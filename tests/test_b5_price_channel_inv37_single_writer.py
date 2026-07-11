# Created: 2026-06-20
# Last audited: 2026-07-11
# Last reused/audited: 2026-07-11
# Authority basis: PR415 ChatGPT deep-review blocker B5 (INV-37). The held- and
#   candidate-priority quote-evidence ingest (and the forever market-channel loop)
#   must write the world event (opportunity_events) AND the trade-owned book witness
#   (execution_feasibility_evidence) through ONE attached connection + a single commit
#   (world.db MAIN + zeus_trades.db ATTACHed as 'trades', schema-qualified feasibility
#   write), NEVER two independent connections committed separately.
"""B5 antibody: price-channel quote-evidence ingest is a single-writer cross-DB path.

RED-on-revert: the prior shape opened
    world_conn = get_world_connection(write_class="live")
    feasibility_conn = get_trade_connection(write_class="live")
and committed them SEPARATELY in `_commit_event_and_feasibility(): world_conn.commit();
feasibility_conn.commit()`. These static + behavioral guards FAIL on that shape and
PASS only when both writes go through one ATTACHed connection with a single commit and
a schema-qualified feasibility insert.
"""
from __future__ import annotations

import ast
import contextlib
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRICE_CHANNEL_MODULE = _REPO_ROOT / "src" / "ingest" / "price_channel_ingest.py"

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


def _world_mutex_keyword_call_names(fn: ast.FunctionDef, call_attr: str) -> list[str]:
    names: list[str] = []
    for sub in ast.walk(fn):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == call_attr
        ):
            for kw in sub.keywords:
                if (
                    kw.arg == "world_mutex"
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
        if world_vars and trade_vars:
            offenders.append(
                f"{fn.name}: world={sorted(world_vars)} trade={sorted(trade_vars)}"
            )
    assert not offenders, (
        "INV-37 violation — a function opens a live world connection AND a live trade "
        f"connection (atomic cross-DB pair on two independent connections): {offenders}"
    )


@pytest.mark.parametrize("func_name", _REFRESH_FUNCS)
def test_refresh_uses_non_flocked_world_connection_with_trades(func_name):
    """Bounded REST refresh functions must use one ATTACHed connection without
    holding cross-process writer flocks across orderbook fetches."""
    node = _func_node(func_name)
    called = {
        sub.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
    }
    assert "get_world_connection_with_trades_required" in called, (
        f"{func_name} must use get_world_connection_with_trades_required: one "
        f"world-main connection with zeus_trades.db ATTACHed."
    )
    assert "world_connection_with_trades_flocked" not in called, (
        f"{func_name} must not use world_connection_with_trades_flocked: "
        f"seed_rest_books_in_chunks performs REST fetches before each DB write "
        f"chunk, and holding writer flocks across that network window starves "
        f"live redecision snapshot refresh."
    )


@pytest.mark.parametrize("func_name", _REFRESH_FUNCS)
def test_refresh_feasibility_write_is_schema_qualified_trades(func_name):
    """The feasibility write must be schema-qualified to the attached 'trades' schema
    (so it never lands in the world shadow table)."""
    node = _func_node(func_name)
    qualifies_trades = any(
        kw.arg == "feasibility_schema"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value == "trades"
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call)
        for kw in sub.keywords
    )
    assert qualifies_trades, (
        f"{func_name} must pass feasibility_schema='trades' so the feasibility insert "
        f"targets trades.execution_feasibility_evidence (not the world shadow table)."
    )


@pytest.mark.parametrize("func_name", _REFRESH_FUNCS)
def test_refresh_seed_chunks_use_unified_world_trade_gate(func_name):
    """The DB write chunk must use the composed world+trade gate, not the bare
    world mutex by itself."""
    node = _func_node(func_name)
    world_mutex_calls = _world_mutex_keyword_call_names(node, "seed_rest_books_in_chunks")
    assert world_mutex_calls == ["_edli_price_channel_world_trade_write_gate"], (
        f"{func_name} must pass _edli_price_channel_world_trade_write_gate(...) as "
        f"seed_rest_books_in_chunks(world_mutex=...), got {world_mutex_calls!r}"
    )


def test_unified_gate_takes_world_mutex_before_coordinator(monkeypatch):
    """Money-path and price-channel writers must share one global lock order.

    Taking coordinator WORLD+TRADE before the world mutex deadlocks against the
    entry/exit path, which already holds the world mutex when it reaches a
    trade writer gate. ExitStack unwinds the reverse acquisition order.
    """
    from src.events.triggers import market_channel_ingestor
    from src.ingest.price_channel_ingest import _PriceChannelWorldTradeWriteGate
    from src.state import write_coordinator

    events: list[str] = []

    @contextlib.contextmanager
    def _world_mutex():
        events.append("enter:world_mutex")
        try:
            yield
        finally:
            events.append("exit:world_mutex")

    class _Coordinator:
        @contextlib.contextmanager
        def lease(self, *_args, **_kwargs):
            events.append("enter:coordinator")
            try:
                yield
            finally:
                events.append("exit:coordinator")

    monkeypatch.setattr(market_channel_ingestor, "_world_write_mutex", _world_mutex)
    monkeypatch.setattr(
        write_coordinator,
        "default_runtime_write_coordinator",
        lambda: _Coordinator(),
    )

    with _PriceChannelWorldTradeWriteGate(owner="lock-order-antibody"):
        events.append("body")

    assert events == [
        "enter:world_mutex",
        "enter:coordinator",
        "body",
        "exit:coordinator",
        "exit:world_mutex",
    ]


def test_forever_ingestor_uses_single_attached_connection():
    """The long-lived market-channel ingestor must use the single-connection ATTACH
    helper (non-flocked, to avoid forever-holding cross-DB flocks), not two
    independent connections."""
    node = _func_node("_edli_market_channel_ingestor_cycle")
    called = {
        sub.func.id
        for sub in ast.walk(node)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
    }
    assert "get_world_connection_with_trades_required" in called, (
        "_edli_market_channel_ingestor_cycle must use the single-connection ATTACH "
        "helper get_world_connection_with_trades_required (INV-37)."
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


def test_forever_ingestor_passes_unified_world_trade_gate():
    """The websocket forever loop must also use the unified world+trade gate for its
    per-message write+commit units."""
    node = _func_node("_edli_market_channel_ingestor_cycle")
    world_mutex_calls: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            if sub.func.id != "run_market_channel_service_forever":
                continue
            for kw in sub.keywords:
                if (
                    kw.arg == "world_mutex"
                    and isinstance(kw.value, ast.Call)
                    and isinstance(kw.value.func, ast.Name)
                ):
                    world_mutex_calls.append(kw.value.func.id)
    assert world_mutex_calls == ["_edli_price_channel_world_trade_write_gate"]


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
