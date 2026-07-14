"""Orchestrator — ties the four planes together for one run.

Lifecycle of one intent:
  1. Surface hands an intent to Orchestrator.start_run().
  2. Orchestrator emits the `intent` event, opens a Blackboard.
  3. Planner LLM picks a resident specialist.
  4. Specialist emits AgentSpec(s) for ephemeral spawns.
  5. Each spawn runs in its sandbox; events flow back into the Blackboard.
  6. ACC + budget governor watch the stream and kill misbehaviour.
  7. Final result is written to a markdown file under the user's choice of
     output dir, and a `result` event closes the run.

v0 keeps step 3 simple: the planner is implemented in code as a router that
maps intent -> role via keyword match, and falls back to `general-helper`.
A future revision swaps in an LLM-based planner without changing this file's
shape — the planner is just a function from (intent, registry) -> AgentSpec.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from plnt.control.acc import ACCMonitor
from plnt.control.budget import BudgetExceeded, BudgetGovernor, RunBudget
from plnt.control.parallel import ParallelOrchestrator
from plnt.control.skills import SkillRegistry
from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox import get_sandbox
from plnt.execution.sandbox.base import SandboxResult
from plnt.execution.spec import AgentSpec, Budget


@dataclass
class RunHandle:
    run_id: str
    intent: str
    blackboard: Blackboard
    budget: BudgetGovernor
    acc: ACCMonitor
    # Single-spawn legacy field — populated by start_run.
    result: SandboxResult | None = None
    # Swarm path — populated by start_swarm.
    plan_text: str = ""
    results: list[SandboxResult] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.results is None:
            self.results = []


PlannerFn = Callable[[str, SkillRegistry], AgentSpec]


def keyword_planner(intent: str, registry: SkillRegistry) -> AgentSpec:
    """Default planner — keyword routing onto the loaded skills."""
    lower = intent.lower()
    available = registry.list()

    # Cheap heuristic; the skill bundles list their own routing keywords in
    # the front-matter `keywords:` line. We don't read those yet — v0 just
    # falls back to a `general-helper` if no name matches.
    chosen = "general-helper"
    for role in available:
        if role in lower or role.replace("-", " ") in lower:
            chosen = role
            break

    sk = registry.get(chosen) or registry.get("general-helper")
    tools = sk.tools if sk else ["search", "execute"]
    model_hint = (sk.model_hint if sk else "auto") or "auto"
    if model_hint not in ("small", "deep", "auto"):
        model_hint = "auto"

    return AgentSpec(
        role=chosen,
        run_id=f"r-{uuid.uuid4().hex[:10]}",  # overwritten by Orchestrator
        depth=0,
        lifetime="ephemeral",
        isolation="process",
        tools=tools,
        inputs={
            "intent": intent,
            "skill_prompt": sk.prompt if sk else None,
        },
        model_hint=model_hint,  # type: ignore[arg-type]
        budget=Budget(
            tokens=sk.budget.get("tokens", 20_000) if sk else 20_000,
            wall_seconds=sk.budget.get("wall_seconds", 300) if sk else 300,
            joules=sk.budget.get("joules", 0) if sk else 0,
        ),
    )


class Orchestrator:
    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        run_budget: RunBudget | None = None,
        planner: PlannerFn | None = None,
        runs_root: Path | None = None,
    ):
        self.skills = skill_registry or SkillRegistry()
        self.run_budget = run_budget or RunBudget()
        self.planner = planner or keyword_planner
        self.runs_root = runs_root

    def start_run(self, intent: str) -> RunHandle:
        run_id = f"r-{uuid.uuid4().hex[:10]}"
        bb = Blackboard(run_id, root=self.runs_root)
        bb.emit("intent", payload={"text": intent})
        budget = BudgetGovernor(run_id, self.run_budget, blackboard=bb)

        # ACC will call back via the sandbox.kill() once it's constructed.
        kill_target: dict[str, Callable] = {}

        def kill(agent_id: str, reason: str) -> bool:
            fn = kill_target.get("kill")
            if fn:
                return bool(fn(agent_id, reason))
            return False

        acc = ACCMonitor(kill_fn=kill)

        spec = self.planner(intent, self.skills)
        spec = spec.model_copy(update={"run_id": run_id})

        try:
            budget.check_spawn(spec)
        except BudgetExceeded as e:
            bb.emit("error", payload={"reason": f"budget pre-check: {e}"})
            bb.emit("finished")
            return RunHandle(run_id, intent, bb, budget, acc)

        sandbox_cls = get_sandbox(spec.isolation)
        sandbox = sandbox_cls(blackboard=bb)
        kill_target["kill"] = sandbox.kill

        # Feed the ACC the events we just appended (the runner will emit more
        # via the subprocess; those land in the same file, so ACC will see
        # them on the post-hoc re-read if we want batch mode).
        for evt in bb.read_all():
            acc.observe(evt)

        result = sandbox.run(spec)
        # Post-run: replay events into ACC so any deferred detections record.
        for evt in result.events:
            acc.observe(evt)

        bb.emit("finished")
        return RunHandle(run_id, intent, bb, budget, acc, result=result)

    def start_swarm(self, intent: str, history: list | None = None) -> RunHandle:
        """LLM-driven planner emits N AgentSpecs; fan out under one Blackboard."""
        run_id = f"r-{uuid.uuid4().hex[:10]}"
        return self.start_swarm_with_id(intent, run_id, history=history)

    def start_swarm_with_id(
        self,
        intent: str,
        run_id: str,
        blackboard: Blackboard | None = None,
        history: list | None = None,
    ) -> RunHandle:
        """Triage -> (chat | one agent | DAG fan-out) -> synthesize.

        - triage classifies the intent. "hi" returns kind=chat with a direct
          reply; no agents are spawned.
        - simple_task -> one agent.
        - complex_task -> planner emits a DAG; DAGExecutor runs it; synthesizer
          reconciles outputs into a single user-facing answer.
        """
        from plnt.control.dag import DAGExecutor
        from plnt.control.planner_llm import llm_planner
        from plnt.control.synthesizer import synthesize
        from plnt.control.triage import Turn as TriTurn
        from plnt.control.triage import triage

        bb = blackboard or Blackboard(run_id, root=self.runs_root)
        bb.emit("intent", payload={"text": intent})
        budget = BudgetGovernor(run_id, self.run_budget, blackboard=bb)
        acc = ACCMonitor()

        # Normalise history to TriTurn list (callers may pass dicts).
        tri_history: list[TriTurn] = []
        for t in history or []:
            if isinstance(t, TriTurn):
                tri_history.append(t)
            elif isinstance(t, dict):
                tri_history.append(TriTurn(prompt=t.get("prompt", ""), answer=t.get("answer", "")))

        bb.emit("triage_start")
        tri = triage(intent, history=tri_history)
        bb.emit("triage", payload={
            "kind": tri.kind,
            "reason": tri.reason,
            "estimated_agents": tri.estimated_agents,
            "missing_info": tri.missing_info,
        })

        # --- chat path: no swarm, just reply ----------------------------------
        if tri.kind == "chat":
            bb.emit("answer", payload={"text": tri.reply or "(no reply)", "source": "triage"})
            bb.emit("finished", payload={"spawned": 0, "completed": 0, "killed": 0})
            handle = RunHandle(run_id, intent, bb, budget, acc)
            handle.plan_text = "chat: replied directly without spawning agents"
            handle.results = []
            return handle

        # If the assistant's previous turn was a clarifying question, this
        # current message is most likely the answer to it — DO NOT re-ask,
        # DO NOT flip to chat. Treat it as a continuation of the prior task.
        from plnt.control.clarify import (
            assistant_was_clarifying,
            clarification_for_manifest,
            first_match,
        )

        replying_to_clarify = assistant_was_clarifying(tri_history)
        if replying_to_clarify and tri.kind in ("chat", "needs_clarification"):
            # Force the planning path. The LLM triage misclassified.
            bb.emit("triage", payload={
                "kind": "complex_task",
                "reason": "override: user is answering a prior clarifying question",
                "missing_info": [],
            })
            tri.kind = "complex_task"  # type: ignore[misc]

        # --- deterministic clarification: if the likely skill needs inputs
        #     the user didn't provide, ask BEFORE running anything.
        #     SKIPPED when we're already in an answer-to-question turn.
        if tri.kind != "needs_clarification" and not replying_to_clarify:
            manifest = first_match(intent, self.skills, history=tri_history)
            if manifest:
                clar = clarification_for_manifest(manifest, intent, history=tri_history)
                if clar:
                    bb.emit("triage", payload={
                        "kind": "needs_clarification",
                        "reason": f"{manifest.role} requires: {','.join(clar.missing)}",
                        "missing_info": clar.missing,
                    })
                    bb.emit("answer", payload={"text": clar.text, "source": "clarify", "missing_info": clar.missing})
                    bb.emit("finished", payload={"spawned": 0, "completed": 0, "killed": 0})
                    handle = RunHandle(run_id, intent, bb, budget, acc)
                    handle.plan_text = f"clarify: {manifest.role} missing {clar.missing}"
                    handle.results = []
                    return handle

        # --- clarification path: ask the user before spawning ------------------
        if tri.kind == "needs_clarification":
            reply = tri.reply or "I need a bit more info before I can start. Could you share more detail?"
            bb.emit("answer", payload={"text": reply, "source": "clarify", "missing_info": tri.missing_info})
            bb.emit("finished", payload={"spawned": 0, "completed": 0, "killed": 0})
            handle = RunHandle(run_id, intent, bb, budget, acc)
            handle.plan_text = "clarify: asked the user for the missing info"
            handle.results = []
            return handle

        # Resolve ONE shared project_dir for the whole swarm BEFORE planning so
        # the planner can see it and emit relative paths instead of inventing
        # absolute ones. The idea is borrowed from phoenix-os PhoenixContext:
        # one workdir flows through every sub-agent so they all operate on the
        # same project, not three disjoint sandboxes.
        user_paths = _harvest_paths(intent, tri_history)
        prior_project = _harvest_prior_project(tri_history)
        project_dir = _resolve_project_dir(
            intent, user_paths, prior_project, run_id, self.runs_root,
        )
        bb.emit("project_dir", payload={
            "path": str(project_dir),
            "source": "prior" if prior_project else ("user_path" if user_paths else "run_scoped"),
        })

        # --- complex path: full plan + DAG ------------------------------------
        if tri.kind == "complex_task":
            bb.emit("planner_start", payload={"intent": intent, "workdir": str(project_dir)})
            specs = llm_planner(
                intent, self.skills, history=tri_history, project_dir=project_dir,
            )
        else:
            # simple_task: one direct agent, no planner LLM call
            from plnt.control.planner_llm import _default_spec
            specs = [_default_spec(intent, self.skills, project_dir=project_dir)]

        # Inject the shared workdir + read-only search_roots into every spec.
        # search_roots is for reading the user's existing files; workdir is the
        # single place writes land. Read != write.
        specs = [_inject_workdir(s, project_dir, user_paths) for s in specs]

        # Fold saved Integrations into AgentSpec.inputs. Planner-supplied
        # values always win; this only fills inputs the planner didn't already
        # specify (paths, API keys, etc. set via the web UI).
        from plnt.surface.integrations import IntegrationsStore, merge_into_spec
        _integrations = IntegrationsStore()
        specs = [merge_into_spec(s, _integrations) for s in specs]

        # If the LLM didn't sequence the DAG and the plan obviously has a
        # producer (scaffolder/init/bootstrap) feeding consumers, wire the
        # implicit edges so we don't race-add a navbar to an empty dir.
        specs = _auto_chain_producer_consumer(specs)

        specs = [s.model_copy(update={"run_id": run_id}) for s in specs]
        bb.emit("plan", payload={
            "agent_count": len(specs),
            "agents": [
                {"id": s.id, "role": s.role, "intent": s.inputs.get("intent", ""),
                 "depends_on": s.inputs.get("depends_on", [])}
                for s in specs
            ],
        })

        executor = DAGExecutor(bb, budget, acc)
        out = executor.run(specs)

        # Produce the user-facing answer. Always non-empty.
        ans, source = _build_user_answer(intent, tri, out, specs)
        # Tack on the project_dir footer so the NEXT conversation turn can
        # parse it back out via _harvest_prior_project — that's how Plnt gets
        # phoenix-style session continuity for free, with no extra schema.
        ans = _attach_project_footer(ans, project_dir)
        bb.emit("answer", payload={
            "text": ans, "source": source, "project_dir": str(project_dir),
        })

        bb.emit("finished", payload={
            "spawned": out.spawned,
            "completed": out.completed,
            "killed": out.killed,
        })

        handle = RunHandle(run_id, intent, bb, budget, acc)
        handle.results = out.results
        return handle

    def write_outcome(self, run: RunHandle, out_dir: Path) -> Path | None:
        if os.environ.get("PLNT_WRITE_MD", "0") != "1":
            return None
        results = run.results or ([run.result] if run.result else [])
        if not results:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"plnt-{run.run_id}-{int(time.time())}.md"
        lines = [f"# Plnt run {run.run_id}", "", f"Intent: {run.intent}", ""]
        for r in results:
            inner = (r.output or {}).get("output") or r.output or {}
            ans = inner.get("answer") if isinstance(inner, dict) else str(inner)
            lines.append(f"## {r.agent_id}")
            lines.append(str(ans or "(no answer)"))
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        return path


def _build_user_answer(intent, tri, out, specs):
    """Always return (text, source). Never empty."""
    from plnt.control.synthesizer import synthesize

    outputs = out.outputs or {}

    if outputs:
        # Single-agent simple_task -> use the agent's answer verbatim.
        if len(outputs) == 1 and tri.kind == "simple_task":
            only = next(iter(outputs.values()))
            ans = only.get("answer") if isinstance(only, dict) else None
            if not ans:
                ans = _concat_fallback(outputs)
            return ans, "agent"
        # Multi-agent -> synth, with deterministic fallback.
        answer = synthesize(intent, "swarm", outputs)
        if answer and answer.strip() and "(no answer)" not in answer:
            return answer, "synth"
        return _concat_fallback(outputs), "fallback"

    # Nothing — explain why.
    return _no_output_fallback(intent, tri, specs), "fallback"


def _concat_fallback(outputs: dict) -> str:
    parts = []
    for aid, out in outputs.items():
        if not isinstance(out, dict):
            parts.append(f"[{aid}] {str(out)[:300]}")
            continue
        ans = out.get("answer") or out.get("error") or ""
        if ans:
            parts.append(f"[{aid}] {ans}")
    if parts:
        return "\n\n".join(parts)
    return "(every agent finished but none produced a useful answer)"


def _no_output_fallback(intent: str, tri, specs) -> str:
    roles = [s.role for s in (specs or [])]
    bits = [f"No agent produced output for: {intent[:200]}"]
    if roles:
        bits.append(f"Spawned: {', '.join(roles)}.")
    bits.append(
        "Agents made model calls that returned empty or malformed tool calls. "
        "Try a more concrete prompt with specific paths, or upgrade to a "
        "stronger model (qwen2.5:7b-instruct or llama3.1:8b)."
    )
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Path harvesting + injection — make sure spawned agents use the path the
# user actually mentioned, not the hallucinated /users/me/project.
# ---------------------------------------------------------------------------

import re as _re

# Absolute POSIX paths and ~/ paths anywhere in a string.
_PATH_RE = _re.compile(r"(~/[\w./_-]+|/[A-Za-z0-9][\w./_-]+)")


def _harvest_paths(intent: str, history: list) -> list[str]:
    """Pull every absolute path mentioned in the current intent + last 6 turns."""
    blobs = [intent or ""]
    for t in (history or [])[-6:]:
        prompt = getattr(t, "prompt", "") if not isinstance(t, dict) else t.get("prompt", "")
        if prompt:
            blobs.append(prompt)
    out: list[str] = []
    seen: set[str] = set()
    for blob in blobs:
        for m in _PATH_RE.findall(blob):
            p = m.strip(".,;:!?)")
            if p and p not in seen:
                out.append(p)
                seen.add(p)
    return out


# ---------------------------------------------------------------------------
# Shared project_dir — every spawn in a swarm cd's into the same dir so they
# share files. Inspired by phoenix-os PhoenixContext.work_dir but extended
# across our parallel DAG: the project IS the shared state.
# ---------------------------------------------------------------------------

# Parent dirs the user almost certainly DOESN'T want us scaffolding directly
# into — they're containers for multiple projects, not a project themselves.
_PARENT_HINTS = ("Documents", "code", "src", "Projects", "workspace", "repos", "Desktop")
# Markers that say "this IS already a project root" — STRONG signals only.
# .git is explicitly EXCLUDED here because many workspace/parent dirs are
# git-managed (mono-repos, dotfiles, the user's own ~/Documents/x dir) and
# would otherwise hijack the project_dir resolution.
_PROJECT_MARKERS = ("package.json", "pyproject.toml", "Cargo.toml", "go.mod")
# Verbs that mean "create something new"; with these, we ALWAYS carve a
# subdir, never write into an existing root.
_CREATE_VERBS = _re.compile(
    r"\b(build|create|make|scaffold|generate|init|initialise|initialize|"
    r"start|new|setup|set\s+up|bootstrap)\b",
    _re.IGNORECASE,
)
# Dirs to skip when counting "is this a project container" — they're noise.
_NOISE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
    "dist", "build", ".cache", ".pytest_cache", "target", ".turbo",
}


def _looks_like_project_root(p: Path) -> bool:
    try:
        return any((p / m).exists() for m in _PROJECT_MARKERS)
    except OSError:
        return False


def _looks_like_parent_container(p: Path) -> bool:
    """True if `p` is a workspace that HOLDS projects rather than IS one."""
    parts = p.parts
    has_parent_hint = any(seg in _PARENT_HINTS for seg in parts)
    try:
        kids = [
            c for c in p.iterdir()
            if c.is_dir() and not c.name.startswith(".") and c.name not in _NOISE_DIRS
        ]
    except OSError:
        return has_parent_hint
    # Strong signal: a path with Documents/code/src/etc. in it is a parent
    # regardless of marker files. Conventional user workspaces.
    if has_parent_hint:
        return True
    # Even without hints, ≥3 non-noise child dirs and no top-level project
    # markers means it's a container.
    if len(kids) >= 3 and not _looks_like_project_root(p):
        return True
    return False


# Stop-words pulled out of the slug so "build me a chatbot please" -> "chatbot".
_STOP_WORDS = {
    "a", "an", "the", "me", "my", "please", "pls", "with", "and", "of", "in",
    "on", "for", "to", "from", "now", "next", "new", "some", "any", "this",
    "that", "build", "create", "make", "scaffold", "generate", "init",
    "initialise", "initialize", "start", "setup", "set", "up", "bootstrap",
    "project", "app", "thing", "stuff", "site", "website", "page", "service",
    "want", "need", "would", "like",
}


def _slug_from_intent(intent: str, max_len: int = 32) -> str:
    """Pull the meaningful noun out of an intent.

    "build a chatbot for me"        -> "chatbot"
    "scaffold a vite project"       -> "vite"
    "create next-base portfolio"    -> "next-base-portfolio"
    """
    words = [
        w for w in _re.split(r"[^a-zA-Z0-9-]+", (intent or "").lower())
        if w and w not in _STOP_WORDS and len(w) > 1
    ]
    if not words:
        return "task"
    slug = "-".join(words)[:max_len].strip("-")
    return slug or "task"


# Back-compat shim — tests/other callers may import this.
def _slugify(text: str, max_len: int = 32) -> str:
    return _slug_from_intent(text, max_len)


def _resolve_project_dir(
    intent: str,
    user_paths: list[str],
    prior_project: str | None,
    run_id: str,
    runs_root: Path | None,
) -> Path:
    """Decide ONE workdir for the whole swarm.

    Resolution order:
      1. Prior turn's project_dir (session continuity) — BUT only if the
         current intent is NOT a fresh create-verb ("build a new chatbot"
         deserves its own dir, not last turn's vite app).
      2. User-mentioned path that's already a project root (package.json /
         pyproject.toml / Cargo.toml / go.mod) AND the verb is edit-style
         ("add a navbar to /Users/x/myapp") — work IN their project.
      3. User-mentioned parent container ("inside /Users/x/Documents/...")
         OR any user path under a create-verb -> carve a slug-named subdir.
         "build chatbot inside ~/Documents/den-agent" ->
           ~/Documents/den-agent/chatbot/
      4. Otherwise: <PLNT_HOME>/runs/<run_id>/project/.

    Key invariant: a create-verb NEVER writes into an existing project root.
    "Build me a new X" always creates a new dir, never overwrites the user's
    existing project.
    """
    is_create = bool(_CREATE_VERBS.search(intent or ""))
    slug = _slug_from_intent(intent)

    # 1. Session continuity — but a create-verb resets the project.
    if prior_project and not is_create:
        p = Path(prior_project).expanduser()
        if p.exists() or p.parent.exists():
            p.mkdir(parents=True, exist_ok=True)
            return p.resolve()

    # 2/3. User mentioned a path
    for raw in user_paths:
        # Skip obvious non-paths the harvester regex catches by mistake
        # (e.g. "/127.0.0.1" from a URL pasted into the prompt). Real
        # filesystem targets either live under ~ or under /Users/, /home/,
        # /tmp/, /var/, /opt/ — anything else at the root is almost
        # certainly a misparse.
        if raw.startswith("/") and not raw.startswith((
            "/Users/", "/home/", "/tmp/", "/var/", "/opt/", "/srv/", "/mnt/", "/private/",
        )):
            continue
        try:
            p = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            continue

        # If the path doesn't exist, treat the user as having named a target
        # dir directly: make it and use it.
        if not p.exists():
            if p.parent.exists():
                try:
                    p.mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
                return p
            continue

        # Parent-container check WINS over project-root check. A user typing
        # ~/Documents/X with X.git inside is still telling us "this is my
        # workspace, put the new thing inside it." The create-verb makes
        # this unambiguous.
        if _looks_like_parent_container(p):
            child = p / slug
            # If a project with that name already exists, fall through to
            # it for session-style continuity instead of clobbering. Append
            # a run-id suffix only if we'd otherwise collide with a non-
            # project-shaped dir.
            if child.exists() and not _looks_like_project_root(child):
                child = p / f"{slug}-{run_id.split('-', 1)[-1][:6]}"
            child.mkdir(parents=True, exist_ok=True)
            return child

        if _looks_like_project_root(p) and not is_create:
            # Editing inside their existing project.
            return p

        if _looks_like_project_root(p) and is_create:
            # They named their project root but asked us to create a NEW
            # thing — carve a slug subdir inside it.
            child = p / slug
            child.mkdir(parents=True, exist_ok=True)
            return child

        # Existing dir, nothing fits — treat as project root.
        return p

    # 4. Run-scoped fallback
    from plnt.config import paths as _paths

    base = (runs_root or _paths().runs) / run_id / "project"
    base.mkdir(parents=True, exist_ok=True)
    return base


_PROJECT_DIR_RE = _re.compile(
    r"(?:project[_\s-]?dir|workdir|working in)[:\s]+`?(/[\w./_-]+|~/[\w./_-]+)`?",
    _re.IGNORECASE,
)


def _harvest_prior_project(history: list) -> str | None:
    """Pull the most recent project_dir we mentioned to the user from history.

    We emit answers that include "Working in: <path>" so a follow-up turn can
    pick it up and reuse the same project. This is how Plnt gets phoenix-style
    session continuity without adding a Session schema.
    """
    if not history:
        return None
    for t in reversed(history):
        ans = getattr(t, "answer", "") if not isinstance(t, dict) else t.get("answer", "")
        if not ans:
            continue
        m = _PROJECT_DIR_RE.search(ans)
        if m:
            return m.group(1)
    return None


def _inject_workdir(spec, project_dir: Path, user_paths: list[str]):
    """Set inputs.workdir (sandbox honors it) and search_roots (read-only).

    The sandbox's process.py reads inputs.workdir or inputs.output_dir to pick
    the spawn's cwd. Setting it here means every sibling agent shares the same
    project root — they can read each other's files.
    """
    new_inputs = dict(spec.inputs)
    new_inputs["workdir"] = str(project_dir)

    # search_roots: project_dir is always readable; user-mentioned paths are
    # readable; the planner's own search_roots from the plan are kept.
    roots: list[str] = []
    for r in (new_inputs.get("search_roots") or []):
        if isinstance(r, str) and r and r not in roots:
            roots.append(r)
    for p in user_paths:
        if p not in roots:
            roots.append(p)
    pd_str = str(project_dir)
    if pd_str not in roots:
        roots.append(pd_str)
    new_inputs["search_roots"] = roots
    return spec.model_copy(update={"inputs": new_inputs})


# Hints for "this agent produces the project skeleton" vs "this agent edits it"
_PRODUCER_RE = _re.compile(
    r"(scaffold|^init|bootstrap|create-(?:project|app|next|vite)|setup|generate-(?:project|app)|skeleton)",
    _re.IGNORECASE,
)


def _attach_project_footer(ans: str, project_dir: Path) -> str:
    """Append a stable, parseable footer so next turn's planner sees it.

    _harvest_prior_project() looks for the exact phrase "Working in:" in the
    history's answers and lifts the path out. Keep the wording stable.
    """
    if not ans:
        ans = ""
    footer = f"\n\nWorking in: {project_dir}"
    if "Working in:" in ans:
        return ans
    return ans.rstrip() + footer


def _auto_chain_producer_consumer(specs: list) -> list:
    """If the planner emitted N peers with no deps but one is clearly a
    producer (vite-scaffolder, project-init, repo-bootstrap), make every other
    spec depend on it so we don't race-edit an empty dir.

    Conservative: only kicks in when (a) no spec already has depends_on, and
    (b) exactly one role matches the producer regex.
    """
    if len(specs) < 2:
        return specs
    any_deps = any(s.inputs.get("depends_on") for s in specs if isinstance(s.inputs, dict))
    if any_deps:
        return specs
    producers = [s for s in specs if _PRODUCER_RE.search(s.role or "")]
    if len(producers) != 1:
        return specs
    producer = producers[0]
    out = []
    for s in specs:
        if s.id == producer.id:
            out.append(s)
            continue
        new_inputs = dict(s.inputs)
        new_inputs["depends_on"] = [producer.id]
        out.append(s.model_copy(update={"inputs": new_inputs}))
    return out
