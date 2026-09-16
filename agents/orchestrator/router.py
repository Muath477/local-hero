"""Decides which agent role handles an incoming message.

Two-stage routing:
  1. Semantic (embeddings + cosine similarity) — near-instant, no LLM call.
     Each role has a handful of example phrases; we embed the user message
     once and compare against all of them.
  2. LLM fallback — only fires when the best semantic match is ambiguous
     (top score below CONFIDENCE_THRESHOLD, or top-2 scores are too close).
     Uses the tiny router model (qwen2.5:1.5b) to output strict JSON.

This keeps routing cheap: stage 1 costs one embedding call; stage 2 (rare)
costs one small-model generation.
"""
from __future__ import annotations

import json
import math

from . import ollama_client as ollama
from .hardware import load_registry

CONFIDENCE_THRESHOLD = 0.62
MARGIN_THRESHOLD = 0.05  # if top two scores are within this margin, treat as ambiguous

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
    ],
    "researcher_rag": [
        "اقرأ هذا الملف ولخصه",
        "ايش يقول التقرير المرفق عن",
        "search this pdf for",
        "answer based on the uploaded document",
        "استخرج لي المعلومات من الملف",
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

    def _semantic_route(self, message: str) -> tuple[str, float, float]:
        if not self._role_vectors:
            self.warm_up()
        msg_vec = ollama.embed(self.embedder_tag, message)

        scores: list[tuple[str, float]] = []
        for role, vectors in self._role_vectors.items():
            best = max(_cosine(msg_vec, v) for v in vectors)
            scores.append((role, best))
        scores.sort(key=lambda kv: kv[1], reverse=True)

        top_role, top_score = scores[0]
        second_score = scores[1][1] if len(scores) > 1 else 0.0
        return top_role, top_score, top_score - second_score

    def _llm_route(self, message: str) -> str:
        roles = list(ROLE_EXAMPLES.keys())
        role_lines = "\n".join(
            f'- "{r}": {self.registry[r]["role"]}' for r in roles
        )
        system = (
            "You are a strict task router. Pick exactly one role for the user's message:\n"
            f"{role_lines}\n\n"
            f'Reply with ONLY a JSON object: {{"role": "<one of {roles}>"}}. No prose, no explanation.'
        )
        result = ollama.chat(
            model=self.router_tag,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": message},
            ],
            keep_alive=self.registry["router"]["keep_alive"],
            temperature=0.0,
        )
        raw = result["message"]["content"].strip()
        try:
            parsed = json.loads(raw)
            role = parsed.get("role")
            if role in ROLE_EXAMPLES:
                return role
        except (json.JSONDecodeError, AttributeError):
            pass
        return "writer_general"  # safe default when even the LLM router can't parse

    def route(self, message: str) -> dict:
        role, score, margin = self._semantic_route(message)
        if score >= CONFIDENCE_THRESHOLD and margin >= MARGIN_THRESHOLD:
            return {"role": role, "method": "semantic", "confidence": round(score, 3)}

        role = self._llm_route(message)
        return {"role": role, "method": "llm_fallback", "confidence": None}
