#!/bin/sh
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"

if ! getent group "${PGID}" >/dev/null 2>&1; then
  groupadd -g "${PGID}" app || true
fi
if ! getent passwd "${PUID}" >/dev/null 2>&1; then
  useradd -u "${PUID}" -g "${PGID}" -M -s /usr/sbin/nologin app || true
fi

mkdir -p "${CONFIG_DIR:-/config}" "${IMPORT_DIR:-/import}" "${MUSIC_DIR:-/music}"
chown "${PUID}:${PGID}" "${CONFIG_DIR:-/config}" "${IMPORT_DIR:-/import}" || true

# Als root schreiben, damit neue Titel auch in Albenordnern landen,
# die nicht diesem Benutzer gehoeren. Die App gibt neue Dateien danach
# an den Besitzer des Ordners weiter.
exec gunicorn "app.main:app" \
    --bind "0.0.0.0:${PORT:-8793}" \
    --workers 1 \
    --threads 4 \
    --timeout 600 \
    --access-logfile - \
    --error-logfile -
