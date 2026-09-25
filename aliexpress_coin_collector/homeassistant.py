"""Zustaende an Home Assistant melden.

Warum ueberhaupt: wer das Handy nicht dauerhaft voll laden will, schaltet die Steckdose nach
Ladestand. Diese Entscheidung gehoert nicht in diesen Dienst -- ein Fehler darin traennte das
Geraet vom Strom und zerstoerte genau das, was er am Leben halten soll. Also liefert er nur
die Zahlen, und Home Assistant entscheidet, mit seinen eigenen Mitteln fuer Verfuegbarkeit
und Wartezeiten.

Die Zustaende gehen ueber die REST-API (`POST /api/states/<entity_id>`). Das ist bewusst
einfach gehalten, hat aber einen Haken, der in der Doku steht: so gesetzte Entitaeten gehoeren
zu keiner Integration und sind nach einem Neustart von Home Assistant weg, bis der Dienst sie
das naechste Mal schickt. Darum geht in jedem Fall alle paar Minuten eine Meldung raus, auch
wenn sich nichts geaendert hat.

Wie notify wirft hier nichts: Home Assistant ist Beiwerk, der Check-in ist die Hauptsache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

from .adb import Battery
from .config import Config
from .store import Attempt

log = logging.getLogger(__name__)

# Auch ohne Aenderung wird spaetestens nach dieser Zeit wieder gemeldet -- siehe oben, sonst
# blieben die Entitaeten nach einem Neustart von Home Assistant leer.
KEEPALIVE_MIN = 5

TIMEOUT_S = 10


@dataclass(frozen=True)
class State:
    """Eine Entitaet, so wie Home Assistant sie entgegennimmt."""

    entity_id: str
    state: str
    attributes: dict[str, Any]


def _clean(value: object) -> str:
    return "unknown" if value is None else str(value)


def states(cfg: Config, battery: Battery | None, last: Attempt | None, reachable: bool) -> list[State]:
    """Woraus die Entitaeten bestehen. Reine Funktion, damit sie ohne Home Assistant pruefbar ist.

    Ein Wert, den der Dienst nicht kennt, wird als "unknown" gemeldet und nicht weggelassen:
    eine Entitaet, die mal da ist und mal nicht, laesst sich in einer Automatisierung schlecht
    gebrauchen.
    """
    p = cfg.ha_prefix
    out = [
        State(
            f"binary_sensor.{p}_erreichbar",
            "on" if reachable else "off",
            {"friendly_name": "Coin Collector Gerät erreichbar", "device_class": "connectivity"},
        )
    ]

    if battery is not None:
        out.append(
            State(
                f"sensor.{p}_akku",
                _clean(battery.level),
                {
                    "friendly_name": "Coin Collector Akku",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                    "state_class": "measurement",
                    "zustand": battery.status_label,
                    "gesundheit": battery.health_label,
                    "am_strom": battery.plugged,
                    "ladevorgang": battery.charging,
                    "spannung_mv": battery.voltage_mv,
                },
            )
        )
        if battery.temperature_c is not None:
            out.append(
                State(
                    f"sensor.{p}_akku_temperatur",
                    f"{battery.temperature_c:.1f}",
                    {
                        "friendly_name": "Coin Collector Akkutemperatur",
                        "device_class": "temperature",
                        "unit_of_measurement": "°C",
                        "state_class": "measurement",
                    },
                )
            )

    if last is not None:
        out.append(
            State(
                f"sensor.{p}_letzter_lauf",
                last.outcome,
                {
                    "friendly_name": "Coin Collector letzter Lauf",
                    "zeitpunkt": last.ts.isoformat(timespec="seconds"),
                    "art": last.kind,
                    "meldung": last.message,
                },
            )
        )
        out.append(
            State(
                f"sensor.{p}_muenzen",
                _clean(last.coins_after),
                {
                    "friendly_name": "Coin Collector Münzstand",
                    "state_class": "total",
                    "stand_vom": last.ts.isoformat(timespec="seconds"),
                },
            )
        )
    return out


@dataclass
class Publisher:
    """Schickt die Zustaende, aber nur wenn sich etwas geaendert hat oder es lange her ist.

    Der Dienst taktet alle 30 Sekunden. Jedes Mal vier Entitaeten zu schicken waere Unfug --
    und gar nicht mehr zu schicken auch, siehe der Hinweis zum Neustart oben im Modul.
    """

    last_sent: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    at: datetime | None = None

    def due(self, entries: list[State], now: datetime, keepalive_min: int = KEEPALIVE_MIN) -> list[State]:
        if self.at is None or now >= self.at + timedelta(minutes=keepalive_min):
            return entries
        return [e for e in entries if self.last_sent.get(e.entity_id) != (e.state, e.attributes)]

    def publish(self, cfg: Config, entries: list[State], now: datetime | None = None) -> int:
        """Faellige Zustaende schicken. Gibt zurueck, wie viele ankamen. Wirft nie."""
        if not cfg.ha_url or not cfg.ha_token:
            return 0
        now = now or datetime.now()
        pending = self.due(entries, now)
        if not pending:
            return 0
        sent = 0
        for entry in pending:
            if _post(cfg, entry):
                self.last_sent[entry.entity_id] = (entry.state, entry.attributes)
                sent += 1
        if sent:
            self.at = now
        return sent


def _post(cfg: Config, entry: State) -> bool:
    try:
        resp = requests.post(
            f"{cfg.ha_url}/api/states/{entry.entity_id}",
            json={"state": entry.state, "attributes": entry.attributes},
            # Das Token ist ein Geheimnis und steht in keiner Meldung und keinem Protokoll.
            headers={"Authorization": f"Bearer {cfg.ha_token}"},
            timeout=TIMEOUT_S,
        )
    except requests.RequestException as exc:
        log.warning("Home Assistant nicht erreichbar: %s", type(exc).__name__)
        return False
    if resp.status_code >= 300:
        hint = " (Token abgelaufen oder falsch)" if resp.status_code in (401, 403) else ""
        log.warning("Home Assistant antwortete auf %s mit HTTP %s%s", entry.entity_id, resp.status_code, hint)
        return False
    return True
