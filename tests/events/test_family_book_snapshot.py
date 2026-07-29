# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   center-evidence campaign data prerequisite. Covers the schema (append-only,
#   dedup), the market_center_native estimator, the fail-soft writer, and the
#   hook's source-order placement in event_reactor_adapter.py.
"""Tests for family_book_snapshots: schema, estimator, writer, hook placement."""
from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

import src.engine.event_reactor_adapter as era
from src.config import City
from src.decision.family_decision_engine import FamilyDecision
from src.events.candidate_binding import EventBoundCandidateFamily
from src.events.family_book_snapshot import (
    _ladder_json,
    append_family_book_snapshot,
    market_center_native,
)
from src.execution.family_book import (
    ExecutableLadder,
    FamilyBook,
    MarketBook,
    build_family_book,
)
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import DebiasAuthority
from src.forecast.predictive_distribution_builder import PredictiveDistributionBuilder
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.outcome_space import OutcomeBin, OutcomeSpace, compute_topology_hash
from src.state.schema.family_book_snapshots_schema import append_snapshot, ensure_table
from src.strategy.live_inference.executable_cost import QuoteLevel

UTC = timezone.utc
ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Shared fixtures -- a real complete 11-bin Omega + FamilyBook (same shape as
# tests/execution/test_family_book.py) and a real PredictiveDistribution (same
# construction as tests/decision/test_family_decision_engine.py).
# ---------------------------------------------------------------------------

def _resolution(metric: str = "high") -> EventResolution:
    city = City(
        name="Tokyo", lat=35.68, lon=139.69, timezone="Asia/Tokyo",
        settlement_unit="C", cluster="asia", wu_station=STATION,
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _case(metric: str = "high") -> ForecastCase:
    return ForecastCase(
        city="Tokyo", city_id="tokyo", station_id=STATION,
        settlement_source_type="wu_icao", target_local_date=date(2026, 6, 14),
        metric=metric, issue_time_utc=ISSUE, lead_hours=6.0, season="summer",
        regime_key="zonal", unit="C", resolution=_resolution(metric),
        family_id="tokyo_high_2026-06-14", source_cycle_time_utc=ISSUE - timedelta(hours=6),
    )


def _bin(bin_id: str, lo, hi, label: str, rule: str, *, executable: bool = True) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id, condition_id=f"cond-{bin_id}", label=label,
        lower_native=lo, upper_native=hi, yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}", executable=executable, rounding_rule=rule,
    )


def _complete_bins(rule: str) -> tuple[OutcomeBin, ...]:
    bins = [_bin("b_low", None, 20.0, "20C or below", rule, executable=False)]
    for t in range(21, 30):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}C", rule))
    bins.append(_bin("b_high", 30.0, None, "30C or above", rule, executable=False))
    return tuple(bins)


def _outcome_space(case: ForecastCase) -> OutcomeSpace:
    resolution = case.resolution
    rule = resolution.rounding_rule
    bins = _complete_bins(rule)
    space = OutcomeSpace(
        family_id=case.family_id, resolution=resolution, bins=bins,
        topology_hash=compute_topology_hash(case.family_id, resolution, bins),
    )
    space.validate()
    return space


def _ladder(side: str, levels: tuple = ()) -> ExecutableLadder:
    return ExecutableLadder(
        levels=levels, side=side, fee_rate=0.0,
        min_tick_size=Decimal("0.01"), min_order_size=Decimal("1.0"),
    )


def _thin_market(bin_id: str) -> MarketBook:
    """A MarketBook with no two-sided YES quote (all ladders empty)."""
    return MarketBook(
        condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}", yes_asks=_ladder("ask"), yes_bids=_ladder("bid"),
        no_asks=_ladder("ask"), no_bids=_ladder("bid"), neg_risk=False,
    )


def _quoted_market(bin_id: str, *, yes_ask: float, yes_bid: float) -> MarketBook:
    return MarketBook(
        condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", (QuoteLevel(Decimal(str(yes_ask)), Decimal("500")),)),
        yes_bids=_ladder("bid", (QuoteLevel(Decimal(str(yes_bid)), Decimal("500")),)),
        no_asks=_ladder("ask"), no_bids=_ladder("bid"), neg_risk=False,
    )


def _complete_family_book(case: ForecastCase, space: OutcomeSpace) -> FamilyBook:
    """Complete book: b25 and b27 two-sided quoted, every other bin thin."""
    markets = {}
    for b in space.bins:
        if b.bin_id == "b25":
            markets[b.bin_id] = _quoted_market(b.bin_id, yes_ask=0.30, yes_bid=0.20)
        elif b.bin_id == "b27":
            markets[b.bin_id] = _quoted_market(b.bin_id, yes_ask=0.70, yes_bid=0.60)
        else:
            markets[b.bin_id] = _thin_market(b.bin_id)
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


def _incomplete_family_book(case: ForecastCase, space: OutcomeSpace) -> FamilyBook:
    """Missing b_high -> complete_book False, even though b25/b27 are quoted."""
    markets = {}
    for b in space.bins:
        if b.bin_id == "b_high":
            continue
        if b.bin_id == "b25":
            markets[b.bin_id] = _quoted_market(b.bin_id, yes_ask=0.30, yes_bid=0.20)
        else:
            markets[b.bin_id] = _thin_market(b.bin_id)
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


def _all_thin_family_book(case: ForecastCase, space: OutcomeSpace) -> FamilyBook:
    """Complete book, but no bin has a two-sided quote anywhere."""
    markets = {b.bin_id: _thin_market(b.bin_id) for b in space.bins}
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


def _member(model_id: str, value_native: float, case: ForecastCase) -> RawModelMember:
    return RawModelMember(
        model_id=model_id, product_id=f"{model_id}_mx2t3",
        source_run_id=f"{model_id}_run_2026061400",
        source_cycle_time_utc=ISSUE - timedelta(hours=6),
        available_at_utc=ISSUE - timedelta(hours=1), value_native=value_native,
        station_mapping_id=f"{STATION}_wu_icao", raw_forecast_artifact_id=f"{model_id}_artifact",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
    )


def _model_set(values_native, case: ForecastCase) -> FreshModelSet:
    model_ids = [f"m{i}" for i in range(len(values_native))]
    members = tuple(_member(mid, v, case) for mid, v in zip(model_ids, values_native))
    arr = np.asarray(values_native, dtype=float)
    return FreshModelSet(
        case=case, members=members, member_values_native=arr,
        min_native=float(arr.min()), max_native=float(arr.max()),
        model_set_hash="modelset_tokyo_high_v1",
    )


def _no_obs() -> Day0ObservationState:
    return Day0ObservationState(
        observed=False, station_id=STATION, source="none", samples_count=0,
        latest_observed_at_utc=None, observed_high_native=None,
        observed_low_native=None, observed_extreme_native=None, raw_observation_hash=None,
    )


def _predictive(case: ForecastCase):
    return PredictiveDistributionBuilder(DebiasAuthority(())).build(
        case, _model_set([24.5, 25.0, 25.5], case), _no_obs(), has_fusion_capture=True,
    )


def _family(case: ForecastCase) -> EventBoundCandidateFamily:
    return EventBoundCandidateFamily(
        family_id=case.family_id, event_id="event-1", event_type="FORECAST_SNAPSHOT_READY",
        city=case.city, target_date=case.target_local_date.isoformat(), metric=case.metric,
        condition_ids=(), yes_token_ids=(), no_token_ids=(), bins=(), candidates=(),
        causal_snapshot_id="snap-causal-1", market_topology_source="executable_market_snapshots",
        binding_hash="binding-hash-1",
    )


def _decision(case: ForecastCase, space: OutcomeSpace, family_book) -> FamilyDecision:
    return FamilyDecision(
        decision_id="test-decision", case=case, predictive=_predictive(case), omega=space,
        joint_q=None, band=None, family_book=family_book, market_coherence=None,
        candidates=(), selected=None, no_trade_reason="TEST_NO_TRADE", receipt_hash="test-hash",
    )


# ---------------------------------------------------------------------------
# Schema: append-only + dedup (direct low-level append_snapshot).
# ---------------------------------------------------------------------------

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    ensure_table(conn)
    return conn


class TestSchemaAppendOnly:
    def test_dedup_ignores_repeat_family_book_hash(self):
        conn = _conn()
        kwargs = dict(
            family_id="f1", city="Tokyo", target_date="2026-06-14", temperature_metric="high",
            captured_at_utc="2026-06-14T12:00:00+00:00", book_hash="hash-1", complete_book=True,
            ladder_json="{}", market_center_c=1.0, our_mu_c=2.0, our_sigma_c=0.5,
            decision_snapshot_id="snap-1",
        )
        first = append_snapshot(conn, snapshot_id="s1", decision_time="t1", **kwargs)
        second = append_snapshot(conn, snapshot_id="s2", decision_time="t2", **kwargs)
        assert first is True
        assert second is False
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_snapshots").fetchone()
        assert count == 1

    def test_update_is_structurally_forbidden(self):
        conn = _conn()
        append_snapshot(
            conn, snapshot_id="s1", family_id="f1", city="Tokyo", target_date="2026-06-14",
            temperature_metric="high", decision_time="t1", captured_at_utc="c1",
            book_hash="hash-1", complete_book=True, ladder_json="{}", market_center_c=None,
            our_mu_c=None, our_sigma_c=None, decision_snapshot_id=None,
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE family_book_snapshots SET book_hash='x'")

    def test_delete_is_structurally_forbidden(self):
        conn = _conn()
        append_snapshot(
            conn, snapshot_id="s1", family_id="f1", city="Tokyo", target_date="2026-06-14",
            temperature_metric="high", decision_time="t1", captured_at_utc="c1",
            book_hash="hash-1", complete_book=True, ladder_json="{}", market_center_c=None,
            our_mu_c=None, our_sigma_c=None, decision_snapshot_id=None,
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM family_book_snapshots")


# ---------------------------------------------------------------------------
# market_center_native estimator.
# ---------------------------------------------------------------------------

class TestMarketCenterNative:
    def test_known_ladder_resolves_to_known_analytic_center(self):
        case = _case()
        space = _outcome_space(case)
        book = _complete_family_book(case, space)
        # weight_b25 = (0.30+0.20)/2 = 0.25 @ rep 25.0; weight_b27 = (0.70+0.60)/2 = 0.65 @ rep 27.0
        expected = (0.25 * 25.0 + 0.65 * 27.0) / (0.25 + 0.65)
        assert market_center_native(book) == pytest.approx(expected)

    def test_incomplete_book_is_none(self):
        case = _case()
        space = _outcome_space(case)
        book = _incomplete_family_book(case, space)
        assert book.complete_book is False
        assert market_center_native(book) is None

    def test_complete_but_all_thin_book_is_none(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_thin_family_book(case, space)
        assert book.complete_book is True
        assert market_center_native(book) is None


class TestLadderJson:
    def test_ladder_json_caps_levels_and_is_valid_json(self):
        case = _case()
        space = _outcome_space(case)
        levels = tuple(
            QuoteLevel(Decimal(f"0.{40 + i:02d}"), Decimal("100")) for i in range(8)
        )
        markets = {b.bin_id: _thin_market(b.bin_id) for b in space.bins}
        markets["b25"] = MarketBook(
            condition_id="cond-b25", bin_id="b25", yes_token_id="yes-b25",
            no_token_id="no-b25", yes_asks=_ladder("ask", levels), yes_bids=_ladder("bid"),
            no_asks=_ladder("ask"), no_bids=_ladder("bid"), neg_risk=True,
        )
        book = build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)
        parsed = json.loads(_ladder_json(book))
        assert len(parsed["b25"]["yes_ask"]) == 5  # capped at top 5, not all 8
        assert parsed["b25"]["yes_ask"][0] == [0.40, 100.0]
        assert parsed["b25"]["neg_risk"] is True
        assert parsed["b25"]["condition_id"] == "cond-b25"


# ---------------------------------------------------------------------------
# append_family_book_snapshot -- the fail-soft writer, real dataclasses.
# ---------------------------------------------------------------------------

class TestAppendFamilyBookSnapshot:
    def test_row_lands_with_expected_columns(self):
        conn = _conn()
        case = _case()
        space = _outcome_space(case)
        book = _complete_family_book(case, space)
        family = _family(case)
        decision = _decision(case, space, book)
        decision_time = datetime(2026, 6, 14, 12, 5, tzinfo=UTC)

        snapshot_id = append_family_book_snapshot(
            conn, decision=decision, family=family, decision_time=decision_time,
            causal_snapshot_id="causal-abc",
        )
        assert snapshot_id is not None
        row = conn.execute(
            "SELECT family_id, city, book_hash, complete_book, market_center_c, "
            "our_mu_c, our_sigma_c, decision_snapshot_id FROM family_book_snapshots "
            "WHERE snapshot_id = ?", (snapshot_id,),
        ).fetchone()
        assert row is not None
        family_id, city, book_hash, complete_book, market_center_c, our_mu_c, our_sigma_c, decision_snapshot_id = row
        assert family_id == case.family_id
        assert city == "Tokyo"
        assert book_hash == book.book_hash
        assert complete_book == 1
        assert market_center_c == pytest.approx(market_center_native(book))
        assert our_mu_c == pytest.approx(decision.predictive.mu_native)
        assert our_sigma_c == pytest.approx(decision.predictive.sigma_native)
        assert decision_snapshot_id == "causal-abc"

    def test_dedup_on_repeat_family_and_book_hash(self):
        conn = _conn()
        case = _case()
        space = _outcome_space(case)
        book = _complete_family_book(case, space)
        family = _family(case)
        decision = _decision(case, space, book)

        first = append_family_book_snapshot(
            conn, decision=decision, family=family,
            decision_time=datetime(2026, 6, 14, 12, 5, tzinfo=UTC),
            causal_snapshot_id="causal-1",
        )
        second = append_family_book_snapshot(
            conn, decision=decision, family=family,
            decision_time=datetime(2026, 6, 14, 12, 6, tzinfo=UTC),  # later cycle, same book
            causal_snapshot_id="causal-2",
        )
        assert first is not None
        assert second is None  # dedup no-op, not a new row
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_snapshots").fetchone()
        assert count == 1

    def test_none_decision_is_a_noop(self):
        conn = _conn()
        case = _case()
        family = _family(case)
        result = append_family_book_snapshot(
            conn, decision=None, family=family,
            decision_time=datetime(2026, 6, 14, 12, 5, tzinfo=UTC),
            causal_snapshot_id="causal-1",
        )
        assert result is None
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_snapshots").fetchone()
        assert count == 0

    def test_decision_with_no_family_book_is_a_noop(self):
        conn = _conn()
        case = _case()
        space = _outcome_space(case)
        decision = _decision(case, space, family_book=None)
        family = _family(case)
        result = append_family_book_snapshot(
            conn, decision=decision, family=family,
            decision_time=datetime(2026, 6, 14, 12, 5, tzinfo=UTC),
            causal_snapshot_id="causal-1",
        )
        assert result is None
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_snapshots").fetchone()
        assert count == 0

    def test_write_failure_is_fail_soft_never_raises(self):
        """A connection missing the table entirely: append_snapshot raises
        OperationalError inside the writer, which must swallow it, never
        propagate -- telemetry-grade persistence must never break the caller."""
        bare_conn = sqlite3.connect(":memory:")  # ensure_table never called
        case = _case()
        space = _outcome_space(case)
        book = _complete_family_book(case, space)
        family = _family(case)
        decision = _decision(case, space, book)

        result = append_family_book_snapshot(
            bare_conn, decision=decision, family=family,
            decision_time=datetime(2026, 6, 14, 12, 5, tzinfo=UTC),
            causal_snapshot_id="causal-1",
        )  # must not raise
        assert result is None


# ---------------------------------------------------------------------------
# Hook placement: the capture call must precede the prepare_global_auction /
# global_actuation branch (source-order proof -- same idiom as
# tests/engine/test_shift_bin_reactor_integration.py and
# tests/contracts/test_decision_provenance.py, which prove call-ordering
# invariants over this same giant function by source inspection rather than
# a full end-to-end replay).
# ---------------------------------------------------------------------------

def test_family_book_snapshot_capture_precedes_prepare_global_auction_branch():
    source = inspect.getsource(era._build_event_bound_no_submit_receipt_core)
    capture_call = "append_family_book_snapshot("
    branch_marker = "if prepare_global_auction:"
    selection_fact_call = "_record_qkernel_selection_family_facts("

    assert capture_call in source
    assert branch_marker in source
    assert selection_fact_call in source
    # "if prepare_global_auction:" appears multiple times in this function (earlier
    # early-exit branches too) -- anchor on the occurrences AFTER the capture call,
    # which is the branch immediately guarding _record_qkernel_selection_family_facts.
    capture_idx = source.index(capture_call)
    # The capture must run BEFORE the branch that decides whether the
    # selection-fact writer even executes -- so it fires on every arm
    # (prepare_global_auction, global_actuation is not None, and the plain
    # _record_qkernel_selection_family_facts arm), not only the one arm that
    # writes selection facts.
    assert capture_idx < source.index(branch_marker, capture_idx)
    assert capture_idx < source.index(selection_fact_call, capture_idx)
