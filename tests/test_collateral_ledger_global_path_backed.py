# Created: 2026-05-13
# Last reused/audited: 2026-05-15
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/Z4.yaml
#                  + 2026-05-13 collateral_ledger singleton lifecycle remediation
#                  + 2026-06-17 path-backed short-connection live repair
"""Relationship test for CollateralLedger global singleton DB-path lifecycle.

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
makes `CollateralLedger` own a durable `db_path` and open short-lived
connections per DB operation, so the singleton remains live regardless of the
caller conn lifetime without holding the trade DB write lane open.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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

    # Production-equivalent setup: build a path-backed ledger against the real
    # DB path, publish it to the global slot.
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

    # The fix-shape: build a path-backed ledger and configure globally.
    path_backed_ledger = CollateralLedger(db_path=db_path)
    configure_global_ledger(path_backed_ledger)

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


def test_path_backed_ledger_refresh_commits_for_fresh_readers(
    tmp_path: Path,
) -> None:
    """RELATIONSHIP: global ledger refresh -> fresh DB reader must see it.

    The live daemon keeps a process-wide ledger with a durable DB path.
    Heartbeat refreshes must become canonical DB truth immediately; otherwise
    the daemon's in-memory collateral view diverges from fresh read-only
    verifier / executor ledger instances.
    """

    db_path = tmp_path / "trades.db"
    ledger = CollateralLedger(db_path=db_path)

    class Adapter:
        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 199_396_602,
                "pusd_allowance_micro": 9_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

    try:
        snapshot = ledger.refresh(Adapter())
        assert snapshot.pusd_allowance_micro == 9_000_000

        fresh_conn = sqlite3.connect(str(db_path))
        fresh_conn.row_factory = sqlite3.Row
        try:
            fresh = CollateralLedger(fresh_conn).snapshot()
            assert fresh.pusd_balance_micro == 199_396_602
            assert fresh.pusd_allowance_micro == 9_000_000
            assert fresh.authority_tier == "CHAIN"
        finally:
            fresh_conn.close()
    finally:
        ledger.close()


def test_path_backed_ledger_does_not_hold_write_lock_between_calls(
    tmp_path: Path,
) -> None:
    """A global collateral ledger must not park a trade-DB writer between calls."""

    db_path = tmp_path / "trades.db"
    ledger = CollateralLedger(db_path=db_path)

    class Adapter:
        def get_collateral_payload(self):
            return {
                "pusd_balance_micro": 199_396_602,
                "pusd_allowance_micro": 9_000_000,
                "usdc_e_legacy_balance_micro": 0,
                "ctf_token_balances": {},
                "ctf_token_allowances": {},
                "authority_tier": "CHAIN",
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }

    try:
        ledger.refresh(Adapter())

        writer = sqlite3.connect(str(db_path), timeout=0.1)
        try:
            writer.execute("PRAGMA busy_timeout=100")
            writer.execute("BEGIN IMMEDIATE")
            writer.rollback()
        finally:
            writer.close()
    finally:
        ledger.close()


def test_path_backed_ledger_reads_fresh_chain_snapshot_past_latest_degraded(
    tmp_path: Path,
) -> None:
    """A transient degraded refresh must not poison live bankroll readers."""

    db_path = tmp_path / "trades.db"
    ledger = CollateralLedger(db_path=db_path)
    try:
        ledger.set_snapshot(
            _snapshot(
                pusd=201_000_000,
                allowance=900_000_000,
                authority="CHAIN",
                token_balances={"tok-live": 5_000_000},
            )
        )
        ledger.set_snapshot(
            _snapshot(
                pusd=0,
                allowance=0,
                authority="DEGRADED",
                token_balances={},
            )
        )

        fresh = ledger.snapshot()

        assert fresh.authority_tier == "CHAIN"
        assert fresh.pusd_balance_micro == 201_000_000
        assert fresh.ctf_token_balances == {"tok-live": 5_000_000}
    finally:
        ledger.close()


def test_path_backed_ledger_does_not_let_empty_ctf_tick_override_active_exposure(
    tmp_path: Path,
) -> None:
    """When chain-synced positions still exist, keep the latest non-empty CTF map."""

    db_path = tmp_path / "trades.db"
    ledger = CollateralLedger(db_path=db_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE position_current (
              position_id TEXT PRIMARY KEY,
              phase TEXT,
              shares REAL,
              chain_shares REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('pos-1', 'active', 5.0, 5.0)"
        )
        conn.commit()
    finally:
        conn.close()

    try:
        ledger.set_snapshot(
            _snapshot(
                pusd=201_000_000,
                allowance=900_000_000,
                authority="CHAIN",
                token_balances={"tok-live": 5_000_000},
            )
        )
        ledger.set_snapshot(
            _snapshot(
                pusd=202_000_000,
                allowance=900_000_000,
                authority="CHAIN",
                token_balances={},
            )
        )

        fresh = ledger.snapshot()

        assert fresh.authority_tier == "CHAIN"
        assert fresh.ctf_token_balances == {"tok-live": 5_000_000}
    finally:
        ledger.close()


def _snapshot(
    *,
    pusd: int,
    allowance: int,
    authority: str,
    token_balances: dict[str, int],
):
    from src.state.collateral_ledger import CollateralSnapshot

    return CollateralSnapshot(
        pusd_balance_micro=pusd,
        pusd_allowance_micro=allowance,
        usdc_e_legacy_balance_micro=0,
        ctf_token_balances=token_balances,
        ctf_token_allowances=dict(token_balances),
        reserved_pusd_for_buys_micro=0,
        reserved_tokens_for_sells={},
        captured_at=datetime.now(timezone.utc),
        authority_tier=authority,  # type: ignore[arg-type]
    )
