# Created: 2026-07-25
# Last reused or audited: 2026-07-25
# Authority basis: fix(state) settlement_price binary payout + law-identity
#   stamps truth-repair packet.
"""Antibodies for the 2026-07-25 truth-repair packet.

Two independent bugs, two independent antibody groups:

1. settlement_price corruption: src.state.chain_mirror_reconciler used to
   write a raw settlement_outcomes temperature into
   position_current.settlement_price instead of the binary [0.0, 1.0]
   payout. Fixed to always grade settlement_price from position_won,
   independent of exit_price. See src.engine.lifecycle_events:315-318 for
   the documented settlement_price == exit_price-on-settled-rows invariant
   this restores.

2. Law-identity stamp (decision_law_id="predicted_bin_ev_v1",
   position_origin="zeus_decision") was only ever written at ONE of three
   Position/position-projection constructors
   (src.events.edli_position_bridge._build_bridge_position). The other two
   -- src.execution.command_recovery._entry_recovery_position and
   src.execution.exchange_reconcile._ensure_entry_fill_position_event's
   SimpleNamespace -- left it NULL. Fixed to stamp both.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.chain_mirror_reconciler import (
    CLOSED_WORTHLESS,
    MirrorFinding,
    _apply_settlement_finding,
)
from src.state.db import init_schema, init_schema_trade_only


# ---------------------------------------------------------------------------
# Group 1: settlement_price binary payout
# ---------------------------------------------------------------------------


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    yield conn
    conn.close()


def _insert_active_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    direction: str = "buy_yes",
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, city, target_date, bin_label,
            direction, unit, shares, chain_shares, cost_basis_usd, entry_price,
            strategy_key, chain_state, token_id, no_token_id, condition_id,
            updated_at, temperature_metric, p_posterior
        ) VALUES (
            ?, 'active', ?, 'Tokyo', '2026-07-25', '26-27C',
            ?, 'C', 10.0, 10.0, 4.0, 0.40,
            'test_strategy', 'synced', ?, ?, 'cond-1',
            '2026-07-25T00:00:00+00:00', 'high', 0.6
        )
        """,
        (
            position_id, position_id, direction,
            f"tok-{position_id}", f"tok-{position_id}-no",
        ),
    )
    conn.commit()


class TestSettlementPriceIsAlwaysBinaryPayout:
    def test_won_settlement_price_is_one_not_raw_temperature(self, trades_conn):
        """Reproduces the corruption signature: a raw settlement_outcomes
        temperature (93.0) arriving on finding.details["settlement_value"]
        must never land in settlement_price -- it must be graded to the
        binary payout instead.
        """
        _insert_active_position(trades_conn, position_id="pos-settle-won", direction="buy_yes")
        finding = MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id="pos-settle-won",
            asset="tok-pos-settle-won",
            writes=True,
            details={"won": True, "settlement_value": 93.0, "market_slug": "tokyo-2026-07-25"},
        )
        _apply_settlement_finding(
            trades_conn, finding, now=datetime(2026, 7, 26, tzinfo=timezone.utc)
        )
        row = trades_conn.execute(
            "SELECT settlement_price, exit_price FROM position_current "
            "WHERE position_id = 'pos-settle-won'"
        ).fetchone()
        assert row["settlement_price"] == pytest.approx(1.0), (
            "settlement_price must be the binary payout 1.0, not the raw "
            f"temperature 93.0; got {row['settlement_price']}"
        )
        assert 0.0 <= row["settlement_price"] <= 1.0
        # Documented invariant (lifecycle_events.py:315-318): settlement_price
        # equals exit_price on settled rows when no real fill was booked.
        assert row["settlement_price"] == row["exit_price"]

    def test_lost_settlement_price_is_zero_not_raw_temperature(self, trades_conn):
        _insert_active_position(trades_conn, position_id="pos-settle-lost", direction="buy_no")
        finding = MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id="pos-settle-lost",
            asset="tok-pos-settle-lost",
            writes=True,
            # buy_no + market_bin_won=True (won=False from this writer's own
            # "did OUR position win" semantics) -- exercise a negative raw
            # temperature to prove no sign/magnitude of settlement_value ever
            # survives into settlement_price.
            details={"won": False, "settlement_value": -12.5, "market_slug": "tokyo-2026-07-25"},
        )
        _apply_settlement_finding(
            trades_conn, finding, now=datetime(2026, 7, 26, tzinfo=timezone.utc)
        )
        row = trades_conn.execute(
            "SELECT settlement_price FROM position_current WHERE position_id = 'pos-settle-lost'"
        ).fetchone()
        assert row["settlement_price"] == pytest.approx(0.0)

    def test_settlement_price_independent_of_booked_exit_price(self, trades_conn):
        """was_economically_closed branch: exit_price preserves the REAL
        booked fill price, but settlement_price must still be the binary
        market payout -- the two axes must diverge here, not collapse.
        """
        trades_conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, city, target_date, bin_label,
                direction, unit, shares, chain_shares, cost_basis_usd, entry_price,
                strategy_key, chain_state, token_id, no_token_id, condition_id,
                updated_at, temperature_metric, realized_pnl_usd, exit_price,
                exit_reason, fill_authority, p_posterior
            ) VALUES (
                'pos-eco-closed', 'economically_closed', 'pos-eco-closed', 'Tokyo',
                '2026-07-25', '26-27C', 'buy_no', 'C', 13.83, 0.0, 8.34, 0.603,
                'test_strategy', 'synced', 'tok-eco', 'tok-eco-no', 'cond-1',
                '2026-07-25T00:00:00+00:00', 'high', -5.50, 0.205,
                'FAMILY_DIRECT_SELL_DOMINATES_HOLD', 'venue_confirmed_full', 0.6
            )
            """
        )
        trades_conn.commit()
        finding = MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id="pos-eco-closed",
            asset="tok-eco",
            writes=True,
            details={"won": True, "settlement_value": 26.0},
        )
        _apply_settlement_finding(
            trades_conn, finding, now=datetime(2026, 7, 26, tzinfo=timezone.utc)
        )
        row = trades_conn.execute(
            "SELECT settlement_price, exit_price, realized_pnl_usd FROM position_current "
            "WHERE position_id = 'pos-eco-closed'"
        ).fetchone()
        # exit_price / realized_pnl_usd preserve the REAL booked fill (Bug C guard).
        assert row["exit_price"] == pytest.approx(0.205)
        assert row["realized_pnl_usd"] == pytest.approx(-5.50)
        # settlement_price is still the binary market payout (won=True -> 1.0),
        # NOT copied from the preserved exit_price.
        assert row["settlement_price"] == pytest.approx(1.0)

# ---------------------------------------------------------------------------
# Group 2: law-identity stamp at command_recovery's entry constructor
# ---------------------------------------------------------------------------


def _valid_recovery_candidate(**overrides) -> dict:
    base = {
        "position_id": "pos-law-1",
        "command_id": "cmd-law-1",
        "venue_order_id": "ord-law-1",
        "token_id": "tok-yes-1",
        "env_condition_id": "cond-1",
        "env_yes_token_id": "tok-yes-1",
        "env_no_token_id": "tok-no-1",
        "fill_filled_size": "10",
        "fill_price": "0.5",
        "fill_observed_at": "2026-07-25T00:00:00+00:00",
        "size": "10",
        "price": "0.5",
        "created_at": "2026-07-25T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _valid_recovery_trade_case(**overrides) -> dict:
    base = {
        "trade_id": "pos-law-1",
        "token_id": "tok-yes-1",
        "city": "Tokyo",
        "target_date": "2026-07-25",
        "bin_label": "26-27C",
        "direction": "buy_yes",
        "strategy_key": "opening_inertia",
        "unit": "C",
        "temperature_metric": "high",
        "p_posterior": 0.6,
        "entry_ci_width": 0.1,
    }
    base.update(overrides)
    return base


class TestCommandRecoveryEntryPositionStampsLawIdentity:
    def test_filled_entry_recovery_position_stamps_law_identity(self):
        from src.execution.command_recovery import _entry_recovery_position

        position = _entry_recovery_position(
            _valid_recovery_candidate(),
            _valid_recovery_trade_case(),
            decision_log_id=None,
            filled=True,
        )
        assert position.decision_law_id == "predicted_bin_ev_v1"
        assert position.position_origin == "zeus_decision"

    def test_live_pending_entry_recovery_position_stamps_law_identity(self):
        """Non-filled (pending/live) recovery branch: filled=False still
        reaches the same stamped SimpleNamespace construction."""
        from src.execution.command_recovery import _entry_recovery_position

        position = _entry_recovery_position(
            _valid_recovery_candidate(fill_filled_size=None, fill_price=None),
            _valid_recovery_trade_case(),
            decision_log_id=None,
            filled=False,
        )
        assert position.decision_law_id == "predicted_bin_ev_v1"
        assert position.position_origin == "zeus_decision"

    def test_stamped_projection_survives_build_position_current_projection(self):
        """The stamp must actually reach the durable projection payload
        (lifecycle_events.build_position_current_projection reads
        getattr(position, "decision_law_id"/"position_origin", "")), not
        just live on the SimpleNamespace attribute.
        """
        from src.engine.lifecycle_events import build_position_current_projection
        from src.execution.command_recovery import _entry_recovery_position

        position = _entry_recovery_position(
            _valid_recovery_candidate(),
            _valid_recovery_trade_case(),
            decision_log_id=None,
            filled=True,
        )
        projection = build_position_current_projection(position)
        assert projection["decision_law_id"] == "predicted_bin_ev_v1"
        assert projection["position_origin"] == "zeus_decision"


# ---------------------------------------------------------------------------
# Group 3: log_execution_fact / log_opportunity_fact decision_law_id wiring
# ---------------------------------------------------------------------------


class TestExecutionAndOpportunityFactCarryLawIdentity:
    def test_log_execution_fact_writes_decision_law_id(self, tmp_path):
        from src.state.db import get_connection, log_execution_fact

        conn = get_connection(tmp_path / "test.db")
        init_schema(conn)
        init_schema_trade_only(conn)

        log_execution_fact(
            conn,
            intent_id="intent-law-1",
            position_id="pos-law-1",
            order_role="entry",
            terminal_exec_status="filled",
            decision_law_id="predicted_bin_ev_v1",
        )
        row = conn.execute(
            "SELECT decision_law_id FROM execution_fact WHERE intent_id = 'intent-law-1'"
        ).fetchone()
        assert row["decision_law_id"] == "predicted_bin_ev_v1"
        conn.close()

    def test_log_execution_fact_decision_law_id_is_write_once(self, tmp_path):
        """COALESCE write-once: a later re-observation of the same intent_id
        must never NULL an already-stamped fact."""
        from src.state.db import get_connection, log_execution_fact

        conn = get_connection(tmp_path / "test.db")
        init_schema(conn)
        init_schema_trade_only(conn)

        log_execution_fact(
            conn,
            intent_id="intent-law-2",
            position_id="pos-law-2",
            order_role="entry",
            terminal_exec_status="pending_fill_authority",
            decision_law_id="predicted_bin_ev_v1",
        )
        # A later repair pass omits decision_law_id (None) -- must not NULL it.
        log_execution_fact(
            conn,
            intent_id="intent-law-2",
            position_id="pos-law-2",
            order_role="entry",
            terminal_exec_status="filled",
            fill_price=0.5,
            shares=10.0,
        )
        row = conn.execute(
            "SELECT decision_law_id, terminal_exec_status FROM execution_fact "
            "WHERE intent_id = 'intent-law-2'"
        ).fetchone()
        assert row["decision_law_id"] == "predicted_bin_ev_v1"
        assert row["terminal_exec_status"] == "filled"
        conn.close()

    def test_log_opportunity_fact_writes_decision_law_id(self, tmp_path):
        import types

        from src.state.db import get_connection, log_opportunity_fact

        # INV-37: log_opportunity_fact verifies conn path == zeus_trades.db.
        conn = get_connection(tmp_path / "zeus_trades.db")
        init_schema_trade_only(conn)

        candidate = types.SimpleNamespace(
            city=types.SimpleNamespace(name="Tokyo"),
            target_date="2026-07-25",
            event_id="evt-law-1",
            slug="tokyo-jul-25",
            discovery_mode="opening_hunt",
        )
        edge = types.SimpleNamespace(
            bin=types.SimpleNamespace(label="26-27C"),
            direction="buy_yes",
            p_model=0.6,
            p_market=0.5,
            edge=0.1,
            ci_lower=0.05,
            ci_upper=0.15,
        )
        decision = types.SimpleNamespace(
            decision_id="dec-law-1",
            edge=edge,
            strategy_key="opening_inertia",
            selected_method="ens_member_counting",
            decision_snapshot_id="",
            availability_status="ok",
            p_raw=[0.55],
            p_cal=[0.6],
            p_market=[0.5],
            bin_labels=["26-27C"],
            alpha=0.4,
        )
        result = log_opportunity_fact(
            conn,
            candidate=candidate,
            decision=decision,
            should_trade=True,
            rejection_stage="",
            rejection_reasons=None,
            recorded_at="2026-07-25T00:00:00Z",
        )
        assert result["status"] == "written"
        row = conn.execute(
            "SELECT decision_law_id FROM opportunity_fact WHERE decision_id = 'dec-law-1'"
        ).fetchone()
        assert row["decision_law_id"] == "predicted_bin_ev_v1"
        conn.close()


# ---------------------------------------------------------------------------
# Group 4: scripts/backfill_settlement_price_2026_07_25.py grading fixture
# ---------------------------------------------------------------------------


class TestBackfillSettlementPriceGrading:
    def test_recompute_settlement_price_won(self):
        from scripts.backfill_settlement_price_2026_07_25 import (
            recompute_settlement_price,
        )

        assert recompute_settlement_price(
            bin_label="70-71°F", direction="buy_yes", winning_bin="70-71°F"
        ) == pytest.approx(1.0)

    def test_recompute_settlement_price_lost(self):
        from scripts.backfill_settlement_price_2026_07_25 import (
            recompute_settlement_price,
        )

        assert recompute_settlement_price(
            bin_label="70-71°F", direction="buy_no", winning_bin="70-71°F"
        ) == pytest.approx(0.0)

    def test_recompute_settlement_price_ungradeable_returns_none(self):
        from scripts.backfill_settlement_price_2026_07_25 import (
            recompute_settlement_price,
        )

        assert recompute_settlement_price(
            bin_label="not-a-real-bin", direction="buy_yes", winning_bin="70-71°F"
        ) is None
