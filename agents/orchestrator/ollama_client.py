"""Thin wrapper around Ollama's local REST API (http://localhost:11434).
No SDK dependency — Ollama's API is simple enough that a raw client keeps
the project lighter and easier to debug.
"""
from __future__ import annotations

import os

import requests

# Overridable so a containerized deployment can point at the host machine's
# Ollama (e.g. http://host.docker.internal:11434) instead of its own
# loopback, which inside a container refers to the container itself.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


def chat(model: str, messages: list[dict], keep_alive: str = "5m",
         tools: list[dict] | None = None, temperature: float = 0.3,
         think: bool | None = None, num_predict: int | None = None,
         format: dict | str | None = None) -> dict:
    """`think=False` switches off the hidden reasoning trace of hybrid
    "thinking" models (Qwen3 etc.) — on CPU that trace costs minutes per
    reply. Leave it None for models without a thinking mode: Ollama rejects
    the flag for them. `num_predict` caps generated tokens (a runaway loop on
    CPU costs minutes); `format` is Ollama's structured-output switch — "json"
    or a JSON schema the reply is constrained to.
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"temperature": temperature},
    }
    if tools:
        payload["tools"] = tools
    if think is not None:
        payload["think"] = think
    if num_predict is not None:
        payload["options"]["num_predict"] = num_predict
    if format is not None:
        payload["format"] = format
    resp = requests.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()


def embed(model: str, text: str) -> list[float]:
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def embed_batch(model: str, texts: list[str]) -> list[list[float]]:
    """One round trip for many inputs instead of one per text — measured
    25x faster for router warm-up (33 short phrases: ~74s sequential vs
    ~3s batched) since each call otherwise pays fixed per-request overhead.
    """
    resp = requests.post(
        f"{OLLAMA_HOST}/api/embed",
        json={"model": model, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def is_alive() -> bool:
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=3)
        return r.status_code == 200
    except requests.RequestException:
        return False


def ensure_model_pulled(tag: str) -> bool:
    """Checks the tag is already present locally — the orchestrator never
    triggers a pull itself (that's a multi-GB download the user should approve).
    """
    r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
    r.raise_for_status()
    local_tags = {m["name"] for m in r.json().get("models", [])}
    return tag in local_tags
