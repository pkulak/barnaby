# Memory

Barnaby can remember past conversations. Every night, it turns sessions that have gone quiet into short markdown notes, and it gives the agent an index of those notes and instructions for searching them.

Memory is off by default. Turn it on for an instance:

```nix
services.barnaby.instances.barnaby.memory.enable = true;
```

## How it works

A `barnaby-memory` timer runs inside the instance's container at 03:00. It looks for sessions in `BARNABY_PI_SESSION_DIR` that haven't changed in 3 days, takes up to 10 of them, and runs a bare `pi -p` for each one to write a note. Trivial sessions are archived without a note.

- Notes go in `~/memory/YYYY/MM/<date>-<title>.md`, one for each session. Each note's frontmatter records when the conversation started and ended, plus its tags.
- `~/memory/INDEX.md` lists every note, newest first, on one line each.
- Raw transcripts are compressed into `~/session-archive/<instance>/`, and each note's `archive:` line points to its transcript. The agent can search it with `zstdcat` when someone wants an exact quote.
- The newest session is never touched, so the current conversation keeps going.

A note is a diary of the people, not of the agent: events, requests and their results, preferences, and follow-ups. The agent's own tooling trouble, URLs, and IDs are left out. The prompt is in [`memory/chat.md`](../memory/chat.md), after the shared [`memory/header.md`](../memory/header.md).

Memory also adds an extension that appends a Memory section to the system prompt. It tells the agent when to check its notes, how to search them and its raw sessions, and not to repeat private details from direct messages in a group room. Chat runs also get `INDEX.md`. Background runs (reminders and triggers) skip the index to stay lean.

## Options

| Option | Default | What it does |
|---|---|---|
| `memory.enable` | `false` | Adds the nightly timer and the Memory section |
| `memory.directory` | `/var/lib/<instance>/memory` | The host directory for notes. The agent always sees it at `~/memory`. |
| `memory.model` | The chat model | The model that writes notes, as `provider/id` with an optional `:thinking` suffix |

Each note costs one run of `memory.model` over the whole session, so a cheaper model saves money, but notes are what the agent remembers for good.

A `memory.directory` outside the state directory is bind-mounted read-write. It must already exist and be writable by the container's `barnaby` user. Use this to keep notes in a synced folder or a git repo.

## Running it by hand

From the host:

```bash
sudo systemctl -M barnaby start barnaby-memory
sudo journalctl -M barnaby -u barnaby-memory
```

The service prints a JSON report with the notes written, failures, sessions archived without a note, and how many idle sessions are left. A session that fails twice is skipped until its entry is removed from `~/.local/state/session-compact/failures.json`.

## Using the script elsewhere

[`memory/session-compact.py`](../memory/session-compact.py) also works outside Barnaby, such as for your own pi sessions. Pass each session store as `--source NAME:KIND:SESSIONS:NOTES`. `chat` sources hold one agent's sessions, and `coding` sources hold a directory per project, like `~/.pi/agent/sessions`. Only a chat prompt ships with Barnaby, so give coding sources one with `--prompt coding=FILE`:

```bash
memory/session-compact.py \
  --source laptop:coding:~/.pi/agent/sessions:~/notes/sessions/laptop \
  --prompt coding=~/notes/coding-prompt.md \
  --model anthropic/claude-sonnet-4-5:medium \
  run --limit 5
```

Run `plan --dry-run --limit 100` instead of `run` to see what's waiting without changing anything.
