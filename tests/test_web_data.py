from datetime import date, datetime, timedelta

from aliexpress_coin_collector.store import Attempt
from aliexpress_coin_collector.web import data, render

DAY = date(2026, 9, 21)


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


# -- Darstellung -----------------------------------------------------------------------------


def test_chart_needs_at_least_two_points():
    assert "zu wenige Daten" in render.coin_chart(data.coin_series([att(0, 8, after=10)]))
    assert "<svg" not in render.coin_chart([])


def test_chart_draws_a_polyline_for_real_data():
    svg = render.coin_chart(data.coin_series([att(0, 8, after=10), att(1, 8, after=25), att(2, 8, after=45)]))
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count("<polyline") == 1
    assert "45" in svg  # Hoechstwert wird beschriftet


def test_chart_survives_a_flat_line():
    # Gleicher Stand an allen Tagen: darf nicht durch null teilen.
    svg = render.coin_chart(data.coin_series([att(0, 8, after=10), att(1, 8, after=10)]))
    assert "<polyline" in svg


def test_history_table_escapes_messages():
    attempts = [att(0, 8, outcome="error")]
    evil = Attempt(ts=attempts[0].ts, kind="manual", outcome="error", message="<script>alert(1)</script>")
    html = render.history_table([evil], shots={})
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_history_table_links_a_screenshot_when_one_exists():
    a = att(0, 8, outcome="not_found")
    shots = {a.ts.strftime("%Y%m%d-%H%M%S"): "20260921-080000-not_found.png"}
    assert "/shot/20260921-080000-not_found.png" in render.history_table([a], shots)


def test_empty_history_says_so():
    assert "Noch keine" in render.history_table([], shots={})
