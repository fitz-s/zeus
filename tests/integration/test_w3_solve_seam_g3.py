# Created: 2026-07-03
# Last reused/audited: 2026-07-17
# Authority basis: W3 SOLVE design packet, global fractional-Kelly repair,
#                  current Day0 global-cut routing, and auditable SELL holding bindings
"""G3 harness for the W3 SOLVE promotion seam (qkernel_spine_bridge.py w3_solve_enabled flag).

Proves the promotion flag is a SAFE, reversible, single-point cutover before any live enablement:
  (a) absent-vs-OFF byte-identity — the flag key absent vs explicitly False produce identical
      SpineDecisionResults over a fixture corpus (the OFF path is a no-op);
  (b) single-divergence-point — `w3_solve_enabled` is consumed at EXACTLY one code site (the guard);
  (c) ON-mode integration — with the flag ON the shim runs and every decision passes
      validate_family_decision_contract (no getattr-default consumer field fired);
  (d) OFF-path import-isolation — a decide call with the flag OFF does not import src.solve.

Fixtures are reused from tests/integration/test_qkernel_spine_routing.py (the realistic family +
proofs the legacy spine path is tested against).
"""

from __future__ import annotations

import ast
import base64
import datetime as _dt
import hashlib
import inspect
import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
import zlib
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

import src.engine.qkernel_spine_bridge as bridge
import src.engine.event_reactor_adapter as era
import src.engine.global_batch_runtime as global_batch_runtime
import src.engine.global_auction_universe as universe
from src.decision_kernel import claims
from src.decision_kernel.canonicalization import (
    qkernel_current_state_identity_hash,
    stable_hash,
)
from src.decision_kernel.certificate import build_certificate
from src.engine.global_single_order_auction import (
    _candidate_portfolio_endowment,
    global_single_order_actuation_identity,
    global_single_order_economic_identity,
    select_prepared_global_auction,
)
from src.engine.global_auction_universe import (
    CurrentGlobalBookAsset,
    CurrentGlobalBookEpoch,
    _current_day0_events,
    _day0_event_is_current_for_entry,
    capture_current_global_book_epoch,
    current_global_scope_events_with_day0,
    current_portfolio_wealth_witness,
    current_global_auction_scope_from_events,
    current_global_book_epoch_identity,
    probe_inflight_buy_ambiguity,
    refresh_current_global_book_epoch_tokens,
)
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.events.day0_authority import (
    assert_live_day0_probability_authority,
    assert_live_day0_qkernel_guard_authority,
)
from src.events.reactor import EventSubmissionReceipt
from src.solve.solver import (
    BinaryTerminalWealthCertificate,
    CurrentExecutionAuthority,
    CurrentFamilyProbabilityAuthority,
    ExecutableSellCurve,
    GlobalBuySizingRejection,
    GlobalSingleOrderCandidate,
    GlobalSingleOrderCandidateEvaluation,
    GlobalSingleOrderDecision,
    GlobalSingleOrderSellCandidate,
    JointOutcomeProbabilityWitness,
    OutcomeTokenBinding,
    PortfolioWealthWitness,
    global_candidate_from_native,
    global_sell_fill_prefix_objective,
    executable_curve_identity,
    joint_probability_witness_identity,
    portfolio_wealth_identity,
    validate_family_decision_contract,
)
from src.contracts.executable_cost_curve import BookLevel, ExecutableCostCurve, FeeModel
from src.contracts.executable_market_snapshot import (
    ExecutableMarketSnapshot,
    canonicalize_fee_details,
)
from src.contracts.semantic_types import Direction
from src.strategy import utility_ranker
from src.state.collateral_ledger import init_collateral_schema
from src.state.portfolio import PortfolioState
from src.state.snapshot_repo import get_snapshot, init_snapshot_schema, insert_snapshot
from src.state.schema.opportunity_events_schema import (
    ensure_table as ensure_opportunity_events_table,
)
from src.types.market import Bin
from tests.integration import test_qkernel_spine_routing as R

_BRIDGE_PATH = bridge.__file__


def test_global_auction_receipt_persists_complete_buy_sell_hold_cash_comparison():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            env TEXT NOT NULL
        )
        """
    )
    evaluations = (
        GlobalSingleOrderCandidateEvaluation(
            candidate_id="buy-paused",
            family_key="family-buy",
            bin_id="20C",
            condition_id="condition-buy",
            side="YES",
            token_id="token-buy",
            action="BUY",
            status="REJECTED",
            rejection_reason="FRACTIONAL_KELLY_INCREMENT_BELOW_MINIMUM",
            buy_sizing_rejection=GlobalBuySizingRejection(
                current_token_shares=Decimal("0"),
                full_kelly_target_shares=Decimal("40"),
                fractional_kelly_target_shares=Decimal("10"),
                minimum_marketable_increment_shares=Decimal("12"),
                minimum_fractional_kelly_multiplier=Decimal("0.3"),
                continuous_full_kelly_target_shares=Decimal("32"),
                continuous_fractional_kelly_target_shares=Decimal("8"),
                continuous_full_robust_delta_log_wealth=0.001,
                continuous_full_robust_ev_usd=0.1,
                minimum_marketable_cost_usd=Decimal("5.88"),
                minimum_marketable_robust_delta_log_wealth=0.0008,
                minimum_marketable_robust_ev_usd=0.08,
                minimum_marketable_capital_efficiency=0.00017,
                minimum_marketable_positive=True,
            ),
        ),
        GlobalSingleOrderCandidateEvaluation(
            candidate_id="sell-negative",
            family_key="family-sell",
            bin_id="21C",
            condition_id="condition-sell",
            side="NO",
            token_id="token-sell",
            action="SELL",
            status="REJECTED",
            position_id="position-sell",
            held_shares=Decimal("12.34"),
            rejection_reason="NON_POSITIVE_ROBUST_OBJECTIVE",
            shares=Decimal("12.34"),
            cost_usd=Decimal("2.34"),
            cash_proceeds_usd=Decimal("10"),
            robust_delta_log_wealth=-0.01,
            robust_ev_usd=-1.106,
            capital_efficiency=-0.004273504273504274,
            capital_action_mode="IMMEDIATE_REDUCE_ONLY_SELL",
            limit_price=Decimal("0.80"),
            expected_fill_price_before_fee=Decimal("0.81"),
            terminal_wealth=BinaryTerminalWealthCertificate(
                win_probability_lcb=0.1,
                loss_probability_ucb=0.9,
                loss_payoff_usd=Decimal("-2.34"),
                win_payoff_usd=Decimal("10"),
                median_payoff_usd=Decimal("-2.34"),
                wealth_after_loss_usd=Decimal("97.66"),
                wealth_after_win_usd=Decimal("110"),
                expected_value_diagnostic_usd=-1.106,
            ),
        ),
    )
    decision = GlobalSingleOrderDecision(
        candidate=None,
        shares=Decimal("0"),
        cost_usd=Decimal("0"),
        robust_delta_log_wealth=0.0,
        robust_ev_usd=0.0,
        capital_efficiency=0.0,
        no_trade_reason="NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        rejection_reasons={
            evaluation.candidate_id: str(evaluation.rejection_reason)
            for evaluation in evaluations
        },
        candidate_evaluations=evaluations,
        candidate_input_count=2,
    )
    selected = SimpleNamespace(decision=decision)
    at = _dt.datetime(2026, 7, 14, 1, 0, tzinfo=_dt.timezone.utc)
    book_asset_states = (
        (
            "family-buy",
            "20C",
            "condition-buy",
            "YES",
            "token-buy",
            "EXECUTABLE",
            "book-buy-yes",
            "event-buy",
            "gamma-buy",
        ),
        (
            "family-buy",
            "20C",
            "condition-buy",
            "NO",
            "token-buy-no",
            "NO_ASK",
            "book-buy-no",
            "event-buy",
            "gamma-buy",
        ),
        (
            "family-sell",
            "21C",
            "condition-sell",
            "YES",
            "token-sell-yes",
            "VENUE_NOT_EXECUTABLE",
            "metadata-sell-yes",
            "event-sell",
            "gamma-sell",
        ),
        (
            "family-sell",
            "21C",
            "condition-sell",
            "NO",
            "token-sell",
            "NO_ASK",
            "book-sell-no",
            "event-sell",
            "gamma-sell",
        ),
    )

    row_id = global_batch_runtime._store_global_auction_receipt(
        conn,
        selected=selected,
        selection_epoch_identity="epoch-current",
        selection_cut_at_utc=at,
        decision_at_utc=at + _dt.timedelta(seconds=1),
        probability_manifest=(("family-buy", "q-buy"), ("family-sell", "q-sell")),
        full_scope_identity="full-scope-current",
        full_scope_family_keys=(
            "family-buy",
            "family-sell",
            "family-q-missing",
        ),
        probability_ineligible_by_family={
            "family-q-missing": (
                "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
                "GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED"
            )
        },
        book_epoch_identity="book-current",
        book_asset_count=2,
        book_asset_states=book_asset_states,
        wealth_witness=SimpleNamespace(
            witness_identity="wealth-current",
            economic_identity="wealth-economics-current",
        ),
        fractional_kelly_multiplier=Decimal("0.25"),
        book_captured_at_utc=at + _dt.timedelta(milliseconds=250),
        book_max_age=_dt.timedelta(seconds=30),
        excluded_by_candidate={
            (
                "BUY",
                "family-buy",
                "20C",
                "YES",
                "token-buy",
            ): "jit depth insufficient"
        },
    )

    row = conn.execute(
        "SELECT mode, artifact_json FROM decision_log WHERE id = ?", (row_id,)
    ).fetchone()
    artifact = json.loads(row["artifact_json"])
    summary = artifact["summary"]
    assert row["mode"] == "global_single_order_auction"
    assert summary["schema_version"] == 14
    assert summary["book_capture_freshness_complete"] is True
    assert summary["book_captured_at_utc"] == "2026-07-14T01:00:00.250000+00:00"
    assert summary["book_deadline_at_utc"] == "2026-07-14T01:00:30.250000+00:00"
    assert summary["book_max_age_seconds"] == 30.0
    assert summary["excluded_by_candidate"] == [
        {
            "action": "BUY",
            "family_key": "family-buy",
            "bin_id": "20C",
            "side": "YES",
            "token_id": "token-buy",
            "reason": "jit depth insufficient",
        }
    ]
    assert summary["full_scope_identity"] == "full-scope-current"
    assert summary["full_scope_family_count"] == 3
    assert summary["eligible_probability_family_count"] == 2
    assert summary["probability_ineligible_family_count"] == 1
    assert summary["scope_family_coverage_complete"] is True
    assert summary["book_native_side_state_count"] == 4
    assert summary["book_native_side_executable_count"] == 1
    assert summary["book_native_side_non_executable_count"] == 3
    assert summary["book_native_side_candidate_coverage_complete"] is True
    assert summary["book_native_side_candidate_coverage_status"] == "COMPLETE"
    assert summary["book_native_side_status_counts"] == {
        "NO": {
            "EXECUTABLE": 0,
            "NO_ASK": 2,
            "VENUE_METADATA_STALE": 0,
            "VENUE_NOT_EXECUTABLE": 0,
        },
        "YES": {
            "EXECUTABLE": 1,
            "NO_ASK": 0,
            "VENUE_METADATA_STALE": 0,
            "VENUE_NOT_EXECUTABLE": 1,
        },
    }
    book_side_states = json.loads(
        zlib.decompress(
            base64.b64decode(summary["book_native_side_states_zlib_b64"])
        )
    )
    assert book_side_states["fields"] == list(
        global_batch_runtime._BOOK_NATIVE_SIDE_STATE_FIELDS
    )
    assert book_side_states["rows"] == [
        list(row) for row in sorted(book_asset_states)
    ]
    with pytest.raises(
        ValueError,
        match=(
            "GLOBAL_AUCTION_RECEIPT_BUY_BOOK_MATERIALIZATION_MISMATCH:"
            "missing=1:extra=0"
        ),
    ):
        global_batch_runtime._book_native_side_receipt(
            asset_states=(
                book_asset_states[0],
                (
                    *book_asset_states[1][:5],
                    "EXECUTABLE",
                    *book_asset_states[1][6:],
                ),
                *book_asset_states[2:],
            ),
            probability_keys=("family-buy", "family-sell"),
            buy_candidate_index=(
                (
                    "buy-paused",
                    "family-buy",
                    "20C",
                    "condition-buy",
                    "YES",
                    "token-buy",
                ),
            ),
            excluded_by_family={},
        )
    assert summary["probability_ineligible_by_family"] == {
        "family-q-missing": (
            "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
            "GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED"
        )
    }
    assert summary["candidate_coverage_complete"] is True
    assert summary["candidate_condition_index_complete"] is True
    assert summary["buy_candidate_index_complete"] is True
    assert summary["buy_candidate_index_count"] == 1
    assert summary["candidate_evaluation_count"] == 2
    assert summary["candidate_input_count"] == 2
    assert summary["candidate_detailed_count"] == 1
    assert summary["candidate_rejection_group_count"] == 1
    assert summary["buy_sizing_rejection_count"] == 1
    assert summary["buy_sizing_rejection_complete"] is True
    assert summary["buy_sizing_rejection_encoding"] == (
        "zlib+base64+indexed-canonical-json-v3"
    )
    assert summary["buy_sizing_rejection_index_source"] == (
        "candidate_evaluations.buy_candidate_index"
    )
    sizing_rejection_json = zlib.decompress(
        base64.b64decode(summary["buy_sizing_rejections_zlib_b64"])
    )
    assert hashlib.sha256(sizing_rejection_json).hexdigest() == (
        summary["buy_sizing_rejections_sha256"]
    )
    sizing_rejections = json.loads(sizing_rejection_json)
    assert sizing_rejections == {
        "fields": [
            "buy_candidate_index",
            "current_token_shares",
            "full_kelly_target_shares",
            "fractional_kelly_target_shares",
            "minimum_marketable_increment_shares",
            "minimum_fractional_kelly_multiplier",
            "continuous_full_kelly_target_shares",
            "continuous_fractional_kelly_target_shares",
            "continuous_full_robust_delta_log_wealth",
            "continuous_full_robust_ev_usd",
            "minimum_marketable_cost_usd",
            "minimum_marketable_robust_delta_log_wealth",
            "minimum_marketable_robust_ev_usd",
            "minimum_marketable_capital_efficiency",
            "minimum_marketable_positive",
        ],
        "rows": [
            [
                0,
                "0",
                "40",
                "10",
                "12",
                "0.3",
                "32",
                "8",
                0.001,
                0.1,
                "5.88",
                0.0008,
                0.08,
                0.00017,
                True,
            ]
        ],
    }
    assert summary["hold_cash"] == {
        "robust_delta_log_wealth": "0",
        "robust_ev_usd": "0",
        "selected": True,
    }
    evaluation_json = zlib.decompress(
        base64.b64decode(summary["candidate_evaluations_zlib_b64"])
    )
    assert hashlib.sha256(evaluation_json).hexdigest() == (
        summary["candidate_evaluations_sha256"]
    )
    assert summary["candidate_evaluation_encoding"] == (
        "zlib+base64+canonical-json-v7"
    )
    candidate_evaluations = json.loads(evaluation_json)
    sizing_row = dict(
        zip(
            sizing_rejections["fields"],
            sizing_rejections["rows"][0],
            strict=True,
        )
    )
    sizing_identity = candidate_evaluations["buy_candidate_index"][
        sizing_row["buy_candidate_index"]
    ]
    assert sizing_identity == [
        "buy-paused",
        "family-buy",
        "20C",
        "condition-buy",
        "YES",
        "token-buy",
    ]
    assert candidate_evaluations["rejected_groups"] == [
        {
            "action": "BUY",
            "side": "YES",
            "reason": "FRACTIONAL_KELLY_INCREMENT_BELOW_MINIMUM",
            "candidate_ids": ["buy-paused"],
        }
    ]
    assert candidate_evaluations["buy_condition_side_masks"] == [
        ["condition-buy", 1]
    ]
    assert candidate_evaluations["buy_candidate_index_fields"] == [
        "candidate_id",
        "family_key",
        "bin_id",
        "condition_id",
        "side",
        "token_id",
    ]
    assert candidate_evaluations["buy_candidate_index"] == [
        [
            "buy-paused",
            "family-buy",
            "20C",
            "condition-buy",
            "YES",
            "token-buy",
        ]
    ]
    assert summary["buy_condition_membership_count"] == 1
    assert [
        (
            evaluation["action"],
            evaluation["status"],
            evaluation["rejection_reason"],
            evaluation["position_id"],
            evaluation["held_shares"],
        )
        for evaluation in candidate_evaluations["detailed"]
    ] == [
        (
            "SELL",
            "REJECTED",
            "NON_POSITIVE_ROBUST_OBJECTIVE",
            "position-sell",
            "12.34",
        )
    ]
    sell_evaluation = candidate_evaluations["detailed"][0]
    assert sell_evaluation["shares"] == "12.34"
    assert sell_evaluation["cash_proceeds_usd"] == "10"
    assert sell_evaluation["limit_price"] == "0.80"
    assert sell_evaluation["expected_fill_price_before_fee"] == "0.81"
    assert sell_evaluation["robust_delta_log_wealth"] == -0.01
    assert sell_evaluation["robust_ev_usd"] == -1.106
    assert sell_evaluation["capital_action_mode"] == (
        "IMMEDIATE_REDUCE_ONLY_SELL"
    )
    assert sell_evaluation["resolution_at_utc"] is None
    assert sell_evaluation["capital_lock_hours"] is None
    assert sell_evaluation["robust_log_growth_per_hour"] is None
    assert len(summary["receipt_hash"]) == 64
    with pytest.raises(ValueError, match="GLOBAL_AUCTION_RECEIPT_SCOPE_INCOMPLETE"):
        global_batch_runtime._store_global_auction_receipt(
            conn,
            selected=selected,
            selection_epoch_identity="epoch-incomplete",
            selection_cut_at_utc=at,
            decision_at_utc=at + _dt.timedelta(seconds=1),
            probability_manifest=(
                ("family-buy", "q-buy"),
                ("family-sell", "q-sell"),
            ),
            full_scope_identity="full-scope-incomplete",
            full_scope_family_keys=(
                "family-buy",
                "family-sell",
                "family-unaccounted",
            ),
            probability_ineligible_by_family={},
            book_epoch_identity="book-current",
            book_asset_count=2,
            book_asset_states=book_asset_states,
            wealth_witness=SimpleNamespace(
                witness_identity="wealth-current",
                economic_identity="wealth-economics-current",
            ),
            fractional_kelly_multiplier=Decimal("0.25"),
        )
    conn.close()


def test_global_auction_receipt_preserves_book_states_with_zero_evaluations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            env TEXT NOT NULL
        )
        """
    )
    decision = GlobalSingleOrderDecision(
        candidate=None,
        shares=Decimal("0"),
        cost_usd=Decimal("0"),
        robust_delta_log_wealth=0.0,
        robust_ev_usd=0.0,
        capital_efficiency=0.0,
        no_trade_reason="NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        rejection_reasons={},
        candidate_evaluations=(),
        candidate_input_count=0,
    )
    at = _dt.datetime(2026, 7, 16, 18, 0, tzinfo=_dt.timezone.utc)
    row_id = global_batch_runtime._store_global_auction_receipt(
        conn,
        selected=SimpleNamespace(decision=decision),
        selection_epoch_identity="epoch-empty-current",
        selection_cut_at_utc=at,
        decision_at_utc=at + _dt.timedelta(seconds=1),
        probability_manifest=(("family-empty", "q-empty"),),
        full_scope_identity="full-scope-empty-current",
        full_scope_family_keys=("family-empty",),
        probability_ineligible_by_family={},
        book_epoch_identity="book-empty-current",
        book_asset_count=1,
        book_asset_states=(
            (
                "family-empty",
                "20C",
                "condition-empty",
                "YES",
                "yes-empty",
                "NO_ASK",
                "book-empty-yes",
                "event-empty",
                "gamma-empty",
            ),
            (
                "family-empty",
                "20C",
                "condition-empty",
                "NO",
                "no-empty",
                "VENUE_NOT_EXECUTABLE",
                "metadata-empty-no",
                "event-empty",
                "gamma-empty",
            ),
        ),
        wealth_witness=SimpleNamespace(
            witness_identity="wealth-empty-current",
            economic_identity="wealth-economics-empty-current",
        ),
        fractional_kelly_multiplier=Decimal("0.25"),
        book_captured_at_utc=at,
        book_max_age=_dt.timedelta(seconds=30),
    )

    artifact = json.loads(
        conn.execute(
            "SELECT artifact_json FROM decision_log WHERE id = ?",
            (row_id,),
        ).fetchone()["artifact_json"]
    )
    summary = artifact["summary"]
    assert summary["candidate_evaluation_count"] == 0
    assert summary["candidate_coverage_complete"] is True
    assert summary["book_native_side_candidate_coverage_status"] == "COMPLETE"
    assert summary["book_native_side_candidate_coverage_complete"] is True
    assert summary["book_native_side_state_count"] == 2
    assert summary["book_native_side_executable_count"] == 0
    assert summary["book_native_side_non_executable_count"] == 2


def test_global_auction_receipt_reuses_unchanged_heavy_no_trade_payload(tmp_path):
    db_path = tmp_path / "trade.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            env TEXT NOT NULL
        )
        """
    )
    decision = GlobalSingleOrderDecision(
        candidate=None,
        shares=Decimal("0"),
        cost_usd=Decimal("0"),
        robust_delta_log_wealth=0.0,
        robust_ev_usd=0.0,
        capital_efficiency=0.0,
        no_trade_reason="NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        rejection_reasons={},
        candidate_evaluations=(),
        candidate_input_count=0,
    )
    selected = SimpleNamespace(decision=decision)
    at = _dt.datetime(2026, 7, 17, 6, 0, tzinfo=_dt.timezone.utc)
    book_states = (
        (
            "family-empty",
            "20C",
            "condition-empty",
            "YES",
            "yes-empty",
            "NO_ASK",
            "book-empty-yes",
            "event-empty",
            "gamma-empty",
        ),
        (
            "family-empty",
            "20C",
            "condition-empty",
            "NO",
            "no-empty",
            "VENUE_NOT_EXECUTABLE",
            "metadata-empty-no",
            "event-empty",
            "gamma-empty",
        ),
    )

    def store(
        *,
        suffix: str,
        current_selected: object = selected,
    ) -> int:
        row_id = global_batch_runtime._store_global_auction_receipt(
            conn,
            selected=current_selected,
            selection_epoch_identity=f"epoch-{suffix}",
            selection_cut_at_utc=at,
            decision_at_utc=at + _dt.timedelta(seconds=1),
            probability_manifest=(("family-empty", f"q-{suffix}"),),
            full_scope_identity="full-scope-empty-current",
            full_scope_family_keys=("family-empty",),
            probability_ineligible_by_family={},
            book_epoch_identity=f"book-{suffix}",
            book_asset_count=1,
            book_asset_states=book_states,
            wealth_witness=SimpleNamespace(
                witness_identity=f"wealth-{suffix}",
                economic_identity="wealth-economics-current",
            ),
            fractional_kelly_multiplier=Decimal("0.25"),
            book_captured_at_utc=at,
            book_max_age=_dt.timedelta(seconds=30),
        )
        assert row_id is not None
        return row_id

    full_row_id = store(suffix="first")
    duplicate_row_id = store(suffix="second")
    rows = conn.execute(
        "SELECT id, mode, artifact_json FROM decision_log ORDER BY id"
    ).fetchall()
    assert [row["mode"] for row in rows] == [
        "global_single_order_auction",
        "global_single_order_auction_duplicate",
    ]
    full_summary = json.loads(rows[0]["artifact_json"])["summary"]
    duplicate_summary = json.loads(rows[1]["artifact_json"])["summary"]
    assert full_row_id == rows[0]["id"]
    assert duplicate_row_id == rows[1]["id"]
    assert duplicate_summary["payload_compacted"] is True
    assert duplicate_summary["payload_reference_decision_log_id"] == full_row_id
    assert duplicate_summary["payload_reference_receipt_hash"] == (
        full_summary["receipt_hash"]
    )
    assert duplicate_summary["probability_manifest"] == [
        ["family-empty", "q-second"]
    ]
    assert duplicate_summary["wealth_witness_identity"] == "wealth-second"
    for field in global_batch_runtime._GLOBAL_AUCTION_HEAVY_RECEIPT_FIELDS:
        assert field in full_summary
        assert field not in duplicate_summary
    assert len(rows[1]["artifact_json"]) < len(rows[0]["artifact_json"])

    winner = SimpleNamespace(
        decision=SimpleNamespace(
            candidate=SimpleNamespace(candidate_id="winner"),
            candidate_evaluations=(),
            candidate_input_count=0,
            no_trade_reason=None,
        )
    )
    winner_row_id = store(suffix="winner", current_selected=winner)
    winner_mode = conn.execute(
        "SELECT mode FROM decision_log WHERE id = ?",
        (winner_row_id,),
    ).fetchone()["mode"]
    assert winner_mode == "global_single_order_auction"
    conn.close()


def test_global_auction_no_trade_log_groups_dynamic_reason_details():
    summary = global_batch_runtime._no_trade_rejection_log_summary(
        SimpleNamespace(
            rejection_reasons={
                "a": "ENTRY_PRICE_BELOW_FLOOR:best_ask=0.31",
                "b": "ENTRY_PRICE_BELOW_FLOOR:best_ask=0.32",
                "c": "NON_POSITIVE_ROBUST_OBJECTIVE:q=0.41",
                "d": "BOOK_STALE:age=3.1",
            }
        ),
        limit=2,
    )

    assert summary == (
        {
            "ENTRY_PRICE_BELOW_FLOOR": 2,
            "BOOK_STALE": 1,
        },
        4,
        1,
    )


def test_global_candidate_correlation_key_is_weather_family_not_event_or_token():
    family_key = "edli_family_shared"
    first = SimpleNamespace(
        family_key=family_key,
        token_id="yes-token",
        owner_event_id="event-a",
    )
    sibling = SimpleNamespace(
        family_key=family_key,
        token_id="no-token",
        owner_event_id="event-b",
    )

    assert era._global_candidate_correlation_key(first) == family_key
    assert era._global_candidate_correlation_key(sibling) == family_key
    with pytest.raises(ValueError, match="GLOBAL_CANDIDATE_FAMILY_ID_MISSING"):
        era._global_candidate_correlation_key(
            SimpleNamespace(token_id="token-without-family")
        )


def test_global_preflight_receipt_persists_pause_and_zero_venue_side_effects():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            env TEXT NOT NULL
        )
        """
    )
    at = _dt.datetime(2026, 7, 15, 14, 29, tzinfo=_dt.timezone.utc)
    candidate = SimpleNamespace(
        candidate_id="candidate-best",
        action="BUY",
        family_key="family-shenzhen",
        bin_id="31C",
        condition_id="condition-shenzhen-31c",
        side="NO",
        token_id="token-shenzhen-31c-no",
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=candidate),
        actuation=SimpleNamespace(
            selection_epoch_identity="epoch-current",
            selection_cut_at_utc=at,
            decision_at_utc=at + _dt.timedelta(seconds=10),
            actuation_identity="actuation-current",
        ),
    )
    preflight = global_batch_runtime.GlobalWinnerPreflight(
        status="BATCH_BLOCKED",
        reason="entries_paused:external:operator",
    )
    authority = global_batch_runtime.GlobalPreflightAuthority(
        probability_manifest=(("family-shenzhen", "q-current"),),
        book_epoch_identity="book-current",
        book_economics_manifest=(("BUY", "token-shenzhen-31c-no"),),
        wealth_witness_identity="wealth-current",
        actuation_deadline=at + _dt.timedelta(seconds=30),
    )

    row_id = global_batch_runtime._store_global_preflight_receipt(
        conn,
        selected=selected,
        preflight=preflight,
        authority=authority,
        checked_at_utc=at + _dt.timedelta(seconds=11),
        winner_event_id="winner-event",
        venue_submit_count_before=0,
        venue_submit_count_after=0,
    )

    row = conn.execute(
        "SELECT mode, artifact_json FROM decision_log WHERE id = ?", (row_id,)
    ).fetchone()
    artifact = json.loads(row["artifact_json"])
    summary = artifact["summary"]
    assert row["mode"] == "global_single_order_auction_preflight"
    assert summary["selection_epoch_identity"] == "epoch-current"
    assert summary["winner_candidate_id"] == "candidate-best"
    assert summary["preflight_status"] == "BATCH_BLOCKED"
    assert summary["preflight_reason"] == "entries_paused:external:operator"
    assert summary["book_epoch_identity"] == "book-current"
    assert summary["venue_submit_count_before"] == 0
    assert summary["venue_submit_count_after"] == 0
    assert summary["venue_side_effect_free"] is True
    receipt_hash = summary.pop("receipt_hash")
    encoded = json.dumps(
        summary,
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == receipt_hash
    conn.close()


def test_global_preflight_exhaustion_distinguishes_cash_from_authority_failure():
    thin_buy = "GLOBAL_CANDIDATE_ALL_SIZES_INFEASIBLE:candidate=candidate-a"
    assert global_batch_runtime._global_preflight_exhaustion_reason(
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        excluded_by_family={},
        excluded_by_candidate={
            ("BUY", "family-a", "bin-a", "NO", "token-a"): thin_buy
        },
    ) == (
        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:families=0:candidates=1"
    )
    assert global_batch_runtime._global_preflight_exhaustion_reason(
        "ROBUST_MAJORITY_LOSS",
        excluded_by_family={},
        excluded_by_candidate={},
    ) == (
        "GLOBAL_PREFLIGHT_HOLD_CASH_OPTIMAL:"
        "ROBUST_MAJORITY_LOSS:families=0:candidates=0"
    )
    assert global_batch_runtime._global_preflight_exhaustion_reason(
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER",
        excluded_by_family={"family-a": "GLOBAL_ACTUATION_BOOK_SUPERSEDED"},
        excluded_by_candidate={},
    ) == (
        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
        "NO_CURRENT_EXECUTABLE_POSITIVE_ORDER:families=1:candidates=0"
    )
    assert global_batch_runtime._global_preflight_exhaustion_reason(
        "GLOBAL_EPOCH_SUPERSEDED",
        excluded_by_family={},
        excluded_by_candidate={},
    ) == (
        "GLOBAL_PREFLIGHT_ACTION_SET_EXHAUSTED:"
        "GLOBAL_EPOCH_SUPERSEDED:families=0:candidates=0"
    )


def test_global_selection_binds_holdings_to_exact_wealth_ledger_generation():
    binding_a = OutcomeTokenBinding(
        bin_id="bin-a",
        condition_id="condition-a",
        yes_token_id="yes-a",
        no_token_id="no-a",
    )
    binding_b = OutcomeTokenBinding(
        bin_id="bin-b",
        condition_id="condition-b",
        yes_token_id="yes-b",
        no_token_id="no-b",
    )
    prepared = {
        "event-a": bridge.PreparedGlobalFamily(
            decision_id="decision-a",
            probability_witness=SimpleNamespace(
                family_key="family-a", bindings=(binding_a,)
            ),
            candidate_seeds=(),
        ),
        "event-b": bridge.PreparedGlobalFamily(
            decision_id="decision-b",
            probability_witness=SimpleNamespace(
                family_key="family-b", bindings=(binding_b,)
            ),
            candidate_seeds=(),
        ),
    }
    state = SimpleNamespace(
        positions=(
            SimpleNamespace(
                position_id="position-a",
                condition_id="condition-a",
                direction="buy_yes",
                token_id="yes-a",
                no_token_id="no-a",
                chain_shares=Decimal("7.25"),
            ),
        )
    )

    rebound = global_batch_runtime._bind_selection_holdings(
        prepared,
        portfolio_state=state,
        wealth_witness=SimpleNamespace(
            ledger_snapshot_id="ledger-selection-cut",
            native_holdings_micro=(("yes-a", 7_250_000),),
        ),
    )

    holding_a = rebound["event-a"].holdings_snapshot
    holding_b = rebound["event-b"].holdings_snapshot
    assert holding_a.ledger_snapshot_id == "ledger-selection-cut"
    assert holding_b.ledger_snapshot_id == "ledger-selection-cut"
    assert holding_a.holdings[0].position_id == "position-a"
    assert holding_a.holdings[0].shares == Decimal("7.25")
    assert holding_b.holdings == ()


def test_global_actuation_does_not_blanket_block_existing_family_exposure():
    """A first fill must not structurally disable every later global order."""

    actuation_source = inspect.getsource(
        era._build_event_bound_no_submit_receipt_core
    )
    metrics_source = inspect.getsource(
        __import__("src.solve.solver", fromlist=["_single_order_metrics"])
        ._single_order_metrics
    )

    assert "GLOBAL_EXISTING_FAMILY_EXPOSURE_UNMODELED" not in actuation_source
    assert "_family_existing_exposure_for_selection_by_bin_id" in actuation_source
    assert "Coupling-robust endowment bound" in metrics_source


def test_current_gamma_market_fetch_batches_concurrently_and_fails_closed():
    condition_ids = tuple(f"condition-{index}" for index in range(205))
    barrier = threading.Barrier(3)
    chunks = []
    lock = threading.Lock()

    def gamma_get(path, *, params, timeout):
        assert path == "/markets"
        assert timeout == 4.0
        chunk = tuple(params["condition_ids"])
        assert params["limit"] == len(chunk)
        with lock:
            chunks.append(chunk)
        barrier.wait(timeout=2.0)
        return SimpleNamespace(
            status_code=200,
            json=lambda: [{"conditionId": condition_id} for condition_id in chunk],
        )

    markets, request_count = universe.fetch_current_gamma_markets(
        condition_ids,
        gamma_get=gamma_get,
        timeout=4.0,
        max_workers=3,
    )
    assert request_count == 3
    assert sorted(map(len, chunks)) == [5, 100, 100]
    assert {market["conditionId"] for market in markets} == set(condition_ids)

    def response(payload, status_code=200):
        return SimpleNamespace(status_code=status_code, json=lambda: payload)

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_GAMMA_MARKETS_HTTP:503"):
        universe.fetch_current_gamma_markets(
            ("condition-0",),
            gamma_get=lambda *_args, **_kwargs: response([], 503),
            timeout=4.0,
        )
    with pytest.raises(
        ValueError, match="GLOBAL_CURRENT_GAMMA_MARKETS_RESPONSE_INVALID"
    ):
        universe.fetch_current_gamma_markets(
            ("condition-0",),
            gamma_get=lambda *_args, **_kwargs: response({}),
            timeout=4.0,
        )
    with pytest.raises(ValueError, match="GLOBAL_CURRENT_GAMMA_MARKET_INVALID"):
        universe.fetch_current_gamma_markets(
            ("condition-0",),
            gamma_get=lambda *_args, **_kwargs: response([None]),
            timeout=4.0,
        )

    def worker_error(_path, *, params, timeout):
        del timeout
        if "condition-100" in params["condition_ids"]:
            raise RuntimeError("worker failed")
        return response([])

    with pytest.raises(RuntimeError, match="worker failed"):
        universe.fetch_current_gamma_markets(
            tuple(f"condition-{index}" for index in range(101)),
            gamma_get=worker_error,
            timeout=4.0,
            max_workers=2,
        )


_SPINE_DECISION_AT = _dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc)


def _spine_wealth_witness() -> PortfolioWealthWitness:
    identity = portfolio_wealth_identity(
        ledger_snapshot_id="spine-ledger",
        position_set_hash="spine-empty-positions",
        wealth_floor_usd=Decimal("1000"),
        wealth_ceiling_usd=Decimal("1000"),
        spendable_cash_usd=Decimal("1000"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=_SPINE_DECISION_AT,
    )
    return PortfolioWealthWitness(
        ledger_snapshot_id="spine-ledger",
        position_set_hash="spine-empty-positions",
        wealth_floor_usd=Decimal("1000"),
        wealth_ceiling_usd=Decimal("1000"),
        spendable_cash_usd=Decimal("1000"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=_SPINE_DECISION_AT,
        max_age=_dt.timedelta(seconds=5),
        witness_identity=identity,
    )


def _drive(family, proofs, payload):
    """Drive decide_family_via_spine with a FIXED positive baseline so the fixture's wealth is
    deterministic (the module bankroll provider is not warm in-test); identical for OFF and ON."""
    return bridge.decide_family_via_spine(
        family=family, payload=payload, proofs=proofs,
        decision_time=_SPINE_DECISION_AT,
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
        solve_wealth_witness=_spine_wealth_witness(),
        solve_positions=(),
    )


def _payload_with_joint_samples(proofs, payload, *, draws=64):
    """Attach a coherent current-posterior draw matrix to a synthetic fixture payload."""
    out = dict(payload)
    out["_edli_spine_served_joint_q_samples_by_condition"] = {
        str(proof.candidate.condition_id): [float(proof.q_posterior)] * draws
        for proof in proofs
        if proof.direction == "buy_yes"
    }
    out["_edli_spine_posterior_identity_hash"] = "fixture-current-posterior"
    return out


def test_global_prepare_empty_scope_names_admission_classes_without_changing_scope():
    _family, proofs, _payload = _corpus()[0]
    ordinary_diagnostic: dict[str, object] = {}
    ordinary = era._selection_scoped_proofs(
        proofs=proofs,
        honor_admission_rejections=False,
        enforce_win_rate_floor=False,
        diagnostic_out=ordinary_diagnostic,
    )
    assert ordinary == tuple(proofs)
    assert ordinary_diagnostic == {}

    blocked = tuple(
        replace(proof, missing_reason="BUY_NO_CONSERVATIVE_EVIDENCE_MISSING")
        for proof in proofs
    )
    blocked_diagnostic: dict[str, object] = {}
    assert era._selection_scoped_proofs(
        proofs=blocked,
        honor_admission_rejections=False,
        enforce_win_rate_floor=False,
        diagnostic_out=blocked_diagnostic,
    ) == ()
    assert blocked_diagnostic == {
        "empty_reason": (
            "SELECTION_SCOPE_EMPTY:admission:"
            f"input={len(blocked)}:classes=BUY_NO_CONSERVATIVE_EVIDENCE_MISSING="
            f"{len(blocked)}"
        )
    }


def test_global_prepare_failure_preserves_early_spine_no_trade_reason():
    assert era._global_prepare_failure_reason(
        SimpleNamespace(
            global_family=None,
            global_prepare_reason=None,
            no_trade_reason="SPINE_INPUTS_UNAVAILABLE:DAY0_OBSERVATION_STALE",
        )
    ) == "SPINE_INPUTS_UNAVAILABLE:DAY0_OBSERVATION_STALE"
    assert era._global_prepare_failure_reason(
        SimpleNamespace(
            global_family=None,
            global_prepare_reason="GLOBAL_FAMILY_PREPARE_FAILED:ValueError:bad",
            no_trade_reason="SPINE_NO_SELECTION",
        )
    ) == "GLOBAL_FAMILY_PREPARE_FAILED:ValueError:bad"
    assert era._global_prepare_failure_reason(
        SimpleNamespace(
            global_family=object(),
            global_prepare_reason=None,
            no_trade_reason="SPINE_NO_SELECTION",
        )
    ) is None


def test_current_global_family_survives_duplicate_local_spine_input_loss():
    spine = SimpleNamespace(
        decision=None,
        no_trade_reason="SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED",
    )

    assert era._global_actuation_local_spine_failure_reason(
        spine,
        prepared_global_family=object(),
    ) is None
    assert era._global_actuation_local_spine_failure_reason(
        spine,
        prepared_global_family=None,
    ) == "SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED"
    assert era._global_actuation_local_spine_failure_reason(
        SimpleNamespace(decision=None, no_trade_reason="SPINE_WIRING_FAULT:broken"),
        prepared_global_family=object(),
    ) == "SPINE_WIRING_FAULT:broken"


def test_global_actuation_revalidates_content_then_preserves_selected_witness(monkeypatch):
    content = {
        field: f"current-{field}"
        for field in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    }
    selected = SimpleNamespace(**content, authority_certificate_hash="selected-cert")
    refreshed = SimpleNamespace(**content, authority_certificate_hash="fresh-cert")
    current_family = bridge.PreparedGlobalFamily(
        decision_id="fresh-decision",
        probability_witness=refreshed,
        candidate_seeds=(),
    )
    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        lambda *_args, **_kwargs: current_family,
    )
    conn = sqlite3.connect(":memory:")
    rebound, current_day0_payload = era._current_global_actuation_prepared_family(
        SimpleNamespace(),
        global_actuation=SimpleNamespace(probability_witness=selected),
        forecast_conn=conn,
        topology_conn=conn,
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    )
    assert rebound.probability_witness is selected
    assert rebound.decision_id == "fresh-decision"
    assert current_day0_payload == {}

    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        lambda *_args, **_kwargs: replace(
            current_family,
            probability_witness=SimpleNamespace(**{**content, "q_version": "moved"}),
        ),
    )
    with pytest.raises(ValueError, match="GLOBAL_ACTUATION_PROBABILITY_SUPERSEDED"):
        era._current_global_actuation_prepared_family(
            SimpleNamespace(),
            global_actuation=SimpleNamespace(probability_witness=selected),
            forecast_conn=conn,
            topology_conn=conn,
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        )
    conn.close()


def test_day0_current_probability_does_not_require_derived_carrier_q_mode():
    from src.events.candidate_binding import weather_family_id

    captured_at = _dt.datetime(2026, 7, 14, 4, 58, tzinfo=_dt.timezone.utc)
    family_key = weather_family_id(
        city="Shenzhen",
        target_date="2026-07-14",
        metric="high",
    )
    event = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Shenzhen|2026-07-14|high|ZGSZ",
        source="global_auction_winner_target:test",
        observed_at="2026-07-14T04:00:00+00:00",
        available_at="2026-07-14T04:01:00+00:00",
        received_at="2026-07-14T04:01:00+00:00",
        causal_snapshot_id="day0-current-probability-test",
        payload={
            "city": "Shenzhen",
            "target_date": "2026-07-14",
            "metric": "high",
        },
    )
    witness = SimpleNamespace(
        family_key=family_key,
        witness_identity="day0-witness",
        q_version="day0-q",
        resolution_identity="day0-resolution",
        topology_identity="day0-topology",
        posterior_identity_hash="day0-posterior",
        source_truth_identity="day0-source-truth-with-remaining-day-mode",
        authority_certificate_hash="day0-certificate",
        band_alpha=0.05,
        band_basis=era._GLOBAL_DAY0_CURRENT_SETTLEMENT_SIMPLEX_BAND_BASIS,
        yes_q_samples=np.asarray(((0.25, 0.75), (0.30, 0.70))),
        captured_at_utc=captured_at,
        max_age=_dt.timedelta(minutes=3),
    )

    conn = sqlite3.connect(":memory:")
    try:
        authority = era.current_global_probability_authority(
            conn,
            event,
            witness,
            decision_time=captured_at + _dt.timedelta(seconds=30),
        )

        assert authority is not None
        assert authority.family_key == family_key
        assert "_edli_day0_q_mode" not in json.loads(event.payload_json)
        wrong_basis = SimpleNamespace(
            **{**vars(witness), "band_basis": "wrong-basis"}
        )
        assert era.current_global_probability_authority(
            conn,
            event,
            wrong_basis,
            decision_time=captured_at + _dt.timedelta(seconds=30),
        ) is None
        assert era.current_global_probability_authority(
            conn,
            event,
            witness,
            decision_time=captured_at + _dt.timedelta(minutes=4),
        ) is None
    finally:
        conn.close()


def _stale_day0_carrier_and_current_observations():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT,
            target_date TEXT,
            source TEXT,
            station_id TEXT,
            local_timestamp TEXT,
            utc_timestamp TEXT,
            imported_at TEXT,
            temp_unit TEXT,
            running_max REAL,
            running_min REAL,
            authority TEXT,
            training_allowed INTEGER,
            causality_status TEXT,
            source_role TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            (
                "Moscow", "2026-07-10", "ogimet_metar_uuww", "UUWW",
                "2026-07-10T16:00:00+03:00", "2026-07-10T13:00:00+00:00",
                "2026-07-10T13:05:00+00:00", "C", 27.0, 27.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
            (
                "Moscow", "2026-07-10", "ogimet_metar_uuww", "UUWW",
                "2026-07-10T22:00:00+03:00", "2026-07-10T19:00:00+00:00",
                "2026-07-10T19:05:00+00:00", "C", 19.0, 19.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
            (
                "Moscow", "2026-07-10", "ogimet_metar_uuww", "UUWW",
                "2026-07-10T23:00:00+03:00", "2026-07-10T20:00:00+00:00",
                "2026-07-10T20:30:00+00:00", "C", 18.0, 18.0,
                "VERIFIED", 1, "OK", "historical_hourly",
            ),
        ),
    )
    carrier_payload = {
        "city": "Moscow",
        "target_date": "2026-07-10",
        "metric": "high",
        "station_id": "UUWW",
        "settlement_source": "ogimet_metar_uuww",
        "settlement_unit": "C",
        "observation_time": "2026-07-10T13:00:00+00:00",
        "observation_available_at": "2026-07-10T13:05:00+00:00",
        "raw_value": 27.0,
        "rounded_value": 27,
        "high_so_far": 27.0,
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    carrier = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Moscow|2026-07-10|high|UUWW",
        source="global_auction_winner_target:old-carrier",
        observed_at="2026-07-10T13:00:00+00:00",
        available_at="2026-07-10T13:05:00+00:00",
        received_at="2026-07-10T13:05:00+00:00",
        payload=carrier_payload,
        causal_snapshot_id="old-day0-carrier",
    )
    return conn, carrier


def test_global_day0_actuation_rebinds_stale_carrier_to_current_conditioning():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    conditioning = {
        "active": True,
        "metric": "high",
        "observation_time": "2026-07-10T19:00:00+00:00",
        "observed_extreme_c": 27.0,
        "sample_count": 2,
        "source": "durable_observation_instants",
        "unit": "C",
    }
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
        resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
        conditioning=conditioning,
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        posterior_id=29914,
    )
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_MISMATCH",
    ):
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(
                city="Moscow", target_date="2026-07-10", metric="high"
            ),
            resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T19:00:00+00:00",
                "observed_extreme_c": 26.0,
                "sample_count": 1,
                "source": "durable_day0_event:ogimet_metar_uuww",
                "unit": "C",
            },
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
            posterior_id=29914,
        )
    conn.close()

    assert json.loads(carrier.payload_json)["observation_time"] == "2026-07-10T13:00:00+00:00"
    assert rebound["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert rebound["high_so_far"] == 27.0
    assert rebound["sample_count"] == 2
    assert rebound["station_id"] == "UUWW"
    assert rebound["settlement_source"] == "ogimet_metar_uuww"
    assert rebound["_edli_global_day0_binding"]["posterior_id"] == 29914


def test_global_day0_actuation_can_bind_current_remaining_day_base_directly():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
        resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
        conditioning=None,
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        posterior_id=None,
        probability_base_identity="current-base-snapshot-1",
    )
    conn.close()

    binding = rebound["_edli_global_day0_binding"]
    assert binding["probability_base_identity"] == "current-base-snapshot-1"
    assert "posterior_id" not in binding
    authority = era._global_day0_probability_authority_payload(rebound)
    assert authority["probability_base_identity"] == "current-base-snapshot-1"
    assert "posterior_id" not in authority
    assert authority["q_source"] == "day0_remaining_day"


def test_global_day0_actuation_compares_physical_state_not_carrier_provenance():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
        resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
        conditioning={
            "active": True,
            "metric": "high",
            "observation_time": "2026-07-10T19:00:00+00:00",
            "observed_extreme_c": 27.0,
            "sample_count": 1,
            "source": "durable_day0_event:ogimet_metar_uuww",
            "unit": "C",
        },
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        posterior_id=29914,
    )
    conn.close()

    assert rebound["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert rebound["high_so_far"] == 27.0
    assert rebound["sample_count"] == 2
    assert rebound["settlement_source"] == "ogimet_metar_uuww"


def test_global_day0_authority_uses_current_possession_clock_not_stale_carrier_clock():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    decision_time = _dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc)
    payload = json.loads(carrier.payload_json)
    payload.update(
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
            resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T19:00:00+00:00",
                "observed_extreme_c": 27.0,
                "sample_count": 2,
                "source": "durable_observation_instants",
                "unit": "C",
            },
            observation_conn=conn,
            decision_time=decision_time,
            posterior_id=29914,
        )
    )
    conn.close()
    old_source_time = _dt.datetime(2026, 7, 10, 13, 0, tzinfo=_dt.timezone.utc)
    old_received_time = _dt.datetime(2026, 7, 10, 13, 5, tzinfo=_dt.timezone.utc)

    def base_cert(certificate_type, cert_payload=None):
        return build_certificate(
            certificate_type=certificate_type,
            semantic_key=f"fixture:{certificate_type}",
            claim_type=certificate_type,
            mode="LIVE",
            decision_time=decision_time,
            source_available_at=old_source_time,
            agent_received_at=old_received_time,
            persisted_at=old_received_time,
            payload=dict(cert_payload or {}),
            authority_id="fixture",
            authority_version="v1",
            algorithm_id="fixture",
            algorithm_version="v1",
        )

    parents = (
        base_cert(claims.CLOCK_MODE),
        base_cert(claims.CAUSAL_EVENT),
        base_cert(claims.SOURCE_TRUTH),
        base_cert(claims.FAMILY_CLOSURE, {"family_id": "Moscow|2026-07-10|high"}),
        base_cert(claims.BELIEF),
    )
    certs = era._day0_live_source_parent_certificates(
        event=carrier,
        payload=payload,
        base_certs=parents,
        decision_time=decision_time,
    )
    authority = next(
        cert for cert in certs if cert.certificate_type == claims.DAY0_AUTHORITY
    )

    assert authority.payload["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert authority.header.source_available_at == _dt.datetime(
        2026, 7, 10, 19, 5, tzinfo=_dt.timezone.utc
    )
    assert authority.header.agent_received_at == decision_time
    assert authority.header.persisted_at == decision_time
    assert (
        authority.header.source_available_at
        <= authority.header.agent_received_at
        <= authority.header.persisted_at
        <= authority.header.decision_time
    )


def test_global_day0_actuation_binds_native_fahrenheit_to_conditioned_celsius():
    conn, old_carrier = _stale_day0_carrier_and_current_observations()
    conn.execute(
        """
        UPDATE observation_instants
           SET city='NYC', source='wu_icao_history', station_id='KLGA', temp_unit='F',
               running_max=CASE utc_timestamp
                   WHEN '2026-07-10T13:00:00+00:00' THEN 80.6
                   WHEN '2026-07-10T19:00:00+00:00' THEN 66.0
                   ELSE 64.0
               END
        """
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "NYC", "2026-07-10", "wu_icao_history_kjfk", "KJFK",
            "2026-07-10T15:30:00-04:00", "2026-07-10T19:30:00+00:00",
            "2026-07-10T19:35:00+00:00", "F", 75.0, 70.0,
            "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )
    conn.execute(
        "INSERT INTO observation_instants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "NYC", "2026-07-10", "wu_icao_history", "KLGA",
            "2026-07-10T15:45:00-04:00", "2026-07-10T19:45:00+00:00",
            "2026-07-10T19:50:00+00:00", "C", 25.0, 20.0,
            "VERIFIED", 1, "OK", "historical_hourly",
        ),
    )
    carrier_payload = {
        **json.loads(old_carrier.payload_json),
        "city": "NYC",
        "station_id": "KLGA",
        "settlement_source": "aviationweather_metar",
        "settlement_unit": "F",
        "raw_value": 80.6,
        "rounded_value": 81,
        "high_so_far": 80.6,
    }
    carrier = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="NYC|2026-07-10|high|KLGA",
        source="global_auction_winner_target:old-nyc-carrier",
        observed_at="2026-07-10T13:00:00+00:00",
        available_at="2026-07-10T13:05:00+00:00",
        received_at="2026-07-10T13:05:00+00:00",
        payload=carrier_payload,
        causal_snapshot_id="old-nyc-day0-carrier",
    )
    rebound = era._global_day0_execution_payload(
        carrier,
        family=SimpleNamespace(city="NYC", target_date="2026-07-10", metric="high"),
        resolution=SimpleNamespace(measurement_unit="F", station_id="KLGA"),
        conditioning={
            "active": True,
            "metric": "high",
            "observation_time": "2026-07-10T19:00:00+00:00",
            "observed_extreme_c": 27.0,
            "sample_count": 2,
            "source": "durable_observation_instants",
            "unit": "F",
        },
        observation_conn=conn,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
        posterior_id=29915,
    )
    assert rebound["high_so_far"] == pytest.approx(80.6)
    assert rebound["observation_time"] == "2026-07-10T19:00:00+00:00"
    assert rebound["sample_count"] == 2
    assert rebound["settlement_unit"] == "F"
    assert rebound["rounded_value"] == 81
    assert rebound["station_id"] == "KLGA"
    assert rebound["settlement_source"] == "wu_icao_history"
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH",
    ):
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(city="NYC", target_date="2026-07-10", metric="high"),
            resolution=SimpleNamespace(measurement_unit="F", station_id="KLGA"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T19:30:00+00:00",
                "observed_extreme_c": 27.0,
                "sample_count": 3,
                "source": "durable_observation_instants",
                "unit": "F",
            },
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
            posterior_id=29915,
        )
    conn.close()


def test_global_day0_actuation_rejects_conditioning_not_equal_to_current_state():
    conn, carrier = _stale_day0_carrier_and_current_observations()
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_CONDITIONING_OBSERVATION_TIME_MISMATCH",
    ):
        era._global_day0_execution_payload(
            carrier,
            family=SimpleNamespace(city="Moscow", target_date="2026-07-10", metric="high"),
            resolution=SimpleNamespace(measurement_unit="C", station_id="UUWW"),
            conditioning={
                "active": True,
                "metric": "high",
                "observation_time": "2026-07-10T13:00:00+00:00",
                "observed_extreme_c": 27.0,
                "sample_count": 2,
                "source": "durable_observation_instants",
                "unit": "C",
            },
            observation_conn=conn,
            decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
            posterior_id=29914,
        )
    conn.close()


def test_global_day0_observation_unknown_source_type_fails_closed(monkeypatch):
    from src.data.replacement_forecast_current_target_plan import (
        _latest_authorized_day0_fact,
    )

    conn, _carrier = _stale_day0_carrier_and_current_observations()
    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "Moscow": SimpleNamespace(
                settlement_source_type="unknown",
                settlement_unit="C",
                wu_station="UUWW",
            )
        },
    )
    assert _latest_authorized_day0_fact(
        conn,
        city="Moscow",
        target_date="2026-07-10",
        temperature_metric="high",
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    ) is None
    conn.close()


def test_global_day0_uses_remaining_day_probability_builder(monkeypatch):
    expected = ({"condition": 0.5}, {}, {}, {}, {"probability_authority": "day0_remaining_day"})
    monkeypatch.setattr(
        "src.data.day0_oracle_anomaly.is_day0_family_paused",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        era,
        "_replacement_authority_probability_and_fdr_proof",
        lambda **_kwargs: pytest.fail("global Day0 must not use full-day replacement q"),
    )
    monkeypatch.setattr(
        era,
        "_canonical_probability_and_fdr_proof",
        lambda **_kwargs: expected,
    )
    monkeypatch.setattr(
        era,
        "_apply_day0_mask_to_generated_probabilities",
        lambda **kwargs: (kwargs["q_by_condition"], kwargs["lcb_by_condition"]),
    )
    conn = sqlite3.connect(":memory:")
    result = era._live_yes_probabilities(
        event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        payload={"_edli_global_auction_prepare": True},
        family=SimpleNamespace(
            city="Moscow", target_date="2026-07-10", metric="high", candidates=()
        ),
        conn=conn,
        calibration_conn=conn,
        native_costs={},
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    )
    conn.close()
    assert result[0] == expected[0]
    assert result[1] == expected[1]
    assert result[2] == {}
    assert result[3] == {}
    assert result[4]["probability_authority"] == "day0_absorbing_hard_fact"


def test_global_day0_joint_witness_uses_one_remaining_day_simplex(monkeypatch):
    matrix = np.asarray([[0.0, 1.0] for _ in range(100)], dtype=float)
    bins = (
        Bin(low=22.0, high=22.0, unit="C", label="22C"),
        Bin(low=23.0, high=None, unit="C", label="23C or above"),
    )
    analysis = SimpleNamespace(
        p_posterior=np.asarray([0.0, 1.0], dtype=float),
        forecast_yes_probability_sample_matrix=lambda _n: matrix,
        _member_maxes=np.asarray([25.0, 25.0], dtype=float),
        _settle=lambda values: values,
        bins=bins,
    )
    monkeypatch.setattr(
        era,
        "_forecast_snapshot_row_for_event",
        lambda *_args, **_kwargs: {"members_json": "[20,21,22]"},
    )
    monkeypatch.setattr(era, "_day0_seed_members_multimodel", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        era,
        "_market_analysis_from_event_snapshot",
        lambda **_kwargs: analysis,
    )

    family = SimpleNamespace(
        candidates=(
            SimpleNamespace(condition_id="condition-22", bin=bins[0]),
            SimpleNamespace(condition_id="condition-23-plus", bin=bins[1]),
        )
    )
    payload = {"metric": "high", "rounded_value": 21.0}
    samples, point, basis = era._day0_remaining_global_probability_components(
        SimpleNamespace(),
        forecast_conn=sqlite3.connect(":memory:"),
        calibration_conn=sqlite3.connect(":memory:"),
        family=family,
        payload=payload,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    )

    zero_hit_ucb = 1.0 - 0.05 ** (1.0 / 2.0)
    assert np.percentile(samples[:, 0], 95.0) >= zero_hit_ucb - 1e-15
    assert point.tolist() == pytest.approx([0.0, 1.0])
    assert basis == "current_coherent_day0_remaining_finite_evidence_v2"
    assert np.allclose(samples.sum(axis=1), 1.0)
    assert payload["_edli_day0_finite_evidence_member_count"] == 2
    assert payload["_edli_day0_finite_evidence_hits_by_condition"] == {
        "condition-22": 0,
        "condition-23-plus": 2,
    }
    assert payload["_edli_day0_finite_evidence_absorbing_no_conditions"] == []
    hard_fact_payload = {"metric": "high", "rounded_value": 23.0}
    hard_fact_floors = era._day0_current_evidence_yes_ucb_floors(
        analysis=analysis,
        family=family,
        payload=hard_fact_payload,
    )
    assert hard_fact_floors[0] == 0.0
    assert hard_fact_payload[
        "_edli_day0_finite_evidence_absorbing_no_conditions"
    ] == ["condition-22"]
    with pytest.raises(
        ValueError,
        match="GLOBAL_DAY0_FINITE_EVIDENCE_ABSORBING_MASK_MISMATCH",
    ):
        era._day0_current_evidence_yes_ucb_floors(
            analysis=analysis,
            family=family,
            payload={
                "metric": "high",
                "rounded_value": 23.0,
                "_edli_day0_lcb_transform": {
                    "absorbing_no_conditions": [],
                },
            },
        )


def test_global_day0_components_never_parse_full_day_members_when_remaining_vectors_exist(
    monkeypatch,
):
    bins = (
        Bin(low=22.0, high=22.0, unit="C", label="22C"),
        Bin(low=23.0, high=None, unit="C", label="23C or above"),
    )
    family = SimpleNamespace(
        family_id="Moscow|2026-07-10|high",
        city="Moscow",
        target_date="2026-07-10",
        metric="high",
        bins=bins,
        candidates=(
            SimpleNamespace(condition_id="condition-22", bin=bins[0]),
            SimpleNamespace(condition_id="condition-23-plus", bin=bins[1]),
        ),
    )
    invalid_full_day_snapshot = {
        "snapshot_id": "full-day-boundary-ambiguous",
        "source_cycle_time": "2026-07-10T12:00:00+00:00",
        "available_at": "2026-07-10T12:30:00+00:00",
        "settlement_unit": "C",
        "temperature_metric": "high",
        "members_json": "[null, null, null]",
        "members_precision": 1.0,
    }
    monkeypatch.setattr(
        era,
        "_forecast_snapshot_row_for_event",
        lambda *_args, **_kwargs: invalid_full_day_snapshot,
    )
    monkeypatch.setattr(
        era,
        "_day0_seed_members_multimodel",
        lambda *_args, **_kwargs: None,
    )

    def remaining_members(*, payload, **_kwargs):
        payload["_edli_day0_remaining_models"] = 3
        payload["_edli_day0_remaining_model_names"] = ["ecmwf", "gfs", "ukmo"]
        payload["_edli_day0_remaining_capture_times_utc"] = [
            "2026-07-10T19:30:00+00:00"
        ]
        return np.asarray([24.0, 25.0, 26.0], dtype=float)

    monkeypatch.setattr(era, "_day0_remaining_day_members", remaining_members)
    payload = {
        "event_type": "DAY0_EXTREME_UPDATED",
        "city": "Moscow",
        "target_date": "2026-07-10",
        "metric": "high",
        "rounded_value": 23,
        "high_so_far": 23.0,
        "observation_time": "2026-07-10T19:00:00+00:00",
        "observation_available_at": "2026-07-10T19:05:00+00:00",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    forecast = sqlite3.connect(":memory:")
    calibration = sqlite3.connect(":memory:")
    samples, point, basis = era._day0_remaining_global_probability_components(
        SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        forecast_conn=forecast,
        calibration_conn=calibration,
        family=family,
        payload=payload,
        decision_time=_dt.datetime(2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc),
    )

    assert samples.shape[1] == 2
    assert np.allclose(samples.sum(axis=1), 1.0)
    assert point.tolist() == pytest.approx([0.0, 1.0])
    assert basis == "current_coherent_day0_remaining_finite_evidence_v2"
    assert payload["_edli_q_source"] == "day0_remaining_day"
    assert payload["_edli_day0_finite_evidence_member_count"] == 3

    monkeypatch.setattr(
        era,
        "_day0_remaining_day_members",
        lambda **_kwargs: None,
    )
    with pytest.raises(ValueError, match="DAY0_REMAINING_DAY_MEMBERS_UNAVAILABLE"):
        era._day0_remaining_global_probability_components(
            SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            forecast_conn=forecast,
            calibration_conn=calibration,
            family=family,
            payload={
                key: value
                for key, value in payload.items()
                if not key.startswith("_edli_")
            },
            decision_time=_dt.datetime(
                2026, 7, 10, 20, 0, tzinfo=_dt.timezone.utc
            ),
        )
    forecast.close()
    calibration.close()


def test_global_day0_current_band_accepts_only_bound_absorbing_certainty():
    sample_hash = "day0-current-simplex"
    economics = {
        "source": "qkernel_spine",
        "decision_id": "decision-1",
        "receipt_hash": "receipt-1",
        "q_version": "q-version-1",
        "sample_hash": sample_hash,
        "side": "NO",
        "payoff_q_point": 1.0,
        "payoff_q_lcb": 1.0,
        "q_lcb_guard_basis": "CURRENT_POSTERIOR_BAND",
        "q_lcb_guard_abstained": False,
        "q_lcb_guard_cell_key": sample_hash,
        "selection_guard_basis": "CURRENT_POSTERIOR_BAND",
        "selection_guard_abstained": False,
        "selection_guard_cell_key": sample_hash,
        "selection_guard_n": 100,
        "selection_guard_q_safe": 1.0,
    }
    economics["current_state_identity_hash"] = qkernel_current_state_identity_hash(
        economics
    )
    payload = {
        "condition_id": "condition-dead",
        "direction": "buy_no",
        "_edli_q_source": "day0_remaining_day",
        "_edli_day0_q_mode": "remaining_day",
        "_edli_day0_remaining_models": 2,
        "rounded_value": 28,
        "observation_time": "2026-07-14T16:00:00+00:00",
        "_edli_day0_lcb_transform": {
            "yes_lcb_by_condition": {"condition-dead": 0.0},
            "no_lcb_by_condition": {"condition-dead": 1.0},
            "absorbing_yes_conditions": [],
            "absorbing_no_conditions": ["condition-dead"],
        },
        "_edli_day0_finite_evidence_absorbing_no_conditions": [
            "condition-dead"
        ],
        "q_live": 1.0,
        "q_lcb_5pct": 1.0,
        "qkernel_execution_economics": economics,
    }

    assert_live_day0_probability_authority(
        payload,
        direction="buy_no",
        condition_id="condition-dead",
        q_live=1.0,
        q_lcb=1.0,
    )
    assert_live_day0_qkernel_guard_authority(
        economics,
        probability_payload=payload,
    )

    payload["_edli_day0_finite_evidence_absorbing_no_conditions"] = []
    with pytest.raises(ValueError, match="degenerate with q_live"):
        assert_live_day0_probability_authority(
            payload,
            direction="buy_no",
            condition_id="condition-dead",
            q_live=1.0,
            q_lcb=1.0,
        )


def _global_scope_event(
    *,
    city: str,
    source_run_id: str,
    city_timezone: str = "UTC",
):
    captured_at = "2026-07-10T08:00:00+00:00"
    payload = ForecastSnapshotReadyPayload(
        city=city,
        target_date="2026-07-11",
        metric="high",
        source_id="replacement_0_1",
        source_run_id=source_run_id,
        cycle="2026-07-10T00:00:00+00:00",
        track="replacement_0_1_openmeteo_bayes_fusion",
        snapshot_id=f"rmf-{city}|2026-07-11|high|2026-07-10",
        snapshot_hash=source_run_id,
        captured_at=captured_at,
        available_at=captured_at,
        required_fields_present=True,
        required_steps_present=True,
        member_count=3,
        min_members_floor=3,
        completeness_status="COMPLETE",
        required_steps=[],
        observed_steps=[],
        expected_members=3,
        source_run_status="COMPLETE",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )
    payload_json = asdict(payload)
    payload_json["city_timezone"] = city_timezone
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|2026-07-11|high",
        source="global-auction-current-scope",
        observed_at=captured_at,
        available_at=captured_at,
        received_at=captured_at,
        payload=payload_json,
        causal_snapshot_id=payload.snapshot_id,
    )


def _global_day0_scope_event(*, city: str, source_run_id: str):
    forecast = _global_scope_event(city=city, source_run_id=source_run_id)
    payload = json.loads(forecast.payload_json)
    payload.update(
        {
            "station_id": "KDFW",
            "settlement_source": "wu_icao_history",
            "settlement_unit": "F",
            "observation_time": "2026-07-11T17:00:00+00:00",
            "observation_available_at": "2026-07-11T17:05:00+00:00",
            "raw_value": 72.0,
            "rounded_value": 72,
            "high_so_far": 72.0,
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
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"{city}|2026-07-11|high|KDFW",
        source="global-auction-current-day0-scope",
        observed_at="2026-07-11T17:00:00+00:00",
        available_at="2026-07-11T17:05:00+00:00",
        received_at="2026-07-11T17:05:00+00:00",
        payload=payload,
        causal_snapshot_id=str(payload["snapshot_id"]),
    )


@pytest.mark.parametrize(
    "bootstrap_basis",
    (
        "global_simplex_v1",
        "global_simplex_current_finite_moment_evidence_v3",
    ),
)
def test_current_global_probability_prepare_does_not_require_price_snapshot(
    monkeypatch,
    bootstrap_basis,
):
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.replacement_forecast_hook_factory as hook_factory

    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.execute(
        """
        CREATE TABLE forecast_posteriors (
            posterior_id INTEGER PRIMARY KEY,
            posterior_identity_hash TEXT NOT NULL,
            dependency_hash TEXT NOT NULL,
            posterior_config_hash TEXT NOT NULL
        )
        """
    )
    forecast.execute(
        "INSERT INTO forecast_posteriors VALUES "
        "(1, 'db-posterior', 'db-dependency', 'db-config')"
    )
    forecast.execute(
        """
        CREATE TABLE market_events (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ("Dallas", "2026-07-11", "high", "c0", "yes0", "dallas-69-or-below", "69F or below", None, 69.0),
            ("Dallas", "2026-07-11", "high", "c1", "yes1", "dallas-70-71", "70-71F", 70.0, 71.0),
            ("Dallas", "2026-07-11", "high", "c2", "yes2", "dallas-72-or-above", "72F or above", 72.0, None),
        ),
    )
    posterior_bins = (
        ("p0", None, (69.0 - 32.0) * 5.0 / 9.0),
        ("p1", (70.0 - 32.0) * 5.0 / 9.0, (71.0 - 32.0) * 5.0 / 9.0),
        ("p2", (72.0 - 32.0) * 5.0 / 9.0, None),
    )
    probabilities = (0.2, 0.3, 0.5)
    bundle = SimpleNamespace(
        posterior_id=1,
        posterior_identity_hash="posterior-1",
        dependency_hash="dependency-1",
        posterior_config_hash="config-1",
        q={key: probability for (key, _lo, _hi), probability in zip(posterior_bins, probabilities)},
        provenance_json={
            "q_bootstrap_samples_basis": bootstrap_basis,
            "q_bootstrap_samples_by_bin": {
                key: [probability] * 400
                for (key, _lo, _hi), probability in zip(posterior_bins, probabilities)
            },
            "bin_topology": [
                {"bin_id": key, "lower_c": lower, "upper_c": upper}
                for key, lower, upper in posterior_bins
            ],
        },
        source_cycle_time="2026-07-10T00:00:00+00:00",
        source_available_at="2026-07-10T06:00:00+00:00",
    )
    fresh_probabilities = (0.25, 0.25, 0.5)
    fresh_bundle = SimpleNamespace(
        posterior_id=2,
        posterior_identity_hash="posterior-2",
        dependency_hash="dependency-2",
        posterior_config_hash="config-1",
        q={
            key: probability
            for (key, _lo, _hi), probability in zip(
                posterior_bins, fresh_probabilities
            )
        },
        provenance_json={
            "q_bootstrap_samples_basis": bootstrap_basis,
            "q_bootstrap_samples_by_bin": {
                key: [probability] * 400
                for (key, _lo, _hi), probability in zip(
                    posterior_bins, fresh_probabilities
                )
            },
            "bin_topology": [
                {"bin_id": key, "lower_c": lower, "upper_c": upper}
                for key, lower, upper in posterior_bins
            ],
        },
        source_cycle_time="2026-07-10T00:00:00+00:00",
        source_available_at="2026-07-10T06:05:00+00:00",
    )
    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
        lambda *args, **kwargs: object(),
    )
    bundle_read: dict[str, object] = {}
    bundle_read_count = 0

    def read_bundle(*args, **kwargs):
        nonlocal bundle_read_count
        bundle_read_count += 1
        bundle_read.update(kwargs)
        return SimpleNamespace(
            ok=True,
            bundle=bundle if bundle_read_count == 1 else fresh_bundle,
            reason_code="READY",
        )

    monkeypatch.setattr(bundle_reader, "read_replacement_forecast_bundle", read_bundle)

    traced: list[str] = []
    forecast.set_trace_callback(traced.append)
    event = _global_scope_event(city="Dallas", source_run_id="run-dallas")
    first_cut = _dt.datetime(
        2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc
    )
    prepared = era._prepare_current_global_probability_family(
        event,
        forecast_conn=forecast,
        topology_conn=forecast,
        decision_time=first_cut,
        max_age=_dt.timedelta(seconds=30),
    )
    forecast.set_trace_callback(None)
    second_cut = first_cut + _dt.timedelta(seconds=1)
    refreshed = era._prepare_current_global_probability_family(
        event,
        forecast_conn=forecast,
        topology_conn=forecast,
        decision_time=second_cut,
        max_age=_dt.timedelta(seconds=20),
    )

    witness = prepared.probability_witness
    assert prepared.candidate_seeds == ()
    assert witness.yes_q_samples.shape == (400, 3)
    assert witness.band_alpha == pytest.approx(0.05)
    assert witness.band_basis == "current_coherent_settlement_simplex_v1"
    assert bundle.provenance_json["q_bootstrap_samples_basis"] == bootstrap_basis
    assert [binding.yes_token_id for binding in witness.bindings] == ["yes0", "yes1", "yes2"]
    assert all(binding.no_token_id is None for binding in witness.bindings)
    assert witness.yes_q_samples[0].tolist() == pytest.approx(list(probabilities))
    assert (1.0 - witness.yes_q_samples[:, 1]).tolist() == pytest.approx([0.7] * 400)
    assert witness.posterior_identity_hash == "posterior-1"
    assert len(str(bundle_read["current_bin_topology_hash"])) == 64
    assert sum("FROM MARKET_EVENTS" in statement.upper() for statement in traced) == 1
    assert not any(
        "SELECT POSTERIOR_IDENTITY_HASH, DEPENDENCY_HASH, POSTERIOR_CONFIG_HASH"
        in statement.upper()
        for statement in traced
    )
    assert bundle_read_count == 2
    refreshed_witness = refreshed.probability_witness
    assert refreshed_witness.yes_q_samples[0].tolist() == pytest.approx(
        list(fresh_probabilities)
    )
    assert refreshed_witness.posterior_identity_hash == "posterior-2"
    assert refreshed_witness.q_version != witness.q_version
    assert refreshed_witness.captured_at_utc == second_cut
    assert refreshed_witness.max_age == _dt.timedelta(seconds=20)
    assert (
        refreshed_witness.authority_certificate_hash
        != witness.authority_certificate_hash
    )
    assert refreshed_witness.witness_identity != witness.witness_identity


def test_current_day0_global_probability_uses_current_remaining_day_not_full_day_bundle(
    monkeypatch,
):
    import src.data.replacement_forecast_bundle_reader as bundle_reader
    import src.engine.replacement_forecast_hook_factory as hook_factory

    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.execute(
        """
        CREATE TABLE market_events (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                "Dallas",
                "2026-07-11",
                "high",
                "c0",
                "yes0",
                "dallas-69-or-below",
                "69F or below",
                None,
                69.0,
            ),
            (
                "Dallas",
                "2026-07-11",
                "high",
                "c1",
                "yes1",
                "dallas-70-71",
                "70-71F",
                70.0,
                71.0,
            ),
            (
                "Dallas",
                "2026-07-11",
                "high",
                "c2",
                "yes2",
                "dallas-72-or-above",
                "72F or above",
                72.0,
                None,
            ),
        ),
    )
    observations = sqlite3.connect(":memory:")
    observations.execute("CREATE TABLE observation_instants (marker INTEGER)")

    def replacement_readiness_must_not_run(*_args, **_kwargs):
        raise AssertionError("Day0 must not read full-day replacement readiness")

    def replacement_bundle_must_not_run(*_args, **_kwargs):
        raise AssertionError("Day0 must not read a full-day replacement bundle")

    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
        replacement_readiness_must_not_run,
    )
    monkeypatch.setattr(
        bundle_reader,
        "read_replacement_forecast_bundle",
        replacement_bundle_must_not_run,
    )
    monkeypatch.setattr(
        era,
        "_forecast_snapshot_row_for_event",
        lambda *_args, **_kwargs: {
            "snapshot_id": "day0-current-base-1",
            "source_cycle_time": "2026-07-11T12:00:00+00:00",
            "available_at": "2026-07-11T12:30:00+00:00",
        },
    )

    def current_observation_payload(*_args, **kwargs):
        base_identity = kwargs["probability_base_identity"]
        return {
            "observation_time": "2026-07-11T17:00:00+00:00",
            "observation_available_at": "2026-07-11T17:05:00+00:00",
            "raw_value": 72.0,
            "rounded_value": 72,
            "high_so_far": 72.0,
            "sample_count": 5,
            "station_id": "KDFW",
            "settlement_source": "wu_icao_history",
            "settlement_unit": "F",
            "source_match_status": "MATCH",
            "local_date_status": "MATCH",
            "station_match_status": "MATCH",
            "dst_status": "UNAMBIGUOUS",
            "metric_match_status": "MATCH",
            "rounding_status": "MATCH",
            "source_authorized_status": "AUTHORIZED",
            "live_authority_status": "live",
            "_edli_global_day0_binding": {
                "city": "Dallas",
                "target_date": "2026-07-11",
                "metric": "high",
                "probability_base_identity": base_identity,
            },
        }

    monkeypatch.setattr(
        era,
        "_global_day0_execution_payload",
        current_observation_payload,
    )

    def remaining_day_components(*_args, **kwargs):
        payload = kwargs["payload"]
        payload.update(
            {
                "_edli_day0_q_mode": "remaining_day",
                "_edli_day0_remaining_model_names": ["ecmwf", "gfs", "ukmo"],
                "_edli_day0_remaining_capture_times_utc": [
                    "2026-07-11T17:30:00+00:00"
                ],
                "_edli_day0_finite_evidence_member_count": 3,
                "_edli_day0_finite_evidence_hits_by_condition": {
                    "c0": 0,
                    "c1": 0,
                    "c2": 3,
                },
                "_edli_day0_finite_evidence_yes_ucb_by_condition": {
                    "c0": 0.1,
                    "c1": 0.1,
                    "c2": 1.0,
                },
                "_edli_day0_finite_evidence_absorbing_no_conditions": [
                    "c0",
                    "c1",
                ],
            }
        )
        matrix = np.asarray([[0.0, 0.0, 1.0]] * 400, dtype=float)
        return (
            matrix,
            np.asarray([0.0, 0.0, 1.0], dtype=float),
            "current_coherent_day0_remaining_finite_evidence_v2",
        )

    monkeypatch.setattr(
        era,
        "_day0_remaining_global_probability_components",
        remaining_day_components,
    )
    day0_payload: dict[str, object] = {}
    prepared = era._prepare_current_global_probability_family(
        _global_day0_scope_event(city="Dallas", source_run_id="run-dallas"),
        forecast_conn=forecast,
        topology_conn=forecast,
        observation_conn=observations,
        decision_time=_dt.datetime(2026, 7, 11, 18, 0, tzinfo=_dt.timezone.utc),
        max_age=_dt.timedelta(seconds=30),
        day0_payload_out=day0_payload,
    )

    witness = prepared.probability_witness
    binding = day0_payload["_edli_global_day0_binding"]
    assert witness.band_alpha == pytest.approx(0.05)
    assert witness.band_basis == "current_coherent_day0_remaining_finite_evidence_v2"
    assert witness.yes_q_samples.shape == (400, 3)
    assert witness.posterior_identity_hash
    assert binding["probability_base_identity"]
    assert "posterior_id" not in binding

    missing_observations = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="GLOBAL_DAY0_OBSERVATION_HWM_UNAVAILABLE"):
        era._prepare_current_global_probability_family(
            _global_day0_scope_event(city="Dallas", source_run_id="run-dallas"),
            forecast_conn=forecast,
            topology_conn=forecast,
            observation_conn=missing_observations,
            decision_time=_dt.datetime(
                2026, 7, 11, 18, 0, tzinfo=_dt.timezone.utc
            ),
            max_age=_dt.timedelta(seconds=30),
        )
    missing_observations.close()
    observations.close()
    forecast.close()


def test_post_day_final_daily_observation_builds_exact_complete_global_simplex(
    monkeypatch,
):
    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.execute(
        """
        CREATE TABLE market_events (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            market_slug TEXT,
            range_label TEXT,
            range_low REAL,
            range_high REAL
        )
        """
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ("Hong Kong", "2026-07-11", "high", "c0", "yes0", "a", "28C or below", None, 28.0),
            ("Hong Kong", "2026-07-11", "high", "c1", "yes1", "b", "29C", 29.0, 29.0),
            ("Hong Kong", "2026-07-11", "high", "c2", "yes2", "c", "30C or above", 30.0, None),
        ),
    )
    forecast.execute(
        """
        CREATE TABLE observations (
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            source TEXT NOT NULL,
            station_id TEXT,
            authority TEXT,
            unit TEXT,
            high_temp REAL,
            low_temp REAL,
            fetched_at TEXT
        )
        """
    )
    forecast.execute(
        "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "Hong Kong",
            "2026-07-11",
            "hko_daily_api",
            "HKO",
            "VERIFIED",
            "C",
            29.8,
            26.0,
            "2026-07-12T06:00:00+00:00",
        ),
    )
    monkeypatch.setattr(
        era,
        "_forecast_snapshot_row_for_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("final observation must not require a forecast snapshot")
        ),
    )
    monkeypatch.setattr(
        era,
        "_day0_remaining_global_probability_components",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("final observation must not request remaining hours")
        ),
    )

    day0_payload: dict[str, object] = {}
    event = _global_day0_scope_event(
        city="Hong Kong", source_run_id="run-hong-kong"
    )
    decision_time = _dt.datetime(2026, 7, 12, 12, 0, tzinfo=_dt.timezone.utc)
    prepared = era._prepare_current_global_probability_family(
        event,
        forecast_conn=forecast,
        topology_conn=forecast,
        observation_conn=sqlite3.connect(":memory:"),
        decision_time=decision_time,
        max_age=_dt.timedelta(seconds=30),
        day0_payload_out=day0_payload,
    )

    witness = prepared.probability_witness
    assert witness.band_basis == (
        "final_daily_observation_exact_settlement_simplex_v1"
    )
    assert witness.yes_q_samples.shape[1] == 3
    assert np.all(witness.yes_q_samples == np.asarray([0.0, 1.0, 0.0]))
    assert day0_payload["probability_authority"] == (
        "final_daily_observation_exact_global_probability_v1"
    )
    assert day0_payload["_edli_global_day0_binding"]["final_daily"] is True
    assert era.current_global_probability_authority(
        forecast,
        event,
        witness,
        decision_time=decision_time,
    ) is not None
    forecast.execute("DELETE FROM observations")
    with pytest.raises(
        ValueError,
        match="POST_LOCAL_DAY_FINAL_OBSERVATION_UNAVAILABLE",
    ):
        era._prepare_current_global_probability_family(
            event,
            forecast_conn=forecast,
            topology_conn=forecast,
            observation_conn=sqlite3.connect(":memory:"),
            decision_time=decision_time,
            max_age=_dt.timedelta(seconds=30),
        )
    forecast.close()


def test_current_forecast_global_probability_still_requires_replacement_readiness(
    monkeypatch,
):
    import src.engine.replacement_forecast_hook_factory as hook_factory

    forecast = sqlite3.connect(":memory:")
    forecast.row_factory = sqlite3.Row
    forecast.execute(
        """
        CREATE TABLE market_events (
            city TEXT, target_date TEXT, temperature_metric TEXT,
            condition_id TEXT, token_id TEXT, market_slug TEXT,
            range_label TEXT, range_low REAL, range_high REAL
        )
        """
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ("Dallas", "2026-07-11", "high", "c0", "yes0", "a", "69F or below", None, 69.0),
            ("Dallas", "2026-07-11", "high", "c1", "yes1", "b", "70-71F", 70.0, 71.0),
            ("Dallas", "2026-07-11", "high", "c2", "yes2", "c", "72F or above", 72.0, None),
        ),
    )
    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_REPLACEMENT_READINESS_MISSING"):
        era._prepare_current_global_probability_family(
            _global_scope_event(city="Dallas", source_run_id="run-dallas"),
            forecast_conn=forecast,
            topology_conn=forecast,
            decision_time=_dt.datetime(
                2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc
            ),
            max_age=_dt.timedelta(seconds=30),
        )
    forecast.close()


@pytest.mark.parametrize(
    "event_factory",
    [_global_scope_event, _global_day0_scope_event],
    ids=["forecast", "day0"],
)
def test_live_adapter_routes_each_global_truth_to_its_owner(monkeypatch, event_factory):
    import src.data.polymarket_client as polymarket_client
    import src.engine.global_auction_universe as universe
    import src.runtime.reactor_wake as reactor_wake

    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    forecast.execute("CREATE TABLE readiness_state (marker TEXT NOT NULL)")
    forecast.execute("INSERT INTO readiness_state VALUES ('fresh-forecast')")
    topology.execute("CREATE TABLE market_events (marker TEXT NOT NULL)")
    topology.execute("INSERT INTO market_events VALUES ('current-topology')")
    world.execute("CREATE TABLE readiness_state (marker TEXT NOT NULL)")
    world.execute("INSERT INTO readiness_state VALUES ('stale-world-shadow')")
    world.execute("CREATE TABLE opportunity_events (marker TEXT NOT NULL)")
    world.execute("INSERT INTO opportunity_events VALUES ('authorized-day0')")
    captured = {}
    prepared_with = []
    capacity_calls = []
    urgent_revision = {"value": (1, 2, 3)}
    urgent_reason = {"value": "day0_extreme_event_committed"}
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_revision",
        lambda: urgent_revision["value"],
    )
    monkeypatch.setattr(
        reactor_wake,
        "reactor_urgent_wake_reason",
        lambda: urgent_reason["value"],
    )

    class CapacityAuthority:
        def capacity_usd(self, **kwargs):
            capacity_calls.append(kwargs)
            return Decimal("17")

    def fake_prepare(_event, **kwargs):
        prepared_with.append(kwargs)
        assert kwargs["forecast_conn"].execute(
            "SELECT marker FROM readiness_state"
        ).fetchone()[0] == "fresh-forecast"
        assert kwargs["topology_conn"].execute(
            "SELECT marker FROM market_events"
        ).fetchone()[0] == "current-topology"
        assert kwargs["observation_conn"].execute(
            "SELECT marker FROM opportunity_events"
        ).fetchone()[0] == "authorized-day0"
        return SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key="family-dallas",
                witness_identity=f"current-q-{len(prepared_with)}",
            ),
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        fake_prepare,
    )
    monkeypatch.setattr(
        era,
        "_entry_global_submit_suppression_reason",
        lambda: "entries_paused:test_containment",
    )
    def make_adapter():
        return era.event_bound_live_adapter_from_trade_conn(
            trade,
            get_current_level=lambda: era.RiskLevel.GREEN,
            forecast_conn=forecast,
            topology_conn=topology,
            calibration_conn=world,
            portfolio_state_provider=lambda: pytest.fail(
                "cycle-start portfolio must not back global selection wealth"
            ),
            auction_capital_authority=CapacityAuthority(),
        )

    adapter = make_adapter()
    urgent_revision["value"] = (4, 5, 6)
    event = event_factory(
        city="Dallas",
        source_run_id="run-dallas",
    )

    result = adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    assert result.events == (event,)
    assert captured["world_conn"] is world
    assert captured["forecast_conn"] is forecast
    assert captured["world_conn"] is not topology
    assert captured["portfolio_state_provider"] is None
    assert captured["epoch_superseded"]() is True
    assert captured["restrict_to_family_keys"] == frozenset(
        {
            era.weather_family_id(
                city="Dallas",
                target_date="2026-07-11",
                metric="high",
            )
        }
    )
    assert callable(captured["candidate_policy_rejection_resolver"])
    assert captured["buy_candidates_enabled"] is False
    candidate = SimpleNamespace(family_key="family-dallas")
    assert captured["current_capital_limit_resolver"](
        candidate,
        "gamma-market",
        "market-event",
        "owner-event",
    ) == Decimal("17")
    assert capacity_calls == [
        {
            "market_id": "gamma-market",
            "event_id": "market-event",
            "resolution_window": "default",
            "correlation_key": "family-dallas",
        }
    ]
    prepared_receipt = captured["prepare_event"](
        event,
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )
    refreshed_receipt = captured["prepare_event"](
        event,
        _dt.datetime(2026, 7, 10, 8, 11, tzinfo=_dt.timezone.utc),
    )
    assert prepared_receipt.prepared_global_family is not None
    assert refreshed_receipt.prepared_global_family is not None
    assert (
        prepared_receipt.prepared_global_family.probability_witness.witness_identity
        == "current-q-1"
    )
    assert (
        refreshed_receipt.prepared_global_family.probability_witness.witness_identity
        == "current-q-2"
    )
    assert len(prepared_with) == 2
    assert all(kwargs["forecast_conn"] is forecast for kwargs in prepared_with)
    assert all(kwargs["topology_conn"] is topology for kwargs in prepared_with)
    assert all(kwargs["observation_conn"] is world for kwargs in prepared_with)
    policy = captured["candidate_policy_rejection_resolver"]
    low_price = SimpleNamespace(
        action="BUY",
        family_key="family-dallas",
        side="YES",
        executable_cost_curve=SimpleNamespace(
            levels=(SimpleNamespace(price=Decimal("0.004")),)
        ),
    )
    live_floor = SimpleNamespace(
        action="BUY",
        family_key="family-dallas",
        side="NO",
        executable_cost_curve=SimpleNamespace(
            levels=(SimpleNamespace(price=Decimal("0.10")),)
        ),
    )
    reduce_only = SimpleNamespace(
        action="SELL",
        family_key="family-dallas",
        side="YES",
    )
    assert policy(low_price) == "entries_paused:test_containment"
    assert policy(live_floor) == "entries_paused:test_containment"
    assert policy(reduce_only) is None
    metadata_calls = []
    bind_calls = []
    metadata_keys = (
        ("condition", "yes-token"),
        ("condition", "no-token"),
    )
    metadata = {
        "condition_id": "condition",
        "active": True,
        "_global_current_gamma": True,
    }
    refresh_hwm_calls = []

    def metadata_refresh_keys(
        _trade_conn,
        _probabilities,
        *,
        checked_at,
        refreshed_at_by_family=None,
    ):
        assert checked_at.tzinfo is not None
        refresh_hwm_calls.append(dict(refreshed_at_by_family or {}))
        return frozenset()

    def fake_bind(
        _forecast_conn,
        *,
        probability_witnesses,
        metadata_sink=None,
        **_,
    ):
        bind_calls.append(metadata_sink is not None)
        if metadata_sink is not None:
            for metadata_key in metadata_keys:
                metadata_sink[metadata_key] = metadata
        return dict(probability_witnesses)

    def fake_capture(_trade_conn, *, metadata_overrides, **_):
        metadata_calls.append(dict(metadata_overrides))
        return SimpleNamespace(witness_identity=f"book-{len(metadata_calls)}")

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, *_args, **_kwargs):
            return {}

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(
        era,
        "_global_book_metadata_refresh_family_keys",
        metadata_refresh_keys,
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakeClient)
    provider = captured["current_book_epoch_provider"]
    probabilities = {
        "family": SimpleNamespace(
            family_key="family",
            bindings=(
                SimpleNamespace(
                    condition_id="condition",
                    yes_token_id="yes-token",
                    no_token_id="no-token",
                ),
            ),
        )
    }
    provider(probabilities, _dt.datetime.now(_dt.timezone.utc))
    rebuilt_adapter = make_adapter()
    rebuilt_adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 11, tzinfo=_dt.timezone.utc),
    )
    captured["current_book_epoch_provider"](
        probabilities,
        _dt.datetime.now(_dt.timezone.utc),
    )

    assert metadata_calls == [
        {metadata_key: metadata for metadata_key in metadata_keys},
        {metadata_key: metadata for metadata_key in metadata_keys},
    ]
    assert bind_calls == [True, True]
    assert refresh_hwm_calls[0] == {}
    assert set(refresh_hwm_calls[1]) == {"family"}
    assert refresh_hwm_calls[1]["family"].tzinfo is not None

    urgent_revision["value"] = (7, 8, 9)
    urgent_reason["value"] = "market_price_advanced"
    day0_event = _global_day0_scope_event(
        city="Dallas",
        source_run_id="run-day0",
    )
    result = adapter.process_global_batch(
        (day0_event,),
        _dt.datetime(2026, 7, 10, 8, 12, tzinfo=_dt.timezone.utc),
    )

    assert result.events == (day0_event,)
    assert captured["epoch_superseded"]() is False

    urgent_revision["value"] = (10, 11, 12)
    urgent_reason["value"] = "day0_extreme_event_committed"
    assert captured["epoch_superseded"]() is True


def test_live_adapter_reuses_unchanged_probability_and_evicts_changed_family(
    monkeypatch,
):
    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    callbacks = []
    prepare_calls = []
    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_CACHE_NAMESPACE", None)
    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_CACHE", {})
    monkeypatch.setattr(
        era,
        "_GLOBAL_PROBABILITY_FAMILY_INELIGIBLE_CACHE",
        {},
    )

    family_key = era.weather_family_id(
        city="Dallas",
        target_date="2026-07-11",
        metric="high",
    )
    bindings = (
        OutcomeTokenBinding("low", "condition-low", None, None),
        OutcomeTokenBinding("high", "condition-high", None, None),
    )
    samples = np.tile(np.asarray(((0.4, 0.6),)), (400, 1))

    def fake_prepare(
        _event,
        *,
        decision_time,
        max_age,
        cache_metadata_out=None,
        **_,
    ):
        prepare_calls.append(decision_time)
        if cache_metadata_out is not None:
            cache_metadata_out["family_binding_hash"] = "family-binding"
        version = f"q-{len(prepare_calls)}"
        identity = joint_probability_witness_identity(
            family_key=family_key,
            bindings=bindings,
            q_version=version,
            resolution_identity="resolution",
            topology_identity="topology",
            posterior_identity_hash="run-dallas",
            source_truth_identity=f"source-{version}",
            authority_certificate_hash=f"certificate-{version}",
            band_alpha=0.05,
            band_basis="test-band",
            yes_q_samples=samples,
            captured_at_utc=decision_time,
        )
        witness = JointOutcomeProbabilityWitness(
            family_key=family_key,
            bindings=bindings,
            yes_q_samples=samples,
            q_version=version,
            resolution_identity="resolution",
            topology_identity="topology",
            posterior_identity_hash="run-dallas",
            source_truth_identity=f"source-{version}",
            authority_certificate_hash=f"certificate-{version}",
            band_alpha=0.05,
            band_basis="test-band",
            captured_at_utc=decision_time,
            max_age=max_age,
            witness_identity=identity,
        )
        return bridge.PreparedGlobalFamily(
            decision_id=f"decision-{version}",
            probability_witness=witness,
            candidate_seeds=(),
        )

    def fake_process(events, **kwargs):
        callbacks.append(kwargs["prepare_event"])
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        fake_prepare,
    )

    def adapter():
        return era.event_bound_live_adapter_from_trade_conn(
            trade,
            get_current_level=lambda: era.RiskLevel.GREEN,
            forecast_conn=forecast,
            topology_conn=topology,
            calibration_conn=world,
        )

    scope_event = _global_scope_event(city="Dallas", source_run_id="run-dallas")
    book_event = replace(scope_event, event_type="BOOK_SNAPSHOT")
    at_0 = _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc)
    at_1 = at_0 + _dt.timedelta(seconds=1)
    at_2 = at_1 + _dt.timedelta(seconds=1)
    at_3 = at_2 + _dt.timedelta(seconds=1)
    at_expired = at_2 + _dt.timedelta(seconds=181)

    adapter().process_global_batch((book_event,), at_0)
    first = callbacks[-1](scope_event, at_0).prepared_global_family
    reused = callbacks[-1](scope_event, at_1).prepared_global_family

    assert len(prepare_calls) == 1
    assert reused.probability_witness.captured_at_utc == at_1
    assert (
        reused.probability_witness.witness_identity
        != first.probability_witness.witness_identity
    )
    assert (
        reused.probability_witness.sample_matrix_identity
        == first.probability_witness.sample_matrix_identity
    )
    expected_certificate = era.stable_hash(
        {
            "event_id": scope_event.event_id,
            "causal_snapshot_id": scope_event.causal_snapshot_id,
            "family_binding_hash": "family-binding",
            "q_version": reused.probability_witness.q_version,
            "source_truth_identity": (
                reused.probability_witness.source_truth_identity
            ),
            "captured_at_utc": at_1.isoformat(),
        }
    )
    assert (
        reused.probability_witness.authority_certificate_hash
        == expected_certificate
    )
    assert reused.decision_id == era.stable_hash(
        {
            "authority_certificate_hash": expected_certificate,
            "witness_identity": reused.probability_witness.witness_identity,
        }
    )

    adapter().process_global_batch((scope_event,), at_2)
    refreshed = callbacks[-1](scope_event, at_2).prepared_global_family
    assert len(prepare_calls) == 2
    assert refreshed.probability_witness.q_version == "q-2"

    adapter().process_global_batch((book_event,), at_3)
    reused_refresh = callbacks[-1](scope_event, at_3).prepared_global_family
    assert len(prepare_calls) == 2
    assert reused_refresh.probability_witness.q_version == "q-2"
    assert reused_refresh.probability_witness.captured_at_utc == at_3

    adapter().process_global_batch((book_event,), at_expired)
    expired_refresh = callbacks[-1](
        scope_event,
        at_expired,
    ).prepared_global_family
    assert len(prepare_calls) == 3
    assert expired_refresh.probability_witness.q_version == "q-3"
    assert expired_refresh.probability_witness.captured_at_utc == at_expired


def test_live_adapter_excludes_closed_forecast_family_before_probability_prepare(
    monkeypatch,
):
    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    callbacks = []

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        lambda events, **kwargs: callbacks.append(kwargs["prepare_event"])
        or SimpleNamespace(events=tuple(events)),
    )
    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        lambda *_args, **_kwargs: pytest.fail(
            "a closed forecast family must not rebuild probability"
        ),
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
    )
    event = _global_scope_event(city="Dallas", source_run_id="run-dallas")
    settlement_day = _dt.datetime(
        2026, 7, 11, 8, 0, tzinfo=_dt.timezone.utc
    )

    adapter.process_global_batch((event,), settlement_day)
    receipt = callbacks[-1](event, settlement_day)

    assert receipt.prepared_global_family is None
    assert receipt.reason is not None
    assert receipt.reason.startswith(
        "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
        "EVENT_BOUND_MARKET_PHASE_CLOSED:settlement_day:"
    )


def test_live_adapter_reuses_ineligible_probability_until_authority_db_changes(
    monkeypatch,
):
    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    callbacks = []
    prepare_calls = []
    revision = {"value": (1, 1, 1)}
    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_CACHE_NAMESPACE", None)
    monkeypatch.setattr(era, "_GLOBAL_PROBABILITY_FAMILY_CACHE", {})
    monkeypatch.setattr(
        era,
        "_GLOBAL_PROBABILITY_FAMILY_INELIGIBLE_CACHE",
        {},
    )
    monkeypatch.setattr(
        era,
        "_global_probability_family_cache_revision",
        lambda _connections: revision["value"],
    )

    def fail_prepare(*_args, **_kwargs):
        prepare_calls.append(1)
        raise ValueError("EVENT_BOUND_MARKET_TOPOLOGY_MISSING")

    monkeypatch.setattr(
        era,
        "_prepare_current_global_probability_family",
        fail_prepare,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        lambda events, **kwargs: (
            callbacks.append(kwargs["prepare_event"])
            or SimpleNamespace(events=tuple(events))
        ),
    )

    def adapter():
        return era.event_bound_live_adapter_from_trade_conn(
            trade,
            get_current_level=lambda: era.RiskLevel.GREEN,
            forecast_conn=forecast,
            topology_conn=topology,
            calibration_conn=world,
        )

    scope_event = _global_scope_event(city="Dallas", source_run_id="run-dallas")
    book_event = replace(scope_event, event_type="BOOK_SNAPSHOT")
    at_0 = _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc)

    adapter().process_global_batch((book_event,), at_0)
    first = callbacks[-1](scope_event, at_0)
    adapter().process_global_batch((book_event,), at_0 + _dt.timedelta(seconds=1))
    reused = callbacks[-1](scope_event, at_0 + _dt.timedelta(seconds=1))

    assert len(prepare_calls) == 1
    assert reused is first
    assert reused.reason.endswith("EVENT_BOUND_MARKET_TOPOLOGY_MISSING")

    revision["value"] = (2, 1, 1)
    adapter().process_global_batch((book_event,), at_0 + _dt.timedelta(seconds=2))
    callbacks[-1](scope_event, at_0 + _dt.timedelta(seconds=2))
    assert len(prepare_calls) == 2

    adapter().process_global_batch((scope_event,), at_0 + _dt.timedelta(seconds=3))
    callbacks[-1](scope_event, at_0 + _dt.timedelta(seconds=3))
    assert len(prepare_calls) == 3


def test_live_adapter_reuses_book_cache_after_probability_rebind(
    monkeypatch,
):
    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured = {}
    monkeypatch.setattr(era, "_GLOBAL_BOOK_EPOCH_CACHE", None)

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
    )
    event = _global_scope_event(city="Dallas", source_run_id="run-dallas")
    adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    metadata = {
        ("condition-a", "yes-token-a"): {
            "condition_id": "condition-a",
            "_global_current_gamma": True,
        },
        ("condition-a", "no-token-a"): {
            "condition_id": "condition-a",
            "_global_current_gamma": True,
        },
        ("condition-b", "yes-token-b"): {
            "condition_id": "condition-b",
            "_global_current_gamma": True,
        },
        ("condition-b", "no-token-b"): {
            "condition_id": "condition-b",
            "_global_current_gamma": True,
        },
    }
    def probability(identity):
        return {
            "family": SimpleNamespace(
                family_key="family",
                witness_identity=identity,
                bindings=(
                    SimpleNamespace(
                        bin_id="bin-a",
                        condition_id="condition-a",
                        yes_token_id="yes-token-a",
                        no_token_id="no-token-a",
                    ),
                    SimpleNamespace(
                        bin_id="bin-b",
                        condition_id="condition-b",
                        yes_token_id="yes-token-b",
                        no_token_id="no-token-b",
                    ),
                ),
            )
        }

    bind_calls = []

    def fake_bind(
        _forecast_conn,
        *,
        probability_witnesses,
        metadata_sink=None,
        **_,
    ):
        bind_calls.append(
            (
                metadata_sink is not None,
                probability_witnesses["family"].witness_identity,
            )
        )
        if metadata_sink is not None:
            metadata_sink.update(metadata)
        return dict(probability_witnesses)

    capture_calls = []
    book_calls = []

    def fake_capture(_trade_conn, **kwargs):
        capture_calls.append(kwargs)
        assert kwargs["metadata_overrides"] == metadata
        tokens = [
            token
            for witness in kwargs["probability_witnesses"].values()
            for binding in witness.bindings
            for token in (binding.yes_token_id, binding.no_token_id)
        ]
        kwargs["get_books"](tokens)
        return SimpleNamespace(
            witness_identity="book-current",
            assets=(),
            current_identity=lambda _checked_at: "book-current",
        )

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, tokens, **_):
            book_calls.append(tuple(sorted(tokens)))
            return {
                token: {"asset_id": token, "hash": f"hash-{token}"}
                for token in tokens
            }

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        FakeClient,
    )
    provider = captured["current_book_epoch_provider"]
    probabilities = probability("request-probability-1")
    bound, epoch = provider(
        probabilities,
        _dt.datetime.now(_dt.timezone.utc),
    )
    next_probabilities = probability("request-probability-2")
    bound_again, epoch_again = provider(
        next_probabilities,
        _dt.datetime.now(_dt.timezone.utc),
    )
    bound_reauction, epoch_reauction = provider(
        bound_again,
        _dt.datetime.now(_dt.timezone.utc),
    )

    assert bound == probabilities
    assert bound_again == next_probabilities
    assert bound_reauction == next_probabilities
    assert epoch.witness_identity == "book-current"
    assert epoch_again is epoch
    assert epoch_reauction is epoch
    assert bind_calls == [(True, "request-probability-1")]
    assert len(capture_calls) == 1
    assert book_calls == [
        (
            "no-token-a",
            "no-token-b",
            "yes-token-a",
            "yes-token-b",
        ),
    ]
    trade.close()
    forecast.close()
    topology.close()
    world.close()


@pytest.mark.parametrize(
    ("condition_a_executable", "expected_book_calls"),
    (
        (True, [("yes-token-a", "no-token-a")]),
        (False, []),
    ),
)
def test_live_adapter_day0_binds_tradeability_before_fetching_executable_books(
    monkeypatch,
    condition_a_executable,
    expected_book_calls,
):
    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured = {}
    monkeypatch.setattr(era, "_GLOBAL_BOOK_EPOCH_CACHE", None)

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
    )
    event = replace(
        _global_scope_event(city="Dallas", source_run_id="run-dallas"),
        event_type="DAY0_EXTREME_UPDATED",
    )
    adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    probability = {
        "family": SimpleNamespace(
            family_key="family",
            witness_identity="probability-current",
            bindings=(
                SimpleNamespace(
                    bin_id="bin-a",
                    condition_id="condition-a",
                    yes_token_id="yes-token-a",
                    no_token_id="no-token-a",
                ),
                SimpleNamespace(
                    bin_id="bin-b",
                    condition_id="condition-b",
                    yes_token_id="yes-token-b",
                    no_token_id="no-token-b",
                ),
            ),
        )
    }
    bind_complete = False
    book_calls = []

    def fake_bind(
        _forecast_conn,
        *,
        probability_witnesses,
        metadata_sink=None,
        **_,
    ):
        nonlocal bind_complete
        assert metadata_sink is not None
        for condition_id, yes_token_id, no_token_id, executable in (
            (
                "condition-a",
                "yes-token-a",
                "no-token-a",
                condition_a_executable,
            ),
            ("condition-b", "yes-token-b", "no-token-b", False),
        ):
            metadata = {
                "_global_current_gamma": True,
                "enable_orderbook": executable,
                "active": executable,
                "closed": not executable,
                "accepting_orders": executable,
                "tradeability_status_json": json.dumps(
                    {"executable_allowed": executable}
                ),
            }
            metadata_sink[(condition_id, yes_token_id)] = metadata
            metadata_sink[(condition_id, no_token_id)] = metadata
        bind_complete = True
        return dict(probability_witnesses)

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, tokens, **_):
            assert bind_complete
            book_calls.append(tuple(tokens))
            return {
                token: {"asset_id": token, "hash": f"hash-{token}"}
                for token in tokens
            }

    def fake_capture(_trade_conn, **kwargs):
        assert set(kwargs["prefetched_books"]) == (
            {"yes-token-a", "no-token-a"}
            if condition_a_executable
            else set()
        )
        assert kwargs["prefetched_at_utc"].tzinfo is not None
        return SimpleNamespace(
            witness_identity="book-current",
            assets=(),
            current_identity=lambda _checked_at: "book-current",
        )

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        FakeClient,
    )

    bound, epoch = captured["current_book_epoch_provider"](
        probability,
        _dt.datetime.now(_dt.timezone.utc),
    )

    assert bound == probability
    assert epoch.witness_identity == "book-current"
    assert book_calls == expected_book_calls
    trade.close()
    forecast.close()
    topology.close()
    world.close()


@pytest.mark.parametrize("projection_survives", [True, False])
def test_live_adapter_overlaps_gamma_bind_with_missing_clob_book_prefetch(
    monkeypatch,
    projection_survives,
):
    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            gamma_market_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            fee_details_json TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        INSERT INTO executable_market_snapshots VALUES (
            'snapshot-a',
            'market-a',
            'event-a',
            'condition-a',
            'yes-token-a',
            'yes-token-a',
            'no-token-a',
            1,
            1,
            0,
            1,
            '0.01',
            '5',
            0,
            '{}',
            '{}',
            '{"asset_id":"yes-token-a","hash":"hash-yes-token-a"}',
            '2026-07-10T07:00:00+00:00',
            '2026-07-10T08:13:00+00:00'
        );
        INSERT INTO executable_market_snapshot_latest VALUES
            (
                'condition-a',
                'yes-token-a',
                'snapshot-a',
                'yes-token-a',
                'no-token-a',
                '2026-07-10T08:13:00+00:00'
            ),
            (
                'condition-a',
                'no-token-a',
                'snapshot-a',
                'yes-token-a',
                'no-token-a',
                '2026-07-10T08:13:00+00:00'
            );
        """
    )
    fresh_at = _dt.datetime.now(_dt.timezone.utc)
    trade.execute(
        "UPDATE executable_market_snapshots "
        "SET captured_at = ?, freshness_deadline = ?",
        (
            (fresh_at - _dt.timedelta(seconds=1)).isoformat(),
            (fresh_at + _dt.timedelta(minutes=3)).isoformat(),
        ),
    )
    trade.execute(
        "UPDATE executable_market_snapshot_latest SET freshness_deadline = ?",
        ((fresh_at + _dt.timedelta(minutes=3)).isoformat(),),
    )
    projected = era._projected_global_books(
        trade,
        ("yes-token-a", "no-token-a"),
        checked_at=_dt.datetime.now(_dt.timezone.utc),
        max_age=_dt.timedelta(minutes=3),
    )
    assert projected is not None
    assert set(projected[0]) == {"yes-token-a"}
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured = {}
    monkeypatch.setattr(era, "_GLOBAL_BOOK_EPOCH_CACHE", None)

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
    )
    event = replace(
        _global_scope_event(city="Dallas", source_run_id="run-dallas"),
        event_type="BOOK_SNAPSHOT",
    )
    adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    probability = {
        "family": SimpleNamespace(
            family_key="family",
            witness_identity="probability-current",
            bindings=(
                SimpleNamespace(
                    bin_id="bin-a",
                    condition_id="condition-a",
                    yes_token_id="yes-token-a",
                    no_token_id="no-token-a",
                ),
            ),
        )
    }
    bind_started = threading.Event()
    book_started = threading.Event()
    book_calls = []

    def fake_bind(
        _forecast_conn,
        *,
        probability_witnesses,
        metadata_sink=None,
        **_,
    ):
        bind_started.set()
        assert book_started.wait(1.0), "CLOB prefetch did not overlap Gamma bind"
        witness = probability_witnesses["family"]
        return {
            "family": SimpleNamespace(
                family_key="family",
                witness_identity=witness.witness_identity,
                bindings=(
                    SimpleNamespace(
                        bin_id="bin-a",
                        condition_id="condition-a",
                        yes_token_id="yes-token-a",
                        no_token_id="no-token-a",
                    ),
                ),
            )
        }

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, tokens, **_):
            book_calls.append(tuple(tokens))
            book_started.set()
            assert bind_started.wait(1.0), "Gamma bind did not overlap CLOB prefetch"
            return {
                token: {"asset_id": token, "hash": f"hash-{token}"}
                for token in tokens
            }

    capture_calls = []

    def fake_capture(_trade_conn, **kwargs):
        capture_calls.append(kwargs)
        expected_yes = {
            "asset_id": "yes-token-a",
            "hash": "hash-yes-token-a",
        }
        if projection_survives:
            expected_yes.update(
                {
                    "tick_size": "0.01",
                    "min_order_size": "5",
                    "neg_risk": False,
                }
            )
        assert kwargs["prefetched_books"] == {
            "yes-token-a": expected_yes,
            "no-token-a": {
                "asset_id": "no-token-a",
                "hash": "hash-no-token-a",
            },
        }
        assert kwargs["prefetched_at_utc"].tzinfo is not None
        return SimpleNamespace(
            witness_identity="book-current",
            assets=(),
            current_identity=lambda _checked_at: "book-current",
        )

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        FakeClient,
    )
    if not projection_survives:
        monkeypatch.setattr(
            era,
            "_global_book_prefetch_epoch_at",
            lambda **_: None,
        )

    bound, epoch = captured["current_book_epoch_provider"](
        probability,
        _dt.datetime.now(_dt.timezone.utc),
    )

    assert bound["family"].bindings[0].yes_token_id == "yes-token-a"
    assert bound["family"].bindings[0].no_token_id == "no-token-a"
    assert epoch.witness_identity == "book-current"
    assert len(capture_calls) == 1
    assert book_calls == (
        [("no-token-a",)]
        if projection_survives
        else [("no-token-a",), ("yes-token-a",)]
    )
    trade.close()
    forecast.close()
    topology.close()
    world.close()


def test_speculative_topology_fills_snapshot_gap_from_complete_receipt():
    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            gamma_market_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            fee_details_json TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            artifact_json TEXT NOT NULL
        );
        INSERT INTO executable_market_snapshots VALUES (
            'snapshot-a',
            'market-a',
            'event-a',
            'condition-a',
            'yes-token-a',
            'yes-token-a',
            'no-token-a',
            1,
            1,
            0,
            1,
            '0.01',
            '5',
            '{}',
            '{}',
            '2026-07-10T07:00:00+00:00',
            '2026-07-10T07:03:00+00:00'
        );
        INSERT INTO executable_market_snapshot_latest VALUES
            (
                'condition-a',
                'yes-token-a',
                'snapshot-a',
                'yes-token-a',
                'no-token-a'
            ),
            (
                'condition-a',
                'no-token-a',
                'snapshot-a',
                'yes-token-a',
                'no-token-a'
            );
        """
    )
    fields = [
        "family_key",
        "bin_id",
        "condition_id",
        "side",
        "token_id",
        "status",
        "book_hash",
        "market_event_id",
        "gamma_market_id",
    ]
    rows = [
        [
            "family",
            "bin-b",
            "condition-b",
            "YES",
            "yes-token-b",
            "EXECUTABLE",
            "hash-yes-b",
            "event-b",
            "market-b",
        ],
        [
            "family",
            "bin-b",
            "condition-b",
            "NO",
            "no-token-b",
            "EXECUTABLE",
            "hash-no-b",
            "event-b",
            "market-b",
        ],
    ]
    encoded = json.dumps(
        {"fields": fields, "rows": rows},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    summary = {
        "schema_version": 14,
        "book_native_side_candidate_coverage_status": "COMPLETE",
        "book_native_side_candidate_coverage_complete": True,
        "book_native_side_encoding": "zlib+base64+canonical-json-v1",
        "book_native_side_state_count": len(rows),
        "book_native_side_states_sha256": hashlib.sha256(encoded).hexdigest(),
        "book_native_side_states_zlib_b64": base64.b64encode(
            zlib.compress(encoded)
        ).decode(),
    }
    trade.execute(
        """
        INSERT INTO decision_log(mode, artifact_json)
        VALUES ('global_single_order_auction', ?)
        """,
        (json.dumps({"summary": summary}),),
    )
    probabilities = {
        "family": SimpleNamespace(
            family_key="family",
            bindings=(
                SimpleNamespace(
                    bin_id="bin-a",
                    condition_id="condition-a",
                ),
                SimpleNamespace(
                    bin_id="bin-b",
                    condition_id="condition-b",
                ),
            ),
        )
    }

    topology = era._global_book_speculative_topology(trade, probabilities)

    assert topology == (
        (
            "family",
            "bin-a",
            "condition-a",
            "yes-token-a",
            "no-token-a",
        ),
        (
            "family",
            "bin-b",
            "condition-b",
            "yes-token-b",
            "no-token-b",
        ),
    )
    trade.close()


def test_speculative_topology_ignores_corrupt_receipt():
    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            gamma_market_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            fee_details_json TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            artifact_json TEXT NOT NULL
        );
        """
    )
    summary = {
        "schema_version": 12,
        "book_native_side_candidate_coverage_status": "COMPLETE",
        "book_native_side_candidate_coverage_complete": True,
        "book_native_side_encoding": "zlib+base64+canonical-json-v1",
        "book_native_side_state_count": 2,
        "book_native_side_states_sha256": "not-the-payload-hash",
        "book_native_side_states_zlib_b64": base64.b64encode(
            zlib.compress(
                json.dumps(
                    {
                        "fields": [
                            "family_key",
                            "bin_id",
                            "condition_id",
                            "side",
                            "token_id",
                            "status",
                            "book_hash",
                            "market_event_id",
                            "gamma_market_id",
                        ],
                        "rows": [],
                    }
                ).encode()
            )
        ).decode(),
    }
    trade.execute(
        """
        INSERT INTO decision_log(mode, artifact_json)
        VALUES ('global_single_order_auction', ?)
        """,
        (json.dumps({"summary": summary}),),
    )
    probabilities = {
        "family": SimpleNamespace(
            family_key="family",
            bindings=(
                SimpleNamespace(
                    bin_id="bin-b",
                    condition_id="condition-b",
                ),
            ),
        )
    }

    assert era._global_book_speculative_topology(trade, probabilities) is None
    trade.close()


def test_live_adapter_discards_stale_hint_then_prefetches_unknown_full_refresh(
    monkeypatch,
):
    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            gamma_market_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            fee_details_json TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        INSERT INTO executable_market_snapshots VALUES (
            'snapshot-old',
            'market-a',
            'event-a',
            'condition-a',
            'yes-token-old',
            'yes-token-old',
            'no-token-old',
            1,
            1,
            0,
            1,
            '0.01',
            '5',
            '{}',
            '{}',
            '2026-07-10T07:00:00+00:00',
            '2026-07-10T07:03:00+00:00'
        );
        INSERT INTO executable_market_snapshot_latest VALUES
            (
                'condition-a',
                'yes-token-old',
                'snapshot-old',
                'yes-token-old',
                'no-token-old'
            ),
            (
                'condition-a',
                'no-token-old',
                'snapshot-old',
                'yes-token-old',
                'no-token-old'
            );
        """
    )
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured = {}
    monkeypatch.setattr(era, "_GLOBAL_BOOK_EPOCH_CACHE", None)

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
    )
    adapter.process_global_batch(
        (
            replace(
                _global_scope_event(
                    city="Dallas",
                    source_run_id="run-dallas",
                ),
                event_type="EDLI_REDECISION_PENDING",
                payload_json="{}",
            ),
        ),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )
    assert captured["restrict_to_family_keys"] is None
    probability = {
        "family": SimpleNamespace(
            family_key="family",
            witness_identity="probability-current",
            bindings=(
                SimpleNamespace(
                    bin_id="bin-a",
                    condition_id="condition-a",
                    yes_token_id="",
                    no_token_id="",
                ),
            ),
        )
    }

    def fake_bind(_forecast_conn, **_):
        return {
            "family": SimpleNamespace(
                family_key="family",
                witness_identity="probability-current",
                bindings=(
                    SimpleNamespace(
                        bin_id="bin-a",
                        condition_id="condition-a",
                        yes_token_id="yes-token-current",
                        no_token_id="no-token-current",
                    ),
                ),
            )
        }

    book_calls = []

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, tokens, **_):
            book_calls.append(tuple(sorted(tokens)))
            return {
                token: {"asset_id": token, "hash": f"hash-{token}"}
                for token in tokens
            }

    capture_prefetched = []

    def fake_capture(_trade_conn, **kwargs):
        capture_prefetched.append("prefetched_books" in kwargs)
        tokens = [
            token
            for witness in kwargs["probability_witnesses"].values()
            for binding in witness.bindings
            for token in (binding.yes_token_id, binding.no_token_id)
        ]
        kwargs["get_books"](tokens)
        return SimpleNamespace(
            witness_identity="book-current",
            assets=(),
            current_identity=lambda _checked_at: "book-current",
        )

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        FakeClient,
    )

    bound, epoch = captured["current_book_epoch_provider"](
        probability,
        _dt.datetime.now(_dt.timezone.utc),
    )
    _, refreshed_epoch = captured["current_book_epoch_provider"](
        dict(bound),
        _dt.datetime.now(_dt.timezone.utc),
    )

    assert epoch.witness_identity == "book-current"
    assert refreshed_epoch.witness_identity == "book-current"
    assert capture_prefetched == [False, True]
    assert book_calls == [
        ("no-token-old", "yes-token-old"),
        ("no-token-current", "yes-token-current"),
        ("no-token-current", "yes-token-current"),
    ]
    trade.close()
    forecast.close()
    topology.close()
    world.close()


def test_live_adapter_reuses_tokens_and_refreshes_only_eligible_book_family(
    monkeypatch,
):
    from src.events.candidate_binding import weather_family_id

    trade = sqlite3.connect(":memory:")
    forecast = sqlite3.connect(":memory:")
    topology = sqlite3.connect(":memory:")
    world = sqlite3.connect(":memory:")
    captured = {}
    monkeypatch.setattr(era, "_GLOBAL_BOOK_EPOCH_CACHE", None)

    def fake_process(events, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(events=tuple(events))

    monkeypatch.setattr(
        global_batch_runtime,
        "process_current_global_batch",
        fake_process,
    )
    event = replace(
        _global_scope_event(city="Dallas", source_run_id="run-dallas"),
        event_type="EDLI_REDECISION_PENDING",
    )
    ineligible_event = _global_scope_event(
        city="Alpha",
        source_run_id="run-alpha",
    )
    def make_adapter():
        return era.event_bound_live_adapter_from_trade_conn(
            trade,
            get_current_level=lambda: era.RiskLevel.GREEN,
            forecast_conn=forecast,
            topology_conn=topology,
            calibration_conn=world,
        )

    adapter = make_adapter()
    adapter.process_global_batch(
        (event, ineligible_event),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    dallas = weather_family_id(
        city="Dallas",
        target_date="2026-07-11",
        metric="high",
    )
    miami = weather_family_id(
        city="Miami",
        target_date="2026-07-11",
        metric="high",
    )

    def witness(family_key, suffix, *, identity=None):
        return SimpleNamespace(
            family_key=family_key,
            witness_identity=identity or f"probability-{suffix}",
            bindings=(
                SimpleNamespace(
                    bin_id=f"bin-{suffix}",
                    condition_id=f"condition-{suffix}",
                    yes_token_id=f"yes-token-{suffix}",
                    no_token_id=f"no-token-{suffix}",
                ),
            ),
        )

    probabilities = {
        dallas: witness(dallas, "dallas"),
        miami: witness(miami, "miami"),
    }
    bind_calls = []
    forced_gamma_calls = []
    expire_next_full_metadata = {"value": False}
    capture_calls = []
    book_calls = []

    def fake_bind(
        _forecast_conn,
        *,
        probability_witnesses,
        metadata_sink=None,
        trade_conn=None,
        **_,
    ):
        family_keys = tuple(sorted(probability_witnesses))
        bind_calls.append(
            (
                "metadata" if metadata_sink is not None else "token",
                family_keys,
            )
        )
        if trade_conn is None:
            forced_gamma_calls.append(family_keys)
        expire_miami = (
            expire_next_full_metadata["value"]
            and trade_conn is not None
            and len(probability_witnesses) > 1
        )
        if metadata_sink is not None:
            for family_key, probability in probability_witnesses.items():
                for binding in probability.bindings:
                    for token_id in (
                        binding.yes_token_id,
                        binding.no_token_id,
                    ):
                        metadata_sink[(binding.condition_id, token_id)] = (
                            {
                                "captured_at": "2020-01-01T00:00:00+00:00",
                                "freshness_deadline": "2020-01-01T00:01:00+00:00",
                            }
                            if expire_miami and family_key == miami
                            else {"_global_current_gamma": True}
                        )
        if expire_miami:
            expire_next_full_metadata["value"] = False
        return dict(probability_witnesses)

    def fake_capture(_trade_conn, **kwargs):
        probability_witnesses = kwargs["probability_witnesses"]
        metadata_overrides = kwargs["metadata_overrides"]
        for probability in probability_witnesses.values():
            for binding in probability.bindings:
                for token_id in (
                    binding.yes_token_id,
                    binding.no_token_id,
                ):
                    assert (binding.condition_id, token_id) in metadata_overrides
                    assert universe._global_book_metadata_is_current(
                        metadata_overrides[(binding.condition_id, token_id)],
                        checked_at_utc=_dt.datetime.now(_dt.timezone.utc),
                    )
        capture_calls.append(tuple(sorted(probability_witnesses)))
        states = []
        for family_key, probability in probability_witnesses.items():
            for binding in probability.bindings:
                for side, token_id in (
                    ("YES", binding.yes_token_id),
                    ("NO", binding.no_token_id),
                ):
                    states.append(
                        (
                            family_key,
                            binding.bin_id,
                            binding.condition_id,
                            side,
                            token_id,
                            "EXECUTABLE",
                            f"hash-{len(capture_calls)}-{token_id}",
                            f"event-{family_key}",
                            f"market-{family_key}",
                        )
                    )
        tokens = [
            token
            for probability in probability_witnesses.values()
            for binding in probability.bindings
            for token in (binding.yes_token_id, binding.no_token_id)
        ]
        kwargs["get_books"](tokens)
        captured_at = kwargs["clock"]()
        return CurrentGlobalBookEpoch(
            assets=(),
            asset_states=tuple(states),
            captured_at_utc=captured_at,
            max_age=_dt.timedelta(seconds=180),
            witness_identity=current_global_book_epoch_identity(
                asset_states=states,
                captured_at_utc=captured_at,
            ),
        )

    class FakeClient:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def get_orderbook_snapshots(self, tokens, **_):
            book_calls.append(tuple(sorted(tokens)))
            return {
                token: {"asset_id": token, "hash": f"hash-{token}"}
                for token in tokens
            }

    monkeypatch.setattr(universe, "bind_current_global_probability_tokens", fake_bind)
    monkeypatch.setattr(universe, "capture_current_global_book_epoch", fake_capture)
    monkeypatch.setattr(
        "src.data.polymarket_client.PolymarketClient",
        FakeClient,
    )
    provider = captured["current_book_epoch_provider"]
    bound, epoch = provider(
        probabilities,
        _dt.datetime.now(_dt.timezone.utc),
    )
    changed_probabilities = {
        dallas: witness(
            dallas,
            "dallas",
            identity="probability-dallas-updated",
        ),
        miami: probabilities[miami],
    }
    bound_again, epoch_again = provider(
        changed_probabilities,
        _dt.datetime.now(_dt.timezone.utc),
    )
    unrelated_drift = {
        dallas: witness(
            dallas,
            "dallas",
            identity="probability-dallas-newer",
        ),
        miami: witness(
            miami,
            "miami",
            identity="probability-miami-updated",
        ),
    }
    bound_after_unrelated_drift, _ = provider(
        unrelated_drift,
        _dt.datetime.now(_dt.timezone.utc),
    )
    adapter.process_global_batch(
        (_global_scope_event(city="Miami", source_run_id="run-miami"),),
        _dt.datetime(2026, 7, 10, 8, 11, tzinfo=_dt.timezone.utc),
    )
    forecast_provider = captured["current_book_epoch_provider"]
    bound_forecast_subset, _ = forecast_provider(
        {miami: unrelated_drift[miami]},
        _dt.datetime.now(_dt.timezone.utc),
    )
    bound_after_removal, epoch_after_removal = provider(
        {miami: unrelated_drift[miami]},
        _dt.datetime.now(_dt.timezone.utc),
    )
    cache_after_removal = era._GLOBAL_BOOK_EPOCH_CACHE
    rebound_after_add, epoch_after_add = provider(
        unrelated_drift,
        _dt.datetime.now(_dt.timezone.utc),
    )
    expired_at = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=181)
    expired_epoch = replace(
        epoch_after_add,
        captured_at_utc=expired_at,
        witness_identity=current_global_book_epoch_identity(
            asset_states=epoch_after_add.asset_states,
            captured_at_utc=expired_at,
        ),
    )
    cache_entry = era._GLOBAL_BOOK_EPOCH_CACHE
    assert cache_entry is not None
    monkeypatch.setattr(
        era,
        "_GLOBAL_BOOK_EPOCH_CACHE",
        replace(cache_entry, epoch=expired_epoch),
    )
    monkeypatch.setattr(
        era,
        "_global_book_metadata_refresh_family_keys",
        lambda *_args, **_kwargs: frozenset({dallas}),
    )
    expire_next_full_metadata["value"] = True
    rebuilt_adapter = make_adapter()
    rebuilt_adapter.process_global_batch(
        (event, ineligible_event),
        _dt.datetime(2026, 7, 10, 8, 12, tzinfo=_dt.timezone.utc),
    )
    rebuilt_provider = captured["current_book_epoch_provider"]
    bound_after_expiry, epoch_after_expiry = rebuilt_provider(
        unrelated_drift,
        _dt.datetime.now(_dt.timezone.utc),
    )

    assert bound == probabilities
    assert bound_again == changed_probabilities
    assert bound_after_unrelated_drift == unrelated_drift
    assert bound_forecast_subset == {miami: unrelated_drift[miami]}
    assert bound_after_removal == {miami: unrelated_drift[miami]}
    assert rebound_after_add == unrelated_drift
    assert bound_after_expiry == unrelated_drift
    # q changed, but the condition/token topology did not. Reuse the still-current
    # cached bindings while refreshing only the triggered family's live books.
    # A rebuilt adapter has no closure-local metadata, so any full capture first
    # certifies current metadata for that capture's complete family scope.
    assert bind_calls == [
        ("metadata", (dallas, miami)),
        ("metadata", (miami,)),
        ("metadata", (dallas,)),
        ("metadata", (dallas, miami)),
        ("metadata", (miami,)),
    ]
    assert forced_gamma_calls == [(miami,)]
    assert cache_after_removal is not None
    assert {family_key for family_key, _ in cache_after_removal.bound_probabilities} == {
        dallas,
        miami,
    }
    assert capture_calls == [
        (dallas, miami),
        (dallas,),
        (dallas,),
        (miami,),
        (dallas,),
        (dallas, miami),
    ]
    assert book_calls == [
        (
            "no-token-dallas",
            "no-token-miami",
            "yes-token-dallas",
            "yes-token-miami",
        ),
        ("no-token-dallas", "yes-token-dallas"),
        ("no-token-dallas", "yes-token-dallas"),
        ("no-token-miami", "yes-token-miami"),
        ("no-token-dallas", "yes-token-dallas"),
        (
            "no-token-dallas",
            "no-token-miami",
            "yes-token-dallas",
            "yes-token-miami",
        ),
    ]
    assert epoch_again.captured_at_utc == epoch.captured_at_utc
    assert len(epoch_again.asset_states) == 4
    assert epoch_again.witness_identity != epoch.witness_identity
    assert len(epoch_after_removal.asset_states) == 2
    assert {row[0] for row in epoch_after_removal.asset_states} == {miami}
    assert len(epoch_after_add.asset_states) == 4
    assert {row[0] for row in epoch_after_add.asset_states} == {
        dallas,
        miami,
    }
    assert epoch_after_expiry.captured_at_utc > expired_epoch.captured_at_utc
    trade.close()
    forecast.close()
    topology.close()
    world.close()


def test_global_book_epoch_cache_requires_stable_topology(monkeypatch):
    from src.events.candidate_binding import weather_family_id

    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(era, "_GLOBAL_BOOK_EPOCH_CACHE", None)
    at = _dt.datetime.now(_dt.timezone.utc)
    probabilities = {
        "family": SimpleNamespace(
            family_key="family",
            witness_identity="probability-current",
            bindings=(
                SimpleNamespace(
                    bin_id="bin",
                    condition_id="condition",
                    yes_token_id="yes-token",
                    no_token_id="no-token",
                ),
            ),
        )
    }
    epoch = SimpleNamespace(
        witness_identity="book-current",
        current_identity=lambda _checked_at: "book-current",
    )
    assert era._store_global_book_epoch(
        conn,
        probabilities,
        epoch,
        checked_at=at,
    ) == "stored"

    cached, reason = era._probe_global_book_epoch_cache(
        conn,
        probabilities,
        checked_at=at,
        allowed=True,
    )
    assert cached is epoch
    assert reason == "hit"
    changed = {
        "family": SimpleNamespace(
            family_key="family",
            witness_identity="probability-changed",
            bindings=(
                SimpleNamespace(
                    bin_id="bin",
                    condition_id="condition",
                    yes_token_id="yes-token",
                    no_token_id="no-token",
                ),
            ),
        )
    }
    assert era._get_cached_global_book_epoch(
        conn,
        changed,
        checked_at=at,
        allowed=True,
    ) is epoch
    topology_changed = {
        "family": SimpleNamespace(
            family_key="family",
            witness_identity="probability-changed",
            bindings=(
                SimpleNamespace(
                    bin_id="bin",
                    condition_id="condition",
                    yes_token_id="yes-token",
                    no_token_id="new-no-token",
                ),
            ),
        )
    }
    assert era._get_cached_global_book_epoch(
        conn,
        topology_changed,
        checked_at=at,
        allowed=True,
    ) is None
    mutable_cached, mutable_reason = era._probe_global_book_epoch_cache(
        conn,
        topology_changed,
        checked_at=at,
        allowed=True,
        mutable_family_keys=frozenset({"family"}),
    )
    assert mutable_cached is epoch
    assert mutable_reason == "hit_mutable_topology"
    price_event = replace(
        _global_scope_event(city="Dallas", source_run_id="run-dallas"),
        event_type="EDLI_REDECISION_PENDING",
    )
    assert era._global_book_refresh_family_keys((price_event,)) == {
        weather_family_id(
            city="Dallas",
            target_date="2026-07-11",
            metric="high",
        )
    }
    forecast_event = _global_scope_event(
        city="Dallas",
        source_run_id="run-dallas",
    )
    assert era._global_book_refresh_family_keys((forecast_event,)) == frozenset()
    assert era._global_probability_refresh_family_keys((price_event,)) == frozenset()
    assert era._global_probability_refresh_family_keys((forecast_event,)) == {
        weather_family_id(
            city="Dallas",
            target_date="2026-07-11",
            metric="high",
        )
    }
    price_payload = json.loads(price_event.payload_json)
    price_payload["redecision_origin"] = "market_price"
    price_payload["price_changed_token_ids"] = ["yes-token-a"]
    exact_price_event = replace(
        price_event,
        payload_json=json.dumps(price_payload),
    )
    assert era._global_projected_book_refresh_tokens((exact_price_event,)) == {
        weather_family_id(
            city="Dallas",
            target_date="2026-07-11",
            metric="high",
        ): frozenset({"yes-token-a"})
    }
    assert era._global_book_refresh_family_keys(
        (
            SimpleNamespace(event_type="BOOK_SNAPSHOT"),
        )
    ) == frozenset()
    assert era._global_book_refresh_family_keys(
        (
            SimpleNamespace(
                event_type="EDLI_REDECISION_PENDING",
                payload_json="{}",
            ),
        )
    ) is None

    expired = SimpleNamespace(
        witness_identity="book-expired",
        current_identity=lambda _checked_at: None,
    )
    assert era._store_global_book_epoch(
        conn,
        probabilities,
        expired,
        checked_at=at,
    ) == "expired"
    cached, reason = era._probe_global_book_epoch_cache(
        conn,
        probabilities,
        checked_at=at,
        allowed=True,
    )
    assert cached is epoch
    assert reason == "hit"
    conn.close()


def test_global_book_cache_rebinds_fresh_q_without_refreshing_untouched_tokens():
    captured_at = _dt.datetime.now(_dt.timezone.utc)
    cached_bindings = (
        OutcomeTokenBinding(
            bin_id="bin-low",
            condition_id="condition-low",
            yes_token_id="yes-low",
            no_token_id="no-low",
        ),
        OutcomeTokenBinding(
            bin_id="bin-high",
            condition_id="condition-high",
            yes_token_id="yes-high",
            no_token_id="no-high",
        ),
    )
    fresh_bindings = tuple(
        replace(binding, yes_token_id=None, no_token_id=None)
        for binding in cached_bindings
    )

    def witness(bindings, samples, version):
        identity = joint_probability_witness_identity(
            family_key="family",
            bindings=bindings,
            q_version=version,
            resolution_identity="resolution",
            topology_identity="topology",
            posterior_identity_hash=f"posterior-{version}",
            source_truth_identity=f"source-{version}",
            authority_certificate_hash=f"certificate-{version}",
            band_alpha=0.05,
            band_basis="test-band",
            yes_q_samples=samples,
            captured_at_utc=captured_at,
        )
        return JointOutcomeProbabilityWitness(
            family_key="family",
            bindings=bindings,
            yes_q_samples=samples,
            q_version=version,
            resolution_identity="resolution",
            topology_identity="topology",
            posterior_identity_hash=f"posterior-{version}",
            source_truth_identity=f"source-{version}",
            authority_certificate_hash=f"certificate-{version}",
            band_alpha=0.05,
            band_basis="test-band",
            captured_at_utc=captured_at,
            max_age=_dt.timedelta(minutes=3),
            witness_identity=identity,
        )

    cached = witness(
        cached_bindings,
        np.tile(np.asarray(((0.35, 0.65),)), (400, 1)),
        "cached",
    )
    fresh = witness(
        fresh_bindings,
        np.tile(np.asarray(((0.60, 0.40),)), (400, 1)),
        "fresh",
    )

    rebound = era._reuse_global_book_token_bindings(
        {"family": fresh},
        {"family": cached},
    )["family"]

    assert rebound.witness_identity != cached.witness_identity
    assert np.array_equal(rebound.yes_q_samples, fresh.yes_q_samples)
    assert rebound.bindings == cached.bindings


def test_global_probability_authority_is_materialized_once_per_family(monkeypatch):
    calls = []
    authority_a = object()

    def build(_cls, witness):
        calls.append(witness)
        if witness == "invalid":
            raise ValueError("invalid witness")
        return authority_a

    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(build),
    )

    authorities = global_batch_runtime._current_probability_authorities(
        {"family-a": "valid", "family-b": "invalid"}
    )

    assert authorities == {"family-a": authority_a, "family-b": None}
    assert calls == ["valid", "invalid"]


def test_global_probability_tightening_keeps_candidate_identity_and_bound():
    candidate = SimpleNamespace(
        family_key="family-a",
        bin_id="bin-a",
        side="NO",
        token_id="token-no-a",
        probability_witness_identity="witness-a",
    )
    actuation = SimpleNamespace(decision=SimpleNamespace(candidate=candidate))
    exc = era._GlobalProbabilityTightened(0.71)
    receipt = era.EventSubmissionReceipt(
        False,
        "event-1",
        "snapshot-1",
        reason=str(exc),
        global_jit_payoff_q_lcb=exc.payoff_q_lcb,
    )

    tightening = era._global_probability_tightening_from_receipt(
        receipt,
        actuation,
    )

    assert tightening is not None
    assert tightening.candidate_key == ("family-a", "bin-a", "NO", "token-no-a")
    assert tightening.probability_witness_identity == "witness-a"
    assert tightening.payoff_q_lcb == 0.71


def test_global_winner_binding_does_not_reapply_legacy_price_floor(monkeypatch):
    at = _dt.datetime(2026, 7, 14, 16, 24, tzinfo=_dt.timezone.utc)
    family_key = "Paris|2026-07-14|high"
    curve = ExecutableCostCurve(
        token_id="no-35c",
        side="NO",
        snapshot_id="snapshot-current",
        book_hash="book-current",
        levels=(BookLevel(price=Decimal("0.01"), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    candidate = SimpleNamespace(
        candidate_id="global-no-35c",
        family_key=family_key,
        bin_id="35C",
        condition_id="condition-35c",
        side="NO",
        token_id="no-35c",
        probability_witness_identity="probability-current",
        resolution_identity="resolution-current",
        ledger_snapshot_id="ledger-current",
        book_captured_at_utc=at,
        book_snapshot_id=curve.snapshot_id,
        execution_curve_identity=executable_curve_identity(curve),
        executable_cost_curve=curve,
    )
    proof = SimpleNamespace(
        candidate=SimpleNamespace(condition_id="condition-35c"),
        token_id="no-35c",
        direction="buy_no",
        missing_reason=(
            "ADMISSION_NEAR_SETTLED_PRICE:price=0.999000:ceiling=0.990000"
        ),
        row={"orderbook_depth_json": json.dumps({"hash": "venue-book-current"})},
        q_posterior=0.6419587,
        q_lcb_5pct=0.5066667,
        qkernel_execution_economics={
            "source": "qkernel_spine",
            "cost": 0.027666,
            "payoff_q_point": 0.6419587,
            "payoff_q_lcb": 0.5066667,
            "edge_lcb": 0.4790007,
        },
    )
    witness = SimpleNamespace(
        family_key=family_key,
        q_version="q-current",
        resolution_identity="resolution-current",
        topology_identity="topology-current",
        posterior_identity_hash="posterior-current",
        source_truth_identity="source-current",
        authority_certificate_hash="authority-current",
        band_alpha=0.05,
        band_basis="CURRENT_EVIDENCE",
        sample_matrix_identity="samples-current",
        witness_identity="probability-current",
    )
    decision = SimpleNamespace(
        candidate=candidate,
        shares=Decimal("100"),
        cost_usd=Decimal("0.9889383435"),
        limit_price=Decimal("0.01"),
        expected_fill_price_before_fee=Decimal("0.0094227"),
        max_spend_usd=Decimal("1.0495"),
        robust_delta_log_wealth=0.01906524,
        robust_ev_usd=49.6777,
        capital_efficiency=0.0192785,
    )
    actuation = SimpleNamespace(
        decision=decision,
        probability_witness=witness,
        actuation_identity="actuation-current",
        economic_identity="economics-current",
        universe_witness_identity="universe-current",
        wealth_witness_identity="wealth-current",
        wealth_economic_identity="wealth-economics-current",
        selection_epoch_identity="epoch-current",
        selection_cut_at_utc=at,
        decision_at_utc=at,
    )
    monkeypatch.setattr(
        era,
        "current_global_probability_authority",
        lambda *_args, **_kwargs: object(),
    )

    def persisted_execution_must_not_be_read(*_args, **_kwargs):
        pytest.fail("global preflight must bind the in-memory book epoch")

    monkeypatch.setattr(
        era,
        "current_global_execution_authority",
        persisted_execution_must_not_be_read,
    )
    captured = {}

    def bind_current(cert, **_kwargs):
        captured.update(cert)
        return dict(cert)

    monkeypatch.setattr(era, "_global_current_state_execution_economics", bind_current)
    monkeypatch.setattr(
        era,
        "_bind_global_current_state_economics_to_proof",
        lambda selected, cert: (selected, cert),
    )
    monkeypatch.setattr(
        "src.solve.solver.global_buy_fak_prefix_certificate",
        lambda *_args, **_kwargs: {
            "global_buy_fak_prefix_semantics": (
                "CONCAVE_WORST_LIMIT_ALL_NONZERO_PREFIXES_POSITIVE"
            )
        },
    )

    selected, cert = era._global_actuation_selected_proof(
        global_actuation=actuation,
        prepared_global_family=SimpleNamespace(probability_witness=witness),
        family=SimpleNamespace(family_id=family_key),
        event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
        all_proofs=(proof,),
        eligible_proofs=(proof,),
        forecast_conn=object(),
        decision_time=at,
    )

    assert selected is proof
    assert cert["global_actuation_identity"] == "actuation-current"
    assert captured["cost"] == 0.027666

    proof.missing_reason = None
    with pytest.raises(
        ValueError,
        match="GLOBAL_ACTUATION_PROOF_NO_LONGER_ELIGIBLE:.*CURRENT_SELECTION_SCOPE",
    ):
        era._global_actuation_selected_proof(
            global_actuation=actuation,
            prepared_global_family=SimpleNamespace(probability_witness=witness),
            family=SimpleNamespace(family_id=family_key),
            event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            all_proofs=(proof,),
            eligible_proofs=(),
            forecast_conn=object(),
            decision_time=at,
        )

    duplicate = SimpleNamespace(**vars(proof))
    with pytest.raises(ValueError, match="GLOBAL_ACTUATION_PROOF_BINDING_MISSING"):
        era._global_actuation_selected_proof(
            global_actuation=actuation,
            prepared_global_family=SimpleNamespace(probability_witness=witness),
            family=SimpleNamespace(family_id=family_key),
            event=SimpleNamespace(event_type="DAY0_EXTREME_UPDATED"),
            all_proofs=(proof, duplicate),
            eligible_proofs=(proof,),
            forecast_conn=object(),
            decision_time=at,
        )


@pytest.mark.parametrize(
    ("reason", "status"),
    (
        ("entries_paused:deployment_freshness_mismatch", "BATCH_BLOCKED"),
        ("live_health_entry_authority:failing_surfaces=runtime_code", "BATCH_BLOCKED"),
        ("EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED", "BATCH_BLOCKED"),
        ("EXECUTOR_BOUNDARY_MISSING", "BATCH_BLOCKED"),
        ("OPERATOR_ARM_REQUIRED", "BATCH_BLOCKED"),
        (
            "GLOBAL_CURRENT_STATE_PAYOFF_Q_TIGHTENED_REAUCTION_REQUIRED",
            "BATCH_BLOCKED",
        ),
        ("GLOBAL_CURRENT_STATE_ROBUST_MAJORITY_LOSS", "BATCH_BLOCKED"),
        ("GLOBAL_CURRENT_STATE_ECONOMICS_NON_POSITIVE", "BATCH_BLOCKED"),
        (
            "GLOBAL_ACTUATION_PREPARE_FAILED:"
            "SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED",
            "BATCH_BLOCKED",
        ),
        ("EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING", "BATCH_BLOCKED"),
        ("GLOBAL_ACTUATION_BOOK_SUPERSEDED", "BATCH_BLOCKED"),
        ("UNCLASSIFIED_PREFLIGHT_FAILURE", "BATCH_BLOCKED"),
        (
            "GLOBAL_ACTUATION_PROOF_NO_LONGER_ELIGIBLE:"
            "QKERNEL_EDGE_LCB_NON_POSITIVE",
            "BLOCKED",
        ),
        ("FILL_UP_NO_SUBMIT:NO_RESIDUAL_AT_OR_OVER_TARGET", "BLOCKED"),
        ("SHIFT_BIN_NO_SUBMIT:OLD_LEG_STILL_STRONG", "BLOCKED"),
        ("EVENT_BOUND_MARKET_PHASE_CLOSED:settlement_day", "BLOCKED"),
        (
            "GLOBAL_FAMILY_INELIGIBLE:"
            "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:"
            "ValueError:GLOBAL_DAY0_CURRENT_OBSERVATION_MISSING",
            "BLOCKED",
        ),
        (
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
            "LIVE_ENTRY_DAY0_PROBABILITY_AUTHORITY_REQUIRED:"
            "selected q_lcb does not match remaining-day transform:"
            "condition_id=condition-a:q_lcb=0.72:transform_lcb=0.965560157285",
            "BLOCKED",
        ),
        (
            "GLOBAL_ACTUATION_PREPARE_FAILED:"
            "SELECTION_SCOPE_EMPTY:locked:input=22:"
            "classes=EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED=22",
            "BLOCKED",
        ),
    ),
)
def test_global_preflight_block_scope_is_explicit(reason, status):
    assert era._global_preflight_block_status(reason) == status


def test_global_preflight_runs_final_entry_authority_before_stable(monkeypatch):
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    receipt = EventSubmissionReceipt(
        False,
        event.event_id,
        event.causal_snapshot_id,
        proof_accepted=True,
        decision_proof_bundle=(object(),),
    )
    monkeypatch.setattr(
        era,
        "_build_live_cap_certificate_from_ledger",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        era,
        "_actionable_payload_from_receipt",
        lambda *_args, **_kwargs: {},
    )
    def reject(_payload):
        raise ValueError(
            "LIVE_ENTRY_DAY0_PROBABILITY_AUTHORITY_REQUIRED:"
            "selected q_lcb does not match remaining-day transform:"
            "condition_id=condition-a:q_lcb=0.72:transform_lcb=0.96"
        )

    monkeypatch.setattr(
        era,
        "_assert_live_entry_submit_authority",
        reject,
    )

    rejected = era._global_preflight_entry_authority_receipt(
        event,
        receipt,
        decision_time=_dt.datetime(2026, 7, 14, tzinfo=_dt.timezone.utc),
        live_cap_conn=object(),
    )

    assert rejected.proof_accepted is False
    assert rejected.side_effect_status == "NO_SUBMIT"
    assert rejected.reason == (
        "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
        "LIVE_ENTRY_DAY0_PROBABILITY_AUTHORITY_REQUIRED:"
        "selected q_lcb does not match remaining-day transform:"
        "condition_id=condition-a:q_lcb=0.72:transform_lcb=0.96"
    )
    assert era._global_preflight_block_status(rejected.reason) == "BLOCKED"


def test_global_preflight_jit_curve_replaces_selected_size_and_reauctions():
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    at = _dt.datetime(2026, 7, 14, 20, 5, tzinfo=_dt.timezone.utc)
    selected_curve = ExecutableCostCurve(
        token_id="token-a",
        side="NO",
        snapshot_id="selected-book",
        book_hash="selected-hash",
        levels=(BookLevel(price=Decimal("0.012"), size=Decimal("190")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    candidate = GlobalSingleOrderCandidate(
        candidate_id="candidate-a",
        family_key="family-a",
        bin_id="bin-a",
        condition_id="condition-a",
        side="NO",
        token_id="token-a",
        probability_witness_identity="probability-a",
        book_snapshot_id=selected_curve.snapshot_id,
        book_captured_at_utc=at,
        execution_curve_identity=executable_curve_identity(selected_curve),
        ledger_snapshot_id="ledger-a",
        executable_cost_curve=selected_curve,
        resolution_identity="resolution-a",
    )
    receipt = EventSubmissionReceipt(
        False,
        event.event_id,
        event.causal_snapshot_id,
        proof_accepted=True,
        decision_proof_bundle=(object(),),
    )
    actuation = SimpleNamespace(
        winner_event_id=event.event_id,
        decision=SimpleNamespace(
            candidate=candidate,
            limit_price=Decimal("0.012"),
            shares=Decimal("190"),
        ),
    )
    calls = []

    def book(token_id):
        calls.append(token_id)
        return {
            "asset_id": token_id,
            "hash": "book-a",
            "bids": [{"price": "0.003", "size": "100"}],
            "asks": [{"price": "0.004", "size": "217.68"}],
        }

    superseded = era._global_preflight_entry_jit_receipt(
        event,
        receipt,
        global_actuation=actuation,
        book_quote_provider=book,
    )

    assert calls == ["token-a"]
    assert superseded.proof_accepted is False
    assert superseded.reason.startswith(
        "GLOBAL_ACTUATION_EXECUTION_BINDING_SUPERSEDED:"
        "curve_economics:jit_detail=fields=levels:"
    )
    assert superseded.global_jit_candidate is not None
    assert superseded.global_jit_candidate.executable_cost_curve.levels == (
        BookLevel(price=Decimal("0.004"), size=Decimal("217.68")),
    )
    status, replacement, reason = era._global_curve_supersession_from_receipt(
        superseded
    )
    assert status == "CURVE_SUPERSEDED"
    assert replacement is superseded.global_jit_candidate
    assert reason == superseded.reason

    replacement_actuation = SimpleNamespace(
        winner_event_id=event.event_id,
        decision=SimpleNamespace(
            candidate=superseded.global_jit_candidate,
            limit_price=Decimal("0.004"),
            shares=Decimal("190"),
        ),
    )
    replacement_captured_at = superseded.global_jit_candidate.book_captured_at_utc
    reused = era._global_preflight_entry_jit_receipt(
        event,
        receipt,
        global_actuation=replacement_actuation,
        book_quote_provider=lambda _token_id: pytest.fail(
            "exact JIT re-auction witness should not be fetched again"
        ),
        current_candidate_override=superseded.global_jit_candidate,
        checked_at_utc=replacement_captured_at + _dt.timedelta(seconds=1),
    )
    assert reused is receipt

    stale_calls = []
    stale = era._global_preflight_entry_jit_receipt(
        event,
        receipt,
        global_actuation=replacement_actuation,
        book_quote_provider=lambda token_id: (
            stale_calls.append(token_id)
            or {
                "asset_id": token_id,
                "hash": "fresh-after-expiry",
                "bids": [{"price": "0.003", "size": "100"}],
                "asks": [{"price": "0.004", "size": "217.68"}],
            }
        ),
        current_candidate_override=superseded.global_jit_candidate,
        checked_at_utc=replacement_captured_at + _dt.timedelta(seconds=31),
    )
    assert stale_calls == ["token-a"]
    assert stale is receipt

    stable = era._global_preflight_entry_jit_receipt(
        event,
        receipt,
        global_actuation=actuation,
        book_quote_provider=lambda token_id: {
            "asset_id": token_id,
            "hash": "evidence-only-hash-change",
            "bids": [{"price": "0.003", "size": "100"}],
            "asks": [{"price": "0.012", "size": "190"}],
        },
    )
    assert stable is receipt


def test_global_preflight_reuses_provider_observation_without_second_fetch():
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    at = _dt.datetime(2026, 7, 14, 20, 5, tzinfo=_dt.timezone.utc)
    curve = ExecutableCostCurve(
        token_id="token-a",
        side="NO",
        snapshot_id="selected-book",
        book_hash="selected-hash",
        levels=(BookLevel(price=Decimal("0.012"), size=Decimal("190")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    candidate = GlobalSingleOrderCandidate(
        candidate_id="candidate-a",
        family_key="family-a",
        bin_id="bin-a",
        condition_id="condition-a",
        side="NO",
        token_id="token-a",
        probability_witness_identity="probability-a",
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=at,
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id="ledger-a",
        executable_cost_curve=curve,
        resolution_identity="resolution-a",
    )
    receipt = EventSubmissionReceipt(
        False,
        event.event_id,
        event.causal_snapshot_id,
        proof_accepted=True,
        decision_proof_bundle=(object(),),
    )
    actuation = SimpleNamespace(
        winner_event_id=event.event_id,
        decision=SimpleNamespace(
            candidate=candidate,
            limit_price=Decimal("0.012"),
            shares=Decimal("190"),
        ),
    )

    class Provider:
        def __init__(self):
            self.fetches = 0
            self.consumes = 0
            self.last = (
                {
                    "asset_id": "token-a",
                    "hash": "reused-book",
                    "bids": [{"price": "0.003", "size": "100"}],
                    "asks": [{"price": "0.012", "size": "190"}],
                },
                at,
                "price_channel_projection",
            )

        def __call__(self, _token_id):
            self.fetches += 1
            raise AssertionError("global preflight performed a second book fetch")

        def consume_last(self, _token_id):
            self.consumes += 1
            last, self.last = self.last, None
            return last

    provider = Provider()

    stable = era._global_preflight_entry_jit_receipt(
        event,
        receipt,
        global_actuation=actuation,
        book_quote_provider=provider,
    )

    assert stable is receipt
    assert provider.consumes == 1
    assert provider.fetches == 0


def test_global_winner_persists_jit_curve_as_executor_depth_authority():
    conn = sqlite3.connect(":memory:")
    init_snapshot_schema(conn)
    captured = _dt.datetime(2026, 7, 14, 20, 5, tzinfo=_dt.timezone.utc)
    old = ExecutableMarketSnapshot(
        snapshot_id="old-snapshot",
        gamma_market_id="gamma-a",
        event_id="market-event-a",
        event_slug="event-a",
        condition_id="condition-a",
        question_id="question-a",
        yes_token_id="token-yes-a",
        no_token_id="token-no-a",
        selected_outcome_token_id="token-no-a",
        outcome_label="NO",
        enable_orderbook=True,
        active=True,
        closed=False,
        accepting_orders=True,
        market_start_at=None,
        market_end_at=None,
        market_close_at=None,
        sports_start_at=None,
        min_tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"),
        fee_details=canonicalize_fee_details(
            {"fee_rate_fraction": 0.05},
            source="fixture",
            token_id="token-no-a",
        ),
        token_map_raw={},
        rfqe=None,
        neg_risk=False,
        orderbook_top_bid=Decimal("0.39"),
        orderbook_top_ask=Decimal("0.40"),
        orderbook_depth_jsonb=json.dumps(
            {
                "bids": [{"price": "0.39", "size": "100"}],
                "asks": [{"price": "0.40", "size": "100"}],
            }
        ),
        raw_gamma_payload_hash="a" * 64,
        raw_clob_market_info_hash="b" * 64,
        raw_orderbook_hash="c" * 64,
        authority_tier="CLOB",
        captured_at=captured - _dt.timedelta(seconds=5),
        freshness_deadline=captured + _dt.timedelta(seconds=25),
    )
    insert_snapshot(conn, old)
    conn.commit()
    selected_curve = ExecutableCostCurve(
        token_id="token-no-a",
        side="NO",
        snapshot_id="selected-snapshot",
        book_hash="d" * 64,
        levels=(
            BookLevel(price=Decimal("0.39"), size=Decimal("100")),
        ),
        fee_model=FeeModel(fee_rate=Decimal("0.05")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("1"),
        quote_ttl=_dt.timedelta(seconds=30),
        fee_details=canonicalize_fee_details(
            {
                "fee_rate_fraction": 0.05,
                "feeSchedule_taker_only": True,
                "maker_rebate_rate": 0.25,
                "fee_type": "weather_fees",
            },
            source="global_current_gamma_fee_schedule",
            token_id="token-no-a",
        ),
    )
    raw_book = {
        "asset_id": "token-no-a",
        "hash": "opaque-venue-hash",
        "asks": [
            {"price": "0.37", "size": "20"},
            {"price": "0.38", "size": "30"},
        ],
    }
    selected = GlobalSingleOrderCandidate(
        candidate_id="candidate-a",
        family_key="family-a",
        bin_id="bin-a",
        side="NO",
        token_id="token-no-a",
        condition_id="condition-a",
        probability_witness_identity="probability-a",
        book_snapshot_id=selected_curve.snapshot_id,
        book_captured_at_utc=captured - _dt.timedelta(seconds=1),
        execution_curve_identity=executable_curve_identity(selected_curve),
        ledger_snapshot_id="ledger-a",
        executable_cost_curve=selected_curve,
        resolution_identity="resolution-a",
    )
    candidate = era._global_buy_candidate_from_raw_book(
        selected,
        raw_book,
        captured_at_utc=captured,
    )
    curve = candidate.executable_cost_curve

    snapshot, row = era._persist_global_candidate_executable_snapshot(
        conn,
        proof=SimpleNamespace(executable_snapshot_id=old.snapshot_id),
        candidate=candidate,
        decision_time=captured + _dt.timedelta(seconds=1),
    )

    assert row["snapshot_id"] == curve.snapshot_id
    assert curve.book_hash == stable_hash(raw_book)
    assert len(curve.book_hash) == 64
    assert snapshot.orderbook_top_ask == Decimal("0.37")
    assert snapshot.raw_orderbook_hash == curve.book_hash
    assert snapshot.min_tick_size == curve.min_tick
    assert snapshot.min_order_size == curve.min_order_size
    assert snapshot.selected_outcome_token_id == curve.token_id
    assert snapshot.fee_details["fee_rate_fraction"] == 0.05
    assert snapshot.fee_details["feeSchedule_taker_only"] is True
    assert snapshot.fee_details["source"] == "global_current_gamma_fee_schedule"
    assert snapshot.orderbook_depth_jsonb == json.dumps(
        {
            "asks": [
                {"price": "0.37", "size": "20"},
                {"price": "0.38", "size": "30"},
            ],
            "bids": [],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert get_snapshot(conn, curve.snapshot_id) == snapshot
    newer_substrate = replace(
        old,
        snapshot_id="newer-substrate",
        gamma_market_id="gamma-b",
        raw_gamma_payload_hash="e" * 64,
    )
    insert_snapshot(conn, newer_substrate)
    conn.commit()
    reused, reused_row = era._persist_global_candidate_executable_snapshot(
        conn,
        proof=SimpleNamespace(executable_snapshot_id=newer_substrate.snapshot_id),
        candidate=candidate,
        decision_time=captured + _dt.timedelta(seconds=2),
    )
    assert reused == snapshot
    assert reused_row["snapshot_id"] == snapshot.snapshot_id

    changed_curve = replace(
        curve,
        fee_model=FeeModel(fee_rate=Decimal("0.10")),
        fee_details=canonicalize_fee_details(
            {"fee_rate_fraction": 0.10},
            source="global_current_gamma_fee_schedule",
            token_id="token-no-a",
        ),
    )
    changed_candidate = replace(
        candidate,
        execution_curve_identity=executable_curve_identity(changed_curve),
        executable_cost_curve=changed_curve,
    )
    with pytest.raises(ValueError, match="GLOBAL_JIT_SNAPSHOT_ID_COLLISION"):
        era._persist_global_candidate_executable_snapshot(
            conn,
            proof=SimpleNamespace(
                executable_snapshot_id=newer_substrate.snapshot_id
            ),
            candidate=changed_candidate,
            decision_time=captured + _dt.timedelta(seconds=2),
        )
    with pytest.raises(ValueError, match="LIVE_DEPTH_AUTHORITY_MISSING"):
        era._assert_taker_depth_authority_fresh(
            snapshot=old,
            direction="buy_no",
            witness_touch=Decimal("0.37"),
            tick_size=Decimal("0.01"),
        )
    era._assert_taker_depth_authority_fresh(
        snapshot=snapshot,
        direction="buy_no",
        witness_touch=Decimal("0.37"),
        tick_size=Decimal("0.01"),
    )


def test_global_preflight_token_lifetime_starts_after_proof_completion():
    started = _dt.datetime(2026, 7, 14, 8, 0, tzinfo=_dt.timezone.utc)
    completed = started + _dt.timedelta(seconds=10.6)

    issued_at, expires_at = era._global_preflight_token_window(
        started + _dt.timedelta(seconds=30),
        issued_at=completed,
    )

    assert issued_at == completed
    assert expires_at == completed + _dt.timedelta(seconds=10)
    assert completed < expires_at


def test_global_preflight_token_lifetime_never_crosses_actuation_deadline():
    deadline = _dt.datetime(2026, 7, 14, 8, 0, 30, tzinfo=_dt.timezone.utc)
    completed = deadline - _dt.timedelta(milliseconds=100)

    issued_at, expires_at = era._global_preflight_token_window(
        deadline,
        issued_at=completed,
    )

    assert issued_at == completed
    assert expires_at == deadline


def test_current_global_scope_uses_latest_day0_carrier_per_family():
    forecast_alpha = _global_scope_event(city="Alpha", source_run_id="run-a")
    forecast_beta = _global_scope_event(city="Beta", source_run_id="run-b")
    day0_payload = Day0ExtremeUpdatedPayload(
        city="Alpha",
        target_date="2026-07-11",
        metric="high",
        settlement_source="WU",
        station_id="ALPHA-WU",
        observation_time="2026-07-10T08:09:00+00:00",
        observation_available_at="2026-07-10T08:10:00+00:00",
        raw_value=21.2,
        rounded_value=21,
        high_so_far=21.2,
        source_match_status="MATCH",
        local_date_status="MATCH",
        station_match_status="MATCH",
        dst_status="UNAMBIGUOUS",
        metric_match_status="MATCH",
        rounding_status="MATCH",
        source_authorized_status="AUTHORIZED",
        live_authority_status="live",
    )
    day0_payload_json = asdict(day0_payload)
    day0_payload_json["city_timezone"] = "UTC"
    day0_alpha = make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key="Alpha|2026-07-11|high|ALPHA-WU",
        source="day0_observation",
        observed_at=day0_payload.observation_time,
        available_at=day0_payload.observation_available_at,
        received_at="2026-07-10T08:10:01+00:00",
        payload=day0_payload_json,
        causal_snapshot_id="day0-alpha-0810",
    )
    forecast_alpha = replace(
        forecast_alpha,
        available_at="2026-07-10T08:11:00+00:00",
        created_at="2026-07-10T08:11:00+00:00",
    )

    forecast_only = current_global_auction_scope_from_events(
        (forecast_alpha, forecast_beta),
        captured_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
    )
    merged_events = current_global_scope_events_with_day0(
        (forecast_alpha, forecast_beta),
        (day0_alpha,),
    )
    merged = current_global_auction_scope_from_events(
        merged_events,
        captured_at_utc=_dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    assert len(merged.events) == 2
    assert merged.events_by_family[0][1].event_id == day0_alpha.event_id
    assert merged.events_by_family[0][1].event_type == "DAY0_EXTREME_UPDATED"
    assert merged.events_by_family[1][1].event_id == forecast_beta.event_id
    assert merged.scope_identity != forecast_only.scope_identity


def test_day0_entry_scope_requires_target_city_current_local_day():
    current = _dt.datetime(2026, 7, 10, 12, 0, tzinfo=_dt.timezone.utc)

    assert _day0_event_is_current_for_entry(
        {"city": "London", "target_date": "2026-07-10"},
        decision_at_utc=current,
    )
    assert not _day0_event_is_current_for_entry(
        {"city": "London", "target_date": "2026-07-09"},
        decision_at_utc=current,
    )
    assert not _day0_event_is_current_for_entry(
        {"city": "London", "target_date": "2026-07-11"},
        decision_at_utc=current,
    )


def _insert_event(conn, event):
    fields = tuple(event.__dataclass_fields__)
    conn.execute(
        f"INSERT INTO opportunity_events ({','.join(fields)}) "
        f"VALUES ({','.join('?' for _ in fields)})",
        tuple(getattr(event, field) for field in fields),
    )


def _current_day0_scope_event(*, city, target_date, available_at):
    payload = {
        "city": city,
        "target_date": target_date,
        "metric": "high",
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
        "live_authority_status": "live",
    }
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"{city}|{target_date}|high|{available_at}",
        source="day0_observation",
        observed_at=available_at,
        available_at=available_at,
        received_at=available_at,
        payload=payload,
        causal_snapshot_id=f"day0-{city}-{target_date}",
    )


def test_current_day0_query_uses_utc_window_and_target_date_index(monkeypatch):
    import src.config as config

    decision_at = _dt.datetime(2026, 7, 10, 11, 30, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(
        config,
        "runtime_cities_by_name",
        lambda: {
            "West": SimpleNamespace(timezone="Etc/GMT+12"),
            "Center": SimpleNamespace(timezone="UTC"),
            "East": SimpleNamespace(timezone="Pacific/Kiritimati"),
            "Old": SimpleNamespace(timezone="UTC"),
            "Future": SimpleNamespace(timezone="UTC"),
        },
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_opportunity_events_table(conn)
    for city, target_date in (
        ("West", "2026-07-09"),
        ("Center", "2026-07-10"),
        ("East", "2026-07-11"),
        ("Old", "2026-07-08"),
        ("Future", "2026-07-12"),
    ):
        _insert_event(
            conn,
            _current_day0_scope_event(
                city=city,
                target_date=target_date,
                available_at="2026-07-10T11:00:00+00:00",
            ),
        )
    _insert_event(
        conn,
        _current_day0_scope_event(
            city="Center",
            target_date="2026-07-10",
            available_at="2026-07-10T10:00:00+00:00",
        ),
    )

    executed_sql = []
    conn.set_trace_callback(executed_sql.append)
    real_loads = json.loads
    decoded = 0

    def _counting_loads(value):
        nonlocal decoded
        decoded += 1
        return real_loads(value)

    monkeypatch.setattr(universe.json, "loads", _counting_loads)
    events = _current_day0_events(conn, decision_at_utc=decision_at)
    monkeypatch.setattr(universe.json, "loads", real_loads)

    events_by_city = {
        json.loads(event.payload_json)["city"]: event for event in events
    }
    assert decoded == 0
    assert set(events_by_city) == {"West", "Center", "East"}
    assert events_by_city["Center"].available_at == "2026-07-10T11:00:00+00:00"
    sql = next(
        sql
        for sql in executed_sql
        if "INDEXED BY idx_opportunity_events_fsr_target_date" in sql
    )
    assert "BETWEEN '2026-07-09' AND '2026-07-11'" in sql
    plan = " ".join(
        row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    ).upper()
    assert (
        "SEARCH OPPORTUNITY_EVENTS USING INDEX IDX_OPPORTUNITY_EVENTS_FSR_TARGET_DATE"
        in plan
    )
    assert "SCAN OPPORTUNITY_EVENTS" not in plan
    assert "USE TEMP B-TREE" not in plan


def test_current_day0_query_restricts_result_to_requested_family(monkeypatch):
    import src.config as config

    decision_at = _dt.datetime(2026, 7, 10, 11, 30, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(
        config,
        "runtime_cities_by_name",
        lambda: {
            "Alpha": SimpleNamespace(timezone="UTC"),
            "Beta": SimpleNamespace(timezone="UTC"),
        },
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_opportunity_events_table(conn)
    for city in ("Alpha", "Beta"):
        _insert_event(
            conn,
            _current_day0_scope_event(
                city=city,
                target_date="2026-07-10",
                available_at="2026-07-10T11:00:00+00:00",
            ),
        )

    events = _current_day0_events(
        conn,
        decision_at_utc=decision_at,
        restrict_to_families=(("Alpha", "2026-07-10", "high"),),
    )

    assert [json.loads(event.payload_json)["city"] for event in events] == [
        "Alpha"
    ]


def test_current_day0_scope_keeps_completed_family_only_when_still_held(
    monkeypatch,
):
    import src.config as config

    decision_at = _dt.datetime(2026, 7, 10, 12, 0, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(
        config,
        "runtime_cities_by_name",
        lambda: {
            "Held": SimpleNamespace(timezone="UTC"),
            "Unheld": SimpleNamespace(timezone="UTC"),
        },
    )
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_opportunity_events_table(conn)
    for city in ("Held", "Unheld"):
        _insert_event(
            conn,
            _current_day0_scope_event(
                city=city,
                target_date="2026-07-08",
                available_at="2026-07-08T23:30:00+00:00",
            ),
        )

    executed_sql = []
    conn.set_trace_callback(executed_sql.append)
    events = _current_day0_events(
        conn,
        decision_at_utc=decision_at,
        held_families=(("Held", "2026-07-08", "high"),),
    )

    assert [json.loads(event.payload_json)["city"] for event in events] == [
        "Held"
    ]
    assert any(
        "json_extract(payload_json, '$.target_date')='2026-07-08'" in sql
        for sql in executed_sql
    )


def test_global_scope_refuses_a_held_family_without_probability_carrier(
    monkeypatch,
):
    calls = []

    class EmptyTrigger:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_committed_snapshot_events(self, **kwargs):
            calls.append(kwargs)
            return ()

    monkeypatch.setattr(universe, "ForecastSnapshotReadyTrigger", EmptyTrigger)
    monkeypatch.setattr(
        universe,
        "executable_forecast_live_eligible_reader",
        lambda _conn: lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(universe, "_current_day0_events", lambda *_args, **_kwargs: ())

    with pytest.raises(
        ValueError,
        match=(
            "GLOBAL_HELD_FAMILY_PROBABILITY_CARRIER_MISSING:"
            r"Held\|2026-07-08\|high"
        ),
    ):
        universe.scan_current_global_auction_scope(
            world_conn=object(),
            forecasts_conn=object(),
            decision_at_utc=_dt.datetime(
                2026, 7, 10, 12, 0, tzinfo=_dt.timezone.utc
            ),
            held_families=(("Held", "2026-07-08", "high"),),
        )

    assert calls[0]["phase_filter_exempt_families"] == {
        ("Held", "2026-07-08", "high")
    }


def test_global_scope_pushes_family_restriction_to_carrier_readers(monkeypatch):
    trigger_calls = []
    day0_calls = []
    event = _global_scope_event(city="Alpha", source_run_id="run-alpha")

    class RestrictedTrigger:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_committed_snapshot_events(self, **kwargs):
            trigger_calls.append(kwargs)
            return (event,)

    def current_day0(*_args, **kwargs):
        day0_calls.append(kwargs)
        return ()

    monkeypatch.setattr(
        universe,
        "ForecastSnapshotReadyTrigger",
        RestrictedTrigger,
    )
    monkeypatch.setattr(
        universe,
        "executable_forecast_live_eligible_reader",
        lambda _conn: lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(universe, "_current_day0_events", current_day0)

    scope = universe.scan_current_global_auction_scope(
        world_conn=object(),
        forecasts_conn=object(),
        decision_at_utc=_dt.datetime(
            2026, 7, 10, 12, 0, tzinfo=_dt.timezone.utc
        ),
        held_families=(
            ("Alpha", "2026-07-11", "high"),
            ("Beta", "2026-07-11", "high"),
        ),
        restrict_to_families=(("Alpha", "2026-07-11", "high"),),
    )

    assert scope.events == (event,)
    assert trigger_calls[0]["restrict_to_families"] == {
        ("Alpha", "2026-07-11", "high")
    }
    assert trigger_calls[0]["phase_filter_exempt_families"] == {
        ("Alpha", "2026-07-11", "high")
    }
    assert day0_calls[0]["restrict_to_families"] == frozenset(
        {("Alpha", "2026-07-11", "high")}
    )
    assert day0_calls[0]["held_families"] == (
        ("Alpha", "2026-07-11", "high"),
    )


def test_day0_only_global_scope_never_builds_a_forecast_carrier(monkeypatch):
    event = _global_day0_scope_event(city="Alpha", source_run_id="run-alpha")
    day0_calls = []

    class ForbiddenForecastTrigger:
        def __init__(self, *_args, **_kwargs):
            pytest.fail("urgent Day0 scope must not build a forecast carrier")

    def current_day0(*_args, **kwargs):
        day0_calls.append(kwargs)
        return (event,)

    monkeypatch.setattr(
        universe,
        "ForecastSnapshotReadyTrigger",
        ForbiddenForecastTrigger,
    )
    monkeypatch.setattr(universe, "_current_day0_events", current_day0)

    scope = universe.scan_current_global_auction_scope(
        world_conn=object(),
        forecasts_conn=object(),
        decision_at_utc=_dt.datetime(
            2026, 7, 11, 17, 6, tzinfo=_dt.timezone.utc
        ),
        restrict_to_families=(("Alpha", "2026-07-11", "high"),),
        day0_only=True,
    )

    assert scope.events == (event,)
    assert day0_calls[0]["restrict_to_families"] == frozenset(
        {("Alpha", "2026-07-11", "high")}
    )


@pytest.fixture(autouse=True)
def _fast_band_draws(monkeypatch):
    monkeypatch.setattr(bridge, "SPINE_BAND_DRAWS", 400, raising=False)


def _corpus():
    """A small (family, proofs, payload) corpus: a +edge trade and an overpriced no-trade."""
    fam_a, _ = R._three_bin_family()
    trade_proofs = R._proofs_for(
        fam_a, yes_asks=[0.05, 0.20, 0.20, 0.05], no_asks=[0.92, 0.75, 0.75, 0.92],
        q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    trade = (
        fam_a,
        trade_proofs,
        _payload_with_joint_samples(
            trade_proofs,
            R._payload_with_spine_inputs(
                mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7]
            ),
        ),
    )
    fam_b, _ = R._three_bin_family()
    no_trade_proofs = R._proofs_for(
        fam_b, yes_asks=[0.60, 0.60, 0.60, 0.60], no_asks=[0.60, 0.60, 0.60, 0.60],
        q_by_bin=[0.05, 0.45, 0.40, 0.10], q_lcb_by_bin=[0.02, 0.32, 0.28, 0.05],
    )
    no_trade = (
        fam_b,
        no_trade_proofs,
        _payload_with_joint_samples(
            no_trade_proofs,
            R._payload_with_spine_inputs(
                mu=20.4, sigma=1.2, members=[19.8, 20.1, 20.5, 21.0, 20.7]
            ),
        ),
    )
    return [trade, no_trade]


def _serialize(result) -> str:
    """Canonical serialization of a SpineDecisionResult for byte-identity comparison."""
    d = result.decision
    sel = getattr(d, "selected", None) if d is not None else None
    parts = [
        f"decided_by_spine={getattr(result, 'decided_by_spine', None)}",
        f"no_trade_reason={result.no_trade_reason!r}",
        f"selected_proof={getattr(getattr(result, 'selected_proof', None), 'token_id', None)!r}",
    ]
    if d is not None:
        parts += [
            f"decision_id={d.decision_id!r}", f"receipt_hash={d.receipt_hash!r}",
            f"no_trade={d.no_trade_reason!r}", f"n_candidates={len(d.candidates)}",
            f"n_candidate_decisions={len(d.candidate_decisions)}",
        ]
    if sel is not None:
        parts += [
            f"sel_route={sel.route_id!r}", f"sel_stake={sel.optimal_stake_usd}",
            f"sel_du={sel.optimal_delta_u!r}",
        ]
    return "|".join(parts)


def _set_flag(value):
    """Set the flag dict entry (None => absent). Returns a restore callable."""
    from src.config import settings

    ff = settings["feature_flags"]
    had = "w3_solve_enabled" in ff
    prev = ff.get("w3_solve_enabled")
    if value is None:
        ff.pop("w3_solve_enabled", None)
    else:
        ff["w3_solve_enabled"] = value

    def _restore():
        if had:
            ff["w3_solve_enabled"] = prev
        else:
            ff.pop("w3_solve_enabled", None)

    return _restore


# --- (a) absent-vs-OFF byte-identity ----------------------------------------

def test_g3_absent_vs_off_byte_identical():
    corpus = _corpus()
    restore = _set_flag(None)  # absent
    try:
        assert bridge.w3_solve_enabled() is False
        absent = [_serialize(_drive(f, p, pl)) for f, p, pl in corpus]
    finally:
        restore()
    restore = _set_flag(False)  # explicit OFF
    try:
        assert bridge.w3_solve_enabled() is False
        off = [_serialize(_drive(f, p, pl)) for f, p, pl in corpus]
    finally:
        restore()
    assert absent == off, f"absent vs OFF diverged:\n absent={absent}\n off={off}"
    # the corpus must run the real pipeline (a FamilyDecision produced), not a trivial input-fault
    assert any("decision_id=" in s for s in off), "corpus did not exercise the engine pipeline"


def test_g3_off_ignores_joint_samples_and_keeps_v1_band_identity():
    restore = _set_flag(None)
    try:
        result = _drive(*_corpus()[0])
    finally:
        restore()

    assert result.decision is not None
    band = result.decision.band
    assert band is not None
    assert band.samples.shape[0] == 1
    expected = hashlib.sha256()
    expected.update(b"REACTOR_SERVED_POSTERIOR_DETERMINISTIC_BAND_V1")
    expected.update(result.decision.joint_q.identity_hash.encode("utf-8"))
    expected.update(f"alpha={float(band.alpha):.12f}".encode("utf-8"))
    assert band.sample_hash == expected.hexdigest()


# --- (b) single divergence point --------------------------------------------

def test_g3_flag_consumed_at_exactly_one_site():
    tree = ast.parse(open(_BRIDGE_PATH).read())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "w3_solve_enabled"
    ]
    wraps = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_wrap_engine_with_solve_shim"
    ]
    assert len(calls) == 1, f"w3_solve_enabled() must be consumed at EXACTLY one site, found {len(calls)}"
    assert len(wraps) == 1, f"_wrap_engine_with_solve_shim must be called exactly once, found {len(wraps)}"


# --- (c) ON-mode integration ------------------------------------------------

_SOLVER_ORIGIN_REASONS = (
    "NO_IMPROVING_DISCRETE_PLAN", "NO_EXECUTABLE_MENU_ITEMS", "UNSAFE_PREFIX_DECOMPOSITION",
    "BUDGET_EXCEEDED", "PHASE1_PRIMARY_LEG",
)


def test_g3_on_mode_shim_runs_and_is_contract_valid():
    corpus = _corpus()
    restore = _set_flag(True)
    try:
        assert bridge.w3_solve_enabled() is True
        ran_solver = False
        for f, p, pl in corpus:
            result = _drive(f, p, pl)
            if result.decision is None:
                continue
            # every emitted FamilyDecision satisfies the frozen consumer contract (no getattr
            # default would fire in the facts writer / overlay)
            validate_family_decision_contract(result.decision)
            if result.decision.selected is not None:
                ran_solver = True
                # projection stamped: selected carries the standalone ΔU value
                assert result.decision.selected.optimal_delta_u is not None
            elif result.no_trade_reason and any(k in result.no_trade_reason for k in _SOLVER_ORIGIN_REASONS):
                ran_solver = True  # a solver-origin no-trade proves the solver selection path ran
        # the ON branch physically imported + executed the solver
        assert "src.solve.solver" in sys.modules
        assert ran_solver, "ON-mode did not exercise the solver selection path"
    finally:
        restore()


def test_g3_on_mode_selection_diverges_from_off():
    # The whole point of the seam: ON runs the current-state solver while OFF retains the
    # legacy empirical-guard selector.  A route becoming honestly executable may make both
    # paths trade, so divergence is proven by decision authority rather than by requiring
    # one path to manufacture a no-trade reason.
    trade = _corpus()[0]
    restore = _set_flag(None)
    try:
        off = _drive(*trade)
    finally:
        restore()
    restore = _set_flag(True)
    try:
        on = _drive(*trade)
    finally:
        restore()
    assert off.decision is not None and on.decision is not None
    assert all(
        candidate.q_lcb_guard_basis != "CURRENT_POSTERIOR_BAND"
        for candidate in off.decision.candidate_decisions
    )
    assert on.decision.candidate_decisions
    assert all(
        candidate.q_lcb_guard_basis == "CURRENT_POSTERIOR_BAND"
        for candidate in on.decision.candidate_decisions
    )
    assert any(k in (on.no_trade_reason or "") for k in _SOLVER_ORIGIN_REASONS) or on.decision.selected is not None


def test_g3_on_mode_never_reads_historical_decision_guards(monkeypatch):
    from src.decision.family_decision_engine import FamilyDecisionEngine

    def _history_read_forbidden(*args, **kwargs):
        raise AssertionError("W3_CURRENT_STATE_SOLVE_MUST_NOT_READ_HISTORICAL_GUARDS")

    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_apply_qlcb_reliability_guard",
        _history_read_forbidden,
    )
    monkeypatch.setattr(
        FamilyDecisionEngine,
        "_apply_selection_calibrator_guard",
        _history_read_forbidden,
    )
    restore = _set_flag(True)
    try:
        result = _drive(*_corpus()[0])
    finally:
        restore()

    assert result.decision is not None
    validate_family_decision_contract(result.decision)


def test_g3_on_mode_fails_closed_without_joint_posterior_samples():
    family, proofs, payload = _corpus()[0]
    payload = dict(payload)
    payload.pop("_edli_spine_served_joint_q_samples_by_condition", None)
    restore = _set_flag(True)
    try:
        result = _drive(family, proofs, payload)
    finally:
        restore()

    assert result.decision is None
    assert result.no_trade_reason == "SPINE_INPUTS_UNAVAILABLE:SERVED_JOINT_SAMPLES_MISSING"


def test_global_family_prepare_binds_full_simplex_to_condition_token_pairs():
    family, proofs, payload = _corpus()[0]
    captured_at = "2026-06-13T11:59:59.900000+00:00"
    proofs = tuple(
        replace(proof, row={**proof.row, "captured_at": captured_at})
        for proof in proofs
    )
    blocked_proof = replace(
        proofs[0],
        missing_reason="ADMISSION_NEAR_SETTLED_PRICE:price=0.999000:ceiling=0.990000",
        passed_prefilter=False,
    )
    recoverable_proof = replace(
        proofs[1],
        missing_reason="ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:legacy-pre-spine",
        passed_prefilter=False,
    )
    proofs = (blocked_proof, recoverable_proof, *proofs[2:])
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    restore = _set_flag(False)
    try:
        result = bridge.decide_family_via_spine(
            family=family,
            payload=payload,
            proofs=proofs,
            decision_time=_dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc),
            native_side_candidate_from_proof=era._native_side_candidate_from_proof,
            global_native_side_candidate_from_proof=(
                era._full_depth_native_side_candidate_from_proof
            ),
            require_global_probability_witness=True,
            global_probability_max_age=_dt.timedelta(seconds=1),
            candidate_bin_id=era._candidate_bin_id,
            payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
            exposure_builder=era._robust_marginal_utility_exposure,
            baseline_usd_provider=lambda: Decimal("1000"),
            per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
            extra_exposure_by_bin_id=None,
        )
    finally:
        restore()

    assert result.global_prepare_reason is None
    prepared = result.global_family
    assert prepared is not None
    probability = prepared.probability_witness
    assert probability.yes_q_samples.shape[0] == 400
    assert all(
        abs(float(row.sum()) - 1.0) < 1e-12
        for row in probability.yes_q_samples
    )
    binding_by_key = {
        (binding.bin_id, "YES"): binding.yes_token_id
        for binding in probability.bindings
    } | {
        (binding.bin_id, "NO"): binding.no_token_id
        for binding in probability.bindings
    }
    assert prepared.candidate_seeds
    seed_tokens = {
        seed.native_candidate.token_id for seed in prepared.candidate_seeds
    }
    assert blocked_proof.token_id not in seed_tokens
    assert recoverable_proof.token_id in seed_tokens
    for seed in prepared.candidate_seeds:
        candidate = seed.native_candidate
        assert candidate.token_id == binding_by_key[(candidate.bin_id, candidate.side)]
        assert candidate.executable_cost_curve.token_id == candidate.token_id
        materialized = global_candidate_from_native(
            candidate,
            probability_witness=probability,
            ledger_snapshot_id="ledger-current",
            book_captured_at_utc=seed.book_captured_at_utc,
        )
        assert materialized.token_id == candidate.token_id
        assert materialized.probability_witness_identity == probability.witness_identity


def _global_book_metadata_conn(
    probability,
    *,
    captured_at="2026-06-13T07:59:00+00:00",
    freshness_deadline="2026-06-13T08:00:30+00:00",
):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            gamma_market_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER NOT NULL,
            fee_details_json TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            tradeability_status_json TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        )
        """
    )
    for binding in probability.bindings:
        for side, token in (
            ("YES", binding.yes_token_id),
            ("NO", binding.no_token_id),
        ):
            snapshot_id = f"metadata-{binding.condition_id}-{side}"
            conn.execute(
                "INSERT INTO executable_market_snapshots VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    f"gamma-market-{binding.condition_id}",
                    f"market-event-{probability.family_key}",
                    binding.condition_id,
                    token,
                    binding.yes_token_id,
                    binding.no_token_id,
                    1,
                    1,
                    0,
                    1,
                    '{"fee_rate_fraction":0}',
                    "0.01",
                    "5",
                    captured_at,
                    freshness_deadline,
                    '{"executable_allowed":true}',
                    '{"unused_append_payload":"must_not_be_read"}',
                ),
            )
            conn.execute(
                "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?,?)",
                (
                    binding.condition_id,
                    token,
                    snapshot_id,
                    binding.yes_token_id,
                    binding.no_token_id,
                ),
            )
    return conn


def test_speculative_global_book_topology_reads_latest_projection_only():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)

    def latest_only_authorizer(action, table, _column, _db, _trigger):
        if action == sqlite3.SQLITE_READ and table == "executable_market_snapshots":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(latest_only_authorizer)
    topology = era._global_book_speculative_topology(
        conn,
        {probability.family_key: probability},
    )

    assert topology is not None
    assert len(topology) == len(probability.bindings)
    assert {
        (row[2], row[3], row[4])
        for row in topology
    } == {
        (
            binding.condition_id,
            binding.yes_token_id,
            binding.no_token_id,
        )
        for binding in probability.bindings
    }


def test_global_book_curve_uses_same_realized_fee_authority_as_jit(monkeypatch):
    observed = []

    def realized_fee(schedule):
        observed.append(schedule)
        return 0.0, "realized_test"

    monkeypatch.setattr(universe, "resolve_taker_fee_fraction", realized_fee)
    raw_book = {
        "hash": "opaque-book-1",
        "tick_size": "0.01",
        "min_order_size": "5",
        "asks": [{"price": "0.30", "size": "100"}],
    }
    curve = universe._global_book_curve(
        family_key="City|2026-07-11|high",
        bin_id="bin-1",
        condition_id="condition-1",
        side="NO",
        token_id="no-1",
        raw_book=raw_book,
        metadata={"fee_details_json": '{"fee_rate_fraction":0.05}'},
        captured_at_utc=_dt.datetime(
            2026, 7, 11, 3, 0, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    )

    assert observed == pytest.approx([0.05])
    assert curve is not None
    assert curve.fee_model.fee_rate == Decimal("0.0")
    assert curve.book_hash == universe._canonical_raw_book_hash(raw_book)
    assert len(curve.book_hash) == 64


def test_current_global_book_epoch_reads_yes_and_no_symmetrically():
    family, proofs, payload = _corpus()[0]
    proofs = tuple(
        replace(
            proof,
            row={**proof.row, "captured_at": "2026-06-13T07:59:59+00:00"},
        )
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    result = bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        global_native_side_candidate_from_proof=era._full_depth_native_side_candidate_from_proof,
        require_global_probability_witness=True,
        global_probability_max_age=_dt.timedelta(seconds=30),
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )
    assert result.global_family is not None
    probability = result.global_family.probability_witness
    conn = _global_book_metadata_conn(probability)
    denied_columns = {"orderbook_depth_json"}

    def metadata_authorizer(action, table, column, _db, _trigger):
        if (
            action == sqlite3.SQLITE_READ
            and table == "executable_market_snapshots"
            and column in denied_columns
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(metadata_authorizer)
    requested = []
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))

    def books(tokens):
        requested.extend(tokens)
        return {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens
        }

    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=books,
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
        batch_size=500,
    )

    expected = 2 * len(probability.bindings)
    assert len(requested) == expected
    assert len(epoch.asset_states) == expected
    assert len(epoch.assets) == expected
    assert len(epoch.sell_assets) == expected
    assert {asset.side for asset in epoch.assets} == {"YES", "NO"}
    assert {asset.side for asset in epoch.sell_assets} == {"YES", "NO"}
    assert all(asset.curve.token_id == asset.token_id for asset in epoch.assets)
    assert all(
        asset.curve.token_id == asset.token_id for asset in epoch.sell_assets
    )

    required_conn = _global_book_metadata_conn(probability)
    denied_columns.clear()
    denied_columns.add("fee_details_json")
    required_conn.set_authorizer(metadata_authorizer)
    required_times = iter((at, at + _dt.timedelta(seconds=1)))
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|prohibited"):
        capture_current_global_book_epoch(
            required_conn,
            probability_witnesses={probability.family_key: probability},
            get_books=books,
            clock=lambda: next(required_times),
            max_age=_dt.timedelta(seconds=30),
            batch_size=500,
        )


def test_current_global_book_epoch_batches_snapshot_invalidation_truth():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshot_invalidations (
            invalidation_id TEXT PRIMARY KEY,
            condition_id TEXT,
            token_id TEXT,
            reason TEXT NOT NULL,
            invalidated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_snapshot_invalidations_condition_time
            ON executable_market_snapshot_invalidations (
                condition_id, invalidated_at DESC
            );
        CREATE INDEX idx_snapshot_invalidations_token_time
            ON executable_market_snapshot_invalidations (
                token_id, invalidated_at DESC
            );
        """
    )
    invalidated = probability.bindings[0]
    conn.executemany(
        """
        INSERT INTO executable_market_snapshot_invalidations VALUES (
            ?, ?, ?, 'market_closed', ?, ?
        )
        """,
        (
            (
                "invalidate-newer-token",
                invalidated.condition_id,
                "a-newer-token",
                "2026-06-13T07:59:30+00:00",
                "2026-06-13T07:59:30+00:00",
            ),
            (
                "invalidate-older-token",
                invalidated.condition_id,
                "z-older-token",
                "2026-06-13T07:58:30+00:00",
                "2026-06-13T07:58:30+00:00",
            ),
        ),
    )
    invalidation_reads = []
    conn.set_trace_callback(
        lambda sql: (
            invalidation_reads.append(sql)
            if "FROM executable_market_snapshot_invalidations" in sql
            else None
        )
    )
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    with pytest.raises(
        ValueError,
        match=f"GLOBAL_BOOK_METADATA_INVALIDATED:{invalidated.condition_id}",
    ):
        capture_current_global_book_epoch(
            conn,
            probability_witnesses={probability.family_key: probability},
            get_books=lambda _tokens: pytest.fail(
                "invalidated metadata must block before CLOB fetch"
            ),
            clock=lambda: at,
            max_age=_dt.timedelta(seconds=30),
        )

    assert len(invalidation_reads) == 1


def test_global_book_metadata_refresh_tracks_unresolved_invalidation():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)
    conn.execute(
        """
        CREATE TABLE executable_market_snapshot_invalidations (
            invalidation_id TEXT PRIMARY KEY,
            condition_id TEXT,
            token_id TEXT,
            reason TEXT NOT NULL,
            invalidated_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    binding = probability.bindings[0]
    invalidated_at = _dt.datetime(
        2026, 6, 13, 7, 59, 30, tzinfo=_dt.timezone.utc
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_invalidations VALUES (
            'tick-change', ?, ?, 'tick_size_change', ?, ?
        )
        """,
        (
            binding.condition_id,
            binding.yes_token_id,
            invalidated_at.isoformat(),
            invalidated_at.isoformat(),
        ),
    )
    checked_at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    probabilities = {probability.family_key: probability}

    assert era._global_book_metadata_refresh_family_keys(
        conn,
        probabilities,
        checked_at=checked_at,
    ) == {probability.family_key}
    assert era._global_book_metadata_refresh_family_keys(
        conn,
        probabilities,
        checked_at=checked_at,
        refreshed_at_by_family={
            probability.family_key: invalidated_at - _dt.timedelta(seconds=1)
        },
    ) == {probability.family_key}
    assert era._global_book_metadata_refresh_family_keys(
        conn,
        probabilities,
        checked_at=checked_at,
        refreshed_at_by_family={
            probability.family_key: invalidated_at
        },
    ) == frozenset()

    later_invalidation = checked_at + _dt.timedelta(seconds=1)
    conn.execute(
        """
        UPDATE executable_market_snapshots
           SET captured_at = '2026-06-13T07:59:45+00:00'
        """
    )
    assert era._global_book_metadata_refresh_family_keys(
        conn,
        probabilities,
        checked_at=checked_at,
    ) == frozenset()

    conn.execute(
        """
        INSERT INTO executable_market_snapshot_invalidations VALUES (
            'market-resolved', ?, ?, 'market_resolved', ?, ?
        )
        """,
        (
            binding.condition_id,
            binding.yes_token_id,
            later_invalidation.isoformat(),
            later_invalidation.isoformat(),
        ),
    )
    assert era._global_book_metadata_refresh_family_keys(
        conn,
        probabilities,
        checked_at=checked_at + _dt.timedelta(seconds=2),
        refreshed_at_by_family={
            probability.family_key: checked_at,
        },
    ) == {probability.family_key}


def test_global_book_metadata_refresh_hwm_survives_adapter_rebuild_scope(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "trade.db"
    first = sqlite3.connect(path)
    rebuilt = sqlite3.connect(path)
    other = sqlite3.connect(tmp_path / "other.db")
    refreshed_at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(era, "_GLOBAL_BOOK_METADATA_REFRESH_NAMESPACE", None)
    monkeypatch.setattr(
        era,
        "_GLOBAL_BOOK_METADATA_REFRESHED_AT_BY_FAMILY",
        {},
    )

    era._record_global_book_metadata_refresh_hwm(
        first,
        ("family-alpha",),
        refreshed_at=refreshed_at,
    )

    assert era._global_book_metadata_refresh_hwm(first) == {
        "family-alpha": refreshed_at,
    }
    assert era._global_book_metadata_refresh_hwm(rebuilt) == {
        "family-alpha": refreshed_at,
    }
    assert era._global_book_metadata_refresh_hwm(other) == {}


def test_current_global_book_epoch_consumes_prefetched_books_at_original_cut():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)
    captured_at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    finished_at = captured_at + _dt.timedelta(seconds=2)
    tokens = tuple(
        token
        for binding in probability.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
    )
    books = {
        token: {
            "asset_id": token,
            "hash": f"book-{token}",
            "tick_size": "0.01",
            "min_order_size": "5",
            "bids": [{"price": "0.20", "size": "100"}],
            "asks": [{"price": "0.30", "size": "100"}],
        }
        for token in tokens
    }

    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=lambda _tokens: pytest.fail("prefetched epoch must not refetch"),
        clock=lambda: finished_at,
        max_age=_dt.timedelta(seconds=30),
        metadata_overrides={},
        prefetched_books=books,
        prefetched_at_utc=captured_at,
    )

    assert epoch.captured_at_utc == captured_at
    assert len(epoch.assets) == len(tokens)
    assert {asset.token_id for asset in epoch.assets} == set(tokens)


def test_current_global_book_epoch_refreshes_only_newer_projected_token():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)
    captured_at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    tokens = tuple(
        token
        for binding in probability.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
    )

    def raw_book(token, ask):
        return {
            "asset_id": token,
            "hash": f"book-{token}-{ask}",
            "tick_size": "0.01",
            "min_order_size": "5",
            "bids": [{"price": "0.20", "size": "100"}],
            "asks": [{"price": ask, "size": "100"}],
        }

    initial_books = {token: raw_book(token, "0.30") for token in tokens}
    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=lambda _tokens: pytest.fail("prefetched epoch must not refetch"),
        clock=lambda: captured_at + _dt.timedelta(seconds=1),
        max_age=_dt.timedelta(seconds=30),
        metadata_overrides={},
        prefetched_books=initial_books,
        prefetched_at_utc=captured_at,
    )
    changed_token = tokens[0]
    changed_state = next(state for state in epoch.asset_states if state[4] == changed_token)
    snapshot_id = f"metadata-{changed_state[2]}-{changed_state[3]}"
    refreshed_at = captured_at + _dt.timedelta(seconds=5)

    refreshed, changed = universe.refresh_current_global_book_epoch_tokens(
        conn,
        epoch=epoch,
        projected_books={
            changed_token: (
                raw_book(changed_token, "0.40"),
                refreshed_at,
                snapshot_id,
            )
        },
        required_tokens=(changed_token,),
        checked_at_utc=refreshed_at + _dt.timedelta(seconds=1),
    )

    assert changed == 1
    assert refreshed.witness_identity != epoch.witness_identity
    refreshed_asset = next(
        asset for asset in refreshed.assets if asset.token_id == changed_token
    )
    assert refreshed_asset.captured_at_utc == refreshed_at
    assert refreshed_asset.curve.levels[0].price == Decimal("0.40")
    unchanged = {
        asset.token_id: asset.captured_at_utc
        for asset in refreshed.assets
        if asset.token_id != changed_token
    }
    assert unchanged == {
        asset.token_id: asset.captured_at_utc
        for asset in epoch.assets
        if asset.token_id != changed_token
    }


def test_global_book_prefetch_reads_complete_fresh_price_channel_projection():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            selected_outcome_token_id TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        """
    )
    checked_at = _dt.datetime(2026, 7, 17, 0, 12, 12, tzinfo=_dt.timezone.utc)
    rows = (
        ("condition-a", "yes-a", "snapshot-yes", "2026-07-17T00:12:11+00:00"),
        ("condition-a", "no-a", "snapshot-no", "2026-07-17T00:12:11.500000+00:00"),
    )
    for condition_id, token_id, snapshot_id, captured_at in rows:
        conn.execute(
            """
            INSERT INTO executable_market_snapshots
                (snapshot_id, selected_outcome_token_id, orderbook_depth_json,
                 min_tick_size, min_order_size, neg_risk, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                token_id,
                json.dumps(
                    {
                        "asset_id": token_id,
                        "hash": f"book-{token_id}",
                        "bids": [{"price": "0.20", "size": "100"}],
                        "asks": [
                            {
                                "price": "0.034" if token_id == "yes-a" else "0.30",
                                "size": "100",
                            }
                        ],
                    }
                ),
                "0.001" if token_id == "yes-a" else "0.01",
                "5",
                1,
                captured_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO executable_market_snapshot_latest
                (condition_id, selected_outcome_token_id, snapshot_id, freshness_deadline)
            VALUES (?, ?, ?, ?)
            """,
            (
                condition_id,
                token_id,
                snapshot_id,
                "2026-07-17T00:15:11+00:00",
            ),
        )

    projected = era._fresh_projected_global_books(
        conn,
        ("yes-a", "no-a"),
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    )

    assert projected is not None
    books, captured_at = projected
    assert set(books) == {"yes-a", "no-a"}
    assert books["yes-a"]["tick_size"] == "0.001"
    assert books["yes-a"]["min_order_size"] == "5"
    assert books["yes-a"]["neg_risk"] is True
    curve = universe._global_book_curve(
        family_key="Paris|2026-07-17|high",
        bin_id="35c",
        condition_id="condition-a",
        side="YES",
        token_id="yes-a",
        raw_book=books["yes-a"],
        metadata={
            "min_tick_size": "0.01",
            "min_order_size": "5",
            "fee_details_json": '{"fee_rate_fraction":0}',
        },
        captured_at_utc=captured_at,
        max_age=_dt.timedelta(minutes=3),
    )
    assert curve is not None
    assert curve.min_tick == Decimal("0.001")
    assert curve.levels[0].price == Decimal("0.034")
    assert captured_at == _dt.datetime(
        2026, 7, 17, 0, 12, 11, tzinfo=_dt.timezone.utc
    )

    conn.execute(
        "DELETE FROM executable_market_snapshot_latest "
        "WHERE selected_outcome_token_id = 'no-a'"
    )
    partial = era._projected_global_books(
        conn,
        ("yes-a", "no-a"),
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    )
    assert partial is not None
    assert set(partial[0]) == {"yes-a"}
    assert era._fresh_projected_global_books(
        conn,
        ("yes-a", "no-a"),
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    ) is None


def test_global_book_prefetch_reuses_latest_market_channel_depth_and_invalidates_stale_tick():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            selected_outcome_token_id TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            quote_seen_at TEXT NOT NULL,
            book_hash_before TEXT,
            depth_before_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_execution_feasibility_evidence_token_created
            ON execution_feasibility_evidence(token_id, created_at DESC);
        """
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES
            ('snapshot-a', 'yes-a', ?, '0.01', '5', 1,
             '2026-07-17T00:11:00+00:00')
        """,
        (
            json.dumps(
                {
                    "asset_id": "yes-a",
                    "bids": [{"price": "0.20", "size": "100"}],
                    "asks": [{"price": "0.30", "size": "100"}],
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_latest VALUES
            ('condition-a', 'yes-a', 'snapshot-a',
             '2026-07-17T00:15:00+00:00')
        """
    )
    channel_depth = {
        "bids": [{"price": "0.40", "size": "20"}],
        "asks": [{"price": "0.42", "size": "30"}],
    }
    channel_rows = (
        (
            "buy-row",
            "book-event",
            "condition-a",
            "yes-a",
            "2026-07-17T00:12:11+00:00",
            "book-hash",
            json.dumps(channel_depth),
            "2026-07-17T00:12:11.100000+00:00",
        ),
        (
            "sell-row",
            "book-event",
            "condition-a",
            "yes-a",
            "2026-07-17T00:12:11+00:00",
            "book-hash",
            None,
            "2026-07-17T00:12:11.200000+00:00",
        ),
    )
    conn.executemany(
        "INSERT INTO execution_feasibility_evidence VALUES (?,?,?,?,?,?,?,?)",
        channel_rows,
    )

    projected = era._projected_global_books(
        conn,
        ("yes-a",),
        checked_at=_dt.datetime(
            2026, 7, 17, 0, 12, 12, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    )

    assert projected is not None
    books, captured_at = projected
    assert books["yes-a"] == {
        **channel_depth,
        "asset_id": "yes-a",
        "hash": "book-hash",
        "tick_size": "0.01",
        "min_order_size": "5",
        "neg_risk": True,
    }
    assert captured_at == _dt.datetime(
        2026, 7, 17, 0, 12, 11, tzinfo=_dt.timezone.utc
    )
    projected_rows = era._projected_global_book_rows(
        conn,
        ("yes-a",),
        checked_at=_dt.datetime(
            2026, 7, 17, 0, 12, 12, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    )
    assert projected_rows is not None
    # Token-delta validation needs the metadata identity, not the quote event ID.
    assert projected_rows["yes-a"][2] == "snapshot-a"

    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES
            ('snapshot-b', 'yes-b', ?, '0.01', '5', 1,
             '2026-07-17T00:11:00+00:00')
        """,
        (
            json.dumps(
                {
                    "asset_id": "yes-b",
                    "bids": [{"price": "0.02", "size": "100"}],
                    "asks": [{"price": "0.04", "size": "100"}],
                }
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_latest VALUES
            ('condition-b', 'yes-b', 'snapshot-b',
             '2026-07-17T00:15:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence VALUES
            ('tick-row', 'tick-event', 'condition-b', 'yes-b',
             '2026-07-17T00:12:11+00:00', 'tick-book-hash', ?,
             '2026-07-17T00:12:11.100000+00:00')
        """,
        (
            json.dumps(
                {
                    "bids": [{"price": "0.021", "size": "20"}],
                    "asks": [{"price": "0.034", "size": "30"}],
                }
            ),
        ),
    )

    assert era._projected_global_book_rows(
        conn,
        ("yes-b",),
        checked_at=_dt.datetime(
            2026, 7, 17, 0, 12, 12, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    ) is None


def test_global_book_prefetch_newer_bba_invalidates_older_depth():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            selected_outcome_token_id TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            quote_seen_at TEXT NOT NULL,
            book_hash_before TEXT,
            depth_before_json TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_execution_feasibility_evidence_token_created
            ON execution_feasibility_evidence(token_id, created_at DESC);
        """
    )
    old_depth = {
        "asset_id": "yes-a",
        "bids": [{"price": "0.20", "size": "100"}],
        "asks": [{"price": "0.30", "size": "100"}],
    }
    conn.execute(
        """
        INSERT INTO executable_market_snapshots VALUES
            ('snapshot-a', 'yes-a', ?, '0.01', '5', 0,
             '2026-07-17T00:12:10+00:00')
        """,
        (json.dumps(old_depth),),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_latest VALUES
            ('condition-a', 'yes-a', 'snapshot-a',
             '2026-07-17T00:15:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence VALUES
            ('bba-row', 'bba-event', 'condition-a', 'yes-a',
             '2026-07-17T00:12:11+00:00', 'new-hash', NULL,
             '2026-07-17T00:12:11.100000+00:00')
        """
    )

    assert era._projected_global_book_rows(
        conn,
        ("yes-a",),
        checked_at=_dt.datetime(
            2026, 7, 17, 0, 12, 12, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    ) is None


def test_market_channel_continuity_cut_requires_current_matching_daemon(
    monkeypatch,
    tmp_path,
):
    checked_at = _dt.datetime(2026, 7, 17, 0, 20, tzinfo=_dt.timezone.utc)
    proof_path = tmp_path / "market-channel-continuity.json"
    heartbeat_path = tmp_path / "daemon-heartbeat-price-channel-ingest.json"
    proof_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channel": "market_channel",
                "connected": True,
                "connected_at": (
                    checked_at - _dt.timedelta(minutes=5)
                ).isoformat(),
                "observed_at": (
                    checked_at - _dt.timedelta(milliseconds=200)
                ).isoformat(),
                "pid": 42,
            }
        ),
        encoding="utf-8",
    )
    heartbeat_path.write_text(
        json.dumps(
            {
                "daemon": "price-channel-ingest",
                "alive_at": (checked_at - _dt.timedelta(seconds=30)).isoformat(),
                "pid": 42,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda name: tmp_path / name,
    )

    assert era._market_channel_continuity_cut(
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    ) == (
        checked_at - _dt.timedelta(minutes=5),
        checked_at - _dt.timedelta(milliseconds=200),
    )

    heartbeat_path.write_text(
        json.dumps(
            {
                "daemon": "price-channel-ingest",
                "alive_at": checked_at.isoformat(),
                "pid": 43,
            }
        ),
        encoding="utf-8",
    )
    assert era._market_channel_continuity_cut(
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    ) is None

    heartbeat_path.write_text(
        json.dumps(
            {
                "daemon": "price-channel-ingest",
                "alive_at": checked_at.isoformat(),
                "pid": 42,
            }
        ),
        encoding="utf-8",
    )
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["observed_at"] = (checked_at - _dt.timedelta(seconds=3)).isoformat()
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    assert era._market_channel_continuity_cut(
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    ) is None


def test_global_book_prefetch_uses_only_current_session_continuous_depth(
    monkeypatch,
):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            selected_outcome_token_id TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            min_tick_size TEXT NOT NULL,
            min_order_size TEXT NOT NULL,
            neg_risk INTEGER NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        CREATE TABLE execution_feasibility_latest (
            token_id TEXT NOT NULL,
            direction TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            condition_id TEXT NOT NULL,
            outcome_label TEXT NOT NULL,
            quote_seen_at TEXT NOT NULL,
            book_hash_before TEXT,
            best_bid_before REAL,
            best_ask_before REAL,
            depth_before_json TEXT,
            created_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            PRIMARY KEY (token_id, direction)
        );
        """
    )
    depth = {
        "bids": [{"price": "0.40", "size": "20"}],
        "asks": [{"price": "0.42", "size": "30"}],
    }
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?,?,?,?)",
        (
            "snapshot-a",
            "yes-a",
            json.dumps({"asset_id": "yes-a", **depth}),
            "0.01",
            "5",
            1,
            "2026-07-17T00:10:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?)",
        (
            "condition-a",
            "yes-a",
            "snapshot-a",
            "2026-07-17T00:30:00+00:00",
        ),
    )
    conn.execute(
        "INSERT INTO execution_feasibility_latest VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "yes-a",
            "buy_yes",
            "evidence-a",
            "event-a",
            "condition-a",
            "YES",
            "2026-07-17T00:16:00+00:00",
            "book-hash",
            0.40,
            0.42,
            json.dumps(depth),
            "2026-07-17T00:16:00+00:00",
            1,
        ),
    )
    checked_at = _dt.datetime(2026, 7, 17, 0, 20, tzinfo=_dt.timezone.utc)
    monkeypatch.setattr(
        era,
        "_market_channel_continuity_cut",
        lambda **_kwargs: (
            _dt.datetime(2026, 7, 17, 0, 15, tzinfo=_dt.timezone.utc),
            checked_at,
        ),
    )

    projected = era._projected_global_books(
        conn,
        ("yes-a",),
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    )
    assert projected is not None
    assert projected[1] == checked_at
    assert projected[0]["yes-a"]["hash"] == "book-hash"

    monkeypatch.setattr(
        era,
        "_market_channel_continuity_cut",
        lambda **_kwargs: (
            _dt.datetime(2026, 7, 17, 0, 17, tzinfo=_dt.timezone.utc),
            checked_at,
        ),
    )
    assert era._projected_global_books(
        conn,
        ("yes-a",),
        checked_at=checked_at,
        max_age=_dt.timedelta(minutes=3),
    ) is None


def test_global_book_prefetch_rejects_incomplete_or_expired_projection():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            selected_outcome_token_id TEXT NOT NULL,
            orderbook_depth_json TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE INDEX idx_snapshot_latest_selected_token_captured
            ON executable_market_snapshot_latest (
                selected_outcome_token_id,
                freshness_deadline DESC
            );
        """
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshots
            (snapshot_id, selected_outcome_token_id, orderbook_depth_json, captured_at)
        VALUES ('snapshot-yes', 'yes-a', ?, '2026-07-17T00:08:00+00:00')
        """,
        (json.dumps({"asset_id": "yes-a", "bids": [], "asks": []}),),
    )
    conn.execute(
        """
        INSERT INTO executable_market_snapshot_latest
            (condition_id, selected_outcome_token_id, snapshot_id, freshness_deadline)
        VALUES (
            'condition-a',
            'yes-a',
            'snapshot-yes',
            '2026-07-17T00:11:00+00:00'
        )
        """
    )

    assert era._fresh_projected_global_books(
        conn,
        ("yes-a", "no-a"),
        checked_at=_dt.datetime(
            2026, 7, 17, 0, 12, 12, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(minutes=3),
    ) is None


def test_current_global_book_epoch_excludes_stale_tradeability_symmetrically():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(
        probability,
        captured_at="2026-06-13T07:58:00+00:00",
        freshness_deadline="2026-06-13T07:59:00+00:00",
    )
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))

    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=lambda tokens: {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens
        },
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
    )

    assert epoch.assets == ()
    assert {state[3] for state in epoch.asset_states} == {"YES", "NO"}
    assert {state[5] for state in epoch.asset_states} == {
        "VENUE_METADATA_STALE"
    }


def _current_global_book_probability():
    family, proofs, payload = _corpus()[0]
    proofs = tuple(
        replace(
            proof,
            row={**proof.row, "captured_at": "2026-06-13T07:59:59+00:00"},
        )
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    result = bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        global_native_side_candidate_from_proof=era._full_depth_native_side_candidate_from_proof,
        require_global_probability_witness=True,
        global_probability_max_age=_dt.timedelta(seconds=30),
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )
    assert result.global_family is not None
    return result.global_family.probability_witness


def test_current_global_book_epoch_refreshes_one_newer_projected_token():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(
        probability,
        freshness_deadline="2026-06-13T08:01:00+00:00",
    )
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    initial_books = {
        token: {
            "asset_id": token,
            "bids": [{"price": "0.20", "size": "100"}],
            "asks": [{"price": "0.30", "size": "100"}],
        }
        for binding in probability.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
    }
    times = iter((at, at + _dt.timedelta(milliseconds=10)))
    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=lambda tokens: {token: initial_books[token] for token in tokens},
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
    )

    binding = probability.bindings[0]
    token = binding.yes_token_id
    sibling = binding.no_token_id
    projected_at = at + _dt.timedelta(seconds=2)
    projected_book = {
        "asset_id": token,
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.10", "size": "100"}],
    }
    snapshot_id = conn.execute(
        "SELECT snapshot_id FROM executable_market_snapshot_latest "
        "WHERE selected_outcome_token_id = ?",
        (token,),
    ).fetchone()[0]
    conn.execute(
        "UPDATE executable_market_snapshots "
        "SET orderbook_depth_json = ?, captured_at = ? WHERE snapshot_id = ?",
        (json.dumps(projected_book), projected_at.isoformat(), snapshot_id),
    )

    refreshed, changed = refresh_current_global_book_epoch_tokens(
        conn,
        epoch=epoch,
        projected_books={token: (projected_book, projected_at, snapshot_id)},
        required_tokens=(token,),
        checked_at_utc=projected_at + _dt.timedelta(milliseconds=10),
    )

    assert changed == 1
    assert refreshed.captured_at_utc == epoch.captured_at_utc
    assert refreshed.witness_identity != epoch.witness_identity
    refreshed_asset = next(asset for asset in refreshed.assets if asset.token_id == token)
    assert refreshed_asset.curve.levels[0].price == Decimal("0.10")
    assert next(asset for asset in refreshed.assets if asset.token_id == sibling) is next(
        asset for asset in epoch.assets if asset.token_id == sibling
    )


def test_current_global_book_epoch_rejects_older_projected_token_change():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    books = {
        token: {
            "asset_id": token,
            "bids": [{"price": "0.20", "size": "100"}],
            "asks": [{"price": "0.30", "size": "100"}],
        }
        for binding in probability.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
    }
    times = iter((at, at + _dt.timedelta(milliseconds=10)))
    epoch = capture_current_global_book_epoch(
        conn,
        probability_witnesses={probability.family_key: probability},
        get_books=lambda tokens: {token: books[token] for token in tokens},
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
    )
    token = probability.bindings[0].yes_token_id
    stale_book = {
        "asset_id": token,
        "bids": [{"price": "0.40", "size": "100"}],
        "asks": [{"price": "0.10", "size": "100"}],
    }
    snapshot_id = conn.execute(
        "SELECT snapshot_id FROM executable_market_snapshot_latest "
        "WHERE selected_outcome_token_id = ?",
        (token,),
    ).fetchone()[0]

    with pytest.raises(ValueError, match="GLOBAL_BOOK_TOKEN_DELTA_NOT_NEWER"):
        refresh_current_global_book_epoch_tokens(
            conn,
            epoch=epoch,
            projected_books={
                token: (stale_book, at - _dt.timedelta(seconds=1), snapshot_id)
            },
            required_tokens=(token,),
            checked_at_utc=at + _dt.timedelta(seconds=1),
        )


def test_current_global_book_epoch_rejects_one_missing_native_side():
    probability = _current_global_book_probability()
    conn = _global_book_metadata_conn(probability)
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))

    def incomplete_books(tokens):
        return {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens[:-1]
        }

    with pytest.raises(ValueError, match="GLOBAL_BOOK_RESPONSE_INCOMPLETE:1"):
        capture_current_global_book_epoch(
            conn,
            probability_witnesses={probability.family_key: probability},
            get_books=incomplete_books,
            clock=lambda: next(times),
            max_age=_dt.timedelta(seconds=30),
            batch_size=500,
        )


def test_current_global_book_epoch_overlaps_chunks_and_preserves_window():
    probability = _current_global_book_probability()
    tokens = [
        token
        for binding in probability.bindings
        for token in (binding.yes_token_id, binding.no_token_id)
    ]
    assert len(tokens) >= 4
    batch_size = max(1, len(tokens) // 4)
    caller_thread = threading.get_ident()
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    max_active = 0
    worker_threads = set()

    def books(chunk):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            worker_threads.add(threading.get_ident())
        try:
            barrier.wait(timeout=2)
            return {
                token: {
                    "asset_id": token,
                    "hash": f"book-{token}",
                    "tick_size": "0.01",
                    "min_order_size": "5",
                    "bids": [{"price": "0.20", "size": "100"}],
                    "asks": [{"price": "0.30", "size": "100"}],
                }
                for token in chunk
            }
        finally:
            with lock:
                active -= 1

    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))
    epoch = capture_current_global_book_epoch(
        _global_book_metadata_conn(probability),
        probability_witnesses={probability.family_key: probability},
        get_books=books,
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
        batch_size=batch_size,
        book_fetch_workers=4,
    )

    assert max_active == 4
    assert len(worker_threads) == 4
    assert caller_thread not in worker_threads
    assert len(epoch.assets) == len(tokens)
    assert epoch.captured_at_utc == at
    with pytest.raises(StopIteration):
        next(times)

    sequential_times = iter((at, at + _dt.timedelta(seconds=1)))
    sequential = capture_current_global_book_epoch(
        _global_book_metadata_conn(probability),
        probability_witnesses={probability.family_key: probability},
        get_books=lambda chunk: {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in chunk
        },
        clock=lambda: next(sequential_times),
        max_age=_dt.timedelta(seconds=30),
        batch_size=batch_size,
    )
    assert epoch.asset_states == sequential.asset_states
    assert epoch.witness_identity == sequential.witness_identity


def test_current_global_book_epoch_rejects_excessive_parallelism():
    probability = _current_global_book_probability()
    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)

    with pytest.raises(ValueError, match="GLOBAL_BOOK_FETCH_CONTRACT_INVALID"):
        capture_current_global_book_epoch(
            _global_book_metadata_conn(probability),
            probability_witnesses={probability.family_key: probability},
            get_books=lambda _tokens: {},
            clock=lambda: at,
            max_age=_dt.timedelta(seconds=30),
            book_fetch_workers=5,
        )


def test_current_global_book_epoch_one_chunk_stays_synchronous():
    probability = _current_global_book_probability()
    caller_thread = threading.get_ident()
    called_threads = []

    def books(tokens):
        called_threads.append(threading.get_ident())
        return {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [{"price": "0.20", "size": "100"}],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens
        }

    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))
    capture_current_global_book_epoch(
        _global_book_metadata_conn(probability),
        probability_witnesses={probability.family_key: probability},
        get_books=books,
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
        batch_size=500,
        book_fetch_workers=2,
    )

    assert called_threads == [caller_thread]


def test_current_global_book_epoch_rejects_parallel_chunk_error():
    probability = _current_global_book_probability()
    failed_token = probability.bindings[0].no_token_id

    def books(tokens):
        if failed_token in tokens:
            raise RuntimeError("chunk failure")
        return {
            token: {
                "asset_id": token,
                "hash": f"book-{token}",
                "tick_size": "0.01",
                "min_order_size": "5",
                "bids": [],
                "asks": [{"price": "0.30", "size": "100"}],
            }
            for token in tokens
        }

    at = _dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))
    with pytest.raises(RuntimeError, match="chunk failure"):
        capture_current_global_book_epoch(
            _global_book_metadata_conn(probability),
            probability_witnesses={probability.family_key: probability},
            get_books=books,
            clock=lambda: next(times),
            max_age=_dt.timedelta(seconds=30),
            batch_size=1,
            book_fetch_workers=2,
        )


def test_current_gamma_identity_fills_missing_no_without_changing_q():
    family, proofs, payload = _corpus()[0]
    proofs = tuple(
        replace(
            proof,
            row={**proof.row, "captured_at": "2026-06-13T07:59:59+00:00"},
        )
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    result = bridge.decide_family_via_spine(
        family=family,
        payload=payload,
        proofs=proofs,
        decision_time=_dt.datetime(2026, 6, 13, 8, 0, tzinfo=_dt.timezone.utc),
        native_side_candidate_from_proof=era._native_side_candidate_from_proof,
        global_native_side_candidate_from_proof=era._full_depth_native_side_candidate_from_proof,
        require_global_probability_witness=True,
        global_probability_max_age=_dt.timedelta(seconds=30),
        candidate_bin_id=era._candidate_bin_id,
        payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
        exposure_builder=era._robust_marginal_utility_exposure,
        baseline_usd_provider=lambda: Decimal("1000"),
        per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
        extra_exposure_by_bin_id=None,
    )
    assert result.global_family is not None
    original = result.global_family.probability_witness
    missing_bindings = tuple(
        OutcomeTokenBinding(
            bin_id=binding.bin_id,
            condition_id=binding.condition_id,
            yes_token_id=binding.yes_token_id,
            no_token_id=(None if index == 0 else binding.no_token_id),
        )
        for index, binding in enumerate(original.bindings)
    )
    missing_identity = joint_probability_witness_identity(
        family_key=original.family_key,
        bindings=missing_bindings,
        q_version=original.q_version,
        resolution_identity=original.resolution_identity,
        topology_identity=original.topology_identity,
        posterior_identity_hash=original.posterior_identity_hash,
        source_truth_identity=original.source_truth_identity,
        authority_certificate_hash=original.authority_certificate_hash,
        band_alpha=original.band_alpha,
        band_basis=original.band_basis,
        yes_q_samples=original.yes_q_samples,
        captured_at_utc=original.captured_at_utc,
    )
    missing = JointOutcomeProbabilityWitness(
        family_key=original.family_key,
        bindings=missing_bindings,
        yes_q_samples=original.yes_q_samples,
        q_version=original.q_version,
        resolution_identity=original.resolution_identity,
        topology_identity=original.topology_identity,
        posterior_identity_hash=original.posterior_identity_hash,
        source_truth_identity=original.source_truth_identity,
        authority_certificate_hash=original.authority_certificate_hash,
        band_alpha=original.band_alpha,
        band_basis=original.band_basis,
        captured_at_utc=original.captured_at_utc,
        max_age=original.max_age,
        witness_identity=missing_identity,
    )
    forecast = sqlite3.connect(":memory:")
    forecast.execute(
        "CREATE TABLE market_events (condition_id TEXT, market_slug TEXT, created_at TEXT)"
    )
    forecast.executemany(
        "INSERT INTO market_events VALUES (?,?,?)",
        [
            (binding.condition_id, "current-family-slug", "2026-07-10T08:00:00+00:00")
            for binding in missing.bindings
        ],
    )
    gamma_event = {
        "id": "gamma-event-current",
        "slug": "current-family-slug",
        "endDate": "2026-07-14T12:00:00Z",
        "markets": [
            {
                "conditionId": binding.condition_id,
                "questionID": f"question-{index}",
                "id": f"market-{index}",
                "question": f"Will the temperature be {index}C?",
                "clobTokenIds": [binding.yes_token_id, original.bindings[index].no_token_id],
                "outcomes": ["Yes", "No"],
                "outcomePrices": ["0.5", "0.5"],
                "acceptingOrders": True,
                "enableOrderBook": True,
                "active": True,
                "closed": False,
                "feeSchedule": {
                    "exponent": 1,
                    "rate": 0.05,
                    "takerOnly": True,
                    "rebateRate": 0.25,
                },
                "feeType": "weather",
                "orderPriceMinTickSize": "0.01",
                "orderMinSize": "5",
            }
            for index, binding in enumerate(missing.bindings)
        ]
    }

    from src.engine.global_auction_universe import bind_current_global_probability_tokens

    gamma_metadata = {}
    rebound = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: gamma_event if slug == "current-family-slug" else None,
        metadata_sink=gamma_metadata,
    )[missing.family_key]

    assert rebound.bindings[0].no_token_id == original.bindings[0].no_token_id
    assert rebound.sample_matrix_identity == missing.sample_matrix_identity
    assert rebound.q_version == missing.q_version
    assert rebound.witness_identity != missing.witness_identity
    assert rebound.family_binding_identity != missing.family_binding_identity
    assert all(
        getattr(rebound, field) == getattr(missing, field)
        for field in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    )
    assert "family_binding_identity" not in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    assert "authority_certificate_hash" not in era._GLOBAL_PROBABILITY_CONTENT_FIELDS
    assert gamma_metadata[
        (rebound.bindings[0].condition_id, rebound.bindings[0].no_token_id)
    ]["fee_details_json"]
    assert {
        row["event_id"] for row in gamma_metadata.values()
    } == {"current-family-slug"}
    assert {
        row["gamma_market_id"] for row in gamma_metadata.values()
    } == {f"market-{index}" for index in range(len(original.bindings))}

    complete_calls = []
    closed_metadata = {}
    closed_event = {
        **gamma_event,
        "markets": [
            {**market, "closed": True, "acceptingOrders": False}
            for market in gamma_event["markets"]
        ],
    }
    complete = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={original.family_key: original},
        get_gamma_event=lambda slug: (
            complete_calls.append(slug) or closed_event
        ),
        metadata_sink=closed_metadata,
    )[original.family_key]
    assert complete.witness_identity == original.witness_identity
    assert complete.bindings == original.bindings
    assert complete.sample_matrix_identity == original.sample_matrix_identity
    assert complete_calls == ["current-family-slug"]
    assert len(closed_metadata) == 2 * len(original.bindings)
    assert {row["accepting_orders"] for row in closed_metadata.values()} == {False}

    batch_calls = []
    batch_metadata = {}
    batch_event = {
        key: value for key, value in gamma_event.items() if key != "markets"
    }
    batch_markets = tuple(
        {**market, "events": [batch_event]}
        for market in gamma_event["markets"]
    )
    batched = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={original.family_key: original},
        get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
        get_gamma_markets=lambda condition_ids: (
            batch_calls.append(tuple(condition_ids)) or batch_markets
        ),
        metadata_sink=batch_metadata,
    )[original.family_key]
    assert batch_calls == [tuple(binding.condition_id for binding in original.bindings)]
    assert batched.witness_identity == original.witness_identity
    assert batched.bindings == original.bindings
    assert batched.sample_matrix_identity == original.sample_matrix_identity
    assert batch_metadata == gamma_metadata
    assert {
        row["market_end_at"] for row in batch_metadata.values()
    } == {"2026-07-14T12:00:00Z"}
    partial_batch_calls = []
    partial_batch_metadata = {}
    partial_batch = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={original.family_key: original},
        get_gamma_event=lambda slug: (
            partial_batch_calls.append(slug) or closed_event
        ),
        get_gamma_markets=lambda _condition_ids: batch_markets[:-1],
        metadata_sink=partial_batch_metadata,
    )[original.family_key]
    assert partial_batch_calls == ["current-family-slug"]
    assert partial_batch.witness_identity == original.witness_identity
    assert partial_batch.bindings == original.bindings
    assert len(partial_batch_metadata) == 2 * len(original.bindings)
    assert {row["accepting_orders"] for row in partial_batch_metadata.values()} == {
        False
    }

    with pytest.raises(ValueError, match="GLOBAL_CURRENT_GAMMA_MARKETS_INCOMPLETE"):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={original.family_key: original},
            get_gamma_markets=lambda _condition_ids: batch_markets[:-1],
            metadata_sink={},
        )
    with pytest.raises(ValueError, match="GLOBAL_CURRENT_GAMMA_MARKET_AMBIGUOUS"):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={original.family_key: original},
            get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
            get_gamma_markets=lambda _condition_ids: (*batch_markets, batch_markets[0]),
            metadata_sink={},
        )
    with pytest.raises(ValueError, match="GLOBAL_CURRENT_GAMMA_MARKET_INVALID"):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={original.family_key: original},
            get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
            get_gamma_markets=lambda _condition_ids: (*batch_markets, None),
            metadata_sink={},
        )
    malformed_batches = (
        ((*batch_markets, {}), "GLOBAL_CURRENT_GAMMA_MARKET_INVALID"),
        (
            ({key: value for key, value in batch_markets[0].items() if key != "events"},
             *batch_markets[1:]),
            "GLOBAL_CURRENT_GAMMA_EVENT_INVALID",
        ),
        (
            ({**batch_markets[0], "events": batch_event}, *batch_markets[1:]),
            "GLOBAL_CURRENT_GAMMA_EVENT_INVALID",
        ),
        (
            ({**batch_markets[0], "events": [{**batch_event, "id": ""}]},
             *batch_markets[1:]),
            "GLOBAL_CURRENT_GAMMA_EVENT_INVALID",
        ),
    )
    for malformed_batch, rejection in malformed_batches:
        with pytest.raises(ValueError, match=rejection):
            bind_current_global_probability_tokens(
                forecast,
                probability_witnesses={original.family_key: original},
                get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
                get_gamma_markets=lambda _condition_ids, rows=malformed_batch: rows,
                metadata_sink={},
            )
    conflicting_events = (
        {**batch_markets[0], "events": [{"id": "different-event"}]},
        *batch_markets[1:],
    )
    with pytest.raises(
        ValueError, match="GLOBAL_CURRENT_GAMMA_EVENT_IDENTITY_AMBIGUOUS"
    ):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={original.family_key: original},
            get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
            get_gamma_markets=lambda _condition_ids: conflicting_events,
            metadata_sink={},
        )
    conflicting_event_metadata = (
        {
            **batch_markets[0],
            "events": [{**batch_event, "endDate": "2026-07-15T12:00:00Z"}],
        },
        *batch_markets[1:],
    )
    metadata_calls = []

    def _metadata_skew_then_current(condition_ids):
        metadata_calls.append(tuple(condition_ids))
        return conflicting_event_metadata if len(metadata_calls) == 1 else batch_markets

    recaptured_metadata = {}
    recovered = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={original.family_key: original},
        get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
        get_gamma_markets=_metadata_skew_then_current,
        metadata_sink=recaptured_metadata,
    )[original.family_key]
    assert metadata_calls == [
        tuple(binding.condition_id for binding in original.bindings),
        tuple(binding.condition_id for binding in original.bindings),
    ]
    assert recovered.witness_identity == original.witness_identity
    assert recovered.bindings == original.bindings
    assert recovered.sample_matrix_identity == original.sample_matrix_identity
    assert recaptured_metadata == batch_metadata
    with pytest.raises(
        ValueError, match="GLOBAL_CURRENT_GAMMA_EVENT_METADATA_AMBIGUOUS"
    ):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={original.family_key: original},
            get_gamma_event=lambda _slug: pytest.fail("per-event Gamma fallback used"),
            get_gamma_markets=lambda _condition_ids: conflicting_event_metadata,
            metadata_sink={},
        )

    at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    times = iter((at, at + _dt.timedelta(seconds=1)))
    closed_epoch = capture_current_global_book_epoch(
        _global_book_metadata_conn(original),
        probability_witnesses={original.family_key: original},
        get_books=lambda _tokens: pytest.fail(
            "closed current Gamma legs must not require a CLOB book"
        ),
        clock=lambda: next(times),
        max_age=_dt.timedelta(seconds=30),
        metadata_overrides=closed_metadata,
    )
    assert closed_epoch.assets == ()
    assert {state[3] for state in closed_epoch.asset_states} == {"YES", "NO"}
    assert {state[5] for state in closed_epoch.asset_states} == {
        "VENUE_NOT_EXECUTABLE"
    }

    gamma_calls = []
    local = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: gamma_calls.append(slug),
        trade_conn=_global_book_metadata_conn(
            original,
            captured_at="2026-07-10T07:59:00+00:00",
            freshness_deadline="2026-07-10T08:00:30+00:00",
        ),
        checked_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
    )[missing.family_key]
    assert gamma_calls == []
    assert local.bindings == original.bindings
    assert local.sample_matrix_identity == missing.sample_matrix_identity

    local_metadata = {}
    local_complete = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={original.family_key: original},
        get_gamma_event=lambda _slug: pytest.fail("fresh local metadata missed"),
        get_gamma_markets=lambda _conditions: pytest.fail(
            "fresh local metadata missed"
        ),
        trade_conn=_global_book_metadata_conn(
            original,
            captured_at="2026-07-10T07:59:00+00:00",
            freshness_deadline="2026-07-10T08:00:30+00:00",
        ),
        checked_at_utc=_dt.datetime(
            2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc
        ),
        metadata_sink=local_metadata,
    )[original.family_key]
    assert local_complete.witness_identity == original.witness_identity
    assert len(local_metadata) == 2 * len(original.bindings)
    assert {row["captured_at"] for row in local_metadata.values()} == {
        "2026-07-10T07:59:00+00:00"
    }

    stale_metadata_calls = []
    stale_metadata = {}
    stale_remote = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={original.family_key: original},
        get_gamma_event=lambda _slug: pytest.fail("batch Gamma path expected"),
        get_gamma_markets=lambda condition_ids: (
            stale_metadata_calls.append(tuple(condition_ids)) or batch_markets
        ),
        trade_conn=_global_book_metadata_conn(
            original,
            captured_at="2026-07-10T07:59:00+00:00",
            freshness_deadline="2026-07-10T08:00:30+00:00",
        ),
        checked_at_utc=_dt.datetime(
            2026, 7, 10, 8, 1, tzinfo=_dt.timezone.utc
        ),
        metadata_sink=stale_metadata,
    )[original.family_key]
    assert stale_remote.witness_identity == original.witness_identity
    assert stale_metadata_calls == [
        tuple(binding.condition_id for binding in original.bindings)
    ]
    assert stale_metadata == batch_metadata

    stale_calls = []
    stale_fallback = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: stale_calls.append(slug) or gamma_event,
        trade_conn=_global_book_metadata_conn(
            original,
            captured_at="2026-07-10T07:59:00+00:00",
            freshness_deadline="2026-07-10T08:00:30+00:00",
        ),
        checked_at_utc=_dt.datetime(2026, 7, 10, 8, 1, tzinfo=_dt.timezone.utc),
    )[missing.family_key]
    assert stale_calls == ["current-family-slug"]
    assert stale_fallback.bindings == original.bindings

    partial = _global_book_metadata_conn(
        original,
        captured_at="2026-07-10T07:59:00+00:00",
        freshness_deadline="2026-07-10T08:00:30+00:00",
    )
    missing_condition = missing.bindings[0].condition_id
    partial.execute(
        "DELETE FROM executable_market_snapshot_latest WHERE condition_id = ?",
        (missing_condition,),
    )
    partial_calls = []
    fallback = bind_current_global_probability_tokens(
        forecast,
        probability_witnesses={missing.family_key: missing},
        get_gamma_event=lambda slug: (
            partial_calls.append(slug) or gamma_event
        ),
        trade_conn=partial,
        checked_at_utc=_dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
    )[missing.family_key]
    assert partial_calls == ["current-family-slug"]
    assert fallback.bindings == original.bindings

    ambiguous = _global_book_metadata_conn(
        original,
        captured_at="2026-07-10T07:59:00+00:00",
        freshness_deadline="2026-07-10T08:00:30+00:00",
    )
    ambiguous.execute(
        """
        INSERT INTO executable_market_snapshots
            SELECT 'conflicting-topology', gamma_market_id, event_id, condition_id,
                   'conflicting-selected', 'conflicting-yes', 'conflicting-no',
                   enable_orderbook, active,
               closed, accepting_orders, fee_details_json, min_tick_size,
               min_order_size, captured_at, freshness_deadline,
               tradeability_status_json, orderbook_depth_json
          FROM executable_market_snapshots
         WHERE condition_id = ?
         LIMIT 1
        """,
        (missing_condition,),
    )
    ambiguous.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?,?)",
        (
            missing_condition,
            "conflicting-selected",
            "conflicting-topology",
            "conflicting-yes",
            "conflicting-no",
        ),
    )
    with pytest.raises(
        ValueError,
        match=f"GLOBAL_LOCAL_TOKEN_IDENTITY_AMBIGUOUS:{missing_condition}",
    ):
        bind_current_global_probability_tokens(
            forecast,
            probability_witnesses={missing.family_key: missing},
            get_gamma_event=lambda _slug: gamma_event,
            trade_conn=ambiguous,
            checked_at_utc=_dt.datetime(
                2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc
            ),
        )


def test_global_scope_is_independent_of_the_reactor_page_and_current_q_identity():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    first = _global_scope_event(city="Chicago", source_run_id="posterior-chicago-a")
    second = _global_scope_event(city="London", source_run_id="posterior-london-a")

    scope = current_global_auction_scope_from_events(
        (first, second),
        captured_at_utc=decision_at,
    )
    reactor_page = current_global_auction_scope_from_events(
        (first,),
        captured_at_utc=decision_at,
    )
    updated = current_global_auction_scope_from_events(
        (
            _global_scope_event(
                city="Chicago", source_run_id="posterior-chicago-new"
            ),
            second,
        ),
        captured_at_utc=decision_at,
    )

    assert len(scope.family_keys) == 2
    assert set(reactor_page.family_keys) < set(scope.family_keys)
    assert reactor_page.scope_identity != scope.scope_identity
    assert updated.family_keys == scope.family_keys
    assert updated.scope_identity != scope.scope_identity
    assert set(scope.resolution_at_by_family.values()) == {
        _dt.datetime(2026, 7, 12, tzinfo=_dt.timezone.utc)
    }


def test_global_scope_decodes_each_immutable_event_once(monkeypatch):
    events = (
        _global_scope_event(city="Chicago", source_run_id="posterior-chicago"),
        _global_scope_event(city="London", source_run_id="posterior-london"),
    )
    real_loads = json.loads
    decoded = 0

    def _counting_loads(value):
        nonlocal decoded
        decoded += 1
        return real_loads(value)

    monkeypatch.setattr(universe.json, "loads", _counting_loads)

    scope = current_global_auction_scope_from_events(
        events,
        captured_at_utc=_dt.datetime(
            2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc
        ),
    )

    assert len(scope.events) == 2
    assert decoded == 2


def test_global_scope_identity_binds_settlement_timezone_horizon():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    utc = current_global_auction_scope_from_events(
        (
            _global_scope_event(
                city="Alpha",
                source_run_id="posterior-current",
                city_timezone="UTC",
            ),
        ),
        captured_at_utc=decision_at,
    )
    auckland = current_global_auction_scope_from_events(
        (
            _global_scope_event(
                city="Alpha",
                source_run_id="posterior-current",
                city_timezone="Pacific/Auckland",
            ),
        ),
        captured_at_utc=decision_at,
    )

    assert utc.family_keys == auckland.family_keys
    assert utc.scope_identity != auckland.scope_identity
    assert next(iter(utc.resolution_at_by_family.values())) == _dt.datetime(
        2026, 7, 12, tzinfo=_dt.timezone.utc
    )
    assert next(iter(auckland.resolution_at_by_family.values())) == _dt.datetime(
        2026, 7, 11, 12, tzinfo=_dt.timezone.utc
    )


def test_global_candidate_endowment_projects_correlated_family_holdings_exactly():
    at = _dt.datetime(2026, 7, 14, 8, 0, tzinfo=_dt.timezone.utc)
    identity = portfolio_wealth_identity(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions-current",
        wealth_floor_usd=Decimal("100"),
        wealth_ceiling_usd=Decimal("135"),
        spendable_cash_usd=Decimal("100"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=at,
    )
    wealth = PortfolioWealthWitness(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions-current",
        wealth_floor_usd=Decimal("100"),
        wealth_ceiling_usd=Decimal("135"),
        spendable_cash_usd=Decimal("100"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=at,
        max_age=_dt.timedelta(seconds=1),
        witness_identity=identity,
    )
    holdings = SimpleNamespace(
        family_key="family",
        ledger_snapshot_id="ledger-current",
        holdings=(
            SimpleNamespace(bin_id="a", side="YES", token_id="yes-a", shares=Decimal("10")),
            SimpleNamespace(bin_id="b", side="NO", token_id="no-b", shares=Decimal("20")),
            SimpleNamespace(bin_id="c", side="NO", token_id="no-c", shares=Decimal("5")),
        ),
    )
    endowment = _candidate_portfolio_endowment(
        SimpleNamespace(
            family_key="family",
            bin_id="c",
            side="NO",
            token_id="no-c",
        ),
        probability_witness=SimpleNamespace(bin_ids=("a", "b", "c")),
        holdings_snapshot=holdings,
        wealth_witness=wealth,
    )

    # If NO-c loses, outcome c still pays the existing NO-b holding ($20).
    # If NO-c wins, outcome a is the family maximum ($35), while impossible
    # simultaneous family payouts are removed from the global ceiling first.
    assert endowment.loss_wealth_floor_usd == Decimal("120")
    assert endowment.win_wealth_ceiling_usd == Decimal("135")
    assert endowment.current_token_shares == Decimal("5")
    assert endowment.ledger_snapshot_id == "ledger-current"

    yes_endowment = _candidate_portfolio_endowment(
        SimpleNamespace(
            family_key="family",
            bin_id="a",
            side="YES",
            token_id="yes-a",
        ),
        probability_witness=SimpleNamespace(bin_ids=("a", "b", "c")),
        holdings_snapshot=holdings,
        wealth_witness=wealth,
    )
    assert yes_endowment.loss_wealth_floor_usd == Decimal("105")
    assert yes_endowment.win_wealth_ceiling_usd == Decimal("135")
    assert yes_endowment.current_token_shares == Decimal("10")


def test_global_selection_endowment_uses_same_chain_balance_as_wealth_witness():
    """A just-filled token cannot be sized again from a stale position projection."""

    @dataclass(frozen=True)
    class Prepared:
        probability_witness: object
        holdings_snapshot: object | None = None

    prepared = Prepared(
        probability_witness=SimpleNamespace(
            family_key="family",
            bindings=(
                SimpleNamespace(
                    bin_id="bin-a",
                    condition_id="condition-a",
                    yes_token_id="yes-a",
                    no_token_id="no-a",
                ),
            ),
        )
    )
    stale_position = SimpleNamespace(
        trade_id="position-a",
        position_id="position-a",
        condition_id="condition-a",
        direction="buy_no",
        token_id="yes-a",
        no_token_id="no-a",
        chain_shares=Decimal("40.5"),
        chain_state="synced",
        chain_verified_at="2026-07-17T05:43:00+00:00",
        state="entered",
    )
    at = _dt.datetime(2026, 7, 17, 5, 44, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(
        captured_at=at,
        ctf={"no-a": 49_500_000},
    )
    portfolio = PortfolioState(
        positions=[stale_position],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    wealth = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=at,
        max_age=_dt.timedelta(seconds=10),
        portfolio_state=portfolio,
    )

    rebound = global_batch_runtime._bind_selection_holdings(
        {"event-a": prepared},
        portfolio_state=portfolio,
        wealth_witness=wealth,
    )

    holding = rebound["event-a"].holdings_snapshot.holdings[0]
    assert wealth.native_holdings_micro == (("no-a", 49_500_000),)
    assert holding.token_id == "no-a"
    assert holding.shares == Decimal("49.5")


def test_global_selection_counts_open_entry_without_granting_sell_inventory():
    """A durable BUY commitment consumes Kelly target before chain projection."""

    @dataclass(frozen=True)
    class Prepared:
        probability_witness: object
        holdings_snapshot: object | None = None

    bindings = (
        SimpleNamespace(
            bin_id="bin-a",
            condition_id="condition-a",
            yes_token_id="yes-a",
            no_token_id="no-a",
        ),
        SimpleNamespace(
            bin_id="bin-b",
            condition_id="condition-b",
            yes_token_id="yes-b",
            no_token_id="no-b",
        ),
    )
    prepared = Prepared(
        probability_witness=SimpleNamespace(
            family_key="family",
            bindings=bindings,
        )
    )
    position = SimpleNamespace(
        trade_id="position-a",
        position_id="position-a",
        condition_id="condition-a",
        direction="buy_no",
        token_id="yes-a",
        no_token_id="no-a",
        shares=Decimal("31.6"),
        chain_shares=Decimal("31.6"),
        chain_state="synced",
        chain_verified_at="2026-07-17T05:43:00+00:00",
        state="entered",
    )
    at = _dt.datetime(2026, 7, 17, 5, 44, 34, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=at)
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)",
        (
            "command-a",
            "position-a",
            "no-a",
            "BUY",
            7.5,
            0.56,
            "ENTRY",
            "POST_ACKED",
        ),
    )
    conn.execute(
        "INSERT INTO entry_exposure_obligations VALUES (?,?,?,?,?,?,?)",
        (
            "command-a",
            "OPEN",
            "no-a",
            7.5,
            4.2,
            0,
            "2026-07-17T05:44:27+00:00",
        ),
    )
    portfolio = PortfolioState(
        positions=[position],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    wealth = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=at,
        max_age=_dt.timedelta(seconds=10),
        portfolio_state=portfolio,
    )
    rebound = global_batch_runtime._bind_selection_holdings(
        {"event-a": prepared},
        portfolio_state=portfolio,
        wealth_witness=wealth,
    )
    snapshot = rebound["event-a"].holdings_snapshot
    endowment = _candidate_portfolio_endowment(
        SimpleNamespace(
            family_key="family",
            bin_id="bin-a",
            side="NO",
            token_id="no-a",
        ),
        probability_witness=SimpleNamespace(bin_ids=("bin-a", "bin-b")),
        holdings_snapshot=snapshot,
        wealth_witness=wealth,
    )

    assert wealth.native_holdings_micro == (("no-a", 31_600_000),)
    assert wealth.pending_entry_endowments_micro == (
        ("command-a", "no-a", 7_500_000),
    )
    assert snapshot.holdings[0].shares == Decimal("31.6")
    assert snapshot.pending_endowments[0].shares == Decimal("7.5")
    assert endowment.current_token_shares == Decimal("39.1")

    conn.execute(
        "UPDATE venue_commands SET state = 'FILLED' WHERE command_id = 'command-a'"
    )
    conn.execute(
        "INSERT INTO venue_command_events VALUES (?,?,?)",
        ("command-a", "FILL_CONFIRMED", "2026-07-17T05:44:30+00:00"),
    )
    position.shares = Decimal("39.1")
    position.chain_shares = Decimal("39.1")
    position.chain_verified_at = "2026-07-17T05:44:42+00:00"
    unresolved = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=at + _dt.timedelta(seconds=10),
        max_age=_dt.timedelta(seconds=10),
        portfolio_state=portfolio,
    )
    assert unresolved.pending_entry_endowments_micro == (
        ("command-a", "no-a", 7_500_000),
    )

    conn.execute(
        "UPDATE entry_exposure_obligations SET status = 'RESOLVED' "
        "WHERE command_id = 'command-a'"
    )
    represented = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=at + _dt.timedelta(seconds=10),
        max_age=_dt.timedelta(seconds=10),
        portfolio_state=portfolio,
    )
    represented_snapshot = global_batch_runtime._bind_selection_holdings(
        {"event-a": prepared},
        portfolio_state=portfolio,
        wealth_witness=represented,
    )["event-a"].holdings_snapshot

    assert represented.native_holdings_micro == (("no-a", 39_100_000),)
    assert represented.pending_entry_endowments_micro == ()
    assert represented_snapshot.holdings[0].shares == Decimal("39.1")
    assert represented_snapshot.pending_endowments == ()


def test_two_prepared_families_choose_one_globally_unique_order():
    family, proofs, payload = _corpus()[0]
    decision_at = _dt.datetime(2026, 6, 13, 12, 0, tzinfo=_dt.timezone.utc)
    captured_at = "2026-06-13T11:59:59.900000+00:00"
    proofs = tuple(
        replace(proof, row={**proof.row, "captured_at": captured_at})
        for proof in proofs
    )
    payload = _payload_with_joint_samples(proofs, payload, draws=400)
    current_scope = current_global_auction_scope_from_events(
        (
            _global_scope_event(
                city="Chicago", source_run_id="posterior-chicago-current"
            ),
            _global_scope_event(
                city="London", source_run_id="posterior-london-current"
            ),
        ),
        captured_at_utc=decision_at,
    )

    prepared_by_event = {}
    restore = _set_flag(False)
    try:
        for suffix, family_key in zip(("a", "b"), current_scope.family_keys):
            scoped_family = replace(
                family,
                family_id=family_key,
                event_id=f"event-{suffix}",
            )
            result = bridge.decide_family_via_spine(
                family=scoped_family,
                payload=payload,
                proofs=proofs,
                decision_time=decision_at,
                native_side_candidate_from_proof=era._native_side_candidate_from_proof,
                global_native_side_candidate_from_proof=(
                    era._full_depth_native_side_candidate_from_proof
                ),
                require_global_probability_witness=True,
                global_probability_max_age=_dt.timedelta(seconds=1),
                candidate_bin_id=era._candidate_bin_id,
                payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
                exposure_builder=era._robust_marginal_utility_exposure,
                baseline_usd_provider=lambda: Decimal("1000"),
                per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs),
                extra_exposure_by_bin_id=None,
            )
            assert result.global_family is not None
            prepared_by_event[f"event-{suffix}"] = result.global_family
    finally:
        restore()

    prepared_by_event = global_batch_runtime._bind_selection_holdings(
        prepared_by_event,
        portfolio_state=SimpleNamespace(positions=()),
        wealth_witness=SimpleNamespace(
            ledger_snapshot_id="ledger-current",
            native_holdings_micro=(),
        ),
    )
    assets = tuple(
        CurrentGlobalBookAsset(
            family_key=prepared.probability_witness.family_key,
            bin_id=seed.native_candidate.bin_id,
            condition_id=seed.native_candidate.condition_id,
            gamma_market_id=f"gamma-{seed.native_candidate.condition_id}",
            market_event_id=f"market-event-{prepared.probability_witness.family_key}",
            side=seed.native_candidate.side,
            token_id=seed.native_candidate.token_id,
            curve=seed.native_candidate.executable_cost_curve,
            captured_at_utc=decision_at,
        )
        for prepared in prepared_by_event.values()
        for seed in prepared.candidate_seeds
    )
    asset_states = tuple(
        (
            asset.family_key,
            asset.bin_id,
            asset.condition_id,
            asset.side,
            asset.token_id,
            "EXECUTABLE",
            asset.curve.book_hash,
            asset.market_event_id,
            asset.gamma_market_id,
        )
        for asset in assets
    )
    book_venue_identity = current_global_book_epoch_identity(
        asset_states=asset_states,
        captured_at_utc=decision_at,
    )
    book_epoch = CurrentGlobalBookEpoch(
        assets=assets,
        asset_states=asset_states,
        captured_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=1),
        witness_identity=book_venue_identity,
    )
    capital_scopes = []

    def current_capital_limit(
        candidate,
        gamma_market_id,
        market_event_id,
        owner_event_id,
    ):
        capital_scopes.append(
            (
                candidate.condition_id,
                gamma_market_id,
                market_event_id,
                owner_event_id,
            )
        )
        return Decimal("100")

    wealth_identity = portfolio_wealth_identity(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions-current",
        wealth_floor_usd=Decimal("1000"),
        wealth_ceiling_usd=Decimal("1000"),
        spendable_cash_usd=Decimal("1000"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=decision_at,
    )
    wealth = PortfolioWealthWitness(
        ledger_snapshot_id="ledger-current",
        position_set_hash="positions-current",
        wealth_floor_usd=Decimal("1000"),
        wealth_ceiling_usd=Decimal("1000"),
        spendable_cash_usd=Decimal("1000"),
        reservations_usd=Decimal("0"),
        collateral_authority="CHAIN",
        captured_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=1),
        witness_identity=wealth_identity,
    )
    probabilities = {
        prepared.probability_witness.family_key: prepared.probability_witness
        for prepared in prepared_by_event.values()
    }
    venue_identity = "current-venue-universe"

    auction_kwargs = dict(
        selection_epoch_identity="selection-epoch-current",
        selection_cut_at_utc=decision_at,
        current_scope=current_scope,
        current_scope_identity_resolver=lambda: current_scope.scope_identity,
        venue_universe_identity=venue_identity,
        current_venue_universe_identity_resolver=lambda: venue_identity,
        universe_max_age=_dt.timedelta(seconds=1),
        current_probability_resolver=lambda key: (
            CurrentFamilyProbabilityAuthority.from_witness(probabilities[key])
        ),
        current_execution_resolver=lambda candidate: CurrentExecutionAuthority(
            token_id=candidate.token_id,
            side=candidate.side,
            book_snapshot_id=candidate.book_snapshot_id,
            execution_curve_identity=candidate.execution_curve_identity,
        ),
        current_wealth_identity_resolver=lambda: wealth.economic_identity,
        wealth_witness=wealth,
        capital_limit_usd=Decimal("100"),
        decision_at_utc=decision_at,
    )
    selected = select_prepared_global_auction(
        prepared_by_event,
        **auction_kwargs,
    )
    assert selected.decision.candidate is not None, selected.decision.no_trade_reason
    fallthrough = select_prepared_global_auction(
        prepared_by_event,
        preflight_excluded_by_family={
            selected.decision.candidate.family_key: "candidate-local-block"
        },
        **auction_kwargs,
    )
    partial = select_prepared_global_auction(
        {"event-a": prepared_by_event["event-a"]},
        **auction_kwargs,
    )

    assert selected.winner_event_id in prepared_by_event
    assert selected.actuation is not None
    assert selected.actuation.decision == selected.decision
    assert selected.actuation.winner_event_id == selected.winner_event_id
    assert selected.actuation.universe_witness_identity
    assert selected.actuation.wealth_witness_identity == wealth.witness_identity
    assert selected.actuation.selection_epoch_identity == "selection-epoch-current"
    assert selected.actuation.selection_cut_at_utc == decision_at
    later_actuation_identity = global_single_order_actuation_identity(
        decision=selected.decision,
        winner_event_id=selected.winner_event_id,
        universe_witness_identity=selected.actuation.universe_witness_identity,
        wealth_witness_identity=selected.actuation.wealth_witness_identity,
        selection_epoch_identity=selected.actuation.selection_epoch_identity,
        selection_cut_at_utc=selected.actuation.selection_cut_at_utc,
        decision_at_utc=decision_at + _dt.timedelta(seconds=30),
    )
    assert later_actuation_identity != selected.actuation.actuation_identity
    assert selected.actuation.economic_identity == global_single_order_economic_identity(
        decision=selected.decision,
        probability_witness=selected.actuation.probability_witness,
        wealth_economic_identity=wealth.economic_identity,
    )
    assert fallthrough.decision.candidate is not None
    assert (
        fallthrough.decision.candidate.family_key
        != selected.decision.candidate.family_key
    )
    assert fallthrough.winner_event_id != selected.winner_event_id
    assert partial.decision.candidate is None
    assert partial.actuation is None
    assert partial.decision.no_trade_reason == "GLOBAL_FEASIBLE_SET_INCOMPLETE"

    book_selected = select_prepared_global_auction(
        prepared_by_event,
        **{
            **auction_kwargs,
            "venue_universe_identity": book_venue_identity,
            "current_venue_universe_identity_resolver": lambda: book_venue_identity,
            "book_epoch": book_epoch,
            "current_capital_limit_resolver": current_capital_limit,
        },
    )
    assert book_selected.decision.candidate is not None
    assert capital_scopes
    assert all(
        gamma_market_id == f"gamma-{condition_id}"
        for condition_id, gamma_market_id, _, _ in capital_scopes
    )
    all_ids = {
        global_candidate_from_native(
            seed.native_candidate,
            probability_witness=prepared.probability_witness,
            ledger_snapshot_id=wealth.ledger_snapshot_id,
            book_captured_at_utc=seed.book_captured_at_utc,
        ).candidate_id
        for prepared in prepared_by_event.values()
        for seed in prepared.candidate_seeds
    }
    assert len(all_ids) == sum(
        len(prepared.candidate_seeds) for prepared in prepared_by_event.values()
    )


def _wealth_test_conn(
    *,
    captured_at: _dt.datetime,
    ctf: dict[str, int] | None = None,
    allowance_micro: int = 20_000_000,
):
    conn = sqlite3.connect(":memory:")
    init_collateral_schema(conn)
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            token_id TEXT,
            side TEXT,
            size REAL,
            price REAL,
            intent_kind TEXT,
            state TEXT
        );
        CREATE TABLE venue_command_events (
            command_id TEXT,
            event_type TEXT,
            occurred_at TEXT
        );
        CREATE TABLE entry_exposure_obligations (
            command_id TEXT PRIMARY KEY,
            status TEXT,
            token_id TEXT,
            shares REAL,
            cost_basis_usd REAL,
            unbounded INTEGER,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            25_000_000,
            allowance_micro,
            2_000_000,
            json.dumps(ctf or {}),
            "{}",
            0,
            "{}",
            captured_at.isoformat(),
            "CHAIN",
            "wallet-hash",
        ),
    )
    return conn


def test_current_portfolio_wealth_witness_uses_one_chain_generation():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )
    repeated = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.spendable_cash_usd == Decimal("25")
    assert witness.wealth_floor_usd == Decimal("27")
    assert witness.wealth_ceiling_usd == Decimal("27")
    assert repeated.witness_identity == witness.witness_identity


def test_position_token_uses_typed_direction_value():
    no_position = SimpleNamespace(
        direction=Direction.NO,
        token_id="yes-token",
        no_token_id="no-token",
    )
    yes_position = SimpleNamespace(
        direction=Direction.YES,
        token_id="yes-token",
        no_token_id="no-token",
    )

    assert universe._position_token(no_position) == "no-token"
    assert universe._position_token(yes_position) == "yes-token"
    assert universe._position_token(
        SimpleNamespace(direction=Direction.UNKNOWN, token_id="yes-token")
    ) == ""


def test_current_portfolio_wealth_keeps_owned_cash_when_allowance_is_zero():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at, allowance_micro=0)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.spendable_cash_usd == Decimal("25")
    assert witness.wealth_floor_usd == Decimal("27")
    assert witness.wealth_ceiling_usd == Decimal("27")


def test_current_solve_ledger_inputs_bind_positions_and_cash_in_one_read_snapshot(
    monkeypatch,
):
    conn = sqlite3.connect(":memory:")
    at = _dt.datetime(2026, 7, 13, 20, 0, tzinfo=_dt.timezone.utc)
    state = SimpleNamespace(positions=[SimpleNamespace(position_id="position-1")])
    witness = SimpleNamespace(ledger_snapshot_id="ledger-1")

    from src.engine import global_auction_universe
    from src.state import portfolio

    def load(current_conn):
        assert current_conn is conn
        assert conn.in_transaction
        return state

    def build(current_conn, *, decision_at_utc, max_age, portfolio_state):
        assert current_conn is conn
        assert conn.in_transaction
        assert decision_at_utc == at
        assert max_age.total_seconds() > 0
        assert portfolio_state is state
        return witness

    monkeypatch.setattr(portfolio, "load_runtime_open_portfolio", load)
    monkeypatch.setattr(
        global_auction_universe,
        "current_portfolio_wealth_witness",
        build,
    )

    actual_witness, positions = era._current_solve_ledger_inputs(
        conn,
        decision_time=at,
    )
    assert actual_witness is witness
    assert positions == tuple(state.positions)
    assert not conn.in_transaction


def test_current_portfolio_wealth_economic_identity_ignores_heartbeat_time_only():
    first_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    second_at = first_at + _dt.timedelta(seconds=30)
    conn = _wealth_test_conn(captured_at=first_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at=first_at.isoformat(),
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    first = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=first_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") SELECT pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,?,?,"
        "raw_balance_payload_hash FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1",
        (second_at.isoformat(), "CHAIN"),
    )
    portfolio.positions[0].chain_verified_at = second_at.isoformat()
    second = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=second_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )

    assert second.witness_identity != first.witness_identity
    assert second.economic_identity == first.economic_identity


def test_current_portfolio_wealth_economic_identity_changes_with_cash():
    first_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    second_at = first_at + _dt.timedelta(seconds=1)
    conn = _wealth_test_conn(captured_at=first_at)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    first = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=first_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )
    conn.execute(
        "INSERT INTO collateral_ledger_snapshots ("
        "pusd_balance_micro,pusd_allowance_micro,usdc_e_legacy_balance_micro,"
        "ctf_token_balances_json,ctf_token_allowances_json,"
        "reserved_pusd_for_buys_micro,reserved_tokens_for_sells_json,"
        "captured_at,authority_tier,raw_balance_payload_hash"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (19_000_000, 20_000_000, 2_000_000, "{}", "{}", 0, "{}", second_at.isoformat(), "CHAIN", "changed"),
    )
    second = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=second_at,
        max_age=_dt.timedelta(seconds=60),
        portfolio_state=portfolio,
    )

    assert second.economic_identity != first.economic_identity


def test_current_portfolio_wealth_uses_fresh_synced_positions_when_ctf_mirror_empty():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at=decision_at.isoformat(),
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_floor_usd == Decimal("27")
    assert witness.wealth_ceiling_usd == Decimal("30.25")


def test_current_portfolio_wealth_uses_fresh_ctf_mirror_over_stale_projection_time():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(
        captured_at=decision_at,
        ctf={"yes-token": 3_250_000},
    )
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at="2026-07-10T07:00:00+00:00",
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_ceiling_usd == Decimal("30.25")


def test_current_portfolio_wealth_uses_ctf_mirror_during_projection_lag():
    decision_at = _dt.datetime(2026, 7, 17, 2, 42, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(
        captured_at=decision_at,
        ctf={"no-token": 14_589_200},
    )
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction=Direction.NO,
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="unknown",
                chain_shares=0.0,
                shares=14.589284,
                fill_authority="venue_confirmed_full",
                chain_verified_at="",
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_ceiling_usd == Decimal("41.5892")


def test_current_portfolio_wealth_bounds_verified_fill_during_chain_lag():
    decision_at = _dt.datetime(2026, 7, 17, 2, 42, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction=Direction.NO,
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="unknown",
                chain_shares=0.0,
                shares=14.589284,
                fill_authority="venue_confirmed_full",
                chain_verified_at="",
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_ceiling_usd == Decimal("41.589284")


def test_current_portfolio_wealth_refuses_unverified_projection_lag():
    decision_at = _dt.datetime(2026, 7, 17, 2, 42, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction=Direction.NO,
                token_id="yes-token",
                no_token_id="no-token",
                chain_state="unknown",
                chain_shares=0.0,
                shares=14.589284,
                fill_authority="optimistic_submitted",
                chain_verified_at="",
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    with pytest.raises(ValueError, match="CURRENT_WEALTH_OPEN_POSITION_INVALID"):
        current_portfolio_wealth_witness(
            conn,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
        )


def test_current_portfolio_wealth_accepts_targeted_ctf_subset():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(
        captured_at=decision_at,
        ctf={"no-token-1": 3_250_000},
    )
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction=Direction.NO,
                token_id="yes-token-1",
                no_token_id="no-token-1",
                chain_state="synced",
                chain_shares=3.25,
                chain_verified_at=decision_at.isoformat(),
                state="entered",
            ),
            SimpleNamespace(
                trade_id="trade-2",
                direction=Direction.YES,
                token_id="yes-token-2",
                no_token_id="no-token-2",
                chain_state="synced",
                chain_shares=2.0,
                chain_verified_at=decision_at.isoformat(),
                state="entered",
            ),
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.wealth_ceiling_usd == Decimal("32.25")


@pytest.mark.parametrize(
    ("chain_state", "chain_verified_at"),
    [
        ("unknown", "2026-07-10T08:00:00+00:00"),
        ("synced", "2026-07-10T07:29:00+00:00"),
        ("synced", ""),
    ],
)
def test_current_portfolio_wealth_bounds_unverified_claim_without_spendable_credit(
    chain_state, chain_verified_at
):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    conn = _wealth_test_conn(captured_at=decision_at)
    portfolio = PortfolioState(
        positions=[
            SimpleNamespace(
                trade_id="trade-1",
                direction="buy_yes",
                token_id="yes-token",
                no_token_id="no-token",
                chain_state=chain_state,
                chain_shares=1.0,
                chain_verified_at=chain_verified_at,
                state="entered",
            )
        ],
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )

    witness = current_portfolio_wealth_witness(
        conn,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.spendable_cash_usd == Decimal("25")
    assert witness.wealth_floor_usd == Decimal("27")
    assert witness.wealth_ceiling_usd == Decimal("28")


def test_current_portfolio_wealth_witness_bounds_inflight_buy_reservation():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    reserved = _wealth_test_conn(captured_at=decision_at)
    reserved.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)",
        ("cmd", "position-1", "token-1", "BUY", 2.0, 0.5, "ENTRY", "POST_ACKED"),
    )
    reserved.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?,?)",
        (
            "filled-cmd",
            "position-2",
            "token-2",
            "BUY",
            1.5,
            0.333333333333,
            "ENTRY",
            "POST_ACKED",
        ),
    )
    reserved.executemany(
        "INSERT INTO entry_exposure_obligations VALUES (?,?,?,?,?,?,?)",
        (
            ("cmd", "OPEN", "token-1", 2.0, 1.0, 0, decision_at.isoformat()),
            (
                "filled-cmd",
                "OPEN",
                "token-2",
                1.5,
                0.5,
                0,
                decision_at.isoformat(),
            ),
        ),
    )
    reserved.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        ("cmd", "PUSD_BUY", None, 1_000_000, decision_at.isoformat()),
    )
    reserved.execute(
        "INSERT INTO collateral_unsettled_proceeds ("
        "command_id,direction,reservation_type,token_id,amount_micro,created_at"
        ") VALUES (?,?,?,?,?,?)",
        (
            "filled-cmd",
            "OUTGOING_DEDUCTION",
            "PUSD_BUY",
            None,
            500_000,
            decision_at.isoformat(),
        ),
    )

    witness = current_portfolio_wealth_witness(
        reserved,
        decision_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        portfolio_state=portfolio,
    )

    assert witness.spendable_cash_usd == Decimal("23.5")
    assert witness.reservations_usd == Decimal("1.5")
    assert witness.wealth_floor_usd == Decimal("25.5")
    assert witness.wealth_ceiling_usd == Decimal("29.0")
    assert probe_inflight_buy_ambiguity(reserved) is False


def test_current_portfolio_wealth_witness_refuses_unbounded_inflight_buy():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    reserved = _wealth_test_conn(captured_at=decision_at)
    reserved.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        ("cmd", "PUSD_BUY", None, 1_000_000, decision_at.isoformat()),
    )

    with pytest.raises(ValueError, match="CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS"):
        current_portfolio_wealth_witness(
            reserved,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
        )


def test_current_portfolio_wealth_witness_refuses_unknown_inventory():
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    portfolio = PortfolioState(
        authority="canonical_db",
        authority_scope="runtime_exposure",
    )
    unknown = _wealth_test_conn(captured_at=decision_at, ctf={"unknown-token": 1_000_000})
    with pytest.raises(ValueError, match="CURRENT_WEALTH_CHAIN_POSITION_SET_MISMATCH"):
        current_portfolio_wealth_witness(
            unknown,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
        )


def test_global_batch_rejects_inflight_buy_before_scope_scan(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    trade_conn = _wealth_test_conn(captured_at=decision_at)
    trade_conn.execute(
        "INSERT INTO collateral_reservations ("
        "command_id,reservation_type,token_id,amount,created_at"
        ") VALUES (?,?,?,?,?)",
        ("cmd", "PUSD_BUY", None, 1_000_000, decision_at.isoformat()),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: pytest.fail("ambiguous wealth must reject before scope scan"),
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade_conn,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: pytest.fail("ambiguous wealth must not prepare q"),
        actuate_winner=lambda *_: pytest.fail("ambiguous wealth must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_AUCTION_FAILED:ValueError:"
        "CURRENT_WEALTH_INFLIGHT_BUY_AMBIGUOUS"
    )


def test_global_batch_reduce_only_skips_nonheld_universe(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,),
        captured_at_utc=decision_at,
    )
    trade_conn = _wealth_test_conn(captured_at=decision_at)
    monkeypatch.setattr(
        global_batch_runtime,
        "_current_held_weather_families",
        lambda _conn: (),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade_conn,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: pytest.fail("reduce-only must not prepare nonheld q"),
        actuate_winner=lambda *_: pytest.fail("reduce-only must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        buy_candidates_enabled=False,
    )

    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_AUCTION_NO_REDUCE_ONLY_FAMILY"
    )


def test_global_batch_routes_restricted_day0_epoch_to_day0_only_scope(monkeypatch):
    from src.events.candidate_binding import weather_family_id

    decision_at = _dt.datetime(2026, 7, 11, 17, 6, tzinfo=_dt.timezone.utc)
    event = _global_day0_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,),
        captured_at_utc=decision_at,
    )
    trade_conn = _wealth_test_conn(captured_at=decision_at)
    scan_calls = []

    monkeypatch.setattr(
        global_batch_runtime,
        "_current_held_weather_families",
        lambda _conn: (),
    )

    def scan(**kwargs):
        scan_calls.append(kwargs)
        return scope

    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        scan,
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade_conn,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: pytest.fail(
            "reduce-only must not prepare a nonheld Day0 family"
        ),
        actuate_winner=lambda *_: pytest.fail(
            "reduce-only must not actuate a nonheld Day0 family"
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        buy_candidates_enabled=False,
        restrict_to_family_keys=frozenset(
            {
                weather_family_id(
                    city="Alpha",
                    target_date="2026-07-11",
                    metric="high",
                )
            }
        ),
    )

    assert result.venue_submit_count == 0
    assert scan_calls[0]["day0_only"] is True


def test_global_batch_reduce_only_prepares_only_held_families(monkeypatch):
    import src.data.replacement_input_hwm as replacement_hwm

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    held_event = _global_scope_event(city="Alpha", source_run_id="run-a")
    unrelated_event = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (held_event, unrelated_event),
        captured_at_utc=decision_at,
    )
    trade_conn = _wealth_test_conn(captured_at=decision_at)
    prepared = []
    monkeypatch.setattr(
        global_batch_runtime,
        "_current_held_weather_families",
        lambda _conn: (("Alpha", "2026-07-11", "high"),),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    monkeypatch.setattr(
        replacement_hwm,
        "prime_frozen_replacement_artifact_hwm",
        lambda *_args, **_kwargs: lambda: None,
    )

    def prepare(event, _at):
        prepared.append(event)
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason="GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:test",
            proof_accepted=False,
        )

    result = global_batch_runtime.process_current_global_batch(
        (held_event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade_conn,
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("ineligible q must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        buy_candidates_enabled=False,
    )

    assert prepared == [held_event]
    assert result.receipts[held_event.event_id].reason == (
        "GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY"
    )


def test_global_batch_waits_until_global_winner_family_is_claimed(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b),
        captured_at_utc=decision_at,
    )
    prepared = {
        event_a.event_id: SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=scope.family_keys[0],
                captured_at_utc=decision_at,
                posterior_identity_hash="run-a",
            )
        ),
        event_b.event_id: SimpleNamespace(
            probability_witness=SimpleNamespace(
                family_key=scope.family_keys[1],
                captured_at_utc=decision_at,
                posterior_identity_hash="run-b",
            )
        ),
    }
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event_b.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-b",
            economic_identity="economic-b",
        ),
    )
    monkeypatch.setattr(global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope)
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(global_batch_runtime, "current_venue_auction_identity", lambda *_, **__: "venue")
    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected)

    result = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: __import__("json").loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=lambda *_: pytest.fail("unclaimed winner must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.next_claim_event is not None
    assert result.next_claim_event.event_id != event_b.event_id
    assert result.next_claim_event.event_type == event_b.event_type
    assert result.next_claim_event.causal_snapshot_id == event_b.causal_snapshot_id
    assert result.next_claim_event.payload_json == event_b.payload_json
    assert result.next_claim_event.source.endswith(":economic-b")
    repeated = global_batch_runtime._next_claim_carrier(
        event_b,
        targeted_at=decision_at + _dt.timedelta(seconds=30),
        economic_identity="economic-b",
        payload=__import__("json").loads(event_b.payload_json),
    )
    assert repeated.event_id == result.next_claim_event.event_id
    assert result.receipts[event_a.event_id].reason == "GLOBAL_WINNER_AWAITS_CLAIM"


def test_global_batch_restricts_urgent_scope_to_changed_families(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b),
        captured_at_utc=decision_at,
    )
    prepared_events = []
    selected_scopes = []
    scan_calls = []

    def scan(**kwargs):
        scan_calls.append(kwargs)
        return scope

    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        scan,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )

    def select(*_args, **kwargs):
        selected_scopes.append(kwargs["current_scope"])
        return SimpleNamespace(
            decision=SimpleNamespace(
                candidate=None,
                no_trade_reason="CASH_DOMINATES",
                rejection_reasons={},
                candidate_evaluations=(),
            ),
            winner_event_id=None,
            actuation=None,
        )

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        select,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        lambda *_args, **_kwargs: None,
    )

    def prepare(event, _at):
        prepared_events.append(event.event_id)
        payload = json.loads(event.payload_json)
        family_key = era.weather_family_id(
            city=payload["city"],
            target_date=payload["target_date"],
            metric=payload["metric"],
        )
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=SimpleNamespace(
                probability_witness=SimpleNamespace(
                    family_key=family_key,
                    captured_at_utc=decision_at,
                    posterior_identity_hash=payload["source_run_id"],
                    witness_identity=f"q-{family_key}",
                    q_version=f"q-{family_key}",
                    family_binding_identity=f"binding-{family_key}",
                    sample_matrix_identity=f"samples-{family_key}",
                    band_alpha=0.05,
                    band_basis="lower-tail",
                )
            ),
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("no-trade scope must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        restrict_to_family_keys=frozenset({scope.family_keys[0]}),
    )

    assert prepared_events == [event_a.event_id]
    assert scan_calls[0]["restrict_to_families"] == frozenset(
        {("Alpha", "2026-07-11", "high")}
    )
    assert len(selected_scopes) == 1
    assert selected_scopes[0].family_keys == (scope.family_keys[0],)
    assert result.receipts[event_a.event_id].reason == (
        "GLOBAL_AUCTION_NO_TRADE:CASH_DOMINATES"
    )


def test_global_batch_requeues_claimed_epoch_when_new_durable_fact_arrives(
    monkeypatch,
):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,),
        captured_at_utc=decision_at,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    prepared = []

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda *_: prepared.append(True),
        actuate_winner=lambda *_: pytest.fail("superseded epoch must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        epoch_superseded=lambda: True,
    )

    assert prepared == []
    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_AUCTION_SUPERSEDED_BY_NEW_FACT"
    )


def test_global_batch_preempts_after_book_capture_before_selection(
    monkeypatch,
):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,),
        captured_at_utc=decision_at,
    )
    probability = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="probability-a",
        q_version="q-a",
        family_binding_identity="family-binding-a",
        sample_matrix_identity="samples-a",
        band_alpha=0.05,
        band_basis="lower-tail",
    )
    prepared = SimpleNamespace(probability_witness=probability)
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    cancelled = [False]
    book_calls = []

    def capture_books(probabilities, _at):
        book_calls.append(True)
        cancelled[0] = True
        return probabilities, object()

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        lambda *_args, **_kwargs: pytest.fail(
            "urgent input after book capture must preempt selection"
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        lambda *_args, **_kwargs: pytest.fail(
            "cancelled selection must not write a heavy auction receipt"
        ),
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda item: json.loads(item.payload_json),
        prepare_event=lambda item, _at: EventSubmissionReceipt(
            False,
            item.event_id,
            item.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail(
            "cancelled selection must not actuate"
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=capture_books,
        selection_cancelled=lambda: cancelled[0],
    )

    assert book_calls == [True]
    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"
    )


def test_global_batch_preempts_after_preflight_before_actuation(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,),
        captured_at_utc=decision_at,
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-run-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    selected = SimpleNamespace(
        decision=SimpleNamespace(
            candidate=SimpleNamespace(family_key=scope.family_keys[0]),
            no_trade_reason=None,
        ),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth-1",
        ),
    )
    cancelled = [False]
    calls = {"preflight": 0, "venue": 0}
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-1",
            economic_identity="wealth-economics-1",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        lambda *_args, **_kwargs: selected,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_preflight_receipt",
        lambda *_args, **_kwargs: pytest.fail(
            "urgent input must release the lane before preflight persistence"
        ),
    )

    def preflight(*_args):
        calls["preflight"] += 1
        cancelled[0] = True
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token="binding-a",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("urgent input must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda *_: pytest.fail("urgent input must not actuate")
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            _global_test_book("book-fence", price="0.40"),
        ),
        selection_cancelled=lambda: cancelled[0],
    )

    assert calls == {"preflight": 1, "venue": 0}
    assert result.winner_event_id is None
    assert result.venue_submit_count == 0
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_AUCTION_NO_TRADE:GLOBAL_SELECTION_CANCELLED"
    )


def test_global_batch_claims_unpaged_cut_time_winner_and_continues_actuation(
    monkeypatch,
):
    from src.engine.global_single_order_auction import (
        GlobalSingleOrderActuation,
        PreparedGlobalAuctionResult,
    )

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    family_a, family_b = scope.family_keys

    def _witness(family_key, suffix):
        return SimpleNamespace(
            family_key=family_key,
            witness_identity=f"probability-{suffix}",
            posterior_identity_hash=f"run-{suffix}",
            q_version=f"q-{suffix}",
            family_binding_identity=f"family-binding-{suffix}",
            sample_matrix_identity=f"sample-matrix-{suffix}",
            band_alpha=0.05,
            band_basis="lower-tail",
            captured_at_utc=decision_at,
        )

    witness_a = _witness(family_a, "a")
    witness_b = _witness(family_b, "b")
    curve = SimpleNamespace(
        book_hash="book-b",
        levels=(SimpleNamespace(price=Decimal("0.40"), size=Decimal("10")),),
        fee_model=SimpleNamespace(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    candidate = SimpleNamespace(
        candidate_id="candidate-b",
        family_key=family_b,
        bin_id="20C",
        condition_id="condition-b",
        side="YES",
        token_id="token-b",
        probability_witness_identity=witness_b.witness_identity,
        book_snapshot_id="book-snapshot-b",
        execution_curve_identity="curve-b",
        executable_cost_curve=curve,
        resolution_identity="resolution-b",
    )
    decision = SimpleNamespace(
        candidate=candidate,
        shares=Decimal("10"),
        cost_usd=Decimal("4"),
        limit_price=Decimal("0.40"),
        expected_fill_price_before_fee=Decimal("0.40"),
        max_spend_usd=Decimal("4"),
        current_token_shares=Decimal("0"),
        full_kelly_target_shares=Decimal("40"),
        fractional_kelly_target_shares=Decimal("10"),
        robust_delta_log_wealth=0.01,
        robust_ev_usd=2.0,
        capital_efficiency=0.25,
        no_trade_reason=None,
        terminal_wealth=SimpleNamespace(
            win_probability_lcb=0.60,
            loss_probability_ucb=0.40,
            loss_payoff_usd=Decimal("-4"),
            win_payoff_usd=Decimal("6"),
            median_payoff_usd=Decimal("6"),
            wealth_after_loss_usd=Decimal("96"),
            wealth_after_win_usd=Decimal("106"),
            expected_value_diagnostic_usd=2.0,
        ),
    )
    wealth_economic_identity = "wealth-economic"
    economic_identity = global_single_order_economic_identity(
        decision=decision,
        probability_witness=witness_b,
        wealth_economic_identity=wealth_economic_identity,
    )
    actuation_identity = global_single_order_actuation_identity(
        decision=decision,
        winner_event_id=event_b.event_id,
        universe_witness_identity="universe",
        wealth_witness_identity="wealth-witness",
        selection_epoch_identity="selection-epoch",
        selection_cut_at_utc=decision_at,
        decision_at_utc=decision_at,
    )
    selected = PreparedGlobalAuctionResult(
        decision=decision,
        winner_event_id=event_b.event_id,
        actuation=GlobalSingleOrderActuation(
            decision=decision,
            winner_event_id=event_b.event_id,
            universe_witness_identity="universe",
            wealth_witness_identity="wealth-witness",
            selection_epoch_identity="selection-epoch",
            probability_witness=witness_b,
            selection_cut_at_utc=decision_at,
            decision_at_utc=decision_at,
            actuation_identity=actuation_identity,
            wealth_economic_identity=wealth_economic_identity,
            economic_identity=economic_identity,
        ),
    )

    @dataclass(frozen=True)
    class _Prepared:
        probability_witness: object

    prepared = {
        event_a.event_id: _Prepared(probability_witness=witness_a),
        event_b.event_id: _Prepared(probability_witness=witness_b),
    }
    monkeypatch.setattr(
        global_batch_runtime,
        "scan_current_global_auction_scope",
        lambda **_: scope,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-witness",
            economic_identity=wealth_economic_identity,
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )
    selection_calls = [0]

    def _select(*_args, **_kwargs):
        selection_calls[0] += 1
        return selected

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        _select,
    )
    claimed_targets = []
    actuated = []
    venue_calls = [0]

    def _claim(target):
        claimed_targets.append(target)
        return True

    def _actuate(event, actuation, _at):
        actuated.append((event, actuation))
        venue_calls[0] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            reason="SUBMITTED:test",
            proof_accepted=True,
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=_actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        claim_unpaged_winner=_claim,
    )

    assert len(claimed_targets) == 1
    target = claimed_targets[0]
    assert result.next_claim_event is None
    assert result.winner_event_id == target.event_id
    assert result.venue_submit_count == 1
    assert selection_calls[0] == 1
    assert set(result.receipts) == {event_a.event_id, target.event_id}
    assert actuated[0][0] == target
    rebound = actuated[0][1]
    assert rebound.winner_event_id == target.event_id
    assert rebound.actuation_identity != actuation_identity
    assert rebound.economic_identity == economic_identity

    actuated.clear()
    venue_calls[0] = 0
    selection_calls[0] = 0
    resumed_wealth_economic_identity = "wealth-economic-resumed"
    resumed_economic_identity = global_single_order_economic_identity(
        decision=decision,
        probability_witness=witness_b,
        wealth_economic_identity=resumed_wealth_economic_identity,
    )
    resumed_selected = replace(
        selected,
        actuation=replace(
            selected.actuation,
            wealth_economic_identity=resumed_wealth_economic_identity,
            economic_identity=resumed_economic_identity,
        ),
    )

    def _select_resumed(*_args, **_kwargs):
        selection_calls[0] += 1
        return resumed_selected

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        _select_resumed,
    )
    resumed = global_batch_runtime.process_current_global_batch(
        (target,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=_actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        claim_unpaged_winner=lambda _target: pytest.fail(
            "an already-claimed deterministic target must not be claimed again"
        ),
    )

    assert resumed.next_claim_event is None
    assert resumed.winner_event_id == target.event_id
    assert resumed.venue_submit_count == 1
    assert selection_calls[0] == 1
    assert set(resumed.receipts) == {target.event_id}
    assert actuated[0][0] == target
    assert actuated[0][1].economic_identity == resumed_economic_identity

    fence_wealth_economic_identity = "wealth-economic-fence"
    fence_economic_identity = global_single_order_economic_identity(
        decision=decision,
        probability_witness=witness_b,
        wealth_economic_identity=fence_wealth_economic_identity,
    )
    fence_selected = replace(
        selected,
        actuation=replace(
            selected.actuation,
            wealth_economic_identity=fence_wealth_economic_identity,
            economic_identity=fence_economic_identity,
        ),
    )
    selections = iter((fence_selected,))
    fence_selection_calls = [0]

    def _select_fence(*_args, **_kwargs):
        fence_selection_calls[0] += 1
        return next(selections)

    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        _select_fence,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_book_economics_manifest",
        lambda _epoch: (("book",),),
    )
    fake_epoch = SimpleNamespace(
        max_age=_dt.timedelta(seconds=30),
        captured_at_utc=decision_at,
        witness_identity="book-epoch",
    )
    claimed_targets.clear()
    actuated.clear()
    venue_calls[0] = 0
    supersession_checks = [0]

    def _wake_after_cut():
        supersession_checks[0] += 1
        # scope, both probability families, the completed probability set,
        # and book capture are fenced. A later wake belongs to the next epoch
        # and cannot starve this winner's exact JIT preflight.
        return supersession_checks[0] > 5

    fenced = global_batch_runtime.process_current_global_batch(
        (event_a,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        ),
        actuate_winner=_actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        claim_unpaged_winner=_claim,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            fake_epoch,
        ),
        preflight_winner=lambda *_: global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token=object(),
        ),
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda event, actuation, at, _token, _authority: _actuate(
                event, actuation, at
            )
        ),
        epoch_superseded=_wake_after_cut,
    )

    assert len(claimed_targets) == 1
    assert claimed_targets[0].source.endswith(f":{fence_economic_identity}")
    assert fenced.winner_event_id == claimed_targets[0].event_id
    assert fenced.venue_submit_count == 1
    assert supersession_checks[0] == 5
    assert fence_selection_calls[0] == 1
    assert set(fenced.receipts) == {
        event_a.event_id,
        claimed_targets[0].event_id,
    }


def test_global_batch_excludes_typed_current_q_ineligible_family(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    family_a, family_b = scope.family_keys
    prepared_b = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=family_b,
            captured_at_utc=decision_at,
            posterior_identity_hash="run-b",
        )
    )
    current_probability = object()
    actuation = SimpleNamespace(actuation_identity="actuation-b")
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event_b.event_id,
        actuation=actuation,
    )
    calls = {"venue": 0, "ineligible_prepare": 0}
    persisted = {}
    ineligible_reason = (
        "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
        "GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED:REPLACEMENT_RAW_INPUT_HWM"
    )

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )

    def select(prepared_by_event, *, current_scope, **_kwargs):
        assert current_scope.family_keys == (family_b,)
        assert tuple(prepared_by_event) == (event_b.event_id,)
        return selected

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        lambda *_, **kwargs: persisted.update(kwargs) or 1,
    )
    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(lambda cls, witness: current_probability),
    )

    def prepare(event, _at):
        if event.event_id == event_a.event_id:
            calls["ineligible_prepare"] += 1
            return EventSubmissionReceipt(
                False,
                event.event_id,
                event.causal_snapshot_id,
                reason=ineligible_reason,
            )
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared_b,
        )

    def actuate(winner, chosen, _at):
        assert winner.event_id == event_b.event_id
        assert chosen is actuation
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert calls["ineligible_prepare"] == 1
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event_b.event_id
    assert result.receipts[event_b.event_id].submitted is True
    assert result.receipts[event_a.event_id].reason == (
        f"GLOBAL_FAMILY_INELIGIBLE:{ineligible_reason}"
    )
    assert persisted["full_scope_identity"] == scope.scope_identity
    assert persisted["full_scope_family_keys"] == scope.family_keys
    assert persisted["probability_ineligible_by_family"] == {
        family_a: ineligible_reason
    }


def test_global_batch_rejects_when_all_families_lack_current_q(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    reason = (
        "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
        "GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED:REPLACEMENT_RAW_INPUT_HWM"
    )
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        lambda *_args, **_kwargs: pytest.fail(
            "an empty current-q scope must not select"
        ),
    )

    def prepare(event, _at):
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            reason=reason,
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail(
            "an empty current-q scope must not actuate"
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert {receipt.reason for receipt in result.receipts.values()} == {
        "GLOBAL_AUCTION_NO_CURRENT_PROBABILITY_FAMILY"
    }


def test_global_batch_rejects_unexpected_probability_prepare_failure(monkeypatch):
    import src.data.replacement_input_hwm as input_hwm

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    reason = "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:RuntimeError:boom"
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    selection = sqlite3.connect(":memory:")
    prime_seen = []

    def prepare(current, _at):
        prime_seen.append(input_hwm._FROZEN_INPUT_HWM.get() is not None)
        return EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            reason=reason,
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=selection,
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("unexpected failure must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        selection_snapshot_connections=(selection,),
    )

    assert prime_seen == [True]
    assert input_hwm._FROZEN_INPUT_HWM.get() is None
    assert selection.in_transaction is False
    assert result.venue_submit_count == 0
    assert result.receipts[event.event_id].reason == (
        f"GLOBAL_PREPARED_FAMILY_INCOMPLETE:{scope.family_keys[0]}:{reason}"
    )
    selection.close()


def test_global_batch_actuates_exactly_one_claimed_global_winner(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    duplicate = _global_scope_event(city="Alpha", source_run_id="run-duplicate")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=scope.family_keys[0],
            captured_at_utc=decision_at,
            posterior_identity_hash="run-a",
        )
    )
    actuation = SimpleNamespace(actuation_identity="actuation-a")
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=actuation,
    )
    current_probability = object()
    calls = {"venue": 0, "fractional_kelly_multiplier": None}
    monkeypatch.setattr(global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope)
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(global_batch_runtime, "current_venue_auction_identity", lambda *_, **__: "venue")
    def select(*_, **kwargs):
        calls["fractional_kelly_multiplier"] = kwargs[
            "fractional_kelly_multiplier"
        ]
        return selected

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)
    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(lambda cls, witness: current_probability),
    )

    def actuate(winner, chosen, _at):
        assert winner.event_id == event.event_id
        assert chosen is actuation
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event, duplicate),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: __import__("json").loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        fractional_kelly_multiplier=Decimal("0.03125"),
    )

    assert calls["venue"] == 1
    assert calls["fractional_kelly_multiplier"] == Decimal("0.03125")
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True
    assert result.receipts[duplicate.event_id].reason == (
        f"GLOBAL_DUPLICATE_FAMILY_CARRIER:{event.event_id}"
    )


def test_global_batch_keeps_current_q_on_its_scope_carrier(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 14, 14, 50, tzinfo=_dt.timezone.utc)
    stale_claim = _global_scope_event(city="Alpha", source_run_id="run-old")
    current = _global_scope_event(city="Alpha", source_run_id="run-current")
    scope = current_global_auction_scope_from_events(
        (current,), captured_at_utc=decision_at
    )
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=scope.family_keys[0],
            captured_at_utc=decision_at,
            posterior_identity_hash="run-current",
        )
    )
    seen_event_ids = []

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue",
    )

    def select(prepared_by_event, **_kwargs):
        seen_event_ids.extend(prepared_by_event)
        return SimpleNamespace(
            decision=SimpleNamespace(candidate=None, no_trade_reason="test-no-trade"),
            winner_event_id=None,
            actuation=None,
        )

    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", select
    )

    result = global_batch_runtime.process_current_global_batch(
        (stale_claim,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda event: json.loads(event.payload_json),
        prepare_event=lambda event, _at: EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("no-trade must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
    )

    assert seen_event_ids == [current.event_id]
    assert result.receipts[stale_claim.event_id].reason == (
        "GLOBAL_AUCTION_NO_TRADE:test-no-trade"
    )


def test_global_one_shot_actuator_refuses_second_consumption():
    calls = []
    receipt = EventSubmissionReceipt(False, "event")
    actuator = global_batch_runtime.GlobalOneShotActuator(
        lambda value: calls.append(value) or receipt
    )

    assert actuator.consume("first") is receipt
    with pytest.raises(RuntimeError, match="GLOBAL_ACTUATION_CAPABILITY_CONSUMED"):
        actuator.consume("second")
    assert calls == ["first"]


def _global_test_book(
    identity: str,
    *,
    price: str,
    captured_at: _dt.datetime | None = None,
):
    return SimpleNamespace(
        witness_identity=identity,
        captured_at_utc=captured_at
        or _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc),
        max_age=_dt.timedelta(seconds=30),
        assets=(
            SimpleNamespace(
                family_key="family",
                bin_id="bin",
                condition_id="condition",
                market_event_id="market-event",
                side="YES",
                token_id="token",
                curve=SimpleNamespace(
                    fee_model=SimpleNamespace(fee_rate=Decimal("0")),
                    min_tick=Decimal("0.001"),
                    min_order_size=Decimal("1"),
                    levels=(
                        SimpleNamespace(
                            price=Decimal(price),
                            size=Decimal("100"),
                        ),
                    ),
                ),
            ),
        ),
    )


def _global_test_buy_candidate(
    *,
    family_key: str,
    probability_witness_identity: str,
    book_identity: str,
    price: str,
    captured_at: _dt.datetime,
    candidate_id: str = "candidate",
    bin_id: str = "bin",
    condition_id: str = "condition",
    side: str = "YES",
    token_id: str = "token",
) -> GlobalSingleOrderCandidate:
    curve = ExecutableCostCurve(
        token_id=token_id,
        side=side,
        snapshot_id=f"snapshot-{book_identity}",
        book_hash=f"hash-{book_identity}",
        levels=(BookLevel(price=Decimal(price), size=Decimal("100")),),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.001"),
        min_order_size=Decimal("1"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    return GlobalSingleOrderCandidate(
        candidate_id=candidate_id,
        family_key=family_key,
        bin_id=bin_id,
        condition_id=condition_id,
        side=side,
        token_id=token_id,
        probability_witness_identity=probability_witness_identity,
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=captured_at,
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id="ledger",
        executable_cost_curve=curve,
        resolution_identity="resolution",
    )


def _global_test_candidate_book(
    *candidates: GlobalSingleOrderCandidate,
    epoch_captured_at: _dt.datetime,
) -> CurrentGlobalBookEpoch:
    assets = tuple(
        CurrentGlobalBookAsset(
            family_key=candidate.family_key,
            bin_id=candidate.bin_id,
            condition_id=candidate.condition_id,
            gamma_market_id=f"gamma-{candidate.condition_id}",
            market_event_id=f"event-{candidate.condition_id}",
            side=candidate.side,
            token_id=candidate.token_id,
            curve=candidate.executable_cost_curve,
            captured_at_utc=candidate.book_captured_at_utc,
        )
        for candidate in candidates
    )
    states = tuple(
        (
            candidate.family_key,
            candidate.bin_id,
            candidate.condition_id,
            candidate.side,
            candidate.token_id,
            "EXECUTABLE",
            candidate.executable_cost_curve.book_hash,
            asset.market_event_id,
            asset.gamma_market_id,
        )
        for candidate, asset in zip(candidates, assets)
    )
    return CurrentGlobalBookEpoch(
        assets=assets,
        asset_states=states,
        captured_at_utc=epoch_captured_at,
        max_age=_dt.timedelta(seconds=30),
        witness_identity=current_global_book_epoch_identity(
            asset_states=states,
            captured_at_utc=epoch_captured_at,
        ),
    )


def test_global_batch_overlays_jit_curve_without_full_universe_refresh(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    witnesses = {
        family_key: SimpleNamespace(
            family_key=family_key,
            captured_at_utc=decision_at,
            posterior_identity_hash=run_id,
            witness_identity=f"q-{run_id}",
        )
        for family_key, run_id in zip(scope.family_keys, ("run-a", "run-b"))
    }
    prepared = {
        event.event_id: SimpleNamespace(
            probability_witness=witnesses[family_key]
        )
        for event, family_key in zip((event_a, event_b), scope.family_keys)
    }
    selected_candidate = _global_test_buy_candidate(
        family_key=scope.family_keys[1],
        probability_witness_identity=witnesses[scope.family_keys[1]].witness_identity,
        book_identity="book-1",
        price="0.41",
        captured_at=decision_at,
    )
    replacement_candidate = _global_test_buy_candidate(
        family_key=scope.family_keys[1],
        probability_witness_identity=witnesses[
            scope.family_keys[1]
        ].witness_identity,
        book_identity="book-2",
        price="0.42",
        captured_at=decision_at,
        candidate_id="candidate-repriced",
    )
    assert replacement_candidate.candidate_id != selected_candidate.candidate_id
    initial_book = _global_test_candidate_book(
        selected_candidate,
        epoch_captured_at=decision_at,
    )
    actuation_b_fence = SimpleNamespace(
        actuation_identity="actuation-b-fence", wealth_witness_identity="wealth-1"
    )
    actuation_b_final = SimpleNamespace(
        actuation_identity="actuation-b-final", wealth_witness_identity="wealth-1"
    )
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
            winner_event_id=event.event_id,
            actuation=actuation,
        )
        for event, candidate, actuation in (
            (event_b, selected_candidate, actuation_b_fence),
            (event_b, replacement_candidate, actuation_b_final),
        )
    )
    calls = {
        "prepare": 0,
        "books": 0,
        "wealth": 0,
        "preflight": [],
        "deadlines": [],
        "book_epochs": [],
        "venue": 0,
    }

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )

    def wealth(*_, **__):
        calls["wealth"] += 1
        return SimpleNamespace(
            spendable_cash_usd=Decimal(str(10 + calls["wealth"])),
            witness_identity=f"wealth-{calls['wealth']}",
            economic_identity=f"wealth-economics-{calls['wealth']}",
        )

    monkeypatch.setattr(
        global_batch_runtime, "current_portfolio_wealth_witness", wealth
    )

    def select(_prepared, **kwargs):
        expected_cash = Decimal(str(10 + calls["wealth"]))
        assert kwargs["capital_limit_usd"] == expected_cash
        calls["book_epochs"].append(kwargs["book_epoch"])
        return next(selections)

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)

    def prepare(event, _at):
        calls["prepare"] += 1
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        )

    def book_provider(probabilities, _at):
        calls["books"] += 1
        return probabilities, initial_book

    def preflight(event, _actuation, _at, authority):
        calls["preflight"].append(event.event_id)
        calls["deadlines"].append(authority.actuation_deadline)
        if len(calls["preflight"]) == 1:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="CURVE_SUPERSEDED",
                replacement_candidate=replacement_candidate,
                reason="curve moved",
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE", binding_token="binding-b"
        )

    def actuate_preflighted(event, actuation, _at, token, authority):
        assert event.event_id == event_b.event_id
        assert actuation is actuation_b_final
        assert token == "binding-b"
        assert (
            authority.book_epoch_identity
            == calls["book_epochs"][-1].witness_identity
        )
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate_preflighted
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=book_provider,
    )

    assert calls == {
        "prepare": 2,
        "books": 1,
        "wealth": 1,
        "preflight": [event_b.event_id, event_b.event_id],
        "deadlines": [
            decision_at + _dt.timedelta(seconds=30),
            decision_at + _dt.timedelta(seconds=30),
        ],
        "book_epochs": calls["book_epochs"],
        "venue": 1,
    }
    assert len(calls["book_epochs"]) == 2
    assert calls["book_epochs"][0] is initial_book
    assert calls["book_epochs"][1].captured_at_utc == decision_at
    assert calls["book_epochs"][1].assets[0].curve is (
        replacement_candidate.executable_cost_curve
    )
    assert result.winner_event_id == event_b.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event_b.event_id].submitted is True


def test_global_batch_validates_wealth_on_selection_clock_not_book_clock(
    monkeypatch,
):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    book_at = decision_at + _dt.timedelta(seconds=1)
    wealth_at = decision_at + _dt.timedelta(seconds=20)
    selection_at = decision_at + _dt.timedelta(seconds=21)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    family_key = scope.family_keys[0]
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=family_key,
            captured_at_utc=decision_at,
            posterior_identity_hash="run-a",
            witness_identity="q-run-a",
        )
    )
    times = iter((decision_at, book_at, wealth_at, selection_at))
    wealth_checks = []

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **kwargs: (
            wealth_checks.append(kwargs["decision_at_utc"])
            or SimpleNamespace(
                spendable_cash_usd=Decimal("10"),
                witness_identity="wealth",
                economic_identity="wealth-economics",
            )
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "select_prepared_global_auction",
        lambda *_, **__: SimpleNamespace(
            decision=SimpleNamespace(
                candidate=None,
                no_trade_reason="NO_CURRENT_EDGE",
            ),
            winner_event_id=None,
            actuation=None,
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        lambda *_, **__: 1,
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("no-trade must not actuate"),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: 0,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: next(times),
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            _global_test_book("book", price="0.40", captured_at=book_at),
        ),
    )

    assert wealth_checks == [wealth_at]
    assert result.winner_event_id is None
    assert set(result.receipts) == {event.event_id}


def test_global_batch_reauctions_with_tightened_candidate_q(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    family_key = scope.family_keys[0]
    witness = SimpleNamespace(
        family_key=family_key,
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-run-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    candidate = SimpleNamespace(
        family_key=family_key,
        bin_id="bin-a",
        side="NO",
        token_id="token-no-a",
        probability_witness_identity="q-run-a",
    )
    selections = iter(
        (
            SimpleNamespace(
                decision=SimpleNamespace(
                    candidate=candidate,
                    terminal_wealth=SimpleNamespace(win_probability_lcb=0.90),
                    no_trade_reason=None,
                ),
                winner_event_id=event.event_id,
                actuation=SimpleNamespace(
                    actuation_identity="actuation-loose",
                    wealth_witness_identity="wealth-1",
                ),
            ),
            SimpleNamespace(
                decision=SimpleNamespace(
                    candidate=candidate,
                    terminal_wealth=SimpleNamespace(win_probability_lcb=0.71),
                    no_trade_reason=None,
                ),
                winner_event_id=event.event_id,
                actuation=SimpleNamespace(
                    actuation_identity="actuation-tight",
                    wealth_witness_identity="wealth-1",
                ),
            ),
        )
    )
    calls = {
        "select_q": [],
        "selection_epoch": [],
        "preflight": 0,
        "venue": 0,
        "wealth": 0,
    }
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )

    def wealth(*_, **__):
        calls["wealth"] += 1
        return SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity=f"wealth-{calls['wealth']}",
            economic_identity=f"wealth-economics-{calls['wealth']}",
        )

    monkeypatch.setattr(
        global_batch_runtime, "current_portfolio_wealth_witness", wealth
    )

    def select(*_args, **kwargs):
        calls["select_q"].append(kwargs["payoff_q_lcb_by_candidate"])
        calls["selection_epoch"].append(kwargs["selection_epoch_identity"])
        return next(selections)

    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", select
    )

    def preflight(*_args):
        calls["preflight"] += 1
        if calls["preflight"] == 1:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="PROBABILITY_TIGHTENED",
                probability_tightening=(
                    global_batch_runtime.GlobalCandidateProbabilityTightening(
                        family_key=family_key,
                        bin_id="bin-a",
                        side="NO",
                        token_id="token-no-a",
                        probability_witness_identity="q-run-a",
                        payoff_q_lcb=0.71,
                    )
                ),
                reason="GLOBAL_CURRENT_STATE_PAYOFF_Q_TIGHTENED_REAUCTION_REQUIRED",
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token="binding-tight",
        )

    def actuate(event_arg, actuation, _at, token, _authority):
        assert event_arg.event_id == event.event_id
        assert actuation.actuation_identity == "actuation-tight"
        assert token == "binding-tight"
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event_arg.event_id,
            event_arg.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            _global_test_book("book-q", price="0.40"),
        ),
    )

    key = (family_key, "bin-a", "NO", "token-no-a")
    assert calls["select_q"] == [None, {key: 0.71}]
    assert calls["selection_epoch"][1] != calls["selection_epoch"][0]
    assert calls["preflight"] == 2
    assert calls["venue"] == 1
    assert calls["wealth"] == 1
    assert result.winner_event_id == event.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event.event_id].submitted is True


def test_global_batch_falls_through_candidate_local_preflight_block(monkeypatch):
    blocked_reason = "SHIFT_BIN_NO_SUBMIT:SHIFT_OLD_LEG_BELIEF_NOT_WEAKENED"
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event_a = _global_scope_event(city="Alpha", source_run_id="run-a")
    event_b = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event_a, event_b), captured_at_utc=decision_at
    )
    witnesses = {
        family_key: SimpleNamespace(
            family_key=family_key,
            captured_at_utc=decision_at,
            posterior_identity_hash=run_id,
            witness_identity=f"q-{run_id}",
        )
        for family_key, run_id in zip(scope.family_keys, ("run-a", "run-b"))
    }
    prepared = {
        event.event_id: SimpleNamespace(
            probability_witness=witnesses[family_key]
        )
        for event, family_key in zip((event_a, event_b), scope.family_keys)
    }
    candidates = {
        event_a.event_id: SimpleNamespace(family_key=scope.family_keys[0]),
        event_b.event_id: SimpleNamespace(family_key=scope.family_keys[1]),
    }
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(
                candidate=candidates[event.event_id], no_trade_reason=None
            ),
            winner_event_id=event.event_id,
            actuation=SimpleNamespace(
                actuation_identity=actuation_id,
                wealth_witness_identity=wealth_id,
            ),
        )
        for event, actuation_id, wealth_id in (
            (event_a, "actuation-a-fence", "wealth-1"),
            (event_b, "actuation-b-fallthrough", "wealth-1"),
        )
    )
    books = iter((_global_test_book("book-1", price="0.41"),))
    calls = {
        "prepare": 0,
        "books": 0,
        "wealth": 0,
        "preflight": [],
        "excluded": [],
        "epoch": [],
        "venue": 0,
    }

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )

    def wealth(*_, **__):
        calls["wealth"] += 1
        return SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity=f"wealth-{calls['wealth']}",
            economic_identity=f"wealth-economics-{calls['wealth']}",
        )

    monkeypatch.setattr(
        global_batch_runtime, "current_portfolio_wealth_witness", wealth
    )

    def select(_prepared, **kwargs):
        calls["excluded"].append(kwargs["preflight_excluded_by_family"])
        calls["epoch"].append(kwargs["selection_epoch_identity"])
        return next(selections)

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)

    def prepare(event, _at):
        calls["prepare"] += 1
        return EventSubmissionReceipt(
            False,
            event.event_id,
            event.causal_snapshot_id,
            prepared_global_family=prepared[event.event_id],
        )

    def book_provider(probabilities, _at):
        calls["books"] += 1
        return probabilities, next(books)

    def preflight(event, _actuation, _at, _authority):
        calls["preflight"].append(event.event_id)
        if event.event_id == event_a.event_id:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="BLOCKED", reason=blocked_reason
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE", binding_token="binding-b"
        )

    def actuate_preflighted(event, actuation, _at, token, _authority):
        assert event.event_id == event_b.event_id
        assert actuation.actuation_identity == "actuation-b-fallthrough"
        assert token == "binding-b"
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event_a, event_b),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate_preflighted
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=book_provider,
    )

    assert calls["prepare"] == 2
    assert calls["books"] == 1
    assert calls["wealth"] == 1
    assert calls["preflight"] == [event_a.event_id, event_b.event_id]
    assert calls["excluded"] == [
        None,
        {scope.family_keys[0]: blocked_reason},
    ]
    assert calls["epoch"][1] != calls["epoch"][0]
    assert calls["venue"] == 1
    assert result.winner_event_id == event_b.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event_b.event_id].submitted is True
    assert result.receipts[event_a.event_id].reason == (
        f"GLOBAL_PREFLIGHT_FAMILY_INELIGIBLE:{blocked_reason}"
    )


def test_global_batch_candidate_block_keeps_sibling_eligible(
    monkeypatch,
):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    family_key = scope.family_keys[0]
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=family_key,
            captured_at_utc=decision_at,
            posterior_identity_hash="run-a",
            witness_identity="q-run-a",
        )
    )
    candidate_a = SimpleNamespace(
        candidate_id="candidate-a",
        action="BUY",
        family_key=family_key,
        bin_id="bin-a",
        side="NO",
        token_id="token-a",
    )
    candidate_b = SimpleNamespace(
        candidate_id="candidate-b",
        action="SELL",
        family_key=family_key,
        bin_id="bin-a",
        side="NO",
        token_id="token-a",
    )
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
            winner_event_id=event.event_id,
            actuation=SimpleNamespace(
                decision=SimpleNamespace(candidate=candidate),
                actuation_identity=identity,
                wealth_witness_identity="wealth-1",
            ),
        )
        for candidate, identity in (
            (candidate_a, "actuation-a"),
            (candidate_b, "actuation-b"),
        )
    )
    base_asset = _global_test_book("book-candidate", price="0.40").assets[0]
    asset = SimpleNamespace(
        **(
            vars(base_asset)
            | {
                "family_key": family_key,
                "bin_id": candidate_a.bin_id,
                "token_id": candidate_a.token_id,
                "side": candidate_a.side,
            }
        )
    )
    book = SimpleNamespace(
        witness_identity="book-candidate",
        captured_at_utc=decision_at,
        max_age=_dt.timedelta(seconds=30),
        assets=(asset,),
        sell_assets=(asset,),
    )
    calls = {"select": 0, "wealth": 0, "preflight": [], "books": 0, "venue": 0}
    reason = "GLOBAL_CANDIDATE_ALL_SIZES_INFEASIBLE:candidate=candidate-a"

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )

    def wealth(*_, **__):
        calls["wealth"] += 1
        return SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-1",
            economic_identity="wealth-economics-1",
        )

    monkeypatch.setattr(
        global_batch_runtime, "current_portfolio_wealth_witness", wealth
    )

    def select(_prepared, **kwargs):
        policy = kwargs["candidate_policy_rejection_resolver"]
        if calls["select"] == 0:
            assert policy(candidate_a) is None
            assert policy(candidate_b) is None
        else:
            expected_a = (
                f"GLOBAL_PREFLIGHT_CANDIDATE_INELIGIBLE:{reason}"
                if calls["select"] == 1
                else None
            )
            assert policy(candidate_a) == expected_a
            assert policy(candidate_b) is None
            assert kwargs["preflight_excluded_by_family"] == {}
        calls["select"] += 1
        return next(selections)

    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", select
    )

    def preflight(_event, actuation, _at, _authority):
        candidate = actuation.decision.candidate
        calls["preflight"].append(candidate.candidate_id)
        if candidate is candidate_a:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="CANDIDATE_BLOCKED",
                reason=reason,
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token="binding-b",
        )

    def actuate(_event, actuation, _at, token, _authority):
        assert actuation.decision.candidate is candidate_b
        assert token == "binding-b"
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    def next_book(probabilities, _at):
        calls["books"] += 1
        return probabilities, book

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=next_book,
    )

    assert calls == {
        "select": 2,
        "wealth": 1,
        "preflight": ["candidate-a", "candidate-b"],
        "books": 1,
        "venue": 1,
    }
    assert result.winner_event_id == event.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event.event_id].submitted is True


@pytest.mark.parametrize(
    "batch_reason",
    (
        "live_health_entry_authority:failing_surfaces=runtime_code",
        "GLOBAL_CURRENT_STATE_PAYOFF_Q_TIGHTENED_REAUCTION_REQUIRED",
        "GLOBAL_CURRENT_STATE_ROBUST_MAJORITY_LOSS",
        "GLOBAL_CURRENT_STATE_ECONOMICS_NON_POSITIVE",
        (
            "GLOBAL_ACTUATION_PREPARE_FAILED:"
            "SPINE_INPUTS_UNAVAILABLE:MU_SIGMA_NOT_STASHED"
        ),
        "EVENT_BOUND_EXECUTABLE_SNAPSHOT_MISSING",
        "GLOBAL_ACTUATION_BOOK_SUPERSEDED",
        "UNCLASSIFIED_PREFLIGHT_FAILURE",
    ),
)
def test_global_batch_stops_on_batch_wide_preflight_block(monkeypatch, batch_reason):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    runner_up = _global_scope_event(city="Beta", source_run_id="run-b")
    scope = current_global_auction_scope_from_events(
        (event, runner_up), captured_at_utc=decision_at
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-run-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    runner_up_prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=scope.family_keys[1],
            captured_at_utc=decision_at,
            posterior_identity_hash="run-b",
            witness_identity="q-run-b",
        )
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(
            candidate=SimpleNamespace(family_key=scope.family_keys[0]),
            no_trade_reason=None,
        ),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth-1",
        ),
    )
    calls = {
        "books": 0,
        "select": 0,
        "preflight": 0,
        "preflight_receipt": 0,
        "venue": 0,
    }
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-1",
            economic_identity="wealth-economics-1",
        ),
    )

    def select(*_args, **_kwargs):
        calls["select"] += 1
        return selected

    def books(probabilities, _at):
        calls["books"] += 1
        return probabilities, _global_test_book("book-fence", price="0.40")

    def preflight(*_args):
        calls["preflight"] += 1
        return global_batch_runtime.GlobalWinnerPreflight(
            status="BATCH_BLOCKED",
            reason=batch_reason,
        )

    def store_preflight(*_args, **kwargs):
        calls["preflight_receipt"] += 1
        assert kwargs["preflight"].reason == batch_reason
        assert kwargs["venue_submit_count_before"] == 0
        assert kwargs["venue_submit_count_after"] == 0
        return 1

    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", select
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_preflight_receipt",
        store_preflight,
    )
    result = global_batch_runtime.process_current_global_batch(
        (event, runner_up),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=(
                prepared if current.event_id == event.event_id else runner_up_prepared
            ),
        ),
        actuate_winner=lambda *_: pytest.fail("batch block must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda *_: pytest.fail("batch block must not actuate")
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=books,
    )

    assert calls == {
        "books": 1,
        "select": 1,
        "preflight": 1,
        "preflight_receipt": 1,
        "venue": 0,
    }
    assert result.winner_event_id is None
    assert result.venue_submit_count == 0
    assert result.receipts[event.event_id].reason == (
        f"GLOBAL_PREFLIGHT_BATCH_BLOCKED:{batch_reason}"
    )
    assert result.receipts[runner_up.event_id].reason == (
        f"GLOBAL_PREFLIGHT_BATCH_BLOCKED:{batch_reason}"
    )


def test_global_batch_reauctions_until_current_curve_stabilizes(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    candidates = tuple(
        _global_test_buy_candidate(
            family_key=scope.family_keys[0],
            probability_witness_identity=witness.witness_identity,
            book_identity=f"book-{index}",
            price=price,
            captured_at=decision_at,
        )
        for index, price in enumerate(("0.40", "0.41", "0.42"))
    )
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
            winner_event_id=event.event_id,
            actuation=SimpleNamespace(
                actuation_identity=f"actuation-{index}",
                wealth_witness_identity="wealth",
            ),
        )
        for index, candidate in enumerate(candidates)
    )
    initial_book = _global_test_candidate_book(
        candidates[0],
        epoch_captured_at=decision_at,
    )
    calls = {"books": 0, "preflight": 0, "select": 0, "venue": 0}
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    def select(*_, **__):
        calls["select"] += 1
        return next(selections)

    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", select)

    def preflight(*_):
        calls["preflight"] += 1
        if calls["preflight"] == 3:
            return global_batch_runtime.GlobalWinnerPreflight(
                status="STABLE",
                binding_token="binding-a",
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="CURVE_SUPERSEDED",
            replacement_candidate=candidates[calls["preflight"]],
            reason=f"curve moved {calls['preflight']}",
        )

    def actuate_preflighted(_event, _actuation, _at, token, authority):
        assert token == "binding-a"
        assert authority.book_epoch_identity != initial_book.witness_identity
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate_preflighted
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            calls.__setitem__("books", calls["books"] + 1) or probabilities,
            initial_book,
        ),
    )

    assert calls == {"books": 1, "preflight": 3, "select": 3, "venue": 1}
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True


def test_global_batch_stable_preflight_cannot_cross_epoch_deadline(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    calls = {"preflight": 0, "venue": 0}
    current_times = iter(
        (
            decision_at,
            decision_at,
            decision_at,
            decision_at,
            decision_at + _dt.timedelta(seconds=29.9),
            decision_at + _dt.timedelta(seconds=30.1),
        )
    )
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected
    )

    def preflight(*_):
        calls["preflight"] += 1
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token="binding-a",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda *_: pytest.fail("must not actuate")
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: next(current_times),
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            _global_test_book("book", price="0.40"),
        ),
    )

    assert calls == {"preflight": 1, "venue": 0}
    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_REAUCTION_EPOCH_EXPIRED"
    )


def test_global_batch_curve_reauction_requires_new_epoch_identity(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-a",
    )
    prepared = SimpleNamespace(probability_witness=witness)
    candidate = _global_test_buy_candidate(
        family_key=scope.family_keys[0],
        probability_witness_identity=witness.witness_identity,
        book_identity="book",
        price="0.40",
        captured_at=decision_at,
    )
    book = _global_test_candidate_book(
        candidate,
        epoch_captured_at=decision_at,
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    calls = {"preflight": 0, "venue": 0}
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected
    )

    def preflight(*_):
        calls["preflight"] += 1
        return global_batch_runtime.GlobalWinnerPreflight(
            status="CURVE_SUPERSEDED",
            replacement_candidate=candidate,
            reason="curve changed without a new epoch",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            lambda *_: pytest.fail("must not actuate")
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            book,
        ),
    )

    assert calls == {"preflight": 1, "venue": 0}
    assert result.venue_submit_count == 0
    assert result.winner_event_id is None
    assert result.receipts[event.event_id].reason == (
        "GLOBAL_REAUCTION_CURVE_NO_PROGRESS:"
        "curve changed without a new epoch"
    )


def test_global_batch_uses_one_probability_and_book_fence_cut(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    initial_witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-cut-a",
    )
    prepared = SimpleNamespace(probability_witness=initial_witness)
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    calls = {"books": 0, "preflight": 0, "venue": 0}
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected
    )

    def book_provider(probabilities, _at):
        calls["books"] += 1
        return probabilities, _global_test_book("book-fence", price="0.40")

    def preflight(*_):
        calls["preflight"] += 1
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token="binding-a",
        )

    def actuate(event, _actuation, _at, token, _authority):
        assert token == "binding-a"
        calls["venue"] += 1
        return EventSubmissionReceipt(
            True,
            event.event_id,
            event.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        ),
        actuate_winner=lambda *_: pytest.fail("must not actuate"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: calls["venue"],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=book_provider,
    )

    assert calls == {"books": 1, "preflight": 1, "venue": 1}
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True


def test_global_batch_commits_receipts_before_external_io(monkeypatch, tmp_path):
    import src.state.portfolio as portfolio

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events(
        (event,), captured_at_utc=decision_at
    )
    witness = SimpleNamespace(
        family_key=scope.family_keys[0],
        captured_at_utc=decision_at,
        posterior_identity_hash="run-a",
        witness_identity="q-a",
    )
    candidate = SimpleNamespace(
        action="BUY",
        family_key=scope.family_keys[0],
        bin_id="bin-a",
        condition_id="condition-a",
        side="YES",
        token_id="token-a",
        candidate_id="candidate-a",
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=candidate, no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    path = tmp_path / "receipt-boundary.db"
    trade_conn = sqlite3.connect(path)
    observer = sqlite3.connect(path, timeout=0)
    assert trade_conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    trade_conn.execute("CREATE TABLE receipt_marks (stage TEXT NOT NULL)")
    trade_conn.commit()
    stages = []
    venue_calls = [0]

    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    monkeypatch.setattr(
        global_batch_runtime, "probe_inflight_buy_ambiguity", lambda _conn: False
    )
    monkeypatch.setattr(
        global_batch_runtime, "_current_held_weather_families", lambda _conn: ()
    )
    monkeypatch.setattr(portfolio, "load_runtime_open_portfolio", lambda _conn: None)
    monkeypatch.setattr(
        global_batch_runtime,
        "replace",
        lambda value, **changes: SimpleNamespace(**(vars(value) | changes)),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected
    )

    def persist(stage):
        def _persist(conn, **_kwargs):
            conn.execute("INSERT INTO receipt_marks VALUES (?)", (stage,))
            assert conn.in_transaction
            return 1

        return _persist

    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_auction_receipt",
        persist("selection"),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "_store_global_preflight_receipt",
        persist("preflight"),
    )

    def assert_writer_released(stage):
        assert not trade_conn.in_transaction
        observer.execute("BEGIN IMMEDIATE")
        observer.rollback()
        stages.append(stage)

    def preflight(*_):
        assert_writer_released("preflight")
        return global_batch_runtime.GlobalWinnerPreflight(
            status="STABLE",
            binding_token="binding-a",
        )

    def actuate(current, _actuation, _at, token, _authority):
        assert token == "binding-a"
        assert_writer_released("actuation")
        venue_calls[0] += 1
        return EventSubmissionReceipt(
            True,
            current.event_id,
            current.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=object(),
        trade_conn=trade_conn,
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=lambda current, _at: EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=SimpleNamespace(probability_witness=witness),
        ),
        actuate_winner=lambda *_: pytest.fail("preflighted lane owns actuation"),
        preflight_winner=preflight,
        actuate_preflighted_winner=global_batch_runtime.GlobalOneShotActuator(
            actuate
        ),
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=lambda: venue_calls[0],
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        current_book_epoch_provider=lambda probabilities, _at: (
            probabilities,
            _global_test_book("book", price="0.40", captured_at=decision_at),
        ),
    )

    assert stages == ["preflight", "actuation"]
    assert trade_conn.execute(
        "SELECT stage FROM receipt_marks ORDER BY rowid"
    ).fetchall() == [("selection",), ("preflight",)]
    assert result.venue_submit_count == 1
    assert result.receipts[event.event_id].submitted is True
    observer.close()
    trade_conn.close()


def test_global_batch_freezes_cut_then_releases_before_winner_jit(
    monkeypatch, tmp_path
):
    import src.data.replacement_input_hwm as input_hwm

    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)
    prepared = SimpleNamespace(
        probability_witness=SimpleNamespace(
            family_key=scope.family_keys[0],
            captured_at_utc=decision_at,
            posterior_identity_hash="run-a",
        )
    )
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(actuation_identity="actuation-a"),
    )
    current_probability = object()
    path = tmp_path / "batch-cut.db"
    seed = sqlite3.connect(path)
    assert seed.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    seed.execute("CREATE TABLE readiness_state (value TEXT NOT NULL)")
    seed.execute("INSERT INTO readiness_state VALUES ('cut')")
    seed.commit()
    seed.close()
    selection = sqlite3.connect(path)
    writer = sqlite3.connect(path)
    scope_reads = []
    held_families = (("Held", "2026-07-09", "high"),)

    def scan(**kwargs):
        scope_reads.append(1)
        assert kwargs["held_families"] == held_families
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
        writer.execute("UPDATE readiness_state SET value='after-cut'")
        writer.commit()
        return scope

    def prepare(current, _at):
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
        assert input_hwm._FROZEN_INPUT_HWM.get() is not None
        return EventSubmissionReceipt(
            False,
            current.event_id,
            current.causal_snapshot_id,
            prepared_global_family=prepared,
        )

    def actuate(winner, _chosen, _at):
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "after-cut"
        assert input_hwm._FROZEN_INPUT_HWM.get() is None
        return EventSubmissionReceipt(
            True,
            winner.event_id,
            winner.causal_snapshot_id,
            proof_accepted=True,
            side_effect_status="SUBMITTED",
        )

    monkeypatch.setattr(global_batch_runtime, "scan_current_global_auction_scope", scan)
    monkeypatch.setattr(
        global_batch_runtime,
        "_current_held_weather_families",
        lambda _conn: held_families,
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_portfolio_wealth_witness",
        lambda *_, **__: SimpleNamespace(
            spendable_cash_usd=Decimal("10"),
            witness_identity="wealth-certificate",
            economic_identity="wealth-economics",
        ),
    )
    monkeypatch.setattr(
        global_batch_runtime,
        "current_venue_auction_identity",
        lambda *_, **__: "venue-before",
    )
    monkeypatch.setattr(global_batch_runtime, "select_prepared_global_auction", lambda *_, **__: selected)
    monkeypatch.setattr(
        global_batch_runtime.CurrentFamilyProbabilityAuthority,
        "from_witness",
        classmethod(lambda cls, witness: current_probability),
    )

    result = global_batch_runtime.process_current_global_batch(
        (event,),
        decision_time=decision_at,
        world_conn=object(),
        forecast_conn=selection,
        trade_conn=object(),
        payload_reader=lambda current: json.loads(current.payload_json),
        prepare_event=prepare,
        actuate_winner=actuate,
        stamp_receipt=lambda receipt: receipt,
        venue_submit_count=iter((0, 1)).__next__,
        current_execution=lambda *_: object(),
        current_time_provider=lambda: decision_at,
        selection_snapshot_connections=(selection,),
    )

    assert scope_reads == [1]
    assert result.venue_submit_count == 1
    assert result.winner_event_id == event.event_id
    assert result.receipts[event.event_id].submitted is True
    assert input_hwm._FROZEN_INPUT_HWM.get() is None
    selection.close()
    writer.close()


def test_global_batch_rejects_mixed_probability_manifest(monkeypatch):
    decision_at = _dt.datetime(2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc)
    event = _global_scope_event(city="Alpha", source_run_id="run-a")
    scope = current_global_auction_scope_from_events((event,), captured_at_utc=decision_at)
    monkeypatch.setattr(
        global_batch_runtime, "scan_current_global_auction_scope", lambda **_: scope
    )
    cases = (
        (
            SimpleNamespace(
                family_key=scope.family_keys[0],
                captured_at_utc=decision_at + _dt.timedelta(microseconds=1),
                posterior_identity_hash="run-a",
            ),
            "GLOBAL_PROBABILITY_EPOCH_MIXED_CUT",
        ),
        (
            SimpleNamespace(
                family_key=scope.family_keys[0],
                captured_at_utc=decision_at,
                posterior_identity_hash="run-after-cut",
            ),
            f"GLOBAL_PROBABILITY_EPOCH_CARRIER_MISMATCH:{scope.family_keys[0]}",
        ),
    )
    for witness, expected_reason in cases:
        prepared = SimpleNamespace(probability_witness=witness)
        result = global_batch_runtime.process_current_global_batch(
            (event,),
            decision_time=decision_at,
            world_conn=object(),
            forecast_conn=object(),
            trade_conn=object(),
            payload_reader=lambda current: json.loads(current.payload_json),
            prepare_event=lambda current, _at: EventSubmissionReceipt(
                False,
                current.event_id,
                current.causal_snapshot_id,
                prepared_global_family=prepared,
            ),
            actuate_winner=lambda *_: pytest.fail(
                "a mixed probability manifest must never actuate"
            ),
            stamp_receipt=lambda receipt: receipt,
            venue_submit_count=lambda: 0,
            current_execution=lambda *_: object(),
            current_time_provider=lambda: decision_at,
        )

        assert result.venue_submit_count == 0
        assert result.receipts[event.event_id].reason == expected_reason


def test_global_selection_read_snapshot_holds_one_readiness_cut(tmp_path):
    path = tmp_path / "selection-cut.db"
    seed = sqlite3.connect(path)
    assert seed.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    seed.execute("CREATE TABLE readiness_state (value TEXT NOT NULL)")
    seed.execute("INSERT INTO readiness_state VALUES ('cut')")
    seed.commit()
    seed.close()

    selection = sqlite3.connect(path)
    writer = sqlite3.connect(path)
    release = global_batch_runtime._begin_selection_read_snapshot(
        (selection, selection)
    )
    try:
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
        writer.execute("UPDATE readiness_state SET value='after-cut'")
        writer.commit()
        assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "cut"
    finally:
        release()
    assert selection.execute("SELECT value FROM readiness_state").fetchone()[0] == "after-cut"
    selection.execute("BEGIN")
    with pytest.raises(
        RuntimeError, match="GLOBAL_SELECTION_SNAPSHOT_CALLER_TXN_OPEN"
    ):
        global_batch_runtime._begin_selection_read_snapshot((selection,))
    selection.rollback()
    selection.close()
    writer.close()


def test_global_selection_schema_reads_are_cached_only_inside_owned_snapshot():
    import src.data.market_topology_rows as topology_rows

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    traced: list[str] = []
    conn.set_trace_callback(traced.append)
    release_snapshot = global_batch_runtime._begin_selection_read_snapshot((conn,))
    release_schema = topology_rows.prime_frozen_schema_reads((conn,))
    try:
        for _ in range(2):
            assert "main" in topology_rows._database_names(conn)
            assert topology_rows._table_ref_exists(conn, "sample") is True
            assert topology_rows._table_ref_columns(conn, "sample") == {"value"}
    finally:
        release_schema()
        release_snapshot()

    assert "main" in topology_rows._database_names(conn)
    assert topology_rows._table_ref_exists(conn, "sample") is True
    assert topology_rows._table_ref_columns(conn, "sample") == {"value"}
    conn.set_trace_callback(None)

    normalized = [" ".join(statement.upper().split()) for statement in traced]
    assert sum(statement == "PRAGMA DATABASE_LIST" for statement in normalized) == 2
    assert sum(
        "FROM SQLITE_MASTER" in statement and "NAME = 'SAMPLE'" in statement
        for statement in normalized
    ) == 2
    assert sum(statement == "PRAGMA TABLE_INFO(SAMPLE)" for statement in normalized) == 2


# --- (d) OFF-path import-isolation (subprocess) -----------------------------

def test_g3_off_path_does_not_import_src_solve():
    script = textwrap.dedent(
        """
        import sys, datetime
        from decimal import Decimal
        from src.config import settings
        settings["feature_flags"].pop("w3_solve_enabled", None)  # OFF/absent
        import src.engine.qkernel_spine_bridge as bridge
        import src.engine.event_reactor_adapter as era
        from src.strategy import utility_ranker
        bridge.SPINE_BAND_DRAWS = 400
        from tests.integration import test_qkernel_spine_routing as R
        fam, _ = R._three_bin_family()
        proofs = R._proofs_for(fam, yes_asks=[0.05,0.20,0.20,0.05], no_asks=[0.92,0.75,0.75,0.92],
                               q_by_bin=[0.05,0.45,0.40,0.10], q_lcb_by_bin=[0.02,0.32,0.28,0.05])
        payload = R._payload_with_spine_inputs(mu=20.4, sigma=1.2, members=[19.8,20.1,20.5,21.0,20.7])
        assert bridge.w3_solve_enabled() is False
        _ = bridge.decide_family_via_spine(  # a full decide with the flag OFF
            family=fam, payload=payload, proofs=proofs,
            decision_time=datetime.datetime(2026,6,13,12,0,tzinfo=datetime.timezone.utc),
            native_side_candidate_from_proof=era._native_side_candidate_from_proof,
            candidate_bin_id=era._candidate_bin_id,
            payoff_matrix_over_bins=utility_ranker.FamilyPayoffMatrix.over_bins,
            exposure_builder=era._robust_marginal_utility_exposure,
            baseline_usd_provider=lambda: Decimal("1000"),
            per_bin_yes_q_lcb=era._per_bin_yes_q_lcb(proofs), extra_exposure_by_bin_id=None,
        )
        leaked = [m for m in sys.modules if m.startswith('src.solve')]
        assert not leaked, f'OFF path imported src.solve: {leaked}'
        print('ISOLATION_OK')
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=".")
    assert "ISOLATION_OK" in proc.stdout, f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"


def _adapter_sell_actuation(event, *, selected_shares="10"):
    at = _dt.datetime(2026, 7, 13, 12, 0, tzinfo=_dt.timezone.utc)
    curve = ExecutableSellCurve(
        token_id="yes-token",
        side="YES",
        snapshot_id="selected-sell-book",
        book_hash="selected-sell-hash",
        levels=(
            BookLevel(price=Decimal("0.60"), size=Decimal("4")),
            BookLevel(price=Decimal("0.50"), size=Decimal("6")),
        ),
        fee_model=FeeModel(fee_rate=Decimal("0")),
        min_tick=Decimal("0.01"),
        min_order_size=Decimal("5"),
        quote_ttl=_dt.timedelta(seconds=30),
    )
    candidate = GlobalSingleOrderSellCandidate(
        candidate_id="sell-position-1",
        family_key="Alpha|2026-07-14|high",
        bin_id="20C",
        condition_id="condition-1",
        side="YES",
        token_id="yes-token",
        position_id="position-1",
        held_shares=Decimal("10"),
        probability_witness_identity="probability-1",
        book_snapshot_id=curve.snapshot_id,
        book_captured_at_utc=at,
        execution_curve_identity=executable_curve_identity(curve),
        ledger_snapshot_id="ledger-1",
        executable_sell_curve=curve,
        resolution_identity="resolution-1",
    )
    selected = Decimal(selected_shares)
    proceeds, expected_fill_price, limit_price = curve.proceeds_for_shares(selected)
    loss_at_risk = selected - proceeds
    robust_q = 0.70
    loss_after = Decimal("110") - selected + proceeds
    win_after = Decimal("100") + proceeds
    robust_du = (1.0 - robust_q) * np.log(float(loss_after / Decimal("110"))) + robust_q * np.log(
        float(win_after / Decimal("100"))
    )
    robust_ev = float(proceeds) - (1.0 - robust_q) * float(selected)
    decision = GlobalSingleOrderDecision(
        candidate=candidate,
        shares=selected,
        cost_usd=loss_at_risk,
        robust_delta_log_wealth=float(robust_du),
        robust_ev_usd=robust_ev,
        capital_efficiency=float(robust_du) / float(loss_at_risk),
        no_trade_reason=None,
        limit_price=limit_price,
        expected_fill_price_before_fee=expected_fill_price,
        cash_proceeds_usd=proceeds,
        terminal_wealth=BinaryTerminalWealthCertificate(
            win_probability_lcb=robust_q,
            loss_probability_ucb=1.0 - robust_q,
            loss_payoff_usd=-loss_at_risk,
            win_payoff_usd=proceeds,
            median_payoff_usd=proceeds,
            wealth_after_loss_usd=loss_after,
            wealth_after_win_usd=win_after,
            expected_value_diagnostic_usd=robust_ev,
        ),
    )
    witness = SimpleNamespace(
        bin_ids=("20C",),
        yes_q_samples=np.asarray([[0.30], [0.30]], dtype=np.float64),
    )
    return SimpleNamespace(
        decision=decision,
        winner_event_id=event.event_id,
        probability_witness=witness,
        actuation_identity="sell-actuation-1",
        wealth_economic_identity="wealth-1",
    )


def test_global_sell_adapter_bypasses_entry_lane_and_uses_reduce_only_exit(
    monkeypatch,
):
    event = _global_scope_event(city="Alpha", source_run_id="run-sell")
    actuation = _adapter_sell_actuation(event, selected_shares="6")
    position = SimpleNamespace(
        trade_id="position-1",
        direction="buy_yes",
        token_id="yes-token",
        no_token_id="no-token",
        condition_id="condition-1",
        chain_shares=10.0,
        effective_shares=10.0,
        exit_state="",
        last_exit_order_id="",
        state="holding",
    )
    portfolio = SimpleNamespace(
        authority="canonical_db",
        authority_scope="runtime_exposure",
        positions=[position],
    )
    monkeypatch.setattr(
        era, "_current_global_actuation_prepared_family", lambda *_, **__: object()
    )
    monkeypatch.setattr(
        era,
        "_global_actuation_current_wealth_block_reason",
        lambda *_, **__: None,
    )
    monkeypatch.setattr(
        "src.state.portfolio.load_runtime_open_portfolio", lambda _conn: portfolio
    )

    class Clob:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get_orderbook_snapshots(self, tokens, *, timeout):
            assert timeout >= 1.0
            assert tokens == ["yes-token"]
            return {
                "yes-token": {
                    "asset_id": "yes-token",
                    "hash": "jit-sell-hash",
                    "tick_size": "0.01",
                    "min_order_size": "5",
                    "bids": [
                        {"price": "0.60", "size": "4"},
                        {"price": "0.50", "size": "6"},
                    ],
                }
            }

        def get_fee_rate_details(self, token_id):
            assert token_id == "yes-token"
            return {"fee_rate_fraction": 0}

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", Clob)
    exits = []

    def execute_exit(portfolio_arg, position_arg, context, **kwargs):
        exits.append((portfolio_arg, position_arg, context, kwargs))
        assert kwargs["exit_intent"].exact_limit_price == pytest.approx(0.50)
        assert kwargs["exit_intent"].shares == pytest.approx(6.0)
        assert kwargs["exit_intent"].close_position is False
        assert kwargs["exit_intent"].submit_order_type == "FAK"
        evidence = kwargs["execution_evidence"]
        evidence.venue_call_started = True
        evidence.venue_ack_received = True
        evidence.command_id = "command-1"
        evidence.command_state = "ACKED"
        evidence.order_type = "FAK"
        evidence.result_status = "pending"
        return "sell_placed: order=sell-1"

    monkeypatch.setattr("src.execution.exit_lifecycle.execute_exit", execute_exit)
    conn = sqlite3.connect(":memory:")
    at = _dt.datetime(2026, 7, 13, 12, 0, tzinfo=_dt.timezone.utc)
    preflight = era._submit_current_global_sell(
        event,
        decision_time=at,
        global_actuation=actuation,
        trade_conn=conn,
        forecast_conn=object(),
        topology_conn=object(),
        calibration_conn=object(),
        real_order_submit_enabled=True,
        preflight_only=True,
        preflight_receipt=None,
    )
    assert preflight.reason == "GLOBAL_SELL_PREFLIGHT_STABLE"
    assert preflight.proof_accepted is True
    receipt = era._submit_current_global_sell(
        event,
        decision_time=at,
        global_actuation=actuation,
        trade_conn=conn,
        forecast_conn=object(),
        topology_conn=object(),
        calibration_conn=object(),
        real_order_submit_enabled=True,
        preflight_only=False,
        preflight_receipt=preflight,
    )
    assert receipt.submitted is True
    assert receipt.side_effect_status == "EXIT_SUBMITTED"
    assert receipt.reason == "GLOBAL_SELL_EXIT:sell_placed: order=sell-1"
    assert receipt.venue_call_started is True
    assert receipt.venue_ack_received is True
    assert receipt.venue_command_id == "command-1"
    assert receipt.venue_command_state == "ACKED"
    assert receipt.venue_order_type == "FAK"
    from src.events.reactor import (
        _is_global_reduce_only_exit_receipt,
        _receipt_matches_event,
    )

    assert _is_global_reduce_only_exit_receipt(receipt) is True
    assert _receipt_matches_event(event, receipt) is True
    assert len(exits) == 1

    def fail_before_venue(*_args, **_kwargs):
        raise RuntimeError("intent persistence failed")

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        fail_before_venue,
    )
    rejected = era._submit_current_global_sell(
        event,
        decision_time=at,
        global_actuation=actuation,
        trade_conn=conn,
        forecast_conn=object(),
        topology_conn=object(),
        calibration_conn=object(),
        real_order_submit_enabled=True,
        preflight_only=False,
        preflight_receipt=preflight,
    )
    assert rejected.submitted is False
    assert rejected.proof_accepted is False
    assert rejected.venue_call_started is False
    assert rejected.venue_ack_received is False
    assert rejected.reason.startswith("GLOBAL_SELL_EXECUTION_FAILED:RuntimeError:")

    def fail_after_unknown_call(*_args, **kwargs):
        evidence = kwargs["execution_evidence"]
        evidence.venue_call_started = True
        evidence.venue_ack_received = False
        evidence.command_id = "command-unknown"
        evidence.command_state = "SUBMIT_UNKNOWN_SIDE_EFFECT"
        evidence.order_type = "FAK"
        evidence.result_status = "unknown_side_effect"
        raise TimeoutError("venue result unknown")

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        fail_after_unknown_call,
    )
    unknown = era._submit_current_global_sell(
        event,
        decision_time=at,
        global_actuation=actuation,
        trade_conn=conn,
        forecast_conn=object(),
        topology_conn=object(),
        calibration_conn=object(),
        real_order_submit_enabled=True,
        preflight_only=False,
        preflight_receipt=preflight,
    )
    assert unknown.submitted is True
    assert unknown.proof_accepted is True
    assert unknown.venue_call_started is True
    assert unknown.venue_ack_received is False
    assert unknown.venue_command_id == "command-unknown"
    assert unknown.reason.startswith("GLOBAL_SELL_EXIT_UNKNOWN:TimeoutError:")
    assert _is_global_reduce_only_exit_receipt(unknown) is True

    def fail_after_deterministic_reject(*_args, **kwargs):
        evidence = kwargs["execution_evidence"]
        evidence.venue_call_started = True
        evidence.venue_ack_received = False
        evidence.command_id = "command-rejected"
        evidence.command_state = "REJECTED"
        evidence.order_type = "FAK"
        evidence.result_status = "rejected"
        evidence.result_reason = "venue rejected"
        raise RuntimeError("lifecycle persistence failed after rejection")

    monkeypatch.setattr(
        "src.execution.exit_lifecycle.execute_exit",
        fail_after_deterministic_reject,
    )
    deterministic_reject = era._submit_current_global_sell(
        event,
        decision_time=at,
        global_actuation=actuation,
        trade_conn=conn,
        forecast_conn=object(),
        topology_conn=object(),
        calibration_conn=object(),
        real_order_submit_enabled=True,
        preflight_only=False,
        preflight_receipt=preflight,
    )
    assert deterministic_reject.submitted is False
    assert deterministic_reject.proof_accepted is False
    assert deterministic_reject.venue_call_started is True
    assert deterministic_reject.venue_ack_received is False
    assert deterministic_reject.venue_command_state == "REJECTED"
    assert deterministic_reject.reason.startswith(
        "GLOBAL_SELL_EXIT_REJECTED:RuntimeError:"
    )
    assert _is_global_reduce_only_exit_receipt(deterministic_reject) is False

    source = inspect.getsource(era.event_bound_live_adapter_from_trade_conn)
    assert source.index("if _global_sell_candidate(global_actuation) is not None") < source.index(
        "if real_order_submit_enabled and not durable_submit_outbox_enabled"
    )
    assert "executor_submit" not in inspect.getsource(era._submit_current_global_sell)


def test_global_sell_worse_jit_bid_batch_blocks_without_buy_overlay():
    event = _global_scope_event(city="Alpha", source_run_id="run-sell")
    actuation = _adapter_sell_actuation(event)
    worse = era._global_sell_candidate_from_raw_book(
        actuation.decision.candidate,
        {
            "asset_id": "yes-token",
            "hash": "worse",
            "tick_size": "0.01",
            "min_order_size": "5",
            "bids": [{"price": "0.49", "size": "10"}],
        },
        captured_at_utc=_dt.datetime.now(_dt.timezone.utc),
    )
    drift = era._global_sell_execution_economics_drift(
        decision=actuation.decision,
        current_candidate=worse,
    )
    assert drift is not None
    receipt = era._global_sell_receipt(
        event,
        global_actuation=actuation,
        reason=(
            "GLOBAL_ACTUATION_EXECUTION_BINDING_SUPERSEDED:"
            f"curve_economics:{drift}"
        ),
        proof_accepted=False,
        jit_candidate=worse,
    )
    assert era._global_curve_supersession_from_receipt(receipt) == (
        "BATCH_BLOCKED",
        None,
        receipt.reason,
    )


def test_global_sell_uses_complement_probability_and_every_fak_prefix_is_positive():
    event = _global_scope_event(city="Alpha", source_run_id="run-sell")
    actuation = _adapter_sell_actuation(event)
    witness = SimpleNamespace(
        bin_ids=("20C",),
        yes_q_samples=np.asarray([[0.20], [0.30]], dtype=np.float64),
    )
    assert era._global_sell_held_probability(
        SimpleNamespace(bin_id="20C", side="YES"), witness
    ) == pytest.approx(0.25)
    assert era._global_sell_held_probability(
        SimpleNamespace(bin_id="20C", side="NO"), witness
    ) == pytest.approx(0.75)

    decision = actuation.decision
    curve = decision.candidate.executable_sell_curve
    for cents in range(1, 1001):
        shares = Decimal(cents) / Decimal("100")
        remaining = shares
        proceeds = Decimal("0")
        for level in curve.levels:
            take = min(remaining, level.size)
            proceeds += take * curve.net_price(level.price)
            remaining -= take
            if remaining <= 0:
                break
        robust_du, robust_ev = global_sell_fill_prefix_objective(
            decision,
            filled_shares=shares,
            net_proceeds_usd=proceeds,
        )
        assert robust_du > 0.0
        assert robust_ev > 0.0


def test_exact_sell_limit_is_audited_and_off_tick_is_rejected_before_submit(
    monkeypatch,
):
    from src.execution.executor import (
        ExitOrderIntent,
        _align_sell_limit_price_to_tick,
        _exit_base_limit_price,
        _resolve_exit_order_type,
        execute_exit_order,
    )
    from src.execution.exit_lifecycle import ExitIntent, _exit_intent_audit_payload

    assert _align_sell_limit_price_to_tick(0.50, Decimal("0.01")) == pytest.approx(
        0.50
    )
    assert _resolve_exit_order_type("GTC", "FAK") == "FAK"
    assert _resolve_exit_order_type("FOK", "FAK") == "FAK"
    assert _exit_base_limit_price(0.001, Decimal("0.001")) == pytest.approx(
        0.001
    )
    assert _exit_base_limit_price(0.01, Decimal("0.01")) == pytest.approx(0.01)
    audit = _exit_intent_audit_payload(
        ExitIntent(
            trade_id="position-1",
            reason="GLOBAL_CAPITAL_OPTIMAL_SELL",
            token_id="yes-token",
            shares=10.0,
            current_market_price=0.54,
            best_bid=0.60,
            exact_limit_price=0.50,
            submit_order_type="FAK",
            capital_certificate={"robust_delta_log_wealth": 0.01},
        )
    )
    assert audit["exit_intent_exact_limit_price"] == pytest.approx(0.50)
    assert audit["exit_intent_submit_order_type"] == "FAK"
    assert audit["exit_intent_capital_certificate"] == {
        "robust_delta_log_wealth": 0.01
    }
    monkeypatch.setattr("src.architecture.gate_runtime.check", lambda *_: None)
    result = execute_exit_order(
        ExitOrderIntent(
            trade_id="position-1",
            token_id="yes-token",
            shares=10.0,
            current_price=0.54,
            best_bid=0.60,
            exact_limit_price=0.505,
            executable_snapshot_min_tick_size="0.01",
        )
    )
    assert result.status == "rejected"
    assert result.reason.startswith("exact_limit_price_not_tick_aligned:")


def test_global_sell_fak_reaches_exit_envelope_and_sdk_when_allocator_is_gtc(
    monkeypatch,
):
    from tests import test_executor as executor_fixtures
    from src.execution.executor import create_exit_order_intent, execute_exit_order
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    old_test_conn = executor_fixtures._TEST_CONN
    executor_fixtures._TEST_CONN = conn
    captured = {}

    class DummyClient:
        def __init__(self):
            self.bound_envelope = None

        def bind_submission_envelope(self, envelope):
            self.bound_envelope = envelope

        def place_limit_order(self, *, token_id, price, size, side, order_type):
            captured.update(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                order_type=order_type,
                envelope_order_type=self.bound_envelope.order_type,
            )
            return executor_fixtures._final_submit_result(
                self.bound_envelope,
                order_id="global-sell-fak-1",
            )

    monkeypatch.setattr("src.data.polymarket_client.PolymarketClient", DummyClient)
    monkeypatch.setattr(
        "src.execution.executor._refresh_exit_collateral_snapshot_for_submit",
        lambda *_args, **_kwargs: {
            "component": "collateral_snapshot_refresh",
            "allowed": True,
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._assert_collateral_allows_sell",
        lambda *_args, **_kwargs: {
            "component": "collateral_sell_preflight",
            "allowed": True,
        },
    )
    monkeypatch.setattr(
        "src.execution.executor._select_risk_allocator_order_type",
        lambda *_args, **_kwargs: "GTC",
    )
    monkeypatch.setattr(
        "src.execution.executor._reserve_collateral_for_sell",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.control.cutover_guard.assert_submit_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.control.heartbeat_supervisor.assert_heartbeat_allows_order_type",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "src.control.ws_gap_guard.assert_ws_allows_submit",
        lambda *_args, **_kwargs: None,
    )
    try:
        result = execute_exit_order(
            create_exit_order_intent(
                trade_id="position-global-fak",
                token_id="yes-token",
                shares=10.0,
                current_price=0.54,
                best_bid=0.60,
                exact_limit_price=0.50,
                submit_order_type="FAK",
                **executor_fixtures._snapshot_kwargs("yes-token"),
            ),
            conn=conn,
            decision_id="global-capital-sell-fak",
        )
    finally:
        executor_fixtures._TEST_CONN = old_test_conn

    assert result.status == "pending"
    assert result.venue_call_started is True
    assert result.venue_ack_received is True
    assert result.submitted_order_type == "FAK"
    assert captured["side"] == "SELL"
    assert captured["order_type"] == "FAK"
    assert captured["envelope_order_type"] == "FAK"
    envelope = conn.execute(
        """
        SELECT e.order_type
          FROM venue_commands c
          JOIN venue_submission_envelopes e ON e.envelope_id = c.envelope_id
         WHERE c.decision_id = ?
        """,
        ("global-capital-sell-fak",),
    ).fetchone()
    assert dict(envelope) == {"order_type": "FAK"}
    conn.close()
