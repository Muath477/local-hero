# Local Hero — developer notes

Read `README.md` first for the architecture, features and setup. This file is the short developer
supplement: what to run to verify a change, what not to break, and where things actually live.

## Two pieces, two processes, one contract

- **Web app** (this root) — Next.js 16 App Router, Prisma → MySQL, RTL Arabic UI, dark-first theme.
  Run local dev on **:3000** (`npm run dev -- -p 3000`). That port is not a free choice:
  `GOOGLE_REDIRECT_URI` in `.env` is `http://localhost:3000/api/auth/google/callback` and must match what is
  registered in Google Cloud Console exactly, so any other port silently breaks Google sign-in with a
  connection-refused on the callback. Docker Compose also uses :3000 (`docker-compose.yml`).
- **Agents service** (`agents/`) — FastAPI + Ollama, OpenAI-compatible `/v1/chat/completions`. The web app only
  ever talks to it over HTTP via `AGENTS_URL` (`src/lib/agents.ts`) — never import Python from the web app or
  vice versa.

`agents/` is the only copy of the backend. Edit it directly and run it with its own environment
(`agents/.venv`, created with `python -m venv .venv && pip install -r requirements.txt`). Don't keep a second copy
elsewhere: two copies drift.

## Running it on Windows

`./start-local-hero.ps1` from the repo root starts MySQL (Docker), the agents service (on the port from
`AGENTS_URL` in `.env`, using `agents/.venv`) and the Next.js app. Docker Desktop must already be running, and
Ollama must be up on :11434.

Two problems hit on the dev machine:

- **`import docx` fails with "Application Control policy has blocked this file"** — Smart App Control blocked the
  compiled `lxml` 6.1.3 module. Pin `lxml==6.1.2` in `agents/.venv` (6.1.2, 6.0.4 and 6.0.2 load fine).
- **Docker Desktop shows an error dialog and the engine never starts** — check
  `%LOCALAPPDATA%\Docker\log\host\com.docker.backend.exe.log`. If it says `listening on unix://...: remove ...: The file
  cannot be accessed by the system`, a crash left undeletable stale AF_UNIX socket files
  (`%LOCALAPPDATA%\Docker\run\*` and `%LOCALAPPDATA%\docker-secrets-engine\engine.sock`). They can't be deleted, but
  renaming their *folders* (e.g. `run` -> `run.stale`) lets Docker recreate them; then start Docker Desktop from your
  own desktop session.

## Verifying a change

```bash
npx tsc --noEmit                       # web app — must be zero errors
cd agents && python -m pytest          # ~430 offline tests, ~30 s, no Ollama needed
```

After changing a model tag, a prompt, a tool description or a skill, also run the live gate (needs Ollama and the
models pulled; ~10 minutes on CPU): `cd agents && python -m pytest -m live`. It benchmarks each role's *configured*
model against a minimum pass rate. `python -m benchmarks.smoke_e2e` runs real requests through router, agents,
tools and skills and checks the content of the answers.

The offline suite mocks Ollama, so it proves the plumbing, not the models. For `agents/` logic changes, call the
endpoint for real (`ollama serve`, `uvicorn api.server:app --port 9099`, then `curl .../v1/chat/completions`)
rather than trusting a code read. Small local models (0.5B–4B) are unreliable in ways that only show up by running
them — see the comments in `agents/config/models.yaml` for measured examples (Arabic mistranslations, wrong-language
leaks, a model silently dropping part of a multi-part answer). Don't assume a prompt tweak fixed something like that:
rerun the actual request.

## Conventions

- RTL Arabic is the primary language throughout the web UI; every new string is Arabic first, `dir="rtl"` semantics
  respected (see how `login/page.tsx` mirrors its two-column layout for RTL).
- No emoji as UI icons — draw an inline SVG in `src/components/Icons.tsx` matching the existing stroke style (24px
  viewBox, `stroke-width: 1.8`, round caps) and import it.
- The brand mark is `src/components/LogoMark.tsx` (`public/logo-mark.png`) — the red duck-in-a-hero-mask logo. Use the
  component as-is.
- `agents/config/models.yaml` is the single source of truth for which Ollama model backs each role. Don't hardcode a
  model tag anywhere else. Every pick there has its measured numbers in a comment (see
  `agents/benchmarks/RESULTS.md`); change a tag only with a benchmark to justify it.
  `python agents/scripts/install_models.py` installs what the file needs.
- Tools live in `agents/tools/` (`@tool`, with Arabic + English `keywords`), skills in `agents/skills/*.md`
  (instructions injected only when their triggers match). Don't add a code-execution tool: a blocklist is not a sandbox.
- Tarjuman never sends inline-formatting tags to the model (they made it mistranslate numbers); read
  `agents/tools/tarjuman.py`'s docstring before changing how formatting is preserved.
- Multi-agent delegation (`agents/tools/agent_tools.py`, the `council` role in `agent_manager.py`) is real: the
  `council` role gets `ask_writer_agent` / `ask_coder_agent` / `ask_researcher_agent` / `ask_tools_agent` as tool
  calls and delegates sub-tasks to other local agents, sequentially (this hardware runs one Ollama model at a time —
  there is no concurrent multi-model execution, by design). If you touch this, re-run a compound test request and
  check that `x_tool_trace` in the response shows more than one agent being consulted — see
  `_backfill_dropped_delegates` in `agent_manager.py` for why the raw tool trace, not just the model's own prose, is
  what's trustworthy here.
