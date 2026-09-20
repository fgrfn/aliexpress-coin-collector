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
