from __future__ import annotations

from plnt.control.skills import SkillRegistry, parse_skill


def test_parse_with_frontmatter():
    sk = parse_skill(
        "literature-scout",
        "---\ntools: search,execute\nmodel_hint: small\ntokens: 8000\n---\nYou are a scout.",
    )
    assert sk.tools == ["search", "execute"]
    assert sk.model_hint == "small"
    assert sk.budget["tokens"] == 8000
    assert "scout" in sk.prompt


def test_parse_without_frontmatter():
    sk = parse_skill("plain", "Just a prompt.")
    assert sk.prompt == "Just a prompt."
    assert sk.tools == ["search", "execute"]


def test_registry_round_trip(isolated_home):
    from plnt.config import paths

    sk_dir = paths().skills
    (sk_dir / "scout.md").write_text("---\nmodel_hint: deep\n---\nPrompt.")
    reg = SkillRegistry(sk_dir)
    assert "scout" in reg.list()
    sk = reg.get("scout")
    assert sk is not None and sk.model_hint == "deep"


def test_hot_reload(isolated_home):
    import time

    from plnt.config import paths

    sk_dir = paths().skills
    p = sk_dir / "live.md"
    p.write_text("v1")
    reg = SkillRegistry(sk_dir)
    sk1 = reg.get("live")
    assert sk1 is not None and "v1" in sk1.prompt
    time.sleep(0.01)
    p.write_text("v2")
    # bump mtime to ensure reload
    import os

    os.utime(p, None)
    sk2 = reg.get("live")
    assert sk2 is not None and "v2" in sk2.prompt
