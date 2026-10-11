"""A fake, deterministic weather API client configured from config/defaults.yaml."""

from __future__ import annotations

import hashlib

import yaml

from utils.helpers import PACKAGE_ROOT


class WeatherClient:
    def __init__(self) -> None:
        config = yaml.safe_load((PACKAGE_ROOT / "config" / "defaults.yaml").read_text())
        self.max_days = int(config["max_forecast_days"])
        self.skies = list(config["skies"])

    def _seed(self, *parts: object) -> int:
        return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)

    def observe(self, city: str) -> dict[str, object]:
        seed = self._seed(city)
        return {"temperature_c": seed % 35, "sky": self.skies[seed % len(self.skies)]}

    def hourly(self, city: str, day: int) -> list[float]:
        return [float(self._seed(city, day, hour) % 30) for hour in range(24)]
