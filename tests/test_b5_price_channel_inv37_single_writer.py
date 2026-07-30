# Created: 2026-06-20
# Last audited: 2026-07-29
# Last reused/audited: 2026-07-29
# Authority basis: PR415 ChatGPT deep-review blocker B5 (INV-37). Quote projection
#   writes TRADE only; derived redecision and NEW_MARKET_DISCOVERED facts write WORLD
#   through independently coordinated lanes. TRADE quote refresh must never acquire
#   the WORLD writer lock.
"""B5 antibodies for price-channel DB ownership and writer-lane isolation."""
from __future__ import annotations

import ast
import contextlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRICE_CHANNEL_MODULE = _REPO_ROOT / "src" / "ingest" / "price_channel_ingest.py"
_MARKET_CHANNEL_MODULE = _REPO_ROOT / "src" / "events" / "triggers" / "market_channel_ingestor.py"

_REFRESH_FUNCS = (
    "_edli_refresh_held_position_quote_evidence",
    "_edli_refresh_candidate_priority_quote_evidence",
)


def _func_node(name: str) -> ast.FunctionDef:
    tree = ast.parse(_PRICE_CHANNEL_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found in price_channel_ingest.py")


def _live_conn_vars(fn: ast.FunctionDef, opener: str) -> set[str]:
    """Vars assigned a freshly-opened ``opener``(write_class='live') in fn (recursive)."""
    out: set[str] = set()
    for sub in ast.walk(fn):
        if (
            isinstance(sub, ast.Assign)
            and isinstance(sub.value, ast.Call)
            and isinstance(sub.value.func, ast.Name)
            and sub.value.func.id == opener
            and any(
                kw.arg == "write_class"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "live"
                for kw in sub.value.keywords
            )
        ):
            for tgt in sub.targets:
                if isinstance(tgt, ast.Name):
                    out.add(tgt.id)
    return out


def _write_gate_keyword_call_names(fn: ast.FunctionDef, call_attr: str) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(fn):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == call_attr
        ):
            for kw in sub.keywords:
                if (
                    kw.arg == "write_gate"
                    and isinstance(kw.value, ast.Call)
                    and isinstance(kw.value.func, ast.Name)
                ):
                    names.append(kw.value.func.id)
    return names


def test_no_function_opens_a_paired_world_and_trade_live_connection():
    """RED-ON-REVERT: the INV-37 violation is a function opening BOTH a live world
    connection AND a live trade connection (the logically-atomic cross-DB pair on two
    independent connections). A standalone single-DB trade write (e.g. snapshot
    invalidation) opening only a trade connection is NOT a violation.
    """
    tree = ast.parse(_PRICE_CHANNEL_MODULE.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        world_vars = _live_conn_vars(fn, "get_world_connection")
        trade_vars = _live_conn_vars(fn, "get_trade_connection")
        if world_vars and trade_vars and fn.name not in {
            "_edli_market_channel_ingestor_cycle",
            "_runner",
        }:
            offenders.append(
                f"{fn.name}: world={sorted(world_vars)} trade={sorted(trade_vars)}"
            )
    assert not offenders, (
        "INV-37 violation — a function opens a live world connection AND a live trade "
        f"connection (atomic cross-DB pair on two independent connections): {offenders}"
    )


def test_forever_runner_opens_independent_world_and_trade_lanes():
    node = _func_node("_edli_market_channel_ingestor_cycle")
    runner = next(
        sub
        for sub in ast.walk(node)
        if isinstance(sub, ast.FunctionDef) and sub.name == "_runner"
    )
    assert _live_conn_vars(runner, "get_world_connection") == {"world_conn"}
    assert "feasibility_conn" in _live_conn_vars(runner, "get_trade_connection")
    assert not _live_conn_vars(runner, "get_world_connection_with_trades_required")


@pytest.mark.parametrize("func_name", _REFRESH_FUNCS)
def test_refresh_uses_trade_only_write_connection(func_name):
    """Quote refresh owns TRADE evidence and must not open an attached WORLD writer."""
    node = _func_node(func_name)
    called = {
        sub.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
    }
    assert "get_trade_connection" in called
    assert "get_world_connection_with_trades_required" not in called
    assert "world_connection_with_trades_flocked" not in called, (
        f"{func_name} must not couple TRADE quote evidence to WORLD ownership."
    )
    expected_bound = (
        "_bound_held_quote_sqlite_wait"
        if func_name == "_edli_refresh_held_position_quote_evidence"
        else "_bound_price_channel_sqlite_wait"
    )
    assert expected_bound in called, (
        f"{func_name} must cap SQLite busy wait before entering the TRADE writer gate."
    )


@pytest.mark.parametrize("func_name", _REFRESH_FUNCS)
def test_refresh_feasibility_write_targets_trade_main_without_world_writer(func_name):
    node = _func_node(func_name)
    trade_main = any(
        kw.arg == "feasibility_schema"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value == ""
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        for kw in sub.keywords
    )
    quote_only = any(
        isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id == "MarketChannelIngestor"
        and sub.args
        and isinstance(sub.args[0], ast.Constant)
        and sub.args[0].value is None
        for sub in ast.walk(node)
    )
    assert trade_main
    assert quote_only


@pytest.mark.parametrize("func_name", _REFRESH_FUNCS)
def test_refresh_seed_chunks_use_trade_only_gate(func_name):
    node = _func_node(func_name)
    write_gate_calls = _write_gate_keyword_call_names(node, "seed_rest_books_in_chunks")
    assert write_gate_calls == ["_edli_price_channel_trade_write_gate"], (
        f"{func_name} must pass _edli_price_channel_trade_write_gate(...) as "
        f"seed_rest_books_in_chunks(write_gate=...), got {write_gate_calls!r}"
    )


def test_trade_gate_never_takes_world_mutex(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest.price_channel_ingest import _PriceChannelWriteGate
    from src.state import write_coordinator

    events: list[str] = []

    class _WorldMutex:
        def acquire(self, *, timeout):
            events.append("enter:world_mutex")
            return True

        def release(self):
            events.append("exit:world_mutex")

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **_kwargs):
            events.append("enter:coordinator")
            try:
                yield
            finally:
                events.append("exit:coordinator")

    monkeypatch.setattr(
        market_channel_ingestor,
        "_world_write_mutex",
        lambda: _WorldMutex(),
    )
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )

    with _PriceChannelWriteGate(owner="trade-lane-antibody", scope="trade"):
        events.append("body")

    assert events == [
        "enter:coordinator",
        "body",
        "exit:coordinator",
    ]

    events.clear()
    with _PriceChannelWriteGate(owner="world-lane-antibody", scope="world"):
        events.append("body")
    assert events == [
        "enter:world_mutex",
        "enter:coordinator",
        "body",
        "exit:coordinator",
        "exit:world_mutex",
    ]


def test_m5_and_fill_bridge_cycles_serialize_on_production_world_trade_gate(
    monkeypatch,
    tmp_path,
):
    from src.events import edli_trade_fact_bridge
    from src.events import price_channel_redecision_router
    from src.ingest import price_channel_ingest as lane
    from src.state import db as state_db
    from src.state import write_coordinator
    from src.state.db import init_schema, init_schema_trade_only
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    world_path = tmp_path / "world.db"
    trade_path = tmp_path / "trades.db"
    world_conn = sqlite3.connect(world_path)
    init_schema(world_conn)
    world_conn.close()
    trade_conn = sqlite3.connect(trade_path)
    init_schema_trade_only(trade_conn)
    trade_conn.close()

    telemetry = []
    coordinator = WriteCoordinator(
        {
            DBIdentity.WORLD: world_path,
            DBIdentity.TRADE: trade_path,
        },
        telemetry_sink=telemetry.append,
    )
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: coordinator,
    )
    monkeypatch.setattr(
        lane,
        "settings",
        {
            "edli": {
                "enabled": True,
                "edli_user_channel_reconcile_enabled": True,
                "edli_user_channel_message_queue_path": "",
                "edli_venue_reconcile_facts_path": "",
            }
        },
    )

    bridge_scan_started = Event()
    release_bridge_scan = Event()
    m5_gate_attempted = Event()
    m5_world_opened = Event()

    class _EmptyUserReader:
        def poll(self, *, max_messages):  # noqa: ARG002
            m5_gate_attempted.set()
            return []

    def _open_world(*args, **kwargs):  # noqa: ARG001
        if bridge_scan_started.is_set():
            m5_world_opened.set()
        conn = sqlite3.connect(world_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _open_trade_with_world(*args, **kwargs):  # noqa: ARG001
        conn = sqlite3.connect(trade_path)
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS world", (str(world_path),))
        return conn

    def _blocking_bridge_scan(
        conn,  # noqa: ARG001
        *,
        now=None,  # noqa: ARG001
        limit=1,  # noqa: ARG001
        failure_reasons=None,  # noqa: ARG001
    ):
        bridge_scan_started.set()
        assert release_bridge_scan.wait(timeout=1.0)
        return 0

    monkeypatch.setattr(lane, "_edli_user_channel_reader", lambda _cfg: _EmptyUserReader())
    monkeypatch.setattr(
        state_db,
        "get_world_connection_with_trades_required",
        _open_world,
    )
    monkeypatch.setattr(
        state_db,
        "get_trade_connection_with_world_required",
        _open_trade_with_world,
    )
    monkeypatch.setattr(
        edli_trade_fact_bridge,
        "append_confirmed_trade_facts_to_edli",
        lambda conn, *, now: 0,
    )
    monkeypatch.setattr(
        edli_trade_fact_bridge,
        "append_rest_filled_orphan_trade_facts_to_edli",
        lambda conn, *, now: 0,
    )
    monkeypatch.setattr(
        lane,
        "_edli_durable_fill_bridge_work_exists_read_only",
        lambda: True,
    )
    monkeypatch.setattr(lane, "_edli_durable_fill_bridge_scan", _blocking_bridge_scan)
    monkeypatch.setattr(
        price_channel_redecision_router,
        "_edli_position_fill_redecision_cycle",
        lambda: 0,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        repair_future = executor.submit(lane._edli_fill_bridge_repair_cycle)
        assert bridge_scan_started.wait(timeout=1.0)
        m5_future = executor.submit(lane._edli_user_channel_reconcile_cycle)
        assert m5_gate_attempted.wait(timeout=1.0)
        assert not m5_world_opened.wait(timeout=0.05)
        release_bridge_scan.set()
        repair_result = repair_future.result(timeout=1.0)
        m5_result = m5_future.result(timeout=1.0)

    assert repair_result["scheduler_failed"] is False
    assert m5_result["status"] == "m5_authority_proof_complete"
    bridge_leases = [
        item for item in telemetry if item.owner == "price_channel_fill_bridge"
    ]
    assert len(bridge_leases) == 1
    assert set(bridge_leases[0].db_set) == {"world", "trade"}


def test_world_gate_releases_mutex_when_coordinator_times_out(monkeypatch):
    from src.events.triggers import market_channel_ingestor
    from src.ingest.price_channel_ingest import _PriceChannelWriteGate
    from src.state import write_coordinator

    events: list[str] = []

    class _WorldMutex:
        def acquire(self, *, timeout):
            events.append("acquire:world")
            return True

        def release(self):
            events.append("release:world")

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **_kwargs):
            events.append("enter:coordinator")
            raise TimeoutError("world writer busy")
            yield

    monkeypatch.setattr(
        market_channel_ingestor,
        "_world_write_mutex",
        lambda: _WorldMutex(),
    )
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )

    with pytest.raises(TimeoutError, match="world writer busy"):
        with _PriceChannelWriteGate(owner="bounded-world", scope="world"):
            pytest.fail("timed-out gate must not enter its body")

    assert events == [
        "acquire:world",
        "enter:coordinator",
        "release:world",
    ]


def test_live_quote_gate_has_millisecond_contention_budget(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.state import write_coordinator

    leases: list[dict[str, int]] = []

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **kwargs):
            leases.append(kwargs)
            yield

    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )

    with lane._edli_price_channel_trade_write_gate(owner="quote-budget-antibody"):
        pass

    assert leases == [
        {
            "owner": "quote-budget-antibody",
            "write_class": "live",
            "deadline_ms": lane.PRICE_CHANNEL_QUOTE_DB_WRITE_LEASE_DEADLINE_MS,
            "max_hold_ms": lane.PRICE_CHANNEL_QUOTE_DB_WRITE_MAX_HOLD_MS,
        }
    ]
    assert leases[0]["deadline_ms"] <= 25
    assert leases[0]["max_hold_ms"] <= 100


def test_held_quote_gate_wait_is_clamped_by_refresh_deadline(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.state import write_coordinator

    leases: list[dict[str, int]] = []

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **kwargs):
            leases.append(kwargs)
            yield

    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )
    monkeypatch.setattr(lane.time, "monotonic", lambda: 100.0)

    with lane._edli_price_channel_trade_write_gate(
        owner="held-quote-budget-antibody",
        deadline_ms=lane.PRICE_CHANNEL_HELD_QUOTE_DB_WRITE_LEASE_DEADLINE_MS,
        deadline_monotonic=100.75,
    ):
        pass

    assert leases[0]["owner"] == "held-quote-budget-antibody"
    assert leases[0]["write_class"] == "live"
    assert 0 < leases[0]["deadline_ms"] <= 100
    assert leases[0]["max_hold_ms"] <= 100


def test_held_quote_sqlite_wait_is_clamped_by_hold_and_refresh_deadlines(
    monkeypatch,
):
    from src.ingest import price_channel_ingest as lane

    conn = sqlite3.connect(":memory:")
    try:
        monkeypatch.setattr(lane.time, "monotonic", lambda: 100.0)
        lane._bound_held_quote_sqlite_wait(
            conn,
            deadline_monotonic=101.0,
        )
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 25

        lane._bound_held_quote_sqlite_wait(
            conn,
            deadline_monotonic=100.075,
        )
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 25

        with pytest.raises(TimeoutError, match="deadline elapsed before DB write"):
            lane._bound_held_quote_sqlite_wait(
                conn,
                deadline_monotonic=99.0,
            )
    finally:
        conn.close()


def test_background_price_channel_gate_caps_lease_deadline_and_hold(monkeypatch):
    from src.ingest import price_channel_ingest as lane
    from src.state import write_coordinator

    leases: list[dict[str, int]] = []

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **kwargs):
            leases.append(kwargs)
            yield

    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )

    with lane._PriceChannelWriteGate(
        owner="fast-yield-bound-antibody",
        scope="trade",
        deadline_ms=2_000,
        max_hold_ms=2_000,
    ):
        pass

    assert leases[0]["deadline_ms"] == 100
    assert leases[0]["max_hold_ms"] == 100


def test_fill_bridge_processes_one_new_fill_per_tick():
    node = _func_node("_edli_durable_fill_bridge_scan")
    limit = next(arg for arg in node.args.kwonlyargs if arg.arg == "limit")
    index = node.args.kwonlyargs.index(limit)
    default = node.args.kw_defaults[index]
    assert isinstance(default, ast.Constant)
    assert default.value == 1

    repair = _func_node("_edli_fill_bridge_repair_cycle")
    calls = [
        call
        for call in ast.walk(repair)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_edli_durable_fill_bridge_scan"
    ]
    assert len(calls) == 1
    assert any(
        keyword.arg == "limit"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value == 1
        for keyword in calls[0].keywords
    )


def test_price_channel_configures_sixteen_quote_flush_batch():
    from src.events.triggers import market_channel_ingestor
    from src.ingest import price_channel_ingest as lane

    prior = market_channel_ingestor.MARKET_CHANNEL_QUOTE_FLUSH_BATCH_SIZE
    try:
        lane._configure_market_channel_quote_flush_batch()
        assert market_channel_ingestor.MARKET_CHANNEL_QUOTE_FLUSH_BATCH_SIZE == 16
    finally:
        market_channel_ingestor.MARKET_CHANNEL_QUOTE_FLUSH_BATCH_SIZE = prior


def test_held_quote_gate_never_enters_sql_after_absolute_deadline(
    monkeypatch,
):
    from src.ingest import price_channel_ingest as lane
    from src.state import write_coordinator

    clock = iter((100.0, 100.0, 101.0))
    monkeypatch.setattr(lane.time, "monotonic", lambda: next(clock))

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **_kwargs):
            yield

    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )
    conn = sqlite3.connect(":memory:")
    entered = False
    try:
        with pytest.raises(TimeoutError, match="deadline elapsed before DB write"):
            with lane._edli_price_channel_trade_write_gate(
                owner="held-absolute-deadline-antibody",
                deadline_ms=2000,
                deadline_monotonic=100.5,
                on_enter=lambda: lane._bound_held_quote_sqlite_wait(
                    conn,
                    deadline_monotonic=100.5,
                ),
            ):
                entered = True
        assert entered is False
    finally:
        conn.close()


def test_held_refresh_uses_fair_deadline_bounded_trade_gate():
    node = _func_node("_edli_refresh_held_position_quote_evidence")
    calls = [
        sub
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id == "_edli_price_channel_trade_write_gate"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    assert isinstance(keywords["deadline_ms"], ast.Name)
    assert (
        keywords["deadline_ms"].id
        == "PRICE_CHANNEL_HELD_QUOTE_DB_WRITE_LEASE_DEADLINE_MS"
    )
    assert isinstance(keywords["deadline_monotonic"], ast.Name)
    assert keywords["deadline_monotonic"].id == "deadline"
    assert isinstance(keywords["on_enter"], ast.Lambda)


def test_forever_ingestor_uses_owner_connections_not_attached_connection():
    node = _func_node("_edli_market_channel_ingestor_cycle")
    called = {
        sub.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
    }
    assert "get_world_connection" in called
    assert "get_trade_connection" in called
    assert "get_world_connection_with_trades_required" not in called
    assert "_bound_price_channel_sqlite_wait" in called, (
        "the forever price-channel connection must not hold all writer gates "
        "for the repo-wide SQLite busy timeout"
    )


def test_user_channel_reconcile_uses_world_main_with_trades_attached():
    """EDLI ledger writes must resolve to canonical world MAIN while authenticated
    command/trade facts resolve through the attached ``trades`` schema."""
    m5_node = _func_node("_edli_user_channel_reconcile_cycle")
    m5_openers = {
        target.id: sub.value.func.id
        for sub in ast.walk(m5_node)
        if isinstance(sub, ast.Assign)
        and isinstance(sub.value, ast.Call)
        and isinstance(sub.value.func, ast.Name)
        for target in sub.targets
        if isinstance(target, ast.Name)
    }
    assert m5_openers["conn"] == "get_world_connection_with_trades_required"

    repair_node = _func_node("_edli_fill_bridge_repair_cycle")
    repair_openers = {
        target.id: sub.value.func.id
        for sub in ast.walk(repair_node)
        if isinstance(sub, ast.Assign)
        and isinstance(sub.value, ast.Call)
        and isinstance(sub.value.func, ast.Name)
        for target in sub.targets
        if isinstance(target, ast.Name)
    }
    assert repair_openers["bridge_conn"] == "get_trade_connection_with_world_required"
    bridge_gate = next(
        sub
        for sub in ast.walk(repair_node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Name)
        and sub.func.id == "_PriceChannelWriteGate"
        and any(
            keyword.arg == "owner"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == "price_channel_fill_bridge"
            for keyword in sub.keywords
        )
    )
    scope = next(
        keyword.value
        for keyword in bridge_gate.keywords
        if keyword.arg == "scope"
    )
    assert isinstance(scope, ast.Constant)
    assert scope.value == "world_trade"


def test_forever_ingestor_passes_independent_trade_and_world_gates():
    node = _func_node("_edli_market_channel_ingestor_cycle")
    gate_calls: dict[str, str] = {}
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            if sub.func.id != "run_market_channel_service_forever":
                continue
            for kw in sub.keywords:
                if (
                    kw.arg in {"quote_write_gate", "world_event_write_gate"}
                    and isinstance(kw.value, ast.Call)
                    and isinstance(kw.value.func, ast.Name)
                ):
                    gate_calls[str(kw.arg)] = kw.value.func.id
    assert gate_calls == {
        "quote_write_gate": "_edli_price_channel_trade_write_gate",
        "world_event_write_gate": "_edli_price_channel_world_write_gate",
    }


@pytest.mark.parametrize(
    ("func_name", "mutex_name"),
        (
            ("seed_rest_books_in_chunks", "write_gate"),
            ("reconnect_rest_books_in_chunks", "write_gate"),
        ),
)
def test_deferred_redecision_sink_supports_atomic_and_independent_flush(
    func_name: str,
    mutex_name: str,
):
    """Default sinks stay atomic; independently coordinated sinks run post-commit."""

    tree = ast.parse(_MARKET_CHANNEL_MODULE.read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == func_name
    )
    all_flushes = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "flush_deferred_market_event_sink"
    ]
    gates = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Name)
            and item.context_expr.id == mutex_name
            for item in node.items
        )
    ]

    assert len(all_flushes) == 2
    assert len(gates) == 1
    flushes_in_gate = [node for node in ast.walk(gates[0]) if node in all_flushes]
    commits_in_gate = [
        node
        for node in ast.walk(gates[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "commit"
    ]
    assert len(flushes_in_gate) == 1
    assert len(commits_in_gate) == 1
    assert flushes_in_gate[0].lineno < commits_in_gate[0].lineno
    flushes_after_gate = [node for node in all_flushes if node not in flushes_in_gate]
    assert len(flushes_after_gate) == 1
    assert flushes_after_gate[0].lineno > gates[0].end_lineno


def test_websocket_quote_and_world_sinks_flush_in_their_own_write_gates():
    tree = ast.parse(_MARKET_CHANNEL_MODULE.read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run_websocket_forever"
    )
    for gate_name in ("_quote_write_gate", "_world_event_write_gate"):
        gates = [
            node
            for node in ast.walk(fn)
            if isinstance(node, ast.With)
            and any(
                isinstance(item.context_expr, ast.Name)
                and item.context_expr.id == gate_name
                for item in node.items
            )
        ]
        assert any(
            sum(
                1
                for node in ast.walk(gate)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "flush_deferred_market_event_sink"
            )
            == 1
            for gate in gates
        )


@pytest.mark.parametrize(
    "func_name",
    (
        "_edli_refresh_held_position_quote_evidence",
        "_edli_refresh_candidate_priority_quote_evidence",
        "_edli_market_channel_ingestor_cycle",
    ),
)
def test_live_price_redecision_sink_is_independently_coordinated(func_name: str):
    node = _func_node(func_name)
    values = [
        kw.value.value
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "MarketChannelIngestor"
        for kw in call.keywords
        if kw.arg == "market_event_sink_independently_coordinated"
        and isinstance(kw.value, ast.Constant)
    ]
    assert values == [True]


def test_world_connection_with_trades_flocked_attaches_trades_world_main():
    """The new helper yields a world-MAIN connection with zeus_trades.db ATTACHed as
    'trades' (so opportunity_events->world MAIN, trades.execution_feasibility_evidence
    reachable). Behavioral: open it and inspect PRAGMA database_list."""
    from src.state.db import world_connection_with_trades_flocked

    with world_connection_with_trades_flocked(write_class="live") as conn:
        rows = conn.execute("PRAGMA database_list").fetchall()
        schemas = {r[1]: r[2] for r in rows}  # name -> file
        assert "main" in schemas and schemas["main"].endswith("zeus-world.db"), (
            f"MAIN must be zeus-world.db, got {schemas.get('main')!r}"
        )
        assert "trades" in schemas and schemas["trades"].endswith("zeus_trades.db"), (
            f"'trades' must be ATTACHed as zeus_trades.db, got {schemas.get('trades')!r}"
        )


def test_get_world_connection_with_trades_required_attaches_trades_world_main():
    """The non-flocked sibling (for the forever loop) yields the same world-MAIN +
    trades-ATTACHed shape."""
    from src.state.db import get_world_connection_with_trades_required

    conn = get_world_connection_with_trades_required(write_class="live")
    try:
        schemas = {r[1]: r[2] for r in conn.execute("PRAGMA database_list").fetchall()}
        assert schemas.get("main", "").endswith("zeus-world.db")
        assert "trades" in schemas and schemas["trades"].endswith("zeus_trades.db")
    finally:
        conn.close()


def test_insert_feasibility_schema_qualifier_targets_attached_schema():
    """RED-ON-REVERT (the qualifier wiring): insert_execution_feasibility_evidence with
    schema='trades' writes to the ATTACHed trades schema, NOT MAIN. Build a two-DB
    in-memory connection where BOTH schemas have the table (mirroring the production
    shadow-table hazard) and confirm the qualified write lands in 'trades' only."""
    from src.events.triggers.market_channel_ingestor import (
        insert_execution_feasibility_evidence,
    )

    ddl = """
        CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY, event_id TEXT, condition_id TEXT, token_id TEXT,
            outcome_label TEXT, direction TEXT, quote_seen_at TEXT, book_hash_before TEXT,
            best_bid_before REAL, best_ask_before REAL, depth_before_json TEXT,
            order_intent_time TEXT, submit_time TEXT, accepted_or_rejected TEXT,
            venue_order_id TEXT, fok_full_fill INTEGER, fak_partial_fill INTEGER,
            filled_shares REAL, fill_price REAL, cancel_remainder_status TEXT,
            book_hash_after TEXT, latency_ms REAL, maker_cancel_before_submit INTEGER,
            would_have_edge_after_fee INTEGER, created_at TEXT, schema_version INTEGER
        )
    """
    conn = sqlite3.connect(":memory:")  # MAIN = the "world" stand-in (has a shadow copy)
    conn.execute(ddl)
    conn.execute("ATTACH DATABASE ':memory:' AS trades")
    conn.execute(ddl.replace("CREATE TABLE", "CREATE TABLE trades."))

    row = {
        "event_id": "evt-1", "condition_id": "c1", "token_id": "t1",
        "outcome_label": "NO", "direction": "buy_no", "quote_seen_at": "2026-06-20T00:00:00Z",
        "book_hash_before": "h", "best_bid_before": 0.4, "best_ask_before": 0.42,
        "depth_before_json": "{}", "order_intent_time": None, "submit_time": None,
        "accepted_or_rejected": None, "venue_order_id": None, "fok_full_fill": None,
        "fak_partial_fill": None, "filled_shares": None, "fill_price": None,
        "cancel_remainder_status": None, "book_hash_after": None, "latency_ms": None,
        "maker_cancel_before_submit": None, "would_have_edge_after_fee": None,
        "fill_truth_source": "",
    }
    insert_execution_feasibility_evidence(conn, dict(row), schema="trades")

    main_n = conn.execute("SELECT COUNT(*) FROM main.execution_feasibility_evidence").fetchone()[0]
    trades_n = conn.execute("SELECT COUNT(*) FROM trades.execution_feasibility_evidence").fetchone()[0]
    assert trades_n == 1, "schema='trades' must write to the ATTACHed trades schema"
    assert main_n == 0, "schema='trades' must NOT write to MAIN (the world shadow)"


def test_insert_feasibility_default_unqualified_writes_main():
    """Backward-compat: schema='' (default) writes to MAIN unqualified (every other
    caller's behavior is preserved)."""
    from src.events.triggers.market_channel_ingestor import (
        insert_execution_feasibility_evidence,
    )

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE execution_feasibility_evidence (
            evidence_id TEXT PRIMARY KEY, event_id TEXT, condition_id TEXT, token_id TEXT,
            outcome_label TEXT, direction TEXT, quote_seen_at TEXT, book_hash_before TEXT,
            best_bid_before REAL, best_ask_before REAL, depth_before_json TEXT,
            order_intent_time TEXT, submit_time TEXT, accepted_or_rejected TEXT,
            venue_order_id TEXT, fok_full_fill INTEGER, fak_partial_fill INTEGER,
            filled_shares REAL, fill_price REAL, cancel_remainder_status TEXT,
            book_hash_after TEXT, latency_ms REAL, maker_cancel_before_submit INTEGER,
            would_have_edge_after_fee INTEGER, created_at TEXT, schema_version INTEGER
        )"""
    )
    row = {
        "event_id": "evt-1", "condition_id": "c1", "token_id": "t1",
        "outcome_label": "NO", "direction": "buy_no", "quote_seen_at": "2026-06-20T00:00:00Z",
        "book_hash_before": "h", "best_bid_before": 0.4, "best_ask_before": 0.42,
        "depth_before_json": "{}", "order_intent_time": None, "submit_time": None,
        "accepted_or_rejected": None, "venue_order_id": None, "fok_full_fill": None,
        "fak_partial_fill": None, "filled_shares": None, "fill_price": None,
        "cancel_remainder_status": None, "book_hash_after": None, "latency_ms": None,
        "maker_cancel_before_submit": None, "would_have_edge_after_fee": None,
        "fill_truth_source": "",
    }
    insert_execution_feasibility_evidence(conn, row)  # schema="" default
    assert conn.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0] == 1


def test_insert_feasibility_rejects_unknown_schema():
    """The schema qualifier is allowlisted (no SQL injection via a caller string)."""
    from src.events.triggers.market_channel_ingestor import (
        insert_execution_feasibility_evidence,
    )

    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        insert_execution_feasibility_evidence(
            conn, {"fill_truth_source": ""}, schema="trades; DROP TABLE x"
        )
