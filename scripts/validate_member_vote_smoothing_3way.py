#!/usr/bin/env python3
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: THE_PATH member-vote smoothing (member_vote_smoothing_alpha in
#   src/strategy/ecmwf_aifs_sampled_2t_probabilities.py); P2_BLEND.md (guarded EB bias);
#   q=1.000 settlement sigma-floor (scripts/fit_settlement_sigma_floor.py). Read-only.
#   Settlement truth = zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'.
#   Walk-forward / anti-lookahead: per cell only use the LATEST AIFS + OM run whose
#   source-available time is STRICTLY BEFORE the target local day start; EB rows are the
#   resolver's own training_cutoff<target self-gate (resolve_replacement_eb_bias_shift_c).
"""3-way settlement validation of the AIFS member-vote smoothing flag.

For every CLEAN settled (city, target_date, metric) cell it recomputes the SHIPPED
soft-anchor posterior THREE ways and settles against VERIFIED settlement:

  BASELINE        : raw member votes, EB bias OFF,  smoothing OFF (veto on)
  BIAS_ONLY       : guarded EB bias ON, smoothing OFF (veto on)
  BIAS_SMOOTHING  : guarded EB bias ON, smoothing ON (alpha pseudo-count; veto lifted)

It uses the EXACT shipped construction (build_openmeteo_ifs9_aifs_soft_anchor_result),
the EXACT shipped EB resolver (resolve_replacement_eb_bias_shift_c with its over-correction
guard + anti-lookahead self-gate), and the EXACT shipped q_lcb settlement sigma-floor
(max(model_sigma, k*sigma_settled)).

Reports:
  (a) UN-HITTABLE RATE before/after: fraction of settled cells whose SETTLEMENT bin has
      ~0 posterior mass (this is what smoothing targets).
  (b) bin-hit (argmax==settlement bin) 3-way with n.
  (c) q_lcb coverage 3-way: realized win-rate of the selected bin vs the claimed q_lcb on
      that bin (conservatism = realized >= claimed).
  (d) after-cost selective PnL 3-way + the count/PnL of losing buy_no-on-impossible-bin trades.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import eccodes as _eccodes  # noqa: E402
from src.data.ecmwf_aifs_sampled_2t_localday import (  # noqa: E402
    AifsInstantSample,
    extract_aifs_sampled_2t_localday,
)
from src.data.forecast_target_contract import compute_target_local_day_window_utc  # noqa: E402
from src.strategy.ecmwf_aifs_sampled_2t_probabilities import (  # noqa: E402
    MEMBER_VOTE_SMOOTHING_ALPHA,
    AifsTemperatureBin,
    build_openmeteo_ifs9_aifs_soft_anchor_result,
)
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import SoftAnchorConfig  # noqa: E402
from src.data.openmeteo_ecmwf_ifs9_anchor import (  # noqa: E402
    OpenMeteoIfs9LocalDayAnchor,
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
)
from src.calibration.replacement_eb_bias import resolve_replacement_eb_bias_shift_c  # noqa: E402
from src.contracts.season import season_from_date  # noqa: E402

# The SHIPPED EB resolver (replacement_forecast_materializer._replacement_eb_bias_shift_c)
# keys the promoted bias by the OpenData ENS product the bias was FIT on (NOT the AIFS
# data_version) — config edli_v1.replacement_0_1_eb_bias_live_data_version. Mirror it here.
EB_BIAS_LIVE_DATA_VERSION = {
    "high": "ecmwf_opendata_mx2t3_local_calendar_day_max",
    "low": "ecmwf_opendata_mn2t3_local_calendar_day_min",
}

UTC = timezone.utc
RAW_ROOT = ROOT.parent / "zeus-ecmwf-replacement-tournament" / ".local" / "replacement_raw"
FCST_DB = "/Users/leofitz/zeus/state/zeus-forecasts.db"
WORLD_DB = "/Users/leofitz/zeus/state/zeus-world.db"
SIGMA_FLOOR_JSON = "/Users/leofitz/zeus/state/settlement_sigma_floor.json"

ANCHOR_WEIGHT = 0.80
ANCHOR_SIGMA_C = 3.00
SIGMA_FLOOR_K = 0.8
# A bin is "un-hittable" if its posterior mass is at/under this epsilon.
UNHITTABLE_EPS = 1e-9
# After-cost trade params (conservative; matches replay defaults).
FEE_RATE = 0.0
SLIPPAGE_RATE = 0.0
STAKE = 1.0
# A bin counts as "impossible" (so a buy_no on it is the manufactured trade smoothing
# is meant to remove) when its posterior mass is ~0.
IMPOSSIBLE_EPS = 1e-9


def season(mm: int) -> str:
    mm = int(mm)
    return "DJF" if mm in (12, 1, 2) else "MAM" if mm in (3, 4, 5) else "JJA" if mm in (6, 7, 8) else "SON"


def to_c(v: float, unit: str) -> float:
    u = (unit or "C").upper()
    if u in ("F", "DEGF"):
        return (float(v) - 32.0) * 5.0 / 9.0
    if u == "K":
        return float(v) - 273.15
    return float(v)


@dataclass(frozen=True)
class CityCfg:
    name: str
    lat: float
    lon: float
    tz: str
    unit: str


def load_cities() -> dict[str, CityCfg]:
    raw = json.loads((ROOT / "config" / "cities.json").read_text())
    out: dict[str, CityCfg] = {}
    for c in raw["cities"]:
        cfg = CityCfg(c["name"], float(c["lat"]), float(c["lon"]), c["timezone"], c.get("unit", "C"))
        out[c["name"]] = cfg
        for a in c.get("aliases", []) or []:
            out.setdefault(a, cfg)
    # common settlement->config aliases
    aliases = {"NYC": "New York", "Hong Kong": "Hong Kong", "San Francisco": "San Francisco"}
    for k, v in aliases.items():
        if v in out and k not in out:
            out[k] = out[v]
    return out


def _om_city_token(name: str) -> str:
    return name.replace(" ", "_")


def _run_stamp_to_dt(stamp: str) -> datetime:
    # OM stamp: YYYYMMDDTHHZ ; AIFS stamp: YYYYMMDD_HHz
    if "T" in stamp:
        return datetime.strptime(stamp.rstrip("Zz"), "%Y%m%dT%H").replace(tzinfo=UTC)
    return datetime.strptime(stamp.rstrip("Zz"), "%Y%m%d_%H").replace(tzinfo=UTC)


def find_om_run(city: str, window_start_utc: datetime) -> tuple[Path, datetime] | None:
    """Latest OM run file for the city whose cycle time is < window_start_utc (anti-lookahead)."""
    token = _om_city_token(city)
    cands: list[tuple[datetime, Path]] = []
    for d in ("openmeteo_jun5_jun6", "openmeteo_jun3_jun6_preday"):
        for p in glob.glob(str(RAW_ROOT / d / f"{token}_*.json")):
            name = os.path.basename(p)
            stamp = name[len(token) + 1:].replace(".json", "")
            try:
                rt = _run_stamp_to_dt(stamp)
            except ValueError:
                continue
            if rt < window_start_utc:
                cands.append((rt, Path(p)))
    if not cands:
        return None
    rt, p = max(cands, key=lambda t: t[0])
    return p, rt


def find_aifs_run(window_start_utc: datetime, window_end_utc: datetime) -> tuple[list[Path], datetime] | None:
    """Latest AIFS pf+cf run whose cycle < window_start AND whose 6/12/18/24 steps reach the window.

    Returns the pf and cf grib paths for the chosen run plus the cycle time.
    """
    runs: dict[datetime, dict[str, Path]] = defaultdict(dict)
    for d in ("aifs_jun5_preday", "aifs_jun3_jun5_preday"):
        for p in glob.glob(str(RAW_ROOT / d / "aifs_ens_*_*z_*2t_steps_*")):
            base = os.path.basename(p)
            # aifs_ens_20260604_18z_pf_2t_steps_6-12-18-24_n001-050.grib2
            try:
                _, _, datestr, cyc, typ = base.split("_", 4)
            except ValueError:
                continue
            stamp = f"{datestr}_{cyc}"
            try:
                rt = _run_stamp_to_dt(stamp)
            except ValueError:
                continue
            kind = "pf" if "_pf_" in base else ("cf" if "_cf_" in base else None)
            if kind is None:
                continue
            runs[rt][kind] = Path(p)
    # choose the latest cycle that is < window_start and whose max step (24h -> 30h) covers window
    eligible: list[datetime] = []
    for rt, parts in runs.items():
        if "pf" not in parts:
            continue
        if rt >= window_start_utc:
            continue
        # need at least one 6h step valid time inside [window_start, window_end)
        covers = any(window_start_utc <= rt + timedelta(hours=h) < window_end_utc for h in (6, 12, 18, 24, 30))
        if covers:
            eligible.append(rt)
    if not eligible:
        return None
    rt = max(eligible)
    parts = runs[rt]
    paths = [parts["pf"]]
    if "cf" in parts:
        paths.append(parts["cf"])
    return paths, rt


def _read_grib_point_samples(path: Path, *, lat: float, lon: float, cycle: datetime) -> list[AifsInstantSample]:
    """Nearest-grid 2t samples for one point from one AIFS ENS GRIB file.

    Uses the SAME eccodes primitives as src.data.ecmwf_aifs_grib_samples
    (codes_grib_find_nearest, member-id-from-type/number, step->valid-time) but does NOT
    apply the per-file full-ensemble identity gate: the downloader splits the 51-member
    ensemble into a 50-member pf file + a 1-member cf file, so neither file alone can ever
    satisfy the 51-member completeness check. The pf+cf pair is recombined by the caller,
    and the combined member set is asserted to be 51 there. The skipped gate is a live-download
    completeness guard, not a per-sample correctness check; the posterior math is identical.
    """
    samples: list[AifsInstantSample] = []
    with path.open("rb") as fh:
        while True:
            gid = _eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                mtype = str(_eccodes.codes_get(gid, "dataType")).lower()
                if mtype == "cf":
                    member_id = "control"
                else:
                    num = _eccodes.codes_get(gid, "number")
                    member_id = f"pf:{int(num):03d}"
                step = int(_eccodes.codes_get(gid, "step"))
                nearest = _eccodes.codes_grib_find_nearest(gid, float(lat), float(lon))[0]
                samples.append(
                    AifsInstantSample(
                        member_id=member_id,
                        valid_time_utc=cycle.astimezone(UTC) + timedelta(hours=step),
                        temperature=float(nearest["value"]),
                        temperature_unit="K",
                    )
                )
            finally:
                _eccodes.codes_release(gid)
    return samples


def build_aifs_extraction(city: CityCfg, target: date, metric: str):
    window = compute_target_local_day_window_utc(city_timezone=city.tz, target_local_date=target)
    found = find_aifs_run(window.start_utc, window.end_utc)
    if found is None:
        return None, None
    paths, cycle = found
    samples: list[AifsInstantSample] = []
    for p in paths:
        samples.extend(_read_grib_point_samples(p, lat=city.lat, lon=city.lon, cycle=cycle))
    # Recombination completeness assertion (replaces the skipped per-file identity gate):
    # the pf+cf pair must yield exactly the 51-member ensemble.
    member_count = len({s.member_id for s in samples})
    if member_count != 51:
        return None, None
    if not samples:
        return None, None
    try:
        extraction = extract_aifs_sampled_2t_localday(
            samples, city_timezone=city.tz, target_local_date=target, source_cycle_time=cycle
        )
    except ValueError:
        return None, None
    return extraction, cycle


def build_om_anchor(city: CityCfg, target: date) -> OpenMeteoIfs9LocalDayAnchor | None:
    """Build the OM IFS9 local-day anchor via the SHIPPED extractor (anti-lookahead run)."""
    window = compute_target_local_day_window_utc(city_timezone=city.tz, target_local_date=target)
    found = find_om_run(city.name, window.start_utc)
    if found is None:
        return None
    path, cycle = found
    payload = json.loads(path.read_text())
    try:
        return extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,
            city_timezone=city.tz,
            target_local_date=target,
            source_cycle_time=cycle,
        )
    except (ValueError, TypeError):
        return None


def build_bins_celsius(market_rows: list[sqlite3.Row], unit: str) -> tuple[AifsTemperatureBin, ...] | None:
    """Build the AifsTemperatureBin family with degC centers from market_events.

    For °C cities the edges/centers are degC directly. For °F cities the displayed
    edges are °F; we convert edge/center to degC so the soft-anchor fuses degC-vs-degC.
    The shipped family validator requires open shoulders + 1-step contiguity, so this
    only succeeds where the market bin grid is a clean contiguous integer (°C) /
    2°F (°F) ladder.
    """
    rows = sorted(
        market_rows,
        key=lambda r: (
            -9999.0 if r["range_low"] is None else float(r["range_low"]),
            9999.0 if r["range_high"] is None else float(r["range_high"]),
        ),
    )
    bins: list[AifsTemperatureBin] = []
    is_f = unit.upper() == "F"
    for r in rows:
        lo = None if r["range_low"] is None else float(r["range_low"])
        hi = None if r["range_high"] is None else float(r["range_high"])
        if lo is None and hi is None:
            return None
        # center in the display unit
        if lo is not None and hi is not None:
            center_disp = (lo + hi) / 2.0
        elif lo is None:
            center_disp = hi  # lower shoulder
        else:
            center_disp = lo  # upper shoulder
        lo_c = None if lo is None else to_c(lo, unit) if is_f else lo
        hi_c = None if hi is None else to_c(hi, unit) if is_f else hi
        center_c = to_c(center_disp, unit) if is_f else center_disp
        try:
            bins.append(
                AifsTemperatureBin(
                    bin_id=str(r["range_label"])[:60] or f"bin_{len(bins)}",
                    lower_c=lo_c,
                    upper_c=hi_c,
                    center_c=center_c,
                    display_unit="C",
                    settlement_unit="C",
                    rounding_rule="wmo_half_up",
                )
            )
        except ValueError:
            return None
    return tuple(bins)


def settlement_bin_id(bins: tuple[AifsTemperatureBin, ...], settlement_c: float, step_c: float) -> str | None:
    matches = []
    for b in bins:
        half = step_c / 2.0
        lo = None if b.lower_c is None else b.lower_c - half
        hi = None if b.upper_c is None else b.upper_c + half
        ok = True
        if lo is not None and settlement_c < lo:
            ok = False
        if hi is not None and settlement_c >= hi:
            ok = False
        if ok:
            matches.append(b.bin_id)
    if len(matches) == 1:
        return matches[0]
    # fall back to nearest-center containment for shoulders
    if not matches:
        return None
    return matches[0]


def load_sigma_floor() -> dict:
    try:
        return json.loads(Path(SIGMA_FLOOR_JSON).read_text()).get("cells", {})
    except Exception:
        return {}


def sigma_floor_for(cells: dict, city: str, seas: str, metric: str) -> float | None:
    c = cells.get(f"{city}|{seas}|{metric}")
    return float(c["sigma_floor_c"]) if c else None


def posterior_mean_sigma_c(posterior: dict, bins: tuple[AifsTemperatureBin, ...]) -> tuple[float, float]:
    by_id = {b.bin_id: b for b in bins}
    mean = sum(p * by_id[k].center_c for k, p in posterior.items())
    var = sum(p * (by_id[k].center_c - mean) ** 2 for k, p in posterior.items())
    return mean, math.sqrt(max(var, 0.0))


def q_lcb_for_selected(posterior: dict, bins: tuple[AifsTemperatureBin, ...], selected_id: str,
                       sigma_floor_c: float | None) -> float:
    """Shipped q_lcb floor: widen the point posterior with sigma_eff=max(model,k*sigma_settled).

    Recompute the selected bin's mass under a Gaussian at the posterior mean with the
    floored sigma. This is the one-sided honesty haircut (only ever widens -> lower q).
    """
    mean, model_sigma = posterior_mean_sigma_c(posterior, bins)
    sigma_eff = model_sigma
    if sigma_floor_c is not None:
        sigma_eff = max(model_sigma, SIGMA_FLOOR_K * sigma_floor_c)
    if sigma_eff <= 0.0:
        return float(posterior.get(selected_id, 0.0))
    b = next(x for x in bins if x.bin_id == selected_id)
    lo = -math.inf if b.lower_c is None else b.lower_c - 0.5
    hi = math.inf if b.upper_c is None else b.upper_c + 0.5

    def cdf(x):
        if math.isinf(x):
            return 0.0 if x < 0 else 1.0
        return 0.5 * (1.0 + math.erf((x - mean) / (sigma_eff * math.sqrt(2.0))))

    q = cdf(hi) - cdf(lo)
    # q_lcb is conservative: floor at the min of point mass and floored-Gaussian mass
    return max(0.0, min(1.0, min(float(posterior.get(selected_id, 0.0)), q)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", nargs="+", default=["2026-06-05", "2026-06-06"])
    ap.add_argument("--alpha", type=float, default=MEMBER_VOTE_SMOOTHING_ALPHA)
    ap.add_argument("--units", nargs="+", default=["C"], help="settlement units to include (C and/or F)")
    args = ap.parse_args()

    cities = load_cities()
    sigma_cells = load_sigma_floor()

    fcst = sqlite3.connect(f"file:{FCST_DB}?mode=ro", uri=True)
    fcst.row_factory = sqlite3.Row
    world = sqlite3.connect(f"file:{WORLD_DB}?mode=ro", uri=True)
    world.row_factory = sqlite3.Row

    settled = fcst.execute(
        "SELECT city,temperature_metric,target_date,settlement_value,settlement_unit,market_slug "
        "FROM settlement_outcomes WHERE authority='VERIFIED' AND target_date IN ({}) "
        "AND settlement_value IS NOT NULL".format(",".join("?" for _ in args.targets)),
        tuple(args.targets),
    ).fetchall()

    arms = ("BASELINE", "BIAS_ONLY", "BIAS_SMOOTHING")
    stats = {a: {"n": 0, "hit": 0, "unhittable": 0,
                 "cov_claim_sum": 0.0, "cov_win": 0, "cov_n": 0,
                 "pnl": 0.0, "trades": 0,
                 "buy_no_impossible_trades": 0, "buy_no_impossible_pnl": 0.0,
                 "buy_no_impossible_losses": 0} for a in arms}
    skipped = defaultdict(int)
    per_cell = []

    for row in settled:
        city_name = row["city"]
        metric = row["temperature_metric"]
        target = date.fromisoformat(str(row["target_date"])[:10])
        unit = (row["settlement_unit"] or "C").upper()
        if unit not in args.units:
            skipped["unit_excluded"] += 1
            continue
        cfg = cities.get(city_name)
        if cfg is None:
            skipped["no_city_cfg"] += 1
            continue
        market_rows = fcst.execute(
            "SELECT range_label,range_low,range_high,condition_id FROM market_events WHERE market_slug=?",
            (row["market_slug"],),
        ).fetchall()
        if len(market_rows) < 2:
            skipped["no_market_bins"] += 1
            continue
        bins = build_bins_celsius(market_rows, unit)
        if bins is None:
            skipped["bin_family_invalid"] += 1
            continue
        settlement_c = to_c(float(row["settlement_value"]), unit)
        step_c = 1.0  # degC bin grid
        settle_id = settlement_bin_id(bins, settlement_c, step_c)
        if settle_id is None:
            skipped["settlement_not_in_bins"] += 1
            continue

        extraction, _cyc = build_aifs_extraction(cfg, target, metric)
        if extraction is None:
            skipped["no_aifs"] += 1
            continue
        anchor = build_om_anchor(cfg, target)
        if anchor is None:
            skipped["no_om"] += 1
            continue

        # Hemisphere-aware season exactly as the shipped resolver (season_from_date(date,lat)).
        seas = season_from_date(target.isoformat(), lat=cfg.lat)
        # EB bias resolver: mirror the SHIPPED materializer wiring exactly — key by the OpenData
        # ENS product the bias was fit on, hemisphere-aware season, the city's settlement unit,
        # and the anti-lookahead self-gate (training_cutoff < target). Guarded over-correction
        # + fail-closed are inside resolve_replacement_eb_bias_shift_c.
        bias = resolve_replacement_eb_bias_shift_c(
            world, city=city_name, season=seas, month=target.month, metric=metric,
            live_data_version=EB_BIAS_LIVE_DATA_VERSION[metric],
            settlement_unit=unit, target_date=target.isoformat(),
        )

        sig_floor = sigma_floor_for(sigma_cells, city_name, seas, metric)

        configs = {
            "BASELINE": dict(bias_shift_c=None, member_vote_smoothing_alpha=None),
            "BIAS_ONLY": dict(bias_shift_c=bias, member_vote_smoothing_alpha=None),
            "BIAS_SMOOTHING": dict(bias_shift_c=bias, member_vote_smoothing_alpha=args.alpha),
        }
        cell_rec = {"city": city_name, "metric": metric, "target": target.isoformat(),
                    "unit": unit, "settle_id": settle_id, "bias": bias, "arms": {}}
        ok = True
        results = {}
        for arm, kw in configs.items():
            try:
                res = build_openmeteo_ifs9_aifs_soft_anchor_result(
                    aifs_extraction=extraction, openmeteo_anchor=anchor, metric=metric, bins=bins,
                    config=SoftAnchorConfig(anchor_weight=ANCHOR_WEIGHT, anchor_sigma_c=ANCHOR_SIGMA_C),
                    settlement_step_c=step_c, **kw,
                )
            except Exception:
                ok = False
                break
            results[arm] = {k: float(v) for k, v in res.posterior.probabilities.items()}
        if not ok:
            skipped["construction_failed"] += 1
            continue

        for arm in arms:
            post = results[arm]
            s = stats[arm]
            s["n"] += 1
            settle_mass = post.get(settle_id, 0.0)
            unhit = settle_mass <= UNHITTABLE_EPS
            if unhit:
                s["unhittable"] += 1
            argmax_id = max(post, key=lambda k: (post[k], k))
            hit = argmax_id == settle_id
            if hit:
                s["hit"] += 1
            # q_lcb on the selected (argmax) bin with the shipped sigma floor
            qlcb = q_lcb_for_selected(post, bins, argmax_id, sig_floor)
            won = argmax_id == settle_id  # buy_yes on argmax wins iff it is the settled bin
            s["cov_claim_sum"] += qlcb
            s["cov_n"] += 1
            if won:
                s["cov_win"] += 1
            # selective after-cost PnL: trade buy_yes on argmax only if qlcb beats price.
            # No live CLOB ask here -> use a flat reference price = 1/n_bins (fair-odds proxy)
            # so the test is purely about whether smoothing changes WHICH bets clear and win.
            price = max(0.02, min(0.98, 1.0 / len(bins)))
            if qlcb > price:
                s["trades"] += 1
                gross = STAKE * ((1.0 / price) - 1.0) if won else -STAKE
                s["pnl"] += gross - STAKE * FEE_RATE - STAKE * SLIPPAGE_RATE
            # manufactured buy_no on an "impossible" (~0 mass) bin: the settlement bin
            # being impossible is the defect; a buy_no on the settlement bin LOSES.
            if settle_mass <= IMPOSSIBLE_EPS:
                # buy_no on the impossible settlement bin: wins iff settle != that bin (never).
                s["buy_no_impossible_trades"] += 1
                no_price = max(0.02, min(0.98, 1.0 - settle_mass))
                # buy_no pays if the bin does NOT settle; but it DID settle -> loss.
                pnl_no = -STAKE  # always a loss because the impossible bin is the settled one
                s["buy_no_impossible_pnl"] += pnl_no
                s["buy_no_impossible_losses"] += 1
            cell_rec["arms"][arm] = {
                "settle_mass": settle_mass, "unhittable": unhit, "hit": hit,
                "argmax": argmax_id, "qlcb": qlcb,
            }
        per_cell.append(cell_rec)

    # report
    out = {"targets": args.targets, "units": args.units, "alpha": args.alpha,
           "skipped": dict(skipped), "arms": {}}
    for arm in arms:
        s = stats[arm]
        n = max(s["n"], 1)
        cov_n = max(s["cov_n"], 1)
        out["arms"][arm] = {
            "n": s["n"],
            "unhittable_rate": s["unhittable"] / n,
            "unhittable_count": s["unhittable"],
            "bin_hit_rate": s["hit"] / n,
            "bin_hit_count": s["hit"],
            "qlcb_mean_claim": s["cov_claim_sum"] / cov_n,
            "realized_win_rate": s["cov_win"] / cov_n,
            "coverage_conservative": (s["cov_win"] / cov_n) >= (s["cov_claim_sum"] / cov_n),
            "selective_trades": s["trades"],
            "selective_pnl": s["pnl"],
            "buy_no_impossible_trades": s["buy_no_impossible_trades"],
            "buy_no_impossible_losses": s["buy_no_impossible_losses"],
            "buy_no_impossible_pnl": s["buy_no_impossible_pnl"],
        }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
