"""Detects the running machine's tier so the platform degrades gracefully
on weaker hardware than the dev machine, instead of assuming 16GB/CPU always.
"""
from __future__ import annotations

import psutil
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models.yaml"


def total_ram_gb() -> float:
    return round(psutil.virtual_memory().total / (1024 ** 3), 1)


def resolve_tier() -> str:
    """Pick the weakest tier whose max_ram_gb still covers this machine,
    so a machine under every threshold falls back to 'min' rather than crashing.
    """
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    ram = total_ram_gb()
    tiers = cfg["tiers"]
    ordered = sorted(tiers.items(), key=lambda kv: kv[1]["max_ram_gb"])
    for name, spec in ordered:
        if ram <= spec["max_ram_gb"]:
            return name
    return ordered[-1][0]  # bigger than every defined tier -> use the top one


def load_registry() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    print(f"Detected RAM: {total_ram_gb()} GB -> tier: {resolve_tier()}")
