# Mannschaftsverwaltung für den Schützenverein

Eine kleine Web-App für Handy und PC. Damit verwaltet ihr Mannschaften, Wettkampftermine, Zu- und Absagen, Aufstellungen und Ringergebnisse.

## Rollen

| Rolle | Was sie darf |
|---|---|
| **Trainer** | Feste Trainingszeiten und einmalige Sondertrainings anlegen und ändern, Trainings ausfallen lassen |
| **Admin** | Benutzer anlegen, Rollen vergeben, Passwörter zurücksetzen, Mannschaften anlegen und einen Mannschaftsführer zuweisen. Zusätzlich alles, was ein Mannschaftsführer darf, und zwar bei jeder Mannschaft |
| **Mannschaftsführer** | Bei den eigenen Mannschaften: Schützen hinzufügen oder entfernen, Wettkämpfe anlegen, Aufstellung festlegen, Ringe eintragen |
| **Schütze** | Mannschaften und Termine ansehen, bei eigenen Wettkämpfen zu- oder absagen, eigene Ergebnisse sehen |

Ein Mannschaftsführer kann auch selbst als Schütze in einer Mannschaft stehen.

## Installation auf Proxmox (LXC oder VM mit Docker)

```bash
# 1. Ordner auf den Server kopieren und hineinwechseln
cd schuetzenverein

# 2. Einstellungen anlegen
cp .env.example .env
nano .env          # Vereinsname, SECRET_KEY (openssl rand -hex 32), Admin-Passwort und BIND_IP setzen

# 3. Datenordner anlegen (der Container läuft als Benutzer 1000)
mkdir -p data && chown 1000:1000 data

# 4. Starten
docker compose up -d --build
```

Danach ist die App unter `http://<BIND_IP>:8000` erreichbar. Melde dich mit dem Admin aus der `.env` an. Hast du kein `ADMIN_PASSWORD` gesetzt, steht ein zufälliges Startpasswort im Log: `docker compose logs | grep Startpasswort`. Beim ersten Login musst du ein eigenes Passwort festlegen.

Hinweis für Proxmox-LXC: Für Docker im Container muss unter *Optionen → Features* die Option **Nesting** aktiviert sein.

## Erste Schritte

1. **Benutzer:** Lege alle Mitglieder an und gib jedem ein Startpasswort. Beim ersten Login muss jeder sein Passwort ändern.
2. **Mannschaften:** Lege die Mannschaften an und wähle jeweils einen Mannschaftsführer aus.
3. **Schützen zuordnen:** Öffne die Mannschaft und füge unten die Schützen hinzu.
4. **Training:** Unter *Termine → Trainingszeiten* (Admin oder Trainer) die festen Termine anlegen, zum Beispiel Dienstag und Freitag, jeweils mit der Anzahl der Stände. Danach sieht jeder die nächsten vier Wochen mit der Zahl der freien Stände und meldet sich mit Zusage, Später oder Absage. Zusage und Später belegen je einen Stand. Sind alle belegt, geht nur noch Absage. Laufen LG und KK zur gleichen Zeit, trägt man beim Training einen zweiten Bereich ein (zum Beispiel KK mit 4 Ständen). Es bleibt ein Termin: Schützen wählen beim Zusagen LG, KK oder egal, und der Trainer teilt die Zugesagten zu. Admin, Trainer und Mannschaftsführer können einzelne Trainings ausfallen lassen.
5. **Wettkämpfe:** Admin oder Mannschaftsführer tragen die Termine ein. Die Schützen sagen dann auf der Startseite zu oder ab.
6. **Nach dem Wettkampf:** Im Wettkampf die Aufstellung anhaken, die Ringe eintragen und die Ringe des Gegners erfassen. Summe, Sieg oder Niederlage und die Schnitte berechnet die App selbst.

## Admin-Übersicht

Der Admin ist kein Schütze. Seine Übersicht zeigt statt Terminen:
- wer gerade online ist und die letzten Anmeldungen (Zeit, Gerät, IP), inklusive Fehlversuchen. Nach 10 Fehlversuchen in 10 Minuten wird die IP kurz gesperrt.
- das Fehlerprotokoll mit Details zu jedem Serverfehler
- Serverdaten (Laufzeit, Datenbankgröße, freier Speicher, Versionen) und ein Datenbank-Backup zum Herunterladen
- offene Aufgaben (Verlegungsanträge, Wettkämpfe ohne Ergebnis, Mannschaften ohne Mannschaftsführer, Mitglieder ohne Anmeldung)

Protokolle werden nach 90 Tagen gelöscht. Läuft die App hinter einem Reverse Proxy oder Cloudflare Tunnel, `TRUST_PROXY=1` in der `.env` setzen, damit die echten IP-Adressen erscheinen.

## Profil, Verein, Gegner, Import

- **Termine filtern:** Unter dem Kalender nach Wettkämpfen, Training, offenen Rückmeldungen, Zusagen oder Aufstellungen filtern.
- **Mein Profil** (Menü oben rechts): Profilbild, Passwort, hell/dunkel, Akzentfarbe und eine kleine Statistik.
- **Trainingsgruppen** (nur Admin, Menü oben rechts): Gruppen wie Jugend oder Erwachsene anlegen und Mitglieder zuordnen. Beim Training wählt Admin oder Trainer, für welche Gruppen es gilt. Mitglieder sehen nur ihre Trainings; ohne Gruppe gilt ein Training für alle.
- **Verein** (nur Admin): Vereinsname, Vereins-ID, Ort, Verband.
- **Gegner** (Admin, Mannschaftsführer): Jeder Gegnerverein wird einmal angelegt, mit Ort, Mannschaften (je Disziplin) und Schützen (mit Pass-Nr.). Beim Wettkampf wählt man die Gegnermannschaft aus der Liste, der Ort wird bei Auswärtskämpfen übernommen. Ein RWK-Import verknüpft passende Gegner automatisch.
- **Import** (Verein → Daten importieren): Schützen (Name, Spieler-ID, Pass-Nr., Verein) oder Gegner aus einer CSV-Datei, zum Beispiel aus dem Stammdaten-Export des DISAG OpticScore Servers oder aus Excel. Neue Schützen bekommen ein Startpasswort, das nach dem Import einmal angezeigt wird.

## Wettkampf: Gegnerschützen, Standplan, DISAG

- **Gegnerschützen:** Beim Wettkampf die Schützen des Gegners eintragen: aus der Gegnerliste, alle Stammschützen der Mannschaft mit einem Klick oder frei (wahlweise gleich in die Gegnerliste übernehmen). Ihre Ringe trägt man neben den eigenen ein, die Summe des Gegners rechnet die App aus.
- **Standplan:** Verteilt eigene und Gegnerschützen auf die Stände, bei Bedarf auf mehrere Durchgänge (z. B. 3 + 3 Schützen auf 4 KK-Stände = 2 Durchgänge). Wahlweise Paarungen nebeneinander oder erst Heim, dann Gast. Von Hand änderbar, Doppelbelegungen werden gemeldet. Alle Schützen sehen den Plan, mit Startzeit je Durchgang. Druckbar.
- **Standanzahl:** Unter *Verein* (Admin): Stände LG, Stände KK und Minuten pro Durchgang. Pro Wettkampf im Standplan änderbar. Ob LG oder KK, erkennt die App an der Disziplin der Mannschaft.
- **DISAG-FLEX-Datei:** Im Standplan und auf der Mannschaftsseite. Format wie die DISAG-Vorlage „SchuetzenMitVereinWaffenart“: `Ausweisnummer;Name;Vorname;Verein;Jahrgang;Waffenart;Geschlecht` (Semikolon, Windows-Zeichensatz). Ausweisnummer = Pass-Nr. (sonst Spieler-ID, sonst Ersatznummer 90000+/95000+), Waffenart aus der Disziplin der Mannschaft (LG, LGA, LP, LPA, LPS, KK, KKA, ZS, ZSA, ZSTRD, KYFFH). Jahrgang und Geschlecht stehen im Benutzerprofil und kommen auch per Import. Dieselbe Datei lässt sich umgekehrt als Schützen-Import einlesen.
- **DISAG-Startliste:** Im Standplan als CSV herunterladen (Semikolon, Windows-Zeichensatz): Durchgang, Startzeit, Stand, Nachname, Vorname, Pass-Nr., Spieler-ID, Verein, Mannschaft, Heim/Gast, Disziplin, Datum, Wettkampf. Die Spalten lassen sich an das Importformat des OpticScore Servers anpassen (`DISAG_COLUMNS` in `app.py`).

## RWK-Onlinemelder

Der RWK-Onlinemelder hat keine Schnittstelle. Die App bindet ihn so an:

- **Wettkampfplan importieren:** In der Mannschaft auf *Wettkampfplan importieren* tippen. Im RWK-Onlinemelder den Wettkampfplan öffnen, die eigene Mannschaft wählen und über das Kalender-Symbol die .ics-Datei speichern. Diese Datei hochladen und die Vorschau prüfen. Ein erneuter Import aktualisiert geänderte Termine, statt sie doppelt anzulegen. Der Kalender enthält meist die ganze Klasse: Die App erkennt eure Mannschaft (auch bei kleinen Tippfehlern im Namen), zeigt nur eure Termine, übernimmt Klasse und Saison und legt auf Wunsch alle Gegner der Klasse mit Schießstand-Adresse in der Gegnerliste an.
- **Link zur Klasse:** Unter *Mannschaft bearbeiten* den Link zur Tabelle im RWK-Onlinemelder hinterlegen.
- **Termin verlegen:** Beim Wettkampf beantragt der Mannschaftsführer einen neuen Termin. Die App erstellt den Antragstext für Gegner und Rundenwettkampfleiter. Nach deren Zustimmung auf *Bestätigt* tippen: Der Termin wird geändert und alle sagen neu zu. Ein späterer RWK-Import überschreibt verlegte Termine nicht ungefragt.
- **Ergebnis melden:** Sobald Ringe eingetragen sind, zeigt der Wettkampf das Ergebnis fertig zum Abtippen beziehungsweise Kopieren, mit einem Knopf zum RWK-Onlinemelder.

## Auf einem Cloud-VPS betreiben

Auf einem VPS (Ubuntu oder Debian) übernimmt Caddy HTTPS mit automatischem Let's-Encrypt-Zertifikat.

1. DNS: Einen A-Eintrag (z. B. `mannschaft.meinverein.de`) auf die IP des VPS setzen.
2. Firewall: Nur die Ports 22, 80 und 443 öffnen (`ufw allow OpenSSH; ufw allow 80; ufw allow 443/tcp; ufw allow 443/udp; ufw enable`).
3. Docker installieren (`curl -fsSL https://get.docker.com | sh`), Repository nach `/opt/schuetzenverein` klonen, `data/` anlegen (`chown 1000:1000`).
4. `.env` wie oben, zusätzlich:
   ```
   DOMAIN=mannschaft.meinverein.de
   COMPOSE_FILE=docker-compose.yml:docker-compose.caddy.yml
   BIND_IP=127.0.0.1
   COOKIE_SECURE=1
   TRUST_PROXY=1
   ```
5. `docker compose up -d --build` – nach einer Minute ist die App unter `https://<DOMAIN>` erreichbar.

Port 8000 ist dabei nur intern erreichbar (`BIND_IP=127.0.0.1`). Updates wie gewohnt mit `./update.sh`.

## Von außen erreichbar machen

Damit Mitglieder auch von zu Hause darauf zugreifen können, braucht ihr einen Reverse Proxy mit HTTPS, zum Beispiel Nginx Proxy Manager, Caddy oder Traefik, oder eine Weiterleitung über die Sophos-Firewall. Stellt die App **nicht ohne HTTPS** ins Internet. Läuft alles über HTTPS, setzt in der `.env` den Wert `COOKIE_SECURE=1`.

Alternativ geht es ohne offenen Port über einen Cloudflare Tunnel oder Tailscale.

## Sicherheit

Eingebaut:
- Passwörter nur als Hash gespeichert, Mindestlänge 8, Pflicht zum Ändern beim ersten Login
- Sperre nach 10 Fehlversuchen in 10 Minuten, pro IP und pro Benutzername
- Nach einem Passwortwechsel (auch durch den Admin) werden alle anderen Sitzungen dieses Benutzers abgemeldet
- Schutz gegen gefälschte Formulare (CSRF), Cross-Site-Scripting, SQL-Injection und Weiterleitungen auf fremde Seiten
- Sicherheits-Header (Content-Security-Policy, Clickjacking-Schutz, kein Caching persönlicher Seiten, HSTS bei HTTPS)
- Profilbilder werden geprüft und neu gespeichert, übergroße Bilder abgelehnt, Uploads höchstens 10 MB
- Container ohne Root, ohne Zusatzrechte, mit schreibgeschütztem Dateisystem

Checkliste vor dem Livegang:
1. `SECRET_KEY` mit `openssl rand -hex 32` setzen.
2. Starkes `ADMIN_PASSWORD` setzen oder leer lassen und das erzeugte aus dem Log nehmen.
3. Nur über HTTPS veröffentlichen (Reverse Proxy oder Cloudflare Tunnel), dann `COOKIE_SECURE=1` und `TRUST_PROXY=1`.
4. `BIND_IP=127.0.0.1`, wenn Proxy oder Tunnel auf demselben Host laufen. Sonst Port 8000 in der Firewall nur für den Proxy öffnen. Port 8000 nie direkt ins Internet weiterleiten.
5. Den Server in ein eigenes VLAN/DMZ legen, damit er nicht ins restliche Netz kommt.
6. Backups einrichten (siehe unten) und `data/` vor fremdem Zugriff schützen (`chmod 700 data`). Die Datenbank enthält Namen, Pass-Nummern und Login-IPs.
7. Regelmäßig aktualisieren: `docker compose build --pull && docker compose up -d`.
8. Datenschutz: Mitglieder darüber informieren, dass Anmeldungen mit IP und Gerät 90 Tage protokolliert werden.

## Wartung, Sicherung, Updates

Im Ordner `scripts/` liegt alles für den sicheren Betrieb. Einmal einrichten (als root):

```bash
cd /opt/schuetzenverein
bash scripts/einrichten.sh
```

Das richtet ein:
- **Automatische Linux-Sicherheitsupdates** jede Nacht, Neustart bei Kernel-Updates um 04:30 (App und Tunnel starten von selbst wieder)
- **fail2ban**: sperrt IPs nach 4 falschen SSH-Anmeldungen für 1 Stunde, bei Wiederholung immer länger (bis 1 Woche)
- **SSH**: höchstens 4 Versuche je Verbindung, Passwort-Anmeldung bleibt erlaubt (nur Schlüssel: `SSH_NUR_SCHLUESSEL=1 bash scripts/einrichten.sh`)
- **Firewall** (ufw) mit offenem SSH, **Docker-Logs** begrenzt auf 3 × 10 MB
- **Zeitpläne**: Sicherung täglich 03:15, Update-Prüfung täglich 06:30, Wartung sonntags 04:00

| Befehl | Was es tut |
|---|---|
| `./update.sh` | Neue App-Version von GitHub holen. Sichert vorher, prüft danach den Start und stellt bei einem Fehler automatisch die vorherige Version wieder her. |
| `./scripts/status.sh` | Überblick: App, letzte Sicherung, anstehende Updates, Neustart nötig, Firewall, fail2ban, SSH |
| `./scripts/backup.sh` | Sicherung jetzt (Datenbank, Profilbilder, `.env`) nach `/opt/backup`, 14 Tage aufbewahrt |
| `./scripts/restore.sh` | Sicherungen anzeigen; mit Dateiname zurückspielen (sichert vorher den aktuellen Stand) |
| `./scripts/pruefen.sh` | Prüft auf Linux-Paket-Updates und neue App-Versionen, ohne etwas zu installieren |
| `./scripts/wartung.sh` | Alles auf einmal: Sicherung, Linux-Updates, App-Image neu bauen, aufräumen, Gesundheitscheck |

Die **Admin-Übersicht** der App zeigt unter *Wartung & Updates* die letzte Sicherung, anstehende Linux-Pakete (Sicherheitsupdates markiert), ob ein Neustart nötig ist und ob es eine neue App-Version auf GitHub gibt.

Sicherungen liegen auf demselben Server. Kopiere `/opt/backup` regelmäßig woandershin, zum Beispiel per `scp` oder `rsync` auf dein NAS.

## Update

Die App liegt in einem privaten GitHub-Repository und wird auf dem Server mit Git aktualisiert:

```bash
cd /opt/schuetzenverein
./update.sh          # holt die neue Version (git pull), sichert vorher die Datenbank, baut neu
./update.sh --force  # baut auch ohne neue Version neu (aktuelle Sicherheitsupdates im Image)
```

`.env` und `data/` stehen in der `.gitignore` und landen nie auf GitHub.

## Benutzer sperren oder löschen

Unter *Benutzer* einen Benutzer öffnen:
- **Zugang sperren** (optional mit Grund): Der Benutzer wird sofort auf allen Geräten abgemeldet und kann sich nicht mehr anmelden. Ergebnisse und Statistik bleiben erhalten. Gesperrte stehen in der Liste unter *Gesperrt* und lassen sich dort mit **Entsperren** wieder freischalten. Das ist der richtige Weg bei Austritt oder Pause.
- **Vorübergehend gesperrt:** Nach 10 falschen Passwörtern ist ein Konto 10 Minuten gesperrt. Der Admin sieht das in der Liste und kann die Sperre sofort aufheben.
- **Endgültig löschen:** Zeigt vorher, was mit gelöscht wird (Ergebnisse, Aufstellungen, Rückmeldungen). Zur Sicherheit muss der Benutzername eingetippt werden.

Sich selbst kann der Admin weder sperren noch löschen.

## Viele Benutzer auf einmal bearbeiten

In der Benutzerliste nach Verein, Rolle, Mannschaft, Gruppe oder „nie angemeldet“ filtern, oben links alle angezeigten auswählen (oder einzelne anhaken) und eine Aktion wählen: sperren, entsperren, Rolle ändern oder endgültig löschen. Zum Löschen muss `LÖSCHEN` eingetippt werden. Das eigene Konto ist nie auswählbar.

Beim Import von Schützen mit Vereinsspalte fragt die App vorher je Verein: als Mitglieder anlegen, als Gegnerschützen übernehmen oder nicht importieren.

## Passwort vergessen?

Der Admin öffnet den Benutzer, trägt ein neues Passwort ein und speichert. Beim nächsten Login muss der Benutzer es ändern.
