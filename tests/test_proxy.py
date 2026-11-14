"""Proxy-Auswahl (Caddy): Prüfung der Eingaben, erzeugte Caddyfile, .env-Änderung, App-Formular."""
import os, sys, tempfile, io, contextlib, shutil, subprocess, re
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import proxy as P
import runner as R
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1
def bad(param):
    try:
        P.parse(param); return False
    except ValueError:
        return True

# ---- Eingaben
c = P.parse("tunnel|1|20|10.0.0.0/8 192.168.1.7|")
check(c == {"mode": "tunnel", "gzip": True, "upload_mb": 20, "wartung_nets": ["10.0.0.0/8", "192.168.1.7/32"], "domain": ""}, c)
check(P.parse("https|0|5||App.Verein.de")["domain"] == "app.verein.de", "domain lowercased")
for p in ("", "tunnel|1|20|", "boese|1|20||", "tunnel|2|20||", "tunnel|1|0||", "tunnel|1|101||", "tunnel|1|abc||",
          "tunnel|1|20|1.2.3.4;rm -rf /|", "tunnel|1|20|1.2.3.4\nrespond 200|", "https|1|20||", "https|1|20||a.b.c}{",
          "https|1|20||evil.de\n:80 {", "tunnel|1|20|" + " ".join(f"10.0.{i}.0/24" for i in range(21)) + "|"):
    check(bad(p), f"rejected {p!r}")
check(P.parse("aus|1|20||ignoriert.de")["domain"] == "", "domain only for https")

# ---- Caddyfile
t = P.caddyfile(P.parse("tunnel|1|20|10.0.0.0/8|"))
check(":8080 {" in t and "auto_https off" in t and "CF-Connecting-IP" in t and "encode zstd gzip" in t
      and "max_size 20MB" in t and "not client_ip 10.0.0.0/8" in t and "Strict-Transport" not in t, "tunnel file")
h = P.caddyfile(P.parse("https|0|5||app.verein.de"))
check("app.verein.de {" in h and "encode" not in h and "@gesperrt" not in h and "Strict-Transport-Security" in h
      and "trusted_proxies" not in h, "https file")
caddy = os.environ.get("CADDY_BIN") or shutil.which("caddy")
if caddy:   # mit echtem Caddy prüfen, falls vorhanden
    d = tempfile.mkdtemp()
    for name, text in (("t", t), ("h", h), ("a", P.caddyfile(P.parse("tunnel|0|100||")))):
        f = os.path.join(d, name)
        open(f, "w").write(text.replace("mannschaften:8000", "127.0.0.1:8000"))
        r = subprocess.run([caddy, "validate", "--config", f, "--adapter", "caddyfile"], capture_output=True, text=True,
                           env=dict(os.environ, HOME=d, XDG_DATA_HOME=d, XDG_CONFIG_HOME=d))
        check(r.returncode == 0, f"caddy validate {name}: {r.stderr[-500:]}")
    print("  (mit echtem Caddy geprüft)")

# ---- .env
env = "# Kommentar\nSECRET_KEY=abc\nTRUST_PROXY=0\n# COMPOSE_FILE=alt\nCOMPOSE_FILE=docker-compose.yml\n"
new = P.env_with(env, {"COMPOSE_FILE": P.MODES["tunnel"], "CADDYFILE": "./Caddyfile.local", "TRUST_PROXY": "1"})
check("SECRET_KEY=abc" in new and "# Kommentar" in new and "# COMPOSE_FILE=alt" in new
      and "COMPOSE_FILE=docker-compose.yml:docker-compose.caddy-tunnel.yml" in new and new.count("COMPOSE_FILE=docker") == 1
      and "TRUST_PROXY=1" in new and "CADDYFILE=./Caddyfile.local" in new, new)
check(P.env_with("A=1\n", {"A": None}) == "\n", "remove key")
for f in ("docker-compose.caddy.yml", "docker-compose.caddy-tunnel.yml"):
    check("${CADDYFILE:-./Caddyfile}" in open(os.path.join(ROOT, f)).read(), f"{f} mounts CADDYFILE")
check("Caddyfile.local" in open(os.path.join(ROOT, ".gitignore")).read(), "generated file ignored by git")

# ---- Wartungsdienst
cmd, err = R.build_command("proxy", "tunnel|1|20||")
check(err is None and cmd[-2].endswith("proxy.py") and cmd[-1] == "tunnel|1|20||", "runner command")
check(R.build_command("proxy", "tunnel|1|20|x|")[0] is None and R.build_command("proxy", None)[0] is None, "runner rejects")

# ---- App-Formular
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(ROOT, "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
f = lambda **kw: A.proxy_param(dict({"mode": "tunnel", "upload_mb": "20", "gzip": "1"}, **kw), "192.168.1.50")
check(f() == ("tunnel|1|20||", None), f())
check(f(wartung_nets="10.0.0.0/8")[0] is None and "aussperren" in f(wartung_nets="10.0.0.0/8")[1], "lockout prevented")
check(f(wartung_nets="192.168.1.0/24, 10.0.0.0/8")[0] == "tunnel|1|20|192.168.1.0/24 10.0.0.0/8|", "own net ok")
check(f(mode="https")[0] is None and f(mode="https", domain="app.verein.de")[0] == "https|1|20||app.verein.de", "https needs domain")
check(f(mode="x")[0] is None and f(upload_mb="500")[0] is None, "form validation")
for p in [f(wartung_nets="192.168.1.0/24")[0], f(mode="https", domain="app.verein.de")[0], f(mode="aus", gzip="")[0]]:
    P.parse(p)   # App-Ausgabe besteht immer die Prüfung des Wartungsdienstes
ok += 1
app = A.app; app.config["TESTING"] = True
os.makedirs(os.path.join(tmp, "jobs", "queue"))
c = app.test_client(); c.get("/login")
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "adminpass1", "csrf": "t"})
import sqlite3; sqlite3.connect(tmp + "/t.db").execute("UPDATE users SET must_change_pw=0").connection.commit()
b = c.get("/admin/wartung").get_data(as_text=True)
check('id="proxy"' in b and "Wird Caddy gebraucht?" in b and 'value="aus" checked' in b, "maintenance shows proxy")
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/admin/wartung/auftrag", data={"csrf": "t", "action": "proxy", "mode": "tunnel", "upload_mb": "20", "gzip": "1"})
q = os.listdir(os.path.join(tmp, "jobs", "queue"))
import json
check(len(q) == 1 and json.load(open(os.path.join(tmp, "jobs", "queue", q[0])))["param"] == "tunnel|1|20||", "job queued")

# ---- Ganzer Ablauf mit Schein-docker (Erfolg und Rückfall bei Fehler)
def run_flow(param, fail_on=""):
    d = tempfile.mkdtemp()
    shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(d, "scripts"))
    os.makedirs(os.path.join(d, "data")); os.makedirs(os.path.join(d, "bin"))
    open(os.path.join(d, ".env"), "w").write("SECRET_KEY=geheim\nTRUST_PROXY=1\n")
    os.chmod(os.path.join(d, ".env"), 0o600)
    log = os.path.join(d, "docker.log")
    fake = os.path.join(d, "bin", "docker")
    open(fake, "w").write(f"""#!/bin/sh
echo "$@" >> {log}
case "$*" in *{fail_on or 'NIE'}*) exit 1;; esac
exit 0
""")
    os.chmod(fake, 0o755)
    r = subprocess.run([sys.executable, "-I", os.path.join(d, "scripts", "proxy.py"), param], capture_output=True, text=True,
                       env=dict(os.environ, PATH=os.path.join(d, "bin") + ":" + os.environ["PATH"]))
    return d, r, open(log).read() if os.path.exists(log) else ""
d, r, calls = run_flow("tunnel|1|20||")
env = open(os.path.join(d, ".env")).read()
check(r.returncode == 0 and "COMPOSE_FILE=docker-compose.yml:docker-compose.caddy-tunnel.yml" in env and "SECRET_KEY=geheim" in env
      and oct(os.stat(os.path.join(d, ".env")).st_mode & 0o777) == "0o600", r.stdout + r.stderr)
check("caddy validate" in calls and "up -d --remove-orphans" in calls and "restart caddy" in calls, calls)
check('"mode": "tunnel"' in open(os.path.join(d, "data", "wartung.json")).read(), "status written")
check("localhost:8080" in r.stdout, "tunnel hint")
d, r, calls = run_flow("tunnel|1|20||", fail_on="validate")
check(r.returncode == 1 and open(os.path.join(d, ".env")).read() == "SECRET_KEY=geheim\nTRUST_PROXY=1\n"
      and not os.path.exists(os.path.join(d, "Caddyfile.local")) and "wiederhergestellt" in r.stdout, "rollback on invalid")
d, r, calls = run_flow("aus|1|20||")
check(r.returncode == 0 and "COMPOSE_FILE=docker-compose.yml\n" in open(os.path.join(d, ".env")).read()
      and "TRUST_PROXY=1" in open(os.path.join(d, ".env")).read() and "validate" not in calls, "switch off keeps TRUST_PROXY")
d, r, calls = run_flow("tunnel|1|20|kaputt|")
check(r.returncode == 2 and not calls and open(os.path.join(d, ".env")).read().startswith("SECRET_KEY"), "invalid param: nothing touched")
print("PROXY OK", ok)
