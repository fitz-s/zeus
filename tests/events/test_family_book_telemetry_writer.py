# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO, plus round-3 review fixes: X1
#   (transaction poisoning -- explicit rollback/connection-replacement
#   tests), X2 (spool architecture -- writes never touch the trade DB
#   directly; only the periodic ingest pass does, under db_writer_lock), X3
#   (per-observation source_manifest_json distinct across observations of
#   identical content), H3 (PRE_VETO_SELECTED rename + orthogonal booleans),
#   H4 (kill switch), M1 (shutdown/second-worker refusal), M2 (restart
#   continuity via cache seeding from durable observations).
"""Tests for src/events/family_book_telemetry_writer.py."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest

import src.events.family_book_telemetry_writer as writer
from src.config import City
from src.decision.family_decision_engine import FamilyDecision
from src.events.candidate_binding import EventBoundCandidateFamily
from src.execution.family_book import ExecutableLadder, MarketBook, build_family_book
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import DebiasAuthority
from src.forecast.predictive_distribution_builder import PredictiveDistributionBuilder
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.outcome_space import OutcomeBin, OutcomeSpace, compute_topology_hash
from src.strategy.live_inference.executable_cost import QuoteLevel

UTC = timezone.utc
ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reset_writer():
    writer.reset_for_test()
    yield
    writer.reset_for_test()


# ---------------------------------------------------------------------------
# Fixtures (same shape as tests/events/test_family_book_manifest.py).
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


def _bin(bin_id, lo, hi, label, rule, *, executable=True) -> OutcomeBin:
    return OutcomeBin(
        bin_id=bin_id, condition_id=f"cond-{bin_id}", label=label, lower_native=lo,
        upper_native=hi, yes_token_id=f"yes-{bin_id}", no_token_id=f"no-{bin_id}",
        executable=executable, rounding_rule=rule,
    )


def _complete_bins(rule: str):
    bins = [_bin("b_low", None, 20.0, "20C or below", rule, executable=False)]
    for t in range(21, 30):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}C", rule))
    bins.append(_bin("b_high", 30.0, None, "30C or above", rule, executable=False))
    return tuple(bins)


def _outcome_space(case: ForecastCase) -> OutcomeSpace:
    resolution = case.resolution
    bins = _complete_bins(resolution.rounding_rule)
    space = OutcomeSpace(
        family_id=case.family_id, resolution=resolution, bins=bins,
        topology_hash=compute_topology_hash(case.family_id, resolution, bins),
    )
    space.validate()
    return space


def _ladder(side, levels=(), *, tick="0.01", min_order="1.0", fee=0.05) -> ExecutableLadder:
    return ExecutableLadder(levels=levels, side=side, fee_rate=fee, min_tick_size=Decimal(tick), min_order_size=Decimal(min_order))


def _quoted_market(bin_id, *, yes_ask=0.30, yes_bid=0.20, **kw) -> MarketBook:
    return MarketBook(
        condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", (QuoteLevel(Decimal(str(yes_ask)), Decimal("500")),), **kw),
        yes_bids=_ladder("bid", (QuoteLevel(Decimal(str(yes_bid)), Decimal("500")),), **kw),
        no_asks=_ladder("ask", **kw), no_bids=_ladder("bid", **kw), neg_risk=False,
    )


def _family_book(case, space, **kw):
    markets = {b.bin_id: _quoted_market(b.bin_id, **kw) for b in space.bins}
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


@dataclass
class _FakeProof:
    bin_id: str
    executable_snapshot_id: Optional[str]
    row: Optional[dict]
    direction: str = "YES"


def _candidate_bin_id(proof: _FakeProof) -> str:
    return proof.bin_id


def _proofs_for(space, *, snapshot_suffix="1", raw_hash="hash-a"):
    return tuple(
        _FakeProof(
            bin_id=b.bin_id, executable_snapshot_id=f"snap-{b.bin_id}-{snapshot_suffix}",
            row={"raw_orderbook_hash": raw_hash, "captured_at": f"2026-06-14T12:0{snapshot_suffix}:00+00:00"},
        )
        for b in space.bins
    )


def _member(model_id, value_native, case) -> RawModelMember:
    return RawModelMember(
        model_id=model_id, product_id=f"{model_id}_mx2t3", source_run_id=f"{model_id}_run_2026061400",
        source_cycle_time_utc=ISSUE - timedelta(hours=6), available_at_utc=ISSUE - timedelta(hours=1),
        value_native=value_native, station_mapping_id=f"{STATION}_wu_icao",
        raw_forecast_artifact_id=f"{model_id}_artifact",
        data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
    )


def _model_set(values_native, case) -> FreshModelSet:
    import numpy as np
    model_ids = [f"m{i}" for i in range(len(values_native))]
    members = tuple(_member(mid, v, case) for mid, v in zip(model_ids, values_native))
    arr = np.asarray(values_native, dtype=float)
    return FreshModelSet(case=case, members=members, member_values_native=arr, min_native=float(arr.min()), max_native=float(arr.max()), model_set_hash="modelset_v1")


def _no_obs() -> Day0ObservationState:
    return Day0ObservationState(observed=False, station_id=STATION, source="none", samples_count=0, latest_observed_at_utc=None, observed_high_native=None, observed_low_native=None, observed_extreme_native=None, raw_observation_hash=None)


def _predictive(case):
    return PredictiveDistributionBuilder(DebiasAuthority(())).build(case, _model_set([24.5, 25.0, 25.5], case), _no_obs(), has_fusion_capture=True)


def _decision(case, space, family_book, *, selected=None, receipt_hash="test-hash"):
    return FamilyDecision(
        decision_id="test-decision", case=case, predictive=_predictive(case), omega=space,
        joint_q=None, band=None, family_book=family_book, market_coherence=None,
        candidates=(), selected=selected, no_trade_reason=None if selected else "TEST_NO_TRADE",
        receipt_hash=receipt_hash,
    )


def _family(case) -> EventBoundCandidateFamily:
    return EventBoundCandidateFamily(
        family_id=case.family_id, event_id="event-1", event_type="FORECAST_SNAPSHOT_READY",
        city=case.city, target_date=case.target_local_date.isoformat(), metric=case.metric,
        condition_ids=(), yes_token_ids=(), no_token_ids=(), bins=(), candidates=(),
        causal_snapshot_id="snap-causal-1", market_topology_source="executable_market_snapshots",
        binding_hash="binding-hash-1",
    )


def _wait_until(predicate, timeout=3.0, interval=0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _start(tmp_path, **overrides):
    """Start the writer against isolated spool/trade paths under tmp_path."""
    spool_path = tmp_path / "spool.db"
    trade_path = tmp_path / "trade.db"
    kwargs = dict(
        spool_conn_factory=lambda: sqlite3.connect(str(spool_path)),
        ingest_conn_factory=lambda: sqlite3.connect(str(trade_path)),
        trade_db_path_factory=lambda: trade_path,
    )
    kwargs.update(overrides)
    writer.start_worker(**kwargs)
    return spool_path, trade_path


def _enqueue(decision, family, proofs, decision_time, causal_snapshot_id="c1"):
    writer.enqueue_family_book_observation(
        decision=decision, family=family, active_proofs=proofs,
        candidate_bin_id=_candidate_bin_id, decision_time=decision_time,
        causal_snapshot_id=causal_snapshot_id,
    )


# ---------------------------------------------------------------------------
# (a) Nonblocking enqueue + drop counter.
# ---------------------------------------------------------------------------

class TestNonblockingEnqueue:
    def test_enqueue_never_blocks_under_held_wal_write_lock(self, tmp_path):
        """The decision thread's enqueue must stay fast even while a SEPARATE
        connection holds a DB's WAL write lock -- architectural property:
        enqueue never touches SQLite at all (X2 -- writes go to the spool
        off-thread), so DB contention anywhere cannot affect it."""
        db_path = tmp_path / "contended.db"
        blocker_conn = sqlite3.connect(str(db_path))
        blocker_conn.execute("PRAGMA journal_mode=WAL")
        blocker_conn.execute("CREATE TABLE t (x INTEGER)")
        blocker_conn.execute("BEGIN IMMEDIATE")
        blocker_conn.execute("INSERT INTO t VALUES (1)")  # write txn held open, never committed

        _start(tmp_path, spool_conn_factory=lambda: sqlite3.connect(str(db_path), timeout=0.05))

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        proofs = _proofs_for(space)

        latencies = []
        for i in range(20):
            t0 = time.monotonic()
            _enqueue(decision, family, proofs, datetime(2026, 6, 14, 12, i, tzinfo=UTC))
            latencies.append(time.monotonic() - t0)

        blocker_conn.rollback()
        blocker_conn.close()
        assert max(latencies) < 0.05, f"enqueue must never block on DB contention; max={max(latencies)}s"

    def test_full_queue_drops_and_increments_counter_without_blocking(self, tmp_path):
        release = threading.Event()

        def _gated_connect():
            release.wait(timeout=5.0)
            return sqlite3.connect(":memory:")

        _start(tmp_path, spool_conn_factory=_gated_connect, maxsize=1)

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        proofs = _proofs_for(space)

        try:
            for i in range(5):
                t0 = time.monotonic()
                _enqueue(decision, family, proofs, datetime(2026, 6, 14, 12, i, tzinfo=UTC))
                assert time.monotonic() - t0 < 0.05
            assert writer.counter("telemetry_drop_total") >= 4  # only the first of 5 fits in maxsize=1
        finally:
            release.set()


# ---------------------------------------------------------------------------
# H4: rollout kill switch.
# ---------------------------------------------------------------------------

class TestKillSwitch:
    def test_disabled_env_var_makes_enqueue_a_complete_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED", "0")
        _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=1.0)
        assert writer.counter("family_book_telemetry_written_states_total") == 0

    def test_enabled_by_default(self, tmp_path):
        spool_path, _ = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        conn = sqlite3.connect(str(spool_path))
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert count == 1


# ---------------------------------------------------------------------------
# X2: spool architecture -- writes go ONLY to the spool; the trade DB is
# touched ONLY by the periodic/forced ingest pass.
# ---------------------------------------------------------------------------

class TestSpoolArchitecture:
    def test_writes_land_in_spool_not_trade_db_until_ingested(self, tmp_path):
        spool_path, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        spool_conn = sqlite3.connect(str(spool_path))
        (spool_obs,) = spool_conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert spool_obs == 1

        # The trade DB file may already exist (M2's startup cache-seed reads
        # it), but it must carry ZERO observation rows until an ingest pass
        # actually runs -- ordinary writes never touch it directly (X2).
        trade_conn = sqlite3.connect(str(trade_path))
        (trade_obs_before,) = trade_conn.execute(
            "SELECT COUNT(*) FROM family_book_observations"
        ).fetchone()
        assert trade_obs_before == 0

        assert writer.force_ingest(timeout=5.0)
        trade_conn = sqlite3.connect(str(trade_path))
        (trade_obs,) = trade_conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert trade_obs == 1

    def test_ingest_is_idempotent_across_repeated_passes(self, tmp_path):
        spool_path, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)
        assert writer.force_ingest(timeout=5.0)  # second pass over the same spool rows
        trade_conn = sqlite3.connect(str(trade_path))
        (trade_obs,) = trade_conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert trade_obs == 1  # ON CONFLICT DO NOTHING -- no duplicate ingestion

    def test_ingest_contention_skips_this_pass_without_blocking(self, tmp_path):
        """Reverse-contention property: an external holder of the SAME
        WriteClass.BULK lock on the trade DB must not block the ingest
        pass -- it skips (typed counter) and the caller (force_ingest)
        returns promptly rather than waiting."""
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        spool_path, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        with db_writer_lock(trade_path, WriteClass.BULK):
            t0 = time.monotonic()
            assert writer.force_ingest(timeout=5.0)
            elapsed = time.monotonic() - t0

        assert elapsed < 1.0, "ingest must skip on contention, not wait"
        assert writer.counter("family_book_telemetry_ingest_contended_total") >= 1

        # Once released, a normal ingest succeeds.
        assert writer.force_ingest(timeout=5.0)
        trade_conn = sqlite3.connect(str(trade_path))
        (trade_obs,) = trade_conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert trade_obs == 1


# ---------------------------------------------------------------------------
# (b) t vs t+1 live-rebuild -- state/observation cardinality (THE dedup fix),
# now verified end-to-end through spool -> ingest.
# ---------------------------------------------------------------------------

class TestLiveRebuildCardinality:
    def test_equal_ladders_at_t_and_t_plus_1_yield_one_state_and_correct_observations(self, tmp_path):
        """THE verified bug (FamilyBook.book_hash hashes captured_at_utc, and
        the live bridge sets captured_at_utc=decision_time) meant an
        UNCHANGED book rebuilt a minute later produced a distinct book_hash
        -> a new row every cycle. state_count == 1 proves content-hash dedup
        fixes that. obs_count == 1 (not 2) proves the sampling policy
        correctly treats "same state, 1 minute later, no trade selected" as
        sampled-out."""
        _, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)  # SAME content both times
        family = _family(case)

        decision_t0 = _decision(case, space, book, receipt_hash="receipt-t0")
        decision_t1 = _decision(case, space, book, receipt_hash="receipt-t1")

        _enqueue(decision_t0, family, _proofs_for(space, snapshot_suffix="1"), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "causal-t0")
        _enqueue(decision_t1, family, _proofs_for(space, snapshot_suffix="2"), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "causal-t1")
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)

        conn = sqlite3.connect(str(trade_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 1, "unchanged content must dedup to ONE state row"
        assert obs_count == 1, "unchanged content 1 min later with no trade selected is sampled OUT"

    def test_changed_content_creates_a_second_state_row(self, tmp_path):
        _, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book_a = _family_book(case, space, fee=0.05)
        book_b = _family_book(case, space, fee=0.09)  # execution metadata changed
        family = _family(case)

        _enqueue(_decision(case, space, book_a, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c1")
        _enqueue(_decision(case, space, book_b, receipt_hash="r2"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c2")
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)

        conn = sqlite3.connect(str(trade_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 2, "a genuine fee change must produce a new state (hash/payload correspondence)"
        assert obs_count == 2


# ---------------------------------------------------------------------------
# X3: per-observation source provenance survives the sampled-out layer too --
# a heartbeat re-observation of unchanged content still carries ITS OWN
# capture identity, not the first-seen state's.
# ---------------------------------------------------------------------------

class TestPerObservationProvenance:
    def test_heartbeat_reobservation_carries_its_own_source_manifest(self, tmp_path):
        _, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)  # identical content both times
        family = _family(case)

        _enqueue(
            _decision(case, space, book, receipt_hash="r0"), family,
            _proofs_for(space, snapshot_suffix="1"), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0",
        )
        assert writer.drain(timeout=3.0)
        _enqueue(
            _decision(case, space, book, receipt_hash="r1"), family,
            _proofs_for(space, snapshot_suffix="2"), datetime(2026, 6, 14, 12, 31, tzinfo=UTC), "c1",  # +31min, distinct snapshot
        )
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)

        conn = sqlite3.connect(str(trade_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        rows = conn.execute(
            "SELECT sampling_reason, source_manifest_json FROM family_book_observations ORDER BY decision_time"
        ).fetchall()
        assert state_count == 1, "unchanged content -> one shared state"
        assert [r[0] for r in rows] == ["STATE_CHANGE", "HEARTBEAT"]
        manifest_0 = json.loads(rows[0][1])
        manifest_1 = json.loads(rows[1][1])
        assert manifest_0["b25"]["executable_snapshot_id"] != manifest_1["b25"]["executable_snapshot_id"]
        assert manifest_0["b25"]["source_captured_at"] != manifest_1["b25"]["source_captured_at"]


# ---------------------------------------------------------------------------
# Sampling policy v2: STATE_CHANGE / HEARTBEAT / PRE_VETO_SELECTED (H3) +
# orthogonal booleans.
# ---------------------------------------------------------------------------

@dataclass
class _FakeSelected:
    candidate_id: str


@dataclass
class _FakeRoute:
    bin_id: str
    side: str


@dataclass
class _FakeCandidateDecision:
    economics: _FakeSelected
    route: _FakeRoute


def _decision_with_selection(case, space, book, selected, candidate_id, bin_id, side, *, receipt_hash):
    cd = _FakeCandidateDecision(economics=selected, route=_FakeRoute(bin_id=bin_id, side=side))
    return FamilyDecision(
        decision_id="test-decision", case=case, predictive=_predictive(case), omega=space,
        joint_q=None, band=None, family_book=book, market_coherence=None,
        candidates=(), selected=selected, no_trade_reason=None, receipt_hash=receipt_hash,
        candidate_decisions=(cd,),
    )


class TestSamplingPolicy:
    def test_repeat_same_state_no_heartbeat_no_selection_is_sampled_out(self, tmp_path):
        _, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        for i in range(3):
            _enqueue(_decision(case, space, book, receipt_hash=f"r{i}"), family, _proofs_for(space), datetime(2026, 6, 14, 12, i, tzinfo=UTC), f"c{i}")
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)

        conn = sqlite3.connect(str(trade_path))
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1
        assert writer.counter("family_book_telemetry_sampled_out_total") == 2

    def test_heartbeat_fires_after_interval_even_without_change(self, tmp_path):
        _, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        _enqueue(_decision(case, space, book, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 31, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)

        conn = sqlite3.connect(str(trade_path))
        rows = conn.execute(
            "SELECT sampling_reason, state_changed, heartbeat_due, pre_veto_selected FROM family_book_observations ORDER BY decision_time"
        ).fetchall()
        assert [r[0] for r in rows] == ["STATE_CHANGE", "HEARTBEAT"]
        assert rows[0] == ("STATE_CHANGE", 1, 0, 0)
        assert rows[1] == ("HEARTBEAT", 0, 1, 0)

    def test_selected_trade_forces_a_pre_veto_selected_observation_with_identity(self, tmp_path):
        _, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        selected = _FakeSelected("candidate-1")
        decision_1 = _decision_with_selection(case, space, book, selected, "candidate-1", "b25", "YES", receipt_hash="r1")
        _enqueue(decision_1, family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)

        conn = sqlite3.connect(str(trade_path))
        rows = conn.execute(
            "SELECT sampling_reason, state_changed, heartbeat_due, pre_veto_selected, selected_bin_id, selected_side "
            "FROM family_book_observations ORDER BY decision_time"
        ).fetchall()
        assert [r[0] for r in rows] == ["STATE_CHANGE", "PRE_VETO_SELECTED"]
        assert rows[1] == ("PRE_VETO_SELECTED", 0, 0, 1, "b25", "YES")


# ---------------------------------------------------------------------------
# X1: transaction poisoning after a partial write / a failed COMMIT.
# ---------------------------------------------------------------------------

class TestTransactionSafety:
    def test_observation_insert_failure_rolls_back_state_insert_too(self, tmp_path, monkeypatch):
        """State INSERT succeeds, observation INSERT raises: the transaction
        must roll back completely (neither row durable), conn.in_transaction
        must be False afterward, and a SEPARATE connection must be able to
        write to the SAME spool file immediately (no lingering writer lock)."""
        spool_path, _ = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)

        def _boom(*_a, **_k):
            raise sqlite3.IntegrityError("simulated observation insert failure")

        monkeypatch.setattr(writer, "insert_observation", _boom)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_write_failures_total") == 1
        assert writer.counter("family_book_telemetry_written_states_total") == 0

        conn = sqlite3.connect(str(spool_path))
        assert conn.in_transaction is False
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 0, "the state INSERT must have been rolled back too, not just the failing statement"
        assert obs_count == 0

        # A SEPARATE connection can write to the spool file immediately --
        # no lingering writer lock from the poisoned transaction.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("COMMIT")

    def test_failed_commit_replaces_the_connection_and_no_later_observation_commits_stale_rows(self, tmp_path):
        """Both INSERTs succeed, COMMIT itself is injected to fail: the
        connection must be replaced (rollback of a connection that cannot
        even commit is the only safe recovery), no success counters
        increment for the failed attempt, and the NEXT observation must
        commit cleanly on the replacement connection (no stale transaction
        state carried forward)."""
        spool_path = tmp_path / "spool.db"
        trade_path = tmp_path / "trade.db"
        real_connect = sqlite3.connect
        call_count = {"n": 0}

        class _CommitFailsOnceConnection(sqlite3.Connection):
            """sqlite3.Connection.commit is a read-only slot on the built-in
            type (cannot be monkeypatched as an instance attribute) -- a
            factory subclass is the only way to inject a commit failure."""

            _commit_failed = False

            def commit(self):
                if not self._commit_failed:
                    self._commit_failed = True
                    raise sqlite3.OperationalError("simulated commit failure")
                return super().commit()

        def _spool_factory():
            call_count["n"] += 1
            factory = _CommitFailsOnceConnection if call_count["n"] == 1 else None
            if factory is not None:
                return real_connect(str(spool_path), factory=factory)
            return real_connect(str(spool_path))

        writer.start_worker(
            spool_conn_factory=_spool_factory,
            ingest_conn_factory=lambda: real_connect(str(trade_path)),
            trade_db_path_factory=lambda: trade_path,
        )

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_write_failures_total") == 1
        assert writer.counter("family_book_telemetry_written_states_total") == 0
        assert writer.counter("family_book_telemetry_written_observations_total") == 0

        # The NEXT observation (distinct content -> guaranteed STATE_CHANGE,
        # never sampled out) must commit cleanly -- proves the connection was
        # replaced with a clean one, not left mid-transaction.
        book_2 = _family_book(case, space, fee=0.11)
        _enqueue(_decision(case, space, book_2, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_written_states_total") == 1
        assert writer.counter("family_book_telemetry_written_observations_total") == 1
        # rollback() itself succeeded on the poisoned-commit connection (only
        # commit() was poisoned), so no connection replacement was needed --
        # the same connection, no longer mid-transaction, committed the next
        # observation cleanly. (Replacement is covered separately below,
        # test_rollback_failure_also_replaces_the_connection.)
        assert call_count["n"] == 1

        conn = real_connect(str(spool_path))
        assert conn.in_transaction is False
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1, "only the SECOND (successful) observation is durable -- no stale rows from the failed commit"

    def test_rollback_failure_also_replaces_the_connection(self, tmp_path):
        """When BOTH commit() and rollback() fail (the genuinely unrecoverable
        case), the writer must close and replace the connection -- the only
        way to guarantee conn.in_transaction is False going forward -- and
        the next observation must still land durably on the replacement."""
        spool_path = tmp_path / "spool.db"
        trade_path = tmp_path / "trade.db"
        real_connect = sqlite3.connect
        call_count = {"n": 0}

        class _CommitAndRollbackFailOnceConnection(sqlite3.Connection):
            _poisoned = False

            def commit(self):
                if not self._poisoned:
                    self._poisoned = True
                    raise sqlite3.OperationalError("simulated commit failure")
                return super().commit()

            def rollback(self):
                if self._poisoned:
                    self._poisoned = False  # only the one recovery attempt fails
                    raise sqlite3.OperationalError("simulated rollback failure too")
                return super().rollback()

        def _spool_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return real_connect(str(spool_path), factory=_CommitAndRollbackFailOnceConnection)
            return real_connect(str(spool_path))

        writer.start_worker(
            spool_conn_factory=_spool_factory,
            ingest_conn_factory=lambda: real_connect(str(trade_path)),
            trade_db_path_factory=lambda: trade_path,
        )

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_written_states_total") == 0

        book_2 = _family_book(case, space, fee=0.13)
        _enqueue(_decision(case, space, book_2, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_written_states_total") == 1
        assert writer.counter("family_book_telemetry_written_observations_total") == 1
        assert call_count["n"] == 2, "rollback failure must force a fresh connection (a second factory call)"

        conn = real_connect(str(spool_path))
        assert conn.in_transaction is False
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1


# ---------------------------------------------------------------------------
# Commit-time / write fault injection: typed counters, no unbounded logging.
# ---------------------------------------------------------------------------

class TestFaultInjection:
    def test_write_exception_increments_counter_and_does_not_crash_worker(self, tmp_path, monkeypatch, caplog):
        spool_path, _ = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        def _boom(*_a, **_k):
            raise sqlite3.OperationalError("simulated disk-full")

        monkeypatch.setattr(writer, "insert_state", _boom)

        import logging
        caplog.set_level(logging.WARNING, logger="src.events.family_book_telemetry_writer")
        for i in range(5):
            _enqueue(_decision(case, space, book, receipt_hash=f"r{i}"), family, _proofs_for(space), datetime(2026, 6, 14, 12, i, tzinfo=UTC), f"c{i}")
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_write_failures_total") == 5  # every failure counted
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) <= 1, "log storm: fault must be rate-limited, not logged every occurrence"
        conn = sqlite3.connect(str(spool_path))
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='family_book_states'"
        ).fetchone()
        assert count == 1  # schema still intact; worker didn't die mid-init


# ---------------------------------------------------------------------------
# SQLite multi-connection WAL-reset-fix version guard.
# ---------------------------------------------------------------------------

class TestSqliteVersionGuard:
    def test_worker_refuses_to_start_below_the_wal_reset_fix_floor(self, tmp_path, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 40, 0))
        caplog.set_level(logging.ERROR, logger="src.events.family_book_telemetry_writer")

        _start(tmp_path)
        assert _wait_until(lambda: writer._worker_thread is None or not writer._worker_thread.is_alive(), timeout=2.0)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 1
        assert "sqlite" in error_records[0].message.lower()


# ---------------------------------------------------------------------------
# M1: shutdown / second-worker refusal.
# ---------------------------------------------------------------------------

class TestShutdownLifecycle:
    def test_start_worker_refuses_while_one_is_already_alive(self, tmp_path):
        spool_path, trade_path = _start(tmp_path)
        started_again = writer.start_worker(
            spool_conn_factory=lambda: sqlite3.connect(str(spool_path)),
            ingest_conn_factory=lambda: sqlite3.connect(str(trade_path)),
            trade_db_path_factory=lambda: trade_path,
        )
        assert started_again is False

    def test_shutdown_succeeds_even_with_a_full_queue(self, tmp_path):
        release = threading.Event()

        def _gated_connect():
            release.wait(timeout=5.0)
            return sqlite3.connect(":memory:")

        _start(tmp_path, spool_conn_factory=_gated_connect, maxsize=1)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))  # fills maxsize=1

        release.set()
        assert writer.shutdown(timeout=5.0) is True


# ---------------------------------------------------------------------------
# M2: restart continuity -- the sampling cache is seeded from the durable
# trade DB, not reset to empty, on worker (re)start.
# ---------------------------------------------------------------------------

class TestRestartContinuity:
    def test_worker_restart_seeds_sampling_cache_from_durable_observations(self, tmp_path):
        spool_path, trade_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        assert writer.force_ingest(timeout=5.0)
        assert writer.shutdown(timeout=5.0)

        # Simulate a full process restart: fresh module-level state, worker
        # started again pointed at a FRESH (empty) spool but the SAME durable
        # trade DB.
        writer._last_state_by_family.clear()
        fresh_spool_path = tmp_path / "spool2.db"
        writer.start_worker(
            spool_conn_factory=lambda: sqlite3.connect(str(fresh_spool_path)),
            ingest_conn_factory=lambda: sqlite3.connect(str(trade_path)),
            trade_db_path_factory=lambda: trade_path,
        )
        assert _wait_until(lambda: writer._last_state_by_family.get(case.family_id) is not None, timeout=2.0)

        # The SAME content, 1 minute later, must be sampled OUT (proving the
        # cache was seeded, not falsely treated as a fresh STATE_CHANGE).
        _enqueue(_decision(case, space, book, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_sampled_out_total") == 1
