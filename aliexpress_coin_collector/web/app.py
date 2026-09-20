"""Weboberflaeche ueber der Laufhistorie.

Bewusst duenn: alle Auswertung steckt in data.py, alle Darstellung in render.py. Hier bleiben nur
HTTP, Anmeldung und der Dateizugriff.

Der Webdienst fasst weder ADB noch das Geraet an. Er oeffnet die Datenbank schreibgeschuetzt und
verstaendigt sich mit dem Sammel-Dienst ueber zwei kleine Dateien in data/:
  run-requested  legt er an, der Dienst holt sie beim naechsten Takt ab
  heartbeat      beruehrt der Dienst, daran erkennt die Seite, ob er laeuft
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import sqlite3
import time as time_mod
from datetime import datetime
from datetime import time as dtime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from .. import __version__, settings
from ..config import Config, ConfigError
from ..scheduler import HEARTBEAT_FILE, REQUEST_FILE, next_due, plan_for
from ..store import Attempt, Store
from . import data, render

log = logging.getLogger(__name__)

COOKIE = "acc_session"
SECRET_FILE = "web-secret"
SHOT_NAME_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z_]+\.png$")
HISTORY_LIMIT = 400
FAILED_LOGIN_DELAY_S = 1.0


def _secret(data_dir: Path) -> bytes:
    """Geheimnis fuer das Sitzungscookie, beim ersten Start erzeugt und danach wiederverwendet."""
    path = data_dir / SECRET_FILE
    if path.is_file():
        return path.read_bytes().strip()
    value = secrets.token_hex(32).encode()
    path.write_bytes(value + b"\n")
    path.chmod(0o600)
    return value


def _read_attempts(cfg: Config) -> list[Attempt]:
    """Historie lesen. Fehlt die Datenbank noch, ist die Historie eben leer."""
    try:
        return Store(cfg.data_dir, read_only=True).recent(HISTORY_LIMIT)
    except sqlite3.Error as exc:
        log.warning("Datenbank nicht lesbar: %s", exc)
        return []


def _heartbeat(cfg: Config) -> datetime | None:
    path = cfg.data_dir / HEARTBEAT_FILE
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except (OSError, ValueError):
        return None


def _shots(cfg: Config) -> dict[str, str]:
    """Screenshot-Dateien nach ihrem Zeitstempel-Praefix, damit die Tabelle sie zuordnen kann."""
    folder = cfg.data_dir / "shots"
    if not folder.is_dir():
        return {}
    return {p.name[:15]: p.name for p in folder.glob("*.png") if SHOT_NAME_RE.match(p.name)}


def create_app(cfg: Config, password: str) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    secret = _secret(cfg.data_dir)
    token = hmac.new(secret, b"session", "sha256").hexdigest()

    def authed(request: Request) -> bool:
        return hmac.compare_digest(request.cookies.get(COOKIE, ""), token)

    def current_cfg() -> Config:
        """Fenster bei jeder Anfrage frisch lesen, damit die Seite nach dem Speichern stimmt."""
        overrides = settings.load(cfg.data_dir)
        if overrides.empty:
            return cfg
        from dataclasses import replace

        return replace(cfg, **overrides.windows, settings_changed_at=overrides.changed_at)

    @app.get("/login", response_class=HTMLResponse)
    def login_form() -> HTMLResponse:
        return HTMLResponse(render.login_page(version=__version__))

    @app.post("/login")
    def login(password_field: str = Form(alias="password", default="")) -> Response:
        if not hmac.compare_digest(password_field, password):
            time_mod.sleep(FAILED_LOGIN_DELAY_S)  # bremst Durchprobieren, mehr ist hier nicht verhaeltnismaessig
            return HTMLResponse(render.login_page("Passwort stimmt nicht.", __version__), status_code=401)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=30 * 24 * 3600)
        return response

    @app.post("/logout")
    def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> Response:
        if not authed(request):
            return RedirectResponse("/login", status_code=303)

        active = current_cfg()
        now = datetime.now()
        attempts = _read_attempts(active)
        today = [a for a in attempts if a.ts.date() == now.date()]
        alive = data.service_alive(_heartbeat(active), now)
        pending = (active.data_dir / REQUEST_FILE).is_file()
        state = data.button_state(attempts, now.date(), alive, pending)
        offset = int(os.environ.get("STREAK_OFFSET") or 0)

        banner = ""
        if not alive:
            banner = (
                '<div class="card banner"><strong>Der Dienst läuft nicht.</strong> '
                "Es gibt kein Lebenszeichen der letzten Minuten — geplante Läufe finden gerade nicht statt.</div>"
            )

        button = f'<button type="submit" {"" if state.enabled else "disabled"}>Lauf jetzt starten</button>' + (
            f'<p class="note">{state.reason}</p>' if state.reason else ""
        )
        plan = plan_for(now.date(), active)

        body = f"""<h1>{render.LOGO}AliExpress Coin Collector</h1>
        {banner}
        <div class="card">
          {
            render.status_tiles(
                data.latest(attempts),
                next_due(now, active, today),
                now,
                data.latest_coins(attempts),
                data.derive_streak(attempts, now.date(), offset),
                alive,
            )
        }
        </div>

        <div class="card">
          <h2>Münzverlauf</h2>
          {render.coin_chart(data.coin_series(attempts))}
        </div>

        <div class="card">
          <h2>Lauf jetzt starten</h2>
          <form method="post" action="/run" onsubmit="return confirm(
            'Wirklich einen Lauf anfordern? Das weckt das Gerät und tippt darauf.');">
            {button}
          </form>
          <p class="note">Der Auftrag wird beim nächsten Takt des Dienstes abgeholt, also binnen 30 Sekunden.</p>
        </div>

        <div class="card">
          <h2>Zeitfenster</h2>
          <form method="post" action="/settings" class="row">
            <label>Morgens ab<br><input type="time" name="morning_start"
              value="{active.morning_start:%H:%M}" required></label>
            <label>bis<br><input type="time" name="morning_end" value="{active.morning_end:%H:%M}" required></label>
            <label>Abends ab<br><input type="time" name="evening_start"
              value="{active.evening_start:%H:%M}" required></label>
            <label>bis<br><input type="time" name="evening_end" value="{active.evening_end:%H:%M}" required></label>
            <button type="submit" class="secondary">Speichern</button>
          </form>
          <p class="note">
            Heute geplant: Morgenlauf {plan.morning_at:%H:%M}, Abendlauf {plan.evening_at:%H:%M}.
            Eine Änderung gilt sofort, aber nicht rückwirkend: liegt die neu gewürfelte Zeit schon in der
            Vergangenheit, läuft heute nichts mehr. Der Abendlauf startet ohnehin nur, wenn morgens
            nichts geklappt hat.
          </p>
        </div>

        <div class="card">
          <h2>Letzte Läufe</h2>
          {render.history_table(attempts[:60], _shots(active))}
        </div>

        <form method="post" action="/logout"><button class="secondary" type="submit">Abmelden</button></form>"""
        return HTMLResponse(render.page("Coin Collector", body, __version__))

    @app.post("/run")
    def request_run(request: Request) -> Response:
        if not authed(request):
            return RedirectResponse("/login", status_code=303)
        active = current_cfg()
        now = datetime.now()
        attempts = _read_attempts(active)
        alive = data.service_alive(_heartbeat(active), now)
        pending = (active.data_dir / REQUEST_FILE).is_file()
        # Serverseitig erneut pruefen: ein deaktivierter Knopf im Browser ist keine Absicherung.
        if not data.button_state(attempts, now.date(), alive, pending).enabled:
            return RedirectResponse("/", status_code=303)
        (active.data_dir / REQUEST_FILE).write_text(f"{now:%Y-%m-%dT%H:%M:%S}\n", encoding="utf-8")
        log.info("Lauf ueber die Weboberflaeche angefordert")
        return RedirectResponse("/", status_code=303)

    @app.post("/settings")
    def save_settings(
        request: Request,
        morning_start: str = Form(...),
        morning_end: str = Form(...),
        evening_start: str = Form(...),
        evening_end: str = Form(...),
    ) -> Response:
        if not authed(request):
            return RedirectResponse("/login", status_code=303)
        try:
            windows = {
                "morning_start": _parse_time(morning_start),
                "morning_end": _parse_time(morning_end),
                "evening_start": _parse_time(evening_start),
                "evening_end": _parse_time(evening_end),
            }
            _check_windows(windows)
        except ValueError as exc:
            return HTMLResponse(
                render.page(
                    "Ungültige Eingabe", f'<div class="card banner">{exc}</div><a href="/">Zurück</a>', __version__
                ),
                status_code=400,
            )
        settings.save(cfg.data_dir, windows)
        log.info("Zeitfenster ueber die Weboberflaeche geaendert")
        return RedirectResponse("/", status_code=303)

    @app.get("/shot/{name}")
    def screenshot(request: Request, name: str) -> Response:
        if not authed(request):
            return RedirectResponse("/login", status_code=303)
        # Strenge Namenspruefung statt Pfadbereinigung: alles, was nicht exakt passt, wird abgelehnt.
        if not SHOT_NAME_RE.match(name):
            return Response("Unbekannt", status_code=404)
        path = cfg.data_dir / "shots" / name
        if not path.is_file():
            return Response("Unbekannt", status_code=404)
        return FileResponse(path, media_type="image/png")

    return app


def _parse_time(value: str) -> dtime:
    try:
        hh, mm = value.strip().split(":")[:2]
        return dtime(int(hh), int(mm))
    except ValueError as exc:
        raise ValueError(f"Ungültige Uhrzeit: {value!r}, erwartet HH:MM") from exc


def _check_windows(windows: dict[str, dtime]) -> None:
    """Dieselben Regeln wie in Config._validate, damit Oberflaeche und Dienst nicht auseinanderlaufen."""
    if windows["morning_end"] < windows["morning_start"]:
        raise ValueError("Das Morgenfenster endet vor seinem Beginn.")
    if windows["evening_end"] < windows["evening_start"]:
        raise ValueError("Das Abendfenster endet vor seinem Beginn.")
    if windows["morning_end"] > windows["evening_start"]:
        raise ValueError("Das Morgenfenster muss vor dem Abendfenster liegen.")


def build() -> FastAPI:
    """Einstiegspunkt fuer uvicorn: Konfiguration laden und Passwort erzwingen."""
    cfg = Config.load(os.environ.get("ACC_ENV_FILE") or ".env")
    password = (os.environ.get("WEB_PASSWORD") or "").strip()
    if not password:
        raise ConfigError(
            "WEB_PASSWORD fehlt. Die Weboberflaeche kann einen Lauf auf dem Geraet ausloesen und "
            "startet deshalb nicht ohne Passwort. Bitte in der .env setzen."
        )
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO), format="%(asctime)s %(levelname)-7s %(message)s"
    )
    return create_app(cfg, password)
