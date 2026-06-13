"""Shared fixtures.

Every test gets a private $PLNT_HOME under tmp_path so no test ever touches
the user's real ~/.plnt.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PLNT_HOME", str(tmp_path / "plnt"))
    # Make sure every module that already imported `paths` sees the new value.
    import plnt.config as cfg

    cfg.paths().ensure()
    yield tmp_path
