# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: docs/authority/ARCHIVAL_RULES.md §Exemption Checks;
#   scripts/operations_package_inventory.py (PR-T4 topology brief)
"""
Tests for operations_package_inventory.py classification logic.

All three tests use SYNTHETIC SignalBundles injected into classify() so no
real packet paths appear in this source. This prevents self-poisoning via
the inbound-ref grep: if any real task_* slug appeared here, that packet
would be classified as LOAD_BEARING_DESPITE_AGE.

Tests:
  1. test_load_bearing_packet_not_archive_candidate
     — a packet with inbound refs MUST classify as LOAD_BEARING_DESPITE_AGE,
       never ARCHIVE_CANDIDATE, even if it is 120 days old.

  2. test_archivable_packet_requires_all_exemption_checks
     — a packet with zero inbound refs, no authority status, no runtime
       gating, and 90 days old classifies as ARCHIVE_CANDIDATE only when
       ALL preconditions pass.

  3. test_runtime_gating_evidence_classified_keep
     — a packet flagged is_runtime_gating_evidence=True classifies as
       RUNTIME_GATING_EVIDENCE (keep), not ARCHIVE_CANDIDATE, even with
       zero inbound refs and 90 days of age.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import helper — find classify() and SignalBundle from script path
# ---------------------------------------------------------------------------

def _import_module():
    """Import operations_package_inventory from installed path or repo worktree."""
    try:
        from scripts import operations_package_inventory as opi
        return opi
    except ImportError:
        sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
        import operations_package_inventory as opi  # type: ignore
        return opi


# ---------------------------------------------------------------------------
# Synthetic signal factories — use slug names that never match real task_*
# dirs so git grep doesn't falsely flag real packets as load-bearing.
# ---------------------------------------------------------------------------

def _synth_signals(opi, **overrides):
    """Build a synthetic SignalBundle with safe defaults, override any field."""
    defaults = dict(
        slug="task_synth_test_packet",   # synthetic — never matches a real dir
        last_modified_date=None,
        days_since_modified=90,
        inbound_ref_count=0,
        inbound_ref_files=[],
        authority_status=None,
        registry_lifecycle=None,
        has_authority_status_text=False,
        is_runtime_gating_evidence=False,
        is_monitoring_surface=False,
        proposed_new_home="",
    )
    defaults.update(overrides)
    return opi.SignalBundle(**defaults)


# ---------------------------------------------------------------------------
# Test 1: load-bearing packet must never be ARCHIVE_CANDIDATE
# ---------------------------------------------------------------------------

class TestLoadBearingPacketNotArchiveCandidate:
    """
    ARCHIVAL_RULES.md exemption check 4 (code reference grep):
    Any inbound ref → LOAD_BEARING_DESPITE_AGE.
    A 120-day-old packet with inbound refs must NOT be ARCHIVE_CANDIDATE.
    """

    def test_inbound_ref_forces_load_bearing(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=120,       # would otherwise be ARCHIVE_CANDIDATE
            inbound_ref_count=3,           # but it has refs
            inbound_ref_files=["architecture/some_manifest.yaml"],
        )
        record = opi.classify(signals)
        assert record.classification == "LOAD_BEARING_DESPITE_AGE", (
            f"Expected LOAD_BEARING_DESPITE_AGE but got {record.classification}. "
            "Packets with inbound refs must not be classified as ARCHIVE_CANDIDATE."
        )

    def test_load_bearing_is_never_archive_candidate(self):
        opi = _import_module()
        # Try various ages — all should be LOAD_BEARING when refs > 0
        for days in [5, 30, 60, 90, 180]:
            signals = _synth_signals(
                opi,
                days_since_modified=days,
                inbound_ref_count=1,
            )
            record = opi.classify(signals)
            assert record.classification != "ARCHIVE_CANDIDATE", (
                f"days={days}: ARCHIVE_CANDIDATE returned despite inbound_ref_count=1"
            )

    def test_authority_status_current_load_bearing_forces_load_bearing(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=120,
            inbound_ref_count=0,           # no inbound refs
            authority_status="CURRENT_LOAD_BEARING",
        )
        record = opi.classify(signals)
        assert record.classification == "LOAD_BEARING_DESPITE_AGE", (
            "authority_status=CURRENT_LOAD_BEARING must produce LOAD_BEARING_DESPITE_AGE"
        )


# ---------------------------------------------------------------------------
# Test 2: archive candidate requires ALL exemption checks to pass
# ---------------------------------------------------------------------------

class TestArchivablePacketRequiresAllExemptionChecks:
    """
    ARCHIVAL_RULES.md: ARCHIVE_CANDIDATE only when packet passes ALL 9 checks.
    In the script's classify() these correspond to: no inbound refs, no
    CURRENT_LOAD_BEARING authority status, no authority text, no runtime gating
    evidence, no active registry lifecycle, modified >60 days ago.
    """

    def test_zero_inbound_refs_old_packet_is_archive_candidate(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=90,
            inbound_ref_count=0,
            authority_status=None,
            registry_lifecycle=None,
            has_authority_status_text=False,
            is_runtime_gating_evidence=False,
            is_monitoring_surface=False,
        )
        record = opi.classify(signals)
        assert record.classification == "ARCHIVE_CANDIDATE", (
            f"Expected ARCHIVE_CANDIDATE for a clean old packet, got {record.classification}"
        )

    def test_single_inbound_ref_blocks_archival(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=90,
            inbound_ref_count=1,           # one reference blocks
        )
        record = opi.classify(signals)
        assert record.classification == "LOAD_BEARING_DESPITE_AGE"

    def test_active_registry_lifecycle_blocks_archival(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=90,
            inbound_ref_count=0,
            registry_lifecycle="active",   # active registry lifecycle → CURRENT_PACKAGE_INPUT
        )
        record = opi.classify(signals)
        assert record.classification != "ARCHIVE_CANDIDATE", (
            "registry_lifecycle=active must not produce ARCHIVE_CANDIDATE"
        )

    def test_packet_modified_within_30_days_is_not_archive_candidate(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=15,        # within active window
            inbound_ref_count=0,
        )
        record = opi.classify(signals)
        assert record.classification not in ("ARCHIVE_CANDIDATE", "LOAD_BEARING_DESPITE_AGE"), (
            "A recently modified packet should not be ARCHIVE_CANDIDATE"
        )

    def test_authority_text_blocks_archival(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=90,
            inbound_ref_count=0,
            has_authority_status_text=True,  # authority text → LOAD_BEARING
        )
        record = opi.classify(signals)
        assert record.classification == "LOAD_BEARING_DESPITE_AGE"


# ---------------------------------------------------------------------------
# Test 3: runtime gating evidence classifies as KEEP, not ARCHIVE_CANDIDATE
# ---------------------------------------------------------------------------

class TestRuntimeGatingEvidenceClassifiedKeep:
    """
    ARCHIVAL_RULES.md rationale: runtime-gating evidence (e.g. TIGGE ingest
    decision) must not be archived because it gates live system behaviour.
    Even with zero inbound refs and 90 days of age, the RUNTIME_GATING_EVIDENCE
    class fires and takes precedence over ARCHIVE_CANDIDATE.
    """

    def test_runtime_gating_evidence_is_not_archive_candidate(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=90,
            inbound_ref_count=0,
            is_runtime_gating_evidence=True,
        )
        record = opi.classify(signals)
        assert record.classification == "RUNTIME_GATING_EVIDENCE", (
            f"Expected RUNTIME_GATING_EVIDENCE, got {record.classification}. "
            "Runtime-gating evidence must never become ARCHIVE_CANDIDATE."
        )

    def test_runtime_gating_classification_is_keep_not_archive(self):
        opi = _import_module()
        signals = _synth_signals(
            opi,
            days_since_modified=200,       # very old — would normally be ARCHIVE_CANDIDATE
            inbound_ref_count=0,
            is_runtime_gating_evidence=True,
        )
        record = opi.classify(signals)
        assert record.classification not in ("ARCHIVE_CANDIDATE", "UNKNOWN_OPERATOR_DECISION"), (
            f"Runtime gating evidence must be kept (got {record.classification})"
        )

    def test_load_bearing_beats_runtime_gating(self):
        opi = _import_module()
        # LOAD_BEARING_DESPITE_AGE has higher precedence than RUNTIME_GATING_EVIDENCE
        signals = _synth_signals(
            opi,
            days_since_modified=90,
            inbound_ref_count=2,
            is_runtime_gating_evidence=True,
        )
        record = opi.classify(signals)
        assert record.classification == "LOAD_BEARING_DESPITE_AGE", (
            "LOAD_BEARING_DESPITE_AGE must beat RUNTIME_GATING_EVIDENCE in precedence"
        )
