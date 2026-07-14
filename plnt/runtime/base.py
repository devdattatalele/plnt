"""RuntimeAdapter — one Protocol, four backends.

Concrete implementations of this Protocol live in
`plnt/playground/backends.py`:

* `MockBackend` — deterministic echo, useful on kind (no GPU) and in tests.
* `HTTPBackend` — proxies to any upstream that speaks OpenAI /v1/chat/completions
 (vLLM, TGI, SGLang, and TRT-LLM/Triton with the OpenAI compat layer all do).

The chart per runtime is the deployment shape; this Protocol is the code shape.
Adding a fifth backend is one class here, not a fork.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from plnt.playground.schemas import (
 ChatCompletionChunk,
 ChatCompletionRequest,
 ChatCompletionResponse,
)


class RuntimeAdapter(Protocol):
 model_id: str
 runtime: str

 async def complete(self, req: ChatCompletionRequest) -> ChatCompletionResponse: ...

 def stream(self, req: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]: ...
