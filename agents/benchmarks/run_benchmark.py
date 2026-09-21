"""Runs the benchmark cases against local Ollama models and prints a comparison.

    python -m benchmarks.run_benchmark --list
    python -m benchmarks.run_benchmark --models qwen3:4b llama3.2:3b --categories coding tools
    python -m benchmarks.run_benchmark --models qwen3:4b --quick

Models run strictly one after another (the platform's own rule: one model in
RAM at a time) and each is unloaded when it finishes. Temperature 0 and a
fixed num_ctx so runs are comparable. Thinking is switched off — on this
CPU-only target a hidden reasoning trace would dominate latency, and it's how
the platform would have to run a hybrid model like Qwen3 anyway.

Results are saved after every model to benchmarks/results/<timestamp>.json
so a long run isn't lost if it's interrupted.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from .tasks import Case, build_cases

OLLAMA = "http://localhost:11434"
NUM_CTX = 4096
RESULTS_DIR = Path(__file__).parent / "results"
SKIP_BY_DEFAULT = ("embed", "moondream", "llava", "deepseek-r1")  # not text chat models / can't disable thinking


def installed_models() -> list[str]:
    r = requests.get(f"{OLLAMA}/api/tags", timeout=10)
    r.raise_for_status()
    return sorted(m["name"] for m in r.json()["models"])


def _post_chat(payload: dict, timeout: float) -> requests.Response:
    resp = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=timeout)
    if resp.status_code == 400 and "think" in payload and "think" in resp.text.lower():
        payload = {k: v for k, v in payload.items() if k != "think"}  # model has no thinking switch
        resp = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=timeout)
    return resp


def is_hybrid_qwen3(model: str) -> bool:
    """Original Qwen3 checkpoints mix thinking and non-thinking. Measured on this
    Ollama build: `think: false` is not honoured for qwen3:4b — the reasoning
    trace lands in the reply and eats the token budget. Qwen's own soft switch
    (/no_think in the system prompt) is the documented alternative. The
    *-2507 Instruct/Thinking releases are separate models with no switch."""
    low = model.lower()
    return "qwen3" in low and "2507" not in low and "embed" not in low and "vl" not in low and "coder" not in low


def ask(model: str, case: Case, timeout: float = 900) -> dict:
    system = case.system
    if is_hybrid_qwen3(model):
        system = ((system or "") + "\n/no_think").strip()
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": case.prompt}]
    payload = {
        "model": model, "messages": messages, "stream": False, "think": False, "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": case.max_tokens, "num_ctx": NUM_CTX},
    }
    if case.tools:
        payload["tools"] = case.tools
    resp = _post_chat(payload, timeout)
    if resp.status_code != 200:
        try:
            err = resp.json().get("error", resp.text)
        except ValueError:
            err = resp.text
        return {"error": f"HTTP {resp.status_code}: {err}"[:200], "content": "", "tool_calls": []}
    body = resp.json()
    msg = body["message"]
    eval_s = (body.get("eval_duration") or 0) / 1e9
    return {
        "content": msg.get("content", "") or "", "tool_calls": msg.get("tool_calls") or [],
        "tokens": body.get("eval_count", 0), "truncated": body.get("done_reason") == "length",
        "tok_per_s": round(body["eval_count"] / eval_s, 2) if eval_s and body.get("eval_count") else None,
        "load_s": round((body.get("load_duration") or 0) / 1e9, 1),
    }


def unload(model: str) -> None:
    try:
        requests.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)
    except requests.RequestException:
        pass


def run_case(model: str, case: Case) -> dict:
    started = time.time()
    try:
        reply = ask(model, case)
    except requests.RequestException as e:
        reply = {"error": f"{type(e).__name__}: {e}"[:200], "content": "", "tool_calls": []}
    if "error" in reply:
        passed, detail = False, reply["error"]
    else:
        passed, detail = case.check(reply)
        if not passed and reply.get("truncated"):
            detail += f" [reply cut off at the {case.max_tokens}-token cap]"
    return {
        "case": case.id, "passed": passed, "detail": detail, "seconds": round(time.time() - started, 1),
        "tok_per_s": reply.get("tok_per_s"), "tokens": reply.get("tokens"),
        "reply": (reply["content"] or json.dumps(reply["tool_calls"], ensure_ascii=False))[:400],
    }


def run_category(model: str, category: str, quick: bool = False, log=print) -> list[dict]:
    cases = build_cases()[category]
    if quick:
        cases = cases[:3]
    out = []
    for case in cases:
        res = run_case(model, case)
        log(f"    {'PASS' if res['passed'] else 'FAIL'}  {category}/{case.id:<22} {res['seconds']:>6.1f}s  {res['detail']}")
        out.append(res)
    return out


def summarize(results: dict) -> str:
    cats = sorted({c for m in results.values() for c in m["categories"]})
    head = "| model | " + " | ".join(cats) + " | overall | median tok/s | total min |"
    lines = [head, "|" + "---|" * (len(cats) + 4)]
    for model, data in results.items():
        cells, passed, total, speeds = [], 0, 0, []
        for c in cats:
            rows = data["categories"].get(c)
            if rows is None:
                cells.append("–")
                continue
            p = sum(r["passed"] for r in rows)
            cells.append(f"{p}/{len(rows)}")
            passed += p
            total += len(rows)
            speeds += [r["tok_per_s"] for r in rows if r["tok_per_s"]]
        pct = f"{100 * passed / total:.0f}%" if total else "–"
        med = f"{statistics.median(speeds):.1f}" if speeds else "–"
        lines.append(f"| {model} | " + " | ".join(cells) + f" | {pct} | {med} | {data['seconds'] / 60:.1f} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", help="Ollama tags (default: every installed chat model)")
    ap.add_argument("--categories", nargs="*", choices=sorted(build_cases()), help="default: all")
    ap.add_argument("--quick", action="store_true", help="first 3 cases per category")
    ap.add_argument("--list", action="store_true", help="list installed models and exit")
    ap.add_argument("--summarize", nargs="+", metavar="JSON", help="merge saved result files into one table and exit")
    args = ap.parse_args(argv)

    # Details quote Arabic replies; a Windows console/pipe defaults to cp1252 and would crash mid-run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.summarize:
        merged: dict = {}
        for f in args.summarize:
            merged.update(json.loads(Path(f).read_text(encoding="utf-8")))
        print(summarize(merged))
        return 0

    try:
        have = installed_models()
    except requests.RequestException:
        print("Ollama isn't reachable on localhost:11434 — start it first.")
        return 1
    if args.list:
        print("\n".join(have))
        return 0

    models = args.models or [m for m in have if not any(s in m for s in SKIP_BY_DEFAULT)]
    missing = [m for m in models if m not in have]
    if missing:
        print(f"Not installed (pull them first): {missing}")
        return 1
    categories = args.categories or sorted(build_cases())

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results: dict = {}
    for model in models:
        print(f"\n=== {model} ===", flush=True)
        started = time.time()
        results[model] = {"categories": {c: run_category(model, c, args.quick, log=lambda s: print(s, flush=True))
                                         for c in categories}}
        results[model]["seconds"] = time.time() - started
        unload(model)
        (RESULTS_DIR / f"{stamp}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    table = summarize(results)
    (RESULTS_DIR / f"{stamp}.md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
