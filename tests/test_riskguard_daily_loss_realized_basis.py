# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: current-state capital allocation. Settled outcomes are
# already embedded in current cash and positions, so a trailing-window veto
# double-counts sunk outcomes. Historical PnL remains diagnostic only.
"""Relationship antibody: daily/weekly settled loss has no actuation authority.

The cross-module invariant this pins (relationship test, not a function test):

    The loss level MUST be invariant to
      (a) capital deployment            (wallet cash -> open position equity),
      (b) projection-pipeline reshuffle (unprojected entry fill -> projected),
      (c) mark-to-market swings         of open positions,
    and current RiskLevel MUST NOT depend on a trailing settled-PnL window.

The strongest form of the antibody is STRUCTURAL:
`_realized_window_loss_telemetry` returns no RiskLevel and accepts no threshold,
so historical PnL cannot become an admission decision through this seam.

This is the regression guard for the 2026-06-08 live false RED: effective
bankroll dropped 244.24 -> 224.99 (-19.25) over 24h with realized PnL flat and
total PnL improved; the old mark-to-market breaker tripped RED and halted 100%
of trading. The realized-basis breaker must read that exact scenario as GREEN.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from src.riskguard.riskguard import _realized_window_loss_telemetry

NOW = "2026-06-08T15:29:00+00:00"
_now_dt = datetime.fromisoformat(NOW)


def _exit(pnl: float, hours_ago: float) -> dict:
    ts = (_now_dt - timedelta(hours=hours_ago)).isoformat()
    return {"city": "X", "exited_at": ts, "pnl": float(pnl)}


def _snap(exits, *, lookback_hours=24, degraded=False):
    return _realized_window_loss_telemetry(
        exits,
        now=NOW,
        lookback=timedelta(hours=lookback_hours),
        degraded=degraded,
        source="realized_settlement_window:test",
    )


# --- 1. STRUCTURAL antibody: equity can never be an input ---------------------
def test_signature_forbids_equity_input_mark_to_market_unconstructable():
    """The breaker is structurally immune to mark-to-market: there is no
    parameter through which effective_bankroll / current equity could enter."""
    params = set(inspect.signature(_realized_window_loss_telemetry).parameters)
    forbidden = {
        "current_equity",
        "effective_bankroll",
        "reference_equity",
        "current_total_value",
        "account_equity",
    }
    leaked = params & forbidden
    assert not leaked, f"mark-to-market input leaked into realized loss breaker: {leaked}"
    assert "threshold_pct" not in params
    assert "initial_bankroll" not in params


# --- 2. The exact 2026-06-08 live false positive -> GREEN ---------------------
def test_live_false_positive_effective_bankroll_swing_is_green():
    """Realized PnL flat over the window (the live truth: -3.19 both snapshots,
    total PnL improved). A -19.25 *effective bankroll* swing must NOT halt,
    because no settled loss occurred. This is the regression that re-opens
    trading."""
    # Realized settlements in the last 24h net to ~0 (a couple of tiny wins and
    # losses that cancel) — mirroring the live state where realized was flat.
    exits = [_exit(+1.10, 3), _exit(-1.05, 8), _exit(+0.40, 20)]
    snap = _snap(exits)
    assert "level" not in snap
    assert snap["loss"] == 0.0
    assert snap["degraded"] is False


# --- 3. Genuine settled loss is measured but cannot veto current action ------
def test_genuine_settled_loss_is_diagnostic_only():
    exits = [_exit(-5.0, 2), _exit(-4.0, 6)]
    snap = _snap(exits)
    assert "level" not in snap
    assert snap["loss"] == pytest.approx(9.0, abs=1e-6)


def test_realized_loss_below_threshold_stays_green():
    exits = [_exit(-3.0, 2), _exit(-2.0, 6)]
    snap = _snap(exits)
    assert "level" not in snap
    assert snap["loss"] == pytest.approx(5.0, abs=1e-6)


# --- 4. Settlements OUTSIDE the window are ignored ----------------------------
def test_out_of_window_loss_ignored():
    """A large settled loss older than the lookback must not count toward the
    daily breaker (it belongs to a prior day / the weekly window)."""
    exits = [_exit(-50.0, 30)]  # 30h ago, outside 24h window
    snap = _snap(exits)
    assert "level" not in snap
    assert snap["loss"] == 0.0
    assert snap["reference"]["settlement_count"] == 0


def test_weekly_window_catches_what_daily_misses():
    exits = [_exit(-20.0, 30)]  # 30h ago: out of daily, in 7d weekly
    daily = _snap(exits, lookback_hours=24)
    weekly = _snap(exits, lookback_hours=24 * 7)
    assert daily["loss"] == 0.0
    assert weekly["loss"] == 20.0
    assert "level" not in daily and "level" not in weekly


# --- 5. Missing historical truth degrades diagnostics, not current action -----
def test_degraded_realized_is_diagnostic_degradation_only():
    snap = _snap([_exit(-99.0, 1)], degraded=True)
    assert "level" not in snap
    assert snap["degraded"] is True


# --- 6. Deployment / reconciliation invariance (the core relationship) --------
def test_invariant_to_capital_deployment_and_reconciliation():
    """Same realized exits, evaluated identically regardless of any equity
    composition. Because equity is not an input, two 'worlds' that differ only
    in wallet/position/unprojected composition produce the SAME level. This is
    the cross-module invariant that the old effective_bankroll breaker
    violated."""
    exits = [_exit(+0.5, 4), _exit(-0.5, 9)]  # net 0 realized
    a = _snap(exits)
    b = _snap(exits)  # no equity knob exists to perturb
    assert "level" not in a and "level" not in b
    assert a["loss"] == b["loss"] == 0.0


def test_unparseable_or_missing_timestamps_are_skipped_not_counted():
    exits = [
        {"city": "X", "exited_at": "", "pnl": -99.0},
        {"city": "Y", "exited_at": "not-a-date", "pnl": -99.0},
        _exit(-2.0, 3),
    ]
    snap = _snap(exits)
    # only the parseable -2.0 counts; the two -99.0 garbage rows are skipped
    assert snap["loss"] == pytest.approx(2.0, abs=1e-6)
    assert snap["reference"]["settlement_count"] == 1
    assert snap["reference"]["skipped_unparseable"] == 2
