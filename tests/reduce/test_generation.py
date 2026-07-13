# Created: 2026-07-13
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-2R-a "Read-model 诚实不变量" -- generation publication must be
#   all-or-nothing, never per-row latest.
"""Tests for src.reduce.generation: contract + table-backed store."""
from __future__ import annotations

import pytest

from src.reduce.generation import (
    CoverageVector,
    GenerationAlreadyPublishedError,
    GenerationPositionSetMismatchError,
    GenerationStore,
    build_generation,
    compute_input_fingerprint,
)
from src.reduce.position_economics import (
    REDUCER_VERSION,
    PositionEconomics,
    reduce_position_economics,
)
from tests.reduce.conftest import (
    insert_trade_fact,
    insert_venue_command,
    seed_fill_sync_watermark,
)


def _coverage() -> CoverageVector:
    return CoverageVector(
        fill_sync_watermarks={"polymarket_v2": "2026-07-13T12:00:00+00:00"},
        payout_observation_complete=False,
        supersession_backfill_marker=None,
    )


def _fixture_economics(position_id: str) -> PositionEconomics:
    return PositionEconomics(
        position_id=position_id,
        keeper_position_id=position_id,
        absorbed_position_ids=(),
        reducer_version=REDUCER_VERSION,
        net_shares=0.0,
        cost_basis_usd=0.0,
        realized_pnl_usd=1.0,
        fees_usd=0.0,
        fill_count=2,
        payout_status="CLOSED_VIA_FILLS",
        payout_pnl_usd=None,
        contributions=(),
    )


class TestCoverageVectorAndFingerprint:
    def test_fingerprint_is_stable_across_dict_ordering(self):
        a = CoverageVector(
            fill_sync_watermarks={"b": "2", "a": "1"},
            payout_observation_complete=True,
            supersession_backfill_marker="marker-1",
        )
        b = CoverageVector(
            fill_sync_watermarks={"a": "1", "b": "2"},
            payout_observation_complete=True,
            supersession_backfill_marker="marker-1",
        )
        assert a.fingerprint() == b.fingerprint()

    def test_fingerprint_changes_with_coverage(self):
        a = _coverage()
        b = CoverageVector(
            fill_sync_watermarks={"polymarket_v2": "2026-07-13T13:00:00+00:00"},
            payout_observation_complete=False,
            supersession_backfill_marker=None,
        )
        assert a.fingerprint() != b.fingerprint()

    def test_input_fingerprint_ignores_position_id_ordering(self):
        cov = _coverage()
        fp1 = compute_input_fingerprint(["p1", "p2"], cov, REDUCER_VERSION)
        fp2 = compute_input_fingerprint(["p2", "p1"], cov, REDUCER_VERSION)
        assert fp1 == fp2

    def test_input_fingerprint_changes_with_reducer_version(self):
        cov = _coverage()
        fp1 = compute_input_fingerprint(["p1"], cov, "v1")
        fp2 = compute_input_fingerprint(["p1"], cov, "v2")
        assert fp1 != fp2


class TestGenerationStorePublication:
    def test_publish_then_get_round_trips(self, conn):
        econ = _fixture_economics("p1")
        gen = build_generation(
            position_ids=["p1"], coverage=_coverage(), computed_at="2026-07-13T12:00:00+00:00"
        )
        store = GenerationStore(conn)
        store.publish(gen, [econ])

        fetched = store.get(gen.generation_id)
        assert fetched is not None
        assert fetched.generation_id == gen.generation_id
        assert fetched.reducer_version == REDUCER_VERSION
        assert fetched.position_ids == ("p1",)
        assert fetched.coverage.fingerprint() == gen.coverage.fingerprint()

        rows = store.economics_for(gen.generation_id)
        assert len(rows) == 1
        assert rows[0]["position_id"] == "p1"
        assert rows[0]["realized_pnl_usd"] == pytest.approx(1.0)

    def test_latest_returns_the_most_recently_computed_generation(self, conn):
        store = GenerationStore(conn)
        gen1 = build_generation(
            position_ids=["p1"], coverage=_coverage(), computed_at="2026-07-13T12:00:00+00:00"
        )
        store.publish(gen1, [_fixture_economics("p1")])
        gen2 = build_generation(
            position_ids=["p1"], coverage=_coverage(), computed_at="2026-07-13T13:00:00+00:00"
        )
        store.publish(gen2, [_fixture_economics("p1")])

        latest = store.latest()
        assert latest is not None
        assert latest.generation_id == gen2.generation_id

    def test_duplicate_generation_id_refuses(self, conn):
        store = GenerationStore(conn)
        gen = build_generation(
            position_ids=["p1"], coverage=_coverage(), computed_at="2026-07-13T12:00:00+00:00"
        )
        store.publish(gen, [_fixture_economics("p1")])

        with pytest.raises(GenerationAlreadyPublishedError):
            store.publish(gen, [_fixture_economics("p1")])

    def test_position_set_mismatch_refuses_before_writing_anything(self, conn):
        store = GenerationStore(conn)
        gen = build_generation(
            position_ids=["p1", "p2"], coverage=_coverage(), computed_at="2026-07-13T12:00:00+00:00"
        )

        with pytest.raises(GenerationPositionSetMismatchError):
            store.publish(gen, [_fixture_economics("p1")])  # missing p2's row

        # All-or-nothing: the rejected publish must not have left a
        # generation row with no economics behind it.
        assert store.get(gen.generation_id) is None

    def test_publish_is_all_or_nothing_on_integrity_failure(self, conn):
        """A row that violates the position_economics CHECK must roll back
        the whole publish, including the generation row itself."""
        store = GenerationStore(conn)
        bad_econ = PositionEconomics(
            position_id="p1",
            keeper_position_id="p1",
            absorbed_position_ids=(),
            reducer_version=REDUCER_VERSION,
            net_shares=0.0,
            cost_basis_usd=0.0,
            realized_pnl_usd=0.0,
            fees_usd=0.0,
            fill_count=0,
            payout_status="NOT_A_REAL_STATUS",  # violates the CHECK
            payout_pnl_usd=None,
            contributions=(),
        )
        gen = build_generation(
            position_ids=["p1"], coverage=_coverage(), computed_at="2026-07-13T12:00:00+00:00"
        )

        with pytest.raises(Exception):
            store.publish(gen, [bad_econ])

        assert store.get(gen.generation_id) is None


class TestEndToEndWithReducer:
    def test_reducer_output_publishes_cleanly(self, conn):
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

        store = GenerationStore(conn)
        gen = build_generation(
            position_ids=["p1"], coverage=_coverage(), computed_at="2026-07-13T12:10:00+00:00"
        )
        store.publish(gen, [econ])

        rows = store.economics_for(gen.generation_id)
        assert rows[0]["realized_pnl_usd"] == pytest.approx(1.0)
        assert rows[0]["payout_status"] == "CLOSED_VIA_FILLS"
