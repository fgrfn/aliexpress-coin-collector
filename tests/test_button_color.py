"""Die Knoepfe der Aufgabenliste ueber ihre Farbe finden.

Die Schrift auf ihnen steht weiss auf orange und war am Geraet nicht zu lesen -- die Flaeche
dagegen ist eindeutig. Geprueft wird an gemalten Bildern: echte Screenshots der Coin-Seite
zeigen Kontodaten und liegen darum nicht im Repo.
"""

from __future__ import annotations

import cv2
import numpy as np

from aliexpress_coin_collector.ocr import find_orange_buttons

BREITE, HOEHE = 720, 1280
ORANGE = (34, 103, 242)  # BGR, wie der Knopf "Und los"
GOLD = (60, 200, 245)  # BGR, die Muenzsymbole daneben


def leer() -> np.ndarray:
    bild = np.full((HOEHE, BREITE, 3), 255, np.uint8)
    return bild


def knopf(bild: np.ndarray, y: int, x: int = 527, w: int = 154, h: int = 60, farbe=ORANGE) -> None:
    cv2.rectangle(bild, (x, y), (x + w, y + h), farbe, -1)


def test_findet_die_knoepfe_von_oben_nach_unten() -> None:
    bild = leer()
    for y in (487, 733, 1012):
        knopf(bild, y)

    gefunden = find_orange_buttons(bild)

    assert len(gefunden) == 3
    assert [b.y for b in gefunden] == sorted(b.y for b in gefunden)
    assert all(b.cx > BREITE * 0.5 for b in gefunden)


def test_weisse_schrift_im_knopf_zerlegt_ihn_nicht() -> None:
    """Die Beschriftung stanzt Loecher in die Flaeche -- daraus darf kein zweiter Knopf werden."""
    bild = leer()
    knopf(bild, 487)
    cv2.putText(bild, "Und los", (555, 527), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)

    gefunden = find_orange_buttons(bild)

    assert len(gefunden) == 1


def test_muenzsymbole_zaehlen_nicht_als_knopf() -> None:
    """Gold ist ein anderer Farbton als das Knopf-Orange."""
    bild = leer()
    cv2.circle(bild, (180, 826), 22, GOLD, -1)
    cv2.circle(bild, (180, 1100), 22, GOLD, -1)

    assert find_orange_buttons(bild) == []


def test_breite_werbeflaechen_zaehlen_nicht_als_knopf() -> None:
    """Ein Banner ueber die ganze Breite ist kein Knopf."""
    bild = leer()
    knopf(bild, 300, x=20, w=680, h=90)

    assert find_orange_buttons(bild) == []


def test_knoepfe_am_linken_rand_zaehlen_nicht() -> None:
    """Die Knoepfe der Aufgabenliste sitzen rechts. Was links steht, gehoert nicht dazu."""
    bild = leer()
    knopf(bild, 487, x=40)

    assert find_orange_buttons(bild) == []


def test_ausreisser_faellt_raus() -> None:
    """Die gemessenen Werte vom Geraet, 26.09.2026, 720x1280.

    Die Farbmaske fand vier Flaechen: drei Knoepfe von genau 154x77 und die Muenzgrafik im Kopf
    des Fensters mit 226x121. Echte Knoepfe sind untereinander gleich gross, die Grafik nicht.
    """
    bild = leer()
    knopf(bild, 257, x=494, w=226, h=121)  # Muenzgrafik im Fensterkopf
    for y in (479, 757, 1052):
        knopf(bild, y, x=528, w=154, h=77)

    gefunden = find_orange_buttons(bild)

    assert len(gefunden) == 3
    # Die Grafik war 121 hoch, die Knoepfe 77 -- was uebrig bleibt, hat Knopfhoehe.
    assert all(abs(b.h - 77) <= 4 for b in gefunden), [b.h for b in gefunden]
    assert all(abs(b.w - 154) <= 4 for b in gefunden), [b.w for b in gefunden]
    assert gefunden[0].y >= 470


def test_zwei_knoepfe_bleiben_beide() -> None:
    """Bei zweien waere die uebliche Groesse kein Massstab, sondern ein Muenzwurf."""
    bild = leer()
    knopf(bild, 400, w=154, h=60)
    knopf(bild, 700, w=200, h=80)

    assert len(find_orange_buttons(bild)) == 2


def test_leeres_bild_gibt_nichts() -> None:
    assert find_orange_buttons(leer()) == []
