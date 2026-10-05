from sqlalchemy import inspect, text

from .database import Base, engine, SessionLocal
from .models import (
    TelegramSettings,
    AnprSettings,
    AppSettings,
    Camera,
    Webhook,
)


def _add_column_if_missing(table: str, column: str, ddl: str):
    inspector = inspect(engine)
    columns = {c["name"] for c in inspector.get_columns(table)}

    if column not in columns:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f'ALTER TABLE "{table}" ADD COLUMN {ddl}'
                )
            )


def init_db():
    # Создаёт новые таблицы.
    # Существующие таблицы и данные не удаляет.
    Base.metadata.create_all(engine)

    # SQLite / SQLAlchemy create_all() не добавляет новые
    # колонки в уже существующие таблицы.
    _add_column_if_missing(
        "cameras",
        "webhook_id",
        "webhook_id INTEGER",
    )
    _add_column_if_missing(
        "cameras",
        "source_type",
        "source_type VARCHAR(20) DEFAULT 'rtsp'",
    )
    _add_column_if_missing(
        "cameras",
        "ha_entity_id",
        "ha_entity_id VARCHAR(255) DEFAULT ''",
    )
    _add_column_if_missing(
        "cameras",
        "purpose",
        "purpose VARCHAR(20) DEFAULT 'entry'",
    )
    _add_column_if_missing(
        "cameras",
        "last_snapshot_path",
        "last_snapshot_path TEXT DEFAULT ''",
    )

    with SessionLocal() as db:
        if db.get(TelegramSettings, 1) is None:
            db.add(TelegramSettings(id=1))

        if db.get(AnprSettings, 1) is None:
            db.add(AnprSettings(id=1))

        if db.get(AppSettings, 1) is None:
            db.add(AppSettings(id=1))

        db.commit()

        # Перенос старого webhook_url в отдельную сущность.
        # Старый webhook создаётся выключенным, чтобы случайно
        # не начать отправлять старые запросы.
        cameras = (
            db.query(Camera)
            .filter(
                Camera.webhook_url != "",
                Camera.webhook_id.is_(None),
            )
            .all()
        )

        for camera in cameras:
            wh = Webhook(
                name=f"Webhook camera {camera.id}",
                enabled=False,
                url=camera.webhook_url,
                method="POST",
                content_type="application/json",
                timeout=20.0,
            )

            db.add(wh)
            db.flush()

            camera.webhook_id = wh.id

        db.commit()
        # Webhooks (в т.ч. шлагбаумы/PerCo) создаются пользователем через UI —
        # в репозиторий секреты и внутренние IP не кладём.
