## Format

This is a long-running chat assistant answering people and scheduled triggers over days or weeks. The reader is that same agent or the people it serves, so they already know who the agent is, its rooms, its skills, its schedule, and how its tools work. Never explain any of that.

The note is a diary of the people, not the agent: things that happened in their lives, what they asked for and got, what they said about plans, preferences, and each other, and problems they had and how they were solved. Ask of each item: "Would one of these people want to recall this in a year?"

The agent's own operational trouble is not durable: it was most likely fixed soon after, and keeping it wastes tokens forever. Never include, in any section:

- The agent's tooling, skills, scripts, logins, APIs, or prompts: what broke, what was missing, workarounds, and fixes someone should make to them. This holds even when the transcript leaves it unresolved, and even when a person pointed it out or asked for the fix.
- URLs, file links, request IDs, room IDs, store paths, and job numbers. This overrides the rule about keeping identifiers.

The one exception is a failure that cost a person something real, such as a missed signup or a car that didn't charge. Give it a few words inside that person's event, with no technical detail.

Leave out routine work that went as expected. Scheduled checks that found nothing, normal mail sweeps, heartbeats, and standard reminder runs get no mention, or at most one line in Summary. Repeated jobs get no bullet of their own, however often they ran, unless they let someone down as above. Don't describe how a request was carried out (commands, scripts, API calls, upload steps) unless that detail is itself worth remembering. Report the result instead: "Sent Sam the pancake recipe as a printable page."

```markdown
<frontmatter from job.md, verbatim, with tags filled in>
# <The period's notable events, such as "Lake trip, tennis signups, new dishwasher". No dates or the group's name; the filename and frontmatter carry those.>

## Summary
2–4 sentences on what stood out in this period.

## Events
- **Aug 22** — Sam asked about the weather at the lake for a 9am start (about 55°F).
- One bullet per notable event, dated, naming the people involved and the outcome.

## Preferences & corrections
- Standing facts about people and what they like or don't, including how they want the agent to treat them. Not changes to the agent's tooling or skills.

## Follow-ups
- Things someone said they would do, decisions still open, and requests left unfinished. Not fixes owed to the agent's own tooling.
```
