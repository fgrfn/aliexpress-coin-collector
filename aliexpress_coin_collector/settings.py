"""Zur Laufzeit aenderbare Einstellungen (data/settings.json).

Die .env bleibt die Vorgabe. Was hier steht, ueberschreibt sie. Der Dienst liest die Datei bei jedem
Takt neu, damit eine Aenderung aus der Weboberflaeche ohne Neustart ankommt.

Aenderbar ist der Ablauf (Zeitfenster, Wartezeiten, Wiederholungen), die Geraeteadresse und die
Benachrichtigung. Bewusst nicht aenderbar bleibt alles, woran die Erkennung haengt -- BUTTON_LABELS,
OCR_LANG, COIN_URL, APP_PACKAGE: ein Vertipper dort liesse den taeglichen Lauf ins Leere greifen,
ohne dass es jemandem auffiele. Das steht weiter nur in der .env.

Die Datei ist flach aufgebaut, ein Schluessel je Einstellung, plus `changed_at`. Aeltere Dateien,
die nur die vier Fenstergrenzen enthalten, werden unveraendert weitergelesen.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SETTINGS_FILE = "settings.json"

# Uhrzeiten, als "HH:MM" abgelegt.
WINDOW_KEYS = ("morning_start", "morning_end", "evening_start", "evening_end")
# Ja/Nein.
FLAG_KEYS = ("skip_if_awake", "notify_on_success", "notify_on_already_done", "notify_on_offline")
# Ganze Zahlen.
NUMBER_KEYS = (
    "busy_retry_min",
    "busy_max_wait_min",
    "page_timeout_s",
    "confirm_timeout_s",
    "launch_retries",
    "offline_alert_min",
)
# Text. `discord_webhook` ist ein Geheimnis und darf nirgends mitprotokolliert werden.
TEXT_KEYS = ("adb_serial", "discord_webhook")
SECRET_KEYS = ("discord_webhook",)

KEYS = WINDOW_KEYS + FLAG_KEYS + NUMBER_KEYS + TEXT_KEYS


@dataclass(frozen=True)
class Overrides:
    """Was in settings.json steht. Leer, wenn es die Datei nicht gibt.

    `values` enthaelt nur Schluessel, die wirklich in der Datei stehen und sich lesen liessen --
    fehlt einer, gilt die .env. Die Werte sind bereits in den Zieltyp gewandelt.
    """

    values: dict[str, Any]
    changed_at: datetime | None

    @property
    def windows(self) -> dict[str, dtime]:
        """Nur die Fenstergrenzen. Der Zeitplan interessiert sich fuer nichts anderes."""
        return {key: value for key, value in self.values.items() if key in WINDOW_KEYS}

    @property
    def empty(self) -> bool:
        return not self.values and self.changed_at is None


EMPTY = Overrides(values={}, changed_at=None)


def path_for(data_dir: Path) -> Path:
    return data_dir / SETTINGS_FILE


def _as_time(value: Any) -> dtime:
    if not isinstance(value, str):
        raise ValueError("keine Uhrzeit")
    hh, mm = value.strip().split(":")[:2]
    return dtime(int(hh), int(mm))


def _as_flag(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("kein Ja/Nein")
    return value


def _as_number(value: Any) -> int:
    # bool ist in Python ein int -- hier waere das fast immer ein Fehler in der Datei.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("keine ganze Zahl")
    return value


def _as_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("kein Text")
    return value.strip()


_READERS = {
    **{key: _as_time for key in WINDOW_KEYS},
    **{key: _as_flag for key in FLAG_KEYS},
    **{key: _as_number for key in NUMBER_KEYS},
    **{key: _as_text for key in TEXT_KEYS},
}


def _hide(key: str, value: Any) -> str:
    """Wert fuer eine Meldung. Geheimnisse werden nie ausgeschrieben."""
    return "(geheim)" if key in SECRET_KEYS else repr(value)


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

    values: dict[str, Any] = {}
    for key in KEYS:
        if key not in raw:
            continue
        try:
            values[key] = _READERS[key](raw[key])
        except (TypeError, ValueError):
            log.warning("settings.json: %s=%s ist unbrauchbar, Wert wird ignoriert", key, _hide(key, raw[key]))

    changed_at = None
    stamp = raw.get("changed_at")
    if isinstance(stamp, str):
        try:
            changed_at = datetime.fromisoformat(stamp)
        except ValueError:
            log.warning("settings.json: changed_at=%r ist kein Zeitstempel", stamp)

    return Overrides(values=values, changed_at=changed_at)


def _check_types(values: dict[str, Any]) -> None:
    unknown = set(values) - set(KEYS)
    if unknown:
        raise ValueError(f"Unbekannte Einstellung: {', '.join(sorted(unknown))}")
    for key, value in values.items():
        expected, name = {
            _as_time: (dtime, "eine Uhrzeit"),
            _as_flag: (bool, "Ja oder Nein"),
            _as_number: (int, "eine ganze Zahl"),
            _as_text: (str, "Text"),
        }[_READERS[key]]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ValueError(f"{key} erwartet {name}, nicht {type(value).__name__}")


def _encode(key: str, value: Any) -> Any:
    return f"{value:%H:%M}" if key in WINDOW_KEYS else value


def save(data_dir: Path, values: dict[str, Any], now: datetime | None = None) -> Overrides:
    """Schreibt die uebergebenen Einstellungen und laesst alle uebrigen stehen.

    Zusammengefuehrt wird bewusst: die Oberflaeche hat je Abschnitt ein eigenes Formular, und das
    Speichern der Benachrichtigung darf die Zeitfenster nicht mitloeschen.

    Der Aenderungszeitpunkt wird nur fortgeschrieben, wenn sich wirklich ein Fenster verschoben hat.
    Er ist kein Schmuck: `scheduler.decide` startet heute keine Laufart mehr, deren neu gewuerfelte
    Uhrzeit zum Zeitpunkt der Aenderung bereits verstrichen war. Sonst wuerde eine Aenderung
    unangekuendigt einen Lauf ausloesen -- oder, andersherum, ein geaenderter Webhook einen
    faelligen Lauf stillschweigend unterdruecken.
    """
    _check_types(values)

    before = load(data_dir)
    merged = {**before.values, **values}
    windows_moved = any(before.values.get(key) != value for key, value in values.items() if key in WINDOW_KEYS)
    changed_at = (now or datetime.now()) if windows_moved else before.changed_at

    payload: dict[str, Any] = {key: _encode(key, value) for key, value in merged.items()}
    if changed_at is not None:
        payload["changed_at"] = changed_at.isoformat(timespec="seconds")

    data_dir.mkdir(parents=True, exist_ok=True)
    target = path_for(data_dir)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Die Datei kann den Discord-Webhook enthalten, also nur fuer den Dienstbenutzer lesbar.
    os.chmod(tmp, 0o600)
    tmp.replace(target)  # atomar, damit der Dienst nie eine halb geschriebene Datei liest
    return Overrides(values=merged, changed_at=changed_at)
