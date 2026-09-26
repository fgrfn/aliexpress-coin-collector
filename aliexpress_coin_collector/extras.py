"""Zusatzaufgaben der Coin-Seite: hinsehen, auswaehlen, abarbeiten.

Nach einem erfolgreichen Check-in heisst der Knopf an derselben Stelle "Mehr Muenzen verdienen".
Dahinter faehrt ein Fenster hoch -- "Weitere Muenzen verdienen" -- mit einer scrollbaren Liste
kleiner Aufgaben. Jede ist eine Karte aus Titel, Beschreibung, Muenzwert und einem orangen
Knopf "Und los" am rechten Rand. Die meisten verlangen nichts weiter, als sich fuenfzehn
Sekunden auf einer Seite aufzuhalten.

Vier Entscheidungen praegen dieses Modul:

**Karten, keine Zeilen.** Ein Titel wie "In kuerzlich angesehenen Artikeln stoebern" bricht auf
dem Telefon ueber zwei Zeilen um, und was eine Aufgabe ausmacht, steht teils im Titel, teils in
der Beschreibung darunter. Die Knoepfe geben die Grenzen vor: je Karte genau ein "Und los",
also gehoert zu einer Karte, was naeher an ihrem Knopf liegt als am naechsten.

**Positivliste, keine Sperrliste.** Angefasst wird nur, was auf der Positivliste steht. Die
Aufgaben wechseln taeglich; eine Sperrliste waere morgen unvollstaendig, und wir taeten
irgendwann etwas, das niemand wollte -- eine Spielrunde, eine Bewertung, im schlimmsten Fall
"1 x Wasser bei Preisland hinzufuegen". Eine Positivliste laesst im Zweifel eine Aufgabe
liegen. Das ist der guenstigere Fehler. Die Sperrliste gibt es trotzdem, als zweites Netz:
sie gewinnt immer.

**Verglichen wird auf Wortanfang, nicht auf Teilzeichenkette.** Sonst spraeche "kauf" auch auf
"Einkaufsguthaben" an und sperrte eine harmlose Stoeber-Aufgabe.

**Der Check-in ist die Hauptsache.** Hier laeuft nichts, bevor er verbucht ist -- den Knopf
gibt es vorher gar nicht. Findet sich die Liste nach einem Zurueck nicht wieder, wird
abgebrochen statt blind weiterzutippen.
"""

from __future__ import annotations

import logging
import random
import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .adb import Adb, AdbError
from .config import Config

log = logging.getLogger(__name__)


# -- Was wir vom Bildschirm brauchen -----------------------------------------------------------
# Als Protokoll, nicht als Import aus ocr: so bleibt dieses Modul ohne OpenCV und Tesseract
# pruefbar. ocr.Word und ocr.Sheet erfuellen es von selbst.


class Spot(Protocol):
    """Ein erkanntes Wort mit seiner Lage."""

    text: str
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> int: ...

    @property
    def cy(self) -> int: ...


class Sheet(Protocol):
    """Ein Bildschirm, in Woerter zerlegt."""

    words: Sequence[Spot]
    width: int
    height: int
    text: str


class Eyes(Protocol):
    """Die Augen: Screenshot rein, gedeutete Werte raus. Im Betrieb ocr, im Test eine Attrappe."""

    def sheet(self, png: bytes) -> Sheet: ...

    def more_button(self, png: bytes) -> Spot | None: ...

    def coins(self, sheet: Sheet) -> int | None: ...


# -- Reine Logik -------------------------------------------------------------------------------

_NOT_WORD = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    """Kleingeschrieben, ohne Umlaute, ohne Satzzeichen, einfache Leerzeichen.

    Die OCR liest Umlaute je nach Schriftgroesse mal so und mal so; ein Vergleich, der auf sie
    angewiesen ist, traegt nicht. "Artikel durchstoebern!" und "ARTIKEL DURCHSTÖBERN" werden
    hier zur selben Zeichenkette.
    """
    folded = text.replace("ß", "ss")
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _NOT_WORD.sub(" ", folded.lower()).strip()


def hits(key: str, terms: Sequence[str]) -> str | None:
    """Erstes Stichwort, das irgendwo vorkommt -- sonst None.

    Verglichen wird auf Teilzeichenkette, nicht auf Wortanfang: Deutsch setzt zusammen, und
    "durchstoebern" muss "stober" genauso treffen wie "stoebern". Der Preis dafuer ist, dass
    die Sperrliste gelegentlich zu viel trifft -- "Vorteilen" enthaelt "teilen". Das ist die
    richtige Richtung: eine Aufgabe zu viel liegen zu lassen kostet fuenf Muenzen, eine zu
    viel angetippt zu haben kann etwas in den Warenkorb legen.

    Die Stichwoerter muessen dafuer genau gewaehlt sein. Darum steht in der Sperrliste
    "kaufen" und nicht "kauf": sonst traefe es auch "Einkaufsguthaben", und das ist eine
    harmlose Stoeber-Aufgabe.
    """
    for term in terms:
        if term and term in key:
            return term
    return None


@dataclass(frozen=True)
class Line:
    """Eine Zeile: der Text und wo er steht."""

    text: str
    cx: int
    cy: int
    x2: int = 0  # rechter Rand, siehe find_cards

    @property
    def key(self) -> str:
        return normalize(self.text)


def group_lines(words: Sequence[Spot], tolerance: float = 0.6) -> list[Line]:
    """Woerter zu Zeilen zusammenfassen.

    Tesseract liefert Woerter, keine Zeilen. Zusammen gehoert, was auf gleicher Hoehe steht:
    zwei Woerter zaehlen zu derselben Zeile, wenn ihre Mitten weniger als `tolerance` mal die
    uebliche Zeilenhoehe auseinanderliegen. Innerhalb der Zeile wird von links nach rechts
    gelesen.
    """
    if not words:
        return []
    heights = sorted(w.h for w in words)
    typical = heights[len(heights) // 2] or 1
    span = max(1.0, typical * tolerance)

    lines: list[Line] = []
    group: list[Spot] = []

    def flush() -> None:
        if not group:
            return
        ordered = sorted(group, key=lambda w: w.x)
        text = " ".join(w.text for w in ordered).strip()
        if text:
            left, right = ordered[0], ordered[-1]
            lines.append(
                Line(
                    text=text,
                    cx=(left.x + right.x + right.w) // 2,
                    cy=sum(w.cy for w in ordered) // len(ordered),
                    x2=right.x + right.w,
                )
            )

    for word in sorted(words, key=lambda w: w.cy):
        if group and abs(word.cy - group[-1].cy) > span:
            flush()
            group = []
        group.append(word)
    flush()
    return lines


@dataclass(frozen=True)
class Card:
    """Eine Aufgabe: ihr ganzer Text und der Knopf, auf den getippt wird."""

    text: str
    go_x: int
    go_y: int

    @property
    def key(self) -> str:
        return normalize(self.text)

    @property
    def short(self) -> str:
        return self.text if len(self.text) <= 60 else self.text[:57] + "..."


def find_cards(lines: Sequence[Line], go: Sequence[str]) -> list[Card]:
    """Aus Zeilen Karten machen, entlang der "Und los"-Knoepfe.

    Je Karte ein Knopf. Die Grenze zwischen zwei Karten liegt auf halbem Weg zwischen ihren
    Knoepfen; ueber der ersten und unter der letzten wird derselbe Abstand angenommen. Was
    darueber liegt -- Kopfzeile, Muenzstand, der Titel des Fensters -- faellt damit heraus.
    """
    knoepfe = [line for line in lines if hits(line.key, go) is not None]
    if not knoepfe:
        return []
    knoepfe.sort(key=lambda line: line.cy)

    if len(knoepfe) > 1:
        abstaende = [b.cy - a.cy for a, b in zip(knoepfe, knoepfe[1:], strict=False)]
        abstaende.sort()
        ueblich = abstaende[len(abstaende) // 2]
    else:
        ueblich = 400

    cards: list[Card] = []
    for i, knopf in enumerate(knoepfe):
        oben = (knoepfe[i - 1].cy + knopf.cy) // 2 if i else knopf.cy - ueblich // 2
        unten = (knopf.cy + knoepfe[i + 1].cy) // 2 if i + 1 < len(knoepfe) else knopf.cy + ueblich // 2
        body = [line for line in lines if oben <= line.cy < unten]
        text = " ".join(line.text for line in sorted(body, key=lambda line: (line.cy, line.cx)))
        # Rechts der Mitte der Knopfzeile: steht der Knopf allein, liegt der Punkt weiter
        # innen im Knopf; ist die Zeile mit dem Titel verschmolzen -- bei einzeiligen Karten
        # stehen beide auf gleicher Hoehe --, liegt er trotzdem noch auf dem Knopf und nicht
        # auf dem Text. "Und los" im Kartentext stoert nicht: es steht auf keiner Liste.
        if text.strip():
            cards.append(Card(text=text.strip(), go_x=(knopf.cx + knopf.x2) // 2, go_y=knopf.cy))
    return cards


TAKE = "nehmen"
BLOCKED = "gesperrt"
UNKNOWN = "unbekannt"


@dataclass(frozen=True)
class Verdict:
    """Was mit einer Karte geschieht, und warum."""

    card: Card
    ruling: str
    reason: str
    search: bool = False  # verlangt einen eingetippten Suchbegriff

    @property
    def wanted(self) -> bool:
        return self.ruling == TAKE


def judge(
    card: Card,
    allow: Sequence[str],
    deny: Sequence[str],
    search_markers: Sequence[str] = (),
    has_terms: bool = False,
) -> Verdict:
    """Ueber eine Karte entscheiden. Die Sperrliste gewinnt immer.

    Suchaufgaben sind der Sonderfall: sie verlangen nicht nur Verweildauer, sondern ein
    eingetipptes Wort, das im Suchverlauf des Kontos stehen bleibt. Sie werden nur angefasst,
    wenn dafuer ausdruecklich ein Begriff hinterlegt ist.
    """
    key = card.key
    gesperrt = hits(key, deny)
    if gesperrt:
        return Verdict(card, BLOCKED, f"gesperrt durch {gesperrt!r}")
    gesucht = hits(key, search_markers)
    if gesucht:
        if has_terms:
            return Verdict(card, TAKE, f"Suchaufgabe ({gesucht!r}), Begriff hinterlegt", search=True)
        return Verdict(card, UNKNOWN, f"Suchaufgabe ({gesucht!r}), aber kein Suchbegriff hinterlegt")
    erlaubt = hits(key, allow)
    if erlaubt:
        return Verdict(card, TAKE, f"erlaubt durch {erlaubt!r}")
    return Verdict(card, UNKNOWN, "steht auf keiner Liste")


def sift(
    cards: Sequence[Card],
    allow: Sequence[str],
    deny: Sequence[str],
    search_markers: Sequence[str] = (),
    has_terms: bool = False,
) -> list[Verdict]:
    """Alle Karten beurteilen, jede nur einmal -- beim Blaettern sieht man sie mehrfach."""
    out: list[Verdict] = []
    seen: set[str] = set()
    for card in cards:
        if card.key in seen:
            continue
        seen.add(card.key)
        out.append(judge(card, allow, deny, search_markers, has_terms))
    return out


# -- Der Ablauf am Geraet ----------------------------------------------------------------------


@dataclass
class TaskRun:
    """Eine abgearbeitete Aufgabe."""

    text: str
    coins_before: int | None = None
    coins_after: int | None = None
    ok: bool = False
    note: str = ""

    @property
    def gain(self) -> int | None:
        if self.coins_before is None or self.coins_after is None:
            return None
        return self.coins_after - self.coins_before


@dataclass
class ExtrasResult:
    """Was der Ausflug ergeben hat."""

    entered: bool = False
    verdicts: list[Verdict] = field(default_factory=list)
    runs: list[TaskRun] = field(default_factory=list)
    shots: list[Path] = field(default_factory=list)
    coins_before: int | None = None
    coins_after: int | None = None
    note: str = ""

    @property
    def gain(self) -> int | None:
        if self.coins_before is None or self.coins_after is None:
            return None
        return self.coins_after - self.coins_before

    def summary(self) -> str:
        if not self.entered:
            return self.note or "Aufgabenliste nicht erreicht"
        teile = [f"{len(self.verdicts)} Aufgaben gelesen"]
        teile.append(f"{sum(1 for v in self.verdicts if v.wanted)} davon brauchbar")
        if self.runs:
            teile.append(f"{sum(1 for r in self.runs if r.ok)} von {len(self.runs)} erledigt")
        if self.gain is not None:
            teile.append(f"{self.gain:+d} Muenzen")
        return ", ".join(teile)


def _save(shots: Path | None, name: str, png: bytes, into: list[Path]) -> None:
    if shots is None:
        return
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / name
    path.write_bytes(png)
    into.append(path)


def _scroll(adb: Adb, sheet: Sheet, down: bool = True) -> None:
    """Innerhalb des Fensters blaettern, mit Abstand zu Kopfzeile und Rand."""
    x = sheet.width // 2
    weit, nah = int(sheet.height * 0.80), int(sheet.height * 0.40)
    if down:
        adb.swipe(x, weit, x, nah)
    else:
        adb.swipe(x, nah, x, weit)


def explore(
    adb: Adb,
    cfg: Config,
    eyes: Eyes,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    choose: Callable[[list[str]], str] = random.choice,
    *,
    act: bool = False,
    shots: Path | None = None,
) -> ExtrasResult:
    """Die Aufgabenliste oeffnen, lesen und -- wenn `act` -- die erlaubten Aufgaben abarbeiten.

    Ohne `act` wird nichts angetippt ausser dem Knopf, der die Liste oeffnet: ein Blick, mehr
    nicht. So laesst sich sehen, was dort ueberhaupt steht, bevor irgendetwas geschieht.
    """
    result = ExtrasResult()
    frist = monotonic() + cfg.extras_budget_s
    ruhe = max(2, cfg.page_timeout_s // 3)

    png = adb.screenshot()
    _save(shots, "00-coinseite.png", png, result.shots)
    knopf = eyes.more_button(png)
    if knopf is None:
        result.note = (
            "Der Knopf fuer die Zusatzaufgaben war nicht zu finden. Er erscheint erst, wenn der "
            "taegliche Check-in erledigt ist."
        )
        log.info("Zusatzaufgaben: %s", result.note)
        return result

    log.info("Zusatzaufgaben: Knopf gefunden (%r), oeffne die Liste", knopf.text)
    adb.tap(knopf.cx, knopf.cy)
    sleep(ruhe)
    result.entered = True

    # -- Hinsehen: die Liste einmal von oben nach unten durchblaettern -------------------------
    gesehen: list[Card] = []
    blatt: Sheet | None = None
    for runde in range(cfg.extras_scrolls + 1):
        png = adb.screenshot()
        _save(shots, f"{runde + 1:02d}-liste.png", png, result.shots)
        blatt = eyes.sheet(png)
        if result.coins_before is None:
            result.coins_before = eyes.coins(blatt)
        gesehen.extend(find_cards(group_lines(blatt.words), cfg.extras_go))
        if runde < cfg.extras_scrolls:
            _scroll(adb, blatt)
            sleep(ruhe)

    result.verdicts = sift(
        gesehen,
        cfg.extras_allow,
        cfg.extras_deny,
        cfg.extras_search_markers,
        bool(cfg.extras_search_terms),
    )
    log.info("Zusatzaufgaben: %s", result.summary())
    for v in result.verdicts:
        log.debug("  [%s] %s (%s)", v.ruling, v.card.short, v.reason)

    if not act or cfg.extras_max == 0:
        result.note = "nur hingesehen, nichts angetippt"
        _heimweg(adb, sleep)
        return result

    # -- Abarbeiten ----------------------------------------------------------------------------
    if blatt is not None:
        for _ in range(cfg.extras_scrolls):
            _scroll(adb, blatt, down=False)
            sleep(1)

    for verdict in [v for v in result.verdicts if v.wanted][: cfg.extras_max]:
        if monotonic() >= frist:
            result.note = "Zeitbudget aufgebraucht, der Rest bleibt liegen"
            log.info("Zusatzaufgaben: %s", result.note)
            break
        begriff = choose(list(cfg.extras_search_terms)) if verdict.search and cfg.extras_search_terms else None
        lauf = _work(adb, cfg, eyes, sleep, verdict.card, shots, result, begriff)
        result.runs.append(lauf)
        if lauf.note == LOST:
            result.note = "abgebrochen: die Liste war nach dem Zurueck nicht wiederzufinden"
            log.warning("Zusatzaufgaben: %s", result.note)
            break

    png = adb.screenshot()
    _save(shots, "99-ende.png", png, result.shots)
    result.coins_after = eyes.coins(eyes.sheet(png))
    log.info("Zusatzaufgaben fertig: %s", result.summary())
    _heimweg(adb, sleep)
    return result


LOST = "Liste nicht wiedergefunden"


def _heimweg(adb: Adb, sleep: Callable[[float], None]) -> None:
    """Das Fenster wieder schliessen. Misslingt es, ist es kein Beinbruch -- der naechste Lauf
    startet die App ohnehin neu."""
    try:
        adb.back()
        sleep(1)
    except AdbError as exc:  # pragma: no cover - reiner Aufraeumweg
        log.debug("Zusatzaufgaben: Rueckweg misslungen (%s)", exc)


def _in_list(lines: Sequence[Line], go: Sequence[str]) -> bool:
    """Sind wir (wieder) in der Liste? Ein "Und los"-Knopf genuegt als Beleg."""
    return any(hits(line.key, go) is not None for line in lines)


def _work(
    adb: Adb,
    cfg: Config,
    eyes: Eyes,
    sleep: Callable[[float], None],
    wanted: Card,
    shots: Path | None,
    result: ExtrasResult,
    search_term: str | None = None,
) -> TaskRun:
    """Eine einzelne Aufgabe: finden, antippen, dableiben, zurueck, nachzaehlen.

    Bei einer Suchaufgabe wird nach dem Antippen der Begriff eingetippt und abgeschickt --
    erst danach laeuft die Verweildauer, denn gezaehlt wird die Zeit auf der Ergebnisseite.
    """
    lauf = TaskRun(text=wanted.short)

    # Die Karte kann seit dem Hinsehen verrutscht sein -- also frisch nachsehen, notfalls blaettern.
    stelle: Card | None = None
    blatt: Sheet | None = None
    for versuch in range(cfg.extras_scrolls + 1):
        png = adb.screenshot()
        blatt = eyes.sheet(png)
        stelle = next((c for c in find_cards(group_lines(blatt.words), cfg.extras_go) if c.key == wanted.key), None)
        if stelle is not None:
            break
        if versuch < cfg.extras_scrolls:
            _scroll(adb, blatt)
            sleep(1)
    if stelle is None or blatt is None:
        lauf.note = "Karte nicht mehr gefunden"
        log.info("Zusatzaufgabe %r: %s", lauf.text, lauf.note)
        return lauf

    lauf.coins_before = eyes.coins(blatt)
    log.info("Zusatzaufgabe %r: antippen", lauf.text)
    adb.tap(stelle.go_x, stelle.go_y)
    if search_term:
        sleep(max(2, cfg.page_timeout_s // 3))
        log.info("Zusatzaufgabe %r: suche nach %r", lauf.text, search_term)
        adb.text(search_term)
        adb.enter()
        lauf.note = f"gesucht nach {search_term!r}"
    sleep(cfg.extras_dwell_s)

    adb.back()
    sleep(max(2, cfg.page_timeout_s // 3))
    png = adb.screenshot()
    _save(shots, f"task-{len(result.runs) + 1:02d}.png", png, result.shots)
    zurueck = eyes.sheet(png)

    if not _in_list(group_lines(zurueck.words), cfg.extras_go):
        adb.back()
        sleep(2)
        png = adb.screenshot()
        zurueck = eyes.sheet(png)
        if not _in_list(group_lines(zurueck.words), cfg.extras_go):
            lauf.note = LOST
            return lauf

    lauf.coins_after = eyes.coins(zurueck)
    gewinn = lauf.gain
    lauf.ok = True
    fertig = "erledigt" if gewinn is None else f"erledigt, {gewinn:+d} Muenzen"
    lauf.note = f"{fertig} ({lauf.note})" if lauf.note else fertig
    log.info("Zusatzaufgabe %r: %s", lauf.text, lauf.note)
    return lauf
