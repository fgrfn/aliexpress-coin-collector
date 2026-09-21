"""Protokolldatei: schreiben, wieder einlesen, filtern.

Kein Gerät nötig. Wichtig ist vor allem, dass ein kaputtes oder fehlendes Protokoll den
Dienst nicht anhält und die Seite nicht leert.
"""

from __future__ import annotations

import logging

from aliexpress_coin_collector import logs

SAMPLE = """2026-09-21 07:46:03,120 INFO    Lauf gestartet (Art: morgens)
2026-09-21 07:46:19,004 INFO    Seite erkannt nach 15.2 s
2026-09-21 07:46:20,880 WARNING Button nur mit Schwellwert 220 gefunden
2026-09-21 07:46:27,001 INFO    Bestaetigung erkannt, Muenzstand 1275
2026-09-21 09:38:11,500 ERROR   Geraet meldet unauthorized
Traceback (most recent call last):
  File "irgendwo.py", line 3, in tu_was
RuntimeError: kaputt
2026-09-21 09:38:14,002 INFO    Verbindung wiederhergestellt
"""


# -- Einlesen --------------------------------------------------------------------------------


def test_lines_are_parsed_into_time_level_and_message():
    lines = logs.parse(SAMPLE)
    assert lines[0].when == "2026-09-21 07:46:03"
    assert lines[0].level == "INFO"
    assert lines[0].message == "Lauf gestartet (Art: morgens)"
    assert lines[0].time == "07:46:03"


def test_a_traceback_belongs_to_the_line_above_it():
    lines = logs.parse(SAMPLE)
    assert len(lines) == 6  # nicht 9: die drei Traceback-Zeilen gehoeren zur ERROR-Zeile
    error = [x for x in lines if x.level == "ERROR"][0]
    assert "RuntimeError: kaputt" in error.message
    assert error.message.startswith("Geraet meldet unauthorized")


def test_levels_get_a_tone_for_the_display():
    by_level = {x.level: x for x in logs.parse(SAMPLE)}
    assert by_level["INFO"].tone == ""
    assert by_level["WARNING"].tone == "warn"
    assert by_level["ERROR"].tone == "bad"


def test_garbage_between_entries_does_not_produce_a_line():
    assert logs.parse("nur irgendein Text ohne Zeitstempel\n") == []


def test_an_empty_log_is_empty_not_an_error():
    assert logs.parse("") == []


# -- Filtern ---------------------------------------------------------------------------------


def test_no_level_means_everything():
    assert len([x for x in logs.parse(SAMPLE) if logs.matches(x, "", "")]) == 6


def test_a_level_filters_from_there_upwards():
    lines = logs.parse(SAMPLE)
    warn = [x for x in lines if logs.matches(x, "WARNING", "")]
    assert [x.level for x in warn] == ["WARNING", "ERROR"]
    errors = [x for x in lines if logs.matches(x, "ERROR", "")]
    assert [x.level for x in errors] == ["ERROR"]


def test_the_search_ignores_case():
    lines = logs.parse(SAMPLE)
    hits = [x for x in lines if logs.matches(x, "", "MUENZSTAND")]
    assert len(hits) == 1
    assert "1275" in hits[0].message


def test_level_and_search_apply_together():
    lines = logs.parse(SAMPLE)
    assert [x for x in lines if logs.matches(x, "ERROR", "unauthorized")]
    assert not [x for x in lines if logs.matches(x, "ERROR", "Bestaetigung")]


# -- Datei -----------------------------------------------------------------------------------


def test_without_a_file_the_log_is_empty(tmp_path):
    assert logs.exists(tmp_path) is False
    assert logs.read(tmp_path) == []


def test_reading_returns_the_newest_last(tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    lines = logs.read(tmp_path)
    assert lines[-1].message == "Verbindung wiederhergestellt"
    assert logs.exists(tmp_path) is True


def test_only_the_tail_is_read_from_a_big_file(tmp_path):
    # Eine Million Zeilen will niemand in den Speicher holen, nur um zwanzig anzuzeigen.
    many = "".join(f"2026-09-21 07:{i % 60:02d}:00,000 INFO    Zeile {i}\n" for i in range(40000))
    logs.path(tmp_path).write_text(many, encoding="utf-8")
    lines = logs.read(tmp_path, limit=20)
    assert len(lines) == 20
    assert lines[-1].message == "Zeile 39999"


def test_a_cut_first_line_is_dropped_not_shown_broken(tmp_path):
    many = "".join(f"2026-09-21 07:00:00,000 INFO    Zeile {i}\n" for i in range(40000))
    logs.path(tmp_path).write_text(many, encoding="utf-8")
    for line in logs.read(tmp_path, limit=500):
        assert line.message.startswith("Zeile ")


def test_the_limit_caps_the_number_of_lines(tmp_path):
    logs.path(tmp_path).write_text(SAMPLE, encoding="utf-8")
    assert len(logs.read(tmp_path, limit=2)) == 2


# -- Schreiben -------------------------------------------------------------------------------


def test_the_daemon_writes_what_the_reader_understands(tmp_path):
    # Der eigentliche Punkt: Format und Auswertung duerfen nicht auseinanderlaufen.
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        assert logs.attach(tmp_path, "INFO") is True
        logging.getLogger("test").warning("Geraet meldet %s", "unauthorized")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()

    lines = logs.read(tmp_path)
    assert len(lines) == 1
    assert lines[0].level == "WARNING"
    assert lines[0].message == "Geraet meldet unauthorized"
    assert lines[0].tone == "warn"


def test_a_log_that_cannot_be_written_does_not_raise(tmp_path):
    # Kein Schreibrecht darf hoechstens das Protokoll kosten, nie den Check-in.
    blocked = tmp_path / "datei"
    blocked.write_text("ich bin kein Verzeichnis", encoding="utf-8")
    assert logs.attach(blocked / "drunter", "INFO") is False
