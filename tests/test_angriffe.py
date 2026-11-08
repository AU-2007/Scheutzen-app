"""Angriffe aus einem übernommenen Container gegen die root-Skripte auf dem Server.
Der Angreifer kontrolliert data/ (uid 1000) und legt dort Symlinks auf Systemdateien.
Erwartung: keine Systemdatei wird gelesen, geändert oder dem App-Benutzer übergeben."""
import json, os, shutil, sqlite3, stat, subprocess, sys, tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1

def fresh():
    root = tempfile.mkdtemp(prefix="angriff-"); os.chmod(root, 0o755)
    app = os.path.join(root, "app")
    os.makedirs(app)
    for name in ("scripts", "update.sh"):
        src = os.path.join(SRC, name)
        (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(app, name))
    os.makedirs(os.path.join(app, "data"))
    os.chown(os.path.join(app, "data"), 1000, 1000)
    bdir = os.path.join(root, "backup"); os.makedirs(bdir)
    open(os.path.join(app, ".env"), "w").write(f"BACKUP_DIR={bdir}\n")
    # Systemdatei des Hosts, die geschützt bleiben muss
    victim = os.path.join(root, "shadow"); open(victim, "w").write("root:GEHEIM\n"); os.chmod(victim, 0o600)
    vdir = os.path.join(root, "etc"); os.makedirs(vdir); os.chmod(vdir, 0o755)
    # docker-Attrappe (compose stop/up, inspect -> healthy)
    fake = os.path.join(root, "bin"); os.makedirs(fake)
    open(os.path.join(fake, "docker"), "w").write('#!/bin/sh\ncase "$*" in *inspect*) echo healthy;; esac\nexit 0\n')
    os.chmod(os.path.join(fake, "docker"), 0o755)
    for d in ("data", "data/jobs", "data/jobs/queue", "data/jobs/log"):
        os.makedirs(os.path.join(app, d), exist_ok=True); os.chown(os.path.join(app, d), 1000, 1000)
    return root, app, victim, vdir, bdir, dict(os.environ, PATH=fake + ":" + os.environ["PATH"], BACKUP_DIR=bdir)

def intact(victim):
    st = os.stat(victim)
    return open(victim).read() == "root:GEHEIM\n" and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o600

def as_attacker(fn):
    """Symlink so anlegen, wie es der Container (uid 1000) könnte."""
    pid = os.fork()
    if pid == 0:
        try:
            os.setgid(1000); os.setuid(1000); fn(); os._exit(0)
        except BaseException as e:
            print("attacker:", e); os._exit(1)
    check(os.waitpid(pid, 0)[1] == 0, "attacker setup")

def sh(app, env, cmd):
    return subprocess.run(["bash", "-c", f"source {app}/scripts/lib.sh; {cmd}"], env=env, capture_output=True, text=True)

# 1) Statusdatei: Symlink auf vorhersagbare .tmp-Datei und auf wartung.json selbst
root, app, victim, vdir, bdir, env = fresh()
d = os.path.join(app, "data")
as_attacker(lambda: (os.symlink(victim, d + "/wartung.json.tmp"), os.symlink(victim, d + "/.wartung.json.tmp")))
r = sh(app, env, "set_status test=1")
check(r.returncode == 0, r.stderr)
check(intact(victim), "set_status: victim intact (tmp symlink)")
os.unlink(d + "/wartung.json.tmp"); os.unlink(d + "/.wartung.json.tmp"); os.unlink(d + "/wartung.json")
as_attacker(lambda: os.symlink(victim, d + "/wartung.json"))
r = sh(app, env, "set_status test=2")
check(r.returncode == 0 and intact(victim), "set_status: wartung.json symlink not followed")
check(not os.path.islink(d + "/wartung.json") and json.load(open(d + "/wartung.json")) == {"test": 2}, "symlink replaced, nothing leaked")
check(os.stat(d + "/wartung.json").st_uid == 1000, "status file owned by app")

# 2) pruefen-Schreiber und snapshot: wartung.json Symlink
os.unlink(d + "/wartung.json"); as_attacker(lambda: os.symlink(victim, d + "/wartung.json"))
r = subprocess.run([sys.executable, "-I", app + "/scripts/snapshot.py"], env=env, capture_output=True, text=True)
check(r.returncode == 0 and intact(victim) and not os.path.islink(d + "/wartung.json"), "snapshot safe " + r.stderr)

# 3) Wartungsdienst: Warteschlange ist Symlink auf Systemordner
root, app, victim, vdir, bdir, env = fresh()
j = os.path.join(app, "data/jobs")
shutil.rmtree(j + "/queue")
as_attacker(lambda: os.symlink(vdir, j + "/queue"))
before = os.stat(vdir)
r = subprocess.run([sys.executable, "-I", app + "/scripts/runner.py"], env=env, capture_output=True, text=True)
check(r.returncode == 0, "runner rc " + r.stderr)
after = os.stat(vdir)
check(after.st_uid == 0 and stat.S_IMODE(after.st_mode) == 0o755, "queue symlink: system dir not chowned")
check(os.path.isdir(j + "/queue") and not os.path.islink(j + "/queue") and os.stat(j + "/queue").st_uid == 1000, "queue replaced by real dir")
# Log-Ordner als Symlink
shutil.rmtree(j + "/log"); as_attacker(lambda: os.symlink(vdir, j + "/log"))
open(j + "/queue/20261106120000-aaaaaaaa.json", "w").write('{"action": "pruefen"}')
subprocess.run([sys.executable, "-I", app + "/scripts/runner.py"], env=env, capture_output=True)
check(os.listdir(vdir) == [] and os.stat(vdir).st_uid == 0, "log symlink: nothing written into system dir")

# 4) Auftrag selbst ist Symlink auf Systemdatei; Log-Datei vorab als Symlink
root, app, victim, vdir, bdir, env = fresh()
q = os.path.join(app, "data/jobs/queue"); l = os.path.join(app, "data/jobs/log")
as_attacker(lambda: (os.symlink(victim, q + "/20261106120000-bbbbbbbb.json"),
                     os.symlink(victim, l + "/20261106120001-cccccccc.log"),
                     os.symlink(victim, l + "/20261106120001-cccccccc.json"),
                     open(q + "/20261106120001-cccccccc.json", "w").write('{"action": "pruefen", "by": "x"}')))
subprocess.run([sys.executable, "-I", app + "/scripts/runner.py"], env=env, capture_output=True)
check(intact(victim), "queue/log symlinks: victim intact")
check(os.listdir(q) == [], "queue emptied")
check("GEHEIM" not in "".join(open(os.path.join(l, f)).read() for f in os.listdir(l) if not os.path.islink(os.path.join(l, f))), "no leak into logs")

# 5) Sicherung: .secret_key und avatars als Symlink auf Systemdateien
root, app, victim, vdir, bdir, env = fresh()
d = os.path.join(app, "data")
db = sqlite3.connect(d + "/verein.db"); db.execute("create table t(x)"); db.commit(); db.close(); os.chown(d + "/verein.db", 1000, 1000)
as_attacker(lambda: (os.symlink(victim, d + "/.secret_key"), os.symlink(vdir, d + "/avatars")))
r = subprocess.run(["bash", app + "/scripts/backup.sh", "manuell"], env=env, capture_output=True, text=True)
check(r.returncode == 0, "backup rc " + r.stdout + r.stderr)
arch = [f for f in os.listdir(bdir) if f.endswith(".tar.gz")][0]
listing = subprocess.run(["tar", "-tzvf", os.path.join(bdir, arch)], capture_output=True, text=True).stdout
check("secret_key" not in listing and "avatars" not in listing, "symlinks not backed up: " + listing)
check(intact(victim), "backup: victim intact")
# verein.db selbst als Symlink auf root-Datei: Sicherung schlägt fehl statt sie zu lesen
os.unlink(d + "/verein.db"); as_attacker(lambda: os.symlink(victim, d + "/verein.db"))
r = subprocess.run(["bash", app + "/scripts/backup.sh", "manuell"], env=env, capture_output=True, text=True)
check(r.returncode != 0 and intact(victim), "db symlink: backup refuses, no read")

# 6) Wiederherstellung: Ziel-Dateien im Datenordner als Symlink auf Systemdateien
root, app, victim, vdir, bdir, env = fresh()
d = os.path.join(app, "data")
db = sqlite3.connect(d + "/verein.db"); db.execute("create table t(x)"); db.commit(); db.close(); os.chown(d + "/verein.db", 1000, 1000)
open(d + "/.secret_key", "w").write("k"); os.chown(d + "/.secret_key", 1000, 1000)
r = subprocess.run(["bash", app + "/scripts/backup.sh", "manuell"], env=env, capture_output=True, text=True)
check(r.returncode == 0, "backup for restore " + r.stderr)
arch = os.path.join(bdir, [f for f in os.listdir(bdir) if f.endswith(".tar.gz")][0])
for f in ("verein.db", ".secret_key", "verein.db-wal"):
    try: os.unlink(os.path.join(d, f))
    except FileNotFoundError: pass
victim2 = victim + "2"; shutil.copy2(victim, victim2)
as_attacker(lambda: (os.symlink(victim, d + "/verein.db"), os.symlink(victim2, d + "/.secret_key"),
                     os.symlink(victim, d + "/verein.db-wal")))
r = subprocess.run(["bash", app + "/scripts/restore.sh", "--ja", arch], env=env, capture_output=True, text=True)
check(r.returncode == 0, "restore rc " + r.stdout[-500:] + r.stderr[-500:])
check(intact(victim) and open(victim2).read() == "root:GEHEIM\n" and os.stat(victim2).st_uid == 0, "restore: victims intact")
check(not os.path.islink(d + "/verein.db") and sqlite3.connect(d + "/verein.db").execute("select count(*) from t").fetchone()[0] == 0, "db restored as real file")
check(open(d + "/.secret_key").read() == "k" and not os.path.islink(d + "/.secret_key"), "secret restored")
print("ANGRIFFE ABGEWEHRT:", ok)
