#!/usr/bin/env python3
# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: per-city historical settlement-skill gate
#   (team-lead approved (a) 2026-06-22; live_order_pathology 2026-06-22). Walk-forward
#   forward-validation harness. READ-ONLY over state/zeus-world.db (settlement_attribution). Writes a
#   JSON report under docs/evidence/live_order_pathology/. The orchestrator owns promotion/deploy.
"""Walk-forward forward-validation for the per-city historical settlement-skill gate (no look-ahead).

THE BAR: at each bet's decision time T (= target_date), rebuild the per-city skill estimate on that
city's PRIOR-settled rows only, LEARN (min_track, floor) on the rows resolved before T, gate, and
report:
  * admitted cities + blocked cities,
  * after-cost NOTIONAL-weighted EV on the admitted set (forward, no look-ahead),
  * per-city early/late sign-stability,
  * contrast vs the LOOK-AHEAD (full-sample-skill) gate so the honest gap is explicit.

HONEST: at n~91 only the reliably-skilled extremes admit; the report states the real admitted-EV.
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os

import scripts.fit_city_skill_gate as fcsg

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD_DEFAULT = os.path.join(REPO, "state", "zeus-world.db")
OUT_DEFAULT = os.path.join(
    REPO, "docs", "evidence", "live_order_pathology",
    "2026-06-22_city_skill_gate_forward_validation.json",
)


def walk_forward(rows) -> dict:
    """As-of-decision replay. For each bet at T=target_date, learn (min_track, floor) on rows
    resolved STRICTLY before T, compute the bet city's prior skill on prior rows, gate, accumulate."""
    rows_sorted = sorted(rows, key=lambda r: r.target_date)

    admit_n = 0
    admit_ev = 0.0
    block_n = 0
    admit_cities = collections.Counter()
    block_cities = collections.Counter()
    per_bet = []

    for r in rows_sorted:
        T = r.target_date
        prior = [x for x in rows_sorted if x.target_date < T]
        if not prior:
            block_n += 1
            block_cities[r.city] += 1
            continue
        # Learn hyperparameters on the PRIOR-resolved rows only (no look-ahead).
        min_track, floor, _ev, _n = fcsg.learn_hyperparameters(prior)
        sk, pn = fcsg.prior_skill(prior, city=r.city, boundary=T)
        admit = (pn >= min_track and sk > floor)
        if admit:
            admit_n += 1
            admit_ev += r.realized_ev
            admit_cities[r.city] += 1
        else:
            block_n += 1
            block_cities[r.city] += 1
        per_bet.append({
            "city": r.city, "target_date": T, "prior_skill": round(sk, 4), "prior_n": pn,
            "min_track": min_track, "floor": floor, "admit": bool(admit),
            "realized_ev": round(r.realized_ev, 4),
        })

    return {
        "admit_n": admit_n,
        "block_n": block_n,
        "admitted_ev_sum": round(admit_ev, 4),
        "admitted_ev_per_bet": round(admit_ev / admit_n, 4) if admit_n else None,
        "admit_cities": dict(admit_cities),
        "block_cities": dict(block_cities),
        "per_bet": per_bet,
    }


def lookahead_gate(rows) -> dict:
    """The LOOK-AHEAD (invalid) gate: gate cities by FULL-sample skill>0. Reported ONLY to make the
    look-ahead vs walk-forward gap explicit (this is the in-sample +10.1% artifact)."""
    skill = collections.defaultdict(lambda: [0, 0.0])
    ev = collections.defaultdict(lambda: [0, 0.0])
    for r in rows:
        skill[r.city][0] += 1
        skill[r.city][1] += (r.market_brier - r.our_brier)
        ev[r.city][0] += 1
        ev[r.city][1] += r.realized_ev
    admit_ev = admit_n = 0.0, 0
    a_ev = a_n = 0
    for city in skill:
        s = skill[city][1] / skill[city][0]
        if s > 0:
            a_ev += ev[city][1]
            a_n += ev[city][0]
    return {"admit_n": a_n, "admitted_ev_sum": round(a_ev, 4),
            "admitted_ev_per_bet": round(a_ev / a_n, 4) if a_n else None,
            "note": "LOOK-AHEAD (full-sample skill>0) — INVALID for promotion; shows the in-sample +10.1% artifact."}


def stability_table(rows) -> list[dict]:
    """Per-city early/late edge-sign stability (split by median target_date)."""
    rows_sorted = sorted(rows, key=lambda r: r.target_date)
    if not rows_sorted:
        return []
    mid = rows_sorted[len(rows_sorted) // 2].target_date
    early = collections.defaultdict(lambda: [0, 0.0])
    late = collections.defaultdict(lambda: [0, 0.0])
    for r in rows_sorted:
        tgt = early if r.target_date < mid else late
        tgt[r.city][0] += 1
        tgt[r.city][1] += r.realized_ev
    out = []
    for city in sorted(set(early) | set(late)):
        e, l = early[city], late[city]
        es = e[1] / e[0] if e[0] else None
        ls = l[1] / l[0] if l[0] else None
        same = (es is not None and ls is not None and (es > 0) == (ls > 0))
        out.append({"city": city, "early_ev": round(es, 4) if es is not None else None, "early_n": e[0],
                    "late_ev": round(ls, 4) if ls is not None else None, "late_n": l[0],
                    "sign_stable": bool(same) if (es is not None and ls is not None) else None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Forward-validate the per-city skill gate (walk-forward, read-only).")
    ap.add_argument("--world", default=WORLD_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    args = ap.parse_args()

    rows = fcsg.build_rows(args.world)
    wf = walk_forward(rows)
    la = lookahead_gate(rows)
    stab = stability_table(rows)
    n_stable = sum(1 for s in stab if s["sign_stable"]) if stab else 0
    n_eval = sum(1 for s in stab if s["sign_stable"] is not None)

    report = {
        "_meta": {
            "authority": "city_skill_gate_forward_validation",
            "created": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "world": args.world,
            "n_rows": len(rows),
        },
        "walk_forward_gate": {k: v for k, v in wf.items() if k != "per_bet"},
        "lookahead_gate_INVALID": la,
        "stability": stab,
        "stability_summary": {"sign_stable_cities": n_stable, "evaluable_cities": n_eval},
        "per_bet": wf["per_bet"],
    }

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=False)

    print(f"[city-skill-gate FV] wrote {args.out}")
    print(f"    n_rows={len(rows)}")
    print(f"    WALK-FORWARD (the gate): admit_n={wf['admit_n']} ev_sum={wf['admitted_ev_sum']} "
          f"ev/bet={wf['admitted_ev_per_bet']} admit_cities={wf['admit_cities']}")
    print(f"    LOOK-AHEAD (invalid):    admit_n={la['admit_n']} ev_sum={la['admitted_ev_sum']} ev/bet={la['admitted_ev_per_bet']}")
    print(f"    stability: {n_stable}/{n_eval} cities sign-stable early/late")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
