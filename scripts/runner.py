#!/usr/bin/env python3
"""Wartungsdienst: führt Aufträge aus der Wartungsseite der App aus.

Die App (im Container, ohne Root-Rechte) legt Aufträge als kleine JSON-Datei in
data/jobs/queue/ ab. systemd (schuetzen-jobs.path) startet daraufhin dieses Skript als root.

Sicherheit: Es gibt nur eine feste Liste von Aktionen. Parameter (Sicherungsdatei, Version)
werden hier noch einmal streng geprüft, egal was die App geschickt hat. Kein Text aus dem
Auftrag landet ungeprüft in einem Befehl, es wird keine Shell benutzt.
"""
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(APP_DIR, "data")
QUEUE = os.path.join(DATA_DIR, "jobs", "queue")
LOGS = os.path.join(DATA_DIR, "jobs", "log")
JOB_ID = re.compile(r"^\d{14}-[0-9a-f]{8}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
BACKUP_FILE = re.compile(r"^schuetzen-(?:(?:taeglich|neubau|manuell|vor-wiederherstellung)-)?\d{4}-\d{2}-\d{2}-\d{4,6}\.tar\.gz$")
KEEP_JOBS = 40


def env_get(key, default=""):
    try:
        for line in open(os.path.join(APP_DIR, ".env")):
            k, _, v = line.strip().partition("=")
            if k.strip() == key:
                default = v.strip().strip("'\"")
    except OSError:
        pass
    return default


BACKUP_DIR = os.environ.get("BACKUP_DIR") or env_get("BACKUP_DIR", "/opt/backup")


def git(*args):
    r = subprocess.run(["git", *args], cwd=APP_DIR, capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def build_command(action, param):
    """Erlaubte Aktion -> fester Befehl. Gibt (befehl, fehler) zurück."""
    sh = lambda name: os.path.join(APP_DIR, "scripts", name)
    if action == "backup":
        return [sh("backup.sh"), "manuell"], None
    if action == "pruefen":
        return [sh("pruefen.sh")], None
    if action == "linux":
        return [sh("linux-updates.sh")], None
    if action == "wartung":
        return [sh("wartung.sh")], None
    if action == "update":
        return [os.path.join(APP_DIR, "update.sh")], None
    if action == "rebuild":
        return [os.path.join(APP_DIR, "update.sh"), "--force"], None
    if action == "reboot":
        exe = "/usr/sbin/shutdown" if os.path.exists("/usr/sbin/shutdown") else "/sbin/shutdown"
        return [exe, "-r", "+1", "Neustart über die Wartungsseite der App"], None
    if action == "rollback":
        if not isinstance(param, str) or not COMMIT.match(param):
            return None, "Ungültige Version."
        rc, _ = git("cat-file", "-e", f"{param}^{{commit}}")
        if rc != 0:
            return None, f"Version {param} gibt es nicht."
        ok_head = git("merge-base", "--is-ancestor", param, "HEAD")[0] == 0
        ok_up = git("merge-base", "--is-ancestor", param, "@{u}")[0] == 0
        if not (ok_head or ok_up):
            return None, "Diese Version gehört nicht zum Verlauf der App."
        return [os.path.join(APP_DIR, "update.sh"), "--rollback", param], None
    if action == "restore":
        if not isinstance(param, str) or not BACKUP_FILE.match(param):
            return None, "Ungültiger Dateiname der Sicherung."
        path = os.path.join(BACKUP_DIR, param)
        if not os.path.isfile(path) or os.path.islink(path):
            return None, "Sicherung nicht gefunden."
        return [sh("restore.sh"), "--ja", path], None
    return None, "Unbekannte Aktion."


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    try:
        os.chown(tmp, 1000, 1000)
    except PermissionError:
        pass
    os.replace(tmp, path)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def handle(path):
    name = os.path.basename(path)
    job_id = name[:-5] if name.endswith(".json") else ""
    try:
        if not JOB_ID.match(job_id) or os.path.islink(path) or os.path.getsize(path) > 4096:
            raise ValueError("ungültiger Auftrag")
        req = json.load(open(path))
        if not isinstance(req, dict):
            raise ValueError("ungültiger Auftrag")
    except (OSError, ValueError):
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.remove(path)
    action = str(req.get("action", ""))[:20]
    param = req.get("param")
    status = {"id": job_id, "action": action, "param": param if isinstance(param, str) else "",
              "by": str(req.get("by", ""))[:80], "requested": str(req.get("at", ""))[:19],
              "started": now(), "state": "running", "rc": None}
    status_path = os.path.join(LOGS, job_id + ".json")
    log_path = os.path.join(LOGS, job_id + ".log")
    write_json(status_path, status)
    cmd, err = build_command(action, param)
    with open(log_path, "w") as log:
        os.chmod(log_path, 0o644)
        try:
            os.chown(log_path, 1000, 1000)
        except PermissionError:
            pass
        if err:
            log.write(f"Abgelehnt: {err}\n")
            status.update(state="failed", rc=2, ended=now())
        else:
            log.write(f"Start {status['started']}: {' '.join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))}\n")
            log.flush()
            env = dict(os.environ, TERM="dumb", NO_COLOR="1", BACKUP_DIR=BACKUP_DIR,
                       PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
            try:
                rc = subprocess.run(cmd, cwd=APP_DIR, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=7200).returncode
            except subprocess.TimeoutExpired:
                rc = 124
                log.write("\nAbgebrochen: Zeitlimit von 2 Stunden überschritten.\n")
            if action == "reboot" and rc == 0:
                log.write("Der Server startet in 1 Minute neu. App und Tunnel sind danach in 1 bis 2 Minuten wieder da.\n")
            status.update(state="done" if rc == 0 else "failed", rc=rc, ended=now())
            log.write(f"\nEnde {status['ended']}: {'erfolgreich' if rc == 0 else f'Fehler (Code {rc})'}\n")
    write_json(status_path, status)


def cleanup():
    jobs = sorted((f for f in os.listdir(LOGS) if f.endswith(".json")), reverse=True)
    for old in jobs[KEEP_JOBS:]:
        for ext in (".json", ".log"):
            try:
                os.remove(os.path.join(LOGS, old[:-5] + ext))
            except OSError:
                pass


def main():
    os.makedirs(QUEUE, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    for d in (QUEUE, LOGS):
        try:
            os.chown(d, 1000, 1000)
        except PermissionError:
            pass
    lock = open("/run/schuetzen-runner.lock" if os.access("/run", os.W_OK) else os.path.join(LOGS, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0   # läuft schon, der laufende Dienst arbeitet die Warteschlange ab
    while True:
        pending = sorted(f for f in os.listdir(QUEUE) if f.endswith(".json"))
        if not pending:
            break
        handle(os.path.join(QUEUE, pending[0]))
    # Fremde Dateien in der Warteschlange entfernen, damit systemd nicht dauernd neu startet
    for f in os.listdir(QUEUE):
        try:
            os.remove(os.path.join(QUEUE, f))
        except OSError:
            pass
    cleanup()
    subprocess.run([sys.executable, os.path.join(APP_DIR, "scripts", "snapshot.py")],
                   env=dict(os.environ, BACKUP_DIR=BACKUP_DIR), capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
