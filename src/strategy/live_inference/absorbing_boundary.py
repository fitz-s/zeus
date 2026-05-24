"""Day0 absorbing-boundary masks."""

from __future__ import annotations

from src.contracts.settlement_semantics import SettlementSemantics
from src.types.market import Bin


def day0_boundary_mask(
    *,
    bins: tuple[Bin, ...],
    metric: str,
    observed_extreme: float,
    settlement_semantics: SettlementSemantics,
) -> tuple[float, ...]:
    rounded = settlement_semantics.round_single(observed_extreme)
    mask: list[float] = []
    for bin_ in bins:
        if metric == "high":
            if bin_.is_open_high and bin_.low is not None and rounded >= float(bin_.low):
                mask.append(1.0)
            elif bin_.high is not None and rounded > float(bin_.high):
                mask.append(0.0)
            else:
                mask.append(1.0)
        elif metric == "low":
            if bin_.is_open_low and bin_.high is not None and rounded <= float(bin_.high):
                mask.append(1.0)
            elif bin_.low is not None and rounded < float(bin_.low):
                mask.append(0.0)
            else:
                mask.append(1.0)
        else:
            raise ValueError(f"unsupported metric {metric!r}")
    return tuple(mask)
