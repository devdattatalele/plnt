"""End-to-end: intent -> orchestrator -> sandbox -> result event in the BB."""

from __future__ import annotations

from plnt.control.orchestrator import Orchestrator
from plnt.control.skills import SkillRegistry


def test_e2e_intent_to_result(isolated_home, tmp_path, monkeypatch):
    # Force the offline router so this test is hermetic — no external Ollama
    # / cloud API needed.
    monkeypatch.setenv("PLNT_REQUIRED_PATH", str(tmp_path / "never-exists"))
    monkeypatch.delenv("PLNT_CLOUD_URL", raising=False)
    monkeypatch.delenv("PLNT_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("PLNT_LOCAL_URL", "http://127.0.0.1:1")

    from plnt.config import paths

    sk_dir = paths().skills
    (sk_dir / "general-helper.md").write_text(
        "---\nmodel_hint: small\ntokens: 5000\nwall_seconds: 30\n---\n"
        "You are the general helper."
    )
    reg = SkillRegistry(sk_dir)

    orch = Orchestrator(skill_registry=reg, runs_root=paths().runs)
    handle = orch.start_run("find agent memory in the source tree")

    events = handle.blackboard.read_all()
    kinds = [e["kind"] for e in events]
    assert "intent" in kinds
    assert "spawn" in kinds
    assert "started" in kinds
    assert "finished" in kinds
    # offline router -> at least one tool_call event
    assert "tool_call" in kinds
