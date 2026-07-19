# Created: 2026-07-19
# Last reused or audited: 2026-07-19
# Authority basis: docs/evidence/capital_efficiency_2026_07_19/pnl_attribution.md
#   §1 -- live DB evidence, 119/264 (45%) settled positions with realized_pnl_usd
#   NULL/wrong; confirmed mechanism traced via position_events payloads.
"""Bug C antibody: src.state.chain_mirror_reconciler._apply_settlement_finding
must NOT clobber a position's already-booked realized_pnl_usd/exit_price when
it fires on a position that already exited via a REAL fill
(phase_before == 'economically_closed').

src.state.portfolio.compute_settlement_close has a `was_economically_closed`
guard (see tests/state/test_settlement_preserves_booked_close_economics.py,
"Bug B") that refuses to recompute pnl/exit_price for a position already
economically closed by a real exit fill -- it trusts the already-booked
values. `_apply_settlement_finding` is a sibling settlement writer (the
chain-mirror discovery path, used as a backstop when the harvester's Gamma-
capture sweep hasn't observed a settlement yet) that reuses the same durable
projection primitive but NEVER had the equivalent guard: it unconditionally
computed `_pnl`/`_exit_price` from the binary settlement outcome (1.0 won /
0.0 lost) and wrote those over `projection["realized_pnl_usd"]` /
`projection["exit_price"]` regardless of what was already durably booked.

A position that exits before the chain-mirror observes the underlying
market's settlement has a real per-share fill price -- not 1.0/0.0 -- booked
into position_current at economic close. If the chain-mirror reconciler
later fires a redundant SETTLED event on that same position (the race the
reconciler's own `has_confirmed_exit_fill_for_position` docstring
acknowledges: "chain-mirror can observe the wallet token disappearing before
the exit-fill projector has folded the position to economically_closed"),
this bug regrades the close using the binary settlement price instead of the
real exit fill's economics -- clobbering a real, already-correct realized
gain/loss with a materially different (sometimes wrong-signed, sometimes
exactly 0.0) number.

Fix: `_apply_settlement_finding` now checks `phase_before == "economically_closed"`
(mirroring compute_settlement_close's `was_economically_closed` guard) and,
when true, preserves the row's own realized_pnl_usd/exit_price instead of
re-deriving them from the binary settlement outcome -- for both the durable
projection AND the SETTLED event's payload_json.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from src.state.chain_mirror_reconciler import (
    CLOSED_WORTHLESS,
    MirrorFinding,
    _apply_settlement_finding,
)
from src.state.db import init_schema, init_schema_trade_only


@pytest.fixture
def trades_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    init_schema_trade_only(conn)
    yield conn
    conn.close()


def _insert_economically_closed_position(
    conn: sqlite3.Connection,
    *,
    position_id: str,
    direction: str,
    shares: float,
    cost_basis_usd: float,
    entry_price: float,
    realized_pnl_usd: float,
    exit_price: float,
) -> None:
    conn.execute(
        """
        INSERT INTO position_current (
            position_id, phase, trade_id, city, target_date, bin_label,
            direction, unit, shares, chain_shares, cost_basis_usd, entry_price,
            strategy_key, chain_state, token_id, no_token_id, condition_id,
            updated_at, temperature_metric, realized_pnl_usd, exit_price,
            exit_reason, fill_authority, p_posterior
        ) VALUES (
            ?, 'economically_closed', ?, 'Tokyo', '2026-07-06', '26°C',
            ?, 'F', ?, 0.0, ?, ?,
            'test_strategy', 'synced', ?, ?, 'cond-1',
            '2026-07-06T03:47:33+00:00', 'high', ?, ?,
            'FAMILY_DIRECT_SELL_DOMINATES_HOLD', 'venue_confirmed_full', 0.87
        )
        """,
        (
            position_id, position_id,
            direction, shares,
            cost_basis_usd, entry_price,
            f"tok-{position_id}", f"tok-{position_id}-no",
            realized_pnl_usd, exit_price,
        ),
    )
    conn.commit()


class TestChainMirrorSettlementPreservesBookedCloseEconomics:
    def test_redundant_settlement_after_real_exit_fill_preserves_booked_pnl(self, trades_conn):
        """Reproduces the confirmed live clobber (position_id 011ebe1c-edd,
        Tokyo buy_no, 2026-07-06/07): a real exit fill booked a loss of
        -$5.50 (13.83 shares @ $0.205 exit, $8.34 cost basis), then a
        redundant chain-mirror-graded settlement (the underlying market
        resolved such that the position's bin *won*, i.e. exit_price=1.0)
        must NOT overwrite the booked -$5.50 loss with the binary win value.
        """
        _insert_economically_closed_position(
            trades_conn,
            position_id="pos-eco-mirror-settle",
            direction="buy_no",
            shares=13.83,
            cost_basis_usd=8.34,
            entry_price=0.603,
            realized_pnl_usd=-5.50,
            exit_price=0.205,
        )

        finding = MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id="pos-eco-mirror-settle",
            asset=f"tok-pos-eco-mirror-settle",
            writes=True,
            details={"won": True, "settlement_value": 26.0, "market_slug": "tokyo-2026-07-06"},
        )
        _apply_settlement_finding(
            trades_conn, finding, now=datetime(2026, 7, 7, 10, 31, tzinfo=timezone.utc)
        )

        row = trades_conn.execute(
            "SELECT phase, realized_pnl_usd, exit_price FROM position_current "
            "WHERE position_id = 'pos-eco-mirror-settle'"
        ).fetchone()
        assert row["phase"] == "settled"
        assert row["realized_pnl_usd"] == pytest.approx(-5.50), (
            "Bug C: redundant chain-mirror SETTLED clobbered booked realized_pnl_usd "
            f"-5.50 -> {row['realized_pnl_usd']}"
        )
        assert row["exit_price"] == pytest.approx(0.205), (
            "Bug C: redundant chain-mirror SETTLED clobbered booked exit_price "
            f"0.205 -> {row['exit_price']}"
        )

        settled_event = trades_conn.execute(
            "SELECT payload_json FROM position_events "
            "WHERE position_id = 'pos-eco-mirror-settle' AND event_type = 'SETTLED'"
        ).fetchone()
        payload = json.loads(settled_event["payload_json"])
        assert payload["pnl"] == pytest.approx(-5.50), (
            f"Bug C: SETTLED payload must carry booked pnl -5.50, got {payload['pnl']}"
        )
        assert payload["exit_price"] == pytest.approx(0.205)

    @pytest.mark.parametrize("missing_field", ["realized_pnl_usd", "exit_price"])
    def test_missing_booked_close_economics_fails_without_settling(
        self, trades_conn, missing_field
    ):
        """An economic close without its booked money facts is corrupt truth.

        The settlement backstop must expose that corruption for recovery; it
        must not invent zero P&L or a binary exit price and then make the false
        economics terminal by advancing the position to settled.
        """
        position_id = f"pos-missing-{missing_field}"
        _insert_economically_closed_position(
            trades_conn,
            position_id=position_id,
            direction="buy_no",
            shares=10.0,
            cost_basis_usd=5.0,
            entry_price=0.5,
            realized_pnl_usd=-1.0,
            exit_price=0.4,
        )
        trades_conn.execute(
            f"UPDATE position_current SET {missing_field} = NULL WHERE position_id = ?",
            (position_id,),
        )
        trades_conn.commit()
        finding = MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id=position_id,
            asset=f"tok-{position_id}",
            writes=True,
            details={"won": False, "settlement_value": 26.0},
        )

        with pytest.raises(ValueError, match="missing booked close economics"):
            _apply_settlement_finding(
                trades_conn,
                finding,
                now=datetime(2026, 7, 19, tzinfo=timezone.utc),
            )

        row = trades_conn.execute(
            "SELECT phase, realized_pnl_usd, exit_price FROM position_current "
            "WHERE position_id = ?",
            (position_id,),
        ).fetchone()
        assert row["phase"] == "economically_closed"
        assert row[missing_field] is None
        event_count = trades_conn.execute(
            "SELECT COUNT(*) FROM position_events WHERE position_id = ? "
            "AND event_type = 'SETTLED'",
            (position_id,),
        ).fetchone()[0]
        assert event_count == 0

    def test_settlement_from_active_still_computes_binary_economics(self, trades_conn):
        """Regression: a position with no prior real exit fill (phase_before
        != economically_closed) must still get its economics from the binary
        settlement outcome exactly as before this fix."""
        trades_conn.execute(
            """
            INSERT INTO position_current (
                position_id, phase, trade_id, city, target_date, bin_label,
                direction, unit, shares, chain_shares, cost_basis_usd, entry_price,
                strategy_key, chain_state, token_id, no_token_id, condition_id,
                updated_at, temperature_metric, p_posterior
            ) VALUES (
                'pos-active-mirror-settle', 'active', 'pos-active-mirror-settle',
                'Milan', '2026-06-23', '40°C', 'buy_yes', 'F', 10.0, 10.0, 4.0, 0.40,
                'test_strategy', 'synced', 'tok-milan-yes', '', 'cond-1',
                '2026-06-23T00:00:00+00:00', 'high', 0.7
            )
            """
        )
        trades_conn.commit()

        finding = MirrorFinding(
            classification=CLOSED_WORTHLESS,
            position_id="pos-active-mirror-settle",
            asset="tok-milan-yes",
            writes=True,
            details={"won": True},
        )
        _apply_settlement_finding(
            trades_conn, finding, now=datetime(2026, 6, 24, tzinfo=timezone.utc)
        )

        row = trades_conn.execute(
            "SELECT realized_pnl_usd, exit_price FROM position_current "
            "WHERE position_id = 'pos-active-mirror-settle'"
        ).fetchone()
        # won: pnl = shares * 1.0 - cost_basis = 10.0 - 4.0 = 6.0
        assert row["realized_pnl_usd"] == pytest.approx(6.0)
        assert row["exit_price"] == pytest.approx(1.0)
