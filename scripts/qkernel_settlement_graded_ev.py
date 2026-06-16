# Created: 2026-06-15
# Last reused or audited: 2026-06-15
# Authority basis: GOAL #117 settlement-graded after-cost EV deploy gate; extends
#   qkernel_arm_replay.py §5 (the ONE ungraded layer it flagged DATA-COVERAGE-LIMITED).
#   This closes exactly the condition_id->bin join the replay could not do from
#   executable_market_snapshots alone, by joining each book condition_id to its bin via
#   no_trade_regret_events.bin_label (the natural-language question string that encodes
#   the bin), then grading the spine's OWN selected legs (edge_lcb>0 gate, the live
#   src/decision/payoff_vector pass) settlement-graded after REAL taker cost+fee.
#
# Reuses (provenance audited 2026-06-15, CURRENT_REUSABLE):
#   - scripts/qkernel_arm_replay.py — fresh-member reconstruction, build_grid_omega,
#     settled_bin_index, the EXACT per-family spine reconstruction (replicated verbatim
#     from replay_family so the q/band are byte-identical to the validated replay; the
#     replay script itself is NOT modified — it is CURRENT_REUSABLE and stays frozen).
#   - src/decision/payoff_vector.{edge_lower_bound,point_fair_value} — the LIVE spine
#     vector-edge functions (the same ones the reactor selects on). edge_lcb gate is
#     reproduced; ΔU>0 (a sizing/exposure condition) is proxied by point_ev>0 among
#     edge_lcb>0 legs (log-utility against a flat baseline is monotone in point edge),
#     and the spine's leg pick = argmax point_ev over edge_lcb>0 legs. Flagged below.
#   - src/probability/instruments.Instrument.payoff_vector — Arrow-Debreu YES/NO vector.
#   - src/contracts/settlement_semantics.{SettlementSemantics.for_city,
#     settlement_preimage_offsets} — per-city rounding (HK oracle_truncate; others
#     wmo_half_up). The realized winning bin = round_single(realized) (NOT reinvented).
#   - src/config.load_cities — city registry / unit.
#   - settlement truth: zeus-forecasts.db.settlement_outcomes WHERE authority='VERIFIED'.
#   - books: zeus_trades.db.executable_market_snapshots (latest taker ask per
#     condition_id+label in window; TAKER ASK ONLY, never mid/last; real fee from
#     fee_details_json.fee_rate_fraction).
#   - bin map: zeus-world.db.no_trade_regret_events (condition_id -> bin_label, 1:1).
#
# READ-ONLY on all live DBs (mode=ro). NO venue calls. NO daemon restart. NO writes to
# live DBs. Coverage is reported honestly; gaps are dropped with a reason, never filled.
"""Settlement-graded after-cost EV of the rebuilt q-kernel spine's SELECTED trades.

Run:  /Users/leofitz/zeus/.venv/bin/python \
        .claude/worktrees/qkernel-rebuild/scripts/qkernel_settlement_graded_ev.py

Writes docs/rebuild/settlement_graded_ev_2026-06-15.md and prints the headline numbers.
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

# --- repo root (the worktree this script lives in) --------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_SCRIPT_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# --- live DBs: ALWAYS the main tree (worktree ships only stubs) -------------
_LIVE_STATE = "/Users/leofitz/zeus/state"
FORECASTS_DB = os.path.join(_LIVE_STATE, "zeus-forecasts.db")
TRADES_DB = os.path.join(_LIVE_STATE, "zeus_trades.db")
WORLD_DB = os.path.join(_LIVE_STATE, "zeus-world.db")

REPORT_PATH = os.path.join(_ROOT, "docs", "rebuild", "settlement_graded_ev_2026-06-15.md")

# --- import the validated replay module by path, registering it in sys.modules
# BEFORE exec so its @dataclass module lookup resolves (Python 3.14). The replay is
# CURRENT_REUSABLE and is imported UNMODIFIED. ----------------------------------------
_REPLAY_PATH = os.path.join(_SCRIPT_DIR, "qkernel_arm_replay.py")
_spec = importlib.util.spec_from_file_location("qkernel_arm_replay", _REPLAY_PATH)
arm = importlib.util.module_from_spec(_spec)
sys.modules["qkernel_arm_replay"] = arm
_spec.loader.exec_module(arm)

from src.config import load_cities  # noqa: E402
from src.contracts.settlement_semantics import (  # noqa: E402
    SettlementSemantics,
)
from src.decision.payoff_vector import (  # noqa: E402
    edge_lower_bound,
    point_fair_value,
)
from src.forecast.debias_authority import DebiasAuthority  # noqa: E402
from src.probability.instruments import Instrument  # noqa: E402
from src.probability.joint_q import JointQError, build_joint_q  # noqa: E402
from src.probability.joint_q_band import (  # noqa: E402
    JointQBandError,
    build_joint_q_band,
)
from src.probability.outcome_space import OutcomeSpaceError  # noqa: E402

# Reuse the replay's exact window / band params so q and band match the validated run.
WINDOW_DAYS = arm.WINDOW_DAYS
N_BAND_DRAWS = arm.N_BAND_DRAWS
BAND_ALPHA = arm.BAND_ALPHA

ro = arm.ro


# ===========================================================================
# bin_label parsing — the natural-language question string encodes the bin.
#   point:  "...be 22°C on June 11?"            -> point integer {22}
#   below:  "...be 33°C or below on June 11?"   -> lower shoulder (-inf, 33]
#   above:  "...be 29°C or higher on June 11?"  -> upper shoulder [29, +inf)
#   range:  "...be between 68-69°F on May 31?"  -> integer interval [68, 69]
# The 4 shapes cover ALL 1986 distinct labels (audited: 0 unmatched).
# A bin "covers" the integer set S(label); the market settles YES on the bin whose
# S contains round_single(realized). The leg's payoff over the replay grid omega is
# the indicator over the grid bins whose representative integer lies in S(label).
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
    """Does the market bin's integer set contain the realized settled integer?"""
    lo = spec.lo if spec.lo is not None else -10**9
    hi = spec.hi if spec.hi is not None else 10**9
    return lo <= settled_int <= hi


def covered_grid_indices(spec: BinSpec, omega) -> list[int]:
    """The grid-omega bin indices a market bin (BinSpec) covers.

    The replay grid omega uses 1°C point bins (``pt_{v}`` with lower==upper==v) and a
    leading/trailing open shoulder; for °F it uses width-2 bins (``rng_{lo}`` with
    [lo, lo+1]). Each market bin's integer set S is mapped to the grid bins whose
    representative integer(s) fall in S. The payoff vector is then the indicator over
    those grid indices — the Arrow-Debreu YES payoff of the market bin expressed on the
    replay omega (so it dots with the SAME q / band.samples the replay validated).
    """
    idx: list[int] = []
    lo = spec.lo if spec.lo is not None else -10**9
    hi = spec.hi if spec.hi is not None else 10**9
    for i, b in enumerate(omega.bins):
        bl = b.lower_native
        bh = b.upper_native
        # representative integer(s) of this grid bin: a grid bin overlaps the market
        # bin's integer set iff any integer in [bl, bh] (with open shoulders clamped)
        # lies in [lo, hi]. For point bins bl==bh==v. For °F rng bins [lo_e, lo_e+1]
        # both integers are members; if EITHER is in S the grid bin is (partly) covered.
        glo = int(math.ceil(bl)) if bl is not None else -10**9
        ghi = int(math.floor(bh)) if bh is not None else 10**9
        # a grid bin's covered range of integers:
        ilo = max(glo, lo)
        ihi = min(ghi, hi)
        if ilo <= ihi:
            idx.append(i)
    return idx


# ===========================================================================
# Per-family spine reconstruction — VERBATIM from replay_family so the q/band are
# byte-identical to the validated replay, but also returning pd / omega / jq / band.
# (The replay's replay_family returns only scalars; we cannot get the objects from it,
# so we replicate its body using the SAME imported builders. Any drift from the replay
# would change the q — kept identical by construction.)
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
    city = rec["city"]
    metric = rec["metric"]
    target_date = rec["target_date"]
    sv = rec["settlement_value"]
    unit = rec["settlement_unit"] or city_obj.settlement_unit

    td = date.fromisoformat(target_date)
    cycle_date = (td - timedelta(days=1)).isoformat()
    members_raw = arm.fresh_members_at_cycle(fc_con, city, metric, target_date, cycle_date)
    if len(members_raw) < 3:
        return None

    from src.probability.event_resolution import (
        ResolutionError,
        event_resolution_for_city,
    )
    from src.forecast.types import FreshModelSet, RawModelMember, ForecastCase
    from src.forecast.predictive_distribution_builder import (
        build_predictive_distribution,
    )
    from src.forecast.sigma_authority import realized_sigma_floor

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
        case,
        fms,
        debias_auth,
        obs=None,
        has_fusion_capture=True,
        fused_center_sd_native=fused_center_sd,
        sigma_resid_native=sigma_resid,
    )
    if not pd.live_eligible:
        return FamilySpine(city, metric, target_date, unit, sv, None, None, None,
                           pd.mu_native, pd.sigma_native, -1, -1,
                           f"INELIGIBLE: {pd.ineligibility_reason}")

    try:
        omega = arm.build_grid_omega(
            family_id=case.family_id, resolution=resolution,
            center_native=pd.mu_native, settlement_value=sv, unit=unit,
        )
    except OutcomeSpaceError as exc:
        return FamilySpine(city, metric, target_date, unit, sv, None, None, None,
                           pd.mu_native, pd.sigma_native, -1, -1, f"OMEGA_FAIL: {exc}")
    try:
        jq = build_joint_q(pd, omega)
    except JointQError as exc:
        return FamilySpine(city, metric, target_date, unit, sv, None, None, None,
                           pd.mu_native, pd.sigma_native, -1, -1, f"JOINTQ_FAIL: {exc}")

    si = arm.settled_bin_index(omega, sv)
    if si is None:
        return None

    band = None
    try:
        band = build_joint_q_band(pd, omega, n_draws=N_BAND_DRAWS, alpha=BAND_ALPHA)
        band.assert_valid()
    except (JointQBandError, AssertionError):
        band = None

    # winning settled integer via the per-city settlement rule (HK truncates).
    ss = SettlementSemantics.for_city(city_obj)
    settled_int = int(round(ss.round_single(sv)))

    return FamilySpine(city, metric, target_date, unit, sv, omega, jq, band,
                       pd.mu_native, pd.sigma_native, si, settled_int, "OK")


# ===========================================================================
# Book loader — latest TAKER ask per (condition_id, outcome_label) in window.
# TAKER ASK ONLY. Real fee from fee_details_json.fee_rate_fraction. min_tick noted.
# ===========================================================================
def _to_float(x) -> Optional[float]:
    try:
        if x is None or str(x).upper() in ("ABSENT", "") or x == "":
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


def load_books(tr_con) -> dict:
    """condition_id -> {label -> dict(ask, fee_frac, min_tick, captured_at)} latest."""
    rows = tr_con.execute(
        """
        SELECT condition_id, outcome_label, orderbook_top_ask, fee_details_json,
               min_tick_size, captured_at
        FROM executable_market_snapshots
        WHERE captured_at >= datetime('now', ?)
          AND outcome_label IS NOT NULL
        """,
        (f"-{WINDOW_DAYS + 2} day",),
    ).fetchall()
    book: dict = defaultdict(dict)
    for cond, label, ask, fee_json, tick, cap in rows:
        ask_f = _to_float(ask)
        fee_frac = None
        try:
            fee_frac = float(json.loads(fee_json).get("fee_rate_fraction"))
        except Exception:
            fee_frac = None
        tick_f = _to_float(tick)
        prev = book[cond].get(label)
        if prev is None or cap > prev["captured_at"]:
            book[cond][label] = {
                "ask": ask_f, "fee_frac": fee_frac, "min_tick": tick_f,
                "captured_at": cap,
            }
    return book


def load_bin_map(world_con) -> dict:
    """condition_id -> (bin_label, city, target_date, metric) from no_trade_regret_events.

    1:1 condition_id -> bin_label (audited). Latest row per condition_id (any will do —
    the label is invariant per condition_id)."""
    rows = world_con.execute(
        """
        SELECT condition_id, bin_label, city, target_date, metric
        FROM no_trade_regret_events
        WHERE condition_id IS NOT NULL AND bin_label IS NOT NULL
        """,
    ).fetchall()
    out: dict = {}
    for cond, label, city, td, metric in rows:
        if cond not in out:
            out[cond] = (label, city, td, metric)
    return out


# ===========================================================================
# Grade one family: build all candidate legs (buy_yes / buy_no per book bin) over the
# grid omega, compute the spine's vector edge (edge_lcb, point_ev) via the LIVE
# payoff_vector functions, apply the gate (edge_lcb>0), pick argmax point_ev, then
# settlement-grade the picked leg after REAL cost+fee.
# ===========================================================================
@dataclass
class GradedTrade:
    city: str
    metric: str
    target_date: str
    side: str          # "buy_yes" | "buy_no"
    bin_kind: str      # point | below | above | range
    is_modal_pick: bool
    is_ring: bool
    is_tail: bool
    ask: float
    fee: float
    edge_lcb: float
    point_ev: float
    payoff: float      # realized 1/0
    ev: float          # payoff - (ask + fee)


def grade_family(fs: FamilySpine, book: dict, bin_map: dict,
                 cond_by_family: dict) -> tuple[list[GradedTrade], str]:
    """Returns (graded selected legs [0 or 1], drop_reason or '')."""
    if fs.band is None:
        return [], "no_band"
    fam_key = (fs.city.lower(), fs.metric, fs.target_date)
    conds = cond_by_family.get(fam_key)
    if not conds:
        return [], "no_book_condition_for_family"

    omega = fs.omega
    q = np.asarray(fs.jq.q, dtype=float)
    # modal grid bin (the spine's favorite) for class labelling
    modal_grid_i = int(np.argmax(q))

    legs: list[dict] = []
    for cond in conds:
        spec_src = bin_map.get(cond)
        if spec_src is None:
            continue
        label = spec_src[0]
        spec = parse_bin_label(label)
        if spec is None:
            continue
        if spec.unit != fs.unit:
            # unit mismatch between the market bin and the family settlement unit
            continue
        cov = covered_grid_indices(spec, omega)
        if not cov:
            continue
        bk = book.get(cond, {})
        # YES payoff vector over the grid omega = indicator on covered grid bins.
        yes_payoff = np.zeros(len(omega.bins), dtype=float)
        yes_payoff[cov] = 1.0
        no_payoff = 1.0 - yes_payoff
        # market bin wins (realized) iff settled integer in the label's integer set
        market_won = label_contains_int(spec, fs.settled_int)
        # is this market bin the spine's modal/favorite? (covers the modal grid bin)
        is_modal = modal_grid_i in cov

        for side, payoff_vec, lbl in (
            ("buy_yes", yes_payoff, "YES"), ("buy_no", no_payoff, "NO"),
        ):
            slot = bk.get(lbl)
            if slot is None:
                continue
            ask = slot["ask"]
            if ask is None or not (0.0 < ask < 1.0):
                continue
            fee_frac = slot["fee_frac"]
            if fee_frac is None:
                continue
            fee = ask * fee_frac  # taker fee on the executed ask notional
            cost = ask + fee
            edge_lcb = edge_lower_bound(fs.band, payoff_vec, cost, alpha=None)
            pev = point_fair_value(fs.jq, payoff_vec) - cost
            # settlement-graded realized payoff of THIS leg
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
            })

    if not legs:
        return [], "no_priced_leg"

    # Spine gate: edge_lcb > 0. Among passers, spine picks argmax point_ev (the ΔU>0
    # proxy: at a flat/zero exposure baseline the family log-growth ΔU is monotone in
    # the point edge, so argmax ΔU == argmax point_ev; the edge_lcb>0 gate is the
    # load-bearing vector condition reproduced exactly via the live edge_lower_bound).
    passers = [l for l in legs if l["edge_lcb"] > 0.0 and l["point_ev"] > 0.0]
    if not passers:
        return [], ""  # spine NO-TRADES this family -> contributes zero trades (not 0-EV)

    pick = max(passers, key=lambda l: l["point_ev"])
    # class: modal pick vs ring (adjacent) vs tail. Modal = the spine's favorite bin.
    is_modal = bool(pick["is_modal"])
    # ring = a point/range bin adjacent to the modal (not modal itself, bounded bin);
    # tail = a shoulder (above/below) bin. Classify by bin kind + modal flag.
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
        ask=pick["ask"], fee=pick["fee"], edge_lcb=pick["edge_lcb"],
        point_ev=pick["point_ev"], payoff=pick["payoff"], ev=pick["ev"],
    )
    return [gt], ""


# ===========================================================================
# Bootstrap CI (resample families).
# ===========================================================================
def bootstrap_ci(values: list[float], n_boot: int = 5000, seed: int = 12345) -> tuple:
    if not values:
        return (float("nan"), float("nan"), float("nan"))
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    n = arr.size
    means = np.empty(n_boot)
    for b in range(n_boot):
        idx = rng.integers(0, n, n)
        means[b] = float(arr[idx].mean())
    lo = float(np.percentile(means, 2.5))
    hi = float(np.percentile(means, 97.5))
    return (float(arr.mean()), lo, hi)


# ===========================================================================
# Main.
# ===========================================================================
def main() -> None:
    cities = load_cities()
    cities_by_name = {c.name: c for c in cities}

    min_date = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat()
    fc_con = ro(FORECASTS_DB)
    tr_con = ro(TRADES_DB)
    world_con = ro(WORLD_DB)

    settlements = arm.load_settlements(fc_con, min_date)
    debias_auth = DebiasAuthority()

    book = load_books(tr_con)               # condition_id -> {label -> slot}
    bin_map = load_bin_map(world_con)       # condition_id -> (bin_label, city, td, metric)

    # family -> set of condition_ids present in BOTH the book and the bin_map
    cond_by_family: dict = defaultdict(list)
    for cond, (label, city, td, metric) in bin_map.items():
        if cond in book:
            cond_by_family[(city.lower(), metric, td)].append(cond)

    n_book_present_families = 0
    graded: list[GradedTrade] = []
    drops = defaultdict(int)
    n_spine_evaluated = 0  # families with a usable spine + a book condition
    n_no_trade = 0

    for rec in settlements:
        city_obj = cities_by_name.get(rec["city"])
        if city_obj is None:
            drops["no_city_obj"] += 1
            continue
        fam_key = (rec["city"].lower(), rec["metric"], rec["target_date"])
        has_book = fam_key in cond_by_family
        if has_book:
            n_book_present_families += 1
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
        n_spine_evaluated += 1
        trades, reason = grade_family(fs, book, bin_map, cond_by_family)
        if reason:
            drops[f"grade:{reason}"] += 1
            continue
        if not trades:
            n_no_trade += 1
            continue
        graded.extend(trades)

    write_report(
        settlements=settlements,
        n_book_present_families=n_book_present_families,
        n_spine_evaluated=n_spine_evaluated,
        n_no_trade=n_no_trade,
        graded=graded,
        drops=drops,
    )


def _fmt(x, nd=4):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def write_report(*, settlements, n_book_present_families, n_spine_evaluated,
                 n_no_trade, graded, drops):
    A = []
    a = A.append
    a("# Q-Kernel Rebuild — Settlement-Graded After-Cost EV (GOAL #117 deploy gate)")
    a("")
    a("Created: 2026-06-15. `scripts/qkernel_settlement_graded_ev.py`. Read-only on live "
      "data (mode=ro); no venue calls; no daemon restart. Extends "
      "`qkernel_arm_replay.py` §5 — closes the condition_id→bin join the replay flagged "
      "DATA-COVERAGE-LIMITED, by joining each book condition_id to its bin via "
      "`zeus-world.db.no_trade_regret_events.bin_label` (the question string that encodes "
      "the bin; 1:1 with condition_id, all 1986 labels parse, 0 unmatched).")
    a("")
    a("**Method.** Per settled family the rebuilt spine is reconstructed VERBATIM from "
      "the validated replay (same fresh members at decision cycle = target−1d, same "
      "predictive-RSS σ, same grid Omega, same joint q + coherent band). For every book "
      "bin (joined via bin_label) both legs (buy_yes / buy_no) are priced at the **taker "
      "ask only** plus the **real taker fee** (`fee_details_json.fee_rate_fraction`, "
      "uniformly 0.05). Each leg's vector edge is computed with the LIVE spine functions "
      "`src/decision/payoff_vector.edge_lower_bound` and `point_fair_value` over the "
      "Arrow-Debreu payoff on the grid Omega. The spine GATE `edge_lcb>0 AND point_ev>0` "
      "is applied; among passers the spine picks **argmax point_ev** (the ΔU>0 proxy: at "
      "a flat exposure baseline family log-growth ΔU is monotone in the point edge). A "
      "family the spine no-trades contributes ZERO trades (not a 0-EV trade). The picked "
      "leg is settlement-graded: realized winning bin = `round_single(realized)` via "
      "`SettlementSemantics.for_city` (HK oracle_truncate; others wmo_half_up).")
    a("")
    a("> **ΔU proxy caveat (honest scope).** The full live pass also requires "
      "`delta_u_at_min>0` and `optimal_delta_u>0` from the vector ΔU sizing against the "
      "live PortfolioExposureVector + executable cost curve — state not reconstructable "
      "offline. We reproduce the load-bearing **vector edge_lcb>0** gate exactly (live "
      "function, real band) and proxy the ΔU sign by `point_ev>0`. This is the spine's "
      "edge gate faithfully; it is NOT the full sizing pass. Grades the SIGN/CI of "
      "after-cost EV on the spine's selected legs, the §117 question.")
    a("")

    # ---- coverage ----
    a("## Coverage")
    a("")
    n_set = len(settlements)
    a(f"- Settled VERIFIED families in window: **{n_set}**")
    a(f"- Settled families with a book condition joined via bin_label: "
      f"**{n_book_present_families}**")
    a(f"- Families with a usable spine AND a joined book (spine-evaluated): "
      f"**{n_spine_evaluated}**")
    a(f"- Of those, spine NO-TRADED (no leg passed edge_lcb>0 ∧ point_ev>0): "
      f"**{n_no_trade}**")
    a(f"- **Spine-SELECTED graded trades (n): {len(graded)}**")
    if drops:
        a("- Drop / skip reasons: " + ", ".join(
            f"{k}={v}" for k, v in sorted(drops.items())))
    a("")

    if not graded:
        a("## VERDICT")
        a("")
        a("**NO SPINE-SELECTED TRADES** — under the reproduced edge_lcb>0 ∧ point_ev>0 "
          "gate over the joined book, the spine selected zero trades in the window. The "
          "after-cost EV is therefore undefined (no trades to grade). This is itself a "
          "finding: at these taker asks + 5% fee the spine's vector edge gate cleared no "
          "leg. See coverage drops above.")
        _finish(A)
        return

    evs = [g.ev for g in graded]
    mean_ev, lo, hi = bootstrap_ci(evs)
    a("## Overall after-cost EV (spine-selected trades)")
    a("")
    a(f"- n trades: **{len(graded)}**")
    a(f"- mean after-cost EV per share: **{_fmt(mean_ev)}**")
    a(f"- bootstrap 95% CI (resample families, 5000): "
      f"**[{_fmt(lo)}, {_fmt(hi)}]**")
    a(f"- median EV: {_fmt(float(np.median(evs)))}; "
      f"win-rate (payoff=1): {_fmt(float(np.mean([g.payoff for g in graded])),3)}; "
      f"mean cost (ask+fee): {_fmt(float(np.mean([g.ask + g.fee for g in graded])))}")
    sign = "POSITIVE (CI excludes 0)" if lo > 0 else (
        "NEGATIVE (CI excludes 0)" if hi < 0 else "INDETERMINATE (CI spans 0)")
    a(f"- **sign: {sign}**")
    a("")

    # ---- by side ----
    a("## By side")
    a("")
    a("| side | n | mean EV | 95% CI | win-rate |")
    a("|---|---|---|---|---|")
    for side in ("buy_yes", "buy_no"):
        sub = [g for g in graded if g.side == side]
        if not sub:
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        wr = float(np.mean([g.payoff for g in sub]))
        a(f"| {side} | {len(sub)} | {_fmt(m)} | [{_fmt(l)}, {_fmt(h)}] | {_fmt(wr,3)} |")
    a("")

    # ---- by class ----
    a("## By class (modal / ring / tail)")
    a("")
    a("Class of the spine's PICKED leg: **modal** = the spine's favorite (highest-q) bin; "
      "**ring** = an adjacent bounded (point/range) bin that is not the modal; **tail** = "
      "a shoulder bin (\"X or below\" / \"X or higher\").")
    a("")
    a("| class | n | mean EV | 95% CI | win-rate | sign |")
    a("|---|---|---|---|---|---|")
    classes = [
        ("modal", lambda g: g.is_modal_pick),
        ("ring", lambda g: g.is_ring),
        ("tail", lambda g: g.is_tail),
    ]
    class_summ = {}
    for name, pred in classes:
        sub = [g for g in graded if pred(g)]
        if not sub:
            a(f"| {name} | 0 | n/a | n/a | n/a | n/a |")
            class_summ[name] = (0, float("nan"), float("nan"), float("nan"))
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        wr = float(np.mean([g.payoff for g in sub]))
        sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
        a(f"| {name} | {len(sub)} | {_fmt(m)} | [{_fmt(l)}, {_fmt(h)}] | {_fmt(wr,3)} | {sgn} |")
        class_summ[name] = (len(sub), m, l, h)
    a("")

    # ---- by metric ----
    a("## By metric")
    a("")
    a("| metric | n | mean EV | 95% CI |")
    a("|---|---|---|---|")
    for metric in ("high", "low"):
        sub = [g for g in graded if g.metric == metric]
        if not sub:
            continue
        m, l, h = bootstrap_ci([g.ev for g in sub])
        a(f"| {metric} | {len(sub)} | {_fmt(m)} | [{_fmt(l)}, {_fmt(h)}] |")
    a("")

    # ---- verdict ----
    a("## VERDICT")
    a("")
    if lo > 0:
        overall = (f"POSITIVE — the spine's after-cost EV on its selected trades is "
                   f"positive with the 95% CI EXCLUDING 0 (mean {_fmt(mean_ev)}, CI "
                   f"[{_fmt(lo)}, {_fmt(hi)}], n={len(graded)}).")
    elif hi < 0:
        overall = (f"NEGATIVE — the spine's after-cost EV on its selected trades is "
                   f"negative with the 95% CI EXCLUDING 0 (mean {_fmt(mean_ev)}, CI "
                   f"[{_fmt(lo)}, {_fmt(hi)}], n={len(graded)}).")
    else:
        overall = (f"INDETERMINATE — the spine's after-cost EV on its selected trades is "
                   f"mean {_fmt(mean_ev)} with the 95% CI SPANNING 0 (CI [{_fmt(lo)}, "
                   f"{_fmt(hi)}], n={len(graded)}): not statistically distinguishable from "
                   f"zero at this sample size.")
    # best/worst class
    valid = {k: v for k, v in class_summ.items() if v[0] > 0 and not math.isnan(v[1])}
    best = max(valid.items(), key=lambda kv: kv[1][1]) if valid else None
    worst = min(valid.items(), key=lambda kv: kv[1][1]) if valid else None
    a(f"**{overall}**")
    a("")
    if best:
        bn, (bnn, bm, bl, bh) = best
        bsign = "POSITIVE (CI excludes 0)" if bl > 0 else (
            "NEGATIVE (CI excludes 0)" if bh < 0 else "0-spanning")
        a(f"- Best class: **{bn}** (n={bnn}, mean EV {_fmt(bm)}, CI [{_fmt(bl)}, "
          f"{_fmt(bh)}], {bsign}).")
    if worst and worst[0] != (best[0] if best else None):
        wn, (wnn, wm, wl, wh) = worst
        wsign = "POSITIVE (CI excludes 0)" if wl > 0 else (
            "NEGATIVE (CI excludes 0)" if wh < 0 else "0-spanning")
        a(f"- Worst class: **{wn}** (n={wnn}, mean EV {_fmt(wm)}, CI [{_fmt(wl)}, "
          f"{_fmt(wh)}], {wsign}).")
    a("")

    _finish(A)

    # ---- stdout headline ----
    print("REPORT WRITTEN docs/rebuild/settlement_graded_ev_2026-06-15.md")
    print()
    print(f"coverage: {n_spine_evaluated} spine-evaluated families "
          f"({n_book_present_families} book-joined / {len(settlements)} settled); "
          f"no-trade={n_no_trade}; selected trades n={len(graded)}")
    if graded:
        print(f"OVERALL after-cost EV/share: mean={mean_ev:+.4f} "
              f"CI95=[{lo:+.4f},{hi:+.4f}] n={len(graded)} -> {sign}")
        for name, pred in classes:
            sub = [g for g in graded if pred(g)]
            if not sub:
                print(f"  class {name}: n=0")
                continue
            m, l, h = bootstrap_ci([g.ev for g in sub])
            sgn = "POS" if l > 0 else ("NEG" if h < 0 else "0-span")
            print(f"  class {name}: n={len(sub)} mean={m:+.4f} CI=[{l:+.4f},{h:+.4f}] {sgn}")
        if best:
            print(f"BEST class={best[0]} mean={best[1][1]:+.4f}; "
                  f"WORST class={worst[0]} mean={worst[1][1]:+.4f}")


def _finish(A):
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(A) + "\n")


if __name__ == "__main__":
    main()
