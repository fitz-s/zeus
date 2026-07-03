#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Read-only executable q_lcb replay over local posterior/book/settlement state.
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import get_forecasts_connection_read_only, get_trade_connection_read_only
from src.strategy.live_inference.source_clock_vnext import (
    no_side_lcb_from_yes_ucb,
    optimal_binary_log_growth,
    source_age_bucket_from_minutes,
)

FORECASTS_DB = ROOT / "state" / "zeus-forecasts.db"
TRADES_DB = ROOT / "state" / "zeus_trades.db"


def dt(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def lead_days(computed_at: str, target_date: str) -> int:
    return max(0, (datetime.fromisoformat(target_date).date() - dt(computed_at).date()).days)


def source_age_bucket(computed_at: str, source_available_at: str) -> str:
    delay_min = (dt(computed_at) - dt(source_available_at)).total_seconds() / 60.0
    return source_age_bucket_from_minutes(delay_min)


def fee_rate(snapshot_fee_json: str, model: str) -> float:
    if model == "polymarket_weather":
        fallback = 0.05
    else:
        fallback = 0.0
    try:
        data = json.loads(snapshot_fee_json or "{}")
        return float(data.get("fee_rate_fraction", fallback))
    except Exception:
        return fallback


def as_float(value: object) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def vwap_to_size(depth_json: str, fallback_ask: float, size: float) -> tuple[float | None, float]:
    try:
        data = json.loads(depth_json or "{}")
        asks = sorted(
            [(float(x["price"]), float(x["size"])) for x in data.get("asks", [])],
            key=lambda x: x[0],
        )
    except Exception:
        asks = []
    if not asks and fallback_ask > 0:
        asks = [(fallback_ask, size)]
    remaining = float(size)
    cost = 0.0
    filled = 0.0
    for price, qty in asks:
        take = min(remaining, max(qty, 0.0))
        if take <= 0:
            continue
        cost += price * take
        filled += take
        remaining -= take
        if remaining <= 1e-9:
            break
    if filled + 1e-9 < size:
        return None, filled
    return cost / size, filled


def contains(row: pd.Series, settled: float) -> bool:
    lo = row.get("range_low")
    hi = row.get("range_high")
    lo_v = -10**9 if pd.isna(lo) else float(lo)
    hi_v = 10**9 if pd.isna(hi) else float(hi)
    return lo_v <= settled <= hi_v


def load_beliefs() -> pd.DataFrame:
    con = get_forecasts_connection_read_only()
    sql = """
    SELECT
      fp.posterior_id, fp.product_id, fp.city, fp.target_date,
      fp.temperature_metric AS metric, fp.source_id, fp.source_available_at, fp.computed_at,
      fp.q_json, fp.q_lcb_json, fp.q_ucb_json,
      me.market_slug, me.condition_id, me.token_id AS yes_token_id,
      me.range_label, me.range_low, me.range_high,
      so.settlement_value, so.authority
    FROM forecast_posteriors fp
    JOIN market_events me
      ON me.city=fp.city AND me.target_date=fp.target_date
     AND me.temperature_metric=fp.temperature_metric
    LEFT JOIN settlement_outcomes so
      ON so.city=fp.city AND so.target_date=fp.target_date
     AND so.temperature_metric=fp.temperature_metric
    WHERE fp.runtime_layer='live'
    """
    rows = pd.read_sql_query(sql, con)
    con.close()
    out = []
    for _, r in rows.iterrows():
        q = json.loads(r["q_json"] or "{}")
        ql = json.loads(r["q_lcb_json"] or "{}")
        qu = json.loads(r["q_ucb_json"] or "{}")
        label = r["range_label"]
        if label not in q:
            continue
        q_yes = float(q[label])
        ql_yes = float(ql.get(label, np.nan))
        qu_yes = float(qu.get(label, np.nan))
        base = r.drop(labels=["q_json", "q_lcb_json", "q_ucb_json"]).to_dict()
        base.update({"q_point_yes": q_yes, "q_lcb_yes": ql_yes, "q_ucb_yes": qu_yes})
        out.append(base)
    return pd.DataFrame(out)


def nearest_snapshot(cur: sqlite3.Cursor, token_id: str, ts: str) -> dict | None:
    row = cur.execute(
        """
        SELECT selected_outcome_token_id, outcome_label, orderbook_top_ask, orderbook_top_bid,
               min_tick_size, fee_details_json, orderbook_depth_json, captured_at
        FROM executable_market_snapshots
        WHERE selected_outcome_token_id=? AND captured_at<=?
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (str(token_id), str(ts)),
    ).fetchone()
    if not row:
        return None
    keys = [
        "selected_outcome_token_id",
        "outcome_label",
        "ask",
        "bid",
        "tick",
        "fee_json",
        "depth_json",
        "book_ts",
    ]
    return dict(zip(keys, row))


def replay(
    min_edges: list[float],
    sizes: list[float],
    fee_model: str,
    *,
    admission_objective: str = "log_utility",
) -> tuple[pd.DataFrame, dict]:
    beliefs = load_beliefs()
    tr = get_trade_connection_read_only()
    cur = tr.cursor()
    trades = []
    candidates = 0
    missing_book = 0
    for _, r in beliefs.iterrows():
        settled_known = r.get("authority") == "VERIFIED" and not pd.isna(r.get("settlement_value"))
        yes_hit = contains(r, float(r["settlement_value"])) if settled_known else np.nan
        sides = [
            (
                "YES",
                str(r["yes_token_id"]),
                float(r["q_point_yes"]),
                float(r["q_lcb_yes"]),
                bool(yes_hit) if settled_known else np.nan,
            )
        ]
        q_ucb_yes = r.get("q_ucb_yes")
        if not pd.isna(q_ucb_yes):
            snap_for_no = cur.execute(
                """
                SELECT no_token_id FROM executable_market_snapshots
                WHERE yes_token_id=? AND captured_at<=?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (str(r["yes_token_id"]), str(r["computed_at"])),
            ).fetchone()
            if snap_for_no:
                sides.append(
                    (
                        "NO",
                        str(snap_for_no[0]),
                        max(0.0, 1.0 - float(r["q_point_yes"])),
                        no_side_lcb_from_yes_ucb(float(q_ucb_yes)),
                        (not yes_hit) if settled_known else np.nan,
                    )
                )
        for side, token, q_point, q_lcb, hit in sides:
            if not math.isfinite(q_lcb):
                continue
            candidates += 1
            snap = nearest_snapshot(cur, token, str(r["computed_at"]))
            if not snap:
                missing_book += 1
                continue
            ask_v = as_float(snap["ask"])
            if ask_v is None:
                missing_book += 1
                continue
            ask = ask_v
            rate = fee_rate(snap["fee_json"], fee_model)
            tick = float(snap["tick"] or 0.01)
            for size in sizes:
                vwap, filled = vwap_to_size(snap["depth_json"], ask, float(size))
                if vwap is None:
                    continue
                fee = rate * vwap * (1.0 - vwap)
                executable_cost = vwap + fee
                edge_lcb = q_lcb - executable_cost
                kelly_f, log_growth_lcb = optimal_binary_log_growth(q_lcb, executable_cost)
                if admission_objective == "log_utility":
                    if log_growth_lcb <= 0.0 or kelly_f <= 0.0:
                        continue
                    objective_edges: list[float | None] = [None]
                else:
                    objective_edges = list(min_edges)
                for min_edge in objective_edges:
                    if admission_objective == "edge_lcb" and min_edge is not None and edge_lcb < min_edge:
                        continue
                    pnl = np.nan if pd.isna(hit) else (1.0 if hit else 0.0) - executable_cost
                    trades.append(
                        {
                            "posterior_id": r["posterior_id"],
                            "city": r["city"],
                            "metric": r["metric"],
                            "target_date": r["target_date"],
                            "lead_days": lead_days(r["computed_at"], r["target_date"]),
                            "market_slug": r["market_slug"],
                            "condition_id": r["condition_id"],
                            "token_id": token,
                            "side": side,
                            "book_ts": snap["book_ts"],
                            "computed_at": r["computed_at"],
                            "source_available_at": r["source_available_at"],
                            "source_age_bucket": source_age_bucket(r["computed_at"], r["source_available_at"]),
                            "size": size,
                            "min_edge": min_edge,
                            "admission_objective": admission_objective,
                            "tick_size": tick,
                            "q_point": q_point,
                            "q_lcb": q_lcb,
                            "vwap": vwap,
                            "fee": fee,
                            "executable_cost": executable_cost,
                            "edge_lcb": edge_lcb,
                            "kelly_spend_fraction_lcb": kelly_f,
                            "expected_log_growth_lcb": log_growth_lcb,
                            "settled": bool(settled_known),
                            "hit": hit,
                            "pnl_per_share": pnl,
                        }
                    )
    tr.close()
    df = pd.DataFrame(trades)
    summary = {
        "admission_objective": admission_objective,
        "belief_side_candidates": int(candidates),
        "missing_book_candidates": int(missing_book),
        "replay_rows": int(len(df)),
    }
    if not df.empty:
        settled = df[df["settled"]].copy()
        summary.update(
            {
                "settled_rows": int(len(settled)),
                "mean_edge_lcb": float(df["edge_lcb"].mean()),
                "mean_expected_log_growth_lcb": float(df["expected_log_growth_lcb"].mean()),
                "positive_log_utility_after_cost": bool(float(df["expected_log_growth_lcb"].mean()) > 0.0),
                "mean_pnl_per_share_settled": float(settled["pnl_per_share"].mean()) if len(settled) else None,
            }
        )
        by_size = settled.groupby("size")["pnl_per_share"].mean().reset_index() if len(settled) else pd.DataFrame()
        summary["monotone_degradation_with_larger_size"] = bool(
            len(by_size) < 2 or all(np.diff(by_size["pnl_per_share"].to_numpy()) <= 1e-9)
        )
    return df, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beliefs")
    ap.add_argument("--books")
    ap.add_argument("--fee-model", default="polymarket_weather")
    ap.add_argument("--min-edge-grid", default="0.03")
    ap.add_argument("--admission-objective", choices=["log_utility", "edge_lcb"], default="log_utility")
    ap.add_argument("--sizes", default="5,10,25,50,100")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    min_edges = [float(x) for x in args.min_edge_grid.split(",") if x]
    sizes = [float(x) for x in args.sizes.split(",") if x]
    df, summary = replay(
        min_edges,
        sizes,
        args.fee_model,
        admission_objective=args.admission_objective,
    )
    df.to_csv(out / "executable_q_lcb_replay.csv", index=False)
    if not df.empty:
        df.groupby(["admission_objective", "min_edge", "size", "settled"], dropna=False).agg(
            rows=("edge_lcb", "size"),
            mean_edge_lcb=("edge_lcb", "mean"),
            mean_expected_log_growth_lcb=("expected_log_growth_lcb", "mean"),
            mean_pnl_per_share=("pnl_per_share", "mean"),
        ).reset_index().to_csv(out / "grid_summary.csv", index=False)
    (out / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
