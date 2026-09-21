# Agents service (council of small agents, CPU-friendly)

> This folder is the backend of [Local Hero](../README.md): the FastAPI service the web app calls at `AGENTS_URL`.
> `docker compose up` (repo root) builds and runs it; everything below also works standalone from this folder.
> The HTTP contract the web app depends on is unchanged: `/v1/models`, `/v1/chat/completions`, `/upload`,
> `/tarjuman/translate`.

A local, multi-agent assistant. One request goes in, a router decides which small model handles it, that
model runs (with tools and skills if needed), and a single answer comes back — no manual model picking.
Everything runs on your machine through [Ollama](https://ollama.com).

## Why it's built this way

Target hardware: CPU-only laptops, ~16 GB RAM (a 2 GB laptop GPU can't hold an LLM). So:

- **One model generating at a time.** Agents run in a sequential handoff, not in parallel.
- **Small models, chosen by measurement.** Every model in `config/models.yaml` has its benchmark numbers in a
  comment next to it; `benchmarks/RESULTS.md` has the full tables. Nothing is picked by reputation.
- **Routing is (mostly) free.** Stage 1 is one embedding + cosine similarity. Stage 2 (a 4B model, constrained to
  a JSON schema of role names) only runs when stage 1 is ambiguous.

## Architecture

```
User message
    │
    ▼
Router (orchestrator/router.py)
    │  stage 1: Qwen3-Embedding-0.6B vs. per-role seed examples (decides ~70-90% of messages)
    │  stage 2: Qwen3-4B, schema-constrained JSON, few-shot prompt (only when stage 1 is unsure)
    ▼
AgentManager (orchestrator/agent_manager.py)
    │  role prompt + skills matched to the message (skills/*.md)
    │  tool roles -> tool-calling loop (tools/), plain roles -> one chat call
    │  post-processing: code punctuation fix, foreign-script repair
    ▼
Ollama (localhost:11434)
    ▼
Answer (+ tool trace, sources, skills used)
```

Roles: `writer_general`, `coder`, `researcher_rag`, `tool_caller`, `council` (delegates to the others),
`vision` (only via `run_vision`; text messages are never routed to it), plus the internal `router`,
`embedder` and `tarjuman` (document translation).

## Models (measured picks)

| role | model | size | why (details in `config/models.yaml`, `benchmarks/RESULTS.md`) |
|---|---|---|---|
| embedder | Qwen3-Embedding-0.6B | 0.64 GB | routing best-match 88% vs 62% for nomic-embed-text (English-centric) |
| router stage 2 · tarjuman | Qwen3-4B-Instruct-2507 | 2.5 GB | stage-2 accuracy 92% on unseen messages (0.5B ≈ chance); as accurate as 8B models at translation, faster, half the RAM |
| writer_general · researcher_rag | Qwen2.5-1.5B | 1.1 GB | writing 6/6, RAG 4/4 at 20 tok/s |
| tool_caller · council | Qwen2.5-3B | 1.9 GB | tool selection 11/11 (1.5B: 8/11) |
| coder | Qwen2.5-Coder-3B | 2.1 GB | 8/8 on executed code tasks |
| vision | moondream 1.8B | 1.7 GB | unchanged |

Install what `models.yaml` needs (downloads nothing without `--yes`):

```bash
python scripts/install_models.py          # shows installed / missing
python scripts/install_models.py --yes    # downloads and registers the missing ones
```

Ollama 0.34 can no longer pull `hf.co/...` tags (Hugging Face's new CDN host is blocked as a "redirect to a
different host"). The script downloads Qwen3-4B with `huggingface_hub` and registers it with `ollama create`;
for the two `hf.co/Qwen/...` tags it tries `ollama pull` first and tells you the library alternative if that fails.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
python scripts/install_models.py --yes

python main.py                                # terminal chat
uvicorn api.server:app --reload --port 8000   # web UI at http://localhost:8000 + OpenAI-compatible API
```

On startup the server (and CLI) warms the router and re-indexes any file in `data/uploads/` that the current
embedder hasn't indexed yet — switching the embedder in `models.yaml` starts a fresh vector collection, so old
vectors are never mixed with new ones.

## Routing

`benchmarks/routing_eval.py` measures the router on labelled Arabic (Gulf dialect and MSA) and English messages.
Final accuracy: **96% on the tuning set, 94% on a held-out set, 95% on fresh messages** (started at 58%).
What moved it: a multilingual embedder, thresholds calibrated for it (0.45 / 0.03), role descriptions that state the
deciding cue, seed examples covering live-data / calculation / file / translation requests, a 4B stage-2 model, and
Ollama's schema-constrained output so stage 2 can only answer with a real role.

Requests that need a tool (calculator, time, file list, translation, live data) route to `tool_caller`; plain
text translation goes there too, because that's where the dedicated translator is.

## Tools and skills

**Tools** (`tools/`, decorate a function with `@tool`; parameter types come from annotations; string arguments
like `"5"` are coerced and undeclared arguments dropped, since small models send both):

| tool | what it does |
|---|---|
| `web_search` | DuckDuckGo html → lite → the connected MCP search server (DuckDuckGo answers a rate-limited request with HTTP 202 and no results, which used to look like "nothing found") |
| `read_uploaded_file`, `list_uploaded_files` | txt / md / pdf / docx from `data/uploads/` only |
| `calculator` | exact arithmetic through a whitelisted AST evaluator (no `eval`) |
| `get_datetime` | date, weekday and time in any timezone |
| `translate_text`, `translate_document` | the Tarjuman engine, for text and for `.docx` files |
| `hf_model_search` | search Hugging Face for models |
| `ask_*_agent` | delegation, available to `council` only |

Each tool declares Arabic + English trigger `keywords` because descriptions are English but users write Arabic.
Tool output shown to the model is capped at 4000 characters (search results can be 10k+ and an over-long prompt is
silently truncated). There is deliberately **no "run Python" tool**: a blocklist is not a sandbox, and an agent that
reads web pages can be steered by text inside them.

**Skills** (`skills/*.md`, loaded by `orchestrator/skills.py`): a header (`name`, `roles`, `triggers`) and instructions.
A skill is appended to the system prompt only when its trigger appears in the message and the role matches (at most
two per message; `triggers: *` = always on for its roles). Shipped: `formal-arabic-writing`, `debugging-code`,
`code-writing`, `document-qa` (cite sources, say "not found" instead of guessing), `web-research`,
`document-translation`. Add your own by dropping a file in `skills/`.

## Tarjuman (document translation)

`POST /tarjuman/translate` (multipart `file` + `target_language`, optional `glossary` JSON) → translated `.docx`.
The model translates text; code protects the document (`tools/tarjuman.py`):

- Walks body, tables (nested, merged cells once), headers and footers; hyperlinks, images and fields stay in place.
- **Inline formatting**: bold/italic inside a sentence is restored after translation. The model never sees
  formatting tags — measured on Qwen3-4B, tags made it translate "two percent" as "200%". Instead the clean
  paragraph is translated first, then a second pass copies the tags onto the finished text and the result is
  accepted only if the text without tags is *identical* (formatting can be lost there, a word can't change).
- **RTL** is set in code as XML properties (paragraph `bidi`, run `rtl`, table `bidiVisual`, all in schema order).
  Alignment is left alone on purpose: in a bidi paragraph Word already starts on the right and left/right are mirrored.
- **Glossary**: `config/tarjuman_glossary.yaml` standard terms; a segment that is exactly one ("Confidential" → سري) is
  replaced with no model call, terms inside sentences are sent as mandatory terminology. Request terms override.
- **Speed**: numbers/URLs/emails never reach the model, identical segments are translated once, short segments are
  batched (≤12 per call), output length is capped, and results are cached in `data/tarjuman_cache.json` (a second run
  of the same document costs nothing). A 21-paragraph sample took 5 model calls instead of 21, 77 s cold, 0 s warm.
- The request runs in a worker thread, so the server stays responsive during a long translation.

Not covered: text boxes, footnotes, `.pptx` / `.xlsx`, PDFs (a PDF is a layout format — upload the original Word file).
`python -m benchmarks.tarjuman_live` translates a realistic sample and checks structure, formatting and RTL.

## Testing and benchmarking

```bash
pip install -r requirements-dev.txt
python -m pytest                 # ~430 offline tests, ~30 s, no Ollama needed
python -m pytest -m live         # quality gate: each role's CONFIGURED model must meet its bar (slow, needs Ollama)
```

The offline suite mocks Ollama and ChromaDB. It covers the router, hardware tiers, `models.yaml`, the tool loop,
skills, every built-in tool, the file-reader sandbox, Tarjuman (round trip, tags, tables, RTL order, cache, glossary),
the API (including upload path traversal) and the benchmark scorers themselves.

```bash
python -m benchmarks.run_benchmark --list
python -m benchmarks.run_benchmark --models qwen2.5:3b --categories tools coding
python -m benchmarks.routing_eval [--embedder TAG] [--router-model TAG] [--sweep]
python -m benchmarks.smoke_e2e          # real router + agents + tools + skills, answers checked
python -m benchmarks.tarjuman_live      # real .docx through Tarjuman
```

Benchmark prompts, tool sets, skills, parsing and post-processing all come from the production code paths, so a
model that passes passes for the reason it would in the app. To swap a role's model: change `ollama_tag` in
`config/models.yaml`, then `pytest -m live -k <role>`.

## Known limits

- `context_window` and `est_ram_gb` in `models.yaml` are documentation: no code sets Ollama's `num_ctx` (roles sharing
  a tag would otherwise reload the model between handoffs).
- Hardware-tier keys `force_fallback_small` / `allow_fallback_large` are not implemented.
- Hybrid Qwen3 checkpoints (`qwen3:4b`, `qwen3:8b`, `qwen3-1.7b`) ignore `think: false` on this Ollama build and leak
  their reasoning into replies; use the `-2507` Instruct builds (or `/no_think`, as the benchmark does).
- Vision has no path from the HTTP API or CLI yet (`AgentManager.run_vision` exists).
- Qwen2.5 1.5B/3B occasionally slip a word from another script into Arabic; `orchestrator/postprocess.py` repairs
  answers once with the translation model, which is also why translation never uses those models.
- Qwen2.5-3B and Coder-3B are, as far as I could tell, released under Qwen's research licence rather than Apache-2.0 — check their model pages before any commercial use.
- The `web_search` fallbacks depend on unofficial DuckDuckGo endpoints and the `free-search-mcp` server.

## Files

```
config/models.yaml            model registry + hardware tiers (each pick has its benchmark numbers)
config/tarjuman_glossary.yaml standard document terms per target language
config/mcp_servers.json       MCP servers connected at startup
orchestrator/
  hardware.py                 RAM detection -> tier
  ollama_client.py            REST wrapper (chat incl. think / num_predict / structured output, embed, embed_batch)
  router.py                   two-stage routing, seed examples, role descriptions
  agent_manager.py            runs a role's turn: skills, tool loop, RAG prompt, post-processing
  skills.py                   skills/*.md loader and matcher
  postprocess.py              code punctuation fix, foreign-script detection and repair
  memory.py                   ConversationMemory + DocumentStore (ChromaDB, per-embedder collection)
tools/
  registry.py                 @tool, typed schemas, argument coercion, keyword tool search
  builtin_tools.py            calculator, datetime, file list, translation, Hugging Face search
  web_search.py  file_reader.py  agent_tools.py  mcp_bridge.py  tarjuman.py
skills/                       instruction files injected on demand
api/server.py                 OpenAI-compatible FastAPI layer, /upload, /tarjuman/translate
web/index.html                single-file chat UI
scripts/install_models.py     install / verify the models models.yaml needs
benchmarks/                   run_benchmark, routing_eval, smoke_e2e, tarjuman_live, RESULTS.md
tests/                        offline suite + `live` quality gate
main.py                       CLI chat
```
