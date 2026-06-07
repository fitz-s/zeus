# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast shadow/veto output from repricing or retargeting order intents.
# Reuse: Run before wiring replacement forecast output near final intent or executor boundaries.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast order-intent immutability tests."""

from __future__ import annotations

import pytest

from src.engine.replacement_forecast_intent_immutability import (
    ReplacementForecastIntentSurface,
    validate_replacement_forecast_intent_immutability,
)


def _intent(**overrides) -> ReplacementForecastIntentSurface:
    values = {
        "market_snapshot_id": "snapshot-1",
        "condition_id": "condition-1",
        "token_id": "native-yes-token",
        "direction": "buy_yes",
        "limit_price": 0.42,
        "kelly_fraction": 0.04,
        "size_usd": 20.0,
        "source": "baseline_intent",
    }
    values.update(overrides)
    return ReplacementForecastIntentSurface(**values)


def test_replacement_intent_guard_allows_only_size_and_kelly_reduction() -> None:
    decision = validate_replacement_forecast_intent_immutability(
        baseline=_intent(),
        proposed=_intent(kelly_fraction=0.02, size_usd=10.0, source="replacement_veto_reduced_intent"),
    )

    assert decision.allowed is True
    assert decision.reason_codes == ("REPLACEMENT_INTENT_IMMUTABILITY_PASS",)


def test_replacement_intent_guard_blocks_reprice_or_native_token_change() -> None:
    decision = validate_replacement_forecast_intent_immutability(
        baseline=_intent(),
        proposed=_intent(limit_price=0.41, token_id="native-no-token"),
    )

    assert decision.allowed is False
    assert "REPLACEMENT_INTENT_LIMIT_PRICE_CHANGED" in decision.reason_codes
    assert "REPLACEMENT_INTENT_NATIVE_TOKEN_CHANGED" in decision.reason_codes


def test_replacement_intent_guard_blocks_snapshot_condition_or_direction_change() -> None:
    decision = validate_replacement_forecast_intent_immutability(
        baseline=_intent(),
        proposed=_intent(market_snapshot_id="snapshot-2", condition_id="condition-2", direction="buy_no"),
    )

    assert decision.allowed is False
    assert "REPLACEMENT_INTENT_CLOB_SNAPSHOT_CHANGED" in decision.reason_codes
    assert "REPLACEMENT_INTENT_CONDITION_CHANGED" in decision.reason_codes
    assert "REPLACEMENT_INTENT_DIRECTION_CHANGED" in decision.reason_codes


def test_replacement_intent_guard_blocks_kelly_or_size_increase() -> None:
    decision = validate_replacement_forecast_intent_immutability(
        baseline=_intent(),
        proposed=_intent(kelly_fraction=0.05, size_usd=21.0),
    )

    assert decision.allowed is False
    assert "REPLACEMENT_INTENT_KELLY_INCREASED" in decision.reason_codes
    assert "REPLACEMENT_INTENT_SIZE_INCREASED" in decision.reason_codes


def test_replacement_intent_surface_rejects_bad_native_direction_or_transcript_shorthand() -> None:
    with pytest.raises(ValueError, match="native YES/NO direction"):
        _intent(direction="buy_maybe")

    with pytest.raises(ValueError, match="full replacement identity"):
        _intent(source="short_" + "h" + "3_alias")
