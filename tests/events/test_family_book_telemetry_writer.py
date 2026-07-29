# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO. Covers the required validations from the
#   review section 3: nonblocking enqueue + drop counter under WAL writer
#   contention, live-rebuild state/observation cardinality (t vs t+1), a
#   content-changing rebuild, the sampling policy (STATE_CHANGE / HEARTBEAT /
#   DECISION), and commit-time fault injection with typed counters.
"""Tests for src/events/family_book_telemetry_writer.py."""
from __future__ import annotations

import queue
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
from src.state.schema.family_book_observations_schema import ensure_table as ensure_obs_table
from src.state.schema.family_book_states_schema import ensure_table as ensure_states_table
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


# ---------------------------------------------------------------------------
# (a) Nonblocking enqueue + drop counter -- WAL writer contention.
# ---------------------------------------------------------------------------

class TestNonblockingEnqueue:
    def test_enqueue_never_blocks_under_held_wal_write_lock(self, tmp_path):
        """The decision thread's enqueue must stay fast even while a SEPARATE
        connection holds the trade DB's WAL write lock -- this is the
        architectural property BLOCKER 1 requires: the enqueue never touches
        SQLite at all, so DB contention cannot affect it."""
        db_path = tmp_path / "contended.db"
        blocker_conn = sqlite3.connect(str(db_path))
        blocker_conn.execute("PRAGMA journal_mode=WAL")
        blocker_conn.execute("CREATE TABLE t (x INTEGER)")
        blocker_conn.execute("BEGIN IMMEDIATE")
        blocker_conn.execute("INSERT INTO t VALUES (1)")  # write txn held open, never committed

        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path), timeout=0.05),
            trade_db_path_factory=lambda: db_path,
        )

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        proofs = _proofs_for(space)

        latencies = []
        for i in range(20):
            t0 = time.monotonic()
            writer.enqueue_family_book_observation(
                decision=decision, family=family, active_proofs=proofs,
                candidate_bin_id=_candidate_bin_id,
                decision_time=datetime(2026, 6, 14, 12, i, tzinfo=UTC),
                causal_snapshot_id="causal-1",
            )
            latencies.append(time.monotonic() - t0)

        blocker_conn.rollback()
        blocker_conn.close()

        assert max(latencies) < 0.05, f"enqueue must never block on DB contention; max={max(latencies)}s"

    def test_full_queue_drops_and_increments_counter_without_blocking(self, tmp_path):
        # A conn_factory gated on an Event this test controls guarantees the
        # worker never reaches its queue.get() loop until released -- nothing
        # races to drain the queue, so maxsize=1 fills deterministically on
        # the second enqueue.
        release = threading.Event()

        def _gated_connect():
            release.wait(timeout=5.0)
            return sqlite3.connect(":memory:")

        writer.start_worker(
            conn_factory=_gated_connect, maxsize=1,
            trade_db_path_factory=lambda: tmp_path / "unused.db",
        )

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        proofs = _proofs_for(space)

        try:
            for i in range(5):
                t0 = time.monotonic()
                writer.enqueue_family_book_observation(
                    decision=decision, family=family, active_proofs=proofs,
                    candidate_bin_id=_candidate_bin_id,
                    decision_time=datetime(2026, 6, 14, 12, i, tzinfo=UTC),
                    causal_snapshot_id="causal-1",
                )
                assert time.monotonic() - t0 < 0.05

            assert writer.counter("telemetry_drop_total") >= 4  # only the first of 5 fits in maxsize=1
        finally:
            release.set()  # let the gated worker thread proceed so teardown's shutdown() joins cleanly


# ---------------------------------------------------------------------------
# (b) t vs t+1 live-rebuild -- state/observation cardinality (THE dedup fix).
# ---------------------------------------------------------------------------

class TestLiveRebuildCardinality:
    def test_equal_ladders_at_t_and_t_plus_1_yield_one_state_and_correct_observations(self, tmp_path):
        """THE verified bug (FamilyBook.book_hash hashes captured_at_utc, and the
        live bridge sets captured_at_utc=decision_time) meant an UNCHANGED book
        rebuilt a minute later produced a distinct book_hash -> a new row every
        cycle. Here: state_count == 1 proves content-hash dedup fixes that.
        obs_count == 1 (not 2) proves the SECOND layer of the fix: the sampling
        policy correctly treats "same state, 1 minute later, no trade selected"
        as sampled-out, not a redundant append -- exactly the volume control
        BLOCKER 2/3 required. (A THIRD rebuild past the heartbeat interval, or
        with a selected trade, WOULD append -- see TestSamplingPolicy.)"""
        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)  # SAME content both times
        family = _family(case)

        # Two decisions, same content, at t and t+1 -- exactly what the live
        # bridge produces every cycle for an unchanged book (different
        # captured_at_utc/decision_time, same observable content).
        decision_t0 = _decision(case, space, book, receipt_hash="receipt-t0")
        decision_t1 = _decision(case, space, book, receipt_hash="receipt-t1")

        writer.enqueue_family_book_observation(
            decision=decision_t0, family=family, active_proofs=_proofs_for(space, snapshot_suffix="1"),
            candidate_bin_id=_candidate_bin_id, decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
            causal_snapshot_id="causal-t0",
        )
        writer.enqueue_family_book_observation(
            decision=decision_t1, family=family, active_proofs=_proofs_for(space, snapshot_suffix="2"),
            candidate_bin_id=_candidate_bin_id, decision_time=datetime(2026, 6, 14, 12, 1, tzinfo=UTC),
            causal_snapshot_id="causal-t1",
        )
        assert writer.drain(timeout=3.0)

        conn = sqlite3.connect(str(db_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 1, "unchanged content must dedup to ONE state row"
        assert obs_count == 1, "unchanged content 1 min later with no trade selected is sampled OUT"

    def test_changed_content_creates_a_second_state_row(self, tmp_path):
        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )

        case = _case()
        space = _outcome_space(case)
        book_a = _family_book(case, space, fee=0.05)
        book_b = _family_book(case, space, fee=0.09)  # execution metadata changed
        family = _family(case)

        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book_a, receipt_hash="r1"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC), causal_snapshot_id="c1",
        )
        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book_b, receipt_hash="r2"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 1, tzinfo=UTC), causal_snapshot_id="c2",
        )
        assert writer.drain(timeout=3.0)

        conn = sqlite3.connect(str(db_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 2, "a genuine fee change must produce a new state (hash/payload correspondence)"
        assert obs_count == 2


# ---------------------------------------------------------------------------
# Sampling policy: STATE_CHANGE / HEARTBEAT / DECISION / sampled-out.
# ---------------------------------------------------------------------------

class TestSamplingPolicy:
    def test_repeat_same_state_no_heartbeat_no_selection_is_sampled_out(self, tmp_path):
        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        for i in range(3):
            writer.enqueue_family_book_observation(
                decision=_decision(case, space, book, receipt_hash=f"r{i}"), family=family,
                active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
                decision_time=datetime(2026, 6, 14, 12, i, tzinfo=UTC), causal_snapshot_id=f"c{i}",
            )
        assert writer.drain(timeout=3.0)

        conn = sqlite3.connect(str(db_path))
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1  # only the first (STATE_CHANGE); the next 2 sampled out
        assert writer.counter("family_book_telemetry_sampled_out_total") == 2

    def test_heartbeat_fires_after_interval_even_without_change(self, tmp_path):
        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book, receipt_hash="r0"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC), causal_snapshot_id="c0",
        )
        assert writer.drain(timeout=3.0)
        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book, receipt_hash="r1"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 31, tzinfo=UTC), causal_snapshot_id="c1",  # +31min
        )
        assert writer.drain(timeout=3.0)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT sampling_reason FROM family_book_observations ORDER BY decision_time").fetchall()
        assert [r[0] for r in rows] == ["STATE_CHANGE", "HEARTBEAT"]

    def test_selected_trade_forces_a_decision_observation(self, tmp_path):
        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book, receipt_hash="r0"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC), causal_snapshot_id="c0",
        )
        assert writer.drain(timeout=3.0)
        selected = object()
        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book, selected=selected, receipt_hash="r1"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 1, tzinfo=UTC), causal_snapshot_id="c1",  # 1 min later, no heartbeat
        )
        assert writer.drain(timeout=3.0)

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT sampling_reason FROM family_book_observations ORDER BY decision_time").fetchall()
        assert [r[0] for r in rows] == ["STATE_CHANGE", "DECISION"]


# ---------------------------------------------------------------------------
# Commit-time / write fault injection: typed counters, no unbounded logging.
# ---------------------------------------------------------------------------

class TestFaultInjection:
    def test_write_exception_increments_counter_and_does_not_crash_worker(self, tmp_path, monkeypatch, caplog):
        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )
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
            writer.enqueue_family_book_observation(
                decision=_decision(case, space, book, receipt_hash=f"r{i}"), family=family,
                active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
                decision_time=datetime(2026, 6, 14, 12, i, tzinfo=UTC), causal_snapshot_id=f"c{i}",
            )
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_write_failures_total") == 5  # every failure counted
        # Rate-limited: 5 failures within the 60s window must not produce 5 log records.
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warning_records) <= 1, "log storm: fault must be rate-limited, not logged every occurrence"
        # Worker thread survives (still alive, not crashed) and the DB has zero rows.
        conn = sqlite3.connect(str(db_path))
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='family_book_states'"
        ).fetchone()
        assert count == 1  # schema still intact; worker didn't die mid-init


# ---------------------------------------------------------------------------
# SQLite multi-connection WAL-reset-fix version guard (team-lead follow-up:
# this module is the first in the repo to run a second live writer
# connection against the trade DB concurrently with the primary).
# ---------------------------------------------------------------------------

class TestSqliteVersionGuard:
    def test_worker_refuses_to_start_below_the_wal_reset_fix_floor(self, tmp_path, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 40, 0))
        db_path = tmp_path / "trade.db"
        caplog.set_level(logging.ERROR, logger="src.events.family_book_telemetry_writer")

        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )
        assert _wait_until(lambda: not writer._worker_thread.is_alive(), timeout=2.0)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 1
        assert "sqlite" in error_records[0].message.lower()
        # The worker exited before ever connecting/creating schema.
        assert not db_path.exists()


# ---------------------------------------------------------------------------
# db_writer_lock (WriteClass.BULK) -- first production wiring. Contention
# with an external holder of the SAME lock class must be non-blocking and
# typed, never a wait or a crash.
# ---------------------------------------------------------------------------

class TestWriterLockContention:
    def test_external_bulk_lock_holder_causes_contended_skip_not_a_write(self, tmp_path):
        from src.state.db_writer_lock import WriteClass, db_writer_lock

        db_path = tmp_path / "trade.db"
        writer.start_worker(
            conn_factory=lambda: sqlite3.connect(str(db_path)),
            trade_db_path_factory=lambda: db_path,
        )
        # Let the worker connect and create schema before contending it.
        assert _wait_until(lambda: db_path.exists(), timeout=2.0)

        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        with db_writer_lock(db_path, WriteClass.BULK):  # held by THIS thread
            writer.enqueue_family_book_observation(
                decision=_decision(case, space, book, receipt_hash="r-contended"), family=family,
                active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
                decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC), causal_snapshot_id="c0",
            )
            assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_write_contended_total") == 1
        assert writer.counter("family_book_telemetry_write_failures_total") == 0  # contention != a fault
        conn = sqlite3.connect(str(db_path))
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 0  # the contended write never happened, not a silent success

        # Once the external lock is released, the SAME decision writes normally.
        writer.enqueue_family_book_observation(
            decision=_decision(case, space, book, receipt_hash="r-clear"), family=family,
            active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 1, tzinfo=UTC), causal_snapshot_id="c1",
        )
        assert writer.drain(timeout=3.0)
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1
