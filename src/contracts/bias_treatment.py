# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: Phase-2 K2+N1+#122 structural fix plan (synthesize structural
#   fix plan from 9 designs + 2 critiques, task #167). Decision D3 "a bias number is
#   corrected XOR distrusted, never both" + D4 "uncertainty travels with every point
#   estimate". Makes the N1 double-penalty (same model_bias_ens row both shifts p_raw
#   AND halves Kelly) UNCONSTRUCTABLE, fail-closes on NULL-authority rows (#122) and on
#   stale training_cutoff (the recurring "May fit applied in June" defect).
"""``BiasTreatment`` — the single typed verdict for a per-(city,bucket) ensemble bias.

Why this type exists
--------------------
Before this contract, the live EDLI path read the SAME ``model_bias_ens`` row from TWO
independent sites:

  * ``_maybe_apply_edli_bias_correction`` subtracted ``effective_bias_c`` from the member
    maxes (shifting p_raw toward "corrected truth" — *trust restored*), and
  * ``_maybe_bias_decay_kelly_haircut`` halved the Kelly multiplier when
    ``|effective_bias_c| > threshold`` (*trust withdrawn*).

For the 20 city-buckets whose ``|effective_bias_c| > 2.0`` BOTH fired on the identical
row: the bias was simultaneously declared corrected (shift the forecast) AND untrustworthy
(halve the bet). Two contradictory treatments of one number — a double penalty applied to
the same evidence (finding N1, HIGH, live today).

The structural fix (D3): there is exactly ONE decision per (city,bucket). A bias is EITHER
``CORRECT`` (shift p_raw; the residual-after-correction is what — if anything — the haircut
may then see) OR ``HAIRCUT`` (size down; never shift). "Shift AND halve the same row" is
made unconstructable because the two consumers receive ONE ``BiasTreatment`` whose ``mode``
already encodes which path is live; the haircut consumes ``residual_native``
(residual-after-correction) — never the raw ``|shift_native|`` magnitude again.

Two further fail-closed antibodies are folded into the constructor so a bad row can never
become a ``BiasTreatment`` at all:

  * **#122 provenance** — ``authority`` must be exactly ``"VERIFIED"``. A NULL / STAGING /
    LEGACY row raises ``BiasProvenanceError``. (The read query already filters
    ``authority='VERIFIED'`` when ``error_model_family`` is supplied, but that is one SQL
    predicate buried in one query path; this makes the guarantee a TYPE the consumer cannot
    bypass.)
  * **stale-cutoff** — ``training_cutoff`` must fall inside the CURRENT meteorological-season
    window for the target date. A May fit served against a June target raises
    ``BiasStaleError`` (the recurring "carry the May fit into June" defect; e.g. Tokyo
    under-corrected +4.9 vs +3.45).

Uncertainty travels with the estimate (D4): the caller CANNOT obtain ``shift_native``
without also receiving ``shift_se_native`` (= ``residual_sd_native / sqrt(n_live)``), the
standard error of the bias-mean estimate. A low-``n_live`` correction therefore WIDENS the
posterior (the SE is folded in quadrature into ``representativeness_sigma`` downstream),
rather than silently applying a point estimate as a hard shift.

Backward-compatibility / shadow safety
-------------------------------------
This TYPE is introduced unconditionally; the BEHAVIOUR it gates (XOR composition, the
SE-widening, NULL/stale refusal) is wired behind ``edli_v1.bias_treatment_v2_enabled``
(default FALSE). With the flag OFF the live q is byte-identical to today: the legacy
``_maybe_apply_edli_bias_correction`` / ``_maybe_bias_decay_kelly_haircut`` shapes are
preserved exactly and this type is not on the live path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Final

from src.contracts.season import season_from_date


class BiasTreatmentMode(str, Enum):
    """Which of the two mutually-exclusive treatments a (city,bucket) receives.

    CORRECT  — the bias is trusted enough to shift p_raw (subtract ``shift_native``
               from the member maxes). The Kelly haircut MUST NOT also fire on this
               row; if a residual magnitude survives the correction it is carried in
               ``residual_native`` and only THAT may inform a downstream size decision.
    HAIRCUT  — the bias is NOT trusted to shift the forecast; size the bet DOWN instead.
               p_raw is left raw (no shift). ``shift_native`` is 0.0 in this mode.
    NONE     — no actionable bias (within threshold AND not large enough to distrust):
               neither shift nor haircut. p_raw raw, Kelly unchanged.
    """

    CORRECT = "CORRECT"
    HAIRCUT = "HAIRCUT"
    NONE = "NONE"


class BiasProvenanceError(ValueError):
    """A bias row whose ``authority`` is not exactly ``'VERIFIED'`` (#122 provenance gate)."""


class BiasStaleError(ValueError):
    """A bias row whose ``training_cutoff`` is outside the target date's season window."""


# Months belonging to each meteorological season code (the season helper already maps
# date -> code hemisphere-aware, so we compare CODES, never raw month numbers, which keeps
# the southern-hemisphere flip consistent).
_SEASON_OF_MONTH: Final[dict[int, str]] = {
    12: "DJF", 1: "DJF", 2: "DJF",
    3: "MAM", 4: "MAM", 5: "MAM",
    6: "JJA", 7: "JJA", 8: "JJA",
    9: "SON", 10: "SON", 11: "SON",
}


@dataclass(frozen=True)
class BiasTreatment:
    """The single typed verdict for one (city,season,metric,bucket) ensemble bias.

    Constructed ONLY via :meth:`from_row` (the fail-closed factory) so a NULL-authority or
    stale row can never reach a consumer. All numeric fields are NATIVE unit (the city's
    settlement unit — degC for C-cities, degF for F-cities). The caller cannot obtain the
    shift without the SE: both are required fields.

    Fields
    ------
    shift_native        : the de-bias shift to SUBTRACT from member maxes, native unit.
                          0.0 unless ``mode is CORRECT``.
    shift_se_native     : standard error of the bias-mean estimate = residual_sd/sqrt(n),
                          native unit. Folded in quadrature into representativeness_sigma so
                          a low-n correction widens q_lcb (D4). Always >= 0.
    residual_native     : the bias magnitude that SURVIVES a correction (native unit). In
                          CORRECT mode this is the post-shift residual the haircut MAY see;
                          the haircut never re-reads the raw |shift| (kills the N1 double
                          penalty). In HAIRCUT mode this carries the (un-corrected) bias
                          magnitude the size-down is responding to.
    n_live              : live-sample count behind the fit (drives the SE and the
                          low-n-widens decision).
    correction_strength : the shrinkage lambda actually applied = effective/raw in [0,1].
                          A row that hardcodes 1.0 is a writer defect (G2).
    authority           : provenance label; constructor guarantees == 'VERIFIED'.
    training_cutoff     : ISO date/datetime the fit was cut at; constructor guarantees it
                          falls in the target date's season window.
    """

    shift_native: float
    shift_se_native: float
    residual_native: float
    n_live: int
    correction_strength: float
    authority: str
    training_cutoff: str
    mode: BiasTreatmentMode

    # ------------------------------------------------------------------ factory
    @staticmethod
    def _season_window_contains(training_cutoff: str, target_date: str, lat: float) -> bool:
        """True iff the fit's training_cutoff season == the target date's season.

        Both are mapped through ``season_from_date`` (hemisphere-aware), so we compare
        season CODES, never raw months. A May (MAM) cutoff applied to a June (JJA) target
        therefore fails — the recurring carry-over defect.
        """
        try:
            cutoff_date = str(training_cutoff)[:10]
            target = str(target_date)[:10]
            return season_from_date(cutoff_date, lat=lat) == season_from_date(target, lat=lat)
        except Exception:
            return False

    @classmethod
    def from_row(
        cls,
        *,
        effective_bias_native: float,
        residual_sd_native: float,
        n_live: int,
        correction_strength: float,
        authority: str | None,
        training_cutoff: str | None,
        target_date: str,
        lat: float,
        threshold_native: float,
        mode: BiasTreatmentMode,
    ) -> "BiasTreatment":
        """Fail-closed factory. Raises before a bad row can become a treatment.

        Parameters are all NATIVE unit (caller has already applied the degC->degF x1.8
        scaling for F-settled cities). ``mode`` is the XOR decision the caller made:
        whether this (city,bucket) is on the CORRECT path or the HAIRCUT path (they are
        mutually exclusive by construction — a single call site picks one).

        Raises
        ------
        BiasProvenanceError : authority is not exactly 'VERIFIED' (#122).
        BiasStaleError      : training_cutoff is None or outside the target season window.
        """
        # #122 provenance — fail closed on anything but VERIFIED.
        if authority != "VERIFIED":
            raise BiasProvenanceError(
                f"bias row authority={authority!r} is not VERIFIED — refused (provenance gate #122)"
            )
        # stale-cutoff — fail closed on missing or out-of-season cutoff.
        if not training_cutoff or not cls._season_window_contains(
            str(training_cutoff), str(target_date), float(lat)
        ):
            raise BiasStaleError(
                f"bias training_cutoff={training_cutoff!r} is outside the season window for "
                f"target_date={target_date!r} — refused (stale fit gate)"
            )

        n = int(n_live)
        # D4: SE of the bias-mean = residual_sd / sqrt(n). n<=0 -> SE undefined -> treat as
        # max-uncertainty by leaving SE at the residual scale (never silently 0, which would
        # be the LEAST conservative outcome).
        sd = abs(float(residual_sd_native))
        se = sd / math.sqrt(n) if n > 0 else sd

        eff = float(effective_bias_native)
        if mode is BiasTreatmentMode.CORRECT:
            shift = eff
            # residual-after-correction: the de-biased member array no longer carries the
            # mean bias, so the magnitude a (hypothetical) downstream size-down may see is 0
            # — NOT the raw |eff|. This is the structural kill of the N1 double penalty.
            residual = 0.0
        elif mode is BiasTreatmentMode.HAIRCUT:
            shift = 0.0
            # haircut path: no shift; the residual the size-down responds to is the raw
            # (un-corrected) bias magnitude.
            residual = abs(eff)
        else:  # NONE
            shift = 0.0
            residual = abs(eff)

        return cls(
            shift_native=float(shift),
            shift_se_native=float(se),
            residual_native=float(residual),
            n_live=n,
            correction_strength=float(correction_strength),
            authority="VERIFIED",
            training_cutoff=str(training_cutoff),
            mode=mode,
        )

    # ------------------------------------------------------------------ helpers
    @property
    def is_correcting(self) -> bool:
        return self.mode is BiasTreatmentMode.CORRECT

    @property
    def is_haircut(self) -> bool:
        return self.mode is BiasTreatmentMode.HAIRCUT

    def kelly_factor(self, *, threshold_native: float, haircut_factor: float) -> float:
        """The Kelly multiplier this treatment implies.

        The XOR invariant lives HERE: a CORRECT treatment NEVER returns a haircut
        (the bias was consumed by the p_raw shift; ``residual_native`` is 0 so the
        |residual|>threshold test is structurally false). Only a HAIRCUT treatment whose
        surviving residual exceeds the threshold sizes down. This makes "shift AND halve
        the same row" unconstructable: you cannot get both a non-zero ``shift_native`` and
        a sub-1.0 ``kelly_factor`` out of one ``BiasTreatment``.
        """
        if self.mode is BiasTreatmentMode.CORRECT:
            return 1.0
        if self.mode is BiasTreatmentMode.HAIRCUT and abs(self.residual_native) > float(
            threshold_native
        ):
            return float(haircut_factor)
        return 1.0
