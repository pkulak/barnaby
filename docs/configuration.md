# Configuration

## Bot commands

Send these as plain text messages in any conversation with the bot:

| Command | Description |
|---|---|
| `!help` | Show available commands |
| `!restart` | Start a fresh session (discards context). Unlike a service restart, which resumes the on-disk session. |
| `!stop` | Abort the currently running agent turn |
| `!compact` | Compact conversation context to reduce token usage |
| `!skills` | List the skills loaded for this bot instance |
| `!verify` | Set up cross-signing so the bot's device shows as verified |
| `!background-stop` | Abort the active background task |
| `!background-restart` | Kill the background Pi process (each task already starts fresh) |
| `!voice-stop` | Abort the active HTTP voice turn |
| `!voice-restart` | Clear pending voice turns and start a fresh voice session on the next request |
| `!voice-compact` | Compact the active voice session |
| `!mcp-stop` | Abort the active MCP request |

## General configuration

| Variable | Default | Description |
|---|---|---|
| `BARNABY_PI_BINARY` | `pi` | Path to the pi binary |
| `BARNABY_PI_SESSION_DIR` | `/var/lib/barnaby/sessions` | Session data directory |
| `BARNABY_PI_PROVIDER` | `anthropic` | LLM provider |
| `BARNABY_PI_MODEL` | `claude-opus-4-6` | Model name |
| `BARNABY_PI_WORKING_DIR` | `/var/lib/barnaby` | Working directory for pi |
| `BARNABY_PI_IDLE_TIMEOUT` | `30m` | Kill pi after this duration of inactivity |
| `BARNABY_PI_COMPACT_ON_IDLE` | `false` | Compact an idle session before the reaper kills its pi process when it has at least 32k context tokens or an unknown token count. The reap that would make a session's 7th compaction starts a fresh session instead. Background work ignores this. |
| `BARNABY_SOUL_FILE` | built-in `SOUL.md` | Path to a file containing the system prompt |
| `BARNABY_PI_SKILLS_DIR` | _(empty)_ | Directory containing skill subdirectories |

## HTTP voice configuration

The HTTP text-turn API is disabled unless `BARNABY_HTTP_LISTEN` is set. Enabling
it creates a dedicated voice worker and Pi session.

| Variable | Default | Description |
|---|---|---|
| `BARNABY_HTTP_LISTEN` | _(empty)_ | TCP listen address, such as `0.0.0.0:8787`. An empty value disables the HTTP server. |
| `BARNABY_HTTP_BEARER_TOKEN` | _(empty)_ | Static bearer token required by `/v1/status` and `/v1/turn`. Required when `BARNABY_HTTP_LISTEN` is set. |

The endpoint has the same tools and skills as the chat worker. Keep it on a
trusted network and treat the bearer token as full access to the Barnaby
instance. See [Home Assistant voice assistant](voice-assistant.md) for the API,
queue behavior, Home Assistant component, and security details.

## MCP configuration

The MCP endpoint is disabled unless `BARNABY_MCP_BEARER_TOKEN` is set. It is
served at `/mcp` on the HTTP listener, so `BARNABY_HTTP_LISTEN` is required
too. Enabling it creates a dedicated MCP worker and Pi process.

| Variable | Default | Description |
|---|---|---|
| `BARNABY_MCP_BEARER_TOKEN` | _(empty)_ | Static bearer token required by `/mcp`. Setting it enables the endpoint. |
| `BARNABY_MCP_SESSION_DIR` | `<tmpdir>/barnaby-mcp` | Directory for MCP Pi session files. Barnaby never deletes them. |

See [MCP endpoint](mcp.md) for the `ask` tool, sessions, queue behavior, and a
LibreChat example.

## File handling

**Receiving files** — Users can send images, audio, video, and documents to the
bot. Attachments are downloaded to the session directory under `attachments/`
and the file path is passed to pi so it can read or process the file with its
tools.

**Sending files back** — Pi can send files to the user by including
`<sendfile>/absolute/path</sendfile>` tags in its response. The bot strips the
tags, uploads each referenced file to Matrix, and delivers it as an attachment.
Multiple `<sendfile>` tags can appear in a single response.

## Matrix configuration

| Variable | Required | Description |
|---|---|---|
| `BARNABY_MATRIX_HOMESERVER` | Yes | Matrix homeserver URL |
| `BARNABY_MATRIX_USER_ID` | Yes | Bot's Matrix user ID |
| `BARNABY_MATRIX_ACCESS_TOKEN` | Yes | Access token (via environment file) |
| `BARNABY_MATRIX_DEVICE_ID` | No | Device ID (auto-resolved if omitted) |
| `BARNABY_MATRIX_PICKLE_KEY` | No | Pickle key for crypto DB |
| `BARNABY_MATRIX_CRYPTO_DB` | No | Path to crypto SQLite DB |
| `BARNABY_MATRIX_ROOM_ID` | No | Default Matrix room ID for triggers and reminders. When set, Matrix invite handling also switches to multi-room mode. |
| `BARNABY_ALLOWED_USERS` | No | Comma-separated Matrix user IDs allowed to interact |

### Matrix room behavior

By default, Matrix runs in **single-room mode**: the bot joins the first
allowed room it is invited to and ignores later invites.

If `BARNABY_MATRIX_ROOM_ID` is set, Matrix switches to **multi-room mode**.
Two things happen:

1. triggers and reminders are routed to that room by default
2. the bot accepts all allowed Matrix invites instead of claiming just one room

This does **not** create separate sessions per room. Chat messages share one
chat session across rooms and DMs, while triggers and reminders use a separate
background session. `!restart`, `!stop`, and `!compact` affect chat; use
`!background-restart` and `!background-stop` for the background session.

The sessions are separate, but the chat worker still sees relevant room activity.
Barnaby saves unaddressed group messages and background-worker replies per room,
then prepends them when chat is next activated. Downloaded attachments include
their local path, so you can post a photo and ask about it in a later mention.
Direct replies are quoted once rather than duplicated in the recent-room block.

Pending room context survives restarts and is bounded to the newest 64 messages
and 64 KiB. The prompt says when older messages were omitted. This is a hard
cutoff, not an AI-generated summary.

Only allowed senders contribute room context. If `BARNABY_ALLOWED_USERS` is
unset, everyone is allowed; set it when other room members should not be able to
influence the agent's context or send it attachments.

### Group message routing

By default, every group message goes to the agent. Set
`BARNABY_GROUP_TRIGGER_SCRIPT` to an executable that decides which ones should.
DMs and `!commands` never go through it.

The script gets the room's recent messages and the current one as JSON on stdin:

```json
{
  "history": [
    {"from": "Gwen", "is_bot": false, "ago": "3m", "text": "crow, when do the bins go out?"},
    {"from": "Crow", "is_bot": true, "ago": "2m", "text": "Tuesday morning, before 7am."}
  ],
  "message": {"from": "Gwen", "text": "and recycling?"}
}
```

History is oldest first and holds the last 20 messages in the room, including
the bot's own replies, files (`[sent a file: plan.pdf]`), and reactions
(`[reacted 👍 to: thanks!]`). Each entry is cut to 500 characters. `ago` is
measured from the current message, in whole units (`45s`, `2m`, `3h`, `2d`).
The history lives in memory, so it starts empty after a restart.

Exit `0` to send the message to the agent and `1` to skip it. Skipped messages
still end up in the room context above. Anything the script prints is logged
with the decision, which makes a probability or reason handy to print.
See [`examples/group_trigger.py`](../examples/group_trigger.py) for a script
that asks Jev.

Any other exit code, or running longer than 10 seconds, is logged as a warning
and the message goes to the agent anyway. A broken script makes the bot chatty,
not deaf. The script runs inline, so a slow one delays every room. Barnaby
refuses to start if the path isn't executable.

## Pi configuration

`BARNABY_BACKGROUND_PI_PROVIDER` and `BARNABY_BACKGROUND_PI_MODEL` optionally
override the provider and model used for reminders and external triggers. Each
falls back to the corresponding `BARNABY_PI_*` setting. Background work keeps
its own Pi process but shares the normal working directory, tools, and skills,
and resets the session before every run so each trigger starts with empty
context.

Background session files live in `BARNABY_BACKGROUND_PI_SESSION_DIR`, which
defaults to `<tmpdir>/barnaby-background`. They are disposable and age out with
normal `/tmp` cleanup.

When the HTTP listener is enabled, voice turns use another Pi session under
`<BARNABY_PI_SESSION_DIR>/voice`. The voice worker inherits the chat provider,
model, soul, working directory, tools, and skills. Its context remains separate
from both chat and background work.

## Secrets and authentication

### LLM provider credentials

Pi needs credentials for your LLM provider. There are two ways to set this up:

**Option A: API key** — set `ANTHROPIC_API_KEY` (or the equivalent for your
provider) in an environment file and pass it via the `environmentFiles` option.
API keys don't expire and are the simplest approach.

**Option B: OAuth (Claude Pro/Max)** — pi supports OAuth against your Anthropic
account, so you can use your subscription instead of API credits. The NixOS
module installs a `barnaby-pi` wrapper on the host that runs pi inside the
container with the correct environment. To authenticate:

```bash
sudo barnaby-pi auth login
```

Pi will print a URL — open it in any browser, complete the Anthropic login, and
paste the redirect URL back into the terminal. No local browser is required on
the server itself. The refresh token persists across restarts — you only need to
do this once (unless the token gets revoked).

### Environment files

For secrets that are plain key=value pairs (e.g. API keys, access tokens), use
`environmentFiles`. These are bind-mounted read-only into the container and
loaded by systemd's `EnvironmentFile=` directive before the service starts:

```nix
services.barnaby.instances.barnaby.environmentFiles = [
  /run/secrets/barnaby-env  # contains ANTHROPIC_API_KEY=sk-...
];
```
