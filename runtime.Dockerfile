# Plnt micro-agent runtime image.
#
# Built once on the host: `docker build -t plnt/runtime:latest -f runtime.Dockerfile .`
# Spawned per agent by DockerSandbox. Small, fast, no model weights inside.

FROM python:3.12-slim

# ripgrep makes search() fast; trash + curl are useful execute() helpers.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ripgrep curl trash-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /plnt
COPY pyproject.toml ./
COPY plnt ./plnt

RUN pip install --no-cache-dir -e . \
    && mkdir -p /work /blackboard /roots

WORKDIR /work

# The container's only entrypoint: run one agent against the AgentSpec on stdin.
ENTRYPOINT ["python", "-m", "plnt.execution.runner"]
