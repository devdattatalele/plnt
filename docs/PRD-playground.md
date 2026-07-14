# PRD — plnt playground

**Status:** Draft v1 · 2026-07-14
**Owner:** Devdatta Talele
**One-liner:** An OpenAI-compatible chat playground served at
`api.plnt.work`, embedded into the plnt.work marketing site, so a visiting
hiring manager can talk to a model deployed on the plnt Kubernetes platform
inside 10 seconds of landing on the page.

---

## 1. Why this exists

The plnt.work site pitches a platform for "multi-model inference on
Kubernetes — Helm charts, Temporal workflows, `InferenceModel` CRD +
operator." Words are cheap. A hiring manager reading the pitch has three
questions:

1. Does the platform actually run?
2. Can I try it right now, without a signup?
3. Does the API look like something I recognise (OpenAI shape)?

The playground answers all three in one screen. It is the **proof surface**
for the platform, and the single most important artifact for the NVIDIA NIM
Factory interview (JR2003580).

## 2. Target users, in order

| Persona                                          | Weight | What they do here                                                                                    |
|--------------------------------------------------|--------|------------------------------------------------------------------------------------------------------|
| **NVIDIA hiring manager** (primary)              | 70%    | Lands from LinkedIn / resume link, scrolls hero, clicks "Try it", sends 1-3 prompts, closes tab.     |
| **Other infra recruiters** (secondary)           | 20%    | Same flow, less patience.                                                                            |
| **Devdatta himself, in interviews** (tertiary)   | 10%    | Screenshares plnt.work/playground live to walk through the platform on the same screen as an IDE.    |

Explicit **non-users**: end consumers wanting a free LLM UI. This is not
ChatGPT. There is no login, no history, no sharing, no rate limit beyond
what the cluster can serve.

## 3. Goals

- **G1** — First contentful token in < **2 seconds** from clicking Send
  (mock backend; real vLLM will be slower and that's fine).
- **G2** — Zero setup for the visitor: no signup, no cookies, no email gate.
- **G3** — Reveal the platform. Every visible surface (model card, "runtime:
  vllm/mock", latency chip) reinforces that this is *not* an OpenAI proxy —
  it's a K8s-served fleet.
- **G4** — Ship-ability. The whole playground (API + Helm chart + Docker
  image + deploy runbook) is version-controlled in this repo and installable
  by `helm install` in one command.
- **G5** — Convertible to real. When the vllm-runtime chart lands, swapping
  the mock backend for real vLLM is a `values.yaml` edit and a `helm upgrade`,
  no code change.

## 4. Non-goals

- ❌ Rate limiting, API keys, per-user quotas. The cluster's HPA is the rate
  limiter of last resort; over that, we accept 429s from the pod.
- ❌ Chat history, saved conversations, sharing. Every session is
  ephemeral; refresh = wipe.
- ❌ RAG, tools, function calling. Just chat completions.
- ❌ Multi-tenant isolation. One playground, one shared cluster budget.
- ❌ Anything on-device, offline, or in a service worker. Server-rendered
  chat, SSE for streaming.
- ❌ A "compare two models side by side" panel in v1 — nice to have, but
  not required for the interview surface.
- ❌ Custom model uploads from the UI. Model lifecycle is a Helm concern
  (see G5); the UI reads what the cluster serves.

## 5. Success metrics

Measured on the deployed API + a single-page-app analytic on the site.

| Metric                                        | Target v1        | How measured                                                       |
|-----------------------------------------------|------------------|--------------------------------------------------------------------|
| Playground page views / month                 | 200+             | Site analytics (Cloudflare Web Analytics — no cookies).            |
| Chat sessions started (≥1 prompt sent)        | 60+ / month      | `/v1/chat/completions` request count (Prometheus, once wired).     |
| Median TTFT, mock backend                     | < 500ms          | API latency histogram, p50.                                        |
| p99 TTFT, mock backend                        | < 2s             | API latency histogram, p99.                                        |
| API uptime                                    | 99.5%            | External check (e.g. Better Uptime free tier) hitting `/healthz`.  |
| Cost                                          | ≤ $30/mo         | DO invoice.                                                        |
| Time from clicking Send → first token visible | < 2s (mock)      | Manual, on a fresh incognito session.                              |

## 6. Scope — v1 (this repo, shipped)

- **API** — FastAPI, OpenAI-shape (`GET /v1/models`, `POST /v1/chat/completions`
  streaming + non-streaming, `/healthz`, `/readyz`).
- **Backends** — `MockBackend` (deterministic echo, streaming word-by-word);
  `HTTPBackend` (proxies to any OpenAI-shape upstream — vLLM, TGI, SGLang).
- **Registry** — ConfigMap-driven model list. `helm upgrade` = restart =
  new model list. No REST mutation.
- **Container image** — `docker/playground-api.Dockerfile`, non-root,
  read-only rootfs, healthcheck.
- **Helm chart** — `plnt/charts/playground-api` with Deployment + Service +
  ConfigMap + Ingress + HPA + imagePullSecrets. cert-manager annotations for
  TLS. SSE-safe nginx annotations.
- **Deploy overlay** — `deploy/do-k8s/values-do.yaml` targeting
  `api.plnt.work` on DOKS in sfo3.
- **Runbook** — `deploy/RUNBOOK-do-k8s.md`, 11 steps, ~40 min.
- **Tests** — 7 pytest cases covering list, non-stream, stream,
  unknown-model, readyz-with-and-without-models.

## 7. Scope — v2 (next 4 weeks)

- **Real vLLM backend** — ship `plnt/charts/vllm-runtime` (HANDOFF.md
  Phase 1), register a CPU-stub or single-GPU model in the playground's
  ConfigMap, prove end-to-end HTTP proxy path.
- **Prometheus + latency histograms** — expose `/metrics`, wire the four
  RED signals (rate, errors, duration, saturation). Enables the uptime
  and TTFT metrics above.
- **Model badges in the UI** — the site's playground shows `mock` vs
  `vllm` vs `tgi` vs `sglang` badges from `/v1/models` `runtime` field.
- **Cold-start optimisation** — investigate scale-to-1 vs scale-to-0
  trade-off (SSE cold-start is user-facing).

## 8. Scope — v3+ (later, HANDOFF.md phases)

- Temporal `DeployModelWorkflow` (Phase 2) — the playground doesn't
  directly consume this, but the model list starts being managed by the
  workflow output instead of a raw ConfigMap.
- `InferenceModel` CRD + kopf operator (Phase 3) — `kubectl apply -f
  llama-70b.yaml` deploys a new backend AND registers it in the playground
  automatically (operator patches the ConfigMap, playground pod restart
  picks it up).
- Benchmarking harness (Phase 6) — `plnt bench` numbers surfaced as a
  "cluster health" strip on the playground page.

## 9. Architecture (v1 — deployed)

```
              Cloudflare DNS
                 │
                 │  api.plnt.work                  playground.plnt.work
                 ▼                                          │
        ┌────────────────┐                        (rewrite) ▼
        │  DOKS ingress  │                          plnt.work/playground
        │   LoadBalancer │                                  │
        └───────┬────────┘                                  │
                │                                           │  fetch()
                ▼                                           │
       ┌─────────────────┐                                  │
       │  playground-api │  ◄──── /v1/chat/completions ─────┘
       │  (2 pods, HPA)  │
       └───────┬─────────┘
               │  routes on model.id
      ┌────────┼─────────────────────────┐
      ▼        ▼                         ▼
  MockBackend  MockBackend         HTTPBackend (future)
                                        │
                                        ▼
                                    vLLM pod
                                    (Phase 1 chart)
```

## 10. Open questions

- **Q1** — Do we ever want auth? Not for v1. Revisit if abuse becomes a
  cost problem (a single Cloudflare rate-limit rule on `POST
  /v1/chat/completions` is the escape hatch).
- **Q2** — Should the playground surface `plnt-cloud` as a preset model
  ("try our booking agent")? Nice sales angle but adds coupling — defer to
  after v2 real-model shows the plumbing works.
- **Q3** — Multi-region? Not v1 or v2. sfo3 is close enough to Santa
  Clara that the answer is "no" until traffic justifies it.
- **Q4** — Should the mock backend get retired once real vLLM is live?
  Probably not — it's the fastest thing to demo when the GPU pod is cold
  or unavailable. Keep it as `plnt-mock-7b` alongside real models.

## 11. Risks & mitigations

| Risk                                                       | Likelihood | Mitigation                                                                              |
|------------------------------------------------------------|------------|-----------------------------------------------------------------------------------------|
| Cloudflare orange-cloud buffers SSE                        | Med        | Grey cloud on `api.plnt.work`. Documented in runbook §6.                                |
| DOKS cluster costs creep past $30/mo                       | Low        | 1-node autoscaling pool max 3; HPA max 6 pods; smallest node size.                      |
| Let's Encrypt HTTP-01 fails at first attempt               | Med        | Runbook §5 tells reader to start with `letsencrypt-staging`, then switch to prod.       |
| Image pulled for wrong arch on Apple silicon               | High       | Runbook §1 hard-codes `--platform linux/amd64`; failure mode called out in §Common.     |
| SSE responses arrive batched (proxy buffering)             | Med        | `nginx.ingress.kubernetes.io/proxy-buffering: off` set in values-do.yaml; verified with curl in §8. |
| Site UI ships before API — visitor sees broken chat        | Low        | Site already falls back to a stub if `PUBLIC_PLNT_ENDPOINT` unset (per site agent).     |
| Prompt injection in mock backend (echoes user text)        | Low        | Echo is server-rendered as plain text; no HTML injection surface.                       |

## 12. Rollout

1. **W0** — API + chart + runbook merged to `main` (done).
2. **W0 +1d** — `docker buildx build --push`, cluster up, first `helm install`,
   `api.plnt.work` live with mock models. Cloudflare A record added.
3. **W0 +2d** — site agent flips `PUBLIC_PLNT_ENDPOINT` to
   `https://api.plnt.work`, redeploys site. End-to-end demo works from
   `plnt.work` landing → `/playground` → chat.
4. **W0 +7d** — write the `vllm-runtime` chart (HANDOFF Phase 1).
5. **W0 +14d** — first real (CPU-stub or single-GPU) model registered in
   the playground. Mock stays as the fast-path default.
