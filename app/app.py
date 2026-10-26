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
DISCIPLINES = ["Luftgewehr", "Luftgewehr Auflage", "Luftpistole", "Luftpistole Auflage", "Luftpistole Standard",
               "KK-Gewehr", "KK-Gewehr Auflage", "KK-Pistole", "Zimmerstutzen", "Zimmerstutzen Auflage",
               "Zimmerstutzen Standard", "Kyffhäuser", "Großkaliber", "Bogen"]
# Kürzel der Waffenart im DISAG-FLEX-Import (Vorlage "SchuetzenMitVereinWaffenart")
WAFFENART = {"Luftgewehr": "LG", "Luftgewehr Auflage": "LGA", "Luftpistole": "LP", "Luftpistole Auflage": "LPA",
             "Luftpistole Standard": "LPS", "KK-Gewehr": "KK", "KK-Gewehr Auflage": "KKA", "Zimmerstutzen": "ZS",
             "Zimmerstutzen Auflage": "ZSA", "Zimmerstutzen Standard": "ZSTRD", "Kyffhäuser": "KYFFH"}
GENDERS = {"": "–", "m": "männlich", "w": "weiblich", "d": "divers"}


def norm_gender(v):
    v = (v or "").strip().lower()
    if v in ("m", "männlich", "maennlich", "mann", "0", "male", "herr"):
        return "m"
    if v in ("w", "weiblich", "frau", "f", "1", "female", "dame"):
        return "w"
    return "d" if v in ("d", "divers") else ""


def norm_year(v):
    v = (v or "").strip()
    m_ = __import__("re").search(r"(19|20)\d\d", v)
    return m_.group(0) if m_ else ""

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
-- Gegner: Verein -> Mannschaften -> Schützen
CREATE TABLE IF NOT EXISTS opp_clubs (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  location TEXT NOT NULL DEFAULT '',
  club_nr TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS opp_teams (
  id INTEGER PRIMARY KEY,
  club_id INTEGER NOT NULL REFERENCES opp_clubs(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '',
  discipline TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS opp_shooters (
  id INTEGER PRIMARY KEY,
  club_id INTEGER NOT NULL REFERENCES opp_clubs(id) ON DELETE CASCADE,
  team_id INTEGER REFERENCES opp_teams(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  pass_nr TEXT NOT NULL DEFAULT '',
  player_id TEXT NOT NULL DEFAULT ''
);
-- Gegnerschützen eines Wettkampfs (mit Ringen und Stand)
CREATE TABLE IF NOT EXISTS match_opp (
  id INTEGER PRIMARY KEY,
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  shooter_id INTEGER REFERENCES opp_shooters(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  pass_nr TEXT NOT NULL DEFAULT '',
  player_id TEXT NOT NULL DEFAULT '',
  rings REAL,
  lane INTEGER,
  relay INTEGER,
  pos INTEGER NOT NULL DEFAULT 0
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
    ("users", "birth_year", "TEXT NOT NULL DEFAULT ''"),  # Jahrgang (für DISAG)
    ("users", "gender", "TEXT NOT NULL DEFAULT ''"),      # m | w | d (für DISAG)
    ("opp_shooters", "birth_year", "TEXT NOT NULL DEFAULT ''"),
    ("opp_shooters", "gender", "TEXT NOT NULL DEFAULT ''"),
    ("matches", "opp_team_id", "INTEGER"),                # Gegnermannschaft aus der Gegnerliste
    ("matches", "area", "TEXT NOT NULL DEFAULT ''"),       # LG | KK | '' = aus Disziplin der Mannschaft
    ("matches", "lanes", "INTEGER"),                       # Stände für diesen Wettkampf, NULL = Vereinseinstellung
    ("lineup", "lane", "INTEGER"),                         # Stand
    ("lineup", "relay", "INTEGER"),                        # Durchgang
    ("lineup", "pos", "INTEGER NOT NULL DEFAULT 0"),       # Reihenfolge / Paarung
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
    migrate_opponents(conn)
    conn.close()


def migrate_opponents(conn):
    """Alte flache Gegnerliste (Verein + Mannschaft) einmalig in Verein -> Mannschaften übernehmen."""
    if conn.execute("SELECT COUNT(*) FROM opp_clubs").fetchone()[0]:
        return
    old = conn.execute("SELECT club, team, location FROM opponents ORDER BY club, team").fetchall()
    clubs = {}
    for club_, team_, loc in old:
        key = club_.strip().lower()
        if key not in clubs:
            clubs[key] = conn.execute("INSERT INTO opp_clubs (name, location) VALUES (?,?)", (club_.strip(), loc)).lastrowid
        elif loc:
            conn.execute("UPDATE opp_clubs SET location=? WHERE id=? AND location=''", (loc, clubs[key]))
        conn.execute("INSERT INTO opp_teams (club_id, name) VALUES (?,?)", (clubs[key], team_.strip()))
    conn.commit()


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


def has_opp_rings(mid):
    return bool(q("SELECT 1 FROM match_opp WHERE match_id=? AND rings IS NOT NULL", (mid,), one=True))


def sync_opp_score(conn, mid):
    """Ringe Gegner = Summe der Gegnerschützen, sobald dort Ringe stehen."""
    r = conn.execute("SELECT SUM(rings), COUNT(rings) FROM match_opp WHERE match_id=?", (mid,)).fetchone()
    if r[1]:
        conn.execute("UPDATE matches SET opponent_score=? WHERE id=?", (r[0], mid))


RANGE_FIELDS = {"lanes_lg": ("Stände Luftgewehr / Luftpistole", 6),
                "lanes_kk": ("Stände Kleinkaliber", 4),
                "relay_minutes": ("Minuten pro Durchgang (für die Startzeiten)", 75)}


def range_setting(key):
    try:
        return max(1, int(setting(key) or RANGE_FIELDS[key][1]))
    except ValueError:
        return RANGE_FIELDS[key][1]


def match_area(m, t):
    if m["area"] in ("LG", "KK"):
        return m["area"]
    disc = (t["discipline"] or "").lower()
    return "KK" if ("kk" in disc or "klein" in disc or "groß" in disc or "gross" in disc) else "LG"


def match_lanes(m, area):
    return m["lanes"] or range_setting("lanes_kk" if area == "KK" else "lanes_lg")


def split_name(name):
    parts = (name or "").strip().rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", parts[0])


def starters(m, t):
    """Alle Schützen eines Wettkampfs (eigene + Gegner) mit Stand und Durchgang."""
    club = setting("club_name") or VEREIN_NAME
    own_side, opp_side = ("Heim", "Gast") if m["home"] else ("Gast", "Heim")
    own = [dict(key=f"u{r['user_id']}", side=own_side, own=True, name=r["name"], lane=r["lane"], relay=r["relay"],
                pos=r["pos"], rings=r["rings"], pass_nr=r["pass_nr"], player_id=r["player_id"],
                club=r["club"] or club, team=t["name"], birth_year=r["birth_year"], gender=r["gender"])
           for r in q("""SELECT l.*, u.name, u.pass_nr, u.player_id, u.club, u.birth_year, u.gender FROM lineup l JOIN users u ON u.id=l.user_id
                         WHERE l.match_id=? ORDER BY l.pos, u.name""", (m["id"],))]
    ot = q("""SELECT ot.name team, c.name club FROM opp_teams ot JOIN opp_clubs c ON c.id=ot.club_id WHERE ot.id=?""",
           (m["opp_team_id"],), one=True) if m["opp_team_id"] else None
    opp = [dict(key=f"o{r['id']}", side=opp_side, own=False, name=r["name"], lane=r["lane"], relay=r["relay"],
                pos=r["pos"], rings=r["rings"], pass_nr=r["pass_nr"], player_id=r["player_id"],
                club=ot["club"] if ot else m["opponent"], team=opp_label(ot["club"], ot["team"]) if ot else m["opponent"],
                birth_year=r["s_year"] or "", gender=r["s_gender"] or "")
           for r in q("""SELECT mo.*, os.birth_year s_year, os.gender s_gender FROM match_opp mo
                         LEFT JOIN opp_shooters os ON os.id=mo.shooter_id WHERE mo.match_id=? ORDER BY mo.pos, mo.id""", (m["id"],))]
    return own, opp


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
                JOIN teams t ON t.id=tm.team_id WHERE tm.user_id=u.id) teams,
                (SELECT GROUP_CONCAT(tm.team_id) FROM team_members tm WHERE tm.user_id=u.id) team_ids,
                (SELECT GROUP_CONCAT(g.name, ', ') FROM user_groups ug JOIN groups g ON g.id=ug.group_id WHERE ug.user_id=u.id) group_names,
                (SELECT GROUP_CONCAT(ug.group_id) FROM user_groups ug WHERE ug.user_id=u.id) group_ids
                FROM users u ORDER BY u.name COLLATE NOCASE""")
    temp = temp_locked_names()
    rows = [dict(r, temp_locked=r["username"].lower() in temp) for r in rows]
    show = request.args.get("zeige", "aktiv")
    counts = {"aktiv": sum(1 for r in rows if r["active"]), "gesperrt": sum(1 for r in rows if not r["active"])}
    if show == "gesperrt":
        rows = [r for r in rows if not r["active"]]
    else:
        show = "aktiv"
        rows = [r for r in rows if r["active"]]
    flt = {"q": request.args.get("q", "").strip(), "rolle": request.args.get("rolle", ""),
           "team": request.args.get("team", type=int), "gruppe": request.args.get("gruppe", type=int),
           "verein": request.args.get("verein", ""), "nie": request.args.get("nie") == "1"}
    clubs = sorted({(r["club"] or "").strip() for r in rows if (r["club"] or "").strip()}, key=str.lower)
    if flt["q"]:
        needle = flt["q"].lower()
        rows = [r for r in rows if needle in " ".join(str(r[k] or "") for k in ("name", "username", "player_id", "pass_nr", "club")).lower()]
    if flt["rolle"] in ROLES:
        rows = [r for r in rows if r["role"] == flt["rolle"]]
    if flt["team"]:
        rows = [r for r in rows if str(flt["team"]) in (r["team_ids"] or "").split(",")]
    if flt["gruppe"]:
        rows = [r for r in rows if str(flt["gruppe"]) in (r["group_ids"] or "").split(",")]
    if flt["verein"] == "-":
        rows = [r for r in rows if not (r["club"] or "").strip()]
    elif flt["verein"]:
        rows = [r for r in rows if (r["club"] or "").strip().lower() == flt["verein"].lower()]
    if flt["nie"]:
        rows = [r for r in rows if not r["last_login"]]
    filtered = bool(flt["q"] or flt["rolle"] in ROLES or flt["team"] or flt["gruppe"] or flt["verein"] or flt["nie"])
    return render_template("users.html", users=rows, show=show, counts=counts, flt=flt, filtered=filtered, clubs=clubs,
                           BULK=BULK_ACTIONS,
                           back=request.full_path.rstrip("?"),
                           teams=q("SELECT id, name FROM teams ORDER BY name"), groups=q("SELECT id, name FROM groups ORDER BY name"))


def temp_locked_names():
    """Benutzernamen, die wegen zu vieler Fehlversuche gerade vorübergehend gesperrt sind."""
    since = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    return {r["n"].lower() for r in q("""SELECT username n FROM login_log WHERE ok=0 AND at>=?
                                         GROUP BY lower(username) HAVING COUNT(*)>=?""", (since, MAX_FAILED_LOGINS))}


BULK_ACTIONS = {"sperren": "Sperren", "entsperren": "Entsperren", "loeschen": "Endgültig löschen",
                **{f"rolle_{k}": f"Rolle: {v}" for k, v in ROLES.items()}}


@app.route("/benutzer/massen", methods=["POST"])
@admin_required
def users_bulk():
    """Mehrere Benutzer auf einmal sperren, entsperren, löschen oder eine Rolle geben."""
    back = safe_next(request.form.get("back"), url_for("users"))
    me_id = current_user()["id"]
    ids = [i for i in {int(x) for x in request.form.getlist("ids") if x.isdigit()} if i != me_id]
    act = request.form.get("action", "")
    if not ids or act not in BULK_ACTIONS:
        flash("Bitte Benutzer und eine Aktion auswählen." if not ids else "Unbekannte Aktion.", "error")
        return redirect(back)
    if act == "loeschen" and request.form.get("confirm", "").strip().upper() != "LÖSCHEN":
        flash("Zum Löschen bitte LÖSCHEN in das Bestätigungsfeld tippen. Es wurde nichts gelöscht.", "error")
        return redirect(back)
    marks = ",".join("?" * len(ids))
    conn = db()
    if act == "sperren":
        conn.execute(f"UPDATE users SET active=0, locked_at=?, pw_ver=pw_ver+1 WHERE active=1 AND id IN ({marks})",
                     [datetime.now().isoformat(timespec="seconds")] + ids)
        msg = f"{len(ids)} Benutzer gesperrt."
    elif act == "entsperren":
        conn.execute(f"UPDATE users SET active=1, lock_reason='', locked_at=NULL WHERE id IN ({marks})", ids)
        msg = f"{len(ids)} Benutzer entsperrt."
    elif act == "loeschen":
        conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
        for i in ids:
            try:
                os.remove(os.path.join(AVATAR_DIR, f"{i}.jpg"))
            except FileNotFoundError:
                pass
        msg = f"{len(ids)} Benutzer endgültig gelöscht."
    else:
        role = act.split("_", 1)[1]
        conn.execute(f"UPDATE users SET role=? WHERE id IN ({marks})", [role] + ids)
        if role not in ("admin", "leader"):
            conn.execute(f"UPDATE teams SET leader_id=NULL WHERE leader_id IN ({marks})", ids)
        msg = f"{len(ids)} Benutzer haben jetzt die Rolle {ROLES[role]}."
    conn.commit()
    skipped_self = len(set(request.form.getlist("ids"))) > len(ids)
    flash(msg + (" Dein eigenes Konto wurde ausgelassen." if skipped_self else ""), "ok")
    return redirect(back)


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
        extra["birth_year"] = norm_year(request.form.get("birth_year"))
        extra["gender"] = norm_gender(request.form.get("gender"))
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
                ex("""UPDATE users SET name=?, username=?, role=?, active=?, player_id=?, pass_nr=?, club=?,
                      birth_year=?, gender=? WHERE id=?""",
                   (name, username, role, active, extra["player_id"], extra["pass_nr"], extra["club"],
                    extra["birth_year"], extra["gender"], uid))
                if pw:
                    ex("UPDATE users SET password_hash=?, must_change_pw=1, pw_ver=pw_ver+1 WHERE id=?",
                       (generate_password_hash(pw), uid))
                if role not in ("admin", "leader"):  # kein Mannschaftsführer mehr
                    ex("UPDATE teams SET leader_id=NULL WHERE leader_id=?", (uid,))
                save_user_groups(uid)
                flash("Benutzer gespeichert.", "ok")
            else:
                new_uid = ex("""INSERT INTO users (name, username, role, active, password_hash, must_change_pw, player_id, pass_nr, club,
                      birth_year, gender) VALUES (?,?,?,?,?,1,?,?,?,?,?)""", (name, username, role, active, generate_password_hash(pw),
                                                    extra["player_id"], extra["pass_nr"], extra["club"], extra["birth_year"], extra["gender"]))
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
    member_of = q("""SELECT t.id, t.name, tm.reserve, t.leader_id = ? is_leader FROM team_members tm JOIN teams t ON t.id=tm.team_id
                     WHERE tm.user_id=? UNION
                     SELECT t.id, t.name, 0, 1 FROM teams t WHERE t.leader_id=? AND t.id NOT IN
                     (SELECT team_id FROM team_members WHERE user_id=?) ORDER BY 2""", (uid, uid, uid, uid)) if uid else []
    return render_template("user_edit.html", user=user, all_groups=q("SELECT * FROM groups ORDER BY name"), member_of=member_of, GENDERS=GENDERS,
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


def maintenance_status():
    """Von den Wartungsskripten (scripts/*.sh) geschriebener Serverstatus, siehe data/wartung.json."""
    import json
    path = os.path.join(os.path.dirname(DB_PATH), "wartung.json")
    try:
        with open(path) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return None
    now = datetime.now()

    def age(key):
        try:
            return (now - datetime.fromisoformat(st[key])).total_seconds() / 3600
        except (KeyError, TypeError, ValueError):
            return None
    st["backup_age_h"] = age("last_backup")
    st["check_age_h"] = age("checked_at")
    st["maint_age_h"] = age("last_maintenance")
    st["pending_list"] = st.get("pending_list") or []
    st["app_new"] = st.get("app_new") or []
    st["problems"] = [p_ for p_ in (st.get("problems") or "").split(";") if p_.strip()]
    return st


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
    maint = maintenance_status()
    if maint:
        if maint["backup_age_h"] is None or maint["backup_age_h"] > 36:
            warnings.append("Die letzte Sicherung ist älter als 36 Stunden. Auf dem Server prüfen: ./scripts/status.sh")
        if maint.get("pending_security"):
            warnings.append(f"{maint['pending_security']} Sicherheitsupdates für Linux stehen aus. Sie werden nachts automatisch "
                            "installiert, sofort mit: ./scripts/wartung.sh")
        if maint.get("reboot_required"):
            warnings.append("Der Server braucht einen Neustart (Kernel-Update). Er erfolgt automatisch um 04:30.")
        for p_ in maint["problems"]:
            warnings.append(f"Letzte Wartung: {p_}")
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
    return render_template("admin_home.html", online=online, logins=logins, stats=stats, errors=errors, maint=maint,
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
        ot = q("""SELECT ot.*, c.name club_name, c.location club_loc FROM opp_teams ot JOIN opp_clubs c ON c.id=ot.club_id
                  WHERE ot.id=?""", (request.form.get("opp_team_id", type=int),), one=True)
        if ot:   # Gegner aus der Liste: Name und (auswärts) Ort übernehmen
            f["opponent"] = opp_label(ot["club_name"], ot["name"])
            if not home and not f["location"]:
                f["location"] = ot["club_loc"]
        opp_team_id = ot["id"] if ot else None
        if not d or not f["opponent"]:
            flash("Datum und Gegner sind Pflicht.", "error")
        else:
            if m:
                if has_opp_rings(mid):   # Summe kommt aus den Gegnerschützen
                    opp = m["opponent_score"]
                ex("""UPDATE matches SET date=?, time=?, opponent=?, home=?, location=?, opponent_score=?,
                      notes=?, meet=?, opp_team_id=? WHERE id=?""",
                   (d, f["time"], f["opponent"], home, f["location"], opp, f["notes"], f["meet"], opp_team_id, mid))
            else:
                mid = ex("""INSERT INTO matches (team_id, date, time, opponent, home, location, opponent_score, notes, meet, opp_team_id)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (t["id"], d, f["time"], f["opponent"], home, f["location"], opp, f["notes"], f["meet"], opp_team_id))
            flash("Wettkampf gespeichert.", "ok")
            return redirect(url_for("match", mid=mid))
        m = dict(m or {}, **f, date=request.form.get("date"), home=home, opponent_score=opp, opp_team_id=opp_team_id)
    return render_template("match_edit.html", m=m, t=t, opponents=opponent_choices(),
                           opp_rings=bool(mid and has_opp_rings(mid)))


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
    opp_rows = q("SELECT * FROM match_opp WHERE match_id=? ORDER BY pos, id", (mid,))
    opp_team = q("""SELECT ot.*, c.name club_name, c.id club_id FROM opp_teams ot JOIN opp_clubs c ON c.id=ot.club_id
                    WHERE ot.id=?""", (m["opp_team_id"],), one=True) if m["opp_team_id"] else None
    taken = {r["shooter_id"] for r in opp_rows}
    opp_pool = [x for x in q("""SELECT os.*, ot.name team_name FROM opp_shooters os LEFT JOIN opp_teams ot ON ot.id=os.team_id
                                WHERE os.club_id=? ORDER BY (os.team_id=?) DESC, os.name""",
                             (opp_team["club_id"], opp_team["id"])) if x["id"] not in taken] if opp_team else []
    area = match_area(m, t)
    placed = sum(1 for r in q("SELECT 1 FROM lineup WHERE match_id=? AND lane IS NOT NULL AND relay IS NOT NULL", (mid,))) + \
        sum(1 for r in opp_rows if r["lane"] and r["relay"])
    plan = {"area": area, "lanes": match_lanes(m, area), "placed": placed,
            "count": sum(1 for r in rows if r["in_lineup"]) + len(opp_rows)}
    return render_template("match.html", m=m, t=t, rows=rows, total=total, moves=moves, open_move=open_move,
                           opp_rows=opp_rows, opp_team=opp_team, opp_pool=opp_pool, plan=plan,
                           opp_sum=sum(r["rings"] for r in opp_rows if r["rings"] is not None),
                           opp_has_rings=any(r["rings"] is not None for r in opp_rows),
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
    keep = {r["user_id"]: r for r in q("SELECT user_id, lane, relay, pos FROM lineup WHERE match_id=?", (mid,))}
    conn = db()
    conn.execute("DELETE FROM lineup WHERE match_id=?", (mid,))
    for uid in chosen:
        k = keep.get(uid)
        conn.execute("INSERT INTO lineup (match_id, user_id, rings, lane, relay, pos) VALUES (?,?,?,?,?,?)",
                     (mid, uid, parse_num(request.form.get(f"rings_{uid}")),
                      k["lane"] if k else None, k["relay"] if k else None, k["pos"] if k else 99))
    for r in q("SELECT id FROM match_opp WHERE match_id=?", (mid,)):
        if f"opp_rings_{r['id']}" in request.form:
            conn.execute("UPDATE match_opp SET rings=? WHERE id=?", (parse_num(request.form.get(f"opp_rings_{r['id']}")), r["id"]))
    if "opponent_score" in request.form:
        conn.execute("UPDATE matches SET opponent_score=? WHERE id=?",
                     (parse_num(request.form.get("opponent_score")), mid))
    sync_opp_score(conn, mid)
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


# ---------------------------------------------------------------- Gegnerschützen eines Wettkampfs
@app.route("/wettkampf/<int:mid>/gegnerschuetzen", methods=["POST"])
@login_required
def match_opp_edit(mid):
    m = get_match(mid)
    if not can_manage(get_team(m["team_id"])):
        abort(403)
    act = "remove" if request.form.get("remove_id") else request.form.get("action")
    conn = db()
    nxt = (conn.execute("SELECT COALESCE(MAX(pos), 0) + 1 FROM match_opp WHERE match_id=?", (mid,)).fetchone()[0])
    if act == "add":
        sid = request.form.get("shooter_id", type=int)
        name = request.form.get("name", "").strip()
        if sid:
            sh = conn.execute("SELECT * FROM opp_shooters WHERE id=?", (sid,)).fetchone()
            if sh and not conn.execute("SELECT 1 FROM match_opp WHERE match_id=? AND shooter_id=?", (mid, sid)).fetchone():
                conn.execute("""INSERT INTO match_opp (match_id, shooter_id, name, pass_nr, player_id, pos)
                                VALUES (?,?,?,?,?,?)""", (mid, sid, sh["name"], sh["pass_nr"], sh["player_id"], nxt))
                flash(f"{sh['name']} hinzugefügt.", "ok")
        elif name:
            pass_nr = request.form.get("pass_nr", "").strip()
            sid = None
            ot = conn.execute("SELECT club_id FROM opp_teams WHERE id=?", (m["opp_team_id"],)).fetchone() if m["opp_team_id"] else None
            if ot and request.form.get("save") == "1":   # gleich in die Stammliste des Gegnervereins
                sid = conn.execute("INSERT INTO opp_shooters (club_id, team_id, name, pass_nr) VALUES (?,?,?,?)",
                                   (ot[0], m["opp_team_id"], name, pass_nr)).lastrowid
            conn.execute("INSERT INTO match_opp (match_id, shooter_id, name, pass_nr, pos) VALUES (?,?,?,?,?)",
                         (mid, sid, name, pass_nr, nxt))
            flash(f"{name} hinzugefügt.", "ok")
    elif act == "team":   # alle Stammschützen der Gegnermannschaft übernehmen
        if m["opp_team_id"]:
            n = 0
            for sh in conn.execute("SELECT * FROM opp_shooters WHERE team_id=? ORDER BY name", (m["opp_team_id"],)).fetchall():
                if not conn.execute("SELECT 1 FROM match_opp WHERE match_id=? AND shooter_id=?", (mid, sh["id"])).fetchone():
                    conn.execute("""INSERT INTO match_opp (match_id, shooter_id, name, pass_nr, player_id, pos)
                                    VALUES (?,?,?,?,?,?)""", (mid, sh["id"], sh["name"], sh["pass_nr"], sh["player_id"], nxt + n))
                    n += 1
            flash(f"{n} Schützen übernommen." if n else "Keine weiteren Stammschützen in dieser Mannschaft.", "ok" if n else "info")
    elif act == "remove":
        conn.execute("DELETE FROM match_opp WHERE id=? AND match_id=?", (request.form.get("remove_id", type=int), mid))
        sync_opp_score(conn, mid)
        flash("Gegnerschütze entfernt.", "ok")
    conn.commit()
    return redirect(url_for("match", mid=mid) + "#gegner")


# ---------------------------------------------------------------- Standplan (LG/KK, Stände, Durchgänge)
def auto_assign(m, t, lanes, mode):
    own, opp = starters(m, t)
    if mode == "heim_zuerst":
        home, guest = (own, opp) if m["home"] else (opp, own)
        order = home + guest
    else:   # abwechselnd: Paarungen 1 gegen 1 stehen nebeneinander im selben Durchgang
        order = []
        for i in range(max(len(own), len(opp))):
            order += [x for x in (own[i] if i < len(own) else None, opp[i] if i < len(opp) else None) if x]
    conn = db()
    for i, s_ in enumerate(order):
        relay, lane = i // lanes + 1, i % lanes + 1
        if s_["own"]:
            conn.execute("UPDATE lineup SET relay=?, lane=?, pos=? WHERE match_id=? AND user_id=?",
                         (relay, lane, i, m["id"], int(s_["key"][1:])))
        else:
            conn.execute("UPDATE match_opp SET relay=?, lane=?, pos=? WHERE id=? AND match_id=?",
                         (relay, lane, i, int(s_["key"][1:]), m["id"]))
    conn.commit()
    return len(order)


@app.route("/wettkampf/<int:mid>/standplan", methods=["GET", "POST"])
@login_required
def standplan(mid):
    m = get_match(mid)
    t = get_team(m["team_id"])
    manage = can_manage(t)
    if request.method == "POST":
        if not manage:
            abort(403)
        act = request.form.get("action")
        if act == "settings":
            area = request.form.get("area") if request.form.get("area") in ("LG", "KK") else ""
            lanes = request.form.get("lanes", type=int)
            lanes = min(max(lanes, 1), 60) if lanes else None
            ex("UPDATE matches SET area=?, lanes=? WHERE id=?", (area, lanes, mid))
            m = get_match(mid)
            n = auto_assign(m, t, match_lanes(m, match_area(m, t)), request.form.get("mode", "abwechselnd"))
            flash(f"{n} Schützen automatisch auf Stände verteilt." if n else "Noch keine Schützen aufgestellt.", "ok" if n else "info")
        elif act == "save":
            own, opp = starters(m, t)
            lanes = match_lanes(m, match_area(m, t))
            seen, conn, clash = {}, db(), []
            for s_ in own + opp:
                relay = request.form.get(f"relay_{s_['key']}", type=int)
                lane = request.form.get(f"lane_{s_['key']}", type=int)
                relay = relay if relay and 1 <= relay <= 20 else None
                lane = lane if lane and 1 <= lane <= lanes else None
                if relay and lane:
                    if (relay, lane) in seen:
                        clash.append(f"Durchgang {relay}, Stand {lane}: {seen[(relay, lane)]} und {s_['name']}")
                    seen[(relay, lane)] = s_["name"]
                if s_["own"]:
                    conn.execute("UPDATE lineup SET relay=?, lane=? WHERE match_id=? AND user_id=?",
                                 (relay, lane, mid, int(s_["key"][1:])))
                else:
                    conn.execute("UPDATE match_opp SET relay=?, lane=? WHERE id=? AND match_id=?",
                                 (relay, lane, int(s_["key"][1:]), mid))
            conn.commit()
            if clash:
                flash("Gespeichert, aber doppelt belegt: " + "; ".join(clash), "error")
            else:
                flash("Standplan gespeichert.", "ok")
        return redirect(url_for("standplan", mid=mid))
    area = match_area(m, t)
    lanes = match_lanes(m, area)
    own, opp = starters(m, t)
    everyone = own + opp
    relays = max([s_["relay"] or 0 for s_ in everyone] + [1])
    grid = [{"relay": r, "start": relay_start(m, r),
             "lanes": [next((s_ for s_ in everyone if s_["relay"] == r and s_["lane"] == ln), None) for ln in range(1, lanes + 1)]}
            for r in range(1, relays + 1)]
    unplaced = [s_ for s_ in everyone if not (s_["relay"] and s_["lane"])]
    need = -(-len(everyone) // lanes) if everyone else 1
    missing = [s_["name"] for s_ in everyone if not (s_["pass_nr"] or s_["player_id"])]
    no_year = sum(1 for s_ in everyone if not (s_["birth_year"] and s_["gender"]))
    return render_template("standplan.html", m=m, t=t, manage=manage, area=area, lanes=lanes, own=own, opp=opp,
                           waffenart=WAFFENART.get(t["discipline"], ""), missing=missing, no_year=no_year,
                           grid=grid, unplaced=unplaced, relay_choices=range(1, max(relays, need) + 2),
                           need=need, default_lanes=range_setting("lanes_kk" if area == "KK" else "lanes_lg"))


def relay_start(m, relay):
    """Startzeit eines Durchgangs: Wettkampfbeginn + (Durchgang-1) x Minuten pro Durchgang."""
    if not m["time"]:
        return ""
    try:
        h, mi = (int(x) for x in m["time"].split(":")[:2])
    except ValueError:
        return ""
    total = h * 60 + mi + (relay - 1) * range_setting("relay_minutes")
    return f"{total // 60 % 24:02d}:{total % 60:02d}"


# ---------------------------------------------------------------- DISAG-Export
DISAG_COLUMNS = ["Durchgang", "Startzeit", "Stand", "Nachname", "Vorname", "Pass-Nr", "Spieler-ID",
                 "Verein", "Mannschaft", "Heim/Gast", "Disziplin", "Datum", "Wettkampf"]


@app.route("/wettkampf/<int:mid>/disag.csv")
@login_required
def disag_export(mid):
    """Startliste für die DISAG-Anlage: ein Schütze pro Zeile, sortiert nach Durchgang und Stand.
    Format: Semikolon, Windows-Zeichensatz (cp1252) wie die DISAG-Software. Spalten siehe DISAG_COLUMNS."""
    import csv
    import io
    m = get_match(mid)
    t = get_team(m["team_id"])
    if not can_manage(t):
        abort(403)
    own, opp = starters(m, t)
    rows = sorted(own + opp, key=lambda s_: (s_["relay"] or 99, s_["lane"] or 99, s_["pos"]))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(DISAG_COLUMNS)
    title = f"{t['name']} - {m['opponent']}"
    for s_ in rows:
        first, last = split_name(s_["name"])
        w.writerow([s_["relay"] or "", relay_start(m, s_["relay"] or 1) if s_["relay"] else "", s_["lane"] or "",
                    last, first, s_["pass_nr"] or "", s_["player_id"] or "", s_["club"] or "", s_["team"] or "",
                    s_["side"], t["discipline"] or "", fmt_date_plain(m["date"]), title])
    data = buf.getvalue().encode("cp1252", errors="replace")
    name = f"DISAG_{m['date']}_{t['name']}".replace(" ", "_")
    import re as _re
    name = _re.sub(r"[^A-Za-z0-9_.-]", "", name) + ".csv"
    return app.response_class(data, mimetype="text/csv; charset=windows-1252",
                              headers={"Content-Disposition": f'attachment; filename="{name}"'})


FLEX_COLUMNS = ["Ausweisnummer", "Name", "Vorname", "Verein", "Jahrgang", "Waffenart", "Geschlecht"]


def flex_csv(rows, waffenart):
    """DISAG-FLEX-Import "SchuetzenMitVereinWaffenart": Semikolon, Windows-Zeichensatz, CRLF."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(FLEX_COLUMNS)
    for r in rows:
        first, last = split_name(r["name"])
        w.writerow([r["ausweis"], last, first, r["club"] or "", r["birth_year"] or "", waffenart, r["gender"] or ""])
    return buf.getvalue().encode("cp1252", errors="replace")


def csv_response(data, name):
    import re as _re
    name = _re.sub(r"[^A-Za-z0-9_.-]", "", name.replace(" ", "_")) + ".csv"
    return app.response_class(data, mimetype="text/csv; charset=windows-1252",
                              headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.route("/wettkampf/<int:mid>/disag-flex.csv")
@login_required
def disag_flex_export(mid):
    """Alle Schützen eines Wettkampfs (eigene + Gegner) im FLEX-Format, sortiert nach Durchgang und Stand.
    Ohne Pass-Nr./Spieler-ID wird eine feste Ersatznummer vergeben (90000 + Benutzer-Nr. bzw. 95000 + Gegner-Nr.)."""
    m = get_match(mid)
    t = get_team(m["team_id"])
    if not can_manage(t):
        abort(403)
    own, opp = starters(m, t)
    rows = []
    for s_ in sorted(own + opp, key=lambda x: (x["relay"] or 99, x["lane"] or 99, x["pos"])):
        n = int(s_["key"][1:])
        rows.append(dict(s_, ausweis=s_["pass_nr"] or s_["player_id"] or str((90000 if s_["own"] else 95000) + n)))
    return csv_response(flex_csv(rows, WAFFENART.get(t["discipline"], "")), f"{m['date']}_{t['name']}_FLEX_SchuetzenMitVereinWaffenart")


@app.route("/mannschaft/<int:tid>/disag-flex.csv")
@login_required
def team_flex_export(tid):
    """Alle Schützen einer Mannschaft im FLEX-Format, z. B. um sie einmalig in DISAG anzulegen."""
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    club = setting("club_name") or VEREIN_NAME
    rows = [dict(name=r["name"], club=r["club"] or club, birth_year=r["birth_year"], gender=r["gender"],
                 ausweis=r["pass_nr"] or r["player_id"] or str(90000 + r["id"]))
            for r in q("""SELECT u.* FROM team_members tm JOIN users u ON u.id=tm.user_id
                          WHERE tm.team_id=? AND u.active=1 ORDER BY u.name""", (tid,))]
    return csv_response(flex_csv(rows, WAFFENART.get(t["discipline"], "")), f"{t['name']}_FLEX_SchuetzenMitVereinWaffenart")


def fmt_date_plain(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso or ""


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

    events, ev, in_sub = [], None, False
    for line in lines:
        if line.startswith("BEGIN:") and line != "BEGIN:VEVENT" and ev is not None:
            in_sub = True                                       # z. B. VALARM: eigene DESCRIPTION ignorieren
        elif line.startswith("END:") and line != "END:VEVENT" and ev is not None:
            in_sub = False
        elif in_sub:
            continue
        elif line == "BEGIN:VEVENT":
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
                ev[name.lower()] = " ".join(unescape(val).split())
    return events


RWK_PREFIX = r"^\s*(?:RWK|Runde|Wettkampf|WK)\s*\d+\s*[-–:]\s*"


def split_pairing(summary):
    """'RWK 01 - SV A 1 : SV B 2' -> ('RWK 01', 'SV A 1', 'SV B 2'). Heim steht links."""
    import re
    rnd = ""
    m_ = re.match(RWK_PREFIX, summary or "", re.I)
    if m_:
        rnd = m_.group(0).strip(" -–:")
        summary = summary[m_.end():]
    for sep in (r"\s+:\s+", r"\s+(?:vs\.?|gegen)\s+", r"\s+[-–]\s+", r"\s*:\s*"):
        parts = re.split(sep, summary.strip(), maxsplit=1)
        if len(parts) == 2 and all(p.strip() for p in parts):
            return rnd, parts[0].strip(), parts[1].strip()
    return rnd, summary.strip(), ""


def rwk_class_info(description):
    """Aus 'RWK-Luftgewehr 2026-2027 Bereichsklasse A Nord RWK 01 - …' Disziplin, Saison und Klasse lesen."""
    import re
    m_ = re.search(r"RWK-?\s*([A-Za-zÄÖÜäöüß. -]+?)\s*(\d{4}\s*[-/]\s*\d{2,4})\s*(.*?)\s*(?:RWK\s*\d+|$)", description or "")
    if not m_:
        return {}
    return {"discipline": m_.group(1).strip(" -"), "season": m_.group(2).replace(" ", "").replace("-", "/"),
            "league": m_.group(3).strip()}


def split_team_label(label):
    """'SV Diana Ennerich 1' -> ('SV Diana Ennerich', '1')."""
    import re
    m_ = re.match(r"^(.*?)\s+(\d+|[IVX]+)$", label.strip())
    return (m_.group(1), m_.group(2)) if m_ else (label.strip(), "")


def guess_pairing(summary, own_name):
    """Aus 'Heim : Gast' (auch mit 'RWK 01 - ' davor) den Gegner und Heim/Auswärts ableiten."""
    _rnd, left, right = split_pairing(summary)
    if not right:
        return summary, 1
    own = (own_name or "").lower().strip()
    if own and own in right.lower() and own not in left.lower():
        return left, 0
    if own and own in left.lower():
        return right, 1
    return right, 1


@app.route("/mannschaft/<int:tid>/rwk-import/entfernen", methods=["POST"])
@login_required
def rwk_import_remove(tid):
    """Alle importierten Termine ohne Ergebnisse dieser Mannschaft löschen (z. B. nach falschem Import)."""
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    if request.form.get("confirm") != "1":
        flash("Bitte bestätigen, dass die importierten Termine gelöscht werden sollen.", "error")
        return redirect(url_for("rwk_import", tid=tid))
    ex("""DELETE FROM matches WHERE team_id=? AND ext_uid IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM lineup l WHERE l.match_id=matches.id AND l.rings IS NOT NULL)
              AND NOT EXISTS (SELECT 1 FROM match_opp o WHERE o.match_id=matches.id AND o.rings IS NOT NULL)""", (tid,))
    ex("UPDATE teams SET rwk_name='' WHERE id=?", (tid,))
    flash("Importierte Termine ohne Ergebnis gelöscht. Jetzt kannst du den richtigen Wettkampfplan importieren.", "ok")
    return redirect(url_for("rwk_import", tid=tid))


def rwk_teams(events):
    """Alle Mannschaften eines Wettkampfplans mit ihrem Heim-Schießstand."""
    teams = {}
    for ev in events:
        _r, home, guest = split_pairing(ev.get("summary", ""))
        if not guest:
            continue
        teams.setdefault(home, ev.get("location", ""))
        if not teams[home] and ev.get("location"):
            teams[home] = ev["location"]
        teams.setdefault(guest, "")
    return teams


def resolve_own(name, teams):
    """Eigene Mannschaft im Plan finden, auch bei kleinen Tippfehlern im Vereinsnamen ('Eisnebach' -> 'Eisenbach').
    Die Mannschaftsnummer muss genau stimmen: 'SV Eisenbach 2' wird nie zu 'SV Eisenbach 3'."""
    import difflib
    name = " ".join((name or "").split())
    if not name:
        return None
    for t_ in teams:
        if t_.lower() == name.lower():
            return t_
    club, nr = split_team_label(name)
    cands = [t_ for t_ in teams if split_team_label(t_)[1] == nr]
    if not nr and len(cands) != len(teams):
        return None
    hits = difflib.get_close_matches(club.lower(), [split_team_label(t_)[0].lower() for t_ in cands], n=2, cutoff=0.8)
    found = [t_ for t_ in cands if split_team_label(t_)[0].lower() in hits]
    return found[0] if len(found) == 1 else None


@app.route("/mannschaft/<int:tid>/rwk-import", methods=["GET", "POST"])
@login_required
def rwk_import(tid):
    t = get_team(tid)
    if not can_manage(t):
        abort(403)
    step = request.form.get("step") if request.method == "POST" else None
    text = request.form.get("ics_text", "")
    if request.method == "POST" and step != "save" and not text:
        f = request.files.get("ics")
        raw = f.read(2_000_000) if f else b""
        text = raw.decode("utf-8", errors="replace")
    events = parse_ics(text) if "BEGIN:VCALENDAR" in text else []
    teams = rwk_teams(events)
    info = next((rwk_class_info(e.get("description")) for e in events if rwk_class_info(e.get("description"))), {})

    # Schritt 2: Vorschau bestätigt -> Gegner und Wettkämpfe anlegen oder aktualisieren
    if step == "save":
        own = request.form.get("rwk_name", "").strip()
        conn = db()
        new_opp = 0
        if request.form.get("add_opps") == "1":
            disc = info.get("discipline", "")
            for label, loc in teams.items():
                if label.lower() == own.lower():
                    continue
                club_name, team_name = split_team_label(label)
                before = conn.total_changes
                cid = find_or_create_opp_club(conn, club_name, loc)
                find_or_create_opp_team(conn, cid, team_name, disc if disc in DISCIPLINES else "")
                new_opp += conn.total_changes > before
            conn.commit()
        created = updated = 0
        for i in request.form.getlist("row"):
            if not request.form.get(f"use_{i}"):
                continue
            d = parse_date(request.form.get(f"date_{i}"))
            opp = request.form.get(f"opp_{i}", "").strip()
            if not d or not opp:
                continue
            vals = (d, request.form.get(f"time_{i}", "").strip(), opp,
                    1 if request.form.get(f"home_{i}") == "1" else 0, request.form.get(f"loc_{i}", "").strip())
            ot = opp_team_by_label(opp)
            uid = request.form.get(f"uid_{i}", "").strip() or None
            old = q("SELECT id FROM matches WHERE team_id=? AND ext_uid=?", (tid, uid), one=True) if uid else None
            if old:
                conn.execute("""UPDATE matches SET date=?, time=?, opponent=?, home=?, location=?,
                                opp_team_id=COALESCE(opp_team_id, ?) WHERE id=?""", vals + (ot["id"] if ot else None, old["id"]))
                updated += 1
            else:
                conn.execute("""INSERT INTO matches (team_id, date, time, opponent, home, location, ext_uid, opp_team_id)
                                VALUES (?,?,?,?,?,?,?,?)""", (tid,) + vals + (uid, ot["id"] if ot else None))
                created += 1
        conn.execute("UPDATE teams SET rwk_name=? WHERE id=?", (own, tid))
        if info:   # Klasse und Saison übernehmen, wenn noch leer
            conn.execute("UPDATE teams SET league=CASE WHEN league='' THEN ? ELSE league END, "
                         "season=CASE WHEN season='' THEN ? ELSE season END WHERE id=?",
                         (info.get("league", ""), info.get("season", ""), tid))
        conn.commit()
        flash(f"Import fertig: {created} neu, {updated} aktualisiert"
              f"{f', {new_opp} Gegner in die Gegnerliste übernommen' if new_opp else ''}.", "ok")
        return redirect(url_for("team", tid=tid))

    # Schritt 1: Datei hochladen (oder andere Mannschaft gewählt) -> Vorschau
    typed = (request.form.get("rwk_name") or t["rwk_name"] or t["name"]).strip()
    rows, own, hidden = None, typed, 0
    if request.method == "POST":
        if "BEGIN:VCALENDAR" not in text:
            flash("Das ist keine Kalenderdatei. Bitte die .ics-Datei aus dem RWK-Onlinemelder wählen.", "error")
        elif not events:
            flash("In der Datei wurden keine Termine gefunden.", "error")
        else:
            own = resolve_own(typed, teams) or typed
            found = own in teams
            rows = []
            for ev in events:
                rnd, home_t, guest_t = split_pairing(ev.get("summary", ""))
                if found and own not in (home_t, guest_t):
                    hidden += 1
                    continue
                opp, home = guess_pairing(ev.get("summary", ""), own)
                existing = q("SELECT id, date, time FROM matches WHERE team_id=? AND ext_uid=?", (tid, ev.get("uid")), one=True) \
                    if ev.get("uid") else None
                moved = None
                if existing and (existing["date"], existing["time"]) != (ev["date"], ev["time"]):
                    mv = q("""SELECT new_date FROM match_moves WHERE match_id=? AND status IN ('bestaetigt','offen')
                              ORDER BY id DESC LIMIT 1""", (existing["id"],), one=True)
                    if mv:
                        moved = existing["date"]
                rows.append(dict(ev, opp=opp, home=home, rnd=rnd, exists=existing is not None, moved=moved,
                                 past=ev["date"] < date.today().isoformat()))
            rows.sort(key=lambda r: (r["date"], r["time"]))
    known = {o["label"].lower() for c in opponent_choices() for o in c["teams"]}
    new_teams = [x for x in teams if x != own and x.lower() not in known]
    imported = q("""SELECT COUNT(*) n FROM matches m WHERE team_id=? AND ext_uid IS NOT NULL
                    AND NOT EXISTS (SELECT 1 FROM lineup l WHERE l.match_id=m.id AND l.rings IS NOT NULL)
                    AND NOT EXISTS (SELECT 1 FROM match_opp o WHERE o.match_id=m.id AND o.rings IS NOT NULL)""", (tid,), one=True)["n"]
    return render_template("rwk_import.html", t=t, rows=rows, own=own, typed=typed, teams=sorted(teams), found=own in teams, imported=imported,
                           hidden=hidden, ics_text=text if rows is not None else "", info=info, new_teams=new_teams)


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
        for k, (_label, default) in RANGE_FIELDS.items():
            n = request.form.get(k, type=int)
            conn.execute("""INSERT INTO settings (key, value) VALUES (?,?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (k, str(min(max(n, 1), 600) if n else default)))
        conn.commit()
        flash("Vereinsdaten gespeichert.", "ok")
        return redirect(url_for("club"))
    values = {k: setting(k) for k in CLUB_FIELDS}
    values["club_name"] = values["club_name"] or VEREIN_NAME
    counts = q("""SELECT (SELECT COUNT(*) FROM users WHERE active=1) users, (SELECT COUNT(*) FROM teams) teams,
                  (SELECT COUNT(*) FROM opp_clubs) opps""", one=True)
    ranges = {k: range_setting(k) for k in RANGE_FIELDS}
    return render_template("club.html", values=values, FIELDS=CLUB_FIELDS, counts=counts, RANGE=RANGE_FIELDS, ranges=ranges)


# ---------------------------------------------------------------- Gegner (Verein -> Mannschaften -> Schützen)
def can_edit_opponents():
    return current_user()["role"] in ("admin", "leader")


def opp_label(club_name, team_name):
    return (club_name + (" " + team_name if team_name else "")).strip()


def find_or_create_opp_club(conn, name, location=""):
    r = conn.execute("SELECT id FROM opp_clubs WHERE lower(name)=lower(?)", (name.strip(),)).fetchone()
    if r:
        if location:
            conn.execute("UPDATE opp_clubs SET location=? WHERE id=? AND location=''", (location, r[0]))
        return r[0]
    return conn.execute("INSERT INTO opp_clubs (name, location) VALUES (?,?)", (name.strip(), location)).lastrowid


def find_or_create_opp_team(conn, club_id, name, discipline=""):
    r = conn.execute("SELECT id FROM opp_teams WHERE club_id=? AND lower(name)=lower(?)", (club_id, name.strip())).fetchone()
    if r:
        return r[0]
    return conn.execute("INSERT INTO opp_teams (club_id, name, discipline) VALUES (?,?,?)",
                        (club_id, name.strip(), discipline)).lastrowid


@app.route("/gegner", methods=["GET", "POST"])
@login_required
def opponents():
    if not can_edit_opponents():
        abort(403)
    if request.method == "POST":
        act = request.form.get("action", "")
        f = lambda k: request.form.get(k, "").strip()
        cid = request.form.get("club_id", type=int)
        anchor = f"#verein-{cid}" if cid else ""
        conn = db()
        if act == "club_save":
            if not f("name"):
                flash("Bitte den Vereinsnamen angeben.", "error")
            elif cid:
                conn.execute("UPDATE opp_clubs SET name=?, location=?, club_nr=? WHERE id=?",
                             (f("name"), f("location"), f("club_nr"), cid))
                flash("Verein gespeichert.", "ok")
            else:
                dupe = conn.execute("SELECT id FROM opp_clubs WHERE lower(name)=lower(?)", (f("name"),)).fetchone()
                if dupe:
                    flash("Diesen Verein gibt es schon.", "error")
                    cid = dupe[0]
                else:
                    cid = conn.execute("INSERT INTO opp_clubs (name, location, club_nr) VALUES (?,?,?)",
                                       (f("name"), f("location"), f("club_nr"))).lastrowid
                    if f("team"):
                        conn.execute("INSERT INTO opp_teams (club_id, name, discipline) VALUES (?,?,?)",
                                     (cid, f("team"), f("discipline")))
                    flash("Verein angelegt. Jetzt Mannschaften und Schützen ergänzen.", "ok")
                anchor = f"#verein-{cid}"
        elif act == "club_delete" and cid:
            conn.execute("UPDATE matches SET opp_team_id=NULL WHERE opp_team_id IN (SELECT id FROM opp_teams WHERE club_id=?)", (cid,))
            conn.execute("DELETE FROM opp_clubs WHERE id=?", (cid,))
            flash("Verein gelöscht. Bestehende Wettkämpfe bleiben erhalten.", "ok")
            anchor = ""
        elif act == "team_add" and cid:
            conn.execute("INSERT INTO opp_teams (club_id, name, discipline) VALUES (?,?,?)", (cid, f("name"), f("discipline")))
            flash("Mannschaft hinzugefügt.", "ok")
        elif act == "team_save":
            conn.execute("UPDATE opp_teams SET name=?, discipline=? WHERE id=? AND club_id=?",
                         (f("name"), f("discipline"), request.form.get("team_id", type=int), cid))
            flash("Mannschaft gespeichert.", "ok")
        elif act == "team_delete":
            tid_ = request.form.get("team_id", type=int)
            conn.execute("UPDATE matches SET opp_team_id=NULL WHERE opp_team_id=?", (tid_,))
            conn.execute("DELETE FROM opp_teams WHERE id=? AND club_id=?", (tid_, cid))
            flash("Mannschaft gelöscht.", "ok")
        elif act == "shooter_add" and cid:
            if f("name"):
                team_id = request.form.get("team_id", type=int)
                if team_id and not conn.execute("SELECT 1 FROM opp_teams WHERE id=? AND club_id=?", (team_id, cid)).fetchone():
                    team_id = None
                conn.execute("INSERT INTO opp_shooters (club_id, team_id, name, pass_nr, player_id) VALUES (?,?,?,?,?)",
                             (cid, team_id, f("name"), f("pass_nr"), f("player_id")))
                flash("Schütze hinzugefügt.", "ok")
        elif act == "shooter_save":
            team_id = request.form.get("team_id", type=int)
            if team_id and not conn.execute("SELECT 1 FROM opp_teams WHERE id=? AND club_id=?", (team_id, cid)).fetchone():
                team_id = None
            conn.execute("UPDATE opp_shooters SET name=?, pass_nr=?, player_id=?, team_id=? WHERE id=? AND club_id=?",
                         (f("name"), f("pass_nr"), f("player_id"), team_id, request.form.get("shooter_id", type=int), cid))
            flash("Schütze gespeichert.", "ok")
        elif act == "shooter_delete":
            conn.execute("DELETE FROM opp_shooters WHERE id=? AND club_id=?", (request.form.get("shooter_id", type=int), cid))
            flash("Schütze entfernt.", "ok")
        conn.commit()
        return redirect(url_for("opponents", q=request.args.get("q") or None) + anchor)
    search = request.args.get("q", "").strip()
    clubs = q("SELECT * FROM opp_clubs ORDER BY name COLLATE NOCASE")
    teams = q("""SELECT ot.*, (SELECT COUNT(*) FROM matches m WHERE m.opp_team_id=ot.id) matches
                 FROM opp_teams ot ORDER BY ot.name COLLATE NOCASE""")
    shooters = q("SELECT * FROM opp_shooters ORDER BY name COLLATE NOCASE")
    data = []
    for c in clubs:
        ct = [t for t in teams if t["club_id"] == c["id"]]
        cs = [x for x in shooters if x["club_id"] == c["id"]]
        if search and search.lower() not in (c["name"] + " " + c["location"] + " " +
                                              " ".join(x["name"] for x in cs)).lower():
            continue
        data.append({"club": c, "teams": ct, "shooters": cs,
                     "team_names": {t["id"]: t["name"] or "–" for t in ct}})
    return render_template("opponents.html", data=data, search=search, total=len(clubs), disciplines=DISCIPLINES)


def opponent_choices():
    """Für die Auswahl beim Wettkampf: Vereine mit ihren Mannschaften."""
    clubs = q("SELECT * FROM opp_clubs ORDER BY name COLLATE NOCASE")
    teams = q("SELECT * FROM opp_teams ORDER BY name COLLATE NOCASE")
    return [{"club": c, "teams": [dict(t, label=opp_label(c["name"], t["name"])) for t in teams if t["club_id"] == c["id"]]}
            for c in clubs]


def opp_team_by_label(label):
    """Gegnermannschaft anhand eines Namens wie 'SV Tell 1' finden (für den RWK-Import)."""
    for c in opponent_choices():
        for t in c["teams"]:
            if t["label"].lower() == (label or "").strip().lower():
                return t
    return None


# ---------------------------------------------------------------- CSV-Import (Schützen, Gegner)
IMPORT_FIELDS = {
    "schuetzen": {"vorname": "Vorname", "nachname": "Nachname", "name": "Name (komplett)",
                  "player_id": "Spieler-ID", "pass_nr": "Pass-Nr. / Ausweisnummer", "club": "Verein",
                  "birth_year": "Jahrgang", "gender": "Geschlecht", "": "– ignorieren –"},
    "gegner": {"club": "Verein", "team": "Mannschaft", "location": "Ort / Schießstand",
               "vorname": "Schütze Vorname", "nachname": "Schütze Nachname", "name": "Schütze (Name komplett)",
               "pass_nr": "Pass-Nr. / Ausweisnummer", "player_id": "Spieler-ID", "birth_year": "Jahrgang",
               "gender": "Geschlecht", "": "– ignorieren –"},
}
HEADER_HINTS = {
    "vorname": ("vorname", "first"), "nachname": ("nachname", "familienname", "last", "zuname"),
    "name": ("name", "schütze", "schuetze"), "player_id": ("id", "startnummer", "startnr", "personennummer", "mitgliedsnummer", "spieler"),
    "pass_nr": ("pass", "ausweis"), "club": ("verein", "club"), "birth_year": ("jahrgang", "geburtsjahr", "geburt"),
    "gender": ("geschlecht", "sex", "gender"), "team": ("mannschaft", "team"), "location": ("ort", "stand", "halle"),
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
    has_first = any("vorname" in h.lower() for h in header)
    for h in header:
        hl = h.lower().strip()
        pick = ""
        if hl == "name" and has_first and "nachname" in fields and "nachname" not in out:
            out.append("nachname")   # DISAG: "Name" + "Vorname" -> Name ist der Nachname
            continue
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


def upsert_opp_shooter(conn, cid, team_id, sname, rec):
    """Gegnerschützen anlegen oder ergänzen (gleicher Name im selben Verein)."""
    vals = (rec.get("pass_nr", ""), rec.get("player_id", ""), norm_year(rec.get("birth_year")), norm_gender(rec.get("gender")))
    old = conn.execute("SELECT id FROM opp_shooters WHERE club_id=? AND lower(name)=lower(?)", (cid, sname)).fetchone()
    if old:
        conn.execute("""UPDATE opp_shooters SET team_id=COALESCE(?, team_id), pass_nr=COALESCE(NULLIF(?, ''), pass_nr),
                        player_id=COALESCE(NULLIF(?, ''), player_id), birth_year=COALESCE(NULLIF(?, ''), birth_year),
                        gender=COALESCE(NULLIF(?, ''), gender) WHERE id=?""", (team_id,) + vals + (old[0],))
    else:
        conn.execute("""INSERT INTO opp_shooters (club_id, team_id, name, pass_nr, player_id, birth_year, gender)
                        VALUES (?,?,?,?,?,?,?)""", (cid, team_id, sname) + vals)


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
        # Schützen-Import mit Vereinsspalte: zuerst wählen, welche Vereine übernommen werden
        if kind == "schuetzen" and "club" in mapping:
            ci = mapping.index("club")
            if request.form.get("clubs_chosen") != "1":
                counts = {}
                for r in body:
                    name = r[ci].strip() if ci < len(r) else ""
                    counts[name] = counts.get(name, 0) + 1
                own = (setting("club_name") or VEREIN_NAME).strip().lower()
                top = max(counts, key=counts.get) if counts else ""
                clubs = [{"name": n, "count": c,
                          "default": "member" if (not n or n.lower() == own or (own and (own in n.lower() or n.lower() in own))
                                                  or (n == top and not any(k.lower() == own for k in counts))) else "skip"}
                         for n, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]
                return render_template("csv_import.html", kind=kind, fields=fields, preview=None, clubstep=clubs,
                                       data=request.form.get("data", ""), header=has_header, mapping=mapping)
            choice = {request.form.get(f"cn_{i}", "").strip().lower(): request.form.get(f"cc_{i}", "skip")
                      for i in range(request.form.get("club_count", type=int) or 0)}
        else:
            ci, choice = None, {}
        created, updated, skipped, accounts, as_opp = 0, 0, 0, [], 0
        conn = db()
        for r in body:
            rec = {}
            for i, f in enumerate(mapping):
                if f and i < len(r) and r[i].strip():
                    rec[f] = r[i].strip()
            if ci is not None:
                pick = choice.get(rec.get("club", "").lower(), "skip")
                if pick == "skip":
                    skipped += 1
                    continue
                if pick == "opponent":   # als Gegnerschütze beim jeweiligen Gegnerverein ablegen
                    sname = rec.get("name") or " ".join(x for x in (rec.get("vorname"), rec.get("nachname")) if x)
                    if sname and rec.get("club"):
                        cid = find_or_create_opp_club(conn, rec["club"])
                        upsert_opp_shooter(conn, cid, None, sname, rec)
                        as_opp += 1
                    else:
                        skipped += 1
                    continue
            if kind == "gegner":
                if not rec.get("club"):
                    skipped += 1
                    continue
                before = conn.total_changes
                cid = find_or_create_opp_club(conn, rec["club"], rec.get("location", ""))
                team_id = find_or_create_opp_team(conn, cid, rec["team"]) if rec.get("team") else None
                sname = rec.get("name") or " ".join(x for x in (rec.get("vorname"), rec.get("nachname")) if x)
                if sname:
                    upsert_opp_shooter(conn, cid, team_id, sname, rec)
                if conn.total_changes > before:
                    created += 1
                else:
                    updated += 1
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
                                pass_nr=COALESCE(NULLIF(?, ''), pass_nr), club=COALESCE(NULLIF(?, ''), club),
                                birth_year=COALESCE(NULLIF(?, ''), birth_year), gender=COALESCE(NULLIF(?, ''), gender) WHERE id=?""",
                             (rec.get("player_id", ""), rec.get("pass_nr", ""), rec.get("club", ""),
                              norm_year(rec.get("birth_year")), norm_gender(rec.get("gender")), old["id"]))
                updated += 1
            else:
                username = make_username(name)
                pw = secrets.token_urlsafe(6)
                conn.execute("""INSERT INTO users (name, username, password_hash, role, must_change_pw, player_id, pass_nr, club,
                                birth_year, gender) VALUES (?,?,?,'shooter',1,?,?,?,?,?)""",
                             (name, username, generate_password_hash(pw), rec.get("player_id", ""),
                              rec.get("pass_nr", ""), rec.get("club", ""), norm_year(rec.get("birth_year")), norm_gender(rec.get("gender"))))
                conn.commit()
                accounts.append({"name": name, "username": username, "password": pw})
                created += 1
        conn.commit()
        flash(f"Import fertig: {created} neu, {updated} aktualisiert{f', {as_opp} als Gegnerschützen' if as_opp else ''}"
              f"{f', {skipped} übersprungen' if skipped else ''}.", "ok")
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
