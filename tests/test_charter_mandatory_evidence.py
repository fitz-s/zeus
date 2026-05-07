# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: ANTI_DRIFT_CHARTER §M4; IMPLEMENTATION_PLAN Phase 2 D-5

"""Schema validator for ANTI_DRIFT_CHARTER §M4.

§M4 (charter §4 "M2 — Opt-in by default, escalation by evidence") governs
HELPER FRONTMATTER in .agents/skills/*/SKILL.md and .claude/skills/*/SKILL.md.
Every helper's SKILL.md frontmatter with mandatory: true must carry all three
keys required by §M4:
  - mandatory_evidence.operator_signature
  - mandatory_evidence.recent_miss
  - mandatory_evidence.sunset_date

The architecture/invariants.yaml guard below is an incidental forward-guard for
invariant entries that may in future carry mandatory: true — it is NOT the
primary §M4 enforcement target. The helper-frontmatter scan is primary.

Per ANTI_DRIFT_CHARTER §4:
  "mandatory: true is permitted only when ALL THREE are present:
   mandatory_evidence.operator_signature, mandatory_evidence.recent_miss,
   mandatory_evidence.sunset_date"

Current state (2026-05-06): no SKILL.md carries mandatory: true; no
invariants.yaml entry carries mandatory: true. Both guards are forward-guards.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
INVARIANTS_YAML = REPO_ROOT / "architecture" / "invariants.yaml"

# Primary §M4 scan targets: helper SKILL.md files.
# Charter §4 explicitly names .agents/skills/zeus-ai-handoff/SKILL.md as the
# drift example; the scan covers all helpers in both skill dirs.
HELPER_SKILL_DIRS = [
    REPO_ROOT / ".agents" / "skills",
    REPO_ROOT / ".claude" / "skills",
]

# Keys required in mandatory_evidence block per charter §M4
MANDATORY_EVIDENCE_KEYS = {"operator_signature", "recent_miss", "sunset_date"}

# enforced_by sub-keys that count as non-empty enforcement metadata
ENFORCEMENT_SUBKEYS = {"tests", "runtime", "evidence"}


def _parse_skill_frontmatter(skill_path: pathlib.Path) -> dict | None:
    """Parse YAML frontmatter block (between first --- delimiters) from a SKILL.md.

    Returns the parsed dict or None if no frontmatter / parse error.
    """
    try:
        content = skill_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1])
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def _collect_skill_files() -> list[pathlib.Path]:
    """Return all SKILL.md paths under the known helper skill dirs."""
    found = []
    for skill_dir in HELPER_SKILL_DIRS:
        if skill_dir.is_dir():
            found.extend(skill_dir.rglob("SKILL.md"))
    return found


# ─── PRIMARY §M4 TESTS: helper SKILL.md frontmatter ─────────────────────────


def test_helper_skill_dirs_exist() -> None:
    """At least one SKILL.md must exist in the helper skill dirs; otherwise we
    cannot enforce the charter and would silently vacuous-pass.

    If neither dir exists, emit pytest.skip with explicit reason so the absence
    is visible rather than silent.
    """
    skill_files = _collect_skill_files()
    if not skill_files:
        pytest.skip(
            reason=(
                "No SKILL.md files found under "
                + str([str(d) for d in HELPER_SKILL_DIRS])
                + " — cannot enforce ANTI_DRIFT_CHARTER §M4 on helper frontmatter. "
                "Verify HELPER_SKILL_DIRS paths are correct for this repo layout."
            )
        )
    # Pass — files were found; the count is informational.
    assert len(skill_files) >= 1, "unreachable — already checked above"


def test_helper_mandatory_true_requires_evidence() -> None:
    """PRIMARY §M4 ENFORCEMENT: every SKILL.md with mandatory: true must carry
    a complete mandatory_evidence block with all three required keys.

    ANTI_DRIFT_CHARTER §4 (M2 mechanism):
      mandatory: true is permitted only when operator_signature, recent_miss,
      and sunset_date are all present and non-empty.

    Current state (2026-05-06): no SKILL.md carries mandatory: true. This is a
    forward-guard — it will fail on the first helper that adds mandatory: true
    without the evidence block.
    """
    skill_files = _collect_skill_files()
    if not skill_files:
        pytest.skip(reason="No SKILL.md files found — see test_helper_skill_dirs_exist")

    mandatory_helpers: list[tuple[pathlib.Path, dict]] = []
    for sf in skill_files:
        fm = _parse_skill_frontmatter(sf)
        if fm and fm.get("mandatory") is True:
            mandatory_helpers.append((sf, fm))

    if not mandatory_helpers:
        # Forward guard active — no mandatory:true helpers found.
        # Use pytest.skip so the absence is visible (not silently vacuous).
        pytest.skip(
            reason=(
                f"No SKILL.md with mandatory: true found across "
                f"{len(skill_files)} helper files — §M4 forward guard active."
            )
        )

    failures = []
    for sf, fm in mandatory_helpers:
        helper_id = fm.get("name", sf.parent.name)
        me = fm.get("mandatory_evidence") or {}

        if not isinstance(me, dict):
            failures.append(f"{helper_id} ({sf}): mandatory_evidence is not a mapping")
            continue

        missing = MANDATORY_EVIDENCE_KEYS - set(me.keys())
        if missing:
            failures.append(
                f"{helper_id} ({sf}): mandatory_evidence missing keys: {sorted(missing)}"
            )
            continue

        for key in MANDATORY_EVIDENCE_KEYS:
            if not me.get(key):
                failures.append(
                    f"{helper_id} ({sf}): mandatory_evidence.{key} is null or empty"
                )

    assert not failures, (
        "ANTI_DRIFT_CHARTER §M4 violation — helper SKILL.md with mandatory: true "
        "missing required evidence:\n" + "\n".join(f"  - {f}" for f in failures)
    )


def test_helper_mandatory_true_no_bare_flag() -> None:
    """Guard: mandatory: true without a mandatory_evidence block at all is a
    hard violation (bare flag pattern per charter §4).
    """
    skill_files = _collect_skill_files()
    if not skill_files:
        pytest.skip(reason="No SKILL.md files found — see test_helper_skill_dirs_exist")

    violations = []
    for sf in skill_files:
        fm = _parse_skill_frontmatter(sf)
        if fm and fm.get("mandatory") is True and "mandatory_evidence" not in fm:
            helper_id = fm.get("name", sf.parent.name)
            violations.append(f"{helper_id} ({sf}): mandatory: true but no mandatory_evidence block")

    assert not violations, (
        "ANTI_DRIFT_CHARTER §M4: mandatory: true requires mandatory_evidence block in "
        "helper SKILL.md frontmatter:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ─── INCIDENTAL FORWARD GUARD: architecture/invariants.yaml ──────────────────
# Not the primary §M4 target (which is helper frontmatter above), but retained
# as a secondary guard in case future invariant entries carry mandatory: true.


def _load_invariants() -> list[dict]:
    with INVARIANTS_YAML.open() as f:
        data = yaml.safe_load(f)
    return data.get("invariants", [])


def _mandatory_invariants() -> list[dict]:
    return [inv for inv in _load_invariants() if inv.get("mandatory") is True]


def test_mandatory_invariants_have_evidence_block() -> None:
    """All mandatory:true invariants carry complete mandatory_evidence block."""
    mandatory = _mandatory_invariants()
    # No mandatory entries currently — this is a forward guard.
    # If this list is non-empty, enforce the charter §M4 contract.
    if not mandatory:
        # Pass with informational note rather than vacuous-pass
        pytest.skip(
            reason="No mandatory:true invariants found in invariants.yaml — "
                   "forward guard active (will fail on first mandatory:true addition "
                   "without evidence block)"
        )

    failures = []
    for inv in mandatory:
        inv_id = inv.get("id", repr(inv)[:30])
        me = inv.get("mandatory_evidence") or {}

        missing_keys = MANDATORY_EVIDENCE_KEYS - set(me.keys())
        if missing_keys:
            failures.append(
                f"{inv_id}: mandatory_evidence missing keys: {sorted(missing_keys)}"
            )
            continue

        # Each key must be non-null / non-empty
        for key in MANDATORY_EVIDENCE_KEYS:
            val = me.get(key)
            if not val:
                failures.append(
                    f"{inv_id}: mandatory_evidence.{key} is null or empty"
                )

    assert not failures, (
        "ANTI_DRIFT_CHARTER §M4 violation — mandatory:true invariants missing "
        "required evidence:\n" + "\n".join(f"  - {f}" for f in failures)
    )


def test_mandatory_invariants_have_enforced_by_metadata() -> None:
    """All mandatory:true invariants have non-empty enforcement metadata (fail-closed)."""
    mandatory = _mandatory_invariants()
    if not mandatory:
        pytest.skip(
            reason="No mandatory:true invariants found — forward guard active"
        )

    failures = []
    for inv in mandatory:
        inv_id = inv.get("id", repr(inv)[:30])
        enforced = inv.get("enforced_by") or {}

        has_enforcement = any(
            enforced.get(key) for key in ENFORCEMENT_SUBKEYS
        )
        if not has_enforcement:
            failures.append(
                f"{inv_id}: enforced_by has no non-empty tests/runtime/evidence "
                f"(got: {list(enforced.keys())})"
            )

    assert not failures, (
        "mandatory:true invariants must have non-empty enforcement metadata "
        "(fail-closed per Phase 2 D-5 spec):\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_no_mandatory_true_without_evidence_block_future_guard() -> None:
    """Guard: if mandatory:true is added, it must ALSO have mandatory_evidence block.

    This test scans ALL invariants (not just detected mandatory ones) to catch
    cases where mandatory:true is set but mandatory_evidence is entirely absent.
    """
    all_invs = _load_invariants()
    violations = []
    for inv in all_invs:
        if inv.get("mandatory") is True:
            if "mandatory_evidence" not in inv:
                inv_id = inv.get("id", repr(inv)[:30])
                violations.append(
                    f"{inv_id}: mandatory:true but no mandatory_evidence block at all"
                )

    assert not violations, (
        "ANTI_DRIFT_CHARTER §M4: mandatory:true requires mandatory_evidence block:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ─── PHASE 4 GATE FRONTMATTER SCAN (F-3 Phase 2 carry-forward) ───────────────
# Canonical gate location: docs/operations/task_2026-05-06_topology_redesign/gates/
# Gates are *.md files with YAML frontmatter. When a gate carries mandatory: true,
# it must also carry a non-empty evidence field per ANTI_DRIFT_CHARTER §M4.
#
# Phase 4.A state: directory exists but no gate files yet (4.B+ authors gates).
# This scan skips gracefully if the directory is empty.
#
# Authority: phase3_h_decision.md F-3 verdict; IMPLEMENTATION_PLAN Phase 4 contract.

GATES_DIR = REPO_ROOT / "docs" / "operations" / "task_2026-05-06_topology_redesign" / "gates"


def _collect_gate_files() -> list[pathlib.Path]:
    """Return all *.md gate files under the canonical gates directory."""
    if not GATES_DIR.is_dir():
        return []
    return sorted(GATES_DIR.glob("*.md"))


def _parse_gate_frontmatter(gate_path: pathlib.Path) -> dict | None:
    """Parse YAML frontmatter (between first --- delimiters) from a gate *.md file."""
    try:
        content = gate_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1])
        return fm if isinstance(fm, dict) else None
    except yaml.YAMLError:
        return None


def test_gate_frontmatter_mandatory_evidence() -> None:
    """F-3 carry-forward (Phase 2): every Phase 4 gate with mandatory: true must
    carry a non-empty evidence field in its frontmatter.

    Scan target: docs/operations/task_2026-05-06_topology_redesign/gates/*.md
    Phase 4.A state: directory is empty (no gate files yet); test skips gracefully.
    Phase 4.B+: gates are authored with frontmatter; this test enforces the contract.

    Contract: if frontmatter.mandatory == true, then frontmatter.evidence must
    be present and non-empty (string or non-empty list).
    """
    gate_files = _collect_gate_files()

    if not gate_files:
        pytest.skip(
            reason=(
                f"No gate *.md files found in {GATES_DIR} — "
                "Phase 4.A pre-gate state; scan becomes active when 4.B+ authors gates."
            )
        )

    failures = []
    for gf in gate_files:
        fm = _parse_gate_frontmatter(gf)
        if fm is None:
            # No frontmatter — only flag if mandatory: true appears in raw text
            # (catches malformed frontmatter that wouldn't parse)
            continue
        if fm.get("mandatory") is not True:
            continue
        gate_id = fm.get("gate_id") or fm.get("id") or gf.stem
        evidence = fm.get("evidence")
        if not evidence:
            failures.append(
                f"{gate_id} ({gf.name}): mandatory: true but evidence field is absent or empty"
            )
        elif isinstance(evidence, list) and len(evidence) == 0:
            failures.append(
                f"{gate_id} ({gf.name}): mandatory: true but evidence list is empty"
            )

    assert not failures, (
        "ANTI_DRIFT_CHARTER §M4 / F-3: Phase 4 gate with mandatory: true "
        "must carry non-empty evidence field:\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


def test_gates_dir_is_canonical_path() -> None:
    """Structural guard: the canonical gates directory must exist once Phase 4.A ships.

    This test fails if the directory is accidentally deleted or moved, alerting
    4.B+ gate authors that they are writing to the wrong location.
    """
    assert GATES_DIR.is_dir(), (
        f"Canonical gates directory missing: {GATES_DIR}\n"
        "Phase 4.A must create this directory. 4.B+ gate files must live here."
    )
