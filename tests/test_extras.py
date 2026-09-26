"""Zusatzaufgaben: Karten bilden, beurteilen, abarbeiten.

Die Aufgabentexte stammen aus echten Screenshots der Coin-Seite vom 26.09.2026 -- nicht aus
Vermutungen. Aendert AliExpress die Formulierungen, fallen die Tests hier und nicht erst im
Betrieb auf.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from aliexpress_coin_collector import extras
from aliexpress_coin_collector.extras import BLOCKED, TAKE, UNKNOWN

# -- Attrappen ---------------------------------------------------------------------------------


@dataclass
class W:
    """Ein Wort, wie ocr.Word es liefert."""

    text: str
    x: int
    y: int
    w: int = 90
    h: int = 30

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


@dataclass
class S:
    """Ein Bildschirm."""

    words: list
    width: int = 720
    height: int = 1280
    text: str = ""


def karte(y: int, *zeilen: str, go: str = "Und los") -> list[W]:
    """Eine Aufgabenkarte: Textzeilen links, der Knopf rechts auf halber Hoehe."""
    out: list[W] = []
    for i, zeile in enumerate(zeilen):
        for j, wort in enumerate(zeile.split()):
            out.append(W(wort, 150 + j * 100, y + i * 50))
    # Der Knopf sitzt rechts, auf halber Kartenhoehe -- wie auf dem Geraet unter dem Titel.
    mitte = y + (len(zeilen) - 1) * 50 + 70
    for j, wort in enumerate(go.split()):
        out.append(W(wort, 530 + j * 70, mitte))
    return out


class Uhr:
    """Eine Uhr, die nur durch Warten laeuft.

    Die Warteschleifen in `explore` sehen nach, warten, sehen wieder nach. Mit einem `sleep`,
    das nichts tut, und einer echten Uhr drehen sie bis zum Zeitlimit leer -- die Tests liefen
    damit halbe Minuten. So haengen beide zusammen wie im Betrieb, nur ohne Wartezeit.
    """

    def __init__(self) -> None:
        self.t = 0.0

    def sleep(self, seconds: float) -> None:
        self.t += seconds

    def now(self) -> float:
        return self.t


class FakeAdb:
    """Bildet nach, was ein Tap bewirkt: der erste oeffnet die Liste, jeder weitere eine
    Aufgabenseite. `back` fuehrt zurueck -- wohin, sagt der Test."""

    def __init__(
        self, start: bytes = b"coin", liste: bytes = b"liste", nach_tap: bytes = b"fremd", nach_back: bytes = b"liste"
    ) -> None:
        self.current = start
        self.liste, self.nach_tap, self.nach_back = liste, nach_tap, nach_back
        self.calls: list[str] = []
        self.taps: list[tuple[int, int]] = []
        self.getippt: list[str] = []

    def screenshot(self) -> bytes:
        self.calls.append("screenshot")
        return self.current

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))
        self.calls.append("tap")
        self.current = self.liste if len(self.taps) == 1 else self.nach_tap

    def swipe(self, x1, y1, x2, y2, ms=400) -> None:
        self.calls.append("swipe")

    def back(self) -> None:
        self.calls.append("back")
        self.current = self.nach_back

    def text(self, value: str) -> None:
        self.getippt.append(value)
        self.calls.append("text")

    def enter(self) -> None:
        self.calls.append("enter")


@dataclass
class FakeEyes:
    """Zu jedem Screenshot ein Bildschirm, ein Muenzstand und ob der Knopf da ist."""

    sheets: dict
    button: W | None = None
    coin_by_png: dict = field(default_factory=dict)
    fallback_coins: int | None = None

    GO_WORDS = {"und", "los", "go", "geht"}

    def sheet(self, png: bytes) -> S:
        """Der normale Durchgang: dunkler Text auf Weiss -- ohne die Knopfbeschriftung."""
        voll = self.sheets.get(png, S(words=[]))
        return S(
            words=[w for w in voll.words if w.text.lower().strip(",.!") not in self.GO_WORDS],
            width=voll.width,
            height=voll.height,
        )

    def bright(self, png: bytes) -> S:
        """Der umgekehrte Durchgang: nur die weisse Schrift auf den orangen Knoepfen."""
        voll = self.sheets.get(png, S(words=[]))
        return S(
            words=[w for w in voll.words if w.text.lower().strip(",.!") in self.GO_WORDS],
            width=voll.width,
            height=voll.height,
        )

    def more_button(self, png: bytes):
        return self.button

    def coins(self, sheet) -> int | None:
        return self.coin_by_png.get(id(sheet), self.fallback_coins)


# -- Reine Logik -------------------------------------------------------------------------------


def test_normalize_faltet_umlaute_und_satzzeichen() -> None:
    assert extras.normalize("ARTIKEL DURCHSTÖBERN!") == "artikel durchstobern"
    assert extras.normalize("Gutscheine & Einkaufsguthaben für Sie!") == "gutscheine einkaufsguthaben fur sie"
    assert extras.normalize("Schließen") == "schliessen"


def test_hits_trifft_nur_am_wortanfang() -> None:
    # Genau darum geht es: "kauf" darf "Einkaufsguthaben" nicht sperren.
    assert extras.hits("jetzt kaufen und sparen", ("kaufen",)) == "kaufen"
    # Deutsch setzt zusammen: "durchstoebern" muss "stober" treffen. Darum Teilzeichenkette --
    # und darum steht in der Sperrliste "kaufen" statt "kauf".
    assert extras.hits("artikel durchstobern", ("stober",)) == "stober"
    assert extras.hits("gutscheine einkaufsguthaben fur sie", ("kaufen",)) is None


def test_group_lines_fasst_gleiche_hoehe_zusammen() -> None:
    lines = extras.group_lines([W("Super", 150, 100), W("Rabatte", 260, 102), W("anzeigen", 380, 480)])
    assert [line.text for line in lines] == ["Super Rabatte", "anzeigen"]


def test_group_lines_ohne_woerter() -> None:
    assert extras.group_lines([]) == []


# Die echten Karten, wie sie am 26.09.2026 auf dem Geraet standen.
ECHTE_KARTEN = [
    ("Tägliche Anmeldung", "Den heutigen Check-in abschließen", BLOCKED),
    ("Gesponserte Artikel entdecken", "Stöbern, shoppen und sparen", TAKE),
    (
        "In kürzlich angesehenen Artikeln stöbern",
        "Münzangebot für zuletzt angesehene Artikel im Warenkorb",
        BLOCKED,  # "Warenkorb" in der Beschreibung -- bewusst vorsichtig, siehe docs/STATUS.md
    ),
    ("Übersicht über Ihre Münzeinsparungen anzeigen", "", TAKE),
    ("Super Rabatte anzeigen", "Surfen Sie 15 Sek. auf dieser Seite, um 5 Münzen zu verdienen", TAKE),
    ("Suchen, was Sie lieben", "Verdienen Sie durch die Nutzung von Schlüsselwörtern", TAKE),
    ("Gutscheine & Einkaufsguthaben für Sie!", "Stöbern Sie auf dieser Seite 15 Sekunden lang", TAKE),
    ("Artikel ab $0.10", "1 x Wasser bei Preisland hinzufügen", BLOCKED),
    ("Schließen Sie 1 Merge-Boss-Spielrunde ab", "Weitere Angebote und noch mehr Spaß!", BLOCKED),
    ("Tagesquiz-Herausforderung", "Schalten Sie Münzbelohnungen im Spiel frei", BLOCKED),
]


@pytest.mark.parametrize(("titel", "beschreibung", "erwartet"), ECHTE_KARTEN)
def test_echte_karten_werden_richtig_beurteilt(cfg, titel, beschreibung, erwartet) -> None:
    card = extras.Card(text=f"{titel} {beschreibung}".strip(), go_x=600, go_y=500)
    urteil = extras.judge(
        card, cfg.extras_allow, cfg.extras_deny, cfg.extras_search_markers, bool(cfg.extras_search_terms)
    )
    assert urteil.ruling == erwartet


def test_suchaufgabe_bleibt_liegen_ohne_hinterlegten_begriff(cfg) -> None:
    card = extras.Card("Suchen, was Sie lieben Verdienen Sie durch die Nutzung von Schlüsselwörtern", 600, 500)
    urteil = extras.judge(card, cfg.extras_allow, cfg.extras_deny, cfg.extras_search_markers, has_terms=False)
    assert urteil.ruling == UNKNOWN
    assert "kein Suchbegriff" in urteil.reason


def test_suchaufgabe_wird_als_solche_erkannt(cfg) -> None:
    card = extras.Card("Suchen, was Sie lieben Verdienen Sie durch die Nutzung von Schlüsselwörtern", 600, 500)
    urteil = extras.judge(card, cfg.extras_allow, cfg.extras_deny, cfg.extras_search_markers, has_terms=True)
    assert urteil.wanted and urteil.search


def test_gesperrte_suchaufgabe_bleibt_gesperrt(cfg) -> None:
    """Die Sperrliste gewinnt auch gegen eine Suchaufgabe."""
    card = extras.Card("Suchen Sie im Merge-Boss-Spiel nach Belohnungen", 600, 500)
    urteil = extras.judge(card, cfg.extras_allow, cfg.extras_deny, cfg.extras_search_markers, has_terms=True)
    assert urteil.ruling == BLOCKED


def test_einkaufsguthaben_bleibt_erlaubt(cfg) -> None:
    """Der Fall, an dem eine stumpfe Teilzeichenkette gescheitert waere."""
    card = extras.Card("Gutscheine & Einkaufsguthaben für Sie! Stöbern Sie auf dieser Seite 15 Sekunden lang", 600, 500)
    urteil = extras.judge(card, cfg.extras_allow, cfg.extras_deny)
    assert urteil.wanted and urteil.reason == "erlaubt durch 'stober'"


def test_find_cards_schneidet_an_den_knoepfen(cfg) -> None:
    woerter = [
        W("Münzen", 200, 120),  # Kopfzeile, gehoert zu keiner Karte
        W("Weitere Münzen verdienen", 100, 300),  # Titel des Fensters
        *karte(450, "Gesponserte Artikel", "entdecken"),
        *karte(750, "Super Rabatte anzeigen"),
    ]
    zeilen = extras.group_lines(woerter)
    cards = extras.find_cards(zeilen, extras.go_lines(zeilen, cfg.extras_go))
    assert len(cards) == 2
    assert "entdecken" in cards[0].text
    assert "Rabatte" in cards[1].text
    # Die Kopfzeile darf in keiner Karte landen.
    assert all("Weitere" not in c.text for c in cards)


def test_einzeilige_karte_verschmilzt_mit_ihrem_knopf(cfg) -> None:
    """Titel und Knopf auf gleicher Hoehe werden eine Zeile -- die Karte darf trotzdem stehen."""
    woerter = [W("Super", 150, 700), W("Rabatte", 260, 700), W("Und", 530, 700), W("los", 600, 700)]
    zeilen = extras.group_lines(woerter)
    cards = extras.find_cards(zeilen, extras.go_lines(zeilen, cfg.extras_go))
    assert len(cards) == 1
    assert "Rabatte" in cards[0].text
    # Getippt wird rechts, auf dem Knopf -- nicht in der Mitte ueber dem Titel.
    assert cards[0].go_x > 500


def test_find_cards_ohne_knoepfe_ergibt_nichts(cfg) -> None:
    zeilen = extras.group_lines([W("Irgendwas", 10, 10)])
    assert extras.find_cards(zeilen, extras.go_lines(zeilen, cfg.extras_go)) == []


def test_einzelner_knopf_zieht_den_fenstertitel_nicht_mit(cfg) -> None:
    """Wird nur ein Knopf gelesen, fehlt der Abstand als Massstab -- die Karte darf nicht wuchern.

    Am 26.09.2026 fand der umgekehrte Durchgang einen von drei Knoepfen, und die eine Karte hiess
    dann "Weitere Münzen verdienen 15s stöbern und sehen, wie viel ..." -- Fenstertitel plus halbe
    Nachbarbeschreibung.
    """
    woerter = [
        W("Weitere", 100, 560),
        W("Münzen", 230, 560),
        W("verdienen", 360, 560),
        *karte(800, "Super Rabatte anzeigen"),
    ]
    zeilen = extras.group_lines(woerter)
    cards = extras.find_cards(zeilen, extras.go_lines(zeilen, cfg.extras_go))
    assert len(cards) == 1
    assert "Weitere" not in cards[0].text
    assert "Rabatte" in cards[0].text


def test_werbefenster_wird_weggetippt(cfg) -> None:
    """Beim Verlassen schiebt die App ein Fenster davor. Getippt wird "Bleiben", nie "Verlassen"."""
    dialog = S(
        words=[
            W("Nicht", 150, 300),
            W("vergessen", 280, 300),
            W("Verlassen", 120, 1100),
            W("Bleiben", 450, 1100),
        ]
    )
    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))

    class MitFenster(FakeAdb):
        def tap(self, x: int, y: int) -> None:
            self.taps.append((x, y))
            self.calls.append("tap")
            # Erster Tap oeffnet die Liste -- aber das Werbefenster liegt davor.
            self.current = b"dialog" if len(self.taps) == 1 else b"liste"

    adb = MitFenster()
    eyes = FakeEyes(sheets={b"dialog": dialog, b"liste": liste}, button=W("verdienen", 200, 560))
    uhr = Uhr()

    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=False)

    # Zweiter Tap traf "Bleiben" (rechts), nicht "Verlassen" (links).
    assert len(adb.taps) >= 2
    assert adb.taps[1][0] > 400
    assert len(result.verdicts) == 1


def test_sift_zaehlt_jede_karte_nur_einmal(cfg) -> None:
    eine = extras.Card("Super Rabatte anzeigen", 600, 100)
    nochmal = extras.Card("Super Rabatte anzeigen", 600, 900)  # beim Blaettern wieder gesehen
    assert len(extras.sift([eine, nochmal], cfg.extras_allow, cfg.extras_deny)) == 1


# -- Ablauf ------------------------------------------------------------------------------------


def test_ohne_knopf_passiert_nichts(cfg) -> None:
    adb = FakeAdb()
    uhr = Uhr()
    result = extras.explore(adb, cfg, FakeEyes(sheets={}, button=None), sleep=uhr.sleep, monotonic=uhr.now)
    assert not result.entered
    assert "Check-in" in result.note
    assert adb.taps == []


def test_wartet_nur_bis_der_knopf_da_ist(cfg) -> None:
    """PAGE_TIMEOUT_S ist die Obergrenze, nicht die Wartezeit.

    Vorher schlief der Befehl stur die volle Zeitspanne -- bei 90 Sekunden Zeitlimit also anderthalb
    Minuten, auch wenn die Seite nach zehn Sekunden stand.
    """
    from dataclasses import replace

    cfg = replace(cfg, page_timeout_s=90)
    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))
    adb = FakeAdb()

    class Spaet(FakeEyes):
        blicke = 0

        def more_button(self, png: bytes):
            Spaet.blicke += 1
            return W("verdienen", 200, 560) if Spaet.blicke >= 3 else None

    uhr = Uhr()
    result = extras.explore(adb, cfg, Spaet(sheets={b"liste": liste}), sleep=uhr.sleep, monotonic=uhr.now, act=False)

    assert result.entered
    assert uhr.now() < 30, f"zu lange gewartet: {uhr.now()} s"


def test_hinsehen_tippt_nur_den_knopf(cfg) -> None:
    liste = S(words=[*karte(450, "Gesponserte Artikel entdecken"), *karte(800, "Merge-Boss-Spielrunde")])
    adb = FakeAdb()
    eyes = FakeEyes(sheets={b"liste": liste}, button=W("verdienen", 200, 560))

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=False)

    assert result.entered
    assert len(adb.taps) == 1  # nur der Knopf, der die Liste oeffnet
    urteile = {v.card.short.split()[0]: v.ruling for v in result.verdicts}
    assert urteile["Gesponserte"] == TAKE
    assert urteile["Merge-Boss-Spielrunde"] == BLOCKED
    assert result.note == "nur hingesehen, nichts angetippt"


def test_ohne_umgekehrten_durchgang_bleiben_die_karten_unsichtbar(cfg) -> None:
    """Der Fehler vom 26.09.2026: die Knoepfe stehen weiss auf orange und wurden nicht gelesen.

    Ohne sie faellt die ganze Liste weg, denn an ihnen werden die Karten geschnitten -- im
    Betrieb kamen alle Kartentitel durch und trotzdem "0 Aufgaben gelesen".
    """

    class Blind(FakeEyes):
        def bright(self, png: bytes) -> S:
            return S(words=[])

    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))
    adb = FakeAdb()
    eyes = Blind(sheets={b"liste": liste}, button=W("verdienen", 200, 560))

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=False)

    assert result.entered and result.verdicts == []


def test_muenzstand_kommt_von_der_coinseite(cfg) -> None:
    """In der Liste liegt das Fenster ueber der Kopfzeile -- dort ist die Zahl nicht lesbar."""
    coin = S(words=[W("140", 230, 120)])
    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))
    adb = FakeAdb()
    eyes = FakeEyes(sheets={b"coin": coin, b"liste": liste}, button=W("verdienen", 200, 560))
    # Nur die Coin-Seite gibt einen Stand her, die Liste nicht.
    eyes.coins = lambda sheet: 140 if any(w.text == "140" for w in sheet.words) else None  # type: ignore[assignment]

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=False)

    assert result.coins_before == 140


def test_abarbeiten_tippt_den_knopf_der_karte_und_zaehlt_nach(cfg) -> None:
    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))
    adb = FakeAdb()  # Tap auf die Karte oeffnet eine fremde Seite, back fuehrt zurueck
    eyes = FakeEyes(sheets={b"liste": liste}, button=W("verdienen", 200, 560))
    # Vorher 140, hinterher 145.
    staende = iter([140, 140, 145, 145, 145])
    eyes.coins = lambda sheet: next(staende, 145)  # type: ignore[assignment]

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=True)

    assert [r.ok for r in result.runs] == [True]
    assert result.runs[0].gain == 5
    assert "back" in adb.calls
    # Getippt wird auf den Knopf rechts (x um 560), nicht auf den Titel links.
    knopf_taps = [t for t in adb.taps[1:]]
    assert knopf_taps and all(x > 500 for x, _ in knopf_taps)


def test_suchaufgabe_tippt_den_begriff_und_schickt_ab(cfg) -> None:
    from dataclasses import replace

    cfg = replace(cfg, extras_search_terms=("Jayo PETG 1.1KG",))
    liste = S(words=karte(450, "Suchen, was Sie lieben", "Nutzung von Schlüsselwörtern"))
    adb = FakeAdb()
    eyes = FakeEyes(sheets={b"liste": liste}, button=W("verdienen", 200, 560), fallback_coins=140)

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, choose=lambda terms: terms[0], act=True)

    assert adb.getippt == ["Jayo PETG 1.1KG"]
    assert adb.calls.count("enter") == 1
    assert "Jayo PETG 1.1KG" in result.runs[0].note


def test_ohne_suchbegriff_wird_nichts_getippt(cfg) -> None:
    from dataclasses import replace

    cfg = replace(cfg, extras_search_terms=())
    liste = S(words=karte(450, "Suchen, was Sie lieben", "Nutzung von Schlüsselwörtern"))
    adb = FakeAdb()
    eyes = FakeEyes(sheets={b"liste": liste}, button=W("verdienen", 200, 560), fallback_coins=140)

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=True)

    assert adb.getippt == []
    assert result.runs == []  # die Karte gilt als unbekannt und wird nicht angefasst


def test_abbruch_wenn_die_liste_nach_dem_zurueck_fehlt(cfg) -> None:
    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))
    fremd = S(words=[W("Irgendeine", 100, 300), W("Produktseite", 100, 400)])
    # Nach dem Tippen fuehrt auch back nicht mehr in die Liste zurueck.
    adb = FakeAdb(nach_back=b"fremd")
    eyes = FakeEyes(sheets={b"liste": liste, b"fremd": fremd}, button=W("verdienen", 200, 560), fallback_coins=140)

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=True)

    assert result.runs[0].note == extras.LOST
    assert "abgebrochen" in result.note


def test_extras_max_null_sieht_nur_hin(cfg, monkeypatch) -> None:
    from dataclasses import replace

    cfg = replace(cfg, extras_max=0)
    liste = S(words=karte(450, "Gesponserte Artikel entdecken"))
    adb = FakeAdb()
    eyes = FakeEyes(sheets={b"liste": liste}, button=W("verdienen", 200, 560))

    uhr = Uhr()
    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=True)

    assert result.runs == []
    assert len(adb.taps) == 1


def test_zeitbudget_bricht_ab(cfg) -> None:
    from dataclasses import replace

    # Null Sekunden Budget: das Hinsehen kostet schon Zeit, danach ist die Frist um.
    cfg = replace(cfg, extras_max=5, extras_budget_s=0)
    liste = S(words=[*karte(300, "Gesponserte Artikel entdecken"), *karte(700, "Super Rabatte anzeigen")])
    adb = FakeAdb()
    eyes = FakeEyes(sheets={b"liste": liste}, button=W("verdienen", 200, 560), fallback_coins=140)
    uhr = Uhr()

    result = extras.explore(adb, cfg, eyes, sleep=uhr.sleep, monotonic=uhr.now, act=True)

    assert result.runs == []
    assert "Zeitbudget" in result.note


def test_summary_bleibt_lesbar(cfg) -> None:
    result = extras.ExtrasResult(entered=True, coins_before=140, coins_after=150)
    result.verdicts = [extras.judge(extras.Card("Super Rabatte anzeigen", 1, 1), cfg.extras_allow, cfg.extras_deny)]
    assert "1 Aufgaben gelesen" in result.summary()
    assert "+10 Muenzen" in result.summary()
