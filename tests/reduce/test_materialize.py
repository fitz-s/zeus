# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-b -- materializes the existing position_economics reducer +
#   condition_resolver into ONE published src.reduce.generation.Generation.
#   Fold/refusal arithmetic is never reimplemented here -- these tests prove
#   the orchestration (enumerate -> resolve -> reduce -> dedupe-by-keeper ->
#   publish), not the arithmetic (already proved in
#   tests/reduce/test_position_economics.py).
"""Tests for src.reduce.materialize: whole-corpus generation materialization."""
from __future__ import annotations

import pytest

from src.reduce.generation import GenerationStore
from src.reduce.materialize import materialize_generation
from src.reduce.position_economics import ConditionAttributionMissingError
from tests.reduce.conftest import (
    NOW,
    insert_identity_superseded,
    insert_payout_observation,
    insert_position_current,
    insert_trade_fact,
    insert_venue_command,
    seed_fill_sync_watermark,
)

CONDITION = "0xcond1"


class TestBasicMaterialization:
    def test_materializes_all_real_positions_excluding_chain_only(self, conn):
        seed_fill_sync_watermark(conn)
        insert_position_current(conn, position_id="p1", condition_id=CONDITION, direction="buy_yes")
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        insert_position_current(conn, position_id="chain-only-abc", condition_id=None, direction=None)

        result = materialize_generation(conn, computed_at=NOW)

        assert result.total_enumerated == 1  # chain-only-abc excluded from the count
        assert [e.position_id for e in result.economics] == ["p1"]
        assert result.refusals == ()

    def test_generation_publishes_and_round_trips(self, conn):
        seed_fill_sync_watermark(conn)
        insert_position_current(conn, position_id="p1", condition_id=None, direction=None)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="10", fill_price="0.5", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c2", position_id="p1", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="10", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        result = materialize_generation(conn, computed_at=NOW)

        store = GenerationStore(conn)
        fetched = store.get(result.generation.generation_id)
        assert fetched is not None
        assert fetched.position_ids == ("p1",)
        rows = store.economics_for(result.generation.generation_id)
        assert len(rows) == 1
        assert rows[0]["realized_pnl_usd"] == pytest.approx(1.0)
        assert rows[0]["payout_status"] == "CLOSED_VIA_FILLS"

    def test_position_with_zero_commands_materializes_as_zero_never_refuses(self, conn):
        """Mirrors the live corpus: 128/970 real positions have no
        venue_commands row at all. net_shares folds to 0 regardless of
        whether condition_id resolves -- the reducer never asks for
        payout truth it doesn't need, so this must NOT show up as a
        refusal even though condition_id is unresolvable here."""
        seed_fill_sync_watermark(conn)
        insert_position_current(conn, position_id="p-no-commands", condition_id=None, direction=None)

        result = materialize_generation(conn, computed_at=NOW)

        assert result.refusals == ()
        assert len(result.economics) == 1
        econ = result.economics[0]
        assert econ.net_shares == pytest.approx(0.0)
        assert econ.payout_status == "CLOSED_VIA_FILLS"

    def test_coverage_vector_reads_the_actual_watermark_row(self, conn):
        seed_fill_sync_watermark(conn, source="polymarket_v2", watermark_ts="2026-07-13T09:30:00+00:00")
        insert_position_current(conn, position_id="p1", condition_id=None, direction=None)

        result = materialize_generation(conn, computed_at=NOW, fill_sync_source="polymarket_v2")

        assert result.generation.coverage.fill_sync_watermarks == {
            "polymarket_v2": "2026-07-13T09:30:00+00:00"
        }
        assert result.generation.coverage.payout_observation_complete is False


class TestRefusalCoverageReport:
    def test_refused_position_not_folded_into_generation(self, conn):
        seed_fill_sync_watermark(conn)
        # Open shares, unresolvable condition/direction -> reducer refuses.
        insert_position_current(conn, position_id="p-open", condition_id=None, direction=None)
        insert_venue_command(conn, command_id="c1", position_id="p-open", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        insert_position_current(conn, position_id="p-closed", condition_id=None, direction=None)
        insert_venue_command(conn, command_id="c2", position_id="p-closed", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c2", filled_size="5", fill_price="0.5", observed_at="2026-07-13T12:00:00+00:00"
        )
        insert_venue_command(conn, command_id="c3", position_id="p-closed", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c3", filled_size="5", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        result = materialize_generation(conn, computed_at=NOW)

        assert [e.position_id for e in result.economics] == ["p-closed"]
        assert len(result.refusals) == 1
        assert result.refusals[0].position_id == "p-open"
        assert result.refusals[0].refusal_type == "ConditionAttributionMissingError"
        assert result.total_enumerated == 2

    def test_coverage_report_counts_refusals_by_type(self, conn):
        seed_fill_sync_watermark(conn)
        insert_position_current(conn, position_id="p-open-1", condition_id=None, direction=None)
        insert_venue_command(conn, command_id="c1", position_id="p-open-1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")

        insert_position_current(conn, position_id="p-open-2", condition_id=None, direction=None)
        insert_venue_command(conn, command_id="c2", position_id="p-open-2", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c2", filled_size="3", fill_price="0.5")

        result = materialize_generation(conn, computed_at=NOW)

        assert result.refusal_counts_by_type == {"ConditionAttributionMissingError": 2}

    def test_reducer_refusal_still_raised_for_direct_callers(self, conn):
        """Sanity: materialize.py never swallows the reducer's own refusal
        type -- it just catches it at the orchestration boundary. Direct
        reducer calls (as every existing test does) are untouched."""
        seed_fill_sync_watermark(conn)
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")
        from src.reduce.position_economics import reduce_position_economics

        with pytest.raises(ConditionAttributionMissingError):
            reduce_position_economics(conn, "p1")


class TestIdentitySupersessionDedup:
    def test_absorbed_duplicate_not_double_counted(self, conn):
        seed_fill_sync_watermark(conn)
        # Both rows carry a resolvable condition/direction so BOTH raw ids'
        # own reduce_position_economics calls succeed independently (net
        # shares stay open post-fold: 10 - 4 = 6) -- this isolates the dedup
        # behavior from the separately-tested refusal-passthrough behavior
        # (TestRefusalCoverageReport already covers the case where a raw
        # id's own attribution is unresolvable).
        insert_position_current(conn, position_id="keeper", condition_id=CONDITION, direction="buy_yes")
        insert_venue_command(conn, command_id="c1", position_id="keeper", intent_kind="ENTRY")
        insert_trade_fact(
            conn, command_id="c1", filled_size="10", fill_price="0.4", observed_at="2026-07-13T12:00:00+00:00"
        )

        insert_position_current(conn, position_id="absorbed", condition_id=CONDITION, direction="buy_yes")
        insert_venue_command(conn, command_id="c2", position_id="absorbed", intent_kind="EXIT")
        insert_trade_fact(
            conn, command_id="c2", filled_size="4", fill_price="0.6", observed_at="2026-07-13T12:05:00+00:00"
        )

        insert_identity_superseded(
            conn, keeper_position_id="keeper", absorbed_position_ids=["absorbed"]
        )

        result = materialize_generation(conn, computed_at=NOW)

        assert [e.position_id for e in result.economics] == ["keeper"]
        assert result.absorbed_duplicate_position_ids == ("absorbed",)
        # 2 raw positions enumerated; 1 published row; 1 absorbed-away; 0 refused.
        assert result.total_enumerated == 2
        assert len(result.economics) + len(result.refusals) + len(result.absorbed_duplicate_position_ids) == 2

        econ = result.economics[0]
        assert econ.net_shares == pytest.approx(6.0)
        assert econ.cost_basis_usd == pytest.approx(2.4)
        assert econ.realized_pnl_usd == pytest.approx(0.8)

    def test_reconciliation_invariant_materialized_plus_refused_plus_absorbed_equals_enumerated(self, conn):
        seed_fill_sync_watermark(conn)
        # keeper/absorbed pair (folds to 1 published row)
        insert_position_current(conn, position_id="keeper", condition_id=None, direction=None)
        insert_position_current(conn, position_id="absorbed", condition_id=None, direction=None)
        insert_identity_superseded(conn, keeper_position_id="keeper", absorbed_position_ids=["absorbed"])
        # a clean standalone position
        insert_position_current(conn, position_id="solo", condition_id=None, direction=None)
        # a refusing position (open shares, unresolvable attribution)
        insert_position_current(conn, position_id="p-open", condition_id=None, direction=None)
        insert_venue_command(conn, command_id="c1", position_id="p-open", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="1", fill_price="0.5")

        result = materialize_generation(conn, computed_at=NOW)

        assert result.total_enumerated == 4
        total = (
            len(result.economics)
            + len(result.refusals)
            + len(result.absorbed_duplicate_position_ids)
        )
        assert total == result.total_enumerated


class TestDeterminism:
    def test_two_runs_over_identical_corpus_agree(self, conn):
        seed_fill_sync_watermark(conn)
        insert_position_current(conn, position_id="p1", condition_id=CONDITION, direction="buy_no")
        insert_venue_command(conn, command_id="c1", position_id="p1", intent_kind="ENTRY")
        insert_trade_fact(conn, command_id="c1", filled_size="10", fill_price="0.5")
        insert_payout_observation(
            conn,
            condition_id=CONDITION,
            outcome_index=1,
            state="RESOLVED_ZERO",
            payout_numerator=0,
            payout_denominator=1,
        )

        result1 = materialize_generation(conn, computed_at="2026-07-13T12:00:00+00:00")
        result2 = materialize_generation(conn, computed_at="2026-07-13T13:00:00+00:00")

        assert result1.generation.generation_id != result2.generation.generation_id
        assert result1.generation.input_fingerprint == result2.generation.input_fingerprint

        econ1 = {e.position_id: e for e in result1.economics}["p1"]
        econ2 = {e.position_id: e for e in result2.economics}["p1"]
        assert econ1.realized_pnl_usd == econ2.realized_pnl_usd
        assert econ1.payout_pnl_usd == econ2.payout_pnl_usd == pytest.approx(-5.0)
        assert econ1.total_realized_pnl_usd == econ2.total_realized_pnl_usd
