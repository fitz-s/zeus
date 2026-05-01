# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P2-1 +
#                  repo_review_2026-05-01 SYNTHESIS K-C +
#                  P1_3_TRUTH_AUTHORITY_AUDIT.md grammar §0
"""Pytest wrapper for scripts/check_contract_source_fields.py.

Locks the per-file count of bare `*source*: str` fields in src/contracts/
at the 2026-05-01 baseline (16 fields across 7 files; PR #35 P2 review
expanded the regex to also catch the `Optional[str]` annotation form).
New file gaining its first such field, or any file's count growing,
fails the gate.

Why
---
The 2026-05-01 P1-3 audit (`docs/operations/repo_review_2026-05-01/
P1_3_TRUTH_AUTHORITY_AUDIT.md` §0) classified every existing
`*source*: str` field in src/contracts/ as an internal label (not an
external-authority value). The global epistemic_scaffold rule
(`~/CLAUDE.md` "every external-authority value needs source/authority
fields") therefore doesn't fire for any of them today.

The forward-looking risk: a future agent adds a NEW `source: str` that
DOES carry external-authority semantics (e.g., a vendor URL, a
third-party fee timestamp), and provenance evaporates at the dataclass
field boundary the moment the row is constructed.

This test makes that case impossible-to-land-silently:
- New file with first `*source*:str` → fail; audit per-field
- Existing file count growing → fail; audit the new occurrence(s)
- Repaired (wrapped in ExternalParameter[T]) → fail until baseline
  shrinks (forces tightening, not relaxing)

The audit decision per new field:
- Internal label → bump baseline, leave as bare str
- External authority → wrap in ExternalParameter[T] /
  InheritedArtifact[T] before merging

Same forcing pattern as `tests/test_dynamic_sql_baseline.py` and
`tests/test_identity_column_defaults.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import check_contract_source_fields as scanner  # type: ignore[import-not-found]
finally:
    sys.path.pop(0)


def test_no_bare_source_str_drift_beyond_2026_05_01_baseline():
    """No NEW bare `*source*: str` field in src/contracts/ may land beyond
    the 2026-05-01 baseline. Bumping a per-file count requires explicitly
    editing `scripts/check_contract_source_fields.py:_BASELINE_PER_FILE`
    AND a P1_3-style audit confirming the new field is an internal label
    (not external-authority data that should be wrapped in
    ExternalParameter[T]).
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
            f"  NEW file: {f} has {actual[f]} bare *source*:str field(s)"
        )
    for f in grown:
        drift_messages.append(
            f"  GROWN: {f} now {actual[f]} (baseline {baseline[f]})"
        )
    for f in shrunk:
        drift_messages.append(
            f"  SHRUNK: {f} now {actual[f]} (baseline {baseline[f]}) — "
            "if you wrapped a field, update the baseline"
        )
    for f in repaired:
        drift_messages.append(
            f"  REPAIRED: {f} no longer has bare source-str fields "
            f"(baseline {baseline[f]}) — remove from _BASELINE_PER_FILE"
        )

    assert not drift_messages, (
        "P2-1 contract-source-field drift beyond 2026-05-01 baseline. "
        "Each entry needs the audit per the criteria in the scanner "
        "module docstring (internal label → bump baseline; external "
        "authority → wrap in ExternalParameter[T] before merging):\n"
        + "\n".join(drift_messages)
    )


def test_baseline_total_matches_sum_of_per_file():
    """Sanity: the scanner total must equal sum of per-file baselines."""
    actual = scanner.collect_per_file_counts()
    baseline_total = sum(scanner._BASELINE_PER_FILE.values())
    actual_total = sum(actual.values())
    assert actual_total == baseline_total, (
        f"Baseline total ({baseline_total}) does not match scanner total "
        f"({actual_total}). Investigate via "
        "`python3 scripts/check_contract_source_fields.py --json`."
    )
