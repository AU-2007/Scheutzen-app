import os, sys, tempfile, io, contextlib, sqlite3
from datetime import date, timedelta
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
app = A.app; app.config["TESTING"] = True
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1
def login(u, p):
    c = app.test_client(); c.get("/login")
    with c.session_transaction() as s: s["csrf"] = "t"
    c.post("/login", data={"username": u, "password": p, "csrf": "t"}); return c
def post(c, url, data, **kw):
    with c.session_transaction() as s: s["csrf"] = "t"
    d = dict(data); d["csrf"] = "t"; return c.post(url, data=d, **kw)
db = sqlite3.connect(tmp + "/t.db"); db.row_factory = sqlite3.Row
db.execute("UPDATE users SET must_change_pw=0, name='Urbschat Anton'")
uid = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES('Müller Jens','jens',?, 'shooter',1,0)", (A.generate_password_hash("passwort12"),)).lastrowid
t = db.execute("INSERT INTO teams(name, discipline) VALUES('LG 1','LG')").lastrowid
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (t, uid))
past = (date.today() - timedelta(days=3)).isoformat(); fut = (date.today() + timedelta(days=3)).isoformat()
mp = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?, ?, '19:30', 'SV Alt 1', 1)", (t, past)).lastrowid
mf = db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?, ?, '19:30', 'SV Neu 1', 1)", (t, fut)).lastrowid
db.commit()
a = login("admin", "adminpass1")
g = lambda mid, f: db.execute(f"SELECT {f} FROM matches WHERE id=?", (mid,)).fetchone()[0]
# Erste Ringe nach dem Wettkampf: ohne Rückfrage
r = post(a, f"/wettkampf/{mp}/aufstellung", {"lineup": [uid], f"rings_{uid}": "380", "opponent_score": "370"})
check(db.execute("SELECT rings FROM lineup WHERE match_id=?", (mp,)).fetchone()[0] == 380 and g(mp, "edited_at") is None, "first results: no confirm, not marked")
b = a.get(f"/wettkampf/{mp}").get_data(as_text=True)
check('data-past="Der Wettkampf ist schon vorbei und hat Ergebnisse' in b, "now locked: forms ask")
# Ändern ohne Bestätigung -> nichts passiert
r = post(a, f"/wettkampf/{mp}/aufstellung", {"lineup": [uid], f"rings_{uid}": "390", "opponent_score": "370"}, follow_redirects=True)
check("bitte bestätigen" in r.get_data(as_text=True) and db.execute("SELECT rings FROM lineup WHERE match_id=?", (mp,)).fetchone()[0] == 380, "unconfirmed change rejected")
check(g(mp, "edited_at") is None, "not marked")
# Mit Bestätigung -> geändert und gekennzeichnet
post(a, f"/wettkampf/{mp}/aufstellung", {"lineup": [uid], f"rings_{uid}": "390", "opponent_score": "370", "confirm_past": "1"})
check(db.execute("SELECT rings FROM lineup WHERE match_id=?", (mp,)).fetchone()[0] == 390 and g(mp, "edited_at"), "confirmed change saved + marked")
b = a.get(f"/wettkampf/{mp}").get_data(as_text=True)
check("Nachträglich bearbeitet" in b and "von Urbschat Anton" in b, "hint shown")
check("✎" in a.get(f"/mannschaft/{t}").get_data(as_text=True), "pencil in team list")
check("Nachträglich bearbeitet" in login("jens", "passwort12").get(f"/wettkampf/{mp}").get_data(as_text=True), "shooters see hint too")
# Gegnerschützen / Ersatz ebenso geschützt
post(a, f"/wettkampf/{mp}/gegnerschuetzen", {"action": "add", "name": "Xaver"})
check(db.execute("SELECT COUNT(*) FROM match_opp WHERE match_id=?", (mp,)).fetchone()[0] == 0, "opp add needs confirm")
post(a, f"/wettkampf/{mp}/gegnerschuetzen", {"action": "add", "name": "Xaver", "confirm_past": "1"})
check(db.execute("SELECT COUNT(*) FROM match_opp WHERE match_id=?", (mp,)).fetchone()[0] == 1, "opp add with confirm")
# Termin bearbeiten: sobald vorbei
r = post(a, f"/wettkampf/{mp}/bearbeiten", {"date": past, "time": "20:00", "opponent": "SV Alt 1", "home": "1"}, follow_redirects=True)
check(g(mp, "time") == "19:30" and "bitte bestätigen" in r.get_data(as_text=True), "termin needs confirm")
b = a.get(f"/wettkampf/{mp}/bearbeiten").get_data(as_text=True)
check("schon vorbei" in b and "data-past=" in b, "edit form warns + asks")
post(a, f"/wettkampf/{mp}/bearbeiten", {"date": past, "time": "20:00", "opponent": "SV Alt 1", "home": "1", "confirm_past": "1"})
check(g(mp, "time") == "20:00", "termin confirmed")
# Zukünftige Wettkämpfe: keine Rückfrage, keine Kennzeichnung
post(a, f"/wettkampf/{mf}/bearbeiten", {"date": fut, "time": "20:00", "opponent": "SV Neu 1", "home": "1"})
post(a, f"/wettkampf/{mf}/aufstellung", {"lineup": [uid], f"rings_{uid}": "380"})
check(g(mf, "time") == "20:00" and g(mf, "edited_at") is None and 'data-past="' not in a.get(f"/wettkampf/{mf}").get_data(as_text=True), "future untouched")
# Reihenfolge: vorbei -> Ergebnis vor Aufstellung vor RWK-Meldung; kommend -> Rückmeldung vor Aufstellung
b = a.get(f"/wettkampf/{mp}").get_data(as_text=True)
check(b.index('class="score') < b.index('id="aufstellung"') < b.index("Ergebnis melden") < b.index('id="verwalten"'), "past order")
jb = login("jens", "passwort12").get(f"/wettkampf/{mf}").get_data(as_text=True)
check(jb.index("Deine Rückmeldung") < jb.index('id="aufstellung"'), "upcoming order")
check("Deine Rückmeldung" not in login("jens", "passwort12").get(f"/wettkampf/{mp}").get_data(as_text=True), "no answer buttons for past")
print("VORBEI OK", ok)
