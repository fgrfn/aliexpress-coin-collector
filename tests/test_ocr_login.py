"""Erkennung einer abgelaufenen Anmeldung.

Braucht kein Tesseract: die Wortliste wird untergeschoben. Geprüft wird vor allem die
Sicherheitszusage — die Erkennung darf einen gescheiterten Lauf genauer benennen, aber
niemals einen erfolgreichen stören.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from aliexpress_coin_collector import ocr


def png(width: int = 720, height: int = 1280) -> bytes:
    ok, buffer = cv2.imencode(".png", np.full((height, width, 3), 240, dtype=np.uint8))
    assert ok
    return buffer.tobytes()


def word(text: str, x: int = 100, y: int = 600) -> ocr.Word:
    return ocr.Word(text=text, x=x, y=y, w=120, h=40, conf=90.0)


@pytest.fixture
def fake_ocr(monkeypatch):
    """Laesst Wortliste und Button-Fund von aussen setzen, ohne Tesseract."""

    def use(words, button=None):
        monkeypatch.setattr(ocr, "read_words", lambda *a, **k: words)
        monkeypatch.setattr(ocr, "find_button", lambda *a, **k: button)

    return use


# -- Die Marker selbst ---------------------------------------------------------------------------


def test_the_usual_wordings_are_recognised():
    for text in ("Bitte anmelden", "JETZT EINLOGGEN", "Sign in to continue", "Anmeldung erforderlich"):
        assert ocr.looks_logged_out(text), text


def test_an_ordinary_coin_page_is_not_mistaken_for_one():
    assert not ocr.looks_logged_out("Sammeln 25 Münzen Tage in Folge Mehr Münzen verdienen")


def test_the_markers_can_be_replaced():
    assert ocr.looks_logged_out("veuillez vous connecter", markers=("connecter",))
    assert not ocr.looks_logged_out("Bitte anmelden", markers=("connecter",))


# -- Die Sicherheitszusage -----------------------------------------------------------------------


def test_a_found_button_wins_over_the_marker(cfg, fake_ocr):
    # Der entscheidende Punkt. Stuende irgendwo auf der Seite "anmelden", waehrend der
    # Sammeln-Knopf da ist, duerfte das den Lauf nicht abwuergen.
    fake_ocr([word("Sammeln"), word("Anmelden", y=1200)], button=word("Sammeln"))
    assert ocr.analyze(png(), cfg).logged_out is False


def test_the_done_state_wins_over_the_marker(cfg, fake_ocr):
    fake_ocr([word("morgen", y=100), word("Anmelden", y=1200)])
    state = ocr.analyze(png(), cfg)
    assert state.done is True
    assert state.logged_out is False


def test_without_button_and_without_done_the_marker_counts(cfg, fake_ocr):
    fake_ocr([word("Bitte"), word("anmelden")])
    state = ocr.analyze(png(), cfg)
    assert state.logged_out is True
    assert state.button is None and state.done is False


def test_an_empty_page_is_not_a_login_prompt(cfg, fake_ocr):
    fake_ocr([])
    assert ocr.analyze(png(), cfg).logged_out is False
