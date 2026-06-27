# Created: 2026-06-26
# Last reused or audited: 2026-06-26
# Authority basis: docs/operations/current/reports/runtime_db_lock_refactor_design_2026-06-26.md
# Lifecycle: created=2026-06-26; last_reviewed=2026-06-26; last_reused=never
# Purpose: Runtime DB write coordinator skeleton antibodies: unified same-file
#   LIVE/BULK writer gate, canonical multi-DB lease order, and single-DB
#   BEGIN IMMEDIATE commit/rollback telemetry.
# Reuse: Run on every PR touching src/state/write_coordinator.py or migrating
#   runtime DB writers onto the new coordinator.

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from src.state.db_writer_lock import WriteClass
from src.state.write_coordinator import (
    CrossDatabaseTransactionUnsupported,
    DBIdentity,
    WriteCoordinator,
    WriteLeaseTelemetry,
    WriteLeaseTimeout,
    unified_writer_lock_path,
)


def _db_paths(tmp_path: Path) -> dict[DBIdentity, Path]:
    return {
        DBIdentity.FORECAST: tmp_path / "zeus-forecasts.db",
        DBIdentity.TRADE: tmp_path / "zeus_trades.db",
        DBIdentity.WORLD: tmp_path / "zeus-world.db",
    }


def test_live_and_bulk_share_same_file_gate(tmp_path: Path) -> None:
    telemetry: list[WriteLeaseTelemetry] = []
    coordinator = WriteCoordinator(_db_paths(tmp_path), telemetry_sink=telemetry.append)

    with coordinator.lease(
        (DBIdentity.WORLD,),
        owner="bulk-backfill",
        write_class=WriteClass.BULK,
    ):
        with pytest.raises(WriteLeaseTimeout):
            with coordinator.lease(
                (DBIdentity.WORLD,),
                owner="live-cycle",
                write_class=WriteClass.LIVE,
                deadline_ms=20,
            ):
                raise AssertionError("live lease must not bypass held bulk gate")

    timeout_rows = [row for row in telemetry if row.owner == "live-cycle"]
    assert len(timeout_rows) == 1
    assert timeout_rows[0].deadline_exceeded is True
    assert timeout_rows[0].db_set == ("world",)
    assert unified_writer_lock_path(tmp_path / "zeus-world.db").exists()
    assert not (tmp_path / "zeus-world.db.writer-lock.live").exists()
    assert not (tmp_path / "zeus-world.db.writer-lock.bulk").exists()


def test_cross_db_leases_use_canonical_order_without_deadlock(tmp_path: Path) -> None:
    telemetry: list[WriteLeaseTelemetry] = []
    coordinator = WriteCoordinator(_db_paths(tmp_path), telemetry_sink=telemetry.append)
    expected_order = coordinator.canonical_db_order(
        (DBIdentity.WORLD, DBIdentity.TRADE, DBIdentity.FORECAST)
    )
    barrier = threading.Barrier(3)
    completed: list[str] = []
    errors: list[BaseException] = []

    def _worker(name: str, dbs: tuple[DBIdentity, ...]) -> None:
        try:
            barrier.wait(timeout=1.0)
            with coordinator.lease(dbs, owner=name, deadline_ms=1000) as lease:
                assert lease.db_set == expected_order
                time.sleep(0.02)
            completed.append(name)
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    first = threading.Thread(
        target=_worker,
        args=("world-first", (DBIdentity.WORLD, DBIdentity.TRADE, DBIdentity.FORECAST)),
    )
    second = threading.Thread(
        target=_worker,
        args=("forecast-first", (DBIdentity.FORECAST, DBIdentity.TRADE, DBIdentity.WORLD)),
    )
    first.start()
    second.start()
    barrier.wait(timeout=1.0)
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert sorted(completed) == ["forecast-first", "world-first"]
    assert {row.db_set for row in telemetry} == {
        tuple(db.value for db in expected_order),
    }


def test_single_db_transaction_commits_with_begin_immediate_telemetry(
    tmp_path: Path,
) -> None:
    telemetry: list[WriteLeaseTelemetry] = []
    coordinator = WriteCoordinator(_db_paths(tmp_path), telemetry_sink=telemetry.append)

    with coordinator.transaction((DBIdentity.WORLD,), owner="unit-test") as tx:
        tx.connection.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)")
        tx.connection.execute("INSERT INTO item (name) VALUES (?)", ("kept",))

    with sqlite3.connect(tmp_path / "zeus-world.db") as conn:
        row = conn.execute("SELECT name FROM item").fetchone()

    assert row == ("kept",)
    assert len(telemetry) == 1
    assert telemetry[0].owner == "unit-test"
    assert telemetry[0].write_class == "live"
    assert telemetry[0].rows_changed == 1
    assert telemetry[0].commit_ms >= 0.0
    assert telemetry[0].deadline_exceeded is False
    assert telemetry[0].error is None


def test_single_db_transaction_rolls_back_on_exception(tmp_path: Path) -> None:
    telemetry: list[WriteLeaseTelemetry] = []
    db_path = tmp_path / "zeus-world.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)")

    coordinator = WriteCoordinator(_db_paths(tmp_path), telemetry_sink=telemetry.append)

    with pytest.raises(RuntimeError):
        with coordinator.transaction((DBIdentity.WORLD,), owner="rollback-test") as tx:
            tx.connection.execute("INSERT INTO item (name) VALUES (?)", ("dropped",))
            raise RuntimeError("force rollback")

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM item").fetchone()[0]

    assert count == 0
    assert len(telemetry) == 1
    assert telemetry[0].error == "RuntimeError"
    assert telemetry[0].rows_changed is None


def test_multi_db_transaction_is_rejected_instead_of_faked(tmp_path: Path) -> None:
    coordinator = WriteCoordinator(_db_paths(tmp_path))

    with pytest.raises(CrossDatabaseTransactionUnsupported):
        with coordinator.transaction(
            (DBIdentity.WORLD, DBIdentity.TRADE),
            owner="bad-cross-db",
        ):
            raise AssertionError("multi-DB independent transaction must not open")
