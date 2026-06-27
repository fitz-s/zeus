# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: Fitz #5 "database is locked" CATEGORY-kill on the trade-substrate
#   path. Live evidence (zeus-live.err 2026-06-08 09:27:50): the EDLI market-substrate
#   warm cycle inserted 0 snapshots ("executable_substrate_coverage_status: 'NONE'"),
#   all failures "database is locked", because the per-row busy_timeout was clamped to
#   250 ms (ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS default) inside the capture loop,
#   overriding the canonical 30 s PRAGMA busy_timeout on the trade connection. Under
#   real in-process trade-DB write contention (executor submit / exit lifecycle /
#   CollateralLedger heartbeat all open independent trade connections), a 250 ms wait
#   fails fast and the universe-wide executable substrate is never refreshed —
#   starving the armed daemon of executable candidates so it cannot trade.
"""Relationship antibody: warm-cycle substrate writer WAITS out contention.

CROSS-MODULE INVARIANT (the relationship, not a function):
  When ``refresh_executable_market_substrate_snapshots`` (the universe-wide
  executable_market_snapshots writer, owned by market_scanner) shares the trade
  DB with ANOTHER writer that briefly holds the WAL write lock, the warm-cycle
  insert must WAIT on its busy_timeout and COMMIT — it must NOT raise / record
  "database is locked" and leave coverage at NONE.

  The boundary that loses semantics in the bug: the caller (main.py
  _refresh_pending_family_snapshots) hands market_scanner a trade connection
  carrying the canonical 30 s PRAGMA busy_timeout, but the capture loop
  (market_scanner.py:4062) OVERRODE it with min(250 ms, remaining). A short
  competing lock therefore turned into a hard failure instead of a brief wait.

Two tests:
  R-WAIT (CONTENTION_WAITS_NOT_RAISES): a competing connection holds the trade-DB
    write lock for ~0.4 s while the warm cycle runs. The warm-cycle capture
    performs a REAL insert on the handed connection. With the contention fix the
    insert WAITS and commits (inserted >= 1, coverage != NONE, no "database is
    locked"). On the pre-fix 250 ms clamp this RED-fails (inserted == 0).
  R-FLOOR (BUSY_TIMEOUT_NOT_SHRUNK_BELOW_FLOOR): the per-row capture wait budget
    never collapses below a usable floor even when the remaining per-cycle budget
    is small — so a contended late-cycle insert still waits long enough to win the
    lock rather than fail-fast at a few milliseconds.
"""
from __future__ import annotations

import os
import sqlite3
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

    def _fake_insert(conn, snapshot) -> None:
        events.append(("insert", snapshot.condition_id))
        conn.total_changes += 1

    monkeypatch.setattr(ms, "insert_snapshot", _fake_insert)
    monkeypatch.setattr(ms, "_write_book_hash_transition", lambda **_kwargs: None)
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

    ms.capture_executable_market_snapshot(
        conn,
        market=market,
        decision=decision,
        clob=object(),
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
# R-WAIT: contention must produce a WAIT, not "database is locked"
# ---------------------------------------------------------------------------

def test_warm_cycle_capture_applies_floor_busy_timeout_to_handed_conn(tmp_path, monkeypatch):
    """R-WAIT (deterministic): the effective PRAGMA busy_timeout ON THE HANDED
    CONNECTION at the moment capture runs must be >= the usable floor, so a real
    competing lock is WAITED out rather than fail-fasted.

    This reads the connection's live ``PRAGMA busy_timeout`` from inside the
    patched capture — the exact value the next ``insert_snapshot`` will use to
    wait on the trade-DB write lock. The pre-fix loop clamped it to
    min(250 ms, remaining) (market_scanner.py:4062), so this asserts the boundary
    value directly with no timing race.

    RED on pre-fix code: observed busy_timeout == 250 (or less). GREEN: >= floor.
    """
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_DB_BUSY_TIMEOUT_MS", raising=False)

    FLOOR_MS = 1000

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
    )

    assert observed_busy_ms, "capture was never invoked"
    assert min(observed_busy_ms) >= FLOOR_MS, (
        "warm-cycle loop installed a busy_timeout below the usable floor on the "
        f"trade connection: observed={observed_busy_ms}. A real competing write "
        "lock would fail-fast as 'database is locked' instead of waiting."
    )
    assert summary["inserted"] >= 1, summary
    conn.close()


def test_warm_cycle_capture_waits_out_real_trade_db_contention(tmp_path, monkeypatch):
    """R-WAIT (integration): a competing writer holding the trade-DB write lock
    must NOT make the warm-cycle snapshot insert record 'database is locked'.

    The capture is patched to a REAL insert on the handed connection so the
    busy_timeout boundary is exercised against a real SQLite WAL write lock. The
    lock is held longer than the entire pre-fix wait+retry budget
    (250 ms × 3 attempts + retry sleeps ≈ <1 s) but well within a healthy wait, so
    the buggy clamp fails and the fix waits and commits.
    """
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

    def _hold_write_lock() -> None:
        # An INDEPENDENT trade-DB connection (executor submit / exit / ledger
        # heartbeat pattern) holds the single WAL write lock.
        other = sqlite3.connect(str(db_path), timeout=30)
        other.execute("PRAGMA journal_mode=WAL")
        other.execute("PRAGMA busy_timeout = 30000")
        other.execute("BEGIN IMMEDIATE")
        other.execute("INSERT INTO _lock_probe (v) VALUES (1)")
        lock_held.set()
        # Hold longer than the buggy 250 ms clamp (so a clamped wait loses) but
        # well under a healthy busy_timeout wait (so the fix wins).
        time.sleep(0.7)
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

    markets = [_make_market(i) for i in range(1, 4)]
    summary = refresh_executable_market_substrate_snapshots(
        conn,
        markets=markets,
        clob=_make_clob_mock(),
        captured_at=_NOW,
        scan_authority="VERIFIED",
        max_outcomes=2,
        budget_seconds=30.0,
    )
    holder.join(timeout=3.0)

    failure_errors = [f.get("error", "") for f in summary.get("failure_samples", [])]
    assert not any("database is locked" in e.lower() for e in failure_errors), (
        f"warm cycle recorded 'database is locked' under contention: {summary}"
    )
    assert summary["inserted"] >= 1, (
        f"warm cycle inserted no snapshots under contention (coverage starvation): {summary}"
    )
    assert summary["executable_substrate_coverage_status"] != "NONE", (
        f"executable substrate coverage collapsed to NONE under contention: {summary}"
    )
    rows = conn.execute("SELECT COUNT(*) FROM executable_market_snapshots").fetchone()[0]
    assert rows >= 1, f"no snapshot row durably committed: {rows}"
    conn.close()


# ---------------------------------------------------------------------------
# R-FLOOR: per-row capture busy_timeout must not collapse below a usable floor
# ---------------------------------------------------------------------------

def test_capture_busy_timeout_not_shrunk_below_floor(monkeypatch):
    """R-FLOOR: even with a tiny remaining per-cycle budget the per-row capture
    wait budget stays at or above a usable floor, so a contended late-cycle insert
    still waits long enough to win the lock instead of fail-fasting at a few ms.

    Pre-fix: _snapshot_capture_busy_timeout_ms(remaining) returned
    min(250, remaining_ms) — for remaining=0.02 s it returned 20 ms, so the very
    inserts the daemon most needs (late-cycle, under contention) fail fastest.
    """
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_DB_BUSY_TIMEOUT_MS", raising=False)

    FLOOR_MS = 1000  # a contended insert must be allowed to wait at least ~1 s

    # Small remaining budget (late cycle, under pressure) must still yield a
    # usable wait — not collapse to tens of milliseconds.
    assert _snapshot_capture_busy_timeout_ms(0.02) >= FLOOR_MS, (
        "per-row capture busy_timeout collapsed below the usable floor when the "
        "remaining per-cycle budget was small — late-cycle contended inserts will "
        "fail-fast as 'database is locked'"
    )
    assert _snapshot_capture_busy_timeout_ms(0.5) >= FLOOR_MS
    assert _snapshot_capture_busy_timeout_ms(10.0) >= FLOOR_MS


def test_batch_capture_busy_timeout_splits_budget_across_remaining_candidates(monkeypatch):
    """Batch substrate refresh must prefer family coverage over one locked row."""

    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_BUSY_TIMEOUT_FLOOR_MS", raising=False)
    monkeypatch.delenv("ZEUS_SNAPSHOT_CAPTURE_PROGRESS_TIMEOUT_FLOOR_MS", raising=False)

    single = _snapshot_capture_busy_timeout_ms(12.0)
    batch = _snapshot_capture_busy_timeout_ms(12.0, remaining_candidates=46)

    assert single >= 4000
    assert 0 < batch < single
    assert batch >= 150


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

    assert broad_batch < priority < 8000
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

    assert priority >= 4000
    assert broad < priority


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

    assert broad_batch < priority_family
    assert priority_family >= 4000


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

    assert priority_claim_batch >= 4000


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

    assert priority_batch == broad_batch
    assert 0 < priority_batch < 4000


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
    import src.main as main_module

    monkeypatch.setenv("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "9.0")
    monkeypatch.delenv("ZEUS_SUBSTRATE_CLOB_TIMEOUT_SECONDS", raising=False)

    assert substrate_observer._substrate_clob_timeout_seconds() == pytest.approx(4.0)
    assert main_module._substrate_clob_timeout_seconds() == pytest.approx(4.0)

    monkeypatch.setenv("ZEUS_SUBSTRATE_CLOB_TIMEOUT_SECONDS", "2.25")

    assert substrate_observer._substrate_clob_timeout_seconds() == pytest.approx(2.25)
    assert main_module._substrate_clob_timeout_seconds() == pytest.approx(2.25)
