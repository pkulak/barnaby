package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"os/exec"
	"strings"
	"sync"
	"time"
)

const (
	defaultProgressDelay    = 10 * time.Second
	defaultProgressInterval = 15 * time.Second
	progressScriptTimeout   = 10 * time.Second
	// progressMaxRunes caps each string in a message passed to the progress
	// script, so a large file read or base64 image doesn't swamp it.
	progressMaxRunes = 2000
)

// progressInput is the JSON a progress script reads on stdin.
type progressInput struct {
	Request  string            `json:"request"`
	Summary  string            `json:"summary"`
	Messages []json.RawMessage `json:"messages"`
}

// progressReporter posts a status message while a chat turn runs and edits
// it as the turn goes on. A user script turns the Pi messages since its last
// run into the status text.
type progressReporter struct {
	script   string
	matrix   workerMatrix
	convID   string
	request  string
	started  time.Time
	interval time.Duration

	mu      sync.Mutex
	pending []json.RawMessage

	// summary and eventID belong to run until done closes.
	summary string
	eventID string

	stop     context.CancelFunc
	done     chan struct{}
	finished bool
}

func startProgressReporter(
	ctx context.Context,
	script string,
	matrixClient workerMatrix,
	convID, request string,
	delay, interval time.Duration,
) *progressReporter {
	runCtx, stop := context.WithCancel(ctx)

	p := &progressReporter{
		script:   script,
		matrix:   matrixClient,
		convID:   convID,
		request:  request,
		started:  time.Now(),
		interval: interval,
		stop:     stop,
		done:     make(chan struct{}),
	}

	go p.run(runCtx, delay)

	return p
}

// record keeps each completed Pi message for the next script run.
func (p *progressReporter) record(evt rpcEvent) {
	if evt.Type != rpcTypeMessageEnd || len(evt.Message) == 0 {
		return
	}

	msg := shortenJSONStrings(evt.Message, progressMaxRunes)

	p.mu.Lock()
	p.pending = append(p.pending, msg)
	p.mu.Unlock()
}

func (p *progressReporter) run(ctx context.Context, delay time.Duration) {
	defer close(p.done)

	timer := time.NewTimer(delay)
	defer timer.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}

		p.update(ctx)
		timer.Reset(p.interval)
	}
}

// update runs the script on the messages since the last run, then posts or
// edits the status message if the summary changed.
func (p *progressReporter) update(ctx context.Context) {
	p.mu.Lock()
	messages := p.pending
	p.pending = nil
	p.mu.Unlock()

	if len(messages) == 0 {
		return
	}

	payload, err := json.Marshal(progressInput{Request: p.request, Summary: p.summary, Messages: messages})
	if err != nil {
		slog.Warn("progress: encoding script input failed", "error", err)

		return
	}

	summary, err := runProgressScript(ctx, p.script, payload)
	if err != nil {
		if ctx.Err() == nil {
			slog.Warn("progress: script failed", "conversation", p.convID, "error", err)
		}

		return
	}

	if summary == "" || summary == p.summary {
		return
	}

	p.summary = summary

	if p.eventID == "" {
		p.eventID = p.matrix.SendMessage(ctx, p.convID, summary, "")

		return
	}

	if err := p.matrix.EditMessage(ctx, p.convID, p.eventID, summary); err != nil {
		slog.Warn("progress: editing status failed", "conversation", p.convID, "error", err)
	}
}

// finish stops the updates. If a status message was posted, it's edited to
// label and the turn's duration, or deleted when label is empty. Only the
// first call does anything, and a nil reporter is a no-op.
func (p *progressReporter) finish(ctx context.Context, label string) {
	if p == nil || p.finished {
		return
	}

	p.finished = true

	p.stop()
	<-p.done

	if p.eventID == "" {
		return
	}

	// The turn may have been cancelled; the status should still settle.
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 10*time.Second)
	defer cancel()

	if label == "" {
		if err := p.matrix.RedactMessage(ctx, p.convID, p.eventID); err != nil {
			slog.Warn("progress: deleting status failed", "conversation", p.convID, "error", err)
		}

		return
	}

	text := fmt.Sprintf("%s (%s)", label, time.Since(p.started).Round(time.Second))
	if err := p.matrix.EditMessage(ctx, p.convID, p.eventID, text); err != nil {
		slog.Warn("progress: editing status failed", "conversation", p.convID, "error", err)
	}
}

// progressDoneLabel is the final status for a turn that replied in
// replyRoom. A reply sent elsewhere links to its room.
func progressDoneLabel(convID, replyRoom string) string {
	if replyRoom == convID {
		return "Done"
	}

	return "Done, replied in https://matrix.to/#/" + replyRoom
}

// runProgressScript pipes payload to script and returns its trimmed stdout.
func runProgressScript(ctx context.Context, script string, payload []byte) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, progressScriptTimeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, script)
	cmd.Stdin = bytes.NewReader(payload)
	// Don't wait forever on output pipes held open by a killed script's children.
	cmd.WaitDelay = time.Second

	out, err := cmd.Output()
	if err != nil {
		var exitErr *exec.ExitError
		if errors.As(err, &exitErr) {
			return "", fmt.Errorf("running progress script: %w: %s", err, strings.TrimSpace(string(exitErr.Stderr)))
		}

		return "", fmt.Errorf("running progress script: %w", err)
	}

	return strings.TrimSpace(string(out)), nil
}

// shortenJSONStrings cuts every string in raw to limit runes.
func shortenJSONStrings(raw json.RawMessage, limit int) json.RawMessage {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return raw
	}

	shortened, err := json.Marshal(shortenStrings(value, limit))
	if err != nil {
		return raw
	}

	return shortened
}

func shortenStrings(value any, limit int) any {
	switch v := value.(type) {
	case string:
		return truncateRunes(v, limit)
	case []any:
		for i, item := range v {
			v[i] = shortenStrings(item, limit)
		}
	case map[string]any:
		for key, item := range v {
			v[key] = shortenStrings(item, limit)
		}
	}

	return value
}
