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

// ---------------------------------------------------------------- stages

type stage int

const (
	stageIdle stage = iota
	stageSubmitting
	stageTriage
	stagePlanning
	stageRunning
	stageSynth
	stageDone
)

func (s stage) String() string {
	switch s {
	case stageIdle:
		return "ready"
	case stageSubmitting:
		return "submitting"
	case stageTriage:
		return "triaging intent"
	case stagePlanning:
		return "planner thinking"
	case stageRunning:
		return "agents running"
	case stageSynth:
		return "synthesizing answer"
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
	logBuf  string
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
	ti.Prompt = "› "
	ti.Placeholder = "type an intent and hit ⏎"
	ti.Focus()
	ti.CharLimit = 2000
	ti.Width = 80

	vp := viewport.New(80, 8)

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colAccent)

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
	return tea.Batch(textinput.Blink, m.spinner.Tick, m.checkHealth())
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
		return nil
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
			canSubmit := m.stage == stageIdle || m.stage == stageDone
			if text != "" && canSubmit {
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
		m.stage = stageTriage
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
		switch evt.Kind {
		case "triage":
			if m.swarm != nil && m.swarm.TriageKind == "chat" {
				m.stage = stageDone
			} else {
				m.stage = stagePlanning
			}
		case "plan":
			m.stage = stageRunning
		case "synth_start":
			m.stage = stageSynth
		case "finished":
			if evt.AgentID == "" {
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
	if m.width < 60 || m.height < 16 {
		return "plnt: resize terminal to at least 60×16"
	}
	header := m.renderHeader()
	statusBar := m.renderStatus()
	swarm := m.renderSwarmPanel()
	answer := m.renderAnswerPanel()
	log := m.renderLogPanel()
	prompt := m.renderPrompt()

	sections := []string{header, statusBar, swarm}
	if answer != "" {
		sections = append(sections, answer)
	}
	sections = append(sections, log, prompt)

	return lipgloss.JoinVertical(lipgloss.Left, sections...)
}

func (m Model) renderHeader() string {
	state := "disconnected"
	color := Err
	if m.connOK {
		state = "connected"
		color = OK
	}
	left := fmt.Sprintf("%s  %s  %s",
		Title.Render("plnt"),
		color.Render("● "+state),
		Subtle.Render(m.cli.BaseURL),
	)
	right := Subtle.Render("⏎ submit · ⎋ clear · ^C quit")
	gap := m.width - lipgloss.Width(left) - lipgloss.Width(right) - 2
	if gap < 1 {
		gap = 1
	}
	return lipgloss.NewStyle().Padding(0, 1).Render(left + strings.Repeat(" ", gap) + right)
}

func (m Model) renderStatus() string {
	line := fmt.Sprintf("%s %s", m.spinner.View(), Accent.Render(m.stage.String()))
	if m.swarm != nil && m.swarm.TriageKind != "" {
		tag := Subtle.Render(fmt.Sprintf("triage=%s", m.swarm.TriageKind))
		line += "  " + tag
		if m.swarm.TriageReason != "" {
			line += "  " + Subtle.Render(m.swarm.TriageReason)
		}
	}
	if m.stage == stageDone {
		line = OK.Render("✓ ") + Accent.Render("done")
	}
	return lipgloss.NewStyle().Padding(0, 2).Render(line)
}

func (m Model) renderSwarmPanel() string {
	w := m.width - 2
	focused := m.stage == stageRunning || m.stage == stageSynth
	style := PanelStyle(w, focused)

	rows := []string{HeaderLabel("swarm")}
	if m.swarm == nil {
		rows = append(rows, Subtle.Render("  no run yet — type an intent below."))
		return style.Render(strings.Join(rows, "\n"))
	}

	rows = append(rows,
		Subtle.Render("intent: ")+m.swarm.Intent,
		Subtle.Render(fmt.Sprintf("run %s · %s", m.swarm.RunID, m.swarm.PlanText)),
	)

	if m.swarm.TriageKind == "chat" {
		rows = append(rows, Chat.Render("  (chat — no agents spawned, planner replied directly)"))
	} else {
		agents := m.swarm.Sorted()
		if len(agents) == 0 {
			rows = append(rows, Subtle.Render("  (waiting for the planner to emit agents…)"))
		}
		for _, a := range agents {
			rows = append(rows, m.renderAgentRow(a))
		}
	}

	return style.Render(strings.Join(rows, "\n"))
}

func (m Model) renderAgentRow(a *AgentView) string {
	var st string
	switch a.Status {
	case "running":
		st = AgentRunning.Render("● running ")
	case "done":
		st = AgentDone.Render("✓ done    ")
	case "killed":
		st = AgentKilled.Render("☠ killed  ")
	case "error":
		st = AgentKilled.Render("☠ error   ")
	case "spawned":
		st = AgentWaiting.Render("◌ pending ")
	default:
		st = AgentWaiting.Render(a.Status)
	}
	deps := ""
	if len(a.DependsOn) > 0 {
		deps = Subtle.Render(fmt.Sprintf(" ← %s", strings.Join(a.DependsOn, ",")))
	}
	tail := ""
	if a.LastTool != "" {
		tail = Subtle.Render(fmt.Sprintf(" · %s(%s)", a.LastTool, a.LastArgs))
	}
	return fmt.Sprintf("  %s %-13s  %s%s  %2d tools  %5s%s",
		st, idShort(a.ID), Accent.Render(padRight(a.Role, 22)), deps, a.ToolCalls, a.Elapsed(), tail)
}

func (m Model) renderAnswerPanel() string {
	if m.swarm == nil || m.swarm.Answer == "" {
		return ""
	}
	style := PanelStyle(m.width-2, m.stage == stageDone)
	source := m.swarm.AnswerSource
	if source == "" {
		source = "answer"
	}
	rows := []string{
		HeaderLabel("answer · " + source),
		wrap(m.swarm.Answer, m.width-6),
	}
	return style.BorderForeground(colAnswer).Render(strings.Join(rows, "\n"))
}

func (m Model) renderLogPanel() string {
	style := PanelStyle(m.width-2, false)
	body := HeaderLabel("event log") + "\n" + m.log.View()
	return style.Render(body)
}

func (m Model) renderPrompt() string {
	style := PanelStyle(m.width-2, m.stage == stageIdle || m.stage == stageDone)
	return style.Render(m.input.View())
}

func (m *Model) layout() {
	if m.width < 40 || m.height < 10 {
		return
	}
	m.input.Width = m.width - 8
	// Roughly: header(1) + status(1) + swarm(8-12) + answer(0 or 5) + log(?) + prompt(3)
	logHeight := m.height - 22
	if logHeight < 4 {
		logHeight = 4
	}
	m.log.Width = m.width - 6
	m.log.Height = logHeight
}

func (m *Model) appendLog(line string) {
	if m.logBuf != "" {
		m.logBuf += "\n"
	}
	m.logBuf += line
	if len(m.logBuf) > 200_000 {
		if i := strings.IndexByte(m.logBuf[50_000:], '\n'); i >= 0 {
			m.logBuf = m.logBuf[50_000+i+1:]
		}
	}
	m.log.SetContent(m.logBuf)
	m.log.GotoBottom()
}

func (m *Model) appendEvent(e client.Event) {
	pre := ""
	switch e.Kind {
	case "intent":
		pre = Accent.Render("intent")
	case "triage_start":
		pre = Subtle.Render("triage")
	case "triage":
		pre = Accent.Render("triage")
	case "planner_start":
		pre = Subtle.Render("plan→ ")
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
	case "answer":
		pre = Answer.Render("→ans  ")
	case "synth_start":
		pre = Accent.Render("synth ")
	case "error":
		pre = Err.Render("✗ err ")
	case "killed":
		pre = Err.Render("☠ kill")
	case "finished":
		pre = OK.Render("end   ")
	default:
		pre = Subtle.Render(fmt.Sprintf("%-6s", e.Kind))
	}
	short := compactPayload(e)
	id := e.AgentID
	if id == "" {
		id = "—"
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
	case "triage":
		kind, _ := e.Payload["kind"].(string)
		return kind
	case "answer":
		src, _ := e.Payload["source"].(string)
		return fmt.Sprintf("from %s", src)
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

// wrap word-wraps `text` to width `w`.
func wrap(text string, w int) string {
	if w < 10 {
		return text
	}
	var out strings.Builder
	for _, paragraph := range strings.Split(text, "\n") {
		words := strings.Fields(paragraph)
		line := ""
		for _, word := range words {
			if line == "" {
				line = word
				continue
			}
			if len(line)+1+len(word) > w {
				out.WriteString(line + "\n")
				line = word
			} else {
				line += " " + word
			}
		}
		if line != "" {
			out.WriteString(line)
		}
		out.WriteString("\n")
	}
	return strings.TrimRight(out.String(), "\n")
}
