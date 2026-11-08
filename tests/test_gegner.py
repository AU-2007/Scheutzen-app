import os, sys, tempfile, io, contextlib, sqlite3, re
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = tmp + "/t.db"; os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
os.environ["VEREIN_NAME"] = "SV Eisenbach"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
# Diese älteren Tests nutzen Namen im Format "Vorname Nachname"
import sqlite3 as _sq; _c = _sq.connect(os.environ["DB_PATH"])
_c.execute("INSERT INTO settings(key, value) VALUES('name_order','vorname') ON CONFLICT(key) DO UPDATE SET value='vorname'"); _c.commit(); _c.close()
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
db.execute("UPDATE users SET must_change_pw=0")
db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES('Eva','eva',?, 'shooter',1,0)", (A.generate_password_hash("passwort12"),))
db.execute("INSERT INTO teams(id, name) VALUES(1, 'LG 1')")
# Ausgangslage wie beim Benutzer: Verein mit Namen (aus RWK) und derselbe nur als Nummer (aus Datei)
db.execute("INSERT INTO opp_clubs(id, name, location) VALUES(1, 'SV Beerfurth', 'Schwimmbadstraße 46, 64385 Reichelsheim')")
db.execute("INSERT INTO opp_teams(id, club_id, name) VALUES(10, 1, '1')")
db.execute("INSERT INTO matches(id, team_id, date, opponent, opp_team_id) VALUES(1, 1, '2026-12-01', 'SV Beerfurth 1', 10)")
db.execute("INSERT INTO opp_shooters(club_id, team_id, name, pass_nr) VALUES(1, 10, 'Anna Alt', '')")
db.execute("INSERT INTO opp_clubs(id, name) VALUES(2, '3200005')")
db.execute("INSERT INTO opp_teams(id, club_id, name) VALUES(20, 2, '1')")
db.execute("INSERT INTO matches(id, team_id, date, opponent, opp_team_id) VALUES(2, 1, '2026-12-08', 'x', 20)")
db.execute("INSERT INTO opp_shooters(id, club_id, team_id, name, pass_nr, birth_year) VALUES(50, 2, 20, 'Anna Alt', 'P-1', '1990')")
db.execute("INSERT INTO opp_shooters(club_id, name) VALUES(2, 'Bert Neu')")
db.execute("INSERT INTO match_opp(match_id, shooter_id, name) VALUES(2, 50, 'Anna Alt')")
db.execute("INSERT INTO opp_clubs(id, name) VALUES(3, '3200006')")
db.execute("INSERT INTO opp_shooters(club_id, name) VALUES(3, 'Carl Drei')")
db.execute("INSERT INTO opp_clubs(id, name) VALUES(4, 'SG Neu-Isenburg')")
db.commit()

a = login("admin", "adminpass1")
body = a.get("/gegner").get_data(as_text=True)
check("Bitte zuordnen" in body and "Nr. 3200005" in body and "Anna Alt, Bert Neu" in body, "unnamed section")
check(body.index("Bitte zuordnen") < body.index("SV Beerfurth</b>"), "unnamed on top")

# Rechte
e = login("eva", "passwort12")
check(post(e, "/gegner", {"action": "club_merge", "club_id": 2, "target_id": 1}).status_code == 403, "shooter cannot merge")

# Zusammenführen
r = post(a, "/gegner", {"action": "club_merge", "club_id": 2, "target_id": 1}, follow_redirects=True)
check("zusammengeführt" in r.get_data(as_text=True), "merge flash")
check(db.execute("SELECT COUNT(*) FROM opp_clubs WHERE id=2").fetchone()[0] == 0, "source deleted")
check(db.execute("SELECT club_nr FROM opp_clubs WHERE id=1").fetchone()[0] == "3200005", "number kept on target")
anna = db.execute("SELECT * FROM opp_shooters WHERE name='Anna Alt'").fetchall()
check(len(anna) == 1 and anna[0]["pass_nr"] == "P-1" and anna[0]["birth_year"] == "1990" and anna[0]["club_id"] == 1, "duplicate shooter merged, data filled")
check(db.execute("SELECT shooter_id FROM match_opp WHERE match_id=2").fetchone()[0] == anna[0]["id"], "match shooter re-pointed")
check(db.execute("SELECT club_id FROM opp_shooters WHERE name='Bert Neu'").fetchone()[0] == 1, "other shooter moved")
check(db.execute("SELECT COUNT(*) FROM opp_teams WHERE club_id=1").fetchone()[0] == 1, "same-named teams merged")
check(db.execute("SELECT opp_team_id FROM matches WHERE id=2").fetchone()[0] == 10, "match team re-pointed")
check(post(a, "/gegner", {"action": "club_merge", "club_id": 1, "target_id": 1}, follow_redirects=True).status_code == 200
      and db.execute("SELECT COUNT(*) FROM opp_clubs WHERE id=1").fetchone()[0] == 1, "merge into itself refused")
check(db.execute("SELECT COUNT(*) FROM opp_clubs WHERE id=1").fetchone()[0] == 1, "target still there")

# Namen geben: Nummer bleibt als Vereinsnummer
post(a, "/gegner", {"action": "club_save", "club_id": 3, "name": "SV Mengerskirchen"})
c3 = db.execute("SELECT * FROM opp_clubs WHERE id=3").fetchone()
check(c3["name"] == "SV Mengerskirchen" and c3["club_nr"] == "3200006", "naming keeps number")
r = post(a, "/gegner", {"action": "club_save", "club_id": 3, "name": "SV Beerfurth"}, follow_redirects=True)
check("gibt es schon" in r.get_data(as_text=True), "rename to existing name refused")

# Import Schützen: Nummer bekannt -> automatisch, unbekannte Nummer -> zuordnen / neu benennen, Name mit e.V. -> erkannt
csv = ("Vorname;Name;Verein;Pass\r\n"
       "Max;Mitglied;SV Eisenbach;1\r\n"
       "Paul;Bekannt;3200005;2\r\n"
       "Paula;Zuordnen;3200099;3\r\n"
       "Petra;Neuling;3200100;4\r\n"
       "Peter;Isenburg;SG Neu-Isenburg e.V.;5\r\n")
with a.session_transaction() as _s: _s["csrf"] = "t"
r = a.post("/import/schuetzen", data={"csrf": "t", "csv": (io.BytesIO(csv.encode("utf-8")), "s.csv")}, content_type="multipart/form-data")
data = re.search(r'<textarea name="data" hidden>(.*?)</textarea>', r.get_data(as_text=True), re.S).group(1)
import html as _h
data = _h.unescape(data)
form = {"step": "save", "header": "1", "data": data, "col_0": "vorname", "col_1": "nachname", "col_2": "club", "col_3": "pass_nr"}
r = post(a, "/import/schuetzen", form)
step = r.get_data(as_text=True)
check("Vereinsnummer" in step and "Gegnerverein" in step, "club step shows mapping")
rows = re.findall(r'name="cn_(\d+)" value="([^"]*)"', step)
check(len(rows) == 5, "5 clubs listed")
idx = {v: i for i, v in rows}
# erkannt?
seg = step.split(f'name="cv_{idx["3200005"]}"')[1].split("</select>")[0]
check('value="1" selected' in seg, "known number auto-matched to SV Beerfurth")
seg = step.split(f'name="cv_{idx["SG Neu-Isenburg e.V."]}"')[1].split("</select>")[0]
check('value="4" selected' in seg, "name with e.V. matched")
form.update({"clubs_chosen": "1", "club_count": "5"})
for v, i in idx.items():
    form[f"cn_{i}"] = v
    form[f"cc_{i}"] = "member" if v == "SV Eisenbach" else "opponent"
form[f"cv_{idx['3200005']}"] = "1"
form[f"cv_{idx['3200099']}"] = "3"                       # Mengerskirchen
form[f"cv_{idx['3200100']}"] = "new"; form[f"cnew_{idx['3200100']}"] = "SV Dietkirchen"
form[f"cv_{idx['SG Neu-Isenburg e.V.']}"] = "4"
form[f"cv_{idx['SV Eisenbach']}"] = "new"
r = post(a, "/import/schuetzen", form, follow_redirects=True)
check(r.status_code == 200, "import ok")
club_of = lambda n: db.execute("SELECT c.name FROM opp_shooters s JOIN opp_clubs c ON c.id=s.club_id WHERE s.name=?", (n,)).fetchone()
check(club_of("Paul Bekannt")[0] == "SV Beerfurth", "known nr")
check(club_of("Paula Zuordnen")[0] == "SV Mengerskirchen", "assigned")
check(db.execute("SELECT club_nr FROM opp_clubs WHERE id=3").fetchone()[0] == "3200006", "existing nr not overwritten")
d = db.execute("SELECT * FROM opp_clubs WHERE name='SV Dietkirchen'").fetchone()
check(d and d["club_nr"] == "3200100" and club_of("Petra Neuling")[0] == "SV Dietkirchen", "new club named, nr stored")
check(club_of("Peter Isenburg")[0] == "SG Neu-Isenburg", "e.V. variant matched")
check(db.execute("SELECT COUNT(*) FROM opp_clubs WHERE name GLOB '[0-9]*'").fetchone()[0] == 0, "no number-only clubs created")
check(db.execute("SELECT COUNT(*) FROM users WHERE name='Max Mitglied'").fetchone()[0] == 1, "member created")

# Nächster Import mit 3200100 erkennt automatisch
with a.session_transaction() as _s: _s["csrf"] = "t"
r = a.post("/import/gegner", data={"csrf": "t", "csv": (io.BytesIO("Verein;Mannschaft;Name\r\n3200100;2;Zoe Zwei\r\n".encode()), "g.csv")}, content_type="multipart/form-data")
data = _h.unescape(re.search(r'<textarea name="data" hidden>(.*?)</textarea>', r.get_data(as_text=True), re.S).group(1))
g = {"step": "save", "header": "1", "data": data, "col_0": "club", "col_1": "team", "col_2": "name"}
step = post(a, "/import/gegner", g).get_data(as_text=True)
check("Mitglieder (Benutzer)" not in step and "Erkannt" in step, "gegner import: club step, recognized")
did = db.execute("SELECT id FROM opp_clubs WHERE name='SV Dietkirchen'").fetchone()[0]
g.update({"clubs_chosen": "1", "club_count": "1", "cn_0": "3200100", "cc_0": "opponent", "cv_0": str(did)})
post(a, "/import/gegner", g)
check(club_of("Zoe Zwei")[0] == "SV Dietkirchen" and db.execute("SELECT COUNT(*) FROM opp_teams WHERE club_id=? AND name='2'", (did,)).fetchone()[0] == 1, "gegner import to recognized club")
check(db.execute("SELECT COUNT(*) FROM opp_clubs WHERE name GLOB '[0-9]*'").fetchone()[0] == 0, "still no number-only clubs")
print("GEGNER OK", ok)

# ---------------- Massenaktionen und Filter
for n in ("Alt 1", "Alt 2", "Alt 3"):
    db.execute("INSERT INTO opp_clubs(name) VALUES(?)", (n,))
db.execute("INSERT INTO opp_clubs(name) VALUES('9900001')")
x = db.execute("SELECT id FROM opp_clubs WHERE name='Alt 1'").fetchone()[0]
db.execute("INSERT INTO opp_teams(id, club_id, name) VALUES(99, ?, '1')", (x,))
db.execute("INSERT INTO matches(id, team_id, date, opponent, opp_team_id) VALUES(9, 1, '2026-12-20', 'Alt 1 1', 99)")
db.execute("INSERT INTO opp_shooters(club_id, name) VALUES(?, 'Xaver')", (x,))
db.commit()
ids = lambda *names: [str(db.execute("SELECT id FROM opp_clubs WHERE name=?", (n,)).fetchone()[0]) for n in names]
b = a.get("/gegner").get_data(as_text=True)
check('form="opp-bulk"' in b and 'id="bulk-bar"' in b and "Zusammenführen in" in b, "bulk ui")
b = a.get("/gegner?zeige=nummer").get_data(as_text=True)
check("Nr. 9900001" in b and "Alt 2</b>" not in b, "filter nummer")
b = a.get("/gegner?zeige=ohne_mannschaft").get_data(as_text=True)
check("Alt 2</b>" in b and "Alt 1</b>" not in b and "SV Beerfurth</b>" not in b, "filter ohne mannschaft")
b = a.get("/gegner?zeige=ungenutzt").get_data(as_text=True)
check("Alt 1</b>" not in b and "Alt 3</b>" in b, "filter ungenutzt")
check("Alt 2</b>" in a.get("/gegner?zeige=boese").get_data(as_text=True), "bad filter -> all")
# Rechte und CSRF
check(post(e, "/gegner/massen", {"action": "loeschen", "ids": ids("Alt 2"), "confirm": "LÖSCHEN"}).status_code == 403, "shooter no bulk")
check(a.post("/gegner/massen", data={"action": "loeschen", "ids": ids("Alt 2"), "confirm": "LÖSCHEN"}).status_code == 400, "csrf")
# Löschen ohne Bestätigung: nichts passiert
r = post(a, "/gegner/massen", {"action": "loeschen", "ids": ids("Alt 2", "Alt 3"), "confirm": "ja"}, follow_redirects=True)
check("nichts gelöscht" in r.get_data(as_text=True) and db.execute("SELECT COUNT(*) FROM opp_clubs WHERE name IN ('Alt 2','Alt 3')").fetchone()[0] == 2, "delete needs confirm")
# Zusammenführen mehrerer (Ziel ist auch angehakt)
tgt = ids("Alt 3")[0]
r = post(a, "/gegner/massen", {"action": "zusammenfuehren", "ids": ids("Alt 1", "9900001", "Alt 3"), "target_id": tgt, "back": "/gegner?zeige=nummer"})
check(r.status_code == 302 and r.headers["Location"].endswith(f"/gegner?zeige=nummer#verein-{tgt}"), "merge redirect keeps filter")
check(db.execute("SELECT COUNT(*) FROM opp_clubs WHERE name IN ('Alt 1','9900001')").fetchone()[0] == 0, "merged away")
check(db.execute("SELECT club_nr FROM opp_clubs WHERE id=?", (tgt,)).fetchone()[0] == "9900001", "nr carried")
check(db.execute("SELECT c.name FROM matches m JOIN opp_teams t ON t.id=m.opp_team_id JOIN opp_clubs c ON c.id=t.club_id WHERE m.id=9").fetchone()[0] == "Alt 3", "match follows")
check(db.execute("SELECT c.name FROM opp_shooters s JOIN opp_clubs c ON c.id=s.club_id WHERE s.name='Xaver'").fetchone()[0] == "Alt 3", "shooter follows")
# ohne Ziel
r = post(a, "/gegner/massen", {"action": "zusammenfuehren", "ids": ids("Alt 2")}, follow_redirects=True)
check("Bitte wählen" in r.get_data(as_text=True), "merge needs target")
# Löschen mit Bestätigung, Wettkampf bleibt
r = post(a, "/gegner/massen", {"action": "loeschen", "ids": ids("Alt 2", "Alt 3") + ["99999", "abc"], "confirm": "löschen"}, follow_redirects=True)
check("2 Vereine gelöscht" in r.get_data(as_text=True), "bulk delete")
check(db.execute("SELECT opp_team_id FROM matches WHERE id=9").fetchone()[0] is None and db.execute("SELECT COUNT(*) FROM matches WHERE id=9").fetchone()[0] == 1, "match kept, link cleared")
check(post(a, "/gegner/massen", {"action": "loeschen", "back": "https://evil.example/"}).headers["Location"].startswith("/"), "safe back")
print("GEGNER MASSEN OK", ok)
