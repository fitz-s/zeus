# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/WAVE_2_PLAN.md §#36 F10
#   "risk_state.db separate-process drift"
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F10 (cross-DB freshness invariant) antibody — synthetic two-DB fixture
#   asserts that when collateral_ledger_snapshots.captured_at falls behind
#   position_current.updated_at by more than the freshness window, the
#   violation is detectable by a structured predicate (i.e. the asymmetry
#   does not silently survive a read).
# Reuse: Run on every PR touching the riskguard ↔ live-trading shared
#   freshness contract or the collateral_ledger_snapshots / position_current
#   timestamp semantics.

"""F10 cross-DB freshness invariant antibody.

Background (WAVE_2_PLAN §#36):
> risk_state.db is written by the riskguard daemon. zeus_trades.db is written
> by the live-trading daemon. Both processes operate on overlapping state.
> Symptom class: collateral_ledger_snapshots.captured_at (the freshness witness
> for chain-side balances) silently lags position_current.updated_at, so the
> riskguard makes sizing decisions from stale collateral.

This antibody is a PREDICATE test, not a probe of live state. It establishes
a `freshness_violation(now, snapshot_captured_at, position_updated_at, window_s)`
shape so future cross-DB readers have a canonical predicate to call, and so a
regression that allows the predicate to silently mask drift is caught.

3 probes:
1. Within-window: snapshot ahead of position → OK
2. Within-window: snapshot just behind position → OK
3. Out-of-window: snapshot >window behind position → VIOLATION detected
4. Edge: snapshot timestamp missing → VIOLATION detected
5. Cross-DB shape: synthetic write to zeus_trades.db happens *after* a write
   to risk_state.db (separate-process drift simulation); freshness gate must
   surface the asymmetry.

The predicate is a small helper, not a production import — F10 fix-shape was
"assert at risk_state.db reader-side"; the antibody documents the assertion
shape so a future reader site can adopt it. (No production code is modified
in this commit; the assertion shape is the deliverable.)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------
# F10 predicate — the cross-DB freshness gate.
# ---------------------------------------------------------------------------
# This is intentionally small + self-contained: a single function whose
# signature documents the F10 contract. Production reader sites are free to
# adopt it as a helper or inline equivalent.

def freshness_violation(
    *,
    now_utc: datetime,
    snapshot_captured_at: str | None,
    position_updated_at: str | None,
    window_seconds: float,
) -> tuple[bool, str | None]:
    """Return (violation, reason).

    Args:
        now_utc: clock as seen by the reader
        snapshot_captured_at: collateral_ledger_snapshots.captured_at ISO-8601 or None
        position_updated_at: position_current.updated_at ISO-8601 or None
        window_seconds: max permitted lag of snapshot behind position.updated_at

    A violation fires when:
      * captured_at is missing/unparseable, OR
      * captured_at is older than position_updated_at by > window_seconds, OR
      * captured_at is in the future relative to now_utc (clock drift).

    The "captured_at newer than position_updated_at" direction is OK — a
    fresh chain snapshot taken before the next position event is the
    normal steady-state pattern.
    """

    def _parse(ts: str | None) -> datetime | None:
        if not ts:
            return None
        try:
            # Handle both `+00:00` and `Z` suffixes consistently.
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    sn = _parse(snapshot_captured_at)
    if sn is None:
        return (True, "collateral_snapshot_missing_or_unparseable")

    if sn > now_utc + timedelta(seconds=window_seconds):
        # Clock drift: snapshot from the "future" relative to reader.
        return (True, "collateral_snapshot_future")

    pos = _parse(position_updated_at)
    if pos is None:
        # No position to compare against ⇒ no asymmetry to report;
        # snapshot-only state is the cold-start case, not a violation.
        return (False, None)

    lag_seconds = (pos - sn).total_seconds()
    if lag_seconds > window_seconds:
        return (
            True,
            f"collateral_snapshot_stale_behind_position_lag={int(lag_seconds)}s",
        )
    return (False, None)


# ---------------------------------------------------------------------------
# Fixtures — synthetic two-DB shape.
# ---------------------------------------------------------------------------

POSITION_CURRENT_DDL = """
CREATE TABLE position_current (
    position_id TEXT PRIMARY KEY,
    phase TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

COLLATERAL_LEDGER_SNAPSHOTS_DDL = """
CREATE TABLE collateral_ledger_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pusd_balance_micro INTEGER NOT NULL,
    captured_at TEXT NOT NULL,
    authority_tier TEXT NOT NULL CHECK (authority_tier IN ('CHAIN','VENUE','DEGRADED'))
);
"""


@pytest.fixture
def two_db_fixture(tmp_path):
    """Create two in-process SQLite DBs mirroring the cross-DB shape:
    - trades_db carries position_current + collateral_ledger_snapshots
      (in live Zeus, both tables live on zeus_trades.db post-K1 split).
    - risk_db is the freshness-consuming reader plane (risk_state.db).

    Tests then write a position row + a snapshot row at different
    timestamps and exercise the freshness_violation predicate.
    """
    trades_path = tmp_path / "zeus_trades.db"
    risk_path = tmp_path / "risk_state.db"
    trades = sqlite3.connect(trades_path)
    risk = sqlite3.connect(risk_path)
    trades.executescript(POSITION_CURRENT_DDL)
    trades.executescript(COLLATERAL_LEDGER_SNAPSHOTS_DDL)
    # risk_state.db only carries its single risk_state table; not used
    # by the predicate but present for shape parity.
    risk.execute("CREATE TABLE risk_state (id INTEGER PRIMARY KEY)")
    trades.commit()
    risk.commit()
    yield (trades, risk)
    trades.close()
    risk.close()


def _insert_position(trades_conn: sqlite3.Connection, *, position_id: str, updated_at: datetime) -> None:
    trades_conn.execute(
        "INSERT INTO position_current (position_id, phase, updated_at) VALUES (?, ?, ?)",
        (position_id, "active", updated_at.isoformat()),
    )
    trades_conn.commit()


def _insert_snapshot(trades_conn: sqlite3.Connection, *, captured_at: datetime, authority: str = "CHAIN") -> None:
    trades_conn.execute(
        "INSERT INTO collateral_ledger_snapshots "
        "(pusd_balance_micro, captured_at, authority_tier) VALUES (?, ?, ?)",
        (1_000_000, captured_at.isoformat(), authority),
    )
    trades_conn.commit()


def _latest_captured_at(trades_conn: sqlite3.Connection) -> str | None:
    row = trades_conn.execute(
        "SELECT captured_at FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _latest_position_updated_at(trades_conn: sqlite3.Connection, position_id: str) -> str | None:
    row = trades_conn.execute(
        "SELECT updated_at FROM position_current WHERE position_id = ?",
        (position_id,),
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Probe 1: snapshot ahead of position → OK
# ---------------------------------------------------------------------------

def test_snapshot_ahead_of_position_is_ok(two_db_fixture) -> None:
    trades, _risk = two_db_fixture
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    # Snapshot taken JUST before now, position updated 60s prior
    _insert_position(trades, position_id="p1", updated_at=now - timedelta(seconds=60))
    _insert_snapshot(trades, captured_at=now - timedelta(seconds=5))

    violation, reason = freshness_violation(
        now_utc=now,
        snapshot_captured_at=_latest_captured_at(trades),
        position_updated_at=_latest_position_updated_at(trades, "p1"),
        window_seconds=120,
    )
    assert violation is False, f"snapshot ahead of position should not violate: {reason}"


# ---------------------------------------------------------------------------
# Probe 2: snapshot just behind position (within window) → OK
# ---------------------------------------------------------------------------

def test_snapshot_just_behind_position_within_window_is_ok(two_db_fixture) -> None:
    trades, _risk = two_db_fixture
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    # Position updated 30s ago, snapshot taken 60s ago — window=120s, so OK
    _insert_position(trades, position_id="p1", updated_at=now - timedelta(seconds=30))
    _insert_snapshot(trades, captured_at=now - timedelta(seconds=60))

    violation, _reason = freshness_violation(
        now_utc=now,
        snapshot_captured_at=_latest_captured_at(trades),
        position_updated_at=_latest_position_updated_at(trades, "p1"),
        window_seconds=120,
    )
    assert violation is False, "snapshot 30s behind position within 120s window should be OK"


# ---------------------------------------------------------------------------
# Probe 3: snapshot >window behind position → VIOLATION (the F10 case)
# ---------------------------------------------------------------------------

def test_snapshot_far_behind_position_is_violation(two_db_fixture) -> None:
    """F10 main case: separate-process drift = collateral snapshot lags
    position_current by more than the freshness window."""
    trades, _risk = two_db_fixture
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    # Position updated just now, snapshot taken 5 minutes ago — window=120s
    _insert_position(trades, position_id="p1", updated_at=now - timedelta(seconds=5))
    _insert_snapshot(trades, captured_at=now - timedelta(seconds=300))

    violation, reason = freshness_violation(
        now_utc=now,
        snapshot_captured_at=_latest_captured_at(trades),
        position_updated_at=_latest_position_updated_at(trades, "p1"),
        window_seconds=120,
    )
    assert violation is True, "5m-old snapshot vs fresh position should violate window=120s"
    assert reason is not None and "stale_behind_position" in reason, (
        f"violation reason should name the lag direction; got {reason!r}"
    )


# ---------------------------------------------------------------------------
# Probe 4: missing snapshot → VIOLATION (edge — cold-start detection)
# ---------------------------------------------------------------------------

def test_missing_snapshot_is_violation(two_db_fixture) -> None:
    trades, _risk = two_db_fixture
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    _insert_position(trades, position_id="p1", updated_at=now)
    # No snapshot inserted

    violation, reason = freshness_violation(
        now_utc=now,
        snapshot_captured_at=_latest_captured_at(trades),
        position_updated_at=_latest_position_updated_at(trades, "p1"),
        window_seconds=120,
    )
    assert violation is True, "missing snapshot should violate"
    assert reason == "collateral_snapshot_missing_or_unparseable"


# ---------------------------------------------------------------------------
# Probe 5: cross-DB shape — separate-process drift simulation
# ---------------------------------------------------------------------------

def test_cross_db_separate_process_drift_simulation(two_db_fixture) -> None:
    """Simulate the F10 root cause: zeus_trades.db (live-trading daemon)
    writes new position_current.updated_at; risk_state.db (riskguard
    daemon) tick races later and reads a stale snapshot. The freshness
    gate must catch the asymmetry across DB boundaries.

    Note: collateral_ledger_snapshots and position_current both live on
    zeus_trades.db post-K1 (verified via PRAGMA inspection 2026-05-18).
    The "cross-DB" framing refers to the WRITING processes, not the
    table locations. The freshness predicate's input is two timestamps
    regardless of where they were sourced from.
    """
    trades, risk = two_db_fixture
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)

    # Step 1: live-trading daemon writes a snapshot at T0
    _insert_snapshot(trades, captured_at=now - timedelta(seconds=600))
    # Step 2: live-trading writes a new position_current at T0+595s
    #         (just before "now")
    _insert_position(trades, position_id="p1", updated_at=now - timedelta(seconds=5))
    # Step 3: riskguard tick wakes; reads both — must detect the gap
    violation, reason = freshness_violation(
        now_utc=now,
        snapshot_captured_at=_latest_captured_at(trades),
        position_updated_at=_latest_position_updated_at(trades, "p1"),
        window_seconds=120,
    )
    assert violation is True, (
        "separate-process drift simulation: stale snapshot (10m) vs fresh "
        "position must surface as F10 violation"
    )
    assert "stale_behind_position" in (reason or ""), (
        f"violation reason should classify as stale_behind_position; got {reason!r}"
    )

    # Sanity: risk_state.db is its own separate DB on disk (the "cross"
    # in "cross-DB" — separate file, separate writer process).
    risk_path = next(
        risk.execute("PRAGMA database_list")
    )[2]
    trades_path = next(
        trades.execute("PRAGMA database_list")
    )[2]
    assert risk_path != trades_path, "F10 fixture must use separate DB files"


# ---------------------------------------------------------------------------
# Probe 6: future-snapshot (clock drift) → VIOLATION
# ---------------------------------------------------------------------------

def test_future_snapshot_clock_drift_is_violation(two_db_fixture) -> None:
    trades, _risk = two_db_fixture
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)
    _insert_position(trades, position_id="p1", updated_at=now - timedelta(seconds=10))
    # Snapshot timestamp far in the future relative to reader's now
    _insert_snapshot(trades, captured_at=now + timedelta(seconds=600))

    violation, reason = freshness_violation(
        now_utc=now,
        snapshot_captured_at=_latest_captured_at(trades),
        position_updated_at=_latest_position_updated_at(trades, "p1"),
        window_seconds=120,
    )
    assert violation is True, "future-dated snapshot should violate clock-drift guard"
    assert reason == "collateral_snapshot_future"
