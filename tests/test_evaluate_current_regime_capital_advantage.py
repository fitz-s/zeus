# Created: 2026-08-12
# Last reused/audited: 2026-09-01
# Lifecycle: created=2026-08-12; last_reviewed=2026-09-01; last_reused=2026-09-01
# Authority: current-regime capital proof must fail closed before entry reopens.
# Purpose: Exact-revision capital proof, per-order disposition, and total-portfolio truth antibodies.
# Reuse: Run whenever the capital evaluator, order facts, or portfolio valuation contract changes.

from __future__ import annotations

import base64
import json
import sqlite3
import zlib
from datetime import datetime, timezone

import pytest

from scripts import evaluate_current_regime_capital_advantage as evaluator
from src.contracts.global_auction_receipt import (
    GlobalAuctionReceiptRef,
    global_auction_artifact_summary_hash,
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


def _proof_summary(*, city: str, target_date: str, condition_id: str) -> dict[str, object]:
    summary = _binding_summary(22)
    summary.update(
        {
            "scope_family_coverage_complete": True,
            "candidate_coverage_complete": True,
            "held_position_coverage_complete": True,
            "book_capture_freshness_complete": True,
            "probability_manifest": [["family", "q-witness"]],
            "full_scope_identity": "scope",
        }
    )
    proof = {
        "role": evaluator.PROOF_ROLE,
        "venue_actuation_available": False,
        "venue_side_effect_free": True,
        "venue_submit_count_before": 7,
        "venue_submit_count_after": 7,
        "global_selection_revision": (
            evaluator.CURRENT_GLOBAL_CAPITAL_SELECTION_REVISION
        ),
        "selection_epoch_identity": summary["selection_epoch_identity"],
        "selection_cut_at_utc": summary["selection_cut_at_utc"],
        "decision_at_utc": summary["decision_at_utc"],
        "probability_manifest": summary["probability_manifest"],
        "full_scope_identity": summary["full_scope_identity"],
        "book_epoch_identity": summary["book_epoch_identity"],
        "wealth_witness_identity": summary["wealth_witness_identity"],
        "wealth_economic_identity": summary["wealth_economic_identity"],
        "candidate_input_count": 1,
        "candidate_evaluation_count": 1,
        "winner": {
            "candidate_id": "proof-buy",
            "action": "BUY",
            "family_key": "family",
            "city": city,
            "target_date": target_date,
            "metric": "high",
            "condition_id": condition_id,
            "side": "YES",
            "execution_mode": "TAKER_LIMIT",
            "cost_usd": "4",
            "probability_semantics_revision": (
                evaluator.CURRENT_EVIDENCE_SEMANTICS_REVISION
            ),
            "evaluation": {
                "candidate_id": "proof-buy",
                "action": "BUY",
                "status": "SELECTED",
                "execution_mode": "TAKER_LIMIT",
                "capital_action_mode": "SETTLEMENT_LOCKED_BUY",
                "fill_probability": 1.0,
                "fill_probability_source": "immediate_taker",
                "expected_growth": {
                    "probability_basis": "POSTERIOR_PREDICTIVE_MEAN"
                },
                "expected_terminal_wealth": {
                    "probability_basis": "POSTERIOR_PREDICTIVE_MEAN",
                    "loss_payoff_usd": "-4",
                    "win_payoff_usd": "6",
                    "wealth_after_loss_usd": "96",
                    "wealth_after_win_usd": "106",
                },
            },
        },
    }
    summary["proof_counterfactual"] = proof
    summary["proof_counterfactual_sha256"] = evaluator.hashlib.sha256(
        evaluator._canonical_json_bytes(proof)
    ).hexdigest()
    audit_context = {
        "probability_manifest": summary["probability_manifest"],
        "buy_disabled_reason_by_family": {},
        "excluded_by_family": {},
        "excluded_by_candidate": {},
    }
    raw_audit_context = evaluator._canonical_json_bytes(audit_context)
    summary["audit_context_encoding"] = "zlib+base64+canonical-json-object-v1"
    summary["audit_context_sha256"] = evaluator.hashlib.sha256(
        raw_audit_context
    ).hexdigest()
    summary["audit_context_zlib_b64"] = base64.b64encode(
        zlib.compress(raw_audit_context)
    ).decode("ascii")
    summary["execution_binding_hash"] = global_auction_execution_binding_hash(
        summary
    )
    summary["receipt_hash"] = "a" * 64
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )
    return summary


def _settlement_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE market_events (condition_id TEXT,city TEXT,"
        "target_date TEXT,temperature_metric TEXT,range_low REAL,range_high REAL)"
    )
    conn.execute(
        "CREATE TABLE settlement_outcomes (settlement_id INTEGER PRIMARY KEY,"
        "city TEXT,target_date TEXT,temperature_metric TEXT,settlement_value REAL,"
        "settlement_unit TEXT,settled_at TEXT,recorded_at TEXT,authority TEXT)"
    )
    return conn


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
                "independent_target_date_count": 0,
                "delta_log_wealth_lcb95": None,
            }
        },
        live_curves={"day0": {"selection_revision_bound": False}},
    )

    assert verdict == "FAIL"
    assert "INSUFFICIENT_CURRENT_REGIME_SETTLED_TARGET_DATES" in failures
    assert "AFTER_COST_DELTA_LOG_WEALTH_LCB_NOT_POSITIVE" in failures
    assert "INSUFFICIENT_EXACT_REVISION_LIVE_REALIZED_POSITIONS" in failures
    assert "EXACT_REVISION_LIVE_CAPITAL_WEIGHTED_RETURN_NOT_POSITIVE" in failures


def test_counterfactual_admission_does_not_require_impossible_prior_live_fills():
    verdict, failures = evaluator._build_counterfactual_admission_verdict(
        receipt={"ready": True},
        shadows={
            "combined": {
                "global_selection_revision_bound": True,
                "independent_target_date_count": 30,
                "delta_log_wealth_lcb95": 0.001,
            }
        },
    )

    assert verdict == "PASS"
    assert failures == []


def test_latest_delta_receipt_is_current_selection_evidence():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE decision_log ("
        "id INTEGER PRIMARY KEY, mode TEXT, completed_at TEXT, artifact_json TEXT)"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
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


def test_compacted_audit_context_delta_rehydrates_probability_manifest():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,"
        "completed_at TEXT,artifact_json TEXT)"
    )
    base = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    current = json.loads(json.dumps(base))
    current_manifest = [["family", "q-witness-new"]]
    current["proof_counterfactual"]["probability_manifest"] = current_manifest
    current["proof_counterfactual_sha256"] = evaluator.hashlib.sha256(
        evaluator._canonical_json_bytes(current["proof_counterfactual"])
    ).hexdigest()
    current_context = {
        "probability_manifest": current_manifest,
        "buy_disabled_reason_by_family": {},
        "excluded_by_family": {},
        "excluded_by_candidate": {},
    }
    current_context_raw = evaluator._canonical_json_bytes(current_context)
    delta = {
        "removed_keys": [],
        "replacements": {"probability_manifest": current_manifest},
    }
    delta_raw = evaluator._canonical_json_bytes(delta)
    current["audit_context_sha256"] = evaluator.hashlib.sha256(
        current_context_raw
    ).hexdigest()
    current.pop("audit_context_zlib_b64")
    current.pop("probability_manifest")
    current.update(
        {
            "audit_context_compacted": True,
            "audit_context_delta_encoding": (
                "zlib+base64+canonical-json-object-delta-v1"
            ),
            "audit_context_delta_sha256": evaluator.hashlib.sha256(
                delta_raw
            ).hexdigest(),
            "audit_context_delta_zlib_b64": base64.b64encode(
                zlib.compress(delta_raw)
            ).decode("ascii"),
            "audit_context_base_decision_log_id": 1,
            "audit_context_base_mode": "global_single_order_auction",
            "audit_context_base_receipt_hash": base["receipt_hash"],
            "audit_context_base_sha256": base["audit_context_sha256"],
            "audit_context_delta_chain_depth": 1,
        }
    )
    current["execution_binding_hash"] = global_auction_execution_binding_hash(
        current
    )
    current["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        current
    )
    conn.executemany(
        "INSERT INTO decision_log VALUES (?,?,?,?)",
        [
            (
                1,
                "global_single_order_auction",
                "2026-08-13T00:00:00+00:00",
                json.dumps({"summary": base}),
            ),
            (
                2,
                "global_single_order_auction_delta",
                "2026-08-13T00:00:01+00:00",
                json.dumps({"summary": current}),
            ),
        ],
    )

    proof = evaluator._summary_proof(conn, 2, current)

    assert proof["probability_manifest"] == current_manifest


def test_latest_proof_receipt_scan_skips_newer_rebound_without_proof():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,"
        "completed_at TEXT,artifact_json TEXT)"
    )
    proof_summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    rebound_summary = dict(proof_summary)
    rebound_summary.pop("proof_counterfactual")
    rebound_summary.pop("proof_counterfactual_sha256")
    rebound_summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        rebound_summary
    )
    conn.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction",
            "2026-08-12T00:00:02+00:00",
            json.dumps({"summary": proof_summary}),
        ),
    )
    conn.execute(
        "INSERT INTO decision_log VALUES (2,?,?,?)",
        (
            "global_single_order_auction_delta",
            "2026-08-12T00:00:03+00:00",
            json.dumps({"summary": rebound_summary}),
        ),
    )

    evidence = evaluator._latest_proof_receipt_coverage(conn)

    assert evidence["ready"] is True
    assert evidence["decision_log_id"] == 1


def test_proof_sample_uses_verified_settlement_and_after_cost_terminal_wealth():
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-1", "Chicago", "2026-08-13", "high", 80, 81),
    )
    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (1,?,?,?,?,?,?,?,?)",
        (
            "Chicago",
            "2026-08-13",
            "high",
            81,
            "F",
            "2026-08-13T20:00:00+00:00",
            "2026-08-13T20:01:00+00:00",
            "VERIFIED",
        ),
    )
    sample = evaluator._realized_proof_sample(
        sqlite3.connect(":memory:"),
        forecasts,
        decision_log_id=17,
        summary=_proof_summary(
            city="Chicago",
            target_date="2026-08-13",
            condition_id="condition-1",
        ),
    )

    assert sample["token_won"] is True
    assert sample["execution_mode"] == "TAKER_LIMIT"
    assert sample["realized_after_cost_payoff_usd"] == "6"
    assert sample["realized_delta_log_wealth"] == pytest.approx(
        evaluator.math.log(106 / 100)
    )


def test_stale_ensemble_semantics_cannot_be_current_capital_evidence():
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-1", "Chicago", "2026-08-13", "high", 80, 81),
    )
    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (1,?,?,?,?,?,?,?,?)",
        (
            "Chicago",
            "2026-08-13",
            "high",
            81,
            "F",
            "2026-08-13T20:00:00+00:00",
            "2026-08-13T20:01:00+00:00",
            "VERIFIED",
        ),
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    winner = summary["proof_counterfactual"]["winner"]
    winner["probability_semantics_revision"] = (
        evaluator.STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION
    )
    summary["proof_counterfactual_sha256"] = evaluator.hashlib.sha256(
        evaluator._canonical_json_bytes(summary["proof_counterfactual"])
    ).hexdigest()
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )

    with pytest.raises(ValueError, match="identity/semantics invalid"):
        evaluator._realized_proof_sample(
            sqlite3.connect(":memory:"),
            forecasts,
            decision_log_id=18,
            summary=summary,
        )


def test_maker_counterfactual_without_fill_path_cannot_prove_capital_gain():
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    winner = summary["proof_counterfactual"]["winner"]
    winner["execution_mode"] = "MAKER_REST"
    winner["evaluation"]["execution_mode"] = "MAKER_REST"
    winner["evaluation"]["capital_action_mode"] = "CONTINGENT_MAKER_REST_BUY"
    summary["proof_counterfactual_sha256"] = evaluator.hashlib.sha256(
        evaluator._canonical_json_bytes(summary["proof_counterfactual"])
    ).hexdigest()
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )

    with pytest.raises(
        ValueError,
        match="lacks immediate full-fill execution proof",
    ):
        evaluator._realized_proof_sample(
            sqlite3.connect(":memory:"),
            _settlement_db(),
            decision_log_id=17,
            summary=summary,
        )


def test_condition_resolution_uses_typed_integer_bin_geometry():
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("finite-range", "Chicago", "2026-08-13", "high", 80, 81),
    )

    assert evaluator._condition_resolved_yes(
        forecasts,
        condition_id="finite-range",
        city="Chicago",
        target_date="2026-08-13",
        metric="high",
        settlement_value=evaluator.Decimal("81"),
        settlement_unit="F",
    )

    with pytest.raises(ValueError, match="geometry invalid"):
        evaluator._condition_resolved_yes(
            forecasts,
            condition_id="finite-range",
            city="Chicago",
            target_date="2026-08-13",
            metric="high",
            settlement_value=evaluator.Decimal("81"),
            settlement_unit="C",
        )


def test_tampered_proof_payoff_is_rejected_by_hash():
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    summary.update(
        winner_event_id="event-1",
        winner_candidate_id="candidate-1",
        winner_actuation_identity="actuation-1",
    )
    summary["execution_binding_hash"] = global_auction_execution_binding_hash(
        summary
    )
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )
    summary["proof_counterfactual"]["winner"]["evaluation"][
        "expected_terminal_wealth"
    ]["win_payoff_usd"] = "60"
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )

    with pytest.raises(ValueError, match="proof counterfactual hash mismatch"):
        evaluator._summary_proof(sqlite3.connect(":memory:"), 1, summary)


def test_counterfactual_evidence_counts_only_first_receipt_per_target_date():
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-1", "Chicago", "2026-08-13", "high", 80, 81),
    )
    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (1,?,?,?,?,?,?,?,?)",
        (
            "Chicago",
            "2026-08-13",
            "high",
            81,
            "F",
            "2026-08-13T20:00:00+00:00",
            "2026-08-13T20:01:00+00:00",
            "VERIFIED",
        ),
    )
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-2", "New York", "2026-08-13", "high", 82, 83),
    )
    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (2,?,?,?,?,?,?,?,?)",
        (
            "New York",
            "2026-08-13",
            "high",
            83,
            "F",
            "2026-08-13T20:00:00+00:00",
            "2026-08-13T20:01:00+00:00",
            "VERIFIED",
        ),
    )
    trades = sqlite3.connect(":memory:")
    trades.row_factory = sqlite3.Row
    trades.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,"
        "completed_at TEXT,artifact_json TEXT)"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    other_summary = _proof_summary(
        city="New York",
        target_date="2026-08-13",
        condition_id="condition-2",
    )
    for row_id, receipt in ((1, summary), (2, other_summary)):
        trades.execute(
            "INSERT INTO decision_log VALUES (?,?,?,?)",
            (
                row_id,
                "global_single_order_auction",
                "2026-08-12T00:00:02+00:00",
                json.dumps({"summary": receipt}),
            ),
        )

    evidence = evaluator._settled_global_counterfactual_evidence(
        trades,
        forecasts,
        as_of=evaluator.datetime.fromisoformat("2026-08-14T00:00:00+00:00"),
    )

    assert evidence["independent_target_date_count"] == 1
    assert evidence["samples"][0]["decision_log_id"] == 1
    assert evidence["rejection_counts"]["duplicate_target_date"] == 1
    assert evidence["delta_log_wealth_lcb95"] is None
    assert evidence["proof_registry_target_date_count"] == 1
    assert evidence["proof_registry"][0]["decision_log_id"] == 1


def test_pending_proof_registry_survives_receipt_scan_window_until_settlement():
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-1", "Chicago", "2026-08-13", "high", 80, 81),
    )
    trades = sqlite3.connect(":memory:")
    trades.row_factory = sqlite3.Row
    trades.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,"
        "completed_at TEXT,artifact_json TEXT)"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    trades.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction",
            "2026-08-12T00:00:02+00:00",
            json.dumps({"summary": summary}),
        ),
    )

    pending = evaluator._settled_global_counterfactual_evidence(
        trades,
        forecasts,
        as_of=evaluator.datetime.fromisoformat("2026-08-13T00:00:00+00:00"),
    )

    assert pending["independent_target_date_count"] == 0
    assert pending["proof_registry_target_date_count"] == 1
    assert pending["proof_registry"][0]["decision_log_id"] == 1

    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (1,?,?,?,?,?,?,?,?)",
        (
            "Chicago",
            "2026-08-13",
            "high",
            81,
            "F",
            "2026-08-13T20:00:00+00:00",
            "2026-08-13T20:01:00+00:00",
            "VERIFIED",
        ),
    )
    trades.execute(
        "INSERT INTO decision_log VALUES (20000,?,?,?)",
        (
            "exit_monitor",
            "2026-08-13T23:00:00+00:00",
            json.dumps({}),
        ),
    )
    settled = evaluator._settled_global_counterfactual_evidence(
        trades,
        forecasts,
        as_of=evaluator.datetime.fromisoformat("2026-08-14T00:00:00+00:00"),
        prior_proof_registry=pending["proof_registry"],
    )

    assert settled["independent_target_date_count"] == 1
    assert settled["samples"][0]["decision_log_id"] == 1
    assert settled["proof_registry_target_date_count"] == 1


def test_invalid_retained_proof_ref_cannot_abort_current_canonical_scan():
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-1", "Chicago", "2026-08-13", "high", 80, 81),
    )
    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (1,?,?,?,?,?,?,?,?)",
        (
            "Chicago",
            "2026-08-13",
            "high",
            81,
            "F",
            "2026-08-13T20:00:00+00:00",
            "2026-08-13T20:01:00+00:00",
            "VERIFIED",
        ),
    )
    trades = sqlite3.connect(":memory:")
    trades.row_factory = sqlite3.Row
    trades.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,"
        "completed_at TEXT,artifact_json TEXT)"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    trades.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction",
            "2026-08-12T00:00:02+00:00",
            json.dumps({"summary": summary}),
        ),
    )

    evidence = evaluator._settled_global_counterfactual_evidence(
        trades,
        forecasts,
        as_of=evaluator.datetime.fromisoformat("2026-08-14T00:00:00+00:00"),
        prior_proof_registry=({"decision_log_id": "invalid"},),
    )

    assert evidence["independent_target_date_count"] == 1
    assert evidence["proof_registry"][0]["decision_log_id"] == 1
    assert evidence["rejection_counts"]["invalid literal for int() with base 10: 'invalid'"] == 1


def test_retained_proof_registry_cannot_outlive_current_evidence_window():
    forecasts = _settlement_db()
    trades = sqlite3.connect(":memory:")
    trades.row_factory = sqlite3.Row
    trades.execute(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,"
        "completed_at TEXT,artifact_json TEXT)"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    trades.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction",
            "2026-08-12T00:00:02+00:00",
            json.dumps({"summary": summary}),
        ),
    )
    retained = {
        "decision_log_id": 1,
        "proof_counterfactual_sha256": summary["proof_counterfactual_sha256"],
        "independence_key": "2026-08-13",
        "decision_at_utc": "2026-08-12T00:00:01+00:00",
    }

    evidence = evaluator._settled_global_counterfactual_evidence(
        trades,
        forecasts,
        as_of=evaluator.datetime.fromisoformat("2026-09-17T00:00:01+00:00"),
        prior_proof_registry=(retained,),
    )

    assert evidence["proof_registry_target_date_count"] == 0
    assert evidence["rejection_counts"][
        "proof decision outside current evidence window"
    ] == 1


def test_live_curve_requires_exact_schema_22_edli_receipt_binding():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    events_conn = sqlite3.connect(":memory:")
    events_conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE venue_commands (command_id TEXT,position_id TEXT,intent_kind TEXT,decision_id TEXT);"
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,artifact_json TEXT);"
    )
    events_conn.execute(
        "CREATE TABLE edli_live_order_events "
        "(aggregate_id TEXT,event_type TEXT,payload_json TEXT)"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    summary.update(
        winner_event_id="event-1",
        winner_candidate_id="candidate-1",
        winner_actuation_identity="actuation-1",
    )
    summary["execution_binding_hash"] = global_auction_execution_binding_hash(
        summary
    )
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )
    conn.execute(
        "INSERT INTO decision_log VALUES (1,?,?)",
        ("global_single_order_auction", json.dumps({"summary": summary})),
    )
    receipt = GlobalAuctionReceiptRef(
        decision_log_id=1,
        decision_log_mode="global_single_order_auction",
        receipt_hash=summary["receipt_hash"],
        execution_binding_hash=summary["execution_binding_hash"],
        artifact_summary_hash=summary["artifact_summary_hash"],
        schema_version=22,
        winner_event_id=summary["winner_event_id"],
        winner_candidate_id=summary["winner_candidate_id"],
        winner_actuation_identity=summary["winner_actuation_identity"],
        selection_epoch_identity=summary["selection_epoch_identity"],
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES "
        "('venue-cmd-1','position-1','ENTRY','cmd-1')"
    )
    events_conn.execute(
        "INSERT INTO edli_live_order_events VALUES ('aggregate-1','ExecutionCommandCreated',?)",
        (json.dumps({"execution_command_id": "cmd-1"}),),
    )
    events_conn.execute(
        "INSERT INTO edli_live_order_events VALUES ('aggregate-1','PreSubmitRevalidated',?)",
        (json.dumps({"global_auction_receipt": receipt.as_payload()}),),
    )
    bound = evaluator._bind_live_curve_to_global_revision(
        conn,
        {
            "curve": [
                {
                    "position_id": "position-1",
                    "capital_committed_usd": 4.0,
                    "net_realized_pnl_usd": 1.0,
                }
            ]
        },
        events_conn=events_conn,
    )

    assert bound["selection_revision_bound"] is True
    assert bound["realized_position_count"] == 1
    assert bound["net_realized_pnl_usd"] == 1.0
    assert bound["curve"][0]["global_auction_decision_log_id"] == 1


def test_live_curve_binds_every_increment_across_selection_epochs():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    events_conn = sqlite3.connect(":memory:")
    events_conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE venue_commands (command_id TEXT,position_id TEXT,"
        "intent_kind TEXT,decision_id TEXT);"
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,artifact_json TEXT);"
    )
    events_conn.execute(
        "CREATE TABLE edli_live_order_events "
        "(aggregate_id TEXT,event_type TEXT,payload_json TEXT)"
    )
    epoch_ids = []
    for index in (1, 2):
        summary = _proof_summary(
            city="Chicago",
            target_date="2026-08-13",
            condition_id=f"condition-{index}",
        )
        summary.update(
            winner_event_id=f"event-{index}",
            winner_candidate_id=f"candidate-{index}",
            winner_actuation_identity=f"actuation-{index}",
            selection_epoch_identity=f"epoch-{index}",
        )
        summary["execution_binding_hash"] = global_auction_execution_binding_hash(
            summary
        )
        summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
            summary
        )
        conn.execute(
            "INSERT INTO decision_log VALUES (?,?,?)",
            (
                index,
                "global_single_order_auction",
                json.dumps({"summary": summary}),
            ),
        )
        receipt = GlobalAuctionReceiptRef(
            decision_log_id=index,
            decision_log_mode="global_single_order_auction",
            receipt_hash=summary["receipt_hash"],
            execution_binding_hash=summary["execution_binding_hash"],
            artifact_summary_hash=summary["artifact_summary_hash"],
            schema_version=22,
            winner_event_id=summary["winner_event_id"],
            winner_candidate_id=summary["winner_candidate_id"],
            winner_actuation_identity=summary["winner_actuation_identity"],
            selection_epoch_identity=summary["selection_epoch_identity"],
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?)",
            (f"venue-cmd-{index}", "position-1", "ENTRY", f"cmd-{index}"),
        )
        events_conn.execute(
            "INSERT INTO edli_live_order_events VALUES (?,?,?)",
            (
                f"aggregate-{index}",
                "ExecutionCommandCreated",
                json.dumps({"execution_command_id": f"cmd-{index}"}),
            ),
        )
        events_conn.execute(
            "INSERT INTO edli_live_order_events VALUES (?,?,?)",
            (
                f"aggregate-{index}",
                "PreSubmitRevalidated",
                json.dumps({"global_auction_receipt": receipt.as_payload()}),
            ),
        )
        epoch_ids.append(summary["selection_epoch_identity"])

    bound = evaluator._bind_live_curve_to_global_revision(
        conn,
        {
            "curve": [
                {
                    "position_id": "position-1",
                    "capital_committed_usd": 4.0,
                    "net_realized_pnl_usd": 1.0,
                }
            ]
        },
        events_conn=events_conn,
    )

    assert bound["realized_position_count"] == 1
    row = bound["curve"][0]
    assert row["global_auction_receipt_count"] == 2
    assert row["global_selection_epoch_identity"] is None
    assert row["global_selection_epoch_identities"] == epoch_ids
    assert {
        binding["venue_command_id"]
        for binding in row["global_auction_receipts"]
    } == {"venue-cmd-1", "venue-cmd-2"}


def test_exact_global_exit_is_ungraded_until_settlement_then_compared_with_hold():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,timestamp TEXT,"
        "artifact_json TEXT);"
        "CREATE TABLE venue_commands (command_id TEXT,position_id TEXT,"
        "intent_kind TEXT,created_at TEXT,state TEXT,token_id TEXT,envelope_id TEXT);"
        "CREATE TABLE position_events (event_id TEXT,position_id TEXT,"
        "sequence_no INTEGER,event_type TEXT,occurred_at TEXT,command_id TEXT,"
        "payload_json TEXT);"
        "CREATE TABLE position_current (position_id TEXT,city TEXT,target_date TEXT,"
        "temperature_metric TEXT,condition_id TEXT,direction TEXT,entry_price REAL,"
        "shares REAL,cost_basis_usd REAL);"
        "CREATE TABLE venue_submission_envelopes (envelope_id TEXT,post_only INTEGER,"
        "fee_details_json TEXT,outcome_label TEXT);"
        "CREATE TABLE execution_fact (command_id TEXT,order_role TEXT,fill_price REAL,"
        "shares REAL,filled_at TEXT,terminal_exec_status TEXT);"
        "CREATE TABLE execution_feasibility_evidence (token_id TEXT,quote_seen_at TEXT,"
        "depth_before_json TEXT);"
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.row_factory = sqlite3.Row
    forecasts.executescript(
        "CREATE TABLE settlement_outcomes (settlement_id TEXT,city TEXT,target_date TEXT,"
        "temperature_metric TEXT,settlement_value REAL,settlement_unit TEXT,"
        "settled_at TEXT,recorded_at TEXT,authority TEXT);"
        "CREATE TABLE market_events (condition_id TEXT,city TEXT,target_date TEXT,"
        "temperature_metric TEXT,range_low REAL,range_high REAL);"
        "INSERT INTO market_events VALUES "
        "('condition-1','Chicago','2026-08-13','high',80,81);"
    )
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    summary.update(
        winner_event_id="event-1",
        winner_candidate_id="candidate-1",
        winner_actuation_identity="actuation-1",
    )
    summary["execution_binding_hash"] = global_auction_execution_binding_hash(
        summary
    )
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )
    conn.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction",
            "2026-08-13T00:00:00+00:00",
            json.dumps({"summary": summary}),
        ),
    )
    receipt = GlobalAuctionReceiptRef(
        decision_log_id=1,
        decision_log_mode="global_single_order_auction",
        receipt_hash=summary["receipt_hash"],
        execution_binding_hash=summary["execution_binding_hash"],
        artifact_summary_hash=summary["artifact_summary_hash"],
        schema_version=22,
        winner_event_id=summary["winner_event_id"],
        winner_candidate_id=summary["winner_candidate_id"],
        winner_actuation_identity=summary["winner_actuation_identity"],
        selection_epoch_identity=summary["selection_epoch_identity"],
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?,?,?)",
        (
            "intent-1",
            "position-1",
            1,
            "EXIT_INTENT",
            "2026-08-13T00:00:01+00:00",
            None,
            json.dumps(
                {
                    "exit_intent_capital_certificate": {
                        "action": "SELL",
                        "position_id": "position-1",
                        "global_auction_receipt": receipt.as_payload(),
                    }
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?)",
        (
            "command-1",
            "position-1",
            "EXIT",
            "2026-08-13T00:00:02+00:00",
            "FILLED",
            "token-yes",
            "envelope-1",
        ),
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?,?,?)",
        (
            "fill-1",
            "position-1",
            2,
            "EXIT_ORDER_FILLED",
            "2026-08-13T00:00:03+00:00",
            "command-1",
            "{}",
        ),
    )
    conn.execute(
        "INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "position-1",
            "Chicago",
            "2026-08-13",
            "high",
            "condition-1",
            "buy_yes",
            0.2,
            5.0,
            1.0,
        ),
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?)",
        (
            "envelope-1",
            0,
            json.dumps({"fee_rate_fraction": 0.0}),
            "YES",
        ),
    )
    conn.execute(
        "INSERT INTO execution_fact VALUES (?,?,?,?,?,?)",
        (
            "command-1",
            "exit",
            0.4,
            5.0,
            "2026-08-13T00:00:03+00:00",
            "filled",
        ),
    )
    conn.execute(
        "INSERT INTO execution_feasibility_evidence VALUES (?,?,?)",
        (
            "token-yes",
            "2026-08-13T00:01:00+00:00",
            json.dumps({"bids": [["0.50", "5"]], "asks": []}),
        ),
    )

    curves = {
        "forecast": {
            "curve": [
                {
                    "position_id": "position-1",
                    "close_type": "EXIT_ORDER_FILLED",
                    "realized_at": "2026-08-13T00:00:03+00:00",
                    "capital_committed_usd": 1.0,
                    "net_realized_pnl_usd": 1.0,
                }
            ]
        }
    }
    evidence = evaluator._globally_selected_exit_quality(
        conn,
        forecasts,
        curves,
        as_of=datetime(2026, 8, 13, 1, tzinfo=timezone.utc),
    )

    assert evidence["status"] == "awaiting_verified_settlement"
    assert evidence["settlement_graded_exit_count"] == 0
    assert evidence["exit_vs_hold_incremental_usd"] is None
    assert evidence["curve"][0]["entry_to_exit_accounting_pnl_usd"] == 1.0
    assert evidence["curve"][0]["observed_peak_miss_usd_lower_bound"] == 0.5
    assert evidence["curve"][0]["peak_proof_status"].endswith(
        "NOT_COMPLETE_PEAK_PROOF"
    )

    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "settlement-1",
            "Chicago",
            "2026-08-13",
            "high",
            80,
            "F",
            "2026-08-14T00:00:00+00:00",
            "2026-08-14T00:00:01+00:00",
            "VERIFIED",
        ),
    )
    graded = evaluator._globally_selected_exit_quality(
        conn,
        forecasts,
        curves,
        as_of=datetime(2026, 8, 14, 1, tzinfo=timezone.utc),
    )

    assert graded["status"] == "settlement_graded_nonpositive"
    assert graded["settlement_graded_exit_count"] == 1
    assert graded["curve"][0]["hold_to_binary_payoff_usd"] == 5.0
    assert graded["curve"][0]["exit_vs_hold_incremental_usd"] == -3.0


def test_executable_bid_vwap_requires_full_size_depth():
    assert evaluator._executable_bid_vwap(
        json.dumps({"bids": [["0.50", "2"], ["0.40", "3"]]}),
        5,
    ) == pytest.approx(0.44)
    assert evaluator._executable_bid_vwap(
        json.dumps({"bids": [["0.50", "2"]]}),
        5,
    ) is None


def test_globally_compared_hold_is_graded_at_verified_binary_settlement():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,timestamp TEXT,"
        "artifact_json TEXT);"
        "CREATE TABLE position_current (position_id TEXT,phase TEXT,city TEXT,"
        "target_date TEXT,temperature_metric TEXT,condition_id TEXT,shares REAL,"
        "settled_at TEXT);"
        "CREATE TABLE position_events (position_id TEXT,event_type TEXT,occurred_at TEXT);"
    )
    coverage = [
        {
            "position_id": "position-hold",
            "status": "EVALUATED",
            "candidate_ids": ["sell-candidate"],
            "decision_at_utc": "2026-08-13T00:00:00+00:00",
            "held_shares": "5",
            "side": "YES",
            "condition_id": "condition-1",
        }
    ]
    raw_coverage = evaluator._canonical_json_bytes(coverage)
    summary = _proof_summary(
        city="Chicago",
        target_date="2026-08-13",
        condition_id="condition-1",
    )
    summary["holding_auction_coverage_zlib_b64"] = base64.b64encode(
        zlib.compress(raw_coverage)
    ).decode("ascii")
    summary["holding_auction_coverage_sha256"] = evaluator.hashlib.sha256(
        raw_coverage
    ).hexdigest()
    summary["execution_binding_hash"] = global_auction_execution_binding_hash(
        summary
    )
    summary["artifact_summary_hash"] = global_auction_artifact_summary_hash(
        summary
    )
    conn.execute(
        "INSERT INTO decision_log VALUES (1,?,?,?)",
        (
            "global_single_order_auction",
            "2026-08-13T00:00:00+00:00",
            json.dumps({"summary": summary}),
        ),
    )
    conn.execute(
        "INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?)",
        (
            "position-hold",
            "settled",
            "Chicago",
            "2026-08-13",
            "high",
            "condition-1",
            5.0,
            "2026-08-14T00:00:02+00:00",
        ),
    )
    forecasts = _settlement_db()
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?,?,?)",
        ("condition-1", "Chicago", "2026-08-13", "high", 80, 81),
    )
    forecasts.execute(
        "INSERT INTO settlement_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
        (
            1,
            "Chicago",
            "2026-08-13",
            "high",
            80,
            "F",
            "2026-08-14T00:00:00+00:00",
            "2026-08-14T00:00:01+00:00",
            "VERIFIED",
        ),
    )

    evidence = evaluator._held_to_binary_settlement_quality(
        conn,
        forecasts,
        as_of=datetime(2026, 8, 14, 1, tzinfo=timezone.utc),
    )

    assert evidence["status"] == "graded"
    assert evidence["settlement_graded_hold_count"] == 1
    assert evidence["held_to_one_count"] == 1
    assert evidence["held_to_zero_count"] == 0
    assert evidence["curve"][0]["result"] == "HELD_TO_ONE"
    assert evidence["curve"][0]["settlement_payoff_usd"] == 5.0
    assert evidence["curve"][0]["global_auction_decision_log_id"] == 1


def test_only_complete_positive_exact_revision_evidence_passes():
    verdict, failures = evaluator._build_verdict(
        receipt={"ready": True},
        shadows={
            "combined": {
                "global_selection_revision_bound": True,
                "independent_target_date_count": 30,
                "delta_log_wealth_lcb95": 0.001,
            }
        },
        live_curves={
            "combined": {
                "selection_revision_bound": True,
                "realized_position_count": 30,
                "realized_capital_committed_usd": 100.0,
                "net_realized_pnl_usd": 1.0,
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


def test_order_capital_ledger_accounts_every_attempt_and_exact_fill_fee():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE venue_commands (command_id TEXT,envelope_id TEXT,"
        "position_id TEXT,decision_id TEXT,intent_kind TEXT,side TEXT,size REAL,"
        "price REAL,state TEXT,created_at TEXT,updated_at TEXT,venue_order_id TEXT);"
        "CREATE TABLE venue_submission_envelopes (envelope_id TEXT,"
        "outcome_label TEXT,post_only INTEGER,fee_details_json TEXT);"
        "CREATE TABLE execution_fact (intent_id TEXT,command_id TEXT,"
        "order_role TEXT,fill_price REAL,shares REAL,filled_at TEXT,"
        "terminal_exec_status TEXT);"
        "CREATE TABLE position_events (position_id TEXT,command_id TEXT,"
        "event_type TEXT,sequence_no INTEGER,occurred_at TEXT,payload_json TEXT);"
    )
    fee = json.dumps({"fee_rate_fraction": 0.05})
    conn.executemany(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?)",
        [("entry-env", "YES", 0, fee), ("exit-env", "YES", 1, fee)],
    )
    conn.executemany(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "entry", "entry-env", "position", "decision-entry", "ENTRY",
                "BUY", 5.0, 0.4, "FILLED", "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:02+00:00", "entry-order",
            ),
            (
                "exit", "exit-env", "position", "decision-exit", "EXIT",
                "SELL", 2.0, 0.7, "CANCELLED", "2026-09-01T00:01:00+00:00",
                "2026-09-01T00:01:02+00:00", "exit-order",
            ),
            (
                "rejected", "entry-env", "other", "decision-reject", "ENTRY",
                "BUY", 5.0, 0.3, "REJECTED", "2026-09-01T00:02:00+00:00",
                "2026-09-01T00:02:01+00:00", "rejected-order",
            ),
        ],
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?,?)",
        (
            "position", "exit", "EXIT_ORDER_FILLED", 1,
            "2026-09-01T00:01:01+00:00", json.dumps({"pnl": 0.6}),
        ),
    )
    conn.executemany(
        "INSERT INTO execution_fact VALUES (?,?,?,?,?,?,?)",
        [
            (
                "entry-fact", "entry", "entry", 0.4, 5.0,
                "2026-09-01T00:00:02+00:00", "filled",
            ),
            (
                "exit-fact", "exit", "exit", 0.7, 2.0,
                "2026-09-01T00:01:01+00:00", "partial",
            ),
        ],
    )

    ledger = evaluator._order_capital_ledger(
        conn,
        as_of=datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
    )

    by_id = {row["command_id"]: row for row in ledger["orders"]}
    assert ledger["command_count"] == 3
    assert ledger["capital_truth_complete"] is True
    assert by_id["entry"]["capital_effect"] == "CAPITAL_COMMITTED_BY_FILL"
    assert by_id["entry"]["after_cost_cash_flow_usd"] == pytest.approx(-2.06)
    assert by_id["exit"]["capital_effect"] == "CAPITAL_RELEASED_BY_FILL"
    assert by_id["exit"]["after_cost_cash_flow_usd"] == pytest.approx(1.4)
    assert by_id["exit"][
        "realized_accounting_gain_after_exit_fee_usd"
    ] == pytest.approx(0.6)
    assert by_id["rejected"]["capital_effect"] == "ZERO_CAPITAL_EFFECT_NO_FILL"
    assert by_id["rejected"]["after_cost_cash_flow_usd"] == 0.0


def test_order_capital_ledger_fails_closed_on_filled_command_without_fact():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE venue_commands (command_id TEXT,envelope_id TEXT,"
        "position_id TEXT,decision_id TEXT,intent_kind TEXT,side TEXT,size REAL,"
        "price REAL,state TEXT,created_at TEXT,updated_at TEXT,venue_order_id TEXT);"
        "CREATE TABLE venue_submission_envelopes (envelope_id TEXT,"
        "outcome_label TEXT,post_only INTEGER,fee_details_json TEXT);"
        "CREATE TABLE execution_fact (intent_id TEXT,command_id TEXT,"
        "order_role TEXT,fill_price REAL,shares REAL,filled_at TEXT,"
        "terminal_exec_status TEXT);"
        "CREATE TABLE position_events (position_id TEXT,command_id TEXT,"
        "event_type TEXT,sequence_no INTEGER,occurred_at TEXT,payload_json TEXT);"
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "missing", "env", "position", "decision", "ENTRY", "BUY", 5.0,
            0.4, "FILLED", "2026-09-01T00:00:00+00:00",
            "2026-09-01T00:00:01+00:00", "missing-order",
        ),
    )

    ledger = evaluator._order_capital_ledger(
        conn,
        as_of=datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
    )

    assert ledger["capital_truth_complete"] is False
    assert ledger["incomplete_reasons"] == {
        "FILLED_COMMAND_EXECUTION_FACT_MISSING": 1
    }
    assert ledger["orders"][0]["after_cost_cash_flow_usd"] is None


def test_order_ledger_prefers_canonical_trade_and_partial_gain_journal():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE venue_commands (command_id TEXT,envelope_id TEXT,"
        "position_id TEXT,decision_id TEXT,intent_kind TEXT,side TEXT,size REAL,"
        "price REAL,state TEXT,created_at TEXT,updated_at TEXT,venue_order_id TEXT);"
        "CREATE TABLE venue_submission_envelopes (envelope_id TEXT,"
        "outcome_label TEXT,post_only INTEGER,fee_details_json TEXT);"
        "CREATE TABLE execution_fact (intent_id TEXT,command_id TEXT,"
        "order_role TEXT,fill_price REAL,shares REAL,filled_at TEXT,"
        "terminal_exec_status TEXT);"
        "CREATE TABLE venue_trade_facts (trade_fact_id INTEGER,command_id TEXT,"
        "trade_id TEXT,state TEXT,filled_size REAL,local_sequence INTEGER,"
        "venue_timestamp TEXT,observed_at TEXT,tx_hash TEXT,raw_payload_json TEXT,"
        "venue_order_id TEXT,fill_price REAL,fee_paid_micro INTEGER);"
        "CREATE TABLE position_events (event_id TEXT,position_id TEXT,"
        "command_id TEXT,order_id TEXT,event_type TEXT,sequence_no INTEGER,"
        "occurred_at TEXT,caused_by TEXT,payload_json TEXT);"
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?)",
        ("env", "YES", 1, "{}"),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "command", "env", "position", "decision", "EXIT", "SELL", 5.0,
            0.5, "FILLED", "2026-09-01T00:00:00+00:00",
            "2026-09-01T00:00:02+00:00", "venue-order",
        ),
    )
    conn.execute(
        "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            1, "command", "trade", "CONFIRMED", 2.0, 1,
            "2026-09-01T00:00:01+00:00", "2026-09-01T00:00:02+00:00",
            "", "{}", "venue-order", 0.6, 0,
        ),
    )
    conn.execute(
        "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            2, "command", "trade", "CONFIRMED", 5.0, 2,
            "2026-09-01T02:00:00+00:00", "2026-09-01T02:00:00+00:00",
            "", "{}", "venue-order", 0.9, 0,
        ),
    )
    conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "partial", "position", None, "venue-order", "MONITOR_REFRESHED", 1,
            "2026-09-01T00:00:02+00:00", "partial_exit_fill",
            json.dumps({"realized_pnl_delta_usd": "0.4"}),
        ),
    )

    ledger = evaluator._order_capital_ledger(
        conn,
        as_of=datetime(2026, 9, 1, 1, tzinfo=timezone.utc),
    )

    order = ledger["orders"][0]
    assert ledger["capital_truth_complete"] is True
    assert ledger["gain_truth_incomplete_command_count"] == 0
    assert order["fill_truth_source"] == "CANONICAL_ECONOMIC_VENUE_TRADE_FACT"
    assert order["canonical_trade_fact_count"] == 1
    assert order["execution_fact_count"] == 0
    assert order["after_cost_cash_flow_usd"] == pytest.approx(1.2)
    assert order["gain_status"] == "PARTIAL_EXIT_ACCOUNTING_GAIN_AFTER_EXIT_FEE"
    assert order["realized_accounting_gain_after_exit_fee_usd"] == pytest.approx(0.4)


def test_order_ledger_proof_gate_separates_capital_and_gain_gaps():
    assert evaluator._order_ledger_proof_failures(
        {
            "capital_truth_complete": True,
            "gain_truth_incomplete_command_count": 1,
        }
    ) == ["ORDER_GAIN_LEDGER_INCOMPLETE"]
    assert evaluator._order_ledger_proof_failures(
        {
            "capital_truth_complete": False,
            "gain_truth_incomplete_command_count": 1,
        }
    ) == [
        "ORDER_CAPITAL_LEDGER_INCOMPLETE",
        "ORDER_GAIN_LEDGER_INCOMPLETE",
    ]


def test_capital_artifact_write_cannot_regress_evaluated_at(tmp_path):
    artifact = tmp_path / "capital.json"
    newer = {
        "evaluated_at": "2026-09-01T01:00:01+00:00",
        "verdict": "FAIL",
        "marker": "newer",
    }
    older = {
        "evaluated_at": "2026-09-01T01:00:00+00:00",
        "verdict": "FAIL",
        "marker": "older",
    }

    assert evaluator._atomic_write(artifact, newer) is True
    assert evaluator._atomic_write(artifact, older) is False
    assert json.loads(artifact.read_text())["marker"] == "newer"


def test_total_portfolio_uses_chain_cash_and_selected_token_full_depth():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE collateral_ledger_snapshots (id INTEGER,"
        "pusd_balance_micro INTEGER,reserved_pusd_for_buys_micro INTEGER,"
        "captured_at TEXT,authority_tier TEXT);"
        "CREATE TABLE collateral_reservations (amount INTEGER,released_at TEXT);"
        "CREATE TABLE collateral_unsettled_proceeds (amount_micro INTEGER,"
        "settled_at TEXT);"
        "CREATE TABLE position_current (position_id TEXT,phase TEXT,city TEXT,"
        "target_date TEXT,temperature_metric TEXT,direction TEXT,chain_state TEXT,"
        "chain_shares REAL,token_id TEXT,no_token_id TEXT);"
        "CREATE TABLE execution_feasibility_evidence (token_id TEXT,direction TEXT,"
        "quote_seen_at TEXT,depth_before_json TEXT);"
        "CREATE TABLE execution_feasibility_latest (token_id TEXT,direction TEXT,"
        "quote_seen_at TEXT,depth_before_json TEXT);"
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots VALUES (1,100000000,10000000,?,?)",
        ("2026-09-01T00:00:00+00:00", "CHAIN"),
    )
    conn.execute("INSERT INTO collateral_reservations VALUES (10000000,NULL)")
    conn.execute(
        "INSERT INTO collateral_unsettled_proceeds VALUES (2000000,NULL)"
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (
                "yes", "active", "Paris", "2026-09-02", "high", "buy_yes",
                "synced", 5.0, "yes-token", "yes-no-token",
            ),
            (
                "no", "active", "Paris", "2026-09-02", "high", "buy_no",
                "synced", 3.0, "other-yes-token", "no-token",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO execution_feasibility_evidence VALUES (?,?,?,?)",
        [
            (
                "yes-token", "buy_yes", "2026-09-01T00:00:30+00:00",
                json.dumps({"bids": [[0.4, 10.0]]}),
            ),
            (
                "no-token", "buy_no", "2026-09-01T00:00:30+00:00",
                json.dumps({"bids": [[0.03, 10.0]]}),
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO execution_feasibility_latest VALUES (?,?,?,?)",
        [
            (
                "yes-token", "buy_yes", "2026-09-01T00:02:00+00:00",
                json.dumps({"bids": [[0.9, 10.0]]}),
            ),
            (
                "no-token", "buy_no", "2026-09-01T00:02:00+00:00",
                json.dumps({"bids": [[0.9, 10.0]]}),
            ),
        ],
    )

    capital = evaluator._current_total_portfolio_capital(
        conn,
        as_of=datetime(2026, 9, 1, 0, 1, tzinfo=timezone.utc),
    )

    assert capital["ready"] is True
    assert capital["chain_cash_usd"] == 100.0
    assert capital["spendable_cash_usd"] == 90.0
    assert capital["unsettled_exit_proceeds_usd"] == 2.0
    assert capital["total_portfolio_terminal_floor_usd"] == 102.0
    assert capital[
        "total_portfolio_current_executable_gross_usd_before_exit_fee"
    ] == pytest.approx(104.0)
    assert capital["total_portfolio_binary_payoff_ceiling_usd"] == 110.0
    assert capital["book_status_counts"] == {
        "BEST_BID_OUTSIDE_LIVE_SUBMIT_BAND": 1,
        "FULL_POSITION_EXECUTABLE": 1,
    }
    by_position = {row["position_id"]: row for row in capital["positions"]}
    assert by_position["no"]["selected_token_id"] == "no-token"
    assert by_position["no"]["executable_prefix_gross_usd_before_exit_fee"] == 0.0


def test_portfolio_curve_tracks_total_capital_change_not_cash_only():
    prior = {
        "evaluated_at": "2026-09-01T00:00:00+00:00",
        "observation_identity": "prior",
        "ready": True,
        "chain_cash_usd": 100.0,
        "total_portfolio_current_executable_gross_usd_before_exit_fee": 120.0,
        "total_portfolio_binary_payoff_ceiling_usd": 140.0,
    }
    current = {
        **prior,
        "evaluated_at": "2026-09-01T00:05:00+00:00",
        "observation_identity": "current",
        "chain_cash_usd": 95.0,
        "total_portfolio_current_executable_gross_usd_before_exit_fee": 125.0,
        "total_portfolio_binary_payoff_ceiling_usd": 145.0,
    }

    trajectory = evaluator._portfolio_observation_curve(
        current,
        prior=(prior,),
        as_of=datetime(2026, 9, 1, 0, 5, tzinfo=timezone.utc),
    )

    assert trajectory["observation_count"] == 2
    assert trajectory["latest_delta"]["chain_cash_delta_usd"] == -5.0
    assert trajectory["latest_delta"][
        "current_executable_gross_capital_delta_usd"
    ] == 5.0
    assert trajectory["profit_proof_eligible"] is False
