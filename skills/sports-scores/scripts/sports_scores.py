#!/usr/bin/env python3
"""Deterministic ESPN sports data helper for the sports-scores skill."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_URL = "https://site.web.api.espn.com/apis/site/v2/sports"
STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports"

LEAGUES = [
    {
        "sport": "basketball",
        "league": "nba",
        "name": "NBA",
        "aliases": ["nba", "pro basketball"],
    },
    {
        "sport": "basketball",
        "league": "wnba",
        "name": "WNBA",
        "aliases": ["wnba", "women's basketball"],
    },
    {
        "sport": "football",
        "league": "nfl",
        "name": "NFL",
        "aliases": ["nfl", "pro football"],
    },
    {
        "sport": "football",
        "league": "college-football",
        "name": "College Football",
        "aliases": ["college football", "cfb", "ncaaf"],
    },
    {
        "sport": "baseball",
        "league": "mlb",
        "name": "MLB",
        "aliases": ["mlb", "baseball"],
    },
    {
        "sport": "hockey",
        "league": "nhl",
        "name": "NHL",
        "aliases": ["nhl", "hockey"],
    },
    {
        "sport": "soccer",
        "league": "usa.1",
        "name": "MLS",
        "aliases": ["mls", "major league soccer"],
    },
    {
        "sport": "soccer",
        "league": "eng.1",
        "name": "English Premier League",
        "aliases": ["epl", "premier league", "english soccer"],
    },
    {
        "sport": "basketball",
        "league": "mens-college-basketball",
        "name": "Men's College Basketball",
        "aliases": ["men's college basketball", "college basketball", "march madness", "ncaam"],
    },
]

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DECISIONS_MODEL = "typesafe/jev-1.13"
# The TZ name when set, since zoneinfo doesn't need TZDIR; otherwise None,
# which datetime treats as the system's local zone.
LOCAL_TZ = ZoneInfo(os.environ["TZ"].removeprefix(":")) if os.environ.get("TZ") else None
# A Jev Choice question takes at most 255 options; one is reserved for "none".
CHOICE_LIMIT = 254
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "sports-scores"
CACHE_MAX_AGE = dt.timedelta(days=30)


def local_now() -> dt.datetime:
    return dt.datetime.now(LOCAL_TZ).astimezone(LOCAL_TZ)


def fail(message: str, code: int = 1) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(code)


def emit(data: object) -> None:
    json.dump(data, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def league_name(sport: str, league: str) -> str:
    for item in LEAGUES:
        if item["sport"] == sport and item["league"] == league:
            return item["name"]
    return league.upper()


def team_identity(team: dict, sport: str, league: str) -> dict:
    return {
        "id": str(team.get("id", "")),
        "abbreviation": team.get("abbreviation", ""),
        "displayName": team.get("displayName", ""),
        "shortDisplayName": team.get("shortDisplayName") or team.get("name") or team.get("displayName", ""),
        "sport": sport,
        "league": league,
        "leagueName": league_name(sport, league),
    }


def league_teams(sport: str, league: str) -> list[dict]:
    """ESPN's team list for a league, cached for a month."""
    path = CACHE_DIR / f"{sport}-{league}.json"
    try:
        age = dt.datetime.now() - dt.datetime.fromtimestamp(path.stat().st_mtime)
        if age < CACHE_MAX_AGE:
            return json.loads(path.read_text())
    except (OSError, ValueError):
        pass

    data = fetch_json(f"{BASE_URL}/{sport}/{league}/teams?limit=1000")
    teams = [
        team_identity(item.get("team") or {}, sport, league)
        for sport_data in data.get("sports") or []
        for league_data in sport_data.get("leagues") or []
        for item in league_data.get("teams") or []
    ]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(teams))
    return teams


def decide(state: object, questions: dict) -> dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        fail("OPENROUTER_API_KEY is not set")
    body = {"model": DECISIONS_MODEL, "state": state, "questions": questions}
    request = urllib.request.Request(
        DECISIONS_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["answers"]
    except urllib.error.HTTPError as exc:
        fail(f"Team lookup returned HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}")
    except (urllib.error.URLError, TimeoutError) as exc:
        fail(f"Could not reach the team lookup service: {exc}")


def search_teams(args: argparse.Namespace) -> None:
    """Map a team description to ESPN teams with Jev, most likely first."""
    leagues = [
        item
        for item in LEAGUES
        if (not args.sport or item["sport"] == args.sport) and (not args.league or item["league"] == args.league)
    ]
    if not leagues:
        fail("No supported league matches --sport/--league; run the leagues command")

    questions: dict = {}
    teams_by_question: dict[str, dict[str, dict]] = {}
    if len(leagues) > 1:
        questions["league"] = {
            "type": "choice",
            "instructions": "Which league does this team play in?",
            "criteria": {
                **{f"{item['sport']}/{item['league']}": item["name"] for item in leagues},
                "none": "Not a team, or a team in a league not listed",
            },
        }
    for item in leagues:
        teams = league_teams(item["sport"], item["league"])
        for start in range(0, len(teams), CHOICE_LIMIT):
            chunk = {team["id"]: team for team in teams[start : start + CHOICE_LIMIT]}
            question_id = f"{item['sport']}/{item['league']}/{start}"
            teams_by_question[question_id] = chunk
            questions[question_id] = {
                "type": "choice",
                "instructions": "Which of these teams is it?",
                "criteria": {
                    **{tid: f"{team['displayName']} ({team['abbreviation']})" for tid, team in chunk.items()},
                    "none": "None of these teams",
                },
            }

    answers = decide({"team": args.query}, questions)
    league_probabilities = (answers.get("league") or {}).get("probabilities") or {}
    results = []
    for question_id, chunk in teams_by_question.items():
        answer = answers.get(question_id) or {}
        choice = answer.get("choice")
        if choice not in chunk:
            continue
        probability = answer["probabilities"][choice]
        if league_probabilities:
            probability *= league_probabilities.get(question_id.rsplit("/", 1)[0], 0)
        if probability >= 0.01:
            results.append({**chunk[choice], "probability": round(probability, 2)})

    results.sort(key=lambda item: -item["probability"])
    emit(results[: args.limit])


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "barnaby-sports-scores/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        fail(f"ESPN API returned HTTP {exc.code}: {url}")
    except urllib.error.URLError as exc:
        fail(f"Could not reach ESPN API: {exc.reason}")
    except TimeoutError:
        fail("Timed out contacting ESPN API")


def fetch_json_optional(url: str) -> dict | None:
    request = urllib.request.Request(url, headers={"User-Agent": "barnaby-sports-scores/1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def parse_espn_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(LOCAL_TZ)


def relative_date(local: dt.datetime | None) -> str | None:
    if local is None:
        return None
    today = local_now().date()
    delta = (local.date() - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if -6 <= delta <= 6:
        return local.strftime("%A")
    return local.strftime("%b %-d, %Y")


def normalize_score(value: object) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("displayValue", value.get("value"))
    if value in (None, ""):
        return None
    return str(value)


def normalize_competitor(item: dict) -> dict:
    team = item.get("team", {})
    records = item.get("records") or []
    return {
        "id": str(team.get("id", "")),
        "abbreviation": team.get("abbreviation", ""),
        "displayName": team.get("displayName", ""),
        "shortDisplayName": team.get("shortDisplayName", team.get("displayName", "")),
        "homeAway": item.get("homeAway"),
        "score": normalize_score(item.get("score")),
        "winner": item.get("winner") if "winner" in item else None,
        "records": [record.get("summary", "") for record in records if record.get("summary")],
    }


def event_status_type(event: dict) -> dict:
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions else {}
    return (event.get("status") or {}).get("type") or competition.get("status", {}).get("type", {})


def normalize_event(event: dict) -> dict:
    local = parse_espn_datetime(event.get("date"))
    competitions = event.get("competitions") or []
    competition = competitions[0] if competitions else {}
    status_type = event_status_type(event)

    broadcasts = []
    for broadcast in competition.get("broadcasts") or []:
        media = broadcast.get("media") or {}
        name = media.get("shortName") or media.get("name") or broadcast.get("names")
        if isinstance(name, list):
            broadcasts.extend(str(x) for x in name if x)
        elif name:
            broadcasts.append(str(name))

    competitors = [normalize_competitor(item) for item in competition.get("competitors") or []]

    return {
        "id": str(event.get("id", "")),
        "name": event.get("name") or event.get("shortName") or "",
        "shortName": event.get("shortName") or "",
        "date": event.get("date"),
        "dateLocal": local.isoformat() if local else None,
        "relativeDateLocal": relative_date(local),
        "status": {
            "state": status_type.get("state"),
            "detail": status_type.get("detail"),
            "shortDetail": status_type.get("shortDetail"),
            "completed": status_type.get("completed"),
        },
        "venue": (competition.get("venue") or {}).get("fullName"),
        "broadcasts": sorted(set(broadcasts)),
        "competitors": competitors,
    }


def select_next_event(events: list[dict]) -> dict | None:
    now = local_now()
    candidates = []
    for event in events:
        status_type = event_status_type(event)
        state = status_type.get("state")
        completed = status_type.get("completed")
        local = parse_espn_datetime(event.get("date"))

        if state == "in":
            priority = 0
        elif state == "pre" and (local is None or local >= now - dt.timedelta(hours=3)):
            priority = 1
        elif state != "post" and completed is not True:
            priority = 2
        else:
            continue

        candidates.append((priority, local or dt.datetime.max.replace(tzinfo=dt.timezone.utc), event))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def team_next(args: argparse.Namespace) -> None:
    team_id = urllib.parse.quote(str(args.team_id))
    schedule_url = f"{BASE_URL}/{args.sport}/{args.league}/teams/{team_id}/schedule"
    schedule_data = fetch_json_optional(schedule_url)

    if schedule_data is not None:
        team_data = schedule_data.get("team") or {}
        events = schedule_data.get("events") or []
        event = select_next_event(events)
        if event is None:
            url = f"{BASE_URL}/{args.sport}/{args.league}/teams/{team_id}"
            data = fetch_json_optional(url)
            if data is not None:
                team_data = data.get("team") or team_data
                event = select_next_event(team_data.get("nextEvent") or [])
    else:
        url = f"{BASE_URL}/{args.sport}/{args.league}/teams/{team_id}"
        data = fetch_json(url)
        team_data = data.get("team") or {}
        event = select_next_event(team_data.get("nextEvent") or [])

    identity = team_identity({"id": args.team_id, **team_data}, args.sport, args.league)
    record = team_data.get("recordSummary") or next(
        (item.get("summary") for item in (team_data.get("record") or {}).get("items") or [] if item.get("summary")),
        None,
    )
    identity["record"] = record
    identity["standing"] = team_data.get("standingSummary")
    emit({"team": identity, "event": normalize_event(event) if event else None})


def scoreboard(args: argparse.Namespace) -> None:
    date_arg = args.date or local_now().strftime("%Y%m%d")
    query = f"?{urllib.parse.urlencode({'dates': date_arg})}"
    url = f"{BASE_URL}/{args.sport}/{args.league}/scoreboard{query}"
    data = fetch_json(url)
    events = [normalize_event(event) for event in data.get("events") or []]
    if args.team_id:
        team_id = str(args.team_id)
        events = [
            event
            for event in events
            if any(competitor.get("id") == team_id for competitor in event.get("competitors", []))
        ]

    emit(
        {
            "sport": args.sport,
            "league": args.league,
            "leagueName": league_name(args.sport, args.league),
            "date": date_arg,
            "events": events,
        }
    )


def score_value(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except ValueError:
            return None


def result_for_event(event: dict, team_id: str) -> dict | None:
    competitors = event.get("competitors", [])
    team = next((item for item in competitors if item.get("id") == team_id), None)
    opponent = next((item for item in competitors if item.get("id") != team_id), None)
    if not team:
        return None

    state = event.get("status", {}).get("state")
    if state != "post":
        return {"outcome": "in_progress" if state == "in" else "scheduled"}

    team_score = score_value(team.get("score"))
    opponent_score = score_value(opponent.get("score") if opponent else None)
    winner = team.get("winner")
    if winner is True:
        outcome = "win"
    elif winner is False and opponent and opponent.get("winner") is True:
        outcome = "loss"
    elif team_score is not None and opponent_score is not None:
        if team_score > opponent_score:
            outcome = "win"
        elif team_score < opponent_score:
            outcome = "loss"
        else:
            outcome = "tie"
    else:
        outcome = "completed"

    return {
        "outcome": outcome,
        "team": team.get("shortDisplayName") or team.get("displayName"),
        "teamScore": team_score,
        "opponent": (opponent or {}).get("shortDisplayName") or (opponent or {}).get("displayName"),
        "opponentScore": opponent_score,
    }


def last_result(args: argparse.Namespace) -> None:
    today = local_now().date()
    team_id = str(args.team_id)
    searched = []

    for offset in range(args.days + 1):
        day = today - dt.timedelta(days=offset)
        date_arg = day.strftime("%Y%m%d")
        params = urllib.parse.urlencode({"dates": date_arg})
        url = f"{BASE_URL}/{args.sport}/{args.league}/scoreboard?{params}"
        data = fetch_json(url)
        events = [normalize_event(event) for event in data.get("events") or []]
        matches = [event for event in events if any(c.get("id") == team_id for c in event.get("competitors", []))]
        searched.append(date_arg)

        in_progress = next((event for event in matches if event.get("status", {}).get("state") == "in"), None)
        if in_progress:
            emit(
                {
                    "teamId": team_id,
                    "searchedDates": searched,
                    "event": in_progress,
                    "result": result_for_event(in_progress, team_id),
                }
            )
            return

        completed = next((event for event in matches if event.get("status", {}).get("state") == "post"), None)
        if completed:
            emit(
                {
                    "teamId": team_id,
                    "searchedDates": searched,
                    "event": completed,
                    "result": result_for_event(completed, team_id),
                }
            )
            return

    emit({"teamId": team_id, "searchedDates": searched, "event": None, "result": None})


STANDING_STATS = {
    "total": "record",
    "vsconf": "conferenceRecord",
    "gamesbehind": "gamesBehind",
    "playoffseed": "playoffSeed",
    "streak": "streak",
    "rank": "rank",
    "points": "points",
    "gamesplayed": "gamesPlayed",
}


def standing_groups(node: dict) -> list[dict]:
    """Flatten ESPN's standings tree into the groups that list teams."""
    groups = []
    entries = (node.get("standings") or {}).get("entries")
    if entries:
        groups.append({"name": node.get("name", ""), "entries": entries})
    for child in node.get("children") or []:
        groups.extend(standing_groups(child))
    return groups


def normalize_standing(entry: dict, sport: str) -> dict:
    team = entry.get("team") or {}
    stats = {}
    for stat in entry.get("stats") or []:
        key = STANDING_STATS.get(stat.get("type", ""))
        if key and stat.get("displayValue") not in (None, ""):
            stats[key] = stat["displayValue"]
    if sport != "soccer":
        stats.pop("points", None)
    if "record" not in stats:
        wins = next((s.get("displayValue") for s in entry.get("stats") or [] if s.get("type") == "wins"), None)
        losses = next((s.get("displayValue") for s in entry.get("stats") or [] if s.get("type") == "losses"), None)
        if wins is not None and losses is not None:
            stats["record"] = f"{wins}-{losses}"
    return {
        "id": str(team.get("id", "")),
        "abbreviation": team.get("abbreviation", ""),
        "displayName": team.get("displayName", ""),
        **stats,
    }


def standing_order(team: dict) -> int:
    """ESPN doesn't always list teams in order; sort by rank, then seed."""
    for key in ("rank", "playoffSeed"):
        value = score_value(team.get(key))
        if value:
            return value
    return 1_000


def standings(args: argparse.Namespace) -> None:
    query = "?level=3" if args.divisions else ""
    data = fetch_json(f"{STANDINGS_URL}/{args.sport}/{args.league}/standings{query}")
    groups = [
        {
            "name": group["name"],
            "teams": sorted(
                (normalize_standing(entry, args.sport) for entry in group["entries"]),
                key=standing_order,
            ),
        }
        for group in standing_groups(data)
    ]
    if args.team_id:
        team_id = str(args.team_id)
        groups = [group for group in groups if any(team["id"] == team_id for team in group["teams"])]
    emit(
        {
            "sport": args.sport,
            "league": args.league,
            "leagueName": league_name(args.sport, args.league),
            "season": (data.get("season") or {}).get("displayName"),
            "groups": groups,
        }
    )


def normalize_rank(rank: dict) -> dict:
    team = rank.get("team") or {}
    return {
        "rank": rank.get("current"),
        "previous": rank.get("previous"),
        "id": str(team.get("id", "")),
        "team": " ".join(part for part in (team.get("location"), team.get("name")) if part),
        "record": rank.get("recordSummary"),
        "points": rank.get("points"),
        "firstPlaceVotes": rank.get("firstPlaceVotes"),
    }


def rankings(args: argparse.Namespace) -> None:
    url = f"{BASE_URL}/{args.sport}/{args.league}/rankings"
    polls = (fetch_json(url).get("rankings")) or []
    if args.team_id:
        team_id = str(args.team_id)
        result = []
        for poll in polls:
            match = next((r for r in poll.get("ranks") or [] if str((r.get("team") or {}).get("id")) == team_id), None)
            if match:
                result.append({"poll": poll.get("name"), **normalize_rank(match)})
        emit({"teamId": team_id, "rankedIn": result})
        return

    if args.poll:
        wanted = args.poll.lower()
        polls = [poll for poll in polls if wanted in poll.get("name", "").lower()]
    else:
        polls = polls[:1]
    emit([{"poll": poll.get("name"), "ranks": [normalize_rank(r) for r in poll.get("ranks") or []]} for poll in polls])


def leagues(_: argparse.Namespace) -> None:
    emit(LEAGUES)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    leagues_parser = subparsers.add_parser("leagues", help="List supported leagues")
    leagues_parser.set_defaults(func=leagues)

    teams_parser = subparsers.add_parser("teams", help="Team lookup commands")
    teams_subparsers = teams_parser.add_subparsers(dest="teams_command", required=True)
    search_parser = teams_subparsers.add_parser("search", help="Find ESPN teams matching a team description")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--sport")
    search_parser.add_argument("--league")
    search_parser.add_argument("--limit", type=int, default=5)
    search_parser.set_defaults(func=search_teams)

    team_parser = subparsers.add_parser("team", help="Team-specific ESPN commands")
    team_subparsers = team_parser.add_subparsers(dest="team_command", required=True)
    next_parser = team_subparsers.add_parser("next", help="Get a team's next event")
    next_parser.add_argument("--sport", required=True)
    next_parser.add_argument("--league", required=True)
    next_parser.add_argument("--team-id", required=True)
    next_parser.set_defaults(func=team_next)

    last_parser = team_subparsers.add_parser("last-result", help="Find a team's most recent completed result")
    last_parser.add_argument("--sport", required=True)
    last_parser.add_argument("--league", required=True)
    last_parser.add_argument("--team-id", required=True)
    last_parser.add_argument("--days", type=int, default=7)
    last_parser.set_defaults(func=last_result)

    scoreboard_parser = subparsers.add_parser("scoreboard", help="Get normalized ESPN scoreboard data")
    scoreboard_parser.add_argument("--sport", required=True)
    scoreboard_parser.add_argument("--league", required=True)
    scoreboard_parser.add_argument("--date", help="YYYYMMDD; defaults to today in local time")
    scoreboard_parser.add_argument("--team-id")
    scoreboard_parser.set_defaults(func=scoreboard)

    standings_parser = subparsers.add_parser("standings", help="Get league standings")
    standings_parser.add_argument("--sport", required=True)
    standings_parser.add_argument("--league", required=True)
    standings_parser.add_argument("--team-id", help="Only return the group containing this team")
    standings_parser.add_argument("--divisions", action="store_true", help="Group by division instead of conference")
    standings_parser.set_defaults(func=standings)

    rankings_parser = subparsers.add_parser("rankings", help="Get college poll rankings")
    rankings_parser.add_argument("--sport", required=True)
    rankings_parser.add_argument("--league", required=True)
    rankings_parser.add_argument("--team-id", help="Report this team's rank in every poll")
    rankings_parser.add_argument("--poll", help="Poll name to show, such as AP or Coaches; defaults to the first poll")
    rankings_parser.set_defaults(func=rankings)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
