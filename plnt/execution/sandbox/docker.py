"""Docker sandbox — rung 0.5 on the ladder.

Sits between the bare-process rung and the gVisor / Firecracker rungs.

Why we ship Docker even though the 2026 consensus says it's not enough for
LLM-emitted code: on a *personal* device running *trusted* skills the threat
model is different. The reason to use Docker is **operational**, not security:

  - Hard CPU + memory caps via `--cpus` and `--memory`.
  - Parallel spawns that can't starve the host (kernel scheduler enforces it).
  - `docker stats` becomes free monitoring.
  - Each spawn gets a clean filesystem; no leftover tempdirs.
  - Easy to teardown a runaway with `docker rm -f`.

Same Sandbox protocol as ProcessSandbox. Spec.isolation: "docker".
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from plnt.execution.blackboard import Blackboard
from plnt.execution.sandbox.base import SandboxResult
from plnt.execution.spec import AgentSpec

DEFAULT_IMAGE = os.environ.get("PLNT_DOCKER_IMAGE", "plnt/runtime:latest")
DEN_LABEL = "dev.plnt.agent"


class DockerSandbox:
    """One container per spawn. Streams JSONL events on stdout."""

    def __init__(
        self,
        blackboard: Blackboard,
        image: str | None = None,
        runner_module: str = "plnt.execution.runner",
    ):
        self.bb = blackboard
        self.image = image or DEFAULT_IMAGE
        self.runner_module = runner_module
        self._client = None
        self._container = None
        self._kill_reason = ""
        self._kill_lock = threading.Lock()

    # ------------------------------------------------------------- client

    def _docker(self):
        if self._client is None:
            try:
                import docker  # type: ignore[import-not-found]
            except ImportError as e:
                raise RuntimeError(
                    "docker SDK not installed. pip install docker"
                ) from e
            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as e:
                raise RuntimeError(f"Docker daemon unreachable: {e}") from e
        return self._client

    # ------------------------------------------------------------- run

    def run(self, spec: AgentSpec) -> SandboxResult:
        if shutil.which("docker") is None:
            raise RuntimeError("`docker` not on PATH; install Docker Desktop or colima")

        client = self._docker()
        started = time.monotonic()

        # Resource caps from spec.budget + sensible defaults.
        cpu_quota = float(os.environ.get("PLNT_DOCKER_CPUS", "1.0"))
        mem_limit = os.environ.get("PLNT_DOCKER_MEM", "1g")

        # Mounts: the run's blackboard dir is shared so events flow back.
        # The agent's search_roots are bind-mounted read-only.
        mounts = self._mounts_for(spec)

        env = self._env_for(spec)

        self.bb.emit(
            "spawn",
            agent_id=spec.id,
            payload={
                "role": spec.role,
                "parent_id": spec.parent_id,
                "depth": spec.depth,
                "isolation": "docker",
                "tools": spec.tools,
                "model_hint": spec.model_hint,
                "image": self.image,
                "cpu_quota": cpu_quota,
                "mem_limit": mem_limit,
            },
        )

        # Pipe the AgentSpec to the container's stdin via `docker run -i`.
        envelope = json.dumps(spec.model_dump(mode="json"))
        cmd = ["python", "-m", self.runner_module]

        try:
            self._container = client.containers.create(
                image=self.image,
                command=cmd,
                environment=env,
                mounts=mounts,
                stdin_open=True,
                tty=False,
                detach=True,
                labels={
                    DEN_LABEL: "true",
                    f"{DEN_LABEL}.run": self.bb.run_id,
                    f"{DEN_LABEL}.agent": spec.id,
                    f"{DEN_LABEL}.role": spec.role,
                },
                nano_cpus=int(cpu_quota * 1e9),
                mem_limit=mem_limit,
                network_mode=os.environ.get("PLNT_DOCKER_NETWORK", "bridge"),
                working_dir="/work",
            )
        except Exception as e:
            self.bb.emit("error", agent_id=spec.id, payload={"reason": f"docker create: {e}"})
            self.bb.emit("finished", agent_id=spec.id, payload={"exit_code": -1, "wall_seconds": 0})
            return SandboxResult(agent_id=spec.id, exit_code=-1, wall_seconds=0, killed=False)

        # Write the envelope and start the container.
        try:
            self._container.start()
            sock = self._container.attach_socket(params={"stdin": 1, "stream": 1})
            # The docker SDK returns a SocketIO-ish object. `_sock` is the raw socket.
            raw = getattr(sock, "_sock", sock)
            raw.sendall((envelope + "\n").encode("utf-8"))
            try:
                raw.shutdown(1)  # close stdin
            except Exception:
                pass
        except Exception as e:
            self.bb.emit("error", agent_id=spec.id, payload={"reason": f"docker start: {e}"})

        # Watchdog
        wd_stop = threading.Event()
        watchdog = threading.Thread(
            target=self._watchdog,
            args=(spec.id, spec.budget.wall_seconds, wd_stop),
            daemon=True,
        )
        watchdog.start()

        # Stream stdout → blackboard.
        events_out: list[dict] = []
        output: dict | None = None
        try:
            for chunk in self._container.logs(stream=True, follow=True, stdout=True, stderr=False):
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        self.bb.emit("log", agent_id=spec.id, payload={"raw": line})
                        continue
                    self.bb.emit(
                        evt.get("kind", "log"),
                        agent_id=evt.get("agent_id") or spec.id,
                        payload=evt.get("payload"),
                    )
                    events_out.append(evt)
                    if evt.get("kind") == "result":
                        output = evt.get("payload")
        except Exception as e:
            self.bb.emit("log", agent_id=spec.id, payload={"stream_err": str(e)})

        # Wait for exit + cleanup.
        try:
            res = self._container.wait(timeout=spec.budget.wall_seconds + 10)
            rc = int(res.get("StatusCode", -1))
        except Exception:
            rc = -1
        wd_stop.set()

        try:
            stderr = self._container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            if stderr.strip():
                self.bb.emit("log", agent_id=spec.id, payload={"stderr": stderr[:4000]})
        except Exception:
            pass

        try:
            self._container.remove(force=True)
        except Exception:
            pass

        wall = time.monotonic() - started
        self.bb.emit(
            "finished",
            agent_id=spec.id,
            payload={
                "exit_code": rc,
                "wall_seconds": round(wall, 3),
                "killed": bool(self._kill_reason),
                "kill_reason": self._kill_reason,
            },
        )

        return SandboxResult(
            agent_id=spec.id,
            exit_code=rc,
            output=output,
            events=events_out,
            wall_seconds=wall,
            killed=bool(self._kill_reason),
            kill_reason=self._kill_reason,
        )

    # ------------------------------------------------------------- kill

    def kill(self, agent_id: str, reason: str) -> bool:
        with self._kill_lock:
            c = self._container
            if c is None:
                return False
            self._kill_reason = reason
            self.bb.emit("killed", agent_id=agent_id, payload={"reason": reason})
            try:
                c.stop(timeout=5)
            except Exception:
                pass
            try:
                c.remove(force=True)
            except Exception:
                pass
            return True

    # ------------------------------------------------------------- helpers

    def _watchdog(self, agent_id: str, wall_seconds: int, stop: threading.Event) -> None:
        deadline = time.monotonic() + wall_seconds
        while not stop.is_set():
            if time.monotonic() > deadline:
                self.kill(agent_id, f"wall_seconds budget {wall_seconds}s exceeded")
                return
            time.sleep(0.5)

    def _mounts_for(self, spec: AgentSpec):
        from docker.types import Mount  # type: ignore[import-not-found]

        mounts = [
            Mount(target="/blackboard", source=str(self.bb.dir.resolve()), type="bind", read_only=False),
        ]
        roots = spec.inputs.get("search_roots", []) if isinstance(spec.inputs, dict) else []
        for i, r in enumerate(roots if isinstance(roots, list) else []):
            host = str(Path(str(r)).expanduser().resolve())
            if not Path(host).exists():
                continue
            mounts.append(
                Mount(target=f"/roots/r{i}", source=host, type="bind", read_only=True)
            )
        return mounts

    def _env_for(self, spec: AgentSpec) -> dict[str, str]:
        # Re-map host search_roots to in-container paths so search() works.
        roots = spec.inputs.get("search_roots", []) if isinstance(spec.inputs, dict) else []
        container_roots = []
        for i, _ in enumerate(roots if isinstance(roots, list) else []):
            container_roots.append(f"/roots/r{i}")
        return {
            "PLNT_AGENT_ID": spec.id,
            "PLNT_RUN_ID": spec.run_id,
            "PLNT_ROLE": spec.role,
            "PLNT_WORKDIR": "/work",
            "PLNT_BLACKBOARD_DIR": "/blackboard",
            "PLNT_SEARCH_ROOTS": ":".join(container_roots),
            "PLNT_COMPUTE_URL": os.environ.get("PLNT_COMPUTE_URL", "http://host.docker.internal:11434"),
            "PLNT_PLANNER_MODEL": os.environ.get("PLNT_PLANNER_MODEL", "llama3.2:3b"),
            "PLNT_DEEP_MODEL": os.environ.get("PLNT_DEEP_MODEL", "llama3.1:8b"),
            "PLNT_CLOUD_URL": os.environ.get("PLNT_CLOUD_URL", ""),
            "PLNT_CLOUD_SMALL_MODEL": os.environ.get("PLNT_CLOUD_SMALL_MODEL", ""),
            "PLNT_CLOUD_API_KEY": os.environ.get("PLNT_CLOUD_API_KEY", ""),
        }
