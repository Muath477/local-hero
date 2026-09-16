## Local Hero — agent operating guide

Read `README.md` first for the full architecture, features and setup —
this file is the short, agent-specific supplement: what to run to verify
a change, what not to break, and where things actually live.

## Two pieces, two processes, one contract

- **Web app** (this root) — Next.js 16 App Router, Prisma → MySQL, RTL
  Arabic UI, dark-first theme. Run local dev on **:3000**
  (`npm run dev -- -p 3000`) — not a free choice: `GOOGLE_REDIRECT_URI`
  in `.env` is hardcoded to `http://localhost:3000/api/auth/google/callback`
  and must match what's registered in Google Cloud Console exactly, so
  running on any other port (e.g. `start-local-hero.ps1`'s `-p 3001`,
  picked to avoid clashing with other local Next.js apps) silently breaks
  Google sign-in with a connection-refused on the callback — already hit
  this once. Docker Compose also uses :3000 (`docker-compose.yml`).
- **Agents service** (`agents/`) — FastAPI + Ollama, OpenAI-compatible
  `/v1/chat/completions`. The web app only ever talks to it over HTTP via
  `AGENTS_URL` (`src/lib/agents.ts`) — never import Python from the web
  app or vice versa.

**`agents/` in this repo is the only copy that matters.** A standalone
clone of the same backend exists on the dev machine at
`local-ai-platform/` outside this repo, from before the two projects
were merged (see the "دمج LocalHero" / merge commit history). It still
has its own `.venv` and is convenient to iterate in locally, but it is
**not** what ships, what Docker builds, or what another agent cloning
this repo will see — after editing backend logic there, copy the
changed files into `agents/` (or better, just edit `agents/` directly)
before considering the change done. Letting the two drift is a real,
already-observed failure mode, not a hypothetical one.

## Verifying a change

```bash
npx tsc --noEmit          # web app — must be zero errors before calling a change done
cd agents && python -m py_compile api/server.py orchestrator/*.py tools/*.py
```

There is no Python test suite yet — for `agents/` logic changes, actually
call the endpoint (`ollama serve`, `uvicorn api.server:app --port 9099`,
then `curl .../v1/chat/completions`) rather than trusting a code read. Small
local models (0.5B–3B) are genuinely unreliable in ways that only show up
by running them — see `agents/config/models.yaml`'s comments for several
measured examples (Arabic mistranslations, wrong-language leaks, a model
silently dropping part of a multi-part answer). Don't assume a prompt
tweak fixed something like that — rerun the actual request.

## Conventions that aren't optional

- RTL Arabic is the primary language throughout the web UI; every new
  string is Arabic first, `dir="rtl"` semantics respected (see how
  `login/page.tsx` mirrors its two-column layout for RTL).
- No emoji as UI icons — draw an inline SVG in `src/components/Icons.tsx`
  matching the existing stroke style (24px viewBox, `stroke-width: 1.8`,
  round caps) and import it. This was a deliberate cleanup (emoji icons
  were replaced repo-wide); don't reintroduce them.
- The brand mark is `src/components/LogoMark.tsx` (`public/logo-mark.png`)
  — the red duck-in-a-hero-mask logo. Don't swap in a different mascot or
  a hand-drawn SVG approximation of it; use the component as-is.
- `agents/config/models.yaml` is the single source of truth for which
  Ollama model backs each role. Don't hardcode a model tag anywhere else.
- Multi-agent delegation (`agents/tools/agent_tools.py`, the `council`
  role in `agent_manager.py`) is real: the `council` role gets
  `ask_writer_agent` / `ask_coder_agent` / `ask_researcher_agent` as tool
  calls and can genuinely delegate sub-tasks to other local agents,
  sequentially (this hardware runs one Ollama model at a time — there is
  no concurrent multi-model execution, by design, not as a gap to fix).
  If you touch this, re-run a compound test request and check
  `x_tool_trace` in the response actually shows more than one agent
  being consulted — see `_backfill_dropped_delegates` in
  `agent_manager.py` for why the raw tool trace, not just the model's own
  prose, is what's trustworthy here.

## Multiple coding agents on this repo

If you're one of several agent sessions (Copilot coding agent, another
Claude Code session, etc.) working on this repo at once: there is no
runtime channel between separate agent sessions — each works in its own
isolated checkout and opens its own PR. Keep sessions from colliding by
scoping each to a clearly separate part of the tree (e.g. one on
`src/app/api/*`, another on `agents/orchestrator/*`) and describing that
scope in the PR/issue you were given, rather than assuming another
session's in-flight work is visible to you.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
