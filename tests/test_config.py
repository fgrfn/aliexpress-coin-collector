from __future__ import annotations

from pathlib import Path

import pytest

from aliexpress_coin_collector.config import Config, ConfigError

# Alle Variablen, die Config.load liest. Sie werden pro Test geleert, damit weder die
# Umgebung des Entwicklers noch die anderen Tests (conftest setzt os.environ direkt) hineinwirken.
ENV_VARS = (
    "ADB_SERIAL",
    "ADB_PATH",
    "APP_PACKAGE",
    "COIN_URL",
    "DISCORD_WEBHOOK_URL",
    "MORNING_START",
    "MORNING_END",
    "EVENING_START",
    "EVENING_END",
    "EVENING_ENABLED",
    "NOTIFY_ON_BATTERY",
    "BATTERY_POLL_MIN",
    "BATTERY_LOW_PCT",
    "BATTERY_HOT_C",
    "HA_URL",
    "HA_TOKEN",
    "HA_PREFIX",
    "MQTT_HOST",
    "MQTT_PORT",
    "MQTT_USER",
    "MQTT_PASSWORD",
    "MQTT_DISCOVERY_PREFIX",
    "SKIP_IF_AWAKE",
    "BUSY_RETRY_MIN",
    "BUSY_MAX_WAIT_MIN",
    "PAGE_TIMEOUT_S",
    "CONFIRM_TIMEOUT_S",
    "LAUNCH_RETRIES",
    "BUTTON_LABELS",
    "OCR_LANG",
    "NOTIFY_ON_SUCCESS",
    "NOTIFY_ON_ALREADY_DONE",
    "DATA_DIR",
    "LOG_LEVEL",
)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """Sauberer Ausgangszustand: nur die Pflichtwerte gesetzt, alles andere auf Standard."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADB_SERIAL", "10.0.0.5:5555")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return monkeypatch


def load(tmp_path: Path) -> Config:
    """Laedt ohne .env-Datei, damit nur die gesetzten Umgebungsvariablen zaehlen."""
    return Config.load(tmp_path / "does-not-exist.env")


def fails(tmp_path: Path) -> str:
    with pytest.raises(ConfigError) as exc:
        load(tmp_path)
    return str(exc.value)


# --- Ausgangszustand -----------------------------------------------------


def test_defaults_are_valid(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load(tmp_path)
    assert cfg.adb_serial == "10.0.0.5:5555"
    assert cfg.log_level == "INFO"
    assert cfg.ocr_lang == "deu"


def test_conftest_values_stay_valid(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # conftest.py setzt genau diese beiden Werte, sie muessen erlaubt bleiben.
    env.setenv("PAGE_TIMEOUT_S", "2")
    env.setenv("LAUNCH_RETRIES", "0")
    cfg = load(tmp_path)
    assert (cfg.page_timeout_s, cfg.launch_retries) == (2, 0)


# --- 1. ADB_SERIAL -------------------------------------------------------


@pytest.mark.parametrize("serial", ["192.168.1.50:5555", "phone.local:5555", "R58M12ABCDE", "emulator-5554"])
def test_serial_accepts_valid_forms(env: pytest.MonkeyPatch, tmp_path: Path, serial: str) -> None:
    env.setenv("ADB_SERIAL", serial)
    assert load(tmp_path).adb_serial == serial


@pytest.mark.parametrize("serial", ["192.168.1.50 5555", "10.0.0.5:5555/x", "kein serial"])
def test_serial_rejects_invalid_forms(env: pytest.MonkeyPatch, tmp_path: Path, serial: str) -> None:
    env.setenv("ADB_SERIAL", serial)
    msg = fails(tmp_path)
    assert "ADB_SERIAL" in msg and serial in msg


def test_serial_rejects_port_out_of_range(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("ADB_SERIAL", "10.0.0.5:70000")
    msg = fails(tmp_path)
    assert "ADB_SERIAL" in msg and "70000" in msg and "1-65535" in msg


def test_serial_empty_still_reported(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("ADB_SERIAL", "  ")
    assert "ADB_SERIAL" in fails(tmp_path)


# --- 2. Zeit- und Zaehlwerte --------------------------------------------


def test_positive_values_accepted(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("PAGE_TIMEOUT_S", "1")
    env.setenv("CONFIRM_TIMEOUT_S", "1")
    env.setenv("BUSY_RETRY_MIN", "1")
    env.setenv("BUSY_MAX_WAIT_MIN", "2")
    cfg = load(tmp_path)
    assert (cfg.page_timeout_s, cfg.confirm_timeout_s, cfg.busy_retry_min, cfg.busy_max_wait_min) == (1, 1, 1, 2)


@pytest.mark.parametrize("name", ["PAGE_TIMEOUT_S", "CONFIRM_TIMEOUT_S", "BUSY_RETRY_MIN", "BUSY_MAX_WAIT_MIN"])
@pytest.mark.parametrize("value", ["0", "-5"])
def test_non_positive_values_rejected(env: pytest.MonkeyPatch, tmp_path: Path, name: str, value: str) -> None:
    env.setenv(name, value)
    msg = fails(tmp_path)
    assert name in msg and value.lstrip("+") in msg and "groesser als 0" in msg


def test_launch_retries_zero_allowed_negative_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("LAUNCH_RETRIES", "0")
    assert load(tmp_path).launch_retries == 0
    env.setenv("LAUNCH_RETRIES", "-1")
    msg = fails(tmp_path)
    assert "LAUNCH_RETRIES" in msg and "-1" in msg


def test_non_numeric_value_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("PAGE_TIMEOUT_S", "neunzig")
    assert "PAGE_TIMEOUT_S" in fails(tmp_path)


# --- 3. BUSY_RETRY_MIN < BUSY_MAX_WAIT_MIN ------------------------------


def test_busy_retry_below_max_wait_ok(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("BUSY_RETRY_MIN", "10")
    env.setenv("BUSY_MAX_WAIT_MIN", "60")
    cfg = load(tmp_path)
    assert (cfg.busy_retry_min, cfg.busy_max_wait_min) == (10, 60)


@pytest.mark.parametrize("retry", ["60", "90"])
def test_busy_retry_not_below_max_wait_rejected(env: pytest.MonkeyPatch, tmp_path: Path, retry: str) -> None:
    env.setenv("BUSY_RETRY_MIN", retry)
    env.setenv("BUSY_MAX_WAIT_MIN", "60")
    msg = fails(tmp_path)
    assert "BUSY_RETRY_MIN" in msg and "BUSY_MAX_WAIT_MIN" in msg and retry in msg


# --- 4. Morgenfenster vor Abendfenster -----------------------------------


@pytest.mark.parametrize(("m_end", "e_start"), [("10:00", "19:00"), ("10:00", "10:00")])
def test_morning_window_before_evening_ok(env: pytest.MonkeyPatch, tmp_path: Path, m_end: str, e_start: str) -> None:
    env.setenv("MORNING_END", m_end)
    env.setenv("EVENING_START", e_start)
    env.setenv("EVENING_END", "21:00")
    load(tmp_path)


def test_morning_window_after_evening_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MORNING_START", "07:00")
    env.setenv("MORNING_END", "20:00")
    msg = fails(tmp_path)
    assert "MORNING_END" in msg and "EVENING_START" in msg and "20:00" in msg


def test_window_end_before_start_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("EVENING_START", "21:00")
    env.setenv("EVENING_END", "19:00")
    assert "19:00" in fails(tmp_path)


def test_broken_time_syntax_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MORNING_START", "7 Uhr")
    assert "HH:MM" in fails(tmp_path)


# --- 5. DATA_DIR ---------------------------------------------------------


def test_data_dir_is_created(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "neu" / "data"
    env.setenv("DATA_DIR", str(target))
    cfg = load(tmp_path)
    assert cfg.data_dir == target and target.is_dir()


def test_data_dir_on_a_file_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "belegt.txt"
    blocker.write_text("kein Verzeichnis", encoding="utf-8")
    env.setenv("DATA_DIR", str(blocker))
    msg = fails(tmp_path)
    assert "DATA_DIR" in msg and str(blocker) in msg


def test_data_dir_under_a_file_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "belegt.txt"
    blocker.write_text("kein Verzeichnis", encoding="utf-8")
    env.setenv("DATA_DIR", str(blocker / "unten"))
    assert "DATA_DIR" in fails(tmp_path)


# --- 6. LOG_LEVEL --------------------------------------------------------


@pytest.mark.parametrize("level", ["DEBUG", "info", "WARNING", "ERROR", "CRITICAL"])
def test_log_level_accepted(env: pytest.MonkeyPatch, tmp_path: Path, level: str) -> None:
    env.setenv("LOG_LEVEL", level)
    assert load(tmp_path).log_level == level.upper()


def test_log_level_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("LOG_LEVEL", "TRACE")
    msg = fails(tmp_path)
    assert "LOG_LEVEL" in msg and "TRACE" in msg and "DEBUG" in msg


# --- 7. OCR_LANG ---------------------------------------------------------


@pytest.mark.parametrize("lang", ["deu", "deu+eng", "chi_sim"])
def test_ocr_lang_accepted(env: pytest.MonkeyPatch, tmp_path: Path, lang: str) -> None:
    env.setenv("OCR_LANG", lang)
    assert load(tmp_path).ocr_lang == lang


@pytest.mark.parametrize("lang", ["deu eng", "deu+", "deu,eng"])
def test_ocr_lang_rejected(env: pytest.MonkeyPatch, tmp_path: Path, lang: str) -> None:
    env.setenv("OCR_LANG", lang)
    msg = fails(tmp_path)
    assert "OCR_LANG" in msg and lang in msg


def test_ocr_lang_empty_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("OCR_LANG", "   ")
    msg = fails(tmp_path)
    assert "OCR_LANG" in msg and "leer" in msg


# --- 8. DISCORD_WEBHOOK_URL ----------------------------------------------


def test_webhook_empty_allowed(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("DISCORD_WEBHOOK_URL", "")
    assert load(tmp_path).discord_webhook == ""


def test_webhook_https_allowed(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = "https://discord.com/api/webhooks/123/geheim"
    env.setenv("DISCORD_WEBHOOK_URL", url)
    assert load(tmp_path).discord_webhook == url


def test_webhook_without_https_rejected_without_leaking_secret(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    url = "http://discord.com/api/webhooks/123/geheim"
    env.setenv("DISCORD_WEBHOOK_URL", url)
    msg = fails(tmp_path)
    assert "DISCORD_WEBHOOK_URL" in msg and "https://" in msg
    assert "geheim" not in msg and url not in msg  # der Wert ist ein Geheimnis


# --- BUTTON_LABELS (bestehende Pruefung) ---------------------------------


def test_button_labels_empty_rejected(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("BUTTON_LABELS", " , ")
    assert "BUTTON_LABELS" in fails(tmp_path)


# --- Abendlauf abschaltbar -----------------------------------------------


def test_evening_is_enabled_by_default(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert load(tmp_path).evening_enabled is True


def test_morning_window_may_sit_anywhere_without_the_evening_run(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ohne Abendlauf gibt es kein Fenster, vor dem das Morgenfenster liegen muesste."""
    env.setenv("MORNING_START", "09:00")
    env.setenv("MORNING_END", "22:00")
    env.setenv("EVENING_ENABLED", "false")
    cfg = load(tmp_path)
    assert cfg.evening_enabled is False
    assert cfg.morning_end.hour == 22


def test_morning_window_must_stay_before_the_evening_run(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MORNING_END", "22:00")
    assert "MORNING_END" in fails(tmp_path)


def test_unused_evening_window_is_not_checked(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Die abendlichen Werte bleiben stehen, spielen aber keine Rolle mehr."""
    env.setenv("EVENING_START", "21:00")
    env.setenv("EVENING_END", "19:00")
    env.setenv("EVENING_ENABLED", "false")
    assert load(tmp_path).evening_enabled is False


# --- Akku und Home Assistant ---------------------------------------------


def test_battery_defaults_are_sane(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load(tmp_path)
    assert (cfg.battery_poll_min, cfg.battery_low_pct, cfg.battery_hot_c) == (15, 25, 40)
    assert cfg.notify_on_battery is True


def test_polling_can_be_switched_off_entirely(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("BATTERY_POLL_MIN", "0")
    assert load(tmp_path).battery_poll_min == 0


def test_a_threshold_that_could_never_be_reached_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("BATTERY_LOW_PCT", "0")
    assert "BATTERY_LOW_PCT" in fails(tmp_path)
    env.setenv("BATTERY_LOW_PCT", "25")
    env.setenv("BATTERY_HOT_C", "5")
    assert "BATTERY_HOT_C" in fails(tmp_path)


def test_home_assistant_is_off_without_an_address(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load(tmp_path)
    assert cfg.ha_url == ""
    assert cfg.ha_prefix == "coin_collector"


def test_an_address_without_a_token_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sonst liefe der Dienst und schickte still 401-Antworten ins Leere."""
    env.setenv("HA_URL", "http://homeassistant.local:8123")
    assert "HA_TOKEN" in fails(tmp_path)


def test_an_address_without_a_scheme_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("HA_URL", "homeassistant.local:8123")
    env.setenv("HA_TOKEN", "geheim")
    assert "HA_URL" in fails(tmp_path)


def test_a_trailing_slash_is_trimmed(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("HA_URL", "http://homeassistant.local:8123/")
    env.setenv("HA_TOKEN", "geheim")
    assert load(tmp_path).ha_url == "http://homeassistant.local:8123"


def test_a_prefix_that_would_make_a_broken_entity_id_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("HA_URL", "http://homeassistant.local:8123")
    env.setenv("HA_TOKEN", "geheim")
    env.setenv("HA_PREFIX", "Coin Collector")
    assert "HA_PREFIX" in fails(tmp_path)


def test_mqtt_is_off_without_a_broker(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = load(tmp_path)
    assert cfg.mqtt_host == ""
    assert cfg.mqtt_port == 1883
    assert cfg.mqtt_discovery_prefix == "homeassistant"


def test_a_broker_without_credentials_is_allowed(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MQTT_HOST", "homeassistant.local")
    assert load(tmp_path).mqtt_host == "homeassistant.local"


def test_an_impossible_mqtt_port_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MQTT_HOST", "homeassistant.local")
    env.setenv("MQTT_PORT", "70000")
    assert "MQTT_PORT" in fails(tmp_path)


def test_a_discovery_prefix_with_a_space_is_refused(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MQTT_HOST", "homeassistant.local")
    env.setenv("MQTT_DISCOVERY_PREFIX", "home assistant")
    assert "MQTT_DISCOVERY_PREFIX" in fails(tmp_path)


def test_slashes_around_the_discovery_prefix_are_trimmed(env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env.setenv("MQTT_HOST", "homeassistant.local")
    env.setenv("MQTT_DISCOVERY_PREFIX", "/hass/")
    assert load(tmp_path).mqtt_discovery_prefix == "hass"
