#!/usr/bin/env python3
"""Momentaufnahme des Servers für die Wartungsseite der App.

Schreibt in data/wartung.json: Sicherungen, App-Versionen (für das Zurückrollen), Zeitpläne,
Server-Versionen. Wird von den Wartungsskripten und vom Wartungsdienst aufgerufen.
Liest nur, ändert nichts am System.
"""
import datetime
import json
import os
import re
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
STATUS_FILE = os.path.join(DATA_DIR, "wartung.json")
BACKUP_DIR = os.environ.get("BACKUP_DIR") or "/opt/backup"
BACKUP_RE = re.compile(r"^schuetzen-(?:(taeglich|neubau|manuell|vor-wiederherstellung)-)?(\d{4}-\d{2}-\d{2}-\d{4,6})\.tar\.gz$")


def run(*cmd, cwd=None, timeout=20):
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024


def backups():
    items = []
    try:
        names = os.listdir(BACKUP_DIR)
    except OSError:
        return items
    for name in names:
        m = BACKUP_RE.match(name)
        if not m:
            continue
        path = os.path.join(BACKUP_DIR, name)
        st = os.stat(path)
        stamp = m.group(2)   # Zeitpunkt aus dem Dateinamen (bleibt auch beim Kopieren richtig)
        try:
            at = datetime.datetime.strptime(stamp, "%Y-%m-%d-%H%M%S" if len(stamp) == 17 else "%Y-%m-%d-%H%M")
        except ValueError:
            at = datetime.datetime.fromtimestamp(st.st_mtime)
        items.append({"file": name, "kind": m.group(1) or "taeglich", "size": human(st.st_size),
                      "at": at.isoformat(timespec="seconds")})
    items.sort(key=lambda b: b["at"], reverse=True)
    return items


def versions():
    """Die letzten Versionen der App aus Git: aktueller Stand und GitHub."""
    ref = "@{u}" if run("git", "rev-parse", "@{u}", cwd=APP_DIR) else "HEAD"
    head = run("git", "rev-parse", "HEAD", cwd=APP_DIR)
    log = run("git", "log", "-n", "15", "--format=%H|%h|%cd|%s", "--date=format:%d.%m.%Y %H:%M", ref, cwd=APP_DIR)
    if head and ref != "HEAD":   # aktuelle Version auch dann aufnehmen, wenn sie älter als die letzten 15 ist
        if head not in log:
            log += "\n" + run("git", "log", "-n", "1", "--format=%H|%h|%cd|%s", "--date=format:%d.%m.%Y %H:%M", head, cwd=APP_DIR)
    out = []
    for line in log.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            out.append({"commit": parts[0], "short": parts[1], "date": parts[2], "msg": parts[3], "current": parts[0] == head})
    return out, head[:7]


def timers():
    out = {}
    for name in ("backup", "pruefen", "wartung"):
        val = run("systemctl", "show", f"schuetzen-{name}.timer", "--property=NextElapseUSecRealtime,LastTriggerUSec,ActiveState")
        props = dict(line.split("=", 1) for line in val.splitlines() if "=" in line)
        out[name] = {"next": props.get("NextElapseUSecRealtime", ""), "last": props.get("LastTriggerUSec", ""),
                     "active": props.get("ActiveState", "") == "active"}
    return out


def host():
    pretty = ""
    try:
        for line in open("/etc/os-release"):
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    cf = run("cloudflared", "--version")
    return {
        "os": pretty, "kernel": run("uname", "-r"),
        "docker": run("docker", "version", "--format", "{{.Server.Version}}"),
        "compose": run("docker", "compose", "version", "--short"),
        "cloudflared": (re.search(r"version (\S+)", cf) or [None, ""])[1] if cf else "",
        "image_created": run("docker", "image", "inspect", "schuetzenverein-mannschaften", "--format", "{{.Created}}")[:19],
        "container_started": run("docker", "inspect", "-f", "{{.State.StartedAt}}", "schuetzen-mannschaften")[:19],
    }


def main():
    try:
        data = json.load(open(STATUS_FILE))
    except (OSError, ValueError):
        data = {}
    vers, head = versions()
    data.update({
        "snapshot_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "backups": backups(), "backup_dir": BACKUP_DIR, "backup_dir_mounted": os.path.ismount(BACKUP_DIR),
        "keep": {"neubau": os.environ.get("KEEP_NEUBAU", "3"), "manuell": os.environ.get("KEEP_MANUELL", "5"),
                 "taeglich_tage": os.environ.get("KEEP_DAYS", "14")},
        "versions": vers, "app_commit": head, "timers": timers(), "host": host(),
        "runner_installed": os.path.exists("/etc/systemd/system/schuetzen-jobs.path"),
    })
    if data["backups"]:
        data["backup_count"] = len(data["backups"])
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    try:
        os.chown(tmp, 1000, 1000)
    except PermissionError:
        pass
    os.replace(tmp, STATUS_FILE)


if __name__ == "__main__":
    sys.exit(main())
