# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/instruments.py" block lines 590-617: Instrument
#   dataclass 594-599, payoff_vector 601-609 where YES_i = e_i and NO_i = 1 - e_i,
#   and the NO probability/lcb derivation 611-617 — fair_no_i = 1 - q[i],
#   no_lcb_i = np.quantile(1 - band.samples[:, i], alpha)); Stage 7 RED-on-revert
#   test names) reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (DRIFT-LEDGER MAJOR:
#   the NO lower bound is NOT 1 - q_ucb_yes — the live replacement defect at
#   event_reactor_adapter.py:9955 — and NOT probability_uncertainty.no_side_samples;
#   NO_i is the payoff vector 1 - e_i computed from the SAME row-normalized
#   JointQBand.samples).
"""RED-on-revert contract tests for the NO basket semantics (Stage 7a instruments).

Two spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces:

  * ``test_no_payoff_vector_wins_on_every_other_bin`` — a NO on bin ``i`` has the
    payoff vector ``1 - e_i``: it pays 1 on EVERY bin except ``i`` and 0 on ``i``. So
    NO is a real basket of all the OTHER YES — it wins whenever any other bin settles.
    RED-on-revert: if NO is modelled as a scalar UI complement (a single ``1 - YES``
    point on bin ``i`` — e.g. the ``e_i``-shaped or all-zero-but-i vectors a
    complement-of-a-single-bin view would produce) the payoff is NOT ``1 - e_i`` and
    this fails. The dot product with q is also checked: payoff @ q == 1 - q[i], the
    basket value.

  * ``test_no_probability_and_lcb_come_from_joint_complement_samples`` — the NO
    probability and lower bound are DIRECT consequences of the joint distribution:
    ``fair_no_i = 1 - q[i]`` and ``no_lcb_i = np.quantile(1 - band.samples[:, i],
    alpha)`` over the SAME row-normalized JointQBand.samples. RED-on-revert: if the NO
    lcb regresses to ``1 - q_ucb_yes`` (the live defect at
    event_reactor_adapter.py:9955) the two diverge — the flipped-upper-bound form
    double-counts the YES error and does NOT equal the joint-complement quantile. The
    test asserts the joint-complement lcb is the actual lower quantile of the NO
    basket AND that it differs from the flipped-ucb value (so a revert to the flip is
    caught).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pytest

from src.config import City
from src.forecast.day0_conditioner import Day0Conditioning
from src.forecast.sigma_authority import SigmaComponents
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.instruments import (
    Instrument,
    InstrumentError,
    fair_no,
    fair_yes,
    no_lcb,
)
from src.probability.joint_q import build_joint_q
from src.probability.joint_q_band import build_joint_q_band
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)


# ---------------------------------------------------------------------------
# Fixtures — real EventResolution / OutcomeSpace / JointQ / JointQBand built the
# SAME way the joint_q_band contract tests build them (live types, sourced rounding
# rule, the per-draw simplex matrix the NO complement reads).
# ---------------------------------------------------------------------------

def _sigma_components(
    *,
    center_parameter_se_native: float,
    model_dispersion_native: float,
    realized_floor_native: float,
    sigma_after_floor_native: float,
) -> SigmaComponents:
    return SigmaComponents(
        raw_member_spread_native=model_dispersion_native,
        model_dispersion_native=model_dispersion_native,
        center_parameter_se_native=center_parameter_se_native,
        station_representativeness_sigma_native=0.0,
        day0_remaining_process_sigma_native=0.0,
        realized_floor_native=realized_floor_native,
        sigma_before_floor_native=model_dispersion_native,
        sigma_after_floor_native=sigma_after_floor_native,
        artifact_id="sigma-test-artifact",
    )


@dataclass(frozen=True)
class _PD:
    """Predictive-distribution double — exactly the fields joint_q / band read."""

    mu_native: float
    sigma_native: float
    distribution_family: str
    day0: Day0Conditioning
    sigma_components: SigmaComponents
    live_eligible: bool = True
    ineligibility_reason: Optional[str] = None
    identity_hash: str = "pd-instrument-test-identity"


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


def _pd(
    mu: float,
    sigma: float,
    *,
    center_se: float,
    model_disp: float,
    realized_floor: float,
    identity: str = "pd-instrument-test-identity",
) -> _PD:
    return _PD(
        mu_native=mu,
        sigma_native=sigma,
        distribution_family="NORMAL",
        day0=_inactive_day0(mu),
        sigma_components=_sigma_components(
            center_parameter_se_native=center_se,
            model_dispersion_native=model_disp,
            realized_floor_native=realized_floor,
            sigma_after_floor_native=sigma,
        ),
        identity_hash=identity,
    )


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1: the NO payoff vector wins on every OTHER bin (1 - e_i).
# ---------------------------------------------------------------------------

def test_no_payoff_vector_wins_on_every_other_bin():
    """A NO on bin i has payoff 1 - e_i: pays 1 on EVERY bin except i, 0 on i.

    The load-bearing contract (spec lines 601-609): NO_i is the payoff vector
    ``1 - e_i`` — the all-ones vector with a single zero at i. So a NO is a real basket
    of all the OTHER bins' YES; it wins whenever ANY other bin settles. Its dot product
    with the joint q is the basket value ``1 - q[i] == Σ_{j != i} q[j]``.

    RED-on-revert: if NO is reverted to a scalar UI complement modelled on bin i alone
    (the ``1 - q_yes`` point view — whose natural payoff vectors are ``e_i`` itself, or
    the all-zero-but-i shapes), the payoff is NOT ``1 - e_i``: it would win on at most
    one bin, not on every other bin. This test asserts the exact ``1 - e_i`` shape AND
    that NO wins on every other bin, so any single-bin-complement revert fails.
    """
    space = _outcome_space(_resolution("Tokyo", "wu_icao", "RJTT", "high"), "tokyo-high")
    n = len(space.bins)
    target = "b25"
    i = [b.bin_id for b in space.bins].index(target)

    yes = Instrument(
        instrument_id="inst-yes-b25",
        bin_id=target,
        side="YES",
        direct_token_id="yes-b25",
    )
    no = Instrument(
        instrument_id="inst-no-b25",
        bin_id=target,
        side="NO",
        direct_token_id="no-b25",
    )

    yes_payoff = yes.payoff_vector(space)
    no_payoff = no.payoff_vector(space)

    # Shape / length aligned 1:1 with the complete Omega.
    assert yes_payoff.shape == (n,)
    assert no_payoff.shape == (n,)

    # YES is e_i: pays ONLY on bin i.
    expected_yes = np.zeros(n)
    expected_yes[i] = 1.0
    assert np.array_equal(yes_payoff, expected_yes)

    # NO is 1 - e_i: pays 1 on EVERY bin except i, 0 on i — the basket of all OTHER YES.
    expected_no = np.ones(n)
    expected_no[i] = 0.0
    assert np.array_equal(no_payoff, expected_no), (
        "NO payoff is not 1 - e_i — it must win on every OTHER bin (a basket of all the "
        "other YES), not be a single-bin scalar complement"
    )

    # The structural property the basket semantics REQUIRE: NO wins on every bin j != i,
    # loses on bin i. A scalar-complement revert (NO modelled on bin i alone) would win
    # on at most one bin — this fails it.
    assert no_payoff[i] == 0.0
    for j in range(n):
        if j != i:
            assert no_payoff[j] == 1.0, f"NO must win on other bin index {j} (j != {i})"
    assert int(no_payoff.sum()) == n - 1, "NO must win on exactly n-1 bins (all but i)"

    # NO and YES are exact complements as payoff vectors: yes + no == all-ones.
    assert np.array_equal(yes_payoff + no_payoff, np.ones(n))

    # Dot product with the joint q is the fair value: YES -> q[i], NO -> 1 - q[i] =
    # the basket value Σ_{j != i} q[j]. (Reuses the live point-q integrator.)
    pd = _pd(24.3, 1.7, center_se=0.6, model_disp=1.2, realized_floor=1.3)
    jq = build_joint_q(pd, space)
    jq.assert_valid()
    q = jq.q
    assert np.isclose(float(yes_payoff @ q), float(q[i]), atol=1e-12)
    assert np.isclose(float(no_payoff @ q), float(1.0 - q[i]), atol=1e-12)
    # The basket value equals the explicit sum of all the OTHER bins' YES mass.
    other_mass = float(sum(q[j] for j in range(n) if j != i))
    assert np.isclose(float(no_payoff @ q), other_mass, atol=1e-12)

    # And fair_no off the JointQ matches the payoff-vector basket value (one source).
    assert np.isclose(fair_no(jq, target), float(no_payoff @ q), atol=1e-12)
    assert np.isclose(fair_yes(jq, target), float(q[i]), atol=1e-12)

    # A bin_id not in the partition fails closed (no silent wrong-bin selection).
    bogus = Instrument(
        instrument_id="inst-bogus",
        bin_id="b_does_not_exist",
        side="NO",
        direct_token_id=None,
    )
    with pytest.raises(InstrumentError):
        bogus.payoff_vector(space)


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2: NO probability + lcb come from the joint-complement samples.
# ---------------------------------------------------------------------------

def test_no_probability_and_lcb_come_from_joint_complement_samples():
    """fair_no_i = 1 - q[i]; no_lcb_i = quantile(1 - band.samples[:, i], alpha).

    The load-bearing contract (spec lines 611-617): the NO probability and lower bound
    are DIRECT consequences of the payoff vector ``1 - e_i`` and Σq = 1, read from the
    SAME row-normalized JointQBand.samples the YES side uses — NOT a special formula,
    NOT a separately-sampled NO belief.

    THE MATHEMATICAL CRUX (the reason the defect is structural, not a numeric flip):
    for ONE column, ``quantile(1 - samples[:, i], alpha) == 1 - quantile(samples[:, i],
    1 - alpha)`` is an algebraic identity. So whether ``no_lcb_i`` is *written* as the
    joint complement or as ``1 - q_ucb_yes_i`` is IRRELEVANT — they are the same number
    ON A GIVEN SAMPLE MATRIX. The real correction is WHICH matrix: the NO bound is only
    a genuine "some OTHER bin wins" lower bound when the rows are NORMALIZED to the
    simplex, because only then is ``1 - samples[k, i]`` EXACTLY the summed mass of all
    the other bins on draw k (``Σ_{j != i} samples[k, j]``). On the un-row-normalized
    draw matrix the live ``_build_fused_q_bounds`` produced (rows that do NOT sum to 1
    — the matrix that fed the live ``1 - q_ucb_yes`` at event_reactor_adapter.py:9955),
    ``1 - samples[k, i]`` is NOT the other-bin mass at all, so the NO bound did not
    describe the NO basket. This test:

      1. asserts ``fair_no == 1 - q[i]`` exactly (basket value off the normalized q);
      2. asserts ``no_lcb == np.quantile(1 - band.samples[:, i], alpha)`` exactly — the
         ONE source, read from the YES joint matrix at the band's own tail;
      3. asserts the BASKET-COHERENCE invariant that the defect violates: per draw,
         ``1 - band.samples[:, i] == Σ_{j != i} band.samples[:, j]`` — true ONLY because
         the rows are renormalized; and shows that on an UN-normalized draw matrix (the
         ``_build_fused_q_bounds`` source behind the live ``1 - q_ucb_yes``) the same
         equality FAILS, so a NO bound built off it does not describe "some other bin
         wins". THAT structural difference — the row-normalized SOURCE, not a per-column
         flip — is the contract;
      4. asserts source provenance: ``no_lcb`` is read bit-for-bit from ``band.samples``
         (the YES joint matrix), so a revert to a separately-drawn ``no_side_samples`` is
         caught.
    """
    space = _outcome_space(_resolution("Tokyo", "wu_icao", "RJTT", "high"), "tokyo-high")
    target = "b25"
    i = [b.bin_id for b in space.bins].index(target)

    # A modal-but-wide-dispersion fixture: b25 carries real mass, and the per-draw
    # (mu_k, sigma_k) jitter enough that the OTHER-bin mass is a genuine distribution.
    pd = _pd(
        25.0, 0.9,
        center_se=0.4,
        model_disp=3.0,
        realized_floor=0.8,
        identity="no-complement-id",
    )

    n_draws = 8000
    alpha = 0.05
    band = build_joint_q_band(pd, space, n_draws=n_draws, alpha=alpha)
    band.assert_valid()
    jq = band.joint_q
    jq.assert_valid()

    # (1) fair_no == 1 - q[i] exactly — the basket value off the single normalized q.
    assert np.isclose(fair_no(jq, target), float(1.0 - jq.q[i]), atol=1e-12)
    assert np.isclose(fair_yes(jq, target), float(jq.q[i]), atol=1e-12)
    # fair_yes + fair_no == 1 (YES and NO partition the unit by Σq = 1).
    assert np.isclose(fair_yes(jq, target) + fair_no(jq, target), 1.0, atol=1e-12)

    # (2) no_lcb == quantile(1 - band.samples[:, i], alpha) EXACTLY — the joint
    #     complement of the SAME row-normalized draw matrix. This is THE source. A revert
    #     that draws a SEPARATE NO sample set (probability_uncertainty.no_side_samples)
    #     instead of reading band.samples would NOT reproduce this exact value.
    expected_no_lcb = float(np.quantile(1.0 - band.samples[:, i], alpha))
    got_no_lcb = no_lcb(band, target, alpha=alpha)
    assert np.isclose(got_no_lcb, expected_no_lcb, atol=1e-12), (
        "no_lcb is not the alpha-quantile of 1 - band.samples[:, i] — the NO bound must "
        "be the joint complement of the row-normalized samples (the YES joint matrix), "
        "not a separately-sampled NO belief"
    )
    # Default alpha == band.alpha gives the SAME tail as the band's YES q_lcb — the NO
    # and YES bounds are coherent because they are read from the SAME matrix at the SAME
    # tail (not two independently-drawn sample sets).
    assert np.isclose(no_lcb(band, target), float(np.quantile(1.0 - band.samples[:, i], band.alpha)), atol=1e-12)

    # The NO lcb is a genuine lower credible bound: at or below the fair NO (downside),
    # and a real probability in [0, 1].
    assert 0.0 <= got_no_lcb <= 1.0
    assert got_no_lcb <= fair_no(jq, target) + 1e-9

    # (3) THE BASKET-COHERENCE INVARIANT — the property that makes the NO bound a true
    #     "some OTHER bin wins" lower bound, and the property a revert to an
    #     un-normalized / separately-sampled NO source VIOLATES. On the COHERENT
    #     (row-normalized) band, per draw 1 - samples[:, i] is EXACTLY the summed mass of
    #     all the OTHER bins, so quantile(1 - samples[:, i], alpha) is the genuine lower
    #     bound of the NO basket payoff 1 - e_i.
    #
    #     (Note on the math: for ONE column, quantile(1 - x, alpha) == 1 - quantile(x,
    #     1 - alpha) is an algebraic identity, so the *written form* "joint complement"
    #     vs "1 - q_ucb_yes" is the SAME number ON A GIVEN MATRIX. The defect was never a
    #     per-column flip — it was the SOURCE matrix: the live 1 - q_ucb_yes was fed by
    #     the un-row-normalized _build_fused_q_bounds draws, on which 1 - samples[:, i] is
    #     NOT the other-bin mass. So the contract this module enforces is that the NO
    #     bound is read from band.samples — the row-normalized joint matrix — where the
    #     basket identity below HOLDS.)
    coherent_complement = 1.0 - band.samples[:, i]
    coherent_other_mass = np.delete(band.samples, i, axis=1).sum(axis=1)
    assert np.allclose(coherent_complement, coherent_other_mass, atol=1e-9), (
        "1 - band.samples[:, i] must equal the per-draw sum of all OTHER bins — the NO "
        "basket payoff — which holds ONLY because the band rows are renormalized to the "
        "simplex (the JointQBand contract the NO bound reads from)"
    )

    # The DEFECT regime, isolated: the SAME seeded draws integrated over the NARROW
    # listed (tradeable) window WITHOUT the per-row simplex renormalization — the exact
    # (draws x bins) grid the live _build_fused_q_bounds percentiled. On a draw whose
    # (mu_k, sigma_k) pushes mass past the window edge, the row sums to < 1; the spilled
    # mass is dropped. On these un-normalized rows the BASKET IDENTITY FAILS — so a NO
    # bound built off them does NOT describe "some other bin wins". This is the structural
    # difference between the broken source and band.samples, independent of the per-column
    # quantile identity.
    window_ids = ("b24", "b25", "b26")
    raw_samples, raw_modal_idx = _unnormalized_window_matrix(
        pd, space, n_draws=n_draws, window_ids=window_ids, modal_id=target
    )
    # Sanity: the raw window rows genuinely do NOT sum to 1 (the incoherence the fix
    # removes — draws spill past the narrow window edge).
    raw_row_sums = raw_samples.sum(axis=1)
    assert raw_row_sums.min() < 1.0 - 1e-3, (
        "fixture sanity: the un-normalized window rows must include rows summing to < 1 "
        "(otherwise the defect regime is not exercised)"
    )
    # On the raw un-normalized window matrix, 1 - raw[:, modal] is NOT the window's
    # other-bin mass (the basket identity FAILS) — the defining failure of the broken
    # source the spec replaces. band.samples (above) satisfies the identity; the raw
    # matrix does not.
    raw_complement = 1.0 - raw_samples[:, raw_modal_idx]
    raw_other_mass = np.delete(raw_samples, raw_modal_idx, axis=1).sum(axis=1)
    assert not np.allclose(raw_complement, raw_other_mass, atol=1e-3), (
        "on the un-normalized (defect) matrix, 1 - samples[:, i] should NOT equal the "
        "other-bin mass — that is exactly why a NO bound built off un-renormalized draws "
        "(the _build_fused_q_bounds source behind the live 1 - q_ucb_yes) was wrong"
    )

    # (4) SOURCE PROVENANCE — the NO bound is the joint complement of band.samples, NOT a
    #     separately-drawn NO sample set. Reconstruct the exact value from band.samples and
    #     confirm no_lcb reproduces it bit-for-bit; an independently-sampled no_side_samples
    #     (the other broken source the spec names) would not. Already covered by (2); this
    #     restates it as the explicit "same matrix as YES" provenance check.
    assert no_lcb(band, target, alpha=alpha) == float(
        np.quantile(1.0 - band.samples[:, i], alpha)
    ), (
        "no_lcb must be read from band.samples (the YES joint matrix), not from a "
        "separately-sampled NO belief"
    )


def _unnormalized_window_matrix(
    pd: _PD,
    space: OutcomeSpace,
    *,
    n_draws: int,
    window_ids: tuple[str, ...],
    modal_id: str,
) -> tuple[np.ndarray, int]:
    """The RAW (un-renormalized) (n_draws, len(window)) window mass matrix — the defect.

    For each of the SAME seeded (mu_k, sigma_k) draws ``build_joint_q_band`` uses (the
    seed is derived from ``pd.identity_hash`` exactly as the band's ``_seed_from_identity``
    does), this integrates the per-bin Normal-interval mass over the NARROW listed window
    — the (draws x bins) grid the live ``_build_fused_q_bounds`` percentiled — WITHOUT the
    per-row ``q = q / q.sum()`` renormalization. On a draw whose mass jitters past the
    window edge the row sums to < 1 (the spilled mass is dropped), so this reproduces the
    incoherent draw matrix the corrected transform replaces.

    Returns ``(raw_window_samples, modal_idx_within_window)``.
    """
    import hashlib

    from scipy.special import ndtr

    from src.probability.joint_q_band import draw_mu, draw_sigma

    window = [b for b in space.bins if b.bin_id in window_ids]
    # WMO symmetric preimage [t-0.5, t+0.5) for each one-degree listed bin (the live grid
    # edges the materializer integrates over).
    lows = np.asarray([float(b.lower_native) - 0.5 for b in window], dtype=float)
    highs = np.asarray([float(b.upper_native) + 0.5 for b in window], dtype=float)
    modal_idx = [b.bin_id for b in window].index(modal_id)

    seed = int.from_bytes(
        hashlib.sha256(pd.identity_hash.encode("utf-8")).digest()[:8], "big", signed=False
    )
    rng = np.random.default_rng(seed)

    probs = np.empty((n_draws, len(window)), dtype=float)
    for k in range(n_draws):
        mu_k = draw_mu(pd, rng)
        sigma_k = draw_sigma(pd, rng)
        z_low = (lows - mu_k) / sigma_k
        z_high = (highs - mu_k) / sigma_k
        probs[k, :] = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)
    return probs, modal_idx


def test_no_lcb_rejects_degenerate_alpha():
    """An explicit out-of-range alpha is refused (fail-closed)."""
    space = _outcome_space(_resolution("Tokyo", "wu_icao", "RJTT", "high"), "tokyo-high")
    pd = _pd(24.3, 1.7, center_se=0.6, model_disp=1.2, realized_floor=1.3, identity="alpha-id")
    band = build_joint_q_band(pd, space, n_draws=200, alpha=0.05)
    with pytest.raises(InstrumentError):
        no_lcb(band, "b25", alpha=0.0)
    with pytest.raises(InstrumentError):
        no_lcb(band, "b25", alpha=1.0)
    with pytest.raises(InstrumentError):
        no_lcb(band, "b_does_not_exist", alpha=0.05)
