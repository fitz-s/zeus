# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL redesign P1 (residual-pairing seam); CRITIC_SYNTHESIS_2026-05-29
#   §2 C1 (source_kind='prior' hardcoded -> TIGGE/OpenData lineage collapse) + the
#   target-equality antibody (forecast_target.assert_same_target).
"""Residual-pairing seam: bind a ForecastObject to a SettlementObject into a typed,
correctly-keyed ResidualKey — and ONLY when they describe the same random variable.

Two structural fixes over the legacy ledger:
  1. ``source_kind`` is DERIVED from the forecast's data_version lineage
     (tigge_prior vs opendata_live), never the hardcoded literal 'prior' that
     collapsed TIGGE and OpenData provenance (build_ens_residual_evidence.py:227).
  2. The pairing routes through ``assert_same_target`` — a forecast cannot be paired
     to a settlement of a different RV (wrong date/station/unit/authority/metric).

The residual VALUE arithmetic (member mean, unit conversion) stays with the ledger;
this module owns the *identity + lineage* of the residual, so every residual the
ledger emits carries a single well-defined random variable and an honest source_kind.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.contracts.forecast_object import ForecastObject
from src.contracts.forecast_target import ForecastTarget, assert_same_target

# Lineage classes. "paired_delta" (a TIGGE<->OpenData same-window paired residual) is
# a two-forecast construction produced elsewhere; a single forecast<->settlement
# residual is one of the two below.
_TIGGE_PRIOR = "tigge_prior"
_OPENDATA_LIVE = "opendata_live"


def source_kind_for_data_version(data_version: str) -> str:
    """Classify a residual's lineage from its forecast data_version.

    Raises ValueError on an unrecognized lineage — an unknown source must not
    silently inherit a default tag (the legacy 'prior' collapse).
    """
    if data_version.startswith("tigge"):
        return _TIGGE_PRIOR
    if data_version.startswith("ecmwf_opendata"):
        return _OPENDATA_LIVE
    raise ValueError(
        f"source_kind refused: data_version={data_version!r} has no recognized "
        f"lineage prefix (expected 'tigge*' or 'ecmwf_opendata*'). Refusing to "
        f"assign a default tag — that is the lineage-collapse bug this gate prevents."
    )


@dataclass(frozen=True)
class SettlementObject:
    """The settlement outcome that is a forecast's payout truth."""

    target: ForecastTarget
    settlement_value: float


@dataclass(frozen=True)
class ResidualKey:
    """Identity + lineage of a single residual. The keying dims a product-segregated,
    lead-respecting bias model needs (product, cycle, lead_hours) plus the RV target
    and the derived source_kind. Carries NO value — the ledger computes residual_c.
    """

    target: ForecastTarget
    product: str
    cycle: str
    lead_hours: float
    source_kind: str


def pair_residual(forecast: ForecastObject, settlement: SettlementObject) -> ResidualKey:
    """Return the keyed identity of the residual binding ``forecast`` to ``settlement``.

    Raises ForecastTargetMismatchError if the two describe different random variables.
    """
    shared_target = assert_same_target(forecast.target, settlement.target)
    return ResidualKey(
        target=shared_target,
        product=forecast.product,
        cycle=forecast.cycle,
        lead_hours=forecast.lead_hours,
        source_kind=source_kind_for_data_version(forecast.data_version),
    )
