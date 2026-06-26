# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: live incident 2026-06-12T02:16:49Z (Helsinki POST_ONLY
#   219.77@0.14 PRE_SUBMIT_ERROR 'depth_status=DEPTH_INSUFFICIENT') + the
#   taker-shaped-check-strangles-maker family (WALL #1 passive_maker_context,
#   maker market identity f6b961731f). docs/operations/
#   k1_final_snapshot_authority_plan_2026-06-11.md owns the surface.
"""ANTIBODY: the executor's stale-snapshot recapture validates MODE-CORRECT
economics. A post_only maker rest ADDS liquidity — it has no crossable depth at
its own limit by construction, so the taker crossable-depth sweep must never be
applied to it (it killed every resting maker whose elected snapshot went stale
before the executor ran). Maker economics depend only on the rest still being
non-crossing on the fresh book.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest


@dataclasses.dataclass
class _LegacyIntent:
    executable_snapshot_id: str = "snap-stale"
    executable_snapshot_hash: str = "hash-stale"
    executable_snapshot_min_tick_size: float = 0.01
    executable_snapshot_min_order_size: float = 5.0
    executable_snapshot_neg_risk: bool = False


def _final_intent(*, post_only: bool, limit: float = 0.14):
    return SimpleNamespace(
        direction="buy_no",
        post_only=post_only,
        order_type="POST_ONLY_LIMIT" if post_only else "FOK",
        final_limit_price=Decimal(str(limit)),
        selected_token_id="tok-no",
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        expected_fill_price_before_fee=Decimal(str(limit)),
    )


def _fresh_snapshot(*, top_ask):
    return SimpleNamespace(
        snapshot_id="snap-fresh",
        executable_snapshot_hash="hash-fresh",
        selected_outcome_token_id="tok-no",
        min_tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        orderbook_top_ask=top_ask,
        yes_token_id="tok-yes",
        no_token_id="tok-no",
        condition_id="cond-1",
    )


def _fresh_snapshot_with_min_order_size(*, top_ask, min_order_size):
    snap = _fresh_snapshot(top_ask=top_ask)
    snap.min_order_size = min_order_size
    return snap


def _fresh_snapshot_with_neg_risk(*, top_ask, neg_risk):
    snap = _fresh_snapshot(top_ask=top_ask)
    snap.neg_risk = neg_risk
    return snap


def _stale_snapshot():
    return SimpleNamespace(
        snapshot_id="snap-stale",
        yes_token_id="tok-yes",
        no_token_id="tok-no",
        condition_id="cond-1",
    )


@pytest.fixture
def _patched_recapture(monkeypatch):
    """Patch the recapture I/O seams so the validation logic runs hermetically."""
    import src.contracts.executable_market_snapshot as snap_contract
    import src.data.market_scanner as scanner
    import src.data.polymarket_client as pmc
    import src.engine.cycle_runtime as cycle_runtime
    import src.state.snapshot_repo as repo

    state = {"fresh": _fresh_snapshot(top_ask=Decimal("0.20"))}

    def fake_get_snapshot(conn, snapshot_id):
        return _stale_snapshot() if snapshot_id == "snap-stale" else state["fresh"]

    monkeypatch.setattr(repo, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(
        snap_contract, "is_fresh", lambda snap, now: snap.snapshot_id == "snap-fresh"
    )
    monkeypatch.setattr(
        scanner,
        "capture_executable_market_snapshot",
        lambda *a, **k: {"executable_snapshot_id": "snap-fresh"},
    )

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pmc, "PolymarketClient", _FakeClient)
    monkeypatch.setattr(cycle_runtime, "_market_dict_from_snapshot", lambda s: {})
    return state


def test_maker_rest_noncrossing_passes_without_depth_sweep(_patched_recapture):
    """Fresh ask 0.20 > limit 0.14: the rest is still non-crossing — the
    recapture must PASS (the old crossable-depth sweep returned
    DEPTH_INSUFFICIENT here and killed the maker)."""
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(),
        _final_intent(post_only=True, limit=0.14),
        conn=object(),
        submitted_shares=219.77,
    )
    assert out.executable_snapshot_id == "snap-fresh"


def test_maker_rest_crossing_fresh_ask_raises(_patched_recapture):
    """Fresh ask moved THROUGH the limit (ask 0.12 <= limit 0.14): the
    post_only premise is gone — the abort is correct and keeps its
    economics-changed reason."""
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot(top_ask=Decimal("0.12"))
    with pytest.raises(ValueError, match="would cross fresh ask"):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(),
            _final_intent(post_only=True, limit=0.14),
            conn=object(),
            submitted_shares=219.77,
        )


def test_maker_rest_empty_fresh_ask_is_bid_establishing(_patched_recapture):
    """No fresh ask at all: a bid-establishing rest stands."""
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot(top_ask=None)
    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(),
        _final_intent(post_only=True, limit=0.14),
        conn=object(),
        submitted_shares=219.77,
    )
    assert out.executable_snapshot_id == "snap-fresh"


def test_same_selected_token_min_order_size_drift_updates_envelope(_patched_recapture):
    """Venue min-order metadata can drift between elected snapshot and submit.
    For the same selected token, recapture should only require the submitted
    shares to satisfy the fresh venue minimum, then carry fresh metadata forward."""

    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot_with_min_order_size(
        top_ask=Decimal("0.20"),
        min_order_size=Decimal("1"),
    )

    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(),
        _final_intent(post_only=True, limit=0.14),
        conn=object(),
        submitted_shares=5.0,
    )

    assert out.executable_snapshot_id == "snap-fresh"
    assert out.executable_snapshot_min_order_size == Decimal("1")


def test_same_selected_token_neg_risk_drift_updates_envelope(_patched_recapture):
    """Fresh recapture may correct stale/missing negRisk metadata for the same token."""

    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot_with_neg_risk(
        top_ask=Decimal("0.20"),
        neg_risk=True,
    )

    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(executable_snapshot_neg_risk=False),
        _final_intent(post_only=True, limit=0.14),
        conn=object(),
        submitted_shares=5.0,
    )

    assert out.executable_snapshot_id == "snap-fresh"
    assert out.executable_snapshot_neg_risk is True


def test_recapture_rejects_when_submitted_shares_below_fresh_min_order_size(_patched_recapture):
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot_with_min_order_size(
        top_ask=Decimal("0.20"),
        min_order_size=Decimal("10"),
    )

    with pytest.raises(ValueError, match="below fresh min_order_size"):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(),
            _final_intent(post_only=True, limit=0.14),
            conn=object(),
            submitted_shares=5.0,
        )


def test_taker_still_validates_with_depth_sweep(_patched_recapture, monkeypatch):
    """The taker lane keeps the crossable-depth sweep — DEPTH_INSUFFICIENT on a
    taker is a REAL economics change and must still raise."""
    import src.execution.executor as executor_mod
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    monkeypatch.setattr(
        executor_mod,
        "simulate_clob_sweep",
        lambda **k: SimpleNamespace(depth_status="DEPTH_INSUFFICIENT", average_price=None),
    )
    with pytest.raises(ValueError, match="depth_status=DEPTH_INSUFFICIENT"):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(),
            _final_intent(post_only=False, limit=0.14),
            conn=object(),
            submitted_shares=10.0,
        )
