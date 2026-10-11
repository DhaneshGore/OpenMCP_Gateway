from __future__ import annotations

from utils.api_client import WeatherClient
from utils.helpers import average


def forecast_for(city: str, days: int) -> list[dict[str, float]]:
    client = WeatherClient()
    days = max(1, min(days, client.max_days))
    out = []
    for day in range(days):
        temps = client.hourly(city, day)
        out.append({"day": day + 1, "mean_c": round(average(temps), 1), "max_c": max(temps)})
    return out
