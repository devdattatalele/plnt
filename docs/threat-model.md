# Threat model

The security-focused view of what plnt is and isn't defending against.
Companion to [`SECURITY.md`](../SECURITY.md).

## System model

Three components carry different trust assumptions:

1. **Playground API pod** — public-facing, browser-callable, anonymous.
   High-exposure, low-privilege.
2. **Runtime pods (vLLM/TGI/etc.)** — internal to the cluster, receive
   traffic only via the playground API's HTTPBackend. Not directly
   exposed on the ingress.
3. **Operator + Temporal worker** — cluster-internal. Reads
   `InferenceModel` CRDs, writes `Deployment`/`Service` via Helm.
   High-privilege on the cluster; not reachable from the internet.

## STRIDE quick pass

| Threat                 | Applies to              | Mitigation                                                      |
|------------------------|-------------------------|-----------------------------------------------------------------|
| **S**poofing           | Playground API callers  | Anonymous by design (v1); no identity to spoof. Add Cloudflare Access if you need one. |
| **T**ampering          | ConfigMap-driven registry | Only cluster-admins can `helm upgrade`. No REST mutation path.  |
| **R**epudiation        | Deploy actions          | Every deploy is a `helm history` entry + Temporal workflow row. |
| **I**nformation disc.  | Prompts and completions | Not logged by plnt (structured logs contain metadata only).     |
| **D**oS                | Playground API pod      | HPA to 6 pods; per-pod uvicorn workers 1. No auth rate-limit.   |
| **E**scalation of priv | Runtime pods            | Non-root, read-only rootfs, no privileged caps, dropped ALL.    |

## Assets

- **Model weights.** Not stored in plnt-owned storage; runtime pulls
  from HuggingFace or a URI you configure. Hash verified in the
  `pull_and_verify_weights` activity (once v0.4 wires the operator).
- **Runtime container images.** Pulled from your configured registry.
  No signing / provenance in v0.1; add cosign in v1.0.
- **User prompts.** Ephemeral. Not persisted. Logged only as metadata
  (model id, timing).
- **Secrets.** Two kinds today:
  - Registry pull secrets (`docr-plnt`, `ghcr-*`) — normal K8s secrets.
  - Upstream backend API keys (e.g. `NIM_API_KEY`) — K8s Secret,
    referenced from Helm values via `secretKeyRef`. Never inline.

## Attack surface

**Externally reachable:**

- `GET /healthz`, `GET /readyz`, `GET /v1/models`, `POST /v1/chat/completions`,
  `GET /`, `GET /docs`, `GET /openapi.json` (FastAPI default).

**Not reachable from the internet:**

- Any runtime pod's raw port.
- Kubernetes API server (private endpoint recommended).
- Temporal Web UI (port-forward only).
- Operator's kopf webhook.

## Trust boundaries

1. **Browser <-> playground API** — TLS, permissive CORS allowlist.
   No cookies. No auth.
2. **Playground API <-> runtime pod** — cluster-internal HTTP. Assumed
   trusted (both are plnt-owned).
3. **Operator <-> Kubernetes API** — service account with scoped RBAC.
   Only permissions needed for Helm install/upgrade/uninstall on the
   plnt namespace.
4. **CI <-> Container registry** — GitHub Actions OIDC to GHCR (v1.0).
   Currently manual `doctl registry login`.

## What plnt intentionally trusts

- **The cluster admin.** Anyone with `kubectl apply` rights on the
  cluster can spawn runtime pods with any GPU request. plnt does not
  quota users. Use K8s ResourceQuotas.
- **The InferenceModel YAML author.** No sanitization of `model.name`
  or `storageUri` beyond scheme validation. A malicious YAML can pull
  arbitrary weights.
- **The runtime image.** vLLM, TGI, SGLang images are pulled as-is.
  Use your own scanner (Trivy, Snyk) as a pipeline gate.

## Explicitly out of scope (v1)

- **Per-user auth.** No identity. If a user calls the API, we cannot
  attribute the call.
- **Rate limiting.** Add at the ingress (nginx annotation or Envoy
  filter) if you need one.
- **Prompt logging.** Not implemented, and not planned to be enabled
  by default. If you enable it downstream, you own the compliance surface.
- **GDPR / SOC2 posture.** Not evaluated. Not target market for v1.
- **Multi-tenant isolation.** One tenant per cluster.
- **Weight provenance attestation.** Hash-check only. cosign-style
  attestations are a v1.0 idea.

## Adversary personas

1. **Curious internet user.** Hits `/v1/chat/completions` from a
   browser. Impact: baseline traffic. Handled by HPA + Cloudflare rate
   rules.
2. **Cost adversary.** Loops chat calls to burn GPU cycles. Impact:
   invoice pain. Handled by HPA ceiling and a Cloudflare rule on
   cost-spike alert.
3. **Insider with cluster access.** Can do anything K8s permits — this
   is a trust boundary plnt does not attempt to defend.
4. **Compromised runtime image.** vLLM package trojan or similar.
   Handled by image scanning at the pipeline (out of plnt's scope).

## Related

- [SECURITY.md](../SECURITY.md) — how to report a bug.
- [Deploy runbook](../deploy/RUNBOOK-do-k8s.md) — the RBAC + secret
  handling recipe.
