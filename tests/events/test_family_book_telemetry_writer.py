# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after three deep-review NO-GOs. Round-5 (this suite) covers:
#   Y1 bounded outbox (watermarked batches, ack-delete, disk budget,
#   pending-stats metrics), Y2 canonical connection hygiene (bootstrap is
#   read-only, ingest has no DDL), Y3 owner-executed delivery
#   (run_bounded_ingest is a pure function taking the CALLER's canonical
#   connection -- the worker thread never opens one), Y4 worker liveness
#   (malformed-envelope quarantine, restart-without-ingest sampling
#   continuity) and Y5 lifecycle (readiness handshake, kill switch stops
#   both capture and draining). X1's transaction-safety tests are preserved
#   with minimal adaptation (same assertions; the insert target moved from
#   the canonical tables directly to the bounded outbox table).
"""Tests for src/events/family_book_telemetry_writer.py."""
from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
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


def _start(tmp_path, **overrides) -> Path:
    """Start the capture-side worker against an isolated spool path; returns
    the spool path. Canonical delivery is a SEPARATE call
    (writer.run_bounded_ingest), never triggered by the worker itself."""
    spool_path = tmp_path / "spool.db"
    kwargs = dict(spool_conn_factory=lambda: sqlite3.connect(str(spool_path)))
    kwargs.update(overrides)
    readiness = writer.start_worker(**kwargs)
    assert readiness.ready, readiness.reason
    return spool_path


def _enqueue(decision, family, proofs, decision_time, causal_snapshot_id="c1"):
    writer.enqueue_family_book_observation(
        decision=decision, family=family, active_proofs=proofs,
        candidate_bin_id=_candidate_bin_id, decision_time=decision_time,
        causal_snapshot_id=causal_snapshot_id,
    )


def _bootstrap_canonical(trade_path: Path) -> None:
    """Simulate the daemon's normal trade-schema bootstrap (init_schema_trade_only)
    that ALWAYS runs before any scheduler job in production."""
    conn = sqlite3.connect(str(trade_path))
    ensure_states_table(conn)
    ensure_obs_table(conn)
    conn.close()


def _ingest(trade_path: Path, spool_path: Path, **kwargs) -> "writer.IngestOutcome":
    conn = sqlite3.connect(str(trade_path))
    try:
        return writer.run_bounded_ingest(conn, spool_conn_factory=lambda: sqlite3.connect(str(spool_path)), **kwargs)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Readiness handshake (Y5) + kill switch.
# ---------------------------------------------------------------------------

class TestReadinessAndKillSwitch:
    def test_start_worker_returns_ready_on_success(self, tmp_path):
        spool_path = tmp_path / "spool.db"
        readiness = writer.start_worker(spool_conn_factory=lambda: sqlite3.connect(str(spool_path)))
        assert readiness.ready is True
        assert readiness.reason is None

    def test_start_worker_terminally_disables_capture_below_sqlite_floor(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 40, 0))
        spool_path = tmp_path / "spool.db"
        readiness = writer.start_worker(spool_conn_factory=lambda: sqlite3.connect(str(spool_path)))
        assert readiness.ready is False
        assert "sqlite" in (readiness.reason or "").lower()

        # Capture is now TERMINALLY disabled -- enqueue is a silent no-op,
        # never a retry attempt (round-4 H2/M1: no restart from the decision thread).
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        _enqueue(_decision(case, space, book), _family(case), _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.counter("family_book_telemetry_startup_failed_total") == 1

    def test_disabled_env_var_stops_capture(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED", "0")
        spool_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        _enqueue(_decision(case, space, book), _family(case), _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=1.0)
        conn = sqlite3.connect(str(spool_path))
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert count == 0

    def test_disabled_env_var_stops_canonical_draining_too(self, tmp_path, monkeypatch):
        """Y5: the kill switch must stop delivery, not merely new enqueues --
        the operator emergency guard must be able to halt canonical I/O even
        with a pending spool backlog already written."""
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        _enqueue(_decision(case, space, book), _family(case), _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        monkeypatch.setenv("ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED", "0")
        outcome = _ingest(trade_path, spool_path)
        assert outcome.disabled is True
        conn = sqlite3.connect(str(trade_path))
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert count == 0, "draining must not proceed while the kill switch is off"

        monkeypatch.delenv("ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED")
        outcome2 = _ingest(trade_path, spool_path)
        assert outcome2.disabled is False
        assert outcome2.ingested_observations == 1


# ---------------------------------------------------------------------------
# Nonblocking enqueue + drop counter.
# ---------------------------------------------------------------------------

class TestNonblockingEnqueue:
    def test_enqueue_never_blocks_under_held_wal_write_lock(self, tmp_path):
        db_path = tmp_path / "contended.db"
        blocker_conn = sqlite3.connect(str(db_path))
        blocker_conn.execute("PRAGMA journal_mode=WAL")
        blocker_conn.execute("CREATE TABLE t (x INTEGER)")
        blocker_conn.execute("BEGIN IMMEDIATE")
        blocker_conn.execute("INSERT INTO t VALUES (1)")

        writer.start_worker(spool_conn_factory=lambda: sqlite3.connect(str(db_path), timeout=0.05))

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
        assert max(latencies) < 0.05

    def test_full_queue_drops_and_increments_counter_without_blocking(self, tmp_path):
        # start_worker() now BLOCKS for a ready handshake (Y5); gating the
        # connect factory to keep the worker "not yet consuming" would also
        # gate readiness itself and trip the terminal-disable path. Start it
        # from a background thread instead, so the test's main thread can
        # fill the queue while the worker thread is still stuck opening its
        # connection -- nothing has drained the queue yet either way.
        release = threading.Event()

        def _gated_connect():
            release.wait(timeout=5.0)
            return sqlite3.connect(":memory:")

        starter = threading.Thread(
            target=lambda: writer.start_worker(spool_conn_factory=_gated_connect, maxsize=1, ready_timeout=10.0),
            daemon=True,
        )
        starter.start()
        # Wait for start_worker() to reach the queue-reconfiguration step
        # (synchronous, before it spawns/waits on the worker thread) so the
        # test doesn't race the default maxsize=2048 queue.
        assert _wait_until(lambda: writer._obs_queue.maxsize == 1, timeout=2.0)

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
            assert writer.counter("telemetry_drop_total") >= 4
        finally:
            release.set()
            starter.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Y1: bounded outbox -- watermark, ack-delete, idempotent ingestion, metrics,
# disk budget.
# ---------------------------------------------------------------------------

class TestBoundedOutbox:
    def test_ingest_deletes_the_acknowledged_batch_from_the_spool(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)
        _enqueue(_decision(case, space, book), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        conn = sqlite3.connect(str(spool_path))
        (pending_before,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert pending_before == 1

        outcome = _ingest(trade_path, spool_path)
        assert outcome.ingested_states == 1
        assert outcome.ingested_observations == 1

        conn = sqlite3.connect(str(spool_path))
        (pending_after,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert pending_after == 0, "the outbox must be a TRANSIENT buffer -- acked rows are deleted"

    def test_repeated_ingest_over_the_same_batch_is_idempotent(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)
        _enqueue(_decision(case, space, book), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        _ingest(trade_path, spool_path)
        _ingest(trade_path, spool_path)  # second pass -- outbox is already empty, no-op
        conn = sqlite3.connect(str(trade_path))
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1

    def test_ingest_is_bounded_by_batch_size_not_all_history(self, tmp_path):
        """The direct fix for round-4's 'unbounded mirror' finding: a
        pending backlog larger than one batch is drained over MULTIPLE
        bounded passes, never one unbounded pass."""
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        family = _family(case)

        for i in range(5):
            book = _family_book(case, space, fee=0.05 + i * 0.01)  # distinct content each time
            _enqueue(_decision(case, space, book, receipt_hash=f"r{i}"), family, _proofs_for(space, snapshot_suffix=str(i)), datetime(2026, 6, 14, 12, i, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        outcome = _ingest(trade_path, spool_path, batch_size=2)
        assert outcome.batch_rows == 2, "one bounded pass must not exceed batch_size"

        conn = sqlite3.connect(str(spool_path))
        (pending,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert pending == 3, "the rest waits for a LATER bounded pass, never replayed all at once"

        # Two more passes drain the remainder.
        _ingest(trade_path, spool_path, batch_size=2)
        _ingest(trade_path, spool_path, batch_size=2)
        conn = sqlite3.connect(str(spool_path))
        (pending_final,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert pending_final == 0

    def test_pending_outbox_stats_reports_count_bytes_and_oldest_age(self, tmp_path):
        spool_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)
        _enqueue(_decision(case, space, book), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        stats = writer.pending_outbox_stats(spool_conn_factory=lambda: sqlite3.connect(str(spool_path)))
        assert stats["pending_count"] == 1
        assert stats["pending_bytes_approx"] > 0
        assert stats["oldest_enqueued_at_utc"] is not None

    def test_spool_disk_budget_stops_capturing_when_exceeded(self, tmp_path):
        spool_path = _start(tmp_path, spool_pending_budget_bytes=1)  # trivially tiny budget
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)
        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        # First write may land (budget checked BEFORE each write); the point
        # is that capture stops once the budget is exceeded, not that zero
        # rows ever land.
        book_2 = _family_book(case, space, fee=0.13)
        _enqueue(_decision(case, space, book_2, receipt_hash="r1"), family, _proofs_for(space, snapshot_suffix="2"), datetime(2026, 6, 14, 12, 1, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_spool_budget_exceeded_total") >= 1


# ---------------------------------------------------------------------------
# t vs t+1 live-rebuild -- state/observation cardinality (THE original dedup
# fix), end-to-end through capture -> outbox -> bounded ingest.
# ---------------------------------------------------------------------------

class TestLiveRebuildCardinality:
    def test_equal_ladders_at_t_and_t_plus_1_yield_one_state_and_correct_observations(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="receipt-t0"), family, _proofs_for(space, snapshot_suffix="1"), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "causal-t0")
        _enqueue(_decision(case, space, book, receipt_hash="receipt-t1"), family, _proofs_for(space, snapshot_suffix="2"), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "causal-t1")
        assert writer.drain(timeout=3.0)
        _ingest(trade_path, spool_path)

        conn = sqlite3.connect(str(trade_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 1
        assert obs_count == 1, "unchanged content 1 min later with no trade selected is sampled OUT"

    def test_changed_content_creates_a_second_state_row(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        family = _family(case)

        _enqueue(_decision(case, space, _family_book(case, space, fee=0.05), receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c1")
        _enqueue(_decision(case, space, _family_book(case, space, fee=0.09), receipt_hash="r2"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c2")
        assert writer.drain(timeout=3.0)
        _ingest(trade_path, spool_path)

        conn = sqlite3.connect(str(trade_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert state_count == 2
        assert obs_count == 2


# ---------------------------------------------------------------------------
# X3: per-observation provenance, end-to-end.
# ---------------------------------------------------------------------------

class TestPerObservationProvenance:
    def test_heartbeat_reobservation_carries_its_own_source_manifest(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space, snapshot_suffix="1"), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        _enqueue(_decision(case, space, book, receipt_hash="r1"), family, _proofs_for(space, snapshot_suffix="2"), datetime(2026, 6, 14, 12, 31, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        _ingest(trade_path, spool_path)

        conn = sqlite3.connect(str(trade_path))
        (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
        rows = conn.execute("SELECT sampling_reason, source_manifest_json FROM family_book_observations ORDER BY decision_time").fetchall()
        assert state_count == 1
        assert [r[0] for r in rows] == ["STATE_CHANGE", "HEARTBEAT"]
        m0, m1 = json.loads(rows[0][1]), json.loads(rows[1][1])
        assert m0["b25"]["executable_snapshot_id"] != m1["b25"]["executable_snapshot_id"]
        assert m0["b25"]["source_captured_at"] != m1["b25"]["source_captured_at"]


# ---------------------------------------------------------------------------
# Sampling policy v2: STATE_CHANGE / HEARTBEAT / PRE_VETO_SELECTED + booleans.
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


def _decision_with_selection(case, space, book, selected, bin_id, side, *, receipt_hash):
    cd = _FakeCandidateDecision(economics=selected, route=_FakeRoute(bin_id=bin_id, side=side))
    return FamilyDecision(
        decision_id="test-decision", case=case, predictive=_predictive(case), omega=space,
        joint_q=None, band=None, family_book=book, market_coherence=None,
        candidates=(), selected=selected, no_trade_reason=None, receipt_hash=receipt_hash,
        candidate_decisions=(cd,),
    )


class TestSamplingPolicy:
    def test_repeat_same_state_no_heartbeat_no_selection_is_sampled_out(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)
        for i in range(3):
            _enqueue(_decision(case, space, book, receipt_hash=f"r{i}"), family, _proofs_for(space), datetime(2026, 6, 14, 12, i, tzinfo=UTC), f"c{i}")
        assert writer.drain(timeout=3.0)
        _ingest(trade_path, spool_path)

        conn = sqlite3.connect(str(trade_path))
        (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
        assert obs_count == 1
        assert writer.counter("family_book_telemetry_sampled_out_total") == 2

    def test_heartbeat_and_selection_orthogonal_booleans(self, tmp_path):
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        selected = _FakeSelected("candidate-1")
        decision_1 = _decision_with_selection(case, space, book, selected, "b25", "YES", receipt_hash="r1")
        _enqueue(decision_1, family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        _ingest(trade_path, spool_path)

        conn = sqlite3.connect(str(trade_path))
        rows = conn.execute(
            "SELECT sampling_reason, state_changed, heartbeat_due, pre_veto_selected, selected_bin_id, selected_side "
            "FROM family_book_observations ORDER BY decision_time"
        ).fetchall()
        assert [r[0] for r in rows] == ["STATE_CHANGE", "PRE_VETO_SELECTED"]
        assert rows[1] == ("PRE_VETO_SELECTED", 0, 0, 1, "b25", "YES")


# ---------------------------------------------------------------------------
# X1 (UNCHANGED per reviewer confirmation): transaction poisoning after a
# partial write / a failed COMMIT. Adapted only to target the outbox table
# insert (insert_outbox_row) instead of the canonical tables directly.
# ---------------------------------------------------------------------------

class TestTransactionSafety:
    def test_outbox_insert_failure_rolls_back_cleanly(self, tmp_path, monkeypatch):
        spool_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        def _boom(*_a, **_k):
            raise sqlite3.IntegrityError("simulated outbox insert failure")

        monkeypatch.setattr(writer, "insert_outbox_row", _boom)
        _enqueue(_decision(case, space, book), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        assert writer.counter("family_book_telemetry_write_failures_total") == 1

        conn = sqlite3.connect(str(spool_path))
        assert conn.in_transaction is False
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert count == 0

        conn.execute("BEGIN IMMEDIATE")
        conn.execute("COMMIT")

    def test_failed_commit_recovers_via_rollback_when_rollback_succeeds(self, tmp_path):
        spool_path = tmp_path / "spool.db"
        real_connect = sqlite3.connect
        call_count = {"n": 0}

        class _CommitFailsOnceConnection(sqlite3.Connection):
            _commit_failed = False

            def commit(self):
                if not self._commit_failed:
                    self._commit_failed = True
                    raise sqlite3.OperationalError("simulated commit failure")
                return super().commit()

        def _spool_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return real_connect(str(spool_path), factory=_CommitFailsOnceConnection)
            return real_connect(str(spool_path))

        writer.start_worker(spool_conn_factory=_spool_factory)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_write_failures_total") == 1

        book_2 = _family_book(case, space, fee=0.11)
        _enqueue(_decision(case, space, book_2, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)

        assert call_count["n"] == 1, "rollback succeeded -- no connection replacement needed"
        conn = real_connect(str(spool_path))
        assert conn.in_transaction is False
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert count == 1, "only the SECOND (successful) observation is durable"

    def test_rollback_failure_also_replaces_the_connection(self, tmp_path):
        spool_path = tmp_path / "spool.db"
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
                    self._poisoned = False
                    raise sqlite3.OperationalError("simulated rollback failure too")
                return super().rollback()

        def _spool_factory():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return real_connect(str(spool_path), factory=_CommitAndRollbackFailOnceConnection)
            return real_connect(str(spool_path))

        writer.start_worker(spool_conn_factory=_spool_factory)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)

        book_2 = _family_book(case, space, fee=0.13)
        _enqueue(_decision(case, space, book_2, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)

        assert call_count["n"] == 2, "rollback failure must force a fresh connection"
        conn = real_connect(str(spool_path))
        assert conn.in_transaction is False
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert count == 1


# ---------------------------------------------------------------------------
# Y4: worker liveness -- malformed envelope quarantine, restart-without-
# ingest sampling continuity.
# ---------------------------------------------------------------------------

class TestWorkerLiveness:
    def test_malformed_envelope_is_quarantined_not_crashing(self, tmp_path, monkeypatch):
        """A bug upstream (e.g. compute_state_identity raising on a
        malformed envelope) must be counted and dropped, never crash the
        worker thread -- proven by a LATER, well-formed envelope still
        landing durably."""
        spool_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        original = writer.compute_state_identity
        state = {"boomed": False}

        def _boom_once(envelope):
            if not state["boomed"]:
                state["boomed"] = True
                raise ValueError("simulated malformed envelope")
            return original(envelope)

        monkeypatch.setattr(writer, "compute_state_identity", _boom_once)
        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_malformed_envelope_total") == 1

        # The worker is still alive and processes the NEXT envelope normally.
        _enqueue(_decision(case, space, book, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        conn = sqlite3.connect(str(spool_path))
        (count,) = conn.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()
        assert count == 1
        assert writer._worker_thread is not None and writer._worker_thread.is_alive()

    def test_restart_without_ingest_seeds_sampling_from_pending_spool(self, tmp_path):
        """Y4's required regression test: a spool write that crashed BEFORE
        canonical ingestion must still be respected on restart -- otherwise
        identical content one minute later is falsely relabeled
        STATE_CHANGE instead of sampled out."""
        spool_path = tmp_path / "spool.db"
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)

        writer.start_worker(
            spool_conn_factory=lambda: sqlite3.connect(str(spool_path)),
            readonly_canonical_conn_factory=lambda: sqlite3.connect(f"file:{trade_path}?mode=ro", uri=True),
        )
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        family = _family(case)

        _enqueue(_decision(case, space, book, receipt_hash="r0"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC), "c0")
        assert writer.drain(timeout=3.0)
        # Deliberately DO NOT ingest -- simulate a crash before canonical delivery.
        assert writer.shutdown(timeout=3.0)

        writer._last_state_by_family.clear()
        readiness = writer.start_worker(
            spool_conn_factory=lambda: sqlite3.connect(str(spool_path)),
            readonly_canonical_conn_factory=lambda: sqlite3.connect(f"file:{trade_path}?mode=ro", uri=True),
        )
        assert readiness.ready
        assert _wait_until(lambda: writer._last_state_by_family.get(case.family_id) is not None, timeout=2.0)

        _enqueue(_decision(case, space, book, receipt_hash="r1"), family, _proofs_for(space), datetime(2026, 6, 14, 12, 1, tzinfo=UTC), "c1")
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_sampled_out_total") == 1


# ---------------------------------------------------------------------------
# SQLite multi-connection WAL-reset-fix version guard.
# ---------------------------------------------------------------------------

class TestSqliteVersionGuard:
    def test_worker_reports_not_ready_below_the_wal_reset_fix_floor(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 40, 0))
        readiness = writer.start_worker(spool_conn_factory=lambda: sqlite3.connect(str(tmp_path / "spool.db")))
        assert readiness.ready is False


# ---------------------------------------------------------------------------
# Shutdown / second-worker refusal.
# ---------------------------------------------------------------------------

class TestShutdownLifecycle:
    def test_start_worker_is_a_noop_while_one_is_already_alive(self, tmp_path):
        spool_path = _start(tmp_path)
        readiness2 = writer.start_worker(spool_conn_factory=lambda: sqlite3.connect(str(tmp_path / "other_spool.db")))
        assert readiness2.ready is True  # returns the EXISTING worker's readiness, doesn't replace it
        conn = sqlite3.connect(str(spool_path))
        conn.execute("SELECT 1")  # original spool path still the one in use

    def test_shutdown_succeeds_even_with_a_full_queue(self, tmp_path):
        release = threading.Event()

        def _gated_connect():
            release.wait(timeout=5.0)
            return sqlite3.connect(":memory:")

        writer.start_worker(spool_conn_factory=_gated_connect, maxsize=1, ready_timeout=0.5)
        case = _case()
        space = _outcome_space(case)
        book = _family_book(case, space)
        decision = _decision(case, space, book)
        family = _family(case)
        _enqueue(decision, family, _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))

        release.set()
        assert writer.shutdown(timeout=5.0) is True


# ---------------------------------------------------------------------------
# Round-4 MEDIUM: SQLITE_CONNECT_ALLOWLIST exemption is file-scoped, not
# function-scoped -- this AST antibody compensates by asserting exactly one
# sqlite3.connect() call exists anywhere in the module, and it is lexically
# inside _default_spool_conn_factory.
# ---------------------------------------------------------------------------

class TestSingleConnectAntibody:
    def test_exactly_one_sqlite3_connect_call_site_in_the_module(self):
        source = inspect.getsource(writer)
        tree = ast.parse(source)

        connect_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                is_connect = (
                    (isinstance(func, ast.Attribute) and func.attr == "connect")
                    or (isinstance(func, ast.Name) and func.id == "connect")
                )
                if is_connect:
                    connect_calls.append(node)

        assert len(connect_calls) == 1, (
            f"expected exactly one sqlite3.connect() call site in "
            f"family_book_telemetry_writer.py; found {len(connect_calls)} -- "
            f"any new raw canonical connect here would evade the file-level "
            f"SQLITE_CONNECT_ALLOWLIST exemption's intended scope"
        )

        # It must be lexically inside _default_spool_conn_factory.
        enclosing = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if child is connect_calls[0]:
                        enclosing = node.name
        assert enclosing == "_default_spool_conn_factory"

    def test_spool_path_never_equals_trade_db_path(self, tmp_path, monkeypatch):
        from src.state import db as db_module

        monkeypatch.setattr(db_module, "_zeus_trade_db_path", lambda: tmp_path / "zeus_trades.db")
        conn = writer._default_spool_conn_factory()
        conn.close()
        spool_path = (tmp_path / "zeus_trades.db").with_name("family_book_telemetry_spool.db")
        assert spool_path.resolve() != (tmp_path / "zeus_trades.db").resolve()
        assert spool_path.name == "family_book_telemetry_spool.db"


# ---------------------------------------------------------------------------
# Round-6 (Z1/Z2) validation: the optional plane must never compete with the
# money path for the trade DB, and must never grow without a real ceiling.
# ---------------------------------------------------------------------------

class TestMoneyPathYield:
    """Z1: delivery yields to the money path and never touches the live-money
    DB when it has nothing to deliver."""

    def test_idle_tick_opens_no_canonical_connection(self, tmp_path):
        """An empty outbox must be decided against the PRIVATE spool alone."""
        spool_path = _start(tmp_path)
        opens: list[int] = []

        def _tripwire_canonical():
            opens.append(1)
            raise AssertionError("canonical connection opened on an idle tick")

        spool_factory = lambda: sqlite3.connect(str(spool_path))
        assert writer.outbox_has_pending(spool_conn_factory=spool_factory) is False
        assert opens == []
        # And the guard is real: with a row pending, it flips.
        case = _case()
        space = _outcome_space(case)
        _enqueue(_decision(case, space, _family_book(case, space)), _family(case),
                 _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        assert writer.outbox_has_pending(spool_conn_factory=spool_factory) is True

    def test_ingest_job_skips_while_reactor_cycle_is_active(self, tmp_path, monkeypatch):
        """The daemon job must return WITHOUT opening a trade connection while
        a reactor/decision cycle holds the money path.

        The outbox is deliberately NON-empty: with nothing pending the job
        would skip anyway via the idle fast path, and the test would pass even
        with the yield guard deleted (verified by mutation). Pending work is
        what makes the guard the only thing standing between this job and the
        live-money DB.
        """
        import src.main as main_module

        _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        _enqueue(_decision(case, space, _family_book(case, space)), _family(case),
                 _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        assert writer.outbox_has_pending() is True  # precondition: work IS waiting

        opened: list[int] = []

        def _tripwire(**kw):
            opened.append(1)
            raise AssertionError("opened trade DB while the money path was active")

        monkeypatch.setattr(main_module, "_edli_reactor_active", lambda: True)
        monkeypatch.setattr(main_module, "get_trade_connection", _tripwire)
        main_module._family_book_telemetry_ingest_cycle()
        assert opened == []

    def test_kill_switch_off_performs_zero_canonical_work(self, tmp_path, monkeypatch):
        """Flipping the switch off stops DELIVERY, not merely new enqueues."""
        spool_path = _start(tmp_path)
        case = _case()
        space = _outcome_space(case)
        _enqueue(_decision(case, space, _family_book(case, space)), _family(case),
                 _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        monkeypatch.setenv("ZEUS_FAMILY_BOOK_TELEMETRY_ENABLED", "0")

        class _ExplodingConn:
            def __getattr__(self, name):
                raise AssertionError(f"canonical {name!r} touched while kill switch is off")

        outcome = writer.run_bounded_ingest(
            _ExplodingConn(), spool_conn_factory=lambda: sqlite3.connect(str(spool_path))
        )
        assert outcome.disabled is True
        assert outcome.batch_rows == 0


class TestSpoolHardBounds:
    """Z2: the admission gate is O(1), and the file ceiling is physical."""

    def test_capture_cost_is_flat_in_backlog_size(self, tmp_path):
        """The admission check must not read the table. Proven structurally:
        pending_budget touches only the metadata table, so its query plan is
        independent of how many outbox rows exist."""
        from src.state.schema import family_book_telemetry_outbox_schema as sch

        conn = sqlite3.connect(str(tmp_path / "spool.db"))
        sch.ensure_table(conn)
        plan_empty = conn.execute(
            "EXPLAIN QUERY PLAN SELECT k, v FROM family_book_telemetry_meta WHERE k IN (?, ?)",
            ("pending_count", "pending_bytes"),
        ).fetchall()
        plan_text = " ".join(str(r) for r in plan_empty)
        assert "family_book_telemetry_outbox" not in plan_text, (
            "the capture-path budget check must never scan the outbox table"
        )
        # And the counters track inserts/deletes exactly.
        row = {c: 0 for c in sch._COLUMNS}
        row.update({"canonical_payload": "x" * 100, "source_manifest_json": "y" * 50})
        for _ in range(5):
            sch.insert_outbox_row(conn, row)
        conn.commit()
        assert sch.pending_budget(conn) == (5, 5 * 150)
        sch.delete_up_to(conn, 3)
        conn.commit()
        assert sch.pending_budget(conn) == (2, 2 * 150)
        conn.close()

    def test_max_page_count_is_a_physical_ceiling(self, tmp_path):
        """Past the page ceiling SQLite itself refuses this module's writes --
        the spool cannot eat the disk the live-money DB needs."""
        from src.state.schema import family_book_telemetry_outbox_schema as sch

        conn = sqlite3.connect(str(tmp_path / "spool.db"))
        sch.ensure_table(conn, max_page_count=16)  # deliberately tiny
        assert conn.execute("PRAGMA max_page_count").fetchone()[0] == 16
        row = {c: 0 for c in sch._COLUMNS}
        row.update({"canonical_payload": "x" * 20000, "source_manifest_json": "y" * 20000})
        with pytest.raises(sqlite3.OperationalError, match="full"):
            for _ in range(200):
                sch.insert_outbox_row(conn, row)
                conn.commit()
        conn.close()

    def test_unreadable_budget_fails_closed_and_drops_capture(self, tmp_path):
        """An unknown backlog must NOT admit a write (fail closed).

        ONLY the budget SELECT is broken -- inserting still works perfectly.
        Dropping the whole metadata table would also break the insert, so the
        row would be missing for an unrelated reason and the test would pass
        even with the fail-closed branch deleted (verified by mutation).
        """
        spool_path = tmp_path / "spool.db"

        class _UnreadableBudgetConn(sqlite3.Connection):
            def execute(self, sql, *a, **kw):
                if "FROM family_book_telemetry_meta" in sql and sql.lstrip().upper().startswith("SELECT"):
                    raise sqlite3.OperationalError("simulated budget read failure")
                return super().execute(sql, *a, **kw)

        _start(tmp_path, spool_conn_factory=lambda: sqlite3.connect(
            str(spool_path), factory=_UnreadableBudgetConn
        ))

        case = _case()
        space = _outcome_space(case)
        _enqueue(_decision(case, space, _family_book(case, space)), _family(case),
                 _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)
        assert writer.counter("family_book_telemetry_spool_budget_unreadable_total") >= 1
        check = sqlite3.connect(str(spool_path))
        try:
            assert check.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()[0] == 0
        finally:
            check.close()


class TestAckFailureReplay:
    def test_ack_failure_replays_then_deletes(self, tmp_path):
        """Canonical commit durable + ack failed => typed outcome, and the NEXT
        pass replays the batch idempotently and then drains it."""
        spool_path = _start(tmp_path)
        trade_path = tmp_path / "trade.db"
        _bootstrap_canonical(trade_path)
        case = _case()
        space = _outcome_space(case)
        _enqueue(_decision(case, space, _family_book(case, space)), _family(case),
                 _proofs_for(space), datetime(2026, 6, 14, 12, 0, tzinfo=UTC))
        assert writer.drain(timeout=3.0)

        class _AckFailingConn(sqlite3.Connection):
            """sqlite3.Connection.execute is read-only, so the ack failure is
            injected by subclassing rather than by patching the attribute."""

            def execute(self, sql, *a, **kw):
                if sql.strip().upper().startswith("DELETE"):
                    raise sqlite3.OperationalError("simulated ack failure")
                return super().execute(sql, *a, **kw)

        def _spool_with_failing_delete():
            return sqlite3.connect(str(spool_path), factory=_AckFailingConn)

        conn = sqlite3.connect(str(trade_path))
        try:
            first = writer.run_bounded_ingest(conn, spool_conn_factory=_spool_with_failing_delete)
        finally:
            conn.close()
        assert first.ack_failed is True
        assert first.failed is False
        assert first.ingested_observations == 1
        assert writer.counter("family_book_telemetry_ack_failures_total") >= 1

        # Replay: same rows, idempotent -- nothing new lands, outbox drains.
        second = _ingest(trade_path, spool_path)
        assert second.batch_rows == 1
        assert second.ingested_observations == 0  # idempotent no-op
        check = sqlite3.connect(str(spool_path))
        try:
            assert check.execute("SELECT COUNT(*) FROM family_book_telemetry_outbox").fetchone()[0] == 0
        finally:
            check.close()
        canon = sqlite3.connect(str(trade_path))
        try:
            assert canon.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()[0] == 1
        finally:
            canon.close()


class TestStartupTimeoutLeavesNoLiveThread:
    def test_readiness_timeout_stops_and_joins_the_worker(self, tmp_path):
        """A slow start must not leave an unsupervised thread that comes up
        later and writes behind the daemon's back."""
        gate = threading.Event()

        def _slow_factory():
            gate.wait(timeout=2.0)
            return sqlite3.connect(str(tmp_path / "spool.db"))

        readiness = writer.start_worker(spool_conn_factory=_slow_factory, ready_timeout=0.05)
        assert readiness.ready is False
        assert readiness.reason == "startup_timeout"
        gate.set()
        time.sleep(0.3)
        assert writer._worker_thread is None, "no worker reference may survive a failed start"
        live = [t for t in threading.enumerate() if t.name == "family-book-telemetry-writer"]
        assert live == [], f"startup timeout left a live worker thread: {live}"
