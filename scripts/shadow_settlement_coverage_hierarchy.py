#!/usr/bin/env python3
# Lifecycle: created=2026-07-19; last_reviewed=2026-07-19; last_reused=never
# Authority basis: docs/evidence/capital_efficiency_2026_07_19/highq_overconfidence.md
#   Sec 5 (fix spec, prerequisite #2) + src/calibration/settlement_coverage_hierarchy.py
#   (F1 walk-forward hierarchical coverage calibrator) + src/engine/event_reactor_adapter.py
#   ::_hierarchy_observations_all (money-path observation pool, now merged with
#   entered-position walk-forward outcomes from position_current).
# Purpose: READ-ONLY shadow-test harness. Runs hierarchical_coverage_check over the
#   last N settled/economically_closed decisions using the EXTENDED observation
#   pool (no-submit receipts + entered positions) and prints a per-q-bucket
#   coverage-verdict report, so the operator can decide whether to arm
#   feature_flags.settlement_coverage_hierarchy_enabled. Does NOT flip the flag,
#   does NOT write to any DB, does NOT touch the live Kelly/admission path.
"""Shadow-test harness for the F1 hierarchical settlement-coverage calibrator.

For each of the last N settled/economically_closed positions (most recent
first), rebuilds the decision AS IT WOULD HAVE BEEN EVALUATED walk-forward
(only observations settled strictly before that decision's own time are
visible to it -- ``filter_observations_prefix``), then runs
``hierarchical_coverage_check`` and buckets the resulting executable-pair
verdict by the position's own raw claimed q. This answers "if the flag were
armed today, what would it have done to entries in each q-bucket" WITHOUT
armed it and WITHOUT writing anything.

Caveat (explicit, not hidden): ``q_lcb_raw`` at true decision time is not
durably stored on ``position_current`` (that value lives transiently in the
receipt/execution path). This script uses ``q_raw`` as a same-value proxy for
``q_lcb_raw`` -- harmless for this report because ``hierarchical_coverage_check``'s
STATUS/LEVEL decision depends only on the observation pool + ``q_raw``'s
0.05-bucket, never on ``q_lcb_raw`` (see that function + ``_apply`` in
settlement_coverage_hierarchy.py). Only the printed "would-be q_lcb_exec"
column carries this proxy's imprecision.

Usage:
    python3 scripts/shadow_settlement_coverage_hierarchy.py [--limit N]

Read-only: opens zeus-forecasts.db and zeus_trades.db via the repo's
``get_*_connection_read_only`` helpers. No writes, no flag flip.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.calibration.settlement_coverage_hierarchy import (  # noqa: E402
    filter_observations_prefix,
    hierarchical_coverage_check,
    q_bucket_key,
)
from src.engine.event_reactor_adapter import (  # noqa: E402
    _coverage_band_template,
    _hierarchy_observations_all,
)
from src.state.db import (  # noqa: E402
    get_forecasts_connection_read_only,
    get_trade_connection_read_only,
)

_DEFAULT_LIMIT = 500


def _recent_settled_decisions(trade_conn, *, limit: int) -> list[dict]:
    """Last ``limit`` settled/economically_closed decisions, most recent first.

    Same fail-soft, same exclusion predicate as
    ``era._position_hierarchy_claims`` -- ``entry_method =
    'chain_only_reconciliation'`` (foreign co-trading, no Zeus decision
    evidence) is excluded here too, for consistency with what the money-path
    pool itself would ingest.
    """
    rows = trade_conn.execute(
        """
        SELECT pc.condition_id, pc.city, pc.temperature_metric, pc.target_date,
               pc.bin_label, pc.direction, pc.strategy_key, pc.p_posterior,
               COALESCE(of.entered_at, pc.updated_at) AS decision_time
        FROM position_current pc
        LEFT JOIN outcome_fact of ON of.position_id = pc.position_id
        WHERE pc.phase IN ('settled', 'economically_closed')
          AND pc.p_posterior IS NOT NULL AND pc.p_posterior > 0
          AND pc.condition_id IS NOT NULL AND pc.condition_id != ''
          AND (pc.entry_method IS NULL OR pc.entry_method != 'chain_only_reconciliation')
        ORDER BY decision_time DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    decisions: list[dict] = []
    for (
        condition_id, city, metric, target_date, bin_label, direction,
        strategy_key, p_posterior, decision_time,
    ) in rows:
        band_template = _coverage_band_template(bin_label)
        if not band_template:
            continue
        try:
            q_raw = float(p_posterior)
        except (TypeError, ValueError):
            continue
        decisions.append(
            {
                "condition_id": str(condition_id or ""),
                "city": str(city or ""),
                "metric": str(metric or "").lower(),
                "target_date": str(target_date or ""),
                "band_template": band_template,
                "direction": str(direction or ""),
                "strategy_key": strategy_key,
                "q_raw": q_raw,
                "decision_time": str(decision_time or ""),
            }
        )
    return decisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=_DEFAULT_LIMIT,
        help=f"number of most-recent settled decisions to shadow-test (default {_DEFAULT_LIMIT})",
    )
    args = parser.parse_args()

    forecasts_conn = get_forecasts_connection_read_only()
    trade_conn = get_trade_connection_read_only()

    try:
        # Merged observation pool: no-submit receipts (world.db) + entered-
        # position walk-forward outcomes (position_current, zeus_trades.db).
        # market_events + settlement_outcomes both live in zeus-forecasts.db.
        all_observations = _hierarchy_observations_all(
            forecast_conn=forecasts_conn,
            topology_conn=forecasts_conn,
            coverage_cache=None,
            fail_closed_on_fault=True,
        )
    except ValueError as exc:
        print(f"FAULT building observation pool (fail-closed): {exc}", file=sys.stderr)
        return 1

    decisions = _recent_settled_decisions(trade_conn, limit=args.limit)

    print(f"Observation pool: {len(all_observations)} graded observations (receipts + entered positions)")
    print(f"Shadow-testing {len(decisions)} settled/economically_closed decisions\n")

    if not decisions:
        print("No eligible settled decisions found -- nothing to shadow-test.")
        return 0

    def _run(as_of_override: str | None) -> dict[str, list]:
        """Bucket verdicts for every decision. ``as_of_override`` replaces each
        decision's own historical decision_time with a fixed instant (used for
        the AS-OF-NOW projection below); ``None`` replays true walk-forward."""
        buckets: dict[str, list] = defaultdict(list)
        for d in decisions:
            as_of = as_of_override or d["decision_time"]
            prefix_obs = filter_observations_prefix(all_observations, as_of)
            pair = hierarchical_coverage_check(
                city=d["city"],
                metric=d["metric"],
                band_template=d["band_template"],
                direction=d["direction"],
                strategy_key=d["strategy_key"],
                q_raw=d["q_raw"],
                q_lcb_raw=d["q_raw"],  # proxy -- see module docstring caveat
                observations=prefix_obs,
            )
            buckets[q_bucket_key(d["q_raw"])].append(pair)
        return buckets

    def _print_report(title: str, by_bucket: dict[str, list]) -> None:
        print(f"\n=== {title} ===")
        print(f"{'bucket':<12}{'n':>6}{'INSUFF':>9}{'LICENSED':>10}{'UNLICENSED':>12}  "
              f"{'levels fired (UNLICENSED/LICENSED)':<45}{'avg q_raw':>10}{'avg q_exec':>11}")
        print("-" * 116)
        for bucket in sorted(by_bucket, key=lambda b: float(b.split("-")[0])):
            pairs = by_bucket[bucket]
            n = len(pairs)
            status_counts = Counter(p.status for p in pairs)
            level_counts = Counter(p.level for p in pairs if p.status != "INSUFFICIENT_DATA")
            levels_str = ", ".join(f"{lvl}:{cnt}" for lvl, cnt in level_counts.most_common()) or "-"
            avg_q_raw = sum(p.q_raw for p in pairs) / n
            avg_q_exec = sum(p.q_exec for p in pairs) / n
            print(
                f"{bucket:<12}{n:>6}{status_counts['INSUFFICIENT_DATA']:>9}"
                f"{status_counts['LICENSED']:>10}{status_counts['UNLICENSED']:>12}  "
                f"{levels_str:<45}{avg_q_raw:>10.3f}{avg_q_exec:>11.3f}"
            )

        hi_pairs = [p for b, ps in by_bucket.items() if 0.80 <= float(b.split("-")[0]) < 0.95 for p in ps]
        print("High-confidence band (0.80-0.95) summary:")
        if hi_pairs:
            n = len(hi_pairs)
            status_counts = Counter(p.status for p in hi_pairs)
            avg_q_raw = sum(p.q_raw for p in hi_pairs) / n
            avg_q_exec = sum(p.q_exec for p in hi_pairs) / n
            print(
                f"  n={n} INSUFFICIENT_DATA={status_counts['INSUFFICIENT_DATA']} "
                f"LICENSED={status_counts['LICENSED']} UNLICENSED={status_counts['UNLICENSED']} "
                f"avg_q_raw={avg_q_raw:.3f} avg_would_be_q_exec={avg_q_exec:.3f}"
            )
        else:
            print("  no decisions in this band")

    # (1) TRUE WALK-FORWARD REPLAY: each decision only sees observations settled
    # strictly before ITS OWN decision_time -- exactly what arming the flag
    # would have produced had it been on at the time. Expect heavy
    # INSUFFICIENT_DATA on a young / recently-settled dataset (cold start:
    # there simply isn't much prior settled history yet for early decisions
    # to pool against) -- this is the module's walk-forward contract working
    # correctly, not a defect.
    _print_report("Walk-forward replay (per-decision, as it would have run live)", _run(None))

    # (2) AS-OF-NOW PROJECTION: every decision's OWN (city, metric, band,
    # direction, strategy, q) re-evaluated against the FULL current pool as of
    # right now -- this answers the operator's actual arming question: "what
    # would today's already-accumulated history do to a decision at this
    # q/strategy/direction if the flag were armed going forward."
    now_iso = datetime.now(timezone.utc).isoformat()
    _print_report(f"As-of-now projection (full current pool, as_of={now_iso})", _run(now_iso))

    return 0


if __name__ == "__main__":
    sys.exit(main())
