import os, sys, tempfile, io, contextlib, sqlite3, re, html as _h
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
def order(v):
    db.execute("INSERT INTO settings(key, value) VALUES('name_order', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (v,)); db.commit()

with app.test_request_context():
    # Standard: Nachname Vorname
    check(A.name_order() == "nachname", "default nachname")
    check(A.split_name("Urbschat Anton") == ("Anton", "Urbschat"), "split surname-first")
    check(A.split_name("Müller Jens Peter") == ("Jens Peter", "Müller"), "two first names")
    check(A.split_name("Müller, Jens") == ("Jens", "Müller"), "comma")
    check(A.join_name("Jens", "Müller") == "Müller Jens", "join surname-first")
    check(A.tidy_name("Müller,  Jens") == ("Müller Jens", "Jens"), "tidy comma -> scheme")
    check(A.first_name_of({"first_name": "", "name": "Urbschat Anton"}) == "Anton", "greeting name surname-first")
    order("vorname")
    check(A.split_name("Jens Peter Müller") == ("Jens Peter", "Müller") and A.join_name("Jens", "Müller") == "Jens Müller", "first-name-first scheme")
    check(A.first_name_of({"first_name": "", "name": "Anton Urbschat"}) == "Anton", "greeting name first-first")
    order("nachname")

db.execute("UPDATE users SET must_change_pw=0, name='Urbschat Anton' WHERE username='admin'")
db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw, birth_year, gender, pass_nr) VALUES('Müller Jens','jens',?, 'shooter',1,0,'1990','m','P1')", (A.generate_password_hash("passwort12"),))
db.commit()
a = login("admin", "adminpass1")
body = a.get("/").get_data(as_text=True)
check("Hallo, Anton" in body and ">Anton</span>" in body, "greeting first name with surname-first scheme")
check("Hallo, Jens" in login("jens", "passwort12").get("/").get_data(as_text=True), "shooter greeting")
# Vereinsdaten: Auswahl
b = a.get("/verein").get_data(as_text=True)
check('name="name_order"' in b and 'value="nachname" selected' in b, "club setting shown")
post(a, "/verein", {"club_name": "SV Eisenbach", "name_order": "vorname"})
check(db.execute("SELECT value FROM settings WHERE key='name_order'").fetchone()[0] == "vorname", "setting saved")
check("Hallo, Urbschat" in a.get("/").get_data(as_text=True), "switching scheme changes reading")
post(a, "/verein", {"club_name": "SV Eisenbach", "name_order": "boese"})
check(db.execute("SELECT value FROM settings WHERE key='name_order'").fetchone()[0] == "nachname", "invalid -> nachname")
# DISAG-FLEX: Name/Vorname richtig getrennt
with app.test_request_context():
    data = A.flex_csv([{"ausweis": "P1", "name": "Müller Jens", "club": "SV", "birth_year": "1990", "gender": "m"}], "LG").decode("cp1252")
check("P1;Müller;Jens;SV;1990;LG;m" in data, "FLEX Name;Vorname correct: " + data)
# Benutzer bearbeiten: Komma-Name ins Vereinsformat
uid = db.execute("SELECT id FROM users WHERE username='jens'").fetchone()[0]
post(a, f"/benutzer/{uid}", {"name": "Müller, Jens", "username": "jens", "role": "shooter", "first_name": ""})
check(db.execute("SELECT name FROM users WHERE id=?", (uid,)).fetchone()[0] == "Müller Jens", "comma -> surname-first stored")
check("Nachname Vorname" in a.get(f"/benutzer/{uid}").get_data(as_text=True), "form label follows scheme")
# Import mit getrennten Spalten -> Nachname Vorname
csv = "Vorname;Nachname\r\nKarl;Schmidt\r\n"
with a.session_transaction() as s: s["csrf"] = "t"
r = a.post("/import/schuetzen", data={"csrf": "t", "csv": (io.BytesIO(csv.encode()), "s.csv")}, content_type="multipart/form-data")
data = _h.unescape(re.search(r'<textarea name="data" hidden>(.*?)</textarea>', r.get_data(as_text=True), re.S).group(1))
post(a, "/import/schuetzen", {"step": "save", "header": "1", "data": data, "col_0": "vorname", "col_1": "nachname"})
check(db.execute("SELECT COUNT(*) FROM users WHERE name='Schmidt Karl'").fetchone()[0] == 1, "import builds 'Schmidt Karl'")
# Tauschen bleibt für Ausreißer
ids = [str(r[0]) for r in db.execute("SELECT id FROM users WHERE username='jens'")]
post(a, "/benutzer/massen", {"action": "namen_tauschen", "ids": ids})
check(db.execute("SELECT name FROM users WHERE username='jens'").fetchone()[0] == "Jens Müller", "swap still available")
print("VORNAME OK", ok)
