# Created: 2026-05-29
# Last reused or audited: 2026-05-29
# Authority basis: TRIBUNAL Findings 1+5 — lead/cycle/product keyed ENS bias correction.
#   Coarse 4-bucket design is intentional: MIN_PAIRED_N=5 drives sample-starvation risk
#   with finer granularity. Boundaries are a tunable P3 decision; the short-lead (L00_24)
#   / long-lead split is mandatory because short-lead forecasts exhibit a sign-flip
#   relative to lead-48 (pooling L00_24 into L24_48 mis-applies the wrong-sign correction).
"""Lead-hour bucket helper for ENS bias/error-model keying.

``lead_bucket(lead_hours)`` maps a raw lead to one of four coarse buckets:

    L00_24  — 0 ≤ h < 24   (short lead; sign-flip behaviour vs long-lead)
    L24_48  — 24 ≤ h < 48  (medium lead)
    L48_96  — 48 ≤ h < 96  (extended medium)
    L96_plus — h ≥ 96      (long-range)

The boundary constant ``LEAD_BUCKET_BOUNDS`` is a module-level tuple of
``(lower_inclusive, upper_exclusive, label)`` entries in ascending order.
"""
from __future__ import annotations

# (lower_inclusive_hours, upper_exclusive_hours, label)
# Four buckets — coarse on purpose (MIN_PAIRED_N=5, sample starvation risk with finer cuts).
# The L00_24 / L24_48 split is the MANDATORY boundary: short-lead (<24h) bias sign often
# flips sign vs medium/long lead; mixing them produces a correction that is wrong for both.
LEAD_BUCKET_BOUNDS: tuple[tuple[float, float, str], ...] = (
    (0.0,   24.0,  "L00_24"),
    (24.0,  48.0,  "L24_48"),
    (48.0,  96.0,  "L48_96"),
    (96.0, float("inf"), "L96_plus"),
)


def lead_bucket(lead_hours: float) -> str:
    """Return the lead-bucket label for a given lead in hours.

    Uses ``LEAD_BUCKET_BOUNDS`` (module-level constant). The upper boundary of each
    bucket is exclusive; ``lead_hours`` that fall exactly on a boundary boundary go to
    the HIGHER bucket (e.g. 24.0 → 'L24_48', not 'L00_24').

    Raises ``ValueError`` for negative leads (negative lead is not physically meaningful).
    """
    if lead_hours < 0:
        raise ValueError(f"lead_bucket: lead_hours must be >= 0, got {lead_hours!r}")
    for lo, hi, label in LEAD_BUCKET_BOUNDS:
        if lo <= lead_hours < hi:
            return label
    # Fallback: exactly at or beyond the last upper bound (shouldn't happen with inf).
    return LEAD_BUCKET_BOUNDS[-1][2]
