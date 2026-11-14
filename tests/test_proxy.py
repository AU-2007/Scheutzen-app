"""Caddy über die Wartung: Prüfung der Eingaben, erzeugte Caddyfile, .env, Zertifikat-Upload, Ablauf mit Rückfall."""
import os, sys, tempfile, io, contextlib, shutil, subprocess, json, sqlite3
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
CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"

# ---- Eingaben
c = P.parse("1|tunnel|1|20|10.0.0.0/8 192.168.1.7||auto|")
check(c["mode"] == "tunnel" and c["wartung_nets"] == ["10.0.0.0/8", "192.168.1.7/32"] and c["domains"] == [], c)
c = P.parse("1|direkt|0|5||App.Verein.de www.verein.de app.verein.de|eigen|a@b.de")
check(c["mode"] == "direkt" and c["domains"] == ["app.verein.de", "www.verein.de"] and c["tls"] == "eigen", c)
check(P.parse("0|direkt|1|20|||auto|")["mode"] == "aus", "off needs no domain")
for p in ("", "1|tunnel|1|20||auto|", "2|tunnel|1|20|||auto|", "1|boot|1|20|||auto|", "1|tunnel|1|0|||auto|",
          "1|tunnel|1|20|1.2.3.4;rm -rf /||auto|", "1|tunnel|1|20|1.2.3.4\nrespond 200||auto|",
          "1|direkt|1|20|||auto|", "1|direkt|1|20||a.b.c}{|auto|", "1|direkt|1|20||evil.de\n:80 {|auto|",
          "1|direkt|1|20||app.de|bogus|", "1|direkt|1|20||app.de|auto|keine-mail", "1|direkt|1|20||*.app.de|auto|",
          "1|direkt|1|20||app.de|auto|a@b.de\n}", "1|direkt|1|20||" + " ".join(f"s{i}.app.de" for i in range(11)) + "|auto|"):
    check(bad(p), f"rejected {p!r}")

# ---- Caddyfile
t = P.caddyfile(P.parse("1|tunnel|1|20|10.0.0.0/8||auto|"))
check(":8080 {" in t and "auto_https off" in t and "CF-Connecting-IP" in t and "encode zstd gzip" in t and "tls" not in t.replace("auto_https", "")
      and "max_size 20MB" in t and "not client_ip 10.0.0.0/8" in t and "Strict-Transport" not in t, t)
h = P.caddyfile(P.parse("1|direkt|0|5||app.verein.de www.verein.de|auto|a@b.de"))
check("app.verein.de, www.verein.de {" in h and "email a@b.de" in h and "encode" not in h and "Strict-Transport-Security" in h, h)
check("tls /certs/cert.pem /certs/key.pem" in P.caddyfile(P.parse("1|direkt|1|20||app.de|eigen|")), "own cert")
check("tls internal" in P.caddyfile(P.parse("1|direkt|1|20||app.de *.app.de|intern|")), "internal cert")
caddy = os.environ.get("CADDY_BIN") or shutil.which("caddy")
if caddy:   # mit echtem Caddy prüfen, falls vorhanden
    d = tempfile.mkdtemp()
    sub = subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "2",
                          "-subj", "/CN=app.de", "-keyout", os.path.join(d, "key.pem"), "-out", os.path.join(d, "cert.pem")], capture_output=True)
    files = {"t": t, "h": h, "i": P.caddyfile(P.parse("1|direkt|1|20||app.de *.app.de|intern|")),
             "e": P.caddyfile(P.parse("1|direkt|1|20||app.de|eigen|")).replace("/certs/", d + "/")}
    for name, text in files.items():
        f = os.path.join(d, name)
        open(f, "w").write(text.replace("mannschaften:8000", "127.0.0.1:8000"))
        r = subprocess.run([caddy, "validate", "--config", f, "--adapter", "caddyfile"], capture_output=True, text=True,
                           env=dict(os.environ, HOME=d, XDG_DATA_HOME=d, XDG_CONFIG_HOME=d))
        check(r.returncode == 0, f"caddy validate {name}: {r.stderr[-600:]}")
    print("  (mit echtem Caddy geprüft)")

# ---- .env
env = "# Kommentar\nSECRET_KEY=abc\nTRUST_PROXY=0\n# COMPOSE_FILE=alt\nCOMPOSE_FILE=docker-compose.yml\n"
new = P.env_with(env, {"COMPOSE_FILE": P.COMPOSE["tunnel"], "CADDYFILE": "./Caddyfile.local", "TRUST_PROXY": "1"})
check("SECRET_KEY=abc" in new and "# Kommentar" in new and "# COMPOSE_FILE=alt" in new
      and "COMPOSE_FILE=docker-compose.yml:docker-compose.caddy-tunnel.yml" in new and new.count("COMPOSE_FILE=docker") == 1
      and "TRUST_PROXY=1" in new and "CADDYFILE=./Caddyfile.local" in new, new)
for f in ("docker-compose.caddy.yml", "docker-compose.caddy-tunnel.yml"):
    check("${CADDYFILE:-./Caddyfile}" in open(os.path.join(ROOT, f)).read(), f"{f} mounts CADDYFILE")
check("./caddy-certs:/certs:ro" in open(os.path.join(ROOT, "docker-compose.caddy.yml")).read(), "certs mounted")
gi = open(os.path.join(ROOT, ".gitignore")).read()
check("Caddyfile.local" in gi and "caddy-certs/" in gi, "generated files ignored by git")

# ---- Wartungsdienst
cmd, err = R.build_command("proxy", "1|tunnel|1|20|||auto|")
check(err is None and cmd[-2].endswith("proxy.py") and cmd[-1] == "1|tunnel|1|20|||auto|", "runner command")
check(R.build_command("proxy", "1|tunnel|1|20|x||auto|")[0] is None and R.build_command("proxy", None)[0] is None, "runner rejects")

# ---- Ganzer Ablauf mit Schein-docker
def run_flow(param, fail_on="", upload=None, keep=None):
    d = keep or tempfile.mkdtemp()
    if not keep:
        shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(d, "scripts"))
        os.makedirs(os.path.join(d, "data")); os.makedirs(os.path.join(d, "bin"))
        open(os.path.join(d, ".env"), "w").write("SECRET_KEY=geheim\nTRUST_PROXY=1\n")
        os.chmod(os.path.join(d, ".env"), 0o600)
    if upload:
        os.makedirs(os.path.join(d, "data", "proxy"), exist_ok=True)
        open(os.path.join(d, "data", "proxy", "cert.pem"), "w").write(upload[0])
        open(os.path.join(d, "data", "proxy", "key.pem"), "w").write(upload[1])
    log = os.path.join(d, "docker.log")
    open(os.path.join(d, "bin", "docker"), "w").write(f"#!/bin/sh\necho \"$@\" >> {log}\ncase \"$*\" in *{fail_on or 'NIE'}*) exit 1;; esac\nexit 0\n")
    os.chmod(os.path.join(d, "bin", "docker"), 0o755)
    open(log, "w").close()
    r = subprocess.run([sys.executable, "-I", os.path.join(d, "scripts", "proxy.py"), param], capture_output=True, text=True,
                       env=dict(os.environ, PROXY_CERT_TRIES="1", PATH=os.path.join(d, "bin") + ":" + os.environ["PATH"]))
    return d, r, open(log).read()
rd = lambda d, *p: open(os.path.join(d, *p)).read()
d, r, calls = run_flow("1|tunnel|1|20|||auto|")
env = rd(d, ".env")
check(r.returncode == 0 and "COMPOSE_FILE=docker-compose.yml:docker-compose.caddy-tunnel.yml" in env and "SECRET_KEY=geheim" in env
      and oct(os.stat(os.path.join(d, ".env")).st_mode & 0o777) == "0o600", r.stdout + r.stderr)
check("caddy validate" in calls and "up -d --remove-orphans" in calls and "restart caddy" in calls, calls)
check('"via": "tunnel"' in rd(d, "data", "wartung.json") and "localhost:8080" in r.stdout, "status + hint")
d, r, calls = run_flow("1|tunnel|1|20|||auto|", fail_on="validate")
check(r.returncode == 1 and rd(d, ".env") == "SECRET_KEY=geheim\nTRUST_PROXY=1\n"
      and not os.path.exists(os.path.join(d, "Caddyfile.local")) and "wiederhergestellt" in r.stdout, "rollback on invalid")
d, r, calls = run_flow("0|tunnel|1|20|||auto|")
check(r.returncode == 0 and "COMPOSE_FILE=docker-compose.yml\n" in rd(d, ".env") and "TRUST_PROXY=1" in rd(d, ".env")
      and "validate" not in calls, "switch off keeps TRUST_PROXY")
d, r, calls = run_flow("1|tunnel|1|20|kaputt||auto|")
check(r.returncode == 2 and not calls and rd(d, ".env").startswith("SECRET_KEY"), "invalid param: nothing touched")
# eigenes Zertifikat: ohne Upload abgelehnt, mit Upload übernommen und aus data gelöscht
d, r, calls = run_flow("1|direkt|1|20||app.de|eigen|")
check(r.returncode == 2 and "hochladen" in r.stdout and not calls, "own cert required")
d, r, calls = run_flow("1|direkt|1|20||app.de|eigen|", upload=(CERT, KEY))
check(r.returncode == 0 and rd(d, "caddy-certs", "cert.pem") == CERT and oct(os.stat(os.path.join(d, "caddy-certs", "key.pem")).st_mode & 0o777) == "0o600"
      and not os.path.exists(os.path.join(d, "data", "proxy", "key.pem")) and "DOMAIN=app.de" in rd(d, ".env"), r.stdout + r.stderr)
check("Noch kein Zertifikat abrufbar" in r.stdout or "gültig bis" in r.stdout, "cert status reported")
d2, r, calls = run_flow("1|direkt|1|20||app.de|eigen|", fail_on="validate", upload=(CERT.replace("MIIB", "NEU1"), KEY), keep=d)
check(r.returncode == 1 and rd(d, "caddy-certs", "cert.pem") == CERT, "rollback restores old cert")
d, r, calls = run_flow("1|direkt|1|20||app.de|eigen|", upload=("kaputt", KEY))
check(r.returncode == 2 and "PEM" in r.stdout, "bad pem rejected")
# Symlink im Upload-Ordner wird nicht verfolgt
d = tempfile.mkdtemp(); shutil.copytree(os.path.join(ROOT, "scripts"), os.path.join(d, "scripts"))
os.makedirs(os.path.join(d, "data", "proxy")); os.makedirs(os.path.join(d, "bin"))
open(os.path.join(d, ".env"), "w").write("X=1\n"); secret = os.path.join(d, "geheim.txt"); open(secret, "w").write("root-only")
os.symlink(secret, os.path.join(d, "data", "proxy", "cert.pem")); os.symlink(secret, os.path.join(d, "data", "proxy", "key.pem"))
d, r, calls = run_flow("1|direkt|1|20||app.de|eigen|", keep=d)
check(r.returncode == 2 and not os.path.exists(os.path.join(d, "caddy-certs", "cert.pem")) and open(secret).read() == "root-only", "symlink not followed")

# ---- App-Formular
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(ROOT, "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
f = lambda **kw: A.proxy_param(dict({"on": "1", "via": "tunnel", "upload_mb": "20", "gzip": "1", "tls": "auto"}, **kw), "192.168.1.50")
check(f() == ("1|tunnel|1|20|||auto|", None), f())
check(f(wartung_nets="10.0.0.0/8")[0] is None and "aussperren" in f(wartung_nets="10.0.0.0/8")[1], "lockout prevented")
check(f(wartung_nets="192.168.1.0/24, 10.0.0.0/8")[0] == "1|tunnel|1|20|192.168.1.0/24 10.0.0.0/8||auto|", "own net ok")
check(f(via="direkt")[0] is None and f(via="direkt", domains="https://App.Verein.de/\nwww.verein.de")[0]
      == "1|direkt|1|20||app.verein.de www.verein.de|auto|", "domains normalized")
check(f(via="x")[0] is None and f(upload_mb="500")[0] is None and f(email="nope")[0] is None and f(tls="x")[0] is None, "form validation")
check(f(cert_pem="x", key_pem="y")[0] is None, "bad pem in form")
p, e = f(via="direkt", domains="app.de", tls="eigen", cert_pem=CERT, key_pem=KEY)
check(e is None and open(os.path.join(tmp, "proxy", "key.pem")).read().strip() == KEY
      and oct(os.stat(os.path.join(tmp, "proxy", "key.pem")).st_mode & 0o777) == "0o600", "upload stored for server")
for p in [f()[0], f(on="")[0], f(via="direkt", domains="a.de b.de", tls="intern", email="x@y.de")[0], A.PROXY_DEFAULT_PARAM]:
    P.parse(p)   # alles, was die App erzeugt, besteht die Prüfung des Wartungsdienstes
ok += 1
app = A.app; app.config["TESTING"] = True
os.makedirs(os.path.join(tmp, "jobs", "queue"))
sqlite3.connect(tmp + "/t.db").execute("UPDATE users SET must_change_pw=0").connection.commit()
c = app.test_client(); c.get("/login")
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "adminpass1", "csrf": "t"})
b = c.get("/admin/wartung").get_data(as_text=True)
check('id="proxy"' in b and "Caddy benutzen" in b and "Über den Cloudflare Tunnel" in b and "Standardwerte" in b, "maintenance shows caddy")
def job(**form):
    for x in os.listdir(os.path.join(tmp, "jobs", "queue")): os.unlink(os.path.join(tmp, "jobs", "queue", x))
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/admin/wartung/auftrag", data=dict({"csrf": "t", "action": "proxy"}, **form))
    q = os.listdir(os.path.join(tmp, "jobs", "queue"))
    return json.load(open(os.path.join(tmp, "jobs", "queue", q[0])))["param"] if q else None
check(job(on="1", via="tunnel", upload_mb="20", gzip="1", tls="auto") == "1|tunnel|1|20|||auto|", "job queued")
check(job(on="1", via="tunnel", upload_mb="30", tls="auto", quick="aus") == "0|tunnel|0|30|||auto|", "quick off keeps rest")
check(job(quick="standard") == A.PROXY_DEFAULT_PARAM, "reset to defaults")

# ---- App nimmt bei ALLOWED_HOSTS nur die Domains an (eigener Prozess, weil beim Start gelesen)
code = """
import os, sys, io, contextlib, tempfile
os.environ.update(DB_PATH=tempfile.mkdtemp() + '/t.db', ADMIN_PASSWORD='adminpass1', ALLOWED_HOSTS='app.de *.verein.de', TRUST_PROXY='1')
sys.path.insert(0, sys.argv[1])
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
c = A.app.test_client()
for h in ('app.de', 'x.verein.de', '127.0.0.1:8000', 'localhost', '203.0.113.5', '203.0.113.5:8000', 'evil.de'):
    print(h, c.get('/healthz', headers={'Host': h}).status_code)
print('fwd', c.get('/healthz', headers={'Host': '127.0.0.1:8000', 'X-Forwarded-Host': '203.0.113.5'}).status_code)
"""
r = subprocess.run([sys.executable, "-c", code, os.path.join(ROOT, "app")], capture_output=True, text=True)
res = dict(l.rsplit(" ", 1) for l in r.stdout.split("\n") if l)
check(res.get("app.de") == "200" and res.get("x.verein.de") == "200" and res.get("127.0.0.1:8000") == "200"
      and res.get("localhost") == "200", r.stdout + r.stderr[-800:])
check(res.get("203.0.113.5") == "400" and res.get("203.0.113.5:8000") == "400" and res.get("evil.de") == "400"
      and res.get("fwd") == "400", r.stdout)
d, r, calls = run_flow("1|direkt|1|20||app.de *.verein.de|intern|")
env = rd(d, ".env")
check("BIND_IP=127.0.0.1" in env and "ALLOWED_HOSTS=app.de *.verein.de" in env and "COOKIE_SECURE=1" in env, env)
d, r, calls = run_flow("1|tunnel|1|20|||auto|", keep=d)
check("ALLOWED_HOSTS" not in rd(d, ".env") and "BIND_IP=127.0.0.1" in rd(d, ".env"), "tunnel drops host list, keeps local bind")
cf = P.caddyfile(P.parse("1|direkt|1|20||app.de *.verein.de|intern|"))
check("strict_sni_host on" in cf and "@fremd not host app.de *.verein.de" in cf and "http:// {\n\tabort\n}" in cf, cf)
check("strict_sni_host" not in P.caddyfile(P.parse("1|tunnel|1|20|||auto|")), "tunnel unaffected")
print("PROXY OK", ok)
