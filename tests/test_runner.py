import random

from aliexpress_coin_collector.ocr import PageState, Word
from aliexpress_coin_collector.runner import Outcome, run_once


class FakeAdb:
    def __init__(self, connected=True, awake=False, by_launch=None):
        self.connected, self.awake = connected, awake
        self.calls = []
        self.tapped = None
        self.starts = 0
        self.by_launch = by_launch or {}

    def ensure_connected(self):
        return self.connected

    def is_awake(self):
        return self.awake

    def wake(self):
        self.calls.append("wake")

    def sleep_screen(self):
        self.calls.append("sleep")

    def force_stop(self, pkg):
        self.calls.append("force_stop")

    def start_url(self, url, pkg):
        self.starts += 1
        self.calls.append("start_url")

    def tap(self, x, y):
        self.tapped = (x, y)
        self.calls.append("tap")

    def screenshot(self):
        return b"png-tapped" if self.tapped else self.by_launch.get(self.starts, b"png-initial")


BTN = Word("Sammeln", 500, 900, 200, 50, 96.0)


def state(button=False, done=False, coins=None, logged_out=False):
    return PageState(BTN if button else None, done, coins, 1200, 1920, "", logged_out=logged_out)


def analyzer(before, after):
    return lambda png, cfg: before if png == b"png-initial" else after


def run(cfg, adb, before, after=None, **kw):
    return run_once(
        cfg,
        adb,
        analyze=analyzer(before, after or before),
        sleep=lambda s: None,
        rng=random.Random(1),
        confirm_timeout_s=1,
        **kw,
    )


def test_claim_success_by_coin_gain(cfg):
    adb = FakeAdb()
    res = run(cfg, adb, state(button=True, coins=0), state(coins=10))
    assert res.outcome == Outcome.CLAIMED and (res.coins_before, res.coins_after) == (0, 10)
    assert adb.tapped and 492 <= adb.tapped[0] <= 608
    assert adb.calls[0] == "wake" and adb.calls[-1] == "sleep"  # Bildschirm wird wieder ausgeschaltet


def test_claim_success_by_done_marker(cfg):
    res = run(cfg, FakeAdb(), state(button=True, coins=None), state(done=True))
    assert res.outcome == Outcome.CLAIMED


def test_already_done_does_not_tap(cfg):
    adb = FakeAdb()
    res = run(cfg, adb, state(done=True, coins=1234))
    assert res.outcome == Outcome.ALREADY_DONE and adb.tapped is None and res.coins_after == 1234


def test_button_still_visible_is_unconfirmed(cfg):
    res = run(cfg, FakeAdb(), state(button=True, coins=5), state(button=True, coins=5))
    assert res.outcome == Outcome.UNCONFIRMED and res.screenshot


def test_unknown_page_is_not_found_with_screenshot(cfg):
    res = run(cfg, FakeAdb(), state())
    assert res.outcome == Outcome.NOT_FOUND and res.screenshot == b"png-initial"


def test_unreachable(cfg):
    res = run(cfg, FakeAdb(connected=False), state())
    assert res.outcome == Outcome.UNREACHABLE and "tcpip" in res.message


def test_busy_skips_without_touching_the_device(cfg):
    adb = FakeAdb(awake=True)
    res = run(cfg, adb, state(button=True))
    assert res.outcome == Outcome.BUSY and adb.calls == []


def test_force_runs_when_awake_and_keeps_screen_on(cfg):
    adb = FakeAdb(awake=True)
    res = run(cfg, adb, state(button=True, coins=1), state(coins=11), force=True)
    assert res.outcome == Outcome.CLAIMED and "sleep" not in adb.calls


def test_second_launch_attempt_recovers_from_stuck_first_start(cfg):
    from dataclasses import replace

    cfg2 = replace(cfg, launch_retries=1)
    adb = FakeAdb(by_launch={1: b"png-stuck", 2: b"png-ok"})
    pages = {b"png-stuck": state(), b"png-ok": state(button=True, coins=0), b"png-tapped": state(coins=10)}
    res = run_once(
        cfg2, adb, analyze=lambda png, c: pages[png], sleep=lambda s: None, rng=random.Random(1), confirm_timeout_s=1
    )
    assert res.outcome == Outcome.CLAIMED and adb.starts == 2


def test_gives_up_after_all_launch_attempts(cfg):
    from dataclasses import replace

    adb = FakeAdb()
    res = run_once(
        replace(cfg, launch_retries=1), adb, analyze=lambda png, c: state(), sleep=lambda s: None, rng=random.Random(1)
    )
    assert res.outcome == Outcome.NOT_FOUND and adb.starts == 2


# -- Anmeldung abgelaufen ----------------------------------------------------------------------
#
# Bisher endete das als "not_found" -- eine Meldung, die einen taeglich zum Screenshot schickt,
# ohne zu sagen, was zu tun ist. Die Erkennung greift ausschliesslich dann, wenn ohnehin weder
# Button noch Erledigt-Zustand gefunden wurden; einen erfolgreichen Lauf kann sie nicht stoeren.


def test_a_login_prompt_is_named_instead_of_nichts_erkannt(cfg):
    res = run(cfg, FakeAdb(), state(logged_out=True))
    assert res.outcome == Outcome.LOGIN_REQUIRED
    assert "neu einloggen" in res.message
    assert res.screenshot, "der Screenshot gehoert dazu, damit man es selbst sehen kann"


def test_without_a_login_prompt_it_stays_nichts_erkannt(cfg):
    res = run(cfg, FakeAdb(), state())
    assert res.outcome == Outcome.NOT_FOUND


def test_a_login_marker_never_spoils_a_working_run(cfg):
    # Der entscheidende Punkt: wird der Button gefunden, ist der Marker gleichgueltig.
    adb = FakeAdb()
    res = run(cfg, adb, state(button=True, coins=0, logged_out=True), state(coins=10))
    assert res.outcome == Outcome.CLAIMED
    assert adb.tapped


# -- Erledigt-Zustand erst glauben, wenn er stehen bleibt -------------------------------------
#
# Der Marker steht oft schon auf der halb geladenen Seite, waehrend der Knopf noch fehlt. Wer
# beim ersten Bild aufhoert, meldet "heute schon eingecheckt" und laesst den Tag ausfallen.


class SequenceAdb(FakeAdb):
    """Liefert pro Screenshot ein anderes Bild, damit sich die Seite 'aufbauen' kann."""

    def __init__(self, frames):
        super().__init__()
        self.frames = list(frames)
        self.taken = 0

    def screenshot(self):
        if self.tapped:
            return b"png-tapped"
        frame = self.frames[min(self.taken, len(self.frames) - 1)]
        self.taken += 1
        return frame


def sequence_run(cfg, frames, after):
    """frames: Liste von PageStates, die nacheinander erkannt werden."""
    seen = {f"png-{i}": st for i, st in enumerate(frames)}
    adb = SequenceAdb([f"png-{i}".encode() for i in range(len(frames))])
    return (
        run_once(
            cfg,
            adb,
            analyze=lambda png, c: after if png == b"png-tapped" else seen[png.decode()],
            sleep=lambda s: None,
            rng=random.Random(1),
            confirm_timeout_s=1,
        ),
        adb,
    )


def test_a_button_appearing_late_is_still_collected(cfg):
    # Zwei Bilder nur mit Marker, dann taucht der Knopf auf.
    frames = [state(done=True), state(done=True), state(button=True, coins=10)]
    res, adb = sequence_run(cfg, frames, state(coins=25))
    assert res.outcome == Outcome.CLAIMED, "der Tag darf nicht wegen eines fruehen Markers ausfallen"
    assert adb.tapped


def test_a_marker_that_stays_is_believed(cfg):
    frames = [state(done=True)] * 5
    res, adb = sequence_run(cfg, frames, state(done=True))
    assert res.outcome == Outcome.ALREADY_DONE
    assert adb.tapped is None
