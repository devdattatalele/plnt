package ui

import (
	"strings"
	"time"
)

// Turn is one round of the conversation.
type Turn struct {
	RunID      string
	Prompt     string
	Answer     string
	Source     string // "triage" | "agent" | "synth" | "fallback" | "clarify" | "" (in-flight)
	TriageKind string
	AgentCount int
	StartedAt  time.Time
	FinishedAt time.Time
	Workdirs   []string // distinct workdirs used by agents in this turn
	FileCount  int      // total files written across all agents
}

func (t Turn) Elapsed() time.Duration {
	end := t.FinishedAt
	if end.IsZero() {
		end = time.Now()
	}
	return end.Sub(t.StartedAt).Round(100 * time.Millisecond)
}

func (t Turn) InFlight() bool {
	return t.FinishedAt.IsZero()
}

// RenderTurns lays out the chat scroll, newest at the bottom.
func RenderTurns(turns []Turn, width int) string {
	if len(turns) == 0 {
		return Subtle.Render("  (no conversation yet — type an intent below)")
	}
	contentWidth := width - 6
	if contentWidth < 20 {
		contentWidth = 20
	}
	var parts []string
	for i, t := range turns {
		parts = append(parts, renderTurn(t, contentWidth))
		if i < len(turns)-1 {
			parts = append(parts, Subtle.Render(strings.Repeat("·", contentWidth/2)))
		}
	}
	return strings.Join(parts, "\n")
}

func renderTurn(t Turn, w int) string {
	var lines []string
	// User bubble
	lines = append(lines, Accent.Render("you  ")+wrap(t.Prompt, w-6))

	// Plnt bubble
	var prefix string
	switch t.Source {
	case "triage":
		prefix = Chat.Render("plnt ")
	case "clarify":
		prefix = Accent.Render("plnt ")
	case "agent":
		prefix = Answer.Render("plnt ")
	case "synth":
		prefix = Answer.Render("plnt ")
	case "fallback":
		prefix = Err.Render("plnt ")
	default:
		prefix = Subtle.Render("plnt ")
	}

	body := t.Answer
	if body == "" && t.InFlight() {
		body = Subtle.Render("(working…)")
	} else if body == "" {
		body = Subtle.Render("(no answer)")
	} else {
		body = wrap(body, w-6)
	}
	lines = append(lines, prefix+body)

	// Footer line — small metadata
	footer := []string{Subtle.Render(t.RunID)}
	if t.TriageKind != "" {
		footer = append(footer, Subtle.Render("triage="+t.TriageKind))
	}
	if t.AgentCount > 0 {
		footer = append(footer, Subtle.Render(formatAgentCount(t.AgentCount)))
	}
	if t.FileCount > 0 {
		footer = append(footer, Accent.Render(digitString(t.FileCount)+" file(s) written"))
	}
	if !t.InFlight() {
		footer = append(footer, Subtle.Render(t.Elapsed().String()))
	}
	lines = append(lines, "     "+strings.Join(footer, " · "))
	// Workdir line — show distinct paths used by this turn's agents.
	for _, wd := range t.Workdirs {
		lines = append(lines, "     "+Subtle.Render("📁 "+wd))
	}
	return strings.Join(lines, "\n")
}

func formatAgentCount(n int) string {
	if n == 1 {
		return "1 agent"
	}
	return rune2str(n) + " agents"
}

func rune2str(n int) string {
	if n < 10 {
		return string(rune('0' + n))
	}
	return digitString(n)
}

func digitString(n int) string {
	var out []byte
	for n > 0 {
		out = append([]byte{byte('0' + n%10)}, out...)
		n /= 10
	}
	if len(out) == 0 {
		return "0"
	}
	return string(out)
}
