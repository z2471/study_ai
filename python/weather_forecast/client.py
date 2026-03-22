"""Open-Meteo weather forecast client (HTTP GET JSON; no API key).

This module intentionally uses only the Python standard library (urllib) to
"crawl" (fetch) Open-Meteo's public JSON API.

Docs:
- https://open-meteo.com/en/docs

We fetch daily forecasts:
- temperature_2m_max (°C)
- temperature_2m_min (°C)
- precipitation_probability_max (%)
- weathercode (WMO)

Public API:
- fetch_forecast(city=..., days=..., lat=..., lon=..., timeout_s=...)

"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_TIMEOUT_S = 10


# Minimal built-in city -> (lat, lon) mapping.
# Extend as needed.
CITY_TO_LATLON: dict[str, tuple[float, float]] = {
    "北京": (39.9042, 116.4074),
    "beijing": (39.9042, 116.4074),
    "shanghai": (31.2304, 121.4737),
    "上海": (31.2304, 121.4737),
    "guangzhou": (23.1291, 113.2644),
    "广州": (23.1291, 113.2644),
    "shenzhen": (22.5431, 114.0579),
    "深圳": (22.5431, 114.0579),
}


@dataclass(frozen=True)
class DailyForecast:
    date: str
    tmax_c: float | None
    tmin_c: float | None
    precip_prob_max: int | None
    weathercode: int | None


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm_city_key(city: str) -> str:
    return city.strip().lower()


def resolve_city_to_latlon(city: str) -> tuple[float, float] | None:
    if not isinstance(city, str) or not city.strip():
        return None
    return CITY_TO_LATLON.get(_norm_city_key(city))


def _to_float_or_none(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


def _to_int_or_none(x: Any) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except Exception:
        return None


def _get_json(url: str, *, timeout_s: int) -> Any:
    req = Request(
        url,
        headers={
            "User-Agent": "study_ai.python.weather_forecast/1.0 (+https://open-meteo.com)",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=timeout_s) as resp:
            # Open-Meteo returns application/json
            raw = resp.read()
    except HTTPError as exc:
        # Provide body snippet if available.
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        snippet = body.strip().replace("\n", " ")[:200]
        raise RuntimeError(f"HTTP {exc.code} from Open-Meteo: {snippet}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error when requesting Open-Meteo: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        sample = raw[:200].decode("utf-8", errors="replace").replace("\n", " ")
        raise RuntimeError(f"Open-Meteo returned non-JSON response (unexpected): {sample!r}") from exc


def fetch_forecast(
    city: str | None = None,
    days: int = 3,
    *,
    lat: float | None = None,
    lon: float | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Fetch daily weather forecast via Open-Meteo.

    Either provide:
    - city (must exist in CITY_TO_LATLON), or
    - lat + lon

    Args:
        city: City name (limited built-in mapping).
        days: Number of days to return (1..16 supported by Open-Meteo; we cap to 16).
        lat/lon: Coordinates.
        timeout_s: HTTP timeout seconds.

    Returns (stable, JSON-serializable):
        {
          "source": "open-meteo",
          "city": "北京",
          "latitude": 39.9,
          "longitude": 116.4,
          "days": 3,
          "fetched_at": "...",
          "forecast": [
             {"date": "YYYY-MM-DD", "tmax_c": 10.0, "tmin_c": 1.0,
              "precip_prob_max": 80, "weathercode": 61}
          ]
        }

    Raises:
        ValueError: invalid input
        RuntimeError: network / payload errors
    """

    if not isinstance(days, int) or days <= 0:
        raise ValueError("days must be a positive integer")

    # Open-Meteo supports up to 16 forecast days on the free endpoint.
    days = min(days, 16)

    resolved: tuple[float, float] | None = None
    if city is not None and isinstance(city, str) and city.strip():
        resolved = resolve_city_to_latlon(city)
        if resolved is None and (lat is None or lon is None):
            raise ValueError(
                f"Unknown city {city!r}. Provide --lat/--lon, or extend CITY_TO_LATLON."
            )

    if lat is None or lon is None:
        if resolved is None:
            raise ValueError("Either provide a known --city, or provide both --lat and --lon")
        lat, lon = resolved

    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        raise ValueError("lat/lon must be numbers")

    params = {
        "latitude": f"{float(lat):.6f}",
        "longitude": f"{float(lon):.6f}",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weathercode",
        "timezone": "auto",
        "forecast_days": str(days),
    }
    url = f"{OPEN_METEO_FORECAST_URL}?{urlencode(params)}"

    data = _get_json(url, timeout_s=timeout_s)
    if not isinstance(data, dict):
        raise RuntimeError(f"Open-Meteo returned unexpected JSON type: {type(data).__name__}")

    daily = data.get("daily")
    if not isinstance(daily, dict):
        raise RuntimeError("Open-Meteo JSON missing 'daily' field")

    times = daily.get("time")
    tmaxs = daily.get("temperature_2m_max")
    tmins = daily.get("temperature_2m_min")
    pmaxs = daily.get("precipitation_probability_max")
    codes = daily.get("weathercode")

    if not (
        isinstance(times, list)
        and isinstance(tmaxs, list)
        and isinstance(tmins, list)
        and isinstance(pmaxs, list)
        and isinstance(codes, list)
    ):
        raise RuntimeError("Open-Meteo JSON missing expected daily arrays")

    n = min(len(times), len(tmaxs), len(tmins), len(pmaxs), len(codes), days)
    if n <= 0:
        raise RuntimeError("Open-Meteo returned empty daily forecast")

    out: list[DailyForecast] = []
    for i in range(n):
        date = times[i]
        if not isinstance(date, str) or not date.strip():
            raise RuntimeError("Open-Meteo daily.time contains invalid date")

        out.append(
            DailyForecast(
                date=date,
                tmax_c=_to_float_or_none(tmaxs[i]),
                tmin_c=_to_float_or_none(tmins[i]),
                precip_prob_max=_to_int_or_none(pmaxs[i]),
                weathercode=_to_int_or_none(codes[i]),
            )
        )

    return {
        "source": "open-meteo",
        "city": city.strip() if isinstance(city, str) and city.strip() else None,
        "latitude": float(lat),
        "longitude": float(lon),
        "days": n,
        "fetched_at": _now_iso_utc(),
        "forecast": [asdict(x) for x in out],
    }
