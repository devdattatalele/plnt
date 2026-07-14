"""Integrations — saved skill input values.

Each skill in skill.toml declares [[requires.inputs]] entries (paths, API
keys, etc.). The Integrations tab in the web UI lets the user set those
values once; the orchestrator merges them into AgentSpec.inputs at spawn
time. Persisted to ~/.plnt/integrations.toml:

    [<skill_role>]
    library_root = "/Users/dev16/Documents/research"
    arxiv_api_key = "..."
"""

from __future__ import annotations

import threading
import tomllib
from pathlib import Path
from typing import Any

from plnt.config import paths


def _escape_toml_string(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"')


class IntegrationsStore:
    """File-backed map of skill_role -> {input_name: value}."""

    def __init__(self, path: Path | None = None):
        self.path = path or (paths().home / "integrations.toml")
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        with open(self.path, "rb") as f:
            return tomllib.load(f)

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# plnt integrations — saved skill inputs", ""]
        for role in sorted(data.keys()):
            values = data[role] or {}
            if not values:
                continue
            lines.append(f"[{role}]")
            for k in sorted(values.keys()):
                v = values[k]
                if isinstance(v, bool):
                    lines.append(f"{k} = {'true' if v else 'false'}")
                elif isinstance(v, int | float):
                    lines.append(f"{k} = {v}")
                elif isinstance(v, list):
                    rendered = ", ".join(f'"{_escape_toml_string(str(item))}"' for item in v)
                    lines.append(f"{k} = [{rendered}]")
                else:
                    lines.append(f'{k} = "{_escape_toml_string(str(v))}"')
            lines.append("")
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def get_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return self._load()

    def get(self, role: str) -> dict[str, Any]:
        with self._lock:
            return self._load().get(role, {})

    def set(self, role: str, values: dict[str, Any]) -> None:
        with self._lock:
            data = self._load()
            data[role] = dict(values)
            self._write(data)


def merge_into_spec(spec, store: IntegrationsStore):
    """Return a copy of `spec` with stored integration values folded into inputs.

    Planner-supplied values always win — we only fill keys the spec doesn't
    already have. Reserved keys (workdir, search_roots, intent, depends_on)
    are never overwritten.
    """
    saved = store.get(spec.role)
    if not saved:
        return spec
    reserved = {"workdir", "search_roots", "intent", "depends_on"}
    new_inputs = dict(spec.inputs)
    for k, v in saved.items():
        if k in reserved:
            continue
        if k in new_inputs and new_inputs[k] not in (None, "", []):
            continue
        new_inputs[k] = v
    return spec.model_copy(update={"inputs": new_inputs})
