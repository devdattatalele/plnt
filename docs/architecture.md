# plnt architecture

plnt is a playground platform for deploying multi-model ML inference on
Kubernetes. Everything is Helm charts, Temporal workflows, and a Python
operator watching one CRD.

```
                          plnt.work (marketing + docs, static)
                                        │
                                        │  playground.plnt.work
                                        ▼
                          ┌───────────────────────────────┐
                          │  Playground API (FastAPI)     │  <- OpenAI-shape
                          │  GET  /v1/models              │     surface. What
                          │  POST /v1/chat/completions    │     the site's chat
                          │  (SSE stream)                 │     box calls into.
                          └──────────────┬────────────────┘
                                         │  routes on model.id
             ┌───────────────────────────┼───────────────────────────┐
             ▼                           ▼                           ▼
   ┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
   │ vllm-runtime    │         │ tgi-runtime     │         │ sglang-runtime  │
   │ (Helm chart,    │         │ (Helm chart,    │         │ (Helm chart,    │
   │  Deployment +   │         │  Deployment +   │         │  Deployment +   │
   │  Service + HPA) │         │  Service + HPA) │         │  Service + HPA) │
   │  nvidia.com/gpu │         │  nvidia.com/gpu │         │  nvidia.com/gpu │
   └────────┬────────┘         └─────────────────┘         └─────────────────┘
            │
            │  deployed by
            ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │  DeployModelWorkflow (Temporal)                                         │
   │    validate_manifest → pull_and_verify_weights → helm_install_canary    │
   │    → run_smoke_test → promote_or_rollback                               │
   │  Compensation on any step → `helm rollback`.                            │
   └───────────────────────────────┬─────────────────────────────────────────┘
                                   │  kicked off by
                                   ▼
   ┌─────────────────────────────────────────────────────────────────────────┐
   │  InferenceModel CRD + kopf operator                                     │
   │    apiVersion: plnt.work/v1                                             │
   │    kind: InferenceModel                                                 │
   │    kubectl apply -f examples/llama-70b.yaml                             │
   └─────────────────────────────────────────────────────────────────────────┘
```

## Layers

| Layer                | Component                                    | Owns                                        |
|----------------------|----------------------------------------------|---------------------------------------------|
| CLI + Python API     | `plnt/cli/`                                  | `plnt deploy`, `plnt list`, `plnt bench`    |
| Playground gateway   | `plnt/playground/`                           | OpenAI-compat REST, SSE, `RuntimeAdapter`   |
| Helm charts          | `plnt/charts/{playground-api, vllm-runtime}` | One chart per runtime + one for the gateway |
| Temporal workflows   | `plnt/workflows/`                            | Deploy saga, canary rollout, batch infer    |
| Runtime adapters     | `plnt/playground/backends.py`                | vLLM / TGI / TRT-LLM / SGLang, mock         |
| Model registry       | `plnt/registry/`                             | MLflow client + local cache                 |
| CRD + operator       | `plnt/operators/`                            | `InferenceModel` CRD, kopf controller       |
| Benchmarks           | `plnt/bench/`                                | TTFT / TPOT / tokens-per-sec/GPU            |

## Playground API

Serves the OpenAI wire format from a single URL — `playground.plnt.work`. The
site at `plnt.work` embeds a chat panel that posts to it, so the interviewer
can talk to whatever's deployed on the cluster without ever leaving the marketing
page.

Model list is configuration, not code — the Helm chart ships a ConfigMap
(`values.yaml → models`) that the pod reads at startup. `helm upgrade` with a
new list restarts the pod (annotation-driven checksum) and picks up the change.
There is no runtime `POST /models` — model lifecycle is a Helm concern, not a
REST call, which keeps the audit trail in `helm history` where cluster
operators expect it.

Backends behind the gateway:

- `mock` — deterministic echo, self-contained. Used on kind (no GPU) and in
  tests. Streams word-by-word via SSE so the UI behaves identically to a real
  model.
- `http` — proxies to any OpenAI-compatible upstream. vLLM, TGI (`--openai-api`),
  SGLang, and llama.cpp server all satisfy this contract. This is how the
  playground talks to the vllm-runtime pods once they're up.

## Deploy demo (kind, local)

```bash
# 1. bring up kind + ingress-nginx
kind create cluster --name plnt
kubectl apply -f https://kind.sigs.k8s.io/examples/ingress/deploy-ingress-nginx.yaml
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=120s

# 2. build + load the playground image
docker build -f docker/playground-api.Dockerfile -t plnt/playground-api:dev .
kind load docker-image plnt/playground-api:dev --name plnt

# 3. install the chart
helm install plnt-playground plnt/charts/playground-api \
  -f examples/values-playground.yaml

# 4. reach it
kubectl port-forward svc/plnt-playground-playground-api 8080:80
curl http://127.0.0.1:8080/v1/models
curl -N -H 'content-type: application/json' \
  -d '{"model":"plnt-mock-7b","messages":[{"role":"user","content":"hi"}],"stream":true}' \
  http://127.0.0.1:8080/v1/chat/completions
```

For a real DNS-backed setup, point `playground.plnt.work` at the ingress
controller's external IP and swap `port-forward` for the public URL.

## Honest gaps

- **No real GPU hardware yet.** Kind cluster + mock backend for the local
  demo. Runtime adapter for real vLLM is written and wired but not yet
  exercised against actual GPU pods.
- **vllm-runtime, tgi-runtime, sglang-runtime charts not shipped yet.** Only
  playground-api is ready — the runtime charts are the next milestone
  (HANDOFF.md, Phase 1).
- **CRD + operator not shipped yet.** Playground works standalone; the
  operator lands in Phase 3.
- **Multi-cluster is design-only.** Single-cluster demo, sketched routing.
