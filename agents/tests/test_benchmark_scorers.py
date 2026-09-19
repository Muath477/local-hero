"""The benchmark is only as trustworthy as its scorers — pin them down."""
import pytest

from benchmarks import tasks
from benchmarks.run_benchmark import summarize
from benchmarks.tasks import (
    REFUSAL, all_of, arabic_only, arabic_ratio, build_cases, calls_tool, extract_code, has,
    max_words, no_tool_and, routes_to, run_python, runs_against,
)


def reply(content="", tool_calls=None):
    return {"content": content, "tool_calls": tool_calls or []}


def call(name, args):
    return [{"function": {"name": name, "arguments": args}}]


class TestTextScorers:
    def test_arabic_ratio(self):
        assert arabic_ratio("مرحبا") == 1.0
        assert arabic_ratio("hello") == 0.0
        assert arabic_ratio("123 !!") == 0.0

    @pytest.mark.parametrize("text, ok", [
        ("هذا نص عربي سليم تماماً", True),
        ("هذا نص مع كلمة hello واحدة فقط داخل جملة عربية طويلة جداً جداً جداً", True),
        ("hello this is english", False),
        ("نص عربي مع 预算 صيني", False),     # CJK leak
        ("نص عربي مع שלום عبري", False),      # Hebrew leak
        ("نص عربي مع привет روسي", False),    # Cyrillic leak
    ])
    def test_arabic_only(self, text, ok):
        assert arabic_only()(reply(text))[0] is ok

    def test_has_is_case_insensitive_and_any_of(self):
        assert has("paris", "لندن")(reply("It is PARIS."))[0]
        assert not has("paris")(reply("berlin"))[0]

    def test_max_words_and_all_of_short_circuit(self):
        assert max_words(3)(reply("a b c"))[0] and not max_words(3)(reply("a b c d"))[0]
        ok, detail = all_of(has("x"), max_words(1))(reply("y"))
        assert not ok and "x" in detail

    @pytest.mark.parametrize("text", [
        "لا يوجد في المستند أي معلومة عن الراتب", "المعلومة غير موجودة في الملف", "لم أجد الجواب",
        "The document does not mention salaries", "Not found in the provided text",
    ])
    def test_refusal_detected(self, text):
        assert REFUSAL.search(text)

    @pytest.mark.parametrize("text", ["الراتب 15000 ريال", "The salary is 15000"])
    def test_invented_answer_is_not_a_refusal(self, text):
        assert not REFUSAL.search(text)


class TestCodeScoring:
    def test_extract_prefers_the_block_with_a_function(self):
        text = "Use it:\n```bash\npip install x\n```\n```python\ndef f():\n    return 1\n```\nDone"
        assert extract_code(text).strip() == "def f():\n    return 1"

    def test_extract_handles_truncated_fence_and_bare_code(self):
        assert "def f" in extract_code("```python\ndef f():\n    return 1")
        assert "def f" in extract_code("def f():\n    return 1")
        assert extract_code("no code here") == ""

    def test_correct_function_passes(self):
        text = "```python\ndef reverse_text(s):\n    return s[::-1]\n```"
        assert runs_against('assert reverse_text("abc") == "cba"')(reply(text)) == (True, "ok")

    def test_wrong_function_fails_with_the_assertion(self):
        text = "```python\ndef reverse_text(s):\n    return s\n```"
        ok, detail = runs_against('assert reverse_text("abc") == "cba"')(reply(text))
        assert not ok and "AssertionError" in detail

    def test_wrong_function_name_fails(self):
        text = "```python\ndef reverse(s):\n    return s[::-1]\n```"
        ok, detail = runs_against('assert reverse_text("abc") == "cba"')(reply(text))
        assert not ok and "NameError" in detail

    def test_no_code_fails(self):
        assert runs_against("pass")(reply("I cannot do that"))[0] is False

    @pytest.mark.parametrize("evil", [
        "import os\ndef f():\n    os.remove('x')", "def f():\n    return __import__('os')",
        "def f():\n    return eval('1')", "def f():\n    return open('x').read()",
        "from subprocess import run\ndef f():\n    pass",
    ])
    def test_dangerous_code_is_never_executed(self, evil, tmp_path):
        marker = tmp_path / "ran"
        ok, detail = runs_against(f"open(r'{marker}', 'w')")(reply(f"```python\n{evil}\n```"))
        assert not ok and "blocked" in detail and not marker.exists()

    def test_infinite_loop_times_out(self):
        ok, detail = run_python("while True:\n    pass", timeout=1)
        assert not ok and "timed out" in detail

    def test_arabic_source_runs(self):
        text = '```python\ndef reverse_text(s):\n    return s[::-1]\n```'
        assert runs_against('assert reverse_text("مرحبا") == "ابحرم"')(reply(text))[0]


class TestToolScoring:
    def test_correct_call(self):
        r = reply(tool_calls=call("web_search", {"query": "سعر الذهب"}))
        assert calls_tool("web_search", "query", "ذهب")(r) == (True, "ok")

    def test_arguments_as_json_string_are_accepted(self):
        r = reply(tool_calls=call("web_search", '{"query": "gold price"}'))
        assert calls_tool("web_search", "query", "gold")(r)[0]

    @pytest.mark.parametrize("r, fragment", [
        (reply("I can't access the internet"), "no tool call"),
        (reply(tool_calls=call("get_weather", {"city": "x"})), "wanted 'web_search'"),
        (reply(tool_calls=call("web_search", {"q": "gold"})), "missing arg"),
        (reply(tool_calls=call("web_search", {"query": "cats"})), "doesn't match"),
        (reply(tool_calls=call("web_search", "{broken")), "missing arg"),
    ])
    def test_failures_explain_themselves(self, r, fragment):
        ok, detail = calls_tool("web_search", "query", "gold")(r)
        assert not ok and fragment in detail

    def test_no_tool_case(self):
        assert no_tool_and("4")(reply("4"))[0]
        assert not no_tool_and("4")(reply("4", call("web_search", {"query": "2+2"})))[0]


class TestRoutingScoring:
    @pytest.mark.parametrize("raw", ['{"role": "coder"}', '```json\n{"role": "coder"}\n```', 'Sure: {"role":"coder"}.'])
    def test_uses_production_parser(self, raw):
        assert routes_to("coder")(reply(raw))[0]

    def test_wrong_and_garbage(self):
        assert not routes_to("coder")(reply('{"role": "vision"}'))[0]
        assert not routes_to("coder")(reply("coder"))[0]


class TestCaseDefinitions:
    CASES = build_cases()

    def test_every_category_has_cases_with_unique_ids(self):
        assert set(self.CASES) == {"writing", "coding", "tools", "routing", "rag", "translation"}
        for category, cases in self.CASES.items():
            ids = [c.id for c in cases]
            assert len(ids) == len(set(ids)) and len(ids) >= 4, category

    def test_role_categories_exist(self):
        assert set(tasks.ROLE_CATEGORY.values()) <= set(self.CASES)

    def test_tool_cases_offer_exactly_what_production_offers(self):
        offered = {c.id: [t["function"]["name"] for t in c.tools] for c in self.CASES["tools"]}
        for id_, names in offered.items():
            assert len(names) <= 5 and not any(n.startswith("ask_") for n in names), id_
        assert "web_search" in offered["search-gold-ar"]
        assert "calculator" in offered["calc-ar"] and "get_datetime" in offered["time-en"]
        assert "translate_text" in offered["translate-ar"] and "list_uploaded_files" in offered["list-files-ar"]
        assert "hf_model_search" in offered["hf-search-en"]

    def test_rag_prompts_use_the_production_template(self):
        prompts = {c.id: c.prompt for c in self.CASES["rag"]}
        assert "Context from uploaded documents" in prompts["jeddah-hours"]
        assert "No matching content" in prompts["no-hits"]

    def test_routing_cases_carry_the_router_system_prompt(self):
        assert all("strict task router" in c.system for c in self.CASES["routing"])

    def test_reference_answers_pass_their_own_checks(self):
        """A case nobody can pass is a broken case — feed each coding case a hand-written solution."""
        solutions = {
            "reverse-ar": "def reverse_text(s):\n    return s[::-1]",
            "factorial-ar": "def fact(n):\n    return 1 if n <= 1 else n * fact(n - 1)",
            "palindrome-en": "def is_palindrome(s):\n    t = [c.lower() for c in s if c.isalnum()]\n    return t == t[::-1]",
            "fizzbuzz-en": "def fizzbuzz(n):\n    return ['FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else str(i) for i in range(1, n + 1)]",
            "count-words-ar": "def count_words(t):\n    d = {}\n    for w in t.split():\n        d[w] = d.get(w, 0) + 1\n    return d",
            "unique-sorted-en": "def unique_sorted(items):\n    return sorted(set(items))",
            "two-sum-en": "def two_sum(nums, target):\n    for i in range(len(nums)):\n        for j in range(i + 1, len(nums)):\n            if nums[i] + nums[j] == target:\n                return (i, j)\n    return None",
            "is-prime-ar": "def is_prime(n):\n    return n > 1 and all(n % i for i in range(2, int(n ** 0.5) + 1))",
        }
        for case in self.CASES["coding"]:
            ok, detail = case.check(reply(f"```python\n{solutions[case.id]}\n```"))
            assert ok, f"{case.id}: {detail}"

    def test_reference_answers_for_other_categories(self):
        by_id = {c.id: c for category in self.CASES.values() for c in category}
        good = {
            "apology-email": "السيد المحترم،\nأعتذر عن التأخر في تسليم التقرير الشهري وسأرسله غداً.\nمع التحية",
            "capital-ar": "عاصمة السعودية هي الرياض.", "arithmetic-ar": "31", "english-follow": "The capital of France is Paris.",
            "short-title": "النوم أساس الصحة والإنتاجية", "jeddah-hours": "يفتح فرع جدة الساعة 10 صباحاً.",
            "unanswerable-salary": "لا يوجد ذكر للراتب في المستند.", "english-revenue": "Revenue grew 12%.",
            "no-hits": "لم أجد أي محتوى عن سياسة الإجازات.", "no-tool-needed": "أهلاً وسهلاً بك",
            "r-coder-ar": '{"role": "coder"}', "r-vision-ar": '{"role": "vision"}',
            "budget-school": "تهدف الميزانية المحلية إلى بناء مدرسة جديدة في المدينة.",
            "contract": "يرجى إرسال العقد الموقع إليّ قبل يوم الأحد.",
            "security": "سيراجع فريقنا متطلبات الأمن الأسبوع القادم.",
            "medicine": "يجب أن يتناول المريض الدواء مرتين يومياً بعد الوجبات.",
            "board": "وافق مجلس الإدارة على الميزانية السنوية أمس ميزانية.",
            "invoice": "يرجى مراجعة الفاتورة المرفقة وتأكيد المبلغ الإجمالي.",
            "customers": "ارتفع رضا العملاء بنسبة عشرة بالمئة هذا الربع.",
            "password": "لا تشارك كلمة المرور مع أي شخص.",
        }
        # the budget/school sentence needs the literal word for "budget"
        good["budget-school"] = "تهدف ميزانية المدينة المحلية إلى بناء مدرسة جديدة."
        for id_, text in good.items():
            ok, detail = by_id[id_].check(reply(text))
            assert ok, f"{id_}: {detail}"


def test_summarize_builds_a_markdown_table():
    rows = [{"case": "a", "passed": True, "tok_per_s": 10.0}, {"case": "b", "passed": False, "tok_per_s": 20.0}]
    table = summarize({"m1": {"categories": {"coding": rows}, "seconds": 120}})
    assert "| m1 | 1/2 | 50% | 15.0 | 2.0 |" in table


class TestCasesUseProductionPrompts:
    CASES = build_cases()

    def _case(self, category, id_):
        return next(c for c in self.CASES[category] if c.id == id_)

    def test_triggered_skills_are_injected_like_in_production(self):
        assert "## Skill: formal-arabic-writing" in self._case("writing", "apology-email").system
        assert "## Skill: web-research" in self._case("tools", "search-gold-ar").system
        assert "## Skill: document-qa" in self._case("rag", "jeddah-hours").system
        assert "## Skill:" not in self._case("writing", "capital-ar").system

    def test_no_case_is_left_without_a_system_prompt(self):
        for category, cases in self.CASES.items():
            assert all(c.system for c in cases), category

    def test_hybrid_qwen3_detection_for_the_no_think_switch(self):
        from benchmarks.run_benchmark import is_hybrid_qwen3

        assert is_hybrid_qwen3("qwen3:4b") and is_hybrid_qwen3("qwen3-1.7b:Q4_K_M") and is_hybrid_qwen3("qwen3:8b")
        assert not is_hybrid_qwen3("qwen3-4b-instruct-2507:Q4_K_M")
        assert not is_hybrid_qwen3("qwen2.5:3b") and not is_hybrid_qwen3("qwen3-embedding:0.6b")


class TestSemanticTranslationCases:
    """Each contract-grade case must accept a correct Arabic rendering and reject the wrong one seen in practice."""
    CASES = {c.id: c for c in build_cases()["translation"]}

    GOOD = {
        "penalty": "تترتب على الدفعات المتأخرة غرامة قدرها اثنان في المئة شهرياً.",
        "confidential": "هذه الوثيقة سرية ولا يجوز مشاركتها.",
        "effective": "يسري هذا الاتفاق اعتباراً من 1 يناير 2027.",
        "due": "يستحق السداد خلال ثلاثين يوماً من تاريخ الفاتورة.",
        "liable": "يتحمل المورد المسؤولية عن أي أضرار ناجمة عن التأخير.",
        "draft": "مسودة - غير مخصصة للتوزيع.",
        "footer": "تذييل الصفحة: شركة أكمي للتجارة.",
        "terminate": "يجوز لأي من الطرفين إنهاء هذا الاتفاق بإشعار كتابي مدته ثلاثين يوماً.",
    }
    BAD = {
        "penalty": "تترتب على الدفعات المتأخرة غرامة قدرها مئتي في المئة شهرياً.",
        "confidential": "هذه الوثيقة مخفي ولا يجوز مشاركتها.",
        "effective": "ينتشر هذا الاتفاق في 1 يناير 2027 ويسري.",
        "footer": "السفر: شركة أكمي للتجارة.",
    }

    @pytest.mark.parametrize("id_", sorted(GOOD))
    def test_a_correct_translation_passes(self, id_):
        ok, detail = self.CASES[id_].check(reply(self.GOOD[id_]))
        assert ok, detail

    @pytest.mark.parametrize("id_", sorted(BAD))
    def test_the_observed_mistranslation_fails(self, id_):
        ok, detail = self.CASES[id_].check(reply(self.BAD[id_]))
        assert not ok and "mistranslation marker" in detail

    def test_lacks_scorer(self):
        from benchmarks.tasks import lacks

        assert lacks("bad")(reply("good text"))[0]
        assert lacks("bad", "worse")(reply("very WORSE"))[0] is False
