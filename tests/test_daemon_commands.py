"""Was der Dienst mit den Auftraegen der Oberflaeche macht.

Mit einer Attrappe statt eines Geraets. Geprueft wird vor allem, dass der Dienst nichts
verschluckt und an keinem Auftrag haengen bleibt -- er darf nie stehenbleiben, nur weil
ein Auftrag scheitert.
"""

from __future__ import annotations

from datetime import datetime

from aliexpress_coin_collector import commands, scheduler
from aliexpress_coin_collector.adb import AdbError
from aliexpress_coin_collector.store import Store

NOW = datetime(2026, 9, 21, 8, 0, 0)


class FakeAdb:
    def __init__(self, state="device", awake=False, connects=True, shot=b"png", raises=None):
        self._state = state
        self._awake = awake
        self._connects = connects
        self._shot = shot
        self._raises = raises
        self.calls: list[str] = []

    def state(self):
        self.calls.append("state")
        if self._raises == "state":
            raise AdbError("adb kaputt")
        return self._state

    def is_awake(self):
        self.calls.append("is_awake")
        if self._raises == "awake":
            raise AdbError("keine Antwort")
        return self._awake

    def ensure_connected(self):
        self.calls.append("ensure_connected")
        if self._connects:
            self._state = "device"
        return self._connects

    def screenshot(self):
        self.calls.append("screenshot")
        if self._raises == "screenshot":
            raise AdbError("kein Bild")
        return self._shot


# -- Zustandsmeldung -------------------------------------------------------------------------


def test_the_daemon_reports_what_it_sees(cfg):
    adb = FakeAdb(state="device", awake=True)
    assert scheduler.report_status(cfg, adb, "0.5.0") == "device"
    status = commands.read_status(cfg.data_dir)
    assert status.device_state == "device"
    assert status.screen_on is True
    assert status.version == "0.5.0"


def test_the_screen_is_not_asked_when_the_device_is_silent(cfg):
    # Ein shell-Aufruf auf ein nicht erreichbares Geraet laeuft nur in einen Timeout.
    adb = FakeAdb(state="offline")
    scheduler.report_status(cfg, adb, "0.5.0")
    assert "is_awake" not in adb.calls
    assert commands.read_status(cfg.data_dir).screen_on is None


def test_a_broken_adb_does_not_stop_the_daemon(cfg):
    adb = FakeAdb(raises="state")
    assert scheduler.report_status(cfg, adb, "0.5.0") == "offline"
    assert commands.read_status(cfg.data_dir).device_state == "offline"


# -- Auftraege abarbeiten --------------------------------------------------------------------


def test_reconnect_reports_success(cfg):
    store = Store(cfg.data_dir)
    commands.submit(cfg.data_dir, commands.RECONNECT, NOW)
    assert scheduler.process_commands(cfg, FakeAdb(state="offline", connects=True), store) == 1

    assert commands.pending(cfg.data_dir) == []
    done = commands.finished(cfg.data_dir)[0]
    assert done.status == commands.DONE
    assert "freigegeben" in done.message


def test_reconnect_explains_a_waiting_dialog(cfg):
    store = Store(cfg.data_dir)
    commands.submit(cfg.data_dir, commands.RECONNECT, NOW)
    scheduler.process_commands(cfg, FakeAdb(state="unauthorized", connects=False), store)
    done = commands.finished(cfg.data_dir)[0]
    assert done.status == commands.FAILED
    assert "Dialog" in done.message


def test_reconnect_names_the_usual_causes_when_there_is_no_connection(cfg):
    store = Store(cfg.data_dir)
    commands.submit(cfg.data_dir, commands.RECONNECT, NOW)
    scheduler.process_commands(cfg, FakeAdb(state="offline", connects=False), store)
    done = commands.finished(cfg.data_dir)[0]
    assert done.status == commands.FAILED
    assert "tcpip" in done.message


def test_a_screenshot_lands_in_the_shots_folder(cfg):
    store = Store(cfg.data_dir)
    commands.submit(cfg.data_dir, commands.SCREENSHOT, NOW)
    scheduler.process_commands(cfg, FakeAdb(shot=b"bilddaten"), store)

    shots = sorted((cfg.data_dir / "shots").glob("*.png"))
    assert len(shots) == 1
    assert shots[0].read_bytes() == b"bilddaten"
    assert shots[0].name.endswith("-manual.png")
    assert commands.finished(cfg.data_dir)[0].status == commands.DONE


def test_a_screenshot_needs_a_connection(cfg):
    store = Store(cfg.data_dir)
    commands.submit(cfg.data_dir, commands.SCREENSHOT, NOW)
    scheduler.process_commands(cfg, FakeAdb(state="offline", connects=False), store)
    assert not list((cfg.data_dir / "shots").glob("*.png"))
    assert commands.finished(cfg.data_dir)[0].status == commands.FAILED


def test_only_the_last_thirty_screenshots_are_kept(cfg):
    shots = cfg.data_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    for i in range(35):
        (shots / f"20260901-{i:06d}-not_found.png").write_bytes(b"alt")
    scheduler.save_screenshot(cfg, b"neu", NOW)
    assert len(list(shots.glob("*.png"))) == 30


def test_a_failing_command_does_not_block_the_next_one(cfg):
    store = Store(cfg.data_dir)
    commands.submit(cfg.data_dir, commands.SCREENSHOT, NOW)
    commands.submit(cfg.data_dir, commands.CHECK, NOW)
    assert scheduler.process_commands(cfg, FakeAdb(raises="screenshot"), store) == 2

    assert commands.pending(cfg.data_dir) == []
    by_name = {c.name: c for c in commands.finished(cfg.data_dir)}
    assert by_name[commands.SCREENSHOT].status == commands.FAILED
    assert by_name[commands.CHECK].status == commands.DONE


def test_nothing_to_do_is_not_an_error(cfg):
    assert scheduler.process_commands(cfg, FakeAdb(), Store(cfg.data_dir)) == 0


def test_a_run_command_writes_an_attempt_to_the_database(cfg, monkeypatch):
    store = Store(cfg.data_dir)
    seen = {}

    def fake_run_once(config, adb, force=False):
        seen["force"] = force
        from aliexpress_coin_collector.runner import Outcome, RunResult

        return RunResult(outcome=Outcome.CLAIMED, coins_before=10, coins_after=17, duration_s=2.0, message="ok")

    monkeypatch.setattr(scheduler, "run_once", fake_run_once)
    monkeypatch.setattr(scheduler.notify, "send_discord", lambda *a, **k: None)

    commands.submit(cfg.data_dir, commands.RUN, NOW)
    scheduler.process_commands(cfg, FakeAdb(), store)

    assert seen["force"] is True  # ein Lauf von Hand ignoriert das Zeitfenster
    rows = store.recent(10)
    assert len(rows) == 1
    assert rows[0].kind == "manual"
    assert rows[0].outcome == "claimed"
