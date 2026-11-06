#!/usr/bin/env bash
# Komplette Wartung in einem Lauf (automatisch jeden Sonntag 04:00, in der App „Wartung jetzt“):
#  1. Sicherung (eine für alles)   2. Linux-Updates   3. neue App-Version holen und Container neu bauen
#  4. Aufräumen   5. Gesundheitscheck   6. Prüfen und Status für die Admin-Übersicht
#   ./scripts/wartung.sh                  alles
#   WARTUNG_APP_UPDATE=0 (in .env)        neue App-Versionen nicht automatisch einspielen, nur neu bauen
#   AUTO_REBOOT=1 ./scripts/wartung.sh    startet den Server neu, wenn ein Kernel-Update das verlangt
# In { } geklammert: Bash liest das Skript ganz ein, bevor es läuft (Schritt 3 kann es per git austauschen).
{
source "$(dirname "$0")/lib.sh"
need_root
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
problems=()
t0=$SECONDS
step() { info "$1 ($((SECONDS - t0)) s)"; }

step "1/6 Sicherung"
"$(dirname "$0")/backup.sh" neubau || problems+=("Sicherung fehlgeschlagen")

step "2/6 Linux-Updates"
IN_WARTUNG=1 "$(dirname "$0")/linux-updates.sh" || problems+=("Linux-Updates fehlgeschlagen")

app_update="${WARTUNG_APP_UPDATE:-$(env_get WARTUNG_APP_UPDATE)}"
if [ "${app_update:-1}" = "0" ]; then
  step "3/6 Container neu bauen (aktuelle Sicherheitsupdates im Image)"
  if compose build --pull -q && compose up -d; then set_status last_rebuild=now
  else problems+=("Neubau der App fehlgeschlagen"); fi
else
  step "3/6 App aktualisieren und Container neu bauen"
  # --force: holt eine neue Version, falls es eine gibt, und baut sonst mit aktuellen Sicherheitsupdates neu.
  # Startet die neue Version nicht, stellt update.sh die vorherige von selbst wieder her.
  SKIP_BACKUP=1 "$APP_DIR/update.sh" --force || problems+=("App-Update/Neubau fehlgeschlagen (vorherige Version läuft weiter)")
fi

step "4/6 Aufräumen"
docker image prune -f >/dev/null || true
docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
command -v journalctl >/dev/null && journalctl --vacuum-time=30d -q || true

step "5/6 Gesundheitscheck"
if wait_healthy 120; then
  set_status app_healthy=true
else
  problems+=("App meldet sich nicht gesund"); set_status app_healthy=false
fi
disk="$(df -P "$APP_DIR" | awk 'NR==2 {gsub("%","",$5); print $5}')"
[ "${disk:-0}" -ge 90 ] && problems+=("Festplatte zu ${disk} % voll")

step "6/6 Prüfen und Status"
reboot=false; [ -f /var/run/reboot-required ] && reboot=true
banned=0; command -v fail2ban-client >/dev/null && banned="$(fail2ban-client status sshd 2>/dev/null | awk -F: '/Currently banned/ {gsub(/ /,"",$2); print $2}')"
set_status last_maintenance=now disk_used_pct="${disk:-0}" ssh_banned="${banned:-0}" \
  problems="$(IFS=';'; echo "${problems[*]:-}")"
SKIP_APT_UPDATE=1 "$(dirname "$0")/pruefen.sh" >/dev/null || true   # Paketlisten sind von Schritt 2 noch frisch
snapshot

if [ "${#problems[@]}" -gt 0 ]; then
  warn "Wartung mit Problemen beendet: ${problems[*]}"
else
  info "Wartung erfolgreich ($((SECONDS - t0)) s)."
fi
if $reboot; then
  if [ "${AUTO_REBOOT:-0}" = "1" ]; then
    warn "Neustart wegen Kernel-Update in 1 Minute …"; shutdown -r +1 "Neustart nach Wartung" >/dev/null
  else
    warn "Ein Neustart ist nötig (Kernel-Update). Automatisch um 04:30, sofort in der App: Wartung → Server neu starten"
  fi
fi
[ "${#problems[@]}" -eq 0 ]
}
