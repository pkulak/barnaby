package main

import (
	"context"
	"errors"
	"fmt"
	"maps"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// #nosec G101 -- test-only bearer token.
const testMCPToken = "mcp-test-token"

type testMCPEnv struct {
	service  *MCPService
	worker   *Worker
	inbox    *InboxStore
	app      *App
	matrix   *mockMatrix
	stateDir string
	clock    *testClock
}

type testClock struct {
	mu  sync.Mutex
	now time.Time
}

func (c *testClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()

	return c.now
}

func (c *testClock) Advance(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()

	c.now = c.now.Add(d)
}

// newTestMCPEnv creates an MCP service and worker backed by testdata/fake-pi.
// The worker loop only runs when start is true.
func newTestMCPEnv(t *testing.T, skills []string, start bool) testMCPEnv {
	t.Helper()

	script, err := filepath.Abs("testdata/fake-pi")
	if err != nil {
		t.Fatal(err)
	}

	stateDir := t.TempDir()
	db := newTestDBAt(t.Context(), t, stateDir+"/mcp.db")
	inbox := newTestInboxWithDB(t.Context(), t, db)
	worker := NewMCPWorker(inbox, PiConfig{
		BinaryPath: "bash",
		BinaryArgs: []string{script},
		SessionDir: filepath.Join(stateDir, "mcp"),
		StateDir:   stateDir,
		WorkingDir: stateDir,
		NoContinue: true,
	})
	clock := &testClock{now: time.Now()}
	worker.mcpSessions.now = clock.Now

	matrixClient := &mockMatrix{}
	app := NewApp(matrixClient, NewWorker(inbox, PiConfig{}), inbox, db)
	app.SetMCPWorker(worker)
	service := NewMCPService(testMCPToken, inbox, worker, skills)
	worker.SetApp(app)
	worker.SetMatrix(matrixClient)
	worker.SetMCPService(service)

	if start {
		ctx, cancel := context.WithCancel(t.Context())
		done := spawnWorker(ctx, worker)

		t.Cleanup(func() {
			cancel()
			<-done
		})
	}

	return testMCPEnv{
		service:  service,
		worker:   worker,
		inbox:    inbox,
		app:      app,
		matrix:   matrixClient,
		stateDir: stateDir,
		clock:    clock,
	}
}

// headerTransport adds fixed headers to every request.
type headerTransport struct {
	header http.Header
}

func (h headerTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	r = r.Clone(r.Context())
	maps.Copy(r.Header, h.header)

	return http.DefaultTransport.RoundTrip(r) //nolint:wrapcheck // transparent test transport
}

func connectTestMCP(t *testing.T, url string, header http.Header) (*mcp.ClientSession, error) {
	t.Helper()

	client := mcp.NewClient(&mcp.Implementation{Name: "test"}, nil)

	session, err := client.Connect(t.Context(), &mcp.StreamableClientTransport{
		Endpoint:             url,
		HTTPClient:           &http.Client{Transport: headerTransport{header: header}},
		DisableStandaloneSSE: true,
		MaxRetries:           -1,
	}, nil)
	if err != nil {
		return nil, fmt.Errorf("connecting to MCP: %w", err)
	}

	t.Cleanup(func() { _ = session.Close() })

	return session, nil
}

func newTestMCPServer(t *testing.T, service *MCPService) *httptest.Server {
	t.Helper()

	voiceWorker := NewVoiceWorker(service.inbox, PiConfig{SessionDir: t.TempDir()})
	voice := NewVoiceService(HTTPConfig{BearerToken: testVoiceToken}, service.inbox, voiceWorker)
	voice.SetMCP(service)

	server := httptest.NewServer(voice.server.Handler)
	t.Cleanup(server.Close)

	return server
}

func writeTestSkill(t *testing.T, dir, name, content string) string {
	t.Helper()

	path := filepath.Join(dir, name)
	if err := os.MkdirAll(path, 0o750); err != nil {
		t.Fatal(err)
	}

	if err := os.WriteFile(filepath.Join(path, "SKILL.md"), []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	return path
}

func readLogLines(t *testing.T, path string) []string {
	t.Helper()

	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}

	if err != nil {
		t.Fatal(err)
	}

	return strings.Fields(string(data))
}

func TestMCPHTTPAuthentication(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, false)
	server := newTestMCPServer(t, env.service)

	for name, header := range map[string]http.Header{
		"missing": {},
		"wrong":   {"Authorization": {"Bearer wrong"}},
		"voice":   {"Authorization": {"Bearer " + testVoiceToken}},
	} {
		if _, err := connectTestMCP(t, server.URL+mcpPath, header); err == nil {
			t.Errorf("%s token connected", name)
		}
	}

	request := httptest.NewRequestWithContext(t.Context(), http.MethodPost, mcpPath, strings.NewReader("{}"))
	response := httptest.NewRecorder()
	env.service.Handler().ServeHTTP(response, request)

	if response.Code != http.StatusUnauthorized {
		t.Fatalf("missing token status = %d", response.Code)
	}

	if _, err := connectTestMCP(t, server.URL+mcpPath, http.Header{"Authorization": {"Bearer " + testMCPToken}}); err != nil {
		t.Fatalf("valid token failed: %v", err)
	}

	health, err := http.Get(server.URL + "/healthz") //nolint:noctx // test request
	if err != nil {
		t.Fatal(err)
	}
	defer health.Body.Close()

	if health.StatusCode != http.StatusOK {
		t.Fatalf("health status = %d", health.StatusCode)
	}
}

func TestMCPToolsListDescribesSkills(t *testing.T) {
	t.Parallel()

	skillsDir := t.TempDir()
	skills := []string{
		writeTestSkill(t, skillsDir, "weather-dir", "---\nname: weather\ndescription: \"Look up the weather forecast.\"\n---\n# Weather\n"),
		writeTestSkill(t, skillsDir, "lights", "---\ndescription: >-\n  Turn lights on\n  and off.\n---\n"),
		writeTestSkill(t, skillsDir, "plain", "# No frontmatter\n"),
	}

	env := newTestMCPEnv(t, skills, false)
	server := newTestMCPServer(t, env.service)

	session, err := connectTestMCP(t, server.URL+mcpPath, http.Header{"Authorization": {"Bearer " + testMCPToken}})
	if err != nil {
		t.Fatal(err)
	}

	result, err := session.ListTools(t.Context(), nil)
	if err != nil {
		t.Fatal(err)
	}

	if len(result.Tools) != 1 || result.Tools[0].Name != "ask" {
		t.Fatalf("tools = %+v", result.Tools)
	}

	for _, want := range []string{
		mcpToolIntro,
		"- weather: Look up the weather forecast.",
		"- lights: Turn lights on and off.",
		"- plain\n",
	} {
		if !strings.Contains(result.Tools[0].Description+"\n", want) {
			t.Errorf("description %q missing %q", result.Tools[0].Description, want)
		}
	}
}

func TestMCPAskReturnsWorkerReply(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, true)
	server := newTestMCPServer(t, env.service)

	session, err := connectTestMCP(t, server.URL+mcpPath, http.Header{
		"Authorization":       {"Bearer " + testMCPToken},
		mcpUserHeader:         {"alice@example.com"},
		mcpConversationHeader: {"conv-1"},
	})
	if err != nil {
		t.Fatal(err)
	}

	result, err := session.CallTool(t.Context(), &mcp.CallToolParams{Name: "ask", Arguments: map[string]any{"request": "hello"}})
	if err != nil {
		t.Fatal(err)
	}

	text, ok := result.Content[0].(*mcp.TextContent)
	if result.IsError || len(result.Content) != 1 || !ok || text.Text != "ok" {
		t.Fatalf("result = %+v", result)
	}

	if _, ok := env.worker.mcpSessions.sessions[mcpSessionKey{user: "alice@example.com", conversation: "conv-1"}]; !ok {
		t.Fatalf("sessions = %+v", env.worker.mcpSessions.sessions)
	}

	result, err = session.CallTool(t.Context(), &mcp.CallToolParams{Name: "ask", Arguments: map[string]any{"request": "   "}})
	if err != nil {
		t.Fatal(err)
	}

	if !result.IsError {
		t.Fatalf("blank request result = %+v", result)
	}
}

func TestMCPAskReturnsSentImages(t *testing.T) { //nolint:cyclop // end-to-end result assertions stay together
	t.Parallel()

	env := newTestMCPEnv(t, nil, true)
	server := newTestMCPServer(t, env.service)

	png := []byte("\x89PNG\r\n\x1a\nfake image data")
	for name, data := range map[string][]byte{"image.png": png, "notes.txt": []byte("notes")} {
		if err := os.WriteFile(filepath.Join(env.stateDir, name), data, 0o600); err != nil {
			t.Fatal(err)
		}
	}

	session, err := connectTestMCP(t, server.URL+mcpPath, http.Header{"Authorization": {"Bearer " + testMCPToken}})
	if err != nil {
		t.Fatal(err)
	}

	result, err := session.CallTool(t.Context(), &mcp.CallToolParams{Name: "ask", Arguments: map[string]any{"request": "mcp-image-test"}})
	if err != nil {
		t.Fatal(err)
	}

	if result.IsError || len(result.Content) != 2 {
		t.Fatalf("result = %+v", result)
	}

	text, ok := result.Content[0].(*mcp.TextContent)
	if !ok || text.Text != "Here it is\n\n(notes.txt could not be returned: only PNG, JPEG, GIF, and WebP images can be returned.)" {
		t.Fatalf("text = %+v", result.Content[0])
	}

	image, ok := result.Content[1].(*mcp.ImageContent)
	if !ok || image.MIMEType != "image/png" || string(image.Data) != string(png) {
		t.Fatalf("image = %+v", result.Content[1])
	}

	if len(env.matrix.sentFiles) != 0 {
		t.Fatalf("files sent to Matrix: %v", env.matrix.sentFiles)
	}
}

func TestMCPResultDescribesImageOnlyReplies(t *testing.T) {
	t.Parallel()

	image := &mcp.ImageContent{Data: []byte("x"), MIMEType: "image/png"}
	result := mcpResult("", []*mcp.ImageContent{image})

	if text, ok := result.Content[0].(*mcp.TextContent); len(result.Content) != 2 || !ok || text.Text == "" || result.Content[1] != image {
		t.Fatalf("result = %+v", result)
	}

	if result := mcpResult("", nil); len(result.Content) != 1 {
		t.Fatalf("empty result = %+v", result)
	}
}

func TestMCPReplyImagesReportsUnreturnableFiles(t *testing.T) {
	t.Parallel()

	dir := t.TempDir()
	large := filepath.Join(dir, "large.png")

	if err := os.WriteFile(large, make([]byte, mcpMaxImageBytes+1), 0o600); err != nil {
		t.Fatal(err)
	}

	text, images := mcpReplyImages("", []string{large, filepath.Join(dir, "missing.png")})

	want := "(large.png could not be returned: larger than 10 MiB.)\n\n(missing.png could not be returned: file not found.)"
	if text != want || len(images) != 0 {
		t.Fatalf("text = %q, images = %d; want %q", text, len(images), want)
	}
}

func TestMCPSessionKeyFromHeader(t *testing.T) {
	t.Parallel()

	header := func(user, conversation string) http.Header {
		h := http.Header{}
		if user != "" {
			h.Set(mcpUserHeader, user) //nolint:canonicalheader // documented spelling; Set canonicalizes it
		}

		if conversation != "" {
			h.Set(mcpConversationHeader, conversation) //nolint:canonicalheader // documented spelling; Set canonicalizes it
		}

		return h
	}

	tests := []struct {
		name   string
		header http.Header
		want   mcpSessionKey
	}{
		{"both", header("alice", "c1"), mcpSessionKey{user: "alice", conversation: "c1"}},
		{"user only", header("alice", ""), mcpSessionKey{user: "alice"}},
		{"none", nil, mcpSessionKey{}},
	}

	for _, tt := range tests {
		got, err := mcpSessionKeyFromHeader(tt.header)
		if err != nil || got != tt.want {
			t.Errorf("%s: key = %+v, err = %v; want %+v", tt.name, got, err, tt.want)
		}
	}

	if _, err := mcpSessionKeyFromHeader(header(strings.Repeat("x", voiceMaxContextBytes+1), "")); !errors.Is(err, errMCPHeaders) {
		t.Fatalf("oversized header error = %v", err)
	}
}

func TestBuildMCPPrompt(t *testing.T) {
	t.Parallel()

	prompt := buildMCPPrompt(mcpSessionKey{user: "a&b", conversation: "c1"}, "lights <now>")
	want := "<mcp-context>\n<user>a&amp;b</user>\n<conversation>c1</conversation>\n</mcp-context>\n<mcp-request>lights &lt;now&gt;</mcp-request>"

	if prompt != want {
		t.Fatalf("prompt = %q, want %q", prompt, want)
	}

	if prompt := buildMCPPrompt(mcpSessionKey{}, "hi"); prompt != "<mcp-request>hi</mcp-request>" {
		t.Fatalf("prompt without context = %q", prompt)
	}
}

func runTestMCPRequest(t *testing.T, env testMCPEnv, key mcpSessionKey, text string) (string, error) {
	t.Helper()

	callID, call, err := env.service.submit(t.Context(), key, text)
	if err != nil {
		t.Fatal(err)
	}

	select {
	case <-call.done:
		return call.text, call.err
	case <-time.After(5 * time.Second):
		env.service.cancel(t.Context(), callID, errMCPCancelled)
		t.Fatal("MCP request did not complete")

		return "", nil
	}
}

func TestMCPWorkerSessionSelection(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, true)
	alice := mcpSessionKey{user: "alice", conversation: "c1"}
	bob := mcpSessionKey{user: "bob"}
	newSessionLog := filepath.Join(env.stateDir, "new_session.log")
	switchLog := filepath.Join(env.stateDir, "switch_session.log")
	aliceFile := filepath.Join(env.stateDir, "session-1.jsonl")

	steps := []struct {
		name         string
		key          mcpSessionKey
		before       func()
		newSessions  int
		switchedTo   []string
		wantFileName string
	}{
		{name: "new key", key: alice, newSessions: 1, wantFileName: "session-1.jsonl"},
		{name: "loaded key", key: alice, newSessions: 1, wantFileName: "session-1.jsonl"},
		{name: "second key", key: bob, newSessions: 2, wantFileName: "session-2.jsonl"},
		{name: "changed key", key: alice, newSessions: 2, switchedTo: []string{aliceFile}, wantFileName: "session-1.jsonl"},
		{
			name: "after restart", key: alice, before: env.worker.stopPi,
			newSessions: 2, switchedTo: []string{aliceFile, aliceFile}, wantFileName: "session-1.jsonl",
		},
		{
			name: "expired", key: alice, before: func() { env.clock.Advance(mcpSessionTTL + time.Minute) },
			newSessions: 3, switchedTo: []string{aliceFile, aliceFile}, wantFileName: "session-3.jsonl",
		},
	}

	for _, step := range steps {
		if step.before != nil {
			step.before()
		}

		reply, err := runTestMCPRequest(t, env, step.key, "hello")
		if err != nil || reply != "ok" {
			t.Fatalf("%s: reply = %q, err = %v", step.name, reply, err)
		}

		if got := len(readLogLines(t, newSessionLog)); got != step.newSessions {
			t.Errorf("%s: new sessions = %d, want %d", step.name, got, step.newSessions)
		}

		if got := readLogLines(t, switchLog); strings.Join(got, ",") != strings.Join(step.switchedTo, ",") {
			t.Errorf("%s: switches = %v, want %v", step.name, got, step.switchedTo)
		}

		if got := filepath.Base(env.worker.mcpSessions.sessions[step.key].file); got != step.wantFileName {
			t.Errorf("%s: session file = %q, want %q", step.name, got, step.wantFileName)
		}
	}
}

func TestMCPWorkerCancelledSwitchFailsRequest(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, false)
	key := mcpSessionKey{user: "alice"}
	env.worker.mcpSessions.sessions[key] = mcpSession{file: filepath.Join(env.stateDir, "cancel-switch.jsonl"), lastUsed: env.clock.Now()}

	ctx, cancel := context.WithCancel(t.Context())
	done := spawnWorker(ctx, env.worker)

	t.Cleanup(func() {
		cancel()
		<-done
	})

	if _, err := runTestMCPRequest(t, env, key, "hello"); !errors.Is(err, errMCPFailed) {
		t.Fatalf("error = %v, want %v", err, errMCPFailed)
	}

	if _, ok := env.worker.mcpSessions.sessions[key]; ok {
		t.Fatal("failed session was kept")
	}

	if reply, err := runTestMCPRequest(t, env, key, "hello"); err != nil || reply != "ok" {
		t.Fatalf("retry reply = %q, err = %v", reply, err)
	}
}

func TestMCPQueueLimitAndCancellation(t *testing.T) { //nolint:cyclop // queue lifecycle assertions stay together
	t.Parallel()

	env := newTestMCPEnv(t, nil, false)

	var (
		firstID   string
		firstCall *mcpCall
	)

	for i := range mcpMaxPending {
		callID, call, err := env.service.submit(t.Context(), mcpSessionKey{}, "queued")
		if err != nil {
			t.Fatal(err)
		}

		if i == 0 {
			firstID, firstCall = callID, call
		}
	}

	if _, _, err := env.service.submit(t.Context(), mcpSessionKey{}, "too many"); !errors.Is(err, errMCPQueueFull) {
		t.Fatalf("overflow error = %v", err)
	}

	firstCall.deadline = time.Now().Add(10 * time.Millisecond)
	if _, err := env.service.await(t.Context(), firstID, firstCall); !errors.Is(err, errMCPTimeout) {
		t.Fatalf("await error = %v, want timeout", err)
	}

	queued := 0

	for {
		item, err := env.inbox.DequeueMCP(t.Context())
		if err != nil {
			break
		}

		if item.MessageID == firstID {
			t.Fatal("timed-out request remained in the queue")
		}

		queued++
	}

	if queued != mcpMaxPending-1 {
		t.Fatalf("queued = %d", queued)
	}

	if _, _, err := env.service.submit(t.Context(), mcpSessionKey{}, "fits again"); err != nil {
		t.Fatalf("submit after timeout = %v", err)
	}
}

func TestMCPExpiredRequestDoesNotRun(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, false)

	callID, call, err := env.service.submit(t.Context(), mcpSessionKey{}, "hello")
	if err != nil {
		t.Fatal(err)
	}

	call.deadline = time.Now().Add(-time.Second)

	if _, _, ok := env.service.begin(callID); ok {
		t.Fatal("expired request began processing")
	}

	if !errors.Is(call.err, errMCPTimeout) {
		t.Fatalf("call error = %v", call.err)
	}
}

func TestApp_MCPStop(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, true)
	lastMessage := func() string {
		env.matrix.mu.Lock()
		defer env.matrix.mu.Unlock()

		return env.matrix.sentMessages[len(env.matrix.sentMessages)-1].text
	}

	sendCommand(env.app, "!mcp-stop")

	if got := lastMessage(); got != "No active MCP request." {
		t.Fatalf("idle stop reply = %q", got)
	}

	callID, call, err := env.service.submit(t.Context(), mcpSessionKey{}, "hang-test")
	if err != nil {
		t.Fatal(err)
	}

	deadline := time.Now().Add(5 * time.Second)
	for sendCommand(env.app, "!mcp-stop"); lastMessage() != "Aborted MCP request."; sendCommand(env.app, "!mcp-stop") {
		if time.Now().After(deadline) {
			env.service.cancel(t.Context(), callID, errMCPCancelled)
			t.Fatal("MCP request never became active")
		}

		time.Sleep(10 * time.Millisecond)
	}

	<-call.done

	if !errors.Is(call.err, errMCPCancelled) {
		t.Fatalf("call error = %v", call.err)
	}
}

func TestMCPClientDisconnectCancelsRequest(t *testing.T) {
	t.Parallel()

	env := newTestMCPEnv(t, nil, true)
	server := newTestMCPServer(t, env.service)

	session, err := connectTestMCP(t, server.URL+mcpPath, http.Header{"Authorization": {"Bearer " + testMCPToken}})
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(t.Context(), 500*time.Millisecond)
	defer cancel()

	if _, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "ask", Arguments: map[string]any{"request": "hang-test"}}); err == nil {
		t.Fatal("hanging request returned")
	}

	deadline := time.Now().Add(5 * time.Second)

	for {
		env.service.mu.Lock()
		pending := len(env.service.calls)
		env.service.mu.Unlock()

		if pending == 0 && !env.worker.IsActive() {
			return
		}

		if time.Now().After(deadline) {
			t.Fatalf("pending calls = %d, pi active = %v after disconnect", pending, env.worker.IsActive())
		}

		time.Sleep(10 * time.Millisecond)
	}
}
