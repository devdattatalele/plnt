# HANDOFF — plnt platform pivot

Paste this into a fresh Claude Code session running in `/Users/dev16/Documents/den-agent/plnt`.

---

## Context (read this first)

**What plnt used to be:** a local-first personal agent runtime — one resident planner spawns sandboxed micro-agents on your own hardware. See `README.md`, `ARCHITECTURE.md`. The four planes (Surface / Control / Execution / Compute) are the existing scaffold.

**What plnt is becoming:** a **playground platform for deploying multiple ML inference models on Kubernetes**, using Helm charts + a Temporal workflow layer for deploy sagas, canary rollouts, and batch inference. Domain: `plnt.work`. Sister site at `/Users/dev16/Documents/den-agent/plnt-site`. Sister demo product at `/Users/dev16/Documents/den-agent/plnt-cloud` (a booking app built on plnt's primitives — the "product on the platform" proof).

**Why the pivot:** target job is **NVIDIA — Senior Software Engineer, NIM Factory Container and Cloud Infrastructure** (Santa Clara, JR2003580). Job core: container strategy for NVIDIA Inference Microservices (NIMs), Python tooling for build orchestration + Helm/Operator automation, K8s deployment patterns for GPU workloads, base image strategy, multi-tenant multi-cluster delivery. plnt is being reshaped to look like *"a mini NIM Factory of my own"* — Helm charts for inference runtimes (vLLM / TGI / TRT-LLM / SGLang), Temporal workflows for deploy sagas, custom `InferenceModel` CRD + operator, Python CLI.

**Prior session artifacts you can reuse:**
- `../plnt-cloud/workflows/saga_booking.py` — reference Temporal saga pattern (create → notify → compensate). Same shape as the deploy saga we need here.
- `../plnt-cloud/workflows/worker.py` — reference `SandboxedWorkflowRunner` + passthrough module list. The plnt deploy workflow will use the same setup.
- `../plnt-cloud/workflows/session.py` — reference `RetryPolicy(initial_interval, maximum_attempts, backoff_coefficient)` per-activity pattern.
- `../plnt-cloud/providers/{base,resy,registry}.py` — reference `ProviderAdapter` abstraction (search/availability/book). We'll build a similar `RuntimeAdapter` for inference backends (vLLM / TGI / TRT-LLM / SGLang).

## Architecture target

```
plnt CLI + Python API
  ├─ plnt/charts/            (Helm charts, one per runtime)
  │   ├─ vllm-runtime/
  │   ├─ tgi-runtime/
  │   ├─ trt-llm-runtime/
  │   ├─ sglang-runtime/
  │   └─ router-envoy/       (L7 routing across models)
  ├─ plnt/operators/         (Python kopf operator watching InferenceModel CRD)
  ├─ plnt/workflows/         (Temporal — deploy saga, canary rollout, batch inference)
  ├─ plnt/runtime/           (RuntimeAdapter abstraction: vLLM / TGI / TRT-LLM / SGLang)
  ├─ plnt/cli/               (typer/click CLI: deploy, list, scale, bench, rollback)
  ├─ plnt/registry/          (model artifact registry — MLflow client wrapper + local cache)
  └─ plnt/bench/             (TTFT / TPOT / tokens-per-sec/GPU harness)

Kubernetes surface (kind cluster for local demo):
  - CRD: apiVersion: plnt.work/v1, kind: InferenceModel
  - Operator (Python kopf) watches InferenceModel → kicks off Temporal deploy workflow
  - Temporal workflow: validate_manifest → pull_weights → helm_install_canary → smoke_test → promote_or_rollback
  - GPU scheduling via nvidia.com/gpu resource requests (works with the nvidia-device-plugin DaemonSet on real clusters, mocked on kind)
```

## Phase plan (2–3 weeks to demo-ready)

**Phase 0 — skeleton + narrative (day 1–2)**
- Add top-level `plnt/charts/`, `plnt/operators/`, `plnt/workflows/`, `plnt/runtime/`, `plnt/cli/`, `plnt/registry/`, `plnt/bench/` dirs with `__init__.py` and stub modules.
- Update `README.md` to reflect the new positioning. Keep the personal-runtime lineage as origin story, but the primary framing is now "multi-model inference playground on K8s."
- New `docs/architecture.md` with the ASCII diagram above.

**Phase 1 — first Helm chart (day 3–5)**
- `plnt/charts/vllm-runtime/` with `Chart.yaml`, `values.yaml`, and templates for Deployment + Service + HPA + ConfigMap.
- values fields: `model.name`, `model.storageUri`, `resources.gpu`, `replicas.min/max`, `runtime.image`, `runtime.args`.
- Test via `helm install llama-70b plnt/charts/vllm-runtime -f examples/values-llama.yaml` against `kind` cluster.
- On kind (no GPU) use a CPU-only stub image or a mock inference server that speaks the OpenAI API shape — the point is proving the chart works end-to-end, not real inference.

**Phase 2 — Temporal deploy saga (day 6–9)**
- `plnt/workflows/deploy_model.py` — `DeployModelWorkflow` with saga:
  1. `validate_manifest` (values.yaml valid, storage URI reachable, image pullable)
  2. `pull_and_verify_weights` (hash-check against registry entry)
  3. `helm_install_canary` (deploy with 5% traffic split — use Envoy VirtualService weights)
  4. `run_smoke_test` (N test prompts, measure TTFT/TPOT, compare against baseline)
  5. `promote_or_rollback` (KPI check → 100% or `helm uninstall`)
- Compensation on any step failure via `helm rollback`.
- Reuse the `RetryPolicy` + `non_retryable_error_types` pattern from plnt-cloud.
- Add `workflows/worker.py` mirroring plnt-cloud's — sandbox + passthrough for anything the activities import.

**Phase 3 — InferenceModel CRD + operator (day 10–13)**
- CRD spec in `plnt/operators/crds/inferencemodel.yaml`.
- `plnt/operators/inferencemodel_controller.py` using `kopf` — watches InferenceModel resources, on create → kicks off `DeployModelWorkflow` via Temporal client; on update → triggers new canary; on delete → `helm uninstall`.
- `examples/llama-70b.yaml` — a real InferenceModel resource `kubectl apply -f` runs the full flow.

**Phase 4 — Python CLI (day 14–16)**
- `plnt deploy --model llama-3-70b --runtime vllm --gpu 1` — wraps `kubectl apply` for the CRD.
- `plnt list-models` — reads InferenceModel resources from the cluster.
- `plnt scale <model> --replicas N` — patches the HPA min/max.
- `plnt rollback <model>` — triggers Temporal compensation.
- `plnt bench <model> --qps 10 --duration 60s` — runs the benchmark harness.
- Typed with pydantic, tested with pytest, linted with ruff.

**Phase 5 — connect plnt-cloud as consumer (day 17–18)**
- Point `../plnt-cloud/workflows/session.py`'s LLM calls at a plnt-served model endpoint.
- README section: *"plnt-cloud is a bookings product built on plnt — the platform serves the model that powers the chat."*

**Phase 6 — benchmarking + one dashboard (day 19–21)**
- `plnt/bench/harness.py` — measures TTFT p50/p95/p99, TPOT p50/p95/p99, tokens/sec/GPU, KV cache utilization (from vLLM /metrics endpoint).
- Prometheus scrape config in the Helm chart.
- One Grafana dashboard JSON committed.

## Honest gaps to name in the pitch

- **No real GPU hardware yet.** Kind cluster + CPU-stub image for the demo. Frame: "platform layer is done; GPU-workload hardening is where I want to grow into the role."
- **BuildKit / multi-arch CUDA image work is new** — plan to add one working CUDA-optimized Dockerfile before the interview.
- **Multi-cluster is theoretical** — single-cluster demo, design discussed.

## User collaboration preferences (durable)

- **Concise > exhaustive.** End-of-turn: one or two sentences max.
- **No vibe code.** No fake KPIs, no placeholder metrics that aren't measured, no unused CLI flags. Every command in the CLI must work.
- **Don't ask before doing read-only investigation.** Grep the codebase, run `--help`, check `kubectl explain` before asking.
- **Honest about gaps.** If a Phase step is scaffolded but not tested, say so explicitly.
- **Match the user's writing style in chat replies (short, lowercase, WhatsApp-shape).**

## Canary protocol (optional — decide)

The sibling `plnt-cloud/CLAUDE.md` enforces starting every response with the word `Thomas` on its own line. Decide whether to inherit this for plnt too — if yes, add a `CLAUDE.md` here with the same rule.

## First task in the new session

Start Phase 0: create the top-level directory skeleton, update README.md with the new positioning, write `docs/architecture.md` with the diagram above. Do NOT touch anything under the existing `plnt/{surface,control,execution,compute,memory}` — that's the personal-runtime origin story and stays as-is.
