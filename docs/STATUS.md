# Stand, Technik und offene Punkte

Ergänzt das [README](../README.md) um Details, die für die Weiterentwicklung wichtig sind: was tatsächlich auf
Hardware überprüft ist, wie die Erkennung funktioniert, welche Fallstricke es gibt und was noch offen ist.

## 1. Was verifiziert ist und was nicht

Die Unterscheidung ist wichtig: einiges ist auf echten Geräten belegt, anderes nur mit Attrappen im Test.

### Auf echter Hardware belegt

- **Ein vollstaendiger Check-in mit `claimed`** (21.09.2026, 10:17, Muenzstand 10 → 25). Damit sind Tap
  **und** Bestaetigung auf dem Geraet belegt — der aelteste offene Punkt des Projekts ist erledigt.

- ADB über WLAN vom Server zum Gerät (`doctor` meldet `device`, Screenshot 720×1280).
- Der Deep-Link öffnet die Coin-Seite aus dem Kaltstart heraus (nach `am force-stop`).
- Erkennung des **Erledigt-Zustands** und des **Münzstands** an echten Screenshots in 720×1280 und 1200×1920.
- Ein vollständiger Lauf im Container endete mit `already_done` (Dauer rund 31 s auf einem langsamen Gerät).
- Discord-Meldungen kommen an.
- **Home Assistant nimmt die MQTT-Discovery an** (25.09.2026, Broker und Home Assistant beim Nutzer).
  Alle fuenf Entitaeten standen nach dem Start des Dienstes mit Werten da: Akku 100 %, Akkutemperatur
  27,9 °C, Geraet erreichbar "Verbunden", Letzter Lauf `claimed`, Muenzstand 140. Damit sind Aufbau der
  Discovery-Nachrichten, `unique_id`, Geraeteeintrag und die `value_template`-Ausdruecke im echten Betrieb
  belegt, nicht nur gegen Mosquitto.
- **Akkuwerte vom Produktivgeraet** (Samsung SM-J330FN): `dumpsys battery` liefert Ladestand und
  Temperatur so, wie der Parser sie erwartet — die beiden Werte oben kommen von dort.

### Nur mit Attrappen oder Näherungen getestet

- **Beginn des Muenztags (0.19.0): mit Attrappen getestet, die Uhrzeit selbst ist geschaetzt.** Dass ein
  Nachtlauf den Tag nicht mehr blockiert, ist durch Tests gedeckt (`tests/test_coin_day.py`, der gemeldete
  Fall als Testfall). Dass der Umschaltpunkt bei 08:00 liegt, ist **nicht gemessen** -- es ist die Vorgabe
  zur Beobachtung "ab etwa 8 oder 9 Uhr".


- **Zusatzaufgaben (0.17.1): halb belegt.** Am Geraet gesehen ist der Weg bis zur Liste: der Knopf
  "Mehr Muenzen verdienen" wird gefunden und getippt, das Fenster geht auf, die Kartentexte werden
  gelesen (26.09.2026, 720x1280). **Nicht gesehen** ist alles danach -- ob der Tap den Knopf "Und los"
  trifft, ob das Zurueck in die Liste fuehrt und ob die Muenzen ankommen.

  Dabei aufgefallen und in 0.17.1 behoben: die Knoepfe stehen **weiss auf orange** und wurden im
  Originalbild gar nicht gelesen. Im erkannten Text der ganzen Liste stand kein einziges "Und los",
  waehrend alle Kartentitel sauber durchkamen -- und weil die Karten an den Knoepfen geschnitten
  werden, meldete der Befehl "0 Aufgaben gelesen". Seither laeuft fuer die Knoepfe ein zweiter,
  umgekehrter Durchgang, wie ihn `find_button` fuer den Sammeln-Knopf schon hatte. Ebenfalls dabei
  gesehen: in der Liste liegt das Fenster ueber der Kopfzeile, der Muenzstand ist dort nicht mehr
  lesbar -- er wird jetzt auf der Coin-Seite davor und danach genommen.

- **Zusatzaufgaben, die Auswahl (0.17.0):** Die Auswahl ist an den
  echten Aufgabentexten vom 26.09.2026 geprueft (Screenshots des Nutzers, in
  `tests/test_extras.py` als Testfaelle hinterlegt) -- alle zehn Karten werden richtig
  einsortiert. **Ungetestet ist alles davor und danach:** ob die OCR die Karten auf dem
  Telefon wirklich so liest, ob der Tap den Knopf "Und los" trifft, ob das Zurueck in die
  Liste fuehrt und ob die Muenzen am Ende ankommen. Die Bilder lagen nur als Anschauung vor,
  nicht als Testbilder -- sie enthalten Kontodaten und sind nicht im Repo.


- **Erkennung von "Sammeln"** (weiße Schrift auf oranger Fläche): gefunden an einem Video-Frame in 1200×1920 mit
  Schwellwert-Vorverarbeitung (Konfidenz 96) und an auf 720 px Breite verkleinerten Kopien. Ein echter Screenshot
  eines Telefons im Offen-Zustand liegt bisher nicht vor.
- **Tap und Bestätigung** (`_confirm_claim`): nur mit Fake-ADB und echten Bildern als Vorher/Nachher-Paar.
  **Auf dem Gerät noch nicht bestätigt.**
- Zeitplan über mehrere Tage, `busy`-Logik im Dauerbetrieb, Abendlauf und Nachholversuch: nur Unit-Tests.
- `install.sh` in allen Betriebsarten gegen einen lokalen Stellvertreter für GitHub geprüft: Installation per
  Pipe ohne Quellbaum, Installation aus einem lokalen Checkout, `--update`, Rollback bei fehlgeschlagenem
  Rauchtest, unveränderte `.env`/`data/`/`.android/` sowie abgebrochene Downloads an sechs Stellen.
  **Gegen das echte GitHub und mit laufendem systemd ungetestet**, ebenso der apt- und der venv-Teil.
- Weboberfläche: Anmeldung, Umleitung ohne Sitzung, Pfad-Ausbruch beim Screenshot-Abruf, Speichern und
  Ablehnen von Zeitfenstern, Knopf-Leitplanken und das Anlegen der Auftragsdatei wurden gegen einen echt
  laufenden Server geprüft. Die Unit selbst und `CAP_NET_BIND_SERVICE` auf Port 80 sind **ungetestet**,
  weil hier kein systemd läuft.
- Passwortvergabe über die Seite: gegen einen echt laufenden Server geprüft — Ersteinrichtung, zu kurzes
  Passwort, abweichende Wiederholung, gesperrte Zweiteinrichtung, falsche Anmeldung, Passwortwechsel samt
  Entwertung des alten Cookies, und die Übernahme eines alten `WEB_PASSWORD` aus der `.env`. Die Seiten
  wurden zusätzlich in Chromium gerendert und angesehen (hell und dunkel).
- Umbau der Oberfläche auf Vorlagen (Etappe 1): gegen einen echt laufenden Server mit gefüllter
  Datenbank geprüft und in Chromium angesehen — hell, dunkel und in Handybreite (390 px), Menü geöffnet,
  ohne Fehler oder Warnungen in der Browserkonsole. Geprüft wurden außerdem: kein waagerechter Überlauf
  auf dem Handy, alle Eingabefelder mit echter Beschriftung, alle Tippziele mindestens 44 px, die
  mitgelieferte Schrift wirklich geladen, und dass keine Adresse nach außen zeigt.
- `systemctl enable --now` für die Weboberfläche in `install.sh`: mit einer `systemctl`-Attrappe geprüft
  (Aufrufe, Erfolgs- und Fehlstartzweig). **Gegen echtes systemd ungetestet.**
- Testmeldung (0.9.1): **nur mit Attrappen getestet.** Sie ist gerade der Weg, die offene
  Frage von 0.9.0 selbst zu beantworten — ein Klick in der Oberfläche zeigt, wie die Embeds im
  echten Kanal aussehen.
- Discord-Embeds und Ausfallmeldung (0.9.0): **nur mit Attrappen getestet.** Der Aufbau der
  Nachricht ist gegen die Grenzen der Discord-API geprüft (Kürzen statt Ablehnen) und die
  Geräteadresse wird nachweislich verkürzt, aber **es wurde keine Meldung an einen echten
  Kanal geschickt** — wie die Embeds dort aussehen, ist nicht belegt.
- Selbstheilung der Verbindung (0.8.1): **nur mit Attrappen getestet.** Der Anlass ist echt --
  auf dem Produktivsystem stand "nicht erreichbar", während das Gerät erreichbar war, und erst
  ein Auftrag mit `ensure_connected` brachte es zurück. Dass die Erholung im Betrieb greift,
  ist damit aber noch nicht belegt; das zeigt sich erst beim nächsten echten Verbindungsabriss.
- Erkennung einer abgelaufenen Anmeldung (0.10.0): **nur mit Attrappen getestet, und die Marker
  selbst sind Vorgaben ohne Beleg.** Ein echter Screenshot des abgemeldeten Zustands liegt nicht
  vor. Belegt ist dagegen die Sicherheitszusage: die Prüfung läuft nur, wenn weder Knopf noch
  Erledigt-Zustand gefunden wurden, kann einen erfolgreichen Lauf also nicht stören.
- MQTT-Anschluss folgt den Einstellungen (0.16.2): **Fehler aus dem Betrieb**, gemeldet mit
  "kommt nix bei HA an". Der Dienst entschied beim Start ein fuer alle Mal, ob MQTT laeuft --
  wer die Broker-Daten spaeter in der Oberflaeche eintrug, wartete vergeblich, ohne jede
  Fehlermeldung. Behoben und mit Attrappen getestet; am laufenden Dienst nicht nachgestellt.
- Frist fuer veraltete Messwerte (0.16.1): **mit Attrappen getestet**, nicht gegen Home
  Assistant. Dass `expire_after` dort wie erwartet greift, ist Konvention und hier ungeprueft.
  Der Fallstrick dahinter ist dagegen belegt: ohne die Nachricht nach jeder Messung haette die
  Frist den Sensor abgewuergt, sobald der Wert einmal stillstand.
- Auftraege binnen einer Sekunde statt bis zu dreissig (0.16.1): **mit einer gestellten Uhr
  getestet**, nicht am laufenden Dienst. Die Warteschlange bleibt, nur die Pause zwischen zwei
  Takten wird in Sekundenscheiben geschlafen und dabei nach Auftraegen gesehen. Der schwere
  Teil des Takts (ADB-Zustand, Bildschirm, Zeitplan) bleibt bei 30 s.
- REST-Weg nach Home Assistant entfernt (0.16.0): eine Entfernung, kein neues Verhalten. Alte
  `HA_URL`/`HA_TOKEN` in `.env` oder `settings.json` werden schlicht nicht mehr gelesen, ein
  Update bricht daran also nicht.
- Akkuwert live in der Oberflaeche (0.16.0): **mit Attrappen getestet.** Der Weg ueber die
  Zustandsdatei ist durch Tests gedeckt, am laufenden Dienst wurde er nicht gesehen.
- Update ueber den Installer (0.15.1): **beide Pfade von Hand durchgespielt** (frische
  Installation und Update auf dasselbe Ziel), allerdings ohne systemd -- der Container hat
  keines. Dass der Sammel-Dienst beim Update wirklich neu startet, ist damit **nicht am
  laufenden System belegt**, nur die Bedingung davor (`systemctl is-active`) und der Rest der
  Ausgabe. `bash -n` und `shellcheck` sind sauber.
- MQTT-Discovery-Anbindung (0.15.0): **gegen einen echten Broker getestet, aber nicht gegen
  Home Assistant.** Drei Tests laufen gegen ein laufendes Mosquitto (`tests/test_mqtt.py`,
  werden ohne installiertes `mosquitto` uebersprungen, also auch in der CI): der Broker nimmt
  Discovery und Zustand an, ein frisch verbundener Mithoerer bekommt beides sofort retained,
  und das Testament faellt wirklich, wenn der Dienst per SIGKILL stirbt (in der Messung binnen
  einer Sekunde, weil das Betriebssystem die Verbindung schliesst; bei Strom- oder Netzausfall
  dauert es das Anderthalbfache von `KEEPALIVE_S`, also gut 45 s). Dass Home Assistant die
  Discovery-Nachrichten annimmt, ist seit dem 25.09.2026 **im Betrieb belegt** (siehe oben).
  Offen bleibt nur der Randfall: ob `default('unknown')` in einem `value_template` wirklich als
  unbekannter Zustand ankommt — bisher waren beim Nutzer alle Werte gesetzt. Ebenso ungesehen ist,
  ob `expire_after` die Sensoren nach drei ausgefallenen Messungen tatsaechlich stillegt.
- Akkuauslesung und Akkuwaechter (0.14.0): **Auslesen im Betrieb gesehen, Waechter nur mit
  Attrappen.** Ladestand und Temperatur des Produktivgeräts stehen seit dem 25.09.2026 in Home
  Assistant, der Parser trifft die echte `dumpsys battery`-Ausgabe also. **Nicht gesehen** ist der
  Wächter selbst: dass eine Meldung bei niedrigem Ladestand, Überhitzung, schlechter
  Akkugesundheit oder verlorener Stromversorgung wirklich herausgeht, ist nur durch Tests gedeckt
  — dafür müssten die Grenzwerte einmal absichtlich gerissen werden. Kachel, Diagramm und die
  beiden Abschnitte der Einstellungsseite wurden in Chromium angesehen. Offen: ob das Gerät
  `health` sinnvoll meldet, und ob `charge_full`/`charge_full_design` lesbar wären (daraus liesse
  sich die Akkugesundheit in Prozent ableiten).
- Abschaltbarer Abendlauf (0.13.0): **nur mit Attrappen getestet.** Entscheidung, Prüfung der
  Konfiguration, Formular und Anzeige sind durch Tests gedeckt; ob der Dienst mit abgeschaltetem
  Abendlauf über mehrere Tage so läuft, ist noch nicht im Betrieb gesehen.
- Stillstandswächter (0.12.0): **mit Attrappen getestet**, gegen das echte Fehlermuster aus der
  Datenbank nachgestellt (sechs Tage „erledigt" bei Stand 10) und das Banner in Chromium angesehen.
  Die Discord-Meldung ging in keinen echten Kanal.
- Vorrang des Knopfes vor dem Erledigt-Marker (0.11.0): **mit Attrappen getestet, der Anlass ist
  echt.** Die Laufdaten belegen den Fehler zweifelsfrei (siehe 5c); dass die Korrektur im Betrieb
  greift, zeigt sich erst an den nächsten Tagen.
- Wochenrückblick und Umschaltpunkt-Hinweis (0.10.0): **nur mit Attrappen getestet.** Der Rückblick
  wurde nicht in einen echten Kanal geschickt; der Hinweis wurde gegen einen laufenden Webdienst mit
  erzeugtem Abendlauf-Muster geprüft und angesehen.
- Etappe 4b (Einstellungen): gegen einen echt laufenden Webdienst geprüft — Speichern aller
  Abschnitte, Abweisen unhaltbarer Werte (Fenster verkehrt herum, Wiederholung nach dem Erzwingen,
  Text statt Zahl, Adresse ohne gültigen Port, Webhook ohne `https://`), und dass ein abgewiesener
  Wert den vorherigen stehen lässt. Nachgewiesen: der Discord-Webhook steht nach dem Speichern
  weder in der Seite noch im Protokoll, `settings.json` ist `0600`, und das Speichern eines anderen
  Abschnitts löscht die Zeitfenster nicht. Die Seite in Chromium angesehen — hell, dunkel,
  Handybreite, ohne Konsolenmeldung und ohne waagerechten Überlauf.
- Etappe 4a (Auswertung): gegen einen echt laufenden Webdienst mit 90 Tagen erzeugter Historie
  geprüft — Zeitraum-Umschalter, Kennzahlen, alle drei Diagramme, Filter über die Verteilung. Die
  Häufung von Fehlschlägen zu einer festen Uhrzeit wird erkannt und benannt. Die Seite in Chromium
  angesehen — hell, dunkel, Handybreite.
- Etappe 3 (Diagnose): **erstmals gegen echtes Tesseract geprüft**, nicht nur mit Attrappen. An zwei
  nachgestellten Screenshots (720×1280, weiße Schrift auf orangem Knopf) findet die Erkennung „Sammeln"
  mit Konfidenz 96 und erkennt den Erledigt-Zustand. Das Protokoll wurde über den echten Schreibweg
  erzeugt und wieder eingelesen, samt Traceback. Nachgewiesen: das hochgeladene Bild landet nirgends
  im Datenverzeichnis. Die Seite in Chromium angesehen — hell, dunkel, Handybreite.
  **Ein echter Screenshot eines Telefons im Offen-Zustand liegt weiterhin nicht vor** — das Werkzeug
  ist da, das Bild fehlt.
- Etappe 2 (Gerät & Dienst): Auftragsablage und Zustandsmeldung gegen einen echt laufenden Webdienst
  geprüft — Auftrag ablegen, doppelte und unbekannte Aufträge abweisen, ein Dienst-Takt arbeitet sie ab,
  die Seite zeigt Ergebnis und Screenshot. Der Root-Helfer `control.sh` wurde mit einer `systemctl`-Attrappe
  gegen Einschleusversuche geprüft (angehängter Befehl, Pfadausbruch, leere Zeile, Selbststopp der
  Oberfläche). Die Seite wurde in Chromium angesehen — hell, dunkel, Handybreite, ohne Konsolenmeldung.
  **Die systemd-Pfadeinheit selbst ist gegen echtes systemd ungetestet**, hier läuft keins.
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

## 3b. Akku und Home Assistant (ueber MQTT)

- `adb.parse_battery` ist eine reine Funktion über der Ausgabe von `dumpsys battery`. Fehlende Felder bleiben `None`;
  ein Gerät, das die Temperatur nicht meldet, gilt nicht als zu warm. `health` gilt nur bei einer ausdrücklichen
  Fehlermeldung als schlecht — sonst warnte der Dienst auf Geräten, die den Wert nicht liefern, jeden Tag ohne Anlass.
- Der Lauf nimmt den Stand nebenbei mit (`runner.read_battery`, fängt jede Ausnahme ab: der Check-in ist die
  Hauptsache). Zusätzlich fragt der Dienst alle `BATTERY_POLL_MIN` Minuten nach, damit Home Assistant frische Werte
  hat — ein einzelner Lauf am Tag genügt für eine Steckdosenautomatik nicht.
- `scheduler.BatteryWatch` ist reine Zustandshaltung wie `Outage`: je Problem eine Meldung, Entwarnung erst, wenn
  alle weg sind. `battery_problems` entscheidet, ohne Gedächtnis und ohne zu senden.
- Die Datenbank bekam drei Spalten (`battery_level`, `battery_temp_c`, `battery_status`). Bestehende Installationen
  werden beim Start des Dienstes per `ALTER TABLE` nachgezogen. Die Oberfläche öffnet nur lesend und kann darum
  nichts migrieren; ihre Abfrage wird deshalb aus den vorhandenen Spalten zusammengesetzt und liest `NULL`, solange
  der Dienst noch nicht gelaufen ist.
- Der Dienst schaltet in Home Assistant nichts, und das soll auch so bleiben: eine Automatik, die das Gerät vom
  Strom trennen kann, zerstört im Fehlerfall ihre eigene Grundlage (leerer Akku → Neustart → `adb tcpip` weg →
  nur per USB-Kabel zu heilen).
- `scheduler.BrokerLink` haelt den Anschluss und zieht ihn bei jedem Takt an den aktuellen Einstellungen nach.
  Praefix und Messabstand zaehlen mit, denn aus ihnen entstehen die Namen der Entitaeten und ihre Ablauffrist.
  Beim Abmelden gilt die Einstellung, unter der angemeldet wurde -- sonst bliebe unter dem alten Namen eine
  Entitaet stehen, die niemand mehr auf `unavailable` setzt.
- `mqtt.py` ist seit 0.16.0 der einzige Weg nach Home Assistant. Der fruehere REST-Weg (`homeassistant.py`) ist
  entfallen; er konnte drei Dinge nicht, die MQTT kann: ein Testament (Last Will), das die Entitaeten bei einem Ausfall des Dienstes auf `unavailable` setzt;
  retained Nachrichten, die einen Neustart von Home Assistant ueberstehen; und `unique_id` plus Geraeteeintrag,
  wodurch sich die Entitaeten umbenennen und einem Bereich zuordnen lassen.
- Die Discovery geht bei **jedem** Verbindungsaufbau erneut raus, nicht nur beim ersten: der Broker koennte
  zwischendurch neu gestartet worden sein und seine retained Nachrichten verloren haben. Dasselbe gilt fuer den
  zuletzt geschickten Zustand.
- Die beiden Akkusensoren tragen `expire_after` (dreimal `BATTERY_POLL_MIN`), damit ein eingefrorener Ladestand in
  Home Assistant nicht als aktueller Wert gilt. Weil der Zustand sonst nur bei Aenderung geht, ein am Netzteil
  haengendes Handy aber tagelang auf 100 Prozent steht, wird nach **jeder** Messung gesendet: eine Nachricht heisst
  "es wurde nachgesehen", keine Nachricht heisst "es wurde nicht gemessen". Ohne diese Kopplung waere die Frist
  schaedlich statt nuetzlich.
- Alle Entitaeten teilen sich ein Zustandsthema mit einer JSON-Nutzlast. Was der Dienst nicht kennt, fehlt darin;
  die Vorlagen machen mit `default('unknown')` daraus einen unbekannten Zustand statt einer erfundenen Null.
- `paho-mqtt` wird beim Import abgefangen: fehlt es, sagt der Dienst das einmal deutlich und laeuft weiter. Eine
  Installation, die noch nicht aktualisiert hat, startet damit trotzdem.
- `MQTT_PASSWORD` ist ein Geheimnis wie `DISCORD_WEBHOOK_URL`: in `settings.SECRET_KEYS`, nie in der Seite, nie im
  Protokoll. Dafür gibt es Tests.

## 3c. Zusatzaufgaben (`extras.py`)

Nach dem Einsammeln heisst der Knopf an derselben Stelle **"Mehr Muenzen verdienen"**. Dahinter faehrt ein
Fenster hoch ("Weitere Muenzen verdienen") mit einer scrollbaren Liste. Jede Aufgabe ist eine Karte aus
Titel, Beschreibung, Muenzwert und einem orangen Knopf **"Und los"** rechts. Die meisten verlangen nur,
sich rund fuenfzehn Sekunden auf einer Seite aufzuhalten.

- **Das Werbefenster** (0.17.2). Beim Verlassen der Coin-Seite schiebt die App ein Fenster davor:
  "Nicht vergessen: morgen fuer weitere Muenzen einchecken! +40", mit den Knoepfen **Verlassen** und
  **Bleiben**. Es legt sich ueber die Liste, und das Zurueck loest es aus. Getippt wird immer
  "Bleiben" -- "Verlassen" traegt uns aus der App und kostet den Rest des Durchgangs. Und getippt
  wird auf das **Wort**, nicht auf die Zeile: beide Knoepfe stehen nebeneinander, die Zeilenmitte
  liegt dazwischen. Nachgesehen wird nur, wenn gar keine Karte zu sehen ist -- liegt etwas davor,
  sind es keine.
- **Dieselbe Karte wird wiedererkannt, auch wenn sie anders gelesen wird** (0.18.3). Beim Blaettern sieht
  man jede Karte mehrfach, und die Texterkennung liest sie jedes Mal etwas anders: am 26.09.2026 wurde aus
  "Uebersicht ueber Ihre Muenzeinsparungen" beim zweiten Mal "Ds Uebersicht ...", aus "Suchen, was Sie
  lieben" wurde "Weitere Muenzen verdienen wa> Verdienen Sie ...". Auf Gleichheit verglichen zaehlte
  dieselbe Aufgabe darum mehrfach -- von sieben "brauchbaren" waren nur fuenf verschieden, und mit `--los`
  waeren zwei doppelt angetippt worden. Verglichen wird jetzt der Anteil gemeinsamer Woerter
  (`SAME_CARD_OVERLAP`), bei sehr kurzen Texten weiter auf Gleichheit. Dasselbe Mass gilt beim
  Wiederfinden einer Karte vor dem Antippen.
- **Der Rand ist eine halbe Kartenhoehe, keine ganze** (0.18.2). Zwischen zwei Knoepfen liegt die Grenze
  auf halbem Weg; ueber dem ersten und unter dem letzten galt frueher eine ganze Kartenhoehe, also der
  doppelte Abstand. Damit zog die erste Karte den Fenstertitel mit herein ("Weitere Muenzen verdienen
  Gesponserte Artikel entdecken ..."). Am Geraet lagen die Knoepfe 278 und 295 Pixel auseinander, der
  Titel 190 Pixel ueber dem ersten -- mit halbem Rand faellt er heraus.
- **Karten wachsen nicht ueber ihre uebliche Hoehe** (0.17.2). Wird nur ein Knopf gelesen, fehlt der
  Abstand zum naechsten als Massstab; ohne Grenze zieht die eine Karte den Fenstertitel und die
  Nachbarbeschreibung mit herein. Am 26.09.2026 hiess die einzige gefundene Karte darum "Weitere
  Muenzen verdienen 15s stoebern und sehen, wie viel ...". Jetzt reicht eine Karte hoechstens eine
  Kartenhoehe weit, bei einem einzelnen Knopf `LONE_CARD_SPAN`.
- **Der umgekehrte Durchgang nimmt den besten Schwellwert**, nicht den ersten, der irgendetwas
  hergibt (0.17.2): am 26.09.2026 fand der erste genau einen von drei Knoepfen. Das kostet drei
  Texterkennungen statt einer -- an den Knoepfen haengt die ganze Liste, die Genauigkeit ist es wert.
- **Gewartet wird auf den Bildschirm, nicht auf die Uhr** (0.17.2). `PAGE_TIMEOUT_S` ist die Obergrenze,
  nicht die Wartezeit: `_await` sieht alle `POLL_S` Sekunden nach und geht weiter, sobald der Knopf oder
  die erste Karte da ist. Vorher schlief der Befehl stur die volle Zeitspanne -- bei 90 Sekunden Zeitlimit
  anderthalb Minuten, bevor ueberhaupt etwas geschah, und danach nochmal 30 Sekunden nach jedem Blaettern.
  Nach einem Wisch muss die Liste nur zur Ruhe kommen, das sind zwei Sekunden.
- **Die Knoepfe ueber die Farbe, die Texte ueber die Schrift** (0.18.0). Zwei verschiedene Dinge, zwei
  Wege. Die Kartentexte stehen dunkel auf weiss und kommen im Originalbild durch. Die Beschriftung der
  Knoepfe steht weiss auf orange und war am Geraet **auch mit Umkehrung nicht zu lesen** -- zwei Anlaeufe
  am 26.09.2026 fanden einen von drei Knoepfen, dann keinen. Die Flaeche dagegen ist eindeutig:
  `find_orange_buttons` sucht kraeftiges Orange (Farbton 5-25, hoch gesaettigt) in Knopfgroesse am rechten
  Rand. Was dort liegt, muss gar nicht gelesen werden -- dass ein Knopf da ist, genuegt. Die Schriftsuche
  bleibt als Rueckfall, falls die App die Farbe aendert.
- **Mehrere Zusatzaufgaben am Geraet erledigt** (26.09.2026, 0.18.4): zwei von fuenf liefen sauber durch --
  antippen, Verweildauer, zurueck in die Liste, naechste. Drei wurden "nicht mehr gefunden", obwohl sie in
  der Liste standen: gesucht wurde von der Stelle aus, an der die vorige Aufgabe die Liste hinterlassen
  hatte, und geblaettert wird nur nach unten. Alles darueber war unerreichbar. Seit 0.19.1 beginnt jede
  Suche oben (`_to_top`) und reicht bis `FIND_SCROLLS` Wischer weit, bricht aber ab, sobald keine neuen
  Karten mehr auftauchen -- dann ist das Ende der Liste erreicht. Wird eine Karte nicht gefunden, liegt das
  Bild dazu in `data/extras/`.

- **Eine Zusatzaufgabe am Geraet erledigt** (26.09.2026): "Gesponserte Artikel entdecken" wurde angetippt,
  die Seite lief ihre 15 Sekunden ab, die Karte stand danach auf 2/2 mit gruenem Haken, und der Muenzstand
  stieg von 140 ueber 146 auf 151. **Tap, Verweildauer und Gutschrift sind damit belegt.**

  Zwei Fehler im Ablauf danach, in 0.18.4 behoben. Erstens galt die Liste nach dem Zurueck als verloren:
  gewartet wurden zwei Sekunden, das Fenster brauchte laenger. Zweitens eskalierten die Zurueck-Tasten --
  das erste schloss das Fenster, das zweite die Coin-Seite, das dritte trug aus der App; am Geraet stand
  danach der Startbildschirm. Jetzt wird auf die Liste gewartet (`BACK_TIMEOUT_S`), es gibt hoechstens ein
  zweites Zurueck, und beendet wird mit `force-stop` statt mit einem weiteren Zurueck.

- **Die ganze Liste am Geraet gelesen** (26.09.2026): vier Runden, 13 Lesungen, daraus zehn verschiedene
  Aufgaben -- und alle zehn richtig einsortiert. Erlaubt: Artikel entdecken, Muenzeinsparungen, Super
  Rabatte, Gutscheine & Einkaufsguthaben, Suchen. Gesperrt: kuerzlich angesehene Artikel (ueber
  "Warenkorb"), Wasser bei Preisland, Merge Boss, Tagesquiz. **Nicht belegt** ist alles danach: ob der Tap
  den Knopf trifft, ob das Zurueck in die Liste fuehrt, ob die Muenzen ankommen.
- **Am Geraet belegt** (26.09.2026, 720x1280): die Farbmaske fand die drei sichtbaren Knoepfe, alle genau
  154x77 an derselben x-Stelle (528..682). Dazu einen Fehltreffer, die Muenzgrafik im Kopf des Fensters
  mit 226x121. Seit 0.18.1 faellt der heraus: echte Knoepfe sind untereinander gleich gross, und was um
  mehr als `BUTTON_SIZE_SPREAD` von der ueblichen Groesse abweicht, ist keiner. Das misst sich selbst und
  braucht keine festen Pixelwerte, die bei anderer Aufloesung wieder danebenlaegen -- erst ab
  `BUTTON_QUORUM` Kandidaten, bei zweien waere der Mittelwert kein Massstab.
- Die Testbilder sind **gemalt** (`tests/test_button_color.py`), tragen aber die am Geraet gemessenen
  Groessen. Echte Screenshots liegen nicht im Repo, sie zeigen Kontodaten.
- **Nachsehen ohne Geraet:** `acc ocr data/extras/01-liste.png --extras` zeigt an einem abgelegten
  Screenshot, wie viele Knoepfe gefunden wurden und ob ueber Farbe oder Schrift, wie die Karten
  geschnitten und wie sie beurteilt wurden. Damit laesst sich die Erkennung nachstellen, ohne das Geraet
  anzufassen.
- **Karten statt Zeilen.** Titel brechen ueber zwei Zeilen um, und was eine Aufgabe ausmacht, steht teils
  im Titel, teils in der Beschreibung. Die Knoepfe geben die Grenzen vor: je Karte genau ein "Und los",
  also gehoert zu einer Karte, was naeher an ihrem Knopf liegt als am naechsten. Die Kopfzeile mit dem
  Muenzstand faellt damit von selbst heraus.
- **Positivliste, keine Sperrliste.** Angefasst wird nur, was in `EXTRAS_ALLOW` steht. Die Aufgaben wechseln
  taeglich; eine Sperrliste waere morgen unvollstaendig. `EXTRAS_DENY` gibt es trotzdem als zweites Netz und
  gewinnt immer.
- **Teilzeichenkette, nicht Wortanfang.** Deutsch setzt zusammen: "durchstoebern" muss "stober" treffen.
  Der Preis ist, dass die Sperrliste gelegentlich zu viel trifft. Das ist die richtige Richtung -- eine
  Aufgabe zu viel liegen zu lassen kostet fuenf Muenzen, eine zu viel angetippt zu haben kann etwas in den
  Warenkorb legen. Die Stichwoerter muessen dafuer genau sitzen: in der Sperrliste steht **"kaufen"**, nicht
  "kauf", sonst traefe es auch "Einkaufsguthaben" -- eine harmlose Stoeber-Aufgabe.
- **Die Liste ist der Beleg.** Nach jedem Zurueck wird geprueft, ob wieder ein "Und los" zu sehen ist. Ist
  es das nicht, wird ein zweites Mal zurueckgegangen und danach abgebrochen -- blind weiterzutippen waere
  der teuerste Fehler. Der Check-in ist zu diesem Zeitpunkt ohnehin schon verbucht.
- **Gezaehlt wird, was ankommt.** Vor und nach jeder Aufgabe wird der Muenzstand gelesen. Damit steht
  hinterher da, was eine Aufgabe wirklich gebracht hat, statt was sie verspricht.
- **Suchaufgaben** ("Suchen, was Sie lieben") verlangen mehr als Verweildauer: ein eingetipptes Wort, das im
  Suchverlauf des Kontos stehen bleibt. Sie werden nur angefasst, wenn in `EXTRAS_SEARCH_TERMS` ausdruecklich
  ein Begriff steht; sonst gelten sie als unbekannt und bleiben liegen. Der Begriff geht durch Shell und
  `input text` und ist darum auf Buchstaben, Ziffern, Leerzeichen und `. , + -` begrenzt -- Umlaute kommen
  dort nicht sauber an. Die Sperrliste gewinnt auch gegen eine Suchaufgabe.
- Bewusst gesperrt: "Taegliche Anmeldung" (ist der Check-in selbst), Merge Boss, Tagesquiz und "1 x Wasser bei
  Preisland hinzufuegen" -- Letzteres legt etwas in den Warenkorb.
- **Die Verweildauer ist geraten, nicht gemessen.** Die App zaehlt 15 Sekunden, aber erst ab geladener Seite,
  und rechts am Rand laeuft dabei ein Zaehler, der am Ende einen gruenen Haken zeigt. Gewartet wird bisher
  stur `EXTRAS_DWELL_S` (25 s). Den Zaehler wirklich abzulesen -- und damit zu wissen, ob eine Aufgabe zaehlte,
  statt es zu hoffen -- geht erst, wenn ein Screenshot einer laufenden Aufgabenseite vorliegt.
- Eine Aufgabe faellt derzeit **zu Unrecht** durch: "In kuerzlich angesehenen Artikeln stoebern" traegt
  "Warenkorb" in der Beschreibung und wird davon gesperrt. Wer sie will, nimmt `warenkorb` aus
  `EXTRAS_DENY` -- der eigentliche Schutz vor dem Warenkorb ist `hinzufug`.

## 4. Zeitplan und Entscheidung (`scheduler.py`)

**Der Muenztag beginnt nicht um Mitternacht** (0.19.0). Die neuen Muenzen stehen erst am Vormittag bereit --
beim Nutzer beobachtet ab etwa 8 bis 9 Uhr. Wer davor nachsieht, sieht noch den Stand des Vortags: die Seite
meldet "heute schon eingecheckt", und das stimmt auch, nur eben fuer gestern.

Am 26.09.2026 wurde genau das gemeldet. Ein Lauf von Hand um vier Uhr nachts fand den Check-in des Vortags
erledigt vor, das Ergebnis wurde als Erfolg des **neuen** Kalendertags verbucht -- und damit fiel der echte
Lauf des Tages aus. Ein Tag ohne Muenzen, ohne dass irgendetwas kaputt war.

- `coin_day(wann, beginn)` sagt, zu welchem Muenztag ein Zeitpunkt gehoert: vor `COIN_DAY_START` zum
  vorigen. `decide` bekommt seither die Versuche des laufenden Muenztags (`attempts_of_coin_day`), nicht die
  des Kalendertags.
- Abgefragt wird ueber den **Zeitraum** (`store.between`), nicht ueber die Spalte `day`. Die traegt weiter
  den Kalendertag: eine Umdeutung haette die vorhandene Historie still verschoben. Die Folge ist ein
  kosmetischer Versatz -- ein Nachtlauf erscheint im Verlauf unter dem neuen Tag, zaehlt fuer die
  Entscheidung aber zum alten. Wer das auch in der Historie sauber will, braucht eine Migration.
- `attempts_for_plan_day` ist die Anzeigeseite davon: vor dem Beginn des Muenztags steht fuer heute noch
  alles aus, egal was nachts lief. Nur dafuer, nicht fuer die Entscheidung.
- **`MORNING_START` sollte nicht vor `COIN_DAY_START` liegen.** Sonst laeuft der erste Versuch ins Leere,
  meldet "schon erledigt" fuer gestern, und erst ein spaeterer holt die Muenzen wirklich. Der Dienst warnt
  beim Start, verweigert aber nichts -- es ist eine Frage der Einstellung, kein Fehler.
- `COIN_DAY_START=00:00` stellt das alte Verhalten wieder her.

**Nicht belegt:** wann genau der Muenztag umschlaegt. 08:00 ist die Vorgabe, die Beobachtung des Nutzers
lautet "irgendwann ab 8 oder 9". Punkt 3 der offenen Liste (Umschaltzeit auswerten) bleibt damit offen --
belegt ist nur, dass es nicht Mitternacht ist.


- `plan_for(day, cfg)` liefert zufällige, aber pro Datum feste Uhrzeiten in Morgen- und Abendfenster. Der Seed hängt
  nur vom Datum ab, die Zeiten überleben also Neustarts des Dienstes.
- `decide(now, plan, attempts, cfg)` ist reine Logik und gut getestet: kein Lauf mehr nach einem Erfolg
  (`claimed`/`already_done`); `busy` zählt nicht als Versuch; höchstens ein echter Versuch je Art; der Abendlauf
  startet auch dann, wenn morgens nichts lief; nach `BUSY_RETRY_MIN` wird erneut versucht, nach `BUSY_MAX_WAIT_MIN`
  wird trotz eingeschaltetem Bildschirm gestartet.
- **`EVENING_ENABLED=false` nimmt den Abendlauf ganz heraus.** Dann entfällt die obere Grenze des Morgenlaufs: er
  bleibt bis Mitternacht fällig, statt mit der Abendzeit zu verfallen — sonst wäre ein Morgenfenster hinter der
  (dann bedeutungslosen) Abendzeit tot. Auch die Prüfung „Morgenfenster vor Abendfenster“ entfällt, das Fenster
  darf also frei liegen. Preis: ein misslungener Morgenlauf wird an diesem Tag nicht nachgeholt.
- `handle_result` schreibt in SQLite, meldet per Discord (Fehler immer, Erfolge je nach `NOTIFY_ON_*`, `busy` nie),
  legt Fehler-Screenshots in `data/shots/` ab (die letzten 30) und ergänzt beim Abendlauf den Hinweis, dass es der
  letzte Versuch des Tages war.
- `daemon` prüft alle 30 s und ruft bei einer Entscheidung `run_once` auf. Die Pause dazwischen verschläft er
  nicht am Stück: `wait_for_commands` schläft in Sekundenscheiben und sieht bei jeder nach Aufträgen, damit ein
  Screenshot aus der Oberfläche nicht bis zu einer halben Minute liegt. Das kostet ein `listdir` je Sekunde.
- `next_runs(day, cfg, days)` und `next_due(now, cfg, attempts)` sind reine Hilfsfunktionen für die Anzeige:
  die geplanten Uhrzeiten der nächsten Tage und der nächste noch ausstehende Lauf. Sie treffen keine
  Entscheidung und werden vom `schedule`-Befehl und von `doctor` genutzt.

> Der Seed in `scheduler._SEED` ist bewusst der historische Wert `aliexpress-coins`. Ändert man ihn, verschieben sich
> alle geplanten Uhrzeiten.

## 4a. Austausch zwischen den beiden Diensten

Alles läuft über Dateien in `data/`, niemand ruft den anderen direkt auf. Je Datei genau ein Schreiber.

| Datei | Schreiber | Leser | Zweck |
|---|---|---|---|
| `heartbeat` | Sammel-Dienst | Oberfläche | Lebenszeichen, alle 30 s berührt |
| `status.json` | Sammel-Dienst | Oberfläche | Gerätezustand, Bildschirm, Version des Dienstes |
| `commands/*.json` | Oberfläche | Sammel-Dienst | offene Aufträge (`run`, `reconnect`, `screenshot`, `check`) |
| `commands/done/*.json` | Sammel-Dienst | Oberfläche | Ergebnis, die letzten 20 |
| `settings.json` | Oberfläche | beide | geänderte Einstellungen, überschreibt die `.env`. `0600`, weil der Discord-Webhook darin stehen kann |
| `web-password` | Oberfläche | Oberfläche | Hash des Passworts |
| `control` | Oberfläche | `control.sh` (root) | zwei geprüfte Wörter für `systemctl` |
| `control-result` | `control.sh` (root) | Oberfläche | Ausgang der letzten Steuerung |
| `collector.log` | Sammel-Dienst | Oberfläche | Protokoll, rotierend, 5 × 1 MB. Nur der Dienst schreibt es |
| `run-requested` | (Altlast) | Sammel-Dienst | Auftragsdatei vor Version 0.5.0, wird noch angenommen |

Die Wartezeit von bis zu einem Takt ist der Preis dafür, dass genau ein Prozess das Gerät anfasst.
Die Oberfläche zeigt sie an, statt sie zu verstecken.

**Warum eine Protokolldatei, wo doch alles ins journald geht:** der Webdienst darf das Journal
nicht lesen. Ihn in die Gruppe `systemd-journal` zu nehmen wäre der Preis gewesen. Geschrieben
wird sie nur vom Dienst — ein Aufruf von Hand (`once`, `doctor`) protokolliert weiter nur auf die
Konsole, sonst legt der erste Aufruf als root eine Datei an, die der Dienst nicht mehr beschreiben
kann.

**Warum `control.sh` und kein `sudo`:** der Webdienst läuft mit `NoNewPrivileges=yes`, damit
funktioniert `sudo` nicht (es braucht setuid). `polkit` ist in einem schlanken LXC oft gar nicht
installiert. Eine systemd-Pfadeinheit auf `data/control` braucht beides nicht und lässt die
Absicherung des Webdienstes unangetastet.

## 5. Datenbank (`store.py`)

Tabelle `runs(id, ts, day, kind, outcome, coins_before, coins_after, message, duration_s)`.

`ts` ist die **naive Ortszeit des Servers** zum Zeitpunkt des Laufs — die Zeitzone des Servers muss deshalb stimmen,
sonst passen Zeitfenster und gespeicherte Zeiten nicht zusammen. `kind` ist `morning`, `evening` oder `manual`, die
möglichen Werte von `outcome` stehen in `runner.Outcome`.

## 5a. Verbindung: warum sie sich selbst heilen muss

`adb get-state` fragt nur den **lokalen** adb-Server. Reisst die TCP-Verbindung ab -- Netz kurz weg,
Geraet im Doze, adb-Server neu gestartet --, meldet get-state dauerhaft `offline`, auch wenn das
Geraet laengst wieder erreichbar ist. Von allein baut es nie neu auf; nur `adb connect` tut das.

Bis 0.8.0 rief die zyklische Zustandsmeldung ausschliesslich get-state. Die Oberflaeche zeigte
darum bis in alle Ewigkeit "nicht erreichbar", bis jemand von Hand "Neu verbinden" drueckte --
auf dem Produktivsystem genau so beobachtet. Seit 0.8.1 versucht `report_status` selbst ein
`connect`, mit wachsendem Abstand (sofort, dann 1, 2, 5, 10 und ab da 30 Minuten). Der Abstand ist
noetig, weil ein `connect` auf ein totes Geraet 15 Sekunden in einen Timeout laeuft -- bei einem
Takt von 30 Sekunden waere das die halbe Arbeitszeit des Dienstes. Bei `unauthorized` wird gar
nicht erst versucht: die Verbindung steht ja, es wartet nur ein Dialog auf dem Geraet.

Ebenfalls seit 0.8.1: nach einem abgearbeiteten Auftrag wird der Zustand **erneut** gemeldet.
Vorher lag `report_status` vor `process_commands` im selben Takt, ein erfolgreiches
"Neu verbinden" war also bis zu 30 Sekunden unsichtbar -- zusammen mit der Abholzeit und dem
10-Sekunden-Poll der Seite bis zu 70 Sekunden. Es sah aus, als haette der Knopf nichts getan.

## 5b. Discord-Meldungen

Ein Embed statt einer Textzeile: Farbe nach Ausgang, Zahlen in eigenen Feldern. Der Grund eines
Fehlschlags steht ausgeschrieben da (`FAILURE_REASONS`), nicht als `not_found` -- auf dem Handy
gelesen sagt ein Codewort nichts.

Getrennt gehalten sind **`describe()`** (eine Zeile fuers Protokoll, ohne Umlaute wie der Rest der
Log-Ausgabe) und **`message_for()`** (die Discord-Meldung, mit Umlauten -- sie liest ein Mensch,
und sie geht weder an die Shell noch an ADB). Sonst muesste eine von beiden Kompromisse machen.

`notify.py` kuerzt Titel, Beschreibung und Felder auf die Grenzen der Discord-API. Wird eine
ueberschritten, lehnt Discord die **ganze** Nachricht ab -- lieber gekuerzt als verloren.

Die Ausfallmeldung (`Outage`) haelt nur fest, seit wann das Geraet weg ist und ob deswegen schon
gemeldet wurde; geschickt wird im Takt. Je Ausfall genau eine Meldung, und die Entwarnung nur,
wenn es vorher auch eine Stoerung gab -- sonst kaeme nach jedem kurzen Aussetzer ein Haken.

Die **Testmeldung** schickt ausnahmsweise die Oberflaeche selbst statt ueber die Auftragsablage:
es ist nur eine HTTPS-Anfrage, kein Zugriff aufs Geraet -- und wer testet, will die Antwort sofort
sehen und nicht dreissig Sekunden auf den naechsten Takt warten. Sie laeuft im Threadpool, weil
`requests` blockiert. Denselben Test gibt es als `notify-test` auf der Kommandozeile.

`send()` gibt darum ein `Sent(ok, detail)` zurueck statt eines nackten bool: bei einem Test will
jemand wissen *warum* es nicht ging. Der Grund steht im Klartext da ("Diesen Webhook gibt es nicht
(mehr)") statt als HTTP-Nummer, und der Webhook selbst nie darin.

Die Geraeteadresse geht nur verkuerzt hinaus (`commands.mask_serial`). Ein Chat-Kanal ist kein
Ort fuer interne Adressen, und Discord-Nachrichten bleiben dort lange stehen.

## 5c. Warum der Erledigt-Marker nicht mehr gewinnt

Die ersten echten Laufdaten zeigten ein Muster, das nicht aufgehen konnte: dreimal `already_done`
bei unveraendertem Muenzstand (10), und zwei Stunden nach dem letzten dieser Laeufe liess sich am
**selben Tag** noch einsammeln (10 → 25). Ein echtes "schon erledigt" haette vorher einen Anstieg
hinterlassen muessen.

Ursache in `ocr.analyze`: `done` wurde aus einem `\bmorgen` in der oberen Bildhaelfte gebildet, und
bei `done` lief die Knopfsuche **gar nicht erst**. Der Vorschautext "wenn Sie morgen vorbeischauen"
steht dort aber in beiden Zustaenden. Dazu kam `_wait_for_page`, das beim ersten Bild mit Marker
sofort aufhoerte -- also womoeglich auf einer halb gerenderten Seite, auf der der Knopf einfach
noch fehlte.

Seit 0.11.0:
- Der Knopf wird **immer** gesucht, und ein gefundener Knopf schlaegt den Marker. Ein Fehlgriff in
  die Gegenrichtung ist unwahrscheinlich: "gesammelt" erreicht gegen "sammeln" nur 0.75 und
  "verdienen" noch weniger -- beide bleiben unter der Schwelle von 0.8.
- Der Erledigt-Zustand wird erst nach `DONE_SETTLE_ROUNDS` aufeinanderfolgenden Bildern geglaubt
  (rund sechs Sekunden). Gezaehlt und nicht die Uhr befragt, damit es ohne Warten pruefbar ist.

## 5d. Der Stillstandswaechter

Der Schaden am 21.09.2026 war nicht der Marker-Fehler (siehe 5c), sondern dass er tagelang
unbemerkt blieb: das System meldete Erfolg, waehrend nichts geschah. Der Fehler selbst ist
behoben, die Klasse nicht -- Layout geaendert, Konto gesperrt, Tap ins Leere.

`stats.stalled_since` prueft darum das Ergebnis statt den Schritt: steht der Muenzstand an drei
aufeinanderfolgenden Erfolgstagen unveraendert, gibt es eine rote Meldung und ein Banner auf der
Uebersicht. Das haette den Fehler am zweiten Tag gefangen, ohne dass jemand die Ursache kennen
musste.

Bewusst nicht mitgezaehlt: Tage ohne Erfolg (dort ist ein stehender Stand die erwartete Folge),
ein unlesbarer Stand (unbekannt ist nicht unveraendert) und ausgegebene Muenzen (der naechste
Anstieg beendet die Serie ohnehin).

Gemerkt wird der Muenzstand, nicht ein Datum: solange er sich nicht bewegt, ist es derselbe
Vorfall und es bleibt bei einer Meldung.

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
| Einstellungen in `settings.json`, nicht in einer zweiten Datenbank | Ein knappes Dutzend Werte ohne Änderungsverlauf. Eine Datei lässt sich im Notfall mit dem Editor geradeziehen, eine zweite SQLite neben `coins.sqlite3` nicht. Die Datei liegt bereits da und wird vom Dienst ohnehin bei jedem Takt gelesen. |
| Die Oberfläche prüft mit `Config.validate`, nicht mit eigenen Regeln | Zwei Regelsätze laufen auseinander. So weist die Seite genau das ab, was auch den Dienst stören würde — die `.env`-Namen in der Meldung werden nur für die Anzeige durch die Feldbeschriftung ersetzt. |
| Der Discord-Webhook wird nie in die Seite geschrieben | Er ist ein Geheimnis. Das Feld bleibt leer und heißt „unverändert"; gelöscht wird nur über ein eigenes Kästchen. Damit landet er weder im Browserverlauf noch in einem Screenshot der Seite. |
| Erkennungswerte (`BUTTON_LABELS`, `OCR_LANG`, `COIN_URL`, `APP_PACKAGE`) bleiben in der `.env` | Ein Vertipper dort ließe den Lauf ins Leere greifen, ohne dass es auffiele. Die Oberfläche soll den Ablauf verstellen können, nicht die Erkennung. |
| Nur eine Fensteränderung setzt `changed_at` fort | Der Zeitstempel legt den heutigen Zeitplan teilweise still. Täte das auch ein geänderter Webhook, unterdrückte eine harmlose Änderung einen fälligen Lauf. |

## 8. Offene Punkte

Nichts davon ist beschlossen, die Reihenfolge ist ein Vorschlag.

1. ~~**Tap auf dem Gerät bestätigen.**~~ Belegt am 21.09.2026: ein Lauf endete mit `claimed`, Münzstand
   10 → 25. Offen bleibt nur noch, dass das über mehrere Tage stabil bleibt.
2. **Fixtures für Erkennungstests.** Echte Screenshots (offen und erledigt, verschiedene Auflösungen) als Testbilder,
   dazu Regressionstests für `ocr.analyze`. Screenshots zeigen Kontostände und müssen vorher zugeschnitten werden;
   die CI bräuchte dafür `tesseract-ocr` und das passende Sprachpaket.
3. ~~**Umschaltzeit auswerten.**~~ Teilweise umgesetzt in 0.10.0 (`data.rollover_hints`): die Verlaufsseite
   meldet es, wenn ein Abendlauf gesammelt hat und es am nächsten Morgen schon erledigt war. Den
   Umschaltpunkt exakt zu bestimmen geht mit einem Lauf pro Tag nicht — dafür fehlen Beobachtungen zu
   verschiedenen Uhrzeiten. Der Hinweis erscheint nur, wenn es überhaupt Abendläufe gab.
4. ~~**Login-Abgelaufen-Erkennung.**~~ Umgesetzt in 0.10.0 (`ocr.looks_logged_out`,
   `Outcome.LOGIN_REQUIRED`). **Die Marker sind an keinem echten abgemeldeten Screenshot geprüft** —
   sie sind begründete Vorgaben. Nachprüfbar ohne Wartezeit über das Werkzeug auf der Diagnose-Seite.
5. **Popups wegtippen** (Bewertungsaufforderung, Update-Hinweis) statt daran zu scheitern.
6a. **Zusatzaufgaben: was noch fehlt.** Der Muenzstand aus einem `extras`-Lauf landet **nirgends** --
   der Befehl schreibt nichts in die Datenbank, also zeigt die Oberflaeche weiter den Stand des letzten
   Check-ins. Zu klaeren ist, wohin er gehoert: eine eigene Zeile in `runs` (Art `extras`) waere die
   kleinste Loesung, verwaessert aber die Erfolgsquote des Check-ins. Ausserdem lassen sich manche Aufgaben
   **mehrfach** erledigen -- die Karten tragen einen Zaehler ("1/2", "0/3"), der bisher ignoriert wird.

6. **Zusatzaufgaben** der Coin-Seite. Angefangen in 0.17.0 (`extras.py`, Befehl `extras`), noch von Hand
   auszuloesen und **nicht** in den taeglichen Lauf eingebaut -- das kommt erst, wenn die Erkennung am Geraet
   belegt ist. Offen bleibt ausserdem: "Suchen, was Sie lieben" verlangt ein eingetipptes Suchwort und steht
   darum nicht auf der Positivliste; die Zaehler an den Karten ("0/3") werden nicht ausgewertet, obwohl sie
   sagen, wie oft eine Aufgabe noch geht; und eine Aufgabe, die dreimal nichts einbrachte, koennte sich
   selbst abschalten.
7. **Vision-Modell als Ausweichweg** für die Button-Erkennung, falls die OCR zu oft danebenliegt.
8. **Neustart des Geräts abfangen.** `adb tcpip 5555` lässt sich ohne USB nicht wiederholen; die `unreachable`-Meldung
   weist bereits darauf hin. Bei Android 11+ wäre Wireless Debugging eine Alternative.
9. **Backup-Fenster des Hypervisors gegen die Laufzeiten prüfen.** Hält oder friert ein Proxmox-Backup den LXC an
   (Modus `stop` oder `suspend`, bei `snapshot` kurz per fsfreeze), reißt die ADB-Verbindung ab und ein Lauf, der
   in dieses Fenster fällt, endet als `unreachable`. Fällt das Backup mit dem Morgenfenster zusammen, sollte eins
   von beiden verschoben werden. Der Dienst holt einen verpassten Morgenlauf abends nach, das federt es ab,
   ersetzt aber keine saubere Trennung der Fenster.
10. **Weboberfläche ausbauen.** Etappen 1 bis 4b sind umgesetzt: Grundgerüst, Gerät & Dienst,
    Diagnose, Auswertung und Einstellungen. Offen bleibt nur noch der Feinschliff — und ein
    Verbindungstest direkt beim Speichern einer neuen Geräteadresse, der bewusst nicht gebaut
    wurde: er würde die Befehlsablage um Aufträge mit Nutzdaten erweitern. Bis dahin wird
    gespeichert und auf der Seite *Gerät* geprüft.
11. **App nach dem Lauf beenden?** Sie bleibt derzeit im Hintergrund auf der Coin-Seite stehen; beendet
    wird sie erst beim nächsten Lauf. Ein `force_stop` direkt nach der Bestätigung wäre riskant, weil
    unklar ist, ob die App den Vorgang schon zum Server durchgeschrieben hat. Falls gewünscht: nur dann
    beenden, wenn der Dienst das Gerät selbst geweckt hat, und mit Abstand.

## 9. Regeln für Änderungen

- **Erkennung:** Änderungen an Zonen, Schwellen oder Labels immer an mehreren echten Screenshots prüfen (verschiedene
  Auflösungen, offen und erledigt) und die relativen Bereiche nicht auf ein einzelnes Bild trimmen.
- **Neue Logik bekommt einen Test.** Die Attrappen stehen bereit: `FakeAdb` in `tests/test_runner.py` und eine
  injizierbare `analyze`-Funktion.
- **Vor jedem Commit** `python -m pytest -q`, `ruff check .` und `ruff format --check .` laufen lassen. Bei Änderungen
  an `install.sh` zusätzlich `bash -n install.sh` und `shellcheck install.sh`.
- **Keine Geheimnisse im Repo:** keine Webhook-URLs, Zugangsdaten, Geräteadressen oder ungeprüften Screenshots.
  Die `.env` steht in `.gitignore` und gehört dort auch hin.
