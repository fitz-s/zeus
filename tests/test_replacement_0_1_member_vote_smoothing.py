# Created: 2026-06-07
# Last reused/audited: 2026-06-07
# Lifecycle: created=2026-06-07; last_reviewed=2026-06-07; last_reused=2026-06-07
# Purpose: Relationship tests for flag-gated additive (Laplace/Dirichlet) smoothing of the
#   AIFS sampled-2t member-vote prior so the soft_anchor.py:197-198 zero-prior -inf veto can
#   no longer make a market bin structurally un-hittable. Fitz #5: kill the category, not the
#   instance — every bin gets a strictly-positive prior so the 0.1 anchor Gaussian (weight 0.80)
#   can place mass in formerly-0-vote bins (the soft anchor becomes soft).
# Reuse: Run before changing member-prior construction, the alpha constant, or the smoothing flag.
# Authority basis: Operator task replacement_0_1_member_vote_smoothing_enabled (edli, default OFF).
"""Flag-gated additive smoothing of the AIFS member-vote prior (the zero-prior veto fix)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.ecmwf_aifs_sampled_2t_localday import (
    AifsInstantSample,
    extract_aifs_sampled_2t_localday,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (
    MEMBER_VOTE_SMOOTHING_ALPHA,
    AifsTemperatureBin,
    build_aifs_sampled_2t_bin_probabilities,
    build_openmeteo_ifs9_aifs_soft_anchor_result,
)

UTC = timezone.utc


def _dt(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _extraction():
    # 4 members: highs land cold(11..14)->1, mild(15..21)->2, hot(22..)->1.
    return extract_aifs_sampled_2t_localday(
        (
            AifsInstantSample("cf", _dt(2026, 6, 6, 0), 16.0, "C"),
            AifsInstantSample("cf", _dt(2026, 6, 6, 6), 21.0, "C"),
            AifsInstantSample("pf001", _dt(2026, 6, 6, 0), 11.0, "C"),
            AifsInstantSample("pf001", _dt(2026, 6, 6, 6), 13.0, "C"),
            AifsInstantSample("pf002", _dt(2026, 6, 6, 0), 23.0, "C"),
            AifsInstantSample("pf002", _dt(2026, 6, 6, 6), 27.0, "C"),
            AifsInstantSample("pf003", _dt(2026, 6, 6, 0), 14.0, "C"),
            AifsInstantSample("pf003", _dt(2026, 6, 6, 6), 17.0, "C"),
        ),
        city_timezone="UTC",
        target_local_date=date(2026, 6, 6),
        source_cycle_time=_dt(2026, 6, 5, 0),
        min_samples_per_member=2,
    )


def _bins() -> tuple[AifsTemperatureBin, ...]:
    return (
        AifsTemperatureBin("cold", upper_c=14.0, center_c=13.0),
        AifsTemperatureBin("mild", lower_c=15.0, upper_c=21.0),
        AifsTemperatureBin("hot", lower_c=22.0, center_c=23.0),
    )


def _zero_vote_within_anchor_bins() -> tuple[AifsTemperatureBin, ...]:
    # Member HIGHS are 13, 17, 21, 27 (see _extraction). This 5-bin contiguous family is built so
    # exactly ONE bin -- 'c_anchor_zero' (19-20) -- receives ZERO votes (no member high lands in
    # 19..20), while the OM9 anchor at 19.5C sits INSIDE it. With smoothing OFF that bin is
    # structurally un-hittable; with smoothing ON the anchor Gaussian can mass it.
    return (
        AifsTemperatureBin("a_cold", upper_c=14.0, center_c=13.5),       # high 13
        AifsTemperatureBin("b_low", lower_c=15.0, upper_c=18.0, center_c=16.5),   # high 17
        AifsTemperatureBin("c_anchor_zero", lower_c=19.0, upper_c=20.0, center_c=19.5),  # ZERO votes
        AifsTemperatureBin("d_upper", lower_c=21.0, upper_c=21.5, center_c=21.0),  # high 21
        AifsTemperatureBin("e_hot", lower_c=22.0, center_c=23.0),        # high 27
    )


def _anchor(high_c: float = 23.0, low_c: float = 12.0) -> OpenMeteoIfs9LocalDayAnchor:
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone="UTC",
        target_local_date=date(2026, 6, 6),
        source_cycle_time=_dt(2026, 6, 5, 0),
        high_c=high_c,
        low_c=low_c,
        sample_count=2,
        contributing_local_times=(_dt(2026, 6, 6, 0), _dt(2026, 6, 6, 6)),
        contributing_valid_times_utc=(_dt(2026, 6, 6, 0), _dt(2026, 6, 6, 6)),
    )


# ---------------------------------------------------------------------------
# (a) flag-OFF byte-identical posterior (the smoothing unreachable when None/0.0)
# ---------------------------------------------------------------------------


def test_member_prior_smoothing_off_is_byte_identical_to_raw_frequency() -> None:
    """alpha=None (flag OFF) and alpha=0.0 BOTH reproduce raw count/total, byte-identical."""
    raw = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())
    off_default = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), member_vote_smoothing_alpha=None
    )
    off_zero = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), member_vote_smoothing_alpha=0.0
    )
    # raw frequency exactly: 1/4, 2/4, 1/4 with NO floor on any bin.
    assert raw.probabilities == {"cold": 0.25, "mild": 0.50, "hot": 0.25}
    assert off_default.probabilities == raw.probabilities
    assert off_zero.probabilities == raw.probabilities


def test_composed_result_smoothing_off_is_byte_identical() -> None:
    """The composed soft-anchor result is byte-identical with smoothing OFF vs unset."""
    base = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(), openmeteo_anchor=_anchor(), metric="high", bins=_bins()
    )
    off = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(),
        metric="high",
        bins=_bins(),
        member_vote_smoothing_alpha=None,
    )
    assert dict(off.posterior.probabilities) == dict(base.posterior.probabilities)
    assert dict(off.aifs_probabilities.probabilities) == dict(base.aifs_probabilities.probabilities)


# ---------------------------------------------------------------------------
# (b) flag-ON: a 0-vote bin within the anchor's mass gets strictly-positive posterior
#     (veto lifted) and q still sums to 1 (mass-preserving)
# ---------------------------------------------------------------------------


def test_zero_vote_bin_raw_prior_is_zero_but_posterior_is_floored_not_unhittable() -> None:
    """With smoothing OFF the 0-vote 'c_anchor_zero' RAW PRIOR is exactly 0.0, but the structural
    floor (Fault A fix) keeps its POSTERIOR strictly positive (never literal-zero / -inf). The
    floor mass is negligible -- the un-hittable CATEGORY is gone unconditionally, while the
    MEANINGFUL trade mass still requires the flag-gated alpha (the next test)."""
    bins = _zero_vote_within_anchor_bins()
    raw = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=bins)
    # 'c_anchor_zero' (19-20) gets ZERO member votes -> raw prior exactly 0.0 (prior builder
    # unchanged; smoothing is the only thing that lifts the prior itself).
    assert raw.probabilities["c_anchor_zero"] == 0.0
    # The anchor at 19.5C sits inside that bin. With smoothing OFF the POSTERIOR is no longer
    # forced to 0.0 by the old -inf veto -- it is floored to a strictly-positive but negligible
    # value (normalizable, hittable), NOT the trade-relevant mass the alpha supplies.
    off = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(high_c=19.5),
        metric="high",
        bins=bins,
    )
    floored = off.posterior.probabilities["c_anchor_zero"]
    assert floored > 0.0  # the un-hittable category is structurally impossible
    assert floored < 1e-9  # but negligible -- not the flag-gated trading mass (iron rule #2/#6)
    assert sum(off.posterior.probabilities.values()) == pytest.approx(1.0)


def test_zero_vote_bin_within_anchor_mass_gets_positive_posterior_when_smoothed() -> None:
    """Flag ON: the 0-vote bin the anchor centers on now carries strictly-positive posterior."""
    bins = _zero_vote_within_anchor_bins()
    smoothed_prior = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=bins, member_vote_smoothing_alpha=MEMBER_VOTE_SMOOTHING_ALPHA
    )
    # Every bin now has strictly-positive prior (the -inf veto can never fire).
    for value in smoothed_prior.probabilities.values():
        assert value > 0.0
    # Prior still sums to 1 (mass-preserving).
    assert sum(smoothed_prior.probabilities.values()) == pytest.approx(1.0)

    result = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(high_c=19.5),  # anchor sits inside the 0-vote bin
        metric="high",
        bins=bins,
        member_vote_smoothing_alpha=MEMBER_VOTE_SMOOTHING_ALPHA,
    )
    posterior = result.posterior.probabilities
    # The veto is lifted: the anchor Gaussian places real mass in the formerly-0-vote bin.
    assert posterior["c_anchor_zero"] > 0.0
    # Posterior still a proper distribution.
    assert sum(posterior.values()) == pytest.approx(1.0)


def test_smoothed_prior_sums_to_one_and_no_bin_is_zero() -> None:
    """Mass-preserving: smoothed prior sums to 1 and no bin is left at 0 (no -inf possible)."""
    for metric in ("high", "low"):
        prior = build_aifs_sampled_2t_bin_probabilities(
            _extraction(),
            metric=metric,
            bins=_zero_vote_within_anchor_bins(),
            member_vote_smoothing_alpha=MEMBER_VOTE_SMOOTHING_ALPHA,
        )
        assert sum(prior.probabilities.values()) == pytest.approx(1.0)
        assert min(prior.probabilities.values()) > 0.0


# ---------------------------------------------------------------------------
# (c) high-vote bins move only marginally (the anchor still dominates)
# ---------------------------------------------------------------------------


def test_high_vote_bin_moves_only_marginally_under_gentle_alpha() -> None:
    """The dominant (high-vote) bin barely moves: gentle alpha << 1 vote."""
    raw = build_aifs_sampled_2t_bin_probabilities(_extraction(), metric="high", bins=_bins())
    smoothed = build_aifs_sampled_2t_bin_probabilities(
        _extraction(), metric="high", bins=_bins(), member_vote_smoothing_alpha=MEMBER_VOTE_SMOOTHING_ALPHA
    )
    # 'mild' is the modal bin (2/4 = 0.50). Its shift must stay tiny (< 1 pp here, K=3).
    delta = abs(smoothed.probabilities["mild"] - raw.probabilities["mild"])
    assert delta < 0.01
    # Smoothed mode is still the same bin (the anchor/prior ordering is preserved).
    modal_raw = max(raw.probabilities, key=lambda k: raw.probabilities[k])
    modal_sm = max(smoothed.probabilities, key=lambda k: smoothed.probabilities[k])
    assert modal_raw == modal_sm == "mild"


def test_alpha_is_a_small_dirichlet_pseudocount_well_below_one_vote() -> None:
    """Guard the gentleness invariant: alpha is a sub-vote symmetric Dirichlet pseudo-count."""
    assert 0.0 < MEMBER_VOTE_SMOOTHING_ALPHA < 1.0
    assert MEMBER_VOTE_SMOOTHING_ALPHA <= 0.1  # never competes with a real member vote


# ---------------------------------------------------------------------------
# (d) a synthetic 'impossible' bin that settles now has non-zero mass
#     (the un-hittable category is gone)
# ---------------------------------------------------------------------------


def test_impossible_settling_bin_floor_vs_meaningful_alpha_mass() -> None:
    """The category killer, in TWO regimes (one mechanism). A bin that gets 0 votes but is where
    settlement lands is never literal-zero / un-hittable anymore.

    Settlement at 19.5C falls in 'c_anchor_zero' (19-20). With smoothing OFF the structural floor
    already removes the un-hittable category, but with only NEGLIGIBLE mass (a normalizability
    guarantee, not a bet). With smoothing ON the flag-gated alpha lifts the SAME bin to MEANINGFUL,
    trade-relevant mass (orders of magnitude larger). This asserts the floor/alpha separation, not
    the old bug-as-law `off == 0.0`.
    """
    bins = _zero_vote_within_anchor_bins()
    settled_bin_id = "c_anchor_zero"

    off = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(high_c=19.5),
        metric="high",
        bins=bins,
    )
    on = build_openmeteo_ifs9_aifs_soft_anchor_result(
        aifs_extraction=_extraction(),
        openmeteo_anchor=_anchor(high_c=19.5),
        metric="high",
        bins=bins,
        member_vote_smoothing_alpha=MEMBER_VOTE_SMOOTHING_ALPHA,
    )
    off_mass = off.posterior.probabilities[settled_bin_id]
    on_mass = on.posterior.probabilities[settled_bin_id]
    # Floor-OFF: strictly positive (category gone) but negligible (structural floor only).
    assert off_mass > 0.0
    assert off_mass < 1e-9
    # Flag-ON: meaningful trade-relevant mass, many orders of magnitude above the floor.
    assert on_mass > 1e-9
    assert on_mass > off_mass * 1e6  # alpha supplies the economic mass, not the floor
