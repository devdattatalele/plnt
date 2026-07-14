# plnt

**Multi-model inference playground on Kubernetes.** Helm charts per runtime
(vLLM, TGI, TRT-LLM, SGLang), a Temporal deploy saga, an `InferenceModel` CRD
served by a Python operator, and an OpenAI-compatible playground API at
[`playground.plnt.work`](https://playground.plnt.work).

The live demo surface is [plnt.work/playground](https://plnt.work/playground)
— a chat panel that talks to the OpenAI-compat API this repo ships. Try it in
a browser before reading further.

---

## What's in this repo

```
plnt/
  playground/      # FastAPI, /v1/models + /v1/chat/completions (this repo's live surface)
  charts/          # Helm charts — playground-api (shipped), vllm-runtime (next)
  cli.py           # `plnt` CLI — playground, deploy, and the origin-story subcommands
docker/            # container image for the playground API
deploy/            # DigitalOcean K8s overlay + cert-manager + runbook
docs/              # architecture, API contract, PRD, local-dev, getting-started
tests/             # pytest — includes a contract test against plnt-site's api.ts
examples/          # sample chart values
```

Everything under `plnt/{surface, control, execution, compute, memory}` and
`plnt-tui/` is the personal-runtime origin story — the codebase this repo
started as. It still builds and runs (`plnt up`, `plnt submit`), but it's
not the primary surface anymore.

## 60-second quickstart

```bash
git clone https://github.com/devdattatalele/plnt && cd plnt
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
plnt playground up
```

In another terminal:

```bash
plnt playground models                     # list registered models
plnt playground chat plnt-mock-7b "hi"     # send a prompt, streams to stdout
plnt playground curl                       # copy-paste curl examples
```

Full walkthrough → [docs/getting-started.md](docs/getting-started.md).

## Where the playground fits

```
plnt.work                          plnt (this repo)                plnt-cloud
─────────                          ────────────────                ──────────
static site (Astro)                platform + playground API       booking product
├─ /                               ├─ plnt/playground/             ├─ Temporal workflows
├─ /architecture                   ├─ plnt/charts/                 ├─ ProviderAdapter
├─ /playground  ◄─── fetch() ───►  playground.plnt.work            └─ (calls plnt's API
├─ /docs             CORS-open     ├─ Helm chart                       for the LLM that
└─ /roadmap                        ├─ Docker image                     powers its chat)
                                   └─ DO K8s runbook
```

- **plnt** (here) — the platform. Playground API, Helm charts, Temporal
  workflows (Phase 2+), CRD + operator (Phase 3+), CLI.
- **[plnt-site](../plnt-site)** — the marketing surface at `plnt.work`.
  Owns the `/playground` UI page, docs portal (Starlight), and static
  landing. Hits this API for chat.
- **[plnt-cloud](../plnt-cloud)** — a bookings product built on plnt.
  Proves the platform end-to-end: the LLM that powers its booking chat is
  served by this API.

## Deploy

```bash
# on any Kubernetes cluster with ingress-nginx + cert-manager installed
helm install plnt-playground plnt/charts/playground-api \
  -f examples/values-playground.yaml
```

Prod-ready DigitalOcean K8s deploy targeting `playground.plnt.work`:
[deploy/RUNBOOK-do-k8s.md](deploy/RUNBOOK-do-k8s.md) — 11 steps,
~40 min, ~$24/mo.

## Docs

- [Getting started](docs/getting-started.md) — pip install → first curl.
- [Local dev](docs/local-dev.md) — playground API + plnt-site together on one laptop.
- [API contract](docs/api-contract.md) — the OpenAI subset this API implements. Enforced by [`tests/test_site_contract.py`](tests/test_site_contract.py).
- [Architecture](docs/architecture.md) — the layered platform diagram, where playground sits, kind demo.
- [PRD — playground](docs/PRD-playground.md) — why the playground exists, users, goals, non-goals, success metrics.
- [Deploy runbook (DO K8s)](deploy/RUNBOOK-do-k8s.md) — step-by-step to `playground.plnt.work`.

## Status

| Component                          | Status              | Where                                        |
|------------------------------------|---------------------|----------------------------------------------|
| Playground API (FastAPI)           | ✅ shipped, tested   | `plnt/playground/`                           |
| Playground Helm chart              | ✅ shipped           | `plnt/charts/playground-api/`                |
| Playground container image         | ✅ shipped           | `docker/playground-api.Dockerfile`           |
| DO K8s deploy overlay + runbook    | ✅ shipped           | `deploy/do-k8s/`                             |
| Contract test vs plnt-site         | ✅ 15/15 passing     | `tests/test_site_contract.py`                |
| `plnt` CLI (playground + deploy)   | ✅ shipped           | `plnt/cli.py`, `plnt/playground/cli.py`      |
| vLLM Helm chart                    | 🔜 next              | HANDOFF.md Phase 1                           |
| Temporal deploy saga               | 🕐 planned           | HANDOFF.md Phase 2                           |
| InferenceModel CRD + kopf operator | 🕐 planned           | HANDOFF.md Phase 3                           |
| Benchmark harness                  | 🕐 planned           | HANDOFF.md Phase 6                           |

## Origin story

plnt started as a **personal local-native agent runtime** — one resident
planner spawning sandboxed micro-agents on your own hardware, four planes
(Surface / Control / Execution / Compute), sandbox rungs from process to
gVisor to microVM. That code still lives under `plnt/{surface, control,
execution, compute, memory}` and is what `plnt up`, `plnt submit`,
`plnt runs`, etc. drive.

The pivot to a K8s inference playground was motivated by the NVIDIA NIM
Factory role (JR2003580, Santa Clara). Same values — vendor-free, plain
files, audit trail you can `cat` — applied at a different scale.
[ARCHITECTURE.md](ARCHITECTURE.md) documents the personal-runtime side;
[docs/architecture.md](docs/architecture.md) documents the platform side.

## License

Apache-2.0. See [LICENSE](LICENSE).
