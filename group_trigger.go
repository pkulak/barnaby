package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	defaultGroupTriggerTimeout = 10 * time.Second
	maxGroupHistory            = 20
	maxGroupHistoryRunes       = 500
)

// groupHistoryEntry is one recent room message, kept in memory so the group
// trigger script can see what led up to the current message.
type groupHistoryEntry struct {
	from  string
	isBot bool
	text  string
	at    time.Time
}

type groupTriggerInput struct {
	History []groupTriggerHistory `json:"history"`
	Message groupTriggerMessage   `json:"message"`
}

type groupTriggerHistory struct {
	From  string `json:"from"`
	IsBot bool   `json:"is_bot"`
	Ago   string `json:"ago"`
	Text  string `json:"text"`
}

type groupTriggerMessage struct {
	From string `json:"from"`
	Text string `json:"text"`
}

func newGroupTriggerInput(history []groupHistoryEntry, from, text string, now time.Time) groupTriggerInput {
	input := groupTriggerInput{
		History: make([]groupTriggerHistory, 0, len(history)),
		Message: groupTriggerMessage{From: from, Text: text},
	}

	for _, entry := range history {
		input.History = append(input.History, groupTriggerHistory{
			From:  entry.from,
			IsBot: entry.isBot,
			Ago:   formatAgo(now.Sub(entry.at)),
			Text:  entry.text,
		})
	}

	return input
}

// formatAgo renders a duration in its largest whole unit: 45s, 2m, 3h, 2d.
func formatAgo(d time.Duration) string {
	d = max(d, 0)

	switch {
	case d < time.Minute:
		return fmt.Sprintf("%ds", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	}
}

func truncateRunes(s string, limit int) string {
	if utf8.RuneCountInString(s) <= limit {
		return s
	}

	return string([]rune(s)[:limit]) + "…"
}

// runGroupTrigger pipes input to script as JSON. Exit 0 means respond and
// exit 1 means skip; any other outcome, including a timeout, is an error.
// It also returns the script's trimmed output for logging.
func runGroupTrigger(ctx context.Context, script string, timeout time.Duration, input groupTriggerInput) (bool, string, error) {
	payload, err := json.Marshal(input)
	if err != nil {
		return false, "", fmt.Errorf("encoding group trigger input: %w", err)
	}

	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, script)
	cmd.Stdin = bytes.NewReader(payload)
	// Don't wait forever on output pipes held open by a killed script's children.
	cmd.WaitDelay = time.Second

	out, err := cmd.CombinedOutput()
	output := strings.TrimSpace(string(out))

	var exitErr *exec.ExitError

	switch {
	case err == nil:
		return true, output, nil
	case ctx.Err() != nil:
		return false, output, fmt.Errorf("group trigger stopped (timeout %s): %w", timeout, ctx.Err())
	case errors.As(err, &exitErr) && exitErr.ExitCode() == 1:
		return false, output, nil
	default:
		return false, output, fmt.Errorf("running group trigger: %w", err)
	}
}
