"""Was der Dienst mit den Auftraegen der Oberflaeche macht.

Mit einer Attrappe statt eines Geraets. Geprueft wird vor allem, dass der Dienst nichts
verschluckt und an keinem Auftrag haengen bleibt -- er darf nie stehenbleiben, nur weil
ein Auftrag scheitert.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

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
    monkeypatch.setattr(scheduler.notify, "send", lambda *a, **k: None)

    commands.submit(cfg.data_dir, commands.RUN, NOW)
    scheduler.process_commands(cfg, FakeAdb(), store)

    assert seen["force"] is True  # ein Lauf von Hand ignoriert das Zeitfenster
    rows = store.recent(10)
    assert len(rows) == 1
    assert rows[0].kind == "manual"
    assert rows[0].outcome == "claimed"


# -- Selbstheilung der Verbindung --------------------------------------------------------------
#
# Anlass: die Seite zeigte dauerhaft "nicht erreichbar", obwohl das Geraet im Netz war.
# 'adb get-state' fragt nur den lokalen adb-Server und baut eine abgerissene TCP-Verbindung
# nie von allein wieder auf.


def test_an_offline_device_gets_a_reconnect_attempt(cfg):
    adb = FakeAdb(state="offline", connects=True)
    assert scheduler.report_status(cfg, adb, "0.9.0", scheduler.Reconnect(), NOW) == "device"
    assert "ensure_connected" in adb.calls


def test_a_connected_device_is_left_alone(cfg):
    adb = FakeAdb(state="device")
    scheduler.report_status(cfg, adb, "0.9.0", scheduler.Reconnect(), NOW)
    assert "ensure_connected" not in adb.calls, "ein laufendes Geraet nicht ohne Not anfassen"


def test_an_unauthorized_device_is_not_hammered(cfg):
    # Die Verbindung steht bereits, auf dem Geraet wartet ein Dialog. Ein weiteres connect
    # aendert daran nichts und kostet nur Zeit.
    adb = FakeAdb(state="unauthorized")
    assert scheduler.report_status(cfg, adb, "0.9.0", scheduler.Reconnect(), NOW) == "unauthorized"
    assert "ensure_connected" not in adb.calls


def test_a_failed_attempt_is_not_repeated_on_the_next_tick(cfg):
    # Ein connect laeuft bei totem Geraet in einen Timeout von 15 s. Bei einem Takt von 30 s
    # waere das die halbe Zeit des Dienstes.
    adb = FakeAdb(state="offline", connects=False)
    state = scheduler.Reconnect()
    scheduler.report_status(cfg, adb, "0.9.0", state, NOW)
    scheduler.report_status(cfg, adb, "0.9.0", state, NOW + timedelta(seconds=30))
    assert adb.calls.count("ensure_connected") == 1


def test_the_gap_between_attempts_grows():
    # Reine Logik, ohne ADB: nach dem n-ten Fehlversuch wird 1, 2, 5, 10 und dann immer
    # 30 Minuten gewartet. Der allererste Versuch kommt sofort.
    state = scheduler.Reconnect()
    assert state.due(NOW)

    for failures, wait in ((1, 1), (2, 2), (3, 5), (4, 10), (5, 30), (9, 30)):
        state.failures, state.last_try = failures, NOW
        assert not state.due(NOW + timedelta(minutes=wait - 1)), f"nach {failures} Fehlversuchen zu frueh"
        assert state.due(NOW + timedelta(minutes=wait)), f"nach {failures} Fehlversuchen zu spaet"


def test_a_device_that_comes_back_may_retry_at_once(cfg):
    state = scheduler.Reconnect()
    dead = FakeAdb(state="offline", connects=False)
    scheduler.report_status(cfg, dead, "0.9.0", state, NOW)
    assert state.failures == 1

    # Geraet ist von allein wieder da -- der naechste Abriss darf nicht erst warten.
    alive = FakeAdb(state="device")
    scheduler.report_status(cfg, alive, "0.9.0", state, NOW + timedelta(seconds=30))
    assert state.failures == 0
    assert state.due(NOW + timedelta(seconds=31))


def test_a_reconnect_that_works_is_written_to_the_status(cfg):
    adb = FakeAdb(state="offline", awake=True, connects=True)
    scheduler.report_status(cfg, adb, "0.9.0", scheduler.Reconnect(), NOW)
    assert commands.read_status(cfg.data_dir).device_state == "device"


# -- Reihenfolge im Takt -----------------------------------------------------------------------


def test_the_status_is_reported_again_after_a_command(cfg):
    # Anlass: "Neu verbinden" hat funktioniert, aber die Seite zeigte bis zum naechsten Takt
    # weiter "nicht erreichbar" -- gemeldet wurde vor dem Auftrag, nicht danach.
    adb = FakeAdb(state="offline", connects=True)
    commands.submit(cfg.data_dir, commands.RECONNECT)

    scheduler.tick(cfg, adb, Store(cfg.data_dir), scheduler.Reconnect(), "0.9.0", now=NOW)

    assert commands.read_status(cfg.data_dir).device_state == "device"


def test_without_a_command_the_status_is_written_only_once(cfg):
    # Der zusaetzliche Blick aufs Geraet soll nur anfallen, wenn wirklich etwas passiert ist.
    adb = FakeAdb(state="device")
    scheduler.tick(cfg, adb, Store(cfg.data_dir), scheduler.Reconnect(), "0.9.0", now=NOW)
    assert adb.calls.count("state") == 1


def test_a_reconnect_that_explodes_still_counts_as_a_failure(cfg):
    # Sonst bliebe der Backoff wirkungslos und der Dienst versuchte es bei jedem Takt neu.
    class Exploding(FakeAdb):
        def ensure_connected(self):
            self.calls.append("ensure_connected")
            raise AdbError("adb-Server weg")

    adb = Exploding(state="offline")
    state = scheduler.Reconnect()
    scheduler.report_status(cfg, adb, "0.9.0", state, NOW)
    assert state.failures == 1
    scheduler.report_status(cfg, adb, "0.9.0", state, NOW + timedelta(seconds=30))
    assert adb.calls.count("ensure_connected") == 1


# -- Meldung bei laengerem Ausfall ---------------------------------------------------------------


def test_a_short_outage_is_not_reported():
    # Ein Handy im Ruhezustand, ein Router, der neu startet: das ist kein Vorfall.
    outage = scheduler.Outage()
    assert outage.note(False, NOW, after_min=30) == ""
    assert outage.note(False, NOW + timedelta(minutes=29), after_min=30) == ""
    assert outage.note(True, NOW + timedelta(minutes=29), after_min=30) == "", "keine Entwarnung ohne Stoerung"


def test_a_long_outage_is_reported_once():
    outage = scheduler.Outage()
    outage.note(False, NOW, after_min=30)
    assert outage.note(False, NOW + timedelta(minutes=30), after_min=30) == "down"
    for extra in (31, 60, 600):
        assert outage.note(False, NOW + timedelta(minutes=extra), after_min=30) == "", "nur eine Meldung je Ausfall"


def test_the_all_clear_follows_a_reported_outage():
    outage = scheduler.Outage()
    outage.note(False, NOW, after_min=30)
    outage.note(False, NOW + timedelta(minutes=30), after_min=30)
    assert outage.minutes(NOW + timedelta(minutes=45)) == 45
    assert outage.note(True, NOW + timedelta(minutes=45), after_min=30) == "up"
    assert outage.note(True, NOW + timedelta(minutes=46), after_min=30) == "", "nur eine Entwarnung"


def test_a_second_outage_is_reported_again():
    outage = scheduler.Outage()
    outage.note(False, NOW, after_min=30)
    outage.note(False, NOW + timedelta(minutes=30), after_min=30)
    outage.note(True, NOW + timedelta(minutes=45), after_min=30)

    later = NOW + timedelta(hours=5)
    outage.note(False, later, after_min=30)
    assert outage.note(False, later + timedelta(minutes=30), after_min=30) == "down"


# Vor dem Morgenfenster (fruehestens 07:00): sonst startet der Takt einen echten Lauf, und
# dessen Fehlschlagmeldung waere in der Zaehlung nicht von der Stoerungsmeldung zu trennen.
BEFORE_WINDOW = datetime(2026, 9, 21, 4, 0, 0)


def outage_ticks(cfg, monkeypatch, minutes=(0, 30)):
    """Laesst den Dienst takten, waehrend das Geraet weg ist. Gibt die Meldungen zurueck."""
    sent = []
    monkeypatch.setattr(scheduler.notify, "send", lambda hook, message, *a, **k: sent.append(message))
    outage, reconnect, store = scheduler.Outage(), scheduler.Reconnect(), Store(cfg.data_dir)
    for offset in minutes:
        adb = FakeAdb(state="offline", connects=False)
        scheduler.tick(
            cfg, adb, store, reconnect, "0.9.0", now=BEFORE_WINDOW + timedelta(minutes=offset), outage=outage
        )
    return sent


def test_the_daemon_reports_an_outage_over_discord(cfg, monkeypatch):
    assert outage_ticks(cfg, monkeypatch, minutes=(0,)) == [], "erst nach der Wartezeit"

    sent = outage_ticks(cfg, monkeypatch, minutes=(0, 30))
    assert len(sent) == 1
    assert "nicht erreichbar" in sent[0].title


def test_the_outage_message_can_be_switched_off(cfg, monkeypatch):
    from dataclasses import replace

    assert outage_ticks(replace(cfg, notify_on_offline=False), monkeypatch) == []


def test_the_waiting_time_comes_from_the_settings(cfg, monkeypatch):
    from dataclasses import replace

    patient = replace(cfg, offline_alert_min=120)
    assert outage_ticks(patient, monkeypatch, minutes=(0, 30, 60)) == []
    assert len(outage_ticks(patient, monkeypatch, minutes=(0, 120))) == 1


# -- Wochenrueckblick: wann er rausgeht ----------------------------------------------------------
#
# Der heikle Teil ist nicht die Rechnung, sondern der Zeitpunkt: genau einmal je Woche, ueber
# Neustarts hinweg, und nicht direkt nach der Installation auf eine leere Woche.

MONDAY = date(2026, 9, 21)  # ein Montag
TUESDAY = MONDAY + timedelta(days=1)


def test_the_very_first_time_nothing_is_sent(cfg):
    # Sonst kaeme direkt nach dem Aufsetzen ein Rueckblick auf eine Woche ohne Daten.
    assert scheduler.digest_due(cfg.data_dir, MONDAY, 0, 9, 10) is False
    assert (cfg.data_dir / scheduler.DIGEST_FILE).is_file(), "der Stichtag wird trotzdem vermerkt"


def test_the_following_week_it_goes_out(cfg):
    scheduler.digest_due(cfg.data_dir, MONDAY, 0, 9, 10)
    assert scheduler.digest_due(cfg.data_dir, MONDAY + timedelta(days=7), 0, 9, 10) is True


def test_not_twice_on_the_same_day(cfg):
    scheduler.digest_due(cfg.data_dir, MONDAY, 0, 9, 10)
    later = MONDAY + timedelta(days=7)
    assert scheduler.digest_due(cfg.data_dir, later, 0, 9, 10) is True
    scheduler._mark_digest(cfg.data_dir, later)
    assert scheduler.digest_due(cfg.data_dir, later, 0, 9, 11) is False


def test_not_on_another_weekday_and_not_too_early(cfg):
    scheduler._mark_digest(cfg.data_dir, MONDAY - timedelta(days=7))
    assert scheduler.digest_due(cfg.data_dir, TUESDAY, 0, 9, 10) is False, "nur montags"
    assert scheduler.digest_due(cfg.data_dir, MONDAY, 0, 9, 8) is False, "erst ab 9 Uhr"
    assert scheduler.digest_due(cfg.data_dir, MONDAY, 0, 9, 9) is True


def test_a_broken_marker_does_not_stop_the_service(cfg):
    (cfg.data_dir / scheduler.DIGEST_FILE).write_text("voellig kaputt", encoding="utf-8")
    assert scheduler.digest_due(cfg.data_dir, MONDAY, 0, 9, 10) is False
    # Und der Merker steht danach wieder brauchbar da.
    assert scheduler.digest_due(cfg.data_dir, MONDAY + timedelta(days=7), 0, 9, 10) is True


def test_the_digest_can_be_switched_off(cfg, monkeypatch):
    from dataclasses import replace

    sent = []
    monkeypatch.setattr(scheduler.notify, "send", lambda hook, message, *a, **k: sent.append(message))
    monkeypatch.setattr(scheduler, "digest_due", lambda *a, **k: True)
    quiet = replace(cfg, notify_weekly=False, notify_on_offline=False)
    scheduler.tick(quiet, FakeAdb(), Store(quiet.data_dir), scheduler.Reconnect(), now=BEFORE_WINDOW)
    assert sent == []


def test_the_digest_goes_out_through_the_tick(cfg, monkeypatch):
    sent = []
    monkeypatch.setattr(scheduler.notify, "send", lambda hook, message, *a, **k: sent.append(message))
    monkeypatch.setattr(scheduler, "digest_due", lambda *a, **k: True)
    scheduler.tick(cfg, FakeAdb(), Store(cfg.data_dir), scheduler.Reconnect(), now=BEFORE_WINDOW)
    assert len(sent) == 1
    assert "Woche" in sent[0].title


# --- Auftraege waehrend der Pause ------------------------------------------------------------
#
# Ohne das laege ein Screenshot aus der Oberflaeche bis zu 30 Sekunden herum, bevor der Dienst
# ihn ueberhaupt bemerkt. Geprueft wird mit einer gestellten Uhr, ohne echtes Warten.


class Uhr:
    """Eine Uhr, die nur springt, wenn geschlafen wird."""

    def __init__(self):
        self.jetzt = 0.0
        self.geschlafen: list[float] = []

    def sleep(self, s):
        self.geschlafen.append(s)
        self.jetzt += s

    def monotonic(self):
        return self.jetzt


def warte(cfg, adb, sekunden, uhr, scheibe=1):
    return scheduler.wait_for_commands(
        cfg,
        adb,
        Store(cfg.data_dir),
        scheduler.Reconnect(),
        "",
        sekunden,
        slice_s=scheibe,
        sleep=uhr.sleep,
        monotonic=uhr.monotonic,
    )


def test_the_pause_is_slept_in_slices_not_in_one_go(cfg):
    uhr = Uhr()
    warte(cfg, FakeAdb(), 30, uhr)
    assert len(uhr.geschlafen) == 30
    assert uhr.jetzt == 30


def test_a_job_dropped_during_the_pause_is_picked_up_within_a_slice(cfg):
    commands.submit(cfg.data_dir, commands.CHECK)
    uhr = Uhr()
    assert warte(cfg, FakeAdb(), 30, uhr) == 1
    # Nach der ersten Scheibe war er weg, nicht erst am Ende der Pause.
    assert commands.pending(cfg.data_dir) == []


def test_without_a_job_the_pause_passes_quietly(cfg):
    assert warte(cfg, FakeAdb(), 5, Uhr()) == 0


def test_a_short_pause_is_not_overslept(cfg):
    """Sonst liefe der Takt aus dem Tritt, wenn die Scheibe groesser ist als der Rest."""
    uhr = Uhr()
    warte(cfg, FakeAdb(), 0.4, uhr)
    assert uhr.geschlafen == [0.4]


# -- Zusatzaufgaben ----------------------------------------------------------------------------
#
# Bis 0.19.2 liefen sie nur von Hand ueber die Kommandozeile. Jetzt haengt der Dienst sie an
# jeden erfolgreichen Lauf und nimmt sie ausserdem als Auftrag der Oberflaeche entgegen.


class ExtrasAdb(FakeAdb):
    """Wie FakeAdb, aber mit dem, was ein Ausflug ausserdem anfasst."""

    def wake(self):
        self.calls.append("wake")
        self._awake = True

    def sleep_screen(self):
        self.calls.append("sleep_screen")
        self._awake = False

    def force_stop(self, package):
        self.calls.append("force_stop")

    def start_url(self, url, package):
        self.calls.append("start_url")


def fake_explore(result):
    """Ein explore, das ohne Geraet und ohne Tesseract ein festes Ergebnis liefert."""

    def _explore(adb, config, eyes, **kw):
        return result

    return _explore


def geglueckter_ausflug():
    from aliexpress_coin_collector import extras

    return extras.ExtrasResult(
        entered=True,
        verdicts=[
            extras.Verdict(extras.Card("Super Rabatte anzeigen", 600, 500), extras.TAKE, "erlaubt durch 'rabatt'"),
            extras.Verdict(extras.Card("Tagesquiz", 600, 800), extras.BLOCKED, "gesperrt durch 'quiz'"),
        ],
        runs=[extras.TaskRun(text="Super Rabatte anzeigen", ok=True, note="erledigt")],
    )


def test_collect_extras_schreibt_die_aufgaben_weg(cfg, monkeypatch):
    store = Store(cfg.data_dir)
    monkeypatch.setattr(scheduler.extras, "explore", fake_explore(geglueckter_ausflug()))
    monkeypatch.setattr(scheduler.ocr, "Sight", lambda config: object())

    result = scheduler.collect_extras(cfg, ExtrasAdb(), store, now=NOW)

    assert result.entered
    zeilen = store.tasks_between(NOW - timedelta(hours=1), NOW + timedelta(hours=1))
    assert [z.text for z in zeilen] == ["Super Rabatte anzeigen", "Tagesquiz"]
    assert zeilen[0].done and not zeilen[1].done


def test_collect_extras_weckt_und_legt_wieder_schlafen(cfg, monkeypatch):
    monkeypatch.setattr(scheduler.extras, "explore", fake_explore(geglueckter_ausflug()))
    monkeypatch.setattr(scheduler.ocr, "Sight", lambda config: object())
    adb = ExtrasAdb(awake=False)

    scheduler.collect_extras(cfg, adb, Store(cfg.data_dir), now=NOW)

    assert "wake" in adb.calls and "sleep_screen" in adb.calls


def test_collect_extras_laesst_einen_wachen_bildschirm_an(cfg, monkeypatch):
    """Wer gerade am Geraet sitzt, soll nicht mitten im Tippen den Bildschirm verlieren."""
    monkeypatch.setattr(scheduler.extras, "explore", fake_explore(geglueckter_ausflug()))
    monkeypatch.setattr(scheduler.ocr, "Sight", lambda config: object())
    adb = ExtrasAdb(awake=True)

    scheduler.collect_extras(cfg, adb, Store(cfg.data_dir), now=NOW)

    assert "wake" not in adb.calls and "sleep_screen" not in adb.calls


def test_collect_extras_haelt_einen_fehler_aus(cfg, monkeypatch):
    """Ein Ausflug darf den Dienst nie anhalten -- er ist die Zugabe, nicht die Hauptsache."""

    def kaputt(*a, **kw):
        raise RuntimeError("irgendwas")

    monkeypatch.setattr(scheduler.extras, "explore", kaputt)
    monkeypatch.setattr(scheduler.ocr, "Sight", lambda config: object())
    store = Store(cfg.data_dir)

    result = scheduler.collect_extras(cfg, ExtrasAdb(), store, now=NOW)

    assert not result.entered
    assert "irgendwas" in result.note
    assert store.tasks_between(NOW - timedelta(days=1), NOW + timedelta(days=1)) == []


def test_collect_extras_ohne_verbindung(cfg, monkeypatch):
    monkeypatch.setattr(scheduler.ocr, "Sight", lambda config: object())

    result = scheduler.collect_extras(cfg, ExtrasAdb(connects=False), Store(cfg.data_dir), now=NOW)

    assert not result.entered
    assert result.note == "Geraet nicht erreichbar"


def test_der_auftrag_extras_wird_ausgefuehrt(cfg, monkeypatch):
    store = Store(cfg.data_dir)
    monkeypatch.setattr(scheduler.extras, "explore", fake_explore(geglueckter_ausflug()))
    monkeypatch.setattr(scheduler.ocr, "Sight", lambda config: object())

    commands.submit(cfg.data_dir, commands.EXTRAS, NOW)
    assert scheduler.process_commands(cfg, ExtrasAdb(), store) == 1

    erledigt = [c for c in commands.recent(cfg.data_dir) if c.name == commands.EXTRAS]
    assert erledigt and erledigt[0].status == commands.DONE
    assert "1 von 1 erledigt" in erledigt[0].message


def tick_mit_lauf(cfg, monkeypatch, ausflug=True):
    """Einen Takt fahren, in dem ein geplanter Lauf faellig ist und glueckt.

    Gibt zurueck, wie oft der Ausflug zu den Zusatzaufgaben stattgefunden hat.
    """
    from dataclasses import replace

    from aliexpress_coin_collector.runner import Outcome, RunResult

    ausfluege = []
    monkeypatch.setattr(scheduler.notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(
        scheduler,
        "run_once",
        lambda config, adb, force=False: RunResult(
            outcome=Outcome.CLAIMED, message="ok", coins_before=10, coins_after=17
        ),
    )
    monkeypatch.setattr(scheduler, "collect_extras", lambda *a, **k: ausfluege.append(True))

    cfg = replace(cfg, extras_after_run=ausflug)
    plan = scheduler.plan_for(NOW.date(), cfg)
    scheduler.tick(cfg, ExtrasAdb(), Store(cfg.data_dir), scheduler.Reconnect(), "0.9.0", now=plan.morning_at)
    return len(ausfluege)


def test_der_dienst_nimmt_die_zusatzaufgaben_nach_dem_lauf_mit(cfg, monkeypatch):
    assert tick_mit_lauf(cfg, monkeypatch, ausflug=True) == 1


def test_die_zusatzaufgaben_lassen_sich_abschalten(cfg, monkeypatch):
    assert tick_mit_lauf(cfg, monkeypatch, ausflug=False) == 0


def test_ohne_erfolgreichen_lauf_kein_ausflug(cfg, monkeypatch):
    """Den Knopf fuer die Zusatzaufgaben zeigt die Seite erst nach erledigtem Check-in."""
    from dataclasses import replace

    from aliexpress_coin_collector.runner import Outcome, RunResult

    ausfluege = []
    monkeypatch.setattr(scheduler.notify, "send", lambda *a, **k: None)
    monkeypatch.setattr(
        scheduler,
        "run_once",
        lambda config, adb, force=False: RunResult(outcome=Outcome.UNREACHABLE, message="weg"),
    )
    monkeypatch.setattr(scheduler, "collect_extras", lambda *a, **k: ausfluege.append(True))

    cfg = replace(cfg, extras_after_run=True)
    plan = scheduler.plan_for(NOW.date(), cfg)
    scheduler.tick(cfg, ExtrasAdb(), Store(cfg.data_dir), scheduler.Reconnect(), "0.9.0", now=plan.morning_at)

    assert ausfluege == []
