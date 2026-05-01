# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P2 +
#                  repo_review_2026-05-01 security.md §10
"""Pytest wrapper for scripts/check_dynamic_sql.py.

Locks the per-file count of f-string SQL interpolation sites (`cursor
.execute(f"...")` and friends) at the 2026-05-01 baseline of 108 sites
across 24 files. Any drift triggers a test failure with a per-file
breakdown.

Why
---
The 108 current sites all bind identifiers (table / column names) from
internal whitelists — `grep -rE 'execute\\(\\s*f"' src/` shows every
f-string is followed by a `{var}` whose binding traces to a hardcoded
constant or schema-introspection result, not user input. But the codebase
has no enforcement of this property; one future refactor that pipes a
request-param string through an f-string is enough for SQL injection.

This test makes new dynamic SQL sites IMPOSSIBLE TO LAND SILENTLY:

- A new file with f-string SQL → fails the gate → audit at PR review time
- An existing file gaining a new f-string SQL site → same outcome
- A removed site that drops a baseline file's count → fails (operator
  must update the script's baseline so the gate tightens, never relaxes)

The audit decision per new site:
- If safe (identifier from closed internal whitelist) → bump baseline
- If unsafe → refactor to parameterized binding before merging

Same forcing pattern as `tests/test_identity_column_defaults.py` and
`tests/test_invariant_citations.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import check_dynamic_sql as scanner  # type: ignore[import-not-found]
finally:
    sys.path.pop(0)


def test_no_dynamic_sql_drift_beyond_2026_05_01_baseline():
    """No NEW dynamic-SQL site may land beyond the 2026-05-01 baseline.
    Bumping a per-file count requires explicitly editing
    `scripts/check_dynamic_sql.py:_BASELINE_PER_FILE` AND a security
    audit confirming the new site's interpolated identifier is from a
    closed internal whitelist (NOT user-controlled input).
    """
    actual = scanner.collect_per_file_counts()
    baseline = scanner._BASELINE_PER_FILE

    new_files = sorted(set(actual) - set(baseline))
    grown = sorted(
        f for f in actual if f in baseline and actual[f] > baseline[f]
    )
    shrunk = sorted(
        f for f in actual if f in baseline and actual[f] < baseline[f]
    )
    repaired = sorted(set(baseline) - set(actual))

    drift_messages: list[str] = []
    for f in new_files:
        drift_messages.append(
            f"  NEW file: {f} has {actual[f]} dynamic-SQL site(s)"
        )
    for f in grown:
        drift_messages.append(
            f"  GROWN: {f} now {actual[f]} sites (baseline {baseline[f]})"
        )
    for f in shrunk:
        drift_messages.append(
            f"  SHRUNK: {f} now {actual[f]} sites (baseline {baseline[f]}) — "
            "if you fixed a site, update the baseline so the gate tightens"
        )
    for f in repaired:
        drift_messages.append(
            f"  REPAIRED: {f} no longer has dynamic SQL (baseline {baseline[f]}) — "
            "remove from _BASELINE_PER_FILE"
        )

    assert not drift_messages, (
        "P2 dynamic-SQL drift beyond 2026-05-01 baseline. Each entry "
        "requires audit per the criteria in `scripts/check_dynamic_sql.py` "
        "module docstring (interpolated identifier from internal whitelist "
        "→ bump baseline; user-controlled → refactor to parameterized "
        "binding before merging):\n" + "\n".join(drift_messages)
    )


def test_dynamic_sql_total_count_matches_sum_of_per_file():
    """Sanity: `sum(_BASELINE_PER_FILE.values()) == total reported by the
    scanner`. Catches a typo that would silently drop a file from baseline.
    """
    actual = scanner.collect_per_file_counts()
    baseline_total = sum(scanner._BASELINE_PER_FILE.values())
    actual_total = sum(actual.values())
    assert actual_total == baseline_total, (
        f"Baseline total ({baseline_total}) does not match scanner total "
        f"({actual_total}). The scanner is reporting drift; investigate "
        "via `python3 scripts/check_dynamic_sql.py --json`."
    )
