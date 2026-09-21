"""Das Erkennungs-Werkzeug der Weboberfläche.

Tesseract ist hier nicht installiert (und in der CI auch nicht), deshalb wird `ocr.analyze`
ersetzt. Geprüft wird alles drumherum: die Annahme der Datei, die Umrechnung der Fundstelle,
die Vorschau — und vor allem, dass das hochgeladene Bild nirgends liegen bleibt.
"""

from __future__ import annotations

import numpy as np
import pytest

from aliexpress_coin_collector import ocr
from aliexpress_coin_collector.web import imagecheck


def png(width: int = 200, height: int = 400) -> bytes:
    import cv2

    img = np.full((height, width, 3), 40, dtype=np.uint8)
    cv2.rectangle(img, (40, 180), (160, 220), (20, 110, 240), -1)
    ok, buffer = cv2.imencode(".png", img)
    assert ok
    return buffer.tobytes()


def state(button=None, done=False, coins=None, width=200, height=400, text="ein paar Woerter hier"):
    return ocr.PageState(button=button, done=done, coins=coins, width=width, height=height, text=text)


# -- Schwellwert -----------------------------------------------------------------------------


def test_an_empty_threshold_means_the_normal_behaviour():
    assert imagecheck.parse_threshold("") == ocr.THRESHOLDS
    assert imagecheck.parse_threshold("   ") == ocr.THRESHOLDS


def test_a_number_replaces_the_whole_chain():
    assert imagecheck.parse_threshold("200") == (200,)
    assert imagecheck.parse_threshold(" 0 ") == (0,)
    assert imagecheck.parse_threshold("255") == (255,)


def test_nonsense_falls_back_instead_of_failing():
    for raw in ("abc", "-5", "256", "1e3", "12; rm -rf /"):
        assert imagecheck.parse_threshold(raw) == ocr.THRESHOLDS, raw


# -- Annahme der Datei -----------------------------------------------------------------------


def test_an_empty_upload_is_refused():
    with pytest.raises(imagecheck.InspectError, match="keine Datei"):
        imagecheck.check_upload("image/png", 0)


def test_a_huge_upload_is_refused_before_it_reaches_opencv():
    with pytest.raises(imagecheck.InspectError, match="zu groß"):
        imagecheck.check_upload("image/png", imagecheck.MAX_UPLOAD + 1)


def test_only_images_are_accepted():
    with pytest.raises(imagecheck.InspectError, match="kein Bild"):
        imagecheck.check_upload("application/zip", 500)
    for kind in ("image/png", "image/jpeg", "image/webp", "image/png; charset=binary"):
        imagecheck.check_upload(kind, 500)


def test_a_missing_content_type_is_not_a_reason_to_refuse():
    imagecheck.check_upload("", 500)  # manche Browser schicken keinen


# -- Vorschau --------------------------------------------------------------------------------


def test_the_preview_is_a_small_inline_image():
    uri = imagecheck.preview(png(800, 1600))
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) < 120_000  # verkleinert, nicht das Original


def test_a_broken_image_yields_no_preview_instead_of_an_error():
    assert imagecheck.preview(b"das ist kein Bild") == ""


# -- Auswertung ------------------------------------------------------------------------------


def test_a_found_button_is_reported_with_position_and_confidence(monkeypatch, cfg):
    word = ocr.Word(text="Sammeln", x=40, y=180, w=120, h=40, conf=94.2)
    monkeypatch.setattr(ocr, "analyze", lambda *a, **k: state(button=word, coins=1275))

    result = imagecheck.analyze(png(), cfg)
    assert result.found is True
    assert result.label == "Sammeln"
    assert result.confidence == 94.2
    assert result.position == "100, 200"
    assert result.coins == 1275
    assert result.width == 200 and result.height == 400
    assert result.duration_s >= 0


def test_the_box_is_given_in_percent_so_the_preview_can_scale(monkeypatch, cfg):
    word = ocr.Word(text="Sammeln", x=40, y=180, w=120, h=40, conf=90.0)
    monkeypatch.setattr(ocr, "analyze", lambda *a, **k: state(button=word))

    box = imagecheck.analyze(png(), cfg).box
    assert box is not None
    assert box.left == pytest.approx(20.0)  # 40 von 200
    assert box.top == pytest.approx(45.0)  # 180 von 400
    assert box.width == pytest.approx(60.0)
    assert box.height == pytest.approx(10.0)


def test_without_a_button_there_is_no_box(monkeypatch, cfg):
    monkeypatch.setattr(ocr, "analyze", lambda *a, **k: state(done=True))
    result = imagecheck.analyze(png(), cfg)
    assert result.found is False
    assert result.box is None
    assert result.done is True


def test_the_chosen_threshold_reaches_the_recognition(monkeypatch, cfg):
    seen = {}

    def spy(image, config, thresholds, invert):
        seen["thresholds"] = thresholds
        seen["invert"] = invert
        return state()

    monkeypatch.setattr(ocr, "analyze", spy)

    imagecheck.analyze(png(), cfg, "200", invert=False)
    assert seen == {"thresholds": (200,), "invert": False}

    imagecheck.analyze(png(), cfg, "", invert=True)
    assert seen == {"thresholds": ocr.THRESHOLDS, "invert": True}


def test_the_used_threshold_is_named_in_the_result(monkeypatch, cfg):
    monkeypatch.setattr(ocr, "analyze", lambda *a, **k: state())
    assert "Standard" in imagecheck.analyze(png(), cfg, "").thresholds
    assert imagecheck.analyze(png(), cfg, "210").thresholds == "210"


def test_a_failing_recognition_becomes_a_readable_message(monkeypatch, cfg):
    def boom(*a, **k):
        raise RuntimeError("tesseract ist nicht installiert")

    monkeypatch.setattr(ocr, "analyze", boom)
    with pytest.raises(imagecheck.InspectError, match="tesseract ist nicht installiert"):
        imagecheck.analyze(png(), cfg)


def test_an_undecodable_image_becomes_a_readable_message(monkeypatch, cfg):
    def boom(*a, **k):
        raise ValueError("Screenshot konnte nicht dekodiert werden")

    monkeypatch.setattr(ocr, "analyze", boom)
    with pytest.raises(imagecheck.InspectError, match="ließ sich nicht lesen"):
        imagecheck.analyze(b"kaputt", cfg)


def test_the_word_list_is_capped(monkeypatch, cfg):
    monkeypatch.setattr(ocr, "analyze", lambda *a, **k: state(text=" ".join(f"w{i}" for i in range(500))))
    result = imagecheck.analyze(png(), cfg)
    assert result.word_count == 500  # gezaehlt werden alle
    assert len(result.words) == 80  # angezeigt nur die ersten
