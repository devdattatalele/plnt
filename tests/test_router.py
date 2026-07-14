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


def test_parse_tool_paren_array_form():
    """Real local models emit TOOL: execute(["cmd", "arg"]) — must work."""
    r = LLMRouter(force="offline")
    d = r._parse_decision('TOOL: execute(["echo", "hi"])', ["search", "execute"])
    assert d.kind == "tool_call" and d.tool_name == "execute"
    assert d.tool_args == {"argv": ["echo", "hi"]}


def test_parse_tool_paren_single_shell_string():
    """TOOL: execute(["npx create-next-app foo"]) -> shlex-split into argv."""
    r = LLMRouter(force="offline")
    d = r._parse_decision('TOOL: execute(["npx create-next-app foo"])', ["execute"])
    assert d.kind == "tool_call" and d.tool_args["argv"] == ["npx", "create-next-app", "foo"]


def test_parse_tool_search_paren_form():
    r = LLMRouter(force="offline")
    d = r._parse_decision('TOOL: search("MemoryManager", "/tmp")', ["search"])
    assert d.kind == "tool_call" and d.tool_args == {"pattern": "MemoryManager", "root": "/tmp"}


def test_parse_final_prefix_no_colon():
    r = LLMRouter(force="offline")
    d = r._parse_decision("FINAL building a site is...", ["search"])
    assert d.kind == "final" and d.text.startswith("building")
