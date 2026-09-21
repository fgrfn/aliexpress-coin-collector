"""Jinja-Umgebung und die Aufbereitung der Daten fuer die Vorlagen.

Zwischen data.py (reine Auswertung) und app.py (nur HTTP) liegt diese Schicht: sie formt
Attempt-Objekte und Zeitangaben in das, was eine Vorlage anzeigt. Sie kennt kein FastAPI,
laesst sich also ohne Webserver pruefen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from ..commands import Command, Status, mask_serial
from ..store import Attempt
from . import data

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"

# Bereiche der Seitenleiste. Etappe 1 bringt nur die Uebersicht; die uebrigen kommen in den
# Etappen 2 bis 4 und stehen erst dann hier -- eine Leiste mit toten Wegen waere schlechter
# als eine kurze.
NAV = [
    {
        "href": "/",
        "label": "Übersicht",
        "icon": "M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5",
    },
    {
        "href": "/geraet",
        "label": "Gerät",
        "icon": "M7.5 2.5h9a2 2 0 0 1 2 2v15a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2ZM10 18.5h4",
    },
]


def environment() -> Environment:
    """Autoescape ist der Grund fuer den Umbau: jede Zeichenkette aus der Datenbank wird
    maskiert, ohne dass jemand daran denken muss. StrictUndefined faengt Tippfehler in
    Vorlagen sofort, statt still eine leere Stelle zu rendern."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(default=True, default_for_string=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["url"] = static_url
    env.filters["zahl"] = german_number
    return env


# Wochentage und Monate ausgeschrieben. Nicht ueber die Locale, sondern fest: im LXC ist
# de_DE meist gar nicht erzeugt, und dann steht "Monday" auf einer deutschen Seite.
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
MONTHS = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)


def german_date(day: date) -> str:
    """ "Montag, 21. September" -- unabhaengig davon, welche Locale im Container existiert."""
    return f"{WEEKDAYS[day.weekday()]}, {day.day}. {MONTHS[day.month - 1]}"


def german_number(value: object) -> str:
    """Tausenderpunkt wie im Deutschen: 1267 wird zu 1.267. Nichtzahlen bleiben, wie sie sind."""
    if not isinstance(value, int) or isinstance(value, bool):
        return "–" if value is None else str(value)
    return f"{value:,}".replace(",", ".")


def static_url(name: str) -> str:
    """Adresse einer mitgelieferten Datei. Eigene Funktion, damit spaeter ein Stempel gegen
    veraltete Zwischenspeicher dazukann, ohne jede Vorlage anzufassen."""
    return f"/static/{name}"


# ---------------------------------------------------------------------------- Aufbereitung


@dataclass(frozen=True)
class Row:
    """Eine Zeile der Laufhistorie, fertig zur Anzeige."""

    when: str
    kind: str
    label: str
    tone: str
    coins: str
    duration: str
    message: str
    shot: str | None


@dataclass(frozen=True)
class Last:
    """Der juengste Lauf, wie ihn die Kachel zeigt."""

    label: str
    tone: str
    when: str
    kind: str


_TONE = {"ok": "ok", "warn": "warn", "bad": "bad"}


def _tone(outcome: str) -> str:
    return _TONE.get(data.outcome_kind(outcome), "")


def rows(attempts: list[Attempt], shots: dict[str, str]) -> list[Row]:
    """Historie, neueste zuerst. 'shots' bildet einen Zeitstempel auf einen Dateinamen ab."""
    out = []
    for a in sorted(attempts, key=lambda x: x.ts, reverse=True):
        if a.coins_before is not None and a.coins_after is not None and a.coins_after != a.coins_before:
            coins = f"{a.coins_before} → {a.coins_after}"
        elif a.coins_after is not None:
            coins = str(a.coins_after)
        else:
            coins = "–"
        out.append(
            Row(
                when=f"{a.ts:%d.%m. %H:%M}",
                kind=a.kind,
                label=data.outcome_label(a.outcome),
                tone=_tone(a.outcome),
                coins=coins,
                duration=f"{a.duration_s:.0f} s",
                message=a.message,
                shot=shots.get(a.ts.strftime("%Y%m%d-%H%M%S")),
            )
        )
    return out


def last_tile(attempts: list[Attempt]) -> Last | None:
    a = data.latest(attempts)
    if a is None:
        return None
    return Last(
        label=data.outcome_label(a.outcome),
        tone=_tone(a.outcome),
        when=f"{a.ts:%d.%m. %H:%M}",
        kind=a.kind,
    )


def relative(target: datetime, now: datetime) -> str:
    """ "in 2 h 15 min" statt einer nackten Uhrzeit -- die Frage ist meist "wie lange noch"."""
    minutes = max(0, int((target - now).total_seconds()) // 60)
    if minutes >= 60:
        return f"in {minutes // 60} h {minutes % 60} min"
    return f"in {minutes} min"


def ago(moment: datetime | None, now: datetime) -> str:
    """ "vor 12 s" -- fuer Angaben, die in der Vergangenheit liegen."""
    if moment is None:
        return "noch nie"
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 90:
        return f"vor {seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"vor {minutes} min"
    hours = minutes // 60
    if hours < 48:
        return f"vor {hours} h"
    return f"vor {hours // 24} Tagen"


def heartbeat_text(beat: datetime | None, now: datetime) -> str:
    """Wie frisch das Lebenszeichen ist. Ohne Datei: eine Aussage, kein leeres Feld."""
    if beat is None:
        return "kein Lebenszeichen"
    seconds = max(0, int((now - beat).total_seconds()))
    if seconds < 90:
        return f"Lebenszeichen vor {seconds} s"
    minutes = seconds // 60
    if minutes < 90:
        return f"Lebenszeichen vor {minutes} min"
    return f"Lebenszeichen vor {minutes // 60} h"


# ---------------------------------------------------------------------------- Geraeteseite


@dataclass(frozen=True)
class Device:
    """Was die Verbindungs-Karte anzeigt."""

    state: str
    label: str
    tone: str
    serial: str
    screen: str
    reported: str


@dataclass(frozen=True)
class QueueRow:
    label: str
    status: str
    tone: str
    message: str
    when: str


@dataclass(frozen=True)
class Service:
    target: str
    label: str
    unit: str
    alive: bool
    since: str
    verbs: tuple[tuple[str, str], ...]


def device_view(status: Status | None, serial: str, now: datetime) -> Device:
    """Zustand des Geraets, wie ihn der Dienst zuletzt gemeldet hat.

    Ohne Meldung wird nicht geraten: "unbekannt" ist ehrlicher als ein erfundenes "offline".
    """
    if status is None:
        return Device(
            state="unbekannt",
            label="unbekannt",
            tone="",
            serial=mask_serial(serial),
            screen="",
            reported="noch nie",
        )
    screen = ""
    if status.screen_on is not None:
        screen = "Bildschirm an" if status.screen_on else "Bildschirm aus"
    return Device(
        state=status.device_state,
        label=status.device_label,
        tone=status.device_tone,
        serial=mask_serial(serial),
        screen=screen,
        reported=ago(status.checked_at or status.written_at, now),
    )


def queue_rows(items: list[Command], now: datetime) -> list[QueueRow]:
    return [
        QueueRow(
            label=c.label,
            status=c.status,
            tone=c.tone,
            message=c.message,
            when=ago(c.finished_at or c.requested_at, now),
        )
        for c in items
    ]


def services(collector_alive: bool, heartbeat: datetime | None, now: datetime) -> list[Service]:
    """Die beiden Dienste fuer die Steuerungs-Karte.

    Ueber die Weboberflaeche selbst ist nur bekannt, dass sie laeuft -- sie beantwortet ja gerade
    diese Anfrage. Stoppen darf man sie hier nicht: danach koennte niemand sie wieder starten.
    """
    return [
        Service(
            target="collector",
            label="Sammel-Dienst",
            unit="aliexpress-coin-collector",
            alive=collector_alive,
            since=f"Lebenszeichen {ago(heartbeat, now)}" if heartbeat else "kein Lebenszeichen",
            verbs=(("restart", "Neustart"), ("stop", "Stoppen")) if collector_alive else (("start", "Starten"),),
        ),
        Service(
            target="web",
            label="Weboberfläche",
            unit="aliexpress-coin-collector-web",
            alive=True,
            since="beantwortet gerade diese Anfrage",
            verbs=(("restart", "Neustart"),),
        ),
    ]
