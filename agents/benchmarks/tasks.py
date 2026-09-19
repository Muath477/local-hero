"""Benchmark cases + scorers for the roles in config/models.yaml.

No network in this module — cases only describe a prompt and how to judge
the reply, so the scorers themselves are unit-tested offline
(tests/test_benchmark_scorers.py). run_benchmark.py does the Ollama I/O.

Prompts, tool schemas and parsing come from the production code paths
(agent_manager.SYSTEM_PROMPTS, tools.registry, router.parse_role,
agent_manager._build_rag_prompt) so a model that passes here passes for the
reason it would in the real app, not against a friendlier toy setup.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

Score = tuple[bool, str]
Check = Callable[[dict], Score]  # gets {"content": str, "tool_calls": list}

ARABIC = re.compile(r"[؀-ۿ]")
LATIN = re.compile(r"[A-Za-z]")
# Scripts a small multilingual model leaks into Arabic answers (see the
# tarjuman notes in config/models.yaml): Hebrew, Cyrillic, CJK, kana, Hangul, Thai.
FOREIGN_SCRIPTS = re.compile(r"[֐-׿Ѐ-ӿ一-鿿぀-ヿ가-힯฀-๿]")

REFUSAL = re.compile(
    r"(لا يوجد|لا توجد|لم (أجد|يرد|يذكر|تذكر|أعثر)|غير (موجود|مذكور|متوفر|متاح)|"
    r"لا (يتوفر|تتوفر|أعرف|أستطيع|أملك)|لا (تحتوي|يحتوي)|لا (تذكر|يذكر)|"
    r"not (found|mentioned|provided|contain|available|specified)|no (information|mention)|"
    r"(doesn't|does not|don't|do not) (contain|mention|say|include|have))",
    re.IGNORECASE,
)


@dataclass
class Case:
    id: str
    prompt: str
    check: Check
    system: str | None = None
    tools: list[dict] | None = None
    max_tokens: int = 200


# ── generic scorers ─────────────────────────────────────────────────────────

def arabic_ratio(text: str) -> float:
    a, l = len(ARABIC.findall(text)), len(LATIN.findall(text))
    return a / (a + l) if a + l else 0.0


def has(*alternatives: str) -> Check:
    def check(r: dict) -> Score:
        text = r["content"]
        if any(re.search(a, text, re.IGNORECASE) for a in alternatives):
            return True, "ok"
        return False, f"missing any of {alternatives}"
    return check


def arabic_only(min_ratio: float = 0.85) -> Check:
    def check(r: dict) -> Score:
        text = r["content"]
        leak = FOREIGN_SCRIPTS.search(text)
        if leak:
            return False, f"foreign-script leak: {leak.group(0)!r}"
        ratio = arabic_ratio(text)
        return (ratio >= min_ratio, f"arabic ratio {ratio:.2f}")
    return check


def english_only(r: dict) -> Score:
    ratio = arabic_ratio(r["content"])
    return (ratio < 0.1, f"arabic ratio {ratio:.2f}")


def max_words(n: int) -> Check:
    def check(r: dict) -> Score:
        words = len(r["content"].split())
        return (words <= n, f"{words} words (max {n})")
    return check


def lacks(*patterns: str) -> Check:
    """Fails when any pattern appears — for the wrong-but-plausible translation ("two percent" -> "200%")."""
    def check(r: dict) -> Score:
        for p in patterns:
            m = re.search(p, r["content"], re.IGNORECASE)
            if m:
                return False, f"mistranslation marker present: {m.group(0)!r}"
        return True, "ok"
    return check


def all_of(*checks: Check) -> Check:
    def check(r: dict) -> Score:
        for c in checks:
            ok, detail = c(r)
            if not ok:
                return False, detail
        return True, "ok"
    return check


# ── coding: execute the generated function against real asserts ─────────────

UNSAFE_CODE = re.compile(
    r"\b(import\s+(os|sys|subprocess|shutil|socket|requests|urllib|pathlib|ctypes|http)\b"
    r"|from\s+(os|sys|subprocess|shutil|socket|pathlib|ctypes)\b"
    r"|__import__|eval\(|exec\(|open\(|input\()"
)


def extract_code(text: str) -> str:
    for block in re.findall(r"```[\w+-]*[ \t]*\n(.*?)```", text, re.DOTALL | re.IGNORECASE):
        if "def " in block:
            return block
    unclosed = re.search(r"```[\w+-]*[ \t]*\n(.*)$", text, re.DOTALL | re.IGNORECASE)
    if unclosed and "def " in unclosed.group(1):
        return unclosed.group(1)
    return text if "def " in text else ""


def run_python(code: str, timeout: float = 10.0) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "case.py"
        path.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(path)], capture_output=True, text=True,
                timeout=timeout, cwd=d, stdin=subprocess.DEVNULL,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout:.0f}s"
    if proc.returncode == 0:
        return True, "ok"
    tail = (proc.stderr.strip().splitlines() or ["non-zero exit"])[-1]
    return False, tail[:160]


def runs_against(test_code: str) -> Check:
    def check(r: dict) -> Score:
        from orchestrator.postprocess import fix_code_punctuation  # what production applies to coder replies
        code = extract_code(fix_code_punctuation(r["content"]))
        if not code:
            return False, "no code in reply"
        if UNSAFE_CODE.search(code):
            return False, "refused to execute: uses a blocked import/builtin"
        return run_python(code + "\n\n" + test_code)
    return check


# ── tool calling ────────────────────────────────────────────────────────────

def _first_call(r: dict) -> tuple[str, dict] | None:
    calls = r.get("tool_calls") or []
    if not calls:
        return None
    fn = calls[0]["function"]
    args = fn.get("arguments") or {}
    if isinstance(args, str):
        import json
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return fn["name"], args if isinstance(args, dict) else {}


def calls_tool(name: str, arg: str, pattern: str) -> Check:
    def check(r: dict) -> Score:
        call = _first_call(r)
        if call is None:
            return False, "no tool call (answered in text / refused)"
        got, args = call
        if got != name:
            return False, f"called {got!r}, wanted {name!r}"
        if not arg:
            return True, "ok"
        if arg not in args:
            return False, f"missing arg {arg!r} (got {sorted(args)})"
        if not re.search(pattern, str(args[arg]), re.IGNORECASE):
            return False, f"{arg}={args[arg]!r} doesn't match /{pattern}/"
        return True, "ok"
    return check


def no_tool_and(*alternatives: str) -> Check:
    def check(r: dict) -> Score:
        if r.get("tool_calls"):
            return False, "called a tool for a question that needs none"
        return has(*alternatives)(r)
    return check


# ── routing ─────────────────────────────────────────────────────────────────

def routes_to(role: str) -> Check:
    def check(r: dict) -> Score:
        from orchestrator.router import parse_role
        got = parse_role(r["content"])
        return (got == role, f"routed to {got!r}, wanted {role!r}")
    return check


# ── translation ─────────────────────────────────────────────────────────────

def clean_arabic(r: dict) -> Score:
    from tools.tarjuman import _is_contaminated
    bad = _is_contaminated(r["content"])
    return (not bad, "foreign word/script leaked into the Arabic" if bad else "ok")


# ── case builders (lazy imports keep `import benchmarks.tasks` light) ───────

def _prod_prompt(role: str, message: str = "") -> str:
    """The system prompt production builds: the role prompt plus any skills the message triggers."""
    from orchestrator.agent_manager import SYSTEM_PROMPTS
    from orchestrator.skills import load_skills, match_skills, render
    return SYSTEM_PROMPTS[role] + render(match_skills(load_skills(), role, message))


def _tool_schemas(prompt: str) -> list[dict]:
    """Exactly what production offers the tool_caller for this message: the top-5 by tool search."""
    from tools.agent_tools import DELEGATE_TOOLS
    from tools.registry import search_tools
    return search_tools(prompt, exclude=DELEGATE_TOOLS)


def _rag_prompt(question: str, hits: list[dict]) -> str:
    from orchestrator.agent_manager import AgentManager
    return AgentManager._build_rag_prompt(question, hits)


def _translate_system() -> str:
    from tools.tarjuman import system_prompt  # the exact prompt production sends
    return system_prompt("Arabic")
_BRANCH = [{"source": "branches.txt", "text":
            "ساعات العمل: فرع الرياض من 9 صباحاً حتى 5 مساءً، وفرع جدة من 10 صباحاً حتى 6 مساءً. "
            "الإجازة الأسبوعية يوم الجمعة."}]
_REPORT = [{"source": "q3.txt", "text":
            "The Q3 report shows revenue grew 12% to 4.5 million dollars, driven mainly by the Jeddah branch."}]


def build_cases() -> dict[str, list[Case]]:
    # Production builds the system prompt per message (role prompt + triggered skills), so the
    # cases do too. `writer` / `tool_system` are filled in per case by the loop at the end.
    writer = tool_system = None

    def code(id_, prompt, test):
        return Case(id_, prompt, runs_against(test), system=_prod_prompt("coder", prompt), max_tokens=400)

    def tool(id_, prompt, name, arg, pattern):
        return Case(id_, prompt, calls_tool(name, arg, pattern), system=_prod_prompt("tool_caller", prompt),
                    tools=_tool_schemas(prompt), max_tokens=120)

    router_system = routing_system_prompt()

    def route(id_, prompt, role):
        return Case(id_, prompt, routes_to(role), system=router_system, max_tokens=40)

    def rag(id_, question, hits, check):
        return Case(id_, _rag_prompt(question, hits), check, system=_prod_prompt("researcher_rag", question),
                    max_tokens=150)

    def translate(id_, text, *keywords, avoid=()):
        # known-wrong markers first, so the report names the actual mistranslation
        return Case(id_, text, all_of(clean_arabic, *[lacks(a) for a in avoid], *[has(k) for k in keywords]),
                    system=_translate_system(), max_tokens=120)

    cases = {
        "writing": [
            Case("apology-email", "اكتب لي إيميل رسمي قصير (٣ أسطر) أعتذر فيه عن تأخر تسليم التقرير الشهري.",
                 all_of(arabic_only(), has("التقرير"), has("أعتذر", "نعتذر", "اعتذار", "آسف", "نأسف", "أسف")),
                 system=writer),
            Case("two-sentence-summary",
                 "لخص في جملتين فقط: الذكاء الاصطناعي بدأ يغير طريقة عمل الشركات، فهو يؤتمت المهام المتكررة "
                 "ويساعد الموظفين على اتخاذ قرارات أسرع، لكنه يحتاج إلى إشراف بشري ومراجعة مستمرة للنتائج.",
                 all_of(arabic_only(), has("ذكاء"), lambda r: (len(r["content"]) < 500, "too long")), system=writer),
            Case("capital-ar", "ما هي عاصمة المملكة العربية السعودية؟ أجب بجملة واحدة.", has("الرياض"), system=writer),
            Case("arithmetic-ar",
                 "عندي ٣ صناديق في كل صندوق ١٢ تفاحة. أكلت ٥ تفاحات. كم تفاحة بقيت؟ اكتب الرقم النهائي.",
                 has("31", "٣١"), system=writer),
            Case("english-follow", "Reply in English, one sentence: what is the capital of France?",
                 all_of(has("Paris"), english_only), system=writer),
            Case("short-title", "اكتب لي عنواناً قصيراً (5 كلمات كحد أقصى) لمقال عن أهمية النوم.",
                 all_of(arabic_only(), max_words(8)), system=writer),
        ],
        "coding": [
            code("reverse-ar", "اكتب لي دالة بايثون اسمها reverse_text تعكس نص",
                 'assert reverse_text("abc") == "cba"\nassert reverse_text("") == ""\nassert reverse_text("مرحبا") == "ابحرم"'),
            code("factorial-ar", "اكتب دالة بايثون اسمها fact تحسب مضروب رقم (factorial) بشكل عودي.",
                 "assert fact(5) == 120\nassert fact(0) == 1\nassert fact(1) == 1"),
            code("palindrome-en", "Write a Python function is_palindrome(s) that ignores case and non-alphanumeric characters.",
                 'assert is_palindrome("A man, a plan, a canal: Panama")\nassert not is_palindrome("hello")\nassert is_palindrome("")'),
            code("fizzbuzz-en", "Write a Python function fizzbuzz(n) that returns a list of strings for 1..n "
                 "(Fizz for multiples of 3, Buzz for 5, FizzBuzz for both, otherwise the number as a string).",
                 'r = fizzbuzz(15)\nassert len(r) == 15 and r[0] == "1" and r[2] == "Fizz" and r[4] == "Buzz" and r[14] == "FizzBuzz"'),
            code("count-words-ar", "اكتب دالة بايثون اسمها count_words تأخذ نصاً وترجع قاموساً فيه عدد تكرار كل كلمة.",
                 'assert count_words("a b a") == {"a": 2, "b": 1}'),
            code("unique-sorted-en", "Write a Python function unique_sorted(items) that returns a sorted list without duplicates.",
                 "assert unique_sorted([3, 1, 3, 2, 1]) == [1, 2, 3]\nassert unique_sorted([]) == []"),
            code("two-sum-en", "Write a Python function two_sum(nums, target) returning a tuple of indices (i, j) with i < j "
                 "of two numbers that add up to target, or None if impossible.",
                 "assert tuple(two_sum([2, 7, 11, 15], 9)) == (0, 1)\nassert two_sum([1, 2], 10) is None"),
            code("is-prime-ar", "اكتب دالة بايثون اسمها is_prime تتحقق هل العدد أولي.",
                 "assert is_prime(2) and is_prime(97)\nassert not is_prime(1) and not is_prime(100) and not is_prime(0)"),
        ],
        "tools": [
            tool("search-gold-ar", "ابحث لي في الإنترنت عن سعر الذهب اليوم", "web_search", "query", "ذهب|gold"),
            tool("search-python-en", "search the web for the latest Python release version", "web_search", "query", "python"),
            tool("read-file-ar", "اقرأ لي الملف report.txt وقل لي ماذا فيه", "read_uploaded_file", "filename", r"report\.txt"),
            tool("weather-en", "What's the weather like in Riyadh right now?", "web_search", "query", "riyadh|الرياض"),
            tool("calc-ar", "كم يساوي 15 × 23 + 7؟", "calculator", "expression", r"15.*23.*7"),
            tool("time-en", "What time is it in London right now?", "get_datetime", "tz", "london|europe"),
            tool("list-files-ar", "وش الملفات اللي رفعتها؟", "list_uploaded_files", "", ""),
            tool("translate-ar", "ترجم لي هذي الجملة للعربية: The meeting starts at noon.", "translate_text", "text", "meeting"),
            tool("hf-search-en", "find a GGUF model for Arabic on Hugging Face", "hf_model_search", "query", "gguf|arabic"),
            tool("search-windows-ar", "ابحث عن آخر إصدار من ويندوز", "web_search", "query", "ويندوز|windows"),
            Case("no-tool-needed", "اكتب لي جملة ترحيب قصيرة", no_tool_and("مرحب", "أهلا", "أهلاً", "السلام", "welcome", "hello"),
                 system=tool_system, tools=_tool_schemas("اكتب لي جملة ترحيب قصيرة"), max_tokens=120),
        ],
        "routing": [
            route("r-writer-ar", "اكتب لي إيميل رسمي لمديري", "writer_general"),
            route("r-coder-ar", "اكتب دالة بايثون تعكس النص", "coder"),
            route("r-rag-ar", "اقرأ الملف المرفوع ولخص لي النقاط", "researcher_rag"),
            route("r-tools-ar", "ابحث في الإنترنت عن أخبار اليوم", "tool_caller"),
            route("r-vision-ar", "ايش في هذي الصورة؟", "vision"),
            route("r-coder-en", "fix this bug in my javascript function", "coder"),
            route("r-writer-en", "summarize this article for me", "writer_general"),
            route("r-tools-en", "check the weather in Jeddah", "tool_caller"),
        ],
        "rag": [
            rag("jeddah-hours", "متى يفتح فرع جدة؟", _BRANCH, has("10", "١٠", "العاشرة")),
            rag("unanswerable-salary", "كم راتب مدير الفرع؟", _BRANCH, lambda r: (bool(REFUSAL.search(r["content"])), "invented an answer instead of saying it isn't in the document")),
            rag("english-revenue", "How much did revenue grow in Q3?", _REPORT, has("12")),
            rag("no-hits", "ما هي سياسة الإجازات؟", [], lambda r: (bool(REFUSAL.search(r["content"])), "answered without any retrieved context")),
        ],
        "translation": [
            translate("budget-school", "The local budget aims to build a new school in the city.", "ميزانية", "مدرسة"),
            translate("contract", "Please send me the signed contract before Sunday.", "عقد", "الأحد"),
            translate("security", "Our team will review the security requirements next week.", "فريق", "أسبوع", "أمن|الأمان|الأمنية|أمني"),
            translate("medicine", "The patient should take the medicine twice a day after meals.", "مريض", "دواء"),
            translate("board", "The board approved the annual budget yesterday.", "مجلس|الإدارة", "ميزانية", "وافق|اعتمد|أقر|صادق"),
            translate("invoice", "Please review the attached invoice and confirm the total amount.", "فاتورة", "المبلغ|الإجمالي"),
            translate("customers", "Customer satisfaction increased by ten percent this quarter.", "العملاء|الزبائن", "عشرة|10|١٠", "الربع"),
            translate("password", "Do not share your password with anyone.", "كلمة المرور|كلمة السر|الرقم السري", "أحد|شخص"),
            # Contract-grade cases: each has a plausible wrong answer a keyword check would miss. All were
            # observed from Qwen3-4B-2507 in a real document ("two percent" -> "مئتي في المئة" = 200%).
            translate("penalty", "Late payments incur a penalty of two percent per month.",
                      "اثنين|اثنان|إثنين|2|٢", "%|في المئة|بالمئة|بالمائة|في المائة|٪", avoid=("مئتي|مائتي|200|٢٠٠",)),
            translate("confidential", "This document is confidential and must not be shared.",
                      "سري|سرية|سرّي", avoid=("مخفي|مُخفي|محرر|مُحرر",)),
            translate("effective", "The agreement takes effect on 1 January 2027.",
                      "يسري|ساري|يبدأ|يدخل|اعتبار|سريان|ينفذ|نافذ|يُعمل", avoid=("ينتشر|يتوسع|يمتد",)),
            translate("due", "Payment is due within thirty days of the invoice date.", "ثلاثين|30", "فاتورة", "يوم|أيام"),
            translate("liable", "The supplier is liable for any damages caused by the delay.",
                      "مسؤول|مسئول|يتحمل", "أضرار|ضرر|تعويض|خسائر"),
            translate("draft", "Draft - not for distribution.", "مسودة|مشروع", "توزيع|نشر|تداول"),
            translate("footer", "Page footer: Acme Trading Co.", "تذييل|أسفل|الهامش السفلي|ذيل", avoid=("السفر|السفل:",)),
            translate("terminate", "Either party may terminate this agreement with thirty days written notice.",
                      "إنهاء|فسخ|إلغاء", "ثلاثين|30", "كتاب|خطي|إشعار"),
        ],
    }
    for case in cases["writing"]:
        case.system = _prod_prompt("writer_general", case.prompt)
    for case in cases["tools"]:
        if case.system is None:
            case.system = _prod_prompt("tool_caller", case.prompt)
    return cases


# Which category each models.yaml role is judged on.
ROLE_CATEGORY = {
    "writer_general": "writing",
    "coder": "coding",
    "tool_caller": "tools",
    "council": "tools",
    "router": "routing",
    "researcher_rag": "rag",
    "tarjuman": "translation",
}


def routing_system_prompt() -> str:
    from orchestrator.hardware import load_registry
    from orchestrator.router import router_system_prompt
    return router_system_prompt(load_registry())
