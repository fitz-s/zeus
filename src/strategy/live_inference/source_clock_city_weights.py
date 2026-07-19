"""Per-city source-clock replacement weights.

The city one-scheme artifact is the operator-selected deployment surface for
source-clock vNext: exactly one source basket per city, with fixed non-negative
weights. This module is deliberately pure and file-backed so the forecast
materializer can consume the same basket as the replay/download tools without
inventing another registry.

ARTIFACT-FIRST LOADING (2026-07-17): ``scheme_for_city`` now prefers the
versioned, walk-forward-refit artifact written by
``scripts/fit_source_clock_city_weights.py`` (``state/source_clock_weights/
ACTIVE.json`` -> ``city_weights_<YYYYMMDD>.json``) over the frozen, never-refit
2026-06-25 CSV. The artifact is per-(city, metric); the CSV is per-city only
(metric-agnostic). Fallback order when no explicit ``path``/env override is
given: artifact city+metric hit -> artifact absent or city/metric miss -> legacy
CSV. An explicit ``path=`` argument or the ``ZEUS_SOURCE_CLOCK_CITY_WEIGHTS`` env
override is a deliberate CSV-only request (tests and callers pinning the legacy
scheme) and bypasses the artifact entirely, byte-identical to pre-2026-07-17
behavior.
"""

from __future__ import annotations

import csv
import hashlib
import json
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

# --- versioned walk-forward artifact (2026-07-17) --------------------------------
DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR = PROJECT_ROOT / "state" / "source_clock_weights"
ENV_SOURCE_CLOCK_ARTIFACT_DIR = "ZEUS_SOURCE_CLOCK_WEIGHTS_ARTIFACT_DIR"
ACTIVE_POINTER_NAME = "ACTIVE.json"
ONE_SCHEME_READY_STATUS = "GRID_CAP10_LIVE_READY"
SOURCE_CLOCK_ARTIFACT_ACTIVE_STATUS = "SOURCE_CLOCK_ARTIFACT_ACTIVE"
# Live call sites (materializer + live queue) thread the track metric since 2026-07-17,
# so HIGH and LOW read their own artifact buckets. "high" remains the documented default
# for metric-less callers (legacy CSV was metric-agnostic) -- a named assumption.
DEFAULT_ARTIFACT_METRIC = "high"


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
    return str(row.get("selection_status") or "").strip() == ONE_SCHEME_READY_STATUS


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


def _source_clock_artifact_dir() -> Path:
    override = os.environ.get(ENV_SOURCE_CLOCK_ARTIFACT_DIR)
    if override and override.strip():
        return Path(override).expanduser()
    return DEFAULT_SOURCE_CLOCK_ARTIFACT_DIR


@lru_cache(maxsize=8)
def _load_active_artifact(artifact_dir_text: str) -> Mapping[str, object] | None:
    """Read+integrity-check the ACTIVE.json pointer -> the referenced artifact JSON.

    Fail-soft: a missing pointer/artifact, a sha256 mismatch, or any parse error returns
    ``None`` (the caller falls back to the legacy CSV) — this loader never raises.
    """
    artifact_dir = Path(artifact_dir_text)
    pointer_path = artifact_dir / ACTIVE_POINTER_NAME
    if not pointer_path.exists():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        artifact_path = artifact_dir / str(pointer["artifact"])
        raw = artifact_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != str(pointer.get("sha256", "")):
            return None
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _artifact_scheme_for_city(city: str, metric: str) -> CityOneScheme | None:
    artifact = _load_active_artifact(str(_source_clock_artifact_dir()))
    if not artifact:
        return None
    cell = (artifact.get("cities") or {}).get(str(city), {}).get(metric)
    if not cell:
        return None
    weights = {
        str(m): float(w) for m, w in (cell.get("models") or {}).items() if float(w) > 0.0
    }
    if not weights:
        return None
    prov = cell.get("basket_provenance") or {}
    return CityOneScheme(
        city=str(city),
        scheme_status="SOURCE_CLOCK_ARTIFACT",
        final_sources=tuple(weights),
        weights=weights,
        sample_n=int(prov.get("n_paired_dates", 0) or 0),
        walkforward_pass=True,
        one_scheme_status=ONE_SCHEME_READY_STATUS,
    )


def scheme_for_city(
    city: str, *, path: str | Path | None = None, metric: str | None = None
) -> CityOneScheme | None:
    """The active per-city source-clock scheme.

    An explicit ``path`` or the ``ZEUS_SOURCE_CLOCK_CITY_WEIGHTS`` env override is a
    deliberate CSV-only request (byte-identical to pre-2026-07-17 behavior). Otherwise the
    versioned walk-forward artifact (state/source_clock_weights/ACTIVE.json) is preferred
    for ``metric`` (default ``DEFAULT_ARTIFACT_METRIC``); a city/metric miss or an absent
    artifact falls back to the legacy CSV.
    """
    if path is not None or os.environ.get(ENV_CITY_ONE_SCHEME_PATH):
        schemes = load_city_one_schemes(None if path is None else str(path))
        return schemes.get(str(city))
    hit = _artifact_scheme_for_city(city, metric or DEFAULT_ARTIFACT_METRIC)
    if hit is not None:
        return hit
    schemes = load_city_one_schemes(None)
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
    metric: str | None = None,
) -> FixedWeightCenter | None:
    scheme = scheme_for_city(city, path=path, metric=metric)
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
