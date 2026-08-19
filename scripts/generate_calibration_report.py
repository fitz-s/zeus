#!/usr/bin/env python3
# Lifecycle: created=2026-07-29; last_reviewed=2026-07-29; last_reused=2026-07-29
# Purpose: the repository's one performance claim its settled sample can actually
#   support is calibration, not returns. This regenerates a reliability diagram
#   (predicted probability vs settled frequency) plus its decomposition and cuts
#   from the SOLE settled-outcome ground truth: settlement_attribution
#   (decision-certificate x VERIFIED-settlement join — see
#   src/analysis/settlement_skill_attribution.py and loop/LEDGER.yaml's
#   "ground truth = decision certificate x real settlement join ONLY" rule).
# Reuse: READ-ONLY over zeus-world.db + zeus_trades.db (file:...?mode=ro).
#   Registered in SQLITE_CONNECT_ALLOWLIST (src/state/db_writer_lock.py).
#   Regenerate after new settlements land (settlement_skill_attribution grades
#   them into settlement_attribution first).
# Last reused/audited: 2026-07-29
# Authority basis: reference/AUDIT.md H2 (PnL scattered across ~20 files; the
#   sample is too small to support a return claim) + loop/LEDGER.yaml (settled-
#   only ground truth law, min_n=30 statistical-conclusion floor) + this
#   repo's documented look-ahead-contamination incident (a calibration store
#   built on settlement midpoints backfilled as forecasts) — this script reads
#   EXCLUSIVELY the settlement_attribution table, whose won/settled_in_bin/
#   settled_value columns are populated only by grade_receipt() against
#   VERIFIED settlement_outcomes, and whose q_live is the FROZEN decision-time
#   value from an immutable VERIFIED ActionableTradeCertificate — never a
#   value reconstructed or backfilled after the fact.
"""Generate docs/reference/calibration_report.md + docs/reference/calibration_reliability.svg.

Reports whether the system's stated win probability (q_live, frozen at decision
time on an immutable certificate) matches the settled frequency — the axis this
sample can actually support, unlike a return figure. Settled-only: reads
settlement_attribution exclusively (never an unsettled/synthetic outcome).

USAGE
    .venv/bin/python scripts/generate_calibration_report.py
    .venv/bin/python scripts/generate_calibration_report.py --stdout   # print report, write nothing

ENV
    ZEUS_MAIN_TREE   overrides the DB root (default ~/zeus); expects
                      $ZEUS_MAIN_TREE/state/{zeus-world.db,zeus_trades.db}.
"""
from __future__ import annotations

import argparse
import math
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

# ZEUS_MAIN_TREE overrides; default is ~/zeus, the operator's standard layout.
# expanduser() so ZEUS_MAIN_TREE=~/zeus (the shell does NOT expand '~' inside
# os.environ.get) resolves correctly instead of leaving a literal '~' segment.
_MAIN_TREE = os.path.expanduser(os.environ.get("ZEUS_MAIN_TREE") or "~/zeus")
WORLD_DB_DEFAULT = os.path.join(_MAIN_TREE, "state", "zeus-world.db")
TRADES_DB_DEFAULT = os.path.join(_MAIN_TREE, "state", "zeus_trades.db")
REPORT_OUT = "docs/reference/calibration_report.md"
SVG_OUT = "docs/reference/calibration_reliability.svg"

# loop/LEDGER.yaml rule: "Statistical conclusions require min_n=30 per cell
# before a status can move off 'open'" — the SAME floor is applied here as
# the thin-sample flag, rather than an invented threshold.
MIN_N = 30

SIX_CATEGORIES = (
    "SKILL_WIN",
    "LUCKY_WIN",
    "SKILL_LOSS",
    "MISCALIBRATED_LOSS",
    "STALE_DECISION",
    "UNATTRIBUTABLE_Q_MISSING",
)
# Categories whose outcome (won) is constant BY CONSTRUCTION (the category
# label is itself a function of won) — a within-category "win rate" for these
# is not a calibration statement, it is the category's own definition restated.
_OUTCOME_DEGENERATE_CATEGORIES = frozenset(
    {"SKILL_WIN", "LUCKY_WIN", "SKILL_LOSS", "MISCALIBRATED_LOSS"}
)

_ENTRY_EVENT_TYPES = ("POSITION_OPEN_INTENT", "ENTRY_ORDER_POSTED", "ENTRY_ORDER_FILLED")

LEAD_BUCKETS: list[tuple[float, float, str]] = [
    (0.0, 24.0, "<24h"),
    (24.0, 72.0, "24-72h (1-3d)"),
    (72.0, 168.0, "72-168h (3-7d)"),
    (168.0, math.inf, "168h+ (7d+)"),
]


# ---------------------------------------------------------------------------
# Read-only DB access
# ---------------------------------------------------------------------------

def ro_connect(world_db: str, trades_db: str) -> sqlite3.Connection:
    """WORLD as main (settlement_attribution lives there), trades ATTACHed
    read-only for the strategy_key / entry-time join (INV-37 pattern: single
    connection, no independent second connection)."""
    conn = sqlite3.connect(f"file:{world_db}?mode=ro", uri=True, timeout=15.0)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("ATTACH DATABASE ? AS trades", (f"file:{trades_db}?mode=ro",))
    return conn


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(str(ts)[:19])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hours_between(a: Optional[datetime], b: Optional[datetime]) -> Optional[float]:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    position_id: str
    category: str
    direction: Optional[str]
    won: bool
    q_live: Optional[float]
    settled_at: Optional[str]
    strategy_key: Optional[str]
    lead_hours: Optional[float]


def load_rows(conn: sqlite3.Connection) -> list[Row]:
    """Every settlement_attribution row (settled-only by construction — see
    module docstring), left-joined to trades.position_current for strategy_key,
    plus a decision-time lead-hours computed from trades.position_events'
    immutable entry timestamps (never position_current.updated_at, which is
    mutated by later projection writes).

    SETTLED-ONLY ASSERTION: every row is required to carry a non-NULL
    settled_at and a category in the six known values (the DB CHECK
    constraint already enforces the latter; re-asserted here in Python as
    defense-in-depth against a schema drift silently widening the source).
    """
    entry_at: dict[str, str] = {}
    placeholders = ",".join("?" for _ in _ENTRY_EVENT_TYPES)
    for position_id, occurred_at in conn.execute(
        f"""
        SELECT position_id, MIN(occurred_at)
        FROM trades.position_events
        WHERE event_type IN ({placeholders}) AND occurred_at IS NOT NULL
        GROUP BY position_id
        """,
        _ENTRY_EVENT_TYPES,
    ).fetchall():
        entry_at[str(position_id)] = str(occurred_at)

    out: list[Row] = []
    for position_id, category, direction, won, q_live, settled_at, strategy_key in conn.execute(
        """
        SELECT sa.position_id, sa.category, sa.direction, sa.won, sa.q_live,
               sa.settled_at, pc.strategy_key
        FROM settlement_attribution AS sa
        LEFT JOIN trades.position_current AS pc ON pc.position_id = sa.position_id
        WHERE sa.settled_at IS NOT NULL
        """
    ).fetchall():
        assert category in SIX_CATEGORIES, (
            f"settled-only assertion failed: unknown category {category!r} on "
            f"position {position_id!r} — settlement_attribution schema drift?"
        )
        dt_settled = _parse_ts(settled_at)
        dt_entry = _parse_ts(entry_at.get(str(position_id)))
        lead_hours = _hours_between(dt_entry, dt_settled)
        out.append(
            Row(
                position_id=str(position_id),
                category=category,
                direction=direction,
                won=bool(won),
                q_live=(float(q_live) if q_live is not None else None),
                settled_at=settled_at,
                strategy_key=strategy_key,
                lead_hours=lead_hours,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

_WILSON_Z_95 = 1.959963984540054


def wilson_interval(hits: int, n: int, z: float = _WILSON_Z_95) -> tuple[Optional[float], Optional[float]]:
    """Two-sided Wilson score interval for a binomial rate. (None, None) if n<=0."""
    if n <= 0:
        return (None, None)
    p_hat = hits / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p_hat + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n))
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return (lo, hi)


@dataclass(frozen=True)
class Bin:
    lo: float
    hi: float
    n: int
    wins: int
    obs_rate: float
    wilson_lo: Optional[float]
    wilson_hi: Optional[float]
    mean_pred: float


def reliability_bins(rows: list[Row], width: float = 0.1) -> list[Bin]:
    """Fixed-width bins over predicted probability [0,1]. Rows without a
    resolvable q_live are excluded upstream by the caller — never imputed."""
    buckets: dict[int, list[Row]] = {}
    n_edges = int(round(1.0 / width))
    for r in rows:
        assert r.q_live is not None
        # A tiny epsilon before the floor guards against float division landing
        # an exact bin-boundary value (e.g. 0.3/0.1 == 2.9999999999999996) one
        # bin low — a boundary value belongs in the upper (>=) half-open bin.
        idx = min(int(math.floor(r.q_live / width + 1e-9)), n_edges - 1)
        buckets.setdefault(idx, []).append(r)
    out: list[Bin] = []
    for idx in range(n_edges):
        items = buckets.get(idx, [])
        n = len(items)
        lo_edge, hi_edge = idx * width, (idx + 1) * width
        if n == 0:
            out.append(Bin(lo_edge, hi_edge, 0, 0, float("nan"), None, None, float("nan")))
            continue
        wins = sum(1 for r in items if r.won)
        obs_rate = wins / n
        wlo, whi = wilson_interval(wins, n)
        mean_pred = sum(r.q_live for r in items) / n
        out.append(Bin(lo_edge, hi_edge, n, wins, obs_rate, wlo, whi, mean_pred))
    return out


@dataclass(frozen=True)
class Decomposition:
    n: int
    base_rate: float
    brier: float
    reliability: float
    resolution: float
    uncertainty: float
    brier_skill_score: float


def decompose(rows: list[Row], bins: list[Bin]) -> Optional[Decomposition]:
    """Murphy (1973) two-term decomposition of the Brier score:
    Brier = reliability - resolution + uncertainty (bin-discretized reliability
    and resolution; a small residual against the exact per-observation Brier
    is expected — standard for the binned form). brier_skill_score = 1 -
    Brier/uncertainty: positive means the predicted probabilities beat always
    guessing the base rate; negative means they are worse than that baseline.
    """
    n = len(rows)
    if n == 0:
        return None
    wins = sum(1 for r in rows if r.won)
    base_rate = wins / n
    brier = sum((r.q_live - (1.0 if r.won else 0.0)) ** 2 for r in rows) / n
    reliability = 0.0
    resolution = 0.0
    for b in bins:
        if b.n == 0:
            continue
        reliability += b.n * (b.mean_pred - b.obs_rate) ** 2
        resolution += b.n * (b.obs_rate - base_rate) ** 2
    reliability /= n
    resolution /= n
    uncertainty = base_rate * (1.0 - base_rate)
    bss = (1.0 - brier / uncertainty) if uncertainty > 0 else float("nan")
    return Decomposition(n, base_rate, brier, reliability, resolution, uncertainty, bss)


@dataclass(frozen=True)
class CutRow:
    group: str
    n_total: int
    n_with_q: int
    mean_pred: Optional[float]
    win_rate: Optional[float]
    wilson_lo: Optional[float]
    wilson_hi: Optional[float]
    thin: bool


def cut_summary(rows: list[Row], key_fn: Callable[[Row], str], order: Optional[list[str]] = None) -> list[CutRow]:
    """Single-point calibration summary per group: mean predicted probability
    vs empirical win rate, BOTH computed on the same q_live-resolvable subset
    (never mixing a full-n win rate against a subset-mean prediction)."""
    groups: dict[str, list[Row]] = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    keys = order if order is not None else sorted(groups.keys())
    out: list[CutRow] = []
    for k in keys:
        items = groups.get(k, [])
        n_total = len(items)
        with_q = [r for r in items if r.q_live is not None]
        n_with_q = len(with_q)
        if n_with_q == 0:
            out.append(CutRow(k, n_total, 0, None, None, None, None, True))
            continue
        wins = sum(1 for r in with_q if r.won)
        win_rate = wins / n_with_q
        wlo, whi = wilson_interval(wins, n_with_q)
        mean_pred = sum(r.q_live for r in with_q) / n_with_q
        out.append(CutRow(k, n_total, n_with_q, mean_pred, win_rate, wlo, whi, n_with_q < MIN_N))
    return out


def lead_bucket_label(hours: Optional[float]) -> Optional[str]:
    if hours is None:
        return None
    for lo, hi, label in LEAD_BUCKETS:
        if lo <= hours < hi:
            return label
    return None


# ---------------------------------------------------------------------------
# SVG reliability diagram (dependency-free: no matplotlib/plotly export chain
# so the artifact regenerates identically in any environment that runs Python)
# ---------------------------------------------------------------------------

def render_svg(bins: list[Bin], *, width: int = 640, height: int = 640) -> str:
    margin = 64
    plot = width - 2 * margin

    def px(v: float) -> float:
        return margin + v * plot

    def py(v: float) -> float:
        return height - margin - v * plot

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Helvetica,Arial,sans-serif">'
    )
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="white"/>')

    # Gridlines + axis ticks at every 0.1.
    for i in range(0, 11):
        v = i / 10.0
        parts.append(
            f'<line x1="{px(v):.1f}" y1="{py(0):.1f}" x2="{px(v):.1f}" y2="{py(1):.1f}" '
            f'stroke="#e5e5e5" stroke-width="1"/>'
        )
        parts.append(
            f'<line x1="{px(0):.1f}" y1="{py(v):.1f}" x2="{px(1):.1f}" y2="{py(v):.1f}" '
            f'stroke="#e5e5e5" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{px(v):.1f}" y="{py(0) + 20:.1f}" font-size="11" fill="#333" '
            f'text-anchor="middle">{v:.1f}</text>'
        )
        parts.append(
            f'<text x="{px(0) - 10:.1f}" y="{py(v) + 4:.1f}" font-size="11" fill="#333" '
            f'text-anchor="end">{v:.1f}</text>'
        )

    # Axes.
    parts.append(
        f'<line x1="{px(0):.1f}" y1="{py(0):.1f}" x2="{px(1):.1f}" y2="{py(0):.1f}" '
        f'stroke="#333" stroke-width="1.5"/>'
    )
    parts.append(
        f'<line x1="{px(0):.1f}" y1="{py(0):.1f}" x2="{px(0):.1f}" y2="{py(1):.1f}" '
        f'stroke="#333" stroke-width="1.5"/>'
    )

    # Perfect-calibration diagonal.
    parts.append(
        f'<line x1="{px(0):.1f}" y1="{py(0):.1f}" x2="{px(1):.1f}" y2="{py(1):.1f}" '
        f'stroke="#999" stroke-width="1.5" stroke-dasharray="5,4"/>'
    )
    parts.append(
        f'<text x="{px(0.62):.1f}" y="{py(0.66):.1f}" font-size="11" fill="#999" '
        f'transform="rotate(-40 {px(0.62):.1f} {py(0.66):.1f})">perfect calibration</text>'
    )

    n_max = max((b.n for b in bins), default=1) or 1
    for b in bins:
        if b.n == 0:
            continue
        x = px(b.mean_pred)
        y = py(b.obs_rate)
        radius = 4.0 + 10.0 * math.sqrt(b.n / n_max)
        thin = b.n < MIN_N
        color = "#c0392b" if thin else "#2166ac"
        if b.wilson_lo is not None and b.wilson_hi is not None:
            y_lo = py(b.wilson_lo)
            y_hi = py(b.wilson_hi)
            parts.append(
                f'<line x1="{x:.1f}" y1="{y_lo:.1f}" x2="{x:.1f}" y2="{y_hi:.1f}" '
                f'stroke="{color}" stroke-width="1.5"/>'
            )
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" fill-opacity="0.75"/>')
        parts.append(
            f'<text x="{x + radius + 3:.1f}" y="{y - radius - 3:.1f}" font-size="10" fill="#333">n={b.n}</text>'
        )

    parts.append(
        f'<text x="{width/2:.1f}" y="{height - 18:.1f}" font-size="12" fill="#111" '
        f'text-anchor="middle">predicted probability (q_live, decision-time)</text>'
    )
    parts.append(
        f'<text x="18" y="{height/2:.1f}" font-size="12" fill="#111" text-anchor="middle" '
        f'transform="rotate(-90 18 {height/2:.1f})">settled frequency (observed win rate)</text>'
    )
    parts.append(
        f'<text x="{width/2:.1f}" y="24" font-size="13" fill="#111" text-anchor="middle">'
        f"Reliability diagram — settled positions, dot area ∝ bin n, red = n&lt;{MIN_N}</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _fmt(v: Optional[float], pct: bool = True, dp: int = 1) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v*100:.{dp}f}%" if pct else f"{v:.{dp+2}f}"


def _bin_table(bins: list[Bin]) -> str:
    lines = [
        "| bin | n | mean predicted | observed win rate | 95% Wilson interval | flag |",
        "|---|---:|---:|---:|---|---|",
    ]
    for b in bins:
        rng = f"[{b.lo:.1f}, {b.hi:.1f})"
        if b.n == 0:
            lines.append(f"| {rng} | 0 | n/a | n/a | n/a | empty |")
            continue
        ci = f"[{_fmt(b.wilson_lo)}, {_fmt(b.wilson_hi)}]"
        flag = f"thin (n<{MIN_N})" if b.n < MIN_N else ""
        lines.append(
            f"| {rng} | {b.n} | {_fmt(b.mean_pred)} | {_fmt(b.obs_rate)} ({b.wins}/{b.n}) | {ci} | {flag} |"
        )
    return "\n".join(lines)


def _cut_table(cut: list[CutRow], header: str) -> str:
    lines = [
        f"| {header} | n (settled) | n (predicted-prob resolvable) | mean predicted | observed win rate | 95% Wilson interval | flag |",
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for c in cut:
        if c.n_with_q == 0:
            lines.append(
                f"| {c.group} | {c.n_total} | 0 | n/a | n/a | n/a | no resolvable predicted probability |"
            )
            continue
        ci = f"[{_fmt(c.wilson_lo)}, {_fmt(c.wilson_hi)}]"
        flag = f"thin (n<{MIN_N})" if c.thin else ""
        lines.append(
            f"| {c.group} | {c.n_total} | {c.n_with_q} | {_fmt(c.mean_pred)} | {_fmt(c.win_rate)} | {ci} | {flag} |"
        )
    return "\n".join(lines)


def build_report(rows: list[Row], *, generated_at: str) -> str:
    q_rows = [r for r in rows if r.q_live is not None]
    n_total = len(rows)
    n_q = len(q_rows)
    settled_dates = sorted(r.settled_at for r in rows if r.settled_at)
    window = f"{settled_dates[0]} .. {settled_dates[-1]}" if settled_dates else "n/a"

    bins = reliability_bins(q_rows, width=0.1)
    decomp = decompose(q_rows, bins)

    by_side = cut_summary(rows, lambda r: r.direction or "unknown", order=["buy_yes", "buy_no", "unknown"])
    strategies = sorted({r.strategy_key or "unknown (no position_current match)" for r in rows})
    by_strategy = cut_summary(rows, lambda r: r.strategy_key or "unknown (no position_current match)", order=strategies)
    lead_rows = [r for r in rows if r.lead_hours is not None]
    n_lead_missing = n_total - len(lead_rows)
    by_lead = cut_summary(
        lead_rows, lambda r: lead_bucket_label(r.lead_hours) or "?", order=[b[2] for b in LEAD_BUCKETS]
    )
    by_category = cut_summary(rows, lambda r: r.category, order=list(SIX_CATEGORIES))

    # The "interesting cut": SKILL_WIN vs LUCKY_WIN predicted-probability
    # comparison (forecast-earned wins should carry HIGH predicted probability;
    # lucky wins are, by the taxonomy's own definition, wins the fresh evidence
    # disagreed with — reported here honestly whether or not the comparison is
    # actually computable in the current corpus).
    skill_win_c = next(c for c in by_category if c.group == "SKILL_WIN")
    lucky_win_c = next(c for c in by_category if c.group == "LUCKY_WIN")
    unattrib_c = next(c for c in by_category if c.group == "UNATTRIBUTABLE_Q_MISSING")

    out: list[str] = []
    out.append("# Zeus settled-position calibration report")
    out.append("")
    out.append(f"Generated: `{generated_at}`")
    out.append("Generator: `python3 scripts/generate_calibration_report.py`")
    out.append("")
    out.append(
        "> **What this is.** A reliability diagram — the system's stated win probability "
        "(`q_live`, frozen at decision time on an immutable `ActionableTradeCertificate`) "
        "against the settled frequency it actually produced. **What this is not: a return "
        "figure.** See the closing section for why."
    )
    out.append("")
    out.append("## Provenance — settled-only, no contamination path")
    out.append("")
    out.append(
        f"Every row in this report comes from `settlement_attribution` "
        f"(`src/analysis/settlement_skill_attribution.py`), the SOLE table whose `won` / "
        f"`settled_in_bin` / `settled_value` columns are populated by `grade_receipt()` "
        f"against a **VERIFIED** `settlement_outcomes` row, and whose `q_live` is the FROZEN "
        f"decision-time value read from an immutable VERIFIED decision certificate — never a "
        f"posterior reconstructed after the fact. This is the exact ground-truth law "
        f"`loop/LEDGER.yaml` states: *\"ground truth = decision certificate x real settlement "
        f"join ONLY\"*. This report never reads `forecast_posteriors` or any other table as if "
        f"it were a settled outcome — the failure mode of a prior calibration store built on "
        f"settlement midpoints backfilled as forecasts."
    )
    out.append("")
    out.append(
        f"- Settled positions loaded: **{n_total}**, settlement window `{window}`.\n"
        f"- Of those, **{n_q}** ({n_q/n_total*100:.1f}%) carry a resolvable decision-time "
        f"predicted probability (`q_live`); the remaining {n_total - n_q} have no resolvable "
        f"immutable decision-q certificate and are excluded from every calibration number "
        f"below (never imputed) — they are counted, not guessed.\n"
        f"- Thin-sample threshold: **n < {MIN_N}** per cell, `loop/LEDGER.yaml`'s own rule "
        f'("Statistical conclusions require min_n=30 per cell before a status can move off '
        f'\'open\'"). Every table below flags cells under that floor rather than hiding them.'
    )
    out.append("")
    out.append("## Reliability diagram")
    out.append("")
    out.append(f"![Reliability diagram]({os.path.basename(SVG_OUT)})")
    out.append("")
    out.append(f"n = {n_q} settled positions with a resolvable predicted probability. Dot area is "
                f"proportional to bin count; the vertical bar is the 95% Wilson interval on the "
                f"observed win rate; a red dot marks a bin under the n<{MIN_N} floor.")
    out.append("")
    out.append(_bin_table(bins))
    out.append("")
    out.append("## Decomposition — reliability vs resolution vs base rate")
    out.append("")
    if decomp is not None:
        out.append(
            f"Murphy (1973) two-term decomposition of the Brier score: "
            f"`Brier = reliability - resolution + uncertainty` (reliability/resolution computed "
            f"on the {len(bins)} bins above — an expected small residual against the exact "
            f"per-observation Brier is the discretization cost of binning)."
        )
        out.append("")
        out.append("| quantity | value | reads as |")
        out.append("|---|---:|---|")
        out.append(f"| n | {decomp.n} | settled positions with a resolvable predicted probability |")
        out.append(f"| base rate (uncertainty term) | {_fmt(decomp.base_rate)} | fraction of settled positions that won |")
        out.append(f"| reliability | {decomp.reliability:.4f} | miscalibration — **lower is better**, 0 = perfectly calibrated |")
        out.append(f"| resolution | {decomp.resolution:.4f} | informativeness — **higher is better**, 0 = no better than the base rate |")
        out.append(f"| uncertainty | {decomp.uncertainty:.4f} | irreducible variance of a coin at the base rate, `p(1-p)` |")
        out.append(f"| Brier score | {decomp.brier:.4f} | mean squared error of the predicted probability, lower is better |")
        out.append(
            f"| Brier skill score vs base rate | {decomp.brier_skill_score:+.3f} | "
            f"`1 - Brier/uncertainty` — positive beats always guessing the base rate, "
            f"negative is worse than that |"
        )
        out.append("")
        if decomp.brier_skill_score < 0:
            out.append(
                f"**The pooled Brier skill score is negative ({decomp.brier_skill_score:+.3f}).** "
                f"Across all {decomp.n} settled positions with a resolvable predicted probability "
                f"— including `STALE_DECISION` rows, whose decision posterior was, by definition, "
                f"already outdated — the stated probability is a worse predictor than simply "
                f"guessing the {_fmt(decomp.base_rate)} base rate. This is a real finding, not an "
                f"artifact of the calculation; see the attribution-class cut below for where it "
                f"concentrates."
            )
        else:
            out.append(
                f"**The pooled Brier skill score is positive ({decomp.brier_skill_score:+.3f})**: "
                f"the stated predicted probability beats guessing the {_fmt(decomp.base_rate)} "
                f"base rate across all {decomp.n} settled positions with a resolvable predicted "
                f"probability."
            )
    else:
        out.append("No rows with a resolvable predicted probability — decomposition unavailable.")
    out.append("")
    out.append("## Cut: by side")
    out.append("")
    out.append(_cut_table(by_side, "side"))
    out.append("")
    out.append("## Cut: by strategy")
    out.append("")
    out.append(_cut_table(by_strategy, "strategy_key"))
    out.append("")
    out.append("## Cut: by lead time (entry to settlement)")
    out.append("")
    out.append(
        f"{len(lead_rows)}/{n_total} settled positions have a resolvable entry timestamp "
        f"(`trades.position_events`, immutable append-only entry events); "
        f"{n_lead_missing} predate that event log and are excluded from this cut (counted, "
        f"not guessed)."
    )
    out.append("")
    out.append(_cut_table(by_lead, "lead time"))
    out.append("")
    out.append("## Cut: by attribution class — the interesting one")
    out.append("")
    out.append(
        "The six-class post-settlement grader (`settlement_skill_attribution.py`) separates "
        "forecast-earned wins from lucky ones specifically so only skill outcomes feed "
        "calibration. Four of the six categories are outcome-degenerate BY CONSTRUCTION "
        "(`SKILL_WIN`/`LUCKY_WIN` are defined as `won=1`, `SKILL_LOSS`/`MISCALIBRATED_LOSS` as "
        "`won=0`) — a within-category \"win rate\" for those four is the category's own "
        "definition restated, not a calibration statement. What IS comparable across them is "
        "the predicted probability itself: a forecast-earned win should carry a HIGH predicted "
        "probability; a lucky win, by the taxonomy's own definition, is one the fresh evidence "
        "disagreed with."
    )
    out.append("")
    out.append(_cut_table(by_category, "attribution class"))
    out.append("")
    out.append("### Skill vs lucky — reported either way")
    out.append("")
    if lucky_win_c.n_with_q == 0:
        out.append(
            f"**Cannot be computed from `q_live` in the current corpus.** `LUCKY_WIN` has "
            f"n={lucky_win_c.n_total} settled positions and **0** of them carry a resolvable "
            f"decision-q certificate — the comparison this cut exists to make (mean predicted "
            f"probability: forecast-earned wins vs lucky wins) is structurally unavailable, not "
            f"merely thin. `SKILL_WIN` (n={skill_win_c.n_total}, {skill_win_c.n_with_q} with a "
            f"resolvable `q_live`, mean predicted probability {_fmt(skill_win_c.mean_pred)}) has "
            f"no `LUCKY_WIN` counterpart to compare against today. This is itself the honest "
            f"finding: report it as unresolved rather than substitute a different sample."
        )
    else:
        out.append(
            f"`SKILL_WIN` mean predicted probability: {_fmt(skill_win_c.mean_pred)} "
            f"(n={skill_win_c.n_with_q}). `LUCKY_WIN` mean predicted probability: "
            f"{_fmt(lucky_win_c.mean_pred)} (n={lucky_win_c.n_with_q})."
        )
    out.append("")
    out.append(
        f"**`UNATTRIBUTABLE_Q_MISSING`** — n={unattrib_c.n_total} settled positions "
        f"({unattrib_c.n_total/n_total*100:.1f}% of the whole settled sample) have **no** "
        f"resolvable immutable decision-q certificate at all: the system's own decision-time "
        f"belief for roughly 1 in {round(n_total/max(unattrib_c.n_total,1))} settled trades is "
        f"unknown, not merely unplotted. That gap in certificate coverage is a data-completeness "
        f"finding in its own right, independent of what the calibration curve above shows."
    )
    out.append("")
    out.append("## What this supports / does not support")
    out.append("")
    out.append(f"- **n = {n_q}/{n_total}** settled positions carry a resolvable predicted probability; "
                f"every number above is scoped to that n, or to the smaller per-cut subsets shown "
                f"in their own columns.")
    out.append(f"- **Supports:** a calibration read on the pooled reliability curve and decomposition "
                f"above (n={n_q}), and on any cut cell not flagged thin (n≥{MIN_N}).")
    out.append(f"- **Does not support:** a return/PnL claim of any kind — see the capital-scale note "
                f"below; a `LUCKY_WIN` calibration comparison (n={lucky_win_c.n_total}, 0 resolvable); "
                f"a firm read on any cell flagged thin above; a claim that `UNATTRIBUTABLE_Q_MISSING` "
                f"positions were miscalibrated (their `q_live` is unknown, not zero or bad).")
    out.append(
        f"- **Capital scale** (stated once, here, nowhere else in this report): the settled "
        f"sample's total cost basis is a low four-figure dollar amount — far too small for the "
        f"standard error on any realized-return figure to be distinguishable from zero. This "
        f"report accordingly makes no return claim; capital-scale detail lives in the repo's "
        f"operational accounting, not here."
    )
    out.append("")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--world-db", default=WORLD_DB_DEFAULT, help="path to zeus-world.db")
    ap.add_argument("--trades-db", default=TRADES_DB_DEFAULT, help="path to zeus_trades.db")
    ap.add_argument("--stdout", action="store_true", help="print the report, write no files")
    args = ap.parse_args(argv)

    conn = ro_connect(args.world_db, args.trades_db)
    try:
        rows = load_rows(conn)
    finally:
        conn.close()

    if not rows:
        sys.stderr.write("generate_calibration_report: 0 settled positions loaded — nothing to report\n")
        return 1

    generated_at = datetime.now(timezone.utc).isoformat()
    q_rows = [r for r in rows if r.q_live is not None]
    bins = reliability_bins(q_rows, width=0.1)
    report = build_report(rows, generated_at=generated_at)
    svg = render_svg(bins)

    if args.stdout:
        sys.stdout.write(report)
        return 0

    with open(REPORT_OUT, "w") as fh:
        fh.write(report)
    with open(SVG_OUT, "w") as fh:
        fh.write(svg)
    sys.stdout.write(f"wrote {REPORT_OUT} ({len(report)} bytes) and {SVG_OUT} ({len(svg)} bytes)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
