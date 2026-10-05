from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.database import SessionLocal
from app.db.models import ParkingCar, Webhook


def _utcnow():
    return datetime.now(timezone.utc)


def normalize_plate(value: str) -> str:
    return (value or "").strip().upper()


def parking_car_for_plate(db, plate: str):
    plate = normalize_plate(plate)

    return db.execute(
        select(ParkingCar).where(
            ParkingCar.enabled.is_(True),
            ParkingCar.plate == plate,
        )
    ).scalar_one_or_none()


def parking_json(car: ParkingCar) -> dict:
    return {
        "id": car.id,
        "name": car.name,
        "plate": car.plate,
        "enabled": bool(car.enabled),
        "webhook_id": car.webhook_id,
        "home_value": car.home_value,
        "away_value": car.away_value,
        "state": car.state,
        "last_seen_at": car.last_seen_at,
        "absence_timeout": car.absence_timeout,
        "created_at": car.created_at,
        "updated_at": car.updated_at,
    }


def check_absence(db):
    """
    Переводит машины home -> away после absence_timeout.
    Возвращает список переходов.
    """
    now = _utcnow()
    transitions = []

    cars = db.execute(
        select(ParkingCar).where(
            ParkingCar.enabled.is_(True),
            ParkingCar.state == "home",
        )
    ).scalars().all()

    for car in cars:
        if not car.last_seen_at:
            continue

        last = car.last_seen_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)

        elapsed = (now - last).total_seconds()

        if elapsed >= max(1.0, float(car.absence_timeout or 30)):
            car.state = "away"
            car.updated_at = now

            transitions.append(
                {
                    "car_id": car.id,
                    "plate": car.plate,
                    "name": car.name,
                    "value": car.away_value,
                    "webhook_id": car.webhook_id,
                    "transition": "away",
                }
            )

    return transitions


def mark_seen(db, plate: str):
    """
    Обрабатывает распознанный номер.

    Возвращает transition только при изменении away -> home.
    """
    now = _utcnow()
    car = parking_car_for_plate(db, plate)

    if not car:
        return None

    previous = car.state

    car.last_seen_at = now
    car.updated_at = now

    if previous != "home":
        car.state = "home"

        return {
            "car_id": car.id,
            "plate": car.plate,
            "name": car.name,
            "value": car.home_value,
            "webhook_id": car.webhook_id,
            "transition": "home",
        }

    return None


def webhook_config(db, webhook_id):
    if not webhook_id:
        return None

    w = db.get(Webhook, webhook_id)

    if not w or not w.enabled or not w.url.strip():
        return None

    return {
        "id": w.id,
        "url": w.url,
        "method": (w.method or "POST").upper(),
        "content_type": w.content_type or "application/json",
        "timeout": w.timeout,
        "headers_json": w.headers_json or "{}",
        "body_json": w.body_json or "{}",

        # legacy
        "token": w.token,
        "device_key": w.device_key,
        "duration_ms": w.duration_ms,
        "extra_payload": w.extra_payload,
    }
