"""Runs a single turn on whichever agent role the router picked.

Design choice driven by the hardware constraint: only ONE model is ever
asked to generate at a time. There is no concurrent multi-model fan-out —
each Ollama call passes `keep_alive` from the model's own config entry, so
Ollama unloads it after that window and the next role's model has full RAM
to load into. This is the "sequential handoff" model discussed with the
user instead of true parallel multi-agent execution, which a CPU-only
16GB machine (and anything weaker) cannot sustain.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from . import ollama_client as ollama
from .hardware import load_registry, resolve_tier
from .memory import DocumentStore, UPLOADS_DIR
from tools.registry import get_tool, search_tools

MAX_TOOL_ITERATIONS = 4
TIER_ORDER = ["min", "std", "high"]

SYSTEM_PROMPTS = {
    "writer_general": "أنت مساعد كتابة وتحليل عام. جاوب بإيجاز ودقة، وبنفس لغة المستخدم.",
    "coder": "أنت مبرمج خبير. اكتب كود صحيح وقابل للتشغيل مباشرة، مع شرح مختصر جداً إن لزم.",
    "researcher_rag": "أنت باحث يعتمد فقط على المحتوى المتاح له (ملفات مرفوعة). "
                       "إذا ما وجدت الجواب في المحتوى، قل ذلك صراحة بدل التخمين.",
    "tool_caller": "أنت وكيل تنفيذي. أنت تملك أدوات فعلية تصل للإنترنت وتقرأ الملفات — لست مقيداً "
                   "بمعرفتك السابقة. أي طلب فيه \"ابحث\" أو \"اجلب\" أو \"افتح رابط\" يعني استدعِ الأداة "
                   "المناسبة فوراً، ولا تقل أبداً إنك لا تستطيع الوصول للإنترنت أو البحث. "
                   "لا تخترع نتائج أدوات لم تستدعها فعلاً.",
    "vision": "صف وحلل الصورة المعطاة بدقة وبإيجاز.",
}


class AgentManager:
    def __init__(self):
        self.registry = load_registry()
        self.tier = resolve_tier()
        disabled = set(self.registry["tiers"][self.tier].get("disable_roles", []))
        self.disabled_roles = disabled
        self.documents = DocumentStore()

    def _model_for(self, role: str) -> dict:
        if role in self.disabled_roles:
            raise RuntimeError(
                f"Role '{role}' is disabled on this hardware tier ('{self.tier}')."
            )
        cfg = self.registry[role]

        # A role can declare a heavier fallback_tag gated behind fallback_tier
        # (e.g. coder's qwen2.5:7b, only worth the latency on tier >= "high").
        # Use the fallback whenever the running machine's tier meets or beats it.
        if "fallback_tag" in cfg:
            required = TIER_ORDER.index(cfg["fallback_tier"])
            current = TIER_ORDER.index(self.tier)
            if current >= required:
                cfg = {**cfg, "ollama_tag": cfg["fallback_tag"]}

        return cfg

    def run(self, role: str, user_message: str, history: list[dict] | None = None) -> dict:
        model_cfg = self._model_for(role)
        messages = [{"role": "system", "content": SYSTEM_PROMPTS.get(role, "")}]
        messages += history or []

        retrieved = []
        if role == "researcher_rag":
            retrieved = self.documents.query(user_message)
            messages.append({"role": "user", "content": self._build_rag_prompt(user_message, retrieved)})
        else:
            messages.append({"role": "user", "content": user_message})

        if role == "tool_caller":
            return self._run_with_tools(model_cfg, messages)

        result = ollama.chat(
            model=model_cfg["ollama_tag"],
            messages=messages,
            keep_alive=model_cfg["keep_alive"],
        )
        return {
            "role": role,
            "content": result["message"]["content"],
            "tool_trace": [],
            "sources": [r["source"] for r in retrieved] if retrieved else [],
        }

    def run_vision(self, image_filename: str, question: str) -> dict:
        """Separate from run() because vision needs actual image bytes, not
        just text — the caller (API/CLI) already knows which file the user
        attached, so this skips the router entirely rather than guessing
        intent from text alone.
        """
        model_cfg = self._model_for("vision")

        image_path = (UPLOADS_DIR / image_filename).resolve()
        if UPLOADS_DIR.resolve() not in image_path.parents and image_path != UPLOADS_DIR.resolve():
            raise ValueError("access outside the uploads directory is not allowed")
        if not image_path.exists():
            raise ValueError(f"'{image_filename}' not found in uploads")

        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPTS["vision"]},
            {"role": "user", "content": question or "صف هذه الصورة.", "images": [image_b64]},
        ]
        result = ollama.chat(
            model=model_cfg["ollama_tag"],
            messages=messages,
            keep_alive=model_cfg["keep_alive"],
        )
        return {"role": "vision", "content": result["message"]["content"], "tool_trace": [], "sources": [image_filename]}

    @staticmethod
    def _build_rag_prompt(user_message: str, retrieved: list[dict]) -> str:
        if not retrieved:
            return (
                f"{user_message}\n\n"
                "(No matching content was found in the uploaded documents — say so "
                "explicitly instead of guessing.)"
            )
        context = "\n\n".join(
            f"[{r['source']} — fragment {i + 1}]\n{r['text']}" for i, r in enumerate(retrieved)
        )
        return (
            f"Context from uploaded documents:\n{context}\n\n"
            f"Question: {user_message}\n\n"
            "Answer using only the context above. If it doesn't contain the answer, say so."
        )

    def _run_with_tools(self, model_cfg: dict, messages: list[dict]) -> dict:
        relevant_tools = search_tools(messages[-1]["content"])
        trace = []

        for _ in range(MAX_TOOL_ITERATIONS):
            result = ollama.chat(
                model=model_cfg["ollama_tag"],
                messages=messages,
                keep_alive=model_cfg["keep_alive"],
                tools=relevant_tools,
            )
            msg = result["message"]
            messages.append(msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                return {"role": "tool_caller", "content": msg["content"], "tool_trace": trace, "sources": []}

            for call in tool_calls:
                name = call["function"]["name"]
                args = call["function"]["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)

                fn = get_tool(name)
                if not fn:
                    output = f"Error: unknown tool '{name}'"
                else:
                    try:
                        output = fn(**args)
                    except Exception as e:  # a broken tool must not kill the agent's turn
                        output = f"Error running tool '{name}': {e}"
                trace.append({"tool": name, "args": args, "output": output})

                messages.append({"role": "tool", "content": str(output)})

        return {
            "role": "tool_caller",
            "content": "توقفت بعد عدة محاولات استخدام أدوات بدون إجابة نهائية.",
            "tool_trace": trace,
            "sources": [],
        }
