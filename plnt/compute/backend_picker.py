"""Backend picker — decide local vs. cloud per call.

The personal-twin idea: when your external SSD with the local models is
mounted, route inference there (private, free, fast enough). When the SSD
is missing or the local URL is unreachable, fall back to a cloud OpenAI-
compatible API (OpenAI / Anthropic / Groq / Together). When neither, the
router's own offline echo kicks in.

This module owns the decision; the router owns the call. Decision is
deterministic and auditable — every switch is logged so a `grep cloud
events.jsonl` answers "did we leak anything off-device?"
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

BackendKind = Literal["local", "cloud", "offline"]


@dataclass
class BackendChoice:
    kind: BackendKind
    url: str
    model: str
    api_key: str = ""
    reason: str = ""

    def to_event(self) -> dict:
        # Never write the api_key into events. Audit log stays grep-safe.
        return {"kind": self.kind, "url": self.url, "model": self.model, "reason": self.reason}


def _ssd_mounted(required_path: str | None) -> bool:
    if not required_path:
        return True  # no requirement → considered mounted
    p = Path(required_path)
    try:
        return p.exists() and p.is_dir()
    except (PermissionError, OSError):
        return False


def _tcp_reachable(url: str, timeout: float = 0.5) -> bool:
    """Cheap liveness check: open a TCP socket to host:port."""
    try:
        from urllib.parse import urlparse

        u = urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = u.port or (443 if u.scheme == "https" else 80)
    except Exception:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ok(url: str, timeout: float = 1.0) -> bool:
    """Stronger check: HTTP GET on /v1/models or /api/tags."""
    candidates = []
    if url.endswith("/"):
        url = url[:-1]
    if "/v1" in url:
        candidates.append(url.rstrip("/") + "/models")
    else:
        candidates.append(url + "/api/tags")
        candidates.append(url + "/v1/models")
    for c in candidates:
        try:
            r = httpx.get(c, timeout=timeout)
            if r.status_code < 500:
                return True
        except Exception:
            continue
    return False


def choose(
    *,
    model_hint: Literal["small", "deep", "auto"] = "auto",
    require_path: str | None = None,
    local_url: str | None = None,
    local_small_model: str | None = None,
    local_deep_model: str | None = None,
    cloud_url: str | None = None,
    cloud_small_model: str | None = None,
    cloud_deep_model: str | None = None,
    cloud_api_key: str | None = None,
    force: Literal["auto", "local", "cloud", "offline"] = "auto",
) -> BackendChoice:
    """Decide which backend to use for this call.

    Order of precedence:
      1. `force` (debug / testing / cost-cap override)
      2. local if SSD mounted AND endpoint healthy
      3. cloud if URL + key configured AND endpoint reachable
      4. offline → router falls back to its deterministic echo path
    """
    require_path = require_path or os.environ.get("PLNT_REQUIRED_PATH") or None
    local_url = local_url or os.environ.get("PLNT_LOCAL_URL") or os.environ.get("PLNT_COMPUTE_URL") or "http://127.0.0.1:11434"
    local_small_model = local_small_model or os.environ.get("PLNT_PLANNER_MODEL", "llama3.2:3b")
    local_deep_model = local_deep_model or os.environ.get("PLNT_DEEP_MODEL", "llama3.1:8b")
    cloud_url = cloud_url or os.environ.get("PLNT_CLOUD_URL", "")
    cloud_small_model = cloud_small_model or os.environ.get("PLNT_CLOUD_SMALL_MODEL", "")
    cloud_deep_model = cloud_deep_model or os.environ.get("PLNT_CLOUD_DEEP_MODEL", cloud_small_model)
    cloud_api_key = cloud_api_key or os.environ.get("PLNT_CLOUD_API_KEY", "")

    if force == "local":
        return BackendChoice("local", local_url, _pick_model(model_hint, local_small_model, local_deep_model), reason="forced")
    if force == "cloud":
        return BackendChoice("cloud", cloud_url, _pick_model(model_hint, cloud_small_model, cloud_deep_model), api_key=cloud_api_key, reason="forced")
    if force == "offline":
        return BackendChoice("offline", "", "", reason="forced")

    # 2. Try local
    local_ok = _ssd_mounted(require_path) and _tcp_reachable(local_url)
    if local_ok:
        return BackendChoice(
            "local",
            local_url,
            _pick_model(model_hint, local_small_model, local_deep_model),
            reason=f"ssd_ok+local_ok path={require_path or 'none-required'}",
        )

    # 3. Try cloud
    if cloud_url and cloud_api_key and cloud_small_model:
        if _tcp_reachable(cloud_url):
            return BackendChoice(
                "cloud",
                cloud_url,
                _pick_model(model_hint, cloud_small_model, cloud_deep_model),
                api_key=cloud_api_key,
                reason=f"local_unavailable (ssd={_ssd_mounted(require_path)})",
            )

    # 4. Offline
    return BackendChoice(
        "offline", "", "",
        reason=f"no_backend_reachable (local={local_ok}, cloud_configured={bool(cloud_url and cloud_api_key)})",
    )


def _pick_model(hint: str, small: str, deep: str) -> str:
    if hint == "deep":
        return deep
    return small
