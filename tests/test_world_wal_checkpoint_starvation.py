# Created: 2026-06-04
# Last reused or audited: 2026-06-04
# Authority basis: docs/operations/HANDOFF_2026-06-04_live_restart_arm.md
#   + critic-proven root: zeus-world.db WAL bloat = checkpoint-starvation by
#     long-lived reader connections pinning the WAL floor (NOT I/O under the
#     world mutex). Live evidence: while a reader pins the floor,
#     PRAGMA wal_checkpoint(PASSIVE) copies fewer frames than the log holds
#     (checkpointed_frames < log_frames) and never truncates. Correction
#     2026-07-21 (audit finding W5-2): PASSIVE's busy field is ALWAYS 0 — it
#     is 1 only for a blocked RESTART/FULL/TRUNCATE checkpoint, never PASSIVE.
# Lifecycle: created=2026-06-04; last_reviewed=2026-06-04; last_reused=never
# Purpose: RED→GREEN relationship test for the WAL checkpoint-starvation fix.
#   Proves the MECHANISM (a reader that never ends its read transaction pins
#   the WAL floor so TRUNCATE returns BUSY and the WAL stays large) AND the FIX
#   (releasing the snapshot per-cycle + a wal_checkpoint(TRUNCATE) backstop
#   truncates the WAL to ~zero).
# Reuse: run on any PR touching src/state/db.py checkpoint helpers, the EDLI
#   reactor read-connection lifecycle, or the checkpoint scheduler job.

"""WAL checkpoint-starvation relationship test.

In SQLite WAL mode a read connection holds a snapshot (its read mark) from its
first SELECT until it COMMITs / ROLLBACKs / closes. The checkpointer can only
reclaim WAL frames OLDER than the oldest active reader's mark. A read-only
connection that polls in a loop but never ends its read transaction therefore
pins the WAL floor forever → wal_checkpoint(TRUNCATE) returns BUSY (busy=1) →
the -wal file grows unboundedly with every write.

The fix has two parts:
  1. Each long-lived world-DB READ connection that polls in a loop ends its read
     transaction between cycles (conn.rollback()/commit()) so its WAL read-mark
     advances WITHOUT closing the connection.
  2. A periodic scheduler job runs PRAGMA wal_checkpoint(TRUNCATE) as a backstop.

This test reproduces the disease at the raw-SQLite level (no production import
needed for the mechanism proof) using literal TRUNCATE calls, since TRUNCATE is
the mode where "BUSY while a reader pins the floor" / "shrinks once released"
is easiest to observe directly. Probe 3 then asserts what the production
helper (``checkpoint_world_wal``) actually runs — PASSIVE, not TRUNCATE (W5-3,
2026-07-21: this file previously assumed TRUNCATE; the implementation has
always run PASSIVE, for live-writer-priority reasons — see that function's
docstring): it drains the log fully once no reader pins the floor, but unlike
TRUNCATE it never shrinks the -wal file itself.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


def _wal_size_bytes(db_path: Path) -> int:
    wal = Path(str(db_path) + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def _open_wal(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    # Disable autocheckpoint so the test controls truncation explicitly and the
    # WAL only shrinks when WE call wal_checkpoint — otherwise a background
    # autocheckpoint could mask the starvation we are proving.
    conn.execute("PRAGMA wal_autocheckpoint=0")
    return conn


def _write_many_frames(writer: sqlite3.Connection, n: int = 2000) -> None:
    writer.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, blob TEXT)")
    writer.commit()
    payload = "x" * 400  # ~400B rows so the WAL grows visibly
    for i in range(n):
        writer.execute("INSERT INTO t (blob) VALUES (?)", (payload,))
    writer.commit()


# ---------------------------------------------------------------------------
# Probe 1 (RED-defining): a reader that NEVER releases pins the WAL floor.
# ---------------------------------------------------------------------------

def test_unreleased_reader_starves_checkpoint(tmp_path: Path) -> None:
    """A persistent reader mid-read makes wal_checkpoint(TRUNCATE) return BUSY
    and the -wal file STAYS large. This is the bug."""
    db_path = tmp_path / "zeus-world-bug.db"
    writer = _open_wal(db_path)
    _write_many_frames(writer, n=2000)

    # Persistent reader opens a read transaction and NEVER ends it (the disease).
    # In SQLite a snapshot is pinned only while a read transaction is OPEN. A
    # long-lived production reader holds this open between polls when it never
    # commits/rolls back (sqlite3's isolation_level="" begins a txn on the first
    # statement inside an explicit BEGIN, or when DML precedes the read). We model
    # that with an explicit BEGIN that stays open — the exact floor-pinning state.
    reader = _open_wal(db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM t").fetchone()  # pins snapshot/read-mark

    wal_before = _wal_size_bytes(db_path)
    assert wal_before > 0, "precondition: WAL must have frames to truncate"

    # Attempt a TRUNCATE checkpoint while the reader pins the floor.
    busy, log_frames, ckpt_frames = writer.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()

    # With a reader pinning the floor, TRUNCATE cannot complete: busy=1.
    assert busy == 1, (
        f"expected checkpoint BUSY (1) while reader pins WAL floor, "
        f"got ({busy},{log_frames},{ckpt_frames})"
    )
    # And the WAL is NOT truncated — it stays large.
    wal_after = _wal_size_bytes(db_path)
    assert wal_after >= wal_before * 0.5, (
        f"WAL should remain large under reader starvation; "
        f"before={wal_before} after={wal_after}"
    )

    reader.close()
    writer.close()


# ---------------------------------------------------------------------------
# Probe 2 (GREEN-defining): releasing the snapshot per-cycle + TRUNCATE shrinks
# the WAL to ~zero. This is the fix mechanism.
# ---------------------------------------------------------------------------

def test_released_reader_lets_checkpoint_truncate(tmp_path: Path) -> None:
    """After the persistent reader ENDS its read transaction (the per-cycle
    release), wal_checkpoint(TRUNCATE) succeeds (busy=0) and the -wal file
    truncates to ~zero. The connection is NOT closed — only its snapshot is
    released — proving the fix keeps the connection alive across cycles."""
    db_path = tmp_path / "zeus-world-fix.db"
    writer = _open_wal(db_path)
    _write_many_frames(writer, n=2000)

    reader = _open_wal(db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT COUNT(*) FROM t").fetchone()  # pins snapshot

    wal_before = _wal_size_bytes(db_path)
    assert wal_before > 0

    # THE FIX (part 1): release the read snapshot between cycles WITHOUT closing.
    # A read-only connection's open read transaction is ended by rollback().
    reader.rollback()

    # THE FIX (part 2): TRUNCATE checkpoint backstop now succeeds.
    busy, log_frames, ckpt_frames = writer.execute(
        "PRAGMA wal_checkpoint(TRUNCATE)"
    ).fetchone()
    assert busy == 0, (
        f"after reader release, TRUNCATE must succeed (busy=0), "
        f"got ({busy},{log_frames},{ckpt_frames})"
    )

    wal_after = _wal_size_bytes(db_path)
    assert wal_after < wal_before * 0.1 or wal_after == 0, (
        f"WAL must truncate to ~zero after release+TRUNCATE; "
        f"before={wal_before} after={wal_after}"
    )

    # The reader connection is STILL USABLE (not closed) — proves we only
    # released the snapshot, preserving the connection across cycles.
    again = reader.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    assert again == 2000

    reader.close()
    writer.close()


# ---------------------------------------------------------------------------
# Probe 3: the db.py production helper truncates the world WAL.
# ---------------------------------------------------------------------------

def test_checkpoint_world_wal_helper_drains_without_truncating(tmp_path: Path, monkeypatch) -> None:
    """``checkpoint_world_wal`` runs wal_checkpoint(PASSIVE) on zeus-world.db and
    returns the (busy, log_frames, checkpointed_frames) triple for observability.
    With no competing reader, PASSIVE fully drains the log (checkpointed_frames
    == log_frames) — but unlike TRUNCATE it never shrinks the -wal file itself;
    that is intentional (live-writer priority), not a bug (W5-3)."""
    from src.state import db as db_module

    world_db = tmp_path / "zeus-world.db"
    monkeypatch.setattr(db_module, "ZEUS_WORLD_DB_PATH", world_db, raising=True)

    writer = _open_wal(world_db)
    _write_many_frames(writer, n=2000)
    # Keep `writer` OPEN: closing the last connection makes SQLite checkpoint &
    # truncate the WAL on close, which would confound the "PASSIVE alone does
    # not shrink the file" assertion below. `writer` is idle (autocommit, no
    # open read txn) so it does not pin the floor — the helper's PASSIVE
    # checkpoint can drain the full log.

    wal_before = _wal_size_bytes(world_db)
    assert wal_before > 0

    result = db_module.checkpoint_world_wal()

    assert isinstance(result, tuple) and len(result) == 4, (
        f"checkpoint_world_wal must return a 4-tuple "
        f"(busy, log_frames, checkpointed_frames, page_size), got {result!r}"
    )
    busy, log_frames, ckpt_frames, page_size = result
    assert busy == 0, f"PASSIVE's busy field is always 0: {result!r}"
    assert ckpt_frames == log_frames > 0, (
        f"with no competing reader PASSIVE should drain the full log: {result!r}"
    )
    assert page_size > 0, f"page_size must be reported for byte-sizing: {result!r}"

    wal_after = _wal_size_bytes(world_db)
    assert wal_after == wal_before, (
        f"PASSIVE must NOT truncate the -wal file (only TRUNCATE mode does); "
        f"before={wal_before} after={wal_after}"
    )

    writer.close()


# ---------------------------------------------------------------------------
# Probe 4: the scheduler job runs the checkpoint helper and LOGS its triple.
# ---------------------------------------------------------------------------

def test_world_wal_checkpoint_job_runs_and_logs(monkeypatch, caplog) -> None:
    """``_world_wal_checkpoint_cycle`` invokes ``checkpoint_world_wal`` and logs
    the (busy, log_frames, checkpointed_frames) triple. A starved result (not
    draining AND past the healthy oscillation band — W5-2 fix, 2026-07-21) is
    logged at WARNING (loud, not silent); a healthy result at INFO."""
    import logging

    from src import main as main_module
    from src.state import db as db_module

    # The world checkpoint cycle defers until the first held-position monitor cycle
    # completes (operator guard); no monitor runs in this unit test, so bypass the
    # deferral to exercise the checkpoint path.
    monkeypatch.setattr(
        main_module, "_defer_for_held_position_monitor", lambda _name: False, raising=True
    )

    # Patch the helper at the source module so the job's local import resolves it.
    calls = {"n": 0}

    def _fake_checkpoint() -> tuple[int, int, int, int]:
        calls["n"] += 1
        return (0, 5, 5, 4096)  # PASSIVE busy=0, fully drained -> healthy

    monkeypatch.setattr(db_module, "checkpoint_world_wal", _fake_checkpoint, raising=True)

    with caplog.at_level(logging.INFO):
        main_module._world_wal_checkpoint_cycle()

    assert calls["n"] == 1, "job must invoke checkpoint_world_wal exactly once"
    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert "world WAL checkpoint" in log_text, f"job must log the checkpoint result; got: {log_text!r}"
    assert "busy=0" in log_text and "checkpointed=5" in log_text, (
        f"job must log the observability triple; got: {log_text!r}"
    )

    # Backlog path is logged at WARNING (loud): a large un-checkpointed remainder
    # (all frames pinned, checkpointed=0) whose byte size clears the 512 MiB line.
    # busy=0 because PASSIVE's busy is always 0 — this is what a real floor-pinned
    # PASSIVE result looks like.
    caplog.clear()
    starved_frames = main_module._WAL_STARVATION_BACKLOG_BYTES // 4096 + 1

    def _fake_starved() -> tuple[int, int, int, int]:
        return (0, starved_frames, 0, 4096)

    monkeypatch.setattr(db_module, "checkpoint_world_wal", _fake_starved, raising=True)
    with caplog.at_level(logging.WARNING):
        main_module._world_wal_checkpoint_cycle()
    warn_text = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "BACKLOG" in warn_text and "busy=0" in warn_text, (
        f"a floor-pinned checkpoint must be logged at WARNING; got: {warn_text!r}"
    )

    # busy=1 -> CONTENDED: a concurrent checkpointer holds the exclusive checkpoint
    # lock, so even a frame count that WOULD exceed the backlog threshold is not
    # evaluated as a backlog sample; log CONTENDED, backlog unknown (consult
    # re-review 2026-07-22).
    caplog.clear()

    def _fake_contended() -> tuple[int, int, int, int]:
        return (1, starved_frames, 0, 4096)  # busy=1 but frames would trip BACKLOG

    monkeypatch.setattr(db_module, "checkpoint_world_wal", _fake_contended, raising=True)
    with caplog.at_level(logging.INFO):
        main_module._world_wal_checkpoint_cycle()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "CONTENDED" in text and "busy=1" in text, (
        f"a busy=1 concurrent checkpointer must log CONTENDED; got: {text!r}"
    )
    assert "BACKLOG" not in text, "busy=1 must NOT be evaluated as a backlog sample"
