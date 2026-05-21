# Lifecycle: created=2026-05-20; last_reviewed=2026-05-21; last_reused=2026-05-21
# Purpose: Relationship antibody (Fitz §3) — mutually-exclusive weather bins
#          (one city/date/metric partition) must NOT emit independent live
#          orders. P0-1 STAGE A single-best entry gate.
# Authority basis: operator P0-1 live-money spec 2026-05-20/21 (mutually-exclusive weather
#                  family sizing), STAGE A.

"""Relationship test: INV-family-exclusive-sizing.

Cross-module relationship under test:
    evaluator emits N EdgeDecision(should_trade=True) for one
    (city, target_date, metric) weather market (a PARTITION — exactly one bin
    resolves YES)  →  cycle_runtime's family gate  →  the executor.

Invariant (the cross-boundary property): when several mutually-exclusive bins
of the SAME family pass family-wise FDR, AT MOST ONE may flow to the executor
as should_trade=True. The dropped bins must carry an auditable
``mutually_exclusive_family_dedup`` reason string. Single-bin families are
unchanged (byte-identical to legacy per-edge path).

Why a relationship test, not a function test: the over-allocation bug lives at
the MOMENT a per-bin decision list crosses from the evaluator into the
execution loop — neither module is individually wrong, the relationship across
the boundary is. Per Fitz §3 this is authored RED before implementation.

RED→GREEN protocol (recorded for the opus critic):
  * ``test_legacy_baseline_emits_three_independent_orders`` pins the CURRENT
    (pre-gate / gate-disabled) behavior: family-wise FDR selects 3 bins →
    3 should_trade=True. This is the RED state the gate must fix.
  * ``test_same_city_date_metric_...`` asserts the REQUIRED post-gate behavior:
    exactly 1 should_trade for the family + 2 dropped bins carrying the dedup
    reason. With the gate disabled this assertion FAILS (3 != 1) — that is the
    RED proof; with the gate ON it is GREEN.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.contracts.no_trade_reason import NoTradeReason  # used in fixtures
from src.engine.evaluator import EdgeDecision
from src.strategy.family_exclusive_dedup import (
    FAMILY_REJECTION_STAGE,
    MUTUALLY_EXCLUSIVE_FAMILY_DEDUP,
    WeatherFamilyExposure,
    WeatherFamilyKey,
    dedup_mutually_exclusive_families,
    preselect_single_family_edge_before_kelly,
    weather_family_exposures_from_portfolio,
)
from src.engine.evaluator import (
    _expected_profit_usd_for_edge,
    _live_entry_economic_floor_rejection,
    _strategy_entry_price_floor_block_reason,
)
from src.contracts.semantic_types import RejectionStage
from src.types import Bin, BinEdge


CITY = "Chicago"
TARGET_DATE = "2026-05-25"
METRIC = "high"


# Five mutually-exclusive temperature bins for ONE (city, date, metric) market.
# Exactly one of them can resolve YES. Domain-valid per Bin invariant:
# °F non-shoulder bins cover exactly 2 settled degrees (high == low + 1);
# open ends are shoulder bins (unbounded width).
_BIN_SPECS = [
    # (low, high, label, support_index)
    (None, 19, "19°F or below", 0),
    (20, 21, "20-21°F", 1),
    (22, 23, "22-23°F", 2),
    (24, 25, "24-25°F", 3),
    (26, None, "26°F or above", 4),
]


def _bin_edge(spec, *, entry_price: float, forward_edge: float) -> BinEdge:
    low, high, label, support = spec
    return BinEdge(
        bin=Bin(low=low, high=high, unit="F", label=label),
        direction="buy_yes",
        edge=forward_edge,
        ci_lower=0.02,
        ci_upper=0.18,
        p_model=0.40,
        p_market=entry_price,
        p_posterior=0.40,
        entry_price=entry_price,
        p_value=0.01,
        vwmp=entry_price,
        forward_edge=forward_edge,
        support_index=support,
    )


def _trade_decision(
    spec,
    *,
    size_usd: float,
    forward_edge: float,
    entry_price: float = 0.45,
    decision_id: str | None = None,
) -> EdgeDecision:
    """A should_trade=True EdgeDecision as the evaluator emits per selected bin."""
    label = spec[2]
    return EdgeDecision(
        should_trade=True,
        edge=_bin_edge(spec, entry_price=entry_price, forward_edge=forward_edge),
        tokens={"yes_token_id": f"tok-{label}", "executable_snapshot_min_order_size": 1.0},
        size_usd=size_usd,
        decision_id=decision_id or f"dec-{label}",
        decision_snapshot_id="snap-1",
        strategy_key="shoulder_buy",
        selected_method="entry_forecast",
    )


def _family_after_fdr() -> list[EdgeDecision]:
    """The 3 bins family-wise FDR selects (20-21, 22-23, 26+) all as trades.

    Differentiated executable sizing so single-best selection is unambiguous:
    22-23°F has the largest size_usd → it is the bin the gate must keep.
    """
    bins = {s[2]: s for s in _BIN_SPECS}
    return [
        _trade_decision(bins["20-21°F"], size_usd=12.0, forward_edge=0.05),
        _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07),  # best
        _trade_decision(bins["26°F or above"], size_usd=8.0, forward_edge=0.04),
    ]


def _count_trades(decisions: list[EdgeDecision]) -> int:
    return sum(1 for d in decisions if d.should_trade)


def test_legacy_baseline_emits_three_independent_orders() -> None:
    """RED baseline: gate OFF == current production == 3 independent orders.

    Pins the over-allocation bug so the GREEN test's delta (3 → 1) is provably
    the gate's effect, not a fixture artifact.
    """
    decisions = _family_after_fdr()
    out = dedup_mutually_exclusive_families(
        decisions,
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=False,  # legacy / gate-disabled behavior
    )
    assert _count_trades(out) == 3, (
        "legacy/gate-disabled path must preserve all 3 FDR-selected bins "
        "(this is the bug the STAGE A gate fixes)"
    )
    assert all(
        MUTUALLY_EXCLUSIVE_FAMILY_DEDUP not in (d.rejection_reasons or [])
        for d in out
    ), "gate-disabled path must not stamp any dedup rejection reason"


def test_same_city_date_metric_mutually_exclusive_bins_do_not_emit_independent_live_orders(
    caplog,
) -> None:
    """REQUIRED live-mode behavior: exactly 1 entry per exclusive family.

    With the gate ON, the 3 FDR-selected mutually-exclusive bins collapse to
    exactly 1 should_trade=True (the single best by executable size_usd) and
    the other 2 carry the auditable MUTUALLY_EXCLUSIVE_FAMILY_DEDUP reason.

    Run with the gate DISABLED this fails (3 != 1) — that is the RED proof.
    """
    import logging

    decisions = _family_after_fdr()
    with caplog.at_level(logging.INFO, logger="src.strategy.family_exclusive_dedup"):
        out = dedup_mutually_exclusive_families(
            decisions,
            city=CITY,
            target_date=TARGET_DATE,
            temperature_metric=METRIC,
            enabled=True,
        )

    # Exactly one entry survives.
    trades = [d for d in out if d.should_trade]
    assert len(trades) == 1, (
        f"mutually-exclusive family must emit exactly 1 live entry, got {len(trades)}"
    )

    # The survivor is the single best by executable size_usd (22-23°F, $20).
    kept = trades[0]
    assert kept.edge is not None and kept.edge.bin.label == "22-23°F", (
        f"single_best must be the highest-size_usd bin; kept {kept.edge.bin.label!r}"
    )

    # The 2 dropped bins carry the auditable dedup reason + detail.
    dropped = [d for d in out if not d.should_trade]
    assert len(dropped) == 2, f"expected 2 dropped bins, got {len(dropped)}"
    for d in dropped:
        assert MUTUALLY_EXCLUSIVE_FAMILY_DEDUP in (d.rejection_reasons or []), (
            f"dropped bin {d.edge.bin.label if d.edge else '?'} must list the "
            "dedup reason string in rejection_reasons"
        )
        assert d.rejection_stage == FAMILY_REJECTION_STAGE == RejectionStage.ANTI_CHURN.value, (
            "dropped bin must carry a legal anti-churn rejection_stage"
        )
        assert d.rejection_reason_detail and "kept_bin='22-23°F'" in d.rejection_reason_detail, (
            "rejection detail must name the kept bin for auditability"
        )
        # STAGE A is pure runtime gating — must NOT touch the enum / DB CHECK.
        assert d.rejection_reason_enum is None, (
            "STAGE A must not set rejection_reason_enum (no schema change)"
        )

    # Structured audit log emitted per dropped bin.
    dedup_logs = [r for r in caplog.records if "MUTUALLY_EXCLUSIVE_FAMILY_DEDUP" in r.getMessage()]
    assert len(dedup_logs) == 2, "expected one structured audit log per dropped bin"

    # Fail-safe: the gate only ever REMOVES entries (never adds/re-enables).
    assert _count_trades(out) <= 3


def test_single_bin_family_unchanged_no_regression() -> None:
    """Single-entry family: byte-identical to legacy per-edge path (no regression)."""
    bins = {s[2]: s for s in _BIN_SPECS}
    decisions = [_trade_decision(bins["20-21°F"], size_usd=12.0, forward_edge=0.05)]
    out = dedup_mutually_exclusive_families(
        decisions,
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )
    assert _count_trades(out) == 1, "single-bin family must keep its one entry"
    assert out[0].should_trade is True
    assert MUTUALLY_EXCLUSIVE_FAMILY_DEDUP not in (out[0].rejection_reasons or []), (
        "single-bin family must not be touched by the gate"
    )
    assert out[0].rejection_reason_enum is None


def test_non_trade_decisions_in_family_are_ignored() -> None:
    """Already-rejected bins do not count toward the family entry budget.

    A family with 1 should_trade=True and 2 pre-rejected bins must keep the
    single trade (it is already the only entry) — the gate must not 'dedup' a
    family that already emits one order.
    """
    bins = {s[2]: s for s in _BIN_SPECS}
    keep = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    rejected_a = EdgeDecision(
        should_trade=False,
        edge=_bin_edge(bins["20-21°F"], entry_price=0.45, forward_edge=0.0),
        decision_snapshot_id="snap-1",
        rejection_stage="SIZING_TOO_SMALL",
        rejection_reason_enum=NoTradeReason.SIZE_BELOW_MINIMUM,
    )
    rejected_b = EdgeDecision(
        should_trade=False,
        edge=_bin_edge(bins["26°F or above"], entry_price=0.45, forward_edge=0.0),
        decision_snapshot_id="snap-1",
        rejection_stage="RISK_REJECTED",
        rejection_reason_enum=NoTradeReason.RISK_LIMITS_EXCEEDED,
    )
    out = dedup_mutually_exclusive_families(
        [keep, rejected_a, rejected_b],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )
    assert _count_trades(out) == 1
    # Pre-existing rejection reasons untouched (not overwritten with dedup).
    assert rejected_a.rejection_reason_enum is NoTradeReason.SIZE_BELOW_MINIMUM
    assert rejected_b.rejection_reason_enum is NoTradeReason.RISK_LIMITS_EXCEEDED


def test_existing_family_exposure_blocks_new_different_bin_across_cycles() -> None:
    """Cross-cycle relationship: existing exposure owns the family entry budget.

    If a prior cycle already opened one bin for the same city/date/metric
    family, a later FDR-selected hypothesis for a different bin is not an
    executable portfolio. It must be rejected before executor submission.
    """
    bins = {s[2]: s for s in _BIN_SPECS}
    new_bin = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase="active",
        position_id="pos-existing-1",
    )

    out = dedup_mutually_exclusive_families(
        [new_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        existing_exposures=[exposure],
        enabled=True,
    )

    assert _count_trades(out) == 0
    rejected = out[0]
    assert rejected.rejection_stage == RejectionStage.ANTI_CHURN.value
    assert MUTUALLY_EXCLUSIVE_FAMILY_DEDUP in rejected.rejection_reasons
    assert rejected.rejection_reason_enum is None
    assert rejected.rejection_reason_detail is not None
    assert "existing_exposure_bin='20-21°F'" in rejected.rejection_reason_detail
    assert "no family portfolio intent" in rejected.rejection_reason_detail


def test_portfolio_positions_project_to_weather_family_exposure_read_model() -> None:
    portfolio = SimpleNamespace(
        positions=[
            SimpleNamespace(
                trade_id="pos-open",
                city=CITY,
                target_date=TARGET_DATE,
                temperature_metric=METRIC,
                bin_label="20-21°F",
                state="holding",
            ),
            SimpleNamespace(
                trade_id="pos-closed",
                city=CITY,
                target_date=TARGET_DATE,
                temperature_metric=METRIC,
                bin_label="22-23°F",
                state="settled",
            ),
        ]
    )

    exposures = weather_family_exposures_from_portfolio(portfolio)

    assert exposures == [
        WeatherFamilyExposure(
            key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
            bin_label="20-21°F",
            phase="holding",
            position_id="pos-open",
        )
    ]


def test_cycle_runtime_threads_portfolio_exposure_into_family_gate() -> None:
    """Relationship guard: runtime callsite must supply current exposure state."""
    source = Path("src/engine/cycle_runtime.py").read_text()
    assert "weather_family_exposures_from_portfolio" in source
    assert "existing_exposures=weather_family_exposures_from_portfolio(portfolio)" in source


def test_family_portfolio_intent_allows_optimizer_owned_multi_bin_execution() -> None:
    """Stage A only blocks independent bins, not an explicit family portfolio."""
    bins = {s[2]: s for s in _BIN_SPECS}
    new_bin = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase="active",
        position_id="pos-existing-1",
    )

    out = dedup_mutually_exclusive_families(
        [new_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        existing_exposures=[exposure],
        family_portfolio_intent=True,
        enabled=True,
    )

    assert _count_trades(out) == 1
    assert out[0].should_trade is True
    assert out[0].rejection_stage == ""
    assert out[0].rejection_reasons == []


def test_family_preselection_happens_before_projected_exposure_mutation() -> None:
    """Pre-Kelly relationship: only one FDR sibling enters scalar sizing."""

    bins = {s[2]: s for s in _BIN_SPECS}
    low_price_tail = _bin_edge(bins["26°F or above"], entry_price=0.02, forward_edge=0.02)
    mid_bin = _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.07)
    side_bin = _bin_edge(bins["20-21°F"], entry_price=0.18, forward_edge=0.04)

    selected, dropped = preselect_single_family_edge_before_kelly(
        [low_price_tail, mid_bin, side_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    assert selected == [mid_bin]
    assert {d.dropped_bin for d in dropped} == {"26°F or above", "20-21°F"}
    assert all(d.kept_bin == "22-23°F" for d in dropped)


def test_family_preselection_is_disabled_without_stage_a_gate() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edges = [
        _bin_edge(bins["20-21°F"], entry_price=0.18, forward_edge=0.04),
        _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.07),
    ]

    selected, dropped = preselect_single_family_edge_before_kelly(
        edges,
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=False,
    )

    assert selected is edges
    assert dropped == []


def test_one_cent_order_rejected_without_tail_strategy_even_if_venue_min_passes() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["26°F or above"], entry_price=0.01, forward_edge=0.04)

    assert _strategy_entry_price_floor_block_reason("opening_inertia", edge) == (
        "STRATEGY_ENTRY_PRICE_BELOW_LIVE_FLOOR(0.0100<=0.05; strategy=opening_inertia)"
    )


def test_venue_min_order_does_not_override_strategy_economic_floor() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["22-23°F"], entry_price=0.20, forward_edge=0.08)
    expected_profit = _expected_profit_usd_for_edge(
        edge,
        notional_usd=0.06,
        price=0.20,
    )

    reason = _live_entry_economic_floor_rejection(
        strategy_key="opening_inertia",
        edge=edge,
        submitted_notional_usd=0.06,  # e.g. venue min shares * price passed.
        expected_profit_usd=expected_profit,
        final_limit_price=0.20,
        passive_order=False,
    )

    assert reason is not None
    assert reason.startswith("STRATEGY_NOTIONAL_BELOW_LIVE_FLOOR")


def test_expected_profit_floor_blocks_tiny_positive_edge_order() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["22-23°F"], entry_price=0.20, forward_edge=0.001)
    expected_profit = _expected_profit_usd_for_edge(
        edge,
        notional_usd=1.00,
        price=0.20,
    )

    reason = _live_entry_economic_floor_rejection(
        strategy_key="center_buy",
        edge=edge,
        submitted_notional_usd=1.00,
        expected_profit_usd=expected_profit,
        final_limit_price=0.20,
        passive_order=False,
    )

    assert reason is not None
    assert reason.startswith("EXPECTED_PROFIT_BELOW_LIVE_FLOOR")


def test_final_one_cent_passive_order_requires_tail_authority_and_fill_model() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["26°F or above"], entry_price=0.20, forward_edge=0.08)

    reason = _live_entry_economic_floor_rejection(
        strategy_key="imminent_open_capture",
        edge=edge,
        submitted_notional_usd=2.00,
        expected_profit_usd=0.20,
        final_limit_price=0.01,
        passive_order=True,
        passive_fill_probability=None,
    )

    assert reason is not None
    assert reason.startswith("ULTRA_LOW_PRICE_NOT_AUTHORIZED")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
