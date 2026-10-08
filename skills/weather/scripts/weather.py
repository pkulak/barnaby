#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TOMORROW_API_URL = "https://api.tomorrow.io/v4"
AIRFIRE_API_URL = "https://near-me.airfire.org/fasm/monitor"
CACHE_SECONDS = 600
USER_AGENT = "barnaby-weather/1"

WEATHER_CODES = {
    0: "Unknown",
    1000: "Clear",
    1001: "Cloudy",
    1100: "Mostly clear",
    1101: "Partly cloudy",
    1102: "Mostly cloudy",
    2000: "Fog",
    2100: "Light fog",
    4000: "Drizzle",
    4001: "Rain",
    4200: "Light rain",
    4201: "Heavy rain",
    5000: "Snow",
    5001: "Flurries",
    5100: "Light snow",
    5101: "Heavy snow",
    6000: "Freezing drizzle",
    6001: "Freezing rain",
    6200: "Light freezing rain",
    6201: "Heavy freezing rain",
    7000: "Ice pellets",
    7101: "Heavy ice pellets",
    7102: "Light ice pellets",
    8000: "Thunderstorm",
}

EVENT_INSIGHTS = [
    "air",
    "fires",
    "wind",
    "winter",
    "thunderstorms",
    "floods",
    "temperature",
    "tropical",
    "marine",
    "fog",
    "tornado",
    "special",
]


class WeatherError(RuntimeError):
    pass


CURRENT_FIELDS = [
    "temperature",
    "feelsLike",
    "humidity",
    "dewPoint",
    "pressureSurfaceLevel",
    "precipitationProbability",
    "rainIntensity",
    "snowIntensity",
    "sleetIntensity",
    "freezingRainIntensity",
    "uvIndex",
    "cloudCover",
    "visibility",
    "windSpeed",
    "windGust",
    "windDirection",
    "weatherCode",
    "weatherDescription",
]

# The realtime fields that say whether, and what, it's precipitating now.
PRECIPITATION_NOW_FIELDS = ["rainIntensity", "snowIntensity", "sleetIntensity", "freezingRainIntensity"]

TOMORROW_CURRENT_MAP = {
    "temperature": ("temperature", 1),
    "feelsLike": ("temperatureApparent", 1),
    "humidity": ("humidity", 0),
    "dewPoint": ("dewPoint", 1),
    "pressureSurfaceLevel": ("pressureSurfaceLevel", 2),
    "precipitationProbability": ("precipitationProbability", 0),
    "rainIntensity": ("rainIntensity", 2),
    "snowIntensity": ("snowIntensity", 2),
    "sleetIntensity": ("sleetIntensity", 2),
    "freezingRainIntensity": ("freezingRainIntensity", 2),
    "uvIndex": ("uvIndex", 1),
    "cloudCover": ("cloudCover", 0),
    "visibility": ("visibility", 2),
    "windSpeed": ("windSpeed", 2),
    "windGust": ("windGust", 2),
    "windDirection": ("windDirection", 0),
}

# Forecast fields Tomorrow.io trims as the horizon extends: later periods can omit
# them. The first period is still required to carry every field so a genuine
# permission or contract breakage fails loudly.
HOURLY_FORECAST_MAP = {
    "temperature": ("temperature", 1),
    "feelsLike": ("temperatureApparent", 1),
    "humidity": ("humidity", 0),
    "dewPoint": ("dewPoint", 1),
    "precipitationProbability": ("precipitationProbability", 0),
    "rainIntensity": ("rainIntensity", 2),
    "snowIntensity": ("snowIntensity", 2),
    "sleetIntensity": ("sleetIntensity", 2),
    "freezingRainIntensity": ("freezingRainIntensity", 2),
    "rainAccumulation": ("rainAccumulation", 2),
    "snowAccumulation": ("snowAccumulation", 2),
    "sleetAccumulation": ("sleetAccumulation", 2),
    "iceAccumulation": ("iceAccumulation", 2),
    "snowDepth": ("snowDepth", 2),
    "pressureSurfaceLevel": ("pressureSurfaceLevel", 2),
    "visibility": ("visibility", 2),
    "uvIndex": ("uvIndex", 1),
    "cloudCover": ("cloudCover", 0),
    "windSpeed": ("windSpeed", 2),
    "windGust": ("windGust", 2),
    "windDirection": ("windDirection", 0),
}

DAILY_FORECAST_MAP = {
    "temperatureHigh": ("temperatureMax", 1),
    "temperatureOvernightLow": ("temperatureMin", 1),
    "feelsLikeHigh": ("temperatureApparentMax", 1),
    "feelsLikeLow": ("temperatureApparentMin", 1),
    "humidityAverage": ("humidityAvg", 0),
    "precipitationProbabilityMaximum": ("precipitationProbabilityMax", 0),
    "rainAccumulation": ("rainAccumulationSum", 2),
    "snowAccumulation": ("snowAccumulationSum", 2),
    "sleetAccumulation": ("sleetAccumulationSum", 2),
    "iceAccumulation": ("iceAccumulationSum", 2),
    "uvIndexMaximum": ("uvIndexMax", 1),
    "cloudCoverAverage": ("cloudCoverAvg", 0),
    "visibilityAverage": ("visibilityAvg", 2),
    "windSpeedAverage": ("windSpeedAvg", 2),
    "windSpeedMaximum": ("windSpeedMax", 2),
    "windGustMaximum": ("windGustMax", 2),
    "windDirectionAverage": ("windDirectionAvg", 0),
}

HOURLY_FIELDS = [*HOURLY_FORECAST_MAP, "weatherCode", "weatherDescription"]
DAILY_FIELDS = [
    *DAILY_FORECAST_MAP,
    "weatherCode",
    "weatherDescription",
    "sunriseTime",
    "sunsetTime",
]


def check_fields(fields: list[str] | None, supported: list[str], kind: str) -> None:
    unknown = sorted(set(fields or []) - set(supported))
    if unknown:
        raise WeatherError(f"Unsupported {kind} fields: {', '.join(unknown)}. Supported: {', '.join(supported)}.")


def select_fields(period: dict[str, Any], fields: list[str] | None, always: tuple[str, ...]) -> dict[str, Any]:
    if not fields:
        return period
    return {key: period[key] for key in [*always, *fields] if key in period}


@dataclass(frozen=True)
class Location:
    id: str
    name: str
    latitude: float
    longitude: float
    zone: tzinfo | None = None

    @property
    def query(self) -> str:
        return f"{self.latitude},{self.longitude}"

    def output(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "latitude": round(self.latitude, 4),
            "longitude": round(self.longitude, 4),
        }
        if self.zone:
            result["timezone"] = str(self.zone)
        return result


class Cache:
    def __init__(self, directory: Path | None = None, ttl: int = CACHE_SECONDS):
        if directory is None:
            cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
            directory = cache_root / "barnaby-weather"
        self.directory = directory
        self.ttl = ttl
        self.disabled = os.environ.get("WEATHER_DISABLE_CACHE") == "1"

    def remember(self, namespace: str, key: Any, load: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if self.disabled:
            return load()

        digest = hashlib.sha256(json.dumps(key, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        path = self.directory / f"{namespace}-{digest}.json"
        try:
            age = datetime.now().timestamp() - path.stat().st_mtime
            if age <= self.ttl:
                return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            try:
                path.unlink()
            except OSError:
                pass

        result = load()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=self.directory, delete=False) as temporary:
                json.dump(result, temporary, separators=(",", ":"))
                temporary.write("\n")
                temporary_path = Path(temporary.name)
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
        except OSError:
            pass
        return result


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise WeatherError(f'Invalid timestamp: "{value}".') from error
    if result.tzinfo is None:
        raise WeatherError(f'Timestamp has no timezone: "{value}".')
    return result


def rounded(value: Any, precision: int | None) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise WeatherError(f'Expected a number, got "{value}".') from error
    if precision == 0:
        return round(number)
    if precision is None:
        return number
    return round(number, precision)


def request_json(
    provider: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode()
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, encoded, headers=request_headers, method=method)
    try:
        # Tomorrow.io allows only a few requests a second, and a summary makes several.
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = response.read()
                break
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == 2:
                    raise
                time.sleep(attempt + 1)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace").strip()
        try:
            message = json.loads(detail).get("message", detail)
        except json.JSONDecodeError:
            message = detail
        extras = []
        correlation_id = error.headers.get("X-Correlation-ID")
        retry_after = error.headers.get("Retry-After")
        if correlation_id:
            extras.append(f"correlation ID {correlation_id}")
        if retry_after:
            extras.append(f"retry after {retry_after}")
        suffix = f" ({', '.join(extras)})" if extras else ""
        raise WeatherError(f"{provider} returned HTTP {error.code}: {message[:500]}{suffix}") from error
    except urllib.error.URLError as error:
        raise WeatherError(f"Could not reach {provider}: {error.reason}") from error
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as error:
        raise WeatherError(f"{provider} returned invalid JSON.") from error
    if not isinstance(result, dict):
        raise WeatherError(f"{provider} returned an unexpected response.")
    return result


class TomorrowClient:
    def __init__(self, cache: Cache) -> None:
        self.token = os.environ.get("TOMORROWIO_API_KEY")
        self.cache = cache

    def headers(self) -> dict[str, str]:
        if not self.token:
            raise WeatherError("TOMORROWIO_API_KEY is not set.")
        return {"apikey": self.token}

    def _get(self, path: str, params: dict[str, Any], *, cached: bool = False) -> dict[str, Any]:
        url = f"{TOMORROW_API_URL}{path}?{urllib.parse.urlencode(params)}"

        def load() -> dict[str, Any]:
            return request_json("Tomorrow.io", url, headers=self.headers())

        if cached:
            namespace = f"tomorrow-{path.strip('/').replace('/', '-')}"
            return self.cache.remember(namespace, params, load)
        return load()

    def realtime(self, location: str) -> dict[str, Any]:
        return self._get("/weather/realtime", {"location": location, "units": "imperial"}, cached=True)

    def forecast(self, location: str, timestep: str) -> dict[str, Any]:
        params = {"location": location, "units": "imperial", "timesteps": timestep}
        return self._get("/weather/forecast", params, cached=True)

    def timeline(self, location: str, fields: list[str], timestep: str, hours: int) -> dict[str, Any]:
        body = {
            "location": location,
            "fields": fields,
            "units": "imperial",
            "timesteps": [timestep],
            "startTime": "now",
            "endTime": f"nowPlus{hours}h",
            "timezone": "auto",
        }
        return self.cache.remember(
            "tomorrow-timeline",
            body,
            lambda: request_json(
                "Tomorrow.io",
                f"{TOMORROW_API_URL}/timelines",
                headers=self.headers(),
                method="POST",
                body=body,
            ),
        )

    def events(self, location: Location) -> dict[str, Any]:
        body = {
            "location": [location.latitude, location.longitude],
            "insights": EVENT_INSIGHTS,
            "buffer": 1,
        }
        return self.cache.remember(
            "tomorrow-events",
            body,
            lambda: request_json(
                "Tomorrow.io",
                f"{TOMORROW_API_URL}/events",
                headers=self.headers(),
                method="POST",
                body=body,
            ),
        )


class AirQualityClient:
    def __init__(self, cache: Cache) -> None:
        self.cache = cache

    def current(self, latitude: float, longitude: float) -> dict[str, Any]:
        params = {
            "lat": latitude,
            "lng": longitude,
            "maxDistanceMiles": 20,
            "limit": 10,
        }
        url = f"{AIRFIRE_API_URL}?{urllib.parse.urlencode(params)}"
        return self.cache.remember(
            "airfire-air-quality",
            params,
            lambda: request_json("AirFire", url),
        )


def validate_no_warnings(payload: dict[str, Any], operation: str) -> None:
    warnings = payload.get("warnings")
    if warnings:
        raise WeatherError(f"Tomorrow.io returned warnings for {operation}: {warnings}")


def tomorrow_value(values: dict[str, Any], key: str, precision: int | None) -> Any:
    if key not in values or values[key] is None:
        raise WeatherError(f"Tomorrow.io did not return {key}.")
    return rounded(values[key], precision)


def nullable_tomorrow_value(values: dict[str, Any], key: str, precision: int | None) -> Any:
    if key not in values:
        raise WeatherError(f"Tomorrow.io did not return {key}.")
    if values[key] is None:
        return None
    return rounded(values[key], precision)


def forecast_value(values: dict[str, Any], key: str, precision: int | None) -> Any:
    """Return a forecast field, or None when the provider omits it for this period.

    Tomorrow.io trims its forecast field set as the horizon extends, so a field
    present for near periods can be absent later. Absence is unavailable data
    here, not a broken contract.
    """
    value = values.get(key)
    if value is None:
        return None
    return rounded(value, precision)


def require_forecast_fields(
    period: dict[str, Any], field_map: dict[str, tuple[str, int | None]], operation: str
) -> None:
    values = period.get("values", {})
    missing = sorted(provider_field for provider_field, _ in field_map.values() if provider_field not in values)
    if missing:
        raise WeatherError(f"Tomorrow.io {operation} omitted {', '.join(missing)} from its first period.")


def weather_description(code: Any) -> str:
    numeric = int(rounded(code, 0))
    return WEATHER_CODES.get(numeric, "Unknown")


def parse_coordinates(value: str) -> tuple[float, float] | None:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        return None
    try:
        latitude, longitude = (float(part) for part in parts)
    except ValueError:
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise WeatherError("Coordinates are outside the valid latitude/longitude range.")
    return latitude, longitude


def provider_location_query(requested: str) -> str:
    if parse_coordinates(requested):
        return requested
    return " ".join(requested.replace(",", " ").split())


def local_zone() -> tzinfo | None:
    """The time zone `date` uses: TZ, or else the zone /etc/localtime links to."""
    name = os.environ.get("TZ", "").removeprefix(":")
    if not name:
        name = str(Path("/etc/localtime").resolve()).partition("zoneinfo/")[2]
    try:
        return ZoneInfo(name) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def home_from_env() -> Location | None:
    value = os.environ.get("WEATHER_HOME", "").strip()
    if not value:
        return None
    coordinates = parse_coordinates(value)
    if not coordinates:
        raise WeatherError("WEATHER_HOME must be latitude,longitude.")
    return Location("home", "Home", *coordinates, zone=local_zone())


def normalized_location(requested: str, provider_location: dict[str, Any]) -> Location:
    try:
        latitude = float(provider_location["lat"])
        longitude = float(provider_location["lon"])
    except (KeyError, TypeError, ValueError) as error:
        raise WeatherError("Tomorrow.io did not return resolved coordinates.") from error
    return Location(
        id="remote",
        name=provider_location.get("name") or requested,
        latitude=latitude,
        longitude=longitude,
    )


class WeatherService:
    def __init__(
        self,
        tomorrow: Any,
        air_quality: Any,
        *,
        home: Location | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.home = home
        self.tomorrow = tomorrow
        self.air_quality_client = air_quality
        self.now = now or (lambda: datetime.now(timezone.utc))
        self._locations: dict[str, Location] = {}

    def generated_at(self) -> str:
        return self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def home_location(self) -> Location:
        if self.home is None:
            raise WeatherError(
                "WEATHER_HOME is not set, so there is no home location. "
                "Pass --location with a place, ZIP code, or latitude,longitude."
            )
        return self.home

    def provider_query(self, requested: str) -> str:
        if requested == "home":
            return self.home_location().query
        return provider_location_query(requested)

    def resolve_location(self, requested: str) -> Location:
        if requested == "home":
            return self.home_location()
        if requested in self._locations:
            return self._locations[requested]
        coordinates = parse_coordinates(requested)
        if coordinates:
            location = Location("remote", requested, *coordinates)
        else:
            payload = self.tomorrow.realtime(provider_location_query(requested))
            validate_no_warnings(payload, "location lookup")
            location = normalized_location(requested, payload.get("location", {}))
        self._locations[requested] = location
        return location

    def normalize_tomorrow_current(self, payload: dict[str, Any], fields: set[str]) -> tuple[dict[str, Any], str]:
        validate_no_warnings(payload, "realtime weather")
        data = payload.get("data", {})
        raw_values = data.get("values", {})
        observed = data.get("time")
        if not observed:
            raise WeatherError("Tomorrow.io realtime weather has no observation time.")
        values: dict[str, Any] = {}
        for field in fields:
            if field in TOMORROW_CURRENT_MAP:
                provider_field, precision = TOMORROW_CURRENT_MAP[field]
                if field == "windDirection":
                    values[field] = nullable_tomorrow_value(raw_values, provider_field, precision)
                else:
                    values[field] = tomorrow_value(raw_values, provider_field, precision)
            elif field in {"weatherCode", "weatherDescription"}:
                code = tomorrow_value(raw_values, "weatherCode", 0)
                values["weatherCode"] = code
                values["weatherDescription"] = weather_description(code)
        return values, observed

    def current(self, requested_location: str = "home", fields: list[str] | None = None) -> dict[str, Any]:
        check_fields(fields, CURRENT_FIELDS, "current")
        selected = set(fields or CURRENT_FIELDS)
        if selected & {"weatherCode", "weatherDescription"}:
            selected.update({"weatherCode", "weatherDescription"})
        payload = self.tomorrow.realtime(self.provider_query(requested_location))
        values, observed = self.normalize_tomorrow_current(payload, selected)
        location = self.payload_location(requested_location, payload)
        return {
            "location": location.output(),
            "units": "us",
            "generatedAt": self.generated_at(),
            "source": "tomorrow_io",
            "current": {"values": values, "observedAt": observed},
        }

    def payload_location(self, requested_location: str, payload: dict[str, Any]) -> Location:
        if requested_location == "home":
            return self.home_location()
        location = normalized_location(requested_location, payload.get("location", {}))
        self._locations[requested_location] = location
        return location

    def hourly(
        self,
        requested_location: str = "home",
        hours: int = 24,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= hours <= 120:
            raise WeatherError("Hourly forecast hours must be between 1 and 120.")
        check_fields(fields, HOURLY_FIELDS, "hourly")
        payload = self.tomorrow.forecast(self.provider_query(requested_location), "1h")
        validate_no_warnings(payload, "hourly forecast")
        raw = payload.get("timelines", {}).get("hourly", [])
        if len(raw) < hours:
            raise WeatherError(f"Tomorrow.io returned {len(raw)} hourly periods; {hours} were requested.")
        require_forecast_fields(raw[0], HOURLY_FORECAST_MAP, "hourly forecast")
        location = self.payload_location(requested_location, payload)
        periods = [
            select_fields(self.normalize_hour(period, location.zone), fields, ("time", "localTime"))
            for period in raw[:hours]
        ]
        return {
            "location": location.output(),
            "units": "us",
            "generatedAt": self.generated_at(),
            "source": "tomorrow_io",
            "hourly": periods,
        }

    def normalize_hour(self, period: dict[str, Any], zone: tzinfo | None = None) -> dict[str, Any]:
        values = period.get("values", {})
        code = tomorrow_value(values, "weatherCode", 0)
        result: dict[str, Any] = {"time": period.get("time")}
        if zone and result["time"]:
            result["localTime"] = parse_time(result["time"]).astimezone(zone).isoformat()
        for field, (provider_field, precision) in HOURLY_FORECAST_MAP.items():
            result[field] = forecast_value(values, provider_field, precision)
        result["weatherCode"] = code
        result["weatherDescription"] = weather_description(code)
        return result

    def daily(
        self,
        requested_location: str = "home",
        days: int = 5,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= days <= 7:
            raise WeatherError("Daily forecast days must be between 1 and 7.")
        check_fields(fields, DAILY_FIELDS, "daily")
        payload = self.tomorrow.forecast(self.provider_query(requested_location), "1d")
        validate_no_warnings(payload, "daily forecast")
        raw = payload.get("timelines", {}).get("daily", [])
        if not raw:
            raise WeatherError("Tomorrow.io returned no daily periods.")
        require_forecast_fields(raw[0], DAILY_FORECAST_MAP, "daily forecast")
        periods = [select_fields(self.normalize_day(period), fields, ("date", "startTime")) for period in raw[:days]]
        location = self.payload_location(requested_location, payload)
        return {
            "location": location.output(),
            "units": "us",
            "generatedAt": self.generated_at(),
            "source": "tomorrow_io",
            "dailyBoundary": "06:00 local time",
            "daily": periods,
        }

    def normalize_day(self, period: dict[str, Any]) -> dict[str, Any]:
        values = period.get("values", {})
        code = tomorrow_value(values, "weatherCodeMax", 0)
        start_time = period.get("time")
        if not start_time:
            raise WeatherError("Tomorrow.io returned a daily period without a time.")
        result: dict[str, Any] = {"date": start_time[:10], "startTime": start_time}
        for field, (provider_field, precision) in DAILY_FORECAST_MAP.items():
            result[field] = forecast_value(values, provider_field, precision)
        result["weatherCode"] = code
        result["weatherDescription"] = weather_description(code)
        result["sunriseTime"] = values.get("sunriseTime")
        result["sunsetTime"] = values.get("sunsetTime")
        return result

    def precipitation(self, requested_location: str = "home", hours: int = 6) -> dict[str, Any]:
        if not 1 <= hours <= 6:
            raise WeatherError("Precipitation forecast hours must be between 1 and 6.")
        location = self.resolve_location(requested_location)
        fields = ["precipitationProbability", *PRECIPITATION_NOW_FIELDS]
        payload = self.tomorrow.timeline(location.query, fields, "5m", hours)
        validate_no_warnings(payload, "precipitation forecast")
        timelines = payload.get("data", {}).get("timelines", [])
        if len(timelines) != 1 or timelines[0].get("timestep") != "5m":
            raise WeatherError("Tomorrow.io returned an unexpected precipitation timeline.")
        intervals = [self.normalize_precipitation_interval(interval) for interval in timelines[0].get("intervals", [])]
        if not intervals:
            raise WeatherError("Tomorrow.io returned no precipitation intervals.")
        return {
            "location": location.output(),
            "units": "us",
            "generatedAt": self.generated_at(),
            "source": "tomorrow_io",
            "current": self.current(requested_location, PRECIPITATION_NOW_FIELDS)["current"],
            "maximumProbability": max(interval["precipitationProbability"] for interval in intervals),
            "nextLikelyWindow": likely_precipitation_window(intervals),
            "forecast": intervals,
        }

    def normalize_precipitation_interval(self, interval: dict[str, Any]) -> dict[str, Any]:
        values = interval.get("values", {})
        start_time = interval.get("startTime")
        if not start_time:
            raise WeatherError("Tomorrow.io returned a precipitation interval without a time.")
        return {
            "time": start_time,
            "precipitationProbability": tomorrow_value(values, "precipitationProbability", 0),
            "rainIntensity": tomorrow_value(values, "rainIntensity", 2),
            "snowIntensity": tomorrow_value(values, "snowIntensity", 2),
            "sleetIntensity": tomorrow_value(values, "sleetIntensity", 2),
            "freezingRainIntensity": tomorrow_value(values, "freezingRainIntensity", 2),
        }

    def air_quality(self, requested_location: str = "home") -> dict[str, Any]:
        location = self.resolve_location(requested_location)
        payload = self.air_quality_client.current(location.latitude, location.longitude)
        monitors = [
            monitor
            for monitor in payload.get("purpleAir", [])
            if monitor.get("status") == 0
            and isinstance(monitor.get("aqi"), (int, float))
            and isinstance(monitor.get("nowcast"), (int, float))
            and isinstance(monitor.get("distanceMiles"), (int, float))
        ]
        if not monitors:
            raise WeatherError("No PurpleAir monitor within 20 miles has a current reading.")
        monitor = min(monitors, key=lambda item: item["distanceMiles"])
        aqi = round(float(monitor["aqi"]))
        return {
            "location": location.output(),
            "units": "us",
            "generatedAt": self.generated_at(),
            "source": "PurpleAir via AirFire",
            "current": {
                "aqi": aqi,
                "category": aqi_category(aqi),
                "pm25": round(float(monitor["nowcast"]), 1),
                "pm25Unit": "µg/m³",
                "observedAt": monitor.get("local_ts") or monitor.get("utc_ts"),
                "sensorId": str(monitor.get("unit_id")),
                "distanceMiles": round(float(monitor["distanceMiles"]), 2),
            },
        }

    def events(self, requested_location: str = "home") -> dict[str, Any]:
        location = self.resolve_location(requested_location)
        payload = self.tomorrow.events(location)
        validate_no_warnings(payload, "severe weather events")
        raw_events = payload.get("data", {}).get("events")
        if not isinstance(raw_events, list):
            raise WeatherError("Tomorrow.io returned an unexpected events response.")
        return {
            "location": location.output(),
            "units": "us",
            "generatedAt": self.generated_at(),
            "source": "tomorrow_io",
            "events": [normalize_event(event) for event in raw_events],
        }

    def summary(self, requested_location: str = "home") -> dict[str, Any]:
        current = self.current(requested_location, ["temperature"])
        daily = self.daily(requested_location, 2 if requested_location == "home" else 1)
        if requested_location == "home":
            today_date = self.now().astimezone(self.home_location().zone).date().isoformat()
            today = next((day for day in daily["daily"] if day["date"] == today_date), None)
            if today is None:
                raise WeatherError("Tomorrow.io did not return today's forecast.")
        else:
            today = daily["daily"][0]
        precipitation = self.precipitation(requested_location, 6)
        try:
            air_quality = self.air_quality(requested_location)["current"]
        except WeatherError:
            air_quality = None
        events = self.events(requested_location)
        return {
            "location": daily["location"],
            "units": "us",
            "generatedAt": self.generated_at(),
            "current": current["current"],
            "today": today,
            "precipitation": {
                "current": precipitation["current"],
                "maximumProbability": precipitation["maximumProbability"],
                "nextLikelyWindow": precipitation["nextLikelyWindow"],
            },
            "airQuality": air_quality,
            "events": events["events"],
        }


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    values = event.get("eventValues") or event.get("conditionValues") or {}
    return {
        "insight": event.get("insight"),
        "title": values.get("title"),
        "origin": values.get("origin"),
        "severity": event.get("severity"),
        "certainty": event.get("certainty"),
        "urgency": event.get("urgency"),
        "startTime": event.get("startTime"),
        "endTime": event.get("endTime"),
        "updateTime": event.get("updateTime"),
        "description": values.get("description"),
        "instruction": values.get("instruction"),
        "distance": values.get("distance"),
        "direction": values.get("direction"),
    }


def likely_precipitation_window(intervals: list[dict[str, Any]], threshold: int = 30) -> dict[str, Any] | None:
    start = None
    selected: list[dict[str, Any]] = []
    for index, interval in enumerate(intervals):
        if interval["precipitationProbability"] >= threshold:
            if start is None:
                start = index
            selected.append(interval)
        elif start is not None:
            break
    if start is None:
        return None

    last_time = parse_time(selected[-1]["time"]) + timedelta(minutes=5)
    intensity_fields = {
        "rain": "rainIntensity",
        "snow": "snowIntensity",
        "sleet": "sleetIntensity",
        "freezing_rain": "freezingRainIntensity",
    }
    maxima = {kind: max(interval[field] for interval in selected) for kind, field in intensity_fields.items()}
    kind = max(maxima, key=maxima.get)
    if maxima[kind] == 0:
        kind = "unknown"
    return {
        "startTime": selected[0]["time"],
        "endTime": last_time.isoformat(),
        "type": kind,
        "maximumProbability": max(interval["precipitationProbability"] for interval in selected),
        "maximumIntensity": max(maxima.values()),
    }


def aqi_category(aqi: int) -> str:
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "Unhealthy for sensitive groups"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very unhealthy"
    return "Hazardous"


def bounded(value: str, minimum: int, maximum: int, label: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{label} must be a whole number.") from error
    if not minimum <= number <= maximum:
        raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}.")
    return number


def parse_fields(value: str) -> list[str]:
    fields = list(dict.fromkeys(field.strip() for field in value.split(",") if field.strip()))
    if not fields:
        raise argparse.ArgumentTypeError("--fields must name at least one field.")
    return fields


def add_location(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--location",
        default="home",
        help='"home" (WEATHER_HOME), a place, a ZIP code, or latitude,longitude',
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Current conditions, forecasts, AQI, and severe-weather events.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    current = subparsers.add_parser("current", help="Get current conditions.")
    add_location(current)
    current.add_argument(
        "--fields",
        type=parse_fields,
        help="Comma-separated fields to keep.",
    )

    hourly = subparsers.add_parser("hourly", help="Get an hourly forecast.")
    add_location(hourly)
    hourly.add_argument("--hours", type=lambda value: bounded(value, 1, 120, "hours"), default=24)
    hourly.add_argument("--fields", type=parse_fields, help="Comma-separated fields to keep for each hour.")

    daily = subparsers.add_parser("daily", help="Get a daily forecast.")
    add_location(daily)
    daily.add_argument("--days", type=lambda value: bounded(value, 1, 7, "days"), default=5)
    daily.add_argument("--fields", type=parse_fields, help="Comma-separated fields to keep for each day.")

    precipitation = subparsers.add_parser("precipitation", help="Get current and near-term precipitation.")
    add_location(precipitation)
    precipitation.add_argument("--hours", type=lambda value: bounded(value, 1, 6, "hours"), default=6)

    air_quality = subparsers.add_parser("air-quality", help="Get current nearby PurpleAir AQI.")
    add_location(air_quality)

    events = subparsers.add_parser("events", help="Get active severe-weather events.")
    add_location(events)

    summary = subparsers.add_parser("summary", help="Get compact current and day-ahead weather data.")
    add_location(summary)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    cache = Cache()
    service = WeatherService(TomorrowClient(cache), AirQualityClient(cache), home=home_from_env())
    if args.command == "current":
        result = service.current(args.location, args.fields)
    elif args.command == "hourly":
        result = service.hourly(args.location, args.hours, args.fields)
    elif args.command == "daily":
        result = service.daily(args.location, args.days, args.fields)
    elif args.command == "precipitation":
        result = service.precipitation(args.location, args.hours)
    elif args.command == "air-quality":
        result = service.air_quality(args.location)
    elif args.command == "events":
        result = service.events(args.location)
    elif args.command == "summary":
        result = service.summary(args.location)
    else:
        raise WeatherError(f"Unsupported command: {args.command}")
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except (WeatherError, KeyError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
