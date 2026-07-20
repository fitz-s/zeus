# Created: 2026-07-13
# Last reused or audited: 2026-07-13
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
from pathlib import Path

import pytest

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
