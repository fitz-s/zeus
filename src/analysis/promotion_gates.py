# Created: 2026-08-24
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 10 ("Two gates ... + single bounded Tier 1") + "Key consult corrections
#   adopted" (no 32-cell capital ladder; two gates + one bounded Tier 1; parity
#   with market NEVER unlocks Kelly; two-way city/date clustering, larger
#   uncertainty governs). Item 9's live verdict (q_cal parity-at-best) is the
#   first real input to Gate A — this module is the machinery that renders
#   that verdict formally, not a rubber stamp.
"""Two-gate capital-promotion evaluator + the single bounded Tier-1 formula.

GATE A (probability use) — prequential non-inferiority of the calibrated
r_hat vs the market price p0 on paired log-loss, cluster-robust (city-date AND
calendar-date, the LARGER uncertainty governs), plus a no-catastrophic-
degradation side condition per |q-p| bucket. Passing Gate A licenses r_hat for
RANKING/lifecycle inputs only (``GateAVerdict.GATE_A_PROBABILITY_USE`` is
deliberately not named anything sizing-shaped) — it is NOT a sizing license.

GATE B (capital use) — TWO components, both required: (1) the current-policy
cheap-taker selection residual (y - p_fill) has a positive one-sided 95% lower
confidence bound under the governing clustering; (2) the preregistered
ordinal-selection-lift test (docs/operations/current/plans/
tier0_selection_lift_preregistration_2026-08-24.md) has reached its positive-
LCB decision branch. Gate B never touches a DB or the selection-lift harness
itself — callers supply typed rows and a ``SelectionLiftDecision`` (mirrors
the "callers supply every fact" idiom in src/strategy/tier0_policy.py).

TIER-1 SIZING FORMULA — a pure function, exported for a FUTURE wiring step
this module does not perform: f = min(25bp, 1/4 * max(0, (r_L - p_fill) /
(1 - p_fill))). Nothing in this module is imported by the live entry path.

This module is DB-agnostic and math-only, matching
src/calibration/market_anchored_residual.py's shape: callers (the CLI script,
tests) extract plain rows and hand them in.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Shared constants (plan item 10, verbatim where the plan gives a number).
# ---------------------------------------------------------------------------

# Gate A non-inferiority margin: H0 is "r_hat is worse than p0 by >= this much
# mean paired log-loss"; Gate A's pooled component passes when the one-sided
# 95% upper confidence bound on mean(d) is strictly below this.
DELTA_A_NON_INFERIORITY_MARGIN: float = 0.01

# Gate A no-catastrophic-degradation side condition: within any single
# |q-p| bucket carrying enough clusters to test, the one-sided 95% upper
# bound on mean(d) must stay strictly below this — a much looser bound than
# the pooled margin, because a bucket is allowed to be modestly worse without
# failing the gate; it must never be CATASTROPHICALLY worse.
DELTA_A_CATASTROPHIC_DEGRADATION_MARGIN: float = 0.05

# A |q-p| bucket is only checked for catastrophic degradation once it has
# accrued at least this many city-date clusters — matching the plan's
# "small-n multiple testing machine" rejection: an under-powered bucket is
# reported as unchecked, never silently passed or silently failed.
MIN_CLUSTERS_FOR_BUCKET_CHECK: int = 30

# One-sided 95% z-critical value. Matches the repository's existing
# convention (scripts/fit_sigma_scale.py::_CAPITAL_LCB_Z = 1.645, "the SAME
# convention as the q_lcb 5% serving bound") rather than introducing a new
# distributional assumption for this module.
Z_95_ONE_SIDED: float = 1.645

# Log-loss probability clip, matching src/calibration/market_anchored_residual.py
# P_CLIP_LO/P_CLIP_HI and scripts/scoreboard_panels.py's _CLIP_LO/_CLIP_HI.
_CLIP_LO: float = 0.005
_CLIP_HI: float = 0.995

# Tier-1 sizing formula constants (plan "Key consult corrections adopted":
# "f = min(25bp, 1/4*max[0,(r_L-p_fill)/(1-p_fill)])"). Mirrored (not
# imported) in config/risk_policy.yaml's tier1.per_position_fraction_ceiling
# — the config ceiling governs the LIVE entry path once Tier-1 is wired;
# this constant is this module's own copy for the pure-function property
# tests and is not read from that file (this module has no DB/config access
# at all, per its module-agnostic design).
TIER1_PER_POSITION_FRACTION_CEILING: float = 0.0025  # 25bp
TIER1_CONSERVATIVE_MULTIPLIER: float = 0.25


def _clip(p: float) -> float:
    return min(max(p, _CLIP_LO), _CLIP_HI)


def _logloss(y: int, p: float) -> float:
    p_c = _clip(p)
    return -(y * math.log(p_c) + (1 - y) * math.log(1.0 - p_c))


def _cluster_key(city: str | None, target_date: str | None) -> str:
    """Mirrors scripts/scoreboard_panels.py::cluster_key exactly (city-date
    diversification unit). Reimplemented rather than imported: this module
    is DB-agnostic math and must not depend on a top-level script module
    (scripts/ depends on src/, never the reverse, elsewhere in this repo)."""
    return f"{city or 'UNKNOWN_CITY'}|{target_date or 'UNKNOWN_DATE'}"


def _clustered_se(diffs_by_cluster: Mapping[str, list[float]]) -> tuple[float | None, int]:
    """Cluster-mean SE: std(per-cluster mean diff) / sqrt(n_clusters).

    Mirrors scripts/scoreboard_panels.py::clustered_se. Returns
    (se, n_clusters); se is None when fewer than 2 clusters (sample stdev
    undefined) — never a divide-by-zero, never a fabricated 0.
    """
    means = [statistics.mean(v) for v in diffs_by_cluster.values() if v]
    n_clusters = len(means)
    if n_clusters < 2:
        return None, n_clusters
    se = statistics.stdev(means) / math.sqrt(n_clusters)
    return se, n_clusters


def _governing_se(se_a: float | None, se_b: float | None) -> float | None:
    """The plan's "use the larger uncertainty" law: two-way clustering, the
    LARGER of the two candidate SEs governs inference. None (undefined SE)
    from one side never silently wins over a defined SE from the other."""
    if se_a is None:
        return se_b
    if se_b is None:
        return se_a
    return max(se_a, se_b)


def _q_p_bucket(diff: float) -> str:
    """Mirrors scripts/scoreboard_panels.py::q_p_bucket exactly."""
    if diff < 0.15:
        return "<0.15"
    if diff < 0.30:
        return "0.15-0.30"
    if diff < 0.50:
        return "0.30-0.50"
    return ">0.50"


_Q_P_BUCKETS: tuple[str, ...] = ("<0.15", "0.15-0.30", "0.30-0.50", ">0.50")


# ---------------------------------------------------------------------------
# GATE A — probability use.
# ---------------------------------------------------------------------------


class GateAVerdict(str, Enum):
    # PASS. Deliberately named for probability/ranking use, never sizing —
    # a caller cannot accidentally treat this enum value as a Kelly license.
    GATE_A_PROBABILITY_USE = "GATE_A_PROBABILITY_USE"
    FAIL_INSUFFICIENT_DATA = "FAIL_INSUFFICIENT_DATA"
    FAIL_NON_INFERIORITY = "FAIL_NON_INFERIORITY"
    FAIL_CATASTROPHIC_DEGRADATION = "FAIL_CATASTROPHIC_DEGRADATION"


@dataclass(frozen=True)
class GateARow:
    """One walk-forward-predicted row: paired (p0, q_raw, r_hat, y) plus the
    two cluster identities Gate A's two-way clustering needs."""

    row_id: str
    p0: float
    q_raw: float
    r_hat: float
    y: int
    city: str | None
    target_date: str | None


@dataclass(frozen=True)
class GateABucketCheck:
    bucket: str
    n: int
    n_clusters_city_date: int
    mean_d: float | None
    se_gate: float | None
    upper_bound: float | None
    checked: bool  # True iff n_clusters_city_date >= MIN_CLUSTERS_FOR_BUCKET_CHECK
    breached: bool  # True iff checked and upper_bound is not None and >= catastrophic_margin


@dataclass(frozen=True)
class GateAResult:
    verdict: GateAVerdict
    n: int
    n_clusters_city_date: int
    n_clusters_date: int
    mean_d: float | None
    se_city_date: float | None
    se_date: float | None
    se_gate: float | None
    upper_bound_pooled: float | None
    non_inferiority_pass: bool
    bucket_checks: tuple[GateABucketCheck, ...]
    catastrophic_breach: bool
    delta_a: float
    catastrophic_margin: float
    min_clusters_for_bucket_check: int


def evaluate_gate_a(
    rows: Sequence[GateARow],
    *,
    delta_a: float = DELTA_A_NON_INFERIORITY_MARGIN,
    catastrophic_margin: float = DELTA_A_CATASTROPHIC_DEGRADATION_MARGIN,
    min_clusters_for_bucket_check: int = MIN_CLUSTERS_FOR_BUCKET_CHECK,
    z: float = Z_95_ONE_SIDED,
) -> GateAResult:
    """Non-inferiority test of r_hat vs p0 on paired log-loss, cluster-robust
    two ways (city-date, calendar-date; the larger governs), plus the
    per-bucket no-catastrophic-degradation side condition.

    d_i = logloss(y_i, r_hat_i) - logloss(y_i, p0_i). H0: mean(d) >= delta_a.
    PASS (reject H0) iff mean(d) + z*se_gate < delta_a AND no qualifying
    |q-p| bucket's own upper bound reaches catastrophic_margin.
    """
    n = len(rows)
    empty_bucket_checks = tuple(
        GateABucketCheck(
            bucket=b, n=0, n_clusters_city_date=0, mean_d=None, se_gate=None,
            upper_bound=None, checked=False, breached=False,
        )
        for b in _Q_P_BUCKETS
    )
    if n == 0:
        return GateAResult(
            verdict=GateAVerdict.FAIL_INSUFFICIENT_DATA,
            n=0, n_clusters_city_date=0, n_clusters_date=0,
            mean_d=None, se_city_date=None, se_date=None, se_gate=None,
            upper_bound_pooled=None, non_inferiority_pass=False,
            bucket_checks=empty_bucket_checks, catastrophic_breach=False,
            delta_a=delta_a, catastrophic_margin=catastrophic_margin,
            min_clusters_for_bucket_check=min_clusters_for_bucket_check,
        )

    diffs = [(_logloss(r.y, r.r_hat) - _logloss(r.y, r.p0), r) for r in rows]
    mean_d = statistics.mean(d for d, _ in diffs)

    by_cd: dict[str, list[float]] = {}
    by_date: dict[str, list[float]] = {}
    for d, r in diffs:
        by_cd.setdefault(_cluster_key(r.city, r.target_date), []).append(d)
        by_date.setdefault(r.target_date or "UNKNOWN_DATE", []).append(d)
    se_cd, n_cd = _clustered_se(by_cd)
    se_date, n_date = _clustered_se(by_date)
    se_gate = _governing_se(se_cd, se_date)

    if se_gate is None:
        return GateAResult(
            verdict=GateAVerdict.FAIL_INSUFFICIENT_DATA,
            n=n, n_clusters_city_date=n_cd, n_clusters_date=n_date,
            mean_d=mean_d, se_city_date=se_cd, se_date=se_date, se_gate=None,
            upper_bound_pooled=None, non_inferiority_pass=False,
            bucket_checks=empty_bucket_checks, catastrophic_breach=False,
            delta_a=delta_a, catastrophic_margin=catastrophic_margin,
            min_clusters_for_bucket_check=min_clusters_for_bucket_check,
        )

    upper_bound_pooled = mean_d + z * se_gate
    non_inferiority_pass = upper_bound_pooled < delta_a

    by_bucket: dict[str, list[tuple[float, GateARow]]] = {b: [] for b in _Q_P_BUCKETS}
    for d, r in diffs:
        by_bucket[_q_p_bucket(abs(r.q_raw - r.p0))].append((d, r))

    bucket_checks: list[GateABucketCheck] = []
    catastrophic_breach = False
    for bucket in _Q_P_BUCKETS:
        group = by_bucket[bucket]
        n_b = len(group)
        by_cd_b: dict[str, list[float]] = {}
        by_date_b: dict[str, list[float]] = {}
        for d, r in group:
            by_cd_b.setdefault(_cluster_key(r.city, r.target_date), []).append(d)
            by_date_b.setdefault(r.target_date or "UNKNOWN_DATE", []).append(d)
        se_cd_b, n_cd_b = _clustered_se(by_cd_b)
        se_date_b, _n_date_b = _clustered_se(by_date_b)
        se_gate_b = _governing_se(se_cd_b, se_date_b)
        mean_d_b = statistics.mean(d for d, _ in group) if group else None
        checked = n_cd_b >= min_clusters_for_bucket_check
        upper_b = (
            mean_d_b + z * se_gate_b
            if (checked and se_gate_b is not None and mean_d_b is not None)
            else None
        )
        breached = checked and upper_b is not None and upper_b >= catastrophic_margin
        if breached:
            catastrophic_breach = True
        bucket_checks.append(
            GateABucketCheck(
                bucket=bucket, n=n_b, n_clusters_city_date=n_cd_b,
                mean_d=mean_d_b, se_gate=se_gate_b, upper_bound=upper_b,
                checked=checked, breached=breached,
            )
        )

    if catastrophic_breach:
        verdict = GateAVerdict.FAIL_CATASTROPHIC_DEGRADATION
    elif not non_inferiority_pass:
        verdict = GateAVerdict.FAIL_NON_INFERIORITY
    else:
        verdict = GateAVerdict.GATE_A_PROBABILITY_USE

    return GateAResult(
        verdict=verdict,
        n=n, n_clusters_city_date=n_cd, n_clusters_date=n_date,
        mean_d=mean_d, se_city_date=se_cd, se_date=se_date, se_gate=se_gate,
        upper_bound_pooled=upper_bound_pooled,
        non_inferiority_pass=non_inferiority_pass,
        bucket_checks=tuple(bucket_checks), catastrophic_breach=catastrophic_breach,
        delta_a=delta_a, catastrophic_margin=catastrophic_margin,
        min_clusters_for_bucket_check=min_clusters_for_bucket_check,
    )


# ---------------------------------------------------------------------------
# GATE B — capital use.
# ---------------------------------------------------------------------------


class GateBVerdict(str, Enum):
    # PASS.
    GATE_B_CAPITAL_USE = "GATE_B_CAPITAL_USE"
    NO_SAMPLE = "NO_SAMPLE"
    FAIL_INSUFFICIENT_DATA = "FAIL_INSUFFICIENT_DATA"
    FAIL_FILL_RESIDUAL_LCB = "FAIL_FILL_RESIDUAL_LCB"
    FAIL_SELECTION_LIFT_NOT_REACHED = "FAIL_SELECTION_LIFT_NOT_REACHED"
    FAIL_BOTH_COMPONENTS = "FAIL_BOTH_COMPONENTS"


@dataclass(frozen=True)
class GateBRow:
    """One Tier-0-flagged settled position: actual fill price + settlement."""

    row_id: str
    p_fill: float
    y: int
    city: str | None
    target_date: str | None


@dataclass(frozen=True)
class SelectionLiftDecision:
    """The dependency contract this module expects from a selection-lift
    harness (src/analysis/selection_lift.py, built against
    docs/operations/current/plans/tier0_selection_lift_preregistration_2026-08-24.md
    by a sibling implementer as of this commit). That harness's full
    permutation-test result reduces to this one boolean for Gate B's purposes:
    ``reached_positive_lcb_branch=True`` corresponds exactly to the
    preregistration's decision-rule branch 1 ("mean(L) positive lower
    one-sided 95% CB -> ELIGIBLE for the Gate-B capital-use evaluation").
    Both the "keep accruing" and "selector retired" branches reduce to
    False here — Gate B treats "not yet eligible" and "retired" identically
    (neither licenses capital use), even though they differ upstream.
    """

    reached_positive_lcb_branch: bool
    n_qualifying_clusters: int
    detail: str = ""


@dataclass(frozen=True)
class GateBResult:
    verdict: GateBVerdict
    n: int
    n_clusters_city_date: int
    n_clusters_date: int
    mean_residual: float | None
    se_city_date: float | None
    se_date: float | None
    se_gate: float | None
    lower_bound: float | None
    fill_residual_pass: bool
    selection_lift_pass: bool
    selection_lift_detail: str


def evaluate_gate_b(
    rows: Sequence[GateBRow],
    *,
    selection_lift: SelectionLiftDecision | None,
    z: float = Z_95_ONE_SIDED,
) -> GateBResult:
    """Both components required for PASS:

    (1) one-sided 95% LOWER confidence bound of mean(y - p_fill) > 0, under
        the governing (larger) two-way clustering (city-date, calendar-date).
    (2) the preregistered selection-lift test has reached its positive-LCB
        decision branch (``selection_lift.reached_positive_lcb_branch``).
        ``selection_lift=None`` (the harness/module is unavailable) is
        treated identically to "not reached" — fail closed, never optimistic.
    """
    n = len(rows)
    if n == 0:
        return GateBResult(
            verdict=GateBVerdict.NO_SAMPLE,
            n=0, n_clusters_city_date=0, n_clusters_date=0,
            mean_residual=None, se_city_date=None, se_date=None, se_gate=None,
            lower_bound=None, fill_residual_pass=False, selection_lift_pass=False,
            selection_lift_detail="no rows",
        )

    diffs = [(r.y - r.p_fill, r) for r in rows]
    mean_residual = statistics.mean(d for d, _ in diffs)

    by_cd: dict[str, list[float]] = {}
    by_date: dict[str, list[float]] = {}
    for d, r in diffs:
        by_cd.setdefault(_cluster_key(r.city, r.target_date), []).append(d)
        by_date.setdefault(r.target_date or "UNKNOWN_DATE", []).append(d)
    se_cd, n_cd = _clustered_se(by_cd)
    se_date, n_date = _clustered_se(by_date)
    se_gate = _governing_se(se_cd, se_date)

    selection_lift_pass = selection_lift is not None and selection_lift.reached_positive_lcb_branch
    selection_lift_detail = (
        selection_lift.detail if selection_lift is not None else "selection_lift unavailable"
    )

    if se_gate is None:
        return GateBResult(
            verdict=GateBVerdict.FAIL_INSUFFICIENT_DATA,
            n=n, n_clusters_city_date=n_cd, n_clusters_date=n_date,
            mean_residual=mean_residual, se_city_date=se_cd, se_date=se_date, se_gate=None,
            lower_bound=None, fill_residual_pass=False, selection_lift_pass=selection_lift_pass,
            selection_lift_detail=selection_lift_detail,
        )

    lower_bound = mean_residual - z * se_gate
    fill_residual_pass = lower_bound > 0.0

    if fill_residual_pass and selection_lift_pass:
        verdict = GateBVerdict.GATE_B_CAPITAL_USE
    elif not fill_residual_pass and not selection_lift_pass:
        verdict = GateBVerdict.FAIL_BOTH_COMPONENTS
    elif not fill_residual_pass:
        verdict = GateBVerdict.FAIL_FILL_RESIDUAL_LCB
    else:
        verdict = GateBVerdict.FAIL_SELECTION_LIFT_NOT_REACHED

    return GateBResult(
        verdict=verdict,
        n=n, n_clusters_city_date=n_cd, n_clusters_date=n_date,
        mean_residual=mean_residual, se_city_date=se_cd, se_date=se_date, se_gate=se_gate,
        lower_bound=lower_bound, fill_residual_pass=fill_residual_pass,
        selection_lift_pass=selection_lift_pass, selection_lift_detail=selection_lift_detail,
    )


# ---------------------------------------------------------------------------
# TIER-1 SIZING FORMULA — pure function, exported for a future wiring step.
# Nothing in this module (or anywhere else as of this commit) calls this from
# the live entry path.
# ---------------------------------------------------------------------------


def tier1_sizing_fraction(
    *,
    r_l: float,
    p_fill: float,
    ceiling: float = TIER1_PER_POSITION_FRACTION_CEILING,
    conservative_multiplier: float = TIER1_CONSERVATIVE_MULTIPLIER,
) -> float:
    """f = min(ceiling, conservative_multiplier * max(0, (r_L - p_fill) / (1 - p_fill))).

    ``r_l`` is the predeclared conservative LOWER bound from calibrated
    prospective evidence — a caller-supplied parameter, never computed here
    (this module has no opinion on where r_l comes from; Gate A licenses
    r_hat for ranking, not as a numeric input to this formula without its own
    predeclared lower-bound construction upstream).

    Properties under test: parity (r_l == p_fill) -> f == 0; f never exceeds
    ``ceiling``; a negative edge (r_l < p_fill) -> f == 0; monotone
    non-decreasing in r_l for fixed p_fill.
    """
    try:
        r_l_f = float(r_l)
        p_f = float(p_fill)
        ceiling_f = float(ceiling)
        mult_f = float(conservative_multiplier)
    except (TypeError, ValueError):
        raise ValueError("tier1_sizing_fraction inputs must be numeric") from None
    if not all(math.isfinite(v) for v in (r_l_f, p_f, ceiling_f, mult_f)):
        raise ValueError("tier1_sizing_fraction inputs must be finite")
    if not (0.0 <= p_f < 1.0):
        raise ValueError(f"p_fill must be in [0, 1), got {p_fill!r}")
    if not (0.0 <= r_l_f <= 1.0):
        raise ValueError(f"r_l must be in [0, 1], got {r_l!r}")
    if ceiling_f <= 0.0:
        raise ValueError(f"ceiling must be positive, got {ceiling!r}")
    if mult_f <= 0.0:
        raise ValueError(f"conservative_multiplier must be positive, got {conservative_multiplier!r}")

    edge = (r_l_f - p_f) / (1.0 - p_f)
    return min(ceiling_f, mult_f * max(0.0, edge))


# ---------------------------------------------------------------------------
# ANTI-PEEKING LEDGER — refuses a second formal Gate-B evaluation per
# preregistration_version (the preregistration's alpha-spending stopping
# rule: docs/operations/current/plans/
# tier0_selection_lift_preregistration_2026-08-24.md, "Frozen analysis
# choices" #3 — a second formal evaluation without a documented amendment is
# forbidden; repeated dashboard viewing must go through --dry-run, which
# never calls record_gate_b_formal_evaluation).
#
# This module never chooses its own path — the caller (the CLI script)
# supplies ``path`` explicitly; tests always pass a tmp_path file so no test
# run can ever touch the live state/ ledger.
# ---------------------------------------------------------------------------

DEFAULT_LEDGER_PATH = Path("state/promotion_gates_ledger.json")


class SecondFormalEvaluationRefused(Exception):
    """A formal (non-dry-run) Gate-B evaluation was attempted for a
    preregistration_version already recorded in the ledger. The
    preregistration's alpha-spending law permits only ONE formal look per
    version; repeated viewing must use --dry-run instead."""


@dataclass(frozen=True)
class LedgerEntry:
    gate: str
    preregistration_version: str
    evaluated_at: str  # ISO8601 UTC
    sample_identity_hash: str
    verdict: str


def load_ledger(path: Path = DEFAULT_LEDGER_PATH) -> list[dict[str, Any]]:
    """Read the ledger file, or [] if absent. A corrupted file fails loud
    (never silently treated as empty — an empty ledger and a corrupted one
    must never be indistinguishable to the anti-peeking check)."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"PROMOTION_GATES_LEDGER_CORRUPTED: {path}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"PROMOTION_GATES_LEDGER_MALFORMED: {path} must be a JSON list")
    return raw


def _atomic_write_json(path: Path, payload: Any) -> None:
    """tempfile.mkstemp + fsync + os.replace, matching
    src/control/control_plane.py::_write_control_payload's atomic-write
    idiom."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".promotion_gates_ledger.", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass


def record_gate_b_formal_evaluation(
    *,
    preregistration_version: str,
    sample_identity_hash: str,
    verdict: str,
    path: Path = DEFAULT_LEDGER_PATH,
    now: datetime | None = None,
) -> LedgerEntry:
    """Record one formal Gate-B evaluation, refusing a second for the same
    preregistration_version. --dry-run callers must never call this."""
    entries = load_ledger(path)
    for entry in entries:
        if entry.get("gate") == "gate_b" and entry.get("preregistration_version") == preregistration_version:
            raise SecondFormalEvaluationRefused(
                f"gate_b already formally evaluated for preregistration_version="
                f"{preregistration_version!r} at {entry.get('evaluated_at')} "
                f"(sample_identity_hash={entry.get('sample_identity_hash')}); "
                "the preregistration's alpha-spending law forbids a second formal "
                "look without a documented amendment — use --dry-run for repeated "
                "viewing"
            )
    ts = (now or datetime.now(timezone.utc)).isoformat()
    new_entry = LedgerEntry(
        gate="gate_b",
        preregistration_version=preregistration_version,
        evaluated_at=ts,
        sample_identity_hash=sample_identity_hash,
        verdict=verdict,
    )
    entries.append(asdict(new_entry))
    _atomic_write_json(path, entries)
    return new_entry
