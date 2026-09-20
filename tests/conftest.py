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
    return Config.load(tmp_path / "does-not-exist.env")
