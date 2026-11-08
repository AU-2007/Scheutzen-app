import os, sys, tempfile, io, sqlite3, re, contextlib, html as _h
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = os.path.join(tmp, "t.db"); os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
# Diese älteren Tests nutzen Namen im Format "Vorname Nachname"
import sqlite3 as _sq; _c = _sq.connect(os.environ["DB_PATH"])
_c.execute("INSERT INTO settings(key, value) VALUES('name_order','vorname') ON CONFLICT(key) DO UPDATE SET value='vorname'"); _c.commit(); _c.close()
app = A.app; app.config["TESTING"] = True
db = sqlite3.connect(os.environ["DB_PATH"])
ok = 0
def check(c, m):
    global ok
    assert c, m; ok += 1
def csrf(c):
    with c.session_transaction() as s:
        s.setdefault("csrf", "tok"); return s["csrf"]
def post(c, url, data=None, **kw):
    d = dict(data or {}); d["csrf"] = csrf(c); return c.post(url, data=d, **kw)
a = app.test_client(); a.get("/login"); post(a, "/login", {"username": "admin", "password": "adminpass1"})
post(a, "/passwort", {"old": "adminpass1", "new": "adminpass2", "new2": "adminpass2"})
post(a, "/verein", {"club_name": "SV Eisnebach"})
# --- Import mit Vereinswahl
csvs = ("Nachname;Vorname;Passnummer;Verein\n"
        "Müller;Hans;P1;SV Eisnebach\nMaier;Eva;P2;SV Eisnebach\nHuber;Xaver;P3;SG Bergheim\nGraf;Yvonne;P4;SG Bergheim\nWirth;Zeno;P5;SV Tell\nOhne;Verein;P6;\n").encode("cp1252")
r = a.post("/import/schuetzen", data={"csrf": csrf(a), "csv": (io.BytesIO(csvs), "s.csv")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
data = _h.unescape(re.search(r'<textarea name="data" hidden>(.*?)</textarea>', body, re.S).group(1))
r = post(a, "/import/schuetzen", {"step": "save", "header": "1", "data": data, "col_0": "nachname", "col_1": "vorname", "col_2": "pass_nr", "col_3": "club"})
body = r.get_data(as_text=True)
check("Übernehmen als" in body and "SG Bergheim" in body and "(ohne Verein)" in body, "club step shown")
names = re.findall(r'name="cn_(\d+)" value="([^"]*)"', body)
sel = {n: re.search(rf'name="cc_{i}".*?<option value="(\w+)" selected', body, re.S).group(1) for i, n in names}
check(sel["SV Eisnebach"] == "member" and sel["SG Bergheim"] == "skip" and sel[""] == "member", f"defaults {sel}")
form = {"step": "save", "header": "1", "data": data, "col_0": "nachname", "col_1": "vorname", "col_2": "pass_nr", "col_3": "club",
        "clubs_chosen": "1", "club_count": str(len(names))}
for i, n in names:
    form[f"cn_{i}"] = _h.unescape(n); form[f"cc_{i}"] = {"SV Eisnebach": "member", "SG Bergheim": "opponent", "SV Tell": "skip", "": "member"}[_h.unescape(n)]
r = post(a, "/import/schuetzen", form, follow_redirects=True)
body = r.get_data(as_text=True)
check("2 als Gegnerschützen" in body and "1 übersprungen" in body, "import summary")
users = [x[0] for x in db.execute("select name from users order by name")]
check("Hans Müller" in users and "Eva Maier" in users and "Verein Ohne" in users and "Xaver Huber" not in users and "Zeno Wirth" not in users, f"members {users}")
check(db.execute("select count(*) from opp_shooters s join opp_clubs c on c.id=s.club_id where c.name='SG Bergheim'").fetchone()[0] == 2, "opp shooters")
# ohne Vereinsspalte: direkter Import
csv2 = "Name\nPeter Neu\n".encode()
r = a.post("/import/schuetzen", data={"csrf": csrf(a), "csv": (io.BytesIO(csv2), "s.csv")}, content_type="multipart/form-data")
data = _h.unescape(re.search(r'<textarea name="data" hidden>(.*?)</textarea>', r.get_data(as_text=True), re.S).group(1))
r = post(a, "/import/schuetzen", {"step": "save", "header": "1", "data": data, "col_0": "name"})
check("Peter Neu" in r.get_data(as_text=True), "no club column direct")

# --- Filter Verein / nie angemeldet
html = a.get("/benutzer?verein=SV+Eisnebach").get_data(as_text=True)
check("Hans Müller" in html and "Verein Ohne" not in html and "Peter Neu" not in html, "club filter")
html = a.get("/benutzer?verein=-").get_data(as_text=True)
check("Verein Ohne" in html and "Hans Müller" not in html, "no club filter")
html = a.get("/benutzer?nie=1").get_data(as_text=True)
check("Hans Müller" in html and 'href="/benutzer/1"' not in html, "never filter")
check('id="sel-all"' in html and 'name="ids"' in html, "checkboxes")

# --- Massenaktionen
ids = [str(x[0]) for x in db.execute("select id from users where name in ('Hans Müller','Eva Maier')")]
post(a, "/benutzer/massen", {"ids": ids, "action": "sperren", "back": "/benutzer?verein=SV+Eisnebach"})
check(db.execute("select count(*) from users where active=0").fetchone()[0] == 2, "bulk lock")
post(a, "/benutzer/massen", {"ids": ids, "action": "entsperren"})
check(db.execute("select count(*) from users where active=0").fetchone()[0] == 0, "bulk unlock")
post(a, "/benutzer/massen", {"ids": ids, "action": "rolle_leader"})
check(db.execute("select count(*) from users where role='leader'").fetchone()[0] == 2, "bulk role")
r = post(a, "/benutzer/massen", {"ids": ids, "action": "loeschen", "confirm": "nein"}, follow_redirects=True)
check("nichts gelöscht" in r.get_data(as_text=True) and db.execute("select count(*) from users").fetchone()[0] == 5, "delete needs confirm")
r = post(a, "/benutzer/massen", {"ids": ids + ["1"], "action": "loeschen", "confirm": "löschen", "back": "//evil.com"})
check(r.location.endswith("/benutzer") and "evil" not in r.location, "safe back")
check(db.execute("select count(*) from users").fetchone()[0] == 3 and db.execute("select count(*) from users where id=1").fetchone()[0] == 1, "bulk delete, self kept")
r = post(a, "/benutzer/massen", {"ids": ["1"], "action": "sperren"}, follow_redirects=True)
check(db.execute("select active from users where id=1").fetchone()[0] == 1, "cannot lock self")
r = post(a, "/benutzer/massen", {"ids": ["2"], "action": "drop"}, follow_redirects=True)
check("Unbekannte Aktion" in r.get_data(as_text=True), "bad action")
# Schütze darf nicht
uid = db.execute("select id, username from users where id<>1 limit 1").fetchone()
s = app.test_client(); s.get("/login")
check(post(s, "/benutzer/massen", {"ids": ["1"], "action": "loeschen", "confirm": "LÖSCHEN"}).status_code in (302, 403), "anon blocked")
check(db.execute("select count(*) from users where id=1").fetchone()[0] == 1, "admin still there")
print("BULK OK", ok)
