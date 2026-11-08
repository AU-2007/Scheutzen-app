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
post(a, "/verein", {"club_name": "SV Unterdorf"})
# --- Import der DISAG-Vorlage
raw = open("/mnt/user-data/attachments/df396d7c-17d2-5ad6-9363-025ceca018f6/76c78443-1793037843097_FLEX_SchuetzenMitVereinWaffenart.csv", "rb").read()
r = a.post("/import/schuetzen", data={"csrf": csrf(a), "csv": (io.BytesIO(raw), "flex.csv")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
for f in ("pass_nr", "nachname", "vorname", "club", "birth_year", "gender"):
    check(f'<option value="{f}" selected' in body, f"mapping {f}")
check(body.count('<option value="" selected') == 1, "Waffenart ignored")
data = _h.unescape(re.search(r'<textarea name="data" hidden>(.*?)</textarea>', body, re.S).group(1))
cols = {"col_0": "pass_nr", "col_1": "nachname", "col_2": "vorname", "col_3": "club", "col_4": "birth_year", "col_5": "", "col_6": "gender"}
r = post(a, "/import/schuetzen", dict(step="save", header="1", data=data, **cols))
body = r.get_data(as_text=True)
check("SV Unterdorf" in body and "KK Flachdach" in body, "club step")
names = re.findall(r'name="cn_(\d+)" value="([^"]*)"', body)
sel = {n: re.search(rf'name="cc_{i}".*?<option value="(\w+)" selected', body, re.S).group(1) for i, n in names}
check(sel["SV Unterdorf"] == "member" and sel["SV Altdorf"] == "skip", f"defaults {sel}")
form = dict(step="save", header="1", data=data, clubs_chosen="1", club_count=str(len(names)), **cols)
for i, n in names:
    form[f"cn_{i}"] = n; form[f"cc_{i}"] = "member" if n == "SV Unterdorf" else "opponent"
r = post(a, "/import/schuetzen", form)
check("Neue Zugänge" in r.get_data(as_text=True), "accounts listed")
u = db.execute("select name, pass_nr, birth_year, gender, club from users where name='Maria Landl'").fetchone()
check(u == ("Maria Landl", "4", "1940", "w", "SV Unterdorf"), f"user {u}")
check(db.execute("select count(*) from users where club='SV Unterdorf'").fetchone()[0] == 3, "3 members")
o = db.execute("select name, birth_year, gender, pass_nr from opp_shooters where name='Klaus Geier'").fetchone()
check(o == ("Klaus Geier", "1942", "m", "6"), f"opp {o}")
# --- Profil zeigt Felder, speichert
uid = db.execute("select id from users where name='Jens Jung'").fetchone()[0]
html = a.get(f"/benutzer/{uid}").get_data(as_text=True)
check('name="birth_year" value="1943"' in html and '<option value="m" selected' in html, "edit shows")
post(a, f"/benutzer/{uid}", {"name": "Jens Jung", "username": "jens.jung", "role": "shooter", "club": "SV Unterdorf", "pass_nr": "9", "birth_year": "Jg. 1944", "gender": "männlich"})
check(db.execute("select birth_year, gender from users where id=?", (uid,)).fetchone() == ("1944", "m"), "normalized")
# --- Mannschaft + Wettkampf + FLEX-Export
post(a, "/mannschaft/neu", {"name": "LG 1", "discipline": "Luftgewehr", "leader_id": "1"})
tid = db.execute("select id from teams").fetchone()[0]
ids = [r_[0] for r_ in db.execute("select id from users where club='SV Unterdorf' order by name")]
for i in ids: post(a, f"/mannschaft/{tid}/mitglieder", {"user_id": i})
r = a.get(f"/mannschaft/{tid}/disag-flex.csv")
txt = r.data.decode("cp1252")
check(txt.startswith("Ausweisnummer;Name;Vorname;Verein;Jahrgang;Waffenart;Geschlecht\r\n"), "header exact")
check("4;Landl;Maria;SV Unterdorf;1940;LG;w\r\n" in txt and "8;Förster;Sonja;SV Unterdorf;1942;LG;w" in txt, f"rows {txt}")
cid = db.execute("select id from opp_clubs where name='KK Flachdach'").fetchone()[0]
post(a, "/gegner", {"action": "team_add", "club_id": cid, "name": "1", "discipline": "Luftgewehr"})
ot = db.execute("select id from opp_teams where club_id=?", (cid,)).fetchone()[0]
db.execute("update opp_shooters set team_id=? where club_id=?", (ot, cid)); db.commit()
post(a, f"/mannschaft/{tid}/wettkampf/neu", {"date": "2026-11-20", "time": "19:00", "home": "1", "opp_team_id": ot})
mid = db.execute("select id from matches").fetchone()[0]
post(a, f"/wettkampf/{mid}/aufstellung", {"lineup": ids})
post(a, f"/wettkampf/{mid}/gegnerschuetzen", {"action": "team"})
post(a, f"/wettkampf/{mid}/gegnerschuetzen", {"action": "add", "name": "Ohne Nummer"})
post(a, f"/wettkampf/{mid}/standplan", {"action": "settings", "area": "LG", "lanes": "6"})
r = a.get(f"/wettkampf/{mid}/disag-flex.csv")
check(r.status_code == 200 and "FLEX_SchuetzenMitVereinWaffenart" in r.headers["Content-Disposition"], "match flex download")
lines = r.data.decode("cp1252").split("\r\n")
check(lines[0] == "Ausweisnummer;Name;Vorname;Verein;Jahrgang;Waffenart;Geschlecht" and len([l for l in lines if l]) == 7, f"lines {lines}")
check("6;Geier;Klaus;KK Flachdach;1942;LG;m" in lines and any(l.startswith("95") and ";Nummer;Ohne;KK Flachdach;;LG;" in l for l in lines), f"opp rows {lines}")
html = a.get(f"/wettkampf/{mid}/standplan").get_data(as_text=True)
check("FLEX-Datei herunterladen" in html and "Ohne Nummer" in html and "Ersatznummer" in html, "standplan hints")
check("DISAG-Datei" in a.get(f"/mannschaft/{tid}").get_data(as_text=True), "team button")
print("FLEX OK", ok)
