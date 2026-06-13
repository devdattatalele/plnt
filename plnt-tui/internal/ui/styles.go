package ui

import "github.com/charmbracelet/lipgloss"

var (
	Title = lipgloss.NewStyle().
		Bold(true).
		Foreground(lipgloss.Color("#7CFC00"))

	Subtle = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#808080"))

	Accent = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#FFD700"))

	Err = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#FF6B6B"))

	OK = lipgloss.NewStyle().
		Foreground(lipgloss.Color("#7CFC00"))

	Box = lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("#444444")).
		Padding(0, 1)

	BoxFocused = Box.Copy().
			BorderForeground(lipgloss.Color("#FFD700"))

	AgentRunning = lipgloss.NewStyle().Foreground(lipgloss.Color("#00BFFF"))
	AgentDone    = lipgloss.NewStyle().Foreground(lipgloss.Color("#7CFC00"))
	AgentKilled  = lipgloss.NewStyle().Foreground(lipgloss.Color("#FF6B6B"))
)
