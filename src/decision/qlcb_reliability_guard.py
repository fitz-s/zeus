# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: docs/evidence/coarse_global_removal/FINAL_no_shadow_execution_flow_2026-06-18.md
#   §"THE q_lcb RELIABILITY GUARD — exact form" + step 6/7 of the NO-SHADOW EXECUTION FLOW.
#   Operator RAW no-de-bias law: q_lcb is made honest by an EMPIRICAL out-of-fold reliability
#   guard that does NOT move μ (not a de-bias → law-compliant) and is the LIVE SERVING RULE
#   (not a parallel product → no shadow). Artifact-gated like the settlement σ-floor
#   (src/data/replacement_forecast_materializer._replacement_sigma_scale_lookup precedent):
#   INERT (pass-through, no abstain) when the OOF reliability table is absent — byte-identical
#   to pre-guard behavior — and ACTIVE the moment the operator places the table in state/.
"""q_lcb empirical reliability guard — the RAW-honest serving rule (no shadow, no de-bias).

THE CRUX (FINAL no-shadow execution flow): a RAW (uncorrected) center has a per-city
LOCATION bias that width calibration cannot fix, so the predictive PIT is miscalibrated and
the raw q_lcb is NOT automatically honest. The law forbids correcting the center (no de-bias)
and forbids a shadow product. The resolution is an EMPIRICAL OUT-OF-FOLD RELIABILITY GUARD on
the SERVED q_lcb:

    q_safe[bin] = min( band.q_lcb[bin], L_g )

where ``L_g`` is the one-sided Wilson 95% LOWER bound of the realized OOF hit-rate in the
reliability CELL ``g = (metric, lead_bucket, bin_position, q_lcb_bucket)`` — NOT per-city (a
per-city offset would BE a fitted de-bias). The candidate may trade ONLY if ALL hold:

    * ``N_g >= N_MIN``                          (the cell has enough OOF evidence)
    * ``L_g >= q_lcb_bucket_floor − EPS``       (the realized frequency supports the bucket)
    * ``q_safe − price − cost > EDGE_FLOOR``    (real after-cost edge survives the deflation)

otherwise the candidate ABSTAINS: ``q_safe = 0`` (publish the point prob, do NOT trade). This
moves NO μ (it only serves a lower bound the realized frequency supports) and it is the live
serving rule applied where the decision layer consumes q_lcb (family_decision_engine). If it
abstains globally, the correct DIRECT decision is "do not trade those bins" — never "quietly
use EB".

ARTIFACT-GATED (no shadow, fail-closed-to-inert): the OOF reliability table is read from
``state/qlcb_oof_reliability.json`` (gitignored generated artifact, same posture as the σ-floor
and the anchor-debias artifacts). When the artifact is ABSENT (the current live state) the
guard is INERT — it serves ``band.q_lcb`` unchanged and abstains on NOTHING — so the live q is
byte-identical to pre-guard behavior. It goes live the moment the operator places the fitted
OOF reliability table in state/ and restarts. The table itself is built OFFLINE from settled
OOF predictions (the same settlement truth everything else grades on); this module only READS
it and applies the Wilson lower bound + the trade/abstain rule. It NEVER fits per-city offsets,
NEVER moves μ, and NEVER constructs a parallel q.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Mapping, Optional

# ---------------------------------------------------------------------------
# Named constants (sane defaults; NO magic in the hot path).
# ---------------------------------------------------------------------------

# Minimum OOF sample count in a reliability cell before the cell may license a trade.
# Below this the cell is "thin" -> abstain (the realized frequency is not yet trustworthy).
N_MIN: int = 30

# After-cost edge floor (probability units). q_safe − price − cost must EXCEED this to trade.
# 0.0 keeps the conservative edge_lcb>0 bar; a positive value raises the trade bar uniformly.
EDGE_FLOOR: float = 0.0

# Tolerance ε when comparing the Wilson lower bound L_g to the q_lcb bucket floor. A cell whose
# realized lower bound is within EPS of its bucket floor is treated as supporting the bucket.
EPS: float = 0.02

# The q_lcb bucket edges (probability units). A served q_lcb falls into the bucket whose
# [lo, hi) it lands in; the bucket's FLOOR is its lower edge — the minimum realized hit-rate the
# bucket claims to support. These are the SERVING buckets the OOF table is keyed by.
#
# 2026-06-18 REFINE: uniform 0.05-width buckets across [0, 1]. The prior (0.0, 0.5, 0.6, ...)
# scheme had a single [0, 0.5) bucket that swallowed the ENTIRE live q_lcb mass — under 1°-wide
# settlement bins the band 5th-percentile q_lcb effectively never exceeds 0.5 even on the modal
# bin (a 1°-bin captures the settlement < ~0.30 of the time), so the buckets ≥ 0.5 were empty and
# the guard degenerated to a single flat ceiling (deflate-to-pooled-Wilson, no confidence
# resolution). The 0.05 grid resolves the [0, 0.5) region into 10 calibration cells: an
# over-confident bin (high band q_lcb but low realized rate) now lands in a high-floor bucket and
# ABSTAINS (over-claim rejected), while a genuinely-low q_lcb in a low-floor bucket is licensed
# and only deflated to its realized Wilson LB. Sparse high buckets simply fail N_MIN -> INERT
# (no false haircut where there is no evidence). The OOF builder imports THIS tuple (single
# source) so the table cells are keyed by the same grid the live guard buckets into.
QLCB_BUCKET_EDGES: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(21))

# Wilson interval z for a one-sided 95% lower bound (z_{0.95}).
_WILSON_Z_95: float = 1.6448536269514722

# The OOF reliability artifact path (gitignored generated file; INERT when absent).
_QLCB_OOF_RELIABILITY_PATH: str = "state/qlcb_oof_reliability.json"

# Module-level cache of the parsed artifact so the hot path reads it once per process.
# ``_RELIABILITY_CACHE`` is the parsed cell map; ``_RELIABILITY_LOADED`` guards the one-shot
# load (a missing file is cached as the empty map -> the guard stays inert without re-statting).
_RELIABILITY_CACHE: Optional[dict[str, tuple[int, float]]] = None
_RELIABILITY_LOADED: bool = False


# ---------------------------------------------------------------------------
# Cells + buckets.
# ---------------------------------------------------------------------------

def lead_bucket(lead_days: float) -> str:
    """The reliability lead bucket. Coarse so cells accrue enough OOF evidence.

    Mirrors the fusion's L1 / L2_3 / L4P grouping (a 24h decision, a 2-3 day lead, and a
    long lead behave differently for q_lcb honesty), so the guard's cell granularity matches
    the σ-floor lead granularity the rest of the spine uses.
    """
    if lead_days <= 1.0:
        return "L1"
    if lead_days <= 3.0:
        return "L2_3"
    return "L4P"


def qlcb_bucket(q_lcb: float) -> tuple[int, float]:
    """The (bucket_index, bucket_floor) the served q_lcb falls into.

    The floor is the bucket's lower edge — the minimum realized hit-rate the bucket claims to
    support. A q_lcb at or above the top edge lands in the last bucket; below 0 lands in the
    first. Returned as (index, floor) so the cell key and the floor comparison share one source.
    """
    q = float(q_lcb)
    edges = QLCB_BUCKET_EDGES
    for i in range(len(edges) - 1):
        lo = edges[i]
        hi = edges[i + 1]
        # Last bucket is closed on the right so q_lcb == 1.0 lands in it.
        if (lo <= q < hi) or (i == len(edges) - 2 and q >= hi):
            return i, lo
    return 0, edges[0]


def cell_key(*, metric: str, lead_days: float, bin_position: str, q_lcb: float) -> str:
    """The reliability CELL key ``g = (metric, lead_bucket, bin_position, q_lcb_bucket)``.

    NOT per-city — a per-city offset would be a fitted de-bias (forbidden). ``bin_position`` is
    the route's position class within the family (e.g. "modal" / "shoulder" / "tail" or the
    venue bin label position); the caller supplies whatever stable, non-per-city position label
    the OOF table was built with. The q_lcb bucket index keys the same buckets the table uses.
    """
    bucket_idx, _floor = qlcb_bucket(q_lcb)
    return f"{str(metric).lower()}|{lead_bucket(lead_days)}|{bin_position}|qb{bucket_idx}"


def wilson_lower_bound_95(hits: int, n: int) -> float:
    """One-sided Wilson 95% LOWER bound of a binomial hit-rate (hits / n).

    The Wilson score interval is well-behaved at the extremes (unlike the normal approximation
    which can go below 0), so a cell with a high but thin realized hit-rate gets a conservatively
    LOW bound — exactly the "don't trust a thin cell" posture the guard needs. Returns 0.0 for a
    degenerate (n <= 0) cell. ``hits`` is clamped into [0, n].
    """
    if n <= 0:
        return 0.0
    k = min(max(int(hits), 0), int(n))
    z = _WILSON_Z_95
    p_hat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    lo = center - margin
    if not math.isfinite(lo):
        return 0.0
    return float(min(max(lo, 0.0), 1.0))


# ---------------------------------------------------------------------------
# The OOF reliability table (artifact-gated; INERT when absent).
# ---------------------------------------------------------------------------

def _load_reliability_table() -> dict[str, tuple[int, float]]:
    """Load the OOF reliability table ``{cell_key: (n, hit_rate)}`` (one-shot, cached).

    The artifact maps each cell key to ``{"n": int, "hit_rate": float}`` (the OOF realized
    frequency the offline fitter wrote from settled predictions). Absent / malformed / empty ->
    the EMPTY map (the guard is then INERT: every cell is "unknown" -> pass-through, see
    ``apply_guard``). FAIL-SOFT: any error -> empty map (never raises into the decision).
    """
    global _RELIABILITY_CACHE, _RELIABILITY_LOADED
    if _RELIABILITY_LOADED and _RELIABILITY_CACHE is not None:
        return _RELIABILITY_CACHE
    out: dict[str, tuple[int, float]] = {}
    try:
        path = _QLCB_OOF_RELIABILITY_PATH
        if not os.path.isabs(path):
            repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.join(repo, _QLCB_OOF_RELIABILITY_PATH)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                artifact = json.load(fh)
            cells = artifact.get("cells") if isinstance(artifact, dict) else None
            if isinstance(cells, dict):
                for key, val in cells.items():
                    if not isinstance(val, dict):
                        continue
                    try:
                        n = int(val.get("n", 0))
                        hr = float(val.get("hit_rate"))
                    except (TypeError, ValueError):
                        continue
                    if n > 0 and math.isfinite(hr) and 0.0 <= hr <= 1.0:
                        out[str(key)] = (n, hr)
    except Exception:  # noqa: BLE001 — fail-soft to inert; never break the decision.
        out = {}
    _RELIABILITY_CACHE = out
    _RELIABILITY_LOADED = True
    return out


def reset_reliability_cache() -> None:
    """Reset the one-shot artifact cache (tests inject a table then reset between cases)."""
    global _RELIABILITY_CACHE, _RELIABILITY_LOADED
    _RELIABILITY_CACHE = None
    _RELIABILITY_LOADED = False


# ---------------------------------------------------------------------------
# The guard verdict + the serving rule.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GuardVerdict:
    """The q_lcb reliability guard's per-candidate verdict.

    * ``q_safe`` — the SERVED q_lcb: ``min(band_q_lcb, L_g)`` when the cell licenses a trade,
      else ``0.0`` (abstain). This is what the after-cost edge is computed on downstream.
    * ``trade`` — True iff ALL of (N_g >= N_MIN, L_g >= bucket_floor − EPS) hold. The edge-floor
      check is applied by the caller against ``q_safe`` (it needs the route price + cost).
    * ``abstained`` — True when the guard deflated q_safe to 0 (cell thin / below floor). The
      caller forces a non-positive edge so the candidate is rejected — never traded.
    * ``cell_key`` / ``L_g`` / ``n_g`` / ``bucket_floor`` — the guard provenance (step 7).
    * ``basis`` — "INERT" when the artifact was absent (pass-through) so the receipt records
      that the guard did not deflate; "OOF_WILSON_95" when an OOF cell was applied.
    """

    q_safe: float
    trade: bool
    abstained: bool
    cell_key: str
    L_g: float
    n_g: int
    bucket_floor: float
    basis: str


def apply_guard(
    *,
    band_q_lcb: float,
    metric: str,
    lead_days: float,
    bin_position: str,
    reliability_table: Optional[Mapping[str, tuple[int, float]]] = None,
) -> GuardVerdict:
    """Apply the q_lcb empirical reliability guard to ONE candidate's served q_lcb.

    ``band_q_lcb`` is the Path-A ``build_joint_q_band`` per-bin lower bound for this route
    (already the coherent quantile). The guard:

      1. Resolves the cell ``g = (metric, lead_bucket, bin_position, q_lcb_bucket)``.
      2. Reads the OOF cell ``(N_g, hit_rate_g)`` from the table (artifact or injected).
      3. INERT path — cell unknown (artifact absent / cell not in table): serves
         ``band_q_lcb`` unchanged, ``trade=True``, ``basis="INERT"`` (pass-through, no abstain;
         the conservative edge_lcb>0 gate downstream is still the trade authority).
      4. ACTIVE path — cell known: ``L_g = wilson_lower_bound_95(hits, N_g)`` where
         ``hits = round(hit_rate_g * N_g)``. Trade is licensed iff ``N_g >= N_MIN`` AND
         ``L_g >= bucket_floor − EPS``. ``q_safe = min(band_q_lcb, L_g)`` when licensed, else
         ``0.0`` (abstain). The after-cost ``q_safe − price − cost > EDGE_FLOOR`` check is the
         caller's (it has the route price + cost).

    The guard NEVER moves μ and NEVER fits a per-city offset; it only serves a lower bound the
    realized frequency supports (or abstains). FAIL-SOFT inside the table read (inert on error).
    """
    table = reliability_table if reliability_table is not None else _load_reliability_table()
    bucket_idx, bucket_floor = qlcb_bucket(band_q_lcb)
    key = f"{str(metric).lower()}|{lead_bucket(lead_days)}|{bin_position}|qb{bucket_idx}"

    cell = table.get(key)
    if cell is None:
        # INERT: no OOF evidence for this cell (artifact absent or cell unseen). Serve the
        # band q_lcb unchanged — byte-identical to pre-guard behavior; the conservative
        # edge_lcb>0 gate remains the trade authority. NOT an abstain (an unknown cell is not
        # evidence of miscalibration; the guard only DEFLATES where the OOF table says so).
        return GuardVerdict(
            q_safe=float(band_q_lcb),
            trade=True,
            abstained=False,
            cell_key=key,
            L_g=float("nan"),
            n_g=0,
            bucket_floor=bucket_floor,
            basis="INERT",
        )

    n_g, hit_rate_g = cell
    hits = int(round(float(hit_rate_g) * int(n_g)))
    L_g = wilson_lower_bound_95(hits, int(n_g))

    licensed = (int(n_g) >= N_MIN) and (L_g >= bucket_floor - EPS)
    if licensed:
        q_safe = min(float(band_q_lcb), float(L_g))
        return GuardVerdict(
            q_safe=q_safe,
            trade=True,
            abstained=False,
            cell_key=key,
            L_g=float(L_g),
            n_g=int(n_g),
            bucket_floor=bucket_floor,
            basis="OOF_WILSON_95",
        )
    # Thin cell or realized frequency below the bucket floor -> abstain. q_safe = 0 deflates the
    # edge so the candidate cannot trade (publish the point prob, do not trade this bin).
    return GuardVerdict(
        q_safe=0.0,
        trade=False,
        abstained=True,
        cell_key=key,
        L_g=float(L_g),
        n_g=int(n_g),
        bucket_floor=bucket_floor,
        basis="OOF_WILSON_95",
    )
