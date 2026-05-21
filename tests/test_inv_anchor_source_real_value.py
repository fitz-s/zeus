# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7.5 (sha 00c2399742)
"""Antibody test: INV-anchor-source-real-value

Invariant: post-T4, zero polymarket_end_anchor_source = 'unknown_legacy' rows
for decisions made after T4_MERGE_DATE.

After T4 production pass wires market_end_anchor_source(market) into
execution_intent.py:~673, the static 'unknown_legacy' default is replaced by
a computed value ('gamma_explicit' or 'f1_12z_fallback'). No fresh decision
row should carry 'unknown_legacy' post-merge.

Cross-module relationship test:
  src/strategy/market_phase.market_end_anchor_source()
  → src/contracts/execution_intent.py from_forecast_context()
  → world.decision_events.polymarket_end_anchor_source

SCAFFOLD status: xfail because execution_intent.py:~673 still passes
context.get("polymarket_end_anchor_source") (returning 'unknown_legacy' default
for callers that don't populate the context key). The antibody fires when
the world DB is present and has fresh decision_events rows.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T4 production pending; polymarket_end_anchor_source='unknown_legacy' rows "
        "expected until market_end_anchor_source() wired at execution_intent.py:~673 (SCAFFOLD)"
    ),
)
def test_inv_anchor_source_real_value() -> None:
    """INV-anchor-source-real-value: zero 'unknown_legacy' rows in decision_events
    for decisions made after T4_MERGE_DATE.

    World-DB read path only (decision_events on world DB). No ATTACH (INV-37).
    Skips when world DB absent or has zero rows after T4_MERGE_DATE.

    SCAFFOLD: fires xfail because current writer still uses static
    context.get("polymarket_end_anchor_source") → '' → stored as '' or
    'unknown_legacy' for legacy callers.

    Production assertion (activated in T4 production pass):
      Count of 'unknown_legacy' rows after T4_MERGE_DATE must be 0.
    """
    from src.analysis.market_analysis_vnext import T4_MERGE_DATE
    from src.state.db import ZEUS_WORLD_DB_PATH

    try:
        conn = sqlite3.connect(f"file:{ZEUS_WORLD_DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        pytest.skip("world DB not present in this environment — live-only antibody")
    conn.row_factory = sqlite3.Row

    try:
        # Total rows in window
        total_rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM decision_events
            WHERE decision_time >= ?
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]

        if total_rows == 0:
            pytest.skip(
                f"No decision_events rows after T4_MERGE_DATE={T4_MERGE_DATE!r} — "
                "SCAFFOLD placeholder date; antibody activates after T4 production merge."
            )

        # Rows with 'unknown_legacy' anchor source post-merge-date
        legacy_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM decision_events
            WHERE decision_time >= ?
              AND polymarket_end_anchor_source = 'unknown_legacy'
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]

        assert legacy_count == 0, (
            f"INV-anchor-source-real-value: {legacy_count} of {total_rows} decision_events "
            f"rows after T4_MERGE_DATE={T4_MERGE_DATE!r} still have "
            "polymarket_end_anchor_source='unknown_legacy'. "
            "market_end_anchor_source() wire-up in execution_intent.py is not active."
        )
    finally:
        conn.close()
