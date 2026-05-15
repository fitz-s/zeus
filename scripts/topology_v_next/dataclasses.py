# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p1_topology_v_next_additive/SCAFFOLD.md §1.2
#                  docs/operations/task_2026-05-15_runtime_improvement_engineering_package/01_topology_v_next/UNIVERSAL_TOPOLOGY_DESIGN.md §2, §12, §13
"""
Pure data declarations for topology v_next admission system.

No logic here. All classes are frozen dataclasses or str-based enums.
Codex-importable: stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    """Admission severity tiers per Universal §2.4."""

    ADMIT = "ADMIT"
    ADVISORY = "ADVISORY"
    SOFT_BLOCK = "SOFT_BLOCK"
    HARD_STOP = "HARD_STOP"


class Intent(str, Enum):
    """
    Canonical intent values plus Zeus-namespace extensions.

    Universal values per UNIVERSAL_TOPOLOGY_DESIGN.md §2.2.
    Zeus extensions per ZEUS_BINDING_LAYER.md §2.
    Extension values are prefixed with `zeus.` to prevent future collisions.
    """

    # Universal canonical values
    plan_only = "plan_only"
    create_new = "create_new"
    modify_existing = "modify_existing"
    refactor = "refactor"
    audit = "audit"
    hygiene = "hygiene"
    hotfix = "hotfix"
    rebase_keepup = "rebase_keepup"
    other = "other"

    # Zeus-specific extensions (zeus. namespace)
    zeus_settlement_followthrough = "zeus.settlement_followthrough"
    zeus_calibration_update = "zeus.calibration_update"
    zeus_data_authority_receipt = "zeus.data_authority_receipt"
    zeus_topology_tooling = "zeus.topology_tooling"


class FrictionPattern(str, Enum):
    """
    Named friction patterns from operational history per Universal §12.

    These are the seven patterns that v_next structurally addresses.
    """

    LEXICAL_PROFILE_MISS = "LEXICAL_PROFILE_MISS"
    UNION_SCOPE_EXPANSION = "UNION_SCOPE_EXPANSION"
    SLICING_PRESSURE = "SLICING_PRESSURE"
    PHRASING_GAME_TAX = "PHRASING_GAME_TAX"
    INTENT_ENUM_TOO_NARROW = "INTENT_ENUM_TOO_NARROW"
    CLOSED_PACKET_STILL_LOAD_BEARING = "CLOSED_PACKET_STILL_LOAD_BEARING"
    ADVISORY_OUTPUT_INVISIBILITY = "ADVISORY_OUTPUT_INVISIBILITY"


@dataclass(frozen=True)
class IssueRecord:
    """
    A single structured issue produced during admission.

    Severity drives routing; code + message drive human-readable diagnostics.
    metadata holds issue-specific context (profile, file, category).
    """

    code: str
    path: str
    severity: Severity
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "severity": self.severity.value,
            "message": self.message,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class DiagnosisEntry:
    """
    Failure-as-diagnosis output per Universal §12.

    Turns a blocked admission into actionable information.
    """

    pattern: FrictionPattern
    evidence: str
    resolution_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern.value,
            "evidence": self.evidence,
            "resolution_path": self.resolution_path,
        }


@dataclass(frozen=True)
class AdmissionDecision:
    """
    Full admission decision per Universal §2.3 and §11.

    ok=True with non-empty issues is a PASS-WITH-CONDITIONS, not a clean pass.
    Callers must surface all ADVISORY and above issues before proceeding.
    """

    ok: bool
    profile_matched: str | None
    intent_class: Intent
    severity: Severity
    issues: tuple[IssueRecord, ...]
    companion_files: tuple[str, ...]
    missing_phrases: tuple[str, ...]
    closest_rejected_profile: str | None
    friction_budget_used: int
    diagnosis: DiagnosisEntry | None
    kernel_alerts: tuple[IssueRecord, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable dict. All issues appear at top level."""
        return {
            "ok": self.ok,
            "profile_matched": self.profile_matched,
            "intent_class": self.intent_class.value,
            "severity": self.severity.value,
            "issues": [i.to_dict() for i in self.issues],
            "companion_files": list(self.companion_files),
            "missing_phrases": list(self.missing_phrases),
            "closest_rejected_profile": self.closest_rejected_profile,
            "friction_budget_used": self.friction_budget_used,
            "diagnosis": self.diagnosis.to_dict() if self.diagnosis else None,
            "kernel_alerts": [i.to_dict() for i in self.kernel_alerts],
        }


@dataclass(frozen=True)
class CoverageMap:
    """
    File-to-profile coverage declaration per Universal §6.

    Every file must be in exactly one of: profiles, orphaned, hard_stop_paths.
    A file in none of these three is a coverage gap (ADVISORY).
    """

    profiles: dict[str, tuple[str, ...]]
    orphaned: tuple[str, ...]
    hard_stop_paths: tuple[str, ...]


@dataclass(frozen=True)
class CohortDecl:
    """
    Named cohort of files per Universal §8.

    A cohort admits its entire file set under the governing profile when
    intent matches and all files are present.
    """

    id: str
    profile: str
    intent_classes: tuple[Intent, ...]
    files: tuple[str, ...]
    description: str


@dataclass(frozen=True)
class BindingLayer:
    """
    Project binding layer per ZEUS_BINDING_LAYER.md.

    The single typed container for all project-specific topology configuration.
    Loaded via profile_loader.load_binding_layer().

    P2.1 additive fields (SCAFFOLD §2.2, §0 INCONSISTENCY-1 resolution):
    - companion_required: profile_id → tuple of authority-doc relative paths
    - companion_skip_tokens: profile_id → exact token string for skip-token short-circuit
    Both default to empty dicts so existing P1 binding YAML loads without modification.
    """

    project_id: str
    intent_extensions: tuple[Intent, ...]
    coverage_map: CoverageMap
    cohorts: tuple[CohortDecl, ...]
    severity_overrides: dict[str, Severity]
    high_fanout_hints: tuple[dict[str, Any], ...]
    artifact_authority_status: dict[str, dict[str, Any]]
    # P2.1 additive fields — default empty so P1 YAML loads unchanged
    companion_required: dict[str, tuple[str, ...]] = field(default_factory=dict)
    companion_skip_tokens: dict[str, str] = field(default_factory=dict)
