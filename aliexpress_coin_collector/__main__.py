from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, logs, ocr
from .adb import Adb, AdbError
from .config import Config, ConfigError
from .runner import run_once
from .scheduler import daemon, describe, handle_result, next_due, next_runs, plan_for
from .store import Store


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format=logs.FORMAT)


def cmd_once(cfg: Config, args: argparse.Namespace) -> int:
    adb = Adb(cfg.adb_serial, cfg.adb_path)
    result = run_once(cfg, adb, force=args.force)
    print(describe("manual", result))
    if result.screenshot and not result.ok:
        out = cfg.data_dir / "last_failure.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(result.screenshot)
        print(f"Screenshot: {out}")
    if not args.no_record:
        store = Store(cfg.data_dir)
        if args.no_notify:
            cfg = Config(**{**cfg.__dict__, "discord_webhook": ""})
        handle_result(cfg, store, "manual", result)
    return 0 if result.ok else 1


def cmd_daemon(cfg: Config, args: argparse.Namespace) -> int:
    # Zusaetzlich in eine Datei, damit die Weboberflaeche das Protokoll zeigen kann.
    # Nur hier, nicht bei Aufrufen von Hand: sonst gehoert die Datei danach dem
    # falschen Nutzer und der Dienst kann sie nicht mehr beschreiben.
    logs.attach(cfg.data_dir, cfg.log_level)
    # Die Version wandert mit in die Zustandsmeldung: die Oberflaeche erkennt daran,
    # ob der Dienst nach einem Update noch in der alten Fassung laeuft.
    daemon(cfg, Adb(cfg.adb_serial, cfg.adb_path), Store(cfg.data_dir), version=__version__)
    return 0


def cmd_ocr(cfg: Config, args: argparse.Namespace) -> int:
    png = Path(args.image).read_bytes()
    state = ocr.analyze(png, cfg)
    print(f"Bild: {state.width}x{state.height}")
    b = state.button
    print(f"Button:  {(b.text, b.cx, b.cy, round(b.conf)) if b else None}")
    print(f"Erledigt: {state.done}")
    print(f"Muenzstand: {state.coins}")
    if args.text:
        print("\nText:", state.text)
    return 0


def cmd_status(cfg: Config, args: argparse.Namespace) -> int:
    store = Store(cfg.data_dir)
    runs = store.recent(args.n)
    if not runs:
        print("Noch keine Laeufe gespeichert.")
        return 0
    for a in reversed(runs):
        coins = f"{a.coins_before}->{a.coins_after}" if a.coins_before is not None else "-"
        print(f"{a.ts:%Y-%m-%d %H:%M}  {a.kind:8} {a.outcome:13} {coins:10} {a.duration_s:5.1f}s  {a.message}")
    print("\nSummen:", store.outcome_counts())
    return 0


def cmd_schedule(cfg: Config, args: argparse.Namespace) -> int:
    now = datetime.now()
    store = Store(cfg.data_dir)
    by_day = {now.date(): store.for_day(now.date())}

    print(
        f"Fenster: morgens {cfg.morning_start:%H:%M}-{cfg.morning_end:%H:%M}, "
        f"abends {cfg.evening_start:%H:%M}-{cfg.evening_end:%H:%M}"
    )
    print(f"{'Datum':12} {'Morgenlauf':11} {'Abendlauf':11} Stand")
    for day, plan in next_runs(now.date(), cfg, max(1, args.n)):
        attempts = by_day.get(day, [])
        if attempts:
            stand = ", ".join(f"{a.kind}={a.outcome}" for a in attempts)
        else:
            stand = "heute noch offen" if day == now.date() else ""
        print(f"{day:%Y-%m-%d}   {plan.morning_at:%H:%M}       {plan.evening_at:%H:%M}       {stand}")

    due = next_due(now, cfg, by_day[now.date()])
    if due is None:
        print("\nKein weiterer Lauf im Planungshorizont.")
    else:
        rest = due - now
        hours, minutes = divmod(int(rest.total_seconds()) // 60, 60)
        print(f"\nNaechster Lauf: {due:%Y-%m-%d %H:%M} (in {hours} h {minutes} min)")
    print("Der Abendlauf startet nur, wenn morgens nichts geklappt hat.")
    return 0


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    ok = True
    print(f"aliexpress-coin-collector {__version__}")

    env_file = Path(args.env)
    if env_file.is_file():
        print(f"OK  Konfiguration: {env_file}")
    else:
        # Kein Fehler: alle Werte koennen auch aus echten Umgebungsvariablen kommen (z. B. im Dienst).
        print(f"HINW {env_file} nicht gefunden, es gelten nur die Umgebungsvariablen")

    now = datetime.now().astimezone()
    tzname = now.tzname() or "unbekannt"
    if now.utcoffset() == timedelta(0):
        # Die Zeitfenster gelten in Serverzeit. Laeuft der Container in UTC, greifen sie zur falschen Stunde.
        print(f"WARN Zeitzone {tzname}: die Zeitfenster gelten in Serverzeit, das ist vermutlich nicht gewollt")
        print("     Korrektur: timedatectl set-timezone Europe/Berlin")
    else:
        print(f"OK  Zeitzone {tzname} (UTC{now.strftime('%z')})")

    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.data_dir / ".doctor-probe"
        probe.write_bytes(b"")
        probe.unlink()
        print(f"OK  Datenverzeichnis beschreibbar: {cfg.data_dir}")
    except OSError as exc:
        print(f"FEHLT Datenverzeichnis {cfg.data_dir} nicht beschreibbar: {exc}")
        ok = False

    for tool in (cfg.adb_path, "tesseract"):
        found = shutil.which(tool)
        print(f"{'OK ' if found else 'FEHLT'} {tool}: {found or 'nicht im PATH'}")
        ok &= bool(found)
    try:
        import pytesseract

        langs = pytesseract.get_languages()
        for lang in cfg.ocr_lang.split("+"):
            has = lang in langs
            print(f"{'OK ' if has else 'FEHLT'} Tesseract-Sprache {lang}")
            ok &= has
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLT Tesseract-Abfrage fehlgeschlagen: {exc}")
        ok = False
    adb = Adb(cfg.adb_serial, cfg.adb_path)
    try:
        connected = adb.ensure_connected()
        print(f"{'OK ' if connected else 'FEHLT'} Geraet {cfg.adb_serial}: {adb.state()}")
        if connected:
            print(f"     Bildschirm an: {adb.is_awake()}")
            state = ocr.analyze(adb.screenshot(), cfg)
            print(f"OK  Screenshot {state.width}x{state.height}")
        else:
            ok = False
    except AdbError as exc:
        print(f"FEHLT ADB: {exc}")
        ok = False

    plan = plan_for(now.date(), cfg)
    print(f"     Heute geplant: Morgenlauf {plan.morning_at:%H:%M}, Abendlauf {plan.evening_at:%H:%M}")
    try:
        store = Store(cfg.data_dir)
        today = store.for_day(now.date())
        last = store.recent(1)
        if last:
            a = last[0]
            print(f"     Letzter Lauf: {a.ts:%Y-%m-%d %H:%M} {a.kind} -> {a.outcome}")
        else:
            print("     Letzter Lauf: noch keiner gespeichert")
        due = next_due(datetime.now(), cfg, today)
        print(f"     Naechster Lauf: {due:%Y-%m-%d %H:%M}" if due else "     Naechster Lauf: keiner im Horizont")
    except Exception as exc:  # noqa: BLE001 - doctor darf an der Datenbank nicht scheitern
        print(f"WARN Datenbank nicht lesbar: {exc}")

    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aliexpress_coin_collector", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--env", default=".env", help="Pfad zur .env-Datei")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("once", help="Einen Lauf sofort ausfuehren")
    p.add_argument("--force", action="store_true", help="auch starten, wenn der Bildschirm an ist")
    p.add_argument("--no-notify", action="store_true", help="keine Discord-Meldung senden")
    p.add_argument("--no-record", action="store_true", help="nichts in die Datenbank schreiben")
    p.set_defaults(func=cmd_once)

    p = sub.add_parser("daemon", help="Dauerbetrieb mit Zeitplan")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("ocr", help="Erkennung auf einem gespeicherten Screenshot testen")
    p.add_argument("image")
    p.add_argument("--text", action="store_true", help="auch den erkannten Volltext ausgeben")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("status", help="Letzte Laeufe und Summen anzeigen")
    p.add_argument("-n", type=int, default=14)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("schedule", help="Geplante Uhrzeiten der naechsten Tage anzeigen")
    p.add_argument("-n", type=int, default=7, help="Anzahl Tage (Standard 7)")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("doctor", help="Installation und Geraeteverbindung pruefen")
    p.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    try:
        cfg = Config.load(args.env)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    _setup_logging(cfg.log_level)
    return args.func(cfg, args)


if __name__ == "__main__":
    raise SystemExit(main())
