# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742) + Phase 3 T2 (2026-05-21): +6 SHOULDER_* members per 04_PHASE_3_SHOULDER.md

"""
NoTradeReason — canonical StrEnum covering every rejection_reasons=[...] callsite
in src/engine/evaluator.py.

Two-tier model (§5.1): NoTradeReason is the CATEGORY (enum CHECK at DB write);
reason_detail TEXT carries the per-rejection diagnostic string (no constraint).

65 members cover 69 callsites (4 shared: ENS_FETCH_INSUFFICIENT_MEMBERS×2,
DAY0_NO_FORECAST_HOURS_REMAIN×2, CROSSCHECK_UNAVAILABLE×3, POLICY_GATED×2 —
see T2_NO_TRADE_EVENTS_SCAFFOLD.md §3 for the full 69-row migration plan).

UNCATEGORIZED is the §13 fallback for unclassified callsites discovered
at production-pass time.

SCAFFOLD — production wiring happens in PR-T2 (T2 production pass).
"""

from __future__ import annotations

from enum import auto
from enum import StrEnum


class NoTradeReason(StrEnum):
    # ── Observation / Day0 data availability ──────────────────────────────────
    DAY0_OBSERVATION_UNAVAILABLE = auto()
    DAY0_LOW_OBSERVATION_UNAVAILABLE = auto()
    DAY0_CURRENT_OBS_UNAVAILABLE = auto()
    DAY0_NO_FORECAST_HOURS_REMAIN = auto()
    DAY0_LOW_CAUSALITY_REJECTED = auto()
    DAY0_FORECAST_INSUFFICIENT_MEMBERS = auto()
    SOLAR_DST_CONTEXT_UNAVAILABLE = auto()

    # ── Observation source / quality gates ────────────────────────────────────
    OBSERVATION_SOURCE_UNAUTHORIZED = auto()
    OBSERVATION_QUALITY_REJECTED = auto()
    ENTRY_FORECAST_ROLLOUT_BLOCKED = auto()

    # ── Support / bin topology ─────────────────────────────────────────────────
    INVALID_SUPPORT_INDEX = auto()
    SUPPORT_INDEX_MISMATCH = auto()
    INSUFFICIENT_BINS = auto()
    BIN_TOPOLOGY_INVALID = auto()
    NO_EXECUTABLE_BINS = auto()

    # ── Entry forecast reader ──────────────────────────────────────────────────
    ENTRY_FORECAST_READER_DB_UNAVAILABLE = auto()
    ENTRY_FORECAST_READER_REJECTED = auto()

    # ── ENS fetch / signal construction ───────────────────────────────────────
    ENS_SOURCE_NOT_ENABLED = auto()
    ENS_FETCH_FAILED = auto()
    ENS_FETCH_INSUFFICIENT_MEMBERS = auto()
    ENS_INSUFFICIENT_REQUIRED_HOUR_MEMBERS = auto()
    ENS_TIMES_PARSE_ERROR = auto()
    ENS_SIGNAL_CONSTRUCTION_FAILED = auto()
    ENS_SNAPSHOT_PERSISTENCE_FAILED = auto()
    ENS_SNAPSHOT_P_RAW_PERSISTENCE_FAILED = auto()

    # ── Forecast source / evidence ─────────────────────────────────────────────
    FORECAST_SOURCE_DEGRADED = auto()
    FORECAST_EVIDENCE_INCOMPLETE = auto()
    UNKNOWN_FORECAST_SOURCE_FAMILY = auto()
    FORECAST_PROVENANCE_INCOMPLETE = auto()
    FORECAST_PROVENANCE_INCONSISTENT = auto()

    # ── Raw probability validity ───────────────────────────────────────────────
    P_RAW_INVALID = auto()
    DT7_BOUNDARY_DAY_AMBIGUOUS = auto()
    EXECUTABLE_FORECAST_MEMBERS_UNIT_MISMATCH = auto()
    EXECUTABLE_FORECAST_MEMBER_EXTREMA_INVALID = auto()

    # ── Authority / calibration ────────────────────────────────────────────────
    AUTHORITY_GATE_DB_FAULT = auto()
    INSUFFICIENT_VERIFIED_CALIBRATION = auto()
    UNSUPPORTED_CALIBRATION_SOURCE_ID = auto()
    P_CAL_INVALID = auto()
    CALIBRATION_MATURITY_INVALID = auto()
    CALIBRATION_IMMATURE_NO_PLATT = auto()
    NATIVE_MULTIBIN_BUY_NO_FLAG_INVALID = auto()

    # ── Market liquidity / crosscheck ─────────────────────────────────────────
    MARKET_EMPTY_ORDERBOOK = auto()
    MARKET_LIQUIDITY_ERROR = auto()
    CROSSCHECK_UNAVAILABLE = auto()
    GFS_CROSSCHECK_UNAVAILABLE = auto()
    MODEL_CONFLICT = auto()

    # ── Strategy / alpha target ────────────────────────────────────────────────
    ALPHA_TARGET_MISMATCH = auto()
    AUTHORITY_VIOLATION = auto()
    SELECTED_EDGE_MISSING_SUPPORT_INDEX = auto()
    SELECTED_EDGE_NO_TOKEN_PAYLOAD = auto()
    STRATEGY_KEY_UNCLASSIFIED = auto()
    CONFIDENCE_BAND_INSUFFICIENT = auto()
    CENTER_BUY_ULTRA_LOW_PRICE = auto()

    # ── Anti-churn / cooldown / position guards ────────────────────────────────
    REENTRY_BLOCKED = auto()
    TOKEN_COOLDOWN = auto()
    ALREADY_HELD_SAME_TOKEN = auto()

    # ── Oracle / DDD ───────────────────────────────────────────────────────────
    ORACLE_BLACKLISTED = auto()
    DDD_FAIL_CLOSED = auto()
    DDD_RAIL1_HALT = auto()

    # ── Sizing / execution price ───────────────────────────────────────────────
    KELLY_SIZING_ERROR = auto()
    POLICY_GATED = auto()
    EXECUTION_PRICE_FEE_RATE_UNAVAILABLE = auto()
    EXECUTION_PRICE_SIZING_ERROR = auto()
    SIZE_BELOW_MINIMUM = auto()
    STRATEGY_ECONOMIC_FLOOR = auto()
    PASSIVE_FILL_MODEL_MISSING = auto()
    ULTRA_LOW_PRICE_NOT_AUTHORIZED = auto()
    SUBSTRATE_TOPOLOGY_INCOMPLETE = auto()
    SNAPSHOT_CAPTURE_SEMANTIC_MISMATCH = auto()
    PARTIAL_SOURCE_QUALITY_REJECTED = auto()
    RISK_LIMITS_EXCEEDED = auto()
    MUTUALLY_EXCLUSIVE_FAMILY_DEDUP = auto()

    # ── Shoulder strategy gates (Phase 3 T2) ─────────────────────────────────
    # 6 SHOULDER_* members per 04_PHASE_3_SHOULDER.md §"NoTradeReason additions"
    SHOULDER_STRESS_FAIL = auto()
    SHOULDER_REGIME_MISMATCH = auto()
    SHOULDER_NATIVE_NO_DEPTH_INSUFFICIENT = auto()
    SHOULDER_DAY0_BOUND_NOT_ELIMINATED = auto()
    SHOULDER_NO_TRADE_GATE = auto()
    SHOULDER_CLUSTER_CAP_EXCEEDED = auto()

    # ── Fallback (§13) ────────────────────────────────────────────────────────
    UNCATEGORIZED = auto()
