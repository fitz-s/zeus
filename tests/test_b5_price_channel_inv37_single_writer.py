# Created: 2026-06-20
# Last audited: 2026-07-18
# Last reused/audited: 2026-07-18
# Authority basis: PR415 ChatGPT deep-review blocker B5 (INV-37). Quote projection
#   writes TRADE only; derived redecision and NEW_MARKET_DISCOVERED facts write WORLD
#   through independently coordinated lanes. TRADE quote refresh must never acquire
#   the WORLD writer lock.
"""B5 antibodies for price-channel DB ownership and writer-lane isolation."""
from __future__ import annotations

import ast
import contextlib
import sqlite3
from pathlib import Path

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
    assert "_bound_price_channel_sqlite_wait" in called, (
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
    node = _func_node("_edli_user_channel_reconcile_cycle")
    assigned_openers = {
        target.id: sub.value.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Assign)
        and isinstance(sub.value, ast.Call)
        and isinstance(sub.value.func, ast.Name)
        for target in sub.targets
        if isinstance(target, ast.Name)
    }
    assert assigned_openers["conn"] == "get_world_connection_with_trades_required"
    assert assigned_openers["bridge_conn"] == "get_trade_connection_with_world_required"


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
