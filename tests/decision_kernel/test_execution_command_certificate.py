# Lifecycle: created=2026-05-25; last_reviewed=2026-07-10; last_reused=2026-07-10
# Purpose: Prove live execution certificates preserve authority, sizing, and submit invariants.
# Reuse: Re-audit final-intent, pre-submit, command, and verifier field closure before relying on it.
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import DecisionCertificateLedger
from src.decision_kernel.verifier import (
    verify_execution_command,
    verify_execution_receipt,
    verify_executor_expressibility,
    verify_final_intent,
)
from src.decision_kernel.certificates.execution import (
    build_execution_command_certificate_from_final_intent,
    build_execution_receipt_certificate,
    build_executor_expressibility_certificate,
    build_final_intent_certificate_from_actionable,
)
from src.engine.event_bound_final_intent import (
    _final_execution_intent_from_payload,
    validate_final_intent_cert_for_existing_executor,
)


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)


def test_execution_command_requires_live_mode():
    parents, command = execution_graph(mode="NO_SUBMIT")

    with pytest.raises(CertificateVerificationError, match="LIVE mode"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_no_submit_parent_certificate():
    parents, command = execution_graph(parent_modes={claims.PRE_SUBMIT_REVALIDATION: "NO_SUBMIT"})

    with pytest.raises(CertificateVerificationError, match="requires LIVE parent certificates"):
        verify_execution_command(command, parents)


def test_execution_command_requires_actionable_parent():
    parents, command = execution_graph(drop_parent=claims.ACTIONABLE_TRADE)

    with pytest.raises(CertificateVerificationError, match="ActionableTradeCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_final_intent_parent():
    parents, command = execution_graph(drop_parent=claims.FINAL_INTENT)

    with pytest.raises(CertificateVerificationError, match="FinalIntentCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_executor_expressibility_parent():
    parents, command = execution_graph(drop_parent=claims.EXECUTOR_EXPRESSIBILITY)

    with pytest.raises(CertificateVerificationError, match="ExecutorExpressibilityCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_live_cap_parent():
    parents, command = execution_graph(drop_parent=claims.LIVE_CAP)

    with pytest.raises(CertificateVerificationError, match="LiveCapCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_requires_pre_submit_revalidation_parent():
    parents, command = execution_graph(drop_parent=claims.PRE_SUBMIT_REVALIDATION)

    with pytest.raises(CertificateVerificationError, match="PreSubmitRevalidationCertificate"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_submitted_true_before_executor():
    parents, command = execution_graph(command_payload={"submitted": True})

    with pytest.raises(CertificateVerificationError, match="submitted=false"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_wrong_token():
    parents, command = execution_graph(command_payload={"token_id": "other-token"})

    with pytest.raises(CertificateVerificationError, match="token_id"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_wrong_condition():
    parents, command = execution_graph(command_payload={"condition_id": "other-condition"})

    with pytest.raises(CertificateVerificationError, match="condition_id"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_wrong_direction():
    parents, command = execution_graph(command_payload={"direction": "sell_yes"})

    with pytest.raises(CertificateVerificationError, match="direction"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_size_below_min_order():
    parents, command = execution_graph(
        actionable_payload={"strategy_key": "unit_test_entry_floor"},
        final_payload={"size": 0.5, "notional_usd": 0.2},
        command_payload={"size": 0.5, "min_order_size": 1.0},
    )

    with pytest.raises(CertificateVerificationError, match="min_order_size"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_presubmit_price_below_strategy_entry_floor():
    parents, command = execution_graph(
        final_payload={
            "limit_price": 0.003,
            "size": 384.79,
            "min_entry_price": 0.05,
            "notional_usd": 1.15437,
            "selection_authority_applied": None,
        },
        command_payload={
            "limit_price": 0.003,
            "q_live": 0.70,
            "q_lcb_5pct": 0.60,
            "expected_edge": 0.597,
            "size": 384.79,
            "min_entry_price": 0.05,
            "min_expected_profit_usd": 1.0,
            "min_submit_edge_density": 0.05,
            "selection_authority_applied": None,
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.70,
                "payoff_q_lcb": 0.60,
                "cost": 0.003,
                "edge_lcb": 0.597,
                "optimal_delta_u": 0.005,
                "delta_u_at_min": 0.005,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.001,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.60,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="below strategy entry floor"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_low_price_with_qkernel_authority_and_profit_floor():
    parents, command = execution_graph(
        final_payload={
            "limit_price": 0.01,
            "size": 384.79,
            "min_entry_price": 0.05,
            "notional_usd": 3.8479,
        },
        command_payload={
            "limit_price": 0.01,
            "q_live": 0.70,
            "q_lcb_5pct": 0.60,
            "expected_edge": 0.59,
            "size": 384.79,
            "min_entry_price": 0.05,
            "min_expected_profit_usd": 1.0,
            "min_submit_edge_density": 0.05,
            "qkernel_execution_economics": {
                "source": "qkernel_spine",
                "side": "YES",
                "payoff_q_point": 0.70,
                "payoff_q_lcb": 0.60,
                "cost": 0.01,
                "edge_lcb": 0.59,
                "optimal_delta_u": 0.005,
                "delta_u_at_min": 0.005,
                "optimal_stake_usd": 5.0,
                "false_edge_rate": 0.001,
                "direction_law_ok": True,
                "coherence_allows": True,
                "selection_guard_basis": "SELECTION_BETA_95",
                "selection_guard_abstained": False,
                "selection_guard_q_safe": 0.60,
            },
        },
    )

    with pytest.raises(CertificateVerificationError, match="below strategy entry floor"):
        verify_execution_command(command, parents)


def test_execution_command_has_no_max_notional_ceiling():
    # 2026-06-08: the tiny_live max_notional cap is DELETED. The execution command
    # verifier no longer rejects a size whose notional exceeds any max_notional —
    # order size is governed solely by structural fractional-Kelly sizing upstream.
    # A size that the old cap would have rejected (20 * 0.40 = 8.0) now verifies.
    parents, command = execution_graph(
        final_payload={"size": 20.0, "notional_usd": 8.0},
        command_payload={"size": 20.0, "limit_price": 0.40},
    )

    verify_execution_command(command, parents)


def test_maker_final_intent_size_is_frozen_to_venue_submit_grid():
    actionable, final_intent, _expressibility, _live_cap = builder_chain(
        actionable_payload={
            **_actionable_payload(),
            "c_fee_adjusted": 0.75,
            "kelly_size_usd": 3.8,
            "live_cap_reserved_notional_usd": 3.8,
        },
        final_payload=None,
    )

    assert actionable.payload["direction"] == "buy_yes"
    assert final_intent.payload["post_only"] is True
    assert final_intent.payload["time_in_force"] == "GTC"
    assert final_intent.payload["limit_price"] == 0.75
    assert final_intent.payload["size"] == 5.06
    assert final_intent.payload["notional_usd"] == pytest.approx(3.795)


def test_event_bound_final_intent_normalizes_legacy_fractional_maker_size():
    _actionable, final_intent, _expressibility, _live_cap = builder_chain(
        final_payload={
            "limit_price": 0.75,
            "expected_fill_price_before_fee": 0.75,
            "size": 5.066666666666666,
            "notional_usd": 3.8,
            "executor_order_type": "GTC",
            "time_in_force": "GTC",
            "post_only": True,
            "maker_intent": True,
            "tick_size": "0.01",
            "min_order_size": 1.0,
        }
    )

    native = _final_execution_intent_from_payload(final_intent.payload)

    assert native.size_kind == "shares"
    assert native.size_value == native.submitted_shares
    assert native.submitted_shares == Decimal("5.06")
    assert native.actionable_certificate_hash == final_intent.payload["actionable_certificate_hash"]


def test_day0_final_intent_preserves_observation_authority_fields():
    _actionable, final_intent, _expressibility, _live_cap = builder_chain(
        actionable_payload={
            "event_type": "DAY0_EXTREME_UPDATED",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
        }
    )

    assert final_intent.payload["event_type"] == "DAY0_EXTREME_UPDATED"
    assert final_intent.payload["selection_authority_applied"] == "qkernel_spine"
    assert final_intent.payload["qkernel_execution_economics"]["source"] == "qkernel_spine"
    assert final_intent.payload["source_match_status"] == "MATCH"
    assert final_intent.payload["local_date_status"] == "MATCH"
    assert final_intent.payload["station_match_status"] == "MATCH"
    assert final_intent.payload["dst_status"] == "UNAMBIGUOUS"
    assert final_intent.payload["metric_match_status"] == "MATCH"
    assert final_intent.payload["rounding_status"] == "MATCH"
    assert final_intent.payload["source_authorized_status"] == "AUTHORIZED"
    assert final_intent.payload["live_authority_status"] == "live"


def test_execution_command_rejects_missing_pre_submit_qkernel_economics():
    parents, command = execution_graph(
        command_payload={"qkernel_execution_economics": None},
    )

    with pytest.raises(CertificateVerificationError, match="qkernel_execution_economics"):
        verify_execution_command(command, parents)


def test_execution_command_accepts_day0_observation_authority_with_qkernel():
    day0_authority = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    parents, command = execution_graph(
        actionable_payload=day0_authority,
        command_payload=day0_authority,
    )

    verify_execution_command(command, parents)


def test_execution_command_rejects_day0_missing_qkernel_economics():
    day0_authority = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "selection_authority_applied": None,
        "qkernel_execution_economics": None,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    parents, command = execution_graph(
        actionable_payload=day0_authority,
        command_payload=day0_authority,
    )

    with pytest.raises(CertificateVerificationError, match="qkernel_execution_economics"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_missing_qkernel_selection_guard():
    economics = dict(_actionable_payload()["qkernel_execution_economics"])
    economics.pop("selection_guard_basis")
    economics.pop("selection_guard_abstained")
    economics.pop("selection_guard_q_safe")
    parents, command = execution_graph(
        command_payload={"qkernel_execution_economics": economics},
    )

    with pytest.raises(CertificateVerificationError, match="selection_guard_basis"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_side_not_armed_qkernel_selection_guard():
    economics = dict(_actionable_payload()["qkernel_execution_economics"])
    economics["selection_guard_basis"] = "SIDE_NOT_ARMED"
    parents, command = execution_graph(
        command_payload={"qkernel_execution_economics": economics},
    )

    with pytest.raises(CertificateVerificationError, match="selection_guard_basis blocks side"):
        verify_execution_command(command, parents)


def test_execution_command_verifies_without_cap_flag():
    # The cap-enabled flag is gone from both the live_cap and actionable payloads;
    # verification does not depend on it and still passes.
    parents, command = execution_graph(
        final_payload={"size": 20.0, "notional_usd": 8.0},
        command_payload={"size": 20.0, "limit_price": 0.40},
    )

    verify_execution_command(command, parents)


def test_execution_command_rejects_tick_misaligned_price():
    parents, command = execution_graph(
        final_payload={"limit_price": 0.333, "notional_usd": 3.33},
        command_payload={"limit_price": 0.333, "tick_size": 0.01},
    )

    with pytest.raises(CertificateVerificationError, match="tick-aligned"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_missing_idempotency_key():
    parents, command = execution_graph(command_payload={"idempotency_key": ""})

    with pytest.raises(CertificateVerificationError, match="idempotency_key"):
        verify_execution_command(command, parents)


def test_execution_command_rejects_venue_order_id_before_submit():
    parents, command = execution_graph(command_payload={"venue_order_id": "venue-1"})

    with pytest.raises(CertificateVerificationError, match="venue_order_id"):
        verify_execution_command(command, parents)


def test_final_intent_requires_actionable_parent():
    _, final_intent = final_intent_graph(drop_parent=claims.ACTIONABLE_TRADE)

    with pytest.raises(CertificateVerificationError, match="ActionableTradeCertificate"):
        verify_final_intent(final_intent, ())


def test_final_intent_rejects_no_submit_parent_certificate():
    parents, final_intent = final_intent_graph(parent_modes={claims.ACTIONABLE_TRADE: "NO_SUBMIT"})

    with pytest.raises(CertificateVerificationError, match="requires LIVE parent certificates"):
        verify_final_intent(final_intent, parents)


def test_final_intent_matches_actionable_event_token_condition_direction():
    parents, final_intent = final_intent_graph()

    verify_final_intent(final_intent, parents)


def test_replacement_no_bound_certificate_survives_final_presubmit_and_command() -> None:
    bound = {
        "schema": "replacement_native_no_bound_v1",
        "certificate_hash": "f" * 64,
    }
    parents, command = execution_graph(
        actionable_payload={"replacement_no_bound_certificate": bound},
    )
    by_type = {parent.certificate_type: parent for parent in parents}
    final_intent = by_type[claims.FINAL_INTENT]
    pre_submit = by_type[claims.PRE_SUBMIT_REVALIDATION]

    assert final_intent.payload["replacement_no_bound_certificate"] == bound
    assert pre_submit.payload["replacement_no_bound_certificate"] == bound
    assert command.payload["replacement_no_bound_certificate_hash"] == "f" * 64
    verify_execution_command(command, parents)


def test_final_intent_rejects_wrong_token():
    parents, final_intent = final_intent_graph(final_payload={"token_id": "other-token"})

    with pytest.raises(CertificateVerificationError, match="token_id"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_wrong_condition():
    parents, final_intent = final_intent_graph(final_payload={"condition_id": "other-condition"})

    with pytest.raises(CertificateVerificationError, match="condition_id"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_wrong_direction():
    parents, final_intent = final_intent_graph(final_payload={"direction": "sell_yes"})

    with pytest.raises(CertificateVerificationError, match="direction"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_missing_order_type():
    parents, final_intent = final_intent_graph(final_payload={"order_type": ""})

    with pytest.raises(CertificateVerificationError, match="order_type"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_venue_order_id_before_submit():
    parents, final_intent = final_intent_graph(final_payload={"venue_order_id": "venue-1"})

    with pytest.raises(CertificateVerificationError, match="venue_order_id"):
        verify_final_intent(final_intent, parents)


def test_final_intent_rejects_price_below_current_live_entry_floor():
    parents, final_intent = final_intent_graph(
        final_payload={
            "limit_price": 0.019,
            "min_entry_price": 0.01,
            "notional_usd": 0.19,
            "selection_authority_applied": None,
        }
    )

    with pytest.raises(CertificateVerificationError, match="limit_price below strategy entry floor"):
        verify_final_intent(final_intent, parents)


def test_final_intent_allows_price_equal_current_live_entry_floor():
    parents, final_intent = final_intent_graph(
        final_payload={
            "limit_price": 0.10,
            "min_entry_price": 0.10,
            "notional_usd": 1.0,
            "selection_authority_applied": None,
        }
    )

    verify_final_intent(final_intent, parents)


def test_final_intent_notional_must_not_exceed_reserved_integrity_guard():
    # PRESERVED guarantee (NOT a cap): the order notional must never exceed the
    # Kelly-sized notional that was reserved for this event. This integrity guard
    # now runs UNCONDITIONALLY (no cap-enabled flag gates it). notional 4.0 >
    # reserved 3.0 must raise.
    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        {
            **_actionable_payload(),
            "live_cap_reserved_notional_usd": 3.0,
        },
    )
    final_intent = _cert(
        claims.FINAL_INTENT,
        "final-intent:intent-1",
        {**_final_intent_payload(actionable), "notional_usd": 4.0},
        parents=(actionable,),
    )

    with pytest.raises(CertificateVerificationError, match="exceeds live cap reserved notional"):
        verify_final_intent(final_intent, (actionable,))


def test_final_intent_notional_at_or_below_reserved_passes_integrity_guard():
    # The order≤reserved integrity guard admits an order whose notional matches the
    # reserved Kelly notional. No cap-enabled flag is consulted.
    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        {
            **_actionable_payload(),
            "live_cap_reserved_notional_usd": 5.0,
        },
    )
    final_intent = _cert(
        claims.FINAL_INTENT,
        "final-intent:intent-1",
        {**_final_intent_payload(actionable), "notional_usd": 4.0},
        parents=(actionable,),
    )

    verify_final_intent(final_intent, (actionable,))


def test_final_intent_notional_float_roundtrip_ulp_passes_integrity_guard():
    """ANTIBODY (live 2026-06-11, Amsterdam 20:26/20:56Z + Lucknow 21:38Z): the
    maker sizing contract is size = reserved/price (float), notional =
    size*price — IEEE754 makes (r/p)*p exceed r by ~1 ULP for a large fraction
    of (r, p) pairs, and the strict > guard hard-killed correctly-sized maker
    intents at random (terminal dead-letter, opportunity consumed). The guard
    must tolerate the round-trip artifact; material excess must still raise."""
    # Real ULP-overflow pair: (14.634415443986683 / 0.217) * 0.217 exceeds the
    # reservation by 1.78e-15.
    reserved = 14.634415443986683
    price = 0.217
    notional = (reserved / price) * price
    assert notional > reserved  # the artifact this antibody exists for

    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        {
            **_actionable_payload(),
            "live_cap_reserved_notional_usd": reserved,
        },
    )
    final_intent = _cert(
        claims.FINAL_INTENT,
        "final-intent:intent-1",
        {
            **_final_intent_payload(actionable),
            "limit_price": price,
            "size": reserved / price,
            "notional_usd": notional,
        },
        parents=(actionable,),
    )

    verify_final_intent(final_intent, (actionable,))

    # Material excess (1 cent on ~$14.6) still raises — the guard is not weakened.
    material = _cert(
        claims.FINAL_INTENT,
        "final-intent:intent-2",
        {
            **_final_intent_payload(actionable),
            "limit_price": price,
            "notional_usd": reserved + 0.01,
        },
        parents=(actionable,),
    )
    with pytest.raises(CertificateVerificationError, match="exceeds live cap reserved notional"):
        verify_final_intent(material, (actionable,))


def test_executor_expressibility_requires_final_intent_parent():
    parents, expressibility = executor_expressibility_graph(drop_parent=claims.FINAL_INTENT)

    with pytest.raises(CertificateVerificationError, match="FinalIntentCertificate"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_requires_can_express_true():
    parents, expressibility = executor_expressibility_graph(express_payload={"can_express": False})

    with pytest.raises(CertificateVerificationError, match="can_express"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_tick_misaligned_price():
    parents, expressibility = executor_expressibility_graph(
        final_payload={"limit_price": 0.333, "notional_usd": 3.33},
        express_payload={"limit_price": 0.333},
    )

    with pytest.raises(CertificateVerificationError, match="tick-aligned"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_size_below_min_order():
    parents, expressibility = executor_expressibility_graph(
        final_payload={"size": 0.1, "notional_usd": 0.04},
        express_payload={"size": 0.1, "min_order_size": 1.0},
    )

    with pytest.raises(CertificateVerificationError, match="min_order_size"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_price_below_final_intent_live_floor():
    parents, expressibility = executor_expressibility_graph(
        final_payload={
            "limit_price": 0.07,
            "min_entry_price": 0.05,
            "notional_usd": 0.70,
            "selection_authority_applied": None,
        }
    )

    with pytest.raises(CertificateVerificationError, match="limit_price below strategy entry floor"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_allows_price_equal_final_intent_live_floor():
    parents, expressibility = executor_expressibility_graph(
        final_payload={
            "limit_price": 0.10,
            "min_entry_price": 0.10,
            "notional_usd": 1.0,
            "selection_authority_applied": None,
        }
    )

    verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_neg_risk_mismatch():
    parents, expressibility = executor_expressibility_graph(
        express_payload={"neg_risk": True},
        executable_payload={"neg_risk": False},
    )

    with pytest.raises(CertificateVerificationError, match="neg_risk"):
        verify_executor_expressibility(expressibility, parents)


def test_executor_expressibility_rejects_taker_order_when_executor_law_requires_maker():
    parents, expressibility = executor_expressibility_graph(
        final_payload={"post_only": False},
        express_payload={"post_only": False},
    )

    with pytest.raises(CertificateVerificationError, match="passive maker"):
        verify_executor_expressibility(expressibility, parents)


def test_execution_command_builder_preserves_event_token_condition_direction():
    actionable, final_intent, expressibility, live_cap = builder_chain()
    pre_submit = _pre_submit_cert(final_intent, live_cap)

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )

    assert command.payload["event_id"] == actionable.payload["event_id"]
    assert command.payload["token_id"] == actionable.payload["token_id"]
    assert command.payload["condition_id"] == actionable.payload["condition_id"]
    assert command.payload["direction"] == actionable.payload["direction"]
    verify_execution_command(command, (actionable, final_intent, expressibility, live_cap, pre_submit))


def test_execution_command_and_receipt_stamp_process_boot_sha(monkeypatch):
    boot_sha = "a" * 40
    monkeypatch.setenv("ZEUS_PROCESS_BOOT_SHA", boot_sha)
    actionable, final_intent, expressibility, live_cap = builder_chain()
    pre_submit = _pre_submit_cert(final_intent, live_cap)

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
    )

    assert command.payload["process_boot_sha"] == boot_sha
    assert command.payload["runtime_sha"] == boot_sha
    assert receipt.payload["process_boot_sha"] == boot_sha
    assert receipt.payload["runtime_sha"] == boot_sha
    verify_execution_command(command, (actionable, final_intent, expressibility, live_cap, pre_submit))
    verify_execution_receipt(receipt, (command,))


def test_execution_command_rejects_command_price_drift_from_final_intent():
    parents, command = execution_graph(
        command_payload={
            "limit_price": 0.39,
            "expected_edge": 0.21,
            "q_lcb_5pct": 0.60,
            "qkernel_execution_economics": {
                **_actionable_payload()["qkernel_execution_economics"],
                "cost": 0.39,
                "edge_lcb": 0.21,
            },
        }
    )

    with pytest.raises(CertificateVerificationError, match="final_intent.limit_price"):
        verify_execution_command(command, parents)


def test_final_intent_builder_rejects_no_submit_parent_certificate():
    with pytest.raises(ValueError, match="requires LIVE parent certificates"):
        builder_chain(parent_modes={claims.QUOTE_FEASIBILITY: "NO_SUBMIT"})


def test_final_intent_builder_rejects_entry_price_below_current_live_floor():
    with pytest.raises(ValueError, match="CERT_BUILD_ENTRY_PRICE_BELOW_STRATEGY_FLOOR"):
        builder_chain(
            actionable_payload={
                "c_fee_adjusted": 0.02,
                "c_cost_95pct": 0.02,
                "q_lcb_5pct": 0.30,
                "trade_score": 0.28,
                "action_score": 0.28,
                "min_entry_price": 0.02,
                "selection_authority_applied": None,
                "qkernel_execution_economics": {
                    **_actionable_payload()["qkernel_execution_economics"],
                    "cost": 0.02,
                    "edge_lcb": 0.28,
                },
            }
        )


def test_final_intent_builder_allows_price_equal_current_live_floor():
    actionable, final_intent, _expressibility, _live_cap = builder_chain(
        actionable_payload={
            "c_fee_adjusted": 0.10,
            "c_cost_95pct": 0.10,
            "q_lcb_5pct": 0.40,
            "trade_score": 0.30,
            "action_score": 0.30,
            "kelly_size_usd": 1.0,
            "min_entry_price": 0.10,
            "selection_authority_applied": None,
            "qkernel_execution_economics": None,
        }
    )

    assert final_intent.payload["limit_price"] == pytest.approx(0.10)
    assert final_intent.payload["min_entry_price"] == pytest.approx(0.10)


def test_final_intent_builder_allows_center_buy_yes_above_micro_tail_floor():
    _actionable, final_intent, _expressibility, _live_cap = builder_chain(
        actionable_payload={
            "c_fee_adjusted": 0.03,
            "c_cost_95pct": 0.03,
            "q_live": 0.12,
            "q_lcb_5pct": 0.08,
            "trade_score": 0.05,
            "action_score": 0.05,
            "min_entry_price": 0.02,
            "qkernel_execution_economics": {
                **_actionable_payload()["qkernel_execution_economics"],
                "payoff_q_point": 0.12,
                "payoff_q_lcb": 0.08,
                "cost": 0.03,
                "edge_lcb": 0.05,
                "selection_guard_q_safe": 0.08,
            },
        }
    )

    assert final_intent.payload["strategy_key"] == "center_buy"
    assert final_intent.payload["direction"] == "buy_yes"
    assert final_intent.payload["limit_price"] == pytest.approx(0.03)
    assert final_intent.payload["min_entry_price"] == pytest.approx(0.02)


def test_final_intent_builder_rejects_direct_qkernel_yes_below_strategy_floor():
    with pytest.raises(ValueError, match="CERT_BUILD_ENTRY_PRICE_BELOW_STRATEGY_FLOOR"):
        builder_chain(
            actionable_payload={
                "c_fee_adjusted": 0.01,
                "c_cost_95pct": 0.01,
                "q_live": 0.82,
                "q_lcb_5pct": 0.72,
                "trade_score": 0.71,
                "action_score": 0.71,
                "min_entry_price": 0.10,
                "qkernel_execution_economics": {
                    **_actionable_payload()["qkernel_execution_economics"],
                    "candidate_id": "YES:b20:DIRECT_YES:b20@proof",
                    "bin_id": "b20",
                    "route_id": "DIRECT_YES:b20@proof",
                    "route_type": "direct",
                    "payoff_q_point": 0.82,
                    "payoff_q_lcb": 0.72,
                    "cost": 0.01,
                    "edge_lcb": 0.71,
                    "delta_u_at_min": 0.01,
                    "optimal_stake_usd": 100.0,
                    "optimal_delta_u": 0.01,
                    "selection_guard_q_safe": 0.72,
                },
            }
        )


def test_execution_command_builder_rejects_no_submit_parent_certificate():
    actionable, final_intent, expressibility, live_cap = builder_chain()
    live_pre_submit = _pre_submit_cert(final_intent, live_cap)
    no_submit_pre_submit = _cert(
        claims.PRE_SUBMIT_REVALIDATION,
        "pre-submit:event-1:intent-1:no-submit",
        live_pre_submit.payload,
        mode="NO_SUBMIT",
        parents=(final_intent, live_cap),
    )

    with pytest.raises(ValueError, match="requires LIVE parent certificates"):
        build_execution_command_certificate_from_final_intent(
            actionable_cert=actionable,
            final_intent_cert=final_intent,
            executor_expressibility_cert=expressibility,
            live_cap_cert=live_cap,
            pre_submit_revalidation_cert=no_submit_pre_submit,
            decision_time=NOW,
        )


def test_execution_command_builder_deterministic_idempotency_key():
    actionable, final_intent, expressibility, live_cap = builder_chain()
    pre_submit = _pre_submit_cert(final_intent, live_cap)

    first = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )
    second = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )

    assert first.payload["idempotency_key"] == second.payload["idempotency_key"]


def test_execution_command_builder_no_venue_order_id_before_submit():
    actionable, final_intent, expressibility, live_cap = builder_chain()
    pre_submit = _pre_submit_cert(final_intent, live_cap)

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )

    assert command.payload["venue_order_id"] is None
    assert command.payload["submitted"] is False


def test_execution_command_builder_rejects_invalid_final_intent_parent():
    actionable, final_intent, expressibility, live_cap = builder_chain(final_payload={"token_id": "other-token"})
    pre_submit = _pre_submit_cert(final_intent, live_cap)

    command = build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )

    with pytest.raises(CertificateVerificationError, match="token_id"):
        verify_execution_command(command, (actionable, final_intent, expressibility, live_cap, pre_submit))


def test_execution_receipt_submit_disabled_has_no_venue_order_id():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(execution_command_cert=command, decision_time=NOW)

    assert receipt.payload["status"] == "SUBMIT_DISABLED"
    assert receipt.payload["venue_order_id"] is None
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_matches_execution_command():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(execution_command_cert=command, decision_time=NOW)

    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_submitted_fixture_response_verifies():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="SUBMITTED",
        reason_code="OK",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        venue_order_id="venue-1",
        raw_response={"status": "submitted"},
    )

    assert receipt.payload["venue_order_id"] == "venue-1"
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_rejected_fixture_response_verifies():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="REJECTED",
        reason_code="VENUE_REJECTED",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        raw_response={"status": "rejected"},
    )

    assert receipt.payload["status"] == "REJECTED"
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_timeout_requires_reconcile_followup():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="TIMEOUT_UNKNOWN",
        reason_code="SUBMIT_TIMEOUT",
    )

    with pytest.raises(CertificateVerificationError, match="reconciliation"):
        verify_execution_receipt(receipt, (command,))


def test_execution_receipt_timeout_fixture_response_verifies_with_reconcile_followup():
    command = receipt_command()
    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="TIMEOUT_UNKNOWN",
        reason_code="SUBMIT_TIMEOUT",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        raw_response={"status": "timeout"},
        reconciliation_followup_required=True,
    )

    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_post_submit_unknown_requires_unknown_side_effect_fields():
    command = receipt_command()
    missing = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="POST_SUBMIT_UNKNOWN",
        reason_code="SDK_EXCEPTION_AFTER_SEND",
        reconciliation_followup_required=True,
    )
    with pytest.raises(CertificateVerificationError, match="venue_call_started"):
        verify_execution_receipt(missing, (command,))

    receipt = build_execution_receipt_certificate(
        execution_command_cert=command,
        decision_time=NOW,
        status="POST_SUBMIT_UNKNOWN",
        reason_code="SDK_EXCEPTION_AFTER_SEND",
        submit_started_at=NOW.isoformat(),
        submit_finished_at=NOW.isoformat(),
        raw_response={"status": "exception_after_send"},
        reconciliation_followup_required=True,
        venue_call_started=True,
        venue_ack_received=False,
        side_effect_known=False,
    )
    verify_execution_receipt(receipt, (command,))


def test_execution_receipt_accepted_not_equal_filled():
    command = receipt_command()
    receipt = _cert(
        claims.EXECUTION_RECEIPT,
        "execution-receipt:accepted",
        {
            "event_id": command.payload["event_id"],
            "execution_command_id": command.payload["execution_command_id"],
            "final_intent_id": command.payload["final_intent_id"],
            "executor_name": command.payload["executor_name"],
            "status": "ACCEPTED",
            "submit_started_at": NOW.isoformat(),
            "submit_finished_at": NOW.isoformat(),
            "venue_order_id": "venue-1",
            "raw_response_hash": "hash",
            "idempotency_key": command.payload["idempotency_key"],
            "reason_code": "OK",
        },
        parents=(command,),
    )

    verify_execution_receipt(receipt, (command,))
    assert receipt.payload["status"] == "ACCEPTED"


def test_ledger_rejects_forged_execution_command_certificate():
    parents, command = execution_graph(command_payload={"limit_price": 0.333})

    with pytest.raises(CertificateVerificationError, match="tick-aligned|actionable trade missing parents"):
        DecisionCertificateLedger(_conn()).persist_all(parents + (command,))


def test_ledger_rejects_execution_command_with_generic_verifier_only_path():
    _, command = execution_graph(command_payload={"submitted": True})

    with pytest.raises(CertificateVerificationError, match="missing parent|ActionableTradeCertificate|submitted=false"):
        DecisionCertificateLedger(_conn()).insert_idempotent(command)


def execution_graph(
    *,
    mode: str = "LIVE",
    parent_modes: dict[str, str] | None = None,
    actionable_payload: dict | None = None,
    live_cap_payload: dict | None = None,
    final_payload: dict | None = None,
    command_payload: dict | None = None,
    drop_parent: str | None = None,
):
    parent_modes = parent_modes or {}
    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        {**_actionable_payload(), **(actionable_payload or {})},
        mode=parent_modes.get(claims.ACTIONABLE_TRADE, "LIVE"),
    )
    final_intent = _cert(
        claims.FINAL_INTENT,
        "final-intent:intent-1",
        {**_final_intent_payload(actionable), **(final_payload or {})},
        mode=parent_modes.get(claims.FINAL_INTENT, "LIVE"),
        parents=(actionable,),
    )
    expressibility = _cert(
        claims.EXECUTOR_EXPRESSIBILITY,
        "executor-expressibility:intent-1",
        _expressibility_payload(final_intent),
        mode=parent_modes.get(claims.EXECUTOR_EXPRESSIBILITY, "LIVE"),
        parents=(final_intent,),
    )
    live_cap = _cert(
        claims.LIVE_CAP,
        "live-cap:cap-1",
        {
            "usage_id": "cap-1",
            "event_id": "event-1",
            "reservation_status": "RESERVED",
            "max_notional_usd": 5.0,
            **(live_cap_payload or {}),
        },
        mode=parent_modes.get(claims.LIVE_CAP, "LIVE"),
    )
    payload = {**_command_payload(actionable), **(command_payload or {})}
    pre_submit = _pre_submit_cert(final_intent, live_cap, payload)
    if pre_submit_mode := parent_modes.get(claims.PRE_SUBMIT_REVALIDATION):
        pre_submit = _cert(
            claims.PRE_SUBMIT_REVALIDATION,
            "pre-submit:event-1:intent-1:parent-mode",
            pre_submit.payload,
            mode=pre_submit_mode,
            parents=(final_intent, live_cap),
        )
    parents = tuple(
        parent
        for parent in (actionable, final_intent, expressibility, live_cap, pre_submit)
        if parent.certificate_type != drop_parent
    )
    command = _cert(claims.EXECUTION_COMMAND, "execution-command:cmd-1", payload, mode=mode, parents=parents)
    return parents, command


def final_intent_graph(
    *,
    final_payload: dict | None = None,
    parent_modes: dict[str, str] | None = None,
    drop_parent: str | None = None,
):
    parent_modes = parent_modes or {}
    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        _actionable_payload(),
        mode=parent_modes.get(claims.ACTIONABLE_TRADE, "LIVE"),
    )
    parents = tuple(parent for parent in (actionable,) if parent.certificate_type != drop_parent)
    payload = {**_final_intent_payload(actionable), **(final_payload or {})}
    final_intent = _cert(claims.FINAL_INTENT, "final-intent:intent-1", payload, parents=parents)
    return parents, final_intent


def executor_expressibility_graph(
    *,
    express_payload: dict | None = None,
    final_payload: dict | None = None,
    executable_payload: dict | None = None,
    drop_parent: str | None = None,
):
    final_parents, final_intent = final_intent_graph(final_payload=final_payload)
    executable = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {"condition_id": "condition-1", "token_id": "yes-1", "neg_risk": False, **(executable_payload or {})},
    )
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    parents = tuple(
        parent for parent in (final_intent, executable, live_cap) if parent.certificate_type != drop_parent
    )
    payload = {**_expressibility_payload(final_intent), **(express_payload or {})}
    expressibility = _cert(claims.EXECUTOR_EXPRESSIBILITY, "executor-expressibility:intent-1", payload, parents=parents)
    return parents, expressibility


def builder_chain(
    final_payload: dict | None = None,
    actionable_payload: dict | None = None,
    parent_modes: dict[str, str] | None = None,
):
    parent_modes = parent_modes or {}
    actionable = _cert(
        claims.ACTIONABLE_TRADE,
        "actionable:event-1",
        {
            **_actionable_payload(),
            "live_cap_reserved_notional_usd": 5.0,
            "neg_risk": False,
            **(actionable_payload or {}),
        },
        mode=parent_modes.get(claims.ACTIONABLE_TRADE, "LIVE"),
    )
    forecast = _cert(
        claims.FORECAST_AUTHORITY,
        "forecast:event-1",
        {
            "source_id": "forecast_live",
            "model_family": "edli_v1",
            "forecast_issue_time": NOW.isoformat(),
            "forecast_valid_time": NOW.isoformat(),
            "forecast_fetch_time": NOW.isoformat(),
            "forecast_available_at": NOW.isoformat(),
            "raw_payload_hash": "c" * 64,
            "degradation_level": "OK",
            "forecast_source_role": "entry_primary",
            "authority_tier": "FORECAST",
            "decision_time": NOW.isoformat(),
            "decision_time_status": "OK",
            "observation_time": NOW.isoformat(),
            "observation_available_at": NOW.isoformat(),
            "polymarket_end_anchor_source": "gamma_explicit",
            "first_member_observed_time": NOW.isoformat(),
            "run_complete_time": NOW.isoformat(),
            "zeus_submit_intent_time": NOW.isoformat(),
            "venue_ack_time": NOW.isoformat(),
        },
        mode=parent_modes.get(claims.FORECAST_AUTHORITY, "LIVE"),
    )
    quote = _cert(
        claims.QUOTE_FEASIBILITY,
        "quote:event-1",
        {
            "side": "BUY",
            "outcome": "YES",
            "execution_price_type": "ExecutionPrice",
            "native_execution_price": 0.4,
            "best_bid": 0.39,
            "best_ask": 0.41,
            "visible_depth": 100.0,
            "tick_size": 0.01,
            "min_order_size": 1.0,
            "neg_risk": False,
            "fill_claim": False,
        },
        mode=parent_modes.get(claims.QUOTE_FEASIBILITY, "LIVE"),
    )
    cost = _cert(
        claims.COST_MODEL,
        "cost:event-1",
        {
            "cost_basis_hash": "b" * 64,
            "cost_basis_id": "cost_basis:" + ("b" * 16),
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "cost_source": "native_orderbook_ask",
            "quote_source_kind": "executable_market_snapshot_native_book",
            "forbidden_cost_source": False,
            "execution_price_type": "ExecutionPrice",
        },
        mode=parent_modes.get(claims.COST_MODEL, "LIVE"),
    )
    executable = _cert(
        claims.EXECUTABLE_SNAPSHOT,
        "executable:exec-1",
        {
            "executable_snapshot_hash": "a" * 64,
            "condition_id": "condition-1",
            "token_id": "yes-1",
            "neg_risk": False,
        },
        mode=parent_modes.get(claims.EXECUTABLE_SNAPSHOT, "LIVE"),
    )
    final_intent = build_final_intent_certificate_from_actionable(
        actionable_cert=actionable,
        executable_snapshot_cert=executable,
        quote_feasibility_cert=quote,
        cost_model_cert=cost,
        forecast_authority_cert=forecast,
        decision_source_context=forecast.payload,
        passive_maker_context={
            "spread_usd": 0.02,
            "quote_age_ms": 0,
            "expected_fill_probability": "0.1",
            "queue_depth_ahead": None,
            "adverse_selection_score": None,
            "orderbook_hash_age_ms": 0,
        },
        decision_time=NOW,
    )
    if final_payload:
        final_intent = _cert(claims.FINAL_INTENT, "final-intent:intent-1", {**final_intent.payload, **final_payload}, parents=(actionable, executable, quote, cost, forecast))
    live_cap = _cert(claims.LIVE_CAP, "live-cap:cap-1", _live_cap_payload())
    expressibility = build_executor_expressibility_certificate(
        final_intent_cert=final_intent,
        executable_snapshot_cert=executable,
        live_cap_cert=live_cap,
        decision_time=NOW,
        executor_native_intent_hash=validate_final_intent_cert_for_existing_executor(final_intent),
    )
    return actionable, final_intent, expressibility, live_cap


def receipt_command():
    actionable, final_intent, expressibility, live_cap = builder_chain()
    pre_submit = _pre_submit_cert(final_intent, live_cap)
    return build_execution_command_certificate_from_final_intent(
        actionable_cert=actionable,
        final_intent_cert=final_intent,
        executor_expressibility_cert=expressibility,
        live_cap_cert=live_cap,
        pre_submit_revalidation_cert=pre_submit,
        decision_time=NOW,
    )


def _actionable_payload() -> dict:
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
        "min_entry_price": 0.05,
        "min_expected_profit_usd": 0.05,
        "min_submit_edge_density": 0.02,
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


def _command_payload(actionable) -> dict:
    bound = actionable.payload.get("replacement_no_bound_certificate")
    return {
        "event_id": "event-1",
        "event_type": actionable.payload.get("event_type", "FORECAST_SNAPSHOT_READY"),
        "actionable_certificate_hash": actionable.certificate_hash,
        "final_intent_id": "intent-1",
        "strategy_key": actionable.payload.get("strategy_key", "center_buy"),
        "execution_command_id": "cmd-1",
        "executor_name": "execute_final_intent",
        "order_type": "POST_ONLY_LIMIT",
        "side": "BUY",
        "direction": "buy_yes",
        "condition_id": "condition-1",
        "token_id": "yes-1",
        "limit_price": 0.40,
        "size": 10.0,
        "time_in_force": "GTC",
        "post_only": True,
        "maker": True,
        "neg_risk": False,
        "tick_size": 0.01,
        "min_order_size": 1.0,
        "fee_rate": 0.0,
        "idempotency_key": "edli:event-1:cmd-1",
        "aggregate_id": "event-1:intent-1",
        "aggregate_pre_submit_event_hash": "pre-submit-hash",
        "aggregate_execution_command_event_hash": "command-hash",
        "replacement_no_bound_certificate_hash": (
            bound.get("certificate_hash") if isinstance(bound, dict) else None
        ),
        "submitted": False,
    }


def _pre_submit_cert(final_intent, live_cap, command_payload: dict | None = None):
    command_payload = command_payload or {}
    payload = {
        "event_id": command_payload.get("event_id", final_intent.payload.get("event_id", "event-1")),
        "event_type": command_payload.get("event_type", final_intent.payload.get("event_type", "FORECAST_SNAPSHOT_READY")),
        "final_intent_id": command_payload.get("final_intent_id", final_intent.payload.get("final_intent_id", "intent-1")),
        "strategy_key": command_payload.get("strategy_key", final_intent.payload.get("strategy_key", "center_buy")),
        "condition_id": command_payload.get("condition_id", final_intent.payload.get("condition_id", "condition-1")),
        "token_id": command_payload.get("token_id", final_intent.payload.get("token_id", "yes-1")),
        "side": command_payload.get("side", final_intent.payload.get("side", "BUY")),
        "direction": command_payload.get("direction", final_intent.payload.get("direction", "buy_yes")),
        "order_type": command_payload.get("order_type", final_intent.payload.get("order_type", "POST_ONLY_LIMIT")),
        "time_in_force": command_payload.get("time_in_force", final_intent.payload.get("time_in_force", "GTC")),
        "post_only": command_payload.get("post_only", final_intent.payload.get("post_only", True)),
        "checked_at": NOW.isoformat(),
        "quote_seen_at": NOW.isoformat(),
        "quote_age_ms": 0,
        "max_quote_age_ms": 1000,
        "book_hash": "book-hash",
        "current_best_bid": 0.39,
        "current_best_ask": 0.41,
        "limit_price": command_payload.get("limit_price", 0.4),
        "q_live": command_payload.get("q_live", final_intent.payload.get("q_live", 0.7)),
        "q_lcb_5pct": command_payload.get(
            "q_lcb_5pct",
            final_intent.payload.get("q_lcb_5pct", 0.6),
        ),
        "expected_edge": command_payload.get(
            "expected_edge",
            final_intent.payload.get("trade_score", 0.2),
        ),
        "action_score": command_payload.get(
            "action_score",
            final_intent.payload.get("action_score", 0.2),
        ),
        "min_entry_price": command_payload.get(
            "min_entry_price",
            final_intent.payload.get("min_entry_price", 0.05),
        ),
        "min_expected_profit_usd": command_payload.get(
            "min_expected_profit_usd",
            0.0,
        ),
        "min_submit_edge_density": command_payload.get(
            "min_submit_edge_density",
            final_intent.payload.get("min_submit_edge_density", 0.02),
        ),
        "selection_authority_applied": command_payload.get(
            "selection_authority_applied",
            final_intent.payload.get("selection_authority_applied"),
        ),
        "qkernel_execution_economics": command_payload.get(
            "qkernel_execution_economics",
            final_intent.payload.get(
                "qkernel_execution_economics",
                {
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
            ),
        ),
        "replacement_no_bound_certificate": final_intent.payload.get(
            "replacement_no_bound_certificate"
        ),
        "would_cross_book": False,
        "tick_size": command_payload.get("tick_size", 0.01),
        "tick_aligned": command_payload.get("tick_aligned", True),
        "min_order_size": command_payload.get("min_order_size", 1.0),
        "size": command_payload.get("size", final_intent.payload.get("size", 10.0)),
        "size_ok": command_payload.get("size_ok", True),
        "neg_risk": command_payload.get("neg_risk", False),
        "heartbeat_status": "OK",
        "user_ws_status": "OK",
        "venue_connectivity_status": "OK",
        "balance_allowance_status": "OK",
        "book_authority_id": "execution_feasibility_evidence",
        "book_captured_at": NOW.isoformat(),
        "heartbeat_authority_id": "heartbeat_supervisor",
        "heartbeat_checked_at": NOW.isoformat(),
        "user_ws_authority_id": "ws_gap_guard",
        "user_ws_checked_at": NOW.isoformat(),
        "venue_connectivity_authority_id": "polymarket_public_orderbook",
        "venue_connectivity_checked_at": NOW.isoformat(),
        "balance_allowance_authority_id": "polymarket_wallet_readonly",
        "balance_allowance_checked_at": NOW.isoformat(),
        "aggregate_id": f"{command_payload.get('event_id', final_intent.payload.get('event_id', 'event-1'))}:{command_payload.get('final_intent_id', final_intent.payload.get('final_intent_id', 'intent-1'))}",
        "aggregate_event_hash": "pre-submit-hash",
        "aggregate_execution_command_event_hash": "command-hash",
        "final_intent_certificate_hash": final_intent.certificate_hash,
        "live_cap_usage_id": live_cap.payload["usage_id"],
    }
    for key in (
        "source_match_status",
        "local_date_status",
        "station_match_status",
        "dst_status",
        "metric_match_status",
        "rounding_status",
        "source_authorized_status",
        "live_authority_status",
    ):
        if key in command_payload:
            payload[key] = command_payload[key]
        elif key in final_intent.payload:
            payload[key] = final_intent.payload[key]
    return _cert(
        claims.PRE_SUBMIT_REVALIDATION,
        "pre-submit:event-1:intent-1",
        payload,
        parents=(final_intent, live_cap),
    )


def _final_intent_payload(actionable) -> dict:
    payload = actionable.payload
    result = {
        "event_id": payload["event_id"],
        "event_type": payload.get("event_type", "FORECAST_SNAPSHOT_READY"),
        "actionable_certificate_hash": actionable.certificate_hash,
        "final_intent_id": payload["final_intent_id"],
        "strategy_key": payload.get("strategy_key", "center_buy"),
        "family_id": payload["family_id"],
        "candidate_id": payload["candidate_id"],
        "condition_id": payload["condition_id"],
        "token_id": payload["token_id"],
        "direction": payload["direction"],
        "side": "BUY",
        "order_type": "POST_ONLY_LIMIT",
        "time_in_force": "GTC",
        "post_only": True,
        "maker_intent": True,
        "limit_price": 0.4,
        "q_live": payload.get("q_live"),
        "q_lcb_5pct": payload.get("q_lcb_5pct"),
        "trade_score": payload.get("trade_score"),
        "action_score": payload.get("action_score"),
        "min_entry_price": payload.get("min_entry_price"),
        "min_expected_profit_usd": payload.get("min_expected_profit_usd"),
        "min_submit_edge_density": payload.get("min_submit_edge_density"),
        "selection_authority_applied": payload.get("selection_authority_applied"),
        "qkernel_execution_economics": payload.get("qkernel_execution_economics"),
        "replacement_no_bound_certificate": payload.get(
            "replacement_no_bound_certificate"
        ),
        "size": 10.0,
        "notional_usd": 4.0,
        "executable_snapshot_id": payload["executable_snapshot_id"],
        "execution_price_type": "ExecutionPrice",
        "fee_deducted": True,
        "neg_risk": False,
        "tick_size": 0.01,
        "min_order_size": 1.0,
        "fee_rate": 0.0,
        "executable_snapshot_hash": "a" * 64,
        "cost_basis_hash": "b" * 64,
        "cost_basis_id": "cost_basis:" + ("b" * 16),
        "executor_order_type": "GTC",
        "decision_source_context": {
            "source_id": "edli_event_bound",
            "model_family": "edli_v1",
            "forecast_issue_time": NOW.isoformat(),
            "forecast_valid_time": NOW.isoformat(),
            "forecast_fetch_time": NOW.isoformat(),
            "forecast_available_at": NOW.isoformat(),
            "raw_payload_hash": "c" * 64,
            "degradation_level": "OK",
            "forecast_source_role": "entry_primary",
            "authority_tier": "FORECAST",
            "decision_time": NOW.isoformat(),
            "decision_time_status": "OK",
            "observation_time": NOW.isoformat(),
            "observation_available_at": NOW.isoformat(),
            "polymarket_end_anchor_source": "gamma_explicit",
            "first_member_observed_time": NOW.isoformat(),
            "run_complete_time": NOW.isoformat(),
            "zeus_submit_intent_time": NOW.isoformat(),
            "venue_ack_time": NOW.isoformat(),
        },
        "passive_maker_context": {
            "spread_usd": "0.01",
            "quote_age_ms": 0,
            "expected_fill_probability": "0.1",
            "orderbook_hash_age_ms": 0,
        },
        "live_cap_usage_id": payload["live_cap_usage_id"],
        "source": "existing_final_intent_builder",
        "submitted": False,
        "venue_order_id": None,
    }
    for key in (
        "source_match_status",
        "local_date_status",
        "station_match_status",
        "dst_status",
        "metric_match_status",
        "rounding_status",
        "source_authorized_status",
        "live_authority_status",
    ):
        if key in payload:
            result[key] = payload[key]
    return result


def _expressibility_payload(final_intent) -> dict:
    payload = final_intent.payload
    return {
        "event_id": payload["event_id"],
        "final_intent_id": payload["final_intent_id"],
        "strategy_key": payload.get("strategy_key"),
        "executor_name": "execute_final_intent",
        "executor_capability_version": "existing_executor_passive_limit_v1",
        "can_express": True,
        "passed": True,
        "reason_code": "OK",
        "executor_native_intent_hash": "d" * 64,
        "order_type": payload["order_type"],
        "side": payload["side"],
        "direction": payload["direction"],
        "token_id": payload["token_id"],
        "condition_id": payload["condition_id"],
        "limit_price": payload["limit_price"],
        "size": payload["size"],
        "time_in_force": payload["time_in_force"],
        "post_only": payload["post_only"],
        "maker_intent": payload["maker_intent"],
        "tick_size": payload["tick_size"],
        "min_order_size": payload["min_order_size"],
        "neg_risk": payload["neg_risk"],
        "fee_rate": payload["fee_rate"],
    }


def _live_cap_payload() -> dict:
    return {
        "usage_id": "cap-1",
        "event_id": "event-1",
        "reservation_status": "RESERVED",
        "max_notional_usd": 5.0,
        "reserved_notional_usd": 5.0,
        "order_count": 1,
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
