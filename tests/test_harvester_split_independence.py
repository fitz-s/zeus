# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §6 antibody #12
"""Antibody #12 — Harvester split independence.

Four tests enforce the structural boundary between the ingest-side settlement
truth writer and the trading-side P&L resolver:

  Test 1: harvester_truth_writer does NOT import from trading modules.
  Test 2: harvester_pnl_resolver does NOT import from ingest_main or scripts.ingest.
  Test 3: harvester_truth_writer only writes to world tables (settlements, settlements_v2,
          market_events_v2), never to trade tables (decision_log, position_*, etc.).
  Test 4: harvester_pnl_resolver does NOT write settlements; reads settlements only.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

_TRUTH_WRITER = PROJECT_ROOT / "src" / "ingest" / "harvester_truth_writer.py"
_PNL_RESOLVER = PROJECT_ROOT / "src" / "execution" / "harvester_pnl_resolver.py"

# ---------------------------------------------------------------------------
# Forbidden import prefixes
# ---------------------------------------------------------------------------

_TRADING_FORBIDDEN_PREFIXES = (
    "src.engine",
    "src.strategy",
    "src.signal",
    "src.execution",  # harvester_truth_writer must not import from src.execution.*
    "src.main",
    "src.control",
    "src.supervisor",
)

_INGEST_FORBIDDEN_PREFIXES = (
    "src.ingest_main",
    "scripts.ingest",
)

# Trade-side table names (harvester_truth_writer must NOT write these)
_TRADE_WRITE_TABLES = (
    "decision_log",
    "position_events",
    "position_current",
    "trade_decisions",
    "venue_commands",
    "risk_state",
    "portfolio",
)

# World-side table names that harvester_pnl_resolver must NOT write
_WORLD_WRITE_TABLES = (
    "settlements",
    "settlements_v2",
    "market_events_v2",
    "observations",
    "observation_instants_v2",
    "forecasts",
    "solar_daily",
    "data_coverage",
    "ensemble_snapshots",
    "ensemble_snapshots_v2",
    "calibration_pairs_v2",
    "platt_models_v2",
    "model_bias",
    "forecast_skill",
)

_SQL_WRITE_RE = __import__("re").compile(
    r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b",
    __import__("re").IGNORECASE,
)


def _collect_imports(source: str) -> list[str]:
    """Return all module names imported (top-level + deferred) in source."""
    imports: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


def _has_write_targeting(source: str, tables: tuple[str, ...]) -> list[str]:
    """Return table names that appear with a SQL write verb in source."""
    import re
    hits: list[str] = []
    write_re = re.compile(
        r"\b(INSERT\s+INTO|UPDATE\s+\w|DELETE\s+FROM)\b",
        re.IGNORECASE,
    )
    table_re = re.compile(
        r"\b(" + "|".join(re.escape(t) for t in tables) + r")\b",
        re.IGNORECASE,
    )
    has_write = bool(write_re.search(source))
    if not has_write:
        return hits
    for table in tables:
        if re.search(r"\b" + re.escape(table) + r"\b", source, re.IGNORECASE):
            hits.append(table)
    return hits


# ---------------------------------------------------------------------------
# Test 1: harvester_truth_writer must not import trading modules
# ---------------------------------------------------------------------------

def test_harvester_truth_writer_does_not_import_trading():
    """AST scan: src/ingest/harvester_truth_writer.py must not import trading modules.

    Forbidden prefixes: src.engine, src.strategy, src.signal, src.execution.*,
    src.main, src.control, src.supervisor.
    The module lives on the ingest side; it must be loadable without trading imports.
    """
    assert _TRUTH_WRITER.exists(), (
        f"Expected file not found: {_TRUTH_WRITER.relative_to(PROJECT_ROOT)}"
    )
    source = _TRUTH_WRITER.read_text(encoding="utf-8")
    imports = _collect_imports(source)

    violations = []
    for imp in imports:
        for prefix in _TRADING_FORBIDDEN_PREFIXES:
            if imp == prefix or imp.startswith(prefix + "."):
                violations.append(f"  import {imp!r} is forbidden (prefix {prefix!r})")
                break

    assert not violations, (
        "harvester_truth_writer.py imports from trading-side modules:\n"
        + "\n".join(violations)
        + "\n\nThis breaks ingest-side independence (design §5 Phase 1.5)."
    )


# ---------------------------------------------------------------------------
# Test 2: harvester_pnl_resolver must not import ingest_main or scripts.ingest
# ---------------------------------------------------------------------------

def test_harvester_pnl_resolver_does_not_import_ingest_main():
    """AST scan: src/execution/harvester_pnl_resolver.py must not import ingest_main.

    Forbidden: src.ingest_main, scripts.ingest.* — these are ingest-daemon internals.
    The resolver runs on the trading scheduler; coupling to ingest_main would
    re-create the lifecycle dependency this split is designed to remove.
    """
    assert _PNL_RESOLVER.exists(), (
        f"Expected file not found: {_PNL_RESOLVER.relative_to(PROJECT_ROOT)}"
    )
    source = _PNL_RESOLVER.read_text(encoding="utf-8")
    imports = _collect_imports(source)

    violations = []
    for imp in imports:
        for prefix in _INGEST_FORBIDDEN_PREFIXES:
            if imp == prefix or imp.startswith(prefix + "."):
                violations.append(f"  import {imp!r} is forbidden (prefix {prefix!r})")
                break

    assert not violations, (
        "harvester_pnl_resolver.py imports from ingest-daemon modules:\n"
        + "\n".join(violations)
        + "\n\nThis breaks trading-side independence (design §5 Phase 1.5)."
    )


# ---------------------------------------------------------------------------
# Test 3: harvester_truth_writer only writes world tables, never trade tables
# ---------------------------------------------------------------------------

def test_harvester_truth_writer_only_writes_world_settlements():
    """Grep: harvester_truth_writer.py must not contain SQL writes to trade tables.

    The ingest-side writer owns ONLY world.settlements (+ settlements_v2,
    market_events_v2). It must NOT emit INSERT INTO / UPDATE / DELETE FROM
    targeting: decision_log, position_events, position_current, trade_decisions,
    venue_commands, risk_state, portfolio.
    """
    assert _TRUTH_WRITER.exists(), (
        f"Expected file not found: {_TRUTH_WRITER.relative_to(PROJECT_ROOT)}"
    )
    source = _TRUTH_WRITER.read_text(encoding="utf-8")

    trade_table_hits = _has_write_targeting(source, _TRADE_WRITE_TABLES)
    assert not trade_table_hits, (
        f"harvester_truth_writer.py contains SQL write verbs targeting trade tables: "
        f"{trade_table_hits}.\n"
        f"The ingest-side writer must only write world tables (settlements, "
        f"settlements_v2, market_events_v2)."
    )


# ---------------------------------------------------------------------------
# Test 4: harvester_pnl_resolver must not write settlements
# ---------------------------------------------------------------------------

def test_harvester_pnl_resolver_does_not_write_world_settlements():
    """Grep: harvester_pnl_resolver.py must not contain SQL writes to world tables.

    The trading-side resolver READS world.settlements (SELECT is allowed) but must
    NOT write it or any other world table. All writes go to trade-side tables
    (decision_log via store_settlement_records, position tables via _settle_positions).
    """
    assert _PNL_RESOLVER.exists(), (
        f"Expected file not found: {_PNL_RESOLVER.relative_to(PROJECT_ROOT)}"
    )
    source = _PNL_RESOLVER.read_text(encoding="utf-8")

    world_table_hits = _has_write_targeting(source, _WORLD_WRITE_TABLES)
    assert not world_table_hits, (
        f"harvester_pnl_resolver.py contains SQL write verbs targeting world tables: "
        f"{world_table_hits}.\n"
        f"The trading-side resolver must only READ world.settlements, not write it."
    )


def test_harvester_pnl_resolver_passes_verified_world_truth_to_position_settlement():
    """Static relationship: world.settlements VERIFIED authority reaches _settle_positions."""
    source = _PNL_RESOLVER.read_text(encoding="utf-8")

    assert "WHERE authority = 'VERIFIED'" in source
    assert 'settlement_truth_source="world.settlements"' in source
    assert "settlement_authority=authority" in source
    assert "settlement_temperature_metric=str(temperature_metric or \"\")" in source
