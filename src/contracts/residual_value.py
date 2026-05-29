# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL P2 hard seam; CRITIC_SYNTHESIS_2026-05-29 Cons-SEV-1.C.
#   Replaces build_ens_residual_evidence.py's members_unit-for-settlement conversion
#   (a masked degC/degF corruption) with own-unit conversion of each side.
"""Unit-correct residual arithmetic.

The legacy ledger converted the settlement value with the ensemble's ``members_unit``;
that is correct only when both sides happen to share a unit. Ensemble members and the
settlement value are stored in DIFFERENT units across sources (OpenData members in degF,
the provenance contract's degC convention, WU settlements in degF), so the residual must
convert EACH side by ITS OWN unit. This module is the single place that arithmetic lives,
so the °C/°F corruption category is unconstructable in the residual path.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

_VALID_UNITS = frozenset({"degC", "degF"})


class ResidualUnitError(ValueError):
    """Raised on a missing/invalid temperature unit in residual arithmetic.

    Kelvin and None are rejected: a residual computed against an unverified unit is the
    silent-mis-scale bug this module exists to prevent.
    """


def _to_celsius(value: float, unit: str | None) -> float:
    if unit not in _VALID_UNITS:
        raise ResidualUnitError(
            f"residual refused: temperature unit {unit!r} is not one of "
            f"{sorted(_VALID_UNITS)}. Convert/declare the unit before forming a residual."
        )
    if unit == "degF":
        return (value - 32.0) * 5.0 / 9.0
    return value


def residual_celsius(
    ensemble_members: Sequence[float],
    members_unit: str | None,
    settlement_value: float,
    settlement_unit: str | None,
) -> float:
    """Return ``mean(members)_°C − settlement_°C`` (forecast minus actual).

    Each side is converted by ITS OWN unit. Raises ResidualUnitError on a bad unit and
    ValueError on empty members. The sign convention (ensemble − settlement) matches the
    legacy ledger's bias direction.
    """
    if not ensemble_members:
        raise ValueError("residual refused: ensemble_members is empty.")
    mean_c = _to_celsius(statistics.mean(ensemble_members), members_unit)
    settle_c = _to_celsius(settlement_value, settlement_unit)
    return mean_c - settle_c
