#!/usr/bin/env python3
# Created: 2026-06-22
# Last audited: 2026-06-22
# Authority basis: per-city historical settlement-skill gate
#   (team-lead approved (a) 2026-06-22; live_order_pathology 2026-06-22). The data-precision /
#   grid-distance hypothesis was falsified (corr(forecast_error, d_eff_m)=−0.52). The pre-trade
#   separator is each city's HISTORICAL settlement-skill (Brier-vs-market). This fitter is the only
#   writer of state/city_skill_gate.json; the runtime serving rule src/decision/city_skill_gate.py
#   READS it. READ-ONLY over state/zeus-world.db (settlement_attribution).
"""Fit the per-city historical settlement-skill gate, WALK-FORWARD (no leak).

Per-city prior skill = mean(market_Brier − our_Brier) over that city's rows settled STRICTLY before a
boundary (positive => our forecast beat the market for that city). The fitter:
  1. Builds settled city bets from settlement_attribution (our q_in_bin, market_in_bin_prob,
     settled_in_bin -> our/market Brier; realized after-cost EV from won/avg_fill_price).
  2. LEARNS (min_track_record, skill_floor) by an INNER walk-forward prequential admitted-EV sweep:
     for each candidate (min_track, floor), replay every bet using ONLY that city's prior rows, admit
     iff prior_n>=min_track and prior_skill>floor, and accumulate the admitted after-cost EV. Choose
     the pair maximizing total admitted EV (ties -> the one admitting more, then the more
     conservative). NEVER hard-coded.
  3. Persists the END-OF-WINDOW per-city prior_skill + prior_n (the runtime applies them to FUTURE,
     un-settled decisions) and the learned hyperparameters in _meta.

HONEST: at n~91 this admits only the few reliably-skilled extremes; most cities abstain. The fitter
reports the learned hyperparameters and the per-city skills; the forward-validation harness owns the
walk-forward EV gate.

READ-ONLY over state/zeus-world.db. Writes state/city_skill_gate.json via atomic replace.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

from src.decision import city_skill_gate as g

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORLD_DEFAULT = os.path.join(REPO, "state", "zeus-world.db")
OUT_DEFAULT = os.path.join(REPO, "state", "city_skill_gate.json")

AUTHORITY = "city_skill_gate_v1_walkforward"
# Hyperparameter search grids (the VALUES are learned from these; the grids themselves bound the
# search, they are not the hard-coded answer).
MIN_TRACK_GRID = (3, 4, 5, 6, 8)
FLOOR_GRID = (0.0, 0.01, 0.02, 0.05, 0.1)


@dataclass(frozen=True)
class SettledCityBet:
    """One settled bet for skill accounting.

    city / target_date (the no-leak key) / our_brier / market_brier (for skill) / realized_ev
    (after-cost: won?(1-fill):(-fill)).
    """

    city: str
    target_date: str
    our_brier: float
    market_brier: float
    realized_ev: float


def _date10(td) -> str:
    return str(td)[:10]


def prior_skill(rows, *, city: str, boundary: str) -> tuple[float, int]:
    """(mean market_Brier − our_Brier, n) over ``city`` rows with target_date STRICTLY before
    ``boundary`` (walk-forward, no leak). (0.0, 0) when no prior rows."""
    b = _date10(boundary)
    sk = 0.0
    n = 0
    for r in rows:
        if r.city != city:
            continue
        if not (_date10(r.target_date) < b):
            continue
        sk += (r.market_brier - r.our_brier)
        n += 1
    return (sk / n if n else 0.0, n)


def _prequential_admitted_ev(rows, *, min_track: int, floor: float) -> tuple[float, int]:
    """Total after-cost admitted EV under (min_track, floor) replayed walk-forward (each bet uses
    only its city's prior rows). Returns (ev_sum, admit_n)."""
    ev = 0.0
    n = 0
    for r in rows:
        sk, pn = prior_skill(rows, city=r.city, boundary=r.target_date)
        if pn >= min_track and sk > floor:
            ev += r.realized_ev
            n += 1
    return ev, n


def learn_hyperparameters(rows) -> tuple[int, float, float, int]:
    """Learn (min_track_record, skill_floor) by inner walk-forward prequential admitted-EV.
    Returns (min_track, floor, best_ev, admit_n). Ties broken toward MORE admits then conservatism."""
    best = None  # (ev, admit_n, -min_track... ) we maximize ev, then admit_n, then conservative
    for mt in MIN_TRACK_GRID:
        for fl in FLOOR_GRID:
            ev, n = _prequential_admitted_ev(rows, min_track=mt, floor=fl)
            # Require at least 1 admit to consider; prefer higher EV, then more admits, then larger
            # min_track (more conservative track-record requirement).
            key = (round(ev, 6), n, mt)
            if n >= 1 and (best is None or key > best[0]):
                best = (key, mt, fl, ev, n)
    if best is None:
        # No (mt, fl) admits anything -> the most conservative gate (admit nothing).
        return MIN_TRACK_GRID[-1], FLOOR_GRID[-1], 0.0, 0
    _key, mt, fl, ev, n = best
    return mt, fl, ev, n


def build_rows(world_path: str) -> list[SettledCityBet]:
    con = sqlite3.connect(f"file:{world_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        db_rows = con.execute(
            "SELECT city, target_date, q_in_bin, market_in_bin_prob, settled_in_bin, won, avg_fill_price "
            "FROM settlement_attribution "
            "WHERE q_in_bin IS NOT NULL AND market_in_bin_prob IS NOT NULL "
            "  AND settled_in_bin IS NOT NULL AND target_date IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    out: list[SettledCityBet] = []
    for r in db_rows:
        t = float(r["settled_in_bin"])
        our_b = (float(r["q_in_bin"]) - t) ** 2
        mkt_b = (float(r["market_in_bin_prob"]) - t) ** 2
        fill = float(r["avg_fill_price"] or 0.0)
        ev = (1.0 - fill) if int(r["won"] or 0) else (-fill)
        out.append(SettledCityBet(str(r["city"]), _date10(r["target_date"]), our_b, mkt_b, ev))
    return out


def _both_halves_skill(rows) -> dict[str, tuple]:
    """Per-city (early_skill, late_skill) split at the median target_date. A city negative in BOTH
    halves is a TEMPORALLY-STABLE loser (the only kind the loss-reduction gate hard-blocks)."""
    rs = sorted(rows, key=lambda r: r.target_date)
    if not rs:
        return {}
    mid = rs[len(rs) // 2].target_date
    early: dict[str, list] = defaultdict(lambda: [0, 0.0])
    late: dict[str, list] = defaultdict(lambda: [0, 0.0])
    for r in rs:
        tgt = early if r.target_date < mid else late
        tgt[r.city][0] += 1
        tgt[r.city][1] += (r.market_brier - r.our_brier)
    out: dict[str, tuple] = {}
    for city in set(early) | set(late):
        e = early[city]
        l = late[city]
        es = e[1] / e[0] if e[0] else None
        ls = l[1] / l[0] if l[0] else None
        out[city] = (es, ls)
    return out


def fit_city_skill_gate(rows, *, posterior_version: str = g.DEFAULT_POSTERIOR_VERSION) -> dict:
    """Fit the artifact: learned (min_track, floor) + per-city end-of-window prior_skill/prior_n."""
    min_track, floor, learned_ev, admit_n = learn_hyperparameters(rows)
    # End-of-window per-city skill (boundary = after the last target_date so every row counts).
    max_td = max((r.target_date for r in rows), default="0000-00-00")
    boundary = "9999-12-31"
    # Both-halves stability (team-lead: only LIST a stable loser confirmed negative in BOTH halves).
    half_skill = _both_halves_skill(rows)
    cities: dict[str, dict] = {}
    seen = sorted({r.city for r in rows})
    for city in seen:
        sk, n = prior_skill(rows, city=city, boundary=boundary)
        e, l = half_skill.get(city, (None, None))
        stable_bad = (e is not None and l is not None and e < 0 and l < 0)
        stable_good = (e is not None and l is not None and e > 0 and l > 0)
        cities[city] = {
            "prior_skill": round(sk, 6), "prior_n": int(n),
            "early_skill": round(e, 6) if e is not None else None,
            "late_skill": round(l, 6) if l is not None else None,
            "stable_bad": bool(stable_bad), "stable_good": bool(stable_good),
        }
    fitted_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return {
        "_meta": {
            "authority": AUTHORITY,
            "version": "city_skill_v1",
            "posterior_version": posterior_version,
            "min_track_record": int(min_track),
            "skill_floor": float(floor),
            "learned_inner_admitted_ev": round(float(learned_ev), 4),
            "learned_inner_admit_n": int(admit_n),
            "max_target_date": max_td,
            "n_rows": len(rows),
            "created": fitted_at,
            "skill_metric": "mean(market_Brier - our_Brier) over prior-settled city rows",
            "note": "HONEST: at n~91 only the reliably-skilled extremes admit; most abstain; reliably-bad blocked.",
        },
        "cities": cities,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Fit the per-city historical settlement-skill gate (walk-forward).")
    ap.add_argument("--world", default=WORLD_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    args = ap.parse_args()

    rows = build_rows(args.world)
    artifact = fit_city_skill_gate(rows)

    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
    os.replace(tmp, args.out)

    m = artifact["_meta"]
    print(f"[city-skill-gate] wrote {args.out}")
    print(f"    n_rows={m['n_rows']} learned min_track={m['min_track_record']} floor={m['skill_floor']} "
          f"inner_admit_ev={m['learned_inner_admitted_ev']} inner_admit_n={m['learned_inner_admit_n']}")
    admits = [c for c, v in artifact["cities"].items() if v["prior_skill"] > m["skill_floor"] and v["prior_n"] >= m["min_track_record"]]
    print(f"    end-of-window ADMIT cities: {admits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
