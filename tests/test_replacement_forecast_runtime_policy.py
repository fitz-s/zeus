# Created: 2026-06-06
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-07; last_reused=2026-06-07
# Purpose: Protect replacement forecast runtime policy flags and evidence-gated live authority.
# Reuse: Run before any event-reactor or daemon wiring of the replacement posterior.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
# History: 2026-06-07 — FIX-1 tightened from OR to AND (PR_SPEC.md §2 FIX-1 + ITEM B/C).
#   LIVE_AUTHORITY now REQUIRES BOTH a passing promotion_evidence AND a passing
#   capital_objective_evidence (different proofs: statistical validation vs empirical
#   winner + after-cost EV). Single-evidence paths cap at SHADOW_VETO_ONLY. The
#   contamination test that asserted production reaches LIVE_AUTHORITY without passing
#   evidence is re-authored to the fail-closed invariant; a separate in-memory test
#   carries the positive both-evidence -> LIVE_AUTHORITY path.
"""Replacement forecast runtime policy tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
    ReplacementForecastPromotionEvidence,
    ReplacementForecastCapitalObjectiveEvidence,
    resolve_replacement_forecast_runtime_policy,
)


def _flags(**overrides: bool) -> dict[str, bool]:
    flags = {
        SHADOW_FLAG: False,
        VETO_FLAG: False,
        TRADE_AUTHORITY_FLAG: False,
        KELLY_INCREASE_FLAG: False,
        DIRECTION_FLIP_FLAG: False,
    }
    flags.update(overrides)
    return flags


def _passing_evidence() -> ReplacementForecastPromotionEvidence:
    return ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=1.0,
        q_lcb_coverage=0.95,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )


def _capital_objective_evidence(**overrides: object) -> ReplacementForecastCapitalObjectiveEvidence:
    values = {
        "selected_label": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
        "replay_status": "EMPIRICAL_WINNER",
        "after_cost_pnl": 97.65,
        "source_availability_observed": True,
        "source_availability_violations": 0,
        "anti_lookahead_violations": 0,
        "same_clob_replay_passed": True,
        "fee_depth_fill_evidence_passed": True,
        "unit_pnl_only": False,
        "product_specific_refit_passed": True,
    }
    values.update(overrides)
    return ReplacementForecastCapitalObjectiveEvidence(**values)


def test_configured_replacement_forecast_flags_fail_closed_without_runtime_evidence() -> None:
    settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
    flags = json.loads(settings_path.read_text())["feature_flags"]

    policy = resolve_replacement_forecast_runtime_policy(flags)

    if flags.get(TRADE_AUTHORITY_FLAG) is True:
        assert policy.status == "BLOCKED"
        assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
        assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
        assert policy.can_read_shadow_posterior is False
        assert policy.can_apply_veto is False
    else:
        if flags.get(SHADOW_FLAG) is True and flags.get(VETO_FLAG) is True:
            assert policy.status == "SHADOW_VETO_ONLY"
            assert policy.reason_codes == ("REPLACEMENT_SHADOW_VETO_ONLY",)
            assert policy.can_read_shadow_posterior is True
            assert policy.can_apply_veto is True
        else:
            assert policy.status == "DISABLED"
            assert policy.reason_codes == ("REPLACEMENT_DISABLED_BY_FLAG",)
            assert policy.can_read_shadow_posterior is False
            assert policy.can_apply_veto is False
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_configured_production_replacement_flags_without_evidence_are_fail_closed_not_live_authority() -> None:
    """ITEM C re-author (PR_SPEC.md §2 FIX-1, post AND-tightening).

    The contamination this replaces asserted the configured production flags reach
    LIVE_AUTHORITY (the f0368a188c worldview) and depended on an uncommitted
    state/replacement_forecast_shadow/promotion_evidence.json. After FIX-1, the
    correct invariant is fail-closed: the configured production flags WITHOUT a pair
    of passing promotion+capital evidence objects resolve to SHADOW_VETO_ONLY (when
    the safe flag ladder holds) or BLOCKED — NEVER LIVE_AUTHORITY. This reads ONLY
    config/settings.json; it never touches the (possibly absent) state evidence file.
    """
    settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
    settings_payload = json.loads(settings_path.read_text())
    flags = settings_payload["feature_flags"]

    # Resolve with NO evidence — exactly the daemon's posture when no passing
    # promotion+capital pair has been produced for THIS commit.
    policy = resolve_replacement_forecast_runtime_policy(flags)

    assert policy.status != "LIVE_AUTHORITY"
    assert policy.status in {"BLOCKED", "SHADOW_VETO_ONLY", "SHADOW_ONLY", "DISABLED"}
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False

    if flags.get(TRADE_AUTHORITY_FLAG) is True:
        # trade_authority armed but no evidence => fail-closed BLOCKED with the
        # evidence-required codes load-bearing.
        assert policy.status == "BLOCKED"
        assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
        assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in policy.reason_codes
        assert policy.can_read_shadow_posterior is False
        assert policy.can_apply_veto is False


def test_configured_production_replacement_reaches_live_authority_only_with_both_passing_evidence() -> None:
    """ITEM C positive companion: the configured production flags DO reach
    LIVE_AUTHORITY, but ONLY when BOTH a passing promotion evidence AND a passing
    capital-objective evidence are supplied. Both are constructed IN-MEMORY here so
    the test does not depend on an uncommitted state file (the contamination cause)."""
    settings_path = Path(__file__).resolve().parents[1] / "config/settings.json"
    settings_payload = json.loads(settings_path.read_text())
    flags = dict(settings_payload["feature_flags"])
    # Pin the flag ladder to the trade-authority posture without the dangerous
    # kelly/direction-flip flags (matches the configured-production invariant the
    # original contamination test claimed, minus its illegal LIVE-without-evidence leap).
    flags[SHADOW_FLAG] = True
    flags[VETO_FLAG] = True
    flags[TRADE_AUTHORITY_FLAG] = True
    flags[KELLY_INCREASE_FLAG] = False
    flags[DIRECTION_FLIP_FLAG] = False

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "LIVE_AUTHORITY"
    assert policy.can_read_shadow_posterior is True
    assert policy.can_apply_veto is True
    assert policy.can_initiate_trade is True
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_replacement_forecast_policy_allows_shadow_then_veto_only() -> None:
    shadow = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True}))
    assert shadow.status == "SHADOW_ONLY"
    assert shadow.can_read_shadow_posterior is True
    assert shadow.can_apply_veto is False
    assert shadow.can_initiate_trade is False

    veto = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, VETO_FLAG: True}))
    assert veto.status == "SHADOW_VETO_ONLY"
    assert veto.can_read_shadow_posterior is True
    assert veto.can_apply_veto is True
    assert veto.can_initiate_trade is False
    assert veto.can_increase_kelly is False
    assert veto.can_flip_direction is False


def test_replacement_forecast_policy_blocks_dangerous_flag_combinations() -> None:
    no_shadow = resolve_replacement_forecast_runtime_policy(_flags(**{VETO_FLAG: True}))
    assert no_shadow.status == "BLOCKED"
    assert "REPLACEMENT_SHADOW_FLAG_REQUIRED" in no_shadow.reason_codes

    no_veto = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, TRADE_AUTHORITY_FLAG: True}))
    assert no_veto.status == "BLOCKED"
    assert "REPLACEMENT_VETO_FLAG_REQUIRED_BEFORE_AUTHORITY" in no_veto.reason_codes
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in no_veto.reason_codes

    kelly_without_trade = resolve_replacement_forecast_runtime_policy(_flags(**{SHADOW_FLAG: True, VETO_FLAG: True, KELLY_INCREASE_FLAG: True}))
    assert kelly_without_trade.status == "BLOCKED"
    assert "REPLACEMENT_TRADE_AUTHORITY_REQUIRED_FOR_DANGEROUS_FLAGS" in kelly_without_trade.reason_codes


def test_replacement_forecast_trade_authority_requires_promotion_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    blocked = resolve_replacement_forecast_runtime_policy(flags)
    assert blocked.status == "BLOCKED"
    assert blocked.can_initiate_trade is False

    weak_evidence = ReplacementForecastPromotionEvidence(
        official_days=1,
        official_rows=57,
        after_cost_pnl=10.0,
        q_lcb_coverage=0.99,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=57,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
        nested_holdout_brier=0.20,
        nested_holdout_log_loss=0.50,
        nested_selected_anchor_weight=0.80,
        nested_selected_anchor_sigma_c=3.00,
        nested_guardrail_bucket_count=1,
        nested_guardrail_bucket_min_rows=20,
        product_specific_refit_passed=True,
    )
    still_blocked = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=weak_evidence)
    assert still_blocked.status == "BLOCKED"

    # FIX-1 AND (ITEM B): a PASSING promotion evidence is NECESSARY but NOT SUFFICIENT.
    # Without an accompanying passing capital-objective evidence the path is BLOCKED
    # (strictly-more-restrictive than SHADOW_VETO_ONLY: it also withholds the shadow
    # read; never weaken the existing no-evidence guard) and surfaces both the
    # LIVE_AUTHORITY_REQUIRES_EVIDENCE sentinel and the capital-objective-required code.
    # Overconfidence = ruin: statistical validation alone does not license real money.
    promotion_only = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=_passing_evidence())
    assert promotion_only.status == "BLOCKED"
    assert promotion_only.status != "LIVE_AUTHORITY"
    assert "REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE" in promotion_only.reason_codes
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_EVIDENCE_REQUIRED" in promotion_only.reason_codes
    assert promotion_only.can_initiate_trade is False
    assert promotion_only.can_increase_kelly is False
    assert promotion_only.can_flip_direction is False


def test_replacement_forecast_trade_authority_capital_objective_evidence_alone_is_not_sufficient() -> None:
    # FIX-1 AND (ITEM B): capital-objective evidence alone (the empirical-winner +
    # after-cost-EV proof) is NECESSARY but NOT SUFFICIENT. Without an accompanying
    # passing promotion evidence (the statistical-validation proof) the path is BLOCKED
    # and surfaces the promotion-required code. The two are DIFFERENT proofs; real
    # money requires both.
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        capital_objective_evidence=_capital_objective_evidence(),
    )

    assert policy.status == "BLOCKED"
    assert policy.status != "LIVE_AUTHORITY"
    assert "REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE" in policy.reason_codes
    assert "REPLACEMENT_PROMOTION_EVIDENCE_REQUIRED" in policy.reason_codes
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False


def test_replacement_forecast_trade_authority_requires_both_evidence_objects() -> None:
    # FIX-1 AND (ITEM B) antibody: only the conjunction of BOTH passing evidence
    # objects reaches LIVE_AUTHORITY. This is the one-evidence-only => NOT
    # LIVE_AUTHORITY invariant stated as a single antibody.
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    neither = resolve_replacement_forecast_runtime_policy(flags)
    assert neither.status != "LIVE_AUTHORITY"

    promotion_only = resolve_replacement_forecast_runtime_policy(
        flags, promotion_evidence=_passing_evidence()
    )
    assert promotion_only.status != "LIVE_AUTHORITY"

    capital_only = resolve_replacement_forecast_runtime_policy(
        flags, capital_objective_evidence=_capital_objective_evidence()
    )
    assert capital_only.status != "LIVE_AUTHORITY"

    both = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=_passing_evidence(),
        capital_objective_evidence=_capital_objective_evidence(),
    )
    assert both.status == "LIVE_AUTHORITY"
    assert both.can_initiate_trade is True


def test_replacement_forecast_trade_authority_rejects_bad_capital_objective_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})

    wrong_winner = resolve_replacement_forecast_runtime_policy(
        flags,
        capital_objective_evidence=_capital_objective_evidence(selected_label="B0"),
    )
    assumed_source = resolve_replacement_forecast_runtime_policy(
        flags,
        capital_objective_evidence=_capital_objective_evidence(source_availability_observed=False),
    )

    assert wrong_winner.status == "BLOCKED"
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_SELECTED_LABEL_MISMATCH" in wrong_winner.reason_codes
    assert assumed_source.status == "BLOCKED"
    assert "REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_NOT_OBSERVED" in assumed_source.reason_codes


def test_replacement_forecast_trade_authority_rejects_unit_pnl_or_incomplete_replay_evidence() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    unit_only = ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=100.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=False,
        unit_pnl_only=True,
    )
    incomplete = ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=100.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=249,
        same_clob_replay_blocked_rows=1,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
    )

    assert resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=unit_only).status == "BLOCKED"
    assert resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=incomplete).status == "BLOCKED"


def test_replacement_forecast_trade_authority_rejects_unstructured_nested_finetune_claim() -> None:
    flags = _flags(**{SHADOW_FLAG: True, VETO_FLAG: True, TRADE_AUTHORITY_FLAG: True})
    fake_nested = ReplacementForecastPromotionEvidence(
        official_days=5,
        official_rows=250,
        after_cost_pnl=100.0,
        q_lcb_coverage=0.96,
        anti_lookahead_violations=0,
        source_availability_violations=0,
        unresolved_regression_clusters=0,
        same_clob_replay_passed=True,
        nested_walk_forward_passed=True,
        same_clob_replay_scored_rows=250,
        same_clob_replay_blocked_rows=0,
        fee_depth_fill_evidence_passed=True,
        unit_pnl_only=False,
    )

    policy = resolve_replacement_forecast_runtime_policy(flags, promotion_evidence=fake_nested)

    assert policy.status == "BLOCKED"
    assert "REPLACEMENT_PROMOTION_NESTED_BRIER_MISSING" in policy.reason_codes
    assert "REPLACEMENT_PROMOTION_PRODUCT_SPECIFIC_REFIT_MISSING" in policy.reason_codes


def test_replacement_forecast_live_authority_surfaces_evidence_blocking_codes() -> None:
    """FIX-1 antibody: flags-all-true + evidence-with-blocking-codes must NOT be
    LIVE_AUTHORITY, and the evidence's own blocking codes must surface verbatim so
    the gate is load-bearing by type rather than theater (§0.3)."""

    flags = _flags(
        **{
            SHADOW_FLAG: True,
            VETO_FLAG: True,
            TRADE_AUTHORITY_FLAG: True,
            KELLY_INCREASE_FLAG: True,
            DIRECTION_FLIP_FLAG: True,
        }
    )

    bad_promotion = ReplacementForecastPromotionEvidence(
        official_days=2,
        official_rows=100,
        after_cost_pnl=-1.0,
        q_lcb_coverage=0.50,
        anti_lookahead_violations=3,
        source_availability_violations=2,
        unresolved_regression_clusters=1,
        same_clob_replay_passed=False,
        nested_walk_forward_passed=False,
        same_clob_replay_scored_rows=0,
        same_clob_replay_blocked_rows=5,
        fee_depth_fill_evidence_passed=False,
        unit_pnl_only=True,
        nested_holdout_brier=None,
        nested_holdout_log_loss=None,
        nested_selected_anchor_weight=None,
        nested_selected_anchor_sigma_c=None,
        nested_guardrail_bucket_count=0,
        nested_guardrail_bucket_min_rows=0,
        product_specific_refit_passed=False,
    )
    bad_capital = _capital_objective_evidence(
        selected_label="B0",
        replay_status="SCORED",
        source_availability_observed=False,
    )

    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=bad_promotion,
        capital_objective_evidence=bad_capital,
    )

    assert policy.status != "LIVE_AUTHORITY"
    assert policy.status == "BLOCKED"
    assert policy.can_initiate_trade is False
    assert policy.can_increase_kelly is False
    assert policy.can_flip_direction is False
    # The evidence's own blocking codes must be load-bearing, not dropped.
    for code in bad_promotion.blocking_reason_codes():
        assert code in policy.reason_codes
    for code in bad_capital.blocking_reason_codes():
        assert code in policy.reason_codes


def test_replacement_forecast_policy_requires_strict_bool_flags() -> None:
    missing = _flags()
    del missing[SHADOW_FLAG]
    with pytest.raises(KeyError, match=SHADOW_FLAG):
        resolve_replacement_forecast_runtime_policy(missing)

    bad_type: dict[str, object] = _flags()
    bad_type[SHADOW_FLAG] = "false"
    with pytest.raises(TypeError, match=SHADOW_FLAG):
        resolve_replacement_forecast_runtime_policy(bad_type)
