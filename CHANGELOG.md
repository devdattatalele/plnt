# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Dates are ISO-8601, UTC.

## [Unreleased]

Nothing yet — next release in progress.

## [0.1.0] — 2026-07-14

Initial platform release. Ships the playground surface end-to-end and
scaffolds the runtime, workflow, and operator layers so v0.2 can iterate
in-place.

### Added

- **Playground API** (`plnt/playground/`) — FastAPI, OpenAI-compat
 `GET /v1/models` + `POST /v1/chat/completions` (streaming + non-streaming),
 `/healthz`, `/readyz`.
- **Backends** — `MockBackend` (deterministic echo, SSE streaming) and
 `HTTPBackend` (proxies to any OpenAI-compat upstream: vLLM, TGI,
 SGLang, llama.cpp server).
- **Registry** — ConfigMap-driven model list; env vars
 `PLNT_PLAYGROUND_MODELS` (inline JSON) and `PLNT_PLAYGROUND_CONFIG`
 (path).
- **CORS** — env-driven allowlist via `PLNT_PLAYGROUND_CORS_ORIGINS`;
 defaults cover plnt.work + playground.plnt.work + local Astro/React dev ports.
- **Container image** — `docker/playground-api.Dockerfile`, non-root,
 read-only rootfs, healthcheck. ~150 MB.
- **Helm chart** — `plnt/charts/playground-api` with Deployment, Service,
 ConfigMap, Ingress (SSE-safe nginx annotations), HPA, imagePullSecrets.
- **DigitalOcean K8s deploy overlay** — `deploy/do-k8s/{values-do.yaml,cert-issuer.yaml}`.
- **Deploy runbook** — `deploy/RUNBOOK-do-k8s.md`, 11 steps, ~40 min, ~$24/mo.
- **Fly.io deploy path** — `fly.toml`, `.dockerignore`; alternative to K8s.
- **CLI subcommands** — `plnt playground {up, models, chat, curl}` +
 `plnt deploy <name> --model <ref>` (renders InferenceModel manifest,
 optional `--apply`).
- **Contract test** — `tests/test_site_contract.py` pins the exact wire
 shapes plnt-site's `api.ts` consumes (CORS preflight + SSE frame shape).
- **Docs** — README rewrite; docs/{getting-started, api-contract,
 local-dev, architecture, PRD, PRD-playground, ERD}.
- **Scaffolds for v0.2+** — `plnt/charts/vllm-runtime/`,
 `plnt/operators/` (CRD + controller), `plnt/workflows/` (Temporal
 deploy saga + activities + worker), `examples/llama-3.1-70b-instruct.yaml`.

### Origin story

The pre-existing personal-runtime code under
`plnt/{surface, control, execution, compute, memory}` and `plnt-tui/`
still builds and runs. `plnt up`, `plnt submit`, `plnt runs`, etc. still
work. It is frozen as origin story — see [`ARCHITECTURE.md`](ARCHITECTURE.md).

[Unreleased]: https://github.com/devdattatalele/plnt/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/devdattatalele/plnt/releases/tag/v0.1.0
