package main

import (
	"context"
	"database/sql"
	_ "embed"
	"fmt"
	"html"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Room messages that don't start a turn (unaddressed group chat and the
// background worker's posts) are queued for the chat worker, which records
// each one in its Pi session with the room-context extension command. The
// agent sees them in order on its next turn, and the session file keeps them
// even if nobody addresses the bot again.

//go:embed extensions/room-context.ts
var roomContextExtension []byte

const roomContextCommand = "/room-context "

// roomMessage is one room event recorded as context.
type roomMessage struct {
	ConversationID string
	RoomName       string
	MessageID      string
	// Speaker is "participant" for room members and "you" for the bot.
	Speaker string
	// Worker is "background" for posts from the background worker.
	Worker     string
	SenderName string
	SenderID   string
	Text       string
	At         time.Time
}

// recordRoomMessage queues msg for the chat worker's session.
func (a *App) recordRoomMessage(ctx context.Context, msg roomMessage) {
	if err := a.inbox.Enqueue(ctx, PriorityUser, sourceRoomContext, formatRoomMessage(msg), "", msg.ConversationID); err != nil {
		slog.Error("failed to record room message", "conversation", msg.ConversationID, "error", err)

		return
	}

	a.worker.Notify()
}

func formatRoomMessage(msg roomMessage) string {
	var attrs strings.Builder

	attr := func(name, value string) {
		if value != "" {
			fmt.Fprintf(&attrs, ` %s="%s"`, name, html.EscapeString(value))
		}
	}

	attr("speaker", msg.Speaker)
	attr("worker", msg.Worker)
	attr("sender-name", msg.SenderName)
	attr("sender-id", msg.SenderID)
	attr("room-name", msg.RoomName)
	attr("room-id", msg.ConversationID)
	attr("message-id", msg.MessageID)

	if !msg.At.IsZero() {
		attr("time", msg.At.Format(time.RFC3339))
	}

	return fmt.Sprintf("<room-message%s>\n%s\n</room-message>", attrs.String(), html.EscapeString(msg.Text))
}

// writeRoomContextExtension installs the embedded extension in dir and returns
// its path. Workers start Pi concurrently, so the file is replaced atomically.
func writeRoomContextExtension(dir string) (string, error) {
	// Pi runs in the working directory, so the path must not be relative.
	dir, err := filepath.Abs(dir)
	if err != nil {
		return "", fmt.Errorf("resolving room-context extension dir: %w", err)
	}

	path := filepath.Join(dir, ".room-context.ts")

	tmp, err := os.CreateTemp(dir, ".room-context-*.ts")
	if err != nil {
		return "", fmt.Errorf("creating room-context extension: %w", err)
	}
	defer os.Remove(tmp.Name()) // a no-op once renamed

	if _, err := tmp.Write(roomContextExtension); err != nil {
		tmp.Close()

		return "", fmt.Errorf("writing room-context extension: %w", err)
	}

	if err := tmp.Close(); err != nil {
		return "", fmt.Errorf("writing room-context extension: %w", err)
	}

	if err := os.Rename(tmp.Name(), path); err != nil {
		return "", fmt.Errorf("installing room-context extension: %w", err)
	}

	return path, nil
}

// migrateRoomContext moves messages left in the retired room_context table
// into the chat inbox. Remove it once every instance has run it.
func migrateRoomContext(ctx context.Context, db *sql.DB) error {
	var tables int
	if err := db.QueryRowContext(ctx,
		`SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'room_context'`,
	).Scan(&tables); err != nil {
		return fmt.Errorf("checking for room_context: %w", err)
	}

	if tables == 0 {
		return nil
	}

	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("beginning room_context migration: %w", err)
	}
	defer tx.Rollback() //nolint:errcheck // rollback after commit is a no-op

	messages, err := loadLegacyRoomContext(ctx, tx)
	if err != nil {
		return err
	}

	queries := New(tx)
	for _, msg := range messages {
		if err := queries.EnqueueInbox(ctx, EnqueueInboxParams{
			Priority:       PriorityUser,
			Source:         sourceRoomContext,
			Content:        formatRoomMessage(msg),
			ConversationID: msg.ConversationID,
		}); err != nil {
			return fmt.Errorf("moving room context to the inbox: %w", err)
		}
	}

	if _, err := tx.ExecContext(ctx, `DROP TABLE room_context; DROP TABLE IF EXISTS room_context_omissions`); err != nil {
		return fmt.Errorf("dropping room_context: %w", err)
	}

	if err := tx.Commit(); err != nil {
		return fmt.Errorf("committing room_context migration: %w", err)
	}

	slog.Info("moved pending room context to the inbox", "messages", len(messages))

	return nil
}

func loadLegacyRoomContext(ctx context.Context, tx *sql.Tx) ([]roomMessage, error) {
	rows, err := tx.QueryContext(ctx, `
		SELECT conversation_id, message_id, speaker, worker, sender_name, sender_id, text, created_at
		FROM room_context ORDER BY id
	`)
	if err != nil {
		return nil, fmt.Errorf("reading room_context: %w", err)
	}
	defer rows.Close()

	var messages []roomMessage

	for rows.Next() {
		var (
			msg     roomMessage
			created string
		)

		if err := rows.Scan(&msg.ConversationID, &msg.MessageID, &msg.Speaker, &msg.Worker,
			&msg.SenderName, &msg.SenderID, &msg.Text, &created); err != nil {
			return nil, fmt.Errorf("scanning room_context: %w", err)
		}

		msg.At, _ = time.Parse(time.RFC3339Nano, created)
		messages = append(messages, msg)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterating room_context: %w", err)
	}

	return messages, nil
}
