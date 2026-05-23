# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §5.2 (sha 00c2399742) + Phase 3 T2 (2026-05-21): +6 SHOULDER_* members per 04_PHASE_3_SHOULDER.md + Phase 4 T2/T3 (2026-05-21): +4 candidate gates per 05_PHASE_4_FDR_FAMILY_CANDIDATES.md + Phase 4 T4 (2026-05-21): +2 T4 deferred candidate gates

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
    DAY0_NOWCAST_NOT_AUTHORIZED = auto()
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

    # ── Phase 4 T2/T3 candidate strategy gates ────────────────────────────────
    # 4 members per 05_PHASE_4_FDR_FAMILY_CANDIDATES.md §"NoTradeReason additions"
    STALE_QUOTE_FILL_INFEASIBLE = auto()       # stale_quote_detector: book hash stale post info-event
    RESOLUTION_DISPUTED = auto()               # resolution_window_maker: venue resolution status contested
    LIQPROV_HEARTBEAT_ABSENT = auto()          # liquidity_provision_with_heartbeat: fill_probability field absent
    WEATHER_ALERT_SOURCE_UNTRUSTED = auto()    # weather_event_arbitrage: external alert feed not wired/trusted
    RESOLUTION_TYPED_OUTCOME_UNAVAILABLE = auto()  # resolution_window_maker: typed SettlementOutcome not wired on context (data-gated)

    # ── Phase 4 T4 candidate strategy gates ────────────────────────────────────
    # 2 members per 05_PHASE_4_FDR_FAMILY_CANDIDATES.md §T4 deferred candidates
    CORR_HEDGE_REGIME_UNAVAILABLE = auto()     # cross_market_correlation_hedge: regime UNKNOWN or store not fit
    NEGRISK_FAMILY_INCOMPLETE = auto()         # neg_risk_basket: full token book per family unavailable
    NEGRISK_NO_PROFITABLE_BASKET = auto()      # neg_risk_basket: book present but max(Π_Y(q*),Π_N(q*)) <= 0

    # ── center_sell parity arb candidate gates ─────────────────────────────────
    CENTER_PAIR_PARITY_BOOK_UNAVAILABLE = auto()  # center_sell: binary_book_snapshot absent on analysis
    CENTER_PAIR_PARITY_NO_EDGE = auto()           # center_sell: a_YES+a_NO+fees >= 1 at q*; no deterministic arb

    # ── shoulder_buy_evt data-gate and theorem gate (2026-05-22) ──────────────
    # DATA-GATED: EVT tail model covariates or calibration set not yet wired.
    # Authority: STRATEGY_TAXONOMY_DIRECTIVE.md §8 + zeus_strategy_spec.md §12
    EVT_TAIL_MODEL_UNWIRED = auto()                     # shoulder_buy_evt: covariates/raw_prob/cal_set absent
    SHOULDER_BUY_LOWER_BOUND_NOT_POSITIVE = auto()      # shoulder_buy_evt: p⁻_u − a_YES − phi ≤ 0; no edge

    # ── shoulder_impossible_tail_capture data-gate (2026-05-22) ────────────────
    # DATA-GATED: physical envelope input (Δ_phys⁺/Δ_phys⁻ from station/season empirical
    # envelope) is not yet wired. Emitted until the envelope feed lands.
    # Authority: STRATEGY_TAXONOMY_DIRECTIVE.md §7 + zeus_strategy_spec.md §11.4
    PHYSICAL_ENVELOPE_UNWIRED = auto()            # shoulder_impossible_tail_capture: Δ_phys⁺/⁻ not wired

    # ── Physical bound theorem failure gate ────────────────────────────────────
    SHOULDER_PHYSICAL_BOUND_NOT_EXCLUDES_TAIL = auto()  # physical bound >= threshold; theorem fails

    # ── settlement_capture shadow: physical-interval theorem (STRATEGY_TAXONOMY_DIRECTIVE §1) ──
    PHYSICAL_INTERVAL_DATA_GATED = auto()      # settlement_capture_shadow: Δ_phys⁺/QC input absent → no_trade until data wired
    PHYSICAL_INTERVAL_OVERLAP = auto()         # settlement_capture_shadow: I_t overlaps B_i but neither ⊆ nor disjoint → ambiguous
    PHYSICAL_INTERVAL_UNPROFITABLE = auto()    # settlement_capture_shadow: I_t⊆B_i or disjoint but a+phi≥1 → no positive profit
    SETTLEMENT_CAPTURE_NOT_LOCKED = auto()     # settlement_capture_shadow: edge is not observation-locked (day0_nowcast scope)


    # ── Fallback (§13) ────────────────────────────────────────────────────────
    UNCATEGORIZED = auto()
