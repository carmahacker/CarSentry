from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

def now():
    return datetime.now(timezone.utc)

class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Camera")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Источник видео: rtsp | ha_camera
    source_type: Mapped[str] = mapped_column(String(20), default="rtsp")
    # entity_id камеры Home Assistant (когда source_type == ha_camera)
    ha_entity_id: Mapped[str] = mapped_column(String(255), default="")

    rtsp_url: Mapped[str] = mapped_column(Text, default="")
    username: Mapped[str] = mapped_column(String(120), default="")
    password: Mapped[str] = mapped_column(Text, default="")

    # Режим работы камеры: entry (Въезд) | parking (Парковка)
    purpose: Mapped[str] = mapped_column(String(20), default="entry")

    # Триггер захвата кадров (для Въезд и как базовый опрос для Парковки)
    # motion | interval
    mode: Mapped[str] = mapped_column(String(20), default="motion")
    interval_seconds: Mapped[float] = mapped_column(Float, default=10.0)
    frame_count: Mapped[int] = mapped_column(Integer, default=3)
    frame_delay_ms: Mapped[int] = mapped_column(Integer, default=250)
    motion_threshold: Mapped[int] = mapped_column(Integer, default=25)
    motion_min_changed_ratio: Mapped[float] = mapped_column(Float, default=0.01)
    motion_idle_timeout: Mapped[float] = mapped_column(Float, default=2.0)
    motion_cooldown: Mapped[float] = mapped_column(Float, default=10.0)
    telegram_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    plate_cooldown_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    # Для совместимости: cooldown | until_gone
    # При purpose=parking принудительно until_gone
    plate_trigger_mode: Mapped[str] = mapped_column(String(20), default="cooldown")
    webhook_id: Mapped[int | None] = mapped_column(
        ForeignKey("webhooks.id"),
        nullable=True,
        index=True,
    )
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_url: Mapped[str] = mapped_column(Text, default="")
    webhook_plate: Mapped[str] = mapped_column(String(32), default="")
    status: Mapped[str] = mapped_column(String(30), default="disabled")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Путь к последнему аннотированному снимку (для camera entity)
    last_snapshot_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)

class TelegramSettings(Base):
    __tablename__ = "telegram_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_token: Mapped[str] = mapped_column(Text, default="")
    chat_id: Mapped[str] = mapped_column(String(120), default="")
    thread_id: Mapped[str] = mapped_column(String(120), default="")
    proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proxy_host: Mapped[str] = mapped_column(String(120), default="tor-proxy")
    proxy_port: Mapped[int] = mapped_column(Integer, default=9150)

class AnprSettings(Base):
    __tablename__ = "anpr_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    confidence: Mapped[float] = mapped_column(Float, default=0.35)
    vote_frames: Mapped[int] = mapped_column(Integer, default=3)
    device: Mapped[str] = mapped_column(String(20), default="cpu")

class AppSettings(Base):
    __tablename__ = "app_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    snapshot_retention_days: Mapped[int] = mapped_column(Integer, default=30)
    sync_seconds: Mapped[int] = mapped_column(Integer, default=3)

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    plate: Mapped[str] = mapped_column(String(32), default="")
    detector_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    ocr_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_path: Mapped[str] = mapped_column(Text, default="")
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_error: Mapped[str] = mapped_column(Text, default="")
    webhook_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    webhook_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ParkingCar(Base):
    __tablename__ = "parking_cars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Car")
    plate: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    webhook_id: Mapped[int | None] = mapped_column(
        ForeignKey("webhooks.id"),
        nullable=True,
        index=True,
    )
    home_value: Mapped[str] = mapped_column(String(120), default="")
    away_value: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(20), default="away")
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    absence_timeout: Mapped[float] = mapped_column(Float, default=30.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now,
        onupdate=now,
    )


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="Webhook")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    url: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(10), default="POST")
    content_type: Mapped[str] = mapped_column(
        String(120),
        default="application/json",
    )

    token: Mapped[str] = mapped_column(Text, default="")
    device_key: Mapped[str] = mapped_column(String(120), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=5000)
    timeout: Mapped[float] = mapped_column(Float, default=20.0)

    # Дополнительный JSON. Например:
    # {"foo":"bar"}
    extra_payload: Mapped[str] = mapped_column(Text, default="")
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    body_json: Mapped[str] = mapped_column(Text, default="{}")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now,
        onupdate=now,
    )
