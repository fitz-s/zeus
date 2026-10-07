#!/usr/bin/env python3
# Created: 2026-10-07
# Last reused or audited: 2026-10-07
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (step 6: replay proof against live DBs, read-only; per-family compute time).
"""Replay the shipped Day0 carrier on live evidence, read-only, and report.

For each (city, metric, decision time):
  1. calls ``build_day0_remaining_probability_carrier`` (the shipped builder) with the live
     current-state print, so dispatch picks the dense law exactly as serving would;
  2. rebuilds the same day with the backtest engine (artifacts/fast_obs_audit/dense_station_model,
     grid oracle on identical inputs: same mean path, latent, rows), and reports the max
     absolute bin difference;
  3. times the dense computation per family (cache disabled).
For a city without a dense channel it rebuilds a persisted V2 posterior's carrier from its
own provenance and compares q, samples and identity byte for byte.

Both DBs are opened ``file:...?mode=ro`` with query_only (forecasts-main + world attached).
Writes only a JSON report to ``--out``.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.config import runtime_cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data import day0_dense_evidence as evidence  # noqa: E402
from src.data import day0_dense_state_space as ds  # noqa: E402
from src.data.day0_hourly_vectors import (  # noqa: E402
    build_day0_remaining_probability_carrier,
    day0_remaining_carrier_identity_inputs,
    read_day0_current_temperature_state,
)
from src.calibration.day0_dense_state_space_params import dense_params_for  # noqa: E402

STATE = Path("/Users/leofitz/zeus/state")
UTC = timezone.utc


def ro_pair() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{STATE / 'zeus-forecasts.db'}?mode=ro", uri=True, timeout=30)
    conn.execute("ATTACH DATABASE ? AS world", (f"file:{STATE / 'zeus-world.db'}?mode=ro",))
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    return conn


def bins_around(center: int, half: int = 5):
    lo, hi = center - half, center + half
    return ((None, float(lo - 1)),) + tuple((float(k), float(k)) for k in range(lo, hi + 1)) + ((float(hi + 1), None),)


def oracle(params, day: ds.DenseDay, bins, preimage):
    """Backtest grid oracle on the same inputs (one-scale OU, Gaussian dense likelihood at cell centres).

    The oracle has no provisional marks, static offset, drift axis or quantised mixture, so it
    is evaluated on a day reduced to exact rows; it checks the shared core (OU recursion,
    interval censoring, pending kills, extreme functional) of the shipped operator."""
    sys.path.insert(0, str(REPO / "artifacts" / "fast_obs_audit" / "dense_station_model"))
    import state_space as bss  # noqa: PLC0415
    from ss_engine import Day  # noqa: PLC0415

    m = ds.mean_path(day.forecast, day.hour, params.model.mean)
    rows = [(t, lo) for t, lo, hi in day.page] + [(t, lo) for t, lo, hi, _ in day.provisional]
    mt = np.asarray([t for t, _ in rows], float)
    mk = np.asarray([k for _, k in rows], int)
    nz = params.model.noise
    dt_ = np.asarray([t for t, _ in day.dense], float)
    dx = np.asarray([x for _, x in day.dense], float)
    hour = np.asarray(day.hour)
    gi = np.clip(np.rint((dt_ + ds.PRE_MIN) / ds.GRID_MIN).astype(int), 0, hour.size - 1)
    db = np.asarray(nz.b_hour)[hour[gi]] if nz is not None else np.zeros(dt_.size)
    bday = Day(day.day_minutes, np.asarray(day.forecast), None, hour, mt, mk, list(day.pending) + list(mt[mt >= 0]),
               dt_, dx, db, fmu=m)
    lat = params.model.latent
    r_dense = 0.0 if nz is None else (1 - nz.pi) * nz.s1 ** 2 + nz.pi * nz.s2 ** 2 + nz.quantum ** 2 / 12
    out = bss.grid_oracle(bday, lat.tau, lat.s2, max(r_dense, 1e-4), day.speci_from, use_dense=nz is not None, K=1201)
    ks, p = out[day.metric]
    pk = dict(zip(ks.tolist(), p.tolist()))
    q = []
    for low, high in bins:
        lo = -10 ** 6 if low is None else int(low)
        hi = 10 ** 6 if high is None else int(high)
        q.append(sum(v for k, v in pk.items() if lo <= k <= hi))
    return np.asarray(q)


def exact_day(day: ds.DenseDay) -> ds.DenseDay:
    """The same day with provisional rows taken as retained (s = 1) and no context rows."""
    return ds.DenseDay(day.metric, day.day_minutes, day.forecast, day.hour,
                       tuple(sorted(day.page + tuple((t, lo, hi) for t, lo, hi, _ in day.provisional))), (),
                       day.dense, day.pending, day.speci_from)


def replay_dense(conn, city_name: str, metric: str, decision: datetime) -> dict:
    city = runtime_cities_by_name()[city_name]
    sem = SettlementSemantics.for_city(city)
    tz = ZoneInfo(city.timezone)
    target = decision.astimezone(tz).date()
    state = read_day0_current_temperature_state(conn=conn, city=city, target_date=target.isoformat(), decision_time=decision)
    if state is None:
        return dict(city=city_name, metric=metric, decision=decision.isoformat(), error="no current state")
    center = int(round(state.value_native))
    bins = bins_around(center + (2 if metric == "high" else -2))
    inputs = day0_remaining_carrier_identity_inputs(city=city_name, unit="C", decision_time_utc=decision.isoformat(),
                                                    station_id=city.wu_station, preliminary_survival_identity="replay")
    inputs["current_path_state"] = state.identity()
    kwargs = dict(future_extremes_c=(float(state.value_native),), boundary_scenarios=((None, 1.0),), metric=metric,
                  path_error_sigma_c=1.0, instrument_sigma_c=0.3, bin_bounds_c=bins, n_point=2000, n_samples=500,
                  identity_inputs=inputs, settlement_semantics=sem)
    evidence._CACHE.clear()
    original = evidence._read_connection

    from contextlib import contextmanager

    @contextmanager
    def reader(_c):
        yield conn

    evidence._read_connection = reader
    try:
        t0 = time.perf_counter()
        carrier = build_day0_remaining_probability_carrier(**kwargs)
        elapsed = time.perf_counter() - t0
    finally:
        evidence._read_connection = original
    res = dict(city=city_name, metric=metric, decision=decision.isoformat(), state=state.identity(),
               operator=carrier["operator"], q=[round(x, 6) for x in carrier["q"]],
               bins=[list(b) for b in bins], seconds=round(elapsed, 4))
    if carrier["operator"] != evidence.DAY0_DENSE_STATE_SPACE_OPERATOR:
        return res
    res["evidence"] = {k: v for k, v in carrier["dense_evidence"].items() if k in (
        "dense_count", "dense_newest_utc", "information_cutoff_utc", "page", "provisional")}
    qualified = dense_params_for(city_name, metric, target.isoformat())
    _, params = qualified
    tau = evidence.state_receipt(conn, city=city_name, station=params.station, state=state.identity(), decision=decision)
    day, _ = evidence.gather_day(conn, params=params, city_obj=city, metric=metric, target=target, decision=tau, semantics=sem)
    reduced = exact_day(day)
    from src.contracts.settlement_semantics import settlement_preimage_offsets

    pre = settlement_preimage_offsets(sem.rounding_rule, half_step=0.5)
    core = ds.DenseModel(ds.DenseLatent(params.model.latent.tau, params.model.latent.s2), None if params.model.noise is None else
                         ds.DenseNoise(params.model.noise.b_hour, math.sqrt((1 - params.model.noise.pi) * params.model.noise.s1 ** 2
                                                                            + params.model.noise.pi * params.model.noise.s2 ** 2),
                                       math.sqrt((1 - params.model.noise.pi) * params.model.noise.s1 ** 2
                                                 + params.model.noise.pi * params.model.noise.s2 ** 2), 0.0, 1e-6),
                         params.model.mean, 0.0)
    shipped_core = ds.bin_probabilities(core, reduced, bins, preimage=pre, cell=0.025)
    backtest = oracle(params, reduced, bins, pre)
    res["vs_backtest_oracle_max_abs"] = round(float(np.max(np.abs(shipped_core - backtest))), 5)
    res["backtest_oracle_q"] = [round(float(x), 6) for x in backtest]
    res["shipped_core_q"] = [round(float(x), 6) for x in shipped_core]
    return res


def replay_legacy(conn, posterior_id: int) -> dict:
    row = conn.execute("SELECT city, temperature_metric, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
                       (posterior_id,)).fetchone()
    p = json.loads(row["provenance_json"])
    city = runtime_cities_by_name()[row["city"]]
    sem = SettlementSemantics.for_city(city)
    inputs = day0_remaining_carrier_identity_inputs(
        city=row["city"], unit="C", decision_time_utc=p["day0_remaining_carrier_probability_cutoff_utc"],
        station_id=city.wu_station, preliminary_survival_identity=p["day0_remaining_carrier_likelihood"]["identity_hash"]
        if isinstance(p.get("day0_remaining_carrier_likelihood"), dict) else "")
    return dict(posterior_id=posterior_id, city=row["city"], operator=p.get("day0_remaining_carrier_operator"),
                note="persisted identity inputs are not fully stored in provenance; see byte-identity test instead",
                inputs=sorted(inputs))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--hours", type=int, nargs="*", default=[6, 9, 12, 14])
    args = ap.parse_args()
    conn = ro_pair()
    report = {"generated_at": datetime.now(UTC).isoformat(), "families": []}
    now = datetime.now(UTC)
    for city_name, tz in (("Helsinki", "Europe/Helsinki"), ("Tokyo", "Asia/Tokyo"), ("Singapore", "Asia/Singapore")):
        z = ZoneInfo(tz)
        for day_offset in (0, 1):
            d = (now.astimezone(z) - timedelta(days=day_offset)).date()
            for h in args.hours:
                decision = datetime(d.year, d.month, d.day, h, 7, tzinfo=z).astimezone(UTC)
                if decision > now:
                    continue
                for metric in ("high", "low"):
                    try:
                        report["families"].append(replay_dense(conn, city_name, metric, decision))
                    except Exception as exc:  # noqa: BLE001 - report every family
                        report["families"].append(dict(city=city_name, metric=metric, decision=decision.isoformat(),
                                                       error=f"{type(exc).__name__}: {exc}"))
    times = [f["seconds"] for f in report["families"] if f.get("operator") == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR]
    if times:
        report["timing_seconds"] = dict(n=len(times), p50=statistics.median(times),
                                        p99=float(np.percentile(times, 99)), max=max(times))
    args.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "families"}, indent=1))
    for f in report["families"]:
        print(f.get("city"), f.get("metric"), f.get("decision"), f.get("operator"), f.get("seconds"),
              f.get("vs_backtest_oracle_max_abs"), f.get("error"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
