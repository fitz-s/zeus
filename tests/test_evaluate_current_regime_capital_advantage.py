# Created: 2026-08-12
# Authority: current-regime capital proof must fail closed before entry reopens.

from __future__ import annotations

import json
import sqlite3

import pytest

from scripts import evaluate_current_regime_capital_advantage as evaluator
from src.contracts.global_auction_receipt import (
    GlobalAuctionReceiptRef,
    global_auction_execution_binding_hash,
)


def _binding_summary(schema_version: int) -> dict[str, object]:
    summary = {
        "schema_version": schema_version,
        "selection_epoch_identity": "epoch",
        "selection_cut_at_utc": "2026-08-12T00:00:00+00:00",
        "decision_at_utc": "2026-08-12T00:00:01+00:00",
        "full_scope_identity": "scope",
        "book_epoch_identity": "book",
        "wealth_witness_identity": "wealth",
        "wealth_economic_identity": "economics",
        "winner_event_id": "",
        "winner_candidate_id": "",
        "winner_actuation_identity": "",
        "payload_identity": "1" * 64,
        "decision_payload_identity": "2" * 64,
        "audit_context_sha256": "3" * 64,
        "book_native_side_states_sha256": "4" * 64,
        "candidate_evaluations_sha256": "5" * 64,
        "buy_minimum_marketable_repairs_sha256": "6" * 64,
        "holding_auction_coverage_sha256": "7" * 64,
    }
    if schema_version == 22:
        summary.update(
            {
                "global_selection_revision": (
                    evaluator.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
                ),
                "portfolio_wealth": {
                    "ledger_snapshot_id": "ledger",
                    "position_set_hash": "positions",
                    "wealth_floor_usd": "18",
                    "wealth_ceiling_usd": "22",
                    "spendable_cash_usd": "10",
                    "reservations_usd": "2",
                    "collateral_authority": "CHAIN",
                },
            }
        )
    return summary


def test_placeholder_database_is_rejected(tmp_path):
    path = tmp_path / "placeholder.db"
    path.write_bytes(b"")

    with pytest.raises(ValueError, match="placeholder"):
        evaluator._read_only(
            path,
            frozenset({"decision_log"}),
            connection_factory=lambda: sqlite3.connect(":memory:"),
        )


def test_current_receipt_without_settled_capital_proof_fails():
    verdict, failures = evaluator._build_verdict(
        receipt={"ready": True},
        shadows={
            "day0": {
                "global_selection_revision_bound": False,
                "independent_family_day_count": 0,
                "delta_log_wealth_lcb95": None,
            }
        },
        live_curves={"day0": {"selection_revision_bound": False}},
    )

    assert verdict == "FAIL"
    assert "INSUFFICIENT_CURRENT_REGIME_SETTLED_FAMILY_DAYS" in failures
    assert "AFTER_COST_DELTA_LOG_WEALTH_LCB_NOT_POSITIVE" in failures
    assert "EXACT_REVISION_LIVE_NET_CAPITAL_GAIN_NOT_POSITIVE" in failures


def test_latest_delta_receipt_is_current_selection_evidence():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE decision_log ("
        "id INTEGER PRIMARY KEY, mode TEXT, completed_at TEXT, artifact_json TEXT)"
    )
    summary = {
        "global_selection_revision": (
            evaluator.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ),
        "portfolio_wealth": {
            "ledger_snapshot_id": "ledger",
            "position_set_hash": "positions",
            "wealth_floor_usd": "18",
            "wealth_ceiling_usd": "22",
            "spendable_cash_usd": "10",
            "reservations_usd": "2",
            "collateral_authority": "CHAIN",
        },
        "scope_family_coverage_complete": True,
        "candidate_coverage_complete": True,
        "held_position_coverage_complete": True,
        "book_capture_freshness_complete": True,
    }
    conn.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction_delta",
            "2026-08-13T00:00:00+00:00",
            json.dumps({"summary": summary}),
        ),
    )

    evidence = evaluator._receipt_revision_coverage(conn)

    assert evidence["decision_log_id"] == 1
    assert evidence["ready"] is True


def test_only_complete_positive_exact_revision_evidence_passes():
    verdict, failures = evaluator._build_verdict(
        receipt={"ready": True},
        shadows={
            "combined": {
                "global_selection_revision_bound": True,
                "independent_family_day_count": 30,
                "delta_log_wealth_lcb95": 0.001,
            }
        },
        live_curves={
            "combined": {
                "selection_revision_bound": True,
                "net_realized_pnl_usd": 0.01,
            }
        },
    )

    assert verdict == "PASS"
    assert failures == []


def test_read_only_schema_gate_never_creates_missing_database(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(ValueError):
        evaluator._read_only(
            missing,
            frozenset(),
            connection_factory=lambda: sqlite3.connect(":memory:"),
        )
    assert not missing.exists()


def test_schema_21_execution_reference_remains_compatible():
    summary = _binding_summary(21)
    binding = global_auction_execution_binding_hash(summary)
    ref = GlobalAuctionReceiptRef(
        decision_log_id=1,
        decision_log_mode="global_single_order_auction",
        receipt_hash="a" * 64,
        execution_binding_hash=binding,
        artifact_summary_hash="b" * 64,
        schema_version=21,
        winner_event_id="event",
        winner_candidate_id="candidate",
        winner_actuation_identity="actuation",
        selection_epoch_identity="epoch",
    )
    assert ref.schema_version == 21


def test_schema_22_binding_covers_selection_revision_and_portfolio_wealth():
    summary = _binding_summary(22)
    original = global_auction_execution_binding_hash(summary)

    changed_revision = dict(summary)
    changed_revision["global_selection_revision"] = "different-revision"
    assert global_auction_execution_binding_hash(changed_revision) != original

    changed_wealth = dict(summary)
    changed_wealth["portfolio_wealth"] = {
        **summary["portfolio_wealth"],
        "wealth_floor_usd": "17",
    }
    assert global_auction_execution_binding_hash(changed_wealth) != original

    missing_wealth = dict(summary)
    missing_wealth.pop("portfolio_wealth")
    with pytest.raises(ValueError, match="PORTFOLIO_WEALTH_MISSING"):
        global_auction_execution_binding_hash(missing_wealth)
