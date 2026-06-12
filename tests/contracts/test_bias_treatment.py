# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K2+N1+#122 (task #167). Relationship antibody for
#   src/contracts/bias_treatment.py::BiasTreatment. Pins the structural invariants that
#   make the N1 double-penalty (shift AND halve the same row) UNCONSTRUCTABLE, the #122
#   NULL-authority refusal, the stale-cutoff refusal, and the D4 SE-with-shift coupling.
"""BiasTreatment type-level relationship tests.

These test the TYPE in isolation (no DB, no reactor). The reactor-wiring tests live in
tests/engine/test_bias_treatment_v2_wiring.py and gate on edli.bias_treatment_v2_enabled.
"""
from __future__ import annotations

import math

import pytest

from src.contracts.bias_treatment import (
    BiasProvenanceError,
    BiasStaleError,
    BiasTreatment,
    BiasTreatmentMode,
)


# A JJA (June/July/Aug) target so an in-season cutoff is a June date and a stale one is May.
_JUNE_TARGET = "2026-06-15"
_JUNE_CUTOFF = "2026-06-10T00:00:00+00:00"
_MAY_CUTOFF = "2026-05-25T00:00:00+00:00"
_NORTH_LAT = 35.0


def _correct(**over):
    base = dict(
        effective_bias_native=3.5,
        residual_sd_native=2.1,
        n_live=7,
        correction_strength=0.8,
        authority="VERIFIED",
        training_cutoff=_JUNE_CUTOFF,
        target_date=_JUNE_TARGET,
        lat=_NORTH_LAT,
        threshold_native=2.0,
        mode=BiasTreatmentMode.CORRECT,
    )
    base.update(over)
    return BiasTreatment.from_row(**base)


# ---------------------------------------------------------------------------
# #122 provenance — NULL/non-VERIFIED authority is refused at construction
# ---------------------------------------------------------------------------
class TestNullAuthorityRefused:
    def test_null_authority_row_refused(self):
        with pytest.raises(BiasProvenanceError):
            _correct(authority=None)

    @pytest.mark.parametrize("auth", ["STAGING", "LEGACY", "", "verified", "Verified"])
    def test_non_verified_authority_refused(self, auth):
        with pytest.raises(BiasProvenanceError):
            _correct(authority=auth)

    def test_verified_authority_constructs(self):
        t = _correct(authority="VERIFIED")
        assert t.authority == "VERIFIED"


# ---------------------------------------------------------------------------
# stale-cutoff — a May fit applied to a June target is refused
# ---------------------------------------------------------------------------
class TestStaleTrainingCutoffRefused:
    def test_stale_training_cutoff_refused(self):
        # MAM cutoff vs JJA target -> different season code -> refused.
        with pytest.raises(BiasStaleError):
            _correct(training_cutoff=_MAY_CUTOFF)

    def test_missing_training_cutoff_refused(self):
        with pytest.raises(BiasStaleError):
            _correct(training_cutoff=None)

    def test_in_season_cutoff_constructs(self):
        t = _correct(training_cutoff=_JUNE_CUTOFF)
        assert t.training_cutoff == _JUNE_CUTOFF

    def test_southern_hemisphere_season_flip_respected(self):
        # Southern hemisphere: June is JJA-code "DJF" (cold). A June cutoff vs a June
        # target is SAME season regardless of hemisphere — must still construct.
        t = _correct(lat=-33.0, training_cutoff=_JUNE_CUTOFF, target_date=_JUNE_TARGET)
        assert t.is_correcting


# ---------------------------------------------------------------------------
# D4 — the caller cannot get the shift without the SE; low-n widens
# ---------------------------------------------------------------------------
class TestUncertaintyTravelsWithShift:
    def test_shift_se_is_required_field(self):
        t = _correct()
        # both present on the frozen dataclass — neither is Optional.
        assert hasattr(t, "shift_native")
        assert hasattr(t, "shift_se_native")

    def test_se_equals_residual_over_sqrt_n(self):
        t = _correct(residual_sd_native=2.1, n_live=7)
        assert t.shift_se_native == pytest.approx(2.1 / math.sqrt(7))

    def test_posterior_width_n7_gt_n50(self):
        # SAME residual scale, smaller n -> LARGER SE -> wider posterior. This is the D4
        # contract: a low-n correction widens, never silently applies a hard point shift.
        t_n7 = _correct(residual_sd_native=2.1, n_live=7)
        t_n50 = _correct(residual_sd_native=2.1, n_live=50)
        assert t_n7.shift_se_native > t_n50.shift_se_native

    def test_se_never_zero_when_n_nonpositive(self):
        # n<=0 -> SE undefined -> fall back to the residual scale (max-conservative),
        # never silently 0 (the LEAST conservative outcome).
        t = _correct(residual_sd_native=2.1, n_live=0)
        assert t.shift_se_native == pytest.approx(2.1)


# ---------------------------------------------------------------------------
# N1 XOR — a row cannot both shift p_raw AND incur a Kelly haircut
# ---------------------------------------------------------------------------
class TestNoDoublePenaltyCorrectedXorHaircut:
    def test_correct_mode_never_haircuts(self):
        # |bias|=3.5 > threshold 2.0 — under the LEGACY logic this row would BOTH shift
        # AND halve. With the XOR type a CORRECT treatment yields a non-zero shift and a
        # kelly_factor of exactly 1.0 (no haircut). Double penalty unconstructable.
        t = _correct(effective_bias_native=3.5, threshold_native=2.0)
        assert t.shift_native == pytest.approx(3.5)
        assert t.kelly_factor(threshold_native=2.0, haircut_factor=0.5) == 1.0

    def test_correct_mode_residual_after_correction_is_zero(self):
        # The de-biased members carry no mean bias -> the magnitude a size-down could see
        # is 0, NOT the raw |eff|. This is WHY the haircut can't re-fire.
        t = _correct(effective_bias_native=3.5)
        assert t.residual_native == 0.0

    def test_haircut_mode_never_shifts(self):
        t = _correct(mode=BiasTreatmentMode.HAIRCUT, effective_bias_native=3.5)
        assert t.shift_native == 0.0
        # haircut residual = raw magnitude; exceeds threshold -> sizes down.
        assert t.residual_native == pytest.approx(3.5)
        assert t.kelly_factor(threshold_native=2.0, haircut_factor=0.5) == 0.5

    def test_haircut_within_threshold_no_size_down(self):
        t = _correct(mode=BiasTreatmentMode.HAIRCUT, effective_bias_native=1.0)
        assert t.kelly_factor(threshold_native=2.0, haircut_factor=0.5) == 1.0

    def test_no_treatment_can_both_shift_and_haircut(self):
        # The universal invariant: across ALL modes, (shift != 0) AND (kelly_factor < 1)
        # is never simultaneously true.
        for mode in BiasTreatmentMode:
            t = _correct(mode=mode, effective_bias_native=3.5, threshold_native=2.0)
            kf = t.kelly_factor(threshold_native=2.0, haircut_factor=0.5)
            assert not (abs(t.shift_native) > 0.0 and kf < 1.0), (
                f"mode={mode} produced BOTH a shift ({t.shift_native}) and a haircut ({kf})"
            )
