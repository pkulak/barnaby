package main

import (
	"context"
	"database/sql"
	"fmt"
	"html"
	"log/slog"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/pkulak/barnaby/matrix"
)

var (
	sendFileRe = regexp.MustCompile(`<sendfile>\s*(.*?)\s*</sendfile>`)
	sendToRe   = regexp.MustCompile(`<send-to>\s*(.*?)\s*</send-to>`)
	reactRe    = regexp.MustCompile(`(?m)(?:^[\t ]*|[\t ]+)<react[\t ]+id="([^"\r\n]+)">([^\r\n]*)</react>[\t ]*$`)
)

const maxReactionBytes = 64

type reactionRequest struct {
	messageID string
	emoji     string
}

type appMatrix interface {
	SendMessage(ctx context.Context, conversationID, text, replyToID string) string
	SendFile(ctx context.Context, conversationID, filePath string) error
	SendReaction(ctx context.Context, conversationID, messageID, emoji string) error
	ResetConversation(ctx context.Context, conversationID string)
	OwnIdentity(ctx context.Context, conversationID string) (name, userID string)
	JoinedRoomIDs(ctx context.Context) []string
	SystemPromptExtra() string
}

// extractReaction finds standalone or line-trailing <react> control tags,
// strips all of them, and returns the first valid request. Tags followed by
// prose are ignored to avoid activating syntax that is only being discussed.
func extractReaction(text string) (string, *reactionRequest) {
	matches := reactRe.FindAllStringSubmatch(text, -1)
	if len(matches) == 0 {
		return text, nil
	}

	if len(matches) > 1 {
		slog.Warn("multiple reaction tags in agent response; using first valid tag", "count", len(matches))
	}

	var reaction *reactionRequest

	for _, match := range matches {
		messageID := strings.TrimSpace(html.UnescapeString(match[1]))
		emoji := strings.TrimSpace(html.UnescapeString(match[2]))

		if reaction == nil && messageID != "" && emoji != "" && len(emoji) <= maxReactionBytes {
			reaction = &reactionRequest{messageID: messageID, emoji: emoji}
		}
	}

	cleaned := reactRe.ReplaceAllString(text, "")

	return strings.TrimSpace(cleaned), reaction
}

// extractSendFiles finds all <sendfile>/path</sendfile> tags in text,
// returns the cleaned text with tags stripped and the list of file paths.
func extractSendFiles(text string) (string, []string) {
	matches := sendFileRe.FindAllStringSubmatch(text, -1)
	if len(matches) == 0 {
		return text, nil
	}

	var paths []string

	for _, m := range matches {
		p := strings.TrimSpace(m[1])
		if p != "" {
			paths = append(paths, p)
		}
	}

	cleaned := sendFileRe.ReplaceAllString(text, "")
	cleaned = strings.TrimSpace(cleaned)

	return cleaned, paths
}

// extractSendTo finds a <send-to>room-id</send-to> tag in text,
// returns the cleaned text with the tag stripped and the target room ID.
// If multiple tags are present, the first one wins.
func extractSendTo(text string) (string, string) {
	match := sendToRe.FindStringSubmatch(text)
	if match == nil {
		return text, ""
	}

	roomID := strings.TrimSpace(match[1])
	cleaned := sendToRe.ReplaceAllString(text, "")
	cleaned = strings.TrimSpace(cleaned)

	return cleaned, roomID
}

// App orchestrates command handling, inbox enqueueing, and file extraction.
type App struct {
	matrix              appMatrix
	worker              *Worker
	backgroundWorker    *Worker
	voiceWorker         *Worker
	voiceService        *VoiceService
	mcpWorker           *Worker
	inbox               *InboxStore
	outbox              *outboxStore
	groupTriggerScript  string
	groupTriggerTimeout time.Duration

	mu           sync.Mutex
	groupHistory map[string][]groupHistoryEntry
}

// NewApp creates a new App. The db connection is shared with the inbox
// and owned by the caller.
func NewApp(matrixClient appMatrix, worker *Worker, inbox *InboxStore, db *sql.DB) *App {
	return &App{
		matrix:              matrixClient,
		worker:              worker,
		inbox:               inbox,
		outbox:              newOutboxStore(db),
		groupTriggerTimeout: defaultGroupTriggerTimeout,
		groupHistory:        make(map[string][]groupHistoryEntry),
	}
}

// SetBackgroundWorker wires the background worker after construction.
func (a *App) SetBackgroundWorker(worker *Worker) { a.backgroundWorker = worker }

// SetVoice wires the optional HTTP voice service and its worker.
func (a *App) SetVoice(worker *Worker, service *VoiceService) {
	a.voiceWorker = worker
	a.voiceService = service
}

// SetMCPWorker wires the optional MCP worker.
func (a *App) SetMCPWorker(worker *Worker) { a.mcpWorker = worker }

// SetGroupTriggerScript configures the script that decides which group
// messages reach the agent.
func (a *App) SetGroupTriggerScript(script string) { a.groupTriggerScript = script }

// HandleMessage dispatches commands and enqueues normal Matrix messages.
func (a *App) HandleMessage(ctx context.Context, msg matrix.Message) { //nolint:cyclop // commands intentionally stay explicit
	// Record the incoming message so future reply-to references can quote it.
	a.outbox.Put(ctx, msg.ConversationID, msg.MessageID, msg.Text)

	switch strings.TrimSpace(msg.Text) {
	case "!help":
		a.handleHelp(ctx, msg)
	case "!restart":
		a.handleRestart(ctx, msg)
	case "!stop":
		a.handleStop(ctx, msg)
	case "!background-stop":
		a.handleBackgroundStop(ctx, msg)
	case "!background-restart":
		a.handleBackgroundRestart(ctx, msg)
	case "!voice-stop":
		a.handleVoiceStop(ctx, msg)
	case "!voice-restart":
		a.handleVoiceRestart(ctx, msg)
	case "!voice-compact":
		a.handleVoiceCompact(ctx, msg)
	case "!mcp-stop":
		a.handleMCPStop(ctx, msg)
	case "!compact":
		a.handleCompact(ctx, msg)
	case "!skills":
		a.handleSkills(ctx, msg)
	default:
		a.handlePrompt(ctx, msg)
	}
}

func (a *App) handleHelp(ctx context.Context, msg matrix.Message) {
	help := "Available commands:\n" +
		"  !help    — Show this help message\n" +
		"  !restart — Kill the current session and start fresh\n" +
		"  !stop    — Abort the currently running agent turn\n" +
		"  !compact — Compact conversation context to reduce token usage\n" +
		"  !skills  — List loaded skills\n" +
		"  !background-stop — Abort the active background task\n" +
		"  !background-restart — Restart the background session\n" +
		"  !voice-stop — Abort the active voice turn\n" +
		"  !voice-restart — Restart the voice session\n" +
		"  !voice-compact — Compact the voice session\n" +
		"  !mcp-stop — Abort the active MCP request"
	a.matrix.SendMessage(ctx, msg.ConversationID, help, "")
}

func (a *App) handleRestart(ctx context.Context, msg matrix.Message) {
	a.matrix.ResetConversation(ctx, msg.ConversationID)
	a.worker.Restart()
	a.matrix.SendMessage(ctx, msg.ConversationID, "Session restarted. Next message starts a fresh session (previous context discarded).", "")
}

func (a *App) handleBackgroundStop(ctx context.Context, msg matrix.Message) {
	if a.backgroundWorker == nil || !a.backgroundWorker.IsActive() || !a.backgroundWorker.Abort() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No active background task.", "")

		return
	}

	a.matrix.SendMessage(ctx, msg.ConversationID, "Aborted background task.", "")
}

func (a *App) handleBackgroundRestart(ctx context.Context, msg matrix.Message) {
	if a.backgroundWorker == nil {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No background worker.", "")

		return
	}

	a.backgroundWorker.Restart()
	a.matrix.SendMessage(ctx, msg.ConversationID, "Background session restarted. Next background task starts fresh.", "")
}

func (a *App) handleVoiceStop(ctx context.Context, msg matrix.Message) {
	if a.voiceWorker == nil || !a.voiceWorker.Abort() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No active voice turn.", "")

		return
	}

	a.matrix.SendMessage(ctx, msg.ConversationID, "Aborted voice turn.", "")
}

func (a *App) handleMCPStop(ctx context.Context, msg matrix.Message) {
	if a.mcpWorker == nil || !a.mcpWorker.Abort() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No active MCP request.", "")

		return
	}

	a.matrix.SendMessage(ctx, msg.ConversationID, "Aborted MCP request.", "")
}

func (a *App) handleVoiceRestart(ctx context.Context, msg matrix.Message) {
	if a.voiceService == nil {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No voice service.", "")

		return
	}

	if err := a.voiceService.restart(ctx); err != nil {
		slog.Error("voice restart failed", "error", err)
		a.matrix.SendMessage(ctx, msg.ConversationID, fmt.Sprintf("Voice restart failed: %v", err), "")

		return
	}

	a.matrix.SendMessage(ctx, msg.ConversationID, "Voice session restarted. Next voice request starts fresh.", "")
}

func (a *App) handleVoiceCompact(ctx context.Context, msg matrix.Message) {
	if a.voiceWorker == nil || !a.voiceWorker.IsActive() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No active voice session to compact.", "")

		return
	}

	result, err := a.voiceWorker.Compact(ctx)
	if err != nil {
		slog.Error("voice compact failed", "error", err)
		a.matrix.SendMessage(ctx, msg.ConversationID, fmt.Sprintf("Voice compaction failed: %v", err), "")

		return
	}

	reply := fmt.Sprintf("Compacted voice conversation (was %d tokens).\nSummary: %s", result.TokensBefore, result.Summary)
	a.matrix.SendMessage(ctx, msg.ConversationID, reply, "")
}

func (a *App) handleStop(ctx context.Context, msg matrix.Message) {
	if !a.worker.IsActive() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No active session.", "")

		return
	}

	if a.worker.Abort() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "Aborted current operation.", "")
	} else {
		a.matrix.SendMessage(ctx, msg.ConversationID, "Nothing running to stop.", "")
	}
}

func (a *App) handleCompact(ctx context.Context, msg matrix.Message) {
	if !a.worker.IsActive() {
		a.matrix.SendMessage(ctx, msg.ConversationID, "No active session to compact.", "")

		return
	}

	result, err := a.worker.Compact(ctx)
	if err != nil {
		slog.Error("compact failed", "conversation", msg.ConversationID, "error", err)
		a.matrix.SendMessage(ctx, msg.ConversationID, fmt.Sprintf("Compaction failed: %v", err), "")

		return
	}

	reply := fmt.Sprintf("Compacted conversation (was %d tokens).\nSummary: %s", result.TokensBefore, result.Summary)
	a.matrix.SendMessage(ctx, msg.ConversationID, reply, "")
}

func (a *App) handleSkills(ctx context.Context, msg matrix.Message) {
	a.matrix.SendMessage(ctx, msg.ConversationID, a.worker.SkillsSummary(), "")
}

func (a *App) handlePrompt(ctx context.Context, msg matrix.Message) {
	deliver := msg.IsDM || a.checkGroupMessage(ctx, msg)
	a.recordHistory(msg.ConversationID, senderLabel(msg), false, msg.Text)

	if !deliver {
		slog.Debug("app: ignoring unaddressed group message",
			"conversation", msg.ConversationID,
			"sender", msg.SenderName,
			"text", msg.Text,
		)

		a.recordRoomMessage(ctx, roomMessage{
			ConversationID: msg.ConversationID,
			RoomName:       msg.RoomName,
			MessageID:      msg.MessageID,
			Speaker:        "participant",
			SenderName:     msg.SenderName,
			SenderID:       msg.SenderID,
			Text:           msg.Text,
			At:             time.Now(),
		})

		return
	}

	a.worker.SetRoomID(msg.ConversationID)

	if a.backgroundWorker != nil {
		a.backgroundWorker.SetRoomID(msg.ConversationID)
		a.backgroundWorker.Notify()
	}

	quoted := ""
	if msg.ReplyToID != "" {
		quoted = a.outbox.Get(ctx, msg.ConversationID, msg.ReplyToID)
	}

	// The agent sees short IDs; resolveRoomID and outbox.Resolve map them back.
	shown := msg
	shown.ConversationID = a.shortRoomID(ctx, msg.ConversationID)
	shown.MessageID = a.outbox.ShortID(ctx, msg.ConversationID, msg.MessageID)

	content := a.buildPromptText(shown, quoted)
	if err := a.inbox.EnqueueUser(ctx, content, msg.ReplyToID, msg.ConversationID, msg.MessageID, !msg.IsDM); err != nil {
		slog.Error("failed to enqueue user message", "error", err)
		a.matrix.SendMessage(ctx, msg.ConversationID, fmt.Sprintf("Error: %v", err), "")

		return
	}

	a.worker.Notify()
}

// buildPromptText prepends room metadata and reply-quote context.
func (a *App) buildPromptText(msg matrix.Message, quoted string) string {
	promptText := msg.Text

	if msg.ReplyToID != "" {
		if quoted != "" {
			promptText = fmt.Sprintf("[user replied to message: %q]\n%s", quoted, promptText)
		} else {
			promptText = "[user replied to a message whose content is unavailable — ask for clarification if their message is unclear]\n" + promptText
		}
	}

	tags := buildContextTags(msg)
	if msg.MessageID != "" {
		messageIDTag := "<message-id>" + escape(msg.MessageID) + "</message-id>"
		if tags == "" {
			tags = messageIDTag
		} else {
			tags += "\n" + messageIDTag
		}
	}

	if tags != "" {
		promptText = tags + "\n\n" + promptText
	}

	return promptText
}

func senderLabel(msg matrix.Message) string {
	if msg.SenderName != "" {
		return msg.SenderName
	}

	return msg.SenderID
}

// recordHistory appends to the room's recent messages, keeping the newest few.
func (a *App) recordHistory(conversationID, from string, isBot bool, text string) {
	a.mu.Lock()
	defer a.mu.Unlock()

	history := a.groupHistory[conversationID]
	history = append(history, groupHistoryEntry{
		from:  from,
		isBot: isBot,
		text:  truncateRunes(text, maxGroupHistoryRunes),
		at:    time.Now(),
	})
	a.groupHistory[conversationID] = history[max(0, len(history)-maxGroupHistory):]
}

// recordAgentActivity records what the bot posted in a room: its text, the
// files it sent, or a reaction.
func (a *App) recordAgentActivity(ctx context.Context, conversationID, text string, files []string) {
	if text == "" && len(files) == 0 {
		return
	}

	name, userID := a.matrix.OwnIdentity(ctx, conversationID)
	if name == "" {
		name = userID
	}

	if text != "" {
		a.recordHistory(conversationID, name, true, text)
	}

	for _, file := range files {
		a.recordHistory(conversationID, name, true, "[sent a file: "+filepath.Base(file)+"]")
	}
}

// checkGroupMessage asks the group trigger script whether the agent should
// see msg. A failing script delivers the message, so the bot is never deaf.
func (a *App) checkGroupMessage(ctx context.Context, msg matrix.Message) bool {
	if a.groupTriggerScript == "" {
		return true
	}

	a.mu.Lock()
	input := newGroupTriggerInput(a.groupHistory[msg.ConversationID], senderLabel(msg), msg.Text, time.Now())
	a.mu.Unlock()

	respond, output, err := runGroupTrigger(ctx, a.groupTriggerScript, a.groupTriggerTimeout, input)
	if err != nil {
		slog.Warn("group trigger failed, delivering message",
			"conversation", msg.ConversationID, "error", err, "output", output)

		return true
	}

	slog.Info("group trigger", "conversation", msg.ConversationID, "respond", respond, "output", output)

	return respond
}

// buildContextTags returns a block of XML-style context tags derived from the
// message's enrichment fields. Each non-zero field produces one line; zero
// values are omitted entirely. Returns an empty string if no fields are set.
func buildContextTags(msg matrix.Message) string {
	var lines []string

	if msg.SenderID != "" {
		lines = append(lines, "<from-id>"+escape(msg.SenderID)+"</from-id>")
	}

	if msg.ConversationID != "" {
		lines = append(lines, "<room-id>"+escape(msg.ConversationID)+"</room-id>")
	}

	lines = append(lines, "<is-dm>"+strconv.FormatBool(msg.IsDM)+"</is-dm>")

	if msg.SenderName != "" {
		lines = append(lines, "<from-name>"+escape(msg.SenderName)+"</from-name>")
	}

	if msg.RoomName != "" && !msg.IsDM {
		lines = append(lines, "<room-name>"+escape(msg.RoomName)+"</room-name>")
	}

	if msg.RoomSize > 0 && !msg.IsDM {
		lines = append(lines, "<room-size>"+strconv.Itoa(msg.RoomSize)+"</room-size>")
	}

	return strings.Join(lines, "\n")
}

func escape(s string) string {
	return html.EscapeString(s)
}

// sendReaction validates that the requested message is known in the current
// conversation before sending it to Matrix.
func (a *App) sendReaction(ctx context.Context, conversationID string, reaction reactionRequest) {
	messageID := a.outbox.Resolve(ctx, conversationID, reaction.messageID)

	target := a.outbox.Get(ctx, conversationID, messageID)
	if target == "" {
		slog.Warn("ignoring reaction to unknown message",
			"conversation", conversationID,
			"message", reaction.messageID,
		)

		return
	}

	if err := a.matrix.SendReaction(ctx, conversationID, messageID, reaction.emoji); err != nil {
		slog.Warn("failed to send reaction",
			"conversation", conversationID,
			"message", reaction.messageID,
			"error", err,
		)
	} else {
		a.recordAgentActivity(ctx, conversationID, "[reacted "+reaction.emoji+" to: "+target+"]", nil)
	}
}

// deliverVoiceReply applies Matrix control tags and returns the text that
// should be spoken by Home Assistant.
func (a *App) deliverVoiceReply(ctx context.Context, defaultRoomID, reply string) VoiceResponse {
	cleanReply, targetRoom := extractSendTo(reply)
	cleanReply, filePaths := extractSendFiles(cleanReply)

	if targetRoom != "" {
		return a.deliverVoiceToMatrix(ctx, a.resolveRoomID(ctx, targetRoom), cleanReply, filePaths)
	}

	return a.deliverVoiceFiles(ctx, defaultRoomID, cleanReply, filePaths)
}

func (a *App) deliverVoiceToMatrix(ctx context.Context, roomID, text string, filePaths []string) VoiceResponse {
	sentFiles, filesOK := a.uploadVoiceFiles(ctx, roomID, filePaths)
	a.recordAgentActivity(ctx, roomID, "", sentFiles)

	var sentID string

	messageOK := true

	if text != "" {
		sentID = a.matrix.SendMessage(ctx, roomID, text, "")

		messageOK = sentID != ""
		if messageOK {
			a.outbox.Put(ctx, roomID, sentID, text)
			a.recordAgentActivity(ctx, roomID, text, nil)
		}
	}

	delivered := sentID != "" || len(sentFiles) > 0

	if !delivered || !messageOK || !filesOK {
		return VoiceResponse{Text: "I couldn't send that to chat.", Delivery: deliveryVoice}
	}

	return VoiceResponse{Text: "I sent that to chat.", Delivery: deliveryMatrix}
}

func (a *App) deliverVoiceFiles(ctx context.Context, defaultRoomID, text string, filePaths []string) VoiceResponse {
	sentFiles, filesOK := a.uploadVoiceFiles(ctx, defaultRoomID, filePaths)
	a.recordAgentActivity(ctx, defaultRoomID, "", sentFiles)

	if !filesOK {
		text = "I couldn't send the file to chat."
	} else if len(filePaths) > 0 {
		if text == "" {
			text = "I sent your file to chat."
		}
	}

	return VoiceResponse{Text: text, Delivery: deliveryVoice}
}

func (a *App) uploadVoiceFiles(ctx context.Context, roomID string, filePaths []string) ([]string, bool) {
	if len(filePaths) == 0 {
		return nil, true
	}

	if roomID == "" {
		return nil, false
	}

	sentFiles, _ := a.sendFiles(ctx, roomID, filePaths, false)

	return sentFiles, len(sentFiles) == len(filePaths)
}

// sendReplyWithFiles extracts <sendfile> tags, uploads each file, and sends
// the final text reply. reportFileErrors controls whether upload failures are
// included in that reply; background infrastructure failures remain log-only.
func (a *App) sendReplyWithFiles(
	ctx context.Context,
	conversationID, reply, replyToID string,
	reportFileErrors, background bool,
) {
	slog.Info("sending reply", "conversation", conversationID, "len", len(reply))
	slog.Debug("outgoing reply content", "conversation", conversationID, "content", reply)

	cleanReply, filePaths := extractSendFiles(reply)
	sentFiles, fileErrors := a.sendFiles(ctx, conversationID, filePaths, reportFileErrors)
	a.recordAgentActivity(ctx, conversationID, "", sentFiles)

	cleanReply += fileErrors

	var sentID string

	if cleanReply != "" {
		sentID = a.matrix.SendMessage(ctx, conversationID, cleanReply, replyToID)
		a.outbox.Put(ctx, conversationID, sentID, cleanReply)

		if sentID != "" {
			a.recordAgentActivity(ctx, conversationID, cleanReply, nil)
		}
	}

	if background {
		a.recordBackgroundReply(ctx, conversationID, sentID, cleanReply, sentFiles)
	}
}

func (a *App) sendFiles(
	ctx context.Context,
	conversationID string,
	filePaths []string,
	reportErrors bool,
) ([]string, string) {
	var (
		fileSendErrors strings.Builder
		sentFiles      []string
	)

	for _, fp := range filePaths {
		slog.Info("sending file", "conversation", conversationID, "path", fp)

		if err := a.matrix.SendFile(ctx, conversationID, fp); err != nil {
			slog.Error("failed to send file", "conversation", conversationID, "path", fp, "error", err)

			if reportErrors {
				fmt.Fprintf(&fileSendErrors, "\n\n(failed to send file %s: %v)", filepath.Base(fp), err)
			}

			continue
		}

		sentFiles = append(sentFiles, fp)
	}

	return sentFiles, fileSendErrors.String()
}

func (a *App) recordBackgroundReply(
	ctx context.Context,
	conversationID, messageID, text string,
	filePaths []string,
) {
	if messageID == "" && len(filePaths) == 0 {
		return
	}

	name, userID := a.matrix.OwnIdentity(ctx, conversationID)
	parts := make([]string, 0, len(filePaths)+1)

	if messageID != "" {
		parts = append(parts, text)
	}

	for _, fp := range filePaths {
		parts = append(parts, "[You sent a file: "+fp+"]")
	}

	a.recordRoomMessage(ctx, roomMessage{
		ConversationID: conversationID,
		MessageID:      messageID,
		Speaker:        "you",
		Worker:         "background",
		SenderName:     name,
		SenderID:       userID,
		Text:           strings.Join(parts, "\n"),
		At:             time.Now(),
	})
}

// shortRoomID abbreviates a room ID for the agent; see shortID.
func (a *App) shortRoomID(ctx context.Context, roomID string) string {
	return shortID(roomID, a.matrix.JoinedRoomIDs(ctx))
}

// resolveRoomID expands a short room ID from the agent. Anything that isn't
// a prefix of exactly one joined room is returned unchanged.
func (a *App) resolveRoomID(ctx context.Context, roomID string) string {
	if full := resolveID(roomID, a.matrix.JoinedRoomIDs(ctx)); full != "" {
		return full
	}

	return roomID
}

// systemPrompt returns the full system prompt including Matrix-specific context.
func (a *App) systemPrompt(basePrompt string) string {
	extra := a.matrix.SystemPromptExtra()
	if extra == "" {
		return basePrompt
	}

	return strings.TrimRight(basePrompt, "\n") + "\n\n" + extra
}
