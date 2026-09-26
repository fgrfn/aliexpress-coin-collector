"""Welche App steht vorn? Die Zeile aus 'dumpsys window' in einen Paketnamen uebersetzen.

Daran haengt die Notbremse der Zusatzaufgaben: verlieren wir die App, darf nicht weiter
gewischt und getippt werden. Greift die Erkennung nicht, zieht die Bremse nie und der Fehler
vom 26.09.2026 kaeme wieder -- das Telefon blaetterte damals durch die Seiten des
Startbildschirms, waehrend der Ausflug seelenruhig die naechste Aufgabe suchte.

Geprueft wird an der echten Ausgabe des Produktivgeraets und an Formen anderer
Android-Versionen. Ein Geraet braucht es dafuer nicht -- die Erkennung ist ein Muster.
"""

from __future__ import annotations

import pytest

from aliexpress_coin_collector.adb import FOCUS_RE

ALIEXPRESS = "com.alibaba.aliexpresshd"

# Wortwoertlich vom Produktivgeraet (Samsung SM-J330FN), 26.09.2026. Das Telefon stand auf dem
# Startbildschirm -- genau der Zustand, in dem die Bremse ziehen muss. `dumpsys window` gibt
# die Zeile zweimal aus, einmal eingerueckt und einmal weniger.
ECHT_LAUNCHER = (
    "    mCurrentFocus=Window{6f5b03a u0 com.sec.android.app.launcher/"
    "com.sec.android.app.launcher.activities.LauncherActivity}\n"
    "  mCurrentFocus=Window{6f5b03a u0 com.sec.android.app.launcher/"
    "com.sec.android.app.launcher.activities.LauncherActivity}\n"
)


def paket(out: str) -> str:
    match = FOCUS_RE.search(out)
    return match.group(1) if match else ""


def test_echte_ausgabe_vom_geraet() -> None:
    assert paket(ECHT_LAUNCHER) == "com.sec.android.app.launcher"


def test_die_bremse_wuerde_am_geraet_ziehen() -> None:
    """Der Kern: auf dem Startbildschirm steht nicht die AliExpress-App vorn."""
    erkannt = paket(ECHT_LAUNCHER)
    assert erkannt and erkannt != ALIEXPRESS


@pytest.mark.parametrize(
    ("zeile", "erwartet"),
    [
        (
            "  mCurrentFocus=Window{1a2b3c u0 com.alibaba.aliexpresshd/com.alibaba.aliexpresshd.module.MainActivity}",
            ALIEXPRESS,
        ),
        # Aeltere Fassungen melden statt mCurrentFocus nur mFocusedApp, und zwar verschachtelt.
        (
            "  mFocusedApp=AppWindowToken{abc token=Token{def ActivityRecord{123 u0 "
            "com.alibaba.aliexpresshd/.module.MainActivity t42}}}",
            ALIEXPRESS,
        ),
        # Ein zweiter Nutzer auf dem Geraet: u10 statt u0.
        ("  mCurrentFocus=Window{1a2b3c u10 com.example.app/com.example.app.Main}", "com.example.app"),
    ],
)
def test_weitere_formen(zeile: str, erwartet: str) -> None:
    assert paket(zeile) == erwartet


@pytest.mark.parametrize(
    "zeile",
    [
        "  mCurrentFocus=null",  # Bildschirm aus, nichts im Vordergrund
        "",  # grep hat nichts gefunden
        "  mCurrentFocus=Window{1a2b3c u0 StatusBar}",  # kein Paketname, kein Schraegstrich
    ],
)
def test_ohne_paketnamen_kommt_nichts_zurueck(zeile: str) -> None:
    """Leer heisst "weiss nicht", und dann wird weitergemacht -- siehe extras._in_app."""
    assert paket(zeile) == ""
