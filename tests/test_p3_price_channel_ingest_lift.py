# Created: 2026-06-08
# Last reused or audited: 2026-06-08 (R2 fix: caller-side no-regression invariants —
#   the original lift left 3 test modules bound to src.main for the lifted producers)
# Authority basis: docs/architecture/system_decomposition_plan.md
#   §4.2 (Price-Channel / CLOB-Fact Ingest), §6 (P3 row + co-location decision),
#   §7 (I2 no-back-coupling: durable fill bridge + execution_feasibility_evidence),
#   §8 Step 3 (lift the user-channel WS thread + market-channel + reconcile cycles),
#   §9 (regression-unconstructable proof — failure-domain isolation).
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=never
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
import inspect
import sqlite3
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
    of where the WS producer lives. The retained reader is
    src.main._edli_latest_pre_submit_book_row (the pre-submit book witness).
    """
    import src.main as main_mod

    assert hasattr(main_mod, "_edli_latest_pre_submit_book_row"), (
        "the order runtime must keep its pre-submit feasibility reader "
        "(_edli_latest_pre_submit_book_row) — the DB-mediated I2 read side P1 keeps."
    )
    reader_src = inspect.getsource(main_mod._edli_latest_pre_submit_book_row)
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
            no_token_id TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO position_current VALUES (?,?,?,?)",
        [
            ("active-1", "active", "yes-active", "no-active"),
            ("day0-1", "day0_window", None, "no-day0"),
            ("exit-1", "pending_exit", "yes-exit", None),
            ("closed-1", "economically_closed", "yes-closed", "no-closed"),
        ],
    )

    assert _edli_held_position_priority_token_ids(conn) == {
        "yes-active",
        "no-active",
        "no-day0",
        "yes-exit",
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


def test_held_position_quote_refresh_writes_feasibility_rows(monkeypatch, tmp_path):
    from src.data import polymarket_client
    from src.ingest.price_channel_ingest import _edli_refresh_held_position_quote_evidence
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

        def get_orderbook_snapshot(self, token_id: str) -> dict:
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

    result = _edli_refresh_held_position_quote_evidence()

    assert result["held_priority_token_ids"] == 2
    assert result["held_token_metadata"] == 2
    assert result["held_quote_refresh_events"] == 2
    check = sqlite3.connect(trade_path)
    try:
        assert (
            check.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
            == 4
        )
    finally:
        check.close()


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

        def get_orderbook_snapshot(self, token_id: str) -> dict:
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
            check.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
            == 2
        )
    finally:
        check.close()


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

        def get_orderbook_snapshot(self, token_id: str) -> dict:
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
            check.execute("SELECT COUNT(*) FROM execution_feasibility_evidence").fetchone()[0]
            == 2
        )
    finally:
        check.close()


def test_candidate_priority_quote_refresh_fetches_new_missing_book_gap_first(monkeypatch, tmp_path):
    from src.data import polymarket_client
    from src.ingest.price_channel_ingest import _edli_refresh_candidate_priority_quote_evidence
    from src.state import db as state_db
    from src.state.db import init_schema, init_schema_trade_only

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
            'BOOK_GAP', ?, '2026-06-25T16:00:00+00:00',
            'Wellington', '2026-06-27', 'high', 'family-wellington-high',
            'Will the highest temperature in Wellington be 12C?', 'buy_no',
            ?, 1
        )
        """,
        [
            ("regret-new", "event-new", "zz-new-token", "2026-06-25T16:10:00+00:00"),
            ("regret-old", "event-old", "aa-old-token", "2026-06-25T16:00:00+00:00"),
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
            ?, ?, 1, 1, 0, '2026-06-27T12:00:00+00:00', '0.01', '5',
            '{}', '{}', 0, '0.40', '0.60', '{}', 'gh', 'ch', 'oh',
            'CLOB', '2026-06-25T16:00:00+00:00',
            '2026-06-25T16:05:00+00:00'
        )
        """,
        [
            ("snap-new", "gamma-new", "event-new", "0xnew", "question-new", "yes-new", "zz-new-token"),
            ("snap-old", "gamma-old", "event-old", "0xold", "question-old", "yes-old", "aa-old-token"),
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

        def get_orderbook_snapshot(self, token_id: str) -> dict:
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
