import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, quote

import cv2
import httpx

from app.db.database import SessionLocal
from app.db.models import Camera, Event, Webhook
from app.cameras.motion import MotionDetector
from app.anpr.pipeline import recognize, annotate
from app.telegram.queue import TelegramQueue, Job
from app.webhook import build_request
from app.parking import mark_seen, check_absence, webhook_config


DATA = Path("/data/snapshots")


class CameraWorker:
    def __init__(self, camera_id, telegram):
        self.camera_id = camera_id
        self.telegram = telegram

        self.cap = None
        self.running = True
        self.motion = None
        self.signature = None
        self.next_interval = 0
        self.last_trigger = 0
        self.processing = False
        self.last_frame = None
        self.last_plate = None
        self.last_plate_seen = 0.0
        self.last_absence_check = 0.0

    def _camera(self):
        with SessionLocal() as db:
            return db.get(Camera, self.camera_id)

    def _url(self, c):
        if not c.username:
            return c.rtsp_url

        p = urlsplit(c.rtsp_url)
        host = p.hostname or ""

        netloc = (
            f"{quote(c.username)}:"
            f"{quote(c.password or '')}@"
            f"{host}"
        )

        if p.port:
            netloc += f":{p.port}"

        return urlunsplit(
            (
                p.scheme,
                netloc,
                p.path,
                p.query,
                p.fragment,
            )
        )

    def _close(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass

            self.cap = None

    async def _connect(self, c):
        self._close()

        self.cap = await asyncio.to_thread(
            cv2.VideoCapture,
            self._url(c),
            cv2.CAP_FFMPEG,
        )

        ok = bool(
            self.cap
            and self.cap.isOpened()
        )

        with SessionLocal() as db:
            x = db.get(Camera, self.camera_id)

            if x:
                x.status = (
                    "connected"
                    if ok
                    else "disconnected"
                )
                db.commit()

        return ok

    async def _read(self):
        if not self.cap:
            return False, None

        return await asyncio.to_thread(
            self.cap.read
        )

    async def _capture_sequence(self, c, first):
        frames = [first.copy()]

        for _ in range(
            max(1, int(c.frame_count)) - 1
        ):
            await asyncio.sleep(
                max(0, c.frame_delay_ms) / 1000
            )

            ok, frame = await self._read()

            if ok:
                frames.append(frame.copy())

        return frames

    def _duplicate(
        self,
        plate,
        now,
        cooldown,
    ):
        """
        Cooldown хранится в Event в SQLite.
        Поэтому после restart/rebuild контейнера
        он не сбрасывается.
        """
        with SessionLocal() as db:
            previous = (
                db.query(Event)
                .filter(
                    Event.camera_id == self.camera_id,
                    Event.plate == plate,
                )
                .order_by(Event.timestamp.desc())
                .first()
            )

            if not previous:
                return False

            prev = previous.timestamp

            if prev.tzinfo is None:
                prev = prev.replace(
                    tzinfo=timezone.utc
                )

            return (
                now - prev
            ).total_seconds() < max(
                0.0,
                float(cooldown),
            )

    def _plate_duplicate(self, c, plate):
        """
        Режимы повторного номера:

        cooldown:
            старое поведение — смотрим последний Event
            в SQLite. Работает и после restart.

        until_gone:
            один Event, пока номер продолжает
            распознаваться. Повтор разрешается только
            после отсутствия успешного распознавания
            этого номера заданное время.
        """
        mode = (
            getattr(
                c,
                "plate_trigger_mode",
                "cooldown",
            )
            or "cooldown"
        ).strip().lower()

        if mode != "until_gone":
            return self._duplicate(
                plate,
                datetime.now(timezone.utc),
                c.plate_cooldown_seconds,
            )

        now = time.monotonic()

        timeout = max(
            0.0,
            float(c.plate_cooldown_seconds),
        )

        if self.last_plate == plate:
            elapsed = (
                now - self.last_plate_seen
            )

            # Номер всё ещё виден.
            # Продлеваем его "присутствие".
            self.last_plate_seen = now

            if elapsed < timeout:
                return True

        # Новый номер либо номер появился
        # после истечения timeout.
        self.last_plate = plate
        self.last_plate_seen = now

        return False

    def _get_webhook_for_plate(
        self,
        c,
        plate,
    ):
        """
        Webhook выбирается только из каталога Webhooks.

        URL/token больше не берутся из Camera.
        """
        if not c.webhook_enabled:
            return None

        if not c.webhook_id:
            return None

        trigger = (
            c.webhook_plate or ""
        ).strip().upper()

        if not trigger:
            return None

        if trigger != plate:
            return None

        with SessionLocal() as db:
            w = db.get(
                Webhook,
                c.webhook_id,
            )

            if not w or not w.enabled:
                return None

            return {
                "id": w.id,
                "url": w.url,
                "method": (
                    w.method or "POST"
                ).upper(),
                "content_type": (
                    w.content_type
                    or "application/json"
                ),
                "token": w.token,
                "device_key": w.device_key,
                "duration_ms": w.duration_ms,
                "timeout": w.timeout,
                "extra_payload":
                    w.extra_payload,
                "headers_json":
                    w.headers_json,
                "body_json":
                    w.body_json,
            }

    @staticmethod
    def _webhook_payload(w):
        """
        PerCo payload по умолчанию:

        {
          "token": "...",
          "device_key": "barrier_2",
          "duration_ms": 5000
        }

        Дополнительный JSON из UI объединяется сверху.
        """
        payload = {}

        if w["token"]:
            payload["token"] = w["token"]

        if w["device_key"]:
            payload["device_key"] = (
                w["device_key"]
            )

        if (
            w["duration_ms"] is not None
            and int(w["duration_ms"]) >= 0
        ):
            payload["duration_ms"] = int(
                w["duration_ms"]
            )

        extra = (
            w.get("extra_payload") or ""
        ).strip()

        if extra:
            try:
                obj = json.loads(extra)

                if isinstance(obj, dict):
                    payload.update(obj)

            except Exception:
                # Неверный extra JSON не должен
                # ломать обработку камеры.
                pass

        return payload

    async def _call_parking_webhook(self, w, value):
        if not w:
            return

        headers_raw = w.get("headers_json") or "{}"
        body_raw = w.get("body_json") or "{}"

        try:
            headers = json.loads(headers_raw)
        except Exception:
            headers = {}

        try:
            body = json.loads(body_raw)
        except Exception:
            body = {}

        if not isinstance(headers, dict):
            headers = {}

        if not isinstance(body, dict):
            body = {}

        def substitute(obj):
            if isinstance(obj, str):
                return obj.replace("{{value}}", value)

            if isinstance(obj, dict):
                return {
                    key: substitute(val)
                    for key, val in obj.items()
                }

            if isinstance(obj, list):
                return [
                    substitute(val)
                    for val in obj
                ]

            return obj

        headers = substitute(headers)
        body = substitute(body)

        timeout_value = max(
            1.0,
            float(w.get("timeout") or 20),
        )

        timeout = httpx.Timeout(
            timeout_value,
            connect=min(5.0, timeout_value),
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            if w["method"] == "GET":
                response = await client.get(
                    w["url"],
                    params=body,
                    headers=headers,
                )
            else:
                response = await client.post(
                    w["url"],
                    json=body,
                    headers=headers,
                )

            response.raise_for_status()

    async def _call_webhook(self, w):
        try:
            (
                method,
                url,
                headers,
                payload,
                timeout_value,
            ) = build_request(w)

        except ValueError:
            raise

        timeout = httpx.Timeout(
            timeout_value,
            connect=min(
                5.0,
                timeout_value,
            ),
        )

        async with httpx.AsyncClient(
            timeout=timeout
        ) as client:

            if method == "GET":
                response = await client.get(
                    url,
                    params=payload,
                    headers=headers,
                )
            else:
                response = await client.request(
                    method,
                    url,
                    json=payload,
                    headers=headers,
                )

            response.raise_for_status()

    async def _process(self, c, frames):
        self.processing = True

        try:
            result = await asyncio.to_thread(
                recognize,
                frames,
            )

            # ------------------------------------------------
            # PARKING MODE
            # ------------------------------------------------
            #
            # until_gone означает:
            #   - номер появился -> home webhook
            #   - пока номер виден -> ничего
            #   - после timeout отсутствия -> away webhook
            #
            purpose = (getattr(c, "purpose", None) or "entry").lower()
            trigger_mode = (
                "until_gone"
                if purpose == "parking"
                else (getattr(c, "plate_trigger_mode", "cooldown") or "cooldown")
            ).strip().lower()

            if trigger_mode == "until_gone":

                transitions = []

                with SessionLocal() as db:

                    # Если сейчас распознан номер —
                    # обновляем его last_seen_at.
                    if result:
                        plate = (
                            result["plate"]
                            .upper()
                            .strip()
                        )

                        transition = mark_seen(
                            db,
                            plate,
                        )

                        if transition:
                            transitions.append(
                                transition
                            )

                    db.commit()

                    # Отправляем webhook уже после commit.
                    for transition in transitions:

                        webhook_id = transition.get(
                            "webhook_id"
                        )

                        value = transition.get(
                            "value"
                        )

                        if not webhook_id or not value:
                            continue

                        w = webhook_config(
                            db,
                            webhook_id,
                        )

                        if not w:
                            continue

                        try:
                            await self._call_parking_webhook(
                                w,
                                value,
                            )

                        except Exception as exc:
                            print(
                                "Parking webhook failed "
                                f"for {transition.get('plate')}: "
                                f"{exc}"
                            )

                return

            # ------------------------------------------------
            # NORMAL / COOLDOWN MODE
            # ------------------------------------------------

            if not result:
                return

            ts = datetime.now(timezone.utc)

            plate = (
                result["plate"]
                .upper()
                .strip()
            )

            if self._plate_duplicate(
                c,
                plate,
            ):
                return

            webhook = (
                self._get_webhook_for_plate(
                    c,
                    plate,
                )
            )

            annotated = annotate(
                result["frame"],
                result,
            )

            name = (
                f"{ts:%Y%m%d_%H%M%S}_"
                f"{self.camera_id}_{plate}.jpg"
            )

            path = DATA / name

            await asyncio.to_thread(
                cv2.imwrite,
                str(path),
                annotated,
                [
                    cv2.IMWRITE_JPEG_QUALITY,
                    92,
                ],
            )

            # Event создаётся только здесь —
            # после фактического обнаружения номера
            # и после cooldown-проверки.
            with SessionLocal() as db:
                # Обновляем путь последнего снимка камеры
                cam = db.get(Camera, self.camera_id)
                if cam is not None:
                    cam.last_snapshot_path = str(path)

                ev = Event(
                    camera_id=self.camera_id,
                    timestamp=ts,
                    plate=plate,
                    detector_confidence=
                        result[
                            "detector_confidence"
                        ],
                    ocr_confidence=
                        result[
                            "ocr_confidence"
                        ],
                    snapshot_path=str(path),
                )

                db.add(ev)
                db.commit()
                db.refresh(ev)

                event_id = ev.id

            # Telegram также только после успешного
            # создания Event.
            if c.telegram_enabled:
                caption = (
                    f"🚗 Камера: {c.name}\n"
                    f"Номер: {plate}\n"
                    f"Время: "
                    f"{ts.astimezone().strftime('%Y-%m-%d %H:%M:%S')}"
                )

                await self.telegram.put(
                    Job(
                        event_id,
                        str(path),
                        caption,
                    )
                )

            # Webhook только если:
            # 1. включён;
            # 2. выбран preset;
            # 3. номер точно совпал.
            if webhook:
                try:
                    await self._call_webhook(
                        webhook
                    )

                    with SessionLocal() as db:
                        ev = db.get(
                            Event,
                            event_id,
                        )

                        if ev:
                            ev.webhook_sent = True
                            ev.webhook_error = ""
                            db.commit()

                except Exception as exc:
                    with SessionLocal() as db:
                        ev = db.get(
                            Event,
                            event_id,
                        )

                        if ev:
                            ev.webhook_sent = False
                            ev.webhook_error = (
                                str(exc)[:2000]
                            )
                            db.commit()

        finally:
            self.processing = False

    async def run(self):
        reconnect = [1, 2, 5, 10, 30]
        ri = 0

        try:
            while self.running:
                c = self._camera()

                if not c or not c.enabled:
                    self._close()

                    if c:
                        with SessionLocal() as db:
                            x = db.get(
                                Camera,
                                self.camera_id,
                            )

                            if x:
                                x.status = "disabled"
                                db.commit()

                    await asyncio.sleep(2)
                    continue

                # Источник — камера Home Assistant:
                # кадры приходят через POST /api/cameras/{id}/analyze
                # из интеграции. Worker только держит status и absence.
                source = (getattr(c, "source_type", None) or "rtsp").lower()
                if source == "ha_camera":
                    self._close()
                    with SessionLocal() as db:
                        x = db.get(Camera, self.camera_id)
                        if x:
                            x.status = "connected"
                            x.last_seen_at = datetime.now(timezone.utc)
                            db.commit()

                    # Парковка: проверка отсутствия машин
                    purpose = (getattr(c, "purpose", None) or "entry").lower()
                    trigger = (
                        "until_gone"
                        if purpose == "parking"
                        else (getattr(c, "plate_trigger_mode", "cooldown") or "cooldown")
                    ).strip().lower()

                    if trigger == "until_gone":
                        now = time.monotonic()
                        last_absence_check = getattr(self, "last_absence_check", 0.0)
                        if now - last_absence_check >= 1.0:
                            self.last_absence_check = now
                            with SessionLocal() as db:
                                transitions = check_absence(db)
                                db.commit()
                                for transition in transitions:
                                    webhook_id = transition.get("webhook_id")
                                    value = transition.get("value")
                                    if not webhook_id or not value:
                                        continue
                                    w = webhook_config(db, webhook_id)
                                    if not w:
                                        continue
                                    try:
                                        await self._call_parking_webhook(w, value)
                                    except Exception as exc:
                                        print(
                                            "Parking absence webhook failed "
                                            f"for {transition.get('plate')}: {exc}"
                                        )

                    await asyncio.sleep(2)
                    continue

                sig = (
                    c.rtsp_url,
                    c.username,
                    c.password,
                    c.mode,
                    c.motion_threshold,
                    c.motion_min_changed_ratio,
                    c.motion_idle_timeout,
                )

                if sig != self.signature:
                    self.signature = sig

                    self.motion = MotionDetector(
                        c.motion_threshold,
                        c.motion_min_changed_ratio,
                        c.motion_idle_timeout,
                    )

                    self.next_interval = (
                        time.monotonic()
                    )

                    self._close()

                if (
                    self.cap is None
                    or not self.cap.isOpened()
                ):
                    ok = await self._connect(c)

                    if not ok:
                        await asyncio.sleep(
                            reconnect[
                                min(
                                    ri,
                                    len(reconnect) - 1,
                                )
                            ]
                        )

                        ri = min(
                            ri + 1,
                            len(reconnect) - 1,
                        )

                        continue

                    ri = 0

                ok, frame = await self._read()

                if ok and frame is not None:
                    self.last_frame = frame.copy()

                if not ok:
                    self._close()

                    await asyncio.sleep(
                        reconnect[
                            min(
                                ri,
                                len(reconnect) - 1,
                            )
                        ]
                    )

                    ri = min(
                        ri + 1,
                        len(reconnect) - 1,
                    )

                    continue

                with SessionLocal() as db:
                    x = db.get(
                        Camera,
                        self.camera_id,
                    )

                    if x:
                        x.last_seen_at = (
                            datetime.now(
                                timezone.utc
                            )
                        )
                        x.status = "connected"
                        db.commit()

                now = time.monotonic()

                # Парковка: независимо проверяем отсутствие
                # зарегистрированных машин.
                if (
                    getattr(c, "plate_trigger_mode", "cooldown")
                    or "cooldown"
                ).strip().lower() == "until_gone":
                    last_absence_check = getattr(
                        self,
                        "last_absence_check",
                        0.0,
                    )

                    if now - last_absence_check >= 1.0:
                        self.last_absence_check = now

                        with SessionLocal() as db:
                            transitions = check_absence(db)
                            db.commit()

                            for transition in transitions:
                                webhook_id = transition.get(
                                    "webhook_id"
                                )
                                value = transition.get(
                                    "value"
                                )

                                if not webhook_id or not value:
                                    continue

                                w = webhook_config(
                                    db,
                                    webhook_id,
                                )

                                if not w:
                                    continue

                                try:
                                    await self._call_parking_webhook(
                                        w,
                                        value,
                                    )
                                except Exception as exc:
                                    print(
                                        "Parking absence webhook failed "
                                        f"for {transition.get('plate')}: {exc}"
                                    )

                trigger = False

                if c.mode == "interval":
                    if now >= self.next_interval:
                        trigger = True

                        self.next_interval = (
                            now
                            + max(
                                0.5,
                                c.interval_seconds,
                            )
                        )

                else:
                    state = self.motion.update(
                        frame
                    )

                    if (
                        state == "started"
                        and now - self.last_trigger
                        >= max(
                            0,
                            c.motion_cooldown,
                        )
                    ):
                        trigger = True
                        self.last_trigger = now

                # Пока предыдущий ANPR-запуск
                # не закончился, новый не запускаем.
                if (
                    trigger
                    and not self.processing
                ):
                    frames = (
                        await self._capture_sequence(
                            c,
                            frame,
                        )
                    )

                    asyncio.create_task(
                        self._process(
                            c,
                            frames,
                        )
                    )

        finally:
            self._close()
