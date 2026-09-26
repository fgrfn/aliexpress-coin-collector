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


# -- Stillstandswaechter auf der Startseite ------------------------------------------------------


def stall_days(coins=10, count=4):
    """Mehrere Tage 'schon erledigt' bei unveraendertem Muenzstand."""
    return [run(i, outcome="already_done", after=coins) for i in range(count)]


def test_a_stall_is_announced_on_the_front_page(client, tmp_path):
    # Gehoert auf die Startseite, nicht in eine Unterseite: wer hier nichts sieht, sieht es nie.
    seed(tmp_path, stall_days())
    body = client.get("/").text
    assert "Erfolg gemeldet, aber nichts gesammelt" in body


def test_a_healthy_history_shows_no_banner(client, tmp_path):
    seed(tmp_path, [run(3, after=10), run(2, after=25), run(1, after=40), run(0, after=55)])
    assert "Erfolg gemeldet, aber nichts gesammelt" not in client.get("/").text


def test_static_files_carry_the_version_against_stale_caches(client):
    """Sonst behaelt der Browser nach einem Update das alte app.css."""
    from aliexpress_coin_collector import __version__

    body = client.get("/").text
    assert f"/static/app.css?v={__version__}" in body
    assert client.get(f"/static/app.css?v={__version__}").status_code == 200


def test_both_copies_of_the_mark_have_their_own_gradient_ids(client):
    """Das Zeichen steht zweimal in der Seite: Kopfzeile schmal, Seitenleiste breit.

    Bei gleichen IDs gewinnt die erste, und die liegt in der gerade ausgeblendeten
    Haelfte -- dann fehlt in der Seitenleiste der orange Schwung.
    """
    body = client.get("/").text
    for stamp in ("bar", "nav"):
        assert f'id="{stamp}Hot"' in body
        assert f"url(#{stamp}Hot)" in body
        assert f'id="{stamp}Gold"' in body
    assert 'id="mkHot"' not in body


# -- Die Tagesliste auf der Startseite --------------------------------------------------------


def seed_tasks(tmp_path, tasks):
    Store(tmp_path).add_tasks(tasks)


def heute(hour=9):
    return datetime.now().replace(hour=hour, minute=30, second=0, microsecond=0)


def test_dashboard_zeigt_die_tagesliste(client, tmp_path):
    from aliexpress_coin_collector import extras
    from aliexpress_coin_collector.store import Task

    seed(tmp_path, [run(0, hour=9, before=140, after=152)])
    seed_tasks(
        tmp_path,
        [
            Task(ts=heute(), text="Super Rabatte anzeigen", ruling=extras.TAKE, done=True, note="erledigt", gain=5),
            Task(ts=heute(), text="Tagesquiz", ruling=extras.BLOCKED, reason="gesperrt durch 'quiz'"),
        ],
    )

    body = client.get("/").text

    assert "Heute gesammelt" in body
    assert "Täglicher Check-in" in body
    assert "Super Rabatte anzeigen" in body
    assert "Tagesquiz" in body
    assert "gesperrt durch &#39;quiz&#39;" in body  # maskiert, wie alles aus der Datenbank


def test_dashboard_zeigt_die_tagesliste_auch_ohne_zusatzaufgaben(client, tmp_path):
    seed(tmp_path, [run(0, hour=9, before=140, after=152)])

    body = client.get("/").text

    assert "Heute gesammelt" in body
    assert "1 von 1 erledigt" in body


def test_dashboard_hat_beide_knoepfe(client, tmp_path):
    seed(tmp_path, [run(0, hour=9, before=140, after=152)])

    body = client.get("/").text

    assert 'action="/run"' in body
    assert 'action="/extras"' in body


def test_extras_knopf_legt_einen_auftrag_ab(client, tmp_path, monkeypatch):
    from aliexpress_coin_collector import commands
    from aliexpress_coin_collector.web import app as web_app

    seed(tmp_path, [run(0, hour=9, before=140, after=152)])
    # Ohne Lebenszeichen des Dienstes waere der Knopf gesperrt.
    monkeypatch.setattr(web_app.data, "service_alive", lambda *a, **k: True)

    assert client.post("/extras").status_code == 303

    offen = commands.pending(tmp_path)
    assert [c.name for c in offen] == [commands.EXTRAS]


def test_ohne_checkin_wird_kein_extras_auftrag_angenommen(client, tmp_path, monkeypatch):
    """Serverseitig geprueft: ein deaktivierter Knopf im Browser ist keine Absicherung."""
    from aliexpress_coin_collector import commands
    from aliexpress_coin_collector.web import app as web_app

    monkeypatch.setattr(web_app.data, "service_alive", lambda *a, **k: True)

    assert client.post("/extras").status_code == 303
    assert commands.pending(tmp_path) == []
