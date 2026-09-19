# Measured results (2026-09-19)

Machine: i7-11370H (4 cores), 15.8 GB RAM, CPU-only inference through Ollama 0.34.2. Temperature 0,
`num_ctx` 4096, one model loaded at a time. Reproduce with `python -m benchmarks.run_benchmark`
(see the README). Small differences (one case) are inside the noise — read the pattern, not the decimals.

## Model comparison — general roles (older case set, before skills were injected)

| model | size | coding /8 | RAG /4 | routing /8 | tools /11 | translation /4 | writing /6 | overall | tok/s |
|---|---|---|---|---|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 | 2.5 GB | 6* | 2 | 8 | 8 | **4** | 5 | 80% | 7.9 |
| Qwen2.5-1.5B | 1.1 GB | 7 | **4** | 8 | 8 | 1 | **6** | 83% | **20.3** |
| qwen2.5:3b | 1.9 GB | 7 | 3 | 7 | **11** | 1 | 5 | 83% | 10.6 |
| Qwen3-1.7B (`/no_think`) | 1.1 GB | 6 | **4** | 7 | 9 | 2 | **6** | 83% | 19.7 |
| Qwen2.5-Coder-3B | 2.1 GB | **8** | – | – | – | – | – | 100% (coding only) | 10.8 |

\* both misses were an Arabic comma inside code; `orchestrator/postprocess.py` now fixes that in production,
which makes it 8/8. No model wins everywhere, so each role got its own pick (`config/models.yaml`).

Original `qwen3:4b` scored 14%: on this Ollama build `think: false` is ignored and its reasoning trace lands in
the reply (measured), so hybrid Qwen3 checkpoints need `/no_think`; the `-2507` Instruct build has no such mode.

## Translation (EN → AR)

| model | 16 cases (8 general + 8 contract-grade) | tok/s | RAM |
|---|---|---|---|
| Qwen3-4B-Instruct-2507 | 13 | 7.6 | 2.5 GB |
| llama3.1:8b (previous pick) | 13 | 4.8 | 4.9 GB |
| qwen3:8b | 13 | 4.3 | 5.2 GB |
| gemma3:4b | 13 | 9.4 | 3.3 GB |
| qwen2.5:7b | 8 | 4.1 | 4.7 GB |

The keyword score does not separate them; a real contract-style document does (`python -m benchmarks.tarjuman_live`):

- Qwen3-4B with inline formatting tags: "two percent per month" → "**200%**", "first party" → "the political party".
  Fixed by never sending tags to the model (see Tarjuman in the README).
- gemma3:4b: fluent but **invented text** ("and the client company" in a footer) and dropped a word — rejected.
- After the fix, Qwen3-4B: 5 model calls instead of 21, 77 s cold, 0 s warm (cache), no invented or dropped text.

## Routing (which agent handles a message)

Final accuracy of the two-stage router. DEV was tuned against; HOLDOUT and FRESH were not.

| configuration | DEV /48 | HOLDOUT /16 | FRESH /20 |
|---|---|---|---|
| original (nomic-embed-text, 0.5B stage 2, no few-shot) | 58% | 56% | – |
| better prompt + JSON-schema-constrained output (0.5B) | 73% | 69% | – |
| Qwen3-Embedding-0.6B, thresholds 0.45 / 0.03, 1.5B stage 2 | 96% | 88% | 80% |
| **same, Qwen3-4B stage 2 (current)** | **96%** | **94%** | **95%** |

A further set of 12 tool-style requests (calculator / time / file list / translation) went from 50% to 100%
after adding their seed examples — those numbers are optimistic because the seeds were written after seeing
the kind of message. Reproduce: `python -m benchmarks.routing_eval`.

Stage-2 model on messages never tuned on (HOLDOUT + FRESH, 36): 0.5B ≈ 40%, 1.5B 83%, Qwen3-4B 92%.
Best single embedding match: nomic 62% / 75% → Qwen3-Embedding 88% / 81% (DEV / HOLDOUT).

## End-to-end (`python -m benchmarks.smoke_e2e`, real Ollama)

8/8 with the answer content checked: routing → skill injected → tool called → correct fact in the answer
(calculator 352, "Python 3.14.7" from a live search, the manager's name from the uploaded file, a translated
sentence via Tarjuman, the uploaded-files list with its Cyrillic slip repaired to "بايت").
