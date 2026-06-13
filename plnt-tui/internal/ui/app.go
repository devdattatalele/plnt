// Package ui is the Bubble Tea model for the live swarm view.
package ui

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/spinner"
	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/plnt/plnt-tui/internal/client"
)

// ---------------------------------------------------------------- messages

type connectedMsg struct{ h client.Health }
type connectErrMsg struct{ err error }
type runStartedMsg struct {
	id     string
	intent string
}
type submitErrMsg struct{ err error }
type eventMsg client.Event
type streamEndedMsg struct{}
type tickMsg struct{}

// ---------------------------------------------------------------- stages

type stage int

const (
	stageIdle stage = iota
	stageSubmitting
	stagePlanning
	stageRunning
	stageDone
)

func (s stage) String() string {
	switch s {
	case stageIdle:
		return "idle"
	case stageSubmitting:
		return "submitting"
	case stagePlanning:
		return "planner thinking"
	case stageRunning:
		return "agents running"
	case stageDone:
		return "done"
	}
	return "?"
}

// ---------------------------------------------------------------- model

type Model struct {
	cli     *client.Client
	ctx     context.Context
	cancel  context.CancelFunc
	width   int
	height  int
	input   textinput.Model
	log     viewport.Model
	logBuf  strings.Builder
	spinner spinner.Model
	swarm   *SwarmState
	health  client.Health
	connOK  bool
	connErr error
	stage   stage
	intent  string
}

var globalProgram *tea.Program

func SetProgram(p *tea.Program) { globalProgram = p }

func New(baseURL string) Model {
	ctx, cancel := context.WithCancel(context.Background())
	ti := textinput.New()
	ti.Placeholder = "type an intent and hit ⏎"
	ti.Focus()
	ti.CharLimit = 2000
	ti.Width = 80

	vp := viewport.New(80, 10)

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(lipgloss.Color("#FFD700"))

	return Model{
		cli:     client.New(baseURL),
		ctx:     ctx,
		cancel:  cancel,
		input:   ti,
		log:     vp,
		spinner: sp,
		stage:   stageIdle,
	}
}

func (m Model) Init() tea.Cmd {
	return tea.Batch(
		textinput.Blink,
		m.spinner.Tick,
		m.checkHealth(),
	)
}

// ---------------------------------------------------------------- commands

func (m Model) checkHealth() tea.Cmd {
	return func() tea.Msg {
		h, err := m.cli.Health(m.ctx)
		if err != nil {
			return connectErrMsg{err}
		}
		return connectedMsg{h}
	}
}

func (m Model) submit(text string) tea.Cmd {
	return func() tea.Msg {
		id, err := m.cli.Submit(m.ctx, text)
		if err != nil {
			return submitErrMsg{err}
		}
		return runStartedMsg{id: id, intent: text}
	}
}

// startStream kicks off the SSE subscription. Returns immediately.
// All subsequent events flow into the program via globalProgram.Send.
func (m *Model) startStream(runID string) tea.Cmd {
	return func() tea.Msg {
		ch := make(chan client.Event, 256)
		go m.cli.Subscribe(m.ctx, runID, ch)
		go func() {
			for e := range ch {
				if globalProgram != nil {
					globalProgram.Send(eventMsg(e))
				}
			}
			if globalProgram != nil {
				globalProgram.Send(streamEndedMsg{})
			}
		}()
		return nil // no immediate message
	}
}

// ---------------------------------------------------------------- update

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd

	switch msg := msg.(type) {

	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.layout()

	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+c", "ctrl+d":
			m.cancel()
			return m, tea.Quit
		case "enter":
			text := strings.TrimSpace(m.input.Value())
			if text != "" && m.stage == stageIdle || m.stage == stageDone {
				m.input.SetValue("")
				m.intent = text
				m.stage = stageSubmitting
				m.swarm = nil
				m.appendLog(Accent.Render("you  ") + text)
				cmds = append(cmds, m.submit(text))
			}
		case "esc":
			m.input.SetValue("")
		}

	case spinner.TickMsg:
		var c tea.Cmd
		m.spinner, c = m.spinner.Update(msg)
		cmds = append(cmds, c)

	case connectedMsg:
		m.connOK = true
		m.health = msg.h
		m.appendLog(OK.Render("✓ connected ") + Subtle.Render(fmt.Sprintf("v%s home=%s", msg.h.Version, msg.h.Home)))

	case connectErrMsg:
		m.connErr = msg.err
		m.appendLog(Err.Render("✗ surface unreachable: ") + msg.err.Error())

	case runStartedMsg:
		m.swarm = NewSwarm(msg.id, msg.intent)
		m.stage = stagePlanning
		m.appendLog(Accent.Render("⤳    ") + "run started " + Subtle.Render(msg.id))
		cmds = append(cmds, m.startStream(msg.id))

	case submitErrMsg:
		m.stage = stageIdle
		m.appendLog(Err.Render("✗ submit failed: ") + msg.err.Error())

	case eventMsg:
		evt := client.Event(msg)
		if m.swarm != nil {
			m.swarm.Apply(evt)
		}
		m.appendEvent(evt)
		// Stage transitions driven by events:
		switch evt.Kind {
		case "plan":
			m.stage = stageRunning
		case "spawn", "started":
			if m.stage == stagePlanning {
				m.stage = stageRunning
			}
		case "finished":
			if evt.AgentID == "" { // run-level finished
				m.stage = stageDone
			}
		}

	case streamEndedMsg:
		if m.stage != stageDone {
			m.stage = stageDone
		}
		m.appendLog(Subtle.Render("(stream ended)"))
	}

	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	cmds = append(cmds, cmd)

	return m, tea.Batch(cmds...)
}

// ---------------------------------------------------------------- view

func (m Model) View() string {
	if m.width == 0 {
		return "starting…"
	}
	header := m.renderHeader()
	swarm := m.renderSwarm()
	log := Box.Width(m.width - 2).Render(m.log.View())
	prompt := m.renderPrompt()
	return lipgloss.JoinVertical(lipgloss.Left, header, swarm, log, prompt)
}

func (m Model) renderHeader() string {
	state := "disconnected"
	color := Err
	if m.connOK {
		state = "connected"
		color = OK
	}
	plnt := Title.Render("plnt")
	st := color.Render("● " + state)
	url := Subtle.Render(m.cli.BaseURL)
	help := Subtle.Render("⏎ submit · ⎋ clear · ^C quit")
	return lipgloss.NewStyle().Padding(0, 1).Render(
		fmt.Sprintf("%s  %s  %s   %s", plnt, st, url, help),
	)
}

func (m Model) renderSwarm() string {
	// Always render the panel — even when idle — so the user has somewhere
	// to watch state appear.
	header := ""
	switch m.stage {
	case stageIdle:
		header = Subtle.Render("waiting for an intent. type one below and press ⏎.")
	case stageSubmitting:
		header = fmt.Sprintf("%s submitting intent…", m.spinner.View())
	case stagePlanning:
		header = fmt.Sprintf("%s planner thinking — deciding how many agents to spawn…", m.spinner.View())
	case stageRunning:
		if m.swarm != nil {
			header = fmt.Sprintf("%s %d agent(s) running · %d killed", m.spinner.View(),
				len(m.swarm.Agents), m.swarm.Killed)
		} else {
			header = fmt.Sprintf("%s running", m.spinner.View())
		}
	case stageDone:
		header = OK.Render("✓ done")
	}

	rows := []string{header}

	if m.intent != "" {
		rows = append(rows, Subtle.Render("intent: ")+m.intent)
	}
	if m.swarm != nil {
		rows = append(rows, Subtle.Render(fmt.Sprintf("run %s · %s", m.swarm.RunID, m.swarm.Plan)))
		rows = append(rows, "")

		agents := m.swarm.Sorted()
		if len(agents) == 0 {
			rows = append(rows, Subtle.Render("  (no agents spawned yet — waiting for plan event)"))
		}
		for _, a := range agents {
			rows = append(rows, m.renderAgentRow(a))
		}
	}

	body := strings.Join(rows, "\n")
	style := Box.Width(m.width - 2)
	if m.stage == stageRunning || m.stage == stagePlanning || m.stage == stageSubmitting {
		style = BoxFocused.Width(m.width - 2)
	}
	return style.Render(body)
}

func (m Model) renderAgentRow(a *AgentView) string {
	var st string
	switch a.Status {
	case "running":
		st = AgentRunning.Render("● running")
	case "done":
		st = AgentDone.Render("✓ done   ")
	case "killed":
		st = AgentKilled.Render("☠ killed ")
	case "error":
		st = AgentKilled.Render("☠ error  ")
	case "spawned":
		st = Subtle.Render("◌ spawned")
	default:
		st = Subtle.Render(a.Status)
	}
	tail := ""
	if a.LastTool != "" {
		tail = Subtle.Render(fmt.Sprintf("· %s(%s)", a.LastTool, a.LastArgs))
	}
	return fmt.Sprintf("  %s  %-14s  %s  %2d tools  %5s  %s",
		st, idShort(a.ID), Accent.Render(padRight(a.Role, 18)), a.ToolCalls, a.Elapsed(), tail)
}

func (m Model) renderPrompt() string {
	prefix := "> "
	if m.stage == stageSubmitting || m.stage == stagePlanning || m.stage == stageRunning {
		prefix = m.spinner.View() + " "
	}
	return Box.Width(m.width - 2).Render(prefix + m.input.View())
}

func (m *Model) layout() {
	if m.width < 40 || m.height < 10 {
		return
	}
	m.input.Width = m.width - 8
	// Reserve: header (1) + swarm panel (variable) + prompt (3). Give the log
	// the rest.
	logHeight := m.height - 18
	if logHeight < 4 {
		logHeight = 4
	}
	m.log.Width = m.width - 4
	m.log.Height = logHeight
}

func (m *Model) appendLog(line string) {
	if m.logBuf.Len() > 0 {
		m.logBuf.WriteString("\n")
	}
	m.logBuf.WriteString(line)
	m.log.SetContent(m.logBuf.String())
	m.log.GotoBottom()
}

func (m *Model) appendEvent(e client.Event) {
	pre := ""
	switch e.Kind {
	case "plan":
		pre = Accent.Render("plan  ")
	case "spawn":
		pre = Accent.Render("spawn ")
	case "started":
		pre = OK.Render("start ")
	case "tool_call":
		pre = Subtle.Render("tool→ ")
	case "tool_result":
		pre = Subtle.Render("←tool ")
	case "model_call":
		pre = Subtle.Render("model→")
	case "model_result":
		pre = Subtle.Render("←model")
	case "result":
		pre = OK.Render("✓ res ")
	case "error":
		pre = Err.Render("✗ err ")
	case "killed":
		pre = Err.Render("☠ kill")
	case "finished":
		pre = OK.Render("end   ")
	case "intent":
		pre = Accent.Render("intent")
	case "planner_start":
		pre = Accent.Render("plan→ ")
	default:
		pre = Subtle.Render(fmt.Sprintf("%-6s", e.Kind))
	}
	short := compactPayload(e)
	id := e.AgentID
	if id == "" {
		id = "-"
	}
	m.appendLog(fmt.Sprintf("%s %s %s", pre, Subtle.Render(idShort(id)), short))
}

func compactPayload(e client.Event) string {
	if e.Payload == nil {
		return ""
	}
	switch e.Kind {
	case "tool_call":
		t, _ := e.Payload["tool"].(string)
		args, _ := e.Payload["args"].(map[string]interface{})
		return fmt.Sprintf("%s(%s)", t, compactArgs(args))
	case "tool_result":
		t, _ := e.Payload["tool"].(string)
		ok, _ := e.Payload["ok"].(bool)
		if ok {
			return fmt.Sprintf("%s ok", t)
		}
		return fmt.Sprintf("%s FAILED", t)
	case "spawn":
		role, _ := e.Payload["role"].(string)
		return fmt.Sprintf("role=%s", role)
	case "plan":
		c, _ := e.Payload["agent_count"].(float64)
		return fmt.Sprintf("agents=%d", int(c))
	case "killed", "error":
		r, _ := e.Payload["reason"].(string)
		if len(r) > 70 {
			r = r[:67] + "…"
		}
		return r
	case "finished":
		rc, ok := e.Payload["exit_code"].(float64)
		if ok {
			w, _ := e.Payload["wall_seconds"].(float64)
			return fmt.Sprintf("exit=%d wall=%.1fs", int(rc), w)
		}
		// run-level finished has no exit_code
		s, _ := e.Payload["spawned"].(float64)
		return fmt.Sprintf("spawned=%d", int(s))
	case "intent":
		t, _ := e.Payload["text"].(string)
		if len(t) > 70 {
			t = t[:67] + "…"
		}
		return t
	}
	return ""
}

func idShort(s string) string {
	if len(s) > 12 {
		return s[:12]
	}
	return s
}

func padRight(s string, n int) string {
	if len(s) >= n {
		return s
	}
	return s + strings.Repeat(" ", n-len(s))
}
