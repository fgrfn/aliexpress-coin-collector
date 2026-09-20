from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

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

_SELECT = "SELECT ts, kind, outcome, coins_before, coins_after, message, duration_s FROM runs"


@dataclass(frozen=True)
class Attempt:
    ts: datetime
    kind: str
    outcome: str
    coins_before: int | None = None
    coins_after: int | None = None
    message: str = ""
    duration_s: float = 0.0


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
            return
        data_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)

    def add(self, attempt: Attempt) -> None:
        if self.read_only:
            raise RuntimeError("Store wurde schreibgeschuetzt geoeffnet")
        self._add(attempt)

    def _add(self, attempt: Attempt) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (ts, day, kind, outcome, coins_before, coins_after, message, duration_s)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    attempt.ts.isoformat(timespec="seconds"),
                    attempt.ts.date().isoformat(),
                    attempt.kind,
                    attempt.outcome,
                    attempt.coins_before,
                    attempt.coins_after,
                    attempt.message,
                    attempt.duration_s,
                ),
            )

    @staticmethod
    def _row(r: tuple) -> Attempt:
        return Attempt(datetime.fromisoformat(r[0]), r[1], r[2], r[3], r[4], r[5] or "", r[6] or 0.0)

    def for_day(self, day: date) -> list[Attempt]:
        cur = self._conn.execute(
            f"{_SELECT} WHERE day=? ORDER BY ts",
            (day.isoformat(),),
        )
        return [self._row(r) for r in cur.fetchall()]

    def recent(self, n: int = 14) -> list[Attempt]:
        cur = self._conn.execute(
            f"{_SELECT} ORDER BY ts DESC LIMIT ?",
            (n,),
        )
        return [self._row(r) for r in cur.fetchall()]

    def outcome_counts(self) -> dict[str, int]:
        cur = self._conn.execute("SELECT outcome, COUNT(*) FROM runs GROUP BY outcome")
        return dict(cur.fetchall())
