FROM python:3.12-slim

LABEL org.opencontainers.image.title="Musik-Import"
LABEL org.opencontainers.image.description="Musik analysieren und in die Navidrome-Bibliothek einsortieren"
LABEL org.opencontainers.image.source="https://github.com/PaulG67/musik-import"
LABEL net.unraid.docker.icon="https://raw.githubusercontent.com/PaulG67/musik-import/main/icon.png"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    IMPORT_DIR=/import \
    MUSIC_DIR=/music \
    CONFIG_DIR=/config \
    PORT=8793 \
    PUID=99 \
    PGID=100

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      gosu \
      passwd \
      ca-certificates \
      libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY icon.svg ./icon.svg
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh \
    && mkdir -p /import /music /config

VOLUME ["/import", "/music", "/config"]
EXPOSE 8793

HEALTHCHECK --interval=60s --timeout=8s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request,sys; p=os.environ.get('PORT','8793'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/health', timeout=5).status==200 else 1)"

ENTRYPOINT ["/docker-entrypoint.sh"]
