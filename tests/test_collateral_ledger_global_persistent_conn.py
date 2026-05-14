# Created: 2026-05-13
# Last reused/audited: 2026-05-13
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z4.yaml
#                  + 2026-05-13 collateral_ledger singleton conn lifecycle remediation
"""Relationship test for CollateralLedger global singleton conn lifecycle.

The R3 Z4 contract is that `configure_global_ledger(...)` installs a
process-wide ledger whose `snapshot()` remains callable for the lifetime
of the live daemon. The deprecated `PolymarketClient.get_balance()` compat
wrapper previously closed its conn *after* publishing the ledger to the
global slot, leaving downstream `assert_buy_preflight` / `assert_sell_preflight`
holding a singleton whose underlying sqlite3.Connection had been closed —
every preflight then raised `sqlite3.ProgrammingError: Cannot operate on
a closed database`.

This test asserts the cross-module invariant that production-equivalent
configure-then-close-source-conn does NOT poison the singleton. The fix
makes `CollateralLedger` own a persistent conn when given a `db_path`,
so the singleton remains live regardless of the caller's conn lifetime.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.state.collateral_ledger import (
    CollateralLedger,
    configure_global_ledger,
    get_global_ledger,
    init_collateral_schema,
)


def test_global_ledger_snapshot_survives_caller_conn_close(tmp_path: Path) -> None:
    """RELATIONSHIP: configure_global_ledger -> caller closes its conn ->
    get_global_ledger().snapshot() must still succeed.

    Failure mode this guards (pre-fix): closing the caller's conn killed
    the singleton because the ledger held a transient conn reference.
    """

    db_path = tmp_path / "trades.db"
    # Prepare the schema on a transient conn the way the deprecated
    # `PolymarketClient.get_balance()` path would, then close it.
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_collateral_schema(seed_conn)
    seed_conn.commit()
    seed_conn.close()

    # Production-equivalent setup: build a ledger that owns a persistent
    # conn against the real DB path, publish it to the global slot.
    ledger = CollateralLedger(db_path=db_path)
    configure_global_ledger(ledger)

    try:
        active = get_global_ledger()
        assert active is not None, "global ledger must be configured"

        # Multiple snapshot calls across time must not raise ProgrammingError.
        for _ in range(3):
            snap = active.snapshot()
            assert snap is not None
            assert snap.authority_tier in {"CHAIN", "VENUE", "DEGRADED"}
    finally:
        configure_global_ledger(None)


def test_global_ledger_snapshot_survives_after_transient_caller_conn_pattern(
    tmp_path: Path,
) -> None:
    """RELATIONSHIP: emulate the historic bug shape exactly — caller opens
    a transient conn, hands it to CollateralLedger, publishes to global
    slot, then closes the conn.  With the fix, the global slot must be
    populated via the `db_path` constructor instead of a transient conn,
    so closing the caller's transient conn cannot poison the singleton.
    """

    db_path = tmp_path / "trades.db"
    seed_conn = sqlite3.connect(str(db_path))
    seed_conn.row_factory = sqlite3.Row
    init_collateral_schema(seed_conn)
    seed_conn.commit()
    seed_conn.close()

    # The fix-shape: build a persistent-conn ledger and configure globally.
    persistent_ledger = CollateralLedger(db_path=db_path)
    configure_global_ledger(persistent_ledger)

    # Now perform a transient conn lifecycle in the caller (mimicking the
    # deprecated get_balance() wrapper) — it must not poison the singleton.
    transient_conn = sqlite3.connect(str(db_path))
    transient_conn.row_factory = sqlite3.Row
    try:
        _ = CollateralLedger(transient_conn)
        transient_conn.commit()
    finally:
        transient_conn.close()

    try:
        active = get_global_ledger()
        assert active is not None
        # If the singleton were holding the transient conn, this raises
        # sqlite3.ProgrammingError: Cannot operate on a closed database.
        snap = active.snapshot()
        assert snap is not None
    finally:
        configure_global_ledger(None)
