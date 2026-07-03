# Created: 2026-07-03
# Authority basis: cross-packet money/order seam proof requested by the full-branch consult
#   review (§6 fix 3) before W4.2 merge-to-main — composes W1.1 (CAS collateral
#   reservation/conversion, src/state/collateral_ledger.py), W2.1 (batch cancel gateway,
#   src/execution/batch_order_submission.py), and W4.2 (C3 staleness/TTL cancel path,
#   src/execution/staleness_cancel.py) in one interleaving: an open ENTRY rest with a
#   partial-fill fact in flight, cancelled alongside a batch chunk whose SDK call raises,
#   then replayed as if a duplicate SOURCE_RUN_ARRIVED drove a second cycle.
"""Cross-packet integration proof: collateral conversion, batch cancel ambiguity, and
reconciled-redecision gating compose correctly under one adversarial interleaving.

Scenario (one MAX_ORDERS_PER_BATCH=15 chunk boundary, deliberately straddled):
  - 16 open ENTRY rests, all past the TTL deadline (rest_deadline_exceeded), each with a
    live PUSD_BUY collateral reservation.
  - Command 1 ("c-partial", chunk 1) has a PARTIALLY_MATCHED venue_order_facts row recorded
    BEFORE the cancel cycle runs -- the partial-fill-fact-in-flight boundary case.
  - Commands 2-15 (chunk 1, 14 more) cancel cleanly.
  - Command 16 ("c-chunk2", alone in chunk 2, in a SEPARATE family OTHER_FAMILY) hits an
    SDK exception on its chunk's cancel call -- the ambiguous-outcome case that must halt
    without a false CANCELLED state.

Assertions (all four required by the consult):
  1. No collateral over-release: every reservation's converted_amount stays within
     [0, amount], and a NON-cancelled command's reservation is untouched (released_at IS NULL).
  2. converted_amount equals latest matched truth: c-partial's converted_amount is derived
     from the ACTUAL matched_size on file at terminalization time (W1.1's
     convert_reservation_on_fill / _max_matched_size), not zero and not the full amount.
  3. No duplicate cancel side effect beyond CANCEL_PENDING recovery: a replayed
     run_c3_staleness_cancel_cycle (simulating a duplicate SOURCE_RUN_ARRIVED) produces zero
     additional CANCEL_ACKED events and zero additional collateral writes for c-partial;
     c-chunk2's ambiguous state is durably recovery-owned (REVIEW_REQUIRED), not re-touched
     by the staleness scan either (it is IN_FLIGHT, not in the open-rest scan's state set).
  4. No redecision emit before durable cancel state: confirmed_families (the gate
     main._c3_staleness_cancel_cycle's redecision emit reads) includes FAMILY (all 15 of
     its commands durably cancelled) but excludes OTHER_FAMILY (its one command never
     reached durable CANCELLED).

Consult round-2 finding on the FIRST version of this file: putting c-chunk2 in the SAME
family as everything else made `confirmed_families == {FAMILY}` true under BOTH the old
per-command bug and the fixed family-level gate -- the assertion never actually exercised
family-level suppression, it just happened to read the same either way. Splitting c-chunk2
into its own family fixes that (assertion 4 above), and
test_cross_packet_same_family_mixed_outcomes_suppress_whole_family_confirmation below is
the test that DOES distinguish the two: two commands sharing ONE family, one cancels
cleanly with correct collateral conversion, the other comes back ambiguous in the SAME
batch call -- the family-level gate must exclude the WHOLE family despite the clean
command's OWN collateral accounting being individually correct.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from src.execution.staleness_cancel import run_c3_staleness_cancel_cycle
from src.state.venue_command_repo import get_command
from src.venue.batch_submit import MAX_ORDERS_PER_BATCH
from tests.execution.test_staleness_cancel import (
    FAMILY,
    DEADLINE_MIN,
    _forecasts_db,
    _seed_market_event,
    _seed_open_entry,
    _trade_db,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 3, 22, 0, 0, tzinfo=UTC)
_RESERVE_AMOUNT_MICRO = 1_000_000  # 1.0 pUSD-equivalent per command, arbitrary but uniform


def _seed_collateral_snapshot(conn: sqlite3.Connection, *, pusd_balance_micro: int) -> None:
    """One collateral_ledger_snapshots row -- required by the CAS
    trg_reservations_no_overreserve trigger (init_schema -> init_collateral_schema)
    before any collateral_reservations INSERT can pass."""
    conn.execute(
        """
        INSERT INTO collateral_ledger_snapshots (
            pusd_balance_micro, pusd_allowance_micro, usdc_e_legacy_balance_micro,
            ctf_token_balances_json, ctf_token_allowances_json,
            reserved_pusd_for_buys_micro, reserved_tokens_for_sells_json,
            captured_at, authority_tier
        ) VALUES (?, ?, 0, '{}', '{}', 0, '{}', ?, 'CHAIN')
        """,
        (pusd_balance_micro, pusd_balance_micro, NOW.isoformat()),
    )
    conn.commit()


def _seed_reservation(conn: sqlite3.Connection, *, command_id: str, amount_micro: int) -> None:
    conn.execute(
        """
        INSERT INTO collateral_reservations
          (command_id, reservation_type, token_id, amount, converted_amount, created_at)
        VALUES (?, 'PUSD_BUY', NULL, ?, 0, ?)
        """,
        (command_id, amount_micro, NOW.isoformat()),
    )
    conn.commit()


def _reservation_row(conn: sqlite3.Connection, command_id: str) -> dict:
    row = conn.execute(
        "SELECT amount, converted_amount, released_at, release_reason "
        "FROM collateral_reservations WHERE command_id = ?",
        (command_id,),
    ).fetchone()
    assert row is not None, f"no reservation row for {command_id}"
    return dict(row)


class _AdversarialGatewayClient:
    """Chunk 1 succeeds; chunk 2 (the last command, alone in its own chunk under
    MAX_ORDERS_PER_BATCH=15) raises -- the ambiguous-outcome halt path."""

    def __init__(self, *, chunk1_order_ids: list[str], raise_on_chunk2: bool):
        self._chunk1_order_ids = list(chunk1_order_ids)
        self._raise_on_chunk2 = raise_on_chunk2
        self.cancel_calls: list[list[str]] = []
        self._call_count = 0

    def cancel_orders_batch(self, order_ids):
        self.cancel_calls.append(list(order_ids))
        self._call_count += 1
        if self._call_count == 1:
            return [{"canceled": True, "orderID": oid} for oid in order_ids]
        if self._raise_on_chunk2:
            raise RuntimeError("simulated venue timeout mid-chunk-2")
        return [{"canceled": True, "orderID": oid} for oid in order_ids]


def test_cross_packet_partial_fill_batch_exception_seam():
    trade_conn = _trade_db()
    forecasts_conn = _forecasts_db()
    _seed_collateral_snapshot(trade_conn, pusd_balance_micro=100_000_000)

    n_commands = MAX_ORDERS_PER_BATCH + 1  # straddles the chunk boundary: 15 + 1
    # Fixed-width suffixes: _seed_open_entry derives idempotency_key by
    # zero-padding command_id to 32 chars, so "c1" and "c10" would otherwise
    # collide ("c1" + 30 zeros == "c10" + 29 zeros).
    command_ids = [f"c{i:03d}" for i in range(n_commands)]
    venue_order_ids = [f"v{i:03d}" for i in range(n_commands)]
    partial_command_id = command_ids[0]
    chunk2_command_id = command_ids[-1]
    other_family = ("Toronto", "2026-07-04", "high")

    for idx, (cid, oid) in enumerate(zip(command_ids, venue_order_ids)):
        # chunk2's command is in ITS OWN family (other_family) -- a shared
        # family here would make confirmed_families == {FAMILY} true under
        # BOTH the old per-command bug and the fixed family-level gate,
        # never actually exercising suppression (consult round-2 finding).
        token_id = "tok-chunk2" if cid == chunk2_command_id else "tok-shared"
        _seed_open_entry(
            trade_conn, command_id=cid, token_id=token_id, venue_order_id=oid,
            q_version="q-old", created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
        )
        _seed_reservation(trade_conn, command_id=cid, amount_micro=_RESERVE_AMOUNT_MICRO)
    _seed_market_event(forecasts_conn, token_id="tok-shared", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
    _seed_market_event(
        forecasts_conn, token_id="tok-chunk2", city=other_family[0], target_date=other_family[1], metric=other_family[2]
    )

    # Partial-fill fact IN FLIGHT before the cancel cycle runs: a real matched
    # amount already on file when TTL classification and terminalization see it.
    trade_conn.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, remaining_size, matched_size, "
        "source, observed_at, local_sequence, raw_payload_hash) "
        "VALUES (?, ?, 'PARTIALLY_MATCHED', '6', '4', 'WS_USER', ?, 1, ?)",
        (venue_order_ids[0], partial_command_id, NOW.isoformat(), "a" * 64),
    )
    trade_conn.commit()

    client = _AdversarialGatewayClient(
        chunk1_order_ids=venue_order_ids[:MAX_ORDERS_PER_BATCH], raise_on_chunk2=True,
    )

    result = run_c3_staleness_cancel_cycle(
        trade_conn, trade_conn, forecasts_conn, client, now=NOW,
    )

    # --- chunk-1 command with the in-flight partial fill: durably cancelled,
    # collateral converted to the ACTUAL matched truth. ---
    assert get_command(trade_conn, partial_command_id)["state"] == "CANCELLED"
    partial_res = _reservation_row(trade_conn, partial_command_id)
    assert partial_res["released_at"] is not None
    # order size=10 (per _seed_open_entry), matched=4 -> ratio 0.4, floor(1_000_000*0.4)=400_000.
    assert partial_res["converted_amount"] == 400_000
    assert partial_res["release_reason"] == "CONVERTED_ON_FILL"
    # No over-release: converted never exceeds the original reserved amount.
    assert 0 <= partial_res["converted_amount"] <= partial_res["amount"]

    # --- the other 13 clean chunk-1 commands: fully released, zero converted
    # (no fill fact for them). ---
    for cid in command_ids[1:MAX_ORDERS_PER_BATCH]:
        assert get_command(trade_conn, cid)["state"] == "CANCELLED"
        res = _reservation_row(trade_conn, cid)
        assert res["released_at"] is not None
        assert res["converted_amount"] == 0
        assert 0 <= res["converted_amount"] <= res["amount"]

    # --- chunk-2 command: SDK exception -> ambiguous outcome -> REVIEW_REQUIRED,
    # NOT CANCELLED. Its reservation must be completely untouched (no over-release,
    # no premature conversion on a command that never durably terminalized). ---
    chunk2_state = get_command(trade_conn, chunk2_command_id)["state"]
    assert chunk2_state == "REVIEW_REQUIRED"
    chunk2_res = _reservation_row(trade_conn, chunk2_command_id)
    assert chunk2_res["released_at"] is None
    assert chunk2_res["converted_amount"] == 0

    # --- no redecision emit before durable cancel state: FAMILY (all 15 chunk-1
    # commands, EVERY one durably CANCELLED) is confirmed; other_family (its
    # ONE command stuck at REVIEW_REQUIRED) is excluded entirely. Because
    # chunk-2's command lives in its OWN family, this specifically proves the
    # gate does not falsely block an UNRELATED clean family -- the same-family
    # mixed-outcome suppression itself is proven by the sibling test below. ---
    assert result["confirmed_families"] == {FAMILY}
    assert other_family not in result["confirmed_families"]
    assert len(client.cancel_calls) == 2  # chunk 1, chunk 2 -- no further chunks attempted

    cancel_acked_count_after_first = trade_conn.execute(
        "SELECT COUNT(*) FROM venue_command_events WHERE command_id = ? AND event_type = 'CANCEL_ACKED'",
        (partial_command_id,),
    ).fetchone()[0]
    assert cancel_acked_count_after_first == 1

    # --- REPLAY: a duplicate SOURCE_RUN_ARRIVED drives a second cycle over the
    # SAME truth. The already-CANCELLED command is no longer in the open-rest
    # scan (state filter excludes CANCELLED); the REVIEW_REQUIRED command is
    # ALSO excluded (recovery-owned, not ACKED/POST_ACKED/PARTIAL) -- so the
    # replay must find nothing left to touch: zero new SDK calls, zero new
    # journal entries, zero collateral mutation. ---
    replay_client = _AdversarialGatewayClient(chunk1_order_ids=[], raise_on_chunk2=False)
    replay_result = run_c3_staleness_cancel_cycle(
        trade_conn, trade_conn, forecasts_conn, replay_client, now=NOW,
    )

    assert replay_result["cancel_set_size"] == 0
    assert replay_client.cancel_calls == []

    partial_res_after_replay = _reservation_row(trade_conn, partial_command_id)
    assert partial_res_after_replay == partial_res  # byte-identical: no duplicate conversion

    cancel_acked_count_after_replay = trade_conn.execute(
        "SELECT COUNT(*) FROM venue_command_events WHERE command_id = ? AND event_type = 'CANCEL_ACKED'",
        (partial_command_id,),
    ).fetchone()[0]
    assert cancel_acked_count_after_replay == 1  # no duplicate side effect


def test_cross_packet_same_family_mixed_outcomes_suppress_whole_family_confirmation():
    """The load-bearing proof for the consult round-2 BLOCKER, at the
    integration level: two commands share ONE family. One (c-partial) has a
    partial-fill fact in flight and cancels cleanly, with W1.1 correctly
    converting its collateral to the ACTUAL matched truth -- its OWN
    accounting is individually perfect. The other (c-ambiguous) comes back
    NOT_CANCELED in the SAME batch call. confirmed_families must exclude the
    WHOLE family regardless of c-partial's clean, individually-correct
    outcome -- the family still carries c-ambiguous's unresolved venue
    exposure, so a redecision emitted "for FAMILY" would submit against that
    ambiguity."""
    trade_conn = _trade_db()
    forecasts_conn = _forecasts_db()
    _seed_collateral_snapshot(trade_conn, pusd_balance_micro=100_000_000)

    _seed_open_entry(
        trade_conn, command_id="c-partial", token_id="tok-partial", venue_order_id="v-partial",
        q_version="q-old", created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
    )
    _seed_reservation(trade_conn, command_id="c-partial", amount_micro=_RESERVE_AMOUNT_MICRO)
    _seed_open_entry(
        trade_conn, command_id="c-ambiguous", token_id="tok-ambiguous", venue_order_id="v-ambiguous",
        q_version="q-old", created_at=NOW - timedelta(minutes=DEADLINE_MIN + 5),
    )
    _seed_reservation(trade_conn, command_id="c-ambiguous", amount_micro=_RESERVE_AMOUNT_MICRO)
    # BOTH tokens resolve to the SAME family.
    _seed_market_event(forecasts_conn, token_id="tok-partial", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])
    _seed_market_event(forecasts_conn, token_id="tok-ambiguous", city=FAMILY[0], target_date=FAMILY[1], metric=FAMILY[2])

    trade_conn.execute(
        "INSERT INTO venue_order_facts (venue_order_id, command_id, state, remaining_size, matched_size, "
        "source, observed_at, local_sequence, raw_payload_hash) "
        "VALUES ('v-partial', 'c-partial', 'PARTIALLY_MATCHED', '6', '4', 'WS_USER', ?, 1, ?)",
        (NOW.isoformat(), "a" * 64),
    )
    trade_conn.commit()

    class _MixedOutcomeClient:
        def __init__(self):
            self.cancel_calls: list[list[str]] = []

        def cancel_orders_batch(self, order_ids):
            self.cancel_calls.append(list(order_ids))
            return [
                {"canceled": True, "orderID": "v-partial"},
                {"orderID": "v-ambiguous", "status": "NOT_CANCELED", "errorMessage": "still live"},
            ]

    client = _MixedOutcomeClient()

    result = run_c3_staleness_cancel_cycle(trade_conn, trade_conn, forecasts_conn, client, now=NOW)

    assert len(client.cancel_calls) == 1  # both commands fit in one chunk, one batch call

    # c-partial's OWN accounting is individually correct...
    assert get_command(trade_conn, "c-partial")["state"] == "CANCELLED"
    partial_res = _reservation_row(trade_conn, "c-partial")
    assert partial_res["converted_amount"] == 400_000  # floor(1_000_000 * 4/10), the matched truth
    assert partial_res["released_at"] is not None

    # ...c-ambiguous is untouched (no over-release)...
    ambiguous_res = _reservation_row(trade_conn, "c-ambiguous")
    assert ambiguous_res["released_at"] is None
    assert ambiguous_res["converted_amount"] == 0

    # ...but the WHOLE family is excluded, despite c-partial's clean outcome.
    assert result["confirmed_families"] == set()
