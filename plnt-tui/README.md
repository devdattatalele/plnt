# plnt-tui

A live terminal client for the Plnt swarm. Built on the Charm stack ([bubbletea](https://github.com/charmbracelet/bubbletea) + [bubbles](https://github.com/charmbracelet/bubbles) + [lipgloss](https://github.com/charmbracelet/lipgloss)) — the same toolkit used by `gh`, `glow`, and `gum`.

Instead of writing markdown to your Desktop, Plnt now ships a chat-shaped TUI:

```
┌ plnt  ● connected  http://127.0.0.1:7777   ⏎ submit · ⎋ clear · ^C quit ┐
│ run r-abc1234567 · agents 3 · spawned 3 · killed 0                       │
│ planner emitted 3 agents                                                  │
│   ● running  a-deadbeef01  literature-scout    2 tools     1.2s  · search(pattern=arxiv) │
│   ● running  a-deadbeef02  paper-reader        0 tools     0.8s          │
│   ✓ done     a-deadbeef03  summarizer          1 tools     3.4s          │
└──────────────────────────────────────────────────────────────────────────┘
┌ event log ────────────────────────────────────────────────────────────────┐
│ ✓ connected v0.0.1 home=/Users/me/.plnt                                  │
│ you  catch me up on agent memory papers                                  │
│ plan  a-deadbeef01 agents=3                                              │
│ spawn a-deadbeef01 role=literature-scout                                 │
│ start a-deadbeef01                                                       │
│ tool  a-deadbeef01 search(pattern=memory root=~/Documents/papers)        │
│ ← res a-deadbeef01 search ok                                             │
│ ...                                                                       │
└──────────────────────────────────────────────────────────────────────────┘
┌ > _                                                                       ┐
└──────────────────────────────────────────────────────────────────────────┘
```

## Build

```bash
cd plnt-tui
go build -o ../plnt-tui-bin ./cmd/plnt-tui
```

You get a single `plnt-tui-bin` (~10 MB). Move it onto $PATH if you like.

## Run

In one terminal — start the plnt surface (Python):

```bash
cd /path/to/plnt
source .venv/bin/activate
plnt up
```

In another terminal — start the TUI:

```bash
./plnt-tui-bin
# or
./plnt-tui-bin -url http://127.0.0.1:7777
# or via env
PLNT_SURFACE_URL=http://my-server:7777 ./plnt-tui-bin
```

Type your intent at the bottom prompt, hit `⏎`, watch the swarm work in real time. `⎋` clears the input. `^C` quits.

## What's shown

- **Header** — surface health, version, the URL you're talking to, the keybinds.
- **Swarm panel** — every agent the planner spawned for this run, with status, role, tool-call count, elapsed time, and what tool it just called.
- **Event log** — the chronological event stream (intent → plan → spawn → started → tool_call → tool_result → result → finished). Same data as `cat events.jsonl` but coloured and live.
- **Prompt** — text input. Disabled while a run is in flight (planner is busy).

## Architecture

```
plnt-tui/
  cmd/plnt-tui/main.go        — entry; sets up tea.Program
  internal/client/sse.go      — HTTP + SSE reader for the plnt surface
  internal/ui/app.go          — root tea.Model
  internal/ui/agents.go       — SwarmState: derives agent status from events
  internal/ui/styles.go       — lipgloss palette
```

The TUI is a thin client. All state derives from the SSE stream coming from `/v1/runs/{id}/stream`. No business logic; the planner, sandboxes, budget, and ACC all live in the Python surface.

## Why Bubble Tea

It's the de-facto Go TUI framework as of 2026 — used by `gh`, `glow`, `gum`, `soft-serve`, `wishlist`, `vhs`. Elm-architecture model/update/view that maps cleanly to "incoming SSE event → state update → re-render."

## Status

v0 minimum-viable. Known gaps:

- Doesn't yet show the agents' **final answers** inline — only that they finished. The `result` event payload is parsed but not rendered into the swarm panel. (Easy next step.)
- No scroll-back on the event log past what fits the viewport.
- Doesn't subscribe across reconnects of the surface — restart the TUI if the surface restarts.

None of those are dealbreakers; they're polish.
