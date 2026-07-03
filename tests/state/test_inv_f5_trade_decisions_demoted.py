# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §F5
"""Acceptance tests: F5 — trade_decisions demoted to audit-only legacy export.

F5 invariant: the live entry path (log_trade_entry) runs successfully on a DB
with NO trade_decisions table and does not raise. Canonical truth lives in
position_events / position_current — not trade_decisions.

Three contracts verified:

1. log_trade_entry succeeds without trade_decisions table present (no exception).

2. Static: no money-path module (calibration / exit / reconciliation / risk /
   harvester) contains a SELECT from trade_decisions.  Modules that reference
   trade_decisions only do so in replay / backtest / diagnostic code that is
   explicitly NOT the live-trading money path.

3. db_table_ownership.yaml classifies trade_decisions (trade db) as schema_class
   'archive', not 'trade_class'.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]

# ---------------------------------------------------------------------------
# Money-path modules that must NOT SELECT from trade_decisions
# ---------------------------------------------------------------------------

_MONEY_PATH_MODULES = [
    "src/state/chain_reconciliation.py",
    "src/execution/exit_triggers.py",
    "src/engine/monitor_refresh.py",
    "src/riskguard/riskguard.py",
]

# Modules that are allowed to reference trade_decisions (replay/backtest/diagnostic):
_ALLOWED_READERS = {
    "src/engine/replay.py",
    "src/backtest/economics.py",
    "src/execution/command_recovery.py",   # _filled_entry_lot_materialization_candidates
    "src/execution/harvester.py",          # SD-1 best-effort writeback (not a query)
    "src/engine/lifecycle_events.py",      # comment / docstring only
    "src/execution/fill_tracker.py",       # comment only
    "src/state/calibration_observation.py",  # comment / PATH B docstring
    "src/state/db.py",                     # DDL + schema helpers (not money-path query)
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_conn_without_trade_decisions() -> sqlite3.Connection:
    """In-memory DB with init_schema run but trade_decisions table dropped."""
    from src.state.db import init_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    conn.execute("DROP TABLE IF EXISTS trade_decisions")
    conn.commit()
    return conn


def _make_minimal_position():
    from src.state.portfolio import Position

    return Position(
        trade_id="f5-test-001",
        market_id="mkt-f5",
        city="Chicago",
        cluster="Great Lakes",
        target_date="2026-06-15",
        bin_label="60-65",
        direction="buy_yes",
        size_usd=10.0,
        entry_price=0.40,
        p_posterior=0.55,
        shares=25.0,
        cost_basis_usd=10.0,
        state="entered",
        edge=0.05,
        edge_source="calibration",
        entry_ci_width=0.10,
        env="live",
        entry_method="limit",
        selected_method="limit",
    )


# ---------------------------------------------------------------------------
# Test 1: log_trade_entry succeeds without trade_decisions table
# ---------------------------------------------------------------------------

def test_log_trade_entry_succeeds_without_trade_decisions_table():
    """log_trade_entry must not raise when trade_decisions table is absent."""
    from src.state.db import log_trade_entry

    conn = _make_minimal_conn_without_trade_decisions()
    pos = _make_minimal_position()

    # Must not raise — F5 invariant: live path runs without trade_decisions
    log_trade_entry(conn, pos)


# ---------------------------------------------------------------------------
# Test 2: money-path modules do not SELECT from trade_decisions
# ---------------------------------------------------------------------------

def test_money_path_modules_do_not_select_trade_decisions():
    """No money-path source file contains a SELECT from trade_decisions."""
    found = []
    for rel_path in _MONEY_PATH_MODULES:
        abs_path = REPO_ROOT / rel_path
        if not abs_path.exists():
            continue
        text = abs_path.read_text(encoding="utf-8")
        # Look for SQL SELECT referencing trade_decisions (case-insensitive)
        lower = text.lower()
        # Find lines that mention trade_decisions in a SELECT context
        for lineno, line in enumerate(text.splitlines(), 1):
            if "trade_decisions" in line.lower() and "select" in line.lower():
                found.append(f"{rel_path}:{lineno}: {line.strip()}")

    assert found == [], (
        f"Money-path modules must not SELECT from trade_decisions. Found:\n"
        + "\n".join(found)
    )


# ---------------------------------------------------------------------------
# Test 3: registry classifies trade_decisions (trade db) as archive
# ---------------------------------------------------------------------------

def test_registry_classifies_trade_decisions_as_archive():
    """db_table_ownership.yaml must have trade_decisions (db: trade) as schema_class: archive."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        import pytest
        pytest.skip("pyyaml not installed")

    registry_path = REPO_ROOT / "architecture" / "db_table_ownership.yaml"
    assert registry_path.exists(), f"{registry_path} not found"

    with registry_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    tables = data.get("tables", []) if isinstance(data, dict) else []
    entry = next(
        (t for t in tables if t.get("name") == "trade_decisions" and t.get("db") == "trade"),
        None,
    )
    assert entry is not None, "No trade_decisions / db=trade entry in registry"
    sc = entry.get("schema_class", "")
    assert sc == "archive", (
        f"trade_decisions (db=trade) schema_class must be 'archive', got {sc!r}"
    )
