# plnt

**Multi-model inference playground on Kubernetes.** Helm charts per runtime
(vLLM, TGI, TRT-LLM, SGLang), a Temporal deploy saga, an `InferenceModel` CRD
served by a Python operator, and an OpenAI-compatible playground API at
[`playground.plnt.work`](https://playground.plnt.work).

The live demo surface is [plnt.work/playground](https://plnt.work/playground) —
a chat panel that talks to the OpenAI-compat API this repo ships. Try it in a
browser before reading further.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Contract tests](https://img.shields.io/badge/contract%20tests-15%2F15-brightgreen.svg)](tests/test_site_contract.py)
[![Roadmap](https://img.shields.io/badge/roadmap-v0.1--v1.0-informational.svg)](ROADMAP.md)

---

## What's in this repo

```
plnt/
  playground/      # FastAPI, /v1/models + /v1/chat/completions (this repo's live surface)
  charts/          # Helm charts: playground-api (shipped), vllm-runtime (scaffold), ...
  operators/       # kopf controller + InferenceModel CRD (scaffold, v0.4)
  workflows/       # Temporal deploy saga (scaffold, v0.4)
  cli.py           # `plnt` CLI: playground, deploy, and the origin-story subcommands
docker/            # container image for the playground API
deploy/            # DigitalOcean K8s overlay + cert-manager + runbook; Fly.io alt
docs/              # architecture, PRD, ERD, api-contract, local-dev, observability, threat-model
tests/             # pytest: playground behavior + contract test vs plnt-site
examples/          # sample chart values and an example InferenceModel
```

Everything under `plnt/{surface, control, execution, compute, memory}` and
`plnt-tui/` is the personal-runtime origin story — the codebase this repo
started as. It still builds and runs (`plnt up`, `plnt submit`); it is frozen
as origin story.

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

Full walkthrough: [docs/getting-started.md](docs/getting-started.md).

## Where plnt fits

```
plnt.work                          plnt (this repo)                plnt-cloud
static site (Astro)                platform + playground API       booking product
  /                                  plnt/playground/                Temporal workflows
  /architecture                      plnt/charts/                    ProviderAdapter
  /playground  <-- fetch() -->     playground.plnt.work            (calls plnt's API
  /docs           CORS-open          Helm chart                     for the LLM that
  /roadmap                           Docker image                    powers its chat)
                                     DO K8s runbook
```

- **plnt** (here) — the platform. Playground API, Helm charts, Temporal
  workflows (v0.4), CRD + operator (v0.4), CLI.
- **[plnt-site](../plnt-site)** — the marketing surface at `plnt.work`. Owns
  the `/playground` UI page, the Starlight docs portal, and the static
  landing. Hits this API for chat.
- **[plnt-cloud](../plnt-cloud)** — a bookings product built on plnt. Proves
  the platform end-to-end: the LLM that powers its booking chat is served by
  this API.

## Deploy

```bash
# on any Kubernetes cluster with ingress-nginx + cert-manager installed
helm install plnt-playground plnt/charts/playground-api \
  -f examples/values-playground.yaml
```

Prod-ready DigitalOcean K8s deploy to `playground.plnt.work`:
[deploy/RUNBOOK-do-k8s.md](deploy/RUNBOOK-do-k8s.md) — 11 steps, ~40 min,
~$24/mo. Alternative Fly.io path: [fly.toml](fly.toml).

## Documentation

Everything is under [`docs/`](docs/index.md). Start here:

**Product**
- [PRD — platform](docs/PRD.md) — problem, users, goals, non-goals, market.
- [PRD — playground](docs/PRD-playground.md) — the demo surface at
  `playground.plnt.work`.
- [Roadmap](ROADMAP.md) — phase-by-phase status.

**Design**
- [Architecture](docs/architecture.md) — layered platform diagram.
- [ERD](docs/ERD.md) — entities, sequences, state machine.
- [Engineering principles](docs/eng-principles.md) — the values that show up
  in code review.

**Reference**
- [API contract](docs/api-contract.md) — the OpenAI subset this API
  implements. Enforced by [`tests/test_site_contract.py`](tests/test_site_contract.py).
- [Glossary](docs/glossary.md) — vocabulary used across code and docs.

**Operate**
- [Getting started](docs/getting-started.md) — pip install to first curl.
- [Local dev](docs/local-dev.md) — playground API and plnt-site together on
  one laptop.
- [Deploy runbook (DO K8s)](deploy/RUNBOOK-do-k8s.md).
- [Observability](docs/observability.md) — RED signals, health probes, alerts.
- [Threat model](docs/threat-model.md) — what plnt does and does not defend.

## Status

| Component                          | Status                | Where                                       |
|------------------------------------|-----------------------|---------------------------------------------|
| Playground API (FastAPI)           | `[done]` v0.1         | `plnt/playground/`                          |
| Playground Helm chart              | `[done]` v0.1         | `plnt/charts/playground-api/`               |
| Playground container image         | `[done]` v0.1         | `docker/playground-api.Dockerfile`          |
| DO K8s deploy overlay + runbook    | `[done]` v0.1         | `deploy/do-k8s/`                            |
| Contract test vs plnt-site         | `[done]` 15/15 green  | `tests/test_site_contract.py`               |
| `plnt` CLI (playground + deploy)   | `[done]` v0.1         | `plnt/cli.py`, `plnt/playground/cli.py`     |
| vLLM Helm chart                    | `[wip]` v0.2          | `plnt/charts/vllm-runtime/`                 |
| Temporal deploy saga               | `[wip]` v0.4 scaffold | `plnt/workflows/`                           |
| InferenceModel CRD + kopf operator | `[wip]` v0.4 scaffold | `plnt/operators/`                           |
| Benchmark harness                  | `[planned]` v0.2      | `ROADMAP.md`                                |

## Community

- [Contributing](CONTRIBUTING.md) — dev setup, PR flow, style, runtime
  chart contract.
- [Code of conduct](CODE_OF_CONDUCT.md) — Contributor Covenant v2.1.
- [Security policy](SECURITY.md) — how to report a vulnerability.
- [Changelog](CHANGELOG.md) — versioned release notes.
- [Citation](CITATION.cff) — how to cite plnt.

## Origin story

plnt started as a **personal local-native agent runtime** — one resident
planner spawning sandboxed micro-agents on your own hardware, four planes
(Surface / Control / Execution / Compute), sandbox rungs from process to
gVisor to microVM. That code still lives under `plnt/{surface, control,
execution, compute, memory}` and is what `plnt up`, `plnt submit`,
`plnt runs` drive.

The pivot to a K8s inference playground was motivated by the NVIDIA NIM
Factory role (JR2003580, Santa Clara). Same values — vendor-free, plain
files, audit trail you can `cat` — applied at a different scale.
[ARCHITECTURE.md](ARCHITECTURE.md) documents the personal-runtime side;
[docs/architecture.md](docs/architecture.md) documents the platform side.

## License

Apache-2.0. See [LICENSE](LICENSE).
