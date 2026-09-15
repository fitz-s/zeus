# Created: 2026-09-04
# Last reused or audited: 2026-09-15
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   (market-anchored calibrator, item 9) + this task's fix — the exit stop was
#   comparing against the RAW posterior-predictive point (measured +0.170
#   over-biased on filled positions), while entries already act on the
#   market-anchored CORRECTED probability. src/state/portfolio.py
#   Position._exit_q_mean_and_source / Position.evaluate_exit;
#   src/calibration/market_anchored_live_fit.py register_active_provider /
#   get_active_provider / corrected_probability.
"""The immediate stop shares the global TAKER SELL bid calibration anchor.
Missing live entry proof fails closed; an absent provider is offline-only
compatibility. Evaluating the stop never opens a DB connection of its own.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.calibration.market_anchored_live_fit import (
    corrected_probability,
    get_active_provider,
    register_active_provider,
)
from src.calibration.market_anchored_residual import LEAD_BUCKETS, ResidualCalibratorArtifact
from src.contracts.payoff_q_correction import CalibrationFitScope
from src.state.portfolio import ExitContext, Position


@pytest.fixture(autouse=True)
def _clear_active_provider():
    """The active provider is process-global state; never let one test's
    registration leak into another."""
    assert get_active_provider() is None, "a prior test left a provider registered"
    yield
    register_active_provider(None)


def _stub_artifact(*, alpha_day0: float = 0.09, beta: float = 0.0) -> ResidualCalibratorArtifact:
    return ResidualCalibratorArtifact(
        alpha={"day0": alpha_day0, "day1": 0.0, "day2": 0.0},
        beta=beta,
        lambda_=1.0,
        clip_d=3.0,
        p_clip=(0.005, 0.995),
        lead_buckets=LEAD_BUCKETS,
        training_cutoff="2026-09-04T00:00:00Z",
        n_train=100,
        n_excluded=0,
        excluded_reasons={},
        param_hash="stub",
        lead_calendar_revision="city_local_target_date_v1",
        city_timezone_snapshot=(("Warsaw", "Europe/Warsaw"),),
    )


class _StubBinding:
    family_key = "Warsaw|2026-09-04|high"
    bin_id = "20-21C"

    def __init__(
        self,
        artifact: ResidualCalibratorArtifact,
        *,
        execution_mode: str = "TAKER_LIMIT",
    ) -> None:
        self._artifact = artifact
        self.fit_scope = CalibrationFitScope(
            metric="high",
            execution_mode=execution_mode,
            execution_contract=(
                "MAKER_REST" if execution_mode == "MAKER_REST" else "FOK_FULL_OR_ZERO"
            ),
            raw_probability_revision="stub-raw-revision",
        )
        self.p0_values: list[float] = []

    def corrected_probability(self, **kwargs):
        self.p0_values.append(float(kwargs["p0"]))
        applied = corrected_probability(
            self._artifact,
            p0=kwargs["p0"], q_raw=kwargs["raw_q"], city=kwargs["city"],
            target_date=kwargs["target_date"], decision_at=kwargs["decision_at"],
            side=kwargs["side"],
        )
        assert applied is not None
        return type("Correction", (), {"corrected_q": applied[0]})()


class _StubProvider:
    """A pre-bound ENTRY reader that cannot fit or open a connection."""

    def __init__(
        self,
        artifact: ResidualCalibratorArtifact | None,
        *,
        execution_mode: str = "TAKER_LIMIT",
    ) -> None:
        self._binding = (
            None
            if artifact is None
            else _StubBinding(artifact, execution_mode=execution_mode)
        )

    def load(self, **_kwargs):
        if self._binding is None:
            raise RuntimeError("entry proof unavailable")
        return self._binding


def _held_position(direction: str = "buy_yes", *, target_date: str = "2026-09-04") -> Position:
    return Position(
        trade_id="pos-market-anchored-exit",
        market_id="mkt-market-anchored-exit",
        city="Warsaw",
        cluster="europe",
        target_date=target_date,
        bin_label="20-21C",
        direction=direction,
        entry_price=0.30,
        size_usd=20.0,
        shares=40.0,
        cost_basis_usd=20.0,
        p_posterior=0.50,
        token_id="yes-token",
        no_token_id="no-token",
    )


def _exit_context(
    *,
    fresh_prob: float,
    current_market_price: float,
    best_bid: float,
    best_ask: float | None = None,
    min_tick: float | None = 0.01,
) -> ExitContext:
    return ExitContext(
        exit_reason="",
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid,
        best_ask=best_ask if best_ask is not None else best_bid + 0.02,
        min_tick=min_tick,
        market_vig=1.0,
        hours_to_settlement=12.0,
        position_state="holding",
        day0_active=False,
        whale_toxicity=False,
        divergence_score=0.0,
        market_velocity_1h=0.0,
        current_ci=(0.05, 0.95),
        belief_available=True,
    )


# --- (a) buy_yes: market-anchored correction replaces the raw point ---------


def test_buy_yes_exit_uses_market_anchored_corrected_q():
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=0.09)))
    # decision_date == target_date (both "today" UTC) => lead_bucket day0.
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is True
    assert source == "market_anchored"
    expected = 1.0 / (1.0 + math.exp(-(math.log(0.17 / 0.83) + 0.09)))
    assert float(q_mean) == pytest.approx(expected, abs=1e-9)

    decision = pos.evaluate_exit(ctx)
    assert "exit_q:market_anchored" in decision.applied_validations
    assert "exit_q:raw" not in decision.applied_validations


# --- (b) buy_no: complement law applied --------------------------------------


def test_buy_no_exit_applies_the_complement_law():
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=0.09)))
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_no", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is True
    assert source == "market_anchored"
    expected = 1.0 - 1.0 / (1.0 + math.exp(-(math.log(0.83 / 0.17) + 0.09)))
    assert float(q_mean) == pytest.approx(expected, abs=1e-9)

    decision = pos.evaluate_exit(ctx)
    assert "exit_q:market_anchored" in decision.applied_validations


# --- (c) provider unavailable -> fail open to the raw point ------------------


def test_no_provider_registered_falls_back_to_raw_q():
    assert get_active_provider() is None
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is True
    assert source == "raw"
    assert float(q_mean) == pytest.approx(0.50, abs=1e-9)

    decision = pos.evaluate_exit(ctx)
    assert "exit_q:raw" in decision.applied_validations
    assert "exit_q:market_anchored" not in decision.applied_validations


def test_missing_entry_proof_makes_statistical_exit_evidence_unavailable():
    register_active_provider(_StubProvider(None))
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert evidence_ok is False
    assert source == "entry_calibration_unavailable"
    assert float(q_mean) == pytest.approx(0.50, abs=1e-9)


def test_live_reader_unavailable_never_reverts_to_raw_statistical_q():
    from src.calibration.market_anchored_live_fit import UnavailableHeldEntryCalibrationProvider

    register_active_provider(UnavailableHeldEntryCalibrationProvider())
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(direction="buy_yes", target_date=today)
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)

    assert float(q_mean) == pytest.approx(0.50, abs=1e-9)
    assert evidence_ok is False
    assert source == "entry_calibration_unavailable"


# --- (d) evaluate_exit never opens its own DB connection ---------------------


def test_evaluate_exit_never_opens_its_own_db_connection(monkeypatch):
    register_active_provider(_StubProvider(_stub_artifact()))

    def _explode(*_args, **_kwargs):
        raise AssertionError("evaluate_exit must never open its own DB connection")

    monkeypatch.setattr(sqlite3, "connect", _explode)

    pos = _held_position(
        direction="buy_yes", target_date=datetime.now(timezone.utc).date().isoformat()
    )
    ctx = _exit_context(fresh_prob=0.50, current_market_price=0.20, best_bid=0.15)

    # Must not raise: only the immutable ENTRY binding is consulted.
    decision = pos.evaluate_exit(ctx)
    assert decision.trigger in {"HOLD", "SELL_REVERSAL", "EVIDENCE_UNAVAILABLE"}
    applied = set(decision.applied_validations)
    assert "exit_q:market_anchored" in applied


@pytest.mark.parametrize("direction,alpha", [("buy_yes", -0.30), ("buy_no", 0.30)])
@pytest.mark.parametrize("ask,market_price", [(0.62, 0.61)])
def test_spread_cannot_hide_immediate_sell_reversal(direction, alpha, ask, market_price):
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=alpha)))
    pos = _held_position(direction, target_date=datetime.now(timezone.utc).date().isoformat())
    ctx = _exit_context(
        fresh_prob=0.70, current_market_price=market_price, best_bid=0.60, best_ask=ask,
    )
    ctx = replace(ctx, bid_ladder=((0.60, 40.0),))

    q, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    # Same gross bid and held-side residual as the canonical TAKER SELL.
    expected_q = (
        1.0 / (1.0 + math.exp(-(math.log(ask / (1.0 - ask)) - 0.30)))
        if direction == "buy_yes"
        else 1.0 - 1.0 / (
            1.0 + math.exp(-(math.log((1.0 - ask) / ask) + 0.30))
        )
    )
    assert evidence_ok and source == "market_anchored"
    assert float(q) == pytest.approx(expected_q)
    assert pos.evaluate_exit(ctx).trigger == "SELL_REVERSAL"


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("bid", [None, float("nan"), float("inf"), -0.1, 1.1])
def test_live_sell_anchor_never_falls_back_to_midpoint(direction, bid):
    register_active_provider(_StubProvider(_stub_artifact()))
    pos = _held_position(direction, target_date=datetime.now(timezone.utc).date().isoformat())
    ctx = _exit_context(fresh_prob=0.5, current_market_price=0.6, best_bid=0.5, best_ask=0.7)
    ctx = replace(ctx, best_bid=bid)

    q_mean, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    assert evidence_ok is True
    assert source == "market_anchored"
    expected = 1.0 / (1.0 + math.exp(-(math.log(0.7 / 0.3) + 0.09)))
    if direction == "buy_no":
        expected = 1.0 - 1.0 / (
            1.0 + math.exp(-(math.log(0.3 / 0.7) + 0.09))
        )
    assert float(q_mean) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("bid", [0.0, 1.0])
def test_taker_anchor_uses_ask_even_when_bid_is_endpoint(direction, bid):
    register_active_provider(_StubProvider(_stub_artifact(alpha_day0=0.0)))
    pos = _held_position(direction, target_date=datetime.now(timezone.utc).date().isoformat())
    ctx = _exit_context(fresh_prob=0.5, current_market_price=0.4, best_bid=bid, best_ask=0.4)
    q, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    assert evidence_ok is True
    assert source == "market_anchored"
    expected = 0.4
    assert float(q) == pytest.approx(expected)


def test_taker_anchor_uses_ask_not_bid_or_tick():
    binding_provider = _StubProvider(_stub_artifact(alpha_day0=0.09))
    register_active_provider(binding_provider)
    pos = _held_position(target_date=datetime.now(timezone.utc).date().isoformat())
    first = _exit_context(
        fresh_prob=0.50, current_market_price=0.20, best_bid=0.10,
        best_ask=0.20, min_tick=0.01,
    )
    second = replace(first, best_bid=0.18, min_tick=0.05)
    q_first, ok_first, _ = pos._exit_q_mean_and_source(first)
    q_second, ok_second, _ = pos._exit_q_mean_and_source(second)
    assert ok_first and ok_second
    assert float(q_first) == pytest.approx(float(q_second))
    assert binding_provider._binding is not None
    assert binding_provider._binding.p0_values == [0.20, 0.20]


def test_maker_anchor_uses_bid_plus_tick_and_no_mirror():
    provider = _StubProvider(
        _stub_artifact(alpha_day0=0.09), execution_mode="MAKER_REST"
    )
    register_active_provider(provider)
    today = datetime.now(timezone.utc).date().isoformat()
    pos = _held_position(target_date=today)
    ctx = _exit_context(
        fresh_prob=0.50, current_market_price=0.20, best_bid=0.15,
        best_ask=0.18, min_tick=0.01,
    )
    q, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    assert evidence_ok and source == "market_anchored"
    expected = 1.0 / (1.0 + math.exp(-(math.log(0.16 / 0.84) + 0.09)))
    assert float(q) == pytest.approx(expected, abs=1e-9)
    assert provider._binding is not None
    assert provider._binding.p0_values == [0.16]

    no_pos = _held_position(direction="buy_no", target_date=today)
    no_q, no_ok, no_source = no_pos._exit_q_mean_and_source(ctx)
    expected_no = 1.0 - 1.0 / (
        1.0 + math.exp(-(math.log(0.84 / 0.16) + 0.09))
    )
    assert no_ok and no_source == "market_anchored"
    assert float(no_q) == pytest.approx(expected_no, abs=1e-9)
    assert provider._binding.p0_values == [0.16, 0.16]


@pytest.mark.parametrize(
    "ctx",
    [
        replace(
            _exit_context(fresh_prob=0.5, current_market_price=0.2, best_bid=0.15),
            best_ask=None,
        ),
        _exit_context(fresh_prob=0.5, current_market_price=0.2, best_bid=0.15, min_tick=None),
        _exit_context(fresh_prob=0.5, current_market_price=0.2, best_bid=0.15, best_ask=0.16),
        _exit_context(
            fresh_prob=0.5, current_market_price=0.2, best_bid=0.155,
            best_ask=0.18, min_tick=0.01,
        ),
        replace(_exit_context(fresh_prob=0.5, current_market_price=0.2, best_bid=0.15), current_market_price_is_fresh=False),
    ],
)
def test_maker_anchor_rejects_missing_crossed_or_stale_quote(ctx):
    register_active_provider(
        _StubProvider(_stub_artifact(), execution_mode="MAKER_REST")
    )
    pos = _held_position(target_date=datetime.now(timezone.utc).date().isoformat())
    _, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    assert evidence_ok is False
    assert source == "entry_calibration_unavailable"


def test_maker_anchor_allows_upper_in_band_ask_with_independent_sell_band():
    provider = _StubProvider(
        _stub_artifact(alpha_day0=0.0), execution_mode="MAKER_REST"
    )
    register_active_provider(provider)
    pos = _held_position(target_date=datetime.now(timezone.utc).date().isoformat())
    ctx = _exit_context(
        fresh_prob=0.5, current_market_price=0.95, best_bid=0.94,
        best_ask=0.96, min_tick=0.01,
    )
    q, evidence_ok, source = pos._exit_q_mean_and_source(ctx)
    assert evidence_ok and source == "market_anchored"
    assert float(q) == pytest.approx(0.95)
    assert provider._binding is not None
    assert provider._binding.p0_values == [0.95]


def test_monitor_quote_position_exit_context_carries_and_clears_tick(monkeypatch):
    from src.engine import cycle_runtime, monitor_refresh

    pos = _held_position(target_date=datetime.now(timezone.utc).date().isoformat())
    class _Book:
        def get_orderbook(self, token_id):
            assert token_id == "yes-token"
            return {
                "bids": [{"price": "0.15", "size": "10"}],
                "asks": [{"price": "0.18", "size": "10"}],
                "min_order_size": "5",
                "tick_size": "0.01",
            }

    monkeypatch.setattr(monitor_refresh, "log_microstructure", lambda *_a, **_k: None, raising=False)
    monitor_refresh.refresh_exact_zero_position(None, _Book(), pos)
    assert pos.last_monitor_min_tick == pytest.approx(0.01)
    edge = SimpleNamespace(
        p_market=[0.16], p_posterior=0.5,
        confidence_band_lower=0.4, confidence_band_upper=0.6,
    )
    ctx = cycle_runtime._build_exit_context(
        pos, edge, hours_to_settlement=1.0, ExitContext=ExitContext,
    )
    assert ctx.best_bid == pytest.approx(0.15)
    assert ctx.best_ask == pytest.approx(0.18)
    assert ctx.min_tick == pytest.approx(0.01)

    monitor_refresh.refresh_exact_zero_position(
        None, SimpleNamespace(), pos, refresh_quote=False,
    )
    assert pos.last_monitor_best_bid is None
    assert pos.last_monitor_best_ask is None
    assert pos.last_monitor_min_tick is None


@pytest.mark.parametrize("direction", ["buy_yes", "buy_no"])
@pytest.mark.parametrize("fresh_tick", [0.01, None], ids=["fresh_tick", "missing_tick"])
def test_pending_exit_retry_replaces_context_tick_from_fresh_quote(
    monkeypatch, direction, fresh_tick
):
    from src.engine import cycle_runtime, monitor_refresh

    pos = _held_position(direction=direction)
    stale_context = replace(
        _exit_context(
            fresh_prob=0.5,
            current_market_price=0.20,
            best_bid=0.15,
            best_ask=0.18,
            min_tick=0.05,
        ),
        current_market_price_is_fresh=False,
    )
    quote = SimpleNamespace(
        full_depth_action_authority=True,
        best_bid=0.16,
        best_ask=0.19,
        bid_size=10.0,
        ask_size=10.0,
        mark_price=0.175,
        source_timestamp="2026-09-15T00:00:00Z",
        bid_ladder=((0.16, 10.0),),
        min_tick=fresh_tick,
    )
    seen = {}

    def retry_quote(conn, clob, got_pos, *, retry_after_prefetch):
        seen["args"] = (conn, clob, got_pos, retry_after_prefetch)
        return quote

    monkeypatch.setattr(monitor_refresh, "monitor_quote_refresh", retry_quote)
    refreshed, did_refresh = cycle_runtime._refresh_pending_exit_retry_quote_from_current_clob(
        conn=object(),
        clob=object(),
        pos=pos,
        exit_context=stale_context,
        identity_seed_allowed=True,
    )

    assert did_refresh is True
    assert seen["args"][2] is pos
    assert seen["args"][3] is True
    assert refreshed.min_tick == fresh_tick
    assert pos.last_monitor_min_tick == fresh_tick


@pytest.mark.parametrize(
    "field", ["tick_size", "min_tick_size", "minimum_tick_size", "minTickSize"]
)
def test_monitor_quote_reads_tick_alias_from_same_book(field):
    from src.engine import monitor_refresh

    class _Book:
        def get_orderbook(self, token_id):
            assert token_id == "yes-token"
            return {
                "bids": [{"price": "0.15", "size": "10"}],
                "asks": [{"price": "0.18", "size": "10"}],
                field: "0.005",
            }

    quote = monitor_refresh.monitor_quote_refresh(
        None, _Book(), _held_position(),
    )
    assert quote is not None
    assert quote.min_tick == pytest.approx(0.005)


def test_monitor_bba_fallback_without_book_metadata_stays_unavailable():
    from src.engine import monitor_refresh

    class _BbaOnly:
        def get_best_bid_ask(self, token_id):
            assert token_id == "yes-token"
            return 0.15, 0.18, 10.0, 10.0

    quote = monitor_refresh.monitor_quote_refresh(
        None, _BbaOnly(), _held_position(),
    )
    assert quote is None


def test_monitor_tick_metadata_does_not_carry_between_books():
    from src.engine import monitor_refresh

    assert monitor_refresh._book_min_tick({"tick_size": "0.005"}) == pytest.approx(0.005)
    assert monitor_refresh._book_min_tick({"bids": [], "asks": []}) is None


def test_monitor_book_without_tick_metadata_keeps_quote_tick_unavailable():
    from src.engine import monitor_refresh

    class _Book:
        def get_orderbook(self, token_id):
            assert token_id == "yes-token"
            return {
                "bids": [{"price": "0.15", "size": "10"}],
                "asks": [{"price": "0.18", "size": "10"}],
            }

    quote = monitor_refresh.monitor_quote_refresh(
        None, _Book(), _held_position(),
    )
    assert quote is not None
    assert quote.min_tick is None
