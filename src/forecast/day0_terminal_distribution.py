# Created: 2026-09-24
# Last reused or audited: 2026-09-24
# Authority basis: resolver-graded Day0 observation model (external review
#   2026-09-24, design decision item 3).
"""Resolver-graded Day0 terminal distribution — a pure operator.

With ``A`` the contract-rounded running extreme possessed at decision time and
``v = +1`` (HIGH) / ``-1`` (LOW)::

    Q(y) = s * Q+(y)                    when v(y - A) >= 0
    Q(y) = (1 - s) * G-(-v(y - A))      when v(y - A) <  0

``s`` is the resolver-graded terminal non-violation probability and ``G-`` the
failure-magnitude law over explicit steps ``k = 1..K`` plus one overflow
category ``k > K``.  The overflow mass follows the geometric tail
``P(k) = 2^-(k - K)``, so it lands only on bins that contain such values.

``Q+`` is the remaining-extreme template, restricted to the non-violation side
and renormalized; it keeps the atom at the observed bin.  The terminal
non-violation mass is therefore exactly ``s``.  Nothing may be applied after
this operator: no ``max(observed, .)``, no impossible-bin mask and no survival
mixture — each would move mass across the ``v(y - A) = 0`` boundary a second
time.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

Bounds = tuple[float | None, float | None]


def _integer_partition(bins: Sequence[Bounds], *, name: str) -> tuple[Bounds, ...]:
    """Validate a strict integer partition of the whole line (shoulders open)."""

    parsed: list[Bounds] = []
    for low, high in bins:
        for edge in (low, high):
            if edge is not None and (not np.isfinite(edge) or float(edge) != round(float(edge))):
                raise ValueError(f"DAY0_TERMINAL_{name}_BOUNDS_INVALID")
        low_f = None if low is None else float(low)
        high_f = None if high is None else float(high)
        if (low_f is None and high_f is None) or (
            low_f is not None and high_f is not None and low_f > high_f
        ):
            raise ValueError(f"DAY0_TERMINAL_{name}_BOUNDS_INVALID")
        parsed.append((low_f, high_f))
    ordered = sorted(parsed, key=lambda item: -np.inf if item[0] is None else item[0])
    if not ordered or ordered[0][0] is not None or ordered[-1][1] is not None:
        raise ValueError(f"DAY0_TERMINAL_{name}_SHOULDER_TOPOLOGY_INVALID")
    for previous, current in zip(ordered, ordered[1:]):
        if previous[1] is None or current[0] is None or current[0] != previous[1] + 1.0:
            raise ValueError(f"DAY0_TERMINAL_{name}_GAP_OR_OVERLAP")
    return tuple(parsed)


def unit_settlement_grid(
    bins: Sequence[Bounds], *, observed: float, steps: int
) -> tuple[Bounds, ...]:
    """Unit-step grid covering every bin edge and ``A +/- (steps + 1)``, with shoulders."""

    edges = [float(edge) for pair in bins for edge in pair if edge is not None]
    lo = int(min((*edges, observed - steps - 1.0)))
    hi = int(max((*edges, observed + steps + 1.0)))
    return (
        (None, lo - 1.0),
        *((float(value), float(value)) for value in range(lo, hi + 1)),
        (hi + 1.0, None),
    )


def nesting_map(fine_bins: Sequence[Bounds], bins: Sequence[Bounds]) -> np.ndarray:
    """``(len(bins), len(fine_bins))`` 0/1 map; each fine bin lies in exactly one bin."""

    fine = _integer_partition(fine_bins, name="TEMPLATE")
    target = _integer_partition(bins, name="BIN")
    out = np.zeros((len(target), len(fine)), dtype=float)
    for column, (low, upper) in enumerate(fine):
        rows = [
            row
            for row, (b_low, b_high) in enumerate(target)
            if (b_low is None or (low is not None and low >= b_low))
            and (b_high is None or (upper is not None and upper <= b_high))
        ]
        if len(rows) != 1:
            raise ValueError("DAY0_TERMINAL_TEMPLATE_NOT_NESTED_IN_BINS")
        out[rows[0], column] = 1.0
    return out


def _overflow_mass(k_low: float, k_high: float, steps: int) -> float:
    """Mass of ``k in [k_low, k_high]`` under the overflow tail ``2^-(k - steps)``."""

    low = max(k_low, steps + 1.0)
    if low > k_high:
        return 0.0
    at_least_low = 2.0 ** -(low - steps - 1.0)
    beyond_high = 0.0 if k_high == np.inf else 2.0 ** -(k_high - steps)
    return at_least_low - beyond_high


def terminal_composition_maps(
    *,
    template_bins: Sequence[Bounds],
    observed: float,
    metric: str,
    bins: Sequence[Bounds],
    failure_steps: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (template non-violation mask, Q+ -> bins map, G- -> bins map).

    Every template bin must lie entirely on one side of ``A`` and inside
    exactly one target bin.
    """

    if metric not in {"high", "low"} or failure_steps < 1:
        raise ValueError("DAY0_TERMINAL_COMPOSITION_INPUT_INVALID")
    if not np.isfinite(observed) or float(observed) != round(float(observed)):
        raise ValueError("DAY0_TERMINAL_OBSERVED_NOT_ON_SETTLEMENT_GRID")
    fine = _integer_partition(template_bins, name="TEMPLATE")
    target = _integer_partition(bins, name="BIN")
    a = float(observed)
    high = metric == "high"

    mask = np.zeros(len(fine), dtype=float)
    for index, (low, upper) in enumerate(fine):
        if high:
            nonviolation = low is not None and low >= a
            violation = upper is not None and upper < a
        else:
            nonviolation = upper is not None and upper <= a
            violation = low is not None and low > a
        if nonviolation == violation:
            raise ValueError("DAY0_TERMINAL_TEMPLATE_BIN_STRADDLES_OBSERVED")
        mask[index] = 1.0 if nonviolation else 0.0
    plus_map = nesting_map(fine, target) * mask

    minus_map = np.zeros((len(target), failure_steps + 1), dtype=float)
    for row, (b_low, b_high) in enumerate(target):
        lo = -np.inf if b_low is None else b_low
        hi = np.inf if b_high is None else b_high
        if high:
            y_high = min(hi, a - 1.0)
            if lo > y_high:
                continue
            k_low, k_high = a - y_high, a - lo
        else:
            y_low = max(lo, a + 1.0)
            if y_low > hi:
                continue
            k_low, k_high = y_low - a, hi - a
        for step in range(1, failure_steps + 1):
            if k_low <= step <= k_high:
                minus_map[row, step - 1] = 1.0
        minus_map[row, failure_steps] = _overflow_mass(k_low, k_high, failure_steps)
    return mask, plus_map, minus_map


def compose_resolver_terminal_distribution(
    *,
    template: Sequence[float] | np.ndarray,
    template_bins: Sequence[Bounds],
    observed: float,
    metric: str,
    nonviolation_probability: float | np.ndarray,
    failure_magnitude: Sequence[float] | np.ndarray,
    bins: Sequence[Bounds],
) -> np.ndarray:
    """Compose ``Q`` over ``bins``; rows broadcast when inputs are 2-D.

    ``template`` is ``(fine,)`` or ``(rows, fine)`` over ``template_bins``;
    ``nonviolation_probability`` is a scalar or ``(rows,)``;
    ``failure_magnitude`` is ``(K + 1,)`` or ``(rows, K + 1)`` with the
    overflow category last.
    """

    template_arr = np.asarray(template, dtype=float)
    s = np.asarray(nonviolation_probability, dtype=float)
    g = np.asarray(failure_magnitude, dtype=float)
    if (
        template_arr.shape[-1] != len(template_bins)
        or g.ndim not in {1, 2}
        or g.shape[-1] < 2
        or not np.isfinite(template_arr).all()
        or np.any(template_arr < 0.0)
        or not np.isfinite(s).all()
        or np.any(s < 0.0)
        or np.any(s > 1.0)
        or not np.isfinite(g).all()
        or np.any(g < 0.0)
        or not np.allclose(g.sum(axis=-1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise ValueError("DAY0_TERMINAL_COMPOSITION_INPUT_INVALID")
    mask, plus_map, minus_map = terminal_composition_maps(
        template_bins=template_bins,
        observed=observed,
        metric=metric,
        bins=bins,
        failure_steps=g.shape[-1] - 1,
    )
    kept = template_arr * mask
    kept_total = kept.sum(axis=-1, keepdims=True)
    if np.any(kept_total <= 0.0):
        raise ValueError("DAY0_TERMINAL_TEMPLATE_HAS_NO_NONVIOLATION_MASS")
    q_plus = kept / kept_total
    s_col = s[..., None]
    return s_col * (q_plus @ plus_map.T) + (1.0 - s_col) * (g @ minus_map.T)
