from __future__ import annotations

import json

from plnt.execution.blackboard import Blackboard


def test_emit_then_read(isolated_home):
    bb = Blackboard("r-test")
    bb.emit("intent", payload={"text": "hello"})
    bb.emit("started", agent_id="a-1")
    bb.emit("result", agent_id="a-1", payload={"answer": "world"})

    events = bb.read_all()
    assert [e["kind"] for e in events] == ["intent", "started", "result"]
    assert events[0]["payload"]["text"] == "hello"
    assert events[2]["agent_id"] == "a-1"


def test_large_payload_spills(isolated_home):
    bb = Blackboard("r-spill")
    big = "x" * 5000
    bb.emit("log", payload={"chunk": big})
    events = bb.read_all()
    assert events[0]["payload"]["chunk"]["_spilled"]
    spilled = events[0]["payload"]["chunk"]["_spilled"]
    assert (bb.artifacts_dir / spilled).read_text() == big


def test_events_file_is_grep_able(isolated_home):
    bb = Blackboard("r-grep")
    bb.emit("intent", payload={"text": "ping"})
    raw = bb.events_path.read_text().strip().splitlines()
    # Each line must be one valid JSON object — the audit contract.
    for line in raw:
        json.loads(line)
