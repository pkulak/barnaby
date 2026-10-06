# Reminders

Barnaby has one-shot and recurring reminders. Both are delivered through the background agent session, separately from chat, and use a one-minute scheduler.

Enable the bundled `reminders` Pi extension to give the agent structured tools:

- `remind_at(when, prompt)` — schedule a one-shot reminder
- `remind_cron(cron, timezone, prompt, end_at?)` — schedule a recurring reminder
- `remind_list()` — list pending one-shot and recurring reminders
- `remind_cancel(id)` — cancel a one-shot reminder
- `remind_cron_cancel(id)` — cancel a recurring reminder

Reminder prompts must be self-contained. The background session does not receive chat history.

A reminder replies in the room it was set in. When the scheduling message has a `<room-id>`, the tools prepend a routing line telling the background agent to start its response with `<send-to>` for that room. Prompts that already contain `<send-to>` are left alone, and reminders set from voice or background runs go to the default room.

One-shot timestamps and optional recurring end times use ISO 8601 with an explicit timezone. Recurring reminders use a five-field cron expression and an explicit IANA timezone:

```text
cron:     0 12 * * 1
timezone: America/Los_Angeles
```

This fires every Monday at noon Pacific time. Cron fields are `minute hour day-of-month month day-of-week`. When both day fields are restricted, either one can match.

Recurring reminders are deliberately loose scheduling. Missed or failed occurrences are not retried, and downtime does not produce catch-up reminders. Before a recurring occurrence is queued, Barnaby counts queued background triggers; it skips the occurrence when there are already five. One-shot reminders and trigger-pipe events are not capped.

Canceling or reaching the optional inclusive end time deletes a recurring series. An occurrence already queued when the series is canceled may still run.

## Background session

Reminders and trigger-pipe events share one background Pi process, but every run starts with empty context: Barnaby resets the session before each trigger. The background session is independent from chat and has the same working directory, tools, skills, and system prompt. Set these optional overrides to use a cheaper model for background work:

- `BARNABY_BACKGROUND_PI_PROVIDER`
- `BARNABY_BACKGROUND_PI_MODEL`

Each falls back to its `BARNABY_PI_*` equivalent. Background tool calls and infrastructure errors are logged rather than sent to Matrix. Normal replies still go to Matrix; `NO_REPLY` remains silent.

A cheap model tier can fail outright for a while. Set `BARNABY_BACKGROUND_FALLBACK_PI_MODEL` to finish those runs on another model. When a run fails with a provider error after Pi's own retries, Barnaby switches the same session to the fallback and sends "Continue." The fallback sees the whole transcript, including tool calls that already succeeded, so it picks up where the run stopped. Later runs stay on the fallback for `BARNABY_BACKGROUND_FALLBACK_COOLDOWN` (default `1h`), then return to the primary model. `BARNABY_BACKGROUND_FALLBACK_PI_PROVIDER` defaults to the background provider. The fallback model must be in Pi's model registry.

Each run is written to its own session file under a temp directory (`BARNABY_BACKGROUND_PI_SESSION_DIR`, default `<tmpdir>/barnaby-background`). Because the context resets per run, a file contains just that run, which makes recent runs easy to inspect. The files are disposable and age out with normal `/tmp` cleanup (about ten days). In the NixOS container `/tmp` is a bind mount of the state directory's `tmp/`, so the files are visible on the host and the `session -b` helper opens the most recent run.

Use `!background-stop` to abort the active background task. `!background-restart` kills the background process; the next task would start fresh anyway. Both leave chat and queued reminders alone.

## Trigger pipe

External processes can wake the background agent by writing to the session directory's named pipe:

```text
<session-dir>/trigger.pipe
```

Each line is a separate trigger. The pipe is unauthenticated: any process that can write to it can inject prompts into Pi, which has full tool access. The FIFO is mode `0664`, so make sure only trusted processes are in the `barnaby` group.

### Enabling on NixOS

```nix
services.barnaby.instances.barnaby.extensions.reminders = true;
```

This pulls the flake's `extension-reminders` package, which bakes the `sqlite3` store path into the extension. For non-Nix installs the extension falls back to PATH lookup, so make sure `sqlite3` is available there.
