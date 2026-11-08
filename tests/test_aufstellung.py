import os, sys, tempfile, io, contextlib, sqlite3, re
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
tid = db.execute("INSERT INTO teams(name, discipline, season) VALUES('LG 1','Luftgewehr','2026/27')").lastrowid
U = {}
for n in ("Anna", "Bert", "Carl", "Dora"):
    U[n] = db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES(?,?,?, 'shooter',1,0)",
                      (n, n.lower(), A.generate_password_hash("passwort12"))).lastrowid
    db.execute("INSERT INTO team_members(team_id, user_id) VALUES(?,?)", (tid, U[n]))
mid = db.execute("INSERT INTO matches(team_id, date, time, opponent) VALUES(?, '2099-01-10', '19:00', 'X')", (tid,)).lastrowid
db.commit()
a = login("admin", "adminpass1")
b = a.get(f"/wettkampf/{mid}").get_data(as_text=True)
check("Noch niemand aufgestellt" in b and b.count('name="lineup"') == 4 and 'id="weitere"' not in b, "nobody lined: all shown")
post(a, f"/wettkampf/{mid}/aufstellung", {"lineup": [U["Anna"], U["Bert"]]})
b = a.get(f"/wettkampf/{mid}").get_data(as_text=True)
main, _, rest = b.partition('id="weitere"')
check(main.count('name="lineup"') == 2 and "Anna" in main and "Carl" not in main.split('id="aufstellung"')[1], "only lined in main table")
check(rest.count('name="lineup"') == 2 and "2 nicht aufgestellt" in rest, "others in fold")
# Nachaufstellen über die aufgeklappte Liste, Ringe bleiben
post(a, f"/wettkampf/{mid}/aufstellung", {"lineup": [U["Anna"], U["Bert"], U["Carl"]], f"rings_{U['Anna']}": "380"})
n = db.execute("SELECT COUNT(*) FROM lineup WHERE match_id=?", (mid,)).fetchone()[0]
check(n == 3, "add from fold")
# Schützenansicht
s = login("dora", "passwort12")
b = s.get(f"/wettkampf/{mid}").get_data(as_text=True)
sec = b.split('id="aufstellung"')[1].split("</section>")[0]
top, _, fold = sec.partition("<details")
check("Anna" in top and "Carl" in top and "Dora" not in top and "Dora" in fold and "Weitere Schützen" in fold, "shooter view only lined")
c = login("carl", "passwort12")
check('pill gold">du' in c.get(f"/wettkampf/{mid}").get_data(as_text=True), "own marker")
print("AUFSTELLUNG OK", ok)
