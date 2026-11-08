import os, sys, tempfile, io, contextlib, sqlite3
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
db.execute("UPDATE users SET must_change_pw=0")
U = {}
for n in ("Anna", "Bert", "Carl", "Dora", "Emil"):
    U[n] = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES(?,?,?, 'shooter',1,0)",
                      (n, n.lower(), "x")).lastrowid
T = {}
for name, disc in (("LG 1", "LG"), ("LG 2", "LG"), ("LG 3", "LG"), ("KK 1", "KK")):
    T[name] = db.execute("INSERT INTO teams(name, discipline, season) VALUES(?,?, '2026/27')", (name, disc)).lastrowid
T["LG 2 alt"] = db.execute("INSERT INTO teams(name, discipline, season) VALUES('LG 2', 'LG', '2025/26')").lastrowid
day = [0]
def match(team, rel=0, d=None):
    day[0] += 1
    return db.execute("INSERT INTO matches(team_id, date, time, opponent, relegation) VALUES(?,?, '19:00', 'X', ?)",
                      (T[team], d or f"2026-10-{day[0]:02d}", rel)).lastrowid
def start(team, who, rel=0, d=None):
    mid = match(team, rel, d)
    for w in who: db.execute("INSERT INTO lineup(match_id, user_id) VALUES(?,?)", (mid, U[w]))
    return mid
# Anna: 3x LG 1 -> an LG 1 gebunden
for _ in range(3): start("LG 1", ["Anna"])
# Bert: 2x LG 1 + 1x LG 2 -> für LG 3 an LG 2 gebunden (VII.3), für LG 2 frei
start("LG 1", ["Bert"]); start("LG 1", ["Bert"]); start("LG 2", ["Bert"])
# Carl: 10 Einsätze in LG 3 (+ Relegation, KK, alte Saison zählen nicht)
for _ in range(10): start("LG 3", ["Carl"])
start("LG 3", ["Carl"], rel=1); start("KK 1", ["Carl"]); start("LG 2 alt", ["Carl"], d="2026-03-01")
# Dora: 2x LG 1 -> noch frei
start("LG 1", ["Dora"]); start("LG 1", ["Dora"])
db.commit()
get_t = lambda n: db.execute("SELECT * FROM teams WHERE id=?", (T[n],)).fetchone()
chk = lambda team, d="2026-11-30": A.rwk_check(get_t(team), d, list(U.values()))
with app.app_context():
    r = chk("LG 2")
    check(r[U["Anna"]]["bound"] == "LG 1" and r[U["Anna"]]["problems"], "Anna bound for LG 2")
    check(not chk("LG 1")[U["Anna"]]["problems"], "Anna fine in LG 1")
    check(not r[U["Bert"]]["problems"], "Bert: 2 higher for LG 2 -> fine")
    r3 = chk("LG 3")
    check(r3[U["Bert"]]["bound"] == "LG 2", "Bert bound to lowest higher class LG 2 (VII.3)")
    check(r3[U["Carl"]]["starts"] == 10 and any("10 Einsätze" in p for p in r3[U["Carl"]]["problems"]), "Carl: 11th start flagged; relegation/KK/old season not counted")
    check(chk("KK 1")[U["Carl"]]["starts"] == 1, "KK separate")
    check(not r[U["Dora"]]["problems"] and r[U["Dora"]]["starts"] == 2, "Dora 2x higher fine")
    check(not chk("LG 2", "2026-10-02")[U["Anna"]]["problems"], "only earlier starts count (chronological)")
    # 12er-Gruppe
    db.execute("UPDATE teams SET max_starts=12 WHERE id=?", (T["LG 3"],)); db.commit()
    check(not chk("LG 3")[U["Carl"]]["problems"], "12 allowed in 7-team group")
    # Rang manuell: LG 2 ist höher als LG 1
    db.execute("UPDATE teams SET rank=2 WHERE id=?", (T["LG 1"],)); db.execute("UPDATE teams SET rank=1 WHERE id=?", (T["LG 2"],)); db.commit()
    check(not chk("LG 2")[U["Anna"]]["problems"] and chk("LG 1")[U["Bert"]]["problems"] == [], "manual rank respected")
    db.execute("UPDATE teams SET rank=NULL"); db.commit()
    # Saison aus Datum, wenn keine eingetragen
    check(A.season_of({"season": ""}, "2026-11-05") == "2026/27" and A.season_of({"season": ""}, "2027-03-01") == "2026/27"
          and A.season_of({"season": ""}, "2026-08-01") == "2026/27", "season from date")
    check(A.team_rank({"rank": None, "name": "LG 2. Mannschaft"}) == 2 and A.team_rank({"rank": None, "name": "Jugend"}) == 99, "rank from name")

# Web: Wettkampf in LG 2, Anna aufstellen -> Warnung
a = login("admin", "adminpass1")
mid = match("LG 2", d="2026-11-20"); db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (T["LG 2"], U["Dora"])); db.commit()
post(a, f"/wettkampf/{mid}/aushilfe", {"user_id": U["Anna"]})
r = post(a, f"/wettkampf/{mid}/aufstellung", {"lineup": [U["Anna"], U["Dora"]]}, follow_redirects=True)
b = r.get_data(as_text=True)
check("RWK-Ordnung VII" in b and "an LG 1 gebunden" in b and "gestrichen" in b, "warning on save")
check("2/10 Einsätze" in b, "starts shown for Dora")
body = a.get(f"/wettkampf/{mid}").get_data(as_text=True)
check(body.count("an LG 1 gebunden") >= 2, "persistent warning + row pill")
# Relegation: keine Prüfung
r = post(a, f"/wettkampf/{mid}/bearbeiten", {"date": "2026-11-20", "time": "19:00", "opponent": "X", "home": "1", "relegation": "1"}, follow_redirects=True)
check(db.execute("SELECT relegation FROM matches WHERE id=?", (mid,)).fetchone()[0] == 1, "relegation saved")
body = a.get(f"/wettkampf/{mid}").get_data(as_text=True)
check("RWK-Ordnung VII" not in body and "zählt nicht" in body, "relegation not checked")
# Mannschaft bearbeiten: Rang und max. Einsätze
post(a, f"/mannschaft/{T['LG 3']}/bearbeiten", {"name": "LG 3", "discipline": "LG", "season": "2026/27", "rank": "3", "max_starts": "12"})
row = db.execute("SELECT rank, max_starts FROM teams WHERE id=?", (T["LG 3"],)).fetchone()
check(row["rank"] == 3 and row["max_starts"] == 12, "team rank/max saved")
post(a, f"/mannschaft/{T['LG 3']}/bearbeiten", {"name": "LG 3", "discipline": "LG", "rank": "999", "max_starts": "50"})
row = db.execute("SELECT rank, max_starts FROM teams WHERE id=?", (T["LG 3"],)).fetchone()
check(row["rank"] is None and row["max_starts"] == 10, "invalid values sanitized")
# Mannschaftsseite: Saison-Spalte
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (T["LG 3"], U["Bert"])); db.commit()
b = a.get(f"/mannschaft/{T['LG 3']}").get_data(as_text=True)
check("an LG 2 gebunden" in b and "3/10" in b, "team page overview")

# ---------------- Überschreiben (pro Saison) und Neustart jede Saison
from datetime import date as _d
season_now = A.season_of(None, _d.today().isoformat())
b = a.get(f"/mannschaft/{T['LG 2']}").get_data(as_text=True)
check("Einsätze &amp; Bindung" in b and f"Saison {season_now}" in b and "Anpassen" in b, "season section")
def ov(uid, **f):
    return post(a, f"/mannschaft/{T['LG 2']}/einsaetze/{uid}", f, follow_redirects=True)
def now_check(team, who):
    with app.app_context():
        return A.rwk_check(get_t(team), _d.today().isoformat(), [U[who]])[U[who]]
# Für heutige Saison echte Einsätze anlegen (unabhängig vom Testdatum)
today = _d.today().isoformat()
for _ in range(3):
    mm = db.execute("INSERT INTO matches(team_id, date, time, opponent) VALUES(?, ?, '08:00', 'X')", (T["LG 1"], today)).lastrowid
    db.execute("INSERT INTO lineup(match_id, user_id) VALUES(?,?)", (mm, U["Emil"]))
db.commit()
db.execute("UPDATE matches SET date=date(?, '-1 day') WHERE date=?", (today, today)); db.commit()
check(now_check("LG 2", "Emil")["bound"] == "LG 1", "Emil auto-bound")
ov(U["Emil"], bind_mode="frei", extra_starts="0", note="Sondergenehmigung")
r = now_check("LG 2", "Emil")
check(not r["problems"] and r["override"]["note"] == "Sondergenehmigung", "override: no binding")
ov(U["Emil"], bind_mode="auto", extra_starts="7")
r = now_check("LG 2", "Emil")
check(r["starts"] == 10 and any("10 Einsätze" in p for p in r["problems"]), "extra starts count toward limit")
ov(U["Emil"], bind_mode="auto", extra_starts="-3")
check(now_check("LG 2", "Emil")["starts"] == 0, "negative correction, not below 0")
ov(U["Dora"], bind_mode="team", bound_team_id=str(T["LG 1"]), extra_starts="0")
check(any("von Hand" in p for p in now_check("LG 2", "Dora")["problems"]), "manual binding to LG 1")
check(not now_check("LG 1", "Dora")["problems"], "manual binding: LG 1 itself fine")
r = ov(U["Dora"], bind_mode="team", bound_team_id=str(T["KK 1"]))
check("Bitte die Mannschaft" in r.get_data(as_text=True), "bound team must be same discipline")
ov(U["Dora"], reset="1")
check(now_check("LG 2", "Dora")["override"] is None, "reset to automatic")
ov(U["Emil"], extra_starts="999")
check(db.execute("SELECT extra_starts FROM rwk_overrides WHERE user_id=?", (U["Emil"],)).fetchone()[0] == 30, "extra clamped")
# Rechte
sh = login("anna", "x")
db.execute("UPDATE users SET password_hash=? WHERE id=?", (A.generate_password_hash("passwort12"), U["Anna"])); db.commit()
sh = login("anna", "passwort12")
check(post(sh, f"/mannschaft/{T['LG 2']}/einsaetze/{U['Emil']}", {"extra_starts": "5"}).status_code == 403, "shooter cannot override")
check(post(a, f"/mannschaft/{T['LG 2']}/einsaetze/99999", {"extra_starts": "5"}).status_code == 404, "unknown user")
# Overrides gelten nur in ihrer Saison
with app.app_context():
    nxt = f"{int(season_now[:4]) + 1}-10-15"
    r = A.rwk_check(get_t("LG 2"), nxt, [U["Emil"]])[U["Emil"]]
    check(r["override"] is None and r["starts"] == 0 and not r["problems"], "new season: counts and overrides reset")
    # alter Saisoneintrag der Mannschaft verhindert den Neustart nicht
    db.execute("UPDATE teams SET season='2026/27'"); db.commit()
    check(A.rwk_check(get_t("LG 2"), "2027-10-15", [U["Anna"]])[U["Anna"]]["starts"] == 0, "stale team season field ignored")
print("RWK-REGELN OK", ok)
