#!/usr/bin/env python3
"""Reverse-Proxy (Caddy) über die Wartungsseite einrichten. Läuft als root (über runner.py).

Aufruf:  proxy.py "AN|WEG|KOMPR|UPLOAD_MB|NETZE|DOMAINS|ZERTIFIKAT|EMAIL"
         proxy.py --status        (nur Zertifikat prüfen und in data/wartung.json eintragen)

  AN          1 = Caddy benutzen, 0 = aus (Anfragen gehen direkt an die App, Port 8000)
  WEG         tunnel = Cloudflare Tunnel liefert die Anfragen (Caddy auf 127.0.0.1:8080, ohne eigenes HTTPS)
              direkt = Anfragen kommen direkt aus dem Internet (Caddy auf Port 80/443 mit Zertifikat)
  KOMPR       1/0 Seiten komprimiert ausliefern
  UPLOAD_MB   1–100, größere Anfragen lehnt Caddy ab
  NETZE       leer oder IP-Adressen/Netze (Leerzeichen): nur von dort ist /admin/wartung erreichbar
  DOMAINS     Domains (Leerzeichen), bei "direkt" mindestens eine
  ZERTIFIKAT  auto   = Let's Encrypt, holt und verlängert Caddy selbst
              eigen  = eigenes Zertifikat (z. B. Cloudflare-Origin-Zertifikat), von der App hochgeladen
              intern = selbst signiert (nur zum Testen / im Vereinsnetz)
  EMAIL       optional: Let's Encrypt schickt dorthin Warnungen, bevor ein Zertifikat abläuft

Sicherheit: Jeder Wert wird hier streng geprüft, die Caddyfile entsteht nur aus festen Bausteinen.
Vor dem Umschalten prüft Caddy die neue Datei; schlägt etwas fehl, wird alles zurückgesetzt.
"""
import datetime
import ipaddress
import os
import re
import socket
import ssl
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safeio   # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CADDYFILE = "Caddyfile.local"              # wird erzeugt, steht in .gitignore
CERT_DIR = "caddy-certs"                   # eigenes Zertifikat (gehört root), steht in .gitignore
COMPOSE = {"aus": "docker-compose.yml",
           "tunnel": "docker-compose.yml:docker-compose.caddy-tunnel.yml",
           "direkt": "docker-compose.yml:docker-compose.caddy.yml"}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$")
PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----")
PEM_KEY = re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----[A-Za-z0-9+/=\s]+-----END (?:RSA |EC )?PRIVATE KEY-----")


def parse(param):
    """Parameter prüfen -> dict. Wirft ValueError mit deutscher Meldung."""
    parts = (param or "").split("|")
    if len(parts) != 8:
        raise ValueError("Ungültige Einstellung.")
    on, via, gz, upload, nets, domains, tls, email = parts
    if on not in ("0", "1") or gz not in ("0", "1"):
        raise ValueError("Ungültige Einstellung.")
    if via not in ("tunnel", "direkt"):
        raise ValueError("Bitte wählen, ob der Cloudflare Tunnel genutzt wird.")
    if not upload.isdigit() or not 1 <= int(upload) <= 100:
        raise ValueError("Upload-Grenze muss zwischen 1 und 100 MB liegen.")
    cidrs = []
    for n in nets.split():
        try:
            cidrs.append(str(ipaddress.ip_network(n, strict=False)))
        except ValueError:
            raise ValueError(f"„{n[:40]}“ ist keine IP-Adresse und kein Netz.")
    if len(cidrs) > 20:
        raise ValueError("Höchstens 20 Netze.")
    doms = []
    for d in domains.lower().split():
        if not DOMAIN_RE.match(d):
            raise ValueError(f"„{d[:60]}“ ist keine gültige Domain.")
        if d not in doms:
            doms.append(d)
    if len(doms) > 10:
        raise ValueError("Höchstens 10 Domains.")
    if tls not in ("auto", "eigen", "intern"):
        raise ValueError("Unbekannte Zertifikatsart.")
    if email and not EMAIL_RE.match(email):
        raise ValueError("Die E-Mail-Adresse ist ungültig.")
    if on == "1" and via == "direkt" and not doms:
        raise ValueError("Ohne Tunnel braucht Caddy mindestens eine Domain.")
    if on == "1" and via == "direkt" and tls == "auto" and any(d.startswith("*.") for d in doms):
        raise ValueError("Platzhalter-Domains (*.) gehen nicht mit automatischem Zertifikat.")
    mode = "aus" if on == "0" else via
    return {"on": on == "1", "via": via, "mode": mode, "gzip": gz == "1", "upload_mb": int(upload),
            "wartung_nets": cidrs, "domains": doms, "tls": tls, "email": email}


def caddyfile(cfg):
    """Caddyfile nur aus festen Bausteinen und geprüften Werten."""
    tunnel = cfg["mode"] == "tunnel"
    out = ["# Automatisch erzeugt über die Wartungsseite der App. Änderungen hier werden überschrieben.", "{"]
    if tunnel:   # cloudflared kommt über die Docker-Bridge (privates Netz); echte IP steht in CF-Connecting-IP
        out += ["\tservers {", "\t\ttrusted_proxies static private_ranges", "\t\tclient_ip_headers CF-Connecting-IP X-Forwarded-For",
                "\t}", "\tauto_https off"]
    elif cfg["email"] and cfg["tls"] == "auto":
        out.append(f"\temail {cfg['email']}")
    out += ["}", ""]
    out.append((":8080" if tunnel else ", ".join(cfg["domains"])) + " {")
    if not tunnel and cfg["tls"] == "eigen":
        out.append("\ttls /certs/cert.pem /certs/key.pem")
    elif not tunnel and cfg["tls"] == "intern":
        out.append("\ttls internal")
    if cfg["gzip"]:
        out.append("\tencode zstd gzip")
    out += ["\trequest_body {", f"\t\tmax_size {cfg['upload_mb']}MB", "\t}"]
    if cfg["wartung_nets"]:
        out += ["\t@gesperrt {", "\t\tpath /admin/wartung /admin/wartung/*",
                "\t\tnot client_ip " + " ".join(cfg["wartung_nets"]), "\t}",
                '\trespond @gesperrt "Die Wartung ist nur aus freigegebenen Netzen erreichbar." 403']
    out += ["\treverse_proxy mannschaften:8000 {", "\t\theader_up X-Forwarded-For {client_ip}", "\t}",
            "\theader -Server"]
    if not tunnel:
        out.append('\theader Strict-Transport-Security "max-age=31536000"')
    out += ["}", ""]
    return "\n".join(out)


def env_with(text, changes):
    """KEY=wert-Zeilen ersetzen oder anhängen; None entfernt den Schlüssel. Alles andere bleibt."""
    lines, seen = [], set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        if key in changes and not line.lstrip().startswith("#"):
            seen.add(key)
            if changes[key] is not None:
                lines.append(f"{key}={changes[key]}")
            continue
        lines.append(line)
    lines += [f"{k}={v}" for k, v in changes.items() if k not in seen and v is not None]
    return "\n".join(lines) + "\n"


def write_root_file(rel, text, mode):
    """Atomar im App-Ordner (gehört root) schreiben; ein Symlink am Ziel wird ersetzt, nicht verfolgt."""
    path = os.path.join(APP_DIR, rel)
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.neu")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def read(rel):
    try:
        with open(os.path.join(APP_DIR, rel)) as fh:
            return fh.read()
    except OSError:
        return None


def take_upload():
    """Von der App hochgeladenes Zertifikat (data/proxy/) holen und dort löschen. -> (cert, key) oder None."""
    try:
        dfd = safeio.open_dir("data", "proxy")
    except OSError:
        return None
    try:
        cert, key = safeio.read_text(dfd, "cert.pem"), safeio.read_text(dfd, "key.pem")
        for n in ("cert.pem", "key.pem"):
            try:
                os.unlink(n, dir_fd=dfd)
            except OSError:
                pass
    finally:
        os.close(dfd)
    if cert is None and key is None:
        return None
    if not cert or not key or not PEM_CERT.search(cert) or not PEM_KEY.search(key):
        raise ValueError("Zertifikat oder privater Schlüssel ist kein gültiges PEM.")
    return cert, key


def compose(*args):
    print("$ docker compose " + " ".join(args), flush=True)
    return subprocess.run(["docker", "compose", *args], cwd=APP_DIR).returncode


def cert_status(cfg):
    """Zertifikat über Port 443 abfragen (nur bei 'direkt'). -> dict für die Wartungsseite."""
    if cfg.get("mode") != "direkt" or not cfg.get("domains"):
        return None
    host = cfg["domains"][0].lstrip("*.")
    res = {"domain": host, "checked": datetime.datetime.now().isoformat(timespec="seconds")}
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with socket.create_connection(("127.0.0.1", 443), timeout=5) as s, ctx.wrap_socket(s, server_hostname=host) as t:
            der = t.getpeercert(binary_form=True)
        r = subprocess.run(["openssl", "x509", "-inform", "DER", "-noout", "-enddate", "-issuer"], input=der,
                           capture_output=True, timeout=10)
        for line in r.stdout.decode(errors="replace").splitlines():
            k, _, v = line.partition("=")
            if k == "notAfter":
                res["until"] = datetime.datetime.strptime(v.strip(), "%b %d %H:%M:%S %Y %Z").date().isoformat()
            elif k == "issuer":
                m = re.search(r"O ?= ?([^,/]+)", v) or re.search(r"CN ?= ?([^,/]+)", v)
                res["issuer"] = (m.group(1) if m else v).strip()[:80]
        try:   # öffentlich vertrauenswürdig? (Let's Encrypt ja; intern/Cloudflare-Origin nein, das ist dann so gewollt)
            with socket.create_connection(("127.0.0.1", 443), timeout=5) as s, \
                    ssl.create_default_context().wrap_socket(s, server_hostname=host):
                res["trusted"] = True
        except (ssl.SSLError, OSError):
            res["trusted"] = False
    except (OSError, ssl.SSLError, subprocess.SubprocessError, ValueError) as e:
        res["error"] = str(e)[:160]
    return res


def save_status(cfg, cert):
    safeio.update_json(os.path.join(APP_DIR, "data", "wartung.json"), {"proxy": cfg, "proxy_cert": cert})


def main(param):
    cfg = parse(param)
    old_env, old_caddy = read(".env") or "", read(CADDYFILE)
    old_cert = (read(os.path.join(CERT_DIR, "cert.pem")), read(os.path.join(CERT_DIR, "key.pem")))
    upload = take_upload()
    if cfg["mode"] == "direkt" and cfg["tls"] == "eigen" and not upload and not all(old_cert):
        raise ValueError("Für ein eigenes Zertifikat bitte Zertifikat und privaten Schlüssel hochladen.")
    changes = {"COMPOSE_FILE": COMPOSE[cfg["mode"]], "CADDYFILE": f"./{CADDYFILE}"}
    if cfg["mode"] != "aus":
        changes["TRUST_PROXY"] = "1"          # App muss X-Forwarded-* von Caddy übernehmen
    if cfg["mode"] == "direkt":
        changes["DOMAIN"] = cfg["domains"][0]
    print(f"Caddy: {'an' if cfg['on'] else 'aus'} · Weg: {'Cloudflare Tunnel' if cfg['via'] == 'tunnel' else 'direkt'}"
          f" · Domains: {' '.join(cfg['domains']) or '–'} · Zertifikat: {cfg['tls']}"
          f" · Komprimierung: {'an' if cfg['gzip'] else 'aus'} · Upload bis {cfg['upload_mb']} MB"
          f" · Wartung nur aus: {' '.join(cfg['wartung_nets']) or 'überall'}", flush=True)
    os.makedirs(os.path.join(APP_DIR, CERT_DIR), mode=0o700, exist_ok=True)
    if upload:
        write_root_file(os.path.join(CERT_DIR, "cert.pem"), upload[0], 0o644)
        write_root_file(os.path.join(CERT_DIR, "key.pem"), upload[1], 0o600)
        print("Neues Zertifikat übernommen.")
    write_root_file(CADDYFILE, caddyfile(cfg), 0o644)
    write_root_file(".env", env_with(old_env, changes), 0o600)

    def rollback(msg):
        print(f"\n!! {msg} – alte Einstellung wird wiederhergestellt.", flush=True)
        write_root_file(".env", old_env, 0o600)
        if old_caddy is None:
            os.unlink(os.path.join(APP_DIR, CADDYFILE))
        else:
            write_root_file(CADDYFILE, old_caddy, 0o644)
        if upload:
            for name, text, mode in (("cert.pem", old_cert[0], 0o644), ("key.pem", old_cert[1], 0o600)):
                if text is None:
                    os.unlink(os.path.join(APP_DIR, CERT_DIR, name))
                else:
                    write_root_file(os.path.join(CERT_DIR, name), text, mode)
        compose("up", "-d", "--remove-orphans")
        return 1

    if cfg["mode"] != "aus":
        if compose("run", "--rm", "--no-deps", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile",
                   "--adapter", "caddyfile") != 0:
            return rollback("Caddy hat die neue Einstellung abgelehnt")
    if compose("up", "-d", "--remove-orphans") != 0:
        return rollback("Starten hat nicht geklappt")
    if cfg["mode"] != "aus" and compose("restart", "caddy") != 0:   # neue Caddyfile sicher übernehmen
        return rollback("Caddy ließ sich nicht neu starten")
    cert = None
    if cfg["mode"] == "direkt":
        print("Warte auf das Zertifikat …", flush=True)
        for _ in range(int(os.environ.get("PROXY_CERT_TRIES", "12"))):   # Let's Encrypt braucht meist 5–30 Sekunden
            cert = cert_status(cfg)
            if cert and cert.get("until"):
                break
            time.sleep(5)
        if cert and cert.get("until"):
            print(f"Zertifikat von {cert.get('issuer', '?')} gültig bis {cert['until']}"
                  + ("" if cert.get("trusted") else " (nicht öffentlich vertrauenswürdig)"))
        else:
            print("Noch kein Zertifikat abrufbar. Domain muss auf diesen Server zeigen, Ports 80/443 offen sein. "
                  "Später unter „Auf Updates prüfen“ wird der Stand aktualisiert.")
    save_status(cfg, cert)
    print("\nFertig.")
    if cfg["mode"] == "tunnel":
        print("WICHTIG: Im Cloudflare-Dashboard beim Tunnel das Ziel auf http://localhost:8080 stellen.\n"
              "Erst dann laufen die Anfragen über Caddy und die Regeln greifen.")
    elif cfg["mode"] == "aus" and cfg["via"] == "tunnel":
        print("Caddy ist aus. Zeigt der Cloudflare Tunnel auf http://localhost:8080, dort wieder http://localhost:8000 eintragen.")
    return 0


def status_only():
    st = safeio.update_json(os.path.join(APP_DIR, "data", "wartung.json"), {})
    cfg = st.get("proxy") if isinstance(st.get("proxy"), dict) else None
    if cfg:
        safeio.update_json(os.path.join(APP_DIR, "data", "wartung.json"), {"proxy_cert": cert_status(cfg)})
    return 0


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--status":
            sys.exit(status_only())
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
    except ValueError as e:
        print(f"Abgelehnt: {e}")
        sys.exit(2)
