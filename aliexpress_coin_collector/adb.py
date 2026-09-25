from __future__ import annotations

import logging
import re
import shlex
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

PNG_MAGIC = b"\x89PNG"

# Zahlen aus android.os.BatteryManager. Sie stehen so in der Ausgabe von 'dumpsys battery'.
# Die Beschriftungen gehen in die Oberflaeche, in die Datenbank und nach Discord, nie an die
# Shell -- darum hier mit Umlauten, anders als in den Log-Texten.
BATTERY_STATUS = {1: "unbekannt", 2: "lädt", 3: "entlädt", 4: "lädt nicht", 5: "voll"}
BATTERY_HEALTH = {
    1: "unbekannt",
    2: "gut",
    3: "überhitzt",
    4: "defekt",
    5: "Überspannung",
    6: "Fehler",
    7: "zu kalt",
}
CHARGING = (2, 5)


@dataclass(frozen=True)
class Battery:
    """Was 'dumpsys battery' ueber den Akku sagt.

    Alle Werte koennen fehlen: nicht jedes Geraet meldet jedes Feld, und eine unvollstaendige
    Auskunft ist besser als gar keine. Wer damit rechnet, prueft auf None.
    """

    level: int | None = None
    status: int | None = None
    health: int | None = None
    temperature_c: float | None = None
    voltage_mv: int | None = None
    plugged: bool = False

    @property
    def charging(self) -> bool:
        return self.status in CHARGING

    @property
    def status_label(self) -> str:
        return BATTERY_STATUS.get(self.status, "unbekannt")

    @property
    def health_label(self) -> str:
        return BATTERY_HEALTH.get(self.health, "unbekannt")

    @property
    def healthy(self) -> bool:
        """Falsch nur bei einer ausdruecklichen Fehlermeldung des Geraets.

        'unbekannt' und ein fehlender Wert gelten als in Ordnung -- sonst warnte der Dienst
        auf Geraeten, die den Wert schlicht nicht liefern, jeden Tag ohne Anlass.
        """
        return self.health is None or self.health in (1, 2)

    @property
    def empty(self) -> bool:
        return self.level is None and self.status is None


_FIELDS = {
    "level": "level",
    "status": "status",
    "health": "health",
    "temperature": "temperature",
    "voltage": "voltage",
}


def parse_battery(text: str) -> Battery:
    """Die Ausgabe von 'dumpsys battery' in Zahlen uebersetzen.

    Reine Funktion, damit sie sich ohne Geraet an echten Ausgaben pruefen laesst. Die Ausgabe
    ist ueber Android-Versionen hinweg erstaunlich stabil, aber nicht jedes Feld ist ueberall
    dabei -- was fehlt, bleibt None.
    """
    values: dict[str, int] = {}
    for key, name in _FIELDS.items():
        match = re.search(rf"^\s*{name}:\s*(-?\d+)\s*$", text, re.IGNORECASE | re.MULTILINE)
        if match:
            values[key] = int(match.group(1))
    plugged = any(
        re.search(rf"^\s*{quelle} powered:\s*true\s*$", text, re.IGNORECASE | re.MULTILINE)
        for quelle in ("AC", "USB", "Wireless", "Dock")
    )
    temperature = values.get("temperature")
    return Battery(
        level=values.get("level"),
        status=values.get("status"),
        health=values.get("health"),
        # Zehntelgrad, so meldet es Android.
        temperature_c=None if temperature is None else round(temperature / 10, 1),
        voltage_mv=values.get("voltage"),
        plugged=plugged,
    )


class AdbError(RuntimeError):
    pass


class Adb:
    """Duenner Wrapper um das adb-Kommandozeilenprogramm (ein Geraet, per TCP oder USB)."""

    def __init__(self, serial: str, adb_path: str = "adb") -> None:
        self.serial = serial
        self.adb_path = adb_path

    # -- Grundfunktionen ---------------------------------------------------
    def _run(self, args: list[str], timeout: int = 30, with_serial: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.adb_path] + (["-s", self.serial] if with_serial else []) + args
        try:
            return subprocess.run(cmd, capture_output=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise AdbError(f"adb nicht gefunden ({self.adb_path}); Paket 'adb' installieren") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb-Timeout bei: {' '.join(args)}") from exc

    def shell(self, command: str, timeout: int = 30) -> str:
        res = self._run(["shell", command], timeout=timeout)
        if res.returncode != 0:
            err = res.stderr.decode(errors="replace").strip() or res.stdout.decode(errors="replace").strip()
            raise AdbError(f"adb shell {command!r} fehlgeschlagen: {err}")
        return res.stdout.decode(errors="replace")

    # -- Verbindung ----------------------------------------------------------
    def state(self) -> str:
        try:
            res = self._run(["get-state"], timeout=10)
        except AdbError:
            return "offline"
        return res.stdout.decode().strip() if res.returncode == 0 else "offline"

    def ensure_connected(self) -> bool:
        """True, wenn das Geraet im Zustand 'device' ist. Versucht bei TCP-Geraeten vorher ein connect."""
        if self.state() == "device":
            return True
        if ":" in self.serial:
            try:
                res = self._run(["connect", self.serial], timeout=15, with_serial=False)
                log.debug("adb connect: %s", res.stdout.decode(errors="replace").strip())
            except AdbError as exc:
                log.warning("adb connect fehlgeschlagen: %s", exc)
                return False
        return self.state() == "device"

    # -- Bildschirm ------------------------------------------------------------
    def is_awake(self) -> bool:
        out = self.shell("dumpsys power")
        match = re.search(r"mWakefulness=(\w+)", out)
        return bool(match and match.group(1) == "Awake")

    def wake(self) -> None:
        self.shell("input keyevent KEYCODE_WAKEUP")
        try:
            self.shell("wm dismiss-keyguard")
        except AdbError:
            pass  # ohne Sperre nicht noetig

    def sleep_screen(self) -> None:
        self.shell("input keyevent KEYCODE_HOME")
        self.shell("input keyevent KEYCODE_SLEEP")

    def screenshot(self) -> bytes:
        res = self._run(["exec-out", "screencap", "-p"], timeout=25)
        if res.returncode != 0 or not res.stdout.startswith(PNG_MAGIC):
            raise AdbError("Screenshot fehlgeschlagen (kein gueltiges PNG)")
        return res.stdout

    # -- App / Eingaben ----------------------------------------------------------
    def force_stop(self, package: str) -> None:
        self.shell(f"am force-stop {shlex.quote(package)}")

    def start_url(self, url: str, package: str) -> None:
        self.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(url)} {shlex.quote(package)}")

    def tap(self, x: int, y: int) -> None:
        self.shell(f"input tap {int(x)} {int(y)}")

    # -- Akku ----------------------------------------------------------------
    def battery(self) -> Battery:
        """Ladestand, Temperatur und Zustand des Akkus.

        Ein reiner Lesezugriff: 'dumpsys battery' weckt den Bildschirm nicht und tippt nichts
        an. Deshalb darf er auch ausserhalb eines Laufs regelmaessig kommen.
        """
        return parse_battery(self.shell("dumpsys battery", timeout=15))
