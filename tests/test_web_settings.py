"""Die Einstellungsseite: alles an einem Ort, geprüft mit denselben Regeln wie der Dienst.

Der wichtigste Test hier ist der zum Discord-Webhook. Er ist ein Geheimnis: wer ihn hat, kann in
den Kanal schreiben. Er darf darum weder in die Seite noch ins Protokoll gelangen — auch nicht,
wenn jemand ihn gerade eingetragen hat.
"""

from __future__ import annotations

from datetime import time as dtime

import pytest

from aliexpress_coin_collector import settings
from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.web import auth

PW = "mein-geheimes-wort"
HOOK = "https://discord.com/api/webhooks/1/streng-geheim"

ABLAUF = {
    "morning_start": "06:00",
    "morning_end": "09:00",
    "evening_start": "18:00",
    "evening_end": "20:00",
    "skip_if_awake": "1",
    "busy_retry_min": "12",
    "busy_max_wait_min": "90",
    "page_timeout_s": "60",
    "confirm_timeout_s": "20",
    "launch_retries": "2",
}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    return tmp_path


@pytest.fixture
def client(data_dir):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from aliexpress_coin_collector.web.app import create_app

    cfg = Config.load(data_dir / "keine.env")
    auth.set_password(data_dir, PW, PW)
    c = fastapi_testclient.TestClient(create_app(cfg), follow_redirects=False)
    c.post("/login", data={"password": PW})
    return c


def stored(data_dir):
    return settings.load(data_dir).values


# -- Zugang ------------------------------------------------------------------------------------


def test_everything_is_behind_the_login(data_dir):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from aliexpress_coin_collector.web.app import create_app

    auth.set_password(data_dir, PW, PW)
    anonymous = fastapi_testclient.TestClient(create_app(Config.load(data_dir / "keine.env")), follow_redirects=False)
    assert anonymous.get("/einstellungen").headers["location"] == "/login"
    for path in ("/einstellungen/ablauf", "/einstellungen/geraet", "/einstellungen/meldungen"):
        assert anonymous.post(path, data={}).headers["location"] == "/login", path
    assert stored(data_dir) == {}, "ein Nichtangemeldeter darf nichts verstellen"


# -- Ablauf ------------------------------------------------------------------------------------


def test_the_page_opens(client):
    body = client.get("/einstellungen").text
    for heading in ("Ablauf &amp; Verhalten", "Gerät", "Benachrichtigungen", "Zugang"):
        assert heading in body, heading


def test_the_sidebar_has_all_five_areas(client):
    body = client.get("/einstellungen").text
    for label in ("Übersicht", "Verlauf", "Gerät", "Diagnose", "Einstellungen"):
        assert f">{label}</span>" in body, label


def test_saving_the_schedule_stores_every_field(client, data_dir):
    assert client.post("/einstellungen/ablauf", data=ABLAUF).status_code == 303
    assert stored(data_dir) == {
        "morning_start": dtime(6, 0),
        "morning_end": dtime(9, 0),
        "evening_start": dtime(18, 0),
        "evening_end": dtime(20, 0),
        "skip_if_awake": True,
        "busy_retry_min": 12,
        "busy_max_wait_min": 90,
        "page_timeout_s": 60,
        "confirm_timeout_s": 20,
        "launch_retries": 2,
    }


def test_an_unticked_box_means_off_not_unchanged(client, data_dir):
    client.post("/einstellungen/ablauf", data=ABLAUF)
    assert stored(data_dir)["skip_if_awake"] is True
    client.post("/einstellungen/ablauf", data={**ABLAUF, "skip_if_awake": ""})
    assert stored(data_dir)["skip_if_awake"] is False


def test_the_saved_value_shows_up_on_the_page(client):
    client.post("/einstellungen/ablauf", data=ABLAUF)
    assert 'value="06:00"' in client.get("/einstellungen").text


def test_a_changed_value_is_marked_as_such(client):
    # Gezaehlt wird die Markierung selbst, nicht das Wort: in der Legende steht es einmal
    # ohnehin, und ein Test, der das mitzaehlt, wuerde nie fehlschlagen.
    mark = '<span class="tag">geändert</span>'
    assert client.get("/einstellungen").text.count(mark) == 1, "nur die Legende"
    client.post("/einstellungen/ablauf", data=ABLAUF)
    # Die zehn gespeicherten Werte plus die Legende.
    assert client.get("/einstellungen").text.count(mark) == 11


# -- Geprueft wird mit den Regeln des Dienstes --------------------------------------------------


def test_a_window_that_ends_before_it_starts_is_refused(client, data_dir):
    client.post("/einstellungen/ablauf", data={**ABLAUF, "morning_end": "05:00"})
    assert stored(data_dir) == {}


def test_a_retry_after_the_forced_run_is_refused(client, data_dir):
    client.post("/einstellungen/ablauf", data={**ABLAUF, "busy_retry_min": "200"})
    assert stored(data_dir) == {}


def test_a_refusal_names_the_field_as_it_is_labelled_not_the_env_variable(client):
    from urllib.parse import unquote

    client.post("/einstellungen/ablauf", data={**ABLAUF, "busy_retry_min": "200"})
    flash = unquote(client.cookies["acc_flash"])
    assert "BUSY_RETRY_MIN" not in flash
    assert "Dann erneut versuchen nach" in flash


def test_text_where_a_number_belongs_is_refused(client, data_dir):
    client.post("/einstellungen/ablauf", data={**ABLAUF, "page_timeout_s": "bald"})
    assert stored(data_dir) == {}


def test_a_broken_value_leaves_the_previous_one_standing(client, data_dir):
    client.post("/einstellungen/ablauf", data=ABLAUF)
    client.post("/einstellungen/ablauf", data={**ABLAUF, "busy_retry_min": "200"})
    assert stored(data_dir)["busy_retry_min"] == 12


# -- Geraet ------------------------------------------------------------------------------------


def test_the_device_address_is_stored(client, data_dir):
    client.post("/einstellungen/geraet", data={"adb_serial": "10.0.0.9:5555"})
    assert stored(data_dir) == {"adb_serial": "10.0.0.9:5555"}


def test_an_impossible_address_is_refused(client, data_dir):
    client.post("/einstellungen/geraet", data={"adb_serial": "kein host:99999"})
    assert stored(data_dir) == {}


def test_saving_the_address_does_not_touch_the_device(client, data_dir):
    # Die Oberflaeche verbindet sich nie selbst -- es entsteht kein Auftrag an den Dienst.
    client.post("/einstellungen/geraet", data={"adb_serial": "10.0.0.9:5555"})
    assert not (data_dir / "commands").exists()
    assert not (data_dir / "control").exists()


# -- Benachrichtigungen ------------------------------------------------------------------------


def test_the_webhook_is_stored_but_never_shown(client, data_dir):
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK, "notify_on_success": "1"})
    assert stored(data_dir)["discord_webhook"] == HOOK
    body = client.get("/einstellungen").text
    assert HOOK not in body
    assert "streng-geheim" not in body
    assert "Webhook hinterlegt" in body


def test_the_webhook_never_reaches_the_log(client, caplog):
    with caplog.at_level("INFO"):
        client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK})
    assert "streng-geheim" not in caplog.text


def test_an_empty_field_keeps_the_stored_webhook(client, data_dir):
    # Das Feld kommt immer leer an, weil der Wert nie in die Seite geschrieben wird. Leer
    # duerfte darum nie "loeschen" heissen.
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK})
    client.post("/einstellungen/meldungen", data={"discord_webhook": "", "notify_on_success": "1"})
    assert stored(data_dir)["discord_webhook"] == HOOK


def test_the_webhook_is_only_cleared_on_request(client, data_dir):
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK})
    client.post("/einstellungen/meldungen", data={"remove_webhook": "1"})
    assert stored(data_dir)["discord_webhook"] == ""
    assert "kein Webhook" in client.get("/einstellungen").text


def test_a_webhook_without_https_is_refused(client, data_dir):
    client.post("/einstellungen/meldungen", data={"discord_webhook": "http://example.invalid/x"})
    assert "discord_webhook" not in stored(data_dir)


def test_a_refused_webhook_is_not_echoed_in_the_message(client):
    from urllib.parse import unquote

    client.post("/einstellungen/meldungen", data={"discord_webhook": "http://example.invalid/streng-geheim"})
    assert "streng-geheim" not in unquote(client.cookies["acc_flash"])


# -- Abschnitte stoeren einander nicht ----------------------------------------------------------


def test_saving_the_notifications_keeps_the_schedule(client, data_dir):
    client.post("/einstellungen/ablauf", data=ABLAUF)
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK})
    assert stored(data_dir)["morning_start"] == dtime(6, 0)
    assert stored(data_dir)["busy_retry_min"] == 12


def test_saving_the_notifications_does_not_suppress_todays_run(client, data_dir):
    # changed_at legt den Zeitplan von heute teilweise still. Das darf nur eine Fensteraenderung.
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK})
    assert settings.load(data_dir).changed_at is None
    client.post("/einstellungen/ablauf", data=ABLAUF)
    assert settings.load(data_dir).changed_at is not None


# -- Passwort ist mit umgezogen -----------------------------------------------------------------


def test_the_password_form_lives_here_now_and_not_on_the_dashboard(client):
    assert 'action="/password"' in client.get("/einstellungen").text
    assert 'action="/password"' not in client.get("/").text


def test_a_wrong_current_password_is_answered_on_this_page(client):
    response = client.post("/password", data={"current": "falsch", "password": "x" * 12, "repeat": "x" * 12})
    assert response.status_code == 400
    assert "Das bisherige Passwort stimmt nicht." in response.text
    assert "Ablauf &amp; Verhalten" in response.text, "die Antwort ist die Einstellungsseite"


def test_changing_the_password_still_works(client):
    new = "ein-anderes-wort"
    response = client.post("/password", data={"current": PW, "password": new, "repeat": new})
    assert response.headers["location"] == "/login"


# -- Das Dashboard zeigt die Zeiten nur noch an --------------------------------------------------


def test_the_dashboard_shows_todays_times_and_links_to_the_settings(client):
    body = client.get("/").text
    assert "Heute geplant" in body
    assert "/einstellungen#ablauf" in body
    assert 'action="/settings"' not in body, "das Formular ist umgezogen"


def test_the_offline_alert_is_configurable(client, data_dir):
    client.post(
        "/einstellungen/meldungen",
        data={"notify_on_offline": "1", "offline_alert_min": "90"},
    )
    assert stored(data_dir)["notify_on_offline"] is True
    assert stored(data_dir)["offline_alert_min"] == 90


def test_the_offline_alert_can_be_switched_off(client, data_dir):
    client.post("/einstellungen/meldungen", data={"offline_alert_min": "30"})
    assert stored(data_dir)["notify_on_offline"] is False


def test_a_waiting_time_of_zero_is_refused(client, data_dir):
    client.post("/einstellungen/meldungen", data={"notify_on_offline": "1", "offline_alert_min": "0"})
    assert "offline_alert_min" not in stored(data_dir)


# -- Testmeldung -------------------------------------------------------------------------------


def test_the_test_button_is_disabled_without_a_webhook(client):
    assert 'action="/einstellungen/test"' in client.get("/einstellungen").text
    assert "Erst einen Webhook hinterlegen" in client.get("/einstellungen").text


def test_the_test_message_goes_out_right_away(client, data_dir, monkeypatch):
    # Bewusst nicht ueber die Auftragsablage: wer testet, will die Antwort sofort.
    from aliexpress_coin_collector import notify

    sent = []
    monkeypatch.setattr(notify, "send", lambda hook, message, *a, **k: sent.append(message) or notify.Sent(True))
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK, "offline_alert_min": "30"})

    response = client.post("/einstellungen/test")
    assert response.status_code == 303
    assert len(sent) == 1
    assert sent[0].title == "🔔 Testmeldung"
    assert not (data_dir / "commands").exists(), "kein Auftrag an den Dienst"


def test_a_failed_test_says_why(client, monkeypatch):
    from urllib.parse import unquote

    from aliexpress_coin_collector import notify

    monkeypatch.setattr(notify, "send", lambda *a, **k: notify.Sent(False, "Diesen Webhook gibt es nicht (mehr)."))
    client.post("/einstellungen/meldungen", data={"discord_webhook": HOOK, "offline_alert_min": "30"})

    client.post("/einstellungen/test")
    assert "gibt es nicht" in unquote(client.cookies["acc_flash"])


def test_a_test_without_a_webhook_is_refused_before_sending(client, monkeypatch):
    from aliexpress_coin_collector import notify

    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("darf ohne Webhook nicht senden"))
    assert client.post("/einstellungen/test").status_code == 303


def test_the_test_is_behind_the_login(data_dir):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    from aliexpress_coin_collector.web.app import create_app

    auth.set_password(data_dir, PW, PW)
    anonymous = fastapi_testclient.TestClient(create_app(Config.load(data_dir / "keine.env")), follow_redirects=False)
    assert anonymous.post("/einstellungen/test").headers["location"] == "/login"
