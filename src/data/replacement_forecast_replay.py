"""Same-CLOB after-cost replay primitives for replacement forecast evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping


VERIFIED_TRUTH_AUTHORITY = "VERIFIED"
REPLAY_STATUS_SCORED = "SCORED"
REPLAY_STATUS_BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ReplacementForecastSameClobReplayInput:
    city: str
    target_date: str
    temperature_metric: str
    condition_id: str
    token_id: str
    yes_token_id: str
    no_token_id: str
    baseline_market_snapshot_id: str
    replacement_market_snapshot_id: str
    decision_time: datetime | str
    baseline_would_trade: bool
    replacement_allows_trade: bool
    direction: str
    entry_price: float
    fee_per_share: float
    slippage_per_share: float
    requested_notional_usd: float
    available_depth_shares: float
    fill_probability: float
    min_order_usd: float
    tick_size: float
    exit_liquidity_available_shares: float
    exit_fill_probability: float
    exit_slippage_per_share: float
    settlement_token_wins: bool
    truth_authority: str
    source_available_at_by_role: Mapping[str, datetime | str]
    processed_at_by_role: Mapping[str, datetime | str]
    derived_posterior_available_at: datetime | str
    q_b0: float
    q_replacement: float
    q_lcb_b0: float
    q_lcb_replacement: float
    veto_reason: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "city",
            "target_date",
            "temperature_metric",
            "condition_id",
            "token_id",
            "yes_token_id",
            "no_token_id",
            "baseline_market_snapshot_id",
            "replacement_market_snapshot_id",
            "direction",
            "truth_authority",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if self.temperature_metric not in {"high", "low"}:
            raise ValueError("temperature_metric must be high or low")
        if self.direction not in {"buy_yes", "buy_no"}:
            raise ValueError("direction must be buy_yes or buy_no")
        if self.yes_token_id == self.no_token_id:
            raise ValueError("yes_token_id and no_token_id must be distinct")
        expected_token_id = self.yes_token_id if self.direction == "buy_yes" else self.no_token_id
        if self.token_id != expected_token_id:
            raise ValueError("token_id must be the native token for direction")
        for field_name in ("entry_price", "fill_probability", "exit_fill_probability", "q_b0", "q_replacement", "q_lcb_b0", "q_lcb_replacement"):
            value = float(getattr(self, field_name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be in [0, 1]")
        for field_name in (
            "fee_per_share",
            "slippage_per_share",
            "requested_notional_usd",
            "available_depth_shares",
            "min_order_usd",
            "exit_liquidity_available_shares",
            "exit_slippage_per_share",
        ):
            value = float(getattr(self, field_name))
            if value < 0.0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.tick_size <= 0.0:
            raise ValueError("tick_size must be positive")
        if not self.source_available_at_by_role:
            raise ValueError("source_available_at_by_role is required")
        if not self.processed_at_by_role:
            raise ValueError("processed_at_by_role is required")


@dataclass(frozen=True)
class ReplacementForecastSameClobReplayResult:
    status: str
    reason_codes: tuple[str, ...]
    market_snapshot_id: str | None
    baseline_after_cost_pnl: float
    replacement_after_cost_pnl: float
    replacement_delta_after_cost_pnl: float
    filled_shares: float
    exit_liquidity_available_shares: float
    exit_fill_probability: float
    all_in_price: float
    same_clob_snapshot: bool
    truth_authority: str
    source_available_at_max: str | None
    processed_at_max: str | None
    derived_posterior_available_at: str | None
    veto_applied: bool
    veto_reason: str | None

    @property
    def scored(self) -> bool:
        return self.status == REPLAY_STATUS_SCORED


def _to_utc(value: datetime | str, *, field_name: str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _blocked(
    row: ReplacementForecastSameClobReplayInput,
    reasons: list[str],
    source_available_at_max: datetime | None,
    processed_at_max: datetime | None,
    derived_posterior_available_at: datetime | None,
) -> ReplacementForecastSameClobReplayResult:
    return ReplacementForecastSameClobReplayResult(
        status=REPLAY_STATUS_BLOCKED,
        reason_codes=tuple(dict.fromkeys(reasons)),
        market_snapshot_id=row.baseline_market_snapshot_id if row.baseline_market_snapshot_id == row.replacement_market_snapshot_id else None,
        baseline_after_cost_pnl=0.0,
        replacement_after_cost_pnl=0.0,
        replacement_delta_after_cost_pnl=0.0,
        filled_shares=0.0,
        exit_liquidity_available_shares=row.exit_liquidity_available_shares,
        exit_fill_probability=row.exit_fill_probability,
        all_in_price=row.entry_price + row.fee_per_share + row.slippage_per_share + row.exit_slippage_per_share,
        same_clob_snapshot=row.baseline_market_snapshot_id == row.replacement_market_snapshot_id,
        truth_authority=row.truth_authority,
        source_available_at_max=source_available_at_max.isoformat() if source_available_at_max is not None else None,
        processed_at_max=processed_at_max.isoformat() if processed_at_max is not None else None,
        derived_posterior_available_at=derived_posterior_available_at.isoformat() if derived_posterior_available_at is not None else None,
        veto_applied=row.baseline_would_trade and not row.replacement_allows_trade,
        veto_reason=row.veto_reason,
    )


def score_replacement_forecast_same_clob_replay(row: ReplacementForecastSameClobReplayInput) -> ReplacementForecastSameClobReplayResult:
    """Score one replacement veto/allow row only when replay-grade evidence is present."""

    decision_time = _to_utc(row.decision_time, field_name="decision_time")
    source_times = {
        role: _to_utc(value, field_name=f"source_available_at_by_role.{role}")
        for role, value in row.source_available_at_by_role.items()
    }
    processed_times = {
        role: _to_utc(value, field_name=f"processed_at_by_role.{role}")
        for role, value in row.processed_at_by_role.items()
    }
    derived_posterior_available_at = _to_utc(
        row.derived_posterior_available_at,
        field_name="derived_posterior_available_at",
    )
    source_available_at_max = max(source_times.values()) if source_times else None
    processed_at_max = max(processed_times.values()) if processed_times else None
    reasons: list[str] = []
    required_roles = {"baseline_b0", "aifs_sampled_2t", "openmeteo_ifs9_anchor", "soft_anchor_posterior"}
    missing_roles = sorted(required_roles - set(source_times))
    if missing_roles:
        reasons.append("REPLACEMENT_REPLAY_SOURCE_AVAILABILITY_MISSING")
    missing_processed_roles = sorted(required_roles - set(processed_times))
    if missing_processed_roles:
        reasons.append("REPLACEMENT_REPLAY_PROCESSED_AT_MISSING")
    if row.baseline_market_snapshot_id != row.replacement_market_snapshot_id:
        reasons.append("REPLACEMENT_REPLAY_NOT_SAME_CLOB_SNAPSHOT")
    if row.truth_authority != VERIFIED_TRUTH_AUTHORITY:
        reasons.append("REPLACEMENT_REPLAY_REQUIRES_OFFICIAL_VERIFIED_TRUTH")
    if source_available_at_max is not None and source_available_at_max > decision_time:
        reasons.append("REPLACEMENT_REPLAY_SOURCE_AFTER_DECISION_TIME")
    if processed_at_max is not None and processed_at_max > decision_time:
        reasons.append("REPLACEMENT_REPLAY_PROCESSED_AFTER_DECISION_TIME")
    if derived_posterior_available_at > decision_time:
        reasons.append("REPLACEMENT_REPLAY_DERIVED_POSTERIOR_AFTER_DECISION_TIME")
    if source_available_at_max is not None and derived_posterior_available_at < source_available_at_max:
        reasons.append("REPLACEMENT_REPLAY_DERIVED_POSTERIOR_BEFORE_SOURCE_READY")
    if processed_at_max is not None and derived_posterior_available_at < processed_at_max:
        reasons.append("REPLACEMENT_REPLAY_DERIVED_POSTERIOR_BEFORE_PROCESSING_READY")
    all_in_price = row.entry_price + row.fee_per_share + row.slippage_per_share + row.exit_slippage_per_share
    if all_in_price <= 0.0 or all_in_price >= 1.0:
        reasons.append("REPLACEMENT_REPLAY_ALL_IN_PRICE_OUT_OF_RANGE")
    if row.available_depth_shares <= 0.0:
        reasons.append("REPLACEMENT_REPLAY_DEPTH_REQUIRED")
    if row.fill_probability <= 0.0:
        reasons.append("REPLACEMENT_REPLAY_FILL_PROBABILITY_REQUIRED")
    if row.exit_liquidity_available_shares <= 0.0:
        reasons.append("REPLACEMENT_REPLAY_EXIT_LIQUIDITY_REQUIRED")
    if row.exit_fill_probability <= 0.0:
        reasons.append("REPLACEMENT_REPLAY_EXIT_FILL_PROBABILITY_REQUIRED")
    if row.requested_notional_usd < row.min_order_usd:
        reasons.append("REPLACEMENT_REPLAY_MIN_ORDER_NOT_MET")
    ticks = row.entry_price / row.tick_size
    if abs(ticks - round(ticks)) > 1e-9:
        reasons.append("REPLACEMENT_REPLAY_ENTRY_PRICE_NOT_ON_TICK")
    if reasons:
        return _blocked(row, reasons, source_available_at_max, processed_at_max, derived_posterior_available_at)

    requested_shares = row.requested_notional_usd / row.entry_price
    depth_limited_shares = min(requested_shares, row.available_depth_shares)
    entry_filled_shares = depth_limited_shares * row.fill_probability
    exit_limited_shares = min(entry_filled_shares, row.exit_liquidity_available_shares)
    filled_shares = exit_limited_shares * row.exit_fill_probability
    token_win_pnl = 1.0 - all_in_price
    token_loss_pnl = -all_in_price
    pnl_per_share = token_win_pnl if row.settlement_token_wins else token_loss_pnl
    baseline_pnl = filled_shares * pnl_per_share if row.baseline_would_trade else 0.0
    replacement_pnl = filled_shares * pnl_per_share if (row.baseline_would_trade and row.replacement_allows_trade) else 0.0
    return ReplacementForecastSameClobReplayResult(
        status=REPLAY_STATUS_SCORED,
        reason_codes=("REPLACEMENT_REPLAY_SCORED_AFTER_COST_SAME_CLOB",),
        market_snapshot_id=row.baseline_market_snapshot_id,
        baseline_after_cost_pnl=baseline_pnl,
        replacement_after_cost_pnl=replacement_pnl,
        replacement_delta_after_cost_pnl=replacement_pnl - baseline_pnl,
        filled_shares=filled_shares,
        exit_liquidity_available_shares=row.exit_liquidity_available_shares,
        exit_fill_probability=row.exit_fill_probability,
        all_in_price=all_in_price,
        same_clob_snapshot=True,
        truth_authority=row.truth_authority,
        source_available_at_max=source_available_at_max.isoformat() if source_available_at_max is not None else None,
        processed_at_max=processed_at_max.isoformat() if processed_at_max is not None else None,
        derived_posterior_available_at=derived_posterior_available_at.isoformat(),
        veto_applied=row.baseline_would_trade and not row.replacement_allows_trade,
        veto_reason=row.veto_reason,
    )
