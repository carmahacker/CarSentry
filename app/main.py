import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from app.db.database import SessionLocal
from app.db.init import init_db
from app.db.models import (
    Camera,
    ParkingCar,
    TelegramSettings,
    AnprSettings,
    AppSettings,
    Event,
    Webhook,
)
from app.telegram.queue import TelegramQueue
from app.telegram.client import TelegramClient
from app.cameras.manager import CameraManager
from app.cameras.rtsp_discovery import discover_rtsp, verify_rtsp


app = FastAPI(title="CarSentry")

queue = TelegramQueue()
manager = CameraManager(queue)


class CameraIn(BaseModel):
    name: str = "Camera"
    enabled: bool = True

    # rtsp | ha_camera
    source_type: str = "rtsp"
    ha_entity_id: str = ""

    rtsp_url: str = ""
    username: str = ""
    password: str | None = None

    # entry (Въезд) | parking (Парковка)
    purpose: str = "entry"

    # motion | interval — способ захвата кадров
    mode: str = "motion"
    interval_seconds: float = 10
    frame_count: int = 3
    frame_delay_ms: int = 250

    motion_threshold: int = 25
    motion_min_changed_ratio: float = 0.01
    motion_idle_timeout: float = 2
    motion_cooldown: float = 10

    plate_cooldown_seconds: float = 30
    plate_trigger_mode: str = "cooldown"
    telegram_enabled: bool = True

    webhook_enabled: bool = False
    webhook_id: int | None = None
    webhook_plate: str = ""


class ParkingCarIn(BaseModel):
    name: str = "Car"
    plate: str
    enabled: bool = True
    webhook_id: int | None = None
    home_value: str = ""
    away_value: str = ""
    absence_timeout: float = 30


class WebhookIn(BaseModel):
    name: str = "Webhook"
    enabled: bool = True
    url: str
    method: str = "POST"
    content_type: str = "application/json"

    # При PUT пустой token означает "оставить старый".
    token: str | None = None

    device_key: str = ""
    duration_ms: int = 5000
    timeout: float = 20

    extra_payload: str = ""
    headers_json: str = '{"Content-Type":"application/json"}'
    body_json: str = "{}"


class TelegramIn(BaseModel):
    enabled: bool = False
    bot_token: str | None = None
    chat_id: str = ""
    thread_id: str = ""
    proxy_enabled: bool = True
    proxy_host: str = "tor-proxy"
    proxy_port: int = 9150


class AnprIn(BaseModel):
    confidence: float = 0.35
    vote_frames: int = 3
    device: str = "cpu"


class AppIn(BaseModel):
    snapshot_retention_days: int = 30
    sync_seconds: int = 3


class RtspDiscoverIn(BaseModel):
    host: str
    username: str = ""
    password: str = ""
    port: int = 554


class RtspVerifyIn(BaseModel):
    rtsp_url: str


@app.on_event("startup")
async def startup():
    Path("/data/snapshots").mkdir(parents=True, exist_ok=True)

    init_db()

    await queue.start()
    await manager.start()

    asyncio.create_task(cleanup_loop())


@app.on_event("shutdown")
async def shutdown():
    await manager.stop()
    await queue.stop()


async def cleanup_loop():
    while True:
        try:
            with SessionLocal() as db:
                s = db.get(AppSettings, 1)

                days = max(
                    1,
                    int(
                        s.snapshot_retention_days
                        if s
                        else 30
                    ),
                )

                cutoff = (
                    datetime.now(timezone.utc)
                    - timedelta(days=days)
                )

                events = (
                    db.query(Event)
                    .filter(Event.timestamp < cutoff)
                    .all()
                )

                for e in events:
                    try:
                        Path(e.snapshot_path).unlink(
                            missing_ok=True
                        )
                    except Exception:
                        pass

                    db.delete(e)

                db.commit()

        except Exception:
            pass

        await asyncio.sleep(3600)


def camera_json(c):
    purpose = getattr(c, "purpose", None) or "entry"
    # Для Парковки plate_trigger_mode всегда until_gone
    plate_trigger = (
        "until_gone"
        if purpose == "parking"
        else (c.plate_trigger_mode or "cooldown")
    )
    return {
        "id": c.id,
        "name": c.name,
        "enabled": c.enabled,
        "source_type": getattr(c, "source_type", None) or "rtsp",
        "ha_entity_id": getattr(c, "ha_entity_id", None) or "",
        "rtsp_url": c.rtsp_url,
        "username": c.username,
        "purpose": purpose,
        "mode": c.mode,
        "interval_seconds": c.interval_seconds,
        "frame_count": c.frame_count,
        "frame_delay_ms": c.frame_delay_ms,
        "motion_threshold": c.motion_threshold,
        "motion_min_changed_ratio": c.motion_min_changed_ratio,
        "motion_idle_timeout": c.motion_idle_timeout,
        "motion_cooldown": c.motion_cooldown,
        "plate_cooldown_seconds": c.plate_cooldown_seconds,
        "plate_trigger_mode": plate_trigger,
        "telegram_enabled": c.telegram_enabled,
        "webhook_enabled": c.webhook_enabled,
        "webhook_id": c.webhook_id,
        "webhook_plate": c.webhook_plate,
        "status": c.status,
        "last_seen_at": c.last_seen_at,
        "last_snapshot_path": getattr(c, "last_snapshot_path", None) or "",
        "created_at": c.created_at,
        "updated_at": c.updated_at,
        "has_password": bool(c.password),
    }



def parking_car_json(c):
    return {
        "id": c.id,
        "name": c.name,
        "plate": c.plate,
        "enabled": c.enabled,
        "webhook_id": c.webhook_id,
        "home_value": c.home_value,
        "away_value": c.away_value,
        "state": c.state,
        "last_seen_at": c.last_seen_at,
        "absence_timeout": c.absence_timeout,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }

def webhook_json(w):
    return {
        "id": w.id,
        "name": w.name,
        "enabled": w.enabled,
        "url": w.url,
        "method": w.method,
        "content_type": w.content_type,
        "has_token": bool(w.token),
        "device_key": w.device_key,
        "duration_ms": w.duration_ms,
        "timeout": w.timeout,
        "extra_payload": w.extra_payload,
        "headers_json": w.headers_json,
        "body_json": w.body_json,
        "created_at": w.created_at,
        "updated_at": w.updated_at,
    }


def validate_extra_payload(value: str):
    if not value.strip():
        return

    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise HTTPException(
            400,
            f"Некорректный дополнительный JSON: {exc}",
        )

    if not isinstance(parsed, dict):
        raise HTTPException(
            400,
            "Дополнительный JSON должен быть объектом",
        )



def validate_json_object(value: str, field_name: str):
    value = (value or "").strip()

    if not value:
        return "{}"

    try:
        parsed = json.loads(value)
    except Exception as exc:
        raise HTTPException(
            400,
            f"{field_name}: некорректный JSON: {exc}",
        )

    if not isinstance(parsed, dict):
        raise HTTPException(
            400,
            f"{field_name}: JSON должен быть объектом",
        )

    return json.dumps(
        parsed,
        ensure_ascii=False,
    )


def validate_webhook_id(db, webhook_id):
    if webhook_id is None:
        return

    if db.get(Webhook, webhook_id) is None:
        raise HTTPException(
            400,
            "Указанный webhook не существует",
        )


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("/app/static/index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


# =========================================================
# CAMERAS
# =========================================================

@app.get("/api/cameras")
def cameras():
    with SessionLocal() as db:
        return [
            camera_json(c)
            for c in db.query(Camera)
            .order_by(Camera.id)
            .all()
        ]


@app.post("/api/cameras")
def create_camera(x: CameraIn):
    with SessionLocal() as db:
        validate_webhook_id(db, x.webhook_id)

        data = x.model_dump(
            exclude={"password"}
        )

        if data.get("webhook_plate"):
            data["webhook_plate"] = (
                data["webhook_plate"]
                .strip()
                .upper()
            )

        # Режим Парковка → until_gone
        purpose = (data.get("purpose") or "entry").strip().lower()
        data["purpose"] = purpose
        if purpose == "parking":
            data["plate_trigger_mode"] = "until_gone"

        source = (data.get("source_type") or "rtsp").strip().lower()
        data["source_type"] = source
        if source == "ha_camera" and not data.get("ha_entity_id"):
            raise HTTPException(400, "Для источника HA Camera укажите ha_entity_id")
        if source == "rtsp" and not data.get("rtsp_url"):
            raise HTTPException(400, "Для RTSP укажите rtsp_url")

        c = Camera(**data)
        c.password = x.password or ""

        db.add(c)
        db.commit()
        db.refresh(c)

        return camera_json(c)


@app.put("/api/cameras/{cid}")
def update_camera(cid: int, x: CameraIn):
    with SessionLocal() as db:
        c = db.get(Camera, cid)

        if not c:
            raise HTTPException(404)

        validate_webhook_id(db, x.webhook_id)

        data = x.model_dump(
            exclude={"password"}
        )

        if data.get("webhook_plate"):
            data["webhook_plate"] = (
                data["webhook_plate"]
                .strip()
                .upper()
            )

        purpose = (data.get("purpose") or "entry").strip().lower()
        data["purpose"] = purpose
        if purpose == "parking":
            data["plate_trigger_mode"] = "until_gone"

        source = (data.get("source_type") or "rtsp").strip().lower()
        data["source_type"] = source
        if source == "ha_camera" and not data.get("ha_entity_id"):
            raise HTTPException(400, "Для источника HA Camera укажите ha_entity_id")
        if source == "rtsp" and not data.get("rtsp_url"):
            raise HTTPException(400, "Для RTSP укажите rtsp_url")

        for k, v in data.items():
            setattr(c, k, v)

        if x.password is not None:
            c.password = x.password

        db.commit()
        db.refresh(c)

        return camera_json(c)


@app.post("/api/cameras/{cid}/test")
async def test_camera(cid: int):
    with SessionLocal() as db:
        c = db.get(Camera, cid)

        if not c:
            raise HTTPException(
                404,
                "Camera not found",
            )

        if not c.enabled:
            raise HTTPException(
                400,
                "Camera disabled",
            )

        worker = manager.workers.get(cid)

        if not worker:
            raise HTTPException(
                409,
                "Camera worker is not running",
            )

        if worker.last_frame is None:
            raise HTTPException(
                409,
                "No current frame available",
            )

        if worker.processing:
            raise HTTPException(
                409,
                "ANPR is already processing",
            )

        frame = worker.last_frame.copy()

    asyncio.create_task(
        worker._process(
            c,
            [frame],
        )
    )

    return {
        "ok": True,
        "message": "Current frame sent to ANPR",
    }


@app.post("/api/rtsp/discover")
async def api_rtsp_discover(x: RtspDiscoverIn):
    """
    Автоопределение RTSP: перебирает стандартные пути
    (Dahua, Hikvision, Reolink, Uniview, generic)
    и проверяет, что реально читается кадр.
    """
    result = await discover_rtsp(
        host=x.host,
        username=x.username,
        password=x.password,
        port=x.port,
    )
    return {
        "success": result.success,
        "rtsp_url": result.rtsp_url,
        "path": result.path,
        "message": result.message,
        "tried": result.tried,
    }


@app.post("/api/rtsp/verify")
async def api_rtsp_verify(x: RtspVerifyIn):
    """Проверка произвольного RTSP URL (кнопка «Проверить»)."""
    result = await verify_rtsp(x.rtsp_url)
    return {
        "success": result.success,
        "rtsp_url": result.rtsp_url,
        "message": result.message,
    }


@app.delete("/api/cameras/{cid}")
def delete_camera(cid: int):
    with SessionLocal() as db:
        c = db.get(Camera, cid)

        if not c:
            raise HTTPException(404)

        db.delete(c)
        db.commit()

        return {"ok": True}


# =========================================================
# WEBHOOKS
# =========================================================


@app.get("/api/parking-cars")
def list_parking_cars():
    with SessionLocal() as db:
        cars = (
            db.query(ParkingCar)
            .order_by(ParkingCar.id.asc())
            .all()
        )

        return [parking_car_json(c) for c in cars]


@app.post("/api/parking-cars")
def create_parking_car(x: ParkingCarIn):
    plate = x.plate.strip().upper()

    if not plate:
        raise HTTPException(400, "Госномер не может быть пустым")

    with SessionLocal() as db:
        if db.query(ParkingCar).count() >= 10:
            raise HTTPException(400, "Можно добавить максимум 10 машин")

        exists = (
            db.query(ParkingCar)
            .filter(ParkingCar.plate == plate)
            .first()
        )

        if exists:
            raise HTTPException(400, "Такая машина уже добавлена")

        c = ParkingCar(
            name=x.name.strip() or "Car",
            plate=plate,
            enabled=x.enabled,
            webhook_id=x.webhook_id,
            home_value=x.home_value.strip(),
            away_value=x.away_value.strip(),
            state="away",
            absence_timeout=max(1.0, float(x.absence_timeout)),
        )

        db.add(c)
        db.commit()
        db.refresh(c)

        return parking_car_json(c)


@app.put("/api/parking-cars/{cid}")
def update_parking_car(cid: int, x: ParkingCarIn):
    plate = x.plate.strip().upper()

    if not plate:
        raise HTTPException(400, "Госномер не может быть пустым")

    with SessionLocal() as db:
        c = db.get(ParkingCar, cid)

        if not c:
            raise HTTPException(404, "Машина не найдена")

        exists = (
            db.query(ParkingCar)
            .filter(
                ParkingCar.plate == plate,
                ParkingCar.id != cid,
            )
            .first()
        )

        if exists:
            raise HTTPException(400, "Такой госномер уже используется")

        c.name = x.name.strip() or "Car"
        c.plate = plate
        c.enabled = x.enabled
        c.webhook_id = x.webhook_id
        c.home_value = x.home_value.strip()
        c.away_value = x.away_value.strip()
        c.absence_timeout = max(1.0, float(x.absence_timeout))

        db.commit()
        db.refresh(c)

        return parking_car_json(c)


@app.delete("/api/parking-cars/{cid}")
def delete_parking_car(cid: int):
    with SessionLocal() as db:
        c = db.get(ParkingCar, cid)

        if not c:
            raise HTTPException(404, "Машина не найдена")

        db.delete(c)
        db.commit()

        return {"ok": True}


@app.post("/api/parking-cars/{cid}/reset")
def reset_parking_car(cid: int):
    with SessionLocal() as db:
        c = db.get(ParkingCar, cid)

        if not c:
            raise HTTPException(404, "Машина не найдена")

        c.state = "away"
        c.last_seen_at = None

        db.commit()
        db.refresh(c)

        return parking_car_json(c)


@app.get("/api/webhooks")
def get_webhooks():
    with SessionLocal() as db:
        return [
            webhook_json(w)
            for w in db.query(Webhook)
            .order_by(Webhook.id)
            .all()
        ]


@app.post("/api/webhooks")
def create_webhook(x: WebhookIn):
    validate_extra_payload(x.extra_payload)
    headers_json = validate_json_object(
        x.headers_json,
        "Заголовки",
    )
    body_json = validate_json_object(
        x.body_json,
        "Тело запроса",
    )

    method = x.method.upper().strip()

    if method not in ("POST", "GET"):
        raise HTTPException(
            400,
            "Поддерживаются только GET и POST",
        )

    if not x.url.strip():
        raise HTTPException(
            400,
            "URL webhook не может быть пустым",
        )

    with SessionLocal() as db:
        w = Webhook(
            name=x.name.strip() or "Webhook",
            enabled=x.enabled,
            url=x.url.strip(),
            method=method,
            content_type=x.content_type.strip()
            or "application/json",
            token=x.token or "",
            device_key=x.device_key.strip(),
            duration_ms=max(0, int(x.duration_ms)),
            timeout=max(1.0, float(x.timeout)),
            extra_payload=x.extra_payload.strip(),
            headers_json=headers_json,
            body_json=body_json,
        )

        db.add(w)
        db.commit()
        db.refresh(w)

        return webhook_json(w)


@app.put("/api/webhooks/{wid}")
def update_webhook(wid: int, x: WebhookIn):
    validate_extra_payload(x.extra_payload)
    headers_json = validate_json_object(
        x.headers_json,
        "Заголовки",
    )
    body_json = validate_json_object(
        x.body_json,
        "Тело запроса",
    )

    method = x.method.upper().strip()

    if method not in ("POST", "GET"):
        raise HTTPException(
            400,
            "Поддерживаются только GET и POST",
        )

    with SessionLocal() as db:
        w = db.get(Webhook, wid)

        if not w:
            raise HTTPException(404)

        w.name = x.name.strip() or "Webhook"
        w.enabled = x.enabled
        w.url = x.url.strip()
        w.method = method
        w.content_type = (
            x.content_type.strip()
            or "application/json"
        )
        w.device_key = x.device_key.strip()
        w.duration_ms = max(
            0,
            int(x.duration_ms),
        )
        w.timeout = max(
            1.0,
            float(x.timeout),
        )
        w.extra_payload = x.extra_payload.strip()
        w.headers_json = headers_json
        w.body_json = body_json

        # Пустое поле token означает:
        # оставить существующий token.
        if x.token is not None and x.token != "":
            w.token = x.token

        db.commit()
        db.refresh(w)

        return webhook_json(w)


@app.delete("/api/webhooks/{wid}")
def delete_webhook(wid: int):
    with SessionLocal() as db:
        w = db.get(Webhook, wid)

        if not w:
            raise HTTPException(404)

        used = (
            db.query(Camera)
            .filter(Camera.webhook_id == wid)
            .first()
        )

        if used:
            raise HTTPException(
                409,
                f"Webhook используется камерой {used.id}. "
                f"Сначала выберите другой webhook.",
            )

        db.delete(w)
        db.commit()

        return {"ok": True}


@app.post("/api/webhooks/{wid}/test")
async def test_webhook(wid: int):
    from app.webhook import build_request
    import httpx

    with SessionLocal() as db:
        w = db.get(Webhook, wid)

        if not w:
            raise HTTPException(404)

        if not w.url.strip():
            raise HTTPException(
                400,
                "URL webhook пустой",
            )

        try:
            method, url, headers, payload, timeout_value = (
                build_request(w, test=True)
            )
        except ValueError as exc:
            raise HTTPException(
                400,
                str(exc),
            )

    try:
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

            return {
                "ok": response.is_success,
                "status_code": response.status_code,
                "response": response.text[:2000],
                "payload": payload,
            }

    except Exception as exc:
        raise HTTPException(
            502,
            f"Webhook test failed: {exc}",
        )


# =========================================================
# TELEGRAM
# =========================================================

@app.get("/api/telegram")
def get_tg():
    with SessionLocal() as db:
        s = db.get(TelegramSettings, 1)

        return {
            "enabled": s.enabled,
            "has_token": bool(s.bot_token),
            "chat_id": s.chat_id,
            "thread_id": s.thread_id,
            "proxy_enabled": s.proxy_enabled,
            "proxy_host": s.proxy_host,
            "proxy_port": s.proxy_port,
        }


@app.put("/api/telegram")
def put_tg(x: TelegramIn):
    with SessionLocal() as db:
        s = db.get(TelegramSettings, 1)

        for k, v in x.model_dump(
            exclude={"bot_token"}
        ).items():
            setattr(s, k, v)

        if (
            x.bot_token is not None
            and x.bot_token != ""
        ):
            s.bot_token = x.bot_token

        db.commit()

        return {"ok": True}


@app.post("/api/telegram/test")
async def test_tg():
    await TelegramClient().send_message(
        "✅ CarSentry: тест Telegram"
    )

    return {"ok": True}


# =========================================================
# ANPR
# =========================================================

@app.get("/api/anpr")
def get_anpr():
    with SessionLocal() as db:
        s = db.get(AnprSettings, 1)

        return {
            "confidence": s.confidence,
            "vote_frames": s.vote_frames,
            "device": s.device,
        }


@app.put("/api/anpr")
def put_anpr(x: AnprIn):
    with SessionLocal() as db:
        s = db.get(AnprSettings, 1)

        s.confidence = x.confidence
        s.vote_frames = x.vote_frames
        s.device = x.device

        db.commit()

        return {"ok": True}


# =========================================================
# APP
# =========================================================

@app.get("/api/app")
def get_app():
    with SessionLocal() as db:
        s = db.get(AppSettings, 1)

        return {
            "snapshot_retention_days":
                s.snapshot_retention_days,
            "sync_seconds":
                s.sync_seconds,
        }


@app.put("/api/app")
def put_app(x: AppIn):
    with SessionLocal() as db:
        s = db.get(AppSettings, 1)

        s.snapshot_retention_days = (
            x.snapshot_retention_days
        )
        s.sync_seconds = x.sync_seconds

        db.commit()

        return {"ok": True}


# =========================================================
# EVENTS
# =========================================================

@app.get("/api/events")
def events(
    limit: int = Query(100, le=500)
):
    with SessionLocal() as db:
        rows = (
            db.query(Event)
            .order_by(Event.timestamp.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                "id": e.id,
                "camera_id": e.camera_id,
                "timestamp": e.timestamp,
                "plate": e.plate,
                "detector_confidence":
                    e.detector_confidence,
                "ocr_confidence":
                    e.ocr_confidence,
                "telegram_sent":
                    e.telegram_sent,
                "telegram_error":
                    e.telegram_error,
                "webhook_sent":
                    e.webhook_sent,
                "webhook_error":
                    e.webhook_error,
                "image":
                    f"/api/events/{e.id}/image",
            }
            for e in rows
        ]


@app.get("/api/events/{eid}/image")
def event_image(eid: int):
    with SessionLocal() as db:
        e = db.get(Event, eid)

        if (
            not e
            or not e.snapshot_path
            or not Path(e.snapshot_path).exists()
        ):
            raise HTTPException(404)

        return FileResponse(
            e.snapshot_path,
            media_type="image/jpeg",
        )


# =========================================================
# CAMERA SNAPSHOT (for HA camera entity)
# =========================================================

@app.get("/api/cameras/{cid}/snapshot")
def camera_snapshot(cid: int):
    """Последний аннотированный снимок камеры (для HA Camera entity)."""
    with SessionLocal() as db:
        c = db.get(Camera, cid)
        if not c:
            raise HTTPException(404, "Camera not found")

        path = getattr(c, "last_snapshot_path", None) or ""
        if not path or not Path(path).exists():
            # fallback: last event for this camera
            e = (
                db.query(Event)
                .filter(Event.camera_id == cid)
                .order_by(Event.timestamp.desc())
                .first()
            )
            if e and e.snapshot_path and Path(e.snapshot_path).exists():
                path = e.snapshot_path
            else:
                raise HTTPException(404, "No snapshot available")

        return FileResponse(path, media_type="image/jpeg")


# =========================================================
# ANALYZE FRAME (для HA camera source — интеграция шлёт JPEG)
# =========================================================

from fastapi import File, UploadFile
import numpy as np
import cv2


@app.post("/api/cameras/{cid}/analyze")
async def analyze_camera_frame(cid: int, file: UploadFile = File(...)):
    """
    Принимает JPEG/PNG кадр от HA-интеграции (source_type=ha_camera),
    прогоняет через тот же ANPR pipeline, что и RTSP worker.
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty image")

    arr = np.frombuffer(content, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(400, "Cannot decode image")

    with SessionLocal() as db:
        c = db.get(Camera, cid)
        if not c:
            raise HTTPException(404, "Camera not found")
        if not c.enabled:
            raise HTTPException(400, "Camera disabled")
        # expunge so attributes remain usable outside session
        db.expunge(c)

    worker = manager.workers.get(cid)
    if worker is None:
        from app.cameras.worker import CameraWorker
        worker = CameraWorker(cid, queue)

    await worker._process(c, [frame])

    return {"ok": True, "message": "Frame sent to ANPR"}
