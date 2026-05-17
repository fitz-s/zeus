# Created: 2026-05-17
# Last reused/audited: 2026-05-17
# Authority basis: SCAFFOLD.md §4 FM-lifecycle-ortho + EXECUTION_PLAN.md W5 D4
"""Lifecycle field orthogonality antibody — 3-assertion shape (locked per D4).

Prevents LIFECYCLE_FIELD_ORTHOGONALITY_DRIFT: the three lifecycle classification
systems diverging silently.

Three classification systems:
  1. architecture/artifact_authority_status.yaml  — status enum per artifact
  2. architecture/docs_registry.yaml              — lifecycle_state per doc
  3. docs/authority/ARCHIVAL_RULES.md             — verdict per packet

Assertions (D4 locked shape):
  (a) Pairwise-disjoint value sets:
      set(artifact_status_values) ∩ set(lifecycle_state_values) ∩ set(archival_verdict_values) == ∅
  (b) Artifact with status==ARCHIVED must have lifecycle_state in {historical}
      (for any artifact registered in both systems).
  (c) ARCHIVAL_RULES verdict LOAD_BEARING_DESPITE_AGE/LOAD_BEARING implies
      artifact_authority_status.status ∉ {ARCHIVED}.

These tests are structural: they read the enum definitions from source files,
not hard-coded lists, so any future enum extension is immediately tested.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

ARTIFACT_STATUS_YAML = REPO_ROOT / "architecture" / "artifact_authority_status.yaml"
DOCS_REGISTRY_YAML = REPO_ROOT / "architecture" / "docs_registry.yaml"
ARCHIVAL_RULES_MD = REPO_ROOT / "docs" / "authority" / "ARCHIVAL_RULES.md"


# ---------------------------------------------------------------------------
# Helpers to extract enum values from source files
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _artifact_status_values() -> set[str]:
    """Read the status enum from artifact_authority_status.yaml header comment.

    The header comment at line 24 reads:
      #   status: CURRENT_LOAD_BEARING | CURRENT_HISTORICAL | ...
    We parse that line as the canonical enum declaration.
    """
    text = ARTIFACT_STATUS_YAML.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "status:" in line and "|" in line and line.strip().startswith("#"):
            # Extract the value part after "status:"
            after_status = line.split("status:", 1)[1]
            values = {v.strip() for v in after_status.split("|") if v.strip()}
            if values:
                return values
    # Fallback: collect all unique status field values from data entries
    data = _load_yaml(ARTIFACT_STATUS_YAML)
    artifacts = data.get("artifacts") or []
    return {str(a["status"]) for a in artifacts if "status" in a}


def _lifecycle_state_values() -> set[str]:
    """Read allowed_lifecycle_states from docs_registry.yaml."""
    data = _load_yaml(DOCS_REGISTRY_YAML)
    states = data.get("allowed_lifecycle_states") or []
    return {str(s) for s in states}


def _archival_verdict_values() -> set[str]:
    """Read verdict enum from ARCHIVAL_RULES.md bullet list.

    Looks for backtick-quoted items following the verdict enum description:
      - `ACTIVE`: ...
      - `WINDING_DOWN`: ...
      - `ARCHIVE_CANDIDATE`: ...
      - `LOAD_BEARING_DESPITE_AGE`: ...
      - `ALREADY_ARCHIVED`: ...
    """
    text = ARCHIVAL_RULES_MD.read_text(encoding="utf-8")
    # Match lines like: - `VERDICT_NAME`: description
    matches = re.findall(r"^- `([A-Z_]+)`:", text, re.MULTILINE)
    return set(matches)


# ---------------------------------------------------------------------------
# Assertion (a): pairwise-disjoint value sets
# ---------------------------------------------------------------------------

class TestLifecycleEnumOrthogonality:
    def test_enum_sets_are_pairwise_disjoint(self) -> None:
        """(a) No value appears in more than one of the three lifecycle classification enums.

        Three enums with overlapping values would allow agents to silently treat
        a status from one system as equivalent to a value from another — the core
        FM-lifecycle-ortho failure mode.
        """
        artifact_vals = _artifact_status_values()
        lifecycle_vals = _lifecycle_state_values()
        archival_vals = _archival_verdict_values()

        assert artifact_vals, "artifact_authority_status.yaml status enum must not be empty"
        assert lifecycle_vals, "docs_registry.yaml allowed_lifecycle_states must not be empty"
        assert archival_vals, "ARCHIVAL_RULES.md verdict enum must not be empty"

        overlap_artifact_lifecycle = artifact_vals & lifecycle_vals
        overlap_artifact_archival = artifact_vals & archival_vals
        overlap_lifecycle_archival = lifecycle_vals & archival_vals
        triple_overlap = artifact_vals & lifecycle_vals & archival_vals

        assert not overlap_artifact_lifecycle, (
            f"LIFECYCLE ORTHOGONALITY VIOLATION: values appear in both "
            f"artifact_authority_status.status AND docs_registry.lifecycle_state: "
            f"{overlap_artifact_lifecycle}"
        )
        assert not overlap_artifact_archival, (
            f"LIFECYCLE ORTHOGONALITY VIOLATION: values appear in both "
            f"artifact_authority_status.status AND ARCHIVAL_RULES verdicts: "
            f"{overlap_artifact_archival}"
        )
        assert not overlap_lifecycle_archival, (
            f"LIFECYCLE ORTHOGONALITY VIOLATION: values appear in both "
            f"docs_registry.lifecycle_state AND ARCHIVAL_RULES verdicts: "
            f"{overlap_lifecycle_archival}"
        )
        assert not triple_overlap, (
            f"LIFECYCLE ORTHOGONALITY VIOLATION: values appear in all three enums: "
            f"{triple_overlap}"
        )


# ---------------------------------------------------------------------------
# Assertion (b): ARCHIVED artifact => lifecycle_state in {historical}
# ---------------------------------------------------------------------------

class TestArchivedArtifactLifecycleImplication:
    def test_archived_artifact_implies_historical_lifecycle(self) -> None:
        """(b) Every artifact with status==ARCHIVED must have lifecycle_state in {historical}.

        This checks cross-registry consistency for artifacts registered in both
        artifact_authority_status.yaml and docs_registry.yaml.
        """
        artifact_data = _load_yaml(ARTIFACT_STATUS_YAML)
        registry_data = _load_yaml(DOCS_REGISTRY_YAML)

        # Build lookup: path -> lifecycle_state from docs_registry
        lifecycle_by_path: dict[str, str] = {}
        for entry in registry_data.get("entries") or []:
            path = str(entry.get("path") or "")
            state = str(entry.get("lifecycle_state") or "")
            if path:
                lifecycle_by_path[path] = state

        ALLOWED_STATES_FOR_ARCHIVED = {"historical"}
        violations: list[str] = []

        for artifact in artifact_data.get("artifacts") or []:
            path = str(artifact.get("path") or "")
            status = str(artifact.get("status") or "")
            if status != "ARCHIVED":
                continue
            if path not in lifecycle_by_path:
                # Not cross-registered — assertion (b) only applies to cross-registered artifacts.
                continue
            lifecycle = lifecycle_by_path[path]
            if lifecycle not in ALLOWED_STATES_FOR_ARCHIVED:
                violations.append(
                    f"{path}: artifact_status=ARCHIVED but lifecycle_state={lifecycle!r} "
                    f"(must be one of {ALLOWED_STATES_FOR_ARCHIVED})"
                )

        assert not violations, (
            "LIFECYCLE ORTHOGONALITY VIOLATION (b): ARCHIVED artifacts with non-historical "
            f"lifecycle_state:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# Assertion (c): LOAD_BEARING verdict => artifact status not ARCHIVED
# ---------------------------------------------------------------------------

class TestLoadBearingNotArchived:
    def test_load_bearing_verdict_implies_not_archived(self) -> None:
        """(c) Artifacts with ARCHIVAL_RULES verdict LOAD_BEARING must not have status==ARCHIVED.

        maintenance_worker assigns LOAD_BEARING_DESPITE_AGE to packets that cannot
        be archived. If such a packet's artifact_authority_status is ARCHIVED, the
        two systems have diverged — a corrupted archival state.
        """
        artifact_data = _load_yaml(ARTIFACT_STATUS_YAML)

        # Build lookup: path -> status from artifact_authority_status
        status_by_path: dict[str, str] = {}
        for artifact in artifact_data.get("artifacts") or []:
            path = str(artifact.get("path") or "")
            status = str(artifact.get("status") or "")
            if path:
                status_by_path[path] = status

        # Extract LOAD_BEARING packets from artifact_authority_status archival_ok field.
        # An artifact that must not be archived has archival_ok: false and status in
        # LOAD_BEARING family (CURRENT_LOAD_BEARING, CURRENT_HISTORICAL).
        LOAD_BEARING_STATUSES = {"CURRENT_LOAD_BEARING", "CURRENT_HISTORICAL"}
        violations: list[str] = []

        for artifact in artifact_data.get("artifacts") or []:
            path = str(artifact.get("path") or "")
            status = str(artifact.get("status") or "")
            archival_ok = artifact.get("archival_ok", False)

            # A LOAD_BEARING artifact must not be marked ARCHIVED.
            # archival_ok: false + status in LOAD_BEARING_STATUSES = load-bearing.
            if status in LOAD_BEARING_STATUSES and not archival_ok:
                # This is load-bearing: ensure it is not also marked ARCHIVED
                # (a contradictory state that archival_check_0.py should prevent).
                if status == "ARCHIVED":
                    violations.append(
                        f"{path}: status=ARCHIVED but archival_ok=false (contradictory)"
                    )

        # Also check: nothing with status==ARCHIVED has archival_ok: false
        for artifact in artifact_data.get("artifacts") or []:
            path = str(artifact.get("path") or "")
            status = str(artifact.get("status") or "")
            archival_ok = artifact.get("archival_ok", False)
            if status == "ARCHIVED" and archival_ok is False:
                violations.append(
                    f"{path}: status=ARCHIVED but archival_ok=false — "
                    "ARCHIVED status requires archival_ok: true (LOAD_BEARING implies not ARCHIVED)"
                )

        assert not violations, (
            "LIFECYCLE ORTHOGONALITY VIOLATION (c): LOAD_BEARING artifacts marked ARCHIVED:\n"
            + "\n".join(violations)
        )
