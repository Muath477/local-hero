# Local AI Platform (Council-of-Agents, CPU-friendly)

A local, multi-agent assistant. One request goes in, a router decides which
small model handles it, that model runs (with tools if needed), and a
single answer comes back — no manual model picking.

## Why it's built this way

Target hardware: CPU-only laptops, ~16GB RAM or less (no usable GPU — a
2GB laptop GPU can't hold an LLM). That rules out running several 7B+
models concurrently. So:

- **One model loaded at a time.** Ollama auto-loads/unloads per
  `keep_alive`; agents run in a **sequential handoff**, not in parallel.
- **Models are 0.5B–3B by default**, pulled straight from Hugging Face
  (`hf.co/Qwen/...-GGUF`, not Ollama's library) for the smallest footprint
  that still works reliably — see "Model sizing" below for what "reliably"
  ruled out. 7B+ only turns on when `orchestrator/hardware.py` detects a
  stronger machine (`tiers` in `config/models.yaml`).
- **Routing is (mostly) free.** Stage 1 is a single embedding + cosine
  similarity — no LLM call. Stage 2 (a tiny 0.5B model) only fires when
  stage 1 is ambiguous.

## Architecture

```
User message
    │
    ▼
Router (orchestrator/router.py)
    │  stage 1: embed message, cosine-match against per-role examples
    │  stage 2 (rare): Qwen2.5-0.5B classifies as strict JSON
    ▼
AgentManager (orchestrator/agent_manager.py)
    │  picks the model for the chosen role from config/models.yaml
    │  role = tool_caller -> tool-calling loop (tools/registry.py)
    │  other roles        -> single chat call
    ▼
Ollama (localhost:11434) — loads/unloads models per keep_alive
    │
    ▼
Answer (+ tool trace if any tools ran)
```

Agent roles today: `writer_general`, `coder`, `researcher_rag`,
`tool_caller`, `vision` (disabled on the `min` hardware tier).

## Setup

1. **Install [Ollama](https://ollama.com/download)** and make sure it's
   running (`ollama serve`, or it auto-starts on Windows/Mac).

2. **Models** — pulled straight from Hugging Face via Ollama's `hf.co/...`
   support (not Ollama's own library), picked for minimum footprint:

   ```bash
   ollama pull hf.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF:Q4_K_M         # router — 491 MB
   ollama pull hf.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF:Q4_K_M         # writer/RAG/tools — 1.1 GB
   ollama pull hf.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF:Q4_K_M     # coder — 1.9 GB
   ollama pull nomic-embed-text                                     # embeddings — 274 MB
   ollama pull moondream:1.8b                                       # vision — 1.7 GB
   ```

   ~5.5 GB total. See "Model sizing" below for why coder is 3B while
   everything else is 0.5–1.5B. Optional upgrade once things are stable:

   ```bash
   ollama pull qwen2.5-coder:7b   # noticeably better than the 3B default, opt-in fallback_tier: high
   ```

3. **Python deps:**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate      # Windows
   pip install -r requirements.txt
   ```

4. **Run it** — CLI, or the actual web UI:

   ```bash
   python main.py                              # terminal chat
   # or
   uvicorn api.server:app --reload --port 8000 # then open http://localhost:8000
   ```

   You'll see the detected hardware tier, then a prompt. Try a coding
   question, then a "search the web for..." question, and watch the
   `[router]` line show which agent it picked.

## What's scaffolded vs. what's next

**Working now (all verified live against Ollama, not just syntax-checked):**
router (semantic + LLM fallback), sequential agent execution, tool-calling
loop, two real tools (`web_search`, `read_uploaded_file`), hardware-tier
detection, per-session conversation history (`orchestrator/memory.py ::
ConversationMemory`), and document RAG for the `researcher_rag` role
(`DocumentStore`: chunk -> embed -> store in ChromaDB -> retrieve-then-generate).

Try the RAG path: `python main.py`, then `/upload test_doc.txt` (a sample
file is already in `data/uploads/`), then ask a question about it.

Known first-run hiccup: the very first time `DocumentStore` creates
`data/chroma/`, something (looked like antivirus scanning the new SQLite
file) made one run take several minutes instead of ~5 seconds. It hasn't
recurred since. If `python main.py` seems to hang on first use, give it a
minute before assuming something's broken.

**HTTP API (`api/server.py`)** — an OpenAI-compatible layer in front of the
same router + agent manager, verified live:

```bash
uvicorn api.server:app --reload --port 8000
```

- `GET /health`, `GET /v1/models` (always returns one virtual model,
  `"council"` — whatever `model` the client sends is ignored, the router
  decides per message)
- `POST /v1/chat/completions` — standard OpenAI shape; point Open WebUI's
  "OpenAI API" connection at `http://localhost:8000/v1` and chat works
  with automatic routing, no model picker needed
- `POST /upload` — multipart file upload, saves to `data/uploads/` and
  indexes it into `DocumentStore` in one call (this one is *not* part of
  the OpenAI spec — it's this project's own endpoint, for whatever
  frontend eventually handles uploads)
- `GET /` — serves `web/index.html`, this project's own chat frontend
  (see below)

**Web UI (`web/index.html`)** — one self-contained HTML file, no build
step, no framework, served directly by FastAPI at `http://localhost:8000`.
RTL, dark theme, chat bubbles, a 📎 button that uploads straight to
`/upload` and indexes for RAG, and a badge under each assistant reply
showing which agent handled it (and its tool calls / cited sources, when
any). History persists in `localStorage` across reloads. Verified live in
a real browser end to end: plain chat, file upload, and a RAG question
that correctly cited the uploaded file and answered from its content —
not from the model's own guess.

## Model sizing — what was actually tested, not guessed

Every role started as small as plausible (0.5B–1.5B, all pulled from
Hugging Face) and only got bumped up where testing showed it necessary:

- **router / writer_general / researcher_rag / tool_caller → 0.5B–1.5B.**
  Tested directly: routing accuracy (0.9+ confidence on every test case),
  a formal Arabic apology email (coherent, correctly formal), a RAG
  question answered from an uploaded file (correct). No failures found —
  these roles stayed at the smallest tier.
- **coder → 3B, not 1.5B.** This one failed testing at 1.5B specifically
  on *Arabic* coding instructions — not a vocabulary problem (it handled
  "العاملي (factorial)" correctly), but "اكتب لي دالة بايثون تعكس نص"
  ("write a function that reverses a string") twice produced an unrelated
  `print`-only function instead of `text[::-1]`. The exact same instruction
  in English worked fine at 1.5B. Bumping to Qwen2.5-Coder-3B fixed both
  cases on the first try, for +800MB. `qwen2.5:7b` is wired as a further
  opt-in `fallback_tag` (hardware tier `"high"` only) — it measured 12+
  minutes for one request on this CPU-only laptop, unusable as a default.
- **vision → moondream:1.8b.** Already about as small as a usable vision
  model gets; not changed.

Total footprint: ~5.5GB across 5 models, down from the original
qwen2.5:3b/7b + phi3:mini lineup's ~9GB, with every non-coder role now
running less than half the parameters per request.

**Separately measured and fixed:** router `warm_up()` (embeds ~35 seed
examples) was taking 74s+ doing one Ollama call per example — switched to
Ollama's batched `/api/embed` endpoint (`ollama_client.embed_batch`), now
~3s. Both `main.py` and `api/server.py` call `warm_up()` eagerly at
startup so this cost never lands on a user's first message.

**MCP support (`tools/mcp_bridge.py`)** — external MCP (Model Context
Protocol) servers plug straight into the same tool-calling loop as native
`@tool` functions, no changes needed elsewhere. Currently wired to
[`free-search-mcp`](https://github.com/sweetcornna/free-search-mcp)
(no API key — DuckDuckGo/Mojeek/Google News), which exposes 11 tools
(`search`, `fetch`, `research`, `read_doc`, `download`, ...). Verified
live end-to-end: a direct call to `mcp_search_search` returned real,
current results (tested query: "latest Python version release date" —
correctly found Python 3.14.7).

Two real bugs turned up during that verification and got fixed, not
glossed over:

1. **Dependency conflict.** `free-search-mcp` requires `mcp>=2`; the
   previously-wired `mcp-server-fetch` requires `mcp<2` — installing both
   breaks one of them (confirmed: it broke fetch). Fixed by dropping
   `mcp-server-fetch` entirely, since `free-search-mcp`'s own `fetch` tool
   covers the same job — one server instead of two, no conflict.
2. **Breaking API change.** `mcp` 2.x renamed `Tool.inputSchema` (camelCase)
   to `Tool.input_schema` (snake_case); `mcp_bridge.py` was still reading
   the old name and every tool failed to register until this was caught
   and fixed.

**Known remaining rough edge:** the 1.5B `tool_caller` model sometimes
sends a string where a tool parameter expects an array (e.g.
`engines: "duckduckgo+mojeek"` instead of `["duckduckgo", "mojeek"]`) —
`mcp_bridge.py::_coerce_args` now auto-splits that case before the call
reaches the server, which fixed the crash, but the model doesn't always
synthesize a clean final answer from a large tool result (tested case:
echoed the result's markdown header instead of the actual release date
buried a few lines down). The search infrastructure is confirmed
correct; the small model's summarization-after-tool-use is the part
still worth tuning (better system prompt, or accept `writer_general`-tier
quality here needs a slightly bigger model, mirroring the `coder` finding
above).

**Second rough edge, isolated during a later live test:** `tool_caller`
flatly refuses ("I can't access the internet") on the exact phrase
"latest Windows version" — but the *identical* sentence structure asking
about gold prices or weather correctly triggers `web_search` every time.
Confirmed topic-specific, not phrasing-specific, by swapping only the
subject across three otherwise-identical requests. Rewriting the system
prompt to explicitly forbid this refusal (tested) did not change the
behavior — this looks like a refusal pattern baked into the base model's
own training (plausibly software-version questions getting associated
with piracy during alignment), not something fixable from the prompt or
this codebase. Worth knowing if a request mysteriously gets refused:
try rephrasing the *subject*, not just the sentence around it.

To add another server, add an entry to `config/mcp_servers.json`:

```json
{
  "mcpServers": {
    "search": { "command": "free-search-mcp", "args": [] },
    "your-server": { "command": "some-mcp-server", "args": ["--flag"] }
  }
}
```

Each tool the server exposes gets registered as `mcp_<server>_<tool>` with
its real JSON-schema parameters (not the naive all-strings schema native
`@tool` functions get) — the server describes its own arguments. Both
`main.py` and `api/server.py` call `init_mcp_tools()` at startup, same
pattern as router warm-up. A server that fails to start is logged and
skipped; it doesn't take down the others or the app.

**Not built yet — next steps, in the order they unblock each other:**

1. **More native tools** — add functions to `tools/`, decorate with
   `@tool(...)`; nothing else needs to change. Once past ~15-20 tools
   total (native + MCP), swap `tools/registry.py::search_tools` from
   keyword matching to the same embedding approach `router.py` uses.
2. **Tune tool_caller's synthesis quality** — see the rough edge noted
   above; likely a system-prompt fix before reaching for a bigger model.
3. **Voice** — `whisper.cpp` (speech-to-text) and `piper` (text-to-speech)
   both run fully local and are light enough for this hardware tier; wire
   them in front of/behind the FastAPI layer.
4. **Vision** — role exists in the registry and is auto-disabled below the
   `std` tier; `agent_manager.py` doesn't yet pass image bytes through
   (Ollama's `/api/chat` takes an `images` field per message — not wired).

## Files

```
config/models.yaml       model registry + hardware tiers
config/mcp_servers.json  MCP servers to connect at startup
orchestrator/
  hardware.py             RAM detection -> tier
  ollama_client.py         raw REST wrapper (chat, embed batched/single, tag check)
  router.py                semantic + LLM-fallback routing
  agent_manager.py         runs one role's turn, incl. tool-calling loop + RAG prompt building
  memory.py                ConversationMemory (per-session JSON) + DocumentStore (ChromaDB RAG)
api/
  server.py                OpenAI-compatible FastAPI layer (/v1/chat/completions, /upload, serves web/)
web/
  index.html               single-file chat frontend (RTL, dark, upload, agent/tool/source badges)
tools/
  registry.py              @tool decorator, schema generation, tool search
  web_search.py            DuckDuckGo HTML search, no API key, retries on connection reset
  file_reader.py           reads txt/md/pdf from data/uploads/ only
  mcp_bridge.py            connects config/mcp_servers.json, registers their tools into the same registry
main.py                    CLI: chat, '/upload <file>' to ingest into RAG, keeps per-session history
```
