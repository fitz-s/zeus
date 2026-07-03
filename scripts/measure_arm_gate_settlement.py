# Created: 2026-06-03
# Last reused or audited: 2026-06-03
# Authority basis: arm-gate MEASURE step, GOAL live profit >51% after-cost settlement
#   win-rate (GOAL#36 / project_live_goal_2026_06_03.md); settlement truth =
#   zeus-forecasts.db.settlement_outcomes WHERE authority='VERIFIED'.
#
# Lifecycle: created=2026-06-03; last_reviewed=2026-06-03; last_reused=never
# Purpose: Reproducible after-cost settlement win-rate measurement for EDLI
#   positions, split by ALL and gate-PASS (mainstream_agreement_pass=1) cohorts.
#   Computes the ARM verdict: ELIGIBLE only if gate-PASS win_rate stably >51% after
#   cost, >=2sigma above breakeven, n>=20 pooled / n>=5 per city.
#   ANTI-FABRICATION: prints NULL/INSUFFICIENT verdict if gate-PASS cohort is empty.
# Reuse: re-run after each new batch of VERIFIED settlements lands. Gate-PASS receipts
#   must post-date gate deployment (commit fe53b00c98, ~2026-06-03). Inspect the
#   printed "gate-PASS overlap" count; if still 0, verdict stays INSUFFICIENT.
#
"""Measure after-cost settlement win-rate for EDLI measured positions.

Direction Law (must hold exactly):
    buy_yes on bin B: WIN iff settlement lands IN bin B  (settled_bin == B)
    buy_no  on bin B: WIN iff settlement does NOT land in bin B  (settled_bin != B)

Bin matching:
    The traded bin is extracted from receipt_json["bin_label"] using
    src.data.market_scanner._parse_temp_range which returns (lo, hi).
    The settlement value comes from settlement_outcomes.settlement_value
    (already WMO half-up rounded, integer precision).
    Bin containment: lo <= settlement_value <= hi (float-tolerant).
    Shoulder bins: lo=None means settlement_value <= hi; hi=None means >= lo.
    Unit: extracted from the bin_label text (°C or °F). Must match
    settlement_outcomes.settlement_unit. Mismatch rows are EXCLUDED with a warning.

Dedup: one final measured position per (city, target_date, token_id, direction) —
    latest decision_time kept (same approach as prior measurement).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# Ensure the repo root is on sys.path so src.* imports resolve correctly
# when the script is executed directly (python scripts/measure_arm_gate_settlement.py).
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_CANDIDATE = os.path.dirname(_SCRIPT_DIR)
if _ROOT_CANDIDATE not in sys.path:
    sys.path.insert(0, _ROOT_CANDIDATE)

# ---------------------------------------------------------------------------
# Repo root — walk up from __file__ to find the directory that contains
# state/zeus-world.db. Works from worktrees (e.g. /tmp/zeus-armgate) where
# state/ may not exist locally, falling back to the git-tracked worktree's
# common directory (i.e. the real working tree with live DBs).
# ---------------------------------------------------------------------------

def _is_populated_world_db(probe: str) -> bool:
    """True iff ``probe`` is a non-stub world DB carrying the receipts table.

    A worktree may ship an EMPTY ``state/zeus-world.db`` stub (e.g. a 4KB shell
    with no rows). Treating that as the repo root makes the measurement read an
    empty DB and silently report a vacuous DENIED — masking the real cohort.
    We therefore require the live table to exist before accepting a candidate;
    an empty stub is skipped so the git-common-dir fallback reaches the live
    checkout's populated state. (Anti-fabrication: never measure the wrong DB.)
    """
    if not os.path.exists(probe) or os.path.getsize(probe) == 0:
        return False
    try:
        c = sqlite3.connect(f"file:{probe}?mode=ro", uri=True)
        try:
            c.execute("SELECT 1 FROM edli_no_submit_receipts LIMIT 1").fetchone()
            return True
        finally:
            c.close()
    except sqlite3.OperationalError:
        return False
    except Exception:  # noqa: BLE001 — any access failure → not usable
        return False


def _find_repo_root() -> str:
    """Return the repo root directory that contains a POPULATED state/zeus-world.db.

    Search order:
      0. ``ZEUS_STATE_DIR`` env override (parent of the state dir) — lets the
         ARM measurement run from any worktree against the LIVE state.
      1. Walk up from this script's location until a populated state/zeus-world.db.
      2. Fall back to git rev-parse --git-common-dir (handles worktrees → main).
    Empty stub DBs are skipped (see _is_populated_world_db).
    """
    env_state = os.environ.get("ZEUS_STATE_DIR")
    if env_state:
        # ZEUS_STATE_DIR points AT the state dir; the repo root is its parent.
        return os.path.dirname(os.path.abspath(env_state.rstrip("/")))

    candidate = os.path.dirname(os.path.abspath(__file__))
    while True:
        probe = os.path.join(candidate, "state", "zeus-world.db")
        if _is_populated_world_db(probe):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent

    # Fallback: ask git for the common .git dir (always points to main worktree's .git)
    try:
        import subprocess
        common_git = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            text=True,
        ).strip().rstrip("/")
        # common_git is like /repo/.git — strip trailing /.git or .git component
        # to get the working tree root.
        if common_git.endswith("/.git"):
            repo_root = common_git[: -len("/.git")]
        elif common_git.endswith(".git"):
            repo_root = os.path.dirname(common_git)
        else:
            repo_root = os.path.dirname(common_git)
        probe = os.path.join(repo_root, "state", "zeus-world.db")
        if _is_populated_world_db(probe):
            return repo_root
    except Exception:
        pass

    # Last resort: use script location's parent
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_REPO_ROOT = _find_repo_root()
_WORLD_DB = os.path.join(_REPO_ROOT, "state", "zeus-world.db")
_FORECASTS_DB = os.path.join(_REPO_ROOT, "state", "zeus-forecasts.db")


# ---------------------------------------------------------------------------
# Per-row grading — the ONE truth path (H2 consolidation, STRUCTURAL_FIX_PLAN
# §P0.1). Grading is delegated to src.contracts.graded_receipt.grade_receipt —
# the single settlement-grounded, unit-correct, BinKind-aware win function. The
# former local ``is_win`` heuristic (a raw-float range test with no unit, no
# BinKind, no per-city rounding) is DELETED: it was a second grading path, and
# two grading paths is exactly the consolidation debt this fix retires. The
# Direction Law now has ONE implementation, exercised here and antibody-tested
# in tests/test_arm_gate_direction_law.py against grade_receipt.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _SettlementStandin:
    """Minimal settlement object satisfying grade_receipt's _SettlementLike.

    grade_receipt reads only ``settlement_value`` + ``settlement_unit``; this
    carries exactly those two, keeping the truth function decoupled from the
    settlement_outcomes row shape.
    """

    settlement_value: float
    settlement_unit: str


def _grade_row_won(
    direction: str,
    traded_bin_lo: Optional[float],
    traded_bin_hi: Optional[float],
    settlement_value: float,
    settlement_unit: str,
    bin_label: str,
) -> Optional[bool]:
    """Grade one measured position via the canonical ``grade_receipt`` truth fn.

    Returns ``won`` (bool) for a gradeable row, or ``None`` when the row cannot
    be graded (bin label un-buildable into a ``Bin``, or a unit mismatch). A
    ``None`` return tells the caller to EXCLUDE the row — the same skip
    semantics the legacy path applied to None/None parses and unit mismatches,
    but now enforced through the typed antibodies rather than ad-hoc float
    checks. The caller logs the exclusion so a silent cohort shift is visible.

    Raises:
        ValueError: if ``direction`` is not 'buy_yes' / 'buy_no' (propagated
            from grade_receipt — an unknown direction is a programmer error,
            not a data-skip case).
    """
    from src.contracts.graded_receipt import grade_receipt
    from src.types.market import Bin
    from src.types.temperature import UnitMismatchError

    # Both None means the bin label failed to parse — cannot evaluate win/loss.
    if traded_bin_lo is None and traded_bin_hi is None:
        return None

    try:
        bin_obj = Bin(
            low=traded_bin_lo,
            high=traded_bin_hi,
            unit=settlement_unit,
            label=bin_label,
        )
    except Exception:  # noqa: BLE001 — malformed/width-invalid bin → exclude, never crash
        return None

    # Minimal settlement stand-in (settlement_value already WMO-rounded at
    # write time — grade_receipt is called WITHOUT semantics, using the value
    # as-is, matching the legacy path that range-tested the rounded value).
    settlement = _SettlementStandin(
        settlement_value=float(settlement_value),
        settlement_unit=settlement_unit,
    )

    try:
        graded = grade_receipt(bin_obj, direction, settlement)
    except UnitMismatchError:
        return None
    return graded.won


# ---------------------------------------------------------------------------
# Capital-weighted ARM verdict (F3 — STRUCTURAL_FIX_PLAN §P0.2)
#
# The equal-row win-rate is row-democracy: every settled position counts the
# same regardless of how much capital it carried. That hides the K2 failure
# mode where the system sizes UP on the bets it is most wrong about — a cohort
# can clear 51% by row count while LOSING money capital-weighted. The ARM
# decision must therefore consume a CAPITAL-WEIGHTED verdict, and it must fail
# CLOSED when any settled row is missing its size (never silently equal-weight).
# ---------------------------------------------------------------------------

# A per-city capital-weighted ROI cluster this negative DENIES arming even when
# the pooled CW-ROI is positive (one strong city must not mask a losing one).
_PER_CITY_CW_ROI_TOLERANCE = -1e-9

# Current live probability-regime provenance. Older receipts omitted q_source
# for hash stability before #120; those rows are useful diagnostic settlement
# history, but they cannot license the current EMOS/honest-raw production
# mechanism for real submit.
CURRENT_ARM_Q_SOURCES = frozenset({"emos", "raw_honest"})


@dataclass(frozen=True)
class CapitalWeightedArmVerdict:
    """Capital-weighted ARM measurement. ALL fields required — no Optional.

    The dataclass cannot be constructed without every metric, so a caller can
    never receive a verdict that silently dropped the capital dimension.
    """

    equal_row_win_rate: float       # row-democracy win-rate (the headline number)
    equal_row_ev_sigma: float       # σ of equal-row mean EV above 0
    capital_weighted_roi: float     # Σ net / Σ invested (size-weighted)
    capital_weighted_ev_sigma: float  # σ of the size-weighted mean EV above 0
    per_city_cw_roi: dict           # city -> capital-weighted ROI for that city
    n: int = 0                      # pooled row count
    per_city_n: dict = field(default_factory=dict)  # city -> row count


def _compute_capital_weighted_verdict(rows: list[dict]) -> CapitalWeightedArmVerdict:
    """Build a CapitalWeightedArmVerdict from graded ARM rows.

    Each row needs: ``win`` (bool), ``price`` (entry cost in 0-1 USDC space),
    ``kelly_size_usd`` (the capital staked — the size source verified present
    in edli_no_submit_receipts).

    Fails CLOSED:
        ValueError('MISSING_SIZE') if any row has kelly_size_usd None or <= 0.
        Missing size must never be silently treated as equal weight — that
        would reintroduce the exact row-democracy blindspot this guards against.
    """
    n = len(rows)
    if n == 0:
        return CapitalWeightedArmVerdict(
            equal_row_win_rate=0.0,
            equal_row_ev_sigma=0.0,
            capital_weighted_roi=0.0,
            capital_weighted_ev_sigma=0.0,
            per_city_cw_roi={},
            n=0,
            per_city_n={},
        )

    # Fail closed on any missing/non-positive size BEFORE any aggregation.
    for r in rows:
        sz = r.get("kelly_size_usd")
        if sz is None or sz <= 0:
            raise ValueError(
                f"MISSING_SIZE: row city={r.get('city')!r} has kelly_size_usd="
                f"{sz!r} (None or <=0). Capital-weighted ARM cannot equal-weight "
                f"a sizeless row — fix the size source, do not impute."
            )

    # --- Equal-row stats (row democracy) ---
    wins = sum(1 for r in rows if r["win"])
    equal_row_win_rate = wins / n
    equal_evs = [(1.0 - r["price"]) if r["win"] else (-r["price"]) for r in rows]
    mean_equal_ev = sum(equal_evs) / n
    if n > 1:
        var_equal = sum((e - mean_equal_ev) ** 2 for e in equal_evs) / n
        se_equal = math.sqrt(var_equal / n) if var_equal > 0 else 0.0
    else:
        se_equal = 0.0
    equal_row_ev_sigma = (mean_equal_ev / se_equal) if se_equal > 0 else 0.0

    # --- Capital-weighted stats (size democracy) ---
    # Net dollars on a position of size S at price p:
    #   win  → S * (1/p - 1)   (S buys S/p shares, each pays $1, cost S)
    #   loss → -S
    # ROI = Σ net / Σ invested(=Σ S).
    def _net_usd(r: dict) -> float:
        s = float(r["kelly_size_usd"])
        p = float(r["price"])
        if p <= 0:
            return 0.0
        return s * (1.0 / p - 1.0) if r["win"] else -s

    total_invested = sum(float(r["kelly_size_usd"]) for r in rows)
    total_net = sum(_net_usd(r) for r in rows)
    capital_weighted_roi = (total_net / total_invested) if total_invested > 0 else 0.0

    # Size-weighted per-trade EV (per dollar staked) and its σ.
    per_dollar_ev = [_net_usd(r) / float(r["kelly_size_usd"]) for r in rows]
    weights = [float(r["kelly_size_usd"]) for r in rows]
    wsum = sum(weights)
    mean_cw_ev = sum(w * e for w, e in zip(weights, per_dollar_ev)) / wsum if wsum > 0 else 0.0
    if n > 1 and wsum > 0:
        var_cw = sum(w * (e - mean_cw_ev) ** 2 for w, e in zip(weights, per_dollar_ev)) / wsum
        # effective sample size for a weighted mean (Kish): (Σw)^2 / Σw^2
        n_eff = (wsum * wsum) / sum(w * w for w in weights)
        se_cw = math.sqrt(var_cw / n_eff) if (var_cw > 0 and n_eff > 0) else 0.0
    else:
        se_cw = 0.0
    capital_weighted_ev_sigma = (mean_cw_ev / se_cw) if se_cw > 0 else 0.0

    # --- Per-city capital-weighted ROI ---
    by_city: dict[str, list] = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)
    per_city_cw_roi: dict[str, float] = {}
    per_city_n: dict[str, int] = {}
    for city, crows in by_city.items():
        inv = sum(float(r["kelly_size_usd"]) for r in crows)
        net = sum(_net_usd(r) for r in crows)
        per_city_cw_roi[city] = (net / inv) if inv > 0 else 0.0
        per_city_n[city] = len(crows)

    return CapitalWeightedArmVerdict(
        equal_row_win_rate=equal_row_win_rate,
        equal_row_ev_sigma=equal_row_ev_sigma,
        capital_weighted_roi=capital_weighted_roi,
        capital_weighted_ev_sigma=capital_weighted_ev_sigma,
        per_city_cw_roi=per_city_cw_roi,
        n=n,
        per_city_n=per_city_n,
    )


def _capital_weighted_arm_decision(
    verdict: CapitalWeightedArmVerdict,
    *,
    min_n_pooled: int = 20,
    min_n_per_city: int = 5,
    min_cw_sigma: float = 2.0,
    min_equal_row_win_rate: float = 0.51,
) -> tuple[bool, str]:
    """ARM decision from a capital-weighted verdict. Fail closed on ALL of:

        1. pooled n >= min_n_pooled
        2. capital_weighted_roi > 0           (size-weighted money is positive)
        3. NO per-city capital cluster negative beyond tolerance
        4. capital_weighted_ev_sigma >= min_cw_sigma
        5. equal_row_win_rate > min_equal_row_win_rate (headline sanity)
        6. every active city has n >= min_n_per_city
    """
    if verdict.n == 0:
        return False, "INSUFFICIENT: empty cohort (no settled rows)"

    # CORRECTNESS VETOES — these run BEFORE the sufficiency (n-floor) gates,
    # because a money-losing or per-city-negative cohort is DENIED on the
    # merits regardless of how large it is. Ordering the n-floor first would
    # let a small losing cohort report the softer "INSUFFICIENT" and hide the
    # fact that, even at scale, this cohort should never arm.

    # Veto 1 — pooled capital-weighted ROI must be strictly positive.
    if verdict.capital_weighted_roi <= 0.0:
        return False, (
            f"DENIED: capital_weighted_roi={verdict.capital_weighted_roi:.4f} <= 0 "
            f"(the cohort loses money once sized — row-rate "
            f"{verdict.equal_row_win_rate:.3f} is row-democracy only)"
        )

    # Veto 2 — no per-city capital cluster may be negative beyond tolerance.
    neg_cities = sorted(
        c for c, roi in verdict.per_city_cw_roi.items()
        if roi < _PER_CITY_CW_ROI_TOLERANCE
    )
    if neg_cities:
        detail = ", ".join(
            f"{c}(roi={verdict.per_city_cw_roi[c]:.3f})" for c in neg_cities
        )
        return False, (
            f"DENIED: {len(neg_cities)} city/cities capital-weighted NEGATIVE: "
            f"{detail}. A positive pool must not mask a losing city cluster."
        )

    # SUFFICIENCY GATES — only reached once the cohort is not money-losing.
    if verdict.n < min_n_pooled:
        return False, f"INSUFFICIENT: n={verdict.n} < {min_n_pooled} minimum"

    # Confidence.
    if verdict.capital_weighted_ev_sigma < min_cw_sigma:
        return False, (
            f"DENIED: capital_weighted_ev_sigma="
            f"{verdict.capital_weighted_ev_sigma:.2f} < {min_cw_sigma:.1f}"
        )

    # 5 — headline equal-row sanity.
    if verdict.equal_row_win_rate <= min_equal_row_win_rate:
        return False, (
            f"DENIED: equal_row_win_rate={verdict.equal_row_win_rate:.3f} "
            f"not > {min_equal_row_win_rate}"
        )

    # 6 — per-city n floor.
    thin = sorted(c for c, nn in verdict.per_city_n.items() if nn < min_n_per_city)
    if thin:
        detail = ", ".join(f"{c}(n={verdict.per_city_n[c]})" for c in thin)
        return False, (
            f"DENIED: per-city n<{min_n_per_city} for {len(thin)} city/cities: "
            f"{detail}."
        )

    return True, (
        f"ELIGIBLE: cw_roi={verdict.capital_weighted_roi:.4f}>0, "
        f"cw_sigma={verdict.capital_weighted_ev_sigma:.2f}>={min_cw_sigma}, "
        f"win_rate={verdict.equal_row_win_rate:.3f}>{min_equal_row_win_rate}, "
        f"all {len(verdict.per_city_n)} cities n>={min_n_per_city} & cw_roi>=0"
    )


def _current_regime_rows(rows: list[dict]) -> list[dict]:
    """Rows allowed to license the current live probability mechanism.

    Missing q_source means pre-provenance history. Treat it as diagnostic only:
    it must neither grant nor deny a current-regime ARM license. This keeps the
    boot gate fail-closed on new mechanisms until their own settled receipts
    exist, instead of mixing an older calibration era into live promotion.
    """
    return [
        r for r in rows
        if str(r.get("q_source") or "").strip() in CURRENT_ARM_Q_SOURCES
    ]


# ---------------------------------------------------------------------------
# Bin label parsing helpers
# ---------------------------------------------------------------------------

def _parse_temp_range_local(label: str) -> Optional[tuple[Optional[float], Optional[float]]]:
    """Parse (lo, hi) from a market bin_label string.

    Delegates to src.data.market_scanner._parse_temp_range.
    Returns None if parse fails.
    """
    try:
        from src.data.market_scanner import _parse_temp_range
        return _parse_temp_range(label)
    except Exception:
        return None


def _extract_unit(label: str) -> Optional[str]:
    """Extract unit letter ('C' or 'F') from a bin_label string."""
    m = re.search(r"\d+°([CF])", label)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_deduped_receipts(world_db: str) -> list[dict]:
    """Load edli_no_submit_receipts, deduped to one final position per
    (city, target_date, token_id, direction) — latest decision_time kept.
    Returns a list of dicts with parsed fields.
    """
    conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT receipt_id, token_id, direction, c_fee_adjusted,
               kelly_size_usd, mainstream_agreement_pass, decision_time,
               receipt_json
        FROM edli_no_submit_receipts
        ORDER BY decision_time DESC
    """)

    seen: dict[tuple, dict] = {}  # key -> row dict (latest decision wins)
    for row in cur.fetchall():
        d = dict(row)
        rj = json.loads(d["receipt_json"])
        city = rj.get("city")
        target_date = rj.get("target_date")
        token_id = d.get("token_id") or rj.get("token_id")
        direction = d["direction"]
        bin_label = rj.get("bin_label", "")
        metric = rj.get("metric", "high")

        key = (city, target_date, token_id, direction)
        if key not in seen:
            parsed = _parse_temp_range_local(bin_label)
            unit = _extract_unit(bin_label)
            seen[key] = {
                "city": city,
                "target_date": target_date,
                "metric": metric,
                "direction": direction,
                "c_fee_adjusted": d["c_fee_adjusted"],
                "kelly_size_usd": d["kelly_size_usd"],
                "mainstream_agreement_pass": d["mainstream_agreement_pass"],
                "decision_time": d["decision_time"],
                "q_source": rj.get("q_source"),
                "bin_label": bin_label,
                "traded_lo": parsed[0] if parsed else None,
                "traded_hi": parsed[1] if parsed else None,
                "unit": unit,
            }

    conn.close()
    return list(seen.values())


def _load_settlements(forecasts_db: str) -> dict[tuple, dict]:
    """Load VERIFIED settlement_outcomes, keyed by (city, target_date, temperature_metric)."""
    conn = sqlite3.connect(f"file:{forecasts_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT city, target_date, temperature_metric,
               settlement_value, settlement_unit, winning_bin
        FROM settlement_outcomes
        WHERE authority = 'VERIFIED'
    """)
    result: dict[tuple, dict] = {}
    for row in cur.fetchall():
        d = dict(row)
        key = (d["city"], d["target_date"], d["temperature_metric"])
        # If duplicate, prefer first (shouldn't happen on VERIFIED)
        if key not in result:
            result[key] = d
    conn.close()
    return result


# ---------------------------------------------------------------------------
# Win-rate computation
# ---------------------------------------------------------------------------

def _compute_rows(receipts: list[dict], settlements: dict) -> list[dict]:
    """Join receipts to settlements and compute win flag per Direction Law.

    Returns list of dicts with: city, target_date, direction, win, price,
    gate_pass, matched_settlement_value, matched_settlement_unit.
    Only rows with a matched VERIFIED settlement are included.
    Unit-mismatch rows are excluded (logged to stderr).
    """
    rows = []
    unit_mismatch = 0

    for r in receipts:
        key = (r["city"], r["target_date"], r["metric"])
        s = settlements.get(key)
        if s is None:
            continue  # no VERIFIED settlement for this (city, date, metric)

        # Unit sanity check: traded bin unit must match settlement unit
        traded_unit = r["unit"]
        settle_unit = s["settlement_unit"]
        if traded_unit is not None and settle_unit is not None:
            if traded_unit.upper() != settle_unit.upper():
                unit_mismatch += 1
                print(
                    f"  [WARN] unit mismatch: city={r['city']} date={r['target_date']} "
                    f"traded_unit={traded_unit} settle_unit={settle_unit} — row EXCLUDED",
                    file=sys.stderr,
                )
                continue

        lo, hi = r["traded_lo"], r["traded_hi"]
        if lo is None and hi is None:
            # parse failure — skip
            continue

        # H2: grade through the ONE truth function (grade_receipt). A None
        # return means the typed path could not grade the row (un-buildable Bin
        # or unit mismatch that slipped past the string check above) — exclude
        # it and log, never silently count it. On the current live cohort this
        # is 0 rows (verdict-equivalent to the retired is_win path, verified).
        won = _grade_row_won(
            direction=r["direction"],
            traded_bin_lo=lo,
            traded_bin_hi=hi,
            settlement_value=s["settlement_value"],
            settlement_unit=s["settlement_unit"],
            bin_label=r["bin_label"],
        )
        if won is None:
            print(
                f"  [WARN] ungradeable bin (grade_receipt could not build/grade): "
                f"city={r['city']} date={r['target_date']} label={r['bin_label']!r} "
                f"— row EXCLUDED",
                file=sys.stderr,
            )
            continue

        win = won
        rows.append({
            "city": r["city"],
            "target_date": r["target_date"],
            "direction": r["direction"],
            "win": win,
            "price": r["c_fee_adjusted"],
            "kelly_size_usd": r["kelly_size_usd"],
            "gate_pass": r["mainstream_agreement_pass"] == 1,
            "q_source": r.get("q_source"),
            "settlement_value": s["settlement_value"],
            "settlement_unit": s["settlement_unit"],
            "traded_lo": lo,
            "traded_hi": hi,
            "bin_label": r["bin_label"],
        })

    if unit_mismatch:
        print(f"  [WARN] {unit_mismatch} rows excluded due to unit mismatch.", file=sys.stderr)

    return rows


def _stats(rows: list[dict], label: str) -> dict:
    """Compute aggregate stats for a list of rows."""
    n = len(rows)
    if n == 0:
        return {"label": label, "n": 0, "win_rate": None, "breakeven": None,
                "roi": None, "mean_ev": None, "se_ev": None, "ev_sigma": None,
                "arm_eligible": False}
    wins = sum(1 for r in rows if r["win"])
    win_rate = wins / n
    avg_price = sum(r["price"] for r in rows) / n
    # After-cost EV per trade: payout if win is (1 - price), loss if lose is (-price)
    # In USDC prediction market: win pays out $1 per share, so net = (1 - price) for win,
    # net = (-price) for loss. ROI = total net / total invested
    total_invested = sum(r["price"] for r in rows)
    total_net = sum((1.0 - r["price"]) if r["win"] else (-r["price"]) for r in rows)
    roi = total_net / total_invested if total_invested > 0 else 0.0

    # Per-trade EV = win_rate - price (in 0-1 USDC space)
    evs = [(1.0 - r["price"]) if r["win"] else (-r["price"]) for r in rows]
    mean_ev = sum(evs) / n
    variance = sum((e - mean_ev) ** 2 for e in evs) / n if n > 1 else 0.0
    se = math.sqrt(variance / n) if n > 1 else float("inf")

    # win_rate vs breakeven (price) in points
    edge_points = win_rate - avg_price
    # sigma above breakeven
    ev_sigma = mean_ev / se if se > 0 else 0.0

    return {
        "label": label,
        "n": n,
        "win_rate": win_rate,
        "breakeven": avg_price,
        "roi": roi,
        "mean_ev": mean_ev,
        "se_ev": se,
        "ev_sigma": ev_sigma,
        "edge_points": edge_points,
        "arm_eligible": False,  # evaluated separately
    }


def _arm_verdict(stats: dict, rows: list[dict], min_n_pooled: int = 20,
                 min_sigma: float = 2.0, min_win_rate: float = 0.51,
                 min_n_per_city: int = 5) -> tuple[bool, str]:
    """Return (arm_eligible, reason) for a gate-PASS cohort.

    Enforces ALL documented ARM criteria (must match script header / PR claim):
        1. Pooled n >= min_n_pooled  (default 20)
        2. win_rate > min_win_rate   (default 51%)
        3. ev_sigma >= min_sigma     (default 2.0)
        4. Every city in the cohort has n >= min_n_per_city (default 5)
           A city with n < 5 is NOT eligible; pooled criteria alone are insufficient.

    Tightening note: the previous version only checked 1-3. Criterion 4 is added here
    to match the documented arm criteria exactly (per script header line 11-12).
    This is an ARM-DECISION tool — a too-lenient verdict is dangerous.
    """
    if stats["n"] == 0:
        return False, "INSUFFICIENT: gate-PASS cohort empty (no overlap with VERIFIED settlements)"
    if stats["n"] < min_n_pooled:
        return False, f"INSUFFICIENT: n={stats['n']} < {min_n_pooled} minimum"
    if stats["win_rate"] is None or stats["win_rate"] <= min_win_rate:
        return False, f"DENIED: win_rate={stats['win_rate']:.3f} not > {min_win_rate}"
    if stats["ev_sigma"] is None or stats["ev_sigma"] < min_sigma:
        return False, f"DENIED: ev_sigma={stats['ev_sigma']:.2f} < {min_sigma:.1f} (insufficient confidence)"

    # Per-city n >= min_n_per_city check (criterion 4 — must all pass)
    by_city: dict[str, int] = defaultdict(int)
    for r in rows:
        by_city[r["city"]] += 1
    thin_cities = sorted(c for c, n in by_city.items() if n < min_n_per_city)
    if thin_cities:
        thin_detail = ", ".join(f"{c}(n={by_city[c]})" for c in thin_cities)
        return False, (
            f"DENIED: per-city n<{min_n_per_city} for {len(thin_cities)} city/cities: "
            f"{thin_detail}. ALL cities must have n>={min_n_per_city} to be ARM-ELIGIBLE."
        )

    return True, (
        f"ELIGIBLE: win_rate={stats['win_rate']:.3f} > {min_win_rate}, "
        f"ev_sigma={stats['ev_sigma']:.2f} >= {min_sigma}, "
        f"all {len(by_city)} cities n>={min_n_per_city}"
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------

def _fmt_pct(v) -> str:
    return f"{v*100:.1f}%" if v is not None else "  N/A "


def _fmt_f(v, fmt=".4f") -> str:
    return format(v, fmt) if v is not None else "  N/A  "


def _print_stats_row(s: dict) -> None:
    label = s["label"]
    n = s["n"]
    if n == 0:
        print(f"  {label:<30s}  n={n:>5}  [EMPTY — no settled overlap]")
        return
    wr = _fmt_pct(s["win_rate"])
    be = _fmt_pct(s["breakeven"])
    roi = _fmt_pct(s["roi"])
    ev = _fmt_f(s["mean_ev"])
    se = _fmt_f(s["se_ev"])
    sigma = _fmt_f(s.get("ev_sigma"), ".2f")
    edge = _fmt_pct(s.get("edge_points"))
    print(
        f"  {label:<30s}  n={n:>5}  wr={wr}  be={be}  "
        f"roi={roi}  ev={ev}±{se}  σ={sigma}  edge={edge}"
    )


def _print_per_city_table(rows: list[dict], title: str) -> None:
    if not rows:
        print(f"\n  {title}: [empty]")
        return
    print(f"\n  {title} (per city):")
    by_city: dict[str, list] = defaultdict(list)
    for r in rows:
        by_city[r["city"]].append(r)
    for city in sorted(by_city):
        s = _stats(by_city[city], city)
        _print_stats_row(s)


# ---------------------------------------------------------------------------
# ARM-GATE ARTIFACT EMITTER (H3 — close the producer/consumer gap)
#
# PR-2's live boot gate (D2) consumes ``state/edli_arm_gate_artifact.json`` and
# REFUSES to arm unless it carries every required field AND
# ``capital_weighted_ev > 0`` AND ``coverage_licensed is True`` (plus a
# commit_sha / measurement_cmd_hash match). NO producer wrote that file — so
# arming was structurally impossible: the consumer existed, the producer did
# not. This emitter is the missing producer.
#
# ANTIBODY / fail-closed: the artifact reflects the HONEST measured verdict.
# With current data the cohort is DENIED, so the emitted artifact MUST carry
# ``capital_weighted_ev <= 0`` and ``coverage_licensed: false`` — values the
# consumer REJECTS. An ARM_ELIGIBLE artifact is NEVER emitted on DENIED data;
# the producer cannot manufacture an arming license the measurement did not
# earn. ``coverage_licensed`` is hardcoded False because no settlement-
# calibrated coverage license (K3) exists yet on this branch — when one lands,
# this is the single line that flips.
# ---------------------------------------------------------------------------

# SINGLE SOURCE OF TRUTH (2026-06-04 antibody): the artifact ``schema`` string is
# OWNED BY THE CONSUMER (the boot gate is the authority that gates arming). This
# producer must emit EXACTLY what ``verify_edli_arm_gate_artifact`` enforces, else
# the boot gate rejects every artifact with ARM_GATE_ARTIFACT_SCHEMA_INVALID — a
# permanent un-armable state no amount of re-emission could fix. The prior literal
# ``"edli_arm_gate_artifact/v1"`` here diverged from the consumer's
# ``"edli_arm_gate_v1"`` (producer/consumer schema-string mismatch — caught by the
# CROSS-MODULE relationship test tests/test_arm_gate_emit_scheduler_job.py, which
# the older same-island test missed by asserting producer-vs-producer). Importing
# the consumer's constant makes the divergence category unconstructable.
from src.events.live_profit_audit import (  # noqa: E402 — after sys.path injection above
    ARM_GATE_ARTIFACT_SCHEMA as ARM_ARTIFACT_SCHEMA,
)

# Fields PR-2's D2 boot gate requires. Kept here as the explicit contract
# the producer fills — a missing key is a producer bug caught by the H3 test.
ARM_ARTIFACT_REQUIRED_FIELDS = frozenset({
    "schema",
    "commit_sha",
    "measurement_cmd_hash",
    "capital_weighted_ev",
    "production_n",
    # Deprecated compatibility alias for older readers. The measured cohort is
    # production/all_rows, not the diagnostic mainstream gate-PASS subset.
    "gate_pass_n",
    "per_city_n",
    "ev_sigma",
    "date_coverage",
    "coverage_licensed",
})


def _git_head_sha() -> str:
    """Return the current HEAD commit SHA, or 'UNKNOWN' if git is unavailable.

    The boot gate matches this against the running checkout's SHA; emitting
    'UNKNOWN' guarantees a mismatch (fail-closed) rather than a false pass.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=_SCRIPT_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip() or "UNKNOWN"
    except Exception:  # noqa: BLE001 — git missing/detached → fail-closed sentinel
        return "UNKNOWN"


def _measurement_cmd_hash(argv: list[str]) -> str:
    """Hash of (this script's source bytes + sorted argv).

    Binds the artifact to the EXACT measurement code + invocation that produced
    it. The boot gate re-derives this hash from the live checkout's script; any
    drift (script edited, different args) changes the hash and fails the gate.
    Sorting argv makes the hash invocation-order-insensitive but argument-set-
    sensitive.
    """
    h = hashlib.sha256()
    try:
        with open(os.path.abspath(__file__), "rb") as fh:
            h.update(fh.read())
    except OSError:
        h.update(b"SCRIPT_SOURCE_UNREADABLE")
    h.update(b"\x00")
    for a in sorted(argv):
        h.update(a.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def build_arm_artifact(
    cw_verdict: "CapitalWeightedArmVerdict",
    gate_rows: list[dict],
    *,
    argv: list[str],
    coverage_licensed: bool = False,
) -> dict:
    """Build the boot-gate artifact dict from the CURRENT measurement.

    The artifact is a faithful projection of the measured cohort — never an
    aspiration. ``capital_weighted_ev`` is the size-weighted ROI (Σnet/Σinvested):
    the field the consumer's ``>0`` rule tests, and the honest "does this cohort
    make money once sized" signal. On a DENIED cohort it is <=0, so the consumer
    rejects.

    Args:
        cw_verdict: the capital-weighted verdict for the gate-PASS cohort.
        gate_rows: the graded gate-PASS rows (for date-coverage evidence).
        argv: the script argv (for the measurement_cmd_hash binding).
        coverage_licensed: K3 settlement-calibrated coverage license. False
            until such a license exists — do NOT pass True without one.

    Returns:
        A dict carrying exactly ``ARM_ARTIFACT_REQUIRED_FIELDS``.
    """
    # date_coverage: the distinct (city, target_date) settled pairs the gate-PASS
    # cohort actually covers — count + the sorted list as evidence.
    date_pairs = sorted({(r["city"], r["target_date"]) for r in gate_rows})
    artifact = {
        "schema": ARM_ARTIFACT_SCHEMA,
        "commit_sha": _git_head_sha(),
        "measurement_cmd_hash": _measurement_cmd_hash(argv),
        # capital_weighted_ev = size-weighted ROI; the consumer rejects on <=0.
        "capital_weighted_ev": cw_verdict.capital_weighted_roi,
        "production_n": cw_verdict.n,
        # Deprecated alias retained for consumers that still read the old name.
        "gate_pass_n": cw_verdict.n,
        "per_city_n": dict(cw_verdict.per_city_n),
        "ev_sigma": cw_verdict.capital_weighted_ev_sigma,
        "date_coverage": {
            "n_pairs": len(date_pairs),
            "pairs": [list(p) for p in date_pairs],
        },
        # Hardcoded False: no settlement-calibrated coverage license yet (K3).
        "coverage_licensed": bool(coverage_licensed),
    }
    return artifact


def emit_arm_artifact(path: str, artifact: dict) -> None:
    """Atomically write the artifact JSON (tmp + os.replace), per repo convention."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        description="Measure after-cost settlement win-rate for EDLI measured "
                    "positions and compute the capital-weighted ARM verdict.",
    )
    parser.add_argument(
        "--emit-artifact",
        metavar="PATH",
        default=None,
        help="Write the boot-gate artifact (state/edli_arm_gate_artifact.json "
             "contract) reflecting the CURRENT measurement to PATH. On a DENIED "
             "cohort the artifact carries capital_weighted_ev<=0 / "
             "coverage_licensed:false so PR-2's D2 boot gate REJECTS it — arming "
             "stays blocked. An ARM_ELIGIBLE artifact is never emitted on DENIED data.",
    )
    args = parser.parse_args(argv)

    print("=" * 78)
    print("EDLI ARM-GATE SETTLEMENT WIN-RATE MEASUREMENT")
    print(f"DB sources:  {_WORLD_DB}")
    print(f"             {_FORECASTS_DB}")
    print("=" * 78)

    # --- Load ---
    receipts = _load_deduped_receipts(_WORLD_DB)
    settlements = _load_settlements(_FORECASTS_DB)
    print(f"\nDeduped measured positions:      {len(receipts):>6}")
    print(f"VERIFIED settlements:            {len(settlements):>6}")

    # --- Join ---
    all_rows = _compute_rows(receipts, settlements)
    gate_rows = [r for r in all_rows if r["gate_pass"]]
    production_rows = _current_regime_rows(all_rows)
    legacy_or_unproven_rows = [
        r for r in all_rows
        if str(r.get("q_source") or "").strip() not in CURRENT_ARM_Q_SOURCES
    ]

    # Coverage summary
    settled_dates = sorted({(r["city"], r["target_date"]) for r in all_rows})
    gate_dates = sorted({(r["city"], r["target_date"]) for r in gate_rows})
    production_dates = sorted({(r["city"], r["target_date"]) for r in production_rows})
    print(f"\nSettled (city,date) pairs in ALL cohort:      {len(settled_dates)}")
    print(f"Settled (city,date) pairs in gate-PASS cohort:{len(gate_dates)}")
    print(f"Settled (city,date) pairs in CURRENT regime: {len(production_dates)}")
    print(
        "Current-regime q_source allowlist:       "
        f"{', '.join(sorted(CURRENT_ARM_Q_SOURCES))}"
    )
    print(f"Legacy/unproven rows excluded from ARM:  {len(legacy_or_unproven_rows)}")

    if not gate_rows:
        print("\n" + "!" * 78)
        print("  GATE-PASS COHORT: EMPTY")
        print("  All receipts with mainstream_agreement_pass=1 target dates NOT YET")
        print("  covered by VERIFIED settlements (gate deployed post-settlement cutoff).")
        print("  Dates in gate-PASS receipts:", sorted({r["target_date"] for r in
              [x for x in _load_deduped_receipts(_WORLD_DB) if x["mainstream_agreement_pass"] == 1]}))
        print("  Most recent VERIFIED settlement date:",
              max((s["target_date"] for s in settlements.values()), default="none"))
        print("!" * 78)

    # --- ALL cohort stats ---
    print("\n" + "=" * 78)
    print("COHORT: ALL (ungated, pooled)")
    print("=" * 78)
    all_stats = _stats(all_rows, "ALL pooled")
    _print_stats_row(all_stats)
    _print_per_city_table(all_rows, "ALL")

    # --- Gate-PASS cohort stats ---
    print("\n" + "=" * 78)
    print("COHORT: GATE-PASS (mainstream_agreement_pass=1)")
    print("=" * 78)
    gate_stats = _stats(gate_rows, "GATE-PASS pooled")
    _print_stats_row(gate_stats)
    _print_per_city_table(gate_rows, "GATE-PASS")

    # --- CAPITAL-WEIGHTED verdict (F3 — the authoritative ARM decision) ---
    # The equal-row verdict (_arm_verdict) is row-democracy and shown for
    # continuity. The ARM DECISION is the capital-weighted one: a cohort can
    # clear 51% by row count while losing money once sized.
    # OPERATOR INTENT RESTORED (2026-06-04): mainstream-agreement is REFERENCE-ONLY
    # and must NEVER wire into a production/arm decision. The ARM VERDICT is
    # computed on the PRODUCTION cohort for the CURRENT probability mechanism
    # (q_source in CURRENT_ARM_Q_SOURCES), NOT the gate-PASS subset and NOT old
    # pre-provenance receipts. Legacy/no-q_source rows remain diagnostic only:
    # they cannot license current EMOS/honest-raw, and they cannot deny it by
    # being mixed into the current regime. If no current-regime settled rows
    # exist, arming fails closed as INSUFFICIENT.
    print("\n" + "=" * 78)
    print("CAPITAL-WEIGHTED ARM VERDICT (CURRENT PRODUCTION regime — q_source provenanced)")
    print("=" * 78)
    cw_verdict: Optional[CapitalWeightedArmVerdict] = None
    cw_eligible = False
    try:
        cw_verdict = _compute_capital_weighted_verdict(production_rows)
        print(f"  equal_row_win_rate      = {_fmt_pct(cw_verdict.equal_row_win_rate)}")
        print(f"  equal_row_ev_sigma      = {_fmt_f(cw_verdict.equal_row_ev_sigma, '.2f')}")
        print(f"  capital_weighted_roi    = {_fmt_pct(cw_verdict.capital_weighted_roi)}")
        print(f"  capital_weighted_sigma  = {_fmt_f(cw_verdict.capital_weighted_ev_sigma, '.2f')}")
        print(f"  pooled n                = {cw_verdict.n}")
        print(
            f"  legacy/unproven excluded= {len(legacy_or_unproven_rows)} "
            "(diagnostic only; cannot license current regime)"
        )
        if cw_verdict.per_city_cw_roi:
            print("  per-city capital-weighted ROI:")
            for c in sorted(cw_verdict.per_city_cw_roi):
                print(f"    {c:<28s} cw_roi={_fmt_pct(cw_verdict.per_city_cw_roi[c])}  "
                      f"n={cw_verdict.per_city_n.get(c, 0)}")
        cw_eligible, cw_reason = _capital_weighted_arm_decision(cw_verdict)
        print(f"\n  ARM (capital-weighted): {'ELIGIBLE' if cw_eligible else 'DENIED/INSUFFICIENT'}")
        print(f"  Reason: {cw_reason}")
    except ValueError as exc:
        # Fail-closed: a sizeless settled row makes the verdict undeterminable.
        # cw_verdict stays None → artifact emission below also fails closed.
        print(f"  ARM (capital-weighted): DENIED — {exc}")

    # --- Equal-row verdict (continuity; NOT the arming decision) ---
    # PRODUCTION cohort (current q_source rows), per reference-only intent above.
    production_stats = _stats(production_rows, "CURRENT production pooled")
    arm_eligible, arm_reason = _arm_verdict(production_stats, production_rows)
    print("\n" + "=" * 78)
    print("EQUAL-ROW VERDICT (continuity only; thresholds: win_rate>51%, sigma>=2.0, n>=20, per-city n>=5)")
    print("=" * 78)
    verdict = "equal-row: ELIGIBLE" if arm_eligible else "equal-row: DENIED/INSUFFICIENT"
    print(f"  {verdict}")
    print(f"  Reason: {arm_reason}")
    print(f"  NOTE: the ARM DECISION is the CAPITAL-WEIGHTED verdict above, not this one.")
    print(f"        A high row-rate that loses money once sized does NOT arm.")
    print("=" * 78)

    # --- H3: emit the boot-gate artifact (the missing producer) ---
    if args.emit_artifact:
        print("\n" + "=" * 78)
        print(f"EMITTING ARM-GATE ARTIFACT → {args.emit_artifact}")
        print("=" * 78)
        if cw_verdict is None:
            # MISSING_SIZE fail-closed: we could not compute a capital-weighted
            # verdict, so we cannot honestly assert any EV. Emit a blocking
            # artifact (ev<=0, coverage_licensed False) so the consumer rejects,
            # rather than refusing to write (which would look like "no measurement").
            blocking = _compute_capital_weighted_verdict([])  # zero-verdict
            artifact = build_arm_artifact(blocking, [], argv=argv, coverage_licensed=False)
            artifact["capital_weighted_ev"] = -1.0  # explicit block on undeterminable size
        else:
            # coverage_licensed is ALWAYS False here: no settlement-calibrated
            # coverage license (K3) exists on this branch. The honest verdict on
            # current data is DENIED, so the artifact is BLOCKING by construction.
            # PRODUCTION cohort (current q_source rows) for date-coverage
            # evidence too — the artifact reflects what the current live
            # mechanism actually trades, not old unproven receipts and not the
            # mainstream-agreeing subset.
            artifact = build_arm_artifact(
                cw_verdict, production_rows, argv=argv, coverage_licensed=False
            )
        emit_arm_artifact(args.emit_artifact, artifact)
        blocks = (artifact["capital_weighted_ev"] <= 0.0) or (not artifact["coverage_licensed"])
        print(f"  schema               = {artifact['schema']}")
        print(f"  commit_sha           = {artifact['commit_sha']}")
        print(f"  measurement_cmd_hash = {artifact['measurement_cmd_hash'][:16]}…")
        print(f"  capital_weighted_ev  = {artifact['capital_weighted_ev']:.6f}")
        print(f"  ev_sigma             = {artifact['ev_sigma']:.4f}")
        print(f"  production_n         = {artifact['production_n']}")
        print(f"  gate_pass_n          = {artifact['gate_pass_n']} (deprecated alias)")
        print(f"  per_city_n           = {artifact['per_city_n']}")
        print(f"  date_coverage        = {artifact['date_coverage']['n_pairs']} (city,date) pairs")
        print(f"  coverage_licensed    = {artifact['coverage_licensed']}")
        print(
            f"\n  CONSUMER VERDICT: {'BLOCKING (boot gate REJECTS — arming stays blocked)' if blocks else 'NON-BLOCKING'}"
        )
        if blocks:
            print("  This is the HONEST DENIED state. Arming is correctly impossible.")
        else:
            # An eligible artifact must never be emitted on DENIED data — if we
            # reach here on the current cohort it is a producer fabrication bug.
            print("  WARNING: artifact is NON-BLOCKING — verify this is a genuine ARM_ELIGIBLE cohort.")
        print("=" * 78)


if __name__ == "__main__":
    main()
