"""Quality gate for config/models.yaml: run the benchmark cases against the
model each role is CURRENTLY configured with and fail if it drops below the
bar. Slow (CPU inference) so it's opt-in:

    pytest -m live                      # every configured role
    pytest -m live -k coder             # just one

Change a tag in models.yaml, run this, and you know whether the swap is safe.
A role whose model isn't pulled is skipped rather than failed.
"""
import pytest

from benchmarks.run_benchmark import run_category
from benchmarks.tasks import ROLE_CATEGORY
from orchestrator.hardware import load_registry

REGISTRY = load_registry()

# Minimum pass rate per role. Deliberately below what the current picks score
# (see benchmarks/RESULTS.md) so normal sampling noise doesn't flake the gate,
# but high enough that a model that breaks the role's core job fails it.
MIN_PASS_RATE = {
    "writer_general": 0.80,   # measured 6/6
    "coder": 0.85,            # 8/8
    "tool_caller": 0.80,      # 11/11 (qwen2.5:3b)
    "council": 0.80,
    "router": 0.85,           # 8/8 (Qwen3-4B stage-2 prompt; the semantic stage decides most messages first)
    "researcher_rag": 0.75,   # 4/4
    "tarjuman": 0.75,         # 13/16 — includes 8 contract-grade cases with known-wrong-answer markers
}


@pytest.mark.live
@pytest.mark.parametrize("role", sorted(ROLE_CATEGORY))
def test_configured_model_meets_the_bar(role, ollama_tags):
    tag = REGISTRY[role]["ollama_tag"]
    if tag not in ollama_tags:
        pytest.skip(f"{tag} isn't pulled")

    category = ROLE_CATEGORY[role]
    rows = run_category(tag, category, log=lambda s: None)
    rate = sum(r["passed"] for r in rows) / len(rows)
    failures = [f"{r['case']}: {r['detail']}" for r in rows if not r["passed"]]
    assert rate >= MIN_PASS_RATE[role], (
        f"{role} -> {tag} passed {rate:.0%} of '{category}' (needs {MIN_PASS_RATE[role]:.0%}). Failures:\n  "
        + "\n  ".join(failures)
    )
