"""Reine Auswertung der Laufhistorie fuer die Weboberflaeche.

Bewusst ohne FastAPI, ohne Datenbank und ohne Dateizugriff: alles hier nimmt eine Liste von
Attempt-Objekten entgegen und gibt Werte zurueck. Dadurch ist die gesamte Auswertung ohne Geraet
und ohne Webserver testbar, genau wie scheduler.decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime

from ..runner import Outcome
from ..stats import (
    SUCCESS_VALUES,
    Quota,
    Stall,
    gains,
    in_range,
    latest_coins,
    stalled_since,
    success_quota,
    total_gain,
)
from ..store import Attempt

# Die Rechenregeln stehen in stats.py, weil der Dienst sie fuer den Wochenrueckblick ebenfalls
# braucht. Hier weitergereicht, damit Vorlagen und Tests sie unveraendert ueber data.* finden.
__all__ = [
    "SUCCESS_VALUES",
    "Quota",
    "Stall",
    "gains",
    "in_range",
    "latest_coins",
    "stalled_since",
    "success_quota",
    "total_gain",
]

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


@dataclass(frozen=True)
class BatteryPoint:
    """Ein Tag im Akkuverlauf."""

    day: date
    level: int
    temp_c: float | None


def latest_battery(attempts: list[Attempt]) -> Attempt | None:
    """Juengster Lauf, bei dem ueberhaupt ein Ladestand abgelesen wurde.

    Nicht einfach der juengste Lauf: war das Geraet nicht erreichbar, steht dort nichts, und
    dann soll die Kachel den letzten bekannten Wert zeigen statt eines Strichs.
    """
    known = [a for a in attempts if a.battery_level is not None]
    return max(known, key=lambda a: a.ts) if known else None


def battery_series(attempts: list[Attempt]) -> list[BatteryPoint]:
    """Ein Punkt je Tag, aufsteigend nach Datum. Je Tag zaehlt der spaeteste Messwert."""
    by_day: dict[date, Attempt] = {}
    for attempt in sorted(attempts, key=lambda a: a.ts):
        if attempt.battery_level is not None:
            by_day[attempt.ts.date()] = attempt
    return [
        BatteryPoint(day=day, level=int(by_day[day].battery_level), temp_c=by_day[day].battery_temp_c)
        for day in sorted(by_day)
    ]


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
        Outcome.CLAIMED.value: "gesammelt",
        Outcome.ALREADY_DONE.value: "schon erledigt",
        Outcome.BUSY.value: "übersprungen",
        Outcome.UNREACHABLE.value: "nicht erreichbar",
        Outcome.LOGIN_REQUIRED.value: "Anmeldung nötig",
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


# ---------------------------------------------------------------------------- Auswertung
#
# Alles hier ist rein lesend und rechnet nur mit dem, was in der Datenbank steht. Ein Lauf,
# den das Geraet uebersprungen hat, ist weder Erfolg noch Misserfolg -- er wird als eigene
# Kategorie gefuehrt, statt die Quote zu verzerren.

RANGES = ((7, "7 Tage"), (30, "30 Tage"), (90, "90 Tage"), (0, "alles"))
DEFAULT_RANGE = 30
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def parse_range(raw: str) -> int:
    """Zeitraum aus der Adresszeile. 0 heisst alles, Unbrauchbares faellt auf den Standard."""
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RANGE
    return days if any(days == d for d, _ in RANGES) else DEFAULT_RANGE


def average_gain(attempts: list[Attempt]) -> float:
    values = gains(attempts)
    return sum(values) / len(values) if values else 0.0


def gain_span(attempts: list[Attempt]) -> tuple[int, int] | None:
    values = gains(attempts)
    return (min(values), max(values)) if values else None


@dataclass(frozen=True)
class Streak:
    days: int
    first: date | None = None
    last: date | None = None


def longest_streak(attempts: list[Attempt]) -> Streak:
    """Laengste ununterbrochene Folge von Tagen mit Erfolg.

    Eine Luecke bricht die Serie -- auch ein Tag ganz ohne Lauf, denn dann wurde auch nichts
    eingesammelt.
    """
    days = sorted({a.ts.date() for a in attempts if a.outcome in SUCCESS_VALUES})
    if not days:
        return Streak(0)
    best = run = 1
    best_end = run_start = best_start = days[0]
    for previous, current in zip(days, days[1:], strict=False):
        if current - previous == timedelta(days=1):
            run += 1
        else:
            run, run_start = 1, current
        if run > best:
            best, best_start, best_end = run, run_start, current
    return Streak(best, best_start, best_end)


@dataclass(frozen=True)
class Share:
    """Ein Ergebnis mit seinem Anteil an allen Laeufen."""

    outcome: str
    label: str
    tone: str
    count: int
    percent: int


def outcome_shares(attempts: list[Attempt]) -> list[Share]:
    """Wie die Laeufe ausgingen, haeufigstes zuerst. Ergebnisse ohne Vorkommen fehlen."""
    total = len(attempts)
    if not total:
        return []
    counts: dict[str, int] = {}
    for a in attempts:
        counts[a.outcome] = counts.get(a.outcome, 0) + 1
    shares = [
        Share(
            outcome=outcome,
            label=outcome_label(outcome),
            tone=outcome_kind(outcome),
            count=count,
            percent=round(100 * count / total),
        )
        for outcome, count in counts.items()
    ]
    return sorted(shares, key=lambda s: (-s.count, s.label))


@dataclass(frozen=True)
class DayShare:
    """Ein Wochentag: an wie vielen davon hat es geklappt."""

    name: str
    good: int
    total: int

    @property
    def percent(self) -> int:
        return round(100 * self.good / self.total) if self.total else 0


def by_weekday(attempts: list[Attempt]) -> list[DayShare]:
    """Erfolge je Wochentag, Montag zuerst. Gezaehlt werden Tage, nicht Laeufe."""
    good: dict[int, set[date]] = {i: set() for i in range(7)}
    seen: dict[int, set[date]] = {i: set() for i in range(7)}
    for a in attempts:
        day = a.ts.date()
        seen[day.weekday()].add(day)
        if a.outcome in SUCCESS_VALUES:
            good[day.weekday()].add(day)
    return [DayShare(name=WEEKDAYS[i], good=len(good[i]), total=len(seen[i])) for i in range(7)]


def busiest_hour(attempts: list[Attempt]) -> tuple[int, int] | None:
    """Stunde mit den meisten Fehlschlaegen, und wie viele.

    Haeuft sich "nicht erreichbar" zu einer festen Uhrzeit, liegt das selten am Geraet --
    eher an etwas, das regelmaessig dazwischenfunkt, etwa einem naechtlichen Backup.
    """
    bad = [a for a in attempts if a.outcome not in SUCCESS_VALUES and a.outcome != Outcome.BUSY.value]
    if not bad:
        return None
    counts: dict[int, int] = {}
    for a in bad:
        counts[a.ts.hour] = counts.get(a.ts.hour, 0) + 1
    hour = max(counts, key=lambda h: (counts[h], -h))
    return (hour, counts[hour]) if counts[hour] >= 2 else None


@dataclass(frozen=True)
class Rollover:
    """Hinweise darauf, dass das Abendfenster hinter dem Umschaltpunkt des AliExpress-Tages liegt."""

    hits: int
    before: dtime | None  # der Umschaltpunkt liegt vor dieser Uhrzeit

    @property
    def found(self) -> bool:
        return self.hits > 0 and self.before is not None


def rollover_hints(attempts: list[Attempt]) -> Rollover:
    """Sucht Abendlaeufe, die schon den naechsten Tag eingesammelt haben.

    Der AliExpress-Tag springt nicht um Mitternacht Ortszeit um. Liegt das Abendfenster dahinter,
    holt der Nachholversuch nicht den verpassten Tag nach, sondern bereits den naechsten -- der
    Fehltag bleibt ein Fehltag, und die Serie reisst trotzdem.

    Belegt ist das durch ein Paar: ein Abendlauf sammelt, und am naechsten Morgen ist es bereits
    erledigt. Die frueheste solche Uhrzeit begrenzt den Umschaltpunkt nach oben.

    Ein Hinweis, kein Beweis: wer abends von Hand am Telefon sammelt, erzeugt dasselbe Muster.
    Und ohne Abendlaeufe in der Historie gibt es hier nichts zu sehen -- die laufen nur, wenn
    morgens etwas schiefging.
    """
    by_day: dict[date, list[Attempt]] = {}
    for a in attempts:
        by_day.setdefault(a.ts.date(), []).append(a)

    hits, before = 0, None
    for day, runs in by_day.items():
        evening = [a for a in runs if a.kind == "evening" and a.outcome == Outcome.CLAIMED.value]
        if not evening:
            continue
        following = sorted(by_day.get(day + timedelta(days=1), []), key=lambda a: a.ts)
        if not following or following[0].outcome != Outcome.ALREADY_DONE.value:
            continue
        hits += 1
        earliest = min(a.ts.time() for a in evening)
        before = earliest if before is None or earliest < before else before
    return Rollover(hits=hits, before=before)
