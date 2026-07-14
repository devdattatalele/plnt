# Security policy

## Supported versions

plnt is pre-1.0 and unstable. Only the latest tagged release + `main`
receive security fixes.

| Version | Supported |
|-----------|--------------------|
| latest | [done] (fixes on main) |
| < latest | [not] |

## Reporting a vulnerability

**Do not** open a public GitHub issue for a security bug.

Email `bonde.sagar@gmail.com` with:

- A description of the issue.
- Reproduction steps (a minimal repo, curl, or `kubectl` example is ideal).
- Your assessment of impact (data exposure? RCE? DoS? auth bypass?).
- Your GitHub handle if you'd like credit in the fix's release notes.

Response targets (best-effort — this is a hobby project):

| Severity | Ack | Fix or public workaround |
|----------|---------|--------------------------|
| Critical | 24h | 7 days |
| High | 72h | 14 days |
| Medium | 1 week | 30 days |
| Low | 2 weeks | best effort |

## Threat model summary

Full threat model: [`docs/threat-model.md`](docs/threat-model.md).

Short version — what plnt does and does not protect:

**In scope:**

- The playground API pod is non-root, read-only rootfs, no privileged
 capabilities.
- CORS is a browser-facing allowlist, not an auth mechanism.
- Helm charts use `imagePullSecrets` — no anonymous pulls from private
 registries.
- Container image is minimal (only playground deps) — small blast
 radius on CVE.
- Sensitive values (NIM keys, MLflow creds) come from `Secret`s or
 `fly secrets`, never inline.

**Explicitly out of scope for v1:**

- Multi-tenant isolation. One tenant per cluster.
- Per-user authentication or authorization at the API. Cloudflare
 Access is the recommended add-on.
- Rate limiting. Add at the ingress if you need it.
- Model weight provenance / supply-chain attestation. Hash-check-only.
- GPU sandboxing between models. Kernel-level MIG or per-pod GPU
 assignment is the operator's job.

## What plnt intentionally trusts

- The Kubernetes cluster itself and everyone with `apply` rights on it.
- Whoever writes the InferenceModel YAML — plnt's operator does not
 validate the model URI is "safe."
- The runtime container image — plnt does not scan vLLM/TGI/SGLang
 images. Use your own scanner.

## Coordinated disclosure

Once a fix is available:

1. A patch release is cut.
2. A GitHub Security Advisory is published on this repo.
3. The reporter is credited (unless they prefer anonymity).
4. CVE is requested if the impact meets standard criteria.
