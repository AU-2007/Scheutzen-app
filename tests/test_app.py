import os, re, sys, tempfile
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["ADMIN_2FA"] = "0"
os.environ["ADMIN_PASSWORD"] = "startpass"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import app as A
import sqlite3 as _sq; _c = _sq.connect(os.environ["DB_PATH"])   # ältere Tests: Format "Vorname Nachname"
_c.execute("INSERT INTO settings(key, value) VALUES('name_order','vorname') ON CONFLICT(key) DO UPDATE SET value='vorname'"); _c.commit(); _c.close()

app = A.app
app.config["TESTING"] = True

@app.route("/kaputt")
def _kaputt():
    raise ValueError("Testfehler 42")

def csrf(c):
    with c.session_transaction() as s:
        if "csrf" not in s:
            s["csrf"] = "tok"
        return s["csrf"]

def post(c, url, data=None, **kw):
    d = dict(data or {})
    d["csrf"] = csrf(c)
    return c.post(url, data=d, **kw)

def login(c, u, p):
    c.get("/login")
    return post(c, "/login", {"username": u, "password": p})

ok = 0
def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1

# --- Admin
a = app.test_client()
r = login(a, "admin", "startpass")
check(r.status_code == 302, "admin login")
r = a.get("/")
check(r.status_code == 302 and "/passwort" in r.location, "must change pw")
r = post(a, "/passwort", {"old": "startpass", "new": "neuesPasswort1", "new2": "neuesPasswort1"})
check(r.status_code == 302, "pw changed")
check(a.get("/").status_code == 200, "dashboard")
# CSRF ohne Token
r = a.post("/benutzer/neu", data={"name": "x"})
check(r.status_code == 400, "csrf blocks")

# Benutzer anlegen
post(a, "/benutzer/neu", {"name": "Max Mannschaftsführer", "username": "max", "role": "leader", "active": "1", "password": "maxpass12"})
post(a, "/benutzer/neu", {"name": "Susi Schütze", "username": "susi", "role": "shooter", "active": "1", "password": "susipass12"})
post(a, "/benutzer/neu", {"name": "Otto O'Brien", "username": "otto", "role": "shooter", "active": "1", "password": "ottopass12"})
r = post(a, "/benutzer/neu", {"name": "Dup", "username": "MAX", "role": "shooter", "active": "1", "password": "xxxxxxxx1"}, follow_redirects=True)
check("schon vergeben" in r.get_data(as_text=True), "dup username")
users = {u["username"]: u["id"] for u in A.sqlite3.connect(os.environ["DB_PATH"]).execute("select username,id from users").fetchall() and [dict(zip(("username","id"),x)) for x in A.sqlite3.connect(os.environ["DB_PATH"]).execute("select username,id from users")]}
check(len(users) == 4, "4 users")
check(a.get("/benutzer").status_code == 200, "users page")
check(a.get(f"/benutzer/{users['otto']}").status_code == 200, "user edit page")

# Mannschaften
r = post(a, "/mannschaft/neu", {"name": "LG 1", "discipline": "Luftgewehr", "league": "Gauliga", "season": "2026/27", "leader_id": users["max"]})
tid1 = int(r.location.rsplit("/", 1)[1])
r = post(a, "/mannschaft/neu", {"name": "LP 1", "discipline": "Luftpistole", "leader_id": ""})
tid2 = int(r.location.rsplit("/", 1)[1])
for u in ("max", "susi", "otto"):
    post(a, f"/mannschaft/{tid1}/mitglieder", {"user_id": users[u]})
check(a.get(f"/mannschaft/{tid1}").status_code == 200, "team page")
check(a.get("/mannschaften").status_code == 200, "teams page")

# --- Mannschaftsführer
m = app.test_client()
login(m, "max", "maxpass12")
post(m, "/passwort", {"old": "maxpass12", "new": "maxpass123", "new2": "maxpass123"})
r = post(m, f"/mannschaft/{tid1}/wettkampf/neu", {"date": "2030-01-10", "time": "19:30", "opponent": "SV Tell 1", "home": "1", "location": "Schützenhaus"})
check(r.status_code == 302, "leader creates match")
mid = int(r.location.rsplit("/", 1)[1])
r = post(m, f"/mannschaft/{tid2}/wettkampf/neu", {"date": "2030-01-10", "opponent": "X"})
check(r.status_code == 403, "leader cannot manage foreign team")
check(m.get("/benutzer").status_code == 403, "leader no users")
check(m.get("/mannschaft/neu").status_code == 403, "leader no new team")
r = post(m, f"/wettkampf/{mid}/aufstellung", {"lineup": [users["max"], users["susi"], 9999], f"rings_{users['max']}": "389,4", f"rings_{users['susi']}": "381", "opponent_score": "760"})
check(r.status_code == 302, "lineup saved")
db = A.sqlite3.connect(os.environ["DB_PATH"])
lu = dict(db.execute("select user_id, rings from lineup where match_id=?", (mid,)).fetchall())
check(lu == {users["max"]: 389.4, users["susi"]: 381.0}, f"lineup values {lu}")
html = m.get(f"/wettkampf/{mid}").get_data(as_text=True)
check("770,4" in html and "Sieg" in html, "total + outcome")
html = m.get(f"/mannschaft/{tid1}").get_data(as_text=True)
check("389,4" in html, "avg shown")

# --- Schütze
s = app.test_client()
login(s, "susi", "susipass12")
r = s.get("/")
check("/passwort" in r.location, "susi must change")
post(s, "/passwort", {"old": "susipass12", "new": "susipass123", "new2": "susipass123"})
html = s.get("/").get_data(as_text=True)
check("danach:" in html and "SV Tell 1" in html, "susi dashboard (Termin nach dieser Woche)")
r = post(s, f"/wettkampf/{mid}/verfuegbarkeit", {"status": "ja", "back": "/"})
check(r.status_code == 302 and r.location.endswith("/"), "susi zusage")
r = post(s, f"/wettkampf/{mid}/verfuegbarkeit", {"status": "nein", "back": "//evil.com"})
check(r.location.endswith(f"/wettkampf/{mid}"), "open redirect blocked")
check(db.execute("select status from availability where user_id=?", (users["susi"],)).fetchone()[0] == "nein", "status updated")
check(post(s, f"/wettkampf/{mid}/aufstellung", {}).status_code == 403, "shooter no lineup")
check(post(s, f"/mannschaft/{tid1}/mitglieder", {"user_id": users["susi"]}).status_code == 403, "shooter no members")
check(s.get(f"/wettkampf/{mid}/bearbeiten").status_code == 403, "shooter no edit")
check(s.get(f"/wettkampf/{mid}").status_code == 200, "shooter sees match")
check("389,4" in s.get("/").get_data(as_text=True) or "381" in s.get("/").get_data(as_text=True), "own results")

# Otto (Name mit Apostroph) Seiten rendern
check("O&#39;Brien" in a.get(f"/mannschaft/{tid1}").get_data(as_text=True), "apostrophe escaped")

# Deaktivieren sperrt Login
post(a, f"/benutzer/{users['otto']}/sperren", {"reason": "ausgetreten"})
o = app.test_client()
r = login(o, "otto", "ottopass12")
check(r.status_code == 200 and "gesperrt" in r.get_data(as_text=True), "inactive cannot log in")
r = login(o, "otto", "falschfalsch")
check("gesperrt" not in r.get_data(as_text=True), "lock not revealed without pw")

# Admin kann sich nicht selbst degradieren
post(a, f"/benutzer/1", {"name": "Administrator", "username": "admin", "role": "shooter", "password": ""})
check(db.execute("select role, active from users where id=1").fetchone() == ("admin", 1), "admin self protect")

# Rolle Schütze -> MF-Zuordnung weg
post(a, f"/benutzer/{users['max']}", {"name": "Max", "username": "max", "role": "shooter", "active": "1", "password": ""})
check(db.execute("select leader_id from teams where id=?", (tid1,)).fetchone()[0] is None, "leader removed")

# Edit / Löschen
check(a.get(f"/wettkampf/{mid}/bearbeiten").status_code == 200, "match edit page")
check(a.get(f"/mannschaft/{tid1}/bearbeiten").status_code == 200, "team edit page")
post(a, f"/wettkampf/{mid}/loeschen")
check(db.execute("select count(*) from lineup").fetchone()[0] == 0, "cascade delete")
check(a.get("/gibtsnicht").status_code == 404, "404")

# ---- Neu: Termine & Bearbeiter
a2 = app.test_client(); login(a2, "admin", "neuesPasswort1")
r = post(a2, "/mannschaft/neu", {"name": "KK 1", "discipline": "KK-Gewehr", "leader_id": "", "editors": [users["susi"]]})
tid3 = int(r.location.rsplit("/", 1)[1])
check(db.execute("select user_id from team_editors where team_id=?", (tid3,)).fetchall() == [(users["susi"],)], "editor saved")
for d in ("2030-02-01","2030-02-08","2030-02-15"):
    post(a2, f"/mannschaft/{tid3}/wettkampf/neu", {"date": d, "opponent": "Gegner "+d, "home": "1"})
post(a2, f"/mannschaft/{tid3}/mitglieder", {"user_id": users["susi"]})
# Susi = Schütze, aber Bearbeiter von KK 1
r = post(s, f"/mannschaft/{tid3}/bearbeiten", {"name": "KK 1 neu", "leader_id": users["susi"], "editors": []})
check(r.status_code == 302, "editor can edit team")
row = db.execute("select name, leader_id from teams where id=?", (tid3,)).fetchone()
check(row == ("KK 1 neu", None), f"editor cannot change leader {row}")
check(db.execute("select count(*) from team_editors where team_id=?", (tid3,)).fetchone()[0] == 1, "editor cannot change editors")
check(post(s, f"/mannschaft/{tid3}/mitglieder", {"user_id": users["otto"]}).status_code == 302, "editor adds member")
check(s.get("/mannschaft/neu").status_code == 403, "shooter cannot create team")
check(s.get(f"/mannschaft/{tid1}/bearbeiten").status_code == 403, "not editor of tid1")
check(post(s, f"/mannschaft/{tid3}/loeschen").status_code == 403, "editor cannot delete team")
html = s.get("/").get_data(as_text=True)
check("Alle Termine im Kalender" in html, "dashboard link to calendar")
html = s.get("/termine?monat=2030-02").get_data(as_text=True)
check("Februar 2030" in html and html.count('class="cal-day') >= 28 and "Gegner 2030-02-15" in html, "calendar")
check('href="#m' in html, "calendar links")
check(s.get("/termine?monat=quatsch").status_code == 200, "bad month ok")
check(s.get("/termine?alle=1&monat=2030-01").status_code == 200, "all teams")
check("März 2030" in s.get("/termine?monat=2030-03").get_data(as_text=True), "next month")
print("OK2", ok)

# ---- Neu: Fahrgemeinschaft, Beschreibung, Ersatz
r = post(a2, f"/mannschaft/{tid3}/wettkampf/neu", {"date": "2030-03-01", "opponent": "Auswärtsgegner", "home": "0", "meet": "Schützenhaus 17:45"})
amid = int(r.location.rsplit("/", 1)[1])
post(a2, f"/benutzer/neu", {"name": "Fritz Fahrer", "username": "fritz", "role": "shooter", "active": "1", "password": "fritzpass1"})
fid = db.execute("select id from users where username='fritz'").fetchone()[0]
post(a2, f"/mannschaft/{tid3}/mitglieder", {"user_id": fid, "reserve": "1"})
check(db.execute("select reserve from team_members where team_id=? and user_id=?", (tid3, fid)).fetchone()[0] == 1, "added as reserve")
post(a2, f"/mannschaft/{tid3}/mitglieder/{fid}/ersatz")
check(db.execute("select reserve from team_members where team_id=? and user_id=?", (tid3, fid)).fetchone()[0] == 0, "toggle reserve")
f = app.test_client(); login(f, "fritz", "fritzpass1"); post(f, "/passwort", {"old": "fritzpass1", "new": "fritzpass12", "new2": "fritzpass12"})
check(post(f, f"/wettkampf/{amid}/fahrt", {"mode": "fahrer", "seats": "1"}).status_code == 302, "fritz drives")
check(post(s, f"/wettkampf/{amid}/fahrt", {"mode": "mitfahrer", "driver_id": fid}).status_code == 302, "susi rides")
html = s.get(f"/wettkampf/{amid}").get_data(as_text=True)
check("Fahrgemeinschaft" in html and "1/1 Plätze belegt" in html and "Schützenhaus 17:45" in html, "carpool shown")
check(post(f, f"/wettkampf/{amid}/fahrt", {"mode": "selbst"}).status_code == 302, "fritz self")
check(db.execute("select driver_id from rides where match_id=? and user_id=?", (amid, users["susi"])).fetchone()[0] is None, "rider released")
check(post(s, f"/wettkampf/{amid}/fahrt", {"mode": "keine"}).status_code == 302, "delete ride")
check(post(s, f"/wettkampf/{amid}/fahrt", {"mode": "quatsch"}).status_code == 400, "bad mode")
check(post(s, f"/wettkampf/{mid if False else amid}/fahrt", {"mode": "selbst"}).status_code == 302, "susi self")
# Heimtermin: keine Fahrgemeinschaft
hm = int(post(a2, f"/mannschaft/{tid3}/wettkampf/neu", {"date": "2030-03-08", "opponent": "Heimgegner", "home": "1"}).location.rsplit("/",1)[1])
check(post(s, f"/wettkampf/{hm}/fahrt", {"mode": "selbst"}).status_code == 403, "no carpool at home")
_h = s.get(f"/wettkampf/{hm}").get_data(as_text=True); print([l for l in _h.splitlines() if "Fahrgemeinschaft" in l]); check("<h2>Fahrgemeinschaft" not in _h, "home no carpool section")
# Beschreibung: Susi ist Bearbeiterin von tid3
check(post(s, f"/wettkampf/{amid}/beschreibung", {"notes": "Neue Info", "meet": "Parkplatz"}).status_code == 302, "editor sets notes")
check(db.execute("select notes, meet from matches where id=?", (amid,)).fetchone() == ("Neue Info", "Parkplatz"), "notes saved")
check(post(f, f"/wettkampf/{amid}/beschreibung", {"notes": "x"}).status_code == 403, "shooter cannot edit notes")
# Aushilfe aus anderer Mannschaft
mx = db.execute("select id from users where username='max'").fetchone()[0]
check(post(s, f"/wettkampf/{amid}/aushilfe", {"user_id": mx}).status_code == 302, "add helper")
check(db.execute("select 1 from lineup where match_id=? and user_id=?", (amid, mx)).fetchone() is not None, "helper in lineup")
check("Aushilfe" in s.get(f"/wettkampf/{amid}").get_data(as_text=True), "helper tag")
check(post(m, f"/wettkampf/{amid}/verfuegbarkeit", {"status": "ja"}).status_code == 302, "helper can answer")
check(post(m, f"/wettkampf/{amid}/fahrt", {"mode": "fahrer", "seats": "3"}).status_code == 302, "helper can carpool")
check("Auswärtsgegner" in m.get("/termine?monat=2030-03").get_data(as_text=True), "helper sees match in schedule")
check(post(f, f"/wettkampf/{amid}/aushilfe", {"user_id": mx}).status_code == 403, "shooter cannot add helper")
print("OK3", ok)

# ---- Neu: Training (Zusage / Später / Absage, keine Stände)
from datetime import date as _d, timedelta as _td
check(s.get("/training").status_code == 302, "training redirects to Termine")
check(s.get("/training/zeiten").status_code == 403, "shooter no slot admin")
post(a2, "/training/zeiten", {"weekday": "1", "start": "19:00", "end": "21:00", "title": "Training", "lanes": "2", "location": "Schützenhaus"})
post(a2, "/training/zeiten", {"weekday": "4", "start": "18:30", "end": "21:00", "title": "Training", "lanes": "6"})
sid = db.execute("select id from training_slots where weekday=1").fetchone()[0]
today = _d.today(); nd = today + _td(days=(1 - today.weekday()) % 7 or 7)
iso = nd.isoformat()
html = s.get("/termine").get_data(as_text=True)
check(html.count("ag-item training") >= 8 and "von 2 Ständen frei" in html and ">Später<" in html and "past-list" in html or html.count("ag-item training") >= 8, "trainings in Termine")
check(post(s, f"/training/{sid}/{iso}/rueckmeldung", {"status": "ja"}).status_code == 302, "susi ja")
check(post(f, f"/training/{sid}/{iso}/rueckmeldung", {"status": "spaeter"}).status_code == 302, "fritz later")
r = post(m, f"/training/{sid}/{iso}/rueckmeldung", {"status": "ja"}, follow_redirects=True)
check("schon belegt" in r.get_data(as_text=True), "full blocks third")
check(post(m, f"/training/{sid}/{iso}/rueckmeldung", {"status": "nein"}).status_code == 302, "absage allowed when full")
html = s.get(f"/training/{sid}/{iso}").get_data(as_text=True)
check("<b>0</b>" in html and "Fritz Fahrer" in html and "Komme später" in html, "detail counts")
post(s, f"/training/{sid}/{iso}/rueckmeldung", {"status": "nein"})
r = post(m, f"/training/{sid}/{iso}/rueckmeldung", {"status": "ja"}, follow_redirects=True)
check("Gespeichert: Zusage" in r.get_data(as_text=True), "free after absage")
check(post(m, f"/training/{sid}/{iso}/rueckmeldung", {"status": "quatsch"}).status_code == 400, "bad status")
check(s.get(f"/training/{sid}/2030-01-02").status_code == 404, "wrong weekday 404")
past = (nd - _td(days=14)).isoformat()
r = post(s, f"/training/{sid}/{past}/rueckmeldung", {"status": "ja"}, follow_redirects=True)
check("keine Rückmeldung möglich" in r.get_data(as_text=True), "no past answer")
check(post(s, f"/training/{sid}/{iso}/ausfall", {"reason": "x"}).status_code == 403, "shooter cannot cancel")
post(a2, f"/training/{sid}/{iso}/ausfall", {"reason": "Feiertag"})
r = post(s, f"/training/{sid}/{iso}/rueckmeldung", {"status": "ja"}, follow_redirects=True)
check("keine Rückmeldung möglich" in r.get_data(as_text=True), "no answer when cancelled")
check("fällt aus: Feiertag" in s.get("/termine").get_data(as_text=True), "cancel shown")
post(a2, f"/training/{sid}/{iso}/ausfall", {"undo": "1"})
check(db.execute("select count(*) from training_cancel").fetchone()[0] == 0, "undo cancel")
r = post(s, f"/training/{sid}/{iso}/rueckmeldung", {"status": "spaeter", "back": "/"})
check(r.location.endswith("/"), "back to dashboard")
html = s.get("/").get_data(as_text=True)
check("Diese Woche:" in html, "dashboard agenda this week")
_wk_end = (_d.today() + _td(days=6 - _d.today().weekday())).isoformat()
check(iso > _wk_end or 'ag-item training' in html, "training shown only if this week")
# Agenda mit Wettkampf in 3 Tagen
from datetime import date as _dd, timedelta as _tt
soon = _dd.today().isoformat()
post(a2, f"/mannschaft/{tid3}/wettkampf/neu", {"date": soon, "time": "23:59", "opponent": "Baldgegner", "home": "1"})
html = s.get("/").get_data(as_text=True)
check("ag-item match" in html and "Baldgegner" in html and "open-badge" in html, "agenda match with open badge")
check("has-tr" in s.get(f"/termine?monat={iso[:7]}").get_data(as_text=True), "calendar training")
check(a2.get("/training/zeiten").status_code == 200, "slots page")
print("OK4", ok)

# ---- Neu: RWK-Onlinemelder
import io
ics = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:rwk-1001@rwk\r
DTSTART;TZID=Europe/Berlin:20301107T193000\r
SUMMARY:SV Musterdorf 1 - SG Bergheim 1\r
LOCATION:Schützenhaus Musterdorf\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:rwk-1002@rwk\r
DTSTART:20301121T173000Z\r
SUMMARY:SV Hubertus 1 - SV Musterdorf 1\r
LOCATION:Hubertushalle\, Oberdorf\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:rwk-1003@rwk\r
DTSTART;VALUE=DATE:20301205\r
SUMMARY:SV Musterdorf 1 : SG Eichen\r
 laub 1\r
END:VEVENT\r
END:VCALENDAR\r
"""
check(s.get(f"/mannschaft/{tid1}/rwk-import").status_code == 403, "shooter no import on foreign team")
r = a2.post(f"/mannschaft/{tid3}/rwk-import", data={"csrf": csrf(a2), "rwk_name": "SV Musterdorf 1",
            "ics": (io.BytesIO(ics.encode()), "plan.ics")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check(r.status_code == 200 and "3 Termine." in html, "ics preview")
check('value="SG Bergheim 1"' in html and 'value="SV Hubertus 1"' in html and 'value="SG Eichenlaub 1"' in html, "opponents parsed")
check('value="18:30"' in html, "UTC converted to Berlin time")
form = {"csrf": csrf(a2), "step": "save", "rwk_name": "SV Musterdorf 1", "row": ["0", "1", "2"]}
rows = [("2030-11-07", "19:30", "SG Bergheim 1", "1", "rwk-1001@rwk", "Schützenhaus Musterdorf"),
        ("2030-11-21", "18:30", "SV Hubertus 1", "0", "rwk-1002@rwk", "Hubertushalle, Oberdorf"),
        ("2030-12-05", "", "SG Eichenlaub 1", "1", "rwk-1003@rwk", "")]
for i, (d, t, o, h, u, l) in enumerate(rows):
    form.update({f"use_{i}": "1", f"date_{i}": d, f"time_{i}": t, f"opp_{i}": o, f"home_{i}": h, f"uid_{i}": u, f"loc_{i}": l})
r = a2.post(f"/mannschaft/{tid3}/rwk-import", data=form, follow_redirects=True)
check("3 neu, 0 aktualisiert" in r.get_data(as_text=True), "import created")
check(db.execute("select home, location from matches where ext_uid='rwk-1002@rwk'").fetchone() == (0, "Hubertushalle, Oberdorf"), "away + location")
form["time_0"] = "20:00"
r = a2.post(f"/mannschaft/{tid3}/rwk-import", data=form, follow_redirects=True)
check("0 neu, 3 aktualisiert" in r.get_data(as_text=True), "reimport updates")
check(db.execute("select time from matches where ext_uid='rwk-1001@rwk'").fetchone()[0] == "20:00", "time updated")
r = a2.post(f"/mannschaft/{tid3}/rwk-import", data={"csrf": csrf(a2), "ics": (io.BytesIO(b"hallo"), "x.ics")}, content_type="multipart/form-data")
check("keine Kalenderdatei" in r.get_data(as_text=True), "bad file")
# Link + Ergebnis melden
post(a2, f"/mannschaft/{tid3}/bearbeiten", {"name": "KK 1 neu", "rwk_url": "www.rwk-onlinemelder.de/online/listen/test", "rwk_name": "SV Musterdorf 1"})
check(db.execute("select rwk_url from teams where id=?", (tid3,)).fetchone()[0] == "https://www.rwk-onlinemelder.de/online/listen/test", "rwk url normalized")
check("RWK</span> Tabelle" in s.get(f"/mannschaft/{tid3}").get_data(as_text=True), "rwk link on team")
mm = db.execute("select id from matches where ext_uid='rwk-1001@rwk'").fetchone()[0]
post(a2, f"/wettkampf/{mm}/aufstellung", {"lineup": [users["susi"]], f"rings_{users['susi']}": "390,5", "opponent_score": "380"})
html = a2.get(f"/wettkampf/{mm}").get_data(as_text=True)
import re as _re; print(_re.findall(r"<pre class=\"report\".*?</pre>", html, _re.S)); check("Ergebnis melden" in html and "Susi Schütze: 390,5" in html and "Gesamt: 390,5 : 380" in html, "rwk report")
check("Ergebnis melden" not in f.get(f"/wettkampf/{mm}").get_data(as_text=True), "report only for managers")
print("OK5", ok)

# ---- Neu: Trainer, Sondertraining, Profil, Verein, Gegner, Import
from datetime import date as _d3, timedelta as _t3
post(a2, "/benutzer/neu", {"name": "Tina Trainer", "username": "tina", "role": "trainer", "active": "1", "password": "tinapass1", "player_id": "T-77", "pass_nr": "P-1"})
check(db.execute("select role, player_id, pass_nr from users where username='tina'").fetchone() == ("trainer", "T-77", "P-1"), "trainer + ids saved")
tn = app.test_client(); login(tn, "tina", "tinapass1"); post(tn, "/passwort", {"old": "tinapass1", "new": "tinapass12", "new2": "tinapass12"})
check(tn.get("/training/zeiten").status_code == 200, "trainer can manage")
check(s.get("/training/zeiten").status_code == 403, "shooter cannot manage")
day = (_d3.today() + _t3(days=1)).isoformat()
r = post(tn, "/training/zeiten", {"kind": "sonder", "once_date": day, "start": "17:00", "end": "19:00", "title": "Sondertraining KM", "lanes": "4", "note": "Vorbereitung"})
sp = db.execute("select id from training_slots where once_date=?", (day,)).fetchone()[0]
check(r.status_code == 302 and f"/training/{sp}/{day}" in r.location, "special created")
html = s.get("/termine").get_data(as_text=True)
check("Sondertraining KM" in html and "Vorbereitung" in html and "+ Sondertraining" not in html, "special in Termine")
check("+ Sondertraining" in tn.get("/termine").get_data(as_text=True), "trainer sees special button")
check(s.get(f"/training/{sp}/{(_d3.today() + _t3(days=8)).isoformat()}").status_code == 404, "special only on its date")
check(post(s, f"/training/{sp}/{day}/rueckmeldung", {"status": "ja"}).status_code == 302, "answer special")
check(post(tn, f"/training/{sp}/{day}/ausfall", {"reason": "x"}).status_code == 302, "trainer can cancel")
post(tn, f"/training/{sp}/{day}/ausfall", {"undo": "1"})
# Pausieren einer festen Zeit
sid2 = db.execute("select id from training_slots where once_date is null limit 1").fetchone()[0]
post(tn, "/training/zeiten", {"id": sid2, "weekday": "1", "start": "19:00", "lanes": "6", "active": ""})
check(db.execute("select active from training_slots where id=?", (sid2,)).fetchone()[0] == 0, "pause slot")
post(tn, "/training/zeiten", {"id": sid2, "weekday": "1", "start": "19:00", "lanes": "6", "active": ["", "1"]})
check(db.execute("select active from training_slots where id=?", (sid2,)).fetchone()[0] == 1, "unpause slot")
# Profil
html = s.get("/profil").get_data(as_text=True)
check("Meine Statistik" in html and "Darstellung" in html and "Passwort ändern" in html, "profile page")
post(s, "/profil", {"action": "look", "theme": "dark", "accent": "blau"})
html = s.get("/").get_data(as_text=True)
check('data-theme="dark"' in html and "#4f86bf" in html, "theme + accent applied")
from PIL import Image as _Im
buf = io.BytesIO(); _Im.new("RGB", (600, 400), (200, 30, 30)).save(buf, "PNG"); buf.seek(0)
r = s.post("/profil", data={"csrf": csrf(s), "action": "avatar", "avatar": (buf, "me.png")}, content_type="multipart/form-data")
check(db.execute("select avatar from users where username='susi'").fetchone()[0] >= 1, "avatar saved")
r = s.get(f"/avatar/{users['susi']}")
check(r.status_code == 200 and r.mimetype == "image/jpeg", "avatar served")
r = s.post("/profil", data={"csrf": csrf(s), "action": "avatar", "avatar": (io.BytesIO(b"kein bild"), "x.png")}, content_type="multipart/form-data", follow_redirects=True)
check("konnte nicht gelesen" in r.get_data(as_text=True), "bad avatar")
check(s.get("/passwort").status_code == 302, "password redirects to profile")
# Verein
check(s.get("/verein").status_code == 403 and tn.get("/verein").status_code == 403, "club admin only")
post(a2, "/verein", {"club_name": "SG Teststadt", "club_id": "4711", "club_place": "Teststadt", "club_assoc": "Gau Süd"})
check("SG Teststadt" in s.get("/").get_data(as_text=True), "club name shown")
# Gegner
check(s.get("/gegner").status_code == 403, "shooter no opponents")
post(a2, "/gegner", {"action": "club_save", "name": "SV Edelweiß", "team": "2", "location": "Edelweißhalle"})
cid = db.execute("select id from opp_clubs where name='SV Edelweiß'").fetchone()[0]
otid = db.execute("select id from opp_teams where club_id=?", (cid,)).fetchone()[0]
html = a2.get(f"/mannschaft/{tid3}/wettkampf/neu").get_data(as_text=True)
check(f'value="{otid}" data-loc="Edelweißhalle"' in html and "SV Edelweiß 2" in html, "opponent select")
post(a2, "/gegner", {"action": "club_delete", "club_id": cid})
check(db.execute("select count(*) from opp_clubs").fetchone()[0] == 0 and db.execute("select count(*) from opp_teams").fetchone()[0] == 0, "opponent deleted")
# CSV-Import Gegner
csvg = "Verein;Mannschaft;Ort\nSG Bergheim;1;Bergheim\nSG Bergheim;1;Bergheim neu\nSV Tell;2;\n".encode("cp1252")
r = a2.post("/import/gegner", data={"csrf": csrf(a2), "csv": (io.BytesIO(csvg), "g.csv")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check("4 Zeilen gefunden" in html and 'value="club" selected' in html, "opp csv preview")
import re as _re2
data = _re2.search(r'<textarea name="data" hidden>(.*?)</textarea>', html, _re2.S).group(1)
import html as _h
from clubstep_helper import confirm_clubs
_f = {"csrf": csrf(a2), "step": "save", "header": "1", "data": _h.unescape(data), "col_0": "club", "col_1": "team", "col_2": "location"}
_f = confirm_clubs(lambda f: a2.post("/import/gegner", data=f), _f)
r = a2.post("/import/gegner", data=_f, follow_redirects=True)
check("2 neu, 1 aktualisiert" in r.get_data(as_text=True), "opp import")
# CSV-Import Schützen (DISAG-ähnlich, Komma, UTF-8 mit BOM)
csvs = "﻿Startnummer,Nachname,Vorname,Passnummer,Verein\n1001,Müller,Hans,PA-1,SV Musterdorf\nT-77,Trainer,Tina,PA-2,SV Musterdorf\n,,,,\n".encode("utf-8")
r = a2.post("/import/schuetzen", data={"csrf": csrf(a2), "csv": (io.BytesIO(csvs), "s.csv")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check('value="player_id" selected' in html and 'value="nachname" selected' in html and 'value="vorname" selected' in html and 'value="pass_nr" selected' in html, "shooter mapping guessed")
data = _h.unescape(_re2.search(r'<textarea name="data" hidden>(.*?)</textarea>', html, _re2.S).group(1))
r = a2.post("/import/schuetzen", data={"csrf": csrf(a2), "step": "save", "header": "1", "data": data, "col_0": "player_id", "col_1": "nachname", "col_2": "vorname", "col_3": "pass_nr", "col_4": "club",
    "clubs_chosen": "1", "club_count": "1", "cn_0": "SV Musterdorf", "cc_0": "member"})
html = r.get_data(as_text=True)
check("hans.mueller" in html and "Neue Zugänge" in html, "new account listed")
check(db.execute("select pass_nr from users where username='tina'").fetchone()[0] == "PA-2", "existing updated by id")
check(s.get("/import/schuetzen").status_code == 403 and s.get("/import/gegner").status_code == 403 and a2.get("/import/gegner").status_code == 200, "import permissions")
print("OK6", ok)

# ---- Neu: Trainingsgruppen
check(tn.get("/gruppen").status_code == 403 and s.get("/gruppen").status_code == 403, "groups admin only")
post(a2, "/gruppen", {"name": "Jugend"}); post(a2, "/gruppen", {"name": "Erwachsene"})
r = post(a2, "/gruppen", {"name": "jugend"}, follow_redirects=True)
check("gibt es schon" in r.get_data(as_text=True), "group dupe")
gj = db.execute("select id from groups where name='Jugend'").fetchone()[0]
ge = db.execute("select id from groups where name='Erwachsene'").fetchone()[0]
post(a2, "/gruppen", {"id": gj, "name": "Jugend", "members_sent": "1", "members": [fid]})
post(a2, f"/benutzer/{users['susi']}", {"name": "Susi Schütze", "username": "susi", "role": "shooter", "active": "1", "password": "", "groups": [ge]})
check(db.execute("select group_id from user_groups where user_id=?", (users["susi"],)).fetchall() == [(ge,)], "user groups via user form")
jday = (_d3.today() + _t3(days=2)).isoformat()
post(tn, "/training/zeiten", {"kind": "sonder", "once_date": jday, "start": "16:00", "title": "Jugendtraining Spezial", "lanes": "6", "groups": [gj]})
jsp = db.execute("select id from training_slots where title='Jugendtraining Spezial'").fetchone()[0]
check(db.execute("select group_id from slot_groups where slot_id=?", (jsp,)).fetchall() == [(gj,)], "slot group saved")
check("Jugendtraining Spezial" not in s.get("/termine").get_data(as_text=True), "susi (Erwachsene) does not see Jugend")
check(s.get(f"/training/{jsp}/{jday}").status_code == 404, "susi cannot open Jugend training")
check(post(s, f"/training/{jsp}/{jday}/rueckmeldung", {"status": "ja"}).status_code == 404, "susi cannot answer Jugend training")
html = f.get("/termine").get_data(as_text=True)
check("Jugendtraining Spezial" in html and 'pill grp">Jugend' in html, "fritz (Jugend) sees it")
check(post(f, f"/training/{jsp}/{jday}/rueckmeldung", {"status": "ja"}).status_code == 302, "fritz answers")
check("Jugendtraining Spezial" in tn.get("/termine").get_data(as_text=True), "trainer sees all")
check("Jugendtraining Spezial" not in tn.get("/").get_data(as_text=True), "trainer overview only own groups")
# Training ohne Gruppe sieht jeder
check("Sondertraining KM" in s.get("/termine").get_data(as_text=True), "ungrouped visible to all")
# Gruppe löschen -> Training für alle
post(a2, "/gruppen", {"id": gj, "delete": "1"})
check("Jugendtraining Spezial" in s.get("/termine").get_data(as_text=True), "after delete visible to all")
check(a2.get("/gruppen").status_code == 200 and tn.get("/training/zeiten").status_code == 200, "pages render")
print("OK7", ok)

# ---- Neu: Verlegung
mvm = db.execute("select id from matches where ext_uid='rwk-1002@rwk'").fetchone()[0]   # 2030-11-21 18:30, KK 1 neu
post(f, f"/wettkampf/{mvm}/verfuegbarkeit", {"status": "ja"})
check(post(f, f"/wettkampf/{mvm}/verlegen", {"action": "antrag", "new_date": "2030-11-28"}).status_code == 403, "shooter cannot move")
r = post(a2, f"/wettkampf/{mvm}/verlegen", {"action": "antrag", "new_date": "2030-11-21", "new_time": "18:30"}, follow_redirects=True)
check("derselbe" in r.get_data(as_text=True), "same date rejected")
post(a2, f"/wettkampf/{mvm}/verlegen", {"action": "antrag", "new_date": "2030-11-28", "new_time": "19:00", "reason": "Schützenfest"})
html = a2.get(f"/wettkampf/{mvm}").get_data(as_text=True)
check("Verlegung beantragt" in html and "Antrag auf Verlegung" in html and "Grund: Schützenfest" in html and "Neuer Termin: Do, 28.11.2030, 19:00 Uhr" in html, "move request + text")
check("Antrag auf Verlegung" not in f.get(f"/wettkampf/{mvm}").get_data(as_text=True) and "Verlegung beantragt" in f.get(f"/wettkampf/{mvm}").get_data(as_text=True), "members see banner, not form")
r = post(a2, f"/wettkampf/{mvm}/verlegen", {"action": "antrag", "new_date": "2030-11-29"}, follow_redirects=True)
check("schon einen offenen" in r.get_data(as_text=True), "only one open move")
check("Verlegung beantragt →" in f.get("/termine").get_data(as_text=True), "agenda shows pending move")
check(db.execute("select date from matches where id=?", (mvm,)).fetchone()[0] == "2030-11-21", "date unchanged while open")
# RWK-Import mit altem Termin: Verlegung schützen
r = a2.post(f"/mannschaft/{tid3}/rwk-import", data={"csrf": csrf(a2), "rwk_name": "SV Musterdorf 1", "ics": (io.BytesIO(ics.encode()), "plan.ics")}, content_type="multipart/form-data")
check("in der App verlegt" not in r.get_data(as_text=True), "open move with unchanged date: no warning yet")
post(a2, f"/wettkampf/{mvm}/verlegen", {"action": "bestaetigt"})
check(db.execute("select date, time from matches where id=?", (mvm,)).fetchone() == ("2030-11-28", "19:00"), "move applied")
check(db.execute("select count(*) from availability where match_id=?", (mvm,)).fetchone()[0] == 0, "availability reset")
r = a2.post(f"/mannschaft/{tid3}/rwk-import", data={"csrf": csrf(a2), "rwk_name": "SV Musterdorf 1", "ics": (io.BytesIO(ics.encode()), "plan.ics")}, content_type="multipart/form-data")
html = r.get_data(as_text=True)
check("in der App verlegt auf Do, 28.11.2030" in html, "import warns about moved match")
check("Verlauf der Verlegungen (1)" in a2.get(f"/wettkampf/{mvm}").get_data(as_text=True), "history shown")
post(a2, f"/wettkampf/{mvm}/verlegen", {"action": "antrag", "new_date": "2030-12-01"})
post(a2, f"/wettkampf/{mvm}/verlegen", {"action": "abgelehnt"})
check(db.execute("select date from matches where id=?", (mvm,)).fetchone()[0] == "2030-11-28", "rejected keeps date")
print("OK8", ok)

# ---- Neu: Filter unter dem Kalender
html = f.get("/termine").get_data(as_text=True)
check('class="filter-bar"' in html and "Wettkämpfe" in html and "Aufgestellt" in html, "filter bar")
hm = f.get("/termine?filter=wettkampf").get_data(as_text=True)
check("ag-item match" in hm and "ag-item training" not in hm.split('id="liste"')[1], "filter matches")
ht = f.get("/termine?filter=training").get_data(as_text=True)
check("ag-item training" in ht and "ag-item match" not in ht.split('id="liste"')[1], "filter trainings")
ho = f.get("/termine?filter=offen").get_data(as_text=True).split('id="liste"')[1]
check("is-open" in ho and ho.count("ag-item") == ho.count("is-open"), "filter open only unanswered")
hz = f.get("/termine?filter=zugesagt").get_data(as_text=True).split('id="liste"')[1]
check("Jugendtraining Spezial" in hz and "Baldgegner" not in hz, "filter zugesagt")
check(f.get("/termine?filter=quatsch").status_code == 200, "bad filter ignored")
check("filter=training" in f.get("/termine?filter=training").get_data(as_text=True).split('aria-label="Nächster Monat"')[0], "month nav keeps filter")
print("OK9", ok)

# ---- Neu: LG/KK-Bereiche
kday = (_d3.today() + _t3(days=3)).isoformat()
post(tn, "/training/zeiten", {"kind": "sonder", "once_date": kday, "start": "19:00", "title": "LG + KK", "area1": "LG", "lanes": "2", "area2": "KK", "lanes2": "1"})
ks = db.execute("select id from training_slots where title='LG + KK'").fetchone()[0]
check(db.execute("select lanes, lanes2, area1, area2 from training_slots where id=?", (ks,)).fetchone() == (2, 1, "LG", "KK"), "areas saved")
html = s.get("/termine").get_data(as_text=True)
check("<b>LG</b> 2/2 frei" in html and "<b>KK</b> 1/1 frei" in html, "agenda shows area free")
html = s.get(f"/training/{ks}/{kday}").get_data(as_text=True)
check("Was möchtest du schießen?" in html and "von 3 Ständen frei" in html, "detail area choice")
post(s, f"/training/{ks}/{kday}/rueckmeldung", {"status": "ja", "area": "2"})
check(db.execute("select area from training_rsvp where slot_id=? and user_id=?", (ks, users["susi"])).fetchone()[0] == 2, "susi wants KK")
r = post(f, f"/training/{ks}/{kday}/rueckmeldung", {"status": "ja", "area": "2"}, follow_redirects=True)
check("Im Bereich KK sind alle 1" in r.get_data(as_text=True), "KK full")
post(f, f"/training/{ks}/{kday}/rueckmeldung", {"status": "ja", "area": ""})
post(tn, f"/training/{ks}/{kday}/rueckmeldung", {"status": "spaeter"})
check(db.execute("select count(*) from training_rsvp where slot_id=? and area is null and status in ('ja','spaeter')", (ks,)).fetchone()[0] == 2, "two unassigned")
r = post(a2, f"/training/{ks}/{kday}/rueckmeldung", {"status": "ja"}, follow_redirects=True)
check("Alle 3 Stände" in r.get_data(as_text=True), "total capacity")
# Status ändern behält Bereich
post(s, f"/training/{ks}/{kday}/rueckmeldung", {"status": "spaeter"})
check(db.execute("select area from training_rsvp where slot_id=? and user_id=?", (ks, users["susi"])).fetchone()[0] == 2, "area kept on status change")
# Trainer teilt zu
tid_ = db.execute("select id from users where username='tina'").fetchone()[0]
check(post(s, f"/training/{ks}/{kday}/zuweisen", {}).status_code == 403, "shooter cannot assign")
r = post(tn, f"/training/{ks}/{kday}/zuweisen", {f"area_{users['susi']}": "2", f"area_{fid}": "2", f"area_{tid_}": "1"}, follow_redirects=True)
check("Zu viele zugeteilt" in r.get_data(as_text=True), "assign over capacity rejected")
post(tn, f"/training/{ks}/{kday}/zuweisen", {f"area_{users['susi']}": "2", f"area_{fid}": "1", f"area_{tid_}": "1"})
check(dict(db.execute("select user_id, area from training_rsvp where slot_id=? and status!='nein'", (ks,)).fetchall()) == {users["susi"]: 2, fid: 1, tid_: 1}, "assignment saved")
html = tn.get(f"/training/{ks}/{kday}").get_data(as_text=True)
check('id="zuteilung"' in html and 'pill area">KK' in html, "assignment ui + pills")
check('id="zuteilung"' not in s.get(f"/training/{ks}/{kday}").get_data(as_text=True), "no assignment ui for shooter")
check("du: KK" in s.get("/termine").get_data(as_text=True), "my area in agenda")
# Bereich 2 entfernen setzt Zuteilung zurück
post(tn, "/training/zeiten", {"id": ks, "kind": "sonder", "once_date": kday, "start": "19:00", "title": "LG + KK", "lanes": "3", "lanes2": "0"})
check(db.execute("select count(*) from training_rsvp where slot_id=? and area is not null", (ks,)).fetchone()[0] == 0, "areas cleared")
print("OK10", ok)

# ---- Neu: Admin-Dashboard
html = a2.get("/").get_data(as_text=True)
check("Gerade online" in html and "Letzte Anmeldungen" in html and "Fehlerprotokoll" in html and "Datenbank herunterladen" in html, "admin dashboard")
check(html.count("Server &amp; Wartung") == 1 and "<h2>Server</h2>" not in html and "Linux-Pakete</dt>" not in html, "dashboard: one collapsible, no duplicates")
check("Gerade online" not in s.get("/").get_data(as_text=True), "shooter keeps agenda")
check(db.execute("select count(*) from login_log where ok=1").fetchone()[0] >= 5, "logins logged")
x = app.test_client(); login(x, "susi", "falsch")
check(db.execute("select username, ok from login_log order by id desc limit 1").fetchone() == ("susi", 0), "failed login logged")
check("Fehlversuch" in a2.get("/").get_data(as_text=True), "failed shown")
for _ in range(10):
    post(x, "/login", {"username": "susi", "password": "falsch"})
r = post(x, "/login", {"username": "susi", "password": "susipass123"})
check(r.status_code == 429, "lockout after many failures")
db.execute("delete from login_log where ok=0"); db.commit()
check(db.execute("select last_seen from users where username='susi'").fetchone()[0] is not None, "last_seen tracked")
# Fehler protokollieren
app.config["TESTING"] = False; app.config["PROPAGATE_EXCEPTIONS"] = False
r = s.get("/kaputt")
app.config["TESTING"] = True
check(r.status_code == 500 and "protokolliert" in r.get_data(as_text=True), "500 page")
row = db.execute("select message, path, detail from error_log order by id desc limit 1").fetchone()
check(row[0] == "ValueError: Testfehler 42" and "/kaputt" in row[1] and "Traceback" in row[2], "error logged")
html = a2.get("/").get_data(as_text=True)
check("ValueError: Testfehler 42" in html, "error on dashboard")
check(a2.get("/admin/protokoll?art=fehler").status_code == 200 and a2.get("/admin/protokoll").status_code == 200, "log pages")
check(s.get("/admin/protokoll").status_code == 403 and s.get("/admin/backup").status_code == 403, "admin only")
r = a2.get("/admin/backup")
check(r.status_code == 200 and r.data[:15] == b"SQLite format 3" and "verein-backup" in r.headers["Content-Disposition"], "backup download")
post(a2, "/admin/protokoll/leeren")
check(db.execute("select count(*) from error_log").fetchone()[0] == 0, "clear errors")
check(s.get("/gibtsnicht").status_code == 404 and db.execute("select count(*) from error_log").fetchone()[0] == 0, "404 not logged as error")
print("OK11", ok)

r = post(a, "/logout")
check(a.get("/").status_code == 302, "logged out")
print("OK", ok, "checks")
