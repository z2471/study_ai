from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .client import fetch_forecast


def _eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def _format_table(payload: dict[str, Any]) -> str:
    rows = payload.get("forecast")
    if not isinstance(rows, list):
        return json.dumps(payload, ensure_ascii=False, indent=2)

    header = (
        f"source: {payload.get('source')}  city: {payload.get('city')}  "
        f"lat: {payload.get('latitude')}  lon: {payload.get('longitude')}  "
        f"fetched_at: {payload.get('fetched_at')}"
    )

    lines = [
        header,
        "date        tmin(°C)  tmax(°C)  precip(%)  code",
    ]

    def _fmt_float(x: Any) -> str:
        try:
            return f"{float(x):.1f}"
        except Exception:
            return "-"

    def _fmt_int(x: Any) -> str:
        try:
            return f"{int(x)}"
        except Exception:
            return "-"

    for r in rows:
        if not isinstance(r, dict):
            continue
        date = str(r.get("date", ""))
        tmin = r.get("tmin_c")
        tmax = r.get("tmax_c")
        pmax = r.get("precip_prob_max")
        code = r.get("weathercode")
        lines.append(
            f"{date:<10}  {_fmt_float(tmin):>7}  {_fmt_float(tmax):>7}  {_fmt_int(pmax):>8}  {_fmt_int(code):>4}"
        )

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m python.weather_forecast",
        description="Fetch daily weather forecast via Open-Meteo (no API key).",
    )
    p.add_argument(
        "--city",
        help="City name with built-in mapping, e.g. 北京 / Shanghai. If unknown, provide --lat/--lon.",
    )
    p.add_argument("--lat", type=float, help="Latitude (fallback when city not mapped)")
    p.add_argument("--lon", type=float, help="Longitude (fallback when city not mapped)")
    p.add_argument("--days", type=int, default=3, help="How many days (default: 3; max: 16)")
    p.add_argument(
        "--format",
        choices=["json", "table"],
        default="json",
        help="Output format (default: json)",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="HTTP timeout seconds (default: 10)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        payload = fetch_forecast(
            args.city,
            args.days,
            lat=args.lat,
            lon=args.lon,
            timeout_s=args.timeout,
        )

        if args.format == "table":
            print(_format_table(payload))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except ValueError as exc:
        _eprint(f"Error: {exc}")
        return 2
    except RuntimeError as exc:
        _eprint(f"Error: {exc}")
        return 1
    except KeyboardInterrupt:
        _eprint("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
