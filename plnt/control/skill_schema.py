"""Pydantic schema for plnt skill manifests.

A skill in plnt v0.2 is a *directory*:

  ~/.plnt/skills/<role>/
    ├── skill.toml      typed metadata (this module's schema)
    └── prompt.md       system prompt (raw markdown, agent sees it verbatim)

Optional:
    └── examples.md     few-shot examples appended to the prompt

The TOML carries:
  [meta]      identity, version, tags
  [runtime]   model hint, tools, isolation rung
  [budget]    tokens / wall_seconds / joules
  [requires]  inputs the skill needs before it can run (drives triage)
  [output]    JSON-Schema-ish description of the agent's structured output
  [graph]     which child skills this skill is allowed to spawn

Old-format markdown skills (`~/.plnt/skills/<role>.md`) keep working — the
loader probes for both forms.

The HTML/RAG storage layer uses the [output] schema's `format = "html"` hint
to know which fields are rendered HTML (for storage and TUI display);
prompts and skill metadata themselves stay markdown/TOML for token
efficiency (per 2026 research — see SETUP.md).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SkillMeta(BaseModel):
    name: str
    version: str = "0.1"
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillRuntime(BaseModel):
    model_hint: Literal["small", "deep", "auto"] = "auto"
    tools: list[str] = Field(default_factory=lambda: ["search", "execute"])
    default_isolation: Literal["process", "docker", "gvisor", "microvm", "wasm"] = "process"

    @field_validator("tools")
    @classmethod
    def _check_tools(cls, v: list[str]) -> list[str]:
        unknown = [t for t in v if t not in ("search", "execute")]
        if unknown:
            raise ValueError(f"unsupported tools {unknown}; only search/execute exist")
        return v or ["search", "execute"]


class SkillBudget(BaseModel):
    tokens: int = Field(default=20_000, ge=100)
    wall_seconds: int = Field(default=300, ge=1)
    joules: int = Field(default=0, ge=0)


class RequiredInput(BaseModel):
    """One thing the skill MUST be given before it can plan or run.

    The triage layer reads these to compose a clarifying question when the
    user's intent doesn't already include them. The match is by name in
    spec.inputs / spec.inputs.from_agents.
    """

    name: str
    type: Literal["path", "file", "directory", "string", "url", "list"] = "string"
    description: str = ""
    example: str = ""

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        if not v or not v.replace("_", "").isalnum():
            raise ValueError(f"name must be a simple identifier, got {v!r}")
        return v


class OptionalInput(RequiredInput):
    default: Any = None


class SkillRequires(BaseModel):
    inputs: list[RequiredInput] = Field(default_factory=list)
    optional: list[OptionalInput] = Field(default_factory=list)


class OutputProperty(BaseModel):
    """One field in the skill's structured output.

    format='html' marks this field as HTML — the storage layer (and TUI)
    will treat it as rich content. Default is plain text/markdown.
    """

    type: Literal["string", "array", "object", "path", "number", "boolean"] = "string"
    description: str = ""
    format: Literal["text", "markdown", "html", "json"] = "text"
    items: str | None = None  # for arrays — element type


class SkillOutput(BaseModel):
    schema_type: Literal["object", "string", "free"] = Field(default="object", alias="schema")
    required: list[str] = Field(default_factory=list)
    properties: dict[str, OutputProperty] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class SkillGraph(BaseModel):
    """Static bound on what this skill is allowed to spawn.

    Empty list means 'no child agents'. Use this to prevent runaway fan-outs
    from skills that should be leaves.
    """

    can_spawn: list[str] = Field(default_factory=list)


class SkillManifest(BaseModel):
    """The complete parsed skill.toml plus the markdown body."""

    meta: SkillMeta
    runtime: SkillRuntime = Field(default_factory=SkillRuntime)
    budget: SkillBudget = Field(default_factory=SkillBudget)
    requires: SkillRequires = Field(default_factory=SkillRequires)
    output: SkillOutput = Field(default_factory=SkillOutput)
    graph: SkillGraph = Field(default_factory=SkillGraph)

    # The markdown body — system prompt the agent sees.
    prompt: str = ""
    # Optional few-shots, appended below the prompt at runtime.
    examples: str = ""

    @property
    def role(self) -> str:
        return self.meta.name
