from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pytest

from aliexpress_coin_collector import settings
from aliexpress_coin_collector.scheduler import decide, plan_for, take_request, touch_heartbeat, with_current_settings
from aliexpress_coin_collector.store import Attempt

DAY = date(2026, 9, 21)


def att(ts, kind, outcome):
    return Attempt(ts=ts, kind=kind, outcome=outcome)


# -- settings.json lesen und schreiben --------------------------------------------------------


def test_missing_file_yields_empty_overrides(tmp_path):
    assert settings.load(tmp_path).empty


def test_save_and_load_roundtrip(tmp_path):
    windows = {"morning_start": time(6, 0), "morning_end": time(9, 30)}
    saved = settings.save(tmp_path, windows, now=datetime(2026, 9, 21, 12, 0))
    loaded = settings.load(tmp_path)
    assert loaded.windows == windows
    assert loaded.changed_at == saved.changed_at == datetime(2026, 9, 21, 12, 0)


def test_save_rejects_unknown_keys(tmp_path):
    with pytest.raises(ValueError, match="quatsch"):
        settings.save(tmp_path, {"quatsch": 1})


def test_save_rejects_a_value_of_the_wrong_type(tmp_path):
    # page_timeout_s ist eine Zahl, keine Uhrzeit -- ein Verwechsler faellt beim Speichern auf.
    with pytest.raises(ValueError, match="page_timeout_s"):
        settings.save(tmp_path, {"page_timeout_s": time(1, 0)})


def test_broken_file_is_ignored_instead_of_crashing(tmp_path):
    settings.path_for(tmp_path).write_text("{kaputt", encoding="utf-8")
    assert settings.load(tmp_path).empty


def test_garbage_values_are_skipped_individually(tmp_path):
    settings.path_for(tmp_path).write_text(
        '{"morning_start": "07:15", "morning_end": "kaputt", "changed_at": "nein"}', encoding="utf-8"
    )
    overrides = settings.load(tmp_path)
    assert overrides.windows == {"morning_start": time(7, 15)}
    assert overrides.changed_at is None


# -- Alles ausser den Fenstern ----------------------------------------------------------------


def test_numbers_flags_and_text_survive_a_roundtrip(tmp_path):
    values = {
        "skip_if_awake": False,
        "busy_retry_min": 12,
        "adb_serial": "10.0.0.9:5555",
        "discord_webhook": "https://discord.com/api/webhooks/1/x",
    }
    settings.save(tmp_path, values)
    assert settings.load(tmp_path).values == values


def test_a_flag_written_as_a_number_is_ignored(tmp_path):
    # true/false, nicht 1/0: sonst waere jede Zahl versehentlich ein Ja.
    settings.path_for(tmp_path).write_text('{"skip_if_awake": 1, "busy_retry_min": 12}', encoding="utf-8")
    assert settings.load(tmp_path).values == {"busy_retry_min": 12}


def test_a_number_written_as_a_flag_is_ignored(tmp_path):
    settings.path_for(tmp_path).write_text('{"busy_retry_min": true}', encoding="utf-8")
    assert settings.load(tmp_path).values == {}


def test_saving_one_section_leaves_the_others_alone(tmp_path):
    # Die Oberflaeche hat je Abschnitt ein eigenes Formular. Das Speichern der Meldungen
    # darf die Zeitfenster nicht mitloeschen.
    settings.save(tmp_path, {"morning_start": time(6, 0), "busy_retry_min": 12})
    settings.save(tmp_path, {"notify_on_success": False})
    loaded = settings.load(tmp_path)
    assert loaded.values == {
        "morning_start": time(6, 0),
        "busy_retry_min": 12,
        "notify_on_success": False,
    }


def test_the_file_is_only_readable_by_its_owner(tmp_path):
    # Sie kann den Discord-Webhook enthalten.
    settings.save(tmp_path, {"discord_webhook": "https://discord.com/api/webhooks/1/x"})
    assert settings.path_for(tmp_path).stat().st_mode & 0o077 == 0


def test_a_secret_is_never_written_to_the_log(tmp_path, caplog):
    settings.path_for(tmp_path).write_text('{"discord_webhook": 12345}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        settings.load(tmp_path)
    assert "12345" not in caplog.text
    assert "geheim" in caplog.text


# -- Der Aenderungszeitpunkt gilt nur den Fenstern ---------------------------------------------


def test_only_a_moved_window_advances_the_change_stamp(tmp_path):
    first = settings.save(tmp_path, {"morning_start": time(6, 0)}, now=datetime(2026, 9, 21, 8, 0))
    # Ein anderer Abschnitt darf den Zeitplan von heute nicht stilllegen.
    later = settings.save(tmp_path, {"notify_on_success": False}, now=datetime(2026, 9, 21, 9, 0))
    assert later.changed_at == first.changed_at == datetime(2026, 9, 21, 8, 0)


def test_saving_the_same_window_again_does_not_advance_the_stamp(tmp_path):
    first = settings.save(tmp_path, {"morning_start": time(6, 0)}, now=datetime(2026, 9, 21, 8, 0))
    again = settings.save(tmp_path, {"morning_start": time(6, 0)}, now=datetime(2026, 9, 21, 9, 0))
    assert again.changed_at == first.changed_at


def test_a_really_moved_window_does_advance_the_stamp(tmp_path):
    settings.save(tmp_path, {"morning_start": time(6, 0)}, now=datetime(2026, 9, 21, 8, 0))
    moved = settings.save(tmp_path, {"morning_start": time(6, 30)}, now=datetime(2026, 9, 21, 9, 0))
    assert moved.changed_at == datetime(2026, 9, 21, 9, 0)


# -- Unbrauchbare Werte halten den Dienst nicht an ----------------------------------------------


def test_a_semantically_broken_file_falls_back_to_the_env(cfg, tmp_path, caplog):
    from aliexpress_coin_collector.config import Config

    # Lesbar und richtig getypt, aber unsinnig: Wiederholung nach dem Erzwingen.
    settings.path_for(tmp_path).write_text('{"busy_retry_min": 500, "busy_max_wait_min": 90}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        reloaded = Config.load(tmp_path / "does-not-exist.env")
    assert reloaded.busy_retry_min == 15  # Vorgabe aus der .env, nicht die 500
    assert "settings.json" in caplog.text


def test_the_daemon_keeps_its_config_when_the_file_turns_bad(cfg, caplog):
    settings.path_for(cfg.data_dir).write_text('{"page_timeout_s": 0}', encoding="utf-8")
    with caplog.at_level("WARNING"):
        assert with_current_settings(cfg) is cfg


def test_the_daemon_picks_up_a_value_outside_the_windows(cfg):
    settings.save(cfg.data_dir, {"busy_retry_min": 7, "adb_serial": "10.0.0.9:5555"})
    updated = with_current_settings(cfg)
    assert updated.busy_retry_min == 7
    assert updated.adb_serial == "10.0.0.9:5555"


def test_config_applies_overrides(cfg, tmp_path):
    from aliexpress_coin_collector.config import Config

    settings.save(tmp_path, {"morning_start": time(6, 0), "morning_end": time(6, 30)})
    reloaded = Config.load(tmp_path / "does-not-exist.env")
    assert reloaded.morning_start == time(6, 0)
    assert reloaded.morning_end == time(6, 30)
    assert reloaded.settings_changed_at is not None


def test_with_current_settings_picks_up_a_change(cfg):
    settings.save(cfg.data_dir, {"morning_start": time(5, 0), "morning_end": time(5, 30)})
    updated = with_current_settings(cfg)
    assert updated.morning_start == time(5, 0)
    assert updated is not cfg


def test_with_current_settings_returns_the_same_config_when_nothing_is_set(cfg):
    assert with_current_settings(cfg) is cfg


# -- Wirksamkeitsregel: sofort, aber nie rueckwirkend -----------------------------------------


def test_a_change_does_not_trigger_a_run_for_a_time_already_passed(cfg):
    plan = plan_for(DAY, cfg)
    # Fenster wurde geaendert, nachdem die (neue) Morgenzeit schon verstrichen war.
    changed = replace(cfg, settings_changed_at=plan.morning_at + timedelta(minutes=5))
    assert decide(plan.morning_at + timedelta(minutes=10), plan, [], changed) is None


def test_a_change_before_the_planned_time_still_allows_the_run(cfg):
    plan = plan_for(DAY, cfg)
    changed = replace(cfg, settings_changed_at=plan.morning_at - timedelta(minutes=5))
    decision = decide(plan.morning_at + timedelta(minutes=1), plan, [], changed)
    assert decision and decision.kind == "morning"


def test_the_rule_expires_the_next_day(cfg):
    # Aenderung von heute darf den Lauf von morgen nicht blockieren.
    today = plan_for(DAY, cfg)
    tomorrow = plan_for(DAY + timedelta(days=1), cfg)
    changed = replace(cfg, settings_changed_at=today.morning_at + timedelta(minutes=5))
    decision = decide(tomorrow.morning_at + timedelta(minutes=1), tomorrow, [], changed)
    assert decision and decision.kind == "morning"


def test_the_rule_also_covers_the_evening_run(cfg):
    plan = plan_for(DAY, cfg)
    changed = replace(cfg, settings_changed_at=plan.evening_at + timedelta(minutes=5))
    assert decide(plan.evening_at + timedelta(minutes=10), plan, [], changed) is None


def test_without_a_change_everything_behaves_as_before(cfg):
    plan = plan_for(DAY, cfg)
    decision = decide(plan.morning_at + timedelta(minutes=1), plan, [], cfg)
    assert decision and decision.kind == "morning"


# -- Herzschlag und Auftragsdatei --------------------------------------------------------------


def test_heartbeat_is_written(cfg):
    touch_heartbeat(cfg)
    assert (cfg.data_dir / "heartbeat").is_file()


def test_request_is_consumed_exactly_once(cfg):
    assert take_request(cfg) is False
    (cfg.data_dir / "run-requested").write_text("jetzt", encoding="utf-8")
    assert take_request(cfg) is True
    assert take_request(cfg) is False  # verbraucht, ein zweiter Lauf entsteht nicht


def test_rechecking_the_settings_does_not_write_anything(cfg):
    # validate() laeuft bei jedem Takt und jeder Anfrage. Der Schreibtest des Datenverzeichnisses
    # gehoert darum nicht hinein: zwei gleichzeitige Pruefungen kaemen sich ueber die Pruefdatei
    # in die Quere, und geschrieben wird umsonst.
    settings.save(cfg.data_dir, {"busy_retry_min": 7})
    before = {p.name for p in cfg.data_dir.rglob("*")}
    for _ in range(3):
        with_current_settings(cfg)
    assert {p.name for p in cfg.data_dir.rglob("*")} == before
