"""Die Knoepfe der Aufgabenliste ueber ihre Farbe finden.

Die Schrift auf ihnen steht weiss auf orange und war am Geraet nicht zu lesen -- die Flaeche
dagegen ist eindeutig. Geprueft wird an gemalten Bildern: echte Screenshots der Coin-Seite
zeigen Kontodaten und liegen darum nicht im Repo.
"""

from __future__ import annotations

import cv2
import numpy as np

from aliexpress_coin_collector.ocr import (
    colour_profile,
    find_anchors,
    find_done_pills,
    find_orange_buttons,
)

BREITE, HOEHE = 720, 1280
ORANGE = (34, 103, 242)  # BGR, wie der Knopf "Und los"
GOLD = (60, 200, 245)  # BGR, die Muenzsymbole daneben


def leer() -> np.ndarray:
    bild = np.full((HOEHE, BREITE, 3), 255, np.uint8)
    return bild


def knopf(
    bild: np.ndarray, y: int, x: int = 527, w: int = 154, h: int = 60, farbe=ORANGE, y2_h: int | None = None
) -> None:
    cv2.rectangle(bild, (x, y), (x + w, y + (y2_h or h)), farbe, -1)


def find_profile(bild: np.ndarray):
    return colour_profile(bild)


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


# -- Farben ausmessen statt raten ----------------------------------------------------------------
#
# Am 26.09.2026 kam heraus, dass erledigte Aufgaben gar keinen Knopf tragen, sondern ein
# blassgruenes Feld mit Haken. Dessen Farbwerte kannte niemand -- und an dieser Stelle war schon
# zweimal geraten und zweimal danebengegriffen worden. Darum ein Messfuehler.

MINT = (230, 244, 238)  # BGR, wie das Feld einer erledigten Aufgabe


def test_farbprofil_findet_den_orangen_knopf() -> None:
    bild = leer()
    knopf(bild, 400, y2_h=77)

    treffer = [r for r in find_profile(bild) if r.s > 100]

    assert len(treffer) == 1
    assert 5 <= treffer[0].h <= 25  # der Farbton, auf den die Knopfsuche hoert
    assert treffer[0].y0 >= 396 and treffer[0].y1 <= 480


def test_farbprofil_zeigt_das_blasse_feld_als_fast_weiss() -> None:
    """Der Grund, warum die Knopfsuche es nicht findet -- und warum Raten hier schiefging."""
    bild = leer()
    cv2.rectangle(bild, (528, 700), (682, 777), MINT, -1)

    feld = [r for r in find_profile(bild) if 700 <= r.y0 <= 780]

    assert feld, find_profile(bild)
    assert feld[0].s < 40, "kaum Saettigung -- von Weiss kaum zu unterscheiden"
    assert feld[0].v > 200


def test_farbprofil_fasst_gleiche_farben_zusammen() -> None:
    """Sonst waere die Ausgabe eine Zahlenwueste statt einer Handvoll Abschnitte."""
    bild = leer()
    knopf(bild, 400, y2_h=77)
    cv2.rectangle(bild, (528, 700), (682, 777), MINT, -1)

    # Weiss oben, Knopf, Weiss, Feld, Weiss unten.
    assert len(find_profile(bild)) == 5


# -- Das blassgruene Feld erledigter Aufgaben ----------------------------------------------------
#
# Die Farbe ist **gemessen**, nicht geschaetzt: `acc ocr 02-liste.png --farben` auf dem
# Produktivgeraet, 26.09.2026, ergab in der Knopfspalte H 78..79, S 13..71, V 225..249 -- und
# fuer die Knoepfe H 12, S 227, V 247. Die gemalten Felder hier tragen genau diese Werte.

HAKEN_HELL = tuple(int(c) for c in cv2.cvtColor(np.uint8([[[78, 13, 249]]]), cv2.COLOR_HSV2BGR)[0][0])
HAKEN_KRAEFTIG = tuple(int(c) for c in cv2.cvtColor(np.uint8([[[79, 71, 225]]]), cv2.COLOR_HSV2BGR)[0][0])
KNOPF_GEMESSEN = tuple(int(c) for c in cv2.cvtColor(np.uint8([[[12, 227, 247]]]), cv2.COLOR_HSV2BGR)[0][0])


def test_findet_das_blassgruene_feld() -> None:
    bild = leer()
    knopf(bild, 400, w=154, h=77, farbe=HAKEN_HELL)

    gefunden = find_done_pills(bild)

    assert len(gefunden) == 1
    assert abs(gefunden[0].h - 77) <= 4 and abs(gefunden[0].w - 154) <= 4


def test_der_haken_im_feld_zerlegt_es_nicht() -> None:
    """Der Haken ist kraeftiger gruen als das Feld -- daraus darf kein zweites Feld werden."""
    bild = leer()
    knopf(bild, 400, w=154, h=77, farbe=HAKEN_HELL)
    cv2.line(bild, (590, 440), (600, 452), HAKEN_KRAEFTIG, 6)
    cv2.line(bild, (600, 452), (620, 425), HAKEN_KRAEFTIG, 6)

    assert len(find_done_pills(bild)) == 1


def test_weiss_ist_kein_erledigt_feld() -> None:
    """Der heikle Fall: das Feld hat Saettigung 13, Weiss hat 0. Viel trennt sie nicht."""
    assert find_done_pills(leer()) == []


def test_der_orange_knopf_ist_kein_erledigt_feld() -> None:
    bild = leer()
    knopf(bild, 400, w=154, h=77, farbe=KNOPF_GEMESSEN)

    assert find_done_pills(bild) == []
    assert len(find_orange_buttons(bild)) == 1


def test_anker_liefert_beide_sorten_getrennt() -> None:
    """Die Lage aus 02-liste.png: erledigt, offen, erledigt."""
    bild = leer()
    knopf(bild, 400, w=154, h=77, farbe=HAKEN_HELL)
    knopf(bild, 700, w=154, h=77, farbe=KNOPF_GEMESSEN)
    knopf(bild, 1000, w=154, h=77, farbe=HAKEN_HELL)

    knoepfe, erledigt = find_anchors(bild)

    assert len(knoepfe) == 1 and len(erledigt) == 2
    assert knoepfe[0].y >= 696
    assert [f.y for f in erledigt] == sorted(f.y for f in erledigt)


def test_die_muenzgrafik_faellt_auch_mit_erledigt_feldern_raus() -> None:
    """Die Groessenpruefung laeuft ueber beide Sorten zusammen -- erst dann hat sie Masse genug."""
    bild = leer()
    knopf(bild, 257, x=494, w=226, h=121, farbe=KNOPF_GEMESSEN)  # Grafik im Fensterkopf
    knopf(bild, 479, w=154, h=77, farbe=HAKEN_HELL)
    knopf(bild, 757, w=154, h=77, farbe=KNOPF_GEMESSEN)
    knopf(bild, 1052, w=154, h=77, farbe=HAKEN_HELL)

    knoepfe, erledigt = find_anchors(bild)

    assert len(knoepfe) == 1, [b.h for b in knoepfe]
    assert len(erledigt) == 2
    assert all(abs(b.h - 77) <= 4 for b in [*knoepfe, *erledigt])
