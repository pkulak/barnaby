package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	testChatProvider       = "chat-provider"
	testChatModel          = "chat-model"
	testSessionDir         = "/tmp/barnaby"
	testBackgroundProvider = "background-provider"
)

func TestMatrixConfig_ValidateReportsAllMissing(t *testing.T) {
	t.Parallel()

	err := (MatrixConfig{}).validate()
	if err == nil {
		t.Fatal("expected error for empty MatrixConfig")
	}

	msg := err.Error()
	for _, want := range []string{
		"BARNABY_MATRIX_HOMESERVER",
		"BARNABY_MATRIX_USER_ID",
		"BARNABY_MATRIX_ACCESS_TOKEN",
	} {
		if !strings.Contains(msg, want) {
			t.Errorf("error %q missing %q", msg, want)
		}
	}
}

// testEnv returns a getenv function backed by a map.
func testEnv(m map[string]string) func(string) string {
	return func(key string) string {
		return m[key]
	}
}

// baseMatrixEnv returns the minimum environment needed for Matrix.
func baseMatrixEnv() map[string]string {
	// #nosec G101 -- these are non-functional test-only Matrix credentials.
	return map[string]string{
		"BARNABY_MATRIX_HOMESERVER":   "https://matrix.example.com",
		"BARNABY_MATRIX_USER_ID":      "@bot:example.com",
		"BARNABY_MATRIX_ACCESS_TOKEN": "syt_test_token",
	}
}

func TestLoadConfig_BackgroundPiOverrides(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_PI_SESSION_DIR"] = testSessionDir
	env["BARNABY_PI_PROVIDER"] = testChatProvider
	env["BARNABY_PI_MODEL"] = testChatModel
	env["BARNABY_BACKGROUND_PI_MODEL"] = "background-model"

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	if cfg.BackgroundPi.SessionDir != filepath.Join(os.TempDir(), "barnaby-background") {
		t.Errorf("background session dir = %q", cfg.BackgroundPi.SessionDir)
	}

	if !cfg.BackgroundPi.NoContinue {
		t.Error("background NoContinue = false, want true")
	}

	if cfg.BackgroundPi.StateDir != "/tmp/barnaby" {
		t.Errorf("background state dir = %q", cfg.BackgroundPi.StateDir)
	}

	if cfg.BackgroundPi.Provider != testChatProvider || cfg.BackgroundPi.Model != "background-model" {
		t.Errorf("background model config = %q/%q", cfg.BackgroundPi.Provider, cfg.BackgroundPi.Model)
	}
}

func TestLoadConfig_CompactOnIdle(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_PI_COMPACT_ON_IDLE"] = "true"

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	if !cfg.Pi.CompactOnIdle {
		t.Error("chat CompactOnIdle = false, want true")
	}

	if cfg.BackgroundPi.CompactOnIdle {
		t.Error("background CompactOnIdle = true, want false")
	}
}

func TestLoadConfig_VoiceInheritsChatConfig(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_PI_SESSION_DIR"] = testSessionDir
	env["BARNABY_PI_PROVIDER"] = testChatProvider
	env["BARNABY_PI_MODEL"] = testChatModel

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	if cfg.VoicePi.SessionDir != "/tmp/barnaby/voice" {
		t.Errorf("voice session dir = %q", cfg.VoicePi.SessionDir)
	}

	if cfg.VoicePi.Provider != testChatProvider || cfg.VoicePi.Model != testChatModel {
		t.Errorf("voice model config = %q/%q", cfg.VoicePi.Provider, cfg.VoicePi.Model)
	}
}

func TestLoadConfig_HTTPRequiresBearerToken(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_HTTP_LISTEN"] = "127.0.0.1:8787"

	_, err := loadConfig(testEnv(env))
	if err == nil || !strings.Contains(err.Error(), "BARNABY_HTTP_BEARER_TOKEN") {
		t.Fatalf("error = %v, want missing bearer token", err)
	}

	env["BARNABY_HTTP_BEARER_TOKEN"] = "secret"

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	if cfg.HTTP.Listen != "127.0.0.1:8787" || cfg.HTTP.BearerToken != "secret" {
		t.Errorf("HTTP config = %+v", cfg.HTTP)
	}
}

func TestLoadConfig_MCPDisabledByDefault(t *testing.T) {
	t.Parallel()

	cfg, err := loadConfig(testEnv(baseMatrixEnv()))
	if err != nil {
		t.Fatal(err)
	}

	if cfg.HTTP.MCPBearerToken != "" {
		t.Errorf("MCP token = %q, want disabled", cfg.HTTP.MCPBearerToken)
	}

	if cfg.MCPPi.SessionDir != filepath.Join(os.TempDir(), "barnaby-mcp") || !cfg.MCPPi.NoContinue || cfg.MCPPi.CompactOnIdle {
		t.Errorf("MCP Pi config = %+v", cfg.MCPPi)
	}
}

func TestLoadConfig_MCPRequiresHTTPListener(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_MCP_BEARER_TOKEN"] = "mcp-secret"

	_, err := loadConfig(testEnv(env))
	if err == nil || !strings.Contains(err.Error(), "BARNABY_HTTP_LISTEN") {
		t.Fatalf("error = %v, want missing listener", err)
	}

	env["BARNABY_HTTP_LISTEN"] = "127.0.0.1:8788"
	env["BARNABY_HTTP_BEARER_TOKEN"] = "voice-secret"
	env["BARNABY_MCP_SESSION_DIR"] = "/tmp/mcp-sessions"
	env["BARNABY_PI_COMPACT_ON_IDLE"] = "1"

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	if cfg.HTTP.MCPBearerToken != "mcp-secret" || cfg.MCPPi.SessionDir != "/tmp/mcp-sessions" || cfg.MCPPi.CompactOnIdle {
		t.Errorf("MCP config = %+v / %+v", cfg.HTTP, cfg.MCPPi)
	}
}

func TestLoadConfig_BackgroundProviderOverrideKeepsChatModel(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_PI_PROVIDER"] = testChatProvider
	env["BARNABY_PI_MODEL"] = testChatModel
	env["BARNABY_BACKGROUND_PI_PROVIDER"] = testBackgroundProvider

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	if cfg.BackgroundPi.Provider != testBackgroundProvider || cfg.BackgroundPi.Model != testChatModel {
		t.Errorf("background model config = %q/%q", cfg.BackgroundPi.Provider, cfg.BackgroundPi.Model)
	}
}

func TestLoadConfig_BackgroundFallback(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_BACKGROUND_PI_PROVIDER"] = testBackgroundProvider
	env["BARNABY_BACKGROUND_FALLBACK_PI_MODEL"] = testFallbackModel
	env["BARNABY_BACKGROUND_FALLBACK_COOLDOWN"] = "15m"

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatal(err)
	}

	bg := cfg.BackgroundPi
	if bg.FallbackProvider != testBackgroundProvider || bg.FallbackModel != testFallbackModel || bg.FallbackCooldown != 15*time.Minute {
		t.Errorf("fallback = %q/%q for %v", bg.FallbackProvider, bg.FallbackModel, bg.FallbackCooldown)
	}

	if cfg.Pi.FallbackModel != "" {
		t.Errorf("chat fallback model = %q, want none", cfg.Pi.FallbackModel)
	}

	env["BARNABY_BACKGROUND_FALLBACK_COOLDOWN"] = "soon"
	if _, err := loadConfig(testEnv(env)); err == nil {
		t.Error("invalid fallback cooldown accepted")
	}
}

func TestMatrixConfig_AllowedUsersParsing(t *testing.T) {
	t.Parallel()

	env := baseMatrixEnv()
	env["BARNABY_ALLOWED_USERS"] = " @alice:example.com, @bob:example.com "

	cfg, err := loadConfig(testEnv(env))
	if err != nil {
		t.Fatalf("loadConfig: %v", err)
	}

	for _, userID := range []string{"@alice:example.com", "@bob:example.com"} {
		if _, ok := cfg.Matrix.AllowedUsers[userID]; !ok {
			t.Errorf("allowed user %q missing from %v", userID, cfg.Matrix.AllowedUsers)
		}
	}
}

func TestDiscoverSkills_Symlinks(t *testing.T) {
	t.Parallel()

	// Create a target directory with SKILL.md
	target := t.TempDir()
	if err := os.WriteFile(filepath.Join(target, "SKILL.md"), []byte("test"), 0o600); err != nil {
		t.Fatal(err)
	}

	// Create a skills dir with a symlink to the target
	skillsDir := t.TempDir()
	if err := os.Symlink(target, filepath.Join(skillsDir, "my-skill")); err != nil {
		t.Fatal(err)
	}

	skills := discoverSkills(skillsDir)
	if len(skills) != 1 {
		t.Fatalf("got %d skills, want 1: %v", len(skills), skills)
	}

	want := filepath.Join(skillsDir, "my-skill")
	if skills[0] != want {
		t.Errorf("skill path = %q, want %q", skills[0], want)
	}
}

func TestLoadConfig_GroupTriggerScript(t *testing.T) {
	t.Parallel()

	dir := t.TempDir()
	script := filepath.Join(dir, "trigger")
	plain := filepath.Join(dir, "plain")

	if err := os.WriteFile(script, []byte("#!/bin/sh\n"), 0o755); err != nil { //nolint:gosec // must be executable
		t.Fatal(err)
	}

	if err := os.WriteFile(plain, nil, 0o600); err != nil {
		t.Fatal(err)
	}

	cfg, err := loadConfig(testEnv(baseMatrixEnv()))
	if err != nil || cfg.GroupTriggerScript != "" {
		t.Errorf("unset: GroupTriggerScript = %q, err = %v; want empty", cfg.GroupTriggerScript, err)
	}

	env := baseMatrixEnv()
	env["BARNABY_GROUP_TRIGGER_SCRIPT"] = script

	cfg, err = loadConfig(testEnv(env))
	if err != nil || cfg.GroupTriggerScript != script {
		t.Errorf("executable: GroupTriggerScript = %q, err = %v; want %q", cfg.GroupTriggerScript, err, script)
	}

	for _, bad := range []string{plain, filepath.Join(dir, "missing")} {
		env["BARNABY_GROUP_TRIGGER_SCRIPT"] = bad

		_, err := loadConfig(testEnv(env))
		if err == nil || !strings.Contains(err.Error(), "BARNABY_GROUP_TRIGGER_SCRIPT") {
			t.Errorf("%s: err = %v, want BARNABY_GROUP_TRIGGER_SCRIPT error", bad, err)
		}
	}
}
