FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ANPR_YOLO_MODEL=/models/anpr/yolo/best.pt \
    ANPR_OCR_MODEL=/models/anpr/ocr/crnn_ocr_model_int8_fx.pth \
    ANPR_OCR_DEVICE=cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libglib2.0-0 \
    libgl1 \
    libgomp1 \
    sqlite3 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/requirements.txt

COPY app /app/app
COPY static /app/static

RUN mkdir -p /models/anpr/yolo /models/anpr/ocr /data/snapshots /data/crops
COPY models/anpr /models/anpr

COPY addon/run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8000

CMD ["/run.sh"]
