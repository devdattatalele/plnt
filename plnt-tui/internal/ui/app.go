// Package ui is the Bubble Tea model for the live swarm view.
package ui

import (
	"context"
	"fmt"
	"strings"

	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/plnt/plnt-tui/internal/client"
)

// Messages
type connectedMsg struct{ h client.Health }
type connectErrMsg struct{ err error }
type runStartedMsg struct{ id string }
type submitErrMsg struct{ err error }
type eventMsg client.Event
type streamEndedMsg struct{}

// Model is the root tea.Model.
type Model struct {
	cli      *client.Client
	ctx      context.Context
	cancel   context.CancelFunc
	width    int
	height   int
	input    textinput.Model
	log      viewport.Model
	logBuf   strings.Builder
	swarm    *SwarmState
	health   client.Health
	connOK   bool
	connErr  error
	lastErr  string
	running  bool
}

func New(baseURL string) Model {
	ctx, cancel := context.WithCancel(context.Background())
	ti := textinput.New()
	ti.Placeholder = "type an intent and hit ⏎"
	ti.Focus()
	ti.CharLimit = 2000
	ti.Width = 80

	vp := viewport.New(80, 10)

	return Model{
		cli:    client.New(baseURL),
		ctx:    ctx,
		cancel: cancel,
		input:  ti,
		log:    vp,
	}
}

func (m Model) Init() tea.Cmd {
	return tea.Batch(
		textinput.Blink,
		m.checkHealth(),
	)
}

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
		return runStartedMsg{id}
	}
}

func (m *Model) subscribe(runID string) tea.Cmd {
	out := make(chan client.Event, 64)
	go m.cli.Subscribe(m.ctx, runID, out)
	return func() tea.Msg {
		evt, ok := <-out
		if !ok {
			return streamEndedMsg{}
		}
		// re-enqueue subsequent events
		go func() {
			for e := range out {
				// fire-and-forget; the program's update will be called via Program.Send
				if globalProgram != nil {
					globalProgram.Send(eventMsg(e))
				}
			}
			if globalProgram != nil {
				globalProgram.Send(streamEndedMsg{})
			}
		}()
		return eventMsg(evt)
	}
}

var globalProgram *tea.Program

func SetProgram(p *tea.Program) { globalProgram = p }

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
			if text != "" && !m.running {
				m.input.SetValue("")
				m.running = true
				m.appendLog(Accent.Render("you  ") + text)
				cmds = append(cmds, m.submit(text))
			}
		case "esc":
			// reset input on Esc
			m.input.SetValue("")
		}
	case connectedMsg:
		m.connOK = true
		m.health = msg.h
		m.appendLog(OK.Render("✓ connected ") + Subtle.Render(fmt.Sprintf("v%s home=%s", msg.h.Version, msg.h.Home)))
	case connectErrMsg:
		m.connErr = msg.err
		m.appendLog(Err.Render("✗ surface unreachable: ") + msg.err.Error())
	case runStartedMsg:
		m.swarm = NewSwarm(msg.id, m.lastIntent())
		m.appendLog(Accent.Render("⤳    ") + "run started " + Subtle.Render(msg.id))
		cmds = append(cmds, m.subscribe(msg.id))
	case submitErrMsg:
		m.running = false
		m.appendLog(Err.Render("✗ submit failed: ") + msg.err.Error())
	case eventMsg:
		evt := client.Event(msg)
		if m.swarm != nil {
			m.swarm.Apply(evt)
		}
		m.appendEvent(evt)
		if evt.Kind == "finished" && evt.AgentID == "" {
			m.running = false
		}
	case streamEndedMsg:
		m.running = false
		m.appendLog(Subtle.Render("(stream ended)"))
	}

	// input + viewport child updates
	var cmd tea.Cmd
	m.input, cmd = m.input.Update(msg)
	cmds = append(cmds, cmd)

	return m, tea.Batch(cmds...)
}

func (m Model) lastIntent() string {
	// We cleared the input on submit, so the intent comes from the log.
	// Cheap way: look for the most recent "you  " line.
	lines := strings.Split(m.logBuf.String(), "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		// drop ANSI color codes — naive but enough for retrieving the text
		l := stripANSI(lines[i])
		if strings.HasPrefix(l, "you  ") {
			return strings.TrimSpace(strings.TrimPrefix(l, "you  "))
		}
	}
	return ""
}

func (m *Model) layout() {
	if m.width < 40 || m.height < 10 {
		return
	}
	m.input.Width = m.width - 6
	logHeight := m.height - 8 - 12 // header + agents pane + input + spacing
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
		pre = Subtle.Render("tool  ")
	case "tool_result":
		pre = Subtle.Render("← res ")
	case "model_call":
		pre = Subtle.Render("model→")
	case "model_result":
		pre = Subtle.Render("←model")
	case "result":
		pre = OK.Render("✓ done")
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
		rc, _ := e.Payload["exit_code"].(float64)
		w, _ := e.Payload["wall_seconds"].(float64)
		return fmt.Sprintf("exit=%d wall=%.1fs", int(rc), w)
	}
	return ""
}

func idShort(s string) string {
	if len(s) > 12 {
		return s[:12]
	}
	return s
}

func (m Model) View() string {
	header := m.renderHeader()
	swarm := m.renderSwarm()
	log := Box.Width(m.width - 2).Render(m.log.View())
	prompt := Box.Width(m.width - 2).Render(m.input.View())

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
	if m.swarm == nil {
		return Box.Width(m.width - 2).Render(Subtle.Render("no run yet"))
	}
	header := fmt.Sprintf("run %s · agents %d · spawned %d · killed %d",
		m.swarm.RunID, len(m.swarm.Agents), m.swarm.Spawned, m.swarm.Killed)
	rows := []string{Subtle.Render(header)}
	rows = append(rows, Subtle.Render(m.swarm.Plan))

	for _, a := range m.swarm.Sorted() {
		st := Subtle.Render(a.Status)
		switch a.Status {
		case "running":
			st = AgentRunning.Render("● running")
		case "done":
			st = AgentDone.Render("✓ done   ")
		case "killed", "error":
			st = AgentKilled.Render("☠ " + a.Status + " ")
		}
		tail := ""
		if a.LastTool != "" {
			tail = Subtle.Render(fmt.Sprintf("· %s(%s)", a.LastTool, a.LastArgs))
		}
		rows = append(rows, fmt.Sprintf("  %s  %-22s  %s  %3d tools  %5s  %s",
			st, idShort(a.ID), Accent.Render(a.Role), a.ToolCalls, a.Elapsed(), tail))
	}
	body := strings.Join(rows, "\n")
	style := Box
	if m.running {
		style = BoxFocused
	}
	return style.Width(m.width - 2).Render(body)
}

// stripANSI removes ANSI escape sequences from a string.
func stripANSI(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == 0x1b {
			// skip until 'm'
			for i < len(s) && s[i] != 'm' {
				i++
			}
			continue
		}
		b.WriteByte(s[i])
	}
	return b.String()
}
