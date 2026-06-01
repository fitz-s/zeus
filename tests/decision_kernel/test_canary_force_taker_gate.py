# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: EDLI_EXECUTION_STRATEGY_DESIGN_2026_05_31.md §4 item 7 (canary
#   knob: FORCE taker while live_canary_enabled AND canary fill count < min).
"""Relationship test for the runtime canary force-taker count-gate.

The design §4 item 7 requires the canary to FORCE the taker branch ONLY while
the live-canary stage is enabled AND the proven canary fill count is below
``edli_live_min_canary_count``. Once min fills land, the gate must RELEASE so
order-type selection reverts to the governor + EV boundary (§1-§2).

This is the cross-module invariant between the world-DB EDLI live-order audit
(``edli_live_profit_audit``, the fill-count truth source) and the per-cycle
force-taker decision wired in ``src.main._edli_canary_force_taker_provider``:

  "as the confirmed canary fill count crosses ``min_canary_count``, the
   force-taker gate flips True -> False."

Plus the conservative defaults:
  - canary disabled  -> gate is always False (never force taker);
  - count unreadable -> gate fails OPEN to force-taker (canary not yet proven).
"""
from __future__ import annotations

import sqlite3

from src.main import _edli_canary_force_taker_provider
from src.state.schema.edli_live_profit_audit_schema import ensure_table as _ensure_audit_table


def _world_conn_with_audit() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _ensure_audit_table(conn)
    return conn


def _insert_confirmed_fill(conn: sqlite3.Connection, *, audit_id: str, aggregate_id: str) -> None:
    """Insert one CONFIRMED + promotion_eligible audit row (counts toward fills)."""
    conn.execute(
        """
        INSERT INTO edli_live_profit_audit (
            audit_id, event_id, aggregate_id, condition_id, token_id,
            order_lifecycle_state, promotion_eligible, realized_edge,
            created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, 'CONFIRMED', 1, 0.01, '2026-05-31T00:00:00Z', 1)
        """,
        (audit_id, f"evt-{audit_id}", aggregate_id, "cond-1", "yes-1"),
    )
    conn.commit()


def test_gate_disabled_when_canary_off():
    conn = _world_conn_with_audit()
    provider = _edli_canary_force_taker_provider(conn, {"live_canary_enabled": False})
    assert provider() is False


def test_gate_forces_taker_below_min_fills():
    conn = _world_conn_with_audit()
    # 0 confirmed fills, min=1 -> below min -> force taker.
    provider = _edli_canary_force_taker_provider(
        conn, {"live_canary_enabled": True, "edli_live_min_canary_count": 1}
    )
    assert provider() is True


def test_gate_releases_at_min_fills():
    conn = _world_conn_with_audit()
    _insert_confirmed_fill(conn, audit_id="a1", aggregate_id="agg-1")
    # 1 confirmed fill, min=1 -> count >= min -> release (governor/EV takes over).
    provider = _edli_canary_force_taker_provider(
        conn, {"live_canary_enabled": True, "edli_live_min_canary_count": 1}
    )
    assert provider() is False


def test_gate_flips_as_count_crosses_min():
    """The load-bearing relationship: gate is True until fills reach min, then False."""
    conn = _world_conn_with_audit()
    cfg = {"live_canary_enabled": True, "edli_live_min_canary_count": 3}
    provider = _edli_canary_force_taker_provider(conn, cfg)

    assert provider() is True  # 0 < 3
    _insert_confirmed_fill(conn, audit_id="a1", aggregate_id="agg-1")
    assert provider() is True  # 1 < 3
    _insert_confirmed_fill(conn, audit_id="a2", aggregate_id="agg-2")
    assert provider() is True  # 2 < 3
    _insert_confirmed_fill(conn, audit_id="a3", aggregate_id="agg-3")
    assert provider() is False  # 3 >= 3 -> released


def test_gate_fails_open_when_count_unreadable():
    """No audit table (canary genesis) -> count unavailable -> force the FOK proof."""
    conn = sqlite3.connect(":memory:")  # NO edli_live_profit_audit table
    conn.row_factory = sqlite3.Row
    provider = _edli_canary_force_taker_provider(
        conn, {"live_canary_enabled": True, "edli_live_min_canary_count": 1}
    )
    # _canonical_promotion_rows returns 0 rows when the table is absent -> 0 < 1
    # -> force taker. (If the read itself errored, the except branch also forces.)
    assert provider() is True
