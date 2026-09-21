"""Weboberflaeche ueber der Laufhistorie.

Bewusst duenn: Auswertung in data.py, Aufbereitung in view.py, Darstellung in den Vorlagen
unter templates/. Hier bleiben HTTP, Anmeldung und Dateizugriff.

Der Webdienst fasst weder ADB noch das Geraet an. Er oeffnet die Datenbank schreibgeschuetzt
und verstaendigt sich mit dem Sammel-Dienst ausschliesslich ueber Dateien in data/:
  run-requested  Auftragsdatei vor Version 0.5.0, wird noch angenommen
  heartbeat      beruehrt der Dienst, daran erkennt die Seite, ob er laeuft
  web-password   Hash des Passworts, bei der Ersteinrichtung ueber die Seite vergeben
  commands/      Auftraege an den Dienst (siehe commands.py)
  status.json    was der Dienst ueber sich und das Geraet meldet
  control        eine Zeile fuer den Root-Helfer, der die Dienste startet und stoppt
  collector.log  Protokoll des Dienstes, rotierend (siehe logs.py)
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import sqlite3
import time as time_mod
from dataclasses import replace
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import __version__, commands, logs, notify, scheduler, settings
from ..config import Config, ConfigError
from ..scheduler import HEARTBEAT_FILE, REQUEST_FILE, next_due, plan_for, with_current_settings
from ..store import Attempt, Store
from . import auth, charts, data, imagecheck, view

log = logging.getLogger(__name__)

COOKIE = "acc_session"
THEME_COOKIE = "acc_theme"
FLASH_COOKIE = "acc_flash"
CONTROL_FILE = "control"
CONTROL_VERBS = ("start", "stop", "restart")
CONTROL_TARGETS = ("collector", "web")
SECRET_FILE = "web-secret"
SHOT_NAME_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z_]+\.png$")
MANUAL_SHOT_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-manual\.png$")
HISTORY_LIMIT = 400
TABLE_LIMIT = 60
LOG_LIMIT = 300
HISTORY_TABLE_LIMIT = 200
FAILED_LOGIN_DELAY_S = 1.0
YEAR_S = 365 * 24 * 3600


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


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=view.STATIC), name="static")
    env = view.environment()
    secret = _secret(cfg.data_dir)

    def token() -> str:
        """Sitzungsmerkmal aus Geheimnis und Passwort-Hash.

        Dass der Hash mit eingeht, ist Absicht: ein geaendertes Passwort macht damit alle
        bestehenden Cookies ungueltig, ohne dass Sitzungen irgendwo verwaltet werden muessten.
        """
        return hmac.new(secret, auth.stored_hash(cfg.data_dir).encode(), "sha256").hexdigest()

    def authed(request: Request) -> bool:
        if not auth.is_set(cfg.data_dir):
            return False
        return hmac.compare_digest(request.cookies.get(COOKIE, ""), token())

    def theme_of(request: Request) -> str:
        """Leer heisst: der Systemeinstellung folgen. Nur 'light' und 'dark' ueberstimmen sie."""
        value = request.cookies.get(THEME_COOKIE, "")
        return value if value in ("light", "dark") else ""

    def _logged_in(target: str = "/") -> Response:
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(COOKIE, token(), httponly=True, samesite="lax", max_age=30 * 24 * 3600)
        return response

    def _gate(request: Request) -> Response | None:
        """Gemeinsame Vorpruefung: erst einrichten, dann anmelden, dann erst die Seite."""
        if not auth.is_set(cfg.data_dir):
            return RedirectResponse("/setup", status_code=303)
        if not authed(request):
            return RedirectResponse("/login", status_code=303)
        return None

    def _run_pending(active: Config) -> bool:
        """Steht schon ein Lauf an? Die alte Auftragsdatei zaehlt mit, damit ein Update ohne
        Neustart der Oberflaeche den Knopf nicht doppelt freigibt."""
        if (active.data_dir / REQUEST_FILE).is_file():
            return True
        return any(c.name == commands.RUN for c in commands.pending(active.data_dir))

    def current_cfg() -> Config:
        """Einstellungen bei jeder Anfrage frisch lesen, damit die Seite nach dem Speichern stimmt.

        Dieselbe Funktion wie im Dienst -- sonst zeigte die Oberflaeche Werte an, nach denen gar
        nicht gearbeitet wird.
        """
        return with_current_settings(cfg)

    def page(name: str, request: Request, **values: object) -> str:
        base = {"version": __version__, "theme": theme_of(request), "nav": view.NAV, "current": "/"}
        return env.get_template(name).render({**base, **values})

    def service_values() -> dict[str, object]:
        """Was das Geruest auf jeder Seite braucht: der Punkt in der Leiste."""
        active = current_cfg()
        now = datetime.now()
        beat = _heartbeat(active)
        return {"alive": data.service_alive(beat, now), "heartbeat_text": view.heartbeat_text(beat, now)}

    def status_values(request: Request) -> dict[str, object]:
        """Alles, was der Statuskopf und der Punkt in der Leiste brauchen."""
        active = current_cfg()
        now = datetime.now()
        attempts = _read_attempts(active)
        today = [a for a in attempts if a.ts.date() == now.date()]
        beat = _heartbeat(active)
        alive = data.service_alive(beat, now)
        next_at = next_due(now, active, today)
        offset = int(os.environ.get("STREAK_OFFSET") or 0)
        return {
            "alive": alive,
            "heartbeat_text": view.heartbeat_text(beat, now),
            "last": view.last_tile(attempts),
            "next_at": next_at,
            "next_in": view.relative(next_at, now) if next_at else "",
            "coins": data.latest_coins(attempts),
            "streak": data.derive_streak(attempts, now.date(), offset),
        }

    # ------------------------------------------------------------------ Anmeldung

    @app.get("/setup", response_class=HTMLResponse)
    def setup_form(request: Request) -> Response:
        if auth.is_set(cfg.data_dir):
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(page("setup.html", request, title="Ersteinrichtung", min_length=auth.MIN_LENGTH, error=""))

    @app.post("/setup")
    def setup(request: Request, password: str = Form(default=""), repeat: str = Form(default="")) -> Response:
        if auth.is_set(cfg.data_dir):
            return RedirectResponse("/login", status_code=303)
        try:
            auth.set_password(cfg.data_dir, password, repeat, only_if_unset=True)
        except auth.PasswordError as exc:
            body = page("setup.html", request, title="Ersteinrichtung", min_length=auth.MIN_LENGTH, error=str(exc))
            return HTMLResponse(body, status_code=400)
        log.info("Passwort der Weboberflaeche bei der Ersteinrichtung vergeben")
        return _logged_in()

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        if not auth.is_set(cfg.data_dir):
            return RedirectResponse("/setup", status_code=303)
        return HTMLResponse(page("login.html", request, title="Anmelden", error=""))

    @app.post("/login")
    def login(request: Request, password_field: str = Form(alias="password", default="")) -> Response:
        if not auth.is_set(cfg.data_dir):
            return RedirectResponse("/setup", status_code=303)
        if not auth.verify(cfg.data_dir, password_field):
            time_mod.sleep(FAILED_LOGIN_DELAY_S)  # bremst Durchprobieren, mehr ist hier nicht verhaeltnismaessig
            body = page("login.html", request, title="Anmelden", error="Passwort stimmt nicht.")
            return HTMLResponse(body, status_code=401)
        return _logged_in()

    @app.post("/logout")
    def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.post("/password")
    def change_password(
        request: Request,
        current: str = Form(default=""),
        password: str = Form(default=""),
        repeat: str = Form(default=""),
    ) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        if not auth.verify(cfg.data_dir, current):
            time_mod.sleep(FAILED_LOGIN_DELAY_S)
            return HTMLResponse(settings_html(request, "Das bisherige Passwort stimmt nicht."), status_code=400)
        try:
            auth.set_password(cfg.data_dir, password, repeat)
        except auth.PasswordError as exc:
            return HTMLResponse(settings_html(request, str(exc)), status_code=400)
        log.info("Passwort der Weboberflaeche geaendert")
        # Das neue Passwort aendert den Token, das alte Cookie ist damit wertlos.
        return RedirectResponse("/login", status_code=303)

    # ------------------------------------------------------------------ Aussehen

    @app.post("/theme")
    def set_theme(request: Request, to: str = Form(default="")) -> Response:
        """Hell oder dunkel ueberstimmen. Ohne gueltigen Wert zurueck zur Systemeinstellung."""
        referer = request.headers.get("referer", "")
        target = referer if referer.startswith("/") else "/"
        response = RedirectResponse(target, status_code=303)
        if to in ("light", "dark"):
            response.set_cookie(THEME_COOKIE, to, samesite="lax", max_age=YEAR_S)
        else:
            response.delete_cookie(THEME_COOKIE)
        return response

    # ------------------------------------------------------------------ Uebersicht

    def dashboard_html(request: Request) -> str:
        active = current_cfg()
        now = datetime.now()
        attempts = _read_attempts(active)
        status = status_values(request)
        series = data.coin_series(attempts)
        return page(
            "dashboard.html",
            request,
            title="Übersicht",
            subtitle=f"{view.german_date(now.date())} · Zeitplan des Dienstes für heute",
            cfg=active,
            plan=plan_for(now.date(), active),
            button=data.button_state(attempts, now.date(), status["alive"], _run_pending(active)),
            series=series,
            chart=charts.coin_chart(series),
            attempts=view.rows(attempts[:TABLE_LIMIT], _shots(active)),
            **status,
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return HTMLResponse(dashboard_html(request))

    # ------------------------------------------------------------------ Teile fuer htmx

    @app.get("/teile/status", response_class=HTMLResponse)
    def part_status(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return HTMLResponse(page("partials/tiles.html", request, **status_values(request)))

    @app.get("/teile/dienst", response_class=HTMLResponse)
    def part_service(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        status = status_values(request)
        return HTMLResponse(
            page("partials/service.html", request, alive=status["alive"], heartbeat_text=status["heartbeat_text"])
        )

    # ------------------------------------------------------------------ Verlauf

    @app.get("/verlauf", response_class=HTMLResponse)
    def history(request: Request, tage: str = "", ergebnis: str = "") -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        active = current_cfg()
        now = datetime.now()
        span = data.parse_range(tage)
        attempts = data.in_range(_read_attempts(active), now.date(), span)
        # Der Filter gilt nur fuer die Tabelle. Kennzahlen und Diagramme bleiben am ganzen
        # Zeitraum -- sonst zeigte die Erfolgsquote nach einem Klick auf "nicht erkannt" null
        # Prozent, was zwar stimmt, aber nichts mehr aussagt.
        picked = ergebnis if any(s.outcome == ergebnis for s in data.outcome_shares(attempts)) else ""
        listed = [x for x in attempts if x.outcome == picked] if picked else attempts
        series = data.coin_series(attempts)
        spread = data.gain_span(attempts)
        average = data.average_gain(attempts)
        body = page(
            "verlauf.html",
            request,
            title="Verlauf & Auswertung",
            subtitle=f"{len(attempts)} Läufe · aus der Laufdatenbank",
            current="/verlauf",
            ranges=data.RANGES,
            span=span,
            attempts=attempts,
            rows=view.rows(listed[:HISTORY_TABLE_LIMIT], _shots(active)),
            more=max(0, len(listed) - HISTORY_TABLE_LIMIT),
            picked=picked,
            picked_label=data.outcome_label(picked) if picked else "",
            quota=data.success_quota(attempts),
            total_gain=data.total_gain(attempts),
            average_gain=f"{average:.1f}".replace(".", ",") if average else "–",
            span_text=f"{spread[0]} bis {spread[1]}" if spread else "",
            streak=data.longest_streak(attempts),
            shares=data.outcome_shares(attempts),
            busiest=data.busiest_hour(attempts),
            rollover=data.rollover_hints(attempts),
            series=series,
            coin_chart=charts.coin_chart(series),
            gain_chart=charts.gain_chart(series),
            weekday_chart=charts.weekday_chart(data.by_weekday(attempts)),
            **service_values(),
        )
        return HTMLResponse(body)

    # ------------------------------------------------------------------ Geraet

    def _flash(response: Response, text: str, tone: str = "ok") -> Response:
        """Eine Meldung fuer genau eine folgende Seite. Als Cookie, damit nach dem Absenden
        umgeleitet werden kann (sonst legt ein Neuladen den Auftrag ein zweites Mal ab).

        Prozentkodiert, weil ein Cookie als HTTP-Kopfzeile nur latin-1 kann: ein deutsches
        Anfuehrungszeichen wuerde sonst beim Senden eine Ausnahme werfen.
        """
        value = f"{tone}|{quote(text[:200], safe='')}"
        response.set_cookie(FLASH_COOKIE, value, max_age=30, samesite="lax", httponly=True)
        return response

    def _take_flash(request: Request) -> dict[str, str] | None:
        raw = request.cookies.get(FLASH_COOKIE, "")
        tone, _, encoded = raw.partition("|")
        if not encoded or tone not in ("ok", "warn", "bad"):
            return None
        return {"tone": tone, "text": unquote(encoded)}

    def _last_manual_shot(cfg_: Config, now: datetime) -> dict[str, str] | None:
        """Der juengste selbst angeforderte Screenshot. Fehlerbilder gehoeren in die Historie,
        nicht hierher."""
        folder = cfg_.data_dir / "shots"
        if not folder.is_dir():
            return None
        names = sorted(p.name for p in folder.glob("*.png") if MANUAL_SHOT_RE.match(p.name))
        if not names:
            return None
        name = names[-1]
        try:
            when = datetime.strptime(name[:15], "%Y%m%d-%H%M%S")
        except ValueError:
            return None
        return {"name": name, "when": f"{when:%d.%m. %H:%M}", "ago": view.ago(when, now)}

    def device_values(request: Request) -> dict[str, object]:
        active = current_cfg()
        now = datetime.now()
        status = commands.read_status(active.data_dir)
        beat = _heartbeat(active)
        alive = data.service_alive(beat, now)
        queue = commands.recent(active.data_dir)
        return {
            "device": view.device_view(status, active.adb_serial, now),
            "queue": view.queue_rows(queue, now),
            "waiting": sum(1 for c in queue if c.status == commands.WAITING),
            "services": view.services(alive, beat, now),
            "shot": _last_manual_shot(active, now),
            "alive": alive,
            "heartbeat_text": view.heartbeat_text(beat, now),
            # Nach einem Update laeuft der Dienst weiter, bis jemand ihn neu startet. Das ist
            # sonst nicht zu sehen und erklaert, warum Auftraege liegen bleiben.
            "stale_daemon": status.version if status and status.version and status.version != __version__ else "",
        }

    @app.get("/geraet", response_class=HTMLResponse)
    def device_page(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        body = page(
            "geraet.html",
            request,
            title="Gerät & Dienst",
            subtitle="Zustand, Verbindung und Steuerung",
            current="/geraet",
            flash=_take_flash(request),
            **device_values(request),
        )
        response = HTMLResponse(body)
        response.delete_cookie(FLASH_COOKIE)
        return response

    @app.post("/geraet/befehl")
    def device_command(request: Request, name: str = Form(default="")) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        active = current_cfg()
        response = RedirectResponse("/geraet", status_code=303)
        try:
            cmd = commands.submit(active.data_dir, name)
        except commands.CommandError as exc:
            return _flash(response, str(exc), "warn")
        log.info("Auftrag ueber die Weboberflaeche abgelegt: %s", cmd.name)
        return _flash(response, f"„{cmd.label}“ abgelegt. Der Dienst holt ihn binnen 30 Sekunden.", "ok")

    @app.post("/geraet/dienst")
    def device_service(request: Request, verb: str = Form(default=""), target: str = Form(default="")) -> Response:
        """Dienst starten, stoppen oder neu starten.

        Geschrieben wird nur eine Zeile aus zwei geprueften Woertern. Ein Helfer, den systemd
        auf die Aenderung hin als root startet, setzt daraus selbst den systemctl-Aufruf
        zusammen -- die Oberflaeche reicht nie einen Befehl durch.
        """
        gate = _gate(request)
        if gate is not None:
            return gate
        response = RedirectResponse("/geraet", status_code=303)
        if verb not in CONTROL_VERBS or target not in CONTROL_TARGETS:
            return _flash(response, "Unbekannte Aktion.", "bad")
        if target == "web" and verb == "stop":
            return _flash(response, "Die Oberfläche darf sich nicht selbst stoppen.", "warn")

        active = current_cfg()
        try:
            # Der Zeitstempel sorgt dafuer, dass sich die Datei wirklich aendert -- sonst
            # bemerkt die Pfadeinheit zwei gleiche Anforderungen hintereinander nicht.
            (active.data_dir / CONTROL_FILE).write_text(
                f"{verb} {target}\n# {datetime.now():%Y-%m-%dT%H:%M:%S}\n", encoding="utf-8"
            )
        except OSError as exc:
            log.warning("Steuerdatei nicht schreibbar: %s", exc)
            return _flash(response, "Die Steuerdatei ließ sich nicht schreiben.", "bad")

        log.info("Dienststeuerung angefordert: %s %s", verb, target)
        wording = {"start": "Starten", "stop": "Stoppen", "restart": "Neustart"}[verb]
        if target == "web":
            return _flash(response, f"{wording} der Oberfläche angefordert. Diese Seite bricht gleich ab.", "warn")
        return _flash(response, f"{wording} des Sammel-Dienstes angefordert.", "ok")

    @app.get("/teile/geraet", response_class=HTMLResponse)
    def part_device(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return HTMLResponse(page("partials/device.html", request, **device_values(request)))

    @app.get("/teile/auftraege", response_class=HTMLResponse)
    def part_queue(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return HTMLResponse(page("partials/queue.html", request, **device_values(request)))

    # ------------------------------------------------------------------ Diagnose

    def log_values(level: str, query: str, follow: bool) -> dict[str, object]:
        active = current_cfg()
        clean = level.upper() if level.upper() in logs.LEVELS else ""
        return {
            "lines": logs.read(active.data_dir, clean, query, LOG_LIMIT),
            "have_file": logs.exists(active.data_dir),
            "level": clean,
            "query": query,
            "follow": follow,
        }

    def diagnose_page(
        request: Request,
        level: str,
        query: str,
        follow: bool,
        result: imagecheck.Result | None = None,
        error: str = "",
        threshold_raw: str = "",
        invert: bool = True,
    ) -> str:
        return page(
            "diagnose.html",
            request,
            title="Diagnose",
            subtitle="Protokoll mitlesen und die Erkennung an echten Bildern prüfen",
            current="/diagnose",
            result=result,
            error=error,
            threshold_raw=threshold_raw,
            invert=invert,
            **log_values(level, query, follow),
            **service_values(),
        )

    @app.get("/diagnose", response_class=HTMLResponse)
    def diagnose(request: Request, stufe: str = "", suche: str = "", mit: str = "") -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return HTMLResponse(diagnose_page(request, stufe, suche[:100], mit == "1"))

    @app.get("/teile/protokoll", response_class=HTMLResponse)
    def part_log(request: Request, stufe: str = "", suche: str = "", mit: str = "") -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return HTMLResponse(page("partials/log.html", request, **log_values(stufe, suche[:100], mit == "1")))

    @app.post("/diagnose/erkennung", response_class=HTMLResponse)
    async def inspect_image(
        request: Request,
        bild: UploadFile = File(default=None),
        schwelle: str = Form(default=""),
        invertiert: str = Form(default=""),
    ) -> Response:
        """Einen hochgeladenen Screenshot durch die Erkennung schicken.

        Das Bild wird nie auf die Platte geschrieben -- es enthaelt Kontodaten. Die Erkennung
        laeuft in einem Arbeits-Thread, damit die Seite waehrenddessen bedienbar bleibt.
        """
        gate = _gate(request)
        if gate is not None:
            return gate
        invert = invertiert == "1"
        try:
            raw = await bild.read() if bild is not None else b""
            imagecheck.check_upload(bild.content_type if bild is not None else "", len(raw))
            result = await run_in_threadpool(imagecheck.analyze, raw, current_cfg(), schwelle, invert)
        except imagecheck.InspectError as exc:
            body = diagnose_page(request, "", "", False, error=str(exc), threshold_raw=schwelle, invert=invert)
            return HTMLResponse(body, status_code=400)
        finally:
            if bild is not None:
                await bild.close()
        log.info("Erkennung an einem hochgeladenen Bild geprueft: %s", "gefunden" if result.found else "nicht gefunden")
        return HTMLResponse(diagnose_page(request, "", "", False, result=result, threshold_raw=schwelle, invert=invert))

    # ------------------------------------------------------------------ Aktionen

    @app.post("/run")
    def request_run(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        active = current_cfg()
        now = datetime.now()
        attempts = _read_attempts(active)
        alive = data.service_alive(_heartbeat(active), now)
        # Serverseitig erneut pruefen: ein deaktivierter Knopf im Browser ist keine Absicherung.
        if not data.button_state(attempts, now.date(), alive, _run_pending(active)).enabled:
            return RedirectResponse("/", status_code=303)
        try:
            commands.submit(active.data_dir, commands.RUN, now)
        except commands.CommandError as exc:
            log.info("Lauf nicht angenommen: %s", exc)
            return RedirectResponse("/", status_code=303)
        log.info("Lauf ueber die Weboberflaeche angefordert")
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------------ Einstellungen

    def settings_html(request: Request, pw_error: str = "") -> str:
        active = current_cfg()
        now = datetime.now()
        return page(
            "einstellungen.html",
            request,
            title="Einstellungen",
            subtitle="Was der Dienst tut, und womit er es tut",
            current="/einstellungen",
            cfg=active,
            plan=plan_for(now.date(), active),
            # Der Webhook selbst wird nie an den Browser gegeben, nur ob einer hinterlegt ist.
            has_webhook=bool(active.discord_webhook),
            stored=set(settings.load(cfg.data_dir).values),
            min_length=auth.MIN_LENGTH,
            pw_error=pw_error,
            flash=_take_flash(request),
            **service_values(),
        )

    @app.get("/einstellungen", response_class=HTMLResponse)
    def settings_page(request: Request) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        response = HTMLResponse(settings_html(request))
        response.delete_cookie(FLASH_COOKIE)
        return response

    def _store(request: Request, values: dict[str, object], what: str) -> Response:
        """Einstellungen pruefen und ablegen.

        Geprueft wird, indem die Werte probeweise in die laufende Konfiguration eingesetzt und
        mit deren eigenen Regeln geprueft werden -- so koennen Oberflaeche und Dienst nicht
        auseinanderlaufen. Erst danach wird geschrieben.
        """
        response = RedirectResponse("/einstellungen", status_code=303)
        try:
            replace(current_cfg(), **values).validate()
        except ConfigError as exc:
            return _flash(response, in_words(str(exc)), "bad")
        try:
            settings.save(cfg.data_dir, values)
        except (OSError, ValueError) as exc:
            log.warning("Einstellungen nicht speicherbar: %s", exc)
            return _flash(response, "Die Einstellungen ließen sich nicht speichern.", "bad")
        # Bewusst ohne Werte: hier stehen Geraeteadresse und Webhook drin.
        log.info("%s ueber die Weboberflaeche geaendert", what)
        return _flash(response, f"{what} gespeichert. Der Dienst übernimmt das binnen 30 Sekunden.", "ok")

    @app.post("/einstellungen/ablauf")
    def save_schedule(
        request: Request,
        morning_start: str = Form(default=""),
        morning_end: str = Form(default=""),
        evening_start: str = Form(default=""),
        evening_end: str = Form(default=""),
        skip_if_awake: str = Form(default=""),
        busy_retry_min: str = Form(default=""),
        busy_max_wait_min: str = Form(default=""),
        page_timeout_s: str = Form(default=""),
        confirm_timeout_s: str = Form(default=""),
        launch_retries: str = Form(default=""),
    ) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        try:
            values: dict[str, object] = {
                "morning_start": _parse_time(morning_start),
                "morning_end": _parse_time(morning_end),
                "evening_start": _parse_time(evening_start),
                "evening_end": _parse_time(evening_end),
                "skip_if_awake": bool(skip_if_awake),
                "busy_retry_min": _parse_int("Wiederholung", busy_retry_min),
                "busy_max_wait_min": _parse_int("Spätestens erzwingen", busy_max_wait_min),
                "page_timeout_s": _parse_int("Wartezeit auf die Seite", page_timeout_s),
                "confirm_timeout_s": _parse_int("Wartezeit auf die Bestätigung", confirm_timeout_s),
                "launch_retries": _parse_int("Erneut öffnen", launch_retries),
            }
        except ValueError as exc:
            return _flash(RedirectResponse("/einstellungen", status_code=303), str(exc), "bad")
        return _store(request, values, "Ablauf & Verhalten")

    @app.post("/einstellungen/geraet")
    def save_device(request: Request, adb_serial: str = Form(default="")) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        return _store(request, {"adb_serial": adb_serial.strip()}, "Geräteadresse")

    @app.post("/einstellungen/test")
    async def send_test(request: Request) -> Response:
        """Testmeldung an Discord.

        Die schickt die Oberflaeche ausnahmsweise selbst statt ueber die Auftragsablage: es ist
        nur eine HTTPS-Anfrage, kein Zugriff aufs Geraet -- und wer einen Test ausloest, will die
        Antwort sofort sehen und nicht dreissig Sekunden auf den naechsten Takt des Dienstes
        warten. Blockierend ist sie trotzdem, deshalb im Threadpool.
        """
        gate = _gate(request)
        if gate is not None:
            return gate
        active = current_cfg()
        response = RedirectResponse("/einstellungen#meldungen", status_code=303)
        if not active.discord_webhook:
            return _flash(response, "Es ist kein Discord-Webhook hinterlegt.", "warn")

        sent = await run_in_threadpool(notify.send, active.discord_webhook, scheduler.test_message(active))
        if sent:
            log.info("Testmeldung an Discord geschickt")
            return _flash(response, "Testmeldung geschickt. Sie sollte jetzt im Kanal stehen.", "ok")
        return _flash(response, f"Nicht zugestellt: {sent.detail}", "bad")

    @app.post("/einstellungen/meldungen")
    def save_notifications(
        request: Request,
        discord_webhook: str = Form(default=""),
        remove_webhook: str = Form(default=""),
        notify_on_success: str = Form(default=""),
        notify_on_already_done: str = Form(default=""),
        notify_on_offline: str = Form(default=""),
        offline_alert_min: str = Form(default=""),
        notify_weekly: str = Form(default=""),
    ) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
        values: dict[str, object] = {
            "notify_on_success": bool(notify_on_success),
            "notify_on_already_done": bool(notify_on_already_done),
            "notify_on_offline": bool(notify_on_offline),
            "notify_weekly": bool(notify_weekly),
        }
        # Ein leeres Zahlenfeld heisst "unveraendert", nicht "Fehler". Sonst verwuerfe ein
        # Formular, das das Feld gar nicht mitschickt, gleich den ganzen Abschnitt -- samt
        # des Webhooks, den daneben jemand gerade eingetragen hat.
        if offline_alert_min.strip():
            try:
                values["offline_alert_min"] = _parse_int("Melden nach", offline_alert_min)
            except ValueError as exc:
                return _flash(RedirectResponse("/einstellungen", status_code=303), str(exc), "bad")
        # Das Feld kommt immer leer an -- der hinterlegte Webhook wird nie in die Seite
        # geschrieben. Leer heisst darum "unveraendert", geleert wird nur auf Ansage.
        if remove_webhook:
            values["discord_webhook"] = ""
        elif discord_webhook.strip():
            values["discord_webhook"] = discord_webhook.strip()
        return _store(request, values, "Benachrichtigungen")

    @app.get("/shot/{name}")
    def screenshot(request: Request, name: str) -> Response:
        gate = _gate(request)
        if gate is not None:
            return gate
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


def _parse_int(label: str, value: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"„{label}“ erwartet eine ganze Zahl, nicht {value!r}.") from exc


# Geprueft wird mit den Regeln der Konfiguration, und die kennt nur .env-Namen. Auf der
# Einstellungsseite steht aber nirgends BUSY_RETRY_MIN -- also werden sie fuer die Meldung
# durch die Beschriftung ersetzt, unter der man den Wert tatsaechlich sieht.
FIELD_NAMES = {
    "BUSY_RETRY_MIN": "„Dann erneut versuchen nach“",
    "BUSY_MAX_WAIT_MIN": "„Spätestens erzwingen nach“",
    "PAGE_TIMEOUT_S": "„Warten auf die Coin-Seite“",
    "CONFIRM_TIMEOUT_S": "„Warten auf die Bestätigung“",
    "LAUNCH_RETRIES": "„App erneut öffnen“",
    "MORNING_START": "„Morgens ab“",
    "MORNING_END": "„Morgens bis“",
    "EVENING_START": "„Abends ab“",
    "EVENING_END": "„Abends bis“",
    "ADB_SERIAL": "Die Geräteadresse",
    "OFFLINE_ALERT_MIN": "„Melden nach“",
    "DISCORD_WEBHOOK_URL": "Der Discord-Webhook",
}


def in_words(message: str) -> str:
    for name, label in FIELD_NAMES.items():
        message = message.replace(name, label)
    return message


def adopt_env_password(cfg: Config) -> None:
    """Ein altes WEB_PASSWORD aus der .env einmalig uebernehmen.

    Bis Version 0.3.0 stand das Passwort in der .env. Bestehende Installationen sollen nach dem
    Update nicht ploetzlich nach einer Ersteinrichtung fragen -- also wird der vorhandene Wert
    beim ersten Start gehasht abgelegt. Danach gilt nur noch die Datei; ein spaeter in der .env
    geaendertes WEB_PASSWORD hat keine Wirkung mehr.
    """
    legacy = (os.environ.get("WEB_PASSWORD") or "").strip()
    if not legacy or auth.is_set(cfg.data_dir):
        return
    try:
        auth.set_password(cfg.data_dir, legacy, legacy, only_if_unset=True)
    except auth.PasswordError as exc:
        log.warning("WEB_PASSWORD aus der .env nicht uebernommen: %s", exc)
        return
    log.info("Passwort aus der .env uebernommen. Es kann dort jetzt geleert werden.")


def build() -> FastAPI:
    """Einstiegspunkt fuer uvicorn. Das Passwort wird bei Bedarf ueber die Seite vergeben."""
    cfg = Config.load(os.environ.get("ACC_ENV_FILE") or ".env")
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO), format="%(asctime)s %(levelname)-7s %(message)s"
    )
    adopt_env_password(cfg)
    if not auth.is_set(cfg.data_dir):
        log.warning(
            "Noch kein Passwort vergeben. Die Seite fragt beim ersten Aufruf danach und zeigt bis dahin nichts an."
        )
    return create_app(cfg)
