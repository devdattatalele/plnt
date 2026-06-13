"""Surface server — task panels, not chat.

Three primitives:
  POST /v1/intents              — submit_intent (returns run_id)
  GET  /v1/runs/{run_id}        — run snapshot (events + result)
  GET  /v1/runs/{run_id}/stream — SSE tail of the run's event stream
  GET  /v1/runs                 — list runs
  GET  /v1/skills               — list installed skills
  GET  /v1/health               — liveness

This is deliberately tiny. Chat is *not* a primitive here. The user files an
intent; the system materialises a swarm; results land in markdown.

mTLS: stub for v0 — bind to 127.0.0.1 by default and document the cert flow
in DEPLOY.md. v0.1 will enforce client certs via uvicorn ssl_keyfile/ssl_certfile.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from plnt import __version__
from plnt.config import DEFAULT_SURFACE_HOST, DEFAULT_SURFACE_PORT, paths
from plnt.control.orchestrator import Orchestrator
from plnt.execution.blackboard import Blackboard

app = FastAPI(title="Plnt Surface", version=__version__)
_paths = paths()
_paths.ensure()
_orchestrator = Orchestrator()


class SubmitIntent(BaseModel):
    text: str


class SubmitResult(BaseModel):
    run_id: str


@app.get("/v1/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "home": str(_paths.home)}


@app.get("/v1/skills")
def list_skills() -> dict:
    return {"skills": _orchestrator.skills.list()}


@app.get("/v1/runs")
def list_runs() -> dict:
    if not _paths.runs.exists():
        return {"runs": []}
    items = []
    for d in sorted(_paths.runs.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        events = d / "events.jsonl"
        items.append(
            {
                "run_id": d.name,
                "modified": d.stat().st_mtime,
                "event_bytes": events.stat().st_size if events.exists() else 0,
            }
        )
    return {"runs": items}


@app.get("/v1/runs/{run_id}")
def get_run(run_id: str) -> dict:
    bb = Blackboard(run_id, root=_paths.runs)
    if not bb.events_path.exists():
        raise HTTPException(404, f"unknown run {run_id}")
    events = bb.read_all()
    result_evts = [e for e in events if e.get("kind") == "result"]
    return {
        "run_id": run_id,
        "events_count": len(events),
        "result": result_evts[-1]["payload"] if result_evts else None,
        "events": events[-100:],
    }


@app.post("/v1/intents", response_model=SubmitResult)
def submit_intent(req: SubmitIntent) -> SubmitResult:
    if not req.text.strip():
        raise HTTPException(400, "empty intent")
    handle = _orchestrator.start_run(req.text)
    # write a default outcome to ~/Desktop if it exists
    desktop = Path.home() / "Desktop"
    if desktop.exists():
        _orchestrator.write_outcome(handle, desktop)
    return SubmitResult(run_id=handle.run_id)


@app.get("/v1/runs/{run_id}/stream")
async def stream_run(run_id: str):
    bb = Blackboard(run_id, root=_paths.runs)
    if not bb.events_path.exists():
        raise HTTPException(404, f"unknown run {run_id}")

    async def gen():
        import json

        offset = 0
        done = False
        # Run at most ~5 minutes of streaming per HTTP request.
        deadline = asyncio.get_event_loop().time() + 300
        while not done and asyncio.get_event_loop().time() < deadline:
            new = []
            with open(bb.events_path, encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except Exception:
                        continue
                    new.append(evt)
                offset = f.tell()
            for evt in new:
                yield {"data": json.dumps(evt, default=str), "event": evt.get("kind", "log")}
                if evt.get("kind") == "finished":
                    done = True
                    break
            await asyncio.sleep(0.2)

    return EventSourceResponse(gen())


def run(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=host or DEFAULT_SURFACE_HOST,
        port=port or DEFAULT_SURFACE_PORT,
        log_level=os.environ.get("PLNT_LOG_LEVEL", "info"),
    )
