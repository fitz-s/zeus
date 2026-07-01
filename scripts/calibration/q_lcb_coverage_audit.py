#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Read-only q_lcb coverage audit over settled local posterior rows.
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replay"))
from executable_q_lcb_replay import contains, dt, lead_days, load_beliefs  # noqa: E402


def lead_bucket(n: int) -> str:
    if n <= 1:
        return "L1"
    if n <= 3:
        return "L2_3"
    return "L4P"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--beliefs")
    ap.add_argument("--settlements")
    ap.add_argument("--group-by", default="city,metric,lead,profile,side")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    beliefs = load_beliefs()
    if beliefs.empty:
        report = {"ok": False, "reason": "no local belief rows"}
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    rows = []
    qlcb_gt_point = 0
    for _, r in beliefs.iterrows():
        if r.get("authority") != "VERIFIED" or pd.isna(r.get("settlement_value")):
            continue
        hit_yes = contains(r, float(r["settlement_value"]))
        lead_n = lead_days(r["computed_at"], r["target_date"])
        common = {
            "city": r["city"],
            "metric": r["metric"],
            "lead": lead_bucket(lead_n),
            "lead_days": lead_n,
            "profile": r["product_id"],
            "posterior_id": r["posterior_id"],
            "target_date": r["target_date"],
            "range_label": r["range_label"],
        }
        q_point_yes = float(r["q_point_yes"])
        q_lcb_yes = float(r["q_lcb_yes"])
        q_ucb_yes = float(r["q_ucb_yes"])
        if q_lcb_yes > q_point_yes + 1e-12:
            qlcb_gt_point += 1
        rows.append({**common, "side": "YES", "q_point": q_point_yes, "q_lcb": q_lcb_yes, "hit": float(hit_yes)})
        rows.append(
            {
                **common,
                "side": "NO",
                "q_point": 1.0 - q_point_yes,
                "q_lcb": 1.0 - q_ucb_yes,
                "hit": float(not hit_yes),
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(out / "q_lcb_coverage_rows.csv", index=False)
    if df.empty:
        report = {"ok": False, "reason": "no settled posterior-bin rows"}
        (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return

    group_cols = [c.strip() for c in args.group_by.split(",") if c.strip()]
    grouped = df.groupby(group_cols, dropna=False).agg(
        n=("hit", "size"),
        observed_frequency=("hit", "mean"),
        mean_q_point=("q_point", "mean"),
        mean_q_lcb=("q_lcb", "mean"),
    ).reset_index()
    grouped["coverage_ok"] = grouped["observed_frequency"] + 1e-12 >= grouped["mean_q_lcb"]
    grouped["coverage_gap"] = grouped["observed_frequency"] - grouped["mean_q_lcb"]
    grouped.to_csv(out / "q_lcb_coverage_groups.csv", index=False)
    material = grouped[grouped["n"] >= 20]
    failing = material[~material["coverage_ok"]]
    report = {
        "ok": True,
        "rows": int(len(df)),
        "groups": int(len(grouped)),
        "material_groups_n_ge_20": int(len(material)),
        "failing_material_groups": int(len(failing)),
        "min_material_coverage_gap": float(material["coverage_gap"].min()) if len(material) else None,
        "q_lcb_gt_q_point_violations": int(qlcb_gt_point),
        "no_side_lower_bound_formula": "1 - q_ucb_yes",
        "pass": bool(len(failing) == 0 and qlcb_gt_point == 0),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
