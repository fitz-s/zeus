# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: selection-aware settlement q_lcb calibrator
#   (frontier consult REQ-20260622-151741; live_order_pathology 2026-06-22).
#   Successor to src/decision/qlcb_reliability_guard.py (price-blind OOF guard): that guard keys on
#   the DERIVED q_lcb bucket and therefore cannot see the price-conditioned ADVERSE SELECTION that
#   loses money. This calibrator keys on the RAW side probability (the actual admission signal) and
#   on the realized SETTLEMENT hit-rate of prior settled rows, fit WALK-FORWARD (no leak). It is the
#   admission lower bound at the q_lcb seam in event_reactor_adapter, BEFORE edge_lcb>0 / BH-FDR /
#   Kelly. Same artifact-gated, FAIL-CLOSED posture as the σ-floor and the OOF guard — but stricter:
#   ABSENT is NOT inert here. A missing/malformed/stale/under-min-N artifact emits NO new entries
#   (q_safe=0, trade=False), NEVER a raw center-bootstrap q_lcb fallback.
"""Selection-aware settlement q_lcb calibrator — the runtime serving rule (no shadow, no de-bias).

THE CRUX (settlement-graded, 2026-06-22): the live book is net-negative because the admission gate
``q_lcb_side > price`` adversely-selects exactly the bins where the model most under-estimates the
bin (its over-confident tail). On the real 104-bet buy_no slice the system's YES-belief-in-bin =
0.126 but realized-in-bin = 0.327 (market priced 0.298) — a ~20pp over-claim on the bought NO side.
The center-uncertainty bootstrap q_lcb (replacement_forecast_materializer._build_fused_q_bounds, the
5th-percentile of N(μ*, center_σ) draws) does NOT cover post-selection settlement error, so it is
not actually conservative for trading.

THE FIX: serve, as the admission lower bound, an EMPIRICAL CONSERVATIVE lower bound on the realized
SETTLEMENT hit-rate of the candidate's SIDE, learned WALK-FORWARD on settled rows ONLY:

    q_safe[side, cell] = beta_lower_bound_95( realized_hits_g, N_g )

where the cell ``g = (side, lead_bucket, bin_class, raw_prob_bucket)``. ``side`` is the executable
claim (YES = the bin hits / NO = the bin does NOT hit). ``raw_prob_bucket`` is the bucket of the RAW
SIDE PROBABILITY (q_yes for YES, 1-q_yes for NO) — the signal that drives admission, so the cell
sees the over-confidence the q_lcb-keyed OOF guard cannot. The candidate may trade ONLY if:

    * ``N_g >= MIN_N``                                  (the cell has enough settled evidence)
    * the cell exists for THIS side/lead/bin_class/prob_bucket   (no silent authority)
    * (caller) ``q_safe - price - cost > EDGE_FLOOR``   (real after-cost edge on the calibrated bound)

otherwise the candidate gets a NO-TRADE verdict (``q_safe = 0``). The bound is a function of the RAW
side prob + the settled hit-rate ONLY — price NEVER enters as a probability target (it may enter the
caller's admission margin as cost context, never here). Raw q stays the single probability authority;
this module serves a LOWER BOUND the realized settlement frequency supports, or abstains.

ARTIFACT (versioned, FAIL-CLOSED — absent is NOT inert): the table is read from
``state/selection_calibrator.json`` (gitignored generated artifact, fit ONLY by
scripts/fit_selection_calibrator.py walk-forward over settled rows). Unlike the OOF guard, absence
is fail-closed: the live admission path emits NO new entries when the artifact is
missing/malformed/stale/under-min-N. The artifact carries the posterior version it was fit under;
a version mismatch is stale -> fail-closed. The artifact's ``max_settled_at`` records the latest
settlement timestamp in the training window (provenance + a runtime staleness check the caller may
apply). This module NEVER fits per-city offsets, NEVER moves μ, NEVER anchors to price, and NEVER
constructs a parallel q.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence


# ---------------------------------------------------------------------------
# Named constants.
# ---------------------------------------------------------------------------

# Minimum settled-sample count in a cell before it may license a calibrated bound. Below this the
# cell is thin -> fail-closed (the realized frequency is not yet trustworthy).
MIN_N: int = 30

# After-cost edge floor (probability units). Applied by the CALLER against q_safe (it has the route
# price + cost). 0.0 keeps the conservative edge_lcb>0 bar.
EDGE_FLOOR: float = 0.0

# Raw side-probability bucket edges (probability units). The cell key buckets the RAW SIDE PROB
# (q_yes for YES, 1-q_yes for NO) so the over-confident tail lands in its own cell. Uniform 0.05
# grid across [0, 1] — same granularity as the OOF guard's q_lcb buckets so cells accrue evidence.
RAW_PROB_BUCKET_EDGES: tuple[float, ...] = tuple(round(0.05 * i, 2) for i in range(21))

# z for a one-sided 95% lower bound.
_Z_95: float = 1.6448536269514722

# The artifact path (gitignored generated file; FAIL-CLOSED when absent).
_SELECTION_CALIBRATOR_PATH: str = "state/selection_calibrator.json"

# The posterior/proof version this calibrator is bound to. A served artifact whose _meta
# posterior_version differs is STALE -> fail-closed. (The live replacement path's q mode is
# BAYES_PRECISION_FUSION; the artifact is fit on those posteriors.)
DEFAULT_POSTERIOR_VERSION: str = "BAYES_PRECISION_FUSION"

# Module-level one-shot cache of the parsed artifact.
_ARTIFACT_CACHE: Optional[dict] = None
_ARTIFACT_LOADED: bool = False


def _artifact_path() -> str:
    path = _SELECTION_CALIBRATOR_PATH
    if os.path.isabs(path):
        return path
    filename = path
    if filename.startswith("state/"):
        filename = filename[len("state/"):]
    try:
        from src.config import state_path

        return str(state_path(filename))
    except Exception:  # noqa: BLE001
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(repo, path)


# ---------------------------------------------------------------------------
# Cells + buckets (pure; the fitter imports these so cell keys match the live serving).
# ---------------------------------------------------------------------------

def lead_bucket(lead_days: float) -> str:
    """The reliability lead bucket. Mirrors the spine's L1 / L2_3 / L4P grouping (and the OOF
    guard's), so the calibrator's cell granularity matches the σ-floor lead granularity.
    """
    if lead_days <= 1.0:
        return "L1"
    if lead_days <= 3.0:
        return "L2_3"
    return "L4P"


def raw_prob_bucket(raw_side_prob: float) -> tuple[int, float]:
    """The (bucket_index, bucket_mid) the RAW SIDE PROBABILITY falls into.

    The mid is the bucket centre (used by the fitter as the isotonic x-coordinate). A prob at or
    above the top edge lands in the last bucket; below 0 in the first.
    """
    p = float(raw_side_prob)
    edges = RAW_PROB_BUCKET_EDGES
    for i in range(len(edges) - 1):
        lo = edges[i]
        hi = edges[i + 1]
        if (lo <= p < hi) or (i == len(edges) - 2 and p >= hi):
            return i, round((lo + hi) / 2.0, 4)
    return 0, round((edges[0] + edges[1]) / 2.0, 4)


def cell_key(*, side: str, lead_days: float, bin_class: str, raw_side_prob: float) -> str:
    """The selection CELL key ``g = (side, lead_bucket, bin_class, raw_prob_bucket)``.

    NOT per-city (a per-city offset would be a fitted de-bias, forbidden). ``side`` is the
    executable claim ("YES" = the bin hits, "NO" = the bin does not). ``bin_class`` is the stable,
    non-per-city position label ("modal" for the forecast/modal bin, "nonmodal" otherwise; the
    caller may also pass an open-shoulder flag class). The bucket index keys the RAW SIDE PROB.
    """
    bucket_idx, _mid = raw_prob_bucket(raw_side_prob)
    clean_side = "NO" if str(side).upper() == "NO" else "YES"
    clean_class = str(bin_class).strip().lower() or "nonmodal"
    return f"{clean_side}|{lead_bucket(lead_days)}|{clean_class}|pb{bucket_idx}"


def beta_lower_bound_95(hits: int, n: int) -> float:
    """One-sided 95% LOWER bound of a binomial hit-rate (hits / n) via the Wilson score interval.

    The Wilson interval is well-behaved at the extremes (unlike the normal approximation which can
    go below 0), so a thin high-rate cell gets a conservatively LOW bound — exactly the "don't trust
    a thin cell" posture the calibrator needs. The lower bound never exceeds the point hits/n.
    Returns 0.0 for a degenerate (n <= 0) cell. ``hits`` is clamped into [0, n].

    Named ``beta_lower_bound_95`` for the conservative-lower-interval contract; the Wilson score
    interval is the closed-form, numerically-stable realization of that lower interval and is the
    SAME bound the OOF reliability guard uses (single math source, no second convention).
    """
    if n <= 0:
        return 0.0
    k = min(max(int(hits), 0), int(n))
    z = _Z_95
    p_hat = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    lo = center - margin
    if not math.isfinite(lo):
        return 0.0
    return float(min(max(lo, 0.0), p_hat))


def isotonic_nondecreasing(xs: Sequence[float], ys: Sequence[float]) -> list[float]:
    """Pool-adjacent-violators isotonic regression: the monotone NON-DECREASING fit of ``ys`` over
    ``xs`` (xs assumed sorted ascending; ties merged by order).

    A higher belief (raw prob) cannot map to a lower calibrated realized rate, so the per-bucket
    realized hit-rates are projected onto the monotone cone. Unweighted PAVA (each bucket counts
    once); the fitter applies it across the within-cell prob buckets to enforce monotonicity before
    persisting. Returns the fitted y-values aligned to ``xs``.
    """
    y = [float(v) for v in ys]
    n = len(y)
    if n == 0:
        return []
    # Pool-adjacent-violators (unweighted).
    vals = list(y)
    weights = [1.0] * n
    # Each block: (sum, weight, count). We merge left-to-right.
    blocks: list[list[float]] = []  # [value, weight]
    for v in vals:
        blocks.append([v, 1.0])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0] + 1e-12:
            v2, w2 = blocks.pop()
            v1, w1 = blocks.pop()
            merged_w = w1 + w2
            merged_v = (v1 * w1 + v2 * w2) / merged_w
            blocks.append([merged_v, merged_w])
    out: list[float] = []
    for value, weight in blocks:
        out.extend([value] * int(round(weight)))
    # Guard length (rounding of merged weights is exact for unit weights).
    if len(out) != n:
        out = (out + [out[-1]] * n)[:n] if out else [0.0] * n
    return out


# ---------------------------------------------------------------------------
# Artifact load (versioned; FAIL-CLOSED when absent).
# ---------------------------------------------------------------------------

def load_artifact() -> Optional[dict]:
    """Load the selection-calibrator artifact (one-shot, cached). Returns None when absent/unreadable
    so the serving rule fails closed (absence is NOT inert here, unlike the OOF guard).
    """
    global _ARTIFACT_CACHE, _ARTIFACT_LOADED
    if _ARTIFACT_LOADED:
        return _ARTIFACT_CACHE
    path = _artifact_path()
    out: Optional[dict] = None
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                parsed = json.load(fh)
            if isinstance(parsed, dict) and isinstance(parsed.get("cells"), dict):
                out = parsed
    except Exception:  # noqa: BLE001 — present-but-bad is fail-closed (None).
        out = None
    _ARTIFACT_CACHE = out
    _ARTIFACT_LOADED = True
    return out


def reset_artifact_cache() -> None:
    """Reset the one-shot cache (tests inject an artifact then reset between cases)."""
    global _ARTIFACT_CACHE, _ARTIFACT_LOADED
    _ARTIFACT_CACHE = None
    _ARTIFACT_LOADED = False


def artifact_status(*, expected_posterior_version: str = DEFAULT_POSTERIOR_VERSION) -> dict:
    """Read-only health for restart/preflight gates."""
    art = load_artifact()
    path = _artifact_path()
    if not isinstance(art, dict):
        return {"path": path, "status": "ABSENT_FAIL_CLOSED", "active": False, "cell_count": 0}
    meta = art.get("_meta") if isinstance(art.get("_meta"), dict) else {}
    cells = art.get("cells") if isinstance(art.get("cells"), dict) else {}
    version = str(meta.get("posterior_version", ""))
    status = "ACTIVE_VALID" if (cells and version == expected_posterior_version) else (
        "STALE_VERSION" if version != expected_posterior_version else "ACTIVE_INVALID"
    )
    return {
        "path": path,
        "status": status,
        "active": bool(cells),
        "cell_count": len(cells),
        "posterior_version": version,
        "max_settled_at": meta.get("max_settled_at"),
    }


# ---------------------------------------------------------------------------
# The verdict + the serving rule.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CalibratorVerdict:
    """The selection calibrator's per-candidate verdict.

    * ``q_safe`` — the SERVED admission lower bound (the calibrated realized-hit-rate lower bound),
      or ``0.0`` on any fail-closed / abstain path. This is what the after-cost edge is computed on.
    * ``trade`` — True iff a deep (N>=MIN_N) cell licensed a calibrated bound. The after-cost
      ``q_safe - price - cost > EDGE_FLOOR`` check is the caller's.
    * ``abstained`` — True whenever the verdict is no-trade (fail-closed: absent/malformed/stale/
      thin/missing-cell). The caller forces a non-positive edge so the candidate is never traded.
    * ``cell_key`` / ``L_g`` / ``n_g`` — guard provenance.
    * ``basis`` — the fail-closed reason or "SELECTION_BETA_95" on a licensed cell.
    """

    q_safe: float
    trade: bool
    abstained: bool
    cell_key: str
    L_g: float
    n_g: int
    basis: str


def _fail_closed(cell_key: str, basis: str) -> CalibratorVerdict:
    return CalibratorVerdict(
        q_safe=0.0, trade=False, abstained=True, cell_key=cell_key, L_g=0.0, n_g=0, basis=basis
    )


def apply_selection_calibrator(
    *,
    raw_side_prob: float,
    side: str,
    lead_days: float,
    bin_class: str,
    admission_margin: float | None = None,  # price/cost CONTEXT only — never a probability target.
    artifact: Optional[Mapping] = None,
    expected_posterior_version: str = DEFAULT_POSTERIOR_VERSION,
) -> CalibratorVerdict:
    """Apply the selection-aware settlement q_lcb calibrator to ONE candidate's side.

    ``raw_side_prob`` is the RAW point probability of THIS side (q_yes for YES, 1-q_yes for NO).
    ``admission_margin`` (carrying price/cost) is accepted for provenance ONLY and NEVER influences
    q_safe — price is context, never a probability target (operator law: raw q is the single
    probability authority).

    Fail-closed everywhere absence/staleness/thinness is detected: the live admission path emits
    NO new entries (q_safe=0, trade=False) rather than falling back to the raw center-bootstrap
    q_lcb. On a deep known cell it serves the conservative beta/Wilson 95% lower bound of the cell's
    realized settlement hit-rate as the admission lower bound.
    """
    art = artifact if artifact is not None else load_artifact()
    key = cell_key(side=side, lead_days=lead_days, bin_class=bin_class, raw_side_prob=raw_side_prob)

    # FAIL-CLOSED: no artifact at all.
    if not isinstance(art, Mapping):
        return _fail_closed(key, "FAIL_CLOSED_NO_ARTIFACT")
    cells = art.get("cells")
    meta = art.get("_meta") if isinstance(art.get("_meta"), Mapping) else {}
    if not isinstance(cells, Mapping) or not cells:
        return _fail_closed(key, "FAIL_CLOSED_MALFORMED")

    # FAIL-CLOSED: stale posterior version (the artifact was fit under a different posterior).
    art_version = str(meta.get("posterior_version", "")) if isinstance(meta, Mapping) else ""
    if art_version and expected_posterior_version and art_version != expected_posterior_version:
        return _fail_closed(key, "FAIL_CLOSED_STALE_VERSION")

    min_n = int(meta.get("min_n", MIN_N)) if isinstance(meta, Mapping) else MIN_N

    cell = cells.get(key)
    if not isinstance(cell, Mapping):
        # Active artifact, absent side-aware cell: it did not grade this claim -> abstain.
        return CalibratorVerdict(
            q_safe=0.0, trade=False, abstained=True, cell_key=key, L_g=0.0, n_g=0,
            basis="ACTIVE_MISSING_CELL",
        )
    try:
        n_g = int(cell.get("n", 0))
        hit_rate = float(cell.get("hit_rate"))
    except (TypeError, ValueError):
        return _fail_closed(key, "FAIL_CLOSED_MALFORMED")
    if not (math.isfinite(hit_rate) and 0.0 <= hit_rate <= 1.0 and n_g > 0):
        return _fail_closed(key, "FAIL_CLOSED_MALFORMED")

    # THIN cell -> fail-closed (never serve a thin-cell rate).
    if n_g < min_n:
        return CalibratorVerdict(
            q_safe=0.0, trade=False, abstained=True, cell_key=key,
            L_g=0.0, n_g=n_g, basis="ACTIVE_THIN_CELL",
        )

    # Deep cell: serve the conservative lower bound of the realized settlement hit-rate.
    hits = int(round(hit_rate * n_g))
    L_g = beta_lower_bound_95(hits, n_g)
    # The lower bound never exceeds the raw side point (a probability lower bound <= its point).
    q_safe = float(min(max(L_g, 0.0), max(min(raw_side_prob, 1.0), 0.0)))
    return CalibratorVerdict(
        q_safe=q_safe, trade=True, abstained=False, cell_key=key,
        L_g=float(L_g), n_g=n_g, basis="SELECTION_BETA_95",
    )


# ---------------------------------------------------------------------------
# Seam integration helper (flag-gated; DEFAULT OFF — does NOT change live behavior).
# ---------------------------------------------------------------------------
#
# The live admission seam in src/engine/event_reactor_adapter.py builds, per candidate side, a robust
# lower bound (yes_lcb / no_lcb from the center-bootstrap bundle, or q_lcb_yes / q_lcb_no from the
# canonical bootstrap) and THEN tests admission as ``lcb_side > cost`` (edge_lcb_positive) and feeds
# ``p_values`` to BH-FDR. To make THIS calibrator the admission lower bound, the orchestrator wires
# ``selection_calibrated_side_lcb`` between the lcb construction and the edge/FDR computation:
#
#     # after yes_lcb / no_lcb (or q_lcb_yes / q_lcb_no) are computed:
#     yes_lcb = selection_calibrated_side_lcb(
#         raw_side_prob=q_yes, prior_lcb=yes_lcb, side="YES", lead_days=<lead>,
#         bin_class=("modal" if bin_id == forecast_bin else "nonmodal"),
#     )
#     no_lcb = selection_calibrated_side_lcb(
#         raw_side_prob=1.0 - q_yes, prior_lcb=no_lcb, side="NO", lead_days=<lead>, bin_class=<...>,
#     )
#
# It returns the calibrated lower bound when the calibrator LICENSES the cell, 0.0 (no-trade) when it
# fails closed, and — by DEFAULT (flag OFF) — the unchanged ``prior_lcb`` so wiring it in is a no-op
# until the orchestrator flips the flag AFTER forward-validation promotes the artifact. This keeps
# the build inert in the live tree (no deploy, no entry-pause change) while shipping the exact seam.

# Live activation flag. DEFAULT OFF -> the seam helper returns prior_lcb unchanged (no live change).
# The orchestrator flips this (settings / env) ONLY after forward-validation promotes the artifact.
_SELECTION_CALIBRATOR_LIVE_ENV: str = "ZEUS_SELECTION_CALIBRATOR_LIVE"


def selection_calibrator_live_enabled() -> bool:
    """Whether the calibrator is the LIVE admission lower bound. DEFAULT OFF (inert seam)."""
    return os.environ.get(_SELECTION_CALIBRATOR_LIVE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def selection_calibrated_side_lcb(
    *,
    raw_side_prob: float,
    prior_lcb: float,
    side: str,
    lead_days: float,
    bin_class: str,
    admission_margin: float | None = None,
    artifact: Optional[Mapping] = None,
    expected_posterior_version: str = DEFAULT_POSTERIOR_VERSION,
) -> float:
    """The seam value: the calibrated admission lower bound for ONE side, or a fail-closed 0.0.

    DEFAULT OFF (``selection_calibrator_live_enabled()`` False) -> returns ``prior_lcb`` UNCHANGED so
    wiring this into the adapter is a no-op until the orchestrator promotes the artifact and flips the
    flag. When LIVE: returns ``min(prior_lcb, q_safe)`` on a licensed cell (the calibrator can only
    LOWER the served bound, never raise it — it is a guard, not a new probability authority), and 0.0
    when the calibrator fails closed (absent/malformed/stale/thin/missing-cell) so the downstream
    ``edge_lcb = lcb - cost`` is non-positive and the candidate is NOT admitted. NEVER falls back to
    the raw center-bootstrap ``prior_lcb`` on a fail-closed verdict.
    """
    if not selection_calibrator_live_enabled():
        return float(prior_lcb)
    verdict = apply_selection_calibrator(
        raw_side_prob=raw_side_prob, side=side, lead_days=lead_days, bin_class=bin_class,
        admission_margin=admission_margin, artifact=artifact,
        expected_posterior_version=expected_posterior_version,
    )
    if not verdict.trade:
        return 0.0  # fail-closed -> non-positive edge downstream -> not admitted.
    # Guard semantics: only ever LOWER the served bound (min), never raise it.
    return float(min(float(prior_lcb), float(verdict.q_safe)))
