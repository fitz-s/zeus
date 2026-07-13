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


# ---------------------------------------------------------------------------
# sweep_and_record — orchestration
# ---------------------------------------------------------------------------


class TestSweepAndRecord:
    def test_sweeps_all_conditions_and_reports_counts(self, conn):
        conn.execute(
            "CREATE TABLE position_current (position_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE settlement_commands (command_id TEXT PRIMARY KEY, condition_id TEXT)"
        )
        conn.execute("INSERT INTO position_current VALUES ('p1', ?)", (_CONDITION_A,))
        conn.execute("INSERT INTO settlement_commands VALUES ('c1', ?)", (_CONDITION_B,))

        rpc, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        result = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc, now="t0")
        assert result["conditions"] == 2
        assert result["appended"] == 4  # 2 outcome_indices x 2 conditions
        total_rows = conn.execute("SELECT COUNT(*) FROM payout_observations").fetchone()[0]
        assert total_rows == 4

        # Re-sweeping with an identical chain state appends nothing new.
        rpc2, _ = _build_stub_rpc(denominator=100, numerators={0: 100, 1: 0})
        result2 = sweep_and_record(conn, rpc_url="https://rpc.example", rpc_call=rpc2, now="t1")
        assert result2["appended"] == 0
        assert result2["unchanged"] == 4


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
