# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: Direction Law antibody (MEMORY.md DIRECTION LAW entry, GOAL#36,
#   project_live_goal_2026_06_03.md, task #135 W5 gate). Locks all four Direction
#   Law cases + complement property + known-loss regression guards.
#
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=2026-06-03
# Purpose: Structural antibody — makes the buy_no/buy_yes win-logic asymmetry error
#   unconstructable. The recurring bug is treating buy_no win-logic as the mirror of
#   buy_yes incorrectly (wrong side fires on the bin that actually settled).
#   This test must be RED if grade_receipt() gets the direction wrong, GREEN when right.
#
# H2 CONSOLIDATION (2026-06-03): these tests were migrated off the deleted local
#   ``is_win`` heuristic onto the ONE truth function, ``grade_receipt``. The ARM
#   measurement script no longer carries a second grading path; the Direction Law
#   has a single implementation and this antibody now guards THAT implementation
#   directly. A tiny ``_won`` adapter below builds the typed ``Bin`` + settlement
#   stand-in and reads ``graded.won`` — it adds NO win-logic of its own.
"""Direction Law antibody tests for EDLI ARM-gate win-logic (via grade_receipt).

The Direction Law (must hold exactly):
    buy_yes on bin B: WIN iff settlement lands IN bin B  (settled_bin == B)
    buy_no  on bin B: WIN iff settlement does NOT land in bin B  (settled_bin != B)

Mnemonic: buy_yes ≈ "I predict it settles HERE" → win when it does.
          buy_no  ≈ "I predict it does NOT settle here" → win when it does NOT.

The historical bug: buy_no win was incorrectly evaluated as (settled == traded_bin),
i.e., as if buy_no WON when the settlement matched the traded bin (backwards).
"""
from __future__ import annotations

from typing import Optional

import pytest

from src.contracts.graded_receipt import grade_receipt
from src.types.market import Bin


# ---------------------------------------------------------------------------
# Adapter: build the typed Bin + settlement stand-in and read graded.won.
# This is the SAME path the ARM script's _grade_row_won uses — no second
# win-logic lives here. Unit is supplied explicitly because grade_receipt is
# unit-correct by construction (the retired is_win had no unit and that was a
# latent °C/°F bug surface). Tests pass the unit matching their bin.
# ---------------------------------------------------------------------------
class _FakeSettlement:
    def __init__(self, value: float, unit: str):
        self.settlement_value = value
        self.settlement_unit = unit


def _won(direction: str, lo: Optional[float], hi: Optional[float],
         settled: float, unit: str, label: str) -> bool:
    """Grade via the one truth function and return ``won``."""
    bin_obj = Bin(low=lo, high=hi, unit=unit, label=label)
    return grade_receipt(bin_obj, direction, _FakeSettlement(settled, unit)).won


# Canonical bins used across tests (explicit unit + label, per Bin's contract).
# 28°C point bin.
_POINT_28C = dict(lo=28.0, hi=28.0, unit="C", label="28°C")
# 68-69°F width-2 range bin.
_RANGE_68_69F = dict(lo=68.0, hi=69.0, unit="F", label="68-69°F")
# Left-shoulder (floor) "19°C or below".
_FLOOR_19C = dict(lo=None, hi=19.0, unit="C", label="19°C or below")
# Right-shoulder (ceiling) "29°C or higher".
_CEIL_29C = dict(lo=29.0, hi=None, unit="C", label="29°C or higher")


# ---------------------------------------------------------------------------
# The Four Canonical Cases (ALL must pass — these are the antibody assertions)
# ---------------------------------------------------------------------------

class TestFourCases:
    """Lock all four Direction Law cases exactly (graded via grade_receipt)."""

    def test_buy_yes_bin_match_is_win(self):
        """buy_yes on bin B, settlement IN bin B → WIN."""
        assert _won("buy_yes", settled=28.0, **_POINT_28C) is True

    def test_buy_yes_bin_mismatch_is_loss(self):
        """buy_yes on bin B, settlement NOT in bin B → LOSS."""
        assert _won("buy_yes", settled=30.0, **_POINT_28C) is False

    def test_buy_no_bin_match_is_loss(self):
        """buy_no on bin B, settlement IN bin B → LOSS (the recurring bug direction)."""
        # This is the critical case: the historical bug made this return True.
        assert _won("buy_no", settled=28.0, **_POINT_28C) is False

    def test_buy_no_bin_mismatch_is_win(self):
        """buy_no on bin B, settlement NOT in bin B → WIN."""
        assert _won("buy_no", settled=30.0, **_POINT_28C) is True


# ---------------------------------------------------------------------------
# Complement Property: buy_yes and buy_no are exact logical complements
# ---------------------------------------------------------------------------

class TestComplementProperty:
    """For any (B, settled), buy_yes win == NOT buy_no win."""

    @pytest.mark.parametrize("binspec,settled", [
        # Point bins (°C)
        (_POINT_28C, 28.0),    # in bin
        (_POINT_28C, 29.0),    # out of bin
        (_POINT_28C, 27.0),    # out of bin
        # Range bins (°F). 68.5 rounds-to-membership inside [68,69].
        (_RANGE_68_69F, 68.5),  # in range (F)
        (_RANGE_68_69F, 70.0),  # above range
        (_RANGE_68_69F, 67.0),  # below range
        # Left-shoulder / floor (°C)
        (_FLOOR_19C, 17.0),    # below shoulder -> in bin
        (_FLOOR_19C, 19.0),    # at boundary -> in bin
        (_FLOOR_19C, 20.0),    # above shoulder -> out of bin
        # Right-shoulder / ceiling (°C)
        (_CEIL_29C, 31.0),     # above shoulder -> in bin
        (_CEIL_29C, 29.0),     # at boundary -> in bin
        (_CEIL_29C, 28.0),     # below shoulder -> out of bin
    ])
    def test_complement_holds(self, binspec, settled):
        """buy_yes won == NOT buy_no won for all (bin, settled) combinations."""
        yes_win = _won("buy_yes", settled=settled, **binspec)
        no_win = _won("buy_no", settled=settled, **binspec)
        assert yes_win == (not no_win), (
            f"Complement violated: bin={binspec['label']} settled={settled} "
            f"buy_yes_win={yes_win} buy_no_win={no_win} (should be exact opposites)"
        )


# ---------------------------------------------------------------------------
# Shoulder Bin Cases
# ---------------------------------------------------------------------------

class TestShoulderBins:
    """Shoulder bins have one open end — verify win logic handles them correctly."""

    def test_left_shoulder_settlement_in_bin_buy_yes_wins(self):
        """Floor 'X or below': settlement <= hi → in bin → buy_yes wins."""
        assert _won("buy_yes", settled=17.0, **_FLOOR_19C) is True

    def test_left_shoulder_settlement_in_bin_buy_no_loses(self):
        """Floor 'X or below': settlement <= hi → in bin → buy_no loses."""
        assert _won("buy_no", settled=17.0, **_FLOOR_19C) is False

    def test_left_shoulder_settlement_above_buy_yes_loses(self):
        """Floor 'X or below': settlement > hi → out of bin → buy_yes loses."""
        assert _won("buy_yes", settled=20.0, **_FLOOR_19C) is False

    def test_left_shoulder_settlement_above_buy_no_wins(self):
        """Floor 'X or below': settlement > hi → out of bin → buy_no wins."""
        assert _won("buy_no", settled=20.0, **_FLOOR_19C) is True

    def test_right_shoulder_settlement_in_bin_buy_yes_wins(self):
        """Ceiling 'X or higher': settlement >= lo → in bin → buy_yes wins."""
        assert _won("buy_yes", settled=31.0, **_CEIL_29C) is True

    def test_right_shoulder_settlement_in_bin_buy_no_loses(self):
        """Ceiling 'X or higher': settlement >= lo → in bin → buy_no loses."""
        assert _won("buy_no", settled=31.0, **_CEIL_29C) is False


# ---------------------------------------------------------------------------
# Range Bin Cases (F-unit markets)
# ---------------------------------------------------------------------------

class TestRangeBins:
    """Bounded range bins like '68-69°F' — settlement must be within [lo, hi]."""

    def test_range_in_bin_buy_yes_wins(self):
        assert _won("buy_yes", settled=68.0, **_RANGE_68_69F) is True

    def test_range_in_bin_buy_yes_wins_at_hi(self):
        assert _won("buy_yes", settled=69.0, **_RANGE_68_69F) is True

    def test_range_below_buy_yes_loses(self):
        assert _won("buy_yes", settled=67.0, **_RANGE_68_69F) is False

    def test_range_above_buy_no_wins(self):
        assert _won("buy_no", settled=70.0, **_RANGE_68_69F) is True


# ---------------------------------------------------------------------------
# Known Historical Losses (regression guard)
# These are buy_no trades on the bin that ACTUALLY settled — they must be LOSSES.
# Source: project_shadow_settlement_edge_2026_06_03.md (MEMORY) — the buy_no-on-
# the-bin-that-settled loss pattern. All °C point bins.
# ---------------------------------------------------------------------------

class TestKnownHistoricalLosses:
    """Regression guard: known losing positions must compute as LOSS."""

    def test_taipei_buy_no_on_settled_bin(self):
        """Taipei traded buy_no on 37°C, settlement=37°C → LOSS (in-bin settlement)."""
        assert _won("buy_no", lo=37.0, hi=37.0, settled=37.0, unit="C", label="37°C") is False

    def test_shanghai_buy_no_on_settled_bin(self):
        """Shanghai traded buy_no on 31°C, settlement=31°C → LOSS (in-bin settlement)."""
        assert _won("buy_no", lo=31.0, hi=31.0, settled=31.0, unit="C", label="31°C") is False

    def test_singapore_buy_no_on_settled_bin(self):
        """Singapore traded buy_no on 33°C, settlement=33°C → LOSS."""
        assert _won("buy_no", lo=33.0, hi=33.0, settled=33.0, unit="C", label="33°C") is False

    def test_buy_no_not_on_settled_bin_is_win(self):
        """Counter-check: buy_no on bin that did NOT settle is a WIN."""
        assert _won("buy_no", lo=31.0, hi=31.0, settled=30.0, unit="C", label="31°C") is True


# ---------------------------------------------------------------------------
# Invalid direction raises ValueError (propagated from grade_receipt)
# ---------------------------------------------------------------------------

class TestInvalidDirection:
    def test_unknown_direction_raises(self):
        with pytest.raises(ValueError):
            _won("sell", lo=28.0, hi=28.0, settled=28.0, unit="C", label="28°C")

    def test_empty_direction_raises(self):
        with pytest.raises(ValueError):
            _won("", lo=28.0, hi=28.0, settled=28.0, unit="C", label="28°C")
