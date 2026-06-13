"""Tests for the v0.2 skill.toml format."""

from __future__ import annotations

from pathlib import Path

import pytest

from plnt.control.clarify import clarification_for_manifest, first_match
from plnt.control.skill_schema import SkillManifest
from plnt.control.skills import SkillRegistry, parse_manifest_dir


def _write_skill(tmp_path: Path, role: str, toml: str, prompt: str) -> Path:
    d = tmp_path / role
    d.mkdir(parents=True)
    (d / "skill.toml").write_text(toml)
    (d / "prompt.md").write_text(prompt)
    return d


def test_loads_minimal_skill(tmp_path):
    _write_skill(
        tmp_path,
        "test-skill",
        """
[meta]
name = "test-skill"
""",
        "You are test-skill.",
    )
    sk = parse_manifest_dir("test-skill", tmp_path / "test-skill")
    assert sk.role == "test-skill"
    assert "test-skill" in sk.prompt
    assert sk.tools == ["search", "execute"]
    assert sk.manifest is not None


def test_validates_unknown_tool(tmp_path):
    _write_skill(
        tmp_path,
        "bad",
        """
[meta]
name = "bad"
[runtime]
tools = ["search", "execute", "delete_world"]
""",
        "x",
    )
    with pytest.raises(ValueError, match="unsupported tools"):
        parse_manifest_dir("bad", tmp_path / "bad")


def test_requires_inputs_round_trip(tmp_path):
    _write_skill(
        tmp_path,
        "needs-things",
        """
[meta]
name = "needs-things"
[[requires.inputs]]
name = "repo_root"
type = "directory"
description = "Where the code lives"
example = "~/proj"
""",
        "prompt",
    )
    sk = parse_manifest_dir("needs-things", tmp_path / "needs-things")
    assert sk.manifest is not None
    reqs = sk.manifest.requires.inputs
    assert len(reqs) == 1 and reqs[0].name == "repo_root" and reqs[0].type == "directory"


def test_registry_supports_both_formats(tmp_path):
    # legacy md
    (tmp_path / "legacy.md").write_text(
        "---\ntools: search,execute\nmodel_hint: small\n---\nYou are legacy."
    )
    # new dir
    _write_skill(
        tmp_path,
        "new-style",
        '[meta]\nname = "new-style"\n',
        "You are new-style.",
    )
    reg = SkillRegistry(tmp_path)
    roles = reg.list()
    assert "legacy" in roles and "new-style" in roles
    assert reg.get("legacy").manifest is None
    assert reg.get("new-style").manifest is not None


def test_clarification_when_required_input_missing(tmp_path):
    _write_skill(
        tmp_path,
        "cs",
        """
[meta]
name = "cs"
tags = ["code"]
[[requires.inputs]]
name = "repo_root"
type = "directory"
description = "Where the code lives"
example = "~/proj"
""",
        "x",
    )
    reg = SkillRegistry(tmp_path)
    manifest = first_match("review my cs project", reg)
    assert manifest is not None and manifest.meta.name == "cs"
    clar = clarification_for_manifest(manifest, "review my cs project")
    assert clar is not None
    assert "repo_root" in clar.missing
    assert "cs" in clar.text or "code lives" in clar.text


def test_clarification_skipped_when_path_in_intent(tmp_path):
    _write_skill(
        tmp_path,
        "cs",
        """
[meta]
name = "cs"
tags = ["code"]
[[requires.inputs]]
name = "repo_root"
type = "directory"
description = "Where"
""",
        "x",
    )
    reg = SkillRegistry(tmp_path)
    manifest = first_match("review my cs project at /Users/me/proj", reg)
    # The path token triggers the "user provided it" heuristic.
    clar = clarification_for_manifest(manifest, "review my cs project at /Users/me/proj")
    assert clar is None
