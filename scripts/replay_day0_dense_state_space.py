#!/usr/bin/env python3
# Created: 2026-10-07
# Last reused or audited: 2026-10-08
# Authority basis: docs/operations/current/plans/task_2026-10-07_dense_obs_probability_model.md
#   (D6 replay, D7 byte-identity); read-only against live DBs.
"""Replay Day0 carriers on live evidence, read-only, and report.

Two separate questions, never merged:
  legacy  For each named persisted live V2/V3 posterior, rebuild its carrier from its own
          provenance through the shipped builder (evaluation=None, the path every adapter call
          takes) and compare q, samples and content identity with the persisted values, byte for
          byte.  Run on two trees to prove the legacy path unchanged.
  dense   For each (city, metric, cut), run the production SELECT path (prepare_dense_request on the
          rows received by the cut, then the operator) and replay the sealed evidence with REPLAY;
          report the operator, serve/decline reason, replay equality and compute time.  This tests
          dispatch, sealing and replay determinism; it is not a probability-quality claim (D6 is).

Both DBs are opened ``file:...?mode=ro`` with query_only (forecasts-main + world attached).
Writes only a JSON report to ``--out``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.config import ensemble_n_mc, runtime_cities_by_name  # noqa: E402
from src.contracts.settlement_semantics import SettlementSemantics  # noqa: E402
from src.data import day0_dense_evidence as evidence  # noqa: E402
from src.data.day0_hourly_vectors import (  # noqa: E402
    Day0CarrierEvaluation,
    build_day0_remaining_probability_carrier,
    day0_remaining_carrier_identity_inputs,
)
from src.signal.ensemble_signal import sigma_instrument_for_city  # noqa: E402

STATE = Path("/Users/leofitz/zeus/state")
UTC = timezone.utc


def ro_pair() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{STATE / 'zeus-forecasts.db'}?mode=ro", uri=True, timeout=30)
    conn.execute("ATTACH DATABASE ? AS world", (f"file:{STATE / 'zeus-world.db'}?mode=ro",))
    conn.execute("PRAGMA query_only = ON")
    return conn


def replay_legacy(conn, posterior_id: int) -> dict:
    """Rebuild one persisted legacy carrier from its provenance; compare with the persisted values."""
    row = conn.execute("SELECT city, temperature_metric, provenance_json FROM forecast_posteriors WHERE posterior_id = ?",
                       (posterior_id,)).fetchone()
    p = json.loads(row[2])
    city = runtime_cities_by_name()[row[0]]
    unit = city.settlement_unit
    lik = p.get("day0_preliminary_report_survival_likelihood") or {}
    survival = float(lik["boundary_survival_probability"])
    inputs = day0_remaining_carrier_identity_inputs(
        city=row[0], unit=unit, decision_time_utc=p["day0_remaining_carrier_probability_cutoff_utc"],
        station_id=city.wu_station, preliminary_survival_identity=str(lik["identity_hash"]))
    inputs["current_path_state"] = p["day0_current_temperature_state"]
    inputs["day0_remaining_center_policy"] = p["day0_remaining_center_policy"]
    inputs["day0_probability_mixture_policy"] = p["day0_probability_mixture_policy"]
    if p.get("day0_conditional_high_shape_identity"):
        inputs["conditional_high_shape_identity"] = p["day0_conditional_high_shape_identity"]
    bins = tuple((None if b["lower_c"] is None else float(b["lower_c"]), None if b["upper_c"] is None else float(b["upper_c"]))
                 for b in p["bin_topology"])
    observed = float(p["day0_provisional_observation"]["observed_extreme_c"])
    carrier = build_day0_remaining_probability_carrier(
        future_extremes_c=tuple(p["day0_remaining_carrier_future_extremes_c"]),
        final_extreme_centers_c=tuple(p.get("day0_remaining_carrier_final_extremes_c") or ()),
        boundary_scenarios=((observed, survival), (None, 1.0 - survival)), metric=row[1],
        path_error_sigma_c=float(p["day0_remaining_carrier_path_error_sigma_c"]),
        instrument_sigma_c=float(sigma_instrument_for_city(city).to(unit).value), bin_bounds_c=bins,
        n_point=ensemble_n_mc(), n_samples=500, identity_inputs=inputs,
        settlement_semantics=SettlementSemantics.for_city(city), operator=p["day0_remaining_carrier_operator"],
        remaining_center_bias_native=0.0)
    blob = json.dumps(carrier, sort_keys=True, separators=(",", ":")).encode()
    return dict(posterior_id=posterior_id, city=row[0], metric=row[1], operator=carrier["operator"],
                carrier_sha256=hashlib.sha256(blob).hexdigest(),
                identity_equal=carrier["content_identity"] == p["day0_remaining_carrier_content_identity"],
                q_equal=[float(x) for x in carrier["q"]] == [float(x) for x in p["day0_remaining_carrier_q"]])


def replay_dense(conn, city_name: str, metric: str, cut: datetime) -> dict:
    """Production SELECT at ``cut`` on the default params artifact, then REPLAY from the sealed evidence."""
    city = runtime_cities_by_name()[city_name]
    sem = SettlementSemantics.for_city(city)
    target = cut.astimezone(ZoneInfo(city.timezone)).date()
    started = time.perf_counter()
    prepared = evidence.prepare_dense_request(conn, city=city_name, metric=metric, target=target, cut=cut, semantics=sem)
    res = dict(city=city_name, metric=metric, cut=cut.isoformat(), serves=prepared.serves, reason=prepared.reason)
    if not prepared.serves:
        return res
    inputs = day0_remaining_carrier_identity_inputs(city=city_name, unit="C", decision_time_utc=cut.isoformat(),
                                                    station_id=city.wu_station, preliminary_survival_identity="r" * 64)
    inputs["current_path_state"] = {"value_native": 0.0, "observed_at_utc": cut.isoformat(), "source": "replay"}
    lo = int(min(prepared.sealed["forecast"])) - 6
    bins = ((None, float(lo)),) + tuple((float(k), float(k)) for k in range(lo + 1, lo + 20)) + ((float(lo + 20), None),)
    common = dict(future_extremes_c=(0.0,), boundary_scenarios=((None, 1.0),), metric=metric, path_error_sigma_c=0.5,
                  instrument_sigma_c=0.2, bin_bounds_c=bins, n_point=10, n_samples=500, identity_inputs=inputs,
                  settlement_semantics=sem)
    selected = build_day0_remaining_probability_carrier(**common, evaluation=Day0CarrierEvaluation.SELECT)
    res["seconds"] = round(time.perf_counter() - started, 3)
    res["operator"] = selected["operator"]
    if selected["operator"] == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR:
        sealed = selected["dense_evidence"]["sealed"]
        replay = build_day0_remaining_probability_carrier(**{**common, "operator": selected["operator"]},
                                                          evaluation=Day0CarrierEvaluation.REPLAY, sealed_dense=sealed)
        res["replay_equal"] = replay["q"] == selected["q"] and replay["content_identity"] == selected["content_identity"]
        res["sealed_bytes"] = len(json.dumps(sealed, sort_keys=True, separators=(",", ":")))
        res["counts"] = dict(page=len(sealed["page"]), marks=len(sealed["marks"]), pending=len(sealed["pending"]),
                             dense=sealed["dense_count"])
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--legacy", type=int, nargs="*", default=[], help="persisted V2/V3 posterior ids")
    ap.add_argument("--hours", type=int, nargs="*", default=[6, 9, 12, 14, 18])
    ap.add_argument("--days", type=int, default=2)
    args = ap.parse_args()
    conn = ro_pair()
    from contextlib import contextmanager

    @contextmanager
    def _same_reader(_conn):
        yield conn

    # The builder's SELECT reads through this one read-only pair (live state paths, not the worktree's).
    evidence._read_connection = _same_reader
    report = {"generated_at": datetime.now(UTC).isoformat(), "legacy": [], "dense": []}
    for pid in args.legacy:
        report["legacy"].append(replay_legacy(conn, pid))
    now = datetime.now(UTC)
    for city_name in ("Helsinki", "Tokyo", "Singapore"):
        z = ZoneInfo(runtime_cities_by_name()[city_name].timezone)
        for day_offset in range(args.days):
            d = (now.astimezone(z) - timedelta(days=day_offset)).date()
            for h in args.hours:
                cut = datetime(d.year, d.month, d.day, h, 7, tzinfo=z).astimezone(UTC)
                if cut > now:
                    continue
                for metric in ("high", "low"):
                    try:
                        report["dense"].append(replay_dense(conn, city_name, metric, cut))
                    except Exception as exc:  # noqa: BLE001 - report every family
                        report["dense"].append(dict(city=city_name, metric=metric, cut=cut.isoformat(),
                                                    error=f"{type(exc).__name__}: {exc}"))
    served = [f for f in report["dense"] if f.get("operator") == evidence.DAY0_DENSE_STATE_SPACE_OPERATOR]
    if served:
        times = [f["seconds"] for f in served]
        sizes = [f["sealed_bytes"] for f in served]
        report["dense_summary"] = dict(n=len(served), p50_seconds=statistics.median(times), max_seconds=max(times),
                                       sealed_bytes_p50=statistics.median(sizes), sealed_bytes_max=max(sizes),
                                       replay_equal_all=all(f["replay_equal"] for f in served))
    args.out.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: v for k, v in report.items() if k not in ("dense",)}, indent=1, default=str))
    for f in report["dense"]:
        print(f.get("city"), f.get("metric"), f.get("cut"), f.get("operator") or f.get("reason"), f.get("seconds"),
              f.get("replay_equal"), f.get("sealed_bytes"), f.get("error"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
