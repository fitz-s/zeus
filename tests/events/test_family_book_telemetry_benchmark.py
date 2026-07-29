# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   deep-review NO-GO section 3 required validation: "production-shaped
#   serialization benchmark: row bytes + insert latency p50/p95/p99 reported
#   in PLAN.md," plus round-3 review 3.6 ("benchmark validity" -- benchmark
#   the full state+observation transaction end-to-end on a file-backed WAL
#   database, not just manifest/hash + state INSERT). This test MEASURES and
#   PRINTS those numbers (run with `-s` to see them); the numbers are copied
#   into PLAN.md by hand after a run (not asserted as regression thresholds
#   -- machine-dependent).
"""Benchmark: family_book_telemetry_writer end-to-end spool-write cost at a
production-shaped family size (51 bins, matching the scout's family count)."""
from __future__ import annotations

import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import src.events.family_book_telemetry_writer as writer
from src.config import City
from src.decision.family_decision_engine import FamilyDecision
from src.events.candidate_binding import EventBoundCandidateFamily
from src.events.family_book_manifest import compute_state_identity, project_observation_envelope
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
N_BINS = 51  # scout: 51 families; a single wide family approximates worst-case bin count


def _resolution(metric="high") -> EventResolution:
    city = City(name="Tokyo", lat=35.68, lon=139.69, timezone="Asia/Tokyo", settlement_unit="C", cluster="asia", wu_station=STATION, settlement_source_type="wu_icao")
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _case() -> ForecastCase:
    return ForecastCase(city="Tokyo", city_id="tokyo", station_id=STATION, settlement_source_type="wu_icao", target_local_date=date(2026, 6, 14), metric="high", issue_time_utc=ISSUE, lead_hours=6.0, season="summer", regime_key="zonal", unit="C", resolution=_resolution(), family_id="tokyo_high_2026-06-14", source_cycle_time_utc=ISSUE - timedelta(hours=6))


def _wide_outcome_space(case: ForecastCase) -> OutcomeSpace:
    rule = case.resolution.rounding_rule
    n_middle = N_BINS - 2
    bins = [OutcomeBin(bin_id="b_low", condition_id="cond-b_low", label="low", lower_native=None, upper_native=-1.0, yes_token_id="yes-b_low", no_token_id="no-b_low", executable=False, rounding_rule=rule)]
    for t in range(0, n_middle):
        bins.append(OutcomeBin(bin_id=f"b{t}", condition_id=f"cond-b{t}", label=f"{t}C", lower_native=float(t), upper_native=float(t), yes_token_id=f"yes-b{t}", no_token_id=f"no-b{t}", executable=True, rounding_rule=rule))
    bins.append(OutcomeBin(bin_id="b_high", condition_id="cond-b_high", label="high", lower_native=float(n_middle), upper_native=None, yes_token_id="yes-b_high", no_token_id="no-b_high", executable=False, rounding_rule=rule))
    space = OutcomeSpace(family_id=case.family_id, resolution=case.resolution, bins=tuple(bins), topology_hash=compute_topology_hash(case.family_id, case.resolution, bins))
    space.validate()
    return space


def _ladder(side, levels=()) -> ExecutableLadder:
    return ExecutableLadder(levels=levels, side=side, fee_rate=0.05, min_tick_size=Decimal("0.01"), min_order_size=Decimal("1.0"))


def _market(bin_id: str) -> MarketBook:
    levels = (QuoteLevel(Decimal("0.30"), Decimal("500")),)  # best-of-book only -- manifest never stores depth
    return MarketBook(condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}", no_token_id=f"no-{bin_id}", yes_asks=_ladder("ask", levels), yes_bids=_ladder("bid", levels), no_asks=_ladder("ask", levels), no_bids=_ladder("bid", levels), neg_risk=False)


@dataclass
class _FakeProof:
    bin_id: str
    executable_snapshot_id: Optional[str]
    row: Optional[dict]
    direction: str = "YES"


def _candidate_bin_id(p: _FakeProof) -> str:
    return p.bin_id


def _proofs(space: OutcomeSpace, *, variant: str = "1"):
    return tuple(_FakeProof(bin_id=b.bin_id, executable_snapshot_id=f"snap-{b.bin_id}", row={"raw_orderbook_hash": f"hash-{b.bin_id}-{variant}", "captured_at": "2026-06-14T12:00:00+00:00"}) for b in space.bins)


def _member(model_id, value_native, case) -> RawModelMember:
    return RawModelMember(model_id=model_id, product_id=f"{model_id}_mx2t3", source_run_id=f"{model_id}_run", source_cycle_time_utc=ISSUE - timedelta(hours=6), available_at_utc=ISSUE - timedelta(hours=1), value_native=value_native, station_mapping_id=f"{STATION}_wu_icao", raw_forecast_artifact_id=f"{model_id}_artifact", data_version="ecmwf_opendata_mx2t3_local_calendar_day_max")


def _model_set(values, case) -> FreshModelSet:
    import numpy as np
    members = tuple(_member(f"m{i}", v, case) for i, v in enumerate(values))
    arr = np.asarray(values, dtype=float)
    return FreshModelSet(case=case, members=members, member_values_native=arr, min_native=float(arr.min()), max_native=float(arr.max()), model_set_hash="modelset_bench")


def _no_obs() -> Day0ObservationState:
    return Day0ObservationState(observed=False, station_id=STATION, source="none", samples_count=0, latest_observed_at_utc=None, observed_high_native=None, observed_low_native=None, observed_extreme_native=None, raw_observation_hash=None)


def _decision(case, space, book, *, receipt_hash="bench-hash") -> FamilyDecision:
    predictive = PredictiveDistributionBuilder(DebiasAuthority(())).build(case, _model_set([24.5, 25.0, 25.5], case), _no_obs(), has_fusion_capture=True)
    return FamilyDecision(decision_id="bench-decision", case=case, predictive=predictive, omega=space, joint_q=None, band=None, family_book=book, market_coherence=None, candidates=(), selected=None, no_trade_reason="BENCH", receipt_hash=receipt_hash)


def _family(case) -> EventBoundCandidateFamily:
    return EventBoundCandidateFamily(family_id=case.family_id, event_id="e1", event_type="FORECAST_SNAPSHOT_READY", city=case.city, target_date=case.target_local_date.isoformat(), metric=case.metric, condition_ids=(), yes_token_ids=(), no_token_ids=(), bins=(), candidates=(), causal_snapshot_id="snap-causal", market_topology_source="executable_market_snapshots", binding_hash="binding-hash")


def _percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    idx = min(int(len(values) * pct), len(values) - 1)
    return values[idx]


def test_benchmark_envelope_projection_and_hash(tmp_path, capsys):
    """Decision-thread-side cost: project_observation_envelope + compute_state_identity."""
    case = _case()
    space = _wide_outcome_space(case)
    markets = {b.bin_id: _market(b.bin_id) for b in space.bins}
    book = build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)
    decision = _decision(case, space, book)
    family = _family(case)
    proofs = _proofs(space)

    n_iters = 200
    project_latencies = []
    hash_latencies = []
    payload_bytes = []

    for i in range(n_iters):
        t0 = time.perf_counter()
        envelope = project_observation_envelope(
            decision=decision, family=family, active_proofs=proofs,
            candidate_bin_id=_candidate_bin_id, decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC),
            causal_snapshot_id="causal-1",
        )
        project_latencies.append(time.perf_counter() - t0)

        t1 = time.perf_counter()
        _, _, canonical_payload = compute_state_identity(envelope)
        hash_latencies.append(time.perf_counter() - t1)
        payload_bytes.append(len(canonical_payload.encode("utf-8")))

    with capsys.disabled():
        print("\n--- family_book_telemetry benchmark: decision-thread projection (N_BINS=%d, n_iters=%d) ---" % (N_BINS, n_iters))
        print(f"project_observation_envelope (us): p50={_percentile(project_latencies, 0.50)*1e6:.1f} "
              f"p95={_percentile(project_latencies, 0.95)*1e6:.1f} p99={_percentile(project_latencies, 0.99)*1e6:.1f}")
        print(f"compute_state_identity (us): p50={_percentile(hash_latencies, 0.50)*1e6:.1f} "
              f"p95={_percentile(hash_latencies, 0.95)*1e6:.1f} p99={_percentile(hash_latencies, 0.99)*1e6:.1f}")
        print(f"canonical_payload bytes (content-only, X3): mean={statistics.mean(payload_bytes):.0f} "
              f"p50={_percentile(payload_bytes, 0.50):.0f} p95={_percentile(payload_bytes, 0.95):.0f} "
              f"p99={_percentile(payload_bytes, 0.99):.0f}")

    assert _percentile(project_latencies, 0.99) < 0.005  # decision-thread cost must stay sub-millisecond


def test_benchmark_end_to_end_spool_write(tmp_path, capsys):
    """Writer-thread cost: the FULL _process_one path (state+observation
    INSERT, explicit transaction, COMMIT) against a real file-backed WAL
    spool database -- not merely the state INSERT in isolation."""
    case = _case()
    space = _wide_outcome_space(case)
    markets = {b.bin_id: _market(b.bin_id) for b in space.bins}
    book = build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)
    family = _family(case)

    spool_path = tmp_path / "bench_spool.db"
    conn = sqlite3.connect(str(spool_path))
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_states_table(conn)
    ensure_obs_table(conn)

    n_iters = 200
    write_latencies = []
    for i in range(n_iters):
        # DISTINCT content every iteration (varying raw_orderbook_hash) so
        # every write is a genuine STATE_CHANGE insert+commit, not the fast
        # sampled-out path -- this measures the worst-case (every cycle
        # writes) transaction cost, not the steady-state (mostly sampled
        # out) cost.
        decision = _decision(case, space, book, receipt_hash=f"bench-hash-{i}")
        envelope = project_observation_envelope(
            decision=decision, family=family, active_proofs=_proofs(space, variant=str(i)),
            candidate_bin_id=_candidate_bin_id,
            decision_time=datetime(2026, 6, 14, 12, 0, tzinfo=UTC) + timedelta(minutes=i),
            causal_snapshot_id="causal-1",
        )
        t0 = time.perf_counter()
        conn = writer._process_one(conn, envelope)
        write_latencies.append(time.perf_counter() - t0)

    with capsys.disabled():
        print("\n--- family_book_telemetry benchmark: end-to-end spool write, worst-case every-cycle-changes (N_BINS=%d, n_iters=%d) ---" % (N_BINS, n_iters))
        print(f"_process_one (state+observation INSERT+COMMIT, ms): p50={_percentile(write_latencies, 0.50)*1000:.3f} "
              f"p95={_percentile(write_latencies, 0.95)*1000:.3f} p99={_percentile(write_latencies, 0.99)*1000:.3f}")

    (state_count,) = conn.execute("SELECT COUNT(*) FROM family_book_states").fetchone()
    (obs_count,) = conn.execute("SELECT COUNT(*) FROM family_book_observations").fetchone()
    assert state_count == n_iters  # every iteration has distinct content -- n_iters state rows
    assert obs_count == n_iters
    assert _percentile(write_latencies, 0.99) < 0.5
