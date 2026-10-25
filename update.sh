#!/bin/sh
# Holt die neueste Version von GitHub und startet die App neu.
# Aufruf auf dem Server:  ./update.sh
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Fehler: .env fehlt. Bitte zuerst .env.example nach .env kopieren und ausfüllen."; exit 1
fi

# Sicherheitskopie der Datenbank vor jedem Update
if [ -f data/verein.db ] && command -v sqlite3 >/dev/null; then
  mkdir -p /opt/backup
  sqlite3 data/verein.db ".backup '/opt/backup/vor-update-$(date +%F-%H%M).db'"
  echo "Backup: /opt/backup/vor-update-$(date +%F-%H%M).db"
fi

alt=$(git rev-parse --short HEAD)
git pull --ff-only
neu=$(git rev-parse --short HEAD)

if [ "$alt" = "$neu" ] && [ "$1" != "--force" ]; then
  echo "Schon aktuell ($neu). Neu bauen trotzdem mit: ./update.sh --force"; exit 0
fi

echo "Änderungen $alt -> $neu:"
git log --oneline "$alt..$neu" || true

docker compose up -d --build
docker image prune -f >/dev/null
echo "Fertig. Status:"
docker compose ps
