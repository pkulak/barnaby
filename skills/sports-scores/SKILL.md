---
name: sports-scores
description: Sports scores, schedules, and next or recent games from ESPN.
---

# Sports Scores

Use the deterministic helper script in this skill directory. It owns team lookup, ESPN API calls, local time conversion, and schema normalization.

You interpret the user's request. The script does not understand English questions. Decide whether the user wants:

- a team's next game: `team next`
- today's/live score for a team: `scoreboard --team-id ...`
- all games in a league today: `scoreboard`
- a recent completed result / "did they win?": `team last-result`
- a team's record or place in its division: `team next` (returns `record` and `standing`)
- a league or conference table: `standings`
- a college poll ranking: `rankings`
- team lookup/disambiguation: `teams search`

Run commands from this skill directory:

```bash
cd "$BARNABY_PI_SKILLS_DIR/sports-scores"
```

## League IDs

Common ESPN league paths:

| User says | sport | league |
|---|---|---|
| NBA | basketball | nba |
| WNBA | basketball | wnba |
| NFL | football | nfl |
| College Football, CFB, NCAAF | football | college-football |
| MLB | baseball | mlb |
| NHL, hockey | hockey | nhl |
| MLS, US soccer | soccer | usa.1 |
| EPL, Premier League, English soccer | soccer | eng.1 |
| Men's college basketball, March Madness | basketball | mens-college-basketball |

You can inspect supported leagues with:

```bash
python3 scripts/sports_scores.py leagues
```

## Team lookup

If you already know a team's ESPN ID, you can use it without searching, as long as the output confirms it: the team's name must appear, such as `team.displayName` from `team next` or a competitor in the returned game. If the name is wrong or missing (for example, `rankings` returns only the ID), search instead.

Otherwise, describe the team you mean, resolving nicknames and context from the conversation first. Include the sport or league when you know it:

```bash
python3 scripts/sports_scores.py teams search --query "Oregon State Beavers football"
python3 scripts/sports_scores.py teams search --query "spurs" --league eng.1
python3 scripts/sports_scores.py teams search --query "Montana Grizzlies" --sport football
```

It returns up to five ESPN teams with a `probability`, most likely first. Use the top result when it clearly leads. An empty result means the team isn't in a supported league.

If several teams are close, use the user's sport/league context to choose. If still ambiguous, ask a short clarification question.

## Next game

```bash
python3 scripts/sports_scores.py team next \
  --sport basketball \
  --league nba \
  --team-id 22
```

`.team.record` is the season record and `.team.standing` its place, such as "4th in AFC West".

If `.event` is `null`, say there is no upcoming game scheduled. Otherwise format naturally. Use the returned `dateLocal`, `relativeDateLocal`, status, venue, broadcasts, and competitors.

Emphasize **today** or **tomorrow** in the final response when applicable.

## Today's/live scoreboard

For one team:

```bash
python3 scripts/sports_scores.py scoreboard \
  --sport basketball \
  --league nba \
  --team-id 22
```

For all games in a league today:

```bash
python3 scripts/sports_scores.py scoreboard \
  --sport basketball \
  --league nba
```

For a specific date:

```bash
python3 scripts/sports_scores.py scoreboard \
  --sport basketball \
  --league nba \
  --date 20260627
```

If there are no events, say there are no games for that team/league today. For league-wide queries, list all games concisely.

## Last result / did they win?

```bash
python3 scripts/sports_scores.py team last-result \
  --sport basketball \
  --league nba \
  --team-id 22 \
  --days 7
```

If `result.outcome` is:

- `win`: say the team beat the opponent with the score and day.
- `loss`: say the team lost to the opponent with the score and day.
- `tie`: for soccer, say they drew; otherwise say tied.
- `in_progress`: report the current live score instead of looking further back.
- `null`: say they have not completed a game in the searched window.

## Standings

```bash
python3 scripts/sports_scores.py standings --sport football --league nfl
python3 scripts/sports_scores.py standings --sport football --league nfl --divisions --team-id 24
```

Groups are conferences by default; `--divisions` groups by division. `--team-id` keeps only the group containing that team. Teams are listed in order, with `record`, and `conferenceRecord`, `gamesBehind`, `playoffSeed`, `streak`, `rank`, or `points` when ESPN provides them.

## Rankings

College leagues only:

```bash
python3 scripts/sports_scores.py rankings --sport football --league college-football
python3 scripts/sports_scores.py rankings --sport football --league college-football --poll coaches
python3 scripts/sports_scores.py rankings --sport football --league college-football --team-id 2483
```

Without `--team-id`, it returns the AP poll unless `--poll` names another. With `--team-id`, `rankedIn` lists every poll that ranks the team; an empty list means unranked.

## Response style

Respond once with the answer, not the raw JSON. Be concise.

Examples:

```text
The **Celtics** play the **Knicks** **today** at 4:30 PM in New York.
```

```text
**Arsenal** beat **Chelsea** 2–1 on Saturday.
```

```text
There are no NBA games today.
```
