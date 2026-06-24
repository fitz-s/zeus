# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: docs/evidence/same_day_exit_blindness/2026-06-23_toronto_total_loss.md
#   + frontier consult REQ-20260623-174044-18fe71
"""Weather-change scenario matrix for hard_fact_bin_verdict (pure function).

Operator hard requirement: a hard-fact exit MUST NEVER fire against a position
that can still win (no false exits). These are TDD characterisation tests — they
assert the EXISTING function's behaviour. If any scenario's actual verdict differs
from the expected matrix, DO NOT edit the function; report the mismatch.

Test surface: hard_fact_bin_verdict (pure, no I/O, no sources).

Notation used in comments:
  M   = effective_extreme (settlement-rounded running high or running low)
  K   = threshold value (24 used throughout for HIGH examples, 24 for LOW)
  "K or above"   -> bin_low=K, bin_high=None   (open-top shoulder)
  "K or below"   -> bin_low=None, bin_high=K   (open-bottom shoulder)
  "exactly K"    -> bin_low=K, bin_high=K      (finite singleton)
  [lo, hi]       -> bin_low=lo, bin_high=hi    (finite range)

MONOTONE EXTREME INVARIANT: effective_extreme is the caller-managed monotone
running max (high) / running min (low), already margin-adjusted. A temperature
FALLING BACK does NOT change effective_extreme — only the running extreme value M
matters.  This means every outcome in the matrix is a TERMINAL verdict for the
bin once triggered.

MARGIN NOTE: the no-false-exit margin lives in evaluate_hard_fact_exit (the
caller), not in this pure function.  Passing M == bin_high exactly tests the
boundary of THIS function; the caller ensures real-world M never reaches that
point unless the margin has been cleared.
"""
from __future__ import annotations

import pytest

from src.execution.day0_hard_fact_exit import HardFactVerdict, hard_fact_bin_verdict

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

K = 24.0      # canonical threshold value; chosen to keep arithmetic clear
LO = 22.0     # range low for finite-range tests
HI = 24.0     # range high for finite-range tests  (same as K intentionally)


def _verdict(
    *,
    metric: str,
    direction: str,
    bin_low: float | None,
    bin_high: float | None,
    effective_extreme: float,
) -> str | None:
    """Call the function under test; return action string or None."""
    result = hard_fact_bin_verdict(
        metric=metric,
        direction=direction,
        bin_low=bin_low,
        bin_high=bin_high,
        effective_extreme=effective_extreme,
    )
    if result is None:
        return None
    assert isinstance(result, HardFactVerdict)
    return result.action


# ===========================================================================
# HIGH METRIC — complete 3 × 6 matrix
#
# Bin types (HIGH):
#   "K or above"  bin_low=K,    bin_high=None  (open-top shoulder)
#   "K or below"  bin_low=None, bin_high=K     (open-bottom shoulder)
#   "exactly K"   bin_low=K,    bin_high=K     (finite singleton)
#
# Row scenarios (M vs K):
#   M < K    (rising/still below threshold)
#   M = K    (first touch / plateau)
#   M > K    (overshoot)
#
# Rationale per cell is encoded in comments on each row.
# ===========================================================================

class TestHighMetricMatrix:
    """Full 3×6 verdict matrix for metric='high'."""

    # ---- "K or above" (bin_low=K, bin_high=None) --------------------------
    # Rationale: M>=K means the running max has permanently reached/passed the
    # threshold — by monotonicity it can never return below K.  YES is certain;
    # NO has structurally lost.
    # M<K: nothing settled yet for either side.

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_high_k_or_above_m_below_k(self, direction, expected):
        """M < K, 'K or above': threshold not yet crossed — no hard fact."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=K, bin_high=None,
            effective_extreme=K - 1,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # M = K: shoulder entered (M >= bin_low, bin_high is None) ->
        #   buy_no  = EXIT_DEAD_BIN   (NO lost; max can never leave shoulder)
        #   buy_yes = HOLD_STRUCTURAL_WIN (YES won; max is in the shoulder)
        ("buy_no",  "EXIT_DEAD_BIN"),
        ("buy_yes", "HOLD_STRUCTURAL_WIN"),
    ])
    def test_high_k_or_above_m_equals_k(self, direction, expected):
        """M = K, 'K or above': shoulder first touch -> NO dead, YES won."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=K, bin_high=None,
            effective_extreme=K,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  "EXIT_DEAD_BIN"),
        ("buy_yes", "HOLD_STRUCTURAL_WIN"),
    ])
    def test_high_k_or_above_m_above_k(self, direction, expected):
        """M > K, 'K or above': shoulder entered and beyond -> same as touch."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=K, bin_high=None,
            effective_extreme=K + 1,
        ) == expected

    # ---- "K or below" (bin_low=None, bin_high=K) --------------------------
    # Rationale: the max settling AT or BELOW K is required for YES.
    # M < K  : max still within the allowed zone, nothing terminal.
    # M = K  : max is exactly K — still alive (max could climb to K+1 tomorrow).
    #           Both sides stay in estimator territory: None.
    # M > K  : max overshot the upper bound.  YES(<=K) is dead; NO won.

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_high_k_or_below_m_below_k(self, direction, expected):
        """M < K, 'K or below': max inside allowed zone — no hard fact."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=None, bin_high=K,
            effective_extreme=K - 1,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # M = K exactly: NOT yet beyond bin_high, so dead = False.
        # shoulder_entered: bin_low is None AND bin_high is NOT None AND M <= K -> True
        # -> buy_no = EXIT_DEAD_BIN, buy_yes = HOLD_STRUCTURAL_WIN
        # Wait — let's re-examine: "K or below" -> bin_low=None, bin_high=K.
        # For metric=high:
        #   dead = bin_high is not None AND M > bin_high -> 24 > 24 = False
        #   shoulder_entered = bin_high is None ... -> bin_high is NOT None -> False
        # So both False -> None. That is the correct result: at exactly K the
        # max might still climb, so neither side has a hard fact.
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_high_k_or_below_m_equals_k(self, direction, expected):
        """M = K, 'K or below': max touches upper edge but NOT beyond — None.

        dead   = M > bin_high = 24 > 24 = False.
        shoulder_entered for 'K or below' requires bin_high is None — it is not.
        -> None for both sides.  The max could still go higher.
        """
        assert _verdict(
            metric="high", direction=direction,
            bin_low=None, bin_high=K,
            effective_extreme=K,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # M > K: dead = True -> YES dead, NO won.
        ("buy_no",  "HOLD_STRUCTURAL_WIN"),
        ("buy_yes", "EXIT_DEAD_BIN"),
    ])
    def test_high_k_or_below_m_above_k(self, direction, expected):
        """M > K, 'K or below': max overshot upper bound -> YES dead, NO won."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=None, bin_high=K,
            effective_extreme=K + 1,
        ) == expected

    # ---- "exactly K" (bin_low=K, bin_high=K) ------------------------------
    # Rationale: the singleton bin means the max must SETTLE AT exactly K.
    # M < K  : max below — both sides live; nothing terminal.
    # M = K  : max AT K — NOT terminal for either side (the Toronto invariant):
    #          dead requires M > bin_high = K, which fails.  The max is currently
    #          at K but could climb to K+1 before close.  None both directions.
    # M > K  : max overshot; YES(==K) is dead; NO(!=K) won.

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_high_exactly_k_m_below_k(self, direction, expected):
        """M < K, 'exactly K': below threshold — no hard fact."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=K, bin_high=K,
            effective_extreme=K - 1,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # THE TORONTO INVARIANT: M = K exactly with a finite singleton bin
        # must NOT produce a hard-fact exit for either side.
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_high_exactly_k_m_equals_k(self, direction, expected):
        """M = K, 'exactly K': current max matches the singleton — NOT terminal.

        dead          = M > K = 24 > 24 = False.
        shoulder_entered: bin_high = K (not None) -> condition fails -> False.
        Result: None both sides (the Toronto no-false-exit invariant).
        """
        assert _verdict(
            metric="high", direction=direction,
            bin_low=K, bin_high=K,
            effective_extreme=K,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # M > K: dead = True -> YES dead, NO won.
        ("buy_no",  "HOLD_STRUCTURAL_WIN"),
        ("buy_yes", "EXIT_DEAD_BIN"),
    ])
    def test_high_exactly_k_m_above_k(self, direction, expected):
        """M > K, 'exactly K': overshoot kills YES, NO won."""
        assert _verdict(
            metric="high", direction=direction,
            bin_low=K, bin_high=K,
            effective_extreme=K + 1,
        ) == expected


# ===========================================================================
# LOW METRIC — mirror of the HIGH matrix
#
# Bin types (LOW):
#   "K or below"  bin_low=None, bin_high=K     (open-bottom shoulder for lows)
#   "K or above"  bin_low=K,    bin_high=None  (open-top for lows)
#   "exactly K"   bin_low=K,    bin_high=K
#
# Row scenarios (m = running low vs K):
#   m > K    (still above threshold — falling)
#   m = K    (first touch)
#   m < K    (undershoot)
#
# Rationale: mirror of HIGH with direction of inequality flipped.
#   dead (low) = bin_low is not None AND m < bin_low
#   shoulder_entered (low) = bin_low is None AND bin_high is not None AND m <= bin_high
# ===========================================================================

class TestLowMetricMatrix:
    """Full 3×6 verdict matrix for metric='low'."""

    # ---- "K or below" (bin_low=None, bin_high=K) for LOW -----------------
    # Rationale: LOW "K or below" means the market requires the running min to
    # settle AT or BELOW K.  When m <= bin_high (= K) the shoulder is entered;
    # YES won, NO lost.  m > K: nothing terminal yet.

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_low_k_or_below_m_above_k(self, direction, expected):
        """m > K, 'K or below' (low): min still above threshold — no hard fact."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=None, bin_high=K,
            effective_extreme=K + 1,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # m = K: shoulder_entered = bin_low is None AND bin_high is not None
        #        AND m <= bin_high -> True
        # -> buy_no  = EXIT_DEAD_BIN (NO lost: min can never rise above K)
        # -> buy_yes = HOLD_STRUCTURAL_WIN
        ("buy_no",  "EXIT_DEAD_BIN"),
        ("buy_yes", "HOLD_STRUCTURAL_WIN"),
    ])
    def test_low_k_or_below_m_equals_k(self, direction, expected):
        """m = K, 'K or below' (low): shoulder first touch -> NO dead, YES won."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=None, bin_high=K,
            effective_extreme=K,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  "EXIT_DEAD_BIN"),
        ("buy_yes", "HOLD_STRUCTURAL_WIN"),
    ])
    def test_low_k_or_below_m_below_k(self, direction, expected):
        """m < K, 'K or below' (low): deep into shoulder — same as touch."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=None, bin_high=K,
            effective_extreme=K - 1,
        ) == expected

    # ---- "K or above" (bin_low=K, bin_high=None) for LOW -----------------
    # Rationale: LOW "K or above" means the min must settle AT or ABOVE K.
    # m < K (undershoot): dead -> YES dead, NO won.
    # m = K: NOT dead (dead requires m < bin_low = K, which fails at equality).
    #        shoulder_entered: bin_high is None -> False.  Both: None.
    # m > K: m > K > bin_low but m is NOT < bin_low -> not dead; not shoulder. None.

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_low_k_or_above_m_above_k(self, direction, expected):
        """m > K, 'K or above' (low): min comfortably above threshold — no fact."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=K, bin_high=None,
            effective_extreme=K + 1,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # m = K exactly: dead = m < K = False; shoulder_entered False.  None.
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_low_k_or_above_m_equals_k(self, direction, expected):
        """m = K, 'K or above' (low): exactly at lower bound — not < K, so None."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=K, bin_high=None,
            effective_extreme=K,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # m < K: dead -> YES dead (min dropped below required floor), NO won.
        ("buy_no",  "HOLD_STRUCTURAL_WIN"),
        ("buy_yes", "EXIT_DEAD_BIN"),
    ])
    def test_low_k_or_above_m_below_k(self, direction, expected):
        """m < K, 'K or above' (low): undershoot -> YES dead, NO won."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=K, bin_high=None,
            effective_extreme=K - 1,
        ) == expected

    # ---- "exactly K" (bin_low=K, bin_high=K) for LOW ---------------------
    # Mirror of HIGH exactly-K logic with opposite inequality for dead.

    @pytest.mark.parametrize("direction,expected", [
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_low_exactly_k_m_above_k(self, direction, expected):
        """m > K, 'exactly K' (low): min above singleton — no hard fact yet."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=K, bin_high=K,
            effective_extreme=K + 1,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # m = K exactly: dead = m < K = False; shoulder False. None both sides
        # (the Toronto invariant mirror: min AT K might still drop to K-1).
        ("buy_no",  None),
        ("buy_yes", None),
    ])
    def test_low_exactly_k_m_equals_k(self, direction, expected):
        """m = K, 'exactly K' (low): min at singleton — NOT terminal (may drop).

        dead = m < bin_low = 24 < 24 = False.
        shoulder_entered: bin_high not None -> False.
        Result: None both sides (Toronto invariant mirror for LOW).
        """
        assert _verdict(
            metric="low", direction=direction,
            bin_low=K, bin_high=K,
            effective_extreme=K,
        ) == expected

    @pytest.mark.parametrize("direction,expected", [
        # m < K: dead = True -> YES dead, NO won.
        ("buy_no",  "HOLD_STRUCTURAL_WIN"),
        ("buy_yes", "EXIT_DEAD_BIN"),
    ])
    def test_low_exactly_k_m_below_k(self, direction, expected):
        """m < K, 'exactly K' (low): undershoot kills YES, NO won."""
        assert _verdict(
            metric="low", direction=direction,
            bin_low=K, bin_high=K,
            effective_extreme=K - 1,
        ) == expected


# ===========================================================================
# NAMED INVARIANT TESTS
#
# These are explicitly required by the operator spec and named so they
# appear clearly in the pytest output.
# ===========================================================================

class TestNamedInvariants:
    """Critical named invariants — the Toronto incident and its neighbours."""

    def test_exact_bin_touch_is_never_a_hard_fact_either_side(self):
        """THE TORONTO INVARIANT: M = K with a finite singleton bin must return
        None for BOTH directions.

        Context: the Toronto incident was caused by treating the running max
        touching the singleton bin exactly as a hard-fact exit for buy_yes.
        At M = K the max has matched the bin value but can still climb to K+1
        before market close, so YES still has a survival path.  No exit must fire.

        HIGH:
          dead           = M > bin_high = K > K = False
          shoulder_entered: bin_high = K (not None) -> condition False
        """
        for direction in ("buy_yes", "buy_no"):
            result = hard_fact_bin_verdict(
                metric="high",
                direction=direction,
                bin_low=K,
                bin_high=K,
                effective_extreme=K,  # exact touch
            )
            assert result is None, (
                f"TORONTO INVARIANT VIOLATED: metric=high direction={direction} "
                f"bin=[{K},{K}] M={K} -> expected None, got {result}"
            )

    def test_exact_bin_touch_low_metric_is_never_a_hard_fact_either_side(self):
        """Toronto invariant mirror for LOW: m = K with singleton -> None both."""
        for direction in ("buy_yes", "buy_no"):
            result = hard_fact_bin_verdict(
                metric="low",
                direction=direction,
                bin_low=K,
                bin_high=K,
                effective_extreme=K,
            )
            assert result is None, (
                f"TORONTO INVARIANT VIOLATED (LOW): direction={direction} "
                f"bin=[{K},{K}] m={K} -> expected None, got {result}"
            )

    def test_exact_bin_overshoot_kills_yes_not_no(self):
        """M = K+1 with 'exactly K' bin: buy_yes exits, buy_no is structural win.

        This is the SMALLEST overshoot that produces a hard fact.  One unit
        beyond the finite bin edge is unambiguous: YES cannot win.
        """
        m = K + 1
        v_yes = hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=K, bin_high=K,
            effective_extreme=m,
        )
        assert v_yes is not None and v_yes.action == "EXIT_DEAD_BIN", (
            f"buy_yes at M={m} vs bin=[{K},{K}]: expected EXIT_DEAD_BIN, got {v_yes}"
        )
        v_no = hard_fact_bin_verdict(
            metric="high", direction="buy_no",
            bin_low=K, bin_high=K,
            effective_extreme=m,
        )
        assert v_no is not None and v_no.action == "HOLD_STRUCTURAL_WIN", (
            f"buy_no at M={m} vs bin=[{K},{K}]: expected HOLD_STRUCTURAL_WIN, got {v_no}"
        )

    # -----------------------------------------------------------------------
    # Finite range [LO, HI] tests
    # -----------------------------------------------------------------------

    def test_finite_range_extreme_inside_is_none_both_directions(self):
        """A finite range [lo, hi] with lo < M < hi: no hard fact for either side.

        The max is inside the range — it can still exit upward (killing YES) or
        never climb above hi (keeping YES alive).  Estimator territory.
        """
        m_inside = (LO + HI) / 2  # strictly between lo and hi
        for direction in ("buy_yes", "buy_no"):
            result = hard_fact_bin_verdict(
                metric="high", direction=direction,
                bin_low=LO, bin_high=HI,
                effective_extreme=m_inside,
            )
            assert result is None, (
                f"finite range [{LO},{HI}] M={m_inside} direction={direction}: "
                f"expected None, got {result}"
            )

    def test_finite_range_extreme_at_low_edge_is_none_both_directions(self):
        """M = lo of a finite range: max is at the lower edge — not a hard fact.

        dead = M > hi = LO > HI? Only if LO > HI (it is not); False.
        shoulder_entered: bin_high is not None -> False.
        """
        for direction in ("buy_yes", "buy_no"):
            result = hard_fact_bin_verdict(
                metric="high", direction=direction,
                bin_low=LO, bin_high=HI,
                effective_extreme=LO,
            )
            assert result is None, (
                f"finite range [{LO},{HI}] M={LO} direction={direction}: "
                f"expected None, got {result}"
            )

    def test_finite_range_extreme_at_high_edge_is_none_both_directions(self):
        """M = hi of a finite range: max at upper edge — NOT yet dead.

        dead = M > hi = HI > HI = False.  No hard fact.  This is the
        boundary-of-the-pure-function test: the caller's margin ensures the
        effective_extreme never reaches bin_high unless the raw extreme cleared
        bin_high + margin.  Within this pure function, equality is not dead.
        """
        for direction in ("buy_yes", "buy_no"):
            result = hard_fact_bin_verdict(
                metric="high", direction=direction,
                bin_low=LO, bin_high=HI,
                effective_extreme=HI,  # exactly at edge
            )
            assert result is None, (
                f"finite range [{LO},{HI}] M={HI} (edge) direction={direction}: "
                f"expected None, got {result} "
                f"(margin lives in evaluate_hard_fact_exit, not this pure function)"
            )

    def test_finite_range_extreme_above_high_kills_yes(self):
        """M > hi of a finite range: dead for buy_yes, win for buy_no."""
        m_above = HI + 0.5
        v_yes = hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=LO, bin_high=HI,
            effective_extreme=m_above,
        )
        assert v_yes is not None and v_yes.action == "EXIT_DEAD_BIN", (
            f"finite range [{LO},{HI}] M={m_above} buy_yes: "
            f"expected EXIT_DEAD_BIN, got {v_yes}"
        )
        v_no = hard_fact_bin_verdict(
            metric="high", direction="buy_no",
            bin_low=LO, bin_high=HI,
            effective_extreme=m_above,
        )
        assert v_no is not None and v_no.action == "HOLD_STRUCTURAL_WIN", (
            f"finite range [{LO},{HI}] M={m_above} buy_no: "
            f"expected HOLD_STRUCTURAL_WIN, got {v_no}"
        )

    def test_finite_range_extreme_below_low_is_none(self):
        """M < lo of a HIGH finite range [lo, hi]: max not yet in range.

        For HIGH metric: dead = M > hi? No.  shoulder_entered: bin_high not None -> False.
        -> None for both sides.  (The range is not yet entered from below.)
        """
        m_below = LO - 1
        for direction in ("buy_yes", "buy_no"):
            result = hard_fact_bin_verdict(
                metric="high", direction=direction,
                bin_low=LO, bin_high=HI,
                effective_extreme=m_below,
            )
            assert result is None, (
                f"finite range [{LO},{HI}] M={m_below} (below lo) direction={direction}: "
                f"expected None, got {result}"
            )

    # -----------------------------------------------------------------------
    # Margin-sensitivity documentation tests
    # -----------------------------------------------------------------------

    def test_margin_boundary_at_bin_high_exactly_is_not_dead(self):
        """M == bin_high exactly -> None (not EXIT).

        Documents that the no-false-exit margin lives in the CALLER
        (evaluate_hard_fact_exit / settlement_grade_effective_extreme), not in
        this pure function.  The pure function uses strict > for dead: if
        effective_extreme == bin_high, dead = False, result = None.

        A settlement-faithful city with margin=0 would pass effective_extreme =
        raw - 0 = raw through.  For the pure function to fire, raw must satisfy
        raw > bin_high, i.e. raw >= bin_high + 1 (integer grid).
        """
        result = hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=K, bin_high=K,
            effective_extreme=K,  # exactly at bin_high, not beyond
        )
        assert result is None, (
            f"M == bin_high: expected None (margin lives in caller), got {result}"
        )

    def test_margin_boundary_epsilon_above_bin_high_fires(self):
        """M = bin_high + epsilon -> EXIT_DEAD_BIN for buy_yes.

        Once the effective_extreme strictly exceeds bin_high (however slightly),
        the pure function fires.  In practice the caller's margin ensures this
        only happens when the raw extreme has cleared the calibration margin.
        """
        epsilon = 0.001  # sub-integer: caller may pass non-integer effective_extreme
        result = hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=K, bin_high=K,
            effective_extreme=K + epsilon,
        )
        assert result is not None and result.action == "EXIT_DEAD_BIN", (
            f"M = bin_high + epsilon: expected EXIT_DEAD_BIN, got {result}"
        )


# ===========================================================================
# EDGE / GUARD TESTS
# ===========================================================================

class TestGuardRails:
    """Guard-rail and edge-case tests for the pure function."""

    def test_both_bin_bounds_none_returns_none(self):
        """bin_low=None and bin_high=None: untyped bin -> None (guard)."""
        assert hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=None, bin_high=None,
            effective_extreme=30.0,
        ) is None

    def test_invalid_metric_returns_none(self):
        assert hard_fact_bin_verdict(
            metric="max", direction="buy_yes",
            bin_low=24.0, bin_high=24.0,
            effective_extreme=25.0,
        ) is None

    def test_invalid_direction_returns_none(self):
        assert hard_fact_bin_verdict(
            metric="high", direction="hold",
            bin_low=24.0, bin_high=24.0,
            effective_extreme=25.0,
        ) is None

    def test_verdict_dataclass_is_frozen(self):
        """HardFactVerdict must be immutable (dataclass frozen=True)."""
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=K, bin_high=K,
            effective_extreme=K + 1,
        )
        assert v is not None
        with pytest.raises((AttributeError, TypeError)):
            v.action = "MUTATED"  # type: ignore[misc]

    def test_high_shoulder_below_threshold_is_none(self):
        """M < bin_low for an open-top 'K or above' shoulder -> None.

        shoulder_entered requires M >= bin_low; below it is not entered.
        """
        result = hard_fact_bin_verdict(
            metric="high", direction="buy_no",
            bin_low=K, bin_high=None,
            effective_extreme=K - 0.5,
        )
        assert result is None

    def test_low_shoulder_above_threshold_is_none(self):
        """m > bin_high for an open-bottom 'K or below' low shoulder -> None.

        shoulder_entered requires m <= bin_high; above it is not entered.
        """
        result = hard_fact_bin_verdict(
            metric="low", direction="buy_no",
            bin_low=None, bin_high=K,
            effective_extreme=K + 0.5,
        )
        assert result is None

    @pytest.mark.parametrize("direction_obj", [
        "buy_yes", "buy_no",
    ])
    def test_direction_string_normalised(self, direction_obj):
        """Direction as plain string (most common caller path) works correctly."""
        result = hard_fact_bin_verdict(
            metric="high", direction=direction_obj,
            bin_low=K, bin_high=K,
            effective_extreme=K + 1,
        )
        assert result is not None

    def test_high_metric_dead_verdict_contains_expected_fields(self):
        """A dead verdict has the correct HardFactVerdict fields."""
        v = hard_fact_bin_verdict(
            metric="high", direction="buy_yes",
            bin_low=K, bin_high=K,
            effective_extreme=K + 1,
        )
        assert v is not None
        assert v.action == "EXIT_DEAD_BIN"
        assert v.metric == "high"
        assert v.rounded_extreme == pytest.approx(K + 1)
        # source is empty string from pure function (caller attaches it)
        assert v.source == ""

    def test_low_metric_shoulder_verdict_contains_expected_fields(self):
        """A shoulder verdict for low metric has correct fields."""
        v = hard_fact_bin_verdict(
            metric="low", direction="buy_no",
            bin_low=None, bin_high=K,
            effective_extreme=K - 1,
        )
        assert v is not None
        assert v.action == "EXIT_DEAD_BIN"
        assert v.metric == "low"
        assert v.rounded_extreme == pytest.approx(K - 1)
