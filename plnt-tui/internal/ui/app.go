// Package ui is the Bubble Tea model for the live swarm view.
package ui

import (
	"context"
	"fmt"
	"strings"
	"time"

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
	chatVP  viewport.Model
	turns   []Turn
	spinner spinner.Model
	swarm   *SwarmState // only for in-flight working strip
	health  client.Health
	connOK  bool
	connErr error
	stage   stage
}

var globalProgram *tea.Program

func SetProgram(p *tea.Program) { globalProgram = p }

func New(baseURL string) Model {
	ctx, cancel := context.WithCancel(context.Background())
	ti := textinput.New()
	ti.Prompt = "› "
	ti.Placeholder = "type a message and hit ⏎"
	ti.Focus()
	ti.CharLimit = 4000
	ti.Width = 80

	vp := viewport.New(80, 12)

	sp := spinner.New()
	sp.Spinner = spinner.Dot
	sp.Style = lipgloss.NewStyle().Foreground(colAccent)

	return Model{
		cli:     client.New(baseURL),
		ctx:     ctx,
		cancel:  cancel,
		input:   ti,
		chatVP:  vp,
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
	// Carry the last few completed turns as conversation memory.
	hist := make([]client.PriorTurn, 0, 6)
	for i := len(m.turns) - 1; i >= 0 && len(hist) < 6; i-- {
		t := m.turns[i]
		if t.InFlight() || t.Answer == "" {
			continue
		}
		hist = append([]client.PriorTurn{{Prompt: t.Prompt, Answer: t.Answer}}, hist...)
	}
	return func() tea.Msg {
		id, err := m.cli.Submit(m.ctx, text, hist)
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
		m.refreshChat()

	case tea.KeyMsg:
		switch msg.String() {
		case "ctrl+c", "ctrl+d":
			m.cancel()
			return m, tea.Quit
		case "ctrl+l":
			m.turns = nil
			m.refreshChat()
		case "enter":
			text := strings.TrimSpace(m.input.Value())
			canSubmit := m.stage == stageIdle || m.stage == stageDone
			if text != "" && canSubmit {
				m.input.SetValue("")
				m.stage = stageSubmitting
				m.swarm = nil
				// Optimistically add the turn; we'll fill RunID on runStartedMsg.
				m.turns = append(m.turns, Turn{Prompt: text, StartedAt: time.Now()})
				m.refreshChat()
				cmds = append(cmds, m.submit(text))
			}
		case "esc":
			m.input.SetValue("")
		case "up":
			m.chatVP.LineUp(1)
		case "down":
			m.chatVP.LineDown(1)
		case "pgup":
			m.chatVP.HalfViewUp()
		case "pgdown":
			m.chatVP.HalfViewDown()
		}

	case spinner.TickMsg:
		var c tea.Cmd
		m.spinner, c = m.spinner.Update(msg)
		cmds = append(cmds, c)
		// Re-render chat so in-flight turns animate their "working…"
		m.refreshChat()

	case connectedMsg:
		m.connOK = true
		m.health = msg.h

	case connectErrMsg:
		m.connErr = msg.err

	case runStartedMsg:
		m.swarm = NewSwarm(msg.id, msg.intent)
		m.stage = stageTriage
		// Attach run_id to the first un-IDed turn matching this intent.
		// Routing by intent prevents id-mixups when the user submits two
		// prompts in quick succession (the optimistic Turn for prompt #2
		// gets pushed before prompt #1's submit completes).
		for i := range m.turns {
			if m.turns[i].RunID == "" && m.turns[i].Prompt == msg.intent {
				m.turns[i].RunID = msg.id
				break
			}
		}
		m.refreshChat()
		cmds = append(cmds, m.startStream(msg.id))

	case submitErrMsg:
		m.stage = stageIdle
		if n := len(m.turns); n > 0 {
			m.turns[n-1].Answer = "submit failed: " + msg.err.Error()
			m.turns[n-1].Source = "fallback"
			m.turns[n-1].FinishedAt = time.Now()
		}
		m.refreshChat()

	case eventMsg:
		evt := client.Event(msg)
		if m.swarm != nil {
			m.swarm.Apply(evt)
		}
		m.applyEventToTurn(evt)
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
				if idx := m.findTurnIndex(evt.RunID); idx >= 0 {
					m.turns[idx].FinishedAt = time.Now()
				}
			}
		}
		m.refreshChat()

	case streamEndedMsg:
		if m.stage != stageDone {
			m.stage = stageDone
		}
		// Mark any still-running agents as 'lost' so they stop spinning at 31m.
		if m.swarm != nil {
			for _, av := range m.swarm.Agents {
				if av.Status == "running" || av.Status == "spawned" {
					av.Status = "killed"
					av.KillReason = "stream ended"
					av.FinishedAt = time.Now()
				}
			}
		}
		// Mark all unfinished turns as done so they stop spinning forever.
		for i := range m.turns {
			if m.turns[i].FinishedAt.IsZero() {
				m.turns[i].FinishedAt = time.Now()
			}
		}
		m.refreshChat()
	}

	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	cmds = append(cmds, cmd)

	return m, tea.Batch(cmds...)
}

// findTurnIndex returns the index of the Turn carrying runID, or -1.
func (m *Model) findTurnIndex(runID string) int {
	if runID == "" {
		return -1
	}
	for i := range m.turns {
		if m.turns[i].RunID == runID {
			return i
		}
	}
	return -1
}

// applyEventToTurn populates the Turn matching evt.RunID. Routing by
// run_id (not by position) prevents events from one run leaking into
// another when the user submits multiple prompts back-to-back.
func (m *Model) applyEventToTurn(evt client.Event) {
	idx := m.findTurnIndex(evt.RunID)
	if idx < 0 {
		return
	}
	t := &m.turns[idx]
	switch evt.Kind {
	case "triage":
		if k, ok := evt.Payload["kind"].(string); ok {
			t.TriageKind = k
		}
	case "plan":
		if c, ok := evt.Payload["agent_count"].(float64); ok {
			t.AgentCount = int(c)
		}
	case "answer":
		if text, ok := evt.Payload["text"].(string); ok {
			t.Answer = text
		}
		if src, ok := evt.Payload["source"].(string); ok {
			t.Source = src
		}
	case "finished":
		// Per-agent finished events carry workdir + file_count. Aggregate
		// them so the chat footer shows the total and 📁 paths appear.
		if evt.AgentID == "" {
			return
		}
		if wd, ok := evt.Payload["workdir"].(string); ok && wd != "" {
			seen := false
			for _, x := range t.Workdirs {
				if x == wd {
					seen = true
					break
				}
			}
			if !seen {
				t.Workdirs = append(t.Workdirs, wd)
			}
		}
		if fc, ok := evt.Payload["file_count"].(float64); ok {
			t.FileCount += int(fc)
		}
	}
}

// ---------------------------------------------------------------- view

func (m Model) View() string {
	if m.width < 60 || m.height < 16 {
		return "plnt: resize terminal to at least 60×16"
	}
	header := m.renderHeader()
	chat := m.renderChatPanel()
	live := m.renderLivePanel()
	prompt := m.renderPrompt()

	sections := []string{header, chat}
	if live != "" {
		sections = append(sections, live)
	}
	sections = append(sections, prompt)
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
	right := Subtle.Render("⏎ send · ⎋ clear · ^L wipe · ^C quit")
	gap := m.width - lipgloss.Width(left) - lipgloss.Width(right) - 2
	if gap < 1 {
		gap = 1
	}
	return lipgloss.NewStyle().Padding(0, 1).Render(left + strings.Repeat(" ", gap) + right)
}

func (m Model) renderChatPanel() string {
	w := m.width - 2
	style := PanelStyle(w, false)
	body := HeaderLabel("conversation") + "\n" + m.chatVP.View()
	return style.Render(body)
}

// renderLivePanel shows the currently-working swarm: stage, triage, agents.
// Hidden when idle.
func (m Model) renderLivePanel() string {
	if m.stage == stageIdle {
		return ""
	}
	w := m.width - 2
	style := PanelStyle(w, true)

	stageText := m.stage.String()
	statusLine := fmt.Sprintf("%s %s", m.spinner.View(), Accent.Render(stageText))
	if m.stage == stageDone {
		statusLine = OK.Render("✓ ") + Accent.Render("done")
	}

	rows := []string{HeaderLabel("working"), statusLine}

	if m.swarm != nil {
		// Triage line
		if m.swarm.TriageKind != "" {
			line := Subtle.Render("triage: ") + m.swarm.TriageKind
			if m.swarm.TriageKind == "chat" {
				line += Chat.Render("  (no agents — direct reply)")
			}
			rows = append(rows, line)
		}
		// Agent rows
		agents := m.swarm.Sorted()
		for _, a := range agents {
			rows = append(rows, m.renderAgentRow(a))
		}
	}

	// Limit the live panel to ~8 rows visually.
	const maxRows = 8
	if len(rows) > maxRows {
		rows = append(rows[:maxRows], Subtle.Render(fmt.Sprintf("  … +%d more", len(rows)-maxRows)))
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
	main := fmt.Sprintf("  %s %-13s  %s%s  %2d tools  %5s%s",
		st, idShort(a.ID), Accent.Render(padRight(a.Role, 22)), deps, a.ToolCalls, a.Elapsed(), tail)
	if a.Workdir != "" || a.FileCount > 0 {
		wd := shortenPath(a.Workdir, m.width-14)
		fc := ""
		if a.FileCount > 0 {
			fc = fmt.Sprintf(" · %d file(s)", a.FileCount)
		}
		main += "\n     " + Subtle.Render("📁 "+wd+fc)
	}
	// Show last few created files so the user sees work landing in real time.
	if len(a.Files) > 0 {
		recent := a.Files
		if len(recent) > 5 {
			recent = recent[len(recent)-5:]
		}
		for _, f := range recent {
			main += "\n        " + AgentDone.Render("+ "+f)
		}
	}
	return main
}

func shortenPath(p string, max int) string {
	if max < 20 || len(p) <= max {
		return p
	}
	return "…" + p[len(p)-max+1:]
}

func (m Model) renderPrompt() string {
	style := PanelStyle(m.width-2, m.stage == stageIdle || m.stage == stageDone)
	return style.Render(m.input.View())
}

// ---------------------------------------------------------------- layout

func (m *Model) layout() {
	if m.width < 40 || m.height < 10 {
		return
	}
	m.input.Width = m.width - 8
	// Live panel takes a fixed slot when visible. Chat gets the rest.
	chatHeight := m.height - 8 // header + prompt + live + breathing room
	if m.stage == stageIdle {
		chatHeight = m.height - 6
	}
	if chatHeight < 6 {
		chatHeight = 6
	}
	m.chatVP.Width = m.width - 6
	m.chatVP.Height = chatHeight
}

func (m *Model) refreshChat() {
	m.layout()
	body := RenderTurns(m.turns, m.width-2)
	m.chatVP.SetContent(body)
	m.chatVP.GotoBottom()
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
