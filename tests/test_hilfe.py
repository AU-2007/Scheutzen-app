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
db = sqlite3.connect(tmp + "/t.db")
for role in ("leader", "trainer", "shooter"):
    db.execute("INSERT INTO users(name, username, password_hash, role, active, must_change_pw) VALUES(?,?,?,?,1,0)", (role, role, A.generate_password_hash("passwort12"), role))
db.execute("UPDATE users SET must_change_pw=0"); db.commit()
check(app.test_client().get("/anleitungen").status_code in (302, 401), "login needed")
pages = {r: login(r, "passwort12").get("/anleitungen") for r in ("leader", "trainer", "shooter")}
pages["admin"] = login("admin", "adminpass1").get("/anleitungen")
for r, p in pages.items():
    check(p.status_code == 200, r)
b = {r: p.get_data(as_text=True) for r, p in pages.items()}
check("Zu einem Termin zu- oder absagen" in b["shooter"] and "Synchronisieren" in b["shooter"], "all: basics")
for r in ("shooter",):
    check("Mannschaft &amp; Wettkampf" not in b[r] and "Wartung &amp; Server" not in b[r] and "Training ausfallen" not in b[r], "shooter sees only basics")
check("Training ausfallen lassen" in b["trainer"] and "Feste Trainingszeiten" in b["trainer"] and "Aufstellung" not in b["trainer"] and "Wartungsseite" not in b["trainer"], "trainer")
check("Aufstellung festlegen" in b["leader"] and "Training ausfallen lassen" in b["leader"] and "Feste Trainingszeiten" not in b["leader"], "leader")
check("Neue Mannschaft anlegen" not in b["leader"] and "Wartungsseite" not in b["leader"] and "status.sh" not in b["leader"], "leader no admin")
check(all(x in b["admin"] for x in ("Neue Mannschaft anlegen", "Wartungsseite", "status.sh", "Benutzer anlegen", "TRUST_PROXY")), "admin all")
check('href="/anleitungen"' in b["shooter"], "menu link")
print("HILFE OK", ok)
