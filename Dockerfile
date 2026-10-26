FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JARVIS_APP_ROOT=/app \
    JARVIS_HEADLESS=true \
    NOC_HOST=0.0.0.0 \
    NOC_PORT=5050

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    libsndfile1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# En conteneur headless (VPS/Cloud), les paquets purement desktop X11/ALSA (PyQt5, pyaudio, pyautogui, pvporcupine)
# sont optionnels car NativeVoiceDaemon bascule automatiquement en mode HEADLESS_WEB_ONLY.
RUN grep -Ev '^(PyQt5|pyaudio|pyautogui|pvporcupine)' /app/requirements.txt > /tmp/requirements-headless.txt \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements-headless.txt \
    && rm -f /tmp/requirements-headless.txt

COPY . /app

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser \
    && mkdir -p /app/data /app/logs /app/notes /app/screenshots /app/certs \
    && chown -R appuser:appuser /app /home/appuser

USER appuser

EXPOSE 5050

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5050/api/health || exit 1

CMD ["python", "main.py"]

