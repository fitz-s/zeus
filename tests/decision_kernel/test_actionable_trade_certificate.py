# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Authority basis: docs/operations/edli_v1/EDLI_REDEMPTION_FINAL_PACKAGE_SPEC.md §14 full-live increment.
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.decision_kernel import claims
from src.decision_kernel.certificate import ParentEdge, build_certificate
from src.decision_kernel.errors import CertificateVerificationError
from src.decision_kernel.ledger import DecisionCertificateLedger
from src.decision_kernel.verifier import verify_actionable_trade


NOW = datetime(2026, 5, 25, 12, tzinfo=timezone.utc)


def test_actionable_requires_live_mode():
    parents, action = actionable_graph(mode="NO_SUBMIT")

    with pytest.raises(CertificateVerificationError, match="LIVE mode"):
        verify_actionable_trade(action, parents)


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


def test_actionable_rejects_execution_command_id_present():
    parents, action = actionable_graph(action_payload={"execution_command_id": "cmd-1"})

    with pytest.raises(CertificateVerificationError, match="execution_command_id"):
        verify_actionable_trade(action, parents)


def test_actionable_rejects_wrong_cost_source_for_buy_no():
    parents, action = actionable_graph(
        action_payload={"direction": "buy_no", "token_id": "no-1"},
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
        "executable_snapshot_id": "exec-1",
        "q_live": 0.7,
        "q_lcb_5pct": 0.6,
        "c_fee_adjusted": 0.4,
        "c_cost_95pct": 0.45,
        "p_fill_lcb": 0.1,
        "trade_score": 0.2,
        "action_score": 0.2,
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
