"""Skills: reusable, task-specific instructions kept as plain Markdown files.

A skill is a file in skills/ with a small header and a body:

    ---
    name: debugging-code
    description: one line, for humans
    roles: coder                     # comma-separated; omit = every role
    triggers: error, bug, traceback  # comma-separated; matched as substrings, case-insensitive
                                     # ("*" = always on for the listed roles)
    ---
    Instructions the model gets appended to its system prompt...

Only skills whose triggers appear in the user's message (and whose roles
include the chosen role) are injected, at most `max_skills` of them — a small
model's context is precious, and instructions that don't apply just dilute the
ones that do. Skills are data, never code: nothing here executes a file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    roles: tuple[str, ...]
    triggers: tuple[str, ...]
    body: str


def _split(value: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in value.split(",") if p.strip())


def parse_skill(text: str) -> Skill:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing '---' header")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("unterminated header") from None
    meta = {}
    for line in lines[1:end]:
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.split(" #")[0].strip()
    body = "\n".join(lines[end + 1:]).strip()
    if not meta.get("name") or not body:
        raise ValueError("a skill needs a name and a body")
    triggers = _split(meta.get("triggers", ""))
    if not triggers:
        raise ValueError("a skill needs at least one trigger")
    return Skill(meta["name"], meta.get("description", ""), _split(meta.get("roles", "")), triggers, body)


def load_skills(directory: Path | None = None) -> list[Skill]:
    """Loads every skills/*.md. A malformed file is logged and skipped — one bad
    skill must not stop the platform from starting."""
    directory = Path(directory) if directory else SKILLS_DIR
    skills = []
    for path in sorted(directory.glob("*.md")) if directory.exists() else []:
        try:
            skills.append(parse_skill(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as e:
            log.warning("skipping skill %s: %s", path.name, e)
    return skills


def match_skills(skills: list[Skill], role: str, message: str, max_skills: int = 2) -> list[Skill]:
    low = message.casefold()
    scored = []
    for order, skill in enumerate(skills):
        if skill.roles and role not in skill.roles:
            continue
        hits = sum(1 for t in skill.triggers if t != "*" and t.casefold() in low)
        if "*" in skill.triggers:
            hits = max(hits, 0.01)  # always-on for its roles, but outranked by a skill that actually matched
        if hits:
            scored.append((-hits, order, skill))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in scored[:max_skills]]


def render(skills: list[Skill]) -> str:
    return "".join(f"\n\n## Skill: {s.name}\n{s.body}" for s in skills)
