# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: architecture/file_arrangement.yaml; PR-T0 advisory file-arrangement kernel brief
"""
Advisory file-arrangement kernel for the topology v_next system.

Tells agents WHERE a file belongs and WHAT to read.
NEVER blocks. Every finding has blocking=False (structurally enforced in __post_init__).
Audit exits 0 on warnings; only crashes produce non-zero exit.

Public:
    load_file_arrangement_manifest(root) -> dict
    classify_artifact(path, manifest) -> str
    recommend_path(*, artifact_kind, slug, filename, root, manifest) -> Path
    audit_file_arrangement(root, manifest) -> list[ArrangementFinding]
    explain_path(path, root, manifest) -> ArrangementFinding

Codex-importable: stdlib + PyYAML only.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Core dataclass — structural advisory invariant enforced in __post_init__
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArrangementFinding:
    """
    A single advisory finding from the file-arrangement kernel.

    blocking is ALWAYS False. __post_init__ raises if a caller attempts
    to construct a finding with blocking=True — making the category impossible.
    """

    code: str
    path: str
    severity: Literal["info", "warn"]
    blocking: bool
    artifact_kind: str
    recommended_path: str
    reason: str
    followups: tuple[str, ...] = field(default_factory=tuple)
    evidence: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.blocking:
            raise ValueError(
                f"ArrangementFinding.blocking must always be False "
                f"(advisory invariant). Got blocking=True for code={self.code!r}, "
                f"path={self.path!r}. The file-arrangement kernel never blocks."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "severity": self.severity,
            "blocking": self.blocking,
            "artifact_kind": self.artifact_kind,
            "recommended_path": self.recommended_path,
            "reason": self.reason,
            "followups": list(self.followups),
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_file_arrangement_manifest(root: Path) -> dict[str, Any]:
    """
    Load and return the parsed architecture/file_arrangement.yaml manifest.

    Returns an empty dict if the manifest does not exist (advisory; no crash).
    """
    manifest_path = root / "architecture" / "file_arrangement.yaml"
    if not manifest_path.exists():
        return {}
    try:
        import yaml  # PyYAML
    except ImportError:
        try:
            import scripts.topology_v_next._yaml_shim as yaml  # type: ignore[no-redef]
        except ImportError:
            return {}
    with manifest_path.open() as fh:
        return yaml.safe_load(fh) or {}


def classify_artifact(path: str, manifest: dict[str, Any]) -> str:
    """
    Return the artifact_kind id for *path*, or "unknown" if unclassified.

    Matches against canonical_path patterns and legacy_paths glob patterns
    for each artifact_kind declared in the manifest.
    """
    kinds = manifest.get("artifact_kinds") or []
    for kind in kinds:
        canonical = kind.get("canonical_path", "")
        legacy = kind.get("legacy_paths") or []
        all_patterns = [canonical] + list(legacy)
        for pattern in all_patterns:
            if _glob_match(path, pattern):
                return kind["id"]
    return "unknown"


def recommend_path(
    *,
    artifact_kind: str,
    slug: str,
    filename: str,
    root: Path,
    manifest: dict[str, Any],
) -> Path:
    """
    Return the recommended canonical path for an artifact of *artifact_kind*.

    Substitutes <slug>, <file>, <topic>, <date>, <ts> template vars.
    Returns root / canonical_path if found; root / "unknown" / filename otherwise.
    """
    kinds = manifest.get("artifact_kinds") or []
    for kind in kinds:
        if kind.get("id") == artifact_kind:
            canonical = kind.get("canonical_path", "")
            resolved = (
                canonical
                .replace("<slug>", slug)
                .replace("<file>", filename)
                .replace("<topic>", slug)
                .replace("<date>", "YYYY-MM-DD")
                .replace("<ts>", "YYYYMMDD_HHmmss")
            )
            return root / resolved
    return root / "unknown" / filename


def audit_file_arrangement(
    root: Path,
    manifest: dict[str, Any],
) -> list[ArrangementFinding]:
    """
    Scan the repo tree and return advisory findings per file_arrangement.yaml rules.

    Handles docs/operations/current/ NOT existing yet (T1 creates it) — emits
    advisory finding, does not crash.

    Exit behavior: caller must exit 0 regardless of finding count.
    Only crashes (exceptions) should produce non-zero exit codes.
    """
    findings: list[ArrangementFinding] = []

    if not manifest:
        findings.append(ArrangementFinding(
            code="manifest_missing",
            path="architecture/file_arrangement.yaml",
            severity="warn",
            blocking=False,
            artifact_kind="unknown",
            recommended_path="architecture/file_arrangement.yaml",
            reason="file_arrangement.yaml manifest not found; skipping audit",
            followups=("Create architecture/file_arrangement.yaml",),
            evidence=(),
        ))
        return findings

    rules = {r["id"]: r for r in (manifest.get("rules") or [])}

    # Check current/ package exists
    current_dir = root / "docs" / "operations" / "current"
    if not current_dir.exists():
        findings.append(ArrangementFinding(
            code="current_package_missing",
            path="docs/operations/current/",
            severity="warn",
            blocking=False,
            artifact_kind="active_task_ledger",
            recommended_path="docs/operations/current/package.yaml",
            reason="docs/operations/current/ does not exist yet (T1 creates it). Advisory only.",
            followups=("T1 will create docs/operations/current/",),
            evidence=(),
        ))

    # Check current_state.md pointer
    current_state = root / "docs" / "operations" / "current_state.md"
    if current_state.exists():
        text = current_state.read_text(encoding="utf-8", errors="replace")
        if "current/package.yaml" not in text and "current\\package.yaml" not in text:
            rule = rules.get("current_state_must_point_to_active_package", {})
            findings.append(ArrangementFinding(
                code="current_state_missing_package_pointer",
                path="docs/operations/current_state.md",
                severity=rule.get("severity", "warn"),  # type: ignore[arg-type]
                blocking=False,
                artifact_kind="current_pointer",
                recommended_path="docs/operations/current_state.md",
                reason=(
                    rule.get("description", "current_state.md should reference current/package.yaml")
                    .strip()
                ),
                followups=("Add pointer to docs/operations/current/package.yaml",),
                evidence=(),
            ))

    # Scan docs/operations/ for top-level task dirs
    ops_dir = root / "docs" / "operations"
    if ops_dir.exists():
        for child in ops_dir.iterdir():
            if not child.is_dir():
                continue
            name = child.name
            if not _is_task_dir(name):
                continue
            # Advisory: top-level task dir detected
            rule = rules.get("no_new_top_level_operation_task_by_default", {})
            findings.append(ArrangementFinding(
                code="top_level_task_dir_advisory",
                path=f"docs/operations/{name}/",
                severity=rule.get("severity", "warn"),  # type: ignore[arg-type]
                blocking=False,
                artifact_kind="operation_plan",
                recommended_path=f"docs/operations/current/plans/{_slug_from_task_dir(name)}/PLAN.md",
                reason=(
                    rule.get("description",
                             "Top-level task dirs are advisory-discouraged; prefer current/plans/<slug>/")
                    .strip()
                ),
                followups=(
                    f"Consider migrating to docs/operations/current/plans/{_slug_from_task_dir(name)}/",
                ),
                evidence=(f"docs/operations/{name}/ exists as top-level dir",),
            ))
            # Check for PLAN.md without scope.yaml
            plan_files = list(child.rglob("PLAN.md"))
            for plan in plan_files:
                sibling_scope = plan.parent / "scope.yaml"
                if not sibling_scope.exists():
                    rule2 = rules.get("plan_requires_scope_sidecar", {})
                    rel_plan = str(plan.relative_to(root))
                    findings.append(ArrangementFinding(
                        code="plan_missing_scope_sidecar",
                        path=rel_plan,
                        severity=rule2.get("severity", "warn"),  # type: ignore[arg-type]
                        blocking=False,
                        artifact_kind="operation_plan",
                        recommended_path=str((plan.parent / "scope.yaml").relative_to(root)),
                        reason=(
                            rule2.get("description",
                                      "operation_plan requires a sibling scope.yaml")
                            .strip()
                        ),
                        followups=(f"Create {plan.parent.relative_to(root)}/scope.yaml",),
                        evidence=(rel_plan,),
                    ))

            # Check for generated reports missing non-authority marker
            for report_file in child.rglob("*.md"):
                rel = str(report_file.relative_to(root))
                kind_id = classify_artifact(rel, manifest)
                if kind_id in ("operation_report", "generated_report"):
                    content = report_file.read_text(encoding="utf-8", errors="replace")
                    if not _has_non_authority_marker(content):
                        rule3 = rules.get("generated_report_must_declare_non_authority", {})
                        findings.append(ArrangementFinding(
                            code="generated_report_missing_non_authority",
                            path=rel,
                            severity=rule3.get("severity", "warn"),  # type: ignore[arg-type]
                            blocking=False,
                            artifact_kind=kind_id,
                            recommended_path=rel,
                            reason=(
                                rule3.get("description",
                                          "Generated reports must declare non-authority near top")
                                .strip()
                            ),
                            followups=('Add "authority: false" or non-authority note near top of file',),
                            evidence=(rel,),
                        ))

    return findings


def explain_path(
    path: str,
    root: Path,
    manifest: dict[str, Any],
) -> ArrangementFinding:
    """
    Return an advisory ArrangementFinding describing WHERE *path* belongs.

    Never raises; always returns a single finding (info or warn).
    """
    kind_id = classify_artifact(path, manifest)
    if kind_id == "unknown":
        return ArrangementFinding(
            code="path_unclassified",
            path=path,
            severity="warn",
            blocking=False,
            artifact_kind="unknown",
            recommended_path="unknown",
            reason=(
                f"'{path}' does not match any artifact_kind in file_arrangement.yaml. "
                "Advisory: classify it or add a pattern to the manifest."
            ),
            followups=("Add matching pattern to architecture/file_arrangement.yaml",),
            evidence=(),
        )

    kinds = manifest.get("artifact_kinds") or []
    kind_meta = next((k for k in kinds if k.get("id") == kind_id), {})
    canonical = kind_meta.get("canonical_path", "")
    is_legacy = _is_legacy_path(path, kind_meta)
    severity: Literal["info", "warn"] = "warn" if is_legacy else "info"
    reason_parts = [f"'{path}' is classified as artifact_kind '{kind_id}'."]
    if canonical:
        reason_parts.append(f"Canonical path template: {canonical}")
    if is_legacy:
        reason_parts.append("This is a legacy path; consider migrating to the canonical template.")
    followups: tuple[str, ...]
    if is_legacy:
        followups = (f"Migrate to canonical pattern: {canonical}",)
    else:
        followups = ()

    return ArrangementFinding(
        code="path_classified",
        path=path,
        severity=severity,
        blocking=False,
        artifact_kind=kind_id,
        recommended_path=canonical,
        reason=" ".join(reason_parts),
        followups=followups,
        evidence=(),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _glob_match(path: str, pattern: str) -> bool:
    """Match path against a pattern that may contain <template> vars or ** globs."""
    # Normalize template vars to fnmatch wildcards
    normalized = (
        pattern
        .replace("<slug>", "*")
        .replace("<file>", "*")
        .replace("<topic>", "*")
        .replace("<date>", "*")
        .replace("<ts>", "*")
    )
    if fnmatch.fnmatch(path, normalized):
        return True
    # Handle /**  suffix
    if normalized.endswith("/**"):
        prefix = normalized[:-3]
        if path == prefix or path.startswith(prefix + "/"):
            return True
    # Handle trailing / (directory prefix)
    if normalized.endswith("/") and path.startswith(normalized):
        return True
    return False


def _is_task_dir(name: str) -> bool:
    """Return True if *name* looks like task_YYYY-MM-DD_<slug>."""
    return name.startswith("task_") and len(name) > 16


def _slug_from_task_dir(name: str) -> str:
    """Extract slug portion from task_YYYY-MM-DD_<slug>."""
    parts = name.split("_", 3)
    # task_{date_part1}_{date_part2}_{slug}  — or simpler task_YYYY-MM-DD_slug
    if len(parts) >= 4:
        return parts[3]
    if len(parts) == 3:
        return parts[2]
    return name


def _is_legacy_path(path: str, kind_meta: dict[str, Any]) -> bool:
    """Return True if path matches a legacy_path pattern (not canonical)."""
    legacy = kind_meta.get("legacy_paths") or []
    for pattern in legacy:
        if _glob_match(path, pattern):
            return True
    return False


def _has_non_authority_marker(content: str) -> bool:
    """Return True if content contains an explicit non-authority declaration."""
    markers = [
        "authority: false",
        "authority:false",
        "non-authority",
        "not an authority",
        "This is a generated report",
        "generated report",
        "non_authority",
    ]
    lower = content[:2000].lower()
    return any(m.lower() in lower for m in markers)
