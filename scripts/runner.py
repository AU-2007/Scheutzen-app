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
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safeio   # noqa: E402  sicheres Schreiben im Datenordner (keine Symlinks)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
        try:   # Schutz vor Neustart-Schleifen (z. B. durch einen übernommenen Container)
            if float(open("/proc/uptime").read().split()[0]) < 600:
                return None, "Der Server läuft erst seit wenigen Minuten. Neustart frühestens 10 Minuten nach dem Hochfahren."
        except (OSError, ValueError):
            pass
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


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def handle(qfd, lfd, name):
    """Einen Auftrag aus der Warteschlange ausführen. qfd/lfd: sicher geöffnete Ordner (dir_fd)."""
    job_id = name[:-5] if name.endswith(".json") else ""
    req = safeio.read_json(qfd, name) if JOB_ID.match(job_id) else None
    try:
        os.unlink(name, dir_fd=qfd)
    except OSError:
        pass
    if not isinstance(req, dict):
        return
    action = str(req.get("action", ""))[:20]
    param = req.get("param")
    status = {"id": job_id, "action": action, "param": param[:200] if isinstance(param, str) else "",
              "by": str(req.get("by", ""))[:80], "requested": str(req.get("at", ""))[:19],
              "started": now(), "state": "running", "rc": None}
    try:
        log_fd = safeio.create_file(lfd, job_id + ".log")
    except FileExistsError:
        return   # gleiche Auftragsnummer schon da: nicht überschreiben
    safeio.write_json(lfd, job_id + ".json", status)
    cmd, err = build_command(action, param)
    with os.fdopen(log_fd, "w", encoding="utf-8") as log:
        if err:
            log.write(f"Abgelehnt: {err}\n")
            status.update(state="failed", rc=2, ended=now())
        else:
            log.write(f"Start {status['started']}: {' '.join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))}\n")
            log.flush()
            env = dict(os.environ, TERM="dumb", NO_COLOR="1", BACKUP_DIR=BACKUP_DIR,
                       PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
            try:
                rc = subprocess.run(cmd, cwd=APP_DIR, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, timeout=7200).returncode
            except subprocess.TimeoutExpired:
                rc = 124
                log.write("\nAbgebrochen: Zeitlimit von 2 Stunden überschritten.\n")
            if action == "reboot" and rc == 0:
                log.write("Der Server startet in 1 Minute neu. App und Tunnel sind danach in 1 bis 2 Minuten wieder da.\n")
            status.update(state="done" if rc == 0 else "failed", rc=rc, ended=now())
            log.write(f"\nEnde {status['ended']}: {'erfolgreich' if rc == 0 else f'Fehler (Code {rc})'}\n")
    safeio.write_json(lfd, job_id + ".json", status)


def cleanup(lfd):
    jobs = sorted((f for f in os.listdir(lfd) if f.endswith(".json") and JOB_ID.match(f[:-5])), reverse=True)
    for old in jobs[KEEP_JOBS:]:
        for ext in (".json", ".log"):
            try:
                os.unlink(old[:-5] + ext, dir_fd=lfd)
            except OSError:
                pass


def main():
    lock = open("/run/schuetzen-runner.lock", "w") if os.access("/run", os.W_OK) else None
    if lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0   # läuft schon, der laufende Dienst arbeitet die Warteschlange ab
    # Ordner ohne Symlinks öffnen (ein Symlink an ihrer Stelle wird durch einen echten Ordner ersetzt)
    qfd = safeio.open_dir("data", "jobs", "queue", create=True)
    lfd = safeio.open_dir("data", "jobs", "log", create=True)
    try:
        for _ in range(50):   # höchstens 50 Aufträge pro Lauf
            pending = sorted(f for f in os.listdir(qfd) if f.endswith(".json"))
            if not pending:
                break
            handle(qfd, lfd, pending[0])
        # Fremdes aus der Warteschlange entfernen, damit systemd nicht dauernd neu startet
        for f in os.listdir(qfd):
            try:
                st = os.stat(f, dir_fd=qfd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    os.rmdir(f, dir_fd=qfd)
                else:
                    os.unlink(f, dir_fd=qfd)
            except OSError:
                pass
        cleanup(lfd)
    finally:
        os.close(qfd)
        os.close(lfd)
    subprocess.run([sys.executable, "-I", os.path.join(APP_DIR, "scripts", "snapshot.py")],
                   env=dict(os.environ, BACKUP_DIR=BACKUP_DIR), capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
