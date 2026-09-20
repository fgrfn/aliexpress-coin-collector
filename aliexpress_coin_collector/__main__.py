from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from . import __version__, ocr
from .adb import Adb, AdbError
from .config import Config, ConfigError
from .runner import run_once
from .scheduler import daemon, describe, handle_result
from .store import Store


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)-7s %(message)s")


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
    daemon(cfg, Adb(cfg.adb_serial, cfg.adb_path), Store(cfg.data_dir))
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


def cmd_doctor(cfg: Config, args: argparse.Namespace) -> int:
    ok = True
    print(f"aliexpress-coin-collector {__version__}")
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
