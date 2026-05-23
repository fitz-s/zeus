# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §10
#                  + docs/reference/zeus_strategy_spec.md §14
"""Relationship tests for Bayes-factor alert arbitrage reframe (§10).

Tests are ordered: relationship invariants → math correctness → candidate gates.

Relationship invariants (cross-module):
  R1. bayes_update(p, LR=1) == p  — alert with no diagnosticity leaves probability unchanged.
  R2. posterior_lower_bound(p, lr) <= bayes_update(p, lr.point) — lower bound ≤ point.
  R3. LR table absent (stub) → WeatherEventArbitrage emits no_trade
      with reason=WEATHER_ALERT_LR_TABLE_MISSING.
  R4. No hardcoded edge constant in weather_event_arbitrage module.

Math correctness:
  M1. bayes_update closed-form correctness (1e-12 tolerance).
  M2. bayes_update(p, LR) monotone in LR — higher LR → higher posterior.
  M3. posterior_lower_bound uses effective_lower, not point.

Candidate gates:
  G1. No metrics → WEATHER_ALERT_SOURCE_UNTRUSTED.
  G2. No alert_source → WEATHER_ALERT_SOURCE_UNTRUSTED.
  G3. Untrusted source → WEATHER_ALERT_SOURCE_UNTRUSTED.
  G4. No active alert → WEATHER_ALERT_SOURCE_UNTRUSTED.
  G5. No prior_p → WEATHER_ALERT_SOURCE_UNTRUSTED.
  G6. LR table None → WEATHER_ALERT_LR_TABLE_MISSING.
  G7. Edge gate: p'⁻ − ask − φ ≤ 0 → WEATHER_ALERT_EDGE_NONPOSITIVE.
  G8. Enter path: outcome == "enter" with real LR + prior_p + favorable ask.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.strategy.bayes_alert import (
    AlertLRStub,
    LRRecord,
    bayes_update,
    posterior_lower_bound,
)
from src.strategy.candidates import (
    CandidateContext,
    WeatherEventArbitrage,
)


# ---------------------------------------------------------------------------
# Shared test infrastructure (mirrors test_phase4_t2_candidates pattern)
# ---------------------------------------------------------------------------

_DECISION_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS decision_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    condition_id        TEXT,
    decision_event_id   TEXT,
    decision_time       TEXT NOT NULL,
    outcome             TEXT NOT NULL,
    side                TEXT NOT NULL,
    strategy_key        TEXT NOT NULL,
    cycle_id            TEXT,
    cycle_iteration     INTEGER,
    p_posterior         REAL,
    edge                REAL,
    target_size_usd     REAL,
    target_price        REAL,
    forecast_time              TEXT,
    provider_reported_time     TEXT,
    observation_available_at   TEXT NOT NULL DEFAULT '',
    polymarket_end_anchor_source TEXT NOT NULL DEFAULT 'unknown_legacy',
    first_member_observed_time TEXT,
    run_complete_time          TEXT,
    zeus_submit_intent_time    TEXT,
    venue_ack_time             TEXT,
    first_inclusion_block_time TEXT,
    finality_confirmed_time    TEXT,
    clock_skew_estimate_ms_at_submit INTEGER,
    raw_orderbook_hash_transition_delta_ms INTEGER,
    schema_version INTEGER NOT NULL,
    source         TEXT NOT NULL,
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""

_NO_TRADE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS no_trade_events (
    market_slug         TEXT NOT NULL,
    temperature_metric  TEXT NOT NULL,
    target_date         TEXT NOT NULL,
    observation_time    TEXT NOT NULL,
    decision_seq        INTEGER NOT NULL,
    reason              TEXT NOT NULL,
    reason_detail       TEXT,
    observed_at         TEXT NOT NULL,
    schema_version      INTEGER NOT NULL,
    schema_compatibility TEXT NOT NULL DEFAULT 'current',
    PRIMARY KEY (market_slug, temperature_metric, target_date, observation_time, decision_seq)
)
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DECISION_EVENTS_DDL)
    conn.execute(_NO_TRADE_EVENTS_DDL)
    conn.commit()
    return conn


def _make_context(analysis: Any) -> CandidateContext:
    nk = make_decision_natural_key(
        market_slug="test-market-chicago-high-2026-07-15",
        temperature_metric="high",
        target_date="2026-07-15",
        observation_time="2026-07-15T10:00:00+00:00",
        decision_seq=0,
    )
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-07-15T10:00:00+00:00",
        analysis=analysis,
    )


def _make_analysis(**overrides: Any) -> SimpleNamespace:
    """Minimal analysis namespace with data-gated defaults."""
    defaults = dict(
        metrics=None,
        alert_source=None,
        active_weather_alert=None,
        alert_prior_p=None,
        alert_type="",
        alert_city="",
        alert_season="",
        alert_lead_time_hours=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_metrics(**overrides: Any) -> SimpleNamespace:
    """Minimal MicrostructureMetrics-like namespace."""
    defaults = dict(
        depth_at_best_ask=5,
        polymarket_end_anchor_source="gamma_explicit",
        best_ask=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_lr_record(
    *,
    point: float = 2.5,
    lower: Optional[float] = 1.8,
    alert_type: str = "ExtremeHeat",
    city: str = "chicago",
    season: str = "summer",
    lead_time_hours: int = 12,
) -> LRRecord:
    return LRRecord(
        point=point,
        lower=lower,
        alert_type=alert_type,
        city=city,
        season=season,
        lead_time_hours=lead_time_hours,
    )


class _FakeLRTable:
    """Test stub that returns a configurable LRRecord."""

    def __init__(self, record: Optional[LRRecord]) -> None:
        self._record = record

    def lookup(self, *, alert_type: str, city: str, season: str, lead_time_hours: int) -> Optional[LRRecord]:
        return self._record


# ---------------------------------------------------------------------------
# R1: Relationship invariant — LR=1 leaves probability unchanged
# ---------------------------------------------------------------------------

class TestBayesUpdateIdentityLR:
    """R1: bayes_update(p, 1.0) == p for all p in (0,1).

    An alert with no diagnosticity (LR=1) must NOT change the probability.
    This is the foundational relationship invariant: the math module must
    respect probability theory before it can be trusted by the candidate.
    """

    @pytest.mark.parametrize("p", [0.05, 0.2, 0.5, 0.8, 0.95])
    def test_lr_one_is_identity(self, p: float) -> None:
        result = bayes_update(p, 1.0)
        assert abs(result - p) < 1e-12, (
            f"bayes_update({p}, 1.0) = {result!r} but expected {p!r}. "
            "LR=1 must leave probability unchanged (no diagnosticity)."
        )


# ---------------------------------------------------------------------------
# M1: Closed-form correctness
# ---------------------------------------------------------------------------

class TestBayesUpdateMath:
    """M1: bayes_update matches the closed-form O·LR / (1 + O·LR) to 1e-12."""

    @pytest.mark.parametrize("prior_p,lr,expected", [
        # O = 0.3/0.7 = 3/7; LR=2 → O' = 6/7 → p' = 6/13
        (0.3, 2.0, (0.3 / 0.7 * 2.0) / (1 + 0.3 / 0.7 * 2.0)),
        # O = 0.5/0.5 = 1; LR=3 → O' = 3 → p' = 3/4
        (0.5, 3.0, 0.75),
        # O = 0.1/0.9; LR=5 → O' = 5/9 → p' = 5/14
        (0.1, 5.0, (0.1 / 0.9 * 5.0) / (1 + 0.1 / 0.9 * 5.0)),
    ])
    def test_closed_form(self, prior_p: float, lr: float, expected: float) -> None:
        result = bayes_update(prior_p, lr)
        assert abs(result - expected) < 1e-12, (
            f"bayes_update({prior_p}, {lr}) = {result!r}, expected {expected!r}"
        )

    def test_raises_on_invalid_prior(self) -> None:
        with pytest.raises(ValueError):
            bayes_update(0.0, 2.0)
        with pytest.raises(ValueError):
            bayes_update(1.0, 2.0)

    def test_raises_on_nonpositive_lr(self) -> None:
        with pytest.raises(ValueError):
            bayes_update(0.5, 0.0)
        with pytest.raises(ValueError):
            bayes_update(0.5, -1.0)

    @pytest.mark.parametrize("lr1,lr2", [(1.0, 2.0), (0.5, 1.0), (2.0, 10.0)])
    def test_monotone_in_lr(self, lr1: float, lr2: float) -> None:
        """M2: higher LR yields higher posterior (monotonicity)."""
        p1 = bayes_update(0.4, lr1)
        p2 = bayes_update(0.4, lr2)
        assert p1 < p2, f"bayes_update(0.4, {lr1})={p1} should be < bayes_update(0.4, {lr2})={p2}"


# ---------------------------------------------------------------------------
# R2: Relationship invariant — lower bound ≤ point posterior
# ---------------------------------------------------------------------------

class TestPosteriorLowerBoundOrdering:
    """R2: posterior_lower_bound(p, lr) ≤ bayes_update(p, lr.point).

    The lower bound must never exceed the point estimate.
    This is the ordering invariant that makes the entry condition conservative.
    """

    @pytest.mark.parametrize("prior_p,point,lower", [
        (0.3, 2.5, 1.8),
        (0.5, 1.5, 0.9),
        (0.7, 4.0, 3.0),
        (0.1, 10.0, 5.0),
        (0.5, 2.0, 2.0),  # lower == point → equality
    ])
    def test_lower_le_point(self, prior_p: float, point: float, lower: float) -> None:
        record = LRRecord(
            point=point, lower=lower,
            alert_type="ExtremeHeat", city="chicago",
            season="summer", lead_time_hours=12,
        )
        p_lower = posterior_lower_bound(prior_p, record)
        p_point = bayes_update(prior_p, point)
        assert p_lower <= p_point + 1e-12, (
            f"posterior_lower_bound={p_lower!r} > point posterior={p_point!r}; "
            "lower bound must not exceed point posterior."
        )

    def test_uses_effective_lower_not_point(self) -> None:
        """M3: posterior_lower_bound uses lr_record.effective_lower(), not .point."""
        record = LRRecord(
            point=5.0, lower=1.0,
            alert_type="ExtremeHeat", city="chicago",
            season="summer", lead_time_hours=12,
        )
        # With LR=1.0 (lower), posterior == prior
        p_lo = posterior_lower_bound(0.5, record)
        p_point = bayes_update(0.5, 5.0)  # should be 5/6 ≈ 0.833
        assert p_lo < p_point, "posterior_lower_bound must use lower bound LR, not point"
        # Verify it actually used LR=1.0: bayes_update(0.5, 1.0) == 0.5
        assert abs(p_lo - 0.5) < 1e-12, (
            f"posterior_lower_bound with lower=1.0 should equal 0.5, got {p_lo!r}"
        )

    def test_none_lower_falls_back_to_point(self) -> None:
        record = LRRecord(
            point=2.0, lower=None,
            alert_type="X", city="y", season="z", lead_time_hours=6,
        )
        p_lo = posterior_lower_bound(0.4, record)
        p_point = bayes_update(0.4, 2.0)
        assert abs(p_lo - p_point) < 1e-12, (
            "When lower=None, effective_lower() returns point; posterior_lower_bound should equal point posterior."
        )


# ---------------------------------------------------------------------------
# R3: LR table absent → no_trade with WEATHER_ALERT_LR_TABLE_MISSING
# ---------------------------------------------------------------------------

class TestLRTableAbsentYieldsNoTrade:
    """R3: Trusted+active alert but stub LR table → no_trade WEATHER_ALERT_LR_TABLE_MISSING.

    This is the data-gate relationship test: the AlertLRStub (default production
    default) must force no_trade regardless of how real the alert looks.
    """

    def test_stub_lr_table_forces_no_trade(self) -> None:
        strat = WeatherEventArbitrage()  # uses AlertLRStub by default

        metrics = _make_metrics(best_ask=Decimal("0.60"))
        analysis = _make_analysis(
            metrics=metrics,
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.30,
            alert_type="ExtremeHeat",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=12,
        )
        conn = _make_conn()
        ctx = _make_context(analysis)

        decision = strat.evaluate(
            context=ctx, conn=conn, decision_time=datetime.utcnow()
        )

        assert decision.outcome == "no_trade", (
            f"Expected no_trade but got {decision.outcome!r}. "
            "AlertLRStub must keep strategy in data-gated no_trade mode."
        )
        assert decision.reason == NoTradeReason.WEATHER_ALERT_LR_TABLE_MISSING, (
            f"Expected WEATHER_ALERT_LR_TABLE_MISSING, got {decision.reason!r}. "
            "LR table absent must use the specific gate reason, not SOURCE_UNTRUSTED."
        )

    def test_alert_lr_stub_always_returns_none(self) -> None:
        """AlertLRStub.lookup() always returns None for any combination."""
        stub = AlertLRStub()
        result = stub.lookup(
            alert_type="ExtremeHeat", city="chicago", season="summer", lead_time_hours=12
        )
        assert result is None, "AlertLRStub must always return None (data-gated)."

    def test_lr_table_absent_no_trade_written_to_db(self) -> None:
        """R3 DB side: no_trade_events row written when LR table returns None."""
        strat = WeatherEventArbitrage()
        metrics = _make_metrics(best_ask=Decimal("0.60"))
        analysis = _make_analysis(
            metrics=metrics,
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.30,
        )
        conn = _make_conn()
        ctx = _make_context(analysis)
        strat.evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())

        rows = conn.execute("SELECT reason FROM no_trade_events").fetchall()
        assert len(rows) == 1, f"Expected 1 no_trade_events row, got {len(rows)}"
        assert rows[0]["reason"] == NoTradeReason.WEATHER_ALERT_LR_TABLE_MISSING.value


# ---------------------------------------------------------------------------
# R4: No hardcoded edge constant in weather_event_arbitrage module
# ---------------------------------------------------------------------------

class TestNoHardcodedEdge:
    """R4: The _SHADOW_EDGE=0.04 placeholder must not exist in the module.

    Structural assertion: parse the source AST and verify no module-level
    float assignment named *EDGE* or *SHADOW* exists. Edge is always computed
    from Bayes + calibrated bounds, never from a hardcoded constant.
    """

    def test_no_shadow_edge_constant(self) -> None:
        import src.strategy.candidates.weather_event_arbitrage as mod

        source = inspect.getsource(mod)
        assert "_SHADOW_EDGE" not in source, (
            "_SHADOW_EDGE placeholder found in weather_event_arbitrage.py. "
            "Replace with Bayes-factor computed edge (§10 theorem)."
        )

    def test_no_hardcoded_edge_float_assignment(self) -> None:
        """AST: no module-level Assign of form <NAME containing EDGE> = <float literal>."""
        import src.strategy.candidates.weather_event_arbitrage as mod

        source = inspect.getsource(mod)
        tree = ast.parse(source)

        violations: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            # Only flag module-level (col_offset == 0) assignments
            if node.col_offset != 0:
                continue
            for target in node.targets:
                name = getattr(target, "id", "") or ""
                if ("EDGE" in name.upper() or "SHADOW" in name.upper()):
                    value = node.value
                    if isinstance(value, (ast.Constant,)) and isinstance(value.value, float):
                        violations.append(f"{name} = {value.value}")

        assert not violations, (
            f"Hardcoded edge float constants found at module level: {violations}. "
            "Edge must be Bayes-computed, never hardcoded."
        )


# ---------------------------------------------------------------------------
# G1-G5: Candidate gate sequence (feed/forecast data-gated paths)
# ---------------------------------------------------------------------------

class TestWeatherEventArbitrageGates:
    """Gates G1-G5: no_trade with WEATHER_ALERT_SOURCE_UNTRUSTED at each data-gate."""

    def _strat(self) -> WeatherEventArbitrage:
        return WeatherEventArbitrage()

    def test_g1_no_metrics(self) -> None:
        """G1: No MicrostructureMetrics → WEATHER_ALERT_SOURCE_UNTRUSTED."""
        conn = _make_conn()
        ctx = _make_context(_make_analysis(metrics=None))
        decision = self._strat().evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED

    def test_g2_no_alert_source(self) -> None:
        """G2: alert_source absent → WEATHER_ALERT_SOURCE_UNTRUSTED."""
        conn = _make_conn()
        analysis = _make_analysis(metrics=_make_metrics(), alert_source=None)
        ctx = _make_context(analysis)
        decision = self._strat().evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED

    def test_g3_untrusted_source(self) -> None:
        """G3: alert_source not in trusted set → WEATHER_ALERT_SOURCE_UNTRUSTED."""
        conn = _make_conn()
        analysis = _make_analysis(metrics=_make_metrics(), alert_source="unknown_vendor")
        ctx = _make_context(analysis)
        decision = self._strat().evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED

    def test_g4_no_active_alert(self) -> None:
        """G4: Trusted source, no active alert → WEATHER_ALERT_SOURCE_UNTRUSTED."""
        conn = _make_conn()
        analysis = _make_analysis(
            metrics=_make_metrics(),
            alert_source="nws_alerts",
            active_weather_alert=False,
        )
        ctx = _make_context(analysis)
        decision = self._strat().evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED

    def test_g5_no_prior_p(self) -> None:
        """G5: Trusted+active alert but prior_p absent → WEATHER_ALERT_SOURCE_UNTRUSTED."""
        conn = _make_conn()
        analysis = _make_analysis(
            metrics=_make_metrics(),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=None,
        )
        ctx = _make_context(analysis)
        decision = self._strat().evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_SOURCE_UNTRUSTED


# ---------------------------------------------------------------------------
# G6: LR table None → WEATHER_ALERT_LR_TABLE_MISSING (via FakeLRTable)
# ---------------------------------------------------------------------------

class TestG6LRTableNone:
    """G6: Custom LR table returning None → WEATHER_ALERT_LR_TABLE_MISSING."""

    def test_custom_lr_table_none(self) -> None:
        strat = WeatherEventArbitrage(lr_table=_FakeLRTable(None))
        conn = _make_conn()
        analysis = _make_analysis(
            metrics=_make_metrics(best_ask=Decimal("0.60")),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.30,
        )
        ctx = _make_context(analysis)
        decision = strat.evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_LR_TABLE_MISSING


# ---------------------------------------------------------------------------
# G7: Edge gate — p'⁻ − ask − φ ≤ 0 → WEATHER_ALERT_EDGE_NONPOSITIVE
# ---------------------------------------------------------------------------

class TestG7EdgeGate:
    """G7: Posterior lower bound does not beat ask + fee → WEATHER_ALERT_EDGE_NONPOSITIVE."""

    def test_edge_nonpositive_when_ask_high(self) -> None:
        # prior_p=0.1, LR lower=1.1 → p' ≈ 0.109; ask=0.60 → clearly negative edge
        record = _make_lr_record(point=1.1, lower=1.1)
        strat = WeatherEventArbitrage(lr_table=_FakeLRTable(record))
        conn = _make_conn()
        analysis = _make_analysis(
            metrics=_make_metrics(best_ask=Decimal("0.60")),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.10,
        )
        ctx = _make_context(analysis)
        decision = strat.evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "no_trade"
        assert decision.reason == NoTradeReason.WEATHER_ALERT_EDGE_NONPOSITIVE


# ---------------------------------------------------------------------------
# G8: Enter path — valid LR + favorable ask → outcome == "enter"
# ---------------------------------------------------------------------------

class TestG8EnterPath:
    """G8: All gates pass, posterior lower bound beats ask+fee → enter."""

    def test_enter_with_favorable_conditions(self) -> None:
        # prior_p=0.5, LR lower=10.0 → p'⁻ = bayes_update(0.5, 10) = 10/11 ≈ 0.909
        # ask=0.50, fee ≈ 0.05*1*0.5*0.5=0.0125 → edge ≈ 0.909 - 0.50 - 0.0125 > 0
        record = _make_lr_record(point=10.0, lower=10.0)
        strat = WeatherEventArbitrage(lr_table=_FakeLRTable(record))
        conn = _make_conn()
        analysis = _make_analysis(
            metrics=_make_metrics(best_ask=Decimal("0.50")),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.50,
        )
        ctx = _make_context(analysis)
        decision = strat.evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())
        assert decision.outcome == "enter", (
            f"Expected enter, got {decision.outcome!r} with reason {decision.reason!r}"
        )
        assert decision.side == "buy_yes"
        assert decision.edge is not None and decision.edge > 0, (
            f"Edge must be positive on enter path, got {decision.edge!r}"
        )
        assert decision.p_posterior is not None, "p_posterior must be set on enter path"

    def test_enter_writes_decision_events_row(self) -> None:
        """G8 DB side: enter writes decision_events row with correct strategy_key."""
        record = _make_lr_record(point=10.0, lower=10.0)
        strat = WeatherEventArbitrage(lr_table=_FakeLRTable(record))
        conn = _make_conn()
        analysis = _make_analysis(
            metrics=_make_metrics(best_ask=Decimal("0.50")),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.50,
        )
        ctx = _make_context(analysis)
        strat.evaluate(context=ctx, conn=conn, decision_time=datetime.utcnow())

        rows = conn.execute("SELECT strategy_key, outcome FROM decision_events").fetchall()
        assert len(rows) == 1, f"Expected 1 decision_events row, got {len(rows)}"
        assert rows[0]["strategy_key"] == "weather_event_arbitrage"
        assert rows[0]["outcome"] in ("shadow", "enter", "shadow_enter")
