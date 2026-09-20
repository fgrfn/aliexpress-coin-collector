from datetime import date, datetime, timedelta

from aliexpress_coin_collector.scheduler import decide, plan_for
from aliexpress_coin_collector.store import Attempt

DAY = date(2026, 9, 21)


def att(ts, kind, outcome):
    return Attempt(ts=ts, kind=kind, outcome=outcome)


def test_plan_is_stable_and_inside_windows(cfg):
    p1, p2 = plan_for(DAY, cfg), plan_for(DAY, cfg)
    assert p1 == p2
    assert datetime.combine(DAY, cfg.morning_start) <= p1.morning_at <= datetime.combine(DAY, cfg.morning_end)
    assert datetime.combine(DAY, cfg.evening_start) <= p1.evening_at <= datetime.combine(DAY, cfg.evening_end)
    assert plan_for(DAY + timedelta(days=1), cfg) != p1


def test_nothing_before_morning(cfg):
    plan = plan_for(DAY, cfg)
    assert decide(plan.morning_at - timedelta(minutes=1), plan, [], cfg) is None


def test_morning_run_when_due(cfg):
    plan = plan_for(DAY, cfg)
    d = decide(plan.morning_at + timedelta(minutes=1), plan, [], cfg)
    assert d and d.kind == "morning" and not d.force


def test_no_run_after_success(cfg):
    plan = plan_for(DAY, cfg)
    now = plan.evening_at + timedelta(minutes=5)
    assert decide(now, plan, [att(plan.morning_at, "morning", "claimed")], cfg) is None
    assert decide(now, plan, [att(plan.morning_at, "manual", "already_done")], cfg) is None


def test_failed_morning_falls_back_to_evening_only_once(cfg):
    plan = plan_for(DAY, cfg)
    failed = att(plan.morning_at, "morning", "not_found")
    assert decide(plan.morning_at + timedelta(hours=1), plan, [failed], cfg) is None  # kein zweiter Morgenversuch
    d = decide(plan.evening_at + timedelta(minutes=1), plan, [failed], cfg)
    assert d and d.kind == "evening"
    evening_failed = att(plan.evening_at, "evening", "unconfirmed")
    assert decide(plan.evening_at + timedelta(hours=1), plan, [failed, evening_failed], cfg) is None


def test_busy_retries_and_finally_forces(cfg):
    plan = plan_for(DAY, cfg)
    t = plan.morning_at + timedelta(minutes=1)
    busy = att(t, "morning", "busy")
    assert decide(t + timedelta(minutes=cfg.busy_retry_min - 1), plan, [busy], cfg) is None  # Abkuehlzeit
    d = decide(t + timedelta(minutes=cfg.busy_retry_min + 1), plan, [busy], cfg)
    assert d and d.kind == "morning" and not d.force
    late = plan.morning_at + timedelta(minutes=cfg.busy_max_wait_min + 1)
    if late < plan.evening_at:
        d = decide(late, plan, [att(late - timedelta(minutes=30), "morning", "busy")], cfg)
        assert d and d.force


def test_evening_runs_even_if_morning_never_happened(cfg):
    plan = plan_for(DAY, cfg)
    d = decide(plan.evening_at + timedelta(minutes=1), plan, [], cfg)
    assert d and d.kind == "evening"
