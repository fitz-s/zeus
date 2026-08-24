# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 6 follow-up. Integration coverage for
#   src/engine/tier0_drawdown_hook.py::tier0_seed_and_check_drawdown_kill:
#   seed-once semantics, restart survival (re-read seed), the entry-time
#   proxy boundary, and breach -> pause_fn wiring.
"""Position-query calls are monkeypatched at src.state.db.query_portfolio_loader_view
(the same reconciliation seam the rest of the money path trusts) rather than
fabricated through the full execution_fact fill-hint machinery -- this module
owns the seed/boundary/drawdown-check logic, not that reconciliation."""
from __future__ import annotations

import sqlite3

import src.state.db as db
from src.engine.tier0_drawdown_hook import tier0_seed_and_check_drawdown_kill
from src.state.ledger import apply_architecture_kernel_schema
from src.strategy.tier0_policy import tier0_start_equity_override_id

RISK_POLICY_YAML = """
policy_version: "9"
tier0:
  aggregate_open_loss_pct_ceiling: 0.02
  drawdown_kill_pct: 0.10
  epoch: 1
"""


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_architecture_kernel_schema(conn)
    return conn


def _seed_row(conn):
    return conn.execute(
        "SELECT value FROM control_overrides WHERE override_id = ?",
        (tier0_start_equity_override_id(1),),
    ).fetchone()


def _write_risk_policy(tmp_path, monkeypatch):
    path = tmp_path / "risk_policy.yaml"
    path.write_text(RISK_POLICY_YAML)
    monkeypatch.setattr(
        "src.strategy.tier0_policy.RISK_POLICY_ARTIFACT_PATH", path
    )
    return path


def test_seed_written_once_when_missing(tmp_path, monkeypatch):
    _write_risk_policy(tmp_path, monkeypatch)
    conn = _memory_conn()
    monkeypatch.setattr(
        db, "query_portfolio_loader_view", lambda *a, **kw: {"positions": []}
    )
    paused = []
    monkeypatch.setattr(
        "src.control.control_plane.pause_entries",
        lambda reason, **kw: paused.append(reason),
    )

    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 268.0)

    row = _seed_row(conn)
    assert row is not None
    from src.strategy.tier0_policy import parse_tier0_seed

    seed = parse_tier0_seed(row["value"])
    assert seed["start_equity_usd"] == 268.0
    assert seed["epoch"] == 1
    # First-seed cycle never checks drawdown (zero elapsed) -- no pause call.
    assert paused == []


def test_seed_not_overwritten_on_second_call_restart_survival(tmp_path, monkeypatch):
    _write_risk_policy(tmp_path, monkeypatch)
    conn = _memory_conn()
    monkeypatch.setattr(
        db, "query_portfolio_loader_view", lambda *a, **kw: {"positions": []}
    )
    monkeypatch.setattr(
        "src.control.control_plane.pause_entries", lambda reason, **kw: None
    )

    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 268.0)
    first = _seed_row(conn)["value"]

    # Simulate a restart (or a later cycle, or a flag off->on flip with the
    # same epoch): a fresh call with a DIFFERENT current bankroll must NOT
    # overwrite the existing seed.
    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 999.0)
    second = _seed_row(conn)["value"]

    assert first == second
    from src.strategy.tier0_policy import parse_tier0_seed

    assert parse_tier0_seed(second)["start_equity_usd"] == 268.0


def test_position_before_started_at_excluded_proxy_boundary(tmp_path, monkeypatch):
    _write_risk_policy(tmp_path, monkeypatch)
    conn = _memory_conn()
    monkeypatch.setattr(
        db, "query_portfolio_loader_view", lambda *a, **kw: {"positions": []}
    )
    monkeypatch.setattr(
        "src.control.control_plane.pause_entries", lambda reason, **kw: None
    )
    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 100.0)
    started_at = __import__("src.strategy.tier0_policy", fromlist=["parse_tier0_seed"]).parse_tier0_seed(
        _seed_row(conn)["value"]
    )["started_at_utc"]

    # A closed position that FILLED before started_at (pre-Tier-0 entry) must
    # not count toward Tier-0 drawdown, no matter how large its loss.
    before_ts = "2020-01-01T00:00:00+00:00"
    assert before_ts < started_at
    monkeypatch.setattr(
        db,
        "query_portfolio_loader_view",
        lambda *a, **kw: {
            "positions": [
                {
                    "phase": "settled",
                    "execution_fact_filled_at": before_ts,
                    "shares": 1000.0,
                    "exit_price": 0.0,
                    "cost_basis_usd": 90.0,  # would be a -90 loss, 90% of 100 equity
                    "entry_price": 0.09,
                    "chain_shares": 0.0,
                    "chain_cost_basis_usd": 0.0,
                    "chain_avg_price": 0.0,
                }
            ]
        },
    )
    paused = []
    monkeypatch.setattr(
        "src.control.control_plane.pause_entries",
        lambda reason, **kw: paused.append(reason),
    )

    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 100.0)

    assert paused == []


def test_position_after_started_at_breach_calls_pause_fn_once(tmp_path, monkeypatch):
    _write_risk_policy(tmp_path, monkeypatch)
    conn = _memory_conn()
    monkeypatch.setattr(
        db, "query_portfolio_loader_view", lambda *a, **kw: {"positions": []}
    )
    monkeypatch.setattr(
        "src.control.control_plane.pause_entries", lambda reason, **kw: None
    )
    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 100.0)

    # Drawdown-kill ceiling is 10% of 100 = $10. A -$15 realized loss on a
    # position that filled AFTER started_at must breach and pause once.
    after_ts = "2099-01-01T00:00:00+00:00"
    monkeypatch.setattr(
        db,
        "query_portfolio_loader_view",
        lambda *a, **kw: {
            "positions": [
                {
                    "phase": "settled",
                    "execution_fact_filled_at": after_ts,
                    "shares": 100.0,
                    "exit_price": 0.0,
                    "cost_basis_usd": 15.0,
                    "entry_price": 0.15,
                    "chain_shares": 0.0,
                    "chain_cost_basis_usd": 0.0,
                    "chain_avg_price": 0.0,
                }
            ]
        },
    )
    paused = []
    monkeypatch.setattr(
        "src.control.control_plane.pause_entries",
        lambda reason, **kw: paused.append(reason),
    )

    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 100.0)

    assert paused == ["reversal_plan_tier0_drawdown_kill_breached"]

    # Idempotency: calling again (already-paused state, still breached) must
    # call pause_fn again but ONLY once per call -- never more than once per
    # invocation, and the caller's own admission gates (not tested here) are
    # what make the already-paused state a no-op downstream.
    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=lambda: 100.0)
    assert paused == [
        "reversal_plan_tier0_drawdown_kill_breached",
        "reversal_plan_tier0_drawdown_kill_breached",
    ]


def test_hook_failure_is_best_effort_never_raises(tmp_path, monkeypatch):
    _write_risk_policy(tmp_path, monkeypatch)
    conn = _memory_conn()

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(db, "query_portfolio_loader_view", _boom)
    # No seed yet, so the bankroll-provider branch runs -- make IT fail too,
    # to prove the whole function is wrapped, not just the position query.
    def _boom_provider():
        raise RuntimeError("simulated bankroll failure")

    # Should not raise.
    tier0_seed_and_check_drawdown_kill(conn, bankroll_usd_provider=_boom_provider)
