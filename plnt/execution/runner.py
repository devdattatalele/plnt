"""Agent runner — PID 1 of a micro-agent process.

Boots from one AgentSpec on stdin. Streams events on stdout. Talks to the
compute plane over HTTP. Has access to exactly the two tools the spec
declares (search / execute). Returns a `result` event with output that
matches the spec's output_schema, or an `error` event.

This is the *body* of an ephemeral micro-agent. The brain is the model
behind the compute router. The spirit is the skill markdown loaded by `role`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from plnt.execution.spec import AgentSpec


def _emit(kind: str, **payload) -> None:
    """Emit one JSONL event to stdout (read by ProcessSandbox)."""
    evt = {"ts": time.time(), "kind": kind, "agent_id": os.environ.get("PLNT_AGENT_ID", "")}
    if payload:
        evt["payload"] = payload
    print(json.dumps(evt, default=str), flush=True)


def _read_spec() -> AgentSpec:
    raw = sys.stdin.readline()
    if not raw:
        raise RuntimeError("no AgentSpec on stdin")
    data = json.loads(raw)
    return AgentSpec.model_validate(data)


def _allowed_roots(spec: AgentSpec) -> list[Path]:
    """Where the agent is allowed to search/execute."""
    workdir = Path(os.environ.get("PLNT_WORKDIR", os.getcwd()))
    roots: list[Path] = [workdir]
    # Inputs may include explicit search_roots (e.g. ~/Documents). The planner
    # decides; we only enforce that they exist.
    extra = spec.inputs.get("search_roots", [])
    if isinstance(extra, list):
        for r in extra:
            p = Path(str(r)).expanduser()
            if p.exists():
                roots.append(p)
    return roots


def _run_skill(spec: AgentSpec, allowed_roots: list[Path]) -> dict[str, Any]:
    """Execute the agent's skill against the LLM.

    v0: a single-turn ReAct-ish loop with the two tools. The skill markdown
    is rendered as the system prompt; the inputs become the user prompt. The
    runner steps the model up to `max_steps` times. Each step the model can
    call search or execute (one tool call per step), and on the final step
    must return a JSON result.

    To keep this file useful even without a live LLM endpoint, the runner
    falls back to an "echo" planner: it runs one search using the inputs and
    returns its hits as the result. Tests exercise both paths.
    """
    from plnt.compute.router import LLMRouter
    from plnt.execution.tools import execute, search

    max_steps = int(spec.inputs.get("max_steps", 6))
    transcript: list[dict[str, Any]] = []
    workdir = Path(os.environ.get("PLNT_WORKDIR", os.getcwd()))

    router = LLMRouter()

    skill_md = spec.inputs.get("skill_prompt") or _default_skill_prompt(spec.role)
    user_msg = spec.inputs.get("intent") or json.dumps(spec.inputs)

    for step in range(1, max_steps + 1):
        _emit("model_call", step=step, model_hint=spec.model_hint)
        decision = router.step(
            system=skill_md,
            user=user_msg,
            transcript=transcript,
            tools=spec.tools,
            model_hint=spec.model_hint,
        )
        _emit(
            "model_result",
            step=step,
            decision_kind=decision.kind,
            tokens=decision.tokens,
            latency_ms=decision.latency_ms,
        )

        if decision.kind == "final":
            ans = decision.text.strip()
            if not ans:
                # Model gave us nothing useful — synthesise from what we DID do.
                ans = _summarise_transcript(spec, transcript, workdir, reason="model returned empty FINAL")
            return {"answer": ans, "steps": step, "transcript": transcript}

        if decision.kind == "tool_call":
            tool = decision.tool_name
            args = decision.tool_args or {}
            _emit("tool_call", step=step, tool=tool, args=args)
            try:
                if tool == "search" and "search" in spec.tools:
                    hits = search(
                        args.get("pattern", ""),
                        args.get("root", str(workdir)),
                        allowed_roots=allowed_roots,
                        max_hits=int(args.get("max_hits", 50)),
                    )
                    result = [h.__dict__ for h in hits]
                elif tool == "execute" and "execute" in spec.tools:
                    res = execute(
                        args.get("argv", []),
                        workdir=workdir,
                        allowed_roots=allowed_roots,
                        timeout_seconds=int(args.get("timeout", 30)),
                    )
                    result = res.__dict__
                else:
                    result = {"error": f"tool {tool!r} not permitted"}
            except Exception as e:
                result = {"error": str(e)}
            _emit("tool_result", step=step, tool=tool, ok="error" not in result)
            transcript.append({"step": step, "tool": tool, "args": args, "result": result})
            continue

        # Unknown decision kind — bail out gracefully.
        return {
            "answer": _summarise_transcript(spec, transcript, workdir, reason=f"unknown decision {decision.kind!r}"),
            "transcript": transcript,
        }

    return {
        "answer": _summarise_transcript(spec, transcript, workdir, reason=f"max_steps {max_steps} exceeded"),
        "transcript": transcript,
    }


def _summarise_transcript(spec: "AgentSpec", transcript: list[dict], workdir: Path, reason: str) -> str:
    """When the model doesn't produce a clean FINAL, build a useful answer
    from what we ACTUALLY did. The user should always see something concrete."""
    role = spec.role
    intent = spec.inputs.get("intent", "") if isinstance(spec.inputs, dict) else ""

    # Tool-call stats.
    tools_used: dict[str, int] = {}
    last_tool = None
    last_args = None
    files_created: list[str] = []
    errors: list[str] = []
    for t in transcript:
        name = t.get("tool", "?")
        tools_used[name] = tools_used.get(name, 0) + 1
        last_tool = name
        last_args = t.get("args", {})
        res = t.get("result")
        if isinstance(res, dict):
            if "error" in res:
                errors.append(str(res["error"])[:120])
            # execute result
            if "stdout" in res and isinstance(res.get("stdout"), str) and res["stdout"].strip():
                files_created.append(f"{name}({last_args}) stdout: {res['stdout'][:200]}")

    # Files on disk now.
    try:
        on_disk = sorted(str(p.relative_to(workdir)) for p in workdir.rglob("*") if p.is_file())
    except Exception:
        on_disk = []

    bits = [f"[{role}] {reason}."]
    if intent:
        bits.append(f"Asked: {intent[:200]}")
    if tools_used:
        summary = ", ".join(f"{k}×{v}" for k, v in tools_used.items())
        bits.append(f"Did {len(transcript)} tool call(s): {summary}.")
    else:
        bits.append("Made no tool calls.")
    if last_tool:
        bits.append(f"Last: {last_tool}({_truncate_args(last_args)}).")
    if on_disk:
        sample = ", ".join(on_disk[:5])
        bits.append(f"Files in workdir ({len(on_disk)}): {sample}.")
    if errors:
        bits.append(f"Errors: {errors[0]}")
    return " ".join(bits)


def _truncate_args(a):
    s = repr(a)
    return s if len(s) <= 80 else s[:77] + "…"


def _default_skill_prompt(role: str) -> str:
    return (
        f"You are the {role} micro-agent in a Plnt swarm. You have two tools: "
        "search(pattern, root) and execute(argv). Context lives in the filesystem; "
        "use search to find things, execute to do things. When done, return a JSON "
        "object describing what you found or did."
    )


def main() -> int:
    try:
        spec = _read_spec()
    except Exception as e:
        _emit("error", reason=f"bad spec: {e}", trace=traceback.format_exc())
        _emit("finished")
        return 2

    _emit("started", role=spec.role, depth=spec.depth)
    try:
        result = _run_skill(spec, _allowed_roots(spec))
        # Last-resort guard: result must always have a non-empty `answer`.
        if not isinstance(result, dict) or not (result.get("answer") or "").strip():
            workdir = Path(os.environ.get("PLNT_WORKDIR", os.getcwd()))
            result = result if isinstance(result, dict) else {}
            result["answer"] = _summarise_transcript(
                spec, result.get("transcript", []), workdir, reason="no answer from skill loop",
            )
        _emit("result", output=result)
        return 0
    except Exception as e:  # noqa: BLE001 — runner is the outermost catch
        workdir = Path(os.environ.get("PLNT_WORKDIR", os.getcwd()))
        ans = f"[{spec.role}] crashed: {e}"
        _emit("error", reason=str(e), trace=traceback.format_exc())
        _emit("result", output={"answer": ans, "error": str(e)})
        return 1
    finally:
        _emit("finished")


if __name__ == "__main__":
    raise SystemExit(main())
