"""Measures the router on labelled messages — Arabic (Gulf dialect + MSA) and English.

    python -m benchmarks.routing_eval                 # dev set + holdout, real Ollama
    python -m benchmarks.routing_eval --no-llm        # semantic stage only (fast, embeddings only)

DEV is what the seed examples in orchestrator/router.py were tuned against.
HOLDOUT was written before tuning and never used to pick examples or
thresholds — if DEV accuracy rises but HOLDOUT doesn't, the tuning overfit.

Only roles reachable from text are labelled (vision needs image bytes; see
router.DEFAULT_EXCLUDED).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from orchestrator import router as router_mod
from orchestrator.router import Router

W, C, R, T = "writer_general", "coder", "researcher_rag", "tool_caller"

DEV: list[tuple[str, str]] = [
    # writer_general
    ("اكتب لي رسالة شكر لزميلي على مساعدته", W),
    ("لخص لي هالفقرة بجملتين", W),
    ("ايش الفرق بين الحب والإعجاب؟", W),
    ("ترجم لي هالجملة للإنجليزي: صباح الخير", T),  # translation goes through the Tarjuman tool
    ("اعطني أفكار لاسم مشروع تقني", W),
    ("write a cover letter for a marketing job", W),
    ("explain quantum computing in simple words", W),
    ("كيف أحسن مهارات التواصل عندي؟", W),
    ("صيغ لي إعلان لمتجر قهوة", W),
    ("what are the pros and cons of remote work?", W),
    ("اشرح لي معنى التضخم الاقتصادي", W),
    ("اكتب قصة قصيرة عن طفل وقطة", W),
    # coder
    ("اكتب لي دالة بايثون تحسب مجموع قائمة", C),
    ("ايش الخطأ في هالكود؟ for i in range(10) print(i)", C),
    ("كيف أقرأ ملف JSON في جافاسكربت؟", C),
    ("write a SQL query to find duplicate emails", C),
    ("convert this loop to a list comprehension", C),
    ("اكتب سكربت باش ياخذ نسخة احتياطية من مجلد", C),
    ("how do I reverse a linked list in C++?", C),
    ("أبغى كلاس بايثون يمثل حساب بنكي", C),
    ("regex to validate an email address", C),
    ("ليش يطلع لي TypeError undefined is not a function؟", C),
    ("اكتب اختبارات pytest لهذه الدالة", C),
    ("git command to undo last commit", C),
    # researcher_rag
    ("لخص لي الملف اللي رفعته", R),
    ("ايش يقول المستند عن سياسة الإجازات؟", R),
    ("استخرج التواريخ المهمة من التقرير المرفق", R),
    ("based on the uploaded pdf, what is the total budget?", R),
    ("answer from the document: who signed the contract?", R),
    ("وش الأرقام المذكورة في الملف عن المبيعات؟", R),
    ("find the section about termination in the attached file", R),
    ("قارن بين الملفين المرفوعين", R),
    ("ايش أهم النقاط في هالتقرير؟", R),
    ("search the uploaded document for payment terms", R),
    ("اقرأ الملف وجاوبني: كم عدد الموظفين؟", R),
    ("what does the attached contract say about liability?", R),
    # tool_caller
    ("ابحث في الإنترنت عن أسعار الذهب اليوم", T),
    ("كم درجة الحرارة في الرياض الحين؟", T),
    ("search the web for the latest iPhone release date", T),
    ("افتح هذا الرابط https://example.com ولخصه", T),
    ("what's the weather in Dubai today?", T),
    ("وش آخر أخبار كأس العالم؟", T),
    ("fetch https://example.com/news and summarize", T),
    ("اجلب لي سعر صرف الدولار مقابل الريال", T),
    ("look up the current bitcoin price", T),
    ("ابحث عن مطاعم قريبة مني", T),
    ("who won the match last night?", T),
    ("شغل الأمر dir واعطني النتيجة", T),
]

HOLDOUT: list[tuple[str, str]] = [
    ("اكتب لي خطاب استقالة مهذب", W),
    ("what's a good way to start a presentation?", W),
    ("ساعدني أرد على إيميل عميل زعلان", W),
    ("اشرح لي الفرق بين الأسهم والسندات ببساطة", W),
    ("اكتب لي دالة جافاسكربت ترتب مصفوفة أرقام", C),
    ("why does my python script throw KeyError?", C),
    ("أبغى كود html لصفحة تسجيل دخول", C),
    ("write a bash one-liner to count lines in all .txt files", C),
    ("لخص لي أهم ما ورد في العقد المرفق", R),
    ("according to the attached report, what were Q2 sales?", R),
    ("وش يقول الملف عن شروط الدفع؟", R),
    ("extract all names mentioned in the uploaded file", R),
    ("ابحث لي عن أحدث أخبار الذكاء الاصطناعي", T),
    ("what is the exchange rate of euro to dollar right now?", T),
    ("افتح موقع الجزيرة وقل لي العناوين", T),
    ("check the current temperature in Jeddah", T),
]

# Written after the seed examples were finalised, run once for the number worth quoting:
# other Gulf/Levantine/Iraqi phrasings, mixed-language, longer messages.
FRESH: list[tuple[str, str]] = [
    ("عطني نصايح عشان أذاكر بشكل أحسن", W),
    ("ابغى أكتب تهنئة لصديقي بمناسبة تخرجه", W),
    ("how do I politely decline a job offer?", W),
    ("لخص لي هذي الفقرة: الذكاء الاصطناعي يتطور بسرعة كبيرة ويؤثر على سوق العمل", W),
    ("ايش معنى كلمة استدامة؟", W),
    ("عندي دالة بايثون ترجع None وش المشكلة؟", C),
    ("write a react component that shows a counter", C),
    ("كيف أسوي loop في جافا يطبع الأرقام من 1 إلى 100؟", C),
    ("optimize this SQL query that joins three tables", C),
    ("اكتب لي سكربت powershell يمسح الملفات المؤقتة", C),
    ("ايش الشروط الجزائية المذكورة في العقد اللي رفعته؟", R),
    ("summarize the third section of the PDF I attached", R),
    ("كم إجمالي المبالغ في الفاتورة المرفقة؟", R),
    ("which clauses in the uploaded agreement mention confidentiality?", R),
    ("طلع لي أسماء الموظفين من ملف الإكسل المرفوع", R),
    ("شنو أسعار البنزين اليوم في السعودية؟", T),
    ("open https://news.ycombinator.com and tell me the top story", T),
    ("when is the next Champions League match?", T),
    ("هل الجو ممطر في جدة اليوم؟", T),
    ("search for cheap flights from Riyadh to Cairo", T),
]

# Written before the tool-request seeds (calculator / time / file list / translation) were added,
# to measure that change on messages it was not tuned on.
FRESH2: list[tuple[str, str]] = [
    ("احسب لي 18% من 2500", T),
    ("كم الساعة الحين في نيويورك؟", T),
    ("what files did I upload?", T),
    ("ترجم هذي الجملة للإنجليزي: الاجتماع بكرة الساعة عشرة", T),
    ("translate the attached contract into Arabic", T),
    ("how much is 45 times 12?", T),
    ("اكتب لي رسالة اعتذار لمدير الشركة", W),
    ("explain what a mortgage is", W),
    ("ايش الفرق بين list و tuple في بايثون؟", C),
    ("اعمل لي دالة تحول الدرجة من مئوية لفهرنهايت", C),
    ("لخص لي الملف اللي رفعته قبل شوي", R),
    ("what does the uploaded spreadsheet say about Q1 costs?", R),
]


def score_set(router: Router, data, use_llm: bool) -> list[dict]:
    rows = []
    for message, expected in data:
        role, score, margin = router._semantic_route(message)
        confident = score >= router_mod.CONFIDENCE_THRESHOLD and margin >= router_mod.MARGIN_THRESHOLD
        if confident:
            final, method = role, "semantic"
        elif use_llm:
            final, method = router._llm_route(message), "llm_fallback"
        else:
            final, method = None, "fallback(skipped)"
        rows.append({"message": message, "expected": expected, "semantic": role, "score": score,
                     "margin": margin, "final": final, "method": method})
    return rows


def report(name: str, rows: list[dict]) -> str:
    n = len(rows)
    confident = [r for r in rows if r["method"] == "semantic"]
    sem_ok = sum(r["final"] == r["expected"] for r in confident)
    raw_ok = sum(r["semantic"] == r["expected"] for r in rows)
    judged = [r for r in rows if r["final"] is not None]
    final_ok = sum(r["final"] == r["expected"] for r in judged)
    lines = [
        f"== {name} ({n} messages) ==",
        f"  semantic stage decides {len(confident)}/{n} ({100 * len(confident) / n:.0f}%), "
        f"correct {sem_ok}/{len(confident)} of those",
        f"  best-embedding-match alone (ignoring thresholds): {raw_ok}/{n} ({100 * raw_ok / n:.0f}%)",
    ]
    if judged:
        lines.append(f"  FINAL accuracy: {final_ok}/{len(judged)} ({100 * final_ok / len(judged):.0f}%)")
    confusion = Counter((r["expected"], r["final"] or r["semantic"]) for r in rows if (r["final"] or r["semantic"]) != r["expected"])
    for r in rows:
        got = r["final"] or r["semantic"]
        if got != r["expected"]:
            lines.append(f"  MISS [{r['method']}, score {r['score']:.2f}, margin {r['margin']:.2f}] "
                         f"wanted {r['expected']}, got {got}: {r['message']}")
    if confusion:
        lines.append("  confusion: " + ", ".join(f"{a}->{b} x{c}" for (a, b), c in confusion.most_common()))
    return "\n".join(lines)


def threshold_sweep(rows: list[dict]) -> str:
    """How many messages the semantic stage would keep at each (confidence, margin), and how accurate it is there."""
    out = ["  threshold sweep (confidence, margin) -> coverage / accuracy of the semantic stage"]
    for conf in (0.50, 0.55, 0.60, 0.65, 0.70, 0.75):
        for margin in (0.0, 0.03, 0.05, 0.08):
            kept = [r for r in rows if r["score"] >= conf and r["margin"] >= margin]
            ok = sum(r["semantic"] == r["expected"] for r in kept)
            acc = f"{100 * ok / len(kept):.0f}%" if kept else "-"
            out.append(f"    conf>={conf:.2f} margin>={margin:.2f}: {len(kept):>2}/{len(rows)} kept, {acc} correct")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-llm", action="store_true", help="skip stage 2 (embeddings only)")
    ap.add_argument("--sweep", action="store_true", help="print the threshold sweep for DEV+HOLDOUT")
    ap.add_argument("--sets", nargs="*", default=["dev", "holdout", "fresh", "fresh2"],
                    choices=["dev", "holdout", "fresh", "fresh2"], help="which message sets to run")
    ap.add_argument("--router-model", help="Ollama tag for stage 2 (default: models.yaml router)")
    ap.add_argument("--embedder", help="Ollama tag for stage 1 (default: models.yaml embedder)")
    ap.add_argument("--confidence", type=float, help="override router.CONFIDENCE_THRESHOLD")
    ap.add_argument("--margin", type=float, help="override router.MARGIN_THRESHOLD")
    args = ap.parse_args(argv)
    if args.confidence is not None:
        router_mod.CONFIDENCE_THRESHOLD = args.confidence
    if args.margin is not None:
        router_mod.MARGIN_THRESHOLD = args.margin
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    router = Router()
    router.router_tag = args.router_model or router.router_tag
    router.embedder_tag = args.embedder or router.embedder_tag
    print(f"stage 1 embedder: {router.embedder_tag} | stage 2 model: {router.router_tag}")
    router.warm_up()
    sets = {"dev": DEV, "holdout": HOLDOUT, "fresh": FRESH, "fresh2": FRESH2}
    scored = {name: score_set(router, sets[name], not args.no_llm) for name in args.sets}
    for name, rows in scored.items():
        print(report(name.upper(), rows))
        print()
    dev, hold = scored.get("dev", []), scored.get("holdout", [])
    if args.sweep:
        print()
        print(threshold_sweep(dev + hold))
    return 0


if __name__ == "__main__":
    sys.exit(main())
