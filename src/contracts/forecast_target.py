# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (ForecastObject/SettlementObject contract);
#   CRITIC_SYNTHESIS_2026-05-29 §2 (lineage collapse; degC/degF mis-scale Cons-SEV-1.C;
#   target-equality completeness Cons-SEV-2-E). Pattern follows
#   src/contracts/alpha_decision.py (frozen dataclass + assert_* + typed mismatch error).
"""Forecast random-variable target identity.

A ``ForecastTarget`` is the tuple that defines *which* random variable a forecast
row is about and which settlement outcome is its payout truth. A residual binding
a forecast to a settlement is only valid when both sides carry an *identical*
target across every dimension — otherwise the residual measures the error of the
wrong random variable.

This is the antibody for the mixed-RV bug family the redesign exists to kill:
TIGGE-6h residuals paired to the wrong/absent settlement (lineage collapse), and
degC/degF unit mis-scale. The target carries the settlement-side identity
(station, unit, authority) precisely because "Chicago HIGH 2026-05-20" is not a
random variable until the settlement station, unit, and source authority are
pinned — two of those differing is two different payout truths.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

# Dimensions that define the random variable. Order is the message order.
_TARGET_DIMENSIONS: tuple[str, ...] = (
    "city",
    "metric",
    "target_local_date",
    "settlement_station",
    "settlement_unit",
    "settlement_authority",
)


class ForecastTargetMismatchError(Exception):
    """Raised when a forecast-side target and a settlement-side target disagree on
    any dimension that defines the random variable. Forming a residual across a
    mismatch would measure the error of the wrong RV (lineage collapse / unit
    mis-scale). This is the redesign's structural antibody.
    """


@dataclass(frozen=True)
class ForecastTarget:
    """Immutable identity of the forecast random variable + its settlement truth.

    Attributes:
        city: settlement city key.
        metric: "HIGH" or "LOW" (daily extreme family).
        target_local_date: local calendar day the contract settles on (ISO date str).
        settlement_station: the station whose observation IS the payout truth
            (e.g. "KORD"). Two stations in the same city are two different RVs.
        settlement_unit: unit the settlement is measured/rounded in ("degC"/"degF").
            A forecast compared in the wrong unit silently mis-scales (Cons-SEV-1.C).
        settlement_authority: the source-authority data_version that owns the truth
            (e.g. "wu_icao_history_v1"). Different authority = different truth basis.
    """

    city: str
    metric: str
    target_local_date: str
    settlement_station: str
    settlement_unit: str
    settlement_authority: str


def assert_same_target(
    forecast_side: ForecastTarget,
    settlement_side: ForecastTarget,
) -> ForecastTarget:
    """Return the shared target if both sides are identical; else raise.

    Compares every dimension in ``_TARGET_DIMENSIONS``. On any difference, raises
    ``ForecastTargetMismatchError`` naming the mismatched dimension(s) and the two
    values — so a residual can only be constructed for a single, well-defined RV.
    """
    mismatches: list[str] = []
    for dim in _TARGET_DIMENSIONS:
        fval = getattr(forecast_side, dim)
        sval = getattr(settlement_side, dim)
        if fval != sval:
            mismatches.append(f"{dim}: forecast={fval!r} settlement={sval!r}")
    if mismatches:
        raise ForecastTargetMismatchError(
            "forecast and settlement targets disagree on "
            f"{len(mismatches)} dimension(s): " + "; ".join(mismatches)
        )
    return forecast_side
