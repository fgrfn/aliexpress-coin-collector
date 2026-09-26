"""Der MQTT-Weg nach Home Assistant.

Drei Dinge sind hier wichtig und eigens geprueft: das Testament (ohne das waere MQTT gegenueber
der REST-API kein Gewinn), dass Passwoerter nirgends auftauchen, und dass ein nicht erreichbarer
Broker den Dienst nicht aus dem Tritt bringt.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime

import pytest

from aliexpress_coin_collector import mqtt, scheduler
from aliexpress_coin_collector.adb import Battery
from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.store import Attempt

PASSWORT = "streng-geheimes-broker-passwort"
AKKU = Battery(level=72, status=2, health=2, temperature_c=30.5, voltage_mv=4100, plugged=True)
LAUF = Attempt(ts=datetime(2026, 9, 22, 8, 15), kind="morning", outcome="claimed", coins_after=315)


@pytest.fixture
def cfg(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "homeassistant.local")
    monkeypatch.setenv("MQTT_USER", "coin-collector")
    monkeypatch.setenv("MQTT_PASSWORD", PASSWORT)
    return Config.load(tmp_path / "keine.env")


class FakeInfo:
    rc = 0


class FakeClient:
    """Ein Broker, der nur mitschreibt. Kein Netz, kein Faden, kein Warten."""

    def __init__(self, *args, **kw):
        self.published: list[tuple[str, str, bool]] = []
        self.will: tuple[str, str, bool] | None = None
        self.credentials: tuple[str, str] | None = None
        self.on_connect = self.on_disconnect = None
        self.started = self.stopped = False

    def username_pw_set(self, user, password):
        self.credentials = (user, password)

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, retain)

    def reconnect_delay_set(self, **kw):
        pass

    def connect_async(self, host, port, keepalive):
        self.target = (host, port)

    def loop_start(self):
        self.started = True

    def loop_stop(self):
        self.stopped = True

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))
        return FakeInfo()

    def topics(self) -> list[str]:
        return [t for t, _p, _r in self.published]


@pytest.fixture
def broker(monkeypatch) -> FakeClient:
    client = FakeClient()
    monkeypatch.setattr(mqtt, "HAVE_PAHO", True)
    monkeypatch.setattr(
        mqtt,
        "paho",
        type("paho", (), {"Client": lambda *a, **k: client, "CallbackAPIVersion": type("V", (), {"VERSION2": 2})}),
    )
    return client


def verbinde(cfg, pub, client) -> None:
    """Den Verbindungsaufbau nachstellen, den paho sonst im Hintergrund meldet."""
    pub.start(cfg)
    client.on_connect(client, None, {}, type("Reason", (), {"is_failure": False})())


# -- Nutzlasten, ohne Broker pruefbar ------------------------------------------------------


def test_every_entity_hangs_on_one_device(cfg):
    geraete = {json.dumps(e.config["device"], sort_keys=True) for e in mqtt.entities(cfg)}
    assert len(geraete) == 1


def test_every_entity_has_a_unique_id_so_it_can_be_renamed(cfg):
    ids = [e.config["unique_id"] for e in mqtt.entities(cfg)]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("coin_collector_") for i in ids)


def test_every_entity_knows_the_availability_topic(cfg):
    availability, _state = mqtt.topics(cfg)
    assert all(e.config["availability_topic"] == availability for e in mqtt.entities(cfg))


def test_the_discovery_topic_follows_the_convention(cfg):
    akku = next(e for e in mqtt.entities(cfg) if e.object_id == "akku")
    assert akku.topic(cfg) == "homeassistant/sensor/coin_collector/akku/config"


def test_the_discovery_prefix_can_be_changed(cfg):
    anders = replace(cfg, mqtt_discovery_prefix="hass")
    assert mqtt.entities(anders)[0].topic(anders).startswith("hass/")


def test_the_battery_entity_carries_the_units_home_assistant_expects(cfg):
    akku = next(e for e in mqtt.entities(cfg) if e.object_id == "akku")
    assert akku.config["device_class"] == "battery"
    assert akku.config["unit_of_measurement"] == "%"


def test_unknown_values_are_left_out_so_the_template_says_unknown(cfg):
    """Ein erfundener Wert waere schlimmer als eine Luecke, gerade beim Ladestand."""
    payload = mqtt.state_payload(Battery(status=3, plugged=False), None, False)
    assert "level" not in payload
    assert "temperature" not in payload
    akku = next(e for e in mqtt.entities(cfg) if e.object_id == "akku")
    assert "default('unknown')" in akku.config["value_template"]


def test_the_payload_carries_what_is_known(cfg):
    payload = mqtt.state_payload(AKKU, LAUF, True)
    assert payload["level"] == 72
    assert payload["temperature"] == 30.5
    assert payload["coins"] == 315
    assert payload["outcome"] == "claimed"
    assert payload["reachable"] == "on"


def test_reachability_is_on_or_off(cfg):
    assert mqtt.state_payload(None, None, False)["reachable"] == "off"


# -- Verbindung und Senden -----------------------------------------------------------------


def test_the_will_makes_the_entities_unavailable_when_the_service_dies(cfg, broker):
    """Genau das kann die REST-API nicht, und darum gibt es diesen Weg ueberhaupt."""
    mqtt.Publisher().start(cfg)
    availability, _ = mqtt.topics(cfg)
    assert broker.will == (availability, mqtt.OFFLINE, True)


def test_connecting_announces_the_entities_and_reports_online(cfg, broker):
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    themen = broker.topics()
    assert sum(1 for t in themen if t.endswith("/config")) == len(mqtt.entities(cfg))
    availability, _ = mqtt.topics(cfg)
    assert (availability, mqtt.ONLINE, True) in broker.published


def test_configuration_and_state_are_retained_so_they_survive_a_restart(cfg, broker):
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    pub.publish(cfg, AKKU, LAUF, True)
    assert all(retain for _t, _p, retain in broker.published)


def test_an_unchanged_state_is_not_sent_again(cfg, broker):
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    assert pub.publish(cfg, AKKU, LAUF, True) is True
    assert pub.publish(cfg, AKKU, LAUF, True) is False


def test_a_reconnect_sends_the_last_state_again(cfg, broker):
    """Der Broker koennte zwischendurch neu gestartet worden sein und alles verloren haben."""
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    pub.publish(cfg, AKKU, LAUF, True)
    broker.published.clear()
    broker.on_connect(broker, None, {}, type("Reason", (), {"is_failure": False})())
    _, state_topic = mqtt.topics(cfg)
    assert any(t == state_topic for t, _p, _r in broker.published)


def test_the_password_goes_to_the_client_and_nowhere_else(cfg, broker, caplog):
    with caplog.at_level(logging.DEBUG):
        pub = mqtt.Publisher()
        verbinde(cfg, pub, broker)
        pub.publish(cfg, AKKU, LAUF, True)
    assert broker.credentials == ("coin-collector", PASSWORT)
    assert PASSWORT not in caplog.text
    assert all(PASSWORT not in payload for _t, payload, _r in broker.published)


def test_a_rejected_login_is_logged_without_the_password(cfg, broker, caplog):
    pub = mqtt.Publisher()
    pub.start(cfg)
    with caplog.at_level(logging.WARNING):
        broker.on_connect(broker, None, {}, type("Reason", (), {"is_failure": True})())
    assert pub.connected is False
    assert PASSWORT not in caplog.text
    assert "abgelehnt" in caplog.text


def test_without_a_broker_nothing_starts(cfg, broker):
    assert mqtt.Publisher().start(replace(cfg, mqtt_host="")) is False
    assert broker.published == []


def test_a_missing_library_is_said_out_loud_and_does_not_crash(cfg, monkeypatch, caplog):
    monkeypatch.setattr(mqtt, "HAVE_PAHO", False)
    with caplog.at_level(logging.ERROR):
        assert mqtt.Publisher().start(cfg) is False
    assert "paho-mqtt" in caplog.text


def test_a_broker_that_refuses_the_connection_never_throws(cfg, monkeypatch):
    def boom(*a, **k):
        raise OSError("Name oder Dienst nicht bekannt")

    monkeypatch.setattr(mqtt, "HAVE_PAHO", True)
    monkeypatch.setattr(mqtt, "paho", type("paho", (), {"Client": boom, "CallbackAPIVersion": type("V", (), {})}))
    assert mqtt.Publisher().start(cfg) is False


def test_publishing_without_a_connection_does_nothing(cfg):
    assert mqtt.Publisher().publish(cfg, AKKU, LAUF, True) is False


def test_stopping_says_goodbye_properly(cfg, broker):
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    broker.published.clear()
    pub.stop(cfg)
    availability, _ = mqtt.topics(cfg)
    assert (availability, mqtt.OFFLINE, True) in broker.published
    assert broker.stopped is True


# -- Gegen einen echten Broker -------------------------------------------------------------
#
# Laeuft nur, wo mosquitto installiert ist, und wird sonst uebersprungen -- auch in der CI.
# Die Attrappen oben pruefen die Logik; hier geht es um das, was eine Attrappe nicht zeigen
# kann: dass ein echter Broker die Nachrichten annimmt, sie retained wieder herausgibt und
# das Testament wirklich faellt, wenn der Dienst stirbt.

import pathlib  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import textwrap  # noqa: E402
import time  # noqa: E402


def pathlib_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


echter_broker = pytest.mark.skipif(shutil.which("mosquitto") is None, reason="mosquitto ist nicht installiert")
PORT = 18855


@pytest.fixture
def mosquitto(tmp_path):
    conf = tmp_path / "mosquitto.conf"
    conf.write_text(f"listener {PORT} 127.0.0.1\nallow_anonymous true\n")
    proc = subprocess.Popen(["mosquitto", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    yield PORT
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def lokal(tmp_path, monkeypatch, mosquitto) -> Config:
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MQTT_HOST", "127.0.0.1")
    monkeypatch.setenv("MQTT_PORT", str(mosquitto))
    monkeypatch.delenv("MQTT_USER", raising=False)
    monkeypatch.delenv("MQTT_PASSWORD", raising=False)
    return Config.load(tmp_path / "keine.env")


def mithoerer(port: int, client_id: str) -> tuple[object, dict[str, str]]:
    import paho.mqtt.client as paho

    gesehen: dict[str, str] = {}
    client = paho.Client(paho.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.on_message = lambda c, u, m: gesehen.__setitem__(m.topic, m.payload.decode())
    client.connect("127.0.0.1", port, 30)
    client.subscribe("#", qos=1)
    client.loop_start()
    return client, gesehen


def warte_auf(gesehen: dict, topic: str, wert: str | None = None, sekunden: float = 8.0) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if topic in gesehen and (wert is None or gesehen[topic] == wert):
            return True
        time.sleep(0.1)
    return False


@echter_broker
def test_a_real_broker_takes_the_discovery_and_the_state(lokal):
    client, gesehen = mithoerer(lokal.mqtt_port, "test-lauscher")
    try:
        pub = mqtt.Publisher()
        assert pub.start(lokal)
        availability, state = mqtt.topics(lokal)
        assert warte_auf(gesehen, availability, mqtt.ONLINE), "keine Verfuegbarkeitsmeldung"
        pub.publish(lokal, AKKU, LAUF, True)
        assert warte_auf(gesehen, state), "kein Zustand"
        for entity in mqtt.entities(lokal):
            assert entity.topic(lokal) in gesehen, entity.object_id
        assert json.loads(gesehen[state])["level"] == 72
        pub.stop(lokal)
    finally:
        client.loop_stop()


@echter_broker
def test_a_fresh_listener_gets_everything_at_once(lokal):
    """Retained: nach einem Neustart von Home Assistant sind die Entitaeten sofort wieder da."""
    pub = mqtt.Publisher()
    pub.start(lokal)
    _availability, state = mqtt.topics(lokal)
    erster, gesehen = mithoerer(lokal.mqtt_port, "test-erster")
    try:
        assert warte_auf(gesehen, state) or pub.publish(lokal, AKKU, LAUF, True)
        pub.publish(lokal, AKKU, LAUF, True)
        time.sleep(1.0)
    finally:
        erster.loop_stop()
    zweiter, frisch = mithoerer(lokal.mqtt_port, "test-zweiter")
    try:
        assert warte_auf(frisch, state), "der Zustand lag nicht retained im Broker"
    finally:
        zweiter.loop_stop()
        pub.stop(lokal)


@echter_broker
def test_the_will_really_fires_when_the_service_is_killed(lokal):
    """Der Grund, warum es MQTT neben der REST-API gibt. Ohne Beleg waere es nur eine Behauptung."""
    kind = textwrap.dedent(f"""
        import os, sys, time
        sys.path.insert(0, {str(pathlib_root())!r})
        os.environ.update(ADB_SERIAL="10.0.0.5:5555", DATA_DIR={str(lokal.data_dir)!r},
                          MQTT_HOST="127.0.0.1", MQTT_PORT="{lokal.mqtt_port}")
        from aliexpress_coin_collector import mqtt
        from aliexpress_coin_collector.config import Config
        cfg = Config.load({str(lokal.data_dir / "keine.env")!r})
        mqtt.Publisher().start(cfg)
        while True:
            time.sleep(1)
    """)
    script = lokal.data_dir / "dienst.py"
    script.write_text(kind)
    client, gesehen = mithoerer(lokal.mqtt_port, "test-testament")
    dienst = subprocess.Popen([sys.executable, str(script)])
    try:
        availability, _ = mqtt.topics(lokal)
        assert warte_auf(gesehen, availability, mqtt.ONLINE), "der Dienst hat sich nie angemeldet"
        dienst.send_signal(signal.SIGKILL)
        dienst.wait(timeout=5)
        assert warte_auf(gesehen, availability, mqtt.OFFLINE), "das Testament ist nicht gefallen"
    finally:
        if dienst.poll() is None:
            dienst.kill()
        client.loop_stop()


# -- Veraltete Messwerte -------------------------------------------------------------------
#
# Das Testament faengt nur ab, dass der Dienst stirbt. Lebt er, erreicht aber das Geraet nicht,
# laege der letzte Ladestand retained im Broker und gaelte als aktueller Wert -- genau der Fall,
# wenn das Handy ohne Strom in den Ruhezustand geht. Dafuer ist expire_after da.


def messwerte(cfg) -> list[mqtt.Entity]:
    return [e for e in mqtt.entities(cfg) if e.object_id.startswith("akku")]


def test_the_measurements_expire_after_three_missed_polls(cfg):
    assert mqtt.measurement_ttl(replace(cfg, battery_poll_min=15)) == 2700
    for e in messwerte(replace(cfg, battery_poll_min=15)):
        assert e.config["expire_after"] == 2700


def test_without_regular_polling_nothing_expires(cfg):
    """Ein Lauf am Tag wuerde jede Frist reissen -- dann bleibt der Wert lieber stehen."""
    aus = replace(cfg, battery_poll_min=0)
    assert mqtt.measurement_ttl(aus) is None
    for e in mqtt.entities(aus):
        assert "expire_after" not in e.config


def test_only_the_measurements_expire(cfg):
    """Erreichbarkeit geht in jedem Takt raus, letzter Lauf und Muenzstand aendern sich taeglich."""
    andere = [e for e in mqtt.entities(cfg) if not e.object_id.startswith("akku")]
    assert andere
    for e in andere:
        assert "expire_after" not in e.config


def test_a_fresh_measurement_is_sent_even_when_nothing_changed(cfg, broker):
    """Sonst schriebe Home Assistant den Sensor ab, waehrend das Handy brav auf 100 % steht."""
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    pub.publish(cfg, AKKU, LAUF, True)
    broker.published.clear()
    assert pub.publish(cfg, AKKU, LAUF, True, measured=True) is True
    _, state = mqtt.topics(cfg)
    assert [t for t, _p, _r in broker.published] == [state]


def test_without_a_measurement_an_unchanged_state_stays_unsent(cfg, broker):
    pub = mqtt.Publisher()
    verbinde(cfg, pub, broker)
    pub.publish(cfg, AKKU, LAUF, True)
    broker.published.clear()
    assert pub.publish(cfg, AKKU, LAUF, True) is False
    assert broker.published == []


# -- Der Anschluss folgt den Einstellungen ---------------------------------------------------
#
# Der Dienst entschied beim Start ein fuer alle Mal, ob MQTT laeuft. Wer die Broker-Daten
# spaeter in der Oberflaeche eintrug, wartete vergeblich -- jede andere Einstellung wird bei
# jedem Takt neu gelesen, diese nicht.


def test_settings_entered_later_bring_the_link_up(cfg, broker):
    """Der Fehler, der dazu fuehrte, dass in Home Assistant nichts ankam."""
    aus = replace(cfg, mqtt_host="")
    link = scheduler.BrokerLink()
    assert link.ensure(aus) is None
    assert link.ensure(cfg) is not None


def test_an_unchanged_setting_keeps_the_same_link(cfg, broker):
    link = scheduler.BrokerLink()
    erster = link.ensure(cfg)
    assert link.ensure(cfg) is erster


def test_a_changed_broker_is_reconnected(cfg, broker):
    link = scheduler.BrokerLink()
    erster = link.ensure(cfg)
    zweiter = link.ensure(replace(cfg, mqtt_host="anderer.local"))
    assert zweiter is not erster


def test_a_changed_prefix_reannounces_the_entities(cfg, broker):
    """Aus dem Praefix entstehen die Namen der Entitaeten -- die muessen neu angemeldet werden."""
    link = scheduler.BrokerLink()
    erster = link.ensure(cfg)
    assert link.ensure(replace(cfg, ha_prefix="handy")) is not erster


def test_a_changed_poll_interval_reannounces_the_entities(cfg, broker):
    """Der Messabstand steckt als expire_after in der Discovery-Konfiguration."""
    link = scheduler.BrokerLink()
    erster = link.ensure(cfg)
    assert link.ensure(replace(cfg, battery_poll_min=5)) is not erster


def test_switching_mqtt_off_says_goodbye(cfg, broker):
    link = scheduler.BrokerLink()
    verbinde(cfg, link.ensure(cfg), broker)
    broker.published.clear()
    assert link.ensure(replace(cfg, mqtt_host="")) is None
    availability, _ = mqtt.topics(cfg)
    assert (availability, mqtt.OFFLINE, True) in broker.published


def test_the_goodbye_uses_the_prefix_it_signed_on_with(cfg, broker):
    """Sonst bliebe unter dem alten Namen eine Entitaet stehen, die niemand mehr abmeldet."""
    link = scheduler.BrokerLink()
    verbinde(cfg, link.ensure(cfg), broker)
    broker.published.clear()
    link.ensure(replace(cfg, ha_prefix="handy"))
    alt, _ = mqtt.topics(cfg)
    assert (alt, mqtt.OFFLINE, True) in broker.published


def test_a_failed_start_is_not_retried_every_tick(cfg, monkeypatch, caplog):
    """paho verbindet sich selbst neu; ein fehlendes paho heilt nicht durch Warten."""
    monkeypatch.setattr(mqtt, "HAVE_PAHO", False)
    link = scheduler.BrokerLink()
    with caplog.at_level(logging.ERROR):
        assert link.ensure(cfg) is None
        assert link.ensure(cfg) is None
    # Ein Eintrag, nicht zwei: sonst liefe das Protokoll bei jedem Takt voll.
    assert len([r for r in caplog.records if "paho-mqtt" in r.getMessage()]) == 1
