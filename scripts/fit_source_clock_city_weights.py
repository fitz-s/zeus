#!/usr/bin/env python3
# Created: 2026-07-17
# Last reused/audited: 2026-07-17
# Authority basis: docs/evidence/upstream_physical_2026_07_17/consult_freshness_decoupling_verdict.txt
#   (basket-governance rule: paired greedy selection with 0.05C/3% practical-gain margin
#   + 2*SE significance test, cap 4, >=60/30/<30 data-availability tiers, region/global-core
#   fallback, weights keyed by exact model id); consult_v2_issuance_staleness_verdict.txt
#   §(c) (frozen weights refit cadence + walk-forward-only training + exact model-id keys);
#   docs/evidence/upstream_physical_2026_07_17/combo_experiments_report.md (measured: full-pool
#   precision beats the frozen 3-model CSV by ~0.08C; >3-4 models hurts; jma_seamless hurts
#   pooled/EU/Asia; value is region-dependent -> this generator replaces the frozen,
#   never-refit CSV with a versioned walk-forward-refit artifact).
"""Generate a walk-forward, versioned source-clock per-city-per-metric weight artifact.

Replaces ``state/fusion_source_compare/grid_aware_retest_20260625/city_one_scheme_grid_aware.csv``
(frozen 2026-06-25, never refit, city-only i.e. metric-agnostic) with a reproducible artifact
written to a content-addressed
``state/source_clock_weights/city_weights_<YYYYMMDD>_<sha256>.json`` candidate. The existing
``state/source_clock_weights/ACTIVE.json`` pointer is changed only by explicit ``--activate``
after separate OOS validation. The consumer switch lives in
``src/strategy/live_inference/source_clock_city_weights.py::scheme_for_city``.

DATA (STRICTLY WALK-FORWARD): the shared current-resolver settlement reader admits only
settlements whose resolver era, source identity, station, unit, rounding and page view match the
CURRENT contract, and whose durable ``label_known_at`` is strictly before an explicit UTC
``as_of`` instant. Observation-only labels and every pre-cutover source epoch are excluded.
``raw_model_forecasts`` contributes a physical-skill residual only when its fixed lead-1
fixed-run ``single_runs`` or ``standard_api_meta_stamped`` row is COVERED, finite, carries
full source identity, retains the raw-input
``training_allowed=0`` marker, and was captured and recorded before ``as_of``. These rows do
not prove publication-time or economic advantage; a separate outer validation owns that claim.

WEIGHT MATH: mirrors ``src/forecast/center.py::raw_second_moment_weights`` EXACTLY (imported,
not reimplemented — source-identity law). Every weights computation in this file calls that
function with ``unit="C"`` because raw_m2 here is always computed in degC^2 (forecast_value_c
is native degC; settlement is converted to degC before the residual) — the same basis the
materializer's EXIT seam uses, never the ENTRY seam's degF^2-native scaling.

BASKET SELECTION (consult basket-governance, simplified per operator instruction to "no
waiting periods" — a single walk-forward pass, not a separate nested outer-rolling-origin
evaluation): start from the best-availability-aware single model; greedily add the candidate
with the most negative paired-date mean-MAE-delta only when the delta clears BOTH a practical-
gain margin ``eps = max(0.05C, 0.03*current_basket_MAE)`` AND a 2*SE significance test (SE from
the target-date-paired delta sample); cap the basket at 4 models. A city with fewer than 60
settled paired dates (for that metric) falls back to its region's pooled basket (regions CONUS/
EU/ASIA/OTHER, derived from config/cities.json country_code+lat/lon for CONUS and timezone
prefix for EU/ASIA); below 30, it falls back to the fixed 3-model global core basket
(icon_global + ecmwf_ifs + ukmo_global_deterministic_10km — the E2-validated backbone; DB model
id for the shorthand "ukmo_global" in the consult is ukmo_global_deterministic_10km, the actual
raw_model_forecasts.model id), weighted by GLOBAL-pooled raw second moments for those 3 models.

DETERMINISM: same DB state + as_of => byte-identical CONTENT (json.dumps(sort_keys=True),
weights/MAE rounded to a fixed precision). ``generated_at`` is wall-clock run metadata, not
derived from DB state, and may legitimately differ between two runs of the same as_of; pass
``--generated-at`` explicitly to get a byte-identical file (this is how the determinism test
pins it).

Refresh cadence (documented, NOT wired as a scheduler job — that is a deploy decision):
weekly cron candidate, e.g. ``0 6 * * 1 cd /path/to/zeus && python3
scripts/fit_source_clock_city_weights.py --as-of <UTC instant>``. This only creates a candidate;
activation remains an explicit operator action after the outer OOS validation passes.

READ-ONLY over state/zeus-forecasts.db (file:...?mode=ro). Writes a candidate artifact under
state/source_clock_weights/; it changes the active pointer only with ``--activate``. It never
touches the legacy CSV or any DB.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import runtime_cities_by_name  # noqa: E402
from src.data.bayes_precision_fusion_download import _model_in_domain  # noqa: E402
from src.data.bayes_precision_fusion_history_provider import (  # noqa: E402
    raw_product_matches_live_source,
)
from src.data.current_settlement_history import read_current_settlement_history  # noqa: E402
from src.forecast.center import MIN_SETTLED_N, raw_second_moment_weights  # noqa: E402
from src.forecast.model_selection import (  # noqa: E402
    ANCHOR_MODEL,
    GLOBAL_LIKELIHOOD_MODELS,
    REGIONAL_MODELS,
)
from src.strategy.live_inference.source_clock_city_weights import (  # noqa: E402
    DEFAULT_CITY_ONE_SCHEME_PATH,
    fixed_weight_center_from_values,
)
from src.strategy.live_inference.source_clock_vnext import provider_family_for_source  # noqa: E402
from src.state.paths import write_json_atomic  # noqa: E402

FCST_DEFAULT = ROOT / "state" / "zeus-forecasts.db"
CITIES_DEFAULT = ROOT / "config" / "cities.json"
OUT_DIR_DEFAULT = ROOT / "state" / "source_clock_weights"
METRICS = ("high", "low")
TRAINING_LEAD_DAYS = 1
TRAINING_CONTRACT = "source_skill_current_resolver_precision_fit_v2"
_CURRENT_RESOLVER_EARLIEST_DATE = "2026-02-21"
GLOBAL_CORE_BASKET = ("icon_global", "ecmwf_ifs", "ukmo_global_deterministic_10km")
# Candidate universe = models the live pipeline actually fetches (anchor + globals +
# regionals, src/forecast/model_selection.py). The previous_runs archive also carries
# retired models (gfs_global/gem_global/jma_seamless/icon_seamless, dropped 2026-06-17);
# a basket naming one would be permanently unservable at decision time — the serving
# renormalizer would silently degrade it, and a single-model retired basket would fall
# below PRESENT_WEIGHT_FLOOR and blank the city (the exact incident class this whole
# artifact exists to prevent).
LIVE_SERVABLE_MODELS = frozenset(
    (ANCHOR_MODEL,) + GLOBAL_LIKELIHOOD_MODELS + REGIONAL_MODELS
)
TIER1_MIN_N = 60      # city-specific greedy basket
TIER2_MIN_N = 30      # region-pooled basket (below TIER1_MIN_N, at/above this)
BASKET_CAP = 4
MIN_ENTRY_PROVIDER_FAMILIES = 2
CONUS_LAT = (24.0, 50.0)
CONUS_LON = (-125.0, -66.0)

_RAW_FORECAST_QUERY = """
    SELECT raw_model_forecast_id, endpoint, model, city, target_date, metric,
           forecast_value_c, source_cycle_time, source_available_at, captured_at,
           recorded_at, training_allowed, coverage_status, source_id, source_family,
           product_id, model_name, request_url_hash, provider, endpoint_mode,
           request_params_json, latitude_requested, longitude_requested, timezone_requested
      FROM raw_model_forecasts
     WHERE endpoint = 'single_runs'
       AND lead_days = ?
       AND target_date >= ?
       AND target_date < ?
     ORDER BY city, metric, model, target_date, raw_model_forecast_id
"""


def _settlement_to_celsius(value: float, unit: str | None) -> float:
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def _as_of_utc(value: str | _dt.datetime) -> _dt.datetime:
    if isinstance(value, _dt.datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            try:
                return _dt.datetime.combine(_dt.date.fromisoformat(raw), _dt.time(), _dt.UTC)
            except ValueError as exc:
                raise ValueError("as_of must be an ISO-8601 UTC timestamp") from exc
        try:
            parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("as_of must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("as_of must include a UTC offset")
    return parsed.astimezone(_dt.UTC)


def _parse_utc(value: object, *, allow_sqlite_utc: bool = False) -> _dt.datetime | None:
    raw = str(value or "").strip()
    try:
        parsed = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        if allow_sqlite_utc and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw):
            return parsed.replace(tzinfo=_dt.UTC)
        return None
    return parsed.astimezone(_dt.UTC)


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _region_for_city(cfg: Mapping[str, object]) -> str:
    """CONUS / EU / ASIA / OTHER, derived from config/cities.json fields only."""
    if str(cfg.get("country_code") or "") == "US":
        try:
            lat = float(cfg.get("lat"))
            lon = float(cfg.get("lon"))
            if CONUS_LAT[0] <= lat <= CONUS_LAT[1] and CONUS_LON[0] <= lon <= CONUS_LON[1]:
                return "CONUS"
        except (TypeError, ValueError):
            pass
    tz = str(cfg.get("timezone") or "")
    if tz.startswith("Europe/"):
        return "EU"
    if tz.startswith("Asia/"):
        return "ASIA"
    return "OTHER"


def _target_local_day_start(city: object, target_date: str) -> _dt.datetime | None:
    try:
        from zoneinfo import ZoneInfo

        return _dt.datetime.combine(
            _dt.date.fromisoformat(target_date), _dt.time(), ZoneInfo(str(city.timezone))
        ).astimezone(_dt.UTC)
    except (AttributeError, TypeError, ValueError):
        return None


def _is_later_actual_capture(
    candidate: tuple[_dt.datetime, _dt.datetime, int],
    current: tuple[_dt.datetime, _dt.datetime, int] | None,
) -> bool:
    return current is None or candidate > current


def load_walk_forward_rows(
    conn: sqlite3.Connection,
    *,
    as_of: str | _dt.datetime,
    servable: frozenset[str] | None = None,
    cities_by_name: Mapping[str, object] | None = None,
) -> dict[str, dict]:
    """Load a fixed-lead physical-skill corpus under the current resolver contract.

    Only single-runs and standard-meta-stamped fixed-run rows reconstruct daily
    physical skill. Previous-runs daily extrema are excluded because their hourly
    values are not one fixed-run product. The returned availability maps retain
    every label and raw-input instant needed by the outer rolling validator.
    """
    cutoff = _as_of_utc(as_of)
    if cities_by_name is None:
        cities_by_name = runtime_cities_by_name()
    history = read_current_settlement_history(
        conn,
        cities_by_name=cities_by_name,
        as_of=cutoff,
    )
    labels = {
        (row.city, row.metric, row.target_date): row
        for row in history.rows
    }
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    rows = cur.execute(
        _RAW_FORECAST_QUERY,
        (TRAINING_LEAD_DAYS, _CURRENT_RESOLVER_EARLIEST_DATE, cutoff.date().isoformat()),
    ).fetchall()
    obs: dict[tuple[str, str], dict[str, dict[str, float]]] = {}
    settle: dict[tuple[str, str], dict[str, float]] = {}
    label_availability: dict[tuple[str, str, str], str] = {
        key: row.label_known_at.isoformat() for key, row in labels.items()
    }
    forecast_availability: dict[tuple[str, str, str, str], dict[str, str]] = {}
    excluded_reason_counts = dict(history.excluded_reason_counts)
    accepted_rows: dict[tuple[str, str, str, str], tuple[sqlite3.Row, float, object, _dt.datetime, _dt.datetime, _dt.datetime, dict[str, str | None]]] = {}

    def exclude(reason: str) -> None:
        excluded_reason_counts[reason] = excluded_reason_counts.get(reason, 0) + 1

    for row in rows:
        city, metric, model, target_date = (
            str(row["city"]), str(row["metric"]), str(row["model"]), str(row["target_date"]),
        )
        label = labels.get((city, metric, target_date))
        if label is None:
            exclude("RAW_LABEL_NOT_CURRENT_RESOLVER_ELIGIBLE")
            continue
        city_config = cities_by_name.get(city)
        if city_config is None:
            exclude("RAW_CITY_NOT_CURRENT_RUNTIME_CITY")
            continue
        if row["training_allowed"] != 0:
            exclude("RAW_TRAINING_ALLOWED_MARKER_INVALID")
            continue
        if str(row["coverage_status"] or "").strip().upper() != "COVERED":
            exclude("RAW_COVERAGE_NOT_COVERED")
            continue
        source_cycle_at = _parse_utc(row["source_cycle_time"])
        captured_at = _parse_utc(row["captured_at"])
        recorded_at = _parse_utc(row["recorded_at"], allow_sqlite_utc=True)
        source_available_at = _parse_utc(row["source_available_at"])
        if (
            source_cycle_at is None
            or captured_at is None
            or recorded_at is None
            or source_available_at is None
            or captured_at >= cutoff
            or recorded_at >= cutoff
            or source_available_at >= cutoff
        ):
            exclude("RAW_AVAILABILITY_NOT_STRICTLY_BEFORE_AS_OF")
            continue
        target_start = _target_local_day_start(city_config, target_date)
        if target_start is None:
            exclude("RAW_CITY_TIMEZONE_INVALID")
            continue
        if (
            source_cycle_at >= target_start
            or source_available_at >= target_start
            or captured_at >= target_start
        ):
            exclude("RAW_NOT_AVAILABLE_BEFORE_TARGET_LOCAL_DAY")
            continue
        source_identity = {
            "source_id": _text(row["source_id"]),
            "source_family": _text(row["source_family"]),
            "product_id": _text(row["product_id"]),
            "request_url_hash": _text(row["request_url_hash"]),
        }
        if any(value is None for value in source_identity.values()):
            exclude("RAW_SOURCE_IDENTITY_MISSING")
            continue
        if not raw_product_matches_live_source(
            row, city_config, lead_days=TRAINING_LEAD_DAYS
        ):
            exclude("RAW_PRODUCT_NOT_CURRENT_LIVE_EQUIVALENT")
            continue
        if servable is not None and model not in servable:
            continue  # archive-only/retired model: unservable at decision time
        try:
            fc = float(row["forecast_value_c"])
        except (TypeError, ValueError):
            exclude("RAW_FORECAST_VALUE_INVALID")
            continue
        if not math.isfinite(fc):
            exclude("RAW_FORECAST_VALUE_INVALID")
            continue
        try:
            settle_value = _settlement_to_celsius(
                label.settlement_value, label.settlement_unit
            )
        except (TypeError, ValueError):
            exclude("CURRENT_RESOLVER_SETTLEMENT_VALUE_INVALID")
            continue
        cell = (city, metric, model, target_date)
        candidate = (source_cycle_at, captured_at, int(row["raw_model_forecast_id"]))
        current = accepted_rows.get(cell)
        if current is not None:
            existing = (current[3], current[4], int(current[0]["raw_model_forecast_id"]))
        else:
            existing = None
        if _is_later_actual_capture(candidate, existing):
            accepted_rows[cell] = (
                row, fc, label, source_cycle_at, captured_at, recorded_at, source_identity
            )

    for (city, metric, model, target_date), (
        row, fc, label, _source_cycle_at, captured_at, recorded_at, source_identity,
    ) in accepted_rows.items():
        key = (city, metric)
        settle_value = _settlement_to_celsius(label.settlement_value, label.settlement_unit)
        settle.setdefault(key, {})[target_date] = settle_value
        obs.setdefault(key, {}).setdefault(target_date, {})[model] = fc
        forecast_availability[(city, metric, target_date, model)] = {
            "captured_at": captured_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "source_available_at": _parse_utc(row["source_available_at"]).isoformat(),
            **{name: str(value) for name, value in source_identity.items()},
        }
    return {
        "obs": obs,
        "settle": settle,
        "label_availability": label_availability,
        "forecast_availability": forecast_availability,
        "excluded_reason_counts": excluded_reason_counts,
    }


def residual_stats_by_model(
    obs_by_key: Mapping[object, Mapping[str, float]], settle_by_key: Mapping[object, float]
) -> dict[str, tuple[float, int]]:
    """``{model: (raw_m2_degC2, n)}`` — mean squared residual (bias^2 included, no
    demeaning), the exact basis ``raw_second_moment_weights`` consumes."""
    sq_sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for key, settle_c in settle_by_key.items():
        for model, fc in obs_by_key.get(key, {}).items():
            r = fc - settle_c
            sq_sums[model] = sq_sums.get(model, 0.0) + r * r
            counts[model] = counts.get(model, 0) + 1
    return {m: (sq_sums[m] / counts[m], counts[m]) for m in sorted(counts)}


def _filter_models(
    obs_by_key: Mapping[object, Mapping[str, float]], eligible: frozenset[str]
) -> dict[object, dict[str, float]]:
    return {
        key: {model: value for model, value in values.items() if model in eligible}
        for key, values in obs_by_key.items()
    }


def _basket_errors(
    models: Sequence[str],
    raw_m2_and_n: Mapping[str, tuple[float | None, int]],
    obs_by_key: Mapping[object, Mapping[str, float]],
    settle_by_key: Mapping[object, float],
) -> dict[object, float]:
    weights = raw_second_moment_weights(
        {m: raw_m2_and_n.get(m, (None, 0)) for m in models}, unit="C"
    )
    errs: dict[object, float] = {}
    for key in sorted(settle_by_key, key=repr):
        vals = obs_by_key.get(key)
        if not vals or any(m not in vals for m in models):
            continue
        mu = sum(weights.get(m, 0.0) * vals[m] for m in models)
        errs[key] = abs(mu - settle_by_key[key])
    return errs


def greedy_basket(
    candidates: Sequence[str],
    raw_m2_and_n: Mapping[str, tuple[float | None, int]],
    obs_by_key: Mapping[object, Mapping[str, float]],
    settle_by_key: Mapping[object, float],
    *,
    cap: int = BASKET_CAP,
) -> tuple[str, ...] | None:
    """Consult basket-governance greedy selection (simplified, single walk-forward pass):
    start from the best single model; add the candidate with the most negative paired
    mean-delta MAE only if it clears BOTH ``eps=max(0.05C, 0.03*current_MAE)`` and a
    2*SE significance test on the paired per-date delta. Returns the selected model tuple,
    or ``None`` when no candidate has even one paired observation.

    MIN-N CANDIDACY FLOOR: a candidate's own walk-forward n must reach ``MIN_SETTLED_N``
    (the SAME thin-evidence threshold ``raw_second_moment_weights`` uses to EB-shrink a
    model's precision) before it may be picked as the STARTING single model. The 2*SE
    significance test already self-penalizes a thin ADD candidate (SE grows as
    ``1/sqrt(n)``), but the bare argmin used for the starting pick has no such guard —
    without this floor a model with a lucky ~10-observation sample (observed on
    icon_seamless for several cities: n=10-11 vs 80-200+ for the rest of the pool) can
    become the city's ENTIRE served basket on small-sample noise. Fail-soft: if NO
    candidate clears the floor (a uniformly thin candidate pool), fall back to the full
    candidate list rather than returning None."""
    ordered_candidates = sorted(candidates)
    eligible = [
        m for m in ordered_candidates if raw_m2_and_n.get(m, (None, 0))[1] >= MIN_SETTLED_N
    ] or ordered_candidates
    singles: dict[str, float] = {}
    for m in eligible:
        errs = _basket_errors((m,), raw_m2_and_n, obs_by_key, settle_by_key)
        if errs:
            singles[m] = sum(errs.values()) / len(errs)
    if not singles:
        return None
    best = min(eligible, key=lambda m: singles.get(m, math.inf))
    basket: list[str] = [best]
    while len(basket) < cap:
        best_pick = None
        best_delta = 0.0
        basket_families = {provider_family_for_source(model) for model in basket}
        for m in eligible:
            if m in basket or provider_family_for_source(m) in basket_families:
                continue
            trial = tuple(basket) + (m,)
            new_errs = _basket_errors(trial, raw_m2_and_n, obs_by_key, settle_by_key)
            if len(new_errs) < 2:
                continue
            restricted_settle = {k: settle_by_key[k] for k in new_errs}
            base_errs = _basket_errors(tuple(basket), raw_m2_and_n, obs_by_key, restricted_settle)
            if len(base_errs) != len(new_errs):
                continue
            paired_keys = sorted(new_errs, key=repr)
            deltas = [new_errs[k] - base_errs[k] for k in paired_keys]
            n = len(deltas)
            mean_delta = sum(deltas) / n
            var = sum((d - mean_delta) ** 2 for d in deltas) / (n - 1)
            se = math.sqrt(var / n)
            base_mae = sum(base_errs.values()) / n
            eps = max(0.05, 0.03 * base_mae)
            if mean_delta < -eps and mean_delta < -2.0 * se and mean_delta < best_delta:
                best_delta = mean_delta
                best_pick = m
        if best_pick is None:
            break
        basket.append(best_pick)

    # The current-evidence posterior requires independent current values from at
    # least two provider families. A statistically dominant first model is not a
    # reason to publish a one-family entry basket: retain it, then add the best
    # remaining individually-scored servable/domain family deterministically.
    if len({provider_family_for_source(model) for model in basket}) < MIN_ENTRY_PROVIDER_FAMILIES:
        basket_families = {provider_family_for_source(model) for model in basket}
        remaining = [
            model
            for model in eligible
            if model not in basket
            and model in singles
            and provider_family_for_source(model) not in basket_families
        ]
        if remaining:
            basket.append(min(remaining, key=lambda model: (singles[model], model)))
    return tuple(basket)


def _require_entry_provider_count(
    basket: Sequence[str],
    *,
    city: str,
    metric: str,
) -> tuple[str, ...]:
    """Reject an artifact cell that cannot form a live current-evidence shape."""
    sources = tuple(dict.fromkeys(str(model) for model in basket))
    families = tuple(dict.fromkeys(provider_family_for_source(source) for source in sources))
    if len(families) < MIN_ENTRY_PROVIDER_FAMILIES:
        raise ValueError(
            "entry-tradeable source-clock basket requires at least "
            f"{MIN_ENTRY_PROVIDER_FAMILIES} distinct servable/domain provider families: "
            f"city={city!r} metric={metric!r} sources={sources!r} families={families!r}"
        )
    return sources


def city_metric_entry(
    *,
    city: str,
    metric: str,
    obs_by_date: Mapping[str, Mapping[str, float]],
    settle_by_date: Mapping[str, float],
    region_key: str,
    region_basket_cache: dict[
        tuple[str, tuple[str, ...]],
        tuple[tuple[str, ...] | None, dict[str, tuple[float, int]]],
    ],
    region_obs: Mapping[object, Mapping[str, float]],
    region_settle: Mapping[object, float],
    global_raw_m2_and_n: Mapping[str, tuple[float, int]],
    frozen_csv_path: Path,
    eligible_models: frozenset[str],
) -> dict[str, object]:
    n_paired = len(settle_by_date)
    obs_by_date = _filter_models(obs_by_date, eligible_models)
    raw_m2_and_n_city = residual_stats_by_model(obs_by_date, settle_by_date)

    if n_paired >= TIER1_MIN_N:
        tier = "CITY_SPECIFIC"
        basket = greedy_basket(list(raw_m2_and_n_city), raw_m2_and_n_city, obs_by_date, settle_by_date)
        basis = raw_m2_and_n_city
    elif n_paired >= TIER2_MIN_N:
        tier = "REGION_POOLED"
        cache_key = (region_key, tuple(sorted(eligible_models)))
        if cache_key not in region_basket_cache:
            eligible_region_obs = _filter_models(region_obs, eligible_models)
            r_raw_m2 = residual_stats_by_model(eligible_region_obs, region_settle)
            r_basket = greedy_basket(
                list(r_raw_m2), r_raw_m2, eligible_region_obs, region_settle
            )
            region_basket_cache[cache_key] = (r_basket, r_raw_m2)
        basket, basis = region_basket_cache[cache_key]
    else:
        tier = "GLOBAL_CORE"
        basket = tuple(model for model in GLOBAL_CORE_BASKET if model in eligible_models)
        basis = {
            model: stat
            for model, stat in global_raw_m2_and_n.items()
            if model in eligible_models
        }

    # Degrade to the global-core basket when the tiered basket cannot form a live
    # current-evidence shape: either no candidate had a paired observation (None) or the
    # city/region archive holds only ONE provider family for this metric (e.g. a city
    # whose low-metric previous_runs history is ukmo-only) — a single-family cell must
    # fall back, never fail the whole artifact.
    if basket is None or len(
        {provider_family_for_source(model) for model in basket}
    ) < MIN_ENTRY_PROVIDER_FAMILIES:
        basket = tuple(model for model in GLOBAL_CORE_BASKET if model in eligible_models)
        basis = {
            model: stat
            for model, stat in global_raw_m2_and_n.items()
            if model in eligible_models
        }
        tier = "GLOBAL_CORE"

    basket = _require_entry_provider_count(basket, city=city, metric=metric)
    weights = raw_second_moment_weights({m: basis.get(m, (None, 0)) for m in basket}, unit="C")
    published_weights = {m: round(float(w), 6) for m, w in sorted(weights.items())}
    published_weights = {m: w for m, w in published_weights.items() if w > 0.0}
    _require_entry_provider_count(tuple(published_weights), city=city, metric=metric)
    city_errs = _basket_errors(basket, basis, obs_by_date, settle_by_date)
    mae_basket = (sum(city_errs.values()) / len(city_errs)) if city_errs else None

    frozen_errs: dict[str, float] = {}
    for target_date, settle_c in settle_by_date.items():
        vals = obs_by_date.get(target_date, {})
        # W1 (fd7e7b48c) made renormalize-over-present the sole behavior; the old
        # allow_incomplete=True semantics are now the default contract.
        center = fixed_weight_center_from_values(
            city=city, values_c_by_source=vals, path=frozen_csv_path
        )
        if center is not None:
            frozen_errs[target_date] = abs(center.mu_c - settle_c)
    common = sorted(set(city_errs) & set(frozen_errs))
    mae_vs_frozen_delta = (
        (sum(city_errs[d] for d in common) - sum(frozen_errs[d] for d in common)) / len(common)
        if common
        else None
    )

    return {
        "models": published_weights,
        "basket_provenance": {
            "n_paired_dates": n_paired,
            "mae_basket": None if mae_basket is None else round(float(mae_basket), 4),
            "mae_vs_frozen_delta": (
                None if mae_vs_frozen_delta is None else round(float(mae_vs_frozen_delta), 4)
            ),
            "region_fallback": tier != "CITY_SPECIFIC",
            "tier": tier,
        },
    }


def build_artifact(
    conn: sqlite3.Connection,
    *,
    as_of: str | _dt.datetime,
    generated_at: str,
    cities_path: Path,
    frozen_csv_path: Path,
    git_sha: str,
    servable: frozenset[str] | None = LIVE_SERVABLE_MODELS,
    cities_by_name: Mapping[str, object] | None = None,
) -> dict[str, object]:
    cities_cfg = json.loads(cities_path.read_text(encoding="utf-8"))["cities"]
    region_by_city = {str(c["name"]): _region_for_city(c) for c in cities_cfg}

    cutoff = _as_of_utc(as_of)
    loaded = load_walk_forward_rows(
        conn,
        as_of=cutoff,
        servable=servable,
        cities_by_name=cities_by_name,
    )
    obs_all: Mapping[tuple[str, str], Mapping[str, Mapping[str, float]]] = loaded["obs"]
    settle_all: Mapping[tuple[str, str], Mapping[str, float]] = loaded["settle"]
    settlement_rows_used = sum(len(v) for v in settle_all.values())

    # Region + global pools built from the FULL dataset, independent of which cities end up
    # falling back — pooling only fallback cities would arbitrarily shrink the pool.
    region_obs: dict[str, dict[tuple[str, str], dict[str, float]]] = {}
    region_settle: dict[str, dict[tuple[str, str], float]] = {}
    global_obs: dict[str, dict[tuple[str, str, str], dict[str, float]]] = {"high": {}, "low": {}}
    global_settle: dict[str, dict[tuple[str, str, str], float]] = {"high": {}, "low": {}}
    for (city, metric), by_date in obs_all.items():
        region = region_by_city.get(city, "OTHER")
        settle_by_date = settle_all.get((city, metric), {})
        for target_date, models in by_date.items():
            if target_date not in settle_by_date:
                continue
            pair_key = (city, target_date)
            region_key = f"{region}::{metric}"
            region_obs.setdefault(region_key, {})[pair_key] = models
            region_settle.setdefault(region_key, {})[pair_key] = settle_by_date[target_date]
            gkey = (city, metric, target_date)
            global_obs[metric][gkey] = models
            global_settle[metric][gkey] = settle_by_date[target_date]

    global_raw_m2_and_n = {
        metric: residual_stats_by_model(global_obs[metric], global_settle[metric])
        for metric in METRICS
    }
    region_basket_cache: dict[
        tuple[str, tuple[str, ...]],
        tuple[tuple[str, ...] | None, dict[str, tuple[float, int]]],
    ] = {}

    candidate_models = (
        servable
        if servable is not None
        else frozenset(GLOBAL_CORE_BASKET).union(
            model
            for by_date in obs_all.values()
            for values in by_date.values()
            for model in values
        )
    )

    cities_out: dict[str, dict[str, object]] = {}
    for city_cfg in cities_cfg:
        city = str(city_cfg["name"])
        region = region_by_city[city]
        lat = float(city_cfg["lat"])
        lon = float(city_cfg["lon"])
        eligible_models = frozenset(
            model
            for model in candidate_models
            if _model_in_domain(model, lat=lat, lon=lon, lead_days=0)
        )
        per_metric: dict[str, object] = {}
        for metric in METRICS:
            obs_by_date = obs_all.get((city, metric), {})
            settle_by_date = settle_all.get((city, metric), {})
            region_key = f"{region}::{metric}"
            per_metric[metric] = city_metric_entry(
                city=city,
                metric=metric,
                obs_by_date=obs_by_date,
                settle_by_date=settle_by_date,
                region_key=region_key,
                region_basket_cache=region_basket_cache,
                region_obs=region_obs.get(region_key, {}),
                region_settle=region_settle.get(region_key, {}),
                global_raw_m2_and_n=global_raw_m2_and_n[metric],
                frozen_csv_path=frozen_csv_path,
                eligible_models=eligible_models,
            )
        cities_out[city] = per_metric

    return {
        "schema_version": 1,
        "as_of": cutoff.isoformat(),
        "generated_at": generated_at,
        "git_sha": git_sha,
        "settlement_rows_used": settlement_rows_used,
        "training_contract": {
            "name": TRAINING_CONTRACT,
            "fixed_lead_days": TRAINING_LEAD_DAYS,
            "label_authority": "current_resolver_settlement_history",
            "oos_or_economic_advantage_claim": False,
        },
        "exclusion_counts": loaded["excluded_reason_counts"],
        "cities": cities_out,
    }


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), text=True
        ).strip()
    except Exception:
        return "unknown"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fcst", type=Path, default=FCST_DEFAULT)
    p.add_argument("--as-of", required=True, help="UTC ISO-8601 cutoff instant")
    p.add_argument("--generated-at", default=None, help="Override for determinism tests")
    p.add_argument("--cities", type=Path, default=CITIES_DEFAULT)
    p.add_argument("--frozen-csv", type=Path, default=DEFAULT_CITY_ONE_SCHEME_PATH)
    p.add_argument("--out-dir", type=Path, default=OUT_DIR_DEFAULT)
    p.add_argument(
        "--activate", action="store_true",
        help="replace ACTIVE.json with this candidate after separate OOS validation",
    )
    return p.parse_args(argv)


def _artifact_payload_bytes(artifact: object) -> bytes:
    """Serialize exactly as ``write_json_atomic`` will persist the artifact."""
    return json.dumps(artifact, sort_keys=True, indent=2, default=str).encode("utf-8")


def _write_immutable_artifact(path: Path, artifact: object, payload: bytes) -> None:
    """Create a content-addressed artifact or verify its existing immutable bytes."""
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"content-addressed artifact collision at {path}")
        return
    written = write_json_atomic(path, artifact, writer_identity="fit_source_clock_city_weights")
    expected_sha = hashlib.sha256(payload).hexdigest()
    if written["sha256"] != expected_sha:
        raise RuntimeError("atomic artifact serialization checksum mismatch")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = args.generated_at or _dt.datetime.now(_dt.UTC).isoformat()
    conn = sqlite3.connect(f"file:{args.fcst}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        artifact = build_artifact(
            conn,
            as_of=args.as_of,
            generated_at=generated_at,
            cities_path=args.cities,
            frozen_csv_path=args.frozen_csv,
            git_sha=_git_sha(),
        )
    finally:
        conn.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    cutoff = _as_of_utc(args.as_of)
    payload = _artifact_payload_bytes(artifact)
    sha = hashlib.sha256(payload).hexdigest()
    fname = f"city_weights_{cutoff.date().isoformat().replace('-', '')}_{sha}.json"
    artifact_path = args.out_dir / fname
    _write_immutable_artifact(artifact_path, artifact, payload)
    if args.activate:
        pointer = {"artifact": fname, "sha256": sha, "as_of": cutoff.isoformat()}
        write_json_atomic(
            args.out_dir / "ACTIVE.json",
            pointer,
            writer_identity="fit_source_clock_city_weights",
        )
    print(
        f"Wrote candidate {artifact_path} (sha256={sha}); "
        f"settlement_rows_used={artifact['settlement_rows_used']}; activated={args.activate}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
