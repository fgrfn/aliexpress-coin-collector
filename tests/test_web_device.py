"""Die Geräteseite: was sie zeigt, was sie ablegt und was sie ablehnt.

Der Schwerpunkt liegt auf dem, was die Oberfläche NICHT darf: das Gerät selbst anfassen,
einen Befehl durchreichen, sich selbst stoppen, oder die vollständige Geräteadresse zeigen.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aliexpress_coin_collector import commands
from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.web import auth, view

PW = "mein-geheimes-wort"
SERIAL = "10.10.30.169:5555"


@pytest.fixture
def client(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", SERIAL)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    cfg = Config.load(tmp_path / "keine.env")
    auth.set_password(tmp_path, PW, PW)
    c = fastapi_testclient.TestClient(create_app(cfg), follow_redirects=False)
    c.post("/login", data={"password": PW})
    return c


def alive(tmp_path):
    (tmp_path / "heartbeat").write_text("")


# -- Anzeige ---------------------------------------------------------------------------------


def test_the_page_is_behind_the_login(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", SERIAL)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    auth.set_password(tmp_path, PW, PW)
    anonymous = fastapi_testclient.TestClient(create_app(Config.load(tmp_path / "keine.env")), follow_redirects=False)
    for path in ("/geraet", "/teile/geraet", "/teile/auftraege"):
        assert anonymous.get(path).headers["location"] == "/login", path
    for path, payload in (
        ("/geraet/befehl", {"name": "reconnect"}),
        ("/geraet/dienst", {"verb": "restart", "target": "collector"}),
    ):
        assert anonymous.post(path, data=payload).headers["location"] == "/login", path


def test_without_a_report_the_state_is_unknown_not_invented(client):
    body = client.get("/geraet").text
    assert "unbekannt" in body
    assert "nicht erreichbar" not in body


def test_the_reported_state_is_shown(client, tmp_path):
    commands.write_status(tmp_path, "0.4.0", "unauthorized", None, datetime.now())
    body = client.get("/geraet").text
    assert "nicht freigegeben" in body
    assert "Immer zulassen" in body  # der Hinweis, was jetzt zu tun ist


def test_the_full_device_address_never_reaches_the_page(client, tmp_path):
    commands.write_status(tmp_path, "0.4.0", "device", False, datetime.now())
    body = client.get("/geraet").text
    assert SERIAL not in body
    assert "10.10.30.169" not in body
    assert "10.10.30.xxx:5555" in body


def test_an_older_daemon_is_pointed_out(client, tmp_path):
    commands.write_status(tmp_path, "0.1.0", "device", False, datetime.now())
    body = client.get("/geraet").text
    assert "0.1.0" in body
    assert "systemctl restart" in body


def test_a_matching_version_says_nothing(client, tmp_path):
    from aliexpress_coin_collector import __version__

    commands.write_status(tmp_path, __version__, "device", False, datetime.now())
    assert "läuft noch in Version" not in client.get("/geraet").text


# -- Aufträge --------------------------------------------------------------------------------


def test_a_command_is_filed_not_executed(client, tmp_path):
    response = client.post("/geraet/befehl", data={"name": "reconnect"})
    assert response.status_code == 303
    assert response.headers["location"] == "/geraet"
    waiting = commands.pending(tmp_path)
    assert [c.name for c in waiting] == ["reconnect"]


def test_an_unknown_command_is_refused(client, tmp_path):
    client.post("/geraet/befehl", data={"name": "shell rm -rf /"})
    assert commands.pending(tmp_path) == []


def test_the_queue_shows_what_waits(client, tmp_path):
    client.post("/geraet/befehl", data={"name": "screenshot"})
    body = client.get("/teile/auftraege").text
    assert "Screenshot holen" in body
    assert "wartet" in body


def test_the_run_button_goes_through_the_same_queue(client, tmp_path):
    alive(tmp_path)
    client.post("/run")
    assert [c.name for c in commands.pending(tmp_path)] == ["run"]
    assert not (tmp_path / "run-requested").exists()


def test_the_run_button_locks_while_one_is_queued(client, tmp_path):
    alive(tmp_path)
    client.post("/run")
    client.post("/run")
    assert len(commands.pending(tmp_path)) == 1
    assert "bereits ein Lauf angefordert" in client.get("/").text


def test_an_old_request_file_still_locks_the_button(client, tmp_path):
    # Nach einem Update kann eine Datei der Vorgaengerversion liegen bleiben.
    alive(tmp_path)
    (tmp_path / "run-requested").write_text("2026-09-21T08:00:00\n")
    assert "bereits ein Lauf angefordert" in client.get("/").text


# -- Dienststeuerung -------------------------------------------------------------------------


def test_a_service_action_writes_two_checked_words(client, tmp_path):
    response = client.post("/geraet/dienst", data={"verb": "restart", "target": "collector"})
    assert response.status_code == 303
    lines = (tmp_path / "control").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "restart collector"


def test_an_unknown_verb_or_target_writes_nothing(client, tmp_path):
    for payload in (
        {"verb": "rm", "target": "collector"},
        {"verb": "restart", "target": "; rm -rf /"},
        {"verb": "restart && curl evil", "target": "web"},
        {"verb": "", "target": ""},
    ):
        client.post("/geraet/dienst", data=payload)
        assert not (tmp_path / "control").exists(), payload


def test_the_web_service_may_not_stop_itself(client, tmp_path):
    client.post("/geraet/dienst", data={"verb": "stop", "target": "web"})
    assert not (tmp_path / "control").exists()


def test_the_control_file_changes_on_a_repeated_request(client, tmp_path):
    # Die Pfadeinheit reagiert auf eine Aenderung. Zweimal derselbe Inhalt waere unsichtbar.
    client.post("/geraet/dienst", data={"verb": "restart", "target": "collector"})
    first = (tmp_path / "control").read_text(encoding="utf-8")
    client.post("/geraet/dienst", data={"verb": "restart", "target": "collector"})
    second = (tmp_path / "control").read_text(encoding="utf-8")
    assert first.splitlines()[0] == second.splitlines()[0]
    assert len(second.splitlines()) > 1  # der Zeitstempel sorgt fuer eine echte Aenderung


def test_the_stop_button_only_appears_while_the_service_runs(client, tmp_path):
    assert "Starten" in client.get("/geraet").text
    alive(tmp_path)
    body = client.get("/geraet").text
    assert "Neustart" in body and "Stoppen" in body


# -- Aufbereitung ohne Webserver -------------------------------------------------------------


def test_the_device_view_does_not_guess_without_a_report():
    device = view.device_view(None, SERIAL, datetime.now())
    assert device.state == "unbekannt"
    assert device.reported == "noch nie"
    assert SERIAL not in device.serial


def test_ago_reads_naturally():
    now = datetime(2026, 9, 21, 8, 0)
    assert view.ago(None, now) == "noch nie"
    assert view.ago(now - timedelta(seconds=12), now) == "vor 12 s"
    assert view.ago(now - timedelta(minutes=10), now) == "vor 10 min"
    assert view.ago(now - timedelta(hours=5), now) == "vor 5 h"
    assert view.ago(now - timedelta(days=3), now) == "vor 3 Tagen"


def test_the_web_service_has_no_stop_button_in_the_view():
    for service in view.services(True, datetime.now(), datetime.now()):
        if service.target == "web":
            assert [v for v, _ in service.verbs] == ["restart"]


def test_a_message_with_umlauts_survives_the_cookie(client, tmp_path):
    # Ein Cookie ist eine HTTP-Kopfzeile und kann nur latin-1. Die Meldungen enthalten aber
    # deutsche Anfuehrungszeichen -- unkodiert wirft das Senden eine Ausnahme.
    response = client.post("/geraet/befehl", data={"name": "reconnect"})
    assert response.status_code == 303
    body = client.get("/geraet").text
    assert "Neu verbinden" in body
    assert "abgelegt" in body


def test_the_message_is_gone_on_the_next_view(client, tmp_path):
    client.post("/geraet/befehl", data={"name": "reconnect"})
    assert "abgelegt" in client.get("/geraet").text
    assert "abgelegt" not in client.get("/geraet").text
