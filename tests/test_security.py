import os, sys, tempfile, io
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["ADMIN_2FA"] = "0"
os.environ.pop("ADMIN_PASSWORD", None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import app as A
out = buf.getvalue()
app = A.app; app.config["TESTING"] = True
ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
def csrf(c):
    with c.session_transaction() as s:
        s.setdefault("csrf", "tok"); return s["csrf"]
def post(c, url, data=None, **kw):
    d = dict(data or {}); d["csrf"] = csrf(c); return c.post(url, data=d, **kw)

# Zufälliges Admin-Passwort statt "admin"
check("Erzeugtes Startpasswort:" in out, "random admin pw printed")
pw = out.split("Erzeugtes Startpasswort: ")[1].split()[0]
check(len(pw) >= 12, "pw long")
c = app.test_client(); c.get("/login")
check(post(c, "/login", {"username": "admin", "password": "admin"}).status_code == 200, "admin/admin rejected")
r = post(c, "/login?next=/termine", {"username": "admin", "password": pw})
check(r.status_code == 302 and r.location.endswith("/termine"), "next ok")

# Open Redirect
for bad in ["//evil.com", "/\\evil.com", "https://evil.com", "/\t/evil.com", "javascript:alert(1)", "/%0d%0aX:1"]:
    x = app.test_client(); x.get("/login")
    r = post(x, "/login?next=" + bad.replace("%", "%25") if "%" in bad else "/login?next=" + bad, {"username": "admin", "password": pw})
    loc = r.location or ""
    check(r.status_code == 302 and "evil" not in loc and "javascript" not in loc, f"redirect blocked {bad} -> {loc}")
check(A.safe_next("/\\evil.com", "/") == "/" and A.safe_next("/termine?x=1", "/") == "/termine?x=1", "safe_next unit")

# Passwort ändern -> andere Sitzung ungültig
c2 = app.test_client(); c2.get("/login"); post(c2, "/login", {"username": "admin", "password": pw})
check(c2.get("/passwort").status_code in (200, 302), "second session")
post(c, "/passwort", {"old": pw, "new": "GanzNeu12345", "new2": "GanzNeu12345"})
check(c.get("/").status_code == 200, "own session stays")
r = c2.get("/")
check(r.status_code == 302 and "/login" in r.location, "other session logged out")

# Admin setzt Passwort zurück -> Sitzung des Benutzers ungültig
post(c, "/benutzer/neu", {"name": "Susi S", "username": "susi", "role": "shooter", "active": "1", "password": "susipass12"})
s = app.test_client(); s.get("/login"); post(s, "/login", {"username": "susi", "password": "susipass12"})
post(s, "/passwort", {"old": "susipass12", "new": "susipass34", "new2": "susipass34"})
check(s.get("/").status_code == 200, "susi in")
uid = A.sqlite3.connect(os.environ["DB_PATH"]).execute("select id from users where username='susi'").fetchone()[0]
post(c, f"/benutzer/{uid}", {"name": "Susi S", "username": "susi", "role": "shooter", "active": "1", "password": "reset12345"})
check(s.get("/").status_code == 302, "reset kicks session")

# Security-Header
r = c.get("/")
h = r.headers
check("frame-ancestors 'none'" in h.get("Content-Security-Policy", ""), "csp")
check(h.get("X-Frame-Options") == "DENY" and h.get("X-Content-Type-Options") == "nosniff", "xfo nosniff")
check(h.get("Cache-Control") == "no-store", "no-store")
check("Strict-Transport-Security" not in h, "no hsts without https")
check("no-store" not in c.get("/static/style.css").headers.get("Cache-Control", ""), "static cacheable")

# CSRF mit falschem Token
check(c.post("/benutzer/neu", data={"csrf": "falsch"}).status_code == 400, "wrong csrf")

# Sperre pro Benutzername (auch bei wechselnden IPs)
db = A.sqlite3.connect(os.environ["DB_PATH"])
now = A.datetime.now().isoformat(timespec="seconds")
for i in range(10):
    db.execute("insert into login_log (at, username, ip, agent, ok) values (?,?,?,?,0)", (now, "susi", f"10.0.0.{i}", ""))
db.commit()
z = app.test_client(); z.get("/login")
check(post(z, "/login", {"username": "SUSI", "password": "reset12345"}).status_code == 429, "per-user lockout")

# Bild-Upload: kein Bild / zu groß
s2 = app.test_client(); s2.get("/login")
db.execute("delete from login_log"); db.commit()
r = post(s2, "/login", {"username": "susi", "password": "reset12345"})
post(s2, "/passwort", {"old": "reset12345", "new": "susipass99", "new2": "susipass99"})
r = post(s2, "/profil", {"action": "avatar", "avatar": (io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")}, content_type="multipart/form-data", follow_redirects=True)
check("konnte nicht gelesen" in r.get_data(as_text=True), "svg rejected")
from PIL import Image
b = io.BytesIO(); Image.new("1", (8000, 8000)).save(b, "PNG"); b.seek(0)
r = post(s2, "/profil", {"action": "avatar", "avatar": (b, "big.png")}, content_type="multipart/form-data", follow_redirects=True)
check("konnte nicht gelesen" in r.get_data(as_text=True), "bomb rejected")
b = io.BytesIO(); Image.new("RGB", (50, 50), "red").save(b, "PNG"); b.seek(0)
r = post(s2, "/profil", {"action": "avatar", "avatar": (b, "ok.png")}, content_type="multipart/form-data", follow_redirects=True)
check("Profilbild gespeichert" in r.get_data(as_text=True), "png ok")

# XSS: Name mit HTML wird escaped
post(c, "/benutzer/neu", {"name": "<script>alert(1)</script>", "username": "xss", "role": "shooter", "active": "1", "password": "xsspass123"})
html = c.get("/benutzer").get_data(as_text=True)
check("<script>alert(1)</script>" not in html and "&lt;script&gt;" in html, "xss escaped")
# Schütze darf keine Admin-Seiten
for u in ["/benutzer", "/gruppen", "/verein", "/admin/backup", "/admin/protokoll", "/mannschaft/neu", "/training/zeiten", "/import/schuetzen"]:
    check(s2.get(u).status_code == 403, f"403 {u}")
print("SECURITY OK", ok)

# ---------- Sperren / Entsperren / Löschen
db = A.sqlite3.connect(os.environ["DB_PATH"]); db.execute("delete from login_log"); db.commit()
post(c, "/benutzer/neu", {"name": "Karl Kurz", "username": "karl", "role": "shooter", "password": "karlpass12"})
kid = db.execute("select id from users where username='karl'").fetchone()[0]
k = app.test_client(); k.get("/login"); post(k, "/login", {"username": "karl", "password": "karlpass12"})
post(k, "/passwort", {"old": "karlpass12", "new": "karlpass34", "new2": "karlpass34"})
check(k.get("/").status_code == 200, "karl in")
r = post(c, f"/benutzer/{kid}/sperren", {"reason": "Beitrag offen"}, follow_redirects=True)
check("auf allen Geräten abgemeldet" in r.get_data(as_text=True), "lock flash")
check(k.get("/").status_code == 302, "lock kicks session")
check(db.execute("select active, lock_reason from users where id=?", (kid,)).fetchone() == (0, "Beitrag offen"), "lock stored")
html = c.get("/benutzer?zeige=gesperrt").get_data(as_text=True)
check("Karl Kurz" in html and "Beitrag offen" in html, "locked list")
check("Karl Kurz" not in c.get("/benutzer").get_data(as_text=True), "active list hides locked")
k2 = app.test_client(); k2.get("/login")
check("gesperrt" in post(k2, "/login", {"username": "karl", "password": "karlpass34"}).get_data(as_text=True), "locked login msg")
check(post(s2, f"/benutzer/{kid}/entsperren").status_code == 403, "shooter cannot unlock")
post(c, f"/benutzer/{kid}/entsperren")
check(post(k2, "/login", {"username": "karl", "password": "karlpass34"}).status_code == 302, "unlocked login")
# Selbst sperren geht nicht
post(c, "/benutzer/1/sperren")
check(db.execute("select active from users where id=1").fetchone()[0] == 1, "no self lock")
# vorübergehende Sperre (Fehlversuche) aufheben
now = A.datetime.now().isoformat(timespec="seconds")
for i in range(10):
    db.execute("insert into login_log (at, username, ip, agent, ok) values (?,?,?,?,0)", (now, "karl", f"10.1.0.{i}", ""))
db.commit()
check("10 Fehlversuche" in c.get("/benutzer").get_data(as_text=True), "temp lock shown")
check("Vorübergehend gesperrt" in c.get(f"/benutzer/{kid}").get_data(as_text=True), "temp lock on edit")
post(c, f"/benutzer/{kid}/entsperren", {"zurueck": "liste"})
k3 = app.test_client(); k3.get("/login")
check(post(k3, "/login", {"username": "karl", "password": "karlpass34"}).status_code == 302, "temp lock lifted")
# Löschen nur mit Benutzername
check("Endgültig löschen" in c.get(f"/benutzer/{kid}").get_data(as_text=True), "delete section")
post(c, f"/benutzer/{kid}/loeschen", {"confirm_name": "falsch"})
check(db.execute("select count(*) from users where id=?", (kid,)).fetchone()[0] == 1, "delete needs name")
post(c, f"/benutzer/{kid}/loeschen", {"confirm_name": "KARL"})
check(db.execute("select count(*) from users where id=?", (kid,)).fetchone()[0] == 0, "deleted")
check(db.execute("select count(*) from user_groups where user_id=?", (kid,)).fetchone()[0] == 0, "cascade")
post(c, "/benutzer/1/loeschen", {"confirm_name": "admin"})
check(db.execute("select count(*) from users where id=1").fetchone()[0] == 1, "no self delete")
check(post(s2, f"/benutzer/1/loeschen", {"confirm_name": "admin"}).status_code == 403, "shooter cannot delete")
print("USER ADMIN OK", ok)
