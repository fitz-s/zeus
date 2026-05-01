# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: ultrareview25_remediation 2026-05-01 P1-8 +
#                  repo_review_2026-05-01 SYNTHESIS K-A (two-ring enforcement)
#                  + architect lane finding §INV-05 doc-only triple-confirmation
"""Pytest wrapper around scripts/check_invariant_test_citations.py.

This locks the 2026-05-01 baseline of 6 known-broken citations in
architecture/invariants.yaml and FAILS LOUDLY when any NEW drift appears.
Operator can shrink KNOWN_BROKEN incrementally as fixes land in invariants.yaml
(those edits require ARCH_PLAN_EVIDENCE per the pre-edit-architecture hook —
filed as separate operator action in
docs/operations/repo_review_2026-05-01/INVARIANT_CITATION_DRIFT_REPAIR.md).

Why test the citations
----------------------
The architect / critic-opus / test-engineer reviews triple-confirmed that
INV-05 cited a non-existent test for at least one full review cycle. This is
exactly the failure mode `scripts/check_invariant_test_citations.py` catches.
Without this test gating commits, the next stale citation lands silently.

Allow-list semantics
--------------------
KNOWN_BROKEN is the EXPECTED set of unresolved citations as of 2026-05-01.
- A regression that breaks a citation NOT in this set fails the test loudly.
- A repair that fixes a citation in this set fails the test (telling the
  operator to remove it from KNOWN_BROKEN). This catches accidental "double
  fixes" and keeps the list shrinking, never growing.

To add a new entry: don't. Add to invariants.yaml only; the test will catch
the new cite if it doesn't resolve, and the operator decides whether the cite
is real (remove from yaml) or aspirational (write the test, then re-cite).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Make the script importable as a module without polluting sys.path globally.
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import check_invariant_test_citations as citation_check  # type: ignore[import-not-found]
finally:
    sys.path.pop(0)


# Baseline locked 2026-05-01; all 6 original broken citations repaired 2026-05-01.
# This set must stay empty — any new broken citation is a regression.
KNOWN_BROKEN: frozenset[tuple[str, str]] = frozenset()


def test_invariant_citation_drift_within_known_baseline():
    """No NEW invariant citation drift may land beyond the 2026-05-01 baseline."""
    failures = citation_check.collect_failures()
    failed_set = frozenset((f.inv_id, f.citation) for f in failures)

    # Regressions: a citation broke that was OK in the baseline.
    new_drift = failed_set - KNOWN_BROKEN
    assert not new_drift, (
        "P1-8 regression: invariant citations broke beyond the 2026-05-01 "
        f"baseline. {len(new_drift)} new failure(s):\n  "
        + "\n  ".join(f"{inv_id} -> {cit}" for inv_id, cit in sorted(new_drift))
        + "\n\nFix the citation in architecture/invariants.yaml (requires "
        "ARCH_PLAN_EVIDENCE per pre-edit-architecture hook), or update the "
        "antibody at the cited path."
    )

    # Cleanups: a citation that was in the baseline now resolves. Don't let
    # KNOWN_BROKEN bit-rot — make the operator confirm the win.
    repaired = KNOWN_BROKEN - failed_set
    assert not repaired, (
        "P1-8 housekeeping: invariant citations once known broken now resolve. "
        "Remove the following entries from KNOWN_BROKEN in this test file so "
        "the baseline shrinks (fewer expected failures = stronger gate):\n  "
        + "\n  ".join(f"{inv_id} -> {cit}" for inv_id, cit in sorted(repaired))
    )


def test_no_NEW_fully_broken_invariant_beyond_known_baseline():
    """No NEW invariant may have all `tests:` cites broken at once. This is
    the INV-05 / INV-13 / INV-32 failure shape: the invariant is documented
    but has zero working antibody. The 2026-05-01 baseline already has 2
    invariants in this state (INV-13, INV-32 — operator action filed in
    INVARIANT_CITATION_DRIFT_REPAIR.md). Any NEW invariant joining that
    set must fail this test loudly so it can't slip in silently.
    """
    import yaml

    data = yaml.safe_load(citation_check.INVARIANTS_YAML.read_text()) or {}
    invariants = data.get("invariants") or []
    failed = citation_check.collect_failures()
    failed_by_inv: dict[str, set[str]] = {}
    for f in failed:
        failed_by_inv.setdefault(f.inv_id, set()).add(f.citation)

    # Invariants with all cites in KNOWN_BROKEN are known-and-tracked.
    known_inv_ids = {inv_id for inv_id, _ in KNOWN_BROKEN}

    new_fully_broken: list[str] = []
    for inv in invariants:
        inv_id = inv.get("id", "<unknown>")
        eb = inv.get("enforced_by") or {}
        cites = list(eb.get("tests") or ())
        if not cites:
            continue  # No `tests:` cite at all — separate concern (INV-03/07/08/10)
        broken = failed_by_inv.get(inv_id, set())
        if not all(c in broken for c in cites):
            continue
        if inv_id in known_inv_ids:
            continue  # Already on the operator's repair list.
        new_fully_broken.append(
            f"{inv_id}: every cite in `tests:` is broken — {sorted(cites)}"
        )

    assert not new_fully_broken, (
        "P1-8 hard regression: a NEW invariant has every `tests:` cite "
        "broken. This is INV-05-shaped doc-only enforcement and must not "
        "slip in silently:\n  "
        + "\n  ".join(new_fully_broken)
        + "\n\nFix the citation OR repair the antibody. If the invariant is "
        "intentionally aspirational, add `enforced_by: {tests: []}` and "
        "remove from this test's check via an explicit allow."
    )
