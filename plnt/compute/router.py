"""LLM router — picks small vs. deep model, hides the backend.

Two ports talked over OpenAI-compatible chat completions: small/fast (local
Ollama) and deep (exo cluster, if reachable). When neither is up, the router
falls back to a deterministic *echo planner* so the whole agent loop is
testable offline.

The runner only sees `step(system, user, transcript, tools, model_hint)` and
gets back a `Decision`. It does not know which backend served it.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from plnt.compute.backend_picker import BackendChoice, choose
from plnt.config import DEFAULT_COMPUTE_URL, DEFAULT_DEEP_MODEL, DEFAULT_PLANNER_MODEL


@dataclass
class Decision:
    kind: Literal["tool_call", "final"]
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    text: str = ""
    tokens: int = 0
    latency_ms: int = 0
    backend: str = "unknown"  # "local" | "cloud" | "offline" — audit field


class LLMRouter:
    """OpenAI-compatible chat router. Backend-agnostic.

    The router does NOT pick the backend. It asks `backend_picker.choose()`
    on every step and uses whatever it gets. That makes the SSD-mounted →
    local-Ollama, SSD-missing → cloud-API behaviour automatic and per-call.
    """

    def __init__(
        self,
        small_url: str | None = None,
        deep_url: str | None = None,
        small_model: str | None = None,
        deep_model: str | None = None,
        timeout: float = 60.0,
        force: Literal["auto", "local", "cloud", "offline"] = "auto",
    ):
        # Kept for back-compat; the picker is now the source of truth.
        self.small_url = small_url or os.environ.get("PLNT_SMALL_URL") or DEFAULT_COMPUTE_URL
        self.deep_url = deep_url or os.environ.get("PLNT_DEEP_URL") or self.small_url
        self.small_model = small_model or DEFAULT_PLANNER_MODEL
        self.deep_model = deep_model or DEFAULT_DEEP_MODEL
        self.timeout = timeout
        self.force = force

    # ---------------------------------------------------------------- step

    def step(
        self,
        *,
        system: str,
        user: str,
        transcript: list[dict] | None = None,
        tools: list[str] | None = None,
        model_hint: str = "auto",
        raw: bool = False,
    ) -> Decision:
        """Run one model turn.

        raw=True: don't append the tool/FINAL prompt suffix and don't parse
        the result as a tool call — return the model's text verbatim in
        Decision.text. Used by the planner, which expects raw JSON.
        """
        choice = choose(model_hint=model_hint, force=self.force)  # type: ignore[arg-type]
        if raw:
            messages = self._build_raw_messages(system, user)
        else:
            messages = self._build_messages(system, user, transcript or [], tools or [])

        if choice.kind == "offline":
            if raw:
                return Decision(kind="final", text="", backend="offline")
            d = self._echo_step(system=system, user=user, transcript=transcript or [], tools=tools or [])
            d.backend = "offline"
            return d

        started = time.monotonic()
        try:
            text, tokens = self._call_openai_compat(choice, messages)
        except Exception:
            if raw:
                return Decision(kind="final", text="", backend="offline")
            d = self._echo_step(system=system, user=user, transcript=transcript or [], tools=tools or [])
            d.backend = "offline"
            return d

        latency_ms = int((time.monotonic() - started) * 1000)
        if raw:
            return Decision(kind="final", text=text, tokens=tokens, latency_ms=latency_ms, backend=choice.kind)
        decision = self._parse_decision(text, tools or [])
        decision.tokens = tokens
        decision.latency_ms = latency_ms
        decision.backend = choice.kind
        return decision

    def _build_raw_messages(self, system: str, user: str) -> list[dict]:
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    # ------------------------------------------------------------- backends

    def _call_openai_compat(self, choice: BackendChoice, messages: list[dict]) -> tuple[str, int]:
        endpoint = choice.url.rstrip("/")
        if not endpoint.endswith("/v1/chat/completions"):
            # Ollama native is /api/chat; OpenAI/Groq/Together expose /v1/chat/completions.
            if "/v1" not in endpoint:
                endpoint = endpoint + "/v1/chat/completions"
            else:
                endpoint = endpoint + "/chat/completions"
        payload = {"model": choice.model, "messages": messages, "stream": False}
        headers = {"Content-Type": "application/json"}
        if choice.api_key:
            headers["Authorization"] = f"Bearer {choice.api_key}"
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(endpoint, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens = int(usage.get("total_tokens") or 0)
        return text, tokens

    # ---------------------------------------------------------- prompt I/O

    def _build_messages(
        self, system: str, user: str, transcript: list[dict], tools: list[str]
    ) -> list[dict]:
        suffix = (
            "\n\nYou must respond with one of:\n"
            "  TOOL: {json: {\"tool\": \"search|execute\", \"args\": {...}}}\n"
            "  FINAL: <plain text answer>\n"
            f"Available tools this turn: {tools}\n"
        )
        msgs = [{"role": "system", "content": (system + suffix).strip()}]
        for t in transcript:
            msgs.append(
                {
                    "role": "user",
                    "content": (
                        f"[step {t.get('step')}] called {t.get('tool')} with {t.get('args')}; "
                        f"result: {json.dumps(t.get('result'), default=str)[:1500]}"
                    ),
                }
            )
        msgs.append({"role": "user", "content": user})
        return msgs

    def _parse_decision(self, text: str, tools: list[str]) -> Decision:
        stripped = text.strip()

        # Form 1: TOOL: {...}    — single line or multi-line JSON blob.
        # Form 2: TOOL: name\n{...}   — common with smaller local models.
        # Form 3: TOOL: name(args)    — even smaller models do this.
        m = re.search(r"TOOL:\s*(\w+)?\s*(\{.*\})", stripped, re.DOTALL)
        if m:
            blob_text = m.group(2)
            # Trim trailing prose after the JSON ends.
            depth = 0
            end = -1
            for i, ch in enumerate(blob_text):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > 0:
                blob_text = blob_text[:end]
            try:
                blob = json.loads(blob_text)
                # Some models emit {"tool":"x","args":{...}}; others put the
                # tool name in the TOOL: header. Accept either.
                tool = str(blob.get("tool") or (m.group(1) or "")).strip()
                args = blob.get("args") if "args" in blob else {k: v for k, v in blob.items() if k != "tool"}
                if tool in tools:
                    return Decision(kind="tool_call", tool_name=tool, tool_args=args or {}, text=stripped)
            except (json.JSONDecodeError, TypeError):
                pass

        if "FINAL:" in stripped:
            tail = stripped.split("FINAL:", 1)[1].strip()
            return Decision(kind="final", text=tail)
        return Decision(kind="final", text=stripped)

    # --------------------------------------------------------- offline path

    def _echo_step(
        self, *, system: str, user: str, transcript: list[dict], tools: list[str]
    ) -> Decision:
        """No backend reachable — give the runner a deterministic answer.

        Step 1 (no transcript): call search() with a token derived from the
        user message. Step 2+: return a FINAL summary of what was found.
        This keeps tests green and demos functional during dev.
        """
        if not transcript and "search" in tools:
            token = self._keyword(user)
            import os
            root = "."
            roots_env = os.environ.get("PLNT_SEARCH_ROOTS", "")
            if roots_env:
                first = roots_env.split(":")[0].strip()
                if first:
                    root = first
            return Decision(
                kind="tool_call",
                tool_name="search",
                tool_args={"pattern": token, "root": root},
                tokens=0,
                latency_ms=0,
            )
        # Summarise prior tool results into a FINAL.
        last = transcript[-1] if transcript else {}
        summary = (
            f"echo-planner: intent={user[:120]!r}; tools_called={len(transcript)}; "
            f"last_tool={last.get('tool')}; last_result_keys="
            f"{list(last.get('result', {}).keys()) if isinstance(last.get('result'), dict) else 'list'}"
        )
        return Decision(kind="final", text=summary, tokens=0, latency_ms=0)

    @staticmethod
    def _keyword(user: str) -> str:
        # Cheap noun-ish extraction: longest alphanumeric token over 3 chars.
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", user)
        if not words:
            return "."
        return max(words, key=len)
