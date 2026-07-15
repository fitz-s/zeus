# Created: 2026-07-03
# Last reused/audited: 2026-07-15
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
from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

import src.engine.qkernel_spine_bridge as bridge
import src.engine.event_reactor_adapter as era
import src.engine.global_batch_runtime as global_batch_runtime
import src.engine.global_auction_universe as universe
from src.decision_kernel import claims
from src.decision_kernel.canonicalization import qkernel_current_state_identity_hash
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
)
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    make_day0_extreme_updated_event,
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
from src.contracts.semantic_types import Direction
from src.strategy import utility_ranker
from src.state.collateral_ledger import init_collateral_schema
from src.state.portfolio import PortfolioState
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
            rejection_reason="ENTRY_ACTION_PAUSED:external:operator",
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
    assert summary["schema_version"] == 10
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
    assert summary["probability_ineligible_by_family"] == {
        "family-q-missing": (
            "GLOBAL_CURRENT_PROBABILITY_PREPARE_FAILED:ValueError:"
            "GLOBAL_CURRENT_REPLACEMENT_BUNDLE_BLOCKED"
        )
    }
    assert summary["candidate_coverage_complete"] is True
    assert summary["candidate_condition_index_complete"] is True
    assert summary["candidate_evaluation_count"] == 2
    assert summary["candidate_input_count"] == 2
    assert summary["candidate_detailed_count"] == 1
    assert summary["candidate_rejection_group_count"] == 1
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
        "zlib+base64+canonical-json-v6"
    )
    candidate_evaluations = json.loads(evaluation_json)
    assert candidate_evaluations["rejected_groups"] == [
        {
            "action": "BUY",
            "side": "YES",
            "reason": "ENTRY_ACTION_PAUSED:external:operator",
            "candidate_ids": ["buy-paused"],
        }
    ]
    assert candidate_evaluations["buy_condition_side_masks"] == [
        ["condition-buy", 1]
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
            wealth_witness=SimpleNamespace(
                witness_identity="wealth-current",
                economic_identity="wealth-economics-current",
            ),
            fractional_kelly_multiplier=Decimal("0.25"),
        )
    conn.close()


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
        excluded_by_family={"family-a": "GLOBAL_JIT_SNAPSHOT_REFRESH_FAILED"},
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
        ledger_snapshot_id="ledger-selection-cut",
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


def _global_scope_event(*, city: str, source_run_id: str):
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
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|2026-07-11|high",
        source="global-auction-current-scope",
        observed_at=captured_at,
        available_at=captured_at,
        received_at=captured_at,
        payload=payload,
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
    monkeypatch.setattr(
        hook_factory,
        "_latest_replacement_readiness",
        lambda *args, **kwargs: object(),
    )
    bundle_read: dict[str, object] = {}

    def read_bundle(*args, **kwargs):
        bundle_read.update(kwargs)
        return SimpleNamespace(
            ok=True,
            bundle=bundle,
            reason_code="READY",
        )

    monkeypatch.setattr(bundle_reader, "read_replacement_forecast_bundle", read_bundle)

    traced: list[str] = []
    forecast.set_trace_callback(traced.append)
    prepared = era._prepare_current_global_probability_family(
        _global_scope_event(city="Dallas", source_run_id="run-dallas"),
        forecast_conn=forecast,
        topology_conn=forecast,
        decision_time=_dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
        max_age=_dt.timedelta(seconds=30),
    )
    forecast.set_trace_callback(None)

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


def test_live_adapter_routes_each_global_truth_to_its_owner(monkeypatch):
    import src.data.polymarket_client as polymarket_client
    import src.engine.global_auction_universe as universe

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
    prepared_with = {}

    def fake_prepare(_event, **kwargs):
        prepared_with.update(kwargs)
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
            probability_witness=SimpleNamespace(family_key="family-dallas"),
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
    adapter = era.event_bound_live_adapter_from_trade_conn(
        trade,
        get_current_level=lambda: era.RiskLevel.GREEN,
        forecast_conn=forecast,
        topology_conn=topology,
        calibration_conn=world,
        portfolio_state_provider=lambda: pytest.fail(
            "cycle-start portfolio must not back global selection wealth"
        ),
    )
    event = _global_scope_event(city="Dallas", source_run_id="run-dallas")

    result = adapter.process_global_batch(
        (event,),
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )

    assert result.events == (event,)
    assert captured["world_conn"] is world
    assert captured["forecast_conn"] is forecast
    assert captured["world_conn"] is not topology
    assert captured["portfolio_state_provider"] is None
    assert captured["candidate_policy_rejection_resolver"] is None
    prepared_receipt = captured["prepare_event"](
        event,
        _dt.datetime(2026, 7, 10, 8, 10, tzinfo=_dt.timezone.utc),
    )
    assert prepared_receipt.prepared_global_family is not None
    assert prepared_with["forecast_conn"] is forecast
    assert prepared_with["topology_conn"] is topology
    assert prepared_with["observation_conn"] is world
    metadata_calls = []
    bind_calls = []
    metadata_key = ("condition", "yes-token")
    metadata = {"condition_id": "condition", "active": True}

    def fake_bind(_forecast_conn, *, probability_witnesses, metadata_sink, **_):
        bind_calls.append(1)
        if len(bind_calls) == 1:
            metadata_sink[metadata_key] = metadata
        return probability_witnesses

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
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakeClient)
    provider = captured["current_book_epoch_provider"]
    probabilities = {"family": object()}
    provider(probabilities, _dt.datetime.now(_dt.timezone.utc))
    provider(probabilities, _dt.datetime.now(_dt.timezone.utc))

    assert metadata_calls == [
        {metadata_key: metadata},
        {metadata_key: metadata},
    ]


def test_global_curve_supersession_keeps_typed_current_candidate():
    candidate = object()
    reason = (
        "GLOBAL_ACTUATION_EXECUTION_BINDING_SUPERSEDED:curve_economics:"
        "detail=prefix_price"
    )
    exc = era._GlobalCurveSuperseded(reason, candidate)
    receipt = era.EventSubmissionReceipt(
        False,
        "event-1",
        "snapshot-1",
        reason=str(exc),
        global_jit_candidate=exc.replacement_candidate,
    )

    assert era._global_curve_supersession_from_receipt(receipt) == (
        "CURVE_SUPERSEDED",
        candidate,
        reason,
    )
    missing = replace(receipt, global_jit_candidate=None)
    assert era._global_curve_supersession_from_receipt(missing) == (
        "BLOCKED",
        None,
        f"{reason}:replacement_candidate_missing",
    )


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
    curve = SimpleNamespace(book_hash="book-current")
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
        book_snapshot_id="snapshot-current",
        execution_curve_identity="curve-current",
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
        "_full_depth_native_side_candidate_from_proof",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        "src.solve.solver.global_candidate_from_native",
        lambda *_args, **_kwargs: candidate,
    )
    monkeypatch.setattr(
        era,
        "_global_selected_order_economics_drift",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        era,
        "current_global_probability_authority",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        era,
        "current_global_execution_authority",
        lambda *_args, **_kwargs: SimpleNamespace(
            book_snapshot_id=candidate.book_snapshot_id,
            execution_curve_identity=candidate.execution_curve_identity,
        ),
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
        trade_conn=object(),
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
            trade_conn=object(),
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
            trade_conn=object(),
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
        ("GLOBAL_JIT_SNAPSHOT_REFRESH_FAILED", "BATCH_BLOCKED"),
        ("GLOBAL_JIT_SNAPSHOT_REFRESH_UNAVAILABLE", "BATCH_BLOCKED"),
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
            "EDLI_LIVE_CERTIFICATE_BUILD_FAILED:"
            "LIVE_ENTRY_DAY0_PROBABILITY_AUTHORITY_REQUIRED:"
            "selected q_lcb does not match remaining-day transform:"
            "condition_id=condition-a:q_lcb=0.72:transform_lcb=0.965560157285",
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
    day0_alpha = make_day0_extreme_updated_event(
        entity_key="Alpha|2026-07-11|high|ALPHA-WU",
        source="day0_observation",
        observed_at=day0_payload.observation_time,
        received_at="2026-07-10T08:10:01+00:00",
        payload=day0_payload,
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
    events = _current_day0_events(conn, decision_at_utc=decision_at)

    events_by_city = {
        json.loads(event.payload_json)["city"]: event for event in events
    }
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
    class EmptyTrigger:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_committed_snapshot_events(self, **_kwargs):
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
                "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?)",
                (binding.condition_id, token, snapshot_id),
            )
    return conn


def test_global_book_curve_uses_same_realized_fee_authority_as_jit(monkeypatch):
    observed = []

    def realized_fee(schedule):
        observed.append(schedule)
        return 0.0, "realized_test"

    monkeypatch.setattr(universe, "resolve_taker_fee_fraction", realized_fee)
    curve = universe._global_book_curve(
        family_key="City|2026-07-11|high",
        bin_id="bin-1",
        condition_id="condition-1",
        side="NO",
        token_id="no-1",
        raw_book={
            "hash": "book-1",
            "tick_size": "0.01",
            "min_order_size": "5",
            "asks": [{"price": "0.30", "size": "100"}],
        },
        metadata={"fee_details_json": '{"fee_rate_fraction":0.05}'},
        captured_at_utc=_dt.datetime(
            2026, 7, 11, 3, 0, tzinfo=_dt.timezone.utc
        ),
        max_age=_dt.timedelta(seconds=30),
    )

    assert observed == pytest.approx([0.05])
    assert curve is not None
    assert curve.fee_model.fee_rate == Decimal("0.0")


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
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?)",
        (missing_condition, "conflicting-selected", "conflicting-topology"),
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
        ledger_snapshot_id="ledger-current",
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

    assert witness.spendable_cash_usd == Decimal("20")
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

    assert witness.spendable_cash_usd == Decimal("0")
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

    assert witness.spendable_cash_usd == Decimal("20")
    assert witness.wealth_floor_usd == Decimal("27")
    assert witness.wealth_ceiling_usd == Decimal("28")


def test_current_portfolio_wealth_witness_refuses_inflight_or_unknown_inventory():
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

    unknown = _wealth_test_conn(captured_at=decision_at, ctf={"unknown-token": 1_000_000})
    with pytest.raises(ValueError, match="CURRENT_WEALTH_CHAIN_POSITION_SET_MISMATCH"):
        current_portfolio_wealth_witness(
            unknown,
            decision_at_utc=decision_at,
            max_age=_dt.timedelta(seconds=30),
            portfolio_state=portfolio,
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
    )

    assert len(claimed_targets) == 1
    assert claimed_targets[0].source.endswith(f":{fence_economic_identity}")
    assert fenced.winner_event_id == claimed_targets[0].event_id
    assert fenced.venue_submit_count == 1
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


def _global_test_book(identity: str, *, price: str):
    return SimpleNamespace(
        witness_identity=identity,
        captured_at_utc=_dt.datetime(
            2026, 7, 10, 8, 0, tzinfo=_dt.timezone.utc
        ),
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


def test_global_batch_reauctions_once_on_full_universe_curve_drift(monkeypatch):
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
    actuation_b_fence = SimpleNamespace(
        actuation_identity="actuation-b-fence", wealth_witness_identity="wealth-1"
    )
    actuation_b_final = SimpleNamespace(
        actuation_identity="actuation-b-final", wealth_witness_identity="wealth-1"
    )
    selections = iter(
        SimpleNamespace(
            decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
            winner_event_id=event.event_id,
            actuation=actuation,
        )
        for event, actuation in (
            (event_b, actuation_b_fence),
            (event_b, actuation_b_final),
        )
    )
    books = iter(
        (
            _global_test_book("book-1", price="0.41"),
            _global_test_book("book-2", price="0.42"),
        )
    )
    replacement_candidate = object()
    calls = {"prepare": 0, "books": 0, "wealth": 0, "preflight": [], "venue": 0}

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
        assert authority.book_epoch_identity == "book-2"
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
        "books": 2,
        "wealth": 1,
        "preflight": [event_b.event_id, event_b.event_id],
        "venue": 1,
    }
    assert result.winner_event_id == event_b.event_id
    assert result.venue_submit_count == 1
    assert result.receipts[event_b.event_id].submitted is True


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


def test_global_batch_candidate_block_keeps_sibling_and_reproves_after_book_refresh(
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
            (candidate_a, "actuation-a-refresh"),
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
    refreshed_book = SimpleNamespace(
        **(vars(book) | {"witness_identity": "book-candidate-refresh"})
    )
    books = iter((book, refreshed_book))
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
            if calls["preflight"].count("candidate-a") > 1:
                return global_batch_runtime.GlobalWinnerPreflight(
                    status="STABLE", binding_token="binding-a-refresh"
                )
            return global_batch_runtime.GlobalWinnerPreflight(
                status="CANDIDATE_BLOCKED",
                reason=reason,
            )
        return global_batch_runtime.GlobalWinnerPreflight(
            status="CURVE_SUPERSEDED",
            replacement_candidate=candidate_b,
            reason="curve refreshed",
        )

    def actuate(_event, actuation, _at, token, _authority):
        assert actuation.decision.candidate is candidate_a
        assert token == "binding-a-refresh"
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
        return probabilities, next(books)

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
        "select": 3,
        "wealth": 1,
        "preflight": ["candidate-a", "candidate-b", "candidate-a"],
        "books": 2,
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
        "GLOBAL_JIT_SNAPSHOT_REFRESH_FAILED",
        "GLOBAL_JIT_SNAPSHOT_REFRESH_UNAVAILABLE",
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
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
        winner_event_id=event.event_id,
        actuation=SimpleNamespace(
            actuation_identity="actuation-a",
            wealth_witness_identity="wealth",
        ),
    )
    calls = {"preflight": 0, "select": 0, "venue": 0}
    books = iter(
        (
            _global_test_book("book", price="0.40"),
            _global_test_book("book-1", price="0.41"),
            _global_test_book("book-2", price="0.42"),
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
    def select(*_, **__):
        calls["select"] += 1
        return selected

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
            replacement_candidate=object(),
            reason=f"curve moved {calls['preflight']}",
        )

    def actuate_preflighted(_event, _actuation, _at, token, authority):
        assert token == "binding-a"
        assert authority.book_epoch_identity == "book-2"
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
            probabilities,
            next(books),
        ),
    )

    assert calls == {"preflight": 3, "select": 3, "venue": 1}
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
    selected = SimpleNamespace(
        decision=SimpleNamespace(candidate=object(), no_trade_reason=None),
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
            replacement_candidate=object(),
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
            _global_test_book("book", price="0.40"),
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


def _adapter_sell_actuation(event):
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
    proceeds = Decimal("5.4")
    loss_at_risk = Decimal("4.6")
    robust_q = 0.70
    robust_du = (1.0 - robust_q) * np.log(105.4 / 110.0) + robust_q * np.log(
        105.4 / 100.0
    )
    robust_ev = robust_q * 10.0 - float(loss_at_risk)
    decision = GlobalSingleOrderDecision(
        candidate=candidate,
        shares=Decimal("10"),
        cost_usd=loss_at_risk,
        robust_delta_log_wealth=float(robust_du),
        robust_ev_usd=robust_ev,
        capital_efficiency=float(robust_du) / float(loss_at_risk),
        no_trade_reason=None,
        limit_price=Decimal("0.50"),
        expected_fill_price_before_fee=Decimal("0.54"),
        cash_proceeds_usd=proceeds,
        terminal_wealth=BinaryTerminalWealthCertificate(
            win_probability_lcb=robust_q,
            loss_probability_ucb=1.0 - robust_q,
            loss_payoff_usd=-loss_at_risk,
            win_payoff_usd=proceeds,
            median_payoff_usd=proceeds,
            wealth_after_loss_usd=Decimal("105.4"),
            wealth_after_win_usd=Decimal("105.4"),
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
    actuation = _adapter_sell_actuation(event)
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
        assert kwargs["exit_intent"].shares == pytest.approx(10.0)
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
