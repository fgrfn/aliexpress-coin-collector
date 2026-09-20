"""Zur Laufzeit aenderbare Einstellungen (data/settings.json).

Die .env bleibt die Vorgabe. Was hier steht, ueberschreibt sie. Der Dienst liest die Datei bei jedem
Takt neu, damit eine Aenderung aus der Weboberflaeche ohne Neustart ankommt.

Nur die vier Fenstergrenzen sind hier aenderbar. Alles andere bleibt bewusst in der .env: eine
Oberflaeche, die Timeouts oder BUTTON_LABELS verstellen kann, legt im Zweifel den taeglichen Lauf lahm.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"
WINDOW_KEYS = ("morning_start", "morning_end", "evening_start", "evening_end")


@dataclass(frozen=True)
class Overrides:
    """Was in settings.json steht. Leer, wenn es die Datei nicht gibt."""

    windows: dict[str, dtime]
    changed_at: datetime | None

    @property
    def empty(self) -> bool:
        return not self.windows and self.changed_at is None


EMPTY = Overrides(windows={}, changed_at=None)


def path_for(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE


def load(data_dir: Path) -> Overrides:
    """Liest die Einstellungen. Eine kaputte Datei darf den Dienst nie stoppen, sie wird ignoriert."""
    path = path_for(data_dir)
    if not path.is_file():
        return EMPTY
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("settings.json nicht lesbar, es gelten die Werte aus der .env: %s", exc)
        return EMPTY
    if not isinstance(raw, dict):
        log.warning("settings.json enthaelt kein Objekt, es gelten die Werte aus der .env")
        return EMPTY

    windows: dict[str, dtime] = {}
    for key in WINDOW_KEYS:
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        try:
            hh, mm = value.strip().split(":")
            windows[key] = dtime(int(hh), int(mm))
        except ValueError:
            log.warning("settings.json: %s=%r ist keine Uhrzeit, Wert wird ignoriert", key, value)

    changed_at = None
    stamp = raw.get("changed_at")
    if isinstance(stamp, str):
        try:
            changed_at = datetime.fromisoformat(stamp)
        except ValueError:
            log.warning("settings.json: changed_at=%r ist kein Zeitstempel", stamp)

    return Overrides(windows=windows, changed_at=changed_at)


def save(data_dir: Path, windows: dict[str, dtime], now: datetime | None = None) -> Overrides:
    """Schreibt die Fenster und haelt den Aenderungszeitpunkt fest.

    Der Zeitpunkt ist kein Schmuck: `scheduler.decide` startet heute keine Laufart mehr, deren neu
    gewuerfelte Uhrzeit zum Zeitpunkt der Aenderung bereits verstrichen war. Sonst wuerde eine
    Einstellungsaenderung unangekuendigt einen Lauf ausloesen.
    """
    unknown = set(windows) - set(WINDOW_KEYS)
    if unknown:
        raise ValueError(f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")

    changed_at = now or datetime.now()
    payload = {key: f"{value:%H:%M}" for key, value in windows.items()}
    payload["changed_at"] = changed_at.isoformat(timespec="seconds")

    data_dir.mkdir(parents=True, exist_ok=True)
    target = path_for(data_dir)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(target)  # atomar, damit der Dienst nie eine halb geschriebene Datei liest
    return Overrides(windows=dict(windows), changed_at=changed_at)
