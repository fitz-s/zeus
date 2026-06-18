# Created: 2026-06-06
# Last reused/audited: 2026-06-17
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-17; last_reused=2026-06-17
# Purpose: Protect replacement forecast runtime policy as live-or-disabled, with no shadow/veto middle state.
# Reuse: Run before daemon or event-reactor wiring of replacement posterior authority.
# Authority basis: Operator directive 2026-06-17: live system cannot conflict with already-running live content.
"""Replacement forecast runtime policy tests."""

from __future__ import annotations

import json
from pathlib import Path

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    LIVE_FLAG,
    LIVE_STATUS,
    SAFE_DEFAULT_STATUS,
    resolve_replacement_forecast_runtime_policy,
)


def _flags(**overrides: bool) -> dict[str, bool]:
    flags = {
        LIVE_FLAG: False,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }
    flags.update(overrides)
    return flags


def test_configured_replacement_forecast_flags_are_live_or_disabled() -> None:
    settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
    flags = json.loads(settings_path.read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    if flags.get(LIVE_FLAG) is True:
        assert policy.status == LIVE_STATUS
        assert policy.can_initiate_trade is True
    else:
        assert policy.status == SAFE_DEFAULT_STATUS
        assert policy.can_initiate_trade is False


def test_live_flag_directly_resolves_live() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(**{LIVE_FLAG: True})
    )

    assert policy.status == LIVE_STATUS
    assert policy.reason_codes == ("REPLACEMENT_LIVE_ENABLED",)
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_disabled_without_live_flag_has_no_live_powers() -> None:
    policy = resolve_replacement_forecast_runtime_policy(_flags())

    assert policy.status == SAFE_DEFAULT_STATUS
    assert policy.reason_codes == ("REPLACEMENT_LIVE_DISABLED",)
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_dangerous_flags_require_live() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(**{KELLY_INCREASE_FLAG: True})
    )

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_LIVE_REQUIRED_FOR_DANGEROUS_FLAGS" in policy.reason_codes
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False


def test_direction_flip_requires_kelly_authority() -> None:
    policy = resolve_replacement_forecast_runtime_policy(
        _flags(**{LIVE_FLAG: True, DIRECTION_FLIP_FLAG: True})
    )

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_DIRECTION_FLIP_REQUIRES_KELLY_AUTHORITY" in policy.reason_codes
