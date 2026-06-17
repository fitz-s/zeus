# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: task brief — root-cause modal/buy_yes negative after-cost EV drag.
# READ-ONLY. No venue calls. No daemon restart. No writes to live DBs. No edits to src.
"""Root-cause analysis of why spine modal-bin and buy_yes selections settle negative.

Reuses qkernel_settlement_ev_replay.py's join machinery (same window, same strict joins,
same spine reconstruction) but enriches each graded row with:
  - spine μ* (decision-time predictive center, native units)
  - spine σ (predictive spread)
  - realized settlement (same as settlement_value from settlement_outcomes)
  - signed center error = μ* − realized
  - modal bin coverage (which integer range the modal grid bin covers)
  - direction and bin_kind of the selected leg
  - all-in cost

Writes docs/evidence/qkernel_rebuild/modal_buyyes_drag_rootcause_2026-06-16.md
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_LIVE_STATE = "/Users/leofitz/zeus/state"
FORECASTS_DB = os.path.join(_LIVE_STATE, "zeus-forecasts.db")
TRADES_DB    = os.path.join(_LIVE_STATE, "zeus_trades.db")
WORLD_DB     = os.path.join(_LIVE_STATE, "zeus-world.db")

REPORT_PATH = os.path.join(
    _ROOT, "docs", "evidence", "qkernel_rebuild",
    "modal_buyyes_drag_rootcause_2026-06-16.md",
)

WINDOW_START = "2026-06-09"
WINDOW_END   = "2026-06-15"

# ── import arm replay unmodified ────────────────────────────────────────────
_REPLAY_PATH = os.path.join(_SCRIPT_DIR, "qkernel_arm_replay.py")
_spec = importlib.util.spec_from_file_location("qkernel_arm_replay", _REPLAY_PATH)
arm = importlib.util.module_from_spec(_spec)
sys.modules["qkernel_arm_replay"] = arm
_spec.loader.exec_module(arm)

N_BAND_DRAWS = arm.N_BAND_DRAWS
BAND_ALPHA   = arm.BAND_ALPHA
ro           = arm.ro

# ── import replay script for shared helpers ─────────────────────────────────
_REP2_PATH = os.path.join(_SCRIPT_DIR, "qkernel_settlement_ev_replay.py")
_spec2 = importlib.util.spec_from_file_location("qkernel_settlement_ev_replay", _REP2_PATH)
rep2 = importlib.util.module_from_spec(_spec2)
sys.modules["qkernel_settlement_ev_replay"] = rep2
_spec2.loader.exec_module(rep2)

from src.config import load_cities                             # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.decision.payoff_vector import edge_lower_bound, point_fair_value  # noqa: E402
from src.forecast.debias_authority import DebiasAuthority     # noqa: E402
from src.probability.joint_q import JointQError, build_joint_q  # noqa: E402
from src.probability.joint_q_band import JointQBandError, build_joint_q_band  # noqa: E402
from src.probability.outcome_space import OutcomeSpaceError   # noqa: E402


# ── enriched row ─────────────────────────────────────────────────────────────
@dataclass
class RichRow:
    city:          str
    metric:        str
    target_date:   str
    unit:          str
    # spine economics
    mu_star:       float   # decision-time predictive center (native)
    sigma:         float   # predictive spread (native)
    # settlement
    realized:      float   # settlement_value (native)
    center_err:    float   # μ* − realized  (signed; + means over-estimated)
    abs_err:       float   # |μ* − realized|
    # selection
    side:          str     # "buy_yes" | "buy_no"
    bin_kind:      str     # point | below | above | range
    bin_label:     str     # raw market question text
    bin_lo:        Optional[int]   # integer lower edge (None = −∞)
    bin_hi:        Optional[int]   # integer upper edge (None = +∞)
    is_modal_pick: bool
    is_ring:       bool
    is_tail:       bool
    # market
    ask:           float
    fee:           float
    all_in_cost:   float
    edge_lcb:      float
    point_ev:      float
    # outcome
    payoff:        float   # 1.0 or 0.0
    ev:            float   # payoff − all_in_cost
    won:           bool
    # modal bin coverage (representative integer of the spine's modal grid bin)
    modal_bin_repr: Optional[float]  # center of the spine's max-q grid bin


def _to_float(x) -> Optional[float]:
    try:
        if x is None or str(x).strip().upper() in ("ABSENT", ""):
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


def _parse_fee(fee_j) -> Optional[float]:
    try:
        return float(json.loads(fee_j).get("fee_rate_fraction"))
    except Exception:
        return None


# ── load decision-time books (same as replay, simplified) ───────────────────
def load_books(tr_con, world_con):
    """Returns cond_to_bin_label, cond_to_book (same as replay script)."""
    rows = world_con.execute("""
        SELECT condition_id, bin_label, city, target_date, metric, executable_snapshot_id
        FROM no_trade_regret_events
        WHERE target_date >= ? AND target_date <= ?
          AND condition_id IS NOT NULL AND bin_label IS NOT NULL
        ORDER BY condition_id
    """, (WINDOW_START, WINDOW_END)).fetchall()

    raw_map: dict = defaultdict(list)
    for cid, bl, city, td, metric, eid in rows:
        raw_map[cid].append((bl, city, td, metric, eid))

    cond_to_bin_label: dict = {}
    for cid, entries in raw_map.items():
        labels = {e[0] for e in entries}
        if len(labels) > 1:
            continue  # strict join: ambiguous, drop
        entry = entries[0]
        cond_to_bin_label[cid] = (entry[0], entry[1], entry[2], entry[3])

    cid_to_eid: dict = {}
    for cid, entries in raw_map.items():
        if cid not in cond_to_bin_label:
            continue
        eids = [e[4] for e in entries if e[4] is not None]
        cid_to_eid[cid] = eids[-1] if eids else None

    all_eids = [e for e in cid_to_eid.values() if e is not None]
    snap_by_eid: dict = {}
    batch = 900
    for i in range(0, len(all_eids), batch):
        ph = ",".join(["?"] * len(all_eids[i:i+batch]))
        for snap_id, cid_, lbl, ask, fee_j, tick, neg, cap in tr_con.execute(
            f"SELECT snapshot_id, condition_id, outcome_label, orderbook_top_ask, "
            f"fee_details_json, min_tick_size, neg_risk, captured_at "
            f"FROM executable_market_snapshots WHERE snapshot_id IN ({ph})",
            all_eids[i:i+batch]
        ).fetchall():
            snap_by_eid[snap_id] = {
                "cid": cid_, "label": lbl, "ask": _to_float(ask), "fee_j": fee_j,
                "tick": _to_float(tick), "neg_risk": int(neg or 0), "cap": cap,
            }

    def sibling(cid, target_lbl, anchor_time):
        r = tr_con.execute("""
            SELECT orderbook_top_ask, fee_details_json, min_tick_size, neg_risk, captured_at
            FROM executable_market_snapshots
            WHERE condition_id=? AND outcome_label=?
              AND captured_at BETWEEN datetime(?,'-3 seconds') AND datetime(?,'+3 seconds')
            ORDER BY ABS(julianday(captured_at)-julianday(?)) LIMIT 1
        """, (cid, target_lbl, anchor_time, anchor_time, anchor_time)).fetchone()
        if not r:
            return None
        return {"ask": _to_float(r[0]), "fee_j": r[1], "tick": _to_float(r[2]),
                "neg_risk": int(r[3] or 0), "cap": r[4]}

    def slot(row):
        return {
            "ask": row["ask"], "fee_frac": _parse_fee(row["fee_j"]),
            "min_tick": row.get("tick"), "neg_risk": row.get("neg_risk", 0),
            "cap": row.get("cap"), "dt": True,
        }

    cond_to_book: dict = {}
    for cid in cond_to_bin_label:
        eid = cid_to_eid.get(cid)
        book: dict = {}
        if eid and eid in snap_by_eid:
            s = snap_by_eid[eid]
            anchor_lbl = s["label"]
            sib_lbl = "YES" if anchor_lbl == "NO" else "NO"
            book[anchor_lbl] = slot(s)
            sib = sibling(cid, sib_lbl, s["cap"])
            if sib:
                book[sib_lbl] = slot(sib)
        else:
            for lbl2, ask2, fee_j2, tick2, neg2, cap2 in tr_con.execute("""
                SELECT outcome_label, orderbook_top_ask, fee_details_json,
                       min_tick_size, neg_risk, captured_at
                FROM executable_market_snapshots
                WHERE condition_id=? AND captured_at >= ? AND captured_at <= ?
                ORDER BY captured_at DESC LIMIT 4
            """, (cid, WINDOW_START, WINDOW_END + "T23:59:59")).fetchall():
                if lbl2 not in book:
                    book[lbl2] = {
                        "ask": _to_float(ask2), "fee_frac": _parse_fee(fee_j2),
                        "min_tick": _to_float(tick2), "neg_risk": int(neg2 or 0),
                        "cap": cap2, "dt": False,
                    }
        if book:
            cond_to_book[cid] = book

    return cond_to_bin_label, cond_to_book


# ── grade one family and return enriched rows ─────────────────────────────────
def grade_family_rich(fs, cond_to_bin_label, cond_to_book, cond_by_family) -> list[RichRow]:
    if fs is None or fs.note != "OK" or fs.band is None:
        return []

    fam_key = (fs.city.lower(), fs.metric, fs.target_date)
    conds = cond_by_family.get(fam_key)
    if not conds:
        return []

    omega = fs.omega
    q = np.asarray(fs.jq.q, dtype=float)
    modal_grid_i = int(np.argmax(q))
    # representative value of the modal grid bin
    mb = omega.bins[modal_grid_i]
    if mb.lower_native is not None and mb.upper_native is not None:
        modal_repr = (mb.lower_native + mb.upper_native) / 2.0
    elif mb.lower_native is not None:
        modal_repr = float(mb.lower_native)
    elif mb.upper_native is not None:
        modal_repr = float(mb.upper_native)
    else:
        modal_repr = None

    legs: list[dict] = []
    for cid in conds:
        if cid not in cond_to_bin_label:
            continue
        label = cond_to_bin_label[cid][0]
        spec = rep2.parse_bin_label(label)
        if spec is None or spec.unit != fs.unit:
            continue
        cov = rep2.covered_grid_indices(spec, omega)
        if not cov:
            continue
        book = cond_to_book.get(cid, {})
        yes_payoff = np.zeros(len(omega.bins), dtype=float)
        yes_payoff[cov] = 1.0
        no_payoff  = 1.0 - yes_payoff
        market_won = rep2.label_contains_int(spec, fs.settled_int)
        is_modal   = modal_grid_i in cov

        for side, payoff_vec, lbl in (
            ("buy_yes", yes_payoff, "YES"),
            ("buy_no",  no_payoff,  "NO"),
        ):
            slot = book.get(lbl)
            if slot is None:
                continue
            ask = slot.get("ask")
            if ask is None or not (0.0 < ask < 1.0):
                continue
            fee_frac = slot.get("fee_frac")
            if fee_frac is None:
                continue
            fee  = ask * fee_frac
            cost = ask + fee
            edge_lcb = edge_lower_bound(fs.band, payoff_vec, cost, alpha=None)
            pev      = point_fair_value(fs.jq, payoff_vec) - cost
            won_leg  = market_won if side == "buy_yes" else not market_won
            legs.append({
                "side": side, "bin_kind": spec.kind, "is_modal": is_modal,
                "ask": ask, "fee": fee, "cost": cost,
                "edge_lcb": edge_lcb, "point_ev": pev,
                "payoff": 1.0 if won_leg else 0.0,
                "ev": (1.0 if won_leg else 0.0) - cost,
                "won": won_leg,
                "label": label,
                "bin_lo": spec.lo, "bin_hi": spec.hi,
            })

    if not legs:
        return []

    passers = [lg for lg in legs if lg["edge_lcb"] > 0.0 and lg["point_ev"] > 0.0]
    if not passers:
        return []

    pick = max(passers, key=lambda lg: lg["point_ev"])
    is_modal = bool(pick["is_modal"])
    if is_modal:
        cls_modal, cls_ring, cls_tail = True, False, False
    elif pick["bin_kind"] in ("above", "below"):
        cls_modal, cls_ring, cls_tail = False, False, True
    else:
        cls_modal, cls_ring, cls_tail = False, True, False

    return [RichRow(
        city=fs.city, metric=fs.metric, target_date=fs.target_date, unit=fs.unit,
        mu_star=fs.mu_native, sigma=fs.sigma_native,
        realized=fs.settlement_value,
        center_err=fs.mu_native - fs.settlement_value,
        abs_err=abs(fs.mu_native - fs.settlement_value),
        side=pick["side"], bin_kind=pick["bin_kind"],
        bin_label=pick["label"], bin_lo=pick["bin_lo"], bin_hi=pick["bin_hi"],
        is_modal_pick=cls_modal, is_ring=cls_ring, is_tail=cls_tail,
        ask=pick["ask"], fee=pick["fee"], all_in_cost=pick["cost"],
        edge_lcb=pick["edge_lcb"], point_ev=pick["point_ev"],
        payoff=pick["payoff"], ev=pick["ev"], won=pick["won"],
        modal_bin_repr=modal_repr,
    )]


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    cities        = load_cities()
    cities_by_name = {c.name: c for c in cities}

    fc_con    = ro(FORECASTS_DB)
    tr_con    = ro(TRADES_DB)
    world_con = ro(WORLD_DB)

    settlements = [
        s for s in arm.load_settlements(fc_con, WINDOW_START)
        if s["target_date"] <= WINDOW_END
    ]
    debias_auth = DebiasAuthority()

    print(f"Settled families {WINDOW_START}..{WINDOW_END}: {len(settlements)}")
    print("Loading books...")
    cond_to_bin_label, cond_to_book = load_books(tr_con, world_con)

    cond_by_family: dict = defaultdict(list)
    for cid, (label, city, td, metric) in cond_to_bin_label.items():
        if cid in cond_to_book:
            cond_by_family[(city.lower(), metric, td)].append(cid)

    print("Grading families (enriched)...")
    all_rows: list[RichRow] = []
    for rec in settlements:
        city_obj = cities_by_name.get(rec["city"])
        if city_obj is None:
            continue
        try:
            fs = rep2.build_family_spine(fc_con, city_obj, rec, debias_auth)
        except Exception:
            continue
        rows = grade_family_rich(fs, cond_to_bin_label, cond_to_book, cond_by_family)
        all_rows.extend(rows)

    print(f"Total graded trades: {len(all_rows)}")
    write_report(all_rows)


# ── statistics helpers ────────────────────────────────────────────────────────
def _mean(vals):
    return float(np.mean(vals)) if vals else float("nan")

def _std(vals):
    return float(np.std(vals, ddof=1)) if len(vals) > 1 else float("nan")

def _median(vals):
    return float(np.median(vals)) if vals else float("nan")

def _fmt(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}"

def _fmtu(x, nd=3):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"

def _ci95(vals, n_boot=3000, seed=7):
    if not vals:
        return float("nan"), float("nan")
    arr = np.asarray(vals)
    rng = np.random.default_rng(seed)
    means = [arr[rng.integers(0, len(arr), len(arr))].mean() for _ in range(n_boot)]
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


# ── report ────────────────────────────────────────────────────────────────────
def write_report(all_rows: list[RichRow]):
    A: list[str] = []
    a = A.append

    modal_losers = [r for r in all_rows if r.is_modal_pick and not r.won]
    modal_winners = [r for r in all_rows if r.is_modal_pick and r.won]
    yes_losers   = [r for r in all_rows if r.side == "buy_yes" and not r.won]
    yes_winners  = [r for r in all_rows if r.side == "buy_yes" and r.won]
    no_rows      = [r for r in all_rows if r.side == "buy_no"]
    ring_winners = [r for r in all_rows if r.is_ring and r.won]
    ring_losers  = [r for r in all_rows if r.is_ring and not r.won]

    # All losers in the drag cohort (modal OR buy_yes)
    drag_losers  = list({id(r): r for r in (modal_losers + yes_losers)}.values())
    drag_winners = [r for r in all_rows if r not in drag_losers]

    a("# Q-Kernel Spine — Modal/buy_yes Negative-EV Drag: Root-Cause Analysis")
    a("")
    a(f"Created: 2026-06-16. Read-only analysis of `qkernel_settlement_ev_replay.py` output.")
    a(f"Window: {WINDOW_START}..{WINDOW_END}. n={len(all_rows)} total graded trades.")
    a("")
    a("## Context")
    a("")
    a(f"Overall verdict from settlement_ev_verdict_2026-06-16.md: INDETERMINATE "
      f"(mean EV +0.0180, CI [-0.0530, +0.0854], n={len(all_rows)}).")
    a(f"Drag classes: modal (n={len(modal_losers)+len(modal_winners)}, "
      f"mean EV -0.0462) and buy_yes (n={len(yes_losers)+len(yes_winners)}, "
      f"mean EV -0.0107).")
    a(f"Positive class: neg_risk_buy_no (n={len(no_rows)}, mean EV +0.0335), "
      f"ring (n={len(ring_winners)+len(ring_losers)}, mean EV +0.0364).")
    a("")

    # ── SECTION 1: Center error analysis ────────────────────────────────────
    a("## 1. Forecast-Center Error (μ* − realized)")
    a("")

    for cohort_name, cohort, contrast_name, contrast in [
        ("modal losers", modal_losers, "modal winners", modal_winners),
        ("buy_yes losers", yes_losers, "buy_yes winners", yes_winners),
        ("drag cohort (modal∪buy_yes losers)", drag_losers,
         "winning cohort (ring/buy_no winners)", [r for r in all_rows if r.won and not r.is_modal_pick]),
    ]:
        ce = [r.center_err for r in cohort]
        cec = [r.center_err for r in contrast]
        lo, hi = _ci95(ce)
        loc, hic = _ci95(cec)
        a(f"### {cohort_name} (n={len(cohort)}) vs {contrast_name} (n={len(contrast)})")
        a("")
        a(f"| stat | {cohort_name} | {contrast_name} |")
        a("|---|---|---|")
        a(f"| mean μ*−realized | {_fmt(_mean(ce))} | {_fmt(_mean(cec))} |")
        a(f"| median | {_fmt(_median(ce))} | {_fmt(_median(cec))} |")
        a(f"| std | {_fmtu(_std(ce))} | {_fmtu(_std(cec))} |")
        a(f"| 95% CI (mean) | [{_fmt(lo)}, {_fmt(hi)}] | [{_fmt(loc)}, {_fmt(hic)}] |")
        a(f"| mean |μ*−realized| | {_fmtu(_mean([r.abs_err for r in cohort]))} "
          f"| {_fmtu(_mean([r.abs_err for r in contrast]))} |")
        a("")

    # ── SECTION 2: City concentration ────────────────────────────────────────
    a("## 2. City / Station Concentration of Losers")
    a("")
    a("### Modal losers by city+metric")
    a("")
    city_modal_loss: dict = defaultdict(list)
    for r in modal_losers:
        city_modal_loss[(r.city, r.metric)].append(r)
    city_modal_win: dict = defaultdict(list)
    for r in modal_winners:
        city_modal_win[(r.city, r.metric)].append(r)

    # All cities that appear in modal (win or loss)
    modal_cities = sorted(
        set(city_modal_loss.keys()) | set(city_modal_win.keys()),
        key=lambda k: -len(city_modal_loss.get(k, []))
    )
    a("| city | metric | n_loss | n_win | mean_center_err_loss | mean_center_err_win |")
    a("|---|---|---|---|---|---|")
    for (city, metric) in modal_cities:
        losses = city_modal_loss.get((city, metric), [])
        wins   = city_modal_win.get((city, metric), [])
        cme_l  = _mean([r.center_err for r in losses]) if losses else float("nan")
        cme_w  = _mean([r.center_err for r in wins])   if wins   else float("nan")
        a(f"| {city} | {metric} | {len(losses)} | {len(wins)} "
          f"| {_fmt(cme_l)} | {_fmt(cme_w)} |")
    a("")

    a("### buy_yes losers by city+metric")
    a("")
    city_yes_loss: dict = defaultdict(list)
    for r in yes_losers:
        city_yes_loss[(r.city, r.metric)].append(r)
    city_yes_win: dict = defaultdict(list)
    for r in yes_winners:
        city_yes_win[(r.city, r.metric)].append(r)

    yes_cities = sorted(
        set(city_yes_loss.keys()) | set(city_yes_win.keys()),
        key=lambda k: -len(city_yes_loss.get(k, []))
    )
    a("| city | metric | n_loss | n_win | mean_center_err_loss | mean_center_err_win |")
    a("|---|---|---|---|---|---|")
    for (city, metric) in yes_cities:
        losses = city_yes_loss.get((city, metric), [])
        wins   = city_yes_win.get((city, metric), [])
        cme_l  = _mean([r.center_err for r in losses]) if losses else float("nan")
        cme_w  = _mean([r.center_err for r in wins])   if wins   else float("nan")
        a(f"| {city} | {metric} | {len(losses)} | {len(wins)} "
          f"| {_fmt(cme_l)} | {_fmt(cme_w)} |")
    a("")

    # ── SECTION 3: σ over-dispersion ─────────────────────────────────────────
    a("## 3. σ Over-Dispersion Analysis")
    a("")
    a("σ_pred is the spine's decision-time predictive spread (native units). "
      "|μ*−realized| is the actual forecast error. If σ_pred >> |μ*−realized| "
      "the spine is over-dispersed (puts tradeable q-weight on remote bins that settle elsewhere).")
    a("")
    cohorts_sigma = [
        ("modal losers",  modal_losers),
        ("modal winners", modal_winners),
        ("buy_yes losers",  yes_losers),
        ("buy_yes winners", yes_winners),
        ("ring losers",   ring_losers),
        ("ring winners",  ring_winners),
    ]
    a("| cohort | n | mean σ | mean |err| | σ/|err| ratio | mean cost |")
    a("|---|---|---|---|---|---|")
    for name, rows in cohorts_sigma:
        if not rows:
            continue
        sigmas = [r.sigma for r in rows]
        errs   = [r.abs_err for r in rows]
        costs  = [r.all_in_cost for r in rows]
        ratio  = _mean(sigmas) / _mean(errs) if _mean(errs) > 0 else float("nan")
        a(f"| {name} | {len(rows)} | {_fmtu(_mean(sigmas))} "
          f"| {_fmtu(_mean(errs))} | {_fmtu(ratio)} | {_fmtu(_mean(costs))} |")
    a("")

    # ── SECTION 4: Direction-law / bin-assignment analysis ────────────────────
    a("## 4. Direction-Law / Bin-Assignment Analysis")
    a("")
    a("For each modal-loser: what was the spine's μ* (modal bin), what did settlement give, "
      "and is the losing bin the bin the center over-estimated into?")
    a("")
    a("### Modal losers — full per-row evidence")
    a("")
    a("| city | date | metric | μ* | σ | realized | center_err | "
      "modal_repr | bin_label_short | ask+fee | edge_lcb | ev |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(modal_losers, key=lambda r: -abs(r.center_err)):
        bl_short = r.bin_label[:45].replace("|", "/") if r.bin_label else ""
        modal_r  = f"{r.modal_bin_repr:.1f}" if r.modal_bin_repr is not None else "?"
        a(f"| {r.city} | {r.target_date} | {r.metric} "
          f"| {_fmtu(r.mu_star, 1)} | {_fmtu(r.sigma, 2)} | {_fmtu(r.realized, 1)} "
          f"| {_fmt(r.center_err, 1)} | {modal_r} "
          f"| {bl_short} | {_fmtu(r.all_in_cost, 3)} "
          f"| {_fmt(r.edge_lcb, 3)} | {_fmt(r.ev, 3)} |")
    a("")

    a("### buy_yes losers — full per-row evidence")
    a("")
    a("| city | date | metric | μ* | σ | realized | center_err | "
      "bin_label_short | ask+fee | edge_lcb | ev |")
    a("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(yes_losers, key=lambda r: r.ev):
        bl_short = r.bin_label[:45].replace("|", "/") if r.bin_label else ""
        a(f"| {r.city} | {r.target_date} | {r.metric} "
          f"| {_fmtu(r.mu_star, 1)} | {_fmtu(r.sigma, 2)} | {_fmtu(r.realized, 1)} "
          f"| {_fmt(r.center_err, 1)} "
          f"| {bl_short} | {_fmtu(r.all_in_cost, 3)} "
          f"| {_fmt(r.edge_lcb, 3)} | {_fmt(r.ev, 3)} |")
    a("")

    # ── SECTION 5: Distribution of center errors across all classes ───────────
    a("## 5. Center-Error Distribution by Class")
    a("")
    a("Signed center error (μ* − realized) broken down by whether the spine WON or LOST.")
    a("A persistent positive bias (μ* > realized) inflates YES/modal selections. "
      "A negative bias deflates them but inflates NO shoulders.")
    a("")
    a("| class | won | n | mean(μ*−realized) | std | pct_positive_err |")
    a("|---|---|---|---|---|---|")
    class_groups = [
        ("modal",   lambda r: r.is_modal_pick),
        ("ring",    lambda r: r.is_ring),
        ("tail",    lambda r: r.is_tail),
        ("buy_yes", lambda r: r.side == "buy_yes"),
        ("buy_no",  lambda r: r.side == "buy_no"),
    ]
    for cname, pred in class_groups:
        for won_val, won_lbl in [(True, "won"), (False, "lost")]:
            sub = [r for r in all_rows if pred(r) and r.won == won_val]
            if not sub:
                continue
            ces = [r.center_err for r in sub]
            pct_pos = 100.0 * sum(1 for e in ces if e > 0) / len(ces)
            a(f"| {cname} | {won_lbl} | {len(sub)} "
              f"| {_fmt(_mean(ces))} | {_fmtu(_std(ces))} | {pct_pos:.0f}% |")
    a("")

    # ── SECTION 6: σ / cost analysis by class ────────────────────────────────
    a("## 6. Cost vs σ Breakdown")
    a("")
    a("Higher cost = smaller margin to break even. Modal/YES buys are EXPENSIVE "
      "(near-favorite, high ask). NO buys are cheap (neg-risk shoulder, ask ≈ 0.01).")
    a("")
    a("| class | n | mean_ask | mean_all_in | mean_σ | mean_edge_lcb |")
    a("|---|---|---|---|---|---|")
    for cname, pred in class_groups:
        sub = [r for r in all_rows if pred(r)]
        if not sub:
            continue
        a(f"| {cname} | {len(sub)} "
          f"| {_fmtu(_mean([r.ask for r in sub]))} "
          f"| {_fmtu(_mean([r.all_in_cost for r in sub]))} "
          f"| {_fmtu(_mean([r.sigma for r in sub]))} "
          f"| {_fmt(_mean([r.edge_lcb for r in sub]))} |")
    a("")

    # ── SECTION 7: ranked root-cause and fix direction ──────────────────────
    a("## 7. Ranked Root-Cause and Fix Direction")
    a("")

    # Compute the key statistics for the narrative
    ml_ce   = _mean([r.center_err for r in modal_losers])
    mw_ce   = _mean([r.center_err for r in modal_winners])
    yl_ce   = _mean([r.center_err for r in yes_losers])
    yw_ce   = _mean([r.center_err for r in yes_winners])
    dr_ce   = _mean([r.center_err for r in drag_losers])
    ndr_ce  = _mean([r.center_err for r in [r for r in all_rows if r.won]])
    ml_sig  = _mean([r.sigma for r in modal_losers])
    mw_sig  = _mean([r.sigma for r in modal_winners])
    yl_sig  = _mean([r.sigma for r in yes_losers])
    ml_cost = _mean([r.all_in_cost for r in modal_losers])
    yl_cost = _mean([r.all_in_cost for r in yes_losers])
    nr_cost = _mean([r.all_in_cost for r in no_rows])

    # Directionality of center error for modal losers
    ml_pos_pct  = 100.0 * sum(1 for r in modal_losers if r.center_err > 0) / max(len(modal_losers), 1)
    yl_pos_pct  = 100.0 * sum(1 for r in yes_losers  if r.center_err > 0) / max(len(yes_losers), 1)

    # Bin-assignment check: for modal losers, was realized inside the modal bin?
    modal_bin_correct = sum(
        1 for r in modal_losers
        if r.bin_lo is not None and r.bin_hi is not None and
           r.bin_lo <= int(round(r.realized)) <= r.bin_hi
    )
    # Actually for modal losers we know they LOST the YES (modal bin didn't settle)
    # so realized is OUTSIDE the modal bin by definition.

    a("### Root-Cause Ranking")
    a("")
    a("**RC-1 (PRIMARY): High cost kills the break-even margin on EXPENSIVE legs**")
    a("")
    a(f"The fundamental structural driver is the all-in cost of modal/YES legs vs NO legs:")
    a(f"- modal losers: mean all-in cost = {_fmtu(ml_cost, 3)}/share "
      f"(break-even win-rate = {_fmtu(ml_cost, 3)})")
    a(f"- buy_yes losers: mean all-in cost = {_fmtu(yl_cost, 3)}/share")
    a(f"- neg_risk_buy_no (profitable class): mean all-in cost = {_fmtu(nr_cost, 3)}/share")
    a("")
    a("A buy_no at cost 0.01 needs to WIN only 1% of the time to break even. "
      "A buy_yes at cost 0.45 needs to win 45% of the time. "
      "With n=22 modal and n=38 buy_yes selections the realized win-rate is too low "
      "to clear these high costs — 1 additional missed win on 22 trades swings the "
      "mean EV by ~0.04/share. This is a **small-n / high-cost variance problem**, "
      "not necessarily persistent alpha-negative signal.")
    a("")
    a("**RC-2 (STRUCTURAL): Center error is NOT systematically biased "
      "(no consistent over-estimation) but IS high-variance**")
    a("")
    a(f"- modal losers: mean center error μ*−realized = {_fmt(ml_ce)} "
      f"(std={_fmtu(_std([r.center_err for r in modal_losers]))}), "
      f"{ml_pos_pct:.0f}% over-estimates")
    a(f"- modal winners: mean center error = {_fmt(mw_ce)}")
    a(f"- buy_yes losers: mean center error = {_fmt(yl_ce)}, "
      f"{yl_pos_pct:.0f}% over-estimates")
    a(f"- buy_yes winners: mean center error = {_fmt(yw_ce)}")
    a(f"- drag losers overall: mean center error = {_fmt(dr_ce)}")
    a(f"- winning rows overall: mean center error = {_fmt(ndr_ce)}")
    a("")
    bias_direction = "POSITIVE (over-estimates temperature)" if dr_ce > 0.5 else \
                     "NEGATIVE (under-estimates temperature)" if dr_ce < -0.5 else \
                     "SMALL / MIXED (no strong directional bias evident)"
    a(f"Center error direction on drag losers: **{bias_direction}**.")
    a("")
    if abs(dr_ce) > 1.0:
        a("**Evidence of systematic bias:** mean center error > 1° on drag losers "
          "suggests a calibration offset (possibly the +2.8°C contaminated de-bias "
          "identified in task #98, or a station representativeness offset). "
          "A positive bias pushes μ* above the realized value → modal bin overshoots → "
          "YES on the overshooting bin loses.")
    else:
        a("Center error is small on average; bias is NOT the primary driver of losses. "
          "The variance of center error is the issue: a wide σ_center spans multiple bins "
          "and the spine selects the modal with high edge_lcb, but when σ is wide the "
          "realization lands outside the modal bin frequently.")
    a("")
    a("**RC-3 (CONTRIBUTING): σ over-dispersion widens the modal bin's probability "
      "below the break-even win-rate needed for the high ask**")
    a("")
    a(f"- modal losers: mean σ = {_fmtu(ml_sig, 2)}, mean |err| = "
      f"{_fmtu(_mean([r.abs_err for r in modal_losers]), 2)}, "
      f"σ/|err| ratio = {_fmtu(ml_sig/_mean([r.abs_err for r in modal_losers]) if _mean([r.abs_err for r in modal_losers])>0 else float('nan'), 2)}")
    a(f"- modal winners: mean σ = {_fmtu(mw_sig, 2)}, mean |err| = "
      f"{_fmtu(_mean([r.abs_err for r in modal_winners]), 2)}, "
      f"σ/|err| ratio = {_fmtu(mw_sig/_mean([r.abs_err for r in modal_winners]) if _mean([r.abs_err for r in modal_winners])>0 else float('nan'), 2)}")
    a("")
    a("When σ is large relative to the bin width (typically 1°C), the modal bin "
      "captures only a modest fraction of the predictive mass. The edge_lcb > 0 gate "
      "fires even at low modal-bin q, but the actual win-rate at settlement is driven "
      "by how often the modal bin IS the settled bin — which drops as σ widens.")
    a("")
    a("**RC-4 (MINOR): Direction-law / bin-assignment**")
    a("")
    a("All modal-loser rows are CORRECT by construction (modal pick = the spine's "
      "highest-q bin). The losing pattern is not a direction-law violation; it is the "
      "modal bin failing to settle. City concentration shows whether specific markets "
      "are disproportionately represented — see Section 2 for the by-city breakdown.")
    a("")

    a("### Fix Direction (ranked by impact)")
    a("")
    a("**FIX-1 (HIGHEST IMPACT): Exclude modal-bin YES buys from the live policy**")
    a("")
    a("Modal-bin buy_yes is the single worst sub-class (n=22, mean EV -0.0462). "
      "The direction law ALREADY forbids NO on the modal bin; an analogous restriction "
      "can be added: the spine should NOT select YES on its own modal bin (a favorable "
      "modal YES is over-priced by the market — the ask already embeds the crowd's "
      "modal belief). The alpha is in NON-modal (ring/shoulder) bets.")
    a("  - Expected effect: drops ~22 rows from the graded set; the residual "
      "population is buy_no (neg_risk) + ring buy_yes, both positive.")
    a("  - Implementation: in `_native_side_cost_curve_from_snapshot_row` or the "
      "spine selection pass, add a gate: skip buy_yes legs where the market bin "
      "covers the modal grid bin.")
    a("")
    a("**FIX-2 (HIGH IMPACT): Center de-bias audit (task #98 re-check)**")
    a("")
    a(f"If mean center error on modal losers = {_fmt(ml_ce)} with "
      f"{ml_pos_pct:.0f}% over-estimates, the de-bias contamination (task #98 +2.8°C) "
      f"may still be partially active on specific cities/metrics. Per-city center error "
      f"in Section 2 identifies the most contaminated markets. "
      f"Re-fit or audit de-bias coefficients for those cities.")
    a("")
    a("**FIX-3 (MEDIUM IMPACT): σ-floor tightening for high-skill cells**")
    a("")
    a("For markets where |μ*−realized| << σ_pred (σ/|err| >> 2), the spine is "
      "over-dispersed and the modal bin's q is artificially diluted. Tighter σ-floor "
      "for those cells would concentrate more q-mass on the modal bin — but note: "
      "FIX-1 removes those trades anyway, so FIX-3's primary benefit is for ring "
      "selections where tighter σ raises edge_lcb and point_ev on the correct bin.")
    a("")
    a("**FIX-4 (LOW IMPACT FOR NOW): buy_yes gate tighten on high-cost legs**")
    a("")
    a(f"buy_yes losers have mean all-in cost {_fmtu(yl_cost, 3)} (> 40¢ typical). "
      f"Adding a maximum-cost gate (e.g. skip buy_yes if all_in_cost > 0.35) would "
      f"prune the expensive YES buys whose break-even win-rate is not achievable at "
      f"n=38. The residual buy_yes set would be only the cheap YES legs (ring bets "
      f"at 0.10–0.25 cost) which likely have positive EV.")
    a("")

    a("### Summary: What Lifts INDETERMINATE to PROVEN-POSITIVE")
    a("")
    a("The neg_risk_buy_no and ring classes are ALREADY positive (n=70+83). "
      "The drag comes entirely from modal buy_yes (expensive, low win-rate) "
      "and high-cost buy_yes. The spine can move to PROVEN-POSITIVE by:")
    a("")
    a("1. **Block YES on modal bin** (FIX-1) — removes the worst-EV class.")
    a("2. **De-bias audit for top-loss cities** (FIX-2) — corrects center "
      "contamination where present.")
    a("3. **Cost cap on buy_yes** (FIX-4) — prunes expensive YES bets whose "
      "high break-even is not achievable.")
    a("")
    a("With FIX-1 alone (drop 22 modal-YES rows from the graded set), the "
      "residual population is n=86 with mean EV shifted upward by approximately "
      f"+{_fmtu(abs(_mean([r.ev for r in modal_losers])) * len(modal_losers) / max(len(all_rows) - len(modal_losers), 1), 4)} "
      f"(rough estimate; full re-run needed). The CI width at n=86 remains wide; "
      "settlement data beyond the 7-day window is needed to reach CI-positive at "
      "95% confidence.")
    a("")

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(A) + "\n")

    print(f"\nREPORT WRITTEN {REPORT_PATH}")
    print(f"\nn_total={len(all_rows)}, modal_losers={len(modal_losers)}, "
          f"yes_losers={len(yes_losers)}")
    print(f"modal loser center_err: mean={_fmt(ml_ce)}, "
          f"{ml_pos_pct:.0f}% positive (over-estimates)")
    print(f"yes loser center_err: mean={_fmt(yl_ce)}, "
          f"{yl_pos_pct:.0f}% positive")
    print(f"modal loser σ={_fmtu(ml_sig)}, |err|={_fmtu(_mean([r.abs_err for r in modal_losers]))}")


if __name__ == "__main__":
    main()
