"""Die Datenbank, vor allem der Umstieg bestehender Installationen.

Die Akkuspalten kamen erst mit 0.14.0 dazu. Eine Datenbank, die schon Laeufe enthaelt, muss
sie nachtraeglich bekommen -- und die Oberflaeche, die nur lesen darf, muss auch damit
zurechtkommen, dass der Dienst sie noch nicht nachgezogen hat.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from aliexpress_coin_collector.store import ADDED_COLUMNS, Attempt, Store

ALT = """
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    outcome TEXT NOT NULL,
    coins_before INTEGER,
    coins_after INTEGER,
    message TEXT,
    duration_s REAL
);
INSERT INTO runs (ts, day, kind, outcome, coins_before, coins_after, message, duration_s)
VALUES ('2026-09-01T08:10:00', '2026-09-01', 'morning', 'claimed', 10, 25, 'Alter Lauf', 12.5);
"""


def alte_datenbank(tmp_path: Path) -> Path:
    conn = sqlite3.connect(tmp_path / "coins.sqlite3")
    conn.executescript(ALT)
    conn.commit()
    conn.close()
    return tmp_path


def spalten(tmp_path: Path) -> set[str]:
    conn = sqlite3.connect(tmp_path / "coins.sqlite3")
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()


def test_an_existing_database_gets_the_new_columns(tmp_path):
    alte_datenbank(tmp_path)
    Store(tmp_path)
    assert set(ADDED_COLUMNS) <= spalten(tmp_path)


def test_the_old_rows_survive_the_migration(tmp_path):
    alte_datenbank(tmp_path)
    rows = Store(tmp_path).recent(5)
    assert len(rows) == 1
    assert rows[0].outcome == "claimed"
    assert rows[0].coins_after == 25
    assert rows[0].battery_level is None


def test_migrating_twice_changes_nothing(tmp_path):
    alte_datenbank(tmp_path)
    Store(tmp_path)
    Store(tmp_path)
    assert len(Store(tmp_path).recent(5)) == 1


def test_the_web_can_read_a_database_the_service_has_not_migrated_yet(tmp_path):
    """Sonst faellt die Oberflaeche um, wenn sie nach einem Update vor dem Dienst startet."""
    alte_datenbank(tmp_path)
    rows = Store(tmp_path, read_only=True).recent(5)
    assert len(rows) == 1
    assert rows[0].battery_level is None
    assert rows[0].battery_status == ""


def test_the_battery_of_a_run_is_stored_and_read_back(tmp_path):
    store = Store(tmp_path)
    store.add(
        Attempt(
            ts=datetime(2026, 9, 22, 8, 30),
            kind="morning",
            outcome="claimed",
            coins_after=40,
            battery_level=87,
            battery_temp_c=31.5,
            battery_status="lädt",
        )
    )
    back = store.recent(1)[0]
    assert (back.battery_level, back.battery_temp_c, back.battery_status) == (87, 31.5, "lädt")


def test_a_run_without_a_battery_reading_stays_empty(tmp_path):
    store = Store(tmp_path)
    store.add(Attempt(ts=datetime(2026, 9, 22, 8, 30), kind="morning", outcome="unreachable"))
    back = store.recent(1)[0]
    assert back.battery_level is None
    assert back.battery_status == ""


def test_a_read_only_store_still_refuses_to_write(tmp_path):
    Store(tmp_path)
    with pytest.raises(RuntimeError):
        Store(tmp_path, read_only=True).add(Attempt(ts=datetime.now(), kind="manual", outcome="claimed"))
