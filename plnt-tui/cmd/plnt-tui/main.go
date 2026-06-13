// Command plnt-tui is a Bubble Tea terminal client for the Plnt surface.
//
// Usage:
//
//	plnt-tui [-url http://127.0.0.1:7777]
//
// The TUI submits intents and shows the swarm working live — agents, tools,
// kills, and the final answer in one running view. No markdown files.
package main

import (
	"flag"
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/plnt/plnt-tui/internal/ui"
)

func main() {
	url := flag.String("url", envOr("PLNT_SURFACE_URL", "http://127.0.0.1:7777"), "Plnt surface base URL")
	flag.Parse()

	m := ui.New(*url)
	p := tea.NewProgram(m, tea.WithAltScreen(), tea.WithMouseCellMotion())
	ui.SetProgram(p)

	if _, err := p.Run(); err != nil {
		fmt.Fprintln(os.Stderr, "plnt-tui:", err)
		os.Exit(1)
	}
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
