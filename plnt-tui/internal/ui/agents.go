package ui

import (
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/plnt/plnt-tui/internal/client"
)

// AgentView tracks one micro-agent's live status as derived from the event stream.
type AgentView struct {
	ID         string
	Role       string
	Depth      int
	StartedAt  time.Time
	FinishedAt time.Time
	Status     string // spawned|running|done|killed|error
	LastTool   string
	LastArgs   string
	ToolCalls  int
	Tokens     int
	Backend    string
	ExitCode   int
	KillReason string
	DependsOn  []string
	Workdir    string
	FileCount  int
	Files      []string
}

func (a *AgentView) Elapsed() time.Duration {
	end := a.FinishedAt
	if end.IsZero() {
		end = time.Now()
	}
	return end.Sub(a.StartedAt).Round(100 * time.Millisecond)
}

// SwarmState is the running picture of the swarm.
type SwarmState struct {
	RunID         string
	Intent        string
	TriageKind    string
	TriageReason  string
	PlanText      string
	Agents        map[string]*AgentView
	Order         []string
	StartedAt     time.Time
	Finished      bool
	Spawned       int
	Killed        int
	Answer        string // synthesized final answer or chat reply
	AnswerSource  string // "synth" | "triage"
}

func NewSwarm(runID, intent string) *SwarmState {
	return &SwarmState{
		RunID:     runID,
		Intent:    intent,
		Agents:    map[string]*AgentView{},
		StartedAt: time.Now(),
	}
}

func (s *SwarmState) Apply(e client.Event) {
	switch e.Kind {
	case "intent":
		// already captured
	case "triage_start":
		s.PlanText = "triaging intent…"
	case "triage":
		kind, _ := e.Payload["kind"].(string)
		reason, _ := e.Payload["reason"].(string)
		s.TriageKind = kind
		s.TriageReason = reason
	case "planner_start":
		s.PlanText = "planner thinking…"
	case "plan":
		count, _ := e.Payload["agent_count"].(float64)
		s.Spawned = int(count)
		s.PlanText = fmt.Sprintf("planner emitted %d agent(s)", s.Spawned)
		if agents, ok := e.Payload["agents"].([]interface{}); ok {
			for _, a := range agents {
				if m, ok := a.(map[string]interface{}); ok {
					id, _ := m["id"].(string)
					role, _ := m["role"].(string)
					if id == "" {
						continue
					}
					av := s.touch(id)
					av.Role = role
					av.Status = "spawned"
					if deps, ok := m["depends_on"].([]interface{}); ok {
						for _, d := range deps {
							if ds, ok := d.(string); ok {
								av.DependsOn = append(av.DependsOn, ds)
							}
						}
					}
				}
			}
		}
	case "spawn":
		av := s.touch(e.AgentID)
		if role, ok := e.Payload["role"].(string); ok {
			av.Role = role
		}
		if d, ok := e.Payload["depth"].(float64); ok {
			av.Depth = int(d)
		}
		if wd, ok := e.Payload["workdir"].(string); ok {
			av.Workdir = wd
		}
		av.Status = "spawned"
	case "started":
		av := s.touch(e.AgentID)
		av.Status = "running"
		av.StartedAt = time.Now()
		if role, ok := e.Payload["role"].(string); ok && av.Role == "" {
			av.Role = role
		}
	case "model_call":
		s.touch(e.AgentID).Status = "running"
	case "model_result":
		av := s.touch(e.AgentID)
		if t, ok := e.Payload["tokens"].(float64); ok {
			av.Tokens += int(t)
		}
	case "tool_call":
		av := s.touch(e.AgentID)
		av.ToolCalls++
		if t, ok := e.Payload["tool"].(string); ok {
			av.LastTool = t
		}
		if args, ok := e.Payload["args"].(map[string]interface{}); ok {
			av.LastArgs = compactArgs(args)
		}
	case "killed":
		av := s.touch(e.AgentID)
		av.Status = "killed"
		if r, ok := e.Payload["reason"].(string); ok {
			av.KillReason = r
		}
		s.Killed++
	case "error":
		if e.AgentID == "" {
			return
		}
		av := s.touch(e.AgentID)
		if av.Status != "killed" {
			av.Status = "error"
		}
		if r, ok := e.Payload["reason"].(string); ok {
			av.KillReason = r
		}
	case "result":
		av := s.touch(e.AgentID)
		if av.Status != "killed" && av.Status != "error" {
			av.Status = "done"
		}
	case "answer":
		txt, _ := e.Payload["text"].(string)
		src, _ := e.Payload["source"].(string)
		s.Answer = txt
		s.AnswerSource = src
	case "synth_start":
		s.PlanText = "synthesizing answer…"
	case "finished":
		if e.AgentID != "" {
			av := s.touch(e.AgentID)
			if av.Status == "running" || av.Status == "spawned" {
				av.Status = "done"
			}
			av.FinishedAt = time.Now()
			if rc, ok := e.Payload["exit_code"].(float64); ok {
				av.ExitCode = int(rc)
			}
			if wd, ok := e.Payload["workdir"].(string); ok && wd != "" {
				av.Workdir = wd
			}
			if fc, ok := e.Payload["file_count"].(float64); ok {
				av.FileCount = int(fc)
			}
			if files, ok := e.Payload["files_written"].([]interface{}); ok {
				for _, f := range files {
					if s, ok := f.(string); ok {
						av.Files = append(av.Files, s)
					}
				}
			}
		} else {
			s.Finished = true
		}
	}
}

func (s *SwarmState) touch(id string) *AgentView {
	if id == "" {
		id = "(unknown)"
	}
	av, ok := s.Agents[id]
	if !ok {
		av = &AgentView{ID: id, StartedAt: time.Now()}
		s.Agents[id] = av
		s.Order = append(s.Order, id)
	}
	return av
}

func (s *SwarmState) Sorted() []*AgentView {
	out := make([]*AgentView, 0, len(s.Agents))
	for _, id := range s.Order {
		if av, ok := s.Agents[id]; ok {
			out = append(out, av)
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		return out[i].StartedAt.Before(out[j].StartedAt)
	})
	return out
}

func compactArgs(m map[string]interface{}) string {
	if len(m) == 0 {
		return ""
	}
	parts := []string{}
	for k, v := range m {
		s := fmt.Sprintf("%v", v)
		if len(s) > 28 {
			s = s[:25] + "…"
		}
		parts = append(parts, fmt.Sprintf("%s=%s", k, s))
	}
	sort.Strings(parts)
	return strings.Join(parts, " ")
}
