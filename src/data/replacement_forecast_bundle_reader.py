"""No-bypass reader for replacement forecast shadow posterior bundles."""

from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.config import cities_by_name
from src.data.replacement_forecast_cycle_policy import (
    CYCLE_PHASE_INTERMEDIATE,
    REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS_DEFAULT,
    classify_cycle_phase,
    cycle_age_exceeds_bound,
    replacement_source_cycle_max_age_hours,
)
from src.data.replacement_forecast_readiness import (
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    ReplacementForecastReadinessDecision,
)


HIGH_DATA_VERSION = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1"
LOW_DATA_VERSION = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"

# Operator clobber-category directive 2026-06-10 — TRADEABLE-LATEST read semantics.
# The bounds-less clobber: a NEWER model cycle that has anchor manifests but no fusion
# instruments yet materializes a bounds-less posterior (q_lcb_json NULL,
# replacement_q_mode=U0R_CAPTURE_MISSING — a SHADOW row by design, with shadow value). The
# absolute-latest read semantics (ORDER BY computed_at DESC) then SERVE that shadow row over
# the still-current tradeable-grade FUSED row, collapsing live eligibility for the whole scope.
# This is the THIRD recurrence; the seed-coverage antibody only fixed the masking side.
#
# A posterior is TRADEABLE-GRADE (live-eligible) iff it carries certified bounds (q_lcb_json
# NOT NULL) AND its provenance.replacement_q_mode is one of the fused-Normal modes. This is the
# EXACT predicate the live gate enforces (event_reactor_adapter._replacement_q_mode_live_eligibility)
# and the seed-coverage antibody uses (shadow_materialization_queue: q_lcb_json IS NOT NULL).
# Mirrored here so LIVE selection can prefer the latest tradeable row WITHOUT importing the
# adapter (no cycle); the constant set is asserted equal in a relationship test.
_REPLACEMENT_Q_MODE_LIVE_ELIGIBLE = frozenset({"FUSED_NORMAL_FULL", "FUSED_NORMAL_PARTIAL"})

# H3 (REAUDIT_0_1.md §2): fail-closed staleness horizon. ``readiness.expires_at``
# was loaded but NEVER compared to decision_time; a forecast cycle this many hours
# (or older) before the decision is treated as DEAD and refused live authority. The
# horizon + its env override now live in src/data/replacement_forecast_cycle_policy.py
# (single source of truth shared with the materialization-side fail-closed gate).
# Re-exported here for backward compatibility with existing imports.
_replacement_source_cycle_max_age_hours = replacement_source_cycle_max_age_hours


# Operator cycle-physics directive 2026-06-10: a posterior sourced from an intermediate
# (06/18Z) model cycle carries provenance_json.cycle_phase == "intermediate". The de-bias
# + fusion weights were trained on ~99% 00Z-cycle history, so an intermediate-phase
# posterior is produced + readiness-stamped (keeping production alive in the dead zones)
# but is held to SHADOW-ONLY for LIVE admission by default until a settlement-graded
# comparison licenses it. Flag default OFF = shadow-only (never weaken a gate).
_REPLACEMENT_INTERMEDIATE_CYCLE_LIVE_FLAG = "replacement_0_1_intermediate_cycle_live_admission_enabled"


def _replacement_intermediate_cycle_live_admission_enabled() -> bool:
    """Whether intermediate-phase (06/18Z) posteriors may be admitted LIVE. Default FALSE.

    Fail-closed: any config error -> False (intermediate phase stays shadow-only). The
    operator promotes this only after a settlement-graded synoptic-vs-intermediate skill
    comparison; until then 06/18Z posteriors keep production alive without trading live.
    """
    try:
        from src.config import settings  # noqa: PLC0415

        return bool(settings["edli_v1"].get(_REPLACEMENT_INTERMEDIATE_CYCLE_LIVE_FLAG, False))
    except Exception:
        return False


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
    trade_authority_status: str

    def __post_init__(self) -> None:
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id), ("data_version", self.data_version)):
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full product identity")
        if self.trade_authority_status not in {"SHADOW_ONLY", "SHADOW_VETO_ONLY"}:
            raise ValueError("replacement posterior bundle must remain shadow-only")
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
    for role in ("baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor"):
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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _row_is_tradeable_grade(row_map: Mapping[str, Any]) -> bool:
    """A posterior row is tradeable-grade (live-eligible) iff it has certified bounds AND a
    fused-Normal q-mode. NULL bounds OR a non-fused mode (U0R_CAPTURE_MISSING,
    FUSED_NORMAL_BOUNDS_MISSING, SOFT_ANCHOR_FALLBACK, ...) => SHADOW-only, never live.

    This is the read-side mirror of the live gate's eligibility predicate; it is what makes a
    newer bounds-less SHADOW row NOT clobber an older tradeable row on the LIVE path.
    """
    if not row_map.get("q_lcb_json"):
        return False
    provenance = _json_mapping(row_map.get("provenance_json"), field_name="provenance_json")
    mode = provenance.get("replacement_q_mode")
    if not isinstance(mode, str) or not mode:
        return False
    return mode in _REPLACEMENT_Q_MODE_LIVE_ELIGIBLE


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


def _current_market_bin_topology_hash(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
) -> str | None:
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
    if not rows:
        return None
    topology: list[dict[str, object]] = []
    for row in rows:
        label = str(row["range_label"] or row["outcome"] or "").strip()
        if not label:
            return None
        city_cfg = cities_by_name.get(city)
        settlement_unit = str(getattr(city_cfg, "settlement_unit", "") or getattr(city_cfg, "unit", "") or "").strip().upper()
        if settlement_unit not in {"C", "F"}:
            settlement_unit = _display_unit_for_label(label, fallback="C")
        display_unit = _display_unit_for_label(label, fallback=settlement_unit)
        rounding_rule = "oracle_truncate" if str(getattr(city_cfg, "settlement_source_type", "") or "") == "hko" else "wmo_half_up"
        settlement_step_c = 5.0 / 9.0 if settlement_unit == "F" else 1.0
        lower_c = _temperature_bound_to_c(row["range_low"], unit=display_unit)
        upper_c = _temperature_bound_to_c(row["range_high"], unit=display_unit)
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
    return _json_hash(topology)


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
    if not rows:
        return None
    topology: list[dict[str, object]] = []
    for row in rows:
        label = str(row["range_label"] or row["outcome"] or "").strip()
        if not label:
            return None
        city_cfg = cities_by_name.get(city)
        settlement_unit = str(getattr(city_cfg, "settlement_unit", "") or getattr(city_cfg, "unit", "") or "").strip().upper()
        if settlement_unit not in {"C", "F"}:
            settlement_unit = _display_unit_for_label(label, fallback="C")
        display_unit = _display_unit_for_label(label, fallback=settlement_unit)
        rounding_rule = "oracle_truncate" if str(getattr(city_cfg, "settlement_source_type", "") or "") == "hko" else "wmo_half_up"
        settlement_step_c = 5.0 / 9.0 if settlement_unit == "F" else 1.0
        lower_c = _temperature_bound_to_c(row["range_low"], unit=display_unit)
        upper_c = _temperature_bound_to_c(row["range_high"], unit=display_unit)
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
) -> ReplacementForecastBundleReadResult:
    """Read a derived replacement posterior only after B0 executable proof exists."""

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

    # H3 (REAUDIT_0_1.md §2) — HARD staleness gate, fail-closed. readiness.expires_at
    # was previously loaded but never compared. A READY posterior whose expiry is at
    # or before decision_time is DEAD; binding it as live authority is the inverse of
    # the zero-trade fault (trading a stale forecast as live). This gate is in the
    # ONE bundle reader so both the live 0.1 path and the legacy hook inherit it.
    if readiness.expires_at is not None and readiness.expires_at <= decision_utc:
        return ReplacementForecastBundleReadResult(
            "BLOCKED", "REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED"
        )

    # TRADEABLE-LATEST read semantics (operator clobber-category directive 2026-06-10).
    # Fetch the scope's posterior rows newest-first, then prefer the latest TRADEABLE-GRADE
    # row over a newer bounds-less SHADOW row. A newer cycle that has anchor manifests but no
    # fusion instruments writes a bounds-less U0R_CAPTURE_MISSING row by design; absolute-latest
    # selection would serve it and collapse live eligibility. The newer bounds-less row stays
    # visible for shadow/telemetry (it is still in the table); the LIVE bundle just does not
    # bind to it. Bound the scan to a small recent window — a healthy scope has 1-3 rows per
    # cycle, and the staleness gate below caps how far back a served row may sit anyway.
    candidate_rows = conn.execute(
        """
        SELECT * FROM forecast_posteriors
        WHERE city = ?
          AND target_date = ?
          AND temperature_metric = ?
          AND source_id = ?
          AND product_id = ?
          AND data_version = ?
          AND training_allowed = 0
          AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
        ORDER BY computed_at DESC, posterior_id DESC
        LIMIT 16
        """,
        (city, target_date_text, metric, SOURCE_ID, PRODUCT_ID, data_version),
    ).fetchall()
    if not candidate_rows:
        return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_MISSING")
    latest_row_map = dict(candidate_rows[0])
    tradeable_row_map: dict | None = None
    for candidate in candidate_rows:
        candidate_map = dict(candidate)
        if _row_is_tradeable_grade(candidate_map):
            tradeable_row_map = candidate_map
            break
    # Serve the latest tradeable-grade row when one exists; otherwise fall through with the
    # absolute-latest row so a scope that has ONLY a bounds-less/non-eligible row still flows
    # through the downstream gates and is BLOCKED with its honest reason (never silently dropped).
    if tradeable_row_map is not None:
        row_map = tradeable_row_map
    else:
        row_map = latest_row_map
    # Fallback case: we are serving an OLDER tradeable row because a NEWER non-tradeable shadow
    # row sits on top. The scope readiness (per-scope upsert) now points at that newer shadow
    # row, so the readiness/dependency-agreement gates below must be re-anchored to the SERVED
    # row's OWN intrinsic provenance (immutable, validated at its materialization) rather than
    # to the overwritten scope readiness. We still enforce EVERY intrinsic-integrity gate on the
    # served row (staleness, intermediate-cycle, topology-to-current-market, identity hashes).
    _served_via_tradeable_fallback = (
        tradeable_row_map is not None
        and int(row_map["posterior_id"]) != int(latest_row_map["posterior_id"])
    )
    _newer_shadow_posterior_id = (
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
    if cycle_age_exceeds_bound(decision_utc, _source_cycle_utc):
        return ReplacementForecastBundleReadResult(
            "BLOCKED", "REPLACEMENT_0_1_LIVE_AUTHORITY_READINESS_EXPIRED"
        )

    # Operator cycle-physics directive 2026-06-10 — intermediate-cycle (06/18Z) live gate.
    # The de-bias + fusion weights were trained on ~99% 00Z-cycle history, so a posterior
    # built on an intermediate cycle applies a bias correction across cycle phase. Such a
    # posterior is still PRODUCED + readiness-stamped (production stays alive in dead zones)
    # but is admitted LIVE only when the operator flag is on (default OFF = shadow-only).
    # We prefer the explicit provenance tag (recorded at materialization), falling back to
    # the source_cycle_time hour so a pre-tag posterior is still classified fail-closed.
    _phase_provenance = _json_mapping(row_map.get("provenance_json"), field_name="provenance_json")
    _cycle_phase = str(_phase_provenance.get("cycle_phase") or "").strip().lower()
    if not _cycle_phase:
        _cycle_phase = classify_cycle_phase(_source_cycle_utc)
    if (
        _cycle_phase == CYCLE_PHASE_INTERMEDIATE
        and not _replacement_intermediate_cycle_live_admission_enabled()
    ):
        return ReplacementForecastBundleReadResult(
            "BLOCKED", "REPLACEMENT_0_1_LIVE_AUTHORITY_INTERMEDIATE_CYCLE_SHADOW_ONLY"
        )

    dependency_json = _json_mapping(row_map["dependency_source_run_ids_json"], field_name="dependency_source_run_ids_json")
    if _served_via_tradeable_fallback:
        # Re-anchor the certification to the SERVED tradeable row's intrinsic provenance. The
        # scope readiness was overwritten in place by the newer shadow cycle's materialization
        # (readiness_state upserts on scope_key — no cycle in the key), so it no longer points at
        # this row. The served row is self-certifying: it was materialized WITH its own READY
        # readiness (a U0R/bounds-less row is never tradeable-grade), and it carries its own
        # immutable dependency_source_run_ids_json + identity hashes. We bind the bundle's
        # baseline_source_run_id to the served row's intrinsic baseline (not the overwritten
        # readiness's), and we DO NOT require the scope readiness to point at this posterior.
        # Every intrinsic-integrity gate (staleness, intermediate-cycle, topology-to-market,
        # identity-hash presence) has already run / still runs against this served row, so the
        # no-bypass guarantee is preserved — only the (stale) readiness pointer is relaxed.
        intrinsic_baseline = dependency_json.get("baseline_b0")
        if isinstance(intrinsic_baseline, str) and intrinsic_baseline:
            baseline_run_id = intrinsic_baseline
    else:
        posterior_dependency = _readiness_dependency_by_role(readiness, "soft_anchor_posterior")
        if posterior_dependency is None or int(posterior_dependency.get("posterior_id") or -1) != int(row_map["posterior_id"]):
            return ReplacementForecastBundleReadResult("BLOCKED", "REPLACEMENT_POSTERIOR_READINESS_MISMATCH")
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
    provenance = _json_mapping(row_map["provenance_json"], field_name="provenance_json")
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
        # older tradeable row because a newer bounds-less shadow row clobbered the latest slot.
        provenance = {
            **provenance,
            "tradeable_latest_selection": {
                "served_posterior_id": int(row_map["posterior_id"]),
                "newer_shadow_posterior_id": _newer_shadow_posterior_id,
                "reason": "newer_row_not_tradeable_grade_served_older_certified_bounds",
            },
        }
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
        trade_authority_status=str(row_map["trade_authority_status"]),
    )
    return ReplacementForecastBundleReadResult(READY_STATUS, "REPLACEMENT_POSTERIOR_READY", bundle)
