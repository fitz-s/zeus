# Lifecycle: created=2026-05-25; last_reviewed=2026-07-19; last_reused=2026-07-19
# Purpose: Prove actionable trade certificates bind every live probability and execution parent.
# Reuse: Re-audit canonical parent identity and selected-leg probability closure before live use.
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import copy
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.decision_kernel import claims
from src.decision_kernel.canonicalization import (
    qkernel_current_state_identity_hash,
    stable_hash,
)
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import DecisionCertificateLedger
from src.decision_kernel.verifier import verify_actionable_trade
from src.engine import event_reactor_adapter as adapter
from src.strategy.live_inference.live_admission import (
    replacement_probability_bundle_hash,
)


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)
_REPLACEMENT_Q = {"11C": 0.348, "other": 0.652}
_REPLACEMENT_Q_LCB = {"11C": 0.30, "other": 0.60}
_REPLACEMENT_Q_UCB = {"11C": 0.383, "other": 0.70}
_REPLACEMENT_CANONICAL_BOUND_HASH = replacement_probability_bundle_hash(
    posterior_id=29872,
    posterior_identity_hash="1" * 64,
    family_id="family-1",
    bin_topology_hash="2" * 64,
    q_mode="FUSED_NORMAL_FULL",
    q_lcb_basis="fused_center_bootstrap_p05",
    q_ucb_role="fused_center_bootstrap_ucb",
    bootstrap_draws=400,
    joint_samples_hash="3" * 64,
    q=_REPLACEMENT_Q,
    q_lcb=_REPLACEMENT_Q_LCB,
    q_ucb=_REPLACEMENT_Q_UCB,
)


def _replacement_no_bound() -> dict:
    body = {
        "schema": "replacement_native_no_bound_v1",
        "probability_authority": "replacement_0_1",
        "posterior_id": 29872,
        "posterior_identity_hash": "1" * 64,
        "family_id": "family-1",
        "bin_topology_hash": "2" * 64,
        "q_mode": "FUSED_NORMAL_FULL",
        "q_lcb_basis": "fused_center_bootstrap_p05",
        "q_ucb_role": "fused_center_bootstrap_ucb",
        "bootstrap_draws": 400,
        "joint_samples_hash": "3" * 64,
        "canonical_bound_hash": _REPLACEMENT_CANONICAL_BOUND_HASH,
        "condition_id": "condition-1",
        "bin_id": "11C",
        "side": "buy_no",
        "yes_q": 0.348,
        "yes_q_ucb": 0.383,
        "side_q_point": 0.652,
        "side_q_lcb_raw": 0.617,
        "side_q_lcb_served": 0.617,
        "coverage_shrink_applied": False,
    }
    return {**body, "certificate_hash": stable_hash(body)}


def _replacement_actionable_overrides() -> tuple[dict, dict[str, dict]]:
    bound = _replacement_no_bound()
    action = {
        "token_id": "no-1",
        "direction": "buy_no",
        "q_live": 0.652,
        "q_lcb_5pct": 0.60,
        "c_fee_adjusted": 0.32,
        "c_cost_95pct": 0.32,
        "trade_score": 0.28,
        "action_score": 0.28,
        "same_bin_yes_posterior": 0.348,
        "q_lcb_calibration_source": "FORECAST_BOOTSTRAP",
        "settlement_coverage_status": "INSUFFICIENT_DATA",
        "probability_authority": "replacement_0_1",
        "posterior_id": 29872,
        "replacement_no_bound_certificate": bound,
        "qkernel_execution_economics": {
            **_action_payload()["qkernel_execution_economics"],
            "side": "NO",
            "payoff_q_point": 0.652,
            "payoff_q_lcb": 0.60,
            "cost": 0.32,
            "edge_lcb": 0.28,
            "selection_guard_q_safe": 0.60,
            "pre_qkernel_q_posterior": 0.652,
            "pre_qkernel_q_lcb_5pct": 0.617,
            "q_lcb_authority": "qkernel_payoff_bound",
            "probability_authority": "qkernel_payoff_direct_route",
        },
    }
    parent = {
        claims.FORECAST_AUTHORITY: {
            "posterior_identity_hash": "1" * 64,
            "replacement_posterior_id": 29872,
            "replacement_family_id": "family-1",
            "replacement_bin_topology_hash": "2" * 64,
            "replacement_q_mode": "FUSED_NORMAL_FULL",
            "replacement_q_lcb_basis": "fused_center_bootstrap_p05",
            "replacement_q_ucb_role": "fused_center_bootstrap_ucb",
            "replacement_bootstrap_draws": 400,
            "replacement_joint_samples_hash": "3" * 64,
            "replacement_canonical_bound_hash": _REPLACEMENT_CANONICAL_BOUND_HASH,
            "replacement_q": _REPLACEMENT_Q,
            "replacement_q_lcb": _REPLACEMENT_Q_LCB,
            "replacement_q_ucb": _REPLACEMENT_Q_UCB,
        },
        claims.CALIBRATION: {
            "authority": "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB",
            "coverage_status": "INSUFFICIENT_DATA",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "bootstrap_draws": 400,
        },
        claims.EXECUTABLE_SNAPSHOT: {"token_id": "no-1"},
        claims.QUOTE_FEASIBILITY: {"token_id": "no-1", "direction": "buy_no"},
        claims.COST_MODEL: {"token_id": "no-1", "direction": "buy_no"},
        claims.CANDIDATE_EVIDENCE: {
            "selected_token_id": "no-1",
            "direction": "buy_no",
            "hypothesis_id": "family-1:no-1",
            "replacement_no_bound_bin_id": "11C",
            "replacement_no_bound_served_lcb": 0.617,
        },
        claims.FDR: {"selected_hypotheses": ("family-1:no-1",)},
    }
    return action, parent


def test_actionable_replacement_no_bound_binds_canonical_parents_after_qkernel_tightening():
    action_payload, parent_overrides = _replacement_actionable_overrides()
    parents, action = actionable_graph(
        action_payload=action_payload,
        parent_overrides=parent_overrides,
    )

    verify_actionable_trade(action, parents)


def test_actionable_replacement_no_bound_rejects_canonical_parent_mismatch():
    action_payload, parent_overrides = _replacement_actionable_overrides()
    parent_overrides[claims.FORECAST_AUTHORITY]["replacement_canonical_bound_hash"] = (
        "5" * 64
    )
    parents, action = actionable_graph(
        action_payload=action_payload,
        parent_overrides=parent_overrides,
    )

    with pytest.raises(
        CertificateVerificationError,
        match="replacement NO bound certificate invalid",
    ):
        verify_actionable_trade(action, parents)


def test_actionable_requires_live_mode():
    parents, action = actionable_graph(mode="NO_SUBMIT")

    with pytest.raises(CertificateVerificationError, match="LIVE mode"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_redecision_as_forecast_lane():
    parents, action = actionable_graph(
        action_payload={"event_type": "EDLI_REDECISION_PENDING"}
    )

    verify_actionable_trade(action, parents)


def test_actionable_accepts_day0_observation_authority_with_qkernel():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 20.0,
            "rounded_value": 20,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "qkernel_execution_economics": _day0_qkernel_economics(),
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_accepts_degenerate_day0_remaining_window_guarded_q():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 20.0,
            "rounded_value": 20,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "q_live": 0.6,
            "q_lcb_5pct": 0.6,
            "qkernel_execution_economics": {
                **_day0_qkernel_economics(),
                "payoff_q_point": 0.6,
                "payoff_q_lcb": 0.6,
                "selection_guard_q_safe": 0.6,
            },
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_degenerate_day0_inert_pass_through_q():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 20.0,
            "rounded_value": 20,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "q_live": 0.6,
            "q_lcb_5pct": 0.6,
            "qkernel_execution_economics": {
                **_day0_qkernel_economics(),
                "payoff_q_point": 0.6,
                "payoff_q_lcb": 0.6,
                "selection_guard_q_safe": 0.6,
                "q_lcb_guard_basis": "INERT",
                "selection_guard_basis": "INERT",
                "q_lcb_guard_cell_key": "high|L1|YES|modal|qb12|coarse_global",
                "selection_guard_cell_key": "high|L1|YES|modal|qb12|coarse_global",
            },
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="degenerate with q_live"):
        verify_actionable_trade(action, parents)


def test_day0_authority_accepts_non_degenerate_current_band_tightening():
    from src.events.day0_authority import assert_live_day0_probability_authority

    payload = {
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_q_mode": "remaining_day",
        "_edli_day0_remaining_models": 2,
        "rounded_value": 25,
        "observation_time": "2026-07-14T15:00:00+00:00",
        "_edli_day0_lcb_transform": {
            "yes_lcb_by_condition": {"condition-1": 0.0},
            "no_lcb_by_condition": {"condition-1": 1.0},
        },
        "qkernel_execution_economics": {
            "payoff_q_point": 1.0,
            "payoff_q_lcb": 0.20,
            "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
            "q_lcb_guard_abstained": False,
            "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.20,
            "selection_guard_n": 100,
            "current_state_identity_hash": "current-band-identity",
        },
    }

    assert_live_day0_probability_authority(
        payload,
        direction="buy_no",
        condition_id="condition-1",
        q_live=1.0,
        q_lcb=0.20,
    )


def test_day0_authority_rejects_degenerate_current_band_claim():
    from src.events.day0_authority import (
        Day0AuthorityError,
        assert_live_day0_probability_authority,
    )

    payload = {
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_q_mode": "remaining_day",
        "_edli_day0_remaining_models": 2,
        "rounded_value": 25,
        "observation_time": "2026-07-14T15:00:00+00:00",
        "_edli_day0_lcb_transform": {
            "yes_lcb_by_condition": {"condition-1": 0.0},
            "no_lcb_by_condition": {"condition-1": 1.0},
        },
        "qkernel_execution_economics": {
            "payoff_q_point": 1.0,
            "payoff_q_lcb": 1.0,
            "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
            "q_lcb_guard_abstained": False,
            "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 1.0,
            "selection_guard_n": 100,
            "current_state_identity_hash": "current-band-identity",
        },
    }

    with pytest.raises(Day0AuthorityError, match="degenerate with q_live"):
        assert_live_day0_probability_authority(
            payload,
            direction="buy_no",
            condition_id="condition-1",
            q_live=1.0,
            q_lcb=1.0,
        )


def test_actionable_rejects_day0_observed_boundary_as_entry_qkernel_guard():
    parents, action = actionable_graph(
        action_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 32.0,
            "rounded_value": 32,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "day0_probability_authority": _day0_probability_authority(),
            "_edli_q_source": "day0_remaining_day",
            "_edli_day0_q_mode": "remaining_day",
            "_edli_day0_remaining_models": 3,
            "_edli_day0_lcb_transform": _day0_lcb_transform(),
            "qkernel_execution_economics": {
                **_day0_qkernel_economics(),
                "q_lcb_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                "selection_guard_basis": "DAY0_OBSERVED_BOUNDARY",
                "q_lcb_guard_cell_key": "day0_observed_boundary",
                "selection_guard_cell_key": "day0_observed_boundary",
            },
        },
        extra_parent_payloads={
            claims.DAY0_AUTHORITY: {
                "event_id": "event-1",
                "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
            },
            claims.ABSORBING_BOUNDARY: {
                "event_id": "event-1",
                "boundary": "day0_absorbing_hard_fact",
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="DAY0_OBSERVED_BOUNDARY"):
        verify_actionable_trade(action, parents)


def _day0_reproof_action_payload(**overrides) -> dict:
    """Base DAY0_EXTREME_UPDATED actionable payload for the M-13 re-proof tests.

    Identical to the fixture ``test_actionable_accepts_day0_observation_authority_with_qkernel``
    already proves passes every OTHER Day0 gate; these tests add only the
    metric/city/bin_label fields the M-13 re-proof consumes.
    """
    payload = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "raw_value": 20.0,
        "rounded_value": 20,
        "observation_time": "2026-05-25T11:30:00+00:00",
        "observation_available_at": "2026-05-25T11:35:00+00:00",
        "day0_probability_authority": _day0_probability_authority(),
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_q_mode": "remaining_day",
        "_edli_day0_remaining_models": 3,
        "_edli_day0_lcb_transform": _day0_lcb_transform(),
        "qkernel_execution_economics": _day0_qkernel_economics(),
    }
    payload.update(overrides)
    return payload


def _day0_authority_parents(
    *,
    metric: str | None = "high",
    city: str | None = None,
    raw_value: float = 20.0,
    rounded_value: float = 20,
) -> dict[str, dict]:
    day0_authority: dict = {
        "event_id": "event-1",
        "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
        "raw_value": raw_value,
        "rounded_value": rounded_value,
    }
    if metric is not None:
        day0_authority["metric"] = metric
    if city is not None:
        day0_authority["city"] = city
    return {
        claims.DAY0_AUTHORITY: day0_authority,
        claims.ABSORBING_BOUNDARY: {"event_id": "event-1", "boundary": "day0_absorbing_hard_fact"},
    }


def test_actionable_day0_reproof_accepts_possible_bin():
    """M-13: a bin the observed extreme does NOT rule out passes the re-proof untouched."""
    parents, action = actionable_graph(
        action_payload=_day0_reproof_action_payload(),
        parent_overrides={claims.CANDIDATE_EVIDENCE: {"bin_label": "22°F"}},
        extra_parent_payloads=_day0_authority_parents(metric="high"),
    )

    verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_rejects_impossible_bin_nonzero_q():
    """M-13 law 1: an impossible bin must carry YES q == 0."""
    parents, action = actionable_graph(
        action_payload=_day0_reproof_action_payload(),
        parent_overrides={claims.CANDIDATE_EVIDENCE: {"bin_label": "18°F"}},
        extra_parent_payloads=_day0_authority_parents(metric="high"),
    )

    with pytest.raises(CertificateVerificationError, match="DAY0_IMPOSSIBLE_BIN_NONZERO_Q"):
        verify_actionable_trade(action, parents)


def _day0_impossible_buy_no_graph(
    *,
    q_live: float,
    bin_label: str = "18°F",
    city: str | None = None,
    obs_extreme: float = 20.0,
):
    transform = {
        **_day0_lcb_transform(),
        "yes_lcb_by_condition": {"condition-1": 0.0},
        "no_lcb_by_condition": {"condition-1": 0.2},
        "rounded_extreme": obs_extreme,
    }
    probability_authority = {
        **_day0_probability_authority(),
        "rounded_value": obs_extreme,
        "lcb_transform": transform,
    }
    economics = {
        **_day0_qkernel_economics(),
        "side": "NO",
        "payoff_q_point": q_live,
        "payoff_q_lcb": 0.2,
        "cost": 0.1,
        "edge_lcb": 0.1,
        "selection_guard_q_safe": 0.2,
    }
    return actionable_graph(
        action_payload=_day0_reproof_action_payload(
            token_id="no-1",
            direction="buy_no",
            q_live=q_live,
            q_lcb_5pct=0.2,
            c_fee_adjusted=0.1,
            c_cost_95pct=0.1,
            trade_score=0.1,
            action_score=0.1,
            raw_value=obs_extreme,
            rounded_value=obs_extreme,
            day0_probability_authority=probability_authority,
            _edli_day0_lcb_transform=transform,
            qkernel_execution_economics=economics,
        ),
        parent_overrides={
            claims.EXECUTABLE_SNAPSHOT: {"token_id": "no-1"},
            claims.QUOTE_FEASIBILITY: {
                "token_id": "no-1",
                "direction": "buy_no",
            },
            claims.COST_MODEL: {
                "token_id": "no-1",
                "direction": "buy_no",
            },
            claims.CANDIDATE_EVIDENCE: {
                "selected_token_id": "no-1",
                "direction": "buy_no",
                "hypothesis_id": "family-1:no-1",
                "bin_label": bin_label,
            },
            claims.FDR: {"selected_hypotheses": ("family-1:no-1",)},
        },
        extra_parent_payloads=_day0_authority_parents(
            metric="high",
            city=city,
            raw_value=obs_extreme,
            rounded_value=obs_extreme,
        ),
    )


def test_actionable_day0_reproof_accepts_certain_no_for_impossible_yes_bin():
    """An impossible YES bin is exactly the absorbing q=1 BUY NO case."""
    parents, action = _day0_impossible_buy_no_graph(q_live=1.0)

    verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_rejects_understated_no_for_impossible_yes_bin():
    """BUY NO is not bypassed: its complement must still prove YES q=0."""
    parents, action = _day0_impossible_buy_no_graph(q_live=0.9)

    with pytest.raises(CertificateVerificationError, match="yes_q_live=0.099"):
        verify_actionable_trade(action, parents)


@pytest.mark.parametrize("q_live", [1.0, 0.99])
def test_actionable_day0_reproof_hong_kong_29c_observed_30_buy_no(q_live):
    """Production regression: HKO 29°C YES is impossible after observed high 30°C."""
    parents, action = _day0_impossible_buy_no_graph(
        q_live=q_live,
        bin_label="29°C",
        city="Hong Kong",
        obs_extreme=30.0,
    )

    if q_live == 1.0:
        verify_actionable_trade(action, parents)
    else:
        with pytest.raises(CertificateVerificationError, match="yes_q_live=0.010"):
            verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_rejects_unrecognized_metric_orientation():
    """M-13 law 2: an unrecognized metric cannot be assigned a floor/ceiling orientation."""
    parents, action = actionable_graph(
        action_payload=_day0_reproof_action_payload(),
        parent_overrides={claims.CANDIDATE_EVIDENCE: {"bin_label": "18°F"}},
        extra_parent_payloads=_day0_authority_parents(metric="sideways"),
    )

    with pytest.raises(CertificateVerificationError, match="DAY0_METRIC_ORIENTATION_INVALID"):
        verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_fails_open_without_bin_label():
    """An unparseable/missing bin_label cannot be re-proven -- absence is not a failure."""
    parents, action = actionable_graph(
        action_payload=_day0_reproof_action_payload(),
        extra_parent_payloads=_day0_authority_parents(metric="high"),
    )

    verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_fails_open_without_metric():
    """No metric persisted on DAY0_AUTHORITY -- not applicable, never a new failure."""
    parents, action = actionable_graph(
        action_payload=_day0_reproof_action_payload(),
        parent_overrides={claims.CANDIDATE_EVIDENCE: {"bin_label": "18°F"}},
        extra_parent_payloads=_day0_authority_parents(metric=None),
    )

    verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_untouched_for_forecast_lane():
    """Non-Day0 certificates are untouched even if a DAY0_AUTHORITY-shaped parent is present."""
    parents, action = actionable_graph(
        parent_overrides={claims.CANDIDATE_EVIDENCE: {"bin_label": "18°F"}},
        extra_parent_payloads=_day0_authority_parents(metric="bogus"),
    )

    verify_actionable_trade(action, parents)


def test_actionable_day0_reproof_hong_kong_oracle_truncate_preimage():
    """M-13 HK case: oracle_truncate's asymmetric preimage keeps a bin possible that
    the symmetric WMO half-up preimage would (wrongly, for HK) rule out.

    obs=28.9 truncates (HKO convention) to 28, so bin "28C" is still reachable: the
    settlement has not yet proven a value >= 29. Under the (wrong-for-HK) WMO half-up
    preimage [27.5, 28.5) the SAME bin would be impossible (28.5 <= 28.9), so this
    proves the re-proof dispatches on the Hong Kong rounding rule, not a hard-coded one.
    """
    parents, action = actionable_graph(
        action_payload=_day0_reproof_action_payload(raw_value=28.9, rounded_value=28),
        parent_overrides={claims.CANDIDATE_EVIDENCE: {"bin_label": "28°C"}},
        extra_parent_payloads=_day0_authority_parents(
            metric="high", city="Hong Kong", raw_value=28.9, rounded_value=28
        ),
    )

    verify_actionable_trade(action, parents)


def _day0_lcb_transform():
    return {
        "yes_lcb_by_condition": {"condition-1": 0.6},
        "no_lcb_by_condition": {"condition-1": 0.2},
        "mask": [1.0],
        "absorbing_yes_conditions": [],
        "absorbing_no_conditions": [],
        "staleness_suppressed_conditions": [],
        "immature_finite_yes_suppressed_conditions": [],
        "day0_exit_authority_status": "mature",
        "day0_exit_authority_reason": "day0_high_extreme_post_peak",
        "rounded_extreme": 20.0,
        "metric": "high",
    }


def _day0_probability_authority():
    return {
        "q_source": "day0_remaining_day",
        "q_mode": "remaining_day",
        "remaining_models": 3,
        "rounded_value": 20,
        "observation_time": "2026-05-25T11:30:00+00:00",
        "observation_available_at": "2026-05-25T11:35:00+00:00",
        "lcb_transform": _day0_lcb_transform(),
    }


def _day0_qkernel_economics() -> dict:
    economics = dict(_action_payload()["qkernel_execution_economics"])
    economics.update(
        {
            "q_lcb_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "q_lcb_guard_abstained": False,
            "q_lcb_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_basis": "DAY0_REMAINING_DAY_Q_LCB",
            "selection_guard_abstained": False,
            "selection_guard_cell_key": "day0_remaining_day_q_lcb",
            "selection_guard_n": 0,
            "selection_guard_q_safe": economics["payoff_q_lcb"],
        }
    )
    return economics


def _replacement_global_day0_probability_authority(
    *, posterior_id: int = 29872
) -> dict:
    observation_payload = {
        "source_match_status": "MATCH",
        "station_match_status": "MATCH",
        "local_date_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
        "observation_time": "2026-05-25T11:30:00+00:00",
        "observation_available_at": "2026-05-25T11:35:00+00:00",
        "raw_value": 72.0,
        "rounded_value": 72,
        "sample_count": 11,
        "samples_count": 11,
        "station_id": "KORD",
        "settlement_source": "wu_icao_history",
        "settlement_unit": "F",
        "_edli_global_day0_binding": {
            "posterior_id": posterior_id,
            "probability_base_identity": "1" * 64,
            "city": "Chicago",
            "target_date": "2026-05-25",
            "metric": "high",
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "observed_extreme_native": 72.0,
            "rounded_value": 72,
            "sample_count": 11,
            "station_id": "KORD",
            "settlement_source": "wu_icao_history",
            "settlement_unit": "F",
        },
    }
    return {
        "probability_authority": (
            "replacement_provisional_day0_global_probability_v1"
        ),
        "q_source": "replacement_0_1",
        "posterior_id": posterior_id,
        "probability_base_identity": "1" * 64,
        "global_current_observation_payload": observation_payload,
    }


def _replacement_day0_actionable_fixture(direction: str):
    action, parent_overrides = _replacement_actionable_overrides()
    if direction == "buy_yes":
        action.pop("replacement_no_bound_certificate", None)
        action.update(
            {
                "token_id": "yes-1",
                "direction": "buy_yes",
                "q_live": 0.348,
                "q_lcb_5pct": 0.30,
                "c_fee_adjusted": 0.20,
                "c_cost_95pct": 0.20,
                "trade_score": 0.10,
                "action_score": 0.10,
                "same_bin_yes_posterior": 0.348,
            }
        )
        parent_overrides[claims.EXECUTABLE_SNAPSHOT] = {"token_id": "yes-1"}
        parent_overrides[claims.QUOTE_FEASIBILITY] = {
            "token_id": "yes-1",
            "direction": "buy_yes",
        }
        parent_overrides[claims.COST_MODEL] = {
            "token_id": "yes-1",
            "direction": "buy_yes",
        }
        parent_overrides[claims.CANDIDATE_EVIDENCE] = {
            "selected_token_id": "yes-1",
            "direction": "buy_yes",
            "hypothesis_id": "family-1:yes-1",
        }
        parent_overrides[claims.FDR] = {
            "selected_hypotheses": ("family-1:yes-1",)
        }
    q_live = float(action["q_live"])
    q_lcb = float(action["q_lcb_5pct"])
    cost = float(action["c_fee_adjusted"])
    sample_hash = f"current-day0-{direction}-samples"
    economics = {
        **action["qkernel_execution_economics"],
        "side": "YES" if direction == "buy_yes" else "NO",
        "payoff_q_point": q_live,
        "payoff_q_lcb": q_lcb,
        "cost": cost,
        "edge_lcb": q_lcb - cost,
        "selection_guard_q_safe": q_lcb,
        "decision_id": f"current-day0-{direction}",
        "receipt_hash": f"current-day0-{direction}-receipt",
        "q_version": "current-day0-q-version",
        "sample_hash": sample_hash,
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": sample_hash,
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": sample_hash,
        "selection_guard_n": 400,
    }
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    probability_authority = _replacement_global_day0_probability_authority()
    action.update(
        {
            "event_type": "DAY0_EXTREME_UPDATED",
            "city": "Chicago",
            "target_date": "2026-05-25",
            "metric": "high",
            "source_match_status": "MATCH",
            "station_match_status": "MATCH",
            "local_date_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "raw_value": 72.0,
            "rounded_value": 72,
            "observation_time": "2026-05-25T11:30:00+00:00",
            "observation_available_at": "2026-05-25T11:35:00+00:00",
            "q_source": "replacement_0_1",
            "_edli_q_source": "replacement_0_1",
            "day0_probability_authority": probability_authority,
            "qkernel_execution_economics": economics,
        }
    )
    parent_overrides[claims.BELIEF] = {
        "qkernel_decision_id": economics["decision_id"],
        "qkernel_receipt_hash": economics["receipt_hash"],
        "qkernel_q_version": economics["q_version"],
        "qkernel_sample_hash": economics["sample_hash"],
        "qkernel_current_state_identity_hash": economics[
            "current_state_identity_hash"
        ],
    }
    extra_parents = {
        claims.DAY0_AUTHORITY: {
            "event_id": "event-1",
            "authority": "DAY0_LIVE_OBSERVATION_HARD_FACT",
        },
        claims.ABSORBING_BOUNDARY: {
            "event_id": "event-1",
            "boundary": "day0_absorbing_hard_fact",
        },
    }
    return action, parent_overrides, extra_parents


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
def test_actionable_accepts_provisional_replacement_day0_without_hard_fact_parents(
    direction,
):
    action_payload, parent_overrides, _extra_parents = (
        _replacement_day0_actionable_fixture(direction)
    )
    parents, action = actionable_graph(
        action_payload=action_payload,
        parent_overrides=parent_overrides,
        extra_parent_payloads={},
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_replacement_day0_posterior_binding_mismatch():
    action_payload, parent_overrides, extra_parents = (
        _replacement_day0_actionable_fixture("buy_yes")
    )
    authority = dict(action_payload["day0_probability_authority"])
    observation = dict(authority["global_current_observation_payload"])
    binding = dict(observation["_edli_global_day0_binding"])
    binding["posterior_id"] = 29873
    observation["_edli_global_day0_binding"] = binding
    authority["global_current_observation_payload"] = observation
    action_payload["day0_probability_authority"] = authority
    parents, action = actionable_graph(
        action_payload=action_payload,
        parent_overrides=parent_overrides,
        extra_parent_payloads=extra_parents,
    )

    with pytest.raises(CertificateVerificationError, match="posterior_id mismatch"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_replacement_day0_posterior_identity_parent_mismatch():
    action_payload, parent_overrides, _extra_parents = (
        _replacement_day0_actionable_fixture("buy_yes")
    )
    authority = dict(action_payload["day0_probability_authority"])
    authority["probability_base_identity"] = "forged-not-current-posterior-hash"
    observation = dict(authority["global_current_observation_payload"])
    binding = dict(observation["_edli_global_day0_binding"])
    binding["probability_base_identity"] = "forged-not-current-posterior-hash"
    observation["_edli_global_day0_binding"] = binding
    authority["global_current_observation_payload"] = observation
    action_payload["day0_probability_authority"] = authority
    parents, action = actionable_graph(
        action_payload=action_payload,
        parent_overrides=parent_overrides,
        extra_parent_payloads={},
    )

    with pytest.raises(
        CertificateVerificationError,
        match="posterior identity",
    ):
        verify_actionable_trade(action, parents)


@pytest.mark.parametrize(
    ("surface", "field", "value"),
    (
        ("selected", "city", "Paris"),
        ("selected", "target_date", "2026-05-26"),
        ("selected", "metric", "low"),
        ("observation", "observation_time", "2026-05-25T11:31:00+00:00"),
        (
            "observation",
            "observation_available_at",
            "2026-05-25T11:36:00+00:00",
        ),
        ("observation", "raw_value", 71.0),
        ("observation", "rounded_value", 71),
        ("observation", "sample_count", 10),
        ("observation", "station_id", "EGLL"),
        ("observation", "settlement_source", "wrong_source"),
        ("observation", "settlement_unit", "C"),
    ),
)
def test_actionable_rejects_any_replacement_day0_binding_tamper(
    surface,
    field,
    value,
):
    action_payload, parent_overrides, extra_parents = (
        _replacement_day0_actionable_fixture("buy_yes")
    )
    action_payload = copy.deepcopy(action_payload)
    if surface == "selected":
        action_payload[field] = value
    else:
        action_payload["day0_probability_authority"][
            "global_current_observation_payload"
        ][field] = value
    parents, action = actionable_graph(
        action_payload=action_payload,
        parent_overrides=parent_overrides,
        extra_parent_payloads=extra_parents,
    )

    with pytest.raises(CertificateVerificationError):
        verify_actionable_trade(action, parents)


def test_day0_replacement_credential_precedes_legacy_remaining_window_calibrator():
    payload = {
        "horizon_profile": "full",
        adapter._REPLACEMENT_CALIBRATION_CREDENTIAL_KEY: {
            "q_mode": "FUSED_NORMAL_FULL",
            "q_lcb_basis": "fused_center_bootstrap_p05",
            "bootstrap_draws": 400,
            "posterior_id": 29872,
            "season": "spring",
            "cohort": "Chicago:high:spring",
            "coverage": {
                "status": "INSUFFICIENT_DATA",
                "coverage_ratio": None,
                "realized_win_rate": None,
                "n": 0,
                "shrink": None,
                "shrink_applied": False,
            },
        },
    }
    calibration, _clock = adapter._calibration_authority_payload_and_clock(
        sqlite3.connect(":memory:"),
        event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        family=SimpleNamespace(
            city="Chicago",
            metric="high",
            target_date="2026-05-25",
        ),
        payload=payload,
        forecast_payload={"horizon_profile": "full"},
        decision_time=NOW,
    )

    assert calibration["authority"] == "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB"
    assert calibration["posterior_id"] == 29872


def test_actionable_requires_positive_action_score():
    parents, action = actionable_graph(action_payload={"action_score": 0.0})

    with pytest.raises(CertificateVerificationError, match="action_score"):
        verify_actionable_trade(action, parents)


def test_actionable_requires_positive_trade_score():
    parents, action = actionable_graph(action_payload={"trade_score": 0.0})

    with pytest.raises(CertificateVerificationError, match="trade_score"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_no_native_quote():
    parents, action = actionable_graph(action_payload={"native_quote_available": False})

    with pytest.raises(CertificateVerificationError, match="native_quote_available"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_p_fill_lcb_zero():
    parents, action = actionable_graph(action_payload={"p_fill_lcb": 0.0})

    with pytest.raises(CertificateVerificationError, match="p_fill_lcb"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_q_lcb_above_q_live():
    parents, action = actionable_graph(action_payload={"q_live": 0.55, "q_lcb_5pct": 0.56})

    with pytest.raises(CertificateVerificationError, match="q_lcb_5pct exceeds q_live"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_underpriced_low_probability_yes():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.24833093804728934,
            "q_lcb_5pct": 0.0990451308919892,
            "c_fee_adjusted": 0.041,
            "c_cost_95pct": 0.041,
            "trade_score": 0.0580451308919892,
            "action_score": 0.0580451308919892,
            "qkernel_execution_economics": {
                **_action_payload()["qkernel_execution_economics"],
                "payoff_q_point": 0.24833093804728934,
                "payoff_q_lcb": 0.0990451308919892,
                "cost": 0.041,
                "edge_lcb": 0.0580451308919892,
                "selection_guard_q_safe": 0.0990451308919892,
            },
        }
    )

    verify_actionable_trade(action, parents)


def test_actionable_current_state_solver_skips_legacy_quality_and_roi_floors():
    q_live = 0.12
    q_lcb = 0.10
    cost = 0.04
    economics = {
        **_action_payload()["qkernel_execution_economics"],
        "payoff_q_point": q_live,
        "payoff_q_lcb": q_lcb,
        "cost": cost,
        "edge_lcb": q_lcb - cost,
        "optimal_stake_usd": 0.01,
        "decision_id": "decision-current-1",
        "receipt_hash": "receipt-current-1",
        "q_version": "q-current-1",
        "sample_hash": "current-sample-hash",
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "current-sample-hash",
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "current-sample-hash",
        "selection_guard_n": 64,
        "selection_guard_q_safe": q_lcb,
    }
    for legacy_field in (
        "route_id",
        "route_type",
        "delta_u_at_min",
        "optimal_stake_usd",
        "optimal_delta_u",
        "false_edge_rate",
        "direction_law_ok",
        "coherence_allows",
    ):
        economics.pop(legacy_field, None)
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(economics)
    parents, action = actionable_graph(
        parent_overrides={
            claims.BELIEF: {
                "qkernel_decision_id": economics["decision_id"],
                "qkernel_receipt_hash": economics["receipt_hash"],
                "qkernel_q_version": economics["q_version"],
                "qkernel_sample_hash": economics["sample_hash"],
                "qkernel_current_state_identity_hash": economics[
                    "current_state_identity_hash"
                ],
            }
        },
        action_payload={
            "q_live": q_live,
            "q_lcb_5pct": q_lcb,
            "c_fee_adjusted": cost,
            "c_cost_95pct": cost,
            "trade_score": q_lcb - cost,
            "action_score": q_lcb - cost,
            "qkernel_execution_economics": economics,
        }
    )

    verify_actionable_trade(action, parents)


def test_actionable_current_state_identity_must_match_belief_parent():
    economics = {
        **_action_payload()["qkernel_execution_economics"],
        "decision_id": "decision-current-1",
        "receipt_hash": "receipt-current-1",
        "q_version": "q-current-1",
        "sample_hash": "current-sample-hash",
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "current-sample-hash",
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "current-sample-hash",
        "selection_guard_n": 64,
    }
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(economics)
    parents, action = actionable_graph(
        parent_overrides={
            claims.BELIEF: {
                "qkernel_decision_id": economics["decision_id"],
                "qkernel_receipt_hash": economics["receipt_hash"],
                "qkernel_q_version": "different-q-version",
                "qkernel_sample_hash": economics["sample_hash"],
                "qkernel_current_state_identity_hash": economics[
                    "current_state_identity_hash"
                ],
            }
        },
        action_payload={"qkernel_execution_economics": economics},
    )

    with pytest.raises(CertificateVerificationError, match="belief.qkernel_q_version"):
        verify_actionable_trade(action, parents)


def test_actionable_declared_current_state_cannot_downgrade_to_legacy_after_tamper():
    economics = {
        **_action_payload()["qkernel_execution_economics"],
        "decision_id": "decision-current-1",
        "receipt_hash": "receipt-current-1",
        "q_version": "q-current-1",
        "sample_hash": "current-sample-hash",
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": "current-sample-hash",
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": "current-sample-hash",
        "selection_guard_n": 64,
    }
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(economics)
    original_identity = economics["current_state_identity_hash"]
    economics.update(cost=0.39, edge_lcb=0.21)
    parents, action = actionable_graph(
        parent_overrides={
            claims.BELIEF: {
                "qkernel_decision_id": economics["decision_id"],
                "qkernel_receipt_hash": economics["receipt_hash"],
                "qkernel_q_version": economics["q_version"],
                "qkernel_sample_hash": economics["sample_hash"],
                "qkernel_current_state_identity_hash": original_identity,
            }
        },
        action_payload={
            "c_fee_adjusted": 0.39,
            "trade_score": 0.21,
            "action_score": 0.21,
            "qkernel_execution_economics": economics,
        },
    )

    with pytest.raises(
        CertificateVerificationError,
        match="current-state identity invalid",
    ):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_center_yes_when_symmetric_quality_floor_clear():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.60,
            "q_lcb_5pct": 0.52,
            "c_fee_adjusted": 0.12,
            "c_cost_95pct": 0.12,
            "trade_score": 0.40,
            "action_score": 0.40,
            "qkernel_execution_economics": {
                **_action_payload()["qkernel_execution_economics"],
                "payoff_q_point": 0.60,
                "payoff_q_lcb": 0.52,
                "cost": 0.12,
                "edge_lcb": 0.40,
                "selection_guard_q_safe": 0.52,
            },
        }
    )

    verify_actionable_trade(action, parents)


def test_actionable_requires_qkernel_spine_selection_authority():
    parents, action = actionable_graph(action_payload={"selection_authority_applied": None})

    with pytest.raises(CertificateVerificationError, match="selection_authority_applied"):
        verify_actionable_trade(action, parents)


def test_actionable_requires_qkernel_selection_guard():
    payload = _action_payload()
    economics = dict(payload["qkernel_execution_economics"])
    economics.pop("selection_guard_basis")
    economics.pop("selection_guard_abstained")
    economics.pop("selection_guard_q_safe")
    parents, action = actionable_graph(
        action_payload={"qkernel_execution_economics": economics}
    )

    with pytest.raises(CertificateVerificationError, match="selection_guard_basis"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_side_not_armed_qkernel_selection_guard():
    parents, action = actionable_graph(
        action_payload={
            "qkernel_execution_economics": {
                **_action_payload()["qkernel_execution_economics"],
                "selection_guard_basis": "SIDE_NOT_ARMED",
            }
        }
    )

    with pytest.raises(CertificateVerificationError, match="selection_guard_basis blocks side"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_qkernel_payoff_probability_mismatch():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.65,
            "q_lcb_5pct": 0.60,
            "c_fee_adjusted": 0.40,
            "c_cost_95pct": 0.40,
            "p_fill_lcb": 0.9997671696598043,
            "trade_score": 0.04049776073684555,
            "action_score": 0.04049776073684555,
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.22351072116676574,
                "payoff_q_lcb": 0.05049776073684555,
                "cost": 0.01,
                "edge_lcb": 0.04049776073684555,
                "optimal_delta_u": 0.013993788651471595,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.02599350162459385,
                "direction_law_ok": False,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.003,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="payoff_q_point mismatches|payoff_q_lcb mismatches"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_oof_reliability_direction_override_for_yes():
    parents, action = actionable_graph(
        action_payload={
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.7,
                "payoff_q_lcb": 0.6,
                "cost": 0.4,
                "edge_lcb": 0.2,
                "optimal_delta_u": 0.01,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.01,
                "direction_law_ok": False,
                "q_lcb_guard_basis": "OOF_WILSON_95",
                "q_lcb_guard_abstained": False,
                "q_lcb_guard_cell_key": "high|L2_3|YES|nonmodal|qb2|coarse_global",
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.6,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="qkernel direction admission"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_execution_command_id_present():
    parents, action = actionable_graph(action_payload={"execution_command_id": "cmd-1"})

    with pytest.raises(CertificateVerificationError, match="execution_command_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_wrong_cost_source_for_buy_no():
    parents, action = actionable_graph(
        action_payload={
            "direction": "buy_no",
            "token_id": "no-1",
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "NO",
                "payoff_q_point": 0.7,
                "payoff_q_lcb": 0.6,
                "cost": 0.4,
                "edge_lcb": 0.2,
                "optimal_delta_u": 0.01,
                "delta_u_at_min": 0.01,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.01,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.6,
            },
        },
        parent_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "buy_no", "selected_token_id": "no-1"},
            claims.EXECUTABLE_SNAPSHOT: {"token_id": "no-1"},
            claims.QUOTE_FEASIBILITY: {"direction": "buy_no", "token_id": "no-1", "cost_source": "native_orderbook_bid"},
            claims.COST_MODEL: {"direction": "buy_no", "token_id": "no-1", "cost_source": "native_orderbook_bid"},
        },
    )

    with pytest.raises(CertificateVerificationError, match="quote.cost_source"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_wrong_cost_source_for_sell_yes():
    parents, action = actionable_graph(
        action_payload={"direction": "sell_yes"},
        parent_overrides={
            claims.CANDIDATE_EVIDENCE: {"direction": "sell_yes"},
            claims.QUOTE_FEASIBILITY: {"direction": "sell_yes", "cost_source": "native_orderbook_ask"},
            claims.COST_MODEL: {"direction": "sell_yes", "cost_source": "native_orderbook_ask"},
        },
    )

    with pytest.raises(CertificateVerificationError, match="quote.cost_source"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_low_price_yes_below_roi_frontier_confidence_floor():
    parents, action = actionable_graph(
        action_payload={
            "q_live": 0.65,
            "q_lcb_5pct": 0.55,
            "c_fee_adjusted": 0.54,
            "c_cost_95pct": 0.54,
            "p_fill_lcb": 0.95,
            "trade_score": 0.01,
            "action_score": 0.01,
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.65,
                "payoff_q_lcb": 0.55,
                "cost": 0.54,
                "edge_lcb": 0.01,
                "optimal_delta_u": 0.00009152233738979263,
                "delta_u_at_min": 0.00009152233738979263,
                "optimal_stake_usd": "1.4412832709285736083984375",
                "false_edge_rate": 0.05,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.55,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="roi frontier not useful"):
        verify_actionable_trade(action, parents)


@pytest.mark.parametrize("bad_source", ["midpoint", "complement_price", "last_trade_price"])
def test_actionable_rejects_forbidden_cost_sources(bad_source):
    parents, action = actionable_graph(parent_overrides={claims.COST_MODEL: {"cost_source": bad_source}})

    with pytest.raises(CertificateVerificationError, match="cost.cost_source"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_fdr_family_mismatch():
    parents, action = actionable_graph(parent_overrides={claims.FDR: {"fdr_family_id": "other-family"}})

    with pytest.raises(CertificateVerificationError, match="actionable.family_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_fdr_missing_candidate_hypothesis():
    parents, action = actionable_graph(parent_overrides={claims.FDR: {"selected_hypotheses": ("other",)}})

    with pytest.raises(CertificateVerificationError, match="selected_hypotheses"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_kelly_cost_basis_mismatch():
    parents, action = actionable_graph(parent_overrides={claims.KELLY_DRY_RUN: {"cost_basis_id": "other-cost"}})

    with pytest.raises(CertificateVerificationError, match="kelly.cost_basis_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_risk_not_passed():
    parents, action = actionable_graph(parent_overrides={claims.RISK_LEVEL: {"passed": False}})

    with pytest.raises(CertificateVerificationError, match="risk.passed"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_conservative_bootstrap_when_coverage_history_is_thin():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB",
                "coverage_status": "INSUFFICIENT_DATA",
                "q_lcb_basis": "fused_center_bootstrap_p05",
                "bootstrap_draws": 200,
                "n_samples": 0,
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_conservative_bootstrap_without_draws():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_CONSERVATIVE_Q_LCB",
                "coverage_status": "INSUFFICIENT_DATA",
                "q_lcb_basis": "fused_center_bootstrap_p05",
                "bootstrap_draws": 10,
                "n_samples": 0,
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="bootstrap draw floor"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_fused_bootstrap_below_live_sample_floor():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE",
                "coverage_status": "LICENSED",
                "n_samples": 3,
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="sample floor"):
        verify_actionable_trade(action, parents)


def test_actionable_accepts_fused_bootstrap_with_sampled_license():
    parents, action = actionable_graph(
        parent_overrides={
            claims.CALIBRATION: {
                "authority": "FUSED_BOOTSTRAP_SETTLEMENT_COVERAGE",
                "coverage_status": "LICENSED",
                "n_samples": 60,
            },
        },
    )

    verify_actionable_trade(action, parents)


def test_actionable_rejects_unreserved_live_cap():
    parents, action = actionable_graph(parent_overrides={claims.LIVE_CAP: {"reservation_status": "RELEASED"}})

    with pytest.raises(CertificateVerificationError, match="reservation_status"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_public_market_channel_fill_parent():
    parents, action = actionable_graph(extra_parent_payloads={claims.FILL: {"source_kind": claims.PUBLIC_MARKET_CHANNEL_SOURCE}})

    with pytest.raises(CertificateVerificationError, match="market-channel"):
        verify_actionable_trade(action, parents)


def test_ledger_rejects_forged_actionable_trade_certificate():
    parents, action = actionable_graph(action_payload={"trade_score": -1.0})

    with pytest.raises(CertificateVerificationError, match="trade_score"):
        DecisionCertificateLedger(_conn()).persist_all(parents + (action,))


def test_ledger_rejects_actionable_with_generic_verifier_only_path():
    _, action = actionable_graph(action_payload={"trade_score": -1.0})

    with pytest.raises(CertificateVerificationError, match="missing parent|trade_score"):
        DecisionCertificateLedger(_conn()).insert_idempotent(action)


def actionable_graph(
    *,
    mode: str = "LIVE",
    action_payload: dict | None = None,
    parent_overrides: dict[str, dict] | None = None,
    extra_parent_payloads: dict[str, dict] | None = None,
):
    parent_overrides = parent_overrides or {}
    parent_payloads = _parent_payloads()
    parent_payloads.update(extra_parent_payloads or {})
    parents = []
    for certificate_type, payload in parent_payloads.items():
        merged = {**payload, **parent_overrides.get(certificate_type, {})}
        parents.append(_cert(certificate_type, f"{certificate_type}:event-1", merged, mode="LIVE"))
    parent_tuple = tuple(parents)
    payload = {**_action_payload(), **(action_payload or {})}
    action = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1:candidate-1",
        payload,
        mode=mode,
        parents=parent_tuple,
    )
    return parent_tuple, action


def _parent_payloads() -> dict[str, dict]:
    return {
        claims.CLOCK_MODE: {"mode": "LIVE"},
        claims.CAUSAL_EVENT: {"event_id": "event-1", "causal_snapshot_id": "snap-1"},
        claims.SOURCE_TRUTH: {"event_id": "event-1", "source_status": "LIVE_ELIGIBLE"},
        claims.MARKET_TOPOLOGY: {"family_id": "family-1"},
        claims.FAMILY_CLOSURE: {"family_id": "family-1"},
        claims.FORECAST_AUTHORITY: {"snapshot_id": "snap-1"},
        claims.CALIBRATION: {"calibrator_model_key": "model-1"},
        claims.MODEL_CONFIG: {"calibrator_model_key": "model-1"},
        claims.BELIEF: {"forecast_snapshot_id": "snap-1"},
        claims.EXECUTABLE_SNAPSHOT: {"executable_snapshot_id": "exec-1", "condition_id": "condition-1", "token_id": "yes-1"},
        claims.QUOTE_FEASIBILITY: {
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "direction": "buy_yes",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
        },
        claims.COST_MODEL: {
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "direction": "buy_yes",
            "cost_basis_id": "cost-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
        },
        claims.PRE_TRADE_EVIDENCE: {"native_quote_available": True},
        claims.CANDIDATE_EVIDENCE: {
            "family_id": "family-1",
            "candidate_id": "candidate-1",
            "condition_id": "condition-1",
            "selected_token_id": "yes-1",
            "direction": "buy_yes",
            "hypothesis_id": "family-1:yes-1",
        },
        claims.TESTING_PROTOCOL: {"protocol": "live_canary"},
        claims.FDR: {"fdr_family_id": "family-1", "selected_hypotheses": ("family-1:yes-1",)},
        claims.KELLY_DRY_RUN: {"kelly_decision_id": "kelly-1", "cost_basis_id": "cost-1", "passed": True},
        claims.RISK_LEVEL: {"risk_decision_id": "risk-1", "passed": True},
        claims.LIVE_CAP: {
            "usage_id": "cap-1",
            "event_id": "event-1",
            "reservation_status": "RESERVED",
            "max_notional_usd": 5.0,
        },
    }


def _action_payload() -> dict:
    return {
        "event_id": "event-1",
        "event_type": "FORECAST_SNAPSHOT_READY",
        "causal_snapshot_id": "snap-1",
        "family_id": "family-1",
        "candidate_id": "candidate-1",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "direction": "buy_yes",
        "strategy_key": "center_buy",
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
        "selection_authority_applied": "qkernel_spine",
        "qkernel_execution_economics": {
            "source": "qkernel_spine",
            "side": "YES",
            "payoff_q_point": 0.7,
            "payoff_q_lcb": 0.6,
            "cost": 0.4,
            "edge_lcb": 0.2,
            "optimal_delta_u": 0.01,
            "delta_u_at_min": 0.01,
            "optimal_stake_usd": 5.0,
            "false_edge_rate": 0.01,
            "direction_law_ok": True,
            "coherence_allows": True,
            "selection_guard_basis": "SELECTION_BETA_95",
            "selection_guard_abstained": False,
            "selection_guard_q_safe": 0.6,
        },
        "fdr_family_id": "family-1",
        "kelly_decision_id": "kelly-1",
        "kelly_size_usd": 3.0,
        "risk_decision_id": "risk-1",
        "live_cap_usage_id": "cap-1",
        "final_intent_id": "intent-1",
        "side_effect_status": "ACTIONABLE_NOT_SUBMITTED",
        "native_quote_available": True,
        "submitted": False,
    }


def _cert(certificate_type: str, semantic_key: str, payload: dict, *, mode: str = "LIVE", parents=()):
    return build_certificate(
        certificate_type=certificate_type,
        semantic_key=semantic_key,
        claim_type=certificate_type,
        mode=mode,
        decision_time=NOW,
        source_available_at=NOW,
        agent_received_at=NOW,
        persisted_at=NOW,
        payload=payload,
        parent_edges=tuple(ParentEdge(_role(parent.certificate_type), parent.certificate_hash, parent.certificate_type) for parent in parents),
        parent_certificates=tuple(parents),
        authority_id="test",
        authority_version="v1",
        algorithm_id="test",
        algorithm_version="v1",
    )


def _role(certificate_type: str) -> str:
    import re

    base = certificate_type.removesuffix("Certificate").replace("Evidence", "")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn
