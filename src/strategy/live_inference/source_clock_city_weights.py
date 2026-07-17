"""Per-city source-clock replacement weights.

The city one-scheme artifact is the operator-selected deployment surface for
source-clock vNext: exactly one source basket per city, with fixed non-negative
weights learned from the 2026-06-25 walk-forward run. This module is deliberately
pure and file-backed so the forecast materializer can consume the same basket as
the replay/download tools without inventing another registry.
"""

from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

from src.config import PROJECT_ROOT
from src.strategy.live_inference.source_clock_vnext import provider_family_for_source


DEFAULT_CITY_ONE_SCHEME_PATH = (
    PROJECT_ROOT
    / "state"
    / "fusion_source_compare"
    / "grid_aware_retest_20260625"
    / "city_one_scheme_grid_aware.csv"
)
ENV_CITY_ONE_SCHEME_PATH = "ZEUS_SOURCE_CLOCK_CITY_WEIGHTS"
GRID_AWARE_ARTIFACT_NAME = "grid_aware_retest_20260625"

# Present-weight floor for fixed_weight_center_from_values: a center built from
# a sliver of the fitted basket is not the fitted estimator. Fail-closed (return
# None) when the present sources' configured weight sums to less than this
# fraction of the basket, i.e. when more than 75% of the fitted basket is
# absent. Not configurable — no new knob for a threshold this is not tuned to.
PRESENT_WEIGHT_FLOOR = 0.25


@dataclass(frozen=True)
class CityOneScheme:
    city: str
    scheme_status: str
    final_sources: tuple[str, ...]
    weights: Mapping[str, float]
    sample_n: int
    walkforward_pass: bool
    one_scheme_status: str

    @property
    def present_weight_sum(self) -> float:
        return float(sum(self.weights.values()))

    @property
    def provider_families(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(provider_family_for_source(source) for source in self.final_sources)
        )


@dataclass(frozen=True)
class FixedWeightCenter:
    city: str
    mu_c: float
    used_weights: Mapping[str, float]
    configured_weights: Mapping[str, float]
    missing_sources: tuple[str, ...]
    renormalized: bool
    one_scheme_status: str
    walkforward_pass: bool

    @property
    def complete(self) -> bool:
        return not self.missing_sources


def city_one_scheme_path() -> Path:
    override = os.environ.get(ENV_CITY_ONE_SCHEME_PATH)
    if override and override.strip():
        return Path(override).expanduser()
    return DEFAULT_CITY_ONE_SCHEME_PATH


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "pass"}


def _parse_weighted_sources(text: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for raw_part in str(text or "").split("+"):
        part = raw_part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        source, weight_text = part.split(":", 1)
        source = source.strip()
        try:
            weight = float(weight_text)
        except ValueError:
            continue
        if not source or not math.isfinite(weight) or weight <= 0.0:
            continue
        weights[source] = weights.get(source, 0.0) + weight
    total = sum(weights.values())
    if total > 0.0:
        weights = {source: weight / total for source, weight in weights.items()}
    return weights


def _parse_sources(text: str, weights: Mapping[str, float]) -> tuple[str, ...]:
    sources = tuple(source.strip() for source in str(text or "").split("+") if source.strip())
    if sources:
        return sources
    return tuple(weights)


def _row_text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def _row_int(row: Mapping[str, object], *keys: str) -> int:
    for key in keys:
        value = row.get(key)
        if value is None or not str(value).strip():
            continue
        try:
            return int(float(value))
        except ValueError:
            continue
    return 0


def _walkforward_or_grid_pass(row: Mapping[str, object]) -> bool:
    if row.get("walkforward_pass") is not None:
        return _truthy(row.get("walkforward_pass"))
    return str(row.get("selection_status") or "").strip() == "GRID_CAP10_LIVE_READY"


@lru_cache(maxsize=8)
def load_city_one_schemes(path_text: str | None = None) -> Mapping[str, CityOneScheme]:
    path = Path(path_text).expanduser() if path_text else city_one_scheme_path()
    if not path.exists():
        return {}
    out: dict[str, CityOneScheme] = {}
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            city = str(row.get("city") or "").strip()
            if not city:
                continue
            weights = _parse_weighted_sources(
                _row_text(row, "final_weighted_sources", "grid_aware_weighted_sources")
            )
            if not weights:
                continue
            out[city] = CityOneScheme(
                city=city,
                scheme_status=_row_text(row, "scheme_status", "selection_status"),
                final_sources=_parse_sources(
                    _row_text(row, "final_sources", "grid_aware_sources"),
                    weights,
                ),
                weights=weights,
                sample_n=_row_int(row, "sample_n", "grid_best_sample_n", "candidate_count"),
                walkforward_pass=_walkforward_or_grid_pass(row),
                one_scheme_status=_row_text(row, "one_scheme_status", "selection_status"),
            )
    return out


def scheme_for_city(city: str, *, path: str | Path | None = None) -> CityOneScheme | None:
    schemes = load_city_one_schemes(None if path is None else str(path))
    return schemes.get(str(city))


def all_configured_source_ids(*, path: str | Path | None = None) -> tuple[str, ...]:
    schemes = load_city_one_schemes(None if path is None else str(path))
    sources: dict[str, None] = {}
    for scheme in schemes.values():
        for source in scheme.final_sources:
            sources[source] = None
    return tuple(sources)


def affected_cities_for_source_updates(
    updated_sources: Sequence[str], *, path: str | Path | None = None
) -> tuple[str, ...]:
    updated = {str(source).strip() for source in updated_sources if str(source).strip()}
    if not updated:
        return ()
    schemes = load_city_one_schemes(None if path is None else str(path))
    return tuple(
        sorted(
            city
            for city, scheme in schemes.items()
            if any(source in updated for source in scheme.final_sources)
        )
    )


def fixed_weight_center_from_values(
    *,
    city: str,
    values_c_by_source: Mapping[str, float],
    path: str | Path | None = None,
) -> FixedWeightCenter | None:
    scheme = scheme_for_city(city, path=path)
    if scheme is None:
        return None
    used: dict[str, float] = {}
    missing: list[str] = []
    for source, configured_weight in scheme.weights.items():
        value = values_c_by_source.get(source)
        try:
            value_f = float(value) if value is not None else None
        except (TypeError, ValueError):
            value_f = None
        if value_f is None or not math.isfinite(value_f):
            missing.append(source)
            continue
        used[source] = float(configured_weight)
    total = sum(used.values())
    if total < PRESENT_WEIGHT_FLOOR:
        # A selected source that has not arrived is omitted and the remaining
        # weights are renormalized (2026-07-17 consult verdict P2-C); basket
        # membership is never a readiness requirement. But a center built from
        # a sliver of the fitted basket is not the fitted estimator: refuse
        # when more than 75% of the configured weight is absent. Incident
        # 2026-07-13/14: a frozen dominant-weight source (gem_hrdps, weight
        # 0.766) going capturable-but-unservable made this function return
        # None outright, darkening CONUS for 30-37h even though the OTHER
        # configured sources were fine.
        return None
    normalized = {source: weight / total for source, weight in used.items()}
    mu = sum(float(values_c_by_source[source]) * weight for source, weight in normalized.items())
    return FixedWeightCenter(
        city=city,
        mu_c=float(mu),
        used_weights=normalized,
        configured_weights=dict(scheme.weights),
        missing_sources=tuple(missing),
        renormalized=bool(missing),
        one_scheme_status=scheme.one_scheme_status,
        walkforward_pass=scheme.walkforward_pass,
    )
