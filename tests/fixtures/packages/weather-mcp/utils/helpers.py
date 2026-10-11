from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def average(values: list[float]) -> float:
    return sum(values) / len(values)


def load_schema() -> dict[str, object]:
    return json.loads((PACKAGE_ROOT / "schemas" / "weather.json").read_text())


def package_version() -> str:
    return str(json.loads((PACKAGE_ROOT / "metadata.json").read_text())["version"])


def render_report(city: str, days: list[dict[str, float]]) -> str:
    template = (PACKAGE_ROOT / "templates" / "report.txt").read_text()
    lines = "\n".join(f"day {d['day']}: mean {d['mean_c']}C, max {d['max_c']}C" for d in days)
    return template.format(city=city, days=len(days), lines=lines)
