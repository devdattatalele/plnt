# API contract

The plnt playground implements a subset of the OpenAI API — just the
surface the plnt.work chat panel and any OpenAI client SDK actually need.
This document is the canonical spec; the pytest suite `tests/test_site_contract.py`
mechanically enforces it.

Base URL: `https://playground.plnt.work` (prod) · `http://127.0.0.1:8080`
(local `plnt playground up`).

## Endpoints

| Method | Path                    | Purpose                          |
|--------|-------------------------|----------------------------------|
| GET    | `/healthz`              | Liveness. Always 200 once up.    |
| GET    | `/readyz`               | Readiness. 503 if no models.     |
| GET    | `/v1/models`            | List registered models.          |
| POST   | `/v1/chat/completions`  | Chat completion (± SSE stream).  |
| GET    | `/`                     | Service banner + link to `/docs`.|
| GET    | `/docs`                 | Swagger UI (FastAPI auto-gen).   |

## GET /v1/models

Response:

```jsonc
{
  "object": "list",
  "data": [
    {
      "id": "plnt-mock-7b",
      "object": "model",
      "created": 1784017752,
      "owned_by": "plnt",
      "runtime": "mock",       // "mock" | "vllm" | "tgi" | "sglang" | "trt-llm"
      "backend": "mock"        // "mock" | "http"
    }
  ]
}
```

The `runtime` and `backend` fields are plnt-specific extensions — an OpenAI
client will ignore them. The site uses `runtime` to badge each model card.

## POST /v1/chat/completions

Request:

```jsonc
{
  "model": "plnt-mock-7b",                              // required, must appear in /v1/models
  "messages": [                                         // required, ≥ 1
    {"role": "system", "content": "You are helpful."},  // optional
    {"role": "user", "content": "hello"}
  ],
  "stream": false,          // default false; true → SSE
  "max_tokens": 256,        // default 256, 1..8192
  "temperature": 0.7,       // default 0.7, 0.0..2.0
  "top_p": 1.0              // default 1.0, 0.0..1.0
}
```

Non-streaming response (`stream: false`):

```jsonc
{
  "id": "chatcmpl-45dca0d6a9cd470bb4d2b8b4",
  "object": "chat.completion",
  "created": 1784017753,
  "model": "plnt-mock-7b",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "..."},
      "finish_reason": "stop"                    // "stop" | "length" | "error"
    }
  ],
  "usage": {
    "prompt_tokens": 4,
    "completion_tokens": 42,
    "total_tokens": 46
  }
}
```

Streaming response (`stream: true`) — Server-Sent Events, `text/event-stream`:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"content":"Hello "},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{"content":"world"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":...,"model":"...","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

Frame invariants (asserted in `tests/test_site_contract.py`):

- Content-Type is `text/event-stream`.
- First frame's `delta.role` is `"assistant"`.
- Interior frames carry `delta.content` deltas — clients concatenate them.
- Last real frame sets `choices[0].finish_reason`.
- Stream terminates with `data: [DONE]`.

## Errors

| Status | When                                                 |
|--------|------------------------------------------------------|
| 400    | Malformed body (pydantic validation).                |
| 404    | `model` not in `/v1/models`.                         |
| 502    | Upstream backend (HTTPBackend) errored or timed out. |
| 503    | Registry empty (`/readyz` only).                     |

Errors are JSON: `{"detail": "..."}` for 4xx/5xx, matching FastAPI's default.

## CORS

Preflight (`OPTIONS`) is allowed for these origins by default:

- `https://plnt.work`, `https://playground.plnt.work` (production)
- `http://localhost:4321`, `http://127.0.0.1:4321` (Astro dev, plnt-site)
- `http://localhost:3000`, `http://127.0.0.1:3000`
- `http://localhost:8080`, `http://127.0.0.1:8080`

Override with the `PLNT_PLAYGROUND_CORS_ORIGINS` env var — comma-separated
list, or the literal `*` to open the door wide.

Allowed methods: `GET`, `POST`, `OPTIONS`. All headers are allowed.

## Auth

None. The playground is a public demo surface — see
[docs/PRD-playground.md §4](./PRD-playground.md#4-non-goals) for the
rationale.

If you need to lock it down later, the cleanest layer is a Cloudflare Access
policy on the ingress; the pod itself stays auth-free.

## Versioning

- **Chart version** — bumped in `plnt/charts/playground-api/Chart.yaml`.
- **API version** — this contract is v1 (the `/v1/...` path prefix). A v2
  path prefix would ship alongside v1, not replace it.
- **Breaking changes** to `/v1/...` require a corresponding update to
  `tests/test_site_contract.py` AND the plnt-site `api.ts`. That's the
  guard against silent breakage — the pytest run will fail visibly.

## Not implemented (won't fix)

These are OpenAI endpoints we deliberately don't serve:

- `POST /v1/completions` (legacy completions). Use chat.
- `POST /v1/embeddings`. Out of playground scope.
- `POST /v1/files`, `POST /v1/fine_tuning/*`. Model lifecycle is a Helm
  concern (see PRD §4).
- `POST /v1/images/*`, `POST /v1/audio/*`. Different playground, different day.

## Related

- Implementation: [`plnt/playground/`](../plnt/playground/)
- Schemas as pydantic models: [`plnt/playground/schemas.py`](../plnt/playground/schemas.py)
- Contract tests: [`tests/test_site_contract.py`](../tests/test_site_contract.py)
- Site consumer: [`plnt-site/src/islands/playground/api.ts`](../../plnt-site/src/islands/playground/api.ts)
