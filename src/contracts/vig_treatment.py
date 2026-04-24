# Created: 2026-04-12
# Last reused/audited: 2026-04-24
# Authority basis: D5 resolution + T6.3 sparse-monitor impute provenance (midstream fix plan 2026-04-23)
"""Vig normalization contract — D5 resolution + T6.3 sparse-monitor impute provenance.

D5 gap: p_market includes vig (~0.90–1.10). compute_posterior() blends
α × p_cal + (1-α) × p_market then normalizes. This smears vig bias across all
bins because the blend happens before vig removal. The normalization step partially
corrects it but the blend coefficients were already contaminated.

Resolution: Vig normalization (p_market_clean = p_market / vig) must happen
BEFORE the blend, under a declared VigTreatment contract.

T6.3 extension (2026-04-24): sparse-monitor p_market (zeros for non-held bins)
may now be repaired via `sibling_snapshot` impute, with `imputation_source`
recording which reference was used (sibling_market vs p_cal_fallback vs none).
This supersedes B086 — which rejected silent p_cal impute — by making the
imputation explicit and traceable via the typed record. See:
docs/archives/local_scratch/2026-04-19/zeus_data_improve_bug_audit_100_resolved.md:17
(B086 entry carries the T6.3 supersedence pointer).

See: docs/zeus_FINAL_spec.md §P9.3 D5
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np


ImputationSource = Literal["none", "sibling_market", "p_cal_fallback"]


@dataclass(frozen=True)
class VigTreatment:
    """Typed record of vig normalization applied before market-model blending.

    Resolves D5: ensures vig is removed before the posterior blend, not after,
    and that the cleaning step is explicit and auditable. Also carries T6.3
    sparse-impute provenance when the raw market vector had zero-imputed bins.

    Attributes:
        raw_market_prices: Original p_market array.
        vig_factor: The vig total. For complete-market treatments this is
            sum(raw_market_prices) and clean_prices = raw / vig_factor sums to
            ~1.0. For sparse-imputed treatments vig_factor is set to 1.0 by
            convention (see T6.3 note below) and clean_prices may sum to > 1.0.
            NOTE: 1.0 alone is NOT a reliable sparse discriminator — a
            legitimate vig-free complete market also produces vig_factor=1.0.
            The authoritative sparse marker is `imputed_bins` non-empty OR
            `imputation_source != "none"`.
        clean_prices: Market probabilities suitable for blending with p_cal.
            Complete-market: sums to ~1.0 after vig removal. Sparse-imputed:
            raw zero-bins replaced by sibling_snapshot values; DOES NOT
            devig the mixed vector because vig semantics don't apply to a
            mix of observed market prices and model-prior fills. The final
            normalization happens in the caller (compute_posterior).
        applied_before_blend: Must be True. If False, the vig was removed AFTER
            blending, which is the D5 violation pattern.
        imputed_bins: Indices of bins that were zero in raw_market_prices and
            were replaced via sibling_snapshot. Empty tuple for complete-market
            treatments or when no sibling_snapshot was supplied.
        imputation_source: One of {"none", "sibling_market", "p_cal_fallback"}.
            Records which reference vector provided the imputed values. "none"
            means no impute happened (either complete market, or sparse with
            no fallback supplied — raw zeros preserved per B086).

    T6.3 note on vig semantics for imputed sparse vectors:
        A sparse monitor refresh observes only the held bin's best_bid. The
        non-held bins are unknown; filling them with p_cal (or a sibling
        market snapshot) introduces values that are NOT vig-bearing (p_cal
        is a model prior, not a bookie's offer). Devigging the mixed vector
        would apply vig correction to model priors, distorting them. So
        VigTreatment skips devig for the imputed path; compute_posterior's
        final normalization handles the overall shape.
    """

    raw_market_prices: np.ndarray
    vig_factor: float
    clean_prices: np.ndarray
    applied_before_blend: bool
    imputed_bins: tuple = field(default_factory=tuple)
    imputation_source: ImputationSource = "none"

    def __post_init__(self) -> None:
        if not np.all(np.isfinite(self.raw_market_prices)):
            raise ValueError("VigTreatment.raw_market_prices must be finite")
        if np.any(self.raw_market_prices < 0.0):
            raise ValueError("VigTreatment.raw_market_prices must be non-negative")
        if not np.all(np.isfinite(self.clean_prices)):
            raise ValueError("VigTreatment.clean_prices must be finite")
        if np.any(self.clean_prices < 0.0):
            raise ValueError("VigTreatment.clean_prices must be non-negative")
        if self.vig_factor <= 0.0:
            raise ValueError(
                f"VigTreatment.vig_factor must be > 0, got {self.vig_factor}"
            )
        if len(self.raw_market_prices) != len(self.clean_prices):
            raise ValueError(
                f"VigTreatment: raw_market_prices length {len(self.raw_market_prices)} "
                f"!= clean_prices length {len(self.clean_prices)}"
            )

        if self.imputation_source not in ("none", "sibling_market", "p_cal_fallback"):
            raise ValueError(
                f"VigTreatment.imputation_source must be one of "
                f"{{'none','sibling_market','p_cal_fallback'}}, "
                f"got {self.imputation_source!r}"
            )

        if self.imputation_source == "none" and self.imputed_bins:
            raise ValueError(
                f"VigTreatment.imputed_bins={self.imputed_bins} is non-empty "
                f"but imputation_source='none'. When impute happened, source "
                f"must name the reference vector."
            )
        if self.imputation_source != "none" and not self.imputed_bins:
            raise ValueError(
                f"VigTreatment.imputation_source={self.imputation_source!r} "
                f"declares impute, but imputed_bins is empty. When no bins "
                f"needed impute, source should be 'none'."
            )

        # T6.3-hardening (con-nyx post-edit finding b): each entry in
        # imputed_bins MUST correspond to a raw_market_prices position that
        # was zero pre-impute. Otherwise a direct dataclass construction
        # (bypassing from_raw) could lie about which bins were imputed.
        # This closes a new silent-failure category: bogus provenance claims.
        for i in self.imputed_bins:
            if self.raw_market_prices[i] != 0.0:
                raise ValueError(
                    f"VigTreatment.imputed_bins includes index {i} but "
                    f"raw_market_prices[{i}]={self.raw_market_prices[i]} "
                    f"is non-zero. Imputation only applies to zero bins."
                )

        # Complete-market vectors devig to sum-to-1. Sparse-imputed vectors
        # keep mixed observed+prior values and defer normalization to caller.
        if not self.imputed_bins:
            clean_sum = float(np.sum(self.clean_prices))
            if not (0.99 <= clean_sum <= 1.01):
                raise ValueError(
                    f"VigTreatment.clean_prices must sum to ~1.0 for complete "
                    f"market, got {clean_sum:.4f}. Ensure "
                    f"clean_prices = raw_market_prices / vig_factor."
                )

        if not self.applied_before_blend:
            raise VigOrderError(
                "VigTreatment.applied_before_blend=False. Vig must be removed "
                "BEFORE blending with p_cal. Post-blend normalization smears vig "
                "bias across all bins (D5 violation)."
            )

    @classmethod
    def from_raw(
        cls,
        raw_market_prices: np.ndarray,
        *,
        sibling_snapshot: Optional[np.ndarray] = None,
        imputation_source: ImputationSource = "none",
    ) -> "VigTreatment":
        """Factory: compute vig_factor and clean_prices from raw market prices.

        This is the canonical way to construct a VigTreatment — it ensures
        vig removal happens at construction time (before any blend call).

        T6.3 extension:
            If `sibling_snapshot` is supplied AND raw_market_prices has zero
            entries, those zero entries are replaced with sibling_snapshot[i]
            at the matching positions. The resulting vector is NOT devigged
            (see module docstring for rationale). imputed_bins records which
            positions were imputed; imputation_source must be passed by the
            caller to record the semantic origin of sibling_snapshot.

            If sibling_snapshot is None: complete-market devig path (unchanged).
            If sibling_snapshot is supplied but raw_market_prices has no zeros:
            sibling_snapshot is ignored, complete-market devig path.
            If sibling_snapshot is supplied, has zeros, but imputation_source
            is "none": error — caller must declare provenance.

        Args:
            raw_market_prices: Observed market vector (may be sparse).
            sibling_snapshot: Optional reference vector same length as
                raw_market_prices. Used to fill zero positions when sparse.
            imputation_source: Required when sibling_snapshot imputes non-empty.
                Must be "sibling_market" (real cross-market reference) or
                "p_cal_fallback" (caller used p_cal as a fallback reference).

        Raises:
            ValueError: for finite/negative/length/source validation failures.
        """
        if not np.all(np.isfinite(raw_market_prices)):
            raise ValueError("raw_market_prices must be finite")
        if np.any(raw_market_prices < 0.0):
            raise ValueError("raw_market_prices must be non-negative")

        if sibling_snapshot is None:
            # Complete-market devig path (backwards-compatible behavior).
            # T6.3-hardening (con-nyx post-edit finding j): if caller supplied
            # imputation_source != "none" but forgot sibling_snapshot, raise
            # instead of silently resetting source to "none". This closes a
            # caller-intent-vs-implementation drift silent-failure category.
            if imputation_source != "none":
                raise ValueError(
                    f"VigTreatment.from_raw: imputation_source="
                    f"{imputation_source!r} declared but sibling_snapshot is "
                    f"None. Provide sibling_snapshot (even a zero-vector) or "
                    f"leave imputation_source='none'."
                )
            vig_factor = float(np.sum(raw_market_prices))
            if vig_factor <= 0.0:
                raise ValueError(
                    f"raw_market_prices sum to {vig_factor}, cannot compute vig"
                )
            clean_prices = raw_market_prices / vig_factor
            return cls(
                raw_market_prices=raw_market_prices,
                vig_factor=vig_factor,
                clean_prices=clean_prices,
                applied_before_blend=True,
                imputed_bins=(),
                imputation_source="none",
            )

        # sibling_snapshot path — impute zero positions.
        sibling = np.asarray(sibling_snapshot, dtype=float)
        if sibling.shape != raw_market_prices.shape:
            raise ValueError(
                f"sibling_snapshot shape {sibling.shape} != "
                f"raw_market_prices shape {raw_market_prices.shape}"
            )
        if not np.all(np.isfinite(sibling)):
            raise ValueError("sibling_snapshot must be finite")
        if np.any(sibling < 0.0):
            raise ValueError("sibling_snapshot must be non-negative")

        zero_mask = raw_market_prices == 0.0
        # Only positions where raw is zero AND sibling provides a non-zero
        # replacement count as imputed. Zero-for-zero substitutions are
        # no-ops and must NOT be recorded in imputed_bins — otherwise an
        # auditor reading `imputed_bins=(0,)` would assume "bin 0 was
        # repaired from sibling" when in fact it remains a raw zero.
        # Surrogate-critic 2026-04-24 MED finding.
        effective_impute_mask = zero_mask & (sibling > 0.0)
        impute_indices = tuple(int(i) for i in np.nonzero(effective_impute_mask)[0])

        if not impute_indices:
            # Either raw has no zeros, or all zero positions had zero
            # sibling values — no actual imputation happened. Reduce to
            # complete-market devig path; the declared imputation_source
            # is demoted to "none" because the typed record should only
            # claim imputation when imputation measurably occurred.
            vig_factor = float(np.sum(raw_market_prices))
            if vig_factor <= 0.0:
                raise ValueError(
                    f"raw_market_prices sum to {vig_factor}, cannot compute vig"
                )
            clean_prices = raw_market_prices / vig_factor
            return cls(
                raw_market_prices=raw_market_prices,
                vig_factor=vig_factor,
                clean_prices=clean_prices,
                applied_before_blend=True,
                imputed_bins=(),
                imputation_source="none",
            )

        if imputation_source == "none":
            raise ValueError(
                "sibling_snapshot supplied with effectively-imputable zero "
                "bins, but imputation_source='none'. Declare "
                "'sibling_market' or 'p_cal_fallback' so the typed record "
                "is auditable."
            )

        # Fill only positions where sibling has a non-zero replacement.
        # Zero-for-zero positions stay at raw zero (no false provenance claim).
        imputed = np.where(effective_impute_mask, sibling, raw_market_prices)
        # vig_factor = 1.0 by convention: no vig applied to the mixed
        # observed-price + prior-fill vector. See module docstring; note
        # 1.0 is NOT a unique sparse discriminator on its own.
        return cls(
            raw_market_prices=raw_market_prices,
            vig_factor=1.0,
            clean_prices=imputed,
            applied_before_blend=True,
            imputed_bins=impute_indices,
            imputation_source=imputation_source,
        )


class VigOrderError(Exception):
    """Raised when VigTreatment is constructed with applied_before_blend=False.
    This is the D5 runtime contract violation — vig applied after blending
    smears the vig bias across all bins, distorting edge signal.
    """
