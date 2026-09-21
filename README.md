# Local Hero

A fully local, private AI chat platform — a familiar chat interface, but
every model call, every uploaded file, and every stored message stays on
the machine it runs on. No cloud inference API, no third-party key, nothing
leaves the network unless *you* ask it to (web search tool aside).

Two pieces, one product, one repo:

| | |
|---|---|
| **`/` (this root)** | The web app — Next.js chat UI, accounts, admin dashboard |
| **`agents/`** | The backend — FastAPI service that routes messages to local models via Ollama |

The two talk over one plain HTTP contract (`AGENTS_URL`, default
`http://localhost:9099`) — the web app never touches a model directly, so
either side can be redeployed or swapped independently.

## Features

### Chat

- Streaming replies, per-user chat history (create/rename/delete)
- Edit a past message and regenerate from that point — everything after it
  is dropped and replayed, like editing a message in any modern chat app
- Auto model routing — a lightweight router classifies each message and
  picks the right backing model; there's also a manual model switcher for
  picking a specific agent role directly
- File upload with two paths, decided automatically by file type:
  - Any non-`.docx` file → RAG (chunked, embedded, retrieved per question)
  - `.docx` → Tarjuman, in-place document translation that preserves
    formatting, tables, and styles rather than dumping plain translated text
- Light/dark theme, RTL-first UI (Arabic primary, works in either direction)

### Accounts

- Email/password auth (bcrypt-hashed) and Google OAuth, side by side
- JWT session cookies (`jose`), 30-day expiry
- Live presence — a 30s heartbeat while a tab is open; the admin dashboard
  shows who's online right now
- Cross-tab session guard — switching accounts in one tab reloads any other
  open tab so it can't keep acting as the previous account

### Admin

- Role/permission system (`src/lib/permissions.ts`) — two roles today
  (`user`, `admin`), each route checks a named permission, not a raw role
  string, so adding a third role later is one table edit, not a grep-and-fix
- Suspend/reactivate any account — takes effect on that user's *next*
  request, even with an already-valid session cookie
- Create a chat on behalf of any user (support/demo use case)
- Real analytics: user/chat/message counts with period-over-period deltas,
  live online count, and **topic classification** — what people are
  actually using the assistant for, computed via embedding similarity
  against a seeded set of category examples (not word-frequency counting)

## Architecture

```text
Browser
  │
  ▼
Next.js (this root) ──Prisma──▶ MySQL
  │  auth, chat CRUD, admin API
  │
  ▼  AGENTS_URL (HTTP)
FastAPI (agents/)
  │  router → agent_manager → tools (RAG / Tarjuman / web search)
  │
  ▼
Ollama (host machine, not containerized)
  small local models, picked for weak/CPU-only hardware
```

`agents/config/models.yaml` is the single source of truth for which Ollama
model backs each agent role, its RAM budget, and its keep-alive window —
tuned for machines without a real GPU, not for throughput.

## Running it

**Docker (recommended):**

```bash
cp .env.example .env   # fill in AUTH_SECRET, Google OAuth creds if wanted
docker compose up -d --build
```

Brings up MySQL, the agents service, and the web app. Ollama itself stays on
the host (`ollama serve`, plus whatever models `agents/config/models.yaml`
expects pulled — `python agents/scripts/install_models.py --yes` installs them) — GPU passthrough for it isn't reliable in a Windows
container, so the `agents` container reaches out to `host.docker.internal:11434`
instead of running its own copy.

Open [http://localhost:3000](http://localhost:3000). First account to
register becomes admin automatically.

**Without Docker:**

```bash
# agents service
cd agents && pip install -r requirements.txt
uvicorn api.server:app --port 9099

# web app (separate terminal)
cp .env.example .env
npm install
npx prisma db push
npm run dev
```

## Environment variables

All in `.env` (see `.env.example`) for the web app:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | MySQL connection string |
| `AUTH_SECRET` | Session JWT signing key — set to a long random string |
| `AGENTS_URL` | Base URL of the FastAPI service |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | Google OAuth — leave blank to disable Google sign-in |

The `agents/` service reads `OLLAMA_HOST` (defaults to `http://localhost:11434`,
overridden to `http://host.docker.internal:11434` in `docker-compose.yml`).

## Project structure

```text
src/
  app/            Next.js routes — chat UI, /login, /settings, /admin, /api/*
  components/      Chat.tsx, AdminDashboard.tsx, LogoMark, Settings, Icons
  lib/            auth, permissions, db (Prisma client), topics (embedding
                   classification), agents.ts (HTTP client for AGENTS_URL)
  middleware.ts    route guard — redirects signed-out users to /login
prisma/
  schema.prisma    User / Chat / Message / Attachment, Role enum

agents/
  api/server.py           OpenAI-compatible-ish HTTP layer
  orchestrator/
    router.py             message → agent role (embedding match, then a 4B model when unsure)
    agent_manager.py      role → model dispatch, skills, tool loop, post-processing
    skills.py             skills/*.md loader
    postprocess.py        code punctuation fix, foreign-script repair
    memory.py             conversation memory + ChromaDB RAG store
    ollama_client.py       thin wrapper over Ollama's REST API
  tools/                  registry, builtin_tools (calculator, time, files, translation, HF search),
                          file_reader, tarjuman, web_search, mcp_bridge
  skills/                 instruction files injected on demand
  config/models.yaml      model registry per agent role, with the benchmark numbers behind each pick
  config/tarjuman_glossary.yaml   standard document terms for Tarjuman
  scripts/install_models.py       installs/verifies the models models.yaml needs
  benchmarks/  tests/     model benchmarks, routing eval, end-to-end smoke test; pytest suite + live quality gate
```

## Stack

Next.js 16 (App Router, Turbopack) · React 19 · Prisma/MySQL · Tailwind ·
FastAPI · Ollama · ChromaDB (RAG) · Docker Compose
