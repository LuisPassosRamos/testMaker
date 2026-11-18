# syntax=docker/dockerfile:1.7
# Prova Pronta — one image with the API, the built web app, LibreOffice (DOCX -> PDF)
# and Tesseract with the Portuguese model. Works on x86_64 and ARM (e.g. Oracle Ampere).

# ---------------------------------------------------------------- web app
FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- runtime
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/app \
    DATA_DIR=/data \
    PORT=8000

# LibreOffice Writer without GUI, Tesseract, and fonts metric-compatible with Arial,
# Times New Roman and Calibri (so line breaks match what teachers see in Word).
RUN printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\n' > /etc/apt/apt.conf.d/80-retries \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      libreoffice-writer-nogui \
      tesseract-ocr tesseract-ocr-osd \
      fonts-liberation fonts-crosextra-carlito fonts-dejavu-core fontconfig \
      ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies first (better layer caching), read from pyproject.toml.
COPY backend/pyproject.toml backend/pyproject.toml
RUN python -c "import tomllib; deps = tomllib.load(open('backend/pyproject.toml', 'rb'))['project']['dependencies']; open('/tmp/requirements.txt', 'w').write('\n'.join(deps))" \
 && pip install -r /tmp/requirements.txt

# Portuguese OCR model (tessdata_best, same as local installs) + English/OSD from the distro.
RUN mkdir -p backend/vendor/tessdata \
 && curl -fsSL -o backend/vendor/tessdata/por.traineddata \
      https://github.com/tesseract-ocr/tessdata_best/raw/main/por.traineddata \
 && cp /usr/share/tesseract-ocr/5/tessdata/eng.traineddata /usr/share/tesseract-ocr/5/tessdata/osd.traineddata \
      backend/vendor/tessdata/

COPY backend/app backend/app
COPY --from=web /web/dist frontend/dist

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /data \
 && chown -R app:app /data /home/app
USER app

EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/api/health', timeout=4)"

# One process on purpose: background jobs and rate limits live in memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --app-dir /app/backend --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
