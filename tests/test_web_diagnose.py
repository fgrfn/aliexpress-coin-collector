"""Die Diagnose-Seite: Protokoll und Erkennungs-Werkzeug über HTTP.

Der wichtigste Test hier ist der letzte: ein hochgeladener Screenshot darf nirgends
liegen bleiben. Er enthält Kontostände und Bestellungen.
"""

from __future__ import annotations

import numpy as np
import pytest

from aliexpress_coin_collector import logs, ocr
from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.web import auth

PW = "mein-geheimes-wort"

SAMPLE = """2026-09-21 07:46:03,120 INFO    Lauf gestartet (Art: morgens)
2026-09-21 07:46:20,880 WARNING Button nur mit Schwellwert 220 gefunden
2026-09-21 09:38:11,500 ERROR   Geraet meldet unauthorized
"""


def png(width: int = 200, height: int = 400) -> bytes:
    import cv2

    img = np.full((height, width, 3), 40, dtype=np.uint8)
    ok, buffer = cv2.imencode(".png", img)
    assert ok
    return buffer.tobytes()


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


def files_in(path):
    return {p.relative_to(path) for p in path.rglob("*") if p.is_file()}


# -- Zugang ----------------------------------------------------------------------------------


def test_everything_is_behind_the_login(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WEB_PASSWORD", raising=False)
    from aliexpress_coin_collector.web.app import create_app

    auth.set_password(tmp_path, PW, PW)
    anonymous = fastapi_testclient.TestClient(create_app(Config.load(tmp_path / "keine.env")), follow_redirects=False)
    assert anonymous.get("/diagnose").headers["location"] == "/login"
    assert anonymous.get("/teile/protokoll").headers["location"] == "/login"
    response = anonymous.post("/diagnose/erkennung", files={"bild": ("x.png", png(), "image/png")})
    assert response.headers["location"] == "/login"


# -- Protokoll -------------------------------------------------------------------------------


def test_without_a_file_the_page_says_why(client):
    body = client.get("/diagnose").text
    assert "Noch keine Protokolldatei" in body


def test_the_log_is_shown(client, tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    body = client.get("/diagnose").text
    assert "Lauf gestartet" in body
    assert "Geraet meldet unauthorized" in body


def test_the_level_filter_works_over_http(client, tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    body = client.get("/diagnose?stufe=ERROR").text
    assert "Geraet meldet unauthorized" in body
    assert "Lauf gestartet" not in body


def test_the_search_works_over_http(client, tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    body = client.get("/diagnose?suche=Schwellwert").text
    assert "Button nur mit Schwellwert" in body
    assert "Lauf gestartet" not in body


def test_an_unknown_level_shows_everything_instead_of_nothing(client, tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    body = client.get("/diagnose?stufe=QUATSCH").text
    assert "Lauf gestartet" in body


def test_following_only_reloads_when_it_is_switched_on(client, tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    assert 'hx-trigger="every 5s"' not in client.get("/teile/protokoll").text
    assert 'hx-trigger="every 5s"' in client.get("/teile/protokoll?mit=1").text


def test_a_log_line_cannot_inject_markup(client, tmp_path):
    logs.path(tmp_path).write_text(
        '2026-09-21 07:46:03,120 ERROR   <script>alert("x")</script> kaputt\n', encoding="utf-8"
    )
    body = client.get("/diagnose").text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body


# -- Erkennungs-Werkzeug ---------------------------------------------------------------------


def test_a_screenshot_is_analysed_and_shown(client, tmp_path, monkeypatch):
    word = ocr.Word(text="Sammeln", x=40, y=180, w=120, h=40, conf=94.0)
    monkeypatch.setattr(
        ocr,
        "analyze",
        lambda *a, **k: ocr.PageState(button=word, done=False, coins=1275, width=200, height=400, text="Sammeln"),
    )
    response = client.post("/diagnose/erkennung", files={"bild": ("shot.png", png(), "image/png")})
    assert response.status_code == 200
    body = response.text
    assert "gefunden" in body
    assert "Sammeln" in body
    assert "94.0" in body
    assert "1.275" in body  # mit Tausenderpunkt
    assert "inspect-box" in body  # Rahmen um die Fundstelle


def test_the_uploaded_screenshot_is_never_written_to_disk(client, tmp_path, monkeypatch):
    monkeypatch.setattr(
        ocr,
        "analyze",
        lambda *a, **k: ocr.PageState(button=None, done=False, coins=None, width=200, height=400, text=""),
    )
    before = files_in(tmp_path)
    client.post("/diagnose/erkennung", files={"bild": ("geheim.png", png(), "image/png")})
    after = files_in(tmp_path)
    assert after == before, f"neue Dateien: {after - before}"


def test_a_non_image_is_refused(client, tmp_path):
    response = client.post("/diagnose/erkennung", files={"bild": ("x.zip", b"PK\x03\x04", "application/zip")})
    assert response.status_code == 400
    assert "kein Bild" in response.text
    assert files_in(tmp_path) == files_in(tmp_path)


def test_a_failing_recognition_is_explained_not_swallowed(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("tesseract ist nicht installiert")

    monkeypatch.setattr(ocr, "analyze", boom)
    response = client.post("/diagnose/erkennung", files={"bild": ("shot.png", png(), "image/png")})
    assert response.status_code == 400
    assert "tesseract ist nicht installiert" in response.text


def test_the_chosen_threshold_reaches_the_recognition(client, monkeypatch):
    seen = {}

    def spy(image, config, thresholds, invert):
        seen["thresholds"], seen["invert"] = thresholds, invert
        return ocr.PageState(button=None, done=False, coins=None, width=200, height=400, text="")

    monkeypatch.setattr(ocr, "analyze", spy)
    client.post(
        "/diagnose/erkennung",
        files={"bild": ("shot.png", png(), "image/png")},
        data={"schwelle": "210", "invertiert": ""},
    )
    assert seen == {"thresholds": (210,), "invert": False}


def test_the_page_promises_that_nothing_is_stored(client):
    assert "wird nie gespeichert" in client.get("/diagnose").text


def test_the_sidebar_lists_every_area(client):
    body = client.get("/diagnose").text
    for label in ("Übersicht", "Verlauf", "Gerät", "Diagnose", "Einstellungen"):
        assert f">{label}</span>" in body, label


def test_the_tool_says_whether_a_login_prompt_was_found(client, tmp_path, monkeypatch):
    # Damit sich die Erkennung an einem echten Screenshot pruefen laesst, ohne einen Lauf
    # abzuwarten: Bild hochladen, und die Seite sagt, ob die Anmelde-Marker greifen.
    monkeypatch.setattr(
        ocr,
        "analyze",
        lambda *a, **k: ocr.PageState(
            button=None, done=False, coins=None, width=200, height=400, text="Bitte anmelden", logged_out=True
        ),
    )
    body = client.post("/diagnose/erkennung", files={"bild": ("shot.png", png(), "image/png")}).text
    assert "Anmelde-Marker gefunden" in body


def test_an_ordinary_screenshot_shows_no_login_prompt(client, monkeypatch):
    monkeypatch.setattr(
        ocr,
        "analyze",
        lambda *a, **k: ocr.PageState(button=None, done=True, coins=1275, width=200, height=400, text=""),
    )
    body = client.post("/diagnose/erkennung", files={"bild": ("shot.png", png(), "image/png")}).text
    assert "Anmelde-Marker gefunden" not in body
