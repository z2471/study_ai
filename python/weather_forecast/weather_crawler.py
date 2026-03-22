"""Backward-compatible wrapper.

Historically this package used a `weather_crawler.py` implementation targeting
wttr.in and the `requests` dependency.

The current implementation targets Open-Meteo and uses only the standard
library in `client.py`.

Keep this module to avoid breaking existing imports:
    from python.weather_forecast.weather_crawler import fetch_forecast

"""

from __future__ import annotations

from .client import fetch_forecast

__all__ = ["fetch_forecast"]
