# Kind demo — 60-second screencast script

For the NVIDIA interview. Runs entirely on your laptop, no cloud, no GPU.
Deploys the plnt operator + Temporal + playground API on a local kind cluster,
then applies an InferenceModel and shows the deploy saga running end-to-end.

The saga uses a **CPU-stub image** in place of vLLM so the flow works without a
real GPU. Frame this honestly in the interview: *"the platform layer is what
you're seeing; hardening for GPU workloads is where I want to grow into the role."*

## Prereqs (one-time)

```sh
brew install kind kubectl helm
```

## The demo — copy/paste in order

### 1. Cluster + operator install (~90 s)

```sh
# create a local single-node cluster
kind create cluster --name plnt

# install the CRD + operator + Temporal
kubectl apply -f plnt/operators/crds/inferencemodel.yaml
kubectl create namespace plnt-system
helm install temporal temporal/temporal --namespace plnt-system \
 --set server.replicaCount=1 --set cassandra.config.cluster_size=1

# install the playground API gateway (mock backend, works without any real model)
helm install plnt-playground plnt/charts/playground-api --namespace plnt-system

# start the operator locally (dev mode — in prod this ships as a Deployment)
python -m kopf run plnt/operators/inferencemodel_controller.py --namespace=default &

# start the Temporal worker
python -m plnt.workflows.worker &
```

### 2. The one command that matters (~30 s)

```sh
kubectl apply -f examples/llama-3.1-70b-instruct.yaml
```

Then in three side-by-side terminal panes:

```sh
# pane A — the resource going through phases
kubectl get inferencemodel -w
# NAME RUNTIME GPU PHASE ENDPOINT
# llama-3.1-70b-instruct vllm nvidia.com/h100 Validating
# llama-3.1-70b-instruct vllm nvidia.com/h100 PullingWeights
# llama-3.1-70b-instruct vllm nvidia.com/h100 Deploying
# llama-3.1-70b-instruct vllm nvidia.com/h100 SmokeTesting
# llama-3.1-70b-instruct vllm nvidia.com/h100 Promoting
# llama-3.1-70b-instruct vllm nvidia.com/h100 Ready llama-3.1-70b-instruct.default.svc:8000

# pane B — the operator picking up the event
kubectl logs -f deployment/plnt-operator -n plnt-system
# InferenceModel/llama-3.1-70b-instruct created — starting DeployModelWorkflow

# pane C — the Temporal workflow history
open http://localhost:7233 # Temporal UI, shows the saga step-by-step
```

### 3. Actually call the model (~15 s)

```sh
kubectl port-forward svc/plnt-playground 8080:8080 -n plnt-system &

curl -s http://localhost:8080/v1/chat/completions \
 -H 'content-type: application/json' \
 -d '{"model":"plnt-mock-7b","messages":[{"role":"user","content":"say hi"}]}' \
 | jq .
```

Response comes back OpenAI-shape. In production the same request hits vLLM/TGI/TRT-LLM/SGLang on real GPUs.

### 4. Rollback story (~15 s)

```sh
kubectl delete inferencemodel llama-3.1-70b-instruct
# operator cancels workflow -> workflow triggers helm uninstall via compensation
# kubectl get pods shows the release being removed
```

## Recording tips

- Use `asciinema rec kind-demo.cast` — text terminal recording, tiny file, embeds anywhere.
- Or QuickTime -> New Screen Recording, cropped to iTerm window.
- Speed up 2× in post — nobody wants to watch the helm install for real.
- One take. Rehearse once; imperfect > polished.

## Talking points for the interview

1. **"This is the deploy contract"** — show `examples/llama-3.1-70b-instruct.yaml`.
2. **"This is the operator watching it"** — flash `plnt/operators/inferencemodel_controller.py`.
3. **"This is the saga that does the work"** — flash `plnt/workflows/deploy_model.py`, point at the 5-step try/except with compensation.
4. **"This is the runtime chart"** — show `plnt/charts/vllm-runtime/templates/deployment.yaml`, highlight the `nvidia.com/gpu` resource request.
5. **"Playground is at play.plnt.work"** — open it, chat with a real NIM-backed model.

Total demo run-time: 3–4 minutes. Rehearsed cold: 6 minutes.
