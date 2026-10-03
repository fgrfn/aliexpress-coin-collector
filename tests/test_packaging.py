from importlib.metadata import version

from aliexpress_coin_collector import __version__


def test_distribution_and_runtime_report_the_same_version():
    """Die gebaute Paketversion darf nicht mehr hinter der Laufzeitversion zurueckbleiben."""
    assert version("aliexpress-coin-collector") == __version__
