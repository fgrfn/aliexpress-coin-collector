"""Discord-Meldungen: was im Kanal ankommt.

Ohne echten Webhook. Geprueft wird der Aufbau der Nachricht und dass ein Ausfall von Discord
den Check-in nie kostet -- eine Meldung ist Beiwerk, der Lauf ist die Arbeit.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import requests

from aliexpress_coin_collector import notify, scheduler
from aliexpress_coin_collector.runner import Outcome, RunResult

NOW = datetime(2026, 9, 21, 8, 30, 0)
HOOK = "https://discord.com/api/webhooks/1/streng-geheim"


class FakeResponse:
    def __init__(self, status_code=204, text=""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def posted(monkeypatch):
    """Faengt ab, was an Discord ginge."""
    seen: list[dict] = []

    def fake_post(url, **kwargs):
        seen.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    return seen


def embed(posted):
    call = posted[-1]
    if "json" in call:
        return call["json"]["embeds"][0]
    import json

    return json.loads(call["data"]["payload_json"])["embeds"][0]


# -- Aufbau der Nachricht ----------------------------------------------------------------------


def test_a_success_names_the_gain_and_the_balance(posted):
    result = RunResult(outcome=Outcome.CLAIMED, coins_before=1572, coins_after=1582, duration_s=21.4, message="")
    assert notify.send(HOOK, scheduler.message_for("morning", result), now=NOW)

    body = embed(posted)
    assert body["title"] == "✅ Münzen gesammelt"
    assert body["color"] == notify.COLORS["ok"]
    assert body["footer"]["text"] == "Morgenlauf"
    values = {f["name"]: f["value"] for f in body["fields"]}
    assert values["Zuwachs"] == "+10"
    assert values["Münzstand"] == "1.582"  # mit Tausenderpunkt
    assert values["Dauer"] == "21 s"


def test_a_missing_balance_is_not_shown_as_zero(posted):
    result = RunResult(outcome=Outcome.CLAIMED, coins_before=None, coins_after=None, duration_s=9.0, message="")
    notify.send(HOOK, scheduler.message_for("morning", result), now=NOW)
    values = {f["name"]: f["value"] for f in embed(posted)["fields"]}
    assert values["Münzstand"] == "unbekannt"
    assert "Zuwachs" not in values, "ohne Vorher und Nachher gibt es keinen Zuwachs"


def test_a_failure_explains_the_reason_in_words(posted):
    result = RunResult(outcome=Outcome.UNREACHABLE, duration_s=9.1, message="Geraet nicht erreichbar")
    notify.send(HOOK, scheduler.message_for("morning", result), now=NOW)

    body = embed(posted)
    assert "Gerät nicht erreichbar" in body["title"]
    assert "adb tcpip 5555" in body["description"], "der Grund gehoert in Worte, nicht als Code-Wort"
    assert body["color"] == notify.COLORS["warn"]


def test_the_last_attempt_of_the_day_is_marked_and_red(posted):
    result = RunResult(outcome=Outcome.NOT_FOUND, duration_s=30.0, message="nichts erkannt")
    notify.send(HOOK, scheduler.message_for("evening", result, final_attempt=True), now=NOW)

    body = embed(posted)
    assert "letzte Versuch für heute" in body["description"]
    assert body["color"] == notify.COLORS["bad"]


def test_a_screenshot_is_shown_inside_the_embed(posted):
    result = RunResult(outcome=Outcome.NOT_FOUND, duration_s=30.0, message="x", screenshot=b"png")
    notify.send(HOOK, scheduler.message_for("morning", result), b"png", now=NOW)

    assert embed(posted)["image"]["url"] == f"attachment://{notify.SHOT_NAME}"
    assert "file" in posted[-1]["files"]


def test_an_already_done_run_is_blue_not_green(posted):
    result = RunResult(outcome=Outcome.ALREADY_DONE, coins_after=1582, duration_s=12.0, message="")
    notify.send(HOOK, scheduler.message_for("morning", result), now=NOW)
    assert embed(posted)["color"] == notify.COLORS["info"]


# -- Stoerungsmeldung --------------------------------------------------------------------------


def test_the_offline_message_masks_the_device_address(posted):
    # Die Adresse ist ein internes Detail und hat in einem Chat-Kanal nichts verloren.
    notify.send(HOOK, scheduler.offline_message(45, "10.10.30.169:5555"), now=NOW)
    values = {f["name"]: f["value"] for f in embed(posted)["fields"]}
    assert values["Adresse"] == "10.10.30.xxx:5555"
    assert "169" not in str(embed(posted))
    assert values["Seit"] == "45 Minuten"


def test_a_long_outage_is_given_in_hours(posted):
    notify.send(HOOK, scheduler.offline_message(150, "10.0.0.1:5555"), now=NOW)
    values = {f["name"]: f["value"] for f in embed(posted)["fields"]}
    assert values["Seit"] == "2 h 30 min"


def test_the_all_clear_is_green(posted):
    notify.send(HOOK, scheduler.back_message(45), now=NOW)
    body = embed(posted)
    assert body["title"] == "✅ Gerät wieder erreichbar"
    assert body["color"] == notify.COLORS["ok"]


# -- Nichts davon darf den Lauf kosten -----------------------------------------------------------


def test_without_a_webhook_nothing_is_sent_and_nothing_breaks(posted):
    sent = notify.send("", notify.Message(title="egal"))
    assert not sent
    assert "kein Discord-Webhook" in sent.detail
    assert posted == []


def test_a_network_error_is_swallowed(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("kein Netz")

    monkeypatch.setattr(requests, "post", boom)
    sent = notify.send(HOOK, notify.Message(title="egal"))
    assert not sent
    assert "nicht erreichbar" in sent.detail


def test_a_rejection_by_discord_is_swallowed(monkeypatch, caplog):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(400, "Bad Request"))
    with caplog.at_level("WARNING"):
        assert not notify.send(HOOK, notify.Message(title="egal"))
    assert "streng-geheim" not in caplog.text, "der Webhook ist ein Geheimnis"


def test_a_deleted_webhook_is_explained_not_just_numbered(monkeypatch):
    # "HTTP 404" sagt niemandem, dass er den Webhook in Discord geloescht hat.
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(404, "Unknown Webhook"))
    sent = notify.send(HOOK, notify.Message(title="egal"))
    assert not sent
    assert "gelöscht" in sent.detail


def test_a_reason_never_contains_the_webhook(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(404, f"Unknown Webhook {HOOK}"))
    assert "streng-geheim" not in notify.send(HOOK, notify.Message(title="egal")).detail


# -- Testmeldung -------------------------------------------------------------------------------


def test_the_test_message_lists_what_will_be_reported(posted, cfg):
    from dataclasses import replace

    loud = replace(cfg, notify_on_success=True, notify_on_already_done=False, notify_on_offline=True)
    assert notify.send(HOOK, scheduler.test_message(loud), now=NOW)

    body = embed(posted)
    assert body["title"] == "🔔 Testmeldung"
    values = {f["name"]: f["value"] for f in body["fields"]}
    assert values["Münzen gesammelt"] == "wird gemeldet"
    assert values["Heute schon eingecheckt"] == "wird nicht gemeldet"
    assert "30 Minuten" in values["Gerät nicht erreichbar"]
    assert "immer" in values["Fehlgeschlagener Lauf"]


def test_the_test_message_says_when_nothing_is_switched_on(posted, cfg):
    from dataclasses import replace

    quiet = replace(cfg, notify_on_success=False, notify_on_already_done=False, notify_on_offline=False)
    notify.send(HOOK, scheduler.test_message(quiet), now=NOW)
    values = {f["name"]: f["value"] for f in embed(posted)["fields"]}
    assert values["Gerät nicht erreichbar"] == "wird nicht gemeldet"
    # Fehlschlaege bleiben an, das ist Absicht und soll auch so dastehen.
    assert "immer" in values["Fehlgeschlagener Lauf"]


def test_an_overlong_text_is_cut_instead_of_being_refused(posted):
    # Discord lehnt eine zu lange Nachricht komplett ab. Lieber kuerzen als ganz verlieren.
    notify.send(HOOK, notify.Message(title="x" * 500, description="y" * 5000), now=NOW)
    body = embed(posted)
    assert len(body["title"]) == notify.MAX_TITLE
    assert len(body["description"]) == notify.MAX_DESCRIPTION


# -- Testmeldung von der Kommandozeile -----------------------------------------------------------


def test_the_cli_reports_success_with_exit_code_zero(cfg, monkeypatch, capsys):
    from dataclasses import replace

    from aliexpress_coin_collector import __main__

    monkeypatch.setattr(__main__.notify, "send", lambda *a, **k: notify.Sent(True))
    code = __main__.cmd_notify_test(replace(cfg, discord_webhook=HOOK), None)
    assert code == 0
    assert "geschickt" in capsys.readouterr().out


def test_the_cli_fails_loudly_and_never_prints_the_webhook(cfg, monkeypatch, capsys):
    from dataclasses import replace

    from aliexpress_coin_collector import __main__

    monkeypatch.setattr(__main__.notify, "send", lambda *a, **k: notify.Sent(False, "Discord war nicht erreichbar."))
    code = __main__.cmd_notify_test(replace(cfg, discord_webhook=HOOK), None)
    assert code == 1
    out = capsys.readouterr()
    assert "nicht erreichbar" in out.err
    assert "streng-geheim" not in out.err + out.out


def test_the_cli_says_when_no_webhook_is_configured(cfg, capsys):
    from aliexpress_coin_collector import __main__

    assert __main__.cmd_notify_test(cfg, None) == 1
    assert "Kein Discord-Webhook" in capsys.readouterr().err
