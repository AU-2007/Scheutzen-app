#!/usr/bin/env bash
# Sicherung der App: Datenbank (konsistent, im laufenden Betrieb), Profilbilder, .env.
#   ./scripts/backup.sh [art]
# art: taeglich (Zeitplan, KEEP_DAYS Tage aufbewahrt), neubau (vor jedem Neubau, immer die letzten 3),
#      manuell (aus der App oder von Hand, die letzten 5), vor-wiederherstellung (die letzten 3)
# Ablage: $BACKUP_DIR/schuetzen-<art>-JJJJ-MM-TT-HHMMSS.tar.gz
source "$(dirname "$0")/lib.sh"

art="${1:-manuell}"
case "$art" in taeglich|neubau|manuell|vor-wiederherstellung) ;; *) fail "Unbekannte Art: $art";; esac
[ -f "$DATA_DIR/verein.db" ] || fail "Keine Datenbank unter $DATA_DIR/verein.db gefunden."
mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
stamp="$(date +%F-%H%M%S)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
chown 1000:1000 "$tmp"   # Daten werden als App-Benutzer gelesen (siehe as_app in lib.sh)

[ -L "$DATA_DIR/verein.db" ] && fail "data/verein.db ist ein Symlink statt einer Datei. Nicht gesichert (möglicher Angriff)."
# Online-Backup über die SQLite-API: sicher, auch wenn gerade jemand speichert
if ! as_app python3 -I - "$DATA_DIR/verein.db" "$tmp/verein.db" <<'PY'
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
# Symlinks nicht mitnehmen; kopiert wird als App-Benutzer (kein Zugriff auf Systemdateien)
[ -f "$DATA_DIR/.secret_key" ] && [ ! -L "$DATA_DIR/.secret_key" ] \
  && as_app cp "$DATA_DIR/.secret_key" "$tmp/secret_key" && files+=(secret_key)
[ -d "$DATA_DIR/avatars" ] && [ ! -L "$DATA_DIR/avatars" ] \
  && as_app cp -rP "$DATA_DIR/avatars" "$tmp/avatars" && files+=(avatars)

out="$BACKUP_DIR/schuetzen-$art-$stamp.tar.gz"
tar -czf "$out.part" -C "$tmp" "${files[@]}" && mv "$out.part" "$out"
chmod 600 "$out"

# Aufbewahrung je Art
keep_newest() {  # art anzahl
  ls -1t "$BACKUP_DIR"/schuetzen-"$1"-*.tar.gz 2>/dev/null | tail -n +"$(( $2 + 1 ))" | xargs -r rm -f
}
case "$art" in
  neubau) keep_newest neubau "$KEEP_NEUBAU" ;;
  manuell) keep_newest manuell "$KEEP_MANUELL" ;;
  vor-wiederherstellung) keep_newest vor-wiederherstellung 3 ;;
  taeglich) find "$BACKUP_DIR" -maxdepth 1 -name 'schuetzen-taeglich-*.tar.gz' -mtime +"$KEEP_DAYS" -delete ;;
esac
# ältere Sicherungen ohne Art im Namen (frühere Version) wie tägliche behandeln
find "$BACKUP_DIR" -maxdepth 1 -regextype posix-extended -regex '.*/schuetzen-[0-9]{4}-[0-9-]+\.tar\.gz' -mtime +"$KEEP_DAYS" -delete

size="$(du -h "$out" | cut -f1)"
count="$(find "$BACKUP_DIR" -maxdepth 1 -name 'schuetzen-*.tar.gz' | wc -l)"
set_status last_backup=now last_backup_file="$(basename "$out")" last_backup_size="$size" backup_count="$count"
snapshot
info "Sicherung ($art): $out ($size), insgesamt $count Sicherungen in $BACKUP_DIR."
