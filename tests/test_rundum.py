"""Rundumtest: jede Route ohne CSRF-Token, mit Störeingaben, ohne Anmeldung; dazu Sitzung, Upload, Push, Paket-Updates."""
import os, sys, tempfile, io, contextlib, sqlite3, re, itertools
from datetime import date, timedelta
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
app = A.app; app.config["TESTING"] = False; app.config["PROPAGATE_EXCEPTIONS"] = False
db = sqlite3.connect(tmp + "/t.db"); db.execute("UPDATE users SET must_change_pw=0")
H = A.generate_password_hash("passwort12")
sid = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw, cal_token) VALUES('Anna','anna',?, 'shooter',1,0,'tok')", (H,)).lastrowid
t = db.execute("INSERT INTO teams(name, discipline) VALUES('LG 1','LG')").lastrowid
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (t, sid))
m = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?, '19:00','X',1)", (t, (date.today()+timedelta(days=3)).isoformat())).lastrowid
db.commit()
def login(u, p):
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/login", data={"username": u, "password": p, "csrf": "t"})
    with c.session_transaction() as s: s["csrf"] = "t"
    return c
adm = login("admin", "adminpass1"); sh = login("anna", "passwort12")
rules = [r for r in app.url_map.iter_rules() if r.endpoint != "static"]
def urls(rule, val):
    args = {a: val for a in rule.arguments}
    try:
        with app.test_request_context():
            return A.url_for(rule.endpoint, **args)
    except Exception:
        return None
problems = []
# 1) CSRF: jede POST-Route ohne Token -> 400 (als Admin, also mit allen Rechten)
n_post = 0
for r in rules:
    if "POST" not in r.methods: continue
    for v in ([1] if r.arguments else [None]):
        u = urls(r, 1) if r.arguments else urls(r, None)
        if not u: continue
        n_post += 1
        with adm.session_transaction() as s: s["csrf"] = "t"
        resp = adm.post(u, data={"x": "1"})
        if resp.status_code != 400:
            problems.append(f"CSRF fehlt? POST {u} -> {resp.status_code}")
        resp = adm.post(u, json={"x": 1})
        if resp.status_code != 400:
            problems.append(f"CSRF fehlt (JSON)? POST {u} -> {resp.status_code}")
# 2) Störeingaben: alle GET-Routen mit kaputten IDs/Parametern -> nie 500
FUZZ = ["0", "-1", "999999999", "1'--", "%00", "../../etc/passwd", "<x>", "1e9", "NaN", "a" * 300]
QS = ["?next=//evil.com", "?m=2026-99", "?saison=' OR 1", "?team=abc", "?q=%27", "?sort=x;--", "?page=-5", "?d=9999-99-99", "?filter=<x>"]
n_get = 0
before = db.execute("SELECT COUNT(*) FROM error_log").fetchone()[0]
for r in rules:
    if "GET" not in r.methods: continue
    vals = FUZZ if r.arguments else [None]
    for v in vals:
        try:
            with app.test_request_context():
                u = A.url_for(r.endpoint, **{a: v for a in r.arguments}) if r.arguments else A.url_for(r.endpoint)
        except Exception:
            continue
        for c, who in ((adm, "admin"), (sh, "schütze"), (app.test_client(), "anonym")):
            for qs in [""] + QS[:4 if v is None else 1]:
                n_get += 1
                resp = c.get(u + qs)
                if resp.status_code >= 500:
                    problems.append(f"500 als {who}: GET {u+qs}")
                if who == "anonym" and resp.status_code == 200 and r.endpoint not in ("login", "healthz", "manifest", "web_manifest", "service_worker", "login_2fa", "calendar_feed"):
                    problems.append(f"ohne Anmeldung lesbar: {u}")
after = db.execute("SELECT COUNT(*) FROM error_log").fetchone()[0]
for row in db.execute("SELECT path, message FROM error_log ORDER BY id DESC LIMIT 15"):
    problems.append(f"Fehlerprotokoll: {row[0]} -> {row[1][:120]}")
# 3) Störeingaben an POST-Routen (mit gültigem Token) als Admin
n_fuzz = 0
for r in rules:
    if "POST" not in r.methods or r.endpoint in ("logout", "admin_job_create"): continue
    u = urls(r, 1) if r.arguments else urls(r, None)
    if not u: continue
    for payload in ({"name": "<x>" * 200, "date": "9999-99-99", "time": "25:99", "status": "x", "role": "admin", "action": "x' OR 1"},
                    {"lineup": ["abc", "-1"], "rings_1": "1e400", "id": "../../x", "ids": ["1;DROP"], "season": "2026/27\n"},
                    {"start": "2026-02-30", "end": "x", "upload_mb": "-5", "remind_days": "999999999999999999"}):
        with adm.session_transaction() as s: s["csrf"] = "t"
        n_fuzz += 1
        resp = adm.post(u, data=dict(payload, csrf="t"))
        if resp.status_code >= 500:
            problems.append(f"500 bei Störeingabe: POST {u} {list(payload)[:3]}")
print(f"  POST-Routen ohne Token: {n_post}, GET-Aufrufe: {n_get}, POST-Störeingaben: {n_fuzz}")
problems += [f"Fehlerprotokoll: {r[0]} -> {r[1][:120]}" for r in sqlite3.connect(tmp + "/t.db").execute("SELECT path, message FROM error_log")]
assert not problems, "\n".join(dict.fromkeys(problems))
ok = 3

# Abmelden macht auch Kopien des Sitzungs-Cookies ungültig
c = login("anna", "passwort12"); copy = app.test_client(); copy.set_cookie("session", c.get_cookie("session").value)
assert copy.get("/").status_code == 200
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/logout", data={"csrf": "t"})
assert copy.get("/").status_code == 302, "cookie copy still valid after logout"; ok += 1
assert login("anna", "passwort12").get("/").status_code == 200, "login after logout"; ok += 1
# Bild-Upload: EPS u. a. werden gar nicht erst geöffnet
c = login("anna", "passwort12")
eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%BeginBinary: -100\n"
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/profil", data={"csrf": "t", "action": "avatar", "avatar": (io.BytesIO(eps), "x.jpg")}, content_type="multipart/form-data",
           follow_redirects=True)
assert "konnte nicht gelesen" in r.get_data(as_text=True), "eps not rejected"; ok += 1
# Push folgt keinen Umleitungen
import webpush, threading, http.server, urllib.request, urllib.error
class Redir(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(302); self.send_header("Location", "http://127.0.0.1:9/x"); self.end_headers()
    def log_message(self, *a): pass
srv = http.server.HTTPServer(("127.0.0.1", 0), Redir); threading.Thread(target=srv.serve_forever, daemon=True).start()
try:
    webpush._OPENER.open(urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/", data=b"x", method="POST"), timeout=3)
    raise AssertionError("push follows redirect")
except urllib.error.HTTPError as e:
    assert e.code == 302
ok += 1
# Neubau holt Paket-Updates (nicht nur, wenn sich requirements.txt ändert)
assert "ARG PIP_REFRESH" in open(os.path.join(ROOT, "Dockerfile")).read() and 'PIP_REFRESH="$(date +%F)"' in open(os.path.join(ROOT, "update.sh")).read()
ok += 1
# Dateien, die nie erreichbar sein dürfen
for p in ("/.env", "/.git/config", "/data/verein.db", "/static/../app.py", "/static/..%2fapp.py", "/app.py", "/requirements.txt"):
    assert login("anna", "passwort12").get(p).status_code == 404, p
ok += 1
print("RUNDUM OK", ok)
