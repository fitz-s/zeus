#!/usr/bin/env python3
# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: docs/archive/2026-Q2/operations_historical/day0_multiangle_critique_2026-06-12.md §(3)
#   ("the one thing most likely to cause the next real-money day0 loss that
#   nobody has named yet" = fast-lane daily-extreme UNDERCAPTURE) + Blind spot D
#   ("final settlement audit is more important than timestamp-matched feed
#   audit"). Re-scoped 2026-06-12 per operator anti-over-design directive: this
#   AUDIT is the highest-value piece (it verifies observation truth against
#   settlement truth); it sets NO caps and gates NOTHING.
"""Settlement-extreme undercapture audit (read-only).

THE RISK THIS NAMES
-------------------
The METAR/observation fast lane proves fast *current-temperature* observations.
It does NOT, on its own, prove that the MAX/MIN of those current-temperature
reports equals the eventual settlement daily high/low. If a sub-interval spike
settles one unit beyond the fast lane's running extreme, Zeus can believe a bin
is alive (or fail to hard-exit) while settlement has already crossed — a loss
class strictly worse than ordinary latency.

WHAT THIS AUDIT DOES
--------------------
For each settled (city, target_date, metric) in the last N days where the city
has a fast-lane source (``fast_obs_source_for_city(city) is not None``):

  1. Reconstruct the fast-lane running daily extreme from the persisted
     hourly observation grid (``observation_instants.temp_current`` over the
     city-local target date), using the SAME settlement station the fast lane
     would read.
  2. Compare the rounded reconstructed extreme against
     ``settlement_outcomes.settlement_value`` (settlement truth).
  3. Classify each (city, date, metric):
       - EXACT      : reconstructed rounded extreme == settlement
       - UNDER      : fast-lane UNDER settlement by >= 1 unit  (THE LOSS CLASS,
                      for HIGH it means we under-read the max; for LOW we
                      over-read the min — both directions of "missed the
                      settling extreme" are counted as undercapture)
       - OVER       : fast-lane OVER settlement by >= 1 unit
       - MISSING    : no observation rows for the cell (cannot reconstruct)

FIDELITY LIMITATION (stated honestly, per the brief)
----------------------------------------------------
Raw METAR fast-lane reports are NOT persisted. This audit reconstructs from
``observation_instants`` (the hourly persisted grid in zeus-world.db). The
hourly grid is COARSER than the sub-hourly METAR stream the live fast lane sees,
so a real sub-hourly spike could be present in live METAR but absent from this
reconstruction. Therefore this audit is a LOWER BOUND on fast-lane fidelity:
an UNDER it reports is a genuine undercapture even against the coarse grid; an
EXACT here does NOT prove the live sub-hourly lane is settlement-complete. The
honest fix for full fidelity is persisting raw METAR reports (or parsing the
official 6-hour max/min remark groups); until then, treat EXACT as necessary
but not sufficient.

DB topology
-----------
- ``observation_instants`` lives in zeus-world.db (~2.7M rows).
- ``settlement_outcomes`` lives in zeus-forecasts.db.
Both opened read-only (``mode=ro`` URIs). No cross-DB ATTACH (the two are read
independently and joined in Python) — INV-37 is about WRITES; reads are
independent.

Usage
-----
::

    python scripts/audit_day0_extreme_undercapture.py [--days 14] [--json]

Exit code: 0 always (an audit reports; it does not gate). 2 on CLI/DB misuse.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import load_cities  # noqa: E402
from src.data.day0_fast_obs import fast_obs_source_for_city  # noqa: E402
from src.state.db import ZEUS_FORECASTS_DB_PATH, ZEUS_WORLD_DB_PATH  # noqa: E402

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Result types.
# ---------------------------------------------------------------------------

#: Cell classifications.
EXACT = "EXACT"
UNDER = "UNDER"
OVER = "OVER"
MISSING = "MISSING"


@dataclass
class CellResult:
    city: str
    target_date: str
    metric: str
    settlement_value: float
    reconstructed_extreme: Optional[float]
    rounded_reconstructed: Optional[int]
    classification: str
    delta_units: Optional[float]  # rounded_reconstructed - settlement (None if MISSING)
    sample_count: int
    station_id: str


@dataclass
class CityRow:
    city: str
    days_audited: int = 0
    exact: int = 0
    under: int = 0  # fast-lane UNDER settlement by >= 1 unit (the loss class)
    over: int = 0
    missing: int = 0
    cells: list[CellResult] = field(default_factory=list)


@dataclass
class AuditReport:
    generated_at: str
    days: int
    cities_audited: list[str]
    per_city: dict[str, CityRow]
    total_exact: int = 0
    total_under: int = 0
    total_over: int = 0
    total_missing: int = 0
    fidelity_limitation: str = (
        "Reconstructed from hourly observation_instants (NOT raw sub-hourly "
        "METAR). EXACT is necessary-but-not-sufficient; UNDER is a genuine "
        "undercapture even against the coarse grid."
    )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _round_half_up(value: float) -> int:
    """WMO-style half-up rounding to integer (settlement precision is 1 unit for
    all current markets). Avoids banker's rounding so a .5 always rounds away
    from zero in the positive direction the settlement contract uses."""
    import math

    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _open_ro(db_path: Path) -> Optional[sqlite3.Connection]:
    """Open a read-only connection, or None if the DB is absent."""
    if not Path(db_path).exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fast_eligible_cities(cities: list[Any]) -> dict[str, Any]:
    """{city_name: city} for cities that have a fast-lane source."""
    out: dict[str, Any] = {}
    for city in cities:
        try:
            if fast_obs_source_for_city(city) is not None:
                out[str(getattr(city, "name", ""))] = city
        except Exception:  # noqa: BLE001 — one city must not abort the audit
            continue
    return out


def _settled_cells(
    forecasts_conn: sqlite3.Connection,
    *,
    city_names: set[str],
    since_date: str,
) -> list[sqlite3.Row]:
    """Settled (city, target_date, metric, settlement_value) rows since a date.

    Only VERIFIED/UNVERIFIED authority rows with a non-null settlement_value
    enter the audit (QUARANTINED settlements are excluded — they are not trusted
    truth)."""
    if not city_names:
        return []
    placeholders = ",".join("?" for _ in city_names)
    sql = f"""
        SELECT city, target_date, temperature_metric AS metric,
               settlement_value, settlement_unit, settlement_station, authority
        FROM settlement_outcomes
        WHERE settlement_value IS NOT NULL
          AND authority IN ('VERIFIED', 'UNVERIFIED')
          AND target_date >= ?
          AND city IN ({placeholders})
        ORDER BY city, target_date, metric
    """
    return list(forecasts_conn.execute(sql, [since_date, *sorted(city_names)]).fetchall())


def _reconstruct_extreme(
    world_conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    metric: str,
    station_id: str,
) -> tuple[Optional[float], int]:
    """(reconstructed_extreme, sample_count) for the city-local target date.

    Reconstructs the running daily extreme as MAX/MIN of ``temp_current`` over
    the persisted hourly grid for the target date, restricted to the SAME
    settlement station the fast lane reads (when known). This mirrors what the
    fast lane's ``running_extremes_for_local_day`` would compute, at the coarser
    hourly cadence (the fidelity limitation documented in the module docstring).
    """
    agg = "MAX" if metric == "high" else "MIN"
    params: list[Any] = [city, target_date]
    station_clause = ""
    if station_id:
        station_clause = "AND (station_id = ? OR station_id IS NULL OR station_id = '')"
        params.append(station_id)
    sql = f"""
        SELECT {agg}(temp_current) AS extreme, COUNT(temp_current) AS n
        FROM observation_instants
        WHERE city = ? AND target_date = ?
          AND temp_current IS NOT NULL
          {station_clause}
    """
    row = world_conn.execute(sql, params).fetchone()
    if row is None:
        return None, 0
    extreme = row["extreme"]
    n = int(row["n"] or 0)
    return (float(extreme) if extreme is not None else None), n


# ---------------------------------------------------------------------------
# Importable core (a later scheduled job reuses this).
# ---------------------------------------------------------------------------

def run_undercapture_audit(
    *,
    days: int = 14,
    world_db_path: Path = ZEUS_WORLD_DB_PATH,
    forecasts_db_path: Path = ZEUS_FORECASTS_DB_PATH,
    cities: Optional[list[Any]] = None,
    now: Optional[datetime] = None,
) -> AuditReport:
    """Core audit, read-only. Returns a structured :class:`AuditReport`.

    A later scheduled job imports and calls this directly. The DB paths are
    parameters so a synthetic fixture DB can be substituted in tests.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    since_date = (moment.date() - timedelta(days=int(days))).isoformat()

    if cities is None:
        cities = load_cities()
    eligible = _fast_eligible_cities(cities)

    report = AuditReport(
        generated_at=moment.isoformat(),
        days=int(days),
        cities_audited=sorted(eligible.keys()),
        per_city={name: CityRow(city=name) for name in sorted(eligible.keys())},
    )
    if not eligible:
        return report

    world_conn = _open_ro(world_db_path)
    forecasts_conn = _open_ro(forecasts_db_path)
    try:
        if forecasts_conn is None or world_conn is None:
            return report
        settled = _settled_cells(
            forecasts_conn, city_names=set(eligible.keys()), since_date=since_date
        )
        for srow in settled:
            city = str(srow["city"])
            target_date = str(srow["target_date"])
            metric = str(srow["metric"])
            if metric not in {"high", "low"}:
                continue
            settlement_value = float(srow["settlement_value"])
            station_id = str(srow["settlement_station"] or "").strip().upper()
            crow = report.per_city.get(city)
            if crow is None:
                continue

            extreme, n = _reconstruct_extreme(
                world_conn,
                city=city,
                target_date=target_date,
                metric=metric,
                station_id=station_id,
            )
            crow.days_audited += 1
            if extreme is None or n == 0:
                classification = MISSING
                rounded = None
                delta = None
                crow.missing += 1
            else:
                rounded = _round_half_up(extreme)
                delta = float(rounded) - settlement_value
                # Undercapture = the fast lane MISSED the settling extreme.
                # HIGH: reconstructed max below settlement -> UNDER (loss).
                # LOW:  reconstructed min above settlement -> UNDER (loss).
                missed_low_side = metric == "high" and rounded < settlement_value - 1e-9
                missed_high_side = metric == "low" and rounded > settlement_value + 1e-9
                if abs(delta) < 1e-9:
                    classification = EXACT
                    crow.exact += 1
                elif missed_low_side or missed_high_side:
                    classification = UNDER
                    crow.under += 1
                else:
                    classification = OVER
                    crow.over += 1

            crow.cells.append(
                CellResult(
                    city=city,
                    target_date=target_date,
                    metric=metric,
                    settlement_value=settlement_value,
                    reconstructed_extreme=extreme,
                    rounded_reconstructed=rounded,
                    classification=classification,
                    delta_units=delta,
                    sample_count=n,
                    station_id=station_id,
                )
            )
    finally:
        if world_conn is not None:
            world_conn.close()
        if forecasts_conn is not None:
            forecasts_conn.close()

    for crow in report.per_city.values():
        report.total_exact += crow.exact
        report.total_under += crow.under
        report.total_over += crow.over
        report.total_missing += crow.missing
    return report


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------

def render_markdown(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append(f"# Day0 settlement-extreme undercapture audit")
    lines.append("")
    lines.append(f"- Generated: {report.generated_at}")
    lines.append(f"- Window: last {report.days} days")
    lines.append(f"- Fast-eligible cities audited: {len(report.cities_audited)}")
    lines.append("")
    lines.append(
        f"**Totals** — EXACT {report.total_exact}, "
        f"UNDER (loss class) {report.total_under}, "
        f"OVER {report.total_over}, MISSING {report.total_missing}"
    )
    lines.append("")
    lines.append(f"> Fidelity limitation: {report.fidelity_limitation}")
    lines.append("")
    lines.append("## Per-city")
    lines.append("")
    lines.append("| City | Days | EXACT | UNDER(loss) | OVER | MISSING |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for name in report.cities_audited:
        c = report.per_city[name]
        if c.days_audited == 0:
            continue
        lines.append(
            f"| {name} | {c.days_audited} | {c.exact} | {c.under} | {c.over} | {c.missing} |"
        )
    lines.append("")
    # Enumerate the loss-class cells explicitly (the actionable rows).
    loss_cells = [
        cell
        for c in report.per_city.values()
        for cell in c.cells
        if cell.classification == UNDER
    ]
    if loss_cells:
        lines.append("## UNDER (loss-class) cells")
        lines.append("")
        lines.append("| City | Date | Metric | Settlement | Reconstructed(rounded) | Delta | n | Station |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|")
        for cell in loss_cells:
            lines.append(
                f"| {cell.city} | {cell.target_date} | {cell.metric} | "
                f"{cell.settlement_value:g} | {cell.rounded_reconstructed} | "
                f"{cell.delta_units:+g} | {cell.sample_count} | {cell.station_id} |"
            )
        lines.append("")
    return "\n".join(lines)


def _report_to_jsonable(report: AuditReport) -> dict[str, Any]:
    return {
        "generated_at": report.generated_at,
        "days": report.days,
        "cities_audited": report.cities_audited,
        "totals": {
            "exact": report.total_exact,
            "under_loss_class": report.total_under,
            "over": report.total_over,
            "missing": report.total_missing,
        },
        "fidelity_limitation": report.fidelity_limitation,
        "per_city": {
            name: {
                "days_audited": c.days_audited,
                "exact": c.exact,
                "under": c.under,
                "over": c.over,
                "missing": c.missing,
            }
            for name, c in report.per_city.items()
            if c.days_audited > 0
        },
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14, help="Lookback window in days (default 14).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown to stdout.")
    parser.add_argument(
        "--out-dir",
        default="docs/evidence/day0",
        help="Directory for the markdown report (default docs/evidence/day0).",
    )
    args = parser.parse_args(argv)

    try:
        report = run_undercapture_audit(days=args.days)
    except sqlite3.Error as exc:
        print(f"DB error: {exc}", file=sys.stderr)
        return 2

    md = render_markdown(report)
    if args.json:
        print(json.dumps(_report_to_jsonable(report), indent=2))
    else:
        print(md)

    # Always also write the markdown evidence file.
    out_dir = _REPO_ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{datetime.now(UTC).date().isoformat()}_extreme_undercapture_audit.md"
    out_path.write_text(md + "\n", encoding="utf-8")
    print(f"\n[wrote {out_path}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
