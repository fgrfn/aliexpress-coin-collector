"""Der Muenztag beginnt nicht um Mitternacht.

Gemeldet am 26.09.2026: nachts um vier wurde von Hand ein Lauf gestartet. Zu der Zeit war der
Check-in des Vortags laengst erledigt und die neuen Muenzen lagen noch nicht bereit -- die Seite
meldete "heute schon eingecheckt", und das stimmte auch, nur eben fuer gestern. Verbucht wurde
es als Erfolg des neuen Tages, und damit fiel der echte Lauf des Tages aus.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path

import pytest

from aliexpress_coin_collector.runner import Outcome
from aliexpress_coin_collector.scheduler import (
    attempts_for_plan_day,
    attempts_of_coin_day,
    coin_day,
    coin_day_bounds,
    decide,
    plan_for,
)
from aliexpress_coin_collector.store import Attempt, Store

ACHT = dtime(8, 0)


# -- Die reine Rechnung --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wann", "erwartet"),
    [
        (datetime(2026, 9, 27, 4, 0), date(2026, 9, 26)),  # der gemeldete Fall
        (datetime(2026, 9, 27, 0, 1), date(2026, 9, 26)),
        (datetime(2026, 9, 27, 7, 59), date(2026, 9, 26)),
        (datetime(2026, 9, 27, 8, 0), date(2026, 9, 27)),  # Punkt acht gehoert schon zum neuen Tag
        (datetime(2026, 9, 27, 9, 30), date(2026, 9, 27)),
        (datetime(2026, 9, 27, 23, 59), date(2026, 9, 27)),
    ],
)
def test_coin_day(wann: datetime, erwartet: date) -> None:
    assert coin_day(wann, ACHT) == erwartet


def test_mitternacht_als_beginn_ist_der_kalendertag() -> None:
    """Wer den Beginn auf Mitternacht setzt, bekommt das alte Verhalten zurueck."""
    assert coin_day(datetime(2026, 9, 27, 4, 0), dtime(0, 0)) == date(2026, 9, 27)


def test_bounds_umfassen_genau_einen_tag() -> None:
    von, bis = coin_day_bounds(date(2026, 9, 26), ACHT)
    assert von == datetime(2026, 9, 26, 8, 0)
    assert bis == datetime(2026, 9, 27, 8, 0)


# -- Der gemeldete Fall, mit Datenbank -----------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def test_nachtlauf_blockiert_den_tag_nicht(cfg, store: Store) -> None:
    """Der Kern der Meldung: der Lauf um vier zaehlt zu gestern, heute steht noch alles aus."""
    cfg = replace(cfg, coin_day_start=ACHT)
    store.add(Attempt(ts=datetime(2026, 9, 27, 4, 0), kind="manual", outcome=Outcome.ALREADY_DONE.value))

    # Um vier gehoert er zu gestern -- und gestern ist damit erledigt.
    assert attempts_of_coin_day(store, datetime(2026, 9, 27, 4, 5), cfg) != []
    # Nach dem Beginn des Muenztags zaehlt er nicht mehr mit.
    assert attempts_of_coin_day(store, datetime(2026, 9, 27, 10, 0), cfg) == []


def test_morgenlauf_findet_trotz_nachtlauf_statt(cfg, store: Store) -> None:
    cfg = replace(cfg, coin_day_start=ACHT, morning_start=dtime(9, 0), morning_end=dtime(11, 0))
    store.add(Attempt(ts=datetime(2026, 9, 27, 4, 0), kind="manual", outcome=Outcome.ALREADY_DONE.value))

    plan = plan_for(date(2026, 9, 27), cfg)
    jetzt = plan.morning_at
    entscheidung = decide(jetzt, plan, attempts_of_coin_day(store, jetzt, cfg), cfg)

    assert entscheidung is not None and entscheidung.kind == "morning"


def test_mit_mitternacht_faellt_der_tag_weiterhin_aus(cfg, store: Store) -> None:
    """Zum Vergleich: so verhielt es sich vorher, und so verhaelt es sich mit COIN_DAY_START=00:00."""
    cfg = replace(cfg, coin_day_start=dtime(0, 0), morning_start=dtime(9, 0), morning_end=dtime(11, 0))
    store.add(Attempt(ts=datetime(2026, 9, 27, 4, 0), kind="manual", outcome=Outcome.ALREADY_DONE.value))

    plan = plan_for(date(2026, 9, 27), cfg)
    jetzt = plan.morning_at
    assert decide(jetzt, plan, attempts_of_coin_day(store, jetzt, cfg), cfg) is None


def test_erfolg_im_muenztag_blockiert_weiter(cfg, store: Store) -> None:
    """Ein echter Erfolg nach dem Beginn des Muenztags soll sehr wohl blockieren."""
    cfg = replace(cfg, coin_day_start=ACHT, morning_start=dtime(9, 0), morning_end=dtime(11, 0))
    store.add(Attempt(ts=datetime(2026, 9, 27, 9, 5), kind="morning", outcome=Outcome.CLAIMED.value))

    plan = plan_for(date(2026, 9, 27), cfg)
    jetzt = datetime(2026, 9, 27, 20, 0)
    assert decide(jetzt, plan, attempts_of_coin_day(store, jetzt, cfg), cfg) is None


# -- Anzeige: was steht heute noch an ------------------------------------------------------------


def test_vor_dem_beginn_zaehlt_fuer_heute_noch_nichts(cfg, store: Store) -> None:
    cfg = replace(cfg, coin_day_start=ACHT)
    store.add(Attempt(ts=datetime(2026, 9, 27, 4, 0), kind="manual", outcome=Outcome.ALREADY_DONE.value))

    # Um 4 Uhr: der heutige Plan hat noch keinen Versuch.
    assert attempts_for_plan_day(store, datetime(2026, 9, 27, 4, 30), cfg) == []
    # Nach dem Beginn ebenso -- der Nachtlauf gehoerte zu gestern.
    assert attempts_for_plan_day(store, datetime(2026, 9, 27, 12, 0), cfg) == []


def test_nach_dem_beginn_zaehlt_der_lauf_des_tages(cfg, store: Store) -> None:
    cfg = replace(cfg, coin_day_start=ACHT)
    store.add(Attempt(ts=datetime(2026, 9, 27, 9, 5), kind="morning", outcome=Outcome.CLAIMED.value))

    assert len(attempts_for_plan_day(store, datetime(2026, 9, 27, 12, 0), cfg)) == 1
