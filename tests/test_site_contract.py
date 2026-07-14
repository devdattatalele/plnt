"""Pin the wire contract the plnt-site playground UI depends on.

The site's `src/islands/playground/api.ts` is the consumer of record. Any
change to the API's response shape here that breaks those calls means the
playground UI silently degrades to its canned stub replies — a bad failure
mode. These tests are the mechanical guard against that drift.

If the site's api.ts changes, mirror the new expectations in this file.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from plnt.playground.api import create_app
from plnt.playground.discovery import Registry

SITE_MODELS = [
    {"id": "llama-3-70b", "backend": "mock", "runtime": "vllm"},
    {"id": "mistral-7b", "backend": "mock", "runtime": "tgi"},
    {"id": "deepseek-coder-33b", "backend": "mock", "runtime": "sglang"},
    {"id": "qwen2-72b", "backend": "mock", "runtime": "trt-llm"},
]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(registry=Registry(SITE_MODELS)))


# ---------------------------------------------------------------------------
# GET /v1/models — what fetchLiveModels() reads.
# api.ts: data.data must be an array; each item needs `.id` (string) and
# optionally `.owned_by` (string). Extra fields are allowed and used by the
# UI to badge the runtime.
# ---------------------------------------------------------------------------


def test_site_models_shape(client: TestClient) -> None:
    resp = client.get("/v1/models", headers={"accept": "application/json"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["object"] == "list"
    assert isinstance(body["data"], list)
    assert len(body["data"]) == len(SITE_MODELS)

    for item in body["data"]:
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["owned_by"], str)
        assert item["runtime"] in {"mock", "vllm", "tgi", "sglang", "trt-llm"}
        assert item["backend"] in {"mock", "http"}


def test_site_models_cors_preflight(client: TestClient) -> None:
    resp = client.options(
        "/v1/models",
        headers={
            "origin": "http://localhost:4321",
            "access-control-request-method": "GET",
        },
    )
    assert resp.status_code in {200, 204}
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:4321"


def test_site_models_cors_production_origin(client: TestClient) -> None:
    resp = client.options(
        "/v1/models",
        headers={
            "origin": "https://plnt.work",
            "access-control-request-method": "GET",
        },
    )
    assert resp.status_code in {200, 204}
    assert resp.headers.get("access-control-allow-origin") == "https://plnt.work"


# ---------------------------------------------------------------------------
# POST /v1/chat/completions (stream=false) — what sendChat() calls.
# api.ts request body:  {model, messages:[{role,content}], stream:false}
# api.ts response reads: data.choices[0].message.content
# ---------------------------------------------------------------------------


def test_site_chat_non_streaming_shape(client: TestClient) -> None:
    body = {
        "model": "llama-3-70b",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "hello"},
        ],
        "stream": False,
    }
    resp = client.post(
        "/v1/chat/completions",
        json=body,
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["object"] == "chat.completion"
    assert data["model"] == "llama-3-70b"
    assert isinstance(data["choices"], list) and data["choices"]

    choice = data["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert isinstance(choice["message"]["content"], str)
    assert choice["message"]["content"]
    assert choice["finish_reason"] in {"stop", "length", "error"}


def test_site_chat_omits_system_when_content_empty(client: TestClient) -> None:
    """api.ts filters out `system` messages with empty content before sending.
    The API must accept a message list with no system message at all."""
    body = {
        "model": "mistral-7b",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
    }
    resp = client.post("/v1/chat/completions", json=body)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"]


def test_site_chat_unknown_model_returns_4xx(client: TestClient) -> None:
    """api.ts catches non-2xx as a signal to fall back to stub replies."""
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "does-not-exist",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        },
    )
    assert 400 <= resp.status_code < 500


# ---------------------------------------------------------------------------
# POST /v1/chat/completions (stream=true) — not yet used by the site, but
# already contracted. Locking the SSE frame shape so when the site adds
# streaming, this repo doesn't have to catch up.
# ---------------------------------------------------------------------------


def test_streaming_frames_openai_shape(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "qwen2-72b",
            "messages": [{"role": "user", "content": "stream"}],
            "stream": True,
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        frames: list[dict] = []
        saw_done = False
        for raw in resp.iter_lines():
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[len("data:") :].strip()
            if payload == "[DONE]":
                saw_done = True
                break
            frames.append(json.loads(payload))

    assert saw_done, "SSE stream must terminate with `data: [DONE]`"
    assert len(frames) >= 2

    # First frame should carry the assistant role hand-off.
    assert frames[0]["object"] == "chat.completion.chunk"
    assert frames[0]["choices"][0]["delta"].get("role") == "assistant"

    # Interior frames must carry `content` deltas — that's what OpenAI SDKs
    # concatenate to render the incremental reply.
    interior = [f for f in frames[1:-1] if f["choices"][0]["delta"].get("content")]
    assert interior, "at least one content delta expected between role + finish"

    # Last frame must set finish_reason so clients know to stop reading.
    assert frames[-1]["choices"][0].get("finish_reason") in {"stop", "length", "error"}


# ---------------------------------------------------------------------------
# Sanity — the same model ids appear in the site's models.ts hardcoded list
# and the registry we mount here. If someone renames a model on the platform,
# the site's default list should be updated too. This test doesn't enforce
# that (cross-repo), but it names the coupling so the intent is auditable.
# ---------------------------------------------------------------------------


def test_default_model_ids_documented_here() -> None:
    """Documents the four model ids the site's models.ts currently hardcodes.

    If this test starts failing because SITE_MODELS was edited, that's a
    signal to also update `plnt-site/src/islands/playground/models.ts`.
    """
    assert {m["id"] for m in SITE_MODELS} == {
        "llama-3-70b",
        "mistral-7b",
        "deepseek-coder-33b",
        "qwen2-72b",
    }
