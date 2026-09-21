"""Die Auswertung: Quote, Zuwachs, Serie, Verteilung, Wochentage.

Reine Funktionen über der Laufdatenbank. Der rote Faden dieser Tests: eine Zahl darf nie
mehr behaupten, als in den Daten steht — ein übersprungener Lauf ist kein Misserfolg, ein
fehlender Münzstand ist kein Nullzuwachs, und ein Tag ohne Lauf bricht eine Serie.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from datetime import time as dtime

import pytest

from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.store import Attempt, Store
from aliexpress_coin_collector.web import auth, charts, data

TODAY = date(2026, 9, 21)
PW = "mein-geheimes-wort"


def run(day_offset=0, hour=8, outcome="claimed", before=None, after=None, kind="morgens"):
    ts = datetime.combine(TODAY - timedelta(days=day_offset), datetime.min.time()) + timedelta(hours=hour)
    return Attempt(ts=ts, kind=kind, outcome=outcome, coins_before=before, coins_after=after, duration_s=20.0)


# -- Zeitraum --------------------------------------------------------------------------------


def test_the_range_falls_back_to_the_default_on_nonsense():
    for raw in ("", "abc", "-1", "365", "7.5", None):
        assert data.parse_range(raw) == data.DEFAULT_RANGE, raw


def test_the_offered_ranges_are_accepted():
    for days, _ in data.RANGES:
        assert data.parse_range(str(days)) == days


def test_zero_means_everything():
    attempts = [run(400), run(0)]
    assert len(data.in_range(attempts, TODAY, 0)) == 2


def test_a_range_includes_today_and_excludes_what_is_older():
    attempts = [run(0), run(6), run(7)]
    kept = data.in_range(attempts, TODAY, 7)
    assert len(kept) == 2  # heute und vor 6 Tagen, nicht der siebte


def test_the_future_is_not_in_the_range():
    # Eine falsch gestellte Uhr soll die Auswertung nicht verzerren.
    assert data.in_range([run(-3)], TODAY, 7) == []


# -- Erfolgsquote ----------------------------------------------------------------------------


def test_the_quota_counts_days_not_runs():
    # Morgens gescheitert, abends geklappt: der Check-in ist eingesammelt, also 100 Prozent.
    attempts = [run(0, 8, "unreachable"), run(0, 19, "claimed")]
    quota = data.success_quota(attempts)
    assert quota.good == 1 and quota.total == 1
    assert quota.percent == 100


def test_already_done_counts_as_success():
    assert data.success_quota([run(0, outcome="already_done")]).percent == 100


def test_a_failed_day_lowers_the_quota():
    attempts = [run(0, outcome="claimed"), run(1, outcome="not_found"), run(2, outcome="claimed")]
    quota = data.success_quota(attempts)
    assert quota.good == 2 and quota.total == 3
    assert quota.percent == 67


def test_without_runs_the_quota_is_zero_not_a_crash():
    quota = data.success_quota([])
    assert quota.percent == 0 and quota.tone == ""


def test_the_quota_gets_a_tone():
    assert data.Quota(good=10, total=10).tone == "ok"
    assert data.Quota(good=8, total=10).tone == "warn"
    assert data.Quota(good=5, total=10).tone == "bad"


# -- Zuwachs ---------------------------------------------------------------------------------


def test_the_gain_needs_both_counts():
    attempts = [
        run(0, before=10, after=17),
        run(1, before=None, after=30),  # kein Vorher
        run(2, before=30, after=None),  # kein Nachher
    ]
    assert data.gains(attempts) == [7]
    assert data.total_gain(attempts) == 7


def test_an_unchanged_count_is_not_a_gain():
    # "schon erledigt" laesst den Stand gleich -- das ist kein Zuwachs von null, sondern keiner.
    assert data.gains([run(0, outcome="already_done", before=100, after=100)]) == []


def test_a_shrinking_count_is_ignored():
    # Faellt der Stand, hat die Erkennung sich verlesen. Das darf die Summe nicht verfaelschen.
    assert data.gains([run(0, before=100, after=40)]) == []


def test_average_and_span():
    attempts = [run(0, before=0, after=5), run(1, before=0, after=9), run(2, before=0, after=7)]
    assert data.average_gain(attempts) == pytest.approx(7.0)
    assert data.gain_span(attempts) == (5, 9)


def test_without_gains_there_is_no_span():
    assert data.gain_span([]) is None
    assert data.average_gain([]) == 0.0


# -- Serie -----------------------------------------------------------------------------------


def test_the_longest_streak_is_found():
    attempts = [run(i) for i in (0, 1, 2)] + [run(i) for i in (6, 7, 8, 9)]
    streak = data.longest_streak(attempts)
    assert streak.days == 4
    assert streak.first == TODAY - timedelta(days=9)
    assert streak.last == TODAY - timedelta(days=6)


def test_a_day_without_a_run_breaks_the_streak():
    # Kein Lauf heisst auch: nichts eingesammelt.
    attempts = [run(0), run(1), run(3), run(4)]
    assert data.longest_streak(attempts).days == 2


def test_a_failed_day_breaks_the_streak():
    attempts = [run(0), run(1, outcome="not_found"), run(2)]
    assert data.longest_streak(attempts).days == 1


def test_several_runs_on_one_day_are_one_day():
    attempts = [run(0, 8), run(0, 19), run(1, 8)]
    assert data.longest_streak(attempts).days == 2


def test_without_success_there_is_no_streak():
    streak = data.longest_streak([run(0, outcome="error")])
    assert streak.days == 0 and streak.first is None


# -- Verteilung ------------------------------------------------------------------------------


def test_the_shares_add_up_and_are_sorted():
    attempts = [run(i, outcome="claimed") for i in range(6)] + [
        run(6, outcome="not_found"),
        run(7, outcome="not_found"),
        run(8, outcome="unreachable"),
    ]
    shares = data.outcome_shares(attempts)
    assert [s.outcome for s in shares] == ["claimed", "not_found", "unreachable"]
    assert shares[0].count == 6
    assert shares[0].percent == 67
    assert shares[0].label == "gesammelt"
    assert shares[0].tone == "ok"


def test_outcomes_that_never_happened_are_absent():
    shares = data.outcome_shares([run(0)])
    assert len(shares) == 1


def test_without_runs_there_are_no_shares():
    assert data.outcome_shares([]) == []


# -- Wochentage ------------------------------------------------------------------------------


def test_every_weekday_appears_even_without_runs():
    days = data.by_weekday([run(0)])
    assert len(days) == 7
    assert [d.name for d in days] == ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    assert sum(d.total for d in days) == 1


def test_weekdays_count_days_not_runs():
    # 21.09.2026 ist ein Montag.
    attempts = [run(0, 8), run(0, 19), run(7, 8)]  # zwei Laeufe an einem Montag, plus Montag davor
    monday = data.by_weekday(attempts)[0]
    assert monday.total == 2
    assert monday.good == 2
    assert monday.percent == 100


def test_a_weekday_without_runs_has_no_percent():
    tuesday = data.by_weekday([run(0)])[1]
    assert tuesday.total == 0 and tuesday.percent == 0


# -- Auffaellige Uhrzeit ---------------------------------------------------------------------


def test_a_repeated_failure_at_the_same_hour_is_pointed_out():
    attempts = [run(i, hour=2, outcome="unreachable") for i in range(4)]
    assert data.busiest_hour(attempts) == (2, 4)


def test_a_single_failure_is_not_a_pattern():
    assert data.busiest_hour([run(0, hour=2, outcome="unreachable")]) is None


def test_skipped_runs_are_not_counted_as_failures():
    # "uebersprungen" heisst: das Geraet war in Benutzung. Kein Fehler.
    assert data.busiest_hour([run(i, hour=2, outcome="busy") for i in range(5)]) is None


def test_successes_are_not_counted_as_failures():
    assert data.busiest_hour([run(i, hour=2) for i in range(5)]) is None


# -- Diagramme -------------------------------------------------------------------------------


def test_the_weekday_chart_survives_an_empty_range():
    assert "<svg" not in charts.weekday_chart(data.by_weekday([]))


def test_the_weekday_chart_draws_one_bar_per_day():
    svg = str(charts.weekday_chart(data.by_weekday([run(i) for i in range(14)])))
    assert svg.startswith('<svg class="chart"')
    assert svg.count("<rect") == 7


def test_the_gain_chart_skips_days_without_a_known_gain():
    # Drei Tage, aber nur zwei mit bekanntem Zuwachs: der mittlere hat kein coins_before.
    series = data.coin_series([run(2, before=0, after=10), run(1, after=20), run(0, before=20, after=27)])
    assert [p.gain for p in series] == [10, None, 7]
    svg = str(charts.gain_chart(series))
    assert svg.startswith('<svg class="chart"')
    assert svg.count("<rect") == 2  # zwei Balken, nicht drei


def test_the_gain_chart_needs_at_least_two_known_gains():
    series = data.coin_series([run(1, after=20), run(0, before=20, after=27)])
    assert "zu wenige" in str(charts.gain_chart(series))


def test_both_charts_are_marked_safe():
    for markup in (charts.weekday_chart(data.by_weekday([run(0)])), charts.gain_chart([])):
        assert hasattr(markup, "__html__")


# -- Die Seite -------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    cfg = Config.load(tmp_path / "keine.env")
    auth.set_password(tmp_path, PW, PW)
    c = fastapi_testclient.TestClient(create_app(cfg), follow_redirects=False)
    c.post("/login", data={"password": PW})
    return c


def seed(tmp_path, attempts):
    store = Store(tmp_path)
    for a in attempts:
        store.add(a)


def today_run(day_offset=0, **kw):
    """Gegen echte 'heute'-Rechnung der Seite, nicht gegen das feste TODAY."""
    ts = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=day_offset)
    return Attempt(
        ts=ts,
        kind="morgens",
        outcome=kw.get("outcome", "claimed"),
        coins_before=kw.get("before"),
        coins_after=kw.get("after"),
        duration_s=20.0,
    )


def test_the_page_is_behind_the_login(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    auth.set_password(tmp_path, PW, PW)
    anonymous = fastapi_testclient.TestClient(create_app(Config.load(tmp_path / "keine.env")), follow_redirects=False)
    assert anonymous.get("/verlauf").headers["location"] == "/login"


def test_an_empty_range_says_so_instead_of_showing_zeros(client):
    body = client.get("/verlauf").text
    assert "noch kein Lauf" in body
    assert "Erfolgsquote" not in body


def test_the_page_shows_the_numbers(client, tmp_path):
    seed(tmp_path, [today_run(i, before=100 + i * 7, after=107 + i * 7) for i in range(10)])
    body = client.get("/verlauf").text
    assert "Erfolgsquote" in body
    assert "100 %" in body
    assert "Längste Serie" in body


def test_the_range_switcher_works(client, tmp_path):
    seed(tmp_path, [today_run(0), today_run(60)])
    assert "2 Läufe" in client.get("/verlauf?tage=0").text
    assert "1 Läufe" in client.get("/verlauf?tage=7").text


def test_a_bad_range_falls_back_instead_of_failing(client, tmp_path):
    seed(tmp_path, [today_run(0)])
    assert client.get("/verlauf?tage=; DROP TABLE runs").status_code == 200


def test_the_sidebar_now_has_four_areas(client):
    body = client.get("/verlauf").text
    for label in ("Übersicht", "Verlauf", "Gerät", "Diagnose"):
        assert f">{label}</span>" in body, label


# -- Filter der Tabelle ----------------------------------------------------------------------


def test_clicking_a_share_filters_only_the_table(client, tmp_path):
    seed(tmp_path, [today_run(0), today_run(1), today_run(2, outcome="not_found")])
    body = client.get("/verlauf?tage=7&ergebnis=not_found").text
    # Die Tabelle zeigt nur den einen Lauf ...
    assert body.count('data-label="Zeit"') == 1
    assert "Läufe: nicht erkannt" in body
    # ... die Kennzahlen bleiben aber am ganzen Zeitraum.
    assert "2 von 3 Tagen" in body


def test_an_unknown_filter_is_ignored_instead_of_emptying_the_table(client, tmp_path):
    seed(tmp_path, [today_run(0), today_run(1)])
    body = client.get("/verlauf?tage=7&ergebnis=gibtsnicht").text
    assert body.count('data-label="Zeit"') == 2
    assert "Alle Läufe" in body


def test_a_filter_for_something_absent_is_ignored(client, tmp_path):
    # "error" kommt im Zeitraum nicht vor -- dann ist der Filter sinnlos und wird verworfen.
    seed(tmp_path, [today_run(0)])
    assert "Alle Läufe" in client.get("/verlauf?tage=7&ergebnis=error").text


def test_the_filter_keeps_the_chosen_range(client, tmp_path):
    seed(tmp_path, [today_run(0, outcome="not_found")])
    body = client.get("/verlauf?tage=90&ergebnis=not_found").text
    assert 'href="/verlauf?tage=90#laeufe"' in body


# -- Umschaltpunkt des AliExpress-Tages ----------------------------------------------------------
#
# Der Tag springt nicht um Mitternacht Ortszeit um. Liegt das Abendfenster dahinter, sammelt der
# Nachholversuch schon den naechsten Tag -- der verpasste bleibt verpasst.


def rollover_pair(day, evening_hour=20, next_outcome="already_done"):
    """Ein Abendlauf sammelt, am naechsten Morgen ist es schon erledigt."""
    return [
        Attempt(ts=datetime.combine(day, dtime(evening_hour, 0)), kind="evening", outcome="claimed"),
        Attempt(ts=datetime.combine(day + timedelta(days=1), dtime(8, 0)), kind="morning", outcome=next_outcome),
    ]


def test_without_evening_runs_there_is_nothing_to_see():
    daily = [
        Attempt(ts=datetime.combine(TODAY - timedelta(days=i), dtime(8, 0)), kind="morning", outcome="claimed")
        for i in range(10)
    ]
    assert data.rollover_hints(daily).found is False


def test_an_evening_run_that_grabbed_the_next_day_is_flagged():
    found = data.rollover_hints(rollover_pair(TODAY))
    assert found.found is True
    assert found.hits == 1
    assert found.before == dtime(20, 0)


def test_an_evening_run_followed_by_a_normal_claim_is_fine():
    # Der Abendlauf hat den verpassten Tag nachgeholt, so wie er soll.
    assert data.rollover_hints(rollover_pair(TODAY, next_outcome="claimed")).found is False


def test_the_earliest_observation_bounds_the_rollover():
    # Sammelte schon ein Lauf um 19:00 den naechsten Tag, liegt der Umschaltpunkt davor.
    attempts = rollover_pair(TODAY, evening_hour=21) + rollover_pair(TODAY - timedelta(days=5), evening_hour=19)
    found = data.rollover_hints(attempts)
    assert found.hits == 2
    assert found.before == dtime(19, 0)


def test_a_failed_evening_run_proves_nothing():
    attempts = [
        Attempt(ts=datetime.combine(TODAY, dtime(20, 0)), kind="evening", outcome="unreachable"),
        Attempt(ts=datetime.combine(TODAY + timedelta(days=1), dtime(8, 0)), kind="morning", outcome="already_done"),
    ]
    assert data.rollover_hints(attempts).found is False


def test_the_hint_shows_up_on_the_history_page(client, tmp_path):
    seed(tmp_path, rollover_pair(datetime.now().date() - timedelta(days=3)))
    body = client.get("/verlauf?tage=0").text
    assert "Abendfenster liegt hinter dem Tageswechsel" in body


def test_the_hint_stays_away_without_evidence(client, tmp_path):
    seed(tmp_path, [Attempt(ts=datetime.now(), kind="morning", outcome="claimed")])
    assert "Abendfenster liegt hinter dem Tageswechsel" not in client.get("/verlauf?tage=0").text
