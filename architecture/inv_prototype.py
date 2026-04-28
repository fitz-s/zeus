"""@enforced_by decorator prototype — round-2 §H1 hold experiment.

Created: 2026-04-28
Last reused/audited: 2026-04-28
Authority basis: docs/operations/task_2026-04-27_harness_debate/round2_verdict.md
§4.2 #13 (operator-decision territory) + §H1 hold (proponent: INVs stay as YAML
pending working `@enforced_by` decorator prototype that demonstrates strictly
stronger enforcement than current YAML+tests setup).

EXPLICIT CHARGE (per Tier 2 Phase 4 dispatch + critic methodology §5.Z2):
prove or disprove strictly-stronger enforcement. Do NOT pre-assume Python is
better. The 3-for-3 pattern (Phase 2 + Phase 3a + Phase 3b) shows replacement
proposals often fail to add value — this prototype is the empirical test.

DESIGN CHOICES (documented for review):

  1. Decorator vs metaclass: DECORATOR. Decorator on a stub function/class is
     less invasive than a metaclass; metaclass would require restructuring
     existing INV definitions. Decorator can be applied incrementally.

  2. EAGER vs LAZY validation: HYBRID.
     - file/path existence: EAGER (validated at import time; cheap).
     - semgrep rule presence: EAGER (cheap YAML grep).
     - test function existence: LAZY (would require pytest collection or
       `inspect` + import — too heavy for import-time decorator).
     - schema column presence: LAZY (requires SQL parsing; too heavy).
     Eager validation surfaces drift at the next module import; lazy validation
     is on-demand via `validate()` method.

  3. Failure mode: COLLECT, do NOT raise. Eager validation accumulates drift
     into INV.drift_findings instead of raising at import time. Reason:
     raising at import would prevent agents from running ANY code while drift
     exists, which would be louder than current YAML+tests. Validate-on-demand
     is the right ergonomic.

  4. Scope: PROTOTYPE ONLY. 5 sample INVs (not all 30). Not yet wired into
     CI. Migration of YAML INVs is operator decision after critic review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class DriftFinding:
    """One enforcement-target citation that does not resolve at HEAD."""
    inv_id: str
    channel: str          # "test" | "semgrep" | "schema" | "script" | "negative_constraint" | "doc"
    target: str           # e.g., "tests/test_x.py::test_y" or "scripts/x.py"
    kind: str             # "FILE_MISSING" | "TEST_NOT_FOUND" | "RULE_NOT_FOUND" | etc.
    detail: str = ""


@dataclass
class INV:
    """Bound INV record produced by @enforced_by decoration.

    The decorator attaches an INV instance to the wrapped class/function as
    `__inv__`. drift_findings accumulates eager-validation issues at decoration
    time; cross-INV checks (e.g., is the cited rule actually wired?) run via
    validate().
    """
    id: str
    statement: str
    enforcement: dict[str, list[str]] = field(default_factory=dict)
    drift_findings: list[DriftFinding] = field(default_factory=list)

    def validate(self) -> list[DriftFinding]:
        """Run lazy validation (test+schema). Returns NEW drift findings."""
        new = []
        for test_ref in self.enforcement.get("test", []):
            new.extend(_validate_test_reference(self.id, test_ref))
        for schema_ref in self.enforcement.get("schema", []):
            new.extend(_validate_schema_column_reference(self.id, schema_ref))
        self.drift_findings.extend(new)
        return new


# --- Eager validators (run at decoration time) -----------------------------

def _validate_path_exists(inv_id: str, channel: str, path: str) -> list[DriftFinding]:
    if (REPO_ROOT / path.split("::")[0]).exists():
        return []
    return [DriftFinding(inv_id, channel, path, "FILE_MISSING",
                         f"cited path {path!r} does not exist at HEAD")]


def _validate_semgrep_rule(inv_id: str, rule_id: str) -> list[DriftFinding]:
    semgrep_yml = REPO_ROOT / "architecture" / "ast_rules" / "semgrep_zeus.yml"
    if not semgrep_yml.exists():
        return [DriftFinding(inv_id, "semgrep", rule_id, "FILE_MISSING",
                             "semgrep_zeus.yml does not exist")]
    text = semgrep_yml.read_text()
    if re.search(rf"^\s*-?\s*id:\s*{re.escape(rule_id)}\s*$", text, re.MULTILINE):
        return []
    return [DriftFinding(inv_id, "semgrep", rule_id, "RULE_NOT_FOUND",
                         f"rule_id {rule_id!r} not declared in semgrep_zeus.yml")]


def _validate_negative_constraint(inv_id: str, nc_id: str) -> list[DriftFinding]:
    nc_yml = REPO_ROOT / "architecture" / "negative_constraints.yaml"
    if not nc_yml.exists():
        return [DriftFinding(inv_id, "negative_constraint", nc_id, "FILE_MISSING",
                             "negative_constraints.yaml does not exist")]
    text = nc_yml.read_text()
    if re.search(rf"^\s*-?\s*id:\s*{re.escape(nc_id)}\s*$", text, re.MULTILINE):
        return []
    return [DriftFinding(inv_id, "negative_constraint", nc_id, "NC_NOT_FOUND",
                         f"NC {nc_id!r} not declared in negative_constraints.yaml")]


# --- Lazy validators (run via validate()) ----------------------------------

def _validate_test_reference(inv_id: str, test_ref: str) -> list[DriftFinding]:
    """Verify tests/test_x.py::test_y resolves to a real def."""
    if "::" not in test_ref:
        return _validate_path_exists(inv_id, "test", test_ref)
    file_part, test_part = test_ref.split("::", 1)
    file_path = REPO_ROOT / file_part
    if not file_path.exists():
        return [DriftFinding(inv_id, "test", test_ref, "FILE_MISSING",
                             f"test file {file_part!r} not found")]
    text = file_path.read_text()
    leaf = test_part.split("::")[-1]   # strip TestClass:: prefix if present
    if re.search(rf"^\s*def\s+{re.escape(leaf)}\b", text, re.MULTILINE):
        return []
    return [DriftFinding(inv_id, "test", test_ref, "TEST_NOT_FOUND",
                         f"def {leaf!r} not in {file_part}")]


def _validate_schema_column_reference(inv_id: str, schema_ref: str) -> list[DriftFinding]:
    """Verify schema citation file exists. Column-level checks are out of scope
    for the prototype (would require SQL parser)."""
    return _validate_path_exists(inv_id, "schema", schema_ref)


# --- The decorator ---------------------------------------------------------

def enforced_by(
    *,
    statement: str,
    test: Optional[list[str]] = None,
    semgrep: Optional[list[str]] = None,
    schema: Optional[list[str]] = None,
    script: Optional[list[str]] = None,
    negative_constraint: Optional[list[str]] = None,
    doc: Optional[list[str]] = None,
):
    """@enforced_by decorator. Attaches INV record + runs eager validators.

    Usage:
        @enforced_by(
            statement="strategy_key is the sole governance key",
            schema=["architecture/2026_04_02_architecture_kernel.sql"],
            test=["tests/test_architecture_contracts.py::test_strategy_key_manifest_is_frozen"],
        )
        class INV_04:
            pass
    """
    def _wrap(target):
        inv_id = target.__name__
        enforcement = {
            "test": test or [],
            "semgrep": semgrep or [],
            "schema": schema or [],
            "script": script or [],
            "negative_constraint": negative_constraint or [],
            "doc": doc or [],
        }
        inv = INV(id=inv_id, statement=statement, enforcement=enforcement)

        # Eager validators (cheap; run at decoration time)
        for s in enforcement["semgrep"]:
            inv.drift_findings.extend(_validate_semgrep_rule(inv_id, s))
        for sc in enforcement["script"]:
            inv.drift_findings.extend(_validate_path_exists(inv_id, "script", sc))
        for nc in enforcement["negative_constraint"]:
            inv.drift_findings.extend(_validate_negative_constraint(inv_id, nc))
        for d in enforcement["doc"]:
            inv.drift_findings.extend(_validate_path_exists(inv_id, "doc", d))
        # NOTE: test + schema are LAZY (require deeper inspection); call .validate()

        target.__inv__ = inv
        return target
    return _wrap


# --- 5 sample INV decorations (the test bed) -------------------------------

@enforced_by(
    statement="Settlement is not exit.",
    schema=["architecture/2026_04_02_architecture_kernel.sql"],
    test=[
        "tests/test_architecture_contracts.py::test_lifecycle_phase_kernel_accepts_current_canonical_builder_folds",
        "tests/test_architecture_contracts.py::test_lifecycle_phase_kernel_rejects_illegal_fold",
    ],
)
class INV_02:
    """Mixed schema + tests (CITATION_REPAIR'd in BATCH D + SIDECAR-2)."""


@enforced_by(
    statement="Lifecycle grammar is finite and authoritative.",
    schema=["architecture/2026_04_02_architecture_kernel.sql"],
    semgrep=["zeus-no-direct-phase-assignment"],
)
class INV_07:
    """Schema + semgrep_rule_id (no tests cited, even though hidden tests exist)."""


@enforced_by(
    statement="Canonical write path has one transaction boundary.",
    script=["scripts/check_kernel_manifests.py"],
)
class INV_08:
    """Script-only enforcement."""


@enforced_by(
    statement="Kelly sizing requires an executable-price distribution.",
    semgrep=["zeus-no-bare-entry-price-kelly"],
    test=["tests/test_dual_track_law_stubs.py::test_kelly_input_carries_distributional_info"],
    negative_constraint=["NC-14"],
)
class INV_21:
    """Multi-channel: semgrep + test + NC."""


@enforced_by(
    statement="LLM output is never authority.",
    script=["scripts/check_work_packets.py"],
    doc=["architecture/self_check/zero_context_entry.md"],
)
class INV_10:
    """Script + doc (no test)."""


# Module-level registry of decorated INVs. Tests iterate this.
PROTOTYPED_INVS = [INV_02, INV_07, INV_08, INV_21, INV_10]


def all_drift_findings() -> list[DriftFinding]:
    """Aggregate eager+lazy findings across all decorated INVs."""
    findings = []
    for cls in PROTOTYPED_INVS:
        inv = cls.__inv__  # type: ignore[attr-defined]
        findings.extend(inv.drift_findings)
        findings.extend(inv.validate())
    return findings


if __name__ == "__main__":
    findings = all_drift_findings()
    if not findings:
        print(f"OK: {len(PROTOTYPED_INVS)} INVs decorated; 0 drift findings")
    else:
        print(f"DRIFT: {len(PROTOTYPED_INVS)} INVs decorated; {len(findings)} findings:")
        for f in findings:
            print(f"  - {f.inv_id} [{f.channel}] {f.target}: {f.kind} — {f.detail}")
