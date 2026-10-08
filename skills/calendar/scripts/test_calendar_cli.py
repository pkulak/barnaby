#!/usr/bin/env python3

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import calendar_cli as cli
import icalendar
import recurring_ical_events

ZONE = ZoneInfo("America/Los_Angeles")

LISTING = """<?xml version="1.0" encoding="UTF-8"?>
<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:response>
    <d:href>/dav/calendars/user/sam%40example.com/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/user/sam@example.com/abc-123/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Sam</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <d:current-user-privilege-set><d:privilege><d:all/></d:privilege><d:privilege><d:read/></d:privilege>
      </d:current-user-privilege-set>
      <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/user/sam%40example.com/kid%40example.com.K1/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Kid</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <d:current-user-privilege-set>
        <d:privilege><d:read/></d:privilege><d:privilege><d:write-properties-collection/></d:privilege>
      </d:current-user-privilege-set>
      <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/user/sam%40example.com/house/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Household</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <d:current-user-privilege-set>
        <d:privilege><d:read/></d:privilege><d:privilege><d:bind/></d:privilege>
        <d:privilege><d:write-content/></d:privilege><d:privilege><d:unbind/></d:privilege>
      </d:current-user-privilege-set>
      <c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/calendars/user/sam%40example.com/tasks/</d:href>
    <d:propstat><d:prop>
      <d:displayname>Tasks</d:displayname>
      <d:resourcetype><d:collection/><c:calendar/></d:resourcetype>
      <c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""


class FakeResource:
    def __init__(self, server, cal_id, uid):
        self.server, self.cal_id, self.uid = server, cal_id, uid
        self.icalendar_instance = icalendar.Calendar.from_ical(server.store[cal_id][uid].to_ical())

    def delete(self):
        del self.server.store[self.cal_id][self.uid]


class FakeServer:
    def __init__(self, calendars):
        self.infos = calendars
        self.store = {cal["id"]: {} for cal in calendars}

    def calendars(self, refresh=False):
        return self.infos

    def put(self, cal_id, ical):
        uid = str(cli.master_of(ical)["UID"])
        self.store[cal_id][uid] = icalendar.Calendar.from_ical(ical.to_ical())
        return uid

    def search(self, info, start, end):
        return [
            component
            for ical in self.store[info["id"]].values()
            for component in recurring_ical_events.of(ical).between(start, end)
        ]

    def resource(self, info, uid):
        return FakeResource(self, info["id"], uid) if uid in self.store[info["id"]] else None

    def create(self, info, ical):
        self.put(info["id"], ical)

    def save(self, resource, ical):
        self.store[resource.cal_id][resource.uid] = icalendar.Calendar.from_ical(ical.to_ical())


def calendar(name, writable=True):
    return {"name": name, "id": name.lower(), "writable": writable, "url": f"https://dav.example/{name}/"}


def event(uid, title, start, end, **extra):
    ical = icalendar.Calendar()
    component = icalendar.Event()
    component.add("UID", uid)
    component.add("SUMMARY", title)
    component.add("DTSTART", start)
    component.add("DTEND", end)
    for key, value in extra.items():
        component.add(key.upper().replace("_", "-"), value)
    ical.add_component(component)
    return ical


def local(*parts):
    return datetime(*parts, tzinfo=ZONE)


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer([calendar("Sam"), calendar("Kid", writable=False)])
        # Swim is every Tuesday at 4 PM, starting Oct 6, 2026.
        self.server.put(
            "sam",
            event("swim", "Swim", local(2026, 10, 6, 16), local(2026, 10, 6, 17), rrule={"freq": "weekly"}),
        )

    def run_cli(self, *argv, server=None):
        args = cli.build_parser().parse_args(argv)
        return cli.run(args, server or self.server, ZONE)

    def events(self, *argv):
        return self.run_cli("events", *argv)["events"]

    def test_listing_reports_writable_event_calendars(self):
        calendars = cli.parse_calendar_listing(LISTING, "https://caldav.example/dav/calendars/user/sam%40example.com/")
        self.assertEqual(
            [(cal["name"], cal["id"], cal["writable"]) for cal in calendars],
            [("Sam", "abc-123", True), ("Kid", "kid@example.com.K1", False), ("Household", "house", True)],
        )
        self.assertEqual(calendars[0]["url"], "https://caldav.example/dav/calendars/user/sam%40example.com/abc-123/")

    def test_events_expand_recurrences_in_local_time(self):
        events = self.events("--from", "2026-10-12", "--days", "7")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start"], "2026-10-13T16:00-07:00")
        self.assertEqual(events[0]["occurrence"], "2026-10-13T16:00-07:00")
        self.assertTrue(events[0]["recurring"])

    def test_events_merge_an_event_on_two_calendars(self):
        shared = event("party", "Party", local(2026, 10, 10, 18), local(2026, 10, 10, 20))
        self.server.put("sam", shared)
        self.server.put("kid", shared)
        events = self.events("--from", "2026-10-10")
        self.assertEqual([(e["title"], e["calendars"]) for e in events], [("Party", ["Sam", "Kid"])])

    def test_all_day_end_is_the_last_day(self):
        self.server.put("kid", event("camp", "Camp", date(2026, 10, 12), date(2026, 10, 15)))
        [camp] = self.events("--from", "2026-10-12", "--calendar", "kid")
        self.assertEqual((camp["start"], camp["end"], camp["allDay"]), ("2026-10-12", "2026-10-14", True))

    def test_query_defaults_to_a_wide_window_and_skips_cancelled(self):
        self.server.put("kid", event("dentist", "Dentist", local(2027, 3, 2, 9), local(2027, 3, 2, 10)))
        self.server.put("kid", event("old", "Dentist", local(2027, 4, 2, 9), local(2027, 4, 2, 10), status="CANCELLED"))
        with patch.object(cli, "today", return_value=date(2026, 10, 8)):
            result = self.run_cli("events", "--query", "dentist")
        self.assertEqual((result["from"], result["to"]), ("2026-09-08", "2027-10-08"))
        self.assertEqual([e["start"] for e in result["events"]], ["2027-03-02T09:00-08:00"])

    def test_add_uses_the_only_writable_calendar_and_defaults_to_an_hour(self):
        added = self.run_cli("add", "--title", "Haircut", "--start", "2026-10-09 15:30")["added"]
        self.assertEqual(
            (added["calendars"], added["start"], added["end"]),
            (["Sam"], "2026-10-09T15:30-07:00", "2026-10-09T16:30-07:00"),
        )
        stored = self.server.store["sam"][added["id"]]
        self.assertTrue(stored.walk("VTIMEZONE"))

    def test_add_needs_a_calendar_when_several_are_writable(self):
        server = FakeServer([calendar("Sam"), calendar("Household")])
        with self.assertRaisesRegex(cli.CalendarError, "Pass --calendar"):
            self.run_cli("add", "--title", "X", "--start", "2026-10-09 15:00", server=server)
        added = self.run_cli("add", "--title", "X", "--start", "2026-10-09", "--calendar", "household", server=server)
        self.assertEqual(added["added"]["calendars"], ["Household"])

    def test_add_refuses_a_read_only_calendar(self):
        with self.assertRaisesRegex(cli.CalendarError, "Kid is read-only"):
            self.run_cli("add", "--title", "X", "--start", "2026-10-09", "--calendar", "Kid")

    def test_add_all_day_with_an_inclusive_end(self):
        added = self.run_cli("add", "--title", "Trip", "--start", "2026-10-16", "--end", "2026-10-18", "--all-day")
        component = cli.master_of(self.server.store["sam"][added["added"]["id"]])
        self.assertEqual((component.start, component.end), (date(2026, 10, 16), date(2026, 10, 19)))
        self.assertEqual(added["added"]["end"], "2026-10-18")

    def test_update_series_keeps_the_length_when_moving(self):
        self.run_cli("update", "swim", "--start", "2026-10-06 17:00", "--title", "Swim team")
        [swim] = self.events("--from", "2026-10-13")
        self.assertEqual(
            (swim["title"], swim["start"], swim["end"]),
            ("Swim team", "2026-10-13T17:00-07:00", "2026-10-13T18:00-07:00"),
        )

    def test_update_one_occurrence_leaves_the_rest(self):
        updated = self.run_cli("update", "swim", "--occurrence", "2026-10-13", "--start", "2026-10-14 10:00")
        self.assertEqual(updated["updated"]["occurrence"], "2026-10-13T16:00-07:00")
        week = self.events("--from", "2026-10-12", "--days", "14")
        self.assertEqual([e["start"] for e in week], ["2026-10-14T10:00-07:00", "2026-10-20T16:00-07:00"])

        # The moved occurrence is still named by its original date, and edits reuse its override.
        self.run_cli("update", "swim", "--occurrence", "2026-10-13", "--title", "Makeup swim")
        ical = self.server.store["sam"]["swim"]
        self.assertEqual(len(ical.walk("VEVENT")), 2)
        moved = self.events("--from", "2026-10-14")[0]
        self.assertEqual((moved["title"], moved["start"]), ("Makeup swim", "2026-10-14T10:00-07:00"))

    def test_delete_one_occurrence(self):
        deleted = self.run_cli("delete", "swim", "--occurrence", "2026-10-13T16:00-07:00")["deleted"]
        self.assertEqual(deleted["occurrence"], "2026-10-13T16:00-07:00")
        week = self.events("--from", "2026-10-12", "--days", "14")
        self.assertEqual([e["start"] for e in week], ["2026-10-20T16:00-07:00"])

    def test_delete_one_all_day_occurrence(self):
        self.server.put(
            "sam", event("trash", "Trash day", date(2026, 10, 5), date(2026, 10, 6), rrule={"freq": "weekly"})
        )
        self.run_cli("delete", "trash", "--occurrence", "2026-10-12")
        self.assertIn(b"EXDATE;VALUE=DATE:20261012", self.server.store["sam"]["trash"].to_ical())
        days = [e["start"] for e in self.events("--from", "2026-10-05", "--days", "15", "--query", "trash")]
        self.assertEqual(days, ["2026-10-05", "2026-10-19"])

    def test_delete_series(self):
        self.run_cli("delete", "swim")
        self.assertEqual(self.server.store["sam"], {})

    def test_occurrence_errors(self):
        self.server.put("sam", event("once", "Once", local(2026, 10, 9, 9), local(2026, 10, 9, 10)))
        with self.assertRaisesRegex(cli.CalendarError, "isn't recurring"):
            self.run_cli("delete", "once", "--occurrence", "2026-10-09")
        with self.assertRaisesRegex(cli.CalendarError, "no occurrence on 2026-10-14"):
            self.run_cli("delete", "swim", "--occurrence", "2026-10-14")
        with self.assertRaisesRegex(cli.CalendarError, "No event with ID nope"):
            self.run_cli("delete", "nope")

    def test_parsers(self):
        self.assertEqual(cli.parse_duration("1h30m"), timedelta(minutes=90))
        self.assertEqual(cli.parse_when("2026-10-09 15:30", ZONE), local(2026, 10, 9, 15, 30))
        with self.assertRaisesRegex(cli.CalendarError, "duration"):
            cli.parse_duration("soon")
        with self.assertRaisesRegex(cli.CalendarError, "YYYY-MM-DD"):
            cli.parse_when("next tuesday", ZONE)


if __name__ == "__main__":
    unittest.main()
