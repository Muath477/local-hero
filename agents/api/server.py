"""OpenAI-compatible HTTP layer in front of the router + agent manager.

Doubles as the "agents service" the LocalHero frontend (C:\\LocalHero\\app,
a separate Next.js project) already expects on port 9099 — its
src/lib/agents.ts was written against exactly this contract before this
file existed. Run on 9099 and LocalHero needs zero config changes:

    uvicorn api.server:app --reload --port 9099

"auto" is always available and behaves like the old single-virtual-model
design (the router decides per message, nobody has to pick). The named
entries in MODEL_CATALOG additionally let a client pick a role directly,
which LocalHero's model-switcher UI expects — both are one project's
router, never two separate systems.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from orchestrator.agent_manager import AgentManager
from orchestrator.hardware import resolve_tier, total_ram_gb
from orchestrator.memory import UPLOADS_DIR
from orchestrator.router import Router
from tools.mcp_bridge import init_mcp_tools
from tools.tarjuman import translate_docx

VIRTUAL_MODEL_NAME = "council"

# id -> (role, display name, description). "general-assistant-plus" matches
# the Prisma schema's Chat.model default in LocalHero, so a brand-new chat
# there (before the user ever opens the model switcher) already resolves
# to something real instead of a 404.
MODEL_CATALOG: dict[str, dict] = {
    "auto": {
        "role": None,  # None = ask the router per message
        "name": "تلقائي",
        "description": "يختار أفضل مساعد لطلبك تلقائياً",
    },
    "general-assistant-plus": {
        "role": "writer_general",
        "name": "المساعد العام",
        "description": "كتابة، حوار، أسئلة عامة",
    },
    "file-analyst": {
        "role": "researcher_rag",
        "name": "محلل الملفات",
        "description": "يقرأ ويجاوب من الملفات المرفوعة",
    },
    "coder": {
        "role": "coder",
        "name": "المبرمج",
        "description": "كتابة وتصحيح الأكواد",
    },
    "tool-caller": {
        "role": "tool_caller",
        "name": "الباحث والتنفيذ",
        "description": "بحث بالإنترنت وتنفيذ الأدوات",
    },
}

app = FastAPI(title="Local AI Platform")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# Loaded once at process start, not per-request: warm_up() embeds every
# router seed example, and AgentManager reads config/models.yaml once.
router = Router()
manager = AgentManager()


@app.on_event("startup")
def _warm_up_router():
    # Without this, the first real user message pays for ~40 sequential
    # embedding calls (measured: over a minute on CPU) instead of the
    # server's own boot time absorbing that cost where nobody is waiting on it.
    router.warm_up()
    init_mcp_tools()  # connects config/mcp_servers.json, registers their tools


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str | None = None
    messages: list[ChatMessage]
    stream: bool | None = False


WEB_DIR = Path(__file__).parent.parent / "web"


@app.get("/")
def index():
    # A single self-contained HTML file (inline CSS/JS, no build step) —
    # keeps the whole stack to one process: `uvicorn api.server:app`.
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "hardware_tier": resolve_tier(), "ram_gb": total_ram_gb()}


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": mid, "object": "model", "created": 0, "owned_by": "local",
             "name": spec["name"], "description": spec["description"]}
            for mid, spec in MODEL_CATALOG.items()
        ],
    }


def _resolve_role(requested_model: str | None, user_message: str) -> dict:
    spec = MODEL_CATALOG.get(requested_model or "auto", MODEL_CATALOG["auto"])
    if spec["role"] is not None:
        return {"role": spec["role"], "method": "explicit", "confidence": None}
    return router.route(user_message)


def _run_chat(req: ChatCompletionRequest) -> tuple[dict, dict]:
    if not req.messages:
        raise HTTPException(400, "messages must not be empty")

    *history_msgs, last = req.messages
    if last.role != "user":
        raise HTTPException(400, "the last message must have role 'user'")

    # OpenAI-compatible clients resend the full transcript every call, so
    # history comes straight from the request — no server-side session
    # store needed for this endpoint (LocalHero keeps its own history in
    # Prisma and resends it; the CLI has no client to do that for it,
    # which is why main.py uses ConversationMemory instead).
    history = [
        {"role": m.role, "content": m.content}
        for m in history_msgs if m.role in ("user", "assistant")
    ]

    decision = _resolve_role(req.model, last.content)
    try:
        result = manager.run(decision["role"], last.content, history=history)
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return decision, result


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest):
    decision, result = _run_chat(req)

    if req.stream:
        return StreamingResponse(
            _sse_stream(result["content"], decision["role"]),
            media_type="text/event-stream",
        )

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": f"{VIRTUAL_MODEL_NAME}:{decision['role']}",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result["content"]},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        # Non-standard extra fields — ignored by strict OpenAI clients,
        # useful for debugging which agent/tools/sources actually ran.
        "x_router": decision,
        "x_tool_trace": result.get("tool_trace", []),
        "x_sources": result.get("sources", []),
    }


async def _sse_stream(content: str, role: str):
    """Fakes token-by-token streaming: the answer is already fully
    generated by the time this runs (agent_manager.run is synchronous —
    real incremental streaming would mean teaching it to yield partial
    Ollama chunks through the tool-calling loop, a bigger change than
    this integration needed). Chunking the finished text client-side
    still gets LocalHero's existing typing-effect UI right, which is the
    part that's actually visible to a user.
    """
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    words = content.split(" ")
    for i, word in enumerate(words):
        piece = word if i == 0 else " " + word
        chunk = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": f"{VIRTUAL_MODEL_NAME}:{role}",
            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.02)
    yield "data: [DONE]\n\n"


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.post("/tarjuman/translate")
async def tarjuman_translate(file: UploadFile = File(...), target_language: str = Form(...)):
    """Contract fixed by LocalHero's src/app/api/translate/route.ts: multipart
    file + target_language in, raw translated .docx bytes out on success,
    {"error": {"message": ...}} JSON on failure. Paragraph-level translation
    only — see tools/tarjuman.py's docstring for exactly what "preserves
    formatting" does and doesn't cover.
    """
    # LocalHero's route.ts reads `(await res.json()).error?.message` on a
    # non-200 — that shape, not FastAPI's default {"detail": ...}, is what
    # has to come back here, so these are JSONResponse, not HTTPException.
    if not file.filename.lower().endswith(".docx"):
        return JSONResponse(status_code=415, content={"error": {"message": "only .docx files are supported"}})

    source_bytes = await file.read()
    try:
        translated_bytes = translate_docx(source_bytes, target_language)
    except Exception as e:
        return JSONResponse(status_code=502, content={"error": {"message": f"translation failed: {e}"}})

    return Response(content=translated_bytes, media_type=DOCX_MEDIA_TYPE)


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Not part of the OpenAI spec — this project's own endpoint for
    getting a file into data/uploads/ and indexed for RAG in one call.
    """
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / file.filename
    dest.write_bytes(await file.read())
    try:
        n_chunks = manager.documents.ingest_file(file.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"filename": file.filename, "chunks_indexed": n_chunks}
