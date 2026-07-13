# Created: 2026-07-13
# Last reused or audited: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md (LX-0R
#   "契约+激活控制"); mirrors src.state.db's schema_epoch T5 pattern.

"""Tests for src.state.truth_epoch — trade-DB truth-epoch machinery (LX-0R).

Covers: LEGACY default on a fresh table, the monotonic forward-only transition
guard (no backward, no no-op repeat, no skip), and the process-capability
check helper. All inert plumbing — no live seam calls any of this yet.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.contracts.economics_ownership import TruthEpoch
from src.state.truth_epoch import (
    ProcessEpochCapability,
    TruthEpochTransitionError,
    capability_admits_epoch,
    current_build_capability,
    ensure_truth_epoch_table,
    read_truth_epoch,
    transition_truth_epoch,
)


def _make_conn() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


# --------------------------------------------------------------------------- #
# Default state                                                               #
# --------------------------------------------------------------------------- #

def test_read_truth_epoch_is_legacy_on_table_absent() -> None:
    conn = _make_conn()
    assert read_truth_epoch(conn) is TruthEpoch.LEGACY


def test_ensure_truth_epoch_table_seeds_legacy_default() -> None:
    conn = _make_conn()
    ensure_truth_epoch_table(conn)
    assert read_truth_epoch(conn) is TruthEpoch.LEGACY


def test_ensure_truth_epoch_table_is_idempotent() -> None:
    conn = _make_conn()
    ensure_truth_epoch_table(conn)
    ensure_truth_epoch_table(conn)  # second call must not raise or reset
    assert read_truth_epoch(conn) is TruthEpoch.LEGACY
    rows = conn.execute("SELECT COUNT(*) FROM truth_epoch").fetchone()[0]
    assert rows == 1


def test_ensure_truth_epoch_table_does_not_clobber_an_already_transitioned_epoch() -> None:
    conn = _make_conn()
    ensure_truth_epoch_table(conn)
    transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test")
    ensure_truth_epoch_table(conn)  # boot-path re-call must not reset to LEGACY
    assert read_truth_epoch(conn) is TruthEpoch.PREPARE


# --------------------------------------------------------------------------- #
# Monotonic transition guard                                                  #
# --------------------------------------------------------------------------- #

def test_transition_legacy_to_prepare_succeeds() -> None:
    conn = _make_conn()
    result = transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")
    assert result is TruthEpoch.PREPARE
    assert read_truth_epoch(conn) is TruthEpoch.PREPARE


def test_transition_prepare_to_active_new_succeeds() -> None:
    conn = _make_conn()
    transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")
    result = transition_truth_epoch(conn, to=TruthEpoch.ACTIVE_NEW, actor="test-actor")
    assert result is TruthEpoch.ACTIVE_NEW
    assert read_truth_epoch(conn) is TruthEpoch.ACTIVE_NEW


def test_transition_refuses_skip_legacy_to_active_new() -> None:
    conn = _make_conn()
    with pytest.raises(TruthEpochTransitionError, match="SKIP_REFUSED"):
        transition_truth_epoch(conn, to=TruthEpoch.ACTIVE_NEW, actor="test-actor")
    assert read_truth_epoch(conn) is TruthEpoch.LEGACY


def test_transition_refuses_backward_from_prepare_to_legacy() -> None:
    conn = _make_conn()
    transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")
    with pytest.raises(TruthEpochTransitionError, match="BACKWARD_OR_NOOP_REFUSED"):
        transition_truth_epoch(conn, to=TruthEpoch.LEGACY, actor="test-actor")
    assert read_truth_epoch(conn) is TruthEpoch.PREPARE


def test_transition_refuses_backward_from_active_new_to_prepare() -> None:
    conn = _make_conn()
    transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")
    transition_truth_epoch(conn, to=TruthEpoch.ACTIVE_NEW, actor="test-actor")
    with pytest.raises(TruthEpochTransitionError, match="BACKWARD_OR_NOOP_REFUSED"):
        transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")
    assert read_truth_epoch(conn) is TruthEpoch.ACTIVE_NEW


def test_transition_refuses_noop_repeat() -> None:
    conn = _make_conn()
    transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")
    with pytest.raises(TruthEpochTransitionError, match="BACKWARD_OR_NOOP_REFUSED"):
        transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="test-actor")


def test_transition_records_actor_and_timestamp() -> None:
    conn = _make_conn()
    transition_truth_epoch(conn, to=TruthEpoch.PREPARE, actor="operator:fitz")
    row = conn.execute(
        "SELECT transitioned_by, transitioned_at FROM truth_epoch WHERE id = 1"
    ).fetchone()
    assert row[0] == "operator:fitz"
    assert row[1]  # non-empty ISO timestamp


# --------------------------------------------------------------------------- #
# Process-capability check helper                                            #
# --------------------------------------------------------------------------- #

def test_current_build_capability_supports_all_three_epochs() -> None:
    cap = current_build_capability()
    assert cap.supports(TruthEpoch.LEGACY)
    assert cap.supports(TruthEpoch.PREPARE)
    assert cap.supports(TruthEpoch.ACTIVE_NEW)


def test_capability_admits_epoch_true_when_supported() -> None:
    cap = ProcessEpochCapability(supported_epochs=frozenset({TruthEpoch.LEGACY, TruthEpoch.PREPARE}))
    assert capability_admits_epoch(cap, TruthEpoch.LEGACY)
    assert capability_admits_epoch(cap, TruthEpoch.PREPARE)


def test_capability_admits_epoch_false_when_unsupported() -> None:
    cap = ProcessEpochCapability(supported_epochs=frozenset({TruthEpoch.LEGACY}))
    assert not capability_admits_epoch(cap, TruthEpoch.ACTIVE_NEW)


def test_capability_admits_epoch_refuses_stale_build_under_active_new() -> None:
    """A build that has not advertised ACTIVE_NEW support must be refused —
    this is the LX-3R 'stale-reader / rolling-deploy' attack the round-2 delta
    named (attack E: an old daemon must never get a lease under the new
    epoch)."""
    stale_build = ProcessEpochCapability(supported_epochs=frozenset({TruthEpoch.LEGACY, TruthEpoch.PREPARE}))
    assert not capability_admits_epoch(stale_build, TruthEpoch.ACTIVE_NEW)
