#!/usr/bin/env bash
# Regelmäßige Wartung (läuft automatisch jeden Sonntag 04:00, siehe einrichten.sh):
#  1. Sicherung   2. Linux-Updates   3. App-Image mit aktuellen Sicherheitsupdates neu bauen
#  4. Aufräumen (alte Images, Logs)   5. Gesundheitscheck   6. Status für die Admin-Übersicht
#   ./scripts/wartung.sh              alles
#   AUTO_REBOOT=1 ./scripts/wartung.sh  startet den Server neu, wenn ein Kernel-Update das verlangt
source "$(dirname "$0")/lib.sh"
need_root
export DEBIAN_FRONTEND=noninteractive
problems=()

info "1/6 Sicherung"
"$(dirname "$0")/backup.sh" || problems+=("Sicherung fehlgeschlagen")

info "2/6 Linux-Updates"
if apt-get update -qq && apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade; then
  set_status last_os_update=now
else
  problems+=("Linux-Updates fehlgeschlagen")
fi

info "3/6 App-Image mit aktuellen Sicherheitsupdates neu bauen"
if compose build --pull -q && compose up -d; then
  set_status last_rebuild=now
else
  problems+=("Neubau der App fehlgeschlagen")
fi

info "4/6 Aufräumen"
docker image prune -f >/dev/null || true
docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
command -v journalctl >/dev/null && journalctl --vacuum-time=30d -q || true

info "5/6 Gesundheitscheck"
if wait_healthy 120; then
  set_status app_healthy=true
else
  problems+=("App meldet sich nicht gesund"); set_status app_healthy=false
fi
disk="$(df -P "$APP_DIR" | awk 'NR==2 {gsub("%","",$5); print $5}')"
[ "${disk:-0}" -ge 90 ] && problems+=("Festplatte zu ${disk} % voll")

info "6/6 Status"
reboot=false; [ -f /var/run/reboot-required ] && reboot=true
banned=0; command -v fail2ban-client >/dev/null && banned="$(fail2ban-client status sshd 2>/dev/null | awk -F: '/Currently banned/ {gsub(/ /,"",$2); print $2}')"
set_status last_maintenance=now disk_used_pct="${disk:-0}" ssh_banned="${banned:-0}" \
  problems="$(IFS=';'; echo "${problems[*]:-}")"
"$(dirname "$0")/pruefen.sh" || true

if [ "${#problems[@]}" -gt 0 ]; then
  warn "Wartung mit Problemen beendet: ${problems[*]}"
else
  info "Wartung erfolgreich."
fi
if $reboot; then
  if [ "${AUTO_REBOOT:-0}" = "1" ]; then
    warn "Neustart wegen Kernel-Update in 1 Minute …"; shutdown -r +1 "Neustart nach Wartung" >/dev/null
  else
    warn "Ein Neustart ist nötig (Kernel-Update). Bei Gelegenheit: reboot"
  fi
fi
[ "${#problems[@]}" -eq 0 ]
