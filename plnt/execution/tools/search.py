"""search() — grep over the agent's allowed roots.

Uses ripgrep when available (fast, gitignore-aware) and falls back to a pure
Python walker. Always returns hits with file path, 1-indexed line number, and
the matched line. Roots are validated against an allow-list — any attempt to
peek outside is a hard error.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SearchHit:
    path: str
    line: int
    text: str


class SearchError(Exception):
    pass


def _resolve_inside(root: Path, allowed_roots: list[Path]) -> Path:
    rr = root.resolve()
    for a in allowed_roots:
        ar = a.resolve()
        try:
            rr.relative_to(ar)
            return rr
        except ValueError:
            continue
    # Give the model enough info to retry intelligently. The error wraps the
    # allow-list so the agent's tool_result includes a usable hint.
    allowed_str = ", ".join(str(a.resolve()) for a in allowed_roots) or "(none)"
    raise SearchError(
        f"path {root} is not inside any allowed root. "
        f"Search must target one of: {allowed_str}. "
        f"Tip: pass '.' to search your workdir."
    )


def search(
    pattern: str,
    root: str | Path,
    *,
    allowed_roots: list[Path],
    max_hits: int = 200,
    case_sensitive: bool = False,
    file_glob: str | None = None,
) -> list[SearchHit]:
    """Search `pattern` (regex) under `root`. Roots must be allow-listed."""
    if not pattern:
        raise SearchError("empty pattern")
    if len(pattern) > 1000:
        raise SearchError("pattern too long")
    rp = _resolve_inside(Path(root), allowed_roots)

    rg = shutil.which("rg")
    if rg:
        return _search_rg(rg, pattern, rp, max_hits, case_sensitive, file_glob)
    return _search_py(pattern, rp, max_hits, case_sensitive, file_glob)


def _search_rg(
    rg: str,
    pattern: str,
    root: Path,
    max_hits: int,
    case_sensitive: bool,
    file_glob: str | None,
) -> list[SearchHit]:
    args = [rg, "--no-heading", "--line-number", "--max-count", str(max_hits)]
    if not case_sensitive:
        args.append("-i")
    if file_glob:
        args.extend(["-g", file_glob])
    args.extend(["--", pattern, str(root)])
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as e:
        raise SearchError("search timed out") from e
    hits: list[SearchHit] = []
    for line in proc.stdout.splitlines():
        # path:lineno:text — but path may itself contain colons. Split from the left twice.
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        try:
            lineno = int(parts[1])
        except ValueError:
            continue
        hits.append(SearchHit(path=parts[0], line=lineno, text=parts[2]))
        if len(hits) >= max_hits:
            break
    return hits


_EXCLUDE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache"}


def _search_py(
    pattern: str,
    root: Path,
    max_hits: int,
    case_sensitive: bool,
    file_glob: str | None,
) -> list[SearchHit]:
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        raise SearchError(f"invalid regex: {e}") from e

    hits: list[SearchHit] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fn in filenames:
            if file_glob and not Path(fn).match(file_glob):
                continue
            fp = Path(dirpath) / fn
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, start=1):
                        if rx.search(line):
                            hits.append(SearchHit(path=str(fp), line=i, text=line.rstrip("\n")))
                            if len(hits) >= max_hits:
                                return hits
            except (OSError, UnicodeDecodeError):
                continue
    return hits
