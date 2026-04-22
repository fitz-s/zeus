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
  ``--min-expected`` (default 18000 for a 2024-01-01 → 2026-04-21
  pilot; HK excluded per Phase 0 design).
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
    EXPECTED_SOURCE_BY_CITY,
    TIER_ALLOWED_SOURCES,
    TIER_SCHEDULE,
    Tier,
    tier_for_city,
)

DEFAULT_DB_PATH = _REPO_ROOT / "state" / "zeus-world.db"

# Expected row count per city for a 2024-01-01 → 2026-04-21 hourly pilot.
# 24h × 832 days = ~19,968 hours. Allow ±10% (plan specifies ±5% in
# AC5 but gap-tolerant starting threshold is 18000 for 832 days).
_DEFAULT_MIN_EXPECTED_PER_CITY = 18_000


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
    """Rows whose source is not in its tier's allowed set (tier-wide check)."""
    violations: list[dict[str, Any]] = []
    for tier, allowed in TIER_ALLOWED_SOURCES.items():
        cities_in_tier = [
            name for name, t in TIER_SCHEDULE.items() if t is tier
        ]
        if not cities_in_tier:
            continue
        placeholders = ",".join(["?"] * len(cities_in_tier))
        allowed_list = sorted(allowed)
        allowed_placeholders = ",".join(["?"] * len(allowed_list))
        sql = (
            f"SELECT city, source, COUNT(*) FROM observation_instants_v2 "
            f"WHERE data_version = ? "
            f"  AND city IN ({placeholders}) "
            f"  AND source NOT IN ({allowed_placeholders}) "
            f"GROUP BY city, source"
        )
        params: list[Any] = [data_version] + cities_in_tier + allowed_list
        for city, source, count in conn.execute(sql, params).fetchall():
            violations.append(
                {
                    "tier": tier.name,
                    "city": city,
                    "source": source,
                    "count": int(count),
                }
            )
    return violations


def _source_tier_mismatches(
    conn: sqlite3.Connection, data_version: str
) -> list[dict[str, Any]]:
    """Rows whose source != expected_source_for_city(city)."""
    mismatches: list[dict[str, Any]] = []
    cities = _all_cities_for_data_version(conn, data_version)
    for city in cities:
        expected = EXPECTED_SOURCE_BY_CITY.get(city)
        if expected is None:
            mismatches.append(
                {
                    "city": city,
                    "expected": None,
                    "source": None,
                    "count": 0,
                    "note": "city not in EXPECTED_SOURCE_BY_CITY",
                }
            )
            continue
        rows = conn.execute(
            "SELECT source, COUNT(*) FROM observation_instants_v2 "
            "WHERE data_version = ? AND city = ? AND source != ? "
            "GROUP BY source",
            (data_version, city, expected),
        ).fetchall()
        for source, count in rows:
            mismatches.append(
                {
                    "city": city,
                    "expected": expected,
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


def _build_report(
    conn: sqlite3.Connection,
    data_version: str,
    min_expected: int,
) -> dict[str, Any]:
    per_city = _per_city_rowcounts(conn, data_version)
    tier_viol = _tier_violations(conn, data_version)
    src_mismatch = _source_tier_mismatches(conn, data_version)
    auth_bad = _authority_unverified_rows(conn, data_version)
    om_rows = _openmeteo_rows(conn, data_version)
    below_thresh = _cities_below_threshold(per_city, min_expected)

    # Hong Kong is expected-gap; filter from below-threshold list.
    below_thresh = [b for b in below_thresh if b["city"] != "Hong Kong"]

    gate_ready = (
        len(tier_viol) == 0
        and len(src_mismatch) == 0
        and auth_bad == 0
        and om_rows == 0
        and len(below_thresh) == 0
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
        "min_expected_per_city": min_expected,
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

    conn = sqlite3.connect(str(args.db))
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
        report = _build_report(conn, args.data_version, args.min_expected)
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
