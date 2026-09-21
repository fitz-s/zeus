# Created: 2026-09-18
# Last reused/audited: 2026-09-21
# Authority basis: held-filled-entry cohort hotfix and ENTRY provenance gate

"""Entry provenance must refuse CONFLICT, not ABSENCE.

`load_held_entry_calibration` gates held-entry calibration, and a refusal is
swallowed upstream into `entry_calibration_unavailable`, which denies the
position an exit decision for the rest of its life. Two of its clauses
contradicted each other:

    or int(event_status[1] or 0) > 0            # ANY row with no decision_id
    or not 1 <= len(event_identity_rows) <= 2   # DISTINCT rows, 1 or 2 allowed

`SELECT DISTINCT decision_id` over three entry events returns 2 rows for
{NULL, one identity} -- which the second clause admits and the first rejects.
The `<= 2` allowance can only have meant "one identity, one NULL row tolerated".

Worse, the pair was inverted: {NULL, one identity} (unambiguous) was REFUSED
while {id1, id2} (two conflicting identities, zero NULLs) PASSED. On 2026-09-18
that denied exit authority to both live positions -- a -$19.44 Hong Kong loser
with FLASH_CRASH_PANIC raised and zero exit commands ever created -- and, through
a permanently stale monitor probability, preempted the auction into a total entry
drought (DEFERRED_PREEMPTED, the top decline reason).
"""

from __future__ import annotations

import sqlite3

import pytest

from src.calibration.market_anchored_live_fit import load_held_entry_calibration
from src.contracts.payoff_q_correction import PayoffQCorrectionUnavailable

_POSITION = "pos-provenance"
_TOKEN = "tok-provenance"
_IDENTITY = "edli_exec_cmd:evt-a:edli_intent:evt-a:tok:tok:buy_no"
_OTHER_IDENTITY = "edli_exec_cmd:evt-b:edli_intent:evt-b:tok:tok:buy_no"
_CERT = "c" * 64


def _ledger(entry_identities: list[str | None]) -> sqlite3.Connection:
    """One position whose three entry events carry the given identities."""

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE position_events (
            position_id TEXT, sequence_no INTEGER, event_type TEXT,
            decision_id TEXT, payload_json TEXT, command_id TEXT
        );
        CREATE TABLE position_decision_attribution (
            position_id TEXT, intent_kind TEXT, resolution TEXT,
            decision_certificate_hash TEXT, command_id TEXT
        );
        CREATE TABLE position_current (position_id TEXT, token_id TEXT, direction TEXT);
        """
    )
    types = ["POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_FILLED"]
    for index, (event_type, identity) in enumerate(zip(types, entry_identities)):
        conn.execute(
            "INSERT INTO position_events VALUES (?,?,?,?,?,?)",
            (
                _POSITION,
                index + 1,
                event_type,
                identity,
                '{"decision_log_id": 7}',
                "command-filled",
            ),
        )
    conn.execute(
        "INSERT INTO position_decision_attribution VALUES (?,?,?,?,?)",
        (_POSITION, "ENTRY", "ATTRIBUTED", _CERT, "command-filled"),
    )
    conn.commit()
    return conn


def _load(conn):
    return load_held_entry_calibration(
        conn, position_id=_POSITION, token_id=_TOKEN, side="NO"
    )


def _refusal(conn) -> str:
    with pytest.raises(PayoffQCorrectionUnavailable) as caught:
        _load(conn)
    return str(caught.value)


class TestProvenanceGate:
    def test_one_identity_with_a_null_row_is_not_ambiguous(self):
        """The live shape: the fill event dropped its identity, siblings kept it."""

        conn = _ledger([_IDENTITY, _IDENTITY, None])
        # It must get PAST the ambiguity clause. The certificate lookup then
        # fails on this minimal fixture, which is a different, later refusal.
        assert "ENTRY_PROVENANCE_AMBIGUOUS" not in _refusal(conn)

    def test_three_distinct_spellings_are_refused(self):
        """Two spellings of one ENTRY are legal; a third is not.

        The normal and recovery writers may spell the same ENTRY differently,
        and the loop after this clause authenticates each spelling against the
        certificate. Only a third distinct identity exceeds that allowance, so
        that -- not a NULL -- is what the row count must catch.
        """

        conn = _ledger([_IDENTITY, _OTHER_IDENTITY, "edli_exec_cmd:evt-c:i:t:t:buy_no"])
        assert "ENTRY_PROVENANCE_AMBIGUOUS" in _refusal(conn)

    def test_a_null_never_consumes_the_two_spelling_allowance(self):
        """A NULL plus two legal spellings must not read as three identities."""

        conn = _ledger([_IDENTITY, _OTHER_IDENTITY, None])
        assert "ENTRY_PROVENANCE_AMBIGUOUS" not in _refusal(conn)

    def test_no_identity_at_all_is_refused(self):
        conn = _ledger([None, None, None])
        assert "ENTRY_PROVENANCE_AMBIGUOUS" in _refusal(conn)

    def test_a_blank_identity_counts_as_absent_not_as_a_second_one(self):
        conn = _ledger([_IDENTITY, "   ", _IDENTITY])
        assert "ENTRY_PROVENANCE_AMBIGUOUS" not in _refusal(conn)

    def test_a_complete_single_identity_still_passes_the_clause(self):
        conn = _ledger([_IDENTITY, _IDENTITY, _IDENTITY])
        assert "ENTRY_PROVENANCE_AMBIGUOUS" not in _refusal(conn)

    def test_two_certificate_hashes_still_refuse(self):
        """The independent cross-check must stay intact."""

        conn = _ledger([_IDENTITY, _IDENTITY, None])
        conn.execute(
            "INSERT INTO position_decision_attribution VALUES (?,?,?,?,?)",
            (_POSITION, "ENTRY", "ATTRIBUTED", "d" * 64, "command-filled"),
        )
        conn.commit()
        assert "ENTRY_PROVENANCE_AMBIGUOUS" in _refusal(conn)

    def test_conflicting_decision_log_ids_still_refuse(self):
        conn = _ledger([_IDENTITY, _IDENTITY, None])
        conn.execute(
            "UPDATE position_events SET payload_json = ? WHERE sequence_no = 2",
            ('{"decision_log_id": 9}',),
        )
        conn.commit()
        assert "ENTRY_PROVENANCE_AMBIGUOUS" in _refusal(conn)
