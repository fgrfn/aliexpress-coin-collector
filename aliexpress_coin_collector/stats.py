"""Die Rechenregeln ueber der Laufhistorie.

Hier steht das, was sowohl die Weboberflaeche als auch der Dienst braucht: was als Erfolg zaehlt,
was als Zuwachs, und wie daraus eine Quote wird. Bewusst ein eigenes Modul und keine Kopie an
zwei Stellen -- an genau diesen Regeln laufen Anzeige und Meldung sonst auseinander.

Ohne FastAPI, ohne Datenbank, ohne Dateizugriff: alles nimmt eine Liste von Attempt-Objekten
entgegen und gibt Werte zurueck. Damit ist es ohne Geraet und ohne Webserver pruefbar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .runner import SUCCESS
from .store import Attempt

SUCCESS_VALUES = {o.value for o in SUCCESS}


def latest_coins(attempts: list[Attempt]) -> int | None:
    """Zuletzt bekannter Muenzstand. Laeufe ohne erkannten Stand werden uebersprungen."""
    for attempt in sorted(attempts, key=lambda a: a.ts, reverse=True):
        if attempt.coins_after is not None:
            return attempt.coins_after
        if attempt.coins_before is not None:
            return attempt.coins_before
    return None


def in_range(attempts: list[Attempt], today: date, days: int) -> list[Attempt]:
    """Laeufe der letzten 'days' Tage, heute eingeschlossen. 0 heisst alles."""
    if days <= 0:
        return list(attempts)
    first = today - timedelta(days=days - 1)
    return [a for a in attempts if first <= a.ts.date() <= today]


@dataclass(frozen=True)
class Quota:
    """Erfolgsquote, bezogen auf Tage statt auf Laeufe.

    Tage sind das ehrlichere Mass: an einem Tag mit einem gescheiterten Morgenlauf und einem
    erfolgreichen Abendlauf ist der Check-in eingesammelt. Nach Laeufen gezaehlt waere das
    50 Prozent, nach Tagen 100 -- und nur Letzteres beantwortet die Frage "hat es geklappt".
    """

    good: int
    total: int

    @property
    def percent(self) -> int:
        return round(100 * self.good / self.total) if self.total else 0

    @property
    def tone(self) -> str:
        if not self.total:
            return ""
        return "ok" if self.percent >= 90 else "warn" if self.percent >= 70 else "bad"


def success_quota(attempts: list[Attempt]) -> Quota:
    days = {a.ts.date() for a in attempts}
    good = {a.ts.date() for a in attempts if a.outcome in SUCCESS_VALUES}
    return Quota(good=len(good), total=len(days))


def gains(attempts: list[Attempt]) -> list[int]:
    """Zuwachs je Lauf, nur wo beide Staende bekannt sind und es wirklich mehr wurde.

    Ein fehlender Stand ist kein Zuwachs von null, und ein fallender Stand heisst, dass die
    Erkennung sich verlesen hat -- beides wird verworfen statt behauptet.
    """
    out = []
    for a in attempts:
        if a.coins_before is None or a.coins_after is None:
            continue
        delta = a.coins_after - a.coins_before
        if delta > 0:
            out.append(delta)
    return out


def total_gain(attempts: list[Attempt]) -> int:
    return sum(gains(attempts))
