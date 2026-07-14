"""FastAPI app serving the OpenAI-compatible playground endpoints.

Endpoints:

* `GET  /healthz` — liveness (always 200 once the process is up)
* `GET  /readyz`  — readiness (200 once the registry has ≥1 adapter)
* `GET  /v1/models` — list registered models
* `POST /v1/chat/completions` — chat completion, SSE-streamed when `stream=true`

The registry is loaded once at startup from env vars (see `discovery.py`), so
`helm upgrade` with new values → pod restart → new model list. There is no
runtime mutation API; deploying a new model is a Helm concern, not a REST call.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from plnt.playground.discovery import Registry, load_registry
from plnt.playground.schemas import (
    ChatCompletionRequest,
    ModelList,
)

log = logging.getLogger("plnt.playground")


# Default allow-list for browser CORS. Covers:
#   - production site + playground subdomain rewrite
#   - Astro dev server (plnt-site) at localhost:4321
#   - common local ports for adjacent dev servers
# Override at deploy time with PLNT_PLAYGROUND_CORS_ORIGINS (comma-separated
# list of origins, or the literal "*" to open the door).
DEFAULT_CORS_ORIGINS = [
    "https://plnt.work",
    "https://playground.plnt.work",
    "http://localhost:4321",
    "http://127.0.0.1:4321",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]


def _cors_origins() -> list[str]:
    raw = os.environ.get("PLNT_PLAYGROUND_CORS_ORIGINS")
    if not raw:
        return DEFAULT_CORS_ORIGINS
    if raw.strip() == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def create_app(registry: Registry | None = None) -> FastAPI:
    app = FastAPI(
        title="plnt playground",
        description=(
            "OpenAI-compatible inference gateway for models deployed on the plnt "
            "platform. Backing runtimes: vLLM / TGI / TRT-LLM / SGLang, or mock."
        ),
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.state.registry = registry or load_registry()

    _mount_routes(app)
    return app


def _mount_routes(app: FastAPI) -> None:
    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        registry: Registry = app.state.registry
        if not registry.ids:
            return JSONResponse({"status": "no models"}, status_code=503)
        return JSONResponse({"status": "ready", "models": len(registry.ids)})

    @app.get("/v1/models", response_model=ModelList)
    async def list_models() -> ModelList:
        registry: Registry = app.state.registry
        return ModelList(data=registry.cards())

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest, request: Request):
        registry: Registry = app.state.registry
        adapter = registry.get(req.model)
        if adapter is None:
            raise HTTPException(
                status_code=404,
                detail=f"model {req.model!r} not registered on this playground",
            )

        if req.stream:
            return EventSourceResponse(_sse_stream(adapter, req, request))

        try:
            return await adapter.complete(req)
        except Exception as exc:  # noqa: BLE001
            log.exception("upstream error for model %s", req.model)
            raise HTTPException(status_code=502, detail=f"upstream error: {exc}") from exc

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": "plnt playground",
            "docs": "/docs",
            "models": "/v1/models",
        }


async def _sse_stream(
    adapter, req: ChatCompletionRequest, request: Request
) -> AsyncIterator[dict[str, str]]:
    try:
        async for chunk in adapter.stream(req):
            if await request.is_disconnected():
                return
            yield {"data": chunk.model_dump_json()}
        yield {"data": "[DONE]"}
    except Exception as exc:  # noqa: BLE001
        log.exception("stream error for model %s", req.model)
        yield {
            "data": json.dumps(
                {"error": {"message": str(exc), "type": "upstream_error"}}
            )
        }


# Module-level ASGI app so `uvicorn plnt.playground.api:app` works in the
# container image without a factory flag.
app = create_app()
