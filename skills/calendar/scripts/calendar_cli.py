#!/usr/bin/env python3
"""Read and change CalDAV calendars. Prints JSON in the local time zone."""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import icalendar
import recurring_ical_events

CACHE_SECONDS = 7 * 24 * 3600
QUERY_DAYS_BACK = 30
QUERY_DAYS_AHEAD = 365
DESCRIPTION_LIMIT = 500
WRITE_PRIVILEGES = {"all", "write"}
PROPFIND_CALENDARS = b"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <d:current-user-privilege-set/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""


class CalendarError(RuntimeError):
    pass


def local_zone() -> tzinfo:
    """The time zone `date` uses: TZ, or else the zone /etc/localtime links to."""
    name = os.environ.get("TZ", "").removeprefix(":")
    if not name:
        name = str(Path("/etc/localtime").resolve()).partition("zoneinfo/")[2]
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now().astimezone().tzinfo or timezone.utc


# Input


def parse_when(value: str, zone: tzinfo) -> date | datetime:
    """A date, or a local date and time such as "2026-10-09 15:30"."""
    text = value.strip().replace(" ", "T", 1)
    try:
        if "T" not in text:
            return date.fromisoformat(text)
        result = datetime.fromisoformat(text)
    except ValueError as error:
        raise CalendarError(f'Expected YYYY-MM-DD or "YYYY-MM-DD HH:MM", got "{value}".') from error
    return result if result.tzinfo else result.replace(tzinfo=zone)


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?", value.strip().lower())
    if not value.strip() or not match:
        raise CalendarError(f'Expected a duration such as "45m", "1h30m", or "2d", got "{value}".')
    days, hours, minutes = (int(part or 0) for part in match.groups())
    return timedelta(days=days, hours=hours, minutes=minutes)


def parse_rrule(value: str) -> icalendar.vRecur:
    try:
        return icalendar.vRecur.from_ical(value.strip().removeprefix("RRULE:"))
    except ValueError as error:
        raise CalendarError(f'Invalid RRULE "{value}".') from error


def is_date(value: Any) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def timing(
    start: date | datetime, end: date | datetime | None, duration: timedelta | None, all_day: bool
) -> tuple[date | datetime, date | datetime]:
    """DTSTART and DTEND for an event. An all-day `end` is the last day, inclusive."""
    if all_day or is_date(start):
        first = start.date() if isinstance(start, datetime) else start
        if end is not None:
            last = end.date() if isinstance(end, datetime) else end
            if last < first:
                raise CalendarError("The end date is before the start date.")
            return first, last + timedelta(days=1)
        if duration is not None:
            if duration.days < 1 or duration % timedelta(days=1):
                raise CalendarError("An all-day event's duration must be whole days.")
            return first, first + duration
        return first, first + timedelta(days=1)
    if is_date(end):
        raise CalendarError("A timed event needs an end time, not just a date.")
    if end is not None:
        if end <= start:
            raise CalendarError("The end is not after the start.")
        return start, end
    return start, start + (duration or timedelta(hours=1))


# Output


def as_local(value: date | datetime, zone: tzinfo) -> date | datetime:
    if is_date(value):
        return value
    return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)


def show(value: date | datetime, zone: tzinfo) -> str:
    local = as_local(value, zone)
    return local.isoformat() if is_date(local) else local.isoformat(timespec="minutes")


def attendee_name(attendee: Any) -> str:
    name = getattr(attendee, "params", {}).get("CN")
    return str(name or attendee).removeprefix("mailto:").removeprefix("MAILTO:")


def end_of(component: icalendar.Event) -> date | datetime:
    """DTEND, or what RFC 5545 says it is when DTEND and DURATION are both missing."""
    try:
        return component.end
    except icalendar.IncompleteComponent:
        start = component.start
        return start + timedelta(days=1) if is_date(start) else start


def describe(component: icalendar.Event, calendar_name: str, zone: tzinfo) -> dict[str, Any]:
    start, end = component.start, end_of(component)
    all_day = is_date(start)
    result: dict[str, Any] = {
        "id": str(component.get("UID")),
        "title": str(component.get("SUMMARY", "")),
        "start": show(start, zone),
        "end": show(end - timedelta(days=1) if all_day else end, zone),
        "allDay": all_day,
        "calendars": [calendar_name],
    }
    if component.get("RRULE"):
        result["recurring"] = True
    if component.get("RECURRENCE-ID"):
        result["recurring"] = True
        result["occurrence"] = show(component["RECURRENCE-ID"].dt, zone)
    if location := str(component.get("LOCATION", "")).strip():
        result["location"] = location
    if description := str(component.get("DESCRIPTION", "")).strip():
        if len(description) > DESCRIPTION_LIMIT:
            description = description[:DESCRIPTION_LIMIT].rstrip() + "…"
        result["description"] = description
    attendees = component.get("ATTENDEE")
    if attendees:
        attendees = attendees if isinstance(attendees, list) else [attendees]
        result["attendees"] = [attendee_name(attendee) for attendee in attendees]
    return result


def merge(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per event occurrence, listing every calendar it's on."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (event["id"], event.get("occurrence") or event["start"])
        if key in merged:
            for name in event["calendars"]:
                if name not in merged[key]["calendars"]:
                    merged[key]["calendars"].append(name)
        else:
            merged[key] = event
    return sorted(merged.values(), key=lambda event: (event["start"], event["title"]))


def matches(component: icalendar.Event, query: str) -> bool:
    text = " ".join(str(component.get(key, "")) for key in ("SUMMARY", "LOCATION", "DESCRIPTION"))
    return query.casefold() in text.casefold()


# Calendar discovery


def parse_calendar_listing(xml: bytes | str, base_url: str) -> list[dict[str, Any]]:
    """Event calendars from a Depth: 1 PROPFIND of the calendar home."""
    root = ElementTree.fromstring(xml.encode() if isinstance(xml, str) else xml)
    calendars = []
    for response in root.iter("{DAV:}response"):
        href = response.findtext("{DAV:}href") or ""
        props = [
            propstat.find("{DAV:}prop")
            for propstat in response.iter("{DAV:}propstat")
            if "200" in (propstat.findtext("{DAV:}status") or "")
        ]
        props = [prop for prop in props if prop is not None]
        if not any(
            prop.find("{DAV:}resourcetype/{urn:ietf:params:xml:ns:caldav}calendar") is not None for prop in props
        ):
            continue
        components = {
            comp.get("name", "").upper() for prop in props for comp in prop.iter("{urn:ietf:params:xml:ns:caldav}comp")
        }
        if components and "VEVENT" not in components:
            continue
        privileges = {
            child.tag.split("}")[1]
            for prop in props
            for privilege in prop.iter("{DAV:}privilege")
            for child in privilege
        }
        identifier = unquote(href.rstrip("/").rsplit("/", 1)[-1])
        name = next((prop.findtext("{DAV:}displayname") for prop in props if prop.findtext("{DAV:}displayname")), None)
        calendars.append(
            {
                "name": name or identifier,
                "id": identifier,
                "writable": bool(privileges & WRITE_PRIVILEGES) or {"bind", "write-content"} <= privileges,
                # Fastmail sends "@" unescaped here, then refuses a PUT to that URL.
                "url": urljoin(base_url, quote(unquote(href), safe="/")),
            }
        )
    return calendars


def pick(calendars: list[dict[str, Any]], name: str | None, *, writable: bool) -> list[dict[str, Any]]:
    """The calendars a command should use."""
    if name:
        wanted = name.casefold()
        found = [cal for cal in calendars if wanted in (cal["name"].casefold(), cal["id"].casefold())]
        if not found:
            names = ", ".join(cal["name"] for cal in calendars)
            raise CalendarError(f'No calendar named "{name}". Calendars: {names}.')
        if writable and not found[0]["writable"]:
            raise CalendarError(f"{found[0]['name']} is read-only.")
        return found[:1]
    if writable:
        found = [cal for cal in calendars if cal["writable"]]
        if not found:
            raise CalendarError("No calendar is writable.")
        return found
    return calendars


def only_one(calendars: list[dict[str, Any]]) -> dict[str, Any]:
    if len(calendars) > 1:
        names = ", ".join(cal["name"] for cal in calendars)
        raise CalendarError(f"Several calendars are writable ({names}). Pass --calendar.")
    return calendars[0]


# Changing events


def master_of(ical: icalendar.Calendar) -> icalendar.Event:
    for component in ical.walk("VEVENT"):
        if not component.get("RECURRENCE-ID"):
            return component
    raise CalendarError("The event has no main VEVENT.")


def same_moment(a: date | datetime, b: date | datetime, zone: tzinfo) -> bool:
    if is_date(a) or is_date(b):
        return is_date(a) and is_date(b) and a == b
    return as_local(a, zone) == as_local(b, zone)


def find_occurrence(ical: icalendar.Calendar, value: str, zone: tzinfo) -> date | datetime:
    """The RECURRENCE-ID of the occurrence named by a date or the `occurrence` value."""
    master = master_of(ical)
    if not master.get("RRULE") and not master.get("RDATE"):
        raise CalendarError("The event isn't recurring, so leave out --occurrence.")
    wanted = parse_when(value, zone)
    day = wanted if is_date(wanted) else as_local(wanted, zone).date()

    def named(rid: date | datetime) -> bool:
        if is_date(wanted):
            return (rid if is_date(rid) else as_local(rid, zone).date()) == wanted
        return same_moment(rid, wanted, zone)

    # Overrides are matched by their original time, since one may have moved to another day.
    found = [component["RECURRENCE-ID"].dt for component in ical.walk("VEVENT") if component.get("RECURRENCE-ID")]
    found = [rid for rid in found if named(rid)]
    if not found:
        start = midnight(day, zone)
        instances = recurring_ical_events.of(ical).between(start, start + timedelta(days=1))
        found = [instance["RECURRENCE-ID"].dt for instance in instances if instance.get("RECURRENCE-ID")]
        found = [rid for rid in found if named(rid)]
    if not found:
        raise CalendarError(f"The event has no occurrence on {value}.")
    if len(found) > 1:
        times = ", ".join(show(rid, zone) for rid in found)
        raise CalendarError(f"The event occurs more than once on {value} ({times}). Pass the exact occurrence.")
    return found[0]


def override_for(ical: icalendar.Calendar, rid: date | datetime, zone: tzinfo) -> icalendar.Event | None:
    for component in ical.walk("VEVENT"):
        if component.get("RECURRENCE-ID") and same_moment(component["RECURRENCE-ID"].dt, rid, zone):
            return component
    return None


def add_override(ical: icalendar.Calendar, rid: date | datetime) -> icalendar.Event:
    """A copy of the series' main event for one occurrence, to change separately."""
    master = master_of(ical)
    length = end_of(master) - master.start
    override = icalendar.Event.from_ical(master.to_ical())
    for key in ("RRULE", "RDATE", "EXDATE", "DTSTART", "DTEND", "DURATION"):
        override.pop(key, None)
    override.add("RECURRENCE-ID", rid)
    override.add("DTSTART", rid)
    override.add("DTEND", rid + length)
    ical.add_component(override)
    return override


def replace(component: icalendar.Event, key: str, value: Any) -> None:
    component.pop(key, None)
    if value is not None and value != "":
        component.add(key, value)


def touch(component: icalendar.Event, *, revision: bool = True) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    replace(component, "DTSTAMP", now)
    replace(component, "LAST-MODIFIED", now)
    if revision:
        replace(component, "SEQUENCE", int(component.get("SEQUENCE", 0)) + 1)


def apply_changes(component: icalendar.Event, changes: dict[str, Any]) -> None:
    for option, key in (("title", "SUMMARY"), ("location", "LOCATION"), ("description", "DESCRIPTION")):
        if changes.get(option) is not None:
            replace(component, key, changes[option])
    if changes.get("rrule") is not None:
        replace(component, "RRULE", changes["rrule"])
    start, end, duration = changes.get("start"), changes.get("end"), changes.get("duration")
    if start is not None or end is not None or duration is not None or changes.get("all_day"):
        old_start, old_end = component.start, end_of(component)
        new_start = start if start is not None else old_start
        all_day = changes.get("all_day")
        becomes_all_day = all_day and not is_date(old_start)
        if end is None and duration is None and is_date(new_start) == is_date(old_start) and not becomes_all_day:
            duration = old_end - old_start
            if is_date(new_start):
                end, duration = new_start + duration - timedelta(days=1), None
        dtstart, dtend = timing(new_start, end, duration, bool(all_day))
        component.pop("DURATION", None)
        replace(component, "DTSTART", dtstart)
        replace(component, "DTEND", dtend)
    touch(component)


def new_event(
    title: str,
    dtstart: date | datetime,
    dtend: date | datetime,
    location: str | None,
    description: str | None,
    rrule: icalendar.vRecur | None,
) -> icalendar.Calendar:
    ical = icalendar.Calendar()
    ical.add("PRODID", "-//Barnaby//calendar skill//EN")
    ical.add("VERSION", "2.0")
    event = icalendar.Event()
    event.add("UID", str(uuid.uuid4()))
    event.add("SUMMARY", title)
    event.add("DTSTART", dtstart)
    event.add("DTEND", dtend)
    for key, value in (("LOCATION", location), ("DESCRIPTION", description), ("RRULE", rrule)):
        if value:
            event.add(key, value)
    touch(event, revision=False)
    ical.add_component(event)
    ical.add_missing_timezones()
    return ical


# The server


class Server:
    def __init__(self) -> None:
        missing = [key for key in ("CALDAV_URL", "CALDAV_USERNAME", "CALDAV_PASSWORD") if not os.environ.get(key)]
        if missing:
            raise CalendarError(f"{', '.join(missing)} must be set.")
        import caldav

        self.caldav = caldav
        self.url = os.environ["CALDAV_URL"]
        self.username = os.environ["CALDAV_USERNAME"]
        self.client = caldav.DAVClient(url=self.url, username=self.username, password=os.environ["CALDAV_PASSWORD"])
        cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
        key = hashlib.sha256(f"{self.url}\n{self.username}".encode()).hexdigest()[:16]
        self.cache = cache_root / "barnaby-calendar" / f"calendars-{key}.json"

    def calendars(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh:
            try:
                if time.time() - self.cache.stat().st_mtime < CACHE_SECONDS:
                    return json.loads(self.cache.read_text())
            except (OSError, ValueError):
                pass
        home = str(self.client.principal().calendar_home_set.url)
        response = self.client.propfind(home, PROPFIND_CALENDARS, depth=1)
        calendars = parse_calendar_listing(response.raw, home)
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps(calendars))
        except OSError:
            pass
        return calendars

    def calendar(self, info: dict[str, Any]) -> Any:
        return self.caldav.Calendar(client=self.client, url=info["url"], name=info["name"])

    def search(self, info: dict[str, Any], start: datetime, end: datetime) -> list[icalendar.Event]:
        found = self.calendar(info).search(start=start, end=end, event=True, expand=True)
        return [component for item in found for component in item.icalendar_instance.walk("VEVENT")]

    def resource(self, info: dict[str, Any], uid: str) -> Any | None:
        try:
            return self.calendar(info).event_by_uid(uid)
        except self.caldav.lib.error.NotFoundError:
            return None

    def create(self, info: dict[str, Any], ical: icalendar.Calendar) -> None:
        self.calendar(info).save_event(ical=ical.to_ical().decode())

    def save(self, resource: Any, ical: icalendar.Calendar) -> None:
        ical.add_missing_timezones()
        resource.icalendar_instance = ical
        resource.save(increase_seqno=False, only_this_recurrence=False, all_recurrences=False)


# Commands


def today(zone: tzinfo) -> date:
    return datetime.now(zone).date()


def window(args: argparse.Namespace, zone: tzinfo) -> tuple[date, date]:
    """The first and last day to search, inclusive."""
    if args.from_ is None and args.to is None and args.days is None and args.query:
        return today(zone) - timedelta(days=QUERY_DAYS_BACK), today(zone) + timedelta(days=QUERY_DAYS_AHEAD)
    first = as_day(args.from_, zone) if args.from_ else today(zone)
    if args.to:
        last = as_day(args.to, zone)
    else:
        last = first + timedelta(days=(args.days or 1) - 1)
    if last < first:
        raise CalendarError("--to is before --from.")
    return first, last


def as_day(value: str, zone: tzinfo) -> date:
    when = parse_when(value, zone)
    return when if is_date(when) else as_local(when, zone).date()


def midnight(day: date, zone: tzinfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone)


def find_resource(
    server: Server, calendars: list[dict[str, Any]], uid: str, name: str | None
) -> tuple[dict[str, Any], Any]:
    found = [(info, res) for info in calendars if (res := server.resource(info, uid)) is not None]
    if not found:
        where = "that calendar" if name else "a writable calendar"
        raise CalendarError(f"No event with ID {uid} on {where}.")
    if len(found) > 1:
        names = ", ".join(info["name"] for info, _ in found)
        raise CalendarError(f"Event {uid} is on several calendars ({names}). Pass --calendar.")
    return found[0]


def changes_from(args: argparse.Namespace, zone: tzinfo) -> dict[str, Any]:
    return {
        "title": args.title,
        "location": args.location,
        "description": args.description,
        "start": parse_when(args.start, zone) if args.start else None,
        "end": parse_when(args.end, zone) if args.end else None,
        "duration": parse_duration(args.duration) if args.duration else None,
        "all_day": getattr(args, "all_day", False),
        "rrule": parse_rrule(args.rrule) if getattr(args, "rrule", None) else None,
    }


def run(args: argparse.Namespace, server: Server, zone: tzinfo) -> dict[str, Any]:
    if args.command == "calendars":
        calendars = server.calendars(refresh=True)
        return {"calendars": [{key: cal[key] for key in ("name", "id", "writable")} for cal in calendars]}

    calendars = server.calendars()

    if args.command == "events":
        first, last = window(args, zone)
        start, end = midnight(first, zone), midnight(last + timedelta(days=1), zone)
        events = []
        for info in pick(calendars, args.calendar, writable=False):
            for component in server.search(info, start, end):
                if str(component.get("STATUS", "")).upper() == "CANCELLED":
                    continue
                if args.query and not matches(component, args.query):
                    continue
                events.append(describe(component, info["name"], zone))
        return {"from": first.isoformat(), "to": last.isoformat(), "events": merge(events)}

    if args.command == "add":
        info = only_one(pick(calendars, args.calendar, writable=True))
        changes = changes_from(args, zone)
        dtstart, dtend = timing(changes["start"], changes["end"], changes["duration"], args.all_day)
        ical = new_event(args.title, dtstart, dtend, args.location, args.description, changes["rrule"])
        server.create(info, ical)
        return {"added": describe(master_of(ical), info["name"], zone)}

    info, resource = find_resource(server, pick(calendars, args.calendar, writable=True), args.id, args.calendar)
    ical = resource.icalendar_instance

    if args.command == "update":
        changes = changes_from(args, zone)
        if not any(value for value in changes.values()):
            raise CalendarError("Nothing to change.")
        if args.occurrence:
            if changes["rrule"] is not None:
                raise CalendarError("--rrule changes the whole series; leave out --occurrence.")
            rid = find_occurrence(ical, args.occurrence, zone)
            component = override_for(ical, rid, zone) or add_override(ical, rid)
        else:
            component = master_of(ical)
        apply_changes(component, changes)
        server.save(resource, ical)
        return {"updated": describe(component, info["name"], zone)}

    # delete
    if not args.occurrence:
        event = describe(master_of(ical), info["name"], zone)
        resource.delete()
        return {"deleted": event}
    rid = find_occurrence(ical, args.occurrence, zone)
    if override := override_for(ical, rid, zone):
        ical.subcomponents.remove(override)
    master = master_of(ical)
    # icalendar leaves VALUE=DATE off a date EXDATE, and some servers then misread it.
    master.add("EXDATE", rid, parameters={"VALUE": "DATE"} if is_date(rid) else None)
    touch(master)
    server.save(resource, ical)
    return {"deleted": {"id": args.id, "title": str(master.get("SUMMARY", "")), "occurrence": show(rid, zone)}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and change CalDAV calendars.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("calendars", help="List calendars and whether each is writable.")

    events = commands.add_parser("events", help="List event occurrences.")
    events.add_argument("--from", dest="from_", help="First day (default: today).")
    span = events.add_mutually_exclusive_group()
    span.add_argument("--to", help="Last day, inclusive.")
    span.add_argument("--days", type=int, help="Number of days (default: 1).")
    events.add_argument("--calendar", help="Only this calendar.")
    events.add_argument("--query", help="Text to find in the title, location, or description.")

    def add_fields(command: argparse.ArgumentParser, *, required: bool) -> None:
        command.add_argument("--title", required=required)
        command.add_argument("--start", required=required, help='Date, or "YYYY-MM-DD HH:MM" local time.')
        length = command.add_mutually_exclusive_group()
        length.add_argument("--end", help="End time, or the last day of an all-day event.")
        length.add_argument("--duration", help='Such as "45m", "1h30m", or "2d".')
        command.add_argument("--location")
        command.add_argument("--description")
        command.add_argument("--all-day", action="store_true")
        command.add_argument("--rrule", help='Recurrence, such as "FREQ=WEEKLY;BYDAY=TU;COUNT=10".')
        command.add_argument("--calendar", help="Required when more than one calendar is writable.")

    add_fields(commands.add_parser("add", help="Add an event."), required=True)

    update = commands.add_parser("update", help="Change an event, or one occurrence of a recurring event.")
    update.add_argument("id")
    update.add_argument("--occurrence", help="Change only this occurrence.")
    add_fields(update, required=False)

    delete = commands.add_parser("delete", help="Delete an event, or one occurrence of a recurring event.")
    delete.add_argument("id")
    delete.add_argument("--occurrence", help="Delete only this occurrence.")
    delete.add_argument("--calendar")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    zone = local_zone()
    try:
        result = run(args, Server(), zone)
    except CalendarError as error:
        raise SystemExit(str(error)) from None
    except Exception as error:
        if not type(error).__module__.startswith(("caldav", "niquests", "requests", "urllib3")):
            raise
        raise SystemExit(f"The CalDAV request failed: {type(error).__name__}: {str(error)[:300]}") from None
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
