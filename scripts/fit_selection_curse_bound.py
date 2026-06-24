#!/usr/bin/env python3
# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: counterfactual_selection_bias.md + fit_selection_curse_bound_report.md (verdict:
#   admitted buy_no claims ~0.83 / realizes ~0.69, +14pp, monotone in NO price, favorites >=0.95
#   calibrated, buy_yes benign; walk-forward price-conditioned correction collapses the OOS over-claim
#   to +/-0.01; caveats: 33 market-days, 5.4-week single season) + the runtime contract
#   src/decision/selection_curse_bound.py (SelectionCurseBound: price_knots ascending, realized_lcb
#   monotone non-decreasing, n_train, armed_sides, artifact_hash, built_at) + operator laws (no
#   hardcoded bucket edges / no hand-tuned constants in the correction magnitude; settlement-evidenced;
#   tighten-only; do not over-gate buy_yes).
#
#   Re-materialization recipe is the PROVEN no-leak build (scratchpad/build_counterfactual_ledger.py /
#   scma_dataset_build.md): compute_replacement_posterior_readonly, identity sigma, freshest-causal
#   cycle (publication lag), target_date<decision history only, the two backfilled-captured_at
#   reconstruction patches (serving age neutralized), JOINed to as-of-decision executable taker prices
#   from executable_market_snapshots.
#
# READ-ONLY w.r.t. the live system: opens zeus-forecasts.db and zeus_trades.db with ?mode=ro (uri=True).
# NO DB writes, NO repo writes. The artifact is written to the path passed via --out (defaults to the
# scratchpad copy for diffing; the promoted production filename is scripts/fit_selection_curse_bound.py
# and its --out default would be config.state_path("selection_curse_bound.json")).
"""Self-contained, reproducible production fitter for the selection-curse authorization bound.

ONE script, NO scratch-parquet dependency. End-to-end:

  DB ──(re-materialize served q / q_lcb band)──► admitted buy_no ledger ──(PAVA isotonic +
       cluster bootstrap lower band)──► walk-forward arm gate ──► SelectionCurseBound artifact JSON

so the artifact is fully reproducible and re-runnable as settlement data accrues (forward cross-season
monitoring). Every step is inlined here; the only repo imports are the read-only forecast
re-materializer (the byte-faithful served-q recipe) and the runtime dataclass (for round-trip verify).

PIPELINE
1. RE-MATERIALIZE + ADMIT. Over the source-matched settled high markets in the price-coverage window
   (executable_market_snapshots spans ~2026-05-15..present), re-serve each market's per-bin q and the
   q_lcb/q_ucb band via compute_replacement_posterior_readonly (identity sigma; freshest causally-valid
   cycle by publication lag; target_date<decision history; serving-age neutralized). Join as-of-decision
   YES/NO taker asks from executable_market_snapshots. Reconstruct the gate's counterfactual buy_no
   admission: q_lcb_no = 1 - q_ucb_yes > fee-adjusted no_ask (schedule fee 0.10, the gate fallback).
   Settlement truth: NO pays iff the bin did NOT win.

2. MONOTONE FIT + LOWER BAND. Pool-Adjacent-Violators (PAVA) isotonic regression of realized `won` on
   `no_price`, NON-DECREASING (the empirical curse shape: cheaper NO = lower realized rate). Weighted
   1/m_g per settled market-day so one busy date cannot dominate. A LOWER CONFIDENCE BAND is produced by
   CLUSTER BOOTSTRAP over the settled market-days: resample dates with replacement, refit weighted
   isotonic, take the 5th percentile per knot, re-monotonize (PAVA). realized_lcb = that band on a
   mechanical ascending price grid 0.50..1.00 step 0.05. No bucket edges and no constants enter the
   correction MAGNITUDE — the magnitude is the bootstrapped data.

3. WALK-FORWARD ARM GATE (expanding origin by settled date). For each origin: fit the bound on prior
   dates; on the next block admit a NO row iff min(raw_qlcb_no, realized_lcb(price)) > price+cost. buy_no
   is ARM-ELIGIBLE iff the aggregate OOS mean over-claim <= 0.01 AND a majority of origins-with-admits
   individually don't over-claim by >0.01. buy_yes is NOT armed (benign).

4. ARTIFACT. Write selection_curse_bound.json with EXACTLY the SelectionCurseBound fields and verify it
   round-trips through the runtime dataclass.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone, date as _date
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------------------- #
# Paths / environment. ZEUS_PRIMARY_ROOT -> live state for the forecasts DB; sys.path -> the worktree
# so the read-only re-materializer + runtime dataclass import. DB connections are ?mode=ro.
# --------------------------------------------------------------------------------------------------- #
os.environ.setdefault("ZEUS_PRIMARY_ROOT", "/Users/leofitz/zeus")
ROOT = Path("/Users/leofitz/zeus/.claude/worktrees/full-lifecycle-impl")
SCR = Path(
    "/private/tmp/claude-501/-Users-leofitz-zeus/4ae4e5b6-dc15-453a-9837-3e76d0be7333/scratchpad"
)
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from src.config import cities_by_name  # noqa: E402
import src.data.replacement_forecast_materializer as M  # noqa: E402
from src.data.replacement_forecast_materializer import (  # noqa: E402
    ReplacementForecastMaterializeRequest,
    compute_replacement_posterior_readonly as crp,
)
from src.data.openmeteo_ecmwf_ifs9_anchor import OpenMeteoIfs9LocalDayAnchor  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_precision_guard import (  # noqa: E402
    OpenMeteoIfs9PrecisionMetadata,
    evaluate_openmeteo_ecmwf_ifs9_precision_guard,
)
from src.data.replacement_forecast_source_run_identity import (  # noqa: E402
    expected_replacement_dependency_identity_by_role,
)

UTC = timezone.utc

# DB locations: live state, opened read-only. Promotion note: when this script lands at
# scripts/fit_selection_curse_bound.py, prefer config helpers (config.state_path / DB path builders)
# over these literals; the literals here keep the scratchpad run hermetic and explicit.
FCST_DB = "/Users/leofitz/zeus/state/zeus-forecasts.db"
TRADES_DB = "/Users/leofitz/zeus/state/zeus_trades.db"

# --- Re-materialization constants (PROVEN recipe) -------------------------------------------------- #
PUB_LAG_H = 8.0  # publication lag: a cycle is causally-usable only PUB_LAG_H after its source_cycle_time
DV_HIGH = expected_replacement_dependency_identity_by_role("high")["baseline_b0"].data_version
# Live fitted sigma_scale (C: k=0.671 w=0.149; F: k=0.7322 w=0.0552) — for the OPTIONAL sensitivity pass.
SIGMA_SCALE = {"C": (0.671, 0.149, 0.0), "F": (0.7322, 0.0552, 0.0)}

# Price-coverage window: executable_market_snapshots begins ~2026-05-15, so only markets whose pre-day
# decision instant falls in the snapshot window have an as-of-decision price. Re-materialization itself
# reaches back further; the PRICED admission ledger is necessarily within this window.
WINDOW_START = "2026-05-15"
WINDOW_END = "2026-06-22"

# --- Fit constants (PROVEN recipe) ----------------------------------------------------------------- #
SCHEDULE_FEE = 0.10  # gate fallback fee_rate_fraction (execution_price); realized live fills carried ~0.
# Mechanical price-EVALUATION grid (NOT a correction-magnitude constant): uniform ascending 0.50..1.00
# step 0.05. The LAW forbids hand-tuned constants in the correction; this is only where we SAMPLE the
# data-driven bootstrapped band. Every realized_lcb value comes from the data, not this grid.
KNOT_GRID = tuple(round(x, 3) for x in np.round(np.arange(0.50, 1.0001, 0.05), 3))
BOOT_N = 2000          # bootstrap resamples for the SHIPPED band
BOOT_LOW_PCTILE = 5.0  # lower 5th percentile over market-day resamples = the conservative band
WF_BOOT_N = 600        # lighter bootstrap inside the walk-forward gate (speed)
SEED = 20260623        # pinned for deterministic bootstrap


# =================================================================================================== #
# Step 1 — RE-MATERIALIZE the served q / q_lcb band from the DBs and JOIN as-of-decision prices.
#   (inlined byte-faithful from scratchpad/build_counterfactual_ledger.py)
# =================================================================================================== #
def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _localday_start_utc(tz, target_date):
    d = _date.fromisoformat(target_date)
    z = ZoneInfo(tz)
    return datetime(d.year, d.month, d.day, tzinfo=z).astimezone(UTC)


def _lead_bucket(cycle_iso, target_date, tz):
    h = (_localday_start_utc(tz, target_date) - _dt(cycle_iso)).total_seconds() / 3600.0
    return "0-24h" if h <= 24 else ("24-36h" if h <= 36 else ">36h")


class _Bin:
    __slots__ = (
        "bin_id", "lower_c", "upper_c", "center_c",
        "display_unit", "settlement_unit", "rounding_rule",
    )

    def __init__(s, bid, lo, hi, ce, du, su, rr):
        s.bin_id = bid
        s.lower_c = lo
        s.upper_c = hi
        s.center_c = ce
        s.display_unit = du
        s.settlement_unit = su
        s.rounding_rule = rr


def _settlement_unit(city):
    cfg = cities_by_name.get(city)
    return str(getattr(cfg, "settlement_unit", "C") or "C").upper()


def _bins_celsius(market_rows, settlement_unit, rounding_rule):
    out = []

    def toC(v):
        if v is None:
            return None
        return (float(v) - 32.0) * 5.0 / 9.0 if settlement_unit == "F" else float(v)

    for m in market_rows:
        lbl = str(m["range_label"])
        lo = m["range_low"]
        hi = m["range_high"]
        lo_c = toC(lo)
        hi_c = toC(hi)
        ce = None
        if lo_c is not None and hi_c is not None:
            ce = (lo_c + hi_c) / 2.0
        elif hi_c is not None:
            ce = hi_c
        elif lo_c is not None:
            ce = lo_c
        out.append(_Bin(lbl, lo_c, hi_c, ce, settlement_unit, settlement_unit, rounding_rule))
    return tuple(out)


def _precision_metadata(city, target_date, tz):
    cfg = cities_by_name.get(city)
    lat = float(getattr(cfg, "lat"))
    lon = float(getattr(cfg, "lon"))
    start = _localday_start_utc(tz, target_date)
    end = start + timedelta(days=1)
    md = OpenMeteoIfs9PrecisionMetadata(
        city=city, station_id=f"recon:{city}", city_lat=lat, city_lon=lon,
        station_lat=lat, station_lon=lon, requested_lat=lat, requested_lon=lon,
        requested_coordinate_precision_decimals=4, nearest_grid_lat=lat, nearest_grid_lon=lon,
        nearest_grid_distance_km=0.0, native_grid="openmeteo_ecmwf_ifs_9km",
        delivery_grid_resolution="9km", interpolation_method="openmeteo_api_point_interpolation",
        endpoint_mode="hourly_zeus_aggregated", local_day_start_utc=start, local_day_end_utc=end,
        timezone_name=tz, target_local_date=target_date, temperature_unit="celsius",
        anchor_sigma_c=3.0, grid_elevation_m=0.0, station_elevation_m=0.0, land_sea_mask="land",
        city_class="standard", station_mapping_policy="settlement_station",
    )
    return evaluate_openmeteo_ecmwf_ifs9_precision_guard(md)


def _pick_freshest_cycle(conn, city, target_date, decision_instant):
    """Freshest source_cycle_time whose causal availability (cycle + PUB_LAG_H) is <= decision."""
    rows = conn.execute(
        """SELECT DISTINCT source_cycle_time FROM raw_model_forecasts
           WHERE city=? AND metric='high' AND target_date=? AND endpoint='previous_runs'
           ORDER BY source_cycle_time DESC""",
        (city, target_date),
    ).fetchall()
    for r in rows:
        if _dt(r[0]) + timedelta(hours=PUB_LAG_H) <= decision_instant:
            return r[0]
    return None


def _anchor_for(conn, city, target_date, metric, cycle_iso, tz):
    ar = conn.execute(
        """SELECT anchor_value_c, contributing_times_json FROM deterministic_forecast_anchors
           WHERE city=? AND target_date=? AND temperature_metric=? AND source_cycle_time=?
           ORDER BY anchor_id DESC LIMIT 1""",
        (city, target_date, metric, cycle_iso),
    ).fetchone()
    cyc = _dt(cycle_iso)
    start = _localday_start_utc(tz, target_date)
    n_hours = 24
    if ar is not None and ar["contributing_times_json"]:
        valid = tuple(_dt(t) for t in json.loads(ar["contributing_times_json"]))
        anchor_val = float(ar["anchor_value_c"])
    else:
        from src.data.replacement_current_value_serving import read_current_instrument_values
        sv = read_current_instrument_values(
            conn, city=city, metric=metric, target_date=target_date,
            source_cycle_time_iso=cycle_iso,
        )
        ec = sv.get("ecmwf_ifs")
        if ec is None:
            return None
        anchor_val = float(ec.value_c)
        valid = tuple(start + timedelta(hours=h) for h in range(n_hours))
    z = ZoneInfo(tz)
    local = tuple(t.astimezone(z) for t in valid)
    sc = len(valid)
    high_c, low_c = (anchor_val, anchor_val - 8.0) if metric == "high" else (anchor_val + 8.0, anchor_val)
    return OpenMeteoIfs9LocalDayAnchor(
        city_timezone=tz, target_local_date=_date.fromisoformat(target_date),
        high_c=high_c, low_c=low_c, sample_count=sc,
        contributing_local_times=local, contributing_valid_times_utc=valid, source_cycle_time=cyc,
    )


def _winning_label_for_bins(bin_rows, settlement_value):
    for b in bin_rows:
        lo = b["range_low"]
        hi = b["range_high"]
        if lo is not None and settlement_value < float(lo):
            continue
        if hi is not None and settlement_value > float(hi):
            continue
        return str(b["range_label"])
    return None


def _patch_serving_age():
    """Backfilled-captured_at reconstruction patch #1/#2: historical serving reads carried backfilled
    captured_at / age; neutralize so the re-materializer doesn't reject on staleness or weight by a
    fabricated age. Identical to the proven build."""
    import src.data.replacement_current_value_serving as S
    from dataclasses import replace as _replace
    _orig = S.read_current_instrument_values

    def _wrapped(conn, *, city, metric, target_date, source_cycle_time_iso,
                 max_substitution_age_hours=1e9):
        served = _orig(
            conn, city=city, metric=metric, target_date=target_date,
            source_cycle_time_iso=source_cycle_time_iso, max_substitution_age_hours=1e9,
        )
        return {m: _replace(s, captured_at=None, age_hours=0.0) for m, s in served.items()}

    S.read_current_instrument_values = _wrapped


def _market_bins_with_cid(conn, city, target_date, metric):
    """Real traded bins from market_events WITH condition_id (the price-join key). One bin per label;
    keep the freshest condition_id per label."""
    rows = conn.execute(
        """SELECT range_label,
                  MIN(range_low) range_low, MIN(range_high) range_high,
                  (SELECT condition_id FROM market_events me2
                     WHERE me2.city=me.city AND me2.target_date=me.target_date
                       AND me2.temperature_metric=me.temperature_metric
                       AND me2.range_label=me.range_label AND me2.condition_id IS NOT NULL
                     ORDER BY me2.event_id DESC LIMIT 1) condition_id
           FROM market_events me
           WHERE city=? AND target_date=? AND temperature_metric=? AND token_id IS NOT NULL
           GROUP BY range_label
           ORDER BY COALESCE(MIN(range_low),-999), COALESCE(MIN(range_high),999)""",
        (city, target_date, metric),
    ).fetchall()
    return rows


def _price_asof(tconn, condition_id, decision_iso):
    """As-of-decision executable taker prices for a bin's condition_id. Returns (yes_ask, no_ask,
    yes_ts, no_ts); each ask is a float or None. Uses the freshest YES-/NO-labeled snapshot at/before
    the decision instant. A non-numeric orderbook_top_ask ('ABSENT') -> no ask (None)."""
    if condition_id is None:
        return (None, None, None, None)

    def _ask_bid(label):
        r = tconn.execute(
            """SELECT orderbook_top_ask, orderbook_top_bid, captured_at FROM executable_market_snapshots
               WHERE condition_id=? AND outcome_label=? AND captured_at<=?
               ORDER BY captured_at DESC LIMIT 1""",
            (condition_id, label, decision_iso),
        ).fetchone()
        if r is None:
            return (None, None, None)
        ask = r[0]
        bid = r[1]
        try:
            ask = float(ask)
        except (TypeError, ValueError):
            ask = None
        try:
            bid = float(bid)
        except (TypeError, ValueError):
            bid = None
        return (ask, bid, r[2])

    ya, yb, yt = _ask_bid("YES")
    na, nb, nt = _ask_bid("NO")
    return (ya, na, yt, nt)


def rematerialize_priced_ledger(scale_mode="identity", limit=None, offset=0, verbose=True):
    """DB -> per-(market,bin) priced ledger rows with the re-materialized served q / q_lcb band and the
    as-of-decision YES/NO taker asks. Returns (rows, stats, timing). READ-ONLY (?mode=ro)."""
    def _scale(unit):
        if scale_mode == "identity":
            return (1.0, 0.0, 0.0)
        return SIGMA_SCALE.get(str(unit).upper(), (1.0, 0.0, 0.0))

    M._effective_unit_sigma_scale = _scale
    _patch_serving_age()

    c = sqlite3.connect(f"file:{FCST_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    t = sqlite3.connect(f"file:{TRADES_DB}?mode=ro", uri=True)

    # Settled high markets in the PRICE-coverage window, source-matched (must have raw_model_forecasts
    # previous_runs history AND a traded market_events condition_id).
    markets = c.execute(
        """SELECT DISTINCT so.city, so.target_date, so.winning_bin, so.settlement_value, so.settled_at
           FROM settlement_outcomes so
           WHERE so.temperature_metric='high' AND so.authority='VERIFIED'
             AND so.winning_bin IS NOT NULL
             AND so.target_date BETWEEN ? AND ?
             AND EXISTS (SELECT 1 FROM raw_model_forecasts r
                         WHERE r.city=so.city AND r.metric='high' AND r.target_date=so.target_date
                           AND r.endpoint='previous_runs')
             AND EXISTS (SELECT 1 FROM market_events m
                         WHERE m.city=so.city AND m.target_date=so.target_date
                           AND m.temperature_metric='high' AND m.condition_id IS NOT NULL)
           ORDER BY so.target_date, so.city""",
        (WINDOW_START, WINDOW_END),
    ).fetchall()
    total = len(markets)
    if offset:
        markets = markets[offset:]
    if limit:
        markets = markets[:limit]

    rows_out = []
    stats = {
        "OK": 0, "NO_CAUSAL_CYCLE": 0, "NO_ANCHOR": 0, "READONLY_NONE": 0, "CYCLE_TOO_STALE": 0,
        "NOT_IN_CONFIG": 0, "ERROR": 0, "NO_BAND": 0, "priced_bins": 0, "unpriced_bins": 0,
    }
    t0 = time.time()
    times = []
    for i, mk in enumerate(markets):
        city = mk["city"]
        target_date = mk["target_date"]
        cfg = cities_by_name.get(city)
        if cfg is None:
            stats["NOT_IN_CONFIG"] += 1
            continue
        tz = str(getattr(cfg, "timezone", "UTC"))
        ts = time.time()
        try:
            decision = _localday_start_utc(tz, target_date) - timedelta(minutes=1)
            decision_iso = decision.isoformat()
            cycle_iso = _pick_freshest_cycle(c, city, target_date, decision)
            if cycle_iso is None:
                stats["NO_CAUSAL_CYCLE"] += 1
                continue
            cyc = _dt(cycle_iso)
            if (decision - cyc) > timedelta(hours=30):
                stats["CYCLE_TOO_STALE"] += 1
                continue
            su = _settlement_unit(city)
            rounding = (
                "oracle_truncate"
                if str(getattr(cfg, "settlement_source_type", "") or "") == "hko"
                else "wmo_half_up"
            )
            step_c = 1.0 if su == "C" else 5.0 / 9.0
            mkt = _market_bins_with_cid(c, city, target_date, "high")
            if not mkt:
                continue
            bins = _bins_celsius(mkt, su, rounding)
            anchor = _anchor_for(c, city, target_date, "high", cycle_iso, tz)
            if anchor is None:
                stats["NO_ANCHOR"] += 1
                continue
            guard = _precision_metadata(city, target_date, tz)
            dep_avail = decision - timedelta(minutes=1)
            req = ReplacementForecastMaterializeRequest(
                city=city, city_id=city, city_timezone=tz, target_date=target_date,
                temperature_metric="high", baseline_source_run_id=f"recon:baseline:{cycle_iso}",
                baseline_data_version=DV_HIGH, baseline_source_available_at=dep_avail,
                openmeteo_anchor=anchor, openmeteo_source_run_id=f"recon:om9:{cycle_iso}",
                openmeteo_source_available_at=dep_avail, bins=bins, source_cycle_time=cyc,
                computed_at=decision, expires_at=decision + timedelta(hours=3),
                anchor_artifact_id=None, openmeteo_precision_guard=guard,
                anchor_weight=0.80, anchor_sigma_c=3.0, settlement_step_c=step_c,
            )
            res = crp(c, req)
            if res is None:
                stats["READONLY_NONE"] += 1
                continue
        except Exception as e:
            stats["ERROR"] += 1
            if verbose and i < 25:
                print(f"  ERR {city} {target_date}: {type(e).__name__}: {str(e)[:90]}", file=sys.stderr)
            continue
        times.append(time.time() - ts)
        q = dict(res.q)
        qlcb = dict(res.q_lcb_map) if res.q_lcb_map else None
        qucb = dict(res.q_ucb_map) if res.q_ucb_map else None
        if qlcb is None or qucb is None:
            stats["NO_BAND"] += 1
        stats["OK"] += 1
        lead = _lead_bucket(cycle_iso, target_date, tz)
        winning = _winning_label_for_bins(mkt, float(mk["settlement_value"]))
        for b in mkt:
            lbl = str(b["range_label"])
            cid = b["condition_id"]
            qy = float(q.get(lbl, 0.0))
            qly = float(qlcb.get(lbl)) if (qlcb and lbl in qlcb) else None
            quy = float(qucb.get(lbl)) if (qucb and lbl in qucb) else None
            ya, na, yt, nt = _price_asof(t, cid, decision_iso)
            if ya is not None or na is not None:
                stats["priced_bins"] += 1
            else:
                stats["unpriced_bins"] += 1
            rows_out.append({
                "city": city, "target_date": target_date, "month": target_date[:7], "lead_bucket": lead,
                "settled_at": mk["settled_at"], "settlement_unit": su,
                "bin_label": lbl, "condition_id": cid,
                "q_yes": qy, "q_lcb_yes": qly, "q_ucb_yes": quy,
                "yes_ask": ya, "no_ask": na, "yes_price_asof": yt, "no_price_asof": nt,
                "did_bin_win": 1 if lbl == winning else 0,
                "decision_iso": decision_iso, "cycle": cycle_iso,
            })
    elapsed = time.time() - t0
    per = (sum(times) / len(times)) if times else 0.0
    c.close()
    t.close()
    if verbose:
        print(f"[1] re-materialized {len(times)} markets in {elapsed:.1f}s "
              f"({per * 1000:.1f} ms/mkt). candidates={total}.")
        print("    stats:", json.dumps(stats))
        print(f"    emitted {len(rows_out)} bin-rows across {stats['OK']} materialized markets")
    timing = {"elapsed_s": round(elapsed, 2), "ms_per_mkt": round(per * 1000, 2),
              "materialized_markets": len(times), "candidate_markets": total}
    return rows_out, stats, timing


# =================================================================================================== #
# Step 1b — reconstruct the ADMITTED buy_no slice from the priced ledger rows.
#   (inlined from scratchpad/fit_selection_curse_bound.py build_admitted_buy_no — operates on the
#    in-memory rows here instead of a parquet)
# =================================================================================================== #
def fee_adj(ask: float, fee_rate: float) -> float:
    """Per-share executable cost: ask + fee_rate*ask*(1-ask) (execution_price.polymarket_fee shape)."""
    return ask + fee_rate * ask * (1.0 - ask)


def build_admitted_buy_no(ledger_rows, fee_rate: float = SCHEDULE_FEE) -> pd.DataFrame:
    """Reconstruct the counterfactual ADMITTED buy_no slice: q_lcb_no = 1-q_ucb_yes > fee-adj no_ask.

    Returns columns: date, city, mday, price (no_ask), cost (fee-adj), won (=1-did_bin_win), claim,
    qlcb_no. One row per admitted (market,bin). Byte-identical rule to the proven recipe.
    """
    led = pd.DataFrame(ledger_rows)
    m = led["no_ask"].notna() & led["q_ucb_yes"].notna() & (led["no_ask"] > 0)
    sub = led.loc[m].copy()
    sub["qlcb_no"] = 1.0 - sub["q_ucb_yes"].astype(float)
    sub["cost"] = fee_adj(sub["no_ask"].astype(float), fee_rate)
    adm = sub.loc[sub["qlcb_no"] > sub["cost"]].copy()
    out = pd.DataFrame({
        "date": adm["target_date"].astype(str).values,
        "city": adm["city"].astype(str).values,
        "price": adm["no_ask"].astype(float).values,
        "cost": adm["cost"].astype(float).values,
        "won": (1 - adm["did_bin_win"].astype(int)).values,   # NO pays iff bin did NOT win
        "claim": (1.0 - adm["q_yes"].astype(float)).values,   # gate's NO point belief
        "qlcb_no": adm["qlcb_no"].astype(float).values,
    })
    # Cluster = settled market-day (calendar date). The distinct settled dates drive the slice.
    out["mday"] = out["date"].values
    return out.sort_values("date").reset_index(drop=True)


# =================================================================================================== #
# Step 2 — PAVA isotonic (pure numpy) + cluster-weighted fit + cluster-bootstrap lower band.
#   (inlined verbatim from scratchpad/fit_selection_curse_bound.py)
# =================================================================================================== #
def pava_nondecreasing(x: np.ndarray, y: np.ndarray, w: np.ndarray):
    """Weighted Pool-Adjacent-Violators -> non-decreasing step fit. Returns (xs, level) for the unique
    sorted x knots, where level[i] is the fitted realized rate at xs[i]. Parameter-free."""
    order = np.argsort(x, kind="mergesort")
    xs_all, ys_all, ws_all = x[order], y[order], w[order]
    xs_u, idx = np.unique(xs_all, return_inverse=True)
    sw = np.zeros(len(xs_u))
    swy = np.zeros(len(xs_u))
    np.add.at(sw, idx, ws_all)
    np.add.at(swy, idx, ws_all * ys_all)
    vals = swy / np.where(sw == 0, 1.0, sw)
    lvl = list(vals)
    wt = list(sw)
    cnt = [1] * len(vals)
    i = 0
    while i < len(lvl) - 1:
        if lvl[i] > lvl[i + 1] + 1e-15:
            nw = wt[i] + wt[i + 1]
            nl = (lvl[i] * wt[i] + lvl[i + 1] * wt[i + 1]) / nw
            lvl[i] = nl
            wt[i] = nw
            cnt[i] += cnt[i + 1]
            del lvl[i + 1]
            del wt[i + 1]
            del cnt[i + 1]
            if i > 0:
                i -= 1
        else:
            i += 1
    out = np.empty(len(xs_u))
    p = 0
    for k in range(len(lvl)):
        for _ in range(cnt[k]):
            out[p] = lvl[k]
            p += 1
    return xs_u, out


def interp_at(xs: np.ndarray, levels: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Monotone linear interpolation of an isotonic step fit at the eval grid; clamp to [xs0,xs-1].
    Matches the runtime _interp_lcb (linear between knots, flat outside the fit support)."""
    if len(xs) == 1:
        return np.full(len(grid), levels[0])
    return np.interp(grid, xs, levels)  # np.interp clamps to endpoints outside [xs[0], xs[-1]]


def cluster_weights(df: pd.DataFrame) -> np.ndarray:
    """1/m_g per market-day so each distinct date contributes equal TOTAL weight (caps concentration)."""
    m_g = df.groupby("mday")["won"].transform("size").astype(float).values
    return 1.0 / m_g


def fit_isotonic_band(df: pd.DataFrame, grid: np.ndarray, boot_n: int = BOOT_N,
                      low_pctile: float = BOOT_LOW_PCTILE, seed: int = SEED):
    """Cluster-weighted isotonic POINT fit + cluster-bootstrap LOWER band on the eval grid.

    Returns (point_on_grid, lcb_on_grid). lcb is the low_pctile of the bootstrap distribution per knot,
    then re-monotonized (PAVA, weight 1) so it satisfies the runtime non-decreasing invariant exactly.
    """
    x = df["price"].values.astype(float)
    y = df["won"].values.astype(float)
    w = cluster_weights(df)
    xs, lvl = pava_nondecreasing(x, y, w)
    point = interp_at(xs, lvl, grid)

    mdays = df["mday"].unique()
    rng = np.random.default_rng(seed)
    boot = np.empty((boot_n, len(grid)))
    by_day = {d: df[df["mday"] == d] for d in mdays}
    for b in range(boot_n):
        pick = rng.choice(mdays, size=len(mdays), replace=True)
        parts = [by_day[d] for d in pick]
        bs = pd.concat(parts, ignore_index=True)
        bx = bs["price"].values.astype(float)
        by = bs["won"].values.astype(float)
        bw = cluster_weights(bs)
        bxs, blvl = pava_nondecreasing(bx, by, bw)
        boot[b] = interp_at(bxs, blvl, grid)
    lcb = np.percentile(boot, low_pctile, axis=0)
    _, lcb_iso = pava_nondecreasing(grid, lcb, np.ones(len(grid)))
    lcb_iso = np.minimum(lcb_iso, point)
    _, lcb_iso = pava_nondecreasing(grid, lcb_iso, np.ones(len(grid)))
    return point, lcb_iso


# =================================================================================================== #
# Step 3 — walk-forward arm gate (expanding origin by settled date).
#   (inlined verbatim from scratchpad/fit_selection_curse_bound.py)
# =================================================================================================== #
def _bound_lcb_fn(grid: np.ndarray, lcb: np.ndarray):
    """Closure: realized_lcb(price) via monotone interp, identity (return None) outside [grid0,grid-1]."""
    g0, g1 = grid[0], grid[-1]

    def f(p: float):
        if p < g0 - 1e-12 or p > g1 + 1e-12:
            return None
        return float(np.interp(p, grid, lcb))

    return f


def walk_forward_arm_gate(adm: pd.DataFrame, grid: np.ndarray, boot_n: int = WF_BOOT_N):
    """Expanding-origin WF. For each settled date d (after a min train), fit the band on dates < d,
    apply to dates == d: the bound ADMITS a NO row iff realized_lcb(price) > price+cost (the bound's own
    corrected edge). On that bound-admitted OOS set, measure the over-claim residual
    mean(realized_lcb - realized_won).

    ARM RULE (verdict standard, economic break-even only): buy_no is ARM-ELIGIBLE iff the AGGREGATE OOS
    mean over-claim is <= 0.01 AND a strict majority of origins-with-admits individually do not over-claim
    by >0.01 (so a pass cannot rest on one thin early block).
    """
    dates = sorted(adm["date"].unique())
    rows = []
    agg_num = 0.0
    agg_admit = 0
    agg_lcb_sum = 0.0
    agg_won_sum = 0.0
    origins_with_admits = 0
    origins_not_overclaiming = 0
    for i, d in enumerate(dates):
        train = adm[adm["date"] < d]
        test = adm[adm["date"] == d]
        if train["date"].nunique() < 5 or len(train) < 60 or len(test) == 0:
            continue
        _, lcb = fit_isotonic_band(train, grid, boot_n=boot_n, seed=SEED + i)
        f = _bound_lcb_fn(grid, lcb)
        admit_lcb, admit_won = [], []
        for _, r in test.iterrows():
            v = f(float(r["price"]))
            if v is None:
                continue  # OUT_OF_SUPPORT -> identity, bound abstains
            corrected = min(float(r["qlcb_no"]), v)
            if corrected > float(r["cost"]):
                admit_lcb.append(v)
                admit_won.append(float(r["won"]))
        n_adm = len(admit_lcb)
        if n_adm == 0:
            rows.append({"origin": d, "train_n": len(train), "test_n": len(test),
                         "bound_admitted": 0, "oos_resid_mean": None, "oos_resid_sum": 0.0,
                         "admit_lcb_mean": None, "admit_won_mean": None})
            continue
        lcb_arr = np.array(admit_lcb)
        won_arr = np.array(admit_won)
        resid_sum = float(np.sum(lcb_arr - won_arr))   # >0 means lcb over-claims realized -> bad
        resid_mean = float(np.mean(lcb_arr - won_arr))
        agg_num += resid_sum
        agg_admit += n_adm
        agg_lcb_sum += float(lcb_arr.sum())
        agg_won_sum += float(won_arr.sum())
        origins_with_admits += 1
        if resid_mean <= 0.01:
            origins_not_overclaiming += 1
        rows.append({"origin": d, "train_n": int(len(train)), "test_n": int(len(test)),
                     "bound_admitted": int(n_adm), "oos_resid_mean": round(resid_mean, 4),
                     "oos_resid_sum": round(resid_sum, 4),
                     "admit_lcb_mean": round(float(lcb_arr.mean()), 4),
                     "admit_won_mean": round(float(won_arr.mean()), 4)})
    overall_resid_mean = (agg_lcb_sum - agg_won_sum) / agg_admit if agg_admit else None
    summary = {
        "origins_evaluated": sum(1 for r in rows if r["bound_admitted"] is not None),
        "origins_with_admits": origins_with_admits,
        "origins_not_overclaiming": origins_not_overclaiming,
        "total_bound_admitted_oos": agg_admit,
        "oos_resid_sum_total": round(agg_num, 4),
        "oos_resid_mean_total": (round(overall_resid_mean, 4) if overall_resid_mean is not None else None),
        "arm_eligible": bool(
            agg_admit > 0
            and overall_resid_mean is not None and overall_resid_mean <= 0.01
            and origins_with_admits > 0
            and origins_not_overclaiming >= (origins_with_admits + 1) // 2
        ),
    }
    return rows, summary


# =================================================================================================== #
# Step 4 — artifact emit + runtime round-trip verification.
#   (inlined verbatim from scratchpad/fit_selection_curse_bound.py)
# =================================================================================================== #
def artifact_hash(price_knots, realized_lcb) -> str:
    """sha256 over the cells+knots (rounded to a stable precision, joined deterministically)."""
    payload = "|".join(f"{k:.6f}:{v:.6f}" for k, v in zip(price_knots, realized_lcb))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_artifact(price_knots, realized_lcb, n_train, armed_sides, built_at) -> dict:
    pk = [round(float(k), 6) for k in price_knots]
    rl = [round(float(v), 6) for v in realized_lcb]
    return {
        "price_knots": pk,
        "realized_lcb": rl,
        "n_train": int(n_train),
        "armed_sides": list(armed_sides),
        "artifact_hash": artifact_hash(pk, rl),
        "built_at": str(built_at),
    }


def verify_roundtrip(artifact: dict) -> dict:
    """Load the artifact into the runtime SelectionCurseBound and confirm the serving behavior:
    a 0.70 buy_no deflates below cost (self-reject); a 0.97 favorite & buy_yes are untouched."""
    from src.decision.selection_curse_bound import SelectionCurseBound, corrected_side_q_lcb
    b = SelectionCurseBound(
        price_knots=tuple(artifact["price_knots"]),
        realized_lcb=tuple(artifact["realized_lcb"]),
        n_train=artifact["n_train"],
        armed_sides=frozenset(artifact["armed_sides"]),
        artifact_hash=artifact["artifact_hash"],
        built_at=artifact["built_at"],
    )
    out = {}
    cost70 = fee_adj(0.70, SCHEDULE_FEE)
    q70, basis70 = corrected_side_q_lcb(b, side="buy_no", price=0.70, raw_q_lcb=0.83)
    out["mid_0.70"] = {"raw_qlcb": 0.83, "corrected_qlcb": round(q70, 4), "cost": round(cost70, 4),
                       "basis": basis70, "self_rejects": q70 <= cost70, "deflated": q70 < 0.83 - 1e-9}
    cost97 = fee_adj(0.97, SCHEDULE_FEE)
    q97, basis97 = corrected_side_q_lcb(b, side="buy_no", price=0.97, raw_q_lcb=0.99)
    out["fav_0.97"] = {"raw_qlcb": 0.99, "corrected_qlcb": round(q97, 4), "cost": round(cost97, 4),
                       "basis": basis97, "still_admits": q97 > cost97}
    qy, basisy = corrected_side_q_lcb(b, side="buy_yes", price=0.05, raw_q_lcb=0.20)
    out["buy_yes_0.05"] = {"raw_qlcb": 0.20, "corrected_qlcb": round(qy, 4), "basis": basisy,
                           "untouched": abs(qy - 0.20) < 1e-12}
    out["pass"] = bool(out["mid_0.70"]["self_rejects"] and out["mid_0.70"]["deflated"]
                       and out["fav_0.97"]["still_admits"] and out["buy_yes_0.05"]["untouched"])
    # hash self-consistency
    out["hash_ok"] = (artifact["artifact_hash"]
                      == artifact_hash(artifact["price_knots"], artifact["realized_lcb"]))
    return out


def admit_decision_table(grid, lcb, prices=(0.55, 0.65, 0.75, 0.85, 0.95)):
    """At sample NO prices: fitted realized_lcb and whether a NO at that price (cost~price+fee) admits."""
    f = _bound_lcb_fn(np.asarray(grid), np.asarray(lcb))
    rows = []
    for p in prices:
        v = f(p)
        cost = fee_adj(p, SCHEDULE_FEE)
        if v is None:
            rows.append((p, None, cost, "OUT_OF_SUPPORT(identity)"))
        else:
            admits = v > cost
            rows.append((p, round(v, 4), round(cost, 4), "ADMIT" if admits else "SELF-REJECT"))
    return rows


def fitted_at(grid, lcb, prices):
    f = _bound_lcb_fn(np.asarray(grid), np.asarray(lcb))
    return {p: (None if f(p) is None else round(f(p), 4)) for p in prices}


def _write_artifact(path: Path, artifact: dict):
    """Atomic write via the repo writer when available; plain JSON fallback. The runtime reads only the
    SelectionCurseBound fields, so the exact serializer is immaterial to correctness."""
    try:
        from src.state.paths import write_json_atomic
        write_json_atomic(path, artifact, writer_identity="fit_selection_curse_bound")
        return "write_json_atomic"
    except Exception:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(artifact, indent=2) + "\n")
        return "plain_json"


# =================================================================================================== #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--built-at", type=str, default="2026-06-23T00:00:00+00:00",
                    help="ISO timestamp for the artifact built_at (deterministic; NEVER datetime.now). "
                         "Default matches the validated artifact.")
    ap.add_argument("--out", type=str, default=str(SCR / "selection_curse_bound_PROD.json"),
                    help="Artifact output path. Default = scratchpad PROD copy (for diffing). When "
                         "promoted, point this at config.state_path('selection_curse_bound.json').")
    ap.add_argument("--scale", type=str, default="identity", choices=["identity", "fitted"],
                    help="Sigma regime for the SHIPPED fit. identity = the contract-primary basis.")
    ap.add_argument("--sensitivity", action="store_true",
                    help="Also re-materialize + fit under fitted-sigma k and report stability.")
    ap.add_argument("--limit", type=int, default=None, help="(debug) cap markets re-materialized.")
    a = ap.parse_args()
    built_at = a.built_at
    out_path = Path(a.out)

    grid = np.array(KNOT_GRID, dtype=float)

    # --- Step 1: DB -> priced ledger -> admitted buy_no slice (NO scratch-parquet dependency).
    led_rows, led_stats, timing = rematerialize_priced_ledger(scale_mode=a.scale, limit=a.limit)
    adm = build_admitted_buy_no(led_rows, SCHEDULE_FEE)
    n_train = len(adm)
    n_mdays = adm["mday"].nunique()
    raw_realized = float(adm["won"].mean())
    raw_claim = float(adm["claim"].mean())
    print(f"[1] admitted buy_no: n={n_train}  market-days={n_mdays}  "
          f"realized={raw_realized:.3f}  claim={raw_claim:.3f}  gap={raw_claim - raw_realized:+.3f}")

    # --- Step 2: cluster-weighted isotonic point + cluster-bootstrap lower band on the knot grid.
    point, lcb = fit_isotonic_band(adm, grid, boot_n=BOOT_N, low_pctile=BOOT_LOW_PCTILE, seed=SEED)
    print("[2] isotonic point fit on grid: "
          + " ".join(f"{g:.2f}:{p:.3f}" for g, p in zip(grid, point)))
    print(f"    bootstrap p{BOOT_LOW_PCTILE:.0f} lower band   : "
          + " ".join(f"{g:.2f}:{v:.3f}" for g, v in zip(grid, lcb)))

    # --- Step 3: walk-forward arm gate.
    wf_rows, wf_summary = walk_forward_arm_gate(adm, grid, boot_n=WF_BOOT_N)
    print("[3] walk-forward arm gate (expanding origin by settled date):")
    print(f"    {'origin':12s} {'train_n':>7s} {'test_n':>6s} {'b_adm':>5s} "
          f"{'lcb_mean':>8s} {'won_mean':>8s} {'resid_mean':>10s}")
    for r in wf_rows:
        lm = "-" if r["admit_lcb_mean"] is None else f"{r['admit_lcb_mean']:.3f}"
        wm = "-" if r["admit_won_mean"] is None else f"{r['admit_won_mean']:.3f}"
        rm = "-" if r["oos_resid_mean"] is None else f"{r['oos_resid_mean']:+.4f}"
        print(f"    {r['origin']:12s} {r['train_n']:7d} {r['test_n']:6d} {r['bound_admitted']:5d} "
              f"{lm:>8s} {wm:>8s} {rm:>10s}")
    print(f"    OOS TOTAL: admitted={wf_summary['total_bound_admitted_oos']}  "
          f"resid_sum={wf_summary['oos_resid_sum_total']:+.4f}  "
          f"resid_mean={wf_summary['oos_resid_mean_total']}  "
          f"origins_safe={wf_summary['origins_not_overclaiming']}/{wf_summary['origins_with_admits']}  "
          f"ARM_ELIGIBLE={wf_summary['arm_eligible']}")

    armed_sides = ["buy_no"] if wf_summary["arm_eligible"] else []
    # buy_yes never armed (benign per verdict): explicitly omitted from armed_sides.

    # --- Step 4: artifact + round-trip.
    artifact = build_artifact(grid.tolist(), lcb.tolist(), n_train, armed_sides, built_at)
    writer = _write_artifact(out_path, artifact)
    print(f"[4] WROTE artifact ({writer}) -> {out_path}")
    print("    " + json.dumps({k: artifact[k] for k in
          ("n_train", "armed_sides", "artifact_hash", "built_at")}))

    print("[4] serving table (NO at price, cost~price+fee):")
    print(f"    {'no_price':>8s} {'realized_lcb':>12s} {'cost':>7s}  decision")
    for p, v, cost, dec in admit_decision_table(grid, lcb):
        vs = "-" if v is None else f"{v:.4f}"
        print(f"    {p:8.2f} {vs:>12s} {cost:7.4f}  {dec}")

    rt = verify_roundtrip(artifact)
    print("[VERIFY] round-trip through runtime SelectionCurseBound:")
    print("    mid 0.70 buy_no :", rt["mid_0.70"])
    print("    fav 0.97 buy_no :", rt["fav_0.97"])
    print("    buy_yes 0.05    :", rt["buy_yes_0.05"])
    print(f"    HASH_OK = {rt['hash_ok']}   ROUND-TRIP PASS = {rt['pass']}")

    # --- Optional sensitivity: fitted-sigma k re-fit (full re-materialization under fitted scale).
    sens = None
    if a.sensitivity:
        f_rows, _, _ = rematerialize_priced_ledger(scale_mode="fitted", limit=a.limit, verbose=False)
        admf = build_admitted_buy_no(f_rows, SCHEDULE_FEE)
        if len(admf) >= 60 and admf["mday"].nunique() >= 5:
            pf, lf = fit_isotonic_band(admf, grid, boot_n=BOOT_N, low_pctile=BOOT_LOW_PCTILE, seed=SEED)
            _, sumf = walk_forward_arm_gate(admf, grid, boot_n=WF_BOOT_N)
            sens = {"n": len(admf), "market_days": admf["mday"].nunique(),
                    "realized": round(float(admf["won"].mean()), 4),
                    "claim": round(float(admf["claim"].mean()), 4),
                    "fitted_lcb_at": fitted_at(grid, lf, (0.55, 0.70, 0.85, 0.95)),
                    "arm_eligible": sumf["arm_eligible"],
                    "oos_resid_mean_total": sumf["oos_resid_mean_total"]}
            print("[SENS] fitted-sigma k:", json.dumps(sens))

    return {"artifact": artifact, "wf_rows": wf_rows, "wf_summary": wf_summary, "roundtrip": rt,
            "n_train": n_train, "n_mdays": n_mdays, "raw_realized": raw_realized, "raw_claim": raw_claim,
            "point": point.tolist(), "lcb": lcb.tolist(), "grid": grid.tolist(),
            "fitted_at_samples": fitted_at(grid, lcb, (0.55, 0.70, 0.85, 0.95)),
            "admit_table": admit_decision_table(grid, lcb), "timing": timing,
            "ledger_stats": led_stats, "sensitivity": sens, "built_at": built_at}


if __name__ == "__main__":
    main()
