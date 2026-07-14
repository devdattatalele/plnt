"""`plnt playground` subcommands.

These are the developer-facing operations on the playground service —
starting it locally, listing models on any target, sending a chat prompt
from the terminal, and printing copy-paste curl examples.

Everything here talks to the API over HTTP. The default target is the
locally-served instance (`http://127.0.0.1:8080`), overridable with
`--endpoint` on any command or via `PLNT_PLAYGROUND_ENDPOINT` in the env.
"""

from __future__ import annotations

import json
import os
import sys

import click
import httpx
from rich.console import Console
from rich.table import Table

console = Console()

DEFAULT_ENDPOINT = "http://127.0.0.1:8080"


def _endpoint(explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("PLNT_PLAYGROUND_ENDPOINT")
    if env:
        return env.rstrip("/")
    return DEFAULT_ENDPOINT


@click.group(name="playground")
def playground_group() -> None:
    """Playground API — OpenAI-shape inference gateway."""


# ---------------------------------------------------------------------------
# plnt playground up
# ---------------------------------------------------------------------------


@playground_group.command("up")
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8080, type=int, show_default=True)
@click.option(
    "--reload/--no-reload",
    default=False,
    help="Uvicorn auto-reload on source changes. Handy for local dev.",
)
def up(host: str, port: int, reload: bool) -> None:
    """Start the playground API locally (foreground, uvicorn)."""
    import uvicorn

    console.print(
        f"[bold green]plnt playground[/bold green] on http://{host}:{port} — "
        "mock backend, ctrl+C to stop."
    )
    console.print(f"  models:  http://{host}:{port}/v1/models")
    console.print(f"  chat:    POST http://{host}:{port}/v1/chat/completions")
    console.print(f"  docs:    http://{host}:{port}/docs")
    uvicorn.run(
        "plnt.playground.api:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


# ---------------------------------------------------------------------------
# plnt playground models
# ---------------------------------------------------------------------------


@playground_group.command("models")
@click.option("--endpoint", "-e", default=None, help="API base URL.")
def models(endpoint: str | None) -> None:
    """List registered models on the target playground."""
    target = _endpoint(endpoint)
    try:
        resp = httpx.get(f"{target}/v1/models", timeout=5)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        console.print(f"[red]request failed[/red] to {target}: {exc}")
        sys.exit(1)

    data = resp.json().get("data", [])
    if not data:
        console.print(f"[yellow]no models registered on {target}[/yellow]")
        return

    table = Table(title=f"models on {target}", header_style="bold cyan")
    table.add_column("id")
    table.add_column("backend")
    table.add_column("runtime")
    table.add_column("owned_by", style="dim")
    for m in data:
        table.add_row(
            m.get("id", ""),
            m.get("backend", ""),
            m.get("runtime", ""),
            m.get("owned_by", ""),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# plnt playground chat MODEL PROMPT...
# ---------------------------------------------------------------------------


@playground_group.command("chat")
@click.argument("model")
@click.argument("prompt", nargs=-1, required=True)
@click.option("--endpoint", "-e", default=None, help="API base URL.")
@click.option("--system", "-s", default=None, help="Optional system prompt.")
@click.option(
    "--stream/--no-stream", default=True, help="Server-sent events streaming."
)
@click.option("--max-tokens", default=256, type=int, show_default=True)
@click.option("--temperature", default=0.7, type=float, show_default=True)
def chat(
    model: str,
    prompt: tuple[str, ...],
    endpoint: str | None,
    system: str | None,
    stream: bool,
    max_tokens: int,
    temperature: float,
) -> None:
    """Send a single-turn chat completion. Streams to stdout by default."""
    target = _endpoint(endpoint)
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": " ".join(prompt)})

    body = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    if not stream:
        try:
            resp = httpx.post(
                f"{target}/v1/chat/completions", json=body, timeout=60
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            console.print(f"[red]request failed[/red]: {exc}")
            sys.exit(1)
        text = resp.json()["choices"][0]["message"]["content"]
        click.echo(text)
        return

    try:
        with httpx.stream(
            "POST",
            f"{target}/v1/chat/completions",
            json=body,
            timeout=60,
            headers={"accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    click.echo()
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = chunk["choices"][0].get("delta", {}).get("content")
                if delta:
                    click.echo(delta, nl=False)
    except httpx.HTTPError as exc:
        console.print(f"\n[red]stream failed[/red]: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# plnt playground curl
# ---------------------------------------------------------------------------


@playground_group.command("curl")
@click.option("--endpoint", "-e", default=None, help="API base URL to embed.")
def curl(endpoint: str | None) -> None:
    """Print copy-paste curl examples for the current endpoint."""
    target = _endpoint(endpoint)
    examples = f"""# List models
curl -s {target}/v1/models | jq

# Non-streaming chat
curl -s {target}/v1/chat/completions \\
  -H 'content-type: application/json' \\
  -d '{{"model":"plnt-mock-7b","messages":[{{"role":"user","content":"hello"}}]}}' \\
  | jq

# Streaming chat (SSE)
curl -sN {target}/v1/chat/completions \\
  -H 'content-type: application/json' \\
  -d '{{"model":"plnt-mock-7b","messages":[{{"role":"user","content":"stream"}}],"stream":true}}'

# Liveness / readiness
curl -s {target}/healthz
curl -s {target}/readyz
"""
    click.echo(examples)
