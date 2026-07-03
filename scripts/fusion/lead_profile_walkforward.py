#!/usr/bin/env python3
# Created: 2026-06-25
# Purpose: Read-only vNext profile walk-forward diagnostic; writes report artifacts only.
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


PROFILE_RE = re.compile(r"L([123])\[([^\]]*)\]")


def profile_sources(label: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for lead_s, body in PROFILE_RE.findall(str(label)):
        out[int(lead_s)] = [x for x in body.split("+") if x]
    return out


def sibling(path: Path, suffix: str) -> Path:
    name = path.name
    marker = ".all_profiles.csv"
    if name.endswith(marker):
        return path.with_name(name[: -len(marker)] + suffix)
    return path.with_suffix(suffix)


def find_source_json(profiles: Path) -> Path:
    md = sibling(profiles, ".md")
    if md.exists():
        for line in md.read_text(encoding="utf-8").splitlines():
            if line.startswith("- source_json:"):
                raw = line.split("`", 2)[1]
                p = Path(raw)
                if p.exists():
                    return p
    candidates = sorted(profiles.parent.glob("fusion_n_compare_*.json"))
    if not candidates:
        raise FileNotFoundError("no fusion_n_compare_*.json sibling found for per-date walk-forward rows")
    return candidates[-1]


def select_candidates(pareto: pd.DataFrame) -> pd.DataFrame:
    p = pareto.copy()
    c = p[
        (~p["has_provider_family_duplicate"].astype(bool))
        & (p["full_lead1_3_coverage"].astype(bool))
        & (p["delta_mae_vs_current"] > 0)
        & (p["delta_rmse_vs_current"] > 0)
    ].copy()
    c["source_count_total"] = c[["lead1_sources", "lead2_sources", "lead3_sources"]].fillna("").map(
        lambda v: 0 if not str(v) else len(str(v).split("+"))
    ).sum(axis=1)
    c["robust_score"] = (
        0.45 * (c["delta_mae_vs_current"] / c["current_mae_c"].replace(0, np.nan))
        + 0.45 * (c["delta_rmse_vs_current"] / c["current_rmse_c"].replace(0, np.nan))
        + 0.10 * (c["delta_abs_bias_vs_current"] / c["current_abs_bias_c"].replace(0, np.nan)).fillna(0).clip(-1, 1)
        - 0.002 * c["source_count_total"]
    )
    selected = (
        c.sort_values(["city", "metric", "robust_score"], ascending=[True, True, False])
        .groupby(["city", "metric"], as_index=False)
        .head(1)
        .copy()
    )
    selected["promotion_tier"] = np.select(
        [
            (selected["delta_mae_vs_current"] >= 0.15)
            & (selected["delta_rmse_vs_current"] >= 0.10)
            & (selected["sample_n"] >= 180)
            & (selected["delta_abs_bias_vs_current"] >= -0.05),
            (selected["delta_mae_vs_current"] >= 0.05)
            & (selected["delta_rmse_vs_current"] >= 0.05)
            & (selected["sample_n"] >= 180)
            & (selected["delta_abs_bias_vs_current"] >= -0.15),
        ],
        ["T0_FAST_PROMOTION", "T1_PROMOTION_CANDIDATE"],
        default="T2_RESEARCH",
    )
    return selected


def cdf(x: float, mu: float, sigma: float) -> float:
    sigma = max(float(sigma), 1e-6)
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def bin_prob(settled_c: float, mu: float, sigma: float) -> float:
    k = int(math.floor(settled_c + 0.5))
    return max(1e-12, cdf(k + 0.5, mu, sigma) - cdf(k - 0.5, mu, sigma))


def crps_normal(y: float, mu: float, sigma: float) -> float:
    sigma = max(float(sigma), 1e-6)
    z = (y - mu) / sigma
    phi = math.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    Phi = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return sigma * (z * (2.0 * Phi - 1.0) + 2.0 * phi - 1.0 / math.sqrt(math.pi))


def predict(row: dict, spec: dict[int, list[str]]) -> float | None:
    sources = spec.get(int(row["lead"]), [])
    values = row.get("source_values_c") or {}
    vals = [float(values[s]) for s in sources if s in values and values[s] is not None]
    if len(vals) != len(sources) or not vals:
        return None
    return float(np.mean(vals))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", required=True)
    ap.add_argument("--split", default="weekly")
    ap.add_argument("--train-window", default="8w")
    ap.add_argument("--test-window", default="1w")
    ap.add_argument("--objectives", default="brier,logloss,crps,mae,rmse")
    ap.add_argument("--family-dedup", choices=["required", "allow"], default="required")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    profiles = Path(args.profiles)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pareto_path = sibling(profiles, ".pareto.csv")
    if not pareto_path.exists():
        raise FileNotFoundError(f"missing sibling Pareto file: {pareto_path}")

    candidates = select_candidates(pd.read_csv(pareto_path))
    t0 = candidates[candidates["promotion_tier"] == "T0_FAST_PROMOTION"].copy()
    source_json = find_source_json(profiles)
    rows = json.loads(source_json.read_text(encoding="utf-8"))["rows"]
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        if int(r.get("lead", 0)) in (1, 2, 3) and r.get("settlement_c") is not None:
            by_key[(r["city"], r["metric"])].append(r)

    fold_rows: list[dict] = []
    train_days = 56
    test_days = 7
    for _, cand in t0.iterrows():
        key = (cand["city"], cand["metric"])
        spec = profile_sources(cand["profile"])
        data = sorted(by_key.get(key, []), key=lambda r: (r["target_date"], int(r["lead"])))
        if not data:
            continue
        dates = sorted({date.fromisoformat(r["target_date"]) for r in data})
        start = min(dates) + timedelta(days=train_days)
        end = max(dates)
        cursor = start
        while cursor <= end:
            train_start = cursor - timedelta(days=train_days)
            test_end = cursor + timedelta(days=test_days)
            train = [r for r in data if train_start <= date.fromisoformat(r["target_date"]) < cursor]
            test = [r for r in data if cursor <= date.fromisoformat(r["target_date"]) < test_end]
            if len(train) >= 30 and test:
                pred_train = [(predict(r, spec), float(r["current_fusion_c"]), float(r["settlement_c"])) for r in train]
                pred_train = [x for x in pred_train if x[0] is not None]
                cand_sigma = max(1.0, float(np.sqrt(np.mean([(p - y) ** 2 for p, _, y in pred_train])))) if pred_train else 1.0
                cur_sigma = max(1.0, float(np.sqrt(np.mean([(c - y) ** 2 for _, c, y in pred_train])))) if pred_train else 1.0
                metrics = defaultdict(list)
                for r in test:
                    p = predict(r, spec)
                    if p is None:
                        continue
                    c = float(r["current_fusion_c"])
                    y = float(r["settlement_c"])
                    qp = bin_prob(y, p, cand_sigma)
                    qc = bin_prob(y, c, cur_sigma)
                    metrics["candidate_abs"].append(abs(p - y))
                    metrics["current_abs"].append(abs(c - y))
                    metrics["candidate_sq"].append((p - y) ** 2)
                    metrics["current_sq"].append((c - y) ** 2)
                    metrics["candidate_logloss"].append(-math.log(qp))
                    metrics["current_logloss"].append(-math.log(qc))
                    metrics["candidate_brier"].append((1.0 - qp) ** 2)
                    metrics["current_brier"].append((1.0 - qc) ** 2)
                    metrics["candidate_crps"].append(crps_normal(y, p, cand_sigma))
                    metrics["current_crps"].append(crps_normal(y, c, cur_sigma))
                    qlcb = max(0.0, qp - 1.64 * math.sqrt(max(qp * (1.0 - qp), 0.0) / max(len(pred_train), 1)))
                    metrics["candidate_qlcb"].append(qlcb)
                    metrics["candidate_hit"].append(1.0)
                if metrics["candidate_abs"]:
                    fold_rows.append(
                        {
                            "city": key[0],
                            "metric": key[1],
                            "profile": cand["profile"],
                            "fold_start": cursor.isoformat(),
                            "n": len(metrics["candidate_abs"]),
                            "candidate_mae": float(np.mean(metrics["candidate_abs"])),
                            "current_mae": float(np.mean(metrics["current_abs"])),
                            "candidate_rmse": float(np.sqrt(np.mean(metrics["candidate_sq"]))),
                            "current_rmse": float(np.sqrt(np.mean(metrics["current_sq"]))),
                            "candidate_logloss": float(np.mean(metrics["candidate_logloss"])),
                            "current_logloss": float(np.mean(metrics["current_logloss"])),
                            "candidate_brier": float(np.mean(metrics["candidate_brier"])),
                            "current_brier": float(np.mean(metrics["current_brier"])),
                            "candidate_crps": float(np.mean(metrics["candidate_crps"])),
                            "current_crps": float(np.mean(metrics["current_crps"])),
                            "candidate_mean_qlcb": float(np.mean(metrics["candidate_qlcb"])),
                            "candidate_hit_rate": float(np.mean(metrics["candidate_hit"])),
                        }
                    )
            cursor += timedelta(days=test_days)

    folds = pd.DataFrame(fold_rows)
    folds.to_csv(out / "walkforward_folds.csv", index=False)
    if folds.empty:
        report = {"ok": False, "reason": "no eligible walk-forward folds", "source_json": str(source_json)}
    else:
        total_n = int(folds["n"].sum())
        w = folds["n"] / total_n
        summary = {
            "folds": int(len(folds)),
            "rows": total_n,
            "t0_profiles": int(len(t0)),
            "candidate_brier": float((folds["candidate_brier"] * w).sum()),
            "current_brier": float((folds["current_brier"] * w).sum()),
            "candidate_logloss": float((folds["candidate_logloss"] * w).sum()),
            "current_logloss": float((folds["current_logloss"] * w).sum()),
            "candidate_rmse": float((folds["candidate_rmse"] * w).sum()),
            "current_rmse": float((folds["current_rmse"] * w).sum()),
            "candidate_mean_qlcb": float((folds["candidate_mean_qlcb"] * w).sum()),
            "candidate_hit_rate": float((folds["candidate_hit_rate"] * w).sum()),
        }
        summary["pass"] = bool(
            summary["candidate_brier"] < summary["current_brier"]
            and summary["candidate_logloss"] < summary["current_logloss"]
            and summary["candidate_rmse"] <= summary["current_rmse"] + 0.03
            and summary["candidate_hit_rate"] >= summary["candidate_mean_qlcb"]
        )
        report = {"ok": True, "source_json": str(source_json), "summary": summary}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
