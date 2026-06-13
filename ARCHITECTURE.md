# Plnt Architecture (v0)

```
┌───────────────────────────────────────────────────────────────────┐
│ SURFACE PLANE                user devices · panels · mTLS         │
│   plnt/surface/server.py · plnt/cli.py                            │
└──────────────────────────┬────────────────────────────────────────┘
                           │  JSON-RPC / SSE
┌──────────────────────────▼────────────────────────────────────────┐
│ CONTROL PLANE              one resident process — the brain       │
│   Orchestrator       plnt/control/orchestrator.py                 │
│   Skills (md, git)   plnt/control/skills.py                       │
│   Budget governor    plnt/control/budget.py                       │
│   ACC monitor        plnt/control/acc.py                          │
│   (Planner LLM)      plnt/compute/router.py  (called from runner) │
└──────────────────────────┬────────────────────────────────────────┘
                           │  AgentSpec  (the only object that
                           │              crosses Control→Execution)
┌──────────────────────────▼────────────────────────────────────────┐
│ EXECUTION PLANE            ephemeral micro-agents in sandboxes    │
│   AgentSpec          plnt/execution/spec.py                       │
│   Blackboard         plnt/execution/blackboard.py                 │
│   Sandbox (rung 0)   plnt/execution/sandbox/process.py            │
│   Runner             plnt/execution/runner.py                     │
│   Tools (RLM)        plnt/execution/tools/{search,execute}.py     │
└──────────────────────────┬────────────────────────────────────────┘
                           │  OpenAI-compatible HTTP
┌──────────────────────────▼────────────────────────────────────────┐
│ COMPUTE PLANE              local inference, optionally federated  │
│   LLMRouter          plnt/compute/router.py                       │
│   (Ollama default · exo for deep model · offline echo fallback)   │
└───────────────────────────────────────────────────────────────────┘
```

## Hard rules

1. **AgentSpec is the only Control→Execution object.**
   Anything you want to enforce on a spawn lives in `AgentSpec` (depth, budget, isolation, tools).

2. **Two tools only.** `search` + `execute`. The RLM pattern is the reason we can skip SQL — context lives in the filesystem and is reached through these.

3. **Blackboard is the audit story.** Every observable event is one JSONL line. If `cat` and `grep` can't see it, it shouldn't exist as state.

4. **Memory is files.** No databases. JSONL + markdown + (later) a derived FAISS sidecar.

5. **Framework owns memory + skills + sandbox.** The model is a swappable backend. (Beyond Scaling, §3.)

6. **Sandbox is a ladder.** Don't pay microVM cost until the threat model demands it.

## What v0 ships

- Surface: REST + SSE, mTLS *stub* (loopback only).
- Control: keyword-routed planner (LLM planner is plug-in shaped, drops in at `Orchestrator.planner`).
- Execution: process-rung sandbox with watchdog + rlimits, full event stream.
- Compute: OpenAI-compat router with offline echo fallback so the whole loop is testable without an LLM.
- Memory: per-run JSONL + artifacts; episodic dir scaffolded.
- Skills: 5 seed bundles + hot-reload registry.

## What v0 explicitly does NOT ship

- gVisor / Firecracker rungs (stubs only).
- mTLS enforcement (loopback only).
- exo backend implementation (the router treats the deep URL as just another OpenAI-compat endpoint — point it at exo, it works).
- iOS client, web dashboard, scheduler.
- A semantic index. The directory exists; the indexer doesn't yet.
- Multi-tenancy. This is *personal* — one user, one machine (or one home network).

## Hard rules for contributions

- New tool? It goes through the AgentSpec.tools allow-list and gets a permission gate. Two tools is a guideline, not a religion — but every additional tool needs to justify itself in a CONTRIBUTING.md PR.
- New sandbox rung? Implement the `Sandbox` protocol in `execution/sandbox/base.py` and register it in `execution/sandbox/__init__.py`.
- New backend? Implement the OpenAI-compat call inside `LLMRouter` or a sibling class. Do not leak backend specifics into the runner.
