"""Was die Vorlagen tatsaechlich ausgeben.

Diese Tests gibt es, weil beim Umbau auf Vorlagen zwei Fehler entstanden sind, die kein
Unit-Test gesehen haette: das selbst gebaute Diagramm wurde von der automatischen Maskierung
mitmaskiert und landete als sichtbarer Text auf der Seite, und das Datum kam auf Englisch.
Beides wird hier festgehalten.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.store import Attempt, Store
from aliexpress_coin_collector.web import auth, charts, data, view

PW = "mein-geheimes-wort"


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


def run(day_offset=0, hour=8, outcome="claimed", before=None, after=None, message="", kind="morgens"):
    ts = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(days=day_offset)
    return Attempt(
        ts=ts,
        kind=kind,
        outcome=outcome,
        coins_before=before,
        coins_after=after,
        duration_s=21.0,
        message=message,
    )


# -- Das Diagramm darf nicht maskiert werden -------------------------------------------------


def test_chart_is_marked_safe_so_jinja_does_not_escape_it():
    points = data.coin_series([run(2, after=10), run(1, after=20), run(0, after=30)])
    svg = charts.coin_chart(points)
    assert hasattr(svg, "__html__"), "ohne Markup schreibt Jinja das SVG als Text auf die Seite"


def test_the_hint_for_too_little_data_is_also_marked_safe():
    assert hasattr(charts.coin_chart([]), "__html__")


def test_dashboard_shows_a_real_svg_not_escaped_text(client, tmp_path):
    seed(tmp_path, [run(2, after=10), run(1, after=20), run(0, after=30)])
    body = client.get("/").text
    assert '<svg class="chart"' in body
    assert "&lt;svg" not in body


# -- Deutsch, unabhaengig von der Locale des Containers --------------------------------------


def test_weekdays_and_months_are_german():
    assert view.german_date(date(2026, 9, 21)) == "Montag, 21. September"
    assert view.german_date(date(2026, 3, 1)) == "Sonntag, 1. März"
    assert view.german_date(date(2026, 12, 31)) == "Donnerstag, 31. Dezember"


def test_dashboard_has_no_english_weekday(client, tmp_path):
    seed(tmp_path, [run(0, after=30)])
    body = client.get("/").text
    for english in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
        assert english not in body


def test_numbers_get_a_german_thousands_separator():
    assert view.german_number(1267) == "1.267"
    assert view.german_number(999) == "999"
    assert view.german_number(1234567) == "1.234.567"
    assert view.german_number(None) == "–"  # kein erkannter Stand


# -- Maskierung von allem, was aus der Datenbank kommt ---------------------------------------


def test_a_message_from_the_database_cannot_inject_markup(client, tmp_path):
    seed(tmp_path, [run(0, outcome="error", message="<script>alert(1)</script>")])
    body = client.get("/").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_outcome_is_called_gesammelt(client, tmp_path):
    seed(tmp_path, [run(0, outcome="claimed", before=10, after=17)])
    body = client.get("/").text
    assert ">gesammelt<" in body
    assert "eingesammelt" not in body


# -- Geruest ---------------------------------------------------------------------------------


def test_every_page_ships_its_own_assets_and_no_foreign_ones(client, tmp_path):
    seed(tmp_path, [run(0, after=30)])
    for path in ("/", "/login"):
        body = client.get(path, follow_redirects=True).text
        assert "googleapis.com" not in body, "keine fremden Quellen"
        assert "cdn." not in body
    assert "/static/htmx.min.js" in client.get("/").text


def test_static_files_are_served(client):
    for path, kind in (
        ("/static/app.css", "text/css"),
        ("/static/htmx.min.js", "javascript"),
        ("/static/icon.svg", "image/svg+xml"),
        ("/static/fonts/space-grotesk-latin.woff2", "font/woff2"),
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert kind in response.headers["content-type"], path


def test_the_partials_refresh_themselves(client, tmp_path):
    seed(tmp_path, [run(0, after=30)])
    tiles = client.get("/teile/status").text
    assert 'hx-trigger="every 5s"' in tiles and 'hx-get="/teile/status"' in tiles
    service = client.get("/teile/dienst").text
    assert 'hx-get="/teile/dienst"' in service


def test_the_partials_are_behind_the_login(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    cfg = Config.load(tmp_path / "keine.env")
    auth.set_password(tmp_path, PW, PW)
    anonymous = fastapi_testclient.TestClient(create_app(cfg), follow_redirects=False)
    for path in ("/teile/status", "/teile/dienst"):
        assert anonymous.get(path).headers["location"] == "/login", path


# -- Aussehen umschalten ---------------------------------------------------------------------


def test_theme_follows_the_system_until_someone_switches(client, tmp_path):
    seed(tmp_path, [run(0, after=30)])
    assert "data-theme" not in client.get("/").text

    client.post("/theme", data={"to": "dark"})
    assert 'data-theme="dark"' in client.get("/").text

    client.post("/theme", data={"to": "light"})
    assert 'data-theme="light"' in client.get("/").text

    client.post("/theme", data={"to": "quatsch"})
    assert "data-theme" not in client.get("/").text


def test_theme_cannot_be_used_to_send_someone_elsewhere(client):
    # Der Referer steuert das Ziel. Eine fremde Adresse darf daraus nicht werden.
    response = client.post("/theme", data={"to": "dark"}, headers={"referer": "https://example.invalid/x"})
    assert response.headers["location"] == "/"
