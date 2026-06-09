# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Authority basis: PR_SPEC.md §1 (config flags true, evidence required) + §2 FIX-1.
# History: re-authored 2026-06-07. The post-f0368a188c body asserted the configured
#   flags resolve to LIVE_AUTHORITY from flags alone; FIX-1 (§0.3) makes evidence
#   load-bearing, so the configured flags fail closed without runtime evidence.
import json
from pathlib import Path

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


def test_current_replacement_settings_fail_closed_without_evidence() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert flags[VETO_FLAG] is True
    assert flags[TRADE_AUTHORITY_FLAG] is True
    assert flags[KELLY_INCREASE_FLAG] is True
    assert flags[DIRECTION_FLIP_FLAG] is True
    # Flags alone, with no runtime evidence supplied, must NOT grant trade authority.
    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_trade_authority_fixture_without_evidence_is_blocked() -> None:
    flags = json.loads((Path(__file__).resolve().parents[1] / "config/settings.json").read_text())["feature_flags"]
    flags = dict(flags)
    flags[TRADE_AUTHORITY_FLAG] = True

    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False
