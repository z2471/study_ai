#!/usr/bin/env python3
"""Weather forecast CLI (no API key).

Data sources:
- wttr.in (city name -> JSON)
- Open-Meteo (lat/lon -> JSON)

Outputs at least 3 days of daily forecast.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

import requests


WTTR_URL = "https://wttr.in"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_TIMEOUT_S = 10


# https://open-meteo.com/en/docs (WMO Weather interpretation codes)
WMO_CODE_TO_DESC = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


@dataclass
class DailyForecast:
    date: str
    tmax_c: float
    tmin_c: float
    desc: str


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _safe_float(x: object, field: str) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except Exception as exc:
        raise ValueError(f"Invalid number for {field}: {x!r}") from exc


def fetch_wttr_city(city: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[DailyForecast]:
    url = f"{WTTR_URL}/{city}"
    params = {"format": "j1"}
    headers = {
        "User-Agent": "weather_forecast/1.0 (+https://github.com/openclaw/openclaw)"
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(f"Network timeout when requesting wttr.in for city={city!r}")
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Network error when requesting wttr.in: {exc}")

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("wttr.in returned non-JSON response (unexpected)") from exc

    weather = data.get("weather")
    if not isinstance(weather, list) or not weather:
        raise RuntimeError("wttr.in JSON missing 'weather' field; city may be invalid")

    out: list[DailyForecast] = []
    for day in weather[:3]:
        date = day.get("date")
        if not isinstance(date, str):
            raise RuntimeError("wttr.in JSON missing 'date' in daily forecast")

        tmax = _safe_float(day.get("maxtempC"), "maxtempC")
        tmin = _safe_float(day.get("mintempC"), "mintempC")

        # Pick a representative description: prefer 12:00, otherwise first available.
        desc = "(no description)"
        hourly = day.get("hourly")
        if isinstance(hourly, list) and hourly:
            chosen = None
            for h in hourly:
                if isinstance(h, dict) and h.get("time") in {"1200", 1200, "12:00"}:
                    chosen = h
                    break
            if chosen is None:
                chosen = hourly[0] if isinstance(hourly[0], dict) else None
            if isinstance(chosen, dict):
                wdesc = chosen.get("weatherDesc")
                if isinstance(wdesc, list) and wdesc and isinstance(wdesc[0], dict):
                    val = wdesc[0].get("value")
                    if isinstance(val, str) and val.strip():
                        desc = val.strip()

        out.append(DailyForecast(date=date, tmax_c=tmax, tmin_c=tmin, desc=desc))

    if len(out) < 3:
        raise RuntimeError("wttr.in returned fewer than 3 days of forecast")
    return out


def fetch_open_meteo_latlon(lat: float, lon: float, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[DailyForecast]:
    params = {
        "latitude": f"{lat}",
        "longitude": f"{lon}",
        "daily": "weathercode,temperature_2m_max,temperature_2m_min",
        "timezone": "auto",
    }
    headers = {
        "User-Agent": "weather_forecast/1.0 (+https://github.com/openclaw/openclaw)"
    }

    try:
        resp = requests.get(OPEN_METEO_URL, params=params, headers=headers, timeout=timeout_s)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"Network timeout when requesting Open-Meteo for lat={lat}, lon={lon}"
        )
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Network error when requesting Open-Meteo: {exc}")

    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError("Open-Meteo returned non-JSON response (unexpected)") from exc

    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError("Open-Meteo JSON missing 'daily' field")

    times = daily.get("time")
    tmaxs = daily.get("temperature_2m_max")
    tmins = daily.get("temperature_2m_min")
    codes = daily.get("weathercode")

    if not (isinstance(times, list) and isinstance(tmaxs, list) and isinstance(tmins, list) and isinstance(codes, list)):
        raise RuntimeError("Open-Meteo JSON missing expected daily arrays")

    n = min(len(times), len(tmaxs), len(tmins), len(codes), 3)
    if n < 3:
        raise RuntimeError("Open-Meteo returned fewer than 3 days of forecast")

    out: list[DailyForecast] = []
    for i in range(n):
        date = times[i]
        if not isinstance(date, str):
            raise RuntimeError("Open-Meteo daily.time contains non-string date")
        tmax = _safe_float(tmaxs[i], "temperature_2m_max")
        tmin = _safe_float(tmins[i], "temperature_2m_min")
        code = codes[i]
        try:
            icode = int(code)  # type: ignore[arg-type]
        except Exception:
            icode = -1
        desc = WMO_CODE_TO_DESC.get(icode, f"WMO:{code}")
        out.append(DailyForecast(date=date, tmax_c=tmax, tmin_c=tmin, desc=desc))

    return out


def format_table(rows: Iterable[DailyForecast]) -> str:
    lines = ["date        tmin(°C)  tmax(°C)  weather"]
    for r in rows:
        lines.append(f"{r.date:<10}  {r.tmin_c:>7.1f}  {r.tmax_c:>7.1f}  {r.desc}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="weather_forecast",
        description="Fetch 3-day weather forecast via wttr.in (city) or Open-Meteo (lat/lon).",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--city", type=str, help="City name, e.g. 'Shanghai' or 'Beijing'")
    g.add_argument("--lat", type=float, help="Latitude (requires --lon)")
    p.add_argument("--lon", type=float, help="Longitude (requires --lat)")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="HTTP timeout seconds (default: 10)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.city:
            rows = fetch_wttr_city(args.city, timeout_s=args.timeout)
        else:
            if args.lon is None:
                eprint("Error: --lat requires --lon")
                return 2
            rows = fetch_open_meteo_latlon(args.lat, args.lon, timeout_s=args.timeout)

        print(format_table(rows))
        return 0
    except RuntimeError as exc:
        eprint(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        eprint("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
