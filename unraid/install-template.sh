#!/bin/bash
# Install / update musik-import Unraid user template + pull image
# Run on Unraid Terminal:
# bash <(curl -fsSL https://raw.githubusercontent.com/PaulG67/musik-import/main/unraid/install-template.sh)

set -euo pipefail

TEMPLATE_DIR="/boot/config/plugins/dockerMan/templates-user"
USER_XML="${TEMPLATE_DIR}/my-musik-import.xml"
IMAGE="ghcr.io/paulg67/musik-import:latest"
RAW_BASE="https://raw.githubusercontent.com/PaulG67/musik-import/main"
SRC_DIR="/mnt/user/appdata/musik-import-src"
REPO="https://github.com/PaulG67/musik-import.git"

echo "==> 1/3 Unraid-Vorlage"
mkdir -p "${TEMPLATE_DIR}"
curl -fsSL -H "Cache-Control: no-cache" "${RAW_BASE}/unraid/my-musik-import.xml?$(date +%s)" -o "${USER_XML}"

if ! grep -q '<Repository>ghcr.io/paulg67/musik-import:latest</Repository>' "${USER_XML}"; then
  echo "FEHLER: Vorlage enthaelt kein Repository — Abbruch."
  exit 1
fi
if ! grep -q 'Name="Import"' "${USER_XML}"; then
  echo "FEHLER: Vorlage enthaelt keinen Import-Ordner — Abbruch."
  exit 1
fi
if ! grep -q '</WebUI>' "${USER_XML}"; then
  echo "FEHLER: WebUI-Tag kaputt — Abbruch."
  exit 1
fi

echo "  ${USER_XML}"
wc -c "${USER_XML}" | awk '{print "  Groesse:" $1 " Bytes"}'

echo "==> 2/3 Docker-Image"
if docker pull "${IMAGE}" 2>/dev/null; then
  echo "  pulled ${IMAGE}"
else
  echo "  pull failed — building locally from GitHub"
  if command -v git >/dev/null 2>&1; then
    if [[ -d "${SRC_DIR}/.git" ]]; then
      git -C "${SRC_DIR}" pull --ff-only || true
    else
      rm -rf "${SRC_DIR}"
      git clone --depth 1 "${REPO}" "${SRC_DIR}"
    fi
  else
    mkdir -p "${SRC_DIR}"
    curl -fsSL "https://codeload.github.com/PaulG67/musik-import/tar.gz/refs/heads/main" \
      | tar -xz -C "${SRC_DIR}" --strip-components=1
  fi
  docker build -t "${IMAGE}" "${SRC_DIR}"
  echo "  tagged ${IMAGE}"
fi

echo "==> 3/3 Fertig"
echo
echo "Seite neu laden (F5), dann:"
echo "  Docker -> Container hinzufuegen -> Template musik-import"
echo "  Import-Ordner ausserhalb der Navidrome-Bibliothek lassen"
echo "  Musik-Ordner auf denselben Share wie Navidrome setzen"
echo "  WebUI: http://UNRAID-IP:8793"
echo "  Updates: Docker -> musik-import -> Force Update"
echo "  Nicht am Router nach aussen freigeben."
