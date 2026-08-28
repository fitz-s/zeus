# Created: 2026-06-08
# Last reused or audited: 2026-08-28 (bounded fill-bridge event discovery)
# Authority basis: docs/reference/design_system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row + co-location decision),
#   §7 (I2 no-back-coupling: durable fill bridge + execution_feasibility_evidence),
#   §8 Step 3 (lift the user-channel WS thread + market-channel + reconcile cycles),
#   §9 (regression-unconstructable proof — failure-domain isolation).
# Lifecycle: created=2026-06-08; last_reviewed=2026-08-24; last_reused=2026-08-24
# Purpose: RELATIONSHIP TESTS for process-topology refactor STEP P3 — lift the
#   price-channel / CLOB-fact ingest (the persistent user/market WebSocket lifecycle)
#   out of the order daemon into its own process (com.zeus.price-channel-ingest).
#
# These tests verify CROSS-MODULE INVARIANTS (Module A's output → Module B), not just
# function behaviour:
#   (NO-REGRESSION) the WS producer + the two channel/reconcile cycles still EXIST and
#     still write the durable fill bridge + current feasibility projection the order
#     runtime READS; the durable fill-bridge SCAN helper stays importable by src.main's
#     BOOT recovery (the persisted truth is shared, so no fill is dropped across the
#     cutover); src.main still imports + boots with the jobs removed; the new process
#     opens its DB via the sanctioned path (no independent cross-DB connection).
#   (SUPERIORITY) the WS-failure latch (ws_gap_guard) is no longer WRITTEN inside the
#     order daemon process: src.main neither STARTS the WS ingestor thread nor REGISTERS
#     the two channel/reconcile cycles, so a WS auth/transport flap (record_gap →
#     reduce_only-forever, src/main.py:2610-2622 history) can no longer originate in the
#     order daemon. The order daemon sees a WS outage ONLY as stale/absent
#     execution_feasibility_evidence rows (DB-mediated, observable), never as a
#     shared-process exception or a poisoned in-memory submit latch.
"""STEP P3 relationship tests: lift the price-channel / CLOB-fact ingest to its own process."""
from __future__ import annotations

import ast
import contextlib
import fcntl
import inspect
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PY = _REPO_ROOT / "src" / "main.py"
_PRICE_CHANNEL_MODULE = _REPO_ROOT / "src" / "ingest" / "price_channel_ingest.py"
_PRICE_CHANNEL_DAEMON = _REPO_ROOT / "src" / "ingest" / "price_channel_daemon.py"
_PRICE_CHANNEL_PLIST = _REPO_ROOT / "deploy" / "launchd" / "com.zeus.price-channel-ingest.plist"
_EXECUTOR_PY = _REPO_ROOT / "src" / "execution" / "executor.py"

# The two scheduled cycles lifted to P3 (the WS user-channel ingestor is a long-running
# THREAD, not an add_job — it is started by _start_user_channel_ingestor_if_enabled).
_LIFTED_JOB_IDS = (
    "edli_market_channel_ingestor",
    "edli_user_channel_reconcile",
    "edli_fill_bridge_repair",
)

# The lifted producer surface that must live in the new P3 lane module.
_LIFTED_PRODUCERS = (
    "_start_user_channel_ingestor_if_enabled",
    "_edli_market_channel_ingestor_cycle",
    "_edli_user_channel_reconcile_cycle",
)


def test_market_channel_bootstrap_separates_entry_and_held_exit_metadata() -> None:
    from src.ingest import price_channel_ingest

    tree = ast.parse(_PRICE_CHANNEL_MODULE.read_text(encoding="utf-8"))
    cycle = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_edli_market_channel_ingestor_cycle"
    )
    entry_calls = [
        call
        for call in ast.walk(cycle)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "active_weather_token_metadata_from_snapshots"
    ]
    exit_calls = [
        call
        for call in ast.walk(cycle)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "active_weather_token_metadata_for_tokens"
    ]
    # Broad snapshot hydration is post-registration via the service reloader;
    # bootstrap only performs bounded targeted reads.
    assert entry_calls == []
    assert exit_calls == []
    reloader_source = inspect.getsource(
        price_channel_ingest._edli_market_channel_token_metadata_reloader
    )
    assert "active_weather_token_metadata_from_snapshots" in reloader_source
    cycle_source = inspect.getsource(price_channel_ingest._edli_market_channel_ingestor_cycle)
    pre_runner = cycle_source.split("    def _runner", 1)[0]
    assert "_connect_read_only" not in pre_runner
    assert "get_forecasts_connection_read_only" not in pre_runner
    assert "get_trade_connection" not in pre_runner
    assert "_edli_complete_market_channel_bootstrap" in pre_runner


def test_price_channel_daemon_separates_starting_status_from_ready_heartbeat() -> None:
    tree = ast.parse(_PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8"))
    main = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    calls = [
        (call.func.id, call.lineno)
        for call in ast.walk(main)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id
        in {
            "_write_price_channel_startup_status",
            "_write_price_channel_heartbeat",
            "_start_user_channel_ingestor_async",
        }
    ]

    starting = next(
        line
        for name, line in calls
        if name == "_write_price_channel_startup_status"
    )
    canonical_starting = next(
        line
        for name, line in calls
        if name == "_write_price_channel_heartbeat"
        and any(
            isinstance(keyword, ast.keyword)
            and keyword.arg == "status"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "STARTING"
            for node in ast.walk(main)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
            for keyword in node.keywords
        )
    )
    async_start = next(
        line for name, line in calls if name == "_start_user_channel_ingestor_async"
    )
    assert starting <= canonical_starting < async_start
    source = _PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8")
    assert '_write_price_channel_heartbeat(status="READY")' not in ast.get_source_segment(
        source, main
    )
    assert "_promote_price_channel_heartbeat_ready" in source


def test_price_channel_starting_heartbeat_is_not_fresh_until_first_m5_success(
    monkeypatch, tmp_path
) -> None:
    from src import config
    from src.ingest import price_channel_daemon as daemon

    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(daemon, "_heartbeat_ready", False)
    monkeypatch.setattr(daemon, "_heartbeat_status", "STARTING")
    monkeypatch.setattr(daemon, "_heartbeat_published", False)
    daemon._write_price_channel_heartbeat(status="STARTING")
    starting = json.loads(
        (tmp_path / "daemon-heartbeat-price-channel-ingest.json").read_text()
    )
    assert starting["status"] == "STARTING"
    assert starting["ready"] is False
    assert "alive_at" not in starting

    daemon._promote_price_channel_heartbeat_ready()
    ready = json.loads(
        (tmp_path / "daemon-heartbeat-price-channel-ingest.json").read_text()
    )
    assert ready["status"] == "READY"
    assert ready["ready"] is True
    assert ready["alive_at"]

    daemon._write_price_channel_heartbeat(status="STOPPING")
    stopping = json.loads(
        (tmp_path / "daemon-heartbeat-price-channel-ingest.json").read_text()
    )
    assert stopping["ready"] is False
    assert "alive_at" not in stopping

    monkeypatch.setattr(daemon, "_heartbeat_ready", True)
    daemon._write_price_channel_heartbeat(status="READY")
    daemon._write_price_channel_heartbeat(status="FAILED")
    failed = json.loads(
        (tmp_path / "daemon-heartbeat-price-channel-ingest.json").read_text()
    )
    assert failed["ready"] is False
    assert "alive_at" not in failed


def test_m5_heartbeat_promotion_requires_current_scheduler_receipt(monkeypatch, tmp_path):
    import src.ingest.price_channel_daemon as daemon
    import src.observability.scheduler_health as scheduler_health

    heartbeat_path = tmp_path / "daemon-heartbeat-price-channel-ingest.json"
    health_path = tmp_path / "scheduler_jobs_health.json"
    from src import config

    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(scheduler_health, "_SCHEDULER_HEALTH_PATH", health_path)
    monkeypatch.setattr(daemon, "_heartbeat_ready", False)
    monkeypatch.setattr(daemon, "_heartbeat_status", "STARTING")
    daemon._write_price_channel_heartbeat(status="STARTING")

    result = {"scheduler_failed": False, "status": "m5_authority_proof_complete"}
    real_write_scheduler_health = scheduler_health._write_scheduler_health
    monkeypatch.setattr(scheduler_health, "_write_scheduler_health", lambda *_a, **_k: None)
    daemon._scheduler_job("edli_user_channel_reconcile")(lambda: result)()
    assert daemon._heartbeat_ready is False
    assert "alive_at" not in json.loads(heartbeat_path.read_text())

    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        real_write_scheduler_health,
    )
    daemon._scheduler_job("edli_user_channel_reconcile")(lambda: result)()
    assert daemon._heartbeat_ready is True
    assert json.loads(heartbeat_path.read_text())["alive_at"]


def test_startup_failure_abort_is_injected_and_not_a_python_raise(monkeypatch):
    from src.ingest import price_channel_daemon as daemon

    class Abort(BaseException):
        pass

    monkeypatch.setattr(daemon.os, "_exit", lambda code: (_ for _ in ()).throw(Abort(code)))
    with pytest.raises(Abort) as exc_info:
        daemon._abort_startup_failure()
    assert exc_info.value.args == (1,)


def test_startup_failure_subprocess_abort_leaves_wal_without_clean_close(tmp_path):
    db_path = tmp_path / "abort-wal.db"
    script = (
        "import os, sqlite3, sys\n"
        "conn = sqlite3.connect(sys.argv[1])\n"
        "conn.execute('PRAGMA journal_mode=WAL')\n"
        "conn.execute('PRAGMA wal_autocheckpoint=0')\n"
        "conn.execute('CREATE TABLE IF NOT EXISTS t (v TEXT)')\n"
        "conn.executemany('INSERT INTO t VALUES (?)', ((str(i),) for i in range(20000)))\n"
        "conn.commit()\n"
        "from src.ingest.price_channel_daemon import _abort_startup_failure\n"
        "_abort_startup_failure()\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(db_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    wal_path = Path(str(db_path) + "-wal")
    assert wal_path.exists()
    assert wal_path.stat().st_size > 0


def test_price_channel_startup_bridge_sets_wal_policy_before_probe_and_releases_read_txn():
    """The preflight probe cannot inherit autocheckpoint or pin a WAL snapshot."""
    from src.ingest import price_channel_daemon as daemon

    class Cursor:
        def __init__(self, events):
            self.events = events

        def fetchone(self):
            self.events.append("fetchone")
            return (1,)

        def close(self):
            self.events.append("cursor_close")

    class Connection:
        in_transaction = False

        def __init__(self):
            self.events = []

        def execute(self, sql):
            self.events.append(sql)
            if sql.startswith("SELECT"):
                return Cursor(self.events)
            return Cursor(self.events)

        def rollback(self):
            self.events.append("rollback")

    conn = Connection()
    daemon._prepare_startup_bridge(conn)

    assert conn.events.index("PRAGMA wal_autocheckpoint = 0") < conn.events.index(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='position_current'"
    )
    assert conn.events.count("rollback") >= 2
    assert conn.events[-1] == "rollback"


def test_price_channel_startup_keeper_handoff_precedes_transient_close(monkeypatch):
    """STARTING is published before keeper handoff; only deliberate close releases it."""
    from src.ingest import price_channel_daemon as daemon

    events: list[str] = []

    class Cursor:
        def fetchone(self):
            events.append("probe")
            return (1,)

        def close(self):
            events.append("cursor_close")

    class Connection:
        in_transaction = False

        def execute(self, sql):
            events.append(sql)
            return Cursor()

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    conn = Connection()
    monkeypatch.setattr(daemon, "_bridge_keeper_conn", None)
    monkeypatch.setattr(
        daemon,
        "_write_price_channel_heartbeat",
        lambda *, status=None: events.append(f"heartbeat:{status}"),
    )

    daemon._prepare_startup_bridge(conn)
    daemon._write_price_channel_heartbeat(status="STARTING")
    daemon._handoff_bridge_keeper(conn)
    events.append("keeper_handoff")
    # A second consumer is the proof that closing this short-lived handle is not
    # the last-close path; the keeper remains open until deliberate cleanup.
    events.append("non_last_consumer_open")
    events.append("non_last_consumer_close")
    daemon._close_bridge_keeper(reason="test_shutdown")

    assert events.index("probe") < events.index("heartbeat:STARTING")
    assert events.index("heartbeat:STARTING") < events.index("keeper_handoff")
    assert events.index("keeper_handoff") < events.index("non_last_consumer_open")
    assert events.index("non_last_consumer_close") < events.index("close")
    assert events.count("close") == 1
    assert daemon._bridge_keeper_conn is None


def test_price_channel_startup_keeper_failure_abandons_without_last_close(monkeypatch):
    from src.ingest import price_channel_daemon as daemon

    class Connection:
        def __init__(self):
            self.closed = 0
            self.rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed += 1

    conn = Connection()
    monkeypatch.setattr(daemon, "_bridge_keeper_conn", conn)
    daemon._abandon_startup_bridge_on_failure()

    assert conn.rollbacks == 0
    assert conn.closed == 0
    assert daemon._bridge_keeper_conn is None


def test_price_channel_startup_bridge_real_wal_keeper_survives_transient_close(tmp_path):
    """A live keeper prevents a transient WAL handle from becoming last-close."""
    from src.ingest import price_channel_daemon as daemon

    db_path = tmp_path / "startup-bridge.db"
    keeper = sqlite3.connect(db_path)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute("CREATE TABLE position_current (position_id TEXT PRIMARY KEY)")
    keeper.commit()
    daemon._prepare_startup_bridge(keeper)

    assert keeper.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == 0
    assert keeper.in_transaction is False
    transient = sqlite3.connect(db_path)
    transient.close()
    assert keeper.execute("SELECT COUNT(*) FROM position_current").fetchone()[0] == 0
    keeper.close()


def test_candidate_quote_refresh_budget_matches_live_redecision_surface() -> None:
    from src.ingest import price_channel_ingest as pci

    assert 30.0 <= pci.MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT < 60.0
    assert pci.MARKET_CHANNEL_PRIORITY_QUOTE_REFRESH_CHUNK_SIZE_DEFAULT <= 4
    assert pci.PRICE_CHANNEL_DB_WRITE_LEASE_DEADLINE_MS <= 25
    assert pci.PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS <= 1000


def test_user_channel_reconcile_gets_bounded_writer_handoff_budget() -> None:
    from src.ingest import price_channel_ingest as pci

    inbox = pci._edli_price_channel_world_write_gate(
        owner="price_channel_user_inbox"
    )
    reconcile = pci._edli_price_channel_world_write_gate(
        owner="price_channel_venue_reconcile"
    )
    coalescible_tick = pci._edli_price_channel_world_write_gate(
        owner="price_channel_market_event"
    )

    assert inbox._deadline_ms == (
        pci.PRICE_CHANNEL_USER_RECONCILE_DB_WRITE_LEASE_DEADLINE_MS
    )
    assert reconcile._deadline_ms == inbox._deadline_ms
    assert 200 <= inbox._deadline_ms <= 500
    assert coalescible_tick._deadline_ms <= 25

    m5 = next(
        node
        for node in ast.walk(ast.parse(_PRICE_CHANNEL_MODULE.read_text()))
        if isinstance(node, ast.FunctionDef)
        and node.name == "_edli_user_channel_reconcile_cycle"
    )
    opener = next(
        node
        for node in ast.walk(m5)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_world_connection_with_trades_required"
    )
    keywords = {keyword.arg: keyword.value for keyword in opener.keywords}
    assert isinstance(keywords["deadline_monotonic"], ast.Name)
    assert keywords["deadline_monotonic"].id == "deadline_monotonic"
    assert "busy_timeout_ms" in keywords


def test_pending_reconcile_scan_uses_state_index_and_preserves_global_age() -> None:
    from src.ingest import price_channel_ingest as pci

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE edli_live_order_projection (
            aggregate_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            final_intent_id TEXT,
            current_state TEXT NOT NULL,
            pending_reconcile INTEGER NOT NULL,
            venue_order_id TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_edli_live_order_projection_state
            ON edli_live_order_projection(current_state, updated_at);
        CREATE INDEX idx_edli_live_order_projection_reconcile
            ON edli_live_order_projection(pending_reconcile, updated_at);
        """
    )
    for state in ("PENDING_RECONCILE", "USER_TRADE_OBSERVED", "RECONCILED"):
        conn.executemany(
            """
            INSERT INTO edli_live_order_projection VALUES (?, ?, NULL, ?, 0, NULL, ?)
            """,
            [
                (f"{state}-{i}", f"event-{state}-{i}", state, f"2026-08-27T01:{i:02d}:00+00:00")
                for i in range(40)
            ],
        )
    conn.executemany(
        """
        INSERT INTO edli_live_order_projection VALUES (?, ?, NULL, ?, 1, NULL, ?)
        """,
        [
            ("newer-a", "event-a", "PENDING_RECONCILE", "2026-08-27T03:00:00+00:00"),
            ("oldest", "event-b", "USER_TRADE_OBSERVED", "2026-08-27T00:01:00+00:00"),
            ("newer-b", "event-c", "RECONCILED", "2026-08-27T04:00:00+00:00"),
            ("second", "event-d", "PENDING_RECONCILE", "2026-08-27T00:02:00+00:00"),
        ],
    )
    traced: list[str] = []
    conn.set_trace_callback(traced.append)

    rows = pci._edli_pending_reconcile_aggregates(conn, limit=3)

    assert [row["aggregate_id"] for row in rows] == ["oldest", "second", "newer-a"]
    projection_reads = [
        sql for sql in traced if "FROM edli_live_order_projection" in sql
    ]
    assert projection_reads
    assert all(
        "INDEXED BY idx_edli_live_order_projection_reconcile" in sql
        for sql in projection_reads
    )


def test_live_order_projection_schema_owns_pending_reconcile_index() -> None:
    from src.state.schema.edli_live_order_events_schema import ensure_tables

    conn = sqlite3.connect(":memory:")
    ensure_tables(conn)

    columns = [
        row[2]
        for row in conn.execute(
            "PRAGMA index_info('idx_edli_live_order_projection_reconcile')"
        ).fetchall()
    ]
    assert columns == ["pending_reconcile", "updated_at"]


def test_quote_refresh_no_coverage_is_business_failure() -> None:
    from src.ingest import price_channel_ingest as pci

    failed, reason = pci._price_channel_quote_refresh_failed(
        {
            "candidate_token_metadata": 32,
            "candidate_quote_refresh_events": 0,
            "budget_exhausted": True,
            "budget_skipped_tokens": 32,
        },
        token_key="candidate_token_metadata",
        event_key="candidate_quote_refresh_events",
    )

    assert failed is True
    assert reason == "quote_refresh_budget_exhausted_no_coverage"


def test_quote_refresh_partial_coverage_is_business_failure() -> None:
    from src.ingest import price_channel_ingest as pci

    failed, reason = pci._price_channel_quote_refresh_failed(
        {
            "held_token_metadata": 2,
            "held_quote_refresh_events": 1,
            "budget_exhausted": False,
            "budget_skipped_tokens": 1,
        },
        token_key="held_token_metadata",
        event_key="held_quote_refresh_events",
    )

    assert failed is True
    assert reason == "quote_refresh_partial_coverage"


def test_quote_refresh_complete_coverage_is_healthy_even_if_elapsed_crosses_budget() -> None:
    from src.ingest import price_channel_ingest as pci

    failed, reason = pci._price_channel_quote_refresh_failed(
        {
            "held_token_metadata": 2,
            "held_quote_refresh_events": 2,
            "budget_exhausted": True,
            "budget_skipped_tokens": 0,
        },
        token_key="held_token_metadata",
        event_key="held_quote_refresh_events",
    )

    assert failed is False
    assert reason is None


def test_price_channel_daemon_scheduler_health_uses_business_result(monkeypatch) -> None:
    import src.ingest.price_channel_daemon as daemon
    import src.observability.scheduler_health as scheduler_health

    writes: list[dict] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: writes.append({"job_name": job_name, **kwargs}),
    )

    wrapped = daemon._scheduler_job("edli_market_channel_ingestor")(
        lambda: {
            "scheduler_failed": True,
            "scheduler_failure_reason": "candidate_quote_refresh_no_coverage",
        }
    )
    result = wrapped()

    assert result["scheduler_failed"] is True
    assert writes == [
        {
            "job_name": "edli_market_channel_ingestor",
            "failed": True,
            "reason": "candidate_quote_refresh_no_coverage",
            "extra": result,
        }
    ]


def test_price_channel_daemon_records_max_instance_skip(monkeypatch) -> None:
    import src.ingest.price_channel_daemon as daemon
    import src.observability.scheduler_health as scheduler_health

    writes: list[dict] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: writes.append({"job_name": job_name, **kwargs}),
    )

    daemon._scheduler_skip_listener(
        types.SimpleNamespace(
            job_id="edli_held_quote_refresh",
            scheduled_run_times=[datetime(2026, 6, 30, tzinfo=timezone.utc)],
        )
    )

    assert writes == [
        {
            "job_name": "edli_held_quote_refresh",
            "failed": False,
            "skipped": True,
            "skip_reason": "max_instances_reached",
            "extra": {
                "scheduler_skip_reason": "max_instances_reached",
                "scheduled_run_times": ["2026-06-30T00:00:00+00:00"],
            },
        }
    ]


def test_market_channel_bootstrap_timeout_fences_late_worker_and_retries(monkeypatch, tmp_path) -> None:
    from threading import Event

    from src import config
    from src.ingest import price_channel_daemon as daemon
    from src.ingest import price_channel_ingest as lane

    target = tmp_path / lane.MARKET_CHANNEL_SINK_READINESS_FILENAME
    monkeypatch.setattr(config, "state_path", lambda _filename: target)
    monkeypatch.setattr(daemon, "_market_channel_bootstrap_worker", None)
    monkeypatch.setattr(daemon, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(daemon, "_market_channel_bootstrap_started_monotonic", None)
    monkeypatch.setattr(lane, "_edli_market_channel_thread", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)

    entered = Event()
    release = Event()
    generations: list[str] = []

    def blocked_bootstrap(*, bootstrap_generation: str, **_kwargs) -> None:
        generations.append(bootstrap_generation)
        entered.set()
        while not lane._edli_market_channel_bootstrap_cancelled(bootstrap_generation):
            release.wait(timeout=0.01)

    first = daemon._market_channel_bootstrap_job(blocked_bootstrap)
    assert first["thread"] == "bootstrap_worker_started"
    assert entered.wait(timeout=1.0)
    old_generation = str(first["bootstrap_generation"])
    real_monotonic = daemon.time.monotonic
    monkeypatch.setattr(
        daemon,
        "_market_channel_bootstrap_started_monotonic",
        0.0,
    )
    monkeypatch.setattr(
        daemon.time,
        "monotonic",
        lambda: daemon.MARKET_CHANNEL_BOOTSTRAP_DEADLINE_SECONDS + 1.0,
    )

    timed_out = daemon._market_channel_bootstrap_job(blocked_bootstrap)
    assert timed_out["scheduler_failed"] is True
    assert timed_out["scheduler_failure_reason"] == "registration_not_reached"
    assert lane._edli_market_channel_bootstrap_is_current(old_generation) is False

    current = daemon._market_channel_bootstrap_worker
    assert current is not None
    daemon.time.monotonic = real_monotonic
    lane._edli_cancel_market_channel_bootstrap(generations[-1])
    current.join(timeout=1.0)
    assert not current.is_alive()
    assert len(generations) == 2
    lane._edli_supersede_market_channel_bootstrap(generations[-1])


def test_market_channel_bootstrap_cancel_interrupts_and_closes_registered_reader(monkeypatch):
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)
    generation = lane._edli_begin_market_channel_bootstrap(
        deadline_monotonic=lane.time.monotonic() + 30.0
    )

    class Connection:
        def __init__(self):
            self.interrupted = 0
            self.closed = 0
            self.handlers = []

        def set_progress_handler(self, handler, interval):
            self.handlers.append((handler, interval))

        def interrupt(self):
            self.interrupted += 1

        def close(self):
            self.closed += 1

    conn = Connection()
    with lane._edli_market_channel_bootstrap_connection(conn, generation):
        assert lane._edli_cancel_market_channel_bootstrap(generation) is True
        assert conn.interrupted == 1
    assert conn.closed == 1
    assert conn.handlers[-1] == (None, 0)
    lane._edli_supersede_market_channel_bootstrap(generation)


def test_market_channel_runner_blocked_connection_is_cancelled_and_joined(monkeypatch):
    from threading import Event

    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)
    generation = lane._edli_begin_market_channel_bootstrap(
        deadline_monotonic=lane.time.monotonic() + 30.0
    )
    entered = Event()
    class Connection:
        def __init__(self):
            self.closed = 0
            self.interrupted = 0

        def set_progress_handler(self, _handler, _interval):
            return None

        def interrupt(self):
            self.interrupted += 1

        def close(self):
            self.closed += 1

    conn = Connection()

    def runner():
        with lane._edli_market_channel_bootstrap_connection(conn, generation):
            entered.set()
            while not lane._edli_market_channel_bootstrap_cancelled(generation):
                entered.wait(timeout=0.01)

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    assert entered.wait(timeout=1.0)
    lane._edli_cancel_market_channel_bootstrap(generation)
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert conn.interrupted == 1
    assert conn.closed == 1
    lane._edli_supersede_market_channel_bootstrap(generation)


def test_market_channel_registration_releases_bootstrap_deadline_after_success(monkeypatch):
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)
    generation = lane._edli_begin_market_channel_bootstrap(
        deadline_monotonic=lane.time.monotonic() + 30.0
    )

    class Connection:
        def __init__(self):
            self.closed = 0
            self.handlers = []

        def set_progress_handler(self, handler, interval):
            self.handlers.append((handler, interval))

        def close(self):
            self.closed += 1

    conn = Connection()
    with lane._edli_market_channel_bootstrap_connection(conn, generation):
        lane._edli_mark_market_channel_bootstrap_registered(generation)
        assert lane._edli_market_channel_bootstrap_cancelled(generation) is False

    assert conn.closed == 1
    assert conn.handlers[-1] == (None, 0)
    lane._edli_supersede_market_channel_bootstrap(generation)


def test_market_channel_sink_readiness_requires_current_pid_and_generation(
    monkeypatch,
    tmp_path,
) -> None:
    from src import config
    from src.ingest import price_channel_ingest as lane

    target = tmp_path / lane.MARKET_CHANNEL_SINK_READINESS_FILENAME
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)

    generation = lane._edli_begin_market_channel_bootstrap()
    service = object()
    calls: list[object] = []
    assert lane._edli_register_current_market_channel_action_sink(
        service,
        generation,
        calls.append,
        calls.append,
    )
    assert lane._edli_market_channel_sink_readiness_error() is None

    proof = json.loads(target.read_text(encoding="utf-8"))
    assert proof["pid"] == os.getpid()
    assert proof["generation"] == generation
    assert proof["sink_registered"] is True
    assert proof["consumer_queue_accepted"] is True

    lane._write_market_channel_continuity(
        {
            "schema_version": 1,
            "channel": "market_channel",
            "generation": generation,
            "connected": True,
            "connected_at": "2026-08-24T00:00:00+00:00",
            "observed_at": "2026-08-24T00:00:01+00:00",
        }
    )
    continuity_path = tmp_path / lane.MARKET_CHANNEL_CONTINUITY_FILENAME
    continuity = json.loads(continuity_path.read_text(encoding="utf-8"))
    continuity["generation"] = "prior-generation"
    continuity_path.write_text(json.dumps(continuity), encoding="utf-8")
    action = types.SimpleNamespace(
        condition_id="condition",
        token_id="token",
        reason="held",
    )
    rejected = lane._edli_enqueue_held_snapshot_refresh_actions([action])
    assert rejected["held_snapshot_refresh_actions_enqueued"] == 0
    assert (
        "ContinuityUnavailable"
        in rejected["held_snapshot_refresh_enqueue_unavailable"][0]["debt_reason"]
    )

    proof["pid"] = os.getpid() + 1
    target.write_text(json.dumps(proof), encoding="utf-8")
    assert (
        "another PID or generation"
        in lane._edli_market_channel_sink_readiness_error()
    )

    lane._edli_unregister_current_market_channel_action_sink(
        service,
        generation,
        calls.append,
    )
    assert calls == [service, service]


def test_market_channel_receipt_write_failure_unregisters_before_next_generation(
    monkeypatch,
    tmp_path,
) -> None:
    from src import config
    from src.events.triggers.market_channel_ingestor import (
        persistent_market_channel_action_receipt,
        register_persistent_market_channel_action_sink,
        unregister_persistent_market_channel_action_sink,
    )
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)
    generation = lane._edli_begin_market_channel_bootstrap()
    original_write = lane._write_market_channel_sink_readiness
    monkeypatch.setattr(
        lane,
        "_write_market_channel_sink_readiness",
        lambda _payload: (_ for _ in ()).throw(OSError("receipt write failed")),
    )

    with pytest.raises(OSError, match="receipt write failed"):
        lane._edli_register_current_market_channel_action_sink(
            types.SimpleNamespace(
                invalidate_snapshot=lambda _action: None,
                refresh_snapshot=lambda _action: None,
            ),
            generation,
            register_persistent_market_channel_action_sink,
            unregister_persistent_market_channel_action_sink,
        )
    assert persistent_market_channel_action_receipt()["queued_exact_actions"] == 0

    monkeypatch.setattr(lane, "_write_market_channel_sink_readiness", original_write)
    next_generation = lane._edli_begin_market_channel_bootstrap()
    next_service = types.SimpleNamespace(
        invalidate_snapshot=lambda _action: None,
        refresh_snapshot=lambda _action: None,
    )
    assert lane._edli_register_current_market_channel_action_sink(
        next_service,
        next_generation,
        register_persistent_market_channel_action_sink,
        unregister_persistent_market_channel_action_sink,
    )
    lane._edli_unregister_current_market_channel_action_sink(
        next_service,
        next_generation,
        unregister_persistent_market_channel_action_sink,
    )


def test_m5_authority_deadline_fails_closed_without_publishing_health(monkeypatch) -> None:
    import src.ingest.price_channel_daemon as daemon
    from src.ingest import price_channel_ingest as lane
    import src.observability.scheduler_health as scheduler_health

    writes: list[dict] = []
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda job_name, **kwargs: writes.append({"job_name": job_name, **kwargs}),
    )
    monotonic = iter((100.0, 120.0))
    monkeypatch.setattr(lane.time, "monotonic", lambda: next(monotonic))

    result = daemon._scheduler_job("edli_user_channel_reconcile")(
        lane._edli_user_channel_reconcile_cycle
    )()

    assert result is None
    assert writes == [
        {
            "job_name": "edli_user_channel_reconcile",
            "failed": True,
            "reason": "m5_authority_proof_deadline_exhausted",
        }
    ]


def test_m5_authority_job_isolated_from_long_fill_bridge_repair() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    tree = ast.parse(_PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8"))
    jobs: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_job"
        ):
            continue
        job_id = next(
            (
                keyword.value.value
                for keyword in node.keywords
                if keyword.arg == "id" and isinstance(keyword.value, ast.Constant)
            ),
            None,
        )
        if isinstance(job_id, str):
            jobs[job_id] = node

    m5_job = jobs["edli_user_channel_reconcile"]
    bridge_job = jobs["edli_fill_bridge_repair"]
    for job, executor in ((m5_job, "m5_authority"), (bridge_job, "fill_bridge")):
        keywords = {keyword.arg: keyword.value for keyword in job.keywords}
        assert isinstance(keywords["max_instances"], ast.Constant)
        assert keywords["max_instances"].value == 1
        assert isinstance(keywords["coalesce"], ast.Constant)
        assert keywords["coalesce"].value is True
        assert isinstance(keywords["executor"], ast.Constant)
        assert keywords["executor"].value == executor

    interval = {
        keyword.arg: keyword.value
        for keyword in m5_job.keywords
        if keyword.arg in {"seconds", "minutes"}
    }
    assert set(interval) == {"seconds"}
    assert isinstance(interval["seconds"], ast.Name)
    assert interval["seconds"].id == "M5_AUTHORITY_PROOF_CADENCE_SECONDS"

    lane_tree = ast.parse(_PRICE_CHANNEL_MODULE.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in ast.walk(lane_tree)
        if isinstance(node, ast.FunctionDef)
    }
    m5_calls = {
        node.func.id
        for node in ast.walk(functions["_edli_user_channel_reconcile_cycle"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    repair_calls = {
        node.func.id
        for node in ast.walk(functions["_edli_fill_bridge_repair_cycle"])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_edli_durable_fill_bridge_scan" not in m5_calls
    assert "_edli_position_fill_redecision_cycle" not in m5_calls
    assert "_edli_price_channel_world_write_gate" in m5_calls
    assert "_edli_durable_fill_bridge_scan" in repair_calls
    assert "_edli_position_fill_redecision_cycle" in repair_calls
    assert "_edli_price_channel_world_write_gate" in repair_calls

    bridge_started = Event()
    release_bridge = Event()

    def _long_bridge_repair() -> str:
        bridge_started.set()
        assert release_bridge.wait(timeout=1.0)
        return "repaired"

    def _m5_proof() -> dict[str, str]:
        return {"status": "m5_authority_proof_complete"}

    with (
        ThreadPoolExecutor(max_workers=1) as m5_executor,
        ThreadPoolExecutor(max_workers=1) as bridge_executor,
    ):
        bridge_future = bridge_executor.submit(_long_bridge_repair)
        assert bridge_started.wait(timeout=0.2)
        proof_future = m5_executor.submit(_m5_proof)
        assert proof_future.result(timeout=0.2) == {
            "status": "m5_authority_proof_complete"
        }
        repeat_proof_future = m5_executor.submit(_m5_proof)
        assert repeat_proof_future.result(timeout=0.2) == {
            "status": "m5_authority_proof_complete"
        }
        assert not bridge_future.done()
        release_bridge.set()
        assert bridge_future.result(timeout=0.2) == "repaired"


def test_price_channel_clob_fetchers_are_budget_bound(monkeypatch) -> None:
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(lane.time, "monotonic", lambda: 100.0)
    seen: dict[str, object] = {}

    class FakeClob:
        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            seen["single_timeout"] = timeout
            return {"asset_id": token_id}

        def get_orderbook_snapshots(self, token_ids: list[str], *, timeout=None) -> dict:  # noqa: ANN001
            seen["batch_timeout"] = timeout
            return {token_id: {"asset_id": token_id} for token_id in token_ids}

    fetch_one, fetch_many = lane._budgeted_orderbook_fetchers(
        FakeClob(),
        deadline_monotonic=103.0,
    )

    assert fetch_one("tok-a") == {"asset_id": "tok-a"}
    assert fetch_many is not None
    assert fetch_many(["tok-b"]) == {"tok-b": {"asset_id": "tok-b"}}
    assert seen["single_timeout"] is not None
    assert seen["batch_timeout"] is not None


def test_price_channel_clob_timeout_fails_when_deadline_exhausted(monkeypatch) -> None:
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(lane.time, "monotonic", lambda: 100.0)

    try:
        lane._price_channel_clob_timeout(100.1)
    except TimeoutError as exc:
        assert "budget exhausted before CLOB fetch" in str(exc)
    else:  # pragma: no cover - explicit regression assertion
        raise AssertionError("expected exhausted price-channel CLOB budget to raise")


# ---------------------------------------------------------------------------
# Shared AST helpers
# ---------------------------------------------------------------------------

def _add_job_first_positional_names(source_path: Path) -> list[str]:
    """Return the first-positional-arg Name id of every `*.add_job(NAME, ...)` call."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            if node.args and isinstance(node.args[0], ast.Name):
                names.append(node.args[0].id)
    return names


def _add_job_ids(source_path: Path) -> list[str]:
    """Return every literal `id=` keyword across `*.add_job(..., id="X")` calls."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    ids: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_job":
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    ids.append(kw.value.value)
    return ids


def _called_func_names(source_path: Path) -> set[str]:
    """Every bare-name function CALL `foo(...)` in the file (executable code, not strings)."""
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


# ===========================================================================
# NO-REGRESSION INVARIANTS (the lift must preserve every property the order
# runtime depends on)
# ===========================================================================

def test_no_regression_price_channel_module_owns_the_lifted_producers():
    """The lifted PRODUCER logic lives in a trading-lane-free module the runtime reads from.

    The WS ingestor starter + the market-channel + user-channel/reconcile cycles must NOT
    vanish — they move host process. The order runtime stays a pure READER of the durable
    fill bridge + execution_feasibility_evidence; the WRITER side moves to
    src.ingest.price_channel_ingest.
    """
    assert _PRICE_CHANNEL_MODULE.exists(), (
        "src/ingest/price_channel_ingest.py must exist — it owns the lifted WS ingestor "
        "starter + the market-channel + user-channel/reconcile cycles."
    )
    import src.ingest.price_channel_ingest as pci

    for fn in _LIFTED_PRODUCERS:
        assert hasattr(pci, fn), f"src.ingest.price_channel_ingest must define {fn}"


def test_no_regression_durable_fill_bridge_scan_shared_by_both_processes():
    """The durable fill-bridge SCAN (the persisted truth) is importable by BOTH P3 and P1.

    I2 + §8 Step 3: the durable fill bridge is the persisted truth so NO fill is lost
    across the cutover. The P3 reconcile cycle WRITES it; the order-runtime BOOT recovery
    (_edli_boot_fill_bridge_recovery, which STAYS in src.main) READS/heals it on restart.
    Both must call the SAME scan helper — a duplicated copy would let one drift and orphan
    capital. The scan therefore lives in the lifted lane module and src.main imports it.
    """
    import src.ingest.price_channel_ingest as pci

    assert hasattr(pci, "_edli_durable_fill_bridge_scan"), (
        "the durable fill-bridge scan must live in src.ingest.price_channel_ingest so both "
        "the P3 reconcile cycle and src.main's boot recovery import the SAME persisted-"
        "truth healer."
    )
    # src.main's boot recovery must consume the SHARED scan (not a local duplicate).
    import src.main as main_mod

    boot_src = inspect.getsource(main_mod._edli_boot_fill_bridge_recovery)
    assert "_edli_durable_fill_bridge_scan" in boot_src, (
        "_edli_boot_fill_bridge_recovery (STAYS in P1) must still call the durable "
        "fill-bridge scan so a restart heals any orphaned confirmed fill."
    )
    # And src.main must NOT define its own copy of the scan (single source of truth).
    defined_in_main = (
        "_edli_durable_fill_bridge_scan" in main_mod.__dict__
        and getattr(getattr(main_mod, "_edli_durable_fill_bridge_scan"), "__module__", "")
        == "src.main"
    )
    assert not defined_in_main, (
        "_edli_durable_fill_bridge_scan must NOT be defined in src.main after the lift — "
        "src.main imports the single canonical copy from src.ingest.price_channel_ingest."
    )


def test_no_regression_order_runtime_keeps_boot_fill_bridge_recovery():
    """P1 MUST keep the boot fill-bridge recovery (it reads the durable bridge; §8 Step 3)."""
    import src.main as main_mod

    assert hasattr(main_mod, "_edli_boot_fill_bridge_recovery"), (
        "the order runtime must keep _edli_boot_fill_bridge_recovery — it reads the durable "
        "fill bridge at boot so no fill is dropped across the P3 cutover."
    )
    # And its asynchronous starter must still be invoked during boot. The
    # canonical recovery itself remains shared and unchanged; only scheduler
    # startup is decoupled from its historical scan.
    assert "_start_edli_boot_fill_bridge_recovery" in _called_func_names(_MAIN_PY), (
        "_start_edli_boot_fill_bridge_recovery must be CALLED at boot in src.main."
    )


def test_boot_fill_bridge_skips_cross_db_writer_when_read_only_probe_is_clean(monkeypatch):
    """Healthy boot must not serialize canonical writers to rediscover no debt."""
    import src.ingest.price_channel_ingest as pci
    import src.main as main_mod
    import src.state.db as db

    monkeypatch.setattr(
        pci,
        "_edli_durable_fill_bridge_candidate_ids_read_only",
        lambda *, limit: (),
    )

    def _writer_must_not_open(*args, **kwargs):
        raise AssertionError("clean fill-bridge admission must not open a writer")

    monkeypatch.setattr(db, "get_trade_connection_with_world_required", _writer_must_not_open)

    main_mod._edli_boot_fill_bridge_recovery()


def test_boot_fill_bridge_probe_uncertainty_keeps_buy_blocked_without_writer(monkeypatch):
    """Uncertain discovery must retry without starving held-monitor writers."""
    import src.ingest.price_channel_ingest as pci
    import src.main as main_mod
    import src.state.db as db

    monkeypatch.setattr(
        pci,
        "_edli_durable_fill_bridge_candidate_ids_read_only",
        lambda *, limit: (_ for _ in ()).throw(RuntimeError("probe uncertain")),
    )

    def _writer_must_not_open(*args, **kwargs):
        raise AssertionError("uncertain discovery must not take canonical writers")

    monkeypatch.setattr(
        db,
        "get_trade_connection_with_world_required",
        _writer_must_not_open,
    )

    assert main_mod._edli_boot_fill_bridge_recovery() is False


def test_boot_fill_bridge_writer_consumes_only_bounded_discovered_ids(monkeypatch):
    """Boot recovery never re-scans historical EDLI events while holding a writer."""
    import src.ingest.price_channel_ingest as pci
    import src.main as main_mod
    import src.state.db as db

    class _Conn:
        committed = False
        closed = False

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    conn = _Conn()
    discoveries = iter((("aggregate-a", "aggregate-b"), ()))
    scan_calls = []
    monkeypatch.setattr(
        pci,
        "_edli_durable_fill_bridge_candidate_ids_read_only",
        lambda *, limit: next(discoveries),
    )

    def _scan(actual_conn, *, now, limit, candidate_aggregate_ids):
        scan_calls.append((actual_conn, limit, candidate_aggregate_ids))
        return 2

    monkeypatch.setattr(pci, "_edli_durable_fill_bridge_scan", _scan)
    monkeypatch.setattr(
        db,
        "get_trade_connection_with_world_required",
        lambda *, write_class: conn,
    )

    assert main_mod._edli_boot_fill_bridge_recovery() is True

    assert conn.committed is True
    assert conn.closed is True
    assert scan_calls == [(conn, 2, ("aggregate-a", "aggregate-b"))]


def test_fill_bridge_discovery_treats_terminal_disposition_as_drained():
    """Settled/manual-review fills cannot keep boot BUY admission pending forever."""
    from src.events.edli_position_bridge import (
        DISPOSITION_SETTLED_MARKET,
        DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW,
        edli_bridge_position_id,
        edli_bridge_position_id_legacy,
    )
    from src.ingest.price_channel_ingest import (
        _edli_durable_fill_bridge_candidate_ids,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE edli_live_order_events (
            aggregate_id TEXT NOT NULL,
            event_sequence INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        );
        CREATE INDEX idx_edli_live_order_events_aggregate
            ON edli_live_order_events(aggregate_id, event_sequence);
        CREATE INDEX idx_edli_live_order_events_type
            ON edli_live_order_events(event_type, occurred_at);
        CREATE TABLE position_current (position_id TEXT PRIMARY KEY);
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            decision_id TEXT NOT NULL,
            position_id TEXT
        );
        CREATE TABLE edli_fill_bridge_dispositions (
            aggregate_id TEXT PRIMARY KEY,
            disposition TEXT
        );
        """
    )
    confirmed = json.dumps({"fill_authority_state": "FILL_CONFIRMED"})
    conn.executemany(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, payload_json, occurred_at
        ) VALUES (?, 1, 'UserTradeObserved', ?, '2026-08-23T00:00:00+00:00')
        """,
        (
            ("settled", confirmed),
            ("manual-review", confirmed),
            ("canonical-position", confirmed),
            ("legacy-position", confirmed),
            ("live-orphan", confirmed),
        ),
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?)",
        (
            (edli_bridge_position_id("canonical-position"),),
            (edli_bridge_position_id_legacy("legacy-position"),),
        ),
    )
    conn.executemany(
        """
        INSERT INTO edli_live_order_events (
            aggregate_id, event_sequence, event_type, payload_json, occurred_at
        ) VALUES (?, 1, 'DecisionProofAccepted', '{}', '2026-08-22T00:00:00+00:00')
        """,
        ((f"irrelevant-{index:05d}",) for index in range(25_000)),
    )
    conn.executemany(
        "INSERT INTO edli_fill_bridge_dispositions VALUES (?, ?)",
        (
            ("settled", DISPOSITION_SETTLED_MARKET),
            ("manual-review", DISPOSITION_UNRECOVERABLE_MANUAL_REVIEW),
        ),
    )

    progress_calls = 0

    def _fail_if_irrelevant_history_is_scanned():
        nonlocal progress_calls
        progress_calls += 1
        return int(progress_calls > 200)

    conn.set_progress_handler(_fail_if_irrelevant_history_is_scanned, 100)
    try:
        assert _edli_durable_fill_bridge_candidate_ids(conn, limit=8) == (
            "live-orphan",
        )
    finally:
        conn.set_progress_handler(None, 0)
    conn.close()


def test_boot_fill_bridge_recovery_does_not_block_scheduler_startup(monkeypatch):
    """Historical recovery runs off the boot thread and releases BUY only after success."""
    import src.main as main_mod

    entered = threading.Event()
    release = threading.Event()
    complete = threading.Event()
    monkeypatch.setattr(main_mod, "_edli_boot_fill_bridge_recovery_complete", complete)
    monkeypatch.setattr(main_mod, "_edli_boot_fill_bridge_recovery_thread", None)

    def _slow_success():
        entered.set()
        assert release.wait(timeout=2.0)
        return True

    monkeypatch.setattr(main_mod, "_edli_boot_fill_bridge_recovery", _slow_success)

    thread = main_mod._start_edli_boot_fill_bridge_recovery()

    assert thread is not None
    assert entered.wait(timeout=1.0)
    assert thread.is_alive()
    assert complete.is_set() is False
    assert main_mod._edli_live_entry_readiness_block({}) == (
        "entry_readiness:EDLI_BOOT_FILL_BRIDGE_RECOVERY_PENDING",
        {},
    )

    release.set()
    thread.join(timeout=2.0)
    assert thread.is_alive() is False
    assert complete.is_set() is True


def test_boot_fill_bridge_recovery_retries_before_buy_release(monkeypatch):
    """A failed pass cannot open BUY admission; the same owner retries to success."""
    import src.main as main_mod

    complete = threading.Event()
    attempts = []
    monkeypatch.setattr(main_mod, "_edli_boot_fill_bridge_recovery_complete", complete)
    monkeypatch.setattr(main_mod, "_edli_boot_fill_bridge_recovery_thread", None)
    monkeypatch.setattr(main_mod, "_EDLI_BOOT_FILL_BRIDGE_RETRY_SECONDS", 0.001)

    def _fail_then_succeed():
        attempts.append(len(attempts) + 1)
        return len(attempts) >= 2

    monkeypatch.setattr(main_mod, "_edli_boot_fill_bridge_recovery", _fail_then_succeed)

    thread = main_mod._start_edli_boot_fill_bridge_recovery()
    assert thread is not None
    thread.join(timeout=2.0)

    assert attempts == [1, 2]
    assert complete.is_set() is True


def test_no_regression_order_runtime_reads_current_feasibility_projection():
    """P1's pre-submit witness reads the current feasibility projection.

    A WS outage surfaces as stale/absent current rows. Append history must not
    silently restore executable authority after current projection is lost.
    """
    from src.engine import event_reactor_adapter as adapter

    assert hasattr(adapter, "_latest_market_channel_book_rows"), (
        "the order runtime must keep its pre-submit feasibility reader "
        "(_latest_market_channel_book_rows) — the DB-mediated I2 read side P1 keeps."
    )
    reader_src = inspect.getsource(adapter._latest_market_channel_book_rows)
    assert "execution_feasibility_latest" in reader_src, (
        "the order runtime's pre-submit witness must SELECT the current projection."
    )
    assert "execution_feasibility_evidence" not in reader_src, (
        "append history cannot restore current book authority."
    )


def test_price_channel_quote_writer_updates_current_without_append_history():
    """Recurring quote ingestion must not regrow the retired append journal."""
    from src.events.triggers.market_channel_ingestor import MarketChannelIngestor

    writer_src = inspect.getsource(MarketChannelIngestor.write_prepared_quote_events)
    assert "append_evidence=False" in writer_src, (
        "the high-rate quote writer must update execution_feasibility_latest "
        "without appending execution_feasibility_evidence"
    )


def test_no_regression_src_main_still_imports():
    """src.main MUST still import successfully with the WS thread + cycles removed."""
    import src.main as main_mod

    assert main_mod is not None


def test_no_regression_price_channel_module_is_not_a_trading_lane_import():
    """The lifted producer module must NOT import the trading lane (failure-domain isolation).

    §criterion 3 / §9: a WS-ingest fault must not raise into the reactor and a trading bug
    must not blind WS ingest. If price_channel_ingest imported src.main / src.engine /
    src.execution / src.strategy, the new P3 process would drag the whole trading lane in,
    re-coupling the failure domains the split exists to separate — AND re-importing the
    order daemon's ws_gap_guard submit-latch reader into the producer process.
    """
    src = _PRICE_CHANNEL_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = (
        "src.main", "src.engine", "src.execution", "src.strategy", "src.signal",
    )
    offending: list[str] = []
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == p or alias.name.startswith(p + ".") for p in forbidden_prefixes):
                    offending.append(alias.name)
            continue
        if mod and any(mod == p or mod.startswith(p + ".") for p in forbidden_prefixes):
            offending.append(mod)
    assert not offending, (
        f"src.ingest.price_channel_ingest must not import the trading lane (failure-domain "
        f"isolation, §criterion 3); offending imports: {offending}"
    )


def test_no_regression_new_process_uses_sanctioned_db_path_no_independent_cross_db():
    """The lifted producer's cross-DB write uses the sanctioned ATTACH path (INV-37).

    The reconcile cycle's fill-bridge pass writes position_current/position_events on a
    trade-connection-with-world-ATTACHed (get_trade_connection_with_world_required) — the
    sanctioned ATTACH+SAVEPOINT cross-DB path. It must NOT hand-roll a raw independent
    connection to a second DB.
    """
    src = _PRICE_CHANNEL_MODULE.read_text(encoding="utf-8")
    assert "get_trade_connection_with_world_required" in src, (
        "the reconcile cycle's fill-bridge cross-DB write must go through the sanctioned "
        "get_trade_connection_with_world_required ATTACH path (INV-37)."
    )
    assert "sqlite3.connect" not in src, (
        "the producer must not open a raw independent connection; cross-DB writes use the "
        "sanctioned ATTACH+SAVEPOINT path (INV-37)."
    )


def test_open_position_tokens_are_market_channel_seed_priority():
    from src.ingest.price_channel_ingest import _edli_held_position_priority_token_ids

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            token_id TEXT,
            no_token_id TEXT,
            chain_shares REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?,?,?,?,?)",
        [
            ("active-1", "active", "yes-active", "no-active", 5.0),
            ("day0-1", "day0_window", None, "no-day0", 3.0),
            ("exit-1", "pending_exit", "yes-exit", None, 2.0),
            ("chain-quarantine-1", "quarantined", "yes-quarantine", "no-quarantine", 29.14),
            ("zero-quarantine-1", "quarantined", "yes-zero-quarantine", "no-zero-quarantine", 0.0),
            ("chain-voided-1", "voided", "yes-voided", "no-voided", 4.0),
            ("closed-1", "economically_closed", "yes-closed", "no-closed", 7.0),
        ],
    )

    assert _edli_held_position_priority_token_ids(conn) == {
        "yes-active",
        "no-active",
        "no-day0",
        "yes-exit",
        "yes-quarantine",
        "no-quarantine",
        "yes-voided",
        "no-voided",
    }


def test_unsettled_schema22_exit_token_stays_market_channel_priority():
    from src.ingest.price_channel_ingest import (
        _edli_current_global_exit_audit_token_ids,
        _edli_held_position_priority_token_ids,
    )

    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            settled_at TEXT,
            direction TEXT,
            token_id TEXT,
            no_token_id TEXT
        );
        CREATE TABLE position_events (
            position_id TEXT,
            sequence_no INTEGER,
            event_type TEXT,
            command_id TEXT,
            payload_json TEXT
        );
        CREATE TABLE venue_commands (
            command_id TEXT,
            position_id TEXT,
            intent_kind TEXT,
            state TEXT,
            token_id TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?,?,?,?,?,?)",
        [
            ("current", "economically_closed", None, "buy_yes", "yes-current", "no-current"),
            ("legacy", "economically_closed", None, "buy_yes", "yes-legacy", "no-legacy"),
            ("settled", "economically_closed", "2026-08-13T02:00:00+00:00", "buy_yes", "yes-settled", "no-settled"),
            ("pending", "pending_exit", None, "buy_no", "yes-pending", "no-pending"),
        ],
    )
    for position_id, schema_version in (("current", 22), ("legacy", 21), ("settled", 22)):
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            (
                position_id,
                1,
                "EXIT_INTENT",
                None,
                json.dumps(
                    {
                        "exit_intent_capital_certificate": {
                            "action": "SELL",
                            "global_auction_receipt": {
                                "schema_version": schema_version
                            },
                        }
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?,?,?,?,?)",
            (
                f"command-{position_id}",
                position_id,
                "EXIT",
                "FILLED",
                f"sold-{position_id}",
            ),
        )
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?)",
            (
                position_id,
                2,
                "EXIT_ORDER_FILLED",
                f"command-{position_id}",
                "{}",
            ),
        )

    conn.execute(
        "INSERT INTO position_events VALUES (?,?,?,?,?)",
        (
            "pending",
            1,
            "EXIT_INTENT",
            None,
            json.dumps(
                {
                    "exit_intent_capital_certificate": {
                        "action": "SELL",
                        "global_auction_receipt": {"schema_version": 22},
                    }
                }
            ),
        ),
    )

    assert _edli_held_position_priority_token_ids(conn) == {
        "sold-current",
        "no-pending",
        "yes-pending",
    }
    assert _edli_current_global_exit_audit_token_ids() == {
        "sold-current",
        "no-pending",
    }


def test_global_exit_audit_appends_only_full_depth_buy_projection():
    from src.ingest.price_channel_ingest import (
        _edli_append_global_exit_audit_quote_evidence,
    )
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    base = (
        "evidence-1",
        "event-1",
        "condition-1",
        "sold-token",
        "YES",
        "2026-08-13T08:44:00+00:00",
        "book-1",
        0.42,
        0.45,
        "2026-08-13T08:44:00+00:00",
        1,
    )
    conn.execute(
        "INSERT INTO execution_feasibility_latest ("
        "evidence_id,event_id,condition_id,token_id,outcome_label,direction,"
        "quote_seen_at,book_hash_before,best_bid_before,best_ask_before,"
        "depth_before_json,created_at,schema_version) VALUES (?,?,?,?,?,'buy_yes',"
        "?,?,?,?,?,?,?)",
        (*base[:5], *base[5:9], '{"bids":[["0.42","5"]],"asks":[]}', *base[9:]),
    )
    conn.execute(
        "INSERT INTO execution_feasibility_latest ("
        "evidence_id,event_id,condition_id,token_id,outcome_label,direction,"
        "quote_seen_at,book_hash_before,best_bid_before,best_ask_before,"
        "depth_before_json,created_at,schema_version) VALUES ("
        "'evidence-2','event-1','condition-1','sold-token','YES','sell_yes',"
        "'2026-08-13T08:44:00+00:00','book-1',0.42,0.45,NULL,"
        "'2026-08-13T08:44:00+00:00',1)"
    )

    assert _edli_append_global_exit_audit_quote_evidence(
        conn, {"sold-token"}
    ) == 1
    assert conn.execute(
        "SELECT token_id,direction,depth_before_json "
        "FROM execution_feasibility_evidence"
    ).fetchall() == [
        ("sold-token", "buy_yes", '{"bids":[["0.42","5"]],"asks":[]}')
    ]
    assert _edli_append_global_exit_audit_quote_evidence(
        conn, {"sold-token"}
    ) == 0


def test_open_rest_tokens_are_market_channel_seed_priority():
    from src.ingest.price_channel_ingest import _edli_open_rest_priority_token_ids

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            intent_kind TEXT,
            state TEXT,
            token_id TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO venue_commands VALUES (?,?,?,?)",
        [
            ("posting", "ENTRY", "POSTING", "tok-posting"),
            ("acked", "ENTRY", "ACKED", "tok-acked"),
            ("partial", "ENTRY", "PARTIAL", "tok-partial"),
            ("exit", "EXIT", "ACKED", "tok-exit"),
            ("filled", "ENTRY", "FILLED", "tok-filled"),
            ("blank", "ENTRY", "ACKED", ""),
        ],
    )

    assert _edli_open_rest_priority_token_ids(conn) == {
        "tok-posting",
        "tok-acked",
        "tok-partial",
    }


def test_candidate_priority_uses_bounded_recent_row_window():
    from src.ingest.price_channel_ingest import _edli_candidate_priority_token_ids

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE no_trade_regret_events (
            regret_event_id TEXT PRIMARY KEY,
            token_id TEXT,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX idx_no_trade_regret_created_at
            ON no_trade_regret_events(created_at DESC)
        """
    )
    recent = datetime.now(timezone.utc).isoformat()
    rows = [(f"old-{idx}", f"stale-{idx}", "2026-01-01T00:00:00+00:00") for idx in range(250)]
    rows.extend(
        [
            ("recent-1", "tok-a", recent),
            ("recent-2", "tok-b", recent),
            ("recent-3", "tok-a", recent),
            ("recent-4", "tok-c", recent),
        ]
    )
    conn.executemany("INSERT INTO no_trade_regret_events VALUES (?,?,?)", rows)
    traces: list[str] = []
    conn.set_trace_callback(traces.append)

    tokens = _edli_candidate_priority_token_ids(conn, lookback_hours=24.0, limit=3)

    assert tokens == ["tok-c", "tok-a", "tok-b"]
    regret_reads = [
        sql
        for sql in traces
        if "FROM no_trade_regret_events" in sql and "sqlite_master" not in sql
    ]
    assert regret_reads
    assert all("GROUP BY" not in sql.upper() for sql in regret_reads)
    assert all(
        "ORDER BY CREATED_AT DESC, ROWID DESC" in sql.upper()
        for sql in regret_reads
    )
    plan = conn.execute(f"EXPLAIN QUERY PLAN {regret_reads[0]}").fetchall()
    assert any(
        "idx_no_trade_regret_created_at" in str(row[3])
        for row in plan
    )


def test_priority_tokens_expand_to_complete_weather_families():
    from src.ingest.price_channel_ingest import _edli_priority_family_token_ids

    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL
        );
        INSERT INTO executable_market_snapshot_latest VALUES
            ('condition-a', 'a-yes', 'a-yes', 'a-no'),
            ('condition-a', 'a-no', 'a-yes', 'a-no'),
            ('condition-b', 'b-yes', 'b-yes', 'b-no'),
            ('condition-b', 'b-no', 'b-yes', 'b-no'),
            ('condition-other', 'other-yes', 'other-yes', 'other-no');
        """
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.executescript(
        """
        CREATE TABLE market_events (
            condition_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL
        );
        INSERT INTO market_events VALUES
            ('condition-a', 'Paris', '2026-07-17', 'high'),
            ('condition-b', 'Paris', '2026-07-17', 'high'),
            ('condition-other', 'Paris', '2026-07-18', 'high');
        """
    )

    expanded = _edli_priority_family_token_ids(
        trade,
        forecasts,
        {"a-no"},
    )

    assert expanded == {"a-yes", "a-no", "b-yes", "b-no"}


def test_priority_family_expansion_never_drops_seed_tokens_at_limit():
    from src.ingest.price_channel_ingest import _edli_priority_family_token_ids

    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL
        );
        INSERT INTO executable_market_snapshot_latest VALUES
            ('condition-a', 'seed-a', 'seed-a', 'expanded-a'),
            ('condition-b', 'seed-b', 'seed-b', 'expanded-b');
        """
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.executescript(
        """
        CREATE TABLE market_events (
            condition_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL
        );
        INSERT INTO market_events VALUES
            ('condition-a', 'Paris', '2026-07-17', 'high'),
            ('condition-b', 'Paris', '2026-07-17', 'high');
        """
    )

    expanded = _edli_priority_family_token_ids(
        trade,
        forecasts,
        {"seed-a", "seed-b"},
        limit=2,
    )

    assert expanded == {"seed-a", "seed-b"}


def test_market_channel_seed_first_includes_all_money_path_priority_tokens():
    from src.ingest.price_channel_ingest import _edli_market_channel_seed_first_token_ids

    ordered = _edli_market_channel_seed_first_token_ids(
        held_priority_token_ids={"held-yes", "held-no"},
        open_rest_priority_token_ids={"rest-no"},
        day0_priority_token_ids={"day0-yes", "day0-no"},
        candidate_priority_token_ids={"candidate-yes", "candidate-no"},
    )

    assert ordered == (
        "held-no",
        "held-yes",
        "rest-no",
        "day0-no",
        "day0-yes",
        "candidate-no",
        "candidate-yes",
    )


def test_market_channel_seed_first_falls_back_to_candidates_without_open_positions():
    from src.ingest.price_channel_ingest import _edli_market_channel_seed_first_token_ids

    assert set(
        _edli_market_channel_seed_first_token_ids(
            held_priority_token_ids=set(),
            candidate_priority_token_ids={"candidate-yes", "candidate-no"},
        )
    ) == {"candidate-yes", "candidate-no"}


def test_market_channel_depth_repair_excludes_broad_day0_until_candidate():
    from src.ingest.price_channel_ingest import (
        _edli_market_channel_depth_repair_token_ids,
    )

    assert _edli_market_channel_depth_repair_token_ids(
        held_priority_token_ids={"held"},
        open_rest_priority_token_ids={"rest"},
        candidate_priority_token_ids={"candidate"},
    ) == ("held", "rest", "candidate")


def test_current_day0_priority_uses_each_city_local_date(monkeypatch):
    from types import SimpleNamespace

    from src.ingest.price_channel_ingest import (
        _edli_current_day0_priority_token_ids,
    )

    monkeypatch.setattr(
        "src.config.runtime_cities_by_name",
        lambda: {
            "Paris": SimpleNamespace(timezone="Europe/Paris"),
            "New York": SimpleNamespace(timezone="America/New_York"),
        },
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.executescript(
        """
        CREATE TABLE market_events (
            condition_id TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL
        );
        INSERT INTO market_events VALUES
            ('paris-current', 'Paris', '2026-07-18', 'high'),
            ('paris-stale', 'Paris', '2026-07-17', 'high'),
            ('ny-current', 'New York', '2026-07-17', 'low');
        """
    )
    trade = sqlite3.connect(":memory:")
    trade.executescript(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER
        );
        INSERT INTO executable_market_snapshot_latest VALUES
            ('paris-current', 'paris-yes', 1, 0, 1),
            ('paris-current', 'paris-no', 1, 0, 1),
            ('paris-stale', 'paris-stale-yes', 1, 0, 1),
            ('ny-current', 'ny-yes', 1, 0, 1),
            ('ny-current', 'ny-closed-no', 0, 1, 0);
        """
    )

    tokens = _edli_current_day0_priority_token_ids(
        trade,
        forecasts,
        checked_at=datetime.fromisoformat("2026-07-17T23:30:00+00:00"),
    )

    assert tokens == ("ny-yes", "paris-no", "paris-yes")


def test_price_channel_money_path_tokens_resolve_to_redecision_families():
    from src.ingest.price_channel_ingest import _edli_money_path_family_keys_for_tokens

    trade = sqlite3.connect(":memory:")
    trade.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade.execute(
        "INSERT INTO position_current VALUES (?,?,?,?,?,?,?)",
        ("pos-1", "active", "Paris", "2026-06-20", "low", "held-yes", "held-no"),
    )
    trade.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
        ("0xrest", "rest-no", "rest-yes", "rest-no"),
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE market_events (
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT
        )
        """
    )
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?)",
        ("0xrest", "Tokyo", "2026-06-20", "high"),
    )

    assert _edli_money_path_family_keys_for_tokens(
        trade,
        forecasts,
        {"held-no", "rest-no", "unknown-token"},
    ) == {
        ("Paris", "2026-06-20", "low"),
        ("Tokyo", "2026-06-20", "high"),
    }


def test_price_channel_money_path_latest_snapshot_does_not_resurrect_old_tokens():
    from src.ingest.price_channel_ingest import _edli_money_path_family_keys_for_tokens

    trade = sqlite3.connect(":memory:")
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade.execute(
        """
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
        ("condition-a", "old-no", "old-yes", "old-no"),
    )
    trade.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?)",
        ("condition-a", "new-no", "new-yes", "new-no"),
    )
    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE market_events (
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT
        )
        """
    )
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?)",
        ("condition-a", "Paris", "2026-07-18", "high"),
    )

    assert _edli_money_path_family_keys_for_tokens(trade, forecasts, {"old-no"}) == set()
    assert _edli_money_path_family_keys_for_tokens(
        trade,
        forecasts,
        {"new-no"},
    ) == {("Paris", "2026-07-18", "high")}


def test_price_channel_held_tokens_resolve_separately_from_entry_candidates():
    from src.ingest.price_channel_ingest import _edli_held_family_keys_for_tokens

    trade = sqlite3.connect(":memory:")
    trade.execute(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade.executemany(
        "INSERT INTO position_current VALUES (?,?,?,?,?,?,?)",
        [
            ("pos-1", "active", "Paris", "2026-06-20", "low", "held-yes", "held-no"),
            ("pos-2", "settled", "Tokyo", "2026-06-20", "high", "settled-yes", "settled-no"),
        ],
    )

    assert _edli_held_family_keys_for_tokens(
        trade,
        {"held-no", "settled-no", "unknown-token"},
    ) == {("Paris", "2026-06-20", "low")}


def _seed_minimal_venue_order_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE venue_order_facts (
            fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            venue_order_id TEXT NOT NULL,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            local_sequence INTEGER NOT NULL
        )
        """
    )


def test_price_channel_resting_order_tokens_resolve_bypassing_screen():
    from src.ingest.price_channel_ingest import (
        _edli_own_resting_order_token_ids,
        _edli_resting_family_keys_for_tokens,
    )

    trade = sqlite3.connect(":memory:")
    _seed_minimal_venue_order_tables(trade)
    trade.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
        ("0xrest", "resting-yes", "resting-yes", "resting-no"),
    )
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?)",
        ("cmd-rest", "resting-yes", "BUY", 0.5, "ACKED",
         "2026-06-20T00:00:00", "2026-06-20T00:00:00"),
    )
    trade.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, source, observed_at, local_sequence)"
        " VALUES (?,?,?,?,?,?)",
        ("vof-1", "cmd-rest", "RESTING", "REST", "2026-06-20T00:00:00", 1),
    )
    # A resting command whose latest fact has already left the open states
    # (cancel-confirmed) must NOT resolve — only the latest local_sequence
    # row per command governs "open".
    trade.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?)",
        ("cmd-cancelled", "cancelled-token", "SELL", 0.6, "ACKED",
         "2026-06-20T00:00:00", "2026-06-20T00:00:00"),
    )
    trade.executemany(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, source, observed_at, local_sequence)"
        " VALUES (?,?,?,?,?,?)",
        [
            ("vof-2a", "cmd-cancelled", "RESTING", "REST", "2026-06-20T00:00:00", 1),
            ("vof-2b", "cmd-cancelled", "CANCEL_CONFIRMED", "REST", "2026-06-20T00:01:00", 2),
        ],
    )

    forecasts = sqlite3.connect(":memory:")
    forecasts.execute(
        """
        CREATE TABLE market_events (
            condition_id TEXT,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT
        )
        """
    )
    forecasts.execute(
        "INSERT INTO market_events VALUES (?,?,?,?)",
        ("0xrest", "Denver", "2026-06-20", "low"),
    )

    resolved_tokens = _edli_own_resting_order_token_ids(
        trade, {"resting-yes", "cancelled-token", "unknown-token"}
    )
    assert resolved_tokens == {"resting-yes"}

    assert _edli_resting_family_keys_for_tokens(
        trade,
        forecasts,
        {"resting-yes", "cancelled-token", "unknown-token"},
    ) == {("Denver", "2026-06-20", "low")}


def test_price_channel_redecision_emit_routes_nonheld_entries_through_screen():
    from src.events import price_channel_redecision_router as router

    src = inspect.getsource(router._edli_price_channel_redecision_events_for_events)

    assert "held_families = _edli_held_family_keys_for_tokens" in src
    assert "entry_families = _edli_screened_entry_family_keys_for_price_channel" in src
    assert "family_keys=clean_families" in inspect.getsource(
        router._edli_screened_entry_family_keys_for_price_channel
    )
    assert "forecast_only_admissible=True" in inspect.getsource(
        router._edli_screened_entry_family_keys_for_price_channel
    )
    assert "set(families) - set(held_families)" in src
    assert "held_families.intersection_update(families)" in src
    assert "resting_families = _edli_resting_family_keys_for_tokens" in src
    assert "if unresolved_families:" in src
    assert "resting_families.intersection_update(unresolved_families)" in src
    assert "families = held_families | entry_families | resting_families" in src
    assert src.index("families = held_families | entry_families") < src.index(
        "trigger.build_committed_snapshot_events"
    )
    assert src.index("resting_families = _edli_resting_family_keys_for_tokens") < src.index(
        "trigger.build_committed_snapshot_events"
    )
    assert "phase_filter_exempt_families=held_families | resting_families" in src
    # Resting bucket is resolved AFTER (independently of) the entry screen call,
    # never fed as one of its inputs.
    assert src.index("entry_families = _edli_screened_entry_family_keys_for_price_channel") < src.index(
        "resting_families = _edli_resting_family_keys_for_tokens"
    )


def test_price_channel_redecision_carries_exact_changed_tokens():
    from src.events import price_channel_redecision_router as router
    from src.events.opportunity_event import make_opportunity_event

    at = "2026-07-17T02:00:00+00:00"
    event = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="weather:seoul:2026-07-17:high",
        source="price_channel",
        observed_at=at,
        available_at=at,
        received_at=at,
        payload={"city": "Seoul", "target_date": "2026-07-17", "metric": "high"},
    )

    rebuilt = router._edli_redecision_event_with_origin(
        event,
        "market_price",
        changed_token_ids=("token-b", "token-a", "token-b", "", None),
    )
    payload = json.loads(rebuilt.payload_json)

    assert payload["redecision_origin"] == "market_price"
    assert payload["price_changed_token_ids"] == ["token-a", "token-b"]


def test_price_channel_redecision_sink_keeps_world_version_proof_through_write(
    monkeypatch,
):
    from src.events import price_channel_redecision_router as router
    from src.ingest import price_channel_ingest
    from src.runtime import reactor_wake
    from src.state import db

    order: list[str] = []

    class Redecision:
        event_id = "evt-price-1"

    class ReadConnection:
        def __init__(self, name: str) -> None:
            self.name = name
            order.append(f"open:{name}")

        def execute(self, sql: str):
            assert self.name == "world"
            assert sql == "PRAGMA data_version"
            return types.SimpleNamespace(fetchone=lambda: (7,))

        def close(self) -> None:
            order.append(f"close:{self.name}")

    class WriteConnection:
        def commit(self) -> None:
            order.append("commit:world")

    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: ReadConnection("world"))
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: ReadConnection("trade"))
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda: ReadConnection("forecasts"),
    )

    def build(world, trade, forecasts, events, **_kwargs):  # noqa: ANN001
        assert [world.name, trade.name, forecasts.name] == ["world", "trade", "forecasts"]
        assert events == ["quote"]
        order.append("build")
        return [Redecision()]

    monkeypatch.setattr(router, "_edli_price_channel_redecision_events_for_events", build)

    @contextlib.contextmanager
    def world_writer(*, owner: str):
        assert owner == "price_channel_redecision_emit"
        order.append("enter:world-writer")
        try:
            yield WriteConnection()
        finally:
            order.append("exit:world-writer")

    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_price_channel_world_write_connection",
        world_writer,
    )

    def write(_conn, events, *, recheck_pending: bool):  # noqa: ANN001
        assert len(events) == 1
        assert events[0].event_id == "evt-price-1"
        assert recheck_pending is False
        order.append("write:redecision")
        return ("evt-price-1",)

    monkeypatch.setattr(
        router,
        "_edli_write_price_channel_redecision_event_ids",
        write,
    )
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **kwargs: order.append(f"wake:{kwargs}"),
    )

    router._edli_price_channel_redecision_sink()(["quote"])

    assert order == [
        "open:world",
        "open:trade",
        "open:forecasts",
        "build",
        "enter:world-writer",
        "write:redecision",
        "commit:world",
        "exit:world-writer",
        "close:forecasts",
        "close:trade",
        "close:world",
        "wake:{'source': 'price_channel_redecision_router', "
        "'reason': 'market_price_advanced', 'event_ids': ('evt-price-1',)}",
    ]


def test_price_channel_redecision_retries_when_world_changes_before_write(
    monkeypatch,
):
    from src.events import price_channel_redecision_router as router
    from src.ingest import price_channel_ingest
    from src.runtime import reactor_wake
    from src.state import db

    versions = iter((11, 12))
    order: list[str] = []

    class ReadConnection:
        def __init__(self, name: str) -> None:
            self.name = name

        def execute(self, sql: str):
            assert self.name == "world"
            assert sql == "PRAGMA data_version"
            return types.SimpleNamespace(fetchone=lambda: (next(versions),))

        def close(self) -> None:
            order.append(f"close:{self.name}")

    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: ReadConnection("world"))
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: ReadConnection("trade"))
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda: ReadConnection("forecasts"),
    )
    monkeypatch.setattr(
        router,
        "_edli_price_channel_redecision_events_for_events",
        lambda *_args, **_kwargs: [types.SimpleNamespace(event_id="evt-raced")],
    )

    @contextlib.contextmanager
    def world_writer(*, owner: str):
        assert owner == "price_channel_redecision_emit"
        order.append("enter:world-writer")
        try:
            yield types.SimpleNamespace(commit=lambda: order.append("commit:world"))
        finally:
            order.append("exit:world-writer")

    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_price_channel_world_write_connection",
        world_writer,
    )
    monkeypatch.setattr(
        router,
        "_edli_write_price_channel_redecision_event_ids",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("changed WORLD snapshot must not write")
        ),
    )
    monkeypatch.setattr(
        reactor_wake,
        "publish_reactor_wake",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("uncommitted event must not wake")
        ),
    )

    with pytest.raises(
        router.PriceChannelRedecisionSnapshotChanged,
        match="WORLD changed",
    ):
        router._edli_price_channel_redecision_sink()(["quote"])

    assert order == [
        "enter:world-writer",
        "exit:world-writer",
        "close:forecasts",
        "close:trade",
        "close:world",
    ]


def test_price_channel_redecision_world_writer_is_bounded_and_preopened(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as pci
    from src.state import db

    order: list[str] = []

    class Connection:
        in_transaction = False

        def execute(self, sql: str):
            order.append(f"sql:{sql}")
            if sql == "BEGIN IMMEDIATE":
                self.in_transaction = True
            return self

        def rollback(self) -> None:
            order.append("rollback")
            self.in_transaction = False

        def close(self) -> None:
            order.append("close")

    class Mutex:
        def acquire(self, *, timeout: float) -> bool:
            order.append(f"acquire:{timeout}")
            return True

        def release(self) -> None:
            order.append("release")

    conn = Connection()
    monkeypatch.setattr(
        db,
        "get_world_connection",
        lambda **_kwargs: order.append("open") or conn,
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "_world_write_mutex",
        lambda: Mutex(),
    )
    monkeypatch.setattr(
        pci,
        "_bound_price_channel_sqlite_wait",
        lambda _conn, *, timeout_ms: order.append(f"busy:{timeout_ms}"),
    )

    with pci._edli_price_channel_world_write_connection(owner="price-redecision"):
        order.append("write")
        conn.in_transaction = False

    timeout_ms = pci.PRICE_CHANNEL_REDECISION_WORLD_WRITE_TIMEOUT_MS
    assert order == [
        "open",
        "sql:PRAGMA wal_autocheckpoint=0",
        f"busy:{timeout_ms}",
        f"acquire:{timeout_ms / 1000.0}",
        "sql:BEGIN IMMEDIATE",
        "write",
        "release",
        "close",
    ]


def test_price_channel_redecision_world_writer_defers_without_waiting(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as pci
    from src.state import db

    class Connection:
        def execute(self, sql: str):
            self.sql.append(sql)
            return self

        def close(self) -> None:
            self.closed = True

    class BusyMutex:
        def acquire(self, *, timeout: float) -> bool:
            self.timeout = timeout
            return False

    conn = Connection()
    conn.closed = False
    conn.sql = []
    mutex = BusyMutex()
    monkeypatch.setattr(db, "get_world_connection", lambda **_kwargs: conn)
    monkeypatch.setattr(
        market_channel_ingestor,
        "_world_write_mutex",
        lambda: mutex,
    )
    monkeypatch.setattr(
        pci,
        "_bound_price_channel_sqlite_wait",
        lambda _conn, *, timeout_ms: None,
    )

    with pytest.raises(TimeoutError, match="WORLD writer busy"):
        with pci._edli_price_channel_world_write_connection(owner="price-redecision"):
            raise AssertionError("busy producer must not enter the write unit")

    assert mutex.timeout == pci.PRICE_CHANNEL_REDECISION_WORLD_WRITE_TIMEOUT_MS / 1000.0
    assert conn.sql == ["PRAGMA wal_autocheckpoint=0"]
    assert conn.closed is True


def _price_redecision_writer_events():
    from src.events.opportunity_event import make_opportunity_event

    return [
        make_opportunity_event(
            event_type="EDLI_REDECISION_PENDING",
            entity_key=f"Paris|2026-07-28|{metric}|run-{index}",
            source="market-price",
            observed_at="2026-07-28T00:00:00+00:00",
            available_at="2026-07-28T00:00:00+00:00",
            received_at=f"2026-07-28T00:00:0{index}+00:00",
            payload={
                "city": "Paris",
                "target_date": "2026-07-28",
                "metric": metric,
                "index": index,
            },
        )
        for index, metric in enumerate(("high", "low", "high"), start=1)
    ]


def _file_backed_world_writer(monkeypatch, tmp_path, traces):
    from src.state import db
    from src.state.db import init_schema

    world_path = tmp_path / "zeus-world.db"
    monkeypatch.setattr(db, "ZEUS_WORLD_DB_PATH", world_path)
    setup = db.get_world_connection()
    init_schema(setup)
    setup.commit()
    setup.close()
    open_world = db.get_world_connection

    def traced_world(**kwargs):
        conn = open_world(**kwargs)
        conn.set_trace_callback(
            lambda sql: traces.append(
                (sql, db.world_mutex_is_held(), time.monotonic_ns())
            )
        )
        return conn

    monkeypatch.setattr(db, "get_world_connection", traced_world)
    return db, world_path


def _price_writer_phase_messages(caplog):
    return [
        record.message
        for record in caplog.records
        if record.message.startswith("price_channel_world_writer")
        and " over_budget " not in record.message
    ]


def _phase_name(message: str) -> str:
    return message.split(" phase=", 1)[1].split(" ", 1)[0]


def _telemetry_ms(message: str, field: str) -> float:
    return float(message.split(f" {field}=", 1)[1].split(" ", 1)[0])


def _telemetry_ns(message: str, field: str) -> int:
    return int(message.split(f" {field}=", 1)[1].split(" ", 1)[0])


def test_price_channel_redecision_real_flock_transaction_is_bounded_atomic_and_idempotent(
    monkeypatch,
    tmp_path,
    caplog,
):
    """Real file-backed WORLD writes keep all metadata reads before the flock."""
    from src.events.event_writer import EventWriter
    from src.ingest import price_channel_ingest as pci

    caplog.set_level("DEBUG", logger="zeus.price_channel_ingest")
    traces: list[tuple[str, bool, int]] = []
    db, world_path = _file_backed_world_writer(monkeypatch, tmp_path, traces)
    events = _price_redecision_writer_events()
    observer = sqlite3.connect(world_path)

    with pci._edli_price_channel_world_write_connection(
        owner="price_channel_redecision_emit"
    ) as world:
        assert db.world_mutex_is_held() is True
        lock_path = world_path.with_name(world_path.name + ".writer-lock.live")
        with lock_path.open("a+") as contender:
            with pytest.raises(BlockingIOError):
                fcntl.flock(
                    contender.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,
                )
        first = EventWriter(world).write_many(events)
        assert (
            observer.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0]
            == 0
        )
        assert (
            observer.execute(
                "SELECT COUNT(*) FROM opportunity_event_processing"
            ).fetchone()[0]
            == 0
        )
        world.commit()
    observer.close()

    assert [result.inserted for result in first] == [True, True, True]
    assert db.world_write_mutex().locked() is False
    assert lock_path.exists()

    metadata_reads = [
        (sql, held, at_ns)
        for sql, held, at_ns in traces
        if "database_list" in sql.lower() or "sqlite_master" in sql.lower()
    ]
    assert any("database_list" in sql.lower() for sql, _held, _at_ns in metadata_reads)
    assert any("sqlite_master" in sql.lower() for sql, _held, _at_ns in metadata_reads)
    assert all(held is False for _sql, held, _at_ns in metadata_reads)
    locked_traces = [(sql, at_ns) for sql, held, at_ns in traces if held]
    assert max(at_ns for _sql, _held, at_ns in metadata_reads) < min(
        at_ns for _sql, at_ns in locked_traces
    )
    locked_sql = [sql for sql, _at_ns in locked_traces]
    assert not any(
        "database_list" in sql.lower() or "sqlite_master" in sql.lower()
        for sql in locked_sql
    )
    assert sum(
        "insert or ignore into opportunity_events" in sql.lower()
        for sql in locked_sql
    ) == 3
    assert sum(
        "insert or ignore into opportunity_event_processing" in sql.lower()
        for sql in locked_sql
    ) == 3
    assert any(sql.lstrip().upper().startswith("BEGIN IMMEDIATE") for sql in locked_sql)
    assert any(sql.lstrip().upper().startswith("COMMIT") for sql in locked_sql)

    messages = _price_writer_phase_messages(caplog)
    assert [_phase_name(message) for message in messages] == [
        "acquire",
        "begin",
        "write",
        "transaction_closed",
        "release",
    ]
    assert [
        _telemetry_ns(message, "monotonic_ns") for message in messages
    ] == sorted(_telemetry_ns(message, "monotonic_ns") for message in messages)
    assert _telemetry_ms(messages[-1], "hold_ms") < 750.0
    assert "transaction=caller_closed" in messages[-1]

    traces.clear()
    caplog.clear()
    with pci._edli_price_channel_world_write_connection(
        owner="price_channel_redecision_emit"
    ) as world:
        duplicate = EventWriter(world).write_many(events)
        world.commit()

    assert [result.duplicate for result in duplicate] == [True, True, True]
    verify = sqlite3.connect(world_path)
    assert verify.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 3
    assert (
        verify.execute(
            "SELECT COUNT(*) FROM opportunity_event_processing "
            "WHERE processing_status = 'pending'"
        ).fetchone()[0]
        == 3
    )
    assert {
        row[0]
        for row in verify.execute(
            "SELECT event_id FROM opportunity_event_processing"
        ).fetchall()
    } == {event.event_id for event in events}
    verify.close()


def test_price_channel_redecision_real_flock_active_rollback_retries_pending(
    monkeypatch,
    tmp_path,
    caplog,
):
    from src.events.event_writer import EventWriter
    from src.ingest import price_channel_ingest as pci

    caplog.set_level("DEBUG", logger="zeus.price_channel_ingest")
    traces: list[tuple[str, bool, int]] = []
    _db, world_path = _file_backed_world_writer(monkeypatch, tmp_path, traces)
    events = _price_redecision_writer_events()

    with pytest.raises(RuntimeError, match="active rollback"):
        with pci._edli_price_channel_world_write_connection(
            owner="price_channel_redecision_emit"
        ) as world:
            assert all(
                result.inserted for result in EventWriter(world).write_many(events)
            )
            raise RuntimeError("active rollback")

    verify = sqlite3.connect(world_path)
    assert verify.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    assert verify.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing"
    ).fetchone()[0] == 0
    verify.close()
    messages = _price_writer_phase_messages(caplog)
    assert [_phase_name(message) for message in messages] == [
        "acquire",
        "begin",
        "write",
        "rollback",
        "transaction_closed",
        "release",
    ]
    assert "transaction=rolled_back" in messages[-1]

    caplog.clear()
    with pci._edli_price_channel_world_write_connection(
        owner="price_channel_redecision_emit"
    ) as world:
        retried = EventWriter(world).write_many(events)
        world.commit()

    assert all(result.inserted for result in retried)
    verify = sqlite3.connect(world_path)
    assert verify.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 3
    assert (
        verify.execute(
            "SELECT COUNT(*) FROM opportunity_event_processing "
            "WHERE processing_status = 'pending'"
        ).fetchone()[0]
        == 3
    )
    verify.close()


def test_price_channel_redecision_sqlite_contention_reports_slow_phase_without_event_loss(
    monkeypatch,
    tmp_path,
    caplog,
):
    from src.events.event_writer import EventWriter
    from src.ingest import price_channel_ingest as pci

    caplog.set_level("DEBUG", logger="zeus.price_channel_ingest")
    traces: list[tuple[str, bool, int]] = []
    db, world_path = _file_backed_world_writer(monkeypatch, tmp_path, traces)
    events = _price_redecision_writer_events()
    blocker = sqlite3.connect(world_path)
    blocker.execute("BEGIN IMMEDIATE")

    def injected_busy_timeout(conn, *, timeout_ms):
        assert timeout_ms == pci.PRICE_CHANNEL_REDECISION_WORLD_WRITE_TIMEOUT_MS == 25
        conn.execute("PRAGMA busy_timeout=900")

    monkeypatch.setattr(
        pci,
        "_bound_price_channel_sqlite_wait",
        injected_busy_timeout,
    )
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            with pci._edli_price_channel_world_write_connection(
                owner="price_channel_redecision_emit"
            ):
                raise AssertionError("contended BEGIN must not yield a write connection")
    finally:
        blocker.rollback()
        blocker.close()

    assert db.world_write_mutex().locked() is False
    phase_messages = _price_writer_phase_messages(caplog)
    assert [_phase_name(message) for message in phase_messages] == [
        "acquire",
        "begin",
        "transaction_closed",
        "release",
    ]
    warning = next(
        record.message
        for record in caplog.records
        if "price_channel_world_writer over_budget" in record.message
    )
    assert "phase=begin" in warning
    assert _telemetry_ms(warning, "phase_ms") >= 750.0
    assert _telemetry_ms(warning, "hold_ms") >= 750.0
    assert "budget_ms=750" in warning
    assert "transaction=begin_failed" in warning
    verify = sqlite3.connect(world_path)
    assert verify.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    verify.close()

    caplog.clear()
    with pci._edli_price_channel_world_write_connection(
        owner="price_channel_redecision_emit"
    ) as world:
        retried = EventWriter(world).write_many(events)
        world.commit()

    assert all(result.inserted for result in retried)
    verify = sqlite3.connect(world_path)
    assert verify.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 3
    assert (
        verify.execute(
            "SELECT COUNT(*) FROM opportunity_event_processing "
            "WHERE processing_status = 'pending'"
        ).fetchone()[0]
        == 3
    )
    verify.close()


def test_price_channel_redecision_coalesced_sink_does_not_block_ingest(
    monkeypatch,
):
    import threading

    from src.events import price_channel_redecision_router as router

    started = threading.Event()
    release = threading.Event()
    batches: list[tuple[object, ...]] = []

    def synchronous_sink(events) -> None:  # noqa: ANN001
        batch = tuple(events)
        batches.append(batch)
        if len(batches) == 1:
            started.set()
            assert release.wait(2.0)

    monkeypatch.setattr(
        router,
        "_edli_price_channel_redecision_sink",
        lambda *_args, **_kwargs: synchronous_sink,
    )
    sink = router._edli_coalesced_price_channel_redecision_sink()

    def event(token: str, version: int):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": token, "version": version}),
        )

    first = event("token-a", 1)
    started_at = time.perf_counter()
    sink((first,))
    elapsed = time.perf_counter() - started_at

    assert elapsed < 0.1
    assert started.wait(1.0)
    replaced = event("token-b", 1)
    latest = event("token-b", 2)
    sink((replaced, latest))
    release.set()
    assert sink.wait_idle(2.0)
    assert batches == [(first,), (latest,)]


def test_price_channel_redecision_worker_reuses_reads_across_quote_burst(monkeypatch):
    import threading

    from src.events import price_channel_redecision_router as router
    from src.state import db

    opened: list[str] = []
    closed: list[str] = []
    routed: list[tuple[object, ...]] = []
    all_closed = threading.Event()

    class ReadConnection:
        def __init__(self, name: str) -> None:
            self.name = name
            opened.append(name)

        def close(self) -> None:
            closed.append(self.name)
            if len(closed) == 3:
                all_closed.set()

    monkeypatch.setattr(db, "get_world_connection_read_only", lambda: ReadConnection("world"))
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: ReadConnection("trade"))
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda: ReadConnection("forecasts"),
    )

    def build(_world, _trade, _forecasts, events, **_kwargs):  # noqa: ANN001
        routed.append(tuple(events))
        return []

    monkeypatch.setattr(router, "_edli_price_channel_redecision_events_for_events", build)
    worker = router._CoalescingPriceChannelRedecisionSink(
        router._edli_price_channel_redecision_sink(reuse_read_connections=True),
        idle_timeout_seconds=0.05,
    )

    def event(token: str, version: int):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": token, "version": version}),
        )

    first = event("token-a", 1)
    second = event("token-b", 1)
    worker((first,))
    assert worker.wait_idle(1.0)
    worker((second,))
    assert worker.wait_idle(1.0)

    assert routed == [(first,), (second,)]
    assert opened == ["world", "trade", "forecasts"]
    assert all_closed.wait(1.0)
    assert closed == ["forecasts", "trade", "world"]


def test_price_channel_redecision_worker_batches_fast_burst_at_fixed_leading_edge():
    import threading

    from src.events import price_channel_redecision_router as router

    batches: list[tuple[object, ...]] = []
    first_batch = threading.Event()

    def synchronous_sink(events) -> None:  # noqa: ANN001
        batches.append(tuple(events))
        first_batch.set()

    worker = router._CoalescingPriceChannelRedecisionSink(
        synchronous_sink,
        batch_window_seconds=0.05,
        idle_timeout_seconds=0.01,
    )

    def event(token: str, version: int):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": token, "version": version}),
        )

    first = event("token-a", 1)
    latest = event("token-a", 2)
    other = event("token-b", 1)
    started_at = time.perf_counter()
    worker((first,))
    worker((latest, other))

    assert time.perf_counter() - started_at < 0.1
    assert first_batch.wait(1.0)
    assert worker.wait_idle(1.0)
    assert batches == [(latest, other)]

    after_window = event("token-c", 1)
    worker((after_window,))
    deadline = time.monotonic() + 1.0
    while len(batches) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.005)
    assert worker.wait_idle(1.0)
    assert batches == [(latest, other), (after_window,)]


def test_price_channel_redecision_worker_keeps_leading_deadline_before_delayed_start(
    monkeypatch,
):
    import threading

    from src.events import price_channel_redecision_router as router

    real_thread = threading.Thread
    worker_started = threading.Event()
    release_worker = threading.Event()
    first_batch_received = threading.Event()
    batches: list[tuple[object, ...]] = []
    batches_received = threading.Event()
    clock = {"now": 0.0}

    class DelayedThread:
        def __init__(self, *, target, **_kwargs) -> None:  # noqa: ANN001
            self._target = target

        def start(self) -> None:
            def run() -> None:
                worker_started.set()
                assert release_worker.wait(1.0)
                self._target()

            real_thread(target=run, daemon=True).start()

    monkeypatch.setattr(router.threading, "Thread", DelayedThread)
    monkeypatch.setattr(router.time, "monotonic", lambda: clock["now"])
    def record_batch(events) -> None:  # noqa: ANN001
        batches.append(tuple(events))
        if len(batches) == 1:
            first_batch_received.set()
        else:
            batches_received.set()

    sink = router._CoalescingPriceChannelRedecisionSink(
        record_batch,
        batch_window_seconds=0.02,
        idle_timeout_seconds=0.01,
    )
    first = types.SimpleNamespace(
        event_type="BOOK_SNAPSHOT",
        payload_json=json.dumps({"token_id": "token-a", "version": 1}),
    )
    late = types.SimpleNamespace(
        event_type="BOOK_SNAPSHOT",
        payload_json=json.dumps({"token_id": "token-b", "version": 2}),
    )

    sink((first,))
    assert worker_started.wait(1.0)
    clock["now"] = 0.04
    sink((late,))
    clock["now"] = 1.0
    release_worker.set()

    assert first_batch_received.wait(0.1)
    time.sleep(0.03)
    clock["now"] = 2.0
    assert batches_received.wait(0.1)
    assert batches == [(first,), (late,)]


def test_price_channel_redecision_worker_recovers_after_thread_start_failure(monkeypatch):
    import threading

    from src.events import price_channel_redecision_router as router

    real_thread = threading.Thread
    starts = {"count": 0}
    batches: list[tuple[object, ...]] = []

    class FlakyThread:
        def __init__(self, *, target, **_kwargs) -> None:  # noqa: ANN001
            self._target = target

        def start(self) -> None:
            starts["count"] += 1
            if starts["count"] == 1:
                raise RuntimeError("thread start failed")
            real_thread(target=self._target, daemon=True).start()

    monkeypatch.setattr(router.threading, "Thread", FlakyThread)
    worker = router._CoalescingPriceChannelRedecisionSink(
        lambda events: batches.append(tuple(events)),
        batch_window_seconds=0.01,
        idle_timeout_seconds=0.01,
    )

    def event(version: int):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": "token-a", "version": version}),
        )

    first = event(1)
    worker((first,))

    assert worker.wait_idle(1.0)
    assert starts["count"] == 2
    assert batches == [(first,)]


def test_price_channel_redecision_worker_waits_for_close_before_restart():
    import threading

    from src.events import price_channel_redecision_router as router

    close_started = threading.Event()
    release_close = threading.Event()
    close_finished = threading.Event()
    second_batch = threading.Event()
    overlap = threading.Event()
    calls = {"count": 0, "closes": 0}

    class ClosingSink:
        def __call__(self, _events) -> None:  # noqa: ANN001
            calls["count"] += 1
            if calls["count"] == 2:
                if not close_finished.is_set():
                    overlap.set()
                second_batch.set()

        def close(self) -> None:
            calls["closes"] += 1
            if calls["closes"] == 1:
                close_started.set()
                assert release_close.wait(1.0)
                close_finished.set()

    worker = router._CoalescingPriceChannelRedecisionSink(
        ClosingSink(),
        batch_window_seconds=0.01,
        idle_timeout_seconds=0.01,
    )

    def event(token: str):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": token}),
        )

    worker((event("token-a"),))
    assert close_started.wait(1.0)
    worker((event("token-b"),))
    assert not second_batch.wait(0.05)
    release_close.set()

    assert second_batch.wait(1.0)
    assert not overlap.is_set()
    assert worker.wait_idle(1.0)


def test_price_channel_redecision_worker_collapses_busy_generations_after_success():
    import threading

    from src.events import price_channel_redecision_router as router

    first_started = threading.Event()
    release_first = threading.Event()
    second_received = threading.Event()
    batches: list[tuple[object, ...]] = []

    def blocking_sink(events) -> None:  # noqa: ANN001
        batches.append(tuple(events))
        if len(batches) == 1:
            first_started.set()
            assert release_first.wait(1.0)
        else:
            second_received.set()

    worker = router._CoalescingPriceChannelRedecisionSink(
        blocking_sink,
        batch_window_seconds=0.02,
        idle_timeout_seconds=0.01,
    )

    def event(token: str, version: int):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": token, "version": version}),
        )

    first = event("token-a", 1)
    stale = event("token-a", 2)
    other = event("token-b", 1)
    latest = event("token-a", 3)
    worker((first,))
    assert first_started.wait(1.0)
    time.sleep(0.03)
    worker((stale,))
    time.sleep(0.03)
    worker((other,))
    time.sleep(0.03)
    worker((latest,))
    release_first.set()

    assert second_received.wait(1.0)
    assert worker.wait_idle(1.0)
    assert batches == [(first,), (latest, other)]


def test_price_channel_redecision_worker_failure_requeues_latest_pending_burst(monkeypatch):
    import threading

    from src.events import price_channel_redecision_router as router

    started = threading.Event()
    release = threading.Event()
    batches: list[tuple[object, ...]] = []

    def retrying_sink(events) -> None:  # noqa: ANN001
        batch = tuple(events)
        batches.append(batch)
        if len(batches) == 1:
            started.set()
            assert release.wait(1.0)
            raise RuntimeError("transient sink failure")

    monkeypatch.setattr(router.time, "sleep", lambda _seconds: None)
    worker = router._CoalescingPriceChannelRedecisionSink(
        retrying_sink,
        batch_window_seconds=0.01,
        idle_timeout_seconds=0.01,
    )

    def event(version: int):
        return types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json=json.dumps({"token_id": "token-a", "version": version}),
        )

    first = event(1)
    latest = event(2)
    worker((first,))
    assert started.wait(1.0)
    worker((latest,))
    release.set()

    assert worker.wait_idle(1.0)
    assert batches == [(first,), (latest,)]


def test_price_channel_redecision_worker_reopens_reads_after_failure(monkeypatch):
    from src.events import price_channel_redecision_router as router

    class RetryingSink:
        def __init__(self) -> None:
            self.calls = 0
            self.closes = 0

        def __call__(self, _events) -> None:  # noqa: ANN001
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("read connection failed")

        def close(self) -> None:
            self.closes += 1

    monkeypatch.setattr(router.time, "sleep", lambda _seconds: None)
    routed = RetryingSink()
    worker = router._CoalescingPriceChannelRedecisionSink(
        routed,
        idle_timeout_seconds=0.01,
    )
    event = types.SimpleNamespace(
        event_type="BOOK_SNAPSHOT",
        payload_json=json.dumps({"token_id": "token-a"}),
    )

    worker((event,))

    assert worker.wait_idle(1.0)
    assert routed.calls == 2
    assert routed.closes >= 1


def test_market_channel_forever_uses_coalesced_redecision_sink():
    from src.ingest import price_channel_ingest

    source = inspect.getsource(price_channel_ingest._edli_market_channel_ingestor_cycle)
    assert "_edli_coalesced_price_channel_redecision_sink" in source
    assert "market_event_sink=_edli_price_channel_redecision_sink" not in source


def test_market_channel_token_metadata_reloader_skips_unchanged_projection(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest
    from src.state import db

    version = [10, 10, "2026-07-17T17:00:00+00:00"]
    held = {"held-token"}
    open_rest = {"rest-token"}
    day0 = {"day0-token"}
    candidates = {"candidate-token"}
    priorities = held | open_rest | day0 | candidates
    calls = {"active": 0, "entry": 0, "exit": 0, "closed": 0}
    order: list[str] = []

    class Cursor:
        def fetchone(self):
            return tuple(version)

    class Connection:
        def execute(self, _sql):  # noqa: ANN001
            return Cursor()

        def close(self):
            calls["closed"] += 1

    class Metadata:
        def __init__(self, condition_id):
            self.condition_id = condition_id

    def active_metadata(_conn, *, priority_token_ids):  # noqa: ANN001
        order.append("broad_hydration")
        calls["active"] += 1
        assert set(priority_token_ids) == priorities
        return {"active-token": Metadata("condition-active")}

    def targeted_metadata(_conn, *, token_ids, purpose):  # noqa: ANN001
        calls[purpose] += 1
        if purpose == "exit":
            order.append("held_metadata")
            assert set(token_ids) == held
        else:
            assert set(token_ids) == priorities
        return {
            token_id: Metadata("condition-held" if purpose == "exit" else "condition-entry")
            for token_id in token_ids
        }

    monkeypatch.setattr(
        db,
        "_connect_read_only",
        lambda _path, *, deadline_monotonic=None: Connection(),
    )
    monkeypatch.setattr(
        db,
        "get_trade_connection",
        lambda *, write_class=None, deadline_monotonic=None: Connection(),
    )
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda *, deadline_monotonic=None: Connection(),
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_from_snapshots",
        active_metadata,
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        targeted_metadata,
    )
    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_held_position_priority_token_ids",
        lambda _conn: set(held),
    )
    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_canonical_open_held_pairs",
        lambda _conn: {("condition-held", "held-token")},
    )
    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_open_rest_priority_token_ids",
        lambda _conn: set(open_rest),
    )
    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_current_day0_priority_token_ids",
        lambda _trade, _forecasts: tuple(day0),
    )
    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_candidate_priority_token_ids",
        lambda _world, *, limit: list(candidates),
    )
    monkeypatch.setattr(
        price_channel_ingest,
        "_edli_priority_family_token_ids",
        lambda _trade, _forecasts, token_ids: set(token_ids),
    )

    reload_token_metadata = (
        price_channel_ingest._edli_market_channel_token_metadata_reloader()
    )
    # The sink receipt is durable before the first reload callback is allowed to
    # enter the potentially blocking/failing broad scan.
    order.append("sink_receipt")
    first = reload_token_metadata()
    second = reload_token_metadata()
    candidates.add("day0-token")
    promoted = reload_token_metadata()
    candidates.discard("day0-token")
    demoted = reload_token_metadata()
    candidates.add("candidate-token-2")
    priorities.add("candidate-token-2")
    priority_only = reload_token_metadata()
    version[0] += 1
    third = reload_token_metadata()

    assert set(first.token_metadata) == {"held-token"}
    assert set(first.seed_first_token_ids) == {"held-token"}
    assert set(first.depth_repair_token_ids) == {"held-token"}
    assert set(second.token_metadata) == {"active-token", "held-token"}
    assert second.token_metadata is not first.token_metadata
    assert "day0-token" in promoted.depth_repair_token_ids
    assert "day0-token" not in demoted.depth_repair_token_ids
    assert "candidate-token-2" in priority_only.token_metadata
    assert priority_only.token_metadata is not first.token_metadata
    assert third.token_metadata is not first.token_metadata
    assert calls == {"active": 2, "entry": 3, "exit": 6, "closed": 16}
    assert order[:2] == ["sink_receipt", "held_metadata"]
    assert order.count("broad_hydration") == 2

    cached = {"held-token": object(), "cached-token": object()}
    cached_reload = price_channel_ingest._edli_market_channel_token_metadata_reloader(
        initial_token_metadata=cached,
        initial_fingerprint=(
            (version[0], version[0]),
            tuple(sorted(priorities)),
            tuple(sorted(held | open_rest | candidates)),
        ),
        initial_seed_first_token_ids=tuple(sorted(priorities)),
        initial_depth_repair_token_ids=tuple(
            sorted(held | open_rest | candidates)
        ),
    )
    cached_result = cached_reload()

    assert cached_result.token_metadata is cached
    assert set(cached_result.seed_first_token_ids) == priorities
    assert set(cached_result.depth_repair_token_ids) == (
        held | open_rest | candidates
    )
    assert calls == {"active": 2, "entry": 3, "exit": 6, "closed": 19}


def test_market_channel_fast_scope_uses_canonical_pairs_not_audit_residual(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db

    class Connection:
        def set_progress_handler(self, _callback, _interval):
            pass

        def close(self):
            pass

    class Metadata:
        def __init__(self, condition_id, token_id):
            self.condition_id = condition_id
            self.token_id = token_id

    canonical = {
        ("condition-a", "held-a"),
        ("condition-b", "held-b"),
    }
    audit = {f"audit-{index}" for index in range(56)}
    audit_calls: list[set[str]] = []
    monkeypatch.setattr(db, "_connect_read_only", lambda *_a, **_k: Connection())
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda **_k: Connection())
    monkeypatch.setattr(db, "get_trade_connection", lambda **_k: Connection())
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda _conn: canonical)
    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda _conn: audit_calls.append(set(audit)) or set(audit),
    )
    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_current_day0_priority_token_ids", lambda *_a: ())
    monkeypatch.setattr(lane, "_edli_priority_family_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_market_channel_token_metadata_fingerprint", lambda *_a: ((1, 1), (), ()))
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda _conn, *, token_ids, purpose: {
            token_id: Metadata(
                "condition-a" if token_id == "held-a" else "condition-b", token_id
            )
            for token_id in token_ids
        },
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_from_snapshots",
        lambda *_a, **_k: {},
    )

    universe = lane._edli_market_channel_token_metadata_reloader()()
    assert set(universe.token_metadata) == {"held-a", "held-b"}
    assert set(universe.seed_first_token_ids) == {"held-a", "held-b"}
    assert audit_calls == []


def test_market_channel_fast_scope_rechecks_new_held_after_empty_first_cadence(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db

    class Connection:
        def set_progress_handler(self, _callback, _interval):
            pass

        def close(self):
            pass

    class Metadata:
        condition_id = "condition-new"

    canonical: set[tuple[str, str]] = set()
    monkeypatch.setattr(db, "_connect_read_only", lambda *_a, **_k: Connection())
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda **_k: Connection())
    monkeypatch.setattr(db, "get_trade_connection", lambda **_k: Connection())
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda _conn: set(canonical))
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda _conn: set())
    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_current_day0_priority_token_ids", lambda *_a: ())
    monkeypatch.setattr(lane, "_edli_priority_family_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_market_channel_token_metadata_fingerprint", lambda *_a: ((1, 1), (), ()))
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda _conn, *, token_ids, purpose: {token_id: Metadata() for token_id in token_ids},
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_from_snapshots",
        lambda *_a, **_k: {},
    )
    reload_token_metadata = lane._edli_market_channel_token_metadata_reloader()
    first = reload_token_metadata()
    assert first.token_metadata == {}
    canonical.add(("condition-new", "held-new"))
    second = reload_token_metadata()
    assert set(second.token_metadata) == {"held-new"}
    assert set(second.seed_first_token_ids) == {"held-new"}


def test_market_channel_first_held_tranche_does_not_open_world_or_forecasts(
    monkeypatch,
):
    """Canonical held subscription cannot be blocked by unrelated DB bootstrap."""
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db

    class Connection:
        def set_progress_handler(self, _callback, _interval):
            pass

        def close(self):
            pass

    class Metadata:
        condition_id = "condition-held"

    monkeypatch.setattr(db, "get_trade_connection", lambda **_k: Connection())
    monkeypatch.setattr(
        db,
        "_connect_read_only",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("world bootstrap must wait for held subscription")
        ),
    )
    monkeypatch.setattr(
        db,
        "get_forecasts_connection_read_only",
        lambda **_k: (_ for _ in ()).throw(
            AssertionError("forecast bootstrap must wait for held subscription")
        ),
    )
    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda _conn: {("condition-held", "held-token")},
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda _conn, *, token_ids, purpose: {
            token_id: Metadata() for token_id in token_ids
        },
    )

    universe = lane._edli_market_channel_token_metadata_reloader()()

    assert set(universe.token_metadata) == {"held-token"}
    assert set(universe.seed_first_token_ids) == {"held-token"}


def test_market_channel_broad_partial_exit_retains_held_and_keeps_m5_debt(
    monkeypatch,
):
    """Broad candidates never replace canonical held rows on partial exit refresh."""
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db

    class Cursor:
        def fetchone(self):
            return (1,)

    class Connection:
        def set_progress_handler(self, _callback, _interval):
            pass

        def execute(self, _sql):
            return Cursor()

        def close(self):
            pass

    class Metadata:
        def __init__(self, condition_id):
            self.condition_id = condition_id

    canonical = {("condition-held", "held-token")}
    exit_results = iter(
        (
            {"held-token": Metadata("condition-held")},
            {},
            {"held-token": Metadata("condition-held")},
        )
    )
    debt_reasons: list[str] = []
    clear_calls: list[str] = []
    monkeypatch.setattr(lane, "_market_channel_universe_refresh_debt", None)
    monkeypatch.setattr(
        lane,
        "_edli_publish_market_channel_universe_refresh_debt",
        lambda _generation, reason: debt_reasons.append(reason),
    )
    monkeypatch.setattr(
        lane,
        "_edli_clear_market_channel_universe_refresh_debt",
        lambda generation: clear_calls.append(generation),
    )
    monkeypatch.setattr(db, "get_trade_connection", lambda **_k: Connection())
    monkeypatch.setattr(db, "_connect_read_only", lambda *_a, **_k: Connection())
    monkeypatch.setattr(
        db, "get_forecasts_connection_read_only", lambda **_k: Connection()
    )
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda _conn: canonical)
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda _conn: set())
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda _conn: set())
    monkeypatch.setattr(lane, "_edli_current_day0_priority_token_ids", lambda *_a: ())
    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(lane, "_edli_priority_family_token_ids", lambda *_a: set())
    monkeypatch.setattr(
        lane,
        "_edli_market_channel_seed_first_token_ids",
        lambda **_k: {"held-token"},
    )
    monkeypatch.setattr(
        lane,
        "_edli_market_channel_depth_repair_token_ids",
        lambda **_k: {"held-token"},
    )
    monkeypatch.setattr(
        lane,
        "_edli_market_channel_token_metadata_fingerprint",
        lambda *_a: ((1, 1), (), ()),
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_from_snapshots",
        lambda *_a, **_k: {"candidate-token": Metadata("condition-candidate")},
    )

    def targeted(_conn, *, token_ids, purpose):
        if purpose == "exit":
            return next(exit_results)
        return {}

    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        targeted,
    )
    reload_token_metadata = lane._edli_market_channel_token_metadata_reloader()

    first = reload_token_metadata()
    second = reload_token_metadata()
    third = reload_token_metadata()

    assert set(first.token_metadata) == {"held-token"}
    assert set(second.token_metadata) == {"held-token", "candidate-token"}
    assert second.token_metadata["held-token"].condition_id == "condition-held"
    assert debt_reasons == ["canonical_held_identity_coverage_missing"]
    assert len(clear_calls) == 1
    assert set(third.token_metadata) == {"held-token", "candidate-token"}


def test_market_channel_canonical_identity_debt_fails_m5_for_all_typed_reasons(
    monkeypatch,
):
    from src.ingest import price_channel_ingest as lane

    for reason in (
        "canonical_held_identity_unavailable",
        "canonical_held_identity_condition_mismatch",
        "canonical_held_identity_coverage_missing",
    ):
        monkeypatch.setattr(
            lane,
            "_market_channel_universe_refresh_debt",
            {"reason": reason},
        )
        assert lane._edli_market_channel_universe_m5_failure_reason() == (
            "canonical_held_identity"
        )


def test_market_channel_universe_reload_interrupts_blocked_sqlite_and_retries_next_cadence(
    monkeypatch, tmp_path
):
    import asyncio

    from src import config
    from src.events.triggers.market_channel_ingestor import MarketChannelOnlineService
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    generation = lane._edli_begin_market_channel_bootstrap(
        deadline_monotonic=time.monotonic() + 30.0
    )
    lane._write_market_channel_sink_readiness(
        {
            "schema_version": 1,
            "generation": generation,
            "sink_registered": True,
            "consumer_queue_accepted": True,
            "phase": "registered",
        }
    )
    assert lane._edli_market_channel_sink_readiness_error() is None

    cancel = threading.Event()
    monkeypatch.setattr(lane, "_market_channel_universe_reload_generation", generation)
    monkeypatch.setattr(
        lane,
        "_market_channel_universe_reload_deadline",
        time.monotonic() + 30.0,
    )
    monkeypatch.setattr(lane, "_market_channel_universe_reload_cancel", cancel)
    monkeypatch.setattr(lane, "_market_channel_universe_reload_connections", set())
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    entered = threading.Event()

    def blocked_sqlite_step():
        entered.set()
        while not cancel.is_set():
            time.sleep(0.001)
        return 1

    conn.create_function("blocked_step", 0, blocked_sqlite_step)
    failure: list[BaseException] = []

    def blocked_reload():
        try:
            with lane._edli_market_channel_universe_reload_connection(conn, generation):
                conn.execute("SELECT blocked_step()").fetchone()
        except BaseException as exc:  # noqa: BLE001 - deterministic timeout antibody
            failure.append(exc)

    worker = threading.Thread(target=blocked_reload, daemon=True)
    started_at = time.monotonic()
    worker.start()
    assert entered.wait(timeout=1.0)
    lane._edli_cancel_market_channel_universe_reload(generation)
    worker.join(timeout=1.0)
    elapsed = time.monotonic() - started_at
    assert not worker.is_alive()
    assert elapsed < 1.0
    assert failure
    assert not lane._market_channel_universe_reload_connections
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")

    class Ingestor:
        active_token_ids = {"registered-token"}

        def replace_token_metadata(self, metadata):
            self.active_token_ids = set(metadata)
            return set(metadata)

    class WebSocket:
        def __init__(self):
            self.sent = []

        async def send(self, payload):
            self.sent.append(json.loads(payload))

    attempts = iter((TimeoutError("universe reload deadline"), {"registered-token": object()}))

    def reload_next_cadence():
        outcome = next(attempts)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    service = MarketChannelOnlineService(Ingestor(), reload_token_metadata=reload_next_cadence)
    subscribed = {"registered-token"}
    websocket = WebSocket()
    asyncio.run(
        service._sync_subscription_universe(
            websocket,
            subscribed_token_ids=subscribed,
            write_gate=contextlib.nullcontext(),
            commit=None,
            logger=None,
        )
    )
    assert subscribed == {"registered-token"}
    assert service.universe_refresh_error_count == 1
    asyncio.run(
        service._sync_subscription_universe(
            websocket,
            subscribed_token_ids=subscribed,
            write_gate=contextlib.nullcontext(),
            commit=None,
            logger=None,
        )
    )
    assert subscribed == {"registered-token"}
    assert websocket.sent == []
    lane._edli_supersede_market_channel_bootstrap(generation)


def test_market_channel_reloader_deadline_interrupts_blocked_broad_hydration(
    monkeypatch, tmp_path,
):
    from src import config
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db

    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    bootstrap_generation = lane._edli_begin_market_channel_bootstrap(
        deadline_monotonic=time.monotonic() + 30.0
    )
    lane._write_market_channel_sink_readiness(
        {
            "schema_version": 1,
            "generation": bootstrap_generation,
            "sink_registered": True,
            "consumer_queue_accepted": True,
            "phase": "registered",
        }
    )

    class Cursor:
        def fetchone(self):
            return (1,)

    class Connection:
        def __init__(self):
            self.closed = False
            self.progress = None

        def set_progress_handler(self, callback, _interval):
            self.progress = callback

        def execute(self, _sql):
            return Cursor()

        def close(self):
            self.closed = True

    connections: list[Connection] = []

    def new_connection(*_args, **_kwargs):
        conn = Connection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(db, "_connect_read_only", new_connection)
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", new_connection)
    monkeypatch.setattr(db, "get_trade_connection", new_connection)
    monkeypatch.setattr(lane, "MARKET_CHANNEL_UNIVERSE_REFRESH_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda *_a, **_k: [])
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_current_day0_priority_token_ids", lambda *_a: ())
    monkeypatch.setattr(lane, "_edli_priority_family_token_ids", lambda *_a: set())
    monkeypatch.setattr(lane, "_edli_market_channel_seed_first_token_ids", lambda **_k: ())
    monkeypatch.setattr(lane, "_edli_market_channel_depth_repair_token_ids", lambda **_k: ())
    monkeypatch.setattr(lane, "_edli_market_channel_token_metadata_fingerprint", lambda *_a: ((1, 1), (), ()))
    started = threading.Event()
    blocked = [True]

    def blocked_broad(_conn, *, priority_token_ids):
        if not blocked[0]:
            return {"hydrated-token": object()}
        started.set()
        while not lane._market_channel_universe_reload_cancel.is_set():
            time.sleep(0.001)
        raise sqlite3.OperationalError("interrupted broad hydration")

    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_from_snapshots",
        blocked_broad,
    )
    reload_token_metadata = lane._edli_market_channel_token_metadata_reloader(
        initial_token_metadata={},
        initial_fingerprint=None,
    )
    started_at = time.monotonic()
    with pytest.raises(TimeoutError):
        reload_token_metadata()
    assert started.wait(timeout=1.0)
    assert time.monotonic() - started_at < 1.0
    assert all(conn.closed for conn in connections)
    assert not lane._market_channel_universe_reload_connections
    debt = json.loads(
        (tmp_path / lane.MARKET_CHANNEL_SINK_READINESS_FILENAME).read_text()
    )["universe_refresh_debt"]
    assert debt["generation"] == bootstrap_generation
    assert debt["pid"] == os.getpid()
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", "stale-bootstrap")
    lane._edli_clear_market_channel_universe_refresh_debt("stale-attempt")
    assert lane._market_channel_universe_refresh_debt is not None
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", bootstrap_generation)

    blocked[0] = False
    second = reload_token_metadata()
    assert "hydrated-token" in second.token_metadata
    readiness = json.loads(
        (tmp_path / lane.MARKET_CHANNEL_SINK_READINESS_FILENAME).read_text()
    )
    assert readiness["sink_registered"] is True
    assert readiness["generation"] == bootstrap_generation
    assert "universe_refresh_debt" not in readiness
    lane._edli_supersede_market_channel_bootstrap(bootstrap_generation)


def test_price_channel_redecision_wake_is_targeted_urgent_fast_path():
    from src.events import reactor
    from src.runtime.reactor_wake import URGENT_WAKE_REASONS

    source = inspect.getsource(reactor.run_edli_event_reactor_cycle)

    assert "market_price_advanced" in URGENT_WAKE_REASONS
    assert 'producer_wake_reason == "market_price_advanced"' in source
    assert "committed_event_wake" in source
    assert "targeted_only=producer_fast_path and bool(targeted_event_ids)" in source


def test_price_channel_redecision_sink_closes_partial_read_open(monkeypatch):
    from src.events import price_channel_redecision_router as router
    from src.state import db

    closed = False

    class WorldRead:
        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(db, "get_world_connection_read_only", WorldRead)
    monkeypatch.setattr(
        db,
        "get_trade_connection_read_only",
        lambda: (_ for _ in ()).throw(RuntimeError("trade open failed")),
    )

    with pytest.raises(RuntimeError, match="trade open failed"):
        router._edli_price_channel_redecision_sink()(["quote"])

    assert closed is True


def test_price_channel_redecision_writer_claims_one_pending_event_per_family():
    from src.events.opportunity_event import make_opportunity_event
    from src.events.price_channel_redecision_router import (
        _edli_write_price_channel_redecision_events,
    )
    from src.state.db import init_schema

    world = sqlite3.connect(":memory:")
    init_schema(world)

    def event(source: str, at: str):
        return make_opportunity_event(
            event_type="EDLI_REDECISION_PENDING",
            entity_key="Munich|2026-07-15|high",
            source=source,
            observed_at=at,
            available_at=at,
            received_at=at,
            payload={"source": source},
        )

    first = event("price:a", "2026-07-14T09:00:00+00:00")
    raced = event("price:b", "2026-07-14T09:00:01+00:00")
    later = event("price:c", "2026-07-14T09:00:02+00:00")

    assert _edli_write_price_channel_redecision_events(world, [first, raced]) == 1
    assert _edli_write_price_channel_redecision_events(world, [later]) == 0
    assert world.execute(
        "SELECT COUNT(*) FROM opportunity_events WHERE entity_key = ?",
        (first.entity_key,),
    ).fetchone()[0] == 1


def test_price_channel_redecision_writer_returns_only_committed_event_ids():
    from src.events.opportunity_event import make_opportunity_event
    from src.events.price_channel_redecision_router import (
        _edli_write_price_channel_redecision_event_ids,
    )
    from src.state.db import init_schema

    world = sqlite3.connect(":memory:")
    init_schema(world)

    def event(source: str, at: str):
        return make_opportunity_event(
            event_type="EDLI_REDECISION_PENDING",
            entity_key="Paris|2026-07-18|high",
            source=source,
            observed_at=at,
            available_at=at,
            received_at=at,
            payload={"source": source},
        )

    first = event("price:a", "2026-07-18T09:00:00+00:00")
    debounced = event("price:b", "2026-07-18T09:00:01+00:00")

    assert _edli_write_price_channel_redecision_event_ids(
        world,
        [first, debounced],
    ) == (first.event_id,)


def _position_fill_redecision_test_dbs():
    from src.state.db import init_schema

    world = sqlite3.connect(":memory:")
    init_schema(world)
    trade = sqlite3.connect(":memory:")
    trade.row_factory = sqlite3.Row
    trade.executescript(
        """
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            phase TEXT NOT NULL,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            shares REAL,
            chain_shares REAL
        );
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            intent_kind TEXT NOT NULL
        );
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY,
            command_id TEXT NOT NULL,
            state TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        );
        INSERT INTO position_current VALUES
            ('beijing-33-yes', 'day0_window', 'Beijing', '2026-07-27', 'high', 8.22, 8.22);
        INSERT INTO venue_commands VALUES
            ('entry-beijing-33', 'beijing-33-yes', 'ENTRY');
        INSERT INTO venue_trade_facts VALUES
            (41, 'entry-beijing-33', 'CONFIRMED', '2026-07-27T01:02:03+00:00');
        """
    )
    forecasts = sqlite3.connect(":memory:")
    return world, trade, forecasts


def _install_position_fill_redecision_trigger(monkeypatch):
    from src.events.opportunity_event import make_opportunity_event
    from src.events.triggers import forecast_snapshot_ready as trigger_module

    calls = []

    class Trigger:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_committed_snapshot_events(self, **kwargs):
            calls.append(kwargs)
            at = kwargs["decision_time"].isoformat()
            return [
                make_opportunity_event(
                    event_type=kwargs["event_type"],
                    entity_key="Beijing|2026-07-27|high|run-beijing",
                    source=kwargs["source"],
                    observed_at=at,
                    available_at=at,
                    received_at=kwargs["received_at"],
                    causal_snapshot_id="posterior-beijing",
                    payload={
                        "city": "Beijing",
                        "target_date": "2026-07-27",
                        "metric": "high",
                        "source_run_id": "run-beijing",
                    },
                )
            ]

    monkeypatch.setattr(trigger_module, "ForecastSnapshotReadyTrigger", Trigger)
    monkeypatch.setattr(
        trigger_module,
        "executable_forecast_live_eligible_reader",
        lambda _conn: None,
    )
    return calls


def test_position_fill_redecision_is_deterministic_and_bound_to_canonical_fill(
    monkeypatch,
):
    from src.engine.event_reactor_adapter import (
        _event_allows_same_family_monitor_owned,
        _global_projected_book_refresh_tokens,
    )
    from src.events.price_channel_redecision_router import (
        _edli_emit_position_fill_redecisions,
    )
    from src.events.candidate_binding import weather_family_id

    calls = _install_position_fill_redecision_trigger(monkeypatch)
    world, trade, forecasts = _position_fill_redecision_test_dbs()

    first, attempted, current = _edli_emit_position_fill_redecisions(
        world,
        trade,
        forecasts,
    )

    assert len(first) == 1
    assert attempted == {41}
    assert current == {41}
    assert calls[0]["decision_time"].isoformat() == "2026-07-27T01:02:03+00:00"
    assert calls[0]["phase_filter_exempt_families"] == {
        ("Beijing", "2026-07-27", "high")
    }
    payload = json.loads(
        world.execute(
            "SELECT payload_json FROM opportunity_events WHERE event_id = ?",
            (first[0],),
        ).fetchone()[0]
    )
    assert payload["redecision_origin"] == "position_fill"
    assert payload["position_fill_trade_fact_ids"] == [41]
    assert payload["position_fill_position_ids"] == ["beijing-33-yes"]
    assert _event_allows_same_family_monitor_owned("EDLI_REDECISION_PENDING")
    assert _global_projected_book_refresh_tokens(
        (
            types.SimpleNamespace(
                event_type="EDLI_REDECISION_PENDING",
                payload_json=json.dumps(payload),
            ),
        )
    ) == {
        weather_family_id(
            city="Beijing",
            target_date="2026-07-27",
            metric="high",
        ): None
    }

    world.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = 'processed'
         WHERE event_id = ?
        """,
        (first[0],),
    )
    second, attempted_again, current_again = _edli_emit_position_fill_redecisions(
        world,
        trade,
        forecasts,
    )

    assert second == ()
    assert attempted_again == {41}
    assert current_again == {41}
    assert (
        world.execute(
            "SELECT COUNT(*) FROM opportunity_events "
            "WHERE json_extract(payload_json, '$.redecision_origin') = 'position_fill'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("prior_status", ["pending", "processing"])
def test_position_fill_redecision_persists_alongside_prior_family_work(
    monkeypatch,
    prior_status,
):
    from src.events.event_store import EventStore
    from src.events.event_writer import EventWriter
    from src.events.opportunity_event import make_opportunity_event
    from src.events.price_channel_redecision_router import (
        _edli_emit_position_fill_redecisions,
    )

    _install_position_fill_redecision_trigger(monkeypatch)
    world, trade, forecasts = _position_fill_redecision_test_dbs()
    at = "2026-07-27T01:02:02+00:00"
    prior = make_opportunity_event(
        event_type="EDLI_REDECISION_PENDING",
        entity_key="Beijing|2026-07-27|high|run-prior",
        source="market-price",
        observed_at=at,
        available_at=at,
        received_at=at,
        payload={
            "city": "Beijing",
            "target_date": "2026-07-27",
            "metric": "high",
            "redecision_origin": "market_price",
        },
    )
    EventWriter(world).write(prior)
    world.execute(
        """
        UPDATE opportunity_event_processing
           SET processing_status = ?
         WHERE event_id = ?
        """,
        (prior_status, prior.event_id),
    )

    emitted, attempted, current = _edli_emit_position_fill_redecisions(
        world,
        trade,
        forecasts,
    )
    assert len(emitted) == 1
    assert attempted == {41}
    assert current == {41}
    assert (
        world.execute(
            "SELECT COUNT(*) FROM opportunity_events "
            "WHERE event_type = 'EDLI_REDECISION_PENDING'"
        ).fetchone()[0]
        == 2
    )
    targeted = EventStore(world).fetch_pending(
        decision_time="2026-07-27T01:02:05+00:00",
        targeted_event_ids=frozenset(emitted),
        targeted_only=True,
    )
    assert [event.event_id for event in targeted] == list(emitted)

    duplicate, acknowledged, current = _edli_emit_position_fill_redecisions(
        world,
        trade,
        forecasts,
    )
    assert duplicate == ()
    assert acknowledged == {41}
    assert current == {41}


def test_position_fill_redecision_debounce_does_not_ack_unwritten_exact_event(
    monkeypatch,
):
    from src.events import price_channel_redecision_router as router

    _install_position_fill_redecision_trigger(monkeypatch)
    world, trade, forecasts = _position_fill_redecision_test_dbs()
    monkeypatch.setattr(
        router,
        "_edli_write_position_fill_redecision_event_ids",
        lambda _world, _events: (),
    )

    emitted, acknowledged, current = router._edli_emit_position_fill_redecisions(
        world,
        trade,
        forecasts,
    )

    assert emitted == ()
    assert acknowledged == set()
    assert current == {41}


def test_position_fill_redecision_uses_latest_partial_fill_fact():
    from src.events.price_channel_redecision_router import (
        _edli_open_position_fill_rows,
    )

    _world, trade, _forecasts = _position_fill_redecision_test_dbs()
    trade.execute(
        "INSERT INTO venue_trade_facts VALUES (?,?,?,?)",
        (
            42,
            "entry-beijing-33",
            "CONFIRMED",
            "2026-07-27T01:02:04+00:00",
        ),
    )

    rows = _edli_open_position_fill_rows(trade)

    assert len(rows) == 1
    assert rows[0]["trade_fact_id"] == 42


def test_position_fill_without_causal_carrier_is_not_rebuilt_every_cycle(
    monkeypatch,
):
    from src.events.price_channel_redecision_router import (
        _edli_position_fill_redecision_events,
    )
    from src.events.triggers import forecast_snapshot_ready as trigger_module

    class EmptyTrigger:
        def __init__(self, *_args, **_kwargs):
            pass

        def build_committed_snapshot_events(self, **_kwargs):
            return []

    monkeypatch.setattr(
        trigger_module,
        "ForecastSnapshotReadyTrigger",
        EmptyTrigger,
    )
    monkeypatch.setattr(
        trigger_module,
        "executable_forecast_live_eligible_reader",
        lambda _conn: None,
    )
    world, trade, forecasts = _position_fill_redecision_test_dbs()

    events, evaluated, event_facts = _edli_position_fill_redecision_events(
        world,
        trade,
        forecasts,
    )
    assert events == []
    assert evaluated == {41}
    assert event_facts == set()

    events, evaluated_again, event_facts_again = (
        _edli_position_fill_redecision_events(
            world,
            trade,
            forecasts,
            seen_trade_fact_ids=evaluated,
        )
    )
    assert events == []
    assert evaluated_again == set()
    assert event_facts_again == set()


def test_position_fill_redecision_reads_before_world_write_without_poisoning_channel_health():
    from src.events import price_channel_redecision_router as router
    from src.ingest import price_channel_ingest as lane

    cycle_src = inspect.getsource(router._edli_position_fill_redecision_cycle)
    read_build = cycle_src.index(
        ") = _edli_position_fill_redecision_events"
    )
    world_write = cycle_src.index(
        "with _edli_price_channel_world_write_connection"
    )
    assert read_build < world_write
    assert "(evaluated_fact_ids - event_fact_ids) | acknowledged_fact_ids" in cycle_src

    m5_src = inspect.getsource(lane._edli_user_channel_reconcile_cycle)
    assert '"scheduler_failed": False' in m5_src
    assert "m5_authority_proof_complete" in m5_src
    assert "_edli_durable_fill_bridge_scan" not in m5_src

    repair_src = inspect.getsource(lane._edli_fill_bridge_repair_cycle)
    assert '"scheduler_failure_reason": scheduler_failure_reason' in repair_src
    assert "canonical_failure_reasons[0]" in repair_src
    assert "else fill_redecision_error" in repair_src
    assert "processed_with_fill_redecision_error" in repair_src


def _seed_committed_denver_2026_06_20(forecasts_conn) -> None:
    """COMPLETE/LIVE_ELIGIBLE Denver low coverage for target 2026-06-20 (same
    shape as tests/events/test_forecast_snapshot_ready.py's Chicago seed)."""
    from src.state.db import init_schema_forecasts

    init_schema_forecasts(forecasts_conn)
    forecasts_conn.execute(
        """
        INSERT INTO source_run (
            source_run_id, source_id, track, release_calendar_key, ingest_mode, origin_mode,
            source_cycle_time, source_available_at, captured_at, target_local_date,
            city_id, city_timezone, temperature_metric, dataset_id,
            expected_members, observed_members, expected_steps_json, observed_steps_json,
            completeness_status, status
        ) VALUES (
            'run-rest-1', 'ecmwf-open-data', 'ens', '2026-06-20T00', 'SCHEDULED_LIVE', 'SCHEDULED_LIVE',
            '2026-06-20T00:00:00+00:00', '2026-06-20T04:15:00+00:00', '2026-06-20T04:16:00+00:00',
            '2026-06-20', 'denver', 'America/Denver', 'low', 'v1',
            51, 51, '[0,3,6]', '[0,3,6]', 'COMPLETE', 'SUCCESS'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO source_run_coverage (
            coverage_id, source_run_id, source_id, source_transport, release_calendar_key, track,
            city_id, city, city_timezone, target_local_date, temperature_metric, physical_quantity,
            observation_field, data_version, expected_members, observed_members, expected_steps_json,
            observed_steps_json, snapshot_ids_json, target_window_start_utc, target_window_end_utc,
            completeness_status, readiness_status, computed_at, expires_at
        ) VALUES (
            'cov-rest-1', 'run-rest-1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', '2026-06-20T00', 'ens',
            'denver', 'Denver', 'America/Denver', '2026-06-20', 'low', 'temperature',
            'low_temp', 'v1', 51, 51, '[0,3,6]', '[0,3,6]', '[1]',
            '2026-06-20T05:00:00+00:00', '2026-06-21T05:00:00+00:00',
            'COMPLETE', 'LIVE_ELIGIBLE', '2026-06-20T04:16:00+00:00', '2026-06-21T04:16:00+00:00'
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO ensemble_snapshots (
            snapshot_id, city, target_date, temperature_metric, physical_quantity, observation_field,
            issue_time, valid_time, available_at, fetch_time, lead_hours, members_json,
            model_version, dataset_id, source_id, source_transport, source_run_id,
            release_calendar_key, source_cycle_time, source_release_time, source_available_at,
            authority, causality_status, boundary_ambiguous, contributes_to_target_extrema,
            forecast_window_attribution_status, local_day_start_utc, step_horizon_hours,
            members_unit, raw_orderbook_hash_transition_delta_ms
        ) VALUES (
            1, 'Denver', '2026-06-20', 'low', 'temperature', 'low_temp',
            '2026-06-20T00:00:00+00:00', '2026-06-20T06:00:00+00:00',
            '2026-06-20T04:15:00+00:00', '2026-06-20T04:16:00+00:00', 6,
            '[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51]',
            'ecmwf', 'v1', 'ecmwf-open-data', 'ensemble_snapshots_db_reader', 'run-rest-1',
            '2026-06-20T00', '2026-06-20T00:00:00+00:00', '2026-06-20T03:00:00+00:00',
            '2026-06-20T04:15:00+00:00', 'VERIFIED', 'OK', 0, 1,
            'FULLY_INSIDE_TARGET_LOCAL_DAY', '2026-06-20T05:00:00+00:00', 6, 'F', 0
        )
        """
    )
    forecasts_conn.execute(
        """
        INSERT INTO market_events (
            market_slug, city, target_date, temperature_metric, condition_id, token_id, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "denver-low-2026-06-20",
            "Denver",
            "2026-06-20",
            "low",
            "0xrest",
            "resting-yes",
            "2026-06-20T04:16:00+00:00",
        ),
    )


def test_price_channel_resting_order_family_emits_and_debounces_on_second_tick(monkeypatch):
    """A family with NO position and NO screen pass (no beliefs seeded, so
    `_edli_screened_entry_family_keys_for_price_channel` yields nothing) still
    gets EDLI_REDECISION_PENDING when Zeus has its own open resting order on
    the token — and the entity-key debounce already in
    `_edli_pending_redecision_entity_keys` blocks a duplicate on the next tick."""
    import types

    from src.ingest.price_channel_ingest import _edli_emit_price_channel_redecisions_for_events
    from src.state.db import init_schema

    # Matches tests/events/test_forecast_snapshot_ready.py's autouse fixture:
    # exercise the legacy ensemble-committed lane, not the replacement
    # forecast_posteriors lane, which is orthogonal to this bridge test.
    monkeypatch.setattr(
        "src.events.triggers.forecast_snapshot_ready._replacement_live_enabled",
        lambda: False,
    )

    world_conn = sqlite3.connect(":memory:")
    init_schema(world_conn)

    trade_conn = sqlite3.connect(":memory:")
    _seed_minimal_venue_order_tables(trade_conn)
    trade_conn.execute(
        """
        CREATE TABLE executable_market_snapshots (
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            yes_token_id TEXT,
            no_token_id TEXT
        )
        """
    )
    trade_conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?,?,?,?)",
        ("0xrest", "resting-yes", "resting-yes", "resting-no"),
    )
    trade_conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?,?,?,?)",
        ("cmd-rest", "resting-yes", "BUY", 0.5, "ACKED",
         "2026-06-20T00:00:00", "2026-06-20T00:00:00"),
    )
    trade_conn.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, source, observed_at, local_sequence)"
        " VALUES (?,?,?,?,?,?)",
        ("vof-1", "cmd-rest", "RESTING", "REST", "2026-06-20T00:00:00", 1),
    )

    forecasts_conn = sqlite3.connect(":memory:")
    forecasts_conn.row_factory = sqlite3.Row
    _seed_committed_denver_2026_06_20(forecasts_conn)

    events = [
        types.SimpleNamespace(
            event_type="BOOK_SNAPSHOT",
            payload_json='{"token_id": "resting-yes"}',
        )
    ]

    first_emitted = _edli_emit_price_channel_redecisions_for_events(
        world_conn,
        trade_conn,
        forecasts_conn,
        events,
        # Denver is well into settlement day. Held/resting capital must still
        # re-decide from a price move after forecast-only intake has closed.
        received_at="2026-06-20T20:00:00+00:00",
    )
    assert first_emitted == 1
    assert (
        world_conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type = 'EDLI_REDECISION_PENDING'"
        ).fetchone()[0]
        == 1
    )
    payload = json.loads(
        world_conn.execute(
            "SELECT payload_json FROM opportunity_events "
            "WHERE event_type = 'EDLI_REDECISION_PENDING'"
        ).fetchone()[0]
    )
    assert payload["redecision_origin"] == "market_price"
    assert payload["price_changed_token_ids"] == ["resting-yes"]

    from src.events import price_channel_redecision_router as router

    monkeypatch.setattr(
        router,
        "_edli_screened_entry_family_keys_for_price_channel",
        lambda *_args, **_kwargs: pytest.fail(
            "an already-pending family must skip the entry screen"
        ),
    )
    second_emitted = _edli_emit_price_channel_redecisions_for_events(
        world_conn,
        trade_conn,
        forecasts_conn,
        events,
        received_at="2026-06-20T20:05:00+00:00",
    )
    assert second_emitted == 0
    assert (
        world_conn.execute(
            "SELECT COUNT(*) FROM opportunity_events WHERE event_type = 'EDLI_REDECISION_PENDING'"
        ).fetchone()[0]
        == 1
    )


def test_held_quote_refresh_orders_missing_and_oldest_feasibility_first():
    from src.ingest.price_channel_ingest import _edli_order_token_ids_by_feasibility_age
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO execution_feasibility_latest (
            evidence_id, event_id, condition_id, token_id, outcome_label,
            direction, quote_seen_at, created_at, schema_version
        ) VALUES
            ('newer', 'event-newer', 'cond', 'newer-token', 'YES', 'buy_yes',
             '2026-06-24T08:00:00+00:00', '2026-06-24T08:00:00+00:00', 1),
            ('stale', 'event-stale', 'cond', 'stale-token', 'YES', 'buy_yes',
             '2026-06-24T07:30:00+00:00', '2026-06-24T07:30:00+00:00', 1)
        """
    )

    ordered = _edli_order_token_ids_by_feasibility_age(
        conn,
        {"newer-token", "missing-token", "stale-token"},
    )

    assert ordered == ["missing-token", "stale-token", "newer-token"]


def test_price_channel_sqlite_wait_is_bounded_by_writer_hold_budget(monkeypatch, tmp_path):
    from src.ingest import price_channel_ingest as lane

    db_path = tmp_path / "contended.db"
    owner = sqlite3.connect(db_path)
    waiter = sqlite3.connect(db_path)
    owner.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY)")
    owner.commit()
    owner.execute("BEGIN IMMEDIATE")
    monkeypatch.setattr(lane, "PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS", 25)
    lane._bound_price_channel_sqlite_wait(waiter)
    started = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            waiter.execute("INSERT INTO facts DEFAULT VALUES")
        assert time.monotonic() - started < 0.5
        assert waiter.execute("PRAGMA busy_timeout").fetchone()[0] == 25
    finally:
        owner.rollback()
        waiter.close()
        owner.close()


def test_feasibility_age_reads_latest_state_without_append_scan():
    from src.ingest.price_channel_ingest import _edli_order_token_ids_by_feasibility_age
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.executemany(
        """
        INSERT INTO execution_feasibility_latest (
            token_id, direction, evidence_id, event_id, condition_id, outcome_label,
            quote_seen_at, created_at, schema_version
        ) VALUES (?, 'buy_yes', ?, ?, 'cond', 'YES', ?, ?, 1)
        """,
        [
            (
                "newer-token",
                "latest-newer",
                "event-newer",
                "2026-06-24T08:00:00+00:00",
                "2026-06-24T08:00:00+00:00",
            ),
            (
                "stale-token",
                "latest-stale",
                "event-stale",
                "2026-06-24T07:30:00+00:00",
                "2026-06-24T07:30:00+00:00",
            ),
        ],
    )
    traces: list[str] = []
    conn.set_trace_callback(traces.append)

    ordered = _edli_order_token_ids_by_feasibility_age(
        conn,
        ["newer-token", "stale-token"],
    )

    append_reads = [
        sql
        for sql in traces
        if "FROM execution_feasibility_evidence" in sql and "sqlite_master" not in sql
    ]
    assert ordered == ["stale-token", "newer-token"]
    assert append_reads == []


def test_feasibility_age_ignores_append_history():
    from src.ingest.price_channel_ingest import _edli_order_token_ids_by_feasibility_age
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.executemany(
        """
        INSERT INTO execution_feasibility_evidence (
            evidence_id, event_id, condition_id, token_id, outcome_label,
            direction, quote_seen_at, created_at, schema_version
        ) VALUES (?, ?, 'cond', ?, 'YES', 'buy_yes', ?, ?, 1)
        """,
        [
            (
                "append-newer",
                "event-newer",
                "newer-token",
                "2026-06-24T08:00:00+00:00",
                "2026-06-24T08:00:00+00:00",
            ),
            (
                "append-stale",
                "event-stale",
                "stale-token",
                "2026-06-24T07:30:00+00:00",
                "2026-06-24T07:30:00+00:00",
            ),
        ],
    )

    ordered = _edli_order_token_ids_by_feasibility_age(
        conn,
        ["newer-token", "missing-token", "stale-token"],
    )

    assert ordered == ["newer-token", "missing-token", "stale-token"]


def test_rest_quote_refresh_reuses_only_current_generation_full_depth(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    checked_at = datetime.fromisoformat("2026-07-17T05:00:10+00:00")
    generation_start = datetime.fromisoformat("2026-07-17T05:00:00+00:00")
    monkeypatch.setattr(
        lane,
        "_edli_market_channel_generation_cut",
        lambda *, checked_at, max_age: generation_start,
    )
    conn.executemany(
        """
        INSERT INTO execution_feasibility_latest (
            token_id, direction, evidence_id, event_id, condition_id, outcome_label,
            quote_seen_at, depth_before_json, created_at, schema_version
        ) VALUES (?, 'buy_yes', ?, ?, 'cond', 'YES', ?, ?, ?, 1)
        """,
        [
            (
                "fresh-depth",
                "e-fresh",
                "event-fresh",
                "2026-07-17T05:00:01+00:00",
                '{"bids": [], "asks": []}',
                "2026-07-17T05:00:01+00:00",
            ),
            (
                "prior-generation",
                "e-old",
                "event-old",
                "2026-07-17T04:59:59+00:00",
                '{"bids": [], "asks": []}',
                "2026-07-17T04:59:59+00:00",
            ),
            (
                "bba-only",
                "e-bba",
                "event-bba",
                "2026-07-17T05:00:02+00:00",
                None,
                "2026-07-17T05:00:02+00:00",
            ),
            (
                "future-depth",
                "e-future",
                "event-future",
                "2026-07-17T05:00:11+00:00",
                '{"bids": [], "asks": []}',
                "2026-07-17T05:00:11+00:00",
            ),
        ],
    )

    required, covered = lane._edli_tokens_requiring_rest_quote_refresh(
        conn,
        [
            "fresh-depth",
            "prior-generation",
            "bba-only",
            "future-depth",
            "missing",
        ],
        checked_at=checked_at,
        continuity_max_age=timedelta(seconds=1),
        evidence_max_age=timedelta(seconds=10),
    )

    assert covered == 1
    assert required == [
        "prior-generation",
        "bba-only",
        "future-depth",
        "missing",
    ]


def test_rest_quote_refresh_rejects_same_pid_prior_generation_continuity(monkeypatch, tmp_path):
    from src import config
    from src.ingest import price_channel_ingest as lane
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    checked_at = datetime.fromisoformat("2026-08-24T05:00:10+00:00")
    conn.execute(
        """
        INSERT INTO execution_feasibility_latest (
            token_id, direction, evidence_id, event_id, condition_id, outcome_label,
            quote_seen_at, depth_before_json, created_at, schema_version
        ) VALUES ('same-pid-old-generation', 'buy_yes', 'e', 'event', 'cond', 'YES',
                  '2026-08-24T05:00:05+00:00', '{"bids": [], "asks": []}',
                  '2026-08-24T05:00:05+00:00', 1)
        """
    )
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", "current-generation")
    (tmp_path / lane.MARKET_CHANNEL_SINK_READINESS_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "generation": "current-generation",
                "sink_registered": True,
                "consumer_queue_accepted": True,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / lane.MARKET_CHANNEL_CONTINUITY_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channel": "market_channel",
                "connected": True,
                "pid": os.getpid(),
                "generation": "prior-generation",
                "connected_at": "2026-08-24T05:00:00+00:00",
                "observed_at": "2026-08-24T05:00:09+00:00",
            }
        ),
        encoding="utf-8",
    )

    required, covered = lane._edli_tokens_requiring_rest_quote_refresh(
        conn,
        ["same-pid-old-generation"],
        checked_at=checked_at,
        continuity_max_age=timedelta(seconds=10),
        evidence_max_age=timedelta(seconds=10),
    )

    assert covered == 0
    assert required == ["same-pid-old-generation"]


def test_rest_quote_refresh_does_not_treat_quiet_generation_depth_as_fresh(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    checked_at = datetime.fromisoformat("2026-07-17T05:04:00+00:00")
    generation_start = datetime.fromisoformat("2026-07-17T05:00:00+00:00")
    monkeypatch.setattr(
        lane,
        "_edli_market_channel_generation_cut",
        lambda *, checked_at, max_age: generation_start,
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_latest (
            token_id, direction, evidence_id, event_id, condition_id, outcome_label,
            quote_seen_at, depth_before_json, created_at, schema_version
        ) VALUES ('quiet-held', 'buy_yes', 'quiet-evidence', 'quiet-event', 'cond',
                  'YES', '2026-07-17T05:00:01+00:00',
                  '{"bids": [{"price": "0.50", "size": "1"}], "asks": [{"price": "0.51", "size": "1"}]}',
                  '2026-07-17T05:00:01+00:00', 1)
        """
    )

    required, covered = lane._edli_tokens_requiring_rest_quote_refresh(
        conn,
        ["quiet-held"],
        checked_at=checked_at,
        continuity_max_age=timedelta(seconds=1),
        evidence_max_age=timedelta(seconds=180),
    )

    assert covered == 0
    assert required == ["quiet-held"]


def test_held_quote_refresh_skips_rest_when_ws_generation_covers_all(monkeypatch):
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda conn, **_kwargs: {"yes-token", "no-token"},
    )
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda conn: set())
    seen = {}

    def _covered(conn, token_ids, **kwargs):  # noqa: ANN001
        seen.update(kwargs)
        return [], len(token_ids)

    monkeypatch.setattr(lane, "_edli_tokens_requiring_rest_quote_refresh", _covered)
    monkeypatch.setattr(
        market_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("metadata and REST lane must remain unopened")
        ),
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection",
        lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"),
    )

    result = lane._edli_refresh_held_position_quote_evidence()
    failed, reason = lane._price_channel_quote_refresh_failed(
        result,
        token_key="held_token_metadata",
        event_key="held_quote_refresh_events",
    )

    assert result["held_quote_refresh_ws_covered_tokens"] == 2
    assert result["held_quote_refresh_attempted_tokens"] == 0
    assert seen["continuity_max_age"] == timedelta(seconds=1)
    assert seen["evidence_max_age"] == timedelta(seconds=180)
    assert failed is False
    assert reason is None


def test_candidate_quote_refresh_skips_rest_when_ws_generation_covers_all(monkeypatch):
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(
        lane,
        "_edli_candidate_priority_token_ids",
        lambda conn, *, limit: ["candidate-token"],
    )
    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda conn: set(),
    )
    monkeypatch.setattr(
        lane,
        "_edli_open_rest_priority_token_ids",
        lambda conn: {"rest-token"},
    )
    monkeypatch.setattr(
        lane,
        "_edli_tokens_requiring_rest_quote_refresh",
        lambda conn, token_ids, **kwargs: ([], len(token_ids)),
    )
    monkeypatch.setattr(
        market_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("metadata and REST lane must remain unopened")
        ),
    )
    monkeypatch.setattr(
        state_db,
        "get_world_connection",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )

    result = lane._edli_refresh_candidate_priority_quote_evidence(
        limit=4,
        budget_seconds=10.0,
    )
    failed, reason = lane._price_channel_quote_refresh_failed(
        result,
        token_key="candidate_token_metadata",
        event_key="candidate_quote_refresh_events",
    )

    assert result["candidate_quote_refresh_ws_covered_tokens"] == 2
    assert result["candidate_quote_refresh_attempted_tokens"] == 0
    assert failed is False
    assert reason is None


def test_pre_submit_book_reader_prefers_latest_without_append_scan():
    from src.events.reactor import _edli_latest_pre_submit_book_row
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO execution_feasibility_latest (
            token_id, direction, evidence_id, event_id, condition_id, outcome_label,
            quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            created_at, schema_version
        ) VALUES (
            'tok-latest', 'buy_yes', 'latest-1', 'event-1', 'cond-1', 'YES',
            '2026-06-24T08:00:00+00:00', 'hash-latest', 0.42, 0.44,
            '2026-06-24T08:00:00+00:00', 1
        )
        """
    )
    traces: list[str] = []
    conn.set_trace_callback(traces.append)

    row = _edli_latest_pre_submit_book_row(
        conn,
        token_id="tok-latest",
        side="BUY",
        decision_time=datetime.fromisoformat("2026-06-24T08:00:01+00:00"),
    )

    append_reads = [
        sql
        for sql in traces
        if "FROM execution_feasibility_evidence" in sql and "sqlite_master" not in sql
    ]
    assert row is not None
    assert row[1] == "hash-latest"
    assert append_reads == []


def test_pre_submit_book_reader_rejects_append_when_latest_side_missing():
    from src.events.reactor import _edli_latest_pre_submit_book_row
    from src.state.schema.execution_feasibility_evidence_schema import ensure_table

    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    conn.execute(
        """
        INSERT INTO execution_feasibility_latest (
            token_id, direction, evidence_id, event_id, condition_id, outcome_label,
            quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            created_at, schema_version
        ) VALUES (
            'tok-fallback', 'sell_yes', 'latest-bid-only', 'event-latest', 'cond-1', 'YES',
            '2026-06-24T08:00:00+00:00', 'hash-bid-only', 0.42, NULL,
            '2026-06-24T08:00:00+00:00', 1
        )
        """
    )
    conn.execute(
        """
        INSERT INTO execution_feasibility_evidence (
            evidence_id, event_id, condition_id, token_id, outcome_label,
            direction, quote_seen_at, book_hash_before, best_bid_before, best_ask_before,
            created_at, schema_version
        ) VALUES (
            'append-ask', 'event-append', 'cond-1', 'tok-fallback', 'YES',
            'buy_yes', '2026-06-24T07:59:00+00:00', 'hash-append', 0.41, 0.43,
            '2026-06-24T07:59:00+00:00', 1
        )
        """
    )

    traces: list[str] = []
    conn.set_trace_callback(traces.append)
    row = _edli_latest_pre_submit_book_row(
        conn,
        token_id="tok-fallback",
        side="BUY",
        decision_time=datetime.fromisoformat("2026-06-24T08:00:01+00:00"),
    )

    assert row is None
    assert all("FROM execution_feasibility_evidence" not in sql for sql in traces)


def test_held_position_quote_refresh_writes_feasibility_rows(monkeypatch, tmp_path):
    from src import config
    from src.data import polymarket_client
    from src.events.triggers.market_channel_ingestor import (
        MarketChannelIngestor,
        MarketChannelOnlineService,
        register_persistent_market_channel_action_sink,
        unregister_persistent_market_channel_action_sink,
    )
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_trade_only

    world_path = tmp_path / "world.db"
    trade_path = tmp_path / "trade.db"
    world_conn = sqlite3.connect(world_path)
    init_schema(world_conn)
    world_conn.commit()
    world_conn.close()
    trade_conn = sqlite3.connect(trade_path)
    init_schema_trade_only(trade_conn)
    trade_conn.commit()
    trade_conn.close()

    trade = sqlite3.connect(trade_path)
    trade.execute(
        """
        INSERT INTO position_current (
            position_id, phase, city, target_date, direction, strategy_key,
            updated_at, temperature_metric, token_id, no_token_id, condition_id
        ) VALUES (
            'pos-1', 'active', 'Paris', '2026-06-20', 'buy_no',
            'opening_inertia', '2026-06-19T10:00:00+00:00', 'low',
            'yes-token', 'no-token', '0xcondition'
        )
        """
    )
    trade.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
            question_id, yes_token_id, no_token_id, enable_orderbook, active,
            closed, market_end_at, min_tick_size, min_order_size,
            fee_details_json, token_map_json, neg_risk, orderbook_top_bid,
            orderbook_top_ask, orderbook_depth_json, raw_gamma_payload_hash,
            raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
            captured_at, freshness_deadline
        ) VALUES (
            'snap-1', 'gamma-1', 'event-1', 'weather-test', '0xcondition',
            'question-1', 'yes-token', 'no-token', 1, 1, 0,
                '2026-07-25T00:00:00+00:00', '0.01', '5', '{}',
            '{}', 0, '0.40', '0.60', '{}', 'gh', 'ch', 'oh',
            'CLOB', '2026-06-19T10:00:00+00:00',
            '2026-06-19T10:05:00+00:00'
        )
        """
    )
    trade.commit()
    trade.close()

    def _trade_conn(*, write_class=None, deadline_monotonic=None):  # noqa: ARG001
        return sqlite3.connect(trade_path)

    def _world_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(world_path)

    def _world_with_trades_required(*, write_class=None):  # noqa: ARG001
        conn = sqlite3.connect(world_path)
        conn.execute(f"ATTACH DATABASE '{trade_path}' AS trades")
        return conn

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.HELD_REDUCE_ONLY

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            return {
                "asset_id": token_id,
                "market": "0xcondition",
                "timestamp": "1781863200000",
                "hash": f"hash-{token_id}",
                "bids": [{"price": "0.70", "size": "10"}],
                "asks": [{"price": "0.75", "size": "10"}],
            }

    monkeypatch.setattr(state_db, "get_trade_connection", _trade_conn)
    monkeypatch.setattr(state_db, "get_world_connection", _world_conn)
    monkeypatch.setattr(state_db, "get_world_connection_with_trades_required", _world_with_trades_required)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    # Isolate the committed-REST wake: the snapshot-outcome scheduler is covered
    # independently below, while this real seed proves commit -> persistent sink.
    monkeypatch.setattr(
        lane,
        "_edli_held_snapshot_refresh_report",
        lambda *args, **kwargs: {
            "canonical_held_pair_count": 1,
            "held_snapshot_fresh_pairs": [{"condition_id": "0xcondition", "token_id": "no-token"}],
            "held_snapshot_due_pairs": [],
            "held_snapshot_refresh_debt_actions": [],
            "held_snapshot_refresh_actions_enqueued": 0,
            "held_snapshot_refresh_enqueue_unavailable": [],
        },
    )
    invalidated = []
    refreshed = []
    persistent = MarketChannelOnlineService(
        MarketChannelIngestor(
            None,
            active_token_ids=set(),
            feasibility_conn=sqlite3.connect(":memory:"),
        ),
        invalidate_snapshot=invalidated.append,
        refresh_snapshot=refreshed.append,
    )
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    generation = lane._edli_begin_market_channel_bootstrap()
    assert lane._edli_register_current_market_channel_action_sink(
        persistent,
        generation,
        register_persistent_market_channel_action_sink,
        unregister_persistent_market_channel_action_sink,
    )
    lane._write_market_channel_continuity(
        {
            "schema_version": 1,
            "channel": "market_channel",
            "generation": generation,
            "connected": True,
            "connected_at": "2026-06-19T10:00:00+00:00",
            "observed_at": "2026-06-19T10:00:00.500000+00:00",
        }
    )

    acquired = lane._candidate_quote_seed_refresh_lock.acquire(blocking=False)
    assert acquired, "candidate quote refresh must not own the held quote lane"
    try:
        result = lane._edli_refresh_held_position_quote_evidence()
    finally:
        lane._candidate_quote_seed_refresh_lock.release()
        assert persistent.wait_refresh_idle(timeout=1.0)
        lane._edli_unregister_current_market_channel_action_sink(
            persistent,
            generation,
            unregister_persistent_market_channel_action_sink,
        )

    assert result["held_priority_token_ids"] == 2
    assert result["held_token_metadata"] == 2
    assert result["held_quote_refresh_events"] == 2
    assert [(action.condition_id, action.token_id, action.reason) for action in refreshed] == [
        ("0xcondition", "no-token", "held_rest_refresh")
    ]
    assert invalidated == refreshed
    check = sqlite3.connect(trade_path)
    try:
        assert (
            check.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
            == 4
        )
    finally:
        check.close()


def test_held_position_quote_refresh_backpressures_without_db_write_or_clob(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda conn, **_kwargs: ["yes-token", "no-token"],
    )
    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda conn: {("0xcondition", "yes-token")},
    )
    monkeypatch.setattr(
        lane,
        "_edli_order_token_ids_by_feasibility_age",
        lambda conn, token_ids: list(token_ids),
    )
    monkeypatch.setattr(
        market_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda conn, token_ids, purpose="entry": {
            token_id: MarketTokenMetadata(
                condition_id="0xcondition",
                token_id=token_id,
                outcome_label="YES" if token_id == "yes-token" else "NO",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snap-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in token_ids
        },
    )
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: (_ for _ in ()).throw(AssertionError("attached write DB must not open under backpressure")),
    )
    monkeypatch.setattr(
        polymarket_client,
        "PolymarketClient",
        lambda: (_ for _ in ()).throw(AssertionError("CLOB client must not open under backpressure")),
    )

    acquired = lane._held_quote_seed_refresh_lock.acquire(blocking=False)
    assert acquired, "test requires the process-local held quote lock to be initially free"
    try:
        result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=10.0)
    finally:
        lane._held_quote_seed_refresh_lock.release()

    assert result["backpressure"] is True
    assert result["skipped"] == "price_channel_held_quote_refresh_in_progress"
    assert result["held_priority_token_ids"] == 2
    assert result["held_token_metadata"] == 2
    assert result["held_quote_refresh_events"] == 0
    assert result["held_quote_refresh_attempted_tokens"] == 0
    assert result["budget_skipped_tokens"] == 2
    assert result["canonical_held_freshness_debt_token_ids"] == ["yes-token"]
    assert result["canonical_rest_due_token_ids"] == ["yes-token"]
    assert result["canonical_rest_refreshed_token_ids"] == []


def test_held_quote_refresh_skips_missing_metadata_tokens_to_refresh_tradeable_holds(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    ordered = ["closed-old-1", "closed-old-2", "live-held-1", "live-held-2"]
    seen: dict[str, list[list[str]] | list[str]] = {"metadata": []}

    monkeypatch.setattr(
        lane,
        "_settings_section",
        lambda name, default=None: {
            "market_channel_held_quote_refresh_max_tokens_per_cycle": 2,
        } if name == "edli_v1" else default,
    )
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: set(ordered))
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda conn: set())
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, token_ids: ordered)

    def _metadata(conn, *, token_ids, purpose="entry"):  # noqa: ANN001
        batch = list(token_ids)
        assert purpose == "exit"
        seen["metadata"].append(batch)
        return {
            token_id: MarketTokenMetadata(
                condition_id="0xcondition",
                token_id=token_id,
                outcome_label="YES",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snap-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in batch
            if token_id.startswith("live-held")
        }

    class FakeService:
        rest_seed_backpressure_count = 0
        rest_seed_backpressure_reason = None

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def seed_rest_books_in_chunks(
            self, *, token_ids, post_commit_quote_sink, **kwargs  # noqa: ANN001, ANN003
        ):
            seen.setdefault("rest_seed_calls", []).append(list(token_ids))
            post_commit_quote_sink(
                [
                    types.SimpleNamespace(
                        payload_json=json.dumps({"token_id": token_id})
                    )
                    for token_id in token_ids
                ]
            )
            return len(token_ids)

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.HELD_REDUCE_ONLY

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            return {}

        def get_orderbook_snapshots(self, token_ids: list[str], *, timeout=None) -> dict:  # noqa: ANN001
            return {}

    monkeypatch.setattr(market_ingestor, "active_weather_token_metadata_for_tokens", _metadata)
    monkeypatch.setattr(market_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())
    monkeypatch.setattr(market_ingestor, "MarketChannelOnlineService", FakeService)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=10.0)

    assert seen["metadata"] == [["closed-old-1", "closed-old-2"], ["live-held-1", "live-held-2"]]
    assert seen["rest_seed_calls"] == [["live-held-1", "live-held-2"]]
    assert result["held_priority_token_ids"] == 4
    assert result["held_token_metadata"] == 2
    assert result["held_quote_refresh_selected_tokens"] == 2
    assert result["held_quote_refresh_metadata_scanned_tokens"] == 4
    assert result["held_quote_refresh_metadata_missing_tokens"] == 2
    assert result["held_quote_refresh_events"] == 2


def test_held_quote_refresh_caps_selected_tokens_before_metadata_and_rest_seed(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    ordered = [f"token-{idx}" for idx in range(10)]
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        lane,
        "_settings_section",
        lambda name, default=None: {
            "market_channel_held_quote_refresh_max_tokens_per_cycle": 3,
        } if name == "edli_v1" else default,
    )
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: set(ordered))
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda conn: set())
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, token_ids: ordered)

    def _metadata(conn, *, token_ids, purpose="entry"):  # noqa: ANN001
        selected = list(token_ids)
        assert purpose == "exit"
        seen["metadata"] = selected
        return {
            token_id: MarketTokenMetadata(
                condition_id="0xcondition",
                token_id=token_id,
                outcome_label="YES",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snap-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in selected
        }

    class FakeService:
        rest_seed_backpressure_count = 0
        rest_seed_backpressure_reason = None

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def seed_rest_books_in_chunks(self, *, token_ids, **kwargs):  # noqa: ANN001, ANN003
            selected = list(token_ids)
            seen["rest_seed"] = selected
            seen["past_end_exit_refresh"] = kwargs.get("past_end_exit_refresh")
            return len(selected)

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.HELD_REDUCE_ONLY

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            return {}

        def get_orderbook_snapshots(self, token_ids: list[str], *, timeout=None) -> dict:  # noqa: ANN001
            return {}

    monkeypatch.setattr(market_ingestor, "active_weather_token_metadata_for_tokens", _metadata)
    monkeypatch.setattr(market_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())
    monkeypatch.setattr(market_ingestor, "MarketChannelOnlineService", FakeService)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=10.0)

    assert seen["metadata"] == ordered[:3]
    assert seen["rest_seed"] == ordered[:3]
    assert seen["past_end_exit_refresh"] is True
    assert result["held_quote_refresh_selected_tokens"] == 3
    assert result["held_quote_refresh_deferred_tokens"] == 7
    assert result["held_quote_refresh_attempted_tokens"] == 3
    assert result["budget_skipped_tokens"] == 0


def test_held_quote_refresh_admits_all_native_held_sides_before_audit_backlog(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    held = {f"held-{index:02d}" for index in range(19)}
    audit = {f"audit-{index:02d}" for index in range(69)}
    seen: dict[str, list[str]] = {}
    monkeypatch.setattr(
        lane,
        "_settings_section",
        lambda name, default=None: {
            "market_channel_held_quote_refresh_max_tokens_per_cycle": 32,
        } if name == "edli_v1" else default,
    )
    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda conn: {(f"condition-{token_id}", token_id) for token_id in held},
    )
    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda conn, **_kwargs: held | audit,
    )
    monkeypatch.setattr(lane, "_edli_unsettled_global_exit_audit_token_ids", lambda conn, **_kwargs: set())
    monkeypatch.setattr(
        lane,
        "_edli_tokens_requiring_rest_quote_refresh",
        lambda conn, token_ids, **kwargs: (sorted(token_ids), 0),
    )
    monkeypatch.setattr(
        lane,
        "_edli_order_token_ids_by_feasibility_age",
        lambda conn, token_ids: sorted(token_ids),
    )

    def _metadata(conn, *, token_ids, purpose="entry"):  # noqa: ANN001
        assert purpose == "exit"
        return {
            token_id: MarketTokenMetadata(
                condition_id=f"condition-{token_id}",
                token_id=token_id,
                outcome_label="YES",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snapshot-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in token_ids
        }

    class FakeService:
        rest_seed_backpressure_count = 0
        rest_seed_backpressure_reason = None

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def seed_rest_books_in_chunks(
            self, *, token_ids, post_commit_quote_sink, **kwargs  # noqa: ANN001, ANN003
        ):
            seen.setdefault("rest_seed_calls", []).append(list(token_ids))
            post_commit_quote_sink(
                [
                    types.SimpleNamespace(
                        payload_json=json.dumps({"token_id": token_id})
                    )
                    for token_id in token_ids
                ]
            )
            return len(token_ids)

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.HELD_REDUCE_ONLY

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id, *, timeout=None):  # noqa: ANN001
            return {}

        def get_orderbook_snapshots(self, token_ids, *, timeout=None):  # noqa: ANN001
            return {}

    monkeypatch.setattr(market_ingestor, "active_weather_token_metadata_for_tokens", _metadata)
    monkeypatch.setattr(market_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())
    monkeypatch.setattr(market_ingestor, "MarketChannelOnlineService", FakeService)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=10.0)

    assert set(seen["rest_seed_calls"][0]) == held
    assert set(seen["rest_seed_calls"][1]).issubset(audit)
    assert len(seen["rest_seed_calls"][1]) == 13
    assert result["canonical_held_token_ids"] == 19
    assert result["canonical_held_freshness_debt_token_ids"] == []
    assert set(result["canonical_rest_refreshed_token_ids"]) == held
    assert result["held_quote_refresh_selected_tokens"] == 32


def test_held_quote_refresh_binds_db_bootstrap_and_finishes_native_before_audit(monkeypatch):
    """The held-side tranche cannot inherit a 30-second DB bootstrap wait."""
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health
    from src.state import db as state_db

    native, audit = "native-held", "audit-only"
    opened_deadlines = []
    seed_calls = []
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", lambda conn: {("condition-native", native)})
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: {native, audit})
    monkeypatch.setattr(lane, "_edli_unsettled_global_exit_audit_token_ids", lambda conn, **_kwargs: {audit})
    monkeypatch.setattr(lane, "_edli_tokens_requiring_rest_quote_refresh", lambda conn, tokens, **kwargs: (sorted(tokens), 0))
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, tokens: sorted(tokens))
    monkeypatch.setattr(
        lane,
        "_edli_held_snapshot_refresh_report",
        lambda *args, **kwargs: {
            "held_snapshot_fresh_pairs": [], "held_snapshot_due_pairs": [],
            "held_snapshot_refresh_debt_actions": [], "held_snapshot_refresh_actions_enqueued": 0,
            "held_snapshot_refresh_enqueue_unavailable": [],
            "held_snapshot_terminal_disposition_required": [],
        },
    )
    monkeypatch.setattr(
        lane,
        "_edli_enqueue_held_snapshot_refresh_actions",
        lambda actions: {
            "held_snapshot_refresh_actions_enqueued": len(actions),
            "held_snapshot_refresh_enqueue_unavailable": [],
        },
    )
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda conn, *, token_ids, purpose: {
            token: MarketTokenMetadata(
                condition_id=("condition-native" if token == native else f"condition-{token}"),
                token_id=token, outcome_label="YES",
                min_tick_size="0.01", min_order_size="5", neg_risk=False,
                executable_snapshot_id=f"snapshot-{token}",
            ) for token in token_ids
        },
    )
    monkeypatch.setattr(market_channel_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())

    class Service:
        rest_seed_backpressure_count = 0
        rest_seed_backpressure_reason = None
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass
        def seed_rest_books_in_chunks(self, *, token_ids, commit, post_commit_quote_sink, **kwargs):  # noqa: ANN001, ANN003
            seed_calls.append(list(token_ids))
            commit()
            post_commit_quote_sink(
                [types.SimpleNamespace(payload_json=json.dumps({"token_id": token_id})) for token_id in token_ids]
            )
            return len(token_ids)

    class Client:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority
            assert public_request_priority is RequestPriority.HELD_REDUCE_ONLY
        def __enter__(self):
            return self
        def __exit__(self, *args):  # noqa: ANN002
            return False
        def get_orderbook_snapshot(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}
        def get_orderbook_snapshots(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return {}

    def _connection(*, write_class=None, deadline_monotonic=None):  # noqa: ANN001
        opened_deadlines.append(deadline_monotonic)
        return sqlite3.connect(":memory:")

    monkeypatch.setattr(market_channel_ingestor, "MarketChannelOnlineService", Service)
    monkeypatch.setattr(state_db, "get_trade_connection", _connection)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", Client)
    monkeypatch.setattr(lane, "_edli_append_global_exit_audit_quote_evidence", lambda *_args: (_ for _ in ()).throw(TimeoutError("audit blocked")))

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=5.0)

    assert len(opened_deadlines) == 2
    assert all(deadline is not None for deadline in opened_deadlines)
    assert seed_calls == [[native], [audit]]
    assert result["canonical_rest_refreshed_token_ids"] == [native]
    assert result["audit_quote_refresh_degraded"] is True
    assert "audit blocked" in result["audit_quote_refresh_degraded_reason"]

    recorded = {}
    monkeypatch.setattr(lane, "_edli_refresh_held_position_quote_evidence", lambda: result)
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda name, **kwargs: recorded.update(name=name, **kwargs),
    )
    assert lane._edli_held_quote_refresh_cycle() is result
    assert recorded["failed"] is False


def test_held_quote_refresh_turns_canonical_sql_deadline_into_truthful_debt(monkeypatch):
    """A timed-out canonical read cannot be misreported as a healthy no-hold cycle."""
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health
    from src.state import db as state_db

    recorded = {}

    def _canonical_long_query(conn):
        conn.execute(
            """
            WITH RECURSIVE counter(value) AS (
                SELECT 0
                UNION ALL
                SELECT value + 1 FROM counter WHERE value < 100000000
            )
            SELECT sum(value) FROM counter
            """
        ).fetchone()
        return set()

    monkeypatch.setattr(
        state_db,
        "get_trade_connection",
        lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", _canonical_long_query)
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda name, **kwargs: recorded.update(name=name, **kwargs),
    )

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=0.025)

    assert result["canonical_held_scope_unavailable"] is True
    assert result["canonical_held_freshness_debt_token_ids"] == [
        "CANONICAL_HELD_SCOPE_UNAVAILABLE"
    ]
    monkeypatch.setattr(lane, "_edli_refresh_held_position_quote_evidence", lambda: result)
    assert lane._edli_held_quote_refresh_cycle() is result
    assert recorded["failed"] is True
    assert recorded["reason"] == "canonical_held_scope_unavailable"


def test_held_quote_readers_reraise_deadline_interrupt_instead_of_empty_scope():
    from src.ingest import price_channel_ingest as lane

    class _InterruptedConnection:
        def execute(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("interrupted")

    deadline = time.monotonic() - 0.001
    with pytest.raises(TimeoutError, match="deadline elapsed during SQLite reader"):
        lane._edli_held_position_priority_token_ids(
            _InterruptedConnection(),
            deadline_monotonic=deadline,
        )
    with pytest.raises(TimeoutError, match="deadline elapsed during SQLite reader"):
        lane._edli_unsettled_global_exit_audit_token_ids(
            _InterruptedConnection(),
            deadline_monotonic=deadline,
        )


def test_held_quote_refresh_turns_priority_reader_timeout_into_canonical_debt(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health
    from src.state import db as state_db

    recorded = {}
    monkeypatch.setattr(
        state_db,
        "get_trade_connection",
        lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda conn: {("condition-held", "held-token")},
    )
    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda conn, **_kwargs: (_ for _ in ()).throw(
            TimeoutError("price-channel held quote refresh deadline elapsed during SQLite reader")
        ),
    )
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda name, **kwargs: recorded.update(name=name, **kwargs),
    )

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=1.0)

    assert result["canonical_held_scope_unavailable"] is True
    assert result["canonical_held_freshness_debt_token_ids"] == [
        "CANONICAL_HELD_SCOPE_UNAVAILABLE"
    ]
    monkeypatch.setattr(lane, "_edli_refresh_held_position_quote_evidence", lambda: result)
    assert lane._edli_held_quote_refresh_cycle() is result
    assert recorded["failed"] is True
    assert recorded["reason"] == "canonical_held_scope_unavailable"


def test_held_quote_refresh_reports_canonical_capacity_debt(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health

    recorded = {}
    monkeypatch.setattr(
        lane,
        "_edli_refresh_held_position_quote_evidence",
        lambda: {
            "held_token_metadata": 33,
            "held_quote_refresh_events": 32,
            "canonical_held_freshness_debt_token_ids": ["held-32"],
        },
    )
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda name, **kwargs: recorded.update(name=name, **kwargs),
    )

    result = lane._edli_held_quote_refresh_cycle()

    assert result["scheduler_failed"] is True
    assert result["scheduler_failure_reason"] == (
        "canonical_held_freshness_capacity_exhausted"
    )
    assert recorded["failed"] is True


def test_held_post_commit_enqueue_failure_fails_scheduler_until_snapshot_outcome_is_checked(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health

    recorded = {}
    monkeypatch.setattr(
        lane,
        "_edli_refresh_held_position_quote_evidence",
        lambda: {
            "held_token_metadata": 1,
            "held_quote_refresh_events": 1,
            "held_snapshot_refresh_debt_actions": [],
            "held_snapshot_refresh_enqueue_unavailable": [
                {
                    "condition_id": "condition-held",
                    "token_id": "held-token",
                    "reason": "held_rest_refresh",
                    "debt_reason": "MarketChannelActionSinkUnavailable",
                }
            ],
        },
    )
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda name, **kwargs: recorded.update(name=name, **kwargs),
    )

    result = lane._edli_held_quote_refresh_cycle()

    assert result["scheduler_failed"] is True
    assert result["scheduler_failure_reason"] == "held_snapshot_refresh_enqueue_unavailable"
    assert recorded["failed"] is True


@pytest.mark.parametrize(
    ("canonical_count", "canonical_covered", "scope_unavailable", "freshness_debt", "audit_events", "expected_failed"),
    ((1, 1, False, [], 0, False), (1, 1, False, [], 1, False), (1, 0, False, ["held"], 1, True), (0, 0, False, [], 0, False), (1, 1, True, [], 0, True)),
)
def test_held_quote_scheduler_health_uses_canonical_scope_not_audit_backlog(
    monkeypatch, canonical_count, canonical_covered, scope_unavailable, freshness_debt, audit_events, expected_failed
):
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health

    recorded = {}
    monkeypatch.setattr(
        lane,
        "_edli_refresh_held_position_quote_evidence",
        lambda: {
            "canonical_held_pair_count": canonical_count,
            "canonical_held_quote_ws_covered_tokens": canonical_covered,
            "canonical_held_freshness_debt_token_ids": freshness_debt,
            "canonical_held_scope_unavailable": scope_unavailable,
            "held_token_metadata": 55,
            "held_quote_refresh_events": audit_events,
            "budget_skipped_tokens": 23,
            "held_snapshot_refresh_debt_actions": [],
            "held_snapshot_terminal_disposition_required": [],
            "held_snapshot_refresh_enqueue_unavailable": [],
        },
    )
    monkeypatch.setattr(scheduler_health, "_write_scheduler_health", lambda _name, **kwargs: recorded.update(kwargs))
    result = lane._edli_held_quote_refresh_cycle()
    assert recorded["failed"] is expected_failed
    if not expected_failed:
        assert result["audit_quote_refresh_degraded"] is True
        assert result["audit_quote_refresh_degraded_reason"] == (
            "quote_refresh_partial_coverage" if audit_events else "quote_refresh_budget_exhausted_no_coverage"
        )


def test_held_snapshot_debt_rebuilds_from_exact_snapshot_outcome_not_queue_state(monkeypatch, tmp_path):
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    trade_path = tmp_path / "trade.db"
    now = datetime.now(timezone.utc)
    stale_deadline = (now + timedelta(seconds=60)).isoformat()
    fresh_deadline = (now + timedelta(seconds=170)).isoformat()
    conn = sqlite3.connect(trade_path)
    conn.executescript(
        """
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            enable_orderbook INTEGER NOT NULL
        );
        CREATE TABLE executable_market_snapshot_latest (
            condition_id TEXT NOT NULL,
            selected_outcome_token_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            active INTEGER NOT NULL,
            closed INTEGER NOT NULL,
            accepting_orders INTEGER,
            yes_token_id TEXT NOT NULL,
            no_token_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            freshness_deadline TEXT NOT NULL,
            PRIMARY KEY (condition_id, selected_outcome_token_id)
        );
        CREATE TABLE executable_market_snapshot_invalidations (
            condition_id TEXT,
            token_id TEXT,
            invalidated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES ('snapshot-1', 'condition-held', 'held-token', 1)"
    )
    conn.execute(
        "INSERT INTO executable_market_snapshot_latest VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "condition-held", "held-token", "snapshot-1", 1, 0, 1,
            "held-token", "sibling-token", now.isoformat(), stale_deadline,
        ),
    )
    conn.commit()
    conn.close()
    actions = []
    monkeypatch.setattr(
        state_db,
        "get_trade_connection",
        lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(trade_path),
    )
    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda conn: {("condition-held", "held-token")},
    )
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: {"held-token"})
    monkeypatch.setattr(lane, "_edli_unsettled_global_exit_audit_token_ids", lambda conn, **_kwargs: set())
    # The quote is current in this WS generation. Snapshot debt must still emit.
    monkeypatch.setattr(
        lane,
        "_edli_tokens_requiring_rest_quote_refresh",
        lambda conn, token_ids, **kwargs: ([], len(token_ids)),
    )
    monkeypatch.setattr(
        lane,
        "_edli_enqueue_held_snapshot_refresh_actions",
        lambda pending: actions.extend(pending) or {
            "held_snapshot_refresh_actions_enqueued": len(pending),
            "held_snapshot_refresh_enqueue_unavailable": [],
        },
    )

    first = lane._edli_refresh_held_position_quote_evidence()
    assert first["held_snapshot_refresh_debt_actions"] == []
    assert first["held_snapshot_proactive_due_pairs"] == [
        {
            "condition_id": "condition-held",
            "token_id": "held-token",
            "reason": "held_snapshot_due",
            "debt_reason": "snapshot_proactive_due",
        }
    ]
    assert [(action.condition_id, action.token_id) for action in actions] == [
        ("condition-held", "held-token")
    ]

    # Simulate a process crash after queue acceptance: durable outcome is still
    # stale, so the next scheduler pass rebuilds and re-emits the exact debt.
    actions.clear()
    second = lane._edli_refresh_held_position_quote_evidence()
    assert second["held_snapshot_refresh_debt_actions"] == []
    assert second["held_snapshot_proactive_due_pairs"] == first["held_snapshot_proactive_due_pairs"]
    assert [(action.condition_id, action.token_id) for action in actions] == [
        ("condition-held", "held-token")
    ]

    conn = sqlite3.connect(trade_path)
    conn.execute(
        "UPDATE executable_market_snapshot_latest SET freshness_deadline = ?",
        (fresh_deadline,),
    )
    conn.commit()
    conn.close()
    actions.clear()
    refreshed = lane._edli_refresh_held_position_quote_evidence()
    assert refreshed["held_snapshot_refresh_debt_actions"] == []
    assert refreshed["held_snapshot_fresh_pairs"] == [
        {"condition_id": "condition-held", "token_id": "held-token"}
    ]
    assert actions == []

    invalidated_at = datetime.now(timezone.utc)
    conn = sqlite3.connect(trade_path)
    conn.execute(
        "INSERT INTO executable_market_snapshot_invalidations VALUES (?,?,?)",
        ("condition-held", "held-token", invalidated_at.isoformat()),
    )
    conn.commit()
    conn.close()
    actions.clear()
    invalidated = lane._edli_refresh_held_position_quote_evidence()
    assert invalidated["held_snapshot_refresh_debt_actions"][0]["debt_reason"] == (
        "snapshot_invalidated"
    )
    # A lost queue after invalidation remains a durable debt next cycle.
    actions.clear()
    rebuilt_invalidated = lane._edli_refresh_held_position_quote_evidence()
    assert rebuilt_invalidated["held_snapshot_refresh_debt_actions"] == (
        invalidated["held_snapshot_refresh_debt_actions"]
    )
    assert [(action.condition_id, action.token_id) for action in actions] == [
        ("condition-held", "held-token")
    ]

    replacement_capture = datetime.now(timezone.utc)
    conn = sqlite3.connect(trade_path)
    conn.execute(
        "UPDATE executable_market_snapshot_latest SET captured_at = ?, freshness_deadline = ?",
        (
            replacement_capture.isoformat(),
            (replacement_capture + timedelta(seconds=180)).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    post_invalidation_snapshot = lane._edli_refresh_held_position_quote_evidence()
    assert post_invalidation_snapshot["held_snapshot_refresh_debt_actions"] == []

    conn = sqlite3.connect(trade_path)
    conn.execute(
        "INSERT INTO executable_market_snapshot_invalidations VALUES (?,?,?)",
        ("condition-held", "held-token", (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshot_invalidations VALUES (?,?,?)",
        ("condition-other", "other-token", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    as_of_clean = lane._edli_refresh_held_position_quote_evidence()
    assert as_of_clean["held_snapshot_refresh_debt_actions"] == []

    conn = sqlite3.connect(trade_path)
    future_capture = now + timedelta(seconds=30)
    conn.execute(
        "UPDATE executable_market_snapshot_latest SET captured_at = ?, freshness_deadline = ?",
        (future_capture.isoformat(), (future_capture + timedelta(seconds=180)).isoformat()),
    )
    conn.commit()
    conn.close()
    future_row = lane._edli_refresh_held_position_quote_evidence()
    assert future_row["held_snapshot_refresh_debt_actions"][0]["debt_reason"] == (
        "snapshot_captured_after_as_of"
    )

    # A matching token under another condition is not the canonical outcome.
    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda conn: {("condition-other", "held-token")},
    )
    mismatched = lane._edli_refresh_held_position_quote_evidence()
    assert mismatched["held_snapshot_refresh_debt_actions"] == [
        {
            "condition_id": "condition-other",
            "token_id": "held-token",
            "reason": "held_snapshot_due",
            "debt_reason": "snapshot_projection_unavailable",
        }
    ]

    conn = sqlite3.connect(trade_path)
    conn.execute(
        "UPDATE executable_market_snapshot_latest SET active = 0, closed = 1 "
        "WHERE condition_id = 'condition-held' AND selected_outcome_token_id = 'held-token'"
    )
    conn.commit()
    actions.clear()
    inactive = lane._edli_held_snapshot_refresh_report(
        conn,
        {("condition-held", "held-token")},
        checked_at=datetime.now(timezone.utc),
    )
    conn.close()
    assert inactive["held_snapshot_refresh_debt_actions"] == []
    assert inactive["held_snapshot_terminal_disposition_required"] == [
        {
            "condition_id": "condition-held",
            "token_id": "held-token",
            "reason": "terminal_disposition_required: snapshot_inactive",
        }
    ]
    assert actions == []


def test_held_quote_commit_tracks_exact_native_refresh_and_rejects_audit_only_commit(monkeypatch):
    from types import SimpleNamespace

    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health
    from src.state import db as state_db

    native = "native-held"
    audit = "audit-only"
    committed: list[bool] = []
    recorded: dict[str, object] = {}

    monkeypatch.setattr(
        lane,
        "_edli_canonical_open_held_pairs",
        lambda conn: {(f"condition-{native}", native)},
    )
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: {native, audit})
    monkeypatch.setattr(lane, "_edli_unsettled_global_exit_audit_token_ids", lambda conn, **_kwargs: set())
    monkeypatch.setattr(
        lane,
        "_edli_tokens_requiring_rest_quote_refresh",
        lambda conn, token_ids, **kwargs: (sorted(token_ids), 0),
    )
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, token_ids: sorted(token_ids))
    monkeypatch.setattr(
        market_channel_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda conn, *, token_ids, purpose: {
            token_id: MarketTokenMetadata(
                condition_id=f"condition-{token_id}", token_id=token_id,
                outcome_label="YES", min_tick_size="0.01", min_order_size="5",
                neg_risk=False, executable_snapshot_id=f"snapshot-{token_id}",
            )
            for token_id in token_ids
        },
    )
    monkeypatch.setattr(market_channel_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())

    class FakeService:
        rest_seed_backpressure_count = 0
        rest_seed_backpressure_reason = None
        emit_native = True

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def seed_rest_books_in_chunks(self, *, token_ids, commit, post_commit_quote_sink, **kwargs):  # noqa: ANN001, ANN003
            commit()
            committed.append(True)
            emitted = list(token_ids) if self.emit_native else [audit]
            post_commit_quote_sink(
                [
                    SimpleNamespace(payload_json=json.dumps({"token_id": token_id}))
                    for token_id in emitted
                ]
            )
            return len(token_ids)

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id, *, timeout=None):  # noqa: ANN001
            return {}

        def get_orderbook_snapshots(self, token_ids, *, timeout=None):  # noqa: ANN001
            return {}

    monkeypatch.setattr(market_channel_ingestor, "MarketChannelOnlineService", FakeService)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(scheduler_health, "_write_scheduler_health", lambda name, **kwargs: recorded.update(name=name, **kwargs))

    result = lane._edli_held_quote_refresh_cycle()

    assert committed == [True, True]
    assert result["canonical_held_freshness_debt_token_ids"] == []
    assert result["canonical_rest_due_token_ids"] == [native]
    assert result["canonical_rest_refreshed_token_ids"] == [native]
    assert result["scheduler_failure_reason"] == "canonical_held_snapshot_refresh_debt"
    assert result["held_snapshot_refresh_debt_actions"] == [
        {
            "condition_id": f"condition-{native}",
            "token_id": native,
            "reason": "held_snapshot_due",
            "debt_reason": "snapshot_projection_unavailable",
        }
    ]
    assert recorded["failed"] is True

    FakeService.emit_native = False
    recorded.clear()
    audit_only = lane._edli_held_quote_refresh_cycle()

    assert committed == [True, True, True, True]
    assert audit_only["canonical_held_freshness_debt_token_ids"] == [native]
    assert audit_only["canonical_rest_due_token_ids"] == [native]
    assert audit_only["canonical_rest_refreshed_token_ids"] == []
    assert audit_only["scheduler_failure_reason"] == (
        "canonical_held_freshness_capacity_exhausted"
    )
    assert recorded["failed"] is True


def test_held_quote_refresh_fails_closed_when_canonical_scope_query_fails(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.observability import scheduler_health
    from src.state import db as state_db

    recorded = {}
    broad_called = False

    def _canonical_failure(conn):  # noqa: ANN001
        raise lane._CanonicalHeldScopeUnavailable("canonical_open_held_query_failed:OperationalError")

    def _broad_scope(conn, **_kwargs):  # noqa: ANN001
        nonlocal broad_called
        broad_called = True
        return {"audit-token"}

    monkeypatch.setattr(lane, "_edli_canonical_open_held_pairs", _canonical_failure)
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", _broad_scope)
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None, deadline_monotonic=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        scheduler_health,
        "_write_scheduler_health",
        lambda name, **kwargs: recorded.update(name=name, **kwargs),
    )

    result = lane._edli_held_quote_refresh_cycle()

    assert broad_called is False
    assert result["held_quote_refresh_events"] == 0
    assert result["canonical_held_scope_unavailable"] is True
    assert result["canonical_held_freshness_debt_token_ids"] == [
        "CANONICAL_HELD_SCOPE_UNAVAILABLE"
    ]
    assert result["scheduler_failed"] is True
    assert result["scheduler_failure_reason"] == "canonical_held_scope_unavailable"
    assert recorded["failed"] is True
    assert recorded["reason"] == "canonical_held_scope_unavailable"


def test_candidate_priority_quote_refresh_writes_feasibility_rows(monkeypatch, tmp_path):
    from src.data import polymarket_client
    from src.ingest.price_channel_ingest import _edli_refresh_candidate_priority_quote_evidence
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_trade_only

    world_path = tmp_path / "world.db"
    trade_path = tmp_path / "trade.db"
    world_conn = sqlite3.connect(world_path)
    init_schema(world_conn)
    world_conn.execute(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason,
            regret_bucket, token_id, decision_time, city, target_date, metric,
            family_id, bin_label, direction, created_at, schema_version
        ) VALUES (
            'regret-1', 'event-1', 'EXECUTOR_EXPRESSIBILITY',
            'EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING',
            'BOOK_GAP', 'no-token', '2026-06-19T10:00:00+00:00',
            'Paris', '2026-06-25', 'low', 'family-paris-low',
            'Will the lowest temperature in Paris be 19C?', 'buy_no',
            '2026-06-24T10:00:00+00:00', 1
        )
        """
    )
    world_conn.execute(
        "UPDATE no_trade_regret_events SET created_at = ? WHERE regret_event_id = 'regret-1'",
        (datetime.now(timezone.utc).isoformat(),),
    )
    world_conn.commit()
    world_conn.close()
    trade_conn = sqlite3.connect(trade_path)
    init_schema_trade_only(trade_conn)
    trade_conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
            question_id, yes_token_id, no_token_id, enable_orderbook, active,
            closed, market_end_at, min_tick_size, min_order_size,
            fee_details_json, token_map_json, neg_risk, orderbook_top_bid,
            orderbook_top_ask, orderbook_depth_json, raw_gamma_payload_hash,
            raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
            captured_at, freshness_deadline
        ) VALUES (
            'snap-1', 'gamma-1', 'event-1', 'weather-test', '0xcondition',
            'question-1', 'yes-token', 'no-token', 1, 1, 0,
            '2026-07-25T12:00:00+00:00', '0.01', '5', '{}',
            '{}', 0, '0.40', '0.60', '{}', 'gh', 'ch', 'oh',
            'CLOB', '2026-06-19T10:00:00+00:00',
            '2026-06-19T10:05:00+00:00'
        )
        """
    )
    trade_conn.commit()
    trade_conn.close()

    def _trade_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(trade_path)

    def _world_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(world_path)

    def _world_with_trades_required(*, write_class=None):  # noqa: ARG001
        conn = sqlite3.connect(world_path)
        conn.execute(f"ATTACH DATABASE '{trade_path}' AS trades")
        return conn

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.SUBMIT_JIT

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            return {
                "asset_id": token_id,
                "market": "0xcondition",
                "timestamp": "1781863200000",
                "hash": f"hash-{token_id}",
                "bids": [{"price": "0.70", "size": "10"}],
                "asks": [{"price": "0.75", "size": "10"}],
            }

    monkeypatch.setattr(state_db, "get_trade_connection", _trade_conn)
    monkeypatch.setattr(state_db, "get_world_connection", _world_conn)
    monkeypatch.setattr(state_db, "get_world_connection_with_trades_required", _world_with_trades_required)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = _edli_refresh_candidate_priority_quote_evidence(limit=4)

    assert result["candidate_priority_token_ids"] == 1
    assert result["candidate_token_metadata"] == 1
    assert result["candidate_quote_refresh_events"] == 1
    check = sqlite3.connect(trade_path)
    try:
        assert (
            check.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
            == 2
        )
    finally:
        check.close()


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        ("request", "quote_refresh_request_failed"),
        ("timeout", "quote_refresh_budget_exhausted_no_coverage"),
    ],
)
def test_candidate_quote_refresh_classifies_request_failure_separately_from_timeout(
    monkeypatch, failure, expected_reason
):
    from src.data import polymarket_client
    from src.data.polymarket_request_governor import RequestAdmissionDenied, RequestPriority
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import (
        MarketChannelOnlineService,
        MarketTokenMetadata,
    )
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    token_id = "candidate-token"
    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda conn, *, limit: [token_id])
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: set())
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda conn: set())
    monkeypatch.setattr(lane, "_edli_tokens_requiring_rest_quote_refresh", lambda *args, **kwargs: ([token_id], 0))
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, token_ids: list(token_ids))
    monkeypatch.setattr(
        market_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda conn, *, token_ids: {
            token_id: MarketTokenMetadata(
                condition_id="condition-candidate",
                token_id=token_id,
                outcome_label="YES",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id="snapshot-candidate",
                market_end_at="2099-01-01T00:00:00+00:00",
            )
            for token_id in token_ids
        },
    )

    singular_calls = 0

    class FakeService(MarketChannelOnlineService):
        def seed_rest_books_in_chunks(self, *, token_ids, deadline_monotonic, **kwargs):  # noqa: ANN001, ARG002
            try:
                self._fetch_rest_seed_books(
                    list(token_ids),
                    deadline_monotonic=deadline_monotonic,
                )
            except BaseException:
                return 0
            return 0

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            assert public_request_priority is RequestPriority.SUBMIT_JIT

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id, *, timeout=None):  # noqa: ANN001
            nonlocal singular_calls
            singular_calls += 1
            raise AssertionError("batch request failure must not fan out to /book")

        def get_orderbook_snapshots(self, token_ids, *, timeout=None):  # noqa: ANN001
            if failure == "request":
                raise RequestAdmissionDenied("POLYMARKET_SCAN_LEASE_BUSY")
            raise TimeoutError("candidate refresh deadline")

    monkeypatch.setattr(market_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())
    monkeypatch.setattr(market_ingestor, "MarketChannelOnlineService", FakeService)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)
    monkeypatch.setattr(state_db, "get_world_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))

    result = lane._edli_refresh_candidate_priority_quote_evidence(
        limit=1,
        budget_seconds=10.0,
    )
    failed, reason = lane._price_channel_quote_refresh_failed(
        result,
        token_key="candidate_token_metadata",
        event_key="candidate_quote_refresh_events",
    )

    assert failed is True
    assert reason == expected_reason
    assert result["budget_skipped_tokens"] == (0 if failure == "request" else 1)
    if failure == "request":
        assert result["budget_exhausted"] is False
        assert result["candidate_quote_refresh_request_failure_count"] == 1
        assert result["candidate_quote_refresh_request_failed_tokens"] == 1
        assert singular_calls == 0
        assert result["candidate_quote_refresh_failure_reasons"] == {
            token_id: "RequestAdmissionDenied: POLYMARKET_SCAN_LEASE_BUSY"
        }
    else:
        assert result["budget_exhausted"] is True
        assert result["candidate_quote_refresh_request_failure_count"] == 0
        assert result["candidate_quote_refresh_timeout_tokens"] == [token_id]
        assert singular_calls == 0


def test_candidate_quote_refresh_excludes_metadata_ineligible_tokens(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda conn, *, limit: ["ineligible-token"])
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: set())
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda conn: set())
    monkeypatch.setattr(lane, "_edli_tokens_requiring_rest_quote_refresh", lambda *args, **kwargs: (["ineligible-token"], 0))
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, token_ids: list(token_ids))
    monkeypatch.setattr(market_ingestor, "active_weather_token_metadata_for_tokens", lambda *args, **kwargs: {})
    monkeypatch.setattr(state_db, "get_world_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        polymarket_client,
        "PolymarketClient",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("ineligible token must not open CLOB client")),
    )

    result = lane._edli_refresh_candidate_priority_quote_evidence(limit=1, budget_seconds=10.0)

    assert result["candidate_token_metadata"] == 0
    assert result["candidate_quote_refresh_events"] == 0
    assert result["candidate_quote_refresh_attempted_tokens"] == 0


def test_candidate_priority_quote_refresh_backpressures_without_db_write_or_clob(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(
        lane,
        "_edli_candidate_priority_token_ids",
        lambda conn, *, limit: ["no-token"],
    )
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda conn: ["yes-token"])
    monkeypatch.setattr(
        lane,
        "_edli_order_token_ids_by_feasibility_age",
        lambda conn, token_ids: list(token_ids),
    )
    monkeypatch.setattr(
        market_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda conn, token_ids: {
            token_id: MarketTokenMetadata(
                condition_id="0xcondition",
                token_id=token_id,
                outcome_label="YES" if token_id == "yes-token" else "NO",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snap-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in token_ids
        },
    )
    monkeypatch.setattr(state_db, "get_world_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: (_ for _ in ()).throw(AssertionError("attached write DB must not open under backpressure")),
    )
    monkeypatch.setattr(
        polymarket_client,
        "PolymarketClient",
        lambda: (_ for _ in ()).throw(AssertionError("CLOB client must not open under backpressure")),
    )

    acquired = lane._candidate_quote_seed_refresh_lock.acquire(blocking=False)
    assert acquired, "test requires the process-local candidate quote lock to be initially free"
    try:
        result = lane._edli_refresh_candidate_priority_quote_evidence(limit=4, budget_seconds=10.0)
    finally:
        lane._candidate_quote_seed_refresh_lock.release()

    assert result["backpressure"] is True
    assert result["skipped"] == "price_channel_candidate_quote_refresh_in_progress"
    assert result["candidate_priority_token_ids"] == 1
    assert result["open_rest_priority_token_ids"] == 1
    assert result["quote_priority_token_ids"] == 2
    assert result["candidate_token_metadata"] == 2
    assert result["candidate_quote_refresh_events"] == 0
    assert result["candidate_quote_refresh_attempted_tokens"] == 0
    assert result["budget_skipped_tokens"] == 2


def test_candidate_quote_refresh_caps_selected_tokens_before_metadata_and_rest_seed(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    ordered = [f"candidate-{idx}" for idx in range(6)]
    seen: dict[str, list[str]] = {}

    monkeypatch.setattr(
        lane,
        "_settings_section",
        lambda name, default=None: {
            "market_channel_candidate_quote_refresh_max_tokens_per_cycle": 2,
        } if name == "edli_v1" else default,
    )
    monkeypatch.setattr(lane, "_edli_candidate_priority_token_ids", lambda conn, *, limit: ordered)
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: set())
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda conn: set())
    monkeypatch.setattr(lane, "_edli_order_token_ids_by_feasibility_age", lambda conn, token_ids: ordered)

    def _metadata(conn, *, token_ids):  # noqa: ANN001
        selected = list(token_ids)
        seen["metadata"] = selected
        return {
            token_id: MarketTokenMetadata(
                condition_id="0xcondition",
                token_id=token_id,
                outcome_label="YES",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snap-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in selected
        }

    class FakeService:
        rest_seed_backpressure_count = 0
        rest_seed_backpressure_reason = None

        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def seed_rest_books_in_chunks(self, *, token_ids, **kwargs):  # noqa: ANN001, ANN003
            selected = list(token_ids)
            seen["rest_seed"] = selected
            return len(selected)

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.SUBMIT_JIT

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            return {}

        def get_orderbook_snapshots(self, token_ids: list[str], *, timeout=None) -> dict:  # noqa: ANN001
            return {}

    monkeypatch.setattr(market_ingestor, "active_weather_token_metadata_for_tokens", _metadata)
    monkeypatch.setattr(market_ingestor, "MarketChannelIngestor", lambda *args, **kwargs: object())
    monkeypatch.setattr(market_ingestor, "MarketChannelOnlineService", FakeService)
    monkeypatch.setattr(state_db, "get_world_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = lane._edli_refresh_candidate_priority_quote_evidence(limit=32, budget_seconds=10.0)

    assert seen["metadata"] == ordered[:2]
    assert seen["rest_seed"] == ordered[:2]
    assert result["candidate_quote_refresh_selected_tokens"] == 2
    assert result["candidate_quote_refresh_deferred_tokens"] == 4
    assert result["candidate_quote_refresh_attempted_tokens"] == 2
    assert result["budget_skipped_tokens"] == 0


def test_candidate_priority_quote_refresh_budget_is_not_capped_when_held_positions_exist(monkeypatch):
    from src.data import polymarket_client
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.events.triggers.market_channel_ingestor import MarketTokenMetadata
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(
        lane,
        "_edli_candidate_priority_token_ids",
        lambda conn, *, limit: ["no-token"],
    )
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn, **_kwargs: {"held-token"})
    monkeypatch.setattr(lane, "_edli_open_rest_priority_token_ids", lambda conn: set())
    monkeypatch.setattr(
        lane,
        "_edli_order_token_ids_by_feasibility_age",
        lambda conn, token_ids: list(token_ids),
    )
    monkeypatch.setattr(
        market_ingestor,
        "active_weather_token_metadata_for_tokens",
        lambda conn, token_ids: {
            token_id: MarketTokenMetadata(
                condition_id="0xcondition",
                token_id=token_id,
                outcome_label="NO",
                min_tick_size="0.01",
                min_order_size="5",
                neg_risk=False,
                executable_snapshot_id=f"snap-{token_id}",
                market_end_at="2026-07-25T00:00:00+00:00",
            )
            for token_id in token_ids
        },
    )
    monkeypatch.setattr(state_db, "get_world_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: (_ for _ in ()).throw(AssertionError("attached write DB must not open under backpressure")),
    )
    monkeypatch.setattr(
        polymarket_client,
        "PolymarketClient",
        lambda: (_ for _ in ()).throw(AssertionError("CLOB client must not open under backpressure")),
    )

    acquired = lane._candidate_quote_seed_refresh_lock.acquire(blocking=False)
    assert acquired, "test requires the process-local candidate quote lock to be initially free"
    try:
        result = lane._edli_refresh_candidate_priority_quote_evidence(limit=4, budget_seconds=45.0)
    finally:
        lane._candidate_quote_seed_refresh_lock.release()

    assert result["backpressure"] is True
    assert result["held_priority_token_ids"] == 1
    assert result["budget_seconds"] == 45.0
    assert "held_active_budget_cap_seconds" not in result
    assert result["candidate_quote_refresh_events"] == 0


def test_open_rest_priority_quote_refresh_writes_without_candidate_regret(monkeypatch, tmp_path):
    from src.data import polymarket_client
    from src.ingest.price_channel_ingest import _edli_refresh_candidate_priority_quote_evidence
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_trade_only

    world_path = tmp_path / "world.db"
    trade_path = tmp_path / "trade.db"
    world_conn = sqlite3.connect(world_path)
    init_schema(world_conn)
    world_conn.commit()
    world_conn.close()
    trade_conn = sqlite3.connect(trade_path)
    init_schema_trade_only(trade_conn)
    trade_conn.execute(
        """
        INSERT INTO venue_commands (
            command_id, snapshot_id, envelope_id, position_id, decision_id,
            idempotency_key, intent_kind, market_id, token_id, side, size,
            price, state, created_at, updated_at
        ) VALUES (
            'entry-resting-1', 'snap-resting', 'env-resting', 'pos-resting',
            'decision-resting', 'idem-resting', 'ENTRY', '0xcondition',
            'no-token', 'BUY', 5.0, 0.75, 'ACKED',
            '2026-06-19T10:00:00+00:00', '2026-06-19T10:00:00+00:00'
        )
        """
    )
    trade_conn.execute(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
            question_id, yes_token_id, no_token_id, enable_orderbook, active,
            closed, market_end_at, min_tick_size, min_order_size,
            fee_details_json, token_map_json, neg_risk, orderbook_top_bid,
            orderbook_top_ask, orderbook_depth_json, raw_gamma_payload_hash,
            raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
            captured_at, freshness_deadline
        ) VALUES (
            'snap-resting', 'gamma-resting', 'event-resting', 'weather-test',
            '0xcondition', 'question-resting', 'yes-token', 'no-token',
            1, 1, 0, '2026-07-25T12:00:00+00:00', '0.01', '5',
            '{}', '{}', 0, '0.40', '0.60', '{}', 'gh', 'ch', 'oh',
            'CLOB', '2026-06-19T10:00:00+00:00',
            '2026-06-19T10:05:00+00:00'
        )
        """
    )
    trade_conn.commit()
    trade_conn.close()

    def _trade_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(trade_path)

    def _world_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(world_path)

    def _world_with_trades_required(*, write_class=None):  # noqa: ARG001
        conn = sqlite3.connect(world_path)
        conn.execute(f"ATTACH DATABASE '{trade_path}' AS trades")
        return conn

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.SUBMIT_JIT

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            return {
                "asset_id": token_id,
                "market": "0xcondition",
                "timestamp": "1781863200000",
                "hash": f"hash-{token_id}",
                "bids": [{"price": "0.70", "size": "10"}],
                "asks": [{"price": "0.75", "size": "10"}],
            }

    monkeypatch.setattr(state_db, "get_trade_connection", _trade_conn)
    monkeypatch.setattr(state_db, "get_world_connection", _world_conn)
    monkeypatch.setattr(state_db, "get_world_connection_with_trades_required", _world_with_trades_required)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = _edli_refresh_candidate_priority_quote_evidence(limit=4)

    assert result["candidate_priority_token_ids"] == 0
    assert result["open_rest_priority_token_ids"] == 1
    assert result["quote_priority_token_ids"] == 1
    assert result["candidate_token_metadata"] == 1
    assert result["candidate_quote_refresh_events"] == 1
    check = sqlite3.connect(trade_path)
    try:
        assert (
            check.execute("SELECT COUNT(*) FROM execution_feasibility_latest").fetchone()[0]
            == 2
        )
    finally:
        check.close()


def test_candidate_priority_quote_refresh_fetches_new_missing_book_gap_first(monkeypatch, tmp_path):
    from src.data import polymarket_client
    from src.ingest.price_channel_ingest import _edli_refresh_candidate_priority_quote_evidence
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_trade_only

    now = datetime.now(timezone.utc)
    decision_time = (now - timedelta(minutes=30)).isoformat()
    new_created_at = (now - timedelta(minutes=10)).isoformat()
    old_created_at = (now - timedelta(minutes=20)).isoformat()
    market_end_at = (now + timedelta(days=1)).isoformat()

    world_path = tmp_path / "world.db"
    trade_path = tmp_path / "trade.db"
    world_conn = sqlite3.connect(world_path)
    init_schema(world_conn)
    world_conn.executemany(
        """
        INSERT INTO no_trade_regret_events (
            regret_event_id, event_id, rejection_stage, rejection_reason,
            regret_bucket, token_id, decision_time, city, target_date, metric,
            family_id, bin_label, direction, created_at, schema_version
        ) VALUES (?, ?, 'EXECUTOR_EXPRESSIBILITY',
            'EDLI_LIVE_CERTIFICATE_BUILD_FAILED:PRE_SUBMIT_BOOK_AUTHORITY_MISSING',
                'BOOK_GAP', ?, ?,
                'Wellington', '2026-06-27', 'high', 'family-wellington-high',
                'Will the highest temperature in Wellington be 12C?', 'buy_no',
                ?, 1
        )
        """,
        [
            ("regret-new", "event-new", "zz-new-token", decision_time, new_created_at),
            ("regret-old", "event-old", "aa-old-token", decision_time, old_created_at),
        ],
    )
    world_conn.commit()
    world_conn.close()

    trade_conn = sqlite3.connect(trade_path)
    init_schema_trade_only(trade_conn)
    trade_conn.executemany(
        """
        INSERT INTO executable_market_snapshots (
            snapshot_id, gamma_market_id, event_id, event_slug, condition_id,
            question_id, yes_token_id, no_token_id, enable_orderbook, active,
            closed, market_end_at, min_tick_size, min_order_size,
            fee_details_json, token_map_json, neg_risk, orderbook_top_bid,
            orderbook_top_ask, orderbook_depth_json, raw_gamma_payload_hash,
            raw_clob_market_info_hash, raw_orderbook_hash, authority_tier,
            captured_at, freshness_deadline
        ) VALUES (?, ?, ?, 'weather-test', ?, ?,
            ?, ?, 1, 1, 0, ?, '0.01', '5',
            '{}', '{}', 0, '0.40', '0.60', '{}', 'gh', 'ch', 'oh',
            'CLOB', '2026-06-25T16:00:00+00:00',
            '2026-06-25T16:05:00+00:00'
        )
        """,
        [
            ("snap-new", "gamma-new", "event-new", "0xnew", "question-new", "yes-new", "zz-new-token", market_end_at),
            ("snap-old", "gamma-old", "event-old", "0xold", "question-old", "yes-old", "aa-old-token", market_end_at),
        ],
    )
    trade_conn.commit()
    trade_conn.close()

    def _trade_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(trade_path)

    def _world_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(world_path)

    def _world_with_trades_required(*, write_class=None):  # noqa: ARG001
        conn = sqlite3.connect(world_path)
        conn.execute(f"ATTACH DATABASE '{trade_path}' AS trades")
        return conn

    fetch_order: list[str] = []

    class FakePolymarketClient:
        def __init__(self, *, public_request_priority=None):  # noqa: ANN001
            from src.data.polymarket_request_governor import RequestPriority

            assert public_request_priority is RequestPriority.SUBMIT_JIT

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        def get_orderbook_snapshot(self, token_id: str, *, timeout=None) -> dict:  # noqa: ANN001
            fetch_order.append(token_id)
            market = {
                "zz-new-token": "0xnew",
                "aa-old-token": "0xold",
            }[token_id]
            return {
                "asset_id": token_id,
                "market": market,
                "timestamp": "1781863200000",
                "hash": f"hash-{token_id}",
                "bids": [{"price": "0.70", "size": "10"}],
                "asks": [{"price": "0.75", "size": "10"}],
            }

    monkeypatch.setattr(state_db, "get_trade_connection", _trade_conn)
    monkeypatch.setattr(state_db, "get_world_connection", _world_conn)
    monkeypatch.setattr(state_db, "get_world_connection_with_trades_required", _world_with_trades_required)
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = _edli_refresh_candidate_priority_quote_evidence(limit=4, budget_seconds=30.0)

    assert result["candidate_quote_refresh_events"] == 2
    assert fetch_order[:2] == ["zz-new-token", "aa-old-token"]


# ===========================================================================
# SUPERIORITY INVARIANTS (the lift makes the reduce_only-forever latch
# unconstructable in the order daemon process)
# ===========================================================================

def test_superiority_order_daemon_does_not_start_the_ws_ingestor_thread():
    """src.main MUST NOT start the WS ingestor thread (the latch WRITER moves to P3).

    The reduce_only-forever latch (src/main.py:2610-2622 history) was: the WS thread, on
    auth/transport failure, called ws_gap_guard.record_gap(AUTH_FAILED), which latched the
    PROCESS-GLOBAL submit guard that the order daemon's executor reads via
    assert_ws_allows_submit — poisoning new submits forever IN THE ORDER DAEMON'S OWN
    PROCESS. The structural fix: the WS thread no longer runs in the order daemon, so its
    record_gap can never write the order daemon's in-memory submit latch. Proven by: boot
    must not call _start_user_channel_ingestor_if_enabled.
    """
    called = _called_func_names(_MAIN_PY)
    assert "_start_user_channel_ingestor_if_enabled" not in called, (
        "src.main must NOT call _start_user_channel_ingestor_if_enabled at boot — the WS "
        "ingestor thread (the ws_gap_guard latch WRITER) is lifted to P3 so a WS flap can "
        "no longer poison the order daemon's in-process submit latch (reduce_only-forever)."
    )


def test_superiority_order_daemon_does_not_define_the_lifted_ws_producers():
    """src.main no longer DEFINES the lifted WS producers (no dead duplicate to re-arm).

    A duplicate def in src.main would let a future edit re-start the WS thread in the order
    process, re-introducing the shared-memory latch. The category must be unconstructable
    in P1 too.
    """
    import src.main as main_mod

    for fn in _LIFTED_PRODUCERS:
        defined_here = (
            fn in main_mod.__dict__
            and getattr(getattr(main_mod, fn), "__module__", "") == "src.main"
        )
        assert not defined_here, (
            f"{fn} must not be DEFINED in src.main after the lift (it lives in "
            "src.ingest.price_channel_ingest)."
        )


def test_superiority_src_main_no_longer_registers_the_two_lifted_cycles():
    """src.main registers EXACTLY the two P3 cycles fewer — both channel cycles are gone."""
    ids = _add_job_ids(_MAIN_PY)
    names = _add_job_first_positional_names(_MAIN_PY)
    for jid in _LIFTED_JOB_IDS:
        assert jid not in ids, (
            f"src.main must NOT register id={jid!r} anymore — it is lifted to P3."
        )
    assert "_edli_market_channel_ingestor_cycle" not in names, (
        "src.main must not register _edli_market_channel_ingestor_cycle anymore."
    )
    assert "_edli_user_channel_reconcile_cycle" not in names, (
        "src.main must not register _edli_user_channel_reconcile_cycle anymore."
    )


def test_superiority_ws_failure_latch_is_not_written_in_order_daemon_process():
    """RELATIONSHIP TEST: a WS auth/transport flap does NOT poison the order daemon's submit latch.

    This is the antibody for the reduce_only-forever latch. We import the ORDER DAEMON's
    boot+registration surface (src.main) and the ORDER DAEMON's submit gate
    (executor._assert_ws_gap_allows_submit reads ws_gap_guard). The producer that WRITES
    the gap latch (record_gap on AUTH_FAILED) is _start_user_channel_ingestor_if_enabled /
    the WS thread runner. After the lift NEITHER is reachable from the order daemon process:
    src.main does not call the starter and does not define the thread runner. Therefore a
    WS flap (which calls record_gap inside the P3 process) cannot mutate the order daemon's
    ws_gap_guard._status — the two processes have independent module memory. The order
    daemon's submit latch can only ever be written by code that RUNS in the order daemon,
    and no such WS-failure writer runs there anymore.
    """
    # The order daemon's submit gate still READS the guard (the consumer side is retained).
    executor_src = _EXECUTOR_PY.read_text(encoding="utf-8")
    assert "assert_ws_allows_submit" in executor_src, (
        "the order daemon's executor must keep reading the ws_gap_guard submit latch — the "
        "CONSUMER side stays; only the failure-state WRITER (the WS thread) is lifted out."
    )
    # The order daemon process contains NO WS-failure WRITER: src.main neither calls the
    # starter at boot nor defines the thread runner that calls record_gap(AUTH_FAILED).
    called = _called_func_names(_MAIN_PY)
    assert "_start_user_channel_ingestor_if_enabled" not in called, (
        "no WS-failure latch writer may run in the order daemon process."
    )
    main_src = _MAIN_PY.read_text(encoding="utf-8")
    # The AUTH_FAILED record_gap writer (the WS thread) must not be DEFINED in src.main.
    assert "def _start_user_channel_ingestor_if_enabled" not in main_src, (
        "the WS thread starter (which arms the record_gap AUTH_FAILED latch writer) must "
        "not be defined in src.main — it is lifted to P3, so the latch is written only in "
        "the P3 address space, never the order daemon's."
    )


def test_superiority_lifted_module_owns_the_ws_failure_latch_writer():
    """The lifted module is where the ws_gap_guard FAILURE writer now lives (containment proof).

    The mirror of the above: the WS-failure latch writer (record_gap on a build/auth
    failure) is now INSIDE the P3 lane module. A flap there writes P3's ws_gap_guard memory
    — contained in P3 — and surfaces to P1 only as stale/absent feasibility rows.
    """
    src = _PRICE_CHANNEL_MODULE.read_text(encoding="utf-8")
    assert "record_gap" in src, (
        "the lifted price-channel module must contain the ws_gap_guard.record_gap failure "
        "writer — the WS-failure state is now produced inside the P3 process, contained."
    )


# ===========================================================================
# NEW PROCESS ARTIFACTS (the lift creates a real, bootable program boundary)
# ===========================================================================

def test_new_daemon_entry_point_exists_and_starts_ws_and_registers_both_cycles():
    """The new daemon entry-point exists, starts the WS thread, and registers both cycles.

    Mirrors the existing daemon pattern (src/ingest/substrate_observer_daemon.py). The WS
    ingestor thread must be STARTED (so fills keep being bridged) and both channel cycles
    must be registered on the NEW scheduler.
    """
    assert _PRICE_CHANNEL_DAEMON.exists(), (
        "src/ingest/price_channel_daemon.py must exist (new P3 entry-point)."
    )
    daemon_src = _PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8")
    assert "_start_user_channel_ingestor_if_enabled" in daemon_src, (
        "the new P3 daemon must START the WS user-channel ingestor thread (the persistent "
        "WS lifecycle is the reason P3 is its own service, §6 co-location)."
    )
    ids = _add_job_ids(_PRICE_CHANNEL_DAEMON)
    for jid in _LIFTED_JOB_IDS:
        assert jid in ids, (
            f"the new price-channel daemon must register id={jid!r} so the lifted producer "
            "keeps writing the durable fill bridge + feasibility evidence."
        )


def test_market_channel_first_fire_is_staggered_from_held_quote_refresh():
    """Candidate and held quote refresh must not start on the same second.

    Both refresh lanes share the process-local REST seed lock. Starting both
    interval jobs immediately made the candidate lane lose the lock every
    minute, leaving executable candidate snapshots stale while held quotes
    refreshed successfully.
    """
    daemon_src = _PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8")
    assert "MARKET_CHANNEL_FIRST_FIRE_DELAY_SECONDS = 30" in daemon_src
    assert "next_run_time=datetime.now(timezone.utc)" in daemon_src
    assert "timedelta(seconds=MARKET_CHANNEL_FIRST_FIRE_DELAY_SECONDS)" in daemon_src


def test_new_daemon_does_not_import_trading_lane():
    """The new daemon module must NOT import the trading lane (whole-process isolation)."""
    src = _PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = (
        "src.main", "src.engine", "src.execution", "src.strategy", "src.signal",
    )
    offending: list[str] = []
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == p or alias.name.startswith(p + ".") for p in forbidden_prefixes):
                    offending.append(alias.name)
            continue
        if mod and any(mod == p or mod.startswith(p + ".") for p in forbidden_prefixes):
            offending.append(mod)
    assert not offending, (
        f"src.ingest.price_channel_daemon must not import the trading lane: {offending}"
    )


def test_new_daemon_has_module_provenance_header():
    """File-header provenance rule (operator law): Created/Last-audited + Authority basis."""
    head = "\n".join(_PRICE_CHANNEL_DAEMON.read_text(encoding="utf-8").splitlines()[:15])
    assert "2026-06-08" in head, "new daemon must carry a 2026-06-08 provenance date"
    assert "system_decomposition_plan" in head, (
        "new daemon must cite system_decomposition_plan as its authority basis"
    )


def test_new_lane_module_has_module_provenance_header():
    """File-header provenance rule for the lifted lane module too."""
    head = "\n".join(_PRICE_CHANNEL_MODULE.read_text(encoding="utf-8").splitlines()[:15])
    assert "2026-06-08" in head, "new lane module must carry a 2026-06-08 provenance date"
    assert "system_decomposition_plan" in head, (
        "new lane module must cite system_decomposition_plan as its authority basis"
    )


def test_launchd_plist_artifact_exists_and_targets_the_new_daemon():
    """The launchd .plist artifact exists, labels com.zeus.price-channel-ingest, runs the daemon.

    ARTIFACT ONLY — this test does NOT load/install the service. It asserts the plist is a
    well-formed launchd job mirroring the existing com.zeus.* pattern and points its
    ProgramArguments at `-m src.ingest.price_channel_daemon`.
    """
    assert _PRICE_CHANNEL_PLIST.exists(), (
        "deploy/launchd/com.zeus.price-channel-ingest.plist artifact must exist."
    )
    text = _PRICE_CHANNEL_PLIST.read_text(encoding="utf-8")
    assert "com.zeus.price-channel-ingest" in text, (
        "plist Label must be com.zeus.price-channel-ingest"
    )
    assert "src.ingest.price_channel_daemon" in text, (
        "plist ProgramArguments must launch `-m src.ingest.price_channel_daemon`."
    )
    import plistlib

    with _PRICE_CHANNEL_PLIST.open("rb") as fh:
        parsed = plistlib.load(fh)
    assert parsed.get("Label") == "com.zeus.price-channel-ingest"
    assert "src.ingest.price_channel_daemon" in parsed.get("ProgramArguments", [])
    env = parsed.get("EnvironmentVariables") or {}
    assert env.get("POLYMARKET_CLOB_V2_SIGNATURE_TYPE") == "2"


# ===========================================================================
# CALLER-SIDE NO-REGRESSION INVARIANTS (R2 fix 2026-06-08).
#
# The original P3 commit moved the producers and repointed FIVE test files, but
# left THREE test modules still bound to `src.main` for the lifted symbols
# (test_live_order_reconcile.py, test_chain_sync_exit_wired_in_edli_mode.py,
# test_edli_online_invariants.py). Those are NOT a producer-side gap — they are a
# broken Module-A→Module-B relationship: a CONSUMER (the test harness) still names a
# symbol that no longer lives where it points. Code review of the producer module
# could never catch this (the producer is correct); only a relationship assertion
# across the caller surface catches it. These tests pin that surface so the
# repoint cannot silently regress again.
# ===========================================================================

_TESTS_ROOT = _REPO_ROOT / "tests"


def _python_files_referencing_main_dot(symbol: str) -> list[str]:
    """Test files whose SOURCE text references `main.<symbol>` or `src.main` attr `<symbol>`.

    A grep-equivalent over the test tree, but scoped to the lifted-symbol token so it
    only flags genuine stale bindings to the order-daemon host.
    """
    hits: list[str] = []
    for path in _TESTS_ROOT.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8")
        # `main.<symbol>` (the monkeypatch target / __wrapped__ caller form) or an
        # attribute access on the src.main module object for the lifted symbol.
        if f"main.{symbol}" in text or f'setattr(main, "{symbol}"' in text:
            # Confirm the file binds `main` to the ORDER DAEMON, not the lane module
            # (tests that do `from src.ingest import price_channel_ingest as main` are
            # the CORRECT repointed form and must NOT be flagged).
            binds_order_daemon = (
                "import src.main as main" in text
                or "from src import main\n" in text
                or "import src.main as main\n" in text
            )
            binds_lane_module = "price_channel_ingest as main" in text
            if binds_order_daemon and not binds_lane_module:
                hits.append(str(path.relative_to(_REPO_ROOT)))
    return sorted(set(hits))


def test_no_regression_no_test_binds_lifted_producers_to_the_order_daemon():
    """RELATIONSHIP: after the P3 lift, NO test may reach a lifted producer via `src.main`.

    The lifted symbols (`_edli_user_channel_reconcile_cycle`,
    `_start_user_channel_ingestor_if_enabled`) no longer exist on the order daemon
    module. Any test that still does `main.<sym>` / `monkeypatch.setattr(main, "<sym>")`
    against `src.main` is a stale cross-module binding that raises AttributeError. This is
    the exact regression the first P3 commit left behind; this test makes it
    unconstructable to ship again.
    """
    offenders: dict[str, list[str]] = {}
    for symbol in (
        "_edli_user_channel_reconcile_cycle",
        "_start_user_channel_ingestor_if_enabled",
    ):
        files = _python_files_referencing_main_dot(symbol)
        if files:
            offenders[symbol] = files
    assert not offenders, (
        "Lifted P3 producers are still bound to the order daemon (src.main) in these "
        f"test files — they must repoint to src.ingest.price_channel_ingest: {offenders}"
    )


def test_no_regression_lifted_reconcile_cycle_invokable_in_new_host():
    """RELATIONSHIP: the lifted reconcile cycle is a BARE callable on its new host.

    Two cross-module facts the repointed tests now depend on, pinned here so they cannot
    drift:
      (1) `_edli_user_channel_reconcile_cycle` is importable from
          src.ingest.price_channel_ingest and is a plain function — it is NO LONGER
          `@_scheduler_job`-decorated in the module (the daemon applies the health
          wrapper at add_job time, the P2 pattern), so it has NO `.__wrapped__`. Tests
          must call it directly, not via `.__wrapped__()`.
      (2) The cycle reads `settings` from the lane module's OWN module global (via
          `_settings_section`), so patching the lane module's `settings` attribute is the
          correct boot-config seam — the order-daemon `settings` is irrelevant to it.
    """
    from src.ingest import price_channel_ingest as lane

    fn = lane._edli_user_channel_reconcile_cycle
    assert callable(fn)
    assert not hasattr(fn, "__wrapped__"), (
        "the lane-module cycle must be a BARE function (daemon wraps it at registration); "
        "tests calling `.__wrapped__()` would mis-bind."
    )
    # The config seam the repointed tests patch: lane.settings is the module global the
    # cycle consults through _settings_section.
    assert hasattr(lane, "settings")
    assert hasattr(lane, "_settings_section")


def test_price_channel_settings_section_accepts_live_edli_alias(monkeypatch):
    """Live settings use `edli`; the lifted lane must not silently no-op on old `edli_v1`."""
    from src.ingest import price_channel_ingest as lane

    monkeypatch.setattr(lane, "settings", {"edli": {"enabled": True}})

    assert lane._settings_section("edli_v1") == {"enabled": True}


def test_market_channel_continuity_proof_is_atomically_published(monkeypatch, tmp_path):
    from src import config
    from src.ingest import price_channel_ingest as lane

    target = tmp_path / lane.MARKET_CHANNEL_CONTINUITY_FILENAME
    monkeypatch.setattr(config, "state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_generation", None)
    monkeypatch.setattr(lane, "_market_channel_bootstrap_started_monotonic", None)
    generation = lane._edli_begin_market_channel_bootstrap()
    service = object()
    calls: list[object] = []
    assert lane._edli_register_current_market_channel_action_sink(
        service,
        generation,
        calls.append,
        calls.append,
    )
    with pytest.raises(RuntimeError, match="generation is not current"):
        lane._write_market_channel_continuity(
            {
                "schema_version": 1,
                "channel": "market_channel",
                "generation": "prior-generation",
                "connected": True,
            }
        )
    assert not target.exists()
    lane._write_market_channel_continuity(
        {
            "schema_version": 1,
            "channel": "market_channel",
            "generation": generation,
            "connected": True,
            "connected_at": "2026-07-17T03:00:00+00:00",
            "observed_at": "2026-07-17T03:00:00.500000+00:00",
            "active_token_count": 154,
        }
    )

    proof = json.loads(target.read_text(encoding="utf-8"))
    assert proof["connected"] is True
    assert proof["generation"] == generation
    assert proof["active_token_count"] == 154
    assert isinstance(proof["pid"], int) and proof["pid"] > 0
    assert not list(tmp_path.glob("*.tmp"))
    lane._edli_unregister_current_market_channel_action_sink(service, generation, calls.append)


def test_no_regression_market_channel_online_service_wiring_lives_in_lane_module():
    """RELATIONSHIP: the market-channel online-service wiring moved to the lane module.

    test_edli_online_invariants asserted `run_market_channel_service_forever` was present
    in `src/main.py`. After the lift that wiring is in the lane module. Pin the new
    location so the source-text assertion repoints with proof, not assumption.
    """
    lane_src = _PRICE_CHANNEL_MODULE.read_text(encoding="utf-8")
    main_src = _MAIN_PY.read_text(encoding="utf-8")
    assert "run_market_channel_service_forever" in lane_src
    assert "get_orderbook_snapshot" in lane_src
    # And it is GONE from the order daemon (the lift, not a copy).
    assert "run_market_channel_service_forever" not in main_src


def test_market_channel_snapshot_refresh_uses_shared_substrate_and_trade_write_coordinator():
    """The lifted price-channel lane must not race main/substrate snapshot writers."""

    lane_src = _PRICE_CHANNEL_MODULE.read_text(encoding="utf-8")
    assert 'acquire_lock("market_substrate_priority_refresh")' in lane_src
    assert "public_request_priority=RequestPriority.SUBMIT_JIT" in lane_src
    assert "_edli_price_channel_trade_write_context_factory(" in lane_src
    assert "snapshot_write_context_factory=" in lane_src
    assert "price_channel_snapshot_invalidate" in lane_src
    assert "db_writer_lock(_zeus_trade_db_path(), WriteClass.LIVE)" not in lane_src
    assert "refresh_executable_market_substrate_snapshots(" in lane_src
