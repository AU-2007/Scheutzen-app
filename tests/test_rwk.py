import os, sys, tempfile, io, sqlite3, re, contextlib, html as _h
tmp = tempfile.mkdtemp(); os.environ["DB_PATH"] = os.path.join(tmp, "t.db"); os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
with contextlib.redirect_stdout(io.StringIO()):
    import app as A
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
post(a, "/mannschaft/neu", {"name": "LG 3", "discipline": "Luftgewehr", "leader_id": "1"})
tid = db.execute("select id from teams").fetchone()[0]
raw = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "rwk_bereichsklasse_a_nord.ics"), "rb").read()
r = a.post(f"/mannschaft/{tid}/rwk-import", data={"csrf": csrf(a), "rwk_name": "SV Eisnebach 3", "ics": (io.BytesIO(raw), "p.ics")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
check("Eure Mannschaft: <b>SV Eisenbach 3</b>" in body and "erkannt aus" in body, "fuzzy own")
check("15 Termine anderer Mannschaften ausgeblendet" in body and "6 Termine." in body, "filtered")
check("Bereichsklasse A Nord · Luftgewehr 2026/2027" in body, "class info")
check("6 Gegner in die Gegnerliste" in body, "new opps offered")
rows = re.findall(r'name="row" value="(\d+)"', body)
form = {"step": "save", "rwk_name": "SV Eisenbach 3", "add_opps": "1", "row": rows,
        "ics_text": _h.unescape(re.search(r'<textarea name="ics_text" hidden>(.*?)</textarea>', body, re.S).group(1))}
for i in rows:
    for k in ("uid", "date", "time", "loc", "opp"):
        m_ = re.search(rf'name="{k}_{i}" value="([^"]*)"', body); form[f"{k}_{i}"] = _h.unescape(m_.group(1))
    form[f"home_{i}"] = re.search(rf'name="home_{i}">.*?<option value="(\d)" selected', body, re.S).group(1)
    form[f"use_{i}"] = "1"
r = post(a, f"/mannschaft/{tid}/rwk-import", form, follow_redirects=True)
check("6 neu" in r.get_data(as_text=True) and "6 Gegner" in r.get_data(as_text=True), "saved")
ms = db.execute("select date, time, opponent, home, location, opp_team_id from matches order by date").fetchall()
check(len(ms) == 6, "6 matches")
check(ms[0][:4] == ("2026-09-13", "10:00", "SV Oberzeuzheim 1", 0), f"first {ms[0]}")
check(ms[1][:4] == ("2026-09-27", "11:00", "SV Dietkirchen 1", 1) and ms[1][4] == "Am Stotz, 65618 Selters-Eisenbach", f"home {ms[1]}")
check(all(m[5] for m in ms), "all linked to opp teams")
check(db.execute("select location from opp_clubs where name='SV Oberzeuzheim'").fetchone()[0] == "Am Schützenhaus, 65589 Hadamar Oberzeuzheim", "opp location (tab cleaned)")
check(db.execute("select t.name, t.discipline from opp_teams t join opp_clubs c on c.id=t.club_id where c.name='SV Oberbrechen'").fetchone() == ("2", "Luftgewehr"), "opp team 2")
check(db.execute("select count(*) from opp_clubs where name like '%Eisenbach%'").fetchone()[0] == 0, "own not opponent")
check(db.execute("select league, season, rwk_name from teams").fetchone() == ("Bereichsklasse A Nord", "2026/2027", "SV Eisenbach 3"), "team meta")
# Erneuter Import: aktualisiert, keine Doppelten
r = a.post(f"/mannschaft/{tid}/rwk-import", data={"csrf": csrf(a), "rwk_name": "SV Eisenbach 3", "ics": (io.BytesIO(raw), "p.ics")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
check(body.count("schon vorhanden") == 6 and "Gegner in die Gegnerliste" not in body, "re-import")
# Unbekannter Name: Auswahl
r = a.post(f"/mannschaft/{tid}/rwk-import", data={"csrf": csrf(a), "rwk_name": "Bayern München", "ics": (io.BytesIO(raw), "p.ics")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
check("steht nicht in diesem Wettkampfplan" in body and ">SV Wolfenhausen 1</option>" in body and "21 Termine." in body, "unknown own")
txt = _h.unescape(re.search(r'<textarea name="ics_text" hidden>(.*?)</textarea>', body, re.S).group(1))
r = post(a, f"/mannschaft/{tid}/rwk-import", {"step": "preview", "ics_text": txt, "rwk_name": "SV Wolfenhausen 1"})
body = r.get_data(as_text=True)
check("Eure Mannschaft: <b>SV Wolfenhausen 1</b>" in body and "Termine." in body, "pick other team")
print("RWK OK", ok)

# --- Mannschaftsnummer muss stimmen
names = ["SV Diana Ennerich 1", "SV Dietkirchen 1", "SV Eisenbach 3", "SV Oberbrechen 2"]
check(A.resolve_own("SV Eisenbach 2", names) is None, "nr mismatch")
check(A.resolve_own("SV Eisnebach 3", names) == "SV Eisenbach 3", "typo same nr")
check(A.resolve_own("sv eisenbach 3", names) == "SV Eisenbach 3", "case")
check(A.resolve_own("SV Eisenbach", names) is None, "no nr, ambiguous with numbered list")
check(A.resolve_own("SV Oberbrechen 1", names) is None, "other nr")
post(a, "/mannschaft/neu", {"name": "SV Eisenbach 2", "discipline": "Luftgewehr", "leader_id": "1"})
t2 = db.execute("select id from teams where name='SV Eisenbach 2'").fetchone()[0]
r = a.post(f"/mannschaft/{t2}/rwk-import", data={"csrf": csrf(a), "ics": (io.BytesIO(raw), "p.ics")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
check("steht nicht in diesem Wettkampfplan" in body and "21 Termine." in body, "eisenbach 2 not matched")
# Falsch importierte Termine entfernen
db.execute("insert into matches (team_id, date, opponent, ext_uid) values (?, '2026-12-01', 'X', 'u1'), (?, '2026-12-02', 'Y', 'u2'), (?, '2026-12-03', 'Hand', NULL)", (t2, t2, t2))
db.execute("update teams set rwk_name='SV Eisenbach 3' where id=?", (t2,)); db.commit()
mid_x = db.execute("select id from matches where ext_uid='u2'").fetchone()[0]
db.execute("insert into match_opp (match_id, name, rings) values (?, 'G', 300)", (mid_x,)); db.commit()
html = a.get(f"/mannschaft/{t2}/rwk-import").get_data(as_text=True)
check("1 importierte Termin dieser Mannschaft hat" in html, f"remove offered")
post(a, f"/mannschaft/{t2}/rwk-import/entfernen", {})
check(db.execute("select count(*) from matches where team_id=?", (t2,)).fetchone()[0] == 3, "needs confirm")
post(a, f"/mannschaft/{t2}/rwk-import/entfernen", {"confirm": "1"})
left = [x[0] for x in db.execute("select opponent from matches where team_id=? order by opponent", (t2,))]
check(left == ["Hand", "Y"], f"removed only imported without results {left}")
check(db.execute("select rwk_name from teams where id=?", (t2,)).fetchone()[0] == "", "rwk name reset")
check(db.execute("select count(*) from matches where team_id=?", (tid,)).fetchone()[0] == 6, "other team untouched")
print("RWK2 OK", ok)
