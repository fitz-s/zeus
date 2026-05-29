#!/usr/bin/env python3
# Created: 2026-05-28
# Last reused or audited: 2026-05-29
# Authority basis (P2 wiring 2026-05-29): TRIBUNAL P2 step-4 (P2_LEDGER_SEAM_FINDINGS_2026-05-29.md)
#   — route the forecast↔settlement pairing through residual_key.pair_residual (D-J1 wrong-station
#   drop) + read the canonical `dataset_id` lineage column (renamed from data_version, B5).
# Authority basis: operator redesign 2026-05-28 — evidence-ledger-backed candidate selection,
#   Principle 3 (evidence ledger is part of training data) + Principle 4 (HIGH window validity proof).
#   Supersedes the trust-the-contributes-flag path: that flag marks contaminated 12z samples as valid
#   (verified 2026-05-28: Jeddah/SF have contributes=1 on BOTH cycles yet 12z carries -5..-7C artifact).
# Purpose: Emit an auditable per-sample residual evidence ledger with CYCLE-STRICT extraction
#   (HIGH -> 0Z cycle only, LOW -> 12Z cycle only). Every bias_c must be a deterministic aggregate
#   of retained, window-proven evidence rows. READ-ONLY on the source DB; writes a CSV sidecar.
"""Residual evidence ledger builder.

For each (city, metric, target_date) with a VERIFIED settlement, select the cycle-valid
snapshot(s) and emit one evidence row per (city, metric, target_date):
  residual_c = ensemble_mean_c - settlement_value_c

CYCLE-STRICT RULE (the structural window fix):
  HIGH accepts ONLY the 0Z cycle (covers local afternoon peak for the cities we trade).
  LOW  accepts ONLY the 12Z cycle (covers local pre-dawn min).
  Any other cycle (06/18) or NULL issue_time is REJECTED with selection_reason.
  This is "proof or no sample" made concrete and uniform — it does NOT rely on the
  per-row window-attribution metadata, which is demonstrably unreliable.

Within the accepted cycle, when multiple snapshots exist for a date, keep the freshest
by available_at (latest forecast issued before settlement, lead<=lead_max).

OUTPUT: CSV sidecar with full provenance per sample + a fit-aggregate summary per bucket.
NEVER writes to any DB.

USAGE
-----
    .venv/bin/python scripts/build_ens_residual_evidence.py \
        --source-db state/zeus-forecasts.db \
        --metric high \
        --out docs/operations/ENS_RESIDUAL_EVIDENCE_2026-05-28.csv \
        [--cities "Jeddah,Shanghai,..."]  [--compare-cycles]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sqlite3
import statistics
import sys
from pathlib import Path

from src.contracts.ensemble_snapshot_provenance import MembersUnitInvalidError
from src.contracts.forecast_object import ForecastObject, ForecastObjectIncompleteError
from src.contracts.forecast_target import ForecastTargetMismatchError
from src.contracts.residual_key import (
    ResidualKey,
    SettlementIncompleteError,
    SettlementObject,
    pair_residual,
    source_kind_for_data_version,
)
from src.contracts.residual_value import residual_celsius

logger = logging.getLogger(__name__)

# Refuse canonical DBs as source (mirrors fit_full_transport_error_models guard): the ledger
# must be built from an isolated copy / historical store, never the live truth DBs.
_FORBIDDEN_BASENAMES = {"zeus-world.db", "zeus_trades.db"}

_HIGH_CYCLE = "00"
_LOW_CYCLE = "12"

# Northern-hemisphere calendar season; SH flip applied via lat sign.
_NH = {12: "DJF", 1: "DJF", 2: "DJF", 3: "MAM", 4: "MAM", 5: "MAM",
       6: "JJA", 7: "JJA", 8: "JJA", 9: "SON", 10: "SON", 11: "SON"}
_SH_FLIP = {"DJF": "JJA", "JJA": "DJF", "MAM": "SON", "SON": "MAM"}


def _season(month: int, lat: float | None) -> str:
    s = _NH[month]
    return _SH_FLIP[s] if (lat is not None and lat < 0) else s


def _load_lat(cities_json: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        d = json.loads(cities_json.read_text())
    except (OSError, json.JSONDecodeError):
        return out
    for c in d.get("cities", []):
        if isinstance(c, dict) and c.get("name") and c.get("lat") is not None:
            out[c["name"]] = float(c["lat"])
    return out


def _is_fahrenheit(unit: str | None) -> bool:
    u = (unit or "").strip().lower()
    return u in {"f", "degf", "fahrenheit"} or (bool(u) and u.endswith("f"))


def _to_celsius(value: float, unit: str | None) -> float:
    """Convert a temperature VALUE (not a delta) to degC per the native unit."""
    return (value - 32.0) * 5.0 / 9.0 if _is_fahrenheit(unit) else value


def _ensemble_mean_c(members_json: str, members_unit: str | None) -> float | None:
    try:
        parsed = json.loads(members_json)
        vals = [float(x) for x in (parsed.values() if isinstance(parsed, dict) else parsed) if x is not None]
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not vals:
        return None
    return _to_celsius(statistics.mean(vals), members_unit)


def _cycle_for_metric(metric: str) -> str:
    return _HIGH_CYCLE if metric == "high" else _LOW_CYCLE


def _strict_evidence_row(e: dict, *, metric: str, lat: dict) -> dict | None:
    """Build one STRICT-ledger output row from an evidence dict ``e``.

    Returns None when the ensemble mean is None (bad members_json).
    Uses residual_celsius (each side converted by its own unit) and
    source_kind_for_data_version (derived lineage, not hardcoded 'prior').
    Raises ValueError from source_kind_for_data_version on an unrecognised
    data_version lineage.
    """
    try:
        parsed = json.loads(e["members_json"])
        members_list = [float(x) for x in (
            parsed.values() if isinstance(parsed, dict) else parsed
        ) if x is not None]
    except (json.JSONDecodeError, TypeError, ValueError):
        members_list = []
    if not members_list:
        return None

    em = _to_celsius(statistics.mean(members_list), e["members_unit"])
    mo = int(str(e["target_date"])[5:7])
    seas = _season(mo, lat.get(e["city"]))
    residual = residual_celsius(
        members_list,
        e["members_unit"],
        e["settlement_value_c"],
        e["settlement_unit"],
    )
    sk = source_kind_for_data_version(e["data_version"])
    return {
        "city": e["city"],
        "metric": metric,
        "season": seas,
        "month": mo,
        "target_date": e["target_date"],
        "source_kind": sk,
        "data_version": e["data_version"],
        "product": e.get("product"),
        "snapshot_id": e["snapshot_id"],
        "settlement_id": e["settlement_id"],
        "issue_time": e["issue_time"],
        "cycle": e["cycle"],
        "lead_hours": e["lead_hours"],
        "contributes_to_target_extrema": e["contributes_to_target_extrema"],
        "boundary_ambiguous": e["boundary_ambiguous"],
        "forecast_window_start_utc": e.get("forecast_window_start_utc"),
        "forecast_window_end_utc": e.get("forecast_window_end_utc"),
        "source_run_id": e.get("source_run_id"),
        "available_at": e["available_at"],
        "members_unit": e["members_unit"],
        "ensemble_mean_c": round(em, 3),
        "settlement_value_c": round(
            _to_celsius(e["settlement_value_c"], e["settlement_unit"]), 3
        ),
        "settlement_value_native": round(e["settlement_value_c"], 3),
        "settlement_unit": e["settlement_unit"],
        "residual_c": round(residual, 3),
        "selection_reason": f"cycle_strict_{_cycle_for_metric(metric)}_only",
    }


def _pair_or_drop(
    forecast_row: dict, settlement_row: dict, *, claimed_unit: str
) -> ResidualKey | None:
    """Gate one candidate forecast<->settlement pairing through the typed contract.

    Builds a ForecastObject + SettlementObject and routes them through ``pair_residual``,
    which RAISES unless they describe the same random variable (city / metric / target_date
    / station / unit / authority). Returns the keyed ``ResidualKey`` on a true pair; returns
    ``None`` — logged, fail-closed — when the settlement is a different RV than the forecast
    claims (D-J1: the legacy loose JOIN matched on city+date+metric only and would pair a
    wrong-station settlement), or when either side is incomplete / carries an unrecognized
    lineage. A dropped sample is NEVER emitted with a collapsed lineage.
    """
    try:
        forecast = ForecastObject.from_snapshot_row(forecast_row)
        settlement = SettlementObject.from_settlement_row(
            settlement_row, claimed_unit=claimed_unit
        )
        return pair_residual(forecast, settlement)
    except (
        ForecastTargetMismatchError,
        SettlementIncompleteError,
        ForecastObjectIncompleteError,
        MembersUnitInvalidError,
        ValueError,
    ) as exc:
        logger.warning(
            "dropped pairing city=%s metric=%s date=%s: %s",
            forecast_row.get("city"),
            forecast_row.get("temperature_metric"),
            forecast_row.get("target_date"),
            exc,
        )
        return None


def build_evidence(conn: sqlite3.Connection, *, metric: str, lead_max: float,
                   cities: list[str] | None, accept_cycle: str | None) -> list[dict]:
    """Return one evidence row per (city, target_date) for the accepted cycle.

    accept_cycle=None uses the metric-strict cycle; pass an explicit "00"/"12"/"ALL" to
    override (for --compare-cycles diagnostics).
    """
    where = ["e.temperature_metric = ?", "e.lead_hours <= ?",
             "e.authority = 'VERIFIED'", "s.authority = 'VERIFIED'",
             "e.members_json IS NOT NULL", "s.settlement_value IS NOT NULL"]
    params: list[object] = [metric, lead_max]
    if cities:
        where.append("e.city IN (%s)" % ",".join("?" * len(cities)))
        params.extend(cities)

    # Canonical lineage column is `dataset_id` (renamed from data_version, commit dd3d19d00a /
    # B5); alias it back to data_version so the row dict key the contracts + _strict_evidence_row
    # consume is stable. Read by column NAME (dict per row) — no fragile positional unpack.
    cur = conn.execute(
        f"""
        SELECT e.city, e.target_date, e.temperature_metric, e.snapshot_id, e.issue_time,
               e.source_cycle_time, e.lead_hours, e.available_at, e.members_json,
               e.members_unit, e.dataset_id AS data_version,
               e.contributes_to_target_extrema, e.boundary_ambiguous,
               e.forecast_window_start_utc, e.forecast_window_end_utc, e.source_run_id,
               e.settlement_station_id, e.settlement_unit, e.settlement_source_type,
               s.settlement_id, s.settlement_value, s.settlement_source, s.provenance_json
        FROM ensemble_snapshots e
        JOIN settlement_outcomes s
          ON s.city = e.city AND s.target_date = e.target_date
         AND s.temperature_metric = e.temperature_metric
        WHERE {" AND ".join(where)}
        """,
        params,
    )
    colnames = [d[0] for d in cur.description]
    rows = [dict(zip(colnames, raw)) for raw in cur.fetchall()]

    strict_cycle = accept_cycle or _cycle_for_metric(metric)

    # Group by (city, target_date); within the accepted cycle keep freshest available_at.
    # Each candidate routes through the typed pairing gate (_pair_or_drop): a wrong-station /
    # wrong-RV settlement (D-J1: the loose JOIN matched city+date+metric only) is DROPPED,
    # never emitted with a collapsed lineage.
    best: dict[tuple, dict] = {}
    rejected_cycle = 0
    rejected_pairing = 0
    for r in rows:
        issue = r["issue_time"]
        hh = str(issue)[11:13] if issue else "NULL"
        if strict_cycle != "ALL" and hh != strict_cycle:
            rejected_cycle += 1
            continue
        rkey = _pair_or_drop(r, r, claimed_unit=r.get("settlement_unit"))
        if rkey is None:
            rejected_pairing += 1
            continue
        key = (r["city"], r["target_date"])
        av = r["available_at"]
        prev = best.get(key)
        if prev is None or str(av) > str(prev["available_at"]):
            best[key] = {
                "city": r["city"], "target_date": r["target_date"], "snapshot_id": r["snapshot_id"],
                "issue_time": issue, "cycle": hh, "lead_hours": r["lead_hours"],
                "available_at": av, "members_json": r["members_json"], "members_unit": r["members_unit"],
                "data_version": r["data_version"],
                "contributes_to_target_extrema": r["contributes_to_target_extrema"],
                "boundary_ambiguous": r["boundary_ambiguous"],
                "forecast_window_start_utc": r["forecast_window_start_utc"],
                "forecast_window_end_utc": r["forecast_window_end_utc"],
                "source_run_id": r["source_run_id"],
                "settlement_id": r["settlement_id"],
                "settlement_value_c": float(r["settlement_value"]), "settlement_unit": r["settlement_unit"],
                "product": rkey.product,
            }

    logger.info(
        "metric=%s cycle=%s: %d candidate rows, %d kept (one per date), %d rejected by cycle, "
        "%d dropped by pairing gate (wrong-station/incomplete)",
        metric, strict_cycle, len(rows), len(best), rejected_cycle, rejected_pairing,
    )
    return list(best.values())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-db", required=True, type=Path)
    ap.add_argument("--cities-json", type=Path,
                    default=Path("/Users/leofitz/.openclaw/workspace-venus/zeus/config/cities.json"))
    ap.add_argument("--metric", choices=("high", "low"), default="high")
    ap.add_argument("--lead-max", type=float, default=48.0)
    ap.add_argument("--cities", default="", help="comma-separated subset; empty = all")
    ap.add_argument("--compare-cycles", action="store_true",
                    help="emit per-bucket 0Z vs 12Z vs ALL bias comparison instead of the strict ledger")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()),
                        format="%(asctime)s %(levelname)s %(message)s")

    if args.source_db.name in _FORBIDDEN_BASENAMES:
        logger.error("refusing canonical DB %s as source; copy to an isolated file first", args.source_db.name)
        return 2

    lat = _load_lat(args.cities_json)
    cities = [c.strip() for c in args.cities.split(",") if c.strip()] or None
    conn = sqlite3.connect(f"file:{args.source_db}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=1;")

    if args.compare_cycles:
        # Diagnostic: per (city, season) bias under 00 / 12 / ALL.
        out_rows = []
        for cyc in ("00", "12", "ALL"):
            ev = build_evidence(conn, metric=args.metric, lead_max=args.lead_max,
                                cities=cities, accept_cycle=cyc)
            bucket: dict[tuple, list[float]] = {}
            for e in ev:
                em = _ensemble_mean_c(e["members_json"], e["members_unit"])
                if em is None:
                    continue
                mo = int(str(e["target_date"])[5:7])
                seas = _season(mo, lat.get(e["city"]))
                settle_c = _to_celsius(e["settlement_value_c"], e["members_unit"])
                bucket.setdefault((e["city"], seas), []).append(em - settle_c)
            for (city, seas), rs in bucket.items():
                out_rows.append({
                    "city": city, "metric": args.metric, "season": seas, "cycle": cyc,
                    "n": len(rs), "mean_residual_c": round(statistics.mean(rs), 3),
                    "median_residual_c": round(statistics.median(rs), 3),
                })
        out_rows.sort(key=lambda r: (r["city"], r["season"], r["cycle"]))
    else:
        # Strict ledger: one row per retained sample, full provenance.
        ev = build_evidence(conn, metric=args.metric, lead_max=args.lead_max,
                            cities=cities, accept_cycle=None)
        out_rows = []
        for e in ev:
            row = _strict_evidence_row(e, metric=args.metric, lat=lat)
            if row is not None:
                out_rows.append(row)
        out_rows.sort(key=lambda r: (r["city"], r["season"], r["target_date"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if out_rows:
        with args.out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
            w.writeheader()
            w.writerows(out_rows)

    # Fit-aggregate per bucket (city, season) — what a bias_c WOULD be from this evidence.
    if not args.compare_cycles and out_rows:
        agg: dict[tuple, list[float]] = {}
        for r in out_rows:
            agg.setdefault((r["city"], r["season"]), []).append(r["residual_c"])
        print(f"\n{'city':16s}{'season':6s}{'n':>4s}  mean_resid  median  evidence_hash")
        print("-" * 70)
        for (city, seas), rs in sorted(agg.items()):
            h = hashlib.sha256((";".join(f"{x:.3f}" for x in sorted(rs))).encode()).hexdigest()[:12]
            print(f"{city:16s}{seas:6s}{len(rs):4d}  {statistics.mean(rs):+9.2f}  {statistics.median(rs):+6.2f}  {h}")

    logger.info("wrote %d rows -> %s", len(out_rows), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
