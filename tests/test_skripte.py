"""Wartungsskripte mit Attrappen (apt, docker, journalctl, GitHub als lokales Repo) durchlaufen lassen.
Prüft Reihenfolge, dass nichts doppelt läuft, Tempo und das Verhalten bei Fehlern."""
import json, os, shutil, sqlite3, subprocess, tempfile, time

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1

def setup(apt_sleep=2, pip_sleep=2, fetch_ok=True, healthy=True):
    root = tempfile.mkdtemp(prefix="skripte-"); os.chmod(root, 0o755)
    origin = os.path.join(root, "origin.git"); app = os.path.join(root, "app")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
    shutil.copytree(SRC, app, ignore=shutil.ignore_patterns(".git", "data", "__pycache__"))
    g = lambda *a: subprocess.run(["git", "-C", app, *a], check=True, capture_output=True)
    g("init", "-q", "-b", "main"); g("add", "-A"); g("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "v1")
    g("remote", "add", "origin", origin); g("push", "-q", "-u", "origin", "main")
    os.makedirs(app + "/data"); os.chown(app + "/data", 1000, 1000)
    db = sqlite3.connect(app + "/data/verein.db"); db.execute("create table t(x)"); db.commit(); db.close()
    os.chown(app + "/data/verein.db", 1000, 1000)
    bdir = os.path.join(root, "backup"); os.makedirs(bdir)
    open(app + "/.env", "w").write(f"BACKUP_DIR={bdir}\n")
    fake = os.path.join(root, "bin"); os.makedirs(fake); log = os.path.join(root, "calls.log")
    def stub(name, body):
        p = os.path.join(fake, name); open(p, "w").write("#!/bin/bash\necho \"%s $*\" >> %s\n%s\n" % (name, log, body)); os.chmod(p, 0o755)
    stub("apt-get", f'case "$*" in *update*) sleep {apt_sleep};; *-s*full-upgrade*) echo "Inst openssl [1] (2)";; esac; exit 0')
    stub("apt", 'echo "Listing..."; echo "openssl/noble-security 2 amd64 [upgradable from: 1]"')
    stub("docker", f'''case "$*" in
  *exec*pip*) sleep {pip_sleep}; echo '[{{"name":"gunicorn","version":"23","latest_version":"26"}}]';;
  *Health.Status*) echo {"healthy" if healthy else "unhealthy"};;
  *version*) echo 29.0;;
esac; exit 0''')
    stub("journalctl", "exit 0"); stub("fail2ban-client", "exit 0"); stub("systemctl", "exit 0"); stub("shutdown", "exit 0")
    if not fetch_ok:
        subprocess.run(["git", "-C", app, "remote", "set-url", "origin", "/nonexistent"], check=True)
    env = dict(os.environ, PATH=fake + ":" + os.environ["PATH"], BACKUP_DIR=bdir)
    return root, app, origin, bdir, env, log

def calls(log):
    return open(log).read().splitlines() if os.path.exists(log) else []

def run(env, *cmd):
    t = time.time(); r = subprocess.run(["bash", *cmd], env=env, capture_output=True, text=True); return r, time.time() - t

# 1) pruefen.sh: drei Abfragen gleichzeitig
root, app, origin, bdir, env, log = setup(apt_sleep=2, pip_sleep=2)
r, dt = run(env, app + "/scripts/pruefen.sh")
check(r.returncode == 0, r.stdout + r.stderr)
check(dt < 3.6, f"pruefen parallel ({dt:.1f}s, nacheinander wären es >4s)")
st = json.load(open(app + "/data/wartung.json"))
check(st["pending_updates"] == 1 and st["outdated"][0]["name"] == "gunicorn", "pruefen results written")
open(log, "w").close()
r, dt = run(dict(env, SKIP_APT_UPDATE="1"), app + "/scripts/pruefen.sh")
check(not any(c.startswith("apt-get") and " update" in c for c in calls(log)), "SKIP_APT_UPDATE skips apt update")

# 2) wartung.sh: neue App-Version auf GitHub -> wird mit eingespielt, alles nur einmal
root, app, origin, bdir, env, log = setup(apt_sleep=1, pip_sleep=1)
other = os.path.join(root, "other")
subprocess.run(["git", "clone", "-q", origin, other], check=True)
open(other + "/NEU.txt", "w").write("v2")
subprocess.run(["git", "-C", other, "add", "-A"], check=True)
subprocess.run(["git", "-C", other, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "v2: neue Funktion"], check=True)
subprocess.run(["git", "-C", other, "push", "-q"], check=True)
r, dt = run(env, app + "/scripts/wartung.sh")
out = r.stdout + r.stderr
check(r.returncode == 0, out[-1500:])
check(os.path.exists(app + "/NEU.txt"), "new app version pulled by wartung")
c = calls(log)
check(sum(1 for x in c if x.startswith("apt-get") and " update" in x) == 1, f"apt update exactly once: {[x for x in c if 'update' in x]}")
check(sum(1 for x in c if "compose build" in x) == 1 and sum(1 for x in c if "compose up" in x) == 1, "one build, one up")
check(sum(1 for x in c if "exec" in x and "pip" in x) == 1, "pruefen exactly once")
check(len([f for f in os.listdir(bdir) if f.endswith(".tar.gz")]) == 1, "exactly one backup")
check("Wartung erfolgreich" in out and "1/6 Sicherung (0 s)" in out, "steps with timing")
st = json.load(open(app + "/data/wartung.json"))
check(st.get("last_maintenance") and st.get("last_update") and st.get("problems") == "", "status written")
check(st.get("app_behind") == 0, "nothing behind after wartung")

# 3) WARTUNG_APP_UPDATE=0: nur neu bauen, kein git
root, app, origin, bdir, env, log = setup(apt_sleep=0, pip_sleep=0)
open(app + "/.env", "a").write("WARTUNG_APP_UPDATE=0\n")
subprocess.run(["git", "clone", "-q", origin, root + "/o2"], check=True)
open(root + "/o2/NEU.txt", "w").write("x"); subprocess.run(["git", "-C", root + "/o2", "add", "-A"], check=True)
subprocess.run(["git", "-C", root + "/o2", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "v2"], check=True)
subprocess.run(["git", "-C", root + "/o2", "push", "-q"], check=True)
r, dt = run(env, app + "/scripts/wartung.sh")
check(r.returncode == 0 and not os.path.exists(app + "/NEU.txt"), "app update disabled -> no pull")
check(any("compose build" in x for x in calls(log)), "still rebuilds")

# 4) GitHub nicht erreichbar: Wartung baut trotzdem neu
root, app, origin, bdir, env, log = setup(apt_sleep=0, pip_sleep=0, fetch_ok=False)
r, dt = run(env, app + "/scripts/wartung.sh")
check(any("compose build" in x for x in calls(log)), "offline: rebuild anyway")
check("GitHub nicht erreichbar" in r.stdout + r.stderr, "offline warning")

# 5) linux-updates.sh allein: prüft danach selbst (ohne zweites apt update)
root, app, origin, bdir, env, log = setup(apt_sleep=0, pip_sleep=0)
r, dt = run(env, app + "/scripts/linux-updates.sh")
c = calls(log)
check(r.returncode == 0 and sum(1 for x in c if x.startswith("apt-get") and " update" in x) == 1 and any("pip" in x for x in c), "linux-updates: one apt update, then check")

# 6) Gesundheitscheck: schneller Takt
src = open(SRC + "/scripts/lib.sh").read()
check("sleep 2" in src and "--start-interval=2s" in open(SRC + "/Dockerfile").read(), "fast health polling")
print("SKRIPTE OK", ok)
