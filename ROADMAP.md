# Roadmap

Public status of every plnt platform component. Updated per merged PR.

**Legend:** `[done]` shipped and green in CI · `[wip]` under active work ·
`[next]` starts within two weeks · `[planned]` on the map, no start date ·
`[idea]` still under debate

---

## v0.1 — Playground foundation (shipped 2026-07)

The proof surface. Anyone can boot the API, hit `plnt playground chat`,
or curl `playground.plnt.work` and get a reply.

| Item                                                                          | Status    |
|-------------------------------------------------------------------------------|-----------|
| FastAPI playground (`GET /v1/models`, `POST /v1/chat/completions`, SSE)       | `[done]`  |
| MockBackend + HTTPBackend + `RuntimeAdapter` protocol                         | `[done]`  |
| ConfigMap-driven model registry                                               | `[done]`  |
| Docker image (non-root, read-only rootfs, healthcheck)                        | `[done]`  |
| Helm chart `plnt/charts/playground-api` (Deployment/Service/Ingress/HPA)      | `[done]`  |
| DigitalOcean K8s deploy overlay + cert-manager Let's Encrypt                  | `[done]`  |
| Deploy runbook (11 steps, ~40 min, ~$24/mo)                                   | `[done]`  |
| Fly.io deploy path (alt to DOKS)                                              | `[done]`  |
| CLI: `plnt playground {up,models,chat,curl}` + `plnt deploy`                  | `[done]`  |
| Contract test vs plnt-site's `api.ts`                                         | `[done]`  |
| CORS env-driven allowlist (defaults cover Astro dev + prod origins)           | `[done]`  |
| Docs: getting-started, api-contract, local-dev, architecture, PRD, ERD        | `[done]`  |

## v0.2 — vLLM real end-to-end (target: 2 weeks)

Get one real GPU-backed model serving through the playground.

| Item                                                                          | Status    |
|-------------------------------------------------------------------------------|-----------|
| `plnt/charts/vllm-runtime` chart green on a CPU-stub image (kind demo)        | `[wip]`   |
| Same chart green on a single-GPU DOKS node (or lambda-labs / vast)            | `[next]`  |
| Playground registers the vLLM model via HTTPBackend -> real inference         | `[next]`  |
| `plnt bench` MVP — TTFT p50/p95, tokens/sec/GPU probe                         | `[next]`  |
| Runbook update: "adding a real vLLM model" section                            | `[next]`  |

## v0.3 — Multi-runtime (target: 4 weeks)

Prove the RuntimeAdapter abstraction across three backends.

| Item                                                                          | Status    |
|-------------------------------------------------------------------------------|-----------|
| `plnt/charts/tgi-runtime` chart, same values contract                         | `[planned]` |
| `plnt/charts/sglang-runtime` chart, same values contract                      | `[planned]` |
| Side-by-side model registration (same weights, different runtimes)            | `[planned]` |
| `plnt bench compare` — TTFT/TPOT table per runtime                            | `[planned]` |
| Playground UI: runtime badge on each model card                               | `[planned]` |

## v0.4 — Observability + operator (target: 6-8 weeks)

Make the platform self-describing.

| Item                                                                          | Status    |
|-------------------------------------------------------------------------------|-----------|
| Prometheus `/metrics` endpoint on playground pod (RED signals)                | `[planned]` |
| Grafana dashboard JSON committed                                              | `[planned]` |
| kopf controller for `InferenceModel` CRD (scaffold shipped in v0.1)           | `[wip]`   |
| Controller -> Temporal `DeployModelWorkflow` (scaffold shipped in v0.1)       | `[wip]`   |
| End-to-end `kubectl apply -f llama.yaml` triggers full saga                   | `[planned]` |
| MLflow client wrapper for hash-verified weight pulls (optional)               | `[idea]`  |

## v0.5 — TRT-LLM + full runtime coverage (target: quarter end)

| Item                                                                          | Status    |
|-------------------------------------------------------------------------------|-----------|
| `plnt/charts/trt-llm-runtime` chart                                           | `[planned]` |
| `TritonBackend` RuntimeAdapter (Triton-native, not OpenAI-HTTP)               | `[planned]` |
| Precompiled engine caching path                                               | `[planned]` |
| Runbook: "picking a runtime" decision tree                                    | `[planned]` |

## v1.0 — Public release

| Item                                                                          | Status    |
|-------------------------------------------------------------------------------|-----------|
| Cross-cluster failover for playground API (active-active)                     | `[idea]`  |
| Hosted DNS at plnt.work with region-aware routing                             | `[idea]`  |
| First-party CI images (GHCR) + provenance attestations                        | `[planned]` |
| Blog post + launch on HN                                                      | `[planned]` |
| Contributing guide finalised + first outside contributor merged               | `[planned]` |

---

## Deliberately not on the roadmap

- Training / fine-tuning UI.
- Hosted model marketplace.
- Multi-tenant billing / metering.
- Windows support.
- SageMaker parity.

See [`docs/PRD.md` section 5](docs/PRD.md#5-non-goals) for the reasoning.

---

## How this is maintained

Every merged PR that ships a listed item flips its status in the same
commit. `main` should always be an accurate picture. If you see drift,
open a PR — it counts as a doc fix.
