# Plnt — v0 Architecture Review

Re-read of the four-plane diagram against the actual code in this commit, plus what to do next.

**Test status:** 31/31 green (`pytest`), end-to-end CLI smoke test produces a clean JSONL audit log with the full `intent -> spawn -> started -> model_call -> tool_call -> tool_result -> result -> finished` sequence.

## Confirmed: aligned with the plan

| Plan property | Where it lives | Status |
|---|---|---|
| `AgentSpec` is the only Control->Execution object | `plnt/execution/spec.py` | [x] enforced |
| Two tools only (RLM) | `ALLOWED_TOOLS` in `spec.py` | [x] schema-level validator rejects others |
| Memory is files (no SQL) | `paths()` in `config.py` + `Blackboard` | [x] pure JSONL + artifacts |
| Sandbox is a ladder | `execution/sandbox/__init__.py` registry | [x] rung 0 done, rungs 1–3 registered as future |
| Framework owns skills (not the model) | `control/skills.py` | [x] markdown loaded with mtime hot-reload |
| Surface = task panels, not chat | `surface/server.py` | [x] no `/chat` endpoint; only `/intents`, `/runs`, `/skills` |
| Hard budget caps as last line of defense | `config.py` + `Budget` validator | [x] ceilings enforced at parse time |
| ACC monitors for loop/fanout/pingpong | `control/acc.py` | [x] all three detectors with tests |
| Offline-capable so testable without LLM | `compute/router.py::_echo_step` | [x] deterministic fallback drives tests |

## Misaligned / needs work

### 1. The "resident planner" is not actually resident yet

The plan says a *long-lived planner LLM* owns identity and stays running. v0's `Orchestrator.start_run()` materialises a *fresh* keyword-route planner per intent. There is no process that "lives". For v0.1: hoist the planner into a long-lived `OrchestratorService` with a state dict (identity, hot specialists) and run it under the surface server's lifespan.

### 2. Resident specialists are skill files, not running processes

Same shape problem. Right now `research-librarian.md` etc. are picked up *as the role for an ephemeral spawn*. The plan calls for a small number of *named, long-lived* specialists holding shared state (HearthNet finding). v0.1 should add a resident-specialist registry that pre-warms one process per resident role at server boot and routes intents to them.

### 3. Three `finished` events fire at end of a run

`runner.finally:` + `ProcessSandbox.run()` + `Orchestrator.start_run()` each emit one. Audit trail is correct but noisy. Cleanest fix: only the runner emits `finished`; the sandbox emits a distinct `sandbox_exit` event with `exit_code` and `wall_seconds`; the orchestrator emits `run_closed`. That makes the JSONL semantically unambiguous and `grep -c finished` becomes a real metric.

### 4. The ACC and budget governor are observe-only post-hoc, not streaming

The current `Orchestrator.start_run()` runs the sandbox to completion, *then* replays events through the ACC. That means the ACC's `kill_fn` never fires in time to actually stop a runaway. To deliver on the plan's red dotted "governance" arrow, the orchestrator needs a background thread that tails the blackboard live and feeds the ACC. v0.1 priority.

### 5. The "compute plane" is one router, not a federated surface

The plan shows Ollama + MLX + exo cluster behind one OpenAI-compat router. v0 has the router but no MLX path and no exo discovery. Acceptable for v0 — exo already provides an OpenAI-compatible endpoint, so the router *works* with exo today by setting `PLNT_DEEP_URL` to the exo cluster URL. The only missing thing is auto-discovery. Defer to v0.2 (needs mDNS).

### 6. mTLS is a stub

`surface/server.py` binds to 127.0.0.1 by default and has no client-cert enforcement. The README and surface docstring are explicit about this. To ship for "devices reach the server" you need: (a) generate root CA + server cert + per-device client certs, (b) uvicorn with `ssl_keyfile`/`ssl_certfile`/`ssl_cert_reqs=CERT_REQUIRED`, (c) a `plnt enroll` CLI to issue device certs. ~200 LOC. v0.1.

### 7. Two-tool surface needs a third quietly: `spawn`

The plan says "two tools only." But the moment a resident specialist needs to spawn an ephemeral helper, it needs a way to emit an `AgentSpec` back to the orchestrator. v0 dodges this because there's no resident specialist process yet. The clean answer when (1)+(2) land: the specialist emits an AgentSpec as part of its `result.output`, the orchestrator picks it up and spawns. That keeps the tool count at two and makes spawn a *declarative* return value, not an imperative call. Document this contract before resident specialists land.

### 8. The episodic memory and FAISS sidecar are scaffolded but not used

`paths().episodic` and `paths().index` exist; nothing writes to them yet. The orchestrator should append one line per run to `episodic/YYYY/MM/DD.jsonl` with `{run_id, intent, outcome_path, started_at}`. ~10 LOC. Then a `plnt memory grep <pattern>` command becomes trivially useful. Do this before declaring v0 "shippable."

## Improvements I would not make

- **Don't add a database.** Several places would feel cleaner with sqlite (runs index, specialist state). The plan explicitly forbids it and the audit story depends on it. Hold the line.
- **Don't add HTTP between planner and specialists.** Process supervision + JSONL is enough and stays debuggable. Reach for a bus only if/when resident specialists need to push events to *each other* (which they currently don't — they share state via the filesystem).
- **Don't add MCP.** The plan calls this out by name and it would dilute the "framework owns skills" rule.

## Concrete v0.1 punch list (ordered)

1. **Resident planner service** — single long-lived process started by `plnt up`. Holds identity + warm planner state. (1.5)
2. **Streaming ACC + budget tail** — orchestrator runs a tailer thread that feeds the live event stream into the ACC so kills happen in real time. (4)
3. **Resident specialist runtime** — pool of pre-warmed processes per named role. (2 + 7)
4. **Episodic memory append** — one JSONL line per completed run. (8)
5. **mTLS + `plnt enroll`** — make "your phone reaches the server" real. (6)
6. **Clean up duplicate `finished` events.** (3)

Estimated 2–3 weeks for an experienced solo dev to land all six. After that, v0.1 is a shippable personal beta.

## What's notably good about the current state

- The runner's two-tool RLM contract is **as small as it can be** — anything new (browsing, mail, calendar) reaches in through `execute()` and the filesystem, never as a new tool.
- The blackboard write path forwards every runner event into the shared `events.jsonl`. That single decision is what makes `cat events.jsonl | jq` a real debugger.
- The router falls back to deterministic offline behaviour, which means **the entire control loop is testable without a model**. That property is worth defending — never let "we need an LLM up to test this" creep in.
- The sandbox protocol is small enough (`run(spec)`, `kill(id, reason)`) that climbing the ladder later is mechanical, not a redesign.
