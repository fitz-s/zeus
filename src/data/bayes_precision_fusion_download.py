# Created: 2026-06-08
# Last reused or audited: 2026-06-13
# Authority basis: BAYES_PRECISION_FUSION_SPEC.md §6 F1 (raw capture: previous_runs + single_runs ->
#   raw_model_forecasts), §3 (causality: previous-runs fixed-lead; single-runs live capture;
#   run_time != source_available_at), §5 (~6mo retention); §7 antibodies (C/F unit mix ->
#   force celsius; fail-soft drop). CONTINUITY_AND_WIRING.md §4 steps 2-3, 9 (forward+history
#   daily download/persist + 180d prune). IRON RULE #4 (ONE-BUILDER: REUSE the existing OM
#   fetchers + OPENMETEO_MODEL_IDS; no parallel fetcher). INV-37: a SINGLE zeus-forecasts.db
#   connection, single BEGIN/commit; no cross-DB write.
#   API-COLLAPSE 2026-06-13: K=4 structural redundancies eliminated — model-batching (R2),
#   metric-fold (R1), cycle-cadence gate (R3), immutable-previous_runs skip (R4a).
#   Reduces ~9.5-12k HTTP/day to ~600-900. q-path byte-identical (same rows, fewer fetches).
"""F1 step-2/3/9 — the FORWARD + walk-forward BAYES_PRECISION_FUSION multi-model download/persist job.

For each current-target (city, metric, target_date, lead) x the extra Open-Meteo models
(decorrelated globals icon_global/ukmo_global + icon_eu + the domain-gated CONUS/N-America nests
ncep_nbm/gfs_hrrr/gem_hrdps + in-domain regionals icon_d2/arome/ukmo_uk), this job:
(2026-06-17: the coarse globals gfs_global 25km / gem_global 15km, the settlement-cold
jma_seamless, AND the alias-dedup probe icon_seamless were all DROPPED from the fusion; they are
no longer fetched here.)

  (1) FORWARD single_runs fetch  — today's current-target value at the fixed cycle (live capture
      for replay; SPEC §3 single-runs identity). REUSES bayes_precision_fusion_capture._default_live_fetch.
  (2) fixed-lead previous_runs fetch — the no-leak walk-forward train value via the OM
      previous-runs API temperature_2m_previous_dayN hourly var (SPEC §3 fixed-lead). Forces
      temperature_unit=celsius (forecast_value_c is ALWAYS degC -> SPEC §7 C/F unit-mix antibody).
  (3) INSERTs the surviving rows into raw_model_forecasts (raw live input, training_allowed=0),
      on a SINGLE zeus-forecasts.db connection (INV-37), UNIQUE-idempotent per cycle.
  (4) PRUNES rows older than the retention cutoff (~180d, SPEC §5) in the same transaction.

FAIL-SOFT IS STRUCTURAL: a per-model fetch failure (raise OR None) DROPS that model's row and
the job proceeds with the survivors — a dropped model is simply absent (the fusion handles
missing sources by construction). The job is a pure side-effect on raw_model_forecasts: it
writes NOTHING into forecast_posteriors and touches NO posterior/q/center/spread/order, so the
money path is byte-identical whether or not this job runs (gated by the SEPARATE capture flag).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence

from src.data.bayes_precision_fusion_capture import (
    OPENMETEO_MODEL_IDS,
    OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
    _default_live_fetch,
)
from src.forecast.model_selection import (
    ANCHOR_MODEL,
    GLOBAL_LIKELIHOOD_MODELS,
    ICON_EU_MODEL,
    REGIONAL_MODELS,
    regional_eligible,
)

_LOG = logging.getLogger("zeus.bayes_precision_fusion_download")

# SPEC §5: ~6 months retention on the raw input capture table.
RETENTION_DAYS = 180


class RawModelForecastRequestConflict(RuntimeError):
    """BLOCKER 4 (operator-sharpened) — a same-logical-key insert arrived with a DIFFERENT
    physical request identity (a corrected request: changed timezone / cell_selection / elevation
    / product_id / request_url_hash).

    The pre-fix UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint) + INSERT OR
    IGNORE SILENTLY discarded such a Run-2, leaving a STALE forecast_value_c to contaminate
    bias/MAE/sigma/covariance/q in the walk-forward history JOIN (bayes_precision_fusion_history_provider keys on
    model/city/metric/lead/endpoint/target_date — NOT on the request hash, so a stale row poisons
    the residual series). This exception replaces the silent drop with a LOUD, attributable fault:
    the persist writes an audit row to raw_model_forecast_request_conflicts and raises, so an
    operator re-pins the request identity rather than a wrong value silently training a live-money
    posterior. The logical key cannot bind two physical requests."""


# The logical-key columns that, together, identified a row under the PRE-fix UNIQUE. Two rows that
# match on ALL of these but DIFFER on (product_id, request_url_hash) are a request conflict.
_RMF_LOGICAL_KEY_COLUMNS = (
    "model", "city", "target_date", "metric", "source_cycle_time", "endpoint",
)

# BLOCKER 4 (product identity): the provider + per-endpoint physical-product constants that the
# download stamps onto every raw_model_forecasts row so a stored forecast_value_c is
# reconstructable to its exact Open-Meteo product. cell_selection / elevation / downscaling are
# the OM grid choices the BAYES_PRECISION_FUSION fetchers use (the single-runs anchor pattern: nearest gridpoint,
# requested elevation, no extra downscaling). endpoint_mode is the physical endpoint family.
OPENMETEO_PROVIDER = "open-meteo"
SINGLE_RUNS_SOURCE_FAMILY = "openmeteo_single_runs"
PREVIOUS_RUNS_SOURCE_FAMILY = "openmeteo_previous_runs"
# 2026-06-17 CELL-SELECTION FIX (operator "fix the math, not a hardcoded value"): the prior
# "nearest" pick snapped coastal airports to the nearest OFFSHORE grid cell, so the model
# returned the SEA-surface temperature (cold by day) instead of the airport's land surface — the
# systematic cold drag. "land" picks the nearest LAND gridpoint (OM prefers >50%-land cells), i.e.
# the model's value AT the airport, not over water. This is a DATA-precision fix (finer data
# closer to the airport), NOT a de-bias / fitted offset. Settlement-graded proof (ecmwf_ifs, all
# cities, high+low, last 10 settled days, n=452): pooled MAE 1.121 -> 0.996 (-0.125, -11%); cold
# bias -0.595 -> -0.423; worst offender Tokyo high -4.09 -> -1.34. Inland airports are unaffected
# (nearest IS land there). cell_selection is part of the BLOCKER-4 product identity, so the land
# captures accumulate their OWN de-bias history (never mixed with the legacy nearest history).
BAYES_PRECISION_FUSION_CELL_SELECTION = "land"             # nearest LAND gridpoint (airport surface, not offshore sea cell).
BAYES_PRECISION_FUSION_ELEVATION_PARAM = "requested"       # OM elevation = requested point (no override).
BAYES_PRECISION_FUSION_DOWNSCALING_POLICY = "none"         # no statistical downscaling applied to the raw value.

# Per-model OM previous-runs source_id (the WHICH-feed identity). Keyed by STORED model identity.
# The anchor is stored model='ecmwf_ifs' but its OM previous-runs source is ecmwf_previous_runs
# serving model_name='ecmwf_ifs025' (0.25 product) — see OPENMETEO_PREVIOUS_RUNS_MODEL_IDS below
# and BLOCKER 3 (the ifs025->ifs9 bridge). Falls back to '<model>_previous_runs'.
OPENMETEO_PREVIOUS_RUNS_SOURCE_ID: dict[str, str] = {
    ANCHOR_MODEL: "ecmwf_previous_runs",
    # 2026-06-17: gfs_global/gem_global/jma_seamless were dropped from the FORWARD fusion, but
    # these previous-runs routing entries are RETAINED — they are the de-bias-HISTORY layer (the
    # same class as the kept ecmwf_previous_runs / gfs_previous_runs / gem_previous_runs / jma_
    # previous_runs registry specs), and resolve the product-identity of the existing history rows
    # as they age out. They are not forward-fetch surface.
    "gfs_global": "gfs_previous_runs",
    "icon_global": "icon_previous_runs",
    "icon_eu": "icon_previous_runs",
    "gem_global": "gem_previous_runs",
    "jma_seamless": "jma_previous_runs",
    "icon_d2": "icon_d2_previous_runs",
    "meteofrance_arome_france_hd": "arome_previous_runs",
    # icon_seamless was REMOVED 2026-06-17 (alias-dedup probe, no longer fetched). The previous-
    # runs routing entry is retained so any remaining history rows resolve their product-identity
    # (they age out under the 180d retention). Not a forward-fetch surface.
    "icon_seamless": "icon_d2_previous_runs",
    # 2026-06-17 PRECISION-INPUT FIX: high-res CONUS / N-America regional experts. The OM
    # previous-runs API serves both under their single-runs id (curl-verified 2026-06-17), so the
    # source_id is the conventional '<model>_previous_runs' feed identity.
    "gfs_hrrr": "gfs_hrrr_previous_runs",
    "gem_hrdps_continental": "gem_hrdps_continental_previous_runs",
}


def _model_domain_hash(
    *,
    provider: str,
    model_name: str,
    cell_selection: str,
    elevation_param: str,
    downscaling_policy: str,
    endpoint_mode: str,
) -> str:
    """BLOCKER 4 — fingerprint binding the physical-product identity of a captured value.

    Two captures that differ in ANY of (provider, model_name, cell_selection, elevation_param,
    downscaling_policy, endpoint_mode) are DIFFERENT physical products and get different hashes,
    so a residual history can never silently mix two cells / two model resolutions under one id.
    """
    payload = json.dumps(
        {
            "provider": provider,
            "model_name": model_name,
            "cell_selection": cell_selection,
            "elevation_param": elevation_param,
            "downscaling_policy": downscaling_policy,
            "endpoint_mode": endpoint_mode,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bayes_precision_fusion_product_identity(model: str, endpoint: str, target: "BayesPrecisionFusionDownloadTarget") -> dict:
    """BLOCKER 4 — the full product-identity payload for one (model, endpoint, target) capture.

    Resolves the OM model id actually addressed (model_name), the source_id/source_family/
    product_id/provider, the requested coordinates+timezone, the cell/elevation/downscaling
    choices, the endpoint_mode, the request params (the reconstructable request) + its url hash,
    and the model_domain_hash. The anchor's model_name is the OM previous-runs id 'ecmwf_ifs025'
    even though the stored `model` column stays the fusion identity 'ecmwf_ifs' (BLOCKER 3).
    """
    if endpoint == "previous_runs":
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL  # noqa: PLC0415

        model_name = OPENMETEO_PREVIOUS_RUNS_MODEL_IDS.get(
            model, OPENMETEO_MODEL_IDS.get(model, model)
        )
        source_family = PREVIOUS_RUNS_SOURCE_FAMILY
        source_id = OPENMETEO_PREVIOUS_RUNS_SOURCE_ID.get(model, f"{model}_previous_runs")
        base_url = PREVIOUS_RUNS_URL
        lead = max(0, int(target.lead_days))
        hourly_var = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
        request_params = {
            "latitude": target.latitude,
            "longitude": target.longitude,
            "start_date": target.target_date,
            "end_date": target.target_date,
            "hourly": hourly_var,
            "models": model_name,
            "temperature_unit": "celsius",
            "timezone": target.timezone_name,
            "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
        }
    else:  # single_runs
        from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
            SINGLE_RUNS_FORECAST_URL,
        )

        model_name = OPENMETEO_MODEL_IDS.get(model, model)
        source_family = SINGLE_RUNS_SOURCE_FAMILY
        source_id = f"{model}_single_runs"
        base_url = SINGLE_RUNS_FORECAST_URL
        request_params = {
            "latitude": target.latitude,
            "longitude": target.longitude,
            "hourly": "temperature_2m",
            "models": model_name,
            "temperature_unit": "celsius",
            "timezone": target.timezone_name,
            "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
        }
    request_params_json = json.dumps(request_params, sort_keys=True, separators=(",", ":"))
    request_url_hash = hashlib.sha256(
        f"{base_url}?{request_params_json}".encode("utf-8")
    ).hexdigest()
    product_id = f"{model_name}::{endpoint}"
    model_domain_hash = _model_domain_hash(
        provider=OPENMETEO_PROVIDER,
        model_name=model_name,
        cell_selection=BAYES_PRECISION_FUSION_CELL_SELECTION,
        elevation_param=BAYES_PRECISION_FUSION_ELEVATION_PARAM,
        downscaling_policy=BAYES_PRECISION_FUSION_DOWNSCALING_POLICY,
        endpoint_mode=endpoint,
    )
    return {
        "source_id": source_id,
        "source_family": source_family,
        "product_id": product_id,
        "provider": OPENMETEO_PROVIDER,
        "model_name": model_name,
        "request_params_json": request_params_json,
        "request_url_hash": request_url_hash,
        "latitude_requested": float(target.latitude),
        "longitude_requested": float(target.longitude),
        "timezone_requested": target.timezone_name,
        "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
        "elevation_param": BAYES_PRECISION_FUSION_ELEVATION_PARAM,
        "downscaling_policy": BAYES_PRECISION_FUSION_DOWNSCALING_POLICY,
        "endpoint_mode": endpoint,
        "model_domain_hash": model_domain_hash,
        "coverage_status": "COVERED",
    }

# FIX 1 (live-money correctness): the ANCHOR (ecmwf_ifs) MUST be captured alongside the
# likelihood instruments. Without it, raw_model_forecasts NEVER accrues anchor previous_runs
# rows -> BayesPrecisionFusionHistoryProvider returns no anchor history -> the fusion's have_anchor is False ->
# the posterior is stuck at EQUAL_WEIGHT forever (the prior is never formed). The anchor is the
# FIRST element so its row provenance is unambiguous in the candidate ordering.
#
# The full capture set: anchor (prior) + globals + icon_eu (likelihood) + in-domain regionals.
# 2026-06-17: icon_seamless was REMOVED — it was the alias-dedup probe (bit-identical to icon_d2,
# contributing no decorrelated information); it is no longer fetched or fused.
BAYES_PRECISION_FUSION_EXTRA_MODELS: tuple[str, ...] = (
    (ANCHOR_MODEL,)
    + tuple(GLOBAL_LIKELIHOOD_MODELS)
    + tuple(REGIONAL_MODELS)
)

# CANDIDATE-ACCRUAL LANE (2026-06-09 regional survey /tmp/uncovered_cities_regional_report.md,
# settlement-graded). These models accrue raw data (forward single_runs + fixed-lead
# previous_runs, both curl-verified available 2026-06-09) so a future promotion has walk-forward
# history — they are NOT in BAYES_PRECISION_FUSION_EXTRA_MODELS' selection inputs and select_models NEVER admits
# them into a fusion (REGIONAL_MODELS / GLOBAL_LIKELIHOOD_MODELS unchanged).
#   ncep_nbm_conus               — CONUS, pooled MAE 1.193 vs ecmwf_ifs 1.395 (-14.4%, n=1029).
#                                  Role candidate: CONUS ANCHOR replacement. NBM blends NCEP
#                                  models incl. GFS -> must NEVER join the decorrelated globals.
#   ukmo_global_deterministic_10km — global, pooled MAE 1.266 vs ecmwf_ifs 1.411 (-10.3%,
#                                  n=1099, 16 non-EU cities; strongest SE-Asia/tropics). Role
#                                  candidate: 5th decorrelated global (distinct dynamical core).
#                                  Weak South Asia (Karachi/Lucknow warm bias) — anchor role
#                                  excluded there.
#   ukmo_uk_deterministic_2km    — UK-only, London MAE 0.919 vs 1.039 (n=112). Role candidate:
#                                  London regional expert (icon_d2 pattern).
# Domain gating: nbm + ukmo_uk have their own polygons (config/model_domain_polygons.yaml);
# ukmo_global is worldwide. Promotion requires forward-shadow validation, never in-sample.
# 2026-06-09 SAME-DAY PROMOTION (operator-directed): all three candidates were promoted into
# the selection sets (model_selection.py — ukmo_global into DECORR_GLOBALS, ncep_nbm into
# GLOBAL_LIKELIHOOD_MODELS via the NCEP family contest, ukmo_uk into REGIONAL_MODELS), so they
# now ride BAYES_PRECISION_FUSION_EXTRA_MODELS automatically. The lane stays for FUTURE candidates; keep it empty
# rather than deleting the mechanism (any model listed here must NOT also be in the selection
# sets, or the download loop would fetch it twice).
BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS: tuple[str, ...] = ()

# K2 (2026-06-09, curl-verified): models the open-meteo single-runs API STRUCTURALLY cannot
# serve. cmc_gem_gdps_15km returns modelRunUnavailable even for cadence-valid 00z/12z runs —
# the product is simply not in the single-runs archive. The forward single_runs request leg is
# skipped for these (no known-dead requests, no 51-cities-per-cycle fail-soft noise); their
# CURRENT value is served from the previous_runs row at the same natural key (the SAME physical
# product their walk-forward de-bias history is fit on) via the materializer's declared
# gem exception in _read_persisted_current_capture. gem_seamless was REJECTED as a substitute:
# it serves HRDPS/RDPS for North-American cities — a different physical product than the GDPS
# history (the source-identity violation class of the EB-bias wrong-set bug ff7f33dd5b).
# 2026-06-17: gem_global (the only member) was dropped from the fusion, so no fetched model is
# single-runs-unservable any more — this list is now empty (the previous_runs-substitution path it
# guarded is still exercised by the surviving providers).
SINGLE_RUNS_UNSERVABLE_MODELS: tuple[str, ...] = ()

# R3 — Per-model run cadence: the UTC init hours each provider actually publishes. Fetching a model
# at a non-publishing cycle re-pulls the SAME underlying run under a wrong source_cycle_time.
# Models not listed here default to all four {0,6,12,18}. NOTE (2026-06-17): jma_seamless and
# gem_global — the ONLY restricted-cadence models — were dropped from the fusion, so this gate is
# currently DORMANT (no fetched model is listed). The entries are kept as ACCURATE provider-cadence
# reference (and are pinned by test_openmeteo_call_budget); they re-arm automatically if a restricted-
# cadence model is ever re-added.
MODEL_PUBLISH_CYCLE_HOURS: dict[str, frozenset[int]] = {
    "jma_seamless": frozenset({0, 12}),   # JMA GSM/seamless init 00/12Z only
    "gem_global":   frozenset({0, 12}),   # CMC GDPS 00/12Z only
}
_ALL_CYCLES: frozenset[int] = frozenset({0, 6, 12, 18})


def _model_publishes_cycle(model: str, cycle_hour: int) -> bool:
    """Return True when `model` actually publishes a new run at `cycle_hour` UTC.

    Models not in MODEL_PUBLISH_CYCLE_HOURS default to publishing at all 4 standard
    cycles. Fetching a model at a non-publishing cycle re-fetches the prior run
    under a wrong source_cycle_time (R3 redundancy).
    """
    return cycle_hour in MODEL_PUBLISH_CYCLE_HOURS.get(model, _ALL_CYCLES)

# Open-Meteo PREVIOUS-RUNS model ids keyed by the STORED model identity. The previous-runs API
# model id can differ from both the stored identity AND the single-runs id: the anchor is stored
# under its fusion identity ANCHOR_MODEL ("ecmwf_ifs", the BayesPrecisionFusionHistoryProvider join key) but the
# OM previous-runs API addresses the ECMWF deterministic feed as "ecmwf_ifs025" (the proven id
# in forecast_source_registry.OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP / forecasts_append). The
# fetch translates store-id -> OM-previous-runs-id here; the stored `model` column is ALWAYS the
# fusion identity. Non-anchor models fall back to OPENMETEO_MODEL_IDS (their OM id == store id).
OPENMETEO_PREVIOUS_RUNS_MODEL_IDS: dict[str, str] = {
    # OM previous-runs ECMWF id; stored model col stays "ecmwf_ifs" (the fusion identity). The
    # value is the SINGLE source of truth in bayes_precision_fusion_capture (BLOCKER 3 bridge gate reads
    # the same constant) so the download and the bridge can never drift on which product served
    # the anchor history.
    ANCHOR_MODEL: OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
}

# Domain-limited models: fetching these for an out-of-domain coordinate yields HTTP 400
# ("No data is available for this location"). The gate below uses the existing polygon config
# so the download and the fusion's selection gate are driven by the SAME polygon shapes.
#
#   icon_d2, meteofrance_arome_france_hd  — regional in REGIONAL_MODELS; gated directly.
#   icon_eu                               — EU-only 7km nest; not in REGIONAL_MODELS but IS
#     domain-limited. We reuse the icon_d2 polygon as the EU-presence gate (model_selection
#     already does this: icon_eu_in_eu_domain = regional_eligible("icon_d2", ...)).
#
# The fetched globals (icon_global, ukmo_global, ecmwf_ifs) are worldwide; they are never skipped
# here. (gfs_global/gem_global/jma_seamless were dropped 2026-06-17 — no longer fetched.)
_DOMAIN_GATED_MODELS: frozenset[str] = (
    frozenset(REGIONAL_MODELS)
    | frozenset({ICON_EU_MODEL})
    # Candidate-accrual models with limited physical domains (nbm: CONUS; ukmo_uk: UK).
    # ukmo_global_deterministic_10km is worldwide and intentionally NOT gated.
    | frozenset({"ncep_nbm_conus", "ukmo_uk_deterministic_2km"})
)


def _model_in_domain(model: str, *, lat: float, lon: float, lead_days: int) -> bool:
    """Return True when it is safe to request ``model`` for the given coordinate.

    Regional models are gated by their OWN polygon (config/model_domain_polygons.yaml).
    2026-06-09 FIX: icon_eu now uses its OWN ICON-EU 7km-nest polygon (Europe + W-Asia/Middle
    East), NOT the tightened icon_d2 Central-EU box. The prior icon_d2-borrow (commit bbe616e1eb)
    stopped the forward single_runs fetch for the 7 EU-edge cities (Madrid/Moscow/Istanbul/Ankara/
    Helsinki/Tel Aviv/Warsaw) that have real COVERED icon_eu data — starving their walk-forward
    history. The original 400-storm was from icon_d2/arome (truly narrow nests), never icon_eu
    (COVERED for all 12 cities), so icon_eu should not have been folded into the icon_d2 gate.
    Global models always return True (no domain restriction).
    """
    if model not in _DOMAIN_GATED_MODELS:
        return True  # global model — worldwide coverage
    # Each domain-gated model is gated by its OWN polygon key (icon_eu has its own ICON-EU hull).
    gate_model = model
    # Pass lead_days=0 to bypass the lead-day cap: we want geographic eligibility only.
    # The fusion's model_selection already applies the correct lead gate; the download
    # should fetch for ALL leads when the city is in-domain so the history accrues.
    return regional_eligible(gate_model, lat=lat, lon=lon, lead_days=0)


# A single-runs (forward) fetch: today's local-day extremum (degC) for the metric, or None.
SingleRunsFetchFn = Callable[..., float | None]
# A previous-runs (fixed-lead) fetch: the fixed-lead local-day extremum (degC), or None.
PreviousRunsFetchFn = Callable[..., float | None]


@dataclass(frozen=True)
class BayesPrecisionFusionDownloadTarget:
    """One current-target the extra models are captured for."""

    city: str
    metric: str
    target_date: str
    lead_days: int
    latitude: float
    longitude: float
    timezone_name: str


def _default_previous_runs_fetch(
    *,
    model: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    target_date: str,
    lead_days: int,
    metric: str,
) -> float | None:
    """Default fixed-lead previous-runs fetch via the OM previous-runs API. FORCES celsius
    (forecast_value_c is always degC; SPEC §7 C/F unit-mix antibody). FAIL-SOFT: returns None
    on ANY error so the model is dropped, never crashing the cycle.

    Uses the temperature_2m_previous_dayN hourly var (fixed-lead, no-leak; SPEC §3): the value
    valid on target_date as forecast lead_days ago. lead_days==0 falls back to temperature_2m.
    """
    try:
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL, fetch  # noqa: PLC0415

        # Translate the STORED model identity -> OM previous-runs model id. The anchor is
        # stored as ANCHOR_MODEL ("ecmwf_ifs") but the OM previous-runs API id is
        # "ecmwf_ifs025"; every other model's OM id equals its store id (OPENMETEO_MODEL_IDS).
        om_model = OPENMETEO_PREVIOUS_RUNS_MODEL_IDS.get(
            model, OPENMETEO_MODEL_IDS.get(model, model)
        )
        lead = max(0, int(lead_days))
        hourly_var = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": target_date,
            "end_date": target_date,
            "hourly": hourly_var,
            "models": om_model,
            "temperature_unit": "celsius",  # NEVER the settlement unit (C/F mix antibody)
            "timezone": timezone_name,
            # 2026-06-17 land-cell fix: the walk-forward de-bias history must train on the SAME
            # land-surface cell the live value reads (else land-current is corrected by
            # nearest-history). cell_selection is in the product identity -> the land history
            # accrues under its own hash, never mixed with the legacy nearest residuals.
            "cell_selection": BAYES_PRECISION_FUSION_CELL_SELECTION,
        }
        payload = fetch(
            PREVIOUS_RUNS_URL,
            params,
            endpoint_label=f"bayes_precision_fusion_{model}_previous_runs",
        )
        return _extract_localday_extremum_c(payload, hourly_var, metric)
    except Exception as exc:  # FAIL-SOFT: drop this model, never block the cycle.
        _LOG.warning("BAYES_PRECISION_FUSION previous-runs fetch dropped model %s (fail-soft): %s", model, exc)
        return None


def _extract_localday_extremum_c(payload: object, hourly_var: str, metric: str) -> float | None:
    """Local-day high/low (degC) from a previous-runs hourly payload over hourly_var. The
    previous-runs API returns the target_date already in the requested timezone, so every
    sample in the (single-day) window belongs to the local day. Returns None if empty."""
    if not isinstance(payload, dict):
        return None
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return None
    values = hourly.get(hourly_var)
    if not isinstance(values, (list, tuple)):
        return None
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    if not nums:
        return None
    return max(nums) if metric == "high" else min(nums)


# ── BATCHED FETCH HELPERS (R1+R2 collapse, 2026-06-13) ──────────────────────────────────
# Open-Meteo `models=a,b,c` returns temperature_2m_a / temperature_2m_b / temperature_2m_c
# keys (or bare `temperature_2m` when a single model). ONE call covers all in-domain models
# for a (city, target_date, cycle). Metric (high/low) is extracted from the SAME payload so
# the fetch count drops from (n_models × n_metrics × 2 endpoints) → (2 per city×date×cycle).


def _default_live_fetch_batched(
    *,
    models: list[str],
    latitude: float,
    longitude: float,
    timezone_name: str,
    run: "datetime",
    target_local_date: "date",
    forecast_hours: int,
) -> dict[str, tuple[float | None, float | None]]:
    """R1+R2: ONE single-runs call for ALL `models` at (city, target_date, cycle).

    Returns {model: (high_c, low_c)} for each model whose series is present in the
    response. Models absent from the response (400, None series) are omitted.
    FAIL-SOFT: any per-model parse error omits that model; total network failure
    returns {}.
    """
    try:
        from src.data.openmeteo_client import fetch  # noqa: PLC0415
        from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
            SINGLE_RUNS_FORECAST_URL,
        )

        om_ids = [OPENMETEO_MODEL_IDS.get(m, m) for m in models]
        run_iso = (
            run.strftime("%Y-%m-%dT%H:%M")
            if hasattr(run, "strftime")
            else str(run)
        )
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m",
            "models": om_ids,
            "run": run_iso,
            "forecast_hours": forecast_hours,
            "temperature_unit": "celsius",
            "timezone": timezone_name,
        }
        payload = fetch(
            SINGLE_RUNS_FORECAST_URL,
            params,
            endpoint_label="bayes_precision_fusion_single_runs_batched",
        )
        return _parse_batched_single_runs_payload(payload, models, target_local_date, timezone_name)
    except Exception as exc:
        _LOG.warning("BAYES_PRECISION_FUSION batched single_runs fetch failed (fail-soft): %s", exc)
        return {}


def _parse_batched_single_runs_payload(
    payload: object,
    models: list[str],
    target_local_date: "date",
    timezone_name: str,
) -> dict[str, tuple[float | None, float | None]]:
    """Parse a batched single-runs response into {model: (high_c, low_c)}.

    Open-Meteo returns temperature_2m_<om_id> for each requested model, or bare
    temperature_2m when a single model is requested.
    Uses extract_openmeteo_ecmwf_ifs9_localday_anchor for consistent local-day windowing.
    """
    from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: PLC0415
        extract_openmeteo_ecmwf_ifs9_localday_anchor,
    )

    if not isinstance(payload, dict):
        return {}
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return {}

    result: dict[str, tuple[float | None, float | None]] = {}
    for model in models:
        om_id = OPENMETEO_MODEL_IDS.get(model, model)
        # Multi-model response keys: temperature_2m_<om_id>; single-model: temperature_2m.
        keyed_var = f"temperature_2m_{om_id}"
        bare_var = "temperature_2m"
        series = hourly.get(keyed_var) or (hourly.get(bare_var) if len(models) == 1 else None)
        if series is None:
            continue
        # Build a sub-payload shaped like a single-model response for the extractor.
        sub_payload = dict(payload)
        sub_payload["hourly"] = dict(hourly)
        sub_payload["hourly"]["temperature_2m"] = series
        try:
            anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
                sub_payload,
                city_timezone=timezone_name,
                target_local_date=target_local_date,
                require_full_localday=True,  # 2026-06-17: reject horizon-clipped partial days
            )
            result[model] = (float(anchor.high_c), float(anchor.low_c))
        except Exception as exc:
            _LOG.warning(
                "BAYES_PRECISION_FUSION parse batched single_runs model=%s (fail-soft): %s", model, exc
            )
    return result


def _default_previous_runs_fetch_batched(
    *,
    models: list[str],
    latitude: float,
    longitude: float,
    timezone_name: str,
    target_date: str,
    lead_days: int,
) -> dict[str, tuple[float | None, float | None]]:
    """R1+R2: ONE previous-runs call for ALL `models` at (city, target_date, lead).

    Returns {model: (high_c, low_c)}.
    FAIL-SOFT: total network failure returns {}.
    """
    try:
        from src.data.openmeteo_client import PREVIOUS_RUNS_URL, fetch  # noqa: PLC0415

        lead = max(0, int(lead_days))
        hourly_var = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
        om_ids = [
            OPENMETEO_PREVIOUS_RUNS_MODEL_IDS.get(m, OPENMETEO_MODEL_IDS.get(m, m))
            for m in models
        ]
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": target_date,
            "end_date": target_date,
            "hourly": hourly_var,
            "models": om_ids,
            "temperature_unit": "celsius",
            "timezone": timezone_name,
        }
        payload = fetch(
            PREVIOUS_RUNS_URL,
            params,
            endpoint_label="bayes_precision_fusion_previous_runs_batched",
        )
        return _parse_batched_previous_runs_payload(payload, models, hourly_var)
    except Exception as exc:
        _LOG.warning("BAYES_PRECISION_FUSION batched previous_runs fetch failed (fail-soft): %s", exc)
        return {}


def _parse_batched_previous_runs_payload(
    payload: object,
    models: list[str],
    hourly_var: str,
) -> dict[str, tuple[float | None, float | None]]:
    """Parse a batched previous-runs response into {model: (high_c, low_c)}.

    Open-Meteo returns <hourly_var>_<om_id> for each model, or bare <hourly_var>
    when a single model is requested. Extracts both high (max) and low (min) from
    the same series.
    """
    if not isinstance(payload, dict):
        return {}
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return {}

    result: dict[str, tuple[float | None, float | None]] = {}
    for model in models:
        om_id = OPENMETEO_PREVIOUS_RUNS_MODEL_IDS.get(
            model, OPENMETEO_MODEL_IDS.get(model, model)
        )
        keyed_var = f"{hourly_var}_{om_id}"
        series = hourly.get(keyed_var) or (hourly.get(hourly_var) if len(models) == 1 else None)
        if series is None:
            continue
        nums = [float(v) for v in series if isinstance(v, (int, float))]
        if not nums:
            continue
        result[model] = (max(nums), min(nums))
    return result


# BLOCKER 4: the persisted column order for one raw_model_forecasts row. The first 10 are the
# original capture columns; the rest are the product-identity columns the download stamps.
_RMF_INSERT_COLUMNS = (
    "model", "city", "target_date", "metric", "source_cycle_time", "source_available_at",
    "captured_at", "lead_days", "forecast_value_c", "endpoint",
    "source_id", "source_family", "product_id", "provider", "model_name",
    "request_params_json", "request_url_hash", "latitude_requested", "longitude_requested",
    "timezone_requested", "cell_selection", "elevation_param", "downscaling_policy",
    "endpoint_mode", "model_domain_hash", "coverage_status",
)


def _detect_request_conflict(conn, row: dict) -> dict | None:
    """BLOCKER 4 — return the EXISTING row's identity if *row* shares the logical key of a stored
    row but carries a DIFFERENT physical request identity (product_id OR request_url_hash);
    else None.

    This is the antibody for the operator-sharpened B4: the logical key
    (model,city,target_date,metric,source_cycle_time,endpoint) must bind EXACTLY ONE physical
    request. A same-logical-key/different-request insert is a corrected request that the pre-fix
    INSERT OR IGNORE would have silently dropped, leaving a stale value to contaminate the history.
    """
    where = " AND ".join(f"{c} = ?" for c in _RMF_LOGICAL_KEY_COLUMNS)
    existing = conn.execute(
        f"""SELECT product_id, request_url_hash, forecast_value_c, cell_selection
            FROM raw_model_forecasts WHERE {where}""",
        tuple(row[c] for c in _RMF_LOGICAL_KEY_COLUMNS),
    ).fetchone()
    if existing is None:
        return None
    existing_product_id, existing_hash, existing_value, existing_cell = existing
    # LANDMINE #1 (operator-flagged, the_path PR review 2026-06-08): a STORED row whose
    # request_url_hash IS NULL is a PRE-IDENTITY / legacy-backfill row (it was seeded before the
    # product-identity columns existed). It carries NO physical request to conflict WITH, so a
    # live populated-identity insert on the same logical key is ENRICHABLE (update-in-place), NOT a
    # corrected-request conflict. Treating it as a conflict would make the FIRST live download after
    # ANY legacy backfill falsely raise. A genuine conflict requires a POPULATED stored hash that
    # DIFFERS from a populated incoming hash (handled below). NOTE: the up-to-date backfill stamps
    # full identity, so this path only fires for rows seeded by an OLD identity-less backfill.
    if existing_hash is None:
        return None
    # Same logical key + same physical request identity == the normal idempotent re-run: not a
    # conflict (INSERT OR IGNORE will collapse it). Only a CHANGED request identity is a conflict.
    if (existing_product_id == row.get("product_id")
            and existing_hash == row.get("request_url_hash")):
        return None
    return {
        "existing_product_id": existing_product_id,
        "existing_request_url_hash": existing_hash,
        "existing_forecast_value_c": existing_value,
        "existing_cell_selection": existing_cell,
    }


def _write_request_conflict_audit(conn, row: dict, existing: dict) -> None:
    """Persist one raw_model_forecast_request_conflicts row recording BOTH the existing and the
    incoming request identity (operator directive: 'write an audit row'). Forensically
    attributable: an operator can see exactly which request changed and the two values in play."""
    conn.execute(
        """INSERT INTO raw_model_forecast_request_conflicts
               (model, city, target_date, metric, source_cycle_time, endpoint,
                existing_product_id, incoming_product_id,
                existing_request_url_hash, incoming_request_url_hash,
                existing_forecast_value_c, incoming_forecast_value_c,
                existing_cell_selection, incoming_cell_selection)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            row["model"], row["city"], row["target_date"], row["metric"],
            row["source_cycle_time"], row["endpoint"],
            existing["existing_product_id"], row.get("product_id"),
            existing["existing_request_url_hash"], row.get("request_url_hash"),
            existing["existing_forecast_value_c"], row.get("forecast_value_c"),
            existing["existing_cell_selection"], row.get("cell_selection"),
        ),
    )


def _request_conflict_error(row: dict, conflict: dict) -> "RawModelForecastRequestConflict":
    """Build the RawModelForecastRequestConflict for a same-logical-key/different-request row."""
    return RawModelForecastRequestConflict(
        "raw_model_forecasts request conflict: logical key "
        f"({'/'.join(str(row[c]) for c in _RMF_LOGICAL_KEY_COLUMNS)}) already bound to "
        f"product_id={conflict['existing_product_id']!r} "
        f"request_url_hash={conflict['existing_request_url_hash']!r}; incoming "
        f"product_id={row.get('product_id')!r} "
        f"request_url_hash={row.get('request_url_hash')!r} differs. A corrected request "
        "must NOT silently overwrite/ignore the stored value (B4 contamination guard)."
    )


def _scan_and_audit_request_conflicts(conn, rows: Sequence[dict]) -> None:
    """BLOCKER 4 production pre-scan — detect same-logical-key/different-request conflicts and
    DURABLY write their audit rows on autocommit BEFORE the caller opens its insert transaction.

    download_bayes_precision_fusion_extra_raw_inputs wraps the insert in a BEGIN it ROLLS BACK on any error. If the
    conflict audit were written inside that BEGIN it would be rolled back along with everything
    else — the operator would lose the forensic trail of the silent-drop-that-wasn't. So the
    production path calls THIS first, with NO open transaction. If ANY conflict is found the audit
    rows are written and EXPLICITLY COMMITTED here (the connection uses sqlite3's default
    isolation_level="", so a DML opens an implicit deferred transaction — we commit it so the
    audit is durable even though the cycle's BEGIN below will be rolled back), then
    RawModelForecastRequestConflict is raised before BEGIN: the cycle's capture rows are never
    inserted (the corrected request must be operator-resolved)."""
    if conn.in_transaction:
        raise RuntimeError(
            "_scan_and_audit_request_conflicts must run with no open transaction so its audit "
            "rows can be committed independently of a caller's later ROLLBACK; an open "
            "transaction was found."
        )
    first_conflict: tuple[dict, dict] | None = None
    for row in rows:
        conflict = _detect_request_conflict(conn, row)
        if conflict is not None:
            _write_request_conflict_audit(conn, row, conflict)
            if first_conflict is None:
                first_conflict = (row, conflict)
    if first_conflict is not None:
        conn.commit()  # DURABLE: survives the caller's later ROLLBACK of the cycle BEGIN
        raise _request_conflict_error(*first_conflict)


def _persist_rows(
    conn,
    rows: Sequence[dict],
) -> int:
    """Persist the captured rows, idempotent on the FULL identity (logical key + product_id +
    request_url_hash). Returns rows actually written.

    BLOCKER 4 (operator-sharpened): BEFORE inserting, each row is checked against any stored row
    sharing its logical key. If a stored row exists with the SAME logical key but a DIFFERENT
    physical request identity (product_id or request_url_hash changed), this is a corrected
    request the pre-fix INSERT OR IGNORE would have SILENTLY dropped — leaving a stale value to
    contaminate bias/MAE/sigma. Instead an audit row is written (on THIS connection's current
    transaction state) and RawModelForecastRequestConflict is raised; no capture row is inserted.
    A same-logical-key + same-request re-run is the normal idempotent case and is INSERT-OR-IGNORE
    collapsed (the widened UNIQUE makes a genuinely changed request a NEW row when its logical key
    already differs).

    AUDIT DURABILITY CONTRACT: this function writes the audit row and raises, but does NOT itself
    guarantee the audit survives a caller's surrounding ROLLBACK. Callers that wrap this in a
    transaction they roll back on error (e.g. download_bayes_precision_fusion_extra_raw_inputs) MUST pre-scan with
    _scan_and_audit_request_conflicts on autocommit BEFORE opening that transaction, so the audit
    is already durable. When called directly on an autocommit connection (the operator-named test
    API), each statement self-commits and the audit is durable on its own.

    Each row is a dict keyed by _RMF_INSERT_COLUMNS (capture columns + BLOCKER 4 product
    identity). raw_sha256 / artifact_id stay NULL here (capture precedes artifact persistence)."""
    # Conflict pass FIRST — a single conflicting row fails the whole batch (the corrected request
    # must be resolved by an operator, not partially applied).
    for row in rows:
        conflict = _detect_request_conflict(conn, row)
        if conflict is not None:
            _write_request_conflict_audit(conn, row, conflict)
            raise _request_conflict_error(row, conflict)
    before = conn.total_changes
    placeholders = ",".join("?" for _ in _RMF_INSERT_COLUMNS)
    cols = ",".join(_RMF_INSERT_COLUMNS)
    conn.executemany(
        f"INSERT OR IGNORE INTO raw_model_forecasts ({cols}) VALUES ({placeholders})",
        [tuple(row[c] for c in _RMF_INSERT_COLUMNS) for row in rows],
    )
    return conn.total_changes - before


def _prune_old(conn, *, cutoff_iso: str) -> int:
    """DELETE rows captured before the retention cutoff (~180d). Returns rows deleted."""
    cur = conn.execute(
        "DELETE FROM raw_model_forecasts WHERE captured_at < ?", (cutoff_iso,)
    )
    return int(cur.rowcount or 0)


def _persist_chunk_with_lock_retry(
    forecast_db: Path | str,
    rows: Sequence[dict],
    *,
    cutoff_iso: str | None = None,
    attempts: int = 6,
) -> tuple[int, int]:
    """Durably persist one CHUNK of capture rows (and optionally prune), retrying
    transient writer locks with the rows held in memory.

    CHUNKED-DURABILITY (2026-06-11, operator class-kill): the capture pass spends
    10-40 MINUTES of network fetches; persisting once at the END made every fetched
    row hostage to a single instant — a daemon restart OR a transient writer lock at
    that moment rolled the WHOLE pass to zero (observed three times in one morning).
    Persisting per chunk (per target) bounds any loss to the in-flight chunk; rows
    are idempotent on their full identity so overlap/retry never double-writes.
    Semantics inside each attempt are unchanged: BLOCKER-4 conflict audit on
    autocommit BEFORE the rollback-on-error BEGIN, one transaction per chunk.
    """
    from src.state.db import _connect  # noqa: PLC0415
    from src.state.schema.v2_schema import (  # noqa: PLC0415
        ensure_replacement_forecast_live_schema,
    )

    written = 0
    pruned = 0
    for _attempt in range(attempts):
        conn = _connect(Path(forecast_db), write_class="live")
        try:
            ensure_replacement_forecast_live_schema(conn)
            if rows:
                _scan_and_audit_request_conflicts(conn, rows)
            # BEGIN IMMEDIATE: take the write lock up front so busy_timeout WAITS for it,
            # instead of a deferred BEGIN failing on the SELECT->INSERT upgrade under
            # rollback-journal (delete) mode contention with the other forecast-DB writers
            # (the "database is locked" storm that starved precision-fusion captures).
            conn.execute("BEGIN IMMEDIATE")
            try:
                if rows:
                    written = _persist_rows(conn, rows)
                if cutoff_iso is not None:
                    pruned = _prune_old(conn, cutoff_iso=cutoff_iso)
                conn.execute("COMMIT")
            except Exception:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise
            return written, pruned
        except sqlite3.OperationalError as lock_exc:
            if "locked" not in str(lock_exc).lower() or _attempt + 1 >= attempts:
                raise
            _LOG.warning(
                "bayes_precision_fusion persist hit transient writer lock (attempt %d/%d) — retrying in 20s "
                "with fetched rows held in memory: %s",
                _attempt + 1,
                attempts,
                lock_exc,
            )
            time.sleep(20)
        finally:
            conn.close()
    return written, pruned


def download_bayes_precision_fusion_extra_raw_inputs(
    *,
    forecast_db: Path,
    cycle: datetime,
    targets: Iterable[BayesPrecisionFusionDownloadTarget],
    single_runs_fetch: SingleRunsFetchFn | None = None,
    previous_runs_fetch: PreviousRunsFetchFn | None = None,
    release_lag_hours: float = 14.0,
    forecast_hours: int = 120,
    retention_days: int = RETENTION_DAYS,
) -> dict[str, object]:
    """Capture (forward single_runs + fixed-lead previous_runs) the 8 extra OM models for each
    current target and persist into raw_model_forecasts on a SINGLE zeus-forecasts.db connection
    (INV-37). Fail-soft per model. Prunes rows older than the retention cutoff in the same
    transaction. Returns a provenance report. Reuses the existing OM fetchers (IRON RULE #4).

    API-COLLAPSE 2026-06-13 (K=4 redundancies eliminated, q-path byte-identical):
      R1: metric (high/low) no longer doubles the fetch — both are extracted from ONE payload.
      R2: models batched — ONE single_runs call + ONE previous_runs call per (city, target_date).
          Uses _default_live_fetch_batched / _default_previous_runs_fetch_batched by default.
          Injected single_runs_fetch / previous_runs_fetch (per-model signature) are still
          supported for tests that inject per-model stubs.
      R3: models absent from the cycle's publish cadence are excluded from the batched set.
      R4a: previous_runs skip key is (model,city,target_date,metric) IGNORING cycle —
           a fixed-lead historical value is immutable once captured (never re-fetched).
    """
    # Detect whether caller injected old-style per-model fetchers (test compat) or batched.
    _use_legacy_per_model = (single_runs_fetch is not None or previous_runs_fetch is not None)
    single_fetch = single_runs_fetch or _default_live_fetch
    prev_fetch = previous_runs_fetch or _default_previous_runs_fetch

    cycle_utc = cycle.astimezone(UTC)
    cycle_iso = cycle_utc.isoformat()
    cycle_hour = cycle_utc.hour
    captured_at = datetime.now(tz=UTC)
    captured_iso = max(captured_at, cycle_utc).isoformat()
    # HONEST AVAILABILITY (U5 step 2a, freshness investigation 2026-06-12 §Q2A/§(d)): the prior
    # code stamped a SYNTHETIC source_available_at = cycle + 14h for every row — a fabricated
    # timestamp ~8-10h LATER than real dissemination (real global lag 4-6h), with zero spread over
    # 12,053 rows. That violates the no-unsupported-hardcoded-values + data-provenance laws: a
    # consumer that read it as a freshness signal would believe data was unavailable for ~8h after
    # it actually disseminated. We only append a row when we POSSESS the value (sv/pv is not None),
    # so captured_at PROVES availability by then — the honest, provenance-clean stamp is the
    # proof-of-possession bound min(captured_at, nominal), exactly as the manifest producer does
    # (scripts/download_replacement_forecast_current_targets.py). The release_lag_hours constant
    # survives only as the nominal backfill ceiling, never as a fabricated live freshness signal.
    # Consumer audit (grep raw_model_forecasts.source_available_at): the current-value serving
    # authority keys freshness on source_cycle_time + captured_at, and the de-bias history provider
    # explicitly does NOT read source_available_at as run_time — so no consumer is poisoned by the
    # honest value (it only stops being a lie).
    nominal_available = cycle_utc + timedelta(hours=release_lag_hours)
    source_available_iso = min(captured_at, nominal_available).isoformat()
    cutoff_iso = (captured_at - timedelta(days=int(retention_days))).isoformat()

    target_list = list(targets)
    rows: list[tuple] = []
    total_written = 0
    dropped: list[str] = []
    domain_excluded: list[str] = []

    # ROW-LEVEL SKIP (2026-06-09, K-root instance #5 resolution): preload the logical keys
    # already persisted for THIS cycle so a re-run only fetches what is MISSING. This replaces
    # the production wrapper's covered-target filter — coverage ("a posterior exists") said
    # nothing about cycle currency, so covered targets never received new-cycle extras (Madrid
    # 06-10 fused with icon_global because its icon_eu row existed only at the stale cycle).
    # With per-row existence as the only skip, the extras job is self-healing per cycle and
    # the steady-state cost is only-missing fetches. Fail-open: any read error -> empty set ->
    # fetch everything (the persist layer is UNIQUE-idempotent anyway).
    #
    # R4a (2026-06-13): previous_runs fixed-lead values are IMMUTABLE once captured — the skip
    # key for previous_runs ignores source_cycle_time so a past target_date is never re-fetched
    # under a new cycle stamp. single_runs KEEPS the per-cycle key (current value changes per cycle).
    persisted_keys: set[tuple] = set()
    prev_runs_done: set[tuple] = set()
    try:
        from src.state.db import _connect as _ro_connect  # noqa: PLC0415

        _ro = _ro_connect(Path(forecast_db))
        try:
            persisted_keys = {
                tuple(r)
                for r in _ro.execute(
                    "SELECT model, city, target_date, metric, endpoint"
                    " FROM raw_model_forecasts WHERE source_cycle_time = ?",
                    (cycle_iso,),
                )
            }
            # R4a: immutable previous_runs skip — ignore source_cycle_time entirely.
            prev_runs_done = {
                tuple(r)
                for r in _ro.execute(
                    "SELECT DISTINCT model, city, target_date, metric"
                    " FROM raw_model_forecasts WHERE endpoint = 'previous_runs'"
                )
            }
        finally:
            _ro.close()
    except Exception:
        persisted_keys = set()
        prev_runs_done = set()

    # De-duplicate targets by (city, target_date, lead_days) for the batched fetch path.
    # The metric dimension is NOT a fetch axis — both high and low come from one payload.
    # All target objects for the same (city, target_date) are kept for row-writing; only
    # ONE HTTP call is made per (city, target_date, cycle).
    from collections import defaultdict  # noqa: PLC0415
    targets_by_city_date: dict[tuple[str, str], list[BayesPrecisionFusionDownloadTarget]] = defaultdict(list)
    for t in target_list:
        targets_by_city_date[(t.city, t.target_date)].append(t)

    for (city, target_date), city_targets in targets_by_city_date.items():
        # All targets for the same (city, target_date) share lat/lon/timezone/lead_days.
        ref = city_targets[0]
        target_local_date = date.fromisoformat(target_date)

        if _use_legacy_per_model:
            # LEGACY PATH: per-model per-metric fetchers (test injection compatibility).
            # Iterates the old per-model loop so injected stubs work unchanged.
            for t in city_targets:
                for model in BAYES_PRECISION_FUSION_EXTRA_MODELS + BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS:
                    if not _model_in_domain(model, lat=t.latitude, lon=t.longitude, lead_days=int(t.lead_days)):
                        key = f"{model}:{t.city}"
                        domain_excluded.append(key)
                        _LOG.info(
                            "BAYES_PRECISION_FUSION download domain-excluded %s for %s (%.3fN, %.3fE) — "
                            "out-of-domain, no request sent",
                            model, t.city, t.latitude, t.longitude,
                        )
                        continue
                    if model in SINGLE_RUNS_UNSERVABLE_MODELS:
                        dropped.append(f"{model}:single_runs_unservable")
                        sv = None
                    elif (model, t.city, t.target_date, t.metric, "single_runs") in persisted_keys:
                        sv = None
                    else:
                        try:
                            sv = single_fetch(
                                model=model, latitude=t.latitude, longitude=t.longitude,
                                timezone_name=t.timezone_name, run=cycle_utc,
                                target_local_date=target_local_date, metric=t.metric,
                                forecast_hours=forecast_hours,
                            )
                        except Exception as exc:
                            _LOG.warning("BAYES_PRECISION_FUSION single_runs dropped %s (fail-soft): %s", model, exc)
                            sv = None
                        if sv is None:
                            dropped.append(f"{model}:single_runs")
                    if sv is not None:
                        rows.append({
                            "model": model, "city": t.city, "target_date": t.target_date,
                            "metric": t.metric, "source_cycle_time": cycle_iso,
                            "source_available_at": source_available_iso, "captured_at": captured_iso,
                            "lead_days": int(t.lead_days), "forecast_value_c": float(sv),
                            "endpoint": "single_runs",
                            **_bayes_precision_fusion_product_identity(model, "single_runs", t),
                        })

                    if (model, t.city, t.target_date, t.metric, "previous_runs") in persisted_keys:
                        pv = None
                    else:
                        try:
                            pv = prev_fetch(
                                model=model, latitude=t.latitude, longitude=t.longitude,
                                timezone_name=t.timezone_name, target_date=t.target_date,
                                lead_days=int(t.lead_days), metric=t.metric,
                            )
                        except Exception as exc:
                            _LOG.warning("BAYES_PRECISION_FUSION previous_runs dropped %s (fail-soft): %s", model, exc)
                            pv = None
                        if pv is None:
                            dropped.append(f"{model}:previous_runs")
                    if pv is not None:
                        rows.append({
                            "model": model, "city": t.city, "target_date": t.target_date,
                            "metric": t.metric, "source_cycle_time": cycle_iso,
                            "source_available_at": source_available_iso, "captured_at": captured_iso,
                            "lead_days": int(t.lead_days), "forecast_value_c": float(pv),
                            "endpoint": "previous_runs",
                            **_bayes_precision_fusion_product_identity(model, "previous_runs", t),
                        })
        else:
            # BATCHED PATH (R1+R2+R3): ONE single_runs call + ONE previous_runs call per
            # (city, target_date, cycle), covering all in-domain models and both metrics.
            all_models = list(BAYES_PRECISION_FUSION_EXTRA_MODELS + BAYES_PRECISION_FUSION_CANDIDATE_ACCRUAL_MODELS)

            # Domain gate + R3 cycle-cadence gate for single_runs.
            single_models: list[str] = []
            for model in all_models:
                if not _model_in_domain(model, lat=ref.latitude, lon=ref.longitude, lead_days=int(ref.lead_days)):
                    domain_excluded.append(f"{model}:{city}")
                    _LOG.info(
                        "BAYES_PRECISION_FUSION download domain-excluded %s for %s (%.3fN, %.3fE) — "
                        "out-of-domain, no request sent",
                        model, city, ref.latitude, ref.longitude,
                    )
                    continue
                if model in SINGLE_RUNS_UNSERVABLE_MODELS:
                    dropped.append(f"{model}:single_runs_unservable")
                    continue
                # R3: skip models that don't publish at this cycle hour.
                if not _model_publishes_cycle(model, cycle_hour):
                    _LOG.debug(
                        "BAYES_PRECISION_FUSION R3 cadence skip: %s does not publish at %02dZ", model, cycle_hour
                    )
                    continue
                # R1+R2 skip: check both metrics already persisted for this (model,city,date,cycle).
                metrics_needed = [
                    met for met in ("high", "low")
                    if (model, city, target_date, met, "single_runs") not in persisted_keys
                ]
                if metrics_needed:
                    single_models.append(model)

            # ONE batched single_runs fetch covers all in-domain models + both metrics.
            if single_models:
                sv_map = _default_live_fetch_batched(
                    models=single_models,
                    latitude=ref.latitude,
                    longitude=ref.longitude,
                    timezone_name=ref.timezone_name,
                    run=cycle_utc,
                    target_local_date=target_local_date,
                    forecast_hours=forecast_hours,
                )
                for model in single_models:
                    hilo = sv_map.get(model)
                    if hilo is None:
                        dropped.append(f"{model}:single_runs")
                        continue
                    high_c, low_c = hilo
                    # Emit one row per metric × target (both metrics from the one payload).
                    for t in city_targets:
                        val = high_c if t.metric == "high" else low_c
                        if val is None:
                            dropped.append(f"{model}:single_runs")
                            continue
                        if (model, t.city, t.target_date, t.metric, "single_runs") in persisted_keys:
                            continue
                        rows.append({
                            "model": model, "city": t.city, "target_date": t.target_date,
                            "metric": t.metric, "source_cycle_time": cycle_iso,
                            "source_available_at": source_available_iso, "captured_at": captured_iso,
                            "lead_days": int(t.lead_days), "forecast_value_c": float(val),
                            "endpoint": "single_runs",
                            **_bayes_precision_fusion_product_identity(model, "single_runs", t),
                        })

            # R4a: immutable previous_runs — only fetch models NOT already in prev_runs_done.
            # Domain gate applies; cadence gate does NOT apply (previous_runs values are
            # historical and valid regardless of which cycle issued the request).
            prev_models: list[str] = []
            for model in all_models:
                if not _model_in_domain(model, lat=ref.latitude, lon=ref.longitude, lead_days=int(ref.lead_days)):
                    continue  # domain_excluded already logged above
                # R4a: check both metrics already in immutable history.
                metrics_needed = [
                    met for met in ("high", "low")
                    if (model, city, target_date, met) not in prev_runs_done
                ]
                if metrics_needed:
                    prev_models.append(model)

            # ONE batched previous_runs fetch covers all models with missing history.
            if prev_models:
                pv_map = _default_previous_runs_fetch_batched(
                    models=prev_models,
                    latitude=ref.latitude,
                    longitude=ref.longitude,
                    timezone_name=ref.timezone_name,
                    target_date=target_date,
                    lead_days=int(ref.lead_days),
                )
                for model in prev_models:
                    hilo = pv_map.get(model)
                    if hilo is None:
                        dropped.append(f"{model}:previous_runs")
                        continue
                    high_c, low_c = hilo
                    for t in city_targets:
                        if (model, t.city, t.target_date, t.metric) in prev_runs_done:
                            continue
                        val = high_c if t.metric == "high" else low_c
                        if val is None:
                            dropped.append(f"{model}:previous_runs")
                            continue
                        rows.append({
                            "model": model, "city": t.city, "target_date": t.target_date,
                            "metric": t.metric, "source_cycle_time": cycle_iso,
                            "source_available_at": source_available_iso, "captured_at": captured_iso,
                            "lead_days": int(t.lead_days), "forecast_value_c": float(val),
                            "endpoint": "previous_runs",
                            **_bayes_precision_fusion_product_identity(model, "previous_runs", t),
                        })

        # CHUNKED DURABILITY (2026-06-11): persist THIS city×date's rows now — a restart or
        # crash later in the pass can no longer destroy completed targets' fetches.
        if rows:
            chunk_written, _ = _persist_chunk_with_lock_retry(forecast_db, rows)
            total_written += chunk_written
            rows = []

    # ---- CHUNKED-DURABLE persist happened per city×date above; final pass prunes only ----
    written = total_written
    _, pruned = _persist_chunk_with_lock_retry(forecast_db, (), cutoff_iso=cutoff_iso)

    if domain_excluded:
        _LOG.info(
            "BAYES_PRECISION_FUSION download domain-excluded %d model×city combos (expected — regional models "
            "not requested for out-of-domain cities): %s",
            len(domain_excluded),
            ", ".join(sorted(set(domain_excluded))[:20]),
        )

    # Ensemble-completeness: global models (always in-domain) that were unexpectedly dropped
    # are the signal of a real upstream problem. Distinguish them from domain-excluded
    # (expected absence) so the operator can tell "complete global ensemble" from "degraded".
    global_models_expected = frozenset(
        m for m in BAYES_PRECISION_FUSION_EXTRA_MODELS
        if m not in frozenset(REGIONAL_MODELS) | frozenset({ICON_EU_MODEL})
    )
    global_single_dropped = {
        d.split(":")[0] for d in dropped if d.endswith(":single_runs")
    } & global_models_expected
    if global_single_dropped:
        _LOG.warning(
            "BAYES_PRECISION_FUSION download: GLOBAL model(s) single_runs UNAVAILABLE (not domain-excluded — "
            "real upstream failure): %s",
            sorted(global_single_dropped),
        )

    return {
        "status": "BAYES_PRECISION_FUSION_EXTRA_RAW_INPUTS_DOWNLOADED",
        "cycle": cycle_iso,
        "forecast_db": str(forecast_db),
        "target_count": len(target_list),
        "candidate_row_count": len(rows),
        "written_row_count": written,
        "pruned_row_count": pruned,
        "dropped": tuple(dropped),
        "domain_excluded": tuple(sorted(set(domain_excluded))),
        # Ensemble-completeness markers: how many global (always-in-domain) models succeeded.
        "global_models_expected": len(global_models_expected),
        "global_models_unavailable": sorted(global_single_dropped),
    }
