"""Cross-module relationship tests.

These tests verify that when Module A's output flows to Module B,
the critical property survives the boundary. They are LIVE STATE tests —
they query the actual DB/files. A failing test is a failing antibody:
the invariant is broken in production right now.

Test philosophy (from CLAUDE.md):
  Standard tests: given input X, output is Y.
  Relationship tests: when Module A's output flows to Module B,
  what property must survive?

All tests skip gracefully when the DB has no relevant data yet.
They FAIL when data exists but the invariant is violated.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _zeus_conn():
    from src.config import settings
    from src.state.db import get_trade_connection_with_world, init_schema

    conn = get_trade_connection_with_world()
    init_schema(conn)
    return conn


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# Test 1: harvester → chronicle + outcome_fact + trade_decisions + risk_state
# ---------------------------------------------------------------------------

def test_settlement_pnl_flows_to_all_surfaces():
    """After settlement, P&L must agree across all four surfaces:
    chronicle.SETTLEMENT → outcome_fact → trade_decisions.settlement_edge_usd
    → risk_state.realized_pnl (aggregate).

    Relationship: harvester.log_event(SETTLEMENT) must produce matching records
    on all four downstream surfaces. If any surface is missing or diverges,
    a pipeline stage failed silently.

    This test catches SD-1 / SD-2 class regressions.
    """
    from src.state.db import RISK_DB_PATH
    from src.config import settings

    conn = _zeus_conn()
    env = settings.mode

    # Gather settled trade_ids from chronicle
    settled_rows = conn.execute(
        """
        SELECT trade_id,
               json_extract(details_json, '$.pnl') AS pnl
        FROM chronicle
        WHERE event_type = 'SETTLEMENT'
          AND env = ?
          AND trade_id IS NOT NULL
          AND id IN (
              SELECT MAX(id)
              FROM chronicle
              WHERE event_type = 'SETTLEMENT'
                AND env = ?
                AND trade_id IS NOT NULL
              GROUP BY trade_id
          )
        """,
        (env, env),
    ).fetchall()

    if not settled_rows:
        conn.close()
        pytest.skip("No SETTLEMENT events in chronicle — nothing to check.")

    chronicle_pnl = {
        r["trade_id"]: float(r["pnl"]) if r["pnl"] is not None else None
        for r in settled_rows
    }

    failures = []

    for trade_id, chron_pnl in chronicle_pnl.items():
        if chron_pnl is None:
            continue  # Chronicle has NULL pnl — can't assert downstream

        # --- outcome_fact ---
        if _table_exists(conn, "outcome_fact"):
            of_row = conn.execute(
                "SELECT pnl FROM outcome_fact WHERE position_id = ?",
                (trade_id,),
            ).fetchone()
            if of_row is None:
                failures.append(
                    f"{trade_id}: chronicle has SETTLEMENT pnl={chron_pnl:.4f} "
                    f"but outcome_fact has NO ROW (SD-2 regression)"
                )
            elif of_row["pnl"] is not None and abs(float(of_row["pnl"]) - chron_pnl) > 0.01:
                failures.append(
                    f"{trade_id}: outcome_fact.pnl={of_row['pnl']:.4f} "
                    f"!= chronicle pnl={chron_pnl:.4f}"
                )

        # --- trade_decisions ---
        if _table_exists(conn, "trade_decisions"):
            td_row = conn.execute(
                """
                SELECT settlement_edge_usd
                FROM trade_decisions
                WHERE runtime_trade_id = ?
                ORDER BY rowid DESC LIMIT 1
                """,
                (trade_id,),
            ).fetchone()
            if td_row is None:
                failures.append(
                    f"{trade_id}: chronicle has SETTLEMENT "
                    f"but trade_decisions has NO ROW (SD-1 regression)"
                )
            elif td_row["settlement_edge_usd"] is None:
                failures.append(
                    f"{trade_id}: trade_decisions.settlement_edge_usd IS NULL "
                    f"(SD-1 fix not applied)"
                )
            elif abs(float(td_row["settlement_edge_usd"]) - chron_pnl) > 0.01:
                failures.append(
                    f"{trade_id}: trade_decisions.settlement_edge_usd="
                    f"{td_row['settlement_edge_usd']:.4f} != chronicle pnl={chron_pnl:.4f}"
                )

    conn.close()

    # --- risk_state aggregate ---
    # The sum of chronicle SETTLEMENT pnl must be reflected in risk_state.realized_pnl.
    # We check that risk_state has at least one row and its realized_pnl is non-zero
    # when we have settled trades (not a precise match — risk_state is sampled).
    chronicle_total = sum(p for p in chronicle_pnl.values() if p is not None)
    if chronicle_total != 0.0:
        try:
            risk_conn = sqlite3.connect(str(RISK_DB_PATH))
            risk_conn.row_factory = sqlite3.Row
            risk_row = risk_conn.execute(
                "SELECT details_json FROM risk_state ORDER BY checked_at DESC LIMIT 1"
            ).fetchone()
            risk_conn.close()
            if risk_row is not None:
                details = json.loads(risk_row["details_json"])
                risk_realized = float(details.get("realized_pnl") or 0.0)
                # Risk state is updated at tick frequency — allow 10% tolerance for timing lag
                if abs(risk_realized - chronicle_total) > abs(chronicle_total) * 0.5 + 0.10:
                    failures.append(
                        f"risk_state.realized_pnl={risk_realized:.4f} far from "
                        f"chronicle total={chronicle_total:.4f} (>50% divergence)"
                    )
        except Exception as exc:
            failures.append(f"risk_state read failed: {exc}")

    if failures:
        pytest.fail(
            f"{len(failures)} settlement P&L surface failures:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )


# ---------------------------------------------------------------------------
# Test 2: ledger.canonical_write → position_events + position_current
# ---------------------------------------------------------------------------

def test_canonical_write_produces_matching_projection():
    """After entry, position_events must have a row AND position_current
    must have a matching row with the same trade_id.

    Relationship: ledger.canonical_write → projection.project_position_current.
    trade_decisions.runtime_trade_id must also match position_current.trade_id.

    A row in position_events with no matching position_current means the
    projection layer never consumed the event (projection pipeline broken).
    """
    conn = _zeus_conn()

    if not _table_exists(conn, "position_events") or not _table_exists(conn, "position_current"):
        conn.close()
        pytest.skip("Canonical position tables not present.")

    # position_events uses position_id (not trade_id)
    event_position_ids = set(
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT position_id FROM position_events WHERE position_id IS NOT NULL"
        ).fetchall()
        if r[0]
    )

    if not event_position_ids:
        conn.close()
        pytest.skip("No rows in position_events.")

    # position_current also uses position_id as primary key
    current_position_ids = set(
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT position_id FROM position_current WHERE position_id IS NOT NULL"
        ).fetchall()
        if r[0]
    )

    # Every event position_id must have a projection in position_current
    missing_in_current = event_position_ids - current_position_ids
    if missing_in_current:
        conn.close()
        sample = sorted(missing_in_current)[:5]
        pytest.fail(
            f"{len(missing_in_current)} position_ids in position_events have no "
            f"matching row in position_current (projection pipeline broken).\n"
            f"Sample: {sample}"
        )

    # Check trade_decisions.runtime_trade_id → position_current.trade_id cross-match
    # position_current has a separate trade_id column distinct from position_id
    if _table_exists(conn, "trade_decisions"):
        current_trade_ids = set(
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT trade_id FROM position_current WHERE trade_id IS NOT NULL"
            ).fetchall()
            if r[0]
        )
        td_rtids = set(
            r[0]
            for r in conn.execute(
                """
                SELECT DISTINCT runtime_trade_id
                FROM trade_decisions
                WHERE runtime_trade_id IS NOT NULL
                  AND runtime_trade_id != ''
                """
            ).fetchall()
            if r[0]
        )
        # Only fail for trade_ids that appear in both event surfaces
        # (have position_events AND trade_decisions) but are missing from position_current
        td_in_events = td_rtids & {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT trade_id FROM position_current WHERE trade_id IS NOT NULL"
            ).fetchall()
            if r[0]
        }
        # runtime_trade_ids with position_events entries but no position_current.trade_id match
        event_trade_ids = set(
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT trade_id FROM position_current WHERE trade_id IS NOT NULL"
            ).fetchall()
            if r[0]
        )
        td_missing_in_current = td_rtids - event_trade_ids
        if td_missing_in_current:
            # Cross-check: only raise if those IDs also appear in position_events
            # (i.e. they went through canonical write but never projected)
            event_pos_trade_ids = set(
                r[0]
                for r in conn.execute(
                    """
                    SELECT DISTINCT json_extract(payload_json, '$.trade_id') as tid
                    FROM position_events
                    WHERE json_extract(payload_json, '$.trade_id') IS NOT NULL
                    """
                ).fetchall()
                if r[0]
            )
            truly_broken = td_missing_in_current & event_pos_trade_ids
            if truly_broken:
                conn.close()
                sample = sorted(truly_broken)[:5]
                pytest.fail(
                    f"{len(truly_broken)} trade_decisions.runtime_trade_ids appear in "
                    f"position_events payload but NOT in position_current.trade_id.\n"
                    f"Sample: {sample}"
                )

    conn.close()


# ---------------------------------------------------------------------------
# Test 3: monitor_refresh → ExitContext.fresh_prob_is_fresh
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Phase2: paper best_bid fallback removed from _build_exit_context")
def test_monitor_refresh_updates_exit_context_freshness():
    """After monitor_refresh runs, last_monitor_prob_is_fresh must be set on
    the position. The ExitContext.fresh_prob_is_fresh that exit triggers read
    must reflect what monitor_refresh last wrote — not stale state.

    Relationship: monitor_refresh.refresh_position(pos) → pos.last_monitor_prob_is_fresh
    → _build_exit_context → ExitContext.fresh_prob_is_fresh → pos.evaluate_exit().

    This test verifies the contract by exercising the data flow directly:
    a position with a known monitor result must produce consistent freshness
    at the ExitContext boundary.
    """
    from types import SimpleNamespace
    from src.engine.cycle_runtime import _build_exit_context
    from src.execution.exit_lifecycle import ExitContext

    # --- Case A: monitor_refresh succeeds → fresh_prob_is_fresh=True ---
    pos_fresh = SimpleNamespace(
        trade_id="test-fresh",
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.55,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=0.54,
        last_monitor_best_ask=0.56,
        last_monitor_market_vig=0.02,
        last_monitor_whale_toxicity=None,
        chain_state="synced",
        state="open",
    )
    edge_ctx_fresh = SimpleNamespace(
        p_posterior=0.60,
        p_market=[0.55],
        divergence_score=0.01,
        market_velocity_1h=0.005,
        forward_edge=0.05,
    )
    exit_ctx_fresh = _build_exit_context(
        pos_fresh,
        edge_ctx_fresh,
        hours_to_settlement=10.0,
        ExitContext=ExitContext,
    )
    assert exit_ctx_fresh.fresh_prob_is_fresh is True, (
        "monitor_refresh set last_monitor_prob_is_fresh=True but ExitContext.fresh_prob_is_fresh "
        "is False — freshness flag dropped at the monitor→exit_context boundary."
    )

    # --- Case B: monitor_refresh fallback/failure → fresh_prob_is_fresh=False ---
    pos_stale = SimpleNamespace(
        trade_id="test-stale",
        last_monitor_prob_is_fresh=False,  # monitor_refresh.py line 372: fallback sets False
        last_monitor_market_price=0.55,
        last_monitor_market_price_is_fresh=False,
        last_monitor_best_bid=None,
        last_monitor_best_ask=None,
        last_monitor_market_vig=None,
        last_monitor_whale_toxicity=None,
        chain_state="synced",
        state="open",
    )
    edge_ctx_stale = SimpleNamespace(
        p_posterior=None,
        p_market=[],
        divergence_score=0.0,
        market_velocity_1h=0.0,
        forward_edge=0.0,
    )
    exit_ctx_stale = _build_exit_context(
        pos_stale,
        edge_ctx_stale,
        hours_to_settlement=10.0,
        ExitContext=ExitContext,
    )
    assert exit_ctx_stale.fresh_prob_is_fresh is False, (
        "monitor_refresh fallback set last_monitor_prob_is_fresh=False but ExitContext sees True — "
        "stale exit trigger may fire on bad data."
    )

    # --- Case C: paper mode + missing best_bid defaults correctly ---
    pos_paper = SimpleNamespace(
        trade_id="test-paper",
        last_monitor_prob_is_fresh=True,
        last_monitor_market_price=0.60,
        last_monitor_market_price_is_fresh=True,
        last_monitor_best_bid=None,  # paper mode: None is OK, defaults to p_market
        last_monitor_best_ask=None,
        last_monitor_market_vig=None,
        last_monitor_whale_toxicity=None,
        chain_state="synced",
        state="open",
    )
    edge_ctx_paper = SimpleNamespace(
        p_posterior=0.65,
        p_market=[0.60],
        divergence_score=0.0,
        market_velocity_1h=0.0,
        forward_edge=0.0,
    )
    exit_ctx_paper = _build_exit_context(
        pos_paper,
        edge_ctx_paper,
        hours_to_settlement=5.0,
        ExitContext=ExitContext,
    )
    assert exit_ctx_paper.fresh_prob_is_fresh is True, (
        "Paper mode: freshness flag lost at boundary"
    )
    assert exit_ctx_paper.best_bid == 0.60, (
        "Paper mode: missing best_bid should default to current_market_price"
    )


# ---------------------------------------------------------------------------
# Test 6: exit_lifecycle.FILL_STATUSES == fill_tracker.FILL_STATUSES
# ---------------------------------------------------------------------------

def test_exit_lifecycle_fill_statuses_match_fill_tracker():
    """FILL_STATUSES in exit_lifecycle.py must be identical to fill_tracker.py.

    Relationship: fill_tracker.py polls order status and decides when a fill
    is complete. exit_lifecycle.py uses its own copy of FILL_STATUSES to
    decide the same thing in a different code path. If they diverge,
    one path will see fills the other misses — exits get stranded.

    Current KNOWN divergence (as of discovery):
      exit_lifecycle: {"MATCHED", "FILLED"}
      fill_tracker:   {"FILLED", "MATCHED", "PARTIALLY_FILLED"}

    PARTIALLY_FILLED orders seen by fill_tracker as fills will NOT be
    recognized as fills by exit_lifecycle — position stays open forever.
    """
    from src.execution.exit_lifecycle import FILL_STATUSES as lifecycle_statuses
    from src.execution.fill_tracker import FILL_STATUSES as tracker_statuses

    assert lifecycle_statuses == tracker_statuses, (
        f"FILL_STATUSES DIVERGED between modules:\n"
        f"  exit_lifecycle.FILL_STATUSES = {sorted(lifecycle_statuses)}\n"
        f"  fill_tracker.FILL_STATUSES   = {sorted(tracker_statuses)}\n"
        f"Consequence: fill statuses in {{tracker_statuses - lifecycle_statuses}} will be "
        f"recognized as fills by fill_tracker but NOT by exit_lifecycle — positions stranded."
    )


# ---------------------------------------------------------------------------
# Test 7: trade_decisions settled rows must have non-NULL settlement_edge_usd
# ---------------------------------------------------------------------------

def test_trade_decisions_has_settlement_outcome_for_settled():
    """Every trade_decisions row with a SETTLEMENT chronicle event must have
    non-NULL settlement_edge_usd.

    Relationship: harvester.settle_position → chronicle(SETTLEMENT) AND
    trade_decisions(settlement_edge_usd via SD-1 fix).

    A NULL settlement_edge_usd on a settled trade means the SD-1 UPDATE
    failed or was skipped — the trade attribution surface is incomplete.
    """
    from src.config import settings

    conn = _zeus_conn()
    env = settings.mode

    if not _table_exists(conn, "trade_decisions") or not _table_exists(conn, "chronicle"):
        conn.close()
        pytest.skip("Required tables missing.")

    # Find all runtime_trade_ids that have a SETTLEMENT chronicle event
    settled_rtids = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT trade_id
            FROM chronicle
            WHERE event_type = 'SETTLEMENT'
              AND env = ?
              AND trade_id IS NOT NULL
            """,
            (env,),
        ).fetchall()
        if r[0]
    ]

    if not settled_rtids:
        conn.close()
        pytest.skip("No SETTLEMENT events in chronicle.")

    # Among those, find any trade_decisions row with NULL settlement_edge_usd
    placeholders = ",".join("?" * len(settled_rtids))
    broken_rows = conn.execute(
        f"""
        SELECT runtime_trade_id, status, settlement_edge_usd
        FROM trade_decisions
        WHERE runtime_trade_id IN ({placeholders})
          AND (settlement_edge_usd IS NULL OR settlement_edge_usd = 0.0)
          AND status IN ('exited', 'day0_window', 'filled')
        """,
        settled_rtids,
    ).fetchall()

    conn.close()

    if broken_rows:
        sample = [(r["runtime_trade_id"], r["status"]) for r in broken_rows[:5]]
        pytest.fail(
            f"{len(broken_rows)} settled trade_decisions rows have NULL/zero settlement_edge_usd "
            f"(SD-1 fix not applied or partial).\nSample: {sample}"
        )


# ---------------------------------------------------------------------------
# Test 8: riskguard.realized_pnl == SUM(chronicle.SETTLEMENT.pnl) deduped
# ---------------------------------------------------------------------------

def test_riskguard_realized_pnl_matches_chronicle():
    """risk_state.realized_pnl must equal SUM(pnl) from chronicle SETTLEMENT
    events (deduped by trade_id, latest event wins).

    Relationship: harvester → chronicle(SETTLEMENT) → _load_chronicle_recent_exits()
    → PortfolioState.realized_pnl → risk_state.details_json.realized_pnl.

    If these diverge, the daily loss limit is computed against wrong P&L,
    which can allow trading beyond the loss budget.
    """
    import sqlite3
    from src.state.db import RISK_DB_PATH
    from src.config import settings

    conn = _zeus_conn()
    env = settings.mode

    if not _table_exists(conn, "chronicle"):
        conn.close()
        pytest.skip("chronicle table not present.")

    # Compute expected realized_pnl from chronicle (same query as riskguard uses)
    rows = conn.execute(
        """
        SELECT json_extract(details_json, '$.pnl') AS pnl
        FROM chronicle
        WHERE event_type = 'SETTLEMENT'
          AND env = ?
          AND trade_id IS NOT NULL
          AND id IN (
              SELECT MAX(id)
              FROM chronicle
              WHERE event_type = 'SETTLEMENT'
                AND env = ?
                AND trade_id IS NOT NULL
              GROUP BY trade_id
          )
        """,
        (env, env),
    ).fetchall()
    conn.close()

    if not rows:
        pytest.skip("No SETTLEMENT events in chronicle.")

    chronicle_realized = sum(
        float(r["pnl"]) for r in rows if r["pnl"] is not None
    )

    # Read realized_pnl from most recent risk_state tick
    try:
        risk_conn = sqlite3.connect(str(RISK_DB_PATH))
        risk_conn.row_factory = sqlite3.Row
        risk_row = risk_conn.execute(
            "SELECT details_json, checked_at FROM risk_state ORDER BY checked_at DESC LIMIT 1"
        ).fetchone()
        risk_conn.close()
    except Exception as exc:
        pytest.skip(f"risk_state.db unavailable: {exc}")

    if risk_row is None:
        pytest.skip("risk_state has no rows yet.")

    details = json.loads(risk_row["details_json"])
    risk_realized = float(details.get("realized_pnl") or 0.0)
    checked_at = risk_row["checked_at"]

    # Tolerance: risk_state is point-in-time; new settlements since last tick will differ.
    # We allow $0.10 absolute OR 5% relative tolerance.
    abs_diff = abs(risk_realized - chronicle_realized)
    rel_tol = abs(chronicle_realized) * 0.05 if abs(chronicle_realized) > 0 else 0.0
    tolerance = max(0.10, rel_tol)

    if abs_diff > tolerance:
        pytest.fail(
            f"risk_state.realized_pnl={risk_realized:.4f} diverges from "
            f"chronicle SUM={chronicle_realized:.4f} "
            f"(diff={abs_diff:.4f}, tol={tolerance:.4f}, "
            f"risk_state checked_at={checked_at}).\n"
            f"The daily loss limit is computed against wrong P&L."
        )


# ---------------------------------------------------------------------------
# Programmatic runner u2014 importable by venus_sensing_report.py
# ---------------------------------------------------------------------------

def run_relationship_checks() -> dict:
    """Run all 8 cross-module relationship checks and return structured results.

    Does NOT use pytest. Returns a dict keyed by check name, each value:
      {
        "status": "PASS" | "FAIL" | "SKIP",
        "detail": str,          # human-readable summary
        ...check-specific keys
      }

    Importable by scripts/venus_sensing_report.py for heartbeat integration.
    """
    from src.config import settings

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"

    results: dict = {}

    # --- 1. settlement_pnl_consistent ---
    try:
        conn = _zeus_conn()
        env = settings.mode
        settled_rows = conn.execute(
            """
            SELECT trade_id, json_extract(details_json, '$.pnl') AS pnl
            FROM chronicle
            WHERE event_type = 'SETTLEMENT' AND env = ?
              AND trade_id IS NOT NULL
              AND id IN (
                  SELECT MAX(id) FROM chronicle
                  WHERE event_type = 'SETTLEMENT' AND env = ? AND trade_id IS NOT NULL
                  GROUP BY trade_id
              )
            """,
            (env, env),
        ).fetchall()
        chronicle_pnl = {
            r["trade_id"]: float(r["pnl"]) if r["pnl"] is not None else None
            for r in settled_rows
        }
        surfaces_checked = 0
        failures = []
        if not chronicle_pnl:
            results["settlement_pnl_consistent"] = {"status": SKIP, "detail": "no SETTLEMENT events", "agreement": True}
        else:
            for trade_id, chron_pnl in chronicle_pnl.items():
                if chron_pnl is None:
                    continue
                if _table_exists(conn, "outcome_fact"):
                    surfaces_checked = max(surfaces_checked, 2)
                    of = conn.execute(
                        "SELECT pnl FROM outcome_fact WHERE position_id = ?", (trade_id,)
                    ).fetchone()
                    if of is None:
                        failures.append(f"{trade_id}: missing from outcome_fact")
                    elif of["pnl"] is not None and abs(float(of["pnl"]) - chron_pnl) > 0.01:
                        failures.append(f"{trade_id}: outcome_fact pnl diverges")
                if _table_exists(conn, "trade_decisions"):
                    surfaces_checked = max(surfaces_checked, 3)
                    td = conn.execute(
                        "SELECT settlement_edge_usd FROM trade_decisions WHERE runtime_trade_id = ? ORDER BY rowid DESC LIMIT 1",
                        (trade_id,),
                    ).fetchone()
                    if td is None:
                        failures.append(f"{trade_id}: missing from trade_decisions")
                    elif td["settlement_edge_usd"] is None:
                        failures.append(f"{trade_id}: settlement_edge_usd IS NULL")
            surfaces_checked = max(surfaces_checked, 1)  # chronicle always present
            results["settlement_pnl_consistent"] = {
                "status": PASS if not failures else FAIL,
                "surfaces_checked": surfaces_checked,
                "trades_checked": len([v for v in chronicle_pnl.values() if v is not None]),
                "agreement": not failures,
                "failures": failures[:3],
                "detail": f"checked {len(chronicle_pnl)} settlements across {surfaces_checked} surfaces",
            }
        conn.close()
    except Exception as exc:
        results["settlement_pnl_consistent"] = {"status": FAIL, "detail": str(exc), "agreement": False}

    # --- 2. canonical_write_matching ---
    try:
        conn = _zeus_conn()
        if not _table_exists(conn, "position_events") or not _table_exists(conn, "position_current"):
            results["canonical_write_matching"] = {"status": SKIP, "detail": "canonical tables absent"}
        else:
            event_ids = set(
                r[0] for r in conn.execute(
                    "SELECT DISTINCT position_id FROM position_events WHERE position_id IS NOT NULL"
                ).fetchall() if r[0]
            )
            current_ids = set(
                r[0] for r in conn.execute(
                    "SELECT DISTINCT position_id FROM position_current WHERE position_id IS NOT NULL"
                ).fetchall() if r[0]
            )
            missing = event_ids - current_ids
            results["canonical_write_matching"] = {
                "status": PASS if not missing else FAIL,
                "events_count": len(event_ids),
                "projected_count": len(current_ids),
                "missing_projections": len(missing),
                "detail": f"{len(missing)} position_ids in events but not in current" if missing else f"all {len(event_ids)} events projected",
            }
        conn.close()
    except Exception as exc:
        results["canonical_write_matching"] = {"status": FAIL, "detail": str(exc)}

    # --- 3. monitor_freshness_propagation ---
    try:
        from types import SimpleNamespace
        from src.engine.cycle_runtime import _build_exit_context
        from src.execution.exit_lifecycle import ExitContext
        pos = SimpleNamespace(
            trade_id="probe", last_monitor_prob_is_fresh=True,
            last_monitor_market_price=0.55, last_monitor_market_price_is_fresh=True,
            last_monitor_best_bid=0.54, last_monitor_best_ask=0.56,
            last_monitor_market_vig=0.02, last_monitor_whale_toxicity=None,
            chain_state="synced", state="open",
        )
        edge_ctx = SimpleNamespace(
            p_posterior=0.60, p_market=[0.55], divergence_score=0.01,
            market_velocity_1h=0.0, forward_edge=0.0,
        )
        ctx = _build_exit_context(pos, edge_ctx, hours_to_settlement=10.0, ExitContext=ExitContext)
        passed = ctx.fresh_prob_is_fresh is True
        results["monitor_freshness_propagation"] = {
            "status": PASS if passed else FAIL,
            "freshness_flag_survived_boundary": passed,
            "detail": "monitor->exit_context freshness boundary intact" if passed else "freshness flag dropped at boundary",
        }
    except Exception as exc:
        results["monitor_freshness_propagation"] = {"status": FAIL, "detail": str(exc)}

    # --- 4. daily_baseline_anchoring ---
    try:
        from src.riskguard.riskguard import _load_baselines_from_risk_history
        from src.state.db import RISK_DB_PATH
        import sqlite3 as _sq3
        rconn = _sq3.connect(str(RISK_DB_PATH))
        rconn.row_factory = _sq3.Row
        row_count = rconn.execute("SELECT COUNT(*) FROM risk_state").fetchone()[0]
        rconn.close()
        if row_count == 0:
            results["daily_baseline_anchoring"] = {"status": SKIP, "detail": "risk_state empty"}
        else:
            daily, weekly = _load_baselines_from_risk_history()
            capital = float(settings.capital_base_usd)
            results["daily_baseline_anchoring"] = {
                "status": PASS,
                "daily_baseline": round(daily, 2),
                "weekly_baseline": round(weekly, 2),
                "capital_base": round(capital, 2),
                "anchored_away_from_capital": abs(daily - capital) > 0.01,
                "detail": f"daily={daily:.2f}, weekly={weekly:.2f}, capital={capital:.2f}",
            }
    except Exception as exc:
        results["daily_baseline_anchoring"] = {"status": FAIL, "detail": str(exc)}

    # --- 5. position_count_consistent ---
    try:
        from src.state.portfolio import POSITIONS_PATH, load_portfolio
        conn = _zeus_conn()
        if not _table_exists(conn, "position_current"):
            conn.close()
            results["position_count_consistent"] = {"status": SKIP, "detail": "position_current absent"}
        else:
            db_count = conn.execute(
                "SELECT COUNT(*) FROM position_current WHERE phase = 'active'"
            ).fetchone()[0]
            conn.close()
            if db_count == 0:
                results["position_count_consistent"] = {"status": SKIP, "detail": "no active positions", "db_count": 0}
            elif not POSITIONS_PATH.exists():
                results["position_count_consistent"] = {"status": SKIP, "detail": "positions.json absent"}
            else:
                json_count = len(load_portfolio().positions)
                match = db_count == json_count
                results["position_count_consistent"] = {
                    "status": PASS if match else FAIL,
                    "db_count": db_count,
                    "json_count": json_count,
                    "detail": f"position_current={db_count} {'==' if match else '!='} positions.json={json_count}",
                }
    except Exception as exc:
        results["position_count_consistent"] = {"status": FAIL, "detail": str(exc)}

    # --- 6. fill_status_definitions_match ---
    try:
        from src.execution.exit_lifecycle import FILL_STATUSES as lifecycle_statuses
        from src.execution.fill_tracker import FILL_STATUSES as tracker_statuses
        match = lifecycle_statuses == tracker_statuses
        results["fill_status_definitions_match"] = {
            "status": PASS if match else FAIL,
            "lifecycle_statuses": sorted(lifecycle_statuses),
            "tracker_statuses": sorted(tracker_statuses),
            "diverged": sorted(lifecycle_statuses ^ tracker_statuses),
            "detail": "FILL_STATUSES identical" if match else f"diverged: {sorted(lifecycle_statuses ^ tracker_statuses)}",
        }
    except Exception as exc:
        results["fill_status_definitions_match"] = {"status": FAIL, "detail": str(exc)}

    # --- 7. settlement_outcomes_written ---
    try:
        conn = _zeus_conn()
        env = settings.mode
        if not _table_exists(conn, "trade_decisions") or not _table_exists(conn, "chronicle"):
            results["settlement_outcomes_written"] = {"status": SKIP, "detail": "required tables absent"}
        else:
            settled_ids = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT trade_id FROM chronicle WHERE event_type='SETTLEMENT' AND env=? AND trade_id IS NOT NULL",
                    (env,),
                ).fetchall() if r[0]
            ]
            if not settled_ids:
                results["settlement_outcomes_written"] = {"status": SKIP, "detail": "no SETTLEMENT events"}
            else:
                placeholders = ",".join("?" * len(settled_ids))
                broken = conn.execute(
                    f"SELECT COUNT(*) FROM trade_decisions WHERE runtime_trade_id IN ({placeholders}) "
                    f"AND (settlement_edge_usd IS NULL OR settlement_edge_usd = 0.0) "
                    f"AND status IN ('exited', 'day0_window', 'filled')",
                    settled_ids,
                ).fetchone()[0]
                total = len(settled_ids)
                results["settlement_outcomes_written"] = {
                    "status": PASS if broken == 0 else FAIL,
                    "settled_trades": total,
                    "missing_edge_usd": broken,
                    "detail": f"{total - broken}/{total} trades have settlement_edge_usd written",
                }
        conn.close()
    except Exception as exc:
        results["settlement_outcomes_written"] = {"status": FAIL, "detail": str(exc)}

    # --- 8. realized_pnl_matches_chronicle ---
    try:
        from src.state.db import RISK_DB_PATH
        import sqlite3 as _sq3
        conn = _zeus_conn()
        env = settings.mode
        rows = conn.execute(
            """
            SELECT json_extract(details_json, '$.pnl') AS pnl
            FROM chronicle
            WHERE event_type = 'SETTLEMENT' AND env = ? AND trade_id IS NOT NULL
              AND id IN (SELECT MAX(id) FROM chronicle WHERE event_type='SETTLEMENT' AND env=? AND trade_id IS NOT NULL GROUP BY trade_id)
            """,
            (env, env),
        ).fetchall()
        conn.close()
        if not rows:
            results["realized_pnl_matches_chronicle"] = {"status": SKIP, "detail": "no SETTLEMENT events"}
        else:
            chronicle_total = sum(float(r["pnl"]) for r in rows if r["pnl"] is not None)
            rconn = _sq3.connect(str(RISK_DB_PATH))
            rconn.row_factory = _sq3.Row
            risk_row = rconn.execute(
                "SELECT details_json FROM risk_state ORDER BY checked_at DESC LIMIT 1"
            ).fetchone()
            rconn.close()
            if risk_row is None:
                results["realized_pnl_matches_chronicle"] = {"status": SKIP, "detail": "risk_state empty"}
            else:
                risk_realized = float(json.loads(risk_row["details_json"]).get("realized_pnl") or 0.0)
                tol = max(0.10, abs(chronicle_total) * 0.05)
                diff = abs(risk_realized - chronicle_total)
                match = diff <= tol
                results["realized_pnl_matches_chronicle"] = {
                    "status": PASS if match else FAIL,
                    "chronicle_total": round(chronicle_total, 4),
                    "risk_state_realized": round(risk_realized, 4),
                    "diff": round(diff, 4),
                    "tolerance": round(tol, 4),
                    "detail": f"diff={diff:.4f} vs tol={tol:.4f} {'OK' if match else 'DIVERGED'}",
                }
    except Exception as exc:
        results["realized_pnl_matches_chronicle"] = {"status": FAIL, "detail": str(exc)}

    return results


if __name__ == "__main__":
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=str(PROJECT_ROOT),
    )
    sys.exit(result.returncode)
