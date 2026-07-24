#!/usr/bin/env python3
# Created: 2026-06-02
# Last reused/audited: 2026-06-02
# Authority basis: CI-artifact diagnosis wf_9fd5ca4c. Operator law = the analytic
#   predictive CI is HONEST iff its nominal coverage equals realized coverage
#   AGAINST SETTLEMENT. A tighter CI that UNDER-covers is false confidence and is
#   FORBIDDEN. This script MEASURES coverage; it relaxes NO gate and flags
#   under-coverage loudly.
#
#   READ-ONLY w.r.t. all live DBs and code. Opens state/zeus-forecasts.db and
#   state/zeus-world.db with mode=ro. SELECT-only. Writes nothing to disk; prints
#   a per-city coverage table + aggregate + licence headline to stdout only.
#
#   Live truth: observation_instants.running_max (WU station daily-max), authority
#   != 'UNVERIFIED'. Forecast members: ensemble_snapshots.members_json. Instrument
#   sigma = 0.28 C (config ensemble.instrument_noise; 0.5 F). Members converted to
#   Celsius first, then sigma=0.28 C applied throughout.
"""Validate whether Zeus may replace the MC-resample trade-score CI with an
analytic Gaussian-mixture predictive CI.

THE FINDING BEING LICENSED
--------------------------
The live robust trade score uses q_lcb_5pct built by
src/strategy/market_analysis.py `_bootstrap_p_raw_all` (lines 346-358):

    sample   = rng.choice(member_maxes, size=n_members, replace=True)  # resample
    noised   = sample + rng.normal(0, sigma, n_members)                # instrument noise
    measured = settle(noised)                                          # round to integer
    p_raw[b] = fraction of `measured` in bin b                         # 51-indicator mean

then percentile-5 over 500 draws.

DIAGNOSIS (proven here on data): this CI half-width is dominated by the binomial
RESAMPLE-WITH-REPLACEMENT variance of a 51-member indicator estimator,
~ 1.645*sqrt(p(1-p)/51) -- an ARTIFACT of the MC estimator, INDEPENDENT of
forecast accuracy. The honest predictive CI a ~1C-MAE forecast supports is far
tighter. The analytic point-equivalent already ships:
src/signal/ensemble_signal.py `analytic_p_raw_vector_from_maxes`
( P(round(x)==t | x~N(m_i,sigma)) = Phi((hi-m_i)/sigma) - Phi((lo-m_i)/sigma),
averaged across members ), proven point-equal to high-n MC by
tests/test_analytic_p_raw_equivalence.py.

WHAT THIS SCRIPT DECIDES
------------------------
For each trustworthy city, on short-lead (lead_hours <= 24) forecast snapshots
joined to settled live truth, it measures:

  (1) Whether the analytic predictive distribution is COVERAGE-HONEST against
      settlement (PIT-uniform, central-90% empirical coverage ~ 0.90).
  (2) How over-conservative the incumbent MC-resample band is, by faithfully
      replicating `_bootstrap_p_raw_all` and comparing its 5-95 bin-probability
      band half-width to the analytic point's (near-zero) estimator noise.

A city is LICENSED only if the analytic CI is coverage-honest. If most
trustworthy cities UNDER-cover, the bare raw-ensemble analytic CI is too tight
(false confidence) and the honest fix must COUPLE EMOS sigma widening -- it must
NOT ship the bare analytic CI. This script says so plainly.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import defaultdict
from datetime import date

import numpy as np
from scipy.stats import norm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORECASTS = os.path.join(REPO, "state", "zeus-forecasts.db")
WORLD = os.path.join(REPO, "state", "zeus-world.db")
EMOS_JSON = os.path.join(REPO, "state", "emos_calibration.json")

# --- constants pinned by the diagnosis / config ---------------------------
INSTRUMENT_SIGMA_C = 0.28          # config ensemble.instrument_noise (0.5 F == 0.28 C)
N_MC_DRAWS = 500                   # incumbent _bootstrap_p_raw_all draw count
MC_SEED = 12345                    # deterministic seed for the observation replay
MAX_DATES_PER_CITY = 60            # "most recent ~60 settled target_dates"
MIN_N_FOR_VERDICT = 20             # below this -> INSUFFICIENT_N
LEAD_HOURS_MAX = 24.0              # short-lead definition

# central-90% predictive interval honesty band
PI_LOW, PI_HIGH = 0.05, 0.95
COV90_LOW, COV90_HIGH = 0.86, 0.94   # honest band around nominal 0.90
PIT_MEAN_TOL = 0.05                  # |PIT_mean - 0.5| tolerance

TRUSTWORTHY_16 = [
    "Atlanta", "Austin", "Chicago", "Dallas", "Helsinki", "London",
    "Madrid", "Mexico City", "Miami", "Milan", "Moscow", "NYC",
    "Sao Paulo", "Seattle", "Warsaw", "Paris",
]


# ---------------------------------------------------------------------------
# unit conversion
# ---------------------------------------------------------------------------
def to_celsius(value: float, unit: str | None) -> float:
    """Convert a temperature to Celsius given a unit string.

    members_unit values seen: 'degC', 'degF'. temp_unit values: 'C', 'F'.
    Rule (from spec): unit starting 'degf' or == 'F' -> (v-32)*5/9 ;
    'K' -> v-273.15 ; else passthrough (already Celsius).
    """
    if unit is None:
        return value
    u = unit.strip().lower()
    if u.startswith("degf") or u == "f":
        return (value - 32.0) * 5.0 / 9.0
    if u == "k" or u.startswith("kelvin"):
        return value - 273.15
    # 'degc', 'c', or unknown -> assume Celsius
    return value


def members_to_celsius(members_json: str, members_unit: str | None) -> np.ndarray | None:
    try:
        arr = np.asarray(json.loads(members_json), dtype=float)
    except Exception:
        return None
    if arr.ndim != 1 or arr.size == 0 or not np.isfinite(arr).all():
        return None
    if members_unit and members_unit.strip().lower().startswith("degf"):
        arr = (arr - 32.0) * 5.0 / 9.0
    elif members_unit and members_unit.strip().lower() in ("f",):
        arr = (arr - 32.0) * 5.0 / 9.0
    elif members_unit and members_unit.strip().lower() in ("k", "kelvin"):
        arr = arr - 273.15
    return arr


# ---------------------------------------------------------------------------
# settlement rounding -- wmo_half_up (floor(x + 0.5)), matching the live
# `_settle` used in _bootstrap_p_raw_all and the analytic equivalence.
# Applied in CELSIUS (members are converted to C first; sigma is 0.28 C).
# ---------------------------------------------------------------------------
def round_half_up(x: np.ndarray) -> np.ndarray:
    """floor(x + 0.5) -- the WMO settlement rounding rule in Celsius integers."""
    return np.floor(np.asarray(x, dtype=float) + 0.5)


# ---------------------------------------------------------------------------
# (1) ANALYTIC PREDICTIVE distribution -- the candidate honest model.
#
# predictive distribution for daily-max = (1/n) * Sum_i N(m_i, eff_sigma^2),
# m_i = member maxes in C. Two variants reported:
#   - 'instr'   : eff_sigma = instrument sigma only (0.28 C)   [DEFAULT candidate]
#   - 'instr+loc': eff_sigma^2 = instrument^2 + (ens_sd/sqrt(n))^2, adding the
#                  mean-location sampling term so the operator sees both.
# ---------------------------------------------------------------------------
def mixture_cdf(y: float, member_maxes_c: np.ndarray, eff_sigma: float) -> float:
    """F_pred(y) for the equal-weight Gaussian mixture (1/n) Sum N(m_i, eff_sigma^2)."""
    z = (y - member_maxes_c) / eff_sigma
    return float(norm.cdf(z).mean())


def eff_sigma_instr() -> float:
    return INSTRUMENT_SIGMA_C


def eff_sigma_instr_loc(member_maxes_c: np.ndarray) -> float:
    n = len(member_maxes_c)
    ens_sd = float(np.std(member_maxes_c, ddof=1)) if n > 1 else 0.0
    loc_term = ens_sd / math.sqrt(n) if n > 0 else 0.0
    return float(math.hypot(INSTRUMENT_SIGMA_C, loc_term))


# ---------------------------------------------------------------------------
# (2a) MC-RESAMPLE band -- faithfully replicate `_bootstrap_p_raw_all`.
# resample 51 w/ replacement + N(0, 0.28) instrument noise + round-half-up
# settle (Celsius) + bin indicator, N_MC_DRAWS draws, on the realized
# settlement BIN (the 1-degree integer bin containing round_half_up(y_obs)).
# Returns the 5-95 percentile band half-width on that bin's probability.
# ---------------------------------------------------------------------------
def mc_resample_bin_band_hw(
    member_maxes_c: np.ndarray, target_int: int, rng: np.random.Generator
) -> float:
    n = len(member_maxes_c)
    draws = np.empty(N_MC_DRAWS, dtype=float)
    for d in range(N_MC_DRAWS):
        sample = rng.choice(member_maxes_c, size=n, replace=True)
        noised = sample + rng.normal(0.0, INSTRUMENT_SIGMA_C, n)
        measured = round_half_up(noised)
        draws[d] = float(np.mean(measured == target_int))
    lo = float(np.percentile(draws, 5))
    hi = float(np.percentile(draws, 95))
    return (hi - lo) / 2.0


# ---------------------------------------------------------------------------
# (2b) ANALYTIC bin-probability parameter-uncertainty half-width.
# The closed-form bin probability for integer t (round-half-up, in C) is:
#   p_bin = (1/n) Sum_i [ Phi((t+0.5 - m_i)/sigma) - Phi((t-0.5 - m_i)/sigma) ]
# The pure-analytic POINT has ~0 estimator noise (no resampling). To give the
# MC band a fair counterpart we propagate the member-MEAN sampling error
# (SE of the mixture location = ens_sd/sqrt(n)) through the closed-form bin CDF
# via a first-order (delta-method) derivative, yielding a 90% half-width that
# reflects *forecast* location uncertainty rather than estimator noise.
#
#   dp_bin/d(mu_shift): shifting all members by ds changes
#     p_bin(ds) = (1/n) Sum_i [ Phi((t+0.5 - m_i - ds)/sigma) - Phi((t-0.5 - m_i - ds)/sigma) ]
#   d/d(ds) at ds=0 = (1/n) Sum_i (-1/sigma)[ phi((t+0.5-m_i)/sigma) - phi((t-0.5-m_i)/sigma) ]
#   hw_90 = 1.645 * |dp/dmu| * (ens_sd / sqrt(n))
# ---------------------------------------------------------------------------
def analytic_bin_prob(member_maxes_c: np.ndarray, target_int: int, sigma: float) -> float:
    hi = (target_int + 0.5 - member_maxes_c) / sigma
    lo = (target_int - 0.5 - member_maxes_c) / sigma
    return float((norm.cdf(hi) - norm.cdf(lo)).mean())


def analytic_bin_band_hw(member_maxes_c: np.ndarray, target_int: int, sigma: float) -> float:
    n = len(member_maxes_c)
    hi = (target_int + 0.5 - member_maxes_c) / sigma
    lo = (target_int - 0.5 - member_maxes_c) / sigma
    dp_dmu = float((-(norm.pdf(hi) - norm.pdf(lo)) / sigma).mean())
    ens_sd = float(np.std(member_maxes_c, ddof=1)) if n > 1 else 0.0
    se_mean = ens_sd / math.sqrt(n) if n > 0 else 0.0
    return 1.645 * abs(dp_dmu) * se_mean


# ---------------------------------------------------------------------------
# EMOS / NGR calibration  --  third predictive variant
#
# model:  mu    = a + b * xbar
#         sigma = sqrt( exp( c + d * log(S2) + e * lead_days ) )
#
# S2 = var(members, ddof=1) + 1e-4   (guard against degenerate spread)
# lead_days = lead_hours / 24
#
# EMOS gives the CALIBRATED single-Gaussian predictive directly.
# Do NOT re-mix members; the EMOS sigma already encodes total predictive
# spread (raw ensemble spread-error relationship + lead decay).
# ---------------------------------------------------------------------------
def _season(month: int) -> str:
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def load_emos_cells() -> dict:
    """Return cells dict from emos_calibration.json. Empty dict if file missing."""
    if not os.path.exists(EMOS_JSON):
        return {}
    try:
        return json.load(open(EMOS_JSON)).get("cells", {})
    except Exception:
        return {}


def emos_predictive(
    city: str,
    target_date_str: str,
    member_maxes_c: np.ndarray,
    lead_hours: float,
    cells: dict,
) -> tuple[float | None, float | None, str]:
    """Return (mu_emos, sigma_emos, served_tag) for a single snapshot.

    served_tag: 'emos' if the cell exists and served=='emos',
                'raw'  if cell missing or served=='raw'.
    Falls back to raw-mixture when served=='raw' or cell missing.
    mu/sigma are None for the raw fallback (caller handles).
    """
    month = int(target_date_str[5:7])
    season = _season(month)
    key = f"{city}|{season}|high"  # 3-key (metric-keyed table; this validator is HIGH-path)
    cell = cells.get(key)
    if cell is None or cell.get("served") != "emos":
        return None, None, "raw"
    a, b, c, d, e = cell["params"]
    xbar = float(np.mean(member_maxes_c))
    s2 = float(np.var(member_maxes_c, ddof=1)) + 1e-4
    log_s2 = math.log(s2)
    lead_days = lead_hours / 24.0
    mu = a + b * xbar
    sigma = math.sqrt(math.exp(c + d * log_s2 + e * lead_days))
    return mu, sigma, "emos"


def emos_cdf(y: float, mu: float, sigma: float) -> float:
    """CDF of N(mu, sigma^2) at y (EMOS single-Gaussian predictive)."""
    return float(norm.cdf((y - mu) / sigma))


def emos_bin_band_hw(mu: float, sigma: float, target_int: int) -> float:
    """90%-PI half-width for the EMOS bin probability via delta-method.

    p_bin = Phi((t+0.5-mu)/sigma) - Phi((t-0.5-mu)/sigma)
    We propagate sigma uncertainty through the CDF — same formula as
    analytic_bin_band_hw but with the EMOS single-Gaussian (no member loop).
    The analytic point estimate has no resampling noise, so we report
    the sigma value itself as the 'hw' of the predictive interval:
      hw = 1.645 * sigma  (the 5-95 half-width of the predictive distribution)
    which is what the EMOS CI would yield on the raw temperature scale,
    then convert to bin-probability domain via dp/dmu for comparability.
    """
    hi = (target_int + 0.5 - mu) / sigma
    lo = (target_int - 0.5 - mu) / sigma
    dp_dmu = -(norm.pdf(hi) - norm.pdf(lo)) / sigma
    # For EMOS, mean-location SE is sigma itself (single predictive, not an ensemble).
    # The meaningful CI half-width is the 90% predictive interval half-width on
    # temperature: hw_temp = 1.645 * sigma. Convert to bin probability scale:
    return 1.645 * abs(float(dp_dmu)) * sigma


# ---------------------------------------------------------------------------
# data loading — extended to also carry lead_hours per snapshot for EMOS
# ---------------------------------------------------------------------------
def load_truth(cities: list[str]) -> dict:
    """(city, target_date) -> realized daily-max in Celsius, authority-verified only.

    MAX(running_max) over the day is the realized daily-max. Rows with
    authority == 'UNVERIFIED' are excluded (operator data-provenance law).
    """
    out: dict = {}
    if not os.path.exists(WORLD):
        print(f"[ERROR] zeus-world.db not found at {WORLD}")
        return out
    conn = sqlite3.connect(f"file:{WORLD}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    ph = ",".join("?" * len(cities))
    rows = conn.execute(
        f"""
        SELECT city, target_date, MAX(running_max) AS dmax, temp_unit
        FROM observation_instants
        WHERE city IN ({ph})
          AND running_max IS NOT NULL
          AND COALESCE(authority,'UNVERIFIED') != 'UNVERIFIED'
        GROUP BY city, target_date, temp_unit
        """,
        cities,
    ).fetchall()
    conn.close()
    # collapse possible per-unit duplicates: prefer the max realized value in C
    for r in rows:
        if r["dmax"] is None:
            continue
        val_c = to_celsius(float(r["dmax"]), r["temp_unit"])
        key = (r["city"], r["target_date"])
        if key not in out or val_c > out[key]:
            out[key] = val_c
    return out


def load_short_lead_snapshots(cities: list[str], truth: dict) -> dict:
    """(city) -> list of (target_date, member_maxes_celsius) for short-lead HIGH
    snapshots that have settled verified truth.

    Per (city, target_date) we elect ONE snapshot: the largest lead_hours <= 24
    (the most representative ~24h-out forecast view), breaking ties by latest
    issue_time. Only target_dates present in `truth` are kept.
    """
    out: dict = defaultdict(list)
    if not os.path.exists(FORECASTS):
        print(f"[ERROR] zeus-forecasts.db not found at {FORECASTS}")
        return out
    conn = sqlite3.connect(f"file:{FORECASTS}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    ph = ",".join("?" * len(cities))
    # window the SQL to settled dates only: target_date strictly before today.
    today = str(date.today())
    rows = conn.execute(
        f"""
        SELECT city, target_date, lead_hours, issue_time, members_json, members_unit
        FROM ensemble_snapshots
        WHERE city IN ({ph})
          AND temperature_metric = 'high'
          AND lead_hours <= ?
          AND target_date < ?
        ORDER BY city, target_date, lead_hours DESC, issue_time DESC
        """,
        [*cities, LEAD_HOURS_MAX, today],
    ).fetchall()
    conn.close()

    elected: dict = {}  # (city, date) -> row (first seen = best by ORDER BY)
    for r in rows:
        key = (r["city"], r["target_date"])
        if key in elected:
            continue
        if key not in truth:
            continue
        elected[key] = r

    for (city, tdate), r in elected.items():
        mm = members_to_celsius(r["members_json"], r["members_unit"])
        if mm is None or len(mm) < 2:
            continue
        out[city].append((tdate, mm, float(r["lead_hours"])))

    # keep most-recent MAX_DATES_PER_CITY settled dates per city
    for city in list(out.keys()):
        recs = sorted(out[city], key=lambda t: t[0])  # ascending date
        out[city] = recs[-MAX_DATES_PER_CITY:]
    return out


# ---------------------------------------------------------------------------
# per-city evaluation
# ---------------------------------------------------------------------------
def evaluate_city(
    city: str, recs: list, truth: dict, rng: np.random.Generator, emos_cells: dict
) -> dict:
    pit_instr, pit_loc, pit_emos = [], [], []
    mc_hws, an_hws, emos_hws = [], [], []
    emos_sigmas, resid_sds_raw = [], []
    emos_served_count, emos_raw_count = 0, 0

    for rec in recs:
        tdate, mm, lead_h = rec[0], rec[1], rec[2]
        y_obs = truth[(city, tdate)]

        # (1) PIT under each analytic predictive variant (instrument-only / instr+loc)
        s_instr = eff_sigma_instr()
        s_loc = eff_sigma_instr_loc(mm)
        pit_instr.append(mixture_cdf(y_obs, mm, s_instr))
        pit_loc.append(mixture_cdf(y_obs, mm, s_loc))

        # (2) MC vs analytic bin-probability half-width on the realized bin
        target_int = int(round_half_up(np.array([y_obs]))[0])
        mc_hws.append(mc_resample_bin_band_hw(mm, target_int, rng))
        an_hws.append(analytic_bin_band_hw(mm, target_int, s_instr))

        # (3) EMOS variant
        mu_e, sig_e, served = emos_predictive(city, tdate, mm, lead_h, emos_cells)
        if served == "emos" and mu_e is not None and sig_e is not None:
            pit_emos.append(emos_cdf(y_obs, mu_e, sig_e))
            emos_hws.append(emos_bin_band_hw(mu_e, sig_e, target_int))
            emos_sigmas.append(sig_e)
            resid_sds_raw.append(abs(y_obs - mu_e))
            emos_served_count += 1
        else:
            # fallback: use raw mixture CDF for this date's PIT
            pit_emos.append(mixture_cdf(y_obs, mm, s_instr))
            emos_hws.append(analytic_bin_band_hw(mm, target_int, s_instr))
            emos_raw_count += 1

    pit_instr = np.asarray(pit_instr)
    pit_loc = np.asarray(pit_loc)
    pit_emos = np.asarray(pit_emos)
    n = len(pit_instr)

    def _summ(pit: np.ndarray) -> dict:
        if len(pit) == 0:
            return dict(mean=float("nan"), std=float("nan"), cov90=float("nan"), ks_p=float("nan"))
        cov90 = float(np.mean((pit >= PI_LOW) & (pit <= PI_HIGH)))
        ks_p = float("nan")
        try:
            from scipy.stats import kstest
            ks_p = float(kstest(pit, "uniform").pvalue)
        except Exception:
            pass
        return dict(mean=float(np.mean(pit)), std=float(np.std(pit)), cov90=cov90, ks_p=ks_p)

    summ_instr = _summ(pit_instr)
    summ_loc = _summ(pit_loc)
    summ_emos = _summ(pit_emos)

    med_mc = float(np.median(mc_hws)) if mc_hws else float("nan")
    med_an = float(np.median(an_hws)) if an_hws else float("nan")
    med_emos_hw = float(np.median(emos_hws)) if emos_hws else float("nan")
    ratio = (med_mc / med_an) if (med_an and med_an > 0) else float("inf")

    med_emos_sigma = float(np.median(emos_sigmas)) if emos_sigmas else float("nan")
    # realized residual SD = std of (y_obs - ensemble_mean) for all recs
    resid_list = [truth[(city, rec[0])] - float(np.mean(rec[1])) for rec in recs]
    realized_resid_sd = float(np.std(resid_list)) if resid_list else float("nan")

    # verdict on DEFAULT candidate (instrument-only)
    cov90 = summ_instr["cov90"]
    pit_mean = summ_instr["mean"]
    if n < MIN_N_FOR_VERDICT:
        verdict = "INSUFFICIENT_N"
    elif cov90 < COV90_LOW:
        verdict = "UNDER_COVERED"
    elif cov90 > COV90_HIGH:
        verdict = "OVER_DISPERSED"
    elif abs(pit_mean - 0.5) <= PIT_MEAN_TOL:
        verdict = "ANALYTIC_CI_HONEST"
    else:
        verdict = "OVER_DISPERSED" if cov90 > 0.90 else "UNDER_COVERED"

    # EMOS verdict
    emos_cov90 = summ_emos["cov90"]
    emos_pit_mean = summ_emos["mean"]
    if n < MIN_N_FOR_VERDICT:
        emos_verdict = "INSUFFICIENT_N"
    elif emos_cov90 < COV90_LOW:
        emos_verdict = "UNDER_COVERED"
    elif emos_cov90 > COV90_HIGH:
        emos_verdict = "OVER_DISPERSED"
    elif abs(emos_pit_mean - 0.5) <= PIT_MEAN_TOL:
        emos_verdict = "EMOS_CI_HONEST"
    else:
        emos_verdict = "OVER_DISPERSED" if emos_cov90 > 0.90 else "UNDER_COVERED"

    # served tag: if ALL recs are emos -> 'emos'; if any fallback -> 'partial' or 'raw'
    if emos_served_count == n:
        served_tag = "emos"
    elif emos_served_count == 0:
        served_tag = "raw"
    else:
        served_tag = f"emos({emos_served_count}/{n})"

    return dict(
        city=city, n=n,
        pit_mean=pit_mean, pit_std=summ_instr["std"], cov90=cov90, ks_p=summ_instr["ks_p"],
        loc_pit_mean=summ_loc["mean"], loc_pit_std=summ_loc["std"], loc_cov90=summ_loc["cov90"],
        mc_hw=med_mc, an_hw=med_an, ratio=ratio, verdict=verdict,
        # EMOS columns
        emos_pit_mean=emos_pit_mean, emos_pit_std=summ_emos["std"],
        emos_cov90=emos_cov90, emos_ks_p=summ_emos["ks_p"],
        emos_hw=med_emos_hw, emos_verdict=emos_verdict,
        emos_sigma_median=med_emos_sigma, realized_resid_sd=realized_resid_sd,
        served=served_tag,
    )


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    print("=" * 96)
    print("ANALYTIC CI COVERAGE VALIDATION  —  coverage-against-settlement (operator law)")
    print("=" * 96)
    print(f"  instrument sigma     : {INSTRUMENT_SIGMA_C} C  (applied in Celsius; members converted first)")
    print(f"  settlement rounding  : wmo_half_up  floor(x+0.5)  (matches live _bootstrap_p_raw_all)")
    print(f"  short-lead window    : lead_hours <= {LEAD_HOURS_MAX:.0f}")
    print(f"  dates per city       : most-recent {MAX_DATES_PER_CITY} settled")
    print(f"  MC draws (incumbent) : {N_MC_DRAWS}")
    print(f"  honest cov90 band    : [{COV90_LOW:.2f}, {COV90_HIGH:.2f}]  |PIT_mean-0.5| <= {PIT_MEAN_TOL}")
    print(f"  candidate eff_sigma  : INSTRUMENT-ONLY (default); 'loc' variant adds ens_sd/sqrt(n)")
    print()

    truth = load_truth(TRUSTWORTHY_16)
    print(f"  loaded {len(truth)} verified (city,date) settlement truths")
    snaps = load_short_lead_snapshots(TRUSTWORTHY_16, truth)
    total_snaps = sum(len(v) for v in snaps.values())
    print(f"  elected {total_snaps} short-lead high snapshots across {len(snaps)} cities")
    print()

    emos_cells = load_emos_cells()
    print(f"  loaded {len(emos_cells)} EMOS cells from emos_calibration.json")
    print()

    rng = np.random.default_rng(MC_SEED)
    results = []
    for city in TRUSTWORTHY_16:
        recs = snaps.get(city, [])
        results.append(evaluate_city(city, recs, truth, rng, emos_cells))

    # ---- per-city table ----
    hdr = (f"{'city':12s} {'n':>4s} {'PIT_mean':>9s} {'PIT_std':>8s} "
           f"{'cov90':>6s} {'KS_p':>6s} {'MC_hw':>7s} {'an_hw':>7s} {'ratio':>7s}  verdict")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        ratio_s = f"{r['ratio']:.1f}x" if math.isfinite(r["ratio"]) else "inf"
        ks_s = f"{r['ks_p']:.3f}" if math.isfinite(r["ks_p"]) else "  -  "
        print(f"{r['city']:12s} {r['n']:>4d} {r['pit_mean']:>9.3f} {r['pit_std']:>8.3f} "
              f"{r['cov90']:>6.3f} {ks_s:>6s} {r['mc_hw']:>7.4f} {r['an_hw']:>7.4f} "
              f"{ratio_s:>7s}  {r['verdict']}")

    # ---- 'loc' variant secondary table (operator-visibility) ----
    print()
    print("  [instr+loc variant — eff_sigma^2 = instrument^2 + (ens_sd/sqrt(n))^2]")
    print(f"  {'city':12s} {'PIT_mean':>9s} {'PIT_std':>8s} {'cov90':>6s}")
    for r in results:
        if r["n"] == 0:
            continue
        print(f"  {r['city']:12s} {r['loc_pit_mean']:>9.3f} {r['loc_pit_std']:>8.3f} {r['loc_cov90']:>6.3f}")

    # ---- aggregate ----
    scored = [r for r in results if r["n"] > 0]
    valid = [r for r in results if r["n"] >= MIN_N_FOR_VERDICT]
    print()
    if scored:
        all_n = sum(r["n"] for r in scored)
        agg_pit_mean = float(np.average([r["pit_mean"] for r in scored], weights=[r["n"] for r in scored]))
        agg_cov90 = float(np.average([r["cov90"] for r in scored], weights=[r["n"] for r in scored]))
        med_mc = float(np.median([r["mc_hw"] for r in scored if math.isfinite(r["mc_hw"])]))
        med_an = float(np.median([r["an_hw"] for r in scored if math.isfinite(r["an_hw"])]))
        agg_ratio = (med_mc / med_an) if med_an > 0 else float("inf")
        print(f"AGGREGATE (n-weighted): total_n={all_n}  PIT_mean={agg_pit_mean:.3f}  "
              f"cov90={agg_cov90:.3f}  median_MC_hw={med_mc:.4f}  median_an_hw={med_an:.4f}  "
              f"ratio={agg_ratio:.1f}x")
    else:
        agg_ratio = float("nan")
        print("AGGREGATE: no scored cities")

    licensed = [r["city"] for r in valid if r["verdict"] == "ANALYTIC_CI_HONEST"]
    under = [r["city"] for r in valid if r["verdict"] == "UNDER_COVERED"]
    over = [r["city"] for r in valid if r["verdict"] == "OVER_DISPERSED"]
    insuff = [r["city"] for r in results if r["verdict"] == "INSUFFICIENT_N"]

    print()
    print(f"  LICENSED (coverage-honest)  : {len(licensed)}/16  {licensed}")
    print(f"  UNDER_COVERED (too tight)   : {len(under)}/16  {under}")
    print(f"  OVER_DISPERSED (too wide)   : {len(over)}/16  {over}")
    if insuff:
        print(f"  INSUFFICIENT_N              : {insuff}")

    # ---- headline variant 1 ----
    ratio_s = f"{agg_ratio:.1f}x" if math.isfinite(agg_ratio) else "n/a"
    print()
    print("=" * 96)
    print(f"HEADLINE: ANALYTIC CI LICENSED for {len(licensed)}/16 trustworthy cities "
          f"(coverage-honest); MC band over-covers, median hw ratio {ratio_s}")
    if under:
        print(f"WARNING: {len(under)} trustworthy cities UNDER-COVER with the bare analytic CI "
              f"(false confidence). The honest fix must COUPLE EMOS sigma widening "
              f"(route to task #110), NOT ship the bare raw-ensemble analytic CI.")
    print("=" * 96)

    # =========================================================================
    # EMOS VARIANT TABLE
    # =========================================================================
    print()
    print("=" * 96)
    print("EMOS VARIANT — COVERAGE VALIDATION  (EMOS sigma replaces instrument-only sigma)")
    print("=" * 96)
    print("  mu = a + b*xbar    sigma = sqrt(exp(c + d*log(S2) + e*lead_days))")
    print("  served==raw cities use raw mixture CDF (fallback); tagged in 'served' column")
    print(f"  honest cov90 band : [{COV90_LOW:.2f}, {COV90_HIGH:.2f}]  |PIT_mean-0.5| <= {PIT_MEAN_TOL}")
    print()
    emos_hdr = (f"{'city':12s} {'n':>4s} {'PIT_mean':>9s} {'PIT_std':>8s} "
                f"{'cov90':>6s} {'KS_p':>6s} {'emos_hw':>8s} "
                f"{'emos_sig':>9s} {'resid_sd':>9s}  {'served':>12s}  verdict")
    print(emos_hdr)
    print("-" * len(emos_hdr))
    for r in results:
        ks_s = f"{r['emos_ks_p']:.3f}" if math.isfinite(r["emos_ks_p"]) else "  -  "
        es = r["emos_sigma_median"]
        rs = r["realized_resid_sd"]
        eh = r["emos_hw"]
        es_s = f"{es:.3f}" if math.isfinite(es) else "  -  "
        rs_s = f"{rs:.3f}" if math.isfinite(rs) else "  -  "
        eh_s = f"{eh:.4f}" if math.isfinite(eh) else "  -  "
        print(f"{r['city']:12s} {r['n']:>4d} {r['emos_pit_mean']:>9.3f} {r['emos_pit_std']:>8.3f} "
              f"{r['emos_cov90']:>6.3f} {ks_s:>6s} {eh_s:>8s} "
              f"{es_s:>9s} {rs_s:>9s}  {r['served']:>12s}  {r['emos_verdict']}")

    # ---- EMOS aggregate ----
    emos_valid = [r for r in results if r["n"] >= MIN_N_FOR_VERDICT]
    emos_scored = [r for r in results if r["n"] > 0]
    if emos_scored:
        all_n = sum(r["n"] for r in emos_scored)
        agg_emos_pit = float(np.average([r["emos_pit_mean"] for r in emos_scored], weights=[r["n"] for r in emos_scored]))
        agg_emos_cov = float(np.average([r["emos_cov90"] for r in emos_scored], weights=[r["n"] for r in emos_scored]))
        med_emos_hw_agg = float(np.median([r["emos_hw"] for r in emos_scored if math.isfinite(r["emos_hw"])]))
        med_sig = float(np.median([r["emos_sigma_median"] for r in emos_scored if math.isfinite(r["emos_sigma_median"])]))
        med_resid = float(np.median([r["realized_resid_sd"] for r in emos_scored if math.isfinite(r["realized_resid_sd"])]))
        print()
        print(f"EMOS AGGREGATE (n-weighted): total_n={all_n}  PIT_mean={agg_emos_pit:.3f}  "
              f"cov90={agg_emos_cov:.3f}  median_emos_hw={med_emos_hw_agg:.4f}  "
              f"median_emos_sigma={med_sig:.3f}  median_realized_resid_sd={med_resid:.3f}")

    emos_licensed = [r["city"] for r in emos_valid if r["emos_verdict"] == "EMOS_CI_HONEST"]
    emos_under = [r["city"] for r in emos_valid if r["emos_verdict"] == "UNDER_COVERED"]
    emos_over = [r["city"] for r in emos_valid if r["emos_verdict"] == "OVER_DISPERSED"]
    emos_insuff = [r["city"] for r in results if r["emos_verdict"] == "INSUFFICIENT_N"]

    print()
    print(f"  EMOS LICENSED (coverage-honest)  : {len(emos_licensed)}/16  {emos_licensed}")
    print(f"  EMOS UNDER_COVERED (too tight)   : {len(emos_under)}/16  {emos_under}")
    print(f"  EMOS OVER_DISPERSED (too wide)   : {len(emos_over)}/16  {emos_over}")
    if emos_insuff:
        print(f"  EMOS INSUFFICIENT_N              : {emos_insuff}")

    # served summary
    emos_cities = [r["city"] for r in results if r["served"].startswith("emos") and not r["served"].startswith("raw")]
    raw_cities = [r["city"] for r in results if r["served"] == "raw"]
    partial_cities = [r["city"] for r in results if r["served"].startswith("emos(")]
    print()
    print(f"  served=emos (current season) : {len(emos_cities)}  {emos_cities}")
    print(f"  served=raw  (current season) : {len(raw_cities)}   {raw_cities}")
    if partial_cities:
        print(f"  partial                      : {partial_cities}")

    print()
    print("=" * 96)
    print(f"HEADLINE: EMOS-sigma CI LICENSED (cov90 in [{COV90_LOW},{COV90_HIGH}], "
          f"|PIT_mean-0.5|<={PIT_MEAN_TOL}, n>={MIN_N_FOR_VERDICT}) for {len(emos_licensed)}/16 trustworthy cities.")
    if emos_under:
        print(f"WARNING: {len(emos_under)} cities UNDER-COVER even with EMOS sigma. "
              f"EMOS calibration may need refit or these cities have structural forecast bias.")
    if emos_over:
        print(f"INFO: {len(emos_over)} cities OVER-DISPERSED with EMOS sigma "
              f"(conservative/safe; may over-size confidence intervals).")
    print("=" * 96)


if __name__ == "__main__":
    main()
