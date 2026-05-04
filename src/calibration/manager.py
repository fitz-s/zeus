"""Calibration manager: bucket routing, maturity gate, hierarchical fallback.

Spec §3.2-3.4:
- cluster taxonomy comes from src.config, not a local hardcoded list
- Lead_days is Platt INPUT FEATURE, not bucket dimension
- Maturity gate controls regularization strength
- Fallback: cluster+season → season → global → uncalibrated
"""

import logging
from typing import Literal, Optional

import numpy as np

logger = logging.getLogger(__name__)

from src.calibration.platt import ExtendedPlattCalibrator, calibrate_and_normalize
from src.calibration.store import (
    get_pairs_for_bucket,
    get_decision_group_count,
    load_platt_model,
    load_platt_model_v2,
    save_platt_model,
)
from src.config import City, calibration_clusters, calibration_maturity_thresholds
from src.contracts.calibration_bins import F_CANONICAL_GRID, C_CANONICAL_GRID

_EXPECTED_GROUP_ROWS = {"F": F_CANONICAL_GRID.n_bins, "C": C_CANONICAL_GRID.n_bins}


# Slice P3-fix2 (post-review MAJOR from both reviewers, 2026-04-26):
# per-(path, cluster, season, metric) seen-set for v2→legacy fallback
# WARNING dedup. v2 coverage may be sparse → without dedup the alert
# fires every cycle for every uncovered bucket, drowning ops attention.
# With dedup: first occurrence per process lifetime emits the WARNING;
# subsequent identical fallbacks suppress (operator already alerted).
# Module-level set is intentional — survives across get_calibrator
# invocations within the same process.
_V2_FALLBACK_SEEN: set[tuple[str, str, str, str]] = set()


# F1 forward-fix (RERUN_PLAN_v2.md §5, 2026-05-03): config-pinned
# frozen-as-of + per-bucket model_key pins for live Platt loader. Read once
# per process from config/settings.json::calibration::pin (best-effort —
# absence preserves legacy behavior of "newest is_active=VERIFIED row wins").
# Operator updates the config explicitly to bless a new calibrator generation
# so future mass-refits don't silently take over live serving.
_PIN_CONFIG_CACHE: Optional[dict] = None


def get_calibration_pin_config() -> dict:
    """Return cached calibration-pin config from settings.json.

    Shape::

        {
          "frozen_as_of": "2026-05-03 12:00:00" | None,
          "model_keys": { "<temperature_metric>:<cluster>:<season>": "<model_key>", ... }
        }

    Both keys default to safe values (None / empty dict) when settings.json
    has no ``calibration.pin`` section. The cache is populated once per
    process; tests that need to invalidate may set ``_PIN_CONFIG_CACHE``
    back to None.
    """
    global _PIN_CONFIG_CACHE
    if _PIN_CONFIG_CACHE is not None:
        return _PIN_CONFIG_CACHE
    pin: dict = {"frozen_as_of": None, "model_keys": {}}
    try:
        import json as _json
        from pathlib import Path as _Path
        # repo root: src/calibration/manager.py → ../../config/settings.json
        cfg_path = _Path(__file__).resolve().parent.parent.parent / "config" / "settings.json"
        if cfg_path.exists():
            cfg = _json.loads(cfg_path.read_text())
            pin_cfg = (cfg.get("calibration") or {}).get("pin") or {}
            if isinstance(pin_cfg.get("frozen_as_of"), str):
                pin["frozen_as_of"] = pin_cfg["frozen_as_of"]
            if isinstance(pin_cfg.get("model_keys"), dict):
                pin["model_keys"] = dict(pin_cfg["model_keys"])
    except Exception as exc:  # noqa: BLE001 — fail-open to legacy behavior
        logger.warning("calibration pin config load failed: %s; using legacy unpinned behavior", exc)
    _PIN_CONFIG_CACHE = pin
    return pin


def _resolve_pin_for_bucket(
    temperature_metric: str, cluster: str, season: str
) -> tuple[Optional[str], Optional[str]]:
    """Look up (frozen_as_of, model_key) for one bucket.

    Returns (frozen_as_of, model_key) where either may be None to indicate
    "no pin at this layer; default loader behavior applies".
    """
    pin = get_calibration_pin_config()
    key = f"{temperature_metric}:{cluster}:{season}"
    return pin.get("frozen_as_of"), pin.get("model_keys", {}).get(key)


def _emit_v2_legacy_fallback_warning(
    path: str, cluster: str, season: str, metric: str
) -> None:
    """Emit a deduplicated WARNING when v2 misses and legacy fills.

    `path` is one of "primary" (cluster+season primary bucket) or
    "season-pool" (season-only fallback to a different cluster). The
    leading `v2_to_legacy_fallback path=...` token is stable so log
    aggregators can group both paths into the same logical event family
    while still distinguishing the originating fallback site.
    """
    key = (path, cluster, season, metric)
    if key in _V2_FALLBACK_SEEN:
        return
    _V2_FALLBACK_SEEN.add(key)
    logger.warning(
        "v2_to_legacy_fallback path=%s cluster=%s season=%s metric=%s "
        "v2 missed; serving legacy platt_models. Operator review v2 "
        "coverage gap (per-bucket dedup — first occurrence only).",
        path, cluster, season, metric,
    )


def lat_for_city(city_name: str) -> float:
    """Look up latitude for a city by name. Returns 90.0 (NH default) if not found."""
    from src.config import cities_by_name
    city = cities_by_name.get(city_name)
    return city.lat if city else 90.0


def bucket_key(cluster: str, season: str) -> str:
    """Canonical bucket key for storage."""
    return f"{cluster}_{season}"


# G10 calibration-fence (2026-04-26, con-nyx NICE-TO-HAVE #4): season helpers
# moved to src.contracts.season so the ingest lane (scripts/ingest/*) can call
# them without transitively pulling src.calibration into the import graph.
# Re-exported here for back-compat — existing callers (harvester.py,
# observation_client.py, replay.py, this module's own L85/L159/L354) keep
# working unchanged. _SH_FLIP also re-exported for any subclass/extension
# that depended on the symbol.
from src.contracts.season import (  # noqa: F401  (re-export)
    _SH_FLIP,
    hemisphere_for_lat,
    season_from_date,
    season_from_month,
)


def route_to_bucket(city: City, target_date: str) -> str:
    """Route a city + date to its calibration bucket key."""
    season = season_from_date(target_date, lat=city.lat)
    return bucket_key(city.name, season)


def maturity_level(n_pairs: int) -> int:
    """Determine calibration maturity level from sample count.

    Spec §3.3:
    Level 1: n >= 150 → standard Platt (C=1.0), edge threshold 1×
    Level 2: 50 <= n < 150 → standard Platt (C=1.0), edge threshold 1.5×
    Level 3: 15 <= n < 50 → strong regularization (C=0.1), edge threshold 2×
    Level 4: n < 15 → no Platt (use P_raw), edge threshold 3×
    """
    level1, level2, level3 = calibration_maturity_thresholds()
    if n_pairs >= level1:
        return 1
    elif n_pairs >= level2:
        return 2
    elif n_pairs >= level3:
        return 3
    else:
        return 4


def regularization_for_level(level: int) -> float:
    """Get sklearn LogisticRegression C parameter for maturity level."""
    if level <= 2:
        return 1.0
    elif level == 3:
        return 0.1
    else:
        raise ValueError(f"Level {level}: no Platt — use P_raw directly")


def edge_threshold_multiplier(level: int) -> float:
    """Edge threshold multiplier by calibration maturity. Spec §3.3."""
    return {1: 1.0, 2: 1.5, 3: 2.0, 4: 3.0}[level]


def get_calibrator(
    conn,
    city: City,
    target_date: str,
    temperature_metric: Literal["high", "low"] = "high",
    *,
    cycle: Optional[str] = None,
    source_id: Optional[str] = None,
    horizon_profile: Optional[str] = None,
) -> tuple[Optional[ExtendedPlattCalibrator], int]:
    """Get the best available calibrator for a city+date+metric.

    Phase 2 (2026-05-04, may4math.md F1 + critic-opus BLOCKER 3): added
    cycle/source_id/horizon_profile keyword params for cycle-stratified Platt
    bucket selection. When all three are None (default), legacy behavior is
    preserved — load_platt_model_v2 hits the schema-default bucket (00z TIGGE
    full horizon). Production callers (evaluator) MUST thread non-None values
    derived from the forecast's actual provenance (issue_time → cycle,
    data_version → source_id, registry → horizon_profile) so that 12z OpenData
    forecasts no longer silently use 00z TIGGE-trained calibration.

    Phase 9C L3 CRITICAL (2026-04-18): added `temperature_metric` param +
    metric-aware hierarchical fallback. Pre-P9C, this function was metric-
    blind and read exclusively from legacy `platt_models` table — a LOW
    candidate would silently receive a HIGH Platt model. Post-P9C:

      1. Try platt_models_v2 filtered by (temperature_metric, cluster, season,
         data_version, cycle, source_id, horizon_profile)
      2. If v2 miss, fall back to legacy platt_models (HIGH historical continuity)
      3. Remaining hierarchical fallback (pool clusters / seasons / global) is
         preserved; v2 lookup is tried first at each tier.

    Law: docs/authority/zeus_dual_track_architecture.md §4 (World DB v2 table
    family keyed on temperature_metric). Writes to platt_models_v2 landed
    Phase 5 (save_platt_model_v2 + refit_platt_v2.py); reads were unwired
    until Phase 9C.

    Implements hierarchical fallback (spec §3.4):
    1. cluster+season (primary bucket)
    2. season-only (pool all clusters)
    3. global (pool everything)
    4. None (uncalibrated — use P_raw)

    Returns: (calibrator_or_None, maturity_level)
    """
    # S3 R5 P10B: runtime enforcement at get_calibrator entry point
    assert temperature_metric in ("high", "low"), (
        f"Invalid temperature_metric: {temperature_metric!r}"
    )
    season = season_from_date(target_date, lat=city.lat)
    cluster = city.cluster

    # 2026-04-30 BLOCKER #1 fix: resolve canonical data_version for the metric
    # so load_platt_model_v2 filters by it. Pre-fix the SELECT picked newest
    # by fitted_at regardless of data_version — invariant-by-coincidence under
    # today's single-version-per-metric world; future metric upgrades would
    # silently shift runtime to the new fit. The data_version constants live
    # in MetricIdentity (metric_identity.py:78-90) and are imported lazily to
    # avoid pulling the typed-atom module into every test that touches the
    # calibration manager.
    #
    # Phase 2.6 (2026-05-04): source-family-aware data_version resolution.
    # When caller passes ``source_id``, derive the matching data_version per
    # the source-family registry — so OpenData live forecasts hit OpenData
    # Platt buckets, not TIGGE-trained ones (BLOCKER 3 fix). Fallback to
    # legacy TIGGE-only constants when source_id is None (back-compat).
    from src.types.metric_identity import (
        HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN, MetricIdentity,
    )
    if source_id is not None:
        # Map source_id back to a source_family that MetricIdentity knows about.
        if source_id == "tigge_mars":
            _source_family = "tigge"
        elif source_id == "ecmwf_open_data":
            _source_family = "ecmwf_opendata"
        else:
            _source_family = None
        if _source_family is not None:
            try:
                expected_data_version = (
                    MetricIdentity.for_metric_with_source_family(
                        temperature_metric, _source_family
                    ).data_version
                )
            except ValueError:
                # Unknown family at this layer → fall back to legacy TIGGE
                # constants. Caller should still rule out unknown sources at
                # an earlier rejection gate (UNKNOWN_FORECAST_SOURCE_FAMILY).
                expected_data_version = (
                    HIGH_LOCALDAY_MAX.data_version
                    if temperature_metric == "high"
                    else LOW_LOCALDAY_MIN.data_version
                )
        else:
            # source_id provided but unknown — legacy fallback (no live route
            # for this source through cycle-stratified Platt).
            expected_data_version = (
                HIGH_LOCALDAY_MAX.data_version
                if temperature_metric == "high"
                else LOW_LOCALDAY_MIN.data_version
            )
    else:
        expected_data_version = (
            HIGH_LOCALDAY_MAX.data_version
            if temperature_metric == "high"
            else LOW_LOCALDAY_MIN.data_version
        )

    # F1 (2026-05-03): resolve config-pinned frozen_as_of + model_key for this
    # bucket. Both default to None → legacy behavior preserved.
    primary_frozen, primary_model_key = _resolve_pin_for_bucket(
        temperature_metric, cluster, season
    )

    # Try primary bucket — v2 FIRST (metric-aware), then legacy (HIGH BC).
    # Phase 2 (2026-05-04): thread cycle/source_id/horizon_profile into v2 load.
    model_data = load_platt_model_v2(
        conn,
        temperature_metric=temperature_metric,
        cluster=cluster,
        season=season,
        data_version=expected_data_version,
        frozen_as_of=primary_frozen,
        model_key=primary_model_key,
        cycle=cycle,
        source_id=source_id,
        horizon_profile=horizon_profile,
    )
    if model_data is None and temperature_metric == "high":
        # Legacy fallback only for HIGH — LOW has never existed in legacy
        bk = bucket_key(cluster, season)
        model_data = load_platt_model(conn, bk)
        # Slice P3.4 + P3-fix2 (post-review MAJOR from both reviewers,
        # 2026-04-26): operator-visible WARNING when v2 misses and legacy
        # fills, deduplicated per-(path,cluster,season,metric) for the
        # process lifetime. v2 coverage may be sparse → first cycle alerts
        # operator; subsequent cycles for the same bucket suppress to
        # avoid log spam (one fact, one alert).
        if model_data is not None:
            _emit_v2_legacy_fallback_warning("primary", cluster, season, "high")
    if model_data is not None:
        if model_data.get("input_space") != "width_normalized_density":
            refit = _fit_from_pairs(
                conn, cluster, season, unit=city.settlement_unit,
                temperature_metric=temperature_metric,
            )
            if refit is not None:
                level = maturity_level(refit.n_samples)
                return refit, level
            logger.warning(
                "Ignoring stale raw-probability Platt model for %s; "
                "width-normalized refit unavailable",
                bk,
            )
        else:
            cal = _model_data_to_calibrator(model_data)
            level = maturity_level(model_data["n_samples"])
            return cal, level

    # Maturity threshold is needed by both the HIGH on-the-fly path AND the
    # season-only fallback loop below; bind it once before the HIGH branch
    # so LOW callers (which skip the on-the-fly attempt) still have it
    # available at L225. Slice A2-fix1 (post-review BLOCKER from
    # code-reviewer 2026-04-26): pre-fix kept this binding inside the HIGH
    # branch and crashed LOW callers with UnboundLocalError when a v2
    # fallback model existed in another cluster's bucket.
    _, _, level3 = calibration_maturity_thresholds()

    # Check if we have enough pairs to fit on the fly.
    # Phase 9C.1 + slice A2 (PR #19 followup, 2026-04-26): on-the-fly refit
    # is HIGH-only because legacy calibration_pairs has no temperature_metric
    # column ("LOW has never existed in legacy" per Phase 9C L3). For LOW
    # callers, skip the count + fit attempt and fall through to v2/fallback
    # paths directly. This avoids a metric-blind count whose result would be
    # discarded anyway (`_fit_from_pairs` short-circuits on non-HIGH at L267).
    if temperature_metric == "high":
        n = get_decision_group_count(conn, cluster, season, metric="high")
        if n >= level3:
            cal = _fit_from_pairs(
                conn, cluster, season, unit=city.settlement_unit,
                temperature_metric=temperature_metric,
            )
            if cal is not None:
                level = maturity_level(n)
                return cal, level

    # Fallback: season-only (pool all clusters). v2 FIRST per metric,
    # legacy only for HIGH backward compat (Phase 9C L3).
    for fallback_cluster in calibration_clusters():
        if fallback_cluster == cluster:
            continue
        fb_frozen, fb_model_key = _resolve_pin_for_bucket(
            temperature_metric, fallback_cluster, season
        )
        model_data = load_platt_model_v2(
            conn,
            temperature_metric=temperature_metric,
            cluster=fallback_cluster,
            season=season,
            data_version=expected_data_version,
            frozen_as_of=fb_frozen,
            model_key=fb_model_key,
        )
        if model_data is None and temperature_metric == "high":
            bk_fb = bucket_key(fallback_cluster, season)
            model_data = load_platt_model(conn, bk_fb)
            # Slice P3.4 + P3-fix2: twin-site dedup per-bucket WARNING.
            if model_data is not None:
                _emit_v2_legacy_fallback_warning(
                    "season-pool", fallback_cluster, season, "high",
                )
        if model_data is not None and model_data["n_samples"] >= level3:
            if model_data.get("input_space") != "width_normalized_density":
                logger.warning(
                    "Skipping stale raw-probability fallback Platt model for %s_%s",
                    fallback_cluster, season,
                )
                continue
            cal = _model_data_to_calibrator(model_data)
            level = maturity_level(model_data["n_samples"])
            return cal, max(level, 3)  # Fallback is at most level 3

    # Level 4: no calibrator available
    return None, 4


def _model_data_to_calibrator(model_data: dict) -> ExtendedPlattCalibrator:
    """Reconstruct calibrator from stored model data."""
    cal = ExtendedPlattCalibrator()
    cal.A = model_data["A"]
    cal.B = model_data["B"]
    cal.C = model_data["C"]
    cal.n_samples = model_data["n_samples"]
    cal.fitted = True
    cal.bootstrap_params = [
        tuple(p) for p in model_data["bootstrap_params"]
    ]
    cal.input_space = model_data.get("input_space", "raw_probability")
    return cal


def _fit_from_pairs(
    conn, cluster: str, season: str, *, unit: str | None = None,
    temperature_metric: Literal["high", "low"] = "high",
) -> Optional[ExtendedPlattCalibrator]:
    """Fit a new calibrator from stored pairs.

    Phase 9C.1 ITERATE-fix (critic-dave cycle-1 MAJOR-2 "latent bomb"):
    on-the-fly refit is HIGH-only. LOW on-the-fly would call the legacy
    metric-blind `save_platt_model` below, polluting `platt_models` with
    a LOW-fitted model that a future HIGH v2-miss could silently read
    back through the legacy-fallback branch at L165-168 — a two-seam
    violation (write-side twin of the L3 read-side fix).

    LOW refits must land via the dedicated v2 pipeline
    (scripts/refit_platt_v2.py → save_platt_model_v2), which is
    Golden-Window-gated. Fast-path on-the-fly refit is unsafe for LOW
    until a metric-aware on-the-fly-to-v2 writer is added (post-dual-
    track cleanup packet).
    """
    if temperature_metric != "high":
        logger.debug(
            "_fit_from_pairs skipped for %s_%s (temperature_metric=%s): "
            "on-the-fly refit is HIGH-only per Phase 9C.1 two-seam law. "
            "LOW refits must use scripts/refit_platt_v2.py.",
            cluster, season, temperature_metric,
        )
        return None
    # Slice A2 (PR #19 followup, 2026-04-26): metric="high" is structurally
    # required because the gate at L267 already short-circuits non-HIGH;
    # passing it explicitly makes the implicit invariant visible at the
    # read seam and satisfies the store-side enforcement landed in slice A1.
    pairs = get_pairs_for_bucket(
        conn, cluster, season, bin_source_filter="canonical_v1", metric="high",
    )
    _, _, level3 = calibration_maturity_thresholds()
    if len(pairs) < level3:
        return None

    decision_group_ids = np.array([p.get("decision_group_id") for p in pairs], dtype=object)
    if any(group_id is None or str(group_id) == "" for group_id in decision_group_ids):
        logger.warning("Platt fit refused for %s_%s: missing decision_group_id", cluster, season)
        return None
    n_eff = len({str(group_id) for group_id in decision_group_ids})
    if n_eff < level3:
        return None
    if not _canonical_pair_groups_valid(pairs, unit=unit):
        logger.warning("Platt fit refused for %s_%s: invalid canonical group shape", cluster, season)
        return None

    p_raw = np.array([p["p_raw"] for p in pairs])
    if not np.isfinite(p_raw).all() or np.any((p_raw < 0.0) | (p_raw > 1.0)):
        logger.warning("Platt fit refused for %s_%s: p_raw outside [0, 1]", cluster, season)
        return None
    lead_days = np.array([p["lead_days"] for p in pairs])
    outcomes = np.array([p["outcome"] for p in pairs])
    bin_widths = np.array([p.get("bin_width") for p in pairs], dtype=object)

    level = maturity_level(n_eff)
    reg_C = regularization_for_level(level)

    cal = ExtendedPlattCalibrator()
    try:
        cal.fit(
            p_raw,
            lead_days,
            outcomes,
            bin_widths=bin_widths,
            decision_group_ids=decision_group_ids,
            regularization_C=reg_C,
        )
    except Exception as e:
        logger.warning("Platt fit failed for %s_%s: %s", cluster, season, e)
        return None

    # Save to DB for future use
    bk = bucket_key(cluster, season)
    save_platt_model(
        conn, bk,
        cal.A, cal.B, cal.C,
        cal.bootstrap_params,
        cal.n_samples,
        input_space=cal.input_space,
    )
    conn.commit()

    return cal


def _canonical_pair_groups_valid(pairs: list[dict], *, unit: str | None = None) -> bool:
    expected_rows = _EXPECTED_GROUP_ROWS.get(unit) if unit else None
    groups: dict[str, dict] = {}
    for pair in pairs:
        group_id = str(pair.get("decision_group_id") or "")
        group = groups.setdefault(group_id, {"rows": 0, "positives": 0, "labels": set()})
        group["rows"] += 1
        group["positives"] += int(pair.get("outcome") == 1)
        group["labels"].add(str(pair.get("range_label")))
    for group in groups.values():
        if expected_rows is not None:
            if group["rows"] != expected_rows:
                return False
        elif group["rows"] not in (92, 102):
            return False
        if group["positives"] != 1:
            return False
        if len(group["labels"]) != group["rows"]:
            return False
    return True


def maybe_refit_bucket(conn, city: City, target_date: str) -> bool:
    """Refit the city's cluster-season bucket if enough fresh pairs now exist."""
    season = season_from_date(target_date, lat=city.lat)
    cal = _fit_from_pairs(conn, city.cluster, season, unit=city.settlement_unit)
    return cal is not None
