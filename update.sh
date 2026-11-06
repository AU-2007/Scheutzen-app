#!/usr/bin/env bash
# App aktualisieren, neu bauen oder auf eine frühere Version zurückrollen.
#   ./update.sh                     neue Version von GitHub holen (nur wenn es eine gibt)
#   ./update.sh --force             auch ohne neue Version neu bauen (aktuelle Sicherheitsupdates im Image)
#   ./update.sh --rollback <commit> auf eine frühere Version zurück (Liste: git log --oneline -15)
# Vor jedem Neubau wird eine Sicherung angelegt (Art "neubau", die letzten 3 bleiben).
# Startet die neue Version nicht sauber, wird automatisch die vorherige wiederhergestellt.
# Alles in { … } klammern: Bash liest das ganze Skript ein, bevor es losläuft. Wichtig, weil git
# diese Datei unten selbst austauscht; sonst würde Bash mitten im Lauf die neue Version weiterlesen.
{
source "$(dirname "$0")/scripts/lib.sh"
cd "$APP_DIR"
[ -f .env ] || fail ".env fehlt. Bitte zuerst .env.example nach .env kopieren und ausfüllen."

mode="update"; target=""
case "${1:-}" in
  --force) mode="force" ;;
  --rollback) mode="rollback"; target="${2:-}" ;;
  "") ;;
  *) fail "Unbekannte Option: $1" ;;
esac

old="$(git rev-parse HEAD)"
if ! git fetch -q; then
  case "$mode" in
    rollback) ;;                                  # alte Versionen sind lokal vorhanden
    force) warn "GitHub nicht erreichbar. Baue die aktuelle Version neu." ;;
    *) fail "GitHub nicht erreichbar (git fetch)." ;;
  esac
fi

if [ "$mode" = "rollback" ]; then
  [[ "$target" =~ ^[0-9a-f]{7,40}$ ]] || fail "Bitte eine Version (Commit-Kennung) angeben, z. B. ./update.sh --rollback 2384b76"
  git cat-file -e "$target^{commit}" 2>/dev/null || fail "Version $target gibt es nicht."
  new="$(git rev-parse "$target^{commit}")"
  # nur Versionen aus dem eigenen Verlauf (aktueller Stand oder GitHub), keine fremden Zweige
  git merge-base --is-ancestor "$new" HEAD || git merge-base --is-ancestor "$new" '@{u}' \
    || fail "Version $target gehört nicht zum Verlauf dieser App."
  [ "$new" = "$old" ] && { info "Diese Version läuft bereits."; exit 0; }
else
  new="$(git rev-parse '@{u}')"
  if [ "$old" = "$new" ] && [ "$mode" != "force" ]; then
    info "Schon aktuell ($(git log -1 --format='%h, %s')). Neu bauen trotzdem mit: ./update.sh --force"
    snapshot; exit 0
  fi
fi

if [ "${SKIP_BACKUP:-0}" != "1" ] && [ -f data/verein.db ]; then info "Sicherung vor dem Neubau"; ./scripts/backup.sh neubau; fi

if [ "$old" != "$new" ]; then
  if [ "$mode" = "rollback" ]; then
    info "Zurück auf: $(git log -1 --format='%h vom %cd, %s' --date=format:'%d.%m.%Y' "$new")"
    git reset -q --hard "$new"
  else
    info "Neue Version:"
    git log --format='  %h %s' "$old..$new"
    git merge --ff-only -q "$new" 2>/dev/null || git reset -q --hard "$new" \
      || fail "Update ließ sich nicht übernehmen. Prüfe: git status"
  fi
  chmod +x update.sh scripts/*.sh 2>/dev/null || true
fi

info "Neu bauen und starten"
if compose build --pull -q && compose up -d && wait_healthy 120; then
  docker image prune -f >/dev/null || true
  set_status last_update=now last_rebuild=now app_version="$(git log -1 --format='%h %cd' --date=format:'%d.%m.%Y')" \
    app_behind="$(git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)" \
    app_new='[]' last_update_mode="$mode"
  snapshot
  info "Fertig. Läuft mit $(git log -1 --format='%h, %s')."
  compose ps
  exit 0
fi

warn "Die Version startet nicht sauber. Letzte Meldungen:"
compose logs --tail 25 mannschaften || true
if [ "$old" != "$new" ]; then
  warn "Stelle die vorherige Version wieder her …"
  git reset -q --hard "$old"
  compose build -q && compose up -d && wait_healthy 120 \
    && warn "Vorherige Version läuft wieder. Die Änderung wurde zurückgenommen." \
    || warn "Auch die vorherige Version startet nicht. Sicherungen: ./scripts/restore.sh"
fi
snapshot
exit 1
}
