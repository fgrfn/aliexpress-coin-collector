from datetime import date, datetime, timedelta

from aliexpress_coin_collector.store import Attempt
from aliexpress_coin_collector.web import charts, data, view

DAY = date(2026, 9, 21)
DAY_START = datetime.combine(DAY, datetime.min.time())


def att(day_offset=0, hour=8, outcome="claimed", before=None, after=None, kind="morning"):
    return Attempt(
        ts=datetime.combine(DAY + timedelta(days=day_offset), datetime.min.time()) + timedelta(hours=hour),
        kind=kind,
        outcome=outcome,
        coins_before=before,
        coins_after=after,
    )


# -- Muenzstand und Verlauf ------------------------------------------------------------------


def test_latest_coins_takes_the_newest_known_value():
    attempts = [att(0, 8, after=10), att(1, 8, after=25), att(2, 8, outcome="not_found")]
    assert data.latest_coins(attempts) == 25  # der not_found-Lauf ohne Stand wird uebersprungen


def test_latest_coins_falls_back_to_before():
    assert data.latest_coins([att(0, 8, outcome="unconfirmed", before=40)]) == 40


def test_latest_coins_is_none_without_any_reading():
    assert data.latest_coins([att(0, 8, outcome="unreachable")]) is None
    assert data.latest_coins([]) is None


def test_coin_series_one_point_per_day_with_gain():
    points = data.coin_series([att(0, 8, before=10, after=25), att(1, 8, before=25, after=45)])
    assert [(p.day, p.coins, p.gain) for p in points] == [
        (DAY, 25, 15),
        (DAY + timedelta(days=1), 45, 20),
    ]


def test_coin_series_prefers_the_latest_run_of_a_day():
    attempts = [att(0, 8, outcome="not_found", before=10), att(0, 20, before=10, after=25)]
    points = data.coin_series(attempts)
    assert len(points) == 1 and points[0].coins == 25


def test_coin_series_leaves_gain_open_when_only_one_side_is_known():
    points = data.coin_series([att(0, 8, after=25), att(1, 8, after=45)])
    assert points[0].gain is None


def test_coin_series_skips_runs_without_any_reading():
    assert data.coin_series([att(0, 8, outcome="unreachable")]) == []


# -- Streak ----------------------------------------------------------------------------------


def test_streak_counts_consecutive_successful_days():
    attempts = [att(0), att(1), att(2)]
    assert data.derive_streak(attempts, DAY + timedelta(days=2)) == 3


def test_streak_ignores_today_while_it_is_still_pending():
    # Gestern und vorgestern erfolgreich, heute steht der Lauf noch aus: die Serie bleibt bei 2.
    attempts = [att(0), att(1)]
    assert data.derive_streak(attempts, DAY + timedelta(days=2)) == 2


def test_streak_breaks_on_a_day_without_success():
    attempts = [att(0), att(1, outcome="not_found"), att(2)]
    assert data.derive_streak(attempts, DAY + timedelta(days=2)) == 1


def test_streak_adds_the_offset_from_the_env():
    assert data.derive_streak([att(0)], DAY, offset=59) == 60


def test_streak_without_any_run_is_just_the_offset():
    assert data.derive_streak([], DAY, offset=59) == 59
    assert data.derive_streak([], DAY) == 0


# -- Herzschlag und Knopf --------------------------------------------------------------------


def test_service_is_alive_only_with_a_recent_heartbeat():
    now = datetime(2026, 9, 21, 12, 0)
    assert data.service_alive(now - timedelta(seconds=40), now)
    assert not data.service_alive(now - timedelta(minutes=30), now)
    assert not data.service_alive(None, now)


def test_button_is_blocked_while_the_service_is_down():
    state = data.button_state([], DAY, alive=False, request_pending=False)
    assert not state.enabled and "läuft nicht" in state.reason


def test_button_is_blocked_after_a_success_today():
    state = data.button_state([att(0, 8, outcome="already_done")], DAY, alive=True, request_pending=False)
    assert not state.enabled and "bereits erfolgreich" in state.reason


def test_button_is_blocked_while_a_request_is_pending():
    state = data.button_state([], DAY, alive=True, request_pending=True)
    assert not state.enabled and "bereits ein Lauf angefordert" in state.reason


def test_button_is_enabled_after_a_failed_run():
    state = data.button_state([att(0, 8, outcome="not_found")], DAY, alive=True, request_pending=False)
    assert state.enabled and state.reason == ""


# -- Diagramm --------------------------------------------------------------------------------


def test_chart_needs_at_least_two_points():
    assert "zu wenige Daten" in charts.coin_chart(data.coin_series([att(0, 8, after=10)]))
    assert "<svg" not in charts.coin_chart([])


def test_chart_draws_a_polyline_for_real_data():
    svg = charts.coin_chart(data.coin_series([att(0, 8, after=10), att(1, 8, after=25), att(2, 8, after=45)]))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<polyline") == 1
    assert "45" in svg  # Hoechstwert wird beschriftet


def test_chart_survives_a_flat_line():
    # Gleicher Stand an allen Tagen: darf nicht durch null teilen.
    svg = charts.coin_chart(data.coin_series([att(0, 8, after=10), att(1, 8, after=10)]))
    assert "<polyline" in svg


def test_chart_scales_only_itself():
    # Die Klasse traegt die Skalierung. Ohne sie wuerde eine Regel fuer alle svg auch das
    # Logo in der Kopfzeile aufblasen -- genau der Fehler, der in 0.3.0 steckte.
    svg = charts.coin_chart(data.coin_series([att(0, 8, after=10), att(1, 8, after=25)]))
    assert svg.startswith('<svg class="chart"')


# -- Aufbereitung der Historie ---------------------------------------------------------------


def test_rows_are_newest_first():
    rows = view.rows([att(0, 8, after=10), att(2, 8, after=30), att(1, 8, after=20)], shots={})
    assert [r.when for r in rows] == ["23.09. 08:00", "22.09. 08:00", "21.09. 08:00"]


def test_rows_show_a_gain_as_an_arrow():
    rows = view.rows([att(0, 8, before=10, after=17)], shots={})
    assert rows[0].coins == "10 → 17"


def test_rows_fall_back_to_a_dash_without_a_known_count():
    assert view.rows([att(0, 8, outcome="not_found")], shots={})[0].coins == "–"


def test_rows_carry_the_screenshot_name():
    a = att(0, 8, outcome="not_found")
    shots = {a.ts.strftime("%Y%m%d-%H%M%S"): "20260921-080000-not_found.png"}
    assert view.rows([a], shots)[0].shot == "20260921-080000-not_found.png"
    assert view.rows([a], {})[0].shot is None


def test_rows_pass_the_message_through_unescaped():
    # Maskiert wird beim Rendern durch Jinja, nicht hier -- sonst waere es doppelt maskiert.
    evil = Attempt(ts=DAY_START, kind="manual", outcome="error", message="<script>alert(1)</script>")
    assert view.rows([evil], shots={})[0].message == "<script>alert(1)</script>"


# -- Zeitangaben -----------------------------------------------------------------------------


def test_relative_reads_as_hours_and_minutes():
    now = datetime(2026, 9, 21, 8, 0)
    assert view.relative(datetime(2026, 9, 21, 8, 45), now) == "in 45 min"
    assert view.relative(datetime(2026, 9, 21, 10, 15), now) == "in 2 h 15 min"
    assert view.relative(datetime(2026, 9, 21, 7, 0), now) == "in 0 min"  # nie negativ


def test_heartbeat_text_says_something_even_without_a_file():
    now = datetime(2026, 9, 21, 8, 0)
    assert view.heartbeat_text(None, now) == "kein Takt seit dem Start"
    assert view.heartbeat_text(datetime(2026, 9, 21, 7, 59, 48), now) == "letzter Takt vor 12 s"
    assert view.heartbeat_text(datetime(2026, 9, 21, 7, 50), now) == "letzter Takt vor 10 min"
    assert view.heartbeat_text(datetime(2026, 9, 21, 4, 0), now) == "letzter Takt vor 4 h"
