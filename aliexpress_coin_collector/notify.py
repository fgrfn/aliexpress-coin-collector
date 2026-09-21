"""Discord-Meldungen. Wirft nie -- eine ausgefallene Meldung darf den Check-in nie kosten.

Geschickt wird ein Embed statt einer Textzeile: Farbe nach Ergebnis, Zahlen in eigenen Feldern.
Das liest sich im Kanal deutlich schneller als "Morgenlauf: Coins eingesammelt (1572 -> 1582, +10)".

Umlaute sind hier ausdruecklich erwuenscht -- diese Texte liest ein Mensch, sie gehen weder an
die Shell noch an ADB.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

import requests

log = logging.getLogger(__name__)

# Farbstreifen am linken Rand des Embeds.
COLORS = {
    "ok": 0x3BA55D,  # gruen
    "info": 0x5865F2,  # blau
    "warn": 0xE8A33D,  # orange
    "bad": 0xE5484D,  # rot
}

SHOT_NAME = "screenshot.png"

# Grenzen von Discord. Wird eine ueberschritten, lehnt die API die ganze Nachricht ab --
# also lieber hier kuerzen als die Meldung ganz verlieren.
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FIELDS = 25
MAX_FOOTER = 2048


@dataclass(frozen=True)
class Field:
    name: str
    value: str
    inline: bool = True


@dataclass(frozen=True)
class Message:
    """Eine Meldung, unabhaengig davon, wie Discord sie darstellt."""

    title: str
    tone: str = "info"  # ok | info | warn | bad
    description: str = ""
    fields: tuple[Field, ...] = field(default_factory=tuple)
    footer: str = ""

    def as_text(self) -> str:
        """Dieselbe Meldung als eine Zeile -- fuer das Protokoll und fuer Tests."""
        parts = [self.title]
        if self.description:
            parts.append(self.description)
        parts += [f"{f.name}: {f.value}" for f in self.fields]
        return " | ".join(parts)


def number(value: int | None) -> str:
    """Tausenderpunkt wie im Deutschen. Eigene Kopie, damit der Dienst nichts aus web/ braucht."""
    if value is None:
        return "unbekannt"
    return f"{value:,}".replace(",", ".")


def _embed(message: Message, now: datetime | None = None, with_image: bool = False) -> dict:
    embed: dict = {
        "title": message.title[:MAX_TITLE],
        "color": COLORS.get(message.tone, COLORS["info"]),
        "timestamp": (now or datetime.now()).astimezone().isoformat(),
    }
    if message.description:
        embed["description"] = message.description[:MAX_DESCRIPTION]
    if message.fields:
        embed["fields"] = [
            {"name": f.name[:MAX_FIELD_NAME], "value": f.value[:MAX_FIELD_VALUE], "inline": f.inline}
            for f in message.fields[:MAX_FIELDS]
        ]
    if message.footer:
        embed["footer"] = {"text": message.footer[:MAX_FOOTER]}
    if with_image:
        # Der angehaengte Screenshot wird so im Embed selbst gezeigt statt darunter.
        embed["image"] = {"url": f"attachment://{SHOT_NAME}"}
    return embed


def send(webhook: str, message: Message, screenshot: bytes | None = None, now: datetime | None = None) -> bool:
    """Meldung schicken. Ohne Webhook landet sie nur im Protokoll. Wirft nie eine Ausnahme."""
    if not webhook:
        log.info("Discord nicht konfiguriert, Meldung nur im Log: %s", message.as_text())
        return False
    payload = {"embeds": [_embed(message, now, with_image=bool(screenshot))]}
    try:
        if screenshot:
            resp = requests.post(
                webhook,
                data={"payload_json": json.dumps(payload)},
                files={"file": (SHOT_NAME, screenshot, "image/png")},
                timeout=20,
            )
        else:
            resp = requests.post(webhook, json=payload, timeout=20)
        if resp.status_code >= 300:
            # Der Webhook selbst steht in keiner Meldung -- er ist ein Geheimnis.
            log.warning("Discord antwortete mit HTTP %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Discord-Meldung fehlgeschlagen: %s", exc)
        return False
