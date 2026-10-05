# CarSentry for Home Assistant

ANPR (Automatic Number Plate Recognition) for Home Assistant.

- **HACS** custom integration — sensors, binary sensors, camera entities, services
- **HA App / Addon** — heavy lifting (Torch + YOLO + CRNN + OpenCV)
- Same models and recognition pipeline as the original standalone project
- Two camera modes:
  1. **Въезд (entry)** — event on plate detection, cooldown, Telegram, webhook, snapshot
  2. **Парковка (parking)** — up to 10 known cars, home/away with absence timeout, individual webhooks
- **RTSP auto-discovery** — no vendor lock-in (Dahua, Hikvision, Reolink, Uniview, generic…)
- **Use existing HA cameras** — select any already connected camera entity as video source

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Home Assistant                                          │
│  ┌──────────────────────┐   HTTP API   ┌──────────────┐ │
│  │ custom_components/   │ ←──────────→ │  HA App      │ │
│  │ carsentry        │              │  (addon)     │ │
│  │ sensors / camera /   │              │  FastAPI +   │ │
│  │ services             │              │  YOLO+CRNN   │ │
│  └──────────────────────┘              └──────────────┘ │
│           │                                              │
│           └── can pull frames from HA camera.* entities  │
└─────────────────────────────────────────────────────────┘
```

The integration is lightweight. All Torch / OpenCV / model work runs inside the addon container.

**Why not pure HACS without addon?**  
Torch + YOLO + CRNN + models (~26 MB) cannot reliably run inside the normal Home Assistant Python environment. HA officially separates heavy container apps (Add-ons / Apps) from light integrations. For the user it still looks like one system: sidebar panel via Ingress + entities in HA.

## Models (required)

Copy the original models into the repository **before building the addon image**:

```
models/anpr/yolo/best.pt                 (~6 MB)
models/anpr/ocr/crnn_ocr_model_int8_fx.pth  (~20 MB)
```

## Installation

### 1. HA App (Addon)

1. Place models, then build/publish the image or install as local addon.
2. Start the addon (Supervisor → Add-on Store → Local add-ons).
3. Web UI is available via **Ingress** (sidebar panel «CarSentry») — **no need to open port 8010** for normal use.
4. Port 8010 is only a fallback for standalone Docker / debugging.

### 2. HACS integration

1. Add repository in HACS → Integration → install **CarSentry**.
2. Restart Home Assistant.
3. Settings → Devices & Services → Add Integration → **CarSentry**.
4. In most cases the addon is **discovered automatically** (internal Supervisor network).  
   Host/port fields are optional and only needed if auto-discovery fails.

## Camera sources

When adding a camera in the addon UI:

| Источник | Описание |
|----------|----------|
| **RTSP** | Прямое подключение. Кнопка «Автоопределение RTSP» + ручной URL. |
| **Камера Home Assistant** | Укажите `entity_id` уже подключённой камеры (`camera.xxx`). Интеграция сама делает snapshot и отправляет кадр в addon. |

## Modes

| Режим | Что делает |
|-------|------------|
| **Въезд** | Событие при распознавании номера, cooldown, Telegram, webhook, снимок |
| **Парковка** | Presence home/away для известных номеров, absence timeout, per-car webhook |

## Entities created by the integration

- `sensor.carsentry_*` — состояние каждой машины на парковке (`home` / `away`), динамически при добавлении
- `sensor.carsentry_last_plate` — последний распознанный номер
- `binary_sensor.*_connected` — статус связи с камерой CarSentry
- `camera.carsentry_*` — последний аннотированный снимок с номером

Все entity создаются **динамически**: новые parking-cars и камеры появляются без перезагрузки HA.

## RTSP Auto-Detect

Service `carsentry.discover_rtsp` / кнопка в UI:

- Пробует стандартные пути (Dahua, Hikvision, Reolink, Uniview, generic)
- Открывает поток и читает **реальный кадр**
- Если не нашло — ручной URL + «Проверить»

## API (addon)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Health |
| GET | `/api/cameras` | List cameras |
| GET | `/api/cameras/{id}/snapshot` | Last annotated snapshot |
| POST | `/api/cameras/{id}/analyze` | Analyze uploaded JPEG (HA camera source) |
| GET | `/api/parking-cars` | Parking cars + states |
| GET | `/api/events` | Recent events |
| POST | `/api/rtsp/discover` | Auto-discover RTSP |
| POST | `/api/rtsp/verify` | Verify RTSP URL |

## Recognition pipeline (unchanged)

```
frame(s)
  → YOLO best.pt
  → crop
  → CRNN int8 .pth
  → temporal voting
  → validated plate
```
