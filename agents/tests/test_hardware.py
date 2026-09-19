import pytest

from orchestrator import hardware


@pytest.mark.parametrize(
    "ram, expected",
    [(4, "min"), (8, "min"), (8.1, "std"), (15.8, "std"), (16, "std"), (16.1, "high"), (32, "high"), (128, "high")],
)
def test_resolve_tier(monkeypatch, ram, expected):
    monkeypatch.setattr(hardware, "total_ram_gb", lambda: ram)
    assert hardware.resolve_tier() == expected


def test_total_ram_is_positive():
    assert hardware.total_ram_gb() > 0
