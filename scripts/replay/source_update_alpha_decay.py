#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Read-only source-availability latency-window replay observation.
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.state.db import get_trade_connection_read_only

try:
    from executable_q_lcb_replay import (
        as_float,
        contains,
        dt,
        fee_rate,
        load_beliefs,
        nearest_snapshot,
        vwap_to_size,
    )
except ModuleNotFoundError:
    from scripts.replay.executable_q_lcb_replay import (
        as_float,
        contains,
        dt,
        fee_rate,
        load_beliefs,
        nearest_snapshot,
        vwap_to_size,
    )


MODEL_UPDATE_AVAILABILITY_COLUMNS = (
    "last_run_availability_time",
    "run_availability_time",
    "availability_time",
    "available_at",
    "source_available_at",
)
MODEL_UPDATE_INIT_COLUMNS = (
    "last_run_initialisation_time",
    "run_initialisation_time",
    "run_init_time",
    "initialisation_time",
    "init_time",
)


def read_frame(path: str | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    if p.suffix == ".parquet":
        return pd.read_parquet(p)
    if p.suffix == ".json":
        return pd.read_json(p)
    return pd.read_csv(p)


def first_present(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def normalize_model_updates(path: str | None) -> pd.DataFrame:
    raw = read_frame(path)
    if raw.empty:
        return pd.DataFrame()
    cols = list(raw.columns)
    availability_col = first_present(cols, MODEL_UPDATE_AVAILABILITY_COLUMNS)
    if availability_col is None:
        raise ValueError(
            f"model updates input lacks availability column; expected one of {MODEL_UPDATE_AVAILABILITY_COLUMNS}"
        )
    init_col = first_present(cols, MODEL_UPDATE_INIT_COLUMNS)
    out = raw.copy()
    source_col = "source_id" if "source_id" in cols else ("model" if "model" in cols else None)
    out["source_id_norm"] = out[source_col].astype(str) if source_col else "unknown_source"
    out["run_availability_time_norm"] = out[availability_col].astype(str)
    out["run_initialisation_time_norm"] = (
        out[init_col].astype(str) if init_col else out["run_availability_time_norm"]
    )
    out["availability_dt"] = out["run_availability_time_norm"].map(dt)
    out["source_update_id"] = (
        out["source_id_norm"]
        + "|"
        + out["run_initialisation_time_norm"]
        + "|"
        + out["run_availability_time_norm"]
    )
    return out.sort_values("availability_dt").reset_index(drop=True)


def fallback_update_for_belief(row: pd.Series) -> dict[str, object]:
    available = str(row["source_available_at"])
    source = str(row.get("source_id") or "forecast_posteriors_fallback")
    return {
        "source_update_id": f"{source}|fallback|{available}",
        "source_id_norm": source,
        "run_availability_time_norm": available,
        "run_initialisation_time_norm": available,
        "availability_dt": dt(available),
    }


def update_for_belief(row: pd.Series, updates: pd.DataFrame) -> dict[str, object]:
    if updates.empty:
        return fallback_update_for_belief(row)
    computed = dt(str(row["computed_at"]))
    source_id = str(row.get("source_id") or "")
    candidates = updates[updates["availability_dt"] <= computed]
    if source_id and "source_id_norm" in candidates.columns:
        same_source = candidates[candidates["source_id_norm"] == source_id]
        if not same_source.empty:
            candidates = same_source
    if candidates.empty:
        return fallback_update_for_belief(row)
    return candidates.iloc[-1].to_dict()


def parse_minutes(spec: str) -> int:
    sign = -1 if spec.startswith("-") else 1
    s = spec.lstrip("+-")
    if s.endswith("m"):
        return sign * int(s[:-1])
    if s.endswith("h"):
        return sign * int(float(s[:-1]) * 60)
    if s.endswith("d"):
        return sign * int(float(s[:-1]) * 24 * 60)
    raise ValueError(f"unsupported time shift/window: {spec}")


def window_edges(spec: str) -> list[int]:
    vals = [parse_minutes(x) for x in spec.split(",") if x]
    vals = sorted(set(vals))
    if len(vals) < 2:
        raise ValueError("--windows needs at least two boundaries")
    return vals


def label_for_delay(delay_min: float, edges: list[int]) -> str | None:
    for lo, hi in zip(edges, edges[1:]):
        if lo <= delay_min < hi:
            return f"{lo}m_{hi}m"
    return None


def run(
    edges: list[int],
    placebo_shifts: list[int],
    *,
    model_updates: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict]:
    beliefs = load_beliefs()
    if beliefs.empty:
        return pd.DataFrame(), {"ok": False, "reason": "no local belief rows"}
    updates = model_updates if model_updates is not None else pd.DataFrame()
    tr = get_trade_connection_read_only()
    cur = tr.cursor()
    rows = []
    previous_q: dict[tuple[str, str, str, str], float] = {}
    ordered = beliefs.sort_values("computed_at")
    for _, r in ordered.iterrows():
        q_key = (r["city"], r["metric"], r["target_date"], r["range_label"])
        q_shock = float(r["q_point_yes"]) - previous_q.get(q_key, float(r["q_point_yes"]))
        previous_q[q_key] = float(r["q_point_yes"])
        settled_known = r.get("authority") == "VERIFIED" and not pd.isna(r.get("settlement_value"))
        yes_hit = contains(r, float(r["settlement_value"])) if settled_known else np.nan
        source_update = update_for_belief(r, updates)
        scenarios = [("actual", 0)] + [(f"placebo_{m:+d}m", m) for m in placebo_shifts]
        for scenario, shift_min in scenarios:
            availability = source_update["availability_dt"] + timedelta(minutes=shift_min)
            delay = (dt(r["computed_at"]) - availability).total_seconds() / 60.0
            window = label_for_delay(delay, edges)
            if window is None:
                continue
            snap = nearest_snapshot(cur, str(r["yes_token_id"]), str(r["computed_at"]))
            if not snap:
                continue
            ask_v = as_float(snap["ask"])
            if ask_v is None:
                continue
            ask = ask_v
            vwap, _filled = vwap_to_size(snap["depth_json"], ask, 10.0)
            if vwap is None:
                continue
            fee = fee_rate(snap["fee_json"], "polymarket_weather") * vwap * (1.0 - vwap)
            cost = vwap + fee
            edge = float(r["q_lcb_yes"]) - cost
            pnl = np.nan if pd.isna(yes_hit) else (1.0 if yes_hit else 0.0) - cost
            rows.append(
                {
                    "scenario": scenario,
                    "window": window,
                    "delay_min": delay,
                    "source_update_id": source_update["source_update_id"],
                    "source_id": source_update["source_id_norm"],
                    "run_availability_time": source_update["run_availability_time_norm"],
                    "city": r["city"],
                    "metric": r["metric"],
                    "target_date": r["target_date"],
                    "range_label": r["range_label"],
                    "q_shock": q_shock,
                    "q_lcb": float(r["q_lcb_yes"]),
                    "executable_cost": cost,
                    "edge_lcb": edge,
                    "settled": bool(settled_known),
                    "pnl_per_share": pnl,
                }
            )
    tr.close()
    df = pd.DataFrame(rows)
    if df.empty:
        return df, {"ok": False, "reason": "no belief rows had matching executable book snapshots"}
    grouped = df.groupby(["scenario", "window"], dropna=False).agg(
        rows=("edge_lcb", "size"),
        mean_q_shock=("q_shock", "mean"),
        mean_edge_lcb=("edge_lcb", "mean"),
        positive_edge_share=("edge_lcb", lambda s: float((s > 0).mean())),
        settled_rows=("settled", "sum"),
        mean_pnl_per_share=("pnl_per_share", "mean"),
    ).reset_index()
    actual = grouped[grouped["scenario"] == "actual"].copy()
    placebo = grouped[grouped["scenario"] != "actual"].copy()
    actual_10_60 = actual[actual["window"].isin(["10m_20m", "20m_40m", "40m_60m"])]
    report = {
        "ok": True,
        "rows": int(len(df)),
        "actual_rows": int((df["scenario"] == "actual").sum()),
        "placebo_rows": int((df["scenario"] != "actual").sum()),
        "actual_10_60_mean_edge_lcb": float(actual_10_60["mean_edge_lcb"].mean()) if len(actual_10_60) else None,
        "placebo_mean_edge_lcb": float(placebo["mean_edge_lcb"].mean()) if len(placebo) else None,
        "alpha_concentrates_10_60": bool(
            len(actual_10_60)
            and float(actual_10_60["mean_edge_lcb"].mean()) > float(actual["mean_edge_lcb"].mean())
        ),
        "placebo_weaker_than_actual": bool(
            len(placebo)
            and float(actual["mean_edge_lcb"].mean()) > float(placebo["mean_edge_lcb"].mean())
        ),
        "source_updates_used": bool(not updates.empty),
        "unique_source_updates": int(df["source_update_id"].nunique()) if "source_update_id" in df else 0,
    }
    return grouped, report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-updates")
    ap.add_argument("--beliefs")
    ap.add_argument("--orderbooks")
    ap.add_argument("--settlements")
    ap.add_argument("--windows", default="0m,5m,10m,20m,40m,60m,120m")
    ap.add_argument("--placebo-shifts", default="-24h,+24h,+7d")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    edges = window_edges(args.windows)
    placebo = [parse_minutes(x) for x in args.placebo_shifts.split(",") if x]
    updates = normalize_model_updates(args.model_updates)
    grouped, report = run(edges, placebo, model_updates=updates)
    grouped.to_csv(out / "source_update_alpha_decay.csv", index=False)
    report["model_updates_input"] = args.model_updates
    report["model_updates_used"] = bool(not updates.empty)
    report["fallback"] = (
        "forecast_posteriors.source_available_at used when model-updates input is missing"
        if updates.empty
        else None
    )
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
