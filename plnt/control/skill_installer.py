"""Pull skills from public libraries into $PLNT_HOME/skills/.

Supports the de facto `SKILL.md` standard used by Anthropic skills, addyosmani
agent-skills, and 60+ other agent frameworks. The format is just YAML
frontmatter + markdown body — we wrap the body into a v0.2 skill dir with a
generated skill.toml that maps the standard fields.

Known sources (well-known shorthands):
  - anthropic         -> github.com/anthropics/skills
  - addyosmani        -> github.com/addyosmani/agent-skills
  - scientific        -> github.com/K-Dense-AI/scientific-agent-skills
  - antigravity       -> github.com/sickn33/antigravity-awesome-skills

Anything else: pass a full git URL.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from plnt.config import paths

KNOWN_SOURCES = {
    "anthropic": "https://github.com/anthropics/skills.git",
    "addyosmani": "https://github.com/addyosmani/agent-skills.git",
    "scientific": "https://github.com/K-Dense-AI/scientific-agent-skills.git",
    "antigravity": "https://github.com/sickn33/antigravity-awesome-skills.git",
    "claude-skills-collection": "https://github.com/abubakarsiddik31/claude-skills-collection.git",
    "the-library": "https://github.com/disler/the-library.git",
}


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class InstallError(Exception):
    pass


def install(source: str, target_dir: Path | None = None, dry_run: bool = False) -> dict:
    """Clone `source`, find every SKILL.md, convert to plnt v0.2 format.

    Returns a summary dict: {imported: N, skipped: N, files: [...]}.
    """
    url = KNOWN_SOURCES.get(source, source)
    if not (url.startswith("http") or url.startswith("git@")):
        raise InstallError(f"unknown source {source!r} and not a git URL")

    target = target_dir or paths().skills
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="plnt-skills-") as tmp:
        tmp_path = Path(tmp) / "repo"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(tmp_path)],
                check=True, capture_output=True, text=True, timeout=180,
            )
        except subprocess.CalledProcessError as e:
            raise InstallError(f"git clone failed: {e.stderr[:400]}") from e
        except subprocess.TimeoutExpired as e:
            raise InstallError("git clone timed out after 3 min") from e

        skill_files = _find_skill_files(tmp_path)
        imported, skipped, items = [], [], []
        for skill_md in skill_files:
            try:
                role, manifest_toml, prompt_md = _convert(skill_md, tmp_path)
            except Exception as e:
                skipped.append({"file": str(skill_md.relative_to(tmp_path)), "reason": str(e)[:200]})
                continue
            dst = target / role
            if dst.exists() and not dry_run:
                # Don't clobber user-edited skills.
                skipped.append({"file": str(skill_md.relative_to(tmp_path)), "reason": "already exists"})
                continue
            if not dry_run:
                dst.mkdir(parents=True, exist_ok=True)
                (dst / "skill.toml").write_text(manifest_toml)
                (dst / "prompt.md").write_text(prompt_md)
            imported.append({"role": role, "from": str(skill_md.relative_to(tmp_path))})
            items.append(role)

    return {
        "source": url,
        "target": str(target),
        "imported": len(imported),
        "skipped": len(skipped),
        "skills": items,
        "skipped_details": skipped[:20],
    }


def _find_skill_files(repo: Path) -> list[Path]:
    """Find every SKILL.md (case-insensitive) in the repo."""
    found: list[Path] = []
    for p in repo.rglob("*"):
        if p.is_file() and p.name.lower() == "skill.md":
            # Skip anything inside .git/, node_modules/, etc.
            if any(part.startswith(".") or part == "node_modules" for part in p.parts):
                continue
            found.append(p)
    return found


def _convert(skill_md: Path, repo_root: Path) -> tuple[str, str, str]:
    """Parse one SKILL.md -> (role, generated_skill_toml, prompt_md_body)."""
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("no YAML frontmatter found in SKILL.md")

    front = _parse_yaml_light(m.group(1))
    body = m.group(2).strip()

    name = str(front.get("name", "")).strip()
    if not name:
        # Fall back to the directory name.
        name = skill_md.parent.name
    role = _sanitize_role(name)

    description = str(front.get("description", "")).strip().replace("\n", " ")[:200]
    tags = front.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = [str(t) for t in tags if isinstance(t, (str, int))]

    toml = _render_toml(role, description, tags)
    return role, toml, body


def _parse_yaml_light(text: str) -> dict:
    """Tiny YAML-ish parser: handles `key: value` and `key:` lists."""
    out: dict = {}
    cur_key: str | None = None
    cur_list: list | None = None
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith(("  ", "\t-")) or line.startswith("-"):
            if cur_list is not None and cur_key is not None:
                cur_list.append(line.strip().lstrip("-").strip().strip("'\""))
            continue
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if not v:
                cur_list = []
                out[k] = cur_list
                cur_key = k
            else:
                v = v.strip().strip("'\"")
                out[k] = v
                cur_key = k
                cur_list = None
    return out


_ROLE_RE = re.compile(r"[^a-z0-9-]+")


def _sanitize_role(name: str) -> str:
    s = name.strip().lower().replace("_", "-").replace(" ", "-")
    s = _ROLE_RE.sub("-", s).strip("-")
    return s[:40] or "imported-skill"


def _render_toml(role: str, description: str, tags: list[str]) -> str:
    tag_arr = "[" + ", ".join(f'"{t}"' for t in tags[:10]) + "]"
    desc_esc = description.replace('"', "'")
    return f"""[meta]
name = "{role}"
version = "0.2"
description = "{desc_esc}"
tags = {tag_arr}

[runtime]
model_hint = "auto"
tools = ["search", "execute"]
default_isolation = "process"

[budget]
tokens = 12_000
wall_seconds = 180

# No declared requires.inputs — the imported skill's prompt drives behaviour.

[output]
schema = "object"
required = ["summary"]
[output.properties.summary]
type = "string"
format = "markdown"

[graph]
can_spawn = []
"""
