# Setting up Plnt

A step-by-step guide that works on **any Mac or Linux machine**. No paths are hardcoded to one developer's box. Pick the variant that matches your machine.

> If something doesn't work, jump to [Troubleshooting](#troubleshooting) at the bottom.

---

## TL;DR (Mac, default paths, local model only)

```bash
# 1. system prereqs
brew install python@3.12 ollama git ripgrep

# 2. clone + install
git clone <your-fork-or-repo-url> plnt && cd plnt
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 3. pull a small model
brew services start ollama
ollama pull llama3.2:3b

# 4. set up the plnt home
export PLNT_HOME="$HOME/.plnt"
mkdir -p "$PLNT_HOME/skills"
cp skills/*.md "$PLNT_HOME/skills/"

# 5. run something
plnt submit "list five interesting things in $PWD"
cat ~/Desktop/plnt-*.md | tail -30   # or open the latest in your editor
```

If those commands succeed, you have a working personal twin. The rest of this document is for advanced setups: external-SSD model storage, cloud fallback when the SSD isn't attached, Docker isolation, multi-device access, autostart.

---

## 1. Prerequisites

You need:

| Tool | Why | Minimum version |
|---|---|---|
| Python | the runtime | 3.11+ |
| pip / venv | dependency install | bundled with Python |
| git | clone + skill versioning | any recent |
| ripgrep (optional) | makes `search()` fast; we fall back to pure-Python | any |
| Ollama (or any OpenAI-compat LLM server) | the brain | latest |
| Docker (optional) | container sandbox + parallel agents | 24+ |

### Install on macOS

```bash
brew install python@3.12 git ripgrep ollama
# Docker only if you want the docker sandbox rung
brew install --cask docker
```

### Install on Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv git ripgrep curl
# Ollama
curl -fsSL https://ollama.com/install.sh | sh
# Docker (only if you want the docker rung)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # log out + back in
```

### Install on Fedora / RHEL

```bash
sudo dnf install -y python3.12 git ripgrep curl
curl -fsSL https://ollama.com/install.sh | sh
# Docker:
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

---

## 2. Install Plnt

```bash
git clone <repo-url> plnt
cd plnt

# Make a venv inside the repo (recommended — keeps everything self-contained)
python3.12 -m venv .venv
source .venv/bin/activate

# Install the package + dev deps (pytest, ruff, mypy)
pip install -e ".[dev]"

# Sanity check
plnt --version           # should print: plnt, version 0.0.1
pytest -q                # should pass: 35 tests
```

If `python3.12` isn't on your PATH but `python3` works, use that instead. Plnt needs Python 3.11+.

---

## 3. Pick where Plnt stores its state (`$PLNT_HOME`)

Plnt keeps everything mutable under one directory. **Default is `~/.plnt`** but you can put it anywhere.

```bash
# Default
export PLNT_HOME="$HOME/.plnt"

# Or on an external SSD (path with a space — quote it!)
export PLNT_HOME="/Volumes/my-ssd/plnt"

# Or on a shared NFS mount on Linux
export PLNT_HOME="/mnt/family-nas/plnt"

# Make Plnt remember this across shells
echo "export PLNT_HOME=\"$PLNT_HOME\"" >> ~/.zshrc    # or ~/.bashrc
```

Now seed it with the bundled skills:

```bash
mkdir -p "$PLNT_HOME/skills"
cp skills/*.md "$PLNT_HOME/skills/"
plnt skills list   # should show the seeds
```

Inside `$PLNT_HOME` Plnt will create on demand:
```
runs/<run_id>/events.jsonl     # per-run audit log
runs/<run_id>/artifacts/       # spilled large payloads + outputs
skills/<role>.md               # skill bundles
episodic/YYYY/MM/DD.jsonl      # long-term memory (future)
index/                         # derived semantic index (future)
identity.toml                  # planner identity (future)
```

---

## 4. Pick a model backend

You have three options. Most people start with (A).

### (A) Local Ollama (free, private, the default)

```bash
# Mac
brew services start ollama
# Linux
sudo systemctl enable --now ollama || ollama serve &

# Pull a small + a deep model
ollama pull llama3.2:3b      # ~2 GB, fast for routing
ollama pull llama3.1:8b      # ~4.7 GB, deeper reasoning

# Verify
curl -s http://127.0.0.1:11434/api/tags | head

# Tell Plnt where it is
export PLNT_COMPUTE_URL="http://127.0.0.1:11434"
export PLNT_PLANNER_MODEL="llama3.2:3b"
export PLNT_DEEP_MODEL="llama3.1:8b"
```

#### Move the Ollama models to an external SSD (optional)

To keep your internal disk light:

```bash
SSD_MODELS="/Volumes/my-ssd/ollama-models"     # quote if it contains spaces
mkdir -p "$SSD_MODELS"

# Option 1 — run Ollama with the env var
export OLLAMA_MODELS="$SSD_MODELS"
ollama serve     # foreground

# Option 2 — symlink (works with `brew services start ollama` or systemd)
ollama list                              # confirm no models you'd lose
mv "$HOME/.ollama/models" "$HOME/.ollama/models.bak" 2>/dev/null || true
ln -s "$SSD_MODELS" "$HOME/.ollama/models"
brew services restart ollama             # Mac
# sudo systemctl restart ollama          # Linux

# Pull as normal — they land on the SSD
ollama pull llama3.2:3b
du -sh "$SSD_MODELS"
```

### (B) Cloud OpenAI-compat API (when local isn't available)

Works with **OpenAI**, **Groq**, **Together**, **OpenRouter**, **Anthropic** (via their compat shim), and anything else that speaks `POST /v1/chat/completions`.

```bash
# Example: OpenAI
export PLNT_CLOUD_URL="https://api.openai.com"
export PLNT_CLOUD_API_KEY="sk-..."
export PLNT_CLOUD_SMALL_MODEL="gpt-4o-mini"
export PLNT_CLOUD_DEEP_MODEL="gpt-4o"

# Example: Groq (cheap, fast)
export PLNT_CLOUD_URL="https://api.groq.com/openai"
export PLNT_CLOUD_API_KEY="gsk_..."
export PLNT_CLOUD_SMALL_MODEL="llama-3.1-8b-instant"
export PLNT_CLOUD_DEEP_MODEL="llama-3.3-70b-versatile"

# Example: Together
export PLNT_CLOUD_URL="https://api.together.xyz"
export PLNT_CLOUD_API_KEY="..."
export PLNT_CLOUD_SMALL_MODEL="meta-llama/Llama-3.2-3B-Instruct-Turbo"
```

### (C) Automatic switch: local when SSD present, cloud otherwise

This is the killer setup. Plnt's **backend picker** decides per call.

```bash
# Tell Plnt the SSD must be mounted for local to count
export PLNT_REQUIRED_PATH="/Volumes/my-ssd/ollama-models"

# Configure local (it's the preference)
export PLNT_LOCAL_URL="http://127.0.0.1:11434"
export PLNT_PLANNER_MODEL="llama3.2:3b"

# Configure cloud (it's the fallback)
export PLNT_CLOUD_URL="https://api.groq.com/openai"
export PLNT_CLOUD_API_KEY="gsk_..."
export PLNT_CLOUD_SMALL_MODEL="llama-3.1-8b-instant"
```

Now:
- SSD mounted + Ollama running -> **local**, private, free.
- SSD unplugged -> **cloud**, automatic, no code change.
- Both unreachable -> **offline echo** (search-and-summarize only).

Every switch is logged in the run's `events.jsonl` so you can audit "did anything leave the box?" with `grep -F '"backend":"cloud"'`.

### (D) Force a backend (for testing / cost cap)

In Python:
```python
from plnt.compute.router import LLMRouter
r = LLMRouter(force="local")    # never use cloud, even if local is down
```

---

## 5. Run your first intent

```bash
# Inline (no server needed)
plnt submit "find anything about TODO comments in $HOME/Documents"

# Or boot the surface server in one terminal:
plnt up
#   ↳ binds 127.0.0.1:7777 by default

# And in another terminal:
plnt submit --remote "summarise the README in this folder"
plnt runs                          # list recent runs
plnt tail r-XXXXXXXX --follow      # stream events live
```

What just happened:

1. Surface received your intent.
2. Orchestrator picked a skill from `$PLNT_HOME/skills/`.
3. A micro-agent was spawned in the **process** sandbox (rung 0).
4. The runner called your backend (local Ollama / cloud / offline).
5. The model emitted `TOOL: {...}` or `FINAL: ...`; the runner dispatched.
6. A markdown result landed on your Desktop (or wherever you configure).
7. The full audit trail is at `$PLNT_HOME/runs/<run_id>/events.jsonl`.

---

## 6. (Optional) Switch to the Docker sandbox

When you want **CPU/memory caps** and **parallel-safe execution**, use the docker rung.

```bash
# 1. Build the runtime image once
docker build -t plnt/runtime:latest -f runtime.Dockerfile .

# 2. Verify
docker run --rm plnt/runtime:latest --help 2>&1 | head

# 3. Tell Plnt to use it (set the spec default OR per-spawn)
export PLNT_DOCKER_IMAGE="plnt/runtime:latest"
export PLNT_DOCKER_CPUS=1.0          # 1 CPU per agent
export PLNT_DOCKER_MEM=1g            # 1 GB RAM per agent

# 4. In a skill front-matter, declare isolation:
#    ---
#    isolation: docker
#    ---
#    or pass it explicitly via the Python API:
```

```python
from plnt.execution.spec import AgentSpec
spec = AgentSpec(role="general-helper", run_id="r-1", isolation="docker", ...)
```

Monitoring while agents run:

```bash
docker ps --filter "label=dev.plnt.agent=true"
docker stats $(docker ps -q --filter "label=dev.plnt.agent=true")
```

Cleanup if you ever orphan one:
```bash
docker rm -f $(docker ps -aq --filter "label=dev.plnt.agent=true")
```

---

## 7. (Optional) Make the server reachable from your phone / laptop

By default `plnt up` binds **127.0.0.1** — only the same machine.

### Easiest path: Tailscale

```bash
# install on Mac/Linux + on your phone (free for personal use)
brew install tailscale && sudo tailscale up
# Now bind plnt to 0.0.0.0 — Tailscale firewalls the rest of the internet
PLNT_SURFACE_HOST=0.0.0.0 plnt up
# On your phone, hit:   http://<machine-name>:7777/v1/health
```

### Self-signed TLS + LAN

For a "do it yourself" setup:

```bash
# Generate a self-signed cert (one-time)
mkdir -p "$PLNT_HOME/certs" && cd "$PLNT_HOME/certs"
openssl req -x509 -newkey rsa:4096 -days 365 -nodes \
  -keyout server.key -out server.crt \
  -subj "/CN=plnt.local"
cd -

# Edit plnt/surface/server.py's `run()` or pass ssl args to uvicorn directly:
.venv/bin/uvicorn plnt.surface.server:app \
  --host 0.0.0.0 --port 7777 \
  --ssl-keyfile "$PLNT_HOME/certs/server.key" \
  --ssl-certfile "$PLNT_HOME/certs/server.crt"

# Trust the cert on the phone (Settings -> Profiles).
# Hit https://<your-ip>:7777/v1/health
```

mTLS (client certs) is on the v0.1 roadmap.

---

## 8. (Optional) Autostart at login

### Mac (launchd)

```bash
cat > ~/Library/LaunchAgents/com.plnt.surface.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.plnt.surface</string>
  <key>ProgramArguments</key>
    <array>
      <string>/bin/zsh</string>
      <string>-lc</string>
      <string>source ~/path/to/plnt/.venv/bin/activate && plnt up</string>
    </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/plnt.out</string>
  <key>StandardErrorPath</key><string>/tmp/plnt.err</string>
</dict>
</plist>
PLIST
launchctl load ~/Library/LaunchAgents/com.plnt.surface.plist
```

### Linux (systemd user unit)

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/plnt.service <<EOF
[Unit]
Description=Plnt surface
After=network.target

[Service]
Type=simple
WorkingDirectory=$HOME/path/to/plnt
ExecStart=$HOME/path/to/plnt/.venv/bin/plnt up
Restart=on-failure
EnvironmentFile=$HOME/.plnt/env

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now plnt
journalctl --user -u plnt -f
```

Put your env vars in `~/.plnt/env`:
```
PLNT_HOME=/home/you/.plnt
PLNT_COMPUTE_URL=http://127.0.0.1:11434
PLNT_PLANNER_MODEL=llama3.2:3b
```

---

## 9. (Optional) Cron-driven autonomy

Make Plnt run a daily intent without you asking:

```bash
crontab -e
# Add:
0 7 * * *  PLNT_HOME=/home/you/.plnt /home/you/path/to/plnt/.venv/bin/plnt submit "scan ~/Downloads for things to triage and write a summary"
```

The agent runs, writes a markdown to `~/Desktop`, and exits. Costs ~0 if you're using local Ollama.

---

## 10. Add your own skill

A skill is one markdown file under `$PLNT_HOME/skills/`. Front-matter declares behaviour, body is the system prompt:

```markdown
---
tools: search,execute
model_hint: small             # or "deep"
tokens: 12000
wall_seconds: 180
---
You are <name>, a specialist for <task>.

You have search(pattern, root) and execute(argv). When you are done,
return one of:
  TOOL: {"tool": "search", "args": {"pattern": "...", "root": "..."}}
  FINAL: <plain-text answer>

Style: be brief, cite paths and line numbers, do not invent sources.
```

Plnt hot-reloads skills on file change — no restart needed.

To make the keyword router pick it, name the file something distinctive (`code-shepherd.md`) and either:
- include that word in your intent (`"have code-shepherd look at..."`), or
- (v0.1) declare keywords in the front-matter.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `plnt: command not found` | venv not activated | `source .venv/bin/activate` |
| `pip install -e .` fails on PEP 639 | very new setuptools + old metadata | already fixed in pyproject; pull latest |
| `events.jsonl` has `"backend":"offline"` | no model server reachable | `curl http://127.0.0.1:11434/api/tags`; check `OLLAMA_HOST` if remote |
| `tokens=0` in every model_result | echo fallback path | same as above; or wrong `PLNT_PLANNER_MODEL` |
| `ollama pull` fills internal disk | `OLLAMA_MODELS` not effective | use the symlink method (works under brew/systemd) |
| External SSD is slow | USB-A or USB-C 5 Gbps | upgrade to USB-C 10 Gbps or Thunderbolt |
| Plnt hangs after SSD sleeps | drive went idle | `pmset -a disksleep 0` on Mac |
| `docker` rung errors `cannot connect to Docker daemon` | Docker Desktop not running | start it; verify `docker ps` works |
| Docker can't reach Ollama on the host | container sees host as `host.docker.internal` on Mac, not on Linux | on Linux add `--add-host=host.docker.internal:host-gateway` or set `PLNT_DOCKER_NETWORK=host` |
| Phone can't reach `plnt up` | bound to 127.0.0.1 | set `PLNT_SURFACE_HOST=0.0.0.0` (and use Tailscale or self-signed TLS) |
| Cron run does nothing | env vars not inherited | put them in `~/.plnt/env` and `source` it in the cron line |

If you hit something not listed, the audit log tells you everything:

```bash
ls $PLNT_HOME/runs/                                          # newest runs
jq '.kind' $PLNT_HOME/runs/<run-id>/events.jsonl | sort | uniq -c
jq 'select(.kind=="error" or .kind=="killed")' $PLNT_HOME/runs/<run-id>/events.jsonl
```

---

## Minimal environment-variable reference

Source these from a single `env` file or your shell rc.

```bash
# Where Plnt keeps its state
PLNT_HOME="$HOME/.plnt"

# Surface server
PLNT_SURFACE_HOST="127.0.0.1"
PLNT_SURFACE_PORT="7777"
PLNT_LOG_LEVEL="info"

# Local backend (Ollama)
PLNT_LOCAL_URL="http://127.0.0.1:11434"
PLNT_PLANNER_MODEL="llama3.2:3b"
PLNT_DEEP_MODEL="llama3.1:8b"

# Required filesystem path for the backend picker to count "local" as available
PLNT_REQUIRED_PATH=""    # e.g. "/Volumes/my-ssd/ollama-models"

# Cloud fallback (any OpenAI-compatible API)
PLNT_CLOUD_URL=""        # e.g. https://api.groq.com/openai
PLNT_CLOUD_API_KEY=""    # bearer token
PLNT_CLOUD_SMALL_MODEL=""
PLNT_CLOUD_DEEP_MODEL=""

# Docker sandbox rung
PLNT_DOCKER_IMAGE="plnt/runtime:latest"
PLNT_DOCKER_CPUS="1.0"
PLNT_DOCKER_MEM="1g"
PLNT_DOCKER_NETWORK="bridge"   # use "host" on Linux to reach host-side Ollama

# Ollama-side (not Plnt, but worth setting)
OLLAMA_MODELS="/Volumes/my-ssd/ollama-models"   # optional external storage
```

---

That's the whole setup. Once `plnt submit "..."` writes useful markdown to your Desktop, the rest is a matter of adding skills and pointing it at the parts of your life you want a twin for.
