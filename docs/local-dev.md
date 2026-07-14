# Local dev — playground API + plnt-site together

Run the plnt playground API and the plnt.work site on the same laptop.
The site's chat panel talks to the API via `PUBLIC_PLNT_ENDPOINT` and CORS
is pre-configured for the Astro dev port.

## The two processes

| Process        | Repo         | Port | Command                          |
|----------------|--------------|------|----------------------------------|
| Playground API | plnt (here)  | 8080 | `plnt playground up`             |
| Site (Astro)   | plnt-site    | 4321 | `npm run dev`                    |

CORS is already open for `http://localhost:4321` — no proxy, no rewrite,
no `--host` flag hackery. The browser can talk to both.

## 1. Start the API

```bash
cd plnt/
source .venv/bin/activate                # or however you activate
plnt playground up
```

Leaves a foreground process on `http://127.0.0.1:8080` with a single mock
model (`plnt-mock-7b`). Verify:

```bash
curl -s http://127.0.0.1:8080/v1/models | jq
```

## 2. Point the site at it

In another terminal:

```bash
cd plnt-site/
PUBLIC_PLNT_ENDPOINT=http://127.0.0.1:8080 npm run dev
```

Open `http://localhost:4321/playground`. The chat panel:

- fetches `/v1/models` on load — you'll see whatever's in the API's registry.
- sends `POST /v1/chat/completions` when you hit Send.
- falls back to canned "stub" replies if the fetch fails, so the UI
  keeps working even when the API is down.

If you see the stub reply and expected a real one, check:

1. **API is up.** `curl -s http://127.0.0.1:8080/healthz` should return
   `{"status":"ok"}`.
2. **CORS.** Open DevTools -> Network -> look for a red preflight
   (`OPTIONS`) request. If the `Access-Control-Allow-Origin` header is
   missing, `PLNT_PLAYGROUND_CORS_ORIGINS` in the API's env may have been
   set narrower than expected. Unset it and restart the API.
3. **Model id.** The site's default `models.ts` hardcodes `llama-3-70b`,
   `mistral-7b`, `deepseek-coder-33b`, `qwen2-72b`. If the API's registry
   uses different ids, the chat request 404s.

## 3. Load the site's model set locally

If you want the API to accept the site's default model ids, mount the
matching registry:

```bash
cat > /tmp/site-models.json <<'EOF'
[
  {"id": "llama-3-70b",         "backend": "mock", "runtime": "vllm"},
  {"id": "mistral-7b",          "backend": "mock", "runtime": "tgi"},
  {"id": "deepseek-coder-33b",  "backend": "mock", "runtime": "sglang"},
  {"id": "qwen2-72b",           "backend": "mock", "runtime": "trt-llm"}
]
EOF

PLNT_PLAYGROUND_CONFIG=/tmp/site-models.json plnt playground up
```

Now `/playground` in the site can select any of the four models and get a
live (mock) reply through the API.

## 4. Add a real vLLM upstream (optional)

If you have vLLM running locally on port 8000:

```bash
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --port 8000
```

Point the playground API at it:

```json
[
  {
    "id": "llama-3-8b-local",
    "backend": "http",
    "runtime": "vllm",
    "upstream_url": "http://127.0.0.1:8000",
    "upstream_model": "meta-llama/Llama-3.1-8B-Instruct"
  }
]
```

Same `PLNT_PLAYGROUND_CONFIG=<path>` env; restart `plnt playground up`.

The API now proxies every `/v1/chat/completions` request for
`llama-3-8b-local` to your vLLM. The site UI works unchanged.

## 5. Watch both processes

Optional but nice: `mprocs` or `tmux` split so both stdout streams are
visible.

```bash
brew install mprocs
mprocs "cd plnt && plnt playground up" \
       "cd plnt-site && PUBLIC_PLNT_ENDPOINT=http://127.0.0.1:8080 npm run dev"
```

## Running the contract tests locally

Any time you change the API or the site's `api.ts`, run the contract
test to guarantee the wire shapes still match:

```bash
cd plnt/
pytest tests/test_site_contract.py -v
```

If it fails after a legitimate site change, update `SITE_MODELS` and the
frame-shape assertions in that test to match.

## Troubleshooting

| Symptom                                                | Fix                                                                                         |
|--------------------------------------------------------|---------------------------------------------------------------------------------------------|
| Chat panel always shows "Stub mode" — no live reply     | API down, wrong endpoint, CORS blocked, or model id mismatch. Check DevTools Network tab.  |
| CORS preflight red in DevTools                          | Add your dev origin to `PLNT_PLAYGROUND_CORS_ORIGINS`, or unset it to use defaults.        |
| `plnt playground up` complains about port in use        | `lsof -iTCP:8080 -sTCP:LISTEN` -> kill, or `plnt playground up --port 8090`.                |
| Site can't find `PUBLIC_PLNT_ENDPOINT`                  | It's a Vite/Astro `PUBLIC_*` prefix — must be set BEFORE `npm run dev`, not exported later. |
| API's `/v1/models` returns `[]`                         | `PLNT_PLAYGROUND_CONFIG` path doesn't exist or JSON is malformed. Restart with a fixed file. |
