# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator law "每个城市都应该有最好的天气预报 / per-city best near-airport source".
#   The authority file is docs/polyweather_city_source_overlay_verified.csv (operator-maintained
#   per-city per-source registry). Operator correction 2026-06-17: "距离不是唯一影响是否贴近wu的点" —
#   physical cell-distance is necessary but NOT sufficient for closeness to the WU settlement point;
#   coverage_relation (airport/city/nearby/regional/national) + source-authority + settlement-skill
#   also decide it. This loader is the CONSUMPTION layer for that registry — it exposes, per city,
#   the forecast sources (Open-Meteo multi-model + national met services where listed) with their
#   coverage_relation / priority_rank / authority, so the per-city-best selection can combine the
#   registry with cell-distance (state/per_city_model_cell_distance.json) and settlement-skill.
"""Per-city source registry loaded from docs/polyweather_city_source_overlay_verified.csv.

The CSV is a normalized per-city per-source table: one row per (city, role_layer, role_kind). This
module reads it into structured per-city profiles. It is READ-ONLY config (no DB, no network) and
fail-soft: a missing/unreadable CSV yields an empty registry (the caller then falls back to the
polygon/global model set, never crashing).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_CSV_PATH = PROJECT_ROOT / "docs" / "polyweather_city_source_overlay_verified.csv"

# National met-service forecast role_kinds (authoritative near-WU forecasts even at "national"
# coverage_relation — the operator's "distance is not the only factor" point). These are NOT yet
# ingested into the forecast path (open-meteo only today); the registry marks where they belong.
NATIONAL_FORECAST_KINDS = frozenset(
    {"nws_forecast", "mgm_forecast", "hko_forecast", "cwa_forecast", "ncm_forecast"}
)
OPENMETEO_FORECAST_KINDS = frozenset(
    {"forecast_primary", "forecast_ensemble", "forecast_multi_model"}
)


@dataclass(frozen=True)
class SourceRow:
    role_layer: str
    role_kind: str
    source_family: str
    provider: str
    product_name: str
    station_or_product_id: str
    coverage_relation: str  # airport | city | nearby_cluster | regional | national | history_only
    priority_rank: int
    is_primary: bool
    lat: float | None
    lon: float | None
    notes: str = ""


@dataclass(frozen=True)
class CitySourceProfile:
    city_key: str
    city_name: str
    icao: str
    lat: float | None
    lon: float | None
    rows: tuple[SourceRow, ...] = field(default_factory=tuple)

    def _layer(self, layer: str) -> tuple[SourceRow, ...]:
        return tuple(r for r in self.rows if r.role_layer == layer)

    @property
    def forecast_sources(self) -> tuple[SourceRow, ...]:
        # priority_rank ascending = most-preferred first
        return tuple(sorted(self._layer("forecast"), key=lambda r: r.priority_rank))

    @property
    def national_forecast_kinds(self) -> tuple[str, ...]:
        return tuple(r.role_kind for r in self.forecast_sources if r.role_kind in NATIONAL_FORECAST_KINDS)

    @property
    def has_openmeteo_multimodel(self) -> bool:
        return any(r.role_kind == "forecast_multi_model" for r in self.forecast_sources)

    @property
    def settlement_source(self) -> SourceRow | None:
        # the WU settlement point: settlement layer, else the settlement_history row
        for r in self._layer("settlement"):
            if r.is_primary:
                return r
        for r in self.rows:
            if r.role_kind == "settlement_history" and r.is_primary:
                return r
        return None


def _to_int(v: str, default: int = 9999) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_per_city_source_registry(
    path: str | Path | None = None,
) -> dict[str, CitySourceProfile]:
    """Load the per-city source registry CSV → {city_key: CitySourceProfile}.

    Fail-soft: missing/unreadable/oddly-shaped CSV → empty dict (caller falls back).
    """
    csv_path = Path(path) if path else REGISTRY_CSV_PATH
    try:
        text = csv_path.read_text()
    except Exception:
        return {}
    reader = csv.DictReader(text.splitlines())
    by_city: dict[str, list[SourceRow]] = {}
    meta: dict[str, tuple[str, str, float | None, float | None]] = {}
    for row in reader:
        ck = (row.get("city_key") or "").strip()
        if not ck:
            continue
        lat = _to_float(row.get("lat", ""))
        lon = _to_float(row.get("lon", ""))
        meta.setdefault(ck, (row.get("city_name", ""), row.get("icao", ""), lat, lon))
        by_city.setdefault(ck, []).append(
            SourceRow(
                role_layer=(row.get("role_layer") or "").strip(),
                role_kind=(row.get("role_kind") or "").strip(),
                source_family=(row.get("source_family") or "").strip(),
                provider=(row.get("provider") or "").strip(),
                product_name=(row.get("product_name") or "").strip(),
                station_or_product_id=(row.get("station_or_product_id") or "").strip(),
                coverage_relation=(row.get("coverage_relation") or "").strip(),
                priority_rank=_to_int(row.get("priority_rank", "")),
                is_primary=str(row.get("is_primary", "")).strip().lower() == "true",
                lat=lat,
                lon=lon,
                notes=(row.get("notes") or "").strip(),
            )
        )
    out: dict[str, CitySourceProfile] = {}
    for ck, rows in by_city.items():
        name, icao, lat, lon = meta[ck]
        out[ck] = CitySourceProfile(
            city_key=ck, city_name=name, icao=icao, lat=lat, lon=lon, rows=tuple(rows)
        )
    return out


def forecast_capability_summary(
    registry: Mapping[str, CitySourceProfile] | None = None,
) -> dict[str, dict]:
    """Per-city forecast-capability summary: openmeteo multi-model presence + national services."""
    reg = registry if registry is not None else load_per_city_source_registry()
    return {
        ck: {
            "openmeteo_multimodel": p.has_openmeteo_multimodel,
            "national_forecasts": list(p.national_forecast_kinds),
            "n_forecast_sources": len(p.forecast_sources),
        }
        for ck, p in reg.items()
    }
