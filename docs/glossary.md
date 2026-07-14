# Glossary

Terms that appear in plnt code, docs, and the site — with the definition
that we mean, not the definition that Wikipedia gives.

---

### Adapter

See [RuntimeAdapter](#runtimeadapter).

### Canary

The initial low-traffic-percent rollout of a new model. Default 5%. If
the [smoke test](#smoke-test) passes, canary is promoted to 100%; if it
fails, `helm rollback` reverts.

### ConfigMap-driven registry

The playground API's model list is read once at pod startup from a
mounted ConfigMap. `helm upgrade` with a new list restarts the pod and
picks up the new registry. There is no REST mutation API.

### CRD

Custom Resource Definition — a Kubernetes-native way to add a new
"kind" of object. plnt defines one: `apiVersion: plnt.work/v1, kind:
InferenceModel`.

### Deploy saga

The Temporal workflow that turns an `InferenceModel` apply into a
running, traffic-served pod. Five activities: validate -> pull-and-verify
weights -> helm-install-canary -> smoke-test -> promote-or-rollback.
Compensation on failure.

### FastAPI

The Python HTTP framework the playground API is built on. Handles
routing, pydantic validation, OpenAPI schema generation, and Server-Sent
Events.

### HPA

Horizontal Pod Autoscaler. Kubernetes primitive for scaling a
Deployment on CPU/memory/custom metrics. Every runtime chart ships one;
the playground-api chart ships one.

### HTTPBackend

`RuntimeAdapter` implementation that proxies to any OpenAI-compat
upstream over HTTP. vLLM, TGI (`--openai-api`), SGLang, llama.cpp
server all satisfy this contract. See
[`plnt/playground/backends.py`](../plnt/playground/backends.py).

### InferenceModel

The plnt CRD. The declarative unit of "I want this model, served by
this runtime, on this cluster." Applied by `kubectl apply -f` or
`plnt deploy`. Watched by the plnt operator.

### JTBD

Job-to-be-done. Framing from Clay Christensen. What is the user
actually trying to accomplish when they reach for plnt? See
[`docs/PRD.md` §3](PRD.md#3-users--jobs-to-be-done).

### Kind (cluster)

`kind` — Kubernetes IN Docker. A local single-node cluster useful for
demo and testing. plnt's local demo path uses kind.

### kopf

Python framework for writing Kubernetes operators. The plnt operator
uses it — see [`plnt/operators/inferencemodel_controller.py`](../plnt/operators/inferencemodel_controller.py).

### MockBackend

`RuntimeAdapter` implementation that echoes the last user message
word-by-word via SSE. Zero dependencies, no GPU, no network. The
default backend when no model config is supplied.

### NIM

NVIDIA Inference Microservice. NVIDIA's first-party program for
containerised model serving. plnt is the OSS, bring-your-own-cluster
spiritual analog. See [`docs/PRD.md` §7](PRD.md#7-market-context).

### Playground

The public demo surface at `plnt.work/playground` (UI, owned by
plnt-site) and `playground.plnt.work` (API, owned by this repo).

### Registry (plnt-scope)

The playground API's in-memory model index. Not to be confused with
container registry (DOCR, GHCR) or model registry (MLflow).

### Runtime

An ML inference server: vLLM, TGI, TRT-LLM, SGLang, mock. Each has a
Helm chart in `plnt/charts/{runtime}-runtime/`.

### RuntimeAdapter

Python protocol in `plnt/playground/backends.py` that abstracts "how do
I talk to a runtime." Implementations: MockBackend, HTTPBackend
(v0.1), TritonBackend (v0.5, planned).

### Saga

Long-running distributed transaction with compensation. The plnt
DeployModelWorkflow is a saga: each activity's failure triggers a
`helm rollback` compensation. Same pattern as plnt-cloud's booking saga.

### Smoke test

The gate activity in the deploy saga. Runs N test prompts against the
canary; asserts TTFT p50 ≤ threshold. Fails the saga if the model
doesn't meet the perf bar.

### SSE

Server-Sent Events. Unidirectional stream from server to client over
HTTP, `Content-Type: text/event-stream`. Used by the playground API for
streaming chat completions.

### Temporal

Durable workflow engine. Owns the deploy saga's event history and
retry state — outlives pod restarts and controller crashes.

### TPOT

Time Per Output Token. The steady-state per-token latency during a
streamed completion. Reported by `plnt bench`.

### TTFT

Time To First Token. Latency from request accepted to first chunk sent.
The primary gate metric in the smoke test.

### values.yaml

The Helm chart's configuration document. Every runtime chart shares a
common core (model.name, resources.gpu, replicas.min/max) — see
[CONTRIBUTING](../CONTRIBUTING.md#runtime-chart-contract).

### vLLM

The reference OSS inference runtime. Paged attention + prefix cache +
continuous batching. First runtime plnt ships an end-to-end chart for.
