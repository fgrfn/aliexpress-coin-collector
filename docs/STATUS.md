# Stand, Technik und offene Punkte

Ergänzt das [README](../README.md) um Details, die für die Weiterentwicklung wichtig sind: was tatsächlich auf
Hardware überprüft ist, wie die Erkennung funktioniert, welche Fallstricke es gibt und was noch offen ist.

## 1. Was verifiziert ist und was nicht

Die Unterscheidung ist wichtig: einiges ist auf echten Geräten belegt, anderes nur mit Attrappen im Test.

### Auf echter Hardware belegt

- ADB über WLAN vom Server zum Gerät (`doctor` meldet `device`, Screenshot 720×1280).
- Der Deep-Link öffnet die Coin-Seite aus dem Kaltstart heraus (nach `am force-stop`).
- Erkennung des **Erledigt-Zustands** und des **Münzstands** an echten Screenshots in 720×1280 und 1200×1920.
- Ein vollständiger Lauf im Container endete mit `already_done` (Dauer rund 31 s auf einem langsamen Gerät).
- Discord-Meldungen kommen an.

### Nur mit Attrappen oder Näherungen getestet

- **Erkennung von "Sammeln"** (weiße Schrift auf oranger Fläche): gefunden an einem Video-Frame in 1200×1920 mit
  Schwellwert-Vorverarbeitung (Konfidenz 96) und an auf 720 px Breite verkleinerten Kopien. Ein echter Screenshot
  eines Telefons im Offen-Zustand liegt bisher nicht vor.
- **Tap und Bestätigung** (`_confirm_claim`): nur mit Fake-ADB und echten Bildern als Vorher/Nachher-Paar.
  **Auf dem Gerät noch nicht bestätigt.**
- Zeitplan über mehrere Tage, `busy`-Logik im Dauerbetrieb, Abendlauf und Nachholversuch: nur Unit-Tests.
- Verhalten bei abgelaufenem Login oder Popups: unbekannt, endet vermutlich als `not_found`.

## 2. Ablauf eines Laufs (`runner.run_once`)

1. `adb.ensure_connected()`, sonst `unreachable` mit Hinweis auf `adb tcpip 5555`.
2. `adb.is_awake()`: Bildschirm an, `SKIP_IF_AWAKE=true` und kein `--force` → `busy`.
3. Bildschirm wecken (`KEYCODE_WAKEUP`, `wm dismiss-keyguard`), 2 s warten. Nur dann merkt sich der Lauf `woke=True`.
4. Bis zu `1 + LAUNCH_RETRIES` Mal: `am force-stop`, 1 s, Deep-Link per `am start -a VIEW`, 4 s, danach alle 3 s
   Screenshot und `ocr.analyze`, bis Button oder Erledigt-Zustand sichtbar ist oder `PAGE_TIMEOUT_S` abläuft.
5. Kein Zustand erkannt → `not_found` mit dem letzten Screenshot. Erledigt-Zustand → `already_done`, kein Tap.
6. Button gefunden → Tap auf die Wortmitte mit Zufallsversatz (±8 px waagerecht, ±5 px senkrecht), 3 s warten, dann
   alle 2 s ein Screenshot bis `CONFIRM_TIMEOUT_S`. **Erfolg** (`claimed`), wenn der Button verschwunden ist **und**
   entweder der Erledigt-Marker sichtbar ist **oder** der Münzstand über dem vorherigen liegt.
7. `finally`: Hat der Lauf den Bildschirm selbst geweckt, folgen `KEYCODE_HOME` und `KEYCODE_SLEEP`.
8. Jede Ausnahme wird zu `error`. Ein einzelner Lauf darf den Dienst nie beenden.

## 3. Erkennung (`ocr.py`)

Tesseract über `pytesseract`, `--psm 11` (Streutext), Mindest-Konfidenz 30.

**Erledigt-Marker.** Regex `\bmorgen` (Groß-/Kleinschreibung egal) über alle Wörter oberhalb von 50 % der Bildhöhe.
Das Wort kommt in allen beobachteten Erledigt-Varianten vor ("Wenn Sie morgen vorbeischauen: …", "Morgen warten
weitere Münzen", Kachel "Morgen"). Im Offen-Zustand steht dort "Heute für … Münzen vorbeischauen", also kein "morgen".

**Button.** Wörter zwischen 30 % und 60 % der Bildhöhe, unscharfer Vergleich über `difflib` (Ähnlichkeit ≥ 0,8,
Mindestlänge 4 Zeichen) gegen `BUTTON_LABELS`. Findet Tesseract im Originalbild nichts — der Normalfall bei weißer
Schrift auf orangem Grund — folgen Durchgänge mit invertierter Binarisierung der Graustufen bei den Schwellen
235, 220 und 245: übrig bleiben nur die fast weißen Pixel als Schrift. Der Button wird nur gesucht, wenn der
Erledigt-Marker fehlt.

**Münzstand.** Zahl im Band 6–16 % der Höhe und 20–60 % der Breite; genommen wird das am weitesten links stehende
Wort, das auf `^(\d{1,3}([.,]\d{3})+|\d+)[≈~=]*$` passt. Vorangestelltes Rauschen des Münz-Symbols (`O0`, `()10`)
schneidet `_ICON_NOISE` ab.

Alle Bildbereiche sind **relative** Werte, kalibriert an 1200×1920 und 720×1280. Deutlich andere Seitenverhältnisse
können Anpassungen erfordern.

## 4. Zeitplan und Entscheidung (`scheduler.py`)

- `plan_for(day, cfg)` liefert zufällige, aber pro Datum feste Uhrzeiten in Morgen- und Abendfenster. Der Seed hängt
  nur vom Datum ab, die Zeiten überleben also Neustarts des Dienstes.
- `decide(now, plan, attempts, cfg)` ist reine Logik und gut getestet: kein Lauf mehr nach einem Erfolg
  (`claimed`/`already_done`); `busy` zählt nicht als Versuch; höchstens ein echter Versuch je Art; der Abendlauf
  startet auch dann, wenn morgens nichts lief; nach `BUSY_RETRY_MIN` wird erneut versucht, nach `BUSY_MAX_WAIT_MIN`
  wird trotz eingeschaltetem Bildschirm gestartet.
- `handle_result` schreibt in SQLite, meldet per Discord (Fehler immer, Erfolge je nach `NOTIFY_ON_*`, `busy` nie),
  legt Fehler-Screenshots in `data/shots/` ab (die letzten 30) und ergänzt beim Abendlauf den Hinweis, dass es der
  letzte Versuch des Tages war.
- `daemon` prüft alle 30 s und ruft bei einer Entscheidung `run_once` auf.

> Der Seed in `scheduler._SEED` ist bewusst der historische Wert `aliexpress-coins`. Ändert man ihn, verschieben sich
> alle geplanten Uhrzeiten.

## 5. Datenbank (`store.py`)

Tabelle `runs(id, ts, day, kind, outcome, coins_before, coins_after, message, duration_s)`.

`ts` ist die **naive Ortszeit des Servers** zum Zeitpunkt des Laufs — die Zeitzone des Servers muss deshalb stimmen,
sonst passen Zeitfenster und gespeicherte Zeiten nicht zusammen. `kind` ist `morning`, `evening` oder `manual`, die
möglichen Werte von `outcome` stehen in `runner.Outcome`.

## 6. Stolpersteine

1. **PNGs nie per Shell-Umleitung holen.** `adb exec-out screencap -p > screen.png` schreibt in PowerShell UTF-16,
   die Datei beginnt dann mit `FF FE` und ist unbrauchbar. Stattdessen `screencap` auf dem Gerät plus `adb pull`.
   Im Code wird `exec-out` direkt ausgelesen, dort tritt das Problem nicht auf.
2. **`adb tcpip 5555` gilt nur bis zum Neustart des Geräts.** Danach meldet der Dienst `unreachable`, und der Schritt
   muss per USB wiederholt werden.
3. **Mehrere ADB-Geräte:** Jeder Befehl braucht `-s <serial>`. Der Code übergibt das immer.
4. **Erste Verbindung als Dienstbenutzer herstellen** (`runuser -u coins -- adb connect …`) und den Dialog auf dem
   Gerät mit "Immer zulassen" bestätigen. Der Schlüssel landet in `~coins/.android/`, und das Home-Verzeichnis des
   Dienstbenutzers ist das Installationsverzeichnis. Die Migration in `install.sh` übernimmt diesen Ordner.
5. **Tablet-Layout:** Auf Tablets fehlt im Tablet-Layout teils der Einstieg zur Coin-Seite. Der Deep-Link öffnet sie
   trotzdem; ein Telefon-Layout ist aber der verlässlichere Fall.
6. **Weiß auf Orange:** Der Button-Text wird ohne Vorverarbeitung nicht gefunden. Das ist der zentrale Kniff in
   `find_button`.
7. **Kleine Münzstände:** Eine führende "0" verschmilzt mit dem Münz-Symbol zu `O0`. Abgefangen in
   `_clean_coin_token`.
8. **Zeitzone des Servers** muss die Ortszeit sein. Läuft der Container in UTC, stehen die Zeitfenster und die
   Zeitstempel in der Datenbank verschoben.
9. **Die Streak-Anzeige zieht verzögert nach:** direkt nach dem Einsammeln stand dort noch der alte Wert, erst beim
   nächsten Öffnen der richtige. Sie taugt deshalb nicht als Erfolgskriterium — daher die Kombination aus
   verschwundenem Button plus Marker oder gestiegenem Münzstand.
10. **Nach `am force-stop` lädt die Seite auf langsamen Geräten rund 30 s.** Der Feed am unteren Rand zeigt oft noch
    "Loading…", während der obere Bereich schon steht. Die Erkennung braucht nur den oberen Teil.
11. **Wann AliExpress den Tag umschaltet, ist nicht gesichert** (Ortszeit oder eine andere Zeitzone). Das Skript
    prüft deshalb immer den Seitenzustand, statt einen Kalendertag anzunehmen.

## 7. Entscheidungen und ihre Begründung

| Entscheidung | Begründung |
|---|---|
| Steuerung per ADB über WLAN (`adb tcpip 5555`) | Robust und einfach. Wireless Debugging mit Pairing-Code ist für Dauerbetrieb unpraktisch, weil der Port wechselt. |
| Eigenes Android-Gerät statt VM | Emulatoren (Android-x86, Redroid) bringen ARM-Übersetzung und Emulator-Erkennung mit sich. |
| Kein `uiautomator2` | Die Coin-Seite ist ein WebView (`com.uc.webview.export.WebView`), der UI-Dump enthält weder Texte noch IDs. Es bleibt nur Bilderkennung. |
| Deep-Link statt Navigation über Icons | Die Adresse stammt aus `dumpsys activity` und funktioniert von außen per `am start`, auch aus dem Kaltstart. Ein schwebendes Widget am Bildschirmrand wäre fragil. |
| Keine Änderung der Bildschirmdichte | Mit Deep-Link nicht nötig, spart Logik zum Zurücksetzen. |
| OCR statt Template-Matching oder Vision-Modell | Kostenlos, lokal, über `BUTTON_LABELS` sprachunabhängig konfigurierbar. Ein Vision-Modell bleibt Ausweichoption. |
| Erfolg = Button weg **und** (Marker **oder** Münzstand ↑) | Die Streak-Zahl zieht verzögert nach und taugt nicht als Beleg (siehe Stolperstein 9). |
| Manueller Login, sonst Abbruch mit Meldung | Automatischer Login scheitert an Captcha und SMS-Verifizierung und wäre der fragilste Teil. Bewusst ausgeschlossen. |
| systemd statt Docker | Ein LXC mit systemd ist die Zielumgebung. Ein Container-Setup bräuchte ein persistentes Volume für `~/.android`. |
| SQLite ab Tag 1, keine WebUI | Ein Datensatz pro Tag reicht; die Historie erlaubt später Auswertungen und eine kleine Weboberfläche. |

## 8. Offene Punkte

Nichts davon ist beschlossen, die Reihenfolge ist ein Vorschlag.

1. **Tap auf dem Gerät bestätigen.** Der wichtigste offene Punkt: Erst mehrere Läufe mit `claimed` belegen, dass Tap
   und Bestätigung funktionieren.
2. **Fixtures für Erkennungstests.** Echte Screenshots (offen und erledigt, verschiedene Auflösungen) als Testbilder,
   dazu Regressionstests für `ocr.analyze`. Screenshots zeigen Kontostände und müssen vorher zugeschnitten werden;
   die CI bräuchte dafür `tesseract-ocr` und das passende Sprachpaket.
3. **Umschaltzeit auswerten.** Aus der Tabelle `runs` ableiten, wann ein neuer Tag beginnt, und die Zeitfenster
   anpassen — das Abendfenster sollte nicht hinter dem Umschaltpunkt liegen, sonst sammelt es den nächsten Tag ein.
4. **Login-Abgelaufen-Erkennung.** Ein eigener Marker im Screenshot, damit die Meldung "bitte neu einloggen" lautet
   statt `not_found`.
5. **Popups wegtippen** (Bewertungsaufforderung, Update-Hinweis) statt daran zu scheitern.
6. **Zusatzaufgaben** der Coin-Seite als eigene, austauschbare Module. Erst nach stabilem Check-in sinnvoll, und mit
   hohem Pflegeaufwand verbunden, weil die Aufgaben wechseln.
7. **Kleine Weboberfläche** über der SQLite-Historie, falls Diagramme gewünscht sind.
8. **Vision-Modell als Ausweichweg** für die Button-Erkennung, falls die OCR zu oft danebenliegt.
9. **Neustart des Geräts abfangen.** `adb tcpip 5555` lässt sich ohne USB nicht wiederholen; die `unreachable`-Meldung
   weist bereits darauf hin. Bei Android 11+ wäre Wireless Debugging eine Alternative.

## 9. Regeln für Änderungen

- **Erkennung:** Änderungen an Zonen, Schwellen oder Labels immer an mehreren echten Screenshots prüfen (verschiedene
  Auflösungen, offen und erledigt) und die relativen Bereiche nicht auf ein einzelnes Bild trimmen.
- **Neue Logik bekommt einen Test.** Die Attrappen stehen bereit: `FakeAdb` in `tests/test_runner.py` und eine
  injizierbare `analyze`-Funktion.
- **Vor jedem Commit** `python -m pytest -q`, `ruff check .` und `ruff format --check .` laufen lassen. Bei Änderungen
  an `install.sh` zusätzlich `bash -n install.sh` und `shellcheck install.sh`.
- **Keine Geheimnisse im Repo:** keine Webhook-URLs, Zugangsdaten, Geräteadressen oder ungeprüften Screenshots.
  Die `.env` steht in `.gitignore` und gehört dort auch hin.
