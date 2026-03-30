"""Strict config loader. No .get(key, fallback) pattern — every key must exist.

Loads config/settings.json and config/cities.json from the project root.
Missing keys raise KeyError immediately at startup, not at trade time.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
STATE_DIR = PROJECT_ROOT / "state"


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


@dataclass(frozen=True)
class City:
    name: str
    lat: float
    lon: float
    timezone: str
    cluster: str
    settlement_unit: str  # "F" or "C"
    iem_station: Optional[str]
    wu_station: Optional[str]


class Settings:
    """Strict settings — every access is a direct dict key lookup."""

    def __init__(self, path: Optional[Path] = None):
        path = path or (CONFIG_DIR / "settings.json")
        self._data = _load_json(path)
        # Validate all top-level sections exist at load time
        required = [
            "capital_base_usd", "mode", "discovery", "ensemble",
            "calibration", "edge", "sizing", "exit", "riskguard", "execution"
        ]
        for key in required:
            if key not in self._data:
                raise KeyError(f"Missing required config key: {key}")

    def __getitem__(self, key: str):
        return self._data[key]

    @property
    def mode(self) -> str:
        return self._data["mode"]

    @property
    def capital_base_usd(self) -> float:
        return float(self._data["capital_base_usd"])


def load_cities(path: Optional[Path] = None) -> list[City]:
    path = path or (CONFIG_DIR / "cities.json")
    data = _load_json(path)
    return [City(**c) for c in data["cities"]]


# Module-level singletons — initialized once at import
settings = Settings()
cities = load_cities()
cities_by_name: dict[str, City] = {c.name: c for c in cities}
