// Package client talks to the Plnt surface API.
//
// Two operations matter:
//   - POST /v1/intents  → returns a run_id
//   - GET  /v1/runs/{id}/stream  → SSE stream of events
//
// The SSE reader is a one-go-routine pump that pushes parsed events onto a
// channel the TUI's tea.Program receives via tea.Msg.
package client

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	BaseURL string
	HTTP    *http.Client
}

func New(baseURL string) *Client {
	if baseURL == "" {
		baseURL = "http://127.0.0.1:7777"
	}
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		HTTP:    &http.Client{Timeout: 0}, // no overall timeout; streams are long
	}
}

type Health struct {
	OK      bool   `json:"ok"`
	Version string `json:"version"`
	Home    string `json:"home"`
}

func (c *Client) Health(ctx context.Context) (Health, error) {
	var h Health
	req, _ := http.NewRequestWithContext(ctx, "GET", c.BaseURL+"/v1/health", nil)
	r, err := c.HTTP.Do(req)
	if err != nil {
		return h, err
	}
	defer r.Body.Close()
	return h, json.NewDecoder(r.Body).Decode(&h)
}

// PriorTurn is one past Q&A the TUI sends along so the planner can use
// conversation memory.
type PriorTurn struct {
	Prompt string `json:"prompt"`
	Answer string `json:"answer"`
}

type submitReq struct {
	Text    string      `json:"text"`
	History []PriorTurn `json:"history,omitempty"`
}
type submitResp struct {
	RunID string `json:"run_id"`
}

func (c *Client) Submit(ctx context.Context, text string, history []PriorTurn) (string, error) {
	body, _ := json.Marshal(submitReq{Text: text, History: history})
	req, _ := http.NewRequestWithContext(ctx, "POST", c.BaseURL+"/v1/intents", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	r, err := c.HTTP.Do(req)
	if err != nil {
		return "", err
	}
	defer r.Body.Close()
	if r.StatusCode/100 != 2 {
		b, _ := io.ReadAll(r.Body)
		return "", fmt.Errorf("surface returned %d: %s", r.StatusCode, b)
	}
	var sr submitResp
	if err := json.NewDecoder(r.Body).Decode(&sr); err != nil {
		return "", err
	}
	return sr.RunID, nil
}

// Event is a single decoded SSE record from /v1/runs/{id}/stream.
type Event struct {
	TS      float64                `json:"ts"`
	RunID   string                 `json:"run_id"`
	Kind    string                 `json:"kind"`
	AgentID string                 `json:"agent_id,omitempty"`
	Payload map[string]interface{} `json:"payload,omitempty"`
}

// Subscribe reads SSE events from a run forever (or until ctx cancels) and
// pushes them onto out. Closes out when the stream ends.
func (c *Client) Subscribe(ctx context.Context, runID string, out chan<- Event) error {
	defer close(out)
	url := fmt.Sprintf("%s/v1/runs/%s/stream", c.BaseURL, runID)
	for {
		if err := c.subscribeOnce(ctx, url, out); err != nil {
			select {
			case <-ctx.Done():
				return ctx.Err()
			default:
			}
			// Brief backoff before retry.
			time.Sleep(500 * time.Millisecond)
			continue
		}
		return nil
	}
}

func (c *Client) subscribeOnce(ctx context.Context, url string, out chan<- Event) error {
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	req.Header.Set("Accept", "text/event-stream")
	r, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer r.Body.Close()

	br := bufio.NewReader(r.Body)
	var dataBuf strings.Builder
	for {
		line, err := br.ReadString('\n')
		if err != nil {
			if err == io.EOF {
				return nil
			}
			return err
		}
		line = strings.TrimRight(line, "\r\n")
		if line == "" {
			if dataBuf.Len() > 0 {
				var e Event
				if jerr := json.Unmarshal([]byte(dataBuf.String()), &e); jerr == nil {
					out <- e
				}
				dataBuf.Reset()
			}
			continue
		}
		if strings.HasPrefix(line, "data:") {
			dataBuf.WriteString(strings.TrimSpace(strings.TrimPrefix(line, "data:")))
		}
		// "event:" lines are decorative; the kind is also inside the JSON.
	}
}
