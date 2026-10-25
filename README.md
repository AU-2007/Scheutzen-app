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
- **Gegner** (Admin, Mannschaftsführer): Liste der Gegner-Vereine und -Mannschaften, die beim Anlegen eines Wettkampfs vorgeschlagen werden.
- **Import** (Verein → Daten importieren): Schützen (Name, Spieler-ID, Pass-Nr., Verein) oder Gegner aus einer CSV-Datei, zum Beispiel aus dem Stammdaten-Export des DISAG OpticScore Servers oder aus Excel. Neue Schützen bekommen ein Startpasswort, das nach dem Import einmal angezeigt wird.

## RWK-Onlinemelder

Der RWK-Onlinemelder hat keine Schnittstelle. Die App bindet ihn so an:

- **Wettkampfplan importieren:** In der Mannschaft auf *Wettkampfplan importieren* tippen. Im RWK-Onlinemelder den Wettkampfplan öffnen, die eigene Mannschaft wählen und über das Kalender-Symbol die .ics-Datei speichern. Diese Datei hochladen und die Vorschau prüfen. Ein erneuter Import aktualisiert geänderte Termine, statt sie doppelt anzulegen.
- **Link zur Klasse:** Unter *Mannschaft bearbeiten* den Link zur Tabelle im RWK-Onlinemelder hinterlegen.
- **Termin verlegen:** Beim Wettkampf beantragt der Mannschaftsführer einen neuen Termin. Die App erstellt den Antragstext für Gegner und Rundenwettkampfleiter. Nach deren Zustimmung auf *Bestätigt* tippen: Der Termin wird geändert und alle sagen neu zu. Ein späterer RWK-Import überschreibt verlegte Termine nicht ungefragt.
- **Ergebnis melden:** Sobald Ringe eingetragen sind, zeigt der Wettkampf das Ergebnis fertig zum Abtippen beziehungsweise Kopieren, mit einem Knopf zum RWK-Onlinemelder.

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

## Backup

Alle Daten liegen in einer einzigen Datei: `data/verein.db`. Am sichersten ist ein Backup im laufenden Betrieb so:

```bash
sqlite3 data/verein.db ".backup 'backup-$(date +%F).db'"
```

Alternativ: den Ordner `data/` in eure Proxmox- oder TrueNAS-Sicherung aufnehmen.

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

## Passwort vergessen?

Der Admin öffnet den Benutzer, trägt ein neues Passwort ein und speichert. Beim nächsten Login muss der Benutzer es ändern.
