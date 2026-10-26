#!/usr/bin/env bash
# Sicherung zurückspielen. Vorher wird der aktuelle Stand noch einmal gesichert.
#   ./scripts/restore.sh                         zeigt die vorhandenen Sicherungen
#   ./scripts/restore.sh /opt/backup/schuetzen-2026-11-01-031500.tar.gz
source "$(dirname "$0")/lib.sh"
need_root

if [ $# -eq 0 ]; then
  echo "Vorhandene Sicherungen (neueste zuerst):"
  ls -1t "$BACKUP_DIR"/schuetzen-*.tar.gz 2>/dev/null | head -20 || echo "  keine"
  echo; echo "Zurückspielen: $0 <Datei>"; exit 0
fi
file="$1"; [ -f "$file" ] || fail "Datei nicht gefunden: $file"

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
tar -xzf "$file" -C "$tmp"
[ -f "$tmp/verein.db" ] || fail "In der Sicherung fehlt verein.db."

read -r -p "Aktuelle Daten durch $(basename "$file") ersetzen? Tippe JA: " answer
[ "$answer" = "JA" ] || fail "Abgebrochen, nichts geändert."

[ -f "$DATA_DIR/verein.db" ] && { info "Sichere zuerst den aktuellen Stand …"; "$(dirname "$0")/backup.sh"; }
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
wait_healthy 90 && info "Fertig, die App läuft mit dem Stand aus $(basename "$file")." || warn "Die App meldet sich nicht gesund. Prüfe: docker compose logs --tail 50"
