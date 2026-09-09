# Created: 2026-07-13
# Last reused or audited: 2026-09-09
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md LX-T1
#   (GATED verdict).
# Reuse: Run when modifying src/ingest/payout_observer.py, the payout
#   selectors, or src/state/schema/payout_observations_schema.py.
"""Antibody tests for the read-only ConditionalTokens payout observer (LX-T1).

Covers: 4-state classification (resolved/unresolved/timeout/garbage/partial),
supersession-on-change, append-only enforcement (immutability + no-delete),
the condition sweep source (position_current UNION settlement_commands), and
a no-signing-capability antibody (this module must never import a wallet
key / signer / py_clob_client_v2 / web3 / PolymarketV2Adapter).
"""

from __future__ import annotations

import ast
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

import src.ingest.payout_observer as payout_observer
from src.ingest.payout_observer import (
    FINALIZED_SOURCE,
    LEGACY_FINALITY_UPGRADE_BATCH_SIZE,
    PAYOUT_DENOMINATOR_SELECTOR,
    PAYOUT_NUMERATORS_SELECTOR,
    STATE_RESOLVED_NONZERO,
    STATE_RESOLVED_ZERO,
    STATE_UNKNOWN,
    STATE_UNRESOLVED,
    append_observation,
    classify_payout,
    conditions_to_observe,
    forecast_conditions_to_observe,
    _learning_sweep,
    payout_observer_cycle,
    read_condition_payout,
    sweep_and_record,
)
from src.state.schema.payout_observations_schema import ensure_table

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _REPO_ROOT / "src" / "ingest" / "payout_observer.py"

_CONDITION_A = "0x" + "ab" * 32
_CONDITION_B = "0x" + "cd" * 32
_BLOCK_HASH = "0x" + "11" * 32


def _uint(value: int) -> str:
    return "0x" + format(value, "064x")


def _build_stub_rpc(
    *,
    denominator,
    numerators: dict[int, int] | None = None,
    block_number: int = 100,
    block_hash: str = _BLOCK_HASH,
    fail_block: bool = False,
    fail_denominator: bool = False,
    garbage_denominator: bool = False,
    fail_numerator_indices: set[int] | None = None,
    garbage_numerator_indices: set[int] | None = None,
):
    """A stub rpc_call answering eth_getBlockByNumber / eth_call by selector.

    Mirrors tests/test_polymarket_v2_adapter_balance_probe.py's
    _build_stub_rpc style (selector-dispatch inspection of eth_call data).
    """
    numerators = numerators or {}
    fail_numerator_indices = fail_numerator_indices or set()
    garbage_numerator_indices = garbage_numerator_indices or set()
    calls: list[tuple[str, str]] = []

    def _rpc(url, method, params):
        if method == "eth_getBlockByNumber":
            calls.append((method, ""))
            if fail_block:
                raise TimeoutError("rpc timeout on eth_getBlockByNumber")
            assert params == ["finalized", False]
            return {"number": hex(block_number), "hash": block_hash}
        assert method == "eth_call"
        data = params[0]["data"]
        selector = data[:10]
        calls.append((method, selector))
        if selector == PAYOUT_DENOMINATOR_SELECTOR:
            if fail_denominator:
                raise TimeoutError("rpc timeout on payoutDenominator")
            if garbage_denominator:
                return "not-hex-at-all"
            return _uint(denominator)
        if selector == PAYOUT_NUMERATORS_SELECTOR:
            idx = int(data[-64:], 16)
            if idx in fail_numerator_indices:
                raise TimeoutError(f"rpc timeout on payoutNumerators[{idx}]")
            if idx in garbage_numerator_indices:
                return "0x"  # empty result — must NOT decode as 0
            return _uint(numerators[idx])
        raise AssertionError(f"unexpected selector {selector}")

    return _rpc, calls


# ---------------------------------------------------------------------------
# Selector canonicality (antibody, mirrors
# test_polymarket_v2_adapter_balance_probe.py::test_selectors_are_canonical)
# ---------------------------------------------------------------------------


def test_selectors_are_canonical():
    from eth_utils import keccak

    assert PAYOUT_DENOMINATOR_SELECTOR == "0x" + keccak(text="payoutDenominator(bytes32)")[:4].hex()
    assert PAYOUT_NUMERATORS_SELECTOR == "0x" + keccak(
        text="payoutNumerators(bytes32,uint256)"
    )[:4].hex()


# ---------------------------------------------------------------------------
# classify_payout — pure function, all 4 states
# ---------------------------------------------------------------------------


class TestClassifyPayout:
    def test_resolved_nonzero(self):
        assert classify_payout(100, 100) == STATE_RESOLVED_NONZERO

    def test_resolved_zero(self):
        assert classify_payout(100, 0) == STATE_RESOLVED_ZERO

    def test_unresolved(self):
        assert classify_payout(0, None) == STATE_UNRESOLVED
        # Even if a numerator value somehow arrived, denominator==0 is
        # authoritative for UNRESOLVED (see classify_payout docstring).
        assert classify_payout(0, 0) == STATE_UNRESOLVED

    def test_unknown_on_missing_denominator(self):
        assert classify_payout(None, None) == STATE_UNKNOWN
        assert classify_payout(None, 5) == STATE_UNKNOWN

    def test_unknown_on_missing_numerator_when_resolved(self):
        # denominator confirms resolved, but numerator read failed.
        assert classify_payout(100, None) == STATE_UNKNOWN


# ---------------------------------------------------------------------------
# read_condition_payout — end-to-end classification via stub RPC
# ---------------------------------------------------------------------------


class TestReadConditionPayout:
    def test_resolved_binary_market(self):
        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        by_idx = {r["outcome_index"]: r for r in results}
        assert by_idx[0]["state"] == STATE_RESOLVED_NONZERO
        assert by_idx[0]["payout_numerator"] == 100
        assert by_idx[0]["payout_denominator"] == 100
        assert by_idx[0]["block_number"] == 100
        assert by_idx[0]["block_hash"] == _BLOCK_HASH
        assert by_idx[1]["state"] == STATE_RESOLVED_ZERO
        assert by_idx[1]["payout_numerator"] == 0

    def test_unresolved_market_never_queries_numerators(self):
        rpc, calls = _build_stub_rpc(denominator=0, numerators={})
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        assert all(r["state"] == STATE_UNRESOLVED for r in results)
        assert all(r["payout_numerator"] is None for r in results)
        # payoutNumerators must never be called for a confirmed-unresolved
        # condition (it would revert on-chain; classification doesn't need it).
        assert not any(sel == PAYOUT_NUMERATORS_SELECTOR for _, sel in calls)

    def test_block_marker_timeout_yields_unknown_never_zero(self):
        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0}, fail_block=True)
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        assert all(r["state"] == STATE_UNKNOWN for r in results)
        assert all(r["payout_numerator"] is None and r["payout_denominator"] is None for r in results)
        assert all(r["block_number"] is None for r in results)

    def test_denominator_timeout_yields_unknown_never_zero(self):
        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0}, fail_denominator=True)
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        assert all(r["state"] == STATE_UNKNOWN for r in results)
        # Block WAS pinned successfully — only the payout read failed.
        assert all(r["block_number"] == 100 for r in results)

    def test_garbage_denominator_yields_unknown_never_zero(self):
        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0}, garbage_denominator=True)
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        assert all(r["state"] == STATE_UNKNOWN for r in results)

    def test_partial_numerator_failure_isolated_to_one_outcome_index(self):
        rpc, _ = _build_stub_rpc(
            denominator=100, numerators={1: 0}, fail_numerator_indices={0},
        )
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        by_idx = {r["outcome_index"]: r for r in results}
        assert by_idx[0]["state"] == STATE_UNKNOWN
        assert by_idx[0]["payout_numerator"] is None
        assert by_idx[1]["state"] == STATE_RESOLVED_ZERO

    def test_empty_numerator_response_is_unknown_not_zero(self):
        rpc, _ = _build_stub_rpc(
            denominator=100, numerators={1: 100}, garbage_numerator_indices={0},
        )
        results = read_condition_payout(_CONDITION_A, rpc_url="https://rpc.example", rpc_call=rpc)
        by_idx = {r["outcome_index"]: r for r in results}
        assert by_idx[0]["state"] == STATE_UNKNOWN
        assert by_idx[0]["payout_numerator"] is None

    def test_invalid_condition_id_yields_unknown(self):
        rpc, calls = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        results = read_condition_payout("not-a-condition-id", rpc_url="https://rpc.example", rpc_call=rpc)
        assert all(r["state"] == STATE_UNKNOWN for r in results)
        # Never even attempted an RPC call for a malformed condition_id.
        assert calls == []


# ---------------------------------------------------------------------------
# append_observation — supersession-on-change + append-only enforcement
# ---------------------------------------------------------------------------


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    ensure_table(c)
    yield c
    c.close()


class TestAppendObservation:
    def test_first_observation_inserts(self, conn):
        new_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=1, block_hash="0xaa", observed_at="2026-07-13T00:00:00+00:00",
        )
        assert new_id is not None
        row = conn.execute("SELECT superseded_by FROM payout_observations WHERE id=?", (new_id,)).fetchone()
        assert row[0] is None

    def test_unchanged_observation_is_a_noop(self, conn):
        first_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=1, block_hash="0xaa", observed_at="2026-07-13T00:00:00+00:00",
        )
        second_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=2, block_hash="0xbb", observed_at="2026-07-13T00:10:00+00:00",
        )
        assert second_id is None
        count = conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0]
        assert count == 1
        # The one row on disk is still the FIRST observation, untouched.
        row = conn.execute("SELECT id FROM payout_observations").fetchone()
        assert row[0] == first_id

    def test_changed_observation_supersedes_prior(self, conn):
        first_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=1, block_hash="0xaa", observed_at="2026-07-13T00:00:00+00:00",
        )
        second_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=50, payout_denominator=100, state=STATE_RESOLVED_NONZERO,
            block_number=2, block_hash="0xbb", observed_at="2026-07-13T00:10:00+00:00",
        )
        assert second_id is not None
        assert second_id != first_id
        prior_row = conn.execute(
            "SELECT superseded_by, state FROM payout_observations WHERE id=?", (first_id,)
        ).fetchone()
        assert prior_row[0] == second_id
        assert prior_row[1] == STATE_UNRESOLVED  # the OLD row's own state is never edited
        new_row = conn.execute(
            "SELECT superseded_by, state FROM payout_observations WHERE id=?", (second_id,)
        ).fetchone()
        assert new_row[0] is None
        assert new_row[1] == STATE_RESOLVED_NONZERO

    def test_distinct_outcome_indices_are_independent_chains(self, conn):
        append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=100, payout_denominator=100, state=STATE_RESOLVED_NONZERO,
            block_number=1, block_hash="0xaa", observed_at="t0",
        )
        append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=1,
            payout_numerator=0, payout_denominator=100, state=STATE_RESOLVED_ZERO,
            block_number=1, block_hash="0xaa", observed_at="t0",
        )
        rows = conn.execute(
            "SELECT outcome_index, superseded_by FROM payout_observations WHERE condition_id=?",
            (_CONDITION_A,),
        ).fetchall()
        assert len(rows) == 2
        assert all(r[1] is None for r in rows)

    def test_rejects_invalid_state(self, conn):
        with pytest.raises(ValueError):
            append_observation(
                conn, condition_id=_CONDITION_A, outcome_index=0,
                payout_numerator=None, payout_denominator=None, state="MADE_UP_STATE",
                block_number=1, block_hash="0xaa", observed_at="t0",
            )

    def test_append_only_no_delete(self, conn):
        row_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=1, block_hash="0xaa", observed_at="t0",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM payout_observations WHERE id=?", (row_id,))

    def test_append_only_no_edit_of_substantive_columns(self, conn):
        row_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=1, block_hash="0xaa", observed_at="t0",
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE payout_observations SET state='RESOLVED_ZERO' WHERE id=?", (row_id,)
            )

    def test_superseded_by_can_only_transition_once(self, conn):
        first_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=None, payout_denominator=0, state=STATE_UNRESOLVED,
            block_number=1, block_hash="0xaa", observed_at="t0",
        )
        second_id = append_observation(
            conn, condition_id=_CONDITION_A, outcome_index=0,
            payout_numerator=100, payout_denominator=100, state=STATE_RESOLVED_NONZERO,
            block_number=2, block_hash="0xbb", observed_at="t1",
        )
        # Try to re-point the already-superseded row at a different target.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE payout_observations SET superseded_by=? WHERE id=?", (999, first_id)
            )


# ---------------------------------------------------------------------------
# CHECK constraint — UNKNOWN-requires-incomplete-tuple (wave-1.5 tightening)
# ---------------------------------------------------------------------------


class TestUnknownRequiresIncompleteTuple:
    def test_unknown_with_partial_numerator_accepted(self, conn):
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, observed_at)
            VALUES (?, 0, 1, NULL, 'UNKNOWN', 't0')
            """,
            (_CONDITION_A,),
        )

    def test_unknown_with_partial_denominator_accepted(self, conn):
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, observed_at)
            VALUES (?, 0, NULL, 100, 'UNKNOWN', 't0')
            """,
            (_CONDITION_A,),
        )

    def test_unknown_with_both_values_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO payout_observations
                    (condition_id, outcome_index, payout_numerator, payout_denominator,
                     state, observed_at)
                VALUES (?, 0, 50, 100, 'UNKNOWN', 't0')
                """,
                (_CONDITION_A,),
            )

    def test_unknown_with_null_null_still_accepted(self, conn):
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, observed_at)
            VALUES (?, 0, NULL, NULL, 'UNKNOWN', 't0')
            """,
            (_CONDITION_A,),
        )
        count = conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0]
        assert count == 1


class TestEnsureTableUpgradesStaleCheck:
    """ensure_table must safely upgrade a table created under the OLD
    (pre-tightening) CHECK — provably-empty via DROP+CREATE, non-empty via
    the guarded rebuild-copy idiom."""

    @staticmethod
    def _create_legacy_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE payout_observations (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_id        TEXT NOT NULL,
                outcome_index       INTEGER NOT NULL,
                payout_numerator    INTEGER,
                payout_denominator  INTEGER,
                state               TEXT NOT NULL CHECK (state IN (
                    'UNKNOWN', 'UNRESOLVED', 'RESOLVED_ZERO', 'RESOLVED_NONZERO'
                )),
                block_number        INTEGER,
                block_hash          TEXT,
                observed_at         TEXT NOT NULL,
                source              TEXT NOT NULL DEFAULT 'chain_rpc',
                superseded_by       INTEGER REFERENCES payout_observations(id),
                CHECK (
                    (state = 'UNKNOWN')
                    OR (state = 'UNRESOLVED' AND payout_denominator = 0)
                    OR (
                        state IN ('RESOLVED_ZERO', 'RESOLVED_NONZERO')
                        AND payout_denominator IS NOT NULL AND payout_denominator > 0
                        AND payout_numerator IS NOT NULL
                        AND (
                            (state = 'RESOLVED_ZERO' AND payout_numerator = 0)
                            OR (state = 'RESOLVED_NONZERO' AND payout_numerator > 0)
                        )
                    )
                )
            )
            """
        )

    def test_upgrades_provably_empty_legacy_table(self):
        conn = sqlite3.connect(":memory:")
        self._create_legacy_table(conn)
        ensure_table(conn)
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, observed_at)
            VALUES (?, 0, 1, NULL, 'UNKNOWN', 't0')
            """,
            (_CONDITION_A,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO payout_observations
                    (condition_id, outcome_index, payout_numerator, payout_denominator,
                     state, observed_at)
                VALUES (?, 1, 1, 1, 'UNKNOWN', 't1')
                """,
                (_CONDITION_A,),
            )
        conn.close()

    def test_upgrades_legacy_table_preserving_existing_rows(self):
        conn = sqlite3.connect(":memory:")
        self._create_legacy_table(conn)
        # Seed a legacy row under the OLD (looser) CHECK — a valid row that
        # must survive the rebuild untouched.
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, block_number, block_hash, observed_at, source)
            VALUES (?, 0, 100, 100, 'RESOLVED_NONZERO', 5, '0xaa', 't0', 'chain_rpc')
            """,
            (_CONDITION_A,),
        )
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, block_number, block_hash, observed_at, source)
            VALUES (?, 1, NULL, 1, 'UNKNOWN', 5, '0xaa', 't0', 'chain_rpc')
            """,
            (_CONDITION_A,),
        )
        conn.commit()
        ensure_table(conn)
        rows = conn.execute(
            "SELECT condition_id, outcome_index, payout_numerator, payout_denominator, "
            "state, block_number, block_hash, observed_at, source, superseded_by "
            "FROM payout_observations ORDER BY outcome_index"
        ).fetchall()
        assert rows == [
            (_CONDITION_A, 0, 100, 100, "RESOLVED_NONZERO", 5, "0xaa", "t0", "chain_rpc", None),
            (_CONDITION_A, 1, None, 1, "UNKNOWN", 5, "0xaa", "t0", "chain_rpc", None),
        ]
        # The tightened CHECK rejects complete UNKNOWN tuples going forward.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO payout_observations
                    (condition_id, outcome_index, payout_numerator, payout_denominator,
                     state, observed_at)
                VALUES (?, 1, 1, 1, 'UNKNOWN', 't1')
                """,
                (_CONDITION_A,),
            )
        conn.close()

    def test_complete_unknown_aborts_rebuild_without_mutating_legacy_table(self):
        conn = sqlite3.connect(":memory:")
        self._create_legacy_table(conn)
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, observed_at)
            VALUES (?, 0, 1, 1, 'UNKNOWN', 't0')
            """,
            (_CONDITION_A,),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            ensure_table(conn)

        row = conn.execute(
            "SELECT payout_numerator, payout_denominator, state "
            "FROM payout_observations"
        ).fetchone()
        assert row == (1, 1, "UNKNOWN")
        conn.close()

    @pytest.mark.parametrize("legacy_alter", [False, True])
    def test_registered_invalid_ghost_view_does_not_block_rebuild(self, legacy_alter):
        conn = sqlite3.connect(":memory:")
        self._create_legacy_table(conn)
        conn.execute(
            """
            INSERT INTO payout_observations
                (condition_id, outcome_index, payout_numerator, payout_denominator,
                 state, observed_at)
            VALUES (?, 1, NULL, 1, 'UNKNOWN', 't0')
            """,
            (_CONDITION_A,),
        )
        conn.execute(
            "CREATE VIEW observation_instants_current AS "
            "SELECT * FROM observation_instants"
        )
        conn.execute(
            f"PRAGMA legacy_alter_table = {'ON' if legacy_alter else 'OFF'}"
        )

        ensure_table(conn)

        row = conn.execute(
            "SELECT payout_numerator, payout_denominator, state "
            "FROM payout_observations"
        ).fetchone()
        assert row == (None, 1, "UNKNOWN")
        assert bool(conn.execute("PRAGMA legacy_alter_table").fetchone()[0]) is legacy_alter
        assert conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='view' AND name='observation_instants_current'"
        ).fetchone() == (1,)
        conn.close()

    def test_already_tightened_table_is_a_noop(self, conn):
        # `conn` fixture already ran ensure_table once (fresh, already
        # tightened) — a second call must not raise or rebuild again.
        ensure_table(conn)
        count = conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# conditions_to_observe — sweep source (position_current UNION settlement_commands)
# ---------------------------------------------------------------------------


class TestConditionsToObserve:
    def test_union_dedupe_across_both_tables(self, conn):
        conn.execute(
            "CREATE TABLE position_current (position_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p1', ?)", (_CONDITION_A,)
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p2', NULL)"
        )
        conn.execute(
            "INSERT INTO settlement_commands VALUES ('c1', ?)", (_CONDITION_A,)
        )
        conn.execute(
            "INSERT INTO settlement_commands VALUES ('c2', ?)", (_CONDITION_B,)
        )
        result = conditions_to_observe(conn)
        assert sorted(result) == sorted({_CONDITION_A, _CONDITION_B})

    def test_skips_terminal_history_but_keeps_current_money_risk(self, conn):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p1', ?, 'settled')", (_CONDITION_A,)
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p2', ?, 'active')", (_CONDITION_B,)
        )
        for condition_id in (_CONDITION_A, _CONDITION_B):
            append_observation(
                conn,
                condition_id=condition_id,
                outcome_index=0,
                payout_numerator=100,
                payout_denominator=100,
                state=STATE_RESOLVED_NONZERO,
                block_number=1,
                block_hash="0xaa",
                observed_at="t0",
            )
            append_observation(
                conn,
                condition_id=condition_id,
                outcome_index=1,
                payout_numerator=0,
                payout_denominator=100,
                state=STATE_RESOLVED_ZERO,
                block_number=1,
                block_hash="0xaa",
                observed_at="t0",
            )

        assert conditions_to_observe(conn) == [_CONDITION_B]

    def test_terminal_pruning_uses_latest_row_not_supersession_pointer(self, conn):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p1', ?, 'settled')", (_CONDITION_A,)
        )
        for outcome_index, state, numerator in (
            (0, STATE_RESOLVED_NONZERO, 100),
            (1, STATE_RESOLVED_ZERO, 0),
        ):
            append_observation(
                conn,
                condition_id=_CONDITION_A,
                outcome_index=outcome_index,
                payout_numerator=numerator,
                payout_denominator=100,
                state=state,
                block_number=1,
                block_hash="0xaa",
                observed_at="t0",
                source="chain_rpc",
            )
        # Simulate legacy pointer drift: the newest outcome-1 row is unresolved
        # but an older resolved row still has superseded_by=NULL. Selection must
        # follow the schema owner's ORDER BY id DESC contract.
        conn.execute(
            "INSERT INTO payout_observations ("
            "condition_id, outcome_index, payout_numerator, payout_denominator, state, "
            "block_number, block_hash, observed_at, source) "
            "VALUES (?, 1, NULL, 0, ?, 2, '0xbb', 't1', ?)",
            (_CONDITION_A, STATE_UNRESOLVED, FINALIZED_SOURCE),
        )

        assert conditions_to_observe(conn) == [_CONDITION_A]

    def test_legacy_terminal_rows_are_upgraded_before_pruning(self, conn):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p1', ?, 'settled')", (_CONDITION_A,)
        )
        for outcome_index, state, numerator in (
            (0, STATE_RESOLVED_NONZERO, 100),
            (1, STATE_RESOLVED_ZERO, 0),
        ):
            append_observation(
                conn,
                condition_id=_CONDITION_A,
                outcome_index=outcome_index,
                payout_numerator=numerator,
                payout_denominator=100,
                state=state,
                block_number=1,
                block_hash="0xaa",
                observed_at="t0",
                source="chain_rpc",
            )

        assert conditions_to_observe(conn) == [_CONDITION_A]
        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        result = sweep_and_record(
            conn, rpc_url="https://rpc.example", rpc_call=rpc, now="t1"
        )
        assert result == {"conditions": 1, "appended": 2, "unchanged": 0}
        assert conditions_to_observe(conn) == []
        sources = {
            row[0]
            for row in conn.execute(
                "SELECT source FROM payout_observations WHERE superseded_by IS NULL"
            )
        }
        assert sources == {FINALIZED_SOURCE}

    def test_legacy_finality_upgrade_batch_is_bounded(self, conn):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        total = LEGACY_FINALITY_UPGRADE_BATCH_SIZE + 3
        for index in range(total):
            condition = f"0x{index + 1:064x}"
            conn.execute(
                "INSERT INTO position_current VALUES (?, ?, 'settled')",
                (f"p{index}", condition),
            )
            for outcome_index, state, numerator in (
                (0, STATE_RESOLVED_NONZERO, 100),
                (1, STATE_RESOLVED_ZERO, 0),
            ):
                append_observation(
                    conn,
                    condition_id=condition,
                    outcome_index=outcome_index,
                    payout_numerator=numerator,
                    payout_denominator=100,
                    state=state,
                    block_number=1,
                    block_hash="0xaa",
                    observed_at="t0",
                    source="chain_rpc",
                )

        first_batch = set(conditions_to_observe(conn))
        assert len(first_batch) == LEGACY_FINALITY_UPGRADE_BATCH_SIZE

        rpc, _ = _build_stub_rpc(denominator=100, fail_denominator=True)
        result = sweep_and_record(
            conn, rpc_url="https://rpc.example", rpc_call=rpc, now="t1"
        )
        assert result == {
            "conditions": LEGACY_FINALITY_UPGRADE_BATCH_SIZE,
            "appended": LEGACY_FINALITY_UPGRADE_BATCH_SIZE * 2,
            "unchanged": 0,
        }
        second_batch = set(conditions_to_observe(conn))
        assert len(second_batch) == LEGACY_FINALITY_UPGRADE_BATCH_SIZE
        assert len(second_batch - first_batch) == 3

    def test_unknown_does_not_hide_prior_finalized_unresolved_fact(self, conn):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )

        conditions = [_CONDITION_A] + [
            f"0x{index + 1:064x}" for index in range(LEGACY_FINALITY_UPGRADE_BATCH_SIZE)
        ]
        for index, condition in enumerate(conditions):
            conn.execute(
                "INSERT INTO position_current VALUES (?, ?, 'settled')",
                (f"p{index}", condition),
            )
            for outcome_index, state, numerator in (
                (0, STATE_RESOLVED_NONZERO, 100),
                (1, STATE_RESOLVED_ZERO, 0),
            ):
                append_observation(
                    conn,
                    condition_id=condition,
                    outcome_index=outcome_index,
                    payout_numerator=numerator,
                    payout_denominator=100,
                    state=state,
                    block_number=1,
                    block_hash="0xaa",
                    observed_at="t0",
                    source="chain_rpc",
                )

        for outcome_index in (0, 1):
            append_observation(
                conn,
                condition_id=_CONDITION_A,
                outcome_index=outcome_index,
                payout_numerator=None,
                payout_denominator=0,
                state=STATE_UNRESOLVED,
                block_number=2,
                block_hash="0xbb",
                observed_at="t1",
            )
            append_observation(
                conn,
                condition_id=_CONDITION_A,
                outcome_index=outcome_index,
                payout_numerator=None,
                payout_denominator=None,
                state=STATE_UNKNOWN,
                block_number=3,
                block_hash="0xcc",
                observed_at="t2",
            )

        selected = conditions_to_observe(conn)
        assert _CONDITION_A in selected
        assert len(selected) == LEGACY_FINALITY_UPGRADE_BATCH_SIZE + 1


# ---------------------------------------------------------------------------
# sweep_and_record — orchestration
# ---------------------------------------------------------------------------


class TestSweepAndRecord:
    def test_sweeps_all_conditions_and_reports_counts(self, conn):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "INSERT INTO position_current VALUES ('p1', ?, 'settled')", (_CONDITION_A,)
        )
        conn.execute("INSERT INTO settlement_commands VALUES ('c1', ?)", (_CONDITION_B,))

        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        result = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc, now="t0")
        assert result["conditions"] == 2
        assert result["appended"] == 4  # 2 outcome_indices x 2 conditions
        total_rows = conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0]
        assert total_rows == 4

        # Terminal binary payout history is immutable and leaves the recurring sweep.
        rpc2, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        result2 = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc2, now="t1")
        assert result2["appended"] == 0
        assert result2 == {"conditions": 0, "appended": 0, "unchanged": 0}

    def test_finishes_all_rpc_reads_before_opening_append_transaction(self, conn):
        conn.execute(
            "CREATE TABLE position_current (position_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute("INSERT INTO position_current VALUES ('p1', ?)", (_CONDITION_A,))
        conn.execute("INSERT INTO settlement_commands VALUES ('c1', ?)", (_CONDITION_B,))
        conn.commit()

        base_rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        rpc_calls = 0

        def rpc(url, method, params):
            nonlocal rpc_calls
            rpc_calls += 1
            assert not conn.in_transaction, (
                "payout RPC ran while the append transaction held the trades-DB writer lock"
            )
            return base_rpc(url, method, params)

        result = sweep_and_record(
            conn,
            rpc_url="https://rpc.example",
            rpc_call=rpc,
            now="t0",
        )

        # One finalized block marker + (denominator + two numerators) per
        # resolved binary condition.
        assert rpc_calls == 7
        assert result == {"conditions": 2, "appended": 4, "unchanged": 0}

    @pytest.mark.parametrize(
        ("numerators", "fail_numerator_indices", "expected_states"),
        [
            ({0: 100, 1: 0}, set(), {0: STATE_RESOLVED_NONZERO, 1: STATE_RESOLVED_ZERO}),
            ({0: 100, 1: 0}, {1}, {0: STATE_RESOLVED_NONZERO, 1: STATE_UNKNOWN}),
        ],
        ids=["complete-finalized-pair", "numerator-failure-unknown"],
    )
    def test_default_observed_at_is_after_all_rpc_reads(
        self, conn, monkeypatch, numerators, fail_numerator_indices, expected_states
    ):
        conn.execute(
            "CREATE TABLE position_current ("
            "position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute("INSERT INTO position_current VALUES ('p1', ?, 'settled')", (_CONDITION_A,))
        conn.commit()

        start = payout_observer.datetime.fromisoformat("2026-09-09T12:00:00+00:00")

        class AdvancingDateTime(payout_observer.datetime):
            current = start

            @classmethod
            def now(cls, tz=None):
                return cls.current

        monkeypatch.setattr(payout_observer, "datetime", AdvancingDateTime)
        base_rpc, _ = _build_stub_rpc(
            denominator=100,
            numerators=numerators,
            fail_numerator_indices=fail_numerator_indices,
        )

        def rpc(url, method, params):
            assert not conn.in_transaction
            try:
                return base_rpc(url, method, params)
            finally:
                AdvancingDateTime.current += timedelta(seconds=5)

        result = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc)
        assert result == {"conditions": 1, "appended": 2, "unchanged": 0}
        after_rpc = start + timedelta(seconds=20)
        assert AdvancingDateTime.current == after_rpc
        rows = conn.execute(
            "SELECT outcome_index, state, observed_at FROM payout_observations "
            "WHERE superseded_by IS NULL ORDER BY outcome_index"
        ).fetchall()
        assert [(index, state) for index, state, _ in rows] == [
            (index, expected_states[index]) for index in (0, 1)
        ]
        assert all(observed_at == after_rpc.isoformat() for _, _, observed_at in rows), rows
        assert all(observed_at > start.isoformat() for _, _, observed_at in rows)


# ---------------------------------------------------------------------------
# No-signing-capability antibody
# ---------------------------------------------------------------------------


_FORBIDDEN_IMPORT_TOKENS = (
    "py_clob_client_v2",
    "web3",
    "Web3",
    "signer_key",
    "private_key",
    "PolymarketV2Adapter",
)


def test_no_signing_capability_import_antibody():
    """payout_observer.py must never import a wallet key / signer / SDK client.

    Read-only law (LX-T1 adjudication): this module only ever issues
    eth_call/eth_getBlockByNumber over public RPC. AST-walk every Import/
    ImportFrom node so a future edit that pulls in signing machinery fails
    this test immediately, rather than silently acquiring a broadcast path.
    """
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_names.add(module)
            for alias in node.names:
                imported_names.add(alias.name)
    offending = {
        name for name in imported_names
        if any(token.lower() in name.lower() for token in _FORBIDDEN_IMPORT_TOKENS)
    }
    assert not offending, f"payout_observer.py imports forbidden signing-capable names: {offending!r}"


def _seed_terminal_pair_candidate(conn, condition=_CONDITION_A, phase="settled"):
    conn.execute("CREATE TABLE IF NOT EXISTS position_current (position_id TEXT PRIMARY KEY, condition_id TEXT, phase TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)")
    conn.execute("INSERT INTO position_current VALUES (?, ?, ?)", (condition, condition, phase))


def _append_pair_slot(conn, index, *, block=100, state=None, condition=_CONDITION_A):
    state = state or (STATE_RESOLVED_ZERO if index == 0 else STATE_RESOLVED_NONZERO)
    return append_observation(
        conn, condition_id=condition, outcome_index=index,
        payout_numerator=None if state == STATE_UNKNOWN else index,
        payout_denominator=None if state == STATE_UNKNOWN else 1,
        state=state, block_number=block, block_hash=hex(block), observed_at=str(block),
    )


def test_partial_then_complete_sweep_converges_finalized_block_pair(conn):
    _seed_terminal_pair_candidate(conn)
    _append_pair_slot(conn, 0)
    _append_pair_slot(conn, 1, state=STATE_UNKNOWN)
    conn.commit()
    rpc, _ = _build_stub_rpc(denominator=1, numerators={0: 0, 1: 1}, block_number=101, block_hash="0x65")
    result = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc)
    latest = conn.execute("SELECT outcome_index,block_number,block_hash FROM payout_observations WHERE id IN (SELECT MAX(id) FROM payout_observations GROUP BY condition_id,outcome_index) ORDER BY outcome_index").fetchall()
    assert [tuple(r) for r in latest] == [(0,101,"0x65"), (1,101,"0x65")]
    assert result["appended"] == 2
    assert conditions_to_observe(conn) == []


def test_mixed_block_terminal_pair_is_retried_with_existing_batch_bound(conn):
    conditions = [f"0x{n+1:064x}" for n in range(LEGACY_FINALITY_UPGRADE_BATCH_SIZE + 2)]
    for condition in conditions:
        _seed_terminal_pair_candidate(conn, condition)
        _append_pair_slot(conn, 0, condition=condition)
        _append_pair_slot(conn, 1, block=101, condition=condition)
    assert len(conditions_to_observe(conn)) == LEGACY_FINALITY_UPGRADE_BATCH_SIZE


def test_latest_unknown_cannot_be_pruned_using_older_terminal_pair(conn):
    _seed_terminal_pair_candidate(conn)
    _append_pair_slot(conn, 0)
    _append_pair_slot(conn, 1)
    _append_pair_slot(conn, 1, block=101, state=STATE_UNKNOWN)
    assert conditions_to_observe(conn) == [_CONDITION_A]


def test_coherent_current_pair_does_not_append_on_every_new_block(conn):
    _seed_terminal_pair_candidate(conn, phase="active")
    _append_pair_slot(conn, 0)
    _append_pair_slot(conn, 1)
    conn.commit()
    rpc, _ = _build_stub_rpc(denominator=1, numerators={0: 0, 1: 1}, block_number=101, block_hash="0x65")
    result = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc)
    assert result == {"conditions": 1, "appended": 0, "unchanged": 2}
    assert conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0] == 2


def test_condition_pair_append_failure_rolls_back_first_slot(conn):
    _seed_terminal_pair_candidate(conn)
    conn.execute("CREATE TRIGGER fail_second BEFORE INSERT ON payout_observations WHEN NEW.outcome_index=1 BEGIN SELECT RAISE(ABORT, 'second_slot_failure'); END")
    conn.commit()
    rpc, _ = _build_stub_rpc(denominator=1, numerators={0: 0, 1: 1})
    with pytest.raises(sqlite3.IntegrityError, match="second_slot_failure"):
        sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc)
    assert conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0] == 0


def _forecast_learning_fixture() -> sqlite3.Connection:
    forecast = sqlite3.connect(":memory:")
    forecast.execute(
        "CREATE TABLE market_events ("
        "condition_id TEXT, city TEXT, target_date TEXT, temperature_metric TEXT)"
    )
    forecast.execute(
        "CREATE TABLE forecast_posteriors ("
        "city TEXT, target_date TEXT, temperature_metric TEXT)"
    )
    return forecast


def _seed_forecast_family(forecast, condition, city, target_date, metric):
    forecast.execute(
        "INSERT INTO market_events VALUES (?, ?, ?, ?)",
        (condition, city, target_date, metric),
    )
    forecast.execute(
        "INSERT INTO forecast_posteriors VALUES (?, ?, ?)",
        (city, target_date, metric),
    )
    forecast.commit()


def test_forecast_universe_includes_untraded_past_high_low_and_excludes_coherent_pair(
    conn,
):
    forecast = _forecast_learning_fixture()
    _seed_forecast_family(forecast, _CONDITION_A, "Tel Aviv", "2026-09-08", "high")
    _seed_forecast_family(forecast, _CONDITION_B, "Tel Aviv", "2026-09-08", "low")
    condition_c = "0x" + "ef" * 32
    _seed_forecast_family(forecast, condition_c, "Tel Aviv", "2026-09-08", "high")
    _seed_forecast_family(forecast, "0x" + "12" * 32, "Tel Aviv", "2026-09-10", "high")
    no_posterior_condition = "0x" + "34" * 32
    forecast.execute(
        "INSERT INTO market_events VALUES (?, ?, ?, ?)",
        (no_posterior_condition, "Tel Aviv", "2026-09-07", "low"),
    )
    forecast.commit()
    for index, numerator in ((0, 100), (1, 0)):
        append_observation(
            conn,
            condition_id=condition_c,
            outcome_index=index,
            payout_numerator=numerator,
            payout_denominator=100,
            state=STATE_RESOLVED_NONZERO if numerator else STATE_RESOLVED_ZERO,
            block_number=7,
            block_hash="0x07",
            observed_at="t0",
        )

    selected = forecast_conditions_to_observe(
        conn,
        forecast,
        now="2026-09-09T12:00:00+00:00",
    )
    assert len(selected["universe"]) == 5
    assert selected["forecast_missing"] == 1
    assert no_posterior_condition in {
        row["condition_id"] for row in selected["pending"]
    }
    assert {row["condition_id"] for row in selected["ended_universe"]} == {
        _CONDITION_A,
        _CONDITION_B,
        condition_c,
        no_posterior_condition,
    }
    assert {row["condition_id"] for row in selected["pending"]} == {
        _CONDITION_A,
        _CONDITION_B,
        no_posterior_condition,
    }


def test_forecast_rotation_advances_with_same_block_and_actual_cap(conn, monkeypatch):
    forecast = _forecast_learning_fixture()
    condition_c = "0x" + "ef" * 32
    _seed_forecast_family(forecast, _CONDITION_A, "Tel Aviv", "2026-09-08", "high")
    _seed_forecast_family(forecast, _CONDITION_B, "Tel Aviv", "2026-09-08", "low")
    _seed_forecast_family(forecast, condition_c, "Tel Aviv", "2026-09-08", "high")
    monkeypatch.setenv("ZEUS_POST_TRADE_PAYOUT_LEARNING_CAP", "1")
    first = forecast_conditions_to_observe(
        conn, forecast, now="2026-09-09T12:00:00+00:00"
    )
    second = forecast_conditions_to_observe(
        conn, forecast, now="2026-09-09T12:10:00+00:00"
    )
    assert len(first["selected"]) == len(second["selected"]) == 1
    assert first["selected"][0]["condition_id"] != second["selected"][0]["condition_id"]


def test_learning_two_phase_batch_keeps_partial_unknown_and_never_reads_unknown_numerator(
    conn, monkeypatch
):
    forecast = _forecast_learning_fixture()
    _seed_forecast_family(forecast, _CONDITION_A, "Tel Aviv", "2026-09-08", "high")
    _seed_forecast_family(forecast, _CONDITION_B, "Tel Aviv", "2026-09-08", "low")
    monkeypatch.setattr(
        payout_observer,
        "_learning_block_marker",
        lambda *_args, **_kwargs: (77, "0x77"),
    )
    batches = []

    def hard_batch(_url, calls, *, timeout_seconds):
        assert not conn.in_transaction
        batches.append(calls)
        if len(batches) == 1:
            return [_uint(100), _uint(0)]
        # The second numerator is an empty response and must remain UNKNOWN.
        return [_uint(100), "0x"]

    monkeypatch.setattr(
        payout_observer,
        "_json_rpc_batch_call_hard_deadline",
        hard_batch,
    )
    result = _learning_sweep(
        conn,
        forecast,
        rpc_url="https://rpc.example",
        rpc_call=payout_observer._json_rpc_call,
        now="2026-09-09T12:00:00+00:00",
    )
    assert len(batches) == 2
    assert len(batches[0]) == 2  # denominator stage, one per condition
    assert len(batches[1]) == 2  # numerators only for resolved denominator
    latest = conn.execute(
        "SELECT condition_id, outcome_index, state FROM payout_observations "
        "WHERE superseded_by IS NULL ORDER BY condition_id, outcome_index"
    ).fetchall()
    assert [tuple(row) for row in latest] == [
        (_CONDITION_A, 0, STATE_RESOLVED_NONZERO),
        (_CONDITION_A, 1, STATE_UNKNOWN),
        (_CONDITION_B, 0, STATE_UNRESOLVED),
        (_CONDITION_B, 1, STATE_UNRESOLVED),
    ]
    assert result["processed"] == 2
    assert result["unknown"] == 1


def test_learning_denominator_timeout_writes_unknown_without_numerator_batch(
    conn, monkeypatch
):
    forecast = _forecast_learning_fixture()
    _seed_forecast_family(forecast, _CONDITION_A, "Tel Aviv", "2026-09-08", "high")
    _seed_forecast_family(forecast, _CONDITION_B, "Tel Aviv", "2026-09-08", "low")
    monkeypatch.setattr(
        payout_observer,
        "_learning_block_marker",
        lambda *_args, **_kwargs: (77, "0x77"),
    )
    batches = []

    def timed_out_batch(_url, calls, *, timeout_seconds):
        assert not conn.in_transaction
        batches.append(calls)
        raise TimeoutError("deadline")

    monkeypatch.setattr(
        payout_observer,
        "_json_rpc_batch_call_hard_deadline",
        timed_out_batch,
    )
    result = _learning_sweep(
        conn,
        forecast,
        rpc_url="https://rpc.example",
        rpc_call=payout_observer._json_rpc_call,
        now="2026-09-09T12:00:00+00:00",
    )
    assert len(batches) == 1
    assert result["processed"] == 2
    assert result["unknown"] == 2
    assert conn.execute(
        "SELECT COUNT(*) FROM payout_observations WHERE state='UNKNOWN'"
    ).fetchone()[0] == 4


def test_primary_commit_survives_learning_failure(conn, monkeypatch):
    forecast = _forecast_learning_fixture()

    def primary(c, **_kwargs):
        append_observation(
            c,
            condition_id=_CONDITION_A,
            outcome_index=0,
            payout_numerator=None,
            payout_denominator=None,
            state=STATE_UNKNOWN,
            block_number=None,
            block_hash=None,
            observed_at="primary",
        )
        return {"conditions": 1, "appended": 1, "unchanged": 0}

    monkeypatch.setattr(payout_observer, "sweep_and_record", primary)
    monkeypatch.setattr(
        payout_observer,
        "_learning_sweep",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("learning failed")),
    )
    result = payout_observer_cycle(
        conn=conn,
        forecast_conn=forecast,
        rpc_call=lambda *_args: None,
        now="2026-09-09T12:00:00+00:00",
    )
    assert conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0] == 1
    assert result["conditions"] == 1
    assert "learning failed" in result["learning"]["error"]


def test_injected_trade_conn_without_forecast_conn_skips_learning(monkeypatch, conn):
    monkeypatch.setattr(
        payout_observer,
        "_learning_sweep",
        lambda *_args, **_kwargs: pytest.fail("primary-only injection opened learning"),
    )
    monkeypatch.setattr(
        payout_observer,
        "sweep_and_record",
        lambda *_args, **_kwargs: {"conditions": 0, "appended": 0, "unchanged": 0},
    )
    result = payout_observer_cycle(
        conn=conn,
        rpc_call=lambda *_args: None,
        now="2026-09-09T12:00:00+00:00",
    )
    assert result["learning"]["status"] == "skipped_no_forecast_connection"


def test_unknown_after_resolved_history_stays_in_bounded_retry_class(conn):
    for n in range(LEGACY_FINALITY_UPGRADE_BATCH_SIZE + 2):
        condition = f"0x{n+1:064x}"
        _seed_terminal_pair_candidate(conn, condition)
        _append_pair_slot(conn, 0, condition=condition)
        _append_pair_slot(conn, 1, condition=condition)
        _append_pair_slot(conn, 1, block=101, state=STATE_UNKNOWN, condition=condition)
    assert len(conditions_to_observe(conn)) == LEGACY_FINALITY_UPGRADE_BATCH_SIZE


@pytest.mark.parametrize("owned", [True, False])
def test_primary_failure_closes_only_owned_connection(monkeypatch, owned):
    import src.ingest.payout_observer as observer
    import src.state.db as state_db

    connection = sqlite3.connect(":memory:")
    monkeypatch.setattr(state_db, "get_trade_connection", lambda **kwargs: connection)
    def fail_primary(*args, **kwargs):
        raise RuntimeError("primary unavailable")
    monkeypatch.setattr(observer, "sweep_and_record", fail_primary)
    try:
        with pytest.raises(RuntimeError, match="primary unavailable"):
            observer.payout_observer_cycle(**({} if owned else {"conn": connection}))
        if owned:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")
        else:
            assert connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        connection.close()


def test_learning_rotation_covers_every_condition_when_budget_only_allows_a_prefix(conn, monkeypatch):
    from datetime import datetime, timedelta, timezone

    forecast = _forecast_learning_fixture()
    conditions = {f"0x{index+1:064x}" for index in range(16)}
    for condition in conditions:
        _seed_forecast_family(forecast, condition, "Tel Aviv", "2026-09-08", "high")
    monkeypatch.setenv("ZEUS_POST_TRADE_PAYOUT_LEARNING_CAP", "4")
    start = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
    reached = set()
    for slot in range(len(conditions)):
        selection = forecast_conditions_to_observe(conn, forecast, now=start+timedelta(minutes=10*slot))
        # A slow endpoint repeatedly consumes the budget on the first condition.
        # None resolves, so N remains stable; no new observation can advance it.
        reached.add(selection["selected"][0]["condition_id"])
    assert reached == conditions
    forecast.close()
