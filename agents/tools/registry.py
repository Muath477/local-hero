"""Central tool registry.

Every tool is a plain Python function decorated with @tool. The decorator
captures the function's name, description and a JSON schema for its
arguments — that schema is exactly what gets sent to Ollama's function
calling API, so writing a new tool never touches the orchestrator.

Parameter types come from the annotations (int/float/bool/list/str), and the
registered callable coerces what small models actually send — "5" for an int,
"true" for a bool — and drops arguments the function doesn't declare, instead
of failing the whole call over a stray key.

Retrieval: with only a handful of tools, keyword matching is plenty and avoids
spinning up ChromaDB. Tool descriptions are English but users write Arabic, so
each tool can declare `keywords` (Arabic + English trigger words) that are
matched against the message as substrings — Arabic attaches prefixes
(ال / و / ب) to words, so token equality would miss them. Once the list grows
past ~15-20 tools, swap `search_tools` to embed each tool description once at
startup and do the same cosine-similarity lookup the router already uses.
"""
from __future__ import annotations

import inspect
import re
import typing
from typing import Callable

_REGISTRY: dict[str, dict] = {}

_STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "you", "your", "are", "was", "use", "when"}
_WORD = re.compile(r"\w+", re.UNICODE)
_TRUE, _FALSE = {"true", "yes", "1", "y", "نعم"}, {"false", "no", "0", "n", "لا"}


def _json_type(annotation) -> str:
    origin = typing.get_origin(annotation) or annotation
    if origin is bool:
        return "boolean"
    if origin is int:
        return "integer"
    if origin is float:
        return "number"
    if origin in (list, tuple, set):
        return "array"
    if origin is dict:
        return "object"
    return "string"


def _coerce(value, annotation):
    """Best-effort conversion of a model-supplied value to the declared type."""
    target = typing.get_origin(annotation) or annotation
    if not isinstance(value, str) or target not in (int, float, bool):
        return value
    text = value.strip()
    try:
        if target is bool:
            low = text.casefold()
            return True if low in _TRUE else False if low in _FALSE else value
        return target(float(text)) if target is int and re.fullmatch(r"-?\d+\.0+", text) else target(text)
    except ValueError:
        return value


def tool(description: str, keywords: tuple[str, ...] | list[str] = (), params: dict[str, str] | None = None):
    def decorator(fn: Callable):
        sig = inspect.signature(fn)
        try:
            hints = typing.get_type_hints(fn)
        except Exception:  # unresolved forward reference: fall back to all-strings
            hints = {}
        props, required = {}, []
        for name, param in sig.parameters.items():
            props[name] = {"type": _json_type(hints.get(name)), "description": (params or {}).get(name, name)}
            if param.default is inspect.Parameter.empty:
                required.append(name)

        accepts_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        def call(**kwargs):
            if not accepts_any:
                kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return fn(**{k: _coerce(v, hints.get(k)) for k, v in kwargs.items()})

        _REGISTRY[fn.__name__] = {
            "fn": call,
            "keywords": [k.casefold() for k in keywords],
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {"type": "object", "properties": props, "required": required},
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


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.casefold()) if len(w) > 2 and w not in _STOPWORDS}


def search_tools(query: str, top_k: int = 5, only: typing.Iterable[str] | None = None,
                 exclude: typing.Iterable[str] = ()) -> list[dict]:
    """Ranks tools by declared keyword hits (weighted) plus description word
    overlap; ties keep registration order. `only` / `exclude` scope the pool
    per agent role. See the module docstring for the embedding-based upgrade
    path once the tool count grows.
    """
    low = query.casefold()
    words = _tokens(query)
    allowed = set(only) if only is not None else None
    banned = set(exclude)
    scored = []
    for name, entry in _REGISTRY.items():
        if name in banned or (allowed is not None and name not in allowed):
            continue
        keyword_hits = sum(1 for k in entry.get("keywords", ()) if k in low)
        overlap = len(words & _tokens(entry["schema"]["function"]["description"]))
        scored.append((3 * keyword_hits + overlap, entry["schema"]))
    scored.sort(key=lambda x: x[0], reverse=True)  # stable: equal scores keep registration order
    return [schema for _, schema in scored[:top_k]]


# Importing this module registers every built-in tool via their @tool decorators.
from . import web_search, file_reader, agent_tools, builtin_tools  # noqa: E402,F401
