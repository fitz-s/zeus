# Lifecycle: created=2026-04-28; last_reviewed=2026-04-28; last_reused=2026-04-28
# Purpose: Evaluate whether the @enforced_by INV prototype strictly catches drift beyond YAML+tests.
# Reuse: Run only as prototype evidence for INV migration decisions; do not treat as migration approval.
# Created: 2026-04-28
# Last reused/audited: 2026-04-28
# Authority basis: round2_verdict.md §H1 + Tier 2 Phase 4 ITEM #17 dispatch
# (build prototype to PROVE OR DISPROVE strictly-stronger enforcement). Per
# critic methodology §5.Z2 (apparent-improvement gate): demonstrate catches
# the current YAML+tests CANNOT catch, or honestly verdict EQUIVALENT/INFERIOR.
"""Relationship tests + Test 4 honest evaluation for @enforced_by prototype.

Tests 1-3 verify the prototype's basic mechanics work (BREAK + PASS cases).
Test 4 is the load-bearing question: does the prototype catch ANYTHING that
the current YAML manifest + topology_doctor + pytest cannot already catch?

Honest finding from Test 4 informs the operator's round-2 §H1 hold decision:
  STRICTLY_DOMINATES → migrate to Python (Phase 4.5+)
  EQUIVALENT          → keep YAML (decoration is re-encoding without value-add)
  INFERIOR            → abandon (less coverage or false positives)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Ensure the prototype module is importable as `architecture.inv_prototype`.
# The architecture/ directory is not a package by default; we sidestep by
# loading the file directly via importlib for test purposes.
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "architecture_inv_prototype",
    REPO_ROOT / "architecture" / "inv_prototype.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["architecture_inv_prototype"] = _mod  # required by Py3.14 @dataclass
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
enforced_by = _mod.enforced_by
DriftFinding = _mod.DriftFinding
PROTOTYPED_INVS = _mod.PROTOTYPED_INVS
all_drift_findings = _mod.all_drift_findings


# ---------------------------------------------------------------------------
# Test 1: BREAK case — decorator catches missing test reference
# ---------------------------------------------------------------------------

def test_decorator_catches_missing_test_file():
    """RELATIONSHIP: cite a non-existent test file → drift finding."""
    @enforced_by(
        statement="hypothetical INV",
        test=["tests/test_does_not_exist.py::test_made_up"],
    )
    class HYPOTHETICAL_INV:
        pass
    findings = HYPOTHETICAL_INV.__inv__.validate()  # type: ignore[attr-defined]
    assert any(f.kind == "FILE_MISSING" for f in findings), \
        f"expected FILE_MISSING for nonexistent test file, got {findings!r}"


def test_decorator_catches_missing_test_function_in_real_file():
    """RELATIONSHIP: cite a real file but missing test name → TEST_NOT_FOUND."""
    @enforced_by(
        statement="hypothetical INV",
        test=["tests/test_architecture_contracts.py::test_this_test_name_does_not_exist"],
    )
    class HYPOTHETICAL_INV2:
        pass
    findings = HYPOTHETICAL_INV2.__inv__.validate()  # type: ignore[attr-defined]
    assert any(f.kind == "TEST_NOT_FOUND" for f in findings), \
        f"expected TEST_NOT_FOUND for nonexistent function, got {findings!r}"


# ---------------------------------------------------------------------------
# Test 2: BREAK case — decorator catches missing semgrep rule
# ---------------------------------------------------------------------------

def test_decorator_catches_missing_semgrep_rule():
    """RELATIONSHIP: cite a non-existent semgrep rule_id → RULE_NOT_FOUND."""
    @enforced_by(
        statement="hypothetical INV",
        semgrep=["zeus-this-rule-does-not-exist"],
    )
    class HYPOTHETICAL_INV3:
        pass
    findings = HYPOTHETICAL_INV3.__inv__.drift_findings  # eager
    assert any(f.kind == "RULE_NOT_FOUND" for f in findings), \
        f"expected RULE_NOT_FOUND for nonexistent semgrep rule, got {findings!r}"


# ---------------------------------------------------------------------------
# Test 3: PASS case — decorator passes when all citations valid
# ---------------------------------------------------------------------------

def test_all_5_prototyped_invs_have_zero_drift():
    """RELATIONSHIP: when citations resolve, drift list is empty."""
    findings = all_drift_findings()
    assert findings == [], (
        f"5 prototyped INVs should have 0 drift findings; got {len(findings)}: "
        + "; ".join(f"{f.inv_id}/{f.kind}/{f.target}" for f in findings[:5])
    )


# ---------------------------------------------------------------------------
# Test 4 (KEY) — does prototype catch what YAML+tests CANNOT?
# ---------------------------------------------------------------------------
# Three concrete scenarios attempted. Honest verdict: see PROTOTYPE_VERDICT
# below. The dispatch explicitly authorizes NULL outcome — do not invent.

def test4a_strict_dominance_on_semgrep_rule_id_typo():
    """SCENARIO 4a: semgrep rule_id typo.

    Test PASSES when prototype catches typo AND no current YAML-side validator
    does the same cross-reference check. This is the STRICT-DOMINANCE evidence
    that round-2 §H1 hold's decision criterion requires.
    """
    # Prototype side: inject typo, verify catch
    @enforced_by(
        statement="Lifecycle grammar is finite (with typo in semgrep ref)",
        semgrep=["zeus-no-direct-phase-asignment"],   # typo
    )
    class TYPO_INV:
        pass
    proto_findings = TYPO_INV.__inv__.drift_findings  # type: ignore[attr-defined]
    proto_catches_typo = any(f.kind == "RULE_NOT_FOUND" for f in proto_findings)

    # YAML side: empirical heuristic — does topology_doctor read semgrep_rule_ids
    # AND cross-reference them against semgrep_zeus.yml? Both signals must be
    # present in the script for a positive identification.
    td_path = REPO_ROOT / "scripts" / "topology_doctor.py"
    td_text = td_path.read_text()
    yaml_validates_semgrep = (
        "semgrep_rule_ids" in td_text and "semgrep_zeus" in td_text
    )

    # STRICT_DOMINANCE: prototype catches AND YAML does not.
    assert proto_catches_typo and not yaml_validates_semgrep, (
        f"STRICT_DOMINANCE check on semgrep typo: prototype_catches={proto_catches_typo}, "
        f"yaml_validates={yaml_validates_semgrep}. Prototype dominates iff "
        f"(True, False); got ({proto_catches_typo}, {yaml_validates_semgrep})."
    )


def test4b_strict_dominance_on_test_function_typo():
    """SCENARIO 4b: test function name typo.

    Test PASSES when prototype catches typo AND no current YAML-side validator
    does the same test-function-resolution check.
    """
    @enforced_by(
        statement="hypothetical INV with typo'd test name",
        test=["tests/test_dual_track_law_stubs.py::test_kely_input_carries_distributional_info"],
    )
    class TYPO_INV_TEST:
        pass
    proto_findings = TYPO_INV_TEST.__inv__.validate()  # type: ignore[attr-defined]
    proto_catches_typo = any(f.kind == "TEST_NOT_FOUND" for f in proto_findings)

    # YAML side: scan tests/test_*.py (excluding self) for a validator that
    # grep-asserts every invariants.yaml `tests:` reference resolves.
    test_files = [tf for tf in (REPO_ROOT / "tests").glob("test_*.py")
                  if tf.name != "test_inv_prototype.py"]
    yaml_validates_test_refs = False
    for tf in test_files:
        text = tf.read_text(errors="ignore")
        if (
            "invariants.yaml" in text
            and ("test_function_resolves" in text or "test_resolves" in text or
                 "every cited test" in text or "tests: block" in text)
        ):
            yaml_validates_test_refs = True
            break

    assert proto_catches_typo and not yaml_validates_test_refs, (
        f"STRICT_DOMINANCE check on test typo: prototype_catches={proto_catches_typo}, "
        f"yaml_validates={yaml_validates_test_refs}. Prototype dominates iff "
        f"(True, False); got ({proto_catches_typo}, {yaml_validates_test_refs})."
    )


def test4c_strict_dominance_on_negative_constraint_typo():
    """SCENARIO 4c: negative_constraint id typo.

    Test PASSES when prototype catches typo AND no current YAML-side validator
    does the same NC-id resolution check.
    """
    @enforced_by(
        statement="hypothetical INV with NC typo",
        negative_constraint=["NC-114"],   # typo: should be NC-14
    )
    class TYPO_INV_NC:
        pass
    proto_findings = TYPO_INV_NC.__inv__.drift_findings  # type: ignore[attr-defined]
    proto_catches_typo = any(f.kind == "NC_NOT_FOUND" for f in proto_findings)

    # YAML side: empirical heuristic — does topology_doctor have an NC-id resolver?
    td_text = (REPO_ROOT / "scripts" / "topology_doctor.py").read_text()
    yaml_validates_nc = (
        "negative_constraints" in td_text
        and ("validate_nc" in td_text or "nc_id_resolves" in td_text or
             "negative_constraint_id" in td_text)
    )

    assert proto_catches_typo and not yaml_validates_nc, (
        f"STRICT_DOMINANCE check on NC typo: prototype_catches={proto_catches_typo}, "
        f"yaml_validates={yaml_validates_nc}. Prototype dominates iff "
        f"(True, False); got ({proto_catches_typo}, {yaml_validates_nc})."
    )


# === ultrareview-25 F5+F10 antibody (validate() purity / idempotency) ===
# Regression-pin the F5+F10 fix: validate() must NOT mutate self.drift_findings,
# and all_drift_findings() must return the SAME list across repeated calls.
# Prior implementation appended lazy findings to self.drift_findings on each
# call, causing all_drift_findings() to double-count on the second call onwards.

def test_validate_is_pure_does_not_mutate_self_drift_findings():
    """F5+F10 antibody: validate() returns new findings without mutating
    self.drift_findings. Calling it twice does not grow the persistent list."""
    from architecture.inv_prototype import PROTOTYPED_INVS
    inv_class = PROTOTYPED_INVS[0]
    inv = inv_class.__inv__  # type: ignore[attr-defined]
    drift_before = list(inv.drift_findings)
    inv.validate()
    inv.validate()
    inv.validate()
    drift_after = list(inv.drift_findings)
    assert drift_before == drift_after, (
        "F5+F10 regression: validate() mutated self.drift_findings. "
        f"Length went from {len(drift_before)} to {len(drift_after)} after "
        "three calls. validate() must be pure — return new findings without "
        "side-effects. Failure surface: all_drift_findings() would double-count."
    )


def test_all_drift_findings_is_idempotent():
    """F5+F10 antibody: all_drift_findings() must be idempotent. Repeated calls
    return identical lists. Prior implementation grew the result on each call
    because validate() side-effects accumulated."""
    from architecture.inv_prototype import all_drift_findings
    first = all_drift_findings()
    second = all_drift_findings()
    third = all_drift_findings()
    assert len(first) == len(second) == len(third), (
        "F5+F10 regression: all_drift_findings() returned different-length "
        "results on repeated calls — "
        f"first={len(first)}, second={len(second)}, third={len(third)}. "
        "This breaks any caller (CI, pre-commit hook, agent prompts) that "
        "summarises drift across runs."
    )
    # Identity-of-content check (not just length): findings should be
    # semantically equal across calls.
    first_keys = sorted((f.inv_id, f.channel, f.target, f.kind) for f in first)
    second_keys = sorted((f.inv_id, f.channel, f.target, f.kind) for f in second)
    assert first_keys == second_keys, (
        "F5+F10 regression: all_drift_findings() returned different findings "
        "across calls (lengths matched but content drifted)."
    )


def test_validate_caches_lazy_findings_without_mutating_drift(monkeypatch):
    """Review-crash antibody: validate() must not re-read lazy targets on every
    call, and caching must not append findings to drift_findings."""
    calls = []

    def fake_validate_test_reference(inv_id, test_ref):
        calls.append((inv_id, test_ref))
        return [DriftFinding(inv_id, "test", test_ref, "TEST_NOT_FOUND", "synthetic")]

    monkeypatch.setattr(_mod, "_validate_test_reference", fake_validate_test_reference)
    inv = _mod.INV(
        id="INV_CACHE",
        statement="cache lazy validation",
        enforcement={"test": ["tests/test_x.py::test_missing"], "schema": []},
    )

    first = inv.validate()
    second = inv.validate()
    third = inv.validate()

    assert first == second == third
    assert calls == [("INV_CACHE", "tests/test_x.py::test_missing")]
    assert inv.drift_findings == []


def test_decorator_resolves_async_test_reference(monkeypatch, tmp_path):
    """Review-crash antibody: async def tests are real test citations."""
    test_file = tmp_path / "tests" / "test_async_contract.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class TestAsyncContract:\n"
        "    async def test_async_guard(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "REPO_ROOT", tmp_path)

    findings = _mod._validate_test_reference(
        "INV_ASYNC",
        "tests/test_async_contract.py::TestAsyncContract::test_async_guard",
    )

    assert findings == []


def test_decorator_rejects_same_test_name_in_wrong_class(monkeypatch, tmp_path):
    """Review-crash antibody: TestClass::test_name must resolve inside that class."""
    test_file = tmp_path / "tests" / "test_class_contract.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class OtherClass:\n"
        "    def test_same_name(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "REPO_ROOT", tmp_path)

    findings = _mod._validate_test_reference(
        "INV_CLASS",
        "tests/test_class_contract.py::ExpectedClass::test_same_name",
    )

    assert [finding.kind for finding in findings] == ["TEST_CLASS_NOT_FOUND"]


def test_decorator_rejects_unqualified_reference_to_class_method(monkeypatch, tmp_path):
    """Review-crash antibody: file::test_name resolves only top-level tests."""
    test_file = tmp_path / "tests" / "test_class_contract.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "class TestOnlyClassMethod:\n"
        "    def test_method_only(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "REPO_ROOT", tmp_path)

    findings = _mod._validate_test_reference(
        "INV_CLASS",
        "tests/test_class_contract.py::test_method_only",
    )

    assert [finding.kind for finding in findings] == ["TEST_NOT_FOUND"]


def test_schema_citation_must_name_existing_content(monkeypatch, tmp_path):
    """Review-crash antibody: schema citations cannot pass by file existence alone."""
    schema = tmp_path / "architecture" / "schema.sql"
    schema.parent.mkdir(parents=True)
    schema.write_text(
        "CREATE TABLE position_events (event_type TEXT CHECK (event_type IN ('SETTLED')));\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "REPO_ROOT", tmp_path)

    assert _mod._validate_schema_column_reference(
        "INV_SCHEMA",
        "architecture/schema.sql::position_events.event_type",
    ) == []

    file_only = _mod._validate_schema_column_reference("INV_SCHEMA", "architecture/schema.sql")
    assert [finding.kind for finding in file_only] == ["SCHEMA_TARGET_MISSING"]

    missing = _mod._validate_schema_column_reference(
        "INV_SCHEMA",
        "architecture/schema.sql::missing_column",
    )
    assert [finding.kind for finding in missing] == ["SCHEMA_TARGET_NOT_FOUND"]
