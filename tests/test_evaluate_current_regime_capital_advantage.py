# Created: 2026-08-12
# Last reused/audited: 2026-08-13
# Authority: current-regime capital proof must fail closed before entry reopens.

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
                "capital_action_mode": "IMMEDIATE_TAKER_BUY",
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
                "independent_family_day_count": 0,
                "delta_log_wealth_lcb95": None,
            }
        },
        live_curves={"day0": {"selection_revision_bound": False}},
    )

    assert verdict == "FAIL"
    assert "INSUFFICIENT_CURRENT_REGIME_SETTLED_FAMILY_DAYS" in failures
    assert "AFTER_COST_DELTA_LOG_WEALTH_LCB_NOT_POSITIVE" in failures
    assert "INSUFFICIENT_EXACT_REVISION_LIVE_REALIZED_POSITIONS" in failures
    assert "EXACT_REVISION_LIVE_CAPITAL_WEIGHTED_RETURN_NOT_POSITIVE" in failures


def test_counterfactual_admission_does_not_require_impossible_prior_live_fills():
    verdict, failures = evaluator._build_counterfactual_admission_verdict(
        receipt={"ready": True},
        shadows={
            "combined": {
                "global_selection_revision_bound": True,
                "independent_family_day_count": 30,
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


def test_counterfactual_evidence_counts_only_first_receipt_per_family_day():
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
    artifact = json.dumps({"summary": summary})
    for row_id in (1, 2):
        trades.execute(
            "INSERT INTO decision_log VALUES (?,?,?,?)",
            (
                row_id,
                "global_single_order_auction",
                "2026-08-12T00:00:02+00:00",
                artifact,
            ),
        )

    evidence = evaluator._settled_global_counterfactual_evidence(
        trades,
        forecasts,
        as_of=evaluator.datetime.fromisoformat("2026-08-14T00:00:00+00:00"),
    )

    assert evidence["independent_family_day_count"] == 1
    assert evidence["samples"][0]["decision_log_id"] == 1
    assert evidence["rejection_counts"]["duplicate_family_day"] == 1
    assert evidence["delta_log_wealth_lcb95"] is None


def test_live_curve_requires_exact_schema_22_edli_receipt_binding():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE venue_commands (position_id TEXT,intent_kind TEXT,decision_id TEXT);"
        "CREATE TABLE edli_live_order_events (aggregate_id TEXT,event_type TEXT,payload_json TEXT);"
        "CREATE TABLE decision_log (id INTEGER PRIMARY KEY,mode TEXT,artifact_json TEXT);"
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
    conn.execute("INSERT INTO venue_commands VALUES ('position-1','ENTRY','cmd-1')")
    conn.execute(
        "INSERT INTO edli_live_order_events VALUES ('aggregate-1','ExecutionCommandCreated',?)",
        (json.dumps({"execution_command_id": "cmd-1"}),),
    )
    conn.execute(
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
    )

    assert bound["selection_revision_bound"] is True
    assert bound["realized_position_count"] == 1
    assert bound["net_realized_pnl_usd"] == 1.0
    assert bound["curve"][0]["global_auction_decision_log_id"] == 1


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
                "independent_family_day_count": 30,
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
