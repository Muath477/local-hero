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
from .postprocess import REPAIR_PROMPT, fix_code_punctuation, leak_count, needs_script_repair
from .skills import load_skills, match_skills, render as render_skills
from tools import agent_tools
from tools.registry import get_tool, search_tools

MAX_TOOL_ITERATIONS = 4
# Search/fetch tools can return 10k+ characters; the whole result is re-sent on every iteration and
# an over-long prompt is silently truncated by the model server, so cap what the model sees.
MAX_TOOL_OUTPUT_CHARS = 4000
TIER_ORDER = ["min", "std", "high"]

# Roles that get a tool-calling loop instead of a single plain completion.
# "council" is the only one that gets the ask_*_agent delegation tools —
# see tools/agent_tools.py — real multi-agent collaboration, still one
# Ollama call at a time (the hardware can't do concurrent models).
TOOL_ROLES = {"tool_caller", "council"}
DELEGATE_TOOLS = agent_tools.DELEGATE_TOOLS

SYSTEM_PROMPTS = {
    "writer_general": "أنت مساعد كتابة وتحليل عام. جاوب بإيجاز ودقة، وبنفس لغة المستخدم. "
                      "اكتب الأرقام والمعادلات كنص عادي بدون LaTeX.",
    "coder": "أنت مبرمج خبير. اكتب كود صحيح وقابل للتشغيل مباشرة، مع شرح مختصر جداً إن لزم. "
             "ملاحظة: كلمة \"مضروب\" في سياق رياضي/برمجي (مثل \"مضروب رقم\" أو \"n المضروب\") "
             "تعني factorial (n! = n*(n-1)*...*1) — وليس عكس الرقم أو ضربه بنفسه. مثال: "
             "\"دالة تحسب مضروب 5 بشكل عودي\" يعني factorial(5) = 120 عبر دالة تستدعي نفسها.",
    "researcher_rag": "أنت باحث يعتمد فقط على المحتوى المتاح له (ملفات مرفوعة). "
                       "إذا ما وجدت الجواب في المحتوى، قل ذلك صراحة بدل التخمين.",
    "tool_caller": "أنت وكيل تنفيذي. أنت تملك أدوات فعلية تصل للإنترنت وتقرأ الملفات — لست مقيداً "
                   "بمعرفتك السابقة. أي طلب فيه \"ابحث\" أو \"اجلب\" أو \"افتح رابط\" يعني استدعِ الأداة "
                   "المناسبة فوراً، ولا تقل أبداً إنك لا تستطيع الوصول للإنترنت أو البحث. "
                   "لا تخترع نتائج أدوات لم تستدعها فعلاً. اكتب الأرقام والمعادلات كنص عادي بدون LaTeX، "
                   "وأجب بلغة المستخدم فقط دون كلمات من لغات أخرى.",
    "vision": "صف وحلل الصورة المعطاة بدقة وبإيجاز.",
    "council": "أنت منسّق فريق من الوكلاء المختصين. مهمتك تفكيك الطلبات المركّبة "
               "إلى أجزاء، وتوزيع كل جزء على الوكيل المناسب عبر أدوات "
               "ask_writer_agent / ask_coder_agent / ask_researcher_agent / ask_tools_agent "
               "بدل ما تجاوب عليه بنفسك مباشرة. ask_tools_agent لأي سؤال يحتاج بحثاً في الإنترنت "
               "أو أخباراً أو أسعاراً أو طقساً، أو حساباً، أو التاريخ والوقت، أو ترجمة، أو قائمة "
               "الملفات — لا تجاوب هذه بنفسك أبداً. ask_researcher_agent فقط إذا كان السؤال "
               "عن محتوى ملف رفعه المستخدم. إذا كان الطلب بسيطاً وما يحتاج تخصصاً، "
               "جاوب مباشرة بدون استدعاء أي وكيل. لا تكرر استدعاء نفس الوكيل لنفس الجزء. "
               "اكتب نص المهمة (task) الذي ترسله للوكيل بنفس لغة المستخدم.\n\n"
               "بعد ما توصلك كل الردود اللازمة: ردّك الأخير للمستخدم لازم يتضمّن "
               "محتوى كل رد رجعه أي وكيل استدعيته، كامل وبدون اختصار أو حذف — "
               "لا تلخّص رد الوكيل ولا تستبدله برأيك، فقط رتّبه ونسّقه بعنوان واضح "
               "لكل جزء (مثلاً '## الشرح' ثم '## الكود'). ردّ وكيل واحد فقط يعني "
               "جزء واحد فقط بالإجابة النهائية —ممنوع تسقط أي جزء طلبه المستخدم.",
}


class AgentManager:
    def __init__(self):
        self.registry = load_registry()
        self.tier = resolve_tier()
        disabled = set(self.registry["tiers"][self.tier].get("disable_roles", []))
        self.disabled_roles = disabled
        self.documents = DocumentStore()
        self.skills = load_skills()
        agent_tools.set_manager(self)

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
        # Task-specific instructions (skills/*.md) join the system prompt only when the message calls for them.
        matched = match_skills(self.skills, role, user_message)
        messages = [{"role": "system", "content": SYSTEM_PROMPTS.get(role, "") + render_skills(matched)}]
        messages += history or []

        retrieved = []
        if role == "researcher_rag":
            retrieved = self.documents.query(user_message)
            messages.append({"role": "user", "content": self._build_rag_prompt(user_message, retrieved)})
        else:
            messages.append({"role": "user", "content": user_message})

        if role in TOOL_ROLES:
            outcome = self._run_with_tools(role, model_cfg, messages)
        else:
            result = ollama.chat(
                model=model_cfg["ollama_tag"],
                messages=messages,
                keep_alive=model_cfg["keep_alive"],
                think=model_cfg.get("think"),
            )
            outcome = {
                "role": role,
                "content": result["message"]["content"],
                "tool_trace": [],
                "sources": [r["source"] for r in retrieved] if retrieved else [],
            }
            if role == "coder":
                outcome["content"] = fix_code_punctuation(outcome["content"])
        self._repair_script_leaks(user_message, outcome)
        outcome["skills"] = [sk.name for sk in matched]
        return outcome

    def _repair_script_leaks(self, user_message: str, outcome: dict) -> None:
        """One targeted rewrite when an Arabic answer carries a stray foreign-script word.
        Kept only if it actually has fewer leaked characters; costs a call only when a leak is found."""
        text = outcome["content"]
        if not needs_script_repair(user_message, text, outcome.get("tool_trace")):
            return
        repairer = self.registry["tarjuman"]  # the model that translates Arabic cleanly (8/8), not the one that leaked
        fixed = ollama.chat(
            model=repairer["ollama_tag"],
            messages=[{"role": "system", "content": REPAIR_PROMPT}, {"role": "user", "content": text}],
            keep_alive=repairer["keep_alive"], temperature=0.0, think=repairer.get("think"),
            num_predict=min(3072, len(text) * 2 + 64),
        )["message"]["content"].strip()
        if fixed and leak_count(fixed) < leak_count(text):
            outcome["content"] = fixed
            outcome["script_repaired"] = True

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
            think=model_cfg.get("think"),
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

    DELEGATE_LABELS = {
        "ask_writer_agent": "الكاتب",
        "ask_coder_agent": "المبرمج",
        "ask_researcher_agent": "الباحث",
        "ask_tools_agent": "الأدوات",
    }

    @classmethod
    def _backfill_dropped_delegates(cls, content: str, trace: list[dict]) -> str:
        """The council's coordinator model (1.5B) is asked to merge several
        agents' replies into one answer. Measured unreliable at exactly
        that: across repeated identical test requests it kept narrating
        only the last delegate it called and silently dropping the others,
        even with an explicit "don't drop any part" instruction in its
        system prompt. Rather than keep trusting prompting alone against a
        demonstrated small-model weakness, append any delegate reply whose
        content doesn't show up in the model's own final text, so nothing a
        specialist agent actually said gets lost.
        """
        appended = []
        for step in trace:
            label = cls.DELEGATE_LABELS.get(step["tool"])
            if not label:
                continue
            output = str(step["output"])
            fingerprint = output[:40].strip()
            if fingerprint and fingerprint not in content:
                appended.append(f"### رد وكيل {label}\n{output}")
        if not appended:
            return content
        return content.rstrip() + "\n\n" + "\n\n".join(appended)

    def _run_with_tools(self, role: str, model_cfg: dict, messages: list[dict]) -> dict:
        query = messages[-1]["content"]
        prior_turns = [m for m in messages[1:-1] if m.get("role") in ("user", "assistant")]  # before this request
        if role == "council":
            relevant_tools = search_tools(query, top_k=len(DELEGATE_TOOLS), only=DELEGATE_TOOLS)
        else:
            relevant_tools = search_tools(query, exclude=DELEGATE_TOOLS)  # delegation is the council's job alone
        trace = []

        for _ in range(MAX_TOOL_ITERATIONS):
            result = ollama.chat(
                model=model_cfg["ollama_tag"],
                messages=messages,
                keep_alive=model_cfg["keep_alive"],
                tools=relevant_tools,
                think=model_cfg.get("think"),
            )
            msg = result["message"]
            messages.append(msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                content = msg["content"]
                if role == "council":
                    if not content.strip() and not trace:
                        # The coordinator produced nothing and delegated nothing (observed with a 3B model
                        # on a plain question): hand the whole request to the tools agent instead of
                        # returning an empty answer.
                        fallback = self.run("tool_caller", query, history=prior_turns)
                        return {"role": role, "content": fallback["content"], "tool_trace": fallback["tool_trace"], "sources": []}
                    content = self._backfill_dropped_delegates(content, trace)
                return {"role": role, "content": content, "tool_trace": trace, "sources": []}

            for call in tool_calls:
                name = call["function"]["name"]
                args = call["function"]["arguments"]
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = None  # small models sometimes emit broken JSON; tell them, don't crash

                fn = get_tool(name)
                if args is None:
                    output = f"Error: arguments for '{name}' were not valid JSON — call it again with a JSON object."
                    args = {}
                elif not fn:
                    output = f"Error: unknown tool '{name}'"
                else:
                    try:
                        output = fn(**args)
                    except Exception as e:  # a broken tool must not kill the agent's turn
                        output = f"Error running tool '{name}': {e}"
                trace.append({"tool": name, "args": args, "output": output})

                shown = str(output)
                if len(shown) > MAX_TOOL_OUTPUT_CHARS:
                    shown = shown[:MAX_TOOL_OUTPUT_CHARS] + "\n...[output truncated]"
                messages.append({"role": "tool", "content": shown})

        return {
            "role": role,
            "content": "توقفت بعد عدة محاولات استخدام أدوات بدون إجابة نهائية.",
            "tool_trace": trace,
            "sources": [],
        }
