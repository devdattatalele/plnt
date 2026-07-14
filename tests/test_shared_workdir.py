"""Shared project_dir contract.

Every spawn in a complex_task swarm cd's into the SAME directory so sibling
agents (scaffolder + navbar-writer + homepage-writer) can read each other's
files. Phoenix-os PhoenixContext.work_dir is the inspiration; we extend it
across our parallel DAG.

Plus: session continuity via the answer footer — turn 2 inherits turn 1's
project so "now add a navbar" lands in the same dir.
"""

from __future__ import annotations

from pathlib import Path

from plnt.control.orchestrator import (
    _attach_project_footer,
    _auto_chain_producer_consumer,
    _harvest_prior_project,
    _inject_workdir,
    _resolve_project_dir,
)
from plnt.execution.spec import AgentSpec, Budget


def _spec(id_: str, role: str, deps: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        id=id_,
        role=role,
        run_id="r-test",
        depth=0,
        inputs={"intent": "test", "depends_on": deps or []},
        budget=Budget(tokens=1000, wall_seconds=10),
    )


# ---- _resolve_project_dir -------------------------------------------------


def test_resolve_falls_back_to_run_scoped_dir_when_nothing_known(tmp_path):
    out = _resolve_project_dir(
        intent="build me a thing",
        user_paths=[],
        prior_project=None,
        run_id="r-abcdef1234",
        runs_root=tmp_path,
    )
    assert out == tmp_path / "r-abcdef1234" / "project"
    assert out.exists()


def test_resolve_uses_existing_project_root_when_marker_present(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    (project / "package.json").write_text("{}")
    out = _resolve_project_dir(
        intent="add a navbar",  # edit-verb, no create-verb
        user_paths=[str(project)],
        prior_project=None,
        run_id="r-xx",
        runs_root=tmp_path,
    )
    assert out == project


def test_resolve_carves_subdir_inside_parent_container(tmp_path):
    parent = tmp_path / "Documents"
    parent.mkdir()
    # Two existing sibling projects make it look like a "I hold projects" dir.
    (parent / "proj-a").mkdir()
    (parent / "proj-b").mkdir()

    out = _resolve_project_dir(
        intent="build a vite website",
        user_paths=[str(parent)],
        prior_project=None,
        run_id="r-deadbeef00",
        runs_root=tmp_path,
    )
    # The result is INSIDE the parent, not the parent itself.
    assert out.parent == parent
    # New slug-from-intent picks the meaningful noun, not the full sentence.
    assert out.name in ("vite", "vite-website")


# REGRESSION: a user-mentioned path that has .git inside used to be treated
# as "this is the project, write here directly." Wrong — den-agent/.git is a
# workspace marker, not a project marker. Parent-container check must win.
def test_resolve_does_not_treat_gitted_workspace_as_project_root(tmp_path):
    workspace = tmp_path / "Documents" / "den-agent"
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()  # workspace is git-managed
    (workspace / "subproj-a").mkdir()
    (workspace / "subproj-b").mkdir()

    out = _resolve_project_dir(
        intent="build a chatbot",
        user_paths=[str(workspace)],
        prior_project=None,
        run_id="r-xx",
        runs_root=tmp_path,
    )
    # Must NOT be the workspace itself — must be a child named after the noun.
    assert out != workspace
    assert out.parent == workspace
    assert out.name == "chatbot"


# REGRESSION: a create-verb in the current intent must override a prior
# project from history. "build a new chatbot" after a vite turn → chatbot
# gets its own dir, doesn't inherit the vite project.
def test_create_verb_resets_session_project(tmp_path):
    prior_project = tmp_path / "old-vite-app"
    prior_project.mkdir()
    (prior_project / "package.json").write_text("{}")

    out = _resolve_project_dir(
        intent="build a chatbot",
        user_paths=[],
        prior_project=str(prior_project),
        run_id="r-fresh01",
        runs_root=tmp_path,
    )
    assert out != prior_project
    # No user_paths → falls to run-scoped fallback (case 4).
    assert "r-fresh01" in str(out)


def test_edit_verb_preserves_session_project(tmp_path):
    prior_project = tmp_path / "old-vite-app"
    prior_project.mkdir()
    (prior_project / "package.json").write_text("{}")

    out = _resolve_project_dir(
        intent="add a footer to the homepage",  # edit-verb, no create-verb
        user_paths=[],
        prior_project=str(prior_project),
        run_id="r-cont01",
        runs_root=tmp_path,
    )
    assert out == prior_project


def test_slug_from_intent_picks_meaningful_noun():
    from plnt.control.orchestrator import _slug_from_intent

    assert _slug_from_intent("build a chatbot for me") == "chatbot"
    assert _slug_from_intent("build me a new portfolio site please") == "portfolio"
    assert _slug_from_intent("scaffold a vite project") == "vite"
    # All stop words → fallback
    assert _slug_from_intent("build me a new thing") == "task"


def test_resolve_prefers_prior_project_for_session_continuity(tmp_path):
    earlier = tmp_path / "earlier-project"
    earlier.mkdir()
    (earlier / "package.json").write_text("{}")

    user_path = tmp_path / "Documents"
    user_path.mkdir()

    out = _resolve_project_dir(
        intent="now add a navbar",
        user_paths=[str(user_path)],
        prior_project=str(earlier),
        run_id="r-anything",
        runs_root=tmp_path,
    )
    assert out == earlier


# ---- _harvest_prior_project + _attach_project_footer ---------------------


def test_attach_then_harvest_roundtrip():
    project = Path("/tmp/plnt-roundtrip-xyz")
    ans = _attach_project_footer("Done — created 4 files.", project)
    assert "Working in:" in ans

    # The next turn's history would carry this answer.
    class Turn:
        prompt = "first turn"
        answer = ans

    got = _harvest_prior_project([Turn()])
    assert got == str(project)


def test_harvest_returns_none_when_no_marker():
    class Turn:
        prompt = "hi"
        answer = "hello there"

    assert _harvest_prior_project([Turn()]) is None


# ---- _inject_workdir ------------------------------------------------------


def test_inject_workdir_sets_workdir_on_every_spec(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    s = _spec("a-scaffold", "vite-scaffolder")
    out = _inject_workdir(s, project, user_paths=[])
    assert out.inputs["workdir"] == str(project)
    # search_roots must include the project itself
    assert str(project) in out.inputs["search_roots"]


def test_inject_workdir_preserves_user_search_roots(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    s = _spec("a-x", "reader")
    out = _inject_workdir(s, project, user_paths=["/extra/read/only"])
    assert "/extra/read/only" in out.inputs["search_roots"]
    assert str(project) in out.inputs["search_roots"]


# ---- _auto_chain_producer_consumer ---------------------------------------


def test_auto_chain_makes_consumers_depend_on_producer():
    specs = [
        _spec("a-vite", "vite-scaffolder"),
        _spec("a-nav",  "navbar-writer"),
        _spec("a-home", "homepage-writer"),
    ]
    out = _auto_chain_producer_consumer(specs)
    deps = {s.id: s.inputs["depends_on"] for s in out}
    assert deps["a-vite"] == []
    assert deps["a-nav"] == ["a-vite"]
    assert deps["a-home"] == ["a-vite"]


def test_auto_chain_skips_when_planner_already_set_deps():
    specs = [
        _spec("a-init", "project-init"),
        _spec("a-edit", "navbar-writer", deps=["a-init"]),
    ]
    out = _auto_chain_producer_consumer(specs)
    # Unchanged
    assert out[1].inputs["depends_on"] == ["a-init"]


def test_auto_chain_noop_when_no_obvious_producer():
    specs = [_spec("a-x", "reader"), _spec("a-y", "writer")]
    out = _auto_chain_producer_consumer(specs)
    for s in out:
        assert s.inputs["depends_on"] == []
