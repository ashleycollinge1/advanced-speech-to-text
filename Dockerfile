# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY requirements.txt requirements-worker.txt ./
RUN pip install -r requirements-worker.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY frontend ./frontend
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} \
    MEDIA_DIR=/data/media \
    MODELS_DIR=/data/models \
    HF_HOME=/data/models/hf \
    # Models are only downloaded when a user clicks "Download" in the UI.
    # Plain HTTP downloads (not Xet) write one growing file, so progress can be shown.
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    # onnxruntime (faster-whisper's VAD) otherwise uploads usage telemetry to
    # mobile.events.data.microsoft.com on every run. Measured: this stops it.
    ORT_DISABLE_TELEMETRY=1

RUN chmod 0755 /usr/local/bin/entrypoint.sh \
 && mkdir -p /data/media /data/models \
 && chown -R app:app /data

LABEL org.opencontainers.image.title="advanced-speech-to-text" \
      org.opencontainers.image.source="https://github.com/ashleycollinge1/advanced-speech-to-text"

EXPOSE 8000
# Starts as root only to fix ownership of mounted folders, then drops to "app".
ENTRYPOINT ["tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["web"]
