---
name: sports-monitor
description: Watch a live game and send one alert on a score threshold or when it ends.
---

# Sports Monitor

Set up and run a self-cancelling live-game watcher. A monitor is a recurring reminder that polls the ESPN scoreboard through the `sports-scores` skill and stays quiet until a stop condition, then sends exactly one notification and cancels itself.

Keep the cadence fixed: poll every **3 minutes**, send a **single terminal notification**, then stop. Do not add repeat pings or configurable intervals; that can come later.

## Creating a monitor

When someone asks to monitor/watch a game:

1. Resolve the team using the `sports-scores` skill. You need `sport`, `league`, and `team-id`.
2. Do one immediate scoreboard read (see **Reading the scoreboard**). If the stop condition is already true, send the notification now and do **not** create a monitor.
3. Otherwise capture the current message's room ID exactly and call `remind_cron` once:
   - `cron`: `*/3 * * * *`
   - `timezone`: `UTC` (an every-3-minutes schedule doesn't depend on it)
   - `end_at`: now + 6 hours, as ISO 8601 with an explicit offset (compute it, e.g. `date -d '+6 hours' --iso-8601=seconds`). This is only a backstop for an orphaned monitor.
   - `prompt`: the thin template below, with every placeholder replaced.

```text
Sports monitor. Follow the sports-monitor skill. This background session has no chat history; everything you need is below.

Team: TEAM_NAME (SPORT/LEAGUE/TEAM_ID)
Notify when: CONDITION
Terminal notification must begin with <send-to>ROOM_ID</send-to>. Output exactly NO_REPLY on every other poll.
```

`CONDITION` is the user's request in their own words (for example `down by less than 8`, or `any final result`). Only the team and the condition vary between monitors; everything else is fixed by this skill.

After `remind_cron` returns an id, tell the user the monitor is set and state the condition in one sentence.

## Running a poll

A recurring trigger arrives in the background session. It contains `Series ID: N` and the reminder prompt with the team and condition.

1. Read the scoreboard for the trigger's `SPORT/LEAGUE/TEAM_ID`.
2. Find the event for today. Identify your team by `team-id`; the other competitor is the opponent.
3. Decide:
   - **No event today, or `status.state == "pre"`**: reply exactly `NO_REPLY`.
   - **`status.state == "in"`**: compute `margin = our_score - opponent_score`.
     - If the notify condition is met, send the update and stop (see **Stopping**).
     - Otherwise reply exactly `NO_REPLY`.
   - **`status.completed == true`** (or `status.state == "post"`): send the final result and stop.
4. If the terminal notification for this game is already in this background session's history, reply exactly `NO_REPLY` — a queued occurrence may fire just after a cancel.

Use the trigger's `detail`/`shortDetail` for the clock (for example `2:34 - 4th Quarter`).

## Stopping

A terminal notification is the only real output. It must begin with the routing tag and then a one-line summary:

```text
<send-to>ROOM_ID</send-to>
Final: Fire 66–82 Golden State Valkyries — Fire loss by 16.
```

or, for a threshold hit:

```text
<send-to>ROOM_ID</send-to>
Fire trail Valkyries 61–55 (down 6) — 9.4 - 3rd Quarter.
```

Immediately after sending it, cancel the series using the `Series ID` from the trigger — no `remind_list` scan is needed:

```text
remind_cancel id=SERIES_ID recurring=true
```

Then stop. Never send a second notification for the same game.

## Reading the scoreboard

Use the unmodified helper in the `sports-scores` skill:

```bash
cd "$BARNABY_PI_SKILLS_DIR/sports-scores" && python3 scripts/sports_scores.py scoreboard \
  --sport SPORT --league LEAGUE --team-id TEAM_ID
```

The JSON has `events[]`; each event has `status.state` (`pre`/`in`/`post`), `status.completed`, `status.detail`, and `competitors[]` with `id`, `displayName`, `score`, and `homeAway`. Pick today's event (`relativeDateLocal == "today"`); ignore anything else.

Interpret a threshold like "down by less than N" as a deficit strictly below N: it holds whenever `margin >= -(N-1)` — trailing by up to N-1, tied, or leading. So "down by less than 8" means `margin >= -7`, and it fires even when the team is tied or ahead. Report only when the condition is unambiguously met (for example, exactly trailing by 8 does not fire).
