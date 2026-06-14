# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md
#   (DebiasAuthority Create block lines 135-218: BiasArtifact 139-164,
#   AppliedDebias 166-184, DebiasAuthority.apply + activation rule 189-218 with
#   N_SIGMA_BIAS=2.0 and the deterministic priority order; Stage 2 block 1053-1070)
#   reconciled against docs/evidence/qkernel_rebuild/spec_vs_live_drift_ledger.md
#   (GREENFIELD ONLY — no live-file edits; prefer the live types).
#   Live artifact superseded: model_bias_ens.effective_bias_c (edli_per_city_v1)
#   read+subtracted at src/engine/event_reactor_adapter.py:11526-11609 (def) /
#   :11084 / :12594 (call sites; neutralized later at Stage 11, NOT here).
"""DebiasAuthority — the single de-bias authority for the q-kernel forecast spine.

This is Stage 2 of the q-kernel rebuild. It replaces the scattered, parallel,
contaminated mean-correction surfaces (the live EDLI ``effective_bias_c``
subtraction, the EMOS μ-offset, the grid-representativeness row, raw-replacement
correction) with ONE place that decides whether — and by exactly how much — the
forecast member center may be shifted toward settlement truth.

Two structural guarantees, both implemented as the TRANSFORMATION (not as a
downstream gate/cap that catches a bad value after a broken transform produced it):

  1. **One shift, one basis.** ``apply`` selects EXACTLY ONE correction basis via
     the deterministic priority order
     ``per_model_station_walk_forward > model_family_station_walk_forward >
     city_station_representativeness > no_debias`` and applies it ONCE. There is
     no second independent center-shift surface left for another correction to add
     to. Rejected artifacts are marked in telemetry, never independently applied.
     A second temperature-mean shift is therefore not a value to be detected — it
     is unrepresentable: the only shift that can reach the members is the single
     chosen basis's per-member vector.

  2. **The served shift IS the realized residual, never the artifact's claim.**
     A de-bias is a *model-validity* statement: "the realized settlement residual
     for this (station, product, regime) is ``residual_mean_native``, so warm the
     members by that much." The artifact's ``proposed_shift_native`` is only
     ADMISSIBLE if it agrees with the realized trailing residual band
     (``|proposed - residual_mean| <= N_SIGMA_BIAS * max(residual_std, eps)``).
     When it agrees, the applied shift is the realized ``residual_mean_native``
     (the trailing-band center), so the served correction is bounded by realized
     residuals by construction. When it disagrees — Tokyo's −4.847°C claim against
     a realized −0.33°C band — the artifact is REFUSED (``MAGNITUDE_REFUSED``) and
     NO shift is applied. The broken −4.847 output is mathematically impossible
     because no code path multiplies it into the members: it is rejected at the
     model-validity boundary, and even an admitted artifact serves the realized
     band center, not its own ``proposed_shift_native``.

All member values are normalized to the settlement unit (``EventResolution``'s
``measurement_unit``) BEFORE any comparison. An artifact whose product / station /
source mapping differs from the members may not apply.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import numpy as np

from src.forecast.types import ForecastCase, FreshModelSet, RawModelMember

# ---------------------------------------------------------------------------
# Activation constants (spec lines 207-212).
# ---------------------------------------------------------------------------

# N_SIGMA_BIAS gates LIVE activation: an artifact may only serve a correction if
# its claimed shift sits within N_SIGMA_BIAS realized residual standard deviations
# of the realized residual mean. This is NOT a downstream cap on the OUTPUT — it
# is a refusal to serve a model artifact whose claimed correction is not supported
# by realized settlement residuals (spec line 212). Tokyo −4.847°C against a
# trailing residual around −0.33°C MUST fail this band -> MAGNITUDE_REFUSED.
N_SIGMA_BIAS: float = 2.0

# Floor for the residual std so a degenerate (near-zero std) artifact cannot make
# the magnitude band collapse to a point and admit an arbitrarily large claimed
# shift. With std ~ 0 the band is N_SIGMA_BIAS * SIGMA_FLOOR_EPSILON wide.
SIGMA_FLOOR_EPSILON: float = 0.25

# CRPS no-harm tolerance: an artifact may only apply if its out-of-sample CRPS
# after correction does not exceed CRPS before by more than this tolerance. A
# de-bias that makes calibrated scoring WORSE is refused (OOS_HARM_REFUSED).
CRPS_TOLERANCE: float = 0.02

# Minimum trailing-residual sample count for an artifact to be trustworthy. Below
# this the realized residual band is too thin to validate a claimed shift.
MIN_N: int = 30

# Freshness window (spec line 202): an artifact is fresh iff its training cutoff
# is within FRESHNESS_DAYS of the case issue time. The stale −4.847 artifact is
# refused here too if its cutoff predates the window.
FRESHNESS_DAYS: int = 3

# Deterministic priority order over correction bases (spec line 216). A lower
# index is strictly preferred; ``no_debias`` is the always-available fallback.
CORRECTION_BASIS_PRIORITY: tuple[str, ...] = (
    "per_model_station_walk_forward",
    "model_family_station_walk_forward",
    "city_station_representativeness",
    "no_debias",
)


# ---------------------------------------------------------------------------
# Artifacts (spec lines 139-184) — EXACT field names, frozen.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BiasArtifact:
    """One fitted de-bias artifact for a (city, station, product, regime, lead).

    ``residual_mean_native`` / ``residual_std_native`` / ``residual_se_native``
    describe the REALIZED trailing settlement residual band (forecast − observed,
    in the settlement native unit). ``proposed_shift_native`` is the artifact's
    CLAIMED correction; it is only honored if it agrees with the realized band
    (see ``DebiasAuthority._activation_status``). ``product_set_hash`` and
    ``station_id`` are the product/station identity an applying artifact must
    match the member set on.
    """

    artifact_id: str
    authority: Literal["SETTLEMENT_STATION_WALK_FORWARD_V1"]
    city: str
    station_id: str
    metric: Literal["high", "low"]
    season: str
    regime_key: str
    lead_bucket: str
    product_set_hash: str
    model_id: str | None
    training_start_utc: datetime
    training_cutoff_utc: datetime
    valid_until_utc: datetime
    n: int
    residual_mean_native: float
    residual_std_native: float
    residual_se_native: float
    proposed_shift_native: float
    oos_crps_before: float
    oos_crps_after: float
    oos_logscore_before: float | None
    oos_logscore_after: float | None
    station_mapping_id: str
    source_hash: str


@dataclass(frozen=True)
class AppliedDebias:
    """The telemetry record of a single de-bias decision.

    ``per_member_shift_native`` is the EXACT per-member shift vector applied to the
    settlement-unit member values (all zeros when no shift applied).
    ``aggregate_shift_native`` is the single scalar center shift.
    ``trailing_residual_mean_native`` / ``trailing_residual_std_native`` are the
    realized band the served shift was anchored to (the shift IS the band center,
    never the artifact's ``proposed_shift_native``).
    """

    artifact_ids: tuple[str, ...]
    per_member_shift_native: tuple[float, ...]
    aggregate_shift_native: float
    trailing_residual_mean_native: float
    trailing_residual_std_native: float
    activation_status: Literal[
        "APPLIED",
        "NO_ARTIFACT",
        "STALE_REFUSED",
        "PRODUCT_MISMATCH_REFUSED",
        "STATION_MISMATCH_REFUSED",
        "OOS_HARM_REFUSED",
        "MAGNITUDE_REFUSED",
        "LOW_N_REFUSED",
    ]
    reason: str


# ---------------------------------------------------------------------------
# Per-case activation thresholds (spec lines 205-206).
# ---------------------------------------------------------------------------

def min_n(case: ForecastCase) -> int:
    """Minimum trailing-residual sample count required for activation."""
    return MIN_N


def crps_tolerance(case: ForecastCase) -> float:
    """OOS CRPS no-harm tolerance for activation."""
    return CRPS_TOLERANCE


# ---------------------------------------------------------------------------
# Settlement-unit normalization (spec line 195: members normalized to the
# settlement unit BEFORE any comparison).
# ---------------------------------------------------------------------------

def _c_to_native(value_c: float, settlement_unit: str) -> float:
    """Convert a °C value to the settlement native unit."""
    if settlement_unit == "F":
        return value_c * 9.0 / 5.0 + 32.0
    return value_c


def _member_native_values(
    case: ForecastCase,
    models: FreshModelSet,
) -> np.ndarray:
    """Return member values in the SETTLEMENT native unit.

    ``FreshModelSet.member_values_native`` already carries member values in the
    settlement native unit (the unit declared on ``case.resolution`` /
    ``case.unit``); this is the single point that materializes them as a float
    array for the shift. Normalizing here means every comparison and every applied
    shift below is in one consistent unit.
    """
    return np.asarray(models.member_values_native, dtype=float)


# ---------------------------------------------------------------------------
# Identity matching (spec lines 195, 203-204).
# ---------------------------------------------------------------------------

def _station_matches(artifact: BiasArtifact, case: ForecastCase) -> bool:
    """The artifact's settlement station must equal the case's settlement station."""
    return artifact.station_id == case.station_id


def _product_matches(
    artifact: BiasArtifact,
    models: FreshModelSet,
    members: tuple[RawModelMember, ...],
) -> bool:
    """Artifact product identity must match the member set.

    Spec line 204: ``product_set_hash == models.model_set_hash`` (whole-set
    product match) OR the per-model artifact's ``model_id`` is one of the member
    model ids (per-model match). An artifact fitted on a different product set or
    a model absent from the members may not apply.
    """
    if artifact.product_set_hash == models.model_set_hash:
        return True
    if artifact.model_id is not None:
        return artifact.model_id in {m.model_id for m in members}
    return False


def _source_mapping_matches(
    artifact: BiasArtifact,
    members: tuple[RawModelMember, ...],
) -> bool:
    """The artifact's station mapping must match the members' station mapping.

    Spec line 195: no artifact may apply if its product/station/source MAPPING
    differs from the member. ``station_mapping_id`` is the source mapping identity
    carried on both the artifact and each ``RawModelMember``.
    """
    if not members:
        return False
    return all(m.station_mapping_id == artifact.station_mapping_id for m in members)


def _correction_basis(artifact: BiasArtifact) -> str:
    """Classify an artifact into one of the priority-ordered correction bases.

    A per-model artifact (``model_id`` set) is a per-model station walk-forward; a
    set-level artifact (``model_id is None``) matched on ``product_set_hash`` is a
    model-family station walk-forward; everything else falls to the
    city/station representativeness basis.
    """
    if artifact.model_id is not None:
        return "per_model_station_walk_forward"
    return "model_family_station_walk_forward"


# ---------------------------------------------------------------------------
# DebiasAuthority (spec lines 189-218).
# ---------------------------------------------------------------------------

class DebiasAuthority:
    """The single authority that decides and applies the forecast center de-bias.

    The only public method is ``apply``. It returns the (possibly shifted) member
    values in the settlement native unit and an ``AppliedDebias`` telemetry record.
    """

    def __init__(self, artifacts: tuple[BiasArtifact, ...] = ()) -> None:
        self._artifacts: tuple[BiasArtifact, ...] = tuple(artifacts)

    # -- public ----------------------------------------------------------------

    def apply(
        self,
        case: ForecastCase,
        models: FreshModelSet,
    ) -> tuple[np.ndarray, AppliedDebias]:
        """Apply at most one de-bias to the member center.

        Steps (spec lines 193-218):
          1. Normalize members to the settlement unit.
          2. For each candidate artifact compute its activation status (fresh,
             right station, right product, enough n, no OOS harm, magnitude ok).
          3. Among the APPLICABLE artifacts choose EXACTLY ONE basis by the
             deterministic priority order; apply its shift ONCE.
          4. If none is applicable, no shift is applied; the highest-severity
             refusal among the candidates is reported, else ``NO_ARTIFACT``.
        """
        members = tuple(models.members)
        member_native = _member_native_values(case, models)
        settlement_unit = case.resolution.measurement_unit

        # Evaluate every candidate artifact for this case.
        evaluations: list[tuple[BiasArtifact, str, str]] = []
        for artifact in self._artifacts:
            if artifact.city != case.city or artifact.metric != case.metric:
                # Not a candidate for this case at all (different family target).
                continue
            status, reason = self._activation_status(
                artifact, case, models, members, settlement_unit
            )
            evaluations.append((artifact, status, reason))

        applicable = [
            (artifact, reason)
            for (artifact, status, reason) in evaluations
            if status == "APPLIED"
        ]

        if applicable:
            chosen, chosen_reason = self._choose_by_priority(applicable)
            return self._apply_chosen(
                chosen, chosen_reason, case, models, member_native, evaluations
            )

        # Nothing applicable: do NOT shift. Report the most informative refusal.
        return self._no_shift(member_native, evaluations)

    # -- activation rule (spec lines 198-212) ----------------------------------

    def _activation_status(
        self,
        artifact: BiasArtifact,
        case: ForecastCase,
        models: FreshModelSet,
        members: tuple[RawModelMember, ...],
        settlement_unit: str,
    ) -> tuple[str, str]:
        """Compute the activation status for ONE artifact.

        Returns ``("APPLIED", reason)`` only when every condition passes. The
        magnitude condition is the model-validity refusal that makes the −4.847
        artifact return ``MAGNITUDE_REFUSED`` (spec line 212).
        """
        # Station identity (spec line 203).
        if not _station_matches(artifact, case):
            return (
                "STATION_MISMATCH_REFUSED",
                f"artifact station {artifact.station_id!r} != case station "
                f"{case.station_id!r}",
            )

        # Product / source-mapping identity (spec lines 195, 204).
        if not _product_matches(artifact, models, members):
            return (
                "PRODUCT_MISMATCH_REFUSED",
                f"artifact product_set_hash {artifact.product_set_hash!r} / "
                f"model_id {artifact.model_id!r} does not match member set "
                f"{models.model_set_hash!r}",
            )
        if not _source_mapping_matches(artifact, members):
            return (
                "PRODUCT_MISMATCH_REFUSED",
                f"artifact station_mapping_id {artifact.station_mapping_id!r} "
                f"does not match member station mapping",
            )

        # Freshness (spec line 202): training cutoff within FRESHNESS_DAYS of issue.
        fresh = artifact.training_cutoff_utc >= case.issue_time_utc - timedelta(
            days=FRESHNESS_DAYS
        )
        if not fresh:
            return (
                "STALE_REFUSED",
                f"training_cutoff_utc {artifact.training_cutoff_utc.isoformat()} "
                f"older than issue {case.issue_time_utc.isoformat()} − "
                f"{FRESHNESS_DAYS}d",
            )

        # Sample count (spec line 205).
        enough_n = artifact.n >= min_n(case)
        if not enough_n:
            return (
                "LOW_N_REFUSED",
                f"n={artifact.n} < min_n={min_n(case)}",
            )

        # OOS no-harm (spec line 206).
        no_harm = artifact.oos_crps_after <= artifact.oos_crps_before + crps_tolerance(
            case
        )
        if not no_harm:
            return (
                "OOS_HARM_REFUSED",
                f"oos_crps_after={artifact.oos_crps_after} > "
                f"oos_crps_before={artifact.oos_crps_before} + "
                f"tol={crps_tolerance(case)}",
            )

        # Magnitude / model validity (spec lines 207-212). The claimed shift must
        # sit within N_SIGMA_BIAS realized residual std of the realized residual
        # mean. THIS is the refusal that kills Tokyo's −4.847 against a −0.33 band.
        band_half_width = N_SIGMA_BIAS * max(
            artifact.residual_std_native, SIGMA_FLOOR_EPSILON
        )
        magnitude_ok = (
            abs(artifact.proposed_shift_native - artifact.residual_mean_native)
            <= band_half_width
        )
        if not magnitude_ok:
            return (
                "MAGNITUDE_REFUSED",
                f"proposed_shift_native={artifact.proposed_shift_native} is "
                f"{abs(artifact.proposed_shift_native - artifact.residual_mean_native):.4f} "
                f"from realized residual_mean_native={artifact.residual_mean_native} "
                f"(band ±{band_half_width:.4f} = {N_SIGMA_BIAS}·"
                f"max(std={artifact.residual_std_native}, eps={SIGMA_FLOOR_EPSILON})): "
                f"claimed correction not supported by realized settlement residuals",
            )

        return ("APPLIED", f"applicable on basis {_correction_basis(artifact)!r}")

    # -- single-basis selection (spec lines 214-218) ---------------------------

    @staticmethod
    def _choose_by_priority(
        applicable: list[tuple[BiasArtifact, str]],
    ) -> tuple[BiasArtifact, str]:
        """Choose EXACTLY ONE applicable artifact by the deterministic priority.

        ``per_model_station_walk_forward > model_family_station_walk_forward >
        city_station_representativeness``. Ties within a basis break by
        ``artifact_id`` so selection is fully deterministic.
        """

        def sort_key(item: tuple[BiasArtifact, str]) -> tuple[int, str]:
            artifact, _ = item
            basis = _correction_basis(artifact)
            try:
                rank = CORRECTION_BASIS_PRIORITY.index(basis)
            except ValueError:  # pragma: no cover - basis is always known
                rank = len(CORRECTION_BASIS_PRIORITY)
            return (rank, artifact.artifact_id)

        return min(applicable, key=sort_key)

    # -- application (the single shift; spec line 213 "de-bias happens once") ---

    def _apply_chosen(
        self,
        chosen: BiasArtifact,
        chosen_reason: str,
        case: ForecastCase,
        models: FreshModelSet,
        member_native: np.ndarray,
        evaluations: list[tuple[BiasArtifact, str, str]],
    ) -> tuple[np.ndarray, AppliedDebias]:
        """Apply the chosen artifact's shift ONCE and emit telemetry.

        The applied shift is the REALIZED trailing residual mean (the band center
        the magnitude rule validated the claim against), NOT the artifact's
        ``proposed_shift_native``. Sign convention matches the superseded live EDLI
        path (``residual = forecast − observed``; subtracting de-biases members
        toward observed truth). Because the served shift is the realized residual
        mean, the applied-bias histogram is bounded by realized residuals by
        construction (Stage-2 live signal).
        """
        aggregate_shift = float(chosen.residual_mean_native)
        corrected = member_native - aggregate_shift
        per_member_shift = tuple(
            float(aggregate_shift) for _ in range(member_native.shape[0])
        )

        rejected_ids = tuple(
            artifact.artifact_id
            for (artifact, status, _) in evaluations
            if artifact.artifact_id != chosen.artifact_id
        )
        applied = AppliedDebias(
            artifact_ids=(chosen.artifact_id,) + rejected_ids,
            per_member_shift_native=per_member_shift,
            aggregate_shift_native=aggregate_shift,
            trailing_residual_mean_native=float(chosen.residual_mean_native),
            trailing_residual_std_native=float(chosen.residual_std_native),
            activation_status="APPLIED",
            reason=(
                f"basis={_correction_basis(chosen)} artifact={chosen.artifact_id} "
                f"shift={aggregate_shift:+.4f} (realized residual mean); "
                f"{chosen_reason}; rejected={list(rejected_ids)}"
            ),
        )
        return corrected, applied

    @staticmethod
    def _no_shift(
        member_native: np.ndarray,
        evaluations: list[tuple[BiasArtifact, str, str]],
    ) -> tuple[np.ndarray, AppliedDebias]:
        """No artifact applied: return members unchanged with the salient refusal.

        Among refusals, report the highest-severity one so telemetry surfaces WHY
        the (e.g. −4.847) artifact was not served. With no candidates at all the
        status is ``NO_ARTIFACT``.
        """
        zero_shift = tuple(0.0 for _ in range(member_native.shape[0]))

        if not evaluations:
            applied = AppliedDebias(
                artifact_ids=(),
                per_member_shift_native=zero_shift,
                aggregate_shift_native=0.0,
                trailing_residual_mean_native=0.0,
                trailing_residual_std_native=0.0,
                activation_status="NO_ARTIFACT",
                reason="no candidate artifact for this case",
            )
            return member_native, applied

        # Priority over refusal reasons (most decision-relevant first).
        refusal_priority = (
            "MAGNITUDE_REFUSED",
            "OOS_HARM_REFUSED",
            "STATION_MISMATCH_REFUSED",
            "PRODUCT_MISMATCH_REFUSED",
            "STALE_REFUSED",
            "LOW_N_REFUSED",
        )
        chosen_status = "NO_ARTIFACT"
        chosen_reason = "no applicable artifact"
        chosen_artifact_id: tuple[str, ...] = ()
        ref_band_mean = 0.0
        ref_band_std = 0.0
        for status_name in refusal_priority:
            match = next(
                (
                    (artifact, reason)
                    for (artifact, status, reason) in evaluations
                    if status == status_name
                ),
                None,
            )
            if match is not None:
                artifact, reason = match
                chosen_status = status_name
                chosen_reason = reason
                chosen_artifact_id = (artifact.artifact_id,)
                ref_band_mean = float(artifact.residual_mean_native)
                ref_band_std = float(artifact.residual_std_native)
                break

        applied = AppliedDebias(
            artifact_ids=chosen_artifact_id,
            per_member_shift_native=zero_shift,
            aggregate_shift_native=0.0,
            trailing_residual_mean_native=ref_band_mean,
            trailing_residual_std_native=ref_band_std,
            activation_status=chosen_status,  # type: ignore[arg-type]
            reason=chosen_reason,
        )
        return member_native, applied
