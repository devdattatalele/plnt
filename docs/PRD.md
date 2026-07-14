# PRD — plnt platform

**Status:** Draft v1 · 2026-07-14 · Owner: Devdatta Talele
**One-liner:** plnt is an opinionated, Helm-native platform for deploying and
serving multi-runtime ML inference on Kubernetes — with a live OpenAI-compatible
playground at [`plnt.work/playground`](https://plnt.work/playground) as the proof
surface.

> This is the **platform** PRD. The narrower [PRD for the playground surface](./PRD-playground.md)
> covers the demo UX in more detail.

---

## 1. Problem

Deploying inference for a new model on Kubernetes today looks like this:

1. Pick a runtime (vLLM, TGI, TRT-LLM, SGLang). Read three READMEs.
2. Write a Deployment + Service + HPA + ConfigMap from scratch, or copy-paste
 from someone else's chart and fix drift.
3. Wire an operator or a script to canary-roll -> smoke-test -> promote.
4. Add ingress + TLS + CORS by hand.
5. Repeat for the next model, and the next.

Every model deploy becomes bespoke YAML. Every runtime has a different
values contract. Rollbacks are manual and often lossy. The team ends up
with a folder of `production/models/*.yaml` that nobody dares refactor.

## 2. Solution

plnt makes deploying a model a one-line kubectl:

```bash
kubectl apply -f examples/llama-3.1-70b-instruct.yaml
```

The `InferenceModel` CRD says *what*; the operator + Temporal deploy saga
handle *how*: validate -> pull -> helm-install-canary -> smoke-test ->
promote-or-rollback. Every runtime (vLLM/TGI/TRT-LLM/SGLang) has a Helm
chart with the same values contract. The playground API sits above the
runtimes and speaks OpenAI to any client that wants to.

The whole thing is designed to look like a **mini NIM Factory** — the
same job-to-be-done as NVIDIA's Inference Microservices program, but
scoped to a single-team platform any infra engineer can stand up.

## 3. Users & jobs-to-be-done

| Persona | JTBD | Interaction |
|---------------------------------------------|------------------------------------------------------------------|----------------------------------------------------------|
| **Infra / platform engineer** (primary) | Give my product teams a self-serve way to deploy a new LLM. | `kubectl apply -f mymodel.yaml`; watches `InferenceModel` events. |
| **ML engineer** (secondary) | Ship the model I fine-tuned without becoming a K8s expert. | Writes a 20-line `InferenceModel` YAML; benchmarks with `plnt bench`. |
| **Product engineer** (tertiary) | Call an LLM from my product — OpenAI SDK, don't lock me in. | `POST https://playground.plnt.work/v1/chat/completions`. |
| **Hiring manager / evaluator** (motivating) | Prove the pitch on the site is real. | Visits `plnt.work/playground`, sends 1–3 prompts. |
| **Adjacent OSS user** | Bring my own cluster, run this to compare vLLM vs SGLang. | Clones repo, `helm install` per runtime, `plnt bench`. |

## 4. Goals

- **G1 — one-line model deploy.** `kubectl apply` a well-formed
 `InferenceModel` and the platform brings up the runtime pod, wires it
 behind the playground API, and canary-promotes.
- **G2 — runtime portability.** Same values contract across vLLM / TGI /
 TRT-LLM / SGLang. Swap runtime with a `spec.runtime:` edit + `kubectl
 apply`, no chart forks.
- **G3 — OpenAI wire compat.** The playground API stays a strict subset
 of the OpenAI /v1/... surface. Any SDK / cURL example that works
 against OpenAI works against plnt. See [`docs/api-contract.md`](./api-contract.md).
- **G4 — safe rollout by default.** Every deploy runs the saga: validate
 -> pull-hash-check -> 5% canary -> smoke test -> promote-or-rollback.
 Compensation on any step failure. No production traffic on a model
 that has not passed a smoke test.
- **G5 — audit trail you can `cat`.** `kubectl get inferencemodel`,
 `helm history`, Temporal workflow events. No proprietary DBs. No
 vendor telemetry.
- **G6 — cost floor at $0.** Runs on kind + CPU-stub locally for zero
 cost. Scales up cleanly on real GPU clusters when you're ready.
- **G7 — < 60s from clone to first curl.** `pip install -e .` ->
 `plnt playground up` -> `plnt playground chat` returns tokens.

## 5. Non-goals

- [not] **Training / fine-tuning.** Serve-only. See vertex.ai, sagemaker,
 runpod for the training side.
- [not] **Model marketplace.** No hosted catalog, no "browse popular
 models" — the operator serves what YOU deploy.
- [not] **Multi-tenant billing / metering.** Out of v1 scope. If you need
 it, a Kong/Envoy plugin layer over the playground API is the seam.
- [not] **Custom UI framework.** The site (plnt-site) is Astro + Starlight
 + a single Preact island. Not a design system, not a component
 library.
- [not] **RAG / agents / function calling** in the playground surface.
 Chat completions only. Higher-order use cases live in downstream
 products like plnt-cloud, not in the platform.
- [not] **Windows support.** Linux + macOS only. GPU-first workflows are
 Linux-first.
- [not] **AWS SageMaker parity.** We're deliberately narrower and more
 opinionated.

## 6. Success metrics

| Metric | Target v1 | How measured |
|-----------------------------------------------------------------|---------------|----------------------------------------------------|
| Time from `pip install` -> first successful `chat` reply | < 60s | Manual, clean venv, mock backend. |
| Time from `kubectl apply -f` -> InferenceModel `Ready: True` | < 3 min | Kind cluster, CPU-stub runtime (real GPU: < 10m). |
| Runtimes supported end-to-end | 4 | vLLM, TGI, TRT-LLM, SGLang. |
| Playground API uptime | 99.5% | External check on `/healthz`. |
| Contract-test pass rate | 100% | `tests/test_site_contract.py` in CI. |
| Docs completeness | 12+ pages | Portal + PRD + ERD + runbook + guides. |
| Stargazers, 90d | 250 | GitHub. |
| Cost of demo footprint | ≤ $30/mo | DO invoice. |

## 7. Market context

- **NVIDIA NIM** — the direct spiritual reference. First-party inference
 microservice program. Closed-source per-runtime containers, K8s
 operator, Helm charts, benchmarks. plnt is the "small-team,
 open-source, single-cluster" analog.
- **KServe** — the OSS status quo for K8s model serving. More
 general, less opinionated; supports many backends but you write the
 YAML per model. plnt trades KServe's generality for a sharper deploy
 saga + a single well-tested runtime contract.
- **BentoML / Modal / RunPod** — API-first, hosted-first. plnt is the
 bring-your-own-cluster answer.
- **Ray Serve** — the "Python-native" option. Great if your infra is
 already Ray; plnt is aimed at teams whose infra is already K8s.

## 8. Roadmap

See [`ROADMAP.md`](../ROADMAP.md) for the phase-by-phase status.

- **v0.1 (shipped)** — playground API + Helm chart + DO deploy runbook +
 vLLM chart scaffold + operator scaffold + Temporal workflow scaffold.
- **v0.2 (next 2 weeks)** — vLLM runtime green end-to-end on a real GPU
 cluster. Operator picks up InferenceModel and runs the workflow.
- **v0.3 (next month)** — TGI + SGLang charts. Multi-runtime canary
 compare demo.
- **v0.4** — Prometheus + Grafana dashboards. Benchmark harness output
 wired into the playground UI as a "cluster health" strip.
- **v0.5** — TRT-LLM runtime chart. Full runtime coverage.
- **v1.0** — Cross-cluster failover for the playground API. Public
 release announcement.

## 9. Open questions

- **Auth.** v1 has none — every playground call is anonymous. If abuse
 becomes a cost problem, the cleanest layer is Cloudflare Access + a
 per-model rate limit at the ingress. Deliberately not doing per-user
 auth at the API level.
- **Model registry.** Currently ConfigMap-driven via `helm upgrade`.
 Once Phase 2 workflow is live, the operator can write the registry —
 removing the ConfigMap edit. Coupling to MLflow is proposed for v0.4;
 might defer if it feels like over-engineering.
- **Real telemetry.** No metrics endpoint on the playground pod yet.
 Prometheus scrape is v0.4. Until then success metrics are best-effort
 external probes.
- **RuntimeAdapter beyond HTTP.** vLLM/TGI/SGLang/llama.cpp all speak
 OpenAI-HTTP. TRT-LLM is Triton-native and needs a Triton adapter, not
 the current HTTP one. That work lands with v0.5.

## 10. Risks

| Risk | Likelihood | Mitigation |
|----------------------------------------------------------|------------|---------------------------------------------------------------------|
| NVIDIA ships a public NIM-Factory tool that eats us | High | plnt is OSS + BYO cluster; that market segment survives. |
| Runtime contract drifts across vLLM upgrades | Med | Pinned chart appVersion + contract tests + regular re-run. |
| Operator + Temporal is over-engineered for one cluster | Med | Both are optional; playground API + Helm chart already useful. |
| Playground abuse (someone rents cycles for free chat) | Low | Cloudflare rule on cost spike; downgrade to mock backend. |
| Cost creep past $30/mo in demo footprint | Low | Autoscaling caps + smallest node size, documented in runbook. |

## 11. Success = the story you can tell

The 90-second pitch, if this works:

> *"plnt.work is a K8s inference platform. Deploy an LLM with one YAML file
> — the operator runs a Temporal saga that pulls weights, canary-installs
> the Helm chart, runs a smoke test, and promotes or rolls back. Same
> values contract across vLLM, TGI, TRT-LLM, SGLang. It's a mini NIM
> Factory. Try it: `plnt.work/playground`."*

If that reads as true to an infra engineer inside 90 seconds, plnt has
served its purpose.
