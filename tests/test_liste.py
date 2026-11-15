"""Angriffe aus der bekannten Liste „15 Wege, eine schnell gebaute App zu knacken“ – gegen diese App ausprobiert.
Nicht zutreffend (es gibt keine): Preise, Zahlungen, Gutscheine, KI-Endpunkte, Prompts, offene Registrierung."""
import os, sys, tempfile, io, contextlib, sqlite3, re, json, threading, subprocess
from datetime import date, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(ROOT, "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
import sicherheit as S
app = A.app; app.config["TESTING"] = True
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1
def login(user, pw):
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/login", data={"username": user, "password": pw, "csrf": "t"}); return c
def post(c, url, data=None, **kw):
    with c.session_transaction() as s: s["csrf"] = "t"
    d = dict(data or {}); d["csrf"] = "t"; return c.post(url, data=d, **kw)
db = sqlite3.connect(tmp + "/t.db"); db.row_factory = sqlite3.Row
db.execute("UPDATE users SET must_change_pw=0")
H = A.generate_password_hash("passwort12")
XSS = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'
U = {}
for n, role in (("anna", "shooter"), ("bert", "shooter"), ("lisa", "leader")):
    U[n] = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES(?,?,?,?,1,0)",
                      (XSS if n == "bert" else n.title(), n, H, role)).lastrowid
tA = db.execute("INSERT INTO teams(name, discipline, leader_id) VALUES(?, 'LG', ?)", ("LG 1", U["lisa"])).lastrowid
tB = db.execute("INSERT INTO teams(name, discipline) VALUES(?, 'LP')", (XSS,)).lastrowid
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (tA, U["anna"]))
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (tA, U["bert"]))
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (tB, U["bert"]))
d = lambda k: (date.today() + timedelta(days=k)).isoformat()
mA = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00',?,1)", (tA, d(5), XSS)).lastrowid
mB = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00','X',1)", (tB, d(6))).lastrowid
db.execute("INSERT INTO lineup(match_id, user_id) VALUES(?,?)", (mA, U["bert"]))
db.commit()
adm = login("admin", "adminpass1"); anna = login("anna", "passwort12"); bert = login("bert", "passwort12"); lisa = login("lisa", "passwort12")

# 1) Script im Namen: überall maskiert ausgegeben
post(bert, "/abwesenheit", {"start": d(30), "end": d(31), "note": XSS})
pages = [(adm, "/benutzer"), (adm, f"/benutzer/{U['bert']}"), (adm, f"/mannschaft/{tA}"), (adm, f"/wettkampf/{mA}"),
         (adm, "/mannschaften"), (adm, "/"), (bert, "/profil"), (bert, "/termine"), (bert, "/"), (adm, f"/mannschaft/{tB}"),
         (lisa, f"/wettkampf/{mA}"), (adm, f"/statistik/{U['bert']}")]
for c, url in pages:
    b = c.get(url).get_data(as_text=True)
    check("<script>alert(1)" not in b, f"XSS raw on {url}")   # maskiert: &lt;script&gt; bzw. \u003cscript
    check("<img src=x" not in b, f"XSS img on {url}")
check("&lt;script&gt;" in adm.get("/benutzer").get_data(as_text=True), "name shown escaped")
csp = adm.get("/").headers.get("Content-Security-Policy", "")
check("script-src" in csp and "'unsafe-eval'" not in csp, "CSP header present: " + csp)

# 2) ID in der Adresse ändern
check(anna.get(f"/statistik/{U['bert']}").status_code == 403, "stats of others")
aid = db.execute("SELECT id FROM absences WHERE user_id=?", (U["bert"],)).fetchone()[0]
check(post(anna, f"/abwesenheit/{aid}/loeschen").status_code == 404 and db.execute("SELECT 1 FROM absences WHERE id=?", (aid,)).fetchone(),
      "delete foreign absence")
check(post(anna, f"/wettkampf/{mB}/verfuegbarkeit", {"status": "ja"}).status_code == 403, "answer for foreign team match")
check(post(anna, f"/wettkampf/{mA}/aufstellung", {"lineup": [U["anna"]]}).status_code == 403, "shooter changes lineup")
check(post(lisa, f"/wettkampf/{mB}/aufstellung", {"lineup": [U["lisa"]]}).status_code == 403, "leader of other team")
check(post(anna, f"/mannschaft/{tA}/bearbeiten", {"name": "X"}).status_code == 403, "shooter edits team")
check(anna.get(f"/benutzer/{U['bert']}").status_code == 403, "shooter opens user edit")
check(anna.get("/kalender/" + "a" * 43 + ".ics").status_code == 404, "guessed calendar token")
pk = db.execute("INSERT INTO passkeys(user_id, cred_id, public_key, name, created) VALUES(?, 'x', x'00', 'Bert', '2026')", (U["bert"],)).lastrowid
db.execute("UPDATE users SET password_hash=? WHERE id=?", (H, U["anna"])); db.commit()
post(anna, "/profil/sicherheit", {"action": "passkey_delete", "pid": pk, "password": "passwort12"})
check(db.execute("SELECT 1 FROM passkeys WHERE id=?", (pk,)).fetchone(), "delete foreign passkey")
db.execute("DELETE FROM passkeys"); db.commit()

# 3) /admin eintippen
for url in ("/admin", "/admin/wartung", "/admin/anmeldungen", "/mannschaften/neue-saison", "/verein", "/benutzer/neu"):
    r = anna.get(url)
    check(r.status_code in (403, 404), f"shooter GET {url}: {r.status_code}")
    check(lisa.get(url).status_code in (403, 404), f"leader GET {url}")
check(post(anna, "/admin/wartung/auftrag", {"action": "update"}).status_code == 403, "shooter starts maintenance job")
check(app.test_client().get("/admin/wartung").status_code == 302, "anonymous -> login")

# 4) Schlüssel im Browser
html = "".join(c.get(u).get_data(as_text=True) for c, u in pages) + adm.get("/admin/wartung").get_data(as_text=True)
js = "".join(open(os.path.join(ROOT, "app", "static", "js", f)).read() for f in os.listdir(os.path.join(ROOT, "app", "static", "js")))
for secret in (app.secret_key if isinstance(app.secret_key, str) else app.secret_key.decode("latin1"), "SECRET_KEY="):
    check(secret not in html and secret not in js, "secret visible in browser")
check(not re.search(r"PRIVATE KEY-----\s*[A-Za-z0-9+/]{20}", html + js), "private key content in browser")   # Platzhalter ist ok
vapid_priv = open(os.path.join(tmp, "vapid.pem")).read() if os.path.exists(os.path.join(tmp, "vapid.pem")) else None
check(not vapid_priv or vapid_priv.split("-----")[2].strip()[:40] not in html, "push private key not exposed")
check(bert.get("/static/../data/t.db").status_code == 404 and bert.get("/data/t.db").status_code == 404, "db not downloadable")

# 6) Admin bei der Anmeldung / Rechte selbst erhöhen
check(app.test_client().get("/registrieren").status_code == 404, "no self sign-up")
post(anna, "/profil", {"action": "name", "name": "Anna", "role": "admin"})
post(anna, "/profil", {"action": "theme", "theme": "dunkel", "role": "admin"})
check(db.execute("SELECT role FROM users WHERE id=?", (U["anna"],)).fetchone()[0] == "shooter", "role via profile")
r = post(lisa, "/benutzer/neu", {"name": "Evil", "username": "evil", "password": "passwort12", "role": "admin"})
check(r.status_code == 403 and not db.execute("SELECT 1 FROM users WHERE username='evil'").fetchone(), "leader creates admin")
check(post(lisa, "/benutzer/sammel", {"action": "role", "role": "admin", "ids": [U["lisa"]]}).status_code in (403, 404)
      and db.execute("SELECT role FROM users WHERE id=?", (U["lisa"],)).fetchone()[0] == "leader", "leader promotes self")

# 7/10) Werte verändern: negative / riesige Zahlen, erfundene Status
post(lisa, f"/wettkampf/{mA}/aufstellung", {"lineup": [U["anna"]], f"rings_{U['anna']}": "-50"})
post(lisa, f"/wettkampf/{mA}/aufstellung", {"lineup": [U["bert"]], f"rings_{U['bert']}": "99999999"})
bad_rings = db.execute("SELECT rings FROM lineup WHERE match_id=? AND rings IS NOT NULL AND (rings < 0 OR rings > 1000)", (mA,)).fetchall()
check(not bad_rings, f"absurd rings stored: {[tuple(r) for r in bad_rings]}")
check(post(anna, f"/wettkampf/{mA}/verfuegbarkeit", {"status": "admin"}).status_code == 403, "invented status")
post(anna, "/abwesenheit", {"start": d(1), "end": d(5000)})
check(not db.execute("SELECT 1 FROM absences WHERE user_id=? AND end=?", (U["anna"], d(5000))).fetchone(), "absurd absence range")
r = post(adm, "/admin/wartung/auftrag", {"action": "bash", "param": "rm -rf /"})
check(not os.path.isdir(os.path.join(tmp, "jobs", "queue")) or not os.listdir(os.path.join(tmp, "jobs", "queue")), "unknown job action")

# 9) Einlösen x100: Wiederherstellungscode / Authenticator-Code gleichzeitig mehrfach
codes = S.recovery_new(2)
db.execute("INSERT INTO recovery_codes(user_id, code_hash) VALUES(?,?)", (U["anna"], S.recovery_hash(codes[0])))
secret = S.totp_new_secret(); db.execute("UPDATE users SET totp_secret=?, totp_last=NULL WHERE id=?", (secret, U["anna"])); db.commit()
def race(code, n=8):
    clients = []
    for _ in range(n):
        c = app.test_client(); c.get("/login")
        with c.session_transaction() as s: s["csrf"] = "t"
        c.post("/login", data={"username": "anna", "password": "passwort12", "csrf": "t"})
        with c.session_transaction() as s: s["csrf"] = "t"   # Anmeldung setzt die Sitzung neu
        clients.append(c)
    res, bar = [], threading.Barrier(n)
    def go(c):
        bar.wait(); r = c.post("/login/zweiter-schritt", data={"code": code, "csrf": "t"}); res.append(r.status_code)
    ts = [threading.Thread(target=go, args=(c,)) for c in clients]
    [t.start() for t in ts]; [t.join() for t in ts]
    return res.count(302)
check(race(codes[0]) == 1, "recovery code usable once under race")
db.execute("DELETE FROM login_log"); db.commit()
check(race(S.totp_now(secret)) == 1, "totp code usable once under race")
db.execute("UPDATE users SET totp_secret=NULL WHERE id=?", (U["anna"],)); db.execute("DELETE FROM login_log"); db.commit()

# 11) Code / Passwort erraten: Sperre nach wenigen Versuchen, auch von wechselnden IPs
c = app.test_client(); c.get("/login")
for i in range(A.MAX_FAILED_LOGINS):
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/login", data={"username": "bert", "password": f"falsch{i}", "csrf": "t"}, environ_base={"REMOTE_ADDR": f"10.9.0.{i}"})
with c.session_transaction() as s: s["csrf"] = "t"
r = c.post("/login", data={"username": "bert", "password": "passwort12", "csrf": "t"}, environ_base={"REMOTE_ADDR": "10.9.9.9"})
check(r.status_code == 429 or "Zu viele" in r.get_data(as_text=True), "username lock across IPs")
check(app.test_client().post("/login", data={"username": "admin", "password": "adminpass1"}).status_code == 400, "CSRF required on login")

# 12) Kein Foto hochladen
for name, data in (("x.jpg", b"<html><script>alert(1)</script></html>"), ("x.svg", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'),
                   ("x.php.jpg", b"\xff\xd8\xff<?php system($_GET[c]); ?>")):
    with anna.session_transaction() as s: s["csrf"] = "t"
    anna.post("/profil", data={"csrf": "t", "action": "avatar", "avatar": (io.BytesIO(data), name)}, content_type="multipart/form-data")
av = os.path.join(os.path.dirname(os.environ["DB_PATH"]), "avatars")
check(not os.path.isdir(av) or not os.listdir(av), "non-images rejected")
from PIL import Image
buf = io.BytesIO(); Image.new("RGB", (50, 50), "red").save(buf, "PNG"); buf = io.BytesIO(buf.getvalue() + b"<?php evil(); ?>")
with anna.session_transaction() as s: s["csrf"] = "t"
anna.post("/profil", data={"csrf": "t", "action": "avatar", "avatar": (buf, "a.png")}, content_type="multipart/form-data")
f = os.path.join(av, f"{U['anna']}.jpg")
check(os.path.exists(f) and b"<?php" not in open(f, "rb").read() and open(f, "rb").read()[:2] == b"\xff\xd8", "image re-encoded, payload gone")

# 13) .env im Git
tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split()
check(not [t for t in tracked if os.path.basename(t) in (".env", ".secret_key", "verein.db", "vapid.pem") or t.startswith("data/")], tracked)
hist = subprocess.run(["git", "-C", ROOT, "log", "--all", "--name-only", "--pretty=format:"], capture_output=True, text=True).stdout.split()
check(not [h for h in hist if os.path.basename(h) in (".env", ".secret_key", "verein.db", "vapid.pem")], "secret file in history")
gi = open(os.path.join(ROOT, ".gitignore")).read(); di = open(os.path.join(ROOT, ".dockerignore")).read()
check(".env" in gi and "data" in gi and ".env" in di, "ignore files")

# 15) Datenbank abfragen (SQL-Injection)
for payload in ("' OR '1'='1", "admin'--", "x' UNION SELECT password_hash FROM users--"):
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/login", data={"username": payload, "password": payload, "csrf": "t"}, environ_base={"REMOTE_ADDR": "10.8.0.1"})
    check(c.get("/").status_code == 302, f"sqli login {payload}")
for url in ("/benutzer?q=' OR 1=1--", "/benutzer?team=1 OR 1=1", "/benutzer?sort=name;DROP TABLE users", "/termine?m=2026-13' --"):
    r = adm.get(url)
    check(r.status_code in (200, 302, 400) and db.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 4, f"sqli {url}")
print("LISTE OK", ok)
