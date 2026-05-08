#!/usr/bin/env python3
# Created: 2026-04-21
# Last reused/audited: 2026-04-21
# Authority basis: plan v3 Phase 0 file #7 (.omc/plans/observation-instants-
#                  migration-iter3.md L86-93, L121-122, L164-168).
"""Row-count + invariant audit for observation_instants_v2.

Read-only inspection. Never modifies the DB. Emits a JSON report
keyed by the Phase 0 Gate 0→1 acceptance criteria so
``jq .tier_violations == 0`` maps straight to AC7 and
``jq .source_tier_mismatches == 0`` maps straight to AC8.

Usage
-----
::

    python scripts/audit_observation_instants_v2.py \\
        --data-version v1.wu-native.pilot [--json]

What it reports
---------------
- ``per_city_rowcounts``: {city: count_of_rows_for_data_version}.
- ``tier_violations``: rows whose ``source`` does not match the
  tier-allowed set per ``tier_resolver.TIER_ALLOWED_SOURCES`` (tier-wide
  check; catches Taipei-46692-style pre-migration drift even if the
  city is now wu_icao).
- ``source_tier_mismatches``: rows whose ``source`` does not equal
  ``tier_resolver.expected_source_for_city(city)`` (per-city check;
  catches Moscow-with-LLBG-source).
- ``authority_unverified_rows``: count of rows with authority='UNVERIFIED'
  or 'QUARANTINED' (readers filter these out; writing them is forbidden
  by the writer, but audit sweeps the base table for legacy rows).
- ``openmeteo_rows``: count of rows with source LIKE '%openmeteo%'
  (AC4 — Day-0 ghost-trade regression pin).
- ``cities_below_threshold``: cities whose row count is below
  ``--min-expected`` (default 20000 for a 2024-01-01 → 2026-04-21
  pilot, 842 days × 24h = 20,208 expected; HK excluded per Phase 0 design).
- ``dates_under_threshold``: per-city per-day gaps; the CRITICAL check
  caught nothing at the per-city level because 22-hour DST holes
  represent only 0.1% of 20,208. Per-day check surfaces them. Valid
  ranges: 22-25 UTC hours per local date (23 = DST spring-forward,
  25 = DST fall-back).
- ``gate_0_1_ready``: boolean — true iff all thresholds pass.

Exit code
---------
- 0: audit passes (gate 0→1 green).
- 1: at least one threshold fails.
- 2: CLI misuse / DB missing / schema missing.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data.tier_resolver import (  # noqa: E402
    ALLOWED_SOURCES_BY_CITY,
    EXPECTED_SOURCE_BY_CITY,
    TIER_ALLOWED_SOURCES,
    TIER_SCHEDULE,
    Tier,
    allowed_sources_for_city,
    tier_for_city,
)
# db_writer_lock removed: this script is read-only (PR #86 Copilot fix)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"
DEFAULT_GAPS_ALLOWLIST_PATH = (
    _REPO_ROOT
    / "docs"
    / "operations"
    / "task_2026-04-21_gate_f_data_backfill"
    / "confirmed_upstream_gaps.yaml"
)

# Expected row count per city for a 2024-01-01 → 2026-04-21 hourly pilot.
# Inclusive days = julianday('2026-04-21') - julianday('2024-01-01') + 1 = 842.
# 842 × 24 = 20,208 expected UTC hours per non-HK city. Tight tolerance
# (20,000) catches missing-days better than the earlier loose 18,000 which
# hid 22-hour DST-day gaps.
_DEFAULT_MIN_EXPECTED_PER_CITY = 20_000

# Per-day sanity threshold. Normal day = 24 UTC hours; DST spring-forward
# = 23 hours (local); DST fall-back = 25 hours (local). UTC is always 24
# but the aggregator's local-date filter converts back. Accept 22-25.
_MIN_HOURS_PER_DAY = 22
_MAX_HOURS_PER_DAY = 25


def _all_cities_for_data_version(
    conn: sqlite3.Connection, data_version: str
) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT city FROM observation_instants_v2 "
        "WHERE data_version = ? ORDER BY city",
        (data_version,),
    ).fetchall()
    return [r[0] for r in rows]


def _per_city_rowcounts(
    conn: sqlite3.Connection, data_version: str
) -> dict[str, int]:
    rows = conn.execute(
        "SELECT city, COUNT(*) FROM observation_instants_v2 "
        "WHERE data_version = ? GROUP BY city ORDER BY city",
        (data_version,),
    ).fetchall()
    return {city: int(count) for city, count in rows}


def _tier_violations(
    conn: sqlite3.Connection, data_version: str
) -> list[dict[str, Any]]:
    """Rows whose source is not in its city's allowed set.

    Updated 2026-04-22 (critic C1 fix): uses per-city allowed-sources
    (ALLOWED_SOURCES_BY_CITY) rather than per-tier TIER_ALLOWED_SOURCES,
    so Tier 1 cities' Ogimet fallback rows (source='ogimet_metar_<icao>')
    no longer register as violations. A violation now means the row's
    source is neither the city's primary nor its documented fallback.
    """
    violations: list[dict[str, Any]] = []
    cities = _all_cities_for_data_version(conn, data_version)
    for city in cities:
        if city not in ALLOWED_SOURCES_BY_CITY:
            continue  # unknown city — handled by source_tier_mismatches
        allowed = sorted(ALLOWED_SOURCES_BY_CITY[city])
        placeholders = ",".join(["?"] * len(allowed))
        sql = (
            f"SELECT source, COUNT(*) FROM observation_instants_v2 "
            f"WHERE data_version = ? AND city = ? "
            f"  AND source NOT IN ({placeholders}) "
            f"GROUP BY source"
        )
        params: list[Any] = [data_version, city] + allowed
        for source, count in conn.execute(sql, params).fetchall():
            violations.append(
                {
                    "tier": tier_for_city(city).name,
                    "city": city,
                    "source": source,
                    "count": int(count),
                }
            )
    return violations


def _source_tier_mismatches(
    conn: sqlite3.Connection, data_version: str
) -> list[dict[str, Any]]:
    """Rows whose source is not in the city's allowed-set.

    Semantically identical to _tier_violations now that allowed-sets are
    per-city; kept as a separate function for API compatibility with
    earlier Gate 0→1 docs. A row is a mismatch if its source is not
    in ``allowed_sources_for_city``.
    """
    mismatches: list[dict[str, Any]] = []
    cities = _all_cities_for_data_version(conn, data_version)
    for city in cities:
        if city not in ALLOWED_SOURCES_BY_CITY:
            mismatches.append(
                {
                    "city": city,
                    "expected": None,
                    "source": None,
                    "count": 0,
                    "note": "city not in ALLOWED_SOURCES_BY_CITY",
                }
            )
            continue
        allowed = ALLOWED_SOURCES_BY_CITY[city]
        placeholders = ",".join(["?"] * len(allowed))
        sql = (
            f"SELECT source, COUNT(*) FROM observation_instants_v2 "
            f"WHERE data_version = ? AND city = ? "
            f"  AND source NOT IN ({placeholders}) "
            f"GROUP BY source"
        )
        params: list[Any] = [data_version, city] + sorted(allowed)
        for source, count in conn.execute(sql, params).fetchall():
            mismatches.append(
                {
                    "city": city,
                    "expected_set": sorted(allowed),
                    "source": source,
                    "count": int(count),
                }
            )
    return mismatches


def _authority_unverified_rows(
    conn: sqlite3.Connection, data_version: str
) -> int:
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM observation_instants_v2 "
        "WHERE data_version = ? AND authority IN ('UNVERIFIED', 'QUARANTINED')",
        (data_version,),
    ).fetchone()
    return int(count)


def _openmeteo_rows(conn: sqlite3.Connection, data_version: str) -> int:
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM observation_instants_v2 "
        "WHERE data_version = ? AND source LIKE '%openmeteo%'",
        (data_version,),
    ).fetchone()
    return int(count)


def _cities_below_threshold(
    per_city: dict[str, int], min_expected: int
) -> list[dict[str, Any]]:
    return [
        {"city": city, "count": count, "min_expected": min_expected}
        for city, count in per_city.items()
        if count < min_expected
    ]


def _load_gaps_allowlist(path: Path) -> set[tuple[str, str]]:
    """Return {(city, target_date)} of confirmed-upstream gaps.

    Reads a minimal YAML subset without requiring PyYAML. The file
    format is stable and small so a hand-rolled parser is cheaper than
    adding a dependency.
    """
    if not path.exists():
        return set()
    accepted: set[tuple[str, str]] = set()
    current_city: str | None = None
    current_date: str | None = None
    with path.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            stripped = line.strip()
            if stripped.startswith("- city:"):
                if current_city and current_date:
                    accepted.add((current_city, current_date))
                current_city = stripped.split(":", 1)[1].strip().strip("'\"")
                current_date = None
            elif stripped.startswith("city:"):
                current_city = stripped.split(":", 1)[1].strip().strip("'\"")
            elif stripped.startswith("target_date:"):
                current_date = stripped.split(":", 1)[1].strip().strip("'\"")
        if current_city and current_date:
            accepted.add((current_city, current_date))
    return accepted


def _dates_under_threshold(
    conn: sqlite3.Connection, data_version: str
) -> list[dict[str, Any]]:
    """Per (city, target_date) buckets whose UTC-hour count is outside [22, 25].

    22: DST-spring-forward low bound (23-hour local day).
    25: DST-fall-back high bound (25-hour local day).
    <22: suspect DST upstream hole (WU) or chunk-boundary clip.
    >25: overlap/duplicate; shouldn't happen with UNIQUE(city,source,utc_timestamp)
         unless multiple sources write different utc values for the same hour.

    Returns each offending (city, target_date, distinct_utc_hour_count).
    HK is excluded (accumulator-only, gap expected).
    """
    sql = f"""
        SELECT city, target_date, COUNT(DISTINCT utc_timestamp) AS hours
        FROM observation_instants_v2
        WHERE data_version = ?
          AND city != 'Hong Kong'
        GROUP BY city, target_date
        HAVING hours < {_MIN_HOURS_PER_DAY} OR hours > {_MAX_HOURS_PER_DAY}
        ORDER BY city, target_date
    """
    rows = conn.execute(sql, (data_version,)).fetchall()
    return [
        {"city": city, "target_date": td, "hours": int(h)}
        for city, td, h in rows
    ]


def _build_report(
    conn: sqlite3.Connection,
    data_version: str,
    min_expected: int,
    gaps_allowlist: set[tuple[str, str]],
) -> dict[str, Any]:
    per_city = _per_city_rowcounts(conn, data_version)
    tier_viol = _tier_violations(conn, data_version)
    src_mismatch = _source_tier_mismatches(conn, data_version)
    auth_bad = _authority_unverified_rows(conn, data_version)
    om_rows = _openmeteo_rows(conn, data_version)
    below_thresh = _cities_below_threshold(per_city, min_expected)
    bad_dates_raw = _dates_under_threshold(conn, data_version)

    # Hong Kong is expected-gap; filter from below-threshold list.
    below_thresh = [b for b in below_thresh if b["city"] != "Hong Kong"]

    # Split bad_dates into blocking vs confirmed-upstream-gap.
    blocking_dates: list[dict[str, Any]] = []
    accepted_dates: list[dict[str, Any]] = []
    for d in bad_dates_raw:
        key = (d["city"], d["target_date"])
        if key in gaps_allowlist:
            accepted_dates.append(d)
        else:
            blocking_dates.append(d)

    gate_ready = (
        len(tier_viol) == 0
        and len(src_mismatch) == 0
        and auth_bad == 0
        and om_rows == 0
        and len(below_thresh) == 0
        and len(blocking_dates) == 0
    )

    return {
        "data_version": data_version,
        "total_rows": sum(per_city.values()),
        "city_count": len(per_city),
        "per_city_rowcounts": per_city,
        "tier_violations": tier_viol,
        "source_tier_mismatches": src_mismatch,
        "authority_unverified_rows": auth_bad,
        "openmeteo_rows": om_rows,
        "cities_below_threshold": below_thresh,
        "dates_under_threshold": blocking_dates,
        "confirmed_upstream_gaps_accepted": accepted_dates,
        "min_expected_per_city": min_expected,
        "min_hours_per_day": _MIN_HOURS_PER_DAY,
        "max_hours_per_day": _MAX_HOURS_PER_DAY,
        "gate_0_1_ready": gate_ready,
    }


def _format_human(report: dict[str, Any]) -> str:
    lines = []
    lines.append(f"observation_instants_v2 audit — data_version={report['data_version']}")
    lines.append("=" * 72)
    lines.append(f"Total rows:  {report['total_rows']:>10,}")
    lines.append(f"City count:  {report['city_count']:>10}")
    lines.append("")
    lines.append("Per-city rowcounts:")
    for city, count in sorted(report["per_city_rowcounts"].items()):
        marker = " !!" if count < report["min_expected_per_city"] and city != "Hong Kong" else ""
        lines.append(f"  {city:20s}: {count:>10,}{marker}")
    lines.append("")
    lines.append("Invariants:")
    lines.append(f"  tier_violations:          {len(report['tier_violations'])}")
    lines.append(f"  source_tier_mismatches:   {len(report['source_tier_mismatches'])}")
    lines.append(f"  authority_unverified_rows:{report['authority_unverified_rows']}")
    lines.append(f"  openmeteo_rows:           {report['openmeteo_rows']}")
    lines.append(
        f"  cities_below_threshold:   {len(report['cities_below_threshold'])} "
        f"(min_expected={report['min_expected_per_city']})"
    )
    lines.append(
        f"  dates_under_threshold:    {len(report['dates_under_threshold'])} "
        f"(per-day range=[{report['min_hours_per_day']},{report['max_hours_per_day']}])"
    )
    if report.get("confirmed_upstream_gaps_accepted"):
        lines.append(
            f"  confirmed_upstream_gaps:  {len(report['confirmed_upstream_gaps_accepted'])} (accepted via allowlist)"
        )
    lines.append("")
    lines.append(
        f"Gate 0→1: {'✅ READY' if report['gate_0_1_ready'] else '❌ NOT READY'}"
    )
    if report["tier_violations"]:
        lines.append("")
        lines.append("Tier violations (first 10):")
        for v in report["tier_violations"][:10]:
            lines.append(f"  {v['city']:16s} source={v['source']!r:30s} count={v['count']}")
    if report["source_tier_mismatches"]:
        lines.append("")
        lines.append("Source/tier mismatches (first 10):")
        for m in report["source_tier_mismatches"][:10]:
            lines.append(
                f"  {m['city']:16s} expected={m['expected']!r:30s} "
                f"got={m['source']!r} count={m['count']}"
            )
    if report["dates_under_threshold"]:
        lines.append("")
        lines.append(
            f"Dates under/over threshold (first 20 of {len(report['dates_under_threshold'])}):"
        )
        for d in report["dates_under_threshold"][:20]:
            lines.append(
                f"  {d['city']:16s} {d['target_date']} hours={d['hours']}"
            )
    return "\n".join(lines)


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="observation_instants_v2 row-count + invariant audit",
    )
    p.add_argument(
        "--data-version",
        required=True,
        help="data_version tag to audit (e.g. 'v1.wu-native.pilot').",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"SQLite DB path (default: {DEFAULT_DB_PATH}).",
    )
    p.add_argument(
        "--min-expected",
        type=int,
        default=_DEFAULT_MIN_EXPECTED_PER_CITY,
        help=(
            "Minimum rows per city to clear the below-threshold gate "
            f"(default: {_DEFAULT_MIN_EXPECTED_PER_CITY:,})."
        ),
    )
    p.add_argument(
        "--gaps-allowlist",
        type=Path,
        default=DEFAULT_GAPS_ALLOWLIST_PATH,
        help=(
            f"Path to the confirmed-upstream-gaps YAML "
            f"(default: {DEFAULT_GAPS_ALLOWLIST_PATH}). "
            "Entries here are exempted from the per-day completeness check."
        ),
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw report as JSON (for jq / CI).",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    if not args.db.exists():
        print(f"FATAL: DB not found at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        # Confirm schema exists before querying.
        (tbl,) = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='observation_instants_v2' LIMIT 1"
        ).fetchone() or (None,)
        if tbl is None:
            print(
                "FATAL: observation_instants_v2 table missing; run "
                "apply_v2_schema first.",
                file=sys.stderr,
            )
            return 2
        allowlist = _load_gaps_allowlist(args.gaps_allowlist)
        report = _build_report(
            conn,
            args.data_version,
            args.min_expected,
            gaps_allowlist=allowlist,
        )
    finally:
        conn.close()

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(_format_human(report))

    return 0 if report["gate_0_1_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
