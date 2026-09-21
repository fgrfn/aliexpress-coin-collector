"""Reine Auswertung der Laufhistorie fuer die Weboberflaeche.

Bewusst ohne FastAPI, ohne Datenbank und ohne Dateizugriff: alles hier nimmt eine Liste von
Attempt-Objekten entgegen und gibt Werte zurueck. Dadurch ist die gesamte Auswertung ohne Geraet
und ohne Webserver testbar, genau wie scheduler.decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..runner import SUCCESS, Outcome
from ..store import Attempt

SUCCESS_VALUES = {o.value for o in SUCCESS}

# Aelter als das, gilt der Dienst als nicht laufend. Der Dienst meldet sich alle 30 s.
HEARTBEAT_MAX_AGE = timedelta(minutes=5)


@dataclass(frozen=True)
class DayPoint:
    """Ein Tag im Muenzverlauf."""

    day: date
    coins: int
    gain: int | None  # Tagesgewinn, None wenn er sich nicht bestimmen laesst


def latest(attempts: list[Attempt]) -> Attempt | None:
    """Juengster Lauf. Erwartet keine bestimmte Sortierung."""
    return max(attempts, key=lambda a: a.ts) if attempts else None


def latest_coins(attempts: list[Attempt]) -> int | None:
    """Zuletzt bekannter Muenzstand. Laeufe ohne erkannten Stand werden uebersprungen."""
    for attempt in sorted(attempts, key=lambda a: a.ts, reverse=True):
        if attempt.coins_after is not None:
            return attempt.coins_after
        if attempt.coins_before is not None:
            return attempt.coins_before
    return None


def succeeded_on(attempts: list[Attempt], day: date) -> bool:
    return any(a.ts.date() == day and a.outcome in SUCCESS_VALUES for a in attempts)


def coin_series(attempts: list[Attempt]) -> list[DayPoint]:
    """Ein Punkt je Tag mit bekanntem Muenzstand, aufsteigend nach Datum.

    Je Tag zaehlt der spaeteste Lauf, der ueberhaupt einen Stand erkannt hat. Der Tagesgewinn kommt
    aus coins_after minus coins_before desselben Laufs; fehlt einer der beiden, bleibt er offen.
    """
    by_day: dict[date, Attempt] = {}
    for attempt in sorted(attempts, key=lambda a: a.ts):
        if attempt.coins_after is None and attempt.coins_before is None:
            continue
        by_day[attempt.ts.date()] = attempt

    points: list[DayPoint] = []
    for day in sorted(by_day):
        attempt = by_day[day]
        coins = attempt.coins_after if attempt.coins_after is not None else attempt.coins_before
        gain = None
        if attempt.coins_after is not None and attempt.coins_before is not None:
            gain = attempt.coins_after - attempt.coins_before
        points.append(DayPoint(day=day, coins=int(coins), gain=gain))
    return points


def derive_streak(attempts: list[Attempt], today: date, offset: int = 0) -> int:
    """Aufeinanderfolgende Tage mit Erfolg, rueckwaerts ab heute, plus Startwert.

    Der heutige Tag zaehlt nur mit, wenn er schon erfolgreich war; steht er noch aus, wird ab
    gestern gezaehlt. Ein Tag ganz ohne erfolgreichen Lauf beendet die Serie.

    Das ist bewusst nicht der Wert aus der App: Tage, die vor dem ersten Lauf des Dienstes von Hand
    gesammelt wurden, kennt die Datenbank nicht. Dafuer gibt es den Startwert STREAK_OFFSET.
    """
    successful = {a.ts.date() for a in attempts if a.outcome in SUCCESS_VALUES}
    day = today if today in successful else today - timedelta(days=1)
    streak = 0
    while day in successful:
        streak += 1
        day -= timedelta(days=1)
    return streak + max(0, offset) if streak else max(0, offset)


def service_alive(heartbeat: datetime | None, now: datetime, max_age: timedelta = HEARTBEAT_MAX_AGE) -> bool:
    """Gilt der Dienst als laufend? Ohne Herzschlag lautet die Antwort nein."""
    if heartbeat is None:
        return False
    return now - heartbeat <= max_age


@dataclass(frozen=True)
class ButtonState:
    """Ob der Knopf 'Lauf jetzt starten' benutzbar ist, und warum nicht."""

    enabled: bool
    reason: str = ""


def button_state(
    attempts: list[Attempt],
    today: date,
    alive: bool,
    request_pending: bool,
) -> ButtonState:
    """Die drei vereinbarten Leitplanken, an einer Stelle und ohne Webserver pruefbar."""
    if not alive:
        return ButtonState(False, "Der Dienst läuft nicht, ein Auftrag würde nie abgeholt werden.")
    if request_pending:
        return ButtonState(False, "Es ist bereits ein Lauf angefordert, der Dienst holt ihn gleich ab.")
    if succeeded_on(attempts, today):
        return ButtonState(False, "Heute wurde bereits erfolgreich eingecheckt, ein weiterer Lauf brächte nichts.")
    return ButtonState(True)


def outcome_label(outcome: str) -> str:
    """Kurztext fuer ein Ergebnis, fuer die Anzeige."""
    return {
        Outcome.CLAIMED.value: "eingesammelt",
        Outcome.ALREADY_DONE.value: "schon erledigt",
        Outcome.BUSY.value: "übersprungen",
        Outcome.UNREACHABLE.value: "nicht erreichbar",
        Outcome.NOT_FOUND.value: "nicht erkannt",
        Outcome.UNCONFIRMED.value: "unbestätigt",
        Outcome.ERROR.value: "Fehler",
    }.get(outcome, outcome)


def outcome_kind(outcome: str) -> str:
    """Grobe Einordnung fuer die Einfaerbung: ok, warn oder bad."""
    if outcome in SUCCESS_VALUES:
        return "ok"
    if outcome == Outcome.BUSY.value:
        return "warn"
    return "bad"
