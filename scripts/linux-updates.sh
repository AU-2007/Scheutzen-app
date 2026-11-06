#!/usr/bin/env bash
# Linux-Pakete aktualisieren (wie "sudo apt update && sudo apt full-upgrade").
# full-upgrade statt upgrade: installiert auch Updates mit neuen Abhängigkeiten, z. B. neue Kernel.
# Gestaffelte Ubuntu-Updates ("phased updates") bleiben bewusst draußen, sie kommen in ein paar Tagen von selbst.
source "$(dirname "$0")/lib.sh"
need_root
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l

info "Paketlisten aktualisieren"
apt-get update "${APT_OPTS[@]}" || fail "apt-get update fehlgeschlagen (Internet / Paketquellen prüfen)"

todo="$(apt-get -s full-upgrade 2>/dev/null | awk '/^Inst /{print $2}' | tr '\n' ' ')"
if [ -z "$todo" ]; then
  info "Keine Updates zu installieren."
else
  info "Installiere: $todo"
  apt-get -y "${APT_OPTS[@]}" full-upgrade \
    || fail "Installation fehlgeschlagen"
fi
info "Nicht mehr benötigte Pakete entfernen (z. B. alte Kernel)"
apt-get -y -q autoremove --purge || warn "Aufräumen fehlgeschlagen"
apt-get -q clean
set_status last_os_update=now
if [ -f /var/run/reboot-required ]; then warn "Neustart nötig ($(tr '\n' ' ' < /var/run/reboot-required.pkgs 2>/dev/null)). In der App: Wartung → Server neu starten, sonst automatisch um 04:30."; fi
left="$(apt list --upgradable 2>/dev/null | tail -n +2 | wc -l)"
if [ "$left" -gt 0 ]; then info "$left Paket(e) noch zurückgehalten: gestaffelte Auslieferung von Ubuntu, kommen in den nächsten Tagen automatisch."; fi
# Status neu prüfen (Paketlisten sind gerade frisch). In der Wartung übernimmt das der letzte Schritt.
[ "${IN_WARTUNG:-0}" = "1" ] || SKIP_APT_UPDATE=1 "$(dirname "$0")/pruefen.sh" >/dev/null 2>&1 || true
info "Fertig."
