#!/usr/bin/env bash
# Prüft auf anstehende Linux-Paket-Updates (apt) und neue App-Versionen (GitHub)
# und schreibt das Ergebnis für die Admin-Übersicht nach data/wartung.json.
# Installiert nichts. Läuft automatisch täglich um 06:30 (siehe einrichten.sh).
source "$(dirname "$0")/lib.sh"
need_root
export DEBIAN_FRONTEND=noninteractive

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

# Die drei Abfragen laufen gleichzeitig (Paketquellen, GitHub, Python-Paketindex)
(
  [ "${SKIP_APT_UPDATE:-0}" = "1" ] || apt-get update -qq -o Acquire::Languages=none || echo fail > "$tmp/apt-fail"
) &
(
  cd "$APP_DIR"
  if git fetch -q 2>/dev/null; then
    git rev-list --count HEAD..@{u} > "$tmp/behind" 2>/dev/null || echo 0 > "$tmp/behind"
    git log --format='%cd|%s' --date=format:'%d.%m.%Y' HEAD..@{u} 2>/dev/null | head -10 > "$tmp/commits" || true
  fi
) &
# Python-Module im Container: gibt es neuere Versionen? (nur Anzeige, Update über Dependabot/GitHub)
( timeout 90 docker exec -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_NO_CACHE_DIR=1 schuetzen-mannschaften \
    python -m pip list --outdated --format=json > "$tmp/outdated" 2>/dev/null || true ) &
wait
[ -f "$tmp/apt-fail" ] && warn "apt-get update fehlgeschlagen"

listing="$(apt list --upgradable 2>/dev/null | tail -n +2 || true)"
# was full-upgrade wirklich installieren würde (ohne gestaffelte Ubuntu-Updates)
installable="$(apt-get -s -o Debug::NoLocking=1 full-upgrade 2>/dev/null | awk '/^Inst /{print $2}' || true)"
behind="$(cat "$tmp/behind" 2>/dev/null || echo 0)"
commits="$(cat "$tmp/commits" 2>/dev/null || true)"
outdated="$(cat "$tmp/outdated" 2>/dev/null || true)"

INSTALLABLE="$installable" OUTDATED="$outdated" LISTING="$listing" COMMITS="$commits" python3 -I - "$STATUS_FILE" "$behind" <<'PY'
import json, os, sys, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[1]))), "scripts"))
from safeio import update_json   # schreibt sicher (folgt keinen Symlinks im Datenordner)
path, behind = sys.argv[1], int(sys.argv[2] or 0)
listing = os.environ.get("LISTING", "")
commits = os.environ.get("COMMITS", "")
installable = set(os.environ.get("INSTALLABLE", "").split())
pkgs = []
for line in listing.splitlines():
    # Beispiel: openssl/noble-updates,noble-security 3.0.13-0ubuntu3.5 amd64 [upgradable from: 3.0.13-0ubuntu3.4]
    if "/" not in line:
        continue
    name, rest = line.split("/", 1)
    parts = rest.split()
    old = line.rsplit("from: ", 1)[-1].rstrip("]") if "from: " in line else ""
    pkgs.append({"name": name, "new": parts[1] if len(parts) > 1 else "", "old": old,
                 "security": "-security" in parts[0], "phased": name not in installable})
pkgs.sort(key=lambda p: (p["phased"], not p["security"], p["name"]))
data = {}
data.update({
    "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "pending_updates": sum(not p["phased"] for p in pkgs),
    "pending_security": sum(p["security"] and not p["phased"] for p in pkgs),
    "pending_phased": sum(p["phased"] for p in pkgs),
    "pending_list": pkgs[:80],
    "reboot_required": os.path.exists("/var/run/reboot-required"),
    "reboot_pkgs": open("/var/run/reboot-required.pkgs").read().split() if os.path.exists("/var/run/reboot-required.pkgs") else [],
    "app_behind": behind,
    "app_new": [dict(zip(("date", "msg"), c.split("|", 1))) for c in commits.splitlines() if "|" in c],
})
try:
    out = json.loads(os.environ.get("OUTDATED") or "null")
    if isinstance(out, list):
        data["outdated"] = [{"name": str(o.get("name", ""))[:60], "version": str(o.get("version", ""))[:30],
                             "latest": str(o.get("latest_version", ""))[:30]} for o in out[:60] if isinstance(o, dict)]
        data["outdated_at"] = data["checked_at"]
except ValueError:
    pass
update_json(path, data)
print(f"{len(pkgs)} Paket-Updates ({sum(p['security'] for p in pkgs)} Sicherheit), App {behind} Version(en) zurück.")
PY
snapshot
