from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

DEFAULT_URL = (
    "https://m.aliexpress.com/p/coin-index/index.html"
    "?_immersiveMode=true&adc_manifest=coinindex&disableNav=true&from=newHp&wh_ttid=adc"
)


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
            ocr_lang=get("OCR_LANG") or "deu",
            notify_on_success=_bool(get("NOTIFY_ON_SUCCESS") or "true"),
            notify_on_already_done=_bool(get("NOTIFY_ON_ALREADY_DONE") or "false"),
            data_dir=Path(get("DATA_DIR") or "./data"),
            log_level=(get("LOG_LEVEL") or "INFO").upper(),
        )
        if cfg.morning_end < cfg.morning_start or cfg.evening_end < cfg.evening_start:
            raise ConfigError("Ende eines Zeitfensters liegt vor dessen Beginn")
        return cfg
