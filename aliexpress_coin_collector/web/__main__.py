"""Startet die Weboberflaeche: python -m aliexpress_coin_collector.web

Bewusst ein eigener Einstiegspunkt und nicht Teil der Haupt-CLI, damit FastAPI und uvicorn nicht
bei jedem Aufruf von 'once' oder 'status' importiert werden muessen.
"""

from __future__ import annotations

import os
import sys

from ..config import ConfigError


def main() -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn fehlt. Bitte 'pip install -r requirements.txt' ausfuehren.", file=sys.stderr)
        return 2

    from .app import build

    try:
        app = build()
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2

    port = int(os.environ.get("WEB_PORT") or 80)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")  # noqa: S104 - LAN-Dienst im Container
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
