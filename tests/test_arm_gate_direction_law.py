# Created: 2026-06-03
# Last reused/audited: 2026-06-03
# Authority basis: Direction Law antibody (MEMORY.md DIRECTION LAW entry, GOAL#36,
#   project_live_goal_2026_06_03.md, task #135 W5 gate). Locks all four Direction
#   Law cases + complement property + known-loss regression guards.
#
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Structural antibody — makes the buy_no/buy_yes win-logic asymmetry error
#   unconstructable. The recurring bug is treating buy_no win-logic as the mirror of
#   buy_yes incorrectly (wrong side fires on the bin that actually settled).
#   This test must be RED if is_win() gets the direction wrong, then GREEN when fixed.
# Reuse: run in CI on every commit that touches EDLI win-logic, direction assignment,
#   or settlement measurement. If any assertion fails, the Direction Law is violated.
"""Direction Law antibody tests for EDLI ARM-gate win-logic.

The Direction Law (must hold exactly):
    buy_yes on bin B: WIN iff settlement lands IN bin B  (settled_bin == B)
    buy_no  on bin B: WIN iff settlement does NOT land in bin B  (settled_bin != B)

Mnemonic: buy_yes ≈ "I predict it settles HERE" → win when it does.
          buy_no  ≈ "I predict it does NOT settle here" → win when it does NOT.

The historical bug: buy_no win was incorrectly evaluated as (settled == traded_bin),
i.e., as if buy_no WON when the settlement matched the traded bin (backwards).
"""
from __future__ import annotations

import pytest

from scripts.measure_arm_gate_settlement import is_win

# ---------------------------------------------------------------------------
# Known bin constants used across tests
# ---------------------------------------------------------------------------

# Point bin: 28°C  (lo=28.0, hi=28.0)
B_POINT_LO = 28.0
B_POINT_HI = 28.0

# Other temperature, clearly different from the traded bin
OTHER_TEMP = 30.0

# Shoulder bins
SHOULDER_HI_LO = None    # left-shoulder: None means "X or below" when hi=19.0
SHOULDER_HI_VAL = 19.0
SHOULDER_LO_VAL = 29.0
SHOULDER_LO_HI = None    # right-shoulder: hi=None means "X or higher" when lo=29.0


# ---------------------------------------------------------------------------
# The Four Canonical Cases (ALL must pass — these are the antibody assertions)
# ---------------------------------------------------------------------------

class TestFourCases:
    """Lock all four Direction Law cases exactly."""

    def test_buy_yes_bin_match_is_win(self):
        """buy_yes on bin B, settlement IN bin B → WIN."""
        assert is_win("buy_yes", B_POINT_LO, B_POINT_HI, B_POINT_LO) is True

    def test_buy_yes_bin_mismatch_is_loss(self):
        """buy_yes on bin B, settlement NOT in bin B → LOSS."""
        assert is_win("buy_yes", B_POINT_LO, B_POINT_HI, OTHER_TEMP) is False

    def test_buy_no_bin_match_is_loss(self):
        """buy_no on bin B, settlement IN bin B → LOSS (the recurring bug direction)."""
        # This is the critical case: the historical bug made this return True.
        assert is_win("buy_no", B_POINT_LO, B_POINT_HI, B_POINT_LO) is False

    def test_buy_no_bin_mismatch_is_win(self):
        """buy_no on bin B, settlement NOT in bin B → WIN."""
        assert is_win("buy_no", B_POINT_LO, B_POINT_HI, OTHER_TEMP) is True


# ---------------------------------------------------------------------------
# Complement Property: buy_yes and buy_no are exact logical complements
# ---------------------------------------------------------------------------

class TestComplementProperty:
    """For any (B, settled), buy_yes win == NOT buy_no win."""

    @pytest.mark.parametrize("lo,hi,settled", [
        # Point bins
        (28.0, 28.0, 28.0),    # in bin
        (28.0, 28.0, 29.0),    # out of bin
        (28.0, 28.0, 27.0),    # out of bin
        # Range bins
        (68.0, 69.0, 68.5),    # in range (F)
        (68.0, 69.0, 70.0),    # above range
        (68.0, 69.0, 67.0),    # below range
        # Left-shoulder
        (None, 19.0, 17.0),    # below shoulder -> in bin
        (None, 19.0, 19.0),    # at boundary -> in bin
        (None, 19.0, 20.0),    # above shoulder -> out of bin
        # Right-shoulder
        (29.0, None, 31.0),    # above shoulder -> in bin
        (29.0, None, 29.0),    # at boundary -> in bin
        (29.0, None, 28.0),    # below shoulder -> out of bin
    ])
    def test_complement_holds(self, lo, hi, settled):
        """is_win(buy_yes) == NOT is_win(buy_no) for all (bin, settled) combinations."""
        yes_win = is_win("buy_yes", lo, hi, settled)
        no_win = is_win("buy_no", lo, hi, settled)
        assert yes_win == (not no_win), (
            f"Complement violated: bin=({lo},{hi}) settled={settled} "
            f"buy_yes_win={yes_win} buy_no_win={no_win} (should be exact opposites)"
        )


# ---------------------------------------------------------------------------
# Shoulder Bin Cases
# ---------------------------------------------------------------------------

class TestShoulderBins:
    """Shoulder bins have one open end — verify win logic handles them correctly."""

    def test_left_shoulder_settlement_in_bin_buy_yes_wins(self):
        """Left shoulder 'X or below': settlement <= hi → in bin → buy_yes wins."""
        assert is_win("buy_yes", None, 19.0, 17.0) is True

    def test_left_shoulder_settlement_in_bin_buy_no_loses(self):
        """Left shoulder 'X or below': settlement <= hi → in bin → buy_no loses."""
        assert is_win("buy_no", None, 19.0, 17.0) is False

    def test_left_shoulder_settlement_above_buy_yes_loses(self):
        """Left shoulder 'X or below': settlement > hi → out of bin → buy_yes loses."""
        assert is_win("buy_yes", None, 19.0, 20.0) is False

    def test_left_shoulder_settlement_above_buy_no_wins(self):
        """Left shoulder 'X or below': settlement > hi → out of bin → buy_no wins."""
        assert is_win("buy_no", None, 19.0, 20.0) is True

    def test_right_shoulder_settlement_in_bin_buy_yes_wins(self):
        """Right shoulder 'X or higher': settlement >= lo → in bin → buy_yes wins."""
        assert is_win("buy_yes", 29.0, None, 31.0) is True

    def test_right_shoulder_settlement_in_bin_buy_no_loses(self):
        """Right shoulder 'X or higher': settlement >= lo → in bin → buy_no loses."""
        assert is_win("buy_no", 29.0, None, 31.0) is False


# ---------------------------------------------------------------------------
# Range Bin Cases (F-unit markets)
# ---------------------------------------------------------------------------

class TestRangeBins:
    """Bounded range bins like '68-69°F' — settlement must be within [lo, hi]."""

    def test_range_in_bin_buy_yes_wins(self):
        assert is_win("buy_yes", 68.0, 69.0, 68.0) is True

    def test_range_in_bin_buy_yes_wins_at_hi(self):
        assert is_win("buy_yes", 68.0, 69.0, 69.0) is True

    def test_range_below_buy_yes_loses(self):
        assert is_win("buy_yes", 68.0, 69.0, 67.0) is False

    def test_range_above_buy_no_wins(self):
        assert is_win("buy_no", 68.0, 69.0, 70.0) is True


# ---------------------------------------------------------------------------
# Known Historical Losses (regression guard)
# These are buy_no trades on the bin that ACTUALLY settled — they must be LOSSES.
# Source: project_shadow_settlement_edge_2026_06_03.md (MEMORY) — the shoulder-bins
# negative cases and the buy_no-on-the-bin-that-settled loss pattern.
# ---------------------------------------------------------------------------

class TestKnownHistoricalLosses:
    """Regression guard: known losing positions must compute as LOSS."""

    def test_taipei_buy_no_on_settled_bin(self):
        """Taipei traded buy_no on 37°C, settlement=37°C → LOSS (in-bin settlement)."""
        # buy_no on 37°C, settlement landed at 37°C → settlement in traded bin → LOSS
        assert is_win("buy_no", 37.0, 37.0, 37.0) is False

    def test_shanghai_buy_no_on_settled_bin(self):
        """Shanghai traded buy_no on 31°C, settlement=31°C → LOSS (in-bin settlement)."""
        # buy_no on 31°C, settlement landed at 31°C → settlement in traded bin → LOSS
        assert is_win("buy_no", 31.0, 31.0, 31.0) is False

    def test_singapore_buy_no_on_settled_bin(self):
        """Singapore traded buy_no on 33°C, settlement=33°C → LOSS."""
        assert is_win("buy_no", 33.0, 33.0, 33.0) is False

    def test_buy_no_not_on_settled_bin_is_win(self):
        """Counter-check: buy_no on bin that did NOT settle is a WIN."""
        # buy_no on 31°C, settlement landed at 30°C (different bin) → WIN
        assert is_win("buy_no", 31.0, 31.0, 30.0) is True


# ---------------------------------------------------------------------------
# Invalid direction raises ValueError
# ---------------------------------------------------------------------------

class TestInvalidDirection:
    def test_unknown_direction_raises(self):
        with pytest.raises(ValueError, match="Unknown direction"):
            is_win("sell", 28.0, 28.0, 28.0)

    def test_empty_direction_raises(self):
        with pytest.raises(ValueError, match="Unknown direction"):
            is_win("", 28.0, 28.0, 28.0)
