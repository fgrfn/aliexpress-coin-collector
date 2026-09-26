import os
from pathlib import Path

import pytest

from aliexpress_coin_collector.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    os.environ["ADB_SERIAL"] = "10.0.0.5:5555"
    os.environ["DATA_DIR"] = str(tmp_path)
    os.environ["PAGE_TIMEOUT_S"] = "2"
    os.environ["LAUNCH_RETRIES"] = "0"
    # Ein Durchgang je Aufgabe. Die Attrappen lassen eine Karte nie abgehakt werden, sonst
    # liefen alle Tests dreimal durch dieselbe Aufgabe. Wiederholungen pruefen die Tests, die
    # sie meinen -- sie setzen den Wert selbst hoch (siehe tests/test_extras.py).
    os.environ["EXTRAS_REPEATS"] = "1"
    return Config.load(tmp_path / "does-not-exist.env")
