# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-a -- synthetic-fixture proof for src.reduce.position_economics.
"""Synthetic-fixture cent-exact proof + refusal matrix for the position-
economics reducer.

Every arithmetic assertion below is hand-computed in the test itself (never
compared against a legacy column -- this packet makes no cent-equivalence
claim, see src/reduce/position_economics.py module docstring).
"""
from __future__ import annotations

import pytest

from src.reduce.position_economics import (
    ConditionAttributionMissingError,
    MissingFillSyncWatermarkError,
    OversoldPositionError,
    UnmigratedIdentitySupersessionSchemaError,
    UnrecognizedIntentKindError,
    reduce_position_economics,
)
from tests.reduce.conftest import (
    insert_identity_superseded,
    insert_payout_observation,
    insert_trade_fact,
    insert_venue_command,
    seed_fill_sync_watermark,
)


def test_entry_reobserved_after_its_exits_still_folds_in_execution_order(conn):
    """Regression: a lifecycle re-observation (e.g. a REST re-confirmation)
    re-stamps an entry fill's ``observed_at`` LATER than its own exits. Folding
    by ``observed_at`` pushes the entry behind the exits and fabricates a bogus
    OversoldPositionError. The reducer must fold in EXECUTION order
    (fill_dedup's stable ``execution_ts`` -- earliest venue_timestamp, or
    earliest observed_at across revisions when no venue timestamp exists), so a
    fully-closed position folds normally instead of refusing.

    Live-observed root cause (Wellington 6f92d690 + ~10 peers, 2026-07-13):
    a REST reconciliation re-CONFIRMED settled entries hours after their exits.
    """
    seed_fill_sync_watermark(conn)
    insert_venue_command(conn, command_id="c-entry", position_id="pos-x", intent_kind="ENTRY")
    insert_venue_command(conn, command_id="c-exit", position_id="pos-x", intent_kind="EXIT")
    # Entry first observed 07:00.
    insert_trade_fact(
        conn, command_id="c-entry", trade_id="t-entry", filled_size="100",
        fill_price="0.50", state="MATCHED", observed_at="2026-07-11T07:00:00+00:00",
    )
    # Exit observed 11:00 (after the entry, before the re-observation).
    insert_trade_fact(
        conn, command_id="c-exit", trade_id="t-exit", filled_size="100",
        fill_price="0.60", state="CONFIRMED", observed_at="2026-07-11T11:00:00+00:00",
    )
    # The SAME entry trade re-observed 2 days later, re-stamped AFTER the exit.
    insert_trade_fact(
        conn, command_id="c-entry", trade_id="t-entry", filled_size="100",
        fill_price="0.50", state="CONFIRMED", observed_at="2026-07-13T22:02:00+00:00",
    )

    result = reduce_position_economics(conn, "pos-x", fill_sync_source="polymarket_v2")
    assert result.net_shares == 0.0
    # Sold 100 @ 0.60 against avg cost 0.50 -> +$10.00 realized, fees 0.
    assert result.realized_pnl_usd == pytest.approx(10.0)

CONDITION = "cond-1"
OUTCOME_INDEX = 0


def _downgrade_position_events_schema(conn) -> None:
    """Simulate a pre-F2-migration DB: position_events.event_type CHECK
    exists but does not admit POSITION_IDENTITY_SUPERSEDED (mirrors the
    LEGACY fixture shape in
    tests/test_position_events_identity_supersession_check_migration.py,
    trimmed to the columns this reducer touches)."""
    conn.execute("DROP TABLE position_events")
    conn.execute(
        """
        CREATE TABLE position_events (
            event_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            event_version INTEGER NOT NULL DEFAULT 1,
            sequence_no INTEGER NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'POSITION_OPEN_INTENT', 'ENTRY_ORDER_FILLED', 'SETTLED',
                'ADMIN_VOIDED', 'REVIEW_REQUIRED'
            )),
            occurred_at TEXT NOT NULL,
            phase_before TEXT,
            phase_after TEXT,
            strategy_key TEXT NOT NULL,
            decision_id TEXT,
            snapshot_id TEXT,
            order_id TEXT,
            command_id TEXT,
            caused_by TEXT,
            idempotency_key TEXT,
            venue_status TEXT,
            source_module TEXT NOT NULL,
            env TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.commit()


class TestBasicFold:
    def test_entry_only_open_position_is_pending_never_zero(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.net_shares == pytest.approx(10.0)
        assert econ.cost_basis_usd == pytest.approx(5.0)
        assert econ.realized_pnl_usd == pytest.approx(0.0)
        assert econ.payout_status == "PENDING"
        assert econ.payout_pnl_usd is None
        assert econ.total_realized_pnl_usd is None

    def test_entry_then_full_exit_closes_via_fills(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="10", fill_price="0.5", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c2", position_id="p1", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="10", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        econ = reduce_position_economics(conn, "p1")

        assert econ.net_shares == pytest.approx(0.0)
        assert econ.cost_basis_usd == pytest.approx(0.0)
        assert econ.realized_pnl_usd == pytest.approx(1.0)  # 10 * (0.6 - 0.5)
        assert econ.payout_status == "CLOSED_VIA_FILLS"
        assert econ.payout_pnl_usd is None
        assert econ.total_realized_pnl_usd == pytest.approx(1.0)
        assert econ.fill_count == 2

    def test_fees_capitalize_into_cost_basis_and_reduce_exit_proceeds(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn,
            command_id="c1",
            filled_size="10",
            fill_price="0.5",
            fee_paid_micro=500_000,  # $0.50
            observed_at="2026-07-13T12:00:00+00:00",
        )
        insert_venue_command(conn, command_id="c2", position_id="p1", intent_kind="EXIT")
        insert_trade_fact(
            conn,
            command_id="c2",
            filled_size="10",
            fill_price="0.6",
            fee_paid_micro=200_000,  # $0.20
            observed_at="2026-07-13T12:05:00+00:00",
        )

        econ = reduce_position_economics(conn, "p1")

        # cost_basis = 10*0.5 + 0.5 = 5.5; avg_cost = 0.55
        # realized = 10*(0.6-0.55) - 0.2 = 0.5 - 0.2 = 0.3
        assert econ.realized_pnl_usd == pytest.approx(0.3)
        assert econ.fees_usd == pytest.approx(0.7)
        assert econ.net_shares == pytest.approx(0.0)

    def test_partial_exit_weighted_average_cost(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="10", fill_price="0.4", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c2", position_id="p1", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="4", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.net_shares == pytest.approx(6.0)
        assert econ.cost_basis_usd == pytest.approx(2.4)  # 4.0 - 4*0.4
        assert econ.realized_pnl_usd == pytest.approx(0.8)  # 4*(0.6-0.4)
        assert econ.payout_status == "PENDING"


class TestAliasDedup:
    def test_tx_hash_aggregate_vs_exact_child_counts_once(self, conn):
        """Mirrors src.state.fill_dedup's own alias-graph exactly-once
        property: an aggregate row (trade_id == tx_hash) and an exact child
        row sharing that tx_hash are the SAME economic fill -- the reducer
        must fold it exactly once, delegated entirely to
        economic_trade_facts_for_command."""
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn,
            command_id="c1",
            trade_id="0xabc",
            tx_hash="0xabc",
            filled_size="10",
            fill_price="0.5",
        )
        insert_trade_fact(
            conn,
            command_id="c1",
            trade_id="child-1",
            tx_hash="0xabc",
            filled_size="10",
            fill_price="0.5",
        )

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.net_shares == pytest.approx(10.0)
        assert econ.cost_basis_usd == pytest.approx(5.0)
        assert econ.fill_count == 1


class TestIdentitySupersession:
    def test_absorbed_position_folds_into_keeper(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="keeper", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="10", fill_price="0.4", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c2", position_id="absorbed", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="4", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )
        insert_identity_superseded(
            conn, keeper_position_id="keeper", absorbed_position_ids=["absorbed"]
        )

        econ_via_keeper = reduce_position_economics(
            conn, "keeper", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )
        econ_via_absorbed = reduce_position_economics(
            conn, "absorbed", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        # Same combined identity group either way in -- same numbers out.
        assert econ_via_keeper.keeper_position_id == "keeper"
        assert econ_via_absorbed.keeper_position_id == "keeper"
        assert econ_via_absorbed.absorbed_position_ids == ("absorbed",)
        for econ in (econ_via_keeper, econ_via_absorbed):
            assert econ.net_shares == pytest.approx(6.0)
            assert econ.cost_basis_usd == pytest.approx(2.4)
            assert econ.realized_pnl_usd == pytest.approx(0.8)
            assert econ.fill_count == 2


class TestPayout:
    def test_resolved_nonzero_realizes_remaining_shares(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")
        insert_payout_observation(
            conn,
            condition_id=CONDITION,
            outcome_index=OUTCOME_INDEX,
            state="RESOLVED_NONZERO",
            payout_numerator=1,
            payout_denominator=1,
        )

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.payout_status == "RESOLVED_NONZERO"
        # payout = 10*1.0 - cost_basis(5.0) = 5.0
        assert econ.payout_pnl_usd == pytest.approx(5.0)
        assert econ.total_realized_pnl_usd == pytest.approx(5.0)

    def test_resolved_zero_is_a_full_loss_never_a_silent_zero_marker(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")
        insert_payout_observation(
            conn,
            condition_id=CONDITION,
            outcome_index=OUTCOME_INDEX,
            state="RESOLVED_ZERO",
            payout_numerator=0,
            payout_denominator=1,
        )

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.payout_status == "RESOLVED_ZERO"
        assert econ.payout_pnl_usd == pytest.approx(-5.0)  # 10*0 - 5.0
        assert econ.total_realized_pnl_usd == pytest.approx(-5.0)

    @pytest.mark.parametrize("state", ["UNKNOWN", "UNRESOLVED"])
    def test_unknown_and_unresolved_never_collapse_to_zero(self, conn, state):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")
        kwargs = {"payout_denominator": 0} if state == "UNRESOLVED" else {}
        insert_payout_observation(
            conn, condition_id=CONDITION, outcome_index=OUTCOME_INDEX, state=state, **kwargs
        )

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.payout_status == "PENDING"
        assert econ.payout_pnl_usd is None
        assert econ.total_realized_pnl_usd is None

    def test_absent_payout_observation_is_pending_not_zero(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        econ = reduce_position_economics(
            conn, "p1", condition_id=CONDITION, outcome_index=OUTCOME_INDEX
        )

        assert econ.payout_status == "PENDING"
        assert econ.payout_pnl_usd is None


class TestRefusalMatrix:
    """Each case names the exact missing input -- fail-closed is a feature."""

    def test_missing_fill_sync_watermark_refuses(self, conn):
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        with pytest.raises(MissingFillSyncWatermarkError, match="polymarket_v2"):
            reduce_position_economics(conn, "p1")

    def test_unmigrated_identity_supersession_schema_refuses(self, conn):
        seed_fill_sync_watermark(conn)
        _downgrade_position_events_schema(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        with pytest.raises(
            UnmigratedIdentitySupersessionSchemaError,
            match="POSITION_IDENTITY_SUPERSEDED",
        ):
            reduce_position_economics(conn, "p1")

    def test_oversold_position_refuses(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="5", fill_price="0.5", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c2", position_id="p1", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="10", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        with pytest.raises(OversoldPositionError):
            reduce_position_economics(conn, "p1")

    def test_unrecognized_intent_kind_with_economic_facts_refuses(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="CANCEL")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        with pytest.raises(UnrecognizedIntentKindError, match="CANCEL"):
            reduce_position_economics(conn, "p1")

    def test_condition_attribution_missing_refuses_when_shares_open(self, conn):
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        with pytest.raises(ConditionAttributionMissingError):
            reduce_position_economics(conn, "p1")

    def test_closed_via_fills_position_does_not_need_condition_attribution(self, conn):
        """A fully-sold position never needs payout truth -- no refusal."""
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="10", fill_price="0.5", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c2", position_id="p1", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="10", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        econ = reduce_position_economics(conn, "p1")  # no condition_id/outcome_index
        assert econ.payout_status == "CLOSED_VIA_FILLS"
