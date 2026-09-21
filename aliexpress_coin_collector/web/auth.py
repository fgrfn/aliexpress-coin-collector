"""Passwort der Weboberflaeche: vergeben, pruefen, aendern.

Das Passwort steht nicht mehr in der .env, sondern wird beim ersten Aufruf der Seite vergeben.
Gespeichert wird ausschliesslich ein Hash, nie das Passwort selbst.

Warum eine Datei in data/ und nicht die Datenbank: der Webdienst oeffnet coins.sqlite3
absichtlich schreibgeschuetzt, damit der Sammel-Dienst der einzige Schreiber bleibt. Das
Passwort in dieselbe Datenbank zu legen hiesse, genau diese Trennung wieder aufzugeben --
fuer einen einzigen Wert, der mit der Laufhistorie nichts zu tun hat.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

PASSWORD_FILE = "web-password"
MIN_LENGTH = 8
_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16


class PasswordError(ValueError):
    """Das gewuenschte Passwort ist nicht brauchbar. Der Text geht an den Nutzer."""


def _path(data_dir: Path) -> Path:
    return data_dir / PASSWORD_FILE


def _hash(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def stored_hash(data_dir: Path) -> str:
    """Die gespeicherte Hash-Zeile, oder "" wenn noch keine vergeben wurde."""
    try:
        return _path(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def is_set(data_dir: Path) -> bool:
    return bool(stored_hash(data_dir))


def check_new(password: str, repeat: str) -> None:
    """Prueft ein neues Passwort. Wirft PasswordError mit einem Text fuer die Oberflaeche."""
    if password != repeat:
        raise PasswordError("Die beiden Eingaben stimmen nicht überein.")
    if len(password) < MIN_LENGTH:
        raise PasswordError(f"Das Passwort muss mindestens {MIN_LENGTH} Zeichen haben.")
    if password.strip() != password:
        raise PasswordError("Das Passwort darf nicht mit einem Leerzeichen beginnen oder enden.")


def set_password(data_dir: Path, password: str, repeat: str, *, only_if_unset: bool = False) -> None:
    """Passwort setzen oder aendern.

    only_if_unset=True fuer die Ersteinrichtung: dann gewinnt der erste Schreiber, und zwei
    gleichzeitige Aufrufe koennen sich nicht gegenseitig ueberschreiben.
    """
    check_new(password, repeat)
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = _hash(password, salt, _ITERATIONS)
    line = f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}\n"

    data_dir.mkdir(parents=True, exist_ok=True)
    target = _path(data_dir)
    if only_if_unset:
        # O_EXCL statt vorheriger Pruefung: sonst bliebe zwischen Pruefung und Schreiben
        # ein Fenster, in dem ein zweiter Aufruf das Passwort ueberschreiben koennte.
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise PasswordError("Es wurde bereits ein Passwort vergeben.") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(line)
        return

    tmp = target.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(line)
    os.replace(tmp, target)


def verify(data_dir: Path, password: str) -> bool:
    """Passwort pruefen. Unbekanntes Format oder fehlende Datei gelten als "stimmt nicht"."""
    raw = stored_hash(data_dir)
    if not raw:
        return False
    try:
        algo, iterations, salt_hex, digest_hex = raw.split("$")
        if algo != _ALGO:
            return False
        expected = bytes.fromhex(digest_hex)
        actual = _hash(password, bytes.fromhex(salt_hex), int(iterations))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)
