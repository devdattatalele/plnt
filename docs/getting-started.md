# Getting started

Boot the plnt playground locally in under 60 seconds. No cluster, no GPU,
no signup.

## 1. Install

```bash
git clone https://github.com/devdattatalele/plnt && cd plnt
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

The `.[dev]` extra pulls pytest and ruff. Skip it if you're just running,
not editing.

## 2. Start the playground

```bash
plnt playground up
```

You'll see:

```
plnt playground on http://127.0.0.1:8080 — mock backend, ctrl+C to stop.
  models:  http://127.0.0.1:8080/v1/models
  chat:    POST http://127.0.0.1:8080/v1/chat/completions
  docs:    http://127.0.0.1:8080/docs
```

The default backend is a mock that echoes your prompt with fake streaming —
enough to verify the wire is intact. Real vLLM comes later (see
[Local dev](./local-dev.md)).

## 3. Talk to it

In a second terminal:

```bash
# list models
plnt playground models

# one-shot chat, streams to stdout
plnt playground chat plnt-mock-7b "hello, plnt"

# same but non-streaming
plnt playground chat plnt-mock-7b "hello" --no-stream

# copy-paste curl examples for anywhere else
plnt playground curl
```

Or directly with curl:

```bash
curl -s http://127.0.0.1:8080/v1/models | jq
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"plnt-mock-7b","messages":[{"role":"user","content":"hi"}]}' \
  | jq
```

## 4. Add your own models

Point the API at a config file:

```bash
cat > /tmp/models.json <<'EOF'
[
  {"id": "gpt-echo", "backend": "mock", "runtime": "mock"},
  {"id": "local-vllm",
   "backend": "http",
   "runtime": "vllm",
   "upstream_url": "http://127.0.0.1:8000"}
]
EOF

PLNT_PLAYGROUND_CONFIG=/tmp/models.json plnt playground up
```

The HTTP backend proxies to any OpenAI-compatible upstream — vLLM (default),
TGI (`--openai-api`), SGLang, llama.cpp server all work unchanged.

## 5. Run the tests

```bash
pytest tests/test_playground_api.py tests/test_site_contract.py -v
```

15 tests. Runs in ~1s. `test_site_contract.py` pins the exact wire shapes
the plnt.work site consumes, so you can't accidentally break the frontend.

## Next

- [Local dev](./local-dev.md) — run the playground API and the plnt-site
  UI on the same laptop, with CORS pre-configured.
- [API contract](./api-contract.md) — the OpenAI subset the playground
  implements, with request/response schemas.
- [Architecture](./architecture.md) — where the playground sits in the
  larger plnt K8s platform.
- [Deploy runbook](../deploy/RUNBOOK-do-k8s.md) — ship it to
  `playground.plnt.work` on DigitalOcean Kubernetes.
