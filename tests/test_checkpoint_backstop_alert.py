# Created: 2026-07-21
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   findings/connections_throughput.md — FINDING W5-2 (PASSIVE false-green),
#   W5-3 (intended TRUNCATE shipped as PASSIVE), W5-4 (no forecasts backstop),
#   W5-5 (PR review: alert measured TOTAL log, not the un-checkpointed backlog,
#   and hardcoded a 4 KiB page).
# Purpose: antibody for the checkpoint backstop alert decision. PASSIVE
#   checkpoints never return SQLITE_BUSY, so the original `busy == 0` gate was
#   always-true dead code; and the follow-up total-log threshold both false-
#   alerted on a 1-frame shortfall of a large log and false-cleared a small log
#   with a large pinned remainder. This file tests the corrected pure decision
#   function in isolation (`src.main._wal_checkpoint_is_starved`) plus the W5-4
#   forecasts backstop, without booting the scheduler.
# Reuse: run on any PR touching the WAL checkpoint alert threshold/logic in
#   src/main.py or the checkpoint_*_wal helpers in src/state/db.py.

"""WAL checkpoint backstop alert-decision + forecasts-backstop tests.

``_wal_checkpoint_is_starved(log_frames, checkpointed_frames, page_size_bytes)``
fires on the UN-checkpointed backlog — ``(log_frames - checkpointed_frames)``
frames a PASSIVE checkpoint could not move back into the DB because a reader
pins the WAL floor — converted to bytes with the DB's ACTUAL page_size, over a
512 MiB line. It measures the outstanding remainder, not the total log size,
and never assumes a 4 KiB page.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_PAGE = 4096  # live canonical DBs use a 4 KiB page (verified read-only)
_THRESHOLD_FRAMES_AT_4K = (512 * 1024 * 1024) // _PAGE  # 131_072


# ---------------------------------------------------------------------------
# _wal_checkpoint_is_starved: pure decision function, no scheduler/DB needed.
# ---------------------------------------------------------------------------

def test_fires_on_large_outstanding_backlog() -> None:
    """A pinned remainder past 512 MiB -> starved (the real signal)."""
    from src.main import _wal_checkpoint_is_starved

    log_frames = _THRESHOLD_FRAMES_AT_4K + 1  # 1 frame past the line, all pinned
    assert _wal_checkpoint_is_starved(log_frames, 0, _PAGE) is True


def test_quiet_on_full_drain_however_large_the_log() -> None:
    """checkpointed == log -> quiet even for a multi-GB WAL: a large log that
    fully drains is healthy, not starved."""
    from src.main import _wal_checkpoint_is_starved

    log_frames = _THRESHOLD_FRAMES_AT_4K * 20  # ~10 GiB WAL
    assert _wal_checkpoint_is_starved(log_frames, log_frames, _PAGE) is False


def test_quiet_on_tiny_shortfall_of_huge_log_regression() -> None:
    """W5-5 regression: the prior predicate (checkpointed<log AND log>131072)
    warned when a huge log missed full drain by a single frame. The outstanding
    backlog is 1 frame (~4 KiB), nowhere near 512 MiB, so this must be QUIET."""
    from src.main import _wal_checkpoint_is_starved

    log_frames = _THRESHOLD_FRAMES_AT_4K * 20
    checkpointed = log_frames - 1
    assert _wal_checkpoint_is_starved(log_frames, checkpointed, _PAGE) is False


def test_quiet_on_small_partial_drain() -> None:
    """A modest pinned remainder (well under 512 MiB) is normal transient reader
    pressure, not starvation."""
    from src.main import _wal_checkpoint_is_starved

    # 1000 frames outstanding ~= 4 MiB at 4 KiB pages.
    assert _wal_checkpoint_is_starved(50_000, 49_000, _PAGE) is False


def test_quiet_exactly_at_threshold() -> None:
    """Outstanding bytes exactly at 512 MiB is not YET past the line (strict >)."""
    from src.main import _wal_checkpoint_is_starved

    assert _wal_checkpoint_is_starved(_THRESHOLD_FRAMES_AT_4K, 0, _PAGE) is False


def test_page_size_is_not_hardcoded() -> None:
    """W5-5: the same FRAME backlog crosses the byte line at a larger page_size.
    100k outstanding frames = 400 MiB at 4 KiB (quiet) but 800 MiB at 8 KiB
    (starved) — proving the decision reads the actual page_size."""
    from src.main import _wal_checkpoint_is_starved

    assert _wal_checkpoint_is_starved(100_000, 0, 4096) is False
    assert _wal_checkpoint_is_starved(100_000, 0, 8192) is True


def test_quiet_on_failed_checkpoint_sentinel() -> None:
    """The helpers return -1 frames / non-positive page_size when the checkpoint
    could not report; the decision must not alert on that (the caller's own log
    line covers a failed checkpoint)."""
    from src.main import _wal_checkpoint_is_starved

    assert _wal_checkpoint_is_starved(-1, -1, -1) is False
    assert _wal_checkpoint_is_starved(1000, 0, 0) is False


# ---------------------------------------------------------------------------
# checkpoint_forecasts_wal: W5-4, the forecasts twin of checkpoint_world_wal /
# checkpoint_trades_wal.
# ---------------------------------------------------------------------------

def _wal_size_bytes(db_path: Path) -> int:
    wal = Path(str(db_path) + "-wal")
    return wal.stat().st_size if wal.exists() else 0


def _open_wal(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    return conn


def test_checkpoint_forecasts_wal_returns_quad_against_tmp_db(tmp_path: Path, monkeypatch) -> None:
    """``checkpoint_forecasts_wal`` runs against a tmp zeus-forecasts.db and
    returns the (busy, log_frames, checkpointed_frames, page_size) 4-tuple,
    mirroring checkpoint_world_wal / checkpoint_trades_wal exactly."""
    from src.state import db as db_module

    forecasts_db = tmp_path / "zeus-forecasts.db"
    monkeypatch.setattr(db_module, "ZEUS_FORECASTS_DB_PATH", forecasts_db, raising=True)

    writer = _open_wal(forecasts_db)
    writer.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, blob TEXT)")
    writer.commit()
    payload = "x" * 400
    for _ in range(500):
        writer.execute("INSERT INTO t (blob) VALUES (?)", (payload,))
    writer.commit()
    # Keep `writer` open and idle (autocommit, no open read txn): it does not
    # pin the WAL floor, but it does keep this from being the LAST connection
    # (whose close would auto-truncate the WAL and mask what the helper did).

    wal_before = _wal_size_bytes(forecasts_db)
    assert wal_before > 0, "precondition: WAL must have frames to checkpoint"

    result = db_module.checkpoint_forecasts_wal()

    assert isinstance(result, tuple) and len(result) == 4, (
        f"checkpoint_forecasts_wal must return a 4-tuple "
        f"(busy, log_frames, checkpointed_frames, page_size), got {result!r}"
    )
    busy, log_frames, ckpt_frames, page_size = result
    assert all(isinstance(v, int) for v in result), f"quad must be all ints: {result!r}"
    assert busy == 0, f"PASSIVE's busy field is always 0: {result!r}"
    assert ckpt_frames == log_frames > 0, (
        f"with no competing reader PASSIVE should drain the full log: {result!r}"
    )
    assert page_size > 0, f"page_size must be reported for byte-sizing: {result!r}"

    writer.close()
