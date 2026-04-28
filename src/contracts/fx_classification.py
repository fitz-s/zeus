"""Typed pUSD/USDC.e FX classification gate for R3 Z4.

Engineering may wire redemption/accounting to the edge of Q-FX-1, but the
operator must choose the classification before any pUSD redemption accounting
path proceeds. String literals are intentionally rejected at the boundary.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class FXClassification(str, Enum):
    TRADING_PNL_INFLOW = "TRADING_PNL_INFLOW"
    FX_LINE_ITEM = "FX_LINE_ITEM"
    CARRY_COST = "CARRY_COST"


class FXClassificationPending(RuntimeError):
    """Raised while Q-FX-1 remains open."""


def parse_fx_classification(value: str) -> FXClassification:
    try:
        return FXClassification(value)
    except ValueError:
        try:
            return FXClassification[value.upper()]
        except KeyError as exc:
            allowed = ", ".join(member.value for member in FXClassification)
            raise FXClassificationPending(
                f"unsupported ZEUS_PUSD_FX_CLASSIFIED={value!r}; expected one of {allowed}"
            ) from exc


def require_fx_classification(classification: Optional[FXClassification] = None) -> FXClassification:
    """Return the operator-selected FX classification or fail closed.

    If a caller provides ``classification`` it must already be an enum member.
    The environment flag is still required because Q-FX-1 is a dual process +
    runtime gate.
    """

    if classification is not None and not isinstance(classification, FXClassification):
        raise TypeError(
            "pUSD redemption FX classification must be FXClassification, "
            f"got {type(classification).__name__}"
        )
    raw = os.environ.get("ZEUS_PUSD_FX_CLASSIFIED", "").strip()
    if not raw:
        raise FXClassificationPending("Q-FX-1 open: ZEUS_PUSD_FX_CLASSIFIED is unset")
    selected = parse_fx_classification(raw)
    if classification is not None and classification is not selected:
        raise FXClassificationPending(
            "Q-FX-1 mismatch: provided classification "
            f"{classification.value} != env {selected.value}"
        )
    return selected
