# Created: 2026-05-27
# Last reused or audited: 2026-05-27
# Authority basis: Exit Strategy math review (operator, 2026-05-27) — D2 of K=5
#   structural decisions. Truncates the entry-side family posterior by the
#   D1 SettlementProgressConstraint feasibility mask, then renormalises.
#
# Math (operator review §3):
#   p_i^obs = 0                              if i ∈ impossible mask
#   p_i^obs = p_i / Σ_{j feasible} p_j       otherwise
# If Σ_{j feasible} p_j ≤ ε, the model and observation contradict each other;
# return a contradiction-flagged result rather than fabricating a distribution.
#
# Boundaries (operator §3 last paragraph):
#   "If denominator is 0, then model distribution and observation authority
#   contradict; cannot pretend model is usable, must go fail-closed."
# This module flags contradiction; the caller (D3 family optimizer / D5 short-
# circuit) decides the fail-closed action (no-trade / exit-only / operator
# review).
#
# Non-mutation invariant (operator §3 last sentence): exit-only.
# This module MUST NOT be wired into entry p_cal, Platt, or any path that
# changes what entry sizing or family selection sees. It is a derived view
# used by exit math.
#
# Purity: total function over its inputs; no DB, no logging, no I/O.
"""Observation-constrained posterior (D2).

Consumes a family probability vector + bins + D1 constraint and returns the
exit-side posterior with impossible-bin mass zeroed and the remaining mass
renormalised. Flags contradictions instead of fabricating distributions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from src.strategy.exit_observation_constraint import (
    SettlementProgressConstraint,
)
from src.types.market import Bin


_DEFAULT_CONTRADICTION_EPS = 1e-9


@dataclass(frozen=True)
class ObservationConstrainedPosterior:
    """Result of constrain_family_posterior_by_observation.

    Fields:
      p_obs                  — post-truncation, renormalised family probability.
                                Length matches input p_family.
      impossible_mask        — True at indices the D1 constraint marked impossible.
                                All-False when constraint is ADVISORY_ONLY.
      renormalization_mass   — Σ p_i over feasible bins BEFORE renormalisation.
      contradiction_flag     — True when renormalization_mass ≤ eps; in this
                                case p_obs is all zero and the caller must
                                fail-closed (no-trade / exit-only).
      authority_status       — Mirrors constraint.authority_status so the
                                caller knows whether p_obs differs from p_family.
    """

    p_obs: tuple[float, ...]
    impossible_mask: tuple[bool, ...]
    renormalization_mass: float
    contradiction_flag: bool
    authority_status: str

    def any_impossible(self) -> bool:
        return any(self.impossible_mask)


def constrain_family_posterior_by_observation(
    p_family: Sequence[float],
    bins: Sequence[Bin],
    constraint: SettlementProgressConstraint,
    *,
    contradiction_eps: float = _DEFAULT_CONTRADICTION_EPS,
) -> ObservationConstrainedPosterior:
    """Project p_family through the D1 feasibility mask.

    Length of p_family and bins must match; mismatched lengths raise
    ValueError (semantic bug, not a runtime authority gap).
    NaN / negative entries in p_family also raise — these are entry-side
    invariants that must hold before the exit math runs.

    ADVISORY_ONLY constraint: returns p_family unchanged, no impossibility,
    no contradiction. authority_status="ADVISORY_ONLY".
    """
    n = len(p_family)
    if len(bins) != n:
        raise ValueError(
            f"length mismatch: p_family={n} bins={len(bins)}"
        )

    # Validate p_family entries up-front so callers get a fail-closed signal
    # for entry-side invariant violations rather than silent garbage.
    cleaned: list[float] = []
    for i, p in enumerate(p_family):
        try:
            v = float(p)
        except (TypeError, ValueError) as exc:  # noqa: BLE001
            raise ValueError(f"p_family[{i}] not numeric: {p!r}") from exc
        if math.isnan(v):
            raise ValueError(f"p_family[{i}] is NaN")
        if v < 0.0:
            raise ValueError(f"p_family[{i}] < 0: {v!r}")
        cleaned.append(v)

    # ADVISORY_ONLY: no observation authority; renormalize by total mass so
    # p_obs is always a probability vector regardless of constraint authority
    # status. Critic-pass-3 (Copilot 2026-05-27): without this, an
    # unnormalized p_family + ADVISORY_ONLY would feed raw weights into
    # optimize_exit_family's hold_value = shares × p_obs.
    if not constraint.is_deterministic():
        total_mass = float(sum(cleaned))
        if total_mass <= contradiction_eps:
            return ObservationConstrainedPosterior(
                p_obs=tuple(0.0 for _ in cleaned),
                impossible_mask=tuple(False for _ in cleaned),
                renormalization_mass=total_mass,
                contradiction_flag=True,
                authority_status=constraint.authority_status,
            )
        p_obs = tuple(v / total_mass for v in cleaned)
        return ObservationConstrainedPosterior(
            p_obs=p_obs,
            impossible_mask=tuple(False for _ in cleaned),
            renormalization_mass=total_mass,
            contradiction_flag=False,
            authority_status=constraint.authority_status,
        )

    impossible_mask = tuple(
        constraint.feasibility(b) == "impossible" for b in bins
    )

    feasible_mass = 0.0
    for v, is_imp in zip(cleaned, impossible_mask):
        if not is_imp:
            feasible_mass += v

    if feasible_mass <= contradiction_eps:
        # Model distribution + observation authority disagree at the family
        # level. Caller fail-closes; no fabricated distribution.
        return ObservationConstrainedPosterior(
            p_obs=tuple(0.0 for _ in cleaned),
            impossible_mask=impossible_mask,
            renormalization_mass=feasible_mass,
            contradiction_flag=True,
            authority_status=constraint.authority_status,
        )

    p_obs = tuple(
        0.0 if is_imp else (v / feasible_mass)
        for v, is_imp in zip(cleaned, impossible_mask)
    )

    return ObservationConstrainedPosterior(
        p_obs=p_obs,
        impossible_mask=impossible_mask,
        renormalization_mass=feasible_mass,
        contradiction_flag=False,
        authority_status=constraint.authority_status,
    )
