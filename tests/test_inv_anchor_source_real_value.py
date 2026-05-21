# Created: 2026-05-20
# Last reused or audited: 2026-05-21
# Authority basis: PHASE_2_ULTRAPLAN.md v3.1 §7.5 (sha 00c2399742)
"""Antibody test: INV-anchor-source-real-value

Invariant: post-T4-merge, zero settlement_commands.polymarket_end_anchor_source
= 'unknown_legacy' rows for commands created after T4_MERGE_DATE.

Cross-module relationship test:
  src/strategy/market_phase.market_end_anchor_source(market)
  → src/contracts/execution_intent.py from_forecast_context() :~673
  → src/execution/settlement_commands.py polymarket_end_anchor_source column

Why settlement_commands, NOT decision_events:
  decision_events.polymarket_end_anchor_source has a DB CHECK constraint
  IN ('gamma_explicit','f1_12z_fallback') (db.py:1338-1339).  'unknown_legacy'
  can NEVER be stored there — querying decision_events for 'unknown_legacy'
  is a permanent tautological 0, not a meaningful antibody.

  settlement_commands.polymarket_end_anchor_source is the actual carrier of
  the 'unknown_legacy' sentinel (settlement_commands.py:238 DEFAULT + :483
  fallback via `polymarket_end_anchor_source or "unknown_legacy"`).
  The T4 wire-up replaces that fallback with a computed real value, so
  post-merge rows must carry 'gamma_explicit' or 'f1_12z_fallback'.

Grep evidence (re-derived against git show origin/main):
  settlement_commands.py:238 — ALTER TABLE ... DEFAULT 'unknown_legacy'
  settlement_commands.py:283 — UPDATE backfill → 'unknown_legacy'
  settlement_commands.py:482 — `polymarket_end_anchor_source or "unknown_legacy"`
  db.py:1338-1339 — decision_events CHECK IN ('gamma_explicit','f1_12z_fallback')

SCAFFOLD status: xfail because settlement_commands writer still falls back to
'unknown_legacy' via `polymarket_end_anchor_source or "unknown_legacy"` (line
482) until market_end_anchor_source(market) is wired at execution_intent.py:~673.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T4 production pending; settlement_commands.polymarket_end_anchor_source='unknown_legacy' "
        "expected until market_end_anchor_source() wired at execution_intent.py:~673 (SCAFFOLD)"
    ),
)
def test_inv_anchor_source_real_value() -> None:
    """INV-anchor-source-real-value: zero 'unknown_legacy' rows in
    settlement_commands for commands created after T4_MERGE_DATE.

    Trade-DB read path only (settlement_commands on zeus_trades.db). No ATTACH.
    Skips when trade DB absent or when T4_MERGE_DATE is still the placeholder.

    SCAFFOLD: fires xfail because the writer still falls back to 'unknown_legacy'
    via `polymarket_end_anchor_source or "unknown_legacy"` in settlement_commands.py:482.

    Production assertion (activated in T4 production pass when T4_MERGE_DATE
    is set to the real merge ISO timestamp):
      COUNT(*) of 'unknown_legacy' rows after T4_MERGE_DATE must be 0.
    """
    from src.analysis.market_analysis_vnext import T4_MERGE_DATE
    from src.state.db import _zeus_trade_db_path

    # Explicit guard: placeholder date means the antibody is inert by design.
    # Production pass sets T4_MERGE_DATE to git log --format=%cI -1 origin/main.
    if T4_MERGE_DATE == "2026-05-XX":
        pytest.skip(
            "T4_MERGE_DATE is placeholder '2026-05-XX' — set at T4 production merge. "
            "Antibody intentionally inert until real merge date is committed."
        )

    trade_db_path = _zeus_trade_db_path()
    try:
        conn = sqlite3.connect(f"file:{trade_db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        pytest.skip("trade DB not present in this environment — live-only antibody")
    conn.row_factory = sqlite3.Row

    try:
        # Rows with 'unknown_legacy' anchor source created after T4 merge
        legacy_count = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM settlement_commands
            WHERE requested_at >= ?
              AND polymarket_end_anchor_source = 'unknown_legacy'
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]

        total_rows = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM settlement_commands
            WHERE requested_at >= ?
            """,
            (T4_MERGE_DATE,),
        ).fetchone()["cnt"]

        if total_rows == 0:
            pytest.skip(
                f"No settlement_commands rows after T4_MERGE_DATE={T4_MERGE_DATE!r} — "
                "no live redemptions yet post-merge; antibody cannot fire."
            )

        assert legacy_count == 0, (
            f"INV-anchor-source-real-value: {legacy_count} of {total_rows} "
            f"settlement_commands rows after T4_MERGE_DATE={T4_MERGE_DATE!r} "
            "still carry polymarket_end_anchor_source='unknown_legacy'. "
            "market_end_anchor_source() wire-up in execution_intent.py is not active."
        )
    finally:
        conn.close()
