from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

from .config import DEFAULT_LOGIN_MARKERS, Config


@dataclass(frozen=True)
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    conf: float

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


@dataclass
class PageState:
    button: Word | None  # Check-in-Button gefunden (Seite noch nicht eingecheckt)
    done: bool  # "morgen"-Marker im Streak-Bereich gefunden (heute schon eingecheckt)
    coins: int | None  # Muenzstand aus der Kopfzeile
    width: int
    height: int
    text: str
    logged_out: bool = False  # Anmelde-Marker im Text, siehe looks_logged_out

    def describe(self) -> str:
        out = " abgemeldet" if self.logged_out else ""
        return f"button={'ja' if self.button else 'nein'} erledigt={self.done} muenzen={self.coins}{out}"


def decode_png(png: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Screenshot konnte nicht dekodiert werden")
    return img


def read_words(img: np.ndarray, lang: str, psm: int = 11, min_conf: float = 30.0) -> list[Word]:
    data = pytesseract.image_to_data(img, lang=lang, config=f"--psm {psm}", output_type=Output.DICT)
    words: list[Word] = []
    for i, raw in enumerate(data["text"]):
        text = raw.strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if text and conf >= min_conf:
            words.append(Word(text, data["left"][i], data["top"][i], data["width"][i], data["height"][i], conf))
    return words


def _matches(word: Word, labels: tuple[str, ...]) -> bool:
    text = word.text.lower().strip(".:!,;")
    if len(text) < 4:
        return False
    return any(difflib.SequenceMatcher(None, text, label.lower()).ratio() >= 0.8 for label in labels)


THRESHOLDS = (235, 220, 245)


def find_button(
    img: np.ndarray,
    words: list[Word],
    labels: tuple[str, ...],
    lang: str,
    thresholds: tuple[int, ...] = THRESHOLDS,
    invert: bool = True,
) -> Word | None:
    """Sucht das Button-Wort im mittleren Bereich der Seite.

    Weisse Schrift auf orangem Grund erkennt Tesseract im Originalbild nicht. Deshalb gibt es
    Ausweichdurchgaenge, die nur die (fast) weissen Pixel als dunkle Schrift auf hellem Grund stehen lassen.

    'thresholds' und 'invert' sind fuer das Diagnose-Werkzeug der Weboberflaeche da: dort laesst
    sich ausprobieren, welcher Schwellwert an einem echten Screenshot traegt. Im Betrieb bleibt
    es bei den Standardwerten.
    """
    height = img.shape[0]

    def in_zone(w: Word) -> bool:
        return height * 0.3 <= w.cy <= height * 0.6

    candidates = [w for w in words if in_zone(w) and _matches(w, labels)]
    if not candidates:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        mode = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        for threshold in thresholds:
            _, binary = cv2.threshold(gray, threshold, 255, mode)
            candidates = [w for w in read_words(binary, lang) if in_zone(w) and _matches(w, labels)]
            if candidates:
                break
    return max(candidates, key=lambda w: w.conf) if candidates else None


@dataclass(frozen=True)
class Sheet:
    """Ein Bildschirm, in Woerter zerlegt -- ohne Deutung.

    `analyze` beantwortet die Frage des taeglichen Laufs: Knopf da, erledigt, wie viele Muenzen.
    Fuer die Liste der Zusatzaufgaben braucht es die Woerter selbst samt Lage -- aus ihnen werden
    Zeilen, aus Zeilen Karten.
    """

    words: list[Word]
    width: int
    height: int
    text: str


def read_sheet(png: bytes, cfg: Config) -> Sheet:
    img = decode_png(png)
    height, width = img.shape[:2]
    words = read_words(img, cfg.ocr_lang)
    return Sheet(words=words, width=width, height=height, text=" ".join(w.text for w in words))


class Sight:
    """Die Augen fuer `extras.explore`: Screenshot rein, gedeutete Werte raus."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def sheet(self, png: bytes) -> Sheet:
        return read_sheet(png, self.cfg)

    def more_button(self, png: bytes) -> Word | None:
        """Der Knopf "Mehr Muenzen verdienen" -- dort, wo vorher "Sammeln" stand."""
        img = decode_png(png)
        words = read_words(img, self.cfg.ocr_lang)
        return find_button(img, words, self.cfg.extras_button_labels, self.cfg.ocr_lang)

    def bright(self, png: bytes) -> Sheet:
        """Helle Schrift auf farbigem Grund -- die Knoepfe "Und los" in der Aufgabenliste.

        Weisse Schrift auf Orange liest Tesseract im Originalbild nicht: am 26.09.2026 stand im
        erkannten Text der ganzen Liste kein einziges "Und los", waehrend alle Kartentitel
        (dunkel auf weiss) sauber durchkamen. Dieselbe Umkehrung, die schon den Sammeln-Knopf
        findet, macht sie lesbar.

        Genommen wird der erste Durchgang, in dem eine Knopfbeschriftung auftaucht -- sonst der
        wortreichste. Jeder Durchgang kostet eine volle Texterkennung, darum nicht alle drei,
        wenn der erste schon traegt.
        """
        img = decode_png(png)
        height, width = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        best: list[Word] = []
        for threshold in THRESHOLDS:
            _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
            words = read_words(binary, self.cfg.ocr_lang)
            if len(words) > len(best):
                best = words
            joined = " ".join(w.text for w in words).lower()
            if any(term in joined for term in self.cfg.extras_go):
                best = words
                break
        return Sheet(words=best, width=width, height=height, text=" ".join(w.text for w in best))

    def coins(self, sheet: Sheet) -> int | None:
        return read_coin_balance(sheet.words, sheet.width, sheet.height)


def looks_logged_out(text: str, markers: tuple[str, ...] = DEFAULT_LOGIN_MARKERS) -> bool:
    """Steht eine Anmeldeaufforderung auf der Seite?

    Gesucht wird im Volltext und nicht ueber _matches: "Sign in" besteht aus zwei Woertern, und
    die Wortvergleiche dort greifen nur je Wort.

    Wird nur ausgewertet, wenn ohnehin weder Button noch Erledigt-Zustand erkannt wurden --
    die Erkennung kann damit einen gescheiterten Lauf genauer benennen, aber nie einen
    erfolgreichen stoeren. Ein Fehlgriff kostet also hoechstens ein falsches Etikett auf einem
    Lauf, der so oder so nichts eingesammelt haette.
    """
    low = text.lower()
    return any(marker in low for marker in markers)


_COIN_RE = re.compile(r"^(\d{1,3}(?:[.,]\d{3})+|\d+)[≈~=]*$")
_ICON_NOISE = re.compile(r"^[()\[\]{}|Oo©®]{1,3}(?=\d)")  # Muenz-Symbol wird manchmal als Zeichen vor die Zahl gelesen


def _clean_coin_token(text: str) -> str:
    return _ICON_NOISE.sub("", text)


def read_coin_balance(words: list[Word], width: int, height: int) -> int | None:
    """Liest den Muenzstand: linkeste reine Zahl rechts vom Muenz-Symbol in der Kopfzeile."""
    band = [
        w
        for w in words
        if height * 0.06 <= w.cy <= height * 0.16
        and width * 0.2 <= w.x <= width * 0.6
        and _COIN_RE.match(_clean_coin_token(w.text))
    ]
    if not band:
        return None
    best = min(band, key=lambda w: w.x)
    digits = re.sub(r"[^\d]", "", _COIN_RE.match(_clean_coin_token(best.text)).group(1))  # type: ignore[union-attr]
    return int(digits) if digits else None


def analyze(
    png: bytes,
    cfg: Config,
    thresholds: tuple[int, ...] = THRESHOLDS,
    invert: bool = True,
) -> PageState:
    img = decode_png(png)
    height, width = img.shape[:2]
    words = read_words(img, cfg.ocr_lang)
    text = " ".join(w.text for w in words)

    top_text = " ".join(w.text for w in words if w.cy < height * 0.5)
    marker = bool(re.search(r"\bmorgen", top_text, re.IGNORECASE))

    # Der Knopf ist das staerkere Signal: ist er da, ist noch nichts eingesammelt. Bis 0.10.0
    # gewann der "morgen"-Marker, und die Knopfsuche lief dann gar nicht erst -- damit fiel ein
    # ganzer Tag aus, sobald das Wort schon auf der halb geladenen Seite stand. Der Vorschautext
    # "wenn Sie morgen vorbeischauen" steht dort naemlich in beiden Zustaenden.
    # Ein Fehlgriff in die andere Richtung ist unwahrscheinlich: "gesammelt" und "verdienen"
    # erreichen gegen "sammeln" nur 0.75 bzw. weniger und bleiben damit unter der Schwelle.
    button = find_button(img, words, cfg.button_labels, cfg.ocr_lang, thresholds, invert)
    coins = read_coin_balance(words, width, height)
    done = marker and button is None
    # Nur pruefen, wenn die Seite ohnehin nichts Brauchbares hergab (siehe looks_logged_out).
    logged_out = not button and not done and looks_logged_out(text, cfg.login_markers)
    return PageState(
        button=button,
        done=done,
        coins=coins,
        width=width,
        height=height,
        text=text,
        logged_out=logged_out,
    )
