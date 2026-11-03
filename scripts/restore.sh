#!/usr/bin/env bash
# Sicherung zurückspielen. Vorher wird der aktuelle Stand noch einmal gesichert.
#   ./scripts/restore.sh                         zeigt die vorhandenen Sicherungen
#   ./scripts/restore.sh /opt/backup/schuetzen-taeglich-2026-11-01-031500.tar.gz
#   ./scripts/restore.sh --ja <Datei>              ohne Rückfrage (für den Wartungsdienst der App)
source "$(dirname "$0")/lib.sh"
need_root

yes=0; [ "${1:-}" = "--ja" ] && { yes=1; shift; }
if [ $# -eq 0 ]; then
  echo "Vorhandene Sicherungen (neueste zuerst):"
  ls -1t "$BACKUP_DIR"/schuetzen-*.tar.gz 2>/dev/null | head -20 || echo "  keine"
  echo; echo "Zurückspielen: $0 <Datei>"; exit 0
fi
file="$1"; [ -f "$file" ] || file="$BACKUP_DIR/$(basename "$file")"
[ -f "$file" ] || fail "Datei nicht gefunden: $1"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
tar -xzf "$file" -C "$tmp"
[ -f "$tmp/verein.db" ] || fail "In der Sicherung fehlt verein.db."

if [ "$yes" -ne 1 ]; then
  read -r -p "Aktuelle Daten durch $(basename "$file") ersetzen? Tippe JA: " answer
  [ "$answer" = "JA" ] || fail "Abgebrochen, nichts geändert."
fi

[ -f "$DATA_DIR/verein.db" ] && { info "Sichere zuerst den aktuellen Stand …"; "$(dirname "$0")/backup.sh" vor-wiederherstellung; }
info "Stoppe die App …"; compose stop mannschaften
cp "$tmp/verein.db" "$DATA_DIR/verein.db"
rm -f "$DATA_DIR/verein.db-wal" "$DATA_DIR/verein.db-shm"
[ -d "$tmp/avatars" ] && { rm -rf "$DATA_DIR/avatars"; cp -r "$tmp/avatars" "$DATA_DIR/avatars"; }
[ -f "$tmp/secret_key" ] && cp "$tmp/secret_key" "$DATA_DIR/.secret_key"
chown -R 1000:1000 "$DATA_DIR"
if [ -f "$tmp/env" ] && ! cmp -s "$tmp/env" "$APP_DIR/.env"; then
  cp "$tmp/env" "$BACKUP_DIR/env-aus-$(basename "$file" .tar.gz)"; chmod 600 "$BACKUP_DIR/env-aus-$(basename "$file" .tar.gz)"
  warn "Die .env der Sicherung weicht ab. Sie liegt zum Vergleich unter $BACKUP_DIR/env-aus-$(basename "$file" .tar.gz) und wurde NICHT übernommen."
fi
info "Starte die App …"; compose up -d
set_status last_restore=now last_restore_file="$(basename "$file")"
snapshot
if wait_healthy 120; then info "Fertig, die App läuft mit dem Stand aus $(basename "$file")."
else warn "Die App meldet sich nicht gesund. Prüfe: docker compose logs --tail 50"; exit 1; fi
