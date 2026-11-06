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
tar -xzf "$file" -C "$tmp" --no-same-owner --no-same-permissions
[ -f "$tmp/verein.db" ] && [ ! -L "$tmp/verein.db" ] || fail "In der Sicherung fehlt verein.db."
chown -R 1000:1000 "$tmp"   # zurückgeschrieben wird als App-Benutzer (siehe as_app in lib.sh)

if [ "$yes" -ne 1 ]; then
  read -r -p "Aktuelle Daten durch $(basename "$file") ersetzen? Tippe JA: " answer
  [ "$answer" = "JA" ] || fail "Abgebrochen, nichts geändert."
fi

if [ -e "$DATA_DIR/verein.db" ] || [ -L "$DATA_DIR/verein.db" ]; then
  info "Sichere zuerst den aktuellen Stand …"
  # Ist der aktuelle Stand kaputt, soll das Zurückspielen trotzdem gehen (dafür ist es ja da)
  "$(dirname "$0")/backup.sh" vor-wiederherstellung || warn "Aktueller Stand ließ sich nicht sichern (beschädigt?). Spiele trotzdem zurück."
fi
info "Stoppe die App …"; compose stop mannschaften
chown -R 1000:1000 "$DATA_DIR"   # folgt keinen Symlinks; stellt sicher, dass der App-Benutzer schreiben darf
# Als App-Benutzer und mit --remove-destination: ein untergeschobener Symlink im Datenordner
# wird ersetzt statt verfolgt, Systemdateien sind ohnehin nicht erreichbar.
as_app cp --remove-destination "$tmp/verein.db" "$DATA_DIR/verein.db"
as_app rm -f "$DATA_DIR/verein.db-wal" "$DATA_DIR/verein.db-shm"
[ -d "$tmp/avatars" ] && { as_app rm -rf "$DATA_DIR/avatars"; as_app cp -rP "$tmp/avatars" "$DATA_DIR/avatars"; }
[ -f "$tmp/secret_key" ] && [ ! -L "$tmp/secret_key" ] && as_app cp --remove-destination "$tmp/secret_key" "$DATA_DIR/.secret_key"
if [ -f "$tmp/env" ] && ! cmp -s "$tmp/env" "$APP_DIR/.env"; then
  cp "$tmp/env" "$BACKUP_DIR/env-aus-$(basename "$file" .tar.gz)"; chmod 600 "$BACKUP_DIR/env-aus-$(basename "$file" .tar.gz)"
  warn "Die .env der Sicherung weicht ab. Sie liegt zum Vergleich unter $BACKUP_DIR/env-aus-$(basename "$file" .tar.gz) und wurde NICHT übernommen."
fi
info "Starte die App …"; compose up -d
set_status last_restore=now last_restore_file="$(basename "$file")"
snapshot
if wait_healthy 120; then info "Fertig, die App läuft mit dem Stand aus $(basename "$file")."
else warn "Die App meldet sich nicht gesund. Prüfe: docker compose logs --tail 50"; exit 1; fi
