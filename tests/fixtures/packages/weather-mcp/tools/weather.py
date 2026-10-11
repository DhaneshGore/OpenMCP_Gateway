from __future__ import annotations

from utils.api_client import WeatherClient


def current_conditions(city: str) -> dict[str, object]:
    reading = WeatherClient().observe(city)
    return {"city": city, "temperature_c": reading["temperature_c"], "sky": reading["sky"]}
