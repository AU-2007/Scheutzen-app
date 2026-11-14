"""Tempo: Browser-Cache für statische Dateien, WAL, Termine nur aktuelle Saison, breite Seiten am PC."""
import os, sys, tempfile, io, contextlib, sqlite3, re
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
db = sqlite3.connect(tmp + "/t.db")
check(db.execute("PRAGMA journal_mode").fetchone()[0] == "wal", "WAL")
c = app.test_client(); b = c.get("/login").get_data(as_text=True)
url = re.search(r'href="(/static/style\.css\?v=[0-9a-f]{10})"', b)
check(url, "versioned css url")
r = c.get(url.group(1))
check(r.status_code == 200 and "max-age=31536000" in r.headers["Cache-Control"], "long cache for static")
check("no-store" in c.get("/login").headers["Cache-Control"], "pages not cached")
check(c.get("/sw.js").headers["Cache-Control"] == "no-cache", "service worker not cached long")
# Termine: Vergangen nur diese Saison
db.execute("UPDATE users SET must_change_pw=0")
u = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES('Jens','jens',?, 'shooter',1,0)",
               (A.generate_password_hash("passwort12"),)).lastrowid
t = db.execute("INSERT INTO teams(name, discipline) VALUES('LG 1','LG')").lastrowid
db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (t, u))
today = date.today(); start = date(int(A.season_of(None, today.isoformat())[:4]), 8, 1)
db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00','Alte Saison',1)", (t, (start - timedelta(days=30)).isoformat()))
if start < today:
    db.execute("INSERT INTO matches(team_id, date, time, opponent, home) VALUES(?,?,'19:00','Diese Saison',1)", (t, start.isoformat()))
db.commit()
s = app.test_client(); s.get("/login")
with s.session_transaction() as ss: ss["csrf"] = "t"
s.post("/login", data={"username": "jens", "password": "passwort12", "csrf": "t"})
b = s.get("/termine").get_data(as_text=True)
check("Alte Saison" not in b and ("Diese Saison" in b or start >= today), "past list limited to current season")
check('class="wrap wide"' in b and 'top-inner wide' in b and "sched-cols" in b, "wide layout on Termine")
check('class="wrap "' in s.get("/profil").get_data(as_text=True), "forms stay narrow")
print("TEMPO OK", ok)
