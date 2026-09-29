# Created: 2026-09-29
# Last audited: 2026-09-29
# Authority basis: docs/operations/current/plans/edge_program_2026-09-25.md goal 4
#   (retention by reachability, one rule for every store; nothing a reader needs is lost).
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data import family_reachability as reach_mod
from src.data import forecast_retention as fr
from src.data import trade_retention as tr

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
OLD = "2026-08-01T00:00:00+00:00"  # older than the 30-day reader window
FRESH = "2026-09-20T00:00:00+00:00"  # inside the reader window

EMS_DDL = f"""
CREATE TABLE executable_market_snapshots (
  snapshot_id TEXT PRIMARY KEY, condition_id TEXT NOT NULL, captured_at TEXT NOT NULL,
  orderbook_depth_json TEXT NOT NULL DEFAULT '', fee_details_json TEXT NOT NULL DEFAULT '',
  token_map_json TEXT NOT NULL DEFAULT '', tradeability_status_json TEXT NOT NULL DEFAULT '{{}}'
);
CREATE INDEX idx_snapshots_condition_captured ON executable_market_snapshots (condition_id, captured_at DESC);
CREATE TRIGGER {tr.DELETE_TRIGGER} BEFORE DELETE ON executable_market_snapshots
BEGIN SELECT RAISE(ABORT, 'executable_market_snapshots is APPEND-ONLY (NC-NEW-B)'); END;
CREATE TABLE executable_market_snapshot_latest (condition_id TEXT, snapshot_id TEXT);
CREATE TABLE position_current (position_id TEXT PRIMARY KEY, phase TEXT, city TEXT,
  target_date TEXT, temperature_metric TEXT);
CREATE TABLE venue_commands (command_id TEXT, position_id TEXT, venue_order_id TEXT,
  token_id TEXT, snapshot_id TEXT, state TEXT, intent_kind TEXT);
CREATE TABLE venue_order_facts (venue_order_id TEXT, state TEXT, remaining_size REAL,
  local_sequence INTEGER);
CREATE TABLE position_events (event_id TEXT PRIMARY KEY, snapshot_id TEXT);
CREATE TABLE market_price_history (id INTEGER PRIMARY KEY, snapshot_id TEXT);
CREATE TABLE opportunity_fact (id INTEGER PRIMARY KEY, snapshot_id TEXT);
"""
WORLD_DDL = """
CREATE TABLE no_trade_regret_events (regret_event_id TEXT PRIMARY KEY,
  causal_snapshot_id TEXT, executable_snapshot_id TEXT);
CREATE TABLE edli_no_submit_receipts (receipt_id TEXT PRIMARY KEY,
  causal_snapshot_id TEXT, executable_snapshot_id TEXT);
CREATE TABLE decision_certificates (certificate_id TEXT PRIMARY KEY,
  certificate_type TEXT, payload_json TEXT);
"""
FORECAST_DDL = """
CREATE TABLE market_events (condition_id TEXT, city TEXT, target_date TEXT,
  temperature_metric TEXT);
"""
# condition -> family. c-old is unreachable (target long past); c-cur is current.
FAMILIES = {
    "c-old": ("Paris", "2026-08-02", "high"),
    "c-cur": ("Paris", "2026-09-29", "high"),
    "c-held": ("Hong Kong", "2026-08-02", "low"),
}


@pytest.fixture
def env(tmp_path: Path):
    trade, world, forecast = (tmp_path / n for n in ("trade.db", "world.db", "forecast.db"))
    for path, ddl in ((trade, EMS_DDL), (world, WORLD_DDL), (forecast, FORECAST_DDL)):
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(ddl)
        conn.commit()
        conn.close()
    conn = sqlite3.connect(forecast)
    conn.executemany("INSERT INTO market_events VALUES (?,?,?,?)",
                     [(c, *f) for c, f in FAMILIES.items()])
    conn.commit()
    conn.close()
    return tmp_path, trade, world, forecast


def _snap(trade: Path, sid: str, cond: str, captured_at: str, depth: str = "x" * 2000) -> None:
    conn = sqlite3.connect(trade)
    conn.execute(
        "INSERT INTO executable_market_snapshots (snapshot_id, condition_id, captured_at,"
        " orderbook_depth_json) VALUES (?,?,?,?)",
        (sid, cond, captured_at, depth),
    )
    conn.commit()
    conn.close()


def _exec(path: Path, sql: str, *params) -> None:
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _ids(trade: Path) -> set[str]:
    conn = sqlite3.connect(trade)
    try:
        return {r[0] for r in conn.execute("SELECT snapshot_id FROM executable_market_snapshots")}
    finally:
        conn.close()


def _plain_transaction(trade: Path):
    @contextlib.contextmanager
    def transaction():
        conn = sqlite3.connect(trade, isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    return transaction


def _run(env, *, apply: bool = True, **kwargs):
    state, trade, world, forecast = env
    kwargs.setdefault("pause_seconds", 0)
    return tr.run_trade_retention(
        apply=apply, now=NOW, state_dir=state, trade_db=trade, world_db=world,
        forecast_db=forecast, transaction=_plain_transaction(trade), **kwargs,
    )


def _seed_old(trade: Path, n: int, cond: str = "c-old") -> None:
    for i in range(n):
        _snap(trade, f"{cond}-{i}", cond, OLD)
    # A newer row of the same condition so none of the above is newest-per-condition.
    _snap(trade, f"{cond}-newest", cond, "2026-08-10T00:00:00+00:00")


def test_shared_law_is_the_forecast_law():
    # One predicate: forecast retention imports it, never redefines it.
    assert fr.Reachability is reach_mod.Reachability
    assert fr.build_reachability is reach_mod.build_reachability
    assert tr.build_reachability is reach_mod.build_reachability


def test_unreachable_evicted_reachable_and_window_kept(env):
    _, trade, *_ = env
    _seed_old(trade, 3)
    _snap(trade, "cur-old-capture", "c-cur", OLD)  # old capture, reachable family
    _snap(trade, "fresh", "c-held", FRESH)  # inside the reader window
    out = _run(env)
    assert out["status"] == "APPLIED", out
    assert _ids(trade) == {"c-old-newest", "cur-old-capture", "fresh"}
    report = out["report"]
    assert report["evicted"] == 3 and report["kept_reachable"] == 1
    assert report["kept_newest"] == 1 and report["stopped"] == "reader_window"


def test_a_newer_row_releases_the_previous_newest(env):
    _, trade, *_ = env
    _seed_old(trade, 1)
    _snap(trade, "c-old-later", "c-old", FRESH)
    _run(env)
    assert _ids(trade) == {"c-old-later"}


def test_open_position_keeps_an_old_family(env):
    _, trade, *_ = env
    _seed_old(trade, 2, cond="c-held")
    _exec(trade, "INSERT INTO position_current VALUES ('p1','active','Hong Kong','2026-08-02','low')")
    _run(env)
    assert _ids(trade) == {"c-held-0", "c-held-1", "c-held-newest"}


@pytest.mark.parametrize(
    "db,sql",
    [
        ("trade", "INSERT INTO venue_commands (command_id, snapshot_id) VALUES ('cmd','c-old-1')"),
        ("trade", "INSERT INTO position_events VALUES ('e1','c-old-1')"),
        ("trade", "INSERT INTO market_price_history (snapshot_id) VALUES ('c-old-1')"),
        ("trade", "INSERT INTO opportunity_fact (snapshot_id) VALUES ('c-old-1')"),
        ("trade", "INSERT INTO executable_market_snapshot_latest VALUES ('c-old','c-old-1')"),
        ("world", "INSERT INTO no_trade_regret_events VALUES ('r1', NULL, 'c-old-1')"),
        ("world", "INSERT INTO edli_no_submit_receipts VALUES ('n1', 'c-old-1', NULL)"),
        ("world", "INSERT INTO decision_certificates VALUES ('d1','ActionableTradeCertificate',"
                  " '{\"qkernel_execution_economics\":{\"raw_calibration_input\":"
                  "{\"book_snapshot_id\":\"c-old-1\"}}}')"),
    ],
)
def test_by_id_referrers_keep_their_rows(env, db, sql):
    state, trade, world, _ = env
    _seed_old(trade, 3)
    _exec(trade if db == "trade" else world, sql)
    _run(env)
    assert _ids(trade) == {"c-old-1", "c-old-newest"}


def test_referrer_added_after_ledger_is_seen_next_pass(env):
    state, trade, world, _ = env
    _seed_old(trade, 1)
    _run(env, row_budget=0)  # ledger reads everything, evicts nothing
    _exec(world, "INSERT INTO no_trade_regret_events VALUES ('r1', NULL, 'c-old-0')")
    _run(env)
    assert "c-old-0" in _ids(trade)


def test_unknown_reachability_evicts_nothing(env):
    _, trade, *_ = env
    _seed_old(trade, 3)
    _exec(trade, "INSERT INTO position_current VALUES ('p9','active',NULL,'2026-08-02','low')")
    out = _run(env)
    assert out["status"] == "REACHABILITY_UNAVAILABLE"
    assert len(_ids(trade)) == 4


def test_condition_without_a_family_is_kept(env):
    _, trade, *_ = env
    _snap(trade, "orphan-0", "c-unknown", OLD)
    _snap(trade, "orphan-1", "c-unknown", "2026-08-10T00:00:00+00:00")
    out = _run(env)
    assert _ids(trade) == {"orphan-0", "orphan-1"}
    assert out["report"]["unclassified"] == 2


def test_lagging_ledger_evicts_nothing(env):
    state, trade, world, _ = env
    _seed_old(trade, 3)
    for i in range(5):
        _exec(world, "INSERT INTO no_trade_regret_events VALUES (?, NULL, NULL)", f"r{i}")
    out = _run(env, referrer_budget=2, read_batch=2)
    assert out["status"] == "LEDGER_CATCHING_UP"
    assert len(_ids(trade)) == 4
    # The cursor persisted; enough passes catch up and then evict.
    for _ in range(3):
        out = _run(env, referrer_budget=2, read_batch=2)
    assert out["status"] == "APPLIED" and len(_ids(trade)) == 1


def test_dry_run_changes_nothing_and_persists_nothing(env):
    state, trade, *_ = env
    _seed_old(trade, 3)
    out = _run(env, apply=False)
    assert out["status"] == "DRY_RUN" and out["report"]["evicted"] == 3
    assert out["report"]["bytes"] >= 3 * 2000
    assert len(_ids(trade)) == 4
    assert not (state / tr.STATE_FILE).exists()


def test_work_is_bounded_and_resumes_from_cursor(env):
    state, trade, *_ = env
    _seed_old(trade, 10)
    out = _run(env, row_budget=4, read_batch=2)
    assert out["report"]["scanned"] == 4 and out["report"]["stopped"] == "row_budget"
    assert len(_ids(trade)) == 11 - 4
    cursor = json.loads((state / tr.STATE_FILE).read_text())["ems_cursor"]
    assert cursor == 4
    _run(env)
    assert _ids(trade) == {"c-old-newest"}


def test_chunks_are_bounded(env):
    _, trade, *_ = env
    _seed_old(trade, 5)
    out = _run(env, chunk_rows=2)
    assert out["report"]["chunks"] == 3 and out["report"]["evicted"] == 5


def test_wal_limit_stops_before_any_write(env, monkeypatch):
    state, trade, *_ = env
    _seed_old(trade, 3)
    monkeypatch.setattr(tr, "_wal_bytes", lambda _p: 10**12)
    out = _run(env)
    assert out["report"]["stopped"] == "wal_limit" and len(_ids(trade)) == 4
    # The cursor stays before the unhandled rows, so they are retried.
    assert json.loads((state / tr.STATE_FILE).read_text())["ems_cursor"] == 0


def test_append_only_trigger_survives_every_chunk(env):
    _, trade, *_ = env
    _seed_old(trade, 3)
    _run(env, chunk_rows=1)
    conn = sqlite3.connect(trade)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="APPEND-ONLY"):
            conn.execute("DELETE FROM executable_market_snapshots")
    finally:
        conn.close()


def test_command_citing_a_row_mid_pass_keeps_it(env):
    _, trade, *_ = env
    _seed_old(trade, 2)
    real = tr.delete_chunk

    def cite_then_delete(conn, ids):
        conn.execute("INSERT INTO venue_commands (command_id, snapshot_id) VALUES ('late','c-old-0')")
        return real(conn, ids)

    tr_delete = pytest.MonkeyPatch()
    tr_delete.setattr(tr, "delete_chunk", cite_then_delete)
    try:
        _run(env)
    finally:
        tr_delete.undo()
    assert "c-old-0" in _ids(trade)


def test_idempotent(env):
    _, trade, *_ = env
    _seed_old(trade, 3)
    _run(env)
    before = _ids(trade)
    out = _run(env)
    assert _ids(trade) == before and out["report"]["evicted"] == 0


def test_post_trade_daemon_registers_the_job():
    from scripts.data_collection_inventory import _scheduled_ids_in
    from src.data.source_job_registry import JOB_REGISTRY
    from src.ingest import post_trade_capital_daemon as daemon

    daemon_file = Path(daemon.__file__)
    assert "trade_retention" in _scheduled_ids_in((daemon_file,))
    spec = JOB_REGISTRY["trade_retention"]
    assert spec.owner_daemon == "post_trade_capital"
    assert spec.callable_ref == "_trade_retention_cycle"
    assert callable(getattr(daemon, spec.callable_ref))
