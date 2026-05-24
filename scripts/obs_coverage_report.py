#!/usr/bin/env python3
# Lifecycle: created=2026-05-23; last_reviewed=2026-05-23; last_reused=never
# Authority basis: operator FIX-5 CORE-P0 spec — observation coverage monitoring tool
"""Observation coverage report — read-only monitoring script.

Purpose: surface which settlement cities have fresh vs stale/missing
observation coverage so blinding of obs-dependent strategies
(settlement_capture / day0_capture / imminent_open_capture) is visible
BEFORE it silently suppresses fills.

Two-layer diagnosis:
  1. Gate-level (source_health.json via evaluate_freshness): did the fetch
     succeed? This is what the actual freshness gate checks — per-source,
     GLOBAL (staleness of any gated source disables day0 for ALL cities).
  2. DB-level (observation_instants_v2 in zeus-world.db): did obs actually
     land per-city? Catches "probe green, no rows landing" silent failures.

Per-city verdict (refinement of the global gate decision):
  COVERED  — all source families that have historically covered this city
             are currently fresh at gate-level, AND city has recent DB rows.
  DEGRADED — some covering source family stale, some fresh; OR gate fresh
             but no recent DB rows for this city.
  BLIND    — every covering source family in DAY0_CAPTURE_GATED_SOURCES
             is currently stale at gate-level (city's obs strategies will
             fail-close). Note: if the GLOBAL gate fires, all cities are
             effectively BLIND regardless of this per-city refinement.

Source family → DB source prefix mapping:
  wu_pws            → wu_%           (wu_icao_history, most cities)
  hko               → hko_%          (hko_hourly_accumulator, HKG only)
  ogimet            → ogimet_%       (ogimet_metar_ICAO, subset of cities)
  open_meteo_archive → (probe-only)  — no DB rows; staleness = GLOBAL gate
  noaa              → (probe-only)   — no DB rows; staleness = GLOBAL gate

Do NOT modify freshness_gate.py or any gate/trade logic.
Do NOT write to state/, DB, or any side-file.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import sqlite3

from src.config import cities_by_name
from src.control.freshness_gate import (
    DAY0_CAPTURE_GATED_SOURCES,
    FRESHNESS_BUDGETS,
    evaluate_freshness,
)

# ── Source family → DB source name prefix (for observation_instants_v2) ──────
# open_meteo_archive and noaa are probe-only (reachability checks, no DB rows).
# Their freshness is global: staleness disables day0 for ALL cities.
DB_SOURCE_PREFIX: dict[str, str] = {
    "wu_pws": "wu_%",
    "hko": "hko_%",
    "ogimet": "ogimet_%",
}
PROBE_ONLY_SOURCES = frozenset({"open_meteo_archive", "noaa"})


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h{m:02d}m ago"


def _fmt_budget(seconds: int) -> str:
    h = seconds // 3600
    return f"{h}h"


def run_report(state_dir: Path, db_path: Path, lookback_hours: int) -> None:
    now = datetime.now(timezone.utc)
    lookback_cutoff = now - timedelta(hours=lookback_hours)

    # ── 1. Gate-level verdict (authoritative, global) ─────────────────────────
    verdict = evaluate_freshness(state_dir=state_dir, now=now)

    print(f"\n{'='*72}")
    print("OBS COVERAGE REPORT")
    print(f"  Generated : {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"  State dir : {state_dir}")
    print(f"  DB path   : {db_path}")
    print(f"  Lookback  : {lookback_hours}h")
    print(f"{'='*72}\n")

    # ── Gate-level summary ────────────────────────────────────────────────────
    print("GATE-LEVEL SOURCE FRESHNESS (source_health.json)")
    print(f"  health file written_at: {verdict.written_at or 'ABSENT'}")
    print(f"  global branch         : {verdict.branch}")
    print(f"  day0_capture_disabled : {verdict.day0_capture_disabled}")
    print(f"  ensemble_disabled     : {verdict.ensemble_disabled}")
    if verdict.operator_overrides:
        print(f"  operator_overrides    : {verdict.operator_overrides}")
    print()

    # Per-source table
    header = f"  {'SOURCE':<24} {'STATUS':<8} {'BUDGET':<8} {'AGE':<14} {'GATED'}"
    print(header)
    print("  " + "-" * 68)
    source_fresh: dict[str, bool] = {}
    for ss in verdict.source_statuses:
        status_label = "FRESH" if ss.fresh else "STALE"
        gated = (
            "DAY0" if ss.source in DAY0_CAPTURE_GATED_SOURCES
            else "ENSEMBLE" if ss.source in (FRESHNESS_BUDGETS.keys() - DAY0_CAPTURE_GATED_SOURCES)
            else ""
        )
        probe_note = " [probe-only]" if ss.source in PROBE_ONLY_SOURCES else ""
        print(
            f"  {ss.source:<24} {status_label:<8} "
            f"{_fmt_budget(ss.budget_seconds):<8} "
            f"{_fmt_age(ss.age_seconds):<14} "
            f"{gated}{probe_note}"
        )
        source_fresh[ss.source] = ss.fresh
    print()

    if verdict.branch == "ABSENT":
        print("WARNING: source_health.json absent or unreadable — gate verdict degraded to all-STALE.\n")
        # Treat all sources as stale
        source_fresh = {src: False for src in FRESHNESS_BUDGETS}

    # ── 2. DB-level: per-city latest import per source family ─────────────────
    print("DB-LEVEL OBS COVERAGE (observation_instants_v2, zeus-world.db)")
    print(f"  Lookback window: {lookback_hours}h (cutoff {lookback_cutoff.strftime('%Y-%m-%dT%H:%MZ')})\n")

    # Query latest import per city per source-family prefix
    # Read-only: open with mode=ro
    city_source_latest: dict[str, dict[str, datetime | None]] = {}

    # Open read-only via URI — never write to the live DB
    db_uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(db_uri, uri=True)
    try:
        cur = conn.cursor()
        for family, prefix in DB_SOURCE_PREFIX.items():
            rows = cur.execute(
                """
                SELECT city, MAX(imported_at) AS latest
                FROM observation_instants_v2
                WHERE source LIKE ?
                GROUP BY city
                """,
                (prefix,),
            ).fetchall()
            for city_name, latest_str in rows:
                if city_name not in city_source_latest:
                    city_source_latest[city_name] = {}
                if latest_str:
                    try:
                        dt = datetime.fromisoformat(latest_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        city_source_latest[city_name][family] = dt
                    except (ValueError, TypeError):
                        city_source_latest[city_name][family] = None
                else:
                    city_source_latest[city_name][family] = None
    finally:
        conn.close()

    # ── 3. Per-city verdict ───────────────────────────────────────────────────
    city_names = sorted(cities_by_name)

    # Determine global BLIND (probe-only stale sources)
    probe_only_stale = [
        src for src in PROBE_ONLY_SOURCES
        if not source_fresh.get(src, False)
    ]
    global_gate_stale = verdict.day0_capture_disabled or bool(probe_only_stale)

    print(f"  {'CITY':<22} {'VERDICT':<12} {'wu_pws':<16} {'hko':<16} {'ogimet':<16} NOTES")
    print("  " + "-" * 98)

    verdict_counts: dict[str, int] = {"COVERED": 0, "DEGRADED": 0, "BLIND": 0}
    blind_cities: list[str] = []

    for city in city_names:
        city_data = city_source_latest.get(city, {})

        # Which DB-backed gated sources have ever covered this city?
        covering_families = [fam for fam in DB_SOURCE_PREFIX if fam in DAY0_CAPTURE_GATED_SOURCES]
        covered_by: list[str] = []
        for fam in covering_families:
            if city_data.get(fam) is not None:
                covered_by.append(fam)

        # Gate-fresh status per family
        fam_status: dict[str, str] = {}
        for fam in covering_families:
            latest = city_data.get(fam)
            if latest is None:
                fam_status[fam] = "no-rows"
            elif latest < lookback_cutoff:
                fam_status[fam] = f"stale({_fmt_age((now - latest).total_seconds())})"
            else:
                fam_status[fam] = f"ok({_fmt_age((now - latest).total_seconds())})"

        # Determine city verdict
        # BLIND conditions:
        # (a) Global gate fires (probe-only stale) — all cities affected, OR
        # (b) All DB-covering gated sources for this city are either stale/absent
        #     AND gate-level stale
        db_gated_stale = all(
            (not source_fresh.get(fam, False) or city_data.get(fam) is None or city_data.get(fam, now) < lookback_cutoff)
            for fam in covered_by
        ) if covered_by else False

        gate_stale_families = [fam for fam in covered_by if not source_fresh.get(fam, True)]

        if global_gate_stale or (covered_by and db_gated_stale and gate_stale_families):
            city_verdict = "BLIND"
            blind_cities.append(city)
        elif not covered_by:
            # No DB obs history for this city — only probe-only sources cover it
            if all(source_fresh.get(src, False) for src in PROBE_ONLY_SOURCES if src in DAY0_CAPTURE_GATED_SOURCES):
                city_verdict = "COVERED"  # relies on probe-only sources, which are fresh
            else:
                city_verdict = "BLIND"
                blind_cities.append(city)
        else:
            # Has DB coverage — check if any DB source is recent
            any_fresh_db = any(
                v is not None and v >= lookback_cutoff
                for v in city_data.values()
            )
            any_stale_gate = any(not source_fresh.get(fam, True) for fam in covered_by)
            if any_fresh_db and not any_stale_gate:
                city_verdict = "COVERED"
            else:
                city_verdict = "DEGRADED"

        verdict_counts[city_verdict] = verdict_counts.get(city_verdict, 0) + 1

        # Format per-source columns
        def _col(fam: str) -> str:
            s = fam_status.get(fam, "no-rows")
            return s[:15]

        notes = ""
        if global_gate_stale and city_verdict == "BLIND":
            notes = f"global: {probe_only_stale or verdict.stale_sources}"

        print(
            f"  {city:<22} {city_verdict:<12} "
            f"{_col('wu_pws'):<16} {_col('hko'):<16} {_col('ogimet'):<16} {notes}"
        )

    print()

    # ── 4. Summary ────────────────────────────────────────────────────────────
    print("SUMMARY")
    print(f"  Total cities  : {len(city_names)}")
    print(f"  COVERED       : {verdict_counts.get('COVERED', 0)}")
    print(f"  DEGRADED      : {verdict_counts.get('DEGRADED', 0)}")
    print(f"  BLIND         : {verdict_counts.get('BLIND', 0)}")

    if verdict.stale_sources:
        print(f"\n  Stale sources : {', '.join(verdict.stale_sources)}")
    if probe_only_stale:
        print(f"  Probe-only stale (global gate fires): {', '.join(probe_only_stale)}")
    if blind_cities:
        print(f"\n  BLIND cities ({len(blind_cities)}):")
        for bc in blind_cities:
            print(f"    - {bc}")

    print()
    if verdict.day0_capture_disabled:
        print("  ACTION REQUIRED: day0_capture / settlement_capture / imminent_open strategies")
        print("  are GLOBALLY disabled. Fix the stale fetch source(s), not the gate.")
    else:
        print("  All strategies gate: OPEN (no action required for obs gate).")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only obs coverage report for all settlement cities.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=PROJECT_ROOT / "state",
        help="Path to state/ directory (default: <project_root>/state)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to zeus-world.db (default: <state-dir>/zeus-world.db)",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=6,
        help="DB obs lookback window in hours (default: 6, matches wu_pws/open_meteo budget)",
    )
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    db_path = (args.db_path or (state_dir / "zeus-world.db")).resolve()

    if not state_dir.is_dir():
        print(f"ERROR: state_dir does not exist: {state_dir}", file=sys.stderr)
        sys.exit(1)
    if not db_path.exists():
        print(f"ERROR: zeus-world.db not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    run_report(state_dir=state_dir, db_path=db_path, lookback_hours=args.hours)


if __name__ == "__main__":
    main()
