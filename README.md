# Plnt — Personal Local Native Twin

A local-first runtime where **one resident planner** spawns a **swarm of sandboxed micro-agents** on your own hardware.

> Status: pre-alpha. v0 scaffold. The four planes are implemented end-to-end with the rung-0 (process) sandbox and an offline-capable compute router. Higher rungs (gVisor, microVM) are stubs.

## Why Plnt

- **Survives sessions.** A resident planner owns identity and memory across days.
- **Runs while you sleep.** Cron + reactive triggers, no human in the loop.
- **Audit trail you can `cat`.** Memory is plain files: JSONL + markdown + a derived FAISS sidecar. No SQL.
- **Vendor-free.** Local Ollama / MLX inference, optionally federated across your other devices in the [exo](https://github.com/exo-explore/exo) style. No MCP, no Claude skill, no SaaS.
- **Sandbox ladder.** Process → gVisor → Firecracker µVM → WASM. Climb when the threat model demands it.

## Architecture — four planes

```
Surface   :  task panels on your devices, JSON-RPC over mTLS
Control   :  planner LLM + resident specialists + ACC + budget governor + scheduler
Execution :  ephemeral micro-agents in sandboxes, two-tool RLM surface
Compute   :  local Ollama / MLX, optionally exo-federated, OpenAI-compat
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the file-by-file mapping.

## Quick start

```bash
# 1. Install (editable)
cd plnt && pip install -e ".[dev]"

# 2. (Optional) point at a local Ollama; otherwise the offline echo planner is used
export PLNT_COMPUTE_URL=http://127.0.0.1:11434
export PLNT_PLANNER_MODEL=llama3.2:3b

# 3. Run an intent locally — no server needed
plnt submit "find anything on agent memory in my Documents"

# 4. Or start the surface server and submit via HTTP
plnt up &
plnt submit --remote "catch me up on agent memory papers"
plnt tail r-XXXXXXXX --follow
```

## Layout

```
plnt/
  surface/      # FastAPI server — task panels
  control/      # planner, specialists, ACC, budget, skills, orchestrator
  execution/    # AgentSpec, blackboard, sandboxes, search+execute, runner
  compute/      # LLM router + backends
  memory/       # episodic JSONL + skills md + FAISS sidecar (lazy)
skills/         # seed skill bundles
tests/          # pytest
```

## Memory is plain files

```
$PLNT_HOME/
  runs/<run_id>/events.jsonl       # the audit log
  runs/<run_id>/artifacts/         # spilled large payloads + outputs
  skills/<role>.md                 # versioned by git
  episodic/YYYY/MM/DD.jsonl        # long-term memory, append-only
  index/                           # derived FAISS, rebuildable
  identity.toml                    # planner identity
```

`cat`, `grep`, `jq`, `git` are the admin tools.

## References

- *Beyond Scaling: Agents Are Heading to the Edge* — arXiv:2605.18535
- *HearthNet* — arXiv:2604.09618
- exo (distributed inference)
- Agentnetes — Firecracker swarm orchestrator
- Firecracker / gVisor sandbox consensus 2026

## License

Apache-2.0.
