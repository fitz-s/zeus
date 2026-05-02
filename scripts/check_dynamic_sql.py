#!/usr/bin/env python3
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P2 (security review §10
#                  "30+ f-string SQL interpolations, no whitelist enforcement")
# Purpose: per-file baseline of dynamic SQL (f-string interpolation in
#          cursor.execute*) calls. New call site beyond baseline fails the gate.
"""Dynamic-SQL surface scanner.

Why this exists
---------------
Security review (`docs/operations/repo_review_2026-05-01/security.md`) flagged
30+ f-string SQL interpolations across `src/state/db.py`, `src/main.py`, and
others. All current sites bind identifiers (table names, column names) from
INTERNAL whitelists — none take user-controlled input. The concern is
forward-looking: one future refactor that pipes a request param through an
f-string is enough for SQL injection.

This scanner doesn't prove every existing site is safe (taint-tracking is out
of scope for a 1-file scanner). It locks the per-file count of dynamic SQL
sites at the 2026-05-01 baseline (74 sites total across 22 files) so:

- Any NEW file gaining its first dynamic SQL site fails the gate.
- Any existing file with MORE dynamic SQL sites than baseline fails the gate.

Both conditions force an audit at PR review time. The audit answer is either:
1. "Yes, this is internal — bump the baseline" — operator updates this script
   and the test, then commits.
2. "Wait, this is reading user input — refactor to parameterized SQL or
   identifier-quoting helper before merging."

Repaired sites (file count drops below baseline) also fail — that's the
"shrink the baseline as you fix things" forcing function.

Patterns matched
----------------
The scanner counts call expressions of the form:
    cursor.execute(f"...")
    conn.execute(f"...")
    cur.executemany(f"...")
    cur.executescript(f"...")
where the f-string is a direct positional argument. NOT matched:
    execute(sql_str)            # string from a variable — out of scope
    execute("..." + var)        # explicit concatenation — separate audit class

Add new bind-style interpolations (`%s`-formatting, `.format()`) here only if
the security audit identifies them as a real surface — for now scope is
f-strings, which is the bulk of the surface.

CLI
---
    python3 scripts/check_dynamic_sql.py            # exit 0 OK, 2 drift
    python3 scripts/check_dynamic_sql.py --json     # JSON report on stdout

Test wrapper
------------
`tests/test_dynamic_sql_baseline.py` calls the same resolver and asserts
zero drift beyond the baseline. Pre-commit hook includes the test file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"


# Pattern: ANY identifier (cursor / conn / cur / db / self.conn / etc) with a
# `.execute*(f"..."` or `.execute*(f'...'` call expression. Anchored on the
# DOT before `execute` so non-method calls (e.g. `subprocess.run`) don't match.
_DYNAMIC_SQL_PATTERN = re.compile(
    r"\.\s*(?:execute|executemany|executescript)\s*\(\s*f[\"']",
    re.MULTILINE,
)


# Per-file baseline as of 2026-05-01 (74 sites across 22 files).
# Update both this dict AND the `total` field below when a site is added or
# removed. The test wrapper enforces both directions.
#
# 2026-05-02 (PR #37 follow-up): src/calibration/store.py registered with
# 14 dynamic-SQL sites. All sites construct `FROM/JOIN <table>` and similar
# table-name interpolations from a small internal whitelist defined in the
# module; no user input is interpolated. Per the scanner contract
# (interpolated identifier from internal whitelist → bump baseline), this
# is an explicit registration, not a relaxation.
_BASELINE_PER_FILE: dict[str, int] = {
    "src/backtest/economics.py": 1,
    "src/calibration/effective_sample_size.py": 2,
    "src/calibration/store.py": 14,
    "src/contracts/world_schema_validator.py": 1,
    "src/data/daily_obs_append.py": 4,
    "src/data/daily_observation_writer.py": 6,
    "src/data/ingest_status_writer.py": 1,
    "src/data/observation_instants_v2_writer.py": 4,
    "src/data/solar_append.py": 4,
    "src/engine/cycle_runtime.py": 4,
    "src/engine/evaluator.py": 13,
    "src/engine/replay.py": 14,
    "src/execution/harvester.py": 5,
    "src/execution/settlement_commands.py": 4,
    "src/ingest/harvester_truth_writer.py": 1,
    "src/ingest/polymarket_user_channel.py": 1,
    "src/main.py": 1,
    "src/observability/status_summary.py": 3,
    "src/state/chronicler.py": 1,
    "src/state/db.py": 21,
    "src/state/ledger.py": 8,
    "src/state/projection.py": 2,
    "src/state/schema/v2_schema.py": 1,
    "src/state/venue_command_repo.py": 5,
    "src/state/ws_poll_reaction.py": 1,
    # Tail catch — fresh files with f-string SQL must be added explicitly.
}


def collect_per_file_counts() -> Counter:
    counts: Counter = Counter()
    for py_file in SRC_DIR.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n = len(_DYNAMIC_SQL_PATTERN.findall(text))
        if n:
            rel = str(py_file.relative_to(REPO_ROOT))
            counts[rel] = n
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)

    actual = collect_per_file_counts()
    baseline = _BASELINE_PER_FILE

    new_files = sorted(set(actual) - set(baseline))
    grown = sorted(
        f for f in actual if f in baseline and actual[f] > baseline[f]
    )
    shrunk = sorted(
        f for f in actual if f in baseline and actual[f] < baseline[f]
    )
    repaired = sorted(set(baseline) - set(actual))

    drift_lines: list[str] = []
    for f in new_files:
        drift_lines.append(
            f"NEW file: {f} has {actual[f]} dynamic-SQL site(s). "
            "Audit each: is the interpolated identifier from a closed "
            "internal whitelist, or could it accept user-controlled input? "
            "If safe, add to _BASELINE_PER_FILE in "
            "scripts/check_dynamic_sql.py."
        )
    for f in grown:
        drift_lines.append(
            f"GROWN: {f} now has {actual[f]} sites (baseline {baseline[f]}). "
            f"Audit the {actual[f] - baseline[f]} new occurrence(s) per the "
            "above criteria."
        )
    for f in shrunk:
        drift_lines.append(
            f"SHRUNK: {f} now has {actual[f]} sites (baseline {baseline[f]}). "
            "If you removed dynamic SQL — congrats; update _BASELINE_PER_FILE "
            "to the new count so the gate tightens."
        )
    for f in repaired:
        drift_lines.append(
            f"REPAIRED: {f} no longer has dynamic SQL (baseline {baseline[f]}). "
            "Remove the entry from _BASELINE_PER_FILE so the gate tightens."
        )

    if args.json:
        report = {
            "ok": not drift_lines,
            "total_actual": sum(actual.values()),
            "total_baseline": sum(baseline.values()),
            "new_files": new_files,
            "grown": grown,
            "shrunk": shrunk,
            "repaired": repaired,
            "actual_counts": dict(sorted(actual.items())),
            "baseline_counts": dict(sorted(baseline.items())),
        }
        print(json.dumps(report, indent=2))
        return 0 if not drift_lines else 2

    if drift_lines:
        print(
            f"[check_dynamic_sql] BLOCKED — {len(drift_lines)} drift event(s) "
            f"vs 2026-05-01 baseline (total {sum(baseline.values())} sites "
            f"across {len(baseline)} files):",
            file=sys.stderr,
        )
        for line in drift_lines:
            print(f"  {line}", file=sys.stderr)
        print(
            "\nThis gate locks the dynamic-SQL surface so a new f-string-interpolation "
            "SQL site cannot land silently. Each new site requires audit: is the "
            "interpolated identifier from a closed internal source, or could it "
            "accept user input? If safe → bump baseline. If unsafe → refactor to "
            "parameterized binding or `quote_identifier()` helper before merging.",
            file=sys.stderr,
        )
        return 2

    print(
        f"[check_dynamic_sql] OK — {sum(actual.values())} dynamic-SQL site(s) "
        f"across {len(actual)} files, all matching 2026-05-01 baseline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
