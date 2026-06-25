#!/usr/bin/env python3
# Created: 2026-06-24
# Last audited: 2026-06-24
# Authority basis: counterfactual_selection_bias.md + fit_selection_curse_bound_report.md (verdict:
#   admitted buy_no claims ~0.83 / realizes ~0.69, +14pp, monotone in NO price, favorites >=0.95
#   calibrated, buy_yes benign) + the runtime contract src/decision/selection_curse_bound.py +
#   operator laws (no hardcoded bucket edges / no hand-tuned constants in the correction magnitude;
#   settlement-evidenced; tighten-only; do not over-gate buy_yes).
#
#   THIS IS THE NO-LEAK REVISION of scripts/fit_selection_curse_bound.py. It fixes two BLOCKERs the
#   PR #419 frontier review found:
#
#   FIX 1 (SETTLEMENT-AVAILABILITY LEAK). The committed walk_forward_arm_gate keyed the expanding
#   origin on `adm["date"] < d` where `date == target_date`. That let a market whose outcome had NOT
#   yet settled at the simulated decision instant enter training (a same-/adjacent-day earlier target
#   settles AFTER a later target's pre-day decision). The fix gates every training row by a real
#   SETTLEMENT-AVAILABILITY time strictly before the decision instant of any test row it informs, and
#   asserts that invariant. Empirical finding (documented at runtime + in the report): the DB
#   `settled_at` column is UNUSABLE as an as-of gate in the priced window — it was bulk-backfilled
#   (1298/1726 in-window markets carry settled_at=2026-06-24, the rest a ~31-day-lag earlier batch;
#   `recorded_at` only spans 06-15..06-24). So we VERIFY settled_at, find it unusable, and FALL BACK
#   to the most defensible deterministic proxy: settlement_available = target_local_day_END +
#   SETTLE_LAG_H (next-day publish). This is leak-free BY CONSTRUCTION and is documented as a proxy.
#
#   FIX 2 (REPRODUCIBLE PATHS + FAIL-CLOSED WRITER). The DB and artifact paths are CLI args
#   (--forecasts-db/--trades-db/--out) defaulting via src.config.state_path / repo-relative. In
#   production mode (--production / non-scratch --out) the atomic writer FAILS CLOSED (raises) — no
#   silent plain-JSON fallback for the real artifact. The scratchpad/diff path keeps the soft writer.
#
# READ-ONLY w.r.t. the live system: opens the forecasts + trades DBs with ?mode=ro (uri=True).
# NO DB writes. When run with the default --out (scratchpad), NO repo writes either.
"""Self-contained, reproducible, NO-LEAK production fitter for the selection-curse authorization bound.

PIPELINE
1. RE-MATERIALIZE + ADMIT. Over source-matched settled high markets in the price-coverage window,
   re-serve each market's per-bin q + q_lcb/q_ucb band via compute_replacement_posterior_readonly
   (identity sigma; freshest causal cycle; target_date<decision history; serving-age neutralized),
   JOIN as-of-decision YES/NO taker asks, reconstruct the gate's counterfactual buy_no admission
   (q_lcb_no = 1 - q_ucb_yes > fee-adjusted no_ask). Carry per row: decision_iso, settled_at (the raw
   DB value, for the usability audit) AND settle_avail_iso (the deterministic leak-free proxy).

2. MONOTONE FIT + LOWER BAND. Cluster-weighted (1/m_g per settled market-day) PAVA isotonic of
   realized `won` on `no_price`, NON-DECREASING; a cluster-bootstrap 5th-percentile LOWER band on a
   mechanical ascending price grid 0.50..1.00 step 0.05.

3. NO-LEAK WALK-FORWARD ARM GATE. Origins iterate over distinct decision DAYS. For each origin day d
   (with decision instant D), TRAIN = all rows whose settle_avail < (earliest decision instant of the
   test block) and TEST = rows decided on day d. The bound admits a NO test row iff
   min(raw_qlcb_no, realized_lcb(price)) > price+cost. ARM-ELIGIBLE iff the aggregate OOS mean
   over-claim <= 0.01 AND a majority of origins-with-admits individually don't over-claim by >0.01.
   The no-leak property is ASSERTED: max(train.settle_avail) < min(test.decision) for every origin.

4. ARTIFACT. Write selection_curse_bound.json with EXACTLY the SelectionCurseBound fields and verify
   round-trip through the runtime dataclass.
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
# Repo root is resolved from THIS file's location (no absolute literal), so the script is portable.
# --------------------------------------------------------------------------------------------------- #
os.environ.setdefault("ZEUS_PRIMARY_ROOT", "/Users/leofitz/zeus")
ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> repo root (when promoted); falls back below.
if not (ROOT / "src").is_dir():
    # Scratchpad placement: this file does not live under scripts/. Use the worktree the run targets.
    _env_root = os.environ.get("ZEUS_WORKTREE_ROOT")
    ROOT = Path(_env_root) if _env_root else Path("/Users/leofitz/zeus/.claude/worktrees/full-lifecycle-impl")
sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from src.config import cities_by_name  # noqa: E402
import src.config as _cfg  # noqa: E402
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

# --- Re-materialization constants (PROVEN recipe) -------------------------------------------------- #
PUB_LAG_H = 8.0  # publication lag: a cycle is causally-usable only PUB_LAG_H after its source_cycle_time
DV_HIGH = expected_replacement_dependency_identity_by_role("high")["baseline_b0"].data_version
SIGMA_SCALE = {"C": (0.671, 0.149, 0.0), "F": (0.7322, 0.0552, 0.0)}

# Price-coverage window: executable_market_snapshots begins ~2026-05-15.
WINDOW_START = "2026-05-15"
WINDOW_END = "2026-06-22"

# --- Fit constants (PROVEN recipe) ----------------------------------------------------------------- #
SCHEDULE_FEE = 0.10
KNOT_GRID = tuple(round(x, 3) for x in np.round(np.arange(0.50, 1.0001, 0.05), 3))
BOOT_N = 2000
BOOT_LOW_PCTILE = 5.0
WF_BOOT_N = 600
SEED = 20260623

# --- NO-LEAK proxy constant (FIX 1) ---------------------------------------------------------------- #
# Settlement-availability proxy: a weather market's outcome becomes known the morning AFTER its target
# local day closes (the settlement observation publishes next-day). settle_avail = local-day-END +
# SETTLE_LAG_H. 24h is the defensible next-day-publish lag; it is conservative (later availability =
# stricter training gate = LESS look-ahead, never more). The decision instant is local-day-START - 1min
# (pre-day), so an earlier target's settlement clears a later target's decision only when targets are
# >= ~3 calendar days apart under this lag — exactly the look-ahead the leaked target_date<d gate hid.
SETTLE_LAG_H = 24.0


# =================================================================================================== #
# Step 1 — RE-MATERIALIZE the served q / q_lcb band from the DBs and JOIN as-of-decision prices.
# =================================================================================================== #
def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def _localday_start_utc(tz, target_date):
    d = _date.fromisoformat(target_date)
    z = ZoneInfo(tz)
    return datetime(d.year, d.month, d.day, tzinfo=z).astimezone(UTC)


def _settle_avail_utc(tz, target_date, lag_h=SETTLE_LAG_H):
    """Deterministic leak-free settlement-availability proxy = target local-day-END + lag_h."""
    return _localday_start_utc(tz, target_date) + timedelta(days=1) + timedelta(hours=lag_h)


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


def rematerialize_priced_ledger(fcst_db, trades_db, scale_mode="identity", limit=None, offset=0,
                                verbose=True):
    """DB -> per-(market,bin) priced ledger rows. Carries settled_at (raw) + settle_avail_iso (proxy).
    READ-ONLY (?mode=ro)."""
    def _scale(unit):
        if scale_mode == "identity":
            return (1.0, 0.0, 0.0)
        return SIGMA_SCALE.get(str(unit).upper(), (1.0, 0.0, 0.0))

    M._effective_unit_sigma_scale = _scale
    _patch_serving_age()

    c = sqlite3.connect(f"file:{fcst_db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    t = sqlite3.connect(f"file:{trades_db}?mode=ro", uri=True)

    # REALTIME REVISION: also recover the REAL per-market settlement-availability time. The settling
    # daily-high OBSERVATION row (joined via provenance_json.obs_id) carries `high_fetch_utc` /
    # `fetched_at` = when that observation was actually fetched/published. That is the moment the
    # market's outcome first became KNOWABLE. It is per-market-varying, 9-21h after local-day-end
    # (next-morning publish), audited trustworthy (98.0% within 0-48h, 0 negative lags, 40 distinct
    # fetch days vs settled_at's single 71% backfill constant). We carry it as obs_avail_iso.
    markets = c.execute(
        """SELECT DISTINCT so.city, so.target_date, so.winning_bin, so.settlement_value, so.settled_at,
                  COALESCE(o.high_fetch_utc, o.fetched_at, o.fetch_utc) AS obs_avail_iso,
                  json_extract(so.provenance_json,'$.obs_source') AS obs_source
           FROM settlement_outcomes so
           LEFT JOIN observations o
             ON o.id = CAST(json_extract(so.provenance_json,'$.obs_id') AS INTEGER)
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
            # Pass the CURRENT module-global lag explicitly (the default-arg binding froze it at
            # def-time, so a --settle-lag-h override was silently ignored in the prior fitter).
            settle_avail_iso = _settle_avail_utc(tz, target_date, lag_h=SETTLE_LAG_H).isoformat()
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
                "settled_at": mk["settled_at"], "settle_avail_iso": settle_avail_iso,
                "obs_avail_iso": mk["obs_avail_iso"], "obs_source": mk["obs_source"],
                "settlement_unit": su,
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
# Step 1a — settled_at USABILITY AUDIT (FIX 1, prerequisite). Decide whether the raw DB settled_at can
# serve as the as-of gate, or whether we must fall back to the deterministic proxy.
# =================================================================================================== #
def audit_settled_at(ledger_rows, verbose=True):
    """Return a verdict dict on whether `settled_at` is usable as an as-of availability gate.

    Unusable signatures (any -> fall back to proxy):
      - a dominant single backfill-constant settled_at date (>= 50% of market-days share one date), OR
      - implausible settlement lag vs the local day (median lag from target-day-end >> a few days), OR
      - settled_at >= the test decision instant for a large fraction (would zero training).
    """
    led = pd.DataFrame(ledger_rows)
    # collapse to market level (city,target_date) — one settled_at per market
    mk = led.drop_duplicates(subset=["city", "target_date"]).copy()
    mk = mk[mk["settled_at"].notna()].copy()
    n_mk = len(mk)
    if n_mk == 0:
        return {"usable": False, "reason": "no_settled_at", "n_markets": 0}
    mk["sa_date"] = mk["settled_at"].astype(str).str[:10]
    date_counts = mk["sa_date"].value_counts()
    top_date = str(date_counts.index[0])
    top_frac = float(date_counts.iloc[0]) / n_mk
    n_distinct = int(date_counts.shape[0])

    # lag of settled_at after the target local-day-end (use settle_avail base minus the proxy lag)
    lags_h = []
    for _, r in mk.iterrows():
        cfg = cities_by_name.get(r["city"])
        tz = str(getattr(cfg, "timezone", "UTC")) if cfg else "UTC"
        try:
            end = _localday_start_utc(tz, r["target_date"]) + timedelta(days=1)
            sa = _dt(str(r["settled_at"]))
            lags_h.append((sa - end).total_seconds() / 3600.0)
        except Exception:
            pass
    lags_h.sort()
    med_lag_h = float(np.median(lags_h)) if lags_h else float("nan")
    max_lag_h = float(lags_h[-1]) if lags_h else float("nan")

    dominant_constant = top_frac >= 0.50
    implausible_lag = (not np.isnan(med_lag_h)) and (med_lag_h > 96.0)  # >4 days median = backfill
    usable = not (dominant_constant or implausible_lag)
    verdict = {
        "usable": bool(usable),
        "n_markets": n_mk,
        "distinct_settled_dates": n_distinct,
        "top_settled_date": top_date,
        "top_date_fraction": round(top_frac, 3),
        "median_lag_from_dayend_h": round(med_lag_h, 1),
        "max_lag_from_dayend_h": round(max_lag_h, 1),
        "dominant_backfill_constant": bool(dominant_constant),
        "implausible_lag": bool(implausible_lag),
        "reason": (
            "USABLE" if usable else
            ("dominant_backfill_constant(%s=%.0f%%)" % (top_date, 100 * top_frac) if dominant_constant
             else "implausible_median_lag(%.0fh=%.1fd)" % (med_lag_h, med_lag_h / 24.0))
        ),
    }
    if verbose:
        print("[1a] settled_at USABILITY AUDIT:")
        print("     " + json.dumps(verdict))
        if not usable:
            print(f"     -> settled_at UNUSABLE as as-of gate. Falling back to deterministic proxy "
                  f"settle_avail = target_local_day_END + {SETTLE_LAG_H:.0f}h (next-day publish).")
        else:
            print("     -> settled_at usable; the no-leak WF would gate on real settled_at.")
    return verdict


def audit_obs_avail(ledger_rows):
    """Audit the REAL obs-availability time (observations fetch time mapped via provenance.obs_id).

    Trustworthy iff: per-row varying (no dominant backfill constant), plausible next-day-publish lag
    vs target local-day-END (most rows 0-48h, per-market), and no negative lags (fetch before day-end
    would be physically impossible for a settling daily high). This is the DEFINITIVE gate key.
    """
    led = pd.DataFrame(ledger_rows)
    mk = led.drop_duplicates(subset=["city", "target_date"]).copy()
    n_mk = len(mk)
    have = mk["obs_avail_iso"].notna().sum() if "obs_avail_iso" in mk else 0
    lags_h, neg = [], 0
    fdays = {}
    for _, r in mk.iterrows():
        ts = r.get("obs_avail_iso")
        if ts is None or (isinstance(ts, float) and np.isnan(ts)):
            continue
        fdays[str(ts)[:10]] = fdays.get(str(ts)[:10], 0) + 1
        cfg = cities_by_name.get(r["city"])
        tz = str(getattr(cfg, "timezone", "UTC")) if cfg else "UTC"
        try:
            end = _localday_start_utc(tz, r["target_date"]) + timedelta(days=1)
            lag = (_dt(str(ts)) - end).total_seconds() / 3600.0
            lags_h.append(lag)
            if lag < 0:
                neg += 1
        except Exception:
            pass
    lags_h.sort()
    a = np.array(lags_h) if lags_h else np.array([np.nan])
    clean = int(((a >= 0) & (a <= 48)).sum())
    top_frac = (max(fdays.values()) / have) if (fdays and have) else 1.0
    verdict = {
        "n_markets": int(n_mk),
        "have_obs_avail": int(have),
        "coverage_frac": round(float(have) / n_mk, 4) if n_mk else 0.0,
        "distinct_fetch_days": int(len(fdays)),
        "top_fetch_day_frac": round(float(top_frac), 4),
        "lag_h_median": round(float(np.nanmedian(a)), 1),
        "lag_h_p05": round(float(np.nanpercentile(a, 5)), 1),
        "lag_h_p95": round(float(np.nanpercentile(a, 95)), 1),
        "lag_h_max": round(float(np.nanmax(a)), 1),
        "n_negative_lag": int(neg),
        "clean_0_48h_frac": round(clean / len(lags_h), 4) if lags_h else 0.0,
        "trustworthy": bool(
            (have / n_mk if n_mk else 0) >= 0.95 and top_frac < 0.50 and neg == 0
            and 0 < float(np.nanmedian(a)) < 96.0),
    }
    return verdict


# =================================================================================================== #
# Step 1b — reconstruct the ADMITTED buy_no slice (carries decision_iso + settle_avail_iso).
# =================================================================================================== #
def fee_adj(ask: float, fee_rate: float) -> float:
    return ask + fee_rate * ask * (1.0 - ask)


def build_admitted_buy_no(ledger_rows, gate_key: str = "obs_avail",
                          fee_rate: float = SCHEDULE_FEE) -> pd.DataFrame:
    """Reconstruct the counterfactual ADMITTED buy_no slice.

    Columns: date, city, mday, price, cost, won, claim, qlcb_no, plus the leak-gate keys as epoch
    nanoseconds (decision_ns, settle_avail_ns) — integers compare cleanly regardless of pandas datetime
    resolution.

    gate_key selects the settlement-availability time used as the no-leak WF gate:
      - "obs_avail" : REAL per-market settling-observation fetch time (provenance.obs_id ->
                      observations.high_fetch_utc/fetched_at). DEFINITIVE — this is when the outcome
                      first became knowable. Used by the realtime re-validation.
      - "proxy"     : deterministic target_local_day_END + SETTLE_LAG_H (the prior fallback proxy).
      - "settled_at": the raw DB settled_at column (UNUSABLE — bulk-backfill; kept for parity only).
    """
    led = pd.DataFrame(ledger_rows)
    m = led["no_ask"].notna() & led["q_ucb_yes"].notna() & (led["no_ask"] > 0)
    sub = led.loc[m].copy()
    sub["qlcb_no"] = 1.0 - sub["q_ucb_yes"].astype(float)
    sub["cost"] = fee_adj(sub["no_ask"].astype(float), fee_rate)
    adm = sub.loc[sub["qlcb_no"] > sub["cost"]].copy()

    # Leak-gate keys as integer epoch-ns (unambiguous comparison; no dtype-resolution pitfalls).
    # NOTE: pandas 2.x/3.x to_datetime defaults to datetime64[us]; force ns BEFORE int64 so the
    # integers are genuinely nanoseconds-since-epoch (else .astype yields microseconds and the
    # dec_day re-derivation mis-parses to 1970).
    def _to_ns(series):
        return pd.to_datetime(series, utc=True).astype("datetime64[ns, UTC]").astype("int64").values

    decision_ns = _to_ns(adm["decision_iso"])
    if gate_key == "obs_avail":
        if adm["obs_avail_iso"].isna().any():
            n_missing = int(adm["obs_avail_iso"].isna().sum())
            raise ValueError(
                f"obs_avail gate requested but {n_missing} admitted rows lack a real obs fetch time; "
                f"the obs-availability recovery is incomplete — do NOT silently fall back.")
        settle_avail_ns = _to_ns(adm["obs_avail_iso"])
    elif gate_key == "settled_at":
        settle_avail_ns = _to_ns(adm["settled_at"])
    else:  # "proxy"
        settle_avail_ns = _to_ns(adm["settle_avail_iso"])

    out = pd.DataFrame({
        "date": adm["target_date"].astype(str).values,
        "city": adm["city"].astype(str).values,
        "price": adm["no_ask"].astype(float).values,
        "cost": adm["cost"].astype(float).values,
        "won": (1 - adm["did_bin_win"].astype(int)).values,
        "claim": (1.0 - adm["q_yes"].astype(float)).values,
        "qlcb_no": adm["qlcb_no"].astype(float).values,
        "decision_ns": decision_ns.astype("int64"),
        "settle_avail_ns": settle_avail_ns.astype("int64"),
    })
    out["mday"] = out["date"].values
    return out.sort_values(["decision_ns", "date"]).reset_index(drop=True)


# =================================================================================================== #
# Step 2 — PAVA isotonic + cluster-weighted fit + cluster-bootstrap lower band.
# =================================================================================================== #
def pava_nondecreasing(x: np.ndarray, y: np.ndarray, w: np.ndarray):
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
    if len(xs) == 1:
        return np.full(len(grid), levels[0])
    return np.interp(grid, xs, levels)


def cluster_weights(df: pd.DataFrame) -> np.ndarray:
    m_g = df.groupby("mday")["won"].transform("size").astype(float).values
    return 1.0 / m_g


def fit_isotonic_band(df: pd.DataFrame, grid: np.ndarray, boot_n: int = BOOT_N,
                      low_pctile: float = BOOT_LOW_PCTILE, seed: int = SEED):
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
# Step 3 — NO-LEAK walk-forward arm gate (FIX 1).
# =================================================================================================== #
def _bound_lcb_fn(grid: np.ndarray, lcb: np.ndarray):
    g0, g1 = grid[0], grid[-1]

    def f(p: float):
        if p < g0 - 1e-12 or p > g1 + 1e-12:
            return None
        return float(np.interp(p, grid, lcb))

    return f


def walk_forward_arm_gate_noleak(adm: pd.DataFrame, grid: np.ndarray, boot_n: int = WF_BOOT_N):
    """NO-LEAK expanding-origin WF. Origins iterate over distinct DECISION DAYS (calendar date of the
    decision instant). For each origin day:
      - test_block  = rows decided on that day.
      - origin_cut  = the EARLIEST decision instant in the test block.
      - train       = ALL rows whose settle_avail_dt < origin_cut (outcome already KNOWN at decision).
    The bound is fit on train, then applied to test; a NO row is bound-admitted iff
    min(raw_qlcb_no, realized_lcb(price)) > price+cost (OUT_OF_SUPPORT price -> identity -> stays).

    ARM CRITERION — settlement-graded after-cost EV, NOT a fixed over-claim %. The bound is
    TIGHTEN-ONLY: bounded-admitted is a SUBSET of the gate's raw admission, so the bound can only
    REMOVE trades, never add one. Per admitted NO share at as-of price p with fee-adjusted cost c that
    realizes won in {0,1}, after-cost EV/share = won - c. Arm iff the bound removes a NON-EMPTY set of
    OOS trades whose aggregate realized after-cost EV is <= 0 — it strips net-losing buy_no and does
    not sacrifice profit (operator law: positive after-cost EV is the bar; a fixed residual % is not a
    valid standard). Calibration over-claim (realized_lcb - won) is still reported for context.

    NO-LEAK INVARIANT (asserted): max(train.settle_avail_ns) < origin_cut == min(test.decision_ns).
    No training row's outcome settled at/after the simulated decision instant of any test row.
    """
    adm = adm.sort_values("decision_ns").reset_index(drop=True)
    adm["dec_day"] = pd.to_datetime(adm["decision_ns"], utc=True, unit="ns").dt.strftime("%Y-%m-%d")
    days = sorted(adm["dec_day"].unique())
    rows = []
    agg_admit = 0
    agg_lcb_sum = 0.0
    agg_won_sum = 0.0
    agg_resid_sum = 0.0
    origins_with_admits = 0
    leak_violations = 0
    origins_evaluated = 0
    # --- after-cost EV accounting (the arm criterion) -------------------------------------------- #
    ev_raw_sum = 0.0       # sum of (won - cost) over the gate's RAW (unbounded) OOS admits
    ev_bounded_sum = 0.0   # sum of (won - cost) over the BOUND-admitted OOS subset
    ev_removed_sum = 0.0   # sum of (won - cost) over rows the bound REMOVED (raw-admit, bound-reject)
    n_raw = 0
    n_removed = 0

    for i, day in enumerate(days):
        test = adm[adm["dec_day"] == day]
        if len(test) == 0:
            continue
        origin_cut = int(test["decision_ns"].min())  # earliest decision instant in the test block (ns)
        train = adm[adm["settle_avail_ns"] < origin_cut]
        # NO-LEAK ASSERTION: every train row's settlement is strictly before the test's first decision.
        if len(train) > 0:
            tmax = int(train["settle_avail_ns"].max())
            if not (tmax < origin_cut):
                leak_violations += 1
                raise AssertionError(
                    f"LEAK at origin {day}: max(train.settle_avail_ns)={tmax} >= origin_cut={origin_cut}"
                )
        # also: no train row may be decided on the test day (disjoint by construction; verify)
        if len(train) and len(test):
            overlap = set(map(tuple, train[["city", "date"]].values)) & \
                      set(map(tuple, test[["city", "date"]].values))
            if overlap and (train["dec_day"].max() == day):
                raise AssertionError(f"train/test overlap on origin {day}: {list(overlap)[:3]}")

        if train["mday"].nunique() < 5 or len(train) < 60:
            rows.append({"origin": day, "train_n": int(len(train)),
                         "train_mdays": int(train["mday"].nunique()), "test_n": int(len(test)),
                         "bound_admitted": None, "oos_resid_mean": None, "ev_removed": None,
                         "admit_lcb_mean": None, "admit_won_mean": None, "note": "INSUFFICIENT_TRAIN"})
            continue
        origins_evaluated += 1
        _, lcb = fit_isotonic_band(train, grid, boot_n=boot_n, seed=SEED + i)
        f = _bound_lcb_fn(grid, lcb)
        admit_lcb, admit_won = [], []
        o_ev_removed = 0.0
        o_n_removed = 0
        for _, r in test.iterrows():
            price = float(r["price"])
            cost = float(r["cost"])
            won = float(r["won"])
            if not (float(r["qlcb_no"]) > cost):  # the gate's RAW (unbounded) admission
                continue
            ev_share = won - cost
            n_raw += 1
            ev_raw_sum += ev_share
            v = f(price)
            # OUT_OF_SUPPORT (v is None) -> runtime returns identity -> the row STAYS admitted.
            corrected = float(r["qlcb_no"]) if v is None else min(float(r["qlcb_no"]), v)
            if corrected > cost:
                ev_bounded_sum += ev_share
                if v is not None:
                    admit_lcb.append(v)
                    admit_won.append(won)
            else:
                ev_removed_sum += ev_share
                o_ev_removed += ev_share
                o_n_removed += 1
                n_removed += 1
        n_adm = len(admit_lcb)
        if n_adm:
            lcb_arr = np.array(admit_lcb)
            won_arr = np.array(admit_won)
            agg_resid_sum += float(np.sum(lcb_arr - won_arr))
            agg_admit += n_adm
            agg_lcb_sum += float(lcb_arr.sum())
            agg_won_sum += float(won_arr.sum())
            origins_with_admits += 1
            resid_mean = float(np.mean(lcb_arr - won_arr))
            lm = round(float(lcb_arr.mean()), 4)
            wm = round(float(won_arr.mean()), 4)
        else:
            resid_mean, lm, wm = None, None, None
        rows.append({"origin": day, "train_n": int(len(train)),
                     "train_mdays": int(train["mday"].nunique()), "test_n": int(len(test)),
                     "bound_admitted": int(n_adm),
                     "oos_resid_mean": (round(resid_mean, 4) if resid_mean is not None else None),
                     "ev_removed": round(o_ev_removed, 4), "n_removed": o_n_removed,
                     "admit_lcb_mean": lm, "admit_won_mean": wm, "note": ""})

    overall_resid_mean = (agg_lcb_sum - agg_won_sum) / agg_admit if agg_admit else None
    ev_raw_mean = (ev_raw_sum / n_raw) if n_raw else None
    ev_bounded_mean = (ev_bounded_sum / agg_admit) if agg_admit else None
    ev_removed_mean = (ev_removed_sum / n_removed) if n_removed else None
    summary = {
        "origins_evaluated": origins_evaluated,
        "origins_with_admits": origins_with_admits,
        "total_bound_admitted_oos": agg_admit,
        "total_raw_admitted_oos": n_raw,
        "n_removed_oos": n_removed,
        "oos_resid_mean_total": (round(overall_resid_mean, 4) if overall_resid_mean is not None else None),
        # after-cost EV (the arm evidence): raw gate vs bound, and the EV of what the bound stripped.
        "ev_raw_after_cost_sum": round(ev_raw_sum, 4),
        "ev_bounded_after_cost_sum": round(ev_bounded_sum, 4),
        "ev_removed_after_cost_sum": round(ev_removed_sum, 4),
        "ev_raw_after_cost_mean": (round(ev_raw_mean, 5) if ev_raw_mean is not None else None),
        "ev_bounded_after_cost_mean": (round(ev_bounded_mean, 5) if ev_bounded_mean is not None else None),
        "ev_removed_after_cost_mean": (round(ev_removed_mean, 5) if ev_removed_mean is not None else None),
        "ev_improvement_after_cost": round(ev_bounded_sum - ev_raw_sum, 4),  # = -ev_removed_sum
        "leak_violations": leak_violations,
        "settle_lag_h": SETTLE_LAG_H,
        # ARM iff the tighten-only block strips a non-empty OOS set whose aggregate realized after-cost
        # EV is <= 0 — it removes net-losing buy_no and never sacrifices profit. No fixed-% gate.
        "arm_eligible": bool(n_removed > 0 and ev_removed_sum <= 1e-9),
    }
    return rows, summary


# =================================================================================================== #
# Step 4 — artifact emit + runtime round-trip verification.
# =================================================================================================== #
def artifact_hash(price_knots, realized_lcb) -> str:
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
    out["hash_ok"] = (artifact["artifact_hash"]
                      == artifact_hash(artifact["price_knots"], artifact["realized_lcb"]))
    return out


def admit_decision_table(grid, lcb, prices=(0.55, 0.65, 0.75, 0.85, 0.95)):
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


def _is_scratch_path(path: Path) -> bool:
    s = str(path)
    return ("/scratchpad" in s) or s.endswith("_PROD.json") or "/tmp/" in s


def _write_artifact(path: Path, artifact: dict, production: bool):
    """Atomic write via the repo writer. FIX 2: in production mode the writer FAILS CLOSED (raises) —
    no silent plain-JSON fallback for the real artifact. The scratchpad/diff path keeps a soft writer."""
    if production:
        # Production: the atomic writer MUST be available and MUST succeed, or we raise.
        from src.state.paths import write_json_atomic
        write_json_atomic(path, artifact, writer_identity="fit_selection_curse_bound")
        return "write_json_atomic(production,fail-closed)"
    # Scratchpad/diff mode: prefer atomic, but allow a documented fallback (no production artifact).
    try:
        from src.state.paths import write_json_atomic
        write_json_atomic(path, artifact, writer_identity="fit_selection_curse_bound")
        return "write_json_atomic"
    except Exception as e:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
        return f"plain_json(scratch-fallback:{type(e).__name__})"


# =================================================================================================== #
def main():
    global SETTLE_LAG_H
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--built-at", type=str, default="2026-06-24T00:00:00+00:00",
                    help="ISO timestamp for the artifact built_at (deterministic; NEVER datetime.now).")
    # FIX 2: reproducible paths.
    ap.add_argument("--forecasts-db", type=str, default=None,
                    help="Forecasts DB path. Default = config.state_path('zeus-forecasts.db').")
    ap.add_argument("--trades-db", type=str, default=None,
                    help="Trades DB path. Default = config.state_path('zeus_trades.db').")
    ap.add_argument("--out", type=str, default=None,
                    help="Artifact output path. Default = repo-relative scratchpad PROD copy (diff). "
                         "When promoting, pass config.state_path('selection_curse_bound.json').")
    ap.add_argument("--production", action="store_true",
                    help="Production mode: atomic writer fails CLOSED (raises) on any write error; "
                         "no plain-JSON fallback. Use when writing the real served artifact.")
    ap.add_argument("--scale", type=str, default="identity", choices=["identity", "fitted"],
                    help="Sigma regime for the SHIPPED fit. identity = the contract-primary basis.")
    ap.add_argument("--sensitivity", action="store_true",
                    help="Also re-materialize + fit under fitted-sigma k and report stability.")
    ap.add_argument("--limit", type=int, default=None, help="(debug) cap markets re-materialized.")
    ap.add_argument("--settle-lag-h", type=float, default=None,
                    help="Settlement-availability proxy lag (hours after target local-day-END) used as "
                         "the no-leak WF gate key in PROXY mode. Default 24h (next-day publish).")
    ap.add_argument("--gate-key", type=str, default="obs_avail",
                    choices=["obs_avail", "proxy", "settled_at"],
                    help="No-leak WF settlement-availability gate key. obs_avail (DEFAULT, DEFINITIVE) = "
                         "real per-market settling-observation fetch time; proxy = dayend+SETTLE_LAG_H; "
                         "settled_at = raw bulk-backfilled column (parity only).")
    a = ap.parse_args()
    built_at = a.built_at
    if a.settle_lag_h is not None:
        SETTLE_LAG_H = float(a.settle_lag_h)

    # --- FIX 2: resolve DB + out paths via config helpers / repo-relative, NOT absolute literals.
    fcst_db = a.forecasts_db or str(_cfg.state_path("zeus-forecasts.db"))
    trades_db = a.trades_db or str(_cfg.state_path("zeus_trades.db"))
    if a.out:
        out_path = Path(a.out)
    else:
        # default diff target lives in the scratchpad alongside this script (reproducible, no literal).
        out_path = Path(__file__).resolve().parent / "selection_curse_bound_NOLEAK.json"
    production = bool(a.production) or not _is_scratch_path(out_path)
    print(f"[0] forecasts_db={fcst_db}")
    print(f"    trades_db={trades_db}")
    print(f"    out={out_path}  production={production}")

    grid = np.array(KNOT_GRID, dtype=float)

    # --- Step 1: DB -> priced ledger.
    led_rows, led_stats, timing = rematerialize_priced_ledger(
        fcst_db, trades_db, scale_mode=a.scale, limit=a.limit)

    # --- Step 1a: settled_at usability audit (kept — documents WHY we don't use the raw column).
    sa_verdict = audit_settled_at(led_rows)

    # --- Step 1a': obs-availability audit (the REAL settlement-availability time). Lag vs day-end.
    obs_verdict = audit_obs_avail(led_rows)
    print("[1a'] obs-availability (provenance.obs_id -> observations.fetched) AUDIT:")
    print("      " + json.dumps(obs_verdict))

    # --- Step 1b: admitted buy_no slice. Gate key is obs_avail (DEFINITIVE) by default.
    gate_key = a.gate_key
    adm = build_admitted_buy_no(led_rows, gate_key=gate_key, fee_rate=SCHEDULE_FEE)
    n_train = len(adm)
    n_mdays = adm["mday"].nunique()
    raw_realized = float(adm["won"].mean())
    raw_claim = float(adm["claim"].mean())
    print(f"[1] admitted buy_no: n={n_train}  market-days={n_mdays}  "
          f"realized={raw_realized:.3f}  claim={raw_claim:.3f}  gap={raw_claim - raw_realized:+.3f}")
    _gk_desc = {"obs_avail": "REAL obs fetch time (DEFINITIVE)",
                "proxy": "PROXY dayend+%.0fh" % SETTLE_LAG_H,
                "settled_at": "raw settled_at (backfill)"}[gate_key]
    print(f"    WF gate key = {_gk_desc}")

    # --- Step 2: cluster-weighted isotonic point + cluster-bootstrap lower band.
    point, lcb = fit_isotonic_band(adm, grid, boot_n=BOOT_N, low_pctile=BOOT_LOW_PCTILE, seed=SEED)
    print("[2] isotonic point fit on grid: "
          + " ".join(f"{g:.2f}:{p:.3f}" for g, p in zip(grid, point)))
    print(f"    bootstrap p{BOOT_LOW_PCTILE:.0f} lower band   : "
          + " ".join(f"{g:.2f}:{v:.3f}" for g, v in zip(grid, lcb)))

    # --- Step 3: NO-LEAK walk-forward arm gate.
    wf_rows, wf_summary = walk_forward_arm_gate_noleak(adm, grid, boot_n=WF_BOOT_N)
    print("[3] NO-LEAK walk-forward arm gate (origin = decision day; train = settle_avail < decision):")
    print(f"    {'origin':12s} {'tr_n':>5s} {'tr_md':>5s} {'te_n':>4s} {'b_adm':>5s} "
          f"{'lcb_mean':>8s} {'won_mean':>8s} {'resid_mean':>10s}  note")
    for r in wf_rows:
        lm = "-" if r["admit_lcb_mean"] is None else f"{r['admit_lcb_mean']:.3f}"
        wm = "-" if r["admit_won_mean"] is None else f"{r['admit_won_mean']:.3f}"
        rm = "-" if r["oos_resid_mean"] is None else f"{r['oos_resid_mean']:+.4f}"
        ba = "-" if r["bound_admitted"] is None else str(r["bound_admitted"])
        print(f"    {r['origin']:12s} {r['train_n']:5d} {r['train_mdays']:5d} {r['test_n']:4d} "
              f"{ba:>5s} {lm:>8s} {wm:>8s} {rm:>10s}  {r.get('note','')}")
    print(f"    OOS TOTAL: raw_admit={wf_summary['total_raw_admitted_oos']} -> "
          f"bound_admit={wf_summary['total_bound_admitted_oos']} (removed={wf_summary['n_removed_oos']})  "
          f"resid_mean={wf_summary['oos_resid_mean_total']}  "
          f"leak_violations={wf_summary['leak_violations']}")
    print(f"    AFTER-COST EV (the arm evidence): raw_sum={wf_summary['ev_raw_after_cost_sum']:+.4f} -> "
          f"bound_sum={wf_summary['ev_bounded_after_cost_sum']:+.4f}  "
          f"removed_sum={wf_summary['ev_removed_after_cost_sum']:+.4f} "
          f"(mean={wf_summary['ev_removed_after_cost_mean']})  "
          f"improvement={wf_summary['ev_improvement_after_cost']:+.4f}  "
          f"ARM_ELIGIBLE={wf_summary['arm_eligible']}")

    armed_sides = ["buy_no"] if wf_summary["arm_eligible"] else []

    # --- Step 4: artifact + round-trip.
    artifact = build_artifact(grid.tolist(), lcb.tolist(), n_train, armed_sides, built_at)
    writer = _write_artifact(out_path, artifact, production=production)
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

    # --- Optional sensitivity: fitted-sigma k re-fit.
    sens = None
    if a.sensitivity:
        f_rows, _, _ = rematerialize_priced_ledger(
            fcst_db, trades_db, scale_mode="fitted", limit=a.limit, verbose=False)
        admf = build_admitted_buy_no(f_rows, gate_key=gate_key, fee_rate=SCHEDULE_FEE)
        if len(admf) >= 60 and admf["mday"].nunique() >= 5:
            pf, lf = fit_isotonic_band(admf, grid, boot_n=BOOT_N, low_pctile=BOOT_LOW_PCTILE, seed=SEED)
            _, sumf = walk_forward_arm_gate_noleak(admf, grid, boot_n=WF_BOOT_N)
            sens = {"n": len(admf), "market_days": admf["mday"].nunique(),
                    "realized": round(float(admf["won"].mean()), 4),
                    "claim": round(float(admf["claim"].mean()), 4),
                    "fitted_lcb_at": fitted_at(grid, lf, (0.55, 0.70, 0.85, 0.95)),
                    "arm_eligible": sumf["arm_eligible"],
                    "oos_resid_mean_total": sumf["oos_resid_mean_total"]}
            print("[SENS] fitted-sigma k:", json.dumps(sens))

    return {"artifact": artifact, "wf_rows": wf_rows, "wf_summary": wf_summary, "roundtrip": rt,
            "settled_at_verdict": sa_verdict, "obs_avail_verdict": obs_verdict, "gate_key": gate_key,
            "n_train": n_train, "n_mdays": n_mdays, "raw_realized": raw_realized, "raw_claim": raw_claim,
            "point": point.tolist(), "lcb": lcb.tolist(), "grid": grid.tolist(),
            "fitted_at_samples": fitted_at(grid, lcb, (0.55, 0.70, 0.85, 0.95)),
            "admit_table": admit_decision_table(grid, lcb), "timing": timing,
            "ledger_stats": led_stats, "sensitivity": sens, "built_at": built_at}


if __name__ == "__main__":
    main()
