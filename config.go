package main

import (
	_ "embed"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Config struct {
	Matrix       MatrixConfig
	Pi           PiConfig
	BackgroundPi PiConfig
	VoicePi      PiConfig
	MCPPi        PiConfig
	HTTP         HTTPConfig
	// GroupTriggerScript decides which group messages reach the agent.
	GroupTriggerScript string
}

type HTTPConfig struct {
	Listen      string
	BearerToken string
	// MCPBearerToken enables the /mcp endpoint on the same listener.
	MCPBearerToken string
	// VoicePrompt is appended to the voice worker's system prompt, after the
	// built-in voice instructions.
	VoicePrompt string
}

type MatrixConfig struct {
	Homeserver   string
	UserID       string
	AccessToken  string
	DeviceID     string
	AllowedUsers map[string]struct{}
	PickleKey    string
	CryptoDBPath string
}

type PiConfig struct {
	BinaryPath string
	// BinaryArgs are inserted before pi's own flags. Unused in production;
	// tests use it to run the fake-pi stub via `bash <script>` so the
	// testdata file needs no exec bit and no shebang lookup.
	BinaryArgs []string
	// SessionDir holds Pi session JSONL files. StateDir holds shared Barnaby
	// state such as barnaby.db, .room_id, and trigger.pipe.
	SessionDir string
	StateDir   string
	Provider   string
	Model      string
	// WorkingDir is the agent's cwd. In system prompts, refer to this as the
	// "working directory".
	WorkingDir   string
	IdleTimeout  time.Duration
	SystemPrompt string
	Skills       []string
	// CompactOnIdle compacts a session before the idle reaper kills its pi
	// process, so the on-disk session is smaller the next time it resumes.
	CompactOnIdle bool // BARNABY_PI_COMPACT_ON_IDLE
	// NoContinue suppresses --continue when spawning pi. Background work sets
	// this: each trigger gets a fresh session via new_session, so resuming the
	// previous run's file would only load context that is about to be discarded.
	NoContinue bool
	// DefaultRoomID is the fallback conversation ID for inbox items that
	// have no ConversationID of their own (triggers). Takes
	// precedence over the per-conversation SetRoomID mechanism.
	DefaultRoomID string
	// FallbackProvider and FallbackModel finish a background turn that
	// failed with a provider error. The fallback stays selected for
	// FallbackCooldown. An empty FallbackModel disables the fallback.
	FallbackProvider string
	FallbackModel    string
	FallbackCooldown time.Duration
}

// LoadConfig reads configuration from os.Getenv.
func LoadConfig() (*Config, error) {
	return loadConfig(os.Getenv)
}

// loadConfig reads configuration using the provided env-lookup function,
// allowing tests to supply isolated environments without mutating os state.
func loadConfig(getenv func(string) string) (*Config, error) {
	env := envReader{getenv: getenv}

	idleTimeout, err := env.duration("BARNABY_PI_IDLE_TIMEOUT", 30*time.Minute)
	if err != nil {
		return nil, err
	}

	skills := discoverSkills(env.str("BARNABY_PI_SKILLS_DIR"))
	allowedUsers := parseAllowedUsers(env.list("BARNABY_ALLOWED_USERS"))
	workingDir := env.or("BARNABY_PI_WORKING_DIR", "/var/lib/barnaby")

	groupTriggerScript := env.str("BARNABY_GROUP_TRIGGER_SCRIPT")
	if groupTriggerScript != "" {
		if _, err := exec.LookPath(groupTriggerScript); err != nil {
			return nil, fmt.Errorf("BARNABY_GROUP_TRIGGER_SCRIPT: %w", err)
		}
	}

	httpCfg, err := loadHTTPConfig(env)
	if err != nil {
		return nil, err
	}

	backgroundPi, err := loadBackgroundPiConfig(env, workingDir, idleTimeout, skills)
	if err != nil {
		return nil, err
	}

	cfg := &Config{
		Matrix: MatrixConfig{
			Homeserver:   env.str("BARNABY_MATRIX_HOMESERVER"),
			UserID:       env.str("BARNABY_MATRIX_USER_ID"),
			AccessToken:  env.str("BARNABY_MATRIX_ACCESS_TOKEN"),
			DeviceID:     env.str("BARNABY_MATRIX_DEVICE_ID"),
			AllowedUsers: allowedUsers,
			PickleKey:    env.or("BARNABY_MATRIX_PICKLE_KEY", "barnaby-default-pickle-key"),
			CryptoDBPath: env.or("BARNABY_MATRIX_CRYPTO_DB", filepath.Join(workingDir, "crypto.db")),
		},
		Pi:                 loadPiConfig(env, workingDir, idleTimeout, skills),
		BackgroundPi:       backgroundPi,
		VoicePi:            loadVoicePiConfig(env, workingDir, idleTimeout, skills),
		MCPPi:              loadMCPPiConfig(env, workingDir, idleTimeout, skills),
		HTTP:               httpCfg,
		GroupTriggerScript: groupTriggerScript,
	}

	if err := cfg.Matrix.validate(); err != nil {
		return nil, err
	}

	return cfg, nil
}

func loadHTTPConfig(env envReader) (HTTPConfig, error) {
	httpCfg := HTTPConfig{
		Listen:         env.str("BARNABY_HTTP_LISTEN"),
		BearerToken:    env.str("BARNABY_HTTP_BEARER_TOKEN"),
		MCPBearerToken: env.str("BARNABY_MCP_BEARER_TOKEN"),
	}
	if httpCfg.Listen != "" && httpCfg.BearerToken == "" {
		return HTTPConfig{}, errors.New("BARNABY_HTTP_BEARER_TOKEN is required when BARNABY_HTTP_LISTEN is set")
	}

	if httpCfg.MCPBearerToken != "" && httpCfg.Listen == "" {
		return HTTPConfig{}, errors.New("BARNABY_HTTP_LISTEN is required when BARNABY_MCP_BEARER_TOKEN is set")
	}

	if path := env.str("BARNABY_VOICE_PROMPT_FILE"); path != "" {
		data, err := os.ReadFile(path)
		if err != nil {
			return HTTPConfig{}, fmt.Errorf("BARNABY_VOICE_PROMPT_FILE: %w", err)
		}

		httpCfg.VoicePrompt = strings.TrimSpace(string(data))
	}

	return httpCfg, nil
}

func (m MatrixConfig) validate() error {
	return errors.Join(
		requireField(m.Homeserver, "BARNABY_MATRIX_HOMESERVER"),
		requireField(m.UserID, "BARNABY_MATRIX_USER_ID"),
		requireField(m.AccessToken, "BARNABY_MATRIX_ACCESS_TOKEN"),
	)
}

// requireField returns an "is required" error if v is empty. Intended for
// use with errors.Join so that validate() reports all missing fields at once.
func loadPiConfig(env envReader, workingDir string, idleTimeout time.Duration, skills []string) PiConfig {
	sessionDir := env.or("BARNABY_PI_SESSION_DIR", "/var/lib/barnaby/sessions")

	return PiConfig{
		BinaryPath:    env.or("BARNABY_PI_BINARY", "pi"),
		SessionDir:    sessionDir,
		StateDir:      sessionDir,
		Provider:      env.or("BARNABY_PI_PROVIDER", "anthropic"),
		Model:         env.or("BARNABY_PI_MODEL", "claude-opus-4-6"),
		WorkingDir:    workingDir,
		IdleTimeout:   idleTimeout,
		SystemPrompt:  loadSoul(env),
		Skills:        skills,
		CompactOnIdle: env.bool("BARNABY_PI_COMPACT_ON_IDLE"),
		DefaultRoomID: env.str("BARNABY_MATRIX_ROOM_ID"),
	}
}

func loadBackgroundPiConfig(env envReader, workingDir string, idleTimeout time.Duration, skills []string) (PiConfig, error) {
	cfg := loadPiConfig(env, workingDir, idleTimeout, skills)
	// Each background turn runs in its own fresh session. Keep the transcripts
	// in a temp dir so recent runs stay debuggable but age out on their own;
	// in the NixOS containers /tmp is a bind mount of the state dir's tmp/.
	cfg.SessionDir = env.or("BARNABY_BACKGROUND_PI_SESSION_DIR", filepath.Join(os.TempDir(), "barnaby-background"))
	cfg.NoContinue = true
	// Background context is one turn; the idle reaper would only waste a
	// summarization call compacting it.
	cfg.CompactOnIdle = false
	cfg.Provider = env.or("BARNABY_BACKGROUND_PI_PROVIDER", cfg.Provider)
	cfg.Model = env.or("BARNABY_BACKGROUND_PI_MODEL", cfg.Model)
	cfg.FallbackProvider = env.or("BARNABY_BACKGROUND_FALLBACK_PI_PROVIDER", cfg.Provider)
	cfg.FallbackModel = env.str("BARNABY_BACKGROUND_FALLBACK_PI_MODEL")

	cooldown, err := env.duration("BARNABY_BACKGROUND_FALLBACK_COOLDOWN", time.Hour)
	if err != nil {
		return PiConfig{}, err
	}

	cfg.FallbackCooldown = cooldown

	return cfg, nil
}

func loadVoicePiConfig(env envReader, workingDir string, idleTimeout time.Duration, skills []string) PiConfig {
	cfg := loadPiConfig(env, workingDir, idleTimeout, skills)
	cfg.SessionDir = filepath.Join(cfg.StateDir, "voice")

	return cfg
}

func loadMCPPiConfig(env envReader, workingDir string, idleTimeout time.Duration, skills []string) PiConfig {
	cfg := loadPiConfig(env, workingDir, idleTimeout, skills)
	cfg.SessionDir = env.or("BARNABY_MCP_SESSION_DIR", filepath.Join(os.TempDir(), "barnaby-mcp"))
	// The MCP worker selects each request's session explicitly with
	// new_session or switch_session, so resuming the latest file is useless.
	cfg.NoContinue = true
	cfg.CompactOnIdle = false

	return cfg
}

func requireField(v, name string) error {
	if v == "" {
		return fmt.Errorf("%s is required", name)
	}

	return nil
}

// envReader wraps a getenv function with typed accessors so callers do not
// mix raw string lookups with ad-hoc parsing at every call site.
type envReader struct {
	getenv func(string) string
}

// str returns the raw value of key.
func (e envReader) str(key string) string {
	return e.getenv(key)
}

// or returns the value of key, or fallback if empty.
func (e envReader) or(key, fallback string) string {
	if v := e.getenv(key); v != "" {
		return v
	}

	return fallback
}

// list parses a comma-separated value, trimming whitespace and dropping empties.
func (e envReader) list(key string) []string {
	return parseCommaSeparated(e.getenv(key))
}

// bool interprets "1", "true", "yes" (case-insensitive) as true.
func (e envReader) bool(key string) bool {
	switch strings.ToLower(e.getenv(key)) {
	case "1", "true", "yes":
		return true
	default:
		return false
	}
}

// duration parses a time.Duration, returning def if unset. The error message
// includes the key name so callers do not need to repeat it.
func (e envReader) duration(key string, def time.Duration) (time.Duration, error) {
	v := e.getenv(key)
	if v == "" {
		return def, nil
	}

	d, err := time.ParseDuration(v)
	if err != nil {
		return 0, fmt.Errorf("parsing %s: %w", key, err)
	}

	return d, nil
}

// discoverSkills scans a directory for subdirectories containing SKILL.md.
func discoverSkills(dir string) []string {
	if dir == "" {
		return nil
	}

	entries, err := os.ReadDir(dir)
	if err != nil {
		if !errors.Is(err, os.ErrNotExist) {
			fmt.Fprintf(os.Stderr, "warning: failed to read skills dir %s: %v\n", dir, err)
		}

		return nil
	}

	var skills []string

	for _, entry := range entries {
		skillPath := filepath.Join(dir, entry.Name())
		skillFile := filepath.Join(skillPath, "SKILL.md")

		if _, err := os.Stat(skillFile); err == nil {
			skills = append(skills, skillPath)
		}
	}

	return skills
}

func parseAllowedUsers(users []string) map[string]struct{} {
	allowedUsers := make(map[string]struct{})
	for _, u := range users {
		allowedUsers[u] = struct{}{}
	}

	return allowedUsers
}

//go:embed SOUL.md
var defaultSoul string

// loadSoul reads the system prompt from BARNABY_SOUL_FILE, falling back to
// the built-in SOUL.md.
func loadSoul(env envReader) string {
	path := env.str("BARNABY_SOUL_FILE")
	if path == "" {
		return defaultSoul
	}

	data, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintf(os.Stderr, "warning: failed to read soul file %s: %v\n", path, err)

		return defaultSoul
	}

	return string(data)
}

const defaultTriggerPrompt = `External trigger received.`

func parseCommaSeparated(s string) []string {
	if s == "" {
		return nil
	}

	var result []string

	for part := range strings.SplitSeq(s, ",") {
		part = strings.TrimSpace(part)
		if part != "" {
			result = append(result, part)
		}
	}

	return result
}
