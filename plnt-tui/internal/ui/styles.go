package ui

import "github.com/charmbracelet/lipgloss"

var (
	colBorder        = lipgloss.Color("#5C5C5C")
	colBorderFocused = lipgloss.Color("#FFD700")
	colTitle         = lipgloss.Color("#7CFC00")
	colSubtle        = lipgloss.Color("#888888")
	colAccent        = lipgloss.Color("#FFD700")
	colErr           = lipgloss.Color("#FF6B6B")
	colOK            = lipgloss.Color("#7CFC00")
	colAnswer        = lipgloss.Color("#00BFFF")
	colChat          = lipgloss.Color("#FFAFD7")
)

var (
	Title = lipgloss.NewStyle().Bold(true).Foreground(colTitle)

	Subtle = lipgloss.NewStyle().Foreground(colSubtle)
	Accent = lipgloss.NewStyle().Foreground(colAccent)
	Err    = lipgloss.NewStyle().Foreground(colErr)
	OK     = lipgloss.NewStyle().Foreground(colOK)
	Answer = lipgloss.NewStyle().Foreground(colAnswer).Bold(true)
	Chat   = lipgloss.NewStyle().Foreground(colChat)

	AgentRunning = lipgloss.NewStyle().Foreground(lipgloss.Color("#00BFFF"))
	AgentDone    = lipgloss.NewStyle().Foreground(colOK)
	AgentKilled  = lipgloss.NewStyle().Foreground(colErr)
	AgentWaiting = lipgloss.NewStyle().Foreground(colSubtle)
)

// PanelStyle returns a bordered, padded panel of the given width that
// labels itself in the top-left.
func PanelStyle(width int, focused bool) lipgloss.Style {
	bc := colBorder
	if focused {
		bc = colBorderFocused
	}
	return lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(bc).
		Padding(0, 1).
		Width(width)
}

// HeaderLabel renders a short label above a panel.
func HeaderLabel(text string) string {
	return lipgloss.NewStyle().Foreground(colSubtle).Render("─ " + text + " ─")
}
