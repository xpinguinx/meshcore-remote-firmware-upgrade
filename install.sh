#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/meshcore-remote-updater"
BIN_LINK="/usr/local/bin/meshcore-update"
SCRIPT_NAME="meshcore_updater.py"
README_NAME="README.md"
REQ_NAME="requirements.txt"
GITATTR_NAME=".gitattributes"
DEFAULT_REF="main"

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

REPO=""
REF="$DEFAULT_REF"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --ref)
      REF="${2:-}"
      shift 2
      ;;
    *)
      echo "Argomento non riconosciuto: $1" >&2
      exit 2
      ;;
  esac
done

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
TMPDIR=""
cleanup() {
  if [ -n "$TMPDIR" ] && [ -d "$TMPDIR" ]; then
    rm -rf "$TMPDIR"
  fi
}
trap cleanup EXIT

LOCAL_MODE=0
if [ -f "$SELF_DIR/$SCRIPT_NAME" ] && [ -f "$SELF_DIR/$REQ_NAME" ] && [ -f "$SELF_DIR/$README_NAME" ]; then
  LOCAL_MODE=1
fi

if [ "$LOCAL_MODE" -eq 1 ]; then
  SRC_DIR="$SELF_DIR"
else
  if [ -z "$REPO" ]; then
    echo "Uso remoto rilevato ma manca --repo owner/repo" >&2
    echo "Esempio:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/TUO-UTENTE/meshcore-remote-firmware-upgrade/main/install.sh | bash -s -- --repo TUO-UTENTE/meshcore-remote-firmware-upgrade" >&2
    exit 2
  fi

  TMPDIR="$(mktemp -d)"
  ARCHIVE_URL="https://github.com/$REPO/archive/refs/heads/$REF.tar.gz"
  ARCHIVE_FILE="$TMPDIR/src.tar.gz"

  echo "[bootstrap] Scarico il repository da GitHub..."
  curl -fL --retry 3 --retry-delay 2 "$ARCHIVE_URL" -o "$ARCHIVE_FILE"
  tar -xzf "$ARCHIVE_FILE" -C "$TMPDIR"
  SRC_DIR="$(find "$TMPDIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

  if [ ! -f "$SRC_DIR/$SCRIPT_NAME" ]; then
    echo "Repository scaricato ma file $SCRIPT_NAME non trovato." >&2
    exit 2
  fi
fi

echo "[1/7] Installo dipendenze di sistema..."
$SUDO apt-get update
$SUDO apt-get install -y python3 python3-venv python3-pip curl ca-certificates tar

echo "[2/7] Creo cartella applicazione..."
$SUDO mkdir -p "$APP_DIR"
$SUDO cp "$SRC_DIR/$SCRIPT_NAME" "$APP_DIR/$SCRIPT_NAME"
$SUDO cp "$SRC_DIR/$REQ_NAME" "$APP_DIR/$REQ_NAME"
$SUDO cp "$SRC_DIR/$README_NAME" "$APP_DIR/$README_NAME"
if [ -f "$SRC_DIR/$GITATTR_NAME" ]; then
  $SUDO cp "$SRC_DIR/$GITATTR_NAME" "$APP_DIR/$GITATTR_NAME"
fi

echo "[3/7] Creo ambiente Python..."
$SUDO rm -rf "$APP_DIR/venv"
$SUDO python3 -m venv "$APP_DIR/venv"

echo "[4/7] Installo librerie Python..."
$SUDO "$APP_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
$SUDO "$APP_DIR/venv/bin/pip" install -r "$APP_DIR/$REQ_NAME"

echo "[5/7] Verifico dipendenze Python..."
$SUDO "$APP_DIR/venv/bin/python" - <<'PY'
import esptool  # noqa: F401
from serial.tools import list_ports  # noqa: F401
print("Python dependencies OK")
PY

echo "[6/7] Creo comando meshcore-update..."
$SUDO tee "$BIN_LINK" >/dev/null <<'WRAP'
#!/usr/bin/env bash
set -euo pipefail
exec "/opt/meshcore-remote-updater/venv/bin/python" "/opt/meshcore-remote-updater/meshcore_updater.py" "$@"
WRAP
$SUDO chmod +x "$BIN_LINK"

echo "[7/7] Test finale wrapper..."
$SUDO "$BIN_LINK" --self-test >/dev/null

echo ""
echo "Installazione completata."
echo "Comando disponibile: meshcore-update"
echo "Avvio guidato:     meshcore-update"
echo "Solo seriali:      meshcore-update --detect"
echo "Solo verifica:     meshcore-update --verify"
echo "Autotest:          meshcore-update --self-test"
