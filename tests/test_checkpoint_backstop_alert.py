# Created: 2026-07-21
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   findings/connections_throughput.md — FINDING W5-2 (PASSIVE false-green),
#   W5-3 (intended TRUNCATE shipped as PASSIVE), W5-4 (no forecasts backstop).
# Purpose: antibody for the W5-2 false-green defect. PASSIVE checkpoints never
#   return SQLITE_BUSY, so the old `busy == 0` alert gate was always-true dead
#   code (see tests/test_world_wal_checkpoint_starvation.py for the sibling
#   probes on checkpoint_world_wal/_world_wal_checkpoint_cycle themselves).
#   This file tests the extracted pure decision function in isolation
#   (`src.main._wal_checkpoint_is_starved`) plus the new W5-4 forecasts
#   checkpoint backstop, without booting the scheduler.
# Reuse: run on any PR touching the WAL checkpoint alert threshold/logic in
#   src/main.py or the checkpoint_*_wal helpers in src/state/db.py.

"""WAL checkpoint backstop alert-decision + forecasts-backstop tests.

``_wal_checkpoint_is_starved(log_frames, checkpointed_frames)`` is the real
starvation signal for a PASSIVE checkpoint: the WAL is not draining
(``checkpointed_frames < log_frames``) AND the undrained log has grown past
the healthy oscillation band (``log_frames > _WAL_STARVATION_FRAME_THRESHOLD``,
512 MiB / 4 KiB page = 131,072 frames — see the constant's docstring in
src/main.py for the derivation from the finding's live evidence: healthy
oscillation tops out at 373 MB, the 2026-06-16 incident reached 810 MB).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _wal_checkpoint_is_starved: pure decision function, no scheduler/DB needed.
# ---------------------------------------------------------------------------

def test_fires_when_not_draining_past_threshold() -> None:
    """checkpointed < log AND log > threshold -> starved (the real signal)."""
    from src.main import _WAL_STARVATION_FRAME_THRESHOLD, _wal_checkpoint_is_starved

    log_frames = _WAL_STARVATION_FRAME_THRESHOLD + 1
    checkpointed_frames = log_frames - 1
    assert _wal_checkpoint_is_starved(log_frames, checkpointed_frames) is True


def test_quiet_on_full_drain() -> None:
    """checkpointed == log -> quiet, even when the log is huge (fully drained,
    not starved — a large WAL that IS draining is healthy)."""
    from src.main import _WAL_STARVATION_FRAME_THRESHOLD, _wal_checkpoint_is_starved

    log_frames = _WAL_STARVATION_FRAME_THRESHOLD * 10
    assert _wal_checkpoint_is_starved(log_frames, log_frames) is False


def test_quiet_on_small_partial_drain() -> None:
    """checkpointed < log but log stays under the threshold -> quiet (a lone
    partial drain is normal under transient reader pressure; live oscillation
    is 95-373 MB, well under the 512 MiB threshold)."""
    from src.main import _WAL_STARVATION_FRAME_THRESHOLD, _wal_checkpoint_is_starved

    log_frames = _WAL_STARVATION_FRAME_THRESHOLD // 2
    checkpointed_frames = log_frames // 2
    assert checkpointed_frames < log_frames  # precondition: this IS a partial drain
    assert _wal_checkpoint_is_starved(log_frames, checkpointed_frames) is False


def test_quiet_exactly_at_threshold() -> None:
    """log_frames == threshold is not YET past the healthy band (strict `>`),
    so a partial drain sitting exactly at the boundary stays quiet."""
    from src.main import _WAL_STARVATION_FRAME_THRESHOLD, _wal_checkpoint_is_starved

    log_frames = _WAL_STARVATION_FRAME_THRESHOLD
    checkpointed_frames = log_frames - 1
    assert _wal_checkpoint_is_starved(log_frames, checkpointed_frames) is False


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


def test_checkpoint_forecasts_wal_returns_triple_against_tmp_db(tmp_path: Path, monkeypatch) -> None:
    """``checkpoint_forecasts_wal`` runs against a tmp zeus-forecasts.db and
    returns the (busy, log_frames, checkpointed_frames) 3-tuple, mirroring
    checkpoint_world_wal / checkpoint_trades_wal exactly."""
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

    assert isinstance(result, tuple) and len(result) == 3, (
        f"checkpoint_forecasts_wal must return a 3-tuple "
        f"(busy, log_frames, checkpointed_frames), got {result!r}"
    )
    busy, log_frames, ckpt_frames = result
    assert all(isinstance(v, int) for v in result), f"triple must be all ints: {result!r}"
    assert busy == 0, f"PASSIVE's busy field is always 0: {result!r}"
    assert ckpt_frames == log_frames > 0, (
        f"with no competing reader PASSIVE should drain the full log: {result!r}"
    )

    writer.close()
