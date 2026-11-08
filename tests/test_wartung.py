import os, sys, tempfile, io, json, re, contextlib, shutil, subprocess, glob
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = os.path.join(tmp, "t.db"); os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
app = A.app; app.config["TESTING"] = True
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1
def csrf(c):
    with c.session_transaction() as s:
        s.setdefault("csrf", "tok"); return s["csrf"]
def post(c, url, data=None, **kw):
    d = dict(data or {}); d["csrf"] = csrf(c); return c.post(url, data=d, **kw)

a = app.test_client(); a.get("/login"); post(a, "/login", {"username": "admin", "password": "adminpass1"})
post(a, "/passwort", {"old": "adminpass1", "new": "adminpass2", "new2": "adminpass2"})

# Seite ohne Status / ohne Dienst
r = a.get("/admin/wartung"); body = r.get_data(as_text=True)
check(r.status_code == 200 and "noch nicht eingerichtet" in body, "page w/o runner")
r = post(a, "/admin/wartung/auftrag", {"action": "backup"}, follow_redirects=True)
check("noch nicht eingerichtet" in r.get_data(as_text=True), "no queue dir")

# Status anlegen
os.makedirs(os.path.join(tmp, "jobs", "queue")); os.makedirs(os.path.join(tmp, "jobs", "log"))
st = {"last_backup": "2026-11-03T03:15:00", "runner_installed": True, "backup_dir": "/mnt/backup", "backup_dir_mounted": True,
      "keep": {"neubau": "3", "manuell": "5", "taeglich_tage": "14"},
      "backups": [{"file": "schuetzen-neubau-2026-11-02-101010.tar.gz", "kind": "neubau", "size": "1,2 MB", "at": "2026-11-02T10:10:10"},
                  {"file": "schuetzen-taeglich-2026-11-03-031500.tar.gz", "kind": "taeglich", "size": "1,1 MB", "at": "2026-11-03T03:15:00"}],
      "versions": [{"commit": "a" * 40, "short": "aaaaaaa", "date": "03.11.2026 10:00", "msg": "Neu <b>", "current": True},
                   {"commit": "b" * 40, "short": "bbbbbbb", "date": "01.11.2026 10:00", "msg": "Alt", "current": False}],
      "timers": {"backup": {"next": "Wed 2026-11-04 03:15:00 CET", "last": "n/a", "active": True},
                 "pruefen": {"next": "", "last": "", "active": False}, "wartung": {}},
      "host": {"os": "Ubuntu 24.04.3 LTS", "kernel": "6.8.0", "docker": "28.1", "compose": "2.35", "cloudflared": "2025.10.0"},
      "outdated": [{"name": "Werkzeug", "version": "3.1.0", "latest": "3.1.9"}, {"name": "pip", "version": "24", "latest": "25"}]}
json.dump(st, open(os.path.join(tmp, "wartung.json"), "w"))
r = a.get("/admin/wartung"); body = r.get_data(as_text=True)
check("noch nicht eingerichtet" not in body, "runner ok")
check("Neu &lt;b&gt;" in body, "escaped msg")
check("3.1.9" in body and "pip 24 → 25" in body, "outdated shown")
import importlib.metadata as _md
st2 = dict(st, outdated=[{"name": "Flask", "version": "0", "latest": _md.version("Flask")}])
json.dump(st2, open(os.path.join(tmp, "wartung.json"), "w"))
check(_md.version("Flask") + "</span>" not in a.get("/admin/wartung").get_data(as_text=True), "no pill when installed is current")
json.dump(st, open(os.path.join(tmp, "wartung.json"), "w"))
check("vor Neubau" in body and "1,2 MB" in body, "backups listed")
check(re.search(r"Flask</td><td class=\"mono small\">3\.", body), "flask version")
check("Ubuntu 24.04.3" in body and "2025.10.0" in body, "host versions")
check(" 03:15" in body and ">aus<" in body, "timers")
check("(läuft jetzt)" in body and "disabled>aaaaaaa" in body.replace('value="' + "a" * 40 + '" ', ""), "current disabled")

# Rechte
import sqlite3
_db = sqlite3.connect(os.environ["DB_PATH"])
cols = [r[1] for r in _db.execute("PRAGMA table_info(users)")]
pwcol = "pw_hash" if "pw_hash" in cols else "password_hash" if "password_hash" in cols else [c for c in cols if "pw" in c or "pass" in c][0]
for role in ("leader", "trainer", "member"):
    _db.execute(f"INSERT INTO users(name, username, {pwcol}, role, active, must_change_pw) VALUES(?,?,?,?,1,0)",
                (role, role, A.generate_password_hash("passwort12"), role))
_db.commit()
for role in ("leader", "member"):
    c = app.test_client(); c.get("/login"); post(c, "/login", {"username": role, "password": "passwort12"})
    check(c.get("/admin/wartung").status_code == 403, f"{role} 403 page")
    check(post(c, "/admin/wartung/auftrag", {"action": "backup"}).status_code == 403, f"{role} 403 post")
anon = app.test_client()
check(anon.get("/admin/wartung").status_code in (302, 401), "anon redirect")
check(anon.get("/admin/wartung/auftrag/20261103101010-abcdef12.json").status_code in (302, 401), "anon json")
# CSRF
check(a.post("/admin/wartung/auftrag", data={"action": "backup"}).status_code == 400, "csrf")

qdir = os.path.join(tmp, "jobs", "queue")
def queued():
    return [json.load(open(f)) | {"id": os.path.basename(f)[:-5]} for f in sorted(glob.glob(qdir + "/*.json"))]

# ungültige Aktionen
check(post(a, "/admin/wartung/auftrag", {"action": "rm -rf"}).status_code == 400, "bad action")
r = post(a, "/admin/wartung/auftrag", {"action": "rollback", "param": "c" * 40, "confirm": "ZURÜCK"}, follow_redirects=True)
check("nicht in der Liste" in r.get_data(as_text=True) and not queued(), "unknown commit")
r = post(a, "/admin/wartung/auftrag", {"action": "rollback", "param": "a" * 40, "confirm": "ZURÜCK"}, follow_redirects=True)
check("läuft bereits" in r.get_data(as_text=True) and not queued(), "current commit")
r = post(a, "/admin/wartung/auftrag", {"action": "rollback", "param": "b" * 40, "confirm": "ja"}, follow_redirects=True)
check("eintippen" in r.get_data(as_text=True) and not queued(), "rollback confirm")
r = post(a, "/admin/wartung/auftrag", {"action": "restore", "param": "../../etc/passwd", "confirm": "EINSPIELEN"}, follow_redirects=True)
check("nicht in der Liste" in r.get_data(as_text=True) and not queued(), "restore traversal")
r = post(a, "/admin/wartung/auftrag", {"action": "restore", "param": "schuetzen-neubau-2026-11-02-101010.tar.gz", "confirm": ""}, follow_redirects=True)
check(not queued(), "restore confirm")

# gültiger Auftrag
r = post(a, "/admin/wartung/auftrag", {"action": "backup", "param": "egal"})
check(r.status_code == 302 and "auftrag=" in r.headers["Location"], "redirect to job")
jobs = queued(); check(len(jobs) == 1 and jobs[0]["action"] == "backup" and jobs[0]["param"] == "" and jobs[0]["by"] == "Administrator" or jobs[0]["by"], "job file")
check(re.match(r"^\d{14}-[0-9a-f]{8}$", jobs[0]["id"]), "job id")
check(not [f for f in os.listdir(os.path.join(tmp, "jobs")) if f.endswith(".tmp")], "no tmp left")
jid = jobs[0]["id"]
r = a.get(f"/admin/wartung/auftrag/{jid}.json"); check(r.json["state"] == "queued" and r.json["label"] == "wartet", "json queued")
r = a.get("/admin/wartung?auftrag=" + jid); body = r.get_data(as_text=True)
check('data-state="queued"' in body and "disabled>Starten" in body, "live box + disabled")
# zweiter Auftrag während einer läuft
r = post(a, "/admin/wartung/auftrag", {"action": "pruefen"}, follow_redirects=True)
check("schon ein Auftrag" in r.get_data(as_text=True) and len(queued()) == 1, "one at a time")
check(a.get("/admin/wartung/auftrag/../../wartung.json").status_code == 404, "json traversal")
check(a.get("/admin/wartung/auftrag/abc.json").status_code == 404, "json bad id")
# fertig simulieren
os.remove(os.path.join(qdir, jid + ".json"))
json.dump({"id": jid, "action": "backup", "state": "done", "rc": 0, "started": "2026-11-03T10:00:00", "ended": "2026-11-03T10:00:05", "by": "x"},
          open(os.path.join(tmp, "jobs", "log", jid + ".json"), "w"))
open(os.path.join(tmp, "jobs", "log", jid + ".log"), "w").write("Start\n\x1b[1;32m==> Sicherung\x1b[0m\n<script>x</script>\nEnde")
r = a.get(f"/admin/wartung/auftrag/{jid}.json"); check(r.json["state"] == "done" and "\x1b" not in r.json["log"] and "==> Sicherung" in r.json["log"], "json done")
body = a.get("/admin/wartung?auftrag=" + jid).get_data(as_text=True)
check("&lt;script&gt;x" in body and "<script>x" not in body, "log escaped")
check("Zeitpläne &amp; Verlauf" in body and "fertig" in body, "history")
check(body.count('class="card fold"') >= 5 and "Alles aktualisieren" in body and 'class="state-grid"' in body, "new layout: tiles, main action, folds")
check(body.index("state-grid") < body.index("main-action") < body.index("fold-list"), "order: state, main action, folds")
check(body.index("state-grid") < body.index('id="auftrag"') < body.index("main-action"), "terminal output below status tiles")
# restore + rollback gültig
r = post(a, "/admin/wartung/auftrag", {"action": "restore", "param": "schuetzen-neubau-2026-11-02-101010.tar.gz", "confirm": "einspielen"})
j = queued(); check(len(j) == 1 and j[0]["action"] == "restore" and j[0]["param"].endswith("101010.tar.gz"), "restore queued")
os.remove(os.path.join(qdir, j[0]["id"] + ".json"))
r = post(a, "/admin/wartung/auftrag", {"action": "rollback", "param": "b" * 40, "confirm": "zurück"})
j = queued(); check(len(j) == 1 and j[0]["param"] == "b" * 40, "rollback queued")
os.remove(os.path.join(qdir, j[0]["id"] + ".json"))
st["reboot_required"] = True; st["reboot_pkgs"] = ["linux-image-7.0.0-42-generic"]
json.dump(st, open(os.path.join(tmp, "wartung.json"), "w"))
body = a.get("/admin/wartung").get_data(as_text=True)
check("Linux-Updates installieren" in body, "linux button")
check("Server neu starten" in body and "wegen linux-image" in body and ">Neu starten<" in body, "reboot button")
_m = body[body.index("main-action"):body.index("fold-list")]
check("Server neu starten" in _m and "btn small-btn primary" in _m, "reboot offered in main action when needed")
r = post(a, "/admin/wartung/auftrag", {"action": "reboot", "param": "x"})
j = queued(); check(len(j) == 1 and j[0]["action"] == "reboot" and j[0]["param"] == "", "reboot queued")
os.remove(os.path.join(qdir, j[0]["id"] + ".json"))
_d = a.get("/").get_data(as_text=True); check("Neustart nötig" in _d and "Server neu starten" in _d and "Zur Wartung" in _d, "dashboard reboot hint")
check("admin/wartung" in a.get("/").get_data(as_text=True), "dashboard link")
print("app tests ok:", ok)

# ---------------- Runner mit Stub-Skripten ----------------
repo = tempfile.mkdtemp()
src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(repo + "/scripts")
shutil.copy(src + "/scripts/runner.py", repo + "/scripts/runner.py"); shutil.copy(src + "/scripts/safeio.py", repo + "/scripts/safeio.py")
open(repo + "/scripts/snapshot.py", "w").write("open(__import__('os').path.join(__import__('os').path.dirname(__file__),'..','snap.done'),'w')\n")
for name in ("scripts/linux-updates.sh", "scripts/backup.sh", "scripts/pruefen.sh", "scripts/wartung.sh", "scripts/restore.sh", "update.sh"):
    p = os.path.join(repo, name)
    open(p, "w").write(f'#!/bin/sh\necho "{name} $*"\n[ "$1" = "--force" ] && exit 3\nexit 0\n'); os.chmod(p, 0o755)
bdir = tempfile.mkdtemp()
open(os.path.join(bdir, "schuetzen-manuell-2026-11-03-101010.tar.gz"), "w").write("x")
os.symlink("/etc/passwd", os.path.join(bdir, "schuetzen-manuell-2026-11-03-111111.tar.gz"))
open(repo + "/.env", "w").write(f"BACKUP_DIR={bdir}\n")
subprocess.run(["git", "init", "-q"], cwd=repo); subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "eins"], cwd=repo)
c1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "zwei"], cwd=repo)
q = repo + "/data/jobs/queue"; os.makedirs(q); os.makedirs(repo + "/data/jobs/log")
def job(n, action, param=""):
    jid = f"2026110310{n:04d}-{n:08x}"
    json.dump({"action": action, "param": param, "by": "T"}, open(f"{q}/{jid}.json", "w")); return jid
rb = open(repo + "/scripts/runner.py").read().replace('"/usr/sbin/shutdown" if os.path.exists("/usr/sbin/shutdown") else "/sbin/shutdown"', 'os.path.join(APP_DIR, "fake-shutdown")')
rb = rb.replace('open("/proc/uptime")', 'open(os.path.join(APP_DIR, "uptime"))')
open(repo + "/uptime", "w").write("5000.0 1.0")
open(repo + "/scripts/runner.py", "w").write(rb)
open(repo + "/fake-shutdown", "w").write('#!/bin/sh\necho "shutdown $*"\n'); os.chmod(repo + "/fake-shutdown", 0o755)
ids = {"reboot": job(20, "reboot", "egal"), "linux": job(21, "linux"),
    "backup": job(1, "backup"), "rebuild": job(2, "rebuild"), "rb_ok": job(3, "rollback", c1[:10]),
    "rb_bad": job(4, "rollback", "deadbeef; rm -rf /"), "rb_missing": job(5, "rollback", "deadbeefdead"),
    "rs_ok": job(6, "restore", "schuetzen-manuell-2026-11-03-101010.tar.gz"),
    "rs_trav": job(7, "restore", "../../etc/passwd"), "rs_link": job(8, "restore", "schuetzen-manuell-2026-11-03-111111.tar.gz"),
    "evil": job(9, "sh"),
}
open(f"{q}/kaputt.json", "w").write("{")
open(f"{q}/20261103100010-0000000a.json", "w").write("[1,2]")
open(f"{q}/fremd.txt", "w").write("x")
r = subprocess.run([sys.executable, repo + "/scripts/runner.py"], capture_output=True, text=True, env=dict(os.environ, BACKUP_DIR=""))
check(r.returncode == 0, "runner rc " + r.stderr)
check(os.listdir(q) == [], "queue empty")
def res(k):
    jid = ids[k]; return json.load(open(f"{repo}/data/jobs/log/{jid}.json")), open(f"{repo}/data/jobs/log/{jid}.log").read()
s, l = res("backup"); check(s["state"] == "done" and "scripts/backup.sh manuell" in l, "backup ran")
s, l = res("rebuild"); check(s["state"] == "failed" and s["rc"] == 3 and "update.sh --force" in l, "rebuild rc")
s, l = res("rb_ok"); check(s["state"] == "done" and f"--rollback {c1[:10]}" in l, "rollback ok")
for k in ("rb_bad", "rb_missing", "rs_trav", "rs_link", "evil"):
    s, l = res(k); check(s["state"] == "failed" and "Abgelehnt" in l, f"{k} rejected: {l}")
s, l = res("reboot"); check(s["state"] == "done" and "shutdown -r +1" in l and "1 Minute neu" in l, "reboot ran: " + l)
s, l = res("linux"); check(s["state"] == "done" and "scripts/linux-updates.sh" in l, "linux ran")
s, l = res("rs_ok"); check(s["state"] == "done" and f"--ja {bdir}/schuetzen-manuell" in l, "restore ok")
check(os.path.exists(repo + "/snap.done"), "snapshot called")
check(len(os.listdir(repo + "/data/jobs/log")) == 2 * len(ids), "logs count (invalid ones dropped)")
# Aufräumen: nur 40 behalten
for n in range(100, 150):
    job(n, "pruefen")
subprocess.run([sys.executable, repo + "/scripts/runner.py"], capture_output=True)
check(len([f for f in os.listdir(repo + "/data/jobs/log") if f.endswith(".json")]) == 40, "keep 40")
open(repo + "/uptime", "w").write("120.0 1.0")
jid_r = job(300, "reboot")
subprocess.run([sys.executable, repo + "/scripts/runner.py"], capture_output=True)
check("10 Minuten" in open(f"{repo}/data/jobs/log/{jid_r}.log").read(), "reboot loop guard")
print("all ok:", ok)
