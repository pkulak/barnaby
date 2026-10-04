package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/pkulak/barnaby/matrix"
)

const familyRoom = "!family:kulak.us"

// writeTriggerScript writes an executable shell script with the given body.
func writeTriggerScript(t *testing.T, body string) string {
	t.Helper()

	path := filepath.Join(t.TempDir(), "trigger.sh")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body+"\n"), 0o755); err != nil { //nolint:gosec // test script must be executable
		t.Fatal(err)
	}

	return path
}

func groupMessage(sender, text string) matrix.Message {
	return matrix.Message{
		ConversationID: familyRoom,
		SenderID:       "@" + strings.ToLower(sender) + ":kulak.us",
		SenderName:     sender,
		Text:           text,
		MessageID:      "$" + strings.ToLower(sender),
	}
}

func inboxCount(t *testing.T, app *App) int64 {
	t.Helper()

	count, err := app.inbox.Count(t.Context())
	if err != nil {
		t.Fatal(err)
	}

	return count
}

func TestApp_GroupTrigger_SkipBuffersAndDrops(t *testing.T) {
	t.Parallel()

	app, _ := newTestApp(t)
	app.SetGroupTriggerScript(writeTriggerScript(t, "exit 1"))

	ctx := t.Context()
	app.HandleMessage(ctx, groupMessage("Gwen", "Did you know dogs can't look up?"))

	if count := inboxCount(t, app); count != 0 {
		t.Errorf("inbox count = %d, want 0", count)
	}

	events, err := loadRoomContextEvents(ctx, app.roomContext.db, familyRoom)
	if err != nil {
		t.Fatal(err)
	}

	if len(events) != 1 || events[0].SenderName != "Gwen" || events[0].Text != "Did you know dogs can't look up?" {
		t.Errorf("room context = %+v, want Gwen's message", events)
	}
}

func TestApp_GroupTrigger_RespondPrependsBufferAndClearsIt(t *testing.T) {
	t.Parallel()

	app, _ := newTestApp(t)
	app.SetGroupTriggerScript(writeTriggerScript(t, `grep -q '"text":"Barn, is that true?"}'`))

	ctx := t.Context()
	app.HandleMessage(ctx, groupMessage("Gwen", "Dogs can't look up."))
	app.HandleMessage(ctx, groupMessage("Phil", "Barn, is that true?"))

	if count := inboxCount(t, app); count != 1 {
		t.Fatalf("inbox count = %d, want 1", count)
	}

	item, err := app.inbox.DequeueChat(ctx)
	if err != nil {
		t.Fatal(err)
	}

	for _, want := range []string{
		`speaker="participant" sender-name="Gwen" sender-id="@gwen:kulak.us"`,
		"Dogs can&#39;t look up.",
		"Barn, is that true?",
	} {
		if !strings.Contains(item.Content, want) {
			t.Errorf("item.Content missing %q, got: %q", want, item.Content)
		}
	}

	events, err := loadRoomContextEvents(ctx, app.roomContext.db, familyRoom)
	if err != nil {
		t.Fatal(err)
	}

	if len(events) != 0 {
		t.Errorf("room context length = %d, want 0 after flush", len(events))
	}
}

func TestApp_GroupTrigger_ScriptSeesHistory(t *testing.T) {
	t.Parallel()

	out := filepath.Join(t.TempDir(), "input.json")
	app, _ := newTestApp(t)
	app.SetGroupTriggerScript(writeTriggerScript(t, `cat > "`+out+`"; exit 1`))

	ctx := t.Context()
	app.HandleMessage(ctx, groupMessage("Gwen", "Who is cooking tonight?"))
	app.sendReplyWithFiles(ctx, familyRoom, "Phil is.<sendfile>/tmp/menu.pdf</sendfile>", "", false, false)
	app.sendReaction(ctx, familyRoom, reactionRequest{messageID: "$gwen", emoji: "👍"})
	app.HandleMessage(ctx, groupMessage("Phil", "Thanks!"))

	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatal(err)
	}

	var got groupTriggerInput
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatal(err)
	}

	want := groupTriggerInput{
		History: []groupTriggerHistory{
			{From: "Gwen", Ago: "0s", Text: "Who is cooking tonight?"},
			{From: "Barnaby", IsBot: true, Ago: "0s", Text: "[sent a file: menu.pdf]"},
			{From: "Barnaby", IsBot: true, Ago: "0s", Text: "Phil is."},
			{From: "Barnaby", IsBot: true, Ago: "0s", Text: "[reacted 👍 to: Who is cooking tonight?]"},
		},
		Message: groupTriggerMessage{From: "Phil", Text: "Thanks!"},
	}

	if !reflect.DeepEqual(got, want) {
		t.Errorf("script input =\n%+v\nwant\n%+v", got, want)
	}
}

func TestApp_GroupTrigger_FailureDelivers(t *testing.T) {
	t.Parallel()

	cases := map[string]string{
		"error":   "exit 2",
		"timeout": "sleep 5",
	}

	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()

			app, _ := newTestApp(t)
			app.SetGroupTriggerScript(writeTriggerScript(t, body))
			app.groupTriggerTimeout = 100 * time.Millisecond
			start := time.Now()

			app.HandleMessage(t.Context(), groupMessage("Gwen", "hello"))

			if elapsed := time.Since(start); elapsed > 3*time.Second {
				t.Errorf("HandleMessage took %s, want the timeout to cut it short", elapsed)
			}

			if count := inboxCount(t, app); count != 1 {
				t.Errorf("inbox count = %d, want 1 (fail open)", count)
			}
		})
	}
}

func TestApp_GroupTrigger_DMSkipsScript(t *testing.T) {
	t.Parallel()

	app, _ := newTestApp(t)
	app.SetGroupTriggerScript(writeTriggerScript(t, "exit 1"))

	msg := groupMessage("Phil", "Random chatter without name")
	msg.IsDM = true
	app.HandleMessage(t.Context(), msg)

	if count := inboxCount(t, app); count != 1 {
		t.Errorf("inbox count = %d, want 1 for DM", count)
	}
}

func TestApp_GroupTrigger_NoScriptDeliversAll(t *testing.T) {
	t.Parallel()

	app, _ := newTestApp(t)
	app.HandleMessage(t.Context(), groupMessage("Phil", "Random chatter"))

	if count := inboxCount(t, app); count != 1 {
		t.Errorf("inbox count = %d, want 1 when no script is set", count)
	}
}

func TestApp_RecordHistoryBounded(t *testing.T) {
	t.Parallel()

	app, _ := newTestApp(t)

	for i := range maxGroupHistory + 5 {
		app.recordHistory(familyRoom, "Gwen", false, strings.Repeat("x", i))
	}

	app.recordHistory(familyRoom, "Gwen", false, strings.Repeat("é", maxGroupHistoryRunes+1))

	history := app.groupHistory[familyRoom]
	if len(history) != maxGroupHistory {
		t.Fatalf("history length = %d, want %d", len(history), maxGroupHistory)
	}

	if want := strings.Repeat("x", 6); history[0].text != want {
		t.Errorf("oldest entry = %q, want %q", history[0].text, want)
	}

	if want := strings.Repeat("é", maxGroupHistoryRunes) + "…"; history[len(history)-1].text != want {
		t.Errorf("long entry was not truncated to %d runes", maxGroupHistoryRunes)
	}
}

func TestFormatAgo(t *testing.T) {
	t.Parallel()

	cases := map[time.Duration]string{
		-time.Second:                   "0s",
		45 * time.Second:               "45s",
		2*time.Minute + 59*time.Second: "2m",
		3 * time.Hour:                  "3h",
		50 * time.Hour:                 "2d",
	}

	for d, want := range cases {
		if got := formatAgo(d); got != want {
			t.Errorf("formatAgo(%s) = %q, want %q", d, got, want)
		}
	}
}

func TestApp_VoiceDeliveryRecordsHistory(t *testing.T) {
	t.Parallel()

	app, _ := newTestApp(t)
	ctx := t.Context()

	app.deliverVoiceToMatrix(ctx, familyRoom, "Lights are off.", []string{"/tmp/floor.png"})
	app.deliverVoiceFiles(ctx, familyRoom, "", []string{"/tmp/receipt.pdf"})

	history := app.groupHistory[familyRoom]
	got := make([]string, 0, len(history))

	for _, entry := range history {
		if !entry.isBot || entry.from != "Barnaby" {
			t.Errorf("entry %+v, want a Barnaby bot entry", entry)
		}

		got = append(got, entry.text)
	}

	want := []string{"[sent a file: floor.png]", "Lights are off.", "[sent a file: receipt.pdf]"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("history = %q, want %q", got, want)
	}
}
