from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)


def send_discord(webhook: str, content: str, screenshot: bytes | None = None) -> bool:
    """Schickt eine Discord-Nachricht (optional mit Screenshot). Wirft nie eine Ausnahme."""
    if not webhook:
        log.info("Discord nicht konfiguriert, Meldung nur im Log: %s", content)
        return False
    try:
        payload = {"content": content[:1900]}
        if screenshot:
            resp = requests.post(
                webhook,
                data={"payload_json": json.dumps(payload)},
                files={"file": ("screenshot.png", screenshot, "image/png")},
                timeout=20,
            )
        else:
            resp = requests.post(webhook, json=payload, timeout=20)
        if resp.status_code >= 300:
            log.warning("Discord antwortete mit HTTP %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Discord-Meldung fehlgeschlagen: %s", exc)
        return False
