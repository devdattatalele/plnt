"""Tests for the skill installer's converter (without git, hermetic)."""

from __future__ import annotations

from pathlib import Path

from plnt.control.skill_installer import _convert, _find_skill_files, _parse_yaml_light, _sanitize_role


def test_parse_yaml_light_simple():
    out = _parse_yaml_light("name: foo\ndescription: bar baz\n")
    assert out["name"] == "foo"
    assert out["description"] == "bar baz"


def test_parse_yaml_light_list():
    out = _parse_yaml_light("tags:\n  - a\n  - b\n  - c\nname: x\n")
    assert out["tags"] == ["a", "b", "c"]
    assert out["name"] == "x"


def test_sanitize_role():
    assert _sanitize_role("My Skill Name") == "my-skill-name"
    assert _sanitize_role("hello_world") == "hello-world"
    assert _sanitize_role("Foo!!Bar") == "foo-bar"
    assert _sanitize_role("") == "imported-skill"


def test_convert_full_skill(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: pdf-extractor\ndescription: Extracts text from PDFs\ntags:\n  - pdf\n  - text\n---\n"
        "# PDF Extractor\n\nDo PDF things.\n"
    )
    role, toml, body = _convert(skill_dir / "SKILL.md", tmp_path)
    assert role == "pdf-extractor"
    assert 'name = "pdf-extractor"' in toml
    assert 'description = "Extracts text from PDFs"' in toml
    assert '"pdf"' in toml and '"text"' in toml
    assert body.startswith("# PDF Extractor")


def test_find_skill_files_skips_hidden_and_node_modules(tmp_path):
    (tmp_path / "good").mkdir()
    (tmp_path / "good" / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "SKILL.md").write_text("---\nname: x\ndescription: y\n---\nbody")

    files = _find_skill_files(tmp_path)
    rels = {str(p.relative_to(tmp_path)) for p in files}
    assert rels == {"good/SKILL.md"}


def test_convert_falls_back_to_dirname(tmp_path):
    skill_dir = tmp_path / "fallback-name"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\ndescription: no name\n---\nbody")
    role, toml, body = _convert(skill_dir / "SKILL.md", tmp_path)
    assert role == "fallback-name"
