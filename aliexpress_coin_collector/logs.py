"""Protokolldatei des Sammel-Dienstes: schreiben und wieder einlesen.

Beides steht hier zusammen, damit Format und Auswertung nicht auseinanderlaufen.

Warum ueberhaupt eine Datei, wo doch alles ins journald geht: der Webdienst laeuft als
unprivilegierter Nutzer und darf das Journal nicht lesen. Ihn dafuer in die Gruppe
systemd-journal zu nehmen waere der Preis gewesen -- eine Datei in data/ ist billiger und
funktioniert auch auf einem System ganz ohne systemd.

Geschrieben wird sie nur vom Dienst. Ein Aufruf von Hand ('once', 'doctor') protokolliert
weiter nur auf die Konsole: sonst legt der erste Aufruf als root eine Datei an, die der
Dienst danach nicht mehr beschreiben kann.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

LOG_FILE = "collector.log"
FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
MAX_BYTES = 1_000_000
BACKUPS = 4  # zusammen mit der aktiven Datei also fuenf mal ein Megabyte
TAIL_BYTES = 256 * 1024  # so viel wird fuer die Anzeige vom Ende gelesen

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:,\d+)? +([A-Z]+) +(.*)$")
_TONE = {"WARNING": "warn", "ERROR": "bad", "CRITICAL": "bad"}


@dataclass(frozen=True)
class Line:
    """Eine Zeile des Protokolls, fertig zur Anzeige."""

    when: str
    level: str
    message: str

    @property
    def time(self) -> str:
        """Nur die Uhrzeit -- das Datum steht meist ohnehin daneben."""
        return self.when[11:] if len(self.when) >= 19 else self.when

    @property
    def tone(self) -> str:
        return _TONE.get(self.level, "")


def path(data_dir: Path) -> Path:
    return data_dir / LOG_FILE


def attach(data_dir: Path, level: str) -> bool:
    """Zusaetzlich in eine rotierende Datei protokollieren. True, wenn es geklappt hat.

    Schlaegt es fehl -- kein Schreibrecht, volle Platte --, laeuft der Dienst weiter und
    protokolliert nur auf die Konsole. Ein Protokoll ist kein Grund, den Check-in ausfallen
    zu lassen.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            path(data_dir), maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Protokolldatei nicht nutzbar, es wird nur auf die Konsole geschrieben: %s", exc)
        return False
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.setLevel(getattr(logging, level, logging.INFO))
    logging.getLogger().addHandler(handler)
    return True


def _tail_text(file: Path, limit_bytes: int = TAIL_BYTES) -> str:
    """Das Ende der Datei lesen. Bei einem Megabyte will niemand alles in den Speicher holen."""
    with file.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - limit_bytes))
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    # Beim Springen mitten in eine Zeile: den angeschnittenen Anfang verwerfen.
    if len(raw) >= limit_bytes and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


def parse(text: str) -> list[Line]:
    """Text in Zeilen zerlegen. Fortsetzungszeilen -- etwa ein Traceback -- gehoeren zur
    vorherigen Meldung und werden ihr angehaengt, statt einzeln aufzutauchen."""
    out: list[Line] = []
    for raw in text.splitlines():
        match = _LINE.match(raw)
        if match:
            out.append(Line(when=match.group(1), level=match.group(2), message=match.group(3)))
        elif out and raw.strip():
            last = out[-1]
            out[-1] = Line(when=last.when, level=last.level, message=f"{last.message}\n{raw}")
    return out


def matches(line: Line, level: str, query: str) -> bool:
    """Filter der Anzeige. 'level' leer heisst alle, sonst ab dieser Stufe aufwaerts."""
    if level:
        try:
            if LEVELS.index(line.level) < LEVELS.index(level):
                return False
        except ValueError:
            pass  # unbekannte Stufe: lieber zeigen als verschlucken
    if query and query.casefold() not in line.message.casefold():
        return False
    return True


def read(data_dir: Path, level: str = "", query: str = "", limit: int = 200) -> list[Line]:
    """Die juengsten Zeilen, neueste zuletzt. Fehlt die Datei, ist das Protokoll eben leer."""
    file = path(data_dir)
    try:
        text = _tail_text(file)
    except OSError:
        return []
    lines = [line for line in parse(text) if matches(line, level, query)]
    return lines[-limit:]


def exists(data_dir: Path) -> bool:
    return path(data_dir).is_file()
