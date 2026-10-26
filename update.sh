#!/usr/bin/env bash
# Holt die neueste Version von GitHub und startet die App neu.
#   ./update.sh           nur wenn es eine neue Version gibt
#   ./update.sh --force   auch ohne neue Version neu bauen (aktuelle Sicherheitsupdates im Image)
# Ablauf: Sicherung -> git pull -> neu bauen -> Gesundheitscheck.
# Startet die neue Version nicht sauber, wird automatisch die vorherige wiederhergestellt.
source "$(dirname "$0")/scripts/lib.sh"
cd "$APP_DIR"
[ -f .env ] || fail ".env fehlt. Bitte zuerst .env.example nach .env kopieren und ausfüllen."

old="$(git rev-parse HEAD)"
git fetch -q || fail "GitHub nicht erreichbar (git fetch)."
new="$(git rev-parse '@{u}')"
if [ "$old" = "$new" ] && [ "${1:-}" != "--force" ]; then
  info "Schon aktuell ($(git log -1 --format='%h, %s')). Neu bauen trotzdem mit: ./update.sh --force"
  exit 0
fi

[ -f data/verein.db ] && { info "Sicherung vor dem Update"; ./scripts/backup.sh; }

if [ "$old" != "$new" ]; then
  info "Neue Version:"
  git log --format='  %h %s' "$old..$new"
  git merge --ff-only -q "$new" || fail "Lokale Änderungen verhindern das Update. Prüfe: git status"
  chmod +x update.sh scripts/*.sh 2>/dev/null || true
fi

info "Neu bauen und starten"
if compose build --pull -q && compose up -d && wait_healthy 120; then
  docker image prune -f >/dev/null || true
  set_status last_update=now app_version="$(git log -1 --format='%h %cd' --date=format:'%d.%m.%Y')" app_behind=0 app_new='[]'
  info "Fertig. Läuft mit $(git log -1 --format='%h, %s')."
  compose ps
  exit 0
fi

warn "Die neue Version startet nicht sauber. Letzte Meldungen:"
compose logs --tail 25 mannschaften || true
if [ "$old" != "$new" ]; then
  warn "Stelle die vorherige Version wieder her …"
  git reset -q --hard "$old"
  compose build -q && compose up -d && wait_healthy 120 \
    && warn "Vorherige Version läuft wieder. Das Update wurde zurückgenommen." \
    || warn "Auch die vorherige Version startet nicht. Sicherungen: ./scripts/restore.sh"
fi
exit 1
