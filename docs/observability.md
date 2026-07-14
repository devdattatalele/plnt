# Observability

How you know plnt is healthy, and what to look at when it isn't.

## Signals

The four RED signals — Rate, Errors, Duration, Saturation — apply to
the playground API. Not all are wired in v0.1; v0.4 makes them
first-class.

| Signal      | Where it lives now (v0.1)              | Where it goes (v0.4)              |
|-------------|----------------------------------------|-----------------------------------|
| Rate        | uvicorn access log                     | Prometheus `http_requests_total`  |
| Errors      | uvicorn access log, structured `log`   | `http_requests_total{status="5xx"}` |
| Duration    | *(not measured)*                       | `http_request_duration_seconds`   |
| Saturation  | K8s HPA CPU/mem                        | HPA + GPU-util (DCGM exporter)    |
| TTFT        | *(not measured)*                       | `chat_completion_ttft_seconds`    |
| TPOT        | *(not measured)*                       | `chat_completion_tpot_seconds`    |

## Health probes

Two endpoints, no shortcuts:

- `GET /healthz` — liveness. Returns `{"status":"ok"}` if the process
  is up. Wired to the k8s livenessProbe. Failure -> pod restart.
- `GET /readyz` — readiness. Returns 503 if the model registry is empty,
  200 otherwise. Wired to the k8s readinessProbe. Failure -> pod removed
  from the Service backends until it recovers.

External uptime probe (recommended): a simple GET on `/healthz` every
minute from any monitoring service (Better Uptime free tier, UptimeRobot,
Cloudflare's built-in health checks).

## Logs

- **Format:** stdout/stderr plain text (uvicorn default). No JSON logger
  in v0.1; a `structlog` swap is a v0.4 idea.
- **Where:** `kubectl logs -n plnt <pod> -f`.
- **What matters:**
  - `INFO:     uvicorn.access:  200 GET /v1/models` — every request line.
  - `ERROR    plnt.playground: upstream error for model <id>` — HTTPBackend
    failed. Check the upstream runtime pod.
  - `ERROR    plnt.playground: stream error for model <id>` — SSE broken
    mid-stream. Often a client disconnect; sometimes a real upstream
    fault.

## The Temporal side (once v0.4 wires the operator)

Deploy sagas are visible in the Temporal Web UI:

```bash
kubectl -n temporal port-forward svc/temporal-web 8088
open http://localhost:8088
```

Filter by workflow type `DeployModelWorkflow`. Each execution shows the
full step tree, retries, and (on failure) the compensation trace.

## Kubernetes-side

```bash
# playground pods
kubectl -n plnt get pods
kubectl -n plnt describe pod <name>
kubectl -n plnt logs <name> --tail=200 -f

# events (why did that pod fail to schedule?)
kubectl -n plnt get events --sort-by=.lastTimestamp

# ingress + cert
kubectl -n plnt get ingress
kubectl -n plnt describe certificate playground-plnt-work-tls

# HPA
kubectl -n plnt get hpa
```

## Alerting (recommended once metrics exist)

The alerts worth paging on for a demo-tier service:

1. **Playground API down** — external `/healthz` probe fails 3 consecutive
   minutes.
2. **cert expiring** — cert-manager `renewalTime` < 7 days out and status
   not `Ready`.
3. **HPA at max, saturated** — pods stuck at `.spec.maxReplicas` for > 15 min
   with CPU > 90%. Either bump the ceiling or investigate a leak.

Not paging-worthy for v0.1: individual 5xx bursts, single pod restarts.

## Cost telemetry

Deployment cost lives in the DO invoice. Rough back-of-envelope:

- 1x `s-1vcpu-2gb` node: $12/mo
- 1x DO LoadBalancer: $12/mo
- DOCR starter: free
- Egress: negligible at demo traffic

Total ~$24/mo baseline. HPA can spawn additional nodes if traffic warrants;
the cluster's autoscaling pool caps at 3 nodes by default.

## What's deliberately not measured

- Per-user metrics (no user identity).
- Prompt content (privacy default).
- Model-quality metrics (not the platform's job).

## Related

- [Deploy runbook](../deploy/RUNBOOK-do-k8s.md) — how to reach the pods.
- [Threat model](threat-model.md) — what "bad state" means to plnt.
- [Roadmap](../ROADMAP.md) — when the missing metrics land.
