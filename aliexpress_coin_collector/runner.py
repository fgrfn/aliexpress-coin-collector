from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from . import ocr
from .adb import Adb, AdbError
from .config import Config

log = logging.getLogger(__name__)


class Outcome(str, Enum):
    CLAIMED = "claimed"  # Check-in eingesammelt und bestaetigt
    ALREADY_DONE = "already_done"  # Seite zeigte schon den erledigt-Zustand
    BUSY = "busy"  # Geraet in Benutzung, Lauf uebersprungen
    UNREACHABLE = "unreachable"  # ADB-Verbindung fehlt
    NOT_FOUND = "not_found"  # weder Button noch erledigt-Zustand erkannt
    UNCONFIRMED = "unconfirmed"  # getippt, aber Erfolg nicht bestaetigt
    ERROR = "error"  # unerwarteter Fehler


SUCCESS = {Outcome.CLAIMED, Outcome.ALREADY_DONE}


@dataclass
class RunResult:
    outcome: Outcome
    message: str
    coins_before: int | None = None
    coins_after: int | None = None
    screenshot: bytes | None = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.outcome in SUCCESS


Analyzer = Callable[[bytes, Config], ocr.PageState]


def _wait_for_page(adb: Adb, cfg: Config, analyze: Analyzer, sleep: Callable[[float], None]):
    """Wartet, bis Button oder erledigt-Zustand sichtbar ist. Gibt (Zustand|None, letzter Screenshot) zurueck."""
    deadline = time.monotonic() + cfg.page_timeout_s
    png = b""
    while True:
        png = adb.screenshot()
        state = analyze(png, cfg)
        log.debug("Seite: %s", state.describe())
        if state.width > state.height:
            log.warning("Screenshot ist im Querformat (%dx%d), Erkennung koennte scheitern", state.width, state.height)
        if state.button or state.done:
            return state, png
        if time.monotonic() >= deadline:
            return None, png
        sleep(3)


def _confirm_claim(adb: Adb, cfg: Config, analyze: Analyzer, before: ocr.PageState, sleep, timeout_s: int = 15):
    deadline = time.monotonic() + timeout_s
    state, png = None, b""
    while True:
        png = adb.screenshot()
        state = analyze(png, cfg)
        log.debug("Nach Tap: %s", state.describe())
        gained = state.coins is not None and before.coins is not None and state.coins > before.coins
        if state.button is None and (state.done or gained):
            return True, state, png
        if time.monotonic() >= deadline:
            return False, state, png
        sleep(2)


def run_once(
    cfg: Config,
    adb: Adb,
    analyze: Analyzer = ocr.analyze,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    confirm_timeout_s: int | None = None,
) -> RunResult:
    rng = rng or random.Random()
    confirm_timeout_s = cfg.confirm_timeout_s if confirm_timeout_s is None else confirm_timeout_s
    t0 = time.monotonic()

    def result(outcome: Outcome, message: str, **kw) -> RunResult:
        return RunResult(outcome, message, duration_s=time.monotonic() - t0, **kw)

    woke = False
    try:
        if not adb.ensure_connected():
            return result(
                Outcome.UNREACHABLE,
                f"Geraet {cfg.adb_serial} nicht erreichbar. Nach einem Neustart per USB neu setzen: `adb tcpip 5555`.",
            )

        awake = adb.is_awake()
        if awake and cfg.skip_if_awake and not force:
            return result(Outcome.BUSY, "Bildschirm ist an, Geraet vermutlich in Benutzung")

        if not awake:
            adb.wake()
            woke = True
            sleep(2)

        attempts = 1 + max(0, cfg.launch_retries)
        state, png = None, b""
        for n in range(1, attempts + 1):
            adb.force_stop(cfg.app_package)
            sleep(1)
            adb.start_url(cfg.coin_url, cfg.app_package)
            sleep(4)
            state, png = _wait_for_page(adb, cfg, analyze, sleep)
            if state is not None:
                break
            log.warning("Coin-Seite nicht erkannt (Startversuch %d/%d)", n, attempts)
        if state is None:
            return result(
                Outcome.NOT_FOUND,
                "Weder Check-in-Button noch erledigt-Zustand erkannt (Login abgelaufen? Popup? Layout geaendert?)",
                screenshot=png,
            )

        if state.button is None:
            return result(
                Outcome.ALREADY_DONE, "Heute bereits eingecheckt", coins_before=state.coins, coins_after=state.coins
            )

        x = state.button.cx + rng.randint(-8, 8)
        y = state.button.cy + rng.randint(-5, 5)
        log.info("Tippe auf '%s' bei (%d, %d), Muenzstand vorher: %s", state.button.text, x, y, state.coins)
        adb.tap(x, y)
        sleep(3)

        ok, after, png2 = _confirm_claim(adb, cfg, analyze, state, sleep, confirm_timeout_s)
        if ok:
            return result(
                Outcome.CLAIMED,
                "Check-in eingesammelt",
                coins_before=state.coins,
                coins_after=after.coins,
                screenshot=png2,
            )
        why = (
            "Button nach dem Tap weiter sichtbar"
            if after.button
            else "Button weg, aber weder Haken-Marker noch hoeherer Muenzstand erkannt"
        )
        return result(
            Outcome.UNCONFIRMED,
            f"Getippt, aber nicht bestaetigt: {why}",
            coins_before=state.coins,
            coins_after=after.coins,
            screenshot=png2,
        )

    except AdbError as exc:
        return result(Outcome.ERROR, f"ADB-Fehler: {exc}")
    except Exception as exc:  # noqa: BLE001 - ein Lauf darf den Daemon nie beenden
        log.exception("Unerwarteter Fehler im Lauf")
        return result(Outcome.ERROR, f"Unerwarteter Fehler: {exc!r}")
    finally:
        if woke:
            try:
                adb.sleep_screen()
            except AdbError:
                log.warning("Bildschirm konnte nicht wieder ausgeschaltet werden")
