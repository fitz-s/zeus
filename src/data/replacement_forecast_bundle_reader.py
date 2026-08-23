"""No-bypass reader for live replacement forecast posterior bundles."""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

from src.config import cities_by_name
from src.contracts.settlement_semantics import SettlementSemantics
from src.data.replacement_forecast_cycle_policy import (
    TRADEABLE_GRADE_QLCB_BASIS,
    current_evidence_shape_has_entry_authority,
    current_evidence_shape_has_held_authority,
    cycle_age_outside_bound,
    current_evidence_shape_source_cycle_time,
    replacement_source_cycle_max_age_hours,
)
from src.data.staleness_degrade_ladder import (
    StalenessBand,
    classify_posterior_staleness,
)
from src.data.replacement_forecast_readiness import (
    HIGH_DATA_VERSION,
    LIVE_RUNTIME_LAYER,
    LOW_DATA_VERSION,
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    ReplacementForecastReadinessDecision,
)
from src.data.replacement_input_hwm import replacement_live_input_lag_reason
from src.data.market_topology_rows import _table_columns


_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"

# Operator clobber-category directive 2026-06-10 — live executable read semantics.
# A newer capture can materialize a non-executable posterior before the full fusion
# certificate is present (q_lcb_json NULL or a non-live q carrier). Absolute latest
# read semantics must not serve that row over a certified live FUSED row and collapse
# live eligibility for the whole scope.
# This is the THIRD recurrence; the seed-coverage antibody only fixed the masking side.
#
# A posterior is live-grade iff the row itself is in runtime_layer == "live",
# carries certified bounds (q_lcb_json/q_ucb_json NOT NULL), and its provenance
# replacement_q_mode is one of the fused Normal carrier modes. Mirrored here so LIVE
# selection can prefer the latest live row WITHOUT importing the adapter
# (no cycle); the constant set is asserted equal in a relationship test.
_REPLACEMENT_Q_MODE_LIVE_ELIGIBLE = frozenset(
    {"FUSED_NORMAL_FULL", "FUSED_NORMAL_PARTIAL"}
)

# H3 (REAUDIT_0_1.md §2): fail-closed staleness horizon. ``readiness.expires_at``
# was loaded but NEVER compared to decision_time; a forecast cycle this many hours
# (or older) before the decision is treated as stale. The
# horizon + its env override now live in src/data/replacement_forecast_cycle_policy.py
# (single source of truth shared with the materialization-side fail-closed gate).
# Re-exported here for backward compatibility with existing imports.
_replacement_source_cycle_max_age_hours = replacement_source_cycle_max_age_hours


class ReplacementForecastAuthorityPurpose(StrEnum):
    ENTRY = "entry"
    HELD_REDECISION = "held_redecision"




@dataclass(frozen=True)
class ReplacementForecastPosteriorBundle:
    posterior_id: int
    city: str
    target_date: str
    temperature_metric: str
    source_id: str
    product_id: str
    data_version: str
    q: Mapping[str, float]
    q_lcb: Mapping[str, float] | None
    q_ucb: Mapping[str, float] | None
    bin_topology_hash: str
    family_id: str | None
    posterior_method: str
    source_cycle_time: str
    source_available_at: str
    computed_at: str
    baseline_source_run_id: str
    dependency_json: Mapping[str, Any]
    provenance_json: Mapping[str, Any]
    runtime_layer: str
    posterior_identity_hash: str
    dependency_hash: str
    posterior_config_hash: str

    def __post_init__(self) -> None:
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id), ("data_version", self.data_version)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full product identity")
        if self.runtime_layer != LIVE_RUNTIME_LAYER:
            raise ValueError("replacement posterior bundle requires runtime_layer=live")
        _normalize_probability_map(self.q, field_name="q")
        if self.q_lcb is not None:
            _normalize_probability_map(self.q_lcb, field_name="q_lcb", require_sum=False)
            if set(self.q_lcb) != set(self.q):
                raise ValueError("q_lcb keys must exactly match q keys")
        if self.q_ucb is not None:
            _normalize_probability_map(self.q_ucb, field_name="q_ucb", require_sum=False)
            if set(self.q_ucb) != set(self.q):
                raise ValueError("q_ucb keys must exactly match q keys")
        if not self.bin_topology_hash.strip():
            raise ValueError("bin_topology_hash is required")
        if not all(
            value.strip()
            for value in (
                self.posterior_identity_hash,
                self.dependency_hash,
                self.posterior_config_hash,
            )
        ):
            raise ValueError("replacement posterior bundle identity is incomplete")


@dataclass(frozen=True)
class ReplacementForecastBundleReadResult:
    status: str
    reason_code: str
    bundle: ReplacementForecastPosteriorBundle | None = None

    @property
    def ok(self) -> bool:
        return self.status == READY_STATUS and self.bundle is not None


def _date_text(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        date.fromisoformat(value)
        return value
    raise ValueError("target_date must be a date or ISO date string")


def _metric(value: str) -> str:
    if value not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    return value


def _data_version_for_metric(metric: str) -> str:
    return HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION


def _json_mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if value is None or value == "":
        return {}
    if isinstance(value, Mapping):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be JSON text")
    parsed = json.loads(value)
    if not isinstance(parsed, Mapping):
        raise ValueError(f"{field_name} must decode to an object")
    return parsed


def _decorrelated_providers_complete(provenance: Mapping[str, Any]) -> bool:
    """Read the materializer's canonical nested completeness verdict."""

    fusion = provenance.get("bayes_precision_fusion")
    return isinstance(fusion, Mapping) and fusion.get(
        "decorrelated_providers_complete"
    ) is True


def _normalize_probability_map(value: Mapping[str, Any], *, field_name: str, require_sum: bool = True) -> dict[str, float]:
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    cleaned: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{field_name} keys must be non-empty strings")
        number = float(raw)
        if number < 0.0 or not math.isfinite(number):
            raise ValueError(f"{field_name} values must be non-negative finite numbers")
        cleaned[key] = number
    if require_sum and abs(sum(cleaned.values()) - 1.0) > 1e-9:
        raise ValueError(f"{field_name} must sum to 1")
    return cleaned


def _baseline_source_run_id(baseline_bundle: object | None) -> str | None:
    if baseline_bundle is None:
        return None
    evidence = getattr(baseline_bundle, "evidence", None)
    source_run_id = getattr(evidence, "source_run_id", None)
    if isinstance(source_run_id, str) and source_run_id:
        return source_run_id
    return None


def _baseline_source_run_id_from_readiness(readiness: ReplacementForecastReadinessDecision) -> str | None:
    baseline_dependency = _readiness_dependency_by_role(readiness, "baseline_b0")
    if baseline_dependency is None:
        return None
    source_run_id = baseline_dependency.get("source_run_id")
    return source_run_id if isinstance(source_run_id, str) and source_run_id else None


def _readiness_dependency_by_role(readiness: ReplacementForecastReadinessDecision, role: str) -> Mapping[str, Any] | None:
    dependencies = readiness.dependency_json.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    for item in dependencies:
        if isinstance(item, Mapping) and item.get("role") == role:
            return item
    return None


def _dependency_source_run_mismatch(
    *,
    readiness: ReplacementForecastReadinessDecision,
    posterior_dependency_json: Mapping[str, Any],
) -> bool:
    # The live fused posterior depends on baseline + OM9 anchor + multi-model fusion rows.
    # Do not require deprecated ensemble roles for source-run-id consistency.
    for role in ("baseline_b0", "openmeteo_ifs9_anchor"):
        readiness_dependency = _readiness_dependency_by_role(readiness, role)
        if readiness_dependency is None:
            return True
        readiness_source_run_id = readiness_dependency.get("source_run_id")
        posterior_source_run_id = posterior_dependency_json.get(role)
        if not isinstance(readiness_source_run_id, str) or not readiness_source_run_id:
            return True
        if posterior_source_run_id != readiness_source_run_id:
            return True
    return False


def _parse_utc(value: str, *, field_name: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _live_grade_provenance(
    row_map: Mapping[str, Any],
    *,
    authority_purpose: ReplacementForecastAuthorityPurpose,
) -> Mapping[str, Any] | None:
    """Return provenance only when it authorizes the named capital action."""
    if str(row_map.get("runtime_layer") or "") != LIVE_RUNTIME_LAYER:
        return None
    if not row_map.get("q_lcb_json"):
        return None
    if not row_map.get("q_ucb_json"):
        return None
    provenance = _json_mapping(row_map.get("provenance_json"), field_name="provenance_json")
    mode = provenance.get("replacement_q_mode")
    if not isinstance(mode, str) or not mode:
        return None
    if mode not in _REPLACEMENT_Q_MODE_LIVE_ELIGIBLE:
        return None
    if provenance.get("q_lcb_basis") != TRADEABLE_GRADE_QLCB_BASIS:
        return None
    fusion = provenance.get("bayes_precision_fusion")
    shape = (
        fusion.get("current_evidence_shape")
        if isinstance(fusion, Mapping)
        else None
    )
    if not isinstance(shape, Mapping):
        return None
    shape_authorized = (
        current_evidence_shape_has_entry_authority(provenance)
        if authority_purpose is ReplacementForecastAuthorityPurpose.ENTRY
        else current_evidence_shape_has_held_authority(provenance)
    )
    if not shape_authorized:
        return None
    return provenance


def _readiness_posterior_id(
    readiness: ReplacementForecastReadinessDecision,
    *,
    data_version: str,
    decision_time: datetime,
) -> int | None:
    dependency = _readiness_dependency_by_role(readiness, "soft_anchor_posterior")
    if dependency is None:
        return None
    if (
        dependency.get("source_id") != SOURCE_ID
        or dependency.get("product_id") != PRODUCT_ID
        or dependency.get("data_version") != data_version
        or dependency.get("status") != READY_STATUS
    ):
        return None
    available_at = dependency.get("source_available_at")
    if not isinstance(available_at, str):
        return None
    try:
        if _parse_utc(available_at, field_name="source_available_at") > decision_time:
            return None
    except ValueError:
        return None
    posterior_id = dependency.get("posterior_id")
    return posterior_id if type(posterior_id) is int and posterior_id > 0 else None


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _temperature_bound_to_c(value: object, *, unit: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if unit == "F":
        return (number - 32.0) * 5.0 / 9.0
    if unit == "C":
        return number
    raise ValueError("temperature unit must be C or F")


def _display_unit_for_label(label: str, *, fallback: str) -> str:
    if "\u00b0F" in label or "Fahrenheit" in label:
        return "F"
    if "\u00b0C" in label or "Celsius" in label:
        return "C"
    normalized = fallback.strip().upper()
    if normalized in {"C", "F"}:
        return normalized
    raise ValueError("temperature unit must be C or F")


def _market_bin_topology_payload_from_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    city: str,
) -> list[dict[str, object]] | None:
    ordered = sorted(
        rows,
        key=lambda row: (
            -999999.0
            if row.get("range_low") is None
            else float(row["range_low"]),
            999999.0
            if row.get("range_high") is None
            else float(row["range_high"]),
            str(row.get("condition_id") or ""),
        ),
    )
    if not ordered:
        return None
    city_cfg = cities_by_name.get(city)
    if city_cfg is None:
        return None
    rounding_rule = SettlementSemantics.for_city(city_cfg).rounding_rule
    topology: list[dict[str, object]] = []
    for row in ordered:
        label = str(row.get("range_label") or row.get("outcome") or "").strip()
        if not label:
            return None
        settlement_unit = str(getattr(city_cfg, "settlement_unit", "") or getattr(city_cfg, "unit", "") or "").strip().upper()
        if settlement_unit not in {"C", "F"}:
            settlement_unit = _display_unit_for_label(label, fallback="C")
        display_unit = _display_unit_for_label(label, fallback=settlement_unit)
        settlement_step_c = 5.0 / 9.0 if settlement_unit == "F" else 1.0
        lower_c = _temperature_bound_to_c(row.get("range_low"), unit=display_unit)
        upper_c = _temperature_bound_to_c(row.get("range_high"), unit=display_unit)
        if lower_c is None and upper_c is not None:
            center_c = upper_c - settlement_step_c
        elif upper_c is None and lower_c is not None:
            center_c = lower_c + settlement_step_c
        elif lower_c is not None and upper_c is not None:
            center_c = (lower_c + upper_c) / 2.0
        else:
            return None
        topology.append(
            {
                "bin_id": label,
                "lower_c": lower_c,
                "upper_c": upper_c,
                "center_c": center_c,
                "display_unit": display_unit,
                "settlement_unit": settlement_unit,
                "rounding_rule": rounding_rule,
                "settlement_step_c": float(settlement_step_c),
            }
        )
    return topology


def market_bin_topology_hash_from_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    city: str,
) -> str | None:
    topology = _market_bin_topology_payload_from_rows(rows, city=city)
    return _json_hash(topology) if topology is not None else None


def _current_market_bin_topology_hash(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> str | None:
    topology = _current_market_bin_topology_payload(
        conn,
        city=city,
        target_date=target_date,
        temperature_metric=temperature_metric,
    )
    return _json_hash(topology) if topology is not None else None


def _current_market_bin_topology_payload(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> list[dict[str, object]] | None:
    columns = _table_columns(conn, "market_events")
    required = {"city", "target_date", "temperature_metric", "condition_id", "range_label", "range_low", "range_high"}
    if not required.issubset(columns):
        return None
    rows = conn.execute(
        """
        SELECT range_label, outcome, range_low, range_high, condition_id
        FROM market_events
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND COALESCE(condition_id, '') != ''
        ORDER BY
          CASE WHEN range_low IS NULL THEN -999999 ELSE range_low END,
          CASE WHEN range_high IS NULL THEN 999999 ELSE range_high END,
          condition_id
        """,
        (city, target_date, temperature_metric),
    ).fetchall()
    return _market_bin_topology_payload_from_rows(
        (dict(row) for row in rows),
        city=city,
    )


def _topology_core(value: object) -> list[dict[str, object]] | None:
    if not isinstance(value, list) or not value:
        return None
    out: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None
        bin_id = str(item.get("bin_id") or "").strip()
        if not bin_id:
            return None
        # H4 (REAUDIT_0_1.md §2): topology-core IS the SETTLEMENT identity, not just
        # physical geometry. Two posteriors with identical Celsius geometry but
        # different rounding_rule (wmo_half_up vs the hko oracle_truncate) or
        # settlement_unit settle to DIFFERENT integers at a bin boundary — they are
        # NOT the same market and must NOT be treated as equivalent by the
        # hash-mismatch fallback (_topology_core_equivalent). Only display_unit is
        # excluded: it is pure presentation and never changes the settlement outcome.
        row = {
            "bin_id": bin_id,
            "lower_c": item.get("lower_c"),
            "upper_c": item.get("upper_c"),
            "center_c": item.get("center_c"),
            "settlement_step_c": item.get("settlement_step_c"),
            "settlement_unit": (
                str(item.get("settlement_unit")).strip().upper()
                if item.get("settlement_unit") is not None
                else None
            ),
            "rounding_rule": (
                str(item.get("rounding_rule")).strip()
                if item.get("rounding_rule") is not None
                else None
            ),
        }
        for key in ("lower_c", "upper_c", "center_c", "settlement_step_c"):
            if row[key] is not None:
                row[key] = round(float(row[key]), 12)
        out.append(row)
    return out


def _topology_core_equivalent(left: object, right: object) -> bool:
    left_core = _topology_core(left)
    right_core = _topology_core(right)
    return left_core is not None and right_core is not None and left_core == right_core


def read_replacement_forecast_bundle(
    conn: sqlite3.Connection,
    *,
    baseline_bundle: object | None,
    readiness: ReplacementForecastReadinessDecision,
    city: str,
    target_date: date | str,
    temperature_metric: str,
    decision_time: datetime | str,
    require_baseline_bundle: bool = True,
    current_bin_topology_hash: str | None = None,
    enforce_raw_input_hwm: bool = False,
    raw_input_hwm_conn: sqlite3.Connection | None = None,
    raw_input_hwm_deadline_monotonic: float | None = None,
    raw_input_hwm_read_max_seconds: float | None = None,
    authority_purpose: ReplacementForecastAuthorityPurpose = (
        ReplacementForecastAuthorityPurpose.ENTRY
    ),
) -> ReplacementForecastBundleReadResult:
    """Read a derived replacement posterior only after B0 executable proof exists.

    ``enforce_raw_input_hwm`` (W0.1, 2026-07-02, default False = every existing caller
    byte-identical): when True, after the bundle is otherwise ready, reject it if a raw
    model/artifact input newer than the served posterior's ``source_cycle_time`` already
    exists (src.data.replacement_input_hwm.replacement_live_input_lag_reason) — fail
    closed on a read-time-stale posterior instead of serving it.
    """

    if not isinstance(authority_purpose, ReplacementForecastAuthorityPurpose):
        raise TypeError(
            "authority_purpose must be ReplacementForecastAuthorityPurpose"
        )
    baseline_run_id = _baseline_source_run_id(baseline_bundle)
    if baseline_run_id is None and not require_baseline_bundle:
        baseline_run_id = _baseline_source_run_id_from_readiness(readiness)
    if baseline_run_id is None:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_BASELINE_EXECUTABLE_FORECAST_REQUIRED")
    if not isinstance(readiness, ReplacementForecastReadinessDecision):
        raise TypeError("readiness must be ReplacementForecastReadinessDecision")
    if readiness.status != READY_STATUS:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_READINESS_NOT_READY")

    metric = _metric(temperature_metric)
    target_date_text = _date_text(target_date)
    data_version = _data_version_for_metric(metric)
    posterior_columns = _table_columns(conn, "forecast_posteriors")
    required_identity_columns = {"q_ucb_json", "bin_topology_hash", "posterior_identity_hash", "dependency_hash", "posterior_config_hash"}
    if not required_identity_columns.issubset(posterior_columns):
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_IDENTITY_SCHEMA_MISSING")
    current_topology_hash = str(current_bin_topology_hash or "").strip()
    if not current_topology_hash:
        current_topology_hash = str(
            _current_market_bin_topology_hash(
                conn,
                city=city,
                target_date=target_date_text,
                temperature_metric=metric,
            )
            or ""
        ).strip()
    if not current_topology_hash:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_CURRENT_BIN_TOPOLOGY_HASH_REQUIRED")
    decision_utc = decision_time if isinstance(decision_time, datetime) else datetime.fromisoformat(decision_time.replace("Z", "+00:00"))
    if decision_utc.tzinfo is None or decision_utc.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    decision_utc = decision_utc.astimezone(timezone.utc)

    # New-entry probability authority is point-in-time truth. Expired readiness cannot
    # license capital, even when it is the newest row present; callers must pursue a fresh
    # certificate and fail closed meanwhile.
    if readiness.expires_at is not None and readiness.expires_at <= decision_utc:
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_LIVE_READINESS_EXPIRED",
        )

    readiness_bound_posterior_id = _readiness_posterior_id(
        readiness,
        data_version=data_version,
        decision_time=decision_utc,
    )
    if readiness_bound_posterior_id is None:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_READINESS_MISMATCH")

    # Live read semantics: execution binds the exact posterior certified by readiness.
    # Forecast posteriors are append-only; a later same-scope row can appear before its
    # readiness certificate is durable. Serving that later row with an older readiness
    # creates a false mismatch and blocks profitable candidates.
    latest_row = conn.execute(
        """
        SELECT * FROM forecast_posteriors
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND product_id = ?
          AND data_version = ?
          AND training_allowed = 0
          AND runtime_layer = ?
        ORDER BY computed_at DESC, posterior_id DESC
        LIMIT 1
        """,
        (city, target_date_text, metric, SOURCE_ID, PRODUCT_ID, data_version, LIVE_RUNTIME_LAYER),
    ).fetchone()
    if latest_row is None:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_MISSING")
    latest_row_map = dict(latest_row)
    if int(latest_row_map["posterior_id"]) == readiness_bound_posterior_id:
        row_map = latest_row_map
    else:
        certified_row = conn.execute(
            """
            SELECT * FROM forecast_posteriors
            WHERE posterior_id = ?
              AND city = ?
              AND target_date = ?
              AND temperature_metric = ?
              AND source_id = ?
              AND product_id = ?
              AND data_version = ?
              AND training_allowed = 0
              AND runtime_layer = ?
            LIMIT 1
            """,
            (
                readiness_bound_posterior_id,
                city,
                target_date_text,
                metric,
                SOURCE_ID,
                PRODUCT_ID,
                data_version,
                LIVE_RUNTIME_LAYER,
            ),
        ).fetchone()
        if certified_row is None:
            return ReplacementForecastBundleReadResult(
                "BLOCKED", "REPLACEMENT_POSTERIOR_READINESS_MISMATCH"
            )
        row_map = dict(certified_row)
    provenance = _live_grade_provenance(
        row_map,
        authority_purpose=authority_purpose,
    )
    if provenance is None:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_READINESS_NOT_LIVE_GRADE")
    # Fallback case: we are serving an OLDER live row because a NEWER non-live
    # row sits on top. The scope readiness (per-scope upsert) now points at that newer non-executable
    # row, so the readiness/dependency-agreement gates below must be re-anchored to the SERVED
    # row's OWN intrinsic provenance (immutable, validated at its materialization) rather than
    # to the overwritten scope readiness. We still enforce EVERY intrinsic-integrity gate on the
    # served row (staleness, intermediate-cycle, topology-to-current-market, identity hashes).
    _served_via_tradeable_fallback = (
        int(row_map["posterior_id"]) != int(latest_row_map["posterior_id"])
        and _live_grade_provenance(
            latest_row_map,
            authority_purpose=authority_purpose,
        )
        is None
    )
    _newer_non_executable_posterior_id = (
        int(latest_row_map["posterior_id"]) if _served_via_tradeable_fallback else None
    )
    if _parse_utc(str(row_map["source_available_at"]), field_name="source_available_at") > decision_utc:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_AFTER_DECISION_TIME")
    if _parse_utc(str(row_map["computed_at"]), field_name="computed_at") > decision_utc:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_COMPUTED_AFTER_DECISION_TIME")
    # H3 (REAUDIT_0_1.md §2) — upper age bound on the underlying forecast cycle.
    # The reader already rejects FUTURE posteriors; this adds the missing STALE
    # bound: a source_cycle_time older than the fail-closed horizon means the data
    # the posterior was built on is too old to trade as live, even if expires_at is
    # still in the future. Same single-gate location, inherited by both paths. The
    # horizon is the SAME constant the materialization-side fail-closed gate uses
    # (src/data/replacement_forecast_cycle_policy.py) so the two gates can never drift.
    _source_cycle_utc = _parse_utc(str(row_map["source_cycle_time"]), field_name="source_cycle_time")
    if cycle_age_outside_bound(decision_utc, _source_cycle_utc):
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_LIVE_CYCLE_AGE_EXCEEDS_BOUND",
        )
    ensemble_cycle_utc = current_evidence_shape_source_cycle_time(provenance)
    if ensemble_cycle_utc is None:
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_ENSEMBLE_CYCLE_TIME_MISSING",
        )
    if cycle_age_outside_bound(decision_utc, ensemble_cycle_utc):
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_ENSEMBLE_CYCLE_AGE_EXCEEDS_BOUND",
        )
    # §4a staleness DEGRADE LADDER (authority doc, 2026-07-17): between the fresh band
    # and the EXPIRED wall handled just above, an aged carrier is GRADED. RED (age > 24h,
    # derived boundary) isolates ENTRY ONLY. HELD_REDECISION is explicitly reduce-only
    # and therefore remains readable in RED;
    # the unchanged 30h absolute cycle-age wall above still blocks both purposes.
    # GREEN/AMBER serve the bundle unchanged here; AMBER's fitted sigma inflation is
    # applied at the admission sigma seam, never by withholding the belief. UNKNOWN
    # falls through to the binary law already enforced above.
    _ladder = classify_posterior_staleness(decision_utc, _source_cycle_utc)
    if (
        authority_purpose is ReplacementForecastAuthorityPurpose.ENTRY
        and _ladder.band is StalenessBand.RED
    ):
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_STALENESS_RED_ENTRY_ISOLATED",
        )

    dependency_json = _json_mapping(row_map["dependency_source_run_ids_json"], field_name="dependency_source_run_ids_json")
    if _served_via_tradeable_fallback:
        # Re-anchor the certification to the SERVED live row's intrinsic provenance. The
        # scope readiness was overwritten in place by the newer non-executable cycle's materialization
        # (readiness_state upserts on scope_key — no cycle in the key), so it no longer points at
        # this row. The served row is self-certifying: it was materialized WITH its own READY
        # readiness (a BAYES_PRECISION_FUSION/bounds-less row is never live-grade), and it carries its own
        # immutable dependency_source_run_ids_json + identity hashes. We bind the bundle's
        # baseline_source_run_id to the served row's intrinsic baseline (not the overwritten
        # readiness's), and we DO NOT require the scope readiness to point at this posterior.
        # Every intrinsic-integrity gate (staleness, intermediate-cycle, topology-to-market,
        # identity-hash presence) has already run / still runs against this served row, so the
        # no-bypass guarantee is preserved — only the stale readiness pointer is relaxed.
        intrinsic_baseline = dependency_json.get("baseline_b0")
        if isinstance(intrinsic_baseline, str) and intrinsic_baseline:
            baseline_run_id = intrinsic_baseline
    else:
        baseline_dependency = _readiness_dependency_by_role(readiness, "baseline_b0")
        if baseline_dependency is None or baseline_dependency.get("source_run_id") != baseline_run_id:
            return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_BASELINE_READINESS_MISMATCH")
        if _dependency_source_run_mismatch(readiness=readiness, posterior_dependency_json=dependency_json):
            return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_DEPENDENCY_SOURCE_RUN_MISMATCH")

    q = _normalize_probability_map(_json_mapping(row_map["q_json"], field_name="q_json"), field_name="q")
    q_lcb_raw = _json_mapping(row_map["q_lcb_json"], field_name="q_lcb_json") if row_map.get("q_lcb_json") else None
    q_lcb = _normalize_probability_map(q_lcb_raw, field_name="q_lcb", require_sum=False) if q_lcb_raw is not None else None
    q_ucb_raw = _json_mapping(row_map["q_ucb_json"], field_name="q_ucb_json") if row_map.get("q_ucb_json") else None
    q_ucb = _normalize_probability_map(q_ucb_raw, field_name="q_ucb", require_sum=False) if q_ucb_raw is not None else None
    row_topology_hash = str(row_map.get("bin_topology_hash") or "").strip()
    provenance_topology_hash = str(provenance.get("bin_topology_hash") or "").strip()
    if not row_topology_hash or not provenance_topology_hash:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_BIN_TOPOLOGY_HASH_MISSING")
    if row_topology_hash != provenance_topology_hash:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_BIN_TOPOLOGY_HASH_CONFLICT")
    if row_topology_hash != current_topology_hash:
        current_topology_payload = _current_market_bin_topology_payload(
            conn,
            city=city,
            target_date=target_date_text,
            temperature_metric=metric,
        )
        if not _topology_core_equivalent(provenance.get("bin_topology"), current_topology_payload):
            return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_BIN_TOPOLOGY_HASH_MISMATCH")
    for field_name in ("posterior_identity_hash", "dependency_hash", "posterior_config_hash"):
        if not str(row_map.get(field_name) or "").strip():
            return ReplacementForecastBundleReadResult("BLOCKED", f"REPLACEMENT_POSTERIOR_{field_name.upper()}_MISSING")
    if _served_via_tradeable_fallback:
        # Record a provenance note (telemetry-visible) that the LIVE bundle fell back to an
        # older live row because a newer non-live row clobbered the latest slot.
        provenance = {
            **provenance,
            "tradeable_latest_selection": {
                "served_posterior_id": int(row_map["posterior_id"]),
                "newer_non_executable_posterior_id": _newer_non_executable_posterior_id,
                "reason": "newer_row_not_live_grade_served_older_live_bounds",
            },
        }
    if enforce_raw_input_hwm:
        hwm_conn = raw_input_hwm_conn or conn
        hwm_deadline = raw_input_hwm_deadline_monotonic
        raw_lag_reason: str | None
        if hwm_deadline is not None and time.monotonic() >= hwm_deadline:
            raw_lag_reason = "basis=HWM_READ_DEADLINE"
        else:
            managed_deadline = (
                hwm_deadline is not None
                or raw_input_hwm_read_max_seconds is not None
            )
            deadline_holder: list[float | None] = [None]
            handler_installed = False
            try:
                if managed_deadline and hasattr(hwm_conn, "set_progress_handler"):
                    hwm_conn.set_progress_handler(
                        lambda: int(
                            deadline_holder[0] is not None
                            and time.monotonic() >= deadline_holder[0]
                        ),
                        1_000,
                    )
                    handler_installed = True
                if raw_input_hwm_read_max_seconds is not None:
                    stage_deadline = time.monotonic() + max(
                        0.0, float(raw_input_hwm_read_max_seconds)
                    )
                    hwm_deadline = (
                        stage_deadline
                        if hwm_deadline is None
                        else min(hwm_deadline, stage_deadline)
                    )
                deadline_holder[0] = hwm_deadline
                if hwm_deadline is not None and time.monotonic() >= hwm_deadline:
                    raw_lag_reason = "basis=HWM_READ_DEADLINE"
                else:
                    raw_lag_reason = replacement_live_input_lag_reason(
                        hwm_conn,
                        city=city,
                        target_date=target_date_text,
                        metric=metric,
                        decision_time=decision_utc,
                        posterior_source_cycle_time=row_map["source_cycle_time"],
                        posterior_computed_at=row_map["computed_at"],
                        posterior_provenance=provenance,
                        held_redecision=(
                            authority_purpose
                            is ReplacementForecastAuthorityPurpose.HELD_REDECISION
                        ),
                    )
                    if (
                        hwm_deadline is not None
                        and time.monotonic() >= hwm_deadline
                    ):
                        raw_lag_reason = "basis=HWM_READ_DEADLINE"
            finally:
                if handler_installed:
                    try:
                        hwm_conn.set_progress_handler(None, 0)
                    except Exception as exc:
                        raise RuntimeError("HWM_READ_CLEANUP_FAILED") from exc
        if raw_lag_reason is not None:
            return ReplacementForecastBundleReadResult(
                "BLOCKED", f"REPLACEMENT_RAW_INPUT_HWM:{raw_lag_reason}"
            )
    bundle = ReplacementForecastPosteriorBundle(
        posterior_id=int(row_map["posterior_id"]),
        city=str(row_map["city"]),
        target_date=str(row_map["target_date"]),
        temperature_metric=str(row_map["temperature_metric"]),
        source_id=str(row_map["source_id"]),
        product_id=str(row_map["product_id"]),
        data_version=str(row_map["data_version"]),
        q=q,
        q_lcb=q_lcb,
        q_ucb=q_ucb,
        bin_topology_hash=row_topology_hash,
        family_id=str(row_map.get("family_id") or "") or None,
        posterior_method=str(row_map["posterior_method"]),
        source_cycle_time=str(row_map["source_cycle_time"]),
        source_available_at=str(row_map["source_available_at"]),
        computed_at=str(row_map["computed_at"]),
        baseline_source_run_id=baseline_run_id,
        dependency_json=dependency_json,
        provenance_json=provenance,
        runtime_layer=str(row_map["runtime_layer"]),
        posterior_identity_hash=str(row_map["posterior_identity_hash"]),
        dependency_hash=str(row_map["dependency_hash"]),
        posterior_config_hash=str(row_map["posterior_config_hash"]),
    )
    return ReplacementForecastBundleReadResult(READY_STATUS, "REPLACEMENT_POSTERIOR_READY", bundle)


def _pinned_readiness_for_posterior(
    row: Mapping[str, Any],
    *,
    posterior_id: int,
) -> ReplacementForecastReadinessDecision:
    """Build an in-memory certificate that binds the exact immutable row.

    This is intentionally not a readiness-state read: the live readiness pointer may
    already have advanced to an incomplete newer cycle.  The existing bundle reader
    still validates every intrinsic row contract against this certificate.
    """

    dependencies = _json_mapping(
        row.get("dependency_source_run_ids_json"),
        field_name="dependency_source_run_ids_json",
    )
    source_available_at = str(row.get("source_available_at") or "").strip()
    data_version = str(row.get("data_version") or "").strip()
    dependency_rows = []
    for role in ("baseline_b0", "openmeteo_ifs9_anchor"):
        dependency_rows.append(
            {
                "role": role,
                "source_id": SOURCE_ID,
                "product_id": PRODUCT_ID,
                "data_version": data_version,
                "source_run_id": dependencies.get(role),
                "source_available_at": source_available_at,
                "status": READY_STATUS,
            }
        )
    dependency_rows.append(
        {
            "role": "soft_anchor_posterior",
            "source_id": SOURCE_ID,
            "product_id": PRODUCT_ID,
            "data_version": data_version,
            "source_run_id": str(row.get("posterior_identity_hash") or ""),
            "source_available_at": source_available_at,
            "status": READY_STATUS,
            "posterior_id": posterior_id,
        }
    )
    return ReplacementForecastReadinessDecision(
        readiness_id=f"pinned-posterior:{posterior_id}",
        status=READY_STATUS,
        reason_codes=("REPLACEMENT_PINNED_COMPLETE_POSTERIOR",),
        dependency_json={"dependencies": dependency_rows},
        provenance_json=_json_mapping(
            row.get("provenance_json"),
            field_name="provenance_json",
        ),
        # Intrinsic source-cycle age below is the real expiry for a pinned row;
        # the readiness value object nevertheless requires a non-null timestamp.
        expires_at=datetime.max.replace(tzinfo=timezone.utc),
        source_id=SOURCE_ID,
        product_id=PRODUCT_ID,
    )


def read_pinned_replacement_forecast_bundle(
    conn: sqlite3.Connection,
    *,
    posterior_id: int,
    city: str,
    target_date: date | str,
    temperature_metric: str,
    decision_time: datetime | str,
    current_bin_topology_hash: str | None = None,
    authority_purpose: ReplacementForecastAuthorityPurpose = (
        ReplacementForecastAuthorityPurpose.HELD_REDECISION
    ),
) -> ReplacementForecastBundleReadResult:
    """Read one exact immutable posterior for held-only Day0 recomputation.

    The exact row is validated by the ordinary no-bypass reader, but readiness and
    raw-input HWM are deliberately not re-read: this route is only for an existing
    held/reduce-only position while a newer cycle is incomplete.  ENTRY cannot call
    this function, and no producer or queue state is touched.
    """

    if authority_purpose is not ReplacementForecastAuthorityPurpose.HELD_REDECISION:
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_HELD_AUTHORITY_REQUIRED",
        )
    try:
        posterior_id = int(posterior_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("REPLACEMENT_PINNED_POSTERIOR_ID_INVALID") from exc
    if posterior_id <= 0:
        raise ValueError("REPLACEMENT_PINNED_POSTERIOR_ID_INVALID")
    metric = _metric(temperature_metric)
    target_date_text = _date_text(target_date)
    data_version = _data_version_for_metric(metric)
    row = conn.execute(
        """
        SELECT * FROM forecast_posteriors
        WHERE posterior_id = ?
          AND city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND product_id = ?
          AND data_version = ?
          AND training_allowed = 0
          AND runtime_layer = ?
        LIMIT 1
        """,
        (
            posterior_id,
            city,
            target_date_text,
            metric,
            SOURCE_ID,
            PRODUCT_ID,
            data_version,
            LIVE_RUNTIME_LAYER,
        ),
    ).fetchone()
    if row is None:
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_POSTERIOR_MISSING",
        )
    row_map = dict(row)
    provenance = _json_mapping(
        row_map.get("provenance_json"),
        field_name="provenance_json",
    )
    if not _decorrelated_providers_complete(provenance):
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_POSTERIOR_NOT_COMPLETE",
        )
    readiness = _pinned_readiness_for_posterior(
        row_map,
        posterior_id=posterior_id,
    )
    return read_replacement_forecast_bundle(
        conn,
        baseline_bundle=None,
        readiness=readiness,
        city=city,
        target_date=target_date_text,
        temperature_metric=metric,
        decision_time=decision_time,
        require_baseline_bundle=False,
        current_bin_topology_hash=current_bin_topology_hash,
        enforce_raw_input_hwm=False,
        authority_purpose=authority_purpose,
    )


def read_prior_complete_replacement_forecast_bundle(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: date | str,
    temperature_metric: str,
    decision_time: datetime | str,
    current_bin_topology_hash: str | None = None,
    authority_purpose: ReplacementForecastAuthorityPurpose = (
        ReplacementForecastAuthorityPurpose.HELD_REDECISION
    ),
) -> ReplacementForecastBundleReadResult:
    """Select the prior complete carrier only across a newer incomplete wave.

    A newer complete row returns ``NOT_APPLICABLE`` so the caller resets to the
    ordinary current-cycle path.  A missing/invalid prior carrier remains blocked;
    it is never replaced with a stale or mixed-clock source.
    """

    if authority_purpose is not ReplacementForecastAuthorityPurpose.HELD_REDECISION:
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_HELD_AUTHORITY_REQUIRED",
        )
    metric = _metric(temperature_metric)
    target_date_text = _date_text(target_date)
    data_version = _data_version_for_metric(metric)
    decision_utc = _parse_utc(
        decision_time.isoformat()
        if isinstance(decision_time, datetime)
        else str(decision_time),
        field_name="decision_time",
    )
    decision_iso = decision_utc.isoformat()
    scope_params = (
        city,
        target_date_text,
        metric,
        SOURCE_ID,
        PRODUCT_ID,
        data_version,
        LIVE_RUNTIME_LAYER,
        decision_iso,
        decision_iso,
        decision_iso,
    )
    try:
        frontier = conn.execute(
            """
            SELECT posterior_id, source_cycle_time, source_available_at,
                   computed_at, provenance_json
              FROM forecast_posteriors
             WHERE city = ?
               AND target_date = ?
               AND temperature_metric = ?
               AND source_id = ?
               AND product_id = ?
               AND data_version = ?
               AND training_allowed = 0
               AND runtime_layer = ?
               AND datetime(source_cycle_time) <= datetime(?)
               AND datetime(source_available_at) <= datetime(?)
               AND datetime(computed_at) <= datetime(?)
             ORDER BY source_cycle_time DESC, computed_at DESC, posterior_id DESC
             LIMIT 1
            """,
            scope_params,
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return ReplacementForecastBundleReadResult(
            "NOT_APPLICABLE",
            "REPLACEMENT_PINNED_POSTERIOR_SCHEMA_UNAVAILABLE",
        )
    if frontier is None:
        return ReplacementForecastBundleReadResult(
            "NOT_APPLICABLE",
            "REPLACEMENT_PINNED_NO_POSTERIOR_WAVE",
        )
    latest = dict(frontier)
    try:
        latest_cycle = _parse_utc(
            str(latest.get("source_cycle_time") or ""),
            field_name="source_cycle_time",
        )
        _latest_computed = _parse_utc(
            str(latest.get("computed_at") or ""),
            field_name="computed_at",
        )
        latest_id = int(latest["posterior_id"])
    except (KeyError, TypeError, ValueError):
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_FRONTIER_CLOCK_INVALID",
        )
    latest_provenance = _json_mapping(
        latest.get("provenance_json"),
        field_name="provenance_json",
    )
    if _decorrelated_providers_complete(latest_provenance):
        return ReplacementForecastBundleReadResult(
            "NOT_APPLICABLE",
            "REPLACEMENT_PINNED_COMPLETE_CYCLE_RESET",
        )
    try:
        candidate = conn.execute(
            """
            SELECT posterior_id, source_cycle_time, source_available_at,
                   computed_at, provenance_json
              FROM forecast_posteriors
             WHERE city = ?
               AND target_date = ?
               AND temperature_metric = ?
               AND source_id = ?
               AND product_id = ?
               AND data_version = ?
               AND training_allowed = 0
               AND runtime_layer = ?
               AND datetime(source_cycle_time) <= datetime(?)
               AND datetime(source_available_at) <= datetime(?)
               AND datetime(computed_at) <= datetime(?)
               AND json_extract(provenance_json, '$.replacement_q_mode') = 'FUSED_NORMAL_FULL'
               AND json_extract(provenance_json, '$.capture_status') = 'FULL_CURRENT'
               AND json_extract(
                     provenance_json,
                     '$.bayes_precision_fusion.decorrelated_providers_complete'
                   ) = 1
               AND (
                     source_cycle_time < ?
                  OR (source_cycle_time = ? AND computed_at < ?)
                  OR (source_cycle_time = ? AND computed_at = ? AND posterior_id < ?)
               )
             ORDER BY source_cycle_time DESC, computed_at DESC, posterior_id DESC
             LIMIT 1
            """,
            scope_params
            + (
                latest.get("source_cycle_time"),
                latest.get("source_cycle_time"),
                latest.get("computed_at"),
                latest.get("source_cycle_time"),
                latest.get("computed_at"),
                latest_id,
            ),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return ReplacementForecastBundleReadResult(
            "NOT_APPLICABLE",
            "REPLACEMENT_PINNED_POSTERIOR_SCHEMA_UNAVAILABLE",
        )
    if candidate is None:
        return ReplacementForecastBundleReadResult(
            "NOT_APPLICABLE",
            "REPLACEMENT_PINNED_COMPLETE_CARRIER_MISSING",
        )
    candidate = dict(candidate)
    if not _decorrelated_providers_complete(
        _json_mapping(candidate.get("provenance_json"), field_name="provenance_json")
    ):
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_COMPLETE_CARRIER_INVALID",
        )
    try:
        candidate_cycle = _parse_utc(
            str(candidate.get("source_cycle_time") or ""),
            field_name="source_cycle_time",
        )
    except ValueError:
        return ReplacementForecastBundleReadResult(
            "BLOCKED",
            "REPLACEMENT_PINNED_COMPLETE_CYCLE_INVALID",
        )
    if candidate_cycle >= latest_cycle:
        return ReplacementForecastBundleReadResult(
            "NOT_APPLICABLE",
            "REPLACEMENT_PINNED_COMPLETE_CYCLE_RESET",
        )
    result = read_pinned_replacement_forecast_bundle(
        conn,
        posterior_id=int(candidate["posterior_id"]),
        city=city,
        target_date=target_date_text,
        temperature_metric=metric,
        decision_time=decision_time,
        current_bin_topology_hash=current_bin_topology_hash,
        authority_purpose=authority_purpose,
    )
    if result.ok:
        return ReplacementForecastBundleReadResult(
            READY_STATUS,
            "REPLACEMENT_PINNED_COMPLETE_POSTERIOR_READY",
            result.bundle,
        )
    return result
