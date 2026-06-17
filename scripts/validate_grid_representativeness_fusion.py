# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: operator "finish v3" (2026-06-17) — settlement-graded GATE for the v3
#   zeus_grid_coordinate_precision_upgrade_v3.md rule 5 deploy flag
#   (edli.replacement_0_1_grid_representativeness_enabled). Proves flag-ON does NOT degrade the
#   pooled fused center and (operator thesis) trims the cold bias by down-weighting coarse/offset
#   native cells via Sigma. Read-only DB (?mode=ro). Reconstructs the EXACT live capture inputs.
"""Settlement-validation replay: fused mu* flag-OFF vs flag-ON (cold-start) vs flag-ON (fitted).

For each VERIFIED settled (city, metric, target_date) lead-1 cell in the HOLDOUT window
(default last 7 days — disjoint from the [-60,-7] fit window of fit_grid_representativeness.py):

  1. Reconstruct the per-model instruments EXACTLY as src/data/bayes_precision_fusion_capture.py
     + src/data/bayes_precision_fusion_history_provider.py would on the live path:
       - CURRENT value  = the persisted single_runs latest-cycle lead-1 forecast_value_c per model
         (the same "current" the live q is built from; measure_fusion harness reconstruction).
       - WALK-FORWARD history (residuals) = endpoint='previous_runs' lead-1 rows JOINed to VERIFIED
         settlement, STRICTLY target_date < this cell's target_date (no leak), residual = fc - settle_C.
       - parent_bias = pooled mean residual over anchor + globals histories; z = raw - eb_bias(...).
       - model selection (decorrelated provider reps, polygon/alias dedup) via select_models.
       - anchor prior: z = raw anchor single_runs value (NO debias artifact present -> no shift,
         matching the current live posture); tau0 = stdev(anchor previous_runs residuals) bridged
         ifs025->ifs9 (the live anchor history product is the 0.25 feed).
       - disagree_var = Var(corrected z over anchor+globals) * DISAGREE_W.
  2. Run fuse_bayes_precision_posterior THREE ways on the SAME instruments:
       (A) OFF       : all sigma_repr_sq = 0, anchor_sigma_repr_sq = 0  (byte-identical baseline)
       (B) ON-cold   : sigma_repr_sq = sigma_repr_sq_for(city,model, fit=COLD_START)
       (C) ON-fitted : sigma_repr_sq = sigma_repr_sq_for(city,model, fit=<state/repr_variance_fit.json>)
  3. Compare fused mu* (native settlement unit) to settlement_value: bias (mean signed error),
     MAE, n; POOLED and PER-CITY; plus improved/worsened cell counts ON vs OFF.

GATE: flag-ON must NOT degrade the pooled center (Delta MAE <= 0 or within noise) AND should
reduce the cold bias. Read-only; writes NO DB and flips NO flag.

Usage: python scripts/validate_grid_representativeness_fusion.py [--holdout 7] [--lead 1]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.forecast.bayes_precision_fusion import (
    ANCHOR_MODEL,
    DISAGREE_W,
    MIN_TRAIN,
    ModelInstrument,
    eb_bias,
    fuse_bayes_precision_posterior,
)
from src.forecast.bayes_precision_fusion_anchor_bridge import (
    anchor_history_requires_bridge,
    bridge_anchor_tau0,
)
from src.data.bayes_precision_fusion_capture import (
    OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME,
)
from src.forecast.grid_representativeness_loader import sigma_repr_sq_for
from src.forecast.model_selection import (
    GLOBAL_LIKELIHOOD_MODELS,
    REGIONAL_MODELS,
    select_models,
)
from src.forecast.representativeness_variance import (
    COLD_START_REPR_VARIANCE,
    ReprVarianceFit,
)

REPO = Path(__file__).resolve().parents[1]
FORECASTS_DB = REPO / "state" / "zeus-forecasts.db"
CITIES = REPO / "config" / "cities.json"
REPR_FIT_PATH = REPO / "state" / "repr_variance_fit.json"

CANDIDATE_MODELS = list(GLOBAL_LIKELIHOOD_MODELS) + list(REGIONAL_MODELS) + ["icon_seamless"]


def c_to_native(v: float, unit: str) -> float:
    return v if unit == "C" else v * 9.0 / 5.0 + 32.0


def settle_to_c(value: float, unit: str | None) -> float:
    if unit == "F":
        return (float(value) - 32.0) * 5.0 / 9.0
    return float(value)


def load_repr_fit() -> ReprVarianceFit | None:
    if not REPR_FIT_PATH.exists():
        return None
    try:
        return ReprVarianceFit(**json.loads(REPR_FIT_PATH.read_text()))
    except Exception:
        return None


def current_values(con, city, metric, target_date, lead):
    """The persisted single_runs latest-cycle lead-1 forecast_value_c per model (the live current).

    Mirrors measure_fusion: MAX(source_cycle_time) per model. single_runs ONLY (the live q is
    built from the persisted single_runs current capture, never previous_runs).
    """
    rows = con.execute(
        """
        SELECT model, forecast_value_c fv FROM raw_model_forecasts r
        WHERE city=? AND metric=? AND target_date=? AND lead_days=? AND endpoint='single_runs'
          AND forecast_value_c IS NOT NULL
          AND source_cycle_time=(
              SELECT MAX(source_cycle_time) FROM raw_model_forecasts r2
              WHERE r2.city=r.city AND r2.metric=r.metric AND r2.target_date=r.target_date
                AND r2.model=r.model AND r2.lead_days=r.lead_days AND r2.endpoint='single_runs')
        """,
        (city, metric, target_date, int(lead)),
    ).fetchall()
    return {r["model"]: float(r["fv"]) for r in rows}


def walk_forward_history(con, city, metric, lead, decision_date, models):
    """Per-model walk-forward residual history (previous_runs JOIN VERIFIED settle, target<decision).

    Returns {model: (residuals_list, residual_by_date_dict, target_dates_list)} — EXACTLY the
    live BayesPrecisionFusionHistoryProvider query (no-leak r.target_date < decision_date).
    """
    if not models:
        return {}
    ph = ",".join("?" for _ in models)
    rows = con.execute(
        f"""
        SELECT r.model model, r.target_date td, r.forecast_value_c fv,
               s.settlement_value sv, s.settlement_unit unit
        FROM raw_model_forecasts r
        JOIN settlement_outcomes s
          ON s.city=r.city AND s.target_date=r.target_date AND s.temperature_metric=r.metric
        WHERE r.city=? AND r.metric=? AND r.lead_days=? AND r.endpoint='previous_runs'
          AND r.model IN ({ph}) AND s.authority='VERIFIED' AND s.settlement_value IS NOT NULL
          AND r.forecast_value_c IS NOT NULL AND r.target_date < ?
        ORDER BY r.model, r.target_date
        """,
        [city, metric, int(lead), *models, decision_date],
    ).fetchall()
    out: dict[str, tuple[list[float], dict[str, float], list[str]]] = {}
    for row in rows:
        try:
            resid = float(row["fv"]) - settle_to_c(row["sv"], row["unit"])
        except (TypeError, ValueError):
            continue
        m = row["model"]
        td = str(row["td"])
        r_list, r_map, d_list = out.setdefault(m, ([], {}, []))
        r_list.append(resid)
        r_map[td] = resid
        d_list.append(td)
    return out


def build_instruments(con, *, city, metric, lat, lon, target_date, lead, repr_fit):
    """Reconstruct (anchor_z, anchor_tau0, instruments, disagree_var) like the live capture.

    Returns None when there is no anchor current value or no surviving extras (the live path
    would fall back to the single-anchor posterior -> not part of the fusion comparison).
    The instruments carry NO sigma_repr (set later per-variant) so OFF/ON share identical inputs.
    """
    cur = current_values(con, city, metric, target_date, lead)
    if ANCHOR_MODEL not in cur:
        return None  # live path requires the persisted anchor current row

    present_values = {m: v for m, v in cur.items() if m in CANDIDATE_MODELS}

    present_for_hist = dict(present_values)
    present_for_hist[ANCHOR_MODEL] = cur[ANCHOR_MODEL]
    hist = walk_forward_history(
        con, city, metric, lead, target_date, list(present_for_hist)
    )

    # parent_bias = pooled mean residual across anchor + globals (live capture).
    pooled: list[float] = []
    for m in (ANCHOR_MODEL,) + tuple(GLOBAL_LIKELIHOOD_MODELS):
        h = hist.get(m)
        if h:
            pooled.extend(h[0])
    parent_bias = (sum(pooled) / len(pooled)) if pooled else 0.0

    # alias series (icon_d2 vs icon_seamless) — forecast value history.
    alias_series: dict[str, list[float]] = {}
    # the history provider stores residuals; for the alias test the live code uses
    # forecast_values. We approximate with the residual series presence only (the conservative
    # dedup path triggers when both present and no series -> dedup). To match the live default
    # (both present, no value series -> conservative dedup), pass None so select_models dedups
    # icon_seamless when icon_d2 present. This matches live behaviour for these cells.
    selection = select_models(
        present_models=present_values, lat=lat, lon=lon, lead_days=lead,
        alias_series=alias_series or None,
    )

    def mk(model, is_regional):
        h = hist.get(model)
        resids = h[0] if h else []
        n = len(resids)
        b_hat = eb_bias(resids, parent_bias) if resids else 0.0
        z = present_values[model] - b_hat
        return ModelInstrument(
            model=model, z=z,
            train_residuals=tuple(resids),
            residuals_by_date=(h[1] if h else {}),
            n_train=n, is_regional=is_regional, sigma_repr_sq=0.0,
        )

    instruments: list[ModelInstrument] = []
    for m in selection.likelihood_globals:
        instruments.append(mk(m, False))
    for m in selection.regional_experts:
        instruments.append(mk(m, True))
    if not instruments:
        return None  # no extras -> live single-anchor fallback

    # anchor prior. Center = raw anchor single_runs value (NO debias artifact present in state/ ->
    # bias_shift_c=None -> anchor_value_corrected_c == raw). tau0 from anchor previous_runs residuals.
    anchor_z = float(cur[ANCHOR_MODEL])
    ah = hist.get(ANCHOR_MODEL)
    anchor_tau0 = None
    if ah and len(ah[0]) >= MIN_TRAIN:
        try:
            raw_tau0 = statistics.stdev(ah[0])
        except statistics.StatisticsError:
            raw_tau0 = None
        if raw_tau0 is not None:
            if anchor_history_requires_bridge(
                stored_model_name=OPENMETEO_PREVIOUS_RUNS_ANCHOR_MODEL_NAME
            ):
                anchor_tau0 = float(bridge_anchor_tau0(float(raw_tau0)))
            else:
                anchor_tau0 = float(raw_tau0)

    # disagree_var = Var(corrected z over anchor+globals) * DISAGREE_W (live capture).
    corr = [ins.z for ins in instruments if not ins.is_regional]
    corr.append(anchor_z)
    if len(corr) >= 2:
        mean = sum(corr) / len(corr)
        disagree_var = (sum((v - mean) ** 2 for v in corr) / len(corr)) * DISAGREE_W
    else:
        disagree_var = 0.0

    return anchor_z, anchor_tau0, instruments, disagree_var, selection


def fuse_variant(anchor_z, anchor_tau0, instruments, disagree_var, *, city, repr_fit):
    """Run the fusion for OFF / ON-cold / ON-fitted and return native mu* for each."""
    def run(get_sigma):
        lik = tuple(
            ModelInstrument(
                model=i.model, z=i.z, train_residuals=i.train_residuals,
                residuals_by_date=i.residuals_by_date, n_train=i.n_train,
                is_regional=i.is_regional, sigma_repr_sq=get_sigma(i.model),
            )
            for i in instruments
        )
        anchor_sig = get_sigma(ANCHOR_MODEL)
        fp = fuse_bayes_precision_posterior(
            anchor_z=anchor_z, anchor_tau0=anchor_tau0, likelihood=lik,
            disagree_var=disagree_var, use_covariance=True, anchor_sigma_repr_sq=anchor_sig,
        )
        return fp.mu

    mu_off = run(lambda m: 0.0)
    mu_cold = run(lambda m: sigma_repr_sq_for(city, m, fit=COLD_START_REPR_VARIANCE))
    if repr_fit is not None:
        mu_fit = run(lambda m: sigma_repr_sq_for(city, m, fit=repr_fit))
    else:
        mu_fit = None
    return mu_off, mu_cold, mu_fit


def _stat(xs):
    if not xs:
        return None, None
    return statistics.mean(xs), statistics.mean(abs(v) for v in xs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", type=int, default=7, help="validate on the last N days (holdout)")
    ap.add_argument("--lead", type=int, default=1)
    args = ap.parse_args()

    cj = {c["name"]: c for c in json.loads(CITIES.read_text())["cities"]}
    repr_fit = load_repr_fit()
    con = sqlite3.connect(f"file:{FORECASTS_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    cells = con.execute(
        """SELECT city, target_date, temperature_metric metric, settlement_value sv,
                  settlement_unit unit
           FROM settlement_outcomes
           WHERE authority='VERIFIED' AND settlement_value IS NOT NULL
             AND target_date >= date('now', ?)
           ORDER BY city, target_date""",
        (f"-{args.holdout} day",),
    ).fetchall()

    err_off: list[float] = []
    err_cold: list[float] = []
    err_fit: list[float] = []
    per_city: dict[str, dict] = {}
    n_skipped = 0
    improved_cold = worsened_cold = improved_fit = worsened_fit = 0

    for r in cells:
        city, td, metric, sv = r["city"], r["target_date"], r["metric"], r["sv"]
        c = cj.get(city)
        if not c:
            n_skipped += 1
            continue
        unit = c.get("unit", "C")
        lat, lon = float(c["lat"]), float(c["lon"])
        built = build_instruments(
            con, city=city, metric=metric, lat=lat, lon=lon,
            target_date=td, lead=args.lead, repr_fit=repr_fit,
        )
        if built is None:
            n_skipped += 1
            continue
        anchor_z, anchor_tau0, instruments, disagree_var, _sel = built
        mu_off, mu_cold, mu_fit = fuse_variant(
            anchor_z, anchor_tau0, instruments, disagree_var, city=city, repr_fit=repr_fit
        )
        e_off = c_to_native(mu_off, unit) - sv
        e_cold = c_to_native(mu_cold, unit) - sv
        err_off.append(e_off)
        err_cold.append(e_cold)
        if abs(e_cold) < abs(e_off) - 1e-9:
            improved_cold += 1
        elif abs(e_cold) > abs(e_off) + 1e-9:
            worsened_cold += 1
        d = per_city.setdefault(city, {"off": [], "cold": [], "fit": []})
        d["off"].append(e_off)
        d["cold"].append(e_cold)
        if mu_fit is not None:
            e_fit = c_to_native(mu_fit, unit) - sv
            err_fit.append(e_fit)
            d["fit"].append(e_fit)
            if abs(e_fit) < abs(e_off) - 1e-9:
                improved_fit += 1
            elif abs(e_fit) > abs(e_off) + 1e-9:
                worsened_fit += 1
    con.close()

    ob, om = _stat(err_off)
    cb, cm = _stat(err_cold)
    fb, fm = _stat(err_fit)
    n = len(err_off)
    print(f"=== Grid-representativeness fusion validation (holdout last {args.holdout}d, lead-{args.lead}) ===")
    print(f"  fused cells n={n}   (skipped {n_skipped}: no anchor current / no extras / unknown city)")
    print(f"  repr fit artifact: {'LOADED ' + REPR_FIT_PATH.name if repr_fit else 'ABSENT (ON-fitted skipped)'}")
    print()
    print(f"  {'variant':16s} {'bias':>9s} {'MAE':>9s} {'dBias':>9s} {'dMAE':>9s}")
    print(f"  {'OFF (baseline)':16s} {ob:+9.4f} {om:9.4f} {'':>9s} {'':>9s}")
    print(f"  {'ON cold-start':16s} {cb:+9.4f} {cm:9.4f} {cb-ob:+9.4f} {cm-om:+9.4f}")
    if fb is not None:
        print(f"  {'ON fitted':16s} {fb:+9.4f} {fm:9.4f} {fb-ob:+9.4f} {fm-om:+9.4f}")
    print()
    print(f"  ON cold-start vs OFF : improved {improved_cold}  worsened {worsened_cold}  (of {n})")
    if fb is not None:
        print(f"  ON fitted    vs OFF : improved {improved_fit}  worsened {worsened_fit}  (of {n})")
    print()

    # per-city movers (by |dMAE| of cold-start, the sharper differential variant)
    rows = []
    for city, d in per_city.items():
        cnt = len(d["off"])
        _, om_c = _stat(d["off"])
        _, cm_c = _stat(d["cold"])
        ob_c, _ = _stat(d["off"])
        cb_c, _ = _stat(d["cold"])
        fm_c = _stat(d["fit"])[1] if d["fit"] else None
        rows.append((city, cnt, ob_c, om_c, cb_c, cm_c, fm_c, (cm_c - om_c)))
    rows.sort(key=lambda x: x[7])  # most-improved (most-negative dMAE) first
    print("  per-city (sorted by cold-start dMAE; negative = ON improves):")
    print(f"    {'city':14s} {'n':>3s} {'biasOFF':>8s} {'MAEoff':>7s} {'biasCLD':>8s} {'MAEcld':>7s} {'MAEfit':>7s} {'dMAE':>7s}")
    for city, cnt, ob_c, om_c, cb_c, cm_c, fm_c, dmae in rows:
        fm_s = f"{fm_c:7.3f}" if fm_c is not None else "    n/a"
        print(f"    {city:14s} {cnt:3d} {ob_c:+8.3f} {om_c:7.3f} {cb_c:+8.3f} {cm_c:7.3f} {fm_s} {dmae:+7.3f}")

    # emit a compact JSON summary for the report + shared memory.
    summary = {
        "n": n, "n_skipped": n_skipped, "holdout_days": args.holdout, "lead": args.lead,
        "off": {"bias": ob, "mae": om},
        "on_cold": {"bias": cb, "mae": cm, "dbias": cb - ob, "dmae": cm - om,
                    "improved": improved_cold, "worsened": worsened_cold},
        "on_fitted": (None if fb is None else
                      {"bias": fb, "mae": fm, "dbias": fb - ob, "dmae": fm - om,
                       "improved": improved_fit, "worsened": worsened_fit}),
    }
    print("\nSUMMARY_JSON " + json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
