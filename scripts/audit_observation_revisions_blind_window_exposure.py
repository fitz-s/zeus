#!/usr/bin/env python3
# Lifecycle: created=2026-07-16; last_reviewed=2026-07-16; last_reused=2026-07-16
# Purpose: Quantify the observation-revision blind-window exposure without writing canonical truth.
# Reuse: Inspect the fixed window, heuristic-only interpretation, and script manifest before relying on output.
# Authority basis: 5997ee49d (observation_revisions CHECK rebuild) — the
#                  audit-spine repair that surfaced this blind window.
"""Read-only exposure-surface scan for the 2026-05-28..2026-07-16 blind window.

The observation_revisions table's CHECK constraint still admitted only the
pre-2026-05-28-consolidation table_name values ('observation_instants_v2',
'observations'). Every writer since the 2026-05-29 v1/v2 merge inserts
table_name='observation_instants', so from 2026-05-28 until f1d135901's
2026-07-16 deploy, ANY payload-hash-changed re-fetch of an hour bucket during
that window was quarantined into a revision INSERT that the CHECK silently
rejected via INSERT OR IGNORE — the main row stayed frozen AND no audit trail
was recorded. There is nothing left to replay for that window (unlike the
pre-05-28 backlog backfill_widened_observation_instants.py already applied):
the revision was never written at all.

IMPORTANT — this script measures EXPOSURE, not CONFIRMED damage. A cell
flagged here (observation_count == 1, no widening ever recorded against it)
is a candidate for having missed a WU/Ogimet/HKO backfill that would have
widened it — there is no ground truth to compare against locally (the
would-be revision row simply doesn't exist), so this is a heuristic surface
area, not a proof any individual cell is actually wrong. A single-report hour
bucket is also a completely normal, correct outcome on its own (METAR cadence
is ~hourly; many buckets legitimately only ever see one report). The only way
to convert "exposed" into "confirmed" is to re-fetch from the upstream source
and see whether it now returns something wider — which is exactly what
scripts/backfill_wu_blind_window.py does for the WU-sourced subset.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.config import STATE_DIR

DEFAULT_DB = STATE_DIR / "zeus-world.db"
BLIND_WINDOW_START = "2026-05-28"
BLIND_WINDOW_END = "2026-07-16"
_WIDENING_APPLIED_REASONS = (
    "payload_hash_mismatch_monotone_widening_applied",
    "backfill_monotone_widening_2026-07-16",
)


def scan_blind_window_exposure(
    conn: sqlite3.Connection,
    *,
    start: str = BLIND_WINDOW_START,
    end: str = BLIND_WINDOW_END,
) -> list[dict]:
    """Return one row per exposed cell: single-report bucket, never widened.

    Two flat scans + a Python set difference, not a correlated NOT EXISTS —
    observation_revisions has no index on (reason) and the candidate set is
    ~130K rows on each side, which makes the correlated form pathological.
    """
    placeholders = ", ".join("?" for _ in _WIDENING_APPLIED_REASONS)
    widened_cells = {
        (r[0], r[1], r[2])
        for r in conn.execute(
            f"""
            SELECT DISTINCT city, source, utc_timestamp
            FROM observation_revisions
            WHERE reason IN ({placeholders})
            """,
            _WIDENING_APPLIED_REASONS,
        ).fetchall()
    }
    candidates = conn.execute(
        """
        SELECT city, target_date, source, utc_timestamp, observation_count
        FROM observation_instants
        WHERE target_date BETWEEN ? AND ? AND observation_count = 1
        ORDER BY city, target_date, utc_timestamp
        """,
        (start, end),
    ).fetchall()
    return [
        {"city": r[0], "target_date": r[1], "source": r[2], "utc_timestamp": r[3], "observation_count": r[4]}
        for r in candidates
        if (r[0], r[2], r[3]) not in widened_cells
    ]


def _baseline_comparison(conn: sqlite3.Connection, cells: list[dict], *, start: str, end: str) -> dict:
    """Per-city observation_count==1 RATE inside the window vs an equal-length
    baseline immediately before it — the exposure count alone conflates two
    very different things: a station whose normal cadence is ~1 report/hour
    (a high count is just that city's baseline, not blind-window damage) vs a
    city whose rate is genuinely elevated inside the window (evidence a
    backfill that would normally have folded a bucket to count>=2 never got
    the chance to). This still isn't proof for any single cell — only the
    aggregate rate is comparable — but it is a materially better signal than
    the raw count.
    """
    from datetime import date, timedelta

    window_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    baseline_end = date.fromisoformat(start) - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=window_days - 1)

    exposed_by_city = Counter(c["city"] for c in cells)
    out: dict[str, dict] = {}
    for city in sorted(exposed_by_city):
        window_total = conn.execute(
            "SELECT COUNT(*) FROM observation_instants WHERE city=? AND target_date BETWEEN ? AND ? AND observation_count IS NOT NULL",
            (city, start, end),
        ).fetchone()[0]
        baseline_total, baseline_count_one = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN observation_count=1 THEN 1 ELSE 0 END) "
            "FROM observation_instants WHERE city=? AND target_date BETWEEN ? AND ? AND observation_count IS NOT NULL",
            (city, baseline_start.isoformat(), baseline_end.isoformat()),
        ).fetchone()
        window_rate = exposed_by_city[city] / window_total if window_total else None
        baseline_rate = (baseline_count_one / baseline_total) if baseline_total else None
        out[city] = {
            "window_rate": round(window_rate, 3) if window_rate is not None else None,
            "baseline_rate": round(baseline_rate, 3) if baseline_rate is not None else None,
            "baseline_total_rows": baseline_total,
            "elevated": (
                window_rate is not None and baseline_rate is not None and window_rate > baseline_rate * 1.5
            ),
        }
    return out


def _summarize(cells: list[dict]) -> dict:
    by_city = Counter(c["city"] for c in cells)
    by_source = Counter(c["source"] for c in cells)
    by_week: dict[str, int] = defaultdict(int)
    for c in cells:
        # Coarse weekly bucket keyed by the Monday of that ISO week.
        from datetime import date
        d = date.fromisoformat(c["target_date"])
        monday = d.fromordinal(d.toordinal() - d.weekday())
        by_week[monday.isoformat()] += 1
    return {
        "total_exposed_cells": len(cells),
        "by_city": by_city.most_common(),
        "by_source": dict(by_source),
        "by_week": dict(sorted(by_week.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--start", default=BLIND_WINDOW_START)
    parser.add_argument("--end", default=BLIND_WINDOW_END)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True, timeout=30.0)
    try:
        cells = scan_blind_window_exposure(conn, start=args.start, end=args.end)
        baseline = _baseline_comparison(conn, cells, start=args.start, end=args.end)
    finally:
        conn.close()

    result = {
        "note": "EXPOSURE surface, not confirmed damage — see module docstring.",
        "window": {"start": args.start, "end": args.end},
        "summary": _summarize(cells),
        "baseline_comparison": baseline,
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
