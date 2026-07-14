# Created: 2026-07-14
# Authority basis: docs/rebuild/local_ledger_excision_2026-07-12.md packet
#   LX-3R -- proves the read-model accessor future money-path readers switch
#   to at the coordinated cutover: keeper resolution, whole-generation
#   scoping (never per-row latest), and the UNKNOWN-never-zero None contract.
"""Tests for src.reduce.read_model: latest_generation_id / latest_position_economics.

Setup goes straight through ``GenerationStore.publish`` with hand-built
``PositionEconomics`` rows (mirroring tests/reduce/test_generation.py's own
``_fixture_economics`` idiom) rather than the full ``materialize_generation``
pipeline -- this module only cares what is already sitting in
``reduce_generations`` / ``reduce_position_economics``, not how it got there
(that pipeline is separately proved in tests/reduce/test_materialize.py).
"""
from __future__ import annotations

import pytest

from src.reduce.generation import CoverageVector, GenerationStore, build_generation
from src.reduce.position_economics import REDUCER_VERSION, PositionEconomics
from src.reduce.read_model import latest_generation_id, latest_position_economics
from tests.reduce.conftest import insert_identity_superseded

_COVERAGE = CoverageVector(
    fill_sync_watermarks={"polymarket_v2": "2026-07-14T00:00:00+00:00"},
    payout_observation_complete=False,
    supersession_backfill_marker=None,
)


def _econ(position_id: str, *, realized_pnl_usd: float = 1.0, **overrides) -> PositionEconomics:
    defaults = dict(
        position_id=position_id,
        keeper_position_id=position_id,
        absorbed_position_ids=(),
        reducer_version=REDUCER_VERSION,
        net_shares=0.0,
        cost_basis_usd=0.0,
        realized_pnl_usd=realized_pnl_usd,
        fees_usd=0.0,
        fill_count=1,
        payout_status="CLOSED_VIA_FILLS",
        payout_pnl_usd=None,
        contributions=(),
    )
    defaults.update(overrides)
    return PositionEconomics(**defaults)


def _publish(conn, *, position_ids, computed_at, generation_id=None, econ_by_id=None):
    store = GenerationStore(conn)
    econ_by_id = econ_by_id or {pid: _econ(pid) for pid in position_ids}
    gen = build_generation(
        position_ids=list(econ_by_id.keys()),
        coverage=_COVERAGE,
        computed_at=computed_at,
        generation_id=generation_id,
    )
    store.publish(gen, list(econ_by_id.values()))
    return gen


class TestLatestGenerationId:
    def test_none_when_table_does_not_exist_yet(self, conn):
        """Pre-cutover trade DB: init_schema/init_schema_trade_only never
        create reduce_generations (src/reduce/generation.py module docstring:
        "NOT wired into src.state.db's init paths")."""
        assert latest_generation_id(conn) is None

    def test_none_when_table_exists_but_empty(self, conn):
        GenerationStore(conn)  # constructs -> ensure_tables, publishes nothing
        assert latest_generation_id(conn) is None

    def test_returns_the_most_recently_computed_generation_id(self, conn):
        gen1 = _publish(conn, position_ids=["p1"], computed_at="2026-07-14T12:00:00+00:00")
        gen2 = _publish(conn, position_ids=["p1"], computed_at="2026-07-14T13:00:00+00:00")

        assert latest_generation_id(conn) == gen2.generation_id
        assert latest_generation_id(conn) != gen1.generation_id


class TestNoneNeverZero:
    """Explicit regression for the money-path UNKNOWN-never-zero invariant
    (module docstring "CRITICAL MONEY-PATH SEMANTIC"): a caller that
    coalesces this function's result must see the bug immediately -- the
    function returns the Python singleton None, never a dict with
    zeroed/empty fields a `result or {}` idiom could silently swallow."""

    def test_none_when_nothing_ever_published(self, conn):
        result = latest_position_economics(conn, "never-published")
        assert result is None
        assert result != {}
        assert not isinstance(result, dict)

    def test_none_when_table_exists_but_position_never_covered(self, conn):
        _publish(conn, position_ids=["p1"], computed_at="2026-07-14T12:00:00+00:00")
        assert latest_position_economics(conn, "p-unrelated") is None

    def test_empty_position_id_raises_rather_than_guessing(self, conn):
        with pytest.raises(ValueError):
            latest_position_economics(conn, "")


class TestLatestPositionEconomicsHappyPath:
    def test_returns_the_published_row_for_a_covered_position(self, conn):
        _publish(
            conn,
            position_ids=["p1"],
            computed_at="2026-07-14T12:00:00+00:00",
            econ_by_id={"p1": _econ("p1", realized_pnl_usd=42.5, fill_count=3)},
        )

        result = latest_position_economics(conn, "p1")

        assert result is not None
        assert result["position_id"] == "p1"
        assert result["keeper_position_id"] == "p1"
        assert result["realized_pnl_usd"] == pytest.approx(42.5)
        assert result["fill_count"] == 3
        assert result["payout_status"] == "CLOSED_VIA_FILLS"

    def test_returned_row_carries_its_own_generation_id(self, conn):
        gen = _publish(
            conn,
            position_ids=["p1"],
            computed_at="2026-07-14T12:00:00+00:00",
        )

        result = latest_position_economics(conn, "p1")

        assert result["generation_id"] == gen.generation_id
        assert result["generation_id"] == latest_generation_id(conn)


class TestKeeperResolution:
    def test_absorbed_identity_resolves_to_keeper_row(self, conn):
        """Mirrors src.reduce.position_economics._resolve_identity_group's
        own contract, reused verbatim (not reimplemented) -- an absorbed raw
        position_id must return the SAME published row as its keeper."""
        insert_identity_superseded(conn, keeper_position_id="keeper", absorbed_position_ids=["absorbed"])
        # Only "keeper" is published -- mirrors materialize_generation's own
        # dedupe-by-keeper contract (src/reduce/materialize.py): the absorbed
        # raw id never gets its own row.
        _publish(
            conn,
            position_ids=["keeper"],
            computed_at="2026-07-14T12:00:00+00:00",
            econ_by_id={"keeper": _econ("keeper", realized_pnl_usd=7.0)},
        )

        via_keeper = latest_position_economics(conn, "keeper")
        via_absorbed = latest_position_economics(conn, "absorbed")

        assert via_absorbed is not None
        assert via_absorbed == via_keeper
        assert via_absorbed["position_id"] == "keeper"
        assert via_absorbed["realized_pnl_usd"] == pytest.approx(7.0)

    def test_absorbed_identity_with_no_keeper_row_in_latest_generation_is_none(self, conn):
        """The keeper resolution is correct, but if the LATEST generation
        simply never covered that keeper, the answer is still fail-closed
        None -- never a fabricated zero."""
        insert_identity_superseded(conn, keeper_position_id="keeper", absorbed_position_ids=["absorbed"])
        _publish(conn, position_ids=["someone-else"], computed_at="2026-07-14T12:00:00+00:00")

        assert latest_position_economics(conn, "absorbed") is None


class TestWholeGenerationScoping:
    """generation.py's honesty invariant: "portfolio 发布按完整 generation,
    不按 per-row latest" -- publication is by complete generation, never
    per-row latest. These are the regression tests that would catch a
    reintroduction of per-row-latest semantics through this accessor."""

    def test_does_not_fall_back_to_an_older_generation_that_covered_the_position(self, conn):
        _publish(
            conn,
            position_ids=["p1"],
            computed_at="2026-07-14T12:00:00+00:00",
            econ_by_id={"p1": _econ("p1", realized_pnl_usd=999.0)},
        )
        # Newer generation exists but does NOT cover p1 (e.g. p1 was refused
        # at this later materialization, or dropped out of the corpus).
        _publish(conn, position_ids=["p2"], computed_at="2026-07-14T13:00:00+00:00")

        # Must be None, NOT the stale 999.0 from the older generation.
        assert latest_position_economics(conn, "p1") is None

    def test_picks_up_the_position_once_a_newer_generation_covers_it_again(self, conn):
        _publish(
            conn,
            position_ids=["p1"],
            computed_at="2026-07-14T12:00:00+00:00",
            econ_by_id={"p1": _econ("p1", realized_pnl_usd=1.0)},
        )
        _publish(conn, position_ids=["p2"], computed_at="2026-07-14T13:00:00+00:00")
        _publish(
            conn,
            position_ids=["p1"],
            computed_at="2026-07-14T14:00:00+00:00",
            econ_by_id={"p1": _econ("p1", realized_pnl_usd=2.0)},
        )

        result = latest_position_economics(conn, "p1")
        assert result is not None
        assert result["realized_pnl_usd"] == pytest.approx(2.0)
