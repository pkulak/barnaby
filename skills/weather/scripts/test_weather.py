#!/usr/bin/env python3

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch

import weather

NOW = datetime(2026, 9, 21, 23, 40, tzinfo=timezone.utc)
REPORTED = "2026-09-21T23:39:00+00:00"
HOME = weather.Location("home", "Home", 45.568, -122.632, zone=ZoneInfo("America/Los_Angeles"))

HOURLY_PROVIDER_VALUES = {
    "weatherCode": 1101,
    "temperature": 70.85,
    "temperatureApparent": 70.12,
    "humidity": 63.4,
    "dewPoint": 57.53,
    "precipitationProbability": 10,
    "rainIntensity": 0,
    "snowIntensity": 0,
    "sleetIntensity": 0,
    "freezingRainIntensity": 0,
    "rainAccumulation": 0,
    "snowAccumulation": 0,
    "sleetAccumulation": 0,
    "iceAccumulation": 0,
    "snowDepth": None,
    "pressureSurfaceLevel": 29.88,
    "visibility": 9.9,
    "uvIndex": 1.0,
    "cloudCover": 62,
    "windSpeed": 0.6,
    "windGust": 6.7,
    "windDirection": 324,
}

DAILY_PROVIDER_VALUES = {
    "weatherCodeMax": 1101,
    "temperatureMax": 70.0,
    "temperatureMin": 48.0,
    "temperatureApparentMax": 70.0,
    "temperatureApparentMin": 48.0,
    "humidityAvg": 60.0,
    "precipitationProbabilityMax": 5,
    "rainAccumulationSum": 0,
    "snowAccumulationSum": 0,
    "sleetAccumulationSum": 0,
    "iceAccumulationSum": 0,
    "uvIndexMax": 4.0,
    "cloudCoverAvg": 40,
    "visibilityAvg": 9.9,
    "windSpeedAvg": 3.0,
    "windSpeedMax": 9.0,
    "windGustMax": 17.0,
    "windDirectionAvg": 300,
    "sunriseTime": "2026-09-21T06:57:00-07:00",
    "sunsetTime": "2026-09-21T19:08:00-07:00",
}


def forecast_payload(timestep, periods):
    return {"timelines": {timestep: [{"time": time, "values": values} for time, values in periods]}}


class FakeTomorrow:
    def __init__(self):
        self.realtime_calls = []
        self.forecast_calls = []
        self.timeline_calls = []
        self.event_calls = []
        self.hourly_payload = None
        self.daily_payload = None
        self.realtime_payload = {
            "data": {
                "time": REPORTED,
                "values": {
                    "temperature": 70.85,
                    "temperatureApparent": 70.12,
                    "humidity": 63.4,
                    "dewPoint": 57.53,
                    "pressureSurfaceLevel": 29.84,
                    "precipitationProbability": 10,
                    "rainIntensity": 0,
                    "snowIntensity": 0,
                    "sleetIntensity": 0,
                    "freezingRainIntensity": 0,
                    "uvIndex": 1.55,
                    "weatherCode": 1101,
                    "cloudCover": 40,
                    "visibility": 10,
                    "windSpeed": 7.123,
                    "windGust": 12.345,
                    "windDirection": 181.2,
                },
            },
            "location": {"lat": 45.568, "lon": -122.632},
        }
        self.timeline_payload = {
            "data": {
                "timelines": [
                    {
                        "timestep": "5m",
                        "intervals": [
                            {
                                "startTime": "2026-09-21T17:00:00-07:00",
                                "values": {
                                    "precipitationProbability": 0,
                                    "rainIntensity": 0,
                                    "snowIntensity": 0,
                                    "sleetIntensity": 0,
                                    "freezingRainIntensity": 0,
                                },
                            }
                        ],
                    }
                ]
            }
        }
        self.events_payload = {"data": {"events": []}}

    def realtime(self, location):
        self.realtime_calls.append(location)
        return self.realtime_payload

    def forecast(self, location, timestep):
        self.forecast_calls.append((location, timestep))
        payload = self.hourly_payload if timestep == "1h" else self.daily_payload
        if payload is None:
            raise AssertionError("Forecast was not expected in this test")
        return payload

    def timeline(self, location, fields, timestep, hours):
        self.timeline_calls.append((location, fields, timestep, hours))
        return self.timeline_payload

    def events(self, location):
        self.event_calls.append(location)
        return self.events_payload


class FakeAirQuality:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {"purpleAir": []}

    def current(self, latitude, longitude):
        self.calls.append((latitude, longitude))
        return self.payload


class WeatherServiceTest(unittest.TestCase):
    def service(self, tomorrow=None, air_quality=None, home=HOME, now=NOW):
        return weather.WeatherService(
            tomorrow or FakeTomorrow(),
            air_quality or FakeAirQuality(),
            home=home,
            now=lambda: now,
        )

    def test_home_current_uses_tomorrow_at_home_coordinates(self):
        tomorrow = FakeTomorrow()
        result = self.service(tomorrow).current("home", ["temperature", "windSpeed", "windDirection"])

        self.assertEqual(
            result["current"],
            {"values": {"temperature": 70.8, "windSpeed": 7.12, "windDirection": 181}, "observedAt": REPORTED},
        )
        self.assertEqual(result["location"]["timezone"], "America/Los_Angeles")
        self.assertEqual(tomorrow.realtime_calls, ["45.568,-122.632"])

    def test_weather_description_always_includes_the_code(self):
        result = self.service().current("home", ["weatherDescription"])
        self.assertEqual(result["current"]["values"]["weatherCode"], 1101)
        self.assertEqual(result["current"]["values"]["weatherDescription"], "Partly cloudy")

    def test_current_rejects_unknown_fields_before_fetching(self):
        tomorrow = FakeTomorrow()
        with self.assertRaisesRegex(weather.WeatherError, "Unsupported current fields: lightning"):
            self.service(tomorrow).current("home", ["lightning"])
        self.assertEqual(tomorrow.realtime_calls, [])

    def test_home_without_weather_home_asks_for_a_location(self):
        with self.assertRaisesRegex(weather.WeatherError, "WEATHER_HOME is not set.*--location"):
            self.service(home=None).current()

    def test_remote_temperature_uses_the_place_name(self):
        tomorrow = FakeTomorrow()
        result = self.service(tomorrow, home=None).current("Seattle, WA", ["temperature"])

        self.assertEqual(result["current"]["values"], {"temperature": 70.8})
        self.assertEqual(tomorrow.realtime_calls, ["Seattle WA"])

    def test_tomorrow_warning_fails_the_request(self):
        tomorrow = FakeTomorrow()
        tomorrow.realtime_payload["warnings"] = ["field unavailable"]
        with self.assertRaisesRegex(weather.WeatherError, "returned warnings"):
            self.service(tomorrow).current("home", ["windSpeed"])

    def test_precipitation_includes_realtime_intensities(self):
        tomorrow = FakeTomorrow()
        tomorrow.realtime_payload["data"]["values"]["rainIntensity"] = 0.034
        result = self.service(tomorrow).precipitation()

        self.assertEqual(
            result["current"]["values"],
            {"rainIntensity": 0.03, "snowIntensity": 0.0, "sleetIntensity": 0.0, "freezingRainIntensity": 0.0},
        )
        self.assertEqual(tomorrow.timeline_calls[0][0], "45.568,-122.632")

    def test_summary_picks_todays_local_forecast(self):
        tomorrow = FakeTomorrow()
        tomorrow.daily_payload = forecast_payload(
            "daily",
            [
                ("2026-09-20T13:00:00Z", {**DAILY_PROVIDER_VALUES, "temperatureMax": 65}),
                ("2026-09-21T13:00:00Z", DAILY_PROVIDER_VALUES),
            ],
        )
        air_quality = FakeAirQuality(
            {"purpleAir": [{"status": 0, "aqi": 20, "nowcast": 4, "distanceMiles": 0.4, "unit_id": 1}]}
        )
        result = self.service(tomorrow, air_quality, now=datetime(2026, 9, 21, 12, 43, tzinfo=timezone.utc)).summary()

        self.assertEqual(result["current"]["values"], {"temperature": 70.8})
        self.assertEqual(result["today"]["date"], "2026-09-21")
        self.assertEqual(result["today"]["temperatureHigh"], 70.0)
        self.assertEqual(result["precipitation"]["current"]["values"]["rainIntensity"], 0.0)
        self.assertEqual(result["airQuality"]["aqi"], 20)

    def test_summary_without_an_aqi_reading_still_succeeds(self):
        tomorrow = FakeTomorrow()
        tomorrow.daily_payload = forecast_payload("daily", [("2026-09-21T13:00:00Z", DAILY_PROVIDER_VALUES)])
        result = self.service(tomorrow).summary()

        self.assertIsNone(result["airQuality"])
        with self.assertRaisesRegex(weather.WeatherError, "within 20 miles"):
            self.service(tomorrow).air_quality()

    def test_home_comes_from_weather_home(self):
        with patch.dict("os.environ", {"WEATHER_HOME": "45.568, -122.632", "TZ": "America/Chicago"}):
            home = weather.home_from_env()
        self.assertEqual(home.query, "45.568,-122.632")
        self.assertEqual(str(home.zone), "America/Chicago")
        with patch.dict("os.environ", {"WEATHER_HOME": "Portland"}):
            with self.assertRaisesRegex(weather.WeatherError, "latitude,longitude"):
                weather.home_from_env()
        with patch.dict("os.environ", {"WEATHER_HOME": ""}):
            self.assertIsNone(weather.home_from_env())

    def test_air_quality_uses_nearest_valid_monitor(self):
        payload = {
            "purpleAir": [
                {
                    "status": 0,
                    "aqi": 90,
                    "nowcast": 30,
                    "distanceMiles": 4,
                    "unit_id": 1,
                    "local_ts": REPORTED,
                },
                {
                    "status": 1,
                    "aqi": 5,
                    "nowcast": 1,
                    "distanceMiles": 0.1,
                    "unit_id": 2,
                },
                {
                    "status": 0,
                    "aqi": 42,
                    "nowcast": 7.6,
                    "distanceMiles": 0.4,
                    "unit_id": 3,
                    "local_ts": REPORTED,
                },
            ]
        }
        air_quality = FakeAirQuality(payload)
        result = self.service(air_quality=air_quality).air_quality("home")

        self.assertEqual(result["current"]["aqi"], 42)
        self.assertEqual(result["current"]["category"], "Good")
        self.assertEqual(result["current"]["sensorId"], "3")
        self.assertEqual(air_quality.calls, [(45.568, -122.632)])

    def test_precipitation_window_skips_low_probability_intervals(self):
        intervals = [
            {
                "time": "2026-09-21T17:00:00-07:00",
                "precipitationProbability": 10,
                "rainIntensity": 0,
                "snowIntensity": 0,
                "sleetIntensity": 0,
                "freezingRainIntensity": 0,
            },
            {
                "time": "2026-09-21T17:05:00-07:00",
                "precipitationProbability": 40,
                "rainIntensity": 0.1,
                "snowIntensity": 0,
                "sleetIntensity": 0,
                "freezingRainIntensity": 0,
            },
            {
                "time": "2026-09-21T17:10:00-07:00",
                "precipitationProbability": 60,
                "rainIntensity": 0.2,
                "snowIntensity": 0,
                "sleetIntensity": 0,
                "freezingRainIntensity": 0,
            },
            {
                "time": "2026-09-21T17:15:00-07:00",
                "precipitationProbability": 20,
                "rainIntensity": 0,
                "snowIntensity": 0,
                "sleetIntensity": 0,
                "freezingRainIntensity": 0,
            },
        ]
        result = weather.likely_precipitation_window(intervals)
        self.assertEqual(result["startTime"], "2026-09-21T17:05:00-07:00")
        self.assertEqual(result["endTime"], "2026-09-21T17:15:00-07:00")
        self.assertEqual(result["type"], "rain")
        self.assertEqual(result["maximumProbability"], 60)

    def test_nullable_tomorrow_value_preserves_a_legitimate_null(self):
        self.assertIsNone(weather.nullable_tomorrow_value({"snowDepth": None}, "snowDepth", 2))
        with self.assertRaisesRegex(weather.WeatherError, "did not return snowDepth"):
            weather.nullable_tomorrow_value({}, "snowDepth", 2)

    def test_forecast_value_treats_absent_and_null_fields_as_unavailable(self):
        self.assertIsNone(weather.forecast_value({}, "sleetAccumulation", 2))
        self.assertIsNone(weather.forecast_value({"sleetAccumulation": None}, "sleetAccumulation", 2))
        self.assertEqual(
            weather.forecast_value({"sleetAccumulation": 0.126}, "sleetAccumulation", 2),
            0.13,
        )

    def test_hourly_forecast_allows_fields_dropped_farther_out(self):
        later = {**HOURLY_PROVIDER_VALUES}
        del later["sleetAccumulation"]
        tomorrow = FakeTomorrow()
        tomorrow.hourly_payload = forecast_payload(
            "hourly",
            [
                ("2026-09-21T23:00:00Z", HOURLY_PROVIDER_VALUES),
                ("2026-09-22T00:00:00Z", later),
            ],
        )
        result = self.service(tomorrow=tomorrow).hourly("home", 2)

        self.assertEqual(result["hourly"][0]["sleetAccumulation"], 0.0)
        self.assertIsNone(result["hourly"][1]["sleetAccumulation"])
        self.assertEqual(result["hourly"][1]["temperature"], 70.8)

    def test_hourly_forecast_keeps_requested_fields_and_local_time(self):
        tomorrow = FakeTomorrow()
        tomorrow.hourly_payload = forecast_payload("hourly", [("2026-09-21T23:00:00Z", HOURLY_PROVIDER_VALUES)])
        result = self.service(tomorrow=tomorrow).hourly("home", 1, ["temperature", "feelsLike"])

        self.assertEqual(
            set(result["hourly"][0]),
            {"time", "localTime", "temperature", "feelsLike"},
        )
        self.assertEqual(result["hourly"][0]["localTime"], "2026-09-21T16:00:00-07:00")

    def test_hourly_forecast_rejects_unknown_fields_before_fetching(self):
        tomorrow = FakeTomorrow()
        with self.assertRaisesRegex(weather.WeatherError, "temperatureApparent.*feelsLike"):
            self.service(tomorrow=tomorrow).hourly("home", 1, ["temperatureApparent"])
        self.assertEqual(tomorrow.forecast_calls, [])

    def test_daily_forecast_keeps_requested_fields(self):
        tomorrow = FakeTomorrow()
        tomorrow.daily_payload = forecast_payload("daily", [("2026-09-21T13:00:00Z", DAILY_PROVIDER_VALUES)])
        result = self.service(tomorrow=tomorrow).daily("home", 1, ["temperatureHigh"])

        self.assertEqual(set(result["daily"][0]), {"date", "startTime", "temperatureHigh"})

    def test_hourly_forecast_fails_when_the_first_period_is_missing_a_field(self):
        first = {**HOURLY_PROVIDER_VALUES}
        del first["sleetAccumulation"]
        tomorrow = FakeTomorrow()
        tomorrow.hourly_payload = forecast_payload("hourly", [("2026-09-21T23:00:00Z", first)])
        with self.assertRaisesRegex(weather.WeatherError, "sleetAccumulation"):
            self.service(tomorrow=tomorrow).hourly("home", 1)

    def test_daily_forecast_allows_fields_dropped_farther_out(self):
        later = {**DAILY_PROVIDER_VALUES}
        del later["sleetAccumulationSum"]
        del later["uvIndexMax"]
        tomorrow = FakeTomorrow()
        tomorrow.daily_payload = forecast_payload(
            "daily",
            [
                ("2026-09-21T13:00:00Z", DAILY_PROVIDER_VALUES),
                ("2026-09-22T13:00:00Z", later),
            ],
        )
        result = self.service(tomorrow=tomorrow).daily("home", 2)

        self.assertEqual(result["daily"][0]["sleetAccumulation"], 0.0)
        self.assertEqual(result["daily"][1]["date"], "2026-09-22")
        self.assertIsNone(result["daily"][1]["sleetAccumulation"])
        self.assertIsNone(result["daily"][1]["uvIndexMaximum"])

    def test_daily_forecast_returns_the_days_available(self):
        tomorrow = FakeTomorrow()
        tomorrow.daily_payload = forecast_payload("daily", [("2026-09-21T13:00:00Z", DAILY_PROVIDER_VALUES)])
        result = self.service(tomorrow=tomorrow).daily("home", 7)

        self.assertEqual([day["date"] for day in result["daily"]], ["2026-09-21"])

    def test_daily_forecast_fails_when_the_first_period_is_missing_a_field(self):
        first = {**DAILY_PROVIDER_VALUES}
        del first["sleetAccumulationSum"]
        tomorrow = FakeTomorrow()
        tomorrow.daily_payload = forecast_payload("daily", [("2026-09-21T13:00:00Z", first)])
        with self.assertRaisesRegex(weather.WeatherError, "sleetAccumulationSum"):
            self.service(tomorrow=tomorrow).daily("home", 1)

    def test_provider_location_query_preserves_coordinates(self):
        self.assertEqual(weather.provider_location_query("45.52,-122.68"), "45.52,-122.68")
        self.assertEqual(weather.provider_location_query("98101, US"), "98101 US")

    def test_parser_has_no_pollen_command(self):
        parser = weather.build_parser()
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                parser.parse_args(["pollen"])

    def test_field_parser_rejects_an_empty_list(self):
        with self.assertRaisesRegex(Exception, "at least one field"):
            weather.parse_fields(" , ")


if __name__ == "__main__":
    unittest.main()
