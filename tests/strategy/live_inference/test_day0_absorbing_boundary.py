# Created: 2026-05-24
# Last reused/audited: 2026-05-24
# Authority basis: EDLI v1 implementation prompt §9 Day0 absorbing boundary contract.
from __future__ import annotations

from src.events.triggers.day0_extreme_updated import Day0HardFactGate
from src.strategy.live_inference.absorbing_boundary import evaluate_day0_absorbing_boundary


class FakeSettlementSemantics:
    def __init__(self, rounded: int) -> None:
        self.rounded = rounded
        self.calls: list[float] = []

    def round_single(self, value: float) -> int:
        self.calls.append(value)
        return self.rounded


def _gate(**overrides) -> Day0HardFactGate:
    values = {
        "source_match_status": "MATCH",
        "local_date_status": "MATCH",
        "station_match_status": "MATCH",
        "dst_status": "UNAMBIGUOUS",
        "metric_match_status": "MATCH",
        "rounding_status": "MATCH",
        "source_authorized_status": "AUTHORIZED",
    }
    values.update(overrides)
    return Day0HardFactGate(**values)


def test_high_finite_bin_killed_when_rounded_high_exceeds_upper():
    sem = FakeSettlementSemantics(76)
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=75.6,
        bin_kind="finite_range",
        lower=70,
        upper=75,
        settlement_semantics=sem,
        hard_fact_gate=_gate(),
    )
    assert result.killed is True
    assert sem.calls == [75.6]


def test_low_finite_bin_killed_when_rounded_low_below_lower():
    result = evaluate_day0_absorbing_boundary(
        metric="low",
        raw_extreme_so_far=49.4,
        bin_kind="finite_range",
        lower=50,
        upper=55,
        settlement_semantics=FakeSettlementSemantics(49),
        hard_fact_gate=_gate(),
    )
    assert result.killed is True


def test_upper_high_shoulder_fact_true():
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=80.1,
        bin_kind="open_shoulder",
        lower=80,
        upper=None,
        settlement_semantics=FakeSettlementSemantics(80),
        hard_fact_gate=_gate(),
    )
    assert result.fact_true is True


def test_lower_low_shoulder_fact_true():
    result = evaluate_day0_absorbing_boundary(
        metric="low",
        raw_extreme_so_far=29.9,
        bin_kind="open_shoulder",
        lower=None,
        upper=30,
        settlement_semantics=FakeSettlementSemantics(30),
        hard_fact_gate=_gate(),
    )
    assert result.fact_true is True


def test_source_mismatch_blocks_fact_true():
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=80.1,
        bin_kind="open_shoulder",
        lower=80,
        upper=None,
        settlement_semantics=FakeSettlementSemantics(80),
        hard_fact_gate=_gate(source_match_status="MISMATCH"),
    )
    assert result.fact_true is False
    assert result.reason == "HARD_FACT_GATE_BLOCKED"


def test_station_mismatch_blocks():
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=80.1,
        bin_kind="open_shoulder",
        lower=80,
        upper=None,
        settlement_semantics=FakeSettlementSemantics(80),
        hard_fact_gate=_gate(station_match_status="MISMATCH"),
    )
    assert result.fact_true is False


def test_metric_swap_blocks():
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=80.1,
        bin_kind="open_shoulder",
        lower=80,
        upper=None,
        settlement_semantics=FakeSettlementSemantics(80),
        hard_fact_gate=_gate(metric_match_status="MISMATCH"),
    )
    assert result.fact_true is False


def test_dst_ambiguous_local_date_blocks():
    result = evaluate_day0_absorbing_boundary(
        metric="low",
        raw_extreme_so_far=29.9,
        bin_kind="open_shoulder",
        lower=None,
        upper=30,
        settlement_semantics=FakeSettlementSemantics(30),
        hard_fact_gate=_gate(dst_status="AMBIGUOUS"),
    )
    assert result.fact_true is False


def test_settlement_semantics_used_not_python_round():
    sem = FakeSettlementSemantics(75)
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=74.5,
        bin_kind="open_shoulder",
        lower=75,
        upper=None,
        settlement_semantics=sem,
        hard_fact_gate=_gate(),
    )
    assert result.fact_true is True
    assert sem.calls == [74.5]


def test_openmeteo_diagnostic_fallback_never_live_source_truth():
    result = evaluate_day0_absorbing_boundary(
        metric="high",
        raw_extreme_so_far=75.1,
        bin_kind="open_shoulder",
        lower=75,
        upper=None,
        settlement_semantics=FakeSettlementSemantics(75),
        hard_fact_gate=_gate(source_authorized_status="DIAGNOSTIC_FALLBACK"),
    )
    assert result.fact_true is False
