"""Mannschaftsverwaltung für Schützenvereine.

Rollen:
  admin   – Vorstand/Admin: verwaltet Benutzer, Mannschaften, alles
  leader  – Mannschaftsführer: verwaltet die eigenen Mannschaften
  shooter – Schütze: sieht Mannschaften/Termine, sagt zu/ab
Zusätzlich kann der Admin pro Mannschaft weitere Bearbeiter eintragen,
die dieselben Rechte wie der Mannschaftsführer dieser Mannschaft haben.
"""
import calendar
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "data", "verein.db"))
VEREIN_NAME = os.environ.get("VEREIN_NAME", "Schützenverein")

# Akzentfarben: Name -> (Bezeichnung, hell, hell 2, dunkel, dunkel 2)
ACCENTS = {
    "gruen": ("Vereinsgrün", "#1f4d2e", "#2d6b41", "#3f8f5a", "#4ea56a"),
    "blau": ("Blau", "#1e3f66", "#2b5c8a", "#4f86bf", "#6ea5d8"),
    "rot": ("Bordeaux", "#6b1f2a", "#8f2d3a", "#c25a67", "#d9737f"),
    "lila": ("Lila", "#43306b", "#5b4391", "#8a72c4", "#a28bd8"),
    "grau": ("Anthrazit", "#2b2f33", "#454b51", "#7d868f", "#98a1aa"),
}
THEMES = {"auto": "Automatisch", "light": "Hell", "dark": "Dunkel"}
ROLES = {"admin": "Admin", "leader": "Mannschaftsführer", "trainer": "Trainer", "shooter": "Schütze"}
AVAIL = {"ja": "Zusage", "vielleicht": "Vielleicht", "nein": "Absage"}
DISCIPLINES = ["Luftgewehr", "Luftgewehr Auflage", "Luftpistole", "Luftpistole Auflage",
               "KK-Gewehr", "KK-Pistole", "Großkaliber", "Bogen"]

def load_secret_key():
    """SECRET_KEY aus der Umgebung, sonst einmalig erzeugen und im Datenordner ablegen."""
    key = os.environ.get("SECRET_KEY", "")
    if key and key != "bitte-aendern":
        return key
    path = os.path.join(os.path.dirname(DB_PATH), ".secret_key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "x") as fh:
            fh.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    with open(path) as fh:
        return fh.read().strip()


app = Flask(__name__)
STARTED_AT = datetime.now()
LOG_KEEP_DAYS = 90          # Login- und Fehlerprotokoll so lange aufheben
MAX_FAILED_LOGINS = 10      # pro IP in 10 Minuten
if os.environ.get("TRUST_PROXY") == "1":   # hinter Reverse Proxy / Cloudflare: echte Client-IP verwenden
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # Uploads (Bilder, Import-Dateien) bis 10 MB
)
DUMMY_HASH = generate_password_hash(secrets.token_hex(8))

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
       "base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@app.after_request
def security_headers(resp):
    h = resp.headers
    h.setdefault("Content-Security-Policy", CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "same-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if app.config["SESSION_COOKIE_SECURE"]:
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    # Seiten mit persönlichen Daten nicht im Browser-/Proxy-Cache ablegen
    if request.endpoint not in ("static", "avatar") and "Cache-Control" not in h:
        h["Cache-Control"] = "no-store"
    h.pop("Server", None)
    return resp


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'shooter',
  active INTEGER NOT NULL DEFAULT 1,
  must_change_pw INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS teams (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  discipline TEXT NOT NULL DEFAULT '',
  league TEXT NOT NULL DEFAULT '',
  season TEXT NOT NULL DEFAULT '',
  leader_id INTEGER REFERENCES users(id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS team_members (
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (team_id, user_id)
);
CREATE TABLE IF NOT EXISTS team_editors (
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (team_id, user_id)
);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY,
  team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  time TEXT NOT NULL DEFAULT '',
  opponent TEXT NOT NULL,
  home INTEGER NOT NULL DEFAULT 1,
  location TEXT NOT NULL DEFAULT '',
  opponent_score REAL,
  notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS availability (
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  PRIMARY KEY (match_id, user_id)
);
CREATE TABLE IF NOT EXISTS lineup (
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  rings REAL,
  PRIMARY KEY (match_id, user_id)
);
CREATE TABLE IF NOT EXISTS rides (
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode TEXT NOT NULL,              -- fahrer | mitfahrer | selbst
  seats INTEGER NOT NULL DEFAULT 0, -- freie Plätze (nur fahrer)
  driver_id INTEGER REFERENCES users(id) ON DELETE SET NULL, -- gewünschter Fahrer (nur mitfahrer)
  PRIMARY KEY (match_id, user_id)
);
CREATE TABLE IF NOT EXISTS training_slots (   -- feste wöchentliche Trainingszeit
  id INTEGER PRIMARY KEY,
  weekday INTEGER NOT NULL,                  -- 0 = Montag … 6 = Sonntag
  start TEXT NOT NULL,
  end TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT 'Training',
  lanes INTEGER NOT NULL DEFAULT 6,          -- Anzahl Stände
  location TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS training_rsvp (    -- Rückmeldung zum Training
  slot_id INTEGER NOT NULL REFERENCES training_slots(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status TEXT NOT NULL,                      -- ja | spaeter | nein
  PRIMARY KEY (slot_id, date, user_id)
);
CREATE TABLE IF NOT EXISTS groups (             -- Benutzergruppen fürs Training, z. B. Jugend, Erwachsene
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS user_groups (
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, group_id)
);
CREATE TABLE IF NOT EXISTS slot_groups (        -- Training nur für diese Gruppen; keine Zeile = für alle
  slot_id INTEGER NOT NULL REFERENCES training_slots(id) ON DELETE CASCADE,
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  PRIMARY KEY (slot_id, group_id)
);
CREATE TABLE IF NOT EXISTS match_moves (        -- Verlegungen von Wettkämpfen
  id INTEGER PRIMARY KEY,
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  old_date TEXT NOT NULL, old_time TEXT NOT NULL DEFAULT '',
  new_date TEXT NOT NULL, new_time TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'offen',          -- offen | bestaetigt | abgelehnt | zurueckgezogen
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE TABLE IF NOT EXISTS login_log (          -- Anmeldungen (erfolgreich und fehlgeschlagen)
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  username TEXT NOT NULL DEFAULT '',
  ip TEXT NOT NULL DEFAULT '',
  agent TEXT NOT NULL DEFAULT '',
  ok INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS error_log (          -- Serverfehler
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT '',
  path TEXT NOT NULL DEFAULT '',
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  message TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (           -- Vereinsdaten (nur Admin)
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS opponents (          -- Gegner-Vereine und -Mannschaften
  id INTEGER PRIMARY KEY,
  club TEXT NOT NULL,
  team TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS training_cancel (
  slot_id INTEGER NOT NULL REFERENCES training_slots(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (slot_id, date)
);
"""
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
TRAINING_WEEKS = 4      # so weit im Voraus werden Trainings angezeigt
BOOK_DAYS_AHEAD = 60    # so weit im Voraus darf zugesagt werden
TR_STATUS = {"ja": "Zusage", "spaeter": "Komme später", "nein": "Absage"}

# Spalten, die in späteren Versionen dazugekommen sind (für bestehende Datenbanken)
MIGRATIONS = [
    ("team_members", "reserve", "INTEGER NOT NULL DEFAULT 0"),
    ("matches", "meet", "TEXT NOT NULL DEFAULT ''"),
    ("matches", "ext_uid", "TEXT"),                       # UID aus dem RWK-Kalenderimport
    ("teams", "rwk_url", "TEXT NOT NULL DEFAULT ''"),     # Link zur Liga im RWK-Onlinemelder
    ("teams", "rwk_name", "TEXT NOT NULL DEFAULT ''"),    # Name der Mannschaft im RWK-Onlinemelder
    ("teams", "ext_id", "TEXT NOT NULL DEFAULT ''"),      # Mannschafts-ID (Verband/RWK)
    ("training_slots", "once_date", "TEXT"),              # gesetzt = einmaliges Sondertraining
    ("training_slots", "note", "TEXT NOT NULL DEFAULT ''"),
    ("training_slots", "area1", "TEXT NOT NULL DEFAULT 'LG'"),   # Name Bereich 1 (Stände: lanes)
    ("training_slots", "area2", "TEXT NOT NULL DEFAULT 'KK'"),   # Name Bereich 2
    ("training_slots", "lanes2", "INTEGER NOT NULL DEFAULT 0"),  # Stände Bereich 2, 0 = kein zweiter Bereich
    ("training_rsvp", "area", "INTEGER"),                        # 1/2 = Bereich, NULL = egal / noch nicht zugeteilt
    ("users", "player_id", "TEXT NOT NULL DEFAULT ''"),   # Spieler-ID (z. B. aus DISAG/Verband)
    ("users", "pass_nr", "TEXT NOT NULL DEFAULT ''"),     # Schützenpass-Nummer
    ("users", "club", "TEXT NOT NULL DEFAULT ''"),        # Verein, falls Gastschütze
    ("users", "theme", "TEXT NOT NULL DEFAULT 'auto'"),   # auto | light | dark
    ("users", "accent", "TEXT NOT NULL DEFAULT 'gruen'"),
    ("users", "avatar", "INTEGER NOT NULL DEFAULT 0"),    # 1 = Profilbild vorhanden
    ("users", "last_seen", "TEXT"),                        # letzte Aktivität
    ("users", "last_login", "TEXT"),
    ("users", "pw_ver", "INTEGER NOT NULL DEFAULT 0"),     # erhöht bei Passwortwechsel -> alte Sitzungen ungültig
    ("users", "lock_reason", "TEXT NOT NULL DEFAULT ''"),  # Grund der Sperre (nur für Admin sichtbar)
    ("users", "locked_at", "TEXT"),
]
RIDE_MODES = {"fahrer": "Ich fahre", "mitfahrer": "Brauche Mitfahrt", "selbst": "Eigene Anreise"}


# ---------------------------------------------------------------- Datenbank
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def q(sql, args=(), one=False):
    cur = db().execute(sql, args)
    rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def ex(sql, args=()):
    cur = db().execute(sql, args)
    db().commit()
    return cur.lastrowid


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        user = os.environ.get("ADMIN_USER", "admin")
        pw = os.environ.get("ADMIN_PASSWORD", "")
        generated = not pw or pw in ("admin", "bitte-aendern") or len(pw) < 8
        if generated:
            pw = secrets.token_urlsafe(12)
        conn.execute(
            "INSERT INTO users (username, name, password_hash, role, must_change_pw) VALUES (?,?,?,?,1)",
            (user, "Administrator", generate_password_hash(pw), "admin"))
        conn.commit()
        print(f"[init] Admin-Benutzer '{user}' angelegt – bitte nach dem ersten Login Passwort ändern.", flush=True)
        if generated:
            print(f"[init] Kein sicheres ADMIN_PASSWORD gesetzt. Erzeugtes Startpasswort: {pw}", flush=True)
    conn.close()


# ---------------------------------------------------------------- Hilfen
def current_user():
    if "uid" not in session:
        return None
    if "user" not in g:
        u = q("SELECT * FROM users WHERE id=? AND active=1", (session["uid"],), one=True)
        if u and session.get("pw_ver", 0) != u["pw_ver"]:
            u = None            # Passwort wurde geändert -> diese Sitzung gilt nicht mehr
        g.user = u
    return g.user


def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        u = current_user()
        if not u:
            session.clear()
            return redirect(url_for("login", next=request.path))
        if u["must_change_pw"] and request.endpoint not in ("password", "logout"):
            flash("Bitte lege zuerst ein eigenes Passwort fest.", "info")
            return redirect(url_for("password"))
        return f(*a, **kw)
    return wrapper


def admin_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if current_user()["role"] != "admin":
            abort(403)
        return f(*a, **kw)
    return wrapper


def safe_next(url, fallback):
    """Nur Weiterleitungen innerhalb der App zulassen (Schutz vor Open Redirect)."""
    url = (url or "").strip()
    if not url.startswith("/") or url.startswith("//") or "\\" in url or any(ord(c) < 32 for c in url):
        return fallback
    parts = urlsplit(url)
    if parts.scheme or parts.netloc:
        return fallback
    return url


def setting(key, default=""):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def can_manage(team):
    """Admin, Mannschaftsführer der Mannschaft oder eingetragener Bearbeiter."""
    u = current_user()
    if not (u and team):
        return False
    if u["role"] == "admin" or team["leader_id"] == u["id"]:
        return True
    return q("SELECT 1 FROM team_editors WHERE team_id=? AND user_id=?", (team["id"], u["id"]), one=True) is not None


def is_member(team_id, user_id):
    return q("SELECT 1 FROM team_members WHERE team_id=? AND user_id=?", (team_id, user_id), one=True) is not None


def is_participant(m, user_id):
    """Mitglied der Mannschaft oder als Aushilfe für diesen Wettkampf aufgestellt."""
    return is_member(m["team_id"], user_id) or \
        q("SELECT 1 FROM lineup WHERE match_id=? AND user_id=?", (m["id"], user_id), one=True) is not None


def get_team(tid):
    t = q("""SELECT t.*, u.name AS leader_name FROM teams t
             LEFT JOIN users u ON u.id = t.leader_id WHERE t.id=?""", (tid,), one=True)
    if not t:
        abort(404)
    return t


def get_match(mid):
    m = q("SELECT * FROM matches WHERE id=?", (mid,), one=True)
    if not m:
        abort(404)
    return m


def parse_num(val):
    val = (val or "").strip().replace(",", ".")
    if val == "":
        return None
    try:
        n = float(val)
    except ValueError:
        return None
    return n if n >= 0 else None


def parse_date(val):
    try:
        return datetime.strptime(val, "%Y-%m-%d").date().isoformat()
    except (ValueError, TypeError):
        return None


@app.template_filter("datum")
def fmt_date(iso):
    try:
        d = datetime.strptime(iso, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return iso
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][d.weekday()]
    return f"{wd}, {d:%d.%m.%Y}"


@app.template_filter("kw")
def fmt_week(iso):
    """'Diese Woche', 'Nächste Woche' oder 'KW 46 · 09.11.–15.11.'"""
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    mon = d - timedelta(days=d.weekday())
    this_mon = date.today() - timedelta(days=date.today().weekday())
    if mon == this_mon:
        return "Diese Woche"
    if mon == this_mon + timedelta(weeks=1):
        return "Nächste Woche"
    sun = mon + timedelta(days=6)
    return f"KW {d.isocalendar()[1]} · {mon:%d.%m.}–{sun:%d.%m.}"


@app.template_filter("ringe")
def fmt_rings(v):
    if v is None:
        return "–"
    if float(v).is_integer():
        return str(int(v))
    return f"{v:.1f}".replace(".", ",")


def match_total(mid):
    r = q("SELECT SUM(rings) s, COUNT(rings) c FROM lineup WHERE match_id=?", (mid,), one=True)
    return r["s"] if r["c"] else None


def match_outcome(m, total):
    if total is None or m["opponent_score"] is None:
        return None
    if total > m["opponent_score"]:
        return "sieg"
    if total < m["opponent_score"]:
        return "niederlage"
    return "unentschieden"


MY_TEAMS_SQL = """SELECT team_id FROM team_members WHERE user_id=:u
                  UNION SELECT team_id FROM team_editors WHERE user_id=:u
                  UNION SELECT id FROM teams WHERE leader_id=:u"""


def matches_for(user_id, all_teams=False, date_from=None, date_to=None):
    """Wettkämpfe mit Mannschaft, eigener Rückmeldung und Aufstellung des Benutzers."""
    where, args = [], {"u": user_id}
    if not all_teams:
        where.append(f"(t.id IN ({MY_TEAMS_SQL}) OR m.id IN (SELECT match_id FROM lineup WHERE user_id=:u))")
    if date_from:
        where.append("m.date >= :df"); args["df"] = date_from
    if date_to:
        where.append("m.date <= :dt"); args["dt"] = date_to
    return q(f"""SELECT m.*, t.name team_name, a.status my_status,
                   (SELECT 1 FROM lineup l WHERE l.match_id=m.id AND l.user_id=:u) in_lineup,
                   (EXISTS (SELECT 1 FROM team_members x WHERE x.team_id=t.id AND x.user_id=:u)
                    OR EXISTS (SELECT 1 FROM lineup y WHERE y.match_id=m.id AND y.user_id=:u)) is_member,
                   (SELECT r.mode FROM rides r WHERE r.match_id=m.id AND r.user_id=:u) my_ride,
                   (SELECT mv.new_date FROM match_moves mv WHERE mv.match_id=m.id AND mv.status='offen') move_date,
                   (SELECT mv.new_time FROM match_moves mv WHERE mv.match_id=m.id AND mv.status='offen') move_time
                 FROM matches m JOIN teams t ON t.id=m.team_id
                 LEFT JOIN availability a ON a.match_id=m.id AND a.user_id=:u
                 {'WHERE ' + ' AND '.join(where) if where else ''}
                 ORDER BY m.date, m.time""", args)


# ---------------------------------------------------------------- CSRF
@app.before_request
def track_activity():
    uid = session.get("uid")
    if not uid or request.endpoint == "static":
        return
    last = session.get("seen_at", 0)
    now = datetime.now()
    if now.timestamp() - last > 60:
        try:
            ex("UPDATE users SET last_seen=? WHERE id=?", (now.isoformat(timespec="seconds"), uid))
            session["seen_at"] = now.timestamp()
        except sqlite3.Error:
            pass


@app.before_request
def csrf_protect():
    if request.method == "POST":
        token = session.get("csrf")
        if not token or not secrets.compare_digest(token, request.form.get("csrf", "")):
            abort(400, "Ungültiges Formular-Token – bitte Seite neu laden.")


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    me_ = current_user()
    acc = ACCENTS.get(me_["accent"] if me_ else "gruen", ACCENTS["gruen"])
    return dict(csrf=session["csrf"], me=me_, ROLES=ROLES, AVAIL=AVAIL, ACC=acc,
                VEREIN=setting("club_name") or VEREIN_NAME, today=date.today().isoformat(), RWK_HOME=RWK_HOME)


# ---------------------------------------------------------------- Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or ""
        since = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        name = request.form.get("username", "").strip()
        fails = q("SELECT COUNT(*) n FROM login_log WHERE ok=0 AND ip=? AND at>=?", (ip, since), one=True)["n"]
        fails_user = q("SELECT COUNT(*) n FROM login_log WHERE ok=0 AND username=? COLLATE NOCASE AND at>=?",
                       (name[:80], since), one=True)["n"]
        if fails >= MAX_FAILED_LOGINS or fails_user >= MAX_FAILED_LOGINS:
            flash("Zu viele Fehlversuche. Bitte in 10 Minuten noch einmal versuchen.", "error")
            return render_template("login.html"), 429
        u = q("SELECT * FROM users WHERE username=?", (name,), one=True)
        # Immer einen Hash prüfen, damit die Antwortzeit nicht verrät, ob es den Benutzer gibt
        pw_ok = check_password_hash(u["password_hash"] if u else DUMMY_HASH, request.form.get("password", "")) and bool(u)
        locked = pw_ok and not u["active"]
        ok = pw_ok and not locked
        now = datetime.now().isoformat(timespec="seconds")
        ex("INSERT INTO login_log (at, user_id, username, ip, agent, ok) VALUES (?,?,?,?,?,?)",
           (now, u["id"] if ok else None, name[:80], ip, (request.user_agent.string or "")[:250], 1 if ok else 0))
        if ok:
            ex("UPDATE users SET last_login=?, last_seen=? WHERE id=?", (now, now, u["id"]))
            ex("DELETE FROM login_log WHERE at < ?", ((datetime.now() - timedelta(days=LOG_KEEP_DAYS)).isoformat(),))
            session.clear()
            session.permanent = True
            session["uid"] = u["id"]
            session["pw_ver"] = u["pw_ver"]
            return redirect(safe_next(request.args.get("next"), url_for("index")))
        if locked:   # nur wer das richtige Passwort kennt, erfährt von der Sperre
            flash("Dein Zugang ist gesperrt. Bitte wende dich an den Admin.", "error")
        else:
            flash("Benutzername oder Passwort falsch.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/passwort", methods=["GET", "POST"])
@login_required
def password():
    """Erzwungene Passwortänderung beim ersten Login. Sonst: Profilseite."""
    u = current_user()
    if request.method == "GET" and not u["must_change_pw"]:
        return redirect(url_for("profile") + "#passwort")
    if request.method == "POST":
        old, new, new2 = (request.form.get(k, "") for k in ("old", "new", "new2"))
        if not check_password_hash(u["password_hash"], old):
            flash("Aktuelles Passwort ist falsch.", "error")
        elif len(new) < 8:
            flash("Das neue Passwort muss mindestens 8 Zeichen haben.", "error")
        elif new != new2:
            flash("Die neuen Passwörter stimmen nicht überein.", "error")
        else:
            ex("UPDATE users SET password_hash=?, must_change_pw=0, pw_ver=pw_ver+1 WHERE id=?",
               (generate_password_hash(new), u["id"]))
            session["pw_ver"] = u["pw_ver"] + 1   # diese Sitzung bleibt, alle anderen werden abgemeldet
            flash("Passwort geändert.", "ok")
            return redirect(url_for("index") if u["must_change_pw"] else url_for("profile"))
    if not u["must_change_pw"]:
        return redirect(url_for("profile") + "#passwort")
    return render_template("password.html")


# ---------------------------------------------------------------- Profil
AVATAR_DIR = os.path.join(os.path.dirname(DB_PATH), "avatars")


def user_stats(uid):
    """Kleine Statistik für die Profilseite."""
    today = date.today().isoformat()
    year_ago = (date.today() - timedelta(days=365)).isoformat()
    tr = q("""SELECT COUNT(*) n FROM training_rsvp r
              WHERE r.user_id=? AND r.status IN ('ja','spaeter') AND r.date < ? AND r.date >= ?
              AND NOT EXISTS (SELECT 1 FROM training_cancel c WHERE c.slot_id=r.slot_id AND c.date=r.date)""",
           (uid, today, year_ago), one=True)["n"]
    res = q("""SELECT l.rings, m.date, m.opponent FROM lineup l JOIN matches m ON m.id=l.match_id
               WHERE l.user_id=? AND l.rings IS NOT NULL ORDER BY m.date""", (uid,))
    rings = [r["rings"] for r in res]
    last = res[-10:]
    lo = min((r["rings"] for r in last), default=0)
    hi = max((r["rings"] for r in last), default=0)
    bars = [dict(r, pct=40 + (60 * (r["rings"] - lo) / (hi - lo) if hi > lo else 60)) for r in last]
    nm = q("SELECT COUNT(*) n FROM lineup l JOIN matches m ON m.id=l.match_id WHERE l.user_id=? AND m.date < ?",
           (uid, today), one=True)["n"]
    return {"trainings": tr, "matches": nm, "avg": sum(rings) / len(rings) if rings else None,
            "best": max(rings) if rings else None, "bars": bars}


@app.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    u = current_user()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "look":
            theme = request.form.get("theme") if request.form.get("theme") in THEMES else "auto"
            accent = request.form.get("accent") if request.form.get("accent") in ACCENTS else "gruen"
            ex("UPDATE users SET theme=?, accent=? WHERE id=?", (theme, accent, u["id"]))
            flash("Darstellung gespeichert.", "ok")
        elif action == "avatar":
            f = request.files.get("avatar")
            try:
                from PIL import Image, ImageOps
                Image.MAX_IMAGE_PIXELS = 25_000_000      # Schutz vor "Dekompressionsbomben"
                img = Image.open(f.stream)
                if img.format not in ("JPEG", "PNG", "WEBP", "GIF", "HEIF", "MPO"):
                    raise ValueError("Bildformat")
                img.draft("RGB", (640, 640))             # große JPEGs gleich verkleinert laden
                img = ImageOps.exif_transpose(img).convert("RGB")
                img = ImageOps.fit(img, (320, 320))
                os.makedirs(AVATAR_DIR, exist_ok=True)
                img.save(os.path.join(AVATAR_DIR, f"{u['id']}.jpg"), "JPEG", quality=85)
                ex("UPDATE users SET avatar=avatar+1 WHERE id=?", (u["id"],))
                flash("Profilbild gespeichert.", "ok")
            except Exception:
                flash("Das Bild konnte nicht gelesen werden. Bitte ein JPG oder PNG wählen.", "error")
        elif action == "avatar_delete":
            try:
                os.remove(os.path.join(AVATAR_DIR, f"{u['id']}.jpg"))
            except FileNotFoundError:
                pass
            ex("UPDATE users SET avatar=0 WHERE id=?", (u["id"],))
            flash("Profilbild entfernt.", "ok")
        return redirect(url_for("profile"))
    teams = q(f"SELECT name FROM teams WHERE id IN ({MY_TEAMS_SQL}) ORDER BY name", {"u": u["id"]})
    return render_template("profile.html", stats=user_stats(u["id"]), teams=teams, ACCENTS=ACCENTS, THEMES=THEMES)


@app.route("/avatar/<int:uid>")
@login_required
def avatar(uid):
    from flask import send_from_directory
    path = os.path.join(AVATAR_DIR, f"{uid}.jpg")
    if not os.path.exists(path):
        abort(404)
    resp = send_from_directory(AVATAR_DIR, f"{uid}.jpg", mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


# ---------------------------------------------------------------- Übersicht
@app.route("/")
@login_required
def index():
    u = current_user()
    if u["role"] == "admin":
        return admin_dashboard()
    my_teams = q(f"""SELECT t.*, t.leader_id = :u is_leader FROM teams t
                     WHERE t.id IN ({MY_TEAMS_SQL}) ORDER BY t.name""", {"u": u["id"]})
    my_results = q("""SELECT m.*, t.name team_name, l.rings FROM lineup l
                      JOIN matches m ON m.id=l.match_id JOIN teams t ON t.id=m.team_id
                      WHERE l.user_id=? AND l.rings IS NOT NULL ORDER BY m.date DESC LIMIT 5""", (u["id"],))

    # Agenda: Wettkämpfe und Trainings dieser Woche in einer Liste
    today = date.today()
    horizon = today + timedelta(days=6 - today.weekday())   # bis Sonntag dieser Woche
    items, days = agenda(u["id"], today, horizon, only_mine=True)
    pending = sum(1 for it in items if it["kind"] == "match" and it["is_member"] and not it["my_status"]) + \
        sum(1 for it in items if it["kind"] == "training" and it["for_me"] and not it["cancelled"] and not it["my_status"])
    later = matches_for(u["id"], date_from=(horizon + timedelta(days=1)).isoformat())
    return render_template("index.html", my_teams=my_teams, days=days, pending=pending,
                           n_matches=sum(1 for it in items if it["kind"] == "match"),
                           n_trainings=sum(1 for it in items if it["kind"] == "training" and it["for_me"] and not it["cancelled"]),
                           next_later=later[0] if later else None, my_results=my_results)


def _is_open(it, past=False):
    """Noch keine eigene Rückmeldung (nur für kommende Termine sinnvoll)."""
    if past:
        return False
    if it["kind"] == "match":
        return bool(it["is_member"]) and not it["my_status"]
    return bool(it["for_me"]) and not it["cancelled"] and not it["my_status"]


AGENDA_FILTERS = {
    "alle": ("Alle", lambda it, past=False: True),
    "wettkampf": ("Wettkämpfe", lambda it, past=False: it["kind"] == "match"),
    "training": ("Training", lambda it, past=False: it["kind"] == "training"),
    "offen": ("Offen", _is_open),
    "zugesagt": ("Zugesagt", lambda it, past=False: it["my_status"] in ("ja", "spaeter")),
    "aufgestellt": ("Aufgestellt", lambda it, past=False: it["kind"] == "match" and bool(it["in_lineup"])),
}


def group_days(items):
    days = []
    for it in items:
        if not days or days[-1]["date"] != it["date"]:
            days.append({"date": it["date"], "items": []})
        days[-1]["items"].append(it)
    return days


def agenda(uid, d_from, d_to, all_teams=False, tr_from=None, tr_to=None, newest_first=False, only_mine=False):
    """Wettkämpfe und Trainings gemeinsam, nach Tagen gruppiert.
    tr_from/tr_to begrenzen die Trainings (feste Trainings gibt es endlos)."""
    items = [dict(m, kind="match", sort=m["date"] + (m["time"] or "99"))
             for m in matches_for(uid, all_teams, d_from.isoformat() if d_from else None,
                                  d_to.isoformat() if d_to else None)]
    items += [dict(t, kind="training", sort=t["date"] + t["start"])
              for t in trainings(tr_from or d_from, tr_to or d_to, uid) if t["for_me"] or not only_mine]
    items.sort(key=lambda x: x["sort"], reverse=newest_first)
    days = []
    for it in items:
        if not days or days[-1]["date"] != it["date"]:
            days.append({"date": it["date"], "items": []})
        days[-1]["items"].append(it)
    return items, days


@app.template_filter("tag")
def fmt_day(iso):
    """'Heute', 'Morgen' oder 'Dienstag, 27.10.'"""
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    if d == date.today():
        return "Heute"
    if d == date.today() + timedelta(days=1):
        return "Morgen"
    return f"{WEEKDAYS[d.weekday()]}, {d:%d.%m.}"


# ---------------------------------------------------------------- Training
def can_manage_training():
    """Feste Trainingszeiten und Sondertrainings anlegen und ändern."""
    u = current_user()
    return bool(u and u["role"] in ("admin", "trainer"))


def can_cancel_training():
    u = current_user()
    return bool(u and u["role"] in ("admin", "leader", "trainer"))


def slot_group_map():
    """{slot_id: [Gruppe, …]} für alle Trainings mit Gruppen."""
    out = {}
    for r in q("SELECT sg.slot_id, g.id, g.name FROM slot_groups sg JOIN groups g ON g.id=sg.group_id ORDER BY g.name"):
        out.setdefault(r["slot_id"], []).append({"id": r["id"], "name": r["name"]})
    return out


def user_group_ids(uid):
    return {r["group_id"] for r in q("SELECT group_id FROM user_groups WHERE user_id=?", (uid,))}


def sees_all_trainings(uid):
    u = q("SELECT role FROM users WHERE id=?", (uid,), one=True)
    return bool(u and u["role"] in ("admin", "trainer"))


def slot_areas(s, counts):
    """Bereiche eines Trainings (z. B. LG und KK) mit Ständen und Belegung.
    counts: {Bereich: Anzahl}, None = noch nicht zugeteilt. Leer bei nur einem Bereich."""
    if not s["lanes2"]:
        return []
    return [{"no": 1, "name": s["area1"] or "LG", "lanes": s["lanes"], "booked": counts.get(1, 0)},
            {"no": 2, "name": s["area2"] or "KK", "lanes": s["lanes2"], "booked": counts.get(2, 0)},
            {"no": None, "name": "noch offen", "lanes": 0, "booked": counts.get(None, 0)}]


def training_for(slot_groups, my_groups):
    """Training gilt für den Benutzer: keine Gruppe gesetzt oder er ist in einer davon."""
    return not slot_groups or any(g["id"] in my_groups for g in slot_groups)


def trainings(d_from, d_to, user_id):
    """Alle Trainingstermine (feste und Sondertrainings) im Zeitraum mit Belegung,
    eigener Rückmeldung und Ausfall. Belegt sind Stände durch Zusage und „Komme später“."""
    f, t = d_from.isoformat(), d_to.isoformat()
    slots = q("""SELECT * FROM training_slots WHERE active=1
                 AND (once_date IS NULL OR once_date BETWEEN ? AND ?) ORDER BY weekday, start""", (f, t))
    gmap, my_groups, see_all = slot_group_map(), user_group_ids(user_id), sees_all_trainings(user_id)
    slots = [s for s in slots if see_all or training_for(gmap.get(s["id"]), my_groups)]
    if not slots:
        return []
    booked, by_area = {}, {}
    for r in q("""SELECT slot_id, date, area, COUNT(*) n FROM training_rsvp WHERE status IN ('ja','spaeter')
                  AND date BETWEEN ? AND ? GROUP BY slot_id, date, area""", (f, t)):
        key = (r["slot_id"], r["date"])
        booked[key] = booked.get(key, 0) + r["n"]
        by_area.setdefault(key, {})[r["area"]] = r["n"]
    mine, my_area = {}, {}
    for r in q("SELECT slot_id, date, status, area FROM training_rsvp WHERE user_id=? AND date BETWEEN ? AND ?",
               (user_id, f, t)):
        mine[(r["slot_id"], r["date"])] = r["status"]
        my_area[(r["slot_id"], r["date"])] = r["area"]
    cancel = {(r["slot_id"], r["date"]): r["reason"] for r in q(
        "SELECT * FROM training_cancel WHERE date BETWEEN ? AND ?", (f, t))}
    out, d = [], d_from
    while d <= d_to:
        iso = d.isoformat()
        for s in slots:
            if (s["once_date"] == iso) or (s["once_date"] is None and s["weekday"] == d.weekday()):
                key = (s["id"], iso)
                n = booked.get(key, 0)
                total = s["lanes"] + (s["lanes2"] or 0)
                out.append(dict(s, slot_id=s["id"], date=iso, booked=n, special=s["once_date"] is not None,
                                total_lanes=total, areas=slot_areas(s, by_area.get(key, {})), my_area=my_area.get(key),
                                groups=gmap.get(s["id"], []), for_me=training_for(gmap.get(s["id"]), my_groups),
                                free=max(total - n, 0), my_status=mine.get(key),
                                cancelled=key in cancel, reason=cancel.get(key, "")))
        d += timedelta(days=1)
    out.sort(key=lambda x: (x["date"], x["start"]))
    return out


def training_session(slot_id, iso):
    """Gültigen Trainingstermin laden oder 404."""
    s = q("SELECT * FROM training_slots WHERE id=?", (slot_id,), one=True)
    d = parse_date(iso)
    if not s or not d:
        abort(404)
    if s["once_date"] is not None:
        if s["once_date"] != d:
            abort(404)
    elif datetime.strptime(d, "%Y-%m-%d").weekday() != s["weekday"]:
        abort(404)
    uid = current_user()["id"]
    if not sees_all_trainings(uid) and not training_for(slot_group_map().get(s["id"]), user_group_ids(uid)):
        abort(404)   # Training einer anderen Gruppe
    return s, d


@app.route("/training")
@login_required
def training():
    """Training und Wettkämpfe stehen gemeinsam unter Termine."""
    return redirect(url_for("schedule"))


@app.route("/training/<int:slot_id>/<iso>")
@login_required
def training_detail(slot_id, iso):
    s, d = training_session(slot_id, iso)
    u = current_user()
    rows = q("""SELECT r.status, r.user_id, r.area, u.name FROM training_rsvp r JOIN users u ON u.id=r.user_id
                WHERE r.slot_id=? AND r.date=? ORDER BY u.name""", (slot_id, d))
    groups = {k: [r for r in rows if r["status"] == k] for k in TR_STATUS}
    booked = len(groups["ja"]) + len(groups["spaeter"])
    mine = next((r["status"] for r in rows if r["user_id"] == u["id"]), None)
    my_area = next((r["area"] for r in rows if r["user_id"] == u["id"]), None)
    counts = {}
    for r in groups["ja"] + groups["spaeter"]:
        counts[r["area"]] = counts.get(r["area"], 0) + 1
    areas = slot_areas(s, counts)
    c = q("SELECT reason FROM training_cancel WHERE slot_id=? AND date=?", (slot_id, d), one=True)
    past = d < date.today().isoformat()
    too_far = d > (date.today() + timedelta(days=BOOK_DAYS_AHEAD)).isoformat()
    return render_template("training_detail.html", s=s, d=d, groups=groups, booked=booked, areas=areas,
                           my_area=my_area, total_lanes=s["lanes"] + (s["lanes2"] or 0),
                           coming=groups["ja"] + groups["spaeter"],
                           tgroups=slot_group_map().get(s["id"], []),
                           free=max(s["lanes"] + (s["lanes2"] or 0) - booked, 0), mine=mine,
                           cancelled=c is not None, reason=c["reason"] if c else "",
                           open_=not past and not too_far and c is None,
                           past=past, can_cancel=can_cancel_training(), can_edit=can_manage_training(),
                           WEEKDAYS=WEEKDAYS, TR=TR_STATUS)


@app.route("/training/<int:slot_id>/<iso>/rueckmeldung", methods=["POST"])
@login_required
def training_answer(slot_id, iso):
    s, d = training_session(slot_id, iso)
    u = current_user()
    status = request.form.get("status")
    back = request.form.get("back") or ""
    back = safe_next(back, url_for("training_detail", slot_id=slot_id, iso=d))
    if status not in TR_STATUS:
        abort(400)
    if d < date.today().isoformat() or d > (date.today() + timedelta(days=BOOK_DAYS_AHEAD)).isoformat() \
            or q("SELECT 1 FROM training_cancel WHERE slot_id=? AND date=?", (slot_id, d), one=True):
        flash("Für dieses Training ist keine Rückmeldung möglich.", "error")
        return redirect(back)
    total = s["lanes"] + (s["lanes2"] or 0)
    old = q("SELECT area FROM training_rsvp WHERE slot_id=? AND date=? AND user_id=?", (slot_id, d, u["id"]), one=True)
    area = old["area"] if old else None
    if "area" in request.form and s["lanes2"]:          # Wunsch LG/KK, leer = egal
        area = request.form.get("area", type=int) if request.form.get("area") in ("1", "2") else None
    if not s["lanes2"]:
        area = None
    if status in ("ja", "spaeter"):
        n = q("""SELECT COUNT(*) n FROM training_rsvp WHERE slot_id=? AND date=? AND user_id<>?
                 AND status IN ('ja','spaeter')""", (slot_id, d, u["id"]), one=True)["n"]
        if n >= total:
            flash(f"Alle {total} Stände sind schon belegt.", "error")
            return redirect(back)
        if area:
            cap = s["lanes"] if area == 1 else s["lanes2"]
            na = q("""SELECT COUNT(*) n FROM training_rsvp WHERE slot_id=? AND date=? AND user_id<>? AND area=?
                      AND status IN ('ja','spaeter')""", (slot_id, d, u["id"], area), one=True)["n"]
            if na >= cap:
                name = s["area1"] if area == 1 else s["area2"]
                flash(f"Im Bereich {name} sind alle {cap} Stände belegt. Wähle „egal“ oder den anderen Bereich.", "error")
                return redirect(back)
    ex("""INSERT INTO training_rsvp (slot_id, date, user_id, status, area) VALUES (?,?,?,?,?)
          ON CONFLICT(slot_id, date, user_id) DO UPDATE SET status=excluded.status, area=excluded.area""",
       (slot_id, d, u["id"], status, area))
    flash(f"Gespeichert: {TR_STATUS[status]}" + (f" ({s['area1'] if area == 1 else s['area2']})" if area and status != "nein" else ""), "ok")
    return redirect(back)


@app.route("/training/<int:slot_id>/<iso>/zuweisen", methods=["POST"])
@login_required
def training_assign(slot_id, iso):
    """Trainer teilt die Zugesagten den Bereichen zu (z. B. LG oder KK)."""
    s, d = training_session(slot_id, iso)
    if not can_manage_training():
        abort(403)
    if not s["lanes2"]:
        abort(400)
    rows = q("""SELECT user_id FROM training_rsvp WHERE slot_id=? AND date=? AND status IN ('ja','spaeter')""", (slot_id, d))
    plan = {}
    for r in rows:
        v = request.form.get(f"area_{r['user_id']}", "")
        plan[r["user_id"]] = int(v) if v in ("1", "2") else None
    n1 = sum(1 for a in plan.values() if a == 1)
    n2 = sum(1 for a in plan.values() if a == 2)
    if n1 > s["lanes"] or n2 > s["lanes2"]:
        flash(f"Zu viele zugeteilt: {s['area1']} hat {s['lanes']}, {s['area2']} hat {s['lanes2']} Stände.", "error")
        return redirect(url_for("training_detail", slot_id=slot_id, iso=d) + "#zuteilung")
    conn = db()
    for uid, a in plan.items():
        conn.execute("UPDATE training_rsvp SET area=? WHERE slot_id=? AND date=? AND user_id=?", (a, slot_id, d, uid))
    conn.commit()
    flash("Zuteilung gespeichert.", "ok")
    return redirect(url_for("training_detail", slot_id=slot_id, iso=d) + "#zuteilung")


@app.route("/training/<int:slot_id>/<iso>/ausfall", methods=["POST"])
@login_required
def training_cancel(slot_id, iso):
    _s, d = training_session(slot_id, iso)
    if not can_cancel_training():
        abort(403)
    if request.form.get("undo"):
        ex("DELETE FROM training_cancel WHERE slot_id=? AND date=?", (slot_id, d))
        flash("Training findet wieder statt.", "ok")
    else:
        ex("""INSERT INTO training_cancel (slot_id, date, reason) VALUES (?,?,?)
              ON CONFLICT(slot_id, date) DO UPDATE SET reason=excluded.reason""",
           (slot_id, d, request.form.get("reason", "").strip()))
        flash("Training ist als ausgefallen markiert.", "ok")
    return redirect(url_for("training_detail", slot_id=slot_id, iso=d))


def save_slot_groups(slot_id):
    valid = {g["id"] for g in q("SELECT id FROM groups")}
    chosen = {int(x) for x in request.form.getlist("groups") if x.isdigit()} & valid
    conn = db()
    conn.execute("DELETE FROM slot_groups WHERE slot_id=?", (slot_id,))
    conn.executemany("INSERT INTO slot_groups (slot_id, group_id) VALUES (?,?)", [(slot_id, g) for g in chosen])
    conn.commit()


def _slot_form():
    """Gemeinsame Felder für feste Trainingszeiten und Sondertrainings."""
    return dict(start=request.form.get("start", "").strip(), end=request.form.get("end", "").strip(),
                title=request.form.get("title", "").strip() or "Training",
                lanes=request.form.get("lanes", type=int), location=request.form.get("location", "").strip(),
                lanes2=max(0, min(60, request.form.get("lanes2", type=int) or 0)),
                area1=request.form.get("area1", "").strip() or "LG", area2=request.form.get("area2", "").strip() or "KK",
                note=request.form.get("note", "").strip())


@app.route("/training/zeiten", methods=["GET", "POST"])
@login_required
def training_slots():
    if not can_manage_training():
        abort(403)
    if request.method == "POST":
        sid = request.form.get("id", type=int)
        if request.form.get("delete") and sid:
            ex("DELETE FROM training_slots WHERE id=?", (sid,))
            flash("Gelöscht.", "ok")
            return redirect(url_for("training_slots"))
        f = _slot_form()
        once = parse_date(request.form.get("once_date")) if request.form.get("kind") == "sonder" else None
        wd = datetime.strptime(once, "%Y-%m-%d").weekday() if once else request.form.get("weekday", type=int)
        # Formulare ohne Aktiv-Feld (neu, Sondertraining) sind immer aktiv
        active = 1 if ("active" not in request.form or "1" in request.form.getlist("active")) else 0
        if request.form.get("kind") == "sonder" and not once:
            flash("Bitte ein Datum für das Sondertraining angeben.", "error")
        elif wd is None or not 0 <= wd <= 6 or not f["start"] or not f["lanes"] or not 1 <= f["lanes"] <= 60:
            flash("Bitte Tag, Beginn und Anzahl Stände (1–60) angeben.", "error")
        elif sid:
            ex("""UPDATE training_slots SET weekday=?, start=?, end=?, title=?, lanes=?, location=?, note=?,
                  active=?, once_date=?, lanes2=?, area1=?, area2=? WHERE id=?""",
               (wd, f["start"], f["end"], f["title"], f["lanes"], f["location"], f["note"], active, once,
                f["lanes2"], f["area1"], f["area2"], sid))
            if not f["lanes2"]:
                ex("UPDATE training_rsvp SET area=NULL WHERE slot_id=?", (sid,))
            save_slot_groups(sid)
            flash("Gespeichert.", "ok")
        else:
            new_id = ex("""INSERT INTO training_slots (weekday, start, end, title, lanes, location, note, active, once_date,
                           lanes2, area1, area2) VALUES (?,?,?,?,?,?,?,1,?,?,?,?)""",
                        (wd, f["start"], f["end"], f["title"], f["lanes"], f["location"], f["note"], once,
                         f["lanes2"], f["area1"], f["area2"]))
            save_slot_groups(new_id)
            flash("Sondertraining angelegt." if once else "Trainingszeit angelegt.", "ok")
            if once:
                return redirect(url_for("training_detail", slot_id=new_id, iso=once))
        return redirect(safe_next(request.form.get("back"), url_for("training_slots")))
    slots = q("SELECT * FROM training_slots WHERE once_date IS NULL ORDER BY weekday, start")
    specials = q("SELECT * FROM training_slots WHERE once_date >= ? ORDER BY once_date, start",
                 (date.today().isoformat(),))
    return render_template("training_slots.html", slots=slots, specials=specials, WEEKDAYS=WEEKDAYS,
                           all_groups=q("SELECT * FROM groups ORDER BY name"),
                           sgmap={k: {g["id"] for g in v} for k, v in slot_group_map().items()},
                           default_lanes=(slots[0]["lanes"] if slots else 6))


# ---------------------------------------------------------------- Termine
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


@app.route("/termine")
@login_required
def schedule():
    u = current_user()
    today = date.today()
    all_teams = request.args.get("alle") == "1"
    try:
        y, m = (int(x) for x in request.args.get("monat", "").split("-"))
        month = date(y, m, 1)
    except (ValueError, TypeError):
        month = today.replace(day=1)
    prev_m = date(month.year - 1, 12, 1) if month.month == 1 else date(month.year, month.month - 1, 1)
    next_m = date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)

    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    in_view = matches_for(u["id"], all_teams, weeks[0][0].isoformat(), weeks[-1][-1].isoformat())
    by_day = {}
    for mt in in_view:
        by_day.setdefault(mt["date"], []).append(mt)

    tr_by_day = {}
    for tr in trainings(weeks[0][0], weeks[-1][-1], u["id"]):
        if not tr["cancelled"]:
            tr_by_day.setdefault(tr["date"], []).append(tr)

    # Kommend: alle Wettkämpfe, Trainings der nächsten Wochen. Vergangen: neueste zuerst.
    up_items, up_days = agenda(u["id"], today, None, all_teams,
                               tr_from=today, tr_to=today + timedelta(weeks=TRAINING_WEEKS))
    past_items, past_days = agenda(u["id"], None, today - timedelta(days=1), all_teams,
                                   tr_from=today - timedelta(weeks=TRAINING_WEEKS),
                                   tr_to=today - timedelta(days=1), newest_first=True)
    for it in past_items:
        if it["kind"] == "match":
            it["total"] = match_total(it["id"])
            it["outcome"] = match_outcome(it, it["total"])

    # Filter für die Liste unter dem Kalender
    flt = request.args.get("filter", "alle")
    if flt not in AGENDA_FILTERS:
        flt = "alle"
    counts = {k: sum(1 for it in up_items if fn(it)) for k, (_label, fn) in AGENDA_FILTERS.items()}
    up_items = [it for it in up_items if AGENDA_FILTERS[flt][1](it)]
    past_items = [it for it in past_items if AGENDA_FILTERS[flt][1](it, past=True)]
    up_days, past_days = group_days(up_items), group_days(past_items)
    return render_template("schedule.html", weeks=weeks, month=month, month_name=MONATE[month.month - 1],
                           prev_m=prev_m.strftime("%Y-%m"), next_m=next_m.strftime("%Y-%m"),
                           by_day=by_day, tr_by_day=tr_by_day, up_days=up_days, n_up=len(up_items),
                           past_days=past_days, n_past=len(past_items), all_teams=all_teams, today_d=today,
                           can_edit_training=can_manage_training(), flt=flt, counts=counts,
                           FILTERS={k: v[0] for k, v in AGENDA_FILTERS.items()})


# ---------------------------------------------------------------- Benutzer (Admin)
@app.route("/benutzer")
@admin_required
def users():
    rows = q("""SELECT u.*, (SELECT GROUP_CONCAT(t.name, ', ') FROM team_members tm
                JOIN teams t ON t.id=tm.team_id WHERE tm.user_id=u.id) teams
                FROM users u ORDER BY u.name""")
    temp = temp_locked_names()
    rows = [dict(r, temp_locked=r["username"].lower() in temp) for r in rows]
    show = request.args.get("zeige", "aktiv")
    counts = {"aktiv": sum(1 for r in rows if r["active"]), "gesperrt": sum(1 for r in rows if not r["active"])}
    if show == "gesperrt":
        rows = [r for r in rows if not r["active"]]
    else:
        show = "aktiv"
        rows = [r for r in rows if r["active"]]
    return render_template("users.html", users=rows, show=show, counts=counts)


def temp_locked_names():
    """Benutzernamen, die wegen zu vieler Fehlversuche gerade vorübergehend gesperrt sind."""
    since = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    return {r["n"].lower() for r in q("""SELECT username n FROM login_log WHERE ok=0 AND at>=?
                                         GROUP BY lower(username) HAVING COUNT(*)>=?""", (since, MAX_FAILED_LOGINS))}


@app.route("/benutzer/<int:uid>/sperren", methods=["POST"])
@admin_required
def user_lock(uid):
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True) or abort(404)
    if uid == current_user()["id"]:
        flash("Du kannst dich nicht selbst sperren.", "error")
        return redirect(url_for("user_edit", uid=uid))
    reason = request.form.get("reason", "").strip()[:200]
    # pw_ver+1: alle offenen Sitzungen sind sofort beendet
    ex("UPDATE users SET active=0, lock_reason=?, locked_at=?, pw_ver=pw_ver+1 WHERE id=?",
       (reason, datetime.now().isoformat(timespec="seconds"), uid))
    flash(f"{u['name']} ist gesperrt und wurde auf allen Geräten abgemeldet.", "ok")
    return redirect(url_for("user_edit", uid=uid))


@app.route("/benutzer/<int:uid>/entsperren", methods=["POST"])
@admin_required
def user_unlock(uid):
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True) or abort(404)
    ex("UPDATE users SET active=1, lock_reason='', locked_at=NULL WHERE id=?", (uid,))
    # vorübergehende Sperre durch Fehlversuche ebenfalls aufheben
    ex("DELETE FROM login_log WHERE ok=0 AND username=? COLLATE NOCASE", (u["username"],))
    flash(f"{u['name']} ist entsperrt und kann sich wieder anmelden.", "ok")
    return redirect(request.form.get("zurueck") == "liste" and url_for("users") or url_for("user_edit", uid=uid))


@app.route("/benutzer/neu", methods=["GET", "POST"])
@app.route("/benutzer/<int:uid>", methods=["GET", "POST"])
@admin_required
def user_edit(uid=None):
    user = q("SELECT * FROM users WHERE id=?", (uid,), one=True) if uid else None
    if uid and not user:
        abort(404)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        role = request.form.get("role", "shooter")
        active = user["active"] if user else 1   # Sperren/Entsperren über eigene Knöpfe
        pw = request.form.get("password", "")
        extra = {k: request.form.get(k, "").strip() for k in ("player_id", "pass_nr", "club")}
        if role not in ROLES:
            role = "shooter"
        if user and user["id"] == current_user()["id"]:
            role, active = "admin", 1  # sich selbst nicht aussperren
        dupe = q("SELECT id FROM users WHERE username=? AND id IS NOT ?", (username, uid), one=True)
        if not name or not username:
            flash("Name und Benutzername sind Pflicht.", "error")
        elif dupe:
            flash("Dieser Benutzername ist schon vergeben.", "error")
        elif not user and len(pw) < 8:
            flash("Startpasswort: mindestens 8 Zeichen.", "error")
        elif user and pw and len(pw) < 8:
            flash("Neues Passwort: mindestens 8 Zeichen.", "error")
        else:
            if user:
                ex("UPDATE users SET name=?, username=?, role=?, active=?, player_id=?, pass_nr=?, club=? WHERE id=?",
                   (name, username, role, active, extra["player_id"], extra["pass_nr"], extra["club"], uid))
                if pw:
                    ex("UPDATE users SET password_hash=?, must_change_pw=1, pw_ver=pw_ver+1 WHERE id=?",
                       (generate_password_hash(pw), uid))
                if role not in ("admin", "leader"):  # kein Mannschaftsführer mehr
                    ex("UPDATE teams SET leader_id=NULL WHERE leader_id=?", (uid,))
                save_user_groups(uid)
                flash("Benutzer gespeichert.", "ok")
            else:
                new_uid = ex("""INSERT INTO users (name, username, role, active, password_hash, must_change_pw, player_id, pass_nr, club)
                      VALUES (?,?,?,?,?,1,?,?,?)""", (name, username, role, active, generate_password_hash(pw),
                                                    extra["player_id"], extra["pass_nr"], extra["club"]))
                save_user_groups(new_uid)
                flash(f"Benutzer {name} angelegt. Beim ersten Login muss das Passwort geändert werden.", "ok")
            return redirect(url_for("users"))
        user = dict(user or {}, name=name, username=username, role=role, active=active, **extra)
    impact = None
    if uid:
        impact = q("""SELECT
            (SELECT COUNT(*) FROM lineup WHERE user_id=:u AND rings IS NOT NULL) results,
            (SELECT COUNT(*) FROM lineup WHERE user_id=:u) lineups,
            (SELECT COUNT(*) FROM team_members WHERE user_id=:u) teams,
            (SELECT COUNT(*) FROM teams WHERE leader_id=:u) led,
            (SELECT COUNT(*) FROM training_rsvp WHERE user_id=:u) trainings""", {"u": uid}, one=True)
    return render_template("user_edit.html", user=user, all_groups=q("SELECT * FROM groups ORDER BY name"),
                           my_groups=user_group_ids(uid) if uid else set(), impact=impact,
                           temp_locked=bool(user and user["username"] and user["username"].lower() in temp_locked_names()))


def save_user_groups(uid):
    valid = {g["id"] for g in q("SELECT id FROM groups")}
    chosen = {int(x) for x in request.form.getlist("groups") if x.isdigit()} & valid
    conn = db()
    conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
    conn.executemany("INSERT INTO user_groups (user_id, group_id) VALUES (?,?)", [(uid, g) for g in chosen])
    conn.commit()


# ---------------------------------------------------------------- Admin-Dashboard
def _size(path):
    total = 0
    if os.path.isfile(path):
        return os.path.getsize(path)
    for root, _dirs, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total


@app.template_filter("bytes")
def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return (f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024


@app.template_filter("seit")
def fmt_since(iso):
    """'gerade eben', 'vor 5 Min.', 'vor 3 Std.', 'gestern 18:04', '12.10. 18:04'"""
    if not iso:
        return "noch nie"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    sec = (datetime.now() - t).total_seconds()
    if sec < 60:
        return "gerade eben"
    if sec < 3600:
        return f"vor {int(sec // 60)} Min."
    if t.date() == date.today():
        return f"heute {t:%H:%M}"
    if t.date() == date.today() - timedelta(days=1):
        return f"gestern {t:%H:%M}"
    return f"{t:%d.%m.%y %H:%M}"


def device_name(agent):
    a = (agent or "").lower()
    os_ = "iPhone" if "iphone" in a else "iPad" if "ipad" in a else "Android" if "android" in a else \
        "Windows" if "windows" in a else "Mac" if "mac os" in a else "Linux" if "linux" in a else "?"
    br = "Edge" if "edg/" in a else "Chrome" if "chrome" in a or "crios" in a else "Firefox" if "firefox" in a or "fxios" in a \
        else "Safari" if "safari" in a else ""
    return f"{os_} · {br}" if br else os_


def admin_dashboard():
    import platform
    import shutil
    import flask
    now = datetime.now()
    online_since = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    online = q("SELECT id, name, role, last_seen, avatar FROM users WHERE last_seen>=? ORDER BY last_seen DESC", (online_since,))
    logins = [dict(r, device=device_name(r["agent"])) for r in q(
        """SELECT l.*, u.name FROM login_log l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 15""")]
    day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
    week_ago = (now - timedelta(days=7)).isoformat(timespec="seconds")
    stats = q(f"""SELECT
        (SELECT COUNT(*) FROM users WHERE active=1) users,
        (SELECT COUNT(*) FROM users WHERE active=1 AND last_seen>=?) active_week,
        (SELECT COUNT(*) FROM login_log WHERE ok=1 AND at>=?) logins_day,
        (SELECT COUNT(*) FROM login_log WHERE ok=0 AND at>=?) failed_day,
        (SELECT COUNT(*) FROM error_log WHERE at>=?) errors_week,
        (SELECT COUNT(*) FROM teams) teams,
        (SELECT COUNT(*) FROM matches WHERE date>=?) matches_up,
        (SELECT COUNT(*) FROM training_slots WHERE active=1 AND once_date IS NULL) slots""",
              (week_ago, day_ago, day_ago, week_ago, date.today().isoformat()), one=True)
    errors = q("""SELECT e.*, u.name FROM error_log e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 10""")
    data_dir = os.path.dirname(DB_PATH)
    disk = shutil.disk_usage(data_dir)
    up = now - STARTED_AT
    server = {
        "uptime": f"{up.days} T. {up.seconds // 3600} Std." if up.days else f"{up.seconds // 3600} Std. {up.seconds % 3600 // 60} Min.",
        "started": STARTED_AT.strftime("%d.%m.%Y %H:%M"),
        "db": _size(DB_PATH), "avatars": _size(AVATAR_DIR) if os.path.isdir(AVATAR_DIR) else 0,
        "disk_free": disk.free, "disk_total": disk.total, "disk_pct": round(100 * disk.used / disk.total) if disk.total else 0,
        "python": platform.python_version(), "flask": flask.__version__, "sqlite": sqlite3.sqlite_version,
        "host": platform.node(), "https": request.is_secure, "cookie_secure": app.config["SESSION_COOKIE_SECURE"],
        "secret_env": bool(os.environ.get("SECRET_KEY")) and os.environ.get("SECRET_KEY") != "bitte-aendern",
    }
    todo = {
        "never": q("SELECT id, name FROM users WHERE active=1 AND last_login IS NULL AND role<>'admin' ORDER BY name"),
        "moves": q("""SELECT mv.*, m.opponent, t.name team_name FROM match_moves mv JOIN matches m ON m.id=mv.match_id
                      JOIN teams t ON t.id=m.team_id WHERE mv.status='offen' ORDER BY mv.new_date"""),
        "no_result": q("""SELECT m.id, m.date, m.opponent, t.name team_name FROM matches m JOIN teams t ON t.id=m.team_id
                          WHERE m.date < ? AND m.date >= ? AND NOT EXISTS (SELECT 1 FROM lineup l WHERE l.match_id=m.id AND l.rings IS NOT NULL)
                          ORDER BY m.date DESC LIMIT 10""", (date.today().isoformat(), (date.today() - timedelta(days=60)).isoformat())),
        "no_leader": q("SELECT id, name FROM teams WHERE leader_id IS NULL ORDER BY name"),
    }
    warnings = []
    if not server["secret_env"]:
        warnings.append("SECRET_KEY ist nicht in der .env gesetzt. Die App nutzt einen automatisch erzeugten Schlüssel.")
    if not server["https"]:
        warnings.append("Die Seite läuft ohne HTTPS. Für den Zugriff von außen bitte einen Reverse Proxy mit HTTPS einrichten.")
    elif not server["cookie_secure"]:
        warnings.append("HTTPS ist aktiv, aber COOKIE_SECURE=1 ist in der .env noch nicht gesetzt.")
    if server["disk_pct"] >= 90:
        warnings.append(f"Der Speicher ist zu {server['disk_pct']} % voll.")
    if q("SELECT 1 FROM users WHERE role='admin' AND must_change_pw=1", one=True):
        warnings.append("Ein Admin-Konto hat noch das Startpasswort.")
    return render_template("admin_home.html", online=online, logins=logins, stats=stats, errors=errors,
                           server=server, todo=todo, warnings=warnings)


@app.route("/admin/backup")
@admin_required
def admin_backup():
    """Konsistente Kopie der Datenbank zum Herunterladen."""
    import tempfile
    from flask import send_file
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp.name)
    src.backup(dst)
    dst.close()
    src.close()
    name = f"verein-backup-{datetime.now():%Y-%m-%d-%H%M}.db"
    resp = send_file(tmp.name, as_attachment=True, download_name=name, mimetype="application/x-sqlite3")
    resp.call_on_close(lambda: os.remove(tmp.name))
    return resp


@app.route("/admin/protokoll")
@admin_required
def admin_log():
    kind = request.args.get("art", "anmeldungen")
    if kind == "fehler":
        rows = q("SELECT e.*, u.name FROM error_log e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 300")
    else:
        kind = "anmeldungen"
        rows = [dict(r, device=device_name(r["agent"])) for r in q(
            "SELECT l.*, u.name FROM login_log l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 300")]
    return render_template("admin_log.html", kind=kind, rows=rows)


@app.route("/admin/protokoll/leeren", methods=["POST"])
@admin_required
def admin_log_clear():
    ex("DELETE FROM error_log")
    flash("Fehlerprotokoll geleert.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- Gruppen (nur Admin)
@app.route("/gruppen", methods=["GET", "POST"])
@admin_required
def groups():
    if request.method == "POST":
        gid = request.form.get("id", type=int)
        name = request.form.get("name", "").strip()
        if request.form.get("delete") and gid:
            ex("DELETE FROM groups WHERE id=?", (gid,))
            flash("Gruppe gelöscht. Trainings dieser Gruppe gelten jetzt für alle, sofern keine andere Gruppe gesetzt ist.", "ok")
            return redirect(url_for("groups"))
        if not name:
            flash("Bitte einen Namen angeben.", "error")
            return redirect(url_for("groups"))
        dupe = q("SELECT id FROM groups WHERE name=? AND id IS NOT ?", (name, gid), one=True)
        if dupe:
            flash("Diese Gruppe gibt es schon.", "error")
            return redirect(url_for("groups"))
        if gid:
            ex("UPDATE groups SET name=? WHERE id=?", (name, gid))
        else:
            gid = ex("INSERT INTO groups (name) VALUES (?)", (name,))
        if request.form.get("members_sent"):
            valid = {u["id"] for u in q("SELECT id FROM users")}
            chosen = {int(x) for x in request.form.getlist("members") if x.isdigit()} & valid
            conn = db()
            conn.execute("DELETE FROM user_groups WHERE group_id=?", (gid,))
            conn.executemany("INSERT INTO user_groups (user_id, group_id) VALUES (?,?)", [(u, gid) for u in chosen])
            conn.commit()
        flash("Gruppe gespeichert.", "ok")
        return redirect(url_for("groups") + f"#g{gid}")
    rows = q("""SELECT g.*, (SELECT COUNT(*) FROM user_groups WHERE group_id=g.id) n,
                (SELECT COUNT(*) FROM slot_groups WHERE group_id=g.id) n_slots FROM groups g ORDER BY g.name""")
    members = {}
    for r in q("SELECT user_id, group_id FROM user_groups"):
        members.setdefault(r["group_id"], set()).add(r["user_id"])
    users_ = q("SELECT id, name, active FROM users ORDER BY active DESC, name")
    return render_template("groups.html", rows=rows, members=members, users=users_)


@app.route("/benutzer/<int:uid>/loeschen", methods=["POST"])
@admin_required
def user_delete(uid):
    u = q("SELECT * FROM users WHERE id=?", (uid,), one=True) or abort(404)
    if uid == current_user()["id"]:
        flash("Du kannst dich nicht selbst löschen.", "error")
        return redirect(url_for("user_edit", uid=uid))
    if request.form.get("confirm_name", "").strip().lower() != u["username"].lower():
        flash("Zum Löschen bitte den Benutzernamen genau eintippen.", "error")
        return redirect(url_for("user_edit", uid=uid) + "#loeschen")
    ex("DELETE FROM users WHERE id=?", (uid,))
    try:
        os.remove(os.path.join(AVATAR_DIR, f"{uid}.jpg"))
    except FileNotFoundError:
        pass
    flash(f"{u['name']} wurde endgültig gelöscht.", "ok")
    return redirect(url_for("users"))


# ---------------------------------------------------------------- Mannschaften
@app.route("/mannschaften")
@login_required
def teams():
    rows = q("""SELECT t.*, u.name leader_name,
                (SELECT COUNT(*) FROM team_members WHERE team_id=t.id) n_members,
                (SELECT MIN(date) FROM matches WHERE team_id=t.id AND date>=?) next_match
                FROM teams t LEFT JOIN users u ON u.id=t.leader_id
                ORDER BY t.season DESC, t.name""", (date.today().isoformat(),))
    return render_template("teams.html", teams=rows)


@app.route("/mannschaft/neu", methods=["GET", "POST"])
@app.route("/mannschaft/<int:tid>/bearbeiten", methods=["GET", "POST"])
@login_required
def team_edit(tid=None):
    """Neue Mannschaft: nur Admin. Bearbeiten: Admin, Mannschaftsführer und Bearbeiter.
    Mannschaftsführer und Bearbeiter festlegen darf nur der Admin."""
    team = get_team(tid) if tid else None
    is_admin = current_user()["role"] == "admin"
    if (team is None and not is_admin) or (team is not None and not can_manage(team)):
        abort(403)
    leaders = q("SELECT id, name FROM users WHERE role IN ('admin','leader') AND active=1 ORDER BY name")
    all_users = q("SELECT id, name FROM users WHERE active=1 ORDER BY name")
    editors = {r["user_id"] for r in q("SELECT user_id FROM team_editors WHERE team_id=?", (tid,))} if tid else set()
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("name", "discipline", "league", "season", "rwk_url", "rwk_name", "ext_id")}
        if f["rwk_url"] and not f["rwk_url"].startswith(("https://", "http://")):
            f["rwk_url"] = "https://" + f["rwk_url"]
        if is_admin:
            leader = request.form.get("leader_id") or None
            if leader and not any(str(l["id"]) == leader for l in leaders):
                leader = None
            leader = int(leader) if leader else None
            valid = {u["id"] for u in all_users}
            editors = {int(x) for x in request.form.getlist("editors") if x.isdigit()} & valid
            editors.discard(leader)
        else:
            leader = team["leader_id"]
        if not f["name"]:
            flash("Name ist Pflicht.", "error")
        else:
            if team:
                ex("""UPDATE teams SET name=?, discipline=?, league=?, season=?, leader_id=?, rwk_url=?, rwk_name=?,
                      ext_id=? WHERE id=?""", (f["name"], f["discipline"], f["league"], f["season"], leader,
                                               f["rwk_url"], f["rwk_name"], f["ext_id"], tid))
            else:
                tid = ex("""INSERT INTO teams (name, discipline, league, season, leader_id, rwk_url, rwk_name, ext_id)
                            VALUES (?,?,?,?,?,?,?,?)""", (f["name"], f["discipline"], f["league"], f["season"],
                                                          leader, f["rwk_url"], f["rwk_name"], f["ext_id"]))
            if is_admin:
                conn = db()
                conn.execute("DELETE FROM team_editors WHERE team_id=?", (tid,))
                conn.executemany("INSERT INTO team_editors (team_id, user_id) VALUES (?,?)",
                                 [(tid, e) for e in editors])
                conn.commit()
            flash("Mannschaft gespeichert.", "ok")
            return redirect(url_for("team", tid=tid))
        team = dict(team or {}, **f, leader_id=leader)
    return render_template("team_edit.html", team=team, leaders=leaders, all_users=all_users,
                           editors=editors, is_admin=is_admin, disciplines=DISCIPLINES)


@app.route("/mannschaft/<int:tid>/loeschen", methods=["POST"])
@admin_required
def team_delete(tid):
    ex("DELETE FROM teams WHERE id=?", (tid,))
    flash("Mannschaft gelöscht.", "ok")
    return redirect(url_for("teams"))


@app.route("/mannschaft/<int:tid>")
@login_required
def team(tid):
    t = get_team(tid)
    members = q("""SELECT u.id, u.name, tm.reserve,
                   COUNT(l.rings) n, AVG(l.rings) avg, MAX(l.rings) best
                   FROM team_members tm JOIN users u ON u.id=tm.user_id
                   LEFT JOIN lineup l ON l.user_id=u.id AND l.rings IS NOT NULL
                        AND l.match_id IN (SELECT id FROM matches WHERE team_id=?)
                   WHERE tm.team_id=? GROUP BY u.id
                   ORDER BY tm.reserve, avg IS NULL, avg DESC, u.name""", (tid, tid))
    matches = [dict(m) for m in q("SELECT * FROM matches WHERE team_id=? ORDER BY date, time", (tid,))]
    record = {"sieg": 0, "unentschieden": 0, "niederlage": 0}
    for m in matches:
        m["total"] = match_total(m["id"])
        m["outcome"] = match_outcome(m, m["total"])
        if m["outcome"]:
            record[m["outcome"]] += 1
    member_ids = {m["id"] for m in members}
    candidates = [u for u in q("SELECT id, name FROM users WHERE active=1 ORDER BY name") if u["id"] not in member_ids]
    editors = q("""SELECT u.name FROM team_editors e JOIN users u ON u.id=e.user_id
                   WHERE e.team_id=? ORDER BY u.name""", (tid,))
    return render_template("team.html", t=t, members=members, matches=matches, record=record,
                           candidates=candidates, manage=can_manage(t), editors=editors)


@app.route("/mannschaft/<int:tid>/mitglieder", methods=["POST"])
@login_required
def team_add_member(tid):
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    uid = request.form.get("user_id", type=int)
    reserve = 1 if request.form.get("reserve") == "1" else 0
    if uid and q("SELECT 1 FROM users WHERE id=? AND active=1", (uid,), one=True):
        ex("INSERT OR IGNORE INTO team_members (team_id, user_id, reserve) VALUES (?,?,?)", (tid, uid, reserve))
    return redirect(url_for("team", tid=tid))


@app.route("/mannschaft/<int:tid>/mitglieder/<int:uid>/ersatz", methods=["POST"])
@login_required
def team_toggle_reserve(tid, uid):
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    ex("UPDATE team_members SET reserve = 1 - reserve WHERE team_id=? AND user_id=?", (tid, uid))
    return redirect(url_for("team", tid=tid))


@app.route("/mannschaft/<int:tid>/mitglieder/<int:uid>/entfernen", methods=["POST"])
@login_required
def team_remove_member(tid, uid):
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    ex("DELETE FROM team_members WHERE team_id=? AND user_id=?", (tid, uid))
    return redirect(url_for("team", tid=tid))


# ---------------------------------------------------------------- Wettkämpfe
@app.route("/mannschaft/<int:tid>/wettkampf/neu", methods=["GET", "POST"])
@app.route("/wettkampf/<int:mid>/bearbeiten", methods=["GET", "POST"])
@login_required
def match_edit(tid=None, mid=None):
    m = get_match(mid) if mid else None
    t = get_team(m["team_id"] if m else tid)
    if not can_manage(t):
        abort(403)
    if request.method == "POST":
        f = {k: request.form.get(k, "").strip() for k in ("time", "opponent", "location", "notes", "meet")}
        d = parse_date(request.form.get("date"))
        home = 1 if request.form.get("home") == "1" else 0
        opp = parse_num(request.form.get("opponent_score"))
        if not d or not f["opponent"]:
            flash("Datum und Gegner sind Pflicht.", "error")
        else:
            if m:
                ex("""UPDATE matches SET date=?, time=?, opponent=?, home=?, location=?, opponent_score=?,
                      notes=?, meet=? WHERE id=?""",
                   (d, f["time"], f["opponent"], home, f["location"], opp, f["notes"], f["meet"], mid))
            else:
                mid = ex("""INSERT INTO matches (team_id, date, time, opponent, home, location, opponent_score, notes, meet)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                         (t["id"], d, f["time"], f["opponent"], home, f["location"], opp, f["notes"], f["meet"]))
            flash("Wettkampf gespeichert.", "ok")
            return redirect(url_for("match", mid=mid))
        m = dict(m or {}, **f, date=request.form.get("date"), home=home, opponent_score=opp)
    return render_template("match_edit.html", m=m, t=t, opponents=opponent_choices())


# ---------------------------------------------------------------- Verlegung
MOVE_STATUS = {"offen": "beantragt", "bestaetigt": "bestätigt", "abgelehnt": "abgelehnt", "zurueckgezogen": "zurückgezogen"}


def move_request_text(m, t, mv):
    """Fertiger Text für den Verlegungsantrag an Gegner und Rundenwettkampfleiter."""
    club = setting("club_name") or VEREIN_NAME
    when = lambda d, tm: fmt_date(d) + (f", {tm} Uhr" if tm else "")
    lines = [
        f"Antrag auf Verlegung eines Rundenwettkampfs",
        "",
        f"Klasse: {t['league'] or '–'}{' (' + t['discipline'] + ')' if t['discipline'] else ''}",
        f"Begegnung: {t['rwk_name'] or t['name']} {'gegen' if m['home'] else 'bei'} {m['opponent']}",
        f"Bisheriger Termin: {when(mv['old_date'], mv['old_time'])}",
        f"Neuer Termin: {when(mv['new_date'], mv['new_time'])}",
    ]
    if mv["reason"]:
        lines.append(f"Grund: {mv['reason']}")
    lines += ["", f"Bitte um Zustimmung. Viele Grüße", f"{current_user()['name']}, {club}"]
    return "\n".join(lines)


@app.route("/wettkampf/<int:mid>/verlegen", methods=["POST"])
@login_required
def match_move(mid):
    m = get_match(mid)
    if not can_manage(get_team(m["team_id"])):
        abort(403)
    action = request.form.get("action")
    open_move = q("SELECT * FROM match_moves WHERE match_id=? AND status='offen'", (mid,), one=True)
    now = datetime.now().isoformat(timespec="minutes")
    if action == "antrag":
        d = parse_date(request.form.get("new_date"))
        tm = request.form.get("new_time", "").strip()
        if not d:
            flash("Bitte den neuen Termin angeben.", "error")
        elif d == m["date"] and tm == m["time"]:
            flash("Der neue Termin ist derselbe wie der bisherige.", "error")
        elif open_move:
            flash("Es gibt schon einen offenen Verlegungsantrag.", "error")
        else:
            ex("""INSERT INTO match_moves (match_id, old_date, old_time, new_date, new_time, reason, created_by, created_at)
                  VALUES (?,?,?,?,?,?,?,?)""", (mid, m["date"], m["time"], d, tm or m["time"],
                                                request.form.get("reason", "").strip(), current_user()["id"], now))
            flash("Verlegung beantragt. Schicke jetzt den Antragstext an Gegner und Rundenwettkampfleiter.", "ok")
    elif open_move and action in ("bestaetigt", "abgelehnt", "zurueckgezogen"):
        conn = db()
        conn.execute("UPDATE match_moves SET status=?, decided_at=? WHERE id=?", (action, now, open_move["id"]))
        if action == "bestaetigt":
            conn.execute("UPDATE matches SET date=?, time=? WHERE id=?", (open_move["new_date"], open_move["new_time"], mid))
            # Neuer Termin: alle sollen neu zu- oder absagen, Fahrgemeinschaften neu planen
            conn.execute("DELETE FROM availability WHERE match_id=?", (mid,))
            conn.execute("DELETE FROM rides WHERE match_id=?", (mid,))
        conn.commit()
        flash({"bestaetigt": "Verlegung bestätigt. Der Termin ist geändert, alle Rückmeldungen wurden zurückgesetzt.",
               "abgelehnt": "Verlegung als abgelehnt markiert. Der bisherige Termin bleibt.",
               "zurueckgezogen": "Verlegungsantrag zurückgezogen."}[action], "ok")
    return redirect(url_for("match", mid=mid) + "#verlegung")


@app.route("/wettkampf/<int:mid>/beschreibung", methods=["POST"])
@login_required
def match_notes(mid):
    """Beschreibung und Treffpunkt direkt auf der Wettkampfseite ändern."""
    m = get_match(mid)
    if not can_manage(get_team(m["team_id"])):
        abort(403)
    ex("UPDATE matches SET notes=?, meet=? WHERE id=?",
       (request.form.get("notes", "").strip(), request.form.get("meet", m["meet"]).strip(), mid))
    flash("Beschreibung gespeichert.", "ok")
    return redirect(url_for("match", mid=mid))


@app.route("/wettkampf/<int:mid>/loeschen", methods=["POST"])
@login_required
def match_delete(mid):
    m = get_match(mid)
    t = get_team(m["team_id"])
    if not can_manage(t):
        abort(403)
    ex("DELETE FROM matches WHERE id=?", (mid,))
    flash("Wettkampf gelöscht.", "ok")
    return redirect(url_for("team", tid=t["id"]))


def carpool(mid):
    """Fahrgemeinschaft eines Wettkampfs: Fahrer mit Mitfahrern, offene Mitfahrer, eigene Anreise."""
    rides = q("""SELECT r.*, u.name FROM rides r JOIN users u ON u.id=r.user_id
                 WHERE r.match_id=? ORDER BY u.name""", (mid,))
    drivers = [dict(r, passengers=[]) for r in rides if r["mode"] == "fahrer"]
    by_id = {d["user_id"]: d for d in drivers}
    open_riders, self_travel = [], []
    for r in rides:
        if r["mode"] == "mitfahrer":
            d = by_id.get(r["driver_id"])
            if d and len(d["passengers"]) < d["seats"]:
                d["passengers"].append(r)
            else:
                open_riders.append(r)
        elif r["mode"] == "selbst":
            self_travel.append(r)
    free = sum(max(d["seats"] - len(d["passengers"]), 0) for d in drivers)
    return {"drivers": drivers, "open": open_riders, "self": self_travel, "free": free,
            "mine": next((r for r in rides if r["user_id"] == current_user()["id"]), None)}


@app.route("/wettkampf/<int:mid>")
@login_required
def match(mid):
    m = get_match(mid)
    t = get_team(m["team_id"])
    u = current_user()
    rows = q("""SELECT u.id, u.name, tm.reserve, 0 guest, a.status, l.user_id IS NOT NULL in_lineup, l.rings
                FROM team_members tm JOIN users u ON u.id=tm.user_id
                LEFT JOIN availability a ON a.match_id=? AND a.user_id=u.id
                LEFT JOIN lineup l ON l.match_id=? AND l.user_id=u.id
                WHERE tm.team_id=? ORDER BY in_lineup DESC, tm.reserve, u.name""", (mid, mid, t["id"]))
    # Aushilfen: aufgestellt, aber nicht Mitglied dieser Mannschaft
    extra = q("""SELECT u.id, u.name, 0 reserve, 1 guest, a.status, 1 in_lineup, l.rings FROM lineup l
                 JOIN users u ON u.id=l.user_id
                 LEFT JOIN availability a ON a.match_id=l.match_id AND a.user_id=u.id
                 WHERE l.match_id=? AND l.user_id NOT IN (SELECT user_id FROM team_members WHERE team_id=?)
                 ORDER BY u.name""", (mid, t["id"]))
    rows = list(rows) + list(extra)
    shown = {r["id"] for r in rows}
    helpers = [x for x in q("SELECT id, name FROM users WHERE active=1 ORDER BY name") if x["id"] not in shown]
    total = match_total(mid)
    participant = is_participant(m, u["id"])
    my = q("SELECT status FROM availability WHERE match_id=? AND user_id=?", (mid, u["id"]), one=True)
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in AVAIL}
    moves = q("""SELECT mv.*, u.name by_name FROM match_moves mv LEFT JOIN users u ON u.id=mv.created_by
                 WHERE mv.match_id=? ORDER BY mv.id DESC""", (mid,))
    open_move = next((mv for mv in moves if mv["status"] == "offen"), None)
    return render_template("match.html", m=m, t=t, rows=rows, total=total, moves=moves, open_move=open_move,
                           move_text=move_request_text(m, t, open_move) if open_move else "",
                           outcome=match_outcome(m, total), manage=can_manage(t),
                           member=participant, my_status=my["status"] if my else None,
                           counts=counts, helpers=helpers,
                           pool=carpool(mid) if not m["home"] else None, RIDE_MODES=RIDE_MODES)


@app.route("/wettkampf/<int:mid>/verfuegbarkeit", methods=["POST"])
@login_required
def match_availability(mid):
    m = get_match(mid)
    u = current_user()
    status = request.form.get("status")
    if not is_participant(m, u["id"]) or status not in AVAIL:
        abort(403)
    ex("""INSERT INTO availability (match_id, user_id, status) VALUES (?,?,?)
          ON CONFLICT(match_id, user_id) DO UPDATE SET status=excluded.status""", (mid, u["id"], status))
    flash(f"Gespeichert: {AVAIL[status]}", "ok")
    back = request.form.get("back")
    return redirect(safe_next(back, url_for("match", mid=mid)))


@app.route("/wettkampf/<int:mid>/fahrt", methods=["POST"])
@login_required
def match_ride(mid):
    m = get_match(mid)
    u = current_user()
    if m["home"] or not is_participant(m, u["id"]):
        abort(403)
    mode = request.form.get("mode")
    conn = db()
    if mode == "keine":
        conn.execute("DELETE FROM rides WHERE match_id=? AND user_id=?", (mid, u["id"]))
        conn.execute("UPDATE rides SET driver_id=NULL WHERE match_id=? AND driver_id=?", (mid, u["id"]))
        conn.commit()
        flash("Fahrt-Angabe entfernt.", "ok")
        return redirect(url_for("match", mid=mid) + "#fahrt")
    if mode not in RIDE_MODES:
        abort(400)
    seats = min(max(request.form.get("seats", type=int) or 0, 0), 8) if mode == "fahrer" else 0
    driver = request.form.get("driver_id", type=int) if mode == "mitfahrer" else None
    if driver and not q("SELECT 1 FROM rides WHERE match_id=? AND user_id=? AND mode='fahrer'", (mid, driver), one=True):
        driver = None
    if driver == u["id"]:
        driver = None
    conn.execute("""INSERT INTO rides (match_id, user_id, mode, seats, driver_id) VALUES (?,?,?,?,?)
                    ON CONFLICT(match_id, user_id) DO UPDATE SET mode=excluded.mode, seats=excluded.seats,
                    driver_id=excluded.driver_id""", (mid, u["id"], mode, seats, driver))
    if mode != "fahrer":  # wer nicht mehr fährt, nimmt niemanden mehr mit
        conn.execute("UPDATE rides SET driver_id=NULL WHERE match_id=? AND driver_id=?", (mid, u["id"]))
    conn.commit()
    flash(f"Gespeichert: {RIDE_MODES[mode]}", "ok")
    return redirect(url_for("match", mid=mid) + "#fahrt")


@app.route("/wettkampf/<int:mid>/aufstellung", methods=["POST"])
@login_required
def match_lineup(mid):
    m = get_match(mid)
    t = get_team(m["team_id"])
    if not can_manage(t):
        abort(403)
    allowed = {r["user_id"] for r in q("SELECT user_id FROM team_members WHERE team_id=?", (t["id"],))}
    allowed |= {r["user_id"] for r in q("SELECT user_id FROM lineup WHERE match_id=?", (mid,))}
    chosen = {int(x) for x in request.form.getlist("lineup") if x.isdigit()} & allowed
    conn = db()
    conn.execute("DELETE FROM lineup WHERE match_id=?", (mid,))
    for uid in chosen:
        conn.execute("INSERT INTO lineup (match_id, user_id, rings) VALUES (?,?,?)",
                     (mid, uid, parse_num(request.form.get(f"rings_{uid}"))))
    conn.execute("UPDATE matches SET opponent_score=? WHERE id=?",
                 (parse_num(request.form.get("opponent_score")), mid))
    conn.commit()
    flash("Aufstellung und Ergebnisse gespeichert.", "ok")
    return redirect(url_for("match", mid=mid))


@app.route("/wettkampf/<int:mid>/aushilfe", methods=["POST"])
@login_required
def match_add_helper(mid):
    """Schützen aus einer anderen Mannschaft als Ersatz für diesen Wettkampf aufstellen."""
    m = get_match(mid)
    if not can_manage(get_team(m["team_id"])):
        abort(403)
    uid = request.form.get("user_id", type=int)
    if uid and q("SELECT 1 FROM users WHERE id=? AND active=1", (uid,), one=True):
        ex("INSERT OR IGNORE INTO lineup (match_id, user_id) VALUES (?,?)", (mid, uid))
        flash("Ersatzschütze aufgestellt.", "ok")
    return redirect(url_for("match", mid=mid))


# ---------------------------------------------------------------- RWK-Onlinemelder
RWK_HOME = "https://www.rwk-onlinemelder.de/"


def parse_ics(text):
    """Termine aus einer iCal-Datei (z. B. Wettkampfplan aus dem RWK-Onlinemelder) lesen.
    Liefert eine Liste von Dicts mit uid, date, time, summary, location."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:      # gefaltete Zeile fortsetzen
            lines[-1] += raw[1:]
        else:
            lines.append(raw)

    def unescape(v):
        return v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()

    events, ev = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT":
            if ev is not None and ev.get("date"):
                events.append(ev)
            ev = None
        elif ev is not None and ":" in line:
            key, val = line.split(":", 1)
            name, *params = key.split(";")
            name = name.upper()
            if name == "DTSTART":
                val = val.strip()
                try:
                    if len(val) == 8:                               # nur Datum
                        ev["date"], ev["time"] = datetime.strptime(val, "%Y%m%d").date().isoformat(), ""
                    else:
                        dt = datetime.strptime(val[:15], "%Y%m%dT%H%M%S")
                        if val.endswith("Z"):                       # UTC -> deutsche Zeit
                            from zoneinfo import ZoneInfo
                            dt = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Europe/Berlin"))
                        ev["date"], ev["time"] = dt.date().isoformat(), dt.strftime("%H:%M")
                except ValueError:
                    pass
            elif name in ("SUMMARY", "LOCATION", "UID", "DESCRIPTION"):
                ev[name.lower()] = unescape(val)
    return events


def guess_pairing(summary, own_name):
    """Aus 'Heim - Gast' den Gegner und Heim/Auswärts ableiten."""
    import re
    parts = re.split(r"\s+(?:-|–|:|vs\.?|gegen)\s+", summary, maxsplit=1)
    if len(parts) != 2:
        return summary, 1
    left, right = (p.strip() for p in parts)
    own = (own_name or "").lower().strip()
    if own and own in right.lower() and own not in left.lower():
        return left, 0
    if own and own in left.lower():
        return right, 1
    return right, 1


@app.route("/mannschaft/<int:tid>/rwk-import", methods=["GET", "POST"])
@login_required
def rwk_import(tid):
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    # Schritt 2: Vorschau bestätigt -> Wettkämpfe anlegen oder aktualisieren
    if request.method == "POST" and request.form.get("step") == "save":
        created = updated = 0
        conn = db()
        for i in request.form.getlist("row"):
            if not request.form.get(f"use_{i}"):
                continue
            d = parse_date(request.form.get(f"date_{i}"))
            opp = request.form.get(f"opp_{i}", "").strip()
            if not d or not opp:
                continue
            vals = (d, request.form.get(f"time_{i}", "").strip(), opp,
                    1 if request.form.get(f"home_{i}") == "1" else 0, request.form.get(f"loc_{i}", "").strip())
            uid = request.form.get(f"uid_{i}", "").strip() or None
            old = q("SELECT id FROM matches WHERE team_id=? AND ext_uid=?", (tid, uid), one=True) if uid else None
            if old:
                conn.execute("UPDATE matches SET date=?, time=?, opponent=?, home=?, location=? WHERE id=?", vals + (old["id"],))
                updated += 1
            else:
                conn.execute("""INSERT INTO matches (team_id, date, time, opponent, home, location, ext_uid)
                                VALUES (?,?,?,?,?,?,?)""", (tid,) + vals + (uid,))
                created += 1
        if request.form.get("rwk_name") is not None:
            conn.execute("UPDATE teams SET rwk_name=? WHERE id=?", (request.form.get("rwk_name", "").strip(), tid))
        conn.commit()
        flash(f"Import fertig: {created} neu, {updated} aktualisiert.", "ok")
        return redirect(url_for("team", tid=tid))

    # Schritt 1: Datei hochladen -> Vorschau
    rows, own = None, (request.form.get("rwk_name") or t["rwk_name"] or t["name"]).strip()
    if request.method == "POST":
        f = request.files.get("ics")
        raw = f.read(2_000_000) if f else b""
        text = raw.decode("utf-8", errors="replace")
        if "BEGIN:VCALENDAR" not in text:
            flash("Das ist keine Kalenderdatei. Bitte die .ics-Datei aus dem RWK-Onlinemelder wählen.", "error")
        else:
            rows = []
            for ev in parse_ics(text):
                opp, home = guess_pairing(ev.get("summary", ""), own)
                existing = q("SELECT id, date, time FROM matches WHERE team_id=? AND ext_uid=?", (tid, ev.get("uid")), one=True) \
                    if ev.get("uid") else None
                moved = None
                if existing and (existing["date"], existing["time"]) != (ev["date"], ev["time"]):
                    mv = q("""SELECT new_date FROM match_moves WHERE match_id=? AND status IN ('bestaetigt','offen')
                              ORDER BY id DESC LIMIT 1""", (existing["id"],), one=True)
                    if mv:
                        moved = existing["date"]
                rows.append(dict(ev, opp=opp, home=home, exists=existing is not None, moved=moved,
                                 past=ev["date"] < date.today().isoformat()))
            rows.sort(key=lambda r: (r["date"], r["time"]))
            if not rows:
                flash("In der Datei wurden keine Termine gefunden.", "error")
                rows = None
    return render_template("rwk_import.html", t=t, rows=rows, own=own)


# ---------------------------------------------------------------- Verein (nur Admin)
CLUB_FIELDS = {"club_name": "Vereinsname", "club_id": "Vereins-ID / Vereinsnummer",
               "club_place": "Ort", "club_assoc": "Verband / Gau / Kreis"}


@app.route("/verein", methods=["GET", "POST"])
@admin_required
def club():
    if request.method == "POST":
        conn = db()
        for k in CLUB_FIELDS:
            conn.execute("""INSERT INTO settings (key, value) VALUES (?,?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (k, request.form.get(k, "").strip()))
        conn.commit()
        flash("Vereinsdaten gespeichert.", "ok")
        return redirect(url_for("club"))
    values = {k: setting(k) for k in CLUB_FIELDS}
    values["club_name"] = values["club_name"] or VEREIN_NAME
    counts = q("""SELECT (SELECT COUNT(*) FROM users WHERE active=1) users, (SELECT COUNT(*) FROM teams) teams,
                  (SELECT COUNT(*) FROM opponents) opps""", one=True)
    return render_template("club.html", values=values, FIELDS=CLUB_FIELDS, counts=counts)


# ---------------------------------------------------------------- Gegner
def can_edit_opponents():
    return current_user()["role"] in ("admin", "leader")


@app.route("/gegner", methods=["GET", "POST"])
@login_required
def opponents():
    if not can_edit_opponents():
        abort(403)
    if request.method == "POST":
        oid = request.form.get("id", type=int)
        if request.form.get("delete") and oid:
            ex("DELETE FROM opponents WHERE id=?", (oid,))
            flash("Gegner gelöscht.", "ok")
        else:
            club_, team_, loc = (request.form.get(k, "").strip() for k in ("club", "team", "location"))
            if not club_:
                flash("Bitte den Verein angeben.", "error")
            elif oid:
                ex("UPDATE opponents SET club=?, team=?, location=? WHERE id=?", (club_, team_, loc, oid))
                flash("Gegner gespeichert.", "ok")
            else:
                ex("INSERT INTO opponents (club, team, location) VALUES (?,?,?)", (club_, team_, loc))
                flash("Gegner angelegt.", "ok")
        return redirect(url_for("opponents"))
    rows = q("SELECT * FROM opponents ORDER BY club, team")
    return render_template("opponents.html", rows=rows)


def opponent_choices():
    """Für die Auswahl beim Wettkampf: Anzeigename und Ort."""
    return [dict(label=(o["club"] + (" " + o["team"] if o["team"] else "")), location=o["location"])
            for o in q("SELECT * FROM opponents ORDER BY club, team")]


# ---------------------------------------------------------------- CSV-Import (Schützen, Gegner)
IMPORT_FIELDS = {
    "schuetzen": {"vorname": "Vorname", "nachname": "Nachname", "name": "Name (komplett)",
                  "player_id": "Spieler-ID", "pass_nr": "Pass-Nr.", "club": "Verein", "": "– ignorieren –"},
    "gegner": {"club": "Verein", "team": "Mannschaft", "location": "Ort / Schießstand", "": "– ignorieren –"},
}
HEADER_HINTS = {
    "vorname": ("vorname", "first"), "nachname": ("nachname", "familienname", "last", "zuname"),
    "name": ("name", "schütze", "schuetze"), "player_id": ("id", "startnummer", "startnr", "personennummer", "mitgliedsnummer", "spieler"),
    "pass_nr": ("pass",), "club": ("verein", "club"), "team": ("mannschaft", "team"), "location": ("ort", "stand", "halle"),
}


def read_csv(raw):
    """CSV mit unbekanntem Trennzeichen und Zeichensatz lesen."""
    import csv
    import io
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4000]
    delim = max((";", ",", "\t"), key=sample.count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    return rows, text


def guess_mapping(header, kind):
    fields = IMPORT_FIELDS[kind]
    out = []
    for h in header:
        hl = h.lower().strip()
        pick = ""
        for f in fields:
            if f and any(word in hl for word in HEADER_HINTS.get(f, ())):
                if f == "name" and ("vor" in hl or "nach" in hl):
                    continue
                if f in out:
                    continue
                pick = f
                break
        out.append(pick)
    return out


def make_username(name):
    import unicodedata
    base = unicodedata.normalize("NFKD", name.lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue"))
    base = "".join(c for c in base if c.isalnum() or c in " .-").strip().replace(" ", ".")
    base = base or "schuetze"
    cand, n = base, 2
    while q("SELECT 1 FROM users WHERE username=?", (cand,), one=True):
        cand, n = f"{base}{n}", n + 1
    return cand


@app.route("/import/<kind>", methods=["GET", "POST"])
@login_required
def csv_import(kind):
    if kind not in IMPORT_FIELDS:
        abort(404)
    if (kind == "schuetzen" and current_user()["role"] != "admin") or (kind == "gegner" and not can_edit_opponents()):
        abort(403)
    fields = IMPORT_FIELDS[kind]
    if request.method == "POST" and request.form.get("step") == "save":
        import csv as _csv
        import io as _io
        rows = list(_csv.reader(_io.StringIO(request.form.get("data", "")), delimiter=";"))
        has_header = request.form.get("header") == "1"
        mapping = [request.form.get(f"col_{i}", "") for i in range(len(rows[0]) if rows else 0)]
        body = rows[1:] if has_header else rows
        created, updated, skipped, accounts = 0, 0, 0, []
        conn = db()
        for r in body:
            rec = {}
            for i, f in enumerate(mapping):
                if f and i < len(r) and r[i].strip():
                    rec[f] = r[i].strip()
            if kind == "gegner":
                if not rec.get("club"):
                    skipped += 1
                    continue
                old = q("SELECT id FROM opponents WHERE lower(club)=lower(?) AND lower(team)=lower(?)",
                        (rec["club"], rec.get("team", "")), one=True)
                if old:
                    if rec.get("location"):
                        conn.execute("UPDATE opponents SET location=? WHERE id=?", (rec["location"], old["id"]))
                    updated += 1
                else:
                    conn.execute("INSERT INTO opponents (club, team, location) VALUES (?,?,?)",
                                 (rec["club"], rec.get("team", ""), rec.get("location", "")))
                    created += 1
                continue
            name = rec.get("name") or " ".join(x for x in (rec.get("vorname"), rec.get("nachname")) if x)
            if not name:
                skipped += 1
                continue
            old = None
            if rec.get("player_id"):
                old = q("SELECT * FROM users WHERE player_id=? AND player_id<>''", (rec["player_id"],), one=True)
            if not old:
                old = q("SELECT * FROM users WHERE lower(name)=lower(?)", (name,), one=True)
            if old:
                conn.execute("""UPDATE users SET player_id=COALESCE(NULLIF(?, ''), player_id),
                                pass_nr=COALESCE(NULLIF(?, ''), pass_nr), club=COALESCE(NULLIF(?, ''), club) WHERE id=?""",
                             (rec.get("player_id", ""), rec.get("pass_nr", ""), rec.get("club", ""), old["id"]))
                updated += 1
            else:
                username = make_username(name)
                pw = secrets.token_urlsafe(6)
                conn.execute("""INSERT INTO users (name, username, password_hash, role, must_change_pw, player_id, pass_nr, club)
                                VALUES (?,?,?,'shooter',1,?,?,?)""",
                             (name, username, generate_password_hash(pw), rec.get("player_id", ""),
                              rec.get("pass_nr", ""), rec.get("club", "")))
                conn.commit()
                accounts.append({"name": name, "username": username, "password": pw})
                created += 1
        conn.commit()
        flash(f"Import fertig: {created} neu, {updated} aktualisiert{f', {skipped} übersprungen' if skipped else ''}.", "ok")
        if accounts:
            return render_template("import_result.html", accounts=accounts)
        return redirect(url_for("users") if kind == "schuetzen" else url_for("opponents"))

    preview = None
    if request.method == "POST":
        f = request.files.get("csv")
        raw = f.read(2_000_000) if f else b""
        rows, text = read_csv(raw) if raw else ([], "")
        if not rows or max(len(r) for r in rows) < 1:
            flash("In der Datei wurden keine Daten gefunden. Bitte eine CSV-Datei wählen (aus Excel: Speichern unter → CSV).", "error")
        else:
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            mapping = guess_mapping(rows[0], kind)
            preview = {"rows": rows[:8], "count": len(rows), "mapping": mapping,
                       "header": any(mapping), "data": "\n".join(";".join('"' + c.replace('"', '""') + '"' for c in r) for r in rows)}
    return render_template("csv_import.html", kind=kind, fields=fields, preview=preview)


# ---------------------------------------------------------------- Fehler
@app.errorhandler(500)
@app.errorhandler(Exception)
def server_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException) and e.code != 500:
        return e
    import traceback
    orig = getattr(e, "original_exception", None) or e
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""INSERT INTO error_log (at, method, path, user_id, message, detail) VALUES (?,?,?,?,?,?)""",
                     (datetime.now().isoformat(timespec="seconds"), request.method, request.full_path[:300],
                      session.get("uid"), f"{type(orig).__name__}: {orig}"[:500],
                      "".join(traceback.format_exception(type(orig), orig, orig.__traceback__))[-6000:]))
        conn.execute("DELETE FROM error_log WHERE at < ?", ((datetime.now() - timedelta(days=LOG_KEEP_DAYS)).isoformat(),))
        conn.commit()
        conn.close()
    except Exception:
        pass
    app.logger.exception("Serverfehler")
    return render_template("error.html", code=500, msg="Da ist etwas schiefgelaufen. Der Fehler wurde für den Admin protokolliert."), 500


@app.errorhandler(403)
def forbidden(_e):
    return render_template("error.html", code=403, msg="Dafür hast du keine Berechtigung."), 403


@app.errorhandler(404)
def notfound(_e):
    return render_template("error.html", code=404, msg="Seite nicht gefunden."), 404


@app.errorhandler(400)
def badreq(e):
    return render_template("error.html", code=400, msg=getattr(e, "description", "Ungültige Anfrage.")), 400


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=os.environ.get("DEBUG") == "1")
