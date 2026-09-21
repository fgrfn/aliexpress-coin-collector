"""Befehlsablage zwischen Weboberflaeche und Sammel-Dienst.

Die Oberflaeche fasst das Geraet nie selbst an. Sie legt einen Auftrag ab, der Dienst holt ihn
beim naechsten Takt und fuehrt ihn aus. Damit bleibt genau ein Besitzer des Geraets, und zwei
gleichzeitige Zugriffe sind bauartbedingt ausgeschlossen.

Je Datei genau ein Schreiber: die Oberflaeche legt in data/commands/ an und liest nur,
der Dienst verbraucht dort und schreibt das Ergebnis nach data/commands/done/.

Der Preis ist die Wartezeit von bis zu einem Takt, also 30 Sekunden. Die Oberflaeche zeigt
den Auftrag so lange als "wartet" an, statt sie zu verstecken.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

QUEUE_DIR = "commands"
DONE_DIR = "done"
STATUS_FILE = "status.json"

# Was die Oberflaeche anfordern darf. Alles andere wird abgelehnt, statt es durchzureichen.
RUN = "run"
RECONNECT = "reconnect"
SCREENSHOT = "screenshot"
CHECK = "check"
KNOWN = (RUN, RECONNECT, SCREENSHOT, CHECK)

LABELS = {
    RUN: "Lauf starten",
    RECONNECT: "Neu verbinden",
    SCREENSHOT: "Screenshot holen",
    CHECK: "Zustand prüfen",
}

WAITING = "wartet"
DONE = "erledigt"
FAILED = "fehlgeschlagen"

KEEP_DONE = 20  # so viele erledigte Auftraege bleiben sichtbar
MAX_WAITING = 10  # mehr offene Auftraege sind ein Zeichen, dass der Dienst steht


class CommandError(ValueError):
    """Der Auftrag ist nicht annehmbar. Der Text geht an den Nutzer."""


@dataclass(frozen=True)
class Command:
    ident: str
    name: str
    requested_at: datetime
    status: str = WAITING
    finished_at: datetime | None = None
    message: str = ""

    @property
    def label(self) -> str:
        return LABELS.get(self.name, self.name)

    @property
    def tone(self) -> str:
        return {WAITING: "warn", DONE: "ok", FAILED: "bad"}.get(self.status, "")


def _queue(data_dir: Path) -> Path:
    return data_dir / QUEUE_DIR


def _done(data_dir: Path) -> Path:
    return data_dir / QUEUE_DIR / DONE_DIR


def _to_json(cmd: Command) -> str:
    return json.dumps(
        {
            "id": cmd.ident,
            "name": cmd.name,
            "requested_at": cmd.requested_at.isoformat(timespec="seconds"),
            "status": cmd.status,
            "finished_at": cmd.finished_at.isoformat(timespec="seconds") if cmd.finished_at else None,
            "message": cmd.message,
        },
        ensure_ascii=False,
    )


def _from_json(raw: str) -> Command | None:
    """Unlesbares wird uebersprungen statt zu werfen: ein kaputter Auftrag darf den Dienst
    nicht anhalten und die Seite nicht leeren."""
    try:
        d = json.loads(raw)
        finished = d.get("finished_at")
        return Command(
            ident=str(d["id"]),
            name=str(d["name"]),
            requested_at=datetime.fromisoformat(d["requested_at"]),
            status=str(d.get("status", WAITING)),
            finished_at=datetime.fromisoformat(finished) if finished else None,
            message=str(d.get("message", "")),
        )
    except (ValueError, KeyError, TypeError):
        return None


def _write(path: Path, text: str) -> None:
    """Erst daneben schreiben, dann umbenennen: ein Leser sieht nie eine halbe Datei."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_dir(folder: Path) -> list[Command]:
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        try:
            cmd = _from_json(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if cmd is not None:
            out.append(cmd)
    return out


# ---------------------------------------------------------------- Weboberflaeche


def submit(data_dir: Path, name: str, now: datetime | None = None) -> Command:
    """Auftrag ablegen. Wirft CommandError, wenn er nicht annehmbar ist."""
    if name not in KNOWN:
        raise CommandError("Unbekannter Auftrag.")
    now = now or datetime.now()

    waiting = pending(data_dir)
    if any(c.name == name for c in waiting):
        raise CommandError(f"„{LABELS[name]}“ wartet bereits auf den Dienst.")
    if len(waiting) >= MAX_WAITING:
        raise CommandError("Es warten schon zu viele Aufträge. Läuft der Sammel-Dienst überhaupt?")

    ident = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    cmd = Command(ident=ident, name=name, requested_at=now)
    folder = _queue(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    _write(folder / f"{ident}.json", _to_json(cmd))
    return cmd


def pending(data_dir: Path) -> list[Command]:
    """Was noch auf den Dienst wartet, aeltestes zuerst."""
    return _load_dir(_queue(data_dir))


def finished(data_dir: Path, limit: int = KEEP_DONE) -> list[Command]:
    """Zuletzt erledigte Auftraege, neueste zuerst."""
    return sorted(_load_dir(_done(data_dir)), key=lambda c: c.requested_at, reverse=True)[:limit]


def recent(data_dir: Path, limit: int = KEEP_DONE) -> list[Command]:
    """Offene und erledigte Auftraege zusammen: offene oben, danach die juengsten erledigten."""
    return pending(data_dir) + finished(data_dir, limit)


# ---------------------------------------------------------------- Sammel-Dienst


def take(data_dir: Path) -> list[Command]:
    """Alle offenen Auftraege uebernehmen, aeltestes zuerst.

    Die Dateien bleiben liegen, bis finish() sie verschiebt -- so bleibt ein Auftrag sichtbar,
    waehrend er laeuft, und geht bei einem Absturz mitten drin nicht still verloren.
    """
    return pending(data_dir)


def finish(data_dir: Path, cmd: Command, ok: bool, message: str = "", now: datetime | None = None) -> None:
    """Auftrag abschliessen: Ergebnis nach done/ schreiben, den offenen Eintrag entfernen."""
    done = Command(
        ident=cmd.ident,
        name=cmd.name,
        requested_at=cmd.requested_at,
        status=DONE if ok else FAILED,
        finished_at=now or datetime.now(),
        message=message,
    )
    folder = _done(data_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _write(folder / f"{cmd.ident}.json", _to_json(done))
        (_queue(data_dir) / f"{cmd.ident}.json").unlink(missing_ok=True)
        for old in sorted(folder.glob("*.json"))[:-KEEP_DONE]:
            old.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("Auftrag %s konnte nicht abgeschlossen werden: %s", cmd.ident, exc)


# ---------------------------------------------------------------- Zustandsmeldung


@dataclass(frozen=True)
class Status:
    """Was der Dienst ueber sich und das Geraet meldet."""

    written_at: datetime
    version: str
    device_state: str = "unbekannt"
    screen_on: bool | None = None
    checked_at: datetime | None = None

    @property
    def device_tone(self) -> str:
        return {"device": "ok", "unauthorized": "warn"}.get(self.device_state, "bad")

    @property
    def device_label(self) -> str:
        return {
            "device": "verbunden und freigegeben",
            "unauthorized": "verbunden, aber nicht freigegeben",
            "offline": "nicht erreichbar",
        }.get(self.device_state, "unbekannt")


def write_status(
    data_dir: Path,
    version: str,
    device_state: str,
    screen_on: bool | None,
    now: datetime | None = None,
) -> None:
    """Der Dienst meldet seinen Blick auf das Geraet. Ein Fehler hier haelt ihn nie an.

    Die Geraeteadresse steht bewusst nicht drin: die Seite kennt sie aus der eigenen
    Konfiguration und zeigt sie nur verkuerzt.
    """
    now = now or datetime.now()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _write(
            data_dir / STATUS_FILE,
            json.dumps(
                {
                    "written_at": now.isoformat(timespec="seconds"),
                    "version": version,
                    "device_state": device_state,
                    "screen_on": screen_on,
                    "checked_at": now.isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
        )
    except OSError as exc:
        log.warning("Zustand konnte nicht geschrieben werden: %s", exc)


def read_status(data_dir: Path) -> Status | None:
    """Was der Dienst zuletzt gemeldet hat. None, wenn er noch nie etwas geschrieben hat."""
    try:
        d = json.loads((data_dir / STATUS_FILE).read_text(encoding="utf-8"))
        checked = d.get("checked_at")
        return Status(
            written_at=datetime.fromisoformat(d["written_at"]),
            version=str(d.get("version", "")),
            device_state=str(d.get("device_state", "unbekannt")),
            screen_on=d.get("screen_on"),
            checked_at=datetime.fromisoformat(checked) if checked else None,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def mask_serial(serial: str) -> str:
    """Adresse fuer die Anzeige verkuerzen: 10.10.30.169:5555 wird zu 10.10.30.xxx:5555.

    Die vollstaendige Adresse gehoert weder in einen Screenshot der Seite noch in einen
    Browserverlauf. Wer sie braucht, findet sie in der .env.
    """
    if not serial:
        return "nicht eingetragen"
    host, _, port = serial.rpartition(":")
    if not host:
        return serial[:4] + "…"  # USB-Seriennummer
    parts = host.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3] + ["xxx"]) + (f":{port}" if port else "")
    return host[: max(1, len(host) - 3)] + "…" + (f":{port}" if port else "")
