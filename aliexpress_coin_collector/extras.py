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

**Verglichen wird auf Teilzeichenkette, nicht auf Wortanfang.** Deutsch setzt zusammen:
"durchstoebern" muss "stober" treffen. Dafuer muessen die Stichwoerter genau gewaehlt sein --
in der Sperrliste steht "kaufen" und nicht "kauf", sonst traefe es "Einkaufsguthaben".

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
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .adb import Adb, AdbError
from .config import Config
from .store import Task

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

    def go_spots(self, png: bytes) -> Sequence[Spot]: ...

    def done_spots(self, png: bytes) -> Sequence[Spot]: ...

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
    # Traegt die Karte statt des Knopfes ein blassgruenes Feld mit Haken? Dann ist die Aufgabe
    # schon abgeholt, und `go_x`/`go_y` zeigen auf dieses Feld -- angetippt wird es nie.
    done: bool = False

    @property
    def key(self) -> str:
        return normalize(self.text)

    @property
    def short(self) -> str:
        return self.text if len(self.text) <= 60 else self.text[:57] + "..."


def go_lines(lines: Sequence[Line], go: Sequence[str]) -> list[Line]:
    """Die Zeilen, die eine Knopfbeschriftung tragen."""
    return [line for line in lines if hits(line.key, go) is not None]


def find_cards(lines: Sequence[Line], knoepfe: Sequence[Line], erledigt: Sequence[Line] = ()) -> list[Card]:
    """Aus Zeilen Karten machen, entlang der Anker am rechten Rand.

    Je Karte genau ein Anker. Die Grenze zwischen zwei Karten liegt auf halbem Weg zwischen
    ihren Ankern; ueber der ersten und unter der letzten wird derselbe Abstand angenommen. Was
    darueber liegt -- Kopfzeile, Muenzstand, der Titel des Fensters -- faellt damit heraus.

    **Zwei Sorten Anker**, und das ist der Kern: eine offene Aufgabe traegt den orangen Knopf
    "Und los", eine erledigte ein blassgruenes Feld mit Haken. Bis 0.19.5 zaehlten nur die
    Knoepfe -- eine erledigte Karte hatte damit keinen eigenen Anker, und ihr Text fiel in die
    Karte darunter. Daher der Name "Gesponserte Artikel entdecken" auf einer Karte, die in
    Wahrheit "In kuerzlich angesehenen Artikeln stoebern" war (am Geraet gesehen, 26.09.2026).

    Die Ankerzeilen kommen von aussen, weil sie aus einem anderen Durchgang stammen: gefunden
    werden sie ueber ihre Farbe, die Kartentexte ueber die Texterkennung.
    """
    erledigt_y = {line.cy for line in erledigt}
    knoepfe = sorted([*knoepfe, *erledigt], key=lambda line: line.cy)
    if not knoepfe:
        return []

    if len(knoepfe) > 1:
        abstaende = [b.cy - a.cy for a, b in zip(knoepfe, knoepfe[1:], strict=False)]
        abstaende.sort()
        ueblich = abstaende[len(abstaende) // 2]
    else:
        ueblich = LONE_CARD_SPAN

    # Zwischen zwei Knoepfen liegt die Grenze auf halbem Weg. Ueber dem ersten und unter dem
    # letzten gilt derselbe Abstand -- eine ganze Kartenhoehe waere doppelt so weit und zieht
    # herein, was darueber steht. Genau daran hing am 26.09.2026 der Fenstertitel in der ersten
    # Karte: "Weitere Muenzen verdienen Gesponserte Artikel entdecken ...".
    rand = max(1, ueblich // 2)

    cards: list[Card] = []
    for i, knopf in enumerate(knoepfe):
        oben = (knoepfe[i - 1].cy + knopf.cy) // 2 if i else knopf.cy - rand
        unten = (knopf.cy + knoepfe[i + 1].cy) // 2 if i + 1 < len(knoepfe) else knopf.cy + rand
        # Sicherheitsnetz gegen ungleiche Abstaende: nie weiter als eine Kartenhoehe.
        oben, unten = max(oben, knopf.cy - ueblich), min(unten, knopf.cy + ueblich)
        body = [line for line in lines if oben <= line.cy < unten]
        text = " ".join(line.text for line in sorted(body, key=lambda line: (line.cy, line.cx)))
        # Rechts der Mitte der Knopfzeile: steht der Knopf allein, liegt der Punkt weiter
        # innen im Knopf; ist die Zeile mit dem Titel verschmolzen -- bei einzeiligen Karten
        # stehen beide auf gleicher Hoehe --, liegt er trotzdem noch auf dem Knopf und nicht
        # auf dem Text. "Und los" im Kartentext stoert nicht: es steht auf keiner Liste.
        if text.strip():
            cards.append(
                Card(
                    text=text.strip(),
                    go_x=(knopf.cx + knopf.x2) // 2,
                    go_y=knopf.cy,
                    done=knopf.cy in erledigt_y,
                )
            )
    return cards


# Wird nur ein einziger Knopf gefunden, fehlt der Abstand zum naechsten als Massstab. Der Wert
# ist eine typische Kartenhoehe -- am Geraet lagen die Knoepfe 278 und 295 Pixel auseinander.
LONE_CARD_SPAN = 280


TAKE = "nehmen"
BLOCKED = "gesperrt"
UNKNOWN = "unbekannt"
ALREADY = "schon erledigt"

# Der Zaehler auf der Karte: "2/2" heisst zweimal von zwei gemacht, "0/3" noch keinmal von drei.
# Gelesen wird er aus dem rohen Text, nicht aus `key`: `normalize` wirft den Schraegstrich weg.
_COUNT_RE = re.compile(r"(?<!\d)(\d{1,2})\s*/\s*(\d{1,2})(?!\d)")
# Mehr als so oft laesst sich keine Aufgabe machen -- was groesser ist, ist etwas anderes.
COUNT_MAX = 20


# Wie ein Urteil auf der Kommandozeile aussieht. Steht hier und nicht im CLI-Modul, damit ein
# neues Urteil nicht an zwei Stellen vergessen wird -- ein fehlender Eintrag warf frueher einen
# KeyError mitten in der Ausgabe.
SIGNS = {TAKE: "+", BLOCKED: "-", UNKNOWN: "?", ALREADY: "="}


# Der Muenzwert auf der Karte, "+5". Eine Spanne ("+1~5" beim Tagesquiz) zaehlt nicht: was
# dabei herauskommt, steht nicht fest.
_REWARD_RE = re.compile(r"\+\s*(\d{1,3})(?![\d\s]*[~\u301c\uff5e-])")
# Mehr als so viele Muenzen gibt keine dieser Aufgaben her -- was groesser ist, ist etwas anderes.
REWARD_MAX = 99


def reward(text: str) -> int | None:
    """Was die Karte an Muenzen verspricht, oder None.

    **Versprochen, nicht gemessen.** Gemessen waere schoener, geht aber nicht: der Muenzstand
    steht in der Kopfzeile, und die liegt unter dem Aufgabenfenster. Am 26.09.2026 gab
    `acc ocr 02-liste.png` dort `Muenzstand: None` -- jeder Versuch, den Zuwachs je Aufgabe aus
    der Liste zu lesen, ergibt darum nichts. Der Wert auf der Karte dagegen ist gut lesbar.
    """
    for treffer in _REWARD_RE.finditer(text):
        wert = int(treffer.group(1))
        if 1 <= wert <= REWARD_MAX:
            return wert
    return None


def progress(text: str) -> tuple[int, int] | None:
    """Der Zaehler der Karte als (gemacht, moeglich), oder None.

    Vorsichtig gelesen: eine Zahl ueber `COUNT_MAX` oder ein Stand ueber dem Moeglichen ist
    kein Zaehler, sondern etwas anderes, das zufaellig einen Schraegstrich enthaelt. Im Zweifel
    lieber keinen Zaehler erkennen -- dann verhaelt sich alles wie vorher.
    """
    for treffer in _COUNT_RE.finditer(text):
        gemacht, moeglich = int(treffer.group(1)), int(treffer.group(2))
        if 1 <= moeglich <= COUNT_MAX and 0 <= gemacht <= moeglich:
            return gemacht, moeglich
    return None


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

    Ob eine Aufgabe schon abgeholt ist, sagt der **Haken**, nicht der Zaehler: "Super Rabatte
    anzeigen" trug 1/3 und war offen, "Uebersicht ueber Ihre Muenzeinsparungen" gar keine Zahl
    und war erledigt (am Geraet gesehen, 26.09.2026). Der Zaehler wird nur gelesen und
    angezeigt -- er entscheidet nichts.
    """
    key = card.key
    zaehler = progress(card.text)
    wert = reward(card.text)
    teile = [f"{zaehler[0]}/{zaehler[1]}" if zaehler else "", f"+{wert}" if wert else ""]
    stand = " (" + ", ".join(t for t in teile if t) + ")" if any(teile) else ""

    gesperrt = hits(key, deny)
    if gesperrt:
        return Verdict(card, BLOCKED, f"gesperrt durch {gesperrt!r}")
    if card.done:
        return Verdict(card, ALREADY, f"schon abgeholt{stand}")
    gesucht = hits(key, search_markers)
    if gesucht:
        if has_terms:
            return Verdict(card, TAKE, f"Suchaufgabe ({gesucht!r}), Begriff hinterlegt{stand}", search=True)
        return Verdict(card, UNKNOWN, f"Suchaufgabe ({gesucht!r}), aber kein Suchbegriff hinterlegt{stand}")
    erlaubt = hits(key, allow)
    if erlaubt:
        return Verdict(card, TAKE, f"erlaubt durch {erlaubt!r}{stand}")
    return Verdict(card, UNKNOWN, f"steht auf keiner Liste{stand}")


# Wie stark sich zwei Kartentexte ueberschneiden muessen, um als dieselbe Karte zu gelten.
SAME_CARD_OVERLAP = 0.6
# Kurze Woerter tragen nichts zur Unterscheidung bei ("und", "sie", "auf").
TOKEN_MIN_LEN = 4
# Unter so vielen Woertern ist ein Anteil kein Mass mehr, sondern Zufall.
TOKEN_QUORUM = 3


def _tokens(key: str) -> set[str]:
    return {t for t in key.split() if len(t) >= TOKEN_MIN_LEN}


def same_card(a: str, b: str) -> bool:
    """Sind das zwei Lesungen derselben Karte?

    Beim Blaettern sieht man jede Karte mehrfach, und die Texterkennung liest sie jedes Mal
    etwas anders: aus "Uebersicht ueber Ihre Muenzeinsparungen" wurde beim zweiten Mal "Ds
    Uebersicht ueber Ihre Muenzeinsparungen", aus "Suchen, was Sie lieben" wurde "Weitere
    Muenzen verdienen wa> Verdienen Sie durch die Nutzung ...". Auf Gleichheit zu vergleichen
    zaehlt dieselbe Aufgabe darum mehrfach -- und mit --los wuerde sie zweimal angetippt.

    Verglichen wird der Anteil gemeinsamer Woerter am kleineren der beiden Texte. Bei sehr
    kurzen Texten faellt das auf Gleichheit zurueck: bei zwei Woertern waere ein Anteil Zufall.
    """
    ta, tb = _tokens(a), _tokens(b)
    if min(len(ta), len(tb)) < TOKEN_QUORUM:
        return a == b
    return len(ta & tb) / min(len(ta), len(tb)) >= SAME_CARD_OVERLAP


def sift(
    cards: Sequence[Card],
    allow: Sequence[str],
    deny: Sequence[str],
    search_markers: Sequence[str] = (),
    has_terms: bool = False,
) -> list[Verdict]:
    """Alle Karten beurteilen, jede nur einmal -- beim Blaettern sieht man sie mehrfach."""
    out: list[Verdict] = []
    for card in cards:
        if any(same_card(card.key, v.card.key) for v in out):
            continue
        out.append(judge(card, allow, deny, search_markers, has_terms))
    return out


# Der Suchbalken steht ganz oben. Unterhalb beginnen schon die Vorschlaege, oberhalb liegt
# die Statuszeile mit Uhr und Akku -- beides darf nicht mitzaehlen.
SEARCH_BAND = (0.04, 0.16)
# Waagrecht wird in der Mitte des Feldes getippt: links sitzt der Zurueckpfeil, rechts Kamera
# und der Knopf "Suchen". Auf Letzteren darf nicht getippt werden -- er schickt den alten
# Begriff ab, und genau das sah am 26.09.2026 nach einer erfolgreichen Suche aus.
SEARCH_FIELD_X = 0.40


def search_field(sheet: Sheet) -> tuple[int, int]:
    """Wo getippt werden muss, damit das Suchfeld die Eingabe bekommt.

    Waagrecht steht die Stelle fest, als Anteil der Breite. Senkrecht wird sie an einem Wort im
    Balken festgemacht, wenn eines zu lesen ist -- meist der alte Begriff. Steht das Feld leer
    und die Erkennung findet nichts, bleibt die Mitte des Balkens.
    """
    oben = int(sheet.height * SEARCH_BAND[0])
    unten = int(sheet.height * SEARCH_BAND[1])
    x = int(sheet.width * SEARCH_FIELD_X)
    im_balken = [w.cy for w in sheet.words if oben <= w.cy <= unten and w.cx < sheet.width * 0.75]
    return x, min(im_balken, default=(oben + unten) // 2)


def typed_ok(screen: str, term: str) -> bool:
    """Steht der Begriff im Feld?

    Verlangt wird nicht der ganze: die Schrift im Suchfeld ist klein, und die Erkennung
    verliest sich an "1.1KG" leichter als an "Jayo". Ein Wort von drei Zeichen reicht. Der
    Fall, um den es geht, ist eindeutig -- steht dort noch der alte Begriff des Nutzers,
    trifft keines.
    """
    key = normalize(screen)
    if not key:
        return False
    tokens = [t for t in normalize(term).split() if len(t) >= 3]
    return any(t in key for t in tokens) if tokens else normalize(term) in key


# -- Der Ablauf am Geraet ----------------------------------------------------------------------


@dataclass
class TaskRun:
    """Eine abgearbeitete Aufgabe."""

    text: str
    coins_before: int | None = None
    coins_after: int | None = None
    ok: bool = False
    note: str = ""
    # Wie oft die Aufgabe in diesem Ausflug abgearbeitet wurde. Manche gehen mehrfach.
    times: int = 0

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


def to_tasks(result: ExtrasResult, now: datetime) -> list[Task]:
    """Aus einem Ausflug die Zeilen fuer die Datenbank -- eine je gesehener Aufgabe.

    Auch die gesperrten kommen mit. In der Tagesliste soll stehen, was bewusst liegen blieb,
    nicht nur was geklappt hat: sonst sieht ein Tag mit zwei Aufgaben genauso aus wie einer,
    an dem acht dastanden und sechs davon nichts fuer uns waren.

    Zugeordnet wird ueber `Card.short`, nicht ueber die Reihenfolge: seit es Wiederholungen
    gibt, bleibt zwar eine Zeile je Aufgabe, aber eine Aufgabe kann ganz ohne Lauf dastehen
    (Zeitbudget alle) -- dann verschoebe sich alles Folgende um eins.
    """
    out: list[Task] = []
    nach_text = {lauf.text: lauf for lauf in result.runs}
    for v in result.verdicts:
        lauf = nach_text.get(v.card.short)
        out.append(
            Task(
                ts=now,
                text=v.card.short,
                ruling=v.ruling,
                reason=v.reason,
                done=bool(lauf and lauf.ok),
                note=lauf.note if lauf else "",
                gain=lauf.gain if lauf else None,
            )
        )
    return out


def _save(shots: Path | None, name: str, png: bytes, into: list[Path]) -> None:
    if shots is None:
        return
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / name
    path.write_bytes(png)
    into.append(path)


# Zwischen zwei Blicken auf den Bildschirm. Jeder kostet eine Texterkennung, bei der Knopfsuche
# bis zu vier -- haeufiger nachsehen bringt darum nichts.
POLL_S = 3
# Nach einem Wisch muss die Liste nur zur Ruhe kommen, nicht laden.
SCROLL_SETTLE_S = 2
# So lange darf das Fenster brauchen, bis Karten darin stehen.
LIST_TIMEOUT_S = 25
# So lange darf die Suchseite brauchen, bevor getippt wird.
SEARCH_LOAD_S = 8
# Nach dem Antippen des Feldes faehrt die Tastatur hoch. Wer vorher tippt, tippt ins Leere.
SEARCH_FOCUS_S = 2
# Zwischen Eintippen und Nachsehen. Das Feld selbst steht sofort, die Vorschlagsliste laedt.
SEARCH_TYPE_S = 2
# So viele Zeichen werden vor dem Tippen geloescht. Ein Begriff darf 40 lang sein.
SEARCH_CLEAR_KEYS = 48
# So lange darf die Liste brauchen, bis sie nach einem Zurueck wieder da ist. Das Fenster faehrt
# hoch, und auf einem langsamen Geraet dauert das -- zwei Sekunden reichten nicht.
BACK_TIMEOUT_S = 20
# So oft wird beim Suchen einer Karte hoechstens geblaettert. Mehr als beim Hinsehen: dort
# reichen drei Runden, um die Liste einmal zu sehen, hier muss eine bestimmte Karte gefunden
# werden -- und zwar von ganz oben.
FIND_SCROLLS = 8


def _await(
    adb: Adb,
    look: Callable[[bytes], object | None],
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    timeout: float,
) -> tuple[object | None, bytes]:
    """Nachsehen, bis `look` etwas hergibt oder die Zeit um ist.

    Wartet nicht stur eine Frist ab, sondern nur so lange wie noetig: die Coin-Seite steht auf
    einem langsamen Geraet nach zehn Sekunden, nicht nach neunzig. Gibt das Gesehene und den
    letzten Screenshot zurueck -- Letzteren auch im Fehlerfall, damit er abgelegt werden kann.
    """
    deadline = monotonic() + timeout
    while True:
        png = adb.screenshot()
        found = look(png)
        if found:
            return found, png
        if monotonic() >= deadline:
            return None, png
        sleep(POLL_S)


def _scroll(adb: Adb, sheet: Sheet, down: bool = True) -> None:
    """Innerhalb des Fensters blaettern, mit Abstand zu Kopfzeile und Rand."""
    x = sheet.width // 2
    weit, nah = int(sheet.height * 0.80), int(sheet.height * 0.40)
    if down:
        adb.swipe(x, weit, x, nah)
    else:
        adb.swipe(x, nah, x, weit)


LEFT = "abgebrochen: wir waren nicht mehr in der App"
NO_LIST = "abgebrochen: die Aufgabenliste kam nicht"


def _in_app(adb: Adb, cfg: Config) -> bool:
    """Sind wir noch in der AliExpress-App?

    Die Notbremse. Verlieren wir die App -- sie stuerzt ab, ein Zurueck zu viel, ein Tap ins
    Leere --, dann zeigt der Bildschirm irgendetwas, und jeder weitere Wisch trifft etwas
    Fremdes. Am 26.09.2026 blaetterte das Telefon danach durch die Seiten des
    Startbildschirms, waehrend der Ausflug seelenruhig die naechste Aufgabe suchte.

    Gefragt wird nur, wenn ohnehin keine Karte zu sehen ist: stehen Karten da, sind wir in der
    Liste, und der Shell-Aufruf waere verschenkt. Laesst sich die Frage nicht beantworten,
    gilt sie als beantwortet mit ja -- eine Notbremse, die bei jeder Unsicherheit zieht,
    haelt den ganzen Ausflug auf.
    """
    paket = adb.current_package()
    if paket and paket != cfg.app_package:
        log.warning(
            "Zusatzaufgaben: vorne steht %r, nicht %r -- hier wird nichts mehr angetippt", paket, cfg.app_package
        )
        return False
    return True


def _look(adb: Adb, eyes: Eyes, cfg: Config, sleep: Callable[[float], None]) -> tuple[list[Card], Sheet, bytes]:
    """Nachsehen, was auf dem Schirm steht -- und ein Werbefenster vorher wegtippen.

    Nur wenn gar keine Karte zu sehen ist: liegt etwas davor, sind es keine, und dann lohnt der
    Blick nach dem "Bleiben"-Knopf. Stehen Karten da, wird nichts angetippt.
    """
    png = adb.screenshot()
    cards, blatt = cards_on(eyes, png, cfg)
    if not cards and _dismiss(adb, cfg, blatt, sleep):
        png = adb.screenshot()
        cards, blatt = cards_on(eyes, png, cfg)
    return cards, blatt, png


def _await_cards(
    adb: Adb,
    eyes: Eyes,
    cfg: Config,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    timeout: float,
) -> tuple[list[Card], Sheet, bytes]:
    """Warten, bis Karten zu sehen sind -- hoechstens `timeout` lang."""
    deadline = monotonic() + timeout
    while True:
        cards, blatt, png = _look(adb, eyes, cfg, sleep)
        if cards or monotonic() >= deadline:
            return cards, blatt, png
        sleep(POLL_S)


def _to_top(adb: Adb, sheet: Sheet, sleep: Callable[[float], None], rounds: int) -> None:
    """Die Liste ganz nach oben schieben.

    Ohne das sucht jede Aufgabe von der Stelle aus, an der die vorige sie hinterlassen hat --
    und weil nur nach unten geblaettert wird, ist alles darueber unerreichbar. Am 26.09.2026
    wurden so drei von fuenf Aufgaben "nicht mehr gefunden", obwohl sie in der Liste standen.
    """
    for _ in range(rounds):
        _scroll(adb, sheet, down=False)
        sleep(SCROLL_SETTLE_S)


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

    # Auf den Knopf warten, nicht auf die Uhr: PAGE_TIMEOUT_S ist die Obergrenze, nicht die
    # Wartezeit. Steht die Seite nach zehn Sekunden, geht es nach zehn Sekunden weiter.
    knopf, png = _await(adb, eyes.more_button, sleep, monotonic, cfg.page_timeout_s)
    _save(shots, "00-coinseite.png", png, result.shots)
    # Der Muenzstand wird hier gelesen, nicht in der Liste: dort liegt das Fenster ueber der
    # Kopfzeile, sie ist abgedunkelt und die Zahl kommt nicht mehr durch.
    result.coins_before = eyes.coins(eyes.sheet(png))
    if knopf is None:
        result.note = (
            "Der Knopf fuer die Zusatzaufgaben war nicht zu finden. Er erscheint erst, wenn der "
            "taegliche Check-in erledigt ist."
        )
        log.info("Zusatzaufgaben: %s", result.note)
        return result

    log.info("Zusatzaufgaben: Knopf gefunden (%r), oeffne die Liste", knopf.text)
    adb.tap(knopf.cx, knopf.cy)
    result.entered = True

    # -- Hinsehen: die Liste einmal von oben nach unten durchblaettern -------------------------
    gesehen: list[Card] = []
    blatt: Sheet | None = None
    for runde in range(cfg.extras_scrolls + 1):
        if runde == 0:
            # Erste Runde: warten, bis das Fenster oben ist und Karten zeigt.
            neue, blatt, png = _await_cards(adb, eyes, cfg, sleep, monotonic, LIST_TIMEOUT_S)
        else:
            neue, blatt, png = _look(adb, eyes, cfg, sleep)
        log.debug("Zusatzaufgaben: Runde %d, %d Karten", runde + 1, len(neue))
        _save(shots, f"{runde + 1:02d}-liste.png", png, result.shots)
        gesehen.extend(neue)
        if not neue and not _in_app(adb, cfg):
            result.note = LEFT
            _heimweg(adb, cfg)
            return result
        if runde == 0 and not neue:
            # Die Liste ist in der ganzen Wartezeit nicht gekommen. Weiterzuwischen hiesse, auf
            # einem Bildschirm zu wischen, den wir nicht erkannt haben -- und wohin ein Wisch
            # dort traegt, weiss niemand. Der abgelegte Screenshot sagt hinterher, was dastand.
            result.note = NO_LIST
            log.warning("Zusatzaufgaben: %s (siehe 01-liste.png)", result.note)
            _heimweg(adb, cfg)
            return result
        if runde < cfg.extras_scrolls:
            _scroll(adb, blatt)
            sleep(SCROLL_SETTLE_S)

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
        _abschluss(adb, eyes, cfg, sleep, shots, result)
        return result

    # -- Abarbeiten ----------------------------------------------------------------------------
    if blatt is not None:
        for _ in range(cfg.extras_scrolls):
            _scroll(adb, blatt, down=False)
            sleep(SCROLL_SETTLE_S)

    # **Erst jede Aufgabe einmal, dann die Wiederholungen.** Manche Aufgaben lassen sich
    # mehrfach abholen -- "Super Rabatte anzeigen" stand am 26.09.2026 nach einem Durchgang auf
    # 2/3, zwei Muenzen blieben liegen. Andersherum herum fraesse eine dreifache Aufgabe das
    # Zeitbudget der anderen auf, bevor die auch nur einmal drankaemen.
    #
    # Schluss ist, sobald die Karte den Haken traegt, nicht bei einer geratenen Zahl: auf den
    # Zaehler ist am Geraet kein Verlass ("1/3" wurde als "B2 I" gelesen). `EXTRAS_REPEATS` ist
    # nur die Reissleine, falls eine Karte den Haken nie bekommt.
    gewollt = [v for v in result.verdicts if v.wanted][: cfg.extras_max]
    laeufe: dict[str, list[TaskRun]] = {v.card.short: [] for v in gewollt}
    offen, abbruch = list(gewollt), ""
    for runde in range(max(1, cfg.extras_repeats)):
        if runde:
            log.info("Zusatzaufgaben: Wiederholungsrunde %d fuer %d Aufgaben", runde + 1, len(offen))
        weiter: list[Verdict] = []
        for verdict in offen:
            if monotonic() >= frist:
                abbruch = "Zeitbudget aufgebraucht, der Rest bleibt liegen"
                break
            begriff = choose(list(cfg.extras_search_terms)) if verdict.search and cfg.extras_search_terms else None
            lauf = _work(adb, cfg, eyes, sleep, monotonic, verdict.card, shots, result, begriff)
            if lauf.note == FINISHED:
                continue  # abgehakt, kein Lauf zu verbuchen
            laeufe[verdict.card.short].append(lauf)
            if lauf.note in (LEFT, NO_LIST):
                # Nicht weitersuchen: der naechste Durchgang wuerde auf einem Bildschirm
                # wischen und tippen, den wir nicht erkannt haben.
                abbruch = lauf.note
                break
            if lauf.note == LOST:
                abbruch = "abgebrochen: die Liste war nach dem Zurueck nicht wiederzufinden"
                break
            if lauf.ok:
                weiter.append(verdict)
        offen = weiter
        if abbruch or not offen:
            break

    for verdict in gewollt:
        durchgaenge = laeufe[verdict.card.short]
        if durchgaenge:
            result.runs.append(_merge(durchgaenge, reward(verdict.card.text)))
    if abbruch:
        result.note = abbruch
        log.warning("Zusatzaufgaben: %s", abbruch)

    _abschluss(adb, eyes, cfg, sleep, shots, result)
    log.info("Zusatzaufgaben fertig: %s", result.summary())
    return result


def _abschluss(
    adb: Adb,
    eyes: Eyes,
    cfg: Config,
    sleep: Callable[[float], None],
    shots: Path | None,
    result: ExtrasResult,
) -> None:
    """Fenster zu, Muenzstand lesen, App beenden.

    Das Zurueck kommt nur, wenn wirklich noch die Liste zu sehen ist. Sonst waere es eines zu
    viel und traege uns aus der App -- der Fehler, der am 26.09.2026 auf dem Startbildschirm
    endete. In der Liste ist die Kopfzeile verdeckt, darum wird der Muenzstand erst danach
    gelesen, und erst nach einem etwaigen Werbefenster.
    """
    cards, _blatt, _png = _look(adb, eyes, cfg, sleep)
    if cards and _in_app(adb, cfg):
        adb.back()
        sleep(SCROLL_SETTLE_S)
        if _dismiss(adb, cfg, eyes.sheet(adb.screenshot()), sleep):
            sleep(SCROLL_SETTLE_S)
        png = adb.screenshot()
        _save(shots, "99-ende.png", png, result.shots)
        result.coins_after = eyes.coins(eyes.sheet(png))
    _heimweg(adb, cfg)


LOST = "Liste nicht wiedergefunden"
# Die Karte traegt jetzt den Haken. Kein Fehler, sondern das Ende der Wiederholungen.
FINISHED = "abgehakt"


def _merge(laeufe: list[TaskRun], wert: int | None) -> TaskRun:
    """Mehrere Durchgaenge derselben Aufgabe zu einer Zeile zusammenfassen.

    In der Tagesliste gehoert eine Aufgabe in eine Zeile mit einem Zaehler, nicht in drei fast
    gleiche. Der Muenzstand kommt vom ersten Anfang und vom letzten Ende -- dazwischen liegt
    alles, was diese Aufgabe eingebracht hat.
    """
    erster, letzter = laeufe[0], laeufe[-1]
    male = sum(1 for lauf in laeufe if lauf.ok)
    note = letzter.note
    if male > 1:
        note = f"{male}x erledigt"
        if wert:
            note += f", +{male * wert} Muenzen laut Karte"
    return TaskRun(
        text=erster.text,
        coins_before=erster.coins_before,
        coins_after=letzter.coins_after,
        ok=any(lauf.ok for lauf in laeufe),
        note=note,
        times=male,
    )


def _heimweg(adb: Adb, cfg: Config) -> None:
    """Sauber aufhoeren: die App beenden.

    Frueher wurde zurueckgetippt. Das ging schief, sobald die Liste nicht mehr zu sehen war:
    das erste Zurueck schloss das Fenster, das zweite die Coin-Seite, das dritte trug uns aus
    der App. Am Geraet stand danach der Startbildschirm, und es wurde zwischen Home und
    App-Uebersicht hin und her geschaltet -- genau so gemeldet am 26.09.2026.

    Ein force-stop laesst keinen Zweifel, wo wir landen, und der naechste Lauf startet die App
    ohnehin neu. Der Check-in ist zu diesem Zeitpunkt laengst verbucht.
    """
    try:
        adb.force_stop(cfg.app_package)
    except AdbError as exc:  # pragma: no cover - reiner Aufraeumweg
        log.debug("Zusatzaufgaben: App liess sich nicht beenden (%s)", exc)


def _dismiss(adb: Adb, cfg: Config, sheet: Sheet, sleep: Callable[[float], None]) -> bool:
    """Das Werbefenster wegtippen, falls eines im Weg steht.

    Beim Verlassen der Coin-Seite schiebt die App ein Fenster davor ("Nicht vergessen: morgen
    einchecken!") mit den Knoepfen "Verlassen" und "Bleiben". Getippt wird immer **Bleiben**:
    das macht das Fenster weg, ohne die Coin-Seite zu verlassen -- "Verlassen" wuerde uns aus
    der App tragen und den Rest des Durchgangs kosten.
    """
    # Auf dem Wort, nicht auf der Zeile: "Verlassen" und "Bleiben" stehen nebeneinander, und die
    # Mitte ihrer gemeinsamen Zeile liegt zwischen beiden Knoepfen -- im schlimmsten Fall auf dem
    # falschen.
    for word in sheet.words:
        if hits(normalize(word.text), cfg.extras_stay) is not None:
            log.info("Zusatzaufgaben: Werbefenster weggetippt (%r)", word.text)
            adb.tap(word.cx, word.cy)
            sleep(SCROLL_SETTLE_S)
            return True
    return False


def cards_on(eyes: Eyes, png: bytes, cfg: Config) -> tuple[list[Card], Sheet]:
    """Was gerade auf dem Schirm steht: Kartentexte aus der Texterkennung, die Knoepfe als
    Flaechen. Zwei Wege, weil zwei verschiedene Dinge gesucht werden -- Text und eine Farbe."""
    blatt = eyes.sheet(png)

    def zeilen(spots) -> list[Line]:
        return [Line(text=s.text, cx=s.cx, cy=s.cy, x2=s.x + s.w) for s in spots]

    knoepfe, erledigt = zeilen(eyes.go_spots(png)), zeilen(eyes.done_spots(png))
    return find_cards(group_lines(blatt.words), knoepfe, erledigt), blatt


def _type_search(adb: Adb, eyes: Eyes, sleep: Callable[[float], None], term: str) -> tuple[bool, str]:
    """Suchbegriff eintippen und abschicken. Gibt zurueck, ob es geklappt hat, und eine Notiz.

    Am 26.09.2026 meldete der Lauf "gesucht nach ...", und gesucht worden war nichts: im Feld
    stand noch der alte Begriff des Nutzers. `input text` schreibt dorthin, wo der Eingabefokus
    liegt, und nach dem Antippen von "Und los" liegt er nirgends. Also erst das Feld antippen,
    dann leeren, dann tippen -- und abgeschickt wird erst nach einem Blick darauf. Lieber eine
    Aufgabe als nicht erledigt melden, als eine Erfolgsmeldung, hinter der nichts steht.
    """
    sleep(SEARCH_LOAD_S)
    blatt = eyes.sheet(adb.screenshot())
    x, y = search_field(blatt)
    log.info("Suchaufgabe: Feld antippen (%d, %d) und %r eintippen", x, y, term)
    adb.tap(x, y)
    sleep(SEARCH_FOCUS_S)
    adb.clear_text(SEARCH_CLEAR_KEYS)
    adb.text(term)
    sleep(SEARCH_TYPE_S)
    if not typed_ok(eyes.sheet(adb.screenshot()).text, term):
        return False, f"Suchbegriff {term!r} kam nicht im Feld an"
    adb.enter()
    return True, f"gesucht nach {term!r}"


def _work(
    adb: Adb,
    cfg: Config,
    eyes: Eyes,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
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

    # Von ganz oben suchen: nach einer erledigten Aufgabe steht die Liste irgendwo, und
    # geblaettert wird nur nach unten.
    # Erst hinsehen, dann wischen. Ohne sichtbare Karten ist nicht gesagt, dass wir in der
    # Liste stehen -- und `_to_top` wischt sonst blind auf irgendeinem Bildschirm.
    cards, blatt, png = _await_cards(adb, eyes, cfg, sleep, monotonic, BACK_TIMEOUT_S)
    if not cards:
        lauf.note = LEFT if not _in_app(adb, cfg) else NO_LIST
        _save(shots, f"keine-liste-{len(result.runs) + 1:02d}.png", png, result.shots)
        log.warning("Zusatzaufgabe %r: %s", lauf.text, lauf.note)
        return lauf
    _to_top(adb, blatt, sleep, cfg.extras_scrolls + 2)

    stelle: Card | None = None
    gesehen: set[str] = set()
    for versuch in range(FIND_SCROLLS + 1):
        cards, blatt, png = _look(adb, eyes, cfg, sleep)
        # Gleichheit traegt hier nicht: zwischen Hinsehen und Abarbeiten liest die Erkennung
        # denselben Text leicht anders.
        stelle = next((c for c in cards if same_card(c.key, wanted.key)), None)
        log.debug(
            "Suche %r: Versuch %d, %d Karten sichtbar%s",
            lauf.text,
            versuch + 1,
            len(cards),
            " -- gefunden" if stelle else "",
        )
        if stelle is not None:
            break
        if not cards and not _in_app(adb, cfg):
            lauf.note = LEFT
            return lauf
        neue = {c.key for c in cards} - gesehen
        if versuch and not neue:
            # Nichts Neues mehr: wir sind unten angekommen, weiter zu blaettern bringt nichts.
            log.debug("Suche %r: Ende der Liste erreicht", lauf.text)
            break
        gesehen |= neue
        if versuch < FIND_SCROLLS:
            _scroll(adb, blatt)
            sleep(SCROLL_SETTLE_S)
    if stelle is not None and stelle.done:
        # Zwischen zwei Durchgaengen abgehakt: die Aufgabe gibt nichts mehr her. Das ist das
        # Ende der Wiederholungen, kein Fehler -- und hier wird nichts mehr angetippt.
        lauf.note = FINISHED
        log.info("Zusatzaufgabe %r: %s", lauf.text, lauf.note)
        return lauf
    if stelle is None or blatt is None:
        lauf.note = "Karte nicht mehr gefunden"
        # Das Bild dazu ablegen: ohne es laesst sich nicht sagen, ob die Karte fehlte oder nur
        # nicht gelesen wurde.
        _save(shots, f"nicht-gefunden-{len(result.runs) + 1:02d}.png", png, result.shots)
        log.info("Zusatzaufgabe %r: %s (%d Karten unterwegs gesehen)", lauf.text, lauf.note, len(gesehen))
        return lauf

    lauf.coins_before = eyes.coins(blatt)
    log.info("Zusatzaufgabe %r: antippen", lauf.text)
    adb.tap(stelle.go_x, stelle.go_y)
    getippt = True
    if search_term:
        getippt, lauf.note = _type_search(adb, eyes, sleep, search_term)
    if getippt:
        # Die Verweildauer zaehlt erst ab der Ergebnisseite -- ohne Suche gibt es keine.
        sleep(cfg.extras_dwell_s)
    else:
        log.warning("Zusatzaufgabe %r: %s", lauf.text, lauf.note)

    # Warten, bis die Liste wieder da ist -- nicht einmal hinsehen und aufgeben. Das Fenster
    # faehrt hoch, und auf dem langsamen Geraet standen nach zwei Sekunden noch keine Karten:
    # am 26.09.2026 galt die Liste darum als verloren, obwohl sie gleich darauf wieder da war.
    adb.back()
    cards, zurueck, png = _await_cards(adb, eyes, cfg, sleep, monotonic, BACK_TIMEOUT_S)
    _save(shots, f"task-{len(result.runs) + 1:02d}.png", png, result.shots)

    if not cards:
        if not _in_app(adb, cfg):
            # Kein zweites Zurueck auf einem fremden Bildschirm: dort schiebt es uns nur
            # weiter weg. Hier ist Schluss, und zwar bevor etwas angefasst wird.
            lauf.note = LEFT
            return lauf
        # Genau ein zweites Zurueck. Danach keines mehr: ein drittes traegt uns aus der App.
        adb.back()
        cards, zurueck, _png = _await_cards(adb, eyes, cfg, sleep, monotonic, BACK_TIMEOUT_S)
        if not cards:
            lauf.note = LOST
            return lauf

    lauf.coins_after = eyes.coins(zurueck)
    gewinn = lauf.gain
    lauf.ok = getippt
    if not getippt:
        log.info("Zusatzaufgabe %r: %s", lauf.text, lauf.note)
        return lauf
    wert = reward(wanted.text)
    if gewinn is not None:
        fertig = f"erledigt, {gewinn:+d} Muenzen"
    elif wert:
        # Gemessen geht nicht: in der Liste liegt das Fenster ueber der Kopfzeile, und der
        # Muenzstand ist dort nicht zu lesen. Was die Karte verspricht, steht aber da.
        fertig = f"erledigt, +{wert} Muenzen laut Karte"
    else:
        fertig = "erledigt"
    lauf.note = f"{fertig} ({lauf.note})" if lauf.note else fertig
    log.info("Zusatzaufgabe %r: %s", lauf.text, lauf.note)
    return lauf
