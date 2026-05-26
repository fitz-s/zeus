# Created: 2026-05-25
# Last reused or audited: 2026-05-25
# Lifecycle: created=2026-05-25; last_reviewed=2026-05-25; last_reused=never
# Purpose: Matched-date proper-score audit comparing raw(none) vs full_transport_v1 on head-to-head events.
# Reuse: Requires stage_db with both raw and ft posteriors; confirm matched event count >= MIN_MATCHED_FLOOR.
# Authority basis: Zeus #64 eval tool — matched-date §4.1 re-evaluation.
#   Kills the 0%-overlap confound that invalidated §4.1 of
#   ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md (raw and ft were scored on
#   disjoint (city, target_date) sets in the original audit_refit_proper_scores.py).
#   Scoring functions and DB access patterns reused directly from that script.
"""Matched-date proper-score audit — raw(none) vs full_transport_v1 on head-to-head events.

Before scoring, INTERSECT the raw-domain and ft-domain distributions on
(city, target_date, lead_days) so every comparison is head-to-head on the
SAME forecast events.  Per-cohort: n_matched, Brier, LogLoss, RPS, P(actual),
PIT histogram, ECE — with cohorts having n_matched < MIN_MATCHED_FLOOR
reported as INSUFFICIENT.

CONSTRAINTS: Read-only on DB. No writes.

USAGE:
  python scripts/audit_matched_date_proper_scores.py
  python scripts/audit_matched_date_proper_scores.py --db /path/to.db --metric high
  python scripts/audit_matched_date_proper_scores.py --db /path/to.db --metric high --cohort city:London
  python scripts/audit_matched_date_proper_scores.py --db /path/to.db --metric low --out /tmp/report.md

Output: markdown report at --out (default: docs/operations/ENS_REFIT_MATCHED_DATE_<metric>_results.md)
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_COASTAL = {
    "San Francisco", "Los Angeles", "Seattle", "Miami", "NYC", "London",
    "Tokyo", "Hong Kong", "Sydney", "Lisbon", "Barcelona", "Mumbai",
}

# Lead-day buckets: day 0, day 1, days 2-3, days 4-5, days 6-7
_LEAD_BUCKETS = [(0, 0), (1, 1), (2, 3), (4, 5), (6, 7)]

ECE_BINS = 10
PIT_BINS = 10

# Minimum matched events per cohort to report; below this → INSUFFICIENT
MIN_MATCHED_FLOOR = 30


# ---------------------------------------------------------------------------
# Bin label utilities (reused from audit_refit_proper_scores.py)
# ---------------------------------------------------------------------------

def _parse_bin_lower(label: str) -> tuple[float, str]:
    """Return (lower_bound_numeric, unit) for ordinal bin sorting.

    Shoulder bins:  '... or below' → -inf,  '... or above' → +inf.
    Range bins:     '49-50°F'       → 49.0, '°F'
                    '-23--22°F'     → -23.0, '°F'
    Single bins:    '-10°C'         → -10.0, '°C'
    """
    label = label.strip()
    unit = "°F" if "°F" in label else "°C"
    if "or below" in label:
        return (-float("inf"), unit)
    if "or above" in label:
        return (float("inf"), unit)
    stripped = label.replace("°F", "").replace("°C", "").strip()
    parts = re.split(r"(?<=[0-9])-(?=-?[0-9])", stripped, maxsplit=1)
    if len(parts) == 2:
        try:
            return (float(parts[0]), unit)
        except ValueError:
            pass
    try:
        return (float(stripped), unit)
    except ValueError:
        return (0.0, unit)


# ---------------------------------------------------------------------------
# Distribution reconstruction (reused from audit_refit_proper_scores.py)
# ---------------------------------------------------------------------------

class BinRow(NamedTuple):
    range_label: str
    p_raw: float
    outcome: int
    lead_days: float
    city: str
    cluster: str
    cycle: str
    season: str
    decision_group_id: str
    temperature_metric: str
    target_date: str


def _load_rows(conn: sqlite3.Connection, family: str, metric: str) -> list[BinRow]:
    """Load all canonical_v2 bin rows for a given family+metric."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(calibration_pairs_v2)")}
    emf_clause = "AND error_model_family = ?" if "error_model_family" in cols else ""
    params: list = ["canonical_v2", metric]
    if emf_clause:
        params.append(family)
    raw = conn.execute(
        f"""
        SELECT city, range_label, p_raw, outcome, lead_days,
               cluster, cycle, season, decision_group_id, temperature_metric,
               target_date
        FROM calibration_pairs_v2
        WHERE bin_source = ? AND temperature_metric = ?
          AND p_raw IS NOT NULL AND decision_group_id IS NOT NULL
          AND decision_group_id != '' {emf_clause}
        """,
        params,
    ).fetchall()
    return [
        BinRow(
            range_label=r["range_label"],
            p_raw=r["p_raw"],
            outcome=r["outcome"],
            lead_days=r["lead_days"],
            city=r["city"],
            cluster=r["cluster"],
            cycle=r["cycle"],
            season=r["season"],
            decision_group_id=r["decision_group_id"],
            temperature_metric=r["temperature_metric"],
            target_date=r["target_date"],
        )
        for r in raw
    ]


def _group_into_distributions(rows: list[BinRow]) -> list[dict]:
    """Aggregate bin rows into per-distribution dicts.

    Each distribution represents one forecast event:
      decision_group_id → sorted list of bins.

    Returns list of dicts with keys:
      decision_group_id, city, cluster, cycle, season, lead_days,
      temperature_metric, target_date, event_key (tuple),
      p_raw_vec (np.array), outcome_idx (int),
      range_labels (list), unit (str), n_bins (int)

    Excludes distributions where p_raw doesn't sum to ~1 or outcome count != 1.
    """
    by_group: dict[str, list[BinRow]] = defaultdict(list)
    for r in rows:
        by_group[r.decision_group_id].append(r)

    distributions = []
    n_skipped = 0
    for gid, bins in by_group.items():
        p_sum = sum(b.p_raw for b in bins)
        n_outcome = sum(b.outcome for b in bins)
        if abs(p_sum - 1.0) > 1e-3 or n_outcome != 1 or len(bins) < 80:
            n_skipped += 1
            continue
        sorted_bins = sorted(bins, key=lambda b: _parse_bin_lower(b.range_label)[0])
        p_vec = np.array([b.p_raw for b in sorted_bins], dtype=float)
        outcome_idx = next(i for i, b in enumerate(sorted_bins) if b.outcome == 1)
        unit = _parse_bin_lower(sorted_bins[0].range_label)[1]
        rep = sorted_bins[0]
        distributions.append({
            "decision_group_id": gid,
            "city": rep.city,
            "cluster": rep.cluster,
            "cycle": rep.cycle,
            "season": rep.season,
            "lead_days": rep.lead_days,
            "temperature_metric": rep.temperature_metric,
            "target_date": rep.target_date,
            # Intersection key: same event in both model arms
            "event_key": (rep.city, rep.target_date, rep.lead_days),
            "p_raw_vec": p_vec,
            "outcome_idx": outcome_idx,
            "range_labels": [b.range_label for b in sorted_bins],
            "unit": unit,
            "n_bins": len(sorted_bins),
        })

    if n_skipped:
        print(f"  [WARN] Skipped {n_skipped} malformed distributions.", file=sys.stderr)
    return distributions


# ---------------------------------------------------------------------------
# Matched-date intersection (the core contribution of this tool)
# ---------------------------------------------------------------------------

def _intersect_distributions(
    raw_dists: list[dict],
    ft_dists: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Return (matched_raw, matched_ft) where each pair shares the same event_key.

    event_key = (city, target_date, lead_days).

    For each key that appears in BOTH domains, exactly one distribution from each
    side is retained (the first encountered if duplicates exist — shouldn't happen
    given 1:1 mapping confirmed in the data).  Keys present in only one domain
    are dropped silently.  The returned lists are aligned: matched_raw[i] and
    matched_ft[i] correspond to the same forecast event.
    """
    raw_by_key: dict[tuple, dict] = {}
    for d in raw_dists:
        k = d["event_key"]
        if k not in raw_by_key:
            raw_by_key[k] = d

    ft_by_key: dict[tuple, dict] = {}
    for d in ft_dists:
        k = d["event_key"]
        if k not in ft_by_key:
            ft_by_key[k] = d

    shared_keys = sorted(raw_by_key.keys() & ft_by_key.keys())
    matched_raw = [raw_by_key[k] for k in shared_keys]
    matched_ft = [ft_by_key[k] for k in shared_keys]
    return matched_raw, matched_ft


# ---------------------------------------------------------------------------
# Per-distribution proper scores (reused from audit_refit_proper_scores.py)
# ---------------------------------------------------------------------------

def _brier_dist(p_vec: np.ndarray, outcome_idx: int) -> float:
    y = np.zeros(len(p_vec))
    y[outcome_idx] = 1.0
    return float(np.sum((p_vec - y) ** 2))


def _logloss_dist(p_vec: np.ndarray, outcome_idx: int, eps: float = 1e-12) -> float:
    return float(-np.log(np.clip(p_vec[outcome_idx], eps, 1.0)))


def _rps_dist(p_vec: np.ndarray, outcome_idx: int) -> float:
    F = np.cumsum(p_vec)
    K = len(p_vec)
    indicator = np.zeros(K)
    indicator[outcome_idx:] = 1.0
    return float(np.sum((F - indicator) ** 2))


def _pit_u(p_vec: np.ndarray, outcome_idx: int) -> float:
    F = np.cumsum(p_vec)
    return float(F[outcome_idx])


def _p_actual(p_vec: np.ndarray, outcome_idx: int) -> float:
    return float(p_vec[outcome_idx])


# ---------------------------------------------------------------------------
# Aggregate metrics (reused from audit_refit_proper_scores.py)
# ---------------------------------------------------------------------------

def _ece_from_dists(dists: list[dict], n_bins: int = ECE_BINS) -> float:
    """ECE on per-bin (p, y) pairs flattened across all distributions."""
    all_p: list[float] = []
    all_y: list[float] = []
    for d in dists:
        p_vec = d["p_raw_vec"]
        y = d["outcome_idx"]
        y_vec = np.zeros(len(p_vec))
        y_vec[y] = 1.0
        all_p.extend(p_vec.tolist())
        all_y.extend(y_vec.tolist())
    if not all_p:
        return float("nan")
    p_arr = np.clip(np.array(all_p, dtype=float), 0.0, 1.0)
    y_arr = np.array(all_y, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    e = 0.0
    n = len(p_arr)
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p_arr >= lo) & (p_arr < hi) if i < n_bins - 1 else (p_arr >= lo) & (p_arr <= hi)
        if not m.any():
            continue
        e += (m.sum() / n) * abs(p_arr[m].mean() - y_arr[m].mean())
    return float(e)


def _aggregate_metrics(dists: list[dict]) -> dict:
    """Compute aggregate proper scores for a list of matched distributions."""
    if not dists:
        return {"n": 0}
    brier_vals, logloss_vals, rps_vals, pactual_vals, pit_vals = [], [], [], [], []
    for d in dists:
        p = d["p_raw_vec"]
        y = d["outcome_idx"]
        brier_vals.append(_brier_dist(p, y))
        logloss_vals.append(_logloss_dist(p, y))
        rps_vals.append(_rps_dist(p, y))
        pactual_vals.append(_p_actual(p, y))
        pit_vals.append(_pit_u(p, y))
    pit_hist, _ = np.histogram(pit_vals, bins=PIT_BINS, range=(0.0, 1.0))
    return {
        "n": len(dists),
        "brier": float(np.mean(brier_vals)),
        "logloss": float(np.mean(logloss_vals)),
        "rps": float(np.mean(rps_vals)),
        "p_actual": float(np.mean(pactual_vals)),
        "ece": _ece_from_dists(dists),
        "pit_hist": pit_hist.tolist(),
    }


# ---------------------------------------------------------------------------
# Cohort filters (reused from audit_refit_proper_scores.py)
# ---------------------------------------------------------------------------

def _cohort_filter(dists: list[dict], cohort: str, value: str) -> list[dict]:
    if cohort == "global":
        return dists
    if cohort == "coastal":
        return [d for d in dists if d["city"] in _COASTAL]
    if cohort == "inland":
        return [d for d in dists if d["city"] not in _COASTAL]
    if cohort == "unit":
        return [d for d in dists if d["unit"] == value]
    if cohort == "city":
        return [d for d in dists if d["city"] == value]
    if cohort == "cluster":
        return [d for d in dists if d["cluster"] == value]
    if cohort == "lead_bucket":
        lo, hi = [int(x) for x in value.split("-")]
        return [d for d in dists if lo <= round(d["lead_days"]) <= hi]
    if cohort == "cycle":
        return [d for d in dists if d["cycle"] == value]
    return dists


# ---------------------------------------------------------------------------
# ASCII PIT histogram (reused from audit_refit_proper_scores.py)
# ---------------------------------------------------------------------------

def _pit_ascii(hist: list[int], label: str) -> str:
    if not hist or sum(hist) == 0:
        return f"  PIT [{label}]: no data\n"
    total = sum(hist)
    expected = total / len(hist)
    lines = [f"  PIT [{label}] (n={total}, expected_per_bin≈{expected:.0f}):"]
    edges = [f"{i/10:.1f}-{(i+1)/10:.1f}" for i in range(len(hist))]
    max_count = max(hist)
    bar_width = 30
    for edge, count in zip(edges, hist):
        bar_len = int(bar_width * count / max_count) if max_count > 0 else 0
        bar = "█" * bar_len
        pct = 100 * count / total
        lines.append(f"  {edge}: {bar:<{bar_width}} {count:5d} ({pct:.1f}%)")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

_HEADER = (
    "| Cohort | n_matched | Brier(raw) | LogLoss(raw) | RPS(raw) | P(actual)(raw) | ECE(raw) "
    "| Brier(ft) | LogLoss(ft) | RPS(ft) | P(actual)(ft) | ECE(ft) | ΔBrier | ΔLogLoss | ΔRPS |"
)
_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def _fmt(v: object, fmt: str = ".4f") -> str:
    if v is None or (isinstance(v, float) and v != v):
        return "n/a"
    return format(v, fmt)  # type: ignore[arg-type]


def _fmt_delta(raw: object, ft: object) -> str:
    """Format delta (ft - raw); negative = ft better for Brier/LogLoss/RPS."""
    if raw is None or ft is None:
        return "n/a"
    if isinstance(raw, float) and raw != raw:
        return "n/a"
    if isinstance(ft, float) and ft != ft:
        return "n/a"
    delta = float(ft) - float(raw)  # type: ignore[arg-type]
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.4f}"


def _render_row(
    label: str,
    n_matched: int,
    raw_m: dict,
    ft_m: dict,
) -> str:
    if n_matched < MIN_MATCHED_FLOOR:
        return (
            f"| {label} | **{n_matched}** (INSUFFICIENT — floor={MIN_MATCHED_FLOOR}) "
            + "| — " * 12
            + "|"
        )
    return (
        f"| {label} | {n_matched} "
        f"| {_fmt(raw_m.get('brier'))} | {_fmt(raw_m.get('logloss'))} "
        f"| {_fmt(raw_m.get('rps'))} | {_fmt(raw_m.get('p_actual'))} "
        f"| {_fmt(raw_m.get('ece'))} "
        f"| {_fmt(ft_m.get('brier'))} | {_fmt(ft_m.get('logloss'))} "
        f"| {_fmt(ft_m.get('rps'))} | {_fmt(ft_m.get('p_actual'))} "
        f"| {_fmt(ft_m.get('ece'))} "
        f"| {_fmt_delta(raw_m.get('brier'), ft_m.get('brier'))} "
        f"| {_fmt_delta(raw_m.get('logloss'), ft_m.get('logloss'))} "
        f"| {_fmt_delta(raw_m.get('rps'), ft_m.get('rps'))} |"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Matched-date proper-score audit: raw vs full_transport_v1 "
                    "on head-to-head (city, target_date, lead_days) events."
    )
    ap.add_argument(
        "--db",
        default="/Users/leofitz/.openclaw/workspace-venus/zeus/state/backups/"
                "ens_refit_full_2026-05-25.db",
        help="Path to read-only staging DB (default: backup ens_refit_full_2026-05-25.db)",
    )
    ap.add_argument(
        "--metric",
        default="high",
        choices=("high", "low"),
        help="temperature_metric filter (default: high)",
    )
    ap.add_argument(
        "--cohort",
        default=None,
        help="Optional cohort filter: e.g. 'city:London', 'cluster:coastal', 'cycle:00'. "
             "If omitted, all standard cohorts are reported.",
    )
    ap.add_argument(
        "--out",
        default=None,
        help="Output markdown path (default: docs/operations/ENS_REFIT_MATCHED_DATE_<metric>_results.md)",
    )
    args = ap.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    out_path = (
        Path(args.out).resolve()
        if args.out
        else PROJECT_ROOT / "docs" / "operations"
        / f"ENS_REFIT_MATCHED_DATE_{args.metric.upper()}_results.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print(f"Loading data from {db_path} (metric={args.metric})...", file=sys.stderr)
    raw_rows = _load_rows(conn, "none", args.metric)
    ft_rows = _load_rows(conn, "full_transport_v1", args.metric)
    conn.close()

    print(f"  raw(none) rows:       {len(raw_rows):,}", file=sys.stderr)
    print(f"  full_transport_v1 rows: {len(ft_rows):,}", file=sys.stderr)

    print("Grouping into distributions...", file=sys.stderr)
    raw_dists = _group_into_distributions(raw_rows)
    ft_dists = _group_into_distributions(ft_rows)
    print(f"  raw(none) distributions:       {len(raw_dists):,}", file=sys.stderr)
    print(f"  full_transport_v1 distributions: {len(ft_dists):,}", file=sys.stderr)

    print("Intersecting on (city, target_date, lead_days)...", file=sys.stderr)
    matched_raw_global, matched_ft_global = _intersect_distributions(raw_dists, ft_dists)
    print(f"  matched pairs (global): {len(matched_raw_global):,}", file=sys.stderr)

    # Diagnostic: (city, target_date)-only match count — helps distinguish post-Fix-A
    # lead_days granularity mismatch (none=integer, full_transport_v1=finer) from
    # true domain gap.  If city+date > 0 but strict == 0, the intersection key
    # needs loosening (or lead_days alignment in Fix-A output).
    raw_date_keys = {(d["city"], d.get("target_date", "")) for d in raw_dists}
    ft_date_keys = {(d["city"], d.get("target_date", "")) for d in ft_dists}
    n_date_only = len(raw_date_keys & ft_date_keys)
    print(
        f"  (city, target_date)-only match (ignoring lead_days): {n_date_only:,}",
        file=sys.stderr,
    )
    if len(matched_raw_global) == 0 and n_date_only > 0:
        print(
            "  NOTE: city+date pairs overlap but strict (city,target_date,lead_days) is 0.",
            file=sys.stderr,
        )
        print(
            "  Post-Fix-A: if this persists, check lead_days granularity alignment",
            file=sys.stderr,
        )
        print(
            "  (none family uses integer lead_days; full_transport_v1 may use finer values).",
            file=sys.stderr,
        )

    # -----------------------------------------------------------------------
    # Build cohort specs
    # -----------------------------------------------------------------------

    def _cohort_pair(cohort_key: str, value: str) -> tuple[list[dict], list[dict]]:
        """Filter BOTH matched arms by cohort — intersection preserved by alignment."""
        # matched_raw/ft are aligned; filter by index to keep pairs in sync
        indices = [
            i for i, d in enumerate(matched_raw_global)
            if _cohort_filter([d], cohort_key, value)
        ]
        return (
            [matched_raw_global[i] for i in indices],
            [matched_ft_global[i] for i in indices],
        )

    if args.cohort:
        # Single cohort mode
        if ":" in args.cohort:
            ck, cv = args.cohort.split(":", 1)
        else:
            ck, cv = args.cohort, ""
        cohort_specs: list[tuple[str, list[dict], list[dict]]] = [
            (args.cohort, *_cohort_pair(ck, cv))
        ]
    else:
        # All standard cohorts
        cohort_specs = [("global", matched_raw_global, matched_ft_global)]
        cohort_specs.append(("coastal", *_cohort_pair("coastal", "")))
        cohort_specs.append(("inland", *_cohort_pair("inland", "")))
        cohort_specs.append(("unit=°F", *_cohort_pair("unit", "°F")))
        cohort_specs.append(("unit=°C", *_cohort_pair("unit", "°C")))

        # Per-cluster (from matched ft side)
        all_clusters = sorted({d["cluster"] for d in matched_ft_global})
        for cl in all_clusters:
            cohort_specs.append((f"cluster={cl}", *_cohort_pair("cluster", cl)))

        # Lead buckets
        for lo, hi in _LEAD_BUCKETS:
            lbl = f"lead={lo}" if lo == hi else f"lead={lo}-{hi}"
            cohort_specs.append((lbl, *_cohort_pair("lead_bucket", f"{lo}-{hi}")))

        # Cycles
        for cyc in sorted({d["cycle"] for d in matched_ft_global}):
            cohort_specs.append((f"cycle={cyc}", *_cohort_pair("cycle", cyc)))

    # -----------------------------------------------------------------------
    # Score and render table rows
    # -----------------------------------------------------------------------
    print("Computing per-cohort metrics...", file=sys.stderr)
    table_rows: list[str] = []
    pit_blocks: list[str] = []
    n_insufficient = 0

    for label, r_sub, ft_sub in cohort_specs:
        n = len(r_sub)
        if n < MIN_MATCHED_FLOOR:
            n_insufficient += 1
            table_rows.append(_render_row(label, n, {}, {}))
            continue
        m_raw = _aggregate_metrics(r_sub)
        m_ft = _aggregate_metrics(ft_sub)
        table_rows.append(_render_row(label, n, m_raw, m_ft))
        if label in ("global", "coastal", "inland") or label.startswith("unit="):
            if m_raw.get("pit_hist"):
                pit_blocks.append(_pit_ascii(m_raw["pit_hist"], f"raw / {label}"))
            if m_ft.get("pit_hist"):
                pit_blocks.append(_pit_ascii(m_ft["pit_hist"], f"ft / {label}"))

    # -----------------------------------------------------------------------
    # Write markdown report
    # -----------------------------------------------------------------------
    metric_upper = args.metric.upper()
    n_global = len(matched_raw_global)
    n_raw_total = len(raw_dists)
    n_ft_total = len(ft_dists)

    domain_note = (
        f"> **n_matched (global) = {n_global}** — raw domain: {n_raw_total:,} dists, "
        f"ft domain: {n_ft_total:,} dists.\n"
        f"> Intersection key: (city, target_date, lead_days). "
        f"Events absent from either arm are excluded.\n"
        f"> (city, target_date)-only match (ignoring lead_days): {n_date_only:,}.\n"
    )
    if n_global == 0:
        domain_note += (
            "> **WARNING: Zero overlap.** The raw(none) and full_transport_v1 domains\n"
            "> are scored on entirely disjoint (city, target_date) sets in this DB.\n"
            "> This is the §4.1 confound. Run again post-Fix-A (#74) once both families\n"
            "> share a common date range. All cohorts below will show INSUFFICIENT.\n"
        )
        if n_date_only > 0:
            domain_note += (
                f"> NOTE: {n_date_only:,} (city, target_date) pairs overlap, "
                f"but lead_days granularity mismatch prevents strict matching.\n"
                "> Post-Fix-A: verify lead_days alignment (none=integer, "
                "full_transport_v1 may use finer values).\n"
            )
    elif n_global < 200:
        domain_note += (
            f"> **LOW MATCH WARNING:** only {n_global} matched pairs. "
            f"Cohort-level results are thin — interpret globally only.\n"
        )

    out_lines = [
        f"# ENS Refit Matched-Date Validation — {metric_upper} Temperature",
        "",
        f"Generated: 2026-05-25",
        f"DB: `{db_path}`",
        f"Authority: Zeus #64 eval tool / ENS_REFIT_REFINEMENT_ROADMAP_2026-05-25.md §4.1",
        f"Script: `scripts/audit_matched_date_proper_scores.py`",
        "",
        "## Purpose",
        "",
        "This tool kills the **0%-overlap confound** that invalidated §4.1 of the original",
        "audit: `raw(none)` and `full_transport_v1` were scored on temporally disjoint",
        "(city, target_date) sets — making any Brier/LogLoss delta meaningless.",
        "",
        "Fix: INTERSECT both domains on `(city, target_date, lead_days)` before scoring.",
        "Every comparison below is **head-to-head on the same forecast events**.",
        "",
        f"INSUFFICIENT floor: n_matched < {MIN_MATCHED_FLOOR} per cohort → not reported.",
        "",
        "## Domain overlap summary",
        "",
        domain_note,
        "",
        "## Matched-date proper scores: raw(none) vs full_transport_v1",
        "",
        "Metrics: multinomial Brier, LogLoss, RPS, P(actual), ECE.",
        "ΔMetric = ft − raw; **negative = full_transport_v1 improves on raw**.",
        "",
        _HEADER,
        _SEP,
    ] + table_rows + [
        "",
        f"> {n_insufficient} cohort(s) marked INSUFFICIENT (n_matched < {MIN_MATCHED_FLOOR}).",
        "",
        "## PIT histograms (global + coastal/inland + unit splits)",
        "",
        "```",
    ] + pit_blocks + [
        "```",
        "",
        "## Interpretation notes",
        "",
        "- Delta column (ft − raw): negative = ft better. Positive = raw better.",
        "- PIT: under perfect calibration, bins should be near-uniform.",
        "  U-shape from discrete distributions is expected artefact — compare relative shape.",
        "- If n_matched == 0 across all cohorts, Fix-A (#74) has not yet landed.",
        "  Re-run this tool after Fix-A populates overlapping (city, target_date) pairs.",
    ]

    out_text = "\n".join(out_lines)
    out_path.write_text(out_text, encoding="utf-8")
    print(f"\nResults written to: {out_path}", file=sys.stderr)

    # Quick summary to stdout
    print(f"\n=== Matched-date global summary ({metric_upper}) ===")
    print(f"  raw domain:  {n_raw_total:,} distributions")
    print(f"  ft domain:   {n_ft_total:,} distributions")
    print(f"  matched:     {n_global:,} pairs (intersection on city+target_date+lead_days)")
    if n_global >= MIN_MATCHED_FLOOR:
        m_raw = _aggregate_metrics(matched_raw_global)
        m_ft = _aggregate_metrics(matched_ft_global)
        print(f"  raw  Brier={m_raw['brier']:.4f}  LogLoss={m_raw['logloss']:.4f}  "
              f"RPS={m_raw['rps']:.4f}  ECE={m_raw['ece']:.4f}")
        print(f"  ft   Brier={m_ft['brier']:.4f}  LogLoss={m_ft['logloss']:.4f}  "
              f"RPS={m_ft['rps']:.4f}  ECE={m_ft['ece']:.4f}")
        db = m_ft["brier"] - m_raw["brier"]
        dl = m_ft["logloss"] - m_raw["logloss"]
        print(f"  ΔBrier={db:+.4f}  ΔLogLoss={dl:+.4f}  "
              f"({'ft better' if db < 0 else 'raw better or tied'})")
    else:
        print(f"  INSUFFICIENT matched pairs (< {MIN_MATCHED_FLOOR}); "
              f"no global scores computed.")
        print("  This is the expected result on the 2026-05-25 backup DB (disjoint domains).")
        print("  Re-run post-Fix-A (#74) for a meaningful comparison.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
