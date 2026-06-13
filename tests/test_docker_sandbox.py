"""Docker sandbox tests — only run when docker is available and the image exists."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from plnt.execution.blackboard import Blackboard
from plnt.execution.spec import AgentSpec, Budget


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        return r.returncode == 0
    except subprocess.SubprocessError:
        return False


def _image_present(tag: str) -> bool:
    r = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, timeout=3)
    return r.returncode == 0


docker_required = pytest.mark.skipif(
    not _docker_available(), reason="docker daemon not available"
)
image_required = pytest.mark.skipif(
    not _docker_available() or not _image_present("plnt/runtime:latest"),
    reason="plnt/runtime:latest image not built; run: docker build -t plnt/runtime:latest -f runtime.Dockerfile .",
)


@docker_required
def test_docker_sandbox_registered():
    from plnt.execution.sandbox import available_rungs

    assert "docker" in available_rungs()


@docker_required
def test_spec_accepts_docker_isolation():
    spec = AgentSpec(role="x", run_id="r-d", isolation="docker")
    assert spec.isolation == "docker"


@image_required
def test_docker_sandbox_end_to_end(isolated_home, tmp_path, monkeypatch):
    monkeypatch.setenv("PLNT_REQUIRED_PATH", str(tmp_path / "nope"))
    monkeypatch.delenv("PLNT_CLOUD_URL", raising=False)
    monkeypatch.setenv("PLNT_LOCAL_URL", "http://127.0.0.1:1")
    (tmp_path / "src.txt").write_text("plnt twin\n")
    from plnt.execution.sandbox.docker import DockerSandbox

    bb = Blackboard("r-docker")
    sb = DockerSandbox(bb)
    spec = AgentSpec(
        role="general-helper",
        run_id="r-docker",
        isolation="docker",
        inputs={"intent": "find plnt", "search_roots": [str(tmp_path)], "max_steps": 2},
        budget=Budget(wall_seconds=60),
    )
    res = sb.run(spec)
    assert res.exit_code == 0, f"runner exited {res.exit_code}; events={[e.get('kind') for e in res.events]}"
    kinds = [e.get("kind") for e in bb.read_all()]
    for required in ("spawn", "started", "finished"):
        assert required in kinds, f"missing event {required}: have {set(kinds)}"
