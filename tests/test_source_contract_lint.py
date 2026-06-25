# Lifecycle: created=2026-05-24; last_reviewed=2026-05-24; last_reused=never
# Purpose: Tests for inter-registry coherence assertions incl. mx2t3/mx2t6 drift xfail.
# Reuse: Inspect docs/operations/current/plans/data_temporal_kernel/PLAN.md + the target module before relying on it.
# Created: 2026-05-24
# Last reused or audited: 2026-05-24
# Authority basis: docs/operations/current/plans/data_temporal_kernel/PLAN.md
"""Relationship tests for scripts/source_contract_lint.py — RED first, then GREEN.

Tests verify 4 inter-registry coherence assertions from PLAN §PR1. Each test
imports and calls the lint functions directly so we exercise the same logic
the CLI exposes.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
CALENDAR_PATH = REPO_ROOT / "config" / "source_release_calendar.yaml"
REGISTRY_PATH = REPO_ROOT / "architecture" / "data_sources_registry_2026_05_08.yaml"


def _load_calendar() -> list[dict]:
    with CALENDAR_PATH.open() as f:
        data = yaml.safe_load(f)
    return data["entries"]


def _load_registry_ids() -> set[str]:
    with REGISTRY_PATH.open() as f:
        data = yaml.safe_load(f)
    return {src["id"] for src in data.get("sources", [])}


# ---------------------------------------------------------------------------
# Test 3: calendar.source_id ⊆ data_sources_registry.sources[].id
# (Assertion 1 in lint)
# ---------------------------------------------------------------------------

def test_calendar_in_data_sources_registry() -> None:
    """Relationship: every calendar source_id must be present in data_sources_registry.

    If a source_id appears in the calendar but not in the registry, it is
    untracked — the lint must surface it as a drift warning.

    KNOWN drift today: calendar carries source_id='tigge', registry has 'tigge_mars'.
    This test asserts the lint DETECTS the drift (returns at least one finding
    for 'tigge'), not that it silently passes.
    """
    from scripts.source_contract_lint import run_assertion_1_calendar_source_in_registry

    findings = run_assertion_1_calendar_source_in_registry()
    # 'tigge' is in calendar but only 'tigge_mars' is in registry
    tigge_findings = [f for f in findings if "tigge" in f.get("source_id", "")]
    assert len(tigge_findings) >= 1, (
        "Expected lint to report 'tigge' (calendar) vs 'tigge_mars' (registry) drift, "
        f"but got findings: {findings}"
    )


# ---------------------------------------------------------------------------
# Test 5: backfill_only=true ⇒ live_authorization=false
# (Assertion 4 in lint)
# ---------------------------------------------------------------------------

def test_backfill_only_implies_not_live_authorization() -> None:
    """Relationship: calendar entries with backfill_only=true must not carry
    live_authorization=true. Verifies the lint detects violations.

    Today's calendar is clean on this; this test asserts zero violations.
    """
    from scripts.source_contract_lint import run_assertion_4_backfill_implies_not_live

    findings = run_assertion_4_backfill_implies_not_live()

    violations = [f for f in findings if f.get("level") == "violation"]
    assert len(violations) == 0, (
        f"Calendar has backfill_only=true entries with live_authorization=true: {violations}"
    )

    # Structural: every backfill_only entry must have live_authorization=false
    entries = _load_calendar()
    backfill_entries = [e for e in entries if e.get("backfill_only") is True]
    for entry in backfill_entries:
        assert entry.get("live_authorization") is False or entry.get("live_authorization") == False, (
            f"calendar_id={entry['calendar_id']!r}: backfill_only must have live_authorization=false"
        )


# ---------------------------------------------------------------------------
# Test 6: code data_version param matches SDK param; calendar drift is reported
# (Assertion 5 in lint) — XFAIL because calendar still says mx2t6 vs code mx2t3
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    reason="calendar param mx2t6 vs code mx2t3 drift — fixed in later calendar PR; see PLAN data_temporal_kernel",
    strict=False,
)
def test_code_data_version_param_matches_calendar_or_lint_flags_drift() -> None:
    """Relationship: the SDK fetch param (ecmwf_open_data.py TRACKS.open_data_param)
    and the code data_version param (snapshot_ingest_contract._ECMWF_OPENDATA_HIGH_DATA_VERSION)
    must align with the calendar's declared parameter field.

    KNOWN drift: calendar says parameter=mx2t6, code uses mx2t3.
    This test is marked xfail — it documents the drift as an antibody and will
    be activated (xfail removed) once the calendar-fix PR updates parameter to mx2t3.

    The lint must REPORT this mismatch as a drift warning (not silently pass).
    """
    from scripts.source_contract_lint import run_assertion_5_code_param_vs_calendar

    findings = run_assertion_5_code_param_vs_calendar()

    # If we reach here with no drift findings, the calendar has been fixed
    # and this test's xfail marker should be removed.
    drift_findings = [f for f in findings if "drift" in f.get("message", "").lower() or "mismatch" in f.get("message", "").lower()]
    assert len(drift_findings) == 0, (
        f"Calendar param drift still present (expected after calendar-fix PR): {drift_findings}"
    )


def test_assertion2_keys_on_roles_not_tier_no_false_drift() -> None:
    """F7: diagnostic-only primary-tier source (openmeteo_previous_runs) must NOT produce a
    drift finding — assertion 2 keys on allowed_roles (entry_primary), not tier=='primary'."""
    from scripts.source_contract_lint import run_assertion_2_primary_tier_implies_live_authorized

    findings = run_assertion_2_primary_tier_implies_live_authorized()
    omp = [f for f in findings if f.get("source_id") == "openmeteo_previous_runs"]
    assert omp == [], f"diagnostic-only source wrongly flagged: {omp}"


def test_assertion2_tigge_experimental_backfill_not_flagged() -> None:
    """G: TIGGE is entry_primary in its role menu but experimental + operator-gated +
    backfill_only — assertion 2 must NOT flag it as missing live_authorization."""
    from scripts.source_contract_lint import run_assertion_2_primary_tier_implies_live_authorized

    findings = run_assertion_2_primary_tier_implies_live_authorized()
    tigge = [f for f in findings if "tigge" in str(f.get("source_id", ""))]
    assert tigge == [], f"TIGGE wrongly flagged by assertion 2: {tigge}"
