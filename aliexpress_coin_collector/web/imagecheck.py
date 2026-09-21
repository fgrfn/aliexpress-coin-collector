"""Erkennung an einem hochgeladenen Screenshot pruefen.

Das Werkzeug gibt es, weil die Erkennung des Sammeln-Buttons bis heute nur an Attrappen
belegt ist -- der groesste offene Punkt des Projekts. Hier laesst sich an echten Bildern
sehen, was die OCR findet und bei welchem Schwellwert.

**Der hochgeladene Screenshot wird nie auf die Platte geschrieben.** Er enthaelt Kontostaende
und Bestellungen. Er lebt nur im Speicher dieser einen Anfrage; die Vorschau geht als
verkleinertes Bild direkt in die Antwort zurueck und liegt nirgends sonst.

OpenCV und Tesseract werden erst beim ersten Aufruf geladen, nicht beim Start des Dienstes:
wer das Werkzeug nie benutzt, zahlt den Speicher nicht.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Config

MAX_UPLOAD = 12 * 1024 * 1024  # groesser wird kein Screenshot eines Telefons
PREVIEW_WIDTH = 320
ALLOWED = ("image/png", "image/jpeg", "image/webp")


class InspectError(ValueError):
    """Das Bild ist nicht brauchbar. Der Text geht an den Nutzer."""


@dataclass(frozen=True)
class Box:
    """Fundstelle im verkleinerten Vorschaubild, in Prozent der Kantenlaenge.

    Prozent statt Pixel, damit die Vorlage die Vorschau frei skalieren kann.
    """

    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class Result:
    """Was das Werkzeug anzeigt."""

    found: bool
    label: str
    confidence: float
    position: str
    done: bool
    logged_out: bool
    coins: int | None
    width: int
    height: int
    duration_s: float
    word_count: int
    thresholds: str
    invert: bool
    preview: str = ""  # data-URI des verkleinerten Bildes, nur fuer diese eine Antwort
    box: Box | None = None
    words: list[str] = field(default_factory=list)


def parse_threshold(raw: str) -> tuple[int, ...]:
    """Leer oder unbrauchbar heisst: die Standardkette, also genau das Verhalten im Betrieb."""
    from .. import ocr

    value = (raw or "").strip()
    if not value:
        return ocr.THRESHOLDS
    try:
        number = int(value)
    except ValueError:
        return ocr.THRESHOLDS
    if not 0 <= number <= 255:
        return ocr.THRESHOLDS
    return (number,)


def check_upload(content_type: str, size: int) -> None:
    """Vor dem Dekodieren pruefen, damit ein falscher Upload nicht erst OpenCV beschaeftigt."""
    if size == 0:
        raise InspectError("Es wurde keine Datei ausgewählt.")
    if size > MAX_UPLOAD:
        raise InspectError(f"Die Datei ist zu groß ({size // 1024 // 1024} MB, erlaubt sind 12 MB).")
    if content_type and content_type.split(";")[0].strip() not in ALLOWED:
        raise InspectError("Das ist kein Bild. Erwartet wird PNG, JPEG oder WebP.")


def analyze(image: bytes, cfg: Config, threshold_raw: str = "", invert: bool = True) -> Result:
    """Den Screenshot durch dieselbe Erkennung schicken, die auch im Betrieb laeuft."""
    from .. import ocr

    thresholds = parse_threshold(threshold_raw)
    started = time.monotonic()
    try:
        state = ocr.analyze(image, cfg, thresholds, invert)
    except ValueError as exc:
        raise InspectError(f"Das Bild ließ sich nicht lesen: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - Tesseract fehlt, kein Sprachpaket, ...
        raise InspectError(f"Die Erkennung ist gescheitert: {exc}") from exc
    duration = time.monotonic() - started

    button = state.button
    box = None
    if button is not None and state.width and state.height:
        box = Box(
            left=100.0 * button.x / state.width,
            top=100.0 * button.y / state.height,
            width=100.0 * button.w / state.width,
            height=100.0 * button.h / state.height,
        )

    default = thresholds == ocr.THRESHOLDS
    return Result(
        found=button is not None,
        label=button.text if button else "",
        confidence=round(button.conf, 1) if button else 0.0,
        position=f"{button.cx}, {button.cy}" if button else "",
        done=state.done,
        logged_out=state.logged_out,
        coins=state.coins,
        width=state.width,
        height=state.height,
        duration_s=round(duration, 1),
        word_count=len(state.text.split()),
        thresholds="Standard (235, 220, 245)" if default else str(thresholds[0]),
        invert=invert,
        preview=preview(image),
        box=box,
        words=state.text.split()[:80],
    )


def preview(image: bytes, width: int = PREVIEW_WIDTH) -> str:
    """Verkleinertes Bild als data-URI.

    Bewusst als data-URI und nicht als Datei: so gibt es nichts wegzuraeumen und nichts, das
    liegen bleibt. Das Bild steckt nur in dieser einen Antwort.
    """
    try:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return ""
        height = max(1, round(img.shape[0] * width / img.shape[1]))
        small = cv2.resize(img, (width, height), interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".png", small)
        if not ok:
            return ""
    except Exception:  # noqa: BLE001 - ohne Vorschau ist das Ergebnis immer noch brauchbar
        return ""
    import base64

    return "data:image/png;base64," + base64.b64encode(buffer.tobytes()).decode("ascii")
