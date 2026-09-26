from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

from . import settings

log = logging.getLogger(__name__)

DEFAULT_URL = (
    "https://m.aliexpress.com/p/coin-index/index.html"
    "?_immersiveMode=true&adc_manifest=coinindex&disableNav=true&from=newHp&wh_ttid=adc"
)

# Entweder host:port (TCP, der Normalfall) oder eine USB-Seriennummer ohne Leerzeichen.
_SERIAL_RE = re.compile(r"^(?:[A-Za-z0-9.\-]+:\d{1,5}|[A-Za-z0-9._\-]+)$")
# Eine Tesseract-Sprache oder mehrere mit "+" verbunden, z. B. "deu" oder "deu+eng".
_OCR_LANG_RE = re.compile(r"^[A-Za-z_]{2,}(?:\+[A-Za-z_]{2,})*$")
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(RuntimeError):
    pass


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Parser. Bereits gesetzte Umgebungsvariablen haben Vorrang."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _time(value: str) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except ValueError as exc:
        raise ConfigError(f"Ungueltige Uhrzeit {value!r}, erwartet HH:MM") from exc


def _bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on", "ja")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine ganze Zahl sein, nicht {raw!r}") from exc


def _list_raw(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Kommaliste, Gross- und Kleinschreibung bleibt -- fuer Werte, die eingetippt werden."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Kommaliste lesen: nicht gesetzt = Vorgabe, leer gesetzt = leere Liste.

    Kleingeschrieben, weil die Vergleiche im kleingeschriebenen Text stattfinden.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _text(name: str, default: str) -> str:
    """Optionalen Textwert lesen: nicht gesetzt = Standard, leer gesetzt = Fehler."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        raise ConfigError(f"{name} darf nicht leer sein, erwartet z. B. {default!r}")
    return value


# Vorgabe fuer LOGIN_MARKERS. Steht hier und nicht in ocr.py, damit die Weboberflaeche sie lesen
# kann, ohne cv2 und pytesseract in ihren Prozess zu ziehen. Kleingeschrieben, weil im
# kleingeschriebenen Volltext gesucht wird.
DEFAULT_LOGIN_MARKERS = ("anmelden", "einloggen", "anmeldung", "sign in", "log in", "登录")

# Zusatzaufgaben der Coin-Seite. Angefasst wird nur, was auf der Positivliste steht -- die
# Aufgaben wechseln staendig, und eine Sperrliste waere morgen unvollstaendig. Verglichen wird
# gegen die normalisierte Zeile (klein, ohne Umlaute), darum stehen hier Wortstaemme.
DEFAULT_EXTRAS_ALLOW = (
    "entdeck",  # "Gesponserte Artikel entdecken"
    "stober",  # "In kuerzlich angesehenen Artikeln stoebern", "Stoebern Sie auf dieser Seite 15 Sekunden"
    "ubersicht",  # "Uebersicht ueber Ihre Muenzeinsparungen anzeigen"
    "rabatt",  # "Super Rabatte anzeigen"
    "surfen",  # "Surfen Sie 15 Sek. auf dieser Seite"
    "anmeldung",  # "Taegliche Anmeldung" -- ein Knopf auf der Coin-Seite, kein Login
    "browse",
    "explore",
)
# Bewusst nicht dabei: "Suchen, was Sie lieben" -- dort muss ein Suchwort eingetippt werden,
# das ist keine reine Verweildauer mehr.
DEFAULT_EXTRAS_DENY = (
    "merge",  # "Schliessen Sie 1 Merge-Boss-Spielrunde ab"
    "spiel",
    "runde",
    "quiz",  # "Tagesquiz-Herausforderung"
    "game",
    "video",
    "bewert",
    "kaufen",  # bewusst nicht "kauf": das traefe auch "Einkaufsguthaben"
    "bestell",
    # "warenkorb" stand hier und sperrte "In kuerzlich angesehenen Artikeln stoebern" mit --
    # in dessen Beschreibung kommt das Wort vor, ohne dass etwas hineingelegt wuerde. Das
    # Hineinlegen sperren "hinzufug" und "preisland", und die treffen genauer.
    "hinzufug",  # "1 x Wasser bei Preisland hinzufuegen" legt etwas in den Warenkorb
    "preisland",
    "abonn",
    "folgen",
    "teilen",
    "einladen",
    "freund",
    "bezahl",
    # "anmelden" bleibt gesperrt: das ist der Login, und der bleibt Sache des Nutzers.
    # "anmeldung" nicht -- "Taegliche Anmeldung" ist eine Aufgabe der Coin-Seite und enthaelt
    # "anmelden" nicht als Teilzeichenkette.
    "anmelden",
)
# Die Knoepfe in der Liste. Auf ihnen wird getippt, nicht auf dem Titel -- und sie zeigen
# zugleich an, dass wir ueberhaupt in der Liste sind.
DEFAULT_EXTRAS_GO = ("und los", "los geht", "go")
# Aufgaben, bei denen ein Suchbegriff eingetippt werden muss ("Suchen, was Sie lieben --
# Verdienen Sie durch die Nutzung von Schluesselwoertern"). Sie werden nur angefasst, wenn
# EXTRAS_SEARCH_TERMS etwas hergibt.
# Das Werbefenster beim Verlassen ("Nicht vergessen: morgen einchecken!") hat zwei Knoepfe.
# Getippt wird immer "Bleiben": es macht das Fenster weg, ohne die Coin-Seite zu verlassen.
DEFAULT_EXTRAS_STAY = ("bleiben", "stay")
DEFAULT_EXTRAS_SEARCH_MARKERS = ("suchen", "schlusselwort", "search", "keyword")
# Was gesucht wird. Landet im Suchverlauf des Kontos -- darum nur, was hier ausdruecklich steht.
DEFAULT_EXTRAS_SEARCH_TERMS = ("Jayo PETG 1.1KG",)
# Ohne Umlaute und Sonderzeichen: der Begriff geht durch Shell und `input text`.
_SEARCH_TERM_RE = re.compile(r"[A-Za-z0-9 .,+-]{2,40}")


def _check_min(name: str, value: int, minimum: int) -> None:
    """Untergrenze eines Zahlenwerts pruefen."""
    if value < minimum:
        expected = "groesser als 0" if minimum == 1 else f"mindestens {minimum}"
        raise ConfigError(f"{name} muss {expected} sein, nicht {value}")


def _check_range(name: str, value: int, low: int, high: int) -> None:
    """Zahlenwert innerhalb sinnvoller Grenzen. Eine Schwelle ausserhalb waere nie erreichbar."""
    if not low <= value <= high:
        raise ConfigError(f"{name} muss zwischen {low} und {high} liegen, nicht {value}")


_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _check_mqtt(host: str, port: int, discovery_prefix: str, entity_prefix: str) -> None:
    """Der MQTT-Weg, falls ein Broker gesetzt ist.

    Benutzername und Passwort sind nicht Pflicht: ein Broker im eigenen Netz laeuft durchaus
    ohne Anmeldung. Der Discovery-Praefix dagegen muss stimmen, sonst horcht Home Assistant
    an einem Zweig, in den nie etwas geschrieben wird.
    """
    if not _PREFIX_RE.match(entity_prefix):
        raise ConfigError(
            f"HA_PREFIX ist ungueltig: {entity_prefix!r}. Erlaubt sind Kleinbuchstaben, Ziffern "
            "und Unterstriche, beginnend mit einem Buchstaben -- daraus werden die Namen der "
            "Entitaeten"
        )
    if not host:
        return
    if not 1 <= port <= 65535:
        raise ConfigError(f"MQTT_PORT muss zwischen 1 und 65535 liegen, nicht {port}")
    if not discovery_prefix or any(c.isspace() for c in discovery_prefix):
        raise ConfigError(
            f"MQTT_DISCOVERY_PREFIX ist ungueltig: {discovery_prefix!r}. Ueblich ist "
            "'homeassistant' -- derselbe Wert, der in Home Assistant unter MQTT eingestellt ist"
        )


def _check_serial(serial: str) -> None:
    """ADB_SERIAL auf host:port oder USB-Seriennummer pruefen."""
    if not _SERIAL_RE.match(serial):
        raise ConfigError(
            f"ADB_SERIAL ist ungueltig: {serial!r}. Erwartet host:port (z. B. 192.168.1.50:5555) "
            "oder eine USB-Seriennummer ohne Leerzeichen"
        )
    _host, sep, port = serial.rpartition(":")
    if sep and not 1 <= int(port) <= 65535:
        raise ConfigError(f"ADB_SERIAL enthaelt den ungueltigen Port {port}, erwartet 1-65535 (Wert: {serial!r})")


def _check_data_dir(path: Path) -> None:
    """DATA_DIR muss anlegbar und beschreibbar sein, sonst faellt es erst im Betrieb auf."""
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"DATA_DIR {path} laesst sich nicht anlegen ({exc.strerror}), erwartet ein beschreibbares Verzeichnis"
        ) from exc
    probe = path / ".write-test"
    try:
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise ConfigError(
            f"DATA_DIR {path} ist nicht beschreibbar ({exc.strerror}), Rechte des Dienstbenutzers pruefen"
        ) from exc


@dataclass(frozen=True)
class Config:
    adb_serial: str
    adb_path: str
    app_package: str
    coin_url: str
    discord_webhook: str
    # Wann der Tag der App beginnt -- nicht um Mitternacht, siehe scheduler.coin_day.
    coin_day_start: time
    morning_start: time
    morning_end: time
    evening_start: time
    evening_end: time
    evening_enabled: bool
    skip_if_awake: bool
    busy_retry_min: int
    busy_max_wait_min: int
    page_timeout_s: int
    confirm_timeout_s: int
    launch_retries: int
    button_labels: tuple[str, ...]
    login_markers: tuple[str, ...]
    ocr_lang: str
    # Zusatzaufgaben. Steht alles nur in der .env: es haengt an der Erkennung, und ein Vertipper
    # in der Oberflaeche liesse den Dienst ins Leere greifen, ohne dass es auffiele.
    extras_button_labels: tuple[str, ...]
    extras_allow: tuple[str, ...]
    extras_deny: tuple[str, ...]
    extras_go: tuple[str, ...]
    extras_search_markers: tuple[str, ...]
    extras_stay: tuple[str, ...]
    extras_search_terms: tuple[str, ...]
    # Ob der Dienst nach einem erfolgreichen Check-in gleich die Zusatzaufgaben mitnimmt.
    # Abschaltbar in der Oberflaeche: es ist der einzige Teil, der ohne Not am Geraet tippt.
    extras_after_run: bool
    extras_dwell_s: int
    extras_max: int
    # So oft wird dieselbe Aufgabe hoechstens hintereinander abgearbeitet. Manche lassen sich
    # mehrfach abholen ("2/3" auf der Karte). Schluss ist, sobald die Karte den Haken traegt --
    # die Zahl ist nur die Reissleine, falls die Karte das nie tut.
    extras_repeats: int
    extras_scrolls: int
    extras_budget_s: int
    notify_on_success: bool
    notify_on_already_done: bool
    notify_on_offline: bool
    offline_alert_min: int
    notify_weekly: bool
    notify_on_stall: bool
    notify_on_battery: bool
    battery_poll_min: int
    battery_low_pct: int
    battery_hot_c: int
    ha_prefix: str
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str
    mqtt_discovery_prefix: str
    data_dir: Path
    log_level: str
    # Zeitpunkt der letzten Aenderung aus settings.json, None wenn es keine gibt.
    # scheduler.decide nutzt ihn, damit eine Fensteraenderung keinen rueckwirkenden Lauf ausloest.
    settings_changed_at: datetime | None = None

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Config:
        _load_dotenv(Path(env_file))
        get = os.environ.get

        labels = tuple(s.strip() for s in (get("BUTTON_LABELS") or "Sammeln,Collect,Claim").split(",") if s.strip())
        if not labels:
            raise ConfigError("BUTTON_LABELS darf nicht leer sein")

        # Kleingeschrieben, weil looks_logged_out im kleingeschriebenen Volltext sucht.
        raw_markers = get("LOGIN_MARKERS")
        markers = (
            tuple(m.strip().lower() for m in raw_markers.split(",") if m.strip())
            if raw_markers is not None
            else DEFAULT_LOGIN_MARKERS
        )
        if not markers:
            raise ConfigError("LOGIN_MARKERS darf nicht leer sein (weglassen setzt die Vorgabe)")

        data_dir = Path(get("DATA_DIR") or "./data")
        base: dict[str, Any] = {
            "adb_serial": (get("ADB_SERIAL") or "").strip(),
            "adb_path": get("ADB_PATH") or "adb",
            "app_package": get("APP_PACKAGE") or "com.alibaba.aliexpresshd",
            "coin_url": get("COIN_URL") or DEFAULT_URL,
            "discord_webhook": (get("DISCORD_WEBHOOK_URL") or "").strip(),
            # Die neuen Muenzen stehen erst am Vormittag bereit. Ein Lauf davor sieht noch
            # den Stand des Vortags und gehoert darum zum vorigen Muenztag.
            "coin_day_start": _time(get("COIN_DAY_START") or "08:00"),
            "morning_start": _time(get("MORNING_START") or "07:00"),
            "morning_end": _time(get("MORNING_END") or "10:00"),
            "evening_start": _time(get("EVENING_START") or "19:00"),
            "evening_end": _time(get("EVENING_END") or "21:00"),
            "evening_enabled": _bool(get("EVENING_ENABLED") or "true"),
            "skip_if_awake": _bool(get("SKIP_IF_AWAKE") or "true"),
            "busy_retry_min": _int("BUSY_RETRY_MIN", 15),
            "busy_max_wait_min": _int("BUSY_MAX_WAIT_MIN", 120),
            "page_timeout_s": _int("PAGE_TIMEOUT_S", 90),
            "confirm_timeout_s": _int("CONFIRM_TIMEOUT_S", 25),
            "launch_retries": _int("LAUNCH_RETRIES", 1),
            "button_labels": labels,
            "login_markers": markers,
            "ocr_lang": _text("OCR_LANG", "deu"),
            # Der Knopf heisst nach dem Einsammeln "Mehr Muenzen verdienen". Gesucht wird ein
            # einzelnes Wort, nicht die Wortgruppe -- "verdienen" ist das kennzeichnende.
            "extras_button_labels": _list("EXTRAS_BUTTON_LABELS", ("verdienen", "earn")),
            "extras_allow": _list("EXTRAS_ALLOW", DEFAULT_EXTRAS_ALLOW),
            "extras_deny": _list("EXTRAS_DENY", DEFAULT_EXTRAS_DENY),
            "extras_go": _list("EXTRAS_GO", DEFAULT_EXTRAS_GO),
            "extras_search_markers": _list("EXTRAS_SEARCH_MARKERS", DEFAULT_EXTRAS_SEARCH_MARKERS),
            "extras_stay": _list("EXTRAS_STAY", DEFAULT_EXTRAS_STAY),
            "extras_search_terms": _list_raw("EXTRAS_SEARCH_TERMS", DEFAULT_EXTRAS_SEARCH_TERMS),
            # Die App zaehlt 15 Sekunden -- aber erst, wenn die Seite steht. Auf dem langsamen
            # Geraet gehen dafuer die ersten Sekunden drauf, darum reichlich Luft.
            "extras_after_run": _bool(get("EXTRAS_AFTER_RUN") or "true"),
            "extras_dwell_s": _int("EXTRAS_DWELL_S", 25),
            "extras_max": _int("EXTRAS_MAX", 6),
            "extras_repeats": _int("EXTRAS_REPEATS", 3),
            "extras_scrolls": _int("EXTRAS_SCROLLS", 3),
            "extras_budget_s": _int("EXTRAS_BUDGET_S", 300),
            "notify_on_success": _bool(get("NOTIFY_ON_SUCCESS") or "true"),
            "notify_on_already_done": _bool(get("NOTIFY_ON_ALREADY_DONE") or "false"),
            "notify_on_offline": _bool(get("NOTIFY_ON_OFFLINE") or "true"),
            "offline_alert_min": _int("OFFLINE_ALERT_MIN", 30),
            "notify_weekly": _bool(get("NOTIFY_WEEKLY") or "true"),
            "notify_on_stall": _bool(get("NOTIFY_ON_STALL") or "true"),
            "notify_on_battery": _bool(get("NOTIFY_ON_BATTERY") or "true"),
            "battery_poll_min": _int("BATTERY_POLL_MIN", 15),
            "battery_low_pct": _int("BATTERY_LOW_PCT", 25),
            "battery_hot_c": _int("BATTERY_HOT_C", 40),
            # Namensanfang der Entitaeten in Home Assistant. Heisst weiter HA_, weil es
            # Home-Assistant-Entitaeten sind -- MQTT ist nur der Weg dorthin.
            "ha_prefix": (get("HA_PREFIX") or "coin_collector").strip(),
            # Ohne Broker bleibt auch dieser Weg aus. Beide zugleich sind erlaubt, ergeben aber
            # zwei Saetze Entitaeten fuer dieselbe Sache -- der Dienst warnt dann beim Start.
            "mqtt_host": (get("MQTT_HOST") or "").strip(),
            "mqtt_port": _int("MQTT_PORT", 1883),
            "mqtt_user": (get("MQTT_USER") or "").strip(),
            "mqtt_password": get("MQTT_PASSWORD") or "",
            "mqtt_discovery_prefix": (get("MQTT_DISCOVERY_PREFIX") or "homeassistant").strip().strip("/"),
            "data_dir": data_dir,
            "log_level": (get("LOG_LEVEL") or "INFO").strip().upper(),
        }

        # settings.json ueberschreibt die .env. Bewusst hier und nicht erst im Dienst, damit CLI,
        # Dienst und Weboberflaeche dieselben Werte sehen.
        overrides = settings.load(data_dir)
        try:
            return cls._build({**base, **overrides.values}, overrides.changed_at)
        except ConfigError as exc:
            if not overrides.values:
                raise
            # Von Hand verstellte Werte duerfen den taeglichen Lauf nicht anhalten. Die .env ist
            # die Rueckfallebene; ist die auch unbrauchbar, faellt das hier weiter auf.
            log.warning("settings.json ergibt keine gueltige Einstellung, es gilt die .env: %s", exc)
            return cls._build(base, None)

    @classmethod
    def _build(cls, values: dict[str, Any], changed_at: datetime | None) -> Config:
        if not values["adb_serial"]:
            raise ConfigError("ADB_SERIAL fehlt (z. B. 192.168.1.50:5555), siehe .env.example")
        cfg = cls(**values, settings_changed_at=changed_at)
        cfg.validate()
        # Nur beim Laden, nicht in validate(): der Test legt eine Datei an und loescht sie wieder.
        # validate() laeuft bei jedem Takt des Dienstes und bei jeder Anfrage der Oberflaeche --
        # dort waere das unnoetige Schreibarbeit, und zwei gleichzeitige Pruefungen kaemen sich
        # ueber dieselbe Pruefdatei in die Quere. DATA_DIR ist ohnehin nicht zur Laufzeit aenderbar.
        _check_data_dir(cfg.data_dir)
        return cfg

    def validate(self) -> None:
        """Alle Werte pruefen. Wird auch zur Laufzeit aufgerufen, wenn settings.json sich aendert,
        und fasst darum nichts an -- sie prueft nur."""
        _check_serial(self.adb_serial)
        _check_min("PAGE_TIMEOUT_S", self.page_timeout_s, 1)
        _check_min("CONFIRM_TIMEOUT_S", self.confirm_timeout_s, 1)
        _check_min("BUSY_RETRY_MIN", self.busy_retry_min, 1)
        _check_min("BUSY_MAX_WAIT_MIN", self.busy_max_wait_min, 1)
        _check_min("LAUNCH_RETRIES", self.launch_retries, 0)
        _check_min("OFFLINE_ALERT_MIN", self.offline_alert_min, 1)
        _check_min("EXTRAS_DWELL_S", self.extras_dwell_s, 1)
        # 0 heisst: hinsehen, aber nichts antippen.
        _check_min("EXTRAS_MAX", self.extras_max, 0)
        _check_min("EXTRAS_REPEATS", self.extras_repeats, 1)
        _check_min("EXTRAS_SCROLLS", self.extras_scrolls, 0)
        _check_min("EXTRAS_BUDGET_S", self.extras_budget_s, 30)
        for term in self.extras_search_terms:
            if not _SEARCH_TERM_RE.fullmatch(term):
                raise ConfigError(
                    f"EXTRAS_SEARCH_TERMS enthaelt einen unbrauchbaren Begriff: {term!r}. "
                    "Erlaubt sind 2 bis 40 Buchstaben, Ziffern, Leerzeichen und . , + - "
                    "(keine Umlaute: der Begriff geht durch Shell und ADB)"
                )
        if not self.extras_go:
            raise ConfigError("EXTRAS_GO darf nicht leer sein (weglassen setzt die Vorgabe)")
        if not self.extras_button_labels:
            raise ConfigError("EXTRAS_BUTTON_LABELS darf nicht leer sein (weglassen setzt die Vorgabe)")
        # 0 ist erlaubt und heisst: nur bei einem Lauf nachsehen, nicht regelmaessig.
        _check_min("BATTERY_POLL_MIN", self.battery_poll_min, 0)
        _check_range("BATTERY_LOW_PCT", self.battery_low_pct, 1, 99)
        _check_range("BATTERY_HOT_C", self.battery_hot_c, 20, 80)
        _check_mqtt(self.mqtt_host, self.mqtt_port, self.mqtt_discovery_prefix, self.ha_prefix)
        if self.busy_retry_min >= self.busy_max_wait_min:
            raise ConfigError(
                f"BUSY_RETRY_MIN ({self.busy_retry_min}) muss kleiner als BUSY_MAX_WAIT_MIN "
                f"({self.busy_max_wait_min}) sein, sonst wird nie erzwungen gestartet"
            )

        windows = [("Morgenfenster", self.morning_start, self.morning_end)]
        if self.evening_enabled:
            windows.append(("Abendfenster", self.evening_start, self.evening_end))
        for name, start, end in windows:
            if end < start:
                raise ConfigError(f"Ende des {name}s ({end:%H:%M}) liegt vor dessen Beginn ({start:%H:%M})")
        # Ohne Abendlauf darf das Morgenfenster liegen, wo es will -- die abendlichen Werte
        # stehen dann zwar noch in der Konfiguration, spielen aber keine Rolle mehr.
        if self.evening_enabled and self.morning_end > self.evening_start:
            raise ConfigError(
                f"MORNING_END ({self.morning_end:%H:%M}) muss vor oder auf EVENING_START "
                f"({self.evening_start:%H:%M}) liegen, das Morgenfenster gehoert vor das Abendfenster"
            )

        if self.log_level not in _LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL muss einer von {', '.join(_LOG_LEVELS)} sein, nicht {self.log_level!r}")
        if not _OCR_LANG_RE.match(self.ocr_lang):
            raise ConfigError(f"OCR_LANG ist ungueltig: {self.ocr_lang!r}, erwartet z. B. 'deu' oder 'deu+eng'")
        if self.discord_webhook and not self.discord_webhook.startswith("https://"):
            # Der Wert selbst ist ein Geheimnis und darf nicht in die Meldung.
            raise ConfigError("DISCORD_WEBHOOK_URL muss mit 'https://' beginnen (oder leer bleiben)")
