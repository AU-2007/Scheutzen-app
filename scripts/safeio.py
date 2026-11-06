"""Sicheres Lesen und Schreiben im Datenordner für Skripte, die als root laufen.

Der Datenordner data/ gehört dem App-Benutzer (uid 1000), weil der Container dort schreibt.
Wer den Container übernimmt, kann dort also Dateien und Symlinks anlegen. Ein root-Skript,
das dort einfach open(), chmod() oder chown() aufruft, würde einem untergeschobenen Symlink
folgen und so beliebige Systemdateien überschreiben oder dem App-Benutzer übergeben.

Darum hier nur:
- Ordner Stück für Stück mit O_NOFOLLOW öffnen und danach relativ dazu arbeiten (dir_fd),
- neue Dateien nur mit O_CREAT|O_EXCL|O_NOFOLLOW anlegen (folgt nie einem Symlink),
- Rechte/Besitzer nur über den Dateideskriptor ändern (fchmod/fchown),
- atomar per rename() an den Zielnamen; ein Symlink dort wird dabei nur ersetzt, nicht verfolgt.
"""
import json
import os
import secrets
import stat

APP_UID = APP_GID = 1000
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def open_dir(*parts, create=False, base=None):
    """Ordner base/parts[0]/parts[1]/… öffnen, ohne einem Symlink zu folgen. Gibt einen fd zurück.
    base (Standard: App-Ordner) gehört root und wird normal geöffnet. Mit create=True werden fehlende
    Ordner angelegt; ein Symlink an dieser Stelle wird entfernt und durch einen echten Ordner ersetzt."""
    fd = os.open(base or APP_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            if not part or "/" in part or part in (".", ".."):
                raise ValueError(f"ungültiger Ordnername: {part!r}")
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except (FileNotFoundError, NotADirectoryError, OSError) as e:
                if not create or isinstance(e, PermissionError):
                    raise
                try:   # Symlink oder Datei an dieser Stelle: weg damit (entfernt nur den Eintrag selbst)
                    st = os.stat(part, dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISDIR(st.st_mode):
                        os.unlink(part, dir_fd=fd)
                except FileNotFoundError:
                    pass
                os.mkdir(part, 0o755, dir_fd=fd)
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                give_to_app(nfd)
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def give_to_app(fd):
    """Besitz an den App-Benutzer geben, über den fd (nie über einen Pfad)."""
    try:
        os.fchown(fd, APP_UID, APP_GID)
    except PermissionError:
        pass   # nicht als root gestartet (z. B. in Tests)


def read_json(dfd, name):
    """JSON-Datei lesen, ohne einem Symlink zu folgen. Fehler -> None."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size > 2_000_000:
            return None
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = None
            return json.load(fh)
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def create_file(dfd, name, mode=0o644):
    """Neue Datei anlegen (nie über einen Symlink), dem App-Benutzer geben. Gibt fd zurück."""
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
    os.fchmod(fd, mode)
    give_to_app(fd)
    return fd


def write_json(dfd, name, data, indent=None):
    """JSON atomar schreiben: unter Zufallsnamen neu anlegen, dann per rename() an den Zielnamen."""
    tmp = f".{name}.{secrets.token_hex(6)}.tmp"
    fd = create_file(dfd, tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=indent, ensure_ascii=False)
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise


def update_json(path, changes, indent=1):
    """Statusdatei (z. B. data/wartung.json) lesen, ändern, sicher zurückschreiben.
    path muss innerhalb des App-Ordners liegen."""
    rel = os.path.relpath(os.path.abspath(path), APP_DIR).split(os.sep)
    if rel[0] == "..":
        raise ValueError("Pfad außerhalb des App-Ordners")
    dfd = open_dir(*rel[:-1], create=True)
    try:
        data = read_json(dfd, rel[-1])
        data = data if isinstance(data, dict) else {}
        data.update(changes)
        write_json(dfd, rel[-1], data, indent=indent)
        return data
    finally:
        os.close(dfd)
