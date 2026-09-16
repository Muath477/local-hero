"""Central tool registry.

Every tool is a plain Python function decorated with @tool. The decorator
captures the function's name, description and a JSON schema for its
arguments — that schema is exactly what gets sent to Ollama's function
calling API, so writing a new tool never touches the orchestrator.

Retrieval: with only a handful of tools, a keyword/description match is
plenty and avoids spinning up ChromaDB. Once this list grows past ~15-20
tools, swap `search_tools` to embed each tool description once at startup
and do the same cosine-similarity lookup the router already uses — the
hook point is marked below.
"""
from __future__ import annotations

import inspect
from typing import Callable

_REGISTRY: dict[str, dict] = {}


def tool(description: str):
    def decorator(fn: Callable):
        sig = inspect.signature(fn)
        params = {}
        required = []
        for name, param in sig.parameters.items():
            params[name] = {"type": "string", "description": name}
            if param.default is inspect.Parameter.empty:
                required.append(name)

        _REGISTRY[fn.__name__] = {
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": required,
                    },
                },
            },
        }
        return fn
    return decorator


def get_tool(name: str) -> Callable | None:
    entry = _REGISTRY.get(name)
    return entry["fn"] if entry else None


def all_schemas() -> list[dict]:
    return [entry["schema"] for entry in _REGISTRY.values()]


def search_tools(query: str, top_k: int = 5) -> list[dict]:
    """Keyword-overlap ranking for now (see module docstring for the
    embedding-based upgrade path once the tool count grows).
    """
    query_words = set(query.lower().split())
    scored = []
    for entry in _REGISTRY.values():
        desc = entry["schema"]["function"]["description"].lower()
        overlap = sum(1 for w in query_words if w in desc)
        scored.append((overlap, entry["schema"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [schema for _, schema in scored[:top_k]] if scored else all_schemas()[:top_k]


# Importing this module registers every built-in tool via their @tool decorators.
from . import web_search, file_reader, agent_tools  # noqa: E402,F401
