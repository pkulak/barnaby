---
name: weather
description: Weather, forecasts, precipitation timing, AQI, and alerts for home or elsewhere.
---

# Weather

Use the helper in this skill for every weather request:

```bash
cd "$BARNABY_PI_SKILLS_DIR/weather"
python3 scripts/weather.py <command>
```

It returns normalized JSON in US units. A nonzero exit means the request failed. Report the useful error and do not substitute remembered weather, another provider, or stale data.

Weather and forecasts come from Tomorrow.io. AQI comes from the nearest PurpleAir monitor, through AirFire.

Every command defaults to `--location home`. If home isn't configured, the helper says so; pass the place you're in instead (see [Other locations](#other-locations)).

## Current conditions

For a question about one or two measurements, request only those fields:

```bash
python3 scripts/weather.py current --fields temperature
python3 scripts/weather.py current --fields windSpeed,windGust,windDirection
```

Current fields: `temperature`, `feelsLike`, `humidity`, `dewPoint`, `pressureSurfaceLevel`, `precipitationProbability`, `rainIntensity`, `snowIntensity`, `sleetIntensity`, `freezingRainIntensity`, `uvIndex`, `cloudCover`, `visibility`, `windSpeed`, `windGust`, `windDirection`, `weatherCode`, `weatherDescription`

Use an unfiltered request for broad questions such as "What's it like outside?":

```bash
python3 scripts/weather.py current
```

## Forecasts

Get the next 24 hours by default:

```bash
python3 scripts/weather.py hourly
python3 scripts/weather.py hourly --hours 48 --fields temperature,feelsLike,precipitationProbability
```

Each hour has a UTC `time`. For home forecasts it also has `localTime`; use that to pick out hours such as "tomorrow afternoon".

Get five forecast days by default:

```bash
python3 scripts/weather.py daily
python3 scripts/weather.py daily --days 6 --fields temperatureHigh,temperatureOvernightLow,weatherDescription
```

Use `--fields` to keep only what the question needs instead of filtering the JSON yourself. Hours always keep `time` and `localTime`; days always keep `date` and `startTime`. An unknown field name fails and lists the supported ones.

- Hourly fields: `temperature`, `feelsLike`, `humidity`, `dewPoint`, `precipitationProbability`, `rainIntensity`, `snowIntensity`, `sleetIntensity`, `freezingRainIntensity`, `rainAccumulation`, `snowAccumulation`, `sleetAccumulation`, `iceAccumulation`, `snowDepth`, `pressureSurfaceLevel`, `visibility`, `uvIndex`, `cloudCover`, `windSpeed`, `windGust`, `windDirection`, `weatherCode`, `weatherDescription`
- Daily fields: `temperatureHigh`, `temperatureOvernightLow`, `feelsLikeHigh`, `feelsLikeLow`, `humidityAverage`, `precipitationProbabilityMaximum`, `rainAccumulation`, `snowAccumulation`, `sleetAccumulation`, `iceAccumulation`, `uvIndexMaximum`, `cloudCoverAverage`, `visibilityAverage`, `windSpeedAverage`, `windSpeedMaximum`, `windGustMaximum`, `windDirectionAverage`, `weatherCode`, `weatherDescription`, `sunriseTime`, `sunsetTime`

Tomorrow.io forecasts at most six days, today plus five. For a week or seven days, use `--days 6` and say the forecast only goes six days out.

A forecast day runs from 6 AM to 6 AM local time. Treat `temperatureOvernightLow` as the low for the night following that day, even though it may occur after midnight.

Tomorrow.io stops forecasting some fields past its short-range window, so later periods can report them as `null` (for example, sleet accumulation beyond the first day). A `null` means the provider has no value for that period; it is not an error.

## Precipitation

Use the precipitation command for "Is it raining?", "Will it rain?", "When will it start?", and similar questions. It returns the current rain, snow, sleet, and freezing-rain intensities plus a five-minute forecast. The default and maximum horizon is six hours.

```bash
python3 scripts/weather.py precipitation
python3 scripts/weather.py precipitation --hours 2
```

A positive intensity in `current.values` means it's falling now. `nextLikelyWindow` requires at least a 30% forecast probability; use `maximumProbability` when the user asks for the exact chance.

## Air quality

AQI is current only. It comes from the nearest PurpleAir monitor within 20 miles, so it's a nearby reading, not a sensor at the house. Coverage is mostly the US and Canada; elsewhere the command fails with no monitor found.

```bash
python3 scripts/weather.py air-quality
```

Report the AQI, category, and PM2.5 value. Mention the monitor distance when it helps.

## Severe-weather events

Retrieve active severe-weather events on demand:

```bash
python3 scripts/weather.py events
```

An empty `events` array means no matching active event was returned. Include the event title, severity, timing, description, and instructions when present. Do not invent an all-clear message or safety guidance that is absent from the response.

## Compact summary

Use `summary` for a morning report or another broad day-ahead overview:

```bash
python3 scripts/weather.py summary
```

It includes the current temperature, today's forecast high and overnight low, current and near-term precipitation, current AQI, and active severe-weather events. `airQuality` is `null` when no monitor has a reading. Turn the JSON into concise prose; never expose the raw response unless asked.

## Other locations

Every command accepts `--location`. Tomorrow.io supports place names, US ZIP codes, and coordinates. Coordinates use `latitude,longitude` order.

```bash
python3 scripts/weather.py daily --location 'Seattle, WA'
python3 scripts/weather.py current --location '98101, US'
python3 scripts/weather.py hourly --location '45.52,-122.68'
```

If a place name is ambiguous, state the normalized location returned by the helper or ask the user to clarify.
