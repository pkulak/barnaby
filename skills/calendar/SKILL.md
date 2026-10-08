---
name: calendar
description: Read, add, change, or cancel calendar events, including one occurrence of a recurring event.
---

# Calendar

Use the helper in this skill for every calendar request:

```bash
cd "$BARNABY_PI_SKILLS_DIR/calendar"
python3 scripts/calendar_cli.py <command>
```

It talks to the calendar server live and returns JSON with times in local time. A nonzero exit means the request failed. Report the useful error, and never claim an event was added, changed, or deleted unless the helper said so.

## Calendars

```bash
python3 scripts/calendar_cli.py calendars
```

Each calendar has a `name` and `writable`. The server decides what's writable, so a read-only calendar can't be changed, no matter who asks. Other commands use a list cached for a week; run `calendars` to refresh it when a calendar seems to be missing.

## Reading events

```bash
python3 scripts/calendar_cli.py events                                  # today
python3 scripts/calendar_cli.py events --from 2026-10-10 --days 2       # a weekend
python3 scripts/calendar_cli.py events --from 2026-10-12 --to 2026-10-18 --calendar Household
python3 scripts/calendar_cli.py events --query dentist                  # 30 days back to a year ahead
```

Run `date` first when the request says "tomorrow", "Saturday", or "next week", so you pick the right dates.

Recurring events come back as separate occurrences. An event on several calendars comes back once, with every calendar in `calendars`. An all-day event's `end` is its last day. `--query` matches the title, location, and description; try a shorter word if nothing turns up.

## Adding events

```bash
python3 scripts/calendar_cli.py add --title "Dentist" --start "2026-10-14 15:30" --duration 45m --location "Smile Dental"
python3 scripts/calendar_cli.py add --title "Camping" --start 2026-10-16 --end 2026-10-18 --all-day
python3 scripts/calendar_cli.py add --title "Swim" --start "2026-10-13 16:00" --end "2026-10-13 17:00" --rrule "FREQ=WEEKLY;BYDAY=TU;UNTIL=20261215"
```

Times are local, as `YYYY-MM-DD HH:MM`. Without `--end` or `--duration`, a timed event lasts an hour and an all-day event lasts one day.

When only one calendar is writable, it's used. Otherwise pass `--calendar`; pick it from who the event is for, and ask if that's unclear. Events have no invitations or alerts.

## Changing and deleting events

Use the `id` from `events`. Without `--occurrence`, a change applies to the whole event, and to every occurrence of a recurring one:

```bash
python3 scripts/calendar_cli.py update <id> --title "Swim team"
python3 scripts/calendar_cli.py update <id> --start "2026-10-13 17:00"     # keeps the length
python3 scripts/calendar_cli.py delete <id>
```

To change or cancel one occurrence of a recurring event, pass its `occurrence` from `events` (a date works when it happens once that day):

```bash
python3 scripts/calendar_cli.py update <id> --occurrence 2026-10-20 --start "2026-10-21 16:00"
python3 scripts/calendar_cli.py delete <id> --occurrence 2026-10-27
```

"Cancel Tuesday's swim" means one occurrence. "No more swim" or "cancel swim" with no date is ambiguous; ask whether they mean one occurrence or the whole series.

Add or change an event directly when the request is clear. Confirm first before deleting anything, or before changing an event on a calendar that isn't yours or the asker's.
