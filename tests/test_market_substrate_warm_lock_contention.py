# Created: 2026-06-08
# Last reused or audited: 2026-08-04
# Authority basis: background TRADE writer fast-yield hotfix; foreground priority capture retains the established contention budget.
#   path. Live evidence (zeus-live.err 2026-06-08 09:27:50): the EDLI market-substrate
#   warm cycle inserted 0 snapshots ("executable_substrate_coverage_status: 'NONE'"),
#   all failures "database is locked", because the per-row busy_timeout was clamped to
#   250 ms (ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS default) inside the capture loop,
#   overriding the canonical 30 s PRAGMA busy_timeout on the trade connection. Under
#   real in-process trade-DB write contention (executor submit / exit lifecycle /
#   CollateralLedger heartbeat all open independent trade connections), a 250 ms wait
#   fails fast and the universe-wide executable substrate is never refreshed —
#   starving the armed daemon of executable candidates so it cannot trade.
# Lifecycle: created=2026-06-08; last_reviewed=2026-08-03; last_reused=2026-08-03
# Purpose: Protect bounded substrate-writer contention and priority-turnstile relationships.
# Reuse: Inspect current snapshot-writer callers, flock semantics, and contention budgets first.
"""Relationship antibody: only broad warm substrate capture fast-yields contention.

CROSS-MODULE INVARIANT (the relationship, not a function):
  When ``refresh_executable_market_substrate_snapshots`` (the universe-wide
  executable_market_snapshots writer, owned by market_scanner) shares the trade
  DB with an active writer, the background cycle must return after its 25ms
  SQLite budget instead of holding a coordinator lease for seconds. The next
  warm tick retries the deferred snapshot.

Two tests:
  R-FAST-YIELD: a competing connection holds the trade-DB write lock while the
    warm cycle runs. The warm-cycle capture performs a REAL insert and returns
    promptly with a retryable lock failure rather than waiting out the lock.
  R-BOUND: only broad background capture receives the fixed 25ms busy timeout;
    priority confirmation capture retains its prior budget semantics.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import src.data.market_scanner as ms
from src.data.market_scanner import (
    _snapshot_capture_busy_timeout_ms,
    refresh_executable_market_substrate_snapshots,
)

_NOW = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)


def test_priority_turnstile_started_first_yields_later_broad_process(tmp_path):
    """An earlier priority intent deterministically prevents cross-process overtaking."""

    from src.data.job_lock import acquire_market_substrate_turnstile

    probe = """
from pathlib import Path
import sys
from src.data.job_lock import acquire_market_substrate_turnstile
with acquire_market_substrate_turnstile(priority=False, _locks_dir_override=Path(sys.argv[1])) as admission:
    print(admission.status, flush=True)
    raise SystemExit(0 if not admission.acquired else 9)
"""
    with acquire_market_substrate_turnstile(
        priority=True,
        _locks_dir_override=tmp_path,
    ) as priority:
        assert priority.acquired and priority.status == "priority_intent_acquired"
        completed = subprocess.run(
            [sys.executable, "-c", probe, str(tmp_path)],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=False,
        )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "priority_intent_active"


def test_priority_turnstile_exception_releases_without_reset_debt(tmp_path):
    """Context exit releases intent even when scope discovery raises."""

    from src.data.job_lock import acquire_market_substrate_turnstile

    with pytest.raises(RuntimeError, match="scope exploded"):
        with acquire_market_substrate_turnstile(
            priority=True,
            _locks_dir_override=tmp_path,
        ) as priority:
            assert priority.acquired
            raise RuntimeError("scope exploded")

    with acquire_market_substrate_turnstile(
        priority=False,
        _locks_dir_override=tmp_path,
    ) as broad:
        assert broad.acquired and broad.status == "broad_turn_admitted"


def test_priority_turnstile_crash_is_released_by_os(tmp_path):
    """Process death drops flock intent; the lock file itself is never a ratchet."""

    from src.data.job_lock import acquire_market_substrate_turnstile

    crash = """
from pathlib import Path
import os
import sys
from src.data.job_lock import acquire_market_substrate_turnstile
with acquire_market_substrate_turnstile(priority=True, _locks_dir_override=Path(sys.argv[1])) as admission:
    print('READY' if admission.acquired else admission.status, flush=True)
    os._exit(17)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", crash, str(tmp_path)],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "READY"
    assert process.wait(timeout=3.0) == 17

    with acquire_market_substrate_turnstile(
        priority=False,
        _locks_dir_override=tmp_path,
    ) as broad:
        assert broad.acquired and broad.status == "broad_turn_admitted"


def test_price_channel_exact_refresh_uses_priority_intent_without_broad_discovery(
    monkeypatch, tmp_path
):
    """Exact channel refresh declares priority and never closes over broad discovery."""

    import inspect

    import src.data.job_lock as job_lock
    from src.data.job_lock import acquire_market_substrate_turnstile
    import src.ingest.price_channel_ingest as price_channel

    monkeypatch.setattr(job_lock, "_LOCKS_DIR", tmp_path)
    with acquire_market_substrate_turnstile(priority=False) as broad:
        assert broad.acquired
        with price_channel._market_substrate_priority_turnstile() as priority:
            assert priority.acquired is False
            assert priority.status == "broad_turn_active"

    source = inspect.getsource(price_channel._edli_market_channel_ingestor_cycle)
    refresh_action = source[source.index("def _refresh_snapshot_action") :].split(
        "# The redecision-routing decision", 1
    )[0]
    assert "find_weather_markets_or_raise" not in refresh_action
    assert refresh_action.index("_market_substrate_priority_turnstile()") < refresh_action.index(
        "_market_substrate_priority_refresh_lock.acquire"
    )
    assert 'acquire_lock("market_substrate_priority_refresh")' in refresh_action
    assert "public_request_priority=RequestPriority.SUBMIT_JIT" in refresh_action
    assert "anonymous action cannot expand refresh scope" in refresh_action
    missing_topology = refresh_action[refresh_action.index("if market is None:") :]
    assert 'return "deferred"' in missing_topology


def test_priority_process_lock_is_independent_of_held_broad_process_lock(tmp_path):
    """A running broad scan cannot deny an exact cross-process refresh lease."""

    from src.data.job_lock import acquire_lock

    with acquire_lock(
        "market_substrate_refresh", _locks_dir_override=tmp_path
    ) as broad:
        assert broad
        with acquire_lock(
            "market_substrate_priority_refresh", _locks_dir_override=tmp_path
        ) as priority:
            assert priority
        with acquire_lock(
            "market_substrate_refresh", _locks_dir_override=tmp_path
        ) as competing_broad:
            assert competing_broad is False


# ---------------------------------------------------------------------------
# Fixtures: a real file-backed trade DB (WAL lock is shareable across
# connections only when file-backed; ":memory:" gives each connection its own DB)
# ---------------------------------------------------------------------------

def _create_trade_db(path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            event_slug TEXT,
            condition_id TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS _lock_probe (id INTEGER PRIMARY KEY, v INTEGER);
        """
    )
    conn.commit()
    conn.close()


def _make_market(idx: int) -> dict:
    cid = f"0x{idx:04x}" + "0" * 60
    cid = cid[:66]
    no_token = f"0x{idx:04x}" + "1" * 60
    no_token = no_token[:66]
    return {
        "event_id": f"evt-{idx}",
        "slug": f"highest-temperature-in-city{idx}-on-2026-06-09",
        "title": f"Highest temperature in City{idx}?",
        "city": f"City{idx}",
        "target_date": "2026-06-09",
        "temperature_metric": "highest",
        "outcomes": [
            {
                "condition_id": cid,
                "token_id": f"0x{idx:04x}" + "a" * 60,
                "no_token_id": no_token,
                "executable": True,
                "accepting_orders": True,
                "closed": False,
                "enable_orderbook": True,
            }
        ],
    }


def _make_clob_mock() -> MagicMock:
    clob = MagicMock()
    clob.get_clob_market_info.side_effect = lambda cid: {
        "condition_id": cid,
        "tokens": [{"token_id": "0xaaaa", "outcome": "YES"}, {"token_id": "0xbbbb", "outcome": "NO"}],
        "rewards": {"min_size": 0, "max_spread": 0},
    }
    clob.get_orderbook_snapshot.side_effect = lambda tid: {
        "asset_id": tid,
        "bids": [{"price": "0.55", "size": "100"}],
        "asks": [{"price": "0.60", "size": "100"}],
    }
    clob.get_fee_rate_details.side_effect = lambda tid: {
        "feeSchedule": {"makerFeeRate": "0.0", "takerFeeRate": "0.02"}
    }
    return clob


def test_snapshot_persist_context_wraps_insert_and_commit(monkeypatch):
    """The coordinator lease must cover the durable snapshot write unit only.

    The refresh loop may spend seconds on CLOB/network prefetch, but the
    per-row persist context must wrap the append, transition write, and commit
    together so the unified writer lease is held for milliseconds, not for the
    whole substrate refresh.
    """

    events: list[object] = []
    commit_records: list[dict[str, object]] = []

    class _FakeConn:
        total_changes = 0

        def commit(self) -> None:
            events.append("commit")

        def rollback(self) -> None:
            events.append("rollback")

    class _FakeLease:
        def record_commit(self, **kwargs) -> None:
            commit_records.append(kwargs)

    class _PersistContext:
        def __enter__(self):
            events.append("enter")
            return _FakeLease()

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            events.append("exit")
            return False

    def _fake_insert(conn, snapshot, **_kwargs) -> None:
        events.append(("insert", snapshot.condition_id))
        conn.total_changes += 1

    monkeypatch.setattr(ms, "insert_snapshot", _fake_insert)
    monkeypatch.setattr(
        ms,
        "_write_book_hash_transition",
        lambda **_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(ms, "_prev_orderbook_hash_by_market", {})

    conn = _FakeConn()
    market = _make_market(1)
    outcome = market["outcomes"][0]
    outcome["question_id"] = "question-1"
    outcome["active"] = True
    prefetched_book = {
        "asset_id": outcome["token_id"],
        "market": outcome["token_id"],
        "bids": [{"price": "0.55", "size": "100"}],
        "asks": [{"price": "0.60", "size": "100"}],
        "tick_size": "0.01",
        "min_order_size": "1",
        "neg_risk": False,
    }
    decision = SimpleNamespace(
        edge=SimpleNamespace(direction="buy_yes"),
        tokens={
            "token_id": outcome["token_id"],
            "no_token_id": outcome["no_token_id"],
            "market_id": outcome["condition_id"],
        },
    )

    class _Clob:
        def get_clob_market_info(self, condition_id: str) -> dict:
            return {
                "condition_id": condition_id,
                "tokens": [
                    {"token_id": outcome["token_id"], "outcome": "YES"},
                    {"token_id": outcome["no_token_id"], "outcome": "NO"},
                ],
                "archived": False,
                "enable_order_book": True,
                "accepting_orders": True,
                "tick_size": "0.01",
                "min_order_size": "1",
                "neg_risk": False,
            }

        def get_fee_rate_details(self, _token_id: str) -> dict:
            return {"feeRate": "0.02"}

    ms.capture_executable_market_snapshot(
        conn,
        market=market,
        decision=decision,
        clob=_Clob(),
        captured_at=_NOW,
        scan_authority="VERIFIED",
        prefetched_orderbook=prefetched_book,
        tolerate_missing_book=True,
        persist_context_factory=_PersistContext,
        commit_after_persist=True,
    )

    assert events == ["enter", ("insert", outcome["condition_id"]), "commit", "exit"]
    assert commit_records
    assert commit_records[0]["rows_changed"] == 1
    assert commit_records[0]["commit_ms"] >= 0


# ---------------------------------------------------------------------------
# R-FAST-YIELD: contention must return promptly and leave the next tick retryable.
# ---------------------------------------------------------------------------

def test_background_warm_capture_applies_fast_yield_busy_timeout_to_handed_conn(tmp_path, monkeypatch):
    """The handed snapshot connection receives the exact 25ms lock budget."""
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_DB_BUSY_TIMEOUT_MS", raising=False)

    BUSY_TIMEOUT_MS = 25

    db_path = tmp_path / "trade.db"
    _create_trade_db(db_path)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")

    observed_busy_ms: list[int] = []

    def _probe_capture(c, *, market, decision, captured_at, **kwargs):
        # Read the busy_timeout the loop installed on THIS connection — the wait
        # budget the real insert would use against a competing write lock.
        observed_busy_ms.append(int(c.execute("PRAGMA busy_timeout").fetchone()[0]))
        cid = market["outcomes"][0]["condition_id"]
        c.execute(
            "INSERT OR REPLACE INTO executable_market_snapshots "
            "(snapshot_id, event_slug, condition_id, captured_at) VALUES (?, ?, ?, ?)",
            (f"{cid}-{len(observed_busy_ms)}", market.get("slug"), cid, captured_at.isoformat()),
        )
        return {"persisted": True}

    monkeypatch.setattr(ms, "capture_executable_market_snapshot", _probe_capture)

    markets = [_make_market(i) for i in range(1, 4)]
    summary = refresh_executable_market_substrate_snapshots(
        conn,
        markets=markets,
        clob=_make_clob_mock(),
        captured_at=_NOW,
        scan_authority="VERIFIED",
        max_outcomes=2,
        budget_seconds=30.0,
        background_fast_yield=True,
    )

    assert observed_busy_ms, "capture was never invoked"
    assert set(observed_busy_ms) == {BUSY_TIMEOUT_MS}
    assert summary["inserted"] >= 1, summary
    conn.close()


def test_background_warm_capture_fast_yields_then_retries_after_lock_release(tmp_path, monkeypatch):
    """The first broad tick yields; the next tick persists the deferred level."""
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_DB_BUSY_TIMEOUT_MS", raising=False)
    # Remove the lock-retry escape hatch so the test isolates the busy_timeout
    # WAIT itself, not the loop's bounded retry ladder.
    monkeypatch.setattr(ms, "_snapshot_capture_sqlite_lock_retries", lambda: 0)

    db_path = tmp_path / "trade.db"
    _create_trade_db(db_path)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")

    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_write_lock() -> None:
        # An INDEPENDENT trade-DB connection (executor submit / exit / ledger
        # heartbeat pattern) holds the single WAL write lock.
        other = sqlite3.connect(str(db_path), timeout=30)
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("PRAGMA busy_timeout = 30000")
        other.execute("BEGIN IMMEDIATE")
        other.execute("INSERT INTO _lock_probe (v) VALUES (1)")
        lock_held.set()
        assert release_lock.wait(timeout=2.0)
        other.commit()
        other.close()

    holder = threading.Thread(target=_hold_write_lock, daemon=True)
    holder.start()
    assert lock_held.wait(timeout=2.0), "lock holder failed to acquire write lock"

    capture_calls: list[str] = []

    def _real_insert_capture(c, *, market, decision, captured_at, **kwargs):
        cid = market["outcomes"][0]["condition_id"]
        capture_calls.append(cid)
        c.execute(
            "INSERT OR REPLACE INTO executable_market_snapshots "
            "(snapshot_id, event_slug, condition_id, captured_at) VALUES (?, ?, ?, ?)",
            (f"{cid}-{len(capture_calls)}", market.get("slug"), cid, captured_at.isoformat()),
        )
        return {"persisted": True}

    monkeypatch.setattr(ms, "capture_executable_market_snapshot", _real_insert_capture)

    markets = [_make_market(1)]
    started = time.monotonic()
    summary = refresh_executable_market_substrate_snapshots(
        conn,
        markets=markets,
        clob=_make_clob_mock(),
        captured_at=_NOW,
        scan_authority="VERIFIED",
        max_outcomes=2,
        budget_seconds=30.0,
        background_fast_yield=True,
    )
    elapsed = time.monotonic() - started

    failure_errors = [f.get("error", "") for f in summary.get("failure_samples", [])]
    assert elapsed < 0.15, f"background warm capture waited too long: {elapsed:.3f}s"
    assert any("database is locked" in e.lower() for e in failure_errors), summary
    assert summary["executable_substrate_coverage_status"] == "NONE"
    assert summary["inserted"] == 0

    release_lock.set()
    holder.join(timeout=3.0)
    assert not holder.is_alive()
    next_summary = refresh_executable_market_substrate_snapshots(
        conn,
        markets=markets,
        clob=_make_clob_mock(),
        captured_at=_NOW,
        scan_authority="VERIFIED",
        max_outcomes=2,
        budget_seconds=30.0,
        background_fast_yield=True,
    )
    assert next_summary["inserted"] > 0, next_summary
    assert next_summary["executable_substrate_coverage_status"] != "NONE"
    rows = conn.execute("SELECT COUNT(*) FROM executable_market_snapshots").fetchone()[0]
    assert rows == next_summary["inserted"]
    conn.close()


# ---------------------------------------------------------------------------
# R-BOUND: per-row capture busy_timeout is fixed at the fast-yield budget.
# ---------------------------------------------------------------------------

def test_background_capture_busy_timeout_is_fixed_fast_yield_budget(monkeypatch):
    """Only the explicitly background helper has the fixed 25ms budget."""
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_DB_BUSY_TIMEOUT_MS", raising=False)

    assert ms._background_snapshot_capture_busy_timeout_ms() == 25


def test_priority_capture_keeps_foreground_budget_and_context_when_background_enabled(
    tmp_path,
    monkeypatch,
):
    """A broad refresh flag cannot downgrade a priority confirmation capture."""
    db_path = tmp_path / "trade.db"
    _create_trade_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout = 30000")
    market = _make_market(1)
    condition_id = market["outcomes"][0]["condition_id"]
    observed: list[tuple[int, object]] = []
    foreground_context = object()
    background_context = object()

    def _capture(c, **kwargs):
        observed.append(
            (
                int(c.execute("PRAGMA busy_timeout").fetchone()[0]),
                kwargs["persist_context_factory"],
            )
        )
        return {"persisted": True}

    monkeypatch.setattr(ms, "capture_executable_market_snapshot", _capture)
    try:
        summary = refresh_executable_market_substrate_snapshots(
            conn,
            markets=[market],
            clob=_make_clob_mock(),
            captured_at=_NOW,
            scan_authority="VERIFIED",
            max_outcomes=2,
            budget_seconds=30.0,
            priority_condition_ids={condition_id},
            snapshot_write_context_factory=foreground_context,
            background_snapshot_write_context_factory=background_context,
            background_fast_yield=True,
        )
    finally:
        conn.close()

    assert summary["inserted"] > 0
    assert observed
    assert {budget for budget, _context in observed} == {8000}
    assert {context for _budget, context in observed} == {foreground_context}


def test_batch_capture_busy_timeout_splits_budget_across_remaining_candidates(monkeypatch):
    """Batch substrate refresh must prefer family coverage over one locked row."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)

    single = _snapshot_capture_busy_timeout_ms(12.0)
    batch = _snapshot_capture_busy_timeout_ms(12.0, remaining_candidates=46)

    assert single == 8000
    assert batch == 260


def test_small_priority_capture_busy_timeout_splits_candidate_budget(monkeypatch):
    """Small priority recaptures must not let one locked row spend the reserve."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PRIORITY_SHARE_MAX_CANDIDATES", raising=False)

    broad_batch = _snapshot_capture_busy_timeout_ms(12.0, remaining_candidates=46)
    priority = _snapshot_capture_busy_timeout_ms(
        12.0,
        remaining_candidates=2,
        priority_candidate=True,
    )

    assert broad_batch == 260
    assert priority == 6000


def test_late_small_priority_capture_keeps_durable_floor(monkeypatch):
    """Late-cycle money-path recapture must not collapse to the progress floor."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PRIORITY_FLOOR_MAX_CANDIDATES", raising=False)

    priority = _snapshot_capture_busy_timeout_ms(
        0.02,
        remaining_candidates=2,
        priority_candidate=True,
    )
    broad = _snapshot_capture_busy_timeout_ms(
        0.02,
        remaining_candidates=2,
        priority_candidate=False,
    )

    assert priority == 4000
    assert broad == 150


def test_family_priority_capture_busy_timeout_keeps_durable_floor(monkeypatch):
    """Family-sized money-path recaptures must wait out normal WAL contention."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PRIORITY_FLOOR_MAX_CANDIDATES", raising=False)

    broad_batch = _snapshot_capture_busy_timeout_ms(12.0, remaining_candidates=46)
    priority_family = _snapshot_capture_busy_timeout_ms(
        12.0,
        remaining_candidates=21,
        priority_candidate=True,
    )

    assert broad_batch == 260
    assert priority_family == 4000


def test_claim_priority_batch_capture_busy_timeout_keeps_durable_floor(monkeypatch):
    """A live claim-order warm batch must not fail-fast under normal WAL contention."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PRIORITY_FLOOR_MAX_CANDIDATES", raising=False)

    priority_claim_batch = _snapshot_capture_busy_timeout_ms(
        5.0,
        remaining_candidates=32,
        priority_candidate=True,
    )

    assert priority_claim_batch == 4000


def test_large_priority_capture_busy_timeout_splits_batch_budget(monkeypatch):
    """An oversized priority batch must make progress past one locked row."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PRIORITY_FLOOR_MAX_CANDIDATES", raising=False)

    broad_batch = _snapshot_capture_busy_timeout_ms(12.0, remaining_candidates=46)
    priority_batch = _snapshot_capture_busy_timeout_ms(
        12.0,
        remaining_candidates=46,
        priority_candidate=True,
    )

    assert priority_batch == broad_batch == 260


def test_multi_candidate_lock_retries_yield_to_next_candidate():
    """A locked candidate in a multi-row refresh should not retry in place."""

    assert ms._snapshot_capture_effective_lock_retries(
        configured_retries=2,
        remaining_candidates=4,
    ) == 0
    assert ms._snapshot_capture_effective_lock_retries(
        configured_retries=2,
        remaining_candidates=1,
    ) == 2


# ---------------------------------------------------------------------------
# R-INTERVAL: the warm-cycle refresh budget must fit inside the scheduler interval
# (the OTHER half of the coverage-starvation: even when inserts succeed, a cycle
# that overruns its 20s trigger is "skipped: maximum number of running instances
# reached" and never refreshes the substrate).
# ---------------------------------------------------------------------------

def test_refresh_budget_fits_inside_warm_interval(monkeypatch):
    """R-INTERVAL (CYCLE_CANNOT_OVERRUN_ITS_TRIGGER): the EDLI substrate warm
    refresh wall-clock budget default must be STRICTLY LESS than the warm-cycle
    APScheduler interval, so the cycle finishes before its next trigger.

    Live (zeus-live.err 2026-06-08): a 29s budget on a 20s interval made every
    overlapping run skip ("maximum number of running instances reached (1)") and
    the executable substrate never refreshed. The budget also stays within the
    30s executable-price freshness window.
    """
    import src.main as main_mod

    monkeypatch.delenv("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", raising=False)

    interval_s = main_mod._EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS
    budget_default_s = max(
        5.0, float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0"))
    )

    assert budget_default_s < interval_s, (
        f"warm refresh budget default {budget_default_s}s must be strictly less than "
        f"the {interval_s}s warm interval — otherwise the cycle overruns its trigger "
        "and is skipped (coverage NONE, daemon starved of executable candidates)"
    )
    # Freshness-window upper bound: the interval must also be <= the 30s executable
    # price freshness window so a refreshed snapshot is still fresh at the next tick.
    assert interval_s <= 30.0, (
        f"warm interval {interval_s}s exceeds the 30s executable-price freshness window"
    )


def test_substrate_clob_timeout_is_short_and_independent_of_discovery(monkeypatch):
    """Background substrate refresh must not inherit the long discovery CLOB timeout.

    The warm lane retries continuously and must stay inside its 20s cadence.
    ``ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS`` is allowed to be longer for broad
    discovery, but it must not make pending-family /books or targeted decision
    refresh block most of a live cycle. The default must still exceed the
    measured cold TLS handshake envelope for the CLOB host.
    """

    import src.data.substrate_observer as substrate_observer

    monkeypatch.setenv("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "9.0")
    monkeypatch.delenv("ZEUS_SUBSTRATE_CLOB_TIMEOUT_SECONDS", raising=False)

    assert substrate_observer._substrate_clob_timeout_seconds() == pytest.approx(4.0)

    monkeypatch.setenv("ZEUS_SUBSTRATE_CLOB_TIMEOUT_SECONDS", "2.25")

    assert substrate_observer._substrate_clob_timeout_seconds() == pytest.approx(2.25)
