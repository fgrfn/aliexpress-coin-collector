from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from . import __version__, commands, homeassistant, notify, settings, stats
from .adb import Adb, Battery
from .config import Config, ConfigError
from .runner import SUCCESS, Outcome, RunResult, read_battery, run_once
from .store import Attempt, Store

log = logging.getLogger(__name__)

SUCCESS_VALUES = {o.value for o in SUCCESS}
HEARTBEAT_FILE = "heartbeat"
REQUEST_FILE = "run-requested"
_SEED = "aliexpress-coins"  # historischer Wert, damit sich die Uhrzeiten pro Datum durch die Umbenennung nicht aendern


@dataclass(frozen=True)
class Plan:
    morning_at: datetime
    evening_at: datetime


@dataclass(frozen=True)
class Decision:
    kind: str  # 'morning' | 'evening'
    force: bool  # trotz eingeschaltetem Bildschirm starten


def _random_between(rng: random.Random, day: date, start: dtime, end: dtime) -> datetime:
    s, e = datetime.combine(day, start), datetime.combine(day, end)
    if e <= s:
        return s
    return s + timedelta(seconds=rng.randint(0, int((e - s).total_seconds())))


def plan_for(day: date, cfg: Config) -> Plan:
    """Zufaellige, aber pro Tag stabile Uhrzeiten (ueberlebt Neustarts des Dienstes)."""
    rng = random.Random(f"{_SEED}-{day.isoformat()}")
    return Plan(
        morning_at=_random_between(rng, day, cfg.morning_start, cfg.morning_end),
        evening_at=_random_between(rng, day, cfg.evening_start, cfg.evening_end),
    )


def next_runs(day: date, cfg: Config, days: int = 7) -> list[tuple[date, Plan]]:
    """Plaene fuer 'days' Tage ab 'day' (einschliesslich)."""
    if days < 1:
        raise ValueError("days muss mindestens 1 sein")
    return [(day + timedelta(days=i), plan_for(day + timedelta(days=i), cfg)) for i in range(days)]


def next_due(now: datetime, cfg: Config, today_attempts: list[Attempt], horizon_days: int = 8) -> datetime | None:
    """Naechster geplanter Lauf, der noch aussteht. None, wenn im Horizont keiner mehr faellig ist.

    Beruecksichtigt fuer heute den Stand aus der Datenbank: nach einem Erfolg steht heute nichts mehr an, und
    eine Art (morning/evening), die bereits einen echten Versuch hatte, wird nicht erneut geplant.
    """
    for offset in range(max(1, horizon_days)):
        day = now.date() + timedelta(days=offset)
        plan = plan_for(day, cfg)
        attempts = today_attempts if offset == 0 else []
        if any(a.outcome in SUCCESS_VALUES for a in attempts):
            continue  # an diesem Tag ist nichts mehr zu tun
        done_kinds = {a.kind for a in attempts if a.outcome != Outcome.BUSY.value}
        kinds = [("morning", plan.morning_at)]
        if cfg.evening_enabled:
            kinds.append(("evening", plan.evening_at))
        for kind, at in kinds:
            if at > now and kind not in done_kinds:
                return at
    return None


def decide(now: datetime, plan: Plan, attempts: list[Attempt], cfg: Config) -> Decision | None:
    """Reine Entscheidungslogik: soll jetzt ein Lauf starten, und welcher?"""
    if any(a.outcome in SUCCESS_VALUES for a in attempts):
        return None

    real = [a for a in attempts if a.outcome != Outcome.BUSY.value]  # BUSY zaehlt nicht als Versuch
    busy = [a for a in attempts if a.outcome == Outcome.BUSY.value]
    done_kinds = {a.kind for a in real}

    # Ohne Abendlauf gibt es keine obere Grenze fuer den Morgenlauf: er bleibt bis Tagesende
    # faellig. Sonst waere ein Morgenfenster hinter der (dann bedeutungslosen) Abendzeit tot.
    evening_at = plan.evening_at if cfg.evening_enabled else None

    if evening_at is not None and now >= evening_at and "evening" not in done_kinds:
        kind, base = "evening", evening_at
    elif plan.morning_at <= now and (evening_at is None or now < evening_at) and "morning" not in done_kinds:
        kind, base = "morning", plan.morning_at
    else:
        return None

    # Wurde das Zeitfenster geaendert, nachdem diese Uhrzeit bereits verstrichen war, gilt sie fuer
    # heute als verpasst. Sonst loeste eine Einstellungsaenderung rueckwirkend einen Lauf aus.
    if cfg.settings_changed_at is not None and base < cfg.settings_changed_at:
        return None

    if busy and now < busy[-1].ts + timedelta(minutes=cfg.busy_retry_min):
        return None
    force = now >= base + timedelta(minutes=cfg.busy_max_wait_min)
    return Decision(kind, force)


def touch_heartbeat(cfg: Config) -> None:
    """Lebenszeichen fuer die Weboberflaeche. Ein Fehler hier darf den Dienst nie stoppen."""
    try:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        (cfg.data_dir / HEARTBEAT_FILE).write_text(f"{datetime.now():%Y-%m-%dT%H:%M:%S}\n", encoding="utf-8")
    except OSError as exc:
        log.warning("Herzschlag konnte nicht geschrieben werden: %s", exc)


def take_request(cfg: Config) -> bool:
    """True, wenn die Weboberflaeche einen Lauf angefordert hat. Die Anforderung wird dabei verbraucht."""
    path = cfg.data_dir / REQUEST_FILE
    try:
        if not path.is_file():
            return False
        path.unlink()
    except OSError as exc:
        log.warning("Auftragsdatei nicht verarbeitbar: %s", exc)
        return False
    return True


def with_current_settings(cfg: Config) -> Config:
    """Einstellungen aus settings.json nachladen, damit eine Aenderung ohne Neustart ankommt."""
    overrides = settings.load(cfg.data_dir)
    if overrides.empty:
        return cfg
    updated = replace(cfg, **overrides.values, settings_changed_at=overrides.changed_at)
    try:
        updated.validate()
    except ConfigError as exc:
        # Von Hand verstellte Werte duerfen den laufenden Dienst nicht aus dem Tritt bringen.
        log.warning("settings.json ergibt keine gueltige Einstellung, es bleibt beim bisherigen Stand: %s", exc)
        return cfg
    return updated


KIND_LABELS = {"morning": "Morgenlauf", "evening": "Abendlauf", "manual": "Handlauf"}

# Was bei einem Fehlschlag in der Meldung steht. Der Grund gehoert in Worte -- 'not_found' sagt
# demjenigen, der die Meldung auf dem Handy liest, nichts.
FAILURE_REASONS = {
    Outcome.UNREACHABLE: (
        "Gerät nicht erreichbar",
        "Aus, im Ruhezustand, nicht im Netz — oder `adb tcpip 5555` fehlt.",
    ),
    Outcome.LOGIN_REQUIRED: (
        "Anmeldung nötig",
        "Die App verlangt eine Anmeldung. **Bitte in der AliExpress-App neu einloggen** — bis dahin "
        "sammelt der Dienst nichts mehr. Das macht er absichtlich nicht selbst.",
    ),
    Outcome.NOT_FOUND: ("Nichts erkannt", "Weder der Sammeln-Knopf noch der Erledigt-Zustand waren auf der Seite."),
    Outcome.UNCONFIRMED: ("Nicht bestätigt", "Es wurde getippt, aber der Erfolg ließ sich danach nicht belegen."),
    Outcome.ERROR: ("Unerwarteter Fehler", ""),
}


def describe(kind: str, result: RunResult) -> str:
    """Eine Zeile fuer das Protokoll. Bewusst ohne Umlaute, wie der Rest der Log-Ausgabe."""
    label = KIND_LABELS.get(kind, kind)
    if result.outcome == Outcome.CLAIMED:
        gain = ""
        if result.coins_before is not None and result.coins_after is not None:
            gain = f" ({result.coins_before} -> {result.coins_after}, +{result.coins_after - result.coins_before})"
        return f"✅ {label}: Coins eingesammelt{gain}"
    if result.outcome == Outcome.ALREADY_DONE:
        return f"ℹ️ {label}: heute schon eingecheckt (Stand: {result.coins_after})"
    return f"⚠️ {label} fehlgeschlagen [{result.outcome.value}]: {result.message}"


def message_for(kind: str, result: RunResult, final_attempt: bool = False) -> notify.Message:
    """Dasselbe Ergebnis als Discord-Meldung: Farbe nach Ausgang, Zahlen in eigenen Feldern.

    Getrennt von describe(), weil das Protokoll eine Zeile will und ohne Umlaute auskommt,
    waehrend diese Meldung ein Mensch im Kanal liest.
    """
    label = KIND_LABELS.get(kind, kind)
    duration = notify.Field("Dauer", f"{result.duration_s:.0f} s")

    if result.outcome == Outcome.CLAIMED:
        fields = []
        if result.coins_before is not None and result.coins_after is not None:
            fields.append(notify.Field("Zuwachs", f"+{result.coins_after - result.coins_before}"))
        fields.append(notify.Field("Münzstand", notify.number(result.coins_after)))
        return notify.Message(
            title="✅ Münzen gesammelt",
            tone="ok",
            fields=(*fields, duration),
            footer=label,
        )

    if result.outcome == Outcome.ALREADY_DONE:
        return notify.Message(
            title="ℹ️ Heute schon eingecheckt",
            tone="info",
            description="Der Check-in war bereits erledigt, es gab nichts mehr zu holen.",
            fields=(notify.Field("Münzstand", notify.number(result.coins_after)), duration),
            footer=label,
        )

    heading, explanation = FAILURE_REASONS.get(result.outcome, ("Fehlgeschlagen", ""))
    description = explanation
    if final_attempt:
        description = f"{description}\n\n❌ **Das war der letzte Versuch für heute.**".strip()
    return notify.Message(
        title=f"⚠️ {label}: {heading}",
        # Rot, sobald es von allein nicht mehr gut wird: beim letzten Versuch des Tages, und
        # immer bei einer noetigen Anmeldung -- die wartet auf einen Menschen.
        tone="bad" if final_attempt or result.outcome == Outcome.LOGIN_REQUIRED else "warn",
        description=description,
        fields=(notify.Field("Meldung", result.message or "—", inline=False), duration),
        footer=label,
    )


# Erst nach dieser Zeit ohne Verbindung wird gemeldet. Kurze Aussetzer sind normal -- ein Handy
# im Doze, ein Router, der neu startet. Eine Meldung nach dreissig Sekunden waere nur Laerm.
OFFLINE_ALERT_MIN = 30


@dataclass
class Outage:
    """Merkt sich, seit wann das Geraet weg ist und ob deswegen schon gemeldet wurde.

    Reine Zustandshaltung: gibt nur zurueck, was zu melden waere, und schickt selbst nichts.
    Damit laesst sich das Verhalten ohne Geraet und ohne Discord pruefen.
    """

    since: datetime | None = None
    alerted: bool = False

    def note(self, reachable: bool, now: datetime, after_min: int = OFFLINE_ALERT_MIN) -> str:
        """Gibt '' (nichts zu tun), 'down' (Stoerung) oder 'up' (Entwarnung) zurueck.

        Gemeldet wird je Ausfall genau einmal, und die Entwarnung nur, wenn es vorher auch
        eine Stoerungsmeldung gab -- sonst kaeme nach jedem kurzen Aussetzer ein Haken.
        """
        if reachable:
            was_alerted = self.alerted
            self.since, self.alerted = None, False
            return "up" if was_alerted else ""
        if self.since is None:
            self.since = now
            return ""
        if not self.alerted and now >= self.since + timedelta(minutes=after_min):
            self.alerted = True
            return "down"
        return ""

    def minutes(self, now: datetime) -> int:
        return 0 if self.since is None else max(0, int((now - self.since).total_seconds()) // 60)


def _duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} Minuten"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def offline_message(minutes: int, serial: str) -> notify.Message:
    return notify.Message(
        title="🔌 Gerät nicht erreichbar",
        tone="bad",
        description=(
            "Der Dienst kommt seit einer Weile nicht an das Gerät. Geplante Läufe fallen aus, "
            "bis die Verbindung wieder steht. Er versucht es weiter, mit wachsendem Abstand."
        ),
        fields=(
            notify.Field("Seit", _duration(minutes)),
            notify.Field("Adresse", commands.mask_serial(serial)),
        ),
        footer="Häufigste Ursache: Gerät aus, im Ruhezustand oder nach einem Neustart ohne adb tcpip",
    )


DIGEST_FILE = "last-digest"
DIGEST_DAYS = 7
DIGEST_WEEKDAY = 0  # Montag
DIGEST_HOUR = 9


def digest_due(data_dir: Path, today: date, weekday: int, hour: int, now_hour: int) -> bool:
    """Ist heute ein Rueckblick faellig, und wurde er noch nicht geschickt?

    Der Merker ist eine Datei mit einem Datum. Ohne sie waere der Rueckblick entweder an jedem
    Takt des Stichtags noch einmal draussen oder nach einem Neustart des Dienstes verloren.
    Beim allerersten Mal wird nichts verschickt: sonst kaeme direkt nach der Installation ein
    Rueckblick auf eine leere Woche.
    """
    if today.weekday() != weekday or now_hour < hour:
        return False
    path = data_dir / DIGEST_FILE
    try:
        last = date.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        # Noch nie geschickt: Stichtag vermerken und diese eine Woche auslassen.
        _mark_digest(data_dir, today)
        return False
    return last < today


def _mark_digest(data_dir: Path, today: date) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / DIGEST_FILE).write_text(today.isoformat() + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("Merker fuer den Rueckblick nicht schreibbar: %s", exc)


def digest_message(attempts: list[Attempt], today: date, days: int = DIGEST_DAYS) -> notify.Message:
    """Rueckblick auf die letzten Tage. Rechnet mit denselben Regeln wie die Weboberflaeche."""
    window = stats.in_range(attempts, today, days)
    quota = stats.success_quota(window)
    gained = stats.total_gain(window)
    balance = stats.latest_coins(window) or stats.latest_coins(attempts)
    missed = quota.total - quota.good

    if not window:
        description = "In den letzten sieben Tagen wurde kein einziger Lauf aufgezeichnet."
    elif missed == 0:
        description = "Jeden Tag eingesammelt."
    else:
        description = f"An {missed} von {quota.total} Tagen hat es nicht geklappt."

    return notify.Message(
        title="📅 Die Woche in Zahlen",
        tone=quota.tone or "info",
        description=description,
        fields=(
            notify.Field("Erfolgsquote", f"{quota.percent} % ({quota.good}/{quota.total} Tage)"),
            notify.Field("Gesammelt", f"+{notify.number(gained)}"),
            notify.Field("Münzstand", notify.number(balance)),
        ),
        footer=f"Rückblick auf {days} Tage",
    )


STALL_FILE = "stall-warned"


def stall_due(data_dir: Path, stall: stats.Stall) -> bool:
    """Ist zu diesem Stillstand schon gewarnt worden?

    Gemerkt wird der Muenzstand, nicht ein Datum: solange er sich nicht bewegt, ist es derselbe
    Vorfall und es bleibt bei einer Meldung. Steigt er wieder und bleibt spaeter erneut stehen,
    unterscheidet sich der Stand -- und es wird wieder gewarnt.
    """
    if not stall.stalled:
        return False
    try:
        return (data_dir / STALL_FILE).read_text(encoding="utf-8").strip() != str(stall.coins)
    except OSError:
        return True


def _mark_stall(data_dir: Path, stall: stats.Stall) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / STALL_FILE).write_text(f"{stall.coins}\n", encoding="utf-8")
    except OSError as exc:
        log.warning("Merker fuer den Stillstand nicht schreibbar: %s", exc)


def stall_message(stall: stats.Stall) -> notify.Message:
    return notify.Message(
        title="🛑 Erfolg gemeldet, aber nichts gesammelt",
        tone="bad",
        description=(
            "Der Dienst meldet seit Tagen Erfolg, der Münzstand steht aber unverändert. "
            "Irgendetwas stimmt nicht — die Läufe laufen, nur bringen sie nichts ein."
        ),
        fields=(
            notify.Field("Tage ohne Zuwachs", str(stall.days)),
            notify.Field("Münzstand", notify.number(stall.coins)),
        ),
        footer="Nachsehen unter Diagnose: was erkennt die Seite? Und im Protokoll, wie die Läufe ausgingen",
    )


# --- Akku ---------------------------------------------------------------------------------

# Wie oft der Akkustand abgefragt wird, wenn nichts anderes eingestellt ist. Der Aufruf ist
# ein reiner Lesezugriff, weckt nichts und tippt nichts an -- alle 15 Minuten ist billig.
BATTERY_POLL_MIN = 15

# Was gemeldet wird, und wie es in der Meldung heisst.
BATTERY_PROBLEMS = {
    "power": (
        "Hängt nicht am Strom",
        "Das Gerät lädt nicht. Läuft der Akku leer, geht es aus — und nach dem Neustart ist "
        "auch die ADB-Verbindung weg, die sich nur per USB wiederherstellen lässt. "
        "Steckdose, Netzteil und Kabel prüfen.",
    ),
    "low": (
        "Ladestand niedrig",
        "Der Akku ist weit unten. Ohne Strom bleiben nur noch Stunden, bis das Gerät ausgeht.",
    ),
    "hot": (
        "Zu warm",
        "Wärme lässt den Akku schneller altern und ihn irgendwann aufblähen. Hülle ab, das "
        "Gerät kühler und freier stellen.",
    ),
    "health": (
        "Das Gerät meldet einen Akkuschaden",
        "Android meldet den Akku ausdrücklich als nicht in Ordnung. Das sagt ein Gerät selten "
        "grundlos — nachsehen, ob die Rückseite sich wölbt.",
    ),
}


def battery_problems(reading: Battery, low_pct: int, hot_c: int) -> set[str]:
    """Was an diesem Messwert nicht stimmt. Reine Logik, ohne Gedaechtnis und ohne Meldung.

    Fehlende Werte ergeben kein Problem: ein Geraet, das die Temperatur nicht meldet, ist
    deswegen nicht zu warm.
    """
    found = set()
    # Auch am Kabel kann der Stand fallen, wenn das Netzteil zu schwach ist oder nichts liefert.
    if not reading.plugged or reading.status == 3:
        found.add("power")
    if reading.level is not None and reading.level <= low_pct:
        found.add("low")
    if reading.temperature_c is not None and reading.temperature_c >= hot_c:
        found.add("hot")
    if not reading.healthy:
        found.add("health")
    return found


@dataclass
class BatteryWatch:
    """Merkt sich den letzten Messwert und wovon schon einmal die Rede war.

    Wie Outage reine Zustandshaltung: sie sagt nur, was zu melden waere, und schickt selbst
    nichts. Je Problem genau eine Meldung, und eine Entwarnung erst, wenn alle weg sind.
    """

    last: Battery | None = None
    at: datetime | None = None
    alerted: set[str] = field(default_factory=set)

    def due(self, now: datetime, every_min: int) -> bool:
        if every_min <= 0:  # abgeschaltet: dann zaehlt nur, was ein Lauf nebenbei mitbringt
            return False
        return self.at is None or now >= self.at + timedelta(minutes=every_min)

    def note(self, reading: Battery, now: datetime, low_pct: int, hot_c: int) -> tuple[set[str], bool]:
        """Messwert uebernehmen. Gibt die neu aufgetretenen Probleme zurueck und ob Entwarnung faellig ist."""
        self.last, self.at = reading, now
        found = battery_problems(reading, low_pct, hot_c)
        fresh = found - self.alerted
        cleared = bool(self.alerted) and not found
        self.alerted = found
        return fresh, cleared


def _battery_fields(reading: Battery) -> tuple[notify.Field, ...]:
    fields = []
    if reading.level is not None:
        fields.append(notify.Field("Ladestand", f"{reading.level} %"))
    if reading.temperature_c is not None:
        fields.append(notify.Field("Temperatur", f"{reading.temperature_c:.1f} °C".replace(".", ",")))
    fields.append(notify.Field("Zustand", reading.status_label))
    return tuple(fields)


def battery_message(problems: set[str], reading: Battery) -> notify.Message:
    """Eine Meldung fuer alle gleichzeitig aufgetretenen Probleme, nicht eine je Problem."""
    # Feste Reihenfolge, damit die Meldung nicht bei jedem Mal anders aussieht.
    order = [key for key in BATTERY_PROBLEMS if key in problems]
    heading = BATTERY_PROBLEMS[order[0]][0] if order else "Akku"
    description = "\n\n".join(f"**{BATTERY_PROBLEMS[key][0]}** — {BATTERY_PROBLEMS[key][1]}" for key in order)
    return notify.Message(
        title=f"🔋 {heading}",
        # Kein Strom heisst: es laeuft auf einen Totalausfall zu. Das ist kein gelber Hinweis.
        tone="bad" if {"power", "health"} & problems else "warn",
        description=description,
        fields=_battery_fields(reading),
        footer="Akku des Geräts",
    )


def battery_ok_message(reading: Battery) -> notify.Message:
    return notify.Message(
        title="🔋 Akku wieder in Ordnung",
        tone="ok",
        description="Die gemeldeten Auffälligkeiten am Akku sind weg.",
        fields=_battery_fields(reading),
    )


def check_battery(cfg: Config, adb: Adb, watch: BatteryWatch, now: datetime) -> Battery | None:
    """Akku abfragen, falls faellig, und melden, was sich geaendert hat. Wirft nie."""
    if not watch.due(now, cfg.battery_poll_min):
        return None
    reading = read_battery(adb)
    if reading is None:
        return None
    fresh, cleared = watch.note(reading, now, cfg.battery_low_pct, cfg.battery_hot_c)
    if cfg.notify_on_battery:
        if fresh:
            notify.send(cfg.discord_webhook, battery_message(fresh, reading))
        elif cleared:
            notify.send(cfg.discord_webhook, battery_ok_message(reading))
    return reading


def test_message(cfg: Config) -> notify.Message:
    """Testmeldung. Zeigt zugleich, was ueberhaupt gemeldet wird -- sonst weiss man nach dem
    erfolgreichen Test immer noch nicht, wovon man kuenftig hoert."""
    on, off = "wird gemeldet", "wird nicht gemeldet"
    return notify.Message(
        title="🔔 Testmeldung",
        tone="info",
        description=(
            "Der Webhook funktioniert. So sehen die Meldungen des Coin Collectors aus — "
            "Farbe nach Ausgang, Zahlen in eigenen Feldern."
        ),
        fields=(
            notify.Field("Münzen gesammelt", on if cfg.notify_on_success else off),
            notify.Field("Heute schon eingecheckt", on if cfg.notify_on_already_done else off),
            notify.Field(
                "Gerät nicht erreichbar",
                f"{on} (nach {_duration(cfg.offline_alert_min)})" if cfg.notify_on_offline else off,
            ),
            notify.Field(
                "Akku",
                f"{on} (unter {cfg.battery_low_pct} %, ab {cfg.battery_hot_c} °C, ohne Strom)"
                if cfg.notify_on_battery
                else off,
            ),
            notify.Field("Fehlgeschlagener Lauf", "wird immer gemeldet, mit Screenshot", inline=False),
        ),
        footer=f"Coin Collector {__version__}",
    )


def back_message(minutes: int) -> notify.Message:
    return notify.Message(
        title="✅ Gerät wieder erreichbar",
        tone="ok",
        description="Die Verbindung steht wieder, der Zeitplan läuft normal weiter.",
        fields=(notify.Field("Ausfall", _duration(minutes)),),
    )


def handle_result(cfg: Config, store: Store, kind: str, result: RunResult, final_attempt: bool = False) -> None:
    battery = result.battery
    store.add(
        Attempt(
            ts=datetime.now(),
            kind=kind,
            outcome=result.outcome.value,
            coins_before=result.coins_before,
            coins_after=result.coins_after,
            message=result.message,
            duration_s=round(result.duration_s, 1),
            battery_level=battery.level if battery else None,
            battery_temp_c=battery.temperature_c if battery else None,
            battery_status=battery.status_label if battery else "",
        )
    )
    log.info(describe(kind, result))

    if result.outcome == Outcome.BUSY:
        return
    if result.ok:
        wanted = cfg.notify_on_success if result.outcome == Outcome.CLAIMED else cfg.notify_on_already_done
        if wanted:
            notify.send(cfg.discord_webhook, message_for(kind, result))
        return

    if result.screenshot:
        shots = cfg.data_dir / "shots"
        shots.mkdir(parents=True, exist_ok=True)
        (shots / f"{datetime.now():%Y%m%d-%H%M%S}-{result.outcome.value}.png").write_bytes(result.screenshot)
        for old in sorted(shots.glob("*.png"))[:-30]:  # nur die letzten 30 behalten
            old.unlink(missing_ok=True)
    notify.send(cfg.discord_webhook, message_for(kind, result, final_attempt), result.screenshot)


# Abstaende zwischen zwei Verbindungsversuchen, in Minuten. Der erste Versuch kommt sofort,
# danach wird der Abstand groesser: ein 'adb connect' laeuft bei totem Geraet in einen Timeout
# von 15 Sekunden, und das bei jedem Takt waere die halbe Zeit des Dienstes.
RECONNECT_BACKOFF_MIN = (1, 2, 5, 10, 30)


@dataclass
class Reconnect:
    """Merkt sich, wann zuletzt neu verbunden wurde und wie oft es nichts brachte.

    Reine Zustandshaltung, ohne ADB -- damit die Abstaende ohne Geraet pruefbar sind.
    """

    failures: int = 0
    last_try: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Darf jetzt wieder ein Versuch laufen?"""
        if self.last_try is None or self.failures == 0:
            return True
        wait = RECONNECT_BACKOFF_MIN[min(self.failures - 1, len(RECONNECT_BACKOFF_MIN) - 1)]
        return now >= self.last_try + timedelta(minutes=wait)

    def note(self, ok: bool, now: datetime) -> None:
        self.last_try = now
        self.failures = 0 if ok else self.failures + 1

    def reset(self) -> None:
        """Das Geraet ist von sich aus wieder da -- der naechste Abriss darf sofort ran."""
        self.failures = 0
        self.last_try = None


def report_status(
    cfg: Config,
    adb: Adb,
    version: str,
    reconnect: Reconnect | None = None,
    now: datetime | None = None,
) -> str:
    """Blick auf das Geraet fuer die Weboberflaeche. Gibt den Zustand zurueck.

    'adb get-state' fragt nur den lokalen adb-Server, weckt das Geraet also nicht und tippt
    nicht darauf. Der Bildschirmzustand kostet dagegen einen echten shell-Aufruf und wird
    deshalb nur geholt, wenn das Geraet ueberhaupt antwortet.

    Mit 'reconnect' heilt sich die Meldung selbst: get-state baut eine abgerissene
    TCP-Verbindung nie von allein wieder auf, die Seite zeigte sonst dauerhaft "nicht
    erreichbar", obwohl das Geraet laengst wieder im Netz haengt.
    """
    now = now or datetime.now()
    state = "unbekannt"
    screen: bool | None = None
    try:
        state = adb.state()
        # Bei 'unauthorized' steht die Verbindung bereits -- da wartet ein Dialog auf dem
        # Geraet, und ein weiteres connect aendert daran nichts.
        if reconnect is not None:
            if state == "device":
                reconnect.reset()
            elif state != "unauthorized" and reconnect.due(now):
                ok = False
                try:
                    ok = adb.ensure_connected()
                finally:
                    # Auch ein Versuch, der mit einer Ausnahme endet, zaehlt als Fehlversuch.
                    # Sonst bliebe last_try leer und der naechste Takt versuchte es sofort
                    # wieder -- der Backoff waere genau dann wirkungslos, wenn es klemmt.
                    reconnect.note(ok, now)
                log.info("Verbindung von allein neu aufgebaut" if ok else "Geraet weiter nicht erreichbar")
                state = adb.state()
        if state == "device":
            screen = adb.is_awake()
    except Exception as exc:  # noqa: BLE001 - ein Fehler hier darf den Dienst nie anhalten
        log.debug("Zustand des Geraets nicht ermittelbar: %s", exc)
        state = "offline" if state == "unbekannt" else state
    commands.write_status(cfg.data_dir, version, state, screen)
    return state


def run_command(cfg: Config, adb: Adb, store: Store, cmd: commands.Command) -> tuple[bool, str]:
    """Einen Auftrag der Weboberflaeche ausfuehren. Wirft nie."""
    try:
        if cmd.name == commands.RUN:
            result = run_once(cfg, adb, force=True)
            handle_result(cfg, store, "manual", result)
            return result.ok, describe("manual", result)

        if cmd.name == commands.RECONNECT:
            if adb.ensure_connected():
                return True, "Verbunden und freigegeben."
            state = adb.state()
            if state == "unauthorized":
                return False, "Verbunden, aber nicht freigegeben. Auf dem Geraet wartet ein Dialog."
            return False, "Keine Verbindung. Geraet aus, nicht im Netz, oder 'adb tcpip 5555' fehlt."

        if cmd.name == commands.SCREENSHOT:
            if not adb.ensure_connected():
                return False, "Keine Verbindung zum Geraet."
            return True, save_screenshot(cfg, adb.screenshot())

        if cmd.name == commands.CHECK:
            return True, f"Zustand: {adb.state()}"
    except Exception as exc:  # noqa: BLE001 - ein kaputter Auftrag darf den Dienst nicht stoppen
        log.warning("Auftrag %s fehlgeschlagen: %s", cmd.name, exc)
        return False, f"Fehlgeschlagen: {exc}"
    return False, "Unbekannter Auftrag."


def save_screenshot(cfg: Config, image: bytes, now: datetime | None = None) -> str:
    """Screenshot ablegen und die aeltesten wegraeumen. Gibt den Dateinamen zurueck.

    Der Name endet auf 'manual', damit die Weboberflaeche ihn ausliefern darf -- sie prueft
    den Namen streng, statt Pfade zu bereinigen.
    """
    now = now or datetime.now()
    shots = cfg.data_dir / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    name = f"{now:%Y%m%d-%H%M%S}-manual.png"
    (shots / name).write_bytes(image)
    for old in sorted(shots.glob("*.png"))[:-30]:  # dieselbe Grenze wie bei Fehlerbildern
        old.unlink(missing_ok=True)
    return name


def process_commands(cfg: Config, adb: Adb, store: Store) -> int:
    """Alle offenen Auftraege abarbeiten, aeltester zuerst. Gibt die Anzahl zurueck."""
    done = 0
    for cmd in commands.take(cfg.data_dir):
        log.info("Auftrag aus der Weboberflaeche: %s", cmd.label)
        ok, message = run_command(cfg, adb, store, cmd)
        commands.finish(cfg.data_dir, cmd, ok, message)
        log.info("Auftrag %s: %s -- %s", cmd.label, "erledigt" if ok else "fehlgeschlagen", message)
        done += 1
    return done


def tick(
    base_cfg: Config,
    adb: Adb,
    store: Store,
    reconnect: Reconnect,
    version: str = "",
    announced: date | None = None,
    now: datetime | None = None,
    outage: Outage | None = None,
    battery: BatteryWatch | None = None,
    publisher: homeassistant.Publisher | None = None,
) -> date | None:
    """Eine Runde des Dienstes. Gibt zurueck, fuer welchen Tag der Plan zuletzt gemeldet wurde.

    Eigene Funktion, damit die Schleife pruefbar ist -- die Reihenfolge darin ist nicht
    beliebig, und genau daran ist schon einmal etwas haengengeblieben.
    """
    now = now or datetime.now()
    cfg = with_current_settings(base_cfg)
    touch_heartbeat(cfg)
    state = report_status(cfg, adb, version, reconnect, now)

    # Faellt das Geraet laenger aus, sagt der Dienst Bescheid -- sonst merkt man erst am
    # ausbleibenden Erfolg, dass tagelang nichts lief. Je Ausfall genau eine Meldung.
    if outage is not None:
        # Dauer vor note() ablesen: die Entwarnung setzt den Beginn zurueck, danach waere sie null.
        minutes = outage.minutes(now)
        event = outage.note(state == "device", now, cfg.offline_alert_min)
        if event and cfg.notify_on_offline:
            message = offline_message(minutes, cfg.adb_serial) if event == "down" else back_message(minutes)
            notify.send(cfg.discord_webhook, message)

    # Der Akku, in groesserem Abstand als der Takt. Das ist die einzige Warnung, die vor einem
    # toten Netzteil kommt, bevor das Geraet ausgeht -- danach waere auch ADB weg.
    if battery is not None and state == "device":
        check_battery(cfg, adb, battery, now)

    # Eine Auftragsdatei aus einer aelteren Version der Oberflaeche: weiter annehmen,
    # damit ein Update ohne Neustart der Oberflaeche nichts verschluckt.
    if take_request(cfg):
        log.info("Lauf ueber die Weboberflaeche angefordert (alte Auftragsdatei)")
        result = run_once(cfg, adb, force=True)
        handle_result(cfg, store, "manual", result)

    # Nach einem Auftrag noch einmal melden. Sonst zeigte die Seite bis zum naechsten Takt
    # weiter den alten Zustand: ein erfolgreiches "Neu verbinden" waere bis zu 30 Sekunden
    # lang unsichtbar, und es saehe aus, als haette der Knopf nichts getan.
    if process_commands(cfg, adb, store):
        report_status(cfg, adb, version, reconnect, datetime.now())

    # Erfolg gemeldet, aber der Muenzstand bewegt sich nicht: dann laeuft zwar alles, bringt aber
    # nichts ein. Genau das blieb tagelang unbemerkt, weil die Quote auf 100 Prozent stand.
    if cfg.notify_on_stall:
        stall = stats.stalled_since(store.recent(400))
        if stall_due(cfg.data_dir, stall):
            notify.send(cfg.discord_webhook, stall_message(stall))
            _mark_stall(cfg.data_dir, stall)

    # Montagmorgens ein Rueckblick auf die Woche. Tag und Uhrzeit sind bewusst fest: noch zwei
    # Einstellungen fuer eine Meldung, die einmal die Woche kommt, waeren keine gewonnen.
    if cfg.notify_weekly and digest_due(cfg.data_dir, now.date(), DIGEST_WEEKDAY, DIGEST_HOUR, now.hour):
        notify.send(cfg.discord_webhook, digest_message(store.recent(400), now.date()))
        _mark_digest(cfg.data_dir, now.date())

    plan = plan_for(now.date(), cfg)
    if announced != now.date():
        log.info(
            "Heute: Morgenlauf ab %s, %s",
            plan.morning_at.strftime("%H:%M"),
            f"Abendlauf ab {plan.evening_at:%H:%M}" if cfg.evening_enabled else "Abendlauf abgeschaltet",
        )
        announced = now.date()

    decision = decide(now, plan, store.for_day(now.date()), cfg)
    if decision:
        log.info("Starte %s%s", decision.kind, " (erzwungen)" if decision.force else "")
        result = run_once(cfg, adb, force=decision.force)
        handle_result(cfg, store, decision.kind, result, final_attempt=decision.kind == "evening")
        # Der Lauf hat gerade frisch nachgesehen -- das gilt auch fuer den Waechter, sonst
        # fragte der gleich noch einmal dasselbe Geraet.
        if battery is not None and result.battery is not None:
            battery.note(result.battery, datetime.now(), cfg.battery_low_pct, cfg.battery_hot_c)

    # Ganz zum Schluss, damit der Zustand alles von dieser Runde enthaelt.
    if publisher is not None:
        recent = store.recent(1)
        entries = homeassistant.states(
            cfg,
            battery.last if battery is not None else None,
            recent[0] if recent else None,
            state == "device",
        )
        publisher.publish(cfg, entries, now)
    return announced


def daemon(cfg: Config, adb: Adb, store: Store, tick_s: int = 30, version: str = "") -> None:
    log.info(
        "Dienst gestartet. Fenster morgens %s-%s, abends %s-%s",
        cfg.morning_start,
        cfg.morning_end,
        cfg.evening_start,
        cfg.evening_end,
    )
    announced: date | None = None
    reconnect, outage, battery = Reconnect(), Outage(), BatteryWatch()
    publisher = homeassistant.Publisher() if cfg.ha_url else None
    if publisher is not None:
        log.info("Home Assistant angebunden, Entitaeten mit dem Praefix %s", cfg.ha_prefix)
    while True:
        announced = tick(
            cfg, adb, store, reconnect, version, announced, outage=outage, battery=battery, publisher=publisher
        )
        time.sleep(tick_s)
