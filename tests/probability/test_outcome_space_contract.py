# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md (OutcomeSpace section,
#   Stage 1 RED-on-revert: test_incomplete_family_fails_closed_and_complete_
#   family_sums_mass). q-kernel rebuild Stage 1 foundation.
"""Contract tests for OutcomeSpace (the complete MECE outcome partition / Omega).

RED-on-revert: these tests fail if OutcomeSpace.validate stops failing closed on
an incomplete family, or if a complete partition's settlement-preimage mass stops
summing to 1.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from scipy.stats import norm

from src.config import City
from src.contracts.settlement_semantics import settlement_preimage_offsets
from src.probability.event_resolution import event_resolution_for_city
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    OutcomeSpaceError,
    compute_topology_hash,
)


def _tokyo_resolution():
    """A real WU °C city → wmo_half_up rounding, sourced from settlement_semantics."""
    tokyo = City(
        name="Tokyo",
        lat=35.55,
        lon=139.78,
        timezone="Asia/Tokyo",
        settlement_unit="C",
        cluster="asia",
        wu_station="RJTT",
        settlement_source_type="wu_icao",
    )
    return event_resolution_for_city(tokyo, date(2026, 6, 14), "high")


def _bin(bin_id, lo, hi, label, rule, *, executable=True):
    return OutcomeBin(
        bin_id=bin_id,
        condition_id=f"cond-{bin_id}",
        label=label,
        lower_native=lo,
        upper_native=hi,
        yes_token_id=f"yes-{bin_id}",
        no_token_id=f"no-{bin_id}",
        executable=executable,
        rounding_rule=rule,
    )


def _complete_tokyo_bins(rule):
    """A complete °C integer partition: (-inf,20], 21, 22, 23, [24,+inf)."""
    return (
        _bin("b0", None, 20.0, "20°C or below", rule, executable=False),
        _bin("b1", 21.0, 21.0, "21°C", rule),
        _bin("b2", 22.0, 22.0, "22°C", rule),
        _bin("b3", 23.0, 23.0, "23°C", rule),
        _bin("b4", 24.0, None, "24°C or above", rule, executable=False),
    )


def _bin_mass(b: OutcomeBin, mu: float, sigma: float, rule: str) -> float:
    lo_off, hi_off = settlement_preimage_offsets(rule)
    lo = -np.inf if b.lower_native is None else b.lower_native + lo_off
    hi = np.inf if b.upper_native is None else b.upper_native + hi_off
    return float(norm.cdf(hi, mu, sigma) - norm.cdf(lo, mu, sigma))


def test_incomplete_family_fails_closed_and_complete_family_sums_mass():
    res = _tokyo_resolution()
    rule = res.rounding_rule
    bins = _complete_tokyo_bins(rule)

    # --- complete family validates and its settlement-preimage mass sums to 1 ---
    space = OutcomeSpace(
        family_id="tokyo-high-2026-06-14",
        resolution=res,
        bins=bins,
        topology_hash=compute_topology_hash("tokyo-high-2026-06-14", res, bins),
    )
    space.validate()  # must not raise

    total = sum(_bin_mass(b, mu=21.4, sigma=1.4, rule=rule) for b in bins)
    assert total == pytest.approx(1.0, abs=1e-9)

    # --- incomplete family (a missing interior bin → a gap) fails CLOSED ---
    gapped = (bins[0], bins[1], bins[3], bins[4])  # drop 22°C
    with pytest.raises(OutcomeSpaceError):
        OutcomeSpace("tokyo-gap", res, gapped, "h").validate()

    # --- a single-bin "family" fails closed (needs >= 2 bins) ---
    with pytest.raises(OutcomeSpaceError):
        OutcomeSpace("tokyo-single", res, (bins[1],), "h").validate()

    # --- a bin declaring a different rounding rule than the family fails closed ---
    mismatched = (
        _bin("m0", None, 20.0, "20°C or below", rule, executable=False),
        _bin("m1", 21.0, 21.0, "21°C", "oracle_truncate"),  # wrong rule
        _bin("m2", 22.0, None, "22°C or above", rule, executable=False),
    )
    with pytest.raises(OutcomeSpaceError):
        OutcomeSpace("tokyo-rulemismatch", res, mismatched, "h").validate()
