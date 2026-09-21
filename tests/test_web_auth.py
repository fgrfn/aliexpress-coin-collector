"""Passwort der Weboberflaeche: Speicherung und der Weg durch die Seiten.

Die Routentests brauchen kein Geraet und keine Datenbank -- nur ein leeres Datenverzeichnis.
"""

from __future__ import annotations

import pytest

from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.web import auth

# -- Speicherung -------------------------------------------------------------------------------


def test_nothing_stored_before_the_first_setup(tmp_path):
    assert auth.is_set(tmp_path) is False
    assert auth.verify(tmp_path, "irgendwas") is False


def test_password_round_trip(tmp_path):
    auth.set_password(tmp_path, "geheim-genug", "geheim-genug")
    assert auth.is_set(tmp_path) is True
    assert auth.verify(tmp_path, "geheim-genug") is True
    assert auth.verify(tmp_path, "geheim-genuG") is False


def test_the_password_itself_is_never_written(tmp_path):
    auth.set_password(tmp_path, "sehr-geheimes-wort", "sehr-geheimes-wort")
    raw = (tmp_path / auth.PASSWORD_FILE).read_text()
    assert "sehr-geheimes-wort" not in raw
    assert raw.startswith("pbkdf2_sha256$")


def test_file_is_only_readable_by_its_owner(tmp_path):
    auth.set_password(tmp_path, "geheim-genug", "geheim-genug")
    assert (tmp_path / auth.PASSWORD_FILE).stat().st_mode & 0o777 == 0o600


def test_two_passwords_get_different_salts(tmp_path, tmp_path_factory):
    other = tmp_path_factory.mktemp("zweite")
    auth.set_password(tmp_path, "gleiches-wort", "gleiches-wort")
    auth.set_password(other, "gleiches-wort", "gleiches-wort")
    assert auth.stored_hash(tmp_path) != auth.stored_hash(other)


def test_mismatch_and_length_are_rejected(tmp_path):
    with pytest.raises(auth.PasswordError, match="überein"):
        auth.set_password(tmp_path, "geheim-genug", "geheim-anders")
    with pytest.raises(auth.PasswordError, match="Zeichen"):
        auth.set_password(tmp_path, "kurz", "kurz")
    assert auth.is_set(tmp_path) is False  # nichts angelegt, wenn die Pruefung scheitert


def test_setup_cannot_overwrite_an_existing_password(tmp_path):
    auth.set_password(tmp_path, "das-erste-wort", "das-erste-wort")
    with pytest.raises(auth.PasswordError, match="bereits"):
        auth.set_password(tmp_path, "das-zweite-wort", "das-zweite-wort", only_if_unset=True)
    assert auth.verify(tmp_path, "das-erste-wort") is True


def test_change_replaces_the_old_password(tmp_path):
    auth.set_password(tmp_path, "das-erste-wort", "das-erste-wort")
    auth.set_password(tmp_path, "das-zweite-wort", "das-zweite-wort")
    assert auth.verify(tmp_path, "das-erste-wort") is False
    assert auth.verify(tmp_path, "das-zweite-wort") is True


def test_unreadable_content_counts_as_wrong_not_as_open(tmp_path):
    (tmp_path / auth.PASSWORD_FILE).write_text("kaputt\n")
    assert auth.is_set(tmp_path) is True  # gesetzt, aber unbrauchbar -- niemand kommt rein
    assert auth.verify(tmp_path, "kaputt") is False
    assert auth.verify(tmp_path, "") is False


# -- Der Weg durch die Seiten ------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    # httpx zuerst: ohne es wirft starlette einen RuntimeError, den importorskip nicht faengt.
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    cfg = Config.load(tmp_path / "keine.env")
    return fastapi_testclient.TestClient(create_app(cfg), follow_redirects=False)


def test_everything_leads_to_the_setup_page_before_a_password_exists(client):
    for path in ("/", "/login", "/shot/20260921-080000-vorher.png"):
        response = client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/setup", path


def test_run_cannot_be_triggered_before_a_password_exists(client, tmp_path):
    assert client.post("/run").headers["location"] == "/setup"
    assert not (tmp_path / "run-requested").exists()


def test_setup_sets_the_password_and_logs_in(client, tmp_path):
    response = client.post("/setup", data={"password": "geheim-genug", "repeat": "geheim-genug"})
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert "acc_session" in response.cookies
    assert auth.verify(tmp_path, "geheim-genug") is True
    assert client.get("/").status_code == 200  # Cookie traegt


def test_setup_refuses_a_mismatch_and_stays_open(client, tmp_path):
    response = client.post("/setup", data={"password": "geheim-genug", "repeat": "etwas-anderes"})
    assert response.status_code == 400
    assert "überein" in response.text
    assert auth.is_set(tmp_path) is False


def test_setup_is_closed_once_a_password_exists(client, tmp_path):
    auth.set_password(tmp_path, "das-erste-wort", "das-erste-wort")
    assert client.get("/setup").headers["location"] == "/login"
    response = client.post("/setup", data={"password": "fremdes-wort", "repeat": "fremdes-wort"})
    assert response.headers["location"] == "/login"
    assert auth.verify(tmp_path, "das-erste-wort") is True


def test_wrong_password_does_not_let_anyone_in(client, tmp_path):
    auth.set_password(tmp_path, "geheim-genug", "geheim-genug")
    response = client.post("/login", data={"password": "geraten"})
    assert response.status_code == 401
    assert "acc_session" not in response.cookies
    assert client.get("/").headers["location"] == "/login"


def test_changing_the_password_invalidates_the_old_cookie(client, tmp_path):
    client.post("/setup", data={"password": "das-erste-wort", "repeat": "das-erste-wort"})
    assert client.get("/").status_code == 200

    response = client.post(
        "/password",
        data={"current": "das-erste-wort", "password": "das-zweite-wort", "repeat": "das-zweite-wort"},
    )
    assert response.headers["location"] == "/login"
    # Der Browser haelt das alte Cookie noch, es traegt aber nicht mehr.
    assert client.get("/").headers["location"] == "/login"
    assert client.post("/login", data={"password": "das-zweite-wort"}).status_code == 303


def test_change_needs_the_current_password(client, tmp_path):
    client.post("/setup", data={"password": "das-erste-wort", "repeat": "das-erste-wort"})
    response = client.post(
        "/password", data={"current": "geraten", "password": "neues-wort-hier", "repeat": "neues-wort-hier"}
    )
    assert response.status_code == 400
    assert auth.verify(tmp_path, "das-erste-wort") is True


def test_an_old_env_password_is_adopted_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_PASSWORD", "altes-env-wort")
    from aliexpress_coin_collector.web.app import adopt_env_password

    cfg = Config.load(tmp_path / "keine.env")
    adopt_env_password(cfg)
    assert auth.verify(tmp_path, "altes-env-wort") is True

    # Ein spaeter geaendertes WEB_PASSWORD wirkt nicht mehr: die Datei gilt.
    monkeypatch.setenv("WEB_PASSWORD", "neues-env-wort")
    adopt_env_password(cfg)
    assert auth.verify(tmp_path, "altes-env-wort") is True
    assert auth.verify(tmp_path, "neues-env-wort") is False
