# Created: 2026-06-08
# Last reused or audited: 2026-07-17 (price-channel durable-event reactor wake)
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row + co-location decision),
#   §7 (I2 no-back-coupling: durable fill bridge + execution_feasibility_evidence),
#   §8 Step 3 (lift the user-channel WS thread + market-channel + reconcile cycles),
#   §9 (regression-unconstructable proof — failure-domain isolation).
# Lifecycle: created=2026-06-08; last_reviewed=2026-07-17; last_reused=2026-07-17
# Purpose: RELATIONSHIP TESTS for process-topology refactor STEP P3 — lift the
#   price-channel / CLOB-fact ingest (the persistent user/market WebSocket lifecycle)
#   out of the order daemon into its own process (com.zeus.price-channel-ingest).
#
# These tests verify CROSS-MODULE INVARIANTS (Module A's output → Module B), not just
# function behaviour:
#   (NO-REGRESSION) the WS producer + the two channel/reconcile cycles still EXIST and
#     still write the durable fill bridge + execution_feasibility_evidence the order
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
import inspect
import json
import sqlite3
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
_LIFTED_JOB_IDS = ("edli_market_channel_ingestor", "edli_user_channel_reconcile")

# The lifted producer surface that must live in the new P3 lane module.
_LIFTED_PRODUCERS = (
    "_start_user_channel_ingestor_if_enabled",
    "_edli_market_channel_ingestor_cycle",
    "_edli_user_channel_reconcile_cycle",
)


def test_market_channel_bootstrap_separates_entry_and_held_exit_metadata() -> None:
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
    assert len(entry_calls) == 1
    assert any(keyword.arg == "priority_token_ids" for keyword in entry_calls[0].keywords)
    assert len(exit_calls) == 1
    assert any(
        keyword.arg == "purpose"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == "exit"
        for keyword in exit_calls[0].keywords
    )


def test_candidate_quote_refresh_budget_matches_live_redecision_surface() -> None:
    from src.ingest import price_channel_ingest as pci

    assert 30.0 <= pci.MARKET_CHANNEL_CANDIDATE_QUOTE_REFRESH_BUDGET_SECONDS_DEFAULT < 60.0
    assert pci.MARKET_CHANNEL_PRIORITY_QUOTE_REFRESH_CHUNK_SIZE_DEFAULT <= 4
    assert pci.PRICE_CHANNEL_DB_WRITE_LEASE_DEADLINE_MS <= 25
    assert pci.PRICE_CHANNEL_DB_WRITE_MAX_HOLD_MS <= 1000


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
    # And it must still be invoked during boot (called inside main()).
    assert "_edli_boot_fill_bridge_recovery" in _called_func_names(_MAIN_PY), (
        "_edli_boot_fill_bridge_recovery must still be CALLED at boot in src.main."
    )


def test_no_regression_order_runtime_still_reads_feasibility_evidence():
    """P1's pre-submit witness MUST keep reading execution_feasibility_evidence (the I2 read side).

    A WS outage surfaces to P1 ONLY as stale/absent execution_feasibility_evidence rows —
    so the order runtime must still consume that table (the DB-mediated seam), independent
    of where the WS producer lives. The retained reader now lives with the
    global auction adapter after price-channel redecision was decoupled from
    ``src.main``.
    """
    from src.engine import event_reactor_adapter as adapter

    assert hasattr(adapter, "_latest_market_channel_book_rows"), (
        "the order runtime must keep its pre-submit feasibility reader "
        "(_latest_market_channel_book_rows) — the DB-mediated I2 read side P1 keeps."
    )
    reader_src = inspect.getsource(adapter._latest_market_channel_book_rows)
    assert "execution_feasibility_evidence" in reader_src, (
        "the order runtime's pre-submit witness must still SELECT "
        "execution_feasibility_evidence — the DB-mediated I2 read side P1 keeps."
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

    assert _edli_market_channel_seed_first_token_ids(
        held_priority_token_ids={"held-yes", "held-no"},
        open_rest_priority_token_ids={"rest-no"},
        candidate_priority_token_ids={"candidate-yes", "candidate-no"},
    ) == {"held-yes", "held-no", "rest-no", "candidate-yes", "candidate-no"}


def test_market_channel_seed_first_falls_back_to_candidates_without_open_positions():
    from src.ingest.price_channel_ingest import _edli_market_channel_seed_first_token_ids

    assert _edli_market_channel_seed_first_token_ids(
        held_priority_token_ids=set(),
        candidate_priority_token_ids={"candidate-yes", "candidate-no"},
    ) == {"candidate-yes", "candidate-no"}


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
    assert "resting_families = _edli_resting_family_keys_for_tokens" in src
    assert "families = held_families | entry_families | resting_families" in src
    assert src.index("families = held_families | entry_families") < src.index(
        "trigger.build_committed_snapshot_events"
    )
    assert src.index("resting_families = _edli_resting_family_keys_for_tokens") < src.index(
        "trigger.build_committed_snapshot_events"
    )
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


def test_price_channel_redecision_sink_closes_reads_before_world_writer(monkeypatch):
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

    def write(_conn, events):  # noqa: ANN001
        assert len(events) == 1
        assert events[0].event_id == "evt-price-1"
        order.append("write:redecision")
        return 1

    monkeypatch.setattr(router, "_edli_write_price_channel_redecision_events", write)
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
        "close:forecasts",
        "close:trade",
        "close:world",
        "enter:world-writer",
        "write:redecision",
        "commit:world",
        "exit:world-writer",
        "wake:{'source': 'price_channel_redecision_router', "
        "'reason': 'market_price_advanced', 'event_ids': ('evt-price-1',)}",
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
        def close(self) -> None:
            self.closed = True

    class BusyMutex:
        def acquire(self, *, timeout: float) -> bool:
            self.timeout = timeout
            return False

    conn = Connection()
    conn.closed = False
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
    assert conn.closed is True


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


def test_market_channel_forever_uses_coalesced_redecision_sink():
    from src.ingest import price_channel_ingest

    source = inspect.getsource(price_channel_ingest._edli_market_channel_ingestor_cycle)
    assert "_edli_coalesced_price_channel_redecision_sink" in source
    assert "market_event_sink=_edli_price_channel_redecision_sink" not in source


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
        received_at="2026-06-20T05:00:00+00:00",
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
        received_at="2026-06-20T05:05:00+00:00",
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
        INSERT INTO execution_feasibility_evidence (
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
        max_age=timedelta(seconds=1),
    )

    assert covered == 1
    assert required == [
        "prior-generation",
        "bba-only",
        "future-depth",
        "missing",
    ]


def test_held_quote_refresh_skips_rest_when_ws_generation_covers_all(monkeypatch):
    from src.events.triggers import market_channel_ingestor as market_ingestor
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db

    monkeypatch.setattr(
        lane,
        "_edli_held_position_priority_token_ids",
        lambda conn: {"yes-token", "no-token"},
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
        "get_trade_connection",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )

    result = lane._edli_refresh_held_position_quote_evidence()
    failed, reason = lane._price_channel_quote_refresh_failed(
        result,
        token_key="held_token_metadata",
        event_key="held_quote_refresh_events",
    )

    assert result["held_quote_refresh_ws_covered_tokens"] == 2
    assert result["held_quote_refresh_attempted_tokens"] == 0
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


def test_pre_submit_book_reader_falls_back_to_append_when_latest_side_missing():
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

    row = _edli_latest_pre_submit_book_row(
        conn,
        token_id="tok-fallback",
        side="BUY",
        decision_time=datetime.fromisoformat("2026-06-24T08:00:01+00:00"),
    )

    assert row is not None
    assert row[1] == "hash-append"


def test_held_position_quote_refresh_writes_feasibility_rows(monkeypatch, tmp_path):
    from src.data import polymarket_client
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

    def _trade_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(trade_path)

    def _world_conn(*, write_class=None):  # noqa: ARG001
        return sqlite3.connect(world_path)

    def _world_with_trades_required(*, write_class=None):  # noqa: ARG001
        conn = sqlite3.connect(world_path)
        conn.execute(f"ATTACH DATABASE '{trade_path}' AS trades")
        return conn

    class FakePolymarketClient:
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

    acquired = lane._candidate_quote_seed_refresh_lock.acquire(blocking=False)
    assert acquired, "candidate quote refresh must not own the held quote lane"
    try:
        result = lane._edli_refresh_held_position_quote_evidence()
    finally:
        lane._candidate_quote_seed_refresh_lock.release()

    assert result["held_priority_token_ids"] == 2
    assert result["held_token_metadata"] == 2
    assert result["held_quote_refresh_events"] == 2
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
        lambda conn: ["yes-token", "no-token"],
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
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn: set(ordered))
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

        def seed_rest_books_in_chunks(self, *, token_ids, **kwargs):  # noqa: ANN001, ANN003
            seen["rest_seed"] = list(token_ids)
            return len(token_ids)

    class FakePolymarketClient:
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
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=10.0)

    assert seen["metadata"] == [["closed-old-1", "closed-old-2"], ["live-held-1", "live-held-2"]]
    assert seen["rest_seed"] == ["live-held-1", "live-held-2"]
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
    seen: dict[str, list[str]] = {}

    monkeypatch.setattr(
        lane,
        "_settings_section",
        lambda name, default=None: {
            "market_channel_held_quote_refresh_max_tokens_per_cycle": 3,
        } if name == "edli_v1" else default,
    )
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn: set(ordered))
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
            return len(selected)

    class FakePolymarketClient:
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
    monkeypatch.setattr(state_db, "get_trade_connection", lambda *, write_class=None: sqlite3.connect(":memory:"))
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        lambda *, write_class=None: sqlite3.connect(":memory:"),
    )
    monkeypatch.setattr(polymarket_client, "PolymarketClient", FakePolymarketClient)

    result = lane._edli_refresh_held_position_quote_evidence(budget_seconds=10.0)

    assert seen["metadata"] == ordered[:3]
    assert seen["rest_seed"] == ordered[:3]
    assert result["held_quote_refresh_selected_tokens"] == 3
    assert result["held_quote_refresh_deferred_tokens"] == 7
    assert result["held_quote_refresh_attempted_tokens"] == 3
    assert result["budget_skipped_tokens"] == 0


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
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn: set())
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
    monkeypatch.setattr(lane, "_edli_held_position_priority_token_ids", lambda conn: {"held-token"})
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
    monkeypatch.setattr(config, "state_path", lambda _filename: target)
    lane._write_market_channel_continuity(
        {
            "schema_version": 1,
            "channel": "market_channel",
            "connected": True,
            "connected_at": "2026-07-17T03:00:00+00:00",
            "observed_at": "2026-07-17T03:00:00.500000+00:00",
            "active_token_count": 154,
        }
    )

    proof = json.loads(target.read_text(encoding="utf-8"))
    assert proof["connected"] is True
    assert proof["active_token_count"] == 154
    assert isinstance(proof["pid"], int) and proof["pid"] > 0
    assert not list(tmp_path.glob("*.tmp"))


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
    assert 'acquire_lock("market_substrate_refresh")' in lane_src
    assert "_edli_price_channel_trade_write_context_factory(" in lane_src
    assert "snapshot_write_context_factory=" in lane_src
    assert "price_channel_snapshot_invalidate" in lane_src
    assert "db_writer_lock(_zeus_trade_db_path(), WriteClass.LIVE)" not in lane_src
    assert "refresh_executable_market_substrate_snapshots(" in lane_src
