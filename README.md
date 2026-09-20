# aliexpress-coin-collector

Sammelt den täglichen Coin-Check-in der AliExpress-App automatisch ein. Ein Android-Gerät (altes Handy oder
Tablet) wird per ADB über WLAN gesteuert, die Coin-Seite per Deep-Link geöffnet und der "Sammeln"-Button per
OCR (Tesseract) gefunden. Ergebnisse gehen per Discord raus, jeder Lauf landet in einer SQLite-Datenbank.

> Version 0.3.0. Der "erledigt"-Zustand, der Münzstand und die Navigation sind auf echter Hardware getestet.
> Der Tap auf "Sammeln" ist mit Attrappen getestet, auf dem Gerät aber noch offen (siehe [Status](#status)).

## Inhalt

- [Wie ein Lauf abläuft](#wie-ein-lauf-abläuft)
- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
- [Aktualisieren](#aktualisieren)
- [Gerät einrichten](#gerät-einrichten)
- [Erster Test](#erster-test)
- [Betrieb](#betrieb)
- [Weboberfläche](#weboberfläche)
- [Konfiguration](#konfiguration)
- [Ergebnisse und Fehlersuche](#ergebnisse-und-fehlersuche)
- [Status](#status)
- [Grenzen und Risiken](#grenzen-und-risiken)
- [Entwicklung](#entwicklung)
- [Lizenz und Rechtliches](#lizenz-und-rechtliches)

## Wie ein Lauf abläuft

1. ADB-Verbindung prüfen, sonst Meldung "Gerät nicht erreichbar".
2. Ist der Bildschirm an und `SKIP_IF_AWAKE=true`, gilt das Gerät als in Benutzung: Lauf überspringen, später erneut.
3. Bildschirm wecken, App beenden, Coin-Seite per Deep-Link öffnen. Bleibt die Seite unerkannt, einmal neu starten.
4. Screenshot alle 3 s auswerten, bis "Sammeln" (nicht eingecheckt) oder der Marker "morgen" (schon erledigt) erscheint.
5. Bei "Sammeln": tippen und prüfen, dass der Button weg ist und der Münzstand gestiegen ist.
6. Bildschirm wieder ausschalten, aber nur, wenn ihn das Skript selbst geweckt hat.

**Zeitplan:** Ein Lauf zu einer zufälligen, pro Tag festen Uhrzeit im Morgenfenster (Standard 07:00–10:00). Schlägt er
fehl, gibt es einen einzigen Nachholversuch im Abendfenster (Standard 19:00–21:00). Gelingt bis dahin nichts, kommt eine
Discord-Meldung mit Screenshot. Die Uhrzeiten hängen nur vom Datum ab und überleben Neustarts des Dienstes.

## Voraussetzungen

- Ein Android-Gerät mit installierter, eingeloggter AliExpress-App, das dauerhaft am Strom hängt und per ADB über WLAN
  erreichbar ist (bei Android 11+ funktioniert der klassische Weg `adb tcpip 5555` weiterhin).
- Ein Debian/Ubuntu-Container oder -Server im selben Netz (oder mit Firewall-Freigabe für TCP 5555 zum Gerät).
  Die Zeitzone des Servers muss die Ortszeit sein, denn die Zeitfenster gelten in Serverzeit.
- Optional ein Discord-Webhook für Meldungen.

## Installation

### Empfohlen: herunterladen, ansehen, ausführen

Das Skript läuft als root und installiert Pakete. Deshalb lohnt sich der Blick hinein, bevor es startet:

```bash
curl -fsSL https://raw.githubusercontent.com/fgrfn/aliexpress-coin-collector/main/install.sh -o install.sh
less install.sh
bash install.sh
```

Findet `install.sh` neben sich keinen Quellbaum, lädt es die neueste veröffentlichte Version selbst als Tarball
und installiert daraus. Weder `git` noch eine lokale Kopie sind nötig. Liegt dagegen ein Checkout daneben, wird
dieser verwendet — es ist dasselbe Skript für beide Wege.

### Kurzform

```bash
curl -fsSL https://raw.githubusercontent.com/fgrfn/aliexpress-coin-collector/main/install.sh | bash
```

Das Skript ist gegen abgebrochene Downloads abgesichert: Der gesamte Code steht in Funktionen, die erst in der
letzten Zeile aufgerufen werden. Bricht die Übertragung vorher ab, führt bash nichts aus.

### Aus einer lokalen Kopie

Projekt in den Container kopieren (z. B. per `scp`), dort als root:

```bash
cd aliexpress-coin-collector
./install.sh
```

Das Skript installiert `adb`, Tesseract mit deutschem Sprachpaket und die Python-Abhängigkeiten, legt den Benutzer
`coins` und `/opt/aliexpress-coin-collector` an, fragt nach Geräte-Adresse und Webhook und installiert den
systemd-Dienst, **startet ihn aber noch nicht**. Nicht-interaktiv:
`ADB_SERIAL=192.168.1.50:5555 DISCORD_WEBHOOK_URL=... ./install.sh`.

**Update von der Vorgängerversion (`aliexpress-coins`):** `./install.sh` erkennt `/opt/aliexpress-coins`, stoppt den
alten Dienst und verschiebt das Verzeichnis samt `.env`, Datenbank und ADB-Freigabe an den neuen Ort. Danach den neuen
Dienst starten:

```bash
systemctl enable --now aliexpress-coin-collector
```

### Aktualisieren

```bash
cd /opt/aliexpress-coin-collector && ./install.sh --update
```

Holt die neueste veröffentlichte Version von GitHub, sichert den alten Programmstand, tauscht die Dateien aus, baut
die Python-Umgebung neu und prüft zum Schluss, ob `--version` noch läuft. Scheitert dieser Rauchtest, wird der
vorherige Stand automatisch wiederhergestellt und der Dienst wieder gestartet, falls er vorher lief.

**`.env`, `data/` und `.android/` werden dabei nie angefasst.** Mit `--ref v0.3.0` oder `--ref main` lässt sich ein
bestimmter Stand erzwingen.

Ein erneuter Aufruf von `./install.sh` ohne Argumente aktualisiert aus dem aktuellen Verzeichnis und meldet das auch
so; eine vorhandene `.env` bleibt dabei immer unverändert. `./install.sh --help` erklärt Aufruf und Umgebungsvariablen und
ändert nichts. `./install.sh --uninstall` stoppt den Dienst und entfernt die systemd-Unit; `.env`, `data/` und
`.android/` bleiben erhalten, gelöscht wird nur nach ausdrücklicher Bestätigung mit `JA`.

## Gerät einrichten

1. Entwickleroptionen aktivieren, **USB-Debugging** einschalten, Bildschirmsperre auf "Keine" stellen.
2. Gerät per USB an einen Rechner, dort `adb tcpip 5555`, dann Kabel abziehen.
3. Im Router eine feste IP (DHCP-Reservierung) für das Gerät anlegen.
4. **Als Dienstbenutzer** einmal verbinden und den Dialog auf dem Gerät mit "Immer zulassen" bestätigen:
   ```bash
   runuser -u coins -- adb connect <geraete-ip>:5555
   ```
5. Konto in der App einmal von Hand anmelden. Sprache Deutsch lassen (oder `BUTTON_LABELS` anpassen).
6. Bei langsamen Geräten in den Entwickleroptionen die Animationen abschalten.

`adb tcpip 5555` gilt nur bis zum nächsten Neustart des Geräts. Danach meldet der Dienst "nicht erreichbar", und der
Schritt muss per USB wiederholt werden.

Auf Tablets zeigt die App die Coin-Seite im Tablet-Layout teils nicht an, der Deep-Link öffnet sie aber trotzdem.
Eine geänderte Bildschirmdichte (`wm density`) ist nicht nötig.

## Erster Test

```bash
cd /opt/aliexpress-coin-collector
runuser -u coins -- .venv/bin/python -m aliexpress_coin_collector doctor
runuser -u coins -- .venv/bin/python -m aliexpress_coin_collector once --force --no-notify
runuser -u coins -- .venv/bin/python -m aliexpress_coin_collector status
```

`doctor` prüft Installation, Tesseract-Sprache und die Verbindung zum Gerät, dazu die Umgebung: ob eine `.env` gefunden wurde, ob die Zeitzone plausibel ist (bei UTC gibt es eine Warnung, denn die Zeitfenster gelten in Serverzeit), ob das Datenverzeichnis beschreibbar ist, sowie den heutigen Plan und den letzten Lauf. Ist heute schon eingecheckt, meldet der
Lauf `already_done`; sonst tippt er auf "Sammeln". Bei einem Fehler liegt der letzte Screenshot in
`data/last_failure.png`, mit `LOG_LEVEL=DEBUG` sieht man die Erkennung pro Screenshot.

Erkennung an einem gespeicherten Screenshot testen. Den Screenshot **ohne** Shell-Umleitung ziehen, also mit
`adb shell screencap -p /sdcard/s.png` und `adb pull` (die Umleitung `>` beschädigt PNGs in PowerShell):

```bash
.venv/bin/python -m aliexpress_coin_collector ocr screen.png --text
```

## Betrieb

```bash
systemctl enable --now aliexpress-coin-collector
journalctl -u aliexpress-coin-collector -f
```

| Befehl | Zweck |
|---|---|
| `doctor` | Installation, Tesseract-Sprache, Geräteverbindung, Screenshot prüfen |
| `once [--force] [--no-notify] [--no-record]` | Einen Lauf sofort ausführen |
| `daemon` | Dauerbetrieb mit Zeitplan (das startet der Dienst) |
| `ocr <bild.png> [--text]` | Erkennung an einem Screenshot testen |
| `status [-n 14]` | Letzte Läufe mit Dauer und Summen aus der Datenbank |
| `schedule [-n 7]` | Geplante Uhrzeiten der nächsten Tage und wann der nächste Lauf ansteht |
| `--version` | Version anzeigen |

Alle Befehle als `python -m aliexpress_coin_collector <befehl>`.

## Weboberfläche

Optional, als **zweiter** Dienst. `install.sh` legt die Unit an, startet sie aber nicht — der tägliche
Check-in soll nicht von ihr abhängen.

```bash
# WEB_PASSWORD in der .env setzen, dann:
systemctl enable --now aliexpress-coin-collector-web
```

Danach erreichbar unter `http://<container-ip>/`. Die Seite zeigt einen Statuskopf (letzter Lauf,
nächster Lauf, Münzstand, abgeleitete Streak, ob der Dienst läuft), den Münzverlauf als Diagramm und
die Historie der letzten Läufe mit anklickbaren Fehler-Screenshots. Die Zeitfenster lassen sich dort
ändern; eine Änderung gilt sofort, aber nicht rückwirkend — liegt die neu ausgewürfelte Uhrzeit schon
in der Vergangenheit, läuft an diesem Tag nichts mehr.

**Ohne `WEB_PASSWORD` startet der Dienst nicht.** Die Seite kann einen Lauf auf dem Gerät auslösen,
deshalb ist die Anmeldung Pflicht und nicht abschaltbar. Sie gehört ins LAN und nicht ins Internet.

Der Knopf „Lauf jetzt starten" fasst das Gerät **nicht** selbst an: Er legt nur eine Datei
`data/run-requested` an, die der Sammel-Dienst beim nächsten Takt abholt. Damit bleibt genau ein
Besitzer des Geräts, und zwei gleichzeitige Läufe sind bauartbedingt ausgeschlossen. Der Knopf ist
gesperrt, wenn heute schon erfolgreich eingecheckt wurde, wenn bereits ein Auftrag offen ist oder
wenn der Dienst kein Lebenszeichen mehr gibt.

Die abgeleitete Streak zählt aufeinanderfolgende Tage mit Erfolg aus der Datenbank. Tage, die vor dem
ersten Lauf des Dienstes von Hand gesammelt wurden, kennt sie nicht — dafür gibt es `STREAK_OFFSET`.

## Konfiguration

Alle Werte stehen in der `.env` (Vorlage `.env.example`). Umgebungsvariablen haben Vorrang.

| Variable | Standard | Bedeutung |
|---|---|---|
| `ADB_SERIAL` | – (Pflicht) | Adresse des Geräts, z. B. `192.168.1.50:5555` |
| `DISCORD_WEBHOOK_URL` | leer | Webhook für Meldungen (leer = nur Log) |
| `MORNING_START` / `MORNING_END` | `07:00` / `10:00` | Fenster für den Morgenlauf |
| `EVENING_START` / `EVENING_END` | `19:00` / `21:00` | Fenster für den Nachholversuch |
| `SKIP_IF_AWAKE` | `true` | Nicht starten, solange der Bildschirm an ist. Bei einem reinen Automatisierungsgerät `false` |
| `BUSY_RETRY_MIN` | `15` | Abstand der Wiederholung, wenn das Gerät in Benutzung ist |
| `BUSY_MAX_WAIT_MIN` | `120` | Danach wird trotz eingeschaltetem Bildschirm gestartet |
| `PAGE_TIMEOUT_S` | `90` | Wartezeit pro Startversuch auf die Coin-Seite |
| `CONFIRM_TIMEOUT_S` | `25` | Wartezeit auf die Bestätigung nach dem Tap |
| `LAUNCH_RETRIES` | `1` | Zusätzliche Startversuche mit App-Neustart |
| `BUTTON_LABELS` | `Sammeln,Collect,Claim` | Mögliche Beschriftungen des Buttons |
| `OCR_LANG` | `deu` | Tesseract-Sprache |
| `NOTIFY_ON_SUCCESS` / `NOTIFY_ON_ALREADY_DONE` | `true` / `false` | Wann Erfolgsmeldungen kommen (Fehler werden immer gemeldet) |
| `DATA_DIR` | `./data` | Datenbank und Fehler-Screenshots (die letzten 30) |
| `LOG_LEVEL` | `INFO` | `DEBUG` zeigt die Erkennung pro Screenshot |
| `WEB_PASSWORD` | – | Pflicht für die Weboberfläche, ohne sie startet der Webdienst nicht |
| `WEB_PORT` | `80` | Port der Weboberfläche |
| `STREAK_OFFSET` | `0` | Tage, die vor dem ersten Lauf von Hand gesammelt wurden |
| `COIN_URL`, `APP_PACKAGE`, `ADB_PATH` | siehe `.env.example` | Nur ändern, wenn AliExpress die Adresse der Coin-Seite ändert |

## Ergebnisse und Fehlersuche

| Ergebnis | Bedeutung |
|---|---|
| `claimed` | Eingesammelt und bestätigt (Button weg, Münzstand gestiegen oder Erledigt-Marker) |
| `already_done` | Die Seite zeigte schon den Erledigt-Zustand, es wurde nichts angetippt |
| `busy` | Gerät in Benutzung, Lauf übersprungen (zählt nicht als Versuch) |
| `unreachable` | ADB-Verbindung fehlt. Nach einem Neustart des Geräts: per USB `adb tcpip 5555` wiederholen |
| `not_found` | Weder Button noch Erledigt-Zustand erkannt: Login abgelaufen, Popup, geändertes Layout. Screenshot prüfen |
| `unconfirmed` | Getippt, Erfolg aber nicht bestätigt. Screenshot prüfen |
| `error` | Unerwarteter Fehler, Details im Log |

Die Datenbank (`data/coins.sqlite3`, Tabelle `runs`) enthält Uhrzeit, Art, Ergebnis, Münzstand vorher/nachher und
Dauer jedes Versuchs. Sie ist die Grundlage für spätere Auswertungen, z. B. zu welcher Uhrzeit AliExpress den Tag
umschaltet.

## Status

| Bereich | Stand |
|---|---|
| Deep-Link auf die Coin-Seite, Kaltstart | auf Gerät getestet |
| Erkennung Erledigt-Zustand und Münzstand | auf echten Screenshots (720×1280 und 1200×1920) getestet |
| Erkennung "Sammeln" (weiß auf orange) | an einem Video-Frame getestet, mit Schwellwert-Vorverarbeitung |
| Tap auf "Sammeln" und Bestätigung | Ablauf mit Attrappen getestet, **auf dem Gerät ausstehend** |
| Discord-Meldungen | Testnachricht getestet |
| Dauerbetrieb (systemd, Zeitplan) | läuft, Ergebnis mehrerer Tage ausstehend |

Getestete Geräte: Samsung SM-J330FN (Android 9, 720×1280, Standard-Dichte 320, langsam: ca. 30 s bis zur geladenen
Seite) und ein Android-Tablet (1200×1920, Dichte 280).

## Grenzen und Risiken

- **Keine Verbindung zu AliExpress.** Dieses Projekt ist ein privates Hobbyprojekt und steht in keinerlei
  Beziehung zu AliExpress, Alibaba oder deren Tochterunternehmen und wird von ihnen weder unterstützt noch geprüft.
  Genannte Namen und Marken gehören ihren jeweiligen Inhabern.
- **Nutzungsbedingungen:** Automatisierte Zugriffe auf die App sind sehr wahrscheinlich nicht erlaubt. Mögliche Folgen
  sind verfallene Coins oder ein eingeschränktes Konto. Nutzung auf eigene Gefahr. Das Projekt umgeht weder Captchas
  noch Verifizierungen noch den Login.
- Die Coin-Seite ist ein WebView ohne Element-IDs, es bleibt nur Bilderkennung. Ändert AliExpress Text oder Design des
  Buttons, müssen `BUTTON_LABELS` oder die Erkennung angepasst werden.
- Ein abgelaufener Login wird nicht automatisch behoben: Der Lauf endet mit `not_found` und einem Screenshot.
- Wann AliExpress den Tag umschaltet (Ortszeit oder Zeit einer anderen Zeitzone), ist nicht gesichert. Das Skript
  prüft deshalb immer den Seitenzustand, statt einen Kalendertag anzunehmen.
- Dauerbetrieb eines alten Geräts am Ladekabel belastet den Akku. Ein Ladelimit oder eine schaltbare Steckdose
  (z. B. über Home Assistant) ist sinnvoll.

## Entwicklung

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest ruff
.venv/bin/python -m pytest -q     # Tests (ohne Gerät, mit Attrappen)
.venv/bin/ruff check .            # Codeprüfung
```

Aufbau: `adb.py` (ADB-Wrapper), `ocr.py` (Seitenerkennung), `runner.py` (Ablauf eines Laufs), `scheduler.py`
(Zeitplan, Entscheidungslogik, Dienstschleife), `store.py` (SQLite), `notify.py` (Discord), `config.py` (`.env`).
GitHub Actions führt bei jedem Push und jedem Pull Request `ruff check`, `ruff format --check`, die Tests auf
Python 3.10 und 3.12 sowie `shellcheck` aus. Technische Details, was auf echter Hardware verifiziert ist, die
bekannten Stolpersteine und die offenen Punkte stehen in [`docs/STATUS.md`](docs/STATUS.md).

Die `.env` enthält Webhook-URL und Geräteadresse und steht deshalb in `.gitignore` — bitte nicht committen.

## Lizenz und Rechtliches

[MIT](LICENSE). Die Software wird ohne jede Gewährleistung bereitgestellt; die Nutzung erfolgt auf eigene Gefahr,
siehe [Grenzen und Risiken](#grenzen-und-risiken).
