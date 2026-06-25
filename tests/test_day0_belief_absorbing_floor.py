# Created: 2026-06-25
# Last audited: 2026-06-25
# Authority basis: operator-reported "wrong exit" 2026-06-25 — Chicago 76-77F NO sold via
#   SETTLEMENT_IMMINENT @0.999 ("this is not held to settlement"; "you don't have 0.6 when market
#   is 0.99"). Root: the day0 belief (monitor_refresh:2524) read the RAW, revisable live
#   obs.high_so_far with no monotonic guard. day0_high_distribution samples = max(observed_high_so_far,
#   future_member_max), so a DOWNWARD revision of the observed high (evening METAR/station report)
#   dropped the floor back into the 76-77 bin, re-opening an ALREADY-WON max-bin and collapsing the
#   belief 1.0 -> 0.6524 -> false SETTLEMENT_IMMINENT sale. The absorbing law (REQ-20260623-184115,
#   _compose_day0_observed_extreme = max(live, canonical)) was wired into the hard-fact/reseed path
#   only; the belief was the last consumer still on the raw live reading. Fix = apply the SAME
#   monotonic absorbing floor to the belief's observed extreme.
"""Antibody: the day0 belief's observed extreme is MONOTONIC.

A later, lower live reading must NEVER undercut the canonical running max (high)
or running min (low). Once the observed running max has exceeded a bin upper
bound, that bin is WON and a subsequent lower reading cannot re-open it.
"""

import math

import pytest

from src.engine.monitor_refresh import _apply_absorbing_floor_to_observed_extreme


class TestAbsorbingFloorOnObservedExtreme:
    def test_high_revised_down_uses_canonical_running_max(self):
        """Chicago incident: live high revised down to 76.0, canonical running max 78.0.
        The won 76-77 bin must stay won — belief uses the absorbing 78.0, not 76.0."""
        assert (
            _apply_absorbing_floor_to_observed_extreme(76.0, 78.0, metric_is_low=False)
            == 78.0
        )

    def test_high_live_improves_uses_higher_live(self):
        assert (
            _apply_absorbing_floor_to_observed_extreme(80.0, 78.0, metric_is_low=False)
            == 80.0
        )

    def test_high_no_canonical_falls_back_to_live(self):
        assert (
            _apply_absorbing_floor_to_observed_extreme(76.0, None, metric_is_low=False)
            == 76.0
        )

    def test_low_revised_up_uses_canonical_running_min(self):
        """Low-metric symmetry: a later HIGHER live low cannot undercut the canonical running min."""
        assert (
            _apply_absorbing_floor_to_observed_extreme(50.0, 48.0, metric_is_low=True)
            == 48.0
        )

    def test_low_live_improves_uses_lower_live(self):
        assert (
            _apply_absorbing_floor_to_observed_extreme(46.0, 48.0, metric_is_low=True)
            == 46.0
        )

    def test_non_finite_live_uses_canonical(self):
        assert (
            _apply_absorbing_floor_to_observed_extreme(float("nan"), 78.0, metric_is_low=False)
            == 78.0
        )

    def test_both_none_returns_live(self):
        assert (
            _apply_absorbing_floor_to_observed_extreme(None, None, metric_is_low=False)
            is None
        )
