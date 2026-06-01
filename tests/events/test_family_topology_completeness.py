# Created: 2026-06-01
# Last reused/audited: 2026-06-01
# Authority basis: Task #114 ELEVATION S6 — FDR family-completeness enforcement.
#   validate_bin_topology-at-construction antibody (PRIMARY plan, critic-corrected).
#   gates_on_q=FALSE: pure topology precondition on family.bins, orthogonal to q.
"""Relationship test: an event-bound candidate family MUST carry a complete
integer-partition bin topology (full support, -inf..+inf), else construction
fails at bind time with CandidateBindingError — NOT silently downstream where
the MC re-normalization (src/signal/ensemble_signal.py:264 `p = p / total`)
masks settlement mass that falls OUTSIDE the modeled bins.

The cross-module invariant under test:
  bind_event_to_candidate_family() output -> validate_bin_topology(family.bins)
A family whose bins do not form a complete partition (the NYC-type dup +
no-shoulder topology, where settlement can land outside modeled support) must
be rejected at construction. A legitimately delisted/subset family that STILL
forms a complete partition (open-ended shoulders present) MUST still construct
(M2 carve-out is load-bearing: no trade-halt for legitimate subset markets).
"""

import pytest

from src.events.candidate_binding import (
    CandidateBindingError,
    MarketTopologyCandidate,
    bind_event_to_candidate_family,
)
from src.events.opportunity_event import (
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.types.market import Bin, BinTopologyError, validate_bin_topology


DECISION_TIME = "2026-05-24T12:00:00+00:00"


def _forecast_payload(
    *,
    city: str = "Chicago",
    target_date: str = "2026-05-25",
    metric: str = "high",
    snapshot_id: str = "snapshot-1",
) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric=metric,
        source_id="ecmwf_open_data",
        source_run_id="source-run-1",
        cycle="00",
        track="mx2t6_high_full_horizon",
        snapshot_id=snapshot_id,
        snapshot_hash="hash-1",
        captured_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="READY",
    )


def _forecast_event(*, causal_snapshot_id: str = "snapshot-1"):
    payload = _forecast_payload(snapshot_id=causal_snapshot_id)
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{payload.city}|{payload.target_date}|{payload.metric}",
        source="forecast",
        observed_at="2026-05-24T09:00:00+00:00",
        available_at="2026-05-24T10:00:00+00:00",
        received_at="2026-05-24T10:01:00+00:00",
        payload=payload,
        causal_snapshot_id=causal_snapshot_id,
    )


def _candidate(
    *,
    bin: Bin,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
    city: str = "Chicago",
    target_date: str = "2026-05-25",
    metric: str = "high",
) -> MarketTopologyCandidate:
    return MarketTopologyCandidate(
        city=city,
        target_date=target_date,
        metric=metric,
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        bin=bin,
        market_slug="chicago-high-2026-05-25",
    )


def _nyc_type_dup_no_shoulder_topology() -> list[MarketTopologyCandidate]:
    """NYC-type family: closed interior bins ONLY, no open-ended shoulders.

    Two contiguous closed °F bins (70-71, 72-73). Because neither end carries a
    -inf / +inf shoulder, settlement values BELOW 70 or ABOVE 73 land OUTSIDE
    modeled support — exactly the topology validate_bin_topology rejects
    (leftmost bin low != -inf). This is the class the downstream MC
    re-normalization silently masks.
    """
    return [
        _candidate(
            bin=Bin(low=70, high=71, unit="F", label="70-71°F"),
            condition_id="condition-1",
            yes_token_id="yes-1",
            no_token_id="no-1",
        ),
        _candidate(
            bin=Bin(low=72, high=73, unit="F", label="72-73°F"),
            condition_id="condition-2",
            yes_token_id="yes-2",
            no_token_id="no-2",
        ),
    ]


def _legit_subset_full_support_topology() -> list[MarketTopologyCandidate]:
    """M2 carve-out: a legitimately delisted/subset family that STILL forms a
    complete partition because open-ended shoulders cover full support.

    Only one interior closed bin remains (72-73°F) but the family is bracketed
    by '71°F or below' and '74°F or above' shoulders, so settlement can never
    land outside modeled support. This MUST construct — no trade-halt.
    """
    return [
        _candidate(
            bin=Bin(low=None, high=71, unit="F", label="71°F or below"),
            condition_id="condition-low",
            yes_token_id="yes-low",
            no_token_id="no-low",
        ),
        _candidate(
            bin=Bin(low=72, high=73, unit="F", label="72-73°F"),
            condition_id="condition-mid",
            yes_token_id="yes-mid",
            no_token_id="no-mid",
        ),
        _candidate(
            bin=Bin(low=74, high=None, unit="F", label="74°F or above"),
            condition_id="condition-high",
            yes_token_id="yes-high",
            no_token_id="no-high",
        ),
    ]


# ---------------------------------------------------------------------------
# (1) STRUCTURAL INVARIANT (the gap the antibody closes): the NYC-type bin set
# is NOT a complete integer partition — validate_bin_topology REJECTS it because
# the leftmost bin carries no -inf shoulder, so settlement can land outside
# modeled support. At HEAD this set bound into a family with NO error (the gap);
# the GREEN guard turns that exact set into a CandidateBindingError at bind time
# (proved in test (3)). This test pins the structural fact the guard relies on.
# ---------------------------------------------------------------------------
def test_nyc_type_dup_no_shoulder_topology_is_not_a_complete_partition():
    topology = _nyc_type_dup_no_shoulder_topology()
    bins = [candidate.bin for candidate in topology]

    # The structural defect that downstream MC re-normalization would mask:
    # validate_bin_topology REJECTS this bin set (no -inf shoulder => settlement
    # outside modeled support). This is the precondition violation the
    # at-construction antibody now catches at bind time.
    with pytest.raises(BinTopologyError):
        validate_bin_topology(bins)


# ---------------------------------------------------------------------------
# (2) M2 REGRESSION: a legitimate delisted/subset family with full-support
# shoulders MUST still construct — both at HEAD and after the antibody lands.
# ---------------------------------------------------------------------------
def test_legit_subset_full_support_family_still_constructs():
    event = _forecast_event()
    topology = _legit_subset_full_support_topology()

    family = bind_event_to_candidate_family(event, topology, decision_time=DECISION_TIME)

    assert len(family.bins) == 3
    # The carve-out invariant: full-support family passes topology validation, so
    # the at-construction guard must NOT halt it.
    validate_bin_topology(list(family.bins))  # does not raise


# ---------------------------------------------------------------------------
# (3) GREEN target: after the guard lands, the NYC-type family raises
# CandidateBindingError (routed to NO_TRADE by the reactor) at bind time.
# ---------------------------------------------------------------------------
def test_nyc_type_family_rejected_at_construction_after_guard():
    event = _forecast_event()
    topology = _nyc_type_dup_no_shoulder_topology()

    with pytest.raises(CandidateBindingError, match="FDR_FAMILY_TOPOLOGY_INCOMPLETE"):
        bind_event_to_candidate_family(event, topology, decision_time=DECISION_TIME)
