#!/usr/bin/env bash
# Sicherung der App: Datenbank (konsistent, im laufenden Betrieb), Profilbilder, .env.
# Ablage: /opt/backup/schuetzen-JJJJ-MM-TT-HHMM.tar.gz, ältere als 14 Tage werden gelöscht.
#   ./scripts/backup.sh            Sicherung anlegen
#   KEEP_DAYS=30 ./scripts/backup.sh
source "$(dirname "$0")/lib.sh"

[ -f "$DATA_DIR/verein.db" ] || fail "Keine Datenbank unter $DATA_DIR/verein.db gefunden."
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
stamp="$(date +%F-%H%M%S)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# Online-Backup über die SQLite-API: sicher, auch wenn gerade jemand speichert
if ! python3 - "$DATA_DIR/verein.db" "$tmp/verein.db" <<'PY'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close(); src.close()
sys.exit(0 if ok == "ok" else 1)
PY
then
  fail "Sicherung fehlgeschlagen oder Integritätsprüfung nicht bestanden."
fi

files=(verein.db)
cp "$APP_DIR/.env" "$tmp/env" 2>/dev/null && files+=(env)
[ -f "$DATA_DIR/.secret_key" ] && cp "$DATA_DIR/.secret_key" "$tmp/secret_key" && files+=(secret_key)
[ -d "$DATA_DIR/avatars" ] && cp -r "$DATA_DIR/avatars" "$tmp/avatars" && files+=(avatars)

out="$BACKUP_DIR/schuetzen-$stamp.tar.gz"
tar -czf "$out" -C "$tmp" "${files[@]}"
chmod 600 "$out"
find "$BACKUP_DIR" -maxdepth 1 -name 'schuetzen-*.tar.gz' -mtime +"$KEEP_DAYS" -delete

size="$(du -h "$out" | cut -f1)"
count="$(find "$BACKUP_DIR" -maxdepth 1 -name 'schuetzen-*.tar.gz' | wc -l)"
set_status last_backup=now last_backup_file="$(basename "$out")" last_backup_size="$size" backup_count="$count"
info "Sicherung: $out ($size), $count Sicherungen vorhanden."
