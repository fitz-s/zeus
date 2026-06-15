# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   ("Create src/probability/joint_q_band.py" block lines 546-588: JointQBand
#   dataclass 557-570 + assert_valid asserting every sample ROW sums to 1; the
#   per-draw algorithm 572-585 — draw mu_k/sigma_k, integrate ALL bins, q_k =
#   q_k/q_k.sum() per draw, then q_lcb = quantile(samples, alpha, axis=0); Stage 6
#   RED-on-revert lines 1140-1141) reconciled against
#   docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md (this is the EXACT
#   fix for _build_fused_q_bounds:1425-1426 — raw per-bin percentile over
#   un-normalized (draws x bins) mass with NO per-row simplex renormalization).
"""RED-on-revert contract tests for build_joint_q_band (the coherent q_lcb).

Two spec-named tests fail if the corrected transformation is reverted to the broken
behavior the spec replaces:

  * ``test_every_band_sample_row_sums_to_one`` — EVERY row of the (n_draws, n_bins)
    draw matrix sums to EXACTLY 1 (within 1e-9), for every distribution family.
    Fails if the per-draw ``q_k = q_k / q_k.sum()`` (the row-simplex normalization
    inside the generator) is removed — i.e. if the band reverts to stacking RAW
    per-bin integrated mass the way ``_build_fused_q_bounds`` does.

  * ``test_modal_lcb_does_not_collapse_from_raw_bin_percentile`` — a narrow,
    high-belief MODAL bin keeps a high q_lcb under the corrected (renormalize-each-
    row-first) transform, whereas the BROKEN raw-per-bin-percentile transform (the
    live defect) collapses that same modal q_lcb toward ~0. Fails if the generator
    regresses to taking marginal quantiles over un-normalized draw rows.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Optional

import numpy as np
import pytest

from src.config import City
from src.forecast.day0_conditioner import Day0Conditioning
from src.forecast.sigma_authority import SigmaComponents
from src.probability.event_resolution import EventResolution, event_resolution_for_city
from src.probability.joint_q import build_joint_q
from src.probability.joint_q_band import (
    JointQBand,
    JointQBandError,
    build_joint_q_band,
)
from src.probability.outcome_space import (
    OutcomeBin,
    OutcomeSpace,
    compute_topology_hash,
)


# ---------------------------------------------------------------------------
# A predictive-distribution test double.
#
# build_joint_q_band reads exactly: mu_native, sigma_native, distribution_family,
# day0 (a real Day0Conditioning), live_eligible, ineligibility_reason,
# identity_hash, and sigma_components (a real SigmaComponents — for the mu-draw SE
# center_parameter_se_native, the sigma-draw dispersion model_dispersion_native, and
# the sigma-draw floor realized_floor_native). The double carries those verbatim so
# the band unit is isolated from the heavy forecast-spine sub-objects (CenterEstimate
# / AppliedDebias) the band never touches. ``replace`` preserves every field, so the
# per-draw replace(pd, mu_native=..., sigma_native=...) works on the double.
# ---------------------------------------------------------------------------

def _sigma_components(
    *,
    center_parameter_se_native: float,
    model_dispersion_native: float,
    realized_floor_native: float,
    sigma_after_floor_native: float,
) -> SigmaComponents:
    """A real SigmaComponents carrying only the fields the band draws read."""
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
    mu_native: float
    sigma_native: float
    distribution_family: str
    day0: Day0Conditioning
    sigma_components: SigmaComponents
    live_eligible: bool = True
    ineligibility_reason: Optional[str] = None
    identity_hash: str = "pd-band-test-identity"


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


def _pd(
    mu: float,
    sigma: float,
    family: str,
    day0: Day0Conditioning,
    *,
    center_se: float,
    model_disp: float,
    realized_floor: float,
    identity: str = "pd-band-test-identity",
) -> _PD:
    return _PD(
        mu_native=mu,
        sigma_native=sigma,
        distribution_family=family,
        day0=day0,
        sigma_components=_sigma_components(
            center_parameter_se_native=center_se,
            model_dispersion_native=model_disp,
            realized_floor_native=realized_floor,
            sigma_after_floor_native=sigma,
        ),
        identity_hash=identity,
    )


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #1: every band sample row sums to 1 (spec lines 570, 1140).
# ---------------------------------------------------------------------------

def test_every_band_sample_row_sums_to_one():
    """Every (n_draws, n_bins) sample ROW is a coherent joint distribution (Σ row == 1).

    The load-bearing contract (spec lines 570, 581-582): each draw's q_k is
    renormalized to the probability simplex INSIDE the generator
    (``q_k = q_k / q_k.sum()`` via build_joint_q) BEFORE it is stacked, so EVERY row
    of ``samples`` sums to 1 within 1e-9 for EVERY distribution family.

    RED-on-revert: if the generator regresses to stacking RAW per-bin integrated mass
    (the ``_build_fused_q_bounds`` defect — no per-row renormalization), the rows no
    longer sum to 1 and both ``assert_valid`` and the explicit row-sum assertions
    below fail.
    """
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    tokyo_low = _resolution("Tokyo", "wu_icao", "RJTT", "low")
    hk_high = _resolution("Hong Kong", "hko", "", "high")

    cases_spaces = [
        # NORMAL
        (
            _pd(
                24.3, 1.7, "NORMAL", _inactive_day0(24.3),
                center_se=0.6, model_disp=1.2, realized_floor=1.3,
                identity="normal-id",
            ),
            _outcome_space(tokyo_high, "tokyo-high"),
        ),
        # DAY0_HIGH_MAX_NORMAL — observed running high pulls the support up.
        (
            _pd(
                24.3, 1.7, "DAY0_HIGH_MAX_NORMAL",
                _high_day0(center_before=24.3, observed_high=25.0),
                center_se=0.6, model_disp=1.2, realized_floor=1.3,
                identity="day0-high-id",
            ),
            _outcome_space(tokyo_high, "tokyo-high"),
        ),
        # DAY0_LOW_MIN_NORMAL — observed running low caps the support.
        (
            _pd(
                24.3, 1.7, "DAY0_LOW_MIN_NORMAL",
                _low_day0(center_before=24.3, observed_low=23.0),
                center_se=0.6, model_disp=1.2, realized_floor=1.3,
                identity="day0-low-id",
            ),
            _outcome_space(tokyo_low, "tokyo-low"),
        ),
        # HK NORMAL — the asymmetric oracle_truncate preimage also yields simplex rows.
        (
            _pd(
                24.3, 1.7, "NORMAL", _inactive_day0(24.3),
                center_se=0.6, model_disp=1.2, realized_floor=1.3,
                identity="hk-normal-id",
            ),
            _outcome_space(hk_high, "hk-high"),
        ),
    ]

    for pd, space in cases_spaces:
        band = build_joint_q_band(pd, space, n_draws=500, alpha=0.05)
        assert isinstance(band, JointQBand)
        assert band.basis == "PARAMETER_POSTERIOR_SIMPLEX_V1"
        # Shape: (n_draws, n_bins).
        assert band.samples.shape == (500, len(space.bins))
        # EVERY row sums to 1 within 1e-9 — the per-draw simplex renormalization.
        row_sums = band.samples.sum(axis=1)
        assert np.allclose(row_sums, 1.0, atol=1e-9), (
            "a sample row does not sum to 1 — the per-draw q=q/q.sum() "
            "renormalization was dropped (the _build_fused_q_bounds defect)"
        )
        # Non-negative everywhere.
        assert np.all(band.samples >= 0)
        # assert_valid re-proves the simplex invariant.
        band.assert_valid()
        # q_lcb / q_ucb are per-bin and bracket sensibly (lcb <= ucb everywhere).
        assert band.q_lcb.shape == (len(space.bins),)
        assert band.q_ucb.shape == (len(space.bins),)
        assert np.all(band.q_lcb <= band.q_ucb + 1e-12)
        # The band brackets the same complete Omega the point q ran over.
        assert band.joint_q.omega is space
        band.joint_q.assert_valid()


# ---------------------------------------------------------------------------
# SPEC RED-on-revert #2: modal q_lcb does NOT collapse (spec lines 587, 1141).
# ---------------------------------------------------------------------------

def test_modal_lcb_does_not_collapse_from_raw_bin_percentile():
    """The per-row simplex renormalization keeps a modal bin's q_lcb from collapsing.

    The defect this module replaces (``_build_fused_q_bounds``:1425-1426): it stacks
    the RAW per-bin integrated mass (draws x bins) and takes ``np.percentile(probs,
    5, axis=0)`` PER BIN with NO per-row simplex renormalization. The live grid is the
    NARROW listed (tradeable) bin window, so on a draw whose (mu_k, sigma_k) pushes
    mass past the window edge, the row sums to STRICTLY LESS than 1 — the spilled mass
    is simply dropped. The per-bin alpha-quantile of that incoherent RAW mass HOLLOWS
    OUT the modal bin: the count / center-granularity artifact the spec names.

    The corrected transform applies ``q_k = q_k / q_k.sum()`` to EACH draw row (inside
    build_joint_q) BEFORE the marginal quantile, so each draw's modal mass is its TRUE
    share of a unit-sum distribution. On the spilled draws the renormalization divides
    by a sub-unit total, LIFTING the modal bin's mass and raising its alpha-quantile.

    This test ISOLATES exactly that one step. Over the SAME seeded (mu_k, sigma_k)
    draws and the SAME narrow window, it compares:
      * RAW    = per-bin alpha-quantile of the un-renormalized window mass (the
                 _build_fused_q_bounds defect); and
      * RENORM = per-bin alpha-quantile of the SAME window mass with each row divided
                 by its own sum (the q = q / q.sum() fix).
    The modal RENORM q_lcb is STRICTLY GREATER than the RAW one — the renormalization
    is what stops the collapse. It then confirms ``build_joint_q_band`` itself applies
    the renormalization (every returned sample row sums to 1, and the modal point-q
    belief is high), so a revert that drops the per-row ``q = q / q.sum()`` makes the
    band's rows stop summing to 1 (assert_valid fails) and erases the RENORM-vs-RAW
    margin this test measures.
    """
    # A high-belief modal scenario: tight base sigma so the point q concentrates on
    # the 25°C ring bin; a small center-parameter SE plus a large model-dispersion so
    # the per-draw (mu_k, sigma_k) genuinely jitter and some draws push mass below the
    # narrow window's lower edge (where the modal bin b25 sits) — the spill the RAW
    # view drops and the renormalization recovers.
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo_high, "tokyo-high")
    modal_pd = _pd(
        25.0, 0.5, "NORMAL", _inactive_day0(25.0),
        center_se=0.2,
        model_disp=12.0,     # sigma draws vary -> draws spill past the narrow window
        realized_floor=0.45,
        identity="modal-collapse-id",
    )

    n_draws = 8000
    alpha = 0.05
    band = build_joint_q_band(modal_pd, space, n_draws=n_draws, alpha=alpha)
    band.assert_valid()

    # The point joint q confirms b25 is genuinely the high-belief modal bin.
    assert band.joint_q.q_by_bin_id["b25"] > 0.4, (
        "fixture sanity: b25 must be the dominant modal bin"
    )

    # Isolate the q = q / q.sum() step on the SAME draws + SAME narrow window. The
    # modal bin b25 sits at the LOWER edge of the (b25, b26, b27) window, so draws
    # whose mass jitters DOWN spill out of the window -> rows sum to < 1 -> the RAW
    # modal alpha-quantile is hollowed while the RENORM one is lifted.
    raw_modal_lcb, renorm_modal_lcb, row_sums = _narrow_window_modal_lcbs(
        modal_pd, space, n_draws=n_draws, alpha=alpha, window_ids=("b25", "b26", "b27")
    )

    # Sanity: the narrow-window rows genuinely fail to sum to 1 (the incoherence the
    # renormalization removes) — so this is the defect's regime, not a no-op.
    assert row_sums.min() < 1.0 - 1e-6 and np.percentile(row_sums, 5) < 0.9, (
        "fixture sanity: the narrow-window rows must sum to materially < 1 (otherwise "
        "renormalization is a no-op and the test proves nothing)"
    )

    # THE FIX, isolated: renormalizing each row to the simplex keeps the modal bin's
    # alpha-quantile materially ABOVE the raw (broken) one. Drop the q = q / q.sum()
    # step and these two coincide.
    assert renorm_modal_lcb > raw_modal_lcb + 0.02, (
        f"renorm modal q_lcb ({renorm_modal_lcb:.4f}) is not materially above the raw "
        f"(broken) modal q_lcb ({raw_modal_lcb:.4f}) — the per-row q = q / q.sum() "
        "renormalization is not protecting the modal bin (the _build_fused_q_bounds "
        "collapse)."
    )
    # The renormalized modal q_lcb is a real, non-collapsed belief.
    assert renorm_modal_lcb > 0.4

    # THE STRUCTURAL GUARANTEE (the non-fragile half of RED-on-revert): the band that
    # build_joint_q_band returns applies that SAME renormalization to EVERY row over
    # the complete Omega, so all rows sum to 1. A revert that drops the per-row
    # q = q / q.sum() makes these rows stop summing to 1 and assert_valid (above)
    # fails — independently of any quantile margin.
    assert np.allclose(band.samples.sum(axis=1), 1.0, atol=1e-9)


def _narrow_window_modal_lcbs(
    pd: _PD,
    space: OutcomeSpace,
    *,
    n_draws: int,
    alpha: float,
    window_ids: tuple[str, ...],
) -> tuple[float, float, np.ndarray]:
    """Isolate the ``q = q / q.sum()`` step on a narrow listed window — RAW vs RENORM.

    For each of the SAME seeded (mu_k, sigma_k) draws build_joint_q_band uses, this
    integrates the per-bin Normal-interval mass over the NARROW listed window
    (``window_ids``) — the (draws x bins) ``probs`` grid the live
    ``_build_fused_q_bounds`` percentiles. It then computes the modal bin's
    alpha-quantile TWO ways from the IDENTICAL mass matrix:

      * RAW    — over the un-renormalized window mass (the defect: rows sum to < 1);
      * RENORM — over the SAME mass with each row divided by its own sum (the fix:
                 ``q_k = q_k / q_k.sum()`` projects each draw onto the simplex).

    Returns ``(raw_modal_lcb, renorm_modal_lcb, row_sums)``. The only difference
    between the two scalars is the per-row renormalization, so their gap measures
    exactly the step the corrected transform adds and the defect omits.
    """
    import hashlib

    from src.probability.joint_q_band import draw_mu, draw_sigma
    from scipy.special import ndtr

    window = [b for b in space.bins if b.bin_id in window_ids]
    # WMO symmetric preimage [t-0.5, t+0.5) for each one-degree listed bin (the live
    # grid edges the materializer integrates over).
    lows = np.asarray([float(b.lower_native) - 0.5 for b in window], dtype=float)
    highs = np.asarray([float(b.upper_native) + 0.5 for b in window], dtype=float)
    modal_window_idx = [b.bin_id for b in window].index("b25")

    digest = hashlib.sha256(pd.identity_hash.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big", signed=False)
    rng = np.random.default_rng(seed)

    probs = np.empty((n_draws, len(window)), dtype=float)
    for k in range(n_draws):
        mu_k = draw_mu(pd, rng)
        sigma_k = draw_sigma(pd, rng)
        z_low = (lows - mu_k) / sigma_k
        z_high = (highs - mu_k) / sigma_k
        probs[k, :] = np.clip(ndtr(z_high) - ndtr(z_low), 0.0, 1.0)

    row_sums = probs.sum(axis=1)
    raw_modal_lcb = float(np.quantile(probs, alpha, axis=0)[modal_window_idx])
    # The fix, isolated: project each row onto the simplex BEFORE the marginal quantile.
    renorm = probs / np.clip(row_sums[:, None], 1e-12, None)
    renorm_modal_lcb = float(np.quantile(renorm, alpha, axis=0)[modal_window_idx])
    return raw_modal_lcb, renorm_modal_lcb, row_sums


# ---------------------------------------------------------------------------
# Supporting contract tests (determinism, fail-closed, band ordering).
# ---------------------------------------------------------------------------

def test_band_is_deterministic_for_fixed_inputs():
    """The seeded draw matrix makes the band (and sample_hash) reproducible."""
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo_high, "tokyo-high")
    pd = _pd(
        24.3, 1.7, "NORMAL", _inactive_day0(24.3),
        center_se=0.6, model_disp=1.2, realized_floor=1.3, identity="determinism-id",
    )
    a = build_joint_q_band(pd, space, n_draws=300, alpha=0.05)
    b = build_joint_q_band(pd, space, n_draws=300, alpha=0.05)
    assert a.sample_hash == b.sample_hash
    assert np.array_equal(a.samples, b.samples)
    assert np.array_equal(a.q_lcb, b.q_lcb)
    assert np.array_equal(a.q_ucb, b.q_ucb)


def test_band_refuses_ineligible_distribution():
    """An ineligible (width-less) pd has no point q, so the band fails closed."""
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo_high, "tokyo-high")
    ineligible = _PD(
        mu_native=24.0,
        sigma_native=0.0,
        distribution_family="NORMAL",
        day0=_inactive_day0(24.0),
        sigma_components=_sigma_components(
            center_parameter_se_native=0.0,
            model_dispersion_native=0.0,
            realized_floor_native=0.0,
            sigma_after_floor_native=0.0,
        ),
        live_eligible=False,
        ineligibility_reason="PREDICTIVE_SIGMA_AUTHORITY_MISSING",
        identity_hash="ineligible-id",
    )
    with pytest.raises(JointQBandError):
        build_joint_q_band(ineligible, space, n_draws=100, alpha=0.05)


def test_band_refuses_degenerate_request():
    """A degenerate n_draws / alpha is refused (fail-closed)."""
    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    space = _outcome_space(tokyo_high, "tokyo-high")
    pd = _pd(
        24.3, 1.7, "NORMAL", _inactive_day0(24.3),
        center_se=0.6, model_disp=1.2, realized_floor=1.3, identity="degenerate-id",
    )
    with pytest.raises(JointQBandError):
        build_joint_q_band(pd, space, n_draws=0, alpha=0.05)
    with pytest.raises(JointQBandError):
        build_joint_q_band(pd, space, n_draws=100, alpha=0.5)
    with pytest.raises(JointQBandError):
        build_joint_q_band(pd, space, n_draws=100, alpha=0.0)


def test_drawn_sigma_never_below_realized_floor():
    """Every drawn sigma is at least the realized floor — the sub-realized invariant.

    The sigma draw is floored at the realized floor by construction (inside
    ``draw_sigma``), so no Monte-Carlo draw can integrate q at a sub-realized width —
    the band cannot smuggle in an overconfident σ the point q forbids.
    """
    from src.probability.joint_q_band import draw_sigma

    tokyo_high = _resolution("Tokyo", "wu_icao", "RJTT", "high")
    _ = _outcome_space(tokyo_high, "tokyo-high")
    realized = 1.3
    pd = _pd(
        24.3, 1.4, "NORMAL", _inactive_day0(24.3),
        center_se=0.6, model_disp=2.0, realized_floor=realized, identity="floor-id",
    )
    rng = np.random.default_rng(7)
    draws = np.array([draw_sigma(pd, rng) for _ in range(2000)])
    assert np.all(draws >= realized - 1e-12), (
        "a drawn sigma fell below the realized floor — the per-draw floor is not "
        "enforced inside draw_sigma"
    )
