# Created: 2026-06-10
# Last reused or audited: 2026-08-19
# Authority basis: operator staleness/cycle-physics directive 2026-06-10. Single source of
#   truth for (a) the bounded source-cycle staleness horizon shared by the materialization
#   fail-closed gate AND the live-admission belt-and-suspenders gate, and (b) the model-cycle
#   PHASE classification used as provenance for the 4-cycle download schedule. Evidence:
#   (computed_at - source_cycle_time) over
#   forecast_posteriors ran min 9.5h / avg 18.9h / max 28.8h in healthy operation (n=1168),
#   so a 30h bound admits all healthy operation with margin while rejecting multi-day laundering.
"""Replacement-forecast cycle policy: bounded staleness horizon + cycle-phase classification.

Two structural invariants live here so neither can be re-implemented divergently (Fitz #2:
encode the invariant in shared structure, not in N parallel checks):

  1. BOUNDED STALENESS — re-materializing the SAME persisted source cycle re-stamps
     ``computed_at`` and grants a fresh readiness TTL. Unbounded, this launders an
     arbitrarily-old cycle into "current" trading inputs forever. The bound caps
     ``computed_at - source_cycle_time`` (at materialization, fail-closed) AND
     ``decision_time - source_cycle_time`` (at live admission, belt-and-suspenders) at
     ``MAX_CYCLE_AGE`` hours. Expired-but-rematerializable: re-stamping the same cycle is
     allowed ONLY while still within the bound.

  2. LIVE SHAPE AUTHORITY — only a same-cycle target-specific ENS shape may
     authorize live probability; a bounded stale shape remains offline evidence.

  3. CYCLE PHASE — operator policy has promoted all four standard UTC cycles
     (00Z/06Z/12Z/18Z) to live-eligible replacement cycles. Phase remains provenance only;
     it must not downgrade 06Z/18Z rows or route them into an experiment-only state.
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone


UTC = timezone.utc


# H3 / operator directives 2026-06-10 + 2026-06-11 ("这个数字取决于该发布频率的tolerance
# 而不是瞎猜"): fail-closed staleness horizon, DERIVED from the measured publication
# rhythm — serve the last fetched data until the provider has had the chance to deliver
# TWO newer live-eligible cycles and we still hold nothing newer; only then is the old
# data "extremely stale" and refused.
#
#   bound = 2 x LIVE_REFRESH_INTERVAL + P50 publication lag
#         = 2 x 12h (replacement live refresh cadence)
#           + 6h   (MEASURED anchor publication lag, healthy: open-meteo bucket meta
#                   showed 06-10 06Z run completed +5.9h; see
#                   docs/evidence/anchor_channels/ + rule1_audits/2026-06-10)
#         = 30h
#
# Cross-checks: empirical healthy cycle age over forecast_posteriors ran min 9.5h /
# avg 18.9h / max 28.8h (n=1168) — all admitted; the 2026-06-10 single-cycle provider
# skip (12Z never published) kept the 00Z row served at 26.8h — correctly within bound;
# a SECOND consecutive miss crosses 30h and fails closed. The availability poll
# (replacement_cycle_availability) eliminated our own fetch delay (publication + <=15min),
# so publication lag is the only stochastic term left in the derivation.
# tests/data/test_cycle_staleness_derivation.py pins the formula to these inputs.
LIVE_CYCLE_REFRESH_INTERVAL_HOURS = 12.0
MEASURED_P50_PUBLICATION_LAG_HOURS = 6.0  # basis=MEASURED 2026-06-11 (see derivation above)
REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT = (
    2.0 * LIVE_CYCLE_REFRESH_INTERVAL_HOURS + MEASURED_P50_PUBLICATION_LAG_HOURS
)
_MAX_AGE_ENV = "ZEUS_REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS"

# Cycle-phase labels (provenance_json.cycle_phase). All standard 00Z/06Z/12Z/18Z
# cycles are live-eligible under current operator policy.
CYCLE_PHASE_SYNOPTIC = "synoptic"
CYCLE_PHASE_INTERMEDIATE = "experiment"
_SYNOPTIC_CYCLE_HOURS = frozenset({0, 6, 12, 18})
_INTERMEDIATE_CYCLE_HOURS = frozenset()

_STRICT_AWARE_ISO_RE = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)


# ---------------------------------------------------------------------------
# TRADEABLE-GRADE COVERAGE PREDICATE — SINGLE AUTHORITY (2026-06-12).
#
# Created: 2026-06-12
# Authority basis: /tmp/qlcb_coverage_fix_report.md. When the no-fusion path began
#   carrying a promoted legacy q_lcb instead of
#   NULL, the three mask-and-starve antibody sites that proxied "tradeable-grade coverage" as
#   `q_lcb_json IS NOT NULL` (live_materialization_queue / seed_discovery / current_target_plan)
#   would have WRONGLY counted a soft-anchor row as covered — re-introducing the exact mask-and-
#   starve disease they were built to prevent (an untradeable, no-current-capture row marking its
#   scope "done forever" and blocking its own fusion repair). The proxy was only ever valid because
#   NULL ⟺ non-fused; promoting the bound broke that biconditional.
#
# THE REAL PREDICATE: tradeable-grade coverage = a posterior whose q_lcb is the CERTIFIED fused-
#   center bootstrap bound. That is keyed by provenance_json.q_lcb_basis EXACTLY equal to the
#   bootstrap marker — the SAME predicate the live calibration-credential reader pins
#   (event_reactor_adapter._FUSED_BOOTSTRAP_QLCB_BASIS). Defining it ONCE here (the module both the
#   materializer and the readers already import, no cycle) makes all four sites share one definition.
TRADEABLE_GRADE_QLCB_BASIS = "fused_center_bootstrap_p05"
# v6 also requires current provider physical-product identity, including its
# default DEM correction. The existing coverage/seed loop regenerates old rows
# after ordinary producer cycles supply this revision's provider inputs.
CURRENT_EVIDENCE_SEMANTICS_REVISION = "ensemble_center_scenarios_v6"

# A bounded older ENS shape retains its raw absolute members and the full
# ENS/provider-center disagreement. This identity supersedes every anomaly-
# transport revision, which synthesized translated members from the fresh
# center and then reused those members as finite evidence.
STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION = (
    "stale_ensemble_absolute_disagreement_v2"
)

# Only a target-specific ENS shape from the carrier cycle is live probability
# authority.  Stale absolute-member shapes remain persisted for walk-forward
# diagnosis, but cross-clock disagreement is not a causal sample of the
# carrier-time settlement distribution and therefore cannot authorize entry,
# held-position statistical redecision, or coverage.
LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS = frozenset(
    {CURRENT_EVIDENCE_SEMANTICS_REVISION}
)

# Between-provider spread is live-authoritative only when its source clocks prove
# one simultaneous cohort. This marker is persisted and included in shape identity.
BETWEEN_COHORT_STATUS_SIMULTANEOUS_PROVEN = "SIMULTANEOUS_PROVEN"


def _current_evidence_shape(provenance: object) -> Mapping[str, object] | None:
    """Return the persisted source-clock shape when one is present."""

    payload = provenance
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, Mapping):
        return None
    fusion = payload.get("bayes_precision_fusion")
    if not isinstance(fusion, Mapping):
        return None
    shape = fusion.get("current_evidence_shape")
    return shape if isinstance(shape, Mapping) else None


def current_evidence_shape_source_cycle_time(
    provenance: object,
) -> datetime | None:
    """Return the selected ENS cycle only for the canonical aware ISO grammar.

    ``datetime.fromisoformat`` also accepts compact offsets such as ``+00`` and
    ``+0000`` while the SQLite coverage predicate intentionally does not.  The
    persisted probability certificate must have one language-independent
    grammar, otherwise Python serving can grant authority to a row that SQL
    correctly considers uncovered.
    """

    shape = _current_evidence_shape(provenance)
    if shape is None:
        return None
    raw = shape.get("source_cycle_time")
    if not isinstance(raw, str) or _STRICT_AWARE_ISO_RE.fullmatch(raw) is None:
        return None
    if raw[-1] != "Z" and (int(raw[-5:-3]) > 23 or int(raw[-2:]) > 59):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def current_evidence_shape_semantics_mismatch(provenance: object) -> bool:
    """Whether a shaped certificate was built under different probability law.

    Shape-less legacy fixtures and explicitly non-source-clock carriers remain
    outside this comparison. Once a current-evidence shape exists, its semantic
    revision is part of probability identity and must match exactly.
    """

    shape = _current_evidence_shape(provenance)
    if shape is None:
        return False
    try:
        stale_shape = float(shape.get("shape_lag_hours") or 0.0) > 0.0
    except (TypeError, ValueError):
        stale_shape = False
    stale_shape = stale_shape or shape.get("stale_shape_reused") is True
    expected = (
        STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
        if stale_shape
        else CURRENT_EVIDENCE_SEMANTICS_REVISION
    )
    return str(shape.get("semantics_revision") or "") != expected


def _anchor_station_ground_has_authority(geometry: Mapping[str, object], audit: object, materialized_at: object = None,
        *, target_scope: object = None, certificate_city: object = None, certificate_target_date: object = None) -> bool:
    """Replay this certificate's own canonical official ground entity."""
    try:
        from src.config import runtime_cities_by_name, runtime_station_geometry_for_city
        from src.data.station_ground_evidence import (
            read_frozen_station_ground_evidence, read_current_station_ground_evidence,
        )

        if materialized_at is None:
            return False  # Never trust a self-claimed replacement cutoff.

        anchor = geometry["providers"]["__anchor_ifs9__"]
        ground = anchor["source_geometry_proof"]["station_ground_proof"]
        city = runtime_cities_by_name().get(str(anchor["city"]))
        if city is None or not isinstance(ground, Mapping):
            return False
        if not isinstance(audit, Mapping) or not isinstance(audit.get("anchor_station_ground"), Mapping):
            return False
        frozen = read_frozen_station_ground_evidence(
            audit["anchor_station_ground"], decision_at=materialized_at,
        )
        if frozen is None:
            return False
        facts = ground.get("facts")
        if not isinstance(facts, Mapping):
            return False
        decision = datetime.fromisoformat(str(audit["decision_at"]).replace("Z", "+00:00"))
        if materialized_at is not None and decision != datetime.fromisoformat(str(materialized_at).replace("Z", "+00:00")):
            return False
        if decision.tzinfo is None:
            return False
        current = read_current_station_ground_evidence(
            frozen["forecast_db"], city=str(anchor["city"]), decision_at=materialized_at,
        )
        if current is None or current["facts"] != frozen["facts"]:
            return False
        from src.config import OSCAR_WMD_SOURCE_KIND
        if isinstance(target_scope,Mapping):
            from src.data.replacement_current_value_serving import station_ground_target_coverage_for_city
            if certificate_city != anchor["city"] or not certificate_target_date:
                return False
            coverage = station_ground_target_coverage_for_city(current,city=str(certificate_city),
                target_date=certificate_target_date,decision_at=materialized_at)
            if (target_scope["city"] != certificate_city
                or str(target_scope["target_local_date"]) != str(certificate_target_date)
                or target_scope["timezone_name"] != city.timezone
                or datetime.fromisoformat(str(target_scope["local_day_start_utc"]).replace("Z","+00:00")) != datetime.fromisoformat(coverage["target_start_utc"])
                or datetime.fromisoformat(str(target_scope["local_day_end_utc"]).replace("Z","+00:00")) != datetime.fromisoformat(coverage["target_end_utc"])
                or coverage["status"] != "VERIFIED"):
                return False
            claimed = audit.get("anchor_station_ground_target_coverage")
            if (frozen["source_kind"] == OSCAR_WMD_SOURCE_KIND or claimed is not None) and claimed != coverage:
                return False
        elif frozen["source_kind"] == OSCAR_WMD_SOURCE_KIND:
            return False
        station = runtime_station_geometry_for_city(city)
        return (
            station.get("validity_reason") is None
            and ground.get("revision") == "station_ground_roles_v1"
            and ground.get("status") == "VERIFIED"
            and facts == frozen["facts"]
            and anchor["station_id"] == station["station_id"]
            and float(anchor["station_elevation_m"]) == float(facts["elevation_m"])
            and float(anchor["station_lat"]) == float(station["lat"])
            and float(anchor["station_lon"]) == float(station["lon"])
        )
    except (KeyError, TypeError, ValueError, OSError):
        return False


def anchor_precision_metadata_identity(metadata: object) -> dict[str, object]:
    """Compare the complete frozen proof across JSON/dataclass date adapters."""
    from dataclasses import asdict
    from datetime import date
    values = asdict(metadata)
    target = values["target_local_date"]
    if isinstance(target, date) and not isinstance(target, datetime):
        values["target_local_date"] = target.isoformat()
    elif isinstance(target, str):
        values["target_local_date"] = date.fromisoformat(target).isoformat()
    else:
        raise ValueError("precision target must be a local date")
    for field in ("local_day_start_utc", "local_day_end_utc"):
        value = values[field]
        if isinstance(value, datetime):
            stamp = value
        elif isinstance(value, str):
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ValueError("precision local-day window must be datetime or ISO text")
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("precision local-day window must be aware")
        values[field] = stamp.astimezone(UTC).isoformat()
    return values


def anchor_local_proof_dependency(evidence: object, *, forecast_db: object) -> dict[str, object]:
    """Immutable local possession dependency; never forecast issue or physical geometry."""
    return {"original_artifact_id": evidence.original_body_artifact["artifact_id"],
        "artifact_id": evidence.proof_artifact_id, "sha256": evidence.proof_sha256,
        "owned_body": dict(evidence.owned_body),
        "local_possessed_at": evidence.local_possessed_at.isoformat(),
        "recorded_at": evidence.recorded_at.isoformat(), "forecast_db": str(forecast_db)}


def _anchor_ifs9_response_has_authority(geometry: Mapping[str, object], audit: object, *, materialized_at: object,
        city: object = None, target_date: object = None, metric: object = None, expected_anchor_artifact_id: object = None,
        anchor_id: object = None, request_anchor_artifact_id: object = None, forecast_db: object = None) -> bool:
    """The soft anchor replays its own canonical body/cell, including anchor-only IFS roles.

    SCOPE: one certificate's anchor. DRAIN: its ordinary source producer freezes
    owned static bytes before a new seed is computed. RESET: complete own body,
    request and static proof possessed by that new cut; no old certificate relabel.
    """
    try:
        from pathlib import Path
        from datetime import date
        from src.state.db import _connect_read_only
        from src.data.replacement_current_value_serving import _ARTIFACT_IDENTITY_JSON_SQL
        from src.data.openmeteo_ecmwf_ifs9_anchor import (
            SOURCE_ID, PRODUCT_ID, HIGH_DATA_VERSION, LOW_DATA_VERSION, SINGLE_RUNS_FORECAST_URL,
            extract_openmeteo_ecmwf_ifs9_localday_anchor,
        )
        from src.data.openmeteo_ecmwf_ifs9_precision_guard import (
            OpenMeteoIfs9PrecisionMetadata, evaluate_openmeteo_ecmwf_ifs9_precision_guard,
        )
        if materialized_at is None or not isinstance(audit, Mapping):
            return False
        if not city or not target_date or metric not in ("high","low"):
            return False
        ground_evidence = audit.get("anchor_station_ground")
        if not isinstance(ground_evidence, Mapping):
            return False
        artifact = audit["anchor_raw_artifact"]
        if not isinstance(artifact, Mapping):
            return False
        if not forecast_db:
            return False
        canonical_db = Path(str(forecast_db)).resolve(strict=True)
        if (Path(str(artifact["forecast_db"])).resolve(strict=True) != canonical_db
            or Path(str(ground_evidence["forecast_db"])).resolve(strict=True) != canonical_db):
            return False
        metadata = OpenMeteoIfs9PrecisionMetadata(**audit["anchor_precision_metadata"])
        anchor = geometry["providers"]["__anchor_ifs9__"]
        conn = _connect_read_only(canonical_db)
        local_proof = None
        try:
            row = conn.execute(f"SELECT {_ARTIFACT_IDENTITY_JSON_SQL} FROM raw_forecast_artifacts a WHERE a.artifact_id=?",
                (artifact["artifact_id"],)).fetchone()
            if anchor_id is not None:
                relation = conn.execute("SELECT city,target_date,temperature_metric,artifact_id FROM deterministic_forecast_anchors WHERE anchor_id=?",
                    (anchor_id,)).fetchone()
                if relation is None or tuple(relation) != (str(city),str(target_date),str(metric),artifact["artifact_id"]):
                    return False
            elif request_anchor_artifact_id != artifact["artifact_id"]:
                return False
            claimed_local_proof = audit.get("anchor_local_proof")
            if claimed_local_proof is not None:
                from src.data.raw_forecast_artifact_manifest import read_anchor_local_proof
                local_proof = read_anchor_local_proof(conn, artifact["artifact_id"],
                    city=str(city), target_date=str(target_date), metric=str(metric), decision_at=materialized_at)
                if (local_proof is None
                    or claimed_local_proof != anchor_local_proof_dependency(local_proof, forecast_db=canonical_db)
                    or anchor_precision_metadata_identity(OpenMeteoIfs9PrecisionMetadata(**local_proof.precision_metadata))
                        != anchor_precision_metadata_identity(metadata)):
                    return False
        finally:
            conn.close()
        if row is None or json.loads(row[0]) != {key:value for key,value in artifact.items() if key != "forecast_db"}:
            return False
        if (expected_anchor_artifact_id != artifact["artifact_id"] or isinstance(expected_anchor_artifact_id,bool)
            or metadata.city != str(city) or str(metadata.target_local_date) != str(target_date)):
            return False
        stamps = []
        for key in ("source_cycle_time", "source_available_at", "captured_at", "recorded_at"):
            stamp = datetime.fromisoformat(str(artifact[key]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                if key != "recorded_at":
                    return False
                stamp = stamp.replace(tzinfo=UTC)
            stamps.append(stamp.astimezone(UTC))
        decision = datetime.fromisoformat(str(materialized_at).replace("Z", "+00:00"))
        if decision.tzinfo is None or not stamps[0] <= stamps[1] <= stamps[2] <= stamps[3] <= decision:
            return False
        if local_proof is not None and decision >= replacement_readiness_expires_at(stamps[0]):
            return False
        product = json.loads(artifact["metadata"])
        if (product["metric"] != metric or artifact["source_id"] != SOURCE_ID or artifact["product_id"] != PRODUCT_ID
            or artifact["data_version"] != (HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION)
            or artifact["request_url"] != SINGLE_RUNS_FORECAST_URL
            or product["city"] != metadata.city or product["target_date"] != str(metadata.target_local_date)):
            return False
        params = json.loads(artifact["request_params_json"])
        if (params["models"] != "ecmwf_ifs" or params["hourly"] != "temperature_2m"
            or params["temperature_unit"] != "celsius" or params["cell_selection"] != "land" or "elevation" in params
            or float(params["latitude"]) != metadata.requested_lat or float(params["longitude"]) != metadata.requested_lon
            or params["timezone"] != metadata.timezone_name
            or datetime.fromisoformat(str(params["run"])).replace(tzinfo=UTC) != stamps[0]):
            return False
        path = Path(str(local_proof.owned_body["path"] if local_proof is not None else artifact["artifact_path"]))
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 8*1024*1024:
            return False
        body = path.read_bytes()
        if len(body) != artifact["byte_size"] or hashlib.sha256(body).hexdigest() != artifact["sha256"]:
            return False
        payload = json.loads(body)
        if payload["hourly_units"]["temperature_2m"] != "°C":
            return False
        extracted = extract_openmeteo_ecmwf_ifs9_localday_anchor(payload,
            city_timezone=metadata.timezone_name, target_local_date=date.fromisoformat(str(metadata.target_local_date)),
            source_cycle_time=stamps[0], require_full_localday=True)
        if extracted.sample_count < 23:
            return False
        if not isinstance(metadata.source_geometry_proof, Mapping):
            return False
        stable_proof = {key:value for key,value in metadata.source_geometry_proof.items()
            if key not in ("station_registry_sha256", "static_hsurf_sha256", "static_asset_audit")
            and not any(clock in key for clock in ("fetched", "captured", "payload_sha", "manifest_sha", "recorded", "cycle", "available"))}
        ground = stable_proof.get("station_ground_proof")
        if isinstance(ground, Mapping):
            stable_proof["station_ground_proof"] = {key:ground[key] for key in ("revision", "status", "reason", "facts") if key in ground}
        if anchor["source_geometry_proof"] != stable_proof:
            return False
        fields = ("city", "station_id", "station_lat", "station_lon", "requested_lat", "requested_lon", "nearest_grid_lat", "nearest_grid_lon",
                  "grid_elevation_m", "station_elevation_m", "timezone_name", "native_grid", "delivery_grid_resolution", "temperature_unit")
        if any(anchor[key] != getattr(metadata, key) for key in fields):
            return False
        return evaluate_openmeteo_ecmwf_ifs9_precision_guard(
            metadata, raw_payload_bytes=body, decision_at=decision,
            station_ground_evidence=ground_evidence,
        ).passable_for_live_materialization
    except (KeyError, IndexError, TypeError, ValueError, OSError, sqlite3.Error):
        return False


DAY0_FAST_RESIDUAL_COVERAGE_REVISION = "target_bound_fast_residual_v1"


def declares_fast_residual_carrier(provenance: object) -> bool:
    """Recognize the source role, including incomplete FAST declarations."""
    from src.events.day0_authority import DAY0_WU_FAST_RESIDUAL_SOURCE

    try:
        payload = json.loads(provenance) if isinstance(provenance, str) else provenance
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, Mapping):
        return False
    if payload.get("q_shape") == "fused_day0_fast_residual_likelihood":
        return True
    return any(isinstance(payload.get(key), Mapping) and (
        payload[key].get("source") == DAY0_WU_FAST_RESIDUAL_SOURCE
        or "fast_residual_likelihood" in payload[key]
    ) for key in ("day0_provisional_observation", "day0_conditioning"))


def fast_residual_carrier_authority_reason(
    provenance: object, *, city: object, target_date: object, metric: object,
    materialized_at: object,
) -> str | None:
    """Use the public reader's complete FAST replay with independent row scope."""
    if not declares_fast_residual_carrier(provenance):
        return None
    # SCOPE: this declared FAST city/date/metric posterior, including partial
    # claims. DRAIN: ordinary target-aware materialization; RESET: its new
    # source/channel/content proof reproduces at the independent computed cut.
    try:
        payload = json.loads(provenance) if isinstance(provenance, str) else provenance
        if not isinstance(payload.get("day0_provisional_observation"), Mapping):
            raise ValueError("FAST observation missing")
        if not isinstance(city, str) or not city or not target_date or metric not in ("high", "low"):
            raise ValueError("independent FAST scope missing")
        if isinstance(materialized_at, datetime):
            cut = materialized_at
        elif isinstance(materialized_at, str) and _STRICT_AWARE_ISO_RE.fullmatch(materialized_at):
            cut = datetime.fromisoformat(materialized_at.replace("Z", "+00:00"))
        else:
            raise ValueError("independent FAST cut missing")
        if cut.tzinfo is None or cut.utcoffset() is None:
            raise ValueError("independent FAST cut naive")
        # Runtime import: the reader itself imports this policy module.
        from src.data.replacement_forecast_bundle_reader import _wu_fast_pinned_carrier_reason

        return _wu_fast_pinned_carrier_reason(payload, city=city, target_date=target_date,
            metric=metric, decision_time=cut.astimezone(UTC))
    except (KeyError, TypeError, ValueError, OverflowError):
        return "REPLACEMENT_PINNED_DAY0_FAST_RESIDUAL_CARRIER_INVALID"


def fast_residual_coverage_dependency(*, city: str, target_date: str) -> dict[str, str]:
    """Stable route and target-owned product channel; not probability authority."""
    from src.config import cities_by_name, settlement_source_type_for_city

    city_obj = cities_by_name[city]
    station = str(city_obj.wu_station).strip().upper()
    source_type = settlement_source_type_for_city(city_obj, target_date)
    if not station or source_type not in ("noaa", "wu_icao"):
        raise ValueError("FAST target has no owned product channel")
    return {"revision": DAY0_FAST_RESIDUAL_COVERAGE_REVISION,
        "settlement_channel": "wu_icao_history" if source_type == "wu_icao"
        else f"noaa_wrh_{station.lower()}"}


def _current_evidence_shape_has_probability_authority(
    provenance: object, *, materialized_at: object = None, city: object = None, target_date: object = None,
    metric: object = None, anchor_id: object = None, request_anchor_artifact_id: object = None,
    forecast_db: object = None,
) -> bool:
    """Validate same-cycle target-specific ENS probability authority."""

    from src.events.day0_authority import current_day0_remaining_center_policy_has_authority

    if not current_day0_remaining_center_policy_has_authority(provenance):
        return False
    from src.events.day0_authority import current_day0_probability_mixture_policy_has_authority

    if not current_day0_probability_mixture_policy_has_authority(provenance):
        return False
    if fast_residual_carrier_authority_reason(provenance, city=city, target_date=target_date,
        metric=metric, materialized_at=materialized_at) is not None:
        return False
    shape = _current_evidence_shape(provenance)
    if shape is None:
        return False
    if not city or not target_date or metric not in ("high","low") or not forecast_db:
        return False
    if current_evidence_shape_source_cycle_time(provenance) is None:
        return False
    shape_lag_hours = shape.get("shape_lag_hours")
    if (
        isinstance(shape_lag_hours, bool)
        or not isinstance(shape_lag_hours, (int, float))
        or not math.isfinite(float(shape_lag_hours))
    ):
        return False
    lag = float(shape_lag_hours)
    if lag != 0.0:
        return False
    stale_shape_reused = shape.get("stale_shape_reused")
    if stale_shape_reused is not None and not isinstance(
        stale_shape_reused, bool
    ):
        return False
    if stale_shape_reused not in (None, False):
        return False
    from src.contracts.ensemble_snapshot_provenance import GRID_SURFACE_EVIDENCE_REVISION

    proof_hash = shape.get("grid_surface_evidence_identity_hash")
    if (
        shape.get("grid_surface_evidence_revision") != GRID_SURFACE_EVIDENCE_REVISION
        or not isinstance(proof_hash, str)
        or len(proof_hash) != 64
        or any(char not in "0123456789abcdef" for char in proof_hash)
    ):
        return False
    geometry = shape.get("provider_geometry_evidence")
    if not isinstance(geometry, Mapping) or geometry.get("revision") != "openmeteo_current_provider_geometry_v1" or not geometry.get("providers"):
        return False
    geometry_hash = hashlib.sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    if shape.get("provider_geometry_identity_hash") != geometry_hash:
        return False
    try:
        from pathlib import Path
        audit = shape["provider_geometry_audit"]
        namespace = Path(str(forecast_db)).resolve(strict=True)
        if any(Path(str(audit[key]["forecast_db"])).resolve(strict=True) != namespace
            for key in ("anchor_raw_artifact", "anchor_station_ground")):
            return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    try:
        payload = json.loads(provenance) if isinstance(provenance, str) else provenance
        precision = payload["openmeteo_precision_guard"]["metadata"]
    except (KeyError, TypeError, ValueError):
        return False
    if not _anchor_station_ground_has_authority(geometry, shape.get("provider_geometry_audit"), materialized_at,
        target_scope=precision,certificate_city=city,certificate_target_date=target_date):
        return False
    try:
        payload = json.loads(provenance) if isinstance(provenance,str) else provenance
        expected_anchor_artifact_id = payload["openmeteo_anchor_artifact_id"]
    except (KeyError,TypeError,ValueError):
        return False
    if not _anchor_ifs9_response_has_authority(geometry, shape.get("provider_geometry_audit"), materialized_at=materialized_at,
        city=city,target_date=target_date,metric=metric,expected_anchor_artifact_id=expected_anchor_artifact_id,
        anchor_id=anchor_id,request_anchor_artifact_id=request_anchor_artifact_id,forecast_db=forecast_db):
        return False
    try:
        payload = json.loads(provenance) if isinstance(provenance, str) else provenance
        serving = payload["bayes_precision_fusion"]["current_value_serving"]
        used = payload["bayes_precision_fusion"]["used_models"]
        if "ecmwf_ifs" in used:
            role = shape["provider_geometry_audit"].get("anchor_ifs9_role")
            if role != ("raw_ifs9_and_anchor" if "ecmwf_ifs" in serving else "anchor_only"):
                return False
        if not isinstance(used, (list, tuple)) or not used or any(
            model not in geometry["providers"] and (model != "ecmwf_ifs" or model in serving)
            for model in used
        ):
            return False
        from src.data.replacement_current_value_serving import _is_station_model, frozen_ifs9_response_has_authority
        from src.data.openmeteo_model_surface import validate_model_surface_witness, model_surface_stable_projection
        for model, physical_geometry in geometry["providers"].items():
            if model == "__anchor_ifs9__" or _is_station_model(str(model)):
                continue
            if model == "ecmwf_ifs":
                if not frozen_ifs9_response_has_authority(serving[model]["physical_response"], physical_geometry, decision_at=materialized_at):
                    return False
                continue
            physical = serving[model]["physical_response"]
            witness = physical["model_surface_witness"]
            if (physical_geometry.get("model_surface_geometry") != model_surface_stable_projection(witness)
                or validate_model_surface_witness(witness, model=str(model),
                    selected_latitude=float(physical_geometry["selected_latitude"]),
                    selected_longitude=float(physical_geometry["selected_longitude"]),
                    body_captured_at=str(physical["proof_captured_at"]), decision_at=materialized_at) is not None):
                return False
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return (
        str(shape.get("semantics_revision") or "")
        in LIVE_CURRENT_EVIDENCE_SEMANTICS_REVISIONS
        and shape.get("translation_applied") is False
        and not current_evidence_shape_semantics_mismatch(provenance)
    )


def current_evidence_shape_has_entry_authority(provenance: object, *, materialized_at: object = None,
        city: object = None,target_date: object = None,metric: object = None,anchor_id: object = None,
        request_anchor_artifact_id: object = None, forecast_db: object = None) -> bool:
    """Whether current evidence authorizes a new entry."""

    # FAIL-CLOSED GATE CONTRACT
    # SCOPE: new-entry authority for this one city/date/metric family.
    # DRAIN: the normal target-specific materializer replaces malformed,
    # translated, expired, or semantically mismatched shape provenance.
    # RESET: a coherent same-cycle target-specific raw-member shape restores
    # the authority ratified in replacement_final_form section 1d.
    return _current_evidence_shape_has_probability_authority(provenance, materialized_at=materialized_at,
        city=city,target_date=target_date,metric=metric,anchor_id=anchor_id,request_anchor_artifact_id=request_anchor_artifact_id,
        forecast_db=forecast_db)


def current_evidence_shape_has_held_authority(provenance: object, *, materialized_at: object = None,
        city: object = None,target_date: object = None,metric: object = None,anchor_id: object = None,
        request_anchor_artifact_id: object = None, forecast_db: object = None) -> bool:
    """Whether a shape can support reduce-only held-position redecision.

    Stale ENS rows remain offline evidence only.  A held position must be
    re-decided from the same current probability law as a new entry; absence of
    a same-cycle shape is DATA_DEGRADED, not permission to use cross-clock
    disagreement as a probability distribution.
    """

    return _current_evidence_shape_has_probability_authority(provenance, materialized_at=materialized_at,
        city=city,target_date=target_date,metric=metric,anchor_id=anchor_id,request_anchor_artifact_id=request_anchor_artifact_id,
        forecast_db=forecast_db)


def tradeable_grade_coverage_sql(
    *,
    posterior_columns,
    decision_time: datetime,
    alias: str = "",
) -> str:
    """SQL fragment selecting ONLY tradeable-grade (certified-bootstrap-bounded) posteriors.

    Replaces the broken ``AND <alias>q_lcb_json IS NOT NULL`` proxy at the mask-and-starve
    antibody sites. A soft-anchor Wilson-bounded row (non-NULL q_lcb but basis != bootstrap) is
    NOT tradeable-grade, so it does NOT count as coverage and correctly re-seeds for fusion repair.

    Schema-conditional and fail-closed: when ``forecast_posteriors`` lacks
    ``provenance_json``, no row can prove current shape authority. ``alias`` is
    the table alias with a trailing dot already applied by the caller's existing
    convention (for example, ``"p."``).
    """
    from src.data.day0_hourly_vectors import (
        DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER,
        DAY0_REMAINING_CARRIER_OPERATOR_V2, DAY0_REMAINING_CARRIER_OPERATOR_V3,
    )
    from src.config import day0_resolver_terminal_residual_enabled

    cols = set(posterior_columns)
    fragments: list[str] = []
    if "q_lcb_json" in cols:
        fragments.append(f"AND {alias}q_lcb_json IS NOT NULL")
    if "q_ucb_json" in cols:
        fragments.append(f"AND {alias}q_ucb_json IS NOT NULL")
    if "provenance_json" not in cols:
        # FAIL-CLOSED GATE CONTRACT
        # SCOPE: coverage for the queried city/date/metric family only.
        # DRAIN: the canonical forecast schema migration adds provenance_json;
        # normal seed/materialization then writes a current shape certificate.
        # RESET: the next coverage query with that column present evaluates the
        # ordinary shape predicate; held-position belief reads are independent.
        fragments.append("AND 0 = 1")
        return "\n              ".join(fragments)
    provenance_expr = (
        f"(CASE WHEN json_valid({alias}provenance_json) "
        f"THEN {alias}provenance_json ELSE '{{}}' END)"
    )
    fragments.append(
        f"AND json_extract({provenance_expr}, '$.q_lcb_basis') = "
        f"'{TRADEABLE_GRADE_QLCB_BASIS}'"
    )
    shape_path = "$.bayes_precision_fusion.current_evidence_shape"
    if decision_time.tzinfo is None or decision_time.utcoffset() is None:
        raise ValueError("coverage decision_time must be timezone-aware")
    decision_iso = decision_time.astimezone(UTC).isoformat().replace("'", "''")
    lag_type = f"json_type({provenance_expr}, '{shape_path}.shape_lag_hours')"
    lag_value = (
        f"CAST(json_extract({provenance_expr}, "
        f"'{shape_path}.shape_lag_hours') AS REAL)"
    )
    stale_type = (
        f"json_type({provenance_expr}, '{shape_path}.stale_shape_reused')"
    )
    translation_type = (
        f"json_type({provenance_expr}, '{shape_path}.translation_applied')"
    )
    revision_value = (
        f"json_extract({provenance_expr}, '{shape_path}.semantics_revision')"
    )
    from src.contracts.ensemble_snapshot_provenance import GRID_SURFACE_EVIDENCE_REVISION

    surface_revision_type = f"json_type({provenance_expr}, '{shape_path}.grid_surface_evidence_revision')"
    surface_revision_value = f"json_extract({provenance_expr}, '{shape_path}.grid_surface_evidence_revision')"
    surface_hash_type = f"json_type({provenance_expr}, '{shape_path}.grid_surface_evidence_identity_hash')"
    surface_hash_value = f"json_extract({provenance_expr}, '{shape_path}.grid_surface_evidence_identity_hash')"
    carrier_identity_type = (
        f"json_type({provenance_expr}, '$.day0_remaining_carrier_content_identity')"
    )
    carrier_identity_value = (
        f"json_extract({provenance_expr}, '$.day0_remaining_carrier_content_identity')"
    )
    carrier_operator_type = (
        f"json_type({provenance_expr}, '$.day0_remaining_carrier_operator')"
    )
    carrier_operator_value = (
        f"json_extract({provenance_expr}, '$.day0_remaining_carrier_operator')"
    )
    carrier_shape_type = f"json_type({provenance_expr}, '$.q_shape')"
    carrier_shape_value = f"json_extract({provenance_expr}, '$.q_shape')"
    provider_type = f"json_type({provenance_expr}, '$.day0_remaining_carrier_station_extreme_providers')"
    provider_value = f"json_extract({provenance_expr}, '$.day0_remaining_carrier_station_extreme_providers')"
    final_centers_type = f"json_type({provenance_expr}, '$.day0_remaining_carrier_final_extremes_c')"
    final_centers_value = f"json_extract({provenance_expr}, '$.day0_remaining_carrier_final_extremes_c')"
    ens_cycle_value = (
        f"json_extract({provenance_expr}, '{shape_path}.source_cycle_time')"
    )
    ens_cycle_type = (
        f"json_type({provenance_expr}, '{shape_path}.source_cycle_time')"
    )
    ens_cycle_has_timezone = (
        f"(substr({ens_cycle_value}, -1, 1) = 'Z' OR ("
        f"length({ens_cycle_value}) >= 6 AND "
        f"substr({ens_cycle_value}, -6, 1) IN ('+', '-') AND "
        f"substr({ens_cycle_value}, -3, 1) = ':'))"
    )
    max_lag = replacement_source_cycle_max_age_hours()
    # Only a same-cycle raw ENS shape is live probability authority.  The
    # broader source-age bound still proves the selected cycle is causal; it
    # does not legalize cross-cycle shape reuse.
    fragments.append(
        "AND ("
        f"{translation_type} = 'false' AND "
        f"{lag_type} IN ('integer', 'real') AND "
        f"{lag_value} = 0.0 AND "
        f"{ens_cycle_type} = 'text' AND {ens_cycle_has_timezone} AND "
        f"julianday({ens_cycle_value}) IS NOT NULL AND "
        f"(julianday('{decision_iso}') - julianday({ens_cycle_value})) * 24.0 "
        f"BETWEEN 0.0 AND {max_lag!r} AND "
        f"({stale_type} IS NULL OR {stale_type} = 'false') AND "
        f"{surface_revision_type} = 'text' AND "
        f"{surface_revision_value} = '{GRID_SURFACE_EVIDENCE_REVISION}' AND "
        f"{surface_hash_type} = 'text' AND "
        f"length({surface_hash_value}) = 64 AND "
        f"{surface_hash_value} NOT GLOB '*[^0-9a-f]*' AND "
        f"json_extract({provenance_expr}, '{shape_path}.provider_geometry_evidence.revision') = 'openmeteo_current_provider_geometry_v1' AND "
        f"json_type({provenance_expr}, '{shape_path}.provider_geometry_evidence.providers') = 'object' AND "
        f"length(json_extract({provenance_expr}, '{shape_path}.provider_geometry_identity_hash')) = 64 AND "
        f"{revision_value} = "
        f"'{CURRENT_EVIDENCE_SEMANTICS_REVISION}')"
    )
    # SCOPE: the exact city/date/metric family represented by this posterior.
    # DRAIN: the existing seed/materialization loop rematerializes rows whose
    # carrier pair is V1, unknown, partial, or absent on a shared shape. RESET: both non-empty identity
    # fields are present and the operator is the current V2 contract. Ordinary
    # replacement rows have neither declaration and remain covered as before.
    fragments.append(
        "AND (("
        f"{carrier_identity_type} IS NULL AND {carrier_operator_type} IS NULL AND "
        f"COALESCE(json_extract({provenance_expr}, '$.q_shape'), '') "
        "NOT IN ('day0_remaining_shared_carrier_v1', 'day0_remaining_shared_carrier_v2', 'day0_remaining_shared_carrier_v3')"
        ") OR ("
        f"{carrier_identity_type} = 'text' AND "
        f"length(trim(COALESCE({carrier_identity_value}, ''))) > 0 AND "
        f"{carrier_operator_type} = 'text' AND "
        f"(({carrier_operator_value} = '{DAY0_REMAINING_CARRIER_OPERATOR_V2}' AND "
        f"({carrier_shape_type} IS NULL OR {carrier_shape_type} = 'null' OR {carrier_shape_value} NOT IN ('day0_remaining_shared_carrier_v1', 'day0_remaining_shared_carrier_v2', 'day0_remaining_shared_carrier_v3') OR {carrier_shape_value} = 'day0_remaining_shared_carrier_v2') AND "
        f"({provider_type} IS NULL OR {provider_type} = 'null' OR ({provider_type} = 'array' AND "
        "json_array_length(" + provenance_expr + ", '$.day0_remaining_carrier_station_extreme_providers') = 0)) AND "
        f"({final_centers_type} IS NULL OR {final_centers_type} = 'null' OR ({final_centers_type} = 'array' AND "
        "json_array_length(" + provenance_expr + ", '$.day0_remaining_carrier_final_extremes_c') = 0))) OR ("
        f"{carrier_operator_value} = '{DAY0_REMAINING_CARRIER_OPERATOR_V3}' AND "
        f"({carrier_shape_type} IS NULL OR {carrier_shape_type} = 'null' OR {carrier_shape_value} NOT IN ('day0_remaining_shared_carrier_v1', 'day0_remaining_shared_carrier_v2', 'day0_remaining_shared_carrier_v3') OR {carrier_shape_value} = 'day0_remaining_shared_carrier_v3') AND "
        f"{provider_type} = 'array' AND json_array_length(" + provenance_expr + ", '$.day0_remaining_carrier_station_extreme_providers') > 0 AND "
        f"{final_centers_type} = 'array' AND "
        "json_array_length(" + provenance_expr + ", '$.day0_remaining_carrier_final_extremes_c') = "
        "json_array_length(" + provenance_expr + ", '$.day0_remaining_carrier_station_extreme_providers') AND "
        f"NOT EXISTS (SELECT 1 FROM json_each(CASE WHEN {provider_type} = 'array' THEN {provider_value} ELSE '[]' END) AS provider "
        f"LEFT JOIN json_each(CASE WHEN {final_centers_type} = 'array' THEN {final_centers_value} ELSE '[]' END) AS final_center "
        "ON final_center.key = provider.key "
        "WHERE provider.type <> 'object' "
        "OR COALESCE(CASE WHEN provider.type = 'object' THEN json_type(provider.value, '$.forecast_value_c') ELSE '' END, '') NOT IN ('integer', 'real') "
        "OR ABS(CAST(CASE WHEN provider.type = 'object' THEN json_extract(provider.value, '$.forecast_value_c') ELSE 0 END AS REAL)) > 1.7976931348623157e308 "
        "OR COALESCE(final_center.type, '') NOT IN ('integer', 'real') "
        "OR ABS(CAST(final_center.value AS REAL)) > 1.7976931348623157e308 "
        "OR CAST(CASE WHEN provider.type = 'object' THEN json_extract(provider.value, '$.forecast_value_c') ELSE 0 END AS REAL) "
        "!= CAST(final_center.value AS REAL)))"
        + (
            # The resolver-graded carrier is admitted only while its switch is
            # on; with it off the fragment is byte-identical to before.
            f") OR ({carrier_operator_value} = '{DAY0_REMAINING_CARRIER_OPERATOR_RESOLVER}' AND "
            f"{carrier_shape_value} = 'day0_remaining_shared_carrier_resolver_v1' AND "
            f"json_type({provenance_expr}, '$.day0_resolver_terminal_input') = 'object'"
            if day0_resolver_terminal_residual_enabled()
            else ""
        )
        + ")))"
    )
    from src.events.day0_authority import DAY0_REMAINING_CENTER_POLICY

    policy_type = f"json_type({provenance_expr}, '$.day0_remaining_center_policy')"
    policy_value = f"json_extract({provenance_expr}, '$.day0_remaining_center_policy')"
    bias_type = f"json_type({provenance_expr}, '$.day0_remaining_center_bias_c')"
    bias_value = f"json_extract({provenance_expr}, '$.day0_remaining_center_bias_c')"
    # Same declaration and strict numeric-zero law as the Python authority gate.
    fragments.append(
        "AND (("
        f"{carrier_identity_type} IS NULL AND {carrier_operator_type} IS NULL AND "
        f"{policy_type} IS NULL AND {bias_type} IS NULL AND "
        f"json_type({provenance_expr}, '$.day0_remaining_bias_status') IS NULL AND "
        f"json_type({provenance_expr}, '$.day0_remaining_bias_artifact') IS NULL AND "
        f"COALESCE({carrier_shape_value}, '') NOT IN ("
        "'day0_remaining_shared_carrier_v1', 'day0_remaining_shared_carrier_v2', "
        "'day0_remaining_shared_carrier_v3', 'day0_remaining_shared_carrier_resolver_v1', "
        "'fused_day0_fast_residual_likelihood')) OR ("
        f"{policy_type} = 'text' AND {policy_value} = '{DAY0_REMAINING_CENTER_POLICY}' AND "
        f"{bias_type} IN ('integer', 'real') AND {bias_value} = 0))"
    )
    from src.events.day0_authority import (
        DAY0_LEGACY_DIURNAL_FIELDS, DAY0_PROBABILITY_MIXTURE_POLICY,
    )

    mixture_policy_type = f"json_type({provenance_expr}, '$.day0_probability_mixture_policy')"
    mixture_policy_value = f"json_extract({provenance_expr}, '$.day0_probability_mixture_policy')"
    fragments.extend(
        f"AND json_type({provenance_expr}, '$.{field}') IS NULL"
        for field in DAY0_LEGACY_DIURNAL_FIELDS
    )
    fragments.append(
        f"AND (({mixture_policy_type} IS NULL AND {carrier_identity_type} IS NULL "
        f"AND {policy_type} IS NULL AND COALESCE({carrier_shape_value}, '') NOT IN ("
        "'day0_remaining_shared_carrier_v1', 'day0_remaining_shared_carrier_v2', "
        "'day0_remaining_shared_carrier_v3', 'day0_remaining_shared_carrier_resolver_v1', "
        "'fused_day0_fast_residual_likelihood')) OR ("
        f"{mixture_policy_type} = 'text' AND {mixture_policy_value} = '{DAY0_PROBABILITY_MIXTURE_POLICY}'))"
    )
    return "\n              ".join(fragments)


def replacement_source_cycle_max_age_hours() -> float:
    """The active staleness horizon in hours (env-overridable, fail-closed).

    A non-positive or unparseable override is IGNORED — it would disable the gate, and the
    gate must never be silently disabled (iron rule: never weaken a gate).
    """
    raw = os.environ.get(_MAX_AGE_ENV)
    if raw is None or not raw.strip():
        return REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT
    return value if value > 0.0 else REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT


def replacement_readiness_expires_at(source_cycle_time: datetime) -> datetime:
    """THE single readiness-expiry derivation (operator directive 2026-06-11 RULE-1 incident).

    Readiness previously expired at ``computed_at + 3h`` (a GUESS, stamped identically at
    TWO sites — materializer + request builder), while the staleness law above says the
    cycle's data is lawful for ``max_age_hours`` after the CYCLE time. Two freshness clocks
    ⇒ the 3h clock re-killed data the 30h law declared lawful: on 2026-06-11 the 06Z rows'
    readiness died at ~06:31Z while the cycle was only ~26h old.

    ONE clock now: readiness expires exactly when the cycle's staleness bound expires.
    The H3 expires_at gate and the cycle-age gate in the bundle reader thereby verify the
    SAME bound from two directions (belt-and-suspenders on one number, never two numbers).
    tests/data/test_cycle_staleness_derivation.py pins both stamp sites to this function.
    """
    from datetime import timedelta  # noqa: PLC0415

    cycle = source_cycle_time if source_cycle_time.tzinfo else source_cycle_time.replace(tzinfo=UTC)
    return cycle.astimezone(UTC) + timedelta(hours=replacement_source_cycle_max_age_hours())


def cycle_age_hours(reference_time: datetime, source_cycle_time: datetime) -> float:
    """``(reference_time - source_cycle_time)`` in hours, both coerced to UTC.

    ``reference_time`` is ``computed_at`` at materialization and ``decision_time`` at live
    admission — the two moments at which a stale cycle would be laundered into "current".
    """
    ref = reference_time.astimezone(UTC)
    cycle = source_cycle_time.astimezone(UTC)
    return (ref - cycle).total_seconds() / 3600.0


def cycle_age_exceeds_bound(
    reference_time: datetime,
    source_cycle_time: datetime,
    *,
    max_age_hours: float | None = None,
) -> bool:
    """True iff the source cycle is older than the staleness bound relative to ``reference_time``."""
    bound = replacement_source_cycle_max_age_hours() if max_age_hours is None else float(max_age_hours)
    return cycle_age_hours(reference_time, source_cycle_time) > bound


def cycle_age_outside_bound(
    reference_time: datetime,
    source_cycle_time: datetime,
    *,
    max_age_hours: float | None = None,
) -> bool:
    """True when a source cycle is future-dated or older than the causal bound."""

    bound = (
        replacement_source_cycle_max_age_hours()
        if max_age_hours is None
        else float(max_age_hours)
    )
    age = cycle_age_hours(reference_time, source_cycle_time)
    return age < 0.0 or age > bound


def classify_cycle_phase(source_cycle_time: datetime) -> str:
    """Classify a model cycle by UTC hour.

    Any hour that is not exactly a 6-hourly cycle hour (defensive: clock skew, sub-hour
    timestamps) is bucketed by nearest 6h cycle. The four standard cycles all return
    ``synoptic`` so 06Z/18Z cannot be downgraded by provenance classification.
    """
    hour = source_cycle_time.astimezone(UTC).hour
    if hour in _SYNOPTIC_CYCLE_HOURS:
        return CYCLE_PHASE_SYNOPTIC
    if hour in _INTERMEDIATE_CYCLE_HOURS:
        return CYCLE_PHASE_INTERMEDIATE
    # Off-cadence hour: snap to the nearest lower 6h cycle and reclassify.
    snapped = (hour // 6) * 6
    return CYCLE_PHASE_SYNOPTIC if snapped in _SYNOPTIC_CYCLE_HOURS else CYCLE_PHASE_INTERMEDIATE
