#!/usr/bin/env bash
# Prüft auf anstehende Linux-Paket-Updates (apt) und neue App-Versionen (GitHub)
# und schreibt das Ergebnis für die Admin-Übersicht nach data/wartung.json.
# Installiert nichts. Läuft automatisch täglich um 06:30 (siehe einrichten.sh).
source "$(dirname "$0")/lib.sh"
need_root
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq || warn "apt-get update fehlgeschlagen"
listing="$(apt list --upgradable 2>/dev/null | tail -n +2 || true)"

# App: neue Version auf GitHub?
behind=0; commits=""
if (cd "$APP_DIR" && git fetch -q 2>/dev/null); then
  behind="$(cd "$APP_DIR" && git rev-list --count HEAD..@{u} 2>/dev/null || echo 0)"
  commits="$(cd "$APP_DIR" && git log --format='%cd|%s' --date=format:'%d.%m.%Y' HEAD..@{u} 2>/dev/null | head -10 || true)"
fi

LISTING="$listing" COMMITS="$commits" python3 - "$STATUS_FILE" "$behind" <<'PY'
import json, os, sys, datetime
path, behind = sys.argv[1], int(sys.argv[2] or 0)
listing = os.environ.get("LISTING", "")
commits = os.environ.get("COMMITS", "")
pkgs = []
for line in listing.splitlines():
    # Beispiel: openssl/noble-updates,noble-security 3.0.13-0ubuntu3.5 amd64 [upgradable from: 3.0.13-0ubuntu3.4]
    if "/" not in line:
        continue
    name, rest = line.split("/", 1)
    parts = rest.split()
    old = line.rsplit("from: ", 1)[-1].rstrip("]") if "from: " in line else ""
    pkgs.append({"name": name, "new": parts[1] if len(parts) > 1 else "", "old": old,
                 "security": "-security" in parts[0]})
pkgs.sort(key=lambda p: (not p["security"], p["name"]))
try:
    data = json.load(open(path))
except Exception:
    data = {}
data.update({
    "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "pending_updates": len(pkgs),
    "pending_security": sum(p["security"] for p in pkgs),
    "pending_list": pkgs[:80],
    "reboot_required": os.path.exists("/var/run/reboot-required"),
    "reboot_pkgs": open("/var/run/reboot-required.pkgs").read().split() if os.path.exists("/var/run/reboot-required.pkgs") else [],
    "app_behind": behind,
    "app_new": [dict(zip(("date", "msg"), c.split("|", 1))) for c in commits.splitlines() if "|" in c],
})
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=1, ensure_ascii=False)
os.chmod(tmp, 0o644)
try:
    os.chown(tmp, 1000, 1000)
except PermissionError:
    pass
os.replace(tmp, path)
print(f"{len(pkgs)} Paket-Updates ({sum(p['security'] for p in pkgs)} Sicherheit), App {behind} Version(en) zurück.")
PY
