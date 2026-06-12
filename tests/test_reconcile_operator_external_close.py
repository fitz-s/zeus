# Created: 2026-06-10
# Last reused or audited: 2026-06-10
# Authority basis: operator external-close incident chain 2026-06-10 — the operator
#   manually SOLD Zeus's Milan position (66.25 YES @0.016, token prefix 13288697) on the
#   SHARED proxy wallet. When the order FILLED, chain_sync VOIDED the position but the
#   void created a terminal_position_current_chain_holdings entry (66.25) WITHOUT
#   consuming the journal buy-claim (66.25) with an offsetting sell fact. The drift
#   detector's expected_wallet then DOUBLE-COUNTED the same 66.25 economic claim
#   (journal 66.25 + closed-holdings 66.25 = 132.50) vs exchange 0 -> position_drift
#   re-records forever. A stopgap auto-resolver was masking it.
"""RELATIONSHIP tests: reconcile sweep -> journal/closed-holdings -> drift latch.

Cross-module invariant (run_reconcile_sweep over the position_drift boundary):
  When a position's tokens leave the wallet via an OPERATOR-CONFIRMED external fill
  (an operator-acknowledged resolution row exists for the SAME subject token), the
  bookkeeping must CONVERGE: the external close is booked as a SELL exit fact that
  consumes the journal buy-claim, the dangling voided-position terminal holdings are
  tagged out of the closed-position-holdings view, and after absorption
  expected_wallet == 0 == exchange so NO finding is recorded on re-sweep.

  STRICTNESS is preserved as a property across the boundary: the SAME double-count
  shape WITHOUT an operator-ack resolution row stays fail-closed and records a finding.
"""
from __future__ import annotations

from datetime import timedelta

from src.contracts.semantic_types import ChainState, VenueVisibilityStatus
from src.execution.exchange_reconcile import (
    _CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES,
    _EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE,
    record_finding,
    resolve_finding,
    run_reconcile_sweep,
)

# Reuse the rich init_schema-backed fixtures + seed helpers from the main M5 suite.
from tests.test_exchange_reconcile import (  # noqa: F401  (conn is a pytest fixture)
    NOW,
    FakeM5Adapter,
    append_trade_fact,
    conn,
    position,
    seed_command,
    seed_position_baseline,
)

TOKEN = "operator-external-close-token"


def _seed_journal_long(conn) -> None:
    """A FILLED entry BUY with a CONFIRMED buy trade fact -> journal long = 66.25.

    Mirrors the LIVE Milan shape: the entry command's position_id (live: 5b676923-707)
    does NOT match the later voided position_current id (live: edlibffd...). The
    _journal_positions_by_token LEFT JOIN therefore finds no position_current for the
    command (pc.position_id IS NULL branch) and INCLUDES the buy fact -> journal counts
    66.25. This is exactly why the journal AND the voided-position closed-holdings both
    represent the same 66.25 and double-count.
    """

    # price 0.02 is tick-aligned for the test snapshot (min_tick 0.01) so the strict
    # seed_command/insert_command submission gate passes. The live entry was 0.016; price
    # is irrelevant to the size-driven absorption — only the journal SIZE drives the latch.
    seed_command(
        conn,
        command_id="cmd-ext-close",
        venue_order_id="ord-ext-close",
        position_id="cmd-pos-ext-close",
        token_id=TOKEN,
        state="FILLED",
        side="BUY",
        size=66.25,
        price=0.02,
    )
    append_trade_fact(
        conn,
        command_id="cmd-ext-close",
        venue_order_id="ord-ext-close",
        token_id=TOKEN,
        trade_id="trade-ext-close-buy",
        size="66.25",
        fill_price="0.02",
        state="CONFIRMED",
    )


def _seed_voided_holding(conn) -> None:
    """The void misbooking: a terminal voided position still holding the token on-chain."""

    seed_position_baseline(
        conn, position_id="pos-ext-close", order_id="ord-ext-close"
    )
    conn.execute(
        """
        UPDATE position_current
           SET phase = 'voided',
               chain_state = 'synced',
               token_id = ?,
               condition_id = 'condition-m5',
               market_id = 'condition-m5',
               direction = 'buy_yes',
               shares = 66.25,
               chain_shares = 66.25,
               order_id = 'ord-ext-close',
               updated_at = ?
         WHERE position_id = 'pos-ext-close'
        """,
        (TOKEN, NOW.isoformat()),
    )


def _operator_acknowledge_drift(conn, *, resolved_by: str, resolution: str) -> None:
    """Record then resolve a position_drift finding the way the operator did live."""

    finding = record_finding(
        conn,
        kind="position_drift",
        subject_id=TOKEN,
        context="ws_gap",
        evidence={"token_id": TOKEN, "reason": "operator_external_close_probe"},
        recorded_at=NOW,
    )
    resolve_finding(
        conn,
        finding.finding_id,
        resolution=resolution,
        resolved_by=resolved_by,
        resolved_at=NOW,
    )


def _sweep_empty_wallet(conn):
    """A sweep where the exchange reports the token wallet flat (operator sold it)."""

    return run_reconcile_sweep(
        FakeM5Adapter(positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=10),
    )


# ---- RELATIONSHIP: operator-ack double-count is absorbed; re-sweep is clean -----------
def test_operator_ack_external_close_absorbs_double_count_no_finding_on_resweep(conn):
    _seed_journal_long(conn)
    _seed_voided_holding(conn)
    _operator_acknowledge_drift(
        conn,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_filled: SELL 66.25 YES @0.016",
    )

    first = _sweep_empty_wallet(conn)
    assert not any(f.kind == "position_drift" for f in first), (
        "the operator-confirmed external close must be ABSORBED, not re-recorded as "
        "the 132.50-vs-0 double-count drift"
    )

    # (a) external close booked as a SELL exit fact consuming the journal buy-claim
    sell_fact = conn.execute(
        "SELECT state, filled_size, source, raw_payload_json FROM venue_trade_facts "
        "WHERE trade_id LIKE 'external_operator_close_fact:%'"
    ).fetchone()
    assert sell_fact is not None
    assert sell_fact["source"] == "OPERATOR"
    assert str(sell_fact["filled_size"]) == "66.25"
    import json as _json

    payload = _json.loads(sell_fact["raw_payload_json"])
    assert payload["classification"] == "external_operator_close"
    assert payload["price_basis"] == "operator_limit"
    sell_cmd = conn.execute(
        "SELECT side, intent_kind FROM venue_commands WHERE command_id LIKE "
        "'external_operator_close:%'"
    ).fetchone()
    assert (sell_cmd["side"], sell_cmd["intent_kind"]) == ("SELL", "EXIT")

    # (b) the dangling voided-position holding is tagged out of closed-holdings
    chain_state = conn.execute(
        "SELECT chain_state, chain_shares FROM position_current WHERE position_id = 'pos-ext-close'"
    ).fetchone()
    assert chain_state["chain_state"] == _EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE
    assert float(chain_state["chain_shares"]) == 0.0

    # (c) expected_wallet == 0 == exchange -> NO finding on re-sweep (idempotent)
    second = run_reconcile_sweep(
        FakeM5Adapter(positions=[]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=11),
    )
    assert not any(f.kind == "position_drift" for f in second)
    unresolved = conn.execute(
        "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE kind='position_drift' "
        "AND subject_id=? AND resolved_at IS NULL",
        (TOKEN,),
    ).fetchone()[0]
    assert unresolved == 0

    # re-sweep does not double-book a second exit fact
    n_facts = conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id LIKE "
        "'external_operator_close_fact:%'"
    ).fetchone()[0]
    assert n_facts == 1, "absorption is idempotent — one external-close fact, not one per sweep"


def test_operator_ack_via_operator_manual_prefix_only(conn):
    # The prefix path (resolved_by some other marker, resolution operator_manual*).
    _seed_journal_long(conn)
    _seed_voided_holding(conn)
    _operator_acknowledge_drift(
        conn,
        resolved_by="src.execution.exchange_reconcile",
        resolution="operator_manual_unwind_filled: SELL leg",
    )
    first = _sweep_empty_wallet(conn)
    assert not any(f.kind == "position_drift" for f in first)


# ---- STRICTNESS: same double-count WITHOUT operator-ack still records a finding -------
def test_double_count_without_operator_ack_records_finding(conn):
    _seed_journal_long(conn)
    _seed_voided_holding(conn)
    # NO operator acknowledgment row for this subject.
    result = _sweep_empty_wallet(conn)
    drift = [f for f in result if f.kind == "position_drift" and f.subject_id == TOKEN]
    assert len(drift) == 1, (
        "an unexplained drift is never auto-absorbed — strictness preserved; absorption "
        "requires an operator-acknowledged resolution row for the SAME subject"
    )
    # No external-close fact was booked.
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id LIKE "
        "'external_operator_close_fact:%'"
    ).fetchone()[0] == 0


def test_unrelated_resolved_finding_is_not_an_operator_ack(conn):
    # A resolved finding that is NOT operator-acknowledged (e.g. a normal cleared drift)
    # must not license absorption.
    _seed_journal_long(conn)
    _seed_voided_holding(conn)
    _operator_acknowledge_drift(
        conn,
        resolved_by="src.execution.exchange_reconcile",
        resolution="position_drift_cleared",
    )
    result = _sweep_empty_wallet(conn)
    assert any(
        f.kind == "position_drift" and f.subject_id == TOKEN for f in result
    ), "a non-operator resolution does not authorize external-close absorption"


# ---- STRICTNESS: exchange holding MORE than journal is a different disease ------------
def test_exchange_above_journal_is_not_external_close(conn):
    # Operator-ack present, but the exchange wallet is ABOVE the journal long (tokens did
    # not leave — the opposite of an external close). Without the voided closed-holding the
    # expected_wallet is just the journal 66.25, exchange is 132.5 -> a real (unrecorded
    # acquisition) drift. Absorption must NOT fire and must NOT book an external-close fact.
    _seed_journal_long(conn)
    _operator_acknowledge_drift(
        conn,
        resolved_by="session_operator_confirmed",
        resolution="operator_manual_unwind_filled: SELL leg",
    )
    result = run_reconcile_sweep(
        FakeM5Adapter(positions=[position(token_id=TOKEN, size="132.5")]),
        conn,
        context="ws_gap",
        observed_at=NOW + timedelta(minutes=10),
    )
    assert any(
        f.kind == "position_drift" and f.subject_id == TOKEN for f in result
    ), "exchange above journal is a different disease and is never absorbed as a close"
    assert conn.execute(
        "SELECT COUNT(*) FROM venue_trade_facts WHERE trade_id LIKE "
        "'external_operator_close_fact:%'"
    ).fetchone()[0] == 0


# ---- CONTRACT: writer-set ⊆ ChainState enum ⊆ consumer-handled-set --------------------
#
# 2026-06-10 live defect: the absorption writer produced chain_state='external_operator_closed'
# but that value was NOT a ChainState enum member. Position.__post_init__ coerces the column
# via VenueVisibilityStatus(value), which raised "not a valid ChainState" and crash-looped the
# riskguard-live tick every cycle -> allocator_not_configured -> all submits blocked. The
# antibody makes the UNKNOWN-STATE category unconstructible: every chain_state value any writer
# can produce MUST be an enum member, every enum member MUST coerce on the Position dataclass,
# and the new terminal value MUST be classified (excluded from drift expected-wallet holdings).
def test_absorption_chain_state_is_a_valid_enum_member():
    # The exact value the absorption writer stamps onto position_current.chain_state.
    assert _EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE in {c.value for c in ChainState}, (
        "every chain_state value the absorption writer produces must be a ChainState member "
        "— the riskguard crash-loop disease"
    )


def test_every_chain_state_enum_member_coerces_on_position_dataclass():
    # Position.__post_init__ coerces chain_state via VenueVisibilityStatus(value); the
    # consumer (riskguard loads positions through portfolio) must construct EVERY member
    # without raising. This proves enum ⊆ consumer-handled-set for the coercion boundary.
    for member in ChainState:
        assert VenueVisibilityStatus(member.value) is member


def test_external_operator_closed_is_excluded_from_drift_closed_holdings():
    # The terminal closed-class value is DELIBERATELY outside the closed-position-holdings
    # set, so a position tagged external_operator_closed contributes NO expected-wallet
    # holding (the single-count guarantee that kills the 132.50-vs-0 double-count).
    assert (
        _EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE
        not in _CLOSED_POSITION_WALLET_HOLDING_CHAIN_STATES
    )


def test_position_dataclass_accepts_external_operator_closed_chain_state():
    # End-to-end coercion: a Position row carrying the new chain_state must build, not crash
    # (the literal reproduction of the riskguard-live tick failure).
    from src.state.portfolio import Position

    pos = Position(
        trade_id="ext-close-coerce",
        market_id="m",
        city="Milan",
        cluster="Milan",
        target_date="2026-06-11",
        bin_label="high-bin",
        direction="buy_yes",
        state="voided",
        chain_state=_EXTERNAL_OPERATOR_CLOSED_CHAIN_STATE,
        temperature_metric="high",
    )
    assert pos.chain_state is ChainState.EXTERNAL_OPERATOR_CLOSED
