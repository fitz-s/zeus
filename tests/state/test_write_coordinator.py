# Created: 2026-06-26
# Last reused or audited: 2026-08-11
# Authority basis: docs/operations/current/reports/runtime_db_lock_refactor_design_2026-06-26.md
# Lifecycle: created=2026-06-26; last_reviewed=2026-08-11; last_reused=2026-08-11
# Purpose: Runtime DB write coordinator skeleton antibodies: unified same-file
#   LIVE/BULK writer gate, canonical multi-DB lease order, and single-DB
#   BEGIN IMMEDIATE commit/rollback telemetry.
# Reuse: Run on every PR touching src/state/write_coordinator.py or migrating
#   runtime DB writers onto the new coordinator.

from __future__ import annotations

import fcntl
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.state.db_writer_lock import WriteClass
from src.state.write_coordinator import (
    CrossDatabaseTransactionUnsupported,
    DBIdentity,
    WriteCoordinator,
    WriteLeaseTelemetry,
    WriteLeaseTimeout,
    WritePriority,
    bounded_sqlite_write,
    unified_writer_lock_path,
    writer_monitor_waiter_path,
)


def _db_paths(tmp_path: Path) -> dict[DBIdentity, Path]:
    return {
        DBIdentity.FORECAST: tmp_path / "zeus-forecasts.db",
        DBIdentity.TRADE: tmp_path / "zeus_trades.db",
        DBIdentity.WORLD: tmp_path / "zeus-world.db",
    }


def _exclusive_flock_visible(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def test_bounded_sqlite_write_caps_raw_writer_wait_and_restores_timeout(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bounded-write.db"
    with sqlite3.connect(path) as setup:
        setup.execute("CREATE TABLE facts (value TEXT PRIMARY KEY)")
    coordinator = WriteCoordinator({DBIdentity.TRADE: path})
    holder = sqlite3.connect(path)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO facts VALUES ('raw-holder')")
    writer = sqlite3.connect(path, timeout=30.0)
    writer.execute("PRAGMA busy_timeout=30000")
    started = time.monotonic()
    try:
        with pytest.raises(WriteLeaseTimeout, match="within hold budget"):
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="bounded-antibody",
                max_hold_ms=80,
            ) as lease:
                with bounded_sqlite_write(writer, lease, max_hold_ms=80):
                    writer.execute("INSERT INTO facts VALUES ('must-not-persist')")
        assert time.monotonic() - started < 0.25
        assert writer.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
    finally:
        writer.close()
        holder.rollback()
        holder.close()


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


def test_background_holder_can_observe_new_monitor_waiter(tmp_path: Path) -> None:
    coordinator = WriteCoordinator(_db_paths(tmp_path))
    waiter_started = threading.Event()
    waiter_acquired = threading.Event()

    def _monitor_waiter() -> None:
        waiter_started.set()
        with coordinator.lease(
            (DBIdentity.TRADE,),
            owner="monitor",
            priority=WritePriority.MONITOR,
            deadline_ms=1_000,
        ):
            waiter_acquired.set()

    with coordinator.lease(
        (DBIdentity.TRADE,),
        owner="background",
        priority=WritePriority.BACKGROUND_RECOVERY,
    ):
        waiter = threading.Thread(target=_monitor_waiter)
        waiter.start()
        assert waiter_started.wait(timeout=1.0)
        deadline = time.monotonic() + 1.0
        while (
            not coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))
        assert not waiter_acquired.is_set()

    waiter.join(timeout=1.0)
    assert not waiter.is_alive()
    assert waiter_acquired.is_set()
    assert not coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))


def test_recovery_queued_before_monitor_yields_at_final_gate(tmp_path: Path) -> None:
    coordinator = WriteCoordinator(_db_paths(tmp_path))
    order: list[str] = []
    errors: list[BaseException] = []

    def _writer(owner: str, priority: WritePriority) -> None:
        try:
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner=owner,
                priority=priority,
                deadline_ms=2_000,
            ):
                order.append(owner)
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    waiter_path = writer_monitor_waiter_path(_db_paths(tmp_path)[DBIdentity.TRADE])

    with coordinator.lease((DBIdentity.TRADE,), owner="blocker"):
        recovery = threading.Thread(
            target=_writer,
            args=("recovery", WritePriority.RECOVERY_CRITICAL),
        )
        recovery.start()
        deadline = time.monotonic() + 1.0
        while not _exclusive_flock_visible(waiter_path) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert _exclusive_flock_visible(waiter_path)

        monitor = threading.Thread(
            target=_writer,
            args=("monitor", WritePriority.MONITOR),
        )
        monitor.start()
        deadline = time.monotonic() + 1.0
        while (
            not coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))

    monitor.join(timeout=2.0)
    recovery.join(timeout=2.0)
    assert not monitor.is_alive()
    assert not recovery.is_alive()
    assert errors == []
    assert order == ["monitor", "recovery"]


def test_multi_db_recovery_yields_when_monitor_arrives_after_first_gate(
    tmp_path: Path,
) -> None:
    paths = _db_paths(tmp_path)
    coordinator = WriteCoordinator(paths)
    order: list[str] = []
    errors: list[BaseException] = []

    def _recovery() -> None:
        try:
            with coordinator.lease(
                (DBIdentity.WORLD, DBIdentity.TRADE),
                owner="recovery",
                priority=WritePriority.RECOVERY_CRITICAL,
                deadline_ms=2_000,
            ):
                order.append("recovery")
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    def _monitor() -> None:
        try:
            with coordinator.lease(
                (DBIdentity.WORLD,),
                owner="monitor",
                priority=WritePriority.MONITOR,
                deadline_ms=2_000,
            ):
                order.append("monitor")
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    with coordinator.lease((DBIdentity.TRADE,), owner="trade-blocker"):
        recovery = threading.Thread(target=_recovery)
        recovery.start()
        world_gate = unified_writer_lock_path(paths[DBIdentity.WORLD])
        deadline = time.monotonic() + 1.0
        while not _exclusive_flock_visible(world_gate) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert _exclusive_flock_visible(world_gate)

        monitor = threading.Thread(target=_monitor)
        monitor.start()
        deadline = time.monotonic() + 1.0
        while (
            not coordinator.has_pending_monitor_waiter((DBIdentity.WORLD,))
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert coordinator.has_pending_monitor_waiter((DBIdentity.WORLD,))

    monitor.join(timeout=2.0)
    recovery.join(timeout=2.0)
    assert not monitor.is_alive()
    assert not recovery.is_alive()
    assert errors == []
    assert order == ["monitor", "recovery"]


def test_monitor_cannot_enter_final_check_to_lease_publication_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _db_paths(tmp_path)
    coordinator = WriteCoordinator(paths)
    publication_barrier_owned = threading.Event()
    release_publication = threading.Event()
    monitor_started = threading.Event()
    order: list[str] = []
    errors: list[BaseException] = []
    original_barrier = coordinator._acquire_nonmonitor_publication_barrier

    def _pause_after_final_check(ordered, *, owner):
        fds = original_barrier(ordered, owner=owner)
        publication_barrier_owned.set()
        assert release_publication.wait(timeout=1.0)
        return fds

    monkeypatch.setattr(
        coordinator,
        "_acquire_nonmonitor_publication_barrier",
        _pause_after_final_check,
    )

    def _recovery() -> None:
        try:
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="recovery",
                priority=WritePriority.RECOVERY_CRITICAL,
                deadline_ms=2_000,
            ):
                order.append("recovery")
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    def _monitor() -> None:
        monitor_started.set()
        try:
            with coordinator.lease(
                (DBIdentity.TRADE,),
                owner="monitor",
                priority=WritePriority.MONITOR,
                deadline_ms=2_000,
            ):
                order.append("monitor")
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    recovery = threading.Thread(target=_recovery)
    recovery.start()
    assert publication_barrier_owned.wait(timeout=1.0)

    monitor = threading.Thread(target=_monitor)
    monitor.start()
    assert monitor_started.wait(timeout=1.0)
    deadline = time.monotonic() + 1.0
    while (
        not coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)
    assert coordinator.has_pending_monitor_waiter((DBIdentity.TRADE,))
    assert order == []

    release_publication.set()
    recovery.join(timeout=2.0)
    monitor.join(timeout=2.0)
    assert not recovery.is_alive()
    assert not monitor.is_alive()
    assert errors == []
    assert order == ["recovery", "monitor"]


def test_exit_writer_identity_failure_cannot_bypass_trade_lease() -> None:
    from src.execution.executor import (
        _canonical_trade_write_lease,
        _trade_writer_lease_required,
    )

    class BrokenIdentityConnection:
        def execute(self, _sql):
            raise sqlite3.OperationalError("identity probe unavailable")

    conn = BrokenIdentityConnection()
    with pytest.raises(RuntimeError, match="canonical TRADE DB identity unavailable"):
        _canonical_trade_write_lease(
            conn,
            owner="identity-failure",
            deadline_ms=10,
            max_hold_ms=10,
        )
    with pytest.raises(RuntimeError, match="canonical TRADE DB identity unavailable"):
        _trade_writer_lease_required(conn)


def test_monitor_and_exit_trade_writers_serialize_wal_transactions_with_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.engine.cycle_runtime import _canonical_trade_write_lease as monitor_write_lease
    from src.execution.executor import _canonical_trade_write_lease as exit_write_lease
    from src.state import db as state_db
    from src.state.collateral_ledger import (
        CollateralLedger,
        CollateralSnapshot,
        init_collateral_schema,
    )
    from src.state import write_coordinator as coordinator_module

    telemetry: list[WriteLeaseTelemetry] = []
    paths = _db_paths(tmp_path)
    coordinator = WriteCoordinator(paths, telemetry_sink=telemetry.append)
    monkeypatch.setattr(state_db, "_zeus_trade_db_path", lambda: paths[DBIdentity.TRADE])
    monkeypatch.setattr(
        coordinator_module,
        "default_runtime_write_coordinator",
        lambda: coordinator,
    )
    with sqlite3.connect(paths[DBIdentity.TRADE]) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE writes (owner TEXT PRIMARY KEY)")
        init_collateral_schema(conn)

    monitor_holding = threading.Event()
    release_monitor = threading.Event()
    errors: list[BaseException] = []

    def monitor_writer(conn: sqlite3.Connection) -> None:
        try:
            with monitor_write_lease(
                conn,
                owner="monitor_canonical_append",
                deadline_ms=500,
                max_hold_ms=250,
            ):
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO writes VALUES ('monitor')")
                monitor_holding.set()
                assert release_monitor.wait(timeout=1.0)
                conn.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    def exit_writer() -> None:
        try:
            assert monitor_holding.wait(timeout=1.0)
            with sqlite3.connect(paths[DBIdentity.TRADE], timeout=0) as conn:
                conn.row_factory = sqlite3.Row
                with exit_write_lease(
                    conn,
                    owner="exit_pre_submit_persist",
                    deadline_ms=500,
                    max_hold_ms=250,
                ):
                    conn.execute("BEGIN IMMEDIATE")
                    CollateralLedger.persist_prepared_snapshot_in_transaction(
                        conn,
                        CollateralSnapshot(
                            pusd_balance_micro=0,
                            pusd_allowance_micro=0,
                            usdc_e_legacy_balance_micro=0,
                            ctf_token_balances={"exit-token": 5_000_000},
                            ctf_token_allowances={"exit-token": 5_000_000},
                            reserved_pusd_for_buys_micro=0,
                            reserved_tokens_for_sells={},
                            captured_at=datetime.now(timezone.utc),
                            authority_tier="CHAIN",
                            raw_balance_payload_hash="prepared",
                        )
                    )
                    CollateralLedger.reserve_tokens_for_sell_in_transaction(
                        conn,
                        "exit-command",
                        "exit-token",
                        5.0,
                    )
                    conn.execute("INSERT INTO writes VALUES ('exit')")
                    conn.commit()
        except BaseException as exc:  # noqa: BLE001 - surfaced below.
            errors.append(exc)

    def monitor_connection_writer() -> None:
        with sqlite3.connect(paths[DBIdentity.TRADE], timeout=0) as conn:
            monitor_writer(conn)

    monitor = threading.Thread(target=monitor_connection_writer)
    exit_writer_thread = threading.Thread(target=exit_writer)
    monitor.start()
    exit_writer_thread.start()
    assert monitor_holding.wait(timeout=1.0)
    time.sleep(0.03)
    release_monitor.set()
    monitor.join(timeout=2.0)
    exit_writer_thread.join(timeout=2.0)

    assert not monitor.is_alive()
    assert not exit_writer_thread.is_alive()
    assert errors == []
    with sqlite3.connect(paths[DBIdentity.TRADE]) as conn:
        assert conn.execute("SELECT owner FROM writes ORDER BY owner").fetchall() == [
            ("exit",),
            ("monitor",),
        ]
        assert conn.execute("SELECT COUNT(*) FROM collateral_ledger_snapshots").fetchone() == (1,)
        assert conn.execute(
            "SELECT command_id, reservation_type, token_id, amount "
            "FROM collateral_reservations"
        ).fetchone() == ("exit-command", "CTF_SELL", "exit-token", 5_000_000)
    by_owner = {row.owner: row for row in telemetry}
    assert by_owner["monitor_canonical_append"].hold_ms > 0.0
    assert by_owner["exit_pre_submit_persist"].wait_ms >= 20.0
    assert all(row.deadline_exceeded is False for row in by_owner.values())


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
