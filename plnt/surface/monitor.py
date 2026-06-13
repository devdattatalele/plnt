"""Monitoring snapshot — what the dashboard / `plnt monitor` reads.

Returns a JSON-able dict describing live agents + host resource pressure.
Cheap to call (no LLM); safe to expose at /v1/system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from plnt.config import paths
from plnt.execution.sandbox import available_rungs


def _docker_agents() -> list[dict[str, Any]]:
    if shutil.which("docker") is None:
        return []
    try:
        out = subprocess.run(
            [
                "docker", "ps",
                "--filter", "label=dev.plnt.agent=true",
                "--format", "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}",
            ],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except subprocess.SubprocessError:
        return []
    agents = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        agents.append({"id": parts[0], "image": parts[1], "status": parts[2], "name": parts[3]})
    return agents


def _docker_stats(ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.ID}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}", *ids],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except subprocess.SubprocessError:
        return []
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        rows.append({"id": parts[0], "cpu": parts[1], "mem": parts[2], "mem_pct": parts[3]})
    return rows


def _runs_summary(limit: int = 20) -> list[dict[str, Any]]:
    runs_dir = paths().runs
    if not runs_dir.exists():
        return []
    items = []
    for d in sorted(runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        if not d.is_dir():
            continue
        events = d / "events.jsonl"
        items.append({
            "run_id": d.name,
            "modified": d.stat().st_mtime,
            "event_bytes": events.stat().st_size if events.exists() else 0,
        })
    return items


def snapshot() -> dict[str, Any]:
    docker_agents = _docker_agents()
    return {
        "sandbox_rungs": available_rungs(),
        "docker_agents": docker_agents,
        "docker_stats": _docker_stats([a["id"] for a in docker_agents]),
        "cpu_count": os.cpu_count(),
        "runs_recent": _runs_summary(),
    }
