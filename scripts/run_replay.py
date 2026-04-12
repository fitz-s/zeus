#!/usr/bin/env python3
"""Decision Replay Engine CLI.

Usage:
  # Audit: how would current logic have performed on historical data?
  .venv/bin/python scripts/run_replay.py --mode audit --start 2025-01-01 --end 2026-03-30

  # Counterfactual: what if London alpha was 0.70?
  .venv/bin/python scripts/run_replay.py --mode counterfactual --start 2025-06-01 --end 2025-09-01 \
    --override "alpha.London.JJA=0.70"

  # With multiple overrides:
  .venv/bin/python scripts/run_replay.py --mode counterfactual \
    --override "alpha.London.DJF=0.65" --override "alpha.London.JJA=0.70"
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.db import get_world_connection as get_connection, init_schema


def _parse_overrides(override_strs: list[str]) -> dict:
    """Parse override strings like 'alpha.London.JJA=0.70' into nested dict."""
    overrides = {}
    for s in override_strs:
        if "=" not in s:
            continue
        path, value = s.split("=", 1)
        parts = path.split(".")

        if parts[0] == "alpha" and len(parts) == 3:
            city, season = parts[1], parts[2]
            overrides.setdefault("alpha", {}).setdefault(city, {})[season] = float(value)
        else:
            print(f"Unknown override: {s}")

    return overrides


def _pnl_available(summary) -> bool:
    if summary.limitations.get("pnl_available") is False:
        return False
    if summary.limitations.get("pnl_requires_market_price_linkage"):
        return int(summary.limitations.get("market_price_linked_subjects") or 0) > 0
    return True


def _format_total_pnl(summary) -> str:
    if _pnl_available(summary):
        return f"${summary.replay_total_pnl:+.2f}"
    reason = str(summary.limitations.get("pnl_unavailable_reason") or "not_computed")
    if reason == "market_price_unavailable":
        unavailable = int(summary.limitations.get("market_price_unavailable_subjects") or 0)
        total = summary.n_replayed
        return f"N/A (market price unavailable for {unavailable}/{total} replayed subjects)"
    return f"N/A ({reason})"


def _fmt_metric(value, *, digits: int = 4) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return str(value)


def _fmt_count_rate(hits, total, rate) -> str:
    if total in (None, 0) or rate is None:
        return "N/A"
    return f"{hits}/{total} ({float(rate):.2%})"


def _print_wu_health_report(summary) -> None:
    skill = summary.limitations.get("forecast_skill") or {}
    print("Forecast skill (WU settlement truth, no PnL):")
    print(
        f"  forecast-bin rows: {skill.get('forecast_skill_rows', 0)} "
        f"yes={skill.get('actual_yes_rows', 0)} no={skill.get('actual_no_rows', 0)} "
        f"yes_rate={_fmt_metric(skill.get('positive_rate'))}"
    )
    print(
        "  threshold hit: "
        f"{_fmt_count_rate(skill.get('threshold_hits'), skill.get('threshold_total'), skill.get('accuracy_at_0_5'))} "
        f"majority_baseline={_fmt_metric(skill.get('majority_baseline_accuracy'))}"
    )
    print(
        "  positive predictions: "
        f"{skill.get('positive_prediction_hits', 0)}/{skill.get('positive_predictions', 0)} "
        f"precision={_fmt_metric(skill.get('positive_prediction_precision'))}"
    )
    print(
        "  top-bin hit: "
        f"{_fmt_count_rate(skill.get('top_bin_hits'), skill.get('top_bin_total'), skill.get('top_bin_accuracy'))} "
        "top-3 hit: "
        f"{_fmt_count_rate(skill.get('top3_bin_hits'), skill.get('top3_bin_total'), skill.get('top3_bin_accuracy'))}"
    )
    print(
        f"  brier={_fmt_metric(skill.get('brier'), digits=6)} "
        f"climatology={_fmt_metric(skill.get('climatology_brier'), digits=6)} "
        f"bss={_fmt_metric(skill.get('brier_skill_score_vs_climatology'), digits=6)}"
    )
    print(
        f"  log_loss={_fmt_metric(skill.get('log_loss'), digits=6)} "
        f"climatology={_fmt_metric(skill.get('climatology_log_loss'), digits=6)} "
        f"skill={_fmt_metric(skill.get('log_loss_skill_score_vs_climatology'), digits=6)}"
    )
    print(
        f"  mean_p_raw={_fmt_metric(skill.get('mean_p_raw'), digits=6)} "
        f"mean_p_on_yes={_fmt_metric(skill.get('mean_p_raw_on_actual_yes'), digits=6)} "
        f"mean_p_on_no={_fmt_metric(skill.get('mean_p_raw_on_actual_no'), digits=6)}"
    )
    group_integrity = skill.get("probability_group_integrity") or {}
    if group_integrity:
        print(
            "  probability groups: "
            f"valid={group_integrity.get('valid_probability_groups', 0)}/"
            f"{group_integrity.get('total_probability_groups', 0)} "
            f"invalid={group_integrity.get('invalid_probability_groups', 0)}"
        )
        reasons = group_integrity.get("invalid_probability_group_reasons") or {}
        if reasons:
            reason_text = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
            print(f"  invalid group reasons: {reason_text}")
        if not skill.get("primary_multiclass_metrics_interpretable", False):
            print("  WARNING: top-bin/top-3 metrics are calculated only on valid probability groups.")
        valid_skill = skill.get("valid_group_forecast_skill") or {}
        if valid_skill.get("forecast_skill_rows", 0):
            print(
                "  valid-group binary skill: "
                f"rows={valid_skill.get('forecast_skill_rows', 0)} "
                f"brier={_fmt_metric(valid_skill.get('brier'), digits=6)} "
                f"bss={_fmt_metric(valid_skill.get('brier_skill_score_vs_climatology'), digits=6)} "
                f"log_loss={_fmt_metric(valid_skill.get('log_loss'), digits=6)}"
            )
    print()

    buckets = skill.get("calibration_buckets") or []
    if buckets:
        print("Calibration buckets:")
        print(f"{'P_raw':>9} {'Rows':>7} {'MeanP':>8} {'Actual%':>8} {'Brier':>9}")
        print("-" * 45)
        for bucket in buckets:
            print(
                f"{bucket['bucket']:>9} {bucket['n']:>7} "
                f"{bucket['mean_p']:>8.4f} {bucket['actual_rate']:>8.2%} "
                f"{bucket['brier']:>9.6f}"
            )
        print()

    print(f"{'City':15} {'Dates':>6} {'Rows':>7} {'Yes%':>7} {'Brier':>10} {'BSS':>9} {'Top1':>11} {'Top3':>11}")
    print("-" * 86)
    for city_name in sorted(summary.per_city.keys()):
        stats = summary.per_city[city_name]
        top1 = _fmt_count_rate(
            stats.get("top_bin_hits"),
            stats.get("top_bin_total"),
            stats.get("top_bin_accuracy"),
        )
        top3 = _fmt_count_rate(
            stats.get("top3_bin_hits"),
            stats.get("top3_bin_total"),
            stats.get("top3_bin_accuracy"),
        )
        print(
            f"{city_name:15} {stats['n_dates']:>6} {stats.get('forecast_skill_rows', 0):>7} "
            f"{float(stats.get('positive_rate') or 0.0):>6.1%} "
            f"{_fmt_metric(stats.get('brier'), digits=6):>10} "
            f"{_fmt_metric(stats.get('brier_skill_score_vs_climatology'), digits=4):>9} "
            f"{top1:>11} {top3:>11}"
        )


def main():
    parser = argparse.ArgumentParser(description="Zeus Decision Replay Engine")
    parser.add_argument("--mode", choices=[
                            "audit",
                            "counterfactual",
                            "walk_forward",
                            "wu_settlement_sweep",
                            "trade_history_audit",
                        ],
                        default="audit", help="Replay mode")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--override", action="append", default=[],
                        help="Parameter override (e.g., 'alpha.London.JJA=0.70')")
    parser.add_argument(
        "--allow-snapshot-only-reference",
        action="store_true",
        help="Allow replay to use snapshot-only fallback references when no trade/decision_log reference exists.",
    )

    args = parser.parse_args()

    # Ensure schema is up to date
    conn = get_connection()
    init_schema(conn)
    conn.close()

    overrides = _parse_overrides(args.override) if args.override else None

    from src.engine.replay import run_replay

    print(f"\n{'='*80}")
    print(f"Decision Replay Engine — {args.mode.upper()}")
    print(f"Date range: {args.start} to {args.end}")
    if overrides:
        print(f"Overrides: {overrides}")
    print(f"{'='*80}\n")

    summary = run_replay(
        start_date=args.start,
        end_date=args.end,
        mode=args.mode,
        overrides=overrides,
        allow_snapshot_only_reference=args.allow_snapshot_only_reference,
    )

    # Report
    print(f"Run ID:       {summary.run_id}")
    print(f"Settlements:  {summary.n_settlements} total, {summary.n_replayed} replayed "
          f"({summary.coverage_pct}% coverage)")
    print(f"Would trade:  {summary.n_would_trade} / {summary.n_replayed}")
    print(f"Win rate:     {summary.replay_win_rate:.1%}")
    print(f"Total PnL:    {_format_total_pnl(summary)}")
    print()

    # Per-city breakdown
    if args.mode == "wu_settlement_sweep":
        _print_wu_health_report(summary)
    else:
        pnl_label = "PnL" if _pnl_available(summary) else "PnL*"
        print(f"{'City':15} {'Dates':>6} {'Trades':>7} {pnl_label:>10} {'Win%':>6}")
        print("-" * 48)
        for city_name in sorted(summary.per_city.keys()):
            stats = summary.per_city[city_name]
            pnl = f"${stats['total_pnl']:>+8.2f}" if _pnl_available(summary) else "N/A"
            print(f"{city_name:15} {stats['n_dates']:>6} {stats['n_trades']:>7} "
                  f"{pnl:>10} {stats['win_rate']:>5.1%}")
        if not _pnl_available(summary):
            print("* PnL is unavailable until replay subjects have decision-time market price linkage.")

    print()
    if args.mode in {"wu_settlement_sweep", "trade_history_audit"}:
        print(f"Results stored in zeus_backtest.db (run_id={summary.run_id})")
    else:
        print(f"Results stored in replay_results table (run_id={summary.run_id})")

    # Authority declaration — always shown
    print(f"\n{'='*80}")
    print("AUTHORITY: APPROXIMATE AUDIT ONLY — not promotion-eligible")
    print("Limitations:")
    for key, value in summary.limitations.items():
        flag = value if not isinstance(value, bool) else ("TRUE" if value else "FALSE")
        print(f"  {key}: {flag}")
    print(f"{'='*80}")

    # Show sample decisions for interesting outcomes
    interesting = [o for o in summary.outcomes if o.replay_would_trade][:5]
    if interesting:
        print(f"\n{'='*80}")
        print("Sample replay decisions (would-trade):")
        print(f"{'='*80}")
        for o in interesting:
            traded_decs = [d for d in o.replay_decisions if d.should_trade]
            for d in traded_decs:
                won = o.replay_pnl > 0
                print(f"  {o.city:12} {o.target_date} {d.range_label[:30]:30} "
                      f"{d.direction:8} edge={d.edge:+.3f} p_post={d.p_posterior:.3f} "
                      f"{'✅' if won else '❌'} PnL=${o.replay_pnl:+.2f}")


if __name__ == "__main__":
    main()
