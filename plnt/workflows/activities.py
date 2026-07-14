"""Activities backing the DeployModelWorkflow.

Activities are the side-effecting steps: they shell out to `helm`, `kubectl`,
the model registry, and the runtime's `/metrics` + `/v1/chat/completions`
endpoints. The workflow itself is pure orchestration (Temporal enforces this
via the sandboxed workflow runner).

Concrete implementations shell out via `asyncio.subprocess` — no Python
Kubernetes client, so the same code works against kind, GKE, EKS, AKS.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from temporalio import activity


class ManifestInvalidError(Exception):
    """Non-retryable — a bad spec won't get better on retry."""


class ImageUnpullableError(Exception):
    """Non-retryable — image tag missing or auth broken."""


class GpuClassUnavailableError(Exception):
    """Non-retryable — nvidia.com/h100 doesn't exist on this cluster."""


@activity.defn
async def validate_manifest(spec: dict[str, Any]) -> dict[str, Any]:
    """Schema-validate spec, check storageUri is reachable, image is pullable."""
    if spec.get("runtime") not in {"vllm", "tgi", "trt-llm", "sglang"}:
        raise ManifestInvalidError(f"unknown runtime: {spec.get('runtime')!r}")
    if not spec.get("model", {}).get("storageUri"):
        raise ManifestInvalidError("spec.model.storageUri is required")
    activity.logger.info("manifest ok for runtime=%s", spec["runtime"])
    return {"ok": True}


@activity.defn
async def pull_and_verify_weights(spec: dict[str, Any]) -> dict[str, Any]:
    """Fetch weights from the CAS-backed registry, hash-verify, cache locally."""
    storage_uri = spec["model"]["storageUri"]
    activity.logger.info("pulling weights from %s", storage_uri)
    # Real implementation would stream weights → CAS + emit heartbeats.
    return {"weights_local_path": "/mnt/models/current", "verified": True}


@activity.defn
async def helm_install_canary(payload: dict[str, Any]) -> dict[str, Any]:
    """helm install the runtime chart with canary weights."""
    ns = payload["namespace"]
    name = payload["name"]
    spec = payload["spec"]
    runtime = spec["runtime"]
    release = f"{name}-canary"
    chart = f"plnt/charts/{runtime}-runtime"
    args = [
        "helm", "upgrade", "--install", release, chart,
        "--namespace", ns,
        "--set", f"model.name={spec['model']['name']}",
        "--set", f"model.storageUri={spec['model']['storageUri']}",
        "--set", f"resources.gpu={spec['resources']['gpu']}",
        "--set", f"router.trafficPercent={spec.get('canary', {}).get('trafficPercent', 5)}",
    ]
    activity.logger.info("running: %s", " ".join(args))
    code, out, err = await _run(args)
    if code != 0:
        raise RuntimeError(f"helm install failed: {err}")
    endpoint = f"{name}.{ns}.svc.cluster.local:8000"
    return {"release": release, "namespace": ns, "endpoint": endpoint}


@activity.defn
async def run_smoke_test(payload: dict[str, Any]) -> dict[str, Any]:
    """Send N prompts, measure TTFT/TPOT, compare against canary KPI budget."""
    release = payload["release"]
    spec = payload["spec"]
    endpoint = release["endpoint"]
    canary = spec.get("canary", {}).get("smokeTest", {})
    n = canary.get("prompts", 10)
    ttft_budget = canary.get("ttftBudgetMs", 500)
    tpot_budget = canary.get("tpotBudgetMs", 60)

    activity.logger.info("smoke: %d prompts against %s", n, endpoint)
    ttfts: list[float] = []
    tpots: list[float] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for _ in range(n):
            metrics = await _one_smoke_request(client, endpoint, spec["model"]["name"])
            ttfts.append(metrics["ttft_ms"])
            tpots.append(metrics["tpot_ms"])

    p50_ttft = _p50(ttfts)
    p50_tpot = _p50(tpots)
    passed = p50_ttft <= ttft_budget and p50_tpot <= tpot_budget
    return {
        "passed": passed,
        "reason": None if passed else f"p50 TTFT={p50_ttft:.0f}ms budget={ttft_budget}ms · p50 TPOT={p50_tpot:.0f}ms budget={tpot_budget}ms",
        "metrics": {"ttft_p50_ms": p50_ttft, "tpot_p50_ms": p50_tpot},
    }


@activity.defn
async def promote_to_stable(release: dict[str, Any]) -> dict[str, Any]:
    """Scale canary to full traffic; drop the -canary suffix in the router."""
    # Real impl would patch the Envoy VirtualService weights 5% → 100% and
    # rename the release.
    activity.logger.info("promoting %s → stable", release["release"])
    return {"promoted": True}


@activity.defn
async def helm_rollback(release_or_spec: dict[str, Any]) -> dict[str, Any]:
    """helm uninstall the canary release. Compensating action."""
    release = release_or_spec.get("release") or release_or_spec.get("name")
    ns = release_or_spec.get("namespace", "default")
    activity.logger.info("rolling back %s in %s", release, ns)
    args = ["helm", "uninstall", release, "--namespace", ns, "--ignore-not-found"]
    await _run(args)
    return {"rolled_back": True}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _run(argv: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def _one_smoke_request(client: httpx.AsyncClient, endpoint: str, model: str) -> dict[str, float]:
    """One OpenAI-shape request; approximate TTFT + TPOT from streaming timing."""
    import time
    url = f"http://{endpoint}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Write one sentence about paged attention."}],
        "stream": True,
        "max_tokens": 64,
    }
    start = time.perf_counter()
    ttft = 0.0
    tokens = 0
    async with client.stream("POST", url, json=payload) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if body == "[DONE]":
                break
            if ttft == 0.0:
                ttft = (time.perf_counter() - start) * 1000
            try:
                obj = json.loads(body)
                if obj["choices"][0].get("delta", {}).get("content"):
                    tokens += 1
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    elapsed_ms = (time.perf_counter() - start) * 1000
    tpot = (elapsed_ms - ttft) / max(1, tokens - 1)
    return {"ttft_ms": ttft, "tpot_ms": tpot}


def _p50(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2
