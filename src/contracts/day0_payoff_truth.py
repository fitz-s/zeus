"""Selected-side payoff truth implied by a running Day0 extreme."""

from __future__ import annotations

import math
from enum import StrEnum


class Day0PayoffTruth(StrEnum):
    LOCKED = "locked"
    REFUTED = "refuted"
    UNRESOLVED = "unresolved"
    UNKNOWN = "unknown"


def classify_day0_payoff_truth(
    *,
    metric: str,
    direction: str,
    observed_extreme: float | int | None,
    bin_low: float | int | None,
    bin_high: float | int | None,
) -> Day0PayoffTruth:
    """Classify whether the observed monotone extreme fixes the selected payoff.

    A daily HIGH can only rise after the observation; a daily LOW can only fall.
    Therefore a finite bin crossed by the running extreme locks NO, while only
    the corresponding open shoulder can lock YES. Everything else still depends
    on the unobserved remainder of the day.
    """

    normalized_metric = str(metric or "").strip().lower()
    normalized_direction = str(direction or "").strip().lower()
    if normalized_direction in {"yes", "direction.yes"}:
        normalized_direction = "buy_yes"
    elif normalized_direction in {"no", "direction.no"}:
        normalized_direction = "buy_no"
    if normalized_metric not in {"high", "low"} or normalized_direction not in {
        "buy_yes",
        "buy_no",
    }:
        return Day0PayoffTruth.UNKNOWN
    try:
        observed = float(observed_extreme)
        low = None if bin_low is None else float(bin_low)
        high = None if bin_high is None else float(bin_high)
    except (TypeError, ValueError):
        return Day0PayoffTruth.UNKNOWN
    if not math.isfinite(observed) or (
        low is not None and not math.isfinite(low)
    ) or (high is not None and not math.isfinite(high)):
        return Day0PayoffTruth.UNKNOWN
    if low is None and high is None:
        return Day0PayoffTruth.UNKNOWN

    if normalized_metric == "high":
        yes_locked = high is None and low is not None and observed >= low
        yes_refuted = high is not None and observed > high
    else:
        yes_locked = low is None and high is not None and observed <= high
        yes_refuted = low is not None and observed < low

    selected_yes = normalized_direction == "buy_yes"
    if yes_locked:
        return Day0PayoffTruth.LOCKED if selected_yes else Day0PayoffTruth.REFUTED
    if yes_refuted:
        return Day0PayoffTruth.REFUTED if selected_yes else Day0PayoffTruth.LOCKED
    return Day0PayoffTruth.UNRESOLVED
