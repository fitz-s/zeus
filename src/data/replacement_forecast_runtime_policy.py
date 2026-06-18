"""Runtime policy resolver for the live replacement forecast path."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


STRATEGY_KEY = "openmeteo_ecmwf_ifs9_bayes_fusion"
TRADE_AUTHORITY_FLAG = "openmeteo_ecmwf_ifs9_bayes_fusion_live_enabled"
KELLY_INCREASE_FLAG = "openmeteo_ecmwf_ifs9_bayes_fusion_kelly_increase_enabled"
DIRECTION_FLIP_FLAG = "openmeteo_ecmwf_ifs9_bayes_fusion_direction_flip_enabled"
REQUIRED_FLAGS = (
    TRADE_AUTHORITY_FLAG,
    KELLY_INCREASE_FLAG,
    DIRECTION_FLIP_FLAG,
)
SAFE_DEFAULT_STATUS = "DISABLED"
BLOCKED_STATUS = "BLOCKED"
LIVE_STATUS = "live"
EXPECTED_ANCHOR_WEIGHT = 0.80
EXPECTED_ANCHOR_SIGMA_C = 3.00
MIN_PROMOTION_GUARDRAIL_BUCKET_ROWS = 20
EXPECTED_CAPITAL_OBJECTIVE_LABEL = "openmeteo_ecmwf_ifs9_bayes_fusion"


@dataclass(frozen=True)
class ReplacementForecastPromotionEvidence:
    official_days: int
    official_rows: int
    after_cost_pnl: float
    q_lcb_coverage: float
    anti_lookahead_violations: int
    source_availability_violations: int
    unresolved_regression_clusters: int
    same_clob_replay_passed: bool
    nested_walk_forward_passed: bool
    same_clob_replay_scored_rows: int = 0
    same_clob_replay_blocked_rows: int = 0
    fee_depth_fill_evidence_passed: bool = False
    unit_pnl_only: bool = True
    nested_holdout_brier: float | None = None
    nested_holdout_log_loss: float | None = None
    nested_selected_anchor_weight: float | None = None
    nested_selected_anchor_sigma_c: float | None = None
    nested_guardrail_bucket_count: int = 0
    nested_guardrail_bucket_min_rows: int = 0
    product_specific_refit_passed: bool = False

    def blocking_reason_codes(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.official_days < 5:
            reasons.append("REPLACEMENT_PROMOTION_INSUFFICIENT_OFFICIAL_DAYS")
        if self.official_rows < 250:
            reasons.append("REPLACEMENT_PROMOTION_INSUFFICIENT_OFFICIAL_ROWS")
        if self.after_cost_pnl <= 0.0:
            reasons.append("REPLACEMENT_PROMOTION_AFTER_COST_PNL_NOT_POSITIVE")
        if self.q_lcb_coverage < 0.95:
            reasons.append("REPLACEMENT_PROMOTION_Q_LCB_COVERAGE_TOO_LOW")
        if self.anti_lookahead_violations != 0:
            reasons.append("REPLACEMENT_PROMOTION_ANTI_LOOKAHEAD_VIOLATIONS")
        if self.source_availability_violations != 0:
            reasons.append("REPLACEMENT_PROMOTION_SOURCE_AVAILABILITY_VIOLATIONS")
        if self.unresolved_regression_clusters != 0:
            reasons.append("REPLACEMENT_PROMOTION_UNRESOLVED_REGRESSION_CLUSTERS")
        if not self.same_clob_replay_passed:
            reasons.append("REPLACEMENT_PROMOTION_SAME_CLOB_REPLAY_NOT_PASSED")
        if self.same_clob_replay_scored_rows < self.official_rows:
            reasons.append("REPLACEMENT_PROMOTION_SAME_CLOB_REPLAY_INCOMPLETE")
        if self.same_clob_replay_blocked_rows != 0:
            reasons.append("REPLACEMENT_PROMOTION_SAME_CLOB_REPLAY_BLOCKED_ROWS")
        if not self.fee_depth_fill_evidence_passed:
            reasons.append("REPLACEMENT_PROMOTION_FEE_DEPTH_FILL_EVIDENCE_MISSING")
        if self.unit_pnl_only:
            reasons.append("REPLACEMENT_PROMOTION_UNIT_PNL_ONLY")
        if not self.nested_walk_forward_passed:
            reasons.append("REPLACEMENT_PROMOTION_NESTED_WALK_FORWARD_NOT_PASSED")
        if not _finite_nonnegative(self.nested_holdout_brier):
            reasons.append("REPLACEMENT_PROMOTION_NESTED_BRIER_MISSING")
        if not _finite_nonnegative(self.nested_holdout_log_loss):
            reasons.append("REPLACEMENT_PROMOTION_NESTED_LOG_LOSS_MISSING")
        if not _matches_expected(self.nested_selected_anchor_weight, EXPECTED_ANCHOR_WEIGHT):
            reasons.append("REPLACEMENT_PROMOTION_ANCHOR_WEIGHT_MISMATCH")
        if not _matches_expected(self.nested_selected_anchor_sigma_c, EXPECTED_ANCHOR_SIGMA_C):
            reasons.append("REPLACEMENT_PROMOTION_ANCHOR_SIGMA_MISMATCH")
        if self.nested_guardrail_bucket_count < 1:
            reasons.append("REPLACEMENT_PROMOTION_GUARDRAIL_BUCKETS_MISSING")
        if self.nested_guardrail_bucket_min_rows < MIN_PROMOTION_GUARDRAIL_BUCKET_ROWS:
            reasons.append("REPLACEMENT_PROMOTION_GUARDRAIL_BUCKET_ROWS_INSUFFICIENT")
        if not self.product_specific_refit_passed:
            reasons.append("REPLACEMENT_PROMOTION_PRODUCT_SPECIFIC_REFIT_MISSING")
        return tuple(reasons)

    def promotion_allowed(self) -> bool:
        return not self.blocking_reason_codes()


@dataclass(frozen=True)
class ReplacementForecastCapitalObjectiveEvidence:
    selected_label: str
    replay_status: str
    after_cost_pnl: float
    source_availability_observed: bool
    source_availability_violations: int
    anti_lookahead_violations: int
    same_clob_replay_passed: bool
    fee_depth_fill_evidence_passed: bool
    unit_pnl_only: bool
    product_specific_refit_passed: bool

    def blocking_reason_codes(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if self.selected_label != EXPECTED_CAPITAL_OBJECTIVE_LABEL:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SELECTED_LABEL_MISMATCH")
        if self.replay_status != "EMPIRICAL_WINNER":
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_REPLAY_NOT_EMPIRICAL_WINNER")
        if self.after_cost_pnl <= 0.0:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_AFTER_COST_PNL_NOT_POSITIVE")
        if not self.source_availability_observed:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_NOT_OBSERVED")
        if self.source_availability_violations != 0:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_VIOLATIONS")
        if self.anti_lookahead_violations != 0:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_ANTI_LOOKAHEAD_VIOLATIONS")
        if not self.same_clob_replay_passed:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SAME_CLOB_REPLAY_NOT_PASSED")
        if not self.fee_depth_fill_evidence_passed:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_FEE_DEPTH_FILL_EVIDENCE_MISSING")
        if self.unit_pnl_only:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_UNIT_PNL_ONLY")
        if not self.product_specific_refit_passed:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_PRODUCT_SPECIFIC_REFIT_MISSING")
        return tuple(reasons)

    def capital_objective_allowed(self) -> bool:
        return not self.blocking_reason_codes()


def replacement_live_evidence_gate(
    promotion_evidence: ReplacementForecastPromotionEvidence | None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None,
) -> tuple[bool, tuple[str, ...]]:
    """The single shared evidence gate for replacement live (replacement_0_1).

    REAUDIT_0_1.md §1.1 (re-pointed FIX-1): ONE pure predicate, co-located with
    the evidence dataclasses (NO new module, NO second loader). It performs NO IO
    (takes already-constructed dataclasses), so it cannot drift from
    ``promotion_allowed()`` / ``capital_objective_allowed()`` — it reuses their
    ``blocking_reason_codes()``. Consulted by BOTH the live 0.1 probability path
    (event_reactor_adapter._replacement_authority_probability_and_fdr_proof,
    Insertion A) AND ``resolve_replacement_forecast_runtime_policy`` (Insertion B)
    so there is ONE gate, one truth (iron rule #4).

    Returns ``(permitted, reason_codes)``:
      - promotion_evidence is None  -> (False, (PROMOTION_EVIDENCE_REQUIRED,))
      - capital_objective_evidence is None -> (False, (CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED,))
      - either disallows -> (False, union of both blocking_reason_codes())
      - else -> (True, ())

    Overconfidence = ruin: promotion evidence (statistical validation) and
    capital-objective evidence (empirical winner + after-cost EV) are DIFFERENT
    proofs; a single passing proof is necessary but NOT sufficient to risk capital.
    """

    if promotion_evidence is None:
        return (False, ("REPLACEMENT_LIVE_PROMOTION_EVIDENCE_REQUIRED",))
    if capital_objective_evidence is None:
        return (False, ("REPLACEMENT_LIVE_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED",))
    blocking = (
        promotion_evidence.blocking_reason_codes()
        + capital_objective_evidence.blocking_reason_codes()
    )
    if blocking:
        return (False, blocking)
    return (True, ())


def _finite_nonnegative(value: float | None) -> bool:
    return value is not None and math.isfinite(float(value)) and float(value) >= 0.0


def _matches_expected(value: float | None, expected: float) -> bool:
    return value is not None and math.isfinite(float(value)) and abs(float(value) - expected) <= 1e-9


@dataclass(frozen=True)
class ReplacementForecastRuntimePolicy:
    status: str
    reason_codes: tuple[str, ...]
    trade_authority_enabled: bool
    kelly_increase_enabled: bool
    direction_flip_enabled: bool
    strategy_key: str = STRATEGY_KEY

    @property
    def can_initiate_trade(self) -> bool:
        return self.status == LIVE_STATUS and self.trade_authority_enabled

    @property
    def can_increase_kelly(self) -> bool:
        return self.status == LIVE_STATUS and self.kelly_increase_enabled

    @property
    def can_flip_direction(self) -> bool:
        return self.status == LIVE_STATUS and self.direction_flip_enabled


def _strict_bool(flags: Mapping[str, object], key: str) -> bool:
    if key not in flags:
        raise KeyError(f"missing replacement forecast feature flag: {key}")
    value = flags[key]
    if not isinstance(value, bool):
        raise TypeError(f"replacement forecast feature flag {key} must be bool")
    return value


def resolve_replacement_forecast_runtime_policy(
    flags: Mapping[str, object],
    *,
    promotion_evidence: ReplacementForecastPromotionEvidence | None = None,
    capital_objective_evidence: ReplacementForecastCapitalObjectiveEvidence | None = None,
) -> ReplacementForecastRuntimePolicy:
    """Resolve the only allowed runtime authority state from strict feature flags."""

    trade_authority = _strict_bool(flags, TRADE_AUTHORITY_FLAG)
    kelly_increase = _strict_bool(flags, KELLY_INCREASE_FLAG)
    direction_flip = _strict_bool(flags, DIRECTION_FLIP_FLAG)

    reasons: list[str] = []
    if not trade_authority and (kelly_increase or direction_flip):
        reasons.append("REPLACEMENT_TRADE_AUTHORITY_REQUIRED_FOR_DANGEROUS_FLAGS")
    if direction_flip and not kelly_increase:
        reasons.append("REPLACEMENT_DIRECTION_FLIP_REQUIRES_KELLY_AUTHORITY")

    # Runtime policy is live-or-blocked. Diagnostic production may exist for
    # observability, but it is not an intermediate trading state and cannot
    # authorize reader or reactor admission.
    if reasons:
        return ReplacementForecastRuntimePolicy(
            status=BLOCKED_STATUS,
            reason_codes=tuple(reasons),
            trade_authority_enabled=False,
            kelly_increase_enabled=False,
            direction_flip_enabled=False,
        )
    if not trade_authority:
        status = SAFE_DEFAULT_STATUS
        reason_codes = ("REPLACEMENT_TRADE_AUTHORITY_DISABLED",)
    else:
        status = LIVE_STATUS
        reason_codes = ("REPLACEMENT_LIVE_ENABLED",)
    return ReplacementForecastRuntimePolicy(
        status=status,
        reason_codes=reason_codes,
        trade_authority_enabled=trade_authority if status == LIVE_STATUS else False,
        kelly_increase_enabled=kelly_increase if status == LIVE_STATUS else False,
        direction_flip_enabled=direction_flip if status == LIVE_STATUS else False,
    )
