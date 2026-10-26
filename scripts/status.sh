#!/usr/bin/env bash
# Schneller Überblick über App, Server und Sicherheit.
source "$(dirname "$0")/lib.sh"

row() { printf '  %-26s %s\n' "$1" "$2"; }
ok()  { printf '\033[32m%s\033[0m' "$*"; }
bad() { printf '\033[31m%s\033[0m' "$*"; }

echo "App"
health="$(docker inspect -f '{{.State.Health.Status}}' schuetzen-mannschaften 2>/dev/null || echo 'läuft nicht')"
row "Zustand" "$([ "$health" = healthy ] && ok "$health" || bad "$health")"
row "Version" "$(cd "$APP_DIR" && git log -1 --format='%h vom %cd' --date=format:'%d.%m.%Y %H:%M' 2>/dev/null || echo '?')"
row "Port 8000" "$(ss -tln 2>/dev/null | awk '$4 ~ /:8000$/ {print $4}' | head -1)"
if systemctl list-unit-files cloudflared.service >/dev/null 2>&1; then
  ct="$(systemctl is-active cloudflared 2>/dev/null || true)"; row "Cloudflare Tunnel" "$([ "$ct" = active ] && ok "$ct" || bad "$ct")"
fi

echo "Sicherung"
last="$(ls -1t "$BACKUP_DIR"/schuetzen-*.tar.gz 2>/dev/null | head -1 || true)"
if [ -n "$last" ]; then
  age_h=$(( ( $(date +%s) - $(stat -c %Y "$last") ) / 3600 ))
  row "Letzte Sicherung" "$(basename "$last") ($([ "$age_h" -le 36 ] && ok "vor $age_h Std." || bad "vor $age_h Std."))"
  row "Anzahl" "$(ls -1 "$BACKUP_DIR"/schuetzen-*.tar.gz | wc -l) in $BACKUP_DIR"
else
  row "Letzte Sicherung" "$(bad 'keine gefunden')"
fi

echo "Server"
upd="$(pending_updates)"; row "Ausstehende Updates" "$([ "$upd" -eq 0 ] && ok 0 || bad "$upd")"
row "Neustart nötig" "$([ -f /var/run/reboot-required ] && bad ja || ok nein)"
row "Festplatte" "$(df -h "$APP_DIR" | awk 'NR==2 {print $5 " belegt, " $4 " frei"}')"
row "Arbeitsspeicher" "$(free -h | awk '/^Mem:/ {print $3 " von " $2}')"
row "Läuft seit" "$(uptime -p 2>/dev/null | sed 's/^up //')"

echo "Sicherheit"
command -v ufw >/dev/null && row "Firewall (ufw)" "$(ufw status 2>/dev/null | head -1 | sed 's/Status: //')"
if command -v fail2ban-client >/dev/null; then
  row "fail2ban (SSH)" "$(fail2ban-client status sshd 2>/dev/null | awk -F: '/Currently banned/ {gsub(/ /,"",$2); print $2 " IPs gesperrt"}')"
else
  row "fail2ban (SSH)" "$(bad 'nicht installiert') – ./scripts/einrichten.sh"
fi
row "SSH-Passwort-Login" "$(sshd -T 2>/dev/null | awk '/^passwordauthentication/ {print $2}' | sed 's/no/aus (gut)/;s/yes/an/')"
row "Automatische Updates" "$(systemctl is-active unattended-upgrades 2>/dev/null || echo aus)"
row "Wartungs-Timer" "$(systemctl is-active schuetzen-wartung.timer 2>/dev/null || echo aus), Backup: $(systemctl is-active schuetzen-backup.timer 2>/dev/null || echo aus)"
