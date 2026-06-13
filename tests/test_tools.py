from __future__ import annotations

from pathlib import Path

import pytest

from plnt.execution.tools.execute import ExecuteError, execute
from plnt.execution.tools.search import SearchError, search


def test_search_finds_match(tmp_path: Path):
    (tmp_path / "hello.txt").write_text("the quick brown fox\nlazy dog\n")
    hits = search("fox", tmp_path, allowed_roots=[tmp_path])
    assert hits and hits[0].text.startswith("the quick")
    assert hits[0].line == 1


def test_search_rejects_outside(tmp_path: Path):
    other = tmp_path.parent
    with pytest.raises(SearchError):
        search("x", other, allowed_roots=[tmp_path])


def test_execute_runs_basic(tmp_path: Path):
    res = execute(["echo", "hi"], workdir=tmp_path, allowed_roots=[tmp_path])
    assert res.exit_code == 0
    assert res.stdout.strip() == "hi"


def test_execute_blocks_sudo(tmp_path: Path):
    with pytest.raises(ExecuteError, match="hard-blocked"):
        execute(["sudo", "ls"], workdir=tmp_path, allowed_roots=[tmp_path])


def test_execute_workdir_must_be_allowed(tmp_path: Path):
    with pytest.raises(ExecuteError):
        execute(["echo", "x"], workdir=Path("/tmp"), allowed_roots=[tmp_path])


def test_execute_times_out(tmp_path: Path):
    res = execute(["sleep", "5"], workdir=tmp_path, allowed_roots=[tmp_path], timeout_seconds=1)
    assert res.exit_code == 124
    assert "timed out" in res.stderr
