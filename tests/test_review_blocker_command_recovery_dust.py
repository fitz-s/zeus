"""Antibody: command_recovery must NOT treat a venue underfill as a full close.

Review blocker C2/C3 class. The removed ``_EXIT_FULL_CLOSE_DUST_TOLERANCE``
(``Decimal("0.011")``) let a sell that covered the EXIT command but left an
on-chain residual (holding > command) be classified as a full close, after which
``_append_exit_filled_projection`` fabricated chain_shares / chain_avg_price /
chain_cost_basis_usd to zero with NO chain-confirmed-zero proof — losing a real
on-chain claim from risk/basis/settlement tracking.

The fix compares exact fill atoms against the exact on-record holding
(``max(command, chain_shares, shares)``) with NO tolerance (C2), and accumulates
fills as exact Decimals instead of a float ``SUM(CAST(... AS REAL))`` that loses
precision on the close/settlement boundary (C3).

Every ``is False`` assertion below returns ``True`` on the pre-fix code (the
tolerance classifies the residual-leaving fill as a full close) and ``False``
after — i.e. these FAIL on pre-fix and pass after. The candidates expose BOTH
the pre-fix float columns (``fill_filled_size``/``fill_avg_price``) and the
post-fix exact ``fill_pairs`` with identical economics, so the assertions isolate
the tolerance/exactness logic from the column-shape change.
"""

from decimal import Decimal

import pytest

from src.execution import command_recovery


def _candidate(*, filled, command_size, price="0.10"):
    filled = str(filled)
    return {
        "cmd_size": str(command_size),
        # post-fix exact-atom column
        "fill_pairs": f"{filled}#{price}",
        # pre-fix float-sum columns (identical value)
        "fill_filled_size": filled,
        "fill_avg_price": price,
    }


class TestExitFullCloseRequiresExactHoldingCoverage:
    """holding (max on-record exposure) = 1.000; the EXIT command fully fills at
    ``1.000 - delta``, leaving an on-chain residual of ``delta``."""

    @pytest.mark.parametrize("delta", ["0.001", "0.010", "0.011", "0.012"])
    def test_underfill_of_holding_is_not_a_full_close(self, delta):
        holding = Decimal("1.000")
        filled = holding - Decimal(delta)
        # command fully filled (filled == command_size) but below the holding.
        candidate = _candidate(filled=filled, command_size=filled)
        current = {"chain_shares": "1.000", "shares": "1.000"}
        # residual `delta` must survive: NOT a full close, so the chain-zeroing
        # projection must not run.
        assert (
            command_recovery._exit_trade_fact_covers_full_close(candidate, current)
            is False
        )

    def test_full_fill_of_holding_still_closes(self):
        candidate = _candidate(filled="1.000", command_size="1.000")
        current = {"chain_shares": "1.000", "shares": "1.000"}
        assert (
            command_recovery._exit_trade_fact_covers_full_close(candidate, current)
            is True
        )

    def test_overfill_still_closes(self):
        candidate = _candidate(filled="1.001", command_size="1.000")
        current = {"chain_shares": "1.000", "shares": "1.000"}
        assert (
            command_recovery._exit_trade_fact_covers_full_close(candidate, current)
            is True
        )

    def test_command_underfill_below_holding_is_not_a_close(self):
        # command 1.000, venue filled only 0.990: short of both command AND
        # holding -> never a full close (guarded pre- and post-fix).
        candidate = _candidate(filled="0.990", command_size="1.000")
        current = {"chain_shares": "1.000", "shares": "1.000"}
        assert (
            command_recovery._exit_trade_fact_covers_full_close(candidate, current)
            is False
        )


class TestExactFillAtomsOnSettlementBoundary:
    # ten 0.1 atoms sum to EXACTLY 1.0 in Decimal; a binary-float running SUM
    # lands on 0.9999999999999999 -- below a 1.0 close target.
    _TEN_TENTHS = "|".join(["0.1#0.10"] * 10)

    def test_accumulate_exact_fills_is_decimal_exact(self):
        filled, notional = command_recovery._accumulate_exact_fills(self._TEN_TENTHS)
        assert filled == Decimal("1.0")
        assert notional == Decimal("0.1")  # 10 * (0.1 * 0.10) == 0.1
        # the exactness the float REAL SUM path would lose:
        running = 0.0
        for _ in range(10):
            running += 0.1
        assert running < 1.0  # 0.9999999999999999
        assert filled >= Decimal("1.0")

    def test_exact_atoms_cover_full_close_where_float_sum_would_miss(self):
        candidate = {"cmd_size": "1.0", "fill_pairs": self._TEN_TENTHS}
        current = {"chain_shares": "1.0", "shares": "1.0"}
        # exact atoms reach the 1.0 holding -> genuine full close; a float SUM
        # would fall to 0.9999999999999999 and wrongly refuse.
        assert (
            command_recovery._exit_trade_fact_covers_full_close(candidate, current)
            is True
        )

    def test_empty_or_absent_pairs_is_not_a_close(self):
        assert command_recovery._accumulate_exact_fills("") == (None, None)
        assert command_recovery._accumulate_exact_fills(None) == (None, None)
        current = {"chain_shares": "1.00", "shares": "1.00"}
        assert (
            command_recovery._exit_trade_fact_covers_full_close(
                {"cmd_size": "1.00", "fill_pairs": ""}, current
            )
            is False
        )
