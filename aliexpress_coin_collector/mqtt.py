"""Zustaende per MQTT Discovery an Home Assistant melden.

Der zweite Weg neben homeassistant.py, und der bessere, wenn ein Broker da ist. Drei Dinge
kann die REST-API nicht:

* **Verfuegbarkeit.** Der Broker bekommt ein Last Will mit. Stirbt der Dienst, setzt er die
  Entitaeten von sich aus auf "unavailable", statt dass ein eingefrorener Ladestand als
  aktueller Wert stehen bleibt. Fuer eine Steckdosenautomatik ist genau das der Punkt.
* **Bestand.** Konfiguration und Zustand liegen retained im Broker. Nach einem Neustart von
  Home Assistant sind die Entitaeten sofort wieder da, ohne auf den naechsten Takt zu warten.
* **Zugehoerigkeit.** Die Entitaeten haengen an einem Geraet im Geraeteregister, haben eine
  unique_id und lassen sich darum umbenennen und einem Bereich zuordnen.

Aufbau wie beim REST-Weg: die Nutzlasten sind reine Funktionen und ohne Broker pruefbar, die
Ein- und Ausgabe steckt in Publisher. Und wie notify wirft hier nichts -- Home Assistant ist
Beiwerk, der Check-in ist die Hauptsache.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .adb import Battery
from .config import Config
from .store import Attempt

log = logging.getLogger(__name__)

try:  # paho ist erst ab 0.15.0 dabei; eine aeltere Installation soll trotzdem starten
    import paho.mqtt.client as paho

    HAVE_PAHO = True
except ImportError:  # pragma: no cover - haengt an der Installation, nicht am Code
    paho = None
    HAVE_PAHO = False

ONLINE, OFFLINE = "online", "offline"

# Wie schnell der Broker einen Ausfall bemerkt, haengt davon ab, wie er passiert. Stirbt der
# Prozess oder haelt der Container an, schliesst das Betriebssystem die Verbindung, und das
# Testament faellt sofort. Faellt dagegen der Strom oder das Netz, merkt es der Broker erst
# nach dem Anderthalbfachen dieser Zeit -- also gut 45 Sekunden.
KEEPALIVE_S = 30


def topics(cfg: Config) -> tuple[str, str]:
    """(Verfuegbarkeit, Zustand). Beides unter einem eigenen Zweig je Praefix."""
    base = f"{cfg.ha_prefix}"
    return f"{base}/status", f"{base}/state"


def _device(cfg: Config) -> dict[str, Any]:
    """Alle Entitaeten haengen an einem Geraet -- sonst lieg en sie einzeln in der Liste herum."""
    return {
        "identifiers": [cfg.ha_prefix],
        "name": "AliExpress Coin Collector",
        "manufacturer": "fgrfn",
        "model": "aliexpress-coin-collector",
        "sw_version": __version__,
    }


@dataclass(frozen=True)
class Entity:
    """Eine Entitaet, wie Home Assistant sie per Discovery entgegennimmt."""

    component: str  # sensor oder binary_sensor
    object_id: str
    config: dict[str, Any]

    def topic(self, cfg: Config) -> str:
        return f"{cfg.mqtt_discovery_prefix}/{self.component}/{cfg.ha_prefix}/{self.object_id}/config"


# Werte, die der Dienst gerade nicht kennt, stehen gar nicht erst in der Nutzlast. Darum
# greift in den Vorlagen `default` -- das ergibt "unknown" statt einer erfundenen Null.
def _template(key: str) -> str:
    return f"{{{{ value_json.{key} | default('unknown') }}}}"


def entities(cfg: Config) -> list[Entity]:
    """Die Discovery-Konfiguration aller Entitaeten. Reine Funktion, ohne Broker pruefbar."""
    availability, state = topics(cfg)
    common: dict[str, Any] = {
        "state_topic": state,
        "availability_topic": availability,
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
        "device": _device(cfg),
    }

    def entity(component: str, object_id: str, name: str, **extra: Any) -> Entity:
        return Entity(
            component,
            object_id,
            {
                **common,
                "name": name,
                "unique_id": f"{cfg.ha_prefix}_{object_id}",
                "object_id": f"{cfg.ha_prefix}_{object_id}",
                **extra,
            },
        )

    return [
        entity(
            "sensor",
            "akku",
            "Akku",
            device_class="battery",
            unit_of_measurement="%",
            state_class="measurement",
            value_template=_template("level"),
            json_attributes_topic=state,
        ),
        entity(
            "sensor",
            "akku_temperatur",
            "Akkutemperatur",
            device_class="temperature",
            unit_of_measurement="°C",
            state_class="measurement",
            value_template=_template("temperature"),
        ),
        entity(
            "sensor",
            "letzter_lauf",
            "Letzter Lauf",
            icon="mdi:history",
            value_template=_template("outcome"),
        ),
        entity(
            "sensor",
            "muenzen",
            "Münzstand",
            icon="mdi:hand-coin",
            state_class="total",
            value_template=_template("coins"),
        ),
        entity(
            "binary_sensor",
            "erreichbar",
            "Gerät erreichbar",
            device_class="connectivity",
            value_template=_template("reachable"),
            payload_on="on",
            payload_off="off",
        ),
    ]


def state_payload(battery: Battery | None, last: Attempt | None, reachable: bool) -> dict[str, Any]:
    """Ein Zustandsthema fuer alle Entitaeten, als JSON.

    Was der Dienst nicht kennt, fehlt hier -- die Vorlagen machen daraus "unknown". Ein
    erfundener Wert waere schlimmer als eine Luecke, gerade beim Ladestand.
    """
    payload: dict[str, Any] = {"reachable": "on" if reachable else "off"}
    if battery is not None:
        if battery.level is not None:
            payload["level"] = battery.level
        if battery.temperature_c is not None:
            payload["temperature"] = battery.temperature_c
        if battery.voltage_mv is not None:
            payload["voltage_mv"] = battery.voltage_mv
        payload["zustand"] = battery.status_label
        payload["gesundheit"] = battery.health_label
        payload["am_strom"] = battery.plugged
        payload["ladevorgang"] = battery.charging
    if last is not None:
        payload["outcome"] = last.outcome
        payload["zeitpunkt"] = last.ts.isoformat(timespec="seconds")
        payload["art"] = last.kind
        payload["meldung"] = last.message
        if last.coins_after is not None:
            payload["coins"] = last.coins_after
    return payload


@dataclass
class Publisher:
    """Haelt die Verbindung zum Broker und schickt Konfiguration und Zustand.

    Die Verbindung laeuft im Hintergrundfaden von paho und baut sich nach einem Abriss selbst
    wieder auf. Bei jedem Verbindungsaufbau geht die Discovery erneut raus: der Broker koennte
    zwischendurch neu gestartet worden sein und seine retained Nachrichten verloren haben.
    """

    client: Any = None
    connected: bool = False
    last_state: dict[str, Any] = field(default_factory=dict)

    def start(self, cfg: Config) -> bool:
        """Verbindung aufbauen. Gibt zurueck, ob es losgehen konnte. Wirft nie."""
        if not cfg.mqtt_host:
            return False
        if not HAVE_PAHO:
            log.error("MQTT_HOST ist gesetzt, aber paho-mqtt fehlt. Installieren: pip install paho-mqtt")
            return False
        availability, _state = topics(cfg)
        try:
            client = paho.Client(
                paho.CallbackAPIVersion.VERSION2,
                client_id=f"{cfg.ha_prefix}-{__version__}",
            )
            if cfg.mqtt_user:
                client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
            # Das Testament: sagt der Broker den Entitaeten, dass hier niemand mehr ist.
            client.will_set(availability, OFFLINE, qos=1, retain=True)
            client.on_connect = lambda c, u, flags, reason, props=None: self._on_connect(cfg, reason)
            client.on_disconnect = lambda c, u, flags, reason, props=None: self._on_disconnect(reason)
            client.reconnect_delay_set(min_delay=1, max_delay=120)
            client.connect_async(cfg.mqtt_host, cfg.mqtt_port, KEEPALIVE_S)
            client.loop_start()
        except Exception as exc:  # noqa: BLE001 - ein kaputter Broker darf den Dienst nicht stoppen
            log.warning("MQTT-Verbindung nicht moeglich: %s", exc)
            return False
        self.client = client
        log.info("MQTT: verbinde mit %s:%s", cfg.mqtt_host, cfg.mqtt_port)
        return True

    def _on_connect(self, cfg: Config, reason: object) -> None:
        if getattr(reason, "is_failure", False):
            # Der Grund selbst ist harmlos, Benutzername und Passwort stehen nicht darin.
            log.warning("MQTT: Anmeldung am Broker abgelehnt (%s)", reason)
            self.connected = False
            return
        self.connected = True
        log.info("MQTT: verbunden, melde die Entitaeten an")
        availability, _ = topics(cfg)
        for entity in entities(cfg):
            self._send(entity.topic(cfg), json.dumps(entity.config, ensure_ascii=False), retain=True)
        self._send(availability, ONLINE, retain=True)
        # Nach einem Neuaufbau ist der zuletzt geschickte Zustand im Broker vielleicht weg.
        if self.last_state:
            _, state_topic = topics(cfg)
            self._send(state_topic, json.dumps(self.last_state, ensure_ascii=False), retain=True)

    def _on_disconnect(self, reason: object) -> None:
        self.connected = False
        log.info("MQTT: Verbindung weg (%s), paho versucht es weiter", reason)

    def _send(self, topic: str, payload: str, retain: bool = False) -> bool:
        try:
            info = self.client.publish(topic, payload, qos=1, retain=retain)
        except Exception as exc:  # noqa: BLE001 - siehe oben
            log.warning("MQTT: %s nicht sendbar: %s", topic, exc)
            return False
        return getattr(info, "rc", 0) == 0

    def publish(self, cfg: Config, battery: Battery | None, last: Attempt | None, reachable: bool) -> bool:
        """Zustand schicken, wenn sich etwas geaendert hat. Wirft nie.

        Anders als beim REST-Weg braucht es kein regelmaessiges Wiederholen: die Nachricht
        liegt retained im Broker und ueberlebt dort einen Neustart von Home Assistant.
        """
        if self.client is None:
            return False
        payload = state_payload(battery, last, reachable)
        if payload == self.last_state:
            return False
        _, state_topic = topics(cfg)
        if not self._send(state_topic, json.dumps(payload, ensure_ascii=False), retain=True):
            return False
        self.last_state = payload
        return True

    def stop(self, cfg: Config) -> None:
        """Sauber abmelden. Das Testament greift nur bei einem Abriss, nicht beim Beenden."""
        if self.client is None:
            return
        availability, _ = topics(cfg)
        self._send(availability, OFFLINE, retain=True)
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception as exc:  # noqa: BLE001 - beim Beenden ist jeder Fehler egal
            log.debug("MQTT: Abmelden fehlgeschlagen: %s", exc)
        self.client, self.connected = None, False
