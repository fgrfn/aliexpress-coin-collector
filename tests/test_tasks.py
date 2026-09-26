"""Zusatzaufgaben festhalten und als Tagesliste zeigen.

Bis 0.19.2 stand das Ergebnis eines Ausflugs nur im Log: die Oberflaeche wusste nichts davon,
und am naechsten Tag war es weg. Jetzt bekommt jede gesehene Aufgabe eine Zeile -- auch die
gesperrten, denn ein Tag mit zwei Aufgaben sieht sonst aus wie einer, an dem acht dastanden.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from aliexpress_coin_collector import extras
from aliexpress_coin_collector.runner import Outcome
from aliexpress_coin_collector.store import Attempt, Store, Task
from aliexpress_coin_collector.web import data

JETZT = datetime(2026, 9, 27, 9, 30)


def karte(text: str) -> extras.Card:
    return extras.Card(text=text, go_x=600, go_y=500)


def urteil(text: str, ruling: str = extras.TAKE, reason: str = "erlaubt durch 'stober'") -> extras.Verdict:
    return extras.Verdict(card=karte(text), ruling=ruling, reason=reason)


# -- Vom Ausflug in die Datenbank ----------------------------------------------------------------


def test_to_tasks_nimmt_auch_die_gesperrten_mit() -> None:
    result = extras.ExtrasResult(
        entered=True,
        verdicts=[
            urteil("Super Rabatte anzeigen"),
            urteil("Tagesquiz-Herausforderung", extras.BLOCKED, "gesperrt durch 'quiz'"),
        ],
        runs=[
            extras.TaskRun(text="Super Rabatte anzeigen", ok=True, note="erledigt", coins_before=140, coins_after=145)
        ],
    )

    zeilen = extras.to_tasks(result, JETZT)

    assert [z.text for z in zeilen] == ["Super Rabatte anzeigen", "Tagesquiz-Herausforderung"]
    assert zeilen[0].done and zeilen[0].gain == 5
    assert not zeilen[1].done and zeilen[1].ruling == extras.BLOCKED


def test_to_tasks_ordnet_der_reihe_nach_zu() -> None:
    """`explore` arbeitet die brauchbaren Urteile in ihrer Reihenfolge ab -- und nur die."""
    result = extras.ExtrasResult(
        entered=True,
        verdicts=[
            urteil("Merge-Boss", extras.BLOCKED, "gesperrt durch 'merge'"),
            urteil("Erste Aufgabe"),
            urteil("Zweite Aufgabe"),
        ],
        # Der Ausflug brach nach der ersten ab: nur ein Lauf, und der gehoert zu "Erste Aufgabe".
        runs=[extras.TaskRun(text="Erste Aufgabe", ok=True, note="erledigt")],
    )

    zeilen = extras.to_tasks(result, JETZT)

    assert [z.done for z in zeilen] == [False, True, False]
    assert zeilen[2].note == ""  # gar nicht erst versucht, kein "nicht erledigt" erfinden


def test_to_tasks_ohne_aufgaben() -> None:
    assert extras.to_tasks(extras.ExtrasResult(entered=True), JETZT) == []


# -- Die Datenbank -------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path)


def test_store_schreibt_und_liest_aufgaben(store: Store) -> None:
    store.add_tasks(
        [
            Task(ts=JETZT, text="Super Rabatte anzeigen", ruling=extras.TAKE, done=True, note="erledigt", gain=5),
            Task(ts=JETZT, text="Tagesquiz", ruling=extras.BLOCKED, reason="gesperrt durch 'quiz'"),
        ]
    )

    zeilen = store.tasks_between(datetime(2026, 9, 27, 8, 0), datetime(2026, 9, 28, 8, 0))

    assert [z.text for z in zeilen] == ["Super Rabatte anzeigen", "Tagesquiz"]
    assert zeilen[0].done is True and zeilen[0].gain == 5
    assert zeilen[1].done is False and zeilen[1].gain is None


def test_store_grenzt_den_zeitraum_ab(store: Store) -> None:
    store.add_tasks([Task(ts=datetime(2026, 9, 27, 7, 59), text="gestern", ruling=extras.TAKE)])
    store.add_tasks([Task(ts=datetime(2026, 9, 27, 8, 0), text="heute", ruling=extras.TAKE)])

    zeilen = store.tasks_between(datetime(2026, 9, 27, 8, 0), datetime(2026, 9, 28, 8, 0))

    assert [z.text for z in zeilen] == ["heute"]


def test_store_ohne_aufgaben_schreibt_nichts(store: Store) -> None:
    store.add_tasks([])
    assert store.tasks_between(datetime(2026, 9, 1), datetime(2026, 10, 1)) == []


def test_lesender_store_ohne_tabelle_wirft_nicht(tmp_path: Path) -> None:
    """Die Oberflaeche kann nichts anlegen. Startet sie nach einem Update vor dem Dienst,
    fehlt die Tabelle noch -- dann ist der Tag eben leer, statt die Seite abzuwerfen."""
    import sqlite3

    pfad = tmp_path / "coins.sqlite3"
    sqlite3.connect(pfad).executescript("CREATE TABLE runs (id INTEGER PRIMARY KEY, ts TEXT, day TEXT)")

    nur_lesen = Store(tmp_path, read_only=True)

    assert nur_lesen.tasks_between(datetime(2026, 9, 1), datetime(2026, 10, 1)) == []


def test_lesender_store_darf_nicht_schreiben(store: Store, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        Store(tmp_path, read_only=True).add_tasks([Task(ts=JETZT, text="x", ruling=extras.TAKE)])


# -- Die Tagesliste ------------------------------------------------------------------------------


def erfolg(ts: datetime = JETZT, vorher: int = 140, nachher: int = 152) -> Attempt:
    return Attempt(ts=ts, kind="morning", outcome=Outcome.CLAIMED.value, coins_before=vorher, coins_after=nachher)


def test_checkliste_beginnt_mit_dem_checkin() -> None:
    items = data.checklist([erfolg()], [])

    assert len(items) == 1
    assert items[0].text == "Täglicher Check-in"
    assert items[0].state == data.DONE
    assert items[0].gain == 12


def test_checkin_steht_offen_wenn_noch_nichts_lief() -> None:
    items = data.checklist([], [])

    assert items[0].state == data.OPEN
    assert items[0].detail == "steht noch aus"


def test_gescheiterter_checkin_wird_als_fehler_gezeigt() -> None:
    items = data.checklist([Attempt(ts=JETZT, kind="morning", outcome=Outcome.UNREACHABLE.value)], [])

    assert items[0].state == data.FAILED
    assert items[0].detail == "nicht erreichbar"


def test_checkliste_zeigt_jede_aufgabe_mit_ihrem_zustand() -> None:
    aufgaben = [
        Task(ts=JETZT, text="Super Rabatte anzeigen", ruling=extras.TAKE, done=True, note="erledigt", gain=5),
        Task(ts=JETZT, text="Suchen, was Sie lieben", ruling=extras.TAKE, note="Suchbegriff kam nicht im Feld an"),
        Task(ts=JETZT, text="Tagesquiz", ruling=extras.BLOCKED, reason="gesperrt durch 'quiz'"),
    ]

    items = data.checklist([erfolg()], aufgaben)

    assert [i.state for i in items] == [data.DONE, data.DONE, data.FAILED, data.SKIPPED]
    assert items[2].detail == "Suchbegriff kam nicht im Feld an"
    assert items[3].detail == "gesperrt durch 'quiz'"


def test_zweiter_ausflug_hebt_erledigtes_nicht_wieder_auf() -> None:
    """Morgens erledigt, abends noch einmal nachgesehen -- die Aufgabe bleibt abgehakt."""
    frueh = Task(ts=JETZT, text="Super Rabatte anzeigen", ruling=extras.TAKE, done=True, note="erledigt", gain=5)
    spaet = Task(
        ts=datetime(2026, 9, 27, 20, 0), text="Super Rabatte anzeigen", ruling=extras.TAKE, note="nicht erledigt"
    )

    items = data.checklist([erfolg()], [frueh, spaet])

    assert len(items) == 2
    assert items[1].state == data.DONE


def test_leicht_anders_gelesene_titel_werden_zusammengefasst() -> None:
    """Die Erkennung liest denselben Titel von Mal zu Mal anders. Eine Zeile, nicht zwei."""
    eine = Task(ts=JETZT, text="In kürzlich angesehenen Artikeln stöbern", ruling=extras.TAKE, done=True)
    andere = Task(
        ts=datetime(2026, 9, 27, 20, 0),
        text="In kurzlich angesehenen Artikein stobern",
        ruling=extras.TAKE,
        note="nicht erledigt",
    )

    items = data.checklist([erfolg()], [eine, andere])

    assert len(items) == 2  # Check-in plus die eine Aufgabe


def test_zusammenfassung_zaehlt_gesperrte_fuer_sich() -> None:
    aufgaben = [
        Task(ts=JETZT, text="A", ruling=extras.TAKE, done=True),
        Task(ts=JETZT, text="B", ruling=extras.TAKE),
        Task(ts=JETZT, text="C", ruling=extras.BLOCKED, reason="gesperrt durch 'quiz'"),
        Task(ts=JETZT, text="D", ruling=extras.UNKNOWN, reason="kein Suchbegriff hinterlegt"),
    ]

    summe = data.checklist_summary(data.checklist([erfolg()], aufgaben))

    assert (summe.done, summe.open, summe.skipped) == (2, 1, 2)
    assert summe.total == 5


# -- Der Knopf -----------------------------------------------------------------------------------


def test_extras_knopf_erst_nach_dem_checkin() -> None:
    zustand = data.extras_button_state([], JETZT.date(), alive=True, request_pending=False)

    assert not zustand.enabled
    assert "erst nach dem Check-in" in zustand.reason


def test_extras_knopf_nach_erfolgreichem_checkin() -> None:
    zustand = data.extras_button_state([erfolg()], JETZT.date(), alive=True, request_pending=False)

    assert zustand.enabled


def test_extras_knopf_bleibt_zu_ohne_dienst() -> None:
    assert not data.extras_button_state([erfolg()], JETZT.date(), alive=False, request_pending=False).enabled


def test_extras_knopf_bleibt_zu_bei_offenem_auftrag() -> None:
    zustand = data.extras_button_state([erfolg()], JETZT.date(), alive=True, request_pending=True)

    assert not zustand.enabled
    assert "bereits ein Ausflug angefordert" in zustand.reason


def test_extras_knopf_und_laufknopf_schliessen_einander_aus() -> None:
    """Der eine geht erst, wenn der andere nicht mehr geht -- genau an derselben Bedingung."""
    for attempts in ([], [erfolg()]):
        lauf = data.button_state(attempts, JETZT.date(), alive=True, request_pending=False)
        extra = data.extras_button_state(attempts, JETZT.date(), alive=True, request_pending=False)
        assert lauf.enabled != extra.enabled


# -- Schon abgeholte Aufgaben ---------------------------------------------------------------------
#
# Seit 0.19.6 erkennt die Auswertung am Haken, dass eine Aufgabe schon abgeholt ist. In der
# Tagesliste gehoert sie zu den erledigten, nicht zu den uebersprungenen: uebersprungen klingt
# nach Versaeumnis, und in der App traegt sie einen Haken.


def test_schon_abgeholte_aufgabe_zaehlt_als_erledigt() -> None:
    aufgaben = [
        Task(ts=JETZT, text="Gesponserte Artikel entdecken", ruling=extras.ALREADY, reason="schon abgeholt (2/2)")
    ]

    items = data.checklist([erfolg()], aufgaben)

    assert items[1].state == data.DONE
    assert items[1].detail == "schon abgeholt (2/2)"


def test_schon_abgeholte_zaehlen_in_der_zusammenfassung_mit() -> None:
    aufgaben = [
        Task(ts=JETZT, text="A", ruling=extras.ALREADY, reason="schon abgeholt"),
        Task(ts=JETZT, text="B", ruling=extras.TAKE, done=True),
        Task(ts=JETZT, text="C", ruling=extras.TAKE),
        Task(ts=JETZT, text="D", ruling=extras.BLOCKED, reason="gesperrt durch 'quiz'"),
    ]

    summe = data.checklist_summary(data.checklist([erfolg()], aufgaben))

    assert (summe.done, summe.open, summe.skipped) == (3, 1, 1)
