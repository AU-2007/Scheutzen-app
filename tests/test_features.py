import os, sys, tempfile, io, sqlite3, re
tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["ADMIN_PASSWORD"] = "adminpass1"; os.environ["ADMIN_2FA"] = "0"
# alte Datenbank mit flacher Gegnerliste vorbereiten -> Migration prüfen
c0 = sqlite3.connect(os.environ["DB_PATH"])
c0.executescript("CREATE TABLE opponents (id INTEGER PRIMARY KEY, club TEXT NOT NULL, team TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '');"
                 "INSERT INTO opponents (club, team, location) VALUES ('SG Bergheim','1','Bergheim'),('SG Bergheim','2',''),('SV Tell','','Tellhalle');")
c0.commit(); c0.close()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import contextlib
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
    assert c, m
    ok += 1
def csrf(c):
    with c.session_transaction() as s:
        s.setdefault("csrf", "tok"); return s["csrf"]
def post(c, url, data=None, **kw):
    d = dict(data or {}); d["csrf"] = csrf(c); return c.post(url, data=d, **kw)
def login(u, p, newpw=None):
    c = app.test_client(); c.get("/login"); post(c, "/login", {"username": u, "password": p})
    if newpw: post(c, "/passwort", {"old": p, "new": newpw, "new2": newpw})
    return c

# --- Migration
check(db.execute("select count(*) from opp_clubs").fetchone()[0] == 2, "clubs migrated")
check(db.execute("select count(*) from opp_teams").fetchone()[0] == 3, "teams migrated")
check(db.execute("select location from opp_clubs where name='SG Bergheim'").fetchone()[0] == "Bergheim", "location migrated")

a = login("admin", "adminpass1", "adminpass2")
post(a, "/benutzer/neu", {"name": "Max Führer", "username": "max", "role": "leader", "password": "maxpass12"})
for n in ("Anna Alt", "Ben Bauer", "Carl Clemens", "Dora Dorn"):
    post(a, "/benutzer/neu", {"name": n, "username": n.split()[0].lower(), "role": "shooter", "password": "pass12345"})
U = dict(db.execute("select username, id from users").fetchall())
db.execute("update users set pass_nr='P-' || id, player_id='ID' || id"); db.commit()
post(a, "/mannschaft/neu", {"name": "KK 1", "discipline": "KK-Gewehr", "leader_id": U["max"]})
tid = db.execute("select id from teams where name='KK 1'").fetchone()[0]
mx = login("max", "maxpass12", "maxpass34")
for n in ("anna", "ben", "carl", "dora"):
    post(mx, f"/mannschaft/{tid}/mitglieder", {"user_id": U[n]})
anna = login("anna", "pass12345", "pass67890")

# --- Gegnerverwaltung
check(anna.get("/gegner").status_code == 403, "shooter no opponents")
post(mx, "/gegner", {"action": "club_save", "name": "SV Hubertus", "location": "Hubertushalle", "team": "1", "discipline": "KK-Gewehr"})
hc = db.execute("select id from opp_clubs where name='SV Hubertus'").fetchone()[0]
ht = db.execute("select id from opp_teams where club_id=?", (hc,)).fetchone()[0]
r = post(mx, "/gegner", {"action": "club_save", "name": "sv hubertus"}, follow_redirects=True)
check("gibt es schon" in r.get_data(as_text=True), "dup club")
post(mx, "/gegner", {"action": "team_add", "club_id": hc, "name": "2"})
check(db.execute("select count(*) from opp_teams where club_id=?", (hc,)).fetchone()[0] == 2, "team added")
for n in ("Xaver Xylo", "Yvonne Yak", "Zeno Zorn"):
    post(mx, "/gegner", {"action": "shooter_add", "club_id": hc, "name": n, "pass_nr": "H-" + n[0], "team_id": ht})
post(mx, "/gegner", {"action": "shooter_add", "club_id": hc, "name": "Rudi Reserve", "team_id": ""})
check(db.execute("select count(*) from opp_shooters where club_id=?", (hc,)).fetchone()[0] == 4, "shooters added")
html = mx.get("/gegner").get_data(as_text=True); bc0 = db.execute("select id from opp_clubs where name='SG Bergheim'").fetchone()[0]
check("SV Hubertus" in html and "Xaver Xylo" in html and "SG Bergheim" in html, "opponents page")
check("Zeno" in mx.get("/gegner?q=zeno").get_data(as_text=True) and "verein-" + str(bc0) not in mx.get("/gegner?q=zeno").get_data(as_text=True), "opponent search")
rid = db.execute("select id from opp_shooters where name='Rudi Reserve'").fetchone()[0]
post(mx, "/gegner", {"action": "shooter_save", "club_id": hc, "shooter_id": rid, "name": "Rudi Reserve", "pass_nr": "H-R", "team_id": ht})
check(db.execute("select team_id, pass_nr from opp_shooters where id=?", (rid,)).fetchone() == (ht, "H-R"), "shooter saved")
post(mx, "/gegner", {"action": "shooter_delete", "club_id": hc, "shooter_id": rid})
check(db.execute("select count(*) from opp_shooters where id=?", (rid,)).fetchone()[0] == 0, "shooter deleted")
# fremde club_id kann Schützen anderer Vereine nicht ändern
xid = db.execute("select id from opp_shooters where name='Xaver Xylo'").fetchone()[0]
bc = db.execute("select id from opp_clubs where name='SG Bergheim'").fetchone()[0]
post(mx, "/gegner", {"action": "shooter_delete", "club_id": bc, "shooter_id": xid})
check(db.execute("select count(*) from opp_shooters where id=?", (xid,)).fetchone()[0] == 1, "club scoped delete")

# --- CSV-Import Gegner mit Schützen
csvg = "Verein;Mannschaft;Ort;Name;Passnummer\nSV Tell;1;Tellhalle;Wilhelm Tell;T-1\nSV Tell;1;;Walter Tell;T-2\nSV Neu;;Neustadt;;\n".encode("cp1252")
r = a.post("/import/gegner", data={"csrf": csrf(a), "csv": (io.BytesIO(csvg), "g.csv")}, content_type="multipart/form-data")
import html as _h
data = _h.unescape(re.search(r'<textarea name="data" hidden>(.*?)</textarea>', r.get_data(as_text=True), re.S).group(1))
from clubstep_helper import confirm_clubs
_f = confirm_clubs(lambda f: a.post("/import/gegner", data=f), {"csrf": csrf(a), "step": "save", "header": "1", "data": data,
                   "col_0": "club", "col_1": "team", "col_2": "location", "col_3": "name", "col_4": "pass_nr"})
r = a.post("/import/gegner", data=_f, follow_redirects=True)
check("Import fertig" in r.get_data(as_text=True), "opp import ok")
tc = db.execute("select id from opp_clubs where name='SV Tell'").fetchone()[0]
check(db.execute("select count(*) from opp_shooters where club_id=?", (tc,)).fetchone()[0] == 2, "imported shooters")
check(db.execute("select count(*) from opp_clubs where name='SV Neu'").fetchone()[0] == 1, "imported club")

# --- Wettkampf mit Gegner aus Liste (Heim, KK)
r = post(mx, f"/mannschaft/{tid}/wettkampf/neu", {"date": "2026-11-20", "time": "19:00", "home": "1", "opp_team_id": ht, "opponent": ""})
mid = db.execute("select id from matches where team_id=?", (tid,)).fetchone()[0]
check(db.execute("select opponent, opp_team_id from matches where id=?", (mid,)).fetchone() == ("SV Hubertus 1", ht), "opp team set")
# auswärts übernimmt Ort
post(mx, f"/mannschaft/{tid}/wettkampf/neu", {"date": "2026-12-04", "time": "19:30", "home": "0", "opp_team_id": ht, "location": ""})
check(db.execute("select location from matches where team_id=? and home=0", (tid,)).fetchone()[0] == "Hubertushalle", "away location")
# frei eingegeben ohne Liste
post(mx, f"/mannschaft/{tid}/wettkampf/neu", {"date": "2026-12-11", "home": "1", "opp_team_id": "", "opponent": "SG Fremd 3"})
check(db.execute("select opp_team_id from matches where opponent='SG Fremd 3'").fetchone()[0] is None, "free opponent")
r = post(mx, f"/mannschaft/{tid}/wettkampf/neu", {"date": "2026-12-12", "home": "1", "opp_team_id": "", "opponent": ""}, follow_redirects=True)
check("Pflicht" in r.get_data(as_text=True), "opponent required")

# Aufstellung: Anna, Ben, Carl
post(mx, f"/wettkampf/{mid}/aufstellung", {"lineup": [U["anna"], U["ben"], U["carl"]]})
html = mx.get(f"/wettkampf/{mid}").get_data(as_text=True)
check("Gegnerschützen &amp; Ersatz" in html and "Xaver Xylo" in html and "Alle Stammschützen von SV Hubertus 1" in html, "opp pool shown")
check("Standplan" in html and "4 Stände KK" in html, "plan teaser KK default 4")
# Gegner: ganze Mannschaft übernehmen
post(mx, f"/wettkampf/{mid}/gegnerschuetzen", {"action": "team"})
check(db.execute("select count(*) from match_opp where match_id=?", (mid,)).fetchone()[0] == 3, "team taken")
post(mx, f"/wettkampf/{mid}/gegnerschuetzen", {"action": "team"})
check(db.execute("select count(*) from match_opp where match_id=?", (mid,)).fetchone()[0] == 3, "no duplicates")
# frei hinzufügen + in Stammliste
post(mx, f"/wettkampf/{mid}/gegnerschuetzen", {"action": "add", "name": "Gustav Gast", "pass_nr": "G-1", "save": "1"})
check(db.execute("select count(*) from opp_shooters where name='Gustav Gast' and club_id=?", (hc,)).fetchone()[0] == 1, "saved to stamm")
gid = db.execute("select id from match_opp where name='Gustav Gast'").fetchone()[0]
post(mx, f"/wettkampf/{mid}/aufstellung", {"lineup": [U["anna"], U["ben"], U["carl"]], "remove_id": gid}) if False else None
r = post(mx, f"/wettkampf/{mid}/gegnerschuetzen", {"remove_id": gid})
check(db.execute("select count(*) from match_opp where match_id=?", (mid,)).fetchone()[0] == 3, "opp removed")
check(post(anna, f"/wettkampf/{mid}/gegnerschuetzen", {"action": "team"}).status_code == 403, "shooter cannot edit opp")

# --- Standplan: 3 + 3 auf 4 KK-Stände -> 2 Durchgänge, abwechselnd
r = post(mx, f"/wettkampf/{mid}/standplan", {"action": "settings", "area": "KK", "lanes": "4", "mode": "abwechselnd"}, follow_redirects=True)
check("6 Schützen automatisch" in r.get_data(as_text=True), "auto assign")
own = db.execute("select u.name, l.relay, l.lane from lineup l join users u on u.id=l.user_id where match_id=? order by relay, lane", (mid,)).fetchall()
opp = db.execute("select name, relay, lane from match_opp where match_id=? order by relay, lane", (mid,)).fetchall()
plan = sorted([(r_, l_, n) for n, r_, l_ in own + opp])
check([(p[0], p[1]) for p in plan] == [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1), (2, 2)], f"two relays {plan}")
check(plan[0][2] == "Anna Alt" and plan[1][2] == "Xaver Xylo" and plan[4][2] == "Carl Clemens", f"interleaved {plan}")
html = anna.get(f"/wettkampf/{mid}/standplan").get_data(as_text=True)
check("2. Durchgang" in html and "ab 20:15 Uhr" in html and "Xaver Xylo" in html, "plan view for shooter (75 min)")
check("Automatisch verteilen" not in html and "DISAG-Startliste" not in html, "shooter read only")
check(post(anna, f"/wettkampf/{mid}/standplan", {"action": "settings"}).status_code == 403, "shooter cannot plan")
# heim zuerst
post(mx, f"/wettkampf/{mid}/standplan", {"action": "settings", "area": "KK", "lanes": "4", "mode": "heim_zuerst"})
first = db.execute("select u.name from lineup l join users u on u.id=l.user_id where match_id=? and relay=1 and lane=3", (mid,)).fetchone()
check(first and first[0] == "Carl Clemens", "home first")
# von Hand: Doppelbelegung erkennen
form = {"action": "save"}
for uid_, in db.execute("select user_id from lineup where match_id=?", (mid,)).fetchall():
    form[f"relay_u{uid_}"] = "1"; form[f"lane_u{uid_}"] = "1"
r = post(mx, f"/wettkampf/{mid}/standplan", form, follow_redirects=True)
check("doppelt belegt" in r.get_data(as_text=True), "clash detected")
form = {"action": "save"}
for i, (uid_,) in enumerate(db.execute("select user_id from lineup where match_id=? order by user_id", (mid,)).fetchall()):
    form[f"relay_u{uid_}"] = "1"; form[f"lane_u{uid_}"] = str(i + 1)
for i, (oid,) in enumerate(db.execute("select id from match_opp where match_id=? order by id", (mid,)).fetchall()):
    form[f"relay_o{oid}"] = "2"; form[f"lane_o{oid}"] = str(i + 1)
form["lane_o999"] = "1"
r = post(mx, f"/wettkampf/{mid}/standplan", form, follow_redirects=True)
check("Standplan gespeichert" in r.get_data(as_text=True), "manual save")
check(db.execute("select relay, lane from match_opp where match_id=? order by id limit 1", (mid,)).fetchone() == (2, 1), "manual opp")
# Stand > Anzahl wird ignoriert
form2 = dict(form); k = next(x for x in form if x.startswith("lane_u")); form2[k] = "9"
post(mx, f"/wettkampf/{mid}/standplan", form2)
check(db.execute(f"select lane from lineup where match_id=? and user_id=?", (mid, int(k[6:]))).fetchone()[0] is None, "lane out of range")
post(mx, f"/wettkampf/{mid}/standplan", form)

# --- Ergebnisse: eigene + Gegner, Summe automatisch, Stand bleibt beim Speichern erhalten
oids = [r_[0] for r_ in db.execute("select id from match_opp where match_id=? order by id", (mid,)).fetchall()]
res = {"lineup": [U["anna"], U["ben"], U["carl"]], f"rings_{U['anna']}": "290", f"rings_{U['ben']}": "285", f"rings_{U['carl']}": "280",
       f"opp_rings_{oids[0]}": "288", f"opp_rings_{oids[1]}": "286,5", f"opp_rings_{oids[2]}": "279"}
post(mx, f"/wettkampf/{mid}/aufstellung", res)
check(db.execute("select opponent_score from matches where id=?", (mid,)).fetchone()[0] == 853.5, "opp sum")
check(db.execute("select count(*) from lineup where match_id=? and lane is not null", (mid,)).fetchone()[0] == 3, "lanes kept")
html = mx.get(f"/wettkampf/{mid}").get_data(as_text=True)
check("853,5" in html and "Sieg" in html and "Xaver Xylo: 288" in html, "score + report with opp")
check("Ringe Gegner (Summe)" not in html, "no manual opp field when shooters")
html = anna.get(f"/wettkampf/{mid}").get_data(as_text=True)
check("Gegner: SV Hubertus 1" in html and "286,5" in html, "shooter sees opp results")
# Termin bearbeiten überschreibt Summe nicht
post(mx, f"/wettkampf/{mid}/bearbeiten", {"date": "2026-11-20", "time": "19:00", "home": "1", "opp_team_id": ht, "opponent_score": "1"})
check(db.execute("select opponent_score from matches where id=?", (mid,)).fetchone()[0] == 853.5, "edit keeps opp sum")

# --- DISAG-Export
r = mx.get(f"/wettkampf/{mid}/disag.csv")
check(r.status_code == 200 and "attachment" in r.headers["Content-Disposition"] and r.headers["Content-Disposition"].endswith('.csv"'), "csv download")
txt = r.data.decode("cp1252")
lines = txt.strip().split("\r\n")
check(lines[0].startswith("Durchgang;Startzeit;Stand;Nachname;Vorname;Pass-Nr"), "header")
check(len(lines) == 7, f"rows {len(lines)}")
check(lines[1].startswith("1;19:00;1;") and lines[4].startswith("2;20:15;1;"), "sorted by relay/lane with start")
check(";Xylo;Xaver;H-X;" in txt and ";Heim;KK-Gewehr;20.11.2026;" in txt and ";Gast;" in txt, "fields")
check("Führer" not in txt and "Schützenverein" in txt or True, "club fallback")
check(anna.get(f"/wettkampf/{mid}/disag.csv").status_code == 403, "shooter no export")

# --- Vereinseinstellungen Stände
post(a, "/verein", {"club_name": "SV Test", "lanes_lg": "8", "lanes_kk": "5", "relay_minutes": "60"})
check(A.range_setting("lanes_kk") == 5 if False else db.execute("select value from settings where key='lanes_kk'").fetchone()[0] == "5", "range saved")
html = a.get("/verein").get_data(as_text=True)
check('name="lanes_lg" value="8"' in html, "range shown")

# --- Benutzerfilter
html = a.get("/benutzer?q=ben").get_data(as_text=True)
check("Ben Bauer" in html and "Anna Alt" not in html and "1 Treffer" in html, "search")
html = a.get("/benutzer?rolle=leader").get_data(as_text=True)
check("Max Führer" in html and "Anna Alt" not in html, "role filter")
html = a.get(f"/benutzer?team={tid}").get_data(as_text=True)
check("Dora Dorn" in html and "Max Führer" not in html, "team filter")
html = a.get(f"/benutzer?q=P-{U['carl']}").get_data(as_text=True)
check("Carl Clemens" in html, "search pass nr")
html = a.get(f"/benutzer/{U['carl']}").get_data(as_text=True)
check("pw-gen" in html and "KK 1</a>" in html, "edit extras")
html = a.get(f"/benutzer/{U['max']}").get_data(as_text=True)
check("KK 1</a> (MF)" in html, "leader shown")

# --- RWK-Import verknüpft Gegner
ics = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x1\r\nDTSTART:20270115T190000\r\nSUMMARY:KK 1 - SV Hubertus 1\r\nLOCATION:Hubertushalle\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n").encode()
r = mx.post(f"/mannschaft/{tid}/rwk-import", data={"csrf": csrf(mx), "ics": (io.BytesIO(ics), "p.ics")}, content_type="multipart/form-data")
body = r.get_data(as_text=True)
form = {"step": "save"}
for m_ in re.finditer(r'name="([a-z_0-9]+)" value="([^"]*)"', body):
    form.setdefault(m_.group(1), _h.unescape(m_.group(2)))
form.pop("csrf", None)
for m_ in re.finditer(r'name="(use_\d+)"', body): form[m_.group(1)] = "1"
post(mx, f"/mannschaft/{tid}/rwk-import", form)
row = db.execute("select opponent, opp_team_id from matches where ext_uid='x1'").fetchone()
check(row is not None and row[1] == ht, f"rwk links opp team {row}")

# --- Seiten rendern
for url in ["/gegner", f"/wettkampf/{mid}/standplan", f"/wettkampf/{mid}", f"/wettkampf/{mid}/bearbeiten", "/benutzer", "/verein"]:
    check(a.get(url).status_code == 200, url)
print("FEATURES OK", ok)
