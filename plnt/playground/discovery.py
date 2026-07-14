"""Model registry for the playground API.

Sources, in order of precedence:

1. `PLNT_PLAYGROUND_MODELS` env var — a JSON array of model specs. This is what
   the Helm chart injects via ConfigMap -> env.
2. `PLNT_PLAYGROUND_CONFIG` env var — path to a JSON/YAML file containing the
   same array. Useful for local dev.
3. Built-in default — a single mock model so `helm install` with no values still
   returns something usable.

A model spec looks like:

    {
      "id": "llama-3-8b",
      "backend": "http",         # "mock" or "http"
      "runtime": "vllm",         # informational
      "upstream_url": "http://llama-3-8b.plnt.svc.cluster.local:8000",
      "upstream_model": "meta-llama/Llama-3-8B-Instruct",   # optional
      "api_key_env": "VLLM_API_KEY"                          # optional
    }
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from plnt.playground.backends import HTTPBackend, MockBackend, RuntimeAdapter
from plnt.playground.schemas import ModelCard

DEFAULT_MODELS: list[dict] = [
    {
        "id": "plnt-mock-7b",
        "backend": "mock",
        "runtime": "mock",
    }
]


class Registry:
    def __init__(self, specs: list[dict]) -> None:
        self._specs = {s["id"]: s for s in specs}
        self._adapters: dict[str, RuntimeAdapter] = {}
        self._created_at = int(time.time())
        for spec in specs:
            self._adapters[spec["id"]] = _build_adapter(spec)

    @property
    def ids(self) -> list[str]:
        return list(self._specs.keys())

    def cards(self) -> list[ModelCard]:
        return [
            ModelCard(
                id=spec["id"],
                created=self._created_at,
                runtime=spec.get("runtime", "unknown"),
                backend=spec["backend"],
            )
            for spec in self._specs.values()
        ]

    def get(self, model_id: str) -> RuntimeAdapter | None:
        return self._adapters.get(model_id)


def _build_adapter(spec: dict) -> RuntimeAdapter:
    backend = spec["backend"]
    if backend == "mock":
        return MockBackend(model_id=spec["id"], runtime=spec.get("runtime", "mock"))
    if backend == "http":
        api_key = None
        if key_env := spec.get("api_key_env"):
            api_key = os.environ.get(key_env)
        return HTTPBackend(
            model_id=spec["id"],
            upstream_url=spec["upstream_url"],
            runtime=spec.get("runtime", "vllm"),
            upstream_model=spec.get("upstream_model"),
            api_key=api_key,
            timeout_seconds=float(spec.get("timeout_seconds", 60.0)),
        )
    raise ValueError(f"unknown backend: {backend!r}")


def load_registry() -> Registry:
    raw = os.environ.get("PLNT_PLAYGROUND_MODELS")
    if raw:
        return Registry(_parse_json(raw))

    if path := os.environ.get("PLNT_PLAYGROUND_CONFIG"):
        text = Path(path).read_text()
        return Registry(_parse_json(text))

    return Registry(DEFAULT_MODELS)


def _parse_json(text: str) -> list[dict]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("playground model config must be a JSON array")
    return data
