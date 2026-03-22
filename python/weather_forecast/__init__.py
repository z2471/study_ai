"""python.weather_forecast

A small, dependency-light weather "crawler" (HTTP GET JSON) based on Open-Meteo.

Public API:
- fetch_forecast(city=..., days=..., lat=..., lon=...)

"""

from .client import fetch_forecast

__all__ = ["fetch_forecast"]
