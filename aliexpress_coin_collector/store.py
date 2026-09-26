from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
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
CREATE INDEX IF NOT EXISTS idx_runs_day ON runs(day);
"""

# Spalten, die erst spaeter dazugekommen sind. Eine bestehende Datenbank bekommt sie beim
# naechsten Start des Dienstes nachtraeglich; CREATE TABLE IF NOT EXISTS allein taete das nicht.
ADDED_COLUMNS = {
    "battery_level": "INTEGER",
    "battery_temp_c": "REAL",
    "battery_status": "TEXT",
}

_BASE_COLUMNS = ("ts", "kind", "outcome", "coins_before", "coins_after", "message", "duration_s")


@dataclass(frozen=True)
class Attempt:
    ts: datetime
    kind: str
    outcome: str
    coins_before: int | None = None
    coins_after: int | None = None
    message: str = ""
    duration_s: float = 0.0
    # Zustand des Akkus zum Zeitpunkt des Laufs. Bleibt leer, wenn das Geraet nicht erreichbar
    # war oder nichts dazu sagt -- und bei allen Laeufen vor Version 0.14.0.
    battery_level: int | None = None
    battery_temp_c: float | None = None
    battery_status: str = ""


class Store:
    """Zugriff auf die Laufhistorie.

    Mit read_only=True wird nur gelesen und nichts angelegt. Die Weboberflaeche nutzt das: so bleibt
    der Dienst der einzige Schreiber, und es kann keine Sperrkonflikte auf der Datei geben.
    """

    def __init__(self, data_dir: Path, read_only: bool = False) -> None:
        self.path = data_dir / "coins.sqlite3"
        self.read_only = read_only
        if read_only:
            uri = f"file:{self.path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
            self._select = self._build_select()
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._select = self._build_select()

    def _migrate(self) -> None:
        """Fehlende Spalten nachziehen. Laeuft bei jedem Start, tut aber nur beim ersten Mal etwas."""
        have = self._column_names()
        with self._conn:
            for name, typ in ADDED_COLUMNS.items():
                if name not in have:
                    log.info("Datenbank wird erweitert: Spalte %s", name)
                    self._conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")

    def _column_names(self) -> set[str]:
        try:
            return {r[1] for r in self._conn.execute("PRAGMA table_info(runs)")}
        except sqlite3.Error:
            return set()

    def _build_select(self) -> str:
        """Die Abfrage an die vorhandenen Spalten anpassen.

        Noetig wegen der Oberflaeche: sie oeffnet die Datenbank nur lesend und kann darum
        nichts nachziehen. Startet sie nach einem Update vor dem Dienst, fehlen die neuen
        Spalten noch -- dann wird an ihrer Stelle NULL gelesen statt die Seite abzuwerfen.
        """
        have = self._column_names()
        extra = [name if name in have else "NULL" for name in ADDED_COLUMNS]
        return f"SELECT {', '.join((*_BASE_COLUMNS, *extra))} FROM runs"

    def add(self, attempt: Attempt) -> None:
        if self.read_only:
            raise RuntimeError("Store wurde schreibgeschuetzt geoeffnet")
        self._add(attempt)

    def _add(self, attempt: Attempt) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (ts, day, kind, outcome, coins_before, coins_after, message, duration_s,"
                " battery_level, battery_temp_c, battery_status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt.ts.isoformat(timespec="seconds"),
                    attempt.ts.date().isoformat(),
                    attempt.kind,
                    attempt.outcome,
                    attempt.coins_before,
                    attempt.coins_after,
                    attempt.message,
                    attempt.duration_s,
                    attempt.battery_level,
                    attempt.battery_temp_c,
                    attempt.battery_status or None,
                ),
            )

    @staticmethod
    def _row(r: tuple) -> Attempt:
        return Attempt(
            datetime.fromisoformat(r[0]),
            r[1],
            r[2],
            r[3],
            r[4],
            r[5] or "",
            r[6] or 0.0,
            r[7],
            r[8],
            r[9] or "",
        )

    def between(self, start: datetime, end: datetime) -> list[Attempt]:
        """Versuche in einem Zeitraum [start, end). Fuer den Muenztag, der nicht um
        Mitternacht beginnt -- die Spalte `day` traegt weiter den Kalendertag."""
        rows = self._conn.execute(
            f"{self._select} WHERE ts >= ? AND ts < ? ORDER BY ts",
            (start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
        )
        return [self._row(r) for r in rows]

    def for_day(self, day: date) -> list[Attempt]:
        cur = self._conn.execute(
            f"{self._select} WHERE day=? ORDER BY ts",
            (day.isoformat(),),
        )
        return [self._row(r) for r in cur.fetchall()]

    def recent(self, n: int = 14) -> list[Attempt]:
        cur = self._conn.execute(
            f"{self._select} ORDER BY ts DESC LIMIT ?",
            (n,),
        )
        return [self._row(r) for r in cur.fetchall()]

    def outcome_counts(self) -> dict[str, int]:
        cur = self._conn.execute("SELECT outcome, COUNT(*) FROM runs GROUP BY outcome")
        return dict(cur.fetchall())
