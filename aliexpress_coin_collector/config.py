from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path

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


def _text(name: str, default: str) -> str:
    """Optionalen Textwert lesen: nicht gesetzt = Standard, leer gesetzt = Fehler."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        raise ConfigError(f"{name} darf nicht leer sein, erwartet z. B. {default!r}")
    return value


def _check_min(name: str, value: int, minimum: int) -> None:
    """Untergrenze eines Zahlenwerts pruefen."""
    if value < minimum:
        expected = "groesser als 0" if minimum == 1 else f"mindestens {minimum}"
        raise ConfigError(f"{name} muss {expected} sein, nicht {value}")


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
    morning_start: time
    morning_end: time
    evening_start: time
    evening_end: time
    skip_if_awake: bool
    busy_retry_min: int
    busy_max_wait_min: int
    page_timeout_s: int
    confirm_timeout_s: int
    launch_retries: int
    button_labels: tuple[str, ...]
    ocr_lang: str
    notify_on_success: bool
    notify_on_already_done: bool
    data_dir: Path
    log_level: str

    @classmethod
    def load(cls, env_file: str | Path = ".env") -> Config:
        _load_dotenv(Path(env_file))
        get = os.environ.get

        serial = (get("ADB_SERIAL") or "").strip()
        if not serial:
            raise ConfigError("ADB_SERIAL fehlt (z. B. 192.168.1.50:5555), siehe .env.example")
        _check_serial(serial)

        labels = tuple(s.strip() for s in (get("BUTTON_LABELS") or "Sammeln,Collect,Claim").split(",") if s.strip())
        if not labels:
            raise ConfigError("BUTTON_LABELS darf nicht leer sein")

        cfg = cls(
            adb_serial=serial,
            adb_path=get("ADB_PATH") or "adb",
            app_package=get("APP_PACKAGE") or "com.alibaba.aliexpresshd",
            coin_url=get("COIN_URL") or DEFAULT_URL,
            discord_webhook=(get("DISCORD_WEBHOOK_URL") or "").strip(),
            morning_start=_time(get("MORNING_START") or "07:00"),
            morning_end=_time(get("MORNING_END") or "10:00"),
            evening_start=_time(get("EVENING_START") or "19:00"),
            evening_end=_time(get("EVENING_END") or "21:00"),
            skip_if_awake=_bool(get("SKIP_IF_AWAKE") or "true"),
            busy_retry_min=_int("BUSY_RETRY_MIN", 15),
            busy_max_wait_min=_int("BUSY_MAX_WAIT_MIN", 120),
            page_timeout_s=_int("PAGE_TIMEOUT_S", 90),
            confirm_timeout_s=_int("CONFIRM_TIMEOUT_S", 25),
            launch_retries=_int("LAUNCH_RETRIES", 1),
            button_labels=labels,
            ocr_lang=_text("OCR_LANG", "deu"),
            notify_on_success=_bool(get("NOTIFY_ON_SUCCESS") or "true"),
            notify_on_already_done=_bool(get("NOTIFY_ON_ALREADY_DONE") or "false"),
            data_dir=Path(get("DATA_DIR") or "./data"),
            log_level=(get("LOG_LEVEL") or "INFO").strip().upper(),
        )
        cfg._validate()
        return cfg

    def _validate(self) -> None:
        """Alle Werte pruefen, damit eine falsche .env sofort beim Start auffaellt."""
        _check_min("PAGE_TIMEOUT_S", self.page_timeout_s, 1)
        _check_min("CONFIRM_TIMEOUT_S", self.confirm_timeout_s, 1)
        _check_min("BUSY_RETRY_MIN", self.busy_retry_min, 1)
        _check_min("BUSY_MAX_WAIT_MIN", self.busy_max_wait_min, 1)
        _check_min("LAUNCH_RETRIES", self.launch_retries, 0)
        if self.busy_retry_min >= self.busy_max_wait_min:
            raise ConfigError(
                f"BUSY_RETRY_MIN ({self.busy_retry_min}) muss kleiner als BUSY_MAX_WAIT_MIN "
                f"({self.busy_max_wait_min}) sein, sonst wird nie erzwungen gestartet"
            )

        for name, start, end in (
            ("Morgenfenster", self.morning_start, self.morning_end),
            ("Abendfenster", self.evening_start, self.evening_end),
        ):
            if end < start:
                raise ConfigError(f"Ende des {name}s ({end:%H:%M}) liegt vor dessen Beginn ({start:%H:%M})")
        if self.morning_end > self.evening_start:
            raise ConfigError(
                f"MORNING_END ({self.morning_end:%H:%M}) muss vor oder auf EVENING_START "
                f"({self.evening_start:%H:%M}) liegen, das Morgenfenster gehoert vor das Abendfenster"
            )

        _check_data_dir(self.data_dir)

        if self.log_level not in _LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL muss einer von {', '.join(_LOG_LEVELS)} sein, nicht {self.log_level!r}")
        if not _OCR_LANG_RE.match(self.ocr_lang):
            raise ConfigError(f"OCR_LANG ist ungueltig: {self.ocr_lang!r}, erwartet z. B. 'deu' oder 'deu+eng'")
        if self.discord_webhook and not self.discord_webhook.startswith("https://"):
            # Der Wert selbst ist ein Geheimnis und darf nicht in die Meldung.
            raise ConfigError("DISCORD_WEBHOOK_URL muss mit 'https://' beginnen (oder leer bleiben)")
