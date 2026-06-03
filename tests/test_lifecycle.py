# Created: 2025-10-01
# Lifecycle: created=2025-10-01; last_reviewed=2026-05-08; last_reused=2026-05-08
# Purpose: Exit-trigger + harvester lifecycle regression tests — covers
#          position exit detection, harvest_settlement default-HIGH routing
#          through calibration_pairs after C5 (2026-04-24), and p_raw
#          skip behavior when ensemble signal is absent.
# Reuse: Referenced by regression suite; last touched 2026-05-08 for Wave28
#        (HIGH→v2 route). Apply v2 schema in test fixtures when asserting
#        post-harvest pair rows.
# Last reused/audited: 2026-05-18
# Authority basis: docs/operations/task_2026-05-08_object_invariance_wave28/PLAN.md
"""Tests for exit triggers and harvester."""

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.engine import monitor_refresh
# Wave 3 (2026-06-02): evaluate_exit_triggers deleted (dead twin). TestExitTriggers
#   repointed to Position.evaluate_exit (the one live path).
from src.execution.harvester import harvest_settlement
from src.state.portfolio import Position, PortfolioState, ExitContext
from src.state.db import get_connection, init_schema
from src.config import City


def _call_exit(
    pos: Position,
    fresh_prob: float,
    current_market_price: float,
    *,
    hours_to_settlement: float = 72.0,
    best_bid: float | None = None,
    divergence_score: float = 0.0,
    market_velocity_1h: float = 0.0,
    whale_toxicity: bool | None = None,
    market_vig: float | None = None,
):
    """Thin wrapper: call the one live exit path."""
    ctx = ExitContext(
        fresh_prob=fresh_prob,
        fresh_prob_is_fresh=True,
        current_market_price=current_market_price,
        current_market_price_is_fresh=True,
        best_bid=best_bid if best_bid is not None else current_market_price,
        hours_to_settlement=hours_to_settlement,
        position_state="active",
        market_velocity_1h=market_velocity_1h,
        divergence_score=divergence_score,
        whale_toxicity=whale_toxicity,
        market_vig=market_vig,
    )
    return pos.evaluate_exit(ctx)


NYC = City(
    name="NYC", lat=40.7772, lon=-73.8726,
    timezone="America/New_York", cluster="US-Northeast",
    settlement_unit="F", wu_station="KLGA",
)


def _make_position(**kwargs) -> Position:
    defaults = dict(
        trade_id="t1", market_id="m1", city="NYC",
        cluster="US-Northeast", target_date="2026-01-15",
        bin_label="39-40", direction="buy_yes",
        size_usd=10.0, entry_price=0.40, p_posterior=0.60,
        edge=0.20, entered_at="2026-01-12T00:00:00Z",
    )
    defaults.update(kwargs)
    return Position(**defaults)


def test_chain_reconciliation_phantom_void_persists_canonical_projection(tmp_path):
    """Relationship: Chain > Portfolio voids must persist to canonical DB truth."""

    from src.state.chain_reconciliation import ChainPosition, reconcile
    from src.state.db import query_position_events

    conn = get_connection(tmp_path / "chain_phantom_void.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="phantom-db-1",
        state="holding",
        chain_state="synced",
        token_id="tok-phantom",
        no_token_id="tok-phantom-no",
        shares=6.0,
        cost_basis_usd=1.86,
        size_usd=1.86,
        entry_price=0.31,
        entered_at="2026-05-18T12:00:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
    )
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric
        ) VALUES (
            ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-phantom",
            "ens_member_counting",
            pos.strategy_key,
            "opening_inertia",
            "opening_hunt",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-phantom",
            "order-phantom",
            "filled",
            pos.entered_at,
            "high",
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    conn.commit()

    row = conn.execute(
        "SELECT phase, chain_state FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    events = query_position_events(conn, pos.trade_id)
    conn.close()

    assert stats["voided"] == 1
    assert row["phase"] == "voided"
    assert [event["event_type"] for event in events] == ["ADMIN_VOIDED"]
    assert events[0]["source"] == "src.state.chain_reconciliation"
    assert events[0]["details"]["reason"] == "PHANTOM_NOT_ON_CHAIN"


def test_chain_reconciliation_phantom_void_allows_legacy_unknown_phase_before(tmp_path):
    """Relationship: legacy runtime states can still be canonically voided."""

    from src.state.chain_reconciliation import ChainPosition, reconcile

    conn = get_connection(tmp_path / "chain_phantom_void_unknown_phase.db")
    init_schema(conn)

    pos = _make_position(
        trade_id="phantom-unknown-phase",
        state="holding",
        chain_state="synced",
        token_id="tok-legacy-phantom",
        no_token_id="tok-legacy-phantom-no",
        shares=2.0,
        cost_basis_usd=0.6,
        size_usd=0.6,
        entry_price=0.3,
        entered_at="2026-05-18T12:00:00+00:00",
        strategy_key="opening_inertia",
        strategy="opening_inertia",
        env="live",
        unit="C",
    )
    pos.state = "quarantine_size_mismatch"
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, market_id, city, cluster,
            target_date, bin_label, direction, unit, size_usd, shares,
            cost_basis_usd, entry_price, p_posterior, decision_snapshot_id,
            entry_method, strategy_key, edge_source, discovery_mode,
            chain_state, token_id, no_token_id, condition_id, order_id,
            order_status, updated_at, temperature_metric
        ) VALUES (
            ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            pos.trade_id,
            pos.trade_id,
            pos.market_id,
            pos.city,
            pos.cluster,
            pos.target_date,
            pos.bin_label,
            pos.direction,
            pos.unit,
            pos.size_usd,
            pos.shares,
            pos.cost_basis_usd,
            pos.entry_price,
            pos.p_posterior,
            "snap-legacy-phantom",
            "ens_member_counting",
            pos.strategy_key,
            "opening_inertia",
            "opening_hunt",
            pos.chain_state,
            pos.token_id,
            pos.no_token_id,
            "cond-legacy-phantom",
            "order-legacy-phantom",
            "filled",
            pos.entered_at,
            "high",
        ),
    )
    conn.commit()

    portfolio = PortfolioState(positions=[pos])
    stats = reconcile(
        portfolio,
        [ChainPosition(token_id="tok-other", size=1.0, avg_price=0.5, condition_id="cond-other")],
        conn=conn,
    )
    row = conn.execute(
        "SELECT phase FROM position_current WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    event = conn.execute(
        "SELECT event_type, phase_before, phase_after FROM position_events WHERE position_id = ?",
        (pos.trade_id,),
    ).fetchone()
    conn.close()

    assert stats["voided"] == 1
    assert row["phase"] == "voided"
    assert dict(event) == {
        "event_type": "ADMIN_VOIDED",
        "phase_before": None,
        "phase_after": "voided",
    }


class TestExitTriggers:
    """Wave 3 (2026-06-02): all tests repointed from evaluate_exit_triggers
    (dead twin, deleted) to Position.evaluate_exit (the one live path).
    entry_price=0.40, p_posterior=0.60; use hours_to_settlement=72.0 unless
    testing near-settlement behavior (near_settlement_hours()=48).
    """

    def test_settlement_imminent(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, hours_to_settlement=0.5)
        assert decision.should_exit
        assert decision.trigger == "SETTLEMENT_IMMINENT"

    def test_whale_toxicity(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, whale_toxicity=True)
        assert decision.should_exit
        assert decision.trigger == "WHALE_TOXICITY"

    def test_soft_divergence_requires_adverse_velocity_confirmation(self):
        """Soft divergence (0.20) without adverse velocity does not panic."""
        pos = _make_position()
        decision = _call_exit(
            pos, 0.20, 0.40, divergence_score=0.20, market_velocity_1h=0.0,
        )
        assert not decision.should_exit or decision.trigger != "MODEL_DIVERGENCE_PANIC"

    def test_hard_divergence_panics_without_velocity_confirmation(self):
        """Hard divergence (>= 0.30) panics regardless of velocity."""
        pos = _make_position()
        decision = _call_exit(
            pos, 0.20, 0.40, divergence_score=0.31, market_velocity_1h=0.0,
        )
        assert decision.should_exit
        assert decision.trigger == "MODEL_DIVERGENCE_PANIC"

    def test_edge_reversal_needs_two_confirmations(self):
        """CLAUDE.md §4.2: EDGE_REVERSAL needs 2 confirmations, 1st doesn't trigger.

        buy_yes: fresh_prob=0.30 < market=0.40 → forward_edge=-0.10 (negative).
        CI_OVERLAP_HOLD gate: entry_ci_width=0 (default), so width/2=0 →
        ci_lo=ci_hi=entry_price → only fires when fresh_prob==entry_price exactly.
        """
        pos = _make_position()
        # First check: edge reversed but only 1 confirmation
        decision = _call_exit(pos, 0.30, 0.40)
        assert not decision.should_exit  # Should NOT trigger on first reversal

        # Second check: confirmed reversal
        decision = _call_exit(pos, 0.30, 0.40)
        assert decision.should_exit
        assert decision.trigger == "EDGE_REVERSAL"

    def test_buy_yes_ev_gate_hold_when_bid_below_posterior(self):
        """When best_bid < p_posterior (hold EV > sell EV), exit is blocked.

        Wave 3: direct observable-behavior test. No monkeypatching needed.
        Position has neg_edge_count=1 (pre-set); next negative cycle would
        normally exit but EV gate blocks it (sell at 0.10 < hold value 0.60).
        """
        pos = _make_position(p_posterior=0.60, entry_price=0.50)
        pos.neg_edge_count = 1
        # fresh_prob=0.10, market=0.55 → edge=-0.45 (deeply negative, would exit)
        # best_bid=0.10 << p_posterior=0.60 → hold EV > sell EV → HOLD
        decision = _call_exit(pos, 0.10, 0.55, best_bid=0.10)
        assert not decision.should_exit  # EV gate blocks

    def test_buy_yes_ev_gate_exits_when_bid_above_posterior(self):
        """When best_bid >= p_posterior (sell EV >= hold EV), exit fires.

        Wave 3: complement of EV-gate-hold test. Fresh posterior has degraded
        (0.10) but market is generous (0.65 bid > posterior). Rational to exit.
        """
        pos = _make_position(p_posterior=0.60, entry_price=0.50)
        pos.neg_edge_count = 1
        # best_bid=0.65 > p_posterior=0.10 → sell value exceeds hold EV → EXIT
        decision = _call_exit(pos, 0.10, 0.65, best_bid=0.65)
        assert decision.should_exit
        assert decision.trigger == "EDGE_REVERSAL"

    def test_stale_probability_authority_blocks_edge_exit(self):
        """Stale fresh_prob (fresh_prob_is_fresh=False) → EVIDENCE_UNAVAILABLE, no exit.

        Wave 3: ExitContext.fresh_prob_is_fresh=False triggers EVIDENCE_UNAVAILABLE hold.
        """
        pos = _make_position(entry_price=0.12, p_posterior=0.90)
        pos.neg_edge_count = 1
        ctx = ExitContext(
            fresh_prob=0.05,
            fresh_prob_is_fresh=False,  # stale — not authority
            current_market_price=0.50,
            current_market_price_is_fresh=True,
            best_bid=0.49,
            hours_to_settlement=72.0,
            position_state="active",
            market_velocity_1h=0.0,
            divergence_score=0.0,
        )
        decision = pos.evaluate_exit(ctx)
        assert not decision.should_exit

    def test_flash_crash_panic_fires_with_adverse_velocity(self):
        """Adverse velocity (-0.20/h) triggers FLASH_CRASH_PANIC.

        Wave 3: live path requires probability authority (fresh_prob_is_fresh=True).
        Flash crash fires AFTER the authority check: it needs consecutive cycles of
        velocity <= flash_crash_velocity(). Set flash_crash_count=2 via two calls.
        """
        pos = _make_position()
        # Two cycles of adverse velocity accumulate flash_crash_count
        _call_exit(pos, 0.60, 0.40, market_velocity_1h=-0.20)
        decision = _call_exit(pos, 0.60, 0.40, market_velocity_1h=-0.20)
        # After 2 consecutive flash-crash-velocity cycles, FLASH_CRASH_PANIC fires
        assert decision.should_exit
        assert decision.trigger == "FLASH_CRASH_PANIC"

    def test_vig_extreme_fires_with_probability_authority(self):
        """Market-vig extreme (>1.08) triggers VIG_EXTREME exit."""
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, market_vig=1.10)
        assert decision.should_exit
        assert decision.trigger == "VIG_EXTREME"

    def test_edge_reversal_resets_on_recovery(self):
        """If edge recovers between checks, counter resets."""
        pos = _make_position()
        _call_exit(pos, 0.30, 0.40)  # neg → count=1
        _call_exit(pos, 0.60, 0.40)  # pos → count=0
        decision = _call_exit(pos, 0.30, 0.40)  # neg → count=1 again
        assert not decision.should_exit  # Only 1st confirmation after reset

    def test_no_exit_when_edge_healthy(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40)
        assert not decision.should_exit

    def test_vig_extreme(self):
        pos = _make_position()
        decision = _call_exit(pos, 0.60, 0.40, market_vig=1.10)
        assert decision.should_exit
        assert decision.trigger == "VIG_EXTREME"


class TestMonitorWhaleToxicity:
    class _BookClob:
        def __init__(self, books):
            self.books = books

        def get_best_bid_ask(self, token_id):
            return self.books[token_id]

    @staticmethod
    def _siblings():
        return [
            {"market_id": "m-below", "range_low": 37, "range_high": 38, "token_id": "yes-below"},
            {"market_id": "m1", "range_low": 39, "range_high": 40, "token_id": "yes-held"},
            {"market_id": "m-above", "range_low": 41, "range_high": 42, "token_id": "yes-above"},
        ]

    @staticmethod
    def _conn_with_prior(tmp_path, token_id: str, price: float, now: datetime):
        conn = get_connection(tmp_path / "whale.db")
        init_schema(conn)
        conn.execute(
            """
            INSERT INTO token_price_log
                (token_id, price, timestamp)
            VALUES (?, ?, ?)
            """,
            (token_id, price, (now - timedelta(hours=2)).isoformat()),
        )
        conn.commit()
        return conn

    def test_orderbook_adjacent_pressure_flags_buy_yes_whale_toxicity(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.40, now)
        clob = self._BookClob({
            "yes-above": (0.50, 0.52, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is True
        assert "whale_toxicity_available:adjacent_orderbook_pressure" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_returns_false_when_clear(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.42, now)
        clob = self._BookClob({
            "yes-above": (0.44, 0.46, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "VERIFIED")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is False
        assert "whale_toxicity_available:clear" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_stays_unknown_without_verified_scan(self, monkeypatch, tmp_path):
        now = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)
        pos = _make_position(market_id="m1", token_id="yes-held", size_usd=10.0)
        conn = self._conn_with_prior(tmp_path, "yes-above", 0.40, now)
        clob = self._BookClob({
            "yes-above": (0.60, 0.62, 100.0, 10.0),
        })
        monkeypatch.setattr(monitor_refresh, "get_sibling_outcomes", lambda market_id: self._siblings())
        monkeypatch.setattr(monitor_refresh, "get_last_scan_authority", lambda: "STALE")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            conn,
            clob,
            pos,
            held_best_bid=0.40,
            held_best_ask=0.43,
            now=now,
        )

        assert result is None
        assert "whale_toxicity_unavailable:market_scan_not_verified" in pos.applied_validations
        conn.close()

    def test_orderbook_adjacent_pressure_is_not_applicable_to_buy_no(self):
        pos = _make_position(direction="buy_no", no_token_id="no-held")

        result = monitor_refresh._detect_whale_toxicity_from_orderbook(
            None,
            None,
            pos,
            held_best_bid=None,
            held_best_ask=None,
        )

        assert result is False
        assert "whale_toxicity_not_applicable:buy_no" in pos.applied_validations


class TestHarvester:
    def test_harvest_creates_pairs(self, tmp_path):
        """Post-C5 (2026-04-24): harvest_settlement default-HIGH path now
        writes to calibration_pairs (previously legacy calibration_pairs).
        """
        from src.state.schema.v2_schema import apply_canonical_schema

        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)
        apply_canonical_schema(conn)

        bin_labels = ["32 or below", "33-34", "35-36", "37-38", "39-40",
                      "41-42", "43-44", "45-46", "47-48", "49-50", "51 or higher"]
        p_raw = [0.02, 0.05, 0.10, 0.20, 0.30, 0.20, 0.08, 0.03, 0.01, 0.005, 0.005]

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=bin_labels,
            p_raw_vector=p_raw,
            lead_days=3.0,
            forecast_issue_time="2026-01-12T00:00:00Z",
            forecast_model_id="test_lifecycle_v1",
        )
        conn.commit()

        assert count == 11

        # Post-C5: HIGH default routes to calibration_pairs.
        rows = conn.execute(
            "SELECT outcome, COUNT(*) FROM calibration_pairs GROUP BY outcome"
        ).fetchall()
        outcome_counts = {r[0]: r[1] for r in rows}
        assert outcome_counts[1] == 1
        assert outcome_counts[0] == 10

        conn.close()

    def test_harvest_skips_missing_p_raw(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        init_schema(conn)

        count = harvest_settlement(
            conn, NYC, "2026-01-15",
            winning_bin_label="39-40",
            bin_labels=["39-40", "41-42"],
            p_raw_vector=None,
        )

        assert count == 0  # No P_raw → no pairs created
        conn.close()
