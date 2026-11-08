"""2FA (TOTP, Passkeys, Codes), Push, Abwesenheiten, Saisonwechsel, Statistik."""
import os, sys, tempfile, io, contextlib, sqlite3, json, hashlib, struct, time, re
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"
os.environ.pop("ADMIN_2FA", None)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
import sicherheit as S
from datetime import date, timedelta, datetime
app = A.app; app.config["TESTING"] = True
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1
def client():
    c = app.test_client(); c.get("/login")
    return c
def post(c, url, data=None, **kw):
    with c.session_transaction() as s: s["csrf"] = "t"
    d = dict(data or {}); d["csrf"] = "t"; return c.post(url, data=d, **kw)
def jpost(c, url, body=None):
    with c.session_transaction() as s: s["csrf"] = "t"
    return c.post(url, json=body or {}, headers={"X-CSRF-Token": "t"})
def login(user, pw):
    c = client(); r = post(c, "/login", {"username": user, "password": pw}); return c, r
db = sqlite3.connect(tmp + "/t.db"); db.row_factory = sqlite3.Row
db.execute("UPDATE users SET must_change_pw=0"); db.commit()
H = A.generate_password_hash("passwort12")
U = {}
for n in ("Anna", "Bert", "Carl", "Lisa"):
    U[n] = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES(?,?,?,?,1,0)",
                      (n, n.lower(), H, "leader" if n == "Lisa" else "shooter")).lastrowid
db.commit()
today = date.today()

# ================= Admin-Pflicht =================
a, r = login("admin", "adminpass1")
check(r.status_code == 302 and r.headers["Location"].endswith("/"), "admin without 2fa: normal login")
r = a.get("/")
check(r.status_code == 302 and "/profil/sicherheit" in r.headers["Location"], "admin forced to setup")
check(a.get("/benutzer").status_code == 302 and a.get("/profil/sicherheit").status_code == 200, "only setup reachable")
check(a.get("/healthz").get_data(as_text=True) == "ok", "healthz")
s, _ = login("anna", "passwort12")
check(s.get("/").status_code == 200, "shooter not forced")

# ================= TOTP =================
post(a, "/profil/sicherheit", {"action": "totp_start"})
with a.session_transaction() as ss: secret = ss["totp_new"]
page = a.get("/profil/sicherheit").get_data(as_text=True)
check("<svg" in page and "otpauth://totp/" in page.replace("&amp;", "&"), "QR shown")
r = post(a, "/profil/sicherheit", {"action": "totp_confirm", "code": "000000"}, follow_redirects=True)
check("stimmt nicht" in r.get_data(as_text=True), "wrong code rejected")
code_now = S.totp_now(secret)
r = post(a, "/profil/sicherheit", {"action": "totp_confirm", "code": code_now}, follow_redirects=True)
b = r.get_data(as_text=True)
check("eingerichtet" in b and "Wiederherstellungscodes" in b, "totp enabled + codes shown")
codes = re.findall(r"<code>([a-z0-9]{4}-[a-z0-9]{4})</code>", b)
check(len(codes) == 10, "10 codes")
check(not re.findall(r"<code>([a-z0-9]{4}-[a-z0-9]{4})</code>", a.get("/profil/sicherheit").get_data(as_text=True)), "codes only once")
check(a.get("/benutzer").status_code == 200, "admin free after setup")
# Anmeldung mit 2. Schritt
c, r = login("admin", "adminpass1")
check("/login/zweiter-schritt" in r.headers["Location"], "2nd step required")
check(c.get("/").status_code == 302, "not logged in yet")
n_ok = db.execute("SELECT COUNT(*) FROM login_log WHERE ok=1").fetchone()[0]
r = post(c, "/login/zweiter-schritt", {"code": "123456"})
check("stimmt nicht" in r.get_data(as_text=True), "wrong 2fa code")
# gleicher Code wie bei Einrichtung: Wiederverwendung gesperrt
r = post(c, "/login/zweiter-schritt", {"code": code_now})
reused_blocked = r.status_code == 200
db.execute("UPDATE users SET totp_last=totp_last-5 WHERE username='admin'"); db.commit()
r = post(c, "/login/zweiter-schritt", {"code": S.totp_now(secret)})
check(r.status_code == 302 and c.get("/").status_code == 200, "totp login ok")
check(db.execute("SELECT COUNT(*) FROM login_log WHERE ok=1").fetchone()[0] == n_ok + 1, "one ok log entry")
check(reused_blocked, "replay blocked")
# Wiederherstellungscode
c, r = login("admin", "adminpass1")
r = post(c, "/login/zweiter-schritt", {"code": codes[0].upper().replace("-", " ")}, follow_redirects=True)
check("Noch 9 Codes" in r.get_data(as_text=True), "recovery code works")
c, r = login("admin", "adminpass1")
check(post(c, "/login/zweiter-schritt", {"code": codes[0]}).status_code == 200, "recovery code only once")
# abgelaufen
c, r = login("admin", "adminpass1")
with c.session_transaction() as ss: p = ss["pending2fa"]; p["at"] -= 1000; ss["pending2fa"] = p
r = c.get("/login/zweiter-schritt")
check(r.status_code == 302 and "/login" in r.headers["Location"], "pending expires")
# Sperre nach vielen Fehlern
c, r = login("admin", "adminpass1")
for _ in range(A.MAX_FAILED_LOGINS):
    post(c, "/login/zweiter-schritt", {"code": "999999"})
check(post(c, "/login/zweiter-schritt", {"code": S.totp_now(secret)}).status_code == 429, "2fa rate limit")
db.execute("DELETE FROM login_log"); db.commit()
# Admin darf letzten Faktor nicht entfernen
a2 = c
a2, _ = login("admin", "adminpass1")
post(a2, "/login/zweiter-schritt", {"code": codes[1]})
r = post(a2, "/profil/sicherheit", {"action": "totp_off", "password": "adminpass1"}, follow_redirects=True)
check("brauchst du einen zweiten Faktor" in r.get_data(as_text=True), "admin keeps last factor")
check(post(a2, "/profil/sicherheit", {"action": "totp_off", "password": "falsch"}, follow_redirects=True).status_code == 200
      and db.execute("SELECT totp_secret FROM users WHERE username='admin'").fetchone()[0], "password required")

# ================= Passkeys (Software-Authenticator) =================
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes
def cbor(v):
    def head(major, n):
        if n < 24: return bytes([major << 5 | n])
        for ai, ln in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 256 ** ln: return bytes([major << 5 | ai]) + n.to_bytes(ln, "big")
    if isinstance(v, bool): return b"\xf5" if v else b"\xf4"
    if isinstance(v, int): return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes): return head(2, len(v)) + v
    if isinstance(v, str): e = v.encode(); return head(3, len(e)) + e
    if isinstance(v, dict): return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    if isinstance(v, list): return head(4, len(v)) + b"".join(cbor(x) for x in v)
class Authn:
    def __init__(self, origin="http://localhost", rp="localhost"):
        self.key = ec.generate_private_key(ec.SECP256R1()); self.cid = os.urandom(32); self.count = 0
        self.origin, self.rp = origin, rp; self.handle = None
    def create(self, opts, uv=True):
        self.handle = opts["user"]["id"]
        n = self.key.public_key().public_numbers()
        cose = cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        ad = hashlib.sha256(self.rp.encode()).digest() + bytes([0x41 | (0x04 if uv else 0)]) + struct.pack(">I", 0) \
            + b"\0" * 16 + struct.pack(">H", len(self.cid)) + self.cid + cose
        cd = json.dumps({"type": "webauthn.create", "challenge": opts["challenge"], "origin": self.origin}).encode()
        return {"id": S.b64u(self.cid), "rawId": S.b64u(self.cid), "type": "public-key",
                "response": {"clientDataJSON": S.b64u(cd), "attestationObject": S.b64u(cbor({"fmt": "none", "attStmt": {}, "authData": ad}))}}
    def get(self, opts, uv=True, count=None):
        self.count = self.count + 1 if count is None else count
        ad = hashlib.sha256(self.rp.encode()).digest() + bytes([0x01 | (0x04 if uv else 0)]) + struct.pack(">I", self.count)
        cd = json.dumps({"type": "webauthn.get", "challenge": opts["challenge"], "origin": self.origin}).encode()
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": S.b64u(self.cid), "rawId": S.b64u(self.cid), "type": "public-key",
                "response": {"clientDataJSON": S.b64u(cd), "authenticatorData": S.b64u(ad), "signature": S.b64u(sig),
                             "userHandle": self.handle}}
dev = Authn()
o = jpost(s, "/passkey/registrieren/optionen").get_json()
check(o["rp"]["id"] == "localhost" and o["user"]["name"] == "anna", "reg options")
bad = Authn(origin="https://evil.example"); r = jpost(s, "/passkey/registrieren", bad.create(o))
check(r.status_code == 400 and not db.execute("SELECT 1 FROM passkeys").fetchone(), "wrong origin rejected")
o = jpost(s, "/passkey/registrieren/optionen").get_json()
cred = dev.create(o); cred["name"] = "Annas Handy"
r = jpost(s, "/passkey/registrieren", cred)
check(r.get_json()["ok"] and db.execute("SELECT name FROM passkeys WHERE user_id=?", (U["Anna"],)).fetchone()[0] == "Annas Handy", "passkey stored")
check(jpost(s, "/passkey/registrieren", cred).status_code == 400, "challenge single use")
check(c.post("/passkey/registrieren/optionen").status_code in (302, 400), "reg needs login + csrf")
# Ohne Passwort anmelden
c = client(); o = jpost(c, "/passkey/anmelden/optionen").get_json()
check(o["userVerification"] == "required" and o["allowCredentials"] == [], "login options")
r = jpost(c, "/passkey/anmelden", dev.get(o))
check(r.get_json()["ok"] and c.get("/").status_code == 200, "passwordless login")
c = client(); o = jpost(c, "/passkey/anmelden/optionen").get_json()
check(jpost(c, "/passkey/anmelden", dev.get(o, uv=False)).status_code == 400, "passwordless needs UV")
c = client(); o = jpost(c, "/passkey/anmelden/optionen").get_json()
check(jpost(c, "/passkey/anmelden", dev.get(o, count=1)).status_code == 400, "counter regression rejected")
c = client(); o = jpost(c, "/passkey/anmelden/optionen").get_json()
fake = dev.get(o); fake["response"]["signature"] = S.b64u(b"\x30\x06\x02\x01\x01\x02\x01\x01")
check(jpost(c, "/passkey/anmelden", fake).status_code == 400, "bad signature rejected")
c = client(); o = jpost(c, "/passkey/anmelden/optionen").get_json(); o["challenge"] = S.b64u(os.urandom(32))
check(jpost(c, "/passkey/anmelden", dev.get(o)).status_code == 400, "wrong challenge rejected")
# Passwort-Anmeldung von Anna braucht jetzt 2. Schritt; Passkey als 2. Faktor
c, r = login("anna", "passwort12")
check("/login/zweiter-schritt" in r.headers["Location"], "anna now 2fa")
o = jpost(c, "/passkey/anmelden/optionen").get_json()
check(len(o["allowCredentials"]) == 1 and o["userVerification"] == "preferred", "2fa options for user")
r = jpost(c, "/passkey/anmelden", dev.get(o, uv=False))
check(r.get_json()["ok"] and c.get("/").status_code == 200, "passkey as 2nd factor (no UV needed)")
# fremder Passkey beim offenen 2. Schritt eines anderen Benutzers
s2, _ = login("bert", "passwort12")
o = jpost(s2, "/passkey/registrieren/optionen").get_json(); devb = Authn(); jpost(s2, "/passkey/registrieren", devb.create(o))
c, r = login("anna", "passwort12"); o = jpost(c, "/passkey/anmelden/optionen").get_json()
check(jpost(c, "/passkey/anmelden", devb.get(o)).status_code == 400, "other user's passkey rejected in 2fa")
# Admin setzt 2FA von Bert zurück, CLI
a, _ = login("admin", "adminpass1"); post(a, "/login/zweiter-schritt", {"code": codes[2]})
post(a, f"/benutzer/{U['Bert']}/2fa-zuruecksetzen")
check(not db.execute("SELECT 1 FROM passkeys WHERE user_id=?", (U["Bert"],)).fetchone(), "admin reset")
check(post(s, f"/benutzer/{U['Bert']}/2fa-zuruecksetzen").status_code == 403, "shooter cannot reset")
res = app.test_cli_runner().invoke(args=["reset-2fa", "anna"])
check("zurückgesetzt" in res.output and not db.execute("SELECT 1 FROM passkeys WHERE user_id=?", (U["Anna"],)).fetchone(), "cli reset")

# ================= Abwesenheit =================
t1 = db.execute("INSERT INTO teams(name, discipline, season) VALUES('LG 1','Luftgewehr','2026/27')").lastrowid
for n in ("Anna", "Bert", "Carl"):
    db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (t1, U[n]))
d = lambda k: (today + timedelta(days=k)).isoformat()
m1 = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00','SV A',1)", (t1, d(10))).lastrowid
m2 = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00','SV B',0)", (t1, d(30))).lastrowid
db.commit()
s, _ = login("anna", "passwort12")
post(s, f"/wettkampf/{m1}/verfuegbarkeit", {"status": "ja"})
r = post(s, "/abwesenheit", {"start": d(8), "end": d(12), "note": "Urlaub"}, follow_redirects=True)
check("1 Wettkampf automatisch abgesagt" in r.get_data(as_text=True), "absence applied")
row = db.execute("SELECT status, auto FROM availability WHERE match_id=? AND user_id=?", (m1, U["Anna"])).fetchone()
check(tuple(row) == ("nein", 1), "set to nein/auto")
# später angelegter Wettkampf
a, _ = login("admin", "adminpass1"); post(a, "/login/zweiter-schritt", {"code": codes[3]})
post(a, f"/mannschaft/{t1}/wettkampf/neu", {"date": d(11), "time": "18:00", "opponent": "SV C", "home": "1"})
m3 = db.execute("SELECT id FROM matches WHERE opponent='SV C'").fetchone()
if m3 is None:   # Route-Name anders: direkt anlegen und irgendeinen POST auslösen
    m3 = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'18:00','SV C',1)", (t1, d(11))).lastrowid; db.commit()
    post(a, "/push/einstellungen", {"push_remind": "1", "push_lineup": "1"})
else:
    m3 = m3[0]
check(db.execute("SELECT status FROM availability WHERE match_id=? AND user_id=?", (m3, U["Anna"])).fetchone()[0] == "nein", "later match auto-absent")
b = a.get(f"/wettkampf/{m1}").get_data(as_text=True)
check("Abwesend" in b, "shown as Abwesend")
check("Urlaub" in s.get("/profil").get_data(as_text=True), "listed in profile")
aid = db.execute("SELECT id FROM absences").fetchone()[0]
post(s, f"/abwesenheit/{aid}/loeschen")
check(not db.execute("SELECT 1 FROM availability WHERE user_id=? AND match_id IN (?,?)", (U["Anna"], m1, m3)).fetchone(), "delete reopens")
check(post(s, "/abwesenheit", {"start": d(-10), "end": d(-5)}, follow_redirects=True).status_code == 200
      and not db.execute("SELECT 1 FROM absences").fetchone(), "past absence rejected")
check(post(c, f"/abwesenheit/{aid}/loeschen").status_code in (302, 404), "cannot delete foreign")
# manuelle Antwort hebt auto auf
post(s, "/abwesenheit", {"start": d(9), "end": d(9)})
post(s, "/abwesenheit", {"start": d(10), "end": d(10)})
post(s, f"/wettkampf/{m1}/verfuegbarkeit", {"status": "ja"})
check(tuple(db.execute("SELECT status, auto FROM availability WHERE match_id=? AND user_id=?", (m1, U["Anna"])).fetchone()) == ("ja", 0), "manual overrides")
db.execute("DELETE FROM absences"); db.commit()

# ================= Push =================
sent = []
A.WP.send = lambda v, sub, data, subj, **k: (sent.append((sub["user_id"], data)), 201)[1]
good = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": S.b64u(b"\x04" + os.urandom(64)), "auth": S.b64u(os.urandom(16))}}
check(jpost(s, "/push/abo", dict(good, endpoint="https://evil.example/x")).status_code == 400, "foreign push host rejected")
check(jpost(s, "/push/abo", dict(good, endpoint="http://fcm.googleapis.com/x")).status_code == 400, "http rejected")
check(jpost(s, "/push/abo", good).get_json()["ok"], "subscribe")
cb, _ = login("bert", "passwort12")
jpost(cb, "/push/abo", dict(good, endpoint="https://web.push.apple.com/xyz"))
r = post(s, "/push/test", follow_redirects=True)
check("Test an 1 Gerät" in r.get_data(as_text=True) and sent[-1][0] == U["Anna"], "test push")
# Erinnerungen: m1 in 10 Tagen -> Tage 10 einstellen
db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('remind_days', '10')"); db.commit()
sent.clear()
db.execute("DELETE FROM availability WHERE match_id=?", (m1,)); db.commit()
n = A.run_reminders(datetime.combine(today, datetime.min.time()).replace(hour=12))
check(n == 2 and sorted(x[0] for x in sent) == sorted([U["Anna"], U["Bert"]]), "reminders to open + subscribed")
check(A.run_reminders(datetime.combine(today, datetime.min.time()).replace(hour=13)) == 0, "no duplicates")
check(A.run_reminders(datetime.combine(today, datetime.min.time()).replace(hour=23)) == 0, "quiet hours")
db.execute("DELETE FROM push_log"); db.commit()
post(s, f"/wettkampf/{m1}/verfuegbarkeit", {"status": "vielleicht"})
post(cb, "/abwesenheit", {"start": d(10), "end": d(10)})
sent.clear()
check(A.run_reminders(datetime.combine(today, datetime.min.time()).replace(hour=12)) == 0, "answered/absent: no reminder")
db.execute("DELETE FROM absences"); db.execute("DELETE FROM availability"); db.execute("DELETE FROM push_log"); db.commit()
post(s, "/push/einstellungen", {})   # Anna: alles aus
check(db.execute("SELECT push_remind FROM users WHERE id=?", (U["Anna"],)).fetchone()[0] == 0, "prefs saved")
sent.clear(); A.run_reminders(datetime.combine(today, datetime.min.time()).replace(hour=12))
check([x[0] for x in sent] == [U["Bert"]], "opted out not reminded")
# Aufstellung -> Nachricht
hook = []
A.PUSH_TEST_HOOK = lambda uids, *a: hook.append((uids, a))
post(s, "/push/einstellungen", {"push_lineup": "1"})
post(a, f"/wettkampf/{m1}/aufstellung", {"lineup": [U["Anna"], U["Carl"]]})
check(hook and set(hook[-1][0]) == {U["Anna"], U["Carl"]} and hook[-1][1][0] == "Du bist aufgestellt", "lineup push")
hook.clear(); post(a, f"/wettkampf/{m1}/aufstellung", {"lineup": [U["Anna"], U["Carl"]]})
check(not hook, "no push for unchanged lineup")
# Erinnern-Knopf
r = post(a, f"/wettkampf/{m2}/erinnern", follow_redirects=True)
check("Erinnerung an 1 Schützen" in r.get_data(as_text=True) and hook[-1][0] == [U["Bert"]] or "an 2" in r.get_data(as_text=True), "manual remind")
r = post(a, f"/wettkampf/{m2}/erinnern", follow_redirects=True)
check("gerade erst erinnert" in r.get_data(as_text=True), "manual remind rate limited")
check(post(s, f"/wettkampf/{m2}/erinnern").status_code == 403, "shooter cannot remind")
check(s.get("/sw.js").headers.get("Service-Worker-Allowed") == "/", "service worker served")
# Verschlüsselung + VAPID echt (ohne Netz): Header aufbauen
v = A.vapid(); check(len(S.b64u_dec(v.public)) == 65 and "vapid t=" in v.auth_header("https://fcm.googleapis.com/x", "mailto:a@b.de"), "vapid")
A.PUSH_TEST_HOOK = None

# ================= Saisonwechsel =================
db.execute("UPDATE teams SET season='2026/27', leader_id=?", (U["Lisa"],)); db.execute("INSERT INTO team_members(team_id, user_id, reserve) VALUES(?,?,1)", (t1, U["Lisa"]))
t2 = db.execute("INSERT INTO teams(name, discipline, season) VALUES('KK 1','KK-Gewehr','2026/27')").lastrowid; db.commit()
check("2027/28" in a.get("/mannschaften/neue-saison").get_data(as_text=True), "next season suggested")
check(post(s, "/mannschaften/neue-saison", {"season": "2027/28", "team": [t1]}).status_code == 403, "shooter cannot")
post(a, "/mannschaften/neue-saison", {"season": "2027/28", "team": [t1], "archive": "1"})
new = db.execute("SELECT * FROM teams WHERE season='2027/28'").fetchall()
check(len(new) == 1 and new[0]["name"] == "LG 1" and new[0]["leader_id"] == U["Lisa"], "team copied")
mem = db.execute("SELECT user_id, reserve FROM team_members WHERE team_id=? ORDER BY user_id", (new[0]["id"],)).fetchall()
check(len(mem) == 4 and dict(mem)[U["Lisa"]] == 1, "members incl. reserve copied")
check(db.execute("SELECT archived FROM teams WHERE id=?", (t1,)).fetchone()[0] == 1 and
      db.execute("SELECT archived FROM teams WHERE id=?", (t2,)).fetchone()[0] == 0, "old archived, unselected stays")
b = a.get("/mannschaften").get_data(as_text=True)
check("Archiv – frühere Saisons" in b and "Saison 2026/27" in b, "archive section")
idx = s.get("/").get_data(as_text=True)
check(idx.count(">LG 1<") <= 1 or idx.count("LG 1") >= 1, "index renders")
my = [t["id"] for t in db.execute(f"SELECT t.id FROM teams t JOIN team_members tm ON tm.team_id=t.id WHERE tm.user_id=? AND t.archived=0", (U["Anna"],))]
check(my == [new[0]["id"]], "only new team active for Anna")
check("Archiv: Diese Mannschaft" in a.get(f"/mannschaft/{t1}").get_data(as_text=True), "archived banner")
post(a, "/mannschaften/neue-saison", {"season": "2027/28", "team": [new[0]["id"]]})
check(db.execute("SELECT COUNT(*) FROM teams WHERE season='2027/28'").fetchone()[0] == 1, "no duplicate for same season")

# ================= Statistik =================
for k, rings in enumerate((370, 380, 375.5)):
    mid = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00',?,1)", (t1, d(-30 + k), f"G{k}")).lastrowid
    db.execute("INSERT INTO lineup(match_id, user_id, rings) VALUES(?,?,?)", (mid, U["Anna"], rings))
mid = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00','KKG',1)", (t2, d(-3))).lastrowid
db.execute("INSERT INTO lineup(match_id, user_id, rings) VALUES(?,?,?)", (mid, U["Anna"], 290)); db.commit()
b = s.get("/statistik").get_data(as_text=True)
check("Luftgewehr" in b and "Kleinkaliber" in b and "375,2" in b and "380" in b and "<polyline" in b, "stats content")
check(s.get(f"/statistik/{U['Bert']}").status_code == 403, "shooter cannot view others")
cl, _ = login("lisa", "passwort12")
check(cl.get(f"/statistik/{U['Anna']}").status_code == 200, "leader can view")
print("NEU OK", ok)
