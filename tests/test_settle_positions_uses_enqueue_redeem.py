# Created: 2026-05-05
# Last reused or audited: 2026-05-05
# Authority basis: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T2G/phase.json
"""Tests for T2G: _settle_positions routes redeem through enqueue_redeem_command.

Invariants asserted:
  T2G-NO-INLINE-REQUEST-REDEEM:
    git grep + AST inspection confirms no call site in src/** imports
    request_redeem outside enqueue_redeem_command's body.
  T2G-SETTLE-USES-WRAPPER:
    _settle_positions calls enqueue_redeem_command (not request_redeem directly)
    for each redeemable settled position.
  T2G-REDEEM-STATE-TRANSITION-AUDITABLE:
    No other src/** call site invokes request_redeem outside enqueue_redeem_command.

Tests:
  test_no_inline_request_redeem_in_src
      — grep + AST: request_redeem not imported in src/** except inside
        enqueue_redeem_command's function body.
  test_settle_positions_calls_enqueue_redeem_command
      — monkeypatch enqueue_redeem_command; assert it is called once per
        redeemable position with the correct kwargs.
  test_settle_positions_does_not_call_request_redeem_directly
      — monkeypatch request_redeem at its definition site; assert it is
        NOT called from _settle_positions (only enqueue_redeem_command is).
"""
from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# Repository root — used for src/ path calculations.
_REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# T2G-NO-INLINE-REQUEST-REDEEM  /  T2G-REDEEM-STATE-TRANSITION-AUDITABLE
# ---------------------------------------------------------------------------

def test_no_inline_request_redeem_in_src():
    """git grep: 'from src.execution.settlement_commands import request_redeem'
    appears exactly once in src/**  and that occurrence is inside
    enqueue_redeem_command's body in harvester.py.

    AST verification confirms the import node is inside a FunctionDef named
    'enqueue_redeem_command', not at module scope or inside _settle_positions.
    """
    result = subprocess.run(
        [
            "git", "grep", "-n",
            "from src.execution.settlement_commands import request_redeem",
            "--", "src/",
        ],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]

    # Filter out comment lines (git grep includes comments).
    non_comment_lines = [l for l in lines if not _is_comment_grep_line(l)]

    assert len(non_comment_lines) == 1, (
        f"Expected exactly 1 non-comment 'from src.execution.settlement_commands import "
        f"request_redeem' in src/**, got {len(non_comment_lines)}:\n"
        + "\n".join(non_comment_lines)
    )

    only_line = non_comment_lines[0]
    assert "harvester.py" in only_line, (
        f"Expected the sole occurrence to be in harvester.py, got: {only_line}"
    )

    # AST check: the import is inside enqueue_redeem_command, not _settle_positions.
    harvester_path = _REPO_ROOT / "src" / "execution" / "harvester.py"
    source = harvester_path.read_text()
    tree = ast.parse(source)

    # Walk top-level functions; find which FunctionDef contains the import.
    containing_funcs = _find_functions_containing_request_redeem_import(tree)

    assert "enqueue_redeem_command" in containing_funcs, (
        "AST: 'from src.execution.settlement_commands import request_redeem' must be "
        f"inside enqueue_redeem_command. Found in: {containing_funcs}"
    )
    assert "_settle_positions" not in containing_funcs, (
        "AST: request_redeem import must NOT appear in _settle_positions. "
        f"Found in: {containing_funcs}"
    )


def _is_comment_grep_line(grep_line: str) -> bool:
    """Return True if the grep hit line is a Python comment (# ...).

    grep output format: 'filename:lineno:content'
    """
    parts = grep_line.split(":", 2)
    if len(parts) < 3:
        return False
    content = parts[2].lstrip()
    return content.startswith("#")


def _find_functions_containing_request_redeem_import(tree: ast.Module) -> list[str]:
    """Return names of top-level FunctionDef nodes whose body contains
    'from src.execution.settlement_commands import request_redeem'."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.ImportFrom):
                continue
            module = child.module or ""
            if module == "src.execution.settlement_commands":
                names = [alias.name for alias in child.names]
                if "request_redeem" in names:
                    found.append(node.name)
    return found


# ---------------------------------------------------------------------------
# T2G-SETTLE-USES-WRAPPER  (monkeypatch path)
# ---------------------------------------------------------------------------

def _make_in_memory_conn_with_settlement_schema():
    """In-memory SQLite with the tables _settle_positions reads."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS position_current (
            trade_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            phase TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decision_log (
            trade_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            bin_label TEXT,
            direction TEXT,
            entry_price REAL,
            exit_price REAL,
            pnl REAL,
            strategy TEXT,
            source TEXT,
            settled_at TEXT,
            decision_snapshot_id INTEGER,
            edge_source TEXT
        )
    """)
    conn.commit()
    return conn


def _make_mock_portfolio_with_position(
    trade_id="trade-t2g-001",
    city="London",
    target_date="2026-05-01",
    direction="buy_yes",
    condition_id="cond-abc123",
    token_id="tok-yes-001",
    shares=100.0,
    entry_price=0.6,
):
    """Return a minimal mock PortfolioState with one position.

    Attributes are set so the position passes all the skip-guards in
    _settle_positions (state not in skip-set, direction valid, etc.).
    """
    from unittest.mock import MagicMock

    pos = MagicMock()
    pos.trade_id = trade_id
    pos.city = city
    pos.target_date = target_date
    pos.direction = direction
    pos.condition_id = condition_id
    pos.token_id = token_id
    pos.no_token_id = None
    pos.entry_price = entry_price
    pos.shares = shares
    pos.p_posterior = 0.7
    pos.bin_label = "16-17°C"
    pos.exit_price = None
    pos.entry_method = "model"
    pos.selected_method = "model"
    pos.decision_snapshot_id = ""
    pos.edge_source = "model"
    pos.strategy = "default"
    pos.last_exit_at = "2026-05-01T18:00:00Z"
    pos.market_id = condition_id
    # State must NOT be in the skip-set (pending_tracked, quarantined, etc.)
    pos.state = "active"
    pos.exit_state = ""
    pos.chain_state = ""

    portfolio = MagicMock()
    portfolio.positions = [pos]
    portfolio.ignored_tokens = []

    return portfolio, pos


def test_settle_positions_calls_enqueue_redeem_command(monkeypatch):
    """_settle_positions calls enqueue_redeem_command for each redeemable
    position (exit_price > 0, condition_id present).

    T2G-SETTLE-USES-WRAPPER: the wrapper is called exactly once per
    redeemable position with expected kwargs.
    """
    import src.execution.harvester as hv
    import src.execution.exit_lifecycle as el

    conn = _make_in_memory_conn_with_settlement_schema()
    portfolio, pos = _make_mock_portfolio_with_position()

    enqueue_calls = []

    def fake_enqueue(c, *, condition_id, payout_asset, market_id, pusd_amount_micro, token_amounts, trade_id):
        enqueue_calls.append({
            "condition_id": condition_id,
            "payout_asset": payout_asset,
            "market_id": market_id,
            "pusd_amount_micro": pusd_amount_micro,
            "token_amounts": token_amounts,
            "trade_id": trade_id,
        })
        return {"status": "queued", "command_id": "cmd-001", "reason": None}

    monkeypatch.setattr(hv, "enqueue_redeem_command", fake_enqueue)

    # Stub out the heavier dependencies so the test focuses on redeem wiring.
    monkeypatch.setattr(hv, "_get_canonical_exit_flag", lambda: True)
    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "record_token_suppression",
                        lambda *a, **kw: {"status": "written"})

    # Stub _settlement_economics_for_position to return (shares, cost_basis).
    monkeypatch.setattr(hv, "_settlement_economics_for_position",
                        lambda p: (p.shares, p.entry_price * p.shares))

    # Stub mark_settled (local import inside _settle_positions via exit_lifecycle).
    closed = MagicMock()
    closed.trade_id = pos.trade_id
    closed.pnl = 10.0
    closed.bin_label = pos.bin_label
    closed.direction = pos.direction
    closed.p_posterior = pos.p_posterior
    closed.decision_snapshot_id = ""
    closed.edge_source = "model"
    closed.strategy = "default"
    closed.last_exit_at = "2026-05-01T18:00:00Z"
    closed.exit_price = 1.0

    monkeypatch.setattr(el, "mark_settled", lambda *a, **kw: closed)

    records = []

    hv._settle_positions(
        conn, portfolio,
        city="London",
        target_date="2026-05-01",
        winning_label=pos.bin_label,
        settlement_records=records,
    )

    assert len(enqueue_calls) == 1, (
        f"Expected exactly 1 enqueue_redeem_command call, got {len(enqueue_calls)}"
    )
    call_kwargs = enqueue_calls[0]
    assert call_kwargs["condition_id"] == pos.condition_id
    assert call_kwargs["payout_asset"] == "pUSD"
    assert call_kwargs["trade_id"] == pos.trade_id
    assert call_kwargs["pusd_amount_micro"] == int(round(pos.shares * 1_000_000))


def test_settle_positions_does_not_call_request_redeem_directly(monkeypatch):
    """_settle_positions must NOT call request_redeem directly.

    Monkeypatches request_redeem at its definition site and asserts it
    stays uncalled. enqueue_redeem_command is also stubbed so the test
    exercises only the call-routing boundary.
    """
    import src.execution.harvester as hv
    import src.execution.settlement_commands as sc
    import src.execution.exit_lifecycle as el

    conn = _make_in_memory_conn_with_settlement_schema()
    portfolio, pos = _make_mock_portfolio_with_position()

    request_redeem_mock = MagicMock(return_value="cmd-should-not-appear")
    monkeypatch.setattr(sc, "request_redeem", request_redeem_mock)

    # Stub enqueue_redeem_command at the harvester module level.
    enqueue_mock = MagicMock(return_value={"status": "queued", "command_id": "cmd-002", "reason": None})
    monkeypatch.setattr(hv, "enqueue_redeem_command", enqueue_mock)

    monkeypatch.setattr(hv, "_get_canonical_exit_flag", lambda: True)
    monkeypatch.setattr(hv, "log_event", lambda *a, **kw: None)
    monkeypatch.setattr(hv, "record_token_suppression",
                        lambda *a, **kw: {"status": "written"})
    monkeypatch.setattr(hv, "_settlement_economics_for_position",
                        lambda p: (p.shares, p.entry_price * p.shares))

    closed = MagicMock()
    closed.trade_id = pos.trade_id
    closed.pnl = 10.0
    closed.bin_label = pos.bin_label
    closed.direction = pos.direction
    closed.p_posterior = pos.p_posterior
    closed.decision_snapshot_id = ""
    closed.edge_source = "model"
    closed.strategy = "default"
    closed.last_exit_at = "2026-05-01T18:00:00Z"
    closed.exit_price = 1.0
    monkeypatch.setattr(el, "mark_settled", lambda *a, **kw: closed)

    hv._settle_positions(
        conn, portfolio,
        city="London",
        target_date="2026-05-01",
        winning_label=pos.bin_label,
        settlement_records=[],
    )

    request_redeem_mock.assert_not_called()
    enqueue_mock.assert_called_once()
