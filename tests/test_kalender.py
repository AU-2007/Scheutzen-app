import os, sys, tempfile, io, re, contextlib, sqlite3
from datetime import date, timedelta
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
def login(user, pw):
    c = app.test_client(); c.get("/login"); post(c, "/login", {"username": user, "password": pw}); return c

db = sqlite3.connect(os.environ["DB_PATH"])
db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES('Jens Müller','jens',?, 'shooter',1,0)",
           (A.generate_password_hash("passwort12"),))
db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES('Eva','eva',?, 'shooter',1,0)",
           (A.generate_password_hash("passwort12"),))
jid, eid = 2, 3
db.execute("INSERT INTO teams(id, name, discipline) VALUES(1,'LG 1','LG')")
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(1,?)", (jid,))
d1 = (date.today() + timedelta(days=10)).isoformat(); d2 = (date.today() + timedelta(days=17)).isoformat()
d3 = (date.today() + timedelta(days=24)).isoformat()
db.execute("INSERT INTO matches(id, team_id, date, time, opponent, home, location, meet, notes) VALUES(1,1,?, '19:30','SV Eisenbach 3',1,'','18:45 Schützenhaus','Bitte; früh, da sein')", (d1,))
db.execute("INSERT INTO matches(id, team_id, date, time, opponent, home, location) VALUES(2,1,?, '','SG Nord',0,'Nordheim, Hauptstr. 5')", (d2,))
db.execute("INSERT INTO matches(id, team_id, date, time, opponent, home) VALUES(3,1,?, '20:00','SV Süd',1)", (d3,))
wd = (date.today() + timedelta(days=3)).weekday()
db.execute("INSERT INTO training_slots(id, weekday, start, end, title, lanes, location, active) VALUES(1,?, '19:00','21:00','Training',6,'',1)", (wd,))
db.commit()

j = login("jens", "passwort12"); e = login("eva", "passwort12")
body = j.get("/profil").get_data(as_text=True)
check("Kalender verbinden" in body, "profile card")
check(post(j, "/profil", {"action": "cal_on"}).status_code == 302, "cal on")
tok = db.execute("SELECT cal_token FROM users WHERE id=?", (jid,)).fetchone()[0]
check(tok and len(tok) >= 40, "token")
body = j.get("/profil").get_data(as_text=True)
check(f"/kalender/{tok}.ics" in body and "webcal://" in body and "calendar.google.com" in body, "link shown")

AND = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 SamsungBrowser/25.0"}
IOS = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"}
b = j.get("/profil", headers=AND).get_data(as_text=True)
check("Mit Google-Kalender verbinden" in b and "play.google.com" not in b and "1,99" not in b and "Synchronisieren“ einschalten" in b, "android view free + sync hint")
b = j.get("/profil", headers=IOS).get_data(as_text=True)
check("Zum Kalender hinzufügen" in b and "ICSx" not in b and 'href="webcal://' in b, "ios view")
anon = app.test_client()
def feed(t=tok):
    return anon.get(f"/kalender/{t}.ics")
check("noch nicht abgerufen" in j.get("/profil").get_data(as_text=True), "not fetched yet")
r = anon.get(f"/kalender/{tok}.ics", headers={"User-Agent": "Google-Calendar-Importer"})
check("Zuletzt abgerufen" in j.get("/profil").get_data(as_text=True) and "Google Kalender" in j.get("/profil").get_data(as_text=True), "fetch recorded")
r = feed(); txt = r.get_data(as_text=True)
check(r.status_code == 200 and r.headers["Content-Type"].startswith("text/calendar"), "feed ok")
check(txt.startswith("BEGIN:VCALENDAR\r\n") and txt.endswith("END:VCALENDAR\r\n"), "crlf")
check("BEGIN:VEVENT" not in txt and "BEGIN:VTIMEZONE" in txt, "no events yet")
check(all(len(l.encode()) <= 75 for l in txt.split("\r\n")), "folded")

# Zusagen
post(j, "/wettkampf/1/verfuegbarkeit", {"status": "ja"})
post(j, "/wettkampf/2/verfuegbarkeit", {"status": "vielleicht"})
post(j, "/wettkampf/3/verfuegbarkeit", {"status": "nein"})
tdate = (date.today() + timedelta(days=3)).isoformat()
post(j, f"/training/1/{tdate}/rueckmeldung", {"status": "ja"})
txt = feed().get_data(as_text=True)
unf = txt.replace("\r\n ", "")
check(unf.count("BEGIN:VEVENT") == 2, "zusage only: match1 + training")
check("UID:wettkampf-1@" in unf and f"DTSTART;TZID=Europe/Berlin:{d1.replace('-', '')}T193000" in unf, "match1 start")
check(f"DTEND;TZID=Europe/Berlin:{d1.replace('-', '')}T223000" in unf, "match1 3h")
check("SUMMARY:LG 1 vs. SV Eisenbach 3" in unf, "summary")
check("Bitte\\; früh\\, da sein" in unf and "Treffpunkt: 18:45 Schützenhaus" in unf, "escaped desc")
check(f"UID:training-1-{tdate}@" in unf and "T190000" in unf and "T210000" in unf, "training times")
check("wettkampf-2@" not in unf and "wettkampf-3@" not in unf, "vielleicht/nein excluded")

# Angebot nach Zusage (ohne Abo), egal von welcher Seite
db.execute("UPDATE users SET cal_token=NULL WHERE id=?", (jid,)); db.commit()
r = post(j, "/wettkampf/1/verfuegbarkeit", {"status": "ja", "back": "/"}, follow_redirects=True)
check("📅 In meinen Kalender eintragen" in r.get_data(as_text=True) and "/wettkampf/1/kalender.ics" in r.get_data(as_text=True), "offer after zusage")
check("eintragen" not in j.get("/").get_data(as_text=True), "offer only once")
r = post(j, "/wettkampf/2/verfuegbarkeit", {"status": "vielleicht"}, follow_redirects=True)
check("eintragen" not in r.get_data(as_text=True), "no offer for vielleicht")
post(j, "/wettkampf/2/verfuegbarkeit", {"status": "vielleicht"})
r = post(j, f"/training/1/{tdate}/rueckmeldung", {"status": "ja"}, follow_redirects=True)
check(f"/training/1/{tdate}/kalender.ics" in r.get_data(as_text=True), "offer after training")
db.execute("UPDATE users SET cal_token=? WHERE id=?", (tok, jid)); db.commit()
r = post(j, "/wettkampf/1/verfuegbarkeit", {"status": "ja"}, follow_redirects=True)
check("eintragen" not in r.get_data(as_text=True), "no offer with abo")
# Einzeltermin zum Antippen
r = j.get("/wettkampf/1/kalender.ics"); t1 = r.get_data(as_text=True)
check(r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and t1.count("BEGIN:VEVENT") == 1 and "UID:wettkampf-1@" in t1, "single match ics")
check(j.get(f"/training/1/{tdate}/kalender.ics").status_code == 200, "single training ics")
check(e.get("/wettkampf/1/kalender.ics").status_code == 404, "eva not member -> 404")
check(app.test_client().get("/wettkampf/1/kalender.ics").status_code in (302, 401), "single needs login")
check(j.get("/training/1/x/kalender.ics").status_code == 404, "bad date")
db.execute("UPDATE users SET cal_token=NULL WHERE id=?", (jid,)); db.commit()
check("📅 In meinen Kalender" in j.get("/wettkampf/1").get_data(as_text=True), "button without abo")
check("📅 In meinen Kalender" in j.get(f"/training/1/{tdate}").get_data(as_text=True), "training button")
db.execute("UPDATE users SET cal_token=? WHERE id=?", (tok, jid)); db.commit()
check("Kommt automatisch" in j.get("/wettkampf/1").get_data(as_text=True), "hint with abo")
# Umfang „alle“
post(j, "/profil", {"action": "cal_scope", "scope": "alle"})
unf = feed().get_data(as_text=True).replace("\r\n ", "")
check("UID:wettkampf-2@" in unf and "SUMMARY:Vielleicht: LG 1 @ SG Nord" in unf, "vielleicht tentative")
check(f"DTSTART;VALUE=DATE:{d2.replace('-', '')}" in unf, "all-day without time")
check("LOCATION:Nordheim\\, Hauptstr. 5" in unf, "location")
check("wettkampf-3@" not in unf, "absage still excluded")
evs = A.parse_ics(feed().get_data(as_text=True))
check(len(evs) == unf.count("BEGIN:VEVENT"), "parse_ics count %d vs %d" % (len(evs), unf.count("BEGIN:VEVENT")))
open(os.path.join(tempfile.gettempdir(), "feed_sample.ics"), "wb").write(feed().get_data())
check("STATUS:TENTATIVE" in unf and "Offen: Training" in unf, "open trainings tentative")
post(j, "/profil", {"action": "cal_scope", "scope": "boese"})
check(db.execute("SELECT cal_scope FROM users WHERE id=?", (jid,)).fetchone()[0] == "zusage", "bad scope -> zusage")

# Absage entfernt den Termin, Ausfall entfernt Training
post(j, "/wettkampf/1/verfuegbarkeit", {"status": "nein"})
db.execute("INSERT INTO training_cancel(slot_id, date, reason) VALUES(1,?, 'Stand gesperrt')", (tdate,)); db.commit()
check("BEGIN:VEVENT" not in feed().get_data(as_text=True), "removed after absage/ausfall")

# Fremde sehen nichts; ungültige Links
check(anon.get("/kalender/" + "x" * 43 + ".ics").status_code == 404, "unknown token")
check(anon.get("/kalender/abc.ics").status_code == 404, "short token")
check(anon.get("/kalender/../../etc.ics").status_code == 404, "traversal")
check(feed(tok + "'--").status_code == 404, "sql chars")
r = feed(); check(r.headers.get("Cache-Control") == "private, no-store" and r.headers.get("X-Robots-Tag") == "noindex", "headers")
# Eva ohne Abo: kein Link, Eva kann Jens' Token nicht ändern (eigene Profil-Aktionen wirken nur auf sie)
post(e, "/profil", {"action": "cal_off"})
check(feed().status_code == 200, "other user can't disable")
# CSRF
check(j.post("/profil", data={"action": "cal_new"}).status_code == 400, "csrf")
# neuer Link
post(j, "/profil", {"action": "cal_new"})
check(feed().status_code == 404, "old token dead after renew")
tok2 = db.execute("SELECT cal_token FROM users WHERE id=?", (jid,)).fetchone()[0]
check(tok2 != tok and feed(tok2).status_code == 200, "new token works")
# gesperrt
db.execute("UPDATE users SET active=0 WHERE id=?", (jid,)); db.commit()
check(feed(tok2).status_code == 404, "locked user")
db.execute("UPDATE users SET active=1 WHERE id=?", (jid,)); db.commit()
# aus
post(j, "/profil", {"action": "cal_off"})
check(feed(tok2).status_code == 404 and "Kalender verbinden" in j.get("/profil").get_data(as_text=True), "off")
# Feed ist ohne Session erreichbar, sonst nichts
check(anon.get("/profil").status_code in (302, 401), "profile needs login")
print("KALENDER OK", ok)
