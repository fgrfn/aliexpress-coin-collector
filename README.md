<p align="center"><img src="docs/logo.svg" width="150" alt=""></p>

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

Als root im Zielcontainer:

```bash
curl -fsSL https://raw.githubusercontent.com/fgrfn/aliexpress-coin-collector/main/install.sh | bash
```

Aus einem vorhandenen Checkout heraus genügt `./install.sh`.

`install.sh` erkennt selbst, woraus es installiert: Liegt ein Quellbaum daneben, nimmt es den; sonst lädt es die
neueste veröffentlichte Version als Tarball. `git` ist nie nötig. Gegen abgebrochene Downloads ist es abgesichert —
aller Code steht in Funktionen, die erst in der letzten Zeile aufgerufen werden.

Installiert werden `adb`, Tesseract mit deutschem Sprachpaket und die Python-Abhängigkeiten; angelegt werden der
Benutzer `coins`, `/opt/aliexpress-coin-collector` und die systemd-Units — **gestartet wird nichts**. Ohne Rückfragen:
`ADB_SERIAL=192.168.1.50:5555 DISCORD_WEBHOOK_URL=... ./install.sh`.

```bash
systemctl enable --now aliexpress-coin-collector
```

| Aufruf | Wirkung |
|---|---|
| `./install.sh` | Neuinstallation oder Aktualisierung aus dem aktuellen Verzeichnis |
| `./install.sh --update [--ref v0.3.0]` | Holt die neueste Veröffentlichung von GitHub, mit Rauchtest und Rollback |
| `./install.sh --uninstall` | Stoppt den Dienst, entfernt die Unit. Daten bleiben, Löschen nur nach Eingabe von `JA` |
| `./install.sh --help` | Erklärt Aufruf und Umgebungsvariablen, verändert nichts |

**`.env`, `data/` und `.android/` werden nie überschrieben** — weder beim Update noch bei einer erneuten Installation.

Scheitert nach einem `--update` der abschließende Rauchtest, stellt das Skript den vorherigen Stand selbst wieder her
und startet den Dienst neu, falls er vorher lief.

**Update von der Vorgängerversion `aliexpress-coins`:** wird erkannt, der alte Dienst gestoppt und das Verzeichnis
samt `.env`, Datenbank und ADB-Freigabe an den neuen Ort verschoben.

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
acc doctor
acc once --force --no-notify
acc status
```

`acc` legt `install.sh` nach `/usr/local/bin`. Der Kurzbefehl wechselt selbst ins
Installationsverzeichnis (die `.env` wird relativ gelesen) und wechselt auf den Dienstbenutzer —
als `root` aufgerufen entstünden sonst Dateien in `data/`, an die der Dienst danach nicht mehr
herankommt. Ohne ihn wäre jeder Aufruf:

```bash
cd /opt/aliexpress-coin-collector && runuser -u coins -- .venv/bin/python -m aliexpress_coin_collector status
```

`doctor` prüft Installation, Tesseract-Sprache und die Verbindung zum Gerät, dazu die Umgebung: ob eine `.env` gefunden wurde, ob die Zeitzone plausibel ist (bei UTC gibt es eine Warnung, denn die Zeitfenster gelten in Serverzeit), ob das Datenverzeichnis beschreibbar ist, sowie den heutigen Plan und den letzten Lauf. Ist heute schon eingecheckt, meldet der
Lauf `already_done`; sonst tippt er auf "Sammeln". Bei einem Fehler liegt der letzte Screenshot in
`data/last_failure.png`, mit `LOG_LEVEL=DEBUG` sieht man die Erkennung pro Screenshot.

Erkennung an einem gespeicherten Screenshot testen. Den Screenshot **ohne** Shell-Umleitung ziehen, also mit
`adb shell screencap -p /sdcard/s.png` und `adb pull` (die Umleitung `>` beschädigt PNGs in PowerShell):

```bash
acc ocr screen.png --text
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
| `notify-test` | Testmeldung an Discord schicken (ohne Gerät) |
| `--version` | Version anzeigen |

Alle Befehle als `acc <befehl>` auf dem installierten System. Im Quellbaum, etwa zur Entwicklung,
stattdessen `python -m aliexpress_coin_collector <befehl>`.

## Weboberfläche

Ein **zweiter** Dienst, den `install.sh` mitinstalliert, aktiviert und startet. Er fasst weder ADB
noch das Gerät an, deshalb darf er sofort laufen — anders als der Sammel-Dienst.

Erreichbar unter `http://<container-ip>/`. **Beim ersten Aufruf vergibst du dort das Passwort**; bis
dahin zeigt die Seite nichts an und es lässt sich kein Lauf auslösen. Gespeichert wird nur ein Hash
(PBKDF2-SHA256) in `data/web-password`, nie das Passwort selbst. Ändern lässt es sich unter
**Einstellungen → Zugang**; danach sind alle angemeldeten Browser abgemeldet.

Die Übersicht zeigt einen Statuskopf (letzter Lauf, nächster Lauf, Münzstand, abgeleitete Streak,
ob der Dienst läuft), den Münzverlauf als Diagramm, die heute geplanten Uhrzeiten und die Historie
der letzten Läufe mit anklickbaren Fehler-Screenshots.

Statuskopf und Dienstzustand aktualisieren sich alle fünf Sekunden von allein, der Rest der Seite
bleibt stehen. Sie ist auf dem Handy genauso bedienbar wie am Rechner, und folgt der Hell-Dunkel-
Einstellung des Systems — der Knopf oben rechts überstimmt sie.

**Nichts wird von außen nachgeladen.** Stylesheet, htmx und die Schrift liegen im Repo und kommen
vom eigenen Dienst; die Seite funktioniert ohne Internetverbindung, und beim Aufruf geht keine
Anfrage nach draußen.

Die Anmeldung ist Pflicht und nicht abschaltbar, weil die Seite einen Lauf auf dem Gerät auslösen
kann. Sie gehört ins LAN und nicht ins Internet.

### Gerät & Dienst

Eine zweite Seite zeigt, in welchem Zustand die ADB-Verbindung ist — verbunden, nicht freigegeben
oder nicht erreichbar —, wann der Dienst das zuletzt geprüft hat und ob der Bildschirm an ist. Von
dort lassen sich die Verbindung neu aufbauen, ein Screenshot anfordern und die beiden Dienste
starten, stoppen und neu starten.

**Die Oberfläche fasst das Gerät nie selbst an.** Sie legt einen Auftrag in `data/commands/` ab, den
der Sammel-Dienst beim nächsten Takt abholt — also binnen 30 Sekunden. Damit bleibt genau ein
Besitzer des Geräts, und zwei gleichzeitige Zugriffe sind bauartbedingt ausgeschlossen. Die
Auftragsschlange steht auf der Seite, damit die Wartezeit sichtbar ist statt versteckt.

Für die Dienststeuerung braucht die Oberfläche **kein `sudo`**: sie läuft bewusst ohne das Recht,
sich Rechte zu holen (`NoNewPrivileges=yes`). Sie schreibt stattdessen eine Zeile aus zwei geprüften
Wörtern nach `data/control`; eine systemd-Pfadeinheit bemerkt das und startet `control.sh` als root,
das aus diesen Wörtern selbst den `systemctl`-Aufruf zusammensetzt. Alles, was nicht exakt passt,
wird abgelehnt. Es wird nie ein Befehl durchgereicht.

Reißt die TCP-Verbindung ab, holt der Dienst sie von allein zurück: `adb get-state` fragt nur den
lokalen adb-Server und baut nie neu auf, deshalb versucht die Zustandsmeldung selbst ein `connect`
— sofort, dann mit wachsendem Abstand (1, 2, 5, 10, danach 30 Minuten). Der Abstand ist nötig, weil
ein `connect` auf ein totes Gerät 15 Sekunden in einen Timeout läuft. Bei „nicht freigegeben" wird
gar nicht erst versucht; da wartet nur ein Dialog auf dem Gerät.

Die zwei Zeitangaben auf der Seite meinen Verschiedenes: **„letzter Takt"** in der Seitenleiste
sagt, wann der Dienst zuletzt seine Runde gedreht hat, **„Gerät geprüft"** auf der Verbindungskarte,
wann er dabei zuletzt nach dem Gerät gesehen hat. Ein frischer Takt neben „nicht erreichbar" ist
also kein Widerspruch.

Die vollständige Geräteadresse steht nicht auf der Seite — sie erscheint verkürzt als
`10.10.30.xxx:5555`, weil sie sonst in jedem Screenshot und im Browserverlauf landet.

Läuft der Sammel-Dienst nach einem Update noch in der alten Fassung, sagt die Seite das: der Dienst
schreibt seine Version mit in die Zustandsmeldung.

Der Knopf „Lauf jetzt starten" geht denselben Weg. Er ist gesperrt, wenn heute schon erfolgreich
eingecheckt wurde, wenn bereits ein Auftrag offen ist oder wenn der Dienst kein Lebenszeichen mehr
gibt.

### Verlauf & Auswertung

Erfolgsquote, gesammelte Münzen, Zuwachs je Erfolg und längste Serie, dazu Münzverlauf,
Zuwachs je Tag, die Verteilung der Ergebnisse und die Erfolge nach Wochentag. Zeitraum
umschaltbar: 7, 30, 90 Tage oder alles.

Zwei Dinge sind bewusst so gerechnet, wie sie da stehen:

- **Die Erfolgsquote zählt Tage, nicht Läufe.** An einem Tag mit gescheitertem Morgenlauf und
  erfolgreichem Abendlauf ist der Check-in eingesammelt — nach Läufen wären das 50 Prozent, nach
  Tagen 100, und nur Letzteres beantwortet die Frage „hat es geklappt".
- **Ein fehlender Münzstand ist kein Zuwachs von null.** Wo die Erkennung den Stand nicht lesen
  konnte, fehlt der Balken, statt eine Null zu behaupten.

Ein Klick auf eine Zeile der Verteilung filtert die Tabelle darunter. Die Kennzahlen bleiben dabei
am ganzen Zeitraum — sonst zeigte die Quote nach einem Klick auf „nicht erkannt" null Prozent, was
zwar stimmt, aber nichts mehr aussagt.

Hat ein **Abendlauf** gesammelt und war es am nächsten Morgen schon erledigt, sagt die Seite das
auch: dann liegt das Abendfenster hinter dem Tageswechsel von AliExpress. Der Nachholversuch holt
dann nicht den verpassten Tag nach, sondern schon den nächsten — der Fehltag bleibt ein Fehltag.
Ein Hinweis, kein Beweis: wer abends von Hand am Telefon sammelt, erzeugt dasselbe Muster.

Häufen sich Fehlschläge zu einer festen Uhrzeit, sagt die Seite das. Das ist selten das Gerät und
meist etwas, das regelmäßig dazwischenfunkt — etwa ein nächtliches Backup des Hypervisors.

### Diagnose

Eine dritte Seite zeigt das **Protokoll des Sammel-Dienstes** — mit Filter nach Stufe, Suche und
einem Schalter „mitlaufen", der die Ansicht alle fünf Sekunden nachlädt. Tracebacks hängen an der
Zeile, zu der sie gehören, statt einzeln aufzutauchen.

Der Dienst schreibt dafür neben der Ausgabe ins journald eine rotierende Datei nach
`data/collector.log` (fünf mal ein Megabyte). Nötig ist das, weil der Webdienst als
unprivilegierter Nutzer das Journal nicht lesen darf. Geschrieben wird sie **nur vom Dienst** — ein
Aufruf von Hand protokolliert weiter auf die Konsole, sonst gehört die Datei danach dem falschen
Nutzer.

Dazu ein **Werkzeug für die Erkennung**: Screenshot hochladen, und die Seite zeigt, was die OCR
darin findet — Button mit Position und Konfidenz, Erledigt-Zustand, Münzstand, Auflösung, Dauer,
dazu ein Rahmen um die Fundstelle. Schwellwert und Invertierung lassen sich ausprobieren; leer
bedeutet genau das Verhalten im Betrieb.

**Das hochgeladene Bild wird nie gespeichert.** Screenshots zeigen Kontostände und Bestellungen.
Es lebt nur im Speicher der einen Anfrage, die Vorschau steckt direkt in der Antwort.

OpenCV und Tesseract werden erst beim ersten Aufruf des Werkzeugs geladen, nicht beim Start: wer es
nie benutzt, zahlt den Speicher nicht.

Die abgeleitete Streak zählt aufeinanderfolgende Tage mit Erfolg aus der Datenbank. Tage, die vor dem
ersten Lauf des Dienstes von Hand gesammelt wurden, kennt sie nicht — dafür gibt es `STREAK_OFFSET`.

### Einstellungen

Alles, was sich zur Laufzeit ändern lässt, steht auf einer Seite: **Ablauf & Verhalten**
(Zeitfenster, Verhalten bei eingeschaltetem Bildschirm, Wartezeiten, Startversuche), **Gerät**
(Adresse), **Benachrichtigungen** (Discord-Webhook, wann gemeldet wird) und **Zugang** (Passwort).

Gespeichert wird nach `data/settings.json`; die `.env` bleibt die Vorgabe und wird nur
überschrieben. Der Dienst liest die Datei bei jedem Takt neu — eine Änderung ist binnen 30 Sekunden
wirksam, ohne Neustart. Werte, die von der `.env` abweichen, sind auf der Seite als „geändert"
markiert.

Geprüft wird mit **denselben Regeln wie im Dienst**: die eingegebenen Werte werden probeweise in die
laufende Konfiguration eingesetzt und deren eigene Prüfung darüber laufen gelassen. So können
Oberfläche und Dienst nicht auseinanderlaufen. Etwas Unhaltbares wird abgewiesen und gar nicht erst
geschrieben, und die Meldung nennt das Feld so, wie es auf der Seite beschriftet ist.

Drei Dinge sind bewusst so gebaut:

- **Der Discord-Webhook wird nie an den Browser gegeben.** Er ist ein Geheimnis — wer ihn hat, kann
  in den Kanal schreiben. Das Feld ist immer leer; leer lassen heißt „unverändert", entfernt wird er
  nur über ein eigenes Kästchen. Auch ins Protokoll kommt er nicht, und `data/settings.json` ist nur
  für den Dienstbenutzer lesbar (`0600`).
- **Eine neue Geräteadresse wird gespeichert, nicht verbunden.** Ob sie stimmt, zeigt „Verbindung
  prüfen" auf der Seite *Gerät* — die Oberfläche fasst das Gerät grundsätzlich nicht selbst an.
- **Nur eine Fensteränderung stellt den heutigen Zeitplan zurück.** Der Zeitstempel in
  `settings.json` sorgt dafür, dass eine Änderung nicht rückwirkend einen Lauf auslöst; ein
  geänderter Webhook darf umgekehrt keinen fälligen Lauf unterdrücken.

Woran die Erkennung hängt, bleibt außerhalb der Oberfläche und nur in der `.env`:
`BUTTON_LABELS`, `OCR_LANG`, `COIN_URL`, `APP_PACKAGE`, `ADB_PATH`, `DATA_DIR`, `LOG_LEVEL`. Ein
Vertipper dort ließe den täglichen Lauf ins Leere greifen, ohne dass es auffiele.

Eine unbrauchbare `settings.json` hält weder Dienst noch CLI an: einzelne unlesbare Werte werden
übersprungen, und ergibt die Datei insgesamt keine gültige Konfiguration, gilt wieder die `.env`.

## Discord-Meldungen

Geschickt wird ein **Embed**, keine Textzeile: Farbe nach Ausgang, Zahlen in eigenen Feldern.

| Anlass | Farbe | Inhalt |
|---|---|---|
| Münzen gesammelt | grün | Zuwachs, Münzstand, Dauer, welcher Lauf |
| Heute schon eingecheckt | blau | Münzstand, Dauer |
| Lauf fehlgeschlagen | orange | Grund in Worten, Meldung, Screenshot im Embed |
| Letzter Versuch des Tages | rot | wie oben, zusätzlich als letzter Versuch markiert |
| Gerät länger nicht erreichbar | rot | seit wann, Adresse (verkürzt) |
| Gerät wieder erreichbar | grün | wie lange der Ausfall dauerte |
| Wochenrückblick (montags 9 Uhr) | nach Quote | Erfolgsquote, gesammelte Münzen, Münzstand |
| Erfolg ohne Zuwachs | rot | Tage ohne Zuwachs, stehender Münzstand |

Der Grund eines Fehlschlags steht ausgeschrieben da, nicht als `not_found` — auf dem Handy gelesen
sagt ein Codewort nichts.

**Die Meldung bei längerem Ausfall** kommt je Ausfall genau einmal, nach `OFFLINE_ALERT_MIN`
Minuten ohne Verbindung, und danach eine Entwarnung, sobald das Gerät wieder da ist. Kurze
Aussetzer bleiben still — ein Handy im Ruhezustand oder ein Router, der neu startet, ist kein
Vorfall. Ohne diese Meldung merkt man einen echten Ausfall erst daran, dass tagelang nichts
eingesammelt wurde.

Die Geräteadresse steht in der Meldung nur verkürzt (`10.10.30.xxx:5555`). Ein Chat-Kanal ist
kein Ort für interne Adressen.

Fehlschläge eines Laufs werden **immer** gemeldet und lassen sich nicht abschalten: eine
Automatik, die stillschweigend aufhört zu funktionieren, ist schlimmer als keine.

### Stillstandswächter

Am 21.09.2026 stellte sich heraus, dass der Dienst tagelang „heute schon eingecheckt" gemeldet
hatte, während gar nichts eingesammelt wurde. Die Erfolgsquote stand auf 100 %, die Serie lief
weiter — und niemandem fiel es auf. Die Ursache ist behoben, aber die *Art* von Fehler bleibt
möglich: Layout geändert, Konto gesperrt, Tap geht ins Leere.

Der Wächter fragt darum nicht „hat der Schritt geklappt", sondern **„ist das Ergebnis
eingetreten"**: steht der Münzstand an drei aufeinanderfolgenden Erfolgstagen unverändert, kommt
eine rote Meldung und ein Banner auf der Übersicht.

Was bewusst *nicht* zählt:

- **Tage ohne Erfolg.** Dort ist ein gleichbleibender Stand die erwartete Folge, kein Widerspruch.
- **Ein unlesbarer Münzstand.** Unbekannt ist nicht dasselbe wie unverändert.
- **Ausgegebene Münzen.** Wer Münzen ausgibt, sammelt danach weiter — der nächste Anstieg beendet
  die Serie.

Gemeldet wird je Vorfall einmal. Gemerkt wird dafür der Münzstand und kein Datum: solange er sich
nicht bewegt, ist es derselbe Vorfall. Steigt er wieder und bleibt später erneut stehen, wird
wieder gewarnt.

### Wochenrückblick

Montags um 9 Uhr eine Meldung mit Erfolgsquote, gesammelten Münzen und Münzstand der letzten
sieben Tage. Die Farbe folgt der Quote — grün ab 90 %, sonst orange oder rot.

Gerechnet wird mit **denselben Regeln wie in der Weboberfläche** (`aliexpress_coin_collector/stats.py`):
die Quote zählt Tage statt Läufe, und ein fehlender Münzstand ist kein Zuwachs von null. Die Regeln
stehen an einer Stelle, damit Anzeige und Meldung nicht auseinanderlaufen.

Beim allerersten Montag nach der Installation kommt nichts — sonst wäre der erste Rückblick einer
auf eine leere Woche. Der Merker dafür liegt in `data/last-digest`.

### Abgelaufene Anmeldung

Die Sitzung in der App läuft irgendwann ab. Bis Version 0.10.0 endete das als `not_found` — also
täglich dieselbe nichtssagende Meldung „nichts erkannt", und man musste den Screenshot ansehen, um
zu verstehen, warum seit Tagen nichts gesammelt wurde.

Jetzt sucht die Erkennung in diesem Fall nach einer Anmeldeaufforderung und meldet
**„Anmeldung nötig — bitte in der AliExpress-App neu einloggen"**, in Rot, weil das auf einen
Menschen wartet und sich nicht von allein erledigt.

**Die Prüfung läuft nur, wenn ohnehin weder der Sammeln-Knopf noch der Erledigt-Zustand gefunden
wurde.** Ein Treffer kann damit einen gescheiterten Lauf genauer benennen, aber niemals einen
erfolgreichen stören — im schlimmsten Fall klebt ein falsches Etikett auf einem Lauf, der so oder
so nichts eingesammelt hätte. Die Marker stehen in `LOGIN_MARKERS`.

Ob sie an deiner App greifen, prüfst du ohne Wartezeit: einen Screenshot des abgemeldeten
Zustands unter **Diagnose** hochladen — dort steht jetzt eine Zeile „Anmeldung nötig".

### Testmeldung

Ob der Webhook wirklich funktioniert, siehst du sonst erst beim nächsten Lauf. Darum ein Test —
in der Oberfläche unter **Einstellungen → Benachrichtigungen** („Testmeldung schicken"), oder
im Terminal:

```bash
acc notify-test
```

Die Testmeldung führt gleich mit auf, wovon du künftig hörst und wovon nicht. Schlägt sie fehl,
steht der Grund im Klartext da statt einer HTTP-Nummer — „Diesen Webhook gibt es nicht (mehr)"
ist die Antwort auf ein in Discord gelöschtes Ziel.

Die Oberfläche schickt diese eine Meldung ausnahmsweise selbst statt über die Auftragsablage:
es ist nur eine HTTPS-Anfrage, kein Zugriff aufs Gerät — und wer testet, will die Antwort sofort
sehen und nicht dreißig Sekunden auf den nächsten Takt warten.

## Konfiguration

Alle Werte stehen in der `.env` (Vorlage `.env.example`). Umgebungsvariablen haben Vorrang. Die
fett markierten lassen sich zusätzlich in der Weboberfläche ändern; was dort gespeichert wird, steht
in `data/settings.json` und überschreibt die `.env`.

| Variable | Standard | Bedeutung |
|---|---|---|
| **`ADB_SERIAL`** | – (Pflicht) | Adresse des Geräts, z. B. `192.168.1.50:5555` |
| **`DISCORD_WEBHOOK_URL`** | leer | Webhook für Meldungen (leer = nur Log) |
| **`MORNING_START` / `MORNING_END`** | `07:00` / `10:00` | Fenster für den Morgenlauf |
| **`EVENING_START` / `EVENING_END`** | `19:00` / `21:00` | Fenster für den Nachholversuch |
| **`SKIP_IF_AWAKE`** | `true` | Nicht starten, solange der Bildschirm an ist. Bei einem reinen Automatisierungsgerät `false` |
| **`BUSY_RETRY_MIN`** | `15` | Abstand der Wiederholung, wenn das Gerät in Benutzung ist |
| **`BUSY_MAX_WAIT_MIN`** | `120` | Danach wird trotz eingeschaltetem Bildschirm gestartet |
| **`PAGE_TIMEOUT_S`** | `90` | Wartezeit pro Startversuch auf die Coin-Seite |
| **`CONFIRM_TIMEOUT_S`** | `25` | Wartezeit auf die Bestätigung nach dem Tap |
| **`LAUNCH_RETRIES`** | `1` | Zusätzliche Startversuche mit App-Neustart |
| `BUTTON_LABELS` | `Sammeln,Collect,Claim` | Mögliche Beschriftungen des Buttons |
| `LOGIN_MARKERS` | `anmelden,einloggen,…` | Woran eine abgelaufene Anmeldung erkannt wird |
| `OCR_LANG` | `deu` | Tesseract-Sprache |
| **`NOTIFY_ON_SUCCESS` / `NOTIFY_ON_ALREADY_DONE`** | `true` / `false` | Wann Erfolgsmeldungen kommen (Fehler werden immer gemeldet) |
| **`NOTIFY_ON_OFFLINE`** | `true` | Melden, wenn das Gerät länger nicht erreichbar ist |
| **`NOTIFY_WEEKLY`** | `true` | Wochenrückblick montags um 9 Uhr |
| **`NOTIFY_ON_STALL`** | `true` | Melden, wenn Erfolg gemeldet wird, aber der Münzstand steht |
| **`OFFLINE_ALERT_MIN`** | `30` | Wartezeit davor, in Minuten |
| `DATA_DIR` | `./data` | Datenbank und Fehler-Screenshots (die letzten 30) |
| `LOG_LEVEL` | `INFO` | `DEBUG` zeigt die Erkennung pro Screenshot |
| `WEB_PORT` | `80` | Port der Weboberfläche (das Passwort wird auf der Seite vergeben, nicht hier) |
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
| Installation, `--update` und Rollback | gegen einen lokalen Stellvertreter für GitHub geprüft, gegen das echte GitHub ausstehend |
| Weboberfläche | gegen einen laufenden Server geprüft; systemd-Unit und Port 80 ausstehend |

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
