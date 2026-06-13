from __future__ import annotations

from plnt.compute.router import Decision, LLMRouter


def test_offline_fallback_does_search_first():
    r = LLMRouter(small_url="http://127.0.0.1:1", deep_url="http://127.0.0.1:1")
    d = r.step(system="sys", user="please find agent memory papers", tools=["search", "execute"])
    assert isinstance(d, Decision)
    # offline → first turn is a search
    assert d.kind == "tool_call" and d.tool_name == "search"


def test_offline_summarises_after_transcript():
    r = LLMRouter(small_url="http://127.0.0.1:1")
    d = r.step(
        system="sys",
        user="catch me up",
        transcript=[{"step": 1, "tool": "search", "args": {"pattern": "x"}, "result": [{"a": 1}]}],
        tools=["search", "execute"],
    )
    assert d.kind == "final"
    assert "echo-planner" in d.text
