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
from plnt.playground.cli import playground_group as _playground_group

console = Console()
_paths = paths()


def _base_url() -> str:
    return f"http://{DEFAULT_SURFACE_HOST}:{DEFAULT_SURFACE_PORT}"


@click.group()
@click.version_option(__version__, prog_name="plnt")
def cli() -> None:
    """plnt — multi-model inference playground on Kubernetes.

    Subcommands split by plane:

    \b
      plnt playground …   the OpenAI-compat inference gateway (this repo's live surface)
      plnt deploy …       apply an InferenceModel manifest (kubectl wrapper)
      plnt up             start the personal-runtime surface server (origin story)
      plnt intent …       (personal runtime) send an intent to the resident planner
    """


# --------------------------------------------------------------- playground group

cli.add_command(_playground_group)


# --------------------------------------------------------------- deploy command


@cli.command()
@click.argument("name")
@click.option(
    "--runtime",
    type=click.Choice(["vllm", "tgi", "sglang", "trt-llm"]),
    default="vllm",
    show_default=True,
)
@click.option(
    "--model",
    "model_ref",
    required=True,
    help="HF-style model ref, e.g. meta-llama/Llama-3-8B-Instruct.",
)
@click.option("--gpu", default=1, type=int, show_default=True)
@click.option("--replicas", default=1, type=int, show_default=True)
@click.option(
    "--apply/--print",
    "do_apply",
    default=False,
    help="Run `kubectl apply -f -` after rendering. Without --apply, print YAML to stdout.",
)
def deploy(
    name: str,
    runtime: str,
    model_ref: str,
    gpu: int,
    replicas: int,
    do_apply: bool,
) -> None:
    """Render (and optionally apply) an InferenceModel resource.

    Note: the InferenceModel CRD + operator land in HANDOFF Phase 3. Until
    then this command produces the manifest that Phase 3 will consume, so
    you can inspect what a `plnt deploy` will look like end-to-end.
    """
    import shutil
    import subprocess

    manifest = f"""apiVersion: plnt.work/v1
kind: InferenceModel
metadata:
  name: {name}
  labels:
    app.kubernetes.io/part-of: plnt
spec:
  runtime: {runtime}
  model: {model_ref}
  resources:
    gpu: {gpu}
  replicas:
    min: {replicas}
    max: {max(replicas, replicas * 3)}
"""
    if not do_apply:
        click.echo(manifest, nl=False)
        return

    if shutil.which("kubectl") is None:
        console.print("[red]kubectl not found on PATH[/red]")
        sys.exit(1)

    console.print(
        "[yellow]note:[/yellow] applying InferenceModel manifest — cluster must "
        "have the CRD installed (Phase 3, not shipped yet)."
    )
    proc = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=manifest.encode(),
        check=False,
    )
    sys.exit(proc.returncode)


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


# ----------------------------------------------------------------------- monitor


@cli.command()
@click.option("--remote/--local", default=False, help="Read /v1/system over HTTP, or compute locally.")
def monitor(remote: bool) -> None:
    """Snapshot live agents + sandbox rungs + recent runs."""
    if remote:
        try:
            r = httpx.get(f"{_base_url()}/v1/system", timeout=4)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            console.print(f"[red]surface unreachable:[/red] {e}")
            sys.exit(1)
    else:
        from plnt.surface.monitor import snapshot

        data = snapshot()

    console.print(f"[bold]sandbox rungs:[/bold] {', '.join(data.get('sandbox_rungs', []))}")
    console.print(f"[bold]cpu count:[/bold] {data.get('cpu_count')}")
    docker_agents = data.get("docker_agents", [])
    console.print(f"[bold]docker agents:[/bold] {len(docker_agents)} live")
    if docker_agents:
        table = Table(title="live agents")
        for col in ("id", "name", "image", "status"):
            table.add_column(col)
        for a in docker_agents:
            table.add_row(a["id"][:12], a["name"], a["image"], a["status"])
        console.print(table)
        stats = data.get("docker_stats", [])
        if stats:
            t2 = Table(title="resources")
            for col in ("id", "cpu", "mem", "mem_pct"):
                t2.add_column(col)
            for s in stats:
                t2.add_row(s["id"][:12], s["cpu"], s["mem"], s["mem_pct"])
            console.print(t2)
    runs = data.get("runs_recent", [])[:5]
    if runs:
        t3 = Table(title="recent runs")
        for col in ("run_id", "event_bytes"):
            t3.add_column(col)
        for r in runs:
            t3.add_row(r["run_id"], str(r["event_bytes"]))
        console.print(t3)


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


# ----------------------------------------------------------------------- auth


@cli.group()
def auth() -> None:
    """Web UI authentication — username/password store."""


@auth.command("set-password")
@click.option("--user", "username", default="admin", help="Username to set.")
@click.password_option("--password", prompt=True, confirmation_prompt=True)
def auth_set_password(username: str, password: str) -> None:
    from plnt.surface.auth import AuthStore

    _paths.ensure()
    store = AuthStore()
    store.set_password(username, password)
    console.print(f"[green]OK[/green] password set for {username!r} at {store.path}")


@auth.command("list-users")
def auth_list_users() -> None:
    from plnt.surface.auth import AuthStore

    store = AuthStore()
    users = store.list_users()
    if not users:
        console.print(f"[dim]no users in {store.path}[/dim]")
        return
    for u in users:
        console.print(u)


# ----------------------------------------------------------------------- vendor


@cli.command("vendor-chat")
@click.option(
    "--source", default=None,
    help="Path to plnt-site/dist/app/. Defaults to ../plnt-site/dist/app relative to this repo.",
)
def vendor_chat(source: str | None) -> None:
    """Copy a built chat bundle into plnt/surface/static/app/."""
    import shutil

    repo_root = Path(__file__).resolve().parent.parent
    src = Path(source).expanduser() if source else (repo_root.parent / "plnt-site" / "dist-app")
    src = src.resolve()
    if not src.exists():
        console.print(f"[red]source not found:[/red] {src}")
        console.print(f"[dim]hint: cd {src.parent.parent} && npm run build:app[/dim]")
        sys.exit(1)
    dst = repo_root / "plnt" / "surface" / "static" / "app"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    console.print(f"[green]OK[/green] vendored {src} -> {dst}")


@skills.command("install")
@click.argument("source")
@click.option("--dry-run", is_flag=True, help="List what would be imported without writing.")
def skills_install(source: str, dry_run: bool) -> None:
    """Install skills from a public library.

    SOURCE can be a shorthand or a full git URL. Shorthands:
      anthropic           Anthropic's official skills repo
      addyosmani          addyosmani/agent-skills (24 engineering skills)
      scientific          K-Dense-AI/scientific-agent-skills (140 skills)
      antigravity         sickn33/antigravity-awesome-skills (1500+ skills)
      claude-skills-collection
      the-library

    Or pass a full URL: https://github.com/owner/repo.git
    """
    from plnt.control.skill_installer import KNOWN_SOURCES, InstallError, install

    if source == "list":
        console.print("[bold]Known shorthands:[/bold]")
        for k, v in KNOWN_SOURCES.items():
            console.print(f"  {k:30} {Subtle.render(v) if hasattr(Subtle, 'render') else v}")
        return

    try:
        result = install(source, dry_run=dry_run)
    except InstallError as e:
        console.print(f"[red]install failed:[/red] {e}")
        sys.exit(1)

    console.print(f"[green]OK[/green] imported {result['imported']} skills from {result['source']}")
    if result['skipped']:
        console.print(f"[yellow]skipped[/yellow] {result['skipped']} (already exist or malformed)")
    if result['skills']:
        for role in result['skills'][:20]:
            console.print(f"  · {role}")
        if len(result['skills']) > 20:
            console.print(f"  · ... and {len(result['skills']) - 20} more")
    console.print(f"[dim]installed to {result['target']}[/dim]")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
