# Created: 2026-06-10
# Last reused or audited: 2026-10-06 (exact original-message retention replay)
# Authority basis: operator green-light 2026-06-10 item B (remaining-day
#   pricing + persist-the-hourly-vector option from the day0 first-principles
#   review §6.1/§6.3). INV-37: all writes go to zeus-forecasts.db under
#   db_writer_lock(LIVE); reads are mode=ro.
"""Day0 high-res hourly forecast vectors: persist + remaining-day extremes.

Why
---
The day0 entry lane priced P(bin) from the FULL-DAY forecast distribution
masked by the running extreme — not P(remaining-day excursion | now). The
review (2026-06-10 §2.4) classified that DEVIATES: post-peak it overprices
bins above the running max. The data needed to fix it (hourly curves from the
high-res models icon_d2 / arome HD / UKMO UKV 2km / NCEP NBM) was being
FETCHED and then reduced to a single daily extremum (raw_model_forecasts).
This module persists the bounded hourly vector so the day0 q can condition on
hours AFTER now.

Bounded by design
-----------------
- Day0-relevant cities use in-domain regional hourly models when available
  (polygon gate reused from src/forecast/model_selection.regional_eligible,
  lead 0). Every city also uses the current global deterministic provider
  bundle so Day0 probability does not collapse to one model outside regional
  domains.
- Only ~2 forecast days of hours per row; retention prunes rows older than
  DAY0_VECTOR_RETENTION_DAYS (default 3) on every write pass.
- Refresh throttled to once per DEFAULT_REFRESH_INTERVAL_S per process.

Provenance: every row carries source identity (provider/model/endpoint/
request hash), a local capture clock, and fetch possession clocks.  The generic
Open-Meteo forecast response does not expose a per-model initialization/run
cycle, so this lane never fabricates one from local fetch time.
Temperatures are ALWAYS degC in storage (the C/F unit-mix antibody from the
bayes_precision_fusion lane: convert at the consumption seam, never store mixed units).
"""
from __future__ import annotations

import contextlib
import base64
import csv
import fcntl
import hashlib
import io
import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass, field
from enum import Enum
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from functools import lru_cache
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
from scipy.special import log_ndtr, ndtri_exp

from src.contracts.settlement_semantics import SettlementSemantics
from src.contracts.settlement_semantics import settlement_preimage_offsets
from src.data.openmeteo_quota import quota_tracker
from src.state.schema.observation_prints_schema import (
    RECEIPT_ORDER_DESC_SQL,
    RECEIPT_US_SQL,
    receipt_us,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc


DAY0_REMAINING_CARRIER_OPERATOR_V1 = "extreme_observed_then_noisy_future_v1"
DAY0_REMAINING_CARRIER_OPERATOR_V2 = (
    "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2"
)
DAY0_REMAINING_CARRIER_OPERATOR_V3 = (
    "typed_remaining_and_final_extreme_gaussian_v3"
)
DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER = "resolver_graded_terminal_composition_v1"
DAY0_REMAINING_CARRIER_OPERATOR = DAY0_REMAINING_CARRIER_OPERATOR_V2


def _day0_utc_now() -> datetime:
    """Return the local aware UTC clock used for fetch possession checks."""

    return datetime.now(UTC)

OPENMETEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
OPENMETEO_ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"

# The extrema product can be structurally non-identifiable when a 3-hour
# ECMWF bucket straddles a city's local midnight (UTC+8 LOW is the common
# case).  The conditional Day0 operator needs the unresolved-hour ENS shape,
# not a mislabeled full-day extrema row.  Persist the exact-run IFS025 member
# paths in the existing hourly-vector table so selection and submit can bind
# the same possession proof without adding a parallel truth store.
DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL = "ecmwf_ifs025"
# The source-clock carrier is served by Open-Meteo's distinct ensemble
# metadata domain. Keep its HWM namespace separate from the deterministic
# ``ecmwf_ifs025`` metadata domain; both endpoints can report different runs.
DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL = "ecmwf_ifs025_ensemble"
DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT = 51
DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX = (
    f"{DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL}_member"
)

#: High-res intraday models for the day0 remaining-day distribution
#: (operator charge #2: icon_d2 ~2km, arome HD, UKMO UKV 2km, NCEP NBM CONUS).
#: Each is domain-gated via config/model_domain_polygons.yaml.
DAY0_HOURLY_MODELS: tuple[str, ...] = (
    "icon_d2",
    "meteofrance_arome_france_hd",
    "ukmo_uk_deterministic_2km",
    "ncep_nbm_conus",
    "jma_msm",
)
GLOBAL_DAY0_HOURLY_MODELS: tuple[str, ...] = (
    "ecmwf_ifs",
    "icon_global",
    "ukmo_global_deterministic_10km",
)

DAY0_VECTOR_RETENTION_DAYS = 3.0
# Provider-run HWM wakes bypass this blind fallback interval. Current
# observations recondition persisted trajectories without another HTTP fetch,
# so polling the same immutable run twice per hour spends quota without adding
# decision-time information.
DEFAULT_REFRESH_INTERVAL_S = 3600.0
DEFAULT_FETCH_TIMEOUT_S = 4.0
DEFAULT_REFRESH_BUDGET_S = 6.0
DEFAULT_REFRESH_MAX_CITIES = 3
# Capture-age bound for ROLLING rows only: deterministic standard-endpoint
# vectors serve a blended "now" whose currency is when they were captured.
# ENS member rows are one immutable provider run; their currency is run
# identity (``day0_ensemble_run_refusal``), never capture age.
DAY0_ROLLING_CAPTURE_MAX_AGE_HOURS = 3.0
DAY0_HOURLY_REFRESH_HEADROOM_HOURS = 1.0
DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES = 60.0
DAY0_HOURLY_FORECAST_HOURS = 72
# The current observation can fall between provider grid hours.  Keep the last
# real provider hour so current-state conditioning has a causal innovation
# anchor after a refresh; never interpolate or stitch one across runs.  This is
# the reach-back beyond the causal gap (``day0_hourly_past_hours``), absorbing
# an hour rollover between the decision clock and the provider's "now".
DAY0_HOURLY_PAST_HOURS = 1
INCOMPLETE_BUNDLE_RETRY_INTERVAL_S = 45.0
INCOMPLETE_BUNDLE_RETRY_MAX_INTERVAL_S = DEFAULT_REFRESH_INTERVAL_S
INCOMPLETE_BUNDLE_CRITICAL_RETRY_MAX_INTERVAL_S = 600.0

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS day0_hourly_vectors (
    vector_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    timezone_name TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'openmeteo',
    endpoint TEXT NOT NULL,
    request_hash TEXT NOT NULL CHECK (request_hash <> ''),
    times_json TEXT NOT NULL,
    temps_c_json TEXT NOT NULL,
    source_run_meta_json TEXT
)
"""
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_day0_hourly_vectors_city_date "
    "ON day0_hourly_vectors(city, target_date, captured_at)"
)
# The retention prune in persist_day0_hourly_vectors filters on captured_at
# alone; without this index it scans the whole table under the forecasts
# LIVE flock every refresh cycle.
_PRUNE_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_day0_hourly_vectors_captured_at "
    "ON day0_hourly_vectors(captured_at)"
)


def day0_hourly_target_dates_for_refresh(
    *, city: Any, decision_time: datetime
) -> tuple[str, ...]:
    """Target dates covered by a 2-day hourly fetch for the city's local clock.

    Open-Meteo requests in this module ask for ``forecast_days=2``. Persisting the
    response only under the city's current local date starves active next-day weather
    markets: the read path correctly requires exact ``(city, target_date)``, so a
    June 29 market cannot use a June 28-stamped vector even though the payload already
    contains June 29 hours. Persist both local today and local tomorrow under separate
    target_date identities.
    """

    tz = ZoneInfo(str(getattr(city, "timezone")))
    local_day = decision_time.astimezone(tz).date()
    return (
        local_day.isoformat(),
        (local_day + timedelta(days=1)).isoformat(),
    )


def day0_source_clock_ensemble_target_dates(
    *,
    city: Any,
    decision_time: datetime,
    conn: sqlite3.Connection | None = None,
) -> tuple[str, ...]:
    """Return current-day LOW scopes whose newest extrema ENS is ambiguous.

    This is a data-product routing decision, not a probability waiver.  Only a
    newest possessed canonical row with true boundary ambiguity can request the
    hourly ensemble carrier; a missing table/row simply leaves ENTRY blocked.
    """

    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    city_name = str(getattr(city, "name", "") or "").strip()
    timezone_name = str(getattr(city, "timezone", "") or "").strip()
    if not city_name or not timezone_name:
        return ()
    target_date = decision_time.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        try:
            conn = get_forecasts_connection_read_only()
        except sqlite3.Error:
            return ()
    try:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ensemble_snapshots'"
        ).fetchone()
        if table is None:
            return ()
        row = conn.execute(
            """
            SELECT boundary_ambiguous, causality_status,
                   contributes_to_target_extrema
              FROM ensemble_snapshots
             WHERE city = ? AND target_date = ?
               AND temperature_metric = 'low'
               AND available_at <= ?
             ORDER BY datetime(available_at) DESC, snapshot_id DESC
             LIMIT 1
            """,
            (city_name, target_date, decision_time.astimezone(UTC).isoformat()),
        ).fetchone()
        if row is None:
            return ()
        return (
            (target_date,)
            if int(row[0] or 0) == 1
            and str(row[1] or "").strip() == "REJECTED_BOUNDARY_AMBIGUOUS"
            and int(row[2] or 0) == 0
            else ()
        )
    except sqlite3.Error:
        return ()
    finally:
        if own_conn and conn is not None:
            conn.close()


@dataclass(frozen=True)
class Day0HourlyVector:
    model: str
    city: str
    target_date: str
    timezone_name: str
    # Local request/capture clock assigned by the fetcher; this is not a
    # provider-issued forecast/observation timestamp and is not possession.
    captured_at: str
    times: tuple[str, ...]       # ISO local timestamps as served (city timezone)
    temps_c: tuple[float, ...]   # ALWAYS degC
    # JSON provenance written only by the live fetch path.  It carries the
    # separate fetch-start/fetch-complete possession clocks and source-run
    # identity; rows without it cannot sponsor held probability authority.
    source_run_meta_json: str | None = None

    @property
    def immutable_run_member(self) -> bool:
        """True for an ENS member row: one immutable run, current by run identity."""
        return str(self.model or "").startswith(DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX)


@dataclass(frozen=True)
class Day0CurrentTemperatureState:
    """One canonical current-temperature witness for a Day0 path rebuild.

    The running extreme remains settlement/probability-boundary evidence.  This
    is deliberately separate trajectory evidence: it aligns the already-pinned
    hourly provider path to the latest same-station current temperature.
    """

    value_native: float
    observed_at: datetime
    source: str
    clock_evidence: Mapping[str, object] | None = None
    # The exact ledger row/event read; telemetry provenance, never identity.
    input_ref: Mapping[str, object] | None = field(default=None, compare=False)

    def identity(self) -> dict[str, object]:
        return {
            "value_native": float(self.value_native),
            "observed_at_utc": self.observed_at.astimezone(UTC).isoformat(),
            "source": str(self.source),
        }


@dataclass(frozen=True)
class Day0CausalBundleValidation:
    """Comparison result for one immutable Day0 vector/posterior bundle."""

    ok: bool
    reason: str | None
    expected_bundle_identity: str
    actual_bundle_identity: str
    expected_carrier_vector_identity: str
    actual_carrier_vector_identity: str
    expected_carrier_vector_hash: str
    actual_carrier_vector_hash: str

    def receipt(self) -> dict[str, object]:
        """Return the exact mismatch evidence suitable for a decision receipt."""

        return {
            "reason": self.reason,
            "expected_bundle_identity": self.expected_bundle_identity,
            "actual_bundle_identity": self.actual_bundle_identity,
            "expected_carrier_vector_identity": self.expected_carrier_vector_identity,
            "actual_carrier_vector_identity": self.actual_carrier_vector_identity,
            "expected_carrier_vector_hash": self.expected_carrier_vector_hash,
            "actual_carrier_vector_hash": self.actual_carrier_vector_hash,
        }


def _day0_canonical_json(value: object) -> object:
    """Normalize only deterministic JSON values used in causal identity keys."""

    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _day0_canonical_json(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_day0_canonical_json(item) for item in value]
    raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")


def _day0_json_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _day0_canonical_json(value), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def build_day0_causal_evidence_bundle(
    *,
    city: str,
    target_date: str,
    metric: str,
    observation_context: Mapping[str, object],
    cutoff_utc: str,
    vector_witness: Mapping[str, object],
) -> dict[str, object]:
    """Build one immutable Day0 causal bundle for a posterior and its vectors.

    The vector identity names the exact per-model persisted rows; the vector
    hash binds their complete provenance.  The bundle identity additionally
    commits to the Day0 observation context and causal cutoff.  Consumers must
    compare two bundles rather than rebind a posterior to a newer vector row.
    """

    normalized_city = str(city or "").strip()
    normalized_target_date = str(target_date or "").strip()
    normalized_metric = str(metric or "").strip().lower()
    normalized_cutoff = str(cutoff_utc or "").strip()
    if (
        not normalized_city
        or not normalized_target_date
        or normalized_metric not in {"high", "low"}
        or not normalized_cutoff
        or not isinstance(observation_context, Mapping)
        or not observation_context
        or not isinstance(vector_witness, Mapping)
    ):
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    try:
        date.fromisoformat(normalized_target_date[:10])
        parsed_cutoff = datetime.fromisoformat(
            normalized_cutoff.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID") from exc
    if parsed_cutoff.tzinfo is None or parsed_cutoff.utcoffset() is None:
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    vector_ids = vector_witness.get("vector_ids_by_model")
    if not isinstance(vector_ids, Mapping) or not vector_ids:
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    normalized_vector_ids = {
        str(model).strip(): str(vector_id).strip()
        for model, vector_id in vector_ids.items()
    }
    if any(
        not model or not vector_id
        for model, vector_id in normalized_vector_ids.items()
    ):
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID")
    canonical_observation = _day0_canonical_json(observation_context)
    canonical_witness = _day0_canonical_json(vector_witness)
    vector_hash_fields = (
        "vector_ids_by_model",
        "capture_times_by_model_utc",
        "request_hash_by_model",
        "source_run_id_by_model",
        "provider_run_id_by_model",
        "provider_source_cycle_time_by_model_utc",
        "provider_source_available_at_by_model_utc",
        "provider_source_modified_at_by_model_utc",
    )
    canonical_vector_provenance = {
        field: canonical_witness[field]
        for field in vector_hash_fields
        if field in canonical_witness
    }
    carrier_vector_identity = _day0_json_hash(
        {"vector_ids_by_model": normalized_vector_ids}
    )
    carrier_vector_hash = _day0_json_hash(canonical_vector_provenance)
    core = {
        "schema": "day0_causal_evidence_bundle_v1",
        "city": normalized_city,
        "target_date": normalized_target_date,
        "metric": normalized_metric,
        "observation_context": canonical_observation,
        "cutoff_utc": parsed_cutoff.astimezone(UTC).isoformat(),
        "carrier_vector_identity": carrier_vector_identity,
        "carrier_vector_hash": carrier_vector_hash,
    }
    return {
        **core,
        "carrier_vector_ids_by_model": normalized_vector_ids,
        "carrier_vector_witness": canonical_witness,
        "bundle_identity": _day0_json_hash(core),
    }


def validate_day0_causal_evidence_bundle(
    *,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> Day0CausalBundleValidation:
    """Compare immutable Day0 evidence bundles without authorizing a rebind."""

    try:
        fields = (
            "city",
            "target_date",
            "metric",
            "observation_context",
            "cutoff_utc",
        )
        expected_core = {key: expected[key] for key in fields}
        actual_core = {key: actual[key] for key in fields}
        expected_rebuilt = build_day0_causal_evidence_bundle(
            **expected_core,
            vector_witness=expected["carrier_vector_witness"],
        )
        actual_rebuilt = build_day0_causal_evidence_bundle(
            **actual_core,
            vector_witness=actual["carrier_vector_witness"],
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("DAY0_CAUSAL_EVIDENCE_BUNDLE_INPUT_INVALID") from None
    # A persisted bundle carries a full vector hash while a consumer's actual
    # bundle normally comes from the same full witness.  Require both supplied
    # values to agree with their own reconstructed identities before comparison.
    expected_identity = str(expected.get("bundle_identity") or "").strip()
    actual_identity = str(actual.get("bundle_identity") or "").strip()
    expected_vector_identity = str(expected.get("carrier_vector_identity") or "").strip()
    actual_vector_identity = str(actual.get("carrier_vector_identity") or "").strip()
    expected_vector_hash = str(expected.get("carrier_vector_hash") or "").strip()
    actual_vector_hash = str(actual.get("carrier_vector_hash") or "").strip()
    complete = all((
        expected_identity, actual_identity, expected_vector_identity,
        actual_vector_identity, expected_vector_hash, actual_vector_hash,
    ))
    self_consistent = (
        expected_identity == expected_rebuilt["bundle_identity"]
        and actual_identity == actual_rebuilt["bundle_identity"]
        and expected_vector_identity == expected_rebuilt["carrier_vector_identity"]
        and actual_vector_identity == actual_rebuilt["carrier_vector_identity"]
        and expected_vector_hash == expected_rebuilt["carrier_vector_hash"]
        and actual_vector_hash == actual_rebuilt["carrier_vector_hash"]
    )
    ok = bool(
        complete
        and self_consistent
        and expected_identity == actual_identity
        and expected_vector_identity == actual_vector_identity
        and expected_vector_hash == actual_vector_hash
    )
    return Day0CausalBundleValidation(
        ok=ok,
        reason=None if ok else "DAY0_CAUSAL_EVIDENCE_BUNDLE_MISMATCH",
        expected_bundle_identity=expected_identity,
        actual_bundle_identity=actual_identity,
        expected_carrier_vector_identity=expected_vector_identity,
        actual_carrier_vector_identity=actual_vector_identity,
        expected_carrier_vector_hash=expected_vector_hash,
        actual_carrier_vector_hash=actual_vector_hash,
    )


#: Metadata that records HOW or WHERE a capture was fetched, never WHAT it
#: observed, so two captures of the SAME provider run may differ here and still
#: be the same evidence. ``endpoint``/``endpoint_mode``/``source_run_authority``
#: join the original four because ``_select_day0_run_endpoint`` deliberately
#: falls back from the run-pinned single-runs endpoint to the standard
#: meta-stamped one (35ff9a3dc) whenever the freshest run fails its clock
#: precheck or its response starts after the causal observation boundary. That
#: fallback proves the SAME run through a different URL: measured 2026-09-18,
#: 28 of that day's endpoint-mode flips carried an identical ``provider_run_id``
#: AND byte-identical values on every shared timestamp, yet the semantic hash
#: rejected the entry (GLOBAL_ACTUATION_PROBABILITY_USE_DIVERGED).
#:
#: ``provider_source_available_at_utc``/``provider_source_modified_at_utc`` are
#: replica-local clocks: Open-Meteo serves ``meta.json`` from replicas that
#: disagree on them for the SAME run. Measured 2026-10-05 (Denver ecmwf_ifs),
#: every capture carried one run identity while ``available_at`` alternated
#: between two values capture to capture, so the hash read a replica flip as
#: new evidence (1726 SEMANTIC_META_MISMATCH DAY0_REMAINING_DAY_MEMBERS_
#: UNAVAILABLE in 7h). They stay validated per row against that row's own
#: decision bound by ``_day0_canonical_vector_row_snapshot``; they just cannot
#: decide equivalence between two rows. The run's identity is
#: ``provider_run_id``, ``provider_source_cycle_time_utc``, ``model_api_id`` and
#: ``provider``, which are NOT exempt, so a flip that advances the run still
#: mismatches.
_DAY0_CAPTURE_EQUIVALENCE_ONLY_META = frozenset(
    {
        "fetch_started_at",
        "fetch_finished_at",
        "request_hash",
        "source_run_id",
        "endpoint",
        "endpoint_mode",
        "source_run_authority",
        "provider_source_available_at_utc",
        "provider_source_modified_at_utc",
        "provider_metadata_bracket_evidence",
    }
)


def _day0_parse_aware_clock(value: object, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _day0_parse_snapshot_written_clock(value: object) -> datetime:
    """Only canonical ensemble_snapshots CURRENT_TIMESTAMP is naive UTC."""
    if not isinstance(value, str):
        raise ValueError("interval_snapshot_written_at invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("interval_snapshot_written_at invalid") from exc
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(UTC)
    # Do not extend the SQLite writer convention to provider/source clocks or
    # arbitrary naive ISO timestamps. Keep the original stored value as witness.
    try:
        canonical = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError("interval_snapshot_written_at invalid") from exc
    if canonical.strftime("%Y-%m-%d %H:%M:%S") != value:
        raise ValueError("interval_snapshot_written_at invalid")
    return canonical.replace(tzinfo=UTC)


def _day0_normalize_vector_request_semantics(
    key: str, value: object, *, model: str
) -> object:
    """Canonicalize request semantics without coercing unknown metadata strings.

    ``request_params_json`` is stamped from the bundle-wide capture request, so
    its ``runs``/``endpoint_modes`` maps carry an entry per sibling model in the
    bundle, not just this row's own model. A sibling model's run advancing must
    not change this row's own semantic identity, so ``runs`` is projected down
    to this row's own model before the equivalence comparison.

    ``endpoint_modes`` is dropped entirely rather than projected: it names the
    URL each model was fetched through, which is transport, not evidence —
    the same reason ``endpoint``/``endpoint_mode`` sit in
    ``_DAY0_CAPTURE_EQUIVALENCE_ONLY_META``. Projecting it to this row's own
    model still let the row's own single-runs/standard fallback change the
    semantic hash, which is the very divergence that exemption exists to
    tolerate. ``runs`` still carries the projected run, so a fallback that
    proves a DIFFERENT run remains a mismatch here.
    """

    if key == "__physical_response_capture_v1" and isinstance(value, Mapping):
        index = value.get("location_index")
        locations = value.get("locations", [])
        geometry = locations[index] if type(index) is int and 0 <= index < len(locations) else {}
        # Each original is independently replayed before this semantic projection.
        # Endpoint/HTTP clocks and a larger donor window are transport, not a new point.
        value = {"model": value.get("model"), "native_variable": value.get("native_variable"),
                 "temperature_unit": value.get("temperature_unit"), "geometry": geometry}
    if key == "__physical_response_reference_v1":
        value = {"revision": "day0_original_entity_reference_v1",
                 "model_surface_geometry": value.get("model_surface_witness", {}).get("geometry")}
    if key == "request_params_json" and isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return value
    if key == "request_params_json" and isinstance(value, Mapping):
        projected = dict(value)
        sub = projected.get("runs")
        if isinstance(sub, Mapping):
            projected["runs"] = {model: sub[model]} if model in sub else {}
        projected.pop("endpoint_modes", None)
        value = projected
    return _day0_canonical_json(value)


def _day0_canonical_vector_row_snapshot(
    conn: sqlite3.Connection,
    *,
    vector_id: str,
    model: str,
    city: str,
    target_date: str,
    timezone_name: str,
    witness: Mapping[str, object],
    decision_bound_utc: datetime,
) -> dict[str, object]:
    """Read and validate one canonical vector row for equivalence proof.

    The row payload is the authority.  Witness hashes/identities are only checked
    against the row's fields; they never supply the content being compared.
    """

    row = conn.execute(
        """
        SELECT vector_id, model, city, target_date, timezone_name, captured_at,
               provider, endpoint, request_hash, times_json, temps_c_json,
               source_run_meta_json
          FROM day0_hourly_vectors
         WHERE vector_id = ?
         LIMIT 1
        """,
        (str(vector_id),),
    ).fetchone()
    if row is None:
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_VECTOR_ROW_MISSING")
    if (
        str(row[0] or "") != str(vector_id)
        or str(row[1] or "") != model
        or str(row[2] or "") != city
        or str(row[3] or "") != target_date
        or str(row[4] or "") != timezone_name
    ):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_VECTOR_SCOPE_MISMATCH")
    try:
        raw_times = json.loads(row[9])
        raw_temps = json.loads(row[10])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_VECTOR_PAYLOAD_INVALID") from exc
    if (
        not isinstance(raw_times, list)
        or not isinstance(raw_temps, list)
        or not raw_times
        or len(raw_times) != len(raw_temps)
        or any(not isinstance(value, str) or not value.strip() for value in raw_times)
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in raw_temps
        )
    ):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_VECTOR_PAYLOAD_INVALID")
    times = tuple(raw_times)
    temps = tuple(float(value) for value in raw_temps)
    try:
        meta = json.loads(str(row[11] or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_SOURCE_META_INVALID") from exc
    if not isinstance(meta, Mapping):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_SOURCE_META_INVALID")
    _day0_replay_vector_original(conn, Day0HourlyVector(model=model, city=city,
        target_date=target_date, timezone_name=timezone_name, captured_at=str(row[5]),
        times=times, temps_c=temps, source_run_meta_json=str(row[11])),
        decision_bound_utc=decision_bound_utc, provider=str(row[6]), endpoint=str(row[7]), request_hash=str(row[8]))

    capture = _day0_parse_aware_clock(row[5], field_name="captured_at")
    fetch_started = _day0_parse_aware_clock(
        meta.get("fetch_started_at"), field_name="fetch_started_at"
    )
    fetch_finished = _day0_parse_aware_clock(
        meta.get("fetch_finished_at"), field_name="fetch_finished_at"
    )
    provider_cycle = _day0_parse_aware_clock(
        meta.get("provider_source_cycle_time_utc"),
        field_name="provider_source_cycle_time_utc",
    )
    provider_available = _day0_parse_aware_clock(
        meta.get("provider_source_available_at_utc"),
        field_name="provider_source_available_at_utc",
    )
    provider_modified = _day0_parse_aware_clock(
        meta.get("provider_source_modified_at_utc"),
        field_name="provider_source_modified_at_utc",
    )
    if not (
        capture <= fetch_started <= fetch_finished <= decision_bound_utc
        and provider_cycle <= provider_available <= fetch_finished
        and provider_modified <= fetch_finished
        and provider_cycle <= decision_bound_utc
        and provider_available <= decision_bound_utc
        and provider_modified <= decision_bound_utc
    ):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CLOCK_INVALID")

    provider_identity = _provider_run_identity_from_meta(
        meta,
        expected_model=model,
    )
    if provider_identity is None:
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_PROVIDER_IDENTITY_INVALID")
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        SINGLE_RUNS_FORECAST_URL,
        STANDARD_FORECAST_URL,
    )

    model_api_id = str(meta.get("model_api_id") or "").strip()
    provider = str(meta.get("provider") or "").strip()
    endpoint = str(row[7] or "").strip()
    authority = str(meta.get("source_run_authority") or "").strip()
    endpoint_mode = str(meta.get("endpoint_mode") or "").strip()
    expected_endpoint_mode = {
        "run_pinned_single_runs": "single_runs",
        "provider_meta_declared": "standard_meta_stamped",
    }.get(authority)
    expected_endpoint = {
        "single_runs": SINGLE_RUNS_FORECAST_URL,
        "standard_meta_stamped": STANDARD_FORECAST_URL,
    }.get(endpoint_mode)
    expected_model_api_id = str(OPENMETEO_MODEL_IDS.get(model, model)).strip()
    provider_run_id = str(meta.get("provider_run_id") or "").strip()
    if (
        provider != "openmeteo"
        or str(row[6] or "").strip() != provider
        or not endpoint
        or str(meta.get("endpoint") or "").strip() != endpoint
        or not model_api_id
        or model_api_id != expected_model_api_id
        or not provider_run_id
        or provider_run_id
        != f"openmeteo:{model_api_id}:{provider_identity[0].isoformat()}"
        or authority not in {"run_pinned_single_runs", "provider_meta_declared"}
        or endpoint_mode not in {"single_runs", "standard_meta_stamped"}
        or endpoint_mode != expected_endpoint_mode
        or endpoint != expected_endpoint
    ):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_PROVIDER_BINDING_INVALID")

    mapping_values = {
        "provider_by_model": str(row[6] or ""),
        "endpoint_by_model": str(row[7] or ""),
        "request_hash_by_model": str(row[8] or ""),
        "source_run_id_by_model": str(meta.get("source_run_id") or ""),
        "provider_run_id_by_model": str(meta.get("provider_run_id") or ""),
        "model_api_id_by_model": str(meta.get("model_api_id") or ""),
        "provider_source_cycle_time_by_model_utc": provider_cycle.isoformat(),
        "provider_source_available_at_by_model_utc": provider_available.isoformat(),
        "provider_source_modified_at_by_model_utc": provider_modified.isoformat(),
        "fetch_started_times_by_model_utc": fetch_started.isoformat(),
        "fetch_finished_times_by_model_utc": fetch_finished.isoformat(),
        "source_run_authority_by_model": str(meta.get("source_run_authority") or ""),
        "endpoint_mode_by_model": str(meta.get("endpoint_mode") or ""),
    }
    for field, expected in mapping_values.items():
        mapping = witness.get(field)
        if not isinstance(mapping, Mapping) or str(mapping.get(model) or "") != expected:
            raise ValueError(f"DAY0_CAUSAL_CAPTURE_EQUIVALENCE_{field.upper()}_MISMATCH")
    capture_mapping = witness.get("capture_times_by_model_utc")
    if not isinstance(capture_mapping, Mapping) or str(capture_mapping.get(model) or "") != str(row[5]):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CAPTURE_CLOCK_MISMATCH")

    request_hash = str(row[8] or "").strip()
    source_run_id = str(meta.get("source_run_id") or "").strip()
    if (
        not request_hash
        or not source_run_id
        or str(meta.get("request_hash") or "").strip() != request_hash
        or source_run_id != f"day0_hourly:{request_hash}"
    ):
        raise ValueError("DAY0_CAUSAL_CAPTURE_EQUIVALENCE_REQUEST_BINDING_INVALID")

    semantic_meta = {
        str(key): _day0_normalize_vector_request_semantics(str(key), value, model=model)
        for key, value in meta.items()
        if str(key) not in _DAY0_CAPTURE_EQUIVALENCE_ONLY_META
    }
    return {
        "model": model,
        "city": city,
        "target_date": target_date,
        "timezone_name": timezone_name,
        "times": times,
        "temps_c": temps,
        "semantic_meta": semantic_meta,
        "capture": capture.isoformat(),
        "fetch_started": fetch_started.isoformat(),
        "fetch_finished": fetch_finished.isoformat(),
        "request_hash": request_hash,
        "source_run_id": source_run_id,
    }


def prove_day0_causal_capture_equivalence(
    *,
    expected: Mapping[str, object],
    actual: Mapping[str, object],
    current_witness: Mapping[str, object],
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    timezone_name: str,
    decision_time_utc: datetime,
    current_vectors: Iterable[Day0HourlyVector],
    remaining_window_start_utc: datetime,
) -> dict[str, object]:
    """Prove a v1 capture-only change without rebinding the original bundle.

    Both bundles must be self-consistent first.  The expected rows are checked
    against the original cutoff, while current rows are checked against the
    current decision/target-end bound.  Only capture/fetch clocks, replica-local
    provider availability/modification clocks and the derived request/source-run
    IDs may differ; run identity, every payload and every other semantic
    metadata field comes from the canonical rows.
    """

    try:
        if (
            decision_time_utc.tzinfo is None
            or decision_time_utc.utcoffset() is None
            or remaining_window_start_utc.tzinfo is None
            or remaining_window_start_utc.utcoffset() is None
        ):
            return {
                "ok": False,
                "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CLOCK_NOT_AWARE",
            }
        expected_self = validate_day0_causal_evidence_bundle(
            expected=expected, actual=expected
        )
        actual_self = validate_day0_causal_evidence_bundle(
            expected=actual, actual=actual
        )
        if not expected_self.ok or not actual_self.ok:
            return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_BUNDLE_INVALID"}
        if expected.get("city") != city or expected.get("target_date") != target_date:
            return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CONTEXT_MISMATCH"}
        if any(
            expected.get(field) != actual.get(field)
            for field in ("city", "target_date", "metric", "observation_context", "cutoff_utc")
        ):
            return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CONTEXT_MISMATCH"}
        expected_witness = expected["carrier_vector_witness"]
        actual_witness = actual["carrier_vector_witness"]
        if not isinstance(expected_witness, Mapping) or not isinstance(actual_witness, Mapping):
            return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_WITNESS_INVALID"}
        expected_ids = expected_witness.get("vector_ids_by_model")
        current_ids = current_witness.get("vector_ids_by_model")
        actual_ids = actual_witness.get("vector_ids_by_model")
        if not isinstance(expected_ids, Mapping) or not isinstance(current_ids, Mapping) or not isinstance(actual_ids, Mapping):
            return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_VECTOR_IDS_INVALID"}
        if dict(actual_ids) != dict(current_ids) or set(expected_ids) != set(current_ids):
            return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_VECTOR_SCOPE_MISMATCH"}
        cutoff = _day0_parse_aware_clock(
            expected["cutoff_utc"], field_name="bundle_cutoff_utc"
        )
        decision = decision_time_utc.astimezone(UTC)
        remaining_window_start = remaining_window_start_utc.astimezone(UTC)
        if remaining_window_start > decision:
            return {
                "ok": False,
                "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_REMAINING_WINDOW_AFTER_DECISION",
            }
        if cutoff > decision:
            return {
                "ok": False,
                "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CUTOFF_AFTER_DECISION",
            }
        local_target = date.fromisoformat(str(target_date)[:10])
        target_end = datetime.combine(
            local_target + timedelta(days=1),
            datetime_time.min,
            tzinfo=ZoneInfo(timezone_name),
        ).astimezone(UTC)
        for witness in (expected_witness, actual_witness):
            if str(witness.get("target_end_utc") or "") != target_end.isoformat():
                return {
                    "ok": False,
                    "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_TARGET_END_MISMATCH",
                }
        current_bound = min(decision, target_end)
        expected_models = tuple(
            str(model).strip()
            for model in (current_witness.get("expected_models") or ())
            if str(model).strip()
        )
        ready_vectors = select_ready_day0_hourly_vectors(
            current_vectors,
            target_date=target_date,
            now=current_bound,
            expected_models=expected_models,
            require_expected=True,
            max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
            remaining_window_start=remaining_window_start,
            require_complete_remaining_window=True,
        )
        ready_capture_by_model = {
            str(vector.model): str(vector.captured_at)
            for vector in ready_vectors
        }
        current_capture_by_model = current_witness.get(
            "capture_times_by_model_utc"
        )
        if (
            not expected_models
            or set(expected_models) != set(current_ids)
            or set(ready_capture_by_model) != set(expected_models)
            or not isinstance(current_capture_by_model, Mapping)
            or any(
                ready_capture_by_model.get(model)
                != str(current_capture_by_model.get(model) or "")
                for model in expected_models
            )
        ):
            return {
                "ok": False,
                "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_CURRENT_BUNDLE_NOT_READY",
            }
        expected_rows = {
            model: _day0_canonical_vector_row_snapshot(
                conn,
                vector_id=str(vector_id),
                model=str(model),
                city=city,
                target_date=target_date,
                timezone_name=timezone_name,
                witness=expected_witness,
                decision_bound_utc=cutoff,
            )
            for model, vector_id in expected_ids.items()
        }
        current_rows = {
            model: _day0_canonical_vector_row_snapshot(
                conn,
                vector_id=str(vector_id),
                model=str(model),
                city=city,
                target_date=target_date,
                timezone_name=timezone_name,
                witness=current_witness,
                decision_bound_utc=current_bound,
            )
            for model, vector_id in current_ids.items()
        }
        for model in expected_rows:
            expected_row = expected_rows[model]
            current_row = current_rows[model]
            identity_mismatch = (
                expected_row["model"], expected_row["city"], expected_row["target_date"], expected_row["timezone_name"]
            ) != (
                current_row["model"], current_row["city"], current_row["target_date"], current_row["timezone_name"]
            )
            # Rolling captures can shift their elapsed prefix. Compare shared
            # target-day instants, including elapsed anchors, exactly; other
            # local dates never enter this target's probability calculation.
            target = date.fromisoformat(target_date)
            tz = ZoneInfo(timezone_name)
            series = []
            for row in (expected_row, current_row):
                vector = Day0HourlyVector(
                    model=row["model"], city=row["city"],
                    target_date=row["target_date"],
                    timezone_name=row["timezone_name"],
                    captured_at=row["capture"],
                    times=tuple(row["times"]), temps_c=tuple(row["temps_c"]),
                )
                values = day0_hourly_vector_target_values_utc(
                    vector, target=target, tz=tz,
                )
                series.append(dict(values or ()))
            expected_series, current_series = series
            shared_times = sorted(expected_series.keys() & current_series.keys())
            first_disagreement = next(
                (t for t in shared_times if expected_series[t] != current_series[t]),
                None,
            )
            payload_mismatch = not shared_times or first_disagreement is not None
            if identity_mismatch or payload_mismatch:
                return {
                    "ok": False,
                    "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_PAYLOAD_MISMATCH",
                    "model": model,
                    "shared_timestamp_count": len(shared_times),
                    "first_disagreeing_timestamp": (
                        first_disagreement.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
                        if first_disagreement is not None else None
                    ),
                }
            if _day0_json_hash(expected_row["semantic_meta"]) != _day0_json_hash(current_row["semantic_meta"]):
                return {"ok": False, "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_SEMANTIC_META_MISMATCH", "model": model}
        return {
            "ok": True,
            "reason": "DAY0_CAUSAL_CAPTURE_EQUIVALENT",
            "expected_vector_ids_by_model": dict(expected_ids),
            "current_vector_ids_by_model": dict(current_ids),
            "allowed_differences": sorted(_DAY0_CAPTURE_EQUIVALENCE_ONLY_META | {"captured_at"}),
            "original_cutoff_utc": cutoff.isoformat(),
            "current_bound_utc": current_bound.isoformat(),
        }
    except (AttributeError, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        return {"ok": False, "reason": str(exc) or "DAY0_CAUSAL_CAPTURE_EQUIVALENCE_REJECTED"}


def day0_remaining_carrier_identity_inputs(
    *,
    city: str,
    unit: str,
    decision_time_utc: str,
    station_id: str,
    preliminary_survival_identity: str,
) -> dict[str, object]:
    """Build the one identity input shape shared by materialize and replay."""

    normalized_city = str(city or "").strip()
    normalized_unit = str(unit or "").strip().upper()
    normalized_decision = str(decision_time_utc or "").strip()
    normalized_station = str(station_id or "").strip().upper()
    normalized_likelihood = str(preliminary_survival_identity or "").strip().lower()
    if (
        not normalized_city
        or normalized_unit not in {"C", "F"}
        or not normalized_decision
        or not normalized_station
        or not normalized_likelihood
    ):
        raise ValueError("DAY0_REMAINING_CARRIER_IDENTITY_INPUT_INVALID")
    return {
        "city": normalized_city,
        "unit": normalized_unit,
        "probability_cutoff_utc": normalized_decision,
        "decision_time_utc": normalized_decision,
        "station_id": normalized_station,
        "awc_source_channel": "aviationweather_metar",
        "ogimet_source_channel": f"ogimet_metar_{normalized_station.lower()}",
        "preliminary_survival_identity": normalized_likelihood,
    }


def _day0_log_normal_interval_probability(
    mu: float, sigma: float, lower: float, upper: float,
) -> float:
    """Log probability for a normal interval, stable in 40-sigma tails."""

    if lower >= upper:
        return -math.inf
    z_lower = -math.inf if lower == -math.inf else (lower - mu) / sigma
    z_upper = math.inf if upper == math.inf else (upper - mu) / sigma
    if z_lower == -math.inf and z_upper == math.inf:
        return 0.0

    def log_difference(log_high: float, log_low: float) -> float:
        if math.isinf(log_low) and log_low < 0.0:
            return log_high
        if log_high <= log_low:
            return -math.inf
        return log_high + math.log(-math.expm1(log_low - log_high))

    if z_upper <= 0.0:
        return log_difference(float(log_ndtr(z_upper)), float(log_ndtr(z_lower)))
    if z_lower >= 0.0:
        return log_difference(float(log_ndtr(-z_lower)), float(log_ndtr(-z_upper)))
    # An interval crossing the mean has no severe cancellation.  Keep this
    # branch in ordinary space so the result is exact around zero as well.
    from scipy.special import ndtr

    probability = float(ndtr(z_upper) - ndtr(z_lower))
    return math.log(probability) if probability > 0.0 else -math.inf


def _day0_truncated_normal_interval_probability(
    *, mu: float, sigma: float, lower: float, upper: float,
    support_lower: float = -math.inf, support_upper: float = math.inf,
) -> float:
    """Return a normal interval mass conditioned on a one-sided support."""

    clipped_lower = max(lower, support_lower)
    clipped_upper = min(upper, support_upper)
    if clipped_lower >= clipped_upper:
        return 0.0
    log_numerator = _day0_log_normal_interval_probability(
        mu, sigma, clipped_lower, clipped_upper
    )
    log_denominator = _day0_log_normal_interval_probability(
        mu, sigma, support_lower, support_upper
    )
    if math.isinf(log_numerator) and log_numerator < 0.0:
        return 0.0
    if math.isinf(log_denominator) or not math.isfinite(log_denominator):
        raise ValueError("DAY0_REMAINING_CARRIER_TRUNCATION_INVALID")
    probability = math.exp(log_numerator - log_denominator)
    return float(min(1.0, max(0.0, probability)))


DAY0_REMAINING_ANALYTIC_OPERATOR = (
    "extreme_observed_then_noisy_future_analytic_gaussian_mixture_v1"
)


def day0_exact_remaining_probability_vector(
    *,
    future_extremes: Iterable[float],
    boundary_scenarios: Iterable[tuple[float | None, float]],
    metric: str,
    path_error_sigma: float,
    instrument_sigma: float,
    bin_bounds: Iterable[tuple[float | None, float | None]],
    settlement_semantics: SettlementSemantics,
    boundary_survival_probability: float = 1.0,
) -> np.ndarray:
    """Return exact settlement-bin probabilities for the Day0 path mixture.

    Each future provider member is ``N(mean, hypot(path_error_sigma,
    instrument_sigma))``.  A non-null boundary applies ``max``/``min`` before
    settlement rounding, and ``boundary_survival_probability`` mixes that
    censored component with the uncensored member.  All values are already in
    the settlement-native unit; settlement preimages therefore come directly
    from ``SettlementSemantics``.
    """
    values = np.sort(np.asarray(tuple(float(value) for value in future_extremes), dtype=float))
    scenarios = tuple(
        (None if boundary is None else float(boundary), float(weight))
        for boundary, weight in boundary_scenarios
    )
    bounds = tuple(
        (
            None if low is None else float(low),
            None if high is None else float(high),
        )
        for low, high in bin_bounds
    )
    if (
        metric not in {"high", "low"}
        or not values.size
        or not np.isfinite(values).all()
        or not scenarios
        or not bounds
        or not math.isfinite(path_error_sigma)
        or path_error_sigma < 0.0
        or not math.isfinite(instrument_sigma)
        or instrument_sigma < 0.0
        or not math.isfinite(boundary_survival_probability)
        or not 0.0 < boundary_survival_probability <= 1.0
        or any(
            (boundary is not None and not math.isfinite(boundary))
            or not math.isfinite(weight)
            or weight < 0.0
            for boundary, weight in scenarios
        )
        or not math.isclose(sum(weight for _, weight in scenarios), 1.0, abs_tol=1e-9)
    ):
        raise ValueError("DAY0_REMAINING_ANALYTIC_INPUT_INVALID")
    if any(
        (low is None and high is None)
        or (low is not None and not math.isclose(low, round(low), abs_tol=1e-9))
        or (high is not None and not math.isclose(high, round(high), abs_tol=1e-9))
        or (low is not None and high is not None and low > high)
        for low, high in bounds
    ):
        raise ValueError("DAY0_REMAINING_ANALYTIC_BIN_BOUNDS_INVALID")
    bounds = tuple(
        (
            None if low is None else float(round(low)),
            None if high is None else float(round(high)),
        )
        for low, high in bounds
    )
    ordered = sorted(bounds, key=lambda item: float("-inf") if item[0] is None else item[0])
    if (ordered and ordered[0][0] is not None) or (ordered and ordered[-1][1] is not None):
        raise ValueError("DAY0_REMAINING_ANALYTIC_SHOULDER_TOPOLOGY_INVALID")
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] is None or current[0] is None or current[0] != previous[1] + 1.0:
            raise ValueError("DAY0_REMAINING_ANALYTIC_BIN_GAP_OR_OVERLAP")

    sigma = math.hypot(path_error_sigma, instrument_sigma)
    low_offset, high_offset = settlement_preimage_offsets(
        settlement_semantics.rounding_rule,
        half_step=settlement_semantics.precision / 2.0,
    )

    def stable_normal_interval_probability(mu: float, lower: float, upper: float) -> float:
        """Return P(lower <= N(mu, sigma) <= upper) without tail cancellation."""
        if lower >= upper:
            return 0.0
        if sigma == 0.0:
            return 1.0 if lower <= mu < upper else 0.0
        z_low = -math.inf if lower == -math.inf else (lower - mu) / sigma
        z_high = math.inf if upper == math.inf else (upper - mu) / sigma
        if z_low >= 0.0:
            log_low = float(log_ndtr(-z_low))
            log_high = float(log_ndtr(-z_high))
            if math.isinf(log_high) and log_high < 0.0:
                return float(math.exp(log_low))
            return float(math.exp(log_low) * (-math.expm1(log_high - log_low)))
        if z_high <= 0.0:
            log_high = float(log_ndtr(z_high))
            log_low = float(log_ndtr(z_low))
            if math.isinf(log_low) and log_low < 0.0:
                return float(math.exp(log_high))
            return float(math.exp(log_high) * (-math.expm1(log_low - log_high)))
        from scipy.special import ndtr

        return float(ndtr(z_high) - ndtr(z_low))

    def exact_member_probability(mu: float, boundary: float | None) -> np.ndarray:
        out = np.zeros(len(bounds), dtype=float)
        if sigma == 0.0:
            final = mu
            if boundary is not None:
                final = max(mu, boundary) if metric == "high" else min(mu, boundary)
            settled = float(settlement_semantics.round_values([final])[0])
            for index, (low, high) in enumerate(bounds):
                if (low is None or settled >= low) and (high is None or settled <= high):
                    out[index] = 1.0
                    return out
            raise ValueError("DAY0_REMAINING_ANALYTIC_BIN_TOPOLOGY_INVALID")

        for index, (low, high) in enumerate(bounds):
            lower = -math.inf if low is None else low + low_offset
            upper = math.inf if high is None else high + high_offset
            if boundary is None:
                out[index] = stable_normal_interval_probability(mu, lower, upper)
                continue
            rounded_boundary = float(settlement_semantics.round_values([boundary])[0])
            atom_in_bin = (
                (low is None or rounded_boundary >= low)
                and (high is None or rounded_boundary <= high)
            )
            if metric == "high":
                out[index] = stable_normal_interval_probability(mu, max(lower, boundary), upper)
                if atom_in_bin:
                    out[index] += stable_normal_interval_probability(mu, -math.inf, boundary)
            else:
                out[index] = stable_normal_interval_probability(mu, lower, min(upper, boundary))
                if atom_in_bin:
                    out[index] += stable_normal_interval_probability(mu, boundary, math.inf)
        total = float(out.sum())
        if total <= 0.0 or not np.isfinite(total):
            raise ValueError("DAY0_REMAINING_ANALYTIC_BIN_TOPOLOGY_INVALID")
        return out / total

    point = np.zeros(len(bounds), dtype=float)
    for member in values:
        member_probability = np.zeros(len(bounds), dtype=float)
        for boundary, weight in scenarios:
            if boundary is None or boundary_survival_probability == 1.0:
                member_probability += float(weight) * exact_member_probability(
                    float(member), boundary
                )
            else:
                member_probability += float(weight) * (
                    boundary_survival_probability
                    * exact_member_probability(float(member), boundary)
                    + (1.0 - boundary_survival_probability)
                    * exact_member_probability(float(member), None)
                )
        point += member_probability
    point /= float(values.size)
    point_total = float(point.sum())
    if point_total <= 0.0 or not np.isfinite(point_total):
        raise ValueError("DAY0_REMAINING_ANALYTIC_BIN_TOPOLOGY_INVALID")
    return point / point_total


def _day0_sample_truncated_normal(
    rng: np.random.Generator, *, mu: np.ndarray, sigma: float,
    boundary: np.ndarray, metric: str,
) -> np.ndarray:
    """Draw one-sided conditional normals using log-CDF inverse tails."""

    uniforms = np.clip(
        rng.random(mu.shape), np.nextafter(0.0, 1.0),
        np.nextafter(1.0, 0.0),
    )
    z_boundary = (boundary - mu) / sigma
    if metric == "high":
        log_tail = log_ndtr(-z_boundary)
        # P(X >= b) is the survival tail.  Multiplication in log space keeps
        # a 40-sigma lower tail finite instead of producing 0/0.
        log_survival = log_tail + np.log1p(-uniforms)
        z = -ndtri_exp(log_survival)
    else:
        log_cdf = log_ndtr(z_boundary)
        log_probability = log_cdf + np.log(uniforms)
        z = ndtri_exp(log_probability)
    if not np.isfinite(z).all():
        raise ValueError(
            "DAY0_REMAINING_CARRIER_TRUNCATED_SAMPLE_INVALID"
        )
    return mu + sigma * z


def _native_interval_role_projection(*, windows_by_member, knots_by_member,
        paired_values, scope_start, scope_end, metric):
    """Project verified original windows; D is support, never an inside sample."""
    points, bounds = [], []
    for member in range(51):
        series = sorted(knots_by_member[member])
        def interpolate(at):
            before = max(pair for pair in series if pair[0] <= at)
            after = min(pair for pair in series if pair[0] >= at)
            if before[0] == after[0]:
                return before[1]
            fraction = (at - before[0]).total_seconds() / (after[0] - before[0]).total_seconds()
            return before[1] + fraction * (after[1] - before[1])
        windows = sorted(windows_by_member[member])
        cursor, member_points, member_bounds = scope_start, [], []
        for start, end, value in windows:
            if start > cursor:
                raise ValueError("MEASUREMENT_NATIVE_INTERVAL_GAP")
            cursor = max(cursor, end)
            if scope_start <= start and end <= scope_end:
                chosen, ambiguity = value, (value, value)
            else:
                a, b = max(start, scope_start), min(end, scope_end)
                partial = [interpolate(a), interpolate(b)] + [v for t, v in series if a < t < b]
                chosen = max(partial) if metric == "high" else min(partial)
                paired_value = paired_values[(member, start, end)]
                actual_inside = [v for t, v in series if a <= t < b]
                ambiguity = ((max([paired_value, *actual_inside]), value) if metric == "high"
                             else (value, min([paired_value, *actual_inside])))
            if not ambiguity[0] <= chosen <= ambiguity[1]:
                raise ValueError("MEASUREMENT_NATIVE_POINT_OUTSIDE_INTERVAL")
            member_points.append(chosen)
            member_bounds.append(ambiguity)
        if cursor < scope_end or not member_points:
            raise ValueError("MEASUREMENT_NATIVE_INTERVAL_GAP")
        aggregate = max if metric == "high" else min
        points.append(aggregate(member_points))
        bounds.append([aggregate(b[0] for b in member_bounds), aggregate(b[1] for b in member_bounds)])
    return points, bounds


def read_native_measurement_role(
    *, conn: Any, city: Any, target_date: str, decision_time: datetime,
    metric: str, role: str, scope_start: datetime, _paths: Any = None,
    snapshot_id: int | None = None,
    paired_snapshot_ids: Sequence[int] | None = None,
) -> dict[str, object]:
    """Read one original model point/ambiguity entity on its own lawful frontier.

    No collection, publication inference or settlement-station equivalence.
    SCOPE is city/metric/role/window. DRAIN is normal native and extrema capture;
    RESET replays their exact originals, never a midpoint or old width fallback.
    """
    from src.data.ecmwf_open_data import (
        _resolve_opendata_paths, _download_output_path, _read_role_message_bytes,
        read_native_temperature_scope, _read_native_extrema_static_scope,
    )
    from src.data.forecast_target_contract import compute_target_local_day_window_utc
    import eccodes as ec
    from scripts import extract_open_ens_localday as decoder

    day = compute_target_local_day_window_utc(
        city_timezone=str(city.timezone), target_local_date=date.fromisoformat(target_date))
    if (metric not in {"high", "low"} or role not in {"remaining_X", "full_Y"}
            or decision_time.tzinfo is None or scope_start.tzinfo is None
            or decision_time.utcoffset() != timedelta(0)
            or decision_time > datetime.now(timezone.utc)
            or not day.start_utc <= scope_start < day.end_utc
            or (role == "full_Y" and scope_start != day.start_utc)):
        raise ValueError("MEASUREMENT_ROLE_DOMAIN_INVALID")
    paths = _paths or _resolve_opendata_paths()
    cities = [{"city": str(city.name), "lat": float(city.lat), "lon": float(city.lon),
               "unit": str(city.settlement_unit).upper()}]
    from src.data.forecast_fetch_plan import data_version_for_track
    from src.config import runtime_coordinate_manifest_json
    track_name = "mx2t6_high" if metric == "high" else "mn2t6_low"
    expected_version = data_version_for_track(track_name, runtime_coordinate_manifest_json())
    # The role's source is its primary statistic, not the optional instantaneous
    # product. A complete contained extrema entity needs no fictitious 2t run.
    rows = conn.execute("""SELECT e.snapshot_id,e.provenance_json,
            COALESCE(e.source_available_at,e.available_at),e.recorded_at,
            e.source_run_id,e.source_cycle_time,s.ingest_mode,s.origin_mode
        FROM ensemble_snapshots e JOIN source_run s ON s.source_run_id=e.source_run_id
        WHERE e.city=? AND e.target_date=? AND e.temperature_metric=?
          AND s.source_id='ecmwf_open_data' AND s.track IN (?,?)
          AND s.status IN ('SUCCESS','PARTIAL') AND s.dataset_id=?
          AND s.ingest_mode IN ('SCHEDULED_LIVE','BOOT_CATCHUP') AND s.origin_mode=s.ingest_mode
          AND s.source_cycle_time=e.source_cycle_time
          AND julianday(e.source_cycle_time)<=julianday(?)
          AND (? IS NULL OR e.snapshot_id=?)
        ORDER BY e.source_cycle_time DESC,e.snapshot_id DESC LIMIT 16""",
        (str(city.name), target_date, metric, track_name + "_full_horizon", track_name + "_short_horizon",
         expected_version, min(scope_start, decision_time).isoformat(), snapshot_id, snapshot_id)).fetchall()
    last_reason = "NO_LAWFUL_ROLE_RUN"
    for row in rows:
        snapshot = row
        run_id, cycle = str(row[4]), datetime.fromisoformat(str(row[5]))
        manifest = paths.raw_root / "raw/ecmwf_open_ens/native_2t_scheduled" / f"{cycle:%Y%m%dT%HZ}" / "source-manifest.json"
        try:
            provenance = json.loads(snapshot[1])
            capture = provenance["native_capture_receipt"]
            if not isinstance(capture.get("messages"), list) or not capture["messages"]:
                raise ValueError("MEASUREMENT_NATIVE_PRIMARY_ORIGINALS_MISSING")
            snapshot_clocks = (
                _day0_parse_aware_clock(snapshot[2], field_name="interval_snapshot_available_at"),
                _day0_parse_snapshot_written_clock(snapshot[3]),
            )
            if (capture["capture_status"] != "OBSERVED" or any(
                    clock > decision_time for clock in snapshot_clocks)):
                last_reason = ("AFTER_DECISION" if any(clock > decision_time for clock in snapshot_clocks)
                    else "MEASUREMENT_NATIVE_PRIMARY_ORIGINALS_MISSING")
                continue
            first = capture["messages"][0]
            grid_sha = hashlib.sha256(base64.b64decode(next(s["bytes_base64"]
                for s in first["metadata_sections"] if s["section_number"] == 3), validate=True)).hexdigest()
            scope = _read_native_extrema_static_scope(paths=paths, run=cycle, grid_sha256=grid_sha,
                cities=cities, metric=metric, role=role, scope_start=scope_start, scope_end=day.end_utc,
                source_run_id=run_id, first_possession=max(snapshot_clocks), decision_time=decision_time,
                origin_mode=str(snapshot[6]))
            if scope.pit_status != "AVAILABLE":
                last_reason = scope.reason or scope.pit_status
                continue
            selected = scope.physical_witness["selected_cities"][str(city.name)]
            point = capture["selected_point"]
            if (int(point["flat_index"]) != int(selected["selected_flat_index"])
                    or float(point["lat"]) != float(selected["selected_lat"])
                    or float(point["lon"]) != float(selected["selected_lon"])):
                continue
            def read_intervals(track, messages):
                retained = {str(m["raw_message_sha256"]): m for m in messages}
                if len(retained) != len(messages):
                    raise ValueError("MEASUREMENT_NATIVE_INTERVAL_DUPLICATE")
                by_member = {m: [] for m in range(51)}
                part_ids = {}
                source = _download_output_path(run_date=cycle.date(), run_hour=cycle.hour,
                    param=track.open_data_param, raw_root=paths.raw_root)
                identities = []
                def original_messages():
                    if source.exists() or source.is_symlink():
                        if source.is_symlink():
                            raise ValueError("MEASUREMENT_NATIVE_ORIGINAL_SYMLINK")
                        with source.open("rb") as stream:
                            while (gid := ec.codes_grib_new_from_file(stream)) is not None:
                                try:
                                    raw = ec.codes_get_message(gid)
                                finally:
                                    ec.codes_release(gid)
                                yield raw
                    else:
                        # Retention replicas preserve the exact captured body.
                        # The canonical receipt/clock and all checks below are
                        # unchanged; a CAS file's mtime conveys no availability.
                        for saved in messages:
                            yield _read_role_message_bytes(paths.raw_root, saved)
                for raw in original_messages():
                    original, decoded = decoder._decode_native_original(raw,
                        flat_indices=(int(point["flat_index"]),), parameter_id=track.paramId)
                    h = original["observed_headers"]
                    if h.get("paramId") != track.paramId:
                        continue
                    saved = retained.get(str(original.get("raw_message_sha256")))
                    if saved != original:
                        continue
                    member = int(h.get("perturbationNumber", h.get("number", 0)))
                    start = cycle + timedelta(hours=int(h["startStep"]))
                    end = cycle + timedelta(hours=int(h["endStep"]))
                    if not (start < day.end_utc and end > scope_start):
                        continue
                    if (int(h["dataDate"]) != int(cycle.strftime("%Y%m%d"))
                            or int(h["dataTime"]) != cycle.hour * 100
                            or h["stepType"] != track.step_type or h["units"] != "K"
                            or (decoded and decoded[2]) != 161
                            or h["typeOfLevel"] != "heightAboveGround" or h["level"] != 2
                            or h["stepUnits"] != 1 or h["indicatorOfUnitForTimeRange"] != 1
                            or h["lengthOfTimeRange"] != int(h["endStep"]) - int(h["startStep"])
                            or h["lengthOfTimeRange"] != track.aggregation_window_hours
                            or h["stepRange"] != f"{h['startStep']}-{h['endStep']}"
                            or h["typeOfStatisticalProcessing"] != (2 if track.step_type == "max" else 3)
                            or hashlib.sha256(base64.b64decode(next(s["bytes_base64"]
                                for s in original["metadata_sections"] if s["section_number"] == 3))).hexdigest()
                            != scope.physical_witness["grid_sha256"]):
                        raise ValueError("MEASUREMENT_NATIVE_INTERVAL_IDENTITY_INVALID")
                    _, _, _, values = decoded
                    value = decoder.kelvin_to_native(values[0], cities[0]["unit"])
                    if not math.isfinite(value) or values[0] == decoded[1]:
                        raise ValueError("MEASUREMENT_NATIVE_INTERVAL_VALUE_INVALID")
                    if (member, start, end) in part_ids or not start < end:
                        raise ValueError("MEASUREMENT_NATIVE_INTERVAL_DUPLICATE")
                    by_member[member].append((start, end, value))
                    identities.append(original["raw_message_sha256"])
                    part_ids[(member, start, end)] = original["raw_message_sha256"]
                return by_member, identities, part_ids
            track = decoder.TRACKS["mx2t6_high" if metric == "high" else "mn2t6_low"]
            by_member, identities, primary_part_ids = read_intervals(track, capture["messages"])
            scope.physical_witness["native_interval_originals"] = sorted(identities)
            required_pairs = {(member, start, end) for member, windows in by_member.items()
                for start, end, _ in windows if not (scope_start <= start and end <= day.end_utc)}
            paired_values, paired_proofs = {}, {}
            if required_pairs:
                other_track = decoder.TRACKS["mn2t6_low" if metric == "high" else "mx2t6_high"]
                selected_pair_ids = tuple(int(value) for value in paired_snapshot_ids or ())
                if len(selected_pair_ids) > 32:
                    raise ValueError("MEASUREMENT_NATIVE_PAIRED_SCOPE_INVALID")
                id_filter = (" AND snapshot_id IN (" + ",".join("?" for _ in selected_pair_ids) + ")"
                             if selected_pair_ids else "")
                pair_rows = conn.execute("""SELECT snapshot_id,provenance_json,
                        COALESCE(source_available_at,available_at),recorded_at
                    FROM ensemble_snapshots WHERE city=? AND target_date=?
                      AND temperature_metric=? AND source_cycle_time=?""" + id_filter +
                    " ORDER BY snapshot_id ASC LIMIT 32", (str(city.name), target_date,
                        "low" if metric == "high" else "high", cycle.isoformat(), *selected_pair_ids)).fetchall()
                for pair_row in pair_rows:
                    if any(clock > decision_time for clock in (
                            _day0_parse_aware_clock(pair_row[2], field_name="paired_interval_available_at"),
                            _day0_parse_snapshot_written_clock(pair_row[3]))):
                        continue
                    paired_capture = json.loads(pair_row[1]).get("native_capture_receipt")
                    if (not isinstance(paired_capture, Mapping)
                            or paired_capture.get("capture_status") != "OBSERVED"
                            or paired_capture.get("selected_point") != point):
                        continue
                    opposite, _, opposite_ids = read_intervals(other_track, paired_capture["messages"])
                    for member, windows in opposite.items():
                        for start, end, value in windows:
                            key = (member, start, end)
                            if key in required_pairs and key not in paired_values:
                                paired_values[key] = value
                                paired_proofs[key] = {"snapshot_id": int(pair_row[0]),
                                    "member": member, "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
                                    "raw_message_sha256": opposite_ids[key],
                                    "available_at": str(pair_row[2]), "written_at": str(pair_row[3])}
                    if required_pairs <= paired_values.keys():
                        break
                if not required_pairs <= paired_values.keys():
                    raise ValueError("MEASUREMENT_NATIVE_PAIRED_INTERVAL_UNAVAILABLE")
            knots = {m: [] for m in range(51)}
            support_clocks = [_day0_parse_aware_clock(scope.physical_dependency_available_at,
                field_name="physical_dependency_available_at")]
            if required_pairs:
                product = json.loads(manifest.read_bytes())
                steps = sorted(int(s) for s in product["product_steps"])
                for start, end in sorted({(max(scope_start, a), min(day.end_utc, b))
                        for _, a, b in required_pairs}):
                    left = max(s for s in steps if cycle + timedelta(hours=s) <= start)
                    right = min(s for s in steps if cycle + timedelta(hours=s) >= end)
                    # Every clip gets its full PL support, including interior
                    # knots. The public instant-series reader stays contiguous.
                    support = read_native_temperature_scope(conn, source_run_id=product["source_run_id"],
                        manifest_path=manifest, required_steps=[s for s in steps if left <= s <= right],
                        qualified_prefix_cut_utc=start, local_day_start_utc=day.start_utc,
                        local_day_end_utc=end, explicit_manifest=cities, decision_at_utc=decision_time,
                        metric=metric, role="remaining_X", _paths=paths)
                    if support.pit_status != "AVAILABLE":
                        raise ValueError(support.reason or support.pit_status)
                    if (support.physical_witness.get("origin_mode") != "SCHEDULED_LIVE"
                            or support.physical_witness["grid_sha256"] != grid_sha
                            or support.physical_witness["selected_cities"][str(city.name)] != selected):
                        raise ValueError("MEASUREMENT_NATIVE_BOUNDARY_GRID_MISMATCH")
                    scope.physical_witness["boundary_supports"].append(support.physical_witness)
                    scope.physical_witness["temperature_messages"].extend(
                        support.physical_witness["temperature_messages"])
                    support_clocks.append(_day0_parse_aware_clock(support.physical_dependency_available_at,
                        field_name="boundary_dependency_available_at"))
                    for knot in support.native_knots:
                        value = decoder.kelvin_to_native(float(knot["value_k"]), cities[0]["unit"])
                        pair = (datetime.fromisoformat(knot["valid_time_utc"]), value)
                        if pair not in knots[int(knot["member"])]:
                            knots[int(knot["member"])].append(pair)
            messages = {m["raw_message_sha256"]: m for m in scope.physical_witness["temperature_messages"]}
            scope.physical_witness["temperature_messages"] = [messages[key] for key in sorted(messages)]
            scope.physical_witness["dependency_revision"] = "native_role_minimal_originals_v2"
            scope.physical_witness["required_original_counts"] = {
                "primary_extrema": len(identities), "paired_extrema": len(paired_proofs),
                "instantaneous": len(messages), "static": len(scope.physical_witness["static_dependencies"])}
            dependency = max(*support_clocks, *snapshot_clocks,
                *(_day0_parse_aware_clock(p["available_at"], field_name="paired_interval_available_at")
                  for p in paired_proofs.values()),
                *(_day0_parse_snapshot_written_clock(p["written_at"]) for p in paired_proofs.values()))
            if dependency >= decision_time:
                raise ValueError("AFTER_DECISION")
            points, bounds = _native_interval_role_projection(windows_by_member=by_member,
                knots_by_member=knots, paired_values=paired_values, scope_start=scope_start,
                scope_end=day.end_utc, metric=metric)
            return {"role": role, "unit": str(city.settlement_unit).upper(),
                "member_points_native": points, "member_interval_bounds_native": bounds,
                "point_model": "contained_native_extrema_straddling_2t_PL_v1",
                "ENS_current_state_transform": "NONE_RAW_NATIVE_ROLE",
                "city": str(city.name), "target_date": target_date, "temperature_metric": metric,
                "scope_start_utc": scope_start.isoformat(), "scope_end_utc": day.end_utc.isoformat(),
                "native_snapshot_id": int(snapshot[0]), "native_interval_original_sha256": sorted(identities),
                "paired_snapshot_ids": sorted({p["snapshot_id"] for p in paired_proofs.values()}),
                "paired_interval_originals": [paired_proofs[key] for key in sorted(paired_proofs)],
                "native_scope": scope.physical_witness,
                "temperature_scope_first_possession_at": scope.temperature_scope_first_possession_at,
                "interval_snapshot_available_at": str(snapshot[2]),
                "interval_snapshot_written_at": str(snapshot[3]),
                "physical_dependency_available_at": dependency.isoformat()}
        except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
            last_reason = str(exc) or type(exc).__name__
            continue
    raise ValueError(f"MEASUREMENT_ROLE_NATIVE_POINT_UNAVAILABLE:{last_reason}")


def _day0_role_noise(shape: Mapping[str, object], *, role: str,
                     centers: np.ndarray) -> tuple[float, tuple[float, float]]:
    """Point approximation and independent identification envelope, not a floor."""
    try:
        points = np.asarray(shape["member_points_native"], dtype=float)
        intervals = np.asarray(shape["member_interval_bounds_native"], dtype=float)
        served = np.asarray(shape["provider_centers_native"], dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("DAY0_DOMAIN_ROLE_SHAPE_INVALID") from exc
    if (shape.get("role") != role or points.shape != (51,) or intervals.shape != (51, 2)
            or not np.isfinite(points).all() or not np.isfinite(intervals).all()
            or not np.array_equal(np.sort(served), centers)
            or np.any(intervals[:, 0] > points) or np.any(points > intervals[:, 1])):
        raise ValueError("DAY0_DOMAIN_ROLE_SHAPE_INVALID")
    mu = float(np.mean(centers))
    point_variance = float(np.mean((points - mu) ** 2))
    near = np.maximum(intervals[:, 0] - mu, np.maximum(mu - intervals[:, 1], 0.0))
    far = np.maximum((intervals[:, 0] - mu) ** 2, (intervals[:, 1] - mu) ** 2)
    return math.sqrt(point_variance), (math.sqrt(float(np.mean(near ** 2))),
                                      math.sqrt(float(np.mean(far))))


def replay_native_measurement_role_identity(
    conn: Any, model: Mapping[str, object], *, city: Any, target_date: str,
    metric: str, decision_time: datetime,
) -> bool:
    """Reproduce the pinned scope, without letting a later partial row steal Y."""
    try:
        if (model["city"] != str(city.name) or model["target_date"] != target_date
                or model["temperature_metric"] != metric
                or model["ENS_current_state_transform"] != "NONE_RAW_NATIVE_ROLE"):
            return False
        replay = read_native_measurement_role(conn=conn, city=city, target_date=target_date,
            metric=metric, decision_time=decision_time, role=str(model["role"]),
            scope_start=_day0_parse_aware_clock(str(model["scope_start_utc"]), field_name="role_scope_start"),
            snapshot_id=int(model["native_snapshot_id"]), paired_snapshot_ids=model.get("paired_snapshot_ids"))
        return all(model.get(key) == value for key, value in replay.items())
    except (ValueError, TypeError, KeyError, OSError):
        return False


def _day0_normal_sigma_envelope(mu: float, lower: float, upper: float,
                                sigma_bounds: tuple[float, float], *,
                                lower_inclusive: bool = True,
                                upper_inclusive: bool = False) -> tuple[float, float]:
    """Gaussian mass envelope including the interior spread stationary point."""
    if lower > upper:
        return 0.0, 0.0
    lo, hi = sigma_bounds
    if lower == upper:
        atom = float(lo == 0.0 and mu == lower and lower_inclusive and upper_inclusive)
        return (atom if hi == 0.0 else 0.0), atom
    candidates = [lo, hi]
    a, b = lower - mu, upper - mu
    if math.isfinite(a) and math.isfinite(b) and a * b > 0.0 and abs(a) != abs(b):
        stationary2 = (b * b - a * a) / (2.0 * math.log(abs(b / a)))
        if stationary2 > 0.0 and lo < math.sqrt(stationary2) < hi:
            candidates.append(math.sqrt(stationary2))
    masses = []
    if lo == 0.0 and hi > 0.0:
        # A degenerate normal follows the discrete rounding contract, while
        # sigma -> 0+ divides mass at an exact preimage boundary.
        masses.append(0.5 if mu in (lower, upper) else float(lower < mu < upper))
    for sigma_value in candidates:
        if sigma_value == 0.0:
            masses.append(float((mu > lower or lower_inclusive and mu == lower)
                                and (mu < upper or upper_inclusive and mu == upper)))
        else:
            masses.append(math.exp(_day0_log_normal_interval_probability(
                mu, sigma_value, lower, upper)))
    return min(masses), max(masses)


def _build_day0_remaining_probability_carrier_v3(
    *, values: np.ndarray, final_centers: np.ndarray,
    scenarios: tuple[tuple[float | None, float], ...], metric: str,
    sigma: float, path_error_sigma: float, instrument_sigma: float,
    bounds: tuple[tuple[float | None, float | None], ...],
    n_point: int, n_samples: int, legacy_identity: str,
    economic_identity_inputs: Mapping[str, object],
    settlement_semantics: SettlementSemantics,
) -> dict[str, object]:
    """Build V3 from typed future and final-extreme components.

    Future components retain the censoring operator used by V2.  Final-extreme
    components are separate continuous centers, conditioned on the same
    report-survival boundary scenario.  Keeping the two loops separate is the
    shape-level guard against accidentally turning a final center into a
    boundary atom.
    """
    domain_shapes = economic_identity_inputs.get("domain_role_shapes")
    sigma_x = sigma_y = sigma
    sigma_ranges: dict[str, tuple[float, float]] = {}
    if domain_shapes is not None:
        if (not isinstance(domain_shapes, Mapping)
                or domain_shapes.get("schema") != "day0_measurement_domain_shapes_v1"
                or domain_shapes.get("unit") != settlement_semantics.measurement_unit):
            raise ValueError("DAY0_DOMAIN_ROLE_SHAPES_INVALID")
        families = []
        for name, role, centers in (("X", "remaining_X", values), ("Y", "full_Y", final_centers)):
            if not centers.size:
                continue
            shape = domain_shapes.get(name)
            if not isinstance(shape, Mapping):
                raise ValueError("DAY0_DOMAIN_ROLE_SHAPE_MISSING")
            if name == "Y" and any(boundary is not None and weight > 0.0 for boundary, weight in scenarios):
                if (shape.get("prefix_information_kind") not in {"REPORTED_PRODUCT_PROXY", "INCOMPLETE_SAME_QUANTITY_BOUND"}
                        or shape.get("conditioning_likelihood_scope") != "COARSENED_BOUND_ONLY"):
                    # A complete same-quantity prefix requires a joint-prefix
                    # likelihood, not the continuous prior's Y <=/>= m event.
                    # SCOPE this Y role; producer can retain qualified X experts
                    # and normally RESET after a lawful joint certificate exists.
                    raise ValueError("Y_PREFIX_LIKELIHOOD_UNIDENTIFIED")
            role_families = shape.get("provider_families")
            if (not isinstance(role_families, list) or len(role_families) != centers.size
                    or any(not isinstance(f, str) or not f for f in role_families)):
                raise ValueError("DAY0_DOMAIN_PROVIDER_FAMILIES_INVALID")
            if len(set(role_families)) != len(role_families):
                raise ValueError("DAY0_DOMAIN_PROVIDER_FAMILIES_DUPLICATED_WITHIN_ROLE")
            families.extend(role_families)
            noise, sigma_ranges[name] = _day0_role_noise(shape, role=role, centers=centers)
            if name == "X":
                sigma_x = noise
            else:
                sigma_y = noise
        if len(set(families)) < 2:
            raise ValueError("DAY0_DOMAIN_PROVIDER_FAMILIES_INCOMPLETE")
    if sigma_y == 0.0:
        for boundary, weight in scenarios:
            if boundary is None or weight <= 0.0:
                continue
            if metric == "high" and np.any(final_centers < boundary):
                raise ValueError(
                    "DAY0_REMAINING_CARRIER_FINAL_CENTER_CONTRADICTS_BOUNDARY"
                )
            if metric == "low" and np.any(final_centers > boundary):
                raise ValueError(
                    "DAY0_REMAINING_CARRIER_FINAL_CENTER_CONTRADICTS_BOUNDARY"
                )

    low_offset, high_offset = settlement_preimage_offsets(
        settlement_semantics.rounding_rule,
        half_step=settlement_semantics.precision / 2.0,
    )

    def bin_probability_vector(mu: float, boundary: float | None, sigma: float = sigma_x) -> np.ndarray:
        out = np.zeros(len(bounds), dtype=float)
        if sigma == 0.0:
            final = mu
            if boundary is not None:
                final = max(mu, boundary) if metric == "high" else min(mu, boundary)
            settled = float(settlement_semantics.round_values([final])[0])
            for index, (low, high) in enumerate(bounds):
                if (low is None or settled >= low) and (high is None or settled <= high):
                    out[index] = 1.0
                    break
            if not out.any():
                raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
            return out

        for index, (low, high) in enumerate(bounds):
            lower = -math.inf if low is None else low + low_offset
            upper = math.inf if high is None else high + high_offset
            if boundary is None:
                out[index] = math.exp(
                    _day0_log_normal_interval_probability(mu, sigma, lower, upper)
                )
                continue

            rounded_boundary = float(settlement_semantics.round_values([boundary])[0])
            atom_in_bin = (
                (low is None or rounded_boundary >= low)
                and (high is None or rounded_boundary <= high)
            )
            if metric == "high":
                out[index] = math.exp(
                    _day0_log_normal_interval_probability(
                        mu, sigma, max(lower, boundary), upper
                    )
                )
                if atom_in_bin:
                    out[index] += math.exp(
                        _day0_log_normal_interval_probability(
                            mu, sigma, -math.inf, boundary
                        )
                    )
            else:
                out[index] = math.exp(
                    _day0_log_normal_interval_probability(
                        mu, sigma, lower, min(upper, boundary)
                    )
                )
                if atom_in_bin:
                    out[index] += math.exp(
                        _day0_log_normal_interval_probability(
                            mu, sigma, boundary, math.inf
                        )
                    )
        total = float(out.sum())
        if total <= 0.0 or not np.isfinite(total):
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
        return out / total

    def final_center_probability_vector(
        mu: float, boundary: float | None, sigma: float = sigma_y,
    ) -> np.ndarray:
        if sigma == 0.0:
            return bin_probability_vector(mu, boundary, sigma)
        out = np.zeros(len(bounds), dtype=float)
        for index, (low, high) in enumerate(bounds):
            lower = -math.inf if low is None else low + low_offset
            upper = math.inf if high is None else high + high_offset
            if boundary is None:
                log_mass = _day0_log_normal_interval_probability(
                    mu, sigma, lower, upper
                )
                out[index] = math.exp(log_mass)
            elif metric == "high":
                out[index] = _day0_truncated_normal_interval_probability(
                    mu=mu, sigma=sigma, lower=lower, upper=upper,
                    support_lower=boundary,
                )
            else:
                out[index] = _day0_truncated_normal_interval_probability(
                    mu=mu, sigma=sigma, lower=lower, upper=upper,
                    support_upper=boundary,
                )
        total = float(out.sum())
        if total <= 0.0 or not np.isfinite(total):
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
        return out / total

    point = np.zeros(len(bounds), dtype=float)
    component_count = values.size + final_centers.size
    for boundary, weight in scenarios:
        if weight <= 0.0:
            continue
        component = np.zeros(len(bounds), dtype=float)
        for member in values:
            component += bin_probability_vector(float(member), boundary)
        for center in final_centers:
            component += final_center_probability_vector(float(center), boundary)
        point += float(weight) * component
    point /= float(component_count)
    point_total = float(point.sum())
    if point_total <= 0.0 or not np.isfinite(point_total):
        raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
    point /= point_total

    identification_bounds = None
    if domain_shapes is not None:
        envelope = np.zeros((len(bounds), 2), dtype=float)
        for boundary, weight in scenarios:
            for role, centers in (("X", values), ("Y", final_centers)):
                for center in centers:
                    mu = float(center)
                    spread = sigma_ranges[role]
                    for index, (low, high) in enumerate(bounds):
                        lower = -math.inf if low is None else low + low_offset
                        upper = math.inf if high is None else high + high_offset
                        if boundary is None:
                            mass = _day0_normal_sigma_envelope(mu, lower, upper, spread,
                                lower_inclusive=settlement_semantics.rounding_rule != "ceil",
                                upper_inclusive=settlement_semantics.rounding_rule == "ceil")
                        elif role == "X":
                            rounded = float(settlement_semantics.round_values([boundary])[0])
                            contains = ((low is None or rounded >= low)
                                        and (high is None or rounded <= high))
                            if metric == "high":
                                lower = -math.inf if contains else max(lower, boundary)
                            else:
                                upper = math.inf if contains else min(upper, boundary)
                            mass = _day0_normal_sigma_envelope(mu, lower, upper, spread,
                                lower_inclusive=settlement_semantics.rounding_rule != "ceil" or lower == boundary,
                                upper_inclusive=settlement_semantics.rounding_rule == "ceil" or upper == boundary)
                        else:
                            support_lo = boundary if metric == "high" else -math.inf
                            support_hi = math.inf if metric == "high" else boundary
                            numerator = _day0_normal_sigma_envelope(
                                mu, max(lower, support_lo), min(upper, support_hi), spread,
                                lower_inclusive=(support_lo > lower or settlement_semantics.rounding_rule != "ceil"),
                                upper_inclusive=(support_hi < upper or settlement_semantics.rounding_rule == "ceil"))
                            denominator = _day0_normal_sigma_envelope(
                                mu, support_lo, support_hi, spread,
                                lower_inclusive=True, upper_inclusive=True)
                            # Conservative ratio enclosure, not an exact supremum.
                            mass = ((0.0, 1.0) if denominator[0] == 0.0 else
                                    (numerator[0] / denominator[1],
                                     min(1.0, numerator[1] / denominator[0])))
                        envelope[index] += float(weight) * np.asarray(mass) / component_count
        identification_bounds = {
            "schema": "role_interval_gaussian_preimage_enclosure_v1",
            "lower": [min(float(v), float(q)) for v, q in zip(envelope[:, 0], point)],
            "upper": [max(float(v), float(q)) for v, q in zip(envelope[:, 1], point)],
            "Y_ratio": "conservative_envelope_not_exact_supremum",
        }

    v3_content = {
        "v": 5,
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_V3,
        "component_types": {
            "remaining_future_extremes": [float(x) for x in values],
            "final_extreme_centers": [float(x) for x in final_centers],
        },
        "boundary_scenarios": scenarios,
        "n_point": n_point,
        "n_samples": n_samples,
        "inputs": economic_identity_inputs,
        "sigma_source": {
            "path_error_sigma": path_error_sigma,
            "instrument_sigma": instrument_sigma,
            "combined_sigma": sigma,
            "confidence_draw_identity": legacy_identity,
            **({"X_point_sigma": sigma_x, "Y_point_sigma": sigma_y,
                "role_sigma_intervals": sigma_ranges,
                "basis": "role_member_point_disagreement_without_double_between_v1"}
               if domain_shapes is not None else {}),
        },
        "bins": bounds,
        "settlement_semantics": {
            "resolution_source": settlement_semantics.resolution_source,
            "measurement_unit": settlement_semantics.measurement_unit,
            "precision": settlement_semantics.precision,
            "rounding_rule": settlement_semantics.rounding_rule,
        },
    }
    identity = hashlib.sha256(
        json.dumps(v3_content, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

    def draw_v3(rows: int, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        future = values + rng.normal(0.0, sigma_x, (rows, values.size))
        scenario_i = rng.choice(len(scenarios), size=rows, p=[w for _, w in scenarios])
        boundary_values = np.asarray(
            [0.0 if scenarios[i][0] is None else scenarios[i][0] for i in scenario_i],
            dtype=float,
        )
        has_boundary = np.asarray(
            [scenarios[i][0] is not None for i in scenario_i], dtype=bool
        )
        bounded = (
            np.maximum(future, boundary_values[:, None])
            if metric == "high"
            else np.minimum(future, boundary_values[:, None])
        )
        future_final = np.where(has_boundary[:, None], bounded, future)
        if final_centers.size:
            center_means = np.broadcast_to(final_centers, (rows, final_centers.size))
            center_final = np.empty_like(center_means, dtype=float)
            if sigma_y == 0.0:
                center_final = center_means.copy()
            else:
                unbounded = ~has_boundary
                if np.any(unbounded):
                    center_final[unbounded] = center_means[unbounded] + rng.normal(
                        0.0, sigma_y, (int(unbounded.sum()), final_centers.size)
                    )
                if np.any(has_boundary):
                    conditional = _day0_sample_truncated_normal(
                        rng,
                        mu=center_means[has_boundary],
                        sigma=sigma_y,
                        boundary=np.broadcast_to(
                            boundary_values[has_boundary, None],
                            (int(has_boundary.sum()), final_centers.size),
                        ),
                        metric=metric,
                    )
                    center_final[has_boundary] = conditional
            all_final = np.concatenate((future_final, center_final), axis=1)
        else:
            all_final = future_final
        settled = settlement_semantics.round_values(all_final)
        out = np.empty((rows, len(bounds)), dtype=float)
        for index, (low, high) in enumerate(bounds):
            mask = np.ones(settled.shape, dtype=bool)
            if low is not None:
                mask &= settled >= low
            if high is not None:
                mask &= settled <= high
            out[:, index] = np.mean(mask, axis=1)
        totals = out.sum(axis=1, keepdims=True)
        if np.any(totals <= 0.0) or not np.isfinite(totals).all():
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
        return out / totals

    samples = draw_v3(n_samples, int(identity[:16], 16) ^ 0x9E3779B97F4A7C15)
    return {
        "q": [float(x) for x in point],
        "samples": [[float(x) for x in row] for row in samples],
        "content_identity": identity,
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_V3,
        "sample_count": n_samples,
        **({"measurement_domain_replay_inputs": {
            "identity_inputs": {key: value for key, value in economic_identity_inputs.items()
                                if key != "domain_role_shapes"},
            "boundary_scenarios": scenarios, "n_point": n_point,
        }} if domain_shapes is not None else {}),
        **({"identification_bounds": identification_bounds}
           if identification_bounds is not None else {}),
    }


def _build_day0_resolver_terminal_carrier(
    *, future_extremes_c: Iterable[float], boundary_scenarios: Iterable[tuple[float | None, float]],
    final_extreme_centers_c: Iterable[float], metric: str, path_error_sigma_c: float,
    instrument_sigma_c: float, bin_bounds_c: Iterable[tuple[float | None, float | None]],
    n_point: int, n_samples: int, identity_inputs: Mapping[str, object],
    settlement_semantics: SettlementSemantics, resolver_terminal: Any,
    remaining_center_bias_native: float,
) -> dict[str, object]:
    """Compose the exact remaining-extreme template with resolver-graded ``s``/``G-``.

    The template is this module's own carrier: the remaining-hourly members are
    shifted by ``remaining_center_bias_native`` first, then censored at the
    observed boundary and integrated on a unit settlement grid, so the
    composition can split family bins exactly at ``A``.  Its samples are
    composed row by row with hierarchical ``(s, G-)`` draws.
    """
    from src.forecast.day0_terminal_distribution import (
        compose_resolver_terminal_distribution,
        unit_settlement_grid,
    )

    scenarios = tuple(boundary_scenarios)
    if len(scenarios) != 1 or scenarios[0][0] is None or float(scenarios[0][1]) != 1.0:
        raise ValueError("DAY0_RESOLVER_TERMINAL_BOUNDARY_SCENARIO_INVALID")
    boundary = float(scenarios[0][0])
    observed = float(settlement_semantics.round_values([boundary])[0])
    bounds = []
    for low, high in bin_bounds_c:
        for edge in (low, high):
            if edge is not None and not math.isclose(float(edge), round(float(edge)), abs_tol=1e-9):
                raise ValueError("DAY0_REMAINING_CARRIER_BIN_BOUNDS_INVALID")
        bounds.append(
            (
                None if low is None else float(round(float(low))),
                None if high is None else float(round(float(high))),
            )
        )
    fine = unit_settlement_grid(
        bounds, observed=observed, steps=len(resolver_terminal.g_root_prior) - 1
    )
    template = build_day0_remaining_probability_carrier(
        future_extremes_c=future_extremes_c,
        final_extreme_centers_c=final_extreme_centers_c,
        boundary_scenarios=((boundary, 1.0),),
        metric=metric,
        path_error_sigma_c=path_error_sigma_c,
        instrument_sigma_c=instrument_sigma_c,
        bin_bounds_c=fine,
        n_point=n_point,
        n_samples=n_samples,
        identity_inputs=identity_inputs,
        settlement_semantics=settlement_semantics,
        remaining_center_bias_native=remaining_center_bias_native,
    )
    terminal = resolver_terminal.to_payload()
    content = {
        "v": 6,
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
        "template_identity": template["content_identity"],
        "template_operator": template["operator"],
        "resolver_terminal": terminal,
        "bins": bounds,
        "n_samples": n_samples,
    }
    identity = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    compose = dict(
        template_bins=fine, observed=observed, metric=metric, bins=bounds,
    )
    point = compose_resolver_terminal_distribution(
        template=template["q"],
        nonviolation_probability=resolver_terminal.nonviolation_probability,
        failure_magnitude=resolver_terminal.failure_magnitude(),
        **compose,
    )
    s_draws, g_draws = resolver_terminal.draw(
        np.random.default_rng(int(identity[:16], 16)), n_samples
    )
    samples = compose_resolver_terminal_distribution(
        template=np.asarray(template["samples"], dtype=float),
        nonviolation_probability=s_draws,
        failure_magnitude=g_draws,
        **compose,
    )
    return {
        "q": [float(x) for x in point],
        "samples": [[float(x) for x in row] for row in samples],
        "content_identity": identity,
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
        "sample_count": n_samples,
        "resolver_terminal_input": terminal,
    }


def build_day0_remaining_probability_carrier(
    *, future_extremes_c: Iterable[float], boundary_scenarios: Iterable[tuple[float | None, float]],
    final_extreme_centers_c: Iterable[float] = (),
    metric: str, path_error_sigma_c: float, instrument_sigma_c: float,
    bin_bounds_c: Iterable[tuple[float | None, float | None]], n_point: int,
    n_samples: int, identity_inputs: Mapping[str, object],
    settlement_semantics: SettlementSemantics,
    operator: str | None = None,
    remaining_center_bias_native: float = 0.0,
    resolver_terminal: Any = None,
) -> dict[str, object]:
    """Pure ``extreme(boundary, noisy future)`` carrier for both Day0 readers.

    Boundary scenarios are a statistical report-survival likelihood, not final
    settlement authority.  Noise is always applied to the future path first.
    Despite the historical ``*_c`` parameter names, all vector, boundary,
    sigma, and bin values are in the settlement-native unit selected by
    ``identity_inputs['unit']``.  This preserves the pre-change V1 contract.

    ``remaining_center_bias_native`` (src/calibration/day0_remaining_bias.py)
    moves only the remaining-hourly member centers, before the boundary and
    settlement integration, so the point q and every confidence draw see one
    shifted distribution and the content identity binds it.  The boundary and
    the typed final-extreme centers are never shifted; zero is the unshifted
    carrier, byte for byte.

    V1 is the historical Monte Carlo operator and is intentionally byte-stable.
    V2 keeps its confidence draw matrix from that same legacy stream while
    replacing only the point estimate with the exact expectation of the same
    physical Gaussian-mixture distribution.  V3 keeps the remaining future
    components censored, while typed final-extreme centers are conditioned on
    the same boundary scenario as continuous one-sided truncated normals.
    ``resolver_terminal`` (a ``Day0ResolverTerminalInput``) selects the
    resolver-graded composition instead of the survival mixture.
    """
    if resolver_terminal is not None or operator == DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER:
        if resolver_terminal is None or operator not in {
            None,
            DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
        }:
            raise ValueError("DAY0_RESOLVER_TERMINAL_OPERATOR_INPUT_MISMATCH")
        return _build_day0_resolver_terminal_carrier(
            future_extremes_c=future_extremes_c,
            boundary_scenarios=boundary_scenarios,
            final_extreme_centers_c=final_extreme_centers_c,
            metric=metric,
            path_error_sigma_c=path_error_sigma_c,
            instrument_sigma_c=instrument_sigma_c,
            bin_bounds_c=bin_bounds_c,
            n_point=n_point,
            n_samples=n_samples,
            identity_inputs=identity_inputs,
            settlement_semantics=settlement_semantics,
            resolver_terminal=resolver_terminal,
            remaining_center_bias_native=remaining_center_bias_native,
        )
    values = np.sort(
        np.asarray(tuple(float(v) for v in future_extremes_c), dtype=float)
    )
    if not math.isfinite(remaining_center_bias_native):
        raise ValueError("DAY0_REMAINING_CARRIER_CENTER_BIAS_INVALID")
    if remaining_center_bias_native:
        values = values + float(remaining_center_bias_native)
    final_centers = np.sort(
        np.asarray(tuple(float(v) for v in final_extreme_centers_c), dtype=float)
    )
    scenarios = tuple(
        (None if b is None else float(b), float(w))
        for b, w in boundary_scenarios
    )
    bounds = tuple(
        (
            None if low is None else float(low),
            None if high is None else float(high),
        )
        for low, high in bin_bounds_c
    )
    unit = str(identity_inputs.get("unit") or "").strip().upper()
    if unit not in {"C", "F"}:
        raise ValueError("DAY0_REMAINING_CARRIER_UNIT_INVALID")
    if settlement_semantics.measurement_unit != unit:
        raise ValueError("DAY0_REMAINING_CARRIER_SETTLEMENT_UNIT_MISMATCH")
    if any(
        (low is not None and not math.isclose(low, round(low), abs_tol=1e-9))
        or (high is not None and not math.isclose(high, round(high), abs_tol=1e-9))
        or (low is not None and high is not None and low > high)
        for low, high in bounds
    ):
        raise ValueError("DAY0_REMAINING_CARRIER_BIN_BOUNDS_INVALID")
    # ``bounds`` arrive on the settlement-native integer grid, but Fahrenheit
    # families round-trip through canonical Celsius storage first.  Normalize
    # the already-validated values before topology checks and probability
    # assignment so harmless conversion residue (for example 97.00000000000001)
    # cannot turn adjacent 97/98 bins into a false gap.
    bounds = tuple(
        (
            None if low is None else float(round(low)),
            None if high is None else float(round(high)),
        )
        for low, high in bounds
    )
    if any(low is None and high is None for low, high in bounds):
        raise ValueError("DAY0_REMAINING_CARRIER_OPEN_OPEN_BIN_INVALID")
    ordered = sorted(bounds, key=lambda item: float("-inf") if item[0] is None else item[0])
    if (ordered and ordered[0][0] is not None) or (
        ordered and ordered[-1][1] is not None
    ):
        raise ValueError("DAY0_REMAINING_CARRIER_SHOULDER_TOPOLOGY_INVALID")
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] is None or current[0] is None or current[0] != previous[1] + 1.0:
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_GAP_OR_OVERLAP")
    if (metric not in {"high", "low"} or not values.size or not np.isfinite(values).all()
            or not np.isfinite(final_centers).all()
            or not scenarios or not bounds or n_point < 1 or n_samples < 1
            or path_error_sigma_c < 0 or instrument_sigma_c < 0
            or not math.isclose(sum(w for _, w in scenarios), 1.0, abs_tol=1e-9)
            or any(
                (b is not None and not math.isfinite(b)) or w < 0
                for b, w in scenarios
            )):
        raise ValueError("DAY0_REMAINING_CARRIER_INPUT_INVALID")
    selected_operator = (
        DAY0_REMAINING_CARRIER_OPERATOR_V3
        if operator is None and (final_centers.size or identity_inputs.get("domain_role_shapes"))
        else DAY0_REMAINING_CARRIER_OPERATOR
        if operator is None
        else operator
    )
    if selected_operator in {
        DAY0_REMAINING_CARRIER_OPERATOR_V1,
        DAY0_REMAINING_CARRIER_OPERATOR_V2,
    } and final_centers.size:
        raise ValueError(
            "DAY0_REMAINING_CARRIER_LEGACY_OPERATOR_FINAL_CENTERS_INVALID"
        )
    if (selected_operator == DAY0_REMAINING_CARRIER_OPERATOR_V3 and not final_centers.size
            and not identity_inputs.get("domain_role_shapes")):
        raise ValueError("DAY0_REMAINING_CARRIER_V3_FINAL_CENTERS_REQUIRED")
    if selected_operator not in {
        DAY0_REMAINING_CARRIER_OPERATOR_V1,
        DAY0_REMAINING_CARRIER_OPERATOR_V2,
        DAY0_REMAINING_CARRIER_OPERATOR_V3,
    }:
        raise ValueError("unsupported Day0 remaining carrier operator")
    # Decision/cutoff clocks prove causality and freshness, but they do not
    # change the probability distribution when the selected future path and
    # physical observation inputs are unchanged. Including them in the content
    # hash also changed the Monte Carlo seed on every monitor refresh, minting
    # false q revisions that could prevent held-SELL coverage from stabilizing.
    economic_identity_inputs = {
        key: value
        for key, value in identity_inputs.items()
        if key not in {"decision_time_utc", "probability_cutoff_utc"}
    }
    # Keep this object and its serialization exactly as the pre-analytic V1
    # implementation.  It is the immutable confidence-draw identity for both
    # operators and the complete V1 receipt identity for explicit V1 replay.
    legacy_content = {"v": 3, "metric": metric, "future": sorted(values.tolist()), "scenarios": scenarios,
                      "path_sigma": path_error_sigma_c, "instrument_sigma": instrument_sigma_c,
                      "bins": bounds, "n_point": n_point, "n_samples": n_samples,
                      "settlement_semantics": {
                          "resolution_source": settlement_semantics.resolution_source,
                          "measurement_unit": settlement_semantics.measurement_unit,
                          "precision": settlement_semantics.precision,
                          "rounding_rule": settlement_semantics.rounding_rule,
                      },
                      "inputs": economic_identity_inputs}
    legacy_identity = hashlib.sha256(json.dumps(legacy_content, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    sigma = math.hypot(path_error_sigma_c, instrument_sigma_c)

    def draw(rows: int, seed: int) -> np.ndarray:
        rng = np.random.default_rng(seed)
        future = values + rng.normal(0.0, sigma, (rows, values.size))
        scenario_i = rng.choice(len(scenarios), size=rows, p=[w for _, w in scenarios])
        boundary = np.asarray(
            [0.0 if scenarios[i][0] is None else scenarios[i][0] for i in scenario_i]
        )[:, None]
        has_boundary = np.asarray(
            [scenarios[i][0] is not None for i in scenario_i], dtype=bool
        )[:, None]
        bounded = (
            np.maximum(future, boundary)
            if metric == "high"
            else np.minimum(future, boundary)
        )
        final = np.where(has_boundary, bounded, future)
        settled = settlement_semantics.round_values(final)
        out = np.empty((rows, len(bounds)), dtype=float)
        for i, (low, high) in enumerate(bounds):
            mask = np.ones(settled.shape, dtype=bool)
            if low is not None:
                mask &= settled >= low
            if high is not None:
                mask &= settled <= high
            out[:, i] = np.mean(mask, axis=1)
        totals = out.sum(axis=1, keepdims=True)
        if np.any(totals <= 0.0) or not np.isfinite(totals).all():
            raise ValueError("DAY0_REMAINING_CARRIER_BIN_TOPOLOGY_INVALID")
        out /= totals
        return out

    # Confidence rows intentionally retain the old stream.  This prevents the
    # analytic point-estimate migration from creating a one-time uncertainty
    # shock in downstream LCB/monitor consumers.
    legacy_seed = int(legacy_identity[:16], 16)
    if selected_operator != DAY0_REMAINING_CARRIER_OPERATOR_V3:
        samples = draw(n_samples, legacy_seed ^ 0x9E3779B97F4A7C15)

    if selected_operator == DAY0_REMAINING_CARRIER_OPERATOR_V1:
        point = draw(n_point, legacy_seed).mean(axis=0)
        return {
            "q": [float(x) for x in point],
            "samples": [[float(x) for x in row] for row in samples],
            "content_identity": legacy_identity,
            "operator": DAY0_REMAINING_CARRIER_OPERATOR_V1,
            "sample_count": n_samples,
        }

    if selected_operator == DAY0_REMAINING_CARRIER_OPERATOR_V3:
        return _build_day0_remaining_probability_carrier_v3(
            values=values,
            final_centers=final_centers,
            scenarios=scenarios,
            metric=metric,
            sigma=sigma,
            path_error_sigma=path_error_sigma_c,
            instrument_sigma=instrument_sigma_c,
            bounds=bounds,
            n_point=n_point,
            n_samples=n_samples,
            legacy_identity=legacy_identity,
            economic_identity_inputs=economic_identity_inputs,
            settlement_semantics=settlement_semantics,
        )

    # All inputs are already in settlement-native units.  In particular, an F
    # carrier arrives with F centers, F boundaries, F sigma, and F preimage
    # offsets; converting only the analytic path would diverge from the V1
    # confidence sampler and from the materializer's native payload.
    point = day0_exact_remaining_probability_vector(
        future_extremes=values,
        boundary_scenarios=scenarios,
        metric=metric,
        path_error_sigma=path_error_sigma_c,
        instrument_sigma=instrument_sigma_c,
        bin_bounds=bounds,
        settlement_semantics=settlement_semantics,
    )

    # V2's identity is a v4 envelope over the legacy confidence identity.  The
    # nested legacy identity deliberately retains n_point because the old
    # confidence seed included it; the analytic point estimate itself does not.
    v2_content = {
        "v": 4,
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_V2,
        "confidence_draw_identity": legacy_identity,
    }
    identity = hashlib.sha256(
        json.dumps(v2_content, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
    return {
        "q": [float(x) for x in point],
        "samples": [[float(x) for x in row] for row in samples],
        "content_identity": identity,
        "operator": DAY0_REMAINING_CARRIER_OPERATOR_V2,
        "sample_count": n_samples,
    }


def day0_remaining_carrier_samples_row_major(
    provenance: Mapping[str, object],
) -> list[list[float]] | None:
    """Return the Day0 shared-carrier draw matrix (draws x bins, row-major).

    Rows written after the 2026-09 storage fix omit the dedicated
    ``day0_remaining_carrier_probability_samples`` key whenever it would be an
    exact transpose of ``q_bootstrap_samples_by_bin`` -- i.e. whenever no
    fast-residual-likelihood mixing ran after the shared carrier was drawn
    (``q_shape == "day0_remaining_shared_carrier_v1"``). Derive it from the
    persisted per-bin draws instead, using ``bin_topology`` for column order
    (NOT ``q_bootstrap_samples_by_bin``'s own key order, which JSON
    serialization may reorder).

    Rows where fast-residual mixing DID run (``q_shape ==
    "fused_day0_fast_residual_likelihood"``) keep the dedicated key, because
    there the raw carrier and the persisted ``q_bootstrap_samples_by_bin``
    genuinely diverge (the latter is post-mixing) -- use it verbatim.

    Returns ``None`` when this row never carried a shared Day0 carrier at all
    (most replacement posteriors -- q_bootstrap_samples_by_bin exists on those
    too, for the unrelated general rho-mix path, and must not be mistaken for
    a carrier matrix), or when neither the persisted key nor a valid
    derivation is available (malformed/partial provenance).
    """
    persisted = provenance.get("day0_remaining_carrier_probability_samples")
    if persisted is not None:
        return persisted
    # Only a row that actually drew from a shared carrier ever had this key;
    # gate on a sibling field written unconditionally whenever
    # _day0_shared_carrier is not None (regardless of fast-residual mixing).
    if provenance.get("day0_remaining_carrier_content_identity") in (None, ""):
        return None
    by_bin = provenance.get("q_bootstrap_samples_by_bin")
    bin_topology = provenance.get("bin_topology")
    if not isinstance(by_bin, Mapping) or not isinstance(bin_topology, (list, tuple)):
        return None
    try:
        bin_order = [str(item["bin_id"]) for item in bin_topology]
        columns = [by_bin[bin_id] for bin_id in bin_order]
    except (KeyError, TypeError):
        return None
    if not columns or len({len(col) for col in columns}) != 1:
        return None
    n_draws = len(columns[0])
    try:
        return [
            [float(columns[c][r]) for c in range(len(columns))]
            for r in range(n_draws)
        ]
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Day0HourlyRefreshStats:
    vectors_written: int = 0
    cities_attempted: int = 0
    cities_skipped_throttle: int = 0
    cities_skipped_quota: int = 0
    incomplete_expected_bundles: int = 0
    unavailable_bundles: tuple["Day0HourlyBundleUnavailable", ...] = ()
    priority_reserve_exhausted: bool = False
    budget_exhausted: bool = False
    ready_city_dates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Day0HourlyBundleUnavailable:
    """Typed fail-closed outcome for one attempted, incomplete live bundle."""

    city: str
    target_dates: tuple[str, ...]
    expected_models: tuple[str, ...]
    available_models: tuple[str, ...]
    missing_models: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class Day0ProviderRunHwm:
    """Publicly usable provider-run scheduling witness.

    Metadata may wake an exact vector fetch, but it is never probability
    evidence. Persisted vector provenance must independently prove the same or
    a newer provider run before the bundle can be consumed.
    """

    model: str
    run_initialisation_time: datetime
    run_availability_time: datetime


# 2026-09-05 (quota root-cause, round 3): Open-Meteo serves
# https://api.open-meteo.com/data/{model}/static/meta.json from more than one replica.
# A direct probe of ecmwf_ifs during the 18Z rollout window caught two replicas
# disagreeing: both named the SAME run (last_run_initialisation_time=2026-09-05T18:00Z,
# same last_run_modification_time), but reported two different
# last_run_availability_time values (00:27:39Z vs 00:54:11Z) -- and, earlier in the
# rollout, ingest-side source_cycles logged the replicas disagreeing about which run is
# current at all (12Z vs 18Z, five flips in 12 minutes). run_initialisation_time is the
# immutable run identity; run_availability_time is freshness evidence for the +10min
# public-usability wait, never part of identity. Pin the HWM monotone per model so a
# stale replica's older run (or an earlier availability for the SAME run, which would
# only relax the usability wait) is never accepted after a newer one has been seen --
# durable across restarts/processes via the same lock-guarded state-file pattern as the
# exact-run-gap memo, since both live daemons probe this endpoint independently.
_DAY0_PROVIDER_RUN_HWM_PIN: dict[str, Day0ProviderRunHwm] = {}
_DAY0_PROVIDER_RUN_HWM_PIN_SCHEMA_VERSION = 1
_DAY0_PROVIDER_RUN_HWM_PIN_LOAD_INTERVAL_SECONDS = 15.0
_day0_provider_run_hwm_pin_state_path: Path | None = None
_day0_provider_run_hwm_pin_last_loaded_monotonic: float = 0.0


def _day0_provider_run_hwm_pin_persistence_enabled() -> bool:
    """Mirror bayes_precision_fusion_download's gap-memo gate: no file I/O under test."""
    return not (
        os.environ.get("ZEUS_TESTING") == "1" or "PYTEST_CURRENT_TEST" in os.environ
    )


def _day0_provider_run_hwm_pin_path() -> Path:
    global _day0_provider_run_hwm_pin_state_path
    if _day0_provider_run_hwm_pin_state_path is None:
        from src.config import state_path

        _day0_provider_run_hwm_pin_state_path = state_path(
            "day0_provider_run_hwm_pin.json"
        )
    return _day0_provider_run_hwm_pin_state_path


def _load_persisted_day0_provider_run_hwm_pin(*, force: bool = False) -> None:
    if not _day0_provider_run_hwm_pin_persistence_enabled():
        return
    global _day0_provider_run_hwm_pin_last_loaded_monotonic
    now = time.monotonic()
    if (
        not force
        and (now - _day0_provider_run_hwm_pin_last_loaded_monotonic)
        < _DAY0_PROVIDER_RUN_HWM_PIN_LOAD_INTERVAL_SECONDS
    ):
        return
    _day0_provider_run_hwm_pin_last_loaded_monotonic = now
    path = _day0_provider_run_hwm_pin_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return
    for model, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        try:
            run_initialisation_time = datetime.fromisoformat(
                str(entry["run_initialisation_time"])
            )
            run_availability_time = datetime.fromisoformat(
                str(entry["run_availability_time"])
            )
        except (KeyError, TypeError, ValueError):
            continue
        disk_hwm = Day0ProviderRunHwm(
            model=str(model),
            run_initialisation_time=run_initialisation_time.astimezone(UTC),
            run_availability_time=run_availability_time.astimezone(UTC),
        )
        current = _DAY0_PROVIDER_RUN_HWM_PIN.get(str(model))
        if current is None or disk_hwm.run_initialisation_time > current.run_initialisation_time:
            _DAY0_PROVIDER_RUN_HWM_PIN[str(model)] = disk_hwm


def _persist_day0_provider_run_hwm_pin(hwm: Day0ProviderRunHwm) -> None:
    if not _day0_provider_run_hwm_pin_persistence_enabled():
        return
    path = _day0_provider_run_hwm_pin_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if path.exists():
                    try:
                        on_disk = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        on_disk = {}
                else:
                    on_disk = {}
                if not isinstance(on_disk, dict):
                    on_disk = {}
                if on_disk.get("schema_version") != _DAY0_PROVIDER_RUN_HWM_PIN_SCHEMA_VERSION:
                    on_disk = {
                        "schema_version": _DAY0_PROVIDER_RUN_HWM_PIN_SCHEMA_VERSION,
                        "entries": {},
                    }
                entries = on_disk.get("entries")
                if not isinstance(entries, dict):
                    entries = {}
                    on_disk["entries"] = entries
                existing = entries.get(hwm.model)
                if isinstance(existing, dict):
                    try:
                        existing_init = datetime.fromisoformat(
                            str(existing["run_initialisation_time"])
                        ).astimezone(UTC)
                    except (KeyError, TypeError, ValueError):
                        existing_init = None
                    if existing_init is not None and existing_init >= hwm.run_initialisation_time:
                        return
                entries[hwm.model] = {
                    "run_initialisation_time": hwm.run_initialisation_time.isoformat(),
                    "run_availability_time": hwm.run_availability_time.isoformat(),
                    "recorded_at": datetime.now(UTC).isoformat(),
                }
                temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                try:
                    temp.write_text(
                        json.dumps(on_disk, sort_keys=True, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    os.replace(temp, path)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        temp.unlink()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except OSError:
        logger.debug(
            "DAY0_HOURLY_VECTORS could not persist provider-run HWM pin", exc_info=True
        )


def _apply_day0_provider_run_hwm_pin(
    probed: Mapping[str, Day0ProviderRunHwm],
) -> dict[str, Day0ProviderRunHwm]:
    """Clamp a freshly probed HWM to a monotone-per-model pin.

    A stale metadata replica may report an OLDER run, or an earlier/later
    ``run_availability_time`` for the SAME run, than one already accepted. Once a run
    has been pinned for a model, an older run is never accepted again, and the run's
    availability_time is fixed to the EARLIEST value observed for it (a later replica's
    later timestamp must never push the +10min public-usability wait backwards in time
    for a run already deemed usable).
    """
    _load_persisted_day0_provider_run_hwm_pin()
    out: dict[str, Day0ProviderRunHwm] = {}
    for model, probe in probed.items():
        pinned = _DAY0_PROVIDER_RUN_HWM_PIN.get(model)
        if pinned is None or probe.run_initialisation_time > pinned.run_initialisation_time:
            out[model] = probe
            _DAY0_PROVIDER_RUN_HWM_PIN[model] = probe
            _persist_day0_provider_run_hwm_pin(probe)
        elif probe.run_initialisation_time == pinned.run_initialisation_time:
            out[model] = Day0ProviderRunHwm(
                model=model,
                run_initialisation_time=pinned.run_initialisation_time,
                run_availability_time=min(
                    pinned.run_availability_time, probe.run_availability_time
                ),
            )
            _DAY0_PROVIDER_RUN_HWM_PIN[model] = out[model]
        else:
            # A stale replica reported a run older than the one already pinned --
            # ignore it, the pinned run is authoritative.
            out[model] = pinned
    return out


def in_domain_models_for_city(city: Any, *, models: Iterable[str] = DAY0_HOURLY_MODELS) -> list[str]:
    """Polygon-gated model list for a city (lead 0). Fail-soft to [] on gate errors."""
    try:
        from src.forecast.model_selection import load_domain_polygons, regional_eligible

        polygons = load_domain_polygons()
        lat = float(getattr(city, "lat"))
        lon = float(getattr(city, "lon"))
        return [
            model
            for model in models
            if regional_eligible(model, lat=lat, lon=lon, lead_days=0, polygons=polygons)
        ]
    except Exception as exc:  # noqa: BLE001 — gating failure means no vectors, never a crash
        logger.warning(
            "DAY0_HOURLY_VECTORS_DOMAIN_GATE_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"), type(exc).__name__, exc,
        )
        return []


def day0_hourly_models_for_city(city: Any) -> list[str]:
    """Live Day0 remaining-day hourly model set for a city.

    Regional high-resolution models are experts, not a replacement for the live
    probability chain's global evidence. Keep every available regional expert
    and the same three global deterministic models used by the current forecast
    provider set. A single global anchor is not a probability distribution: it
    erases current between-model disagreement and makes Day0 entry/exit bands
    depend on one provider path.
    """

    regional = in_domain_models_for_city(city)
    out: list[str] = []
    for model in (*regional, *GLOBAL_DAY0_HOURLY_MODELS):
        normalized = str(model or "").strip()
        if normalized and normalized not in out:
            out.append(normalized)
    return out


def day0_source_clock_ensemble_member_models() -> tuple[str, ...]:
    """Canonical row identities for one 51-member IFS025 hourly capture."""

    return tuple(
        f"{DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX}{index:02d}"
        for index in range(DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT)
    )


def probe_day0_provider_run_hwm(
    cities: Iterable[Any],
    *,
    decision_time: datetime,
    timeout_s: float,
) -> dict[str, Day0ProviderRunHwm]:
    """Read one coalesced provider-run HWM for the candidate city set."""

    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    models = tuple(
        sorted(
            {
                model
                for city in cities
                for model in day0_hourly_models_for_city(city)
                if str(model or "").strip()
            }
        )
    )
    if not models:
        return {}
    from src.data.openmeteo_model_updates import fetch_model_updates
    from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at

    updates = fetch_model_updates(
        models,
        timeout_seconds=max(0.25, float(timeout_s)),
        max_workers=max(1, min(len(models), 8)),
        priority=True,
    )
    now = decision_time.astimezone(UTC)
    out: dict[str, Day0ProviderRunHwm] = {}
    for update in updates:
        model = str(update.model or "").strip()
        if model not in models:
            continue
        if now < source_publicly_usable_at(update.to_source_run_clock()):
            continue
        out[model] = Day0ProviderRunHwm(
            model=model,
            run_initialisation_time=update.last_run_initialisation_time.astimezone(UTC),
            run_availability_time=update.last_run_availability_time.astimezone(UTC),
        )
    # QUOTA (round 3): Open-Meteo's meta.json is served from more than one replica,
    # and replicas have been observed disagreeing about which run is current. Pin
    # monotone per model so a stale replica's older run never displaces an already-
    # accepted newer one.
    return _apply_day0_provider_run_hwm_pin(out)


def _provider_run_identity_from_meta(
    payload: object,
    *,
    expected_model: str,
) -> tuple[datetime, datetime] | None:
    """Parse exact Open-Meteo provenance without local-time coercion."""

    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("model") or "").strip() != expected_model:
        return None
    if str(payload.get("provider") or "").strip() != "openmeteo":
        return None
    try:
        run = datetime.fromisoformat(
            str(payload["provider_source_cycle_time_utc"]).replace("Z", "+00:00")
        )
        available = datetime.fromisoformat(
            str(payload["provider_source_available_at_utc"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return None
    if (
        run.tzinfo is None
        or run.utcoffset() is None
        or available.tzinfo is None
        or available.utcoffset() is None
    ):
        return None
    return run.astimezone(UTC), available.astimezone(UTC)


def day0_hourly_release_due_city_dates(
    cities: Iterable[Any],
    *,
    decision_time: datetime,
    provider_run_hwm: Mapping[str, Day0ProviderRunHwm],
    conn: sqlite3.Connection | None = None,
) -> frozenset[tuple[str, str]]:
    """Return city/date scopes whose persisted vectors trail a public run HWM."""

    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
    due: set[tuple[str, str]] = set()
    try:
        for city in cities:
            city_name = str(getattr(city, "name", "") or "").strip()
            if not city_name:
                continue
            target_date = day0_hourly_target_dates_for_refresh(
                city=city, decision_time=decision_time
            )[0]
            expected_models = day0_hourly_models_for_city(city)
            required = {
                model: provider_run_hwm[model]
                for model in expected_models
                if model in provider_run_hwm
            }
            if not required:
                continue
            rows = conn.execute(
                """
                SELECT model, source_run_meta_json
                FROM day0_hourly_vectors
                WHERE city = ? AND target_date = ?
                ORDER BY captured_at DESC
                """,
                (city_name, target_date),
            ).fetchall()
            latest: dict[str, Mapping[str, object]] = {}
            for row in rows:
                model = str(row[0] or "").strip()
                if model in latest or model not in required:
                    continue
                try:
                    payload = json.loads(str(row[1] or ""))
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = None
                latest[model] = payload if isinstance(payload, Mapping) else {}
            for model, hwm in required.items():
                payload = latest.get(model)
                actual = _provider_run_identity_from_meta(
                    payload,
                    expected_model=model,
                )
                if actual is None:
                    due.add((city_name, target_date))
                    break
                # QUOTA (round 3): identity is (model, run_initialisation_time) only --
                # run_availability_time is freshness evidence, not identity, and Open-
                # Meteo's meta.json replicas have been observed disagreeing on it for
                # the SAME immutable run. Comparing the full pair made an already-
                # current persisted run look "trailing" on every replica skew.
                if actual[0] < hwm.run_initialisation_time:
                    due.add((city_name, target_date))
                    break
    finally:
        if own_conn and conn is not None:
            conn.close()
    return frozenset(due)


def _vectors_trailing_provider_hwm(
    vectors: Iterable[Day0HourlyVector],
    *,
    required_hwm: Mapping[str, Day0ProviderRunHwm],
) -> tuple[str, ...]:
    """Identify exact payloads that do not prove their scheduling HWM."""

    by_model = {str(vector.model): vector for vector in vectors}
    trailing: list[str] = []
    for model, hwm in required_hwm.items():
        vector = by_model.get(model)
        try:
            payload = json.loads(str(vector.source_run_meta_json or ""))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            trailing.append(model)
            continue
        actual = _provider_run_identity_from_meta(payload, expected_model=model)
        if actual is None:
            trailing.append(model)
            continue
        # QUOTA (round 3): identity is run_initialisation_time only; see the comment
        # in day0_hourly_release_due_city_dates above.
        if actual[0] < hwm.run_initialisation_time:
            trailing.append(model)
    return tuple(trailing)


def _current_provider_bundle_already_persisted(
    *,
    city: str,
    target_dates: Sequence[str],
    expected_models: Sequence[str],
    required_hwm: Mapping[str, Day0ProviderRunHwm],
    decision_time: datetime,
    remaining_window_starts: Mapping[str, datetime | None],
) -> bool:
    """Prove that shared storage already has this exact provider-run bundle."""

    expected = tuple(dict.fromkeys(str(model).strip() for model in expected_models))
    if not expected or set(required_hwm) != set(expected):
        return False
    try:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
        try:
            for target_date in target_dates:
                window_start = remaining_window_starts.get(str(target_date))
                if window_start is None:
                    return False
                vectors = read_freshest_day0_hourly_vectors(
                    city=city,
                    target_date=str(target_date),
                    now=decision_time,
                    expected_models=expected,
                    require_expected=True,
                    max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                    remaining_window_start=window_start,
                    require_complete_remaining_window=True,
                    conn=conn,
                    raise_on_db_error=True,
                )
                by_model = {str(vector.model): vector for vector in vectors}
                if set(by_model) != set(expected):
                    return False
                for model in expected:
                    try:
                        payload = json.loads(
                            str(by_model[model].source_run_meta_json or "")
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return False
                    actual = _provider_run_identity_from_meta(
                        payload if isinstance(payload, Mapping) else None,
                        expected_model=model,
                    )
                    hwm = required_hwm[model]
                    if actual is None:
                        return False
                    # QUOTA (round 3): a persisted bundle proves the SAME run as the
                    # current HWM by run_initialisation_time alone. Open-Meteo's
                    # meta.json is served from more than one replica and replicas have
                    # been observed disagreeing on run_availability_time for the exact
                    # same immutable run (same initialisation_time, same
                    # modification_time) -- comparing the full pair made an already-
                    # persisted, still-current bundle fail this check on every replica
                    # skew and re-fetch the whole city bundle for a run it already had.
                    if actual[0] != hwm.run_initialisation_time.astimezone(UTC):
                        return False
            return True
        finally:
            conn.close()
    except (OSError, sqlite3.Error, RuntimeError):
        return False


def _current_ensemble_bundle_already_persisted(
    *,
    city: str,
    target_dates: Sequence[str],
    run_hwm: Day0ProviderRunHwm,
    decision_time: datetime,
    remaining_window_starts: Mapping[str, datetime | None],
) -> bool:
    """Prove that shared storage already has this exact 51-member ENS bundle.

    One provider run (``ecmwf_ifs025``) backs every member row, so a single
    probed HWM is checked against each persisted member's own recorded run
    identity, mirroring ``_current_provider_bundle_already_persisted`` above
    for the deterministic models.
    """

    expected = day0_source_clock_ensemble_member_models()
    try:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
        try:
            for target_date in target_dates:
                window_start = remaining_window_starts.get(str(target_date))
                if window_start is None:
                    return False
                vectors = read_freshest_day0_hourly_vectors(
                    city=city,
                    target_date=str(target_date),
                    now=decision_time,
                    expected_models=expected,
                    require_expected=True,
                    max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                    remaining_window_start=window_start,
                    require_complete_remaining_window=True,
                    conn=conn,
                    raise_on_db_error=True,
                )
                by_model = {str(vector.model): vector for vector in vectors}
                if set(by_model) != set(expected):
                    return False
                for model in expected:
                    try:
                        payload = json.loads(
                            str(by_model[model].source_run_meta_json or "")
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        return False
                    actual = _provider_run_identity_from_meta(
                        payload if isinstance(payload, Mapping) else None,
                        expected_model=model,
                    )
                    if actual is None:
                        return False
                    # QUOTA (round 3): run identity is (model, run_initialisation_time)
                    # only -- see _current_provider_bundle_already_persisted above.
                    if actual[0] != run_hwm.run_initialisation_time.astimezone(UTC):
                        return False
            return True
        finally:
            conn.close()
    except (OSError, sqlite3.Error, RuntimeError):
        return False


def _new_ensemble_run_due_for_refresh(
    *,
    city: str,
    target_dates: Sequence[str],
    decision_time: datetime,
    remaining_window_starts: Mapping[str, datetime | None],
) -> bool:
    """A known usable ENS run may bypass success TTL, never failed backoff.

    This is a local scheduling hint only. The normal metadata-bracketed fetch
    and strict 51-member readback still establish probability evidence.
    """
    from src.state.db import get_forecasts_connection_read_only
    from src.strategy.live_inference.source_clock_vnext import (
        SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES,
    )

    _load_persisted_day0_provider_run_hwm_pin(force=True)
    hwm = _DAY0_PROVIDER_RUN_HWM_PIN.get(DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL)
    if hwm is None or hwm.model != DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL:
        return False
    if (
        hwm.run_initialisation_time > decision_time
        or hwm.run_availability_time
        + timedelta(minutes=SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES)
        > decision_time
    ):
        return False
    if _day0_provider_run_hwm_pin_persistence_enabled():
        try:
            entry = json.loads(_day0_provider_run_hwm_pin_path().read_text())["entries"][hwm.model]
            recorded = datetime.fromisoformat(str(entry["recorded_at"]))
            if recorded.tzinfo is None or recorded > decision_time:
                return False
        except (OSError, KeyError, TypeError, ValueError):
            return False
    conn = None
    try:
        conn = get_forecasts_connection_read_only()
        expected = day0_source_clock_ensemble_member_models()
        for target_date in target_dates:
            if _current_ensemble_bundle_already_persisted(
                city=city, target_dates=(str(target_date),), run_hwm=hwm,
                decision_time=decision_time,
                remaining_window_starts=remaining_window_starts,
            ):
                continue
            # Cost evidence is an exact-scope causal run identity, not the old
            # member values' freshness/completeness permission to compute q.
            rows = conn.execute(
                """SELECT model, captured_at, source_run_meta_json, request_hash
                     FROM day0_hourly_vectors
                    WHERE city=? AND target_date=? AND provider='openmeteo'
                      AND julianday(captured_at) <= julianday(?)
                    ORDER BY captured_at DESC""",
                (city, str(target_date), decision_time.isoformat()),
            )
            for model, captured_text, raw_meta, request_hash in rows:
                if str(model) not in expected or not str(request_hash or "").strip():
                    continue
                try:
                    meta = json.loads(raw_meta or "{}")
                    if not isinstance(meta, Mapping):
                        continue
                    params = json.loads(meta.get("request_params_json") or "{}")
                    if not isinstance(params, Mapping):
                        continue
                    identity = _provider_run_identity_from_meta(meta, expected_model=str(model))
                    captured = _day0_parse_aware_clock(captured_text, field_name="cost_capture")
                    started = _day0_parse_aware_clock(meta["fetch_started_at"], field_name="cost_fetch_started")
                    finished = _day0_parse_aware_clock(meta["fetch_finished_at"], field_name="cost_fetch_finished")
                except (KeyError, TypeError, ValueError):
                    continue
                if (
                    identity is not None
                    and params.get("metadata_model") == DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL
                    and identity[0] < hwm.run_initialisation_time
                    and identity[0] <= identity[1] <= finished <= decision_time
                    and captured <= started <= finished
                ):
                    return True
        return False
    except (OSError, sqlite3.Error, RuntimeError, TypeError, ValueError):
        return False
    finally:
        if conn is not None:
            conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_TABLE_DDL)
    conn.execute(_INDEX_DDL)
    conn.execute(_PRUNE_INDEX_DDL)


def _vector_id(model: str, city: str, target_date: str, captured_at: str) -> str:
    canonical = f"d0hv|{model}|{city}|{target_date}|{captured_at}"
    return "d0hv" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def build_request_hash(
    *,
    endpoint: str,
    params: dict,
    models: list[str],
    captured_at: str,
    payload: object,
) -> str:
    """Replayable provenance identity for one hourly-vector capture
    (PR#404 P1): canonicalized request params + endpoint + model list +
    captured_at bucket + response payload hash. A persisted vector row can
    always answer 'which exact request and response produced you'."""
    canonical_params = json.dumps(params, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    canonical = "|".join((
        "d0hv_req_v1", endpoint, canonical_params, ",".join(sorted(models)),
        str(captured_at)[:16],  # minute bucket: idempotent within a capture pass
        payload_hash,
    ))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _day0_provider_run_meta(
    *,
    model: str,
    model_api_id: str,
    run: datetime,
    available_at: datetime,
    modified_at: datetime | None,
    authority: str,
    endpoint_mode: str,
    request_params: Mapping[str, object],
    request_hash: str,
    fetch_started_at: datetime,
    fetch_finished_at: datetime,
    physical_response: Mapping[str, object] | None = None,
    metadata_bracket_evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build explicit provider-run provenance for one hourly vector."""

    if modified_at is None:
        raise ValueError("provider model metadata modification time is required")
    return {
        "source_run_id": f"day0_hourly:{request_hash}",
        "provider_run_id": f"openmeteo:{model_api_id}:{run.isoformat()}",
        "provider_source_cycle_time_utc": run.isoformat(),
        "provider_source_available_at_utc": available_at.isoformat(),
        "provider_source_modified_at_utc": modified_at.isoformat(),
        "source_run_authority": authority,
        "endpoint_mode": endpoint_mode,
        "model": model,
        "model_api_id": model_api_id,
        "provider": "openmeteo",
        "endpoint": request_params.get("endpoint"),
        "request_params_json": json.dumps(
            {key: value for key, value in request_params.items() if key != "endpoint"},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "request_hash": request_hash,
        "fetch_started_at": fetch_started_at.isoformat(),
        "fetch_finished_at": fetch_finished_at.isoformat(),
        **({"__physical_response_capture_v1": dict(physical_response)} if physical_response is not None else {}),
        **({"provider_metadata_bracket_evidence": dict(metadata_bracket_evidence)} if metadata_bracket_evidence is not None else {}),
    }


@dataclass(frozen=True)
class Day0RunEndpointSelection:
    """Which endpoint proves one model's exact-run hourly vector, and why."""

    endpoint_mode: str  # "single_runs" | "standard_meta_stamped"
    reason: str


def day0_hourly_past_hours(
    *,
    decision_time: datetime,
    window_start: datetime | None,
    timezone_name: str,
) -> int:
    """Hours a rolling-"now" hourly request must reach back to hold the anchor.

    The standard Forecast and Ensemble APIs start their hourly axis at the
    current local hour minus ``past_hours``.  The conditioning consumer needs
    the last grid hour at or before the causal observation boundary, which
    lags the clock by however long the last print is old.  A constant reach
    served only boundaries inside the current hour; the gap is derived here.

    The provider documents ``past_hours`` as any integer > 0 with no maximum
    (open-meteo.com/en/docs and /en/docs/ensemble-api).  The coverage gate
    already requires the boundary to lie on the target local day, so the gap
    is bounded by that day, not by a cap.
    """

    if window_start is None or window_start.tzinfo is None:
        return DAY0_HOURLY_PAST_HOURS
    tz = ZoneInfo(timezone_name)
    current_hour = decision_time.astimezone(tz).replace(
        minute=0, second=0, microsecond=0
    )
    gap_s = (current_hour - window_start.astimezone(tz)).total_seconds()
    return max(0, math.ceil(gap_s / 3600.0)) + DAY0_HOURLY_PAST_HOURS


def _select_day0_run_endpoint(
    *,
    run: datetime,
    usable_at: datetime | None,
    decision_utc: datetime,
    boundary_utc: datetime | None,
) -> Day0RunEndpointSelection:
    """Choose the endpoint that proves this model's freshest run, and why.

    Prefers the freshest run via the pinned single-runs endpoint. Falls back
    to the standard (non-pinned) endpoint -- proving whatever run its own
    meta.json bracket reports, not necessarily the freshest run -- when
    either of two gates on the freshest run fails:

    (a) it is not yet safely usable at decision time (``usable_at`` unknown,
        because the provider has not confirmed a modification time yet, or
        in the future relative to ``decision_utc``); or
    (b) its own local start (single-runs ignores ``past_hours`` and starts
        exactly at ``run``) is after the causal observation boundary the
        caller already knows about, so single-runs would leave a coverage
        gap between the boundary and the run.

    Both gates collapse into one rule and one action: the standard endpoint's
    own bracket is never ahead of what it can actually serve, so proving
    whatever run it reports resolves either failure without a second
    candidate-run list or a bigger constant.
    """

    if usable_at is None or decision_utc < usable_at:
        return Day0RunEndpointSelection(
            endpoint_mode="standard_meta_stamped",
            reason=(
                "DAY0_RUN_NOT_PUBLICLY_USABLE_AT_DECISION "
                f"run={run.isoformat()} "
                f"usable_at={'unknown' if usable_at is None else usable_at.isoformat()} "
                f"decision_utc={decision_utc.isoformat()}"
            ),
        )
    if boundary_utc is not None and run > boundary_utc:
        return Day0RunEndpointSelection(
            endpoint_mode="standard_meta_stamped",
            reason=(
                "DAY0_RUN_STARTS_AFTER_CAUSAL_BOUNDARY "
                f"run={run.isoformat()} boundary_utc={boundary_utc.isoformat()}"
            ),
        )
    return Day0RunEndpointSelection(
        endpoint_mode="single_runs",
        reason=f"DAY0_RUN_FRESHEST_USABLE run={run.isoformat()}",
    )


def _day0_exact_run_payloads(
    *,
    city: Any,
    models: list[str],
    decision_time: datetime,
    timeout_s: float,
    causal_boundary_utc: datetime | None = None,
) -> tuple[list[tuple[str, Mapping[str, object], dict[str, object]]], dict[str, object]]:
    """Fetch one exact provider run per model, preserving the raw hourly payload.

    Model metadata is read directly, never from the stale source-clock JSONL cache. The
    raw Single Runs request is delegated to the existing BPF transport adapter. When the
    freshest run fails either usability gate (see ``_select_day0_run_endpoint``), the
    standard endpoint is used instead and whatever run its own metadata bracket reports
    is accepted -- it is never asked to prove the disqualified freshest run. A genuine
    transport failure on an otherwise-selected single-runs attempt still falls back to
    proving that SAME frozen run via the standard endpoint, unchanged from before.
    ``causal_boundary_utc`` is the latest same-station observation instant already known
    to the caller (``read_day0_current_temperature_state(...).observed_at``); ``None``
    means the caller has no such boundary in hand, so only gate (a) can be evaluated here.
    Metadata and the per-model exact requests share one bounded caller budget; a
    single-city refresh therefore fails closed rather than extending the cycle.
    """
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    from src.data.bayes_precision_fusion_download import (
        _fetch_single_runs_hourly_payloads_batched,
        _fetch_standard_meta_stamped_payloads,
    )
    from src.data.openmeteo_ecmwf_ifs9_anchor import (
        SINGLE_RUNS_FORECAST_URL,
        STANDARD_FORECAST_URL,
    )
    from src.data.openmeteo_model_updates import fetch_model_updates
    from src.strategy.live_inference.source_clock_vnext import source_publicly_usable_at

    deadline_monotonic = time.monotonic() + max(1.0, float(timeout_s))

    def _remaining_budget_seconds() -> float:
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError("DAY0_PROVIDER_RUN_BUDGET_EXHAUSTED")
        return remaining

    updates = fetch_model_updates(
        models,
        timeout_seconds=_remaining_budget_seconds(),
        max_workers=max(1, min(len(models), 8)),
    )
    by_model = {str(update.model).strip(): update for update in updates}
    if set(by_model) != {str(model).strip() for model in models}:
        raise ValueError("DAY0_PROVIDER_RUN_METADATA_INCOMPLETE")
    city_name = str(getattr(city, "name", "?") or "?")
    location = (
        float(getattr(city, "lat")),
        float(getattr(city, "lon")),
        str(getattr(city, "timezone")),
        day0_hourly_target_dates_for_refresh(city=city, decision_time=decision_time),
    )
    captured_at = decision_time.astimezone(UTC).isoformat()
    past_hours = day0_hourly_past_hours(
        decision_time=decision_time,
        window_start=causal_boundary_utc,
        timezone_name=location[2],
    )
    fetched: list[tuple[str, Mapping[str, object], dict[str, object]]] = []
    request_identity: dict[str, object] = {
        "endpoint": OPENMETEO_FORECAST_URL,
        "city": city_name,
        "latitude": location[0],
        "longitude": location[1],
        "timezone": location[2],
        "hourly": "temperature_2m",
        "forecast_hours": DAY0_HOURLY_FORECAST_HOURS,
        "past_hours": past_hours,
        "temperature_unit": "celsius",
        "cell_selection": "land",
        "models": [],
        "runs": {},
        "endpoint_modes": {},
    }
    decision_utc = decision_time.astimezone(UTC)
    for model in models:
        _remaining_budget_seconds()
        model = str(model).strip()
        update = by_model[model]
        run = update.last_run_initialisation_time.astimezone(UTC)
        available_at = update.last_run_availability_time.astimezone(UTC)
        modified_at = (
            update.last_run_modification_time.astimezone(UTC)
            if update.last_run_modification_time is not None
            else None
        )
        usable_at = (
            None
            if modified_at is None
            else max(
                run,
                available_at,
                source_publicly_usable_at(update.to_source_run_clock()),
            )
        )
        selection = _select_day0_run_endpoint(
            run=run,
            usable_at=usable_at,
            decision_utc=decision_utc,
            boundary_utc=causal_boundary_utc,
        )
        model_api_id = OPENMETEO_MODEL_IDS.get(model, model)
        request_identity["models"].append(model_api_id)
        fetch_started = _day0_utc_now()
        metadata_bracket_evidence = None
        if selection.endpoint_mode == "single_runs":
            authority = "run_pinned_single_runs"
            endpoint_mode = "single_runs"
            try:
                payloads = _fetch_single_runs_hourly_payloads_batched(
                    models=[model], locations=[location], run=run,
                    forecast_hours=DAY0_HOURLY_FORECAST_HOURS,
                    deadline_monotonic=deadline_monotonic,
                    # Single Runs starts at ``run`` and ignores past_hours;
                    # a varying value would only fragment its payload cache.
                    past_hours=DAY0_HOURLY_PAST_HOURS,
                )
                payload = payloads[0]
            except Exception as single_exc:
                try:
                    payloads, transport = _fetch_standard_meta_stamped_payloads(
                        model=model, locations=[location], run=run,
                        source_available_at=available_at,
                        forecast_hours=DAY0_HOURLY_FORECAST_HOURS,
                        deadline_monotonic=deadline_monotonic,
                        past_hours=past_hours,
                    )
                    payload = payloads[0]
                    run = transport.run.astimezone(UTC)
                    available_at = transport.source_available_at.astimezone(UTC)
                    modified_at = transport.modification_time.astimezone(UTC)
                    authority = "provider_meta_declared"
                    endpoint_mode = "standard_meta_stamped"
                    metadata_bracket_evidence = transport.metadata_bracket_evidence
                except Exception as standard_exc:
                    raise ValueError(
                        f"DAY0_PROVIDER_RUN_TRANSPORT_UNAVAILABLE:{model}:"
                        f"single={type(single_exc).__name__}:standard={type(standard_exc).__name__}"
                    ) from standard_exc
        else:
            logger.info(
                "DAY0_RUN_ENDPOINT_SELECTED model=%s run=%s usable_at=%s "
                "decision_utc=%s boundary_utc=%s reason=%s",
                model, run.isoformat(),
                "unknown" if usable_at is None else usable_at.isoformat(),
                decision_utc.isoformat(),
                "unknown" if causal_boundary_utc is None else causal_boundary_utc.isoformat(),
                selection.reason,
            )
            authority = "provider_meta_declared"
            endpoint_mode = "standard_meta_stamped"
            try:
                payloads, transport = _fetch_standard_meta_stamped_payloads(
                    model=model, locations=[location], run=None,
                    source_available_at=available_at,
                    forecast_hours=DAY0_HOURLY_FORECAST_HOURS,
                    deadline_monotonic=deadline_monotonic,
                    past_hours=past_hours,
                )
                payload = payloads[0]
                run = transport.run.astimezone(UTC)
                available_at = transport.source_available_at.astimezone(UTC)
                modified_at = transport.modification_time.astimezone(UTC)
                metadata_bracket_evidence = transport.metadata_bracket_evidence
            except Exception as standard_exc:
                raise ValueError(
                    f"DAY0_PROVIDER_RUN_TRANSPORT_UNAVAILABLE:{model}:"
                    f"single=skipped:standard={type(standard_exc).__name__}"
                ) from standard_exc
        fetch_finished = _day0_utc_now()
        # Set after any run/endpoint substitution above -- this must name the
        # run actually used, never the originally-selected freshest run.
        request_identity["runs"][model] = run.isoformat()
        request_identity["endpoint_modes"][model] = endpoint_mode
        fetched.append((model, payload, {
            "model_api_id": model_api_id,
            "run": run,
            "available_at": available_at,
            "modified_at": modified_at,
            "authority": authority,
            "endpoint_mode": endpoint_mode,
            "fetch_started": fetch_started,
            "fetch_finished": fetch_finished,
            "metadata_bracket_evidence": metadata_bracket_evidence,
        }))
    request_identity_payload = {
        **request_identity,
        "runs": dict(sorted(request_identity["runs"].items())),
        "models": tuple(request_identity["models"]),
    }
    bundle_hash = build_request_hash(
        endpoint=OPENMETEO_FORECAST_URL, params=request_identity_payload,
        models=models, captured_at=captured_at,
        payload={model: payload for model, payload, _meta in fetched},
    )
    return ([(model, payload, _day0_provider_run_meta(
        model=model, model_api_id=str(meta["model_api_id"]), run=meta["run"],
        available_at=meta["available_at"], modified_at=meta["modified_at"],
        authority=str(meta["authority"]), endpoint_mode=str(meta["endpoint_mode"]),
        request_params={
            **request_identity_payload,
            "endpoint": (
                SINGLE_RUNS_FORECAST_URL
                if str(meta["endpoint_mode"]) == "single_runs"
                else STANDARD_FORECAST_URL
            ),
            "model": model,
                        "model_api_id": meta["model_api_id"],
                        "run": meta["run"].isoformat()},
        request_hash=bundle_hash, fetch_started_at=meta["fetch_started"],
        fetch_finished_at=meta["fetch_finished"],
        physical_response=payload.get("__physical_response_capture_v1"),
        metadata_bracket_evidence=meta.get("metadata_bracket_evidence"),
    )) for model, payload, meta in fetched], request_identity_payload)


def fetch_day0_hourly_vectors(
    city: Any,
    *,
    models: Optional[list[str]] = None,
    now: Optional[datetime] = None,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    causal_boundary_utc: Optional[datetime] = None,
) -> tuple[list[Day0HourlyVector], str]:
    """Fetch exact-run hourly temperature curves for in-domain models.

    Returns (vectors, request_hash) — the hash is the replayable
    provenance identity persisted with every row (PR#404 P1: empty provenance
    identity is not acceptable for q-construction inputs). Fail-soft:
    ([], "") on any transport/shape error. ``causal_boundary_utc``, when the
    caller has it, is the latest same-station observation instant the
    exact-run selection must not start after (see ``_select_day0_run_endpoint``).
    """
    chosen = models if models is not None else day0_hourly_models_for_city(city)
    if not chosen:
        return [], ""
    # This is the local request/capture clock used for vector row identity;
    # possession is the separate fetch_finished_at in source_run_meta_json.
    source_time = (now or _day0_utc_now()).astimezone(UTC)
    captured_at = source_time.isoformat()
    try:
        fetched, _request_identity = _day0_exact_run_payloads(
            city=city,
            models=[str(model).strip() for model in chosen if str(model).strip()],
            decision_time=source_time,
            timeout_s=timeout_s,
            causal_boundary_utc=causal_boundary_utc,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft lane
        logger.warning(
            "DAY0_HOURLY_VECTORS_FETCH_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"), type(exc).__name__, exc,
        )
        return [], ""
    request_hash = str(fetched[0][2].get("request_hash") or "")
    if not request_hash:
        return [], ""
    vectors: list[Day0HourlyVector] = []
    for model, payload, source_meta in fetched:
        vectors.extend(parse_openmeteo_hourly_payload(
            payload, city=city, models=[model], captured_at=captured_at,
            source_run_meta_json=json.dumps(
                source_meta, sort_keys=True, separators=(",", ":")
            ),
        ))
    return (
        vectors,
        request_hash,
    )


def _same_model_update(left: Any, right: Any) -> bool:
    """Require the metadata bracket to name one immutable provider run.

    QUOTA (round 3): run_initialisation_time and last_run_modification_time have been
    directly confirmed identical across Open-Meteo meta.json replicas for the SAME run;
    only run_availability_time differs by replica (a CDN-edge serving timestamp, not
    provider-run identity). Comparing availability_time here made this torn-read guard
    spuriously reject an in-flight fetch whenever the second meta.json probe happened to
    land on a different replica than the first, discarding an otherwise-good fetch.
    """

    return bool(
        left is not None
        and right is not None
        and left.last_run_initialisation_time == right.last_run_initialisation_time
        and left.last_run_modification_time == right.last_run_modification_time
    )


def parse_openmeteo_ensemble_hourly_payload(
    payload: object,
    *,
    city: Any,
    captured_at: str,
    source_meta_by_member: Mapping[str, Mapping[str, object]],
) -> list[Day0HourlyVector]:
    """Parse one complete IFS025 control+50 perturbed-member response.

    Open-Meteo names the control field ``temperature_2m`` and perturbed
    members ``temperature_2m_member01`` ... ``member50``.  A partial response
    is unusable: the current-evidence within-spread must retain all 51 members.
    """

    if not isinstance(payload, Mapping) or not isinstance(
        payload.get("hourly"), Mapping
    ):
        return []
    hourly = payload["hourly"]
    times = hourly.get("time")
    if not isinstance(times, (list, tuple)) or not times:
        return []
    expected = day0_source_clock_ensemble_member_models()
    if set(source_meta_by_member) != set(expected):
        return []
    vectors: list[Day0HourlyVector] = []
    for index, model in enumerate(expected):
        key = "temperature_2m" if index == 0 else f"temperature_2m_member{index:02d}"
        values = hourly.get(key)
        if not isinstance(values, (list, tuple)) or len(values) != len(times):
            return []
        pairs: list[tuple[str, float]] = []
        for timestamp, raw in zip(times, values, strict=True):
            if raw is None or isinstance(raw, bool):
                return []
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return []
            if not math.isfinite(value):
                return []
            pairs.append((str(timestamp), value))
        vectors.append(
            Day0HourlyVector(
                model=model,
                city=str(getattr(city, "name", "") or ""),
                target_date="",
                timezone_name=str(getattr(city, "timezone")),
                captured_at=captured_at,
                times=tuple(timestamp for timestamp, _value in pairs),
                temps_c=tuple(value for _timestamp, value in pairs),
                source_run_meta_json=json.dumps(
                    source_meta_by_member[model],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return vectors


def _probe_day0_source_clock_ensemble_run_hwm(
    *,
    decision_time: datetime,
    timeout_s: float,
) -> Day0ProviderRunHwm | None:
    """Cheap meta-only probe of the ENS carrier's current provider run.

    Folded through the same monotone HWM pin used for deterministic models,
    but under the ensemble metadata namespace. The deterministic
    ``ecmwf_ifs025`` metadata endpoint is a different provider domain and may
    report a different run from the 51-member ensemble carrier.
    """

    try:
        from src.data.openmeteo_model_updates import fetch_model_updates

        updates = fetch_model_updates(
            [DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL],
            timeout_seconds=max(0.25, float(timeout_s)),
            max_workers=1,
            priority=True,
        )
    except Exception:  # noqa: BLE001 - probe failure just skips the dedup check
        return None
    if len(updates) != 1:
        return None
    update = updates[0]
    if update.model != DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL:
        return None
    probe = Day0ProviderRunHwm(
        model=DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL,
        run_initialisation_time=update.last_run_initialisation_time.astimezone(UTC),
        run_availability_time=update.last_run_availability_time.astimezone(UTC),
    )
    pinned = _apply_day0_provider_run_hwm_pin(
        {DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL: probe}
    )
    return pinned[DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL]


def fetch_day0_source_clock_ensemble_vectors(
    city: Any,
    *,
    now: Optional[datetime] = None,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    window_start: Optional[datetime] = None,
) -> tuple[list[Day0HourlyVector], str]:
    """Fetch one possession-bracketed 51-member hourly ENS carrier.

    ``window_start`` is the earliest causal boundary the carrier must cover;
    the request reaches back to its anchor hour (``day0_hourly_past_hours``).

    The provider metadata is read before and after the response.  A run change
    inside that bracket discards the payload, so a local fetch clock can never
    masquerade as provider-cycle identity.  The standard Ensemble API response
    is accepted only with that exact metadata bracket and is persisted through
    the same replayable request hash as deterministic Day0 paths.
    """

    from src.data.openmeteo_client import fetch as fetch_openmeteo
    from src.data.openmeteo_model_updates import fetch_model_updates
    from src.strategy.live_inference.source_clock_vnext import (
        source_publicly_usable_at,
    )

    decision_time = (now or _day0_utc_now()).astimezone(UTC)
    captured_at = decision_time.isoformat()
    try:
        before_rows = fetch_model_updates(
            [DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL],
            timeout_seconds=max(0.25, float(timeout_s)),
            max_workers=1,
            priority=True,
        )
        if len(before_rows) != 1:
            return [], ""
        before = before_rows[0]
        if (
            before.model != DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL
            or before.last_run_modification_time is None
            or before.last_run_initialisation_time > decision_time
            or before.last_run_availability_time > decision_time
            or decision_time < source_publicly_usable_at(before.to_source_run_clock())
        ):
            return [], ""
        params = {
            "latitude": float(getattr(city, "lat")),
            "longitude": float(getattr(city, "lon")),
            "hourly": "temperature_2m",
            "models": DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
            "timezone": str(getattr(city, "timezone")),
            "forecast_hours": DAY0_HOURLY_FORECAST_HOURS,
            "past_hours": day0_hourly_past_hours(
                decision_time=decision_time,
                window_start=window_start,
                timezone_name=str(getattr(city, "timezone")),
            ),
            "temperature_unit": "celsius",
            "cell_selection": "land",
        }
        metadata_params = {
            **params,
            "metadata_model": DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL,
        }
        fetch_started = _day0_utc_now()
        payload = fetch_openmeteo(
            OPENMETEO_ENSEMBLE_URL,
            params,
            timeout=max(0.25, float(timeout_s)),
            max_retries=1,
            endpoint_label="day0_source_clock_ensemble",
        )
        fetch_finished = _day0_utc_now()
        after_rows = fetch_model_updates(
            [DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL],
            timeout_seconds=max(0.25, float(timeout_s)),
            max_workers=1,
            priority=True,
        )
        after = after_rows[0] if len(after_rows) == 1 else None
        if (
            after is None
            or after.model != DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL
            or not _same_model_update(before, after)
        ):
            return [], ""
        request_hash = build_request_hash(
            endpoint=OPENMETEO_ENSEMBLE_URL,
            params={
                **metadata_params,
                "provider_run": before.last_run_initialisation_time.isoformat(),
            },
            models=[DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL],
            captured_at=captured_at,
            payload=payload,
        )
        member_meta: dict[str, Mapping[str, object]] = {}
        for model in day0_source_clock_ensemble_member_models():
            member_meta[model] = _day0_provider_run_meta(
                model=model,
                model_api_id=DAY0_SOURCE_CLOCK_ENSEMBLE_MODEL,
                run=before.last_run_initialisation_time.astimezone(UTC),
                available_at=before.last_run_availability_time.astimezone(UTC),
                modified_at=before.last_run_modification_time.astimezone(UTC),
                authority="provider_meta_declared",
                endpoint_mode="ensemble_meta_stamped",
                request_params={
                    **metadata_params,
                    "endpoint": OPENMETEO_ENSEMBLE_URL,
                    "run": before.last_run_initialisation_time.isoformat(),
                },
                request_hash=request_hash,
                fetch_started_at=fetch_started,
                fetch_finished_at=fetch_finished,
            )
        vectors = parse_openmeteo_ensemble_hourly_payload(
            payload,
            city=city,
            captured_at=captured_at,
            source_meta_by_member=member_meta,
        )
        if len(vectors) != DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT:
            return [], ""
        return vectors, request_hash
    except Exception as exc:  # noqa: BLE001 - missing carrier leaves ENTRY fail-closed.
        logger.warning(
            "DAY0_SOURCE_CLOCK_ENSEMBLE_FETCH_FAILED city=%s exc=%s: %s",
            getattr(city, "name", "?"),
            type(exc).__name__,
            exc,
        )
        return [], ""


def _day0_original_entity(vector: Day0HourlyVector, meta: Mapping[str, object], *, city: Any = None) -> dict:
    """Replay one deterministic provider entity, not request-coordinate echoes.

    SCOPE: this vector's model/location/run and consumed projection. DRAIN: a
    normal capture retains its genuine original descriptor. RESET: the same
    immutable evidence replays independently; no newest-row equality is required.
    Native ENS members retain their separate original-message/member contract.
    """
    from src.data.bayes_precision_fusion_capture import OPENMETEO_MODEL_IDS
    from src.data.openmeteo_ecmwf_ifs9_anchor import SINGLE_RUNS_FORECAST_URL, STANDARD_FORECAST_URL
    capture = meta.get("__physical_response_capture_v1")
    if not isinstance(capture, Mapping):
        raise ValueError("DAY0_ORIGINAL_ENTITY_MISSING")
    params = capture["request_params"]
    if (capture["revision"] != "openmeteo_single_model_entity_body_v1"
            or capture["model"] != vector.model or meta.get("model") != vector.model
            or meta.get("provider") != "openmeteo"
            or params["models"] != OPENMETEO_MODEL_IDS.get(vector.model, vector.model)
            or meta.get("model_api_id") != params["models"]
            or meta.get("provider_run_id") != f"openmeteo:{params['models']}:{capture['source_cycle_time']}"
            or params.get("temperature_unit") != "celsius" or params.get("hourly") != "temperature_2m"
            or params.get("cell_selection", "land") != "land" or params.get("elevation") is not None
            or capture["temperature_unit"] != "celsius" or capture["native_variable"] != "temperature_2m"
            or capture["source_cycle_time"] != meta["provider_source_cycle_time_utc"]):
        raise ValueError("DAY0_ORIGINAL_MODEL_RUN_UNIT_MISMATCH")
    endpoint = {"single_runs": SINGLE_RUNS_FORECAST_URL, "standard_meta_stamped": STANDARD_FORECAST_URL}.get(meta.get("endpoint_mode"))
    if (endpoint is None or capture["request_url"] != endpoint or meta.get("endpoint") != endpoint
            or meta.get("source_run_authority") != {"single_runs": "run_pinned_single_runs",
                "standard_meta_stamped": "provider_meta_declared"}.get(meta.get("endpoint_mode"))):
        raise ValueError("DAY0_ORIGINAL_ENDPOINT_MISMATCH")
    run = _day0_parse_aware_clock(capture["source_cycle_time"], field_name="original_run")
    if endpoint == SINGLE_RUNS_FORECAST_URL:
        pinned = datetime.fromisoformat(str(params["run"]))
        if pinned.tzinfo is None:
            pinned = pinned.replace(tzinfo=UTC)  # this protocol's explicit run parameter
        if pinned != run:
            raise ValueError("DAY0_ORIGINAL_PINNED_RUN_MISMATCH")
    body = Path(str(capture["artifact_path"])).read_bytes()
    if len(body) != capture["byte_size"] or hashlib.sha256(body).hexdigest() != capture["sha256"]:
        raise ValueError("DAY0_ORIGINAL_BODY_HASH_MISMATCH")
    decoded = json.loads(body)
    payloads = [decoded] if isinstance(decoded, dict) else decoded
    index = capture["location_index"]
    locations = capture["locations"]
    if (type(index) is not int or not isinstance(payloads, list) or not 0 <= index < len(payloads)
            or len(locations) != len(payloads)):
        raise ValueError("DAY0_ORIGINAL_LOCATION_INDEX_INVALID")
    payload, point = payloads[index], locations[index]
    requests = list(zip(str(params["latitude"]).split(","), str(params["longitude"]).split(","),
                        str(params["timezone"]).split(","), strict=True))
    scope = json.loads(str(meta["request_params_json"]))
    if len(requests) != len(payloads):
        raise ValueError("DAY0_ORIGINAL_LOCATION_COUNT_MISMATCH")
    latitude, longitude, zone = requests[index]
    if city is None:
        from src.config import runtime_cities_by_name
        city = runtime_cities_by_name().get(vector.city)
    if (city is None or str(getattr(city, "name", "")) != vector.city
            or scope.get("city") != vector.city
            or float(getattr(city, "lat")) != float(latitude)
            or float(getattr(city, "lon")) != float(longitude)
            or str(getattr(city, "timezone")) != zone):
        raise ValueError("DAY0_ORIGINAL_CITY_REQUEST_BINDING_MISMATCH")
    if (float(latitude) != float(scope["latitude"]) or float(longitude) != float(scope["longitude"])
            or float(point["requested_latitude"]) != float(latitude)
            or float(point["requested_longitude"]) != float(longitude)
            or zone != vector.timezone_name or point["timezone"] != zone or payload["timezone"] != zone):
        raise ValueError("DAY0_ORIGINAL_REQUESTED_LOCATION_MISMATCH")
    for key, proofkey, low, high in (("latitude", "selected_latitude", -90, 90),
            ("longitude", "selected_longitude", -180, 180), ("elevation", "target_dem_elevation_m", -500, 9000)):
        actual = float(payload[key])
        if not math.isfinite(actual) or not low <= actual <= high or actual != float(point[proofkey]):
            raise ValueError("DAY0_ORIGINAL_RETURNED_POINT_MISMATCH")
    lat1, lat2 = math.radians(float(latitude)), math.radians(float(payload["latitude"]))
    dlat, dlon = lat2-lat1, math.radians(float(payload["longitude"])-float(longitude))
    hav = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    if 2*6371.0088*math.asin(math.sqrt(min(1., hav))) >= 50.:
        raise ValueError("DAY0_ORIGINAL_WRONG_SITE")  # existing wrong-site bound, not sensor equivalence
    keyed = f"temperature_2m_{OPENMETEO_MODEL_IDS.get(vector.model, vector.model)}"
    units = payload["hourly_units"]
    if units.get(keyed, units.get("temperature_2m")) != "°C":
        raise ValueError("DAY0_ORIGINAL_NATIVE_UNIT_MISMATCH")
    raw_times, raw_values = payload["hourly"]["time"], payload["hourly"].get(keyed, payload["hourly"].get("temperature_2m"))
    if (not isinstance(raw_times, list) or not isinstance(raw_values, list) or len(raw_times) != len(raw_values)
            or not raw_times or len(set(raw_times)) != len(raw_times)):
        raise ValueError("DAY0_ORIGINAL_ARRAY_INVALID")
    scope_hours = scope.get("forecast_hours", len(raw_times))
    transport_hours = params.get("forecast_hours", len(raw_times))
    if (type(scope_hours) is not int or type(transport_hours) is not int
            or not 0 < scope_hours <= transport_hours):
        raise ValueError("DAY0_ORIGINAL_TRANSPORT_PROJECTION_MISMATCH")
    limit = scope_hours if endpoint == SINGLE_RUNS_FORECAST_URL else len(raw_times)
    pairs = [(str(t), float(v)) for t, v in zip(raw_times[:limit], raw_values[:limit], strict=True)
             if v is not None and not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(float(v))]
    if tuple(t for t, _ in pairs) != vector.times or tuple(v for _, v in pairs) != vector.temps_c:
        raise ValueError("DAY0_ORIGINAL_CONSUMED_ARRAY_MISMATCH")
    if endpoint == STANDARD_FORECAST_URL:
        evidence = meta.get("provider_metadata_bracket_evidence")
        if not isinstance(evidence, Mapping) or evidence.get("status") != "CAPTURED":
            raise ValueError("DAY0_ORIGINAL_METADATA_BRACKET_MISSING")
        encoded = Path(str(evidence["manifest_path"])).read_bytes()
        if hashlib.sha256(encoded).hexdigest() != evidence["sha256"]:
            raise ValueError("DAY0_ORIGINAL_METADATA_BRACKET_HASH_MISMATCH")
        bracket = json.loads(encoded)
        if (bracket["revision"] != "openmeteo_standard_metadata_bracket_v1"
                or bracket["model"] != vector.model or bracket["model_reference_time_utc"] != run.isoformat()
                or bracket["provider_modification_time_utc"] != meta["provider_source_modified_at_utc"]
                or bracket["temperature"]["body_sha256"] != capture["sha256"]
                or bracket["temperature"]["byte_size"] != capture["byte_size"]
                or bracket["temperature"]["request_url"] != endpoint
                or bracket["temperature"]["request_params"] != params
                or bracket["temperature"]["fetched_at_utc"] != capture["captured_at"]):
            raise ValueError("DAY0_ORIGINAL_METADATA_TEMPERATURE_MISMATCH")
        from src.data.openmeteo_model_updates import parse_model_update, metadata_model_id
        from urllib.parse import urlsplit
        possession = []
        for role in ("before", "after"):
            original = bracket[role]
            url = urlsplit(str(original["request_url"]))
            if (url.scheme != "https" or url.hostname != "api.open-meteo.com"
                    or url.path != f"/data/{metadata_model_id(vector.model)}/static/meta.json"
                    or original["request_params"] or original["response_role"] != "NETWORK_200_ENTITY"
                    or original["clock_role"] != "LOCAL_HTTP_ENTITY_POSSESSION"):
                raise ValueError("DAY0_ORIGINAL_METADATA_SOURCE_MISMATCH")
            encoded_meta = Path(original["artifact_path"]).read_bytes()
            if len(encoded_meta) != original["byte_size"] or hashlib.sha256(encoded_meta).hexdigest() != original["body_sha256"]:
                raise ValueError("DAY0_ORIGINAL_METADATA_BODY_MISMATCH")
            update = parse_model_update(vector.model, json.loads(encoded_meta))
            if update.last_run_initialisation_time != run or update.last_run_modification_time.isoformat() != bracket["provider_modification_time_utc"]:
                raise ValueError("DAY0_ORIGINAL_METADATA_RUN_MISMATCH")
            possession.append(_day0_parse_aware_clock(original["fetched_at_utc"], field_name=role))
        if not (run <= possession[0] <= possession[1]
                <= _day0_parse_aware_clock(meta["fetch_finished_at"], field_name="fetch_finished")
                and _day0_parse_aware_clock(capture["captured_at"], field_name="body_possession") <= possession[1]):
            raise ValueError("DAY0_ORIGINAL_METADATA_POSSESSION_INVALID")
    return {"capture": capture, "point": point, "run": run}


def _day0_artifact(conn: sqlite3.Connection, artifact_id: int) -> dict:
    columns = ("artifact_id", "source_id", "product_id", "data_version", "source_cycle_time",
        "source_available_at", "captured_at", "artifact_path", "sha256", "byte_size", "request_url",
        "request_params_json", "artifact_metadata_json", "recorded_at")
    row = conn.execute("SELECT " + ",".join(columns) + " FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    if row is None:
        raise ValueError("DAY0_ORIGINAL_ARTIFACT_ROW_MISSING")
    artifact = dict(zip(columns, row, strict=True))
    artifact["metadata"] = artifact.pop("artifact_metadata_json")
    return artifact


def _day0_register_vector_original(conn: sqlite3.Connection, vector: Day0HourlyVector, meta: dict) -> dict:
    from src.data.bayes_precision_fusion_download import _persist_physical_response_artifact, _persist_http_capture_receipt
    original = _day0_original_entity(vector, meta)
    capture = original["capture"]
    mode = "single_runs" if meta["endpoint_mode"] == "single_runs" else "standard_meta_stamped"
    product = (f"{meta['model_api_id']}::single_runs" if mode == "single_runs" else
        f"{meta['model_api_id']}::standard_api_meta_stamped::run={meta['provider_source_cycle_time_utc']}::modified={meta['provider_source_modified_at_utc']}")
    row = {"source_id": f"{vector.model}_{mode}", "product_id": product,
           "source_cycle_time": capture["source_cycle_time"], "source_available_at": capture["captured_at"]}
    body_id, _captured, _available = _persist_physical_response_artifact(conn, row, capture)
    event = capture.get("network_capture")
    receipt = conn.execute("SELECT artifact_id FROM raw_forecast_artifacts WHERE source_id=? AND product_id=? "
        "AND source_cycle_time=? AND data_version='openmeteo_single_model_http_capture_receipt_v1' "
        "AND json_extract(artifact_metadata_json,'$.physical_http_capture_receipt.body_artifact_id')=? "
        "AND captured_at=? ORDER BY artifact_id LIMIT 1",
        (row["source_id"], row["product_id"], row["source_cycle_time"], body_id, capture["captured_at"])).fetchone()
    if receipt is None and isinstance(event, Mapping):
        _persist_http_capture_receipt(conn, row, capture, body_id)
        receipt = conn.execute("SELECT artifact_id FROM raw_forecast_artifacts WHERE source_id=? AND product_id=? "
            "AND source_cycle_time=? AND data_version='openmeteo_single_model_http_capture_receipt_v1' "
            "AND json_extract(artifact_metadata_json,'$.physical_http_capture_receipt.body_artifact_id')=? "
            "AND captured_at=? ORDER BY artifact_id LIMIT 1",
            (row["source_id"], row["product_id"], row["source_cycle_time"], body_id, capture["captured_at"])).fetchone()
    if receipt is None:
        raise ValueError("DAY0_ORIGINAL_HTTP_RECEIPT_MISSING")
    written_at = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%f+00:00','now')").fetchone()[0]
    from src.data.replacement_current_value_serving import _current_model_surface_witness
    surface = _current_model_surface_witness({"model": vector.model,
        "latitude_requested": original["point"]["requested_latitude"],
        "longitude_requested": original["point"]["requested_longitude"],
        "physical_proof_cutoff": written_at}, original["point"], _day0_artifact(conn, body_id))
    if surface is None:
        raise ValueError("DAY0_ORIGINAL_MODEL_SURFACE_UNAVAILABLE")
    return {**meta, "__physical_response_reference_v1": {
        "body_artifact_id": body_id, "http_receipt_artifact_id": int(receipt[0]),
        "body_sha256": capture["sha256"],
        "model_surface_witness": surface, "vector_written_at": written_at}}


def _day0_replay_vector_original(conn: sqlite3.Connection, vector: Day0HourlyVector, *,
        decision_bound_utc: datetime, provider: str = "openmeteo", endpoint: str | None = None,
        request_hash: str | None = None) -> dict:
    meta = json.loads(str(vector.source_run_meta_json or ""))
    # The existing native ENS family is not a deterministic API vector. A retained
    # deterministic descriptor cannot be relabelled as an ENS member to bypass replay.
    if vector.immutable_run_member:
        # The legacy OM Ensemble API rows have no retained original entity
        # closure. Labels cannot confer authority or masquerade as deterministic
        # evidence. Native ENS remains on read_native_measurement_role's independent
        # GRIB/member/static proof path; its source cadence is the normal DRAIN.
        raise ValueError("DAY0_ORIGINAL_PRODUCER_FAMILY_ENSEMBLE_UNAVAILABLE")
    original = _day0_original_entity(vector, meta)
    if (provider != "openmeteo" or (endpoint is not None and endpoint != meta["endpoint"])
            or (request_hash is not None and (request_hash != meta["request_hash"]
                or meta["source_run_id"] != f"day0_hourly:{request_hash}"))):
        raise ValueError("DAY0_ORIGINAL_CANONICAL_IDENTITY_MISMATCH")
    reference = meta["__physical_response_reference_v1"]
    body = _day0_artifact(conn, reference["body_artifact_id"])
    receipt = _day0_artifact(conn, reference["http_receipt_artifact_id"])
    capture = original["capture"]
    if body["sha256"] != reference["body_sha256"] or body["sha256"] != capture["sha256"]:
        raise ValueError("DAY0_ORIGINAL_REFERENCE_HASH_MISMATCH")
    receipt["body_artifact"] = body
    from src.data.replacement_current_value_serving import _resolve_http_capture_receipt
    resolved = _resolve_http_capture_receipt({"physical_artifact": receipt, "artifact_id": body["artifact_id"]})
    if resolved is None:
        raise ValueError("DAY0_ORIGINAL_HTTP_RECEIPT_INVALID")
    physical = resolved["physical_artifact"]
    mode = "single_runs" if meta["endpoint_mode"] == "single_runs" else "standard_meta_stamped"
    if (physical["source_cycle_time"] != capture["source_cycle_time"]
            or physical["source_id"] != f"{vector.model}_{mode}"
            or physical["request_url"] != capture["request_url"]
            or json.loads(physical["request_params_json"]) != capture["request_params"]
            or physical["captured_at"] != capture["captured_at"]):
        raise ValueError("DAY0_ORIGINAL_HTTP_BODY_IDENTITY_MISMATCH")
    clocks = [original["run"], _day0_parse_aware_clock(capture["captured_at"], field_name="first_possession"),
              _day0_parse_aware_clock(physical["recorded_at"], field_name="http_written"),
              _day0_parse_aware_clock(reference["vector_written_at"], field_name="vector_written")]
    body_written = _day0_parse_aware_clock(body["recorded_at"], field_name="body_written")
    first_possession = _day0_parse_aware_clock(body["captured_at"], field_name="body_first_possession")
    if (not clocks[0] <= first_possession <= body_written <= decision_bound_utc
            or not first_possession <= clocks[1] <= clocks[2] <= clocks[3] <= decision_bound_utc):
        raise ValueError("DAY0_ORIGINAL_POSSESSION_WRITTEN_PIT_INVALID")
    request_capture = _day0_parse_aware_clock(vector.captured_at, field_name="request_capture")
    started = _day0_parse_aware_clock(meta["fetch_started_at"], field_name="fetch_started")
    finished = _day0_parse_aware_clock(meta["fetch_finished_at"], field_name="fetch_finished")
    available = _day0_parse_aware_clock(meta["provider_source_available_at_utc"], field_name="provider_available")
    modified = _day0_parse_aware_clock(meta["provider_source_modified_at_utc"], field_name="provider_modified")
    if (not request_capture <= started <= finished <= decision_bound_utc
            or not original["run"] <= available <= finished or not original["run"] <= modified <= finished):
        raise ValueError("DAY0_ORIGINAL_LOCAL_PROVIDER_CLOCK_INVALID")
    # Replay the frozen static witness, not an always-newest static reader.
    surface = reference["model_surface_witness"]
    point = original["point"]
    if vector.model == "ecmwf_ifs":
        from src.data.openmeteo_ecmwf_ifs9_bucket_transport import validate_source_cell_geometry_proof
        reason = validate_source_cell_geometry_proof(point["source_cell_geometry_proof"],
            latitude=float(point["selected_latitude"]), longitude=float(point["selected_longitude"]),
            target_elevation_m=float(point["target_dem_elevation_m"]),
            requested_latitude=float(point["requested_latitude"]), requested_longitude=float(point["requested_longitude"]),
            decision_at=decision_bound_utc)
        if surface.get("geometry") != {**point["source_cell_geometry_proof"],
                "native_surface": "SEA" if point["source_cell_geometry_proof"]["cell_is_sea"] else "LAND",
                "native_grid_elevation_m": point["source_cell_geometry_proof"]["raw_grid_elevation_m"]}:
            reason = "DAY0_ORIGINAL_STATIC_WITNESS_MISMATCH"
    else:
        from src.data.openmeteo_model_surface import validate_model_surface_witness
        reason = validate_model_surface_witness(surface, model=vector.model,
            selected_latitude=float(point["selected_latitude"]), selected_longitude=float(point["selected_longitude"]),
            body_captured_at=body["captured_at"], decision_at=decision_bound_utc)
    if reason is not None:
        raise ValueError(f"DAY0_ORIGINAL_MODEL_SURFACE_UNAVAILABLE:{reason}")
    return original


def parse_openmeteo_hourly_payload(
    payload: object,
    *,
    city: Any,
    models: list[str],
    captured_at: str,
    source_run_meta_json: str | None = None,
) -> list[Day0HourlyVector]:
    """Parse a (possibly multi-model) open-meteo hourly payload.

    Multi-model requests return either a list of per-model dicts or a single
    dict with suffixed keys (temperature_2m_<model>). Both shapes handled;
    target_date is stamped per-vector at read time (the vector spans 2 days).
    """
    tz_name = str(getattr(city, "timezone"))
    city_name = str(getattr(city, "name", "") or "")

    def _vector_from(hourly: dict, model: str, temp_key: str) -> Optional[Day0HourlyVector]:
        times = hourly.get("time")
        temps = hourly.get(temp_key)
        if not isinstance(times, (list, tuple)) or not isinstance(temps, (list, tuple)):
            return None
        pairs = [
            (str(t), float(v))
            for t, v in zip(times, temps)
            if v is not None and isinstance(v, (int, float))
        ]
        if not pairs:
            return None
        vector = Day0HourlyVector(
            model=model,
            city=city_name,
            target_date="",  # stamped per consumption window
            timezone_name=tz_name,
            captured_at=captured_at,
            times=tuple(t for t, _ in pairs),
            temps_c=tuple(v for _, v in pairs),
            source_run_meta_json=source_run_meta_json,
        )
        try:
            meta = json.loads(str(source_run_meta_json or ""))
            _day0_original_entity(vector, meta, city=city)
        except (KeyError, TypeError, ValueError, OSError, IndexError):
            return None
        return vector

    out: list[Day0HourlyVector] = []
    if isinstance(payload, list):
        for model, entry in zip(models, payload):
            if isinstance(entry, dict) and isinstance(entry.get("hourly"), dict):
                vector = _vector_from(entry["hourly"], model, "temperature_2m")
                if vector is not None:
                    out.append(vector)
        return out
    if isinstance(payload, dict) and isinstance(payload.get("hourly"), dict):
        hourly = payload["hourly"]
        for model in models:
            vector = _vector_from(hourly, model, f"temperature_2m_{model}")
            if vector is None and len(models) == 1:
                # single-model responses may omit the model suffix
                vector = _vector_from(hourly, model, "temperature_2m")
            if vector is not None:
                out.append(vector)
    return out


def persist_day0_hourly_vectors(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    conn: Optional[sqlite3.Connection] = None,
    request_hash: str,
    endpoint: str = OPENMETEO_FORECAST_URL,
    retention_days: float = DAY0_VECTOR_RETENTION_DAYS,
    now: Optional[datetime] = None,
    lock_blocking: bool = True,
) -> int:
    """Persist vectors (idempotent on (model,city,date,captured_at)) + prune.

    conn=None -> zeus-forecasts.db under db_writer_lock(LIVE) per INV-37; the
    connection is OPENED INSIDE the flock (lock-order hygiene: connection-open
    contention stays under the same writer lock — PR review PR#404 finding).

    request_hash is REQUIRED non-empty (PR#404 P1: rows feeding the
    remaining-day q must carry a replayable provenance identity; the table
    CHECK enforces the same on fresh DBs).

    ``now`` pins the retention-prune reference clock (the cutoff is
    ``now - retention_days``). Defaults to live wall-clock ``datetime.now(UTC)``
    so production behaviour is unchanged; tests inject it so a fixture with
    fixed captured_at timestamps is not pruned non-deterministically as real
    time advances past the retention window.
    """
    if not vectors:
        return 0
    if not str(request_hash or "").strip():
        raise ValueError(
            "persist_day0_hourly_vectors requires a non-empty request_hash "
            "(replayable provenance identity; see build_request_hash)"
        )
    own_conn = conn is None
    if own_conn:
        from src.state.db import ZEUS_FORECASTS_DB_PATH, get_forecasts_connection
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        lock_ctx = db_writer_lock(
            ZEUS_FORECASTS_DB_PATH,
            WriteClass.LIVE,
            blocking=lock_blocking,
        )
    else:
        from contextlib import nullcontext

        lock_ctx = nullcontext()
    written = 0
    try:
        with lock_ctx:
            if own_conn:
                conn = get_forecasts_connection(write_class=WriteClass.LIVE)
            _ensure_schema(conn)
            conn.execute("SAVEPOINT day0_vector_originals")
            for vector in vectors:
                if not _vector_covers_target_from_capture(
                    vector, target_date=target_date
                ):
                    logger.warning(
                        "DAY0_HOURLY_VECTOR_TARGET_COVERAGE_REJECTED "
                        "city=%s model=%s target_date=%s captured_at=%s",
                        vector.city,
                        vector.model,
                        target_date,
                        vector.captured_at,
                    )
                    continue
                row_id = _vector_id(vector.model, vector.city, target_date, vector.captured_at)
                if conn.execute("SELECT 1 FROM day0_hourly_vectors WHERE vector_id=?", (row_id,)).fetchone():
                    continue  # immutable prior rows, including unproved legacy captures, are not backfilled
                row_endpoint = endpoint
                stored_meta = vector.source_run_meta_json
                try:
                    source_meta = json.loads(str(vector.source_run_meta_json or ""))
                    if isinstance(source_meta, Mapping) and str(
                        source_meta.get("endpoint") or ""
                    ).strip():
                        row_endpoint = str(source_meta["endpoint"]).strip()
                    if not vector.immutable_run_member or "__physical_response_capture_v1" in source_meta:
                        source_meta = _day0_register_vector_original(conn, vector, dict(source_meta))
                        stored_meta = json.dumps(source_meta, sort_keys=True, separators=(",", ":"))
                except (KeyError, TypeError, ValueError, OSError, IndexError) as exc:
                    logger.warning("DAY0_ORIGINAL_VECTOR_UNAVAILABLE city=%s model=%s reason=%s", vector.city, vector.model, exc)
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO day0_hourly_vectors (
                        vector_id, model, city, target_date, timezone_name,
                        captured_at, provider, endpoint, request_hash,
                        times_json, temps_c_json, source_run_meta_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 'openmeteo', ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id, vector.model, vector.city, target_date,
                        vector.timezone_name, vector.captured_at, row_endpoint,
                        request_hash, json.dumps(list(vector.times)),
                        json.dumps(list(vector.temps_c)),
                        stored_meta,
                    ),
                )
                written += int(cur.rowcount or 0)
            prune_reference = (now or datetime.now(UTC)).astimezone(UTC)
            cutoff = prune_reference.timestamp() - retention_days * 86400.0
            cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
            conn.execute(
                "DELETE FROM day0_hourly_vectors WHERE captured_at < ?",
                (cutoff_iso,),
            )
            conn.execute("RELEASE SAVEPOINT day0_vector_originals")
            conn.commit()
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        # conn can be None when the connection-open itself failed inside the
        # flock — guard so the original exception is never masked (PR review
        # PR#404 finding).
        if own_conn and conn is not None:
            conn.close()
    return written


def _vector_covers_target_from_capture(
    vector: Day0HourlyVector,
    *,
    target_date: str,
) -> bool:
    """Require exact target-day support still usable at capture time."""

    try:
        target = date.fromisoformat(str(target_date)[:10])
        captured = datetime.fromisoformat(
            str(vector.captured_at).replace("Z", "+00:00")
        )
        tz = ZoneInfo(vector.timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return False
    if captured.tzinfo is None:
        return False
    captured = captured.astimezone(UTC)
    captured_day = captured.astimezone(tz).date()
    if captured_day > target:
        return False
    boundary = (
        captured
        if captured_day == target
        else datetime.combine(target, datetime_time.min, tzinfo=tz)
    )
    return day0_hourly_vectors_cover_remaining_window(
        [vector],
        target_date=target_date,
        window_start=boundary,
    )


def _day0_source_clock_ensemble_metadata_is_current(
    vector: Day0HourlyVector,
) -> bool:
    """Require ENS rows to carry the ensemble metadata-domain provenance."""

    if not str(vector.model or "").strip().startswith(
        DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX
    ):
        return True
    try:
        source_meta = json.loads(str(vector.source_run_meta_json or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(source_meta, Mapping):
        return False
    try:
        request_params = json.loads(
            str(source_meta.get("request_params_json") or "")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(request_params, Mapping)
        and str(request_params.get("metadata_model") or "").strip()
        == DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL
    )


def day0_ensemble_run_refusal(
    run: datetime, *, decision_time: datetime
) -> str | None:
    """Run-identity currency of one immutable ENS run; None when current.

    A capture of run R carries the same members however long ago it was
    fetched, so capture age measures nothing. R is stale only when (a) the
    provider's latest-available run (the monotone ensemble HWM pin) names a
    newer run that was publicly usable and recorded by ``decision_time``, or
    (b) R's own cycle is outside the source-cycle age law
    (``replacement_source_cycle_max_age_hours``: decision minus CYCLE time,
    30h default) -- the backstop when a dead metadata probe stops the pin
    from advancing.
    """
    from src.data.replacement_forecast_cycle_policy import cycle_age_outside_bound

    cutoff = decision_time.astimezone(UTC)
    if cycle_age_outside_bound(cutoff, run):
        return "DAY0_CONDITIONAL_HIGH_ENSEMBLE_RUN_EXPIRED"
    try:
        pin = json.loads(_day0_provider_run_hwm_pin_path().read_text(encoding="utf-8"))
        entry = pin["entries"][DAY0_SOURCE_CLOCK_ENSEMBLE_METADATA_MODEL]
        pinned_run = _day0_parse_aware_clock(
            entry["run_initialisation_time"], field_name="ensemble_pin_run"
        )
        pinned_available = _day0_parse_aware_clock(
            entry["run_availability_time"], field_name="ensemble_pin_available"
        )
        pinned_recorded = _day0_parse_aware_clock(
            entry["recorded_at"], field_name="ensemble_pin_recorded"
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None  # No causal pin: the cycle-age law alone governs.
    from src.strategy.live_inference.source_clock_vnext import (
        SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES,
    )

    if (
        pinned_run <= pinned_available
        and pinned_recorded <= cutoff
        and pinned_available + timedelta(
            minutes=SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES
        ) <= cutoff
        and run.astimezone(UTC) < pinned_run
    ):
        return "DAY0_CONDITIONAL_HIGH_ENSEMBLE_SUPERSEDED"
    return None


def _rolling_capture_age_at_window_close(
    captured: datetime, moment: datetime, *, vector: "Day0HourlyVector", target_date: str,
) -> float:
    """Hours from capture to the close of the window the capture forecasts.

    The rolling bound keeps a forecast of still-future hours the latest one
    available. Once the target local day has ended, no newer forecast of its
    hours can be issued: the last capture before day end is final evidence, so
    its age is judged at day end, min(decision, day_end) - captured_at. A
    capture already stale at day end stays stale; an open day is unchanged.
    """
    close = moment
    try:
        day_end = datetime.combine(
            date.fromisoformat(str(target_date)) + timedelta(days=1), datetime_time.min,
            tzinfo=ZoneInfo(str(vector.timezone_name)),
        ).astimezone(UTC)
    except (TypeError, ValueError, KeyError, ZoneInfoNotFoundError):
        day_end = None
    if day_end is not None and day_end < close:
        close = day_end
    return (close - captured).total_seconds() / 3600.0


class Day0HourlyBundleRefusal(str, Enum):
    """Why a strict Day0 hourly bundle read returned no bundle."""

    ABSENT = "absent"  # an expected model has no row at all
    STALE = "stale"  # rows exist but none is current (capture age / ENS run)
    WINDOW_INCOMPLETE = "window_incomplete"  # current rows miss the remaining window


class Day0HourlyBundle(list):
    """A strict bundle read: the selected vectors, or empty with a typed refusal.

    A ``list`` so every caller that tests truthiness or compares to ``[]`` is
    unchanged; ``refusal`` is None exactly when the read selected a bundle.
    """

    refusal: Day0HourlyBundleRefusal | None = None

    @classmethod
    def refused(cls, refusal: Day0HourlyBundleRefusal) -> "Day0HourlyBundle":
        out = cls()
        out.refusal = refusal
        return out


def select_ready_day0_hourly_vectors(
    vectors: Iterable[Day0HourlyVector],
    *,
    target_date: str,
    max_age_hours: float = DAY0_ROLLING_CAPTURE_MAX_AGE_HOURS,
    now: Optional[datetime] = None,
    expected_models: Optional[Iterable[str]] = None,
    require_expected: bool = False,
    max_bundle_skew_minutes: Optional[float] = None,
    remaining_window_start: datetime | None = None,
    require_complete_remaining_window: bool = False,
) -> Day0HourlyBundle:
    """Pure strict-bundle predicate shared by producer and live readers.

    It is intentionally the one place that decides freshness, expected-model
    completeness, capture skew, and remaining-window coverage. Freshness splits
    by row kind: a rolling row must be captured within ``max_age_hours``; an
    immutable ENS member row must name a current run
    (``day0_ensemble_run_refusal``), whatever its capture age.  The producer
    probes persisted readiness through ``read_freshest_day0_hourly_vectors``;
    health and money-path readers do the same, so a city cannot be prioritized
    by a weaker interpretation than the authority consumer accepts.

    An empty result carries ``refusal``: ``ABSENT`` when an expected model has
    no row, ``WINDOW_INCOMPLETE`` when a current row was rejected only for
    remaining-window coverage, else ``STALE`` (no current row, or capture
    skew).  The typing names the debt; it never admits a row.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    expected: list[str] = []
    for model in expected_models or ():
        normalized = str(model or "").strip()
        if normalized and normalized not in expected:
            expected.append(normalized)
    expected_set = set(expected)

    run_refusals: dict[datetime, str | None] = {}
    parsed: list[tuple[datetime, Day0HourlyVector]] = []
    seen_models: set[str] = set()
    window_rejected = False
    for vector in vectors:
        model = str(vector.model or "").strip()
        if not model or (expected_set and model not in expected_set):
            continue
        seen_models.add(model)
        if not _day0_source_clock_ensemble_metadata_is_current(vector):
            continue
        try:
            captured = datetime.fromisoformat(
                str(vector.captured_at).replace("Z", "+00:00")
            )
            if captured.tzinfo is None:
                continue
            captured = captured.astimezone(UTC)
            age_hours = (moment - captured).total_seconds() / 3600.0
        except (TypeError, ValueError):
            continue
        if age_hours < 0.0:
            continue
        if vector.immutable_run_member:
            try:
                run = _day0_parse_aware_clock(
                    json.loads(str(vector.source_run_meta_json or ""))[
                        "provider_source_cycle_time_utc"
                    ],
                    field_name="provider_source_cycle_time_utc",
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if run not in run_refusals:
                run_refusals[run] = day0_ensemble_run_refusal(run, decision_time=moment)
            if run_refusals[run] is not None:
                continue
        elif _rolling_capture_age_at_window_close(
            captured, moment, vector=vector, target_date=target_date,
        ) > float(max_age_hours):
            continue
        if require_complete_remaining_window:
            try:
                source_meta = json.loads(str(vector.source_run_meta_json or ""))
                if not isinstance(source_meta, Mapping):
                    continue
                fetch_started = datetime.fromisoformat(
                    str(source_meta["fetch_started_at"]).replace("Z", "+00:00")
                )
                fetch_finished = datetime.fromisoformat(
                    str(source_meta["fetch_finished_at"]).replace("Z", "+00:00")
                )
                if (
                    fetch_started.tzinfo is None
                    or fetch_started.utcoffset() is None
                    or fetch_finished.tzinfo is None
                    or fetch_finished.utcoffset() is None
                ):
                    continue
                fetch_started = fetch_started.astimezone(UTC)
                fetch_finished = fetch_finished.astimezone(UTC)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if not (
                captured <= fetch_started <= fetch_finished <= moment
            ):
                continue
        if (
            require_complete_remaining_window
            and (
                remaining_window_start is None
                or not day0_hourly_vectors_cover_remaining_window(
                    [vector],
                    target_date=target_date,
                    window_start=remaining_window_start,
                )
            )
        ):
            window_rejected = True
            continue
        parsed.append((captured, vector))

    freshest: dict[str, Day0HourlyVector] = {}
    for _captured, vector in sorted(parsed, key=lambda item: item[0], reverse=True):
        freshest.setdefault(str(vector.model), vector)
    if require_expected and expected and any(model not in freshest for model in expected):
        if any(model not in seen_models for model in expected):
            return Day0HourlyBundle.refused(Day0HourlyBundleRefusal.ABSENT)
        return Day0HourlyBundle.refused(
            Day0HourlyBundleRefusal.WINDOW_INCOMPLETE
            if window_rejected
            else Day0HourlyBundleRefusal.STALE
        )
    if (
        require_expected
        and expected
        and max_bundle_skew_minutes is not None
        and all(model in freshest for model in expected)
    ):
        captured_times: list[datetime] = []
        try:
            for model in expected:
                captured = datetime.fromisoformat(
                    str(freshest[model].captured_at).replace("Z", "+00:00")
                )
                if captured.tzinfo is None:
                    return Day0HourlyBundle.refused(Day0HourlyBundleRefusal.STALE)
                captured_times.append(captured.astimezone(UTC))
        except (TypeError, ValueError):
            return Day0HourlyBundle.refused(Day0HourlyBundleRefusal.STALE)
        if (
            max(captured_times) - min(captured_times)
        ).total_seconds() / 60.0 > float(max_bundle_skew_minutes):
            return Day0HourlyBundle.refused(Day0HourlyBundleRefusal.STALE)
    selected = (
        [freshest[model] for model in expected if model in freshest]
        if expected
        else list(freshest.values())
    )
    if require_complete_remaining_window and (
        remaining_window_start is None
        or not day0_hourly_vectors_cover_remaining_window(
            selected,
            target_date=target_date,
            window_start=remaining_window_start,
        )
    ):
        return Day0HourlyBundle.refused(
            Day0HourlyBundleRefusal.WINDOW_INCOMPLETE
        )
    if not selected:
        return Day0HourlyBundle.refused(
            Day0HourlyBundleRefusal.ABSENT
            if not seen_models
            else Day0HourlyBundleRefusal.WINDOW_INCOMPLETE
            if window_rejected
            else Day0HourlyBundleRefusal.STALE
        )
    return Day0HourlyBundle(selected)


def newest_day0_rolling_capture_at(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    decision_time: datetime,
) -> datetime | None:
    """Newest rolling (non-ENS-member) capture of one city/date at the cut.

    One seek on ``idx_day0_hourly_vectors_city_date``.  Every expected
    deterministic model is a rolling row, so when this capture is older than a
    strict reader's ``max_age_hours`` that read is provably empty.  The fetcher
    writes ``captured_at`` as UTC ISO-8601, so text order is time order (the
    index order).  ``ValueError`` names an unparseable newest capture.
    """
    row = conn.execute(
        """
        SELECT captured_at FROM day0_hourly_vectors
         WHERE city = ? AND target_date = ? AND captured_at <= ?
           AND substr(model, 1, ?) <> ?
         ORDER BY captured_at DESC
         LIMIT 1
        """,
        (
            str(city),
            str(target_date),
            decision_time.astimezone(UTC).isoformat(),
            len(DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX),
            DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX,
        ),
    ).fetchone()
    if row is None:
        return None
    return _day0_parse_aware_clock(row[0], field_name="captured_at").astimezone(UTC)


def read_freshest_day0_hourly_vectors(
    *,
    city: str,
    target_date: str,
    max_age_hours: float = DAY0_ROLLING_CAPTURE_MAX_AGE_HOURS,
    now: Optional[datetime] = None,
    conn: Optional[sqlite3.Connection] = None,
    expected_models: Optional[Iterable[str]] = None,
    require_expected: bool = False,
    max_bundle_skew_minutes: Optional[float] = None,
    remaining_window_start: datetime | None = None,
    require_complete_remaining_window: bool = False,
    raise_on_db_error: bool = False,
) -> list[Day0HourlyVector]:
    """Freshest persisted vector per model for (city, target_date).

    The result is ``select_ready_day0_hourly_vectors``'s typed
    ``Day0HourlyBundle``: when empty it names why (absent / stale /
    window-incomplete).  The SQL prefilter drops rows older than the capture
    floor, so a city whose only rows are that old reads ``ABSENT``.

    Rolling vectors captured more than max_age_hours ago are EXCLUDED (a stale
    high-res "now" must not masquerade as the current remaining-day
    distribution). Immutable ENS member rows are instead admitted by run
    currency; the SQL prefilter bounds them by the source-cycle age law.

    ``expected_models`` lets live consumers define the complete bundle they are
    willing to treat as same-day authority. With ``require_expected=True``, any
    missing expected model returns [] so a partial single-model regional vector
    cannot sponsor a live decision. ``max_bundle_skew_minutes`` additionally
    prevents mixing a fresh model row with a materially older row from another
    model as one live authority bundle. Live probability consumers set
    ``require_complete_remaining_window`` and provide their causal boundary;
    every model must then contain each hourly grid point from that boundary to
    local-day end. A partial future path is not probability authority.
    Producer readiness probes set ``raise_on_db_error`` so an unreadable store
    cannot be misclassified as a proved, normally missing bundle.
    """
    own_conn = conn is None
    if own_conn:
        from src.state.db import get_forecasts_connection_read_only

        conn = get_forecasts_connection_read_only()
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    # SQL candidate floor only; select_ready_day0_hourly_vectors ages each row
    # exactly at the close of its window (min(decision, local-day end)).
    capture_close = moment
    try:
        from src.config import runtime_cities_by_name

        city_obj = runtime_cities_by_name().get(str(city))
        if city_obj is not None:
            day_end = datetime.combine(
                date.fromisoformat(str(target_date)) + timedelta(days=1), datetime_time.min,
                tzinfo=ZoneInfo(str(city_obj.timezone)),
            ).astimezone(UTC)
            capture_close = min(moment, day_end)
    except (TypeError, ValueError, KeyError, ZoneInfoNotFoundError):
        capture_close = moment
    oldest_capture = capture_close - timedelta(hours=float(max_age_hours))
    from src.data.replacement_forecast_cycle_policy import (
        replacement_source_cycle_max_age_hours,
    )

    oldest_run = moment - timedelta(hours=replacement_source_cycle_max_age_hours())
    try:
        try:
            rows = conn.execute(
                """
                SELECT model, city, target_date, timezone_name, captured_at,
                       times_json, temps_c_json, source_run_meta_json, provider, endpoint, request_hash
                FROM day0_hourly_vectors
                WHERE city = ? AND target_date = ?
                  AND julianday(captured_at) <= julianday(?)
                  AND (
                      julianday(captured_at) >= julianday(?)
                      OR (
                          substr(model, 1, ?) = ?
                          AND julianday(json_extract(
                              source_run_meta_json,
                              '$.provider_source_cycle_time_utc'
                          )) >= julianday(?)
                      )
                  )
                ORDER BY captured_at DESC
                """,
                (
                    str(city),
                    str(target_date),
                    moment.isoformat(),
                    oldest_capture.isoformat(),
                    len(DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX),
                    DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_PREFIX,
                    oldest_run.isoformat(),
                ),
            ).fetchall()
        except sqlite3.Error:
            if raise_on_db_error:
                raise
            return []
        candidates: list[Day0HourlyVector] = []
        for row in rows:
            model = str(row[0])
            try:
                times = tuple(str(t) for t in json.loads(row[5]))
                temps = tuple(float(v) for v in json.loads(row[6]))
                if not times or len(times) != len(temps):
                    continue
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            candidate = Day0HourlyVector(
                model=model, city=str(row[1]), target_date=str(row[2]),
                timezone_name=str(row[3]), captured_at=str(row[4]),
                times=times, temps_c=temps,
                source_run_meta_json=(
                    None if row[7] in (None, "") else str(row[7])
                ),
            )
            try:
                _day0_replay_vector_original(conn, candidate, decision_bound_utc=moment,
                    provider=str(row[8]), endpoint=str(row[9]), request_hash=str(row[10]))
            except (KeyError, TypeError, ValueError, OSError, IndexError):
                continue
            candidates.append(candidate)
        return select_ready_day0_hourly_vectors(
            candidates,
            target_date=target_date,
            max_age_hours=max_age_hours,
            now=moment,
            expected_models=expected_models,
            require_expected=require_expected,
            max_bundle_skew_minutes=max_bundle_skew_minutes,
            remaining_window_start=remaining_window_start,
            require_complete_remaining_window=require_complete_remaining_window,
        )
    finally:
        if own_conn:
            conn.close()


@lru_cache(maxsize=256)
def _target_day_hour_grid_utc(
    *, target: date, tz: ZoneInfo, utc_aligned: bool = False,
) -> tuple[datetime, ...]:
    """Exact native hourly instants inside the local settlement day."""

    start = datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(
        target + timedelta(days=1), datetime_time.min, tzinfo=tz
    ).astimezone(UTC)
    out: list[datetime] = []
    cursor = start
    if utc_aligned:
        cursor = start.replace(minute=0, second=0, microsecond=0)
        if cursor < start:
            cursor += timedelta(hours=1)
    while cursor < end:
        out.append(cursor)
        cursor += timedelta(hours=1)
    return tuple(out)


def _vector_target_day_hour_grid_utc(
    vector: Day0HourlyVector, *, target: date, tz: ZoneInfo,
) -> tuple[datetime, ...]:
    """Recognize local-hour or UTC-hour samples without shifting their times."""
    for raw_time in vector.times:
        try:
            parsed = datetime.fromisoformat(str(raw_time))
        except (TypeError, ValueError):
            return ()
        local = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
        if local.date() != target:
            continue
        if local.second or local.microsecond:
            return ()
        if local.minute == 0:
            return _target_day_hour_grid_utc(target=target, tz=tz)
        if local.astimezone(UTC).minute == 0:
            return _target_day_hour_grid_utc(target=target, tz=tz, utc_aligned=True)
        return ()
    return ()


def day0_hourly_vector_target_values_utc(
    vector: Day0HourlyVector,
    *,
    target: date,
    tz: ZoneInfo,
) -> tuple[tuple[datetime, float], ...] | None:
    """Map one provider-local target-day vector to exact UTC instants."""

    grid = _vector_target_day_hour_grid_utc(vector, target=target, tz=tz)
    if not grid or len(vector.times) != len(vector.temps_c):
        return None
    by_label: dict[str, list[datetime]] = {}
    for instant in grid:
        label = instant.astimezone(tz).strftime("%Y-%m-%dT%H:%M")
        by_label.setdefault(label, []).append(instant)
    label_uses: Counter[str] = Counter()
    seen_instants: set[datetime] = set()
    values: list[tuple[datetime, float]] = []
    for raw_time, temp in zip(vector.times, vector.temps_c):
        try:
            parsed = datetime.fromisoformat(str(raw_time))
            value = float(temp)
        except (TypeError, ValueError):
            return None
        local = (
            parsed.replace(tzinfo=tz)
            if parsed.tzinfo is None
            else parsed.astimezone(tz)
        )
        if local.date() != target:
            continue
        if (
            not math.isfinite(value)
            or local.second != 0
            or local.microsecond != 0
        ):
            return None
        if parsed.tzinfo is None:
            label = local.strftime("%Y-%m-%dT%H:%M")
            choices = by_label.get(label, [])
            use_index = label_uses[label]
            if use_index >= len(choices):
                return None
            instant = choices[use_index]
            label_uses[label] += 1
        else:
            instant = parsed.astimezone(UTC)
            if instant not in grid:
                return None
        if instant in seen_instants:
            return None
        seen_instants.add(instant)
        values.append((instant, value))
    return tuple(values)


def align_day0_hourly_vectors_on_common_causal_grid(
    vectors: Iterable[Day0HourlyVector],
    *,
    target_date: str,
    window_start: datetime,
) -> tuple[tuple[datetime, ...], tuple[tuple[float, ...], ...]] | None:
    """Align a complete provider bundle on one exact UTC causal grid.

    Provider runs can expose different *elapsed* prefixes (for example
    ``24/21/24`` target-day rows) while still sharing the complete stochastic
    suffix that begins at the current observation boundary.  The live
    consumers need a rectangular matrix, so this helper keeps only the exact
    UTC instants common to every provider: the latest hourly anchor at or
    before ``window_start`` and every target-day grid point after it.

    This is an alignment operation, not a resampler: no timestamp or value is
    fabricated.  The per-provider causal coverage gate runs first, then the
    common grid is checked again.  A missing causal hour, a missing <=1-hour
    anchor, timezone mismatch, duplicate/non-finite timestamp, or invalid DST
    shape returns ``None``.  In particular, a future target-day bundle whose
    provider omits midnight hours remains unavailable; a current-day prefix
    must never relax that contract.
    """
    bundle = tuple(vectors)
    if not bundle or window_start.tzinfo is None:
        return None
    try:
        target = date.fromisoformat(str(target_date)[:10])
    except (TypeError, ValueError):
        return None
    if not day0_hourly_vectors_cover_remaining_window(
        list(bundle), target_date=target_date, window_start=window_start
    ):
        return None

    timezone_name = str(bundle[0].timezone_name or "").strip()
    if not timezone_name:
        return None
    try:
        timezone_obj = ZoneInfo(timezone_name)
    except (TypeError, ZoneInfoNotFoundError):
        return None
    for vector in bundle[1:]:
        if str(vector.timezone_name or "").strip() != timezone_name:
            return None

    target_grid = _vector_target_day_hour_grid_utc(
        bundle[0], target=target, tz=timezone_obj
    )
    if not target_grid:
        return None
    boundary_utc = window_start.astimezone(UTC)
    anchor_candidates = [instant for instant in target_grid if instant <= boundary_utc]
    if not anchor_candidates:
        return None
    causal_anchor = anchor_candidates[-1]
    if not timedelta(0) <= boundary_utc - causal_anchor <= timedelta(hours=1):
        return None
    causal_grid = tuple(instant for instant in target_grid if instant >= causal_anchor)
    if not causal_grid:
        return None

    aligned_rows: list[tuple[float, ...]] = []
    for vector in bundle:
        values = day0_hourly_vector_target_values_utc(
            vector, target=target, tz=timezone_obj
        )
        if values is None:
            return None
        by_instant: dict[datetime, float] = {}
        for instant, value in values:
            if instant in by_instant or not math.isfinite(float(value)):
                return None
            by_instant[instant] = float(value)
        if any(instant not in by_instant for instant in causal_grid):
            return None
        aligned_rows.append(tuple(by_instant[instant] for instant in causal_grid))
    if not aligned_rows or any(len(row) != len(causal_grid) for row in aligned_rows):
        return None
    return causal_grid, tuple(aligned_rows)


def day0_hourly_vectors_cover_remaining_window(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    window_start: datetime,
) -> bool:
    """Prove every model covers the causal boundary through local-day end.

    Native hourly samples may land on fractional local hours. Expected instants
    retain their phase; duplicate local labels are assigned in chronological order,
    so 23/25-hour DST days remain exact even when timestamps omit offsets. When
    the causal boundary is inside the terminal sub-hour, the final elapsed grid
    point is required as the interval anchor instead of pretending that an empty
    future grid is complete.
    """

    if not vectors or window_start.tzinfo is None:
        return False
    try:
        target = date.fromisoformat(str(target_date)[:10])
    except ValueError:
        return False
    boundary_utc = window_start.astimezone(UTC)
    common_grid: tuple[datetime, ...] | None = None
    timezone_name = vectors[0].timezone_name
    for vector in vectors:
        if vector.timezone_name != timezone_name:
            return False
        try:
            tz = ZoneInfo(vector.timezone_name)
        except Exception:
            return False
        boundary_local = boundary_utc.astimezone(tz)
        if boundary_local.date() != target:
            return False
        grid = _vector_target_day_hour_grid_utc(vector, target=target, tz=tz)
        if not grid or boundary_utc < grid[0]:
            return False
        if common_grid is not None and grid != common_grid:
            return False
        common_grid = grid
        values = day0_hourly_vector_target_values_utc(
            vector,
            target=target,
            tz=tz,
        )
        if not grid or values is None:
            return False
        counts = Counter(instant for instant, _value in values)
        if grid != _target_day_hour_grid_utc(target=target, tz=tz):
            anchor = max(instant for instant in grid if instant <= boundary_utc)
            if counts[anchor] != 1:
                return False
        required = tuple(instant for instant in grid if instant >= boundary_utc)
        if required:
            if any(counts[instant] != 1 for instant in required):
                return False
            continue
        final_grid = grid[-1]
        if (
            counts[final_grid] != 1
            or not timedelta(0) <= boundary_utc - final_grid <= timedelta(hours=1)
        ):
            return False
    return True


def remaining_day_extremes_c(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    now: datetime,
    metric: str,
    window_start: datetime | None = None,
) -> list[float]:
    """Per-model extreme over the target-day interval not yet observed.

    ``now`` is the decision/freshness cut. ``window_start`` is the latest causal
    observation time and defaults to ``now``. Grid points at/after that boundary
    are ordinary support. During the terminal sub-hour, the final elapsed hourly
    point remains the interval anchor for at most one hour. This preserves an
    unobserved target-day tail after local midnight without reopening hours that
    canonical observations already cover.
    """
    if metric not in {"high", "low"}:
        raise ValueError(f"unsupported metric: {metric}")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start = window_start or now
    if start.tzinfo is None:
        raise ValueError("window_start must be timezone-aware")
    if start.astimezone(UTC) > now.astimezone(UTC):
        raise ValueError("window_start cannot be after now")
    target = date.fromisoformat(str(target_date)[:10])
    if not day0_hourly_vectors_cover_remaining_window(
        vectors,
        target_date=target_date,
        window_start=start,
    ):
        return []
    out: list[float] = []
    start_utc = start.astimezone(UTC)
    for vector in vectors:
        try:
            tz = ZoneInfo(vector.timezone_name)
        except Exception:
            continue
        start_local = start.astimezone(tz)
        if start_local.date() != target:
            continue
        target_values = day0_hourly_vector_target_values_utc(
            vector,
            target=target,
            tz=tz,
        )
        if target_values is None:
            return []
        values: list[float] = []
        elapsed_target_points: list[tuple[datetime, float]] = []
        for instant, temp in target_values:
            if instant < start_utc:
                elapsed_target_points.append((instant, float(temp)))
                continue
            values.append(float(temp))
        if (
            not values
            and elapsed_target_points
        ):
            local_day_end = datetime.combine(
                target + timedelta(days=1),
                datetime.min.time(),
                tzinfo=tz,
            )
            anchor_time, anchor_temp = max(
                elapsed_target_points,
                key=lambda item: item[0],
            )
            anchor_age = start_utc - anchor_time
            time_to_day_end = local_day_end.astimezone(UTC) - start_utc
            if (
                timedelta(0) < time_to_day_end <= timedelta(hours=1)
                and timedelta(0) <= anchor_age <= timedelta(hours=1)
            ):
                values.append(anchor_temp)
        if not values:
            continue
        out.append(max(values) if metric == "high" else min(values))
    return out


HKO_CURRENT_TEMPERATURE_URL = (
    "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/"
    "latest_1min_temperature.csv"
)
HKO_CURRENT_TEMPERATURE_CHANNEL = "hko_current_1min_mean"
HKO_CURRENT_TEMPERATURE_MAX_AGE = timedelta(minutes=25)


def hko_current_temperature_evidence(
    body: bytes, *, last_modified: str, fetched_at: datetime, written_at: datetime,
    response_headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Retain the provisional one-minute mean, not an extreme or hard fact.

    HTTP Last-Modified dates this representation, not a formal bulletin issue.
    The CSV row supplies the physical observation clock. Reported decimal
    precision does not assert a rounding interval or change instrument noise.
    """
    if not isinstance(body, bytes) or not body or len(body) > 65_536:
        raise ValueError("HKO current body unavailable or oversized")
    updated = parsedate_to_datetime(last_modified)
    if any(clock.tzinfo is None for clock in (updated, fetched_at, written_at)):
        raise ValueError("HKO current clocks must be aware")
    updated, fetched, written = (clock.astimezone(UTC) for clock in (updated, fetched_at, written_at))
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if reader.fieldnames != ["Date time", "Automatic Weather Station", "Air Temperature(degree Celsius)"]:
        raise ValueError("HKO current Celsius quantity header unavailable")
    rows = [row for row in reader if row.get("Automatic Weather Station") == "HK Observatory"]
    if len(rows) != 1:
        raise ValueError("HKO current exact HQ row unavailable")
    row = rows[0]
    observed = datetime.strptime(row["Date time"], "%Y%m%d%H%M").replace(
        tzinfo=ZoneInfo("Asia/Hong_Kong")
    ).astimezone(UTC)
    value = float(row["Air Temperature(degree Celsius)"])
    if (
        not math.isfinite(value)
        or not observed <= updated <= fetched <= written
        or fetched-observed > HKO_CURRENT_TEMPERATURE_MAX_AGE
        or fetched-updated > HKO_CURRENT_TEMPERATURE_MAX_AGE
    ):
        raise ValueError("HKO current value/causal clock unavailable")
    headers = {str(key).lower():str(value) for key,value in (response_headers or {}).items()
        if str(key).lower() in {"last-modified","date","etag","content-type"}}
    if headers.get("last-modified",last_modified) != last_modified:
        raise ValueError("HKO current representation header mismatch")
    headers["last-modified"] = last_modified
    if "date" in headers:
        response_date = parsedate_to_datetime(headers["date"])
        if response_date.tzinfo is None or not updated <= response_date <= fetched:
            raise ValueError("HKO current HTTP response clock unavailable")
    return {
        "source": HKO_CURRENT_TEMPERATURE_CHANNEL,
        "source_endpoint": HKO_CURRENT_TEMPERATURE_URL,
        "station_id": "HKO_HQ", "station_alias": "HK Observatory",
        "quantity": "one_minute_mean_air_temperature",
        "authority": "CURRENT_ONLY", "averaging_window_seconds": 60,
        "reported_precision_c": 0.1, "unit": "C", "value_native": value,
        "observed_at_utc": observed.isoformat(),
        "publication_clock_role": "HTTP_REPRESENTATION_LAST_MODIFIED",
        "representation_updated_at_utc": updated.isoformat(),
        "last_modified": last_modified, "available_at_utc": fetched.isoformat(),
        "http_response_headers": headers,
        "written_at_utc": written.isoformat(),
        "body_base64": base64.b64encode(body).decode("ascii"),
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }


def replay_hko_current_temperature_print(
    raw_report: str, *, representation_updated_at: datetime,
    available_at: datetime, decision_time: datetime,
) -> Day0CurrentTemperatureState:
    """Pure replay of a possessed CSV print; no HTTP, no authority upgrade."""
    if representation_updated_at.tzinfo is None:
        raise ValueError("HKO representation clock must be aware")
    proof = json.loads(raw_report)
    body = base64.b64decode(proof["body_base64"], validate=True)
    written = datetime.fromisoformat(proof["written_at_utc"])
    reproduced = hko_current_temperature_evidence(body,last_modified=proof["last_modified"],
        fetched_at=available_at,written_at=written,response_headers=proof.get("http_response_headers"))
    if (
        proof != reproduced or decision_time.tzinfo is None
        or representation_updated_at.astimezone(UTC).isoformat() != proof["representation_updated_at_utc"]
        or written > decision_time or decision_time-available_at > HKO_CURRENT_TEMPERATURE_MAX_AGE
        or decision_time-datetime.fromisoformat(proof["observed_at_utc"]) > HKO_CURRENT_TEMPERATURE_MAX_AGE
    ):
        raise ValueError("HKO current immutable print/cutoff mismatch")
    return Day0CurrentTemperatureState(
        value_native=proof["value_native"], observed_at=datetime.fromisoformat(proof["observed_at_utc"]),
        source=HKO_CURRENT_TEMPERATURE_CHANNEL,
        clock_evidence={**proof, "raw_report": raw_report,
            "raw_report_sha256": hashlib.sha256(raw_report.encode("utf-8")).hexdigest()},
    )


def day0_current_temperature_channels(city: Any) -> tuple[str, tuple[str, ...]] | None:
    """Return the station and physical-current channels admitted for a city."""

    source_type = str(getattr(city, "settlement_source_type", "") or "").strip().lower()
    unit = str(getattr(city, "settlement_unit", "") or "").strip().upper()
    station = "HKO" if source_type == "hko" else str(
        getattr(city, "wu_station", "") or ""
    ).strip().upper()
    if not station or unit not in {"C", "F"}:
        return None
    if source_type == "wu_icao":
        channels = ("wu_icao_history", "aviationweather_metar")
    elif source_type == "hko":
        channels = ("hko_rhrread_spot", HKO_CURRENT_TEMPERATURE_CHANNEL)
    elif source_type == "noaa":
        channels = (f"noaa_wrh_{station.lower()}", f"ogimet_metar_{station.lower()}", "aviationweather_metar")
    else:
        return None
    from src.data.physical_current_sources import physical_current_sources_for_city
    channels += tuple(route.source_channel for route in physical_current_sources_for_city(city))
    return station, tuple(dict.fromkeys(channels))


def read_day0_current_temperature_state(
    *,
    conn: sqlite3.Connection,
    city: Any,
    target_date: str,
    decision_time: datetime,
) -> Day0CurrentTemperatureState | None:
    """Read the latest causal same-station temperature for a Day0 path.

    The caller supplies the canonical forecasts connection, which may have the
    world DB attached read-only.  This is intentionally a data-layer read so a
    producer and a held monitor cannot implement different current-state
    admission rules for the same vector carrier.
    """

    if decision_time.tzinfo is None:
        return None
    city_name = str(getattr(city, "name", "") or "").strip()
    timezone_name = str(getattr(city, "timezone", "") or "").strip()
    unit = str(getattr(city, "settlement_unit", "") or "").strip().upper()
    if not city_name or not timezone_name or unit not in {"C", "F"}:
        return None
    source_contract = day0_current_temperature_channels(city)
    if source_contract is None:
        return None
    station, channels = source_contract
    from src.data.physical_current_sources import physical_current_sources_for_city
    station_routes = {
        route.source_channel: route
        for route in physical_current_sources_for_city(city)
    }
    source_type = str(getattr(city, "settlement_source_type", "") or "").lower()
    base_settlement_channels = (
        {"wu_icao_history"}
        if source_type == "wu_icao"
        else {f"noaa_wrh_{station.lower()}"} if source_type == "noaa" else set()
    )
    try:
        target = date.fromisoformat(str(target_date)[:10])
        tz = ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None
    from src.data.day0_fast_obs import (
        KMA_PRIORITY_STATIONS,
        KmaObservationUnavailable,
        _latest_kma_day0_event_state,
    )

    kma = None
    if station in KMA_PRIORITY_STATIONS:
        try:
            kma = _latest_kma_day0_event_state(
                conn, city=city, target_date=target_date, decision_time=decision_time,
                metric=None,
            )
        except KmaObservationUnavailable:
            return None
    kma_state = (
        Day0CurrentTemperatureState(
            value_native=(
                kma.current_temp_c if unit == "C"
                else kma.current_temp_c * 9.0 / 5.0 + 32.0
            ),
            observed_at=kma.observed_at,
            source="aviationweather_metar",
            input_ref={"kma_event_id": kma.event_id},
        ) if kma is not None else None
    )
    if kma_state is not None:
        return kma_state
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    schema = "world" if "world" in attached else "main"
    table = "world.observation_prints" if schema == "world" else "observation_prints"
    if conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = 'observation_prints'"
    ).fetchone() is None:
        return None
    start = datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)
    end = datetime.combine(target + timedelta(days=1), datetime_time.min, tzinfo=tz).astimezone(UTC)
    placeholders = ",".join("?" for _ in channels)
    try:
        rows = conn.execute(
            f"""
            SELECT publish_ts_utc, value_native, unit, station_id, source_channel,
                   raw_report, fetched_at_utc, rowid
              FROM {table}
             WHERE city = ?
               AND source_channel IN ({placeholders})
               AND publish_ts_utc >= ? AND publish_ts_utc < ?
               AND julianday(publish_ts_utc) <= julianday(?)
               AND {RECEIPT_US_SQL} <= ?
             ORDER BY publish_ts_utc DESC, {RECEIPT_ORDER_DESC_SQL}
            """,
            (
                city_name,
                *channels,
                (start - timedelta(hours=1)).isoformat(),
                (end + timedelta(hours=1)).isoformat(),
                decision_time.astimezone(UTC).isoformat(),
                receipt_us(decision_time),
            ),
        ).fetchall()
    except sqlite3.Error:
        return None
    latest_state = None
    latest_clock = None
    decision_utc = decision_time.astimezone(UTC)
    for publish_raw, value_raw, unit_raw, station_raw, channel_raw, raw_report, fetched_raw, row_id in rows:
        channel = str(channel_raw or "").strip().lower()
        station_raw = str(station_raw or "").strip().upper()
        if station_raw != station and not station_raw.startswith(f"{station}:"):
            continue
        try:
            published = datetime.fromisoformat(str(publish_raw).replace("Z", "+00:00"))
            fetched = datetime.fromisoformat(str(fetched_raw).replace("Z", "+00:00"))
            value = float(value_raw)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None or fetched.tzinfo is None:
            continue
        published = published.astimezone(UTC)
        fetched = fetched.astimezone(UTC)
        if published > decision_utc or fetched > decision_utc:
            continue
        observation_time = published
        from src.data.station_temperature_adapters import CHANNELS, valid_station_print
        route = station_routes.get(channel)
        if channel in CHANNELS.values():
            if route is None or route.station_id != station_raw or not valid_station_print(
                route, str(raw_report or ""), observed_at=published, value=value
            ):
                continue
        clock_evidence = None
        if channel == "fmi_airport_temperature":
            from src.data.fmi_airport_temperature import valid_ledger_print

            if (
                route is None
                or str(unit_raw or "").strip().upper() != "C"
                or fetched < published
                or (target == decision_utc.astimezone(tz).date()
                    and (decision_utc - fetched > timedelta(minutes=25)
                         or decision_utc - published > timedelta(minutes=25)))
                or not valid_ledger_print(str(raw_report or ""), observed_at=published,
                                          value=value, station=route.station)
            ):
                continue
        if channel == "aviationweather_metar":
            from src.data.day0_fast_obs import (
                _T_GROUP_RE,
                metar_observation_time_from_raw,
                metar_t_group_temperature_c,
            )

            observation_time = metar_observation_time_from_raw(
                str(raw_report or ""), published_at=published
            )
            if observation_time is None:
                continue
            if unit == "F":
                if not _T_GROUP_RE.search(str(raw_report or "")):
                    continue
                precise_c = metar_t_group_temperature_c(str(raw_report or ""))
                if precise_c is None:
                    continue
                value = precise_c * 9.0 / 5.0 + 32.0
        elif str(unit_raw or "").strip().upper() != unit:
            continue
        if channel == "hko_rhrread_spot":
            from src.data.day0_observation_reader import hko_temperature_observed_at

            try:
                temperature = json.loads(str(raw_report or ""))
                observation_time = hko_temperature_observed_at(
                    temperature, published_at=published, fetched_at=fetched,
                )
            except (TypeError, ValueError):
                # SCOPE: this source print, never the entire city universe.
                # DRAIN: the next valid source record. RESET: all three causal
                # clocks validate; missing truth is unavailable, not a fake
                # fresh observation at publication time.
                continue
            clock_evidence = {
                "source": channel,
                "station_id": station_raw,
                "raw_report": str(raw_report),
                "raw_report_sha256": hashlib.sha256(str(raw_report).encode("utf-8")).hexdigest(),
                "published_at_utc": published.isoformat(),
                "available_at_utc": fetched.isoformat(),
            }
        elif channel == HKO_CURRENT_TEMPERATURE_CHANNEL:
            try:
                if station_raw != "HKO":
                    continue
                state = replay_hko_current_temperature_print(str(raw_report or ""),
                    representation_updated_at=published,available_at=fetched,decision_time=decision_utc)
                if state.value_native != value or unit != "C":
                    continue
            except (KeyError, TypeError, ValueError, UnicodeError):
                # SCOPE: this CURRENT_ONLY print. DRAIN/RESET: the next valid
                # HQ representation; other lawful observation sources survive.
                continue
            observation_time, clock_evidence = state.observed_at, state.clock_evidence
        observation_time = observation_time.astimezone(UTC)
        if (
            observation_time > decision_utc
            or observation_time.astimezone(tz).date() != target
            or not math.isfinite(value)
        ):
            continue
        # Publication can lag physical observation. Delayed older reports
        # cannot roll back the current state used by entry and held paths.
        # At the SAME physical instant, preserve verified native precision.
        # The HKO native path above has already replayed its body and clocks;
        # FMI remains physical-only unless actual value identity proves grade.
        settlement_grade = (
            channel in base_settlement_channels
            or bool(route is not None and route.settlement_authorized)
        )
        clock = (
            observation_time,
            int(settlement_grade or channel == HKO_CURRENT_TEMPERATURE_CHANNEL),
            published,
            fetched,
        )
        if latest_clock is None or clock > latest_clock:
            latest_clock = clock
            latest_state = Day0CurrentTemperatureState(
                value_native=value,
                observed_at=observation_time,
                source=str(channel_raw),
                clock_evidence=clock_evidence,
                input_ref={"print_id": int(row_id)},
            )
    return latest_state


def remaining_day_extremes_c_with_current_state(
    vectors: list[Day0HourlyVector],
    *,
    target_date: str,
    decision_time: datetime,
    metric: str,
    current_state: Day0CurrentTemperatureState | None,
    settlement_unit: str,
    fallback_window_start: datetime,
    unresolved_window_start: datetime | None = None,
) -> tuple[list[float], dict[str, float]]:
    """Apply the sole Day0 current-state transform, or the shared no-state path."""

    if current_state is None:
        return (
            remaining_day_extremes_c(
                vectors,
                target_date=target_date,
                now=decision_time,
                metric=metric,
                window_start=fallback_window_start,
            ),
            {},
        )
    if metric not in {"high", "low"} or settlement_unit not in {"C", "F"}:
        raise ValueError("DAY0_CURRENT_STATE_INPUT_INVALID")
    from src.config import day0_current_state_innovation_e_fold_hours
    from src.signal.day0_window import condition_day0_hourly_members_on_current_state

    observed_utc = current_state.observed_at.astimezone(UTC)
    if observed_utc > decision_time.astimezone(UTC):
        return [], {}
    aligned = align_day0_hourly_vectors_on_common_causal_grid(
        vectors, target_date=target_date, window_start=(unresolved_window_start or observed_utc)
    )
    if aligned is None:
        return [], {}
    causal_grid, aligned_rows = aligned
    current_c = (
        float(current_state.value_native)
        if settlement_unit == "C"
        else (float(current_state.value_native) - 32.0) * 5.0 / 9.0
    )
    conditioned = condition_day0_hourly_members_on_current_state(
        np.asarray([list(row) for row in aligned_rows], dtype=float),
        [instant.isoformat() for instant in causal_grid],
        observation_time=observed_utc,
        current_temp=current_c,
        e_fold_hours=day0_current_state_innovation_e_fold_hours(),
    )
    if conditioned is None:
        return [], {}
    conditioned_members, innovation_values = conditioned
    remaining_indices = [
        index for index, instant in enumerate(causal_grid) if instant > observed_utc
    ]
    if not remaining_indices:
        target = date.fromisoformat(str(target_date)[:10])
        try:
            timezone_obj = ZoneInfo(str(vectors[0].timezone_name))
        except (IndexError, ZoneInfoNotFoundError):
            return [], {}
        day_end = datetime.combine(
            target + timedelta(days=1),
            datetime_time.min,
            tzinfo=timezone_obj,
        ).astimezone(UTC)
        if (
            causal_grid[0] != causal_grid[-1]
            or not timedelta(0) < day_end - observed_utc <= timedelta(hours=1)
            or not timedelta(0) <= observed_utc - causal_grid[0] <= timedelta(hours=1)
        ):
            return [], {}
        remaining_indices = [0]
    remaining = conditioned_members[:, remaining_indices]
    if unresolved_window_start is not None:
        # A spot update does not supply cumulative coverage of the gap before
        # it. Keep the issued forecast approximation there, without applying
        # a future innovation backwards or replacing a past knot by the spot.
        past_indices = [i for i, at in enumerate(causal_grid)
                        if unresolved_window_start <= at <= observed_utc]
        if past_indices:
            remaining = np.concatenate((np.asarray(aligned_rows)[:, past_indices], remaining), axis=1)
    values = remaining.min(axis=1) if metric == "low" else remaining.max(axis=1)
    return (
        [float(value) for value in values.tolist()],
        {
            str(vector.model): float(innovation)
            for vector, innovation in zip(vectors, innovation_values, strict=True)
        },
    )


def day0_effective_path_sigma_c(
    *,
    source_clock_predictive_sigma_c: float,
    centers_c: Iterable[float],
    instrument_sigma_c: float,
    observation_margin_c: float = 0.0,
) -> float:
    """One variance closure for producer and held Day0 carriers."""

    centers = np.asarray(tuple(float(value) for value in centers_c), dtype=float)
    total = float(source_clock_predictive_sigma_c)
    instrument = float(instrument_sigma_c)
    margin = float(observation_margin_c)
    if (
        not centers.size
        or not np.isfinite(centers).all()
        or not math.isfinite(total)
        or total <= 0.0
        or not math.isfinite(instrument)
        or instrument < 0.0
        or not math.isfinite(margin)
        or margin < 0.0
    ):
        raise ValueError("DAY0_CURRENT_PATH_SIGMA_INVALID")
    residual = math.sqrt(max(total**2 - float(np.std(centers, ddof=0)) ** 2, 0.0))
    baseline = math.hypot(instrument, margin / 2.0)
    return max(baseline, residual)


@dataclass(frozen=True)
class Day0ConditionalHighShape:
    """Conditional variance of the existing equal-weight hourly-provider carrier."""

    provider_centers_c: tuple[float, ...]
    ensemble_centers_c: tuple[float, ...]
    ensemble_within_sigma_c: float
    provider_between_sigma_c: float
    ensemble_center_delta_c: float
    model_residual_sigma_c: float
    effective_sigma_c: float
    extra_sigma_c: float
    identity: str
    witness: Mapping[str, object]


def day0_conditional_high_run_proof(
    providers: list[Day0HourlyVector],
    ensemble: list[Day0HourlyVector],
    *, decision_time: datetime,
) -> tuple[dict[str, tuple[datetime, str, str]], datetime]:
    """Prove each provider clock and one coherent, current ENS product run."""

    cutoff = decision_time.astimezone(UTC)

    def run_meta(vector: Day0HourlyVector) -> tuple[datetime, str, str]:
        try:
            meta = json.loads(str(vector.source_run_meta_json or ""))
            run = _day0_parse_aware_clock(
                meta["provider_source_cycle_time_utc"], field_name="provider_cycle"
            )
            available = _day0_parse_aware_clock(
                meta["provider_source_available_at_utc"], field_name="provider_available"
            )
            finished = _day0_parse_aware_clock(
                meta["fetch_finished_at"], field_name="fetch_finished"
            )
            captured = _day0_parse_aware_clock(vector.captured_at, field_name="captured_at")
            request_hash = str(meta["request_hash"])
            provider_run_id = str(meta["provider_run_id"])
            if not (
                run <= available <= finished <= cutoff
                and captured <= finished
                and request_hash and provider_run_id
            ):
                raise ValueError("invalid clocks or identity")
            return run, request_hash, provider_run_id
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("DAY0_CONDITIONAL_HIGH_RUN_PROOF_INVALID") from exc

    provider_meta = {vector.model: run_meta(vector) for vector in providers}
    ecm_run = provider_meta.get("ecmwf_ifs", (None, "", ""))[0]
    if ecm_run is None:
        raise ValueError("DAY0_CONDITIONAL_HIGH_ECMWF_RUN_MISSING")
    ens_meta = [run_meta(vector) for vector in ensemble]
    if len(ens_meta) != DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT:
        raise ValueError("DAY0_CONDITIONAL_HIGH_ENSEMBLE_UNAVAILABLE")
    ensemble_run = ens_meta[0][0]
    if any(run != ensemble_run for run, _hash, _id in ens_meta):
        raise ValueError("DAY0_CONDITIONAL_HIGH_ENSEMBLE_CYCLE_MISMATCH")
    if len({(request_hash, provider_run_id) for _run, request_hash, provider_run_id in ens_meta}) != 1:
        raise ValueError("DAY0_CONDITIONAL_HIGH_ENSEMBLE_CAPTURE_MISMATCH")
    # The deterministic IFS and Ensemble API have independent release clocks.
    # The ENS bundle is current by its own run identity, never capture age.
    refusal = day0_ensemble_run_refusal(ensemble_run, decision_time=cutoff)
    if refusal is not None:
        raise ValueError(refusal)
    return provider_meta, ensemble_run


def day0_hourly_provider_representatives(
    vectors: list[Day0HourlyVector],
) -> list[Day0HourlyVector]:
    """Keep the first, most specific hourly path for each physical provider."""
    from src.strategy.live_inference.source_clock_vnext import provider_family_for_source

    seen: set[str] = set()
    representatives: list[Day0HourlyVector] = []
    for vector in vectors:
        family = provider_family_for_source(str(vector.model))
        if family not in seen:
            representatives.append(vector)
            seen.add(family)
    return representatives


def day0_conditional_high_shape(
    *,
    conn: sqlite3.Connection,
    city: Any,
    target_date: str,
    decision_time: datetime,
    current_state: Day0CurrentTemperatureState,
    provider_vectors: list[Day0HourlyVector] | None = None,
) -> Day0ConditionalHighShape:
    """Condition providers and 51 ENS members on the same observed hour/run.

    The 51 members measure within-IFS uncertainty; they are not additional
    provider scenarios in the existing equal-weight remaining-path mixture.
    """

    if decision_time.tzinfo is None or current_state.observed_at.tzinfo is None:
        raise ValueError("DAY0_CONDITIONAL_HIGH_OBSERVATION_CLOCK_INVALID")
    observed = current_state.observed_at.astimezone(UTC)
    cutoff = decision_time.astimezone(UTC)
    if observed > cutoff:
        raise ValueError("DAY0_CONDITIONAL_HIGH_OBSERVATION_AFTER_DECISION")
    unit = str(getattr(city, "settlement_unit", "") or "").upper()
    if unit not in {"C", "F"}:
        raise ValueError("DAY0_CONDITIONAL_HIGH_UNIT_INVALID")
    expected = tuple(day0_hourly_models_for_city(city))
    try:
        local_target = observed.astimezone(ZoneInfo(str(city.timezone))).date().isoformat()
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("DAY0_CONDITIONAL_HIGH_TIMEZONE_INVALID") from exc
    if local_target != target_date:
        raise ValueError("DAY0_CONDITIONAL_HIGH_OBSERVATION_DATE_MISMATCH")
    if provider_vectors is None:
        provider_vectors = read_freshest_day0_hourly_vectors(
            city=str(city.name), target_date=target_date, now=cutoff, conn=conn,
            expected_models=expected, require_expected=True,
            max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
            remaining_window_start=observed, require_complete_remaining_window=True,
        )
    elif provider_vectors != select_ready_day0_hourly_vectors(
        provider_vectors, target_date=target_date, now=cutoff,
        expected_models=expected, require_expected=True,
        max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
        remaining_window_start=observed, require_complete_remaining_window=True,
    ):
        raise ValueError("DAY0_CONDITIONAL_HIGH_PROVIDER_BUNDLE_INVALID")
    if tuple(vector.model for vector in provider_vectors) != expected or len(expected) < 2:
        raise ValueError("DAY0_CONDITIONAL_HIGH_PROVIDER_BUNDLE_INCOMPLETE")
    if any(
        vector.city != str(city.name) or vector.target_date != target_date
        or vector.timezone_name != str(city.timezone)
        for vector in provider_vectors
    ):
        raise ValueError("DAY0_CONDITIONAL_HIGH_PROVIDER_SCOPE_MISMATCH")
    ensemble_models = day0_source_clock_ensemble_member_models()
    ensemble = read_freshest_day0_hourly_vectors(
        city=str(city.name), target_date=target_date, now=cutoff, conn=conn,
        expected_models=ensemble_models, require_expected=True,
        max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
        remaining_window_start=observed, require_complete_remaining_window=True,
    )
    if len(ensemble) != DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT:
        raise ValueError("DAY0_CONDITIONAL_HIGH_ENSEMBLE_UNAVAILABLE")

    provider_meta, ensemble_run = day0_conditional_high_run_proof(
        provider_vectors, ensemble, decision_time=cutoff
    )
    provider_vectors = day0_hourly_provider_representatives(provider_vectors)
    provider_centers, _ = remaining_day_extremes_c_with_current_state(
        provider_vectors, target_date=target_date, decision_time=cutoff,
        metric="high", current_state=current_state, settlement_unit=unit,
        fallback_window_start=observed,
    )
    ensemble_centers, _ = remaining_day_extremes_c_with_current_state(
        ensemble, target_date=target_date, decision_time=cutoff,
        metric="high", current_state=current_state, settlement_unit=unit,
        fallback_window_start=observed,
    )
    if len(provider_centers) != len(provider_vectors) or len(ensemble_centers) != len(ensemble_models):
        raise ValueError("DAY0_CONDITIONAL_HIGH_OBSERVATION_ANCHOR_UNAVAILABLE")
    provider_values = np.asarray(provider_centers, dtype=float)
    ensemble_values = np.asarray(ensemble_centers, dtype=float)
    if not np.isfinite(provider_values).all() or not np.isfinite(ensemble_values).all():
        raise ValueError("DAY0_CONDITIONAL_HIGH_CENTERS_INVALID")
    within = float(np.std(ensemble_values, ddof=0))
    between = float(np.std(provider_values, ddof=0))
    delta = abs(float(np.mean(provider_values) - np.mean(ensemble_values)))
    from src.signal.day0_obs_latency import (
        stale_extreme_uncertainty_margin,
        staleness_budget_minutes,
    )

    margin = stale_extreme_uncertainty_margin(
        unit="C", obs_age_minutes=(cutoff - observed).total_seconds() / 60.0,
        budget_minutes=staleness_budget_minutes(str(city.name)),
    )
    # Provider between-spread already exists in the carrier's equal-weight
    # scenario centers. Only within-ENS and center disagreement remain noise.
    from src.signal.ensemble_signal import sigma_instrument_for_city

    instrument_c = float(sigma_instrument_for_city(city).to("C").value)
    model_residual = math.hypot(within, delta)
    effective = max(model_residual, math.hypot(instrument_c, margin / 2.0))
    extra = math.sqrt(max(effective**2 - instrument_c**2, 0.0))
    witness = {
        "semantics": "day0_conditional_high_equal_provider_v1",
        "city": str(city.name), "target_date": target_date,
        "observed_at": observed.isoformat(),
        "observed_native": float(current_state.value_native),
        "observation_unit": unit,
        "observed_source": str(current_state.source),
        "provider_models": [vector.model for vector in provider_vectors],
        "provider_vector_ids": [
            _vector_id(vector.model, vector.city, vector.target_date, vector.captured_at)
            for vector in provider_vectors
        ],
        "provider_runs": {model: meta[0].isoformat() for model, meta in provider_meta.items()},
        "ensemble_vector_ids": [
            _vector_id(vector.model, vector.city, vector.target_date, vector.captured_at)
            for vector in ensemble
        ],
        "ensemble_run": ensemble_run.isoformat(),
        "provider_centers_c": [float(value) for value in provider_values],
        "ensemble_centers_c": [float(value) for value in ensemble_values],
        "provider_scenario_weights": [1.0 / len(provider_vectors)] * len(provider_vectors),
        "within_c": within, "between_c": between, "delta_c": delta,
        "latency_margin_c": margin, "instrument_sigma_c": instrument_c,
        "model_residual_sigma_c": model_residual,
        "effective_sigma_c": effective, "extra_sigma_c": extra,
    }
    identity = hashlib.sha256(json.dumps(
        witness, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    return Day0ConditionalHighShape(
        provider_centers_c=tuple(float(value) for value in provider_values),
        ensemble_centers_c=tuple(float(value) for value in ensemble_values),
        ensemble_within_sigma_c=within,
        provider_between_sigma_c=between,
        ensemble_center_delta_c=delta,
        model_residual_sigma_c=model_residual,
        effective_sigma_c=effective,
        extra_sigma_c=extra,
        identity=identity,
        witness=witness,
    )


# ---------------------------------------------------------------------------
# Throttled refresh hook (wired from the day0 emit cycle; NO daemon restart
# needed for the schema — table is created on first write).
# ---------------------------------------------------------------------------

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_MONOTONIC: dict[str, float] = {}
_INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC: dict[str, float] = {}
_INCOMPLETE_RETRY_STREAK: dict[str, int] = {}


def _refresh_throttled_locked(
    refresh_key: str,
    *,
    now_monotonic: float,
    interval_s: float,
    bypass_interval: bool = False,
) -> bool:
    """Return whether refresh is throttled while ``_REFRESH_LOCK`` is held."""

    retry_not_before = _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.get(refresh_key)
    if retry_not_before is not None:
        if now_monotonic < retry_not_before:
            return True
        _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.pop(refresh_key, None)
    if bypass_interval:
        return False
    last = _LAST_REFRESH_MONOTONIC.get(refresh_key)
    return last is not None and now_monotonic - last < float(interval_s)


def _persist_complete_ensemble_bundle(
    *,
    name: str,
    ensemble_vectors: list[Day0HourlyVector],
    ensemble_request_hash: str,
    ensemble_target_dates: tuple[str, ...],
    ensemble_window_starts: Mapping[str, datetime | None],
    materialization_time: datetime,
    persist_lock_blocking: bool,
) -> tuple[int, bool]:
    """Persist a complete 51-member ENS carrier for every requested date.

    Selection is the same strict live-authority read the deterministic bundle
    uses (all members, bounded skew, complete remaining window). An incomplete
    carrier is logged and never treated as a complete bundle. Returns physical
    writes plus whether every requested target passed strict persist readback.
    """

    if not ensemble_target_dates:
        return 0, True
    ensemble_expected = day0_source_clock_ensemble_member_models()
    persisted = 0
    complete = True
    for target_date in ensemble_target_dates:
        window_start = ensemble_window_starts.get(target_date)
        selected_ensemble = (
            select_ready_day0_hourly_vectors(
                ensemble_vectors,
                target_date=target_date,
                now=materialization_time,
                expected_models=ensemble_expected,
                require_expected=True,
                max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                remaining_window_start=window_start,
                require_complete_remaining_window=True,
            )
            if window_start is not None
            and ensemble_request_hash
            and len(ensemble_vectors) == DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT
            else []
        )
        if not selected_ensemble:
            logger.warning(
                "DAY0_SOURCE_CLOCK_ENSEMBLE_BUNDLE_UNAVAILABLE "
                "city=%s target_date=%s available=%d expected=%d",
                name,
                target_date,
                len(ensemble_vectors),
                DAY0_SOURCE_CLOCK_ENSEMBLE_MEMBER_COUNT,
            )
            complete = False
            continue
        try:
            persisted += persist_day0_hourly_vectors(
                selected_ensemble,
                target_date=target_date,
                request_hash=ensemble_request_hash,
                endpoint=OPENMETEO_ENSEMBLE_URL,
                lock_blocking=persist_lock_blocking,
            )
            post_persist_materialization_time = _day0_utc_now()
            readback = read_freshest_day0_hourly_vectors(
                city=name,
                target_date=target_date,
                now=post_persist_materialization_time,
                expected_models=ensemble_expected,
                require_expected=True,
                max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                remaining_window_start=window_start,
                require_complete_remaining_window=True,
            )
        except Exception as exc:  # noqa: BLE001 - caller applies bounded retry debt
            complete = False
            logger.warning(
                "DAY0_SOURCE_CLOCK_ENSEMBLE_PERSIST_READBACK_FAILED "
                "city=%s target_date=%s exc=%s: %s",
                name,
                target_date,
                type(exc).__name__,
                exc,
            )
            continue
        if not readback:
            complete = False
            logger.warning(
                "DAY0_SOURCE_CLOCK_ENSEMBLE_PERSIST_READBACK_INCOMPLETE "
                "city=%s target_date=%s",
                name,
                target_date,
            )
    return persisted, complete


def _day0_readback_bundle_is_current_or_newer(
    selected: Iterable[Day0HourlyVector],
    readback: Iterable[Day0HourlyVector],
) -> bool:
    """Accept an idempotent or concurrent newer write as this date's drain.

    A zero-row INSERT can mean the exact selected rows already exist, but a
    fresh row from an older provider cycle must not clear retry debt.  Request/
    capture time is the local ordering; when both rows carry provider
    provenance, the provider cycle must also be monotone.
    """
    selected_by_model = {str(vector.model): vector for vector in selected}
    readback_by_model = {str(vector.model): vector for vector in readback}
    if set(selected_by_model) != set(readback_by_model):
        return False
    for model, selected_vector in selected_by_model.items():
        current_vector = readback_by_model[model]
        try:
            selected_capture = datetime.fromisoformat(
                str(selected_vector.captured_at).replace("Z", "+00:00")
            ).astimezone(UTC)
            current_capture = datetime.fromisoformat(
                str(current_vector.captured_at).replace("Z", "+00:00")
            ).astimezone(UTC)
        except (AttributeError, TypeError, ValueError):
            return False
        if current_capture < selected_capture:
            return False
        try:
            selected_meta = json.loads(str(selected_vector.source_run_meta_json or ""))
            current_meta = json.loads(str(current_vector.source_run_meta_json or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            selected_meta = current_meta = {}
        selected_run = selected_meta.get("provider_source_cycle_time_utc")
        current_run = current_meta.get("provider_source_cycle_time_utc")
        if selected_run and current_run:
            try:
                selected_cycle = datetime.fromisoformat(
                    str(selected_run).replace("Z", "+00:00")
                ).astimezone(UTC)
                current_cycle = datetime.fromisoformat(
                    str(current_run).replace("Z", "+00:00")
                ).astimezone(UTC)
            except (AttributeError, TypeError, ValueError):
                return False
            if current_cycle < selected_cycle:
                return False
    return True


def maybe_refresh_day0_hourly_vectors(
    cities: list[Any],
    *,
    decision_time: datetime,
    interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
    budget_s: float = DEFAULT_REFRESH_BUDGET_S,
    max_cities: int = DEFAULT_REFRESH_MAX_CITIES,
    timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
    quota_critical_cities: int = 0,
    quota_priority_cities: int = 0,
    critical_max_attempts: int | None = None,
    allow_priority_recovery: bool = False,
    remaining_window_starts: Mapping[tuple[str, str], datetime] | None = None,
    causal_run_boundaries: Mapping[tuple[str, str], datetime] | None = None,
    provider_run_hwm: Mapping[str, Day0ProviderRunHwm] | None = None,
    release_due_city_dates: Iterable[tuple[str, str]] = (),
    high_ensemble_city_dates: Iterable[tuple[str, str]] = (),
    persist_lock_blocking: bool = True,
    return_stats: bool = False,
) -> int | Day0HourlyRefreshStats:
    """Throttled per-city fetch+persist of the freshest high-res hourly curves.

    Cities with an in-domain regional high-res model use that regional source;
    other cities use the ECMWF IFS global fallback from
    ``day0_hourly_models_for_city``. One open-meteo call per city per interval.
    Fail-soft per city. A maintenance fetch failure retains the normal refresh
    interval so a provider outage cannot turn the 45-second scheduler into a
    quota-consuming retry storm. Missing-authority priority and held-capital
    failures instead use the same bounded retry debt as incomplete bundles, so
    a recovered provider cannot leave current probability dark for the full
    normal interval. An incomplete fetch is never persisted as a partial live
    bundle. The ordered critical prefix may consume only the final held-position
    reserve; the following priority prefix may consume the source-clock reserve
    but never the critical reserve. All remaining cities stay in maintenance
    quota.  When explicitly authorized, a priority city may use the bounded
    recovery lane after ordinary priority quota is exhausted.  That lane is
    capped below the critical limits, preserving a hard held-capital floor.

    ``max_cities`` counts fetch attempts, never offered positions: a throttled
    or already-current city is skipped without consuming a slot, so the page
    scans forward to the next due city.  ``critical_max_attempts`` (None =
    unbounded) caps the attempts the critical prefix may spend, so due held
    cities go first but cannot consume the slots reserved for the priority
    prefix behind them.

    ``causal_run_boundaries`` is a separate, optional per-(city, target_date)
    map from ``remaining_window_starts``: it carries
    ``read_day0_current_temperature_state(...).observed_at`` (the same
    predicate the materializer and the live-materialization-queue preflight
    already use), consulted by the fetcher to choose between the pinned
    single-runs endpoint and the standard endpoint for each model
    (``_select_day0_run_endpoint``). A city/date pair absent from this map
    fetches with no boundary known, so only the publicly-usable gate can be
    resolved there; the coverage gate keeps today's freshest-run-only
    behavior for that pair.
    """
    if decision_time.tzinfo is None:
        raise ValueError("decision_time must be timezone-aware")
    source_decision_time = decision_time.astimezone(UTC)
    from src.data.bayes_precision_fusion_download import (
        bayes_precision_fusion_held_quota_priority,
        bayes_precision_fusion_recovery_quota_priority,
        bayes_precision_fusion_source_clock_quota_priority,
    )

    def strict_window_start(city: Any, target_date: str) -> datetime | None:
        explicit = (remaining_window_starts or {}).get(
            (str(getattr(city, "name", "") or ""), target_date)
        )
        if explicit is not None:
            if explicit.tzinfo is None or explicit > decision_time:
                return None
            return explicit.astimezone(UTC)
        try:
            tz = ZoneInfo(str(getattr(city, "timezone")))
            target = date.fromisoformat(target_date)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            return None
        local_day = decision_time.astimezone(tz).date()
        if target < local_day:
            return None
        if target == local_day:
            return decision_time.astimezone(UTC)
        return datetime.combine(target, datetime_time.min, tzinfo=tz).astimezone(UTC)

    def mark_incomplete(
        *,
        refresh_key: str,
        quota_lane: str,
        name: str,
        target_dates: tuple[str, ...],
        expected_models: tuple[str, ...],
        available_models: tuple[str, ...],
        missing_models: tuple[str, ...],
        reason: str,
    ) -> None:
        nonlocal incomplete_expected_bundles
        incomplete_expected_bundles += 1
        unavailable_bundles.append(
            Day0HourlyBundleUnavailable(
                city=name,
                target_dates=target_dates,
                expected_models=expected_models,
                available_models=available_models,
                missing_models=missing_models,
                reason=reason,
            )
        )
        with _REFRESH_LOCK:
            _LAST_REFRESH_MONOTONIC.pop(refresh_key, None)
            streak = _INCOMPLETE_RETRY_STREAK.get(refresh_key, 0) + 1
            _INCOMPLETE_RETRY_STREAK[refresh_key] = streak
            retry_cap_s = (
                INCOMPLETE_BUNDLE_CRITICAL_RETRY_MAX_INTERVAL_S
                if quota_lane in {"critical", "priority"}
                else INCOMPLETE_BUNDLE_RETRY_MAX_INTERVAL_S
            )
            max_exponent = max(
                0,
                int(
                    math.ceil(
                        math.log2(
                            retry_cap_s / INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
                        )
                    )
                ),
            )
            retry_delay_s = min(
                retry_cap_s,
                INCOMPLETE_BUNDLE_RETRY_INTERVAL_S
                * (2 ** min(streak - 1, max_exponent)),
            )
            _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] = (
                time.monotonic() + retry_delay_s
            )

    written = 0
    skipped_throttle = 0
    skipped_quota = 0
    incomplete_expected_bundles = 0
    unavailable_bundles: list[Day0HourlyBundleUnavailable] = []
    ready_city_dates: list[tuple[str, str]] = []
    priority_reserve_exhausted = False
    budget_exhausted = False
    now_monotonic = time.monotonic()
    started_monotonic = now_monotonic
    checked = 0
    critical_checked = 0
    critical_city_count = max(0, int(quota_critical_cities))
    priority_city_count = max(0, int(quota_priority_cities))
    release_due_scopes = frozenset(
        (str(city).strip(), str(target_date).strip())
        for city, target_date in release_due_city_dates
    )
    high_ensemble_scopes = frozenset(
        (str(city).strip().casefold(), str(target_date).strip())
        for city, target_date in high_ensemble_city_dates
    )
    for city_index, city in enumerate(cities):
        if checked >= max(0, int(max_cities)):
            break
        if budget_s > 0.0 and checked > 0 and (time.monotonic() - started_monotonic) >= budget_s:
            budget_exhausted = True
            logger.warning(
                "DAY0_HOURLY_VECTORS_REFRESH_BUDGET_EXHAUSTED checked=%d budget_s=%.3f",
                checked,
                budget_s,
            )
            break
        name = str(getattr(city, "name", "") or "")
        if not name:
            continue
        if (
            city_index < critical_city_count
            and critical_max_attempts is not None
            and critical_checked >= max(0, int(critical_max_attempts))
        ):
            continue
        try:
            target_dates = day0_hourly_target_dates_for_refresh(
                city=city, decision_time=decision_time
            )
            refresh_key = f"{name}|{target_dates[0]}"
            models = day0_hourly_models_for_city(city)
            if not models:
                continue
            required_hwm = {
                model: provider_run_hwm[model]
                for model in models
                if provider_run_hwm is not None and model in provider_run_hwm
            }
            release_due = (
                (name, target_dates[0]) in release_due_scopes and bool(required_hwm)
            )
            window_starts = {
                target_date: strict_window_start(city, target_date)
                for target_date in target_dates
            }
            # One fetch call covers every date in target_dates at once, so
            # only one boundary can drive endpoint selection here. The map is
            # populated per (city, target_date) -- not just each city's first
            # date -- but target_dates[0] is the correct key to read: it is
            # the earliest, already-started local date (a not-yet-started
            # date has no boundary and gap (b) cannot apply to it anyway).
            causal_boundary = (causal_run_boundaries or {}).get(
                (name, target_dates[0])
            )
            deterministic_ready = (
                not release_due
                and _current_provider_bundle_already_persisted(
                    city=name,
                    target_dates=target_dates,
                    expected_models=models,
                    required_hwm=required_hwm,
                    decision_time=decision_time,
                    remaining_window_starts=window_starts,
                )
            )
            if city_index < critical_city_count:
                quota_lane = "critical"
                quota_context = quota_tracker.critical_lane()
                transport_quota_context = (
                    bayes_precision_fusion_held_quota_priority()
                )
            elif city_index < critical_city_count + priority_city_count:
                quota_lane = "priority"
                if allow_priority_recovery:
                    with quota_tracker.priority_lane():
                        priority_available = quota_tracker.can_call()
                    if not priority_available:
                        quota_lane = "recovery"
                quota_context = (
                    quota_tracker.recovery_lane()
                    if quota_lane == "recovery"
                    else quota_tracker.priority_lane()
                )
                transport_quota_context = (
                    bayes_precision_fusion_recovery_quota_priority()
                    if quota_lane == "recovery"
                    else bayes_precision_fusion_source_clock_quota_priority()
                )
            else:
                quota_lane = "maintenance"
                quota_context = nullcontext()
                transport_quota_context = nullcontext()
            low_ensemble_dates = (
                day0_source_clock_ensemble_target_dates(
                    city=city,
                    decision_time=decision_time,
                )
                if quota_lane in {"priority", "recovery"}
                else ()
            )
            current_local_date = decision_time.astimezone(
                ZoneInfo(str(getattr(city, "timezone")))
            ).date().isoformat()
            ensemble_target_dates = tuple(dict.fromkeys((
                *low_ensemble_dates,
                *(
                    (current_local_date,)
                    if quota_lane in {"critical", "priority", "recovery"}
                    and (name.casefold(), current_local_date) in high_ensemble_scopes
                    else ()
                ),
            )))
            if ensemble_target_dates:
                # ENS-required entry refreshes own a distinct retry/throttle
                # identity so deterministic-only completion cannot clear or
                # defer their carrier debt.
                refresh_key = (
                    f"{refresh_key}|ens={','.join(ensemble_target_dates)}"
                )
            ensemble_window_starts = {
                target_date: (
                    window_starts.get(target_date)
                    or strict_window_start(city, target_date)
                )
                for target_date in ensemble_target_dates
            }
            ensemble_ready = not ensemble_target_dates
            ensemble_incomplete = False
            ensemble_available_models: tuple[str, ...] = ()
            ensemble_missing_models: tuple[str, ...] = ()
            ensemble_vectors: list[Day0HourlyVector] = []
            ensemble_request_hash = ""
            ensemble_run_due = False
            with _REFRESH_LOCK:
                retry_not_before = _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.get(
                    refresh_key
                )
                if quota_lane == "critical" and retry_not_before is not None:
                    _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] = min(
                        retry_not_before,
                        now_monotonic
                        + INCOMPLETE_BUNDLE_CRITICAL_RETRY_MAX_INTERVAL_S,
                    )
                if now_monotonic < _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.get(refresh_key, 0.0):
                    skipped_throttle += 1
                    continue
                throttled = _refresh_throttled_locked(
                    refresh_key,
                    now_monotonic=now_monotonic,
                    interval_s=interval_s,
                    bypass_interval=release_due,
                )
            if throttled:
                ensemble_run_due = bool(
                    ensemble_target_dates
                    and _new_ensemble_run_due_for_refresh(
                        city=name, target_dates=ensemble_target_dates,
                        decision_time=decision_time,
                        remaining_window_starts=ensemble_window_starts,
                    )
                )
                if not ensemble_run_due:
                    skipped_throttle += 1
                    continue
            if ensemble_target_dates:
                ensemble_run_hwm = _probe_day0_source_clock_ensemble_run_hwm(
                    decision_time=decision_time, timeout_s=timeout_s
                )
                ensemble_ready = bool(
                    ensemble_run_hwm is not None
                    and _current_ensemble_bundle_already_persisted(
                        city=name,
                        target_dates=ensemble_target_dates,
                        run_hwm=ensemble_run_hwm,
                        decision_time=decision_time,
                        remaining_window_starts=ensemble_window_starts,
                    )
                )
            if deterministic_ready and ensemble_ready:
                # Process-local throttles cannot deduplicate concurrent daemon
                # owners. Shared DB + exact current identities are the
                # composite no-fetch authority; clear retry debt only here.
                with _REFRESH_LOCK:
                    _LAST_REFRESH_MONOTONIC[refresh_key] = now_monotonic
                    _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.pop(refresh_key, None)
                    _INCOMPLETE_RETRY_STREAK.pop(refresh_key, None)
                continue
            # The hourly builder delegates exact-run transport to the BPF
            # module, which owns a separate process-local tracker instance over
            # the same durable quota file.  Carry the selected economic lane to
            # both trackers; otherwise priority/recovery work is silently
            # reclassified as maintenance at the HTTP reservation boundary.
            with quota_context, transport_quota_context:
                if not quota_tracker.can_call():
                    skipped_quota += 1
                    if quota_lane in {"priority", "recovery"}:
                        priority_reserve_exhausted = True
                        logger.error(
                            "DAY0_HOURLY_PRIORITY_RECOVERY_EXHAUSTED "
                            "city=%s checked=%d lane=%s; held reserve preserved",
                            name,
                            checked,
                            quota_lane,
                        )
                    break
                with _REFRESH_LOCK:
                    if _refresh_throttled_locked(
                        refresh_key,
                        now_monotonic=now_monotonic,
                        interval_s=interval_s,
                        bypass_interval=release_due or ensemble_run_due,
                    ):
                        skipped_throttle += 1
                        continue
                    _LAST_REFRESH_MONOTONIC[refresh_key] = now_monotonic
                checked += 1
                if quota_lane == "critical":
                    critical_checked += 1
                vectors: list[Day0HourlyVector] = []
                request_hash = ""
                materialization_time: datetime | None = None
                if not deterministic_ready:
                    try:
                        try:
                            vectors, request_hash = fetch_day0_hourly_vectors(
                                city,
                                models=models,
                                now=source_decision_time,
                                timeout_s=timeout_s,
                                causal_boundary_utc=causal_boundary,
                            )
                        except TypeError as exc:
                            message = str(exc)
                            if "causal_boundary_utc" in message:
                                try:
                                    vectors, request_hash = fetch_day0_hourly_vectors(
                                        city,
                                        models=models,
                                        now=source_decision_time,
                                        timeout_s=timeout_s,
                                    )
                                except TypeError as exc2:
                                    if "timeout_s" not in str(exc2):
                                        raise
                                    vectors, request_hash = fetch_day0_hourly_vectors(
                                        city, models=models, now=source_decision_time
                                    )
                            elif "timeout_s" in message:
                                vectors, request_hash = fetch_day0_hourly_vectors(
                                    city, models=models, now=source_decision_time
                                )
                            else:
                                raise
                    except Exception as exc:  # noqa: BLE001 - preserve ENS sibling
                        logger.warning(
                            "DAY0_HOURLY_VECTORS_FETCH_FAILED city=%s exc=%s: %s",
                            name,
                            type(exc).__name__,
                            exc,
                        )
                        vectors = []
                        request_hash = ""
                    materialization_time = _day0_utc_now()
                if ensemble_target_dates and not ensemble_ready:
                    try:
                        ensemble_vectors, ensemble_request_hash = (
                            fetch_day0_source_clock_ensemble_vectors(
                                city,
                                now=source_decision_time,
                                timeout_s=timeout_s,
                                window_start=min(
                                    (
                                        start
                                        for start in ensemble_window_starts.values()
                                        if start is not None
                                    ),
                                    default=None,
                                ),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - preserve deterministic sibling
                        logger.warning(
                            "DAY0_SOURCE_CLOCK_ENSEMBLE_FETCH_FAILED city=%s exc=%s: %s",
                            name,
                            type(exc).__name__,
                            exc,
                        )
                        ensemble_vectors = []
                        ensemble_request_hash = ""
                    materialization_time = _day0_utc_now()
                    # QUOTA (round 6, 2026-09-06): persist the ENS carrier the moment it
                    # is complete. It used to be persisted only after the deterministic
                    # bundle had passed every completeness gate below, so each
                    # deterministic ``continue`` (fetch unavailable, model missing,
                    # remaining window incomplete) discarded an already-paid, complete
                    # 51-member bundle and the same ensemble request re-issued on every
                    # pass for any city whose deterministic bundle stays incomplete
                    # (Los Angeles / Seattle / San Francisco / Lucknow: zero member rows
                    # ever persisted, 9-17 attempts each). The two carriers are
                    # independent data products; only their fetch shares a pass.
                    ensemble_written, ensemble_complete = _persist_complete_ensemble_bundle(
                        name=name,
                        ensemble_vectors=ensemble_vectors,
                        ensemble_request_hash=ensemble_request_hash,
                        ensemble_target_dates=ensemble_target_dates,
                        ensemble_window_starts=ensemble_window_starts,
                        materialization_time=materialization_time,
                        persist_lock_blocking=persist_lock_blocking,
                    )
                    written += ensemble_written
                    if not ensemble_complete:
                        ensemble_incomplete = True
                        ensemble_available_models = tuple(
                            dict.fromkeys(str(vector.model) for vector in ensemble_vectors)
                        )
                        ensemble_missing_models = tuple(
                            model
                            for model in day0_source_clock_ensemble_member_models()
                            if model not in ensemble_available_models
                        )
                if deterministic_ready:
                    if ensemble_incomplete:
                        mark_incomplete(
                            refresh_key=refresh_key,
                            quota_lane=quota_lane,
                            name=name,
                            target_dates=ensemble_target_dates,
                            expected_models=day0_source_clock_ensemble_member_models(),
                            available_models=ensemble_available_models,
                            missing_models=ensemble_missing_models,
                            reason="DAY0_SOURCE_CLOCK_ENSEMBLE_BUNDLE_INCOMPLETE",
                        )
                    else:
                        ready_city_dates.extend((name, target_date) for target_date in target_dates)
                        with _REFRESH_LOCK:
                            _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.pop(refresh_key, None)
                            _INCOMPLETE_RETRY_STREAK.pop(refresh_key, None)
                    continue
            expected_models = tuple(dict.fromkeys(str(model) for model in models))
            vector_models = tuple(dict.fromkeys(str(vector.model) for vector in vectors))
            missing_models = tuple(
                model for model in expected_models if model not in vector_models
            )
            retry_target_dates = (
                ensemble_target_dates if ensemble_incomplete else target_dates
            )
            retry_expected_models = (
                day0_source_clock_ensemble_member_models()
                if ensemble_incomplete
                else expected_models
            )
            retry_available_models = (
                ensemble_available_models if ensemble_incomplete else vector_models
            )
            retry_missing_models = (
                ensemble_missing_models if ensemble_incomplete else missing_models
            )
            retry_reason = (
                "DAY0_SOURCE_CLOCK_ENSEMBLE_BUNDLE_INCOMPLETE"
                if ensemble_incomplete
                else "DAY0_HOURLY_BUNDLE_INCOMPLETE"
            )
            if not vectors or not request_hash:
                if ensemble_incomplete:
                    mark_incomplete(
                        refresh_key=refresh_key,
                        quota_lane=quota_lane,
                        name=name,
                        target_dates=retry_target_dates,
                        expected_models=retry_expected_models,
                        available_models=retry_available_models,
                        missing_models=retry_missing_models,
                        reason=retry_reason,
                    )
                elif quota_lane in {"critical", "priority"}:
                    mark_incomplete(
                        refresh_key=refresh_key,
                        quota_lane=quota_lane,
                        name=name,
                        target_dates=target_dates,
                        expected_models=expected_models,
                        available_models=vector_models,
                        missing_models=missing_models or expected_models,
                        reason="DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE",
                    )
                else:
                    unavailable_bundles.append(
                        Day0HourlyBundleUnavailable(
                            city=name,
                            target_dates=target_dates,
                            expected_models=expected_models,
                            available_models=vector_models,
                            missing_models=missing_models or expected_models,
                            reason="DAY0_HOURLY_BUNDLE_FETCH_UNAVAILABLE",
                        )
                    )
                continue
            if missing_models:
                mark_incomplete(
                    refresh_key=refresh_key,
                    quota_lane=quota_lane,
                    name=name,
                    target_dates=retry_target_dates,
                    expected_models=retry_expected_models,
                    available_models=retry_available_models,
                    missing_models=retry_missing_models,
                    reason=retry_reason,
                )
                continue
            trailing_hwm_models = (
                _vectors_trailing_provider_hwm(vectors, required_hwm=required_hwm)
                if release_due
                else ()
            )
            if trailing_hwm_models:
                mark_incomplete(
                    refresh_key=refresh_key,
                    quota_lane=quota_lane,
                    name=name,
                    target_dates=retry_target_dates,
                    expected_models=retry_expected_models,
                    available_models=retry_available_models,
                    missing_models=(
                        retry_missing_models if ensemble_incomplete else trailing_hwm_models
                    ),
                    reason=(
                        retry_reason
                        if ensemble_incomplete
                        else "DAY0_PROVIDER_RUN_HWM_NOT_CAPTURED"
                    ),
                )
                continue

            strict_bundles: dict[str, tuple[datetime, list[Day0HourlyVector]]] = {}
            incomplete_target_dates: list[str] = []
            for target_date in target_dates:
                window_start = window_starts[target_date]
                selected = select_ready_day0_hourly_vectors(
                    vectors,
                    target_date=target_date,
                    now=materialization_time,
                    expected_models=expected_models,
                    require_expected=True,
                    max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                    remaining_window_start=window_start,
                    require_complete_remaining_window=True,
                )
                if window_start is None or not selected:
                    incomplete_target_dates.append(target_date)
                    continue
                strict_bundles[target_date] = (window_start, selected)

            persisted = 0
            persist_failed_dates: list[str] = []
            contended_target_dates: set[str] = set()
            for target_date, (_window_start, selected) in strict_bundles.items():
                try:
                    date_written = persist_day0_hourly_vectors(
                        selected,
                        target_date=target_date,
                        request_hash=request_hash,
                        lock_blocking=persist_lock_blocking,
                    )
                    persisted += date_written
                    if date_written != len(selected):
                        persist_failed_dates.append(target_date)
                        logger.warning(
                            "DAY0_HOURLY_BUNDLE_PERSIST_INCOMPLETE city=%s "
                            "target_date=%s selected=%d written=%d",
                            name,
                            target_date,
                            len(selected),
                            date_written,
                        )
                except Exception as exc:  # noqa: BLE001 - retain sibling date progress
                    persist_failed_dates.append(target_date)
                    if isinstance(exc, BlockingIOError):
                        contended_target_dates.add(target_date)
                    logger.warning(
                        "DAY0_HOURLY_BUNDLE_PERSIST_FAILED city=%s target_date=%s "
                        "exc=%s: %s",
                        name,
                        target_date,
                        type(exc).__name__,
                        exc,
                    )
            post_persist_materialization_time = _day0_utc_now()
            readback_failed_dates: list[str] = []
            for target_date, (window_start, _selected) in strict_bundles.items():
                try:
                    readback = read_freshest_day0_hourly_vectors(
                        city=name,
                        target_date=target_date,
                        now=post_persist_materialization_time,
                        expected_models=expected_models,
                        require_expected=True,
                        max_bundle_skew_minutes=DAY0_HOURLY_BUNDLE_MAX_SKEW_MINUTES,
                        remaining_window_start=window_start,
                        require_complete_remaining_window=True,
                    )
                except Exception as exc:  # noqa: BLE001 - retain sibling date progress
                    readback = []
                    logger.warning(
                        "DAY0_HOURLY_BUNDLE_READBACK_FAILED city=%s target_date=%s "
                        "exc=%s: %s",
                        name,
                        target_date,
                        type(exc).__name__,
                        exc,
                    )
                if not readback or (
                    target_date in persist_failed_dates
                    and not _day0_readback_bundle_is_current_or_newer(
                        _selected, readback
                    )
                ):
                    readback_failed_dates.append(target_date)
            written += persisted
            pending_target_dates = tuple(
                dict.fromkeys(
                    (
                        *incomplete_target_dates,
                        *readback_failed_dates,
                    )
                )
            )
            if not ensemble_incomplete:
                ready_city_dates.extend(
                    (name, target_date)
                    for target_date in strict_bundles
                    if target_date not in pending_target_dates
                )
            if pending_target_dates or ensemble_incomplete:
                if ensemble_incomplete:
                    pending_retry_target_dates = tuple(
                        dict.fromkeys((*ensemble_target_dates, *pending_target_dates))
                    )
                    pending_retry_expected_models = (
                        day0_source_clock_ensemble_member_models()
                        if not pending_target_dates
                        else tuple(
                            dict.fromkeys(
                                (
                                    *expected_models,
                                    *day0_source_clock_ensemble_member_models(),
                                )
                            )
                        )
                    )
                    pending_retry_available_models = (
                        ensemble_available_models
                        if not pending_target_dates
                        else tuple(dict.fromkeys((*vector_models, *ensemble_available_models)))
                    )
                    pending_retry_missing_models = tuple(
                        dict.fromkeys(
                            (
                                *ensemble_missing_models,
                                *(missing_models if pending_target_dates else ()),
                            )
                        )
                    )
                    pending_retry_reason = retry_reason
                else:
                    pending_retry_target_dates = pending_target_dates
                    pending_retry_expected_models = expected_models
                    pending_retry_available_models = vector_models
                    pending_retry_missing_models = ()
                    pending_retry_reason = (
                        "DAY0_HOURLY_BUNDLE_REMAINING_WINDOW_INCOMPLETE"
                        if incomplete_target_dates
                        else "DAY0_HOURLY_BUNDLE_PERSIST_READBACK_INCOMPLETE"
                    )
                mark_incomplete(
                    refresh_key=refresh_key,
                    quota_lane=quota_lane,
                    name=name,
                    target_dates=pending_retry_target_dates,
                    expected_models=pending_retry_expected_models,
                    available_models=pending_retry_available_models,
                    missing_models=pending_retry_missing_models,
                    reason=pending_retry_reason,
                )
                if (
                    not incomplete_target_dates
                    and not ensemble_incomplete
                    and set(pending_target_dates).issubset(contended_target_dates)
                ):
                    # Local lock contention keeps debt immediately retryable;
                    # it is not an incomplete-provider backoff condition.
                    with _REFRESH_LOCK:
                        _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC[refresh_key] = (
                            time.monotonic()
                        )
                continue
            with _REFRESH_LOCK:
                _INCOMPLETE_RETRY_NOT_BEFORE_MONOTONIC.pop(refresh_key, None)
                _INCOMPLETE_RETRY_STREAK.pop(refresh_key, None)
        except Exception as exc:  # noqa: BLE001 — one city must not kill the pass
            if isinstance(exc, BlockingIOError):
                with _REFRESH_LOCK:
                    _LAST_REFRESH_MONOTONIC.pop(locals().get("refresh_key", name), None)
            logger.warning(
                "DAY0_HOURLY_VECTORS_REFRESH_FAILED city=%s exc=%s: %s",
                name, type(exc).__name__, exc,
            )
    stats = Day0HourlyRefreshStats(
        vectors_written=written,
        cities_attempted=checked,
        cities_skipped_throttle=skipped_throttle,
        cities_skipped_quota=skipped_quota,
        incomplete_expected_bundles=incomplete_expected_bundles,
        unavailable_bundles=tuple(unavailable_bundles),
        priority_reserve_exhausted=priority_reserve_exhausted,
        budget_exhausted=budget_exhausted,
        ready_city_dates=tuple(dict.fromkeys(ready_city_dates)),
    )
    return stats if return_stats else stats.vectors_written
