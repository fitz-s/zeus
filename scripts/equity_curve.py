#!/usr/bin/env python3
# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Build a live-runtime equity curve from current truth files.
# Reuse: Run after live-only truth-file helper changes or equity report routing changes.
"""Build a mode-aware equity curve from current truth files."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
except ModuleNotFoundError:
    matplotlib = None
    mdates = None
    plt = None

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.portfolio import (
    CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION,
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
    FILL_AUTHORITY_CANCELLED_REMAINDER,
    FILL_AUTHORITY_SETTLED,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
)
from src.state.truth_files import current_mode, read_mode_truth_json


OUT = PROJECT_ROOT / "equity_curve.png"
LEGACY_DIAGNOSTIC_COHORT = "legacy_diagnostic"
_FILL_GRADE_AUTHORITIES = frozenset({
    FILL_AUTHORITY_VENUE_CONFIRMED_PARTIAL,
    FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
    FILL_AUTHORITY_CANCELLED_REMAINDER,
    FILL_AUTHORITY_SETTLED,
})
_ENTRY_GRADE_AUTHORITIES = frozenset({
    ENTRY_ECONOMICS_AVG_FILL_PRICE,
    ENTRY_ECONOMICS_CORRECTED_COST_BASIS,
})


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _positive_number(value) -> bool:
    try:
        return float(value or 0.0) > 0.0
    except (TypeError, ValueError):
        return False


def _exit_economics_cohort(row: dict) -> str:
    corrected = (
        row.get("pricing_semantics_version")
        == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION
        or row.get("corrected_executable_economics_eligible") is True
    )
    if not corrected:
        return LEGACY_DIAGNOSTIC_COHORT
    ready = (
        row.get("entry_economics_authority") in _ENTRY_GRADE_AUTHORITIES
        and row.get("fill_authority") in _FILL_GRADE_AUTHORITIES
        and _positive_number(row.get("shares_filled"))
        and _positive_number(row.get("filled_cost_basis_usd"))
        and (
            bool(row.get("entry_cost_basis_hash"))
            or bool(row.get("execution_cost_basis_version"))
        )
    )
    if not ready:
        raise ValueError("corrected equity row is missing fill/cost-basis authority")
    return CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION


def _single_exit_economics_cohort(rows: list[dict]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        cohort = _exit_economics_cohort(row)
        counts[cohort] = counts.get(cohort, 0) + 1
    if len(counts) > 1:
        raise ValueError(
            "mixed pricing semantics cohorts are forbidden in equity curve: "
            f"{sorted(counts)}"
        )
    return next(iter(counts), LEGACY_DIAGNOSTIC_COHORT), counts


def build_equity_curve(mode: str) -> dict:
    status, status_truth = read_mode_truth_json("status_summary.json", mode=mode)
    portfolio, portfolio_truth = read_mode_truth_json("positions.json", mode=mode)
    _tracker, tracker_truth = read_mode_truth_json("strategy_tracker.json", mode=mode)

    initial = float(status["portfolio"]["initial_bankroll"])
    realized_now = float(status["portfolio"]["realized_pnl"])
    unrealized_now = float(status["portfolio"]["unrealized_pnl"])
    total_now = float(status["portfolio"]["effective_bankroll"])

    recent_exits = sorted(
        [
            row for row in portfolio.get("recent_exits", [])
            if not str(row.get("market_id", "")).startswith("mock_")
        ],
        key=lambda row: row.get("exited_at", ""),
    )
    economics_cohort, economics_cohort_counts = _single_exit_economics_cohort(recent_exits)
    timestamps = [
        dt for dt in (
            _parse_ts(row.get("entered_at")) for row in portfolio.get("positions", [])
        )
        if dt is not None
    ]
    timestamps.extend(
        dt for dt in (_parse_ts(row.get("exited_at")) for row in recent_exits) if dt is not None
    )
    generated_at = _parse_ts(status_truth.get("generated_at")) or datetime.now(timezone.utc)
    start_dt = min(timestamps) if timestamps else generated_at

    events: list[tuple[datetime, float, str]] = [(start_dt, initial, "Start")]
    running_realized = 0.0
    for exit_row in recent_exits:
        ts = _parse_ts(exit_row.get("exited_at"))
        if ts is None:
            continue
        pnl = float(exit_row.get("pnl", 0.0) or 0.0)
        running_realized += pnl
        label = (
            f"{exit_row.get('city', '?')} {exit_row.get('bin_label', '')} "
            f"{exit_row.get('direction', '')} {pnl:+.2f}"
        ).strip()
        events.append((ts, initial + running_realized, label))

    events.append((generated_at, total_now, f"Current {total_now:+.2f}"))
    events.sort(key=lambda row: row[0])

    return_pct = (total_now / initial - 1.0) * 100.0 if initial > 0 else 0.0
    report = {
        "mode": mode,
        "initial_bankroll": initial,
        "realized_pnl": realized_now,
        "unrealized_pnl": unrealized_now,
        "total_pnl": total_now - initial,
        "bankroll": total_now,
        "return_pct": round(return_pct, 2),
        "status_truth": status_truth,
        "portfolio_truth": portfolio_truth,
        "tracker_truth": tracker_truth,
        "output_path": str(OUT) if plt is not None else None,
        "n_realized_events": len(recent_exits),
        "economics_cohort": economics_cohort,
        "economics_cohort_counts": economics_cohort_counts,
        "promotion_grade": economics_cohort == CORRECTED_EXECUTABLE_PRICING_SEMANTICS_VERSION,
        "matplotlib_available": plt is not None,
    }
    if plt is None:
        return report

    fig, ax = plt.subplots(figsize=(13, 7))
    times = [row[0] for row in events]
    equity = [row[1] for row in events]

    ax.step(times, equity, where="post", color="#00D4AA", linewidth=2.5)
    ax.fill_between(times, initial, equity, step="post", alpha=0.12, color="#00D4AA")
    ax.axhline(initial, color="red", linewidth=1.2, linestyle="--", alpha=0.6, label=f"Initial ${initial:.2f}")
    ax.axhline(initial + realized_now, color="steelblue", linewidth=1.2, linestyle=":", alpha=0.7, label=f"Realized ${initial + realized_now:.2f}")
    ax.axhline(total_now, color="#00D4AA", linewidth=1.2, linestyle="-", alpha=0.8, label=f"Total ${total_now:.2f}")

    ax.set_title(f"Zeus {mode} Equity Curve", fontsize=14, fontweight="bold")
    ax.set_ylabel("Bankroll (USD)", fontsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.xticks(rotation=30, fontsize=8)
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper left", fontsize=9)

    summary = (
        f"env={mode} | generated_at={status_truth.get('generated_at')} | "
        f"status_source={status_truth.get('source_path')} | "
        f"status_stale_age_seconds={status_truth.get('stale_age_seconds')} | "
        f"realized=${realized_now:.2f} | unrealized=${unrealized_now:.2f} | "
        f"total=${total_now:.2f} | return={return_pct:+.1f}%"
    )
    ax.text(0.5, -0.16, summary, transform=ax.transAxes, ha="center", fontsize=9, color="dimgray")

    plt.tight_layout()
    plt.savefig(str(OUT), dpi=150, bbox_inches="tight")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default=None)
    args = parser.parse_args()

    mode = current_mode(args.mode)
    report = build_equity_curve(mode)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
