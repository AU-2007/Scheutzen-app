#!/usr/bin/env python3
"""Reverse-Proxy (Caddy) ein- oder ausschalten und seine Regeln setzen. Läuft als root (über runner.py).

Aufruf: proxy.py "MODUS|KOMPRIMIERUNG|UPLOAD_MB|WARTUNG_NETZE|DOMAIN"
  MODUS          aus     – kein Caddy (Cloudflare Tunnel oder anderer Proxy geht direkt auf die App, Port 8000)
                 tunnel  – Caddy auf 127.0.0.1:8080 hinter dem Cloudflare Tunnel (Tunnel-Ziel auf http://localhost:8080 stellen)
                 https   – Caddy mit eigenem Let's-Encrypt-Zertifikat auf Port 80/443 (VPS ohne Tunnel)
  KOMPRIMIERUNG  1/0
  UPLOAD_MB      1–100, größere Anfragen lehnt Caddy ab
  WARTUNG_NETZE  leer oder IP-Adressen/Netze (Leerzeichen getrennt): nur von dort ist /admin/wartung erreichbar
  DOMAIN         nur für https

Sicherheit: Jeder Wert wird hier streng geprüft und nur aus festen Bausteinen eine Caddyfile gebaut.
Vor dem Umschalten prüft Caddy die neue Datei; schlägt etwas fehl, werden .env und Caddyfile zurückgesetzt.
"""
import ipaddress
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safeio   # noqa: E402

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CADDYFILE = "Caddyfile.local"              # wird erzeugt, steht in .gitignore
MODES = {"aus": "docker-compose.yml",
         "tunnel": "docker-compose.yml:docker-compose.caddy-tunnel.yml",
         "https": "docker-compose.yml:docker-compose.caddy.yml"}
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def parse(param):
    """'tunnel|1|20|10.0.0.0/8 1.2.3.4|' -> dict. Wirft ValueError mit deutscher Meldung."""
    parts = (param or "").split("|")
    if len(parts) != 5:
        raise ValueError("Ungültige Einstellung.")
    mode, gz, upload, nets, domain = parts
    if mode not in MODES:
        raise ValueError("Unbekannter Modus.")
    if gz not in ("0", "1"):
        raise ValueError("Komprimierung muss 0 oder 1 sein.")
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
    domain = domain.strip().lower()
    if mode == "https" and not DOMAIN_RE.match(domain):
        raise ValueError("Für eigenes HTTPS bitte eine gültige Domain angeben.")
    return {"mode": mode, "gzip": gz == "1", "upload_mb": int(upload), "wartung_nets": cidrs,
            "domain": domain if mode == "https" else ""}


def caddyfile(cfg):
    """Caddyfile nur aus festen Bausteinen und geprüften Werten."""
    tunnel = cfg["mode"] == "tunnel"
    out = ["# Automatisch erzeugt über die Wartungsseite der App. Änderungen hier werden überschrieben.", "{"]
    if tunnel:   # cloudflared kommt über die Docker-Bridge (privates Netz); echte IP steht in CF-Connecting-IP
        out += ["\tservers {", "\t\ttrusted_proxies static private_ranges", "\t\tclient_ip_headers CF-Connecting-IP X-Forwarded-For",
                "\t}", "\tauto_https off"]
    out += ["}", "", (":8080" if tunnel else cfg["domain"]) + " {"]
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


def write_root_file(name, text, mode):
    """Atomar im App-Ordner (gehört root) schreiben; ein Symlink am Ziel wird ersetzt, nicht verfolgt."""
    tmp = os.path.join(APP_DIR, f".{name}.neu")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, os.path.join(APP_DIR, name))


def read(name):
    try:
        with open(os.path.join(APP_DIR, name)) as fh:
            return fh.read()
    except OSError:
        return None


def compose(*args):
    print("$ docker compose " + " ".join(args), flush=True)
    return subprocess.run(["docker", "compose", *args], cwd=APP_DIR).returncode


def main(param):
    cfg = parse(param)
    old_env, old_caddy = read(".env") or "", read(CADDYFILE)
    changes = {"COMPOSE_FILE": MODES[cfg["mode"]], "CADDYFILE": f"./{CADDYFILE}"}
    if cfg["mode"] != "aus":
        changes["TRUST_PROXY"] = "1"          # App muss X-Forwarded-* von Caddy übernehmen
    if cfg["mode"] == "https":
        changes["DOMAIN"] = cfg["domain"]
    new_env = env_with(old_env, changes)     # alle anderen Zeilen bleiben unverändert
    print(f"Modus: {cfg['mode']}, Komprimierung: {'an' if cfg['gzip'] else 'aus'}, Upload bis {cfg['upload_mb']} MB, "
          f"Wartung nur aus: {' '.join(cfg['wartung_nets']) or 'überall'}", flush=True)
    write_root_file(CADDYFILE, caddyfile(cfg), 0o644)
    write_root_file(".env", new_env, 0o600)

    def rollback(msg):
        print(f"\n!! {msg} – alte Einstellung wird wiederhergestellt.", flush=True)
        write_root_file(".env", old_env, 0o600)
        if old_caddy is None:
            os.unlink(os.path.join(APP_DIR, CADDYFILE))
        else:
            write_root_file(CADDYFILE, old_caddy, 0o644)
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
    safeio.update_json(os.path.join(APP_DIR, "data", "wartung.json"), {"proxy": cfg})
    print("\nFertig.")
    if cfg["mode"] == "tunnel":
        print("WICHTIG: Im Cloudflare-Dashboard beim Tunnel das Ziel auf http://localhost:8080 stellen.\n"
              "Erst dann laufen die Anfragen über Caddy und die Regeln greifen.")
    elif cfg["mode"] == "https":
        print(f"Caddy holt jetzt das Zertifikat für {cfg['domain']}. Ports 80 und 443 müssen von außen erreichbar sein.")
    else:
        print("Caddy ist aus. Zeigt der Cloudflare Tunnel auf http://localhost:8080, dort wieder http://localhost:8000 eintragen.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
    except ValueError as e:
        print(f"Abgelehnt: {e}")
        sys.exit(2)
