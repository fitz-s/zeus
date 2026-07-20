"""Order-intent immutability guard for replacement forecast output."""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.venue_submission_envelope import assert_live_order_unit_price


PASS_STATUS = "PASS"
BLOCK_STATUS = "BLOCK"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastIntentSurface:
    market_snapshot_id: str
    condition_id: str
    token_id: str
    direction: str
    limit_price: float
    kelly_fraction: float
    size_usd: float
    source: str = "baseline_intent"

    def __post_init__(self) -> None:
        for field_name in ("market_snapshot_id", "condition_id", "token_id", "direction", "source"):
            value = str(getattr(self, field_name) or "")
            if not value:
                raise ValueError(f"{field_name} is required")
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full replacement identity")
        if self.direction not in {"buy_yes", "buy_no", "sell_yes", "sell_no"}:
            raise ValueError("direction must be a native YES/NO direction")
        for field_name in ("limit_price", "kelly_fraction", "size_usd"):
            value = float(getattr(self, field_name))
            if value < 0.0:
                raise ValueError(f"{field_name} must be non-negative")
        assert_live_order_unit_price(self.limit_price)


@dataclass(frozen=True)
class ReplacementForecastIntentImmutabilityDecision:
    status: str
    reason_codes: tuple[str, ...]
    baseline: ReplacementForecastIntentSurface
    proposed: ReplacementForecastIntentSurface

    @property
    def allowed(self) -> bool:
        return self.status == PASS_STATUS


def validate_replacement_forecast_intent_immutability(
    *,
    baseline: ReplacementForecastIntentSurface,
    proposed: ReplacementForecastIntentSurface,
) -> ReplacementForecastIntentImmutabilityDecision:
    """Ensure replacement forecast output cannot reprice or retarget intent.

    The replacement path may reduce confidence before intent construction. Once
    an intent surface exists, the replacement artifact cannot change the native
    token, direction, market snapshot, or limit price. Size/Kelly may only stay
    equal or move downward.
    """

    if not isinstance(baseline, ReplacementForecastIntentSurface):
        raise TypeError("baseline must be ReplacementForecastIntentSurface")
    if not isinstance(proposed, ReplacementForecastIntentSurface):
        raise TypeError("proposed must be ReplacementForecastIntentSurface")
    reasons: list[str] = []
    if proposed.market_snapshot_id != baseline.market_snapshot_id:
        reasons.append("REPLACEMENT_INTENT_CLOB_SNAPSHOT_CHANGED")
    if proposed.condition_id != baseline.condition_id:
        reasons.append("REPLACEMENT_INTENT_CONDITION_CHANGED")
    if proposed.token_id != baseline.token_id:
        reasons.append("REPLACEMENT_INTENT_NATIVE_TOKEN_CHANGED")
    if proposed.direction != baseline.direction:
        reasons.append("REPLACEMENT_INTENT_DIRECTION_CHANGED")
    if abs(float(proposed.limit_price) - float(baseline.limit_price)) > 1e-12:
        reasons.append("REPLACEMENT_INTENT_LIMIT_PRICE_CHANGED")
    if float(proposed.kelly_fraction) > float(baseline.kelly_fraction) + 1e-15:
        reasons.append("REPLACEMENT_INTENT_KELLY_INCREASED")
    if float(proposed.size_usd) > float(baseline.size_usd) + 1e-9:
        reasons.append("REPLACEMENT_INTENT_SIZE_INCREASED")
    return ReplacementForecastIntentImmutabilityDecision(
        status=BLOCK_STATUS if reasons else PASS_STATUS,
        reason_codes=tuple(reasons or ("REPLACEMENT_INTENT_IMMUTABILITY_PASS",)),
        baseline=baseline,
        proposed=proposed,
    )
