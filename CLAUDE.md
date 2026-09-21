# CLAUDE.md — aliexpress-coin-collector

Diese Datei wird von Claude Code beim Start gelesen. Hintergrund, Architekturdetails, was verifiziert ist und was
noch offen ist, stehen in **`docs/STATUS.md`** — bitte zuerst dort lesen.

## Worum es geht

Automatisiert den täglichen Coin-Check-in der AliExpress-App. Ein Android-Gerät wird per ADB über WLAN gesteuert,
die Coin-Seite per Deep-Link geöffnet, der "Sammeln"-Button per OCR (Tesseract) gefunden und angetippt. Ergebnisse
gehen per Discord raus, jeder Lauf steht in einer SQLite-Datenbank. Läuft als systemd-Dienst.

## Arbeitsregeln

- **Sprache:** Antworten und Doku auf Deutsch. Code-Kommentare, Log- und Discord-Texte sind deutsch, Bezeichner
  englisch. Ohne Umlaute in Quelltext-Strings, die an Shell oder ADB gehen.
- **Ein Lauf fasst echte Hardware an.** `once`, `doctor` und `daemon` sprechen ein Gerät an und tippen darauf.
  Diese Befehle nur nach ausdrücklicher Zustimmung ausführen, nie ungefragt.
- **Produktivsystem nicht anfassen ohne Rückfrage.** Nichts neu starten, migrieren oder am Gerät verstellen
  (Einstellungen, Bildschirmdichte, Apps), solange es nicht ausdrücklich erlaubt ist.
- **Keine Geheimnisse:** Nie Zugangsdaten, Webhook-URLs, Passwörter, Tokens, Kontodaten, Geräteadressen oder interne
  IPs in Dateien, Commits, Logs oder Nachrichten schreiben. `.env` steht in `.gitignore`. Screenshots enthalten
  Kontodaten und kommen nicht ungeprüft ins Repo.
- **Nie Zugangsdaten auf dem Gerät eingeben.** Die Anmeldung in der App macht der Nutzer selbst. Keine Umgehung von
  Captchas, Verifizierungen oder Login — das ist bewusst ausgeschlossen.
- **Vor jeder Änderung an Erkennung oder Ablauf** die Tests laufen lassen, danach `ruff`. Änderungen an der Erkennung
  möglichst an echten Screenshots prüfen (`ocr`-Befehl), und zwar an mehreren Auflösungen und Zuständen.
- **Ehrlich zum Stand:** In Berichten strikt zwischen "auf dem Gerät gesehen" und "mit Attrappen getestet"
  unterscheiden. Der aktuelle Stand steht in `docs/STATUS.md` Abschnitt 1.
- **Commits klein halten**, Commit-Texte deutsch.

## Befehle

```bash
python -m pytest -q                       # Tests, ohne Gerät (Attrappen)
ruff check . && ruff format --check .
python -m aliexpress_coin_collector doctor                     # Installation + Geräteverbindung
python -m aliexpress_coin_collector once --force --no-notify   # ein Lauf (fasst das Gerät an!)
python -m aliexpress_coin_collector ocr bild.png --text        # Erkennung an Screenshot testen
python -m aliexpress_coin_collector status                     # letzte Läufe + Summen
python -m aliexpress_coin_collector schedule                   # geplante Uhrzeiten der nächsten Tage
python -m aliexpress_coin_collector.web                        # Weboberfläche (eigener Dienst)
```

`once`, `doctor` und `daemon` brauchen Netzzugang zum Gerät. `ocr`, `--version` und die Tests brauchen kein Gerät.

Bei Änderungen an `install.sh` zusätzlich `bash -n install.sh` und `shellcheck install.sh`. Dieselben Prüfungen
laufen in der CI.

## Struktur

```
aliexpress_coin_collector/
  config.py     .env laden, Config (frozen dataclass), alle Einstellungen
  adb.py        ADB-Wrapper (connect, screenshot, tap, wake/sleep, Deep-Link)
  ocr.py        Screenshot -> PageState (Button, erledigt, Münzstand)
  runner.py     ein Lauf: Outcome-Logik, Wartezeiten, Wiederholung, Bestätigung
  scheduler.py  Zeitplan (plan_for, next_runs, next_due), Entscheidung (decide), Dienstschleife, Meldungen
  store.py      SQLite (Tabelle runs)
  notify.py     Discord-Webhook (wirft nie)
  __main__.py   CLI: once | daemon | ocr | status | schedule | doctor
  settings.py   zur Laufzeit änderbare Einstellungen (data/settings.json): überschreibt die .env
  commands.py   Auftragsablage Oberfläche → Dienst, Zustandsmeldung Dienst → Oberfläche
  logs.py       Protokolldatei des Dienstes: schreiben und wieder einlesen
  web/          Weboberfläche (eigener Dienst, liest die Datenbank nur):
                data.py (Auswertung), view.py (Jinja-Umgebung + Aufbereitung),
                charts.py (SVG), auth.py (Passwort-Hash), imagecheck.py (Erkennung
                an hochgeladenen Bildern prüfen), app.py (FastAPI; schreibt als
                einziges auch settings.json),
                templates/ (Jinja), static/ (CSS, htmx, Schrift, Icon)
tests/          test_config.py, test_runner.py, test_scheduler.py, test_settings.py,
                test_commands.py, test_daemon_commands.py, test_logs.py,
                test_imagecheck.py, test_web_data.py, test_web_auth.py,
                test_web_pages.py, test_web_device.py, test_web_diagnose.py,
                test_web_stats.py, test_web_settings.py
                (Attrappen, weder Gerät noch Tesseract nötig)
control.sh      Root-Helfer: startet/stoppt die Dienste, von systemd auf data/control hin
                gestartet. Nimmt keinen Befehl entgegen, nur zwei geprüfte Wörter.
install.sh      Installation/Update im LXC: lokaler Quellbaum oder Selbstdownload per curl,
                Migration der Vorgängerversion, --update mit Rollback, --uninstall
.github/workflows/ci.yml   CI: ruff, pytest (3.10/3.12), shellcheck
docs/STATUS.md  Stand, Technikdetails, Stolpersteine, offene Punkte
```

## Stil

Python ≥ 3.10, `from __future__ import annotations`, Typannotationen, Zeilenlänge 120, `ruff` mit E, F, I, B, UP.
Reine Logik (Entscheidungen, Erkennung) getrennt von Ein-/Ausgabe halten, damit sie ohne Gerät testbar bleibt.
Runner und Scheduler bekommen ADB, Analysefunktion und `sleep` injiziert — so arbeiten die Tests.
