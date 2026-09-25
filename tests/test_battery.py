"""Auslesen des Akkus: Ausgabe von 'dumpsys battery' in Zahlen uebersetzen.

Geprueft wird an echten Ausgaben mehrerer Android-Versionen. Ein Geraet braucht es dafuer
nicht -- der Parser ist eine reine Funktion, genau dafuer ist er getrennt.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from aliexpress_coin_collector.adb import AdbError, Battery, parse_battery
from aliexpress_coin_collector.runner import read_battery
from aliexpress_coin_collector.scheduler import (
    BatteryWatch,
    battery_message,
    battery_ok_message,
    battery_problems,
)

# So sieht es auf einem Geraet am Netzteil aus (Android 9).
AM_STROM = """Current Battery Service state:
  AC powered: true
  USB powered: false
  Wireless powered: false
  Max charging current: 1500000
  Max charging voltage: 5000000
  Charge counter: 2461000
  status: 2
  health: 2
  present: true
  level: 100
  scale: 100
  voltage: 4350
  temperature: 320
  technology: Li-ion
"""

OHNE_STROM = """Current Battery Service state:
  AC powered: false
  USB powered: false
  Wireless powered: false
  status: 3
  health: 2
  present: true
  level: 64
  scale: 100
  voltage: 3812
  temperature: 287
  technology: Li-ion
"""


def test_reads_every_field_from_a_charging_device():
    b = parse_battery(AM_STROM)
    assert b.level == 100
    assert b.voltage_mv == 4350
    assert b.plugged is True
    assert b.charging is True
    assert b.healthy is True


def test_tenths_of_a_degree_become_degrees():
    assert parse_battery(AM_STROM).temperature_c == 32.0
    assert parse_battery(OHNE_STROM).temperature_c == 28.7


def test_a_device_off_the_charger_is_not_plugged():
    b = parse_battery(OHNE_STROM)
    assert b.plugged is False
    assert b.charging is False
    assert b.status_label == "entlädt"


def test_usb_alone_counts_as_plugged():
    assert parse_battery("  USB powered: true\n  level: 50\n").plugged is True


def test_missing_fields_stay_none_instead_of_guessing():
    b = parse_battery("Current Battery Service state:\n  level: 41\n")
    assert b.level == 41
    assert b.status is None
    assert b.temperature_c is None
    assert b.status_label == "unbekannt"


def test_nothing_readable_is_recognisable_as_such():
    assert parse_battery("").empty is True
    assert parse_battery(AM_STROM).empty is False


def test_a_reported_defect_is_the_only_thing_that_counts_as_unhealthy():
    """Ein Geraet, das den Wert nicht liefert, darf nicht jeden Tag eine Warnung ausloesen."""
    assert Battery(health=None).healthy is True
    assert Battery(health=1).healthy is True
    assert Battery(health=2).healthy is True
    assert Battery(health=3).healthy is False
    assert Battery(health=4).healthy is False
    assert Battery(health=3).health_label == "überhitzt"


def test_a_label_for_an_unknown_number_does_not_crash():
    assert Battery(status=99, health=99).status_label == "unbekannt"
    assert Battery(status=99, health=99).health_label == "unbekannt"


# --- Der Akku am Lauf --------------------------------------------------------


class FakeAdb:
    def __init__(self, reading=None, boom=None):
        self.reading, self.boom, self.calls = reading, boom, 0

    def battery(self):
        self.calls += 1
        if self.boom:
            raise self.boom
        return self.reading


def test_a_run_picks_the_reading_up():
    adb = FakeAdb(parse_battery(AM_STROM))
    assert read_battery(adb).level == 100


def test_an_unreadable_battery_never_disturbs_the_run():
    """Der Akkustand ist Beiwerk. Ein Fehler dabei darf den Check-in nicht kosten."""
    assert read_battery(FakeAdb(boom=AdbError("weg"))) is None
    assert read_battery(FakeAdb(boom=RuntimeError("kaputt"))) is None


def test_a_device_that_says_nothing_counts_as_no_reading():
    assert read_battery(FakeAdb(parse_battery(""))) is None


# --- Der Waechter ------------------------------------------------------------
JETZT = datetime(2026, 9, 22, 12, 0)
GUT = Battery(level=80, status=2, health=2, temperature_c=30.0, plugged=True)


def test_a_healthy_battery_on_the_charger_has_no_problems():
    assert battery_problems(GUT, 25, 40) == set()


def test_no_power_is_a_problem_even_at_a_full_charge():
    assert battery_problems(replace(GUT, level=100, plugged=False, status=3), 25, 40) == {"power"}


def test_a_charger_that_delivers_nothing_is_caught_too():
    """Am Kabel, aber der Stand faellt: schwaches Netzteil, defektes Kabel."""
    assert "power" in battery_problems(replace(GUT, plugged=True, status=3), 25, 40)


def test_the_thresholds_are_the_ones_passed_in():
    niedrig = replace(GUT, level=25)
    assert "low" in battery_problems(niedrig, 25, 40)
    assert "low" not in battery_problems(niedrig, 20, 40)
    warm = replace(GUT, temperature_c=40.0)
    assert "hot" in battery_problems(warm, 25, 40)
    assert "hot" not in battery_problems(warm, 25, 45)


def test_a_reported_defect_is_a_problem():
    assert "health" in battery_problems(replace(GUT, health=3), 25, 40)


def test_values_the_device_does_not_report_are_not_invented():
    """Ein Geraet ohne Temperaturangabe ist nicht zu warm."""
    assert battery_problems(Battery(level=80, status=2, plugged=True), 25, 40) == set()


def test_each_problem_is_reported_once():
    watch = BatteryWatch()
    kaputt = replace(GUT, plugged=False, status=3)
    fresh, cleared = watch.note(kaputt, JETZT, 25, 40)
    assert fresh == {"power"} and not cleared
    fresh, cleared = watch.note(kaputt, JETZT + timedelta(minutes=15), 25, 40)
    assert fresh == set() and not cleared


def test_a_second_problem_is_reported_on_its_own():
    watch = BatteryWatch()
    watch.note(replace(GUT, plugged=False, status=3), JETZT, 25, 40)
    fresh, _ = watch.note(replace(GUT, plugged=False, status=3, level=10), JETZT, 25, 40)
    assert fresh == {"low"}


def test_the_all_clear_comes_only_after_a_warning():
    watch = BatteryWatch()
    assert watch.note(GUT, JETZT, 25, 40) == (set(), False)
    watch.note(replace(GUT, plugged=False, status=3), JETZT, 25, 40)
    assert watch.note(GUT, JETZT, 25, 40) == (set(), True)
    # und nicht noch einmal
    assert watch.note(GUT, JETZT, 25, 40) == (set(), False)


def test_the_reading_is_asked_for_only_at_the_set_interval():
    watch = BatteryWatch()
    assert watch.due(JETZT, 15) is True
    watch.note(GUT, JETZT, 25, 40)
    assert watch.due(JETZT + timedelta(minutes=14), 15) is False
    assert watch.due(JETZT + timedelta(minutes=15), 15) is True


def test_an_interval_of_zero_switches_the_polling_off():
    assert BatteryWatch().due(JETZT, 0) is False


def test_the_message_names_every_problem_and_stays_red_without_power():
    m = battery_message({"power", "low"}, Battery(level=12, status=3, temperature_c=29.5))
    assert m.tone == "bad"
    assert "Strom" in m.description and "Ladestand" in m.description
    assert "12 %" in m.as_text()
    # Deutsches Dezimalkomma, wie ueberall sonst in der Oberflaeche.
    assert "29,5 °C" in m.as_text()


def test_a_mere_warning_stays_yellow():
    assert battery_message({"hot"}, GUT).tone == "warn"


def test_the_all_clear_is_green():
    assert battery_ok_message(GUT).tone == "ok"


# --- Der frische Wert in der Oberflaeche -------------------------------------


def test_the_tile_prefers_the_live_reading_over_the_database():
    """Der Dienst misst oefter als ein Lauf stattfindet -- sonst haengt die Kachel einen Tag hinterher."""
    from datetime import datetime as dt

    from aliexpress_coin_collector.commands import Status
    from aliexpress_coin_collector.store import Attempt
    from aliexpress_coin_collector.web import view

    alt = [
        Attempt(
            ts=dt(2026, 9, 24, 8, 20),
            kind="morning",
            outcome="claimed",
            battery_level=100,
            battery_temp_c=30.0,
            battery_status="lädt",
        )
    ]
    live = Status(
        written_at=dt(2026, 9, 25, 14, 0),
        version="0.15.1",
        battery_level=41,
        battery_temp_c=33.5,
        battery_status="entlädt",
        battery_at=dt(2026, 9, 25, 13, 55),
    )
    kachel = view.battery_tile(alt, 25, 40, live)
    assert kachel.level == "41 %"
    assert "entlädt" in kachel.sub
    assert "25.09. 13:55" in kachel.sub


def test_without_a_live_reading_the_last_run_still_counts():
    """Steht der Dienst oder ist die Messung abgeschaltet, ist der alte Wert besser als keiner."""
    from datetime import datetime as dt

    from aliexpress_coin_collector.commands import Status
    from aliexpress_coin_collector.store import Attempt
    from aliexpress_coin_collector.web import view

    alt = [Attempt(ts=dt(2026, 9, 24, 8, 20), kind="morning", outcome="claimed", battery_level=100)]
    leer = Status(written_at=dt(2026, 9, 25, 14, 0), version="0.15.1")
    assert view.battery_tile(alt, 25, 40, leer).level == "100 %"
    assert view.battery_tile(alt, 25, 40, None).level == "100 %"
    assert view.battery_tile([], 25, 40, leer) is None


def test_the_live_reading_is_coloured_by_the_same_thresholds():
    from datetime import datetime as dt

    from aliexpress_coin_collector.commands import Status
    from aliexpress_coin_collector.web import view

    def tile(level, temp=30.0):
        live = Status(written_at=dt(2026, 9, 25, 14, 0), version="x", battery_level=level, battery_temp_c=temp)
        return view.battery_tile([], 25, 40, live)

    assert tile(80).tone == ""
    assert tile(20).tone == "bad"
    assert tile(80, temp=41.0).tone == "warn"


def test_the_service_reports_the_battery_to_the_interface(tmp_path):
    """Die Zustandsdatei ist der Weg vom Dienst zur Seite -- ohne ihn bliebe die Kachel alt."""
    from aliexpress_coin_collector import commands

    commands.write_status(
        tmp_path,
        "0.15.1",
        "device",
        False,
        battery_level=64,
        battery_temp_c=29.5,
        battery_status="entlädt",
        battery_at=JETZT,
    )
    zurueck = commands.read_status(tmp_path)
    assert zurueck.battery_level == 64
    assert zurueck.battery_temp_c == 29.5
    assert zurueck.battery_status == "entlädt"
    assert zurueck.battery_at == JETZT


def test_an_older_status_file_without_the_battery_still_reads(tmp_path):
    """Nach einem Update kann die Datei noch vom alten Dienst stammen."""
    import json

    from aliexpress_coin_collector import commands

    (tmp_path / commands.STATUS_FILE).write_text(
        json.dumps({"written_at": JETZT.isoformat(), "version": "0.13.1", "device_state": "device"}),
        encoding="utf-8",
    )
    zurueck = commands.read_status(tmp_path)
    assert zurueck is not None
    assert zurueck.battery_level is None
    assert zurueck.battery_status == ""
