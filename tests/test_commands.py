"""Befehlsablage zwischen Oberflaeche und Dienst, und die Zustandsmeldung.

Kein Geraet noetig: alles laeuft ueber Dateien in einem Verzeichnis.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aliexpress_coin_collector import commands

NOW = datetime(2026, 9, 21, 8, 0, 0)


# -- Ablegen ---------------------------------------------------------------------------------


def test_nothing_waits_at_the_start(tmp_path):
    assert commands.pending(tmp_path) == []
    assert commands.recent(tmp_path) == []


def test_a_command_can_be_submitted_and_is_then_waiting(tmp_path):
    cmd = commands.submit(tmp_path, commands.RECONNECT, NOW)
    assert cmd.status == commands.WAITING
    waiting = commands.pending(tmp_path)
    assert [c.name for c in waiting] == [commands.RECONNECT]
    assert waiting[0].ident == cmd.ident
    assert waiting[0].label == "Neu verbinden"


def test_an_unknown_command_is_refused(tmp_path):
    with pytest.raises(commands.CommandError, match="Unbekannt"):
        commands.submit(tmp_path, "rm -rf /", NOW)
    assert commands.pending(tmp_path) == []


def test_the_same_command_is_not_queued_twice(tmp_path):
    commands.submit(tmp_path, commands.SCREENSHOT, NOW)
    with pytest.raises(commands.CommandError, match="wartet bereits"):
        commands.submit(tmp_path, commands.SCREENSHOT, NOW)
    assert len(commands.pending(tmp_path)) == 1


def test_different_commands_may_wait_side_by_side(tmp_path):
    commands.submit(tmp_path, commands.SCREENSHOT, NOW)
    commands.submit(tmp_path, commands.RECONNECT, NOW)
    assert len(commands.pending(tmp_path)) == 2


def test_a_pile_of_waiting_commands_is_refused(tmp_path):
    # Mehr offene Auftraege als Befehlsarten kann es nur geben, wenn der Dienst steht.
    for i in range(commands.MAX_WAITING):
        cmd = commands.Command(ident=f"x{i}", name=commands.CHECK, requested_at=NOW)
        (tmp_path / commands.QUEUE_DIR).mkdir(parents=True, exist_ok=True)
        (tmp_path / commands.QUEUE_DIR / f"x{i}.json").write_text(commands._to_json(cmd), encoding="utf-8")
    with pytest.raises(commands.CommandError, match="zu viele"):
        commands.submit(tmp_path, commands.RECONNECT, NOW)


def test_ids_stay_unique_within_the_same_second(tmp_path):
    a = commands.submit(tmp_path, commands.RECONNECT, NOW)
    commands.finish(tmp_path, a, True, "", NOW)
    b = commands.submit(tmp_path, commands.RECONNECT, NOW)
    assert a.ident != b.ident


# -- Abarbeiten ------------------------------------------------------------------------------


def test_the_daemon_sees_what_waits_oldest_first(tmp_path):
    commands.submit(tmp_path, commands.RECONNECT, NOW)
    commands.submit(tmp_path, commands.SCREENSHOT, NOW + timedelta(seconds=5))
    assert [c.name for c in commands.take(tmp_path)] == [commands.RECONNECT, commands.SCREENSHOT]


def test_finishing_moves_it_out_of_the_queue_and_keeps_the_result(tmp_path):
    cmd = commands.submit(tmp_path, commands.RECONNECT, NOW)
    commands.finish(tmp_path, cmd, True, "Verbunden und freigegeben.", NOW + timedelta(seconds=20))

    assert commands.pending(tmp_path) == []
    done = commands.finished(tmp_path)
    assert len(done) == 1
    assert done[0].status == commands.DONE
    assert done[0].message == "Verbunden und freigegeben."
    assert done[0].finished_at == NOW + timedelta(seconds=20)


def test_a_failure_is_recorded_as_such(tmp_path):
    cmd = commands.submit(tmp_path, commands.RECONNECT, NOW)
    commands.finish(tmp_path, cmd, False, "Keine Verbindung.", NOW)
    done = commands.finished(tmp_path)[0]
    assert done.status == commands.FAILED
    assert done.tone == "bad"


def test_only_the_last_few_finished_commands_are_kept(tmp_path):
    for i in range(commands.KEEP_DONE + 8):
        cmd = commands.submit(tmp_path, commands.CHECK, NOW + timedelta(seconds=i))
        commands.finish(tmp_path, cmd, True, "", NOW + timedelta(seconds=i))
    assert len(commands.finished(tmp_path, limit=999)) == commands.KEEP_DONE


def test_recent_puts_waiting_before_finished(tmp_path):
    old = commands.submit(tmp_path, commands.CHECK, NOW)
    commands.finish(tmp_path, old, True, "", NOW)
    commands.submit(tmp_path, commands.RECONNECT, NOW + timedelta(minutes=1))
    rows = commands.recent(tmp_path)
    assert rows[0].status == commands.WAITING
    assert rows[1].status == commands.DONE


def test_a_broken_file_is_skipped_not_fatal(tmp_path):
    commands.submit(tmp_path, commands.RECONNECT, NOW)
    (tmp_path / commands.QUEUE_DIR / "kaputt.json").write_text("{ das ist kein JSON", encoding="utf-8")
    assert [c.name for c in commands.pending(tmp_path)] == [commands.RECONNECT]


# -- Zustandsmeldung -------------------------------------------------------------------------


def test_without_a_report_there_is_no_status(tmp_path):
    assert commands.read_status(tmp_path) is None


def test_status_round_trip(tmp_path):
    commands.write_status(tmp_path, "0.5.0", "device", False, NOW)
    status = commands.read_status(tmp_path)
    assert status is not None
    assert status.version == "0.5.0"
    assert status.device_state == "device"
    assert status.screen_on is False
    assert status.device_label == "verbunden und freigegeben"
    assert status.device_tone == "ok"


def test_the_states_are_told_apart(tmp_path):
    for state, tone, word in (
        ("device", "ok", "freigegeben"),
        ("unauthorized", "warn", "nicht freigegeben"),
        ("offline", "bad", "nicht erreichbar"),
        ("irgendwas", "bad", "unbekannt"),
    ):
        commands.write_status(tmp_path, "0.5.0", state, None, NOW)
        status = commands.read_status(tmp_path)
        assert status.device_tone == tone, state
        assert word in status.device_label, state


def test_the_status_file_never_holds_the_device_address(tmp_path):
    commands.write_status(tmp_path, "0.5.0", "device", True, NOW)
    raw = (tmp_path / commands.STATUS_FILE).read_text(encoding="utf-8")
    assert "5555" not in raw
    assert "." not in raw.split('"device_state"')[1][:40]  # keine IP im Zustandsfeld


def test_a_broken_status_file_reads_as_no_status(tmp_path):
    (tmp_path / commands.STATUS_FILE).write_text("kaputt", encoding="utf-8")
    assert commands.read_status(tmp_path) is None


# -- Adresse verkuerzen ----------------------------------------------------------------------


def test_the_address_is_shortened_for_display():
    assert commands.mask_serial("10.10.30.169:5555") == "10.10.30.xxx:5555"
    assert commands.mask_serial("192.168.1.50:5555") == "192.168.1.xxx:5555"
    assert commands.mask_serial("") == "nicht eingetragen"


def test_a_usb_serial_is_shortened_too():
    masked = commands.mask_serial("ABCDEF123456")
    assert masked != "ABCDEF123456"
    assert "123456" not in masked


def test_a_hostname_keeps_its_port_but_loses_its_tail():
    masked = commands.mask_serial("tablet.fritz.box:5555")
    assert masked.endswith(":5555")
    assert "tablet.fritz.box" not in masked
