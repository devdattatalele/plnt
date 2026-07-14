"""Surface server — task panels + web chat.

API primitives:
  POST /v1/intents              — submit_intent (returns run_id)
  GET  /v1/runs                 — list runs
  GET  /v1/runs/{run_id}        — run snapshot (events + result)
  GET  /v1/runs/{run_id}/stream — SSE tail of the run's event stream
  GET  /v1/skills               — list installed skill roles
  GET  /v1/skills/{role}        — manifest + prompt.md for one skill
  GET  /v1/integrations         — saved per-skill input map
  PUT  /v1/integrations/{role}  — replace saved inputs for a skill
  GET  /v1/health               — liveness (unauthenticated)

Auth:
  POST /v1/auth/login           — body {username, password} → sets cookie
  POST /v1/auth/logout          — clears cookie
  GET  /v1/auth/me              — current session info

Web chat: built bundle is served as a static SPA at /app/* (same-origin to the
API so cookies just work). Set PLNT_LOCALHOST_TRUST=1 to skip auth on loopback
requests; default is off — even local users go through the login flow.

mTLS: stub for v0 — bind to 127.0.0.1 by default and document the cert flow
in DEPLOY.md. v0.1 will enforce client certs via uvicorn ssl_keyfile/ssl_certfile.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from plnt import __version__
from plnt.config import DEFAULT_SURFACE_HOST, DEFAULT_SURFACE_PORT, paths
from plnt.control.orchestrator import Orchestrator
from plnt.execution.blackboard import Blackboard
from plnt.surface.auth import (
    SESSION_TTL_SECONDS,
    AuthStore,
    Sessions,
)
from plnt.surface.integrations import IntegrationsStore

app = FastAPI(title="Plnt Surface", version=__version__)
_paths = paths()
_paths.ensure()
_orchestrator = Orchestrator()
_auth_store = AuthStore()
_sessions = Sessions()
_integrations = IntegrationsStore()

_SESSION_COOKIE = "plnt_session"
_STATIC_DIR = Path(__file__).parent / "static"
_LOOPBACK_TRUST = os.environ.get("PLNT_LOCALHOST_TRUST", "0") == "1"

# CORS — same-origin works without this. Configure when fronted by Vercel /
# Cloudflare tunnel. Comma-separated origin list in PLNT_CORS_ALLOW.
_extra_origins = [o.strip() for o in os.environ.get("PLNT_CORS_ALLOW", "").split(",") if o.strip()]
_base_origins = [f"http://{DEFAULT_SURFACE_HOST}:{DEFAULT_SURFACE_PORT}"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_base_origins + _extra_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- models


class PriorTurn(BaseModel):
    prompt: str = ""
    answer: str = ""


class SubmitIntent(BaseModel):
    text: str
    history: list[PriorTurn] = []  # conversation memory; oldest first


class SubmitResult(BaseModel):
    run_id: str


class LoginRequest(BaseModel):
    username: str
    password: str


class IntegrationUpdate(BaseModel):
    values: dict


# ---------------------------------------------------------------- auth dep


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


def require_session(request: Request) -> str:
    """Returns the authenticated username, or raises 401."""
    if _LOOPBACK_TRUST and _is_loopback(request):
        return "localhost"
    token = request.cookies.get(_SESSION_COOKIE, "")
    sess = _sessions.get(token)
    if sess is None:
        raise HTTPException(401, "unauthenticated")
    return sess.username


# ---------------------------------------------------------------- auth routes


@app.post("/v1/auth/login")
def login(req: LoginRequest, response: Response) -> dict:
    # Bootstrap on first call if the store is empty so users aren't locked out.
    bootstrap = _auth_store.bootstrap_if_empty()
    if bootstrap:
        # Initial credentials were just generated; reject this login attempt
        # so the operator notices and re-tries with the freshly printed creds.
        bu, bp = bootstrap
        print(f"[plnt auth] bootstrapped user {bu!r} with password: {bp}")
        raise HTTPException(401, f"no users existed; created {bu!r} — check server stdout for password")

    if not _auth_store.verify(req.username, req.password):
        raise HTTPException(401, "invalid credentials")

    sess = _sessions.create(req.username)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=sess.token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=False,  # loopback HTTP; flip to True when fronted by HTTPS
        path="/",
    )
    return {"ok": True, "username": req.username}


@app.post("/v1/auth/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(_SESSION_COOKIE, "")
    if token:
        _sessions.destroy(token)
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/v1/auth/me")
def auth_me(request: Request) -> dict:
    if _LOOPBACK_TRUST and _is_loopback(request):
        return {"authenticated": True, "username": "localhost", "loopback_trust": True}
    token = request.cookies.get(_SESSION_COOKIE, "")
    sess = _sessions.get(token)
    if sess is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": sess.username}


# ---------------------------------------------------------------- public


@app.get("/v1/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "home": str(_paths.home)}


# ---------------------------------------------------------------- runs


@app.get("/v1/system")
def system_snapshot(_: str = Depends(require_session)) -> dict:
    """Live host snapshot: sandbox rungs, docker stats, recent runs."""
    from plnt.surface.monitor import snapshot

    return snapshot()


@app.get("/v1/skills")
def list_skills(_: str = Depends(require_session)) -> dict:
    return {"skills": _orchestrator.skills.list()}


@app.get("/v1/skills/{role}")
def get_skill(role: str, _: str = Depends(require_session)) -> dict:
    sk = _orchestrator.skills.get(role)
    if sk is None:
        raise HTTPException(404, f"unknown skill {role}")

    manifest = None
    if sk.manifest is not None:
        manifest = sk.manifest.model_dump(mode="json", exclude_none=True)

    prompt_md = ""
    examples_md: str | None = None
    if sk.source_path is not None:
        if sk.source_path.name == "skill.toml":
            pmd = sk.source_path.parent / "prompt.md"
            emd = sk.source_path.parent / "examples.md"
            if pmd.exists():
                prompt_md = pmd.read_text(encoding="utf-8")
            if emd.exists():
                examples_md = emd.read_text(encoding="utf-8")
        else:
            prompt_md = sk.prompt
    else:
        prompt_md = sk.prompt

    return {
        "role": role,
        "tools": sk.tools,
        "model_hint": sk.model_hint,
        "budget": sk.budget,
        "manifest": manifest,
        "prompt_md": prompt_md,
        "examples_md": examples_md,
    }


@app.get("/v1/runs")
def list_runs(_: str = Depends(require_session)) -> dict:
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
def get_run(run_id: str, _: str = Depends(require_session)) -> dict:
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
def submit_intent(req: SubmitIntent, _: str = Depends(require_session)) -> SubmitResult:
    """Spawn the swarm in a background thread and return the run_id immediately.

    The client subscribes to /v1/runs/{id}/stream and watches the work happen.
    """
    if not req.text.strip():
        raise HTTPException(400, "empty intent")

    import threading
    import uuid

    run_id = f"r-{uuid.uuid4().hex[:10]}"
    bb = Blackboard(run_id, root=_paths.runs)  # touches events.jsonl so SSE can subscribe

    history = [{"prompt": t.prompt, "answer": t.answer} for t in req.history]

    def _run_swarm():
        try:
            handle = _orchestrator.start_swarm_with_id(
                req.text, run_id, blackboard=bb, history=history,
            )
            desktop = Path.home() / "Desktop"
            if desktop.exists():
                _orchestrator.write_outcome(handle, desktop)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            bb.emit("error", payload={"reason": f"swarm crashed: {e}", "traceback": tb})
            # Without an `answer` event, the chat UI has nothing to render
            # for this turn. Surface the failure as the assistant's reply.
            bb.emit("answer", payload={
                "text": f"This run crashed before the planner could spawn anything.\n\n`{e}`\n\nThis is usually a path-resolution bug in the orchestrator. Try a more specific prompt, or check the server logs.",
                "source": "error",
            })
            bb.emit("finished", payload={"spawned": 0, "completed": 0, "killed": 0})

    threading.Thread(target=_run_swarm, daemon=True, name=f"swarm-{run_id}").start()
    return SubmitResult(run_id=run_id)


@app.get("/v1/runs/{run_id}/stream")
async def stream_run(run_id: str, request: Request):
    # SSE auth — EventSource can't set headers, but cookies travel automatically.
    require_session(request)

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
                # End the stream ONLY on the run-level finished event
                # (the one without an agent_id). Per-agent finished events
                # come first and would otherwise terminate the stream
                # before the synth answer event arrives.
                if evt.get("kind") == "finished" and not evt.get("agent_id"):
                    done = True
                    break
            await asyncio.sleep(0.2)

    return EventSourceResponse(gen())


# ---------------------------------------------------------------- integrations


@app.get("/v1/integrations")
def get_integrations(_: str = Depends(require_session)) -> dict:
    return {"integrations": _integrations.get_all()}


@app.put("/v1/integrations/{role}")
def set_integration(role: str, body: IntegrationUpdate, _: str = Depends(require_session)) -> dict:
    if _orchestrator.skills.get(role) is None:
        raise HTTPException(404, f"unknown skill {role}")
    _integrations.set(role, body.values)
    return {"ok": True, "role": role, "values": _integrations.get(role)}


# ---------------------------------------------------------------- static SPA

# Mount the chat bundle if it's been vendored in. Done last so route handlers
# above take precedence over the catch-all. `html=True` makes /app/ serve
# index.html and /app/foo (no extension) fall back to it for SPA routing.
_app_dir = _STATIC_DIR / "app"
if _app_dir.exists():
    app.mount("/app", StaticFiles(directory=_app_dir, html=True), name="app")


# ---------------------------------------------------------------- entry


def run(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    # Print bootstrap credentials once if this is the first run on the box.
    bootstrap = _auth_store.bootstrap_if_empty()
    if bootstrap:
        bu, bp = bootstrap
        print()
        print("=" * 60)
        print(f"plnt auth bootstrap — user: {bu}   password: {bp}")
        print(f"(saved to {_auth_store.path}; change with `plnt auth set-password`)")
        print("=" * 60)
        print()

    uvicorn.run(
        app,
        host=host or DEFAULT_SURFACE_HOST,
        port=port or DEFAULT_SURFACE_PORT,
        log_level=os.environ.get("PLNT_LOG_LEVEL", "info"),
    )
