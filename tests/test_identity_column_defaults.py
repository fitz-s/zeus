# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P1-2 +
#                  repo_review_2026-05-01 SYNTHESIS K-D + INV-14
"""Pytest wrapper for scripts/check_identity_column_defaults.py.

Why
---
INV-14 says identity columns (`temperature_metric`, `data_version`,
`physical_quantity`, ...) are part of canonical row identity. A
`DEFAULT 'X'` on a bivalent or versioned identity column silently routes
missing-value INSERTs to one half of the identity, producing rows that
look valid but carry the wrong metric. The 2026-05-01 review (architect
K-D) catalogued 4 `DEFAULT 'high'` sites + 1 `DEFAULT 'v1'` site as the
2026-05-01 baseline.

This test fails if any NEW identity-column DEFAULT appears beyond that
baseline. It also fails when a baseline entry is repaired (operator
must update the script's `_BASELINE_*` constants in lockstep so the
gate tightens, never relaxes silently).

The actual DDL repair (drop the DEFAULT, add INSERT discipline,
backfill legacy rows) requires editing
`architecture/2026_04_02_architecture_kernel.sql` under
ARCH_PLAN_EVIDENCE — filed in
`docs/operations/repo_review_2026-05-01/P1_2_DEFAULT_HIGH_REPAIR.md`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import check_identity_column_defaults as scanner  # type: ignore[import-not-found]
finally:
    sys.path.pop(0)


def test_no_new_identity_column_defaults_beyond_2026_05_01_baseline():
    """No NEW `DEFAULT 'X'` may land on a guarded identity column beyond
    the 2026-05-01 baseline. Per-(column, file) occurrence counts must
    not grow either — that catches a 5th DEFAULT 'high' added to the same
    file as a hidden runtime DDL.

    To repair (after operator unblocks ARCH_PLAN_EVIDENCE):
      1. Edit DDL to drop the DEFAULT.
      2. Add an INSERT-side antibody asserting the column is always
         supplied at write time.
      3. Update `_BASELINE_KNOWN_DEFAULTS` and
         `_BASELINE_OCCURRENCE_COUNTS` in
         `scripts/check_identity_column_defaults.py` so the gate tightens.
    """
    occurrences = scanner.collect_occurrences()
    by_pair: dict[tuple[str, str], list[scanner.DefaultOccurrence]] = {}
    for occ in occurrences:
        by_pair.setdefault((occ.column, occ.file_rel), []).append(occ)

    new_pairs: list[str] = []
    grown_pairs: list[str] = []
    for (col, file_rel), hits in by_pair.items():
        if (col, file_rel) not in scanner._BASELINE_KNOWN_DEFAULTS:
            new_pairs.append(
                f"  ({col!r}, {file_rel!r})  [{len(hits)} hit(s)]\n"
                f"    Why dangerous: {scanner._GUARDED_IDENTITY_COLUMNS[col]}"
            )
            continue
        baseline_count = scanner._BASELINE_OCCURRENCE_COUNTS.get(
            (col, file_rel), 0
        )
        if len(hits) > baseline_count:
            new_lines = "\n".join(
                f"      L{h.line_no}: {h.excerpt!r}"
                for h in hits[baseline_count:]
            )
            grown_pairs.append(
                f"  ({col!r}, {file_rel!r})  baseline={baseline_count}, "
                f"now={len(hits)}\n{new_lines}"
            )

    assert not new_pairs and not grown_pairs, (
        "P1-2 regression: identity-column DEFAULT(s) added beyond 2026-05-01 "
        "baseline.\n\n"
        + ("New (column, file) pairs:\n" + "\n".join(new_pairs) + "\n\n" if new_pairs else "")
        + ("Grown counts on existing pairs:\n" + "\n".join(grown_pairs) + "\n\n" if grown_pairs else "")
        + "Identity columns must be supplied explicitly at every INSERT. "
        "See docs/operations/repo_review_2026-05-01/P1_2_DEFAULT_HIGH_REPAIR.md "
        "for the operator repair path."
    )

    # Pair-positive: ensure baseline pairs are still present. If the operator
    # repaired one (DEFAULT removed), this test catches the lingering
    # baseline entry so the script's KNOWN_DEFAULTS shrinks in lockstep.
    found_pairs = set(by_pair.keys())
    repaired_but_still_in_baseline = [
        f"  ({col!r}, {file_rel!r})"
        for col, file_rel in scanner._BASELINE_KNOWN_DEFAULTS
        if (col, file_rel) not in found_pairs
    ]
    assert not repaired_but_still_in_baseline, (
        "P1-2 housekeeping: identity-column DEFAULT(s) once known are now "
        "GONE — congrats on the repair! Now remove these from "
        "scripts/check_identity_column_defaults.py:_BASELINE_KNOWN_DEFAULTS "
        "and _BASELINE_OCCURRENCE_COUNTS so the gate tightens:\n"
        + "\n".join(repaired_but_still_in_baseline)
    )


def test_identity_column_default_scanner_finds_all_known_baseline_sites():
    """Sanity: the scanner must report exactly the 2026-05-01 baseline.
    If the regex stops matching one of the historical sites, this fails
    early (rather than the per-pair test silently passing while drift
    detection is broken).
    """
    occurrences = scanner.collect_occurrences()
    found_pairs = {(o.column, o.file_rel) for o in occurrences}
    assert found_pairs == scanner._BASELINE_KNOWN_DEFAULTS, (
        "Scanner regex drift: the set of (column, file) pairs reported "
        f"({sorted(found_pairs)}) does not match the recorded baseline "
        f"({sorted(scanner._BASELINE_KNOWN_DEFAULTS)}). Either the scanner "
        "regex broke (regex too tight or DDL syntax changed) or a baseline "
        "entry was silently removed. Re-audit before adjusting either side."
    )

    total_count = len(occurrences)
    expected_total = sum(scanner._BASELINE_OCCURRENCE_COUNTS.values())
    assert total_count == expected_total, (
        f"Scanner total occurrence count ({total_count}) does not match "
        f"sum of baseline per-pair counts ({expected_total}). Either a new "
        "DEFAULT landed in an existing file or one was repaired — "
        "investigate via `python3 scripts/check_identity_column_defaults.py "
        "--json`."
    )
