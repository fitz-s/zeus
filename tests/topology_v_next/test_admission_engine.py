# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1
"""
Unit tests for scripts/topology_v_next/admission_engine.py.

Covers: full §4 algorithm trace per step; HARD_STOP short-circuit;
AdmissionDecision struct field population; friction_budget_used defaulting
when no friction_state; _check_authority_status emits authority_status_stale
when TTL exceeded; anti-sidecar signature checks.
"""
from __future__ import annotations

import inspect
import time

import pytest

from scripts.topology_v_next.admission_engine import admit, _check_authority_status
from scripts.topology_v_next.dataclasses import (
    AdmissionDecision,
    BindingLayer,
    CohortDecl,
    CoverageMap,
    Intent,
    IssueRecord,
    Severity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_binding(
    profiles: dict[str, tuple[str, ...]] | None = None,
    cohorts: tuple[CohortDecl, ...] = (),
    hard_stop_paths: tuple[str, ...] = ("src/execution/**", "src/venue/**"),
    artifact_authority_status: dict | None = None,
    severity_overrides: dict | None = None,
) -> BindingLayer:
    cm = CoverageMap(
        profiles=profiles or {
            "agent_runtime": (
                "scripts/topology_doctor.py",
                "scripts/topology_doctor_digest.py",
                "architecture/task_boot_profiles.yaml",
                "architecture/admission_severity.yaml",
                "architecture/test_topology.yaml",
                "docs/operations/AGENTS.md",
            ),
            "test_suite": (
                "tests/test_*.py",
                "tests/topology_v_next/**",
                "tests/fixtures/**",
            ),
        },
        orphaned=("tmp/**", "*.bak.*", ".gitignore"),
        hard_stop_paths=hard_stop_paths,
    )
    return BindingLayer(
        project_id="zeus",
        intent_extensions=(
            Intent.zeus_settlement_followthrough,
            Intent.zeus_calibration_update,
            Intent.zeus_data_authority_receipt,
            Intent.zeus_topology_tooling,
        ),
        coverage_map=cm,
        cohorts=cohorts,
        severity_overrides=severity_overrides or {},
        high_fanout_hints=(),
        artifact_authority_status=artifact_authority_status or {},
    )


STUB_BINDING = _make_binding()


# ---------------------------------------------------------------------------
# Tests: admit() signature (anti-sidecar)
# ---------------------------------------------------------------------------

class TestAdmitSignature:
    def test_no_task_parameter(self):
        sig = inspect.signature(admit)
        assert "task" not in sig.parameters
        assert "task_phrase" not in sig.parameters
        assert "phrase" not in sig.parameters

    def test_hint_is_positional_with_default(self):
        sig = inspect.signature(admit)
        assert "hint" in sig.parameters
        assert sig.parameters["hint"].default == ""

    def test_binding_is_keyword_only(self):
        sig = inspect.signature(admit)
        p = sig.parameters["binding"]
        assert p.kind == inspect.Parameter.KEYWORD_ONLY

    def test_friction_state_is_keyword_only(self):
        sig = inspect.signature(admit)
        p = sig.parameters["friction_state"]
        assert p.kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Tests: basic admit() paths
# ---------------------------------------------------------------------------

class TestAdmitBasic:
    def test_returns_admission_decision(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert isinstance(result, AdmissionDecision)

    def test_clean_single_profile_admits(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert result.ok is True
        assert result.profile_matched == "agent_runtime"
        assert result.severity in (Severity.ADMIT, Severity.ADVISORY)

    def test_intent_class_populated(self):
        result = admit(
            intent=Intent.create_new,
            files=["tests/test_foo.py"],
            binding=STUB_BINDING,
        )
        assert result.intent_class == Intent.create_new

    def test_friction_budget_defaults_to_1_when_no_state(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
            friction_state=None,
        )
        assert result.friction_budget_used == 1

    def test_friction_budget_increments_from_state(self):
        state = {"attempts_this_session": 2}
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
            friction_state=state,
        )
        assert result.friction_budget_used == 3
        assert state["attempts_this_session"] == 3

    def test_to_dict_is_json_serializable(self):
        import json
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        json.dumps(result.to_dict())  # must not raise

    def test_kernel_alerts_field_is_tuple(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert isinstance(result.kernel_alerts, tuple)

    def test_issues_field_is_tuple(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert isinstance(result.issues, tuple)

    def test_companion_files_field_is_tuple(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert isinstance(result.companion_files, tuple)


# ---------------------------------------------------------------------------
# Tests: HARD_STOP short-circuit
# ---------------------------------------------------------------------------

class TestHardStopShortCircuit:
    def test_hard_stop_file_ok_is_false(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/execution/orders.py"],
            binding=STUB_BINDING,
        )
        assert result.ok is False

    def test_hard_stop_severity(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/execution/orders.py"],
            binding=STUB_BINDING,
        )
        assert result.severity == Severity.HARD_STOP

    def test_hard_stop_profile_matched_is_none(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/execution/orders.py"],
            binding=STUB_BINDING,
        )
        assert result.profile_matched is None

    def test_hard_stop_kernel_alerts_populated(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["src/execution/orders.py"],
            binding=STUB_BINDING,
        )
        assert len(result.kernel_alerts) >= 1
        assert all(a.severity == Severity.HARD_STOP for a in result.kernel_alerts)

    def test_mixed_files_with_hard_stop_blocks(self):
        """Even one hard-stop file in a set blocks the whole admission."""
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py", "src/venue/client.py"],
            binding=STUB_BINDING,
        )
        assert result.ok is False
        assert result.severity == Severity.HARD_STOP


# ---------------------------------------------------------------------------
# Tests: coverage gap advisory
# ---------------------------------------------------------------------------

class TestCoverageGap:
    def test_unknown_file_gets_coverage_gap_advisory(self):
        result = admit(
            intent=Intent.create_new,
            files=["src/new_module.py"],
            binding=STUB_BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "coverage_gap" in codes

    def test_coverage_gap_does_not_block_when_no_hard_stop(self):
        """coverage_gap is ADVISORY by default; ok=True permitted."""
        result = admit(
            intent=Intent.create_new,
            files=["scripts/topology_doctor.py", "src/new_module.py"],
            binding=STUB_BINDING,
        )
        # composition may soft-block due to multi-profile; but not HARD_STOP
        assert result.severity != Severity.HARD_STOP


# ---------------------------------------------------------------------------
# Tests: intent handling
# ---------------------------------------------------------------------------

class TestIntentHandling:
    def test_none_intent_gets_advisory(self):
        result = admit(
            intent=None,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "intent_unspecified" in codes
        assert result.intent_class == Intent.other

    def test_unknown_string_intent_gets_advisory(self):
        result = admit(
            intent="nonexistent_intent",
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "intent_enum_unknown" in codes
        assert result.intent_class == Intent.other

    def test_valid_string_intent_resolves(self):
        result = admit(
            intent="modify_existing",
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert result.intent_class == Intent.modify_existing

    def test_typed_intent_passes_through(self):
        result = admit(
            intent=Intent.refactor,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        assert result.intent_class == Intent.refactor


# ---------------------------------------------------------------------------
# Tests: multi-profile composition conflict
# ---------------------------------------------------------------------------

class TestCompositionConflict:
    def test_multi_profile_without_cohort_soft_blocks(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py", "tests/test_foo.py"],
            binding=STUB_BINDING,
        )
        codes = {i.code for i in result.issues}
        assert "composition_conflict" in codes
        assert result.profile_matched is None

    def test_cohort_resolves_multi_profile(self):
        cohort = CohortDecl(
            id="zeus.new_test_with_topology_registration",
            profile="test_suite",
            intent_classes=(Intent.create_new,),
            files=(
                "tests/test_{new_module}.py",
                "architecture/test_topology.yaml",
            ),
            description="new test + topology yaml companion",
        )
        binding = _make_binding(cohorts=(cohort,))
        result = admit(
            intent=Intent.create_new,
            files=["tests/test_calibration.py", "architecture/test_topology.yaml"],
            binding=binding,
        )
        assert result.profile_matched == "test_suite"
        assert result.ok is True


# ---------------------------------------------------------------------------
# Tests: _check_authority_status helper
# ---------------------------------------------------------------------------

class TestCheckAuthorityStatus:
    def _stale_ts(self, days_ago: int) -> str:
        """Return an ISO date string that is days_ago days in the past."""
        import datetime
        d = datetime.date.today() - datetime.timedelta(days=days_ago)
        return d.strftime("%Y-%m-%d")

    def test_empty_status_dict_returns_no_issues(self):
        issues = _check_authority_status(["scripts/foo.py"], {})
        assert issues == []

    def test_file_not_in_status_no_issue(self):
        issues = _check_authority_status(
            ["scripts/foo.py"],
            {"architecture/other.yaml": {"status": "CURRENT", "last_confirmed": "2026-01-01", "confirmation_ttl_days": 30}},
        )
        assert issues == []

    def test_current_historical_emits_closed_packet_advisory(self):
        issues = _check_authority_status(
            ["architecture/old_spec.yaml"],
            {
                "architecture/old_spec.yaml": {
                    "status": "CURRENT_HISTORICAL",
                    "last_confirmed": "2026-01-01",
                    "confirmation_ttl_days": 365,
                }
            },
        )
        codes = {i.code for i in issues}
        assert "closed_packet_authority" in codes
        assert all(i.severity == Severity.ADVISORY for i in issues)

    def test_stale_ttl_emits_authority_status_stale(self):
        issues = _check_authority_status(
            ["architecture/spec.yaml"],
            {
                "architecture/spec.yaml": {
                    "status": "CURRENT",
                    "last_confirmed": self._stale_ts(60),
                    "confirmation_ttl_days": 30,
                }
            },
        )
        codes = {i.code for i in issues}
        assert "authority_status_stale" in codes

    def test_fresh_ttl_no_stale_issue(self):
        issues = _check_authority_status(
            ["architecture/spec.yaml"],
            {
                "architecture/spec.yaml": {
                    "status": "CURRENT",
                    "last_confirmed": self._stale_ts(5),
                    "confirmation_ttl_days": 30,
                }
            },
        )
        codes = {i.code for i in issues}
        assert "authority_status_stale" not in codes

    def test_both_historical_and_stale_emits_both(self):
        issues = _check_authority_status(
            ["architecture/old.yaml"],
            {
                "architecture/old.yaml": {
                    "status": "CURRENT_HISTORICAL",
                    "last_confirmed": self._stale_ts(60),
                    "confirmation_ttl_days": 30,
                }
            },
        )
        codes = {i.code for i in issues}
        assert "closed_packet_authority" in codes
        assert "authority_status_stale" in codes

    def test_authority_check_via_admit(self):
        """End-to-end: admit() surfaces _check_authority_status issues."""
        binding = _make_binding(
            artifact_authority_status={
                "architecture/test_topology.yaml": {
                    "status": "CURRENT_HISTORICAL",
                    "last_confirmed": "2026-01-01",
                    "confirmation_ttl_days": 365,
                }
            }
        )
        result = admit(
            intent=Intent.modify_existing,
            files=["architecture/test_topology.yaml"],
            binding=binding,
        )
        codes = {i.code for i in result.issues}
        assert "closed_packet_authority" in codes


# ---------------------------------------------------------------------------
# Tests: hint only affects closest_rejected_profile
# ---------------------------------------------------------------------------

class TestHintIsolation:
    def test_different_hints_same_profile_matched(self):
        """Profile resolution must be identical regardless of hint."""
        binding = STUB_BINDING
        files = ["scripts/topology_doctor.py"]

        r1 = admit(intent=Intent.modify_existing, files=files, hint="topology doc fix", binding=binding)
        r2 = admit(intent=Intent.modify_existing, files=files, hint="something completely different", binding=binding)

        assert r1.profile_matched == r2.profile_matched
        assert r1.ok == r2.ok

    def test_hint_does_not_appear_in_issues(self):
        """Hint string must NOT appear as an issue code or influence issue list."""
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            hint="magic_profile_override",
            binding=STUB_BINDING,
        )
        for issue in result.issues:
            assert "magic_profile_override" not in issue.code
            assert "magic_profile_override" not in issue.message


# ---------------------------------------------------------------------------
# Tests: to_dict() output shape
# ---------------------------------------------------------------------------

class TestToDictShape:
    def test_all_required_fields_present(self):
        result = admit(
            intent=Intent.modify_existing,
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        d = result.to_dict()
        required = {
            "ok", "profile_matched", "intent_class", "severity",
            "issues", "companion_files", "missing_phrases",
            "closest_rejected_profile", "friction_budget_used",
            "diagnosis", "kernel_alerts",
        }
        assert required.issubset(set(d.keys()))

    def test_ok_true_with_issues_at_top_level(self):
        """ADVISORY_OUTPUT_INVISIBILITY fix: issues visible even when ok=True."""
        result = admit(
            intent=None,  # will generate intent_unspecified ADVISORY
            files=["scripts/topology_doctor.py"],
            binding=STUB_BINDING,
        )
        d = result.to_dict()
        assert isinstance(d["issues"], list)
        # ok may be True (ADVISORY doesn't block) but issues must be visible
        if d["ok"]:
            assert len(d["issues"]) > 0  # intent_unspecified advisory present
