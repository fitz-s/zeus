# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   deep-review NO-GO section 3 required validation: "production-shaped
#   serialization benchmark: row bytes + insert latency p50/p95/p99 reported
#   in PLAN.md." This test MEASURES and PRINTS those numbers (run with
#   `-s` to see them); the numbers are copied into PLAN.md by hand after a run
#   (not asserted as regression thresholds -- machine-dependent).
"""Benchmark: family_book_telemetry_writer serialization + insert cost at a
production-shaped family size (51 bins, matching the scout's family count)."""
from __future__ import annotations

import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from src.config import City
from src.decision.family_decision_engine import FamilyDecision
from src.events.candidate_binding import EventBoundCandidateFamily
from src.events.family_book_manifest import build_manifest, compute_state_identity
from src.execution.family_book import ExecutableLadder, MarketBook, build_family_book
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import DebiasAuthority
from src.forecast.predictive_distribution_builder import PredictiveDistributionBuilder
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.outcome_space import OutcomeBin, OutcomeSpace, compute_topology_hash
from src.state.schema.family_book_observations_schema import ensure_table as ensure_obs_table, insert_observation
from src.state.schema.family_book_states_schema import ensure_table as ensure_states_table, insert_state
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
    levels = (QuoteLevel(Decimal("0.30"), Decimal("500")),) * 5  # top-5, matching production cap intent
    return MarketBook(condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}", no_token_id=f"no-{bin_id}", yes_asks=_ladder("ask", levels), yes_bids=_ladder("bid", levels), no_asks=_ladder("ask", levels), no_bids=_ladder("bid", levels), neg_risk=False)


@dataclass
class _FakeProof:
    bin_id: str
    executable_snapshot_id: Optional[str]
    row: Optional[dict]
    direction: str = "YES"


def _candidate_bin_id(p: _FakeProof) -> str:
    return p.bin_id


def _proofs(space: OutcomeSpace):
    return tuple(_FakeProof(bin_id=b.bin_id, executable_snapshot_id=f"snap-{b.bin_id}", row={"raw_orderbook_hash": f"hash-{b.bin_id}", "captured_at": "2026-06-14T12:00:00+00:00"}) for b in space.bins)


def _member(model_id, value_native, case) -> RawModelMember:
    return RawModelMember(model_id=model_id, product_id=f"{model_id}_mx2t3", source_run_id=f"{model_id}_run", source_cycle_time_utc=ISSUE - timedelta(hours=6), available_at_utc=ISSUE - timedelta(hours=1), value_native=value_native, station_mapping_id=f"{STATION}_wu_icao", raw_forecast_artifact_id=f"{model_id}_artifact", data_version="ecmwf_opendata_mx2t3_local_calendar_day_max")


def _model_set(values, case) -> FreshModelSet:
    import numpy as np
    members = tuple(_member(f"m{i}", v, case) for i, v in enumerate(values))
    arr = np.asarray(values, dtype=float)
    return FreshModelSet(case=case, members=members, member_values_native=arr, min_native=float(arr.min()), max_native=float(arr.max()), model_set_hash="modelset_bench")


def _no_obs() -> Day0ObservationState:
    return Day0ObservationState(observed=False, station_id=STATION, source="none", samples_count=0, latest_observed_at_utc=None, observed_high_native=None, observed_low_native=None, observed_extreme_native=None, raw_observation_hash=None)


def _decision(case, space, book) -> FamilyDecision:
    predictive = PredictiveDistributionBuilder(DebiasAuthority(())).build(case, _model_set([24.5, 25.0, 25.5], case), _no_obs(), has_fusion_capture=True)
    return FamilyDecision(decision_id="bench-decision", case=case, predictive=predictive, omega=space, joint_q=None, band=None, family_book=book, market_coherence=None, candidates=(), selected=None, no_trade_reason="BENCH", receipt_hash="bench-hash")


def _family(case) -> EventBoundCandidateFamily:
    return EventBoundCandidateFamily(family_id=case.family_id, event_id="e1", event_type="FORECAST_SNAPSHOT_READY", city=case.city, target_date=case.target_local_date.isoformat(), metric=case.metric, condition_ids=(), yes_token_ids=(), no_token_ids=(), bins=(), candidates=(), causal_snapshot_id="snap-causal", market_topology_source="executable_market_snapshots", binding_hash="binding-hash")


def _percentile(values: list[float], pct: float) -> float:
    values = sorted(values)
    idx = min(int(len(values) * pct), len(values) - 1)
    return values[idx]


def test_benchmark_manifest_and_insert_latency(tmp_path, capsys):
    case = _case()
    space = _wide_outcome_space(case)
    markets = {b.bin_id: _market(b.bin_id) for b in space.bins}
    book = build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)
    decision = _decision(case, space, book)
    family = _family(case)
    proofs = _proofs(space)

    conn = sqlite3.connect(str(tmp_path / "bench.db"))
    ensure_states_table(conn)
    ensure_obs_table(conn)

    n_iters = 200
    manifest_latencies = []
    payload_bytes = []
    insert_latencies = []

    for i in range(n_iters):
        t0 = time.perf_counter()
        manifest = build_manifest(decision, active_proofs=proofs, candidate_bin_id=_candidate_bin_id)
        state_id, content_hash, canonical_payload = compute_state_identity(
            family_id=case.family_id, topology_hash=space.topology_hash,
            complete_book=book.complete_book, manifest=manifest,
        )
        manifest_latencies.append(time.perf_counter() - t0)
        payload_bytes.append(len(canonical_payload.encode("utf-8")))

        t1 = time.perf_counter()
        insert_state(
            conn, state_id=f"{state_id}-{i}", family_id=case.family_id, content_hash=f"{content_hash}-{i}",
            topology_hash=space.topology_hash, complete_book=book.complete_book,
            canonical_payload=canonical_payload, first_seen_decision_time="2026-06-14T12:00:00+00:00",
        )
        conn.commit()
        insert_latencies.append(time.perf_counter() - t1)

    with capsys.disabled():
        print("\n--- family_book_telemetry benchmark (N_BINS=%d, n_iters=%d) ---" % (N_BINS, n_iters))
        print(f"canonical_payload bytes: mean={statistics.mean(payload_bytes):.0f} "
              f"p50={_percentile(payload_bytes, 0.50):.0f} p95={_percentile(payload_bytes, 0.95):.0f} "
              f"p99={_percentile(payload_bytes, 0.99):.0f}")
        print(f"manifest+hash build (ms): p50={_percentile(manifest_latencies, 0.50)*1000:.3f} "
              f"p95={_percentile(manifest_latencies, 0.95)*1000:.3f} "
              f"p99={_percentile(manifest_latencies, 0.99)*1000:.3f}")
        print(f"state insert+commit (ms): p50={_percentile(insert_latencies, 0.50)*1000:.3f} "
              f"p95={_percentile(insert_latencies, 0.95)*1000:.3f} "
              f"p99={_percentile(insert_latencies, 0.99)*1000:.3f}")

    # Sanity bounds only (not tight perf assertions -- machine-dependent; the
    # printed numbers above are what gets copied into PLAN.md).
    assert _percentile(manifest_latencies, 0.99) < 0.05
    assert _percentile(insert_latencies, 0.99) < 0.5
