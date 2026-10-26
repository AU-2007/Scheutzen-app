#!/usr/bin/env bash
# Einmalige Grundabsicherung und Automatik für den Server. Kann gefahrlos mehrfach laufen.
#   ./scripts/einrichten.sh
# Richtet ein:
#  - automatische Sicherheitsupdates für Linux (unattended-upgrades), Neustart bei Bedarf um 04:30
#  - fail2ban: sperrt IPs nach falschen SSH-Anmeldungen
#  - SSH: höchstens 4 Versuche je Verbindung; Passwort-Anmeldung bleibt erlaubt
#    (abschalten nur mit:  SSH_NUR_SCHLUESSEL=1 bash scripts/einrichten.sh)
#  - Firewall ufw: nur SSH offen (Ports 80/443 bleiben, falls schon freigegeben)
#  - Docker-Logs begrenzen (max. 3 x 10 MB je Container)
#  - Zeitpläne: Sicherung täglich 03:15, Update-Prüfung täglich 06:30, Wartung sonntags 04:00
source "$(dirname "$0")/lib.sh"
need_root
export DEBIAN_FRONTEND=noninteractive
chmod +x "$APP_DIR"/scripts/*.sh "$APP_DIR"/update.sh 2>/dev/null || true

info "Pakete installieren"
apt-get update -qq
apt-get install -y -qq unattended-upgrades fail2ban python3-systemd ufw python3 >/dev/null

info "Automatische Sicherheitsupdates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
cat > /etc/apt/apt.conf.d/52schuetzen-upgrades <<'CONF'
// Neustart nach Kernel-Updates nachts, die App und der Tunnel starten danach von selbst
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CONF
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

info "fail2ban für SSH"
cat > /etc/fail2ban/jail.d/schuetzen.conf <<'CONF'
[sshd]
enabled  = true
backend  = systemd
maxretry = 4
findtime = 10m
bantime  = 1h
# Wiederholungstäter werden immer länger gesperrt (bis 1 Woche)
bantime.increment = true
bantime.maxtime = 1w
CONF
systemctl enable fail2ban >/dev/null 2>&1 || true
systemctl restart fail2ban || warn "fail2ban ließ sich nicht starten: journalctl -u fail2ban"

info "SSH absichern"
# Passwort-Anmeldung bleibt erlaubt. Nur mit SSH_NUR_SCHLUESSEL=1 wird sie abgeschaltet
# (dann muss bereits ein SSH-Schlüssel hinterlegt sein, sonst passiert nichts).
# 01-… wird vor den Einstellungen des Anbieters (z. B. 50-cloud-init.conf) gelesen und gewinnt.
conf=/etc/ssh/sshd_config.d/01-schuetzen.conf
{
  echo "# Schützen-App: SSH-Grundabsicherung (scripts/einrichten.sh)"
  echo "MaxAuthTries 4"
  echo "LoginGraceTime 30"
  echo "PermitEmptyPasswords no"
  echo "X11Forwarding no"
} > "$conf"
if [ "${SSH_NUR_SCHLUESSEL:-0}" = "1" ]; then
  keys=0
  for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -s "$f" ] && keys=1; done
  if [ "$keys" -eq 1 ]; then
    printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' >> "$conf"
    info "SSH: Anmeldung nur noch mit Schlüssel. Teste in einem ZWEITEN Fenster, ob du noch reinkommst!"
  else
    warn "SSH_NUR_SCHLUESSEL=1, aber kein SSH-Schlüssel hinterlegt: Passwort-Anmeldung bleibt an."
  fi
else
  info "SSH: Passwort-Anmeldung bleibt erlaubt, geschützt durch fail2ban (Sperre nach 4 Fehlversuchen)."
fi
if sshd -t; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
else
  rm -f "$conf"; warn "SSH-Konfiguration ungültig, nichts geändert."
fi

info "Firewall"
ufw allow OpenSSH >/dev/null
ufw --force enable >/dev/null
ufw status | head -1

info "Docker-Logs begrenzen"
if [ ! -f /etc/docker/daemon.json ]; then
  printf '{\n  "log-driver": "json-file",\n  "log-opts": { "max-size": "10m", "max-file": "3" }\n}\n' > /etc/docker/daemon.json
  systemctl restart docker && compose up -d >/dev/null
else
  warn "/etc/docker/daemon.json gibt es schon, nicht verändert."
fi

info "Zeitpläne (systemd-Timer)"
unit() {  # name, beschreibung, befehl, zeitplan
  cat > "/etc/systemd/system/schuetzen-$1.service" <<CONF
[Unit]
Description=$2
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=$3
CONF
  cat > "/etc/systemd/system/schuetzen-$1.timer" <<CONF
[Unit]
Description=$2 (Zeitplan)
[Timer]
OnCalendar=$4
Persistent=true
RandomizedDelaySec=5m
[Install]
WantedBy=timers.target
CONF
}
unit backup  "Schützen-App: Sicherung"       "$APP_DIR/scripts/backup.sh"  "*-*-* 03:15:00"
unit pruefen "Schützen-App: Update-Prüfung"  "$APP_DIR/scripts/pruefen.sh" "*-*-* 06:30:00"
unit wartung "Schützen-App: Wartung"         "$APP_DIR/scripts/wartung.sh" "Sun *-*-* 04:00:00"
systemctl daemon-reload
systemctl enable --now schuetzen-backup.timer schuetzen-pruefen.timer schuetzen-wartung.timer >/dev/null

info "Erste Sicherung und Prüfung"
[ -f "$DATA_DIR/verein.db" ] && "$APP_DIR/scripts/backup.sh" || true
"$APP_DIR/scripts/pruefen.sh" || true
set_status setup_done=now

echo
"$APP_DIR/scripts/status.sh" || true
echo
info "Fertig. Nächste Termine:"
systemctl list-timers 'schuetzen-*' --no-pager | head -5
