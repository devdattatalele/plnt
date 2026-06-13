from __future__ import annotations

from plnt.compute.router import Decision, LLMRouter


def test_offline_fallback_does_search_first():
    r = LLMRouter(force="offline")
    d = r.step(system="sys", user="please find agent memory papers", tools=["search", "execute"])
    assert isinstance(d, Decision)
    assert d.kind == "tool_call" and d.tool_name == "search"
    assert d.backend == "offline"


def test_offline_summarises_after_transcript():
    r = LLMRouter(force="offline")
    d = r.step(
        system="sys",
        user="catch me up",
        transcript=[{"step": 1, "tool": "search", "args": {"pattern": "x"}, "result": [{"a": 1}]}],
        tools=["search", "execute"],
    )
    assert d.kind == "final"
    assert "echo-planner" in d.text


def test_parse_multiline_tool_block():
    """Real local models emit TOOL: name\\n{json} — parser must handle."""
    r = LLMRouter(force="offline")
    d = r._parse_decision(
        'TOOL: search\n{"pattern": "MemoryManager", "root": "."}\nsome trailing prose',
        ["search", "execute"],
    )
    assert d.kind == "tool_call" and d.tool_name == "search"
    assert d.tool_args == {"pattern": "MemoryManager", "root": "."}


def test_parse_final_anywhere():
    r = LLMRouter(force="offline")
    d = r._parse_decision("Looking at the results... FINAL: done", ["search"])
    assert d.kind == "final" and d.text == "done"
