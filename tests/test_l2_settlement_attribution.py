# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: PROMOTION_PIPELINE_DESIGN.md §4 (Track L-2) + §2 (EvidenceReport contract)
#                  + regret_decomposer.py (sign convention POSITIVE=WIN)
"""Relationship tests for Track L-2 settlement-attribution cron job.

TESTS FIRST (methodology rule): relationship tests verified BEFORE implementation.

Tests
-----
R1  slug/target_date join: attributed rows match on market_slug + target_date + temperature_metric
R2  regret components sum to total within 1e-9
R3  win-sign convention: POSITIVE=WIN (realized > 0 when settled in our favour)
R4  idempotent re-run: second attribution run writes zero additional rows
R5  COUNT>0 smoke: WORLD.regret_decompositions + WORLD.shadow_experiments after attribution
R6  no_settlement skip: unmatched decision events produce no regret rows
R7  already_attributed gate: _already_attributed returns True for existing deid
R8  dry_run: no DB rows written when dry_run=True
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.db import init_schema, init_schema_forecasts
from src.cron.settlement_attribution import (
    run_attribution,
    compute_realized_pnl,
    _already_attributed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def world_conn() -> sqlite3.Connection:
    """In-memory WORLD DB with full world schema."""
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return conn


@pytest.fixture()
def fcst_conn() -> sqlite3.Connection:
    """In-memory FCST DB with full forecast schema (contains settlements_v2)."""
    conn = sqlite3.connect(":memory:")
    init_schema_forecasts(conn)
    return conn


def _insert_shadow_decision(conn: sqlite3.Connection, **overrides) -> str:
    """Insert a minimal shadow_decision row into decision_events; return decision_event_id."""
    defaults = dict(
        market_slug="highest-temperature-in-chicago-on-06-15-2026",
        temperature_metric="high",
        target_date="2026-06-15",
        observation_time="2026-06-14T12:00:00+00:00",
        decision_seq=0,
        decision_event_id="deid_v1_test001",
        decision_time="2026-06-14T12:00:00+00:00",
        outcome="shadow_enter",
        side="YES",
        strategy_key="shoulder_sell",
        target_price=0.30,
        target_size_usd=10.0,
        schema_version=29,
        source="shadow_decision",
    )
    defaults.update(overrides)
    conn.execute(
        """
        INSERT INTO decision_events (
            market_slug, temperature_metric, target_date, observation_time,
            decision_seq, condition_id, decision_event_id, decision_time,
            outcome, side, strategy_key, cycle_id, cycle_iteration,
            p_posterior, edge, target_size_usd, target_price,
            forecast_time, provider_reported_time,
            observation_available_at, polymarket_end_anchor_source,
            first_member_observed_time, run_complete_time,
            zeus_submit_intent_time, venue_ack_time,
            first_inclusion_block_time, finality_confirmed_time,
            clock_skew_estimate_ms_at_submit, raw_orderbook_hash_transition_delta_ms,
            schema_version, source
        ) VALUES (
            :market_slug, :temperature_metric, :target_date, :observation_time,
            :decision_seq, NULL, :decision_event_id, :decision_time,
            :outcome, :side, :strategy_key, NULL, NULL,
            NULL, NULL, :target_size_usd, :target_price,
            NULL, NULL,
            '', 'unknown_legacy',
            NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
            :schema_version, :source
        )
        """,
        defaults,
    )
    conn.commit()
    return defaults["decision_event_id"]


def _insert_settlement(conn: sqlite3.Connection, **overrides) -> None:
    """Insert a row into settlements_v2 in the given (possibly in-memory) DB."""
    defaults = dict(
        city="Chicago",
        target_date="2026-06-15",
        temperature_metric="high",
        market_slug="highest-temperature-in-chicago-on-06-15-2026",
        winning_bin="above_90F",
        settlement_value=92.3,
        settlement_source="test",
        settled_at="2026-06-16T00:00:00+00:00",
        authority="VERIFIED",
        provenance_json="{}",
    )
    defaults.update(overrides)
    conn.execute(
        """
        INSERT INTO settlements_v2 (
            city, target_date, temperature_metric, market_slug, winning_bin,
            settlement_value, settlement_source, settled_at, authority, provenance_json
        ) VALUES (
            :city, :target_date, :temperature_metric, :market_slug, :winning_bin,
            :settlement_value, :settlement_source, :settled_at, :authority, :provenance_json
        )
        """,
        defaults,
    )
    conn.commit()


def _attach_fcst(world_conn: sqlite3.Connection, fcst_conn: sqlite3.Connection) -> sqlite3.Connection:
    """Attach an in-memory fcst DB to world_conn as 'forecasts'.

    SQLite in-memory DBs cannot be ATTACHed by file path across separate
    connections.  We pre-populate a temp file, attach that, or we use the
    approach of copying the fcst schema + rows into world_conn via a second
    'forecasts' schema using the URI memory trick.

    Simplest test approach: use a named :memory: shared-cache URI so we can
    ATTACH it by name.  Requires uri=True + shared_cache=True-ish.
    Fallback: directly insert settlements_v2 rows into the world_conn under a
    manually created 'forecasts.settlements_v2' via ATTACH of a second
    :memory: DB — not possible in standard SQLite.

    Workaround: create settlements_v2 in the MAIN schema of world_conn (which
    already has init_schema run, adding ghost copies of v2 tables).  Then
    SELECT from 'settlements_v2' without schema prefix — this exercises the
    ATTACH path in a unit-test compatible way.

    For the relationship test we simply CREATE a 'forecasts' schema by
    ATTACHing a fresh :memory: DB using the file URI trick with a unique name:
        ATTACH 'file:fcst_{id}?mode=memory&cache=shared' AS forecasts

    This requires sqlite3 to be compiled with URI and shared-cache support
    (standard on macOS + Linux CPython).
    """
    import os
    uri = f"file:fcst_{id(world_conn)}?mode=memory&cache=shared"
    # Open the shared-cache DB using the same URI to populate it
    shared = sqlite3.connect(uri, uri=True, check_same_thread=False)
    init_schema_forecasts(shared)
    # Insert settlement data into the shared cache
    for row in fcst_conn.execute("SELECT city, target_date, temperature_metric, market_slug, winning_bin, settlement_value, settlement_source, settled_at, authority, provenance_json FROM settlements_v2").fetchall():
        shared.execute(
            "INSERT OR IGNORE INTO settlements_v2 (city, target_date, temperature_metric, market_slug, winning_bin, settlement_value, settlement_source, settled_at, authority, provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            row,
        )
    shared.commit()
    # ATTACH the shared-cache DB to world_conn
    world_conn.execute(f"ATTACH DATABASE '{uri}' AS forecasts")
    return shared  # caller keeps reference to prevent GC


# ---------------------------------------------------------------------------
# R1: Slug/target_date join correctness
# ---------------------------------------------------------------------------

class TestSlugJoinCorrectness:
    """R1: attributed rows join on market_slug + target_date + temperature_metric."""

    def test_matching_slug_produces_attribution(self, world_conn, fcst_conn) -> None:
        """A decision_event whose market_slug matches settlements_v2 produces a regret row."""
        deid = _insert_shadow_decision(world_conn)
        _insert_settlement(fcst_conn)
        shared = _attach_fcst(world_conn, fcst_conn)

        now = datetime(2026, 6, 16, tzinfo=timezone.utc)
        stats = run_attribution(world_conn=world_conn, now_utc=now)

        assert stats["attributed"] == 1
        assert stats["skipped_no_settlement"] == 0

        rd_count = world_conn.execute("SELECT COUNT(*) FROM regret_decompositions").fetchone()[0]
        assert rd_count == 1

    def test_wrong_slug_produces_no_attribution(self, world_conn, fcst_conn) -> None:
        """A decision_event with a slug that doesn't match settlements_v2 is skipped."""
        _insert_shadow_decision(
            world_conn,
            market_slug="highest-temperature-in-dallas-on-06-15-2026",
            decision_event_id="deid_v1_test002",
        )
        _insert_settlement(fcst_conn)  # Chicago slug only
        shared = _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn)

        assert stats["skipped_no_settlement"] == 1
        assert stats["attributed"] == 0

    def test_wrong_target_date_produces_no_attribution(self, world_conn, fcst_conn) -> None:
        """Different target_date means no join; row skipped."""
        _insert_shadow_decision(
            world_conn,
            target_date="2026-06-20",
            decision_event_id="deid_v1_test003",
        )
        _insert_settlement(fcst_conn, target_date="2026-06-15")  # different date
        shared = _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn)

        assert stats["skipped_no_settlement"] == 1
        assert stats["attributed"] == 0

    def test_wrong_temperature_metric_produces_no_attribution(self, world_conn, fcst_conn) -> None:
        """Different temperature_metric means no join; row skipped."""
        _insert_shadow_decision(
            world_conn,
            temperature_metric="low",
            decision_event_id="deid_v1_test004",
        )
        _insert_settlement(fcst_conn, temperature_metric="high")
        shared = _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn)

        assert stats["skipped_no_settlement"] == 1
        assert stats["attributed"] == 0


# ---------------------------------------------------------------------------
# R2: Regret components sum to total within 1e-9
# ---------------------------------------------------------------------------

class TestRegretComponentSumInvariant:
    """R2: 7 regret components sum to total_regret_usd within 1e-9."""

    def test_attributed_row_components_sum_to_total(self, world_conn, fcst_conn) -> None:
        """After attribution, all 7 components in the DB sum to total_regret_usd."""
        _insert_shadow_decision(world_conn, target_price=0.30, target_size_usd=10.0)
        _insert_settlement(fcst_conn)
        _attach_fcst(world_conn, fcst_conn)

        run_attribution(world_conn=world_conn)

        row = world_conn.execute(
            """
            SELECT forecast_error_usd, observation_error_usd, quote_error_usd,
                   non_fill_error_usd, fee_error_usd, timing_error_usd,
                   settlement_ambiguity_error_usd, total_regret_usd
            FROM regret_decompositions
            LIMIT 1
            """
        ).fetchone()
        assert row is not None, "Expected at least one regret row"

        (fe, oe, qe, nfe, fee, te, sae, total) = row
        component_sum = fe + oe + qe + nfe + fee + te + sae
        assert abs(component_sum - total) < 1e-9, (
            f"Components sum={component_sum:.15f} != total={total:.15f}"
        )

    def test_decompose_regret_sum_invariant_positive(self) -> None:
        """decompose_regret sum invariant holds for a positive outcome (WIN)."""
        from src.analysis.regret_decomposer import decompose_regret

        # YES position, entry=0.30, size=10, won: payoff=1.0
        # shares = 10/0.30 = 33.33...
        # realized = (1.0-0.30) * 33.33 = 23.33...
        realized = (1.0 - 0.30) * (10.0 / 0.30)
        components = decompose_regret(
            forecast_error_usd=realized,  # v1 thin: all to forecast_error
            realized_pnl_usd=realized,
            counterfactual_pnl_usd=0.0,
        )
        components.verify_sum()
        assert components.total_regret_usd > 0  # WIN

    def test_decompose_regret_sum_invariant_negative(self) -> None:
        """decompose_regret sum invariant holds for a negative outcome (LOSS)."""
        from src.analysis.regret_decomposer import decompose_regret

        realized = (0.0 - 0.30) * (10.0 / 0.30)  # lost: payoff=0
        components = decompose_regret(
            forecast_error_usd=realized,
            realized_pnl_usd=realized,
            counterfactual_pnl_usd=0.0,
        )
        components.verify_sum()
        assert components.total_regret_usd < 0  # LOSS


# ---------------------------------------------------------------------------
# R3: Win-sign convention POSITIVE=WIN
# ---------------------------------------------------------------------------

class TestWinSignConvention:
    """R3: POSITIVE total_regret_usd when side wins at settlement."""

    def test_yes_position_win_positive_pnl(self) -> None:
        """YES side + above-threshold bin → positive realized PnL (WIN)."""
        pnl = compute_realized_pnl(
            side="YES",
            winning_bin="above_90F",
            target_price=0.30,
            target_size_usd=10.0,
        )
        assert pnl is not None
        assert pnl > 0, f"Expected positive PnL (WIN) but got {pnl}"

    def test_yes_position_loss_negative_pnl(self) -> None:
        """YES side + below-threshold bin → negative realized PnL (LOSS)."""
        pnl = compute_realized_pnl(
            side="YES",
            winning_bin="below_80F",
            target_price=0.30,
            target_size_usd=10.0,
        )
        assert pnl is not None
        assert pnl < 0, f"Expected negative PnL (LOSS) but got {pnl}"

    def test_no_position_win_positive_pnl(self) -> None:
        """NO side + below-threshold bin (NO outcome) → positive realized PnL (WIN)."""
        pnl = compute_realized_pnl(
            side="NO",
            winning_bin="no_above_90F",
            target_price=0.30,
            target_size_usd=10.0,
        )
        assert pnl is not None
        assert pnl > 0, f"Expected positive PnL (WIN) but got {pnl}"

    def test_no_position_loss_negative_pnl(self) -> None:
        """NO side + above-threshold bin (YES outcome) → negative realized PnL (LOSS)."""
        pnl = compute_realized_pnl(
            side="NO",
            winning_bin="above_90F",
            target_price=0.30,
            target_size_usd=10.0,
        )
        assert pnl is not None
        assert pnl < 0, f"Expected negative PnL (LOSS) but got {pnl}"

    def test_winning_row_total_regret_positive_in_db(self, world_conn, fcst_conn) -> None:
        """Winning attributed row has total_regret_usd > 0 in regret_decompositions."""
        # YES + above_90F = WIN
        _insert_shadow_decision(
            world_conn, side="YES", target_price=0.30, target_size_usd=10.0
        )
        _insert_settlement(fcst_conn, winning_bin="above_90F")
        _attach_fcst(world_conn, fcst_conn)

        run_attribution(world_conn=world_conn)

        row = world_conn.execute(
            "SELECT total_regret_usd FROM regret_decompositions LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] > 0, f"Expected positive total_regret_usd (WIN) but got {row[0]}"

    def test_losing_row_total_regret_negative_in_db(self, world_conn, fcst_conn) -> None:
        """Losing attributed row has total_regret_usd < 0 in regret_decompositions."""
        # YES + below_80F = LOSS
        _insert_shadow_decision(
            world_conn, side="YES", target_price=0.30, target_size_usd=10.0,
            decision_event_id="deid_v1_loss001",
        )
        _insert_settlement(fcst_conn, winning_bin="below_80F")
        _attach_fcst(world_conn, fcst_conn)

        run_attribution(world_conn=world_conn)

        row = world_conn.execute(
            "SELECT total_regret_usd FROM regret_decompositions LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row[0] < 0, f"Expected negative total_regret_usd (LOSS) but got {row[0]}"


# ---------------------------------------------------------------------------
# R4: Idempotent re-run
# ---------------------------------------------------------------------------

class TestIdempotentRerun:
    """R4: Second run writes zero additional rows."""

    def test_second_run_writes_no_new_rows(self, world_conn, fcst_conn) -> None:
        """Running attribution twice produces the same row count as running once."""
        _insert_shadow_decision(world_conn)
        _insert_settlement(fcst_conn)
        shared = _attach_fcst(world_conn, fcst_conn)

        now = datetime(2026, 6, 16, tzinfo=timezone.utc)
        stats1 = run_attribution(world_conn=world_conn, now_utc=now)
        rd_after_first = world_conn.execute(
            "SELECT COUNT(*) FROM regret_decompositions"
        ).fetchone()[0]

        stats2 = run_attribution(world_conn=world_conn, now_utc=now)
        rd_after_second = world_conn.execute(
            "SELECT COUNT(*) FROM regret_decompositions"
        ).fetchone()[0]

        assert stats2["skipped_already_attributed"] == 1
        assert stats2["attributed"] == 0
        assert rd_after_second == rd_after_first, (
            f"Second run wrote extra rows: {rd_after_first} → {rd_after_second}"
        )

    def test_already_attributed_returns_true_for_existing(self, world_conn) -> None:
        """_already_attributed returns True when a regret row exists for the deid."""
        from src.state.shadow_experiment_registry import register_shadow_experiment
        from src.analysis.regret_decomposer import decompose_regret, write_regret_decomposition

        exp_id = register_shadow_experiment(
            "shoulder_sell", {"v": 1}, "cohort_r4",
            started_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
            conn=world_conn,
        )
        world_conn.commit()
        components = decompose_regret(
            forecast_error_usd=1.0,
            realized_pnl_usd=1.0,
            counterfactual_pnl_usd=0.0,
        )
        write_regret_decomposition(
            experiment_id=exp_id,
            decision_event_id="deid_v1_r4test",
            components=components,
            conn=world_conn,
        )
        world_conn.commit()

        assert _already_attributed("deid_v1_r4test", world_conn) is True
        assert _already_attributed("deid_v1_notexist", world_conn) is False


# ---------------------------------------------------------------------------
# R5: COUNT>0 smoke on WORLD after attribution
# ---------------------------------------------------------------------------

class TestCountSmoke:
    """R5: WORLD.regret_decompositions and WORLD.shadow_experiments have rows after attribution."""

    def test_world_regret_count_positive_after_attribution(self, world_conn, fcst_conn) -> None:
        """regret_decompositions COUNT > 0 after at least one attribution."""
        _insert_shadow_decision(world_conn)
        _insert_settlement(fcst_conn)
        _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn)
        assert stats["world_rows_written"] > 0

        rd_count = world_conn.execute("SELECT COUNT(*) FROM regret_decompositions").fetchone()[0]
        se_count = world_conn.execute("SELECT COUNT(*) FROM shadow_experiments").fetchone()[0]
        assert rd_count > 0, "regret_decompositions must have ≥1 row after attribution"
        assert se_count > 0, "shadow_experiments must have ≥1 row after attribution"

    def test_multiple_decisions_all_attributed(self, world_conn, fcst_conn) -> None:
        """Two shadow decisions in same market both attributed; both appear in regret_decompositions."""
        _insert_shadow_decision(
            world_conn,
            decision_event_id="deid_v1_multi001",
            decision_seq=0,
            observation_time="2026-06-13T12:00:00+00:00",
        )
        _insert_shadow_decision(
            world_conn,
            decision_event_id="deid_v1_multi002",
            decision_seq=1,
            observation_time="2026-06-14T12:00:00+00:00",
        )
        _insert_settlement(fcst_conn)
        _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn)
        assert stats["attributed"] == 2
        rd_count = world_conn.execute("SELECT COUNT(*) FROM regret_decompositions").fetchone()[0]
        assert rd_count == 2


# ---------------------------------------------------------------------------
# R6: No-settlement skip
# ---------------------------------------------------------------------------

class TestNoSettlementSkip:
    """R6: Unmatched decisions (no settlement row) produce no regret rows."""

    def test_unsettled_market_skipped(self, world_conn, fcst_conn) -> None:
        """Decision for market with no settlements_v2 row is skipped."""
        _insert_shadow_decision(
            world_conn,
            market_slug="highest-temperature-in-miami-on-07-01-2026",
            decision_event_id="deid_v1_nosett01",
        )
        # No settlement inserted for Miami
        _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn)
        assert stats["skipped_no_settlement"] == 1
        assert stats["attributed"] == 0
        rd_count = world_conn.execute("SELECT COUNT(*) FROM regret_decompositions").fetchone()[0]
        assert rd_count == 0


# ---------------------------------------------------------------------------
# R7: Already-attributed gate
# ---------------------------------------------------------------------------

class TestAlreadyAttributedGate:
    """R7: _already_attributed correctly guards against double-write."""

    def test_no_row_returns_false(self, world_conn) -> None:
        assert _already_attributed("deid_v1_notexist", world_conn) is False

    def test_existing_row_returns_true(self, world_conn) -> None:
        from src.state.shadow_experiment_registry import register_shadow_experiment
        from src.analysis.regret_decomposer import decompose_regret, write_regret_decomposition

        exp_id = register_shadow_experiment(
            "shoulder_sell", {"v": 1}, "cohort_r7",
            started_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
            conn=world_conn,
        )
        world_conn.commit()
        c = decompose_regret(forecast_error_usd=0.5, realized_pnl_usd=0.5, counterfactual_pnl_usd=0.0)
        write_regret_decomposition("exp_r7", "deid_v1_r7gate", c, conn=world_conn)
        world_conn.commit()

        assert _already_attributed("deid_v1_r7gate", world_conn) is True


# ---------------------------------------------------------------------------
# R8: Dry-run writes nothing
# ---------------------------------------------------------------------------

class TestDryRun:
    """R8: dry_run=True computes but does not write to DB."""

    def test_dry_run_no_db_rows(self, world_conn, fcst_conn) -> None:
        """With dry_run=True, no rows are written to regret_decompositions."""
        _insert_shadow_decision(world_conn)
        _insert_settlement(fcst_conn)
        _attach_fcst(world_conn, fcst_conn)

        stats = run_attribution(world_conn=world_conn, dry_run=True)
        assert stats["attributed"] == 1
        assert stats["world_rows_written"] == 0

        rd_count = world_conn.execute("SELECT COUNT(*) FROM regret_decompositions").fetchone()[0]
        assert rd_count == 0, "dry_run must not write any regret rows"
