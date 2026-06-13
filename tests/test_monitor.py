from __future__ import annotations

from plnt.surface.monitor import snapshot


def test_snapshot_has_required_keys(isolated_home):
    s = snapshot()
    for k in ("sandbox_rungs", "docker_agents", "docker_stats", "cpu_count", "runs_recent"):
        assert k in s, f"missing {k}"
    assert "process" in s["sandbox_rungs"]
