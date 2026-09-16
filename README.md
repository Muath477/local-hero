# Local Hero

A fully local, private AI chat platform. No cloud, no API keys to a third
party, no data leaving the machine it runs on. Two pieces, one product:

- **`/` (this root)** — the web app: Next.js chat UI, auth (email/password +
  Google), per-user chat history, admin dashboard with real embedding-based
  topic analytics, role/permission system, live presence.
- **`agents/`** — the backend: a FastAPI service that routes each message to
  a small local model via [Ollama](https://ollama.com), with RAG over
  uploaded files and Tarjuman (document translation preserving formatting).

The two talk over a plain HTTP contract (`AGENTS_URL`) — the web app never
calls a model directly.

## Running it

**Docker (recommended):**

```bash
cp .env.example .env   # fill in AUTH_SECRET, Google OAuth creds if wanted
docker compose up -d --build
```

This brings up MySQL, the agents service, and the web app. Ollama itself
stays on the host (`ollama serve`, plus whatever models `agents/config/models.yaml`
expects pulled) — Docker Desktop's GPU passthrough for it isn't reliable on
Windows, so the `agents` container reaches out to `host.docker.internal:11434`
instead of running its own copy.

Open [http://localhost:3000](http://localhost:3000).

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

## Stack

Next.js 16 (App Router) · Prisma/MySQL · FastAPI · Ollama · ChromaDB (RAG) ·
Tailwind
