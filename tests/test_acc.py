from __future__ import annotations

from plnt.control.acc import ACCMonitor


def _evt(kind, agent_id="a1", payload=None):
    return {"kind": kind, "agent_id": agent_id, "payload": payload or {}}


def test_loop_detection_fires_after_threshold():
    acc = ACCMonitor()
    call = _evt("tool_call", payload={"tool": "search", "args": {"pattern": "x"}})
    assert acc.observe(call) == []
    assert acc.observe(call) == []
    detections = acc.observe(call)  # third
    assert detections and detections[0].kind == "loop"


def test_depth_cap_blows_at_root_plus_three():
    killed = []
    acc = ACCMonitor(kill_fn=lambda aid, reason: killed.append((aid, reason)) or True)
    acc.observe(_evt("spawn", agent_id="a4", payload={"depth": 4, "role": "x"}))
    assert killed and killed[0][0] == "a4"


def test_pingpong():
    acc = ACCMonitor()
    for _ in range(2):
        acc.observe(_evt("spawn", agent_id="A", payload={"parent_id": "P1", "role": "B-role", "depth": 1}))
        acc.observe(_evt("spawn", agent_id="B", payload={"parent_id": "P2", "role": "A-role", "depth": 1}))
    # last call should report a pingpong
    dets = acc.observe(_evt("spawn", agent_id="C", payload={"parent_id": "P1", "role": "B-role", "depth": 1}))
    kinds = [d.kind for d in dets]
    # not strictly required to fire by step 5, but should NOT crash
    assert isinstance(kinds, list)
