"""End-to-end smoke test with the REAL stack (Ollama + router + agents + tools + skills).

    python -m benchmarks.smoke_e2e

One message per role/tool/skill, routed exactly as a user's message would be
(no role forcing). For each it prints the routing decision, the skills injected,
the tool calls with their arguments, and the head of the answer, then asserts
what matters: right role, right tool, right skill, and that the ANSWER contains
the fact it should (a tool that ran but whose result got lost is a failure).
Slow on CPU — it loads each role's model in turn, as a real session would.
"""
from __future__ import annotations

import json
import re
import sys
import time

from orchestrator.agent_manager import AgentManager
from orchestrator.postprocess import leak_count
from orchestrator.router import Router
from tools.mcp_bridge import init_mcp_tools

# (message, expected role, expected tool or None, expected skill or None, regex the answer must match or None)
CASES = [
    ("اكتب لي إيميل رسمي أعتذر فيه عن تأخر التقرير", "writer_general", None, "formal-arabic-writing", "تحية|أعتذر|نعتذر"),
    ("ليش يطلع لي TypeError في هالكود؟ print('a' + 1)", "coder", None, "debugging-code", "str|int|نص|سلسلة"),
    ("ايش اسم مدير الفرع حسب الملف؟", "researcher_rag", None, "document-qa", "سلطان"),
    ("كم يساوي 15 × 23 + 7؟", "tool_caller", "calculator", None, "352"),
    ("what time is it in London right now?", "tool_caller", "get_datetime", None, r"\d{2}:\d{2}"),
    ("ابحث في الإنترنت عن آخر إصدار من بايثون", "tool_caller", "web_search", "web-research", r"3\.1\d"),
    ("وش الملفات اللي رفعتها؟", "tool_caller", "list_uploaded_files", None, "branch_info"),
    ("ترجم لي هذي الجملة للعربية: The meeting starts at noon.", "tool_caller", "translate_text", None, "[؀-ۿ]"),
]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    router, manager = Router(), AgentManager()
    router.warm_up()
    print("mcp:", list(init_mcp_tools()))
    print("reindexed:", manager.documents.reindex_missing())

    failures = 0
    for message, want_role, want_tool, want_skill, want_text in CASES:
        started = time.time()
        decision = router.route(message)
        result = manager.run(decision["role"], message)
        calls = result["tool_trace"]
        tools = [t["tool"] for t in calls]
        answer = result["content"].strip()

        problems = []
        if decision["role"] != want_role:
            problems.append(f"routed to {decision['role']}, wanted {want_role}")
        if want_tool and want_tool not in tools:
            problems.append(f"tool {want_tool} not called (called {tools})")
        if want_skill and want_skill not in result["skills"]:
            problems.append(f"skill {want_skill} not injected (got {result['skills']})")
        if want_text and not re.search(want_text, answer):
            problems.append(f"answer doesn't contain /{want_text}/")
        if not answer:
            problems.append("empty answer")
        if leak_count(answer):
            problems.append("foreign-script letters in the answer")
        failures += bool(problems)

        print(f"\n[{'FAIL' if problems else 'PASS'}] {message}")
        print(f"  route: {decision['role']} via {decision['method']} | skills: {result['skills']} | {time.time() - started:.0f}s"
              + (" | script-repaired" if result.get("script_repaired") else ""))
        for c in calls:
            print(f"  tool: {c['tool']}({json.dumps(c['args'], ensure_ascii=False)[:120]}) -> {str(c['output']).replace(chr(10), ' ')[:110]}")
        print("  answer:", answer.replace("\n", " ")[:240])
        for p in problems:
            print("  !!", p)
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
