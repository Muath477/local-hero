"""Dev CLI: talk to the platform before any frontend is wired up.

Usage:
    python main.py
    /upload <filename>   ingest a file already placed in data/uploads/ for RAG
    exit | quit
"""
import sys
import uuid

from orchestrator import ollama_client as ollama
from orchestrator.agent_manager import AgentManager
from orchestrator.hardware import resolve_tier, total_ram_gb
from orchestrator.memory import ConversationMemory
from orchestrator.router import Router
from tools.mcp_bridge import init_mcp_tools


def main():
    if not ollama.is_alive():
        print("Ollama isn't running on http://localhost:11434 — start it first (`ollama serve`).")
        sys.exit(1)

    tier = resolve_tier()
    print(f"[hardware] {total_ram_gb()} GB RAM detected -> tier '{tier}'")

    router = Router()
    manager = AgentManager()
    memory = ConversationMemory()
    session_id = str(uuid.uuid4())

    print("[router] تجهيز أولي (embedding للأمثلة)...")
    router.warm_up()
    reindexed = manager.documents.reindex_missing()  # RAG stays in step with data/uploads
    if reindexed:
        print(f"[rag] reindexed: {', '.join(reindexed)}")

    mcp_status = init_mcp_tools()
    for server, outcome in mcp_status.items():
        if isinstance(outcome, list):
            print(f"[mcp] {server}: {len(outcome)} أداة ({', '.join(outcome)})")
        else:
            print(f"[mcp] {server}: {outcome}")

    print("جاهز. اكتب رسالتك، أو '/upload اسم_الملف' لتحليل ملف من data/uploads، أو 'exit' للخروج.\n")
    while True:
        user_msg = input("أنت> ").strip()
        if user_msg.lower() in {"exit", "quit"}:
            break
        if not user_msg:
            continue

        if user_msg.startswith("/upload "):
            filename = user_msg[len("/upload "):].strip()
            try:
                n = manager.documents.ingest_file(filename)
                print(f"[rag] تم تقسيم وتخزين {n} مقطع من '{filename}'. اسأل عنه بأي وقت.\n")
            except ValueError as e:
                print(f"[error] {e}\n")
            continue

        decision = router.route(user_msg)
        print(f"[router] -> {decision['role']} ({decision['method']}, "
              f"confidence={decision['confidence']})")

        try:
            history = memory.get_history(session_id)
            result = manager.run(decision["role"], user_msg, history=history)
        except RuntimeError as e:
            print(f"[error] {e}")
            continue

        if result["tool_trace"]:
            for step in result["tool_trace"]:
                print(f"  [tool] {step['tool']}({step['args']}) -> "
                      f"{str(step['output'])[:200]}")

        if result.get("sources"):
            print(f"  [sources] {', '.join(sorted(set(result['sources'])))}")

        print(f"\nالمساعد> {result['content']}\n")

        memory.add_turn(session_id, "user", user_msg)
        memory.add_turn(session_id, "assistant", result["content"])


if __name__ == "__main__":
    main()
