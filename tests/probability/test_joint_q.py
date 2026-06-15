# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/joint_q.py" block lines 505-544: JointQ dataclass
#   509-521 + assert_valid; build_joint_q point integration 523-541 incl. the
#   NORMAL / DAY0_HIGH_MAX_NORMAL / DAY0_LOW_MIN_NORMAL family switch and the
#   q = q/q.sum() normalization; Stage 6 RED-on-revert lines 1138-1139) reconciled
#   against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (HK
#   oracle_truncate MUST thread into the q integration; do NOT default WMO).
"""RED-on-revert contract tests for build_joint_q (the ONE normalized joint q).

Two spec-named tests fail if the corrected transformation is reverted:

  * ``test_q_sum_one_for_every_family`` — q sums to EXACTLY 1 (within 1e-9) for
    every distribution family (NORMAL, DAY0_HIGH_MAX_NORMAL, DAY0_LOW_MIN_NORMAL).
    Fails if the single ``q = q / q.sum()`` normalization is removed (the old fused
    path integrated per-bin masses without renormalizing the joint).

  * ``test_hk_oracle_truncate_threaded_into_q_integration`` — the HK
    ``oracle_truncate`` rounding rule reaches the q integration and produces a
    DIFFERENT joint q than WMO half-up for the same (mu, sigma) and bin labels.
    Fails if the integrator defaults to WMO for HK (the build_emos_q defect: the
    rounding_rule was dropped at the seam and silently defaulted WMO).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pytest

from src.config import City
from src.forecast.day0_conditioner import Day0Conditioning
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.joint_q import JointQ, JointQError, build_joint_q
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)


# ---------------------------------------------------------------------------
# A minimal predictive-distribution test double.
#
# build_joint_q reads exactly these fields from PredictiveDistribution
# (mu_native, sigma_native, distribution_family, day0.observed_extreme_native,
# live_eligible, ineligibility_reason, identity_hash). The double carries those
# verbatim so the q-integration unit is isolated from the heavy forecast-spine
# sub-objects (CenterEstimate / AppliedDebias / SigmaComponents), which q never
# touches. ``day0`` is a REAL Day0Conditioning (the only sub-object q reads).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _PD:
    mu_native: float
    sigma_native: float
    distribution_family: str
    day0: Day0Conditioning
    live_eligible: bool = True
    ineligibility_reason: Optional[str] = None
    identity_hash: str = "pd-test-identity"


def _inactive_day0(center: float) -> Day0Conditioning:
    return Day0Conditioning(
        active=False,
        observed_extreme_native=None,
        support_lower_native=None,
        support_upper_native=None,
        center_before_native=center,
        center_after_native=center,
        status="NO_DAY0",
    )


def _high_day0(center_before: float, observed_high: float) -> Day0Conditioning:
    after = max(center_before, observed_high)
    return Day0Conditioning(
        active=True,
        observed_extreme_native=observed_high,
        support_lower_native=observed_high,
        support_upper_native=None,
        center_before_native=center_before,
        center_after_native=after,
        status="HIGH_CLAMPED",
    )


def _low_day0(center_before: float, observed_low: float) -> Day0Conditioning:
    after = min(center_before, observed_low)
    return Day0Conditioning(
        active=True,
        observed_extreme_native=observed_low,
        support_lower_native=None,
        support_upper_native=observed_low,
        center_before_native=center_before,
        center_after_native=after,
        status="LOW_CLAMPED",
    )


# ---------------------------------------------------------------------------
# Real EventResolution / OutcomeSpace fixtures (live types, sourced rounding rule).
# ---------------------------------------------------------------------------

def _resolution(city_name: str, source_type: str, wu_station: str, metric: str) -> EventResolution:
    city = City(
        name=city_name,
        lat=22.3,
        lon=114.17,
        timezone="Asia/Hong_Kong" if source_type == "hko" else "Asia/Tokyo",
        settlement_unit="C",
        cluster="asia",
        wu_station=wu_station,
        settlement_source_type=source_type,
    )
    return event_resolution_for_city(city, date(2026, 6, 14), metric)


def _bin(bin_id: str, lo, hi, label: str, rule: str, *, executable: bool = True) -> OutcomeBin:
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


def _complete_bins(rule: str) -> tuple[OutcomeBin, ...]:
    """A complete °C integer partition: (-inf,20], 21..29, [30,+inf)."""
    bins = [_bin("b_low", None, 20.0, "20°C or below", rule, executable=False)]
    for t in range(21, 30):
        bins.append(_bin(f"b{t}", float(t), float(t), f"{t}°C", rule))
    bins.append(_bin("b_high", 30.0, None, "30°C or above", rule, executable=False))
    return tuple(bins)


def _outcome_space(resolution: EventResolution, family_id: str) -> OutcomeSpace:
    rule = resolution.rounding_rule
    bins = _complete_bins(rule)
    space = OutcomeSpace(
        family_id=family_id,
        resolution=resolution,
        bins=bins,
        topology_hash=compute_topology_hash(family_id, resolution, bins),
    )
    space.validate()  # complete MECE partition; must not raise
    return space


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1: q sums to 1 for EVERY family (spec line 1138).
# ---------------------------------------------------------------------------

def test_q_sum_one_for_every_family():
    """build_joint_q normalizes to Sigma q == 1 over the COMPLETE Omega for every family.

    The load-bearing contract (spec lines 500-501: "No mass leak", "No
    executable-subset renormalization"): q is integrated over the COMPLETE
    partition — including the non-tradeable tail/shoulder bins — and normalized
    ONCE over that complete set. The tail bins carry their REAL mass; the
    executable subset alone does NOT sum to 1.

    RED-on-revert: if build_joint_q regresses to integrating / normalizing only the
    executable subset (the defect the spec replaces), the open tail mass leaks and
    the executable bins are renormalized to 1 among themselves — so the
    non-executable shoulder's positive mass and the executable-subset < 1
    assertions below fail.
    """
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    tokyo_low = _resolution("Tokyo", "wu_icao", "RJTT", "low")
    hk_high = _resolution("Hong Kong", "hko", "", "high")

    cases = [
        # NORMAL
        _PD(
            mu_native=24.3,
            sigma_native=1.7,
            distribution_family="NORMAL",
            day0=_inactive_day0(24.3),
        ),
        # DAY0_HIGH_MAX_NORMAL — observed running high pulls the support up.
        _PD(
            mu_native=24.3,
            sigma_native=1.7,
            distribution_family="DAY0_HIGH_MAX_NORMAL",
            day0=_high_day0(center_before=24.3, observed_high=25.0),
        ),
        # DAY0_LOW_MIN_NORMAL — observed running low caps the support.
        _PD(
            mu_native=24.3,
            sigma_native=1.7,
            distribution_family="DAY0_LOW_MIN_NORMAL",
            day0=_low_day0(center_before=24.3, observed_low=23.0),
        ),
    ]
    spaces = [
        _outcome_space(tokyo_high, "tokyo-high"),
        _outcome_space(tokyo_high, "tokyo-high"),
        _outcome_space(tokyo_low, "tokyo-low"),
    ]
    # And the HK NORMAL family, to prove the sum-one contract holds under the
    # asymmetric oracle_truncate preimage too.
    cases.append(
        _PD(
            mu_native=24.3,
            sigma_native=1.7,
            distribution_family="NORMAL",
            day0=_inactive_day0(24.3),
        )
    )
    spaces.append(_outcome_space(hk_high, "hk-high"))

    for pd, space in zip(cases, spaces):
        jq = build_joint_q(pd, space)
        assert isinstance(jq, JointQ)
        # The structural invariant: Sigma q == 1 within 1e-9, by construction.
        assert abs(float(jq.q.sum()) - 1.0) <= 1e-9
        assert jq.q_sum == pytest.approx(1.0, abs=1e-9)
        # assert_valid re-proves q >= 0 and Sigma q == 1.
        jq.assert_valid()
        # q is aligned 1:1 with the bins and mirrored into q_by_bin_id.
        assert len(jq.q) == len(space.bins)
        assert set(jq.q_by_bin_id) == {b.bin_id for b in space.bins}
        for b, m in zip(space.bins, jq.q):
            assert jq.q_by_bin_id[b.bin_id] == pytest.approx(float(m))

    # --- No mass leak / no executable-subset renormalization (spec 500-501) ------
    # A center near the open-low shoulder puts substantial mass on the
    # NON-EXECUTABLE tail bin. The complete-Omega integration must KEEP that mass
    # on the tail and normalize over the complete set, so the EXECUTABLE subset
    # alone sums to STRICTLY LESS than 1. An executable-subset renormalization (the
    # defect) would leak the tail mass and force the executable bins to sum to 1.
    leak_space = _outcome_space(tokyo_high, "tokyo-high")
    leak_pd = _PD(
        mu_native=21.5,  # near the (-inf, 20] open-low shoulder
        sigma_native=2.5,
        distribution_family="NORMAL",
        day0=_inactive_day0(21.5),
    )
    leak_q = build_joint_q(leak_pd, leak_space)
    leak_q.assert_valid()
    # The non-executable open-low shoulder carries real, substantial mass.
    non_exec_ids = [b.bin_id for b in leak_space.bins if not b.executable]
    non_exec_mass = sum(leak_q.q_by_bin_id[bid] for bid in non_exec_ids)
    assert non_exec_mass > 0.2, (
        "non-executable tail mass leaked — q was integrated/normalized over the "
        "executable subset (spec 500-501 violation)"
    )
    exec_ids = [b.bin_id for b in leak_space.bins if b.executable]
    exec_mass = sum(leak_q.q_by_bin_id[bid] for bid in exec_ids)
    assert exec_mass < 1.0 - 1e-9, (
        "executable subset renormalized to 1 — the tail mass was leaked"
    )
    # But the COMPLETE set still sums to exactly 1 (one normalization, no leak).
    assert exec_mass + non_exec_mass == pytest.approx(1.0, abs=1e-9)


def test_q_sum_one_holds_when_day0_zeros_impossible_bins():
    """The DAY0 collapse zeros impossible bins yet q STILL sums to 1.

    A HIGH-market day0 with an observed running high of 25 makes every bin entirely
    below 25 impossible (q == 0), and collapses all remaining mass below the
    observed bin onto it. The renormalization is what keeps Sigma q == 1 despite
    the mass redistribution — RED-on-revert against dropping the normalization.
    """
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo_high, "tokyo-high")
    pd = _PD(
        mu_native=24.3,
        sigma_native=1.7,
        distribution_family="DAY0_HIGH_MAX_NORMAL",
        day0=_high_day0(center_before=24.3, observed_high=25.0),
    )
    jq = build_joint_q(pd, space)
    jq.assert_valid()
    assert abs(float(jq.q.sum()) - 1.0) <= 1e-9

    # Bins whose settlement preimage lies ENTIRELY below the observed high carry
    # q == 0 by the settlement-conditioned transform. Under WMO the 24°C bin's
    # preimage is [23.5, 24.5), entirely below obs_high=25.0; likewise 21..24 and
    # the open-low shoulder. (The 25°C bin's preimage [24.5, 25.5) STRADDLES 25.0,
    # so it is the observed bin, not an impossible one.)
    for label in ("b_low", "b21", "b22", "b23", "b24"):
        assert jq.q_by_bin_id[label] == 0.0, (
            f"bin {label} (preimage entirely below observed high 25) must be impossible"
        )
    # The 25°C bin (the observed/straddle bin) absorbs all remaining mass below it,
    # so it carries strictly positive probability.
    assert jq.q_by_bin_id["b25"] > 0.0


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2: HK oracle_truncate threads into the q integration.
# ---------------------------------------------------------------------------

def test_hk_oracle_truncate_threaded_into_q_integration():
    """HK oracle_truncate reaches the q integration and shifts mass vs WMO.

    The asymmetric HK preimage [t, t+1) integrates a DIFFERENT interval than the
    symmetric WMO [t-0.5, t+0.5) for the same bin label, so the resulting joint q
    differs bin-by-bin. The build_emos_q defect dropped the rounding_rule and
    silently used WMO for HK; this test fails if build_joint_q regresses to that.

    Construction: a Hong Kong family (oracle_truncate) and a WMO family over the
    SAME integer bin labels and the SAME (mu, sigma). If the rule were NOT threaded
    (defaulted to WMO), the two joint q vectors would be IDENTICAL. They are not.
    """
    hk = _resolution("Hong Kong", "hko", "", "high")
    tokyo = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    assert hk.rounding_rule == "oracle_truncate"
    assert tokyo.rounding_rule == "wmo_half_up"

    hk_space = _outcome_space(hk, "hk-high")
    wmo_space = _outcome_space(tokyo, "tokyo-high")
    # Same labels on both sides (the partitions are constructed identically except
    # for the per-bin rounding_rule carried from the resolution).
    assert [b.label for b in hk_space.bins] == [b.label for b in wmo_space.bins]

    mu, sigma = 24.3, 1.4
    hk_pd = _PD(mu_native=mu, sigma_native=sigma, distribution_family="NORMAL",
                day0=_inactive_day0(mu))
    wmo_pd = _PD(mu_native=mu, sigma_native=sigma, distribution_family="NORMAL",
                 day0=_inactive_day0(mu))

    hk_q = build_joint_q(hk_pd, hk_space)
    wmo_q = build_joint_q(wmo_pd, wmo_space)

    # Both normalize to 1...
    hk_q.assert_valid()
    wmo_q.assert_valid()
    # ...but the joint distributions are genuinely DIFFERENT — the rounding rule
    # was threaded, not defaulted. If HK silently used WMO, these would be equal.
    hk_vec = hk_q.q
    wmo_vec = wmo_q.q
    assert hk_vec.shape == wmo_vec.shape
    assert not np.allclose(hk_vec, wmo_vec, atol=1e-9), (
        "HK oracle_truncate q is identical to WMO q — the rounding_rule was "
        "dropped at the q integration seam (the build_emos_q defect)."
    )

    # Direction check: HK's preimage [t, t+1) sits to the RIGHT of WMO's
    # [t-0.5, t+0.5) for the same label, so for an interior bin straddling mu the
    # mass profile is shifted. Concretely the 24°C bin under HK integrates
    # [24, 25) while WMO integrates [23.5, 24.5); with mu=24.3 these are not equal.
    assert hk_q.q_by_bin_id["b24"] != pytest.approx(wmo_q.q_by_bin_id["b24"], abs=1e-9)

    # The q_source records the family; the rounding rule is on the resolution.
    assert hk_q.q_source == "SETTLEMENT_STATION_NORMAL_V1"
    assert hk_q.omega.resolution.rounding_rule == "oracle_truncate"


# ---------------------------------------------------------------------------
# Fail-closed contracts (no degenerate / width-less q served).
# ---------------------------------------------------------------------------

def test_ineligible_distribution_is_refused_not_served_degenerate():
    tokyo = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo, "tokyo-high")
    ineligible = _PD(
        mu_native=24.3,
        sigma_native=0.0,
        distribution_family="NORMAL",
        day0=_inactive_day0(24.3),
        live_eligible=False,
        ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING",
    )
    with pytest.raises(JointQError):
        build_joint_q(ineligible, space)


def test_day0_family_without_observed_extreme_is_refused():
    tokyo = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo, "tokyo-high")
    # A DAY0 family whose day0 carries no observed extreme is incoherent — refuse.
    broken = _PD(
        mu_native=24.3,
        sigma_native=1.7,
        distribution_family="DAY0_HIGH_MAX_NORMAL",
        day0=_inactive_day0(24.3),  # observed_extreme_native is None
    )
    with pytest.raises(JointQError):
        build_joint_q(broken, space)


def test_identity_hash_is_deterministic_and_pd_linked():
    tokyo = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo, "tokyo-high")
    pd = _PD(mu_native=24.3, sigma_native=1.7, distribution_family="NORMAL",
             day0=_inactive_day0(24.3), identity_hash="pd-xyz")
    a = build_joint_q(pd, space)
    b = build_joint_q(pd, space)
    assert a.identity_hash == b.identity_hash
    assert a.predictive_distribution_id == "pd-xyz"
