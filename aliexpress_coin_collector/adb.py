from __future__ import annotations

import logging
import re
import shlex
import subprocess

log = logging.getLogger(__name__)

PNG_MAGIC = b"\x89PNG"


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
