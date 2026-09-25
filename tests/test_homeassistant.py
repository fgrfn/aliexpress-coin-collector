"""Die Anbindung an Home Assistant.

Zwei Dinge sind hier wichtig und darum eigens geprueft: das Token darf nirgends auftauchen,
und ein nicht erreichbares Home Assistant darf den Dienst nicht aus dem Tritt bringen.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import requests

from aliexpress_coin_collector import homeassistant as ha
from aliexpress_coin_collector.adb import Battery
from aliexpress_coin_collector.config import Config
from aliexpress_coin_collector.store import Attempt

TOKEN = "streng.geheimes.langzeit.token"
JETZT = datetime(2026, 9, 22, 12, 0)
AKKU = Battery(level=72, status=2, health=2, temperature_c=30.5, voltage_mv=4100, plugged=True)
LAUF = Attempt(ts=datetime(2026, 9, 22, 8, 15), kind="morning", outcome="claimed", coins_after=315)


@pytest.fixture
def cfg(tmp_path, monkeypatch) -> Config:
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HA_URL", "http://homeassistant.local:8123")
    monkeypatch.setenv("HA_TOKEN", TOKEN)
    return Config.load(tmp_path / "keine.env")


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.text = ""


@pytest.fixture
def posts(monkeypatch) -> list[dict]:
    """Faengt die Anfragen ab, statt eines wirklich laufenden Home Assistant."""
    seen: list[dict] = []

    def fake_post(url, **kw):
        seen.append({"url": url, **kw})
        return FakeResponse(kw.pop("_status", 200))

    monkeypatch.setattr(ha.requests, "post", fake_post)
    return seen


# -- Woraus die Entitaeten bestehen -------------------------------------------------------


def test_every_entity_carries_the_configured_prefix(cfg):
    ids = [e.entity_id for e in ha.states(cfg, AKKU, LAUF, True)]
    assert all("coin_collector" in i for i in ids)
    assert "sensor.coin_collector_akku" in ids
    assert "binary_sensor.coin_collector_erreichbar" in ids


def test_the_prefix_can_be_changed(cfg):
    ids = [e.entity_id for e in ha.states(replace(cfg, ha_prefix="handy"), AKKU, LAUF, True)]
    assert "sensor.handy_akku" in ids


def test_the_battery_arrives_with_the_units_home_assistant_expects(cfg):
    akku = next(e for e in ha.states(cfg, AKKU, LAUF, True) if e.entity_id.endswith("_akku"))
    assert akku.state == "72"
    assert akku.attributes["device_class"] == "battery"
    assert akku.attributes["unit_of_measurement"] == "%"
    assert akku.attributes["am_strom"] is True


def test_the_temperature_becomes_its_own_entity(cfg):
    temp = next(e for e in ha.states(cfg, AKKU, LAUF, True) if e.entity_id.endswith("_akku_temperatur"))
    assert temp.state == "30.5"
    assert temp.attributes["device_class"] == "temperature"


def test_a_device_without_a_temperature_gets_no_temperature_entity(cfg):
    ids = [e.entity_id for e in ha.states(cfg, replace(AKKU, temperature_c=None), LAUF, True)]
    assert not any(i.endswith("_akku_temperatur") for i in ids)


def test_reachability_is_a_binary_sensor(cfg):
    an = next(e for e in ha.states(cfg, AKKU, LAUF, True) if e.entity_id.startswith("binary_sensor."))
    aus = next(e for e in ha.states(cfg, AKKU, LAUF, False) if e.entity_id.startswith("binary_sensor."))
    assert (an.state, aus.state) == ("on", "off")


def test_an_unknown_value_is_reported_as_unknown_not_left_out(cfg):
    """Eine Entitaet, die mal da ist und mal nicht, taugt in einer Automatisierung nichts."""
    muenzen = next(
        e for e in ha.states(cfg, AKKU, replace(LAUF, coins_after=None), True) if e.entity_id.endswith("_muenzen")
    )
    assert muenzen.state == "unknown"


def test_without_a_reading_only_reachability_is_reported(cfg):
    entries = ha.states(cfg, None, None, True)
    assert [e.entity_id for e in entries] == ["binary_sensor.coin_collector_erreichbar"]


# -- Schicken ------------------------------------------------------------------------------


def test_the_token_goes_in_the_header_and_never_in_the_url(cfg, posts):
    ha.Publisher().publish(cfg, ha.states(cfg, AKKU, LAUF, True), JETZT)
    assert posts
    for call in posts:
        assert TOKEN not in call["url"]
        assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_the_token_never_reaches_the_log(cfg, posts, monkeypatch, caplog):
    monkeypatch.setattr(ha.requests, "post", lambda url, **kw: FakeResponse(401))
    with caplog.at_level(logging.DEBUG):
        ha.Publisher().publish(cfg, ha.states(cfg, AKKU, LAUF, True), JETZT)
    assert TOKEN not in caplog.text
    assert "Token" in caplog.text  # der Hinweis selbst kommt schon


def test_nothing_is_sent_without_a_configured_address(cfg, posts):
    aus = replace(cfg, ha_url="", ha_token="")
    assert ha.Publisher().publish(aus, ha.states(cfg, AKKU, LAUF, True), JETZT) == 0
    assert posts == []


def test_unchanged_states_are_not_sent_again(cfg, posts):
    pub = ha.Publisher()
    entries = ha.states(cfg, AKKU, LAUF, True)
    first = pub.publish(cfg, entries, JETZT)
    assert first == len(entries)
    posts.clear()
    assert pub.publish(cfg, entries, JETZT + timedelta(minutes=1)) == 0
    assert posts == []


def test_a_changed_value_goes_out_on_its_own(cfg, posts):
    pub = ha.Publisher()
    pub.publish(cfg, ha.states(cfg, AKKU, LAUF, True), JETZT)
    posts.clear()
    pub.publish(cfg, ha.states(cfg, replace(AKKU, level=71), LAUF, True), JETZT + timedelta(minutes=1))
    assert len(posts) == 1
    assert posts[0]["url"].endswith("sensor.coin_collector_akku")


def test_everything_is_repeated_after_a_while(cfg, posts):
    """Nach einem Neustart von Home Assistant sind die Entitaeten weg, bis wieder etwas kommt."""
    pub = ha.Publisher()
    entries = ha.states(cfg, AKKU, LAUF, True)
    pub.publish(cfg, entries, JETZT)
    posts.clear()
    pub.publish(cfg, entries, JETZT + timedelta(minutes=ha.KEEPALIVE_MIN))
    assert len(posts) == len(entries)


def test_an_unreachable_home_assistant_never_throws(cfg, monkeypatch):
    def boom(url, **kw):
        raise requests.ConnectionError("kein Netz")

    monkeypatch.setattr(ha.requests, "post", boom)
    assert ha.Publisher().publish(cfg, ha.states(cfg, AKKU, LAUF, True), JETZT) == 0


def test_a_rejected_state_is_not_remembered_as_sent(cfg, monkeypatch):
    """Sonst bliebe es nach einem abgelehnten Versuch bis zum Keepalive bei der Luecke."""
    monkeypatch.setattr(ha.requests, "post", lambda url, **kw: FakeResponse(500))
    pub = ha.Publisher()
    entries = ha.states(cfg, AKKU, LAUF, True)
    assert pub.publish(cfg, entries, JETZT) == 0
    monkeypatch.setattr(ha.requests, "post", lambda url, **kw: FakeResponse(200))
    assert pub.publish(cfg, entries, JETZT + timedelta(seconds=30)) == len(entries)
