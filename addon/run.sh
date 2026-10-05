#!/usr/bin/env bash
set -euo pipefail

# Home Assistant addon entrypoint
# Data is expected under /data (mapped by Supervisor)

mkdir -p /data/snapshots /data/crops

# Optional: copy models from image to /data if you want runtime override
# (by default models stay inside the image at /models)

export ANPR_YOLO_MODEL="${ANPR_YOLO_MODEL:-/models/anpr/yolo/best.pt}"
export ANPR_OCR_MODEL="${ANPR_OCR_MODEL:-/models/anpr/ocr/crnn_ocr_model_int8_fx.pth}"
export ANPR_OCR_DEVICE="${ANPR_OCR_DEVICE:-cpu}"

echo "Starting CarSentry..."
echo "  YOLO : $ANPR_YOLO_MODEL"
echo "  OCR  : $ANPR_OCR_MODEL"
echo "  Device: $ANPR_OCR_DEVICE"

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
