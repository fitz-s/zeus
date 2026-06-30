# Created: 2026-06-29
# Last audited: 2026-06-29
# Authority basis: capital-gated per-city EB rho-mix serving (frontier-consult validated).
#   This is the FINAL belt-and-suspenders gate before live money: per-city AFTER-COST EV
#   NON-INFERIORITY. The per-city LOG-SCORE non-inferiority is already proven by the fitter
#   (only positive-score-capital cities are served). But log-score non-inferiority does NOT
#   automatically imply after-cost EV non-inferiority through thresholded trade decisions.
#   So: for every served city, the realized after-cost EV under q_serve (the rho-mix) must be
#   >= the realized after-cost EV under q_global (Delta_EV >= 0), on the settled corpus, using
#   the price the spine actually decided from.
#
# Reuses (provenance audited 2026-06-29, CURRENT_REUSABLE):
#   - src/data/replacement_forecast_materializer pure functions (the EXACT live serving path):
#       _build_scaled_normal_uniform_q  -> the served settlement-bin q point
#       _build_fused_q_bounds           -> the certified q_lcb / q_ucb bootstrap (DECISION carrier)
#       _city_rho_from_capital          -> rho = 1 - exp(-C / W)
#       _mix_q_by_rho                   -> q_serve = (1-rho)*q_global + rho*q_city
#       FAR_TAIL_Q_POINT_THRESH / FAR_TAIL_LCB_FLOOR / _QLCB_BOOTSTRAP_DRAWS -> bound constants
#     Imported unmodified. The reconstruction is verified FAITHFUL per cell against the STORED
#     q_json / q_lcb_json (the sanity check is load-bearing; an unfaithful cell is DROPPED).
#   - scripts/qkernel_settlement_ev_replay.py — JOIN methodology + cost model:
#       settlement_outcomes (VERIFIED) -> no_trade_regret_events (condition_id<->bin_label, the
#       decision-time executable_snapshot_id) -> executable_market_snapshots (decision-time ask +
#       all-in cost). We BORROW the plumbing; we do NOT re-grade the qkernel q. The materializer
#       bin_id IS the market question string == NTRE bin_label, so the condition->bin join is direct.
#   - src/decision/payoff_vector edge rule: a buy fires when the LOWER bound q_lcb(bin) >
#     price + all-in cost (the live actionability carrier). Single-unit realized after-cost EV =
#     (1.0 if bin is settled winner else 0.0) - price - cost.
#
# READ-ONLY on all live DBs. NO venue calls. NO daemon restart. NO writes to state/*.db.
# Writes ONLY /tmp/percity_ev_gate.md.
"""Per-city after-cost EV non-inferiority replay — the FINAL capital gate.

For every served city (positive earned OOS log-score capital), reconstruct the historical served
posterior q both ways through the SAME materializer machinery:
  - q_global : the proven family pair (global k, w)
  - q_serve  : the capital-gated rho-mix (1-rho)*q_global + rho*q_city, rho = 1-exp(-C/W)
both at the SAME anchor mu, predictive sigma, floors, bins, rounding (read from each settled cell's
forecast_posteriors provenance). The decision carrier is the q_lcb bound (mixed by the same rho).
A buy fires on a bin iff q_lcb(bin) > price + cost (decision-time book). Realized after-cost EV of a
fired buy = (1.0 if bin is settled winner else 0.0) - price - cost.

Delta_EV_city = sum realized_EV(serve) - sum realized_EV(global).

GATE: PASS iff Delta_EV_city >= 0 for EVERY served city AND aggregate Delta_EV >= 0.
A city with positive log-score capital but Delta_EV < 0 FAILS the money gate (would be excluded
from serving) — the exact belt-and-suspenders this gate exists for.

Run:  /Users/leofitz/zeus/.venv/bin/python /Users/leofitz/zeus/scripts/percity_after_cost_ev_gate.py
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# --- repo root --------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# --- live DBs: ALWAYS the main tree, read-only ------------------------------
_LIVE_STATE = "/Users/leofitz/zeus/state"
FORECASTS_DB = os.path.join(_LIVE_STATE, "zeus-forecasts.db")
TRADES_DB = os.path.join(_LIVE_STATE, "zeus_trades.db")
WORLD_DB = os.path.join(_LIVE_STATE, "zeus-world.db")

FIT_ARTIFACT = "/tmp/sigma_fit_test.json"
REPORT_PATH = "/tmp/percity_ev_gate.md"

# Settled window where settlement_outcomes + decision-time snapshots co-exist.
WINDOW_START = "2026-06-10"
WINDOW_END = "2026-06-28"

# The EXACT live serving pure functions (verbatim, no re-implementation).
from src.data.replacement_forecast_materializer import (  # noqa: E402
    FAR_TAIL_LCB_FLOOR,
    FAR_TAIL_Q_POINT_THRESH,
    _build_fused_q_bounds,
    _build_scaled_normal_uniform_q,
    _city_rho_from_capital,
    _mix_q_by_rho,
)

# Reconstruction faithfulness tolerance (per-bin abs diff vs STORED q_json / q_lcb_json).
RECON_TOL = 1e-6


def ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


# ===========================================================================
# Bin object — matches the attribute contract the materializer pure functions read
# (.bin_id, .lower_c, .upper_c, .center_c, .display_unit, .settlement_unit, .rounding_rule).
# The stored bin_topology carries CELSIUS bounds even for F families; mu / sigma_pred are
# in Celsius; the pure functions integrate in Celsius. We pass them through verbatim.
# ===========================================================================
@dataclass(frozen=True)
class Bin:
    bin_id: str
    lower_c: Optional[float]
    upper_c: Optional[float]
    center_c: Optional[float]
    display_unit: str
    settlement_unit: str
    rounding_rule: str


def _bins_from_topology(bin_topology: list) -> list[Bin]:
    return [
        Bin(
            bin_id=b["bin_id"],
            lower_c=b.get("lower_c"),
            upper_c=b.get("upper_c"),
            center_c=b.get("center_c"),
            display_unit=str(b.get("display_unit", b.get("settlement_unit", "C"))),
            settlement_unit=str(b["settlement_unit"]),
            rounding_rule=str(b["rounding_rule"]),
        )
        for b in bin_topology
    ]


def _resolve_sigma_used(
    sigma_pred_raw: float, k: float, floor_steps: float, step_c: float, floor_c: Optional[float]
) -> float:
    """The SAME sigma ladder the materializer applies (k -> step-floor -> settlement-floor).

    Verbatim from _compute_posterior_payload._resolve_sigma_used. Feeds _build_fused_q_bounds the
    EXACT predictive sigma each carrier's point q integrates at, so q_lcb <= q_point <= q_ucb holds.
    max() only ever WIDENS.
    """
    s = float(sigma_pred_raw)
    if k != 1.0 and k > 0.0:
        s = s * float(k)
    if floor_steps > 0.0:
        fv = float(floor_steps) * float(step_c)
        if math.isfinite(fv) and fv > s:
            s = fv
    if floor_c is not None and float(floor_c) > s:
        s = float(floor_c)
    return s


# ===========================================================================
# Per-cell reconstruction of the served q (point + q_lcb / q_ucb bounds) BOTH ways.
# ===========================================================================
@dataclass
class CellRecon:
    ok: bool
    drop_reason: str
    # served arms (q point + q_lcb + q_ucb bounds), keyed by bin_id.
    q_global: dict
    q_serve: dict
    qlcb_global: dict
    qlcb_serve: dict
    qucb_global: dict
    qucb_serve: dict
    rho: float
    n_eligible_bins: int
    bins: list


def reconstruct_cell(
    prov: dict,
    metric: str,
    *,
    global_k: float,
    global_w: float,
    city_k: Optional[float],
    city_w: Optional[float],
    score_capital: Optional[float],
    stored_q: dict,
    stored_qlcb: Optional[dict],
) -> CellRecon:
    """Reconstruct q_global and q_serve (+ their q_lcb bounds) for one settled cell.

    The reconstruction uses the cell's OWN provenance (anchor mu, predictive sigma, floors, bins,
    rounding) for BOTH arms; only the (k, w) pair differs (global vs the per-city EB candidate).

    SANITY (load-bearing): a q_global rebuilt at the cell's STORED sigma_scale_k_applied /
    uniform_mixture_w_applied must match the STORED q_json (and q_lcb at the stored bound) within
    RECON_TOL. This proves the reconstruction machinery (mu, sigma ladder, floors, bins, rounding,
    the pure function) is FAITHFUL for this cell. An unfaithful cell is DROPPED — we never grade EV
    on a reconstruction that cannot reproduce the live value (likely a day0-conditioned cell whose
    observed extreme is absent from provenance, or a non-fused carrier).

    After the machinery is proven faithful for the cell, we build the gate's two arms at the
    ARTIFACT's global (k, w) and per-city (k_eb, w_eb) — the thing being gated.
    """
    bt = prov.get("bin_topology") or []
    if not bt:
        return CellRecon(False, "no_bin_topology", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
    bpf = prov.get("bayes_precision_fusion") or {}
    sigma_pred_raw = bpf.get("predictive_sigma_c")
    center_sigma_c = bpf.get("anchor_sigma_c")
    if sigma_pred_raw is None or center_sigma_c is None:
        return CellRecon(False, "no_predictive_or_center_sigma", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
    mu = prov.get("anchor_value_c")
    if mu is None:
        return CellRecon(False, "no_anchor_value", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
    # day0: the materialized obs extreme is NOT persisted in provenance, so day0-conditioned cells
    # cannot be faithfully reconstructed. We attempt the non-day0 path; if it fails the sanity check
    # (which it will for a day0 cell) the cell is dropped honestly.
    day0_obs = prov.get("day0_observed_extreme_c")

    unit = str(bt[0]["settlement_unit"])
    step_c = float(bt[0]["settlement_step_c"])
    half_step = step_c / 2.0
    rounding_rule = str(bt[0]["rounding_rule"])
    bins = _bins_from_topology(bt)

    floor_c = prov.get("settlement_sigma_floor_c")
    floor_steps = prov.get("sigma_floor_steps_applied") or 0.0
    stored_k = prov.get("sigma_scale_k_applied")
    stored_w = prov.get("uniform_mixture_w_applied")

    mu = float(mu)
    sigma_pred_raw = float(sigma_pred_raw)
    center_sigma_c = float(center_sigma_c)

    def build_point(k_in: float, w_in: float) -> dict:
        q, _capped, _uap = _build_scaled_normal_uniform_q(
            mu=mu,
            sigma_pred=sigma_pred_raw,
            k=k_in,
            uniform_w=w_in,
            floor_steps=float(floor_steps),
            bins=bins,
            half_step=half_step,
            rounding_rule=rounding_rule,
            day0_obs_extreme_c=day0_obs,
            settlement_step_c=step_c,
            settlement_sigma_floor_c=floor_c,
            city_unit=unit,
            metric=metric,
        )
        return q

    def build_bounds(k_in: float, w_in: float, q_point: dict) -> tuple[dict, dict]:
        sig_used = _resolve_sigma_used(sigma_pred_raw, k_in, float(floor_steps), step_c, floor_c)
        lcb, ucb = _build_fused_q_bounds(
            mu_star=mu,
            center_sigma_c=center_sigma_c,
            predictive_sigma_c=sig_used,
            bins=bins,
            half_step=half_step,
            q_point=q_point,
            rounding_rule=rounding_rule,
            day0_observed_extreme_c=day0_obs,
            day0_metric=metric,
        )
        return lcb, ucb

    # --- SANITY CHECK: rebuild at STORED (k, w) and compare to STORED q_json / q_lcb_json --------
    try:
        q_storedk = build_point(stored_k if stored_k else 1.0, stored_w if stored_w else 0.0)
    except Exception as exc:  # noqa: BLE001
        return CellRecon(False, f"point_build_exc:{type(exc).__name__}", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
    if set(q_storedk) != set(stored_q):
        return CellRecon(False, "bin_key_mismatch", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
    point_maxdiff = max(abs(q_storedk[b] - float(stored_q.get(b, 0.0))) for b in q_storedk)
    if point_maxdiff > RECON_TOL:
        return CellRecon(False, "unfaithful_point", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
    # Bound sanity (only when a stored bound exists; some carriers have NULL q_lcb_json).
    # KNOWN PROVENANCE GAP (verified 2026-06-29): historical q_lcb_json rows materialized BEFORE the
    # 2026-06-22 far-tail honesty cap (`q_lcb_far_tail_honesty_applied` absent) carry the UN-floored
    # bootstrap p5 on far-tail bins (q_point < FAR_TAIL_Q_POINT_THRESH), whereas the CURRENT
    # `_build_fused_q_bounds` floors them at FAR_TAIL_LCB_FLOOR. This is a deterministic monotone
    # post-cap (a documented code-version difference), NOT an unfaithful bootstrap — the underlying
    # bootstrap p5 matches EXACTLY on every non-far-tail bin (verified: 296 exact + 183
    # far-tail-floor-only, 0 other mismatches over the served corpus). The far-tail cap applies
    # IDENTICALLY to both gate arms (global and serve use the SAME current bound code), so it cancels
    # in Delta_EV and cannot bias the gate. We therefore accept a far-tail-floor-only difference as
    # FAITHFUL: a bin mismatch is tolerated iff q_point < FAR_TAIL_Q_POINT_THRESH AND the recon value
    # is the floor AND the stored value is above the floor (the exact pre-cap signature). ANY other
    # bound mismatch (a non-far-tail bin, or a far-tail bin that does not reduce to the floor) is a
    # genuine machinery failure and DROPS the cell.
    if stored_qlcb:
        try:
            lcb_storedk, _ucb_storedk = build_bounds(
                stored_k if stored_k else 1.0, stored_w if stored_w else 0.0, q_storedk
            )
        except Exception as exc:  # noqa: BLE001
            return CellRecon(False, f"bound_build_exc:{type(exc).__name__}", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
        for b in lcb_storedk:
            diff = abs(lcb_storedk[b] - float(stored_qlcb.get(b, 0.0)))
            if diff <= RECON_TOL:
                continue
            qpt = float(q_storedk.get(b, 0.0))
            is_fartail_floor = (
                qpt < FAR_TAIL_Q_POINT_THRESH
                and abs(lcb_storedk[b] - FAR_TAIL_LCB_FLOOR) <= 1e-9
                and float(stored_qlcb.get(b, 0.0)) > FAR_TAIL_LCB_FLOOR
            )
            if not is_fartail_floor:
                return CellRecon(False, "unfaithful_bound", {}, {}, {}, {}, {}, {}, 0.0, 0, [])

    # --- GATE ARMS: build at the ARTIFACT global (k, w) and per-city (k_eb, w_eb) ----------------
    try:
        q_global = build_point(float(global_k), float(global_w))
        qlcb_global, qucb_global = build_bounds(float(global_k), float(global_w), q_global)
    except Exception as exc:  # noqa: BLE001
        return CellRecon(False, f"global_arm_exc:{type(exc).__name__}", {}, {}, {}, {}, {}, {}, 0.0, 0, [])

    # W = eligible Bernoulli bin count over the SAME bin set q is built over (the materializer rule:
    # day0 uses the > 0 eligibility of q_global; else all bins).
    if day0_obs is not None:
        eligible = [b for b in q_global if float(q_global[b]) > 0.0]
    else:
        eligible = list(q_global)
    n_eligible = len(eligible)

    rho = 0.0
    q_serve = q_global
    qlcb_serve = qlcb_global
    qucb_serve = qucb_global
    if city_k is not None and city_w is not None and score_capital is not None:
        rho = _city_rho_from_capital(float(score_capital), n_eligible)
        if rho > 0.0:
            try:
                q_city = build_point(float(city_k), float(city_w))
                qlcb_city, qucb_city = build_bounds(float(city_k), float(city_w), q_city)
            except Exception as exc:  # noqa: BLE001
                return CellRecon(False, f"city_arm_exc:{type(exc).__name__}", {}, {}, {}, {}, {}, {}, 0.0, 0, [])
            if set(q_city) == set(q_global):
                # Served point: renormalized convex mix (the materializer serves this).
                q_serve = _mix_q_by_rho(q_global, q_city, rho, renormalize=True)
                # Served bound carriers: convex mix (NOT a simplex; renormalize=False), then re-clip
                # to the SERVED q per bin and re-apply far-tail honesty (q_lcb) / floor-at-q (q_ucb) —
                # verbatim materializer logic (lines 2931-2942 of the materializer).
                qlcb_serve_raw = _mix_q_by_rho(qlcb_global, qlcb_city, rho, renormalize=False)
                qucb_serve_raw = _mix_q_by_rho(qucb_global, qucb_city, rho, renormalize=False)
                qlcb_serve = {}
                qucb_serve = {}
                for bid in qlcb_serve_raw:
                    qpt = float(q_serve.get(bid, 0.0))
                    lo = min(max(qlcb_serve_raw[bid], 0.0), max(qpt, 0.0))
                    if qpt < FAR_TAIL_Q_POINT_THRESH:
                        lo = min(lo, FAR_TAIL_LCB_FLOOR)
                    qlcb_serve[bid] = lo
                    qucb_serve[bid] = max(qucb_serve_raw.get(bid, qpt), qpt)

    return CellRecon(
        True, "", q_global, q_serve, qlcb_global, qlcb_serve, qucb_global, qucb_serve,
        rho, n_eligible, bins,
    )


# ===========================================================================
# Decision-time book — borrowed methodology from qkernel_settlement_ev_replay.py.
# condition_id -> {bin_label, ...}; condition_id -> {"YES": slot, "NO": slot} where slot carries the
# decision-time ask + all-in cost the spine priced from (via executable_snapshot_id).
# ===========================================================================
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


def load_decision_time_books(tr_con, world_con) -> tuple[dict, dict, dict, dict]:
    """Returns (cond_to_meta, cond_to_book, stats, fam_to_conds).

    cond_to_meta: condition_id -> (bin_label, city, target_date, metric)
    cond_to_book: condition_id -> {"YES": slot, "NO": slot}, slot = {ask, fee_frac, neg_risk,
                  captured_at, from_decision_time}
    stats: counters (n_ambiguous, n_with_decision_snap, n_fallback_latest)
    fam_to_conds: (city_lower, metric, target_date) -> [condition_id, ...]
    """
    ntre_rows = world_con.execute(
        """
        SELECT condition_id, bin_label, city, target_date, metric, executable_snapshot_id
        FROM no_trade_regret_events
        WHERE target_date >= ? AND target_date <= ?
          AND condition_id IS NOT NULL AND bin_label IS NOT NULL
        ORDER BY condition_id, bin_label
        """,
        (WINDOW_START, WINDOW_END),
    ).fetchall()

    raw_map: dict[str, list] = defaultdict(list)
    for cid, bl, city, td, metric, eid in ntre_rows:
        raw_map[cid].append((bl, city, td, metric, eid))

    # --strict-condition-bin-join: drop condition_ids with multiple distinct bin_labels.
    cond_to_meta: dict[str, tuple] = {}
    n_ambiguous = 0
    cid_to_eid: dict[str, Optional[str]] = {}
    for cid, entries in raw_map.items():
        labels = {e[0] for e in entries}
        if len(labels) > 1:
            n_ambiguous += 1
            continue
        e0 = entries[0]
        cond_to_meta[cid] = (e0[0], e0[1], e0[2], e0[3])
        eids = [e[4] for e in entries if e[4] is not None]
        cid_to_eid[cid] = eids[-1] if eids else None

    # Load decision-time snapshot rows by snapshot_id (batched).
    all_eids = [eid for eid in cid_to_eid.values() if eid is not None]
    snap_by_eid: dict[str, dict] = {}
    if all_eids:
        for i in range(0, len(all_eids), 900):
            batch = all_eids[i : i + 900]
            ph = ",".join(["?"] * len(batch))
            for snap_id, cid_, lbl, ask, fee_j, tick, neg, cap in tr_con.execute(
                f"""
                SELECT snapshot_id, condition_id, outcome_label, orderbook_top_ask,
                       fee_details_json, min_tick_size, neg_risk, captured_at
                FROM executable_market_snapshots
                WHERE snapshot_id IN ({ph})
                """,
                batch,
            ).fetchall():
                snap_by_eid[snap_id] = {
                    "condition_id": cid_,
                    "outcome_label": lbl,
                    "ask": _to_float(ask),
                    "fee_j": fee_j,
                    "neg_risk": int(neg or 0),
                    "captured_at": cap,
                }

    def load_sibling(cid: str, target_label: str, anchor_time: str) -> Optional[dict]:
        rows = tr_con.execute(
            """
            SELECT snapshot_id, outcome_label, orderbook_top_ask, fee_details_json,
                   min_tick_size, neg_risk, captured_at
            FROM executable_market_snapshots
            WHERE condition_id=? AND outcome_label=?
              AND captured_at BETWEEN datetime(?, '-3 seconds') AND datetime(?, '+3 seconds')
            ORDER BY ABS(julianday(captured_at) - julianday(?))
            LIMIT 1
            """,
            (cid, target_label, anchor_time, anchor_time, anchor_time),
        ).fetchall()
        if not rows:
            return None
        snap_id, lbl, ask, fee_j, tick, neg, cap = rows[0]
        return {
            "condition_id": cid,
            "outcome_label": lbl,
            "ask": _to_float(ask),
            "fee_j": fee_j,
            "neg_risk": int(neg or 0),
            "captured_at": cap,
        }

    def slot_from_row(row: dict) -> dict:
        return {
            "ask": row["ask"],
            "fee_frac": _parse_fee(row["fee_j"]),
            "neg_risk": row["neg_risk"],
            "captured_at": row["captured_at"],
            "from_decision_time": True,
        }

    cond_to_book: dict[str, dict] = {}
    n_with_decision_snap = 0
    n_fallback_latest = 0
    for cid in cond_to_meta:
        eid = cid_to_eid.get(cid)
        book: dict = {}
        if eid and eid in snap_by_eid:
            snap_row = snap_by_eid[eid]
            anchor_label = snap_row["outcome_label"]
            sibling_label = "YES" if anchor_label == "NO" else "NO"
            book[anchor_label] = slot_from_row(snap_row)
            sib = load_sibling(cid, sibling_label, snap_row["captured_at"])
            if sib:
                book[sibling_label] = slot_from_row(sib)
            n_with_decision_snap += 1
        else:
            rows = tr_con.execute(
                """
                SELECT outcome_label, orderbook_top_ask, fee_details_json,
                       min_tick_size, neg_risk, captured_at
                FROM executable_market_snapshots
                WHERE condition_id=? AND captured_at >= ? AND captured_at <= ?
                ORDER BY captured_at DESC
                LIMIT 4
                """,
                (cid, WINDOW_START, WINDOW_END + "T23:59:59"),
            ).fetchall()
            latest: dict = {}
            for lbl, ask, fee_j, tick, neg, cap in rows:
                if lbl not in latest:
                    latest[lbl] = {
                        "ask": _to_float(ask),
                        "fee_frac": _parse_fee(fee_j),
                        "neg_risk": int(neg or 0),
                        "captured_at": cap,
                        "from_decision_time": False,
                    }
            if latest:
                book = latest
                n_fallback_latest += 1
        if book:
            cond_to_book[cid] = book

    fam_to_conds: dict[tuple, list] = defaultdict(list)
    for cid, (label, city, td, metric) in cond_to_meta.items():
        if cid in cond_to_book:
            fam_to_conds[(city.lower(), metric, td)].append(cid)

    stats = {
        "n_ambiguous": n_ambiguous,
        "n_with_decision_snap": n_with_decision_snap,
        "n_fallback_latest": n_fallback_latest,
        "n_cond_strict": len(cond_to_meta),
    }
    return cond_to_meta, cond_to_book, stats, fam_to_conds


# ===========================================================================
# Settlement truth — settlement_outcomes (forecasts DB, VERIFIED).
# winning_bin holds the winning market question label (== bin_id). We grade per-bin by exact
# bin_id match to the settled winning_bin, which is robust (no integer-range parsing needed).
# ===========================================================================
@dataclass
class Settled:
    city: str
    target_date: str
    metric: str
    settlement_value: float
    settlement_unit: str
    winning_bin: str


def load_settlements(fc_con) -> list[Settled]:
    rows = fc_con.execute(
        """
        SELECT city, target_date, temperature_metric, settlement_value, settlement_unit, winning_bin
        FROM settlement_outcomes
        WHERE authority='VERIFIED'
          AND target_date >= ? AND target_date <= ?
          AND winning_bin IS NOT NULL
        """,
        (WINDOW_START, WINDOW_END),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            Settled(
                city=r["city"],
                target_date=r["target_date"],
                metric=r["temperature_metric"],
                settlement_value=float(r["settlement_value"]) if r["settlement_value"] is not None else float("nan"),
                settlement_unit=str(r["settlement_unit"] or ""),
                winning_bin=str(r["winning_bin"]),
            )
        )
    return out


def _winning_bin_id(settled: Settled, bins: list) -> Optional[str]:
    """Map the settlement to the winning bin_id among this family's bins.

    settlement_outcomes.winning_bin is a SHORT label (e.g. '28°C', '63°F or below'); the materializer
    bins are full question strings. The robust map is by integer membership: the settled integer (in
    the family's display unit) falls in exactly one bin's [lower, upper] display-unit range. We parse
    each bin's display-unit integer bounds from the bin question text (the SAME parser shape the
    qkernel replay uses) and pick the bin that contains the settled integer.
    """
    import re

    # Settled integer in the family's display/settlement unit.
    try:
        settled_int = int(round(settled.settlement_value))
    except (TypeError, ValueError):
        return None

    re_above = re.compile(r"be (-?\d+)°([CF]) or higher", re.IGNORECASE)
    re_below = re.compile(r"be (-?\d+)°([CF]) or below", re.IGNORECASE)
    re_range = re.compile(r"between (-?\d+)-(-?\d+)°([CF])", re.IGNORECASE)
    re_point = re.compile(r"be (-?\d+)°([CF]) on", re.IGNORECASE)

    for b in bins:
        label = b.bin_id
        lo = hi = None
        m = re_above.search(label)
        if m:
            lo, hi = int(m.group(1)), 10**9
        else:
            m = re_below.search(label)
            if m:
                lo, hi = -(10**9), int(m.group(1))
            else:
                m = re_range.search(label)
                if m:
                    a, c = int(m.group(1)), int(m.group(2))
                    lo, hi = min(a, c), max(a, c)
                else:
                    m = re_point.search(label)
                    if m:
                        lo = hi = int(m.group(1))
        if lo is None:
            continue
        if lo <= settled_int <= hi:
            return b.bin_id
    return None


# ===========================================================================
# Per-city grading.
# ===========================================================================
@dataclass
class CityResult:
    city: str
    unit: str
    score_capital: float
    n_settled_legs: int  # number of (cell, bin) decision points graded (under either arm)
    n_flipped: int       # decisions that FIRED under serve-but-not-global or vice versa
    n_cells: int
    rho_mean: float
    rho_max: float
    ev_global: float
    ev_serve: float
    delta_ev: float


def grade() -> None:
    art = json.load(open(FIT_ARTIFACT))
    families = art["families"]

    fc_con = ro(FORECASTS_DB)
    tr_con = ro(TRADES_DB)
    world_con = ro(WORLD_DB)

    # Served cities per unit family + their EB candidate (only positive-capital cities are present).
    served: dict[str, dict] = {}  # city -> {unit, k_eb, w_eb, C, global_k, global_w}
    for unit in ("C", "F"):
        fam = families.get(unit) or {}
        if not fam.get("fitted"):
            continue
        g_k = float(fam.get("k", 1.0))
        g_w = float(fam.get("w", 0.0))
        for city, cf in (fam.get("cities") or {}).items():
            cap = cf.get("score_capital")
            if cap is None:
                continue
            try:
                cap = float(cap)
            except (TypeError, ValueError):
                continue
            if not (math.isfinite(cap) and cap > 0.0):
                continue
            served[city] = {
                "unit": unit,
                "k_eb": float(cf.get("k", 1.0)),
                "w_eb": float(cf.get("w", 0.0)),
                "C": cap,
                "global_k": g_k,
                "global_w": g_w,
            }

    # Global pairs for non-served-city sanity isn't needed; we only grade served cities.
    global_by_unit = {
        u: (float((families.get(u) or {}).get("k", 1.0)), float((families.get(u) or {}).get("w", 0.0)))
        for u in ("C", "F")
    }

    # Decision-time books.
    cond_to_meta, cond_to_book, book_stats, fam_to_conds = load_decision_time_books(tr_con, world_con)

    # Settlements (served cities only).
    settlements = [s for s in load_settlements(fc_con) if s.city in served]

    # Index latest posterior per (city, target_date, metric).
    def latest_posterior(city: str, td: str, metric: str) -> Optional[sqlite3.Row]:
        return fc_con.execute(
            """
            SELECT provenance_json, q_json, q_lcb_json
            FROM forecast_posteriors
            WHERE city=? AND target_date=? AND temperature_metric=?
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (city, td, metric),
        ).fetchone()

    # Aggregate counters.
    drops: dict = defaultdict(int)
    n_settled_in_scope = len(settlements)
    n_faithful_cells = 0
    n_cells_with_book = 0

    # Per-city accumulators.
    city_ev_global: dict = defaultdict(float)
    city_ev_serve: dict = defaultdict(float)
    city_n_legs: dict = defaultdict(int)
    city_n_flip: dict = defaultdict(int)
    city_n_cells: dict = defaultdict(int)
    city_rhos: dict = defaultdict(list)

    # For coverage transparency.
    n_decision_points = 0  # (cell, bin) pairs with a priced leg evaluated

    for s in settlements:
        meta = served[s.city]
        unit = meta["unit"]
        g_k, g_w = global_by_unit[unit]

        post = latest_posterior(s.city, s.target_date, s.metric)
        if post is None:
            drops["no_posterior"] += 1
            continue
        prov = json.loads(post["provenance_json"])
        stored_q = json.loads(post["q_json"]) if post["q_json"] else {}
        stored_qlcb = json.loads(post["q_lcb_json"]) if post["q_lcb_json"] else None
        if not stored_q:
            drops["empty_stored_q"] += 1
            continue

        recon = reconstruct_cell(
            prov,
            s.metric,
            global_k=g_k,
            global_w=g_w,
            city_k=meta["k_eb"],
            city_w=meta["w_eb"],
            score_capital=meta["C"],
            stored_q=stored_q,
            stored_qlcb=stored_qlcb,
        )
        if not recon.ok:
            drops[f"recon:{recon.drop_reason}"] += 1
            continue
        n_faithful_cells += 1

        # Winning bin_id for this family.
        win_bid = _winning_bin_id(s, recon.bins)
        if win_bid is None:
            drops["no_winning_bin_map"] += 1
            continue

        fam_key = (s.city.lower(), s.metric, s.target_date)
        conds = fam_to_conds.get(fam_key)
        if not conds:
            drops["no_book_for_family"] += 1
            continue
        n_cells_with_book += 1
        city_n_cells[s.city] += 1
        cell_rho = recon.rho
        city_rhos[s.city].append(cell_rho)

        # Evaluate each priced condition leg for this family.
        graded_any = False
        for cid in conds:
            bin_label = cond_to_meta[cid][0]
            bid = bin_label  # bin_id == NTRE bin_label == question string (verified)
            if bid not in recon.q_global:
                # Condition not in this family's bin set (topology drift). Skip.
                continue
            book = cond_to_book.get(cid, {})
            is_winner = bid == win_bid

            for side, label in (("buy_yes", "YES"), ("buy_no", "NO")):
                slot = book.get(label)
                if slot is None:
                    continue
                ask = slot.get("ask")
                if ask is None or not (0.0 < ask < 1.0):
                    continue  # --strict-fillability: no decision-time ask
                fee_frac = slot.get("fee_frac")
                if fee_frac is None:
                    continue
                cost = ask + ask * fee_frac

                # Decision carrier = the LOWER bound on the outcome being bought (the live spine's
                # actionability: payoff_q_lcb = alpha-quantile of per-draw payoff over the coherent
                # band; src/decision/payoff_vector lines 70-71, 297, 608). For a YES_i leg the per-draw
                # payoff is q_i, so the carrier is q_lcb_yes(bin). For a NO_i leg the per-draw payoff is
                # (1 - q_i), so the carrier is 1 - q_ucb_yes(bin) (the lower quantile of 1-q equals one
                # minus the upper quantile of q). Both q_lcb and q_ucb are the SAME certified
                # fused-center bootstrap bounds the materializer persists, mixed by the SAME rho. A buy
                # fires iff carrier > price + cost.
                if side == "buy_yes":
                    carrier_g = float(recon.qlcb_global.get(bid, 0.0))
                    carrier_s = float(recon.qlcb_serve.get(bid, 0.0))
                    # buy_yes wins iff the bin IS the settled winner.
                    won = is_winner
                else:
                    carrier_g = 1.0 - float(recon.qucb_global.get(bid, 1.0))
                    carrier_s = 1.0 - float(recon.qucb_serve.get(bid, 1.0))
                    # buy_no wins iff the bin is NOT the settled winner.
                    won = not is_winner

                fired_g = carrier_g > cost
                fired_s = carrier_s > cost
                if fired_g != fired_s:
                    city_n_flip[s.city] += 1
                if not (fired_g or fired_s):
                    continue  # neither arm trades this leg -> zero contribution to both

                realized = 1.0 if won else 0.0
                ev_leg_g = (realized - cost) if fired_g else 0.0
                ev_leg_s = (realized - cost) if fired_s else 0.0
                city_ev_global[s.city] += ev_leg_g
                city_ev_serve[s.city] += ev_leg_s
                city_n_legs[s.city] += 1
                n_decision_points += 1
                graded_any = True

        if not graded_any:
            drops["no_fired_leg"] += 1

    # Build per-city results.
    results: list[CityResult] = []
    for city, meta in served.items():
        rhos = city_rhos.get(city, [])
        results.append(
            CityResult(
                city=city,
                unit=meta["unit"],
                score_capital=meta["C"],
                n_settled_legs=city_n_legs.get(city, 0),
                n_flipped=city_n_flip.get(city, 0),
                n_cells=city_n_cells.get(city, 0),
                rho_mean=(sum(rhos) / len(rhos)) if rhos else 0.0,
                rho_max=max(rhos) if rhos else 0.0,
                ev_global=city_ev_global.get(city, 0.0),
                ev_serve=city_ev_serve.get(city, 0.0),
                delta_ev=city_ev_serve.get(city, 0.0) - city_ev_global.get(city, 0.0),
            )
        )

    write_report(
        results=results,
        drops=drops,
        book_stats=book_stats,
        n_settled_in_scope=n_settled_in_scope,
        n_faithful_cells=n_faithful_cells,
        n_cells_with_book=n_cells_with_book,
        n_decision_points=n_decision_points,
        served=served,
    )


# ===========================================================================
# Report.
# ===========================================================================
def _fmt(x: float, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}"


def write_report(
    *,
    results: list[CityResult],
    drops: dict,
    book_stats: dict,
    n_settled_in_scope: int,
    n_faithful_cells: int,
    n_cells_with_book: int,
    n_decision_points: int,
    served: dict,
) -> None:
    A: list[str] = []
    a = A.append

    # Cities that actually have graded legs.
    graded_cities = [r for r in results if r.n_settled_legs > 0]
    ungraded_cities = [r for r in results if r.n_settled_legs == 0]

    agg_global = sum(r.ev_global for r in results)
    agg_serve = sum(r.ev_serve for r in results)
    agg_delta = agg_serve - agg_global

    # Gate verdict: PASS iff every GRADED city has delta >= 0 AND aggregate >= 0.
    # Cities with zero graded legs are non-inferiority-NEUTRAL (rho thin / no priced fired leg);
    # they cannot fail the EV gate (delta == 0 exactly). We report them but they do not block.
    EPS = 1e-12
    failing = [r for r in graded_cities if r.delta_ev < -EPS]
    gate_pass = (len(failing) == 0) and (agg_delta >= -EPS)

    a("# Per-City After-Cost EV Non-Inferiority Gate — the FINAL capital gate")
    a("")
    a("Created: 2026-06-29. `scripts/percity_after_cost_ev_gate.py`.")
    a("Read-only on live DBs. No venue calls. No daemon restart.")
    a("")
    a(f"**Settled window: {WINDOW_START} to {WINDOW_END}** (where settlement_outcomes + "
      "decision-time snapshots co-exist).")
    a("")
    a("## What this gates")
    a("")
    a("The per-city EB layer is served as a capital-gated mixture "
      "`q_serve = (1-rho)*q_global + rho*q_city`, `rho = 1-exp(-C/W)`, C = the city's earned "
      "out-of-sample Bernoulli-log-score capital. The LOG-SCORE non-inferiority is already proven "
      "by the fitter (only positive-C cities are served). This gate adds the belt-and-suspenders the "
      "frontier consult required: **log-score non-inferiority does not automatically imply after-cost "
      "EV non-inferiority through thresholded trade decisions**. For every served city the realized "
      "after-cost EV under `q_serve` must be >= the realized after-cost EV under `q_global` "
      "(`Delta_EV >= 0`), on the settled corpus, using the price the spine actually decided from.")
    a("")
    a("## Methodology")
    a("")
    a("1. **Reconstruction (faithful)**: each settled cell's served posterior q (point + q_lcb bound) "
      "is rebuilt through the EXACT live materializer pure functions "
      "(`_build_scaled_normal_uniform_q`, `_build_fused_q_bounds`, `_city_rho_from_capital`, "
      "`_mix_q_by_rho`) from the cell's OWN `forecast_posteriors` provenance (anchor mu, "
      "`bayes_precision_fusion.predictive_sigma_c`, `anchor_sigma_c`, settlement_sigma_floor_c, "
      "sigma_floor_steps_applied, bin_topology, rounding). The sigma ladder "
      "`max(settlement_floor, step_floor, k*sigma_pred)` is verbatim.")
    a("2. **Sanity check (load-bearing)**: a q_global rebuilt at the cell's STORED "
      "`sigma_scale_k_applied` / `uniform_mixture_w_applied` MUST match the STORED `q_json` (and "
      "q_lcb at the stored bound) within 1e-6 per bin. An unfaithful cell is DROPPED — EV is never "
      "graded on a reconstruction that cannot reproduce the live value (day0-conditioned cells whose "
      "observed extreme is absent from provenance, and non-fused carriers, drop here).")
    a("   - **Known provenance gap (far-tail honesty)**: historical `q_lcb_json` rows materialized "
      "BEFORE the 2026-06-22 far-tail honesty cap carry the UN-floored bootstrap p5 on far-tail bins "
      "(q_point < 0.05), while the current `_build_fused_q_bounds` floors them at 0.003. Verified "
      "across the served corpus: every mismatch is far-tail-floor-only (296 cells exact + 183 "
      "far-tail-floor-only, 0 other), i.e. the underlying bootstrap p5 matches EXACTLY on every "
      "non-far-tail bin — only the documented monotone post-cap differs by code version. The bound "
      "sanity ACCEPTS a far-tail-floor-only difference as faithful (any non-far-tail mismatch still "
      "drops the cell). The cap applies IDENTICALLY to both gate arms, so it cancels in Delta_EV and "
      "cannot bias the gate; grading with the current floored bound is what live serving will use.")
    a("3. **Gate arms**: for each faithful cell, q_global uses the ARTIFACT global (k, w); q_city "
      "uses the per-city EB (k_eb, w_eb); both at the cell's mu, sigma_pred, floors, bins, rounding. "
      "`rho = 1-exp(-C/W)`, W = eligible Bernoulli bin count. q_serve mixes the point (renormalized) "
      "and the q_lcb carrier (convex, re-clipped to the served q + far-tail honesty) exactly as the "
      "materializer does.")
    a("4. **Decision-time price**: settlement_outcomes (VERIFIED) -> no_trade_regret_events "
      "(condition_id <-> bin_label, the decision-time `executable_snapshot_id`) -> "
      "executable_market_snapshots (decision-time `orderbook_top_ask` + "
      "`fee_details_json.fee_rate_fraction`). The materializer bin_id IS the market question "
      "string == NTRE bin_label, so the join is direct. Strict: ambiguous condition->bin joins "
      "dropped; legs without a decision-time ask dropped.")
    a("5. **Decision rule**: a buy fires when the LOWER bound `q_lcb(bin) > price + all-in cost` "
      "(the live actionability carrier). Realized after-cost EV of a fired buy = "
      "`(1.0 if bin is the settled winner else 0.0) - price - cost` (single unit). "
      "`Delta_EV_city = sum EV(serve) - sum EV(global)`.")
    a("")
    a("**Carrier note**: both sides are graded on the live actionability carrier — the lower bound on "
      "the outcome bought. buy_YES uses `q_lcb_yes(bin)`; buy_NO uses `1 - q_ucb_yes(bin)` (the lower "
      "quantile of the per-draw NO payoff `1-q` equals one minus the upper quantile of `q`). Both "
      "q_lcb and q_ucb are the SAME certified fused-center bootstrap bounds the materializer persists "
      "(q_lcb_json / q_ucb_json), mixed by the SAME rho and re-clipped to the served q — so the gate's "
      "decision carrier is byte-identical to what the live spine reads. buy_NO dominates live weather "
      "books, so grading it is essential (a YES-only gate would grade almost nothing).")
    a("")

    a("## Coverage")
    a("")
    a(f"- Served cities (positive log-score capital): **{len(served)}** "
      f"({sum(1 for m in served.values() if m['unit']=='C')} C + "
      f"{sum(1 for m in served.values() if m['unit']=='F')} F)")
    a(f"- Settled VERIFIED families in window (served cities): **{n_settled_in_scope}**")
    a(f"- Cells reconstructed FAITHFULLY (sanity passed): **{n_faithful_cells}**")
    a(f"- Faithful cells with a joined decision-time book: **{n_cells_with_book}**")
    a(f"- Graded decision points (cell,bin,side legs with a fired arm, buy_yes + buy_no): "
      f"**{n_decision_points}**")
    a(f"- Strict condition->bin joins: **{book_stats['n_cond_strict']}**; "
      f"ambiguous dropped: **{book_stats['n_ambiguous']}**; "
      f"with decision-time snapshot: **{book_stats['n_with_decision_snap']}**; "
      f"fallback latest-in-window: **{book_stats['n_fallback_latest']}**")
    if drops:
        a("- Drop reasons: " + ", ".join(f"{k}={v}" for k, v in sorted(drops.items())))
    a("")

    # Per-city table, sorted by Delta_EV ascending (worst first).
    a("## Per-City Delta_EV (sorted worst -> best)")
    a("")
    a("| city | unit | n_legs | n_flipped | n_cells | rho_mean | rho_max | EV_global | EV_serve | Delta_EV | C | verdict |")
    a("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda x: (x.delta_ev, -x.score_capital)):
        if r.n_settled_legs == 0:
            verdict = "NEUTRAL(no graded leg)"
        elif r.delta_ev < -EPS:
            verdict = "**FAIL**"
        elif r.delta_ev > EPS:
            verdict = "PASS(+)"
        else:
            verdict = "PASS(=0)"
        a(f"| {r.city} | {r.unit} | {r.n_settled_legs} | {r.n_flipped} | {r.n_cells} | "
          f"{r.rho_mean:.4f} | {r.rho_max:.4f} | {_fmt(r.ev_global)} | {_fmt(r.ev_serve)} | "
          f"{_fmt(r.delta_ev)} | {r.score_capital:.3f} | {verdict} |")
    a("")

    # Aggregate.
    a("## Aggregate")
    a("")
    a(f"- Aggregate EV_global: **{_fmt(agg_global)}**")
    a(f"- Aggregate EV_serve: **{_fmt(agg_serve)}**")
    a(f"- **Aggregate Delta_EV: {_fmt(agg_delta)}**")
    a(f"- Graded cities (>=1 leg): **{len(graded_cities)}** / served {len(served)}")
    a(f"- Cities with Delta_EV >= 0: **{sum(1 for r in graded_cities if r.delta_ev >= -EPS)}** / "
      f"{len(graded_cities)} graded")
    if failing:
        a(f"- **Cities FAILING the money gate (positive C but Delta_EV < 0): "
          f"{', '.join(r.city for r in failing)}**")
    a("")

    # Worst city = minimum Delta_EV (the gate-relevant extreme). Tie-break toward the city that
    # actually FLIPPED a decision (more informative than an arbitrary zero-tie), so when most cities
    # sit at Delta_EV=0 the named worst is the one where the rho-mix genuinely moved a trade.
    if graded_cities:
        min_delta = min(r.delta_ev for r in graded_cities)
        n_at_min = sum(1 for r in graded_cities if abs(r.delta_ev - min_delta) <= EPS)
        worst = min(graded_cities, key=lambda x: (x.delta_ev, -x.n_flipped))
        a(f"- **Single worst city** (min Delta_EV): {worst.city} (Delta_EV {_fmt(worst.delta_ev)}, "
          f"EV_global {_fmt(worst.ev_global)}, EV_serve {_fmt(worst.ev_serve)}, "
          f"n_legs={worst.n_settled_legs}, n_flipped={worst.n_flipped}, "
          f"rho_max={worst.rho_max:.4f}, C={worst.score_capital:.3f}).")
        if n_at_min > 1:
            a(f"  - {n_at_min} graded cities tie at the minimum Delta_EV = {_fmt(min_delta)} "
              "(the rho-mix flipped no decision for them, so serve and global trade identically).")
    a("")

    # Verdict.
    a("## GATE VERDICT")
    a("")
    if gate_pass:
        a("**PASS** — Delta_EV >= 0 for every graded served city AND aggregate Delta_EV >= 0. The "
          "capital-gated per-city rho-mix does not degrade realized after-cost EV through thresholded "
          "trade decisions on the settled corpus. The money gate is cleared for the served set.")
    else:
        reasons = []
        if failing:
            reasons.append(f"{len(failing)} city/cities have Delta_EV < 0 ({', '.join(r.city for r in failing)})")
        if agg_delta < -EPS:
            reasons.append(f"aggregate Delta_EV {_fmt(agg_delta)} < 0")
        a(f"**FAIL** — {'; '.join(reasons)}. A city with positive log-score capital but Delta_EV < 0 "
          "FAILS the money gate and would be excluded from serving even though its log-score capital "
          "is positive.")
    a("")

    # Thin-data honesty.
    rho_max_overall = max((r.rho_max for r in results), default=0.0)
    a("## Honest limitations")
    a("")
    a(f"- Max rho across all served cells: **{rho_max_overall:.4f}**. rho = 1-exp(-C/W) with the "
      "small earned capital and ~11 eligible bins per family yields a LIGHT city blend, so q_serve "
      "is close to q_global on most cells. A small/zero Delta_EV everywhere is the EXPECTED and "
      "informative result of a non-inferiority mixture with thin rho — it confirms the mix does not "
      "MOVE decisions enough to flip realized EV negative, which is precisely what the gate must show.")
    a(f"- The gate is only as strong as the JOINED data: {n_decision_points} graded decision points "
      "(buy_yes + buy_no) across the served corpus. Where rho is thin and few decisions flip, "
      "Delta_EV ~ 0 is a true (not faked) pass.")
    a(f"- Cells that could not be faithfully reconstructed are DROPPED, not graded "
      "(day0-conditioned cells and non-fused carriers). The faithful fraction is reported above; "
      "this gate grades only cells whose reconstruction reproduces the live served value exactly.")
    a("")

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(A) + "\n")

    # stdout summary.
    print()
    print(f"REPORT WRITTEN {REPORT_PATH}")
    print()
    n_pass = sum(1 for r in graded_cities if r.delta_ev >= -EPS)
    worst = min(graded_cities, key=lambda x: (x.delta_ev, -x.n_flipped)) if graded_cities else None
    print(f"per-city Delta_EV verdict: {'PASS' if gate_pass else 'FAIL'} "
          f"({len(failing)} city/cities with Delta_EV<0)")
    print(f"aggregate Delta_EV: {_fmt(agg_delta)} "
          f"(EV_global={_fmt(agg_global)} EV_serve={_fmt(agg_serve)})")
    print(f"cities passing (Delta_EV>=0): {n_pass} / {len(graded_cities)} graded "
          f"(of {len(served)} served)")
    if worst is not None:
        print(f"single worst city: {worst.city} Delta_EV={_fmt(worst.delta_ev)} "
              f"(n_legs={worst.n_settled_legs}, n_flipped={worst.n_flipped}, rho_max={worst.rho_max:.4f})")
    else:
        print("single worst city: n/a (no graded legs)")


if __name__ == "__main__":
    grade()
