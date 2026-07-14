"""Tests for the playground API — mock backend, no network, no k8s."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from plnt.playground.api import create_app
from plnt.playground.discovery import Registry


@pytest.fixture()
def client() -> TestClient:
    registry = Registry(
        [
            {"id": "plnt-mock-7b", "backend": "mock", "runtime": "mock"},
            {"id": "plnt-mock-70b", "backend": "mock", "runtime": "mock"},
        ]
    )
    return TestClient(create_app(registry=registry))


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_with_models(client: TestClient) -> None:
    resp = client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["models"] == 2


def test_readyz_without_models() -> None:
    empty = TestClient(create_app(registry=Registry([])))
    resp = empty.get("/readyz")
    assert resp.status_code == 503


def test_list_models(client: TestClient) -> None:
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    ids = {m["id"] for m in body["data"]}
    assert ids == {"plnt-mock-7b", "plnt-mock-70b"}
    for card in body["data"]:
        assert card["backend"] == "mock"
        assert card["owned_by"] == "plnt"


def test_chat_completion_non_stream(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "plnt-mock-7b",
            "messages": [{"role": "user", "content": "hello world"}],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "plnt-mock-7b"
    assert len(body["choices"]) == 1
    choice = body["choices"][0]
    assert choice["message"]["role"] == "assistant"
    assert "hello world" in choice["message"]["content"]
    assert choice["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] > 0


def test_chat_completion_unknown_model(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={
            "model": "does-not-exist",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 404


def test_chat_completion_streaming(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "plnt-mock-7b",
            "messages": [{"role": "user", "content": "stream please"}],
            "stream": True,
        },
    ) as resp:
        assert resp.status_code == 200
        chunks: list[dict] = []
        saw_done = False
        for raw in resp.iter_lines():
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[len("data:") :].strip()
            if payload == "[DONE]":
                saw_done = True
                break
            chunks.append(json.loads(payload))

    assert saw_done
    assert len(chunks) >= 3
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    joined = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks)
    assert "stream please" in joined
    assert chunks[-1]["choices"][0].get("finish_reason") == "stop"
