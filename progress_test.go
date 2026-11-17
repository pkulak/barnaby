package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"
)

// newProgressWorker returns a fake-pi chat worker whose progress script runs
// body, with delays short enough for a slow-progress turn.
func newProgressWorker(t *testing.T, body string) (*Worker, *mockMatrix) {
	t.Helper()

	w := newFakePiWorker(t)
	mb := &mockMatrix{}
	w.SetMatrix(mb)

	db := newTestDBAt(t.Context(), t, t.TempDir()+"/test.db")
	app := NewApp(mb, w, newTestInboxWithDB(t.Context(), t, db), db)
	w.SetApp(app)

	w.SetProgressScript(writeTriggerScript(t, body))
	w.progressDelay = 20 * time.Millisecond
	w.progressInterval = 50 * time.Millisecond

	return w, mb
}

func TestWorker_ProgressStatusThenReply(t *testing.T) {
	t.Parallel()

	input := filepath.Join(t.TempDir(), "input.json")
	w, mb := newProgressWorker(t, `cat > `+input+`; echo "Running a command"`)

	w.processPrompt(t.Context(), Inbox{Source: sourceUser, Content: "slow-progress", ConversationID: testRoom})

	mb.mu.Lock()
	defer mb.mu.Unlock()

	if want := []sentMessage{{testRoom, "Running a command"}, {testRoom, "ok"}}; !slices.Equal(mb.sentMessages, want) {
		t.Fatalf("sent messages = %+v, want status then reply", mb.sentMessages)
	}

	if len(mb.edits) != 1 || mb.edits[0].eventID != "$sent-1" || !strings.HasPrefix(mb.edits[0].text, "Done (") {
		t.Fatalf("edits = %+v, want the status edited to Done", mb.edits)
	}

	got := readProgressInput(t, input)
	if got.Request != "slow-progress" || got.Summary != "" || len(got.Messages) != 1 ||
		!strings.Contains(string(got.Messages[0]), `"toolCall"`) {
		t.Errorf("script input = %+v", got)
	}
}

func readProgressInput(t *testing.T, path string) progressInput {
	t.Helper()

	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	var input progressInput
	if err := json.Unmarshal(data, &input); err != nil {
		t.Fatal(err)
	}

	return input
}

func TestWorker_ProgressSuppressedReplyDeletesStatus(t *testing.T) {
	t.Parallel()

	w, mb := newProgressWorker(t, `echo "Thinking"`)

	w.processPrompt(t.Context(), Inbox{Source: sourceUser, Content: "slow-progress silent-test", ConversationID: testRoom})

	mb.mu.Lock()
	defer mb.mu.Unlock()

	if len(mb.sentMessages) != 1 || len(mb.edits) != 0 {
		t.Fatalf("sent = %+v, edits = %+v, want only the status", mb.sentMessages, mb.edits)
	}

	if len(mb.redactions) != 1 || mb.redactions[0] != "$sent-1" {
		t.Errorf("redactions = %v, want the status", mb.redactions)
	}
}

func TestWorker_ProgressSendToLinksRoom(t *testing.T) {
	t.Parallel()

	w, mb := newProgressWorker(t, `echo "Thinking"`)

	w.processPrompt(t.Context(), Inbox{Source: sourceUser, Content: "slow-progress send-to-test", ConversationID: testRoom})

	mb.mu.Lock()
	defer mb.mu.Unlock()

	if len(mb.edits) != 1 || !strings.HasPrefix(mb.edits[0].text, "Done, replied in https://matrix.to/#/!other:matrix.org (") {
		t.Fatalf("edits = %+v, want a link to the other room", mb.edits)
	}
}

func TestWorker_ProgressScriptFailurePostsNoStatus(t *testing.T) {
	t.Parallel()

	w, mb := newProgressWorker(t, `echo "broken" >&2; exit 1`)

	w.processPrompt(t.Context(), Inbox{Source: sourceUser, Content: "slow-progress", ConversationID: testRoom})

	mb.mu.Lock()
	defer mb.mu.Unlock()

	if len(mb.sentMessages) != 1 || mb.sentMessages[0].text != "ok" || len(mb.edits) != 0 {
		t.Fatalf("sent = %+v, edits = %+v, want only the reply", mb.sentMessages, mb.edits)
	}
}

func TestShortenJSONStrings(t *testing.T) {
	t.Parallel()

	raw := json.RawMessage(`{"role":"toolResult","timestamp":1733234403000,"content":[{"type":"text","text":"abcdef"}]}`)

	got := string(shortenJSONStrings(raw, 3))

	want := `{"content":[{"text":"abc…","type":"tex…"}],"role":"too…","timestamp":1733234403000}`
	if got != want {
		t.Errorf("shortenJSONStrings = %s, want %s", got, want)
	}
}
