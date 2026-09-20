from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime

from . import notify
from .adb import Adb
from .config import Config
from .runner import SUCCESS, Outcome, RunResult, run_once
from .store import Attempt, Store

log = logging.getLogger(__name__)

SUCCESS_VALUES = {o.value for o in SUCCESS}
_SEED = "aliexpress-coins"  # historischer Wert, damit sich die Uhrzeiten pro Datum durch die Umbenennung nicht aendern


@dataclass(frozen=True)
class Plan:
    morning_at: datetime
    evening_at: datetime


@dataclass(frozen=True)
class Decision:
    kind: str  # 'morning' | 'evening'
    force: bool  # trotz eingeschaltetem Bildschirm starten


def _random_between(rng: random.Random, day: date, start: dtime, end: dtime) -> datetime:
    s, e = datetime.combine(day, start), datetime.combine(day, end)
    if e <= s:
        return s
    return s + timedelta(seconds=rng.randint(0, int((e - s).total_seconds())))


def plan_for(day: date, cfg: Config) -> Plan:
    """Zufaellige, aber pro Tag stabile Uhrzeiten (ueberlebt Neustarts des Dienstes)."""
    rng = random.Random(f"{_SEED}-{day.isoformat()}")
    return Plan(
        morning_at=_random_between(rng, day, cfg.morning_start, cfg.morning_end),
        evening_at=_random_between(rng, day, cfg.evening_start, cfg.evening_end),
    )


def next_runs(day: date, cfg: Config, days: int = 7) -> list[tuple[date, Plan]]:
    """Plaene fuer 'days' Tage ab 'day' (einschliesslich)."""
    if days < 1:
        raise ValueError("days muss mindestens 1 sein")
    return [(day + timedelta(days=i), plan_for(day + timedelta(days=i), cfg)) for i in range(days)]


def next_due(now: datetime, cfg: Config, today_attempts: list[Attempt], horizon_days: int = 8) -> datetime | None:
    """Naechster geplanter Lauf, der noch aussteht. None, wenn im Horizont keiner mehr faellig ist.

    Beruecksichtigt fuer heute den Stand aus der Datenbank: nach einem Erfolg steht heute nichts mehr an, und
    eine Art (morning/evening), die bereits einen echten Versuch hatte, wird nicht erneut geplant.
    """
    for offset in range(max(1, horizon_days)):
        day = now.date() + timedelta(days=offset)
        plan = plan_for(day, cfg)
        attempts = today_attempts if offset == 0 else []
        if any(a.outcome in SUCCESS_VALUES for a in attempts):
            continue  # an diesem Tag ist nichts mehr zu tun
        done_kinds = {a.kind for a in attempts if a.outcome != Outcome.BUSY.value}
        for kind, at in (("morning", plan.morning_at), ("evening", plan.evening_at)):
            if at > now and kind not in done_kinds:
                return at
    return None


def decide(now: datetime, plan: Plan, attempts: list[Attempt], cfg: Config) -> Decision | None:
    """Reine Entscheidungslogik: soll jetzt ein Lauf starten, und welcher?"""
    if any(a.outcome in SUCCESS_VALUES for a in attempts):
        return None

    real = [a for a in attempts if a.outcome != Outcome.BUSY.value]  # BUSY zaehlt nicht als Versuch
    busy = [a for a in attempts if a.outcome == Outcome.BUSY.value]
    done_kinds = {a.kind for a in real}

    if now >= plan.evening_at and "evening" not in done_kinds:
        kind, base = "evening", plan.evening_at
    elif plan.morning_at <= now < plan.evening_at and "morning" not in done_kinds:
        kind, base = "morning", plan.morning_at
    else:
        return None

    if busy and now < busy[-1].ts + timedelta(minutes=cfg.busy_retry_min):
        return None
    force = now >= base + timedelta(minutes=cfg.busy_max_wait_min)
    return Decision(kind, force)


def describe(kind: str, result: RunResult) -> str:
    label = {"morning": "Morgenlauf", "evening": "Abendlauf", "manual": "Handlauf"}.get(kind, kind)
    if result.outcome == Outcome.CLAIMED:
        gain = ""
        if result.coins_before is not None and result.coins_after is not None:
            gain = f" ({result.coins_before} -> {result.coins_after}, +{result.coins_after - result.coins_before})"
        return f"✅ {label}: Coins eingesammelt{gain}"
    if result.outcome == Outcome.ALREADY_DONE:
        return f"ℹ️ {label}: heute schon eingecheckt (Stand: {result.coins_after})"
    return f"⚠️ {label} fehlgeschlagen [{result.outcome.value}]: {result.message}"


def handle_result(cfg: Config, store: Store, kind: str, result: RunResult, final_attempt: bool = False) -> None:
    store.add(
        Attempt(
            ts=datetime.now(),
            kind=kind,
            outcome=result.outcome.value,
            coins_before=result.coins_before,
            coins_after=result.coins_after,
            message=result.message,
            duration_s=round(result.duration_s, 1),
        )
    )
    text = describe(kind, result)
    log.info(text)

    if result.outcome == Outcome.BUSY:
        return
    if result.ok:
        wanted = cfg.notify_on_success if result.outcome == Outcome.CLAIMED else cfg.notify_on_already_done
        if wanted:
            notify.send_discord(cfg.discord_webhook, text)
        return

    if result.screenshot:
        shots = cfg.data_dir / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        (shots / f"{datetime.now():%Y%m%d-%H%M%S}-{result.outcome.value}.png").write_bytes(result.screenshot)
        for old in sorted(shots.glob("*.png"))[:-30]:  # nur die letzten 30 behalten
            old.unlink(missing_ok=True)
    if final_attempt:
        text += "\n❌ Das war der letzte Versuch fuer heute."
    notify.send_discord(cfg.discord_webhook, text, result.screenshot)


def daemon(cfg: Config, adb: Adb, store: Store, tick_s: int = 30) -> None:
    log.info(
        "Dienst gestartet. Fenster morgens %s-%s, abends %s-%s",
        cfg.morning_start,
        cfg.morning_end,
        cfg.evening_start,
        cfg.evening_end,
    )
    announced: date | None = None
    while True:
        now = datetime.now()
        plan = plan_for(now.date(), cfg)
        if announced != now.date():
            log.info(
                "Heute: Morgenlauf ab %s, Abendlauf ab %s",
                plan.morning_at.strftime("%H:%M"),
                plan.evening_at.strftime("%H:%M"),
            )
            announced = now.date()

        decision = decide(now, plan, store.for_day(now.date()), cfg)
        if decision:
            log.info("Starte %s%s", decision.kind, " (erzwungen)" if decision.force else "")
            result = run_once(cfg, adb, force=decision.force)
            handle_result(cfg, store, decision.kind, result, final_attempt=decision.kind == "evening")
        time.sleep(tick_s)
