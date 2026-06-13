"""plnt — Click-based CLI for the personal twin."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.table import Table

from plnt import __version__
from plnt.config import DEFAULT_SURFACE_HOST, DEFAULT_SURFACE_PORT, paths
from plnt.control.orchestrator import Orchestrator
from plnt.execution.blackboard import Blackboard

console = Console()
_paths = paths()


def _base_url() -> str:
    return f"http://{DEFAULT_SURFACE_HOST}:{DEFAULT_SURFACE_PORT}"


@click.group()
@click.version_option(__version__, prog_name="plnt")
def cli() -> None:
    """Plnt — Personal Local Native Twin."""


# ---------------------------------------------------------------------- server


@cli.command()
@click.option("--host", default=None)
@click.option("--port", default=None, type=int)
def up(host: str | None, port: int | None) -> None:
    """Start the surface server (foreground)."""
    _paths.ensure()
    from plnt.surface.server import run

    console.print(f"[bold green]plnt[/bold green] surface starting on {host or DEFAULT_SURFACE_HOST}:{port or DEFAULT_SURFACE_PORT}")
    console.print(f"home: {_paths.home}")
    run(host=host, port=port)


@cli.command()
def status() -> None:
    """Show surface status."""
    try:
        r = httpx.get(f"{_base_url()}/v1/health", timeout=2)
        console.print_json(r.text)
    except Exception as e:
        console.print(f"[red]surface unreachable:[/red] {e}")


# ---------------------------------------------------------------------- intents


@cli.command()
@click.argument("intent", nargs=-1, required=True)
@click.option("--remote/--local", default=False, help="Submit via HTTP (remote) or run inline (local).")
def submit(intent: tuple[str, ...], remote: bool) -> None:
    """Submit an intent. Default: inline (no server needed)."""
    text = " ".join(intent)
    if remote:
        r = httpx.post(f"{_base_url()}/v1/intents", json={"text": text}, timeout=10)
        r.raise_for_status()
        console.print(r.json())
        return

    _paths.ensure()
    orch = Orchestrator()
    handle = orch.start_run(text)
    console.print(f"[green]run[/green] {handle.run_id}")
    if handle.result and handle.result.output:
        console.print_json(json.dumps(handle.result.output, default=str))
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        out = orch.write_outcome(handle, desktop)
        if out:
            console.print(f"[dim]wrote[/dim] {out}")


# ----------------------------------------------------------------------- views


@cli.command()
def runs() -> None:
    """List recent runs."""
    if not _paths.runs.exists():
        console.print("[dim]no runs yet[/dim]")
        return
    table = Table(title="runs")
    table.add_column("run_id")
    table.add_column("bytes", justify="right")
    table.add_column("mtime")
    for d in sorted(_paths.runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        if not d.is_dir():
            continue
        events = d / "events.jsonl"
        size = events.stat().st_size if events.exists() else 0
        table.add_row(d.name, str(size), str(d.stat().st_mtime))
    console.print(table)


@cli.command()
@click.argument("run_id")
@click.option("--follow/--no-follow", default=False)
def tail(run_id: str, follow: bool) -> None:
    """cat or follow a run's event log."""
    bb = Blackboard(run_id, root=_paths.runs)
    if not bb.events_path.exists():
        console.print(f"[red]unknown run[/red] {run_id}")
        sys.exit(1)
    if not follow:
        for evt in bb.read_all():
            console.print_json(json.dumps(evt, default=str))
        return
    try:
        for evt in bb.tail():
            console.print_json(json.dumps(evt, default=str))
            if evt.get("kind") == "finished":
                break
    except KeyboardInterrupt:
        pass


# ----------------------------------------------------------------------- skills


@cli.group()
def skills() -> None:
    """Skills loaded from $PLNT_HOME/skills/*.md."""


@skills.command("list")
def skills_list() -> None:
    from plnt.control.skills import SkillRegistry

    reg = SkillRegistry(_paths.skills)
    items = reg.list()
    if not items:
        console.print(f"[dim]no skills in {_paths.skills}[/dim]")
        return
    for role in items:
        sk = reg.get(role)
        if sk:
            console.print(f"[bold]{role}[/bold] · tools={sk.tools} hint={sk.model_hint}")


@skills.command("show")
@click.argument("role")
def skills_show(role: str) -> None:
    from plnt.control.skills import SkillRegistry

    sk = SkillRegistry(_paths.skills).get(role)
    if not sk:
        console.print(f"[red]no such skill[/red] {role}")
        sys.exit(1)
    console.rule(f"{role}")
    console.print(sk.prompt)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
