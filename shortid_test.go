package main

import (
	"context"
	"strings"
	"testing"

	"github.com/pkulak/barnaby/matrix"
)

func TestShortID(t *testing.T) {
	t.Parallel()

	known := []string{"$abcdefghij", "$abcdefXYZ", "$zzzzzzzzzz", "!short"}

	tests := []struct {
		id, want string
	}{
		{"$zzzzzzzzzz", "$zzzzzz"},
		{"$abcdefghij", "$abcdefg"},
		{"$abcdefXYZ", "$abcdefX"},
		{"!short", "!short"},
		{"$unknown-event", "$unknown-event"},
	}

	for _, tt := range tests {
		if got := shortID(tt.id, known); got != tt.want {
			t.Errorf("shortID(%q) = %q, want %q", tt.id, got, tt.want)
		}
	}
}

func TestResolveID(t *testing.T) {
	t.Parallel()

	known := []string{"$abcdefghij", "$abcdefXYZ", "$abc"}

	tests := []struct {
		prefix, want string
	}{
		{"$abcdefg", "$abcdefghij"},
		{"$abcdefXYZ", "$abcdefXYZ"},
		{"$abc", "$abc"},
		{"$abcdef", ""},
		{"$nope", ""},
		{"", ""},
	}

	for _, tt := range tests {
		if got := resolveID(tt.prefix, known); got != tt.want {
			t.Errorf("resolveID(%q) = %q, want %q", tt.prefix, got, tt.want)
		}
	}
}

func TestApp_PromptShowsShortIDs(t *testing.T) {
	t.Parallel()

	const room = "!LmhiriMncBFSihYoFk:kulak.us"

	matrixClient := &mockMatrix{joinedRooms: []string{room, "!other:kulak.us"}}
	app := newTestAppWithMatrix(t, matrixClient)

	app.HandleMessage(t.Context(), matrix.Message{
		ConversationID: room,
		SenderID:       "@phil:kulak.us",
		MessageID:      "$VX_QqfzzY9MrBmcHDM82Pz3qWEWFSIJwfiLlWM_nClI",
		Text:           "yup",
		IsDM:           true,
	})

	item, err := app.inbox.DequeueChat(t.Context())
	if err != nil {
		t.Fatal(err)
	}

	for _, want := range []string{"!Lmhiri", "$VX_Qqf"} {
		if !strings.Contains(item.Content, want) {
			t.Errorf("content = %q, want %q", item.Content, want)
		}
	}

	for _, full := range []string{room, "$VX_QqfzzY9"} {
		if strings.Contains(item.Content, full) {
			t.Errorf("content = %q, should not contain %q", item.Content, full)
		}
	}

	if item.ConversationID != room || item.MessageID != "$VX_QqfzzY9MrBmcHDM82Pz3qWEWFSIJwfiLlWM_nClI" {
		t.Errorf("inbox item keeps full IDs, got %+v", item)
	}
}

func TestApp_SendReactionResolvesShortID(t *testing.T) {
	t.Parallel()

	matrixClient := &mockMatrix{}
	app := newTestAppWithMatrix(t, matrixClient)
	ctx := context.Background()

	app.outbox.Put(ctx, reactionSourceRoom, "$abcdefghij", "hello")
	app.outbox.Put(ctx, reactionSourceRoom, "$abcdefXYZ", "world")
	app.sendReaction(ctx, reactionSourceRoom, reactionRequest{messageID: "$abcdefg", emoji: "👍"})
	app.sendReaction(ctx, reactionSourceRoom, reactionRequest{messageID: "$abcdef", emoji: "❤️"})

	matrixClient.mu.Lock()
	defer matrixClient.mu.Unlock()

	if len(matrixClient.reactions) != 1 || matrixClient.reactions[0].messageID != "$abcdefghij" {
		t.Errorf("reactions = %+v, want one to $abcdefghij", matrixClient.reactions)
	}
}

func TestDeliverVoiceReplyResolvesShortRoomID(t *testing.T) {
	t.Parallel()

	const target = "!targetRoomID:example.com"

	matrixClient := &mockMatrix{joinedRooms: []string{target}}
	app := newTestAppWithMatrix(t, matrixClient)

	app.deliverVoiceReply(t.Context(), testRoom, "<send-to>!target</send-to>Report")

	matrixClient.mu.Lock()
	defer matrixClient.mu.Unlock()

	if len(matrixClient.sentMessages) != 1 || matrixClient.sentMessages[0].conversationID != target {
		t.Errorf("sent messages = %+v, want one to %s", matrixClient.sentMessages, target)
	}
}
