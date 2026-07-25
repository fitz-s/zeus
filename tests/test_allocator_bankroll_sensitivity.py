# Lifecycle: created=2026-07-25; last_reviewed=2026-07-25; last_reused=never
# Purpose: Unit-test the bankroll-sensitivity probe's pure aggregation/compute
#   logic (compute_sensitivity/aggregate) against synthetic frozen Candidate
#   inputs, plus the decision_log extraction/dedup logic against a tiny
#   in-memory sqlite fixture.
# Reuse: Re-derive expected notional if kelly.py's Kelly boundary formula or
#   CapPolicy field names change.
"""Tests for scripts/allocator_bankroll_sensitivity.py."""

from __future__ import annotations

import base64
import json
import sqlite3
import zlib

import pytest

from scripts.allocator_bankroll_sensitivity import (
    Candidate,
    aggregate,
    compute_sensitivity,
    extract_candidates,
)
from src.risk_allocator.governor import CapPolicy


def _cap_policy(max_per_market_usd: float = 250.0) -> CapPolicy:
    return CapPolicy(max_per_market_micro=int(max_per_market_usd * 1_000_000))


def test_uncapped_ratio_is_exactly_the_multiplier() -> None:
    # p=0.60, price=0.50 -> f* = (0.60-0.50)/(1-0.50) = 0.20
    cand = Candidate(
        cycle_id=1,
        candidate_id="c1",
        condition_id="0xabc",
        side="YES",
        status="SELECTED",
        price=0.50,
        p_posterior=0.60,
        bankroll=1000.0,
        kelly_mult=0.25,
        recorded_full_notional=200.0,
        recorded_fractional_notional=50.0,
    )
    results, warnings = compute_sensitivity([cand], multiplier=3.5, cap_policy=_cap_policy())
    assert warnings == []
    agg = aggregate(results)
    assert agg.ratio_raw == pytest.approx(3.5)
    assert agg.ratio_capped == pytest.approx(3.5)
    # f* * kelly_mult * bankroll = 0.20 * 0.25 * 1000 = 50
    assert round(results[0].notional_1x, 6) == 50.0
    assert round(results[0].notional_mult_x, 6) == 175.0


def test_per_market_cap_binds_and_suppresses_ratio() -> None:
    # Large edge candidate whose 1x notional already exceeds a small cap.
    cand = Candidate(
        cycle_id=1,
        candidate_id="c1",
        condition_id="0xabc",
        side="YES",
        status="SELECTED",
        price=0.10,
        p_posterior=0.90,
        bankroll=1000.0,
        kelly_mult=1.0,
        recorded_full_notional=888.888889,
        recorded_fractional_notional=888.888889,
    )
    small_cap = _cap_policy(max_per_market_usd=100.0)
    results, _ = compute_sensitivity([cand], multiplier=3.5, cap_policy=small_cap)
    agg = aggregate(results)
    # f* = (0.9-0.1)/(1-0.1) = 0.8889 -> notional_1x = 888.89, way above the $100 cap
    assert results[0].depth_capped_1x is True
    assert results[0].depth_capped_mult_x is True
    assert results[0].capped_1x == 100.0
    assert results[0].capped_mult_x == 100.0
    # capped ratio collapses to 1.0 because BOTH sides hit the same fixed cap
    assert agg.ratio_capped == 1.0
    assert agg.ratio_raw == pytest.approx(3.5)  # uncapped math is still exactly linear


def test_aggregate_sums_across_multiple_candidates() -> None:
    cands = [
        Candidate(i, f"c{i}", f"0x{i}", "YES", "SELECTED", 0.5, 0.6, 1000.0, 0.25, 200.0, 50.0)
        for i in range(3)
    ]
    results, _ = compute_sensitivity(cands, multiplier=2.0, cap_policy=_cap_policy())
    agg = aggregate(results)
    assert agg.count == 3
    assert round(agg.sum_raw_1x, 6) == 150.0  # 50 * 3
    assert round(agg.sum_raw_mult_x, 6) == 300.0  # 100 * 3


def test_reconstruction_mismatch_emits_warning() -> None:
    # recorded_fractional_notional deliberately inconsistent with the derived
    # (p_posterior, price, bankroll, kelly_mult) inputs.
    cand = Candidate(
        cycle_id=1,
        candidate_id="c1",
        condition_id="0xabc",
        side="YES",
        status="SELECTED",
        price=0.50,
        p_posterior=0.60,
        bankroll=1000.0,
        kelly_mult=0.25,
        recorded_full_notional=200.0,
        recorded_fractional_notional=999.0,  # inconsistent with the true 50.0
    )
    _, warnings = compute_sensitivity([cand], multiplier=3.5, cap_policy=_cap_policy())
    assert len(warnings) == 1
    assert "diverges" in warnings[0]


def _decision_log_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            artifact_json TEXT NOT NULL
        )
        """
    )
    return conn


def _detailed_candidate(
    *,
    candidate_id: str,
    condition_id: str,
    side: str = "NO",
    status: str = "SELECTED",
    limit_price: float = 0.5,
    full_kelly_target_shares: float = 100.0,
    fractional_kelly_target_shares: float = 6.25,
    cost_usd: float = 50.0,
    wealth_after_loss_usd: float = 950.0,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "condition_id": condition_id,
        "side": side,
        "status": status,
        "limit_price": limit_price,
        "full_kelly_target_shares": full_kelly_target_shares,
        "fractional_kelly_target_shares": fractional_kelly_target_shares,
        "cost_usd": cost_usd,
        "terminal_wealth": {"wealth_after_loss_usd": wealth_after_loss_usd},
    }


def _insert_cycle(
    conn: sqlite3.Connection, *, cycle_id: int, kelly_mult: float, detailed: list[dict]
) -> None:
    payload = {"buy_candidate_index": [], "detailed": detailed}
    raw = json.dumps(payload).encode()
    blob = base64.b64encode(zlib.compress(raw)).decode()
    artifact = {
        "summary": {
            "fractional_kelly_multiplier": str(kelly_mult),
            "candidate_evaluations_zlib_b64": blob,
        }
    }
    conn.execute(
        "INSERT INTO decision_log (id, mode, artifact_json) VALUES (?, 'global_single_order_auction', ?)",
        (cycle_id, json.dumps(artifact)),
    )


def test_extract_candidates_skips_zero_kelly_and_out_of_bound_price() -> None:
    conn = _decision_log_conn()
    _insert_cycle(
        conn,
        cycle_id=1,
        kelly_mult=0.25,
        detailed=[
            _detailed_candidate(candidate_id="a", condition_id="0xa", full_kelly_target_shares=0),
            _detailed_candidate(candidate_id="b", condition_id="0xb", limit_price=0),
            _detailed_candidate(candidate_id="c", condition_id="0xc"),
        ],
    )
    conn.commit()
    candidates, warnings, cycles_scanned = extract_candidates(conn, cycle_limit=10)
    assert cycles_scanned == 1
    assert warnings == []
    assert [c.candidate_id for c in candidates] == ["c"]
    assert candidates[0].price == 0.5
    assert candidates[0].bankroll == 1000.0  # 950 + 50


def test_extract_candidates_dedupes_recurring_candidate_to_latest_cycle() -> None:
    """A resting/re-decided candidate recurs across cycles; keep only the latest."""

    conn = _decision_log_conn()
    for cycle_id, cost in [(1, 50.0), (2, 50.0), (3, 55.0)]:
        _insert_cycle(
            conn,
            cycle_id=cycle_id,
            kelly_mult=0.25,
            detailed=[_detailed_candidate(candidate_id="dup", condition_id="0xdup", cost_usd=cost)],
        )
    conn.commit()
    candidates, _, cycles_scanned = extract_candidates(conn, cycle_limit=10)
    assert cycles_scanned == 3
    assert len(candidates) == 1
    assert candidates[0].cycle_id == 3
    assert candidates[0].bankroll == 950.0 + 55.0


def test_extract_candidates_reports_empty_mode() -> None:
    conn = _decision_log_conn()
    conn.execute(
        "INSERT INTO decision_log (mode, artifact_json) VALUES ('exit_monitor', '{}')"
    )
    conn.commit()
    candidates, warnings, cycles_scanned = extract_candidates(conn, cycle_limit=10)
    assert candidates == []
    assert cycles_scanned == 0
    assert any("no 'global_single_order_auction'" in w for w in warnings)
