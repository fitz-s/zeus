# Created: 2026-05-22
# Last reused or audited: 2026-05-22
# Authority basis: architecture/file_arrangement.yaml; PR-T0 advisory file-arrangement kernel brief
"""
Tests for the PR-T0 advisory file-arrangement kernel.

Core invariant: every ArrangementFinding has blocking=False.
Audit exit is always 0 on findings; only crashes produce non-zero.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _import_module():
    """Import file_arrangement module from either installed or worktree path."""
    try:
        from scripts.topology_v_next import file_arrangement
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
        from topology_v_next import file_arrangement  # type: ignore
    return file_arrangement


def _make_minimal_repo(tmp_path: Path) -> Path:
    """
    Build a minimal synthetic repo structure with a real file_arrangement.yaml.

    Uses the actual manifest from the worktree so tests always reflect the
    shipped manifest schema, not a stale copy.
    """
    # Copy real manifest
    real_manifest = Path(__file__).parents[2] / "architecture" / "file_arrangement.yaml"
    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir()
    shutil.copy(real_manifest, arch_dir / "file_arrangement.yaml")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Advisory invariant: constructor raises on blocking=True
# ---------------------------------------------------------------------------

class TestArrangementFindingAdvisoryInvariant:
    """Structural antibody: ArrangementFinding rejects blocking=True at construction."""

    def test_blocking_true_raises_value_error(self):
        fa = _import_module()
        with pytest.raises(ValueError, match="blocking=True"):
            fa.ArrangementFinding(
                code="test_violation",
                path="some/path.md",
                severity="warn",
                blocking=True,  # MUST raise
                artifact_kind="operation_plan",
                recommended_path="docs/operations/current/plans/foo/PLAN.md",
                reason="This construction must be rejected.",
            )

    def test_blocking_false_constructs_fine(self):
        fa = _import_module()
        finding = fa.ArrangementFinding(
            code="test_ok",
            path="some/path.md",
            severity="warn",
            blocking=False,
            artifact_kind="operation_plan",
            recommended_path="docs/operations/current/plans/foo/PLAN.md",
            reason="This is always advisory.",
        )
        assert finding.blocking is False

    def test_default_followups_and_evidence_are_tuples(self):
        fa = _import_module()
        finding = fa.ArrangementFinding(
            code="c",
            path="p",
            severity="info",
            blocking=False,
            artifact_kind="durable_reference",
            recommended_path="p",
            reason="r",
        )
        assert isinstance(finding.followups, tuple)
        assert isinstance(finding.evidence, tuple)


# ---------------------------------------------------------------------------
# 2. Advisory invariant over ALL audit findings
# ---------------------------------------------------------------------------

class TestEveryFindingIsAdvisory:
    """Proof: no audit code path can emit a blocking finding."""

    def test_every_file_arrangement_finding_is_advisory(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)

        # Synthetic structure: task dir at docs/operations/ top level
        ops = repo / "docs" / "operations"
        ops.mkdir(parents=True)
        task_dir = ops / "task_2026-05-22_test-slug"
        task_dir.mkdir()
        (task_dir / "PLAN.md").write_text("# Plan\n")
        # No scope.yaml — triggers plan_missing_scope_sidecar advisory
        # No current/ dir — triggers current_package_missing advisory

        manifest = fa.load_file_arrangement_manifest(repo)
        findings = fa.audit_file_arrangement(repo, manifest)

        # Must have at least one finding (current_package_missing etc.)
        assert len(findings) > 0, "Expected at least one advisory finding"

        # THE CORE INVARIANT: every finding must be advisory (blocking=False)
        for f in findings:
            assert f.blocking is False, (
                f"INVARIANT VIOLATION: finding {f.code!r} for {f.path!r} "
                f"has blocking=True. The file-arrangement kernel never blocks."
            )
            assert f.severity in ("info", "warn"), (
                f"finding {f.code!r} has unexpected severity {f.severity!r}"
            )

    def test_audit_on_empty_repo_is_advisory(self, tmp_path):
        """Even a bare repo with just the manifest returns only advisory findings."""
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        # No docs/operations/ — should still not crash
        manifest = fa.load_file_arrangement_manifest(repo)
        findings = fa.audit_file_arrangement(repo, manifest)
        for f in findings:
            assert f.blocking is False


# ---------------------------------------------------------------------------
# 3. load_file_arrangement_manifest
# ---------------------------------------------------------------------------

class TestLoadManifest:
    def test_loads_real_manifest(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        assert manifest.get("schema_version") == 1
        # enforcement lives under metadata
        assert manifest.get("metadata", {}).get("enforcement") == "advisory"
        assert isinstance(manifest.get("artifact_kinds"), list)
        assert isinstance(manifest.get("rules"), list)

    def test_missing_manifest_returns_empty_dict(self, tmp_path):
        fa = _import_module()
        # No architecture/file_arrangement.yaml
        manifest = fa.load_file_arrangement_manifest(tmp_path)
        assert manifest == {}

    def test_enforcement_is_advisory(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        assert manifest["metadata"]["enforcement"] == "advisory"

    def test_all_rules_are_non_blocking(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        for rule in manifest.get("rules", []):
            assert rule.get("blocking") is False, (
                f"Rule {rule.get('id')!r} has blocking={rule.get('blocking')!r}; "
                "all rules must be non-blocking (advisory)"
            )


# ---------------------------------------------------------------------------
# 4. classify_artifact
# ---------------------------------------------------------------------------

class TestClassifyArtifact:
    def test_classifies_known_plan(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        # PLAN.md under a task dir
        kind = fa.classify_artifact(
            "docs/operations/task_2026-05-22_slug/PLAN.md", manifest
        )
        assert kind != "unknown", f"Expected a known kind, got {kind!r}"

    def test_returns_unknown_for_unrecognized(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        kind = fa.classify_artifact("completely/random/path_xyz123.ext", manifest)
        assert kind == "unknown"


# ---------------------------------------------------------------------------
# 5. explain_path
# ---------------------------------------------------------------------------

class TestExplainPath:
    def test_explain_returns_advisory_finding(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        result = fa.explain_path(
            "docs/operations/task_2026-05-01_foo/PLAN.md", repo, manifest
        )
        assert isinstance(result, fa.ArrangementFinding)
        assert result.blocking is False

    def test_explain_unclassified_path_is_advisory_warn(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        result = fa.explain_path("xyz/totally/unknown/file.rnd", repo, manifest)
        assert result.blocking is False
        assert result.severity == "warn"
        assert result.code == "path_unclassified"


# ---------------------------------------------------------------------------
# 6. recommend_path
# ---------------------------------------------------------------------------

class TestRecommendPath:
    def test_recommend_returns_a_path(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        result = fa.recommend_path(
            artifact_kind="operation_plan",
            slug="my-task",
            filename="PLAN.md",
            root=repo,
            manifest=manifest,
        )
        assert isinstance(result, Path)

    def test_recommend_unknown_kind_returns_unknown_subdir(self, tmp_path):
        fa = _import_module()
        repo = _make_minimal_repo(tmp_path)
        manifest = fa.load_file_arrangement_manifest(repo)
        result = fa.recommend_path(
            artifact_kind="totally_unknown_kind",
            slug="slug",
            filename="file.md",
            root=repo,
            manifest=manifest,
        )
        # Should not crash; returns some path under root
        assert isinstance(result, Path)
        assert str(result).startswith(str(repo))


# ---------------------------------------------------------------------------
# 7. to_dict serialization
# ---------------------------------------------------------------------------

class TestFindingToDict:
    def test_to_dict_is_json_serializable(self):
        fa = _import_module()
        finding = fa.ArrangementFinding(
            code="manifest_missing",
            path="architecture/file_arrangement.yaml",
            severity="warn",
            blocking=False,
            artifact_kind="unknown",
            recommended_path="architecture/file_arrangement.yaml",
            reason="manifest not found",
            followups=("Create it",),
            evidence=(),
        )
        d = finding.to_dict()
        # Must serialize cleanly (no sets, no Path objects)
        serialized = json.dumps(d)
        assert '"blocking": false' in serialized
        assert '"severity": "warn"' in serialized
