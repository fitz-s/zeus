#!/usr/bin/env python3
# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P1-2 +
#                  repo_review_2026-05-01 SYNTHESIS K-D (identity-column
#                  anti-default sweep) + INV-14 (row identity)
# Purpose: scan DDL files for `DEFAULT 'X'` on bivalent / versioned identity
#          columns and fail-loud on any NEW occurrence beyond the documented
#          2026-05-01 baseline. The 5th DEFAULT 'high' must not land silently.
"""Identity-column default scanner.

Why this exists
---------------
INV-14 (row identity) says columns like `temperature_metric`, `data_version`,
and `physical_quantity` are part of canonical row identity — every INSERT
must specify them explicitly. A `DEFAULT 'high'` (for temperature_metric)
silently coerces a missing INSERT-side value into one half of a bivalent
identity, producing rows that LOOK valid but carry the wrong metric.

The 2026-05-01 multi-lane review (architect K-D) found 4 `DEFAULT 'high'`
sites; depth audit added 1 `DEFAULT 'v1'` site for `data_version`. This
scanner locks both counts: any NEW DEFAULT on these columns fails the gate.

Operator side: the actual DDL repair (drop DEFAULT, replace with explicit
INSERT discipline + migration of legacy rows) requires editing
architecture/2026_04_02_architecture_kernel.sql under ARCH_PLAN_EVIDENCE.
That repair is filed in P1_2_DEFAULT_HIGH_REPAIR.md.

Until the repair lands, this scanner ensures the surface does not GROW.
After it lands, the operator updates `KNOWN_DEFAULTS` and the scanner
keeps working as a regression gate.

Exit codes: 0 = OK, 2 = drift (new identity-column DEFAULT outside baseline)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


# Files to scan. Limited to canonical DDL surfaces — the kernel SQL and the
# Python module that hosts runtime DDL (init_schema + ALTER migrations).
_SCAN_TARGETS: tuple[Path, ...] = (
    REPO_ROOT / "architecture" / "2026_04_02_architecture_kernel.sql",
    REPO_ROOT / "src" / "state" / "db.py",
)


# Identity columns whose DEFAULT silently splits a bivalent or versioned
# identity. Each entry is (column_name, "why this is dangerous"). Adding a
# new column here is a deliberate operator decision; the same scanner then
# locks the new column too.
_GUARDED_IDENTITY_COLUMNS: dict[str, str] = {
    "temperature_metric": (
        "INV-14 row identity (HIGH/LOW). DEFAULT 'high' silently routes any "
        "missing-value INSERT to the HIGH-track regardless of the actual "
        "track that produced the row."
    ),
    "data_version": (
        "INV-14 row identity (versioned). DEFAULT 'v1' silently labels any "
        "missing-value INSERT with the v1 calibration era regardless of "
        "which model version actually produced the row."
    ),
}


# Baseline of DEFAULT occurrences known and documented as of 2026-05-01.
# Each entry is (column, file_relative, line_no_lower_bound).
# When the operator repairs a site and removes the DEFAULT, also remove
# the matching entry from this baseline so the gate becomes stricter.
_BASELINE_KNOWN_DEFAULTS: frozenset[tuple[str, str]] = frozenset({
    # Site #1 (kernel SQL ~:129) REPAIRED via cherry-pick of 21cff1ec
    # (ultrareview-25 P2a) on 2026-05-01. Removed from baseline.
    #
    # Sites #3 + #4 in src/state/db.py:
    #   #3 (~:1559) position_current ALTER — DEFAULT retained; INSERT-side
    #      discipline guards; ALTER is dead code for fresh DBs.
    #   #4 (~:1581) ensemble_snapshots ALTER — table is write-frozen
    #      (zero runtime writers per operator review 2026-05-01); legacy
    #      rows are genuinely high-track per pre-HIGH/LOW-duality history.
    #      Closed as false-alarm in P1_2_DEFAULT_HIGH_REPAIR.md §Site #4.
    # Site #2 (init_schema ensemble_snapshots ~:515) and site #5 (data_version
    # ~:513) repaired 2026-05-01.
    ("temperature_metric", "src/state/db.py"),
})


# Per-(column, file) expected occurrence count baseline. Updated 2026-05-01:
# sites #2 (ensemble_snapshots temperature_metric) and #5 (ensemble_snapshots
# data_version) repaired. Sites #1 (kernel), #3, #4 (all position_current or
# deferred) remain — total 3 temperature_metric occurrences across 2 files.
_BASELINE_OCCURRENCE_COUNTS: dict[tuple[str, str], int] = {
    # Kernel SQL count repaired via 21cff1ec cherry-pick. Only db.py
    # remains: 2 occurrences (lines 1559 + 1581) per the operator-cleared
    # design rationale documented in _BASELINE_KNOWN_DEFAULTS above.
    ("temperature_metric", "src/state/db.py"): 2,
}


@dataclass(frozen=True)
class DefaultOccurrence:
    column: str
    file_rel: str
    line_no: int
    excerpt: str


def _scan_file(path: Path, columns: dict[str, str]) -> list[DefaultOccurrence]:
    if not path.exists():
        return []
    text = path.read_text()
    rel = str(path.relative_to(REPO_ROOT))
    found: list[DefaultOccurrence] = []
    for col in columns:
        # Pattern matches both DDL-as-string and SQL syntax variants:
        #   temperature_metric TEXT NOT NULL DEFAULT 'high'
        #   "ADD COLUMN temperature_metric TEXT NOT NULL DEFAULT 'high'"
        # We allow optional CHECK clause after.
        pattern = re.compile(
            rf"\b{re.escape(col)}\b\s+\w+(?:\s+NOT\s+NULL)?\s+DEFAULT\s+'[^']+'",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            excerpt = match.group(0).strip()
            found.append(
                DefaultOccurrence(
                    column=col, file_rel=rel, line_no=line_no, excerpt=excerpt
                )
            )
    return found


def collect_occurrences() -> list[DefaultOccurrence]:
    occurrences: list[DefaultOccurrence] = []
    for target in _SCAN_TARGETS:
        occurrences.extend(_scan_file(target, _GUARDED_IDENTITY_COLUMNS))
    return occurrences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", action="store_true", help="emit JSON report")
    args = parser.parse_args(argv)

    occurrences = collect_occurrences()
    by_pair: dict[tuple[str, str], list[DefaultOccurrence]] = {}
    for occ in occurrences:
        by_pair.setdefault((occ.column, occ.file_rel), []).append(occ)

    drift: list[str] = []
    for (col, file_rel), hits in by_pair.items():
        baseline_count = _BASELINE_OCCURRENCE_COUNTS.get((col, file_rel), 0)
        if (col, file_rel) not in _BASELINE_KNOWN_DEFAULTS:
            drift.append(
                f"NEW: {col!r} DEFAULT in {file_rel} ({len(hits)} hit(s)). "
                f"Reason this is dangerous: {_GUARDED_IDENTITY_COLUMNS[col]}"
            )
        elif len(hits) > baseline_count:
            drift.append(
                f"GROWN: {col!r} DEFAULT in {file_rel} now {len(hits)} hits "
                f"(baseline {baseline_count}). New occurrences:\n"
                + "\n".join(
                    f"    L{h.line_no}: {h.excerpt!r}" for h in hits[baseline_count:]
                )
            )

    # Repair-detection: a baseline pair that disappeared.
    found_pairs = set(by_pair.keys())
    repaired: list[str] = []
    for col, file_rel in _BASELINE_KNOWN_DEFAULTS:
        if (col, file_rel) not in found_pairs:
            repaired.append(f"  ({col!r}, {file_rel!r})")

    if args.json:
        report = {
            "ok": not drift and not repaired,
            "drift": drift,
            "repaired_baseline": repaired,
            "occurrences": [
                {
                    "column": o.column,
                    "file": o.file_rel,
                    "line": o.line_no,
                    "excerpt": o.excerpt,
                }
                for o in occurrences
            ],
        }
        print(json.dumps(report, indent=2))
        return 2 if (drift or repaired) else 0

    if drift:
        print(
            f"[check_identity_column_defaults] BLOCKED — {len(drift)} new "
            f"identity-column DEFAULT occurrence(s) beyond 2026-05-01 baseline:",
            file=sys.stderr,
        )
        for entry in drift:
            print(f"  {entry}", file=sys.stderr)
        print(
            "\nIdentity columns must be supplied explicitly at every INSERT. "
            "If you genuinely need a new DEFAULT, the repair must include: "
            "(a) a backfill plan for legacy rows, (b) an INSERT-side antibody "
            "test asserting the column is always supplied, and (c) operator "
            "approval to extend KNOWN_DEFAULTS in this script. See "
            "docs/operations/repo_review_2026-05-01/P1_2_DEFAULT_HIGH_REPAIR.md.",
            file=sys.stderr,
        )
        return 2

    if repaired:
        print(
            f"[check_identity_column_defaults] HOUSEKEEPING — baseline entries "
            f"once present now repaired:",
            file=sys.stderr,
        )
        for entry in repaired:
            print(entry, file=sys.stderr)
        print(
            "\nUpdate _BASELINE_KNOWN_DEFAULTS and _BASELINE_OCCURRENCE_COUNTS "
            "in this script (and the matching test) so the gate tightens.",
            file=sys.stderr,
        )
        return 2

    print(
        f"[check_identity_column_defaults] OK — {len(occurrences)} "
        f"identity-column DEFAULT occurrence(s) all match 2026-05-01 baseline."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
