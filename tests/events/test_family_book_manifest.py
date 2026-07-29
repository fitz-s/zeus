# Created: 2026-07-29
# Last reused or audited: 2026-07-29
# Authority basis: docs/operations/current/book_snapshot_persistence/PLAN.md --
#   redesign after deep-review NO-GO. Covers the manifest builder, the
#   timestamp-free content hash (the fix for the verified book_hash/dedup
#   bug), and the tightened market_center_native coverage requirement.
"""Tests for src/events/family_book_manifest.py."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

from src.config import City
from src.events.family_book_manifest import (
    build_manifest,
    compute_state_identity,
    market_center_native,
    market_center_status,
    market_q_fields,
    model_q_fields,
)
from src.execution.family_book import ExecutableLadder, MarketBook, build_family_book
from src.forecast.day0_conditioner import Day0ObservationState
from src.forecast.debias_authority import DebiasAuthority
from src.forecast.predictive_distribution_builder import PredictiveDistributionBuilder
from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.outcome_space import OutcomeBin, OutcomeSpace, compute_topology_hash
from src.strategy.live_inference.executable_cost import QuoteLevel

ISSUE = datetime(2026, 6, 14, 0, 0, 0)
STATION = "RJTT"
_CAPTURED = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures (same shape as tests/execution/test_family_book.py /
# tests/decision/test_family_decision_engine.py).
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


def _ladder(side: str, levels: tuple = (), *, tick="0.01", min_order="1.0", fee=0.05) -> ExecutableLadder:
    return ExecutableLadder(
        levels=levels, side=side, fee_rate=fee,
        min_tick_size=Decimal(tick), min_order_size=Decimal(min_order),
    )


def _thin_market(bin_id: str, **kw) -> MarketBook:
    return MarketBook(
        condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}", yes_asks=_ladder("ask", **kw), yes_bids=_ladder("bid", **kw),
        no_asks=_ladder("ask", **kw), no_bids=_ladder("bid", **kw), neg_risk=False,
    )


def _quoted_market(bin_id: str, *, yes_ask: float, yes_bid: float, **kw) -> MarketBook:
    return MarketBook(
        condition_id=f"cond-{bin_id}", bin_id=bin_id, yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        yes_asks=_ladder("ask", (QuoteLevel(Decimal(str(yes_ask)), Decimal("500")),), **kw),
        yes_bids=_ladder("bid", (QuoteLevel(Decimal(str(yes_bid)), Decimal("500")),), **kw),
        no_asks=_ladder("ask", **kw), no_bids=_ladder("bid", **kw), neg_risk=False,
    )


def _all_quoted_family_book(case: ForecastCase, space: OutcomeSpace, **kw) -> "Any":
    """complete_book True, EVERY bin (incl. non-executable tail) two-sided quoted."""
    markets = {b.bin_id: _quoted_market(b.bin_id, yes_ask=0.30, yes_bid=0.20, **kw) for b in space.bins}
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


def _partially_quoted_family_book(case: ForecastCase, space: OutcomeSpace) -> "Any":
    """complete_book True, but only b25/b27 quoted -- must now be NULL (tightened rule)."""
    markets = {}
    for b in space.bins:
        if b.bin_id in ("b25", "b27"):
            markets[b.bin_id] = _quoted_market(b.bin_id, yes_ask=0.30, yes_bid=0.20)
        else:
            markets[b.bin_id] = _thin_market(b.bin_id)
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


def _incomplete_family_book(case: ForecastCase, space: OutcomeSpace) -> "Any":
    markets = {b.bin_id: _thin_market(b.bin_id) for b in space.bins if b.bin_id != "b_high"}
    return build_family_book(omega=space, markets=markets, captured_at_utc=_CAPTURED)


@dataclass
class _FakeProof:
    bin_id: str
    executable_snapshot_id: Optional[str]
    row: Optional[dict]
    direction: str = "YES"


def _candidate_bin_id(proof: _FakeProof) -> str:
    return proof.bin_id


def _proofs_for(space: OutcomeSpace, *, snapshot_suffix: str = "1", raw_hash: str = "hash-a") -> tuple[_FakeProof, ...]:
    return tuple(
        _FakeProof(
            bin_id=b.bin_id,
            executable_snapshot_id=f"snap-{b.bin_id}-{snapshot_suffix}",
            row={"raw_orderbook_hash": raw_hash, "captured_at": f"2026-06-14T12:0{snapshot_suffix}:00+00:00"},
        )
        for b in space.bins
    )


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
    import numpy as np

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


def _decision(case, space, family_book, *, joint_q=None, market_implied_q=None, selected=None):
    from src.decision.family_decision_engine import FamilyDecision

    return FamilyDecision(
        decision_id="test-decision", case=case, predictive=_predictive(case), omega=space,
        joint_q=joint_q, band=None, family_book=family_book, market_coherence=None,
        candidates=(), selected=selected, no_trade_reason="TEST_NO_TRADE", receipt_hash="test-hash",
        market_implied_q=market_implied_q,
    )


# ---------------------------------------------------------------------------
# build_manifest: identity/hash/source-time from proofs, metadata from FamilyBook.
# ---------------------------------------------------------------------------

class TestBuildManifest:
    def test_manifest_carries_snapshot_identity_from_proofs_and_metadata_from_book(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space, tick="0.01", min_order="5.0", fee=0.07)
        proofs = _proofs_for(space)
        manifest = build_manifest(_decision(case, space, book), active_proofs=proofs, candidate_bin_id=_candidate_bin_id)

        by_bin = {e["bin_id"]: e for e in manifest}
        assert set(by_bin) == set(b.bin_id for b in space.bins)
        entry = by_bin["b25"]
        assert entry["executable_snapshot_id"] == "snap-b25-1"
        assert entry["raw_orderbook_hash"] == "hash-a"
        assert entry["source_captured_at"] == "2026-06-14T12:01:00+00:00"
        assert entry["condition_id"] == "cond-b25"
        assert entry["min_tick_size"] == "0.01"
        assert entry["min_order_size"] == "5.0"
        assert entry["fee_rate"] == 0.07
        assert entry["neg_risk"] is False

    def test_bin_with_no_matching_proof_gets_null_identity_not_dropped(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        proofs = tuple(p for p in _proofs_for(space) if p.bin_id != "b25")  # b25 missing
        manifest = build_manifest(_decision(case, space, book), active_proofs=proofs, candidate_bin_id=_candidate_bin_id)
        by_bin = {e["bin_id"]: e for e in manifest}
        assert "b25" in by_bin  # bin present (from FamilyBook), just no identity
        assert by_bin["b25"]["executable_snapshot_id"] is None
        assert by_bin["b25"]["raw_orderbook_hash"] is None


# ---------------------------------------------------------------------------
# compute_state_identity: THE core fix -- timestamp-free content hash.
# ---------------------------------------------------------------------------

class TestComputeStateIdentity:
    def test_identical_content_at_different_capture_times_hashes_equal(self):
        """THE verified bug this table fixes: FamilyBook.book_hash hashes
        captured_at_utc (live bridge sets it = decision_time), so an unchanged
        book never dedups. content_hash must NOT reproduce that: same content,
        different source_captured_at/executable_snapshot_id (as a live rebuild
        one cycle later would have) -> SAME content_hash."""
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        decision = _decision(case, space, book)

        manifest_t0 = build_manifest(decision, active_proofs=_proofs_for(space, snapshot_suffix="1"), candidate_bin_id=_candidate_bin_id)
        manifest_t1 = build_manifest(decision, active_proofs=_proofs_for(space, snapshot_suffix="2"), candidate_bin_id=_candidate_bin_id)

        # Fixture sanity: the two manifests really do differ in snapshot_id/time.
        assert manifest_t0[0]["executable_snapshot_id"] != manifest_t1[0]["executable_snapshot_id"]
        assert manifest_t0[0]["source_captured_at"] != manifest_t1[0]["source_captured_at"]

        id0 = compute_state_identity(
            family_id=case.family_id, topology_hash=space.topology_hash,
            complete_book=book.complete_book, manifest=manifest_t0,
        )
        id1 = compute_state_identity(
            family_id=case.family_id, topology_hash=space.topology_hash,
            complete_book=book.complete_book, manifest=manifest_t1,
        )
        assert id0[0] == id1[0]  # state_id
        assert id0[1] == id1[1]  # content_hash

    def test_changed_raw_orderbook_hash_changes_content_hash(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        decision = _decision(case, space, book)
        manifest_a = build_manifest(decision, active_proofs=_proofs_for(space, raw_hash="hash-a"), candidate_bin_id=_candidate_bin_id)
        manifest_b = build_manifest(decision, active_proofs=_proofs_for(space, raw_hash="hash-b"), candidate_bin_id=_candidate_bin_id)
        id_a = compute_state_identity(family_id=case.family_id, topology_hash=space.topology_hash, complete_book=True, manifest=manifest_a)
        id_b = compute_state_identity(family_id=case.family_id, topology_hash=space.topology_hash, complete_book=True, manifest=manifest_b)
        assert id_a[1] != id_b[1]

    def test_changed_fee_or_tick_changes_content_hash(self):
        case = _case()
        space = _outcome_space(case)
        book_a = _all_quoted_family_book(case, space, tick="0.01", fee=0.05)
        book_b = _all_quoted_family_book(case, space, tick="0.02", fee=0.05)
        proofs = _proofs_for(space)
        manifest_a = build_manifest(_decision(case, space, book_a), active_proofs=proofs, candidate_bin_id=_candidate_bin_id)
        manifest_b = build_manifest(_decision(case, space, book_b), active_proofs=proofs, candidate_bin_id=_candidate_bin_id)
        id_a = compute_state_identity(family_id=case.family_id, topology_hash=space.topology_hash, complete_book=True, manifest=manifest_a)
        id_b = compute_state_identity(family_id=case.family_id, topology_hash=space.topology_hash, complete_book=True, manifest=manifest_b)
        assert id_a[1] != id_b[1]

    def test_canonical_payload_is_valid_json_and_round_trips(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        manifest = build_manifest(_decision(case, space, book), active_proofs=_proofs_for(space), candidate_bin_id=_candidate_bin_id)
        _, _, payload = compute_state_identity(
            family_id=case.family_id, topology_hash=space.topology_hash, complete_book=True, manifest=manifest,
        )
        parsed = json.loads(payload)
        assert parsed["family_id"] == case.family_id
        assert len(parsed["bins"]) == len(space.bins)


# ---------------------------------------------------------------------------
# market_center_native / market_center_status -- tightened coverage rule.
# ---------------------------------------------------------------------------

class TestMarketCenter:
    def test_full_executable_coverage_resolves_to_analytic_center(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)  # every bin quoted identically 0.25 mid
        value = market_center_native(book)
        status = market_center_status(book)
        assert status == "OK"
        assert value is not None

    def test_partial_coverage_on_complete_book_is_now_null_not_a_number(self):
        """Deep-review 2026-07-29 fix: a family where only 2 of 11 bins are
        quoted is NOT an identified family-wide expectation -- must be NULL,
        not a number silently computed from the quoted subset."""
        case = _case()
        space = _outcome_space(case)
        book = _partially_quoted_family_book(case, space)
        assert book.complete_book is True  # every bin HAS a MarketBook (thin or quoted)
        assert market_center_native(book) is None
        assert market_center_status(book) == "INSUFFICIENT_COVERAGE"

    def test_incomplete_book_is_null(self):
        case = _case()
        space = _outcome_space(case)
        book = _incomplete_family_book(case, space)
        assert market_center_native(book) is None
        assert market_center_status(book) == "INCOMPLETE_BOOK"


# ---------------------------------------------------------------------------
# model_q_fields / market_q_fields.
# ---------------------------------------------------------------------------

class TestQFields:
    def test_model_q_fields_none_when_joint_q_none(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        decision = _decision(case, space, book, joint_q=None)
        assert model_q_fields(decision) == (None, None)

    def test_model_q_fields_populated_ordered_by_bin_id(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        fake_joint_q = SimpleNamespace(
            q_by_bin_id={b.bin_id: 1.0 / len(space.bins) for b in space.bins},
            identity_hash="jq-hash-1",
        )
        decision = _decision(case, space, book, joint_q=fake_joint_q)
        q_json, identity_hash = model_q_fields(decision)
        assert identity_hash == "jq-hash-1"
        parsed = json.loads(q_json)
        assert set(parsed) == set(b.bin_id for b in space.bins)

    def test_market_q_fields_none_when_market_implied_q_none(self):
        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        decision = _decision(case, space, book, market_implied_q=None)
        fields = market_q_fields(decision)
        assert fields["market_q_json"] is None
        assert fields["market_q_basis"] is None

    def test_market_q_fields_populated_aligned_to_omega_bins(self):
        import numpy as np

        case = _case()
        space = _outcome_space(case)
        book = _all_quoted_family_book(case, space)
        n = len(space.bins)
        fake_miq = SimpleNamespace(
            q=np.full(n, 1.0 / n), basis="DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1",
            depth_score=0.9, spread_score=0.02, projection_error=0.001, book_hash="miq-book-hash",
        )
        decision = _decision(case, space, book, market_implied_q=fake_miq)
        fields = market_q_fields(decision)
        parsed = json.loads(fields["market_q_json"])
        assert set(parsed) == set(b.bin_id for b in space.bins)
        assert fields["market_q_basis"] == "DEFRICTIONED_FAMILY_BOOK_MIDPOINT_PROJECTION_V1"
        assert fields["market_q_book_hash"] == "miq-book-hash"
