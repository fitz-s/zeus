# Created: 2026-06-12
# Last reused or audited: 2026-07-14
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
import math
from types import SimpleNamespace

import pytest


@dataclasses.dataclass
class _LegacyIntent:
    executable_snapshot_id: str = "snap-stale"
    executable_snapshot_hash: str = "hash-stale"
    executable_snapshot_min_tick_size: float = 0.01
    executable_snapshot_min_order_size: float = 5.0
    executable_snapshot_neg_risk: bool = False
    limit_price: float = 0.14


def _final_intent(
    *,
    post_only: bool,
    limit: float = 0.14,
    order_type: str | None = None,
    qkernel_execution_economics=None,
):
    return SimpleNamespace(
        direction="buy_no",
        post_only=post_only,
        order_policy="post_only_passive_limit" if post_only else "marketable_limit_depth_bound",
        order_type=order_type or ("POST_ONLY_LIMIT" if post_only else "FOK"),
        final_limit_price=Decimal(str(limit)),
        selected_token_id="tok-no",
        tick_size=0.01,
        min_order_size=5.0,
        neg_risk=False,
        expected_fill_price_before_fee=Decimal(str(limit)),
        fee_rate=Decimal(
            str(
                (qkernel_execution_economics or {}).get(
                    "global_buy_fak_fee_rate", "0"
                )
            )
        ),
        qkernel_execution_economics=qkernel_execution_economics,
    )


def _buy_fak_economics(*, shares=Decimal("10"), limit=Decimal("0.14")):
    fee_rate = Decimal("0.05")
    max_fee_shape = (
        Decimal("0.25")
        if limit >= Decimal("0.5")
        else limit * (Decimal("1") - limit)
    )
    worst_fee_per_share = Decimal("2") * fee_rate * max_fee_shape
    unit_cost = limit + worst_fee_per_share
    full_cost = unit_cost * shares
    win_q = Decimal("0.60")
    loss_q = Decimal("0.40")
    floor = Decimal("100")
    ceiling = Decimal("100")
    robust_du = float(loss_q) * math.log(float((floor - full_cost) / floor))
    robust_du += float(win_q) * math.log(
        float((ceiling - full_cost + shares) / ceiling)
    )
    curve = "curve-current"
    return {
        "side": "NO",
        "global_jit_execution_curve_identity": curve,
        "global_target_shares": str(shares),
        "global_limit_price": str(limit),
        "global_terminal_win_probability_lcb": str(win_q),
        "global_terminal_loss_probability_ucb": str(loss_q),
        "global_terminal_loss_payoff_usd": "-1.4",
        "global_terminal_win_payoff_usd": "8.6",
        "global_terminal_wealth_after_loss_usd": "98.6",
        "global_terminal_wealth_after_win_usd": "108.6",
        "global_buy_fak_prefix_semantics": (
            "CONCAVE_WORST_LIMIT_ALL_NONZERO_PREFIXES_POSITIVE"
        ),
        "global_buy_fak_fee_rate_source": "CURRENT_EXECUTABLE_CURVE",
        "global_buy_fak_execution_curve_identity": curve,
        "global_buy_fak_fee_rate": str(fee_rate),
        "global_buy_fak_fee_rounding_bound": (
            "ROUNDED_FEE_AT_MOST_TWO_X_UNROUNDED"
        ),
        "global_buy_fak_worst_fee_shape": str(max_fee_shape),
        "global_buy_fak_worst_fee_per_share": str(worst_fee_per_share),
        "global_buy_fak_worst_unit_cost": str(unit_cost),
        "global_buy_fak_full_worst_cost_usd": str(full_cost),
        "global_buy_fak_full_robust_delta_log_wealth": robust_du,
        "global_buy_fak_full_robust_ev_usd": float(win_q * shares - full_cost),
    }


def _fresh_snapshot(*, top_ask):
    return SimpleNamespace(
        snapshot_id="snap-fresh",
        executable_snapshot_hash="hash-fresh",
        selected_outcome_token_id="tok-no",
        min_tick_size=0.01,
        min_order_size=5.0,
        fee_details={"fee_rate_fraction": "0.05"},
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


def _fresh_snapshot_with_tick(*, top_ask, min_tick_size):
    snap = _fresh_snapshot(top_ask=top_ask)
    snap.min_tick_size = min_tick_size
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

    state = {"fresh": _fresh_snapshot(top_ask=Decimal("0.20")), "capture_calls": 0}

    def fake_get_snapshot(conn, snapshot_id):
        return _stale_snapshot() if snapshot_id == "snap-stale" else state["fresh"]

    monkeypatch.setattr(repo, "get_snapshot", fake_get_snapshot)
    monkeypatch.setattr(
        snap_contract, "is_fresh", lambda snap, now: snap.snapshot_id == "snap-fresh"
    )
    def fake_capture(*_args, **_kwargs):
        state["capture_calls"] += 1
        return {"executable_snapshot_id": "snap-fresh"}

    monkeypatch.setattr(scanner, "capture_executable_market_snapshot", fake_capture)

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


def test_same_selected_token_tick_drift_updates_envelope(_patched_recapture):
    """Venue tick metadata can drift between elected snapshot and submit.
    For the same selected token, recapture should carry the fresh tick forward
    and keep the order if its fresh-tick limit remains non-crossing."""

    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot_with_tick(
        top_ask=Decimal("0.20"),
        min_tick_size=Decimal("0.01"),
    )

    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(executable_snapshot_min_tick_size=Decimal("0.001")),
        _final_intent(post_only=True, limit=0.141),
        conn=object(),
        submitted_shares=5.0,
    )

    assert out.executable_snapshot_id == "snap-fresh"
    assert out.executable_snapshot_min_tick_size == Decimal("0.01")
    assert out.limit_price == 0.14


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


def test_certified_fak_accepts_positive_partial_depth_at_submit_recapture(
    _patched_recapture, monkeypatch
):
    import src.execution.executor as executor_mod
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    monkeypatch.setattr(
        executor_mod,
        "simulate_clob_sweep",
        lambda **_k: SimpleNamespace(
            depth_status="DEPTH_INSUFFICIENT",
            average_price=Decimal("0.14"),
            filled_shares=Decimal("3.25"),
        ),
    )

    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(),
        _final_intent(
            post_only=False,
            limit=0.14,
            order_type="FAK",
            qkernel_execution_economics=_buy_fak_economics(),
        ),
        conn=object(),
        submitted_shares=10.0,
    )

    assert out.executable_snapshot_id == "snap-fresh"


def test_certified_fak_rejects_fresh_tick_above_certified_limit(
    _patched_recapture, monkeypatch
):
    import src.execution.executor as executor_mod
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"] = _fresh_snapshot_with_tick(
        top_ask=Decimal("0.02"),
        min_tick_size=Decimal("0.01"),
    )

    def unexpected_sweep(**_kwargs):
        pytest.fail("fresh tick must fail before depth simulation")

    monkeypatch.setattr(executor_mod, "simulate_clob_sweep", unexpected_sweep)

    with pytest.raises(
        ValueError,
        match="fresh tick cannot express prefix-certified limit",
    ):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(limit_price=0.004),
            _final_intent(
                post_only=False,
                limit=0.004,
                order_type="FAK",
                qkernel_execution_economics=_buy_fak_economics(
                    limit=Decimal("0.004")
                ),
            ),
            conn=object(),
            submitted_shares=10.0,
        )


def test_certified_fak_rejects_fresh_fee_above_certificate(
    _patched_recapture, monkeypatch
):
    import src.execution.executor as executor_mod
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    _patched_recapture["fresh"].fee_details = {"fee_rate_fraction": "0.10"}
    monkeypatch.setattr(
        executor_mod,
        "simulate_clob_sweep",
        lambda **_k: SimpleNamespace(
            depth_status="DEPTH_INSUFFICIENT",
            average_price=Decimal("0.14"),
            filled_shares=Decimal("3.25"),
        ),
    )

    with pytest.raises(ValueError, match="fee exceeds prefix certificate"):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(),
            _final_intent(
                post_only=False,
                limit=0.14,
                order_type="FAK",
                qkernel_execution_economics=_buy_fak_economics(),
            ),
            conn=object(),
            submitted_shares=10.0,
        )


def test_uncertified_fak_keeps_full_depth_requirement(_patched_recapture, monkeypatch):
    import src.execution.executor as executor_mod
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    monkeypatch.setattr(
        executor_mod,
        "simulate_clob_sweep",
        lambda **_k: SimpleNamespace(
            depth_status="DEPTH_INSUFFICIENT",
            average_price=Decimal("0.14"),
            filled_shares=Decimal("3.25"),
        ),
    )

    with pytest.raises(ValueError, match="DEPTH_INSUFFICIENT"):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(),
            _final_intent(post_only=False, limit=0.14, order_type="FAK"),
            conn=object(),
            submitted_shares=10.0,
        )


def test_taker_recaptures_even_when_elected_snapshot_is_fresh(_patched_recapture, monkeypatch):
    """FOK/FAK entries need submit-time depth, not merely a not-yet-expired DB snapshot."""
    import src.contracts.executable_market_snapshot as snap_contract
    import src.execution.executor as executor_mod
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    monkeypatch.setattr(snap_contract, "is_fresh", lambda _snap, _now: True)
    monkeypatch.setattr(
        executor_mod,
        "simulate_clob_sweep",
        lambda **_k: SimpleNamespace(depth_status="PASS", average_price=Decimal("0.14")),
    )

    out = _recapture_fresh_entry_snapshot_if_needed(
        _LegacyIntent(),
        _final_intent(post_only=False, limit=0.14),
        conn=object(),
        submitted_shares=10.0,
    )

    assert _patched_recapture["capture_calls"] == 1
    assert out.executable_snapshot_id == "snap-fresh"


def test_taker_fails_closed_when_fresh_recapture_unavailable(_patched_recapture, monkeypatch):
    """A taker FOK without a fresh depth witness must stop before venue submit."""
    import src.contracts.executable_market_snapshot as snap_contract
    import src.data.market_scanner as scanner
    from src.execution.executor import _recapture_fresh_entry_snapshot_if_needed

    monkeypatch.setattr(snap_contract, "is_fresh", lambda _snap, _now: True)
    monkeypatch.setattr(scanner, "capture_executable_market_snapshot", lambda *a, **k: {})

    with pytest.raises(ValueError, match="TAKER_FRESH_DEPTH_RECAPTURE_UNAVAILABLE"):
        _recapture_fresh_entry_snapshot_if_needed(
            _LegacyIntent(),
            _final_intent(post_only=False, limit=0.14),
            conn=object(),
            submitted_shares=10.0,
        )
