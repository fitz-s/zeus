# Created: 2026-09-04
# Last reused or audited: 2026-10-03
# Lifecycle: created=2026-09-04; last_reviewed=2026-10-03; last_reused=2026-10-03
# Purpose: Authenticate current held exit q, including typed zero-observation Day0.
# Reuse: Inspect source revision, canonical ENTRY identity and dated fit fixtures.
# Current authority: docs/operations/current/finite_evidence_probability_symmetry/PLAN.md
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

import json
import math
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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


@pytest.mark.parametrize("direction", ("buy_yes", "buy_no"))
@pytest.mark.parametrize("fresh_quote", (True, False))
def test_authenticated_identity_holding_uses_current_source_without_residual(direction, fresh_quote):
    from src.calibration.market_anchored_live_fit import HeldSourceIdentityBinding
    from src.contracts.payoff_q_correction import SourceIdentityBaseline

    side = "YES" if direction == "buy_yes" else "NO"
    baseline = SourceIdentityBaseline(
        family_key="family", bin_id="bin", side=side,
        token_id="yes-token" if side == "YES" else "no-token",
        raw_q=0.9, p0=0.4, raw_probability_revision="entry-revision",
        q_version="entry-q", probability_witness_identity="entry-witness",
        probability_content_identity="entry-content", source_truth_identity="entry-source",
        sample_matrix_identity="entry-samples",
    )
    binding = HeldSourceIdentityBinding(
        baseline=baseline, position_id="pos-market-anchored-exit",
        decision_log_id=1, decision_certificate_hash="authenticated-certificate",
    )
    register_active_provider(SimpleNamespace(load=lambda **_: binding))
    ctx = replace(_exit_context(fresh_prob=0.3, current_market_price=0.5, best_bid=0.5),
                  current_market_price_is_fresh=fresh_quote)
    position = _held_position(direction)
    raw, raw_ok = position._held_side_point_with_confidence(ctx)
    q, valid, source = position._exit_q_mean_and_source(ctx)
    assert raw_ok
    assert q == raw
    assert valid is fresh_quote
    assert source == ("source_identity_baseline" if fresh_quote else "entry_calibration_unavailable")


def test_authenticated_identity_cohort_holding_uses_current_source_without_full_witness():
    from src.calibration.market_anchored_live_fit import (
        HeldSourceIdentityBinding,
        HeldSourceIdentityCohortBinding,
        HeldSourceIdentityEntryParent,
    )
    from src.contracts.payoff_q_correction import SourceIdentityBaseline

    baseline = SourceIdentityBaseline(
        family_key="family", bin_id="bin", side="YES", token_id="yes-token",
        raw_q=0.9, p0=0.4, raw_probability_revision="entry-revision",
        q_version="entry-q", probability_witness_identity="entry-witness",
        probability_content_identity="entry-content", source_truth_identity="entry-source",
        sample_matrix_identity="entry-samples",
    )
    cohort = HeldSourceIdentityCohortBinding(
        parents=tuple(
            HeldSourceIdentityEntryParent(
                command_id,
                HeldSourceIdentityBinding(
                    baseline=baseline, position_id="pos-market-anchored-exit",
                    decision_log_id=index, decision_certificate_hash=f"certificate-{index}",
                ),
            )
            for index, command_id in enumerate(("entry-a", "entry-b"), start=1)
        )
    )
    register_active_provider(SimpleNamespace(load=lambda **_: cohort))
    ctx = replace(
        _exit_context(fresh_prob=0.3, current_market_price=0.5, best_bid=0.5),
        current_market_price_is_fresh=True,
    )
    position = _held_position("buy_yes")
    raw, raw_ok = position._held_side_point_with_confidence(ctx)
    q, valid, source = position._exit_q_mean_and_source(ctx)
    assert raw_ok
    assert q == raw
    assert valid
    assert source == "source_identity_baseline"


@pytest.fixture
def zero_observation_entry_provider(monkeypatch):
    """Canonical private ENTRY proof; only the persisted audit read is a leaf double.

    The normal loader authenticates event/attribution, certificate hash, native
    token/side and baseline-to-receipt equality. Its provider and at_decision
    revision gate are real, including when the original source is replayed.
    """
    from src.calibration import market_anchored_live_fit as live_fit
    from src.contracts.payoff_q_correction import SourceIdentityBaseline
    from src.decision_kernel.canonicalization import stable_hash

    opened = []

    def build(position):
        side = "YES" if position.direction.value == "buy_yes" else "NO"
        token = position.token_id if side == "YES" else position.no_token_id
        baseline = SourceIdentityBaseline(
            family_key=f"{position.city}|{position.target_date}|{position.temperature_metric}",
            bin_id=position.bin_label, side=side, token_id=token, raw_q=.9995072436735342,
            p0=.85, raw_probability_revision="entry-revision", q_version="entry-q",
            probability_witness_identity="entry-witness", probability_content_identity="entry-content",
            source_truth_identity="entry-source", sample_matrix_identity="entry-samples",
        )
        trade, world = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        opened.extend((trade, world))
        trade.execute("CREATE TABLE position_events (position_id TEXT, event_type TEXT, sequence_no INTEGER, decision_id TEXT, payload_json TEXT, command_id TEXT)")
        trade.execute("CREATE TABLE position_decision_attribution (position_id TEXT, intent_kind TEXT, resolution TEXT, decision_certificate_hash TEXT, command_id TEXT)")
        world.execute("CREATE TABLE decision_certificates (certificate_hash TEXT, certificate_type TEXT, mode TEXT, verifier_status TEXT, payload_json TEXT, payload_hash TEXT)")
        receipt = {
            "decision_log_id": 7, "decision_log_mode": "global_single_order_auction",
            "receipt_hash": "b" * 64, "execution_binding_hash": "c" * 64,
            "artifact_summary_hash": "d" * 64, "schema_version": 22,
            "winner_event_id": "event-a", "winner_candidate_id": "candidate-a",
            "winner_actuation_identity": "actuation-a", "selection_epoch_identity": "epoch-a",
        }
        certificate = {
            "global_auction_receipt": receipt, "direction": position.direction.value,
            "token_id": token, "global_token_id": token,
            "global_family_key": baseline.family_key, "global_bin_id": baseline.bin_id,
            "market_anchored_correction": baseline.as_cert_fields(),
        }
        trade.execute("INSERT INTO position_events VALUES (?,?,?,?,?,?)", (
            position.trade_id, "ENTRY_ORDER_FILLED", 1, "cert-a",
            json.dumps({"decision_log_id": 7}), "command-filled",
        ))
        trade.execute("INSERT INTO position_decision_attribution VALUES (?,?,?,?,?)", (
            position.trade_id, "ENTRY", "ATTRIBUTED", "cert-a", "command-filled",
        ))
        world.execute("INSERT INTO decision_certificates VALUES (?,?,?,?,?,?)", (
            "cert-a", "ActionableTradeCertificate", "LIVE", "VERIFIED",
            json.dumps(certificate), stable_hash(certificate),
        ))
        scope = CalibrationFitScope(
            position.temperature_metric, "TAKER_LIMIT", "FOK_FULL_OR_ZERO", "entry-revision",
        )
        monkeypatch.setattr(live_fit, "_load_held_audit_context", lambda *_a, **_kw: {
            "market_anchored_fit_artifact_audit": {"source_identity_baselines": {
                "entry": {"status": "SOURCE_IDENTITY_BASELINE", "scope": scope.as_payload(),
                          "baseline": baseline.as_payload()},
            }},
        })
        binding = live_fit.load_held_entry_calibration(
            trade, position_id=position.trade_id, token_id=token, side=side, world_conn=world,
        )
        assert isinstance(binding, live_fit.HeldSourceIdentityBinding)
        provider = live_fit.HeldEntryCalibrationProvider(trade, world_conn=world)
        register_active_provider(provider)
        return trade, world

    yield build
    register_active_provider(None)
    for conn in opened:
        conn.close()


def _zero_observation_current_receipt(position, *, q=.3, ci=(.2, .4)):
    from src.engine import monitor_refresh
    from src.data.replacement_forecast_cycle_policy import CURRENT_EVIDENCE_SEMANTICS_REVISION
    from src.data.replacement_forecast_readiness import SOURCE_ID

    now = datetime.now(timezone.utc)
    return monitor_refresh._compact_monitor_probability_receipt({
        "schema_version": 1, "selected_method": "replacement_posterior",
        "probability_authority": "forecast_posteriors",
        "probability_functional": "POSTERIOR_PREDICTIVE_MEAN",
        "probability_semantics_revision": CURRENT_EVIDENCE_SEMANTICS_REVISION,
        "posterior_id": "733934", "computed_at": (now - timedelta(minutes=2)).isoformat(),
        "source_cycle_time": (now - timedelta(hours=2)).isoformat(),
        "source_id": SOURCE_ID, "posterior_method": SOURCE_ID,
        "captured_at_utc": now.isoformat(), "city": position.city,
        "target_date": position.target_date, "temperature_metric": position.temperature_metric,
        "bin_label": position.bin_label, "bin_key": position.bin_label,
        "held_direction": position.direction.value, "held_side_probability": q,
        "held_side_lcb": ci[0], "held_side_ucb": ci[1],
        "day0_zero_observation_proven": True,
    })


@pytest.mark.parametrize("metric", ("high", "low"))
@pytest.mark.parametrize("direction", ("buy_yes", "buy_no"))
def test_zero_observation_day0_authenticates_current_source_revision(
    zero_observation_entry_provider, metric, direction,
):
    position = _held_position(direction, target_date=datetime.now(timezone.utc).date().isoformat())
    position.temperature_metric = metric
    zero_observation_entry_provider(position)
    ctx = replace(
        _exit_context(fresh_prob=.3, current_market_price=.5, best_bid=.5),
        day0_active=True, position_state="day0_window", current_ci=(.2, .4),
        probability_receipt=_zero_observation_current_receipt(position),
    )
    q, valid, source = position._exit_q_mean_and_source(ctx)
    assert (float(q), valid, source) == (.3, True, "source_identity_baseline")
    decision = position.evaluate_exit(ctx)
    assert decision.trigger == "SELL_REVERSAL"
    assert "exit_q:source_identity_baseline" in decision.applied_validations


@pytest.mark.parametrize("mutation", (
    "missing_receipt", "unknown_observation", "wrong_city", "wrong_date", "wrong_metric",
    "wrong_direction", "wrong_bin", "wrong_bin_key", "wrong_revision", "wrong_source", "wrong_method",
    "missing_posterior", "wrong_point", "wrong_ci", "missing_ci", "future_compute",
    "future_read", "naive_clock", "stale_source", "tampered_hash", "stale_q", "stale_quote", "entry_conflict",
    "observed_q_version",
))
def test_zero_observation_day0_current_receipt_failures_remain_unavailable(
    zero_observation_entry_provider, mutation,
):
    from src.engine import monitor_refresh

    position = _held_position(target_date=datetime.now(timezone.utc).date().isoformat())
    trade, _world = zero_observation_entry_provider(position)
    receipt = dict(_zero_observation_current_receipt(position))
    receipt.pop("evidence_content_hash", None)
    changes = {
        "unknown_observation": {"day0_zero_observation_proven": False},
        "wrong_city": {"city": "Munich"}, "wrong_date": {"target_date": "2026-01-01"},
        "wrong_metric": {"temperature_metric": "low"},
        "wrong_direction": {"held_direction": "buy_no"}, "wrong_bin": {"bin_label": "99C"},
        "wrong_bin_key": {"bin_key": "99C"},
        "wrong_revision": {"probability_semantics_revision": "ensemble_center_scenarios_v5"},
        "wrong_source": {"source_id": "legacy"}, "wrong_method": {"posterior_method": "legacy"},
        "missing_posterior": {"posterior_id": ""}, "wrong_point": {"held_side_probability": .9},
        "wrong_ci": {"held_side_lcb": .35}, "missing_ci": {"held_side_ucb": None},
        "future_compute": {"computed_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        "future_read": {"captured_at_utc": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
        "naive_clock": {"captured_at_utc": datetime.now().isoformat()},
        "stale_source": {"source_cycle_time": (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()},
        "observed_q_version": {"q_version": "day0-semrev:observed:certificate"},
    }
    receipt.update(changes.get(mutation, {}))
    receipt = monitor_refresh._compact_monitor_probability_receipt(receipt)
    if mutation == "tampered_hash":
        receipt["posterior_id"] = "tampered"
    if mutation == "entry_conflict":
        trade.execute("UPDATE position_events SET decision_id='conflicting-entry'")
    ctx = replace(
        _exit_context(fresh_prob=.3, current_market_price=.5, best_bid=.5),
        day0_active=True, position_state="day0_window", current_ci=(.2, .4),
        fresh_prob_is_fresh=mutation != "stale_q",
        current_market_price_is_fresh=mutation != "stale_quote",
        probability_receipt=None if mutation == "missing_receipt" else receipt,
    )
    _q, valid, _source = position._exit_q_mean_and_source(ctx)
    assert valid is False
    assert position.evaluate_exit(ctx).trigger == "EVIDENCE_UNAVAILABLE"


@pytest.mark.parametrize("has_day0_revision", (True, False))
def test_observed_day0_keeps_its_own_revision_without_zero_observation_role(
    zero_observation_entry_provider, has_day0_revision,
):
    from src.events.day0_authority import DAY0_PROBABILITY_SEMANTICS_REVISION

    position = _held_position(target_date=datetime.now(timezone.utc).date().isoformat())
    zero_observation_entry_provider(position)
    receipt = {
        "probability_authority": "day0_remaining_day_global_probability_v1",
        "probability_semantics_revision": "ensemble_center_scenarios_v6",
    }
    if has_day0_revision:
        receipt["q_version"] = f"day0-semrev:{DAY0_PROBABILITY_SEMANTICS_REVISION}:observed-current"
    ctx = replace(
        _exit_context(fresh_prob=.3, current_market_price=.5, best_bid=.5),
        day0_active=True, position_state="day0_window", probability_receipt=receipt,
    )
    _q, valid, source = position._exit_q_mean_and_source(ctx)
    assert valid is has_day0_revision
    assert source == ("source_identity_baseline" if has_day0_revision else "entry_calibration_unavailable")
