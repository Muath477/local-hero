"""Decides which agent role handles an incoming message.

Two-stage routing:
  1. Semantic (embeddings + cosine similarity) — near-instant, no LLM call.
     Each role has a handful of example phrases; we embed the user message
     once and compare against all of them.
  2. LLM fallback — only fires when the best semantic match is ambiguous
     (top score below CONFIDENCE_THRESHOLD, or top-2 scores are too close).
     Uses the router model from config/models.yaml, constrained to a JSON schema of role names.

This keeps routing cheap: stage 1 costs one embedding call; stage 2 (rare)
costs one small-model generation.
"""
from __future__ import annotations

import json
import math
import re

from . import ollama_client as ollama
from .hardware import load_registry

# Calibrated for qwen3-embedding:0.6b on the dev set (benchmarks/routing_eval.py --sweep); at these values
# the semantic stage decides ~70-85% of messages at 91-100% accuracy and defers the rest to stage 2.
# They are specific to the embedder: nomic-embed-text scored everything ~0.8 and needed 0.62 / 0.05.
CONFIDENCE_THRESHOLD = 0.45
MARGIN_THRESHOLD = 0.03  # if top two scores are within this margin, treat as ambiguous

# Seed examples per role — extend these freely, they're the entire "training set".
# Note: short imperative phrases like "اكتب لي X" repeat across roles (writer vs
# coder both start with "اكتب لي"), which pulls their embeddings close together.
# Counter that by loading up on domain-specific nouns (بايثون/كود/دالة/سكربت)
# in every coder example rather than relying on the verb alone to disambiguate.
ROLE_EXAMPLES: dict[str, list[str]] = {
    "writer_general": [
        "اكتب لي إيميل رسمي",
        "لخص لي هذا النص",
        "ساعدني أصيغ فقرة عن",
        "ايش رأيك في هذا الموضوع",
        "write a short blog post about",
        "summarize this paragraph",
        "اشرح لي بشكل مبسط",
        "اعطني أفكار واقتراحات",
        "اكتب قصة قصيرة",
        "ساعدني أكتب رسالة",
        "explain this in simple words",
        "give me tips on",
        "what are the pros and cons of",
    ],
    "coder": [
        "اكتب لي كود بايثون يسوي",
        "اكتب لي دالة بايثون تحسب",
        "اكتب سكربت برمجي يقوم بـ",
        "صحح لي هذا الخطأ في الكود",
        "أبغى سكربت يقرأ ملف csv",
        "اشرح لي هذا الكود البرمجي",
        "حول هذا الكود من لغة إلى لغة",
        "write a python function that",
        "write a script that",
        "debug this python script",
        "fix this error in my code",
        "refactor this function",
        "ليش الكود يعطيني خطأ",
        "اكتب استعلام SQL",
        "أمر git لـ",
        "regex للتحقق من",
        "اكتب اختبارات للدالة",
        "how do I do this in javascript",
        "write a SQL query to",
        "write unit tests for",
        "why do I get this error",
        "ايش الفرق بين X و Y في لغة برمجة",
        "what is the difference between a class and a function in python",
    ],
    "researcher_rag": [
        "اقرأ هذا الملف ولخصه",
        "ايش يقول التقرير المرفق عن",
        "search this pdf for",
        "answer based on the uploaded document",
        "استخرج لي المعلومات من الملف",
        "لخص لي الملف المرفوع",
        "حسب المستند المرفق",
        "قارن بين الملفين",
        "ايش يقول العقد عن",
        "what does the attached file say about",
        "according to the uploaded report",
        "find the section in the document about",
        "extract the key points from the attached file",
    ],
    "tool_caller": [
        "ابحث لي في الإنترنت عن",
        "افتح لي هذا الرابط",
        "افتح لي رابط ولخص لي محتواه",
        "اجلب لي محتوى هذا الموقع",
        "اقرأ لي هذه الصفحة من الإنترنت",
        "شغل هذا الأمر",
        "search the web for",
        "fetch this URL and summarize it",
        "run this command",
        "check the weather in",
        "كم سعر الذهب اليوم",
        "وش حالة الطقس في",
        "وش آخر الأخبار عن",
        "سعر صرف العملة الحين",
        "نتيجة مباراة أمس",
        "what is the current price of",
        "latest news about",
        "what's the exchange rate right now",
        "who won the game",
        "احسب لي",
        "كم يساوي هذا الحساب",
        "how much is",
        "calculate",
        "كم الساعة الحين في",
        "what time is it in",
        "وش الملفات اللي رفعتها",
        "list my uploaded files",
        "ترجم لي هذه الجملة",
        "ترجم الملف المرفوع إلى",
        "translate this sentence into",
        "translate the attached document into",
    ],
    "vision": [
        "ايش تشوف في هذي الصورة",
        "اشرح لي هذي الصورة",
        "what is in this image",
        "describe this picture",
    ],
}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# The text endpoints (CLI, /v1/chat/completions) never carry image bytes, so a
# text message routed to "vision" would reach moondream with no image and get
# a meaningless answer. run_vision() is the only door to that role.
DEFAULT_EXCLUDED = frozenset({"vision"})


def routable_roles(exclude=DEFAULT_EXCLUDED) -> list[str]:
    return [r for r in ROLE_EXAMPLES if r not in exclude]


# Routing-specific descriptions: models.yaml's `role` strings describe what a
# model *does*, which is too vague to separate "explain this concept" (writer)
# from "explain this code" (coder) or "summarize the file I uploaded" (rag)
# from "summarize this paragraph" (writer). Each line states the deciding cue.
ROUTER_DESCRIPTIONS = {
    "writer_general": "everything conversational or textual: writing, rewriting or summarizing text, "
                      "explaining concepts, advice, brainstorming, general questions",
    "coder": "programming: writing, fixing, explaining or converting code, scripts, SQL, regex, "
             "shell/git commands, error messages",
    "researcher_rag": "questions about a file, document, PDF, report or contract the user uploaded or "
                      "attached (\"the attached file\", \"the report\", \"the document\")",
    "tool_caller": "needs live data or an action: searching the web, current news/weather/prices/scores, "
                   "opening or fetching a URL, running a command, arithmetic calculations, the current time or "
                   "date, listing the uploaded files, translating text or files",
    "vision": "describing or analysing an image",
}
FEW_SHOT_PER_ROLE = 4


def _spread(items: list[str], n: int) -> list[str]:
    """n examples evenly spaced through the list, so the prompt covers the whole
    range of a role (Arabic and English, all sub-types) instead of just its first few."""
    if len(items) <= n:
        return list(items)
    return [items[i * len(items) // n] for i in range(n)]


def router_system_prompt(registry: dict, roles: list[str] | None = None) -> str:
    roles = roles or list(ROLE_EXAMPLES.keys())
    role_lines = "\n".join(f'- "{r}": {ROUTER_DESCRIPTIONS.get(r, registry[r]["role"])}' for r in roles)
    examples = "\n".join(f'"{ex}" -> {r}' for r in roles for ex in _spread(ROLE_EXAMPLES[r], FEW_SHOT_PER_ROLE))
    return (
        "You are a strict task router. Pick exactly one role for the user's message:\n"
        f"{role_lines}\n\n"
        f"Examples:\n{examples}\n\n"
        f'Reply with ONLY a JSON object: {{"role": "<one of {roles}>"}}. No prose, no explanation.'
    )


def role_schema(roles: list[str]) -> dict:
    """Ollama structured output: the reply is *constrained* to one of these
    role names, so even a 0.5B model can't answer with prose or a made-up role."""
    return {"type": "object", "properties": {"role": {"type": "string", "enum": roles}}, "required": ["role"]}


def parse_role(raw: str, allowed: list[str] | None = None) -> str | None:
    """Extracts the role from an LLM router reply, or None if unusable. Small
    models often wrap the JSON in a ```json fence or add a sentence around
    it, so pull out the first {...} instead of requiring a bare object.
    """
    raw = raw.strip()
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    try:
        role = json.loads(match.group(0) if match else raw).get("role")
    except (json.JSONDecodeError, AttributeError):
        return None
    return role if role in (allowed or ROLE_EXAMPLES) else None


class Router:
    def __init__(self):
        self.registry = load_registry()
        self.embedder_tag = self.registry["embedder"]["ollama_tag"]
        self.router_tag = self.registry["router"]["ollama_tag"]
        self._role_vectors: dict[str, list[list[float]]] = {}

    def warm_up(self):
        """Pre-embeds every seed example once, in a single batched call
        (see ollama_client.embed_batch — ~25x faster than one call per
        example). Call this at startup, not per-request.
        """
        roles = list(ROLE_EXAMPLES.keys())
        flat_texts = [ex for role in roles for ex in ROLE_EXAMPLES[role]]
        flat_vectors = ollama.embed_batch(self.embedder_tag, flat_texts)

        i = 0
        for role in roles:
            n = len(ROLE_EXAMPLES[role])
            self._role_vectors[role] = flat_vectors[i:i + n]
            i += n

    def _semantic_route(self, message: str, exclude=DEFAULT_EXCLUDED) -> tuple[str, float, float]:
        if not self._role_vectors:
            self.warm_up()
        msg_vec = ollama.embed(self.embedder_tag, message)

        scores: list[tuple[str, float]] = []
        for role, vectors in self._role_vectors.items():
            if role in exclude:
                continue
            best = max(_cosine(msg_vec, v) for v in vectors)
            scores.append((role, best))
        scores.sort(key=lambda kv: kv[1], reverse=True)

        top_role, top_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else 0.0
        return top_role, top_score, top_score - second_score

    def _llm_route(self, message: str, exclude=DEFAULT_EXCLUDED) -> str:
        roles = routable_roles(exclude)
        result = ollama.chat(
            model=self.router_tag,
            messages=[
                {"role": "system", "content": router_system_prompt(self.registry, roles)},
                {"role": "user", "content": message},
            ],
            keep_alive=self.registry["router"]["keep_alive"],
            temperature=0.0,
            format=role_schema(roles),
            think=self.registry["router"].get("think"),
        )
        # safe default when even the LLM router can't produce a parseable answer
        return parse_role(result["message"]["content"], roles) or "writer_general"

    def route(self, message: str, exclude=DEFAULT_EXCLUDED) -> dict:
        role, score, margin = self._semantic_route(message, exclude)
        if score >= CONFIDENCE_THRESHOLD and margin >= MARGIN_THRESHOLD:
            return {"role": role, "method": "semantic", "confidence": round(score, 3)}

        role = self._llm_route(message, exclude)
        return {"role": role, "method": "llm_fallback", "confidence": None}
