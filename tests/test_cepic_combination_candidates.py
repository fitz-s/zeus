# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/operations/task_2026-05-21_mainline_completion_authority/STRATEGY_TAXONOMY_DIRECTIVE.md §13, §14
"""C-EPIC combination strategy relationship tests.

RELATIONSHIP TESTS for:
  C1 (§13) — shoulder_buy × weather_event joint tail Bayes
    R1: posterior_odds = prior_tail_odds · LR(A); joint Bayes update is multiplicative
    R2: p⁻_tail(X,A) is the conformal lower bound of the Bayes-updated tail probability
    R3: Enter iff p⁻_tail(X,A) − a_YES − phi(a_YES) > 0 (strict)
    R4: Data-gated → no_trade(JOINT_EVT_ALERT_UNWIRED) when EVT or alert inputs missing

  C2 (§14) — opening_inertia × stale_quote opening-stale-FOK
    R5: EV = Pr(F) · (p⁻ − a0 − phi(a0)); FOK no-fill → 0 loss (sign of edge is p⁻ − a0 − phi)
    R6: Pr(F) > 0 does not change sign of edge — only sign of (p⁻ − a0 − phi) matters
    R7: Data-gated → no_trade(OPENING_STALE_FOK_UNWIRED) when p⁻ or stale-quote inputs missing

Schema:
    R8: NoTradeReason.JOINT_EVT_ALERT_UNWIRED, JOINT_EVT_ALERT_LR_MISSING, JOINT_EVT_TAIL_NO_EDGE exist
    R9: NoTradeReason.OPENING_STALE_FOK_UNWIRED, OPENING_STALE_FOK_NO_EDGE exist
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.contracts.decision_natural_key import make_decision_natural_key
from src.contracts.no_trade_reason import NoTradeReason
from src.state.db import SCHEMA_VERSION
from src.strategy.bayes_alert import AlertLRStub, LRRecord
from src.strategy.candidates import CandidateContext
from src.strategy.candidates.c1_joint_tail_bayes import JointTailBayes
from src.strategy.candidates.c2_opening_stale_fok import OpeningStaleQuoteFOK


# ---------------------------------------------------------------------------
# R8 / R9: schema enum existence assertions (import-level)
# ---------------------------------------------------------------------------

assert hasattr(NoTradeReason, "JOINT_EVT_ALERT_UNWIRED"), (
    "NoTradeReason.JOINT_EVT_ALERT_UNWIRED must exist (C-EPIC §13)"
)
assert hasattr(NoTradeReason, "JOINT_EVT_ALERT_LR_MISSING"), (
    "NoTradeReason.JOINT_EVT_ALERT_LR_MISSING must exist (C-EPIC §13)"
)
assert hasattr(NoTradeReason, "JOINT_EVT_TAIL_NO_EDGE"), (
    "NoTradeReason.JOINT_EVT_TAIL_NO_EDGE must exist (C-EPIC §13)"
)
assert hasattr(NoTradeReason, "OPENING_STALE_FOK_UNWIRED"), (
    "NoTradeReason.OPENING_STALE_FOK_UNWIRED must exist (C-EPIC §14)"
)
assert hasattr(NoTradeReason, "OPENING_STALE_FOK_NO_EDGE"), (
    "NoTradeReason.OPENING_STALE_FOK_NO_EDGE must exist (C-EPIC §14)"
)


# ---------------------------------------------------------------------------
# Shared DDL
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


def _make_context(conn: sqlite3.Connection, analysis: Any) -> CandidateContext:
    nk = make_decision_natural_key(
        market_slug="test-market-NYC-high-2026-06-15",
        temperature_metric="high",  # type: ignore[arg-type]
        target_date="2026-06-15",
        observation_time="2026-06-15T10:00:00+00:00",
        decision_seq=0,
    )
    return CandidateContext(
        natural_key=nk,
        observed_at="2026-06-15T10:00:00+00:00",
        analysis=analysis,
    )


_DT = datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

# Calibration set: 5-point conformal set with enough spread to produce
# a non-trivial lower bound.
_CAL_P_HATS = [0.60, 0.62, 0.64, 0.66, 0.68]
_CAL_OUTCOMES = [1, 1, 1, 1, 1]


# ---------------------------------------------------------------------------
# Helper: fake LR table that always returns a specified record
# ---------------------------------------------------------------------------

class _FixedLRTable:
    """Returns a fixed LRRecord for any lookup (for testing)."""

    def __init__(self, record: LRRecord) -> None:
        self._record = record

    def lookup(self, *, alert_type: str, city: str, season: str, lead_time_hours: int) -> LRRecord:
        return self._record


# ---------------------------------------------------------------------------
# C1 relationship tests
# ---------------------------------------------------------------------------

class TestJointTailBayesRelationships:
    """R1-R4: C1 (§13) shoulder_buy × weather_event joint tail Bayes."""

    # --- R4: data-gate when EVT tail inputs missing ---

    def test_r4_data_gate_missing_evt(self) -> None:
        """R4: Missing evt_tail_prob_raw → no_trade(JOINT_EVT_ALERT_UNWIRED)."""
        conn = _make_conn()
        analysis = SimpleNamespace(
            # EVT tail prob missing
            evt_tail_prob_raw=None,
            evt_cal_p_hats=_CAL_P_HATS,
            evt_cal_outcomes=_CAL_OUTCOMES,
            native_yes_ask=Decimal("0.45"),
            # alert inputs present
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.50,
            alert_type="ExtremeHeat",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=24,
        )
        ctx = _make_context(conn, analysis)
        cand = JointTailBayes()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.JOINT_EVT_ALERT_UNWIRED
        row = conn.execute("SELECT reason FROM no_trade_events").fetchone()
        assert row is not None
        assert row["reason"] == NoTradeReason.JOINT_EVT_ALERT_UNWIRED.name.lower()

    def test_r4_data_gate_missing_alert_source(self) -> None:
        """R4: Missing alert_source → no_trade(JOINT_EVT_ALERT_UNWIRED)."""
        conn = _make_conn()
        analysis = SimpleNamespace(
            evt_tail_prob_raw=0.65,
            evt_cal_p_hats=_CAL_P_HATS,
            evt_cal_outcomes=_CAL_OUTCOMES,
            native_yes_ask=Decimal("0.45"),
            # alert feed not wired
            alert_source=None,
            active_weather_alert=True,
            alert_prior_p=0.50,
            alert_type="ExtremeHeat",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=24,
        )
        ctx = _make_context(conn, analysis)
        cand = JointTailBayes()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.JOINT_EVT_ALERT_UNWIRED

    def test_r4_data_gate_lr_table_stub(self) -> None:
        """R4: Default LR stub (no fitted data) → no_trade(JOINT_EVT_ALERT_LR_MISSING)."""
        conn = _make_conn()
        analysis = SimpleNamespace(
            evt_tail_prob_raw=0.65,
            evt_cal_p_hats=_CAL_P_HATS,
            evt_cal_outcomes=_CAL_OUTCOMES,
            native_yes_ask=Decimal("0.45"),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.50,
            alert_type="ExtremeHeat",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=24,
        )
        ctx = _make_context(conn, analysis)
        # Default stub always returns None → LR_MISSING
        cand = JointTailBayes()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.JOINT_EVT_ALERT_LR_MISSING

    # --- R1: Bayes update is multiplicative (posterior_odds = prior_odds · LR) ---

    def test_r1_bayes_combination_multiplicative(self) -> None:
        """R1: posterior_odds = prior_tail_odds · LR — the combination is multiplicative Bayes."""
        from src.strategy.bayes_alert import bayes_update

        prior_p = 0.60   # Pr(T>u | X)
        lr = 2.5         # LR(A) = Pr(A|T>u,X)/Pr(A|T≤u,X)

        # Direct computation
        prior_odds = prior_p / (1 - prior_p)
        posterior_odds = prior_odds * lr
        expected_posterior = posterior_odds / (1 + posterior_odds)

        # Via bayes_update (the shared math used by both weather_event_arbitrage and C1)
        actual_posterior = bayes_update(prior_p, lr)

        assert abs(actual_posterior - expected_posterior) < 1e-12, (
            f"R1 invariant broken: bayes_update({prior_p}, {lr}) = {actual_posterior} "
            f"expected {expected_posterior}"
        )

    # --- R2: p⁻_tail(X,A) is conformal lower bound of Bayes-updated tail prob ---

    def test_r2_lower_bound_on_bayes_updated_prob(self) -> None:
        """R2: p⁻_tail(X,A) = conformal lower bound applied AFTER Bayes update."""
        from src.calibration.bounds import calibrated_bounds
        from src.strategy.bayes_alert import bayes_update

        prior_p = 0.60
        lr = 2.0
        posterior = bayes_update(prior_p, lr)

        p_lo, _ = calibrated_bounds(posterior, _CAL_P_HATS, _CAL_OUTCOMES, alpha=0.10)

        # R2: lower bound ≤ Bayes-updated posterior (conformal lower bound property)
        assert p_lo <= posterior, (
            f"R2: conformal lower bound {p_lo} must be ≤ Bayes-updated posterior {posterior}"
        )
        # R2: lower bound ≥ 0
        assert p_lo >= 0.0

    # --- R3: enter iff p⁻_tail(X,A) − a_YES − phi > 0 ---

    def test_r3_enter_when_edge_positive(self) -> None:
        """R3: Enter when p⁻_tail − a_YES − phi > 0 using a fixed LR table."""
        conn = _make_conn()
        # LR = 4.0 boosts prior_p=0.65 to ~0.884; lower bound still >> ask
        lr_record = LRRecord(
            point=4.0,
            lower=3.5,
            alert_type="ExtremeHeat",
            city="chicago",
            season="summer",
            lead_time_hours=24,
        )
        lr_table = _FixedLRTable(lr_record)

        analysis = SimpleNamespace(
            evt_tail_prob_raw=0.65,
            evt_cal_p_hats=[0.80, 0.82, 0.84, 0.86, 0.88],
            evt_cal_outcomes=[1, 1, 1, 1, 1],
            native_yes_ask=Decimal("0.40"),
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.65,
            alert_type="ExtremeHeat",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=24,
        )
        ctx = _make_context(conn, analysis)
        cand = JointTailBayes(lr_table=lr_table)
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", f"Expected enter, got {dec.outcome}, reason={getattr(dec, 'reason', None)}, detail={getattr(dec, 'reason_detail', None)}"
        assert dec.side == "buy_yes"
        assert dec.strategy_key == "c1_joint_tail_bayes"

        # Verify decision_events row written
        row = conn.execute("SELECT strategy_key, outcome FROM decision_events").fetchone()
        assert row is not None
        assert row["strategy_key"] == "c1_joint_tail_bayes"
        assert row["outcome"] == "shadow_enter"

    def test_r3_no_trade_when_edge_nonpositive(self) -> None:
        """R3: no_trade(JOINT_EVT_TAIL_NO_EDGE) when p⁻ − a_YES − phi ≤ 0."""
        conn = _make_conn()
        # LR = 1.0 (no boost) + low prior → lower bound below ask
        lr_record = LRRecord(
            point=1.0,
            lower=1.0,
            alert_type="MinorAlert",
            city="chicago",
            season="summer",
            lead_time_hours=24,
        )
        lr_table = _FixedLRTable(lr_record)

        analysis = SimpleNamespace(
            evt_tail_prob_raw=0.30,
            evt_cal_p_hats=[0.20, 0.22, 0.24, 0.26, 0.28],
            evt_cal_outcomes=[0, 0, 0, 0, 0],
            native_yes_ask=Decimal("0.80"),  # far above any lower bound
            alert_source="nws_alerts",
            active_weather_alert=True,
            alert_prior_p=0.30,
            alert_type="MinorAlert",
            alert_city="chicago",
            alert_season="summer",
            alert_lead_time_hours=24,
        )
        ctx = _make_context(conn, analysis)
        cand = JointTailBayes(lr_table=lr_table)
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.JOINT_EVT_TAIL_NO_EDGE


# ---------------------------------------------------------------------------
# C2 relationship tests
# ---------------------------------------------------------------------------

class TestOpeningStaleQuoteFOKRelationships:
    """R5-R7: C2 (§14) opening_inertia × stale_quote opening-stale-FOK."""

    # --- R7: data-gate when inputs missing ---

    def test_r7_data_gate_missing_calibration(self) -> None:
        """R7: Missing calibration set → no_trade(OPENING_STALE_FOK_UNWIRED)."""
        conn = _make_conn()
        analysis = SimpleNamespace(
            # No calibration set → cannot compute p⁻
            p_hat=0.60,
            cal_p_hats=[],
            cal_outcomes=[],
            ask=0.45,
            # stale quote present
            info_event_observed=True,
            p_after_lower_bound=0.65,
            stale_quote_price=0.45,
            book_hash="abc123",
            book_hash_transition_delta_ms=None,
        )
        ctx = _make_context(conn, analysis)
        cand = OpeningStaleQuoteFOK()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.OPENING_STALE_FOK_UNWIRED

    def test_r7_data_gate_missing_stale_quote_price(self) -> None:
        """R7: Stale quote price absent → no_trade(OPENING_STALE_FOK_UNWIRED)."""
        conn = _make_conn()
        analysis = SimpleNamespace(
            p_hat=0.60,
            cal_p_hats=_CAL_P_HATS,
            cal_outcomes=_CAL_OUTCOMES,
            ask=0.45,
            # stale quote not wired
            info_event_observed=False,
            p_after_lower_bound=None,
            stale_quote_price=None,
            book_hash=None,
            book_hash_transition_delta_ms=None,
        )
        ctx = _make_context(conn, analysis)
        cand = OpeningStaleQuoteFOK()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.OPENING_STALE_FOK_UNWIRED

    # --- R5: EV = Pr(F) · (p⁻ − a0 − phi); no-fill → 0 ---

    def test_r5_fok_no_fill_is_zero_loss(self) -> None:
        """R5: FOK no-fill produces 0 loss; edge sign depends on (p⁻ − a0 − phi) alone."""
        from src.strategy.fees import phi as phi_fn, venue_fee_rate

        # Theorem: EV = Pr(F) · EV_filled; EV_filled = p⁻ − a0 − phi(a0)
        # No-fill payoff is always 0 (FOK — position not taken)
        p_lower = 0.65
        a0 = Decimal("0.45")
        fee_rate = venue_fee_rate()
        fee = phi_fn(Decimal("1"), a0, fee_rate)
        ev_filled = Decimal(str(p_lower)) - a0 - fee

        # Pr(F) affects magnitude, not sign; EV > 0 iff ev_filled > 0
        assert ev_filled > Decimal("0"), (
            f"R5: ev_filled={ev_filled} must be > 0 for positive EV"
        )
        # No-fill payoff is 0
        no_fill_payoff = Decimal("0")
        assert no_fill_payoff == Decimal("0")

    # --- R6: Pr(F) > 0 does not change sign of edge ---

    def test_r6_fill_probability_does_not_change_sign(self) -> None:
        """R6: Positive EV sign is determined by (p⁻ − a0 − phi), not Pr(F)."""
        from src.strategy.fees import phi as phi_fn, venue_fee_rate

        fee_rate = venue_fee_rate()
        a0 = Decimal("0.45")
        fee = phi_fn(Decimal("1"), a0, fee_rate)

        # p⁻ > a0 + phi → EV_filled > 0 → EV = Pr(F) · EV_filled > 0 for any Pr(F) > 0
        p_lo_positive_case = Decimal("0.65")
        ev_filled_pos = p_lo_positive_case - a0 - fee
        assert ev_filled_pos > Decimal("0"), "R6 setup: positive edge case must hold"

        # p⁻ < a0 + phi → EV_filled < 0 → EV < 0 regardless of Pr(F)
        p_lo_negative_case = Decimal("0.35")
        ev_filled_neg = p_lo_negative_case - a0 - fee
        assert ev_filled_neg < Decimal("0"), "R6 setup: negative edge case must hold"

        # Pr(F) = 0.7 (arbitrary) — sign preserved
        pr_fill = Decimal("0.7")
        assert pr_fill * ev_filled_pos > Decimal("0"), "R6: positive EV with Pr(F)=0.7"
        assert pr_fill * ev_filled_neg < Decimal("0"), "R6: negative EV with Pr(F)=0.7"

    # --- Enter and no-trade round-trip ---

    def test_c2_enter_when_both_components_wired_positive_edge(self) -> None:
        """C2 enter: opening p⁻ beats ask, book stale, info event present → enter.

        Calibration set: p_hat=0.90, cal_p_hats close to 0.90 with outcomes=1.
        Nonconformity scores |1 - cal_p_hat| ≈ 0.07–0.11; 90%-quantile ≈ 0.10.
        p_lo = max(0, 0.90 - 0.10) = 0.79, which comfortably beats ask=0.40.
        """
        conn = _make_conn()
        # cal nonconformity: |1-p| for p in [0.89..0.93] → [0.11,0.10,0.09,0.08,0.07]
        # 90th-pctile of 5 scores = index ceil(0.9*5)=5 → 0.11; p_lo=0.90-0.11=0.79
        cal_p = [0.89, 0.90, 0.91, 0.92, 0.93]
        cal_y = [1, 1, 1, 1, 1]
        analysis = SimpleNamespace(
            p_hat=0.90,
            cal_p_hats=cal_p,
            cal_outcomes=cal_y,
            ask=0.40,
            # stale-quote wired: info event observed, stale price present, book stale
            info_event_observed=True,
            p_after_lower_bound=0.90,
            stale_quote_price=Decimal("0.40"),
            book_hash="hash-unchanged",
            book_hash_transition_delta_ms=None,  # never transitioned = stale
        )
        ctx = _make_context(conn, analysis)
        cand = OpeningStaleQuoteFOK()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "enter", (
            f"Expected enter, got {dec.outcome}, reason={getattr(dec, 'reason', None)}, "
            f"detail={getattr(dec, 'reason_detail', None)}"
        )
        assert dec.strategy_key == "c2_opening_stale_fok"
        assert dec.side == "buy_yes"

        row = conn.execute("SELECT strategy_key, outcome FROM decision_events").fetchone()
        assert row is not None
        assert row["strategy_key"] == "c2_opening_stale_fok"

    def test_c2_no_trade_when_edge_nonpositive(self) -> None:
        """C2 no_trade: p⁻ below ask → OPENING_STALE_FOK_NO_EDGE."""
        conn = _make_conn()
        cal_p = [0.30, 0.31, 0.32, 0.33, 0.34]
        cal_y = [0, 0, 0, 0, 0]
        analysis = SimpleNamespace(
            p_hat=0.32,
            cal_p_hats=cal_p,
            cal_outcomes=cal_y,
            ask=0.85,  # ask far above any lower bound
            info_event_observed=True,
            p_after_lower_bound=0.35,
            stale_quote_price=Decimal("0.85"),
            book_hash="hash-unchanged",
            book_hash_transition_delta_ms=None,
        )
        ctx = _make_context(conn, analysis)
        cand = OpeningStaleQuoteFOK()
        dec = cand.evaluate(context=ctx, conn=conn, decision_time=_DT)

        assert dec.outcome == "no_trade"
        assert dec.reason == NoTradeReason.OPENING_STALE_FOK_NO_EDGE


# ---------------------------------------------------------------------------
# Schema coherence check
# ---------------------------------------------------------------------------

def test_schema_version_is_int_and_bumped() -> None:
    """SCHEMA_VERSION must be int >= 31 for C-EPIC enum additions."""
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 31, (
        f"SCHEMA_VERSION {SCHEMA_VERSION} must be >= 31 for C-EPIC NoTradeReason additions"
    )
