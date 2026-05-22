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

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.engine.evaluator as evaluator_module
from src.contracts.no_trade_reason import NoTradeReason  # used in fixtures
from src.engine.evaluator import EdgeDecision
from src.strategy.family_exclusive_dedup import (
    FAMILY_REJECTION_STAGE,
    MUTUALLY_EXCLUSIVE_FAMILY_DEDUP,
    NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG,
    NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG,
    WeatherFamilyExposureReducer,
    WeatherFamilyExposure,
    WeatherFamilyKey,
    build_weather_family_decision,
    dedup_mutually_exclusive_families,
    optimize_exclusive_outcome_portfolio,
    preselect_single_family_edge_before_kelly,
    resolve_weather_family_exposures,
    weather_family_exposures_from_trade_db,
    weather_family_exposures_from_portfolio,
)
from src.engine.evaluator import (
    _expected_profit_usd_for_edge,
    _live_entry_economic_floor_rejection,
    _projects_exposure_during_family_fallback_sizing,
    _source_quality_kelly_haircut,
    _source_quality_policy_rejection,
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

    Differentiated executable economics so single-best selection is unambiguous:
    22-23°F has the largest expected-net-profit proxy.
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
    exactly 1 should_trade=True (the single family utility winner) and
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

    # The survivor is the single best by family expected-net-profit proxy.
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
        assert d.rejection_reason_enum is NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP

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


@pytest.mark.parametrize("blocking_phase", ["open", "pending", "active"])
def test_open_pending_active_family_exposure_blocks_fdr_selected_hypothesis_without_optimizer_intent(
    blocking_phase: str,
) -> None:
    """Cross-cycle relationship: existing exposure owns the family entry budget.

    If a prior cycle already opened one bin for the same city/date/metric
    family, a later FDR-selected hypothesis for a different bin is still only
    a statistical selection, not an executable family portfolio selection. It
    must be rejected before executor submission unless a FamilyPortfolioOptimizer
    portfolio intent is present.
    """
    bins = {s[2]: s for s in _BIN_SPECS}
    new_bin = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    new_bin.fdr_family_size = len(_BIN_SPECS)
    new_bin.n_edges_after_fdr = 1
    exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase=blocking_phase,
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
    assert rejected.rejection_reason_enum is NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP
    assert rejected.rejection_reason_detail is not None
    assert "existing_exposure_bin='20-21°F'" in rejected.rejection_reason_detail
    assert "no family portfolio intent" in rejected.rejection_reason_detail
    assert "FDR" not in rejected.rejection_stage


def test_known_different_market_family_exposure_does_not_block_replacement_market() -> None:
    """Known venue family identity narrows B2 exposure blocking.

    City/date/metric remains the conservative fallback, but when both old and
    new sides carry explicit market-family ids, only the same event family can
    block replacement/reopened weather markets.
    """
    bins = {s[2]: s for s in _BIN_SPECS}
    new_bin = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    new_bin.fdr_family_size = len(_BIN_SPECS)
    new_bin.n_edges_after_fdr = 1
    exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC, "old-event-family"),
        bin_label="20-21°F",
        phase="active",
        position_id="pos-old-family",
    )

    out = dedup_mutually_exclusive_families(
        [new_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        market_family_id="new-event-family",
        existing_exposures=[exposure],
        enabled=True,
    )

    assert _count_trades(out) == 1
    assert out[0].should_trade is True


def test_unknown_market_family_exposure_blocks_conservatively() -> None:
    """Historical exposure without family id still blocks same city/date/metric."""
    bins = {s[2]: s for s in _BIN_SPECS}
    new_bin = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    new_bin.fdr_family_size = len(_BIN_SPECS)
    new_bin.n_edges_after_fdr = 1
    exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase="active",
        position_id="pos-legacy-family",
    )

    out = dedup_mutually_exclusive_families(
        [new_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        market_family_id="new-event-family",
        existing_exposures=[exposure],
        enabled=True,
    )

    assert _count_trades(out) == 0
    assert out[0].rejection_reason_enum is NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP
    assert "existing_exposure_bin='20-21°F'" in str(out[0].rejection_reason_detail)


@pytest.mark.parametrize("blocking_phase", ["open", "pending", "active"])
def test_family_portfolio_optimizer_intent_is_the_only_existing_exposure_bypass(
    blocking_phase: str,
) -> None:
    """FamilyPortfolioOptimizer intent, not FDR selection, can own multi-bin execution."""
    bins = {s[2]: s for s in _BIN_SPECS}
    new_bin = _trade_decision(bins["22-23°F"], size_usd=20.0, forward_edge=0.07)
    new_bin.fdr_family_size = len(_BIN_SPECS)
    new_bin.n_edges_after_fdr = 1
    exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase=blocking_phase,
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


def test_weather_family_exposure_resolver_is_canonical_entrypoint() -> None:
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
        ]
    )

    wrapper_exposures = weather_family_exposures_from_portfolio(portfolio)
    reducer_exposures = WeatherFamilyExposureReducer.from_portfolio(portfolio)
    resolved_exposures = resolve_weather_family_exposures(portfolio=portfolio)

    assert wrapper_exposures == reducer_exposures == resolved_exposures


def test_weather_family_exposure_resolver_merges_trade_truth_and_portfolio_projection() -> None:
    trade_exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase="ACKED",
        position_id="cmd-1",
    )
    portfolio_exposure = WeatherFamilyExposure(
        key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
        bin_label="20-21°F",
        phase="holding",
        position_id="pos-1",
    )

    exposures = WeatherFamilyExposureReducer.merge([trade_exposure], [portfolio_exposure])

    assert exposures == [trade_exposure, portfolio_exposure]


def test_cycle_runtime_threads_portfolio_exposure_into_family_gate() -> None:
    """Relationship guard: runtime callsite must supply current exposure state."""
    source = Path("src/engine/cycle_runtime.py").read_text()
    assert "resolve_weather_family_exposures" in source
    assert "existing_exposures=_family_exposures" in source


def test_trade_db_family_exposures_include_live_entry_commands(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "family-exposure.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT NOT NULL,
            intent_kind TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE position_current (
            position_id TEXT PRIMARY KEY,
            city TEXT,
            target_date TEXT,
            temperature_metric TEXT,
            bin_label TEXT,
            phase TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?)",
        ("pos-1", CITY, TARGET_DATE, METRIC, "20-21°F", "pending_entry"),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, ?, ?)",
        ("cmd-1", "pos-1", "ENTRY", "ACKED"),
    )

    exposures = weather_family_exposures_from_trade_db(conn)

    assert exposures == [
        WeatherFamilyExposure(
            key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC),
            bin_label="20-21°F",
            phase="pending_entry",
            position_id="pos-1",
        )
    ]


def test_trade_db_family_exposure_blocks_command_without_position_projection(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "family-exposure-no-projection.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            envelope_id TEXT,
            snapshot_id TEXT,
            market_id TEXT,
            intent_kind TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT PRIMARY KEY,
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT
        );
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT,
            event_slug TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT
        );
        CREATE TABLE market_events_v2 (
            market_slug TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?, ?, ?, ?)",
        ("env-1", "cond-1", "tok-yes-1", "YES"),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?, ?, ?, ?, ?)",
        ("snap-1", "cond-1", "weather-chicago-high", "tok-yes-1", "YES"),
    )
    conn.execute(
        "INSERT INTO market_events_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("weather-chicago-high", CITY, TARGET_DATE, METRIC, "cond-1", "tok-yes-1", "20-21°F"),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("cmd-1", "", "env-1", "snap-1", "cond-1", "ENTRY", "ACKED"),
    )

    exposures = weather_family_exposures_from_trade_db(conn)

    assert exposures == [
        WeatherFamilyExposure(
            key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC, "weather-chicago-high"),
            bin_label="20-21°F",
            phase="ACKED",
            position_id="cmd-1",
        )
    ]


def test_trade_db_family_exposure_prefers_forecasts_market_events_authority(tmp_path) -> None:
    """K1: market_events_v2 family identity comes from forecasts when attached."""
    import sqlite3

    db_path = tmp_path / "family-exposure-main.db"
    forecasts_path = tmp_path / "forecasts.db"
    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{forecasts_path}' AS forecasts")
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            envelope_id TEXT,
            snapshot_id TEXT,
            market_id TEXT,
            intent_kind TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT PRIMARY KEY,
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT
        );
        CREATE TABLE market_events_v2 (
            market_slug TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT
        );
        CREATE TABLE forecasts.market_events_v2 (
            market_slug TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?, ?, ?, ?)",
        ("env-1", "cond-1", "tok-yes-1", "YES"),
    )
    conn.execute(
        "INSERT INTO market_events_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("stale-main-copy", "Wrong City", "2099-01-01", "low", "cond-1", "tok-yes-1", "bad"),
    )
    conn.execute(
        "INSERT INTO forecasts.market_events_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("weather-chicago-high", CITY, TARGET_DATE, METRIC, "cond-1", "tok-yes-1", "20-21°F"),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("cmd-1", "", "env-1", "", "cond-1", "ENTRY", "ACKED"),
    )

    exposures = weather_family_exposures_from_trade_db(conn)

    assert exposures == [
        WeatherFamilyExposure(
            key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC, "weather-chicago-high"),
            bin_label="20-21°F",
            phase="ACKED",
            position_id="cmd-1",
        )
    ]


def test_trade_db_family_exposure_does_not_let_stale_envelope_mask_snapshot_identity(tmp_path) -> None:
    """Relationship: command-only exposure must try every durable identity surface."""
    import sqlite3

    db_path = tmp_path / "family-exposure-stale-envelope.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY,
            position_id TEXT,
            envelope_id TEXT,
            snapshot_id TEXT,
            market_id TEXT,
            token_id TEXT,
            intent_kind TEXT NOT NULL,
            state TEXT NOT NULL
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT PRIMARY KEY,
            condition_id TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT
        );
        CREATE TABLE executable_market_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            condition_id TEXT,
            event_slug TEXT,
            selected_outcome_token_id TEXT,
            outcome_label TEXT
        );
        CREATE TABLE market_events_v2 (
            market_slug TEXT,
            city TEXT NOT NULL,
            target_date TEXT NOT NULL,
            temperature_metric TEXT NOT NULL,
            condition_id TEXT,
            token_id TEXT,
            range_label TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?, ?, ?, ?)",
        ("env-1", "stale-cond", "stale-token", "YES"),
    )
    conn.execute(
        "INSERT INTO executable_market_snapshots VALUES (?, ?, ?, ?, ?)",
        ("snap-1", "cond-1", "weather-chicago-high", "tok-yes-1", "YES"),
    )
    conn.execute(
        "INSERT INTO market_events_v2 VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("weather-chicago-high", CITY, TARGET_DATE, METRIC, "cond-1", "tok-yes-1", "20-21°F"),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("cmd-1", "", "env-1", "snap-1", "unused-market-id", "unused-token", "ENTRY", "ACKED"),
    )

    exposures = weather_family_exposures_from_trade_db(conn)

    assert exposures == [
        WeatherFamilyExposure(
            key=WeatherFamilyKey(CITY, TARGET_DATE, METRIC, "weather-chicago-high"),
            bin_label="20-21°F",
            phase="ACKED",
            position_id="cmd-1",
        )
    ]


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


def test_weather_family_decision_is_first_class_single_leg_intent() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    low_price_tail = _bin_edge(bins["26°F or above"], entry_price=0.02, forward_edge=0.02)
    mid_bin = _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.07)

    family_decision = build_weather_family_decision(
        [low_price_tail, mid_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    assert family_decision is not None
    assert family_decision.family_portfolio_intent is True
    assert family_decision.portfolio.family_key == WeatherFamilyKey(CITY, TARGET_DATE, METRIC)
    assert family_decision.portfolio.selected_leg is mid_bin
    assert family_decision.portfolio.selected_legs == (mid_bin,)
    assert family_decision.portfolio.fallback_candidate_legs == (mid_bin, low_price_tail)
    assert family_decision.portfolio.objective.startswith("expected_log_growth_payoff_vector")
    assert family_decision.portfolio.payoff_matrix
    assert family_decision.dropped == ()


def test_family_decision_retains_ranked_fallback_candidates_before_execution_viability() -> None:
    """Primary leg failure must not erase sibling legs before executable reprice."""

    bins = {s[2]: s for s in _BIN_SPECS}
    low_price_tail = _bin_edge(bins["26°F or above"], entry_price=0.02, forward_edge=0.02)
    mid_bin = _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.07)
    side_bin = _bin_edge(bins["20-21°F"], entry_price=0.18, forward_edge=0.04)
    weak_bin = _bin_edge(bins["19°F or below"], entry_price=0.10, forward_edge=0.01)

    family_decision = build_weather_family_decision(
        [low_price_tail, mid_bin, side_bin, weak_bin],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    assert family_decision is not None
    assert family_decision.portfolio.selected_leg is mid_bin
    assert family_decision.portfolio.fallback_candidate_legs == (
        mid_bin,
        side_bin,
        low_price_tail,
    )
    assert [d.dropped_bin for d in family_decision.dropped] == ["19°F or below"]


def test_family_decision_excludes_live_disabled_buy_no_from_fallback_slots(monkeypatch) -> None:
    """Live-disabled buy_no is a structural non-executable leg, not fallback capacity."""

    flags = dict(evaluator_module.settings["feature_flags"])
    flags[NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG] = True
    flags[NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG] = False
    monkeypatch.setitem(evaluator_module.settings._data, "feature_flags", flags)

    bins = {s[2]: s for s in _BIN_SPECS}
    live_disabled_buy_no = replace(
        _bin_edge(bins["26°F or above"], entry_price=0.12, forward_edge=0.50),
        direction="buy_no",
    )
    best_buy_yes = _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.07)
    fallback_buy_yes = _bin_edge(bins["20-21°F"], entry_price=0.18, forward_edge=0.04)

    family_decision = build_weather_family_decision(
        [live_disabled_buy_no, best_buy_yes, fallback_buy_yes],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    assert family_decision is not None
    assert family_decision.portfolio.selected_leg is best_buy_yes
    assert family_decision.portfolio.fallback_candidate_legs == (
        best_buy_yes,
        fallback_buy_yes,
    )
    assert [d.dropped_bin for d in family_decision.dropped] == ["26°F or above"]
    assert family_decision.dropped[0].rejection_reason == "NATIVE_MULTIBIN_BUY_NO_LIVE_DISABLED"


def test_family_decision_all_live_disabled_buy_no_does_not_self_drop(monkeypatch) -> None:
    """If no executable sibling exists, blocked diagnostics must not mark the selected leg dropped."""

    flags = dict(evaluator_module.settings["feature_flags"])
    flags[NATIVE_MULTIBIN_BUY_NO_SHADOW_FLAG] = True
    flags[NATIVE_MULTIBIN_BUY_NO_LIVE_FLAG] = False
    monkeypatch.setitem(evaluator_module.settings._data, "feature_flags", flags)

    bins = {s[2]: s for s in _BIN_SPECS}
    buy_no_a = replace(
        _bin_edge(bins["26°F or above"], entry_price=0.12, forward_edge=0.50),
        direction="buy_no",
    )
    buy_no_b = replace(
        _bin_edge(bins["20-21°F"], entry_price=0.18, forward_edge=0.04),
        direction="buy_no",
    )

    family_decision = build_weather_family_decision(
        [buy_no_a, buy_no_b],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    assert family_decision is not None
    assert family_decision.portfolio.selected_leg is buy_no_a
    assert family_decision.portfolio.fallback_candidate_legs == (buy_no_a, buy_no_b)
    assert family_decision.dropped == ()


def test_runtime_dedup_preserves_ranked_fallback_candidates_until_submit() -> None:
    """Fallback candidates stay attemptable; runtime submits at most one later."""

    decisions = _family_after_fdr()
    for rank, decision in enumerate(decisions, start=1):
        decision.family_fallback_rank = rank
        decision.family_fallback_candidate_count = len(decisions)

    out = dedup_mutually_exclusive_families(
        decisions,
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    assert _count_trades(out) == 3
    assert all(d.rejection_stage == "" for d in out)
    assert all("family_ranked_executable_fallback" in d.applied_validations for d in out)


def test_family_fallback_sizing_does_not_accumulate_sibling_exposure() -> None:
    """Risk sizing sees ranked fallback siblings as alternate attempts, not a basket."""

    assert not _projects_exposure_during_family_fallback_sizing(
        family_fallback_rank=1,
        family_fallback_candidate_count=3,
    )
    assert not _projects_exposure_during_family_fallback_sizing(
        family_fallback_rank=2,
        family_fallback_candidate_count=3,
    )
    assert _projects_exposure_during_family_fallback_sizing(
        family_fallback_rank=0,
        family_fallback_candidate_count=0,
    )
    assert _projects_exposure_during_family_fallback_sizing(
        family_fallback_rank=1,
        family_fallback_candidate_count=1,
    )


def test_family_portfolio_can_select_explicit_multi_leg_payoff_vector() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge_a = _bin_edge(bins["20-21°F"], entry_price=0.20, forward_edge=0.05)
    edge_b = _bin_edge(bins["22-23°F"], entry_price=0.24, forward_edge=0.04)
    edge_c = _bin_edge(bins["26°F or above"], entry_price=0.70, forward_edge=0.01)

    portfolio = optimize_exclusive_outcome_portfolio(
        [edge_a, edge_b, edge_c],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        min_legs=2,
        max_legs=2,
    )

    assert portfolio is not None
    assert portfolio.family_key == WeatherFamilyKey(CITY, TARGET_DATE, METRIC)
    assert len(portfolio.selected_legs) == 2
    assert portfolio.selected_legs == (edge_a, edge_b)
    assert len(portfolio.payoff_matrix) == 3
    assert all(len(row) == 2 for row in portfolio.payoff_matrix)
    assert len(portfolio.posterior_vector) == 3
    assert len(portfolio.leg_weights) == 2
    assert portfolio.expected_log_growth > 0


def test_runtime_family_dedup_ranks_by_expected_net_profit_not_size_usd() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    big_low_utility = _trade_decision(
        bins["20-21°F"],
        size_usd=50.0,
        forward_edge=0.001,
        decision_id="a-big-low-utility",
    )
    smaller_high_utility = _trade_decision(
        bins["22-23°F"],
        size_usd=10.0,
        forward_edge=0.08,
        decision_id="b-smaller-high-utility",
    )

    out = dedup_mutually_exclusive_families(
        [big_low_utility, smaller_high_utility],
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=True,
    )

    trades = [d for d in out if d.should_trade]
    assert trades == [smaller_high_utility]
    assert big_low_utility.rejection_reason_enum is NoTradeReason.MUTUALLY_EXCLUSIVE_FAMILY_DEDUP


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


def test_weather_family_decision_is_disabled_with_stage_a_gate() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edges = [
        _bin_edge(bins["20-21°F"], entry_price=0.18, forward_edge=0.04),
        _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.07),
    ]

    family_decision = build_weather_family_decision(
        edges,
        city=CITY,
        target_date=TARGET_DATE,
        temperature_metric=METRIC,
        enabled=False,
    )

    assert family_decision is None


def test_one_cent_opening_inertia_order_uses_profit_floor_not_price_floor() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["26°F or above"], entry_price=0.01, forward_edge=0.04)

    assert _strategy_entry_price_floor_block_reason("opening_inertia", edge) is None

    expected_profit = _expected_profit_usd_for_edge(
        edge,
        notional_usd=1.00,
        price=0.01,
    )
    assert expected_profit > 0.05
    assert _live_entry_economic_floor_rejection(
        strategy_key="opening_inertia",
        edge=edge,
        submitted_notional_usd=1.00,
        expected_profit_usd=expected_profit,
        final_limit_price=0.01,
        passive_order=False,
    ) is None


def test_one_cent_center_buy_order_still_rejected_even_if_venue_min_passes() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["22-23°F"], entry_price=0.01, forward_edge=0.04)

    assert _strategy_entry_price_floor_block_reason("center_buy", edge) == (
        "CENTER_BUY_ULTRA_LOW_PRICE(0.0100<=0.02; "
        "strategy=center_buy; direction=buy_yes; tail_topology=false; "
        "specialized=CENTER_BUY_ULTRA_LOW_PRICE(0.0100<=0.02))"
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
    assert reason.startswith("PASSIVE_FILL_PROBABILITY_UNMODELED")


def test_ultra_low_tail_authority_does_not_bypass_passive_fill_model(monkeypatch) -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["26°F or above"], entry_price=0.20, forward_edge=0.08)
    monkeypatch.setattr(
        evaluator_module,
        "_try_get_strategy_profile",
        lambda _strategy_key: SimpleNamespace(
            min_entry_price=0.05,
            min_strategy_notional_usd=1.00,
            min_expected_profit_usd=0.05,
            allow_ultra_low_tail=True,
        ),
    )

    reason = _live_entry_economic_floor_rejection(
        strategy_key="tail_arbitrage",
        edge=edge,
        submitted_notional_usd=2.00,
        expected_profit_usd=0.20,
        final_limit_price=0.01,
        passive_order=True,
        passive_fill_probability=None,
    )

    assert reason is not None
    assert reason.startswith("PASSIVE_FILL_PROBABILITY_UNMODELED")


def test_ultra_low_non_passive_order_requires_tail_authority_for_non_opening_strategy() -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["26°F or above"], entry_price=0.20, forward_edge=0.08)

    reason = _live_entry_economic_floor_rejection(
        strategy_key="center_buy",
        edge=edge,
        submitted_notional_usd=2.00,
        expected_profit_usd=0.20,
        final_limit_price=0.01,
        passive_order=False,
        passive_fill_probability=None,
    )

    assert reason is not None
    assert reason.startswith("ULTRA_LOW_PRICE_NOT_AUTHORIZED")


def test_partial_source_tail_order_requires_complete_source(monkeypatch) -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["26°F or above"], entry_price=0.01, forward_edge=0.08)
    monkeypatch.setattr(
        evaluator_module,
        "_try_get_strategy_profile",
        lambda _strategy_key: SimpleNamespace(
            min_entry_price=0.05,
            min_strategy_notional_usd=1.00,
            min_expected_profit_usd=0.05,
            allow_ultra_low_tail=True,
            partial_source_run_allowed=True,
            complete_required_for_tail_orders=True,
            partial_run_kelly_haircut=0.5,
        ),
    )
    ens_result = {
        "source_run_status": "PARTIAL",
        "source_run_completeness_status": "PARTIAL",
        "coverage_completeness_status": "PARTIAL",
        "expected_members": 51,
        "observed_members": 49,
    }

    reason = _source_quality_policy_rejection(
        strategy_key="tail_arbitrage",
        edge=edge,
        ens_result=ens_result,
    )

    assert reason is not None
    assert reason.startswith("PARTIAL_SOURCE_RUN_FOR_TAIL_ORDER")


def test_partial_source_allowed_mid_bin_gets_kelly_haircut(monkeypatch) -> None:
    bins = {s[2]: s for s in _BIN_SPECS}
    edge = _bin_edge(bins["22-23°F"], entry_price=0.45, forward_edge=0.08)
    monkeypatch.setattr(
        evaluator_module,
        "_try_get_strategy_profile",
        lambda _strategy_key: SimpleNamespace(
            min_entry_price=0.05,
            min_strategy_notional_usd=1.00,
            min_expected_profit_usd=0.05,
            allow_ultra_low_tail=False,
            partial_source_run_allowed=True,
            complete_required_for_tail_orders=True,
            partial_run_kelly_haircut=0.4,
        ),
    )
    ens_result = {
        "source_run_status": "SUCCESS",
        "source_run_completeness_status": "PARTIAL",
        "coverage_completeness_status": "PARTIAL",
        "expected_members": 51,
        "observed_members": 49,
    }

    assert _source_quality_policy_rejection(
        strategy_key="opening_inertia",
        edge=edge,
        ens_result=ens_result,
    ) is None
    assert _source_quality_kelly_haircut("opening_inertia", ens_result) == pytest.approx(0.4)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
