"""Der Stillstandswächter: Erfolg gemeldet, aber nichts gesammelt.

Anlass aus dem Betrieb: die Erkennung meldete tagelang fälschlich „heute schon eingecheckt".
Die Erfolgsquote stand auf 100 Prozent, während der Münzstand unverändert bei 10 blieb, und
niemandem fiel es auf. Dieser Wächter fragt nicht, ob der Schritt geklappt hat, sondern ob das
Ergebnis eingetreten ist — und fängt damit auch die nächste Ursache, die noch niemand kennt.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from aliexpress_coin_collector import scheduler, stats
from aliexpress_coin_collector.store import Attempt

NOW = datetime(2026, 9, 21, 8, 0)


def day(offset: int, coins: int | None, outcome: str = "already_done") -> Attempt:
    return Attempt(ts=NOW - timedelta(days=offset), kind="morning", outcome=outcome, coins_after=coins)


# -- Wann er anschlaegt ---------------------------------------------------------------------------


def test_the_real_case_is_caught():
    # Genau das Muster aus der Datenbank: "erledigt" bei stehendem Stand.
    stall = stats.stalled_since([day(3, 10), day(2, 10), day(1, 10), day(0, 10)])
    assert stall.stalled is True
    assert (stall.days, stall.coins) == (3, 10)


def test_a_rising_balance_is_never_a_stall():
    assert stats.stalled_since([day(3, 10), day(2, 25), day(1, 40), day(0, 55)]).stalled is False


def test_two_flat_days_are_not_enough():
    # Ein einzelner Ausrutscher soll niemanden wecken.
    assert stats.stalled_since([day(2, 10), day(1, 10), day(0, 10)]).stalled is False


def test_spending_coins_is_not_a_stall():
    # Wer Muenzen ausgibt, sammelt danach weiter -- der Anstieg beendet die Serie.
    assert stats.stalled_since([day(3, 25), day(2, 25), day(1, 10), day(0, 25)]).stalled is False


def test_days_without_success_do_not_count():
    # An einem Tag ohne Erfolg ist ein gleichbleibender Stand die erwartete Folge.
    attempts = [day(3, 10), day(2, 10, outcome="unreachable"), day(1, 10, outcome="not_found"), day(0, 10)]
    assert stats.stalled_since(attempts).stalled is False


def test_an_unreadable_balance_is_not_the_same_as_unchanged():
    attempts = [day(3, 10), day(2, None), day(1, None), day(0, 10)]
    assert stats.stalled_since(attempts).stalled is False


def test_too_little_history_is_not_a_stall():
    assert stats.stalled_since([]).stalled is False
    assert stats.stalled_since([day(0, 10)]).stalled is False


def test_the_newest_gain_ends_the_series():
    # Drei flache Tage, dann endlich wieder Zuwachs.
    assert stats.stalled_since([day(3, 10), day(2, 10), day(1, 10), day(0, 25)]).stalled is False


# -- Gemeldet wird je Vorfall einmal --------------------------------------------------------------


def stalled(coins: int = 10) -> stats.Stall:
    return stats.Stall(days=4, coins=coins)


def test_the_first_time_it_is_reported(cfg):
    assert scheduler.stall_due(cfg.data_dir, stalled()) is True


def test_the_same_stall_is_not_reported_twice(cfg):
    scheduler._mark_stall(cfg.data_dir, stalled())
    assert scheduler.stall_due(cfg.data_dir, stalled()) is False


def test_a_new_stall_at_a_different_level_is_reported_again(cfg):
    scheduler._mark_stall(cfg.data_dir, stalled(10))
    assert scheduler.stall_due(cfg.data_dir, stalled(25)) is True


def test_nothing_is_reported_without_a_stall(cfg):
    assert scheduler.stall_due(cfg.data_dir, stats.Stall(days=1, coins=None)) is False


# -- Der Weg nach draussen -------------------------------------------------------------------------


def test_the_message_names_the_days_and_the_balance():
    message = scheduler.stall_message(stalled(1275))
    assert "nichts gesammelt" in message.title
    assert message.tone == "bad"
    values = {f.name: f.value for f in message.fields}
    assert values["Tage ohne Zuwachs"] == "4"
    assert values["Münzstand"] == "1.275"


def test_the_daemon_sends_it_once(cfg, monkeypatch):
    from aliexpress_coin_collector.store import Store
    from tests.test_daemon_commands import BEFORE_WINDOW, FakeAdb

    sent = []
    monkeypatch.setattr(scheduler.notify, "send", lambda hook, message, *a, **k: sent.append(message))
    store = Store(cfg.data_dir)
    for a in (day(3, 10), day(2, 10), day(1, 10), day(0, 10)):
        store.add(a)

    for _ in range(2):
        scheduler.tick(cfg, FakeAdb(), store, scheduler.Reconnect(), now=BEFORE_WINDOW)
    assert len(sent) == 1, "je Vorfall genau eine Meldung, auch ueber viele Takte"
    assert "nichts gesammelt" in sent[0].title


def test_the_watchdog_can_be_switched_off(cfg, monkeypatch):
    from dataclasses import replace

    from aliexpress_coin_collector.store import Store
    from tests.test_daemon_commands import BEFORE_WINDOW, FakeAdb

    sent = []
    monkeypatch.setattr(scheduler.notify, "send", lambda hook, message, *a, **k: sent.append(message))
    quiet = replace(cfg, notify_on_stall=False, notify_on_offline=False)
    store = Store(quiet.data_dir)
    for a in (day(3, 10), day(2, 10), day(1, 10), day(0, 10)):
        store.add(a)

    scheduler.tick(quiet, FakeAdb(), store, scheduler.Reconnect(), now=BEFORE_WINDOW)
    assert sent == []
