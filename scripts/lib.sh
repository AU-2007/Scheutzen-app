#!/usr/bin/env bash
# Gemeinsame Hilfsfunktionen für die Wartungsskripte. Wird von den anderen Skripten eingebunden.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$APP_DIR/data"
STATUS_FILE="$DATA_DIR/wartung.json"

# Einstellung aus der .env lesen (ohne die Datei auszuführen)
env_get() { [ -f "$APP_DIR/.env" ] || return 0; sed -n "s/^[[:space:]]*$1=//p" "$APP_DIR/.env" 2>/dev/null | tail -1 | tr -d '\r' | sed "s/^[\"']//; s/[\"']\$//" || true; }

# Sicherungen liegen auf einem festen Laufwerk des Servers, Standard /opt/backup.
# Anderes Laufwerk: in der .env z. B. BACKUP_DIR=/mnt/backup (eigene Festplatte / Block-Storage)
BACKUP_DIR="${BACKUP_DIR:-$(env_get BACKUP_DIR)}"; BACKUP_DIR="${BACKUP_DIR:-/opt/backup}"
KEEP_DAYS="${KEEP_DAYS:-$(env_get BACKUP_KEEP_TAGE)}"; KEEP_DAYS="${KEEP_DAYS:-14}"          # tägliche Sicherungen
KEEP_NEUBAU="${KEEP_NEUBAU:-$(env_get BACKUP_KEEP_NEUBAU)}"; KEEP_NEUBAU="${KEEP_NEUBAU:-3}"   # vor jedem Neubau
KEEP_MANUELL="${KEEP_MANUELL:-$(env_get BACKUP_KEEP_MANUELL)}"; KEEP_MANUELL="${KEEP_MANUELL:-5}"

# Status-Momentaufnahme für die Wartungsseite der App (Versionen, Sicherungen, Zeitpläne)
snapshot() { BACKUP_DIR="$BACKUP_DIR" KEEP_NEUBAU="$KEEP_NEUBAU" KEEP_MANUELL="$KEEP_MANUELL" KEEP_DAYS="$KEEP_DAYS" python3 "$APP_DIR/scripts/snapshot.py" "$@" >/dev/null 2>&1 || true; }

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || fail "Bitte als root ausführen (sudo -i)."; }

compose() { (cd "$APP_DIR" && docker compose "$@"); }

# Wert in data/wartung.json setzen (die App zeigt diese Datei in der Admin-Übersicht an)
set_status() {
  [ -d "$DATA_DIR" ] || return 0
  python3 - "$STATUS_FILE" "$@" <<'PY'
import json, sys, os, datetime
path, pairs = sys.argv[1], sys.argv[2:]
try:
    data = json.load(open(path))
except Exception:
    data = {}
for p in pairs:
    k, _, v = p.partition("=")
    if v and v[0] in "[{":
        try:
            v = json.loads(v)
        except ValueError:
            pass
    elif v == "now":
        v = datetime.datetime.now().isoformat(timespec="seconds")
    elif v.lstrip("-").isdigit():
        v = int(v)
    elif v in ("true", "false"):
        v = v == "true"
    data[k] = v
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=1, ensure_ascii=False)
os.chmod(tmp, 0o644)
try:
    os.chown(tmp, 1000, 1000)
except PermissionError:
    pass
os.replace(tmp, path)
PY
}

# Auf "healthy" warten (max. $1 Sekunden). Rückgabe 0 = gesund.
wait_healthy() {
  local limit="${1:-90}" waited=0 state
  while [ "$waited" -lt "$limit" ]; do
    state="$(docker inspect -f '{{.State.Health.Status}}' schuetzen-mannschaften 2>/dev/null || echo fehlt)"
    [ "$state" = "healthy" ] && return 0
    sleep 5; waited=$((waited + 5))
  done
  return 1
}

# Pakete mit ausstehenden Updates zählen (nur Debian/Ubuntu)
pending_updates() {
  if command -v apt-get >/dev/null; then
    apt-get -s -o Debug::NoLocking=1 full-upgrade 2>/dev/null | grep -c '^Inst ' || true
  else
    echo 0
  fi
}
