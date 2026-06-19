# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: docs/rebuild/deploy_runbook.md §ARM [BLOCKER] — "current replay
#   cannot prove positive settlement-graded after-cost EV because snapshots alone
#   cannot map a selected condition to the settled bin and current EV is market-implied
#   only — concrete fix: add scripts/qkernel_settlement_ev_replay.py joining
#   settlement_outcomes to market_events topology and decision-time executable
#   snapshots, then grade selected qkernel candidates by realized payoff minus
#   all-in cost."
#
# Reuses (provenance audited 2026-06-16, CURRENT_REUSABLE):
#   - scripts/qkernel_arm_replay.py — fresh-member reconstruction, build_grid_omega,
#     settled_bin_index, season_for, DEFAULT_REGIME_KEY, REPLAY_LEAD_HOURS, ro(),
#     load_settlements(). Imported unmodified via importlib.
#   - scripts/qkernel_settlement_graded_ev.py — bin_label parsing logic
#     (parse_bin_label, label_contains_int, covered_grid_indices, BinSpec), bootstrap_ci,
#     grade_family structure. Adapted with decision-time snapshot lookup.
#   - src/decision/payoff_vector.{edge_lower_bound,point_fair_value} — live spine edge.
#   - src/contracts/settlement_semantics.SettlementSemantics.for_city — per-city rounding.
#   - src/config.load_cities — city registry.
#   - src/probability.{joint_q,joint_q_band,outcome_space} — via arm replay.
#
# Key improvement over qkernel_settlement_graded_ev.py:
#   - Uses DECISION-TIME executable snapshot (no_trade_regret_events.executable_snapshot_id
#     → executable_market_snapshots.snapshot_id) rather than the latest snapshot in the
#     window, giving the cost the spine actually priced from at decision time.
#   - Window is the specific 06-09..06-15 post-spine-enable period.
#   - Breakdown includes neg-risk buy_no (dominant class in live markets) vs others.
#   - --strict-condition-bin-join: fail-closed on ambiguous joins (report drop count).
#   - --no-day0, --no-synthetic, --no-arb, --no-conversion flags matched.
#
# Flags:
#   --strict-condition-bin-join  (always enforced; drop any condition_id whose bin_label
#                                 parse is ambiguous — never guess)
#   --strict-fillability         (drop legs whose decision-time snapshot has no ask)
#   --no-day0                    (drop families where decision is same-day as target)
#   --no-synthetic / --no-arb / --no-conversion  (not applicable: single-leg only)
#
# READ-ONLY on all live DBs. NO venue calls. NO daemon restart. NO writes to live DBs.
"""Settlement-EV replay for the q-kernel spine — definitive settlement-graded verdict.

Grading period: 2026-06-09 to 2026-06-15 (inclusive).

Join methodology:
  settlement_outcomes (VERIFIED) → family identity (city, date, metric, settlement_value)
  no_trade_regret_events         → condition_id + bin_label (--strict: 1:1, no ambiguity)
  executable_market_snapshots    → DECISION-TIME book (via executable_snapshot_id)

Run:  /Users/leofitz/zeus/.venv/bin/python \\
        /Users/leofitz/zeus/.claude/worktrees/qkernel-rebuild/scripts/qkernel_settlement_ev_replay.py

Writes docs/evidence/qkernel_rebuild/settlement_ev_verdict_2026-06-16.md
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

# --- repo root (this worktree) -----------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# --- live DBs: ALWAYS the main tree ------------------------------------------
_LIVE_STATE = "/Users/leofitz/zeus/state"
FORECASTS_DB = os.path.join(_LIVE_STATE, "zeus-forecasts.db")
TRADES_DB = os.path.join(_LIVE_STATE, "zeus_trades.db")
WORLD_DB = os.path.join(_LIVE_STATE, "zeus-world.db")

REPORT_PATH = os.path.join(
    _ROOT, "docs", "evidence", "qkernel_rebuild",
    "settlement_ev_verdict_2026-06-16.md",
)

# Window: the post-spine-enable settled period.
WINDOW_START = "2026-06-09"
WINDOW_END = "2026-06-15"

# --- import arm replay unmodified (CURRENT_REUSABLE 2026-06-16) --------------
_REPLAY_PATH = os.path.join(_SCRIPT_DIR, "qkernel_arm_replay.py")
_spec = importlib.util.spec_from_file_location("qkernel_arm_replay", _REPLAY_PATH)
arm = importlib.util.module_from_spec(_spec)
sys.modules["qkernel_arm_replay"] = arm
_spec.loader.exec_module(arm)

# Reuse replay's band params for byte-identical q/band.
N_BAND_DRAWS = arm.N_BAND_DRAWS
BAND_ALPHA = arm.BAND_ALPHA

ro = arm.ro

from src.config import load_cities  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.decision.payoff_vector import (  # noqa: E402
    edge_lower_bound,
    point_fair_value,
)
from src.forecast.debias_authority import DebiasAuthority  # noqa: E402
from src.probability.joint_q import JointQError, build_joint_q  # noqa: E402
from src.probability.joint_q_band import JointQBandError, build_joint_q_band  # noqa: E402
from src.probability.outcome_space import OutcomeSpaceError  # noqa: E402


# ===========================================================================
# Bin-label parsing (verbatim from qkernel_settlement_graded_ev.py, proven 0-unmatched).
# ===========================================================================
_RE_ABOVE = re.compile(r"be (-?\d+)°([CF]) or higher", re.IGNORECASE)
_RE_BELOW = re.compile(r"be (-?\d+)°([CF]) or below", re.IGNORECASE)
_RE_RANGE = re.compile(r"between (-?\d+)-(-?\d+)°([CF])", re.IGNORECASE)
_RE_POINT = re.compile(r"be (-?\d+)°([CF]) on", re.IGNORECASE)


@dataclass(frozen=True)
class BinSpec:
    kind: str          # "point" | "below" | "above" | "range"
    lo: Optional[int]  # inclusive integer low (None => -inf)
    hi: Optional[int]  # inclusive integer high (None => +inf)
    unit: str          # "C" | "F"


def parse_bin_label(label: str) -> Optional[BinSpec]:
    if not label:
        return None
    m = _RE_ABOVE.search(label)
    if m:
        return BinSpec("above", int(m.group(1)), None, m.group(2).upper())
    m = _RE_BELOW.search(label)
    if m:
        return BinSpec("below", None, int(m.group(1)), m.group(2).upper())
    m = _RE_RANGE.search(label)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return BinSpec("range", min(a, b), max(a, b), m.group(3).upper())
    m = _RE_POINT.search(label)
    if m:
        v = int(m.group(1))
        return BinSpec("point", v, v, m.group(2).upper())
    return None


def label_contains_int(spec: BinSpec, settled_int: int) -> bool:
    lo = spec.lo if spec.lo is not None else -(10**9)
    hi = spec.hi if spec.hi is not None else 10**9
    return lo <= settled_int <= hi


def covered_grid_indices(spec: BinSpec, omega) -> list[int]:
    """Map a market-bin BinSpec to grid-omega bin indices (Arrow-Debreu payoff support)."""
    idx: list[int] = []
    lo = spec.lo if spec.lo is not None else -(10**9)
    hi = spec.hi if spec.hi is not None else 10**9
    for i, b in enumerate(omega.bins):
        bl = b.lower_native
        bh = b.upper_native
        glo = int(math.ceil(bl)) if bl is not None else -(10**9)
        ghi = int(math.floor(bh)) if bh is not None else 10**9
        ilo = max(glo, lo)
        ihi = min(ghi, hi)
        if ilo <= ihi:
            idx.append(i)
    return idx


# ===========================================================================
# Family spine reconstruction — byte-identical to qkernel_settlement_graded_ev.py.
# Adapted from build_family_spine there (which itself is byte-identical to replay_family
# in qkernel_arm_replay.py), returning the same omega/jq/band objects.
# ===========================================================================
@dataclass
class FamilySpine:
    city: str
    metric: str
    target_date: str
    unit: str
    settlement_value: float
    omega: object
    jq: object
    band: object
    mu_native: float
    sigma_native: float
    settled_grid_index: int
    settled_int: int
    note: str


def build_family_spine(fc_con, city_obj, rec, debias_auth) -> Optional[FamilySpine]:
    """Reconstruct the spine for one settled family. Identical to qkernel_settlement_graded_ev."""
    from src.probability.event_resolution import ResolutionError, event_resolution_for_city
    from src.forecast.types import FreshModelSet, RawModelMember, ForecastCase
    from src.forecast.predictive_distribution_builder import build_predictive_distribution
    from src.forecast.sigma_authority import realized_sigma_floor

    city = rec["city"]
    metric = rec["metric"]
    target_date = rec["target_date"]
    sv = rec["settlement_value"]
    unit = rec["settlement_unit"] or city_obj.settlement_unit

    td = date.fromisoformat(target_date)
    cycle_date = (td - timedelta(days=1)).isoformat()

    # --no-day0: skip if decision cycle date == target_date (same-day / day0 families).
    # cycle_date is always target-1d here, so this check guards against any edge case.
    if cycle_date >= target_date:
        return None  # day0 — excluded by --no-day0

    members_raw = arm.fresh_members_at_cycle(fc_con, city, metric, target_date, cycle_date)
    if len(members_raw) < 3:
        return None

    try:
        resolution = event_resolution_for_city(city_obj, td, metric)
    except ResolutionError:
        return None

    def c_to_native(v_c: float) -> float:
        return v_c if unit == "C" else (v_c * 9.0 / 5.0 + 32.0)

    issue = datetime(td.year, td.month, td.day, 0, 0, tzinfo=timezone.utc) - timedelta(days=1)
    members = []
    vals = []
    for model, sct, sa, ld, val_c in members_raw:
        v_native = c_to_native(float(val_c))
        from src.forecast.types import RawModelMember
        members.append(
            RawModelMember(
                model_id=model,
                product_id=model,
                source_run_id=f"{model}:{sct}",
                source_cycle_time_utc=datetime.fromisoformat(sct),
                available_at_utc=datetime.fromisoformat(sa),
                value_native=v_native,
                station_mapping_id=city_obj.wu_station,
                raw_forecast_artifact_id="hist_replay",
                data_version="hist_replay",
            )
        )
        vals.append(v_native)
    vals = np.asarray(vals, dtype=float)

    case = ForecastCase(
        city=city,
        city_id=city,
        station_id=resolution.station_id,
        settlement_source_type=city_obj.settlement_source_type,
        target_local_date=td,
        metric=metric,
        issue_time_utc=issue,
        lead_hours=arm.REPLAY_LEAD_HOURS,
        season=arm.season_for(td),
        regime_key=arm.DEFAULT_REGIME_KEY,
        unit=unit,
        resolution=resolution,
        family_id=f"{city}|{target_date}|{metric}",
        source_cycle_time_utc=issue,
    )
    fms = FreshModelSet(
        case=case,
        members=tuple(members),
        member_values_native=vals,
        min_native=float(vals.min()),
        max_native=float(vals.max()),
        model_set_hash="hist_replay",
    )

    n_mem = int(vals.size)
    member_sd = float(np.std(vals, ddof=1)) if n_mem >= 2 else 1.0
    fused_center_sd = member_sd / math.sqrt(max(n_mem, 1)) if member_sd > 0 else 0.3
    floor_art = realized_sigma_floor(case)
    sigma_resid = float(floor_art.rmse_native) if floor_art is not None else 1.0

    pd = build_predictive_distribution(
        case, fms, debias_auth,
        obs=None, has_fusion_capture=True,
        fused_center_sd_native=fused_center_sd,
        sigma_resid_native=sigma_resid,
    )
    if not pd.live_eligible:
        return FamilySpine(city, metric, target_date, unit, sv, None, None, None,
                           pd.mu_native, pd.sigma_native, -1, -1,
                           f"INELIGIBLE:{pd.ineligibility_reason}")

    try:
        omega = arm.build_grid_omega(
            family_id=case.family_id, resolution=resolution,
            center_native=pd.mu_native, settlement_value=sv, unit=unit,
        )
    except OutcomeSpaceError as exc:
        return FamilySpine(city, metric, target_date, unit, sv, None, None, None,
                           pd.mu_native, pd.sigma_native, -1, -1, f"OMEGA_FAIL:{exc}")

    try:
        jq = build_joint_q(pd, omega)
    except JointQError as exc:
        return FamilySpine(city, metric, target_date, unit, sv, None, None, None,
                           pd.mu_native, pd.sigma_native, -1, -1, f"JOINTQ_FAIL:{exc}")

    si = arm.settled_bin_index(omega, sv)
    if si is None:
        return None

    band = None
    try:
        band = build_joint_q_band(pd, omega, n_draws=N_BAND_DRAWS, alpha=BAND_ALPHA)
        band.assert_valid()
    except (JointQBandError, AssertionError):
        band = None

    ss = SettlementSemantics.for_city(city_obj)
    settled_int = int(round(ss.round_single(sv)))

    return FamilySpine(city, metric, target_date, unit, sv, omega, jq, band,
                       pd.mu_native, pd.sigma_native, si, settled_int, "OK")


# ===========================================================================
# Decision-time book loader.
#
# Strategy:
#   1. Load no_trade_regret_events for the window: condition_id + bin_label +
#      executable_snapshot_id (the decision-time snapshot Zeus priced from).
#   2. For each (condition_id, executable_snapshot_id), load the decision-time
#      snapshot row for that leg (outcome_label=YES or NO).
#   3. For the sibling leg (the OTHER outcome_label for the same condition_id),
#      find the nearest-in-time snapshot (within ±2 seconds, same captured_at batch).
#   4. --strict-fillability: drop any leg whose decision-time ask is absent/ABSENT.
#
# This gives the DECISION-TIME cost the spine actually priced from (not latest-in-window).
# ===========================================================================

def _to_float(x) -> Optional[float]:
    try:
        if x is None or str(x).strip().upper() in ("ABSENT", ""):
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


def load_decision_time_books(tr_con, world_con) -> tuple[dict, dict, int, int]:
    """Load decision-time book pricing keyed by condition_id.

    Returns:
        cond_to_bin_label: condition_id -> (bin_label, city, target_date, metric)
        cond_to_book:      condition_id -> {"YES": slot, "NO": slot} where
                           slot = {"ask": float|None, "fee_frac": float|None,
                                   "min_tick": float|None, "neg_risk": int,
                                   "captured_at": str, "from_decision_time": bool}
        n_with_decision_snap: how many condition_ids had a resolvable decision-time snapshot
        n_fallback_latest: how many fell back to latest-in-window (no executable_snapshot_id)
    """
    # Step 1: load condition_id -> bin_label + executable_snapshot_id from NTRC.
    # --strict-condition-bin-join: require condition_id and bin_label both non-null;
    # if one condition_id maps to multiple bin_labels, drop it (ambiguous).
    ntrc_rows = world_con.execute("""
        SELECT condition_id, bin_label, city, target_date, metric,
               executable_snapshot_id
        FROM no_trade_regret_events
        WHERE target_date >= ? AND target_date <= ?
          AND condition_id IS NOT NULL
          AND bin_label IS NOT NULL
        ORDER BY condition_id, bin_label
    """, (WINDOW_START, WINDOW_END)).fetchall()

    # Build condition_id -> [(bin_label, city, td, metric, eid)]
    raw_map: dict[str, list] = defaultdict(list)
    for cid, bl, city, td, metric, eid in ntrc_rows:
        raw_map[cid].append((bl, city, td, metric, eid))

    # --strict-condition-bin-join: drop condition_ids with multiple distinct bin_labels.
    cond_to_bin_label: dict[str, tuple] = {}
    n_ambiguous = 0
    for cid, entries in raw_map.items():
        labels = {e[0] for e in entries}
        if len(labels) > 1:
            n_ambiguous += 1
            continue  # ambiguous join: drop, never guess
        entry = entries[0]
        cond_to_bin_label[cid] = (entry[0], entry[1], entry[2], entry[3])

    # Step 2: for each condition_id, get its decision-time snapshot.
    # Collect: condition_id -> latest executable_snapshot_id seen in NTRC.
    cid_to_eid: dict[str, Optional[str]] = {}
    for cid, entries in raw_map.items():
        if cid not in cond_to_bin_label:
            continue  # was ambiguous
        # Take the most recent executable_snapshot_id (latest decision that priced it).
        eids = [e[4] for e in entries if e[4] is not None]
        cid_to_eid[cid] = eids[-1] if eids else None

    # Step 3: load snapshot rows by snapshot_id.
    all_eids = [eid for eid in cid_to_eid.values() if eid is not None]
    snap_by_eid: dict[str, dict] = {}  # snapshot_id -> row dict
    if all_eids:
        # Load in batches (SQLite IN clause limit = 999)
        batch_size = 900
        for i in range(0, len(all_eids), batch_size):
            batch = all_eids[i:i + batch_size]
            ph = ",".join(["?"] * len(batch))
            rows = tr_con.execute(f"""
                SELECT snapshot_id, condition_id, outcome_label,
                       orderbook_top_ask, fee_details_json, min_tick_size,
                       neg_risk, captured_at
                FROM executable_market_snapshots
                WHERE snapshot_id IN ({ph})
            """, batch).fetchall()
            for snap_id, cid_, lbl, ask, fee_j, tick, neg, cap in rows:
                snap_by_eid[snap_id] = {
                    "condition_id": cid_, "outcome_label": lbl,
                    "ask": _to_float(ask), "fee_j": fee_j,
                    "min_tick": _to_float(tick), "neg_risk": int(neg or 0),
                    "captured_at": cap,
                }

    # Step 4: for each condition_id, find the sibling leg (opposite outcome_label)
    # from the same captured_at batch (within ±2 seconds).
    # Build a lookup: condition_id -> {captured_at_prefix -> {YES:row, NO:row}}
    # We'll do this lazily per condition_id that has a decision-time snapshot.

    def load_sibling(tr_con, cid: str, target_label: str, anchor_time: str) -> Optional[dict]:
        """Find the sibling leg (target_label) for cid captured near anchor_time."""
        # The YES and NO snapshots for the same batch are typically within 2 seconds.
        rows = tr_con.execute("""
            SELECT snapshot_id, outcome_label, orderbook_top_ask, fee_details_json,
                   min_tick_size, neg_risk, captured_at
            FROM executable_market_snapshots
            WHERE condition_id=? AND outcome_label=?
              AND captured_at BETWEEN datetime(?, '-3 seconds')
              AND datetime(?, '+3 seconds')
            ORDER BY ABS(julianday(captured_at) - julianday(?))
            LIMIT 1
        """, (cid, target_label, anchor_time, anchor_time, anchor_time)).fetchall()
        if not rows:
            return None
        snap_id, lbl, ask, fee_j, tick, neg, cap = rows[0]
        return {
            "condition_id": cid, "outcome_label": lbl,
            "ask": _to_float(ask), "fee_j": fee_j,
            "min_tick": _to_float(tick), "neg_risk": int(neg or 0),
            "captured_at": cap,
        }

    def _parse_fee(fee_j) -> Optional[float]:
        try:
            return float(json.loads(fee_j).get("fee_rate_fraction"))
        except Exception:
            return None

    def _slot_from_row(row: dict) -> dict:
        return {
            "ask": row["ask"],
            "fee_frac": _parse_fee(row["fee_j"]),
            "min_tick": row["min_tick"],
            "neg_risk": row["neg_risk"],
            "captured_at": row["captured_at"],
            "from_decision_time": True,
        }

    # Step 5: build cond_to_book.
    cond_to_book: dict[str, dict] = {}
    n_with_decision_snap = 0
    n_fallback_latest = 0

    for cid in cond_to_bin_label:
        eid = cid_to_eid.get(cid)
        book: dict = {}

        if eid and eid in snap_by_eid:
            snap_row = snap_by_eid[eid]
            anchor_label = snap_row["outcome_label"]  # which leg this snapshot is for
            sibling_label = "YES" if anchor_label == "NO" else "NO"
            anchor_slot = _slot_from_row(snap_row)
            sibling_row = load_sibling(tr_con, cid, sibling_label, snap_row["captured_at"])
            sibling_slot = _slot_from_row(sibling_row) if sibling_row else None
            book[anchor_label] = anchor_slot
            if sibling_slot:
                book[sibling_label] = sibling_slot
            n_with_decision_snap += 1
        else:
            # Fallback: use latest snapshot in the window.
            # --strict-fillability: both YES and NO legs must have an ask; if not, skip.
            rows = tr_con.execute("""
                SELECT outcome_label, orderbook_top_ask, fee_details_json,
                       min_tick_size, neg_risk, captured_at
                FROM executable_market_snapshots
                WHERE condition_id=? AND captured_at >= ? AND captured_at <= ?
                ORDER BY captured_at DESC
                LIMIT 4
            """, (cid, WINDOW_START, WINDOW_END + "T23:59:59")).fetchall()
            latest: dict = {}
            for lbl, ask, fee_j, tick, neg, cap in rows:
                if lbl not in latest:
                    latest[lbl] = {
                        "ask": _to_float(ask), "fee_frac": _parse_fee(fee_j),
                        "min_tick": _to_float(tick), "neg_risk": int(neg or 0),
                        "captured_at": cap, "from_decision_time": False,
                    }
            if latest:
                book = latest
                n_fallback_latest += 1

        if book:
            cond_to_book[cid] = book

    return cond_to_bin_label, cond_to_book, n_with_decision_snap, n_fallback_latest, n_ambiguous


# ===========================================================================
# Grade one family.
# ===========================================================================
@dataclass
class GradedTrade:
    city: str
    metric: str
    target_date: str
    side: str            # "buy_yes" | "buy_no"
    bin_kind: str        # point | below | above | range
    is_modal_pick: bool
    is_ring: bool
    is_tail: bool
    is_neg_risk: bool    # neg_risk=1 in the snapshot
    ask: float
    fee: float
    edge_lcb: float
    point_ev: float
    payoff: float        # realized 1.0 or 0.0
    ev: float            # payoff - (ask + fee)
    from_decision_time: bool  # True = decision-time snapshot, False = fallback latest


def grade_family(
    fs: FamilySpine,
    cond_to_bin_label: dict,
    cond_to_book: dict,
    cond_by_family: dict,
) -> tuple[list[GradedTrade], str]:
    """Returns (graded selected legs [0 or 1], drop_reason or '')."""
    if fs.band is None:
        return [], "no_band"

    fam_key = (fs.city.lower(), fs.metric, fs.target_date)
    conds = cond_by_family.get(fam_key)
    if not conds:
        return [], "no_book_condition_for_family"

    omega = fs.omega
    q = np.asarray(fs.jq.q, dtype=float)
    modal_grid_i = int(np.argmax(q))

    legs: list[dict] = []
    for cid in conds:
        if cid not in cond_to_bin_label:
            continue  # dropped by --strict-condition-bin-join
        label = cond_to_bin_label[cid][0]
        spec = parse_bin_label(label)
        if spec is None:
            continue  # parse failure: drop (--strict-condition-bin-join)
        if spec.unit != fs.unit:
            continue  # unit mismatch

        cov = covered_grid_indices(spec, omega)
        if not cov:
            continue

        book = cond_to_book.get(cid, {})
        yes_payoff = np.zeros(len(omega.bins), dtype=float)
        yes_payoff[cov] = 1.0
        no_payoff = 1.0 - yes_payoff
        market_won = label_contains_int(spec, fs.settled_int)
        is_modal = modal_grid_i in cov

        for side, payoff_vec, lbl in (
            ("buy_yes", yes_payoff, "YES"),
            ("buy_no", no_payoff, "NO"),
        ):
            slot = book.get(lbl)
            if slot is None:
                continue
            ask = slot.get("ask")
            if ask is None or not (0.0 < ask < 1.0):
                # --strict-fillability: skip legs with absent/invalid ask
                continue
            fee_frac = slot.get("fee_frac")
            if fee_frac is None:
                continue
            fee = ask * fee_frac
            cost = ask + fee
            neg_risk = bool(slot.get("neg_risk", 0))
            from_dt = bool(slot.get("from_decision_time", False))

            edge_lcb = edge_lower_bound(fs.band, payoff_vec, cost, alpha=None)
            pev = point_fair_value(fs.jq, payoff_vec) - cost
            if side == "buy_yes":
                won = market_won
            else:
                won = not market_won
            realized_payoff = 1.0 if won else 0.0
            ev = realized_payoff - cost

            legs.append({
                "side": side, "bin_kind": spec.kind, "is_modal": is_modal,
                "ask": ask, "fee": fee, "edge_lcb": edge_lcb, "point_ev": pev,
                "payoff": realized_payoff, "ev": ev,
                "neg_risk": neg_risk, "from_decision_time": from_dt,
            })

    if not legs:
        return [], "no_priced_leg"

    # Spine gate: edge_lcb > 0 AND point_ev > 0 (no-synthetic/arb/conversion: single-leg only).
    passers = [lg for lg in legs if lg["edge_lcb"] > 0.0 and lg["point_ev"] > 0.0]
    if not passers:
        return [], ""  # spine no-trades: zero contribution (not 0-EV)

    pick = max(passers, key=lambda lg: lg["point_ev"])
    is_modal = bool(pick["is_modal"])
    if is_modal:
        cls_modal, cls_ring, cls_tail = True, False, False
    elif pick["bin_kind"] in ("above", "below"):
        cls_modal, cls_ring, cls_tail = False, False, True
    else:
        cls_modal, cls_ring, cls_tail = False, True, False

    gt = GradedTrade(
        city=fs.city, metric=fs.metric, target_date=fs.target_date,
        side=pick["side"], bin_kind=pick["bin_kind"],
        is_modal_pick=cls_modal, is_ring=cls_ring, is_tail=cls_tail,
        is_neg_risk=pick["neg_risk"],
        ask=pick["ask"], fee=pick["fee"],
        edge_lcb=pick["edge_lcb"], point_ev=pick["point_ev"],
        payoff=pick["payoff"], ev=pick["ev"],
        from_decision_time=pick["from_decision_time"],
    )
    return [gt], ""


# ===========================================================================
# Bootstrap CI.
# ===========================================================================
def bootstrap_ci(values: list[float], n_boot: int = 5000, seed: int = 42) -> tuple:
    if not values:
        return (float("nan"), float("nan"), float("nan"))
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    n = arr.size
    means = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, n, n)
        means[b] = float(arr[idx].mean())
    return (float(arr.mean()), float(np.percentile(means, 2.5)),
            float(np.percentile(means, 97.5)))


# ===========================================================================
# Main.
# ===========================================================================
def main() -> None:
    cities = load_cities()
    cities_by_name = {c.name: c for c in cities}

    fc_con = ro(FORECASTS_DB)
    tr_con = ro(TRADES_DB)
    world_con = ro(WORLD_DB)

    # Settlement truth for the specific window.
    settlements = [
        s for s in arm.load_settlements(fc_con, WINDOW_START)
        if s["target_date"] <= WINDOW_END
    ]
    # SINGLE TRUTH (bias-maze strip 2026-06-17): the settlement-residual de-bias provider
    # is REMOVED. The replay runs on the raw precise multi-model fused center with the
    # IDENTITY (empty-artifact) DebiasAuthority — exactly what the live spine ships.
    debias_auth = DebiasAuthority()
    print("De-bias: IDENTITY (single-truth raw multi-model center)")

    print(f"Settled VERIFIED families in {WINDOW_START}..{WINDOW_END}: {len(settlements)}")

    # Load decision-time books (with strict joins).
    print("Loading decision-time books from no_trade_regret_events + executable_market_snapshots...")
    cond_to_bin_label, cond_to_book, n_dt_snap, n_fallback, n_ambiguous = \
        load_decision_time_books(tr_con, world_con)

    print(f"  condition_ids with bin_label (strict): {len(cond_to_bin_label)}")
    print(f"  dropped ambiguous join: {n_ambiguous}")
    print(f"  with decision-time snapshot: {n_dt_snap}")
    print(f"  fallback latest-in-window: {n_fallback}")

    # Index by family.
    cond_by_family: dict = defaultdict(list)
    for cid, (label, city, td, metric) in cond_to_bin_label.items():
        if cid in cond_to_book:
            cond_by_family[(city.lower(), metric, td)].append(cid)

    # Grade each settled family.
    graded: list[GradedTrade] = []
    drops: dict = defaultdict(int)
    n_book_joined = 0
    n_spine_eval = 0
    n_no_trade = 0

    for rec in settlements:
        city_obj = cities_by_name.get(rec["city"])
        if city_obj is None:
            drops["no_city_obj"] += 1
            continue
        fam_key = (rec["city"].lower(), rec["metric"], rec["target_date"])
        has_book = fam_key in cond_by_family
        if has_book:
            n_book_joined += 1

        try:
            fs = build_family_spine(fc_con, city_obj, rec, debias_auth)
        except Exception as exc:  # noqa: BLE001
            drops[f"spine_exc:{type(exc).__name__}"] += 1
            continue

        if fs is None:
            drops["no_members_or_resolution"] += 1
            continue
        if fs.note != "OK":
            drops[f"spine:{fs.note.split(':')[0]}"] += 1
            continue
        if not has_book:
            drops["no_book_for_settled_family"] += 1
            continue

        n_spine_eval += 1
        trades, reason = grade_family(fs, cond_to_bin_label, cond_to_book, cond_by_family)
        if reason:
            drops[f"grade:{reason}"] += 1
            continue
        if not trades:
            n_no_trade += 1
            continue
        graded.extend(trades)

    write_report(
        settlements=settlements,
        n_book_joined=n_book_joined,
        n_spine_eval=n_spine_eval,
        n_no_trade=n_no_trade,
        graded=graded,
        drops=drops,
        n_dt_snap=n_dt_snap,
        n_fallback=n_fallback,
        n_ambiguous=n_ambiguous,
        n_cond_strict=len(cond_to_bin_label),
    )


def _fmt(x, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def write_report(*, settlements, n_book_joined, n_spine_eval, n_no_trade, graded,
                 drops, n_dt_snap, n_fallback, n_ambiguous, n_cond_strict):
    A: list[str] = []
    a = A.append

    a("# Q-Kernel Spine — Definitive Settlement-Graded After-Cost EV Verdict")
    a("")
    a("Created: 2026-06-16. `scripts/qkernel_settlement_ev_replay.py`.")
    a("Read-only on live DBs. No venue calls. No daemon restart.")
    a("")
    a(f"**Grading window: {WINDOW_START} to {WINDOW_END}** (inclusive; post-spine-enable).")
    a("")
    a("## Join Methodology")
    a("")
    a("1. **Settlement truth**: `zeus-forecasts.db.settlement_outcomes` WHERE "
      f"`authority='VERIFIED'` AND `target_date` IN [{WINDOW_START}, {WINDOW_END}].")
    a("2. **Bin topology**: `zeus-world.db.no_trade_regret_events` "
      "(condition_id → bin_label, city, target_date, metric). "
      "**--strict-condition-bin-join**: any condition_id with multiple distinct bin_labels "
      "is DROPPED (never guessed); reported as `n_ambiguous_join`. The `market_events` "
      "table is empty in the live DBs (confirmed: 0 rows); `no_trade_regret_events` is "
      "the only available condition_id→bin join for this window.")
    a("3. **Decision-time cost**: `no_trade_regret_events.executable_snapshot_id` → "
      "`zeus_trades.db.executable_market_snapshots.snapshot_id`. This is the EXACT "
      "snapshot Zeus priced from at decision time, not a retroactive latest-in-window "
      "snapshot. The sibling leg (opposite outcome_label) is found by the same "
      "condition_id within ±3 seconds of the anchor snapshot's `captured_at`. "
      "**--strict-fillability**: any leg with absent or invalid `orderbook_top_ask` is "
      "DROPPED. Condition_ids with no `executable_snapshot_id` fall back to latest "
      "snapshot in window (counted separately as `n_fallback_latest`).")
    a("4. **Spine reconstruction**: VERBATIM from `qkernel_arm_replay.py` (CURRENT_REUSABLE "
      "2026-06-16) — same fresh members at decision cycle = target−1d, same σ-floor, "
      "same grid Omega, same joint q + coherent band (300 draws, α=0.05). "
      "**--no-day0**: decision cycle is always target−1d (no same-day grading). "
      "**--no-synthetic / --no-arb / --no-conversion**: single-leg DIRECT routes only.")
    a("5. **Spine gate**: `edge_lcb > 0 AND point_ev > 0` (the live `edge_lower_bound` "
      "function over the coherent band). argmax `point_ev` picks the selected leg. "
      "A no-trade family contributes ZERO legs (not a 0-EV entry).")
    a("6. **Realized payoff**: `buy_yes` wins if the market bin's integer set contains "
      "`round_single(settlement_value)` per `SettlementSemantics.for_city` (HK: "
      "oracle_truncate; others: wmo_half_up). `buy_no` wins if it does NOT.")
    a("7. **After-cost EV**: `realized_payoff − (ask + ask × fee_rate_fraction)` "
      "(taker ask + taker fee, all-in cost).")
    a("")

    a("## Coverage")
    a("")
    n_set = len(settlements)
    a(f"- Settled VERIFIED families in window: **{n_set}**")
    a(f"- Strict condition_id→bin_label joins (no ambiguity): **{n_cond_strict}**")
    a(f"- Dropped for ambiguous join (multiple bin_labels per condition_id): **{n_ambiguous}**")
    a(f"- With decision-time snapshot (executable_snapshot_id resolved): **{n_dt_snap}**")
    a(f"- Fallback to latest-in-window (no executable_snapshot_id): **{n_fallback}**")
    a(f"- Settled families with at least one joined condition_id + book: **{n_book_joined}**")
    a(f"- Spine-evaluated (usable spine + book condition): **{n_spine_eval}**")
    a(f"- Spine NO-TRADED (no leg passed edge_lcb>0 ∧ point_ev>0): **{n_no_trade}**")
    a(f"- **Spine-SELECTED graded trades (n): {len(graded)}**")
    if drops:
        a("- Drop/skip reasons: " + ", ".join(
            f"{k}={v}" for k, v in sorted(drops.items())))
    a("")

    if not graded:
        a("## VERDICT")
        a("")
        a("**NO SPINE-SELECTED TRADES** — under the reproduced edge_lcb>0 ∧ point_ev>0 "
          "gate over the decision-time book, the spine selected zero trades in the window. "
          "After-cost EV undefined (no graded trades). See coverage drops above.")
        a("")
        a("**VERDICT: INDETERMINATE** (no graded trades — n=0)")
        _finish(A)
        return

    evs = [g.ev for g in graded]
    mean_ev, lo, hi = bootstrap_ci(evs)

    a("## Overall Settlement-Realized After-Cost EV")
    a("")
    a(f"- **n graded trades**: **{len(graded)}**")
    a(f"- **mean after-cost EV per share**: **{mean_ev:+.4f}**")
    a(f"- **bootstrap 95% CI (5000 resamples)**: **[{lo:+.4f}, {hi:+.4f}]**")
    a(f"- median EV: {float(np.median(evs)):+.4f}; "
      f"win-rate: {float(np.mean([g.payoff for g in graded])):.3f}; "
      f"mean all-in cost: {float(np.mean([g.ask + g.fee for g in graded])):.4f}")
    n_dt = sum(1 for g in graded if g.from_decision_time)
    a(f"- decision-time snapshot coverage: {n_dt}/{len(graded)} graded trades "
      f"({100*n_dt/len(graded):.0f}% from decision-time snapshot)")

    sign_str = ("POSITIVE (CI lower bound > 0)" if lo > 0 else
                "NEGATIVE (CI upper bound < 0)" if hi < 0 else
                "INDETERMINATE (CI spans 0)")
    a(f"- **sign: {sign_str}**")
    a("")

    # --- by side ---
    a("## By Side")
    a("")
    a("| side | n | mean EV | 95% CI | win-rate |")
    a("|---|---|---|---|---|")
    for side in ("buy_yes", "buy_no"):
        sub = [g for g in graded if g.side == side]
        if not sub:
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        wr = float(np.mean([g.payoff for g in sub]))
        a(f"| {side} | {len(sub)} | {m:+.4f} | [{l:+.4f}, {h:+.4f}] | {wr:.3f} |")
    a("")

    # --- by class ---
    a("## By Class (modal / ring / tail)")
    a("")
    a("**modal** = spine's favorite (max-q) bin; "
      "**ring** = adjacent bounded bin (not modal); "
      "**tail** = shoulder bin (X or below / X or above).")
    a("")
    a("| class | n | mean EV | 95% CI | win-rate | sign |")
    a("|---|---|---|---|---|---|")
    classes = [
        ("modal", lambda g: g.is_modal_pick),
        ("ring", lambda g: g.is_ring),
        ("tail", lambda g: g.is_tail),
    ]
    class_summ: dict = {}
    for name, pred in classes:
        sub = [g for g in graded if pred(g)]
        if not sub:
            a(f"| {name} | 0 | n/a | n/a | n/a | n/a |")
            class_summ[name] = (0, float("nan"), float("nan"), float("nan"))
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        wr = float(np.mean([g.payoff for g in sub]))
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        a(f"| {name} | {len(sub)} | {m:+.4f} | [{l:+.4f}, {h:+.4f}] | {wr:.3f} | {sgn} |")
        class_summ[name] = (len(sub), m, l, h)
    a("")

    # --- by neg-risk class ---
    a("## By Market Class (neg-risk buy_no vs other)")
    a("")
    a("| class | n | mean EV | 95% CI | win-rate | sign |")
    a("|---|---|---|---|---|---|")
    neg_risk_classes = [
        ("neg_risk_buy_no", lambda g: g.is_neg_risk and g.side == "buy_no"),
        ("neg_risk_buy_yes", lambda g: g.is_neg_risk and g.side == "buy_yes"),
        ("non_neg_risk", lambda g: not g.is_neg_risk),
    ]
    nr_summ: dict = {}
    for name, pred in neg_risk_classes:
        sub = [g for g in graded if pred(g)]
        if not sub:
            a(f"| {name} | 0 | n/a | n/a | n/a | n/a |")
            nr_summ[name] = (0, float("nan"), float("nan"), float("nan"))
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        wr = float(np.mean([g.payoff for g in sub]))
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        a(f"| {name} | {len(sub)} | {m:+.4f} | [{l:+.4f}, {h:+.4f}] | {wr:.3f} | {sgn} |")
        nr_summ[name] = (len(sub), m, l, h)
    a("")

    # --- by metric ---
    a("## By Metric")
    a("")
    a("| metric | n | mean EV | 95% CI | sign |")
    a("|---|---|---|---|---|")
    for metric in ("high", "low"):
        sub = [g for g in graded if g.metric == metric]
        if not sub:
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        a(f"| {metric} | {len(sub)} | {m:+.4f} | [{l:+.4f}, {h:+.4f}] | {sgn} |")
    a("")

    # --- by route (decision-time vs fallback) ---
    a("## By Snapshot Source")
    a("")
    a("| source | n | mean EV | 95% CI | sign |")
    a("|---|---|---|---|---|")
    for src_name, src_pred in [
        ("decision_time_snapshot", lambda g: g.from_decision_time),
        ("fallback_latest_in_window", lambda g: not g.from_decision_time),
    ]:
        sub = [g for g in graded if src_pred(g)]
        if not sub:
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        a(f"| {src_name} | {len(sub)} | {m:+.4f} | [{l:+.4f}, {h:+.4f}] | {sgn} |")
    a("")

    # --- verdict ---
    a("## Verdict")
    a("")
    if lo > 0:
        overall_verdict = "SPINE_PROVEN_POSITIVE_AFTER_COST"
        detail = (f"mean after-cost EV {mean_ev:+.4f}/share, 95% CI [{lo:+.4f}, "
                  f"{hi:+.4f}] EXCLUDES 0 on the low side, n={len(graded)}.")
    elif hi < 0:
        overall_verdict = "PROVEN_NEGATIVE"
        detail = (f"mean after-cost EV {mean_ev:+.4f}/share, 95% CI [{lo:+.4f}, "
                  f"{hi:+.4f}] EXCLUDES 0 on the high side, n={len(graded)}.")
    else:
        overall_verdict = "INDETERMINATE"
        detail = (f"mean after-cost EV {mean_ev:+.4f}/share, 95% CI [{lo:+.4f}, "
                  f"{hi:+.4f}] SPANS 0, n={len(graded)}: not statistically "
                  f"distinguishable from zero at this sample size.")

    a(f"**VERDICT: {overall_verdict}** — {detail}")
    a("")

    valid_cls = {k: v for k, v in class_summ.items() if v[0] > 0 and not math.isnan(v[1])}
    if valid_cls:
        best = max(valid_cls.items(), key=lambda kv: kv[1][1])
        worst = min(valid_cls.items(), key=lambda kv: kv[1][1])
        bn, (bnn, bm, bl, bh) = best
        bsgn = "POSITIVE (CI>0)" if bl > 0 else ("NEGATIVE" if bh < 0 else "0-spanning")
        a(f"- Best class: **{bn}** (n={bnn}, mean EV {bm:+.4f}, CI [{bl:+.4f}, {bh:+.4f}], {bsgn}).")
        if worst[0] != best[0]:
            wn, (wnn, wm, wl, wh) = worst
            wsgn = "POSITIVE (CI>0)" if wl > 0 else ("NEGATIVE" if wh < 0 else "0-spanning")
            a(f"- Worst class: **{wn}** (n={wnn}, mean EV {wm:+.4f}, CI [{wl:+.4f}, {wh:+.4f}], {wsgn}).")
    a("")
    a("### Operator bar")
    a("")
    a("Settlement-graded positive after-cost EV with 95% CI lower bound > 0 "
      "(not merely positive mean). The spine is ARM-approved for live trading "
      "only if `VERDICT: SPINE_PROVEN_POSITIVE_AFTER_COST`.")
    a("")

    _finish(A)

    # stdout summary
    print()
    print(f"REPORT WRITTEN {REPORT_PATH}")
    print()
    print(f"n={len(graded)} graded trades | "
          f"mean EV={mean_ev:+.4f} | CI=[{lo:+.4f},{hi:+.4f}] | {sign_str}")
    print(f"VERDICT: {overall_verdict}")
    for name, pred in classes:
        sub = [g for g in graded if pred(g)]
        if not sub:
            print(f"  class {name}: n=0")
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        print(f"  class {name}: n={len(sub)} mean={m:+.4f} CI=[{l:+.4f},{h:+.4f}] {sgn}")
    for name, pred in neg_risk_classes:
        sub = [g for g in graded if pred(g)]
        if not sub:
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        print(f"  nr-class {name}: n={len(sub)} mean={m:+.4f} CI=[{l:+.4f},{h:+.4f}] {sgn}")


def _finish(A: list[str]) -> None:
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(A) + "\n")


if __name__ == "__main__":
    main()
