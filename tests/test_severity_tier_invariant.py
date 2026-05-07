# Created: 2026-05-07
# Last reused or audited: 2026-05-07
# Authority basis: Navigation Topology v2 PLAN §1.3 K1 structural decision; admission_severity.yaml; sunset 2026-11-07

"""K1 severity-tier invariant tests for architecture/admission_severity.yaml.

Asserts:
  1. No entry has target_severity=BLOCKING with reversibility_class=WORKING.
     (The K1 invariant: BLOCKING is reserved for TRUTH_REWRITE+ reversibility.)
  2. F5 rules (script_long_lived_bad_name, script_diagnostic_forbidden_write_target)
     have target_severity=ADVISORY (both are WORKING reversibility class).
  3. No WORKING-class entry silently escalates to BLOCKING via a severity_when clause
     without also carrying an explicit target_severity override.

Per Navigation Topology v2 PLAN §1.3 K1 structural decision:
  "Default flip: ADVISORY for everything except a small explicit BLOCKING list
   (TRUTH_REWRITE+ paths from architecture/capabilities.yaml reversibility class)."

Per critic-opus evidence/topology_v2_critic_opus.md ATTACK 7 and PLAN §4 H-R1:
  "Every demoted issue emits ritual_signal severity_demoted: true; tests assert
   blocking severity preserved for TRUTH_REWRITE+ paths."
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
ADMISSION_SEVERITY_YAML = REPO_ROOT / "architecture" / "admission_severity.yaml"

# F5 rule codes that must be ADVISORY (WORKING reversibility; reversible by git mv / git rm)
F5_RULE_CODES = {
    "script_long_lived_bad_name",
    "script_diagnostic_forbidden_write_target",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_issue_entries() -> list[dict]:
    assert ADMISSION_SEVERITY_YAML.exists(), (
        f"architecture/admission_severity.yaml not found at {ADMISSION_SEVERITY_YAML}"
    )
    with ADMISSION_SEVERITY_YAML.open() as f:
        data = yaml.safe_load(f)
    return data.get("issue_severity", [])


def _issue_entry_params() -> list[tuple[str, dict]]:
    if not ADMISSION_SEVERITY_YAML.exists():
        return []
    return [(e.get("rule_id", e.get("code", repr(e)[:30])), e)
            for e in _load_issue_entries()]


# ---------------------------------------------------------------------------
# K1 invariant: BLOCKING requires TRUTH_REWRITE or ON_CHAIN reversibility
# ---------------------------------------------------------------------------

def test_no_blocking_with_working_reversibility() -> None:
    """K1 invariant: no entry may have target_severity=BLOCKING and reversibility_class=WORKING.

    BLOCKING is reserved for TRUTH_REWRITE+ paths (reversibility_class in
    {TRUTH_REWRITE, ON_CHAIN}). WORKING-class issues must be ADVISORY or silent.

    Per Navigation Topology v2 PLAN §1.3 K1 and PLAN §4 H-R1.
    """
    entries = _load_issue_entries()
    violations = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        rule_id = entry.get("rule_id", entry.get("code", "?"))
        ts = entry.get("target_severity")
        rc = entry.get("reversibility_class")
        if ts == "BLOCKING" and rc == "WORKING":
            violations.append(
                f"  {rule_id}: target_severity=BLOCKING but reversibility_class=WORKING"
            )
    assert not violations, (
        "K1 invariant violated — BLOCKING requires TRUTH_REWRITE or ON_CHAIN "
        "reversibility class:\n" + "\n".join(violations) + "\n\n"
        "Per Navigation Topology v2 PLAN §1.3: BLOCKING is reserved for "
        "TRUTH_REWRITE+ paths only."
    )


@pytest.mark.parametrize("rule_id,entry", _issue_entry_params(),
                         ids=[p[0] for p in _issue_entry_params()])
def test_blocking_entries_have_truth_rewrite_or_on_chain(rule_id: str, entry: dict) -> None:
    """Every BLOCKING entry must have reversibility_class in {TRUTH_REWRITE, ON_CHAIN}."""
    ts = entry.get("target_severity")
    rc = entry.get("reversibility_class")
    if ts != "BLOCKING":
        return  # Not a BLOCKING entry; invariant does not apply
    assert rc in {"TRUTH_REWRITE", "ON_CHAIN"}, (
        f"Entry {rule_id!r}: target_severity=BLOCKING requires reversibility_class "
        f"in {{TRUTH_REWRITE, ON_CHAIN}}, got {rc!r}.\n"
        f"K1 invariant: WORKING-class issues must be ADVISORY, not BLOCKING."
    )


# ---------------------------------------------------------------------------
# F5 rule assertions
# ---------------------------------------------------------------------------

def test_f5_script_long_lived_bad_name_is_advisory() -> None:
    """script_long_lived_bad_name must have target_severity=ADVISORY (F5 fix).

    Per PLAN §1.1 F5: name change is reversible by ordinary git mv (WORKING class);
    therefore severity must be ADVISORY, not BLOCKING.
    Verified emitter: scripts/topology_doctor_script_checks.py:121-128.
    """
    entries = _load_issue_entries()
    matching = [e for e in entries if e.get("code") == "script_long_lived_bad_name"]
    assert matching, (
        "No entry with code='script_long_lived_bad_name' found in issue_severity. "
        "F5 fix requires this entry to exist with target_severity=ADVISORY."
    )
    for entry in matching:
        rule_id = entry.get("rule_id", "?")
        ts = entry.get("target_severity")
        assert ts == "ADVISORY", (
            f"Entry {rule_id!r} (code=script_long_lived_bad_name): "
            f"target_severity={ts!r}, expected ADVISORY.\n"
            f"F5 fix: name change is reversible by git mv (WORKING class); "
            f"must be ADVISORY per K1 structural decision."
        )


def test_f5_script_diagnostic_forbidden_write_target_is_advisory() -> None:
    """script_diagnostic_forbidden_write_target must have target_severity=ADVISORY (F5 fix).

    Per PLAN §1.1 F5: write-target change is reversible by git rm + reroute (WORKING class);
    therefore severity must be ADVISORY, not BLOCKING.
    Verified emitter: scripts/topology_doctor_script_checks.py:267-274.
    """
    entries = _load_issue_entries()
    matching = [e for e in entries if e.get("code") == "script_diagnostic_forbidden_write_target"]
    assert matching, (
        "No entry with code='script_diagnostic_forbidden_write_target' found in issue_severity. "
        "F5 fix requires this entry to exist with target_severity=ADVISORY."
    )
    for entry in matching:
        rule_id = entry.get("rule_id", "?")
        ts = entry.get("target_severity")
        assert ts == "ADVISORY", (
            f"Entry {rule_id!r} (code=script_diagnostic_forbidden_write_target): "
            f"target_severity={ts!r}, expected ADVISORY.\n"
            f"F5 fix: write-target change is reversible (WORKING class); "
            f"must be ADVISORY per K1 structural decision."
        )


def test_f5_rules_have_working_reversibility() -> None:
    """Both F5 rule codes must have reversibility_class=WORKING.

    Confirms the WORKING classification is consistent with the ADVISORY target_severity.
    WORKING means: reversible by ordinary means (git mv, git rm) without replay.
    """
    entries = _load_issue_entries()
    for code in F5_RULE_CODES:
        matching = [e for e in entries if e.get("code") == code]
        assert matching, (
            f"No entry with code={code!r} found. F5 fix requires this entry."
        )
        for entry in matching:
            rule_id = entry.get("rule_id", "?")
            rc = entry.get("reversibility_class")
            assert rc == "WORKING", (
                f"Entry {rule_id!r} (code={code!r}): reversibility_class={rc!r}, "
                f"expected WORKING. F5 rules are reversible by ordinary git operations."
            )


# ---------------------------------------------------------------------------
# No silent escalation: WORKING entries may not have severity_when that forces BLOCKING
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rule_id,entry", _issue_entry_params(),
                         ids=[p[0] for p in _issue_entry_params()])
def test_working_entry_severity_when_does_not_escalate_to_blocking(
    rule_id: str, entry: dict
) -> None:
    """WORKING-class entries with severity_when clauses must not escalate to BLOCKING.

    A severity_when clause on a WORKING-class entry may produce a BLOCKING result
    only if it explicitly overrides reversibility_class to TRUTH_REWRITE+. If the
    entry has reversibility_class=WORKING and carries severity_when, the severity
    in that clause must not be 'blocking' (case-insensitive).

    This prevents silent K1 invariant bypass via conditional escalation.
    """
    rc = entry.get("reversibility_class")
    if rc != "WORKING":
        return  # Only applies to WORKING entries

    severity_when = entry.get("severity_when")
    if not severity_when:
        return  # No conditional clause; K1 invariant satisfied trivially

    # severity_when exists on a WORKING entry; check the severity value within
    when_severity = severity_when.get("severity") if isinstance(severity_when, dict) else None
    if when_severity is None:
        return  # No severity key inside severity_when; not an escalation

    assert str(when_severity).upper() != "BLOCKING", (
        f"Entry {rule_id!r}: reversibility_class=WORKING but severity_when.severity="
        f"{when_severity!r} (BLOCKING). K1 invariant forbids BLOCKING on WORKING entries. "
        f"Use target_severity=ADVISORY or elevate reversibility_class to TRUTH_REWRITE+."
    )
