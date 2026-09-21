"""config/models.yaml is the single source of truth for every role — a typo
here only shows up at runtime as a KeyError or an Ollama 404, so it's worth
validating up front."""
import re

import pytest

from orchestrator.agent_manager import SYSTEM_PROMPTS, TIER_ORDER, TOOL_ROLES
from orchestrator.hardware import load_registry
from orchestrator.router import ROLE_EXAMPLES

REGISTRY = load_registry()
ROLES = [k for k in REGISTRY if k != "tiers"]
HF_TAG = re.compile(r"^hf\.co/[\w.\-]+/[\w.\-]+:[\w.\-]+$")
LIBRARY_TAG = re.compile(r"^[\w.\-]+(:[\w.\-]+)?$")


def _valid_tag(tag: str) -> bool:
    return bool(HF_TAG.match(tag) or LIBRARY_TAG.match(tag))


@pytest.mark.parametrize("role", ROLES)
def test_role_has_required_fields(role):
    cfg = REGISTRY[role]
    assert isinstance(cfg["role"], str) and cfg["role"].strip()
    assert _valid_tag(cfg["ollama_tag"]), cfg["ollama_tag"]
    assert cfg["est_ram_gb"] > 0
    assert isinstance(cfg["keep_alive"], str)
    assert cfg["context_window"] >= 1024


@pytest.mark.parametrize("role", [r for r in ROLES if "fallback_tag" in REGISTRY[r]])
def test_fallback_is_well_formed(role):
    cfg = REGISTRY[role]
    assert _valid_tag(cfg["fallback_tag"])
    assert cfg["fallback_tier"] in TIER_ORDER
    assert cfg["fallback_tag"] != cfg["ollama_tag"]


def test_tiers_are_ordered_and_consistent():
    tiers = REGISTRY["tiers"]
    assert set(tiers) == set(TIER_ORDER)
    limits = [tiers[t]["max_ram_gb"] for t in TIER_ORDER]
    assert limits == sorted(limits) and len(set(limits)) == len(limits)
    for spec in tiers.values():
        for role in spec["disable_roles"]:
            assert role in REGISTRY, f"tier disables unknown role {role!r}"


def test_every_routable_role_is_fully_wired():
    for role in ROLE_EXAMPLES:
        assert role in REGISTRY, f"router can route to {role!r} but models.yaml has no entry"
        assert role in SYSTEM_PROMPTS, f"{role!r} has no system prompt"
    for role in TOOL_ROLES:
        assert role in REGISTRY and role in SYSTEM_PROMPTS


def test_only_one_heavy_model_fits_beside_router_and_embedder():
    """The whole design is 'one model generating at a time'. Router + embedder
    stay resident (keep_alive 30m), so the biggest role model on top of them
    must leave headroom on a 16GB machine for the OS and the Python process."""
    resident = REGISTRY["router"]["est_ram_gb"] + REGISTRY["embedder"]["est_ram_gb"]
    for role in ROLES:
        if role in ("router", "embedder"):
            continue
        assert resident + REGISTRY[role]["est_ram_gb"] <= 10, f"{role} too big for a 16GB box"


def test_router_and_embedder_stay_warm():
    for role in ("router", "embedder"):
        assert REGISTRY[role]["keep_alive"] not in ("0", "0s")
