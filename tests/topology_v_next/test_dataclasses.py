# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §2.1
"""
Unit tests for scripts/topology_v_next/dataclasses.py.

Covers: frozen-ness, to_dict round-trip, Intent enum coverage (universal +
zeus.* extensions), Severity ordering, FrictionPattern membership,
IssueRecord, DiagnosisEntry, AdmissionDecision, CoverageMap, CohortDecl,
BindingLayer instantiation.
"""
import dataclasses
import pytest

from scripts.topology_v_next.dataclasses import (
    AdmissionDecision,
    BindingLayer,
    CohortDecl,
    CoverageMap,
    DiagnosisEntry,
    FrictionPattern,
    Intent,
    IssueRecord,
    Severity,
)


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_all_values_present(self):
        names = {s.name for s in Severity}
        assert names == {"ADMIT", "ADVISORY", "SOFT_BLOCK", "HARD_STOP"}

    def test_str_value_matches_name(self):
        for s in Severity:
            assert s.value == s.name

    def test_is_str_subclass(self):
        assert isinstance(Severity.ADMIT, str)


# ---------------------------------------------------------------------------
# Intent
# ---------------------------------------------------------------------------

class TestIntent:
    UNIVERSAL_VALUES = {
        "plan_only", "create_new", "modify_existing", "refactor",
        "audit", "hygiene", "hotfix", "rebase_keepup", "other",
    }
    ZEUS_EXTENSIONS = {
        "zeus.settlement_followthrough",
        "zeus.calibration_update",
        "zeus.data_authority_receipt",
        "zeus.topology_tooling",
    }

    def test_universal_values_present(self):
        actual_values = {i.value for i in Intent}
        assert self.UNIVERSAL_VALUES.issubset(actual_values)

    def test_zeus_extensions_present(self):
        actual_values = {i.value for i in Intent}
        assert self.ZEUS_EXTENSIONS.issubset(actual_values)

    def test_zeus_extensions_namespaced(self):
        for value in self.ZEUS_EXTENSIONS:
            assert value.startswith("zeus."), f"Missing zeus. prefix: {value}"

    def test_is_str_subclass(self):
        assert isinstance(Intent.create_new, str)

    def test_total_count(self):
        # 9 universal + 4 zeus = 13 total
        assert len(Intent) == 13

    def test_resolve_by_value(self):
        assert Intent("create_new") is Intent.create_new
        assert Intent("zeus.calibration_update") is Intent.zeus_calibration_update


# ---------------------------------------------------------------------------
# FrictionPattern
# ---------------------------------------------------------------------------

class TestFrictionPattern:
    EXPECTED = {
        "LEXICAL_PROFILE_MISS",
        "UNION_SCOPE_EXPANSION",
        "SLICING_PRESSURE",
        "PHRASING_GAME_TAX",
        "INTENT_ENUM_TOO_NARROW",
        "CLOSED_PACKET_STILL_LOAD_BEARING",
        "ADVISORY_OUTPUT_INVISIBILITY",
    }

    def test_all_patterns_present(self):
        actual = {p.name for p in FrictionPattern}
        assert actual == self.EXPECTED

    def test_closed_packet_spelling(self):
        # Authoritative spelling per Universal §1.1 glossary
        assert FrictionPattern.CLOSED_PACKET_STILL_LOAD_BEARING.value == "CLOSED_PACKET_STILL_LOAD_BEARING"

    def test_is_str_subclass(self):
        assert isinstance(FrictionPattern.SLICING_PRESSURE, str)


# ---------------------------------------------------------------------------
# IssueRecord
# ---------------------------------------------------------------------------

class TestIssueRecord:
    def _make(self, **kwargs) -> IssueRecord:
        defaults = dict(
            code="test_code",
            path="src/some/file.py",
            severity=Severity.ADVISORY,
            message="test message",
            metadata={"k": "v"},
        )
        defaults.update(kwargs)
        return IssueRecord(**defaults)

    def test_frozen(self):
        rec = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            rec.code = "mutated"  # type: ignore[misc]

    def test_to_dict_fields(self):
        rec = self._make()
        d = rec.to_dict()
        assert d["code"] == "test_code"
        assert d["severity"] == "ADVISORY"
        assert d["path"] == "src/some/file.py"
        assert d["metadata"] == {"k": "v"}

    def test_to_dict_severity_is_string(self):
        rec = self._make(severity=Severity.HARD_STOP)
        assert isinstance(rec.to_dict()["severity"], str)

    def test_default_metadata_empty(self):
        # metadata has default_factory=dict but frozen dataclass doesn't mutate
        rec = IssueRecord(
            code="x", path="p", severity=Severity.ADMIT, message="m"
        )
        assert rec.metadata == {}


# ---------------------------------------------------------------------------
# DiagnosisEntry
# ---------------------------------------------------------------------------

class TestDiagnosisEntry:
    def _make(self) -> DiagnosisEntry:
        return DiagnosisEntry(
            pattern=FrictionPattern.LEXICAL_PROFILE_MISS,
            evidence="profile agent_runtime rejected due to phrase mismatch",
            resolution_path="Supply typed intent=modify_existing instead of relying on phrase",
        )

    def test_frozen(self):
        d = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.evidence = "mutated"  # type: ignore[misc]

    def test_to_dict(self):
        d = self._make()
        out = d.to_dict()
        assert out["pattern"] == "LEXICAL_PROFILE_MISS"
        assert "evidence" in out
        assert "resolution_path" in out

    def test_pattern_is_friction_pattern(self):
        d = self._make()
        assert isinstance(d.pattern, FrictionPattern)


# ---------------------------------------------------------------------------
# AdmissionDecision
# ---------------------------------------------------------------------------

class TestAdmissionDecision:
    def _make_issue(self) -> IssueRecord:
        return IssueRecord(
            code="coverage_gap",
            path="src/new.py",
            severity=Severity.ADVISORY,
            message="file not in any profile",
        )

    def _make(self, **kwargs) -> AdmissionDecision:
        defaults = dict(
            ok=True,
            profile_matched="agent_runtime",
            intent_class=Intent.create_new,
            severity=Severity.ADVISORY,
            issues=(self._make_issue(),),
            companion_files=("architecture/test_topology.yaml",),
            missing_phrases=(),
            closest_rejected_profile=None,
            friction_budget_used=1,
            diagnosis=None,
            kernel_alerts=(),
        )
        defaults.update(kwargs)
        return AdmissionDecision(**defaults)

    def test_frozen(self):
        dec = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            dec.ok = False  # type: ignore[misc]

    def test_to_dict_top_level_issues(self):
        """ADVISORY_OUTPUT_INVISIBILITY fix: issues at top level, not buried."""
        dec = self._make()
        d = dec.to_dict()
        assert "issues" in d
        assert len(d["issues"]) == 1
        assert d["issues"][0]["code"] == "coverage_gap"

    def test_to_dict_ok_true_with_issues_is_pass_with_conditions(self):
        """ok=True with non-empty issues is a pass-with-conditions, not clean."""
        dec = self._make(ok=True)
        d = dec.to_dict()
        assert d["ok"] is True
        assert len(d["issues"]) > 0

    def test_to_dict_severity_string(self):
        dec = self._make()
        assert isinstance(dec.to_dict()["severity"], str)

    def test_to_dict_intent_string(self):
        dec = self._make()
        assert isinstance(dec.to_dict()["intent_class"], str)

    def test_to_dict_no_diagnosis(self):
        dec = self._make(diagnosis=None)
        assert dec.to_dict()["diagnosis"] is None

    def test_to_dict_with_diagnosis(self):
        diag = DiagnosisEntry(
            pattern=FrictionPattern.UNION_SCOPE_EXPANSION,
            evidence="two profiles matched",
            resolution_path="declare a cohort",
        )
        dec = self._make(diagnosis=diag)
        d = dec.to_dict()
        assert d["diagnosis"] is not None
        assert d["diagnosis"]["pattern"] == "UNION_SCOPE_EXPANSION"

    def test_tuple_fields(self):
        """issues, companion_files, kernel_alerts must be tuples (not lists)."""
        dec = self._make()
        assert isinstance(dec.issues, tuple)
        assert isinstance(dec.companion_files, tuple)
        assert isinstance(dec.kernel_alerts, tuple)
        assert isinstance(dec.missing_phrases, tuple)

    def test_to_dict_json_serializable(self):
        """All values in to_dict must be JSON-serializable primitives."""
        import json
        dec = self._make()
        json.dumps(dec.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# CoverageMap
# ---------------------------------------------------------------------------

class TestCoverageMap:
    def test_instantiation(self):
        cm = CoverageMap(
            profiles={"agent_runtime": ("scripts/topology_doctor.py",)},
            orphaned=("tmp/**",),
            hard_stop_paths=("src/execution/**",),
        )
        assert "agent_runtime" in cm.profiles

    def test_frozen(self):
        cm = CoverageMap(profiles={}, orphaned=(), hard_stop_paths=())
        with pytest.raises(dataclasses.FrozenInstanceError):
            cm.orphaned = ("new",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# CohortDecl
# ---------------------------------------------------------------------------

class TestCohortDecl:
    def test_instantiation(self):
        c = CohortDecl(
            id="zeus.new_test_with_topology_registration",
            profile="test_suite",
            intent_classes=(Intent.create_new,),
            files=("tests/test_{new_module}.py", "architecture/test_topology.yaml"),
            description="new test + topology registration cohort",
        )
        assert c.profile == "test_suite"
        assert Intent.create_new in c.intent_classes

    def test_frozen(self):
        c = CohortDecl(
            id="x", profile="p", intent_classes=(), files=(), description="d"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.profile = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# BindingLayer
# ---------------------------------------------------------------------------

class TestBindingLayer:
    def _make(self) -> BindingLayer:
        cm = CoverageMap(
            profiles={"test_suite": ("tests/test_*.py",)},
            orphaned=("tmp/**",),
            hard_stop_paths=("src/execution/**",),
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
            cohorts=(),
            severity_overrides={"closed_packet_authority": Severity.SOFT_BLOCK},
            high_fanout_hints=(),
            artifact_authority_status={},
        )

    def test_instantiation(self):
        bl = self._make()
        assert bl.project_id == "zeus"
        assert len(bl.intent_extensions) == 4

    def test_frozen(self):
        bl = self._make()
        with pytest.raises(dataclasses.FrozenInstanceError):
            bl.project_id = "mutated"  # type: ignore[misc]

    def test_intent_extensions_are_intent_enum(self):
        bl = self._make()
        for ext in bl.intent_extensions:
            assert isinstance(ext, Intent)

    def test_severity_overrides_values_are_severity_enum(self):
        bl = self._make()
        for v in bl.severity_overrides.values():
            assert isinstance(v, Severity)
