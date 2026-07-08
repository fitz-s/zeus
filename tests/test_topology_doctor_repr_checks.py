"""Tests for topology_doctor_repr_checks -- the --repr representation-contract
checker family. Coverage: comment-law banned patterns, stale file/symbol
references, naming-law forbidden aliases, metadata-law drift-detector
completeness, anchor-law bidirectional lint. All checks are advisory
(never raise, never gate exit code) per contract Sec 4.
"""
from __future__ import annotations

import subprocess
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from scripts.topology_doctor_repr_checks import (
    build_repo_symbol_index,
    check_agents_token_budgets,
    check_anchor_bidirectional,
    check_banned_comment_patterns,
    check_forbidden_aliases_in_new_defs,
    check_metadata_row_drift_detector,
    check_stale_file_reference_comments,
    check_stale_symbol_reference_comments,
    run_repr,
)


def _make_api(root: Path, tracked_files: list[str] | None = None) -> Any:
    api = MagicMock()
    api.ROOT = root
    api._git_ls_files.return_value = tracked_files or []
    return api


# --- comment law: banned patterns -------------------------------------------


def test_lifecycle_header_prose_is_banned():
    text = "x = 1\n# Created: 2026-06-14\n# audited: 2026-06-29\n"
    findings = check_banned_comment_patterns(MagicMock(), "f.py", text)
    codes = {f["code"] for f in findings}
    assert "repr_banned_lifecycle_header_comment" in codes


def test_dated_incident_narrative_is_banned():
    text = "# 2026-06-14 hotfix for the settlement race\nx = 1\n"
    findings = check_banned_comment_patterns(MagicMock(), "f.py", text)
    codes = {f["code"] for f in findings}
    assert "repr_dated_incident_narrative_comment" in codes


def test_authority_claim_is_banned():
    text = "# ONLY decision authority for this branch\nx = 1\n"
    findings = check_banned_comment_patterns(MagicMock(), "f.py", text)
    codes = {f["code"] for f in findings}
    assert "repr_authority_claim_comment" in codes


def test_ordinary_comment_is_clean():
    text = "# units: Celsius, per WU station convention\nx = 1\n"
    findings = check_banned_comment_patterns(MagicMock(), "f.py", text)
    assert findings == []


# --- comment law: stale file/symbol references ------------------------------


def test_stale_file_reference_is_flagged(tmp_path: Path):
    api = _make_api(tmp_path)
    text = "# see src/nonexistent/ghost_module.py for details\nx = 1\n"
    findings = check_stale_file_reference_comments(api, "f.py", text)
    assert len(findings) == 1
    assert findings[0]["code"] == "repr_stale_file_reference_comment"


def test_existing_file_reference_is_not_flagged(tmp_path: Path):
    api = _make_api(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "real_module.py").write_text("pass\n")
    text = "# see src/real_module.py for details\nx = 1\n"
    findings = check_stale_file_reference_comments(api, "f.py", text)
    assert findings == []


def test_stale_symbol_reference_is_flagged():
    api = MagicMock()
    text = "# see `ghost_helper()` for the old code path\nx = 1\n"
    findings = check_stale_symbol_reference_comments(api, "f.py", text, symbol_index={"real_helper"})
    assert len(findings) == 1
    assert findings[0]["code"] == "repr_stale_symbol_reference_comment"


def test_existing_symbol_reference_is_not_flagged():
    api = MagicMock()
    text = "# see `real_helper()` for the logic\nx = 1\n"
    findings = check_stale_symbol_reference_comments(api, "f.py", text, symbol_index={"real_helper"})
    assert findings == []


def test_build_repo_symbol_index(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def foo():\n    pass\n\n\nclass Bar:\n    pass\n")
    api = _make_api(tmp_path, tracked_files=["mod.py"])
    names = build_repo_symbol_index(api)
    assert names == {"foo", "Bar"}


# --- naming law: forbidden aliases in NEW defs -------------------------------


def _vocab() -> dict[str, Any]:
    return {
        "terms": [
            {"concept_id": "prob.q", "canonical": "q", "forbidden_aliases": ["posterior", "belief"]},
            {"concept_id": "prob.posterior", "canonical": "posterior", "forbidden_aliases": ["belief", "q"]},
        ]
    }


def test_forbidden_alias_in_new_def_is_flagged():
    text = "def compute_belief(x):\n    return x\n"
    findings = check_forbidden_aliases_in_new_defs(MagicMock(), "f.py", text, _vocab())
    assert len(findings) == 1
    assert findings[0]["code"] == "repr_forbidden_alias_in_new_def"


def test_canonical_name_itself_is_not_flagged_as_alias():
    # "posterior" is forbidden_alias of q, but it is ALSO the canonical name of
    # prob.posterior -- a purely lexical match cannot tell which concept is meant,
    # so it must not be flagged (see _forbidden_alias_index docstring).
    text = "def posterior(x):\n    return x\n"
    findings = check_forbidden_aliases_in_new_defs(MagicMock(), "f.py", text, _vocab())
    assert findings == []


def test_clean_def_is_not_flagged():
    text = "def compute_q(x):\n    return x\n"
    findings = check_forbidden_aliases_in_new_defs(MagicMock(), "f.py", text, _vocab())
    assert findings == []


# --- metadata law: enforced_by / tests completeness --------------------------


def test_metadata_row_missing_enforced_by_is_flagged(tmp_path: Path):
    arch = tmp_path / "architecture"
    arch.mkdir()
    (arch / "invariants.yaml").write_text(
        "invariants:\n"
        "  - id: INV-01\n"
        "    statement: has a checker\n"
        "    enforced_by:\n"
        "      tests: [tests/test_x.py::test_y]\n"
        "  - id: INV-02\n"
        "    statement: no checker at all\n"
    )
    (arch / "negative_constraints.yaml").write_text("constraints: []\n")
    (arch / "fatal_misreads.yaml").write_text("misreads: []\n")
    api = _make_api(tmp_path)
    findings = check_metadata_row_drift_detector(api)
    paths = {f["path"] for f in findings}
    assert "architecture/invariants.yaml#INV-02" in paths
    assert "architecture/invariants.yaml#INV-01" not in paths


def test_fatal_misread_missing_tests_is_flagged(tmp_path: Path):
    arch = tmp_path / "architecture"
    arch.mkdir()
    (arch / "invariants.yaml").write_text("invariants: []\n")
    (arch / "negative_constraints.yaml").write_text("constraints: []\n")
    (arch / "fatal_misreads.yaml").write_text(
        "misreads:\n  - id: some_misread\n    tests: []\n"
    )
    api = _make_api(tmp_path)
    findings = check_metadata_row_drift_detector(api)
    assert any(f["path"] == "architecture/fatal_misreads.yaml#some_misread" for f in findings)


# --- anchor law: bidirectional lint (real tmp git repo) ----------------------


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "wip"], cwd=root, check=True)


def test_anchor_bidirectional_flags_unregistered_and_unpadded_and_unreferenced(tmp_path: Path):
    root = tmp_path
    _init_repo(root)
    arch = root / "architecture"
    arch.mkdir()
    (arch / "invariants.yaml").write_text(
        "invariants:\n"
        "  - id: INV-01\n"
        "    statement: referenced correctly\n"
        "  - id: INV-02\n"
        "    statement: never referenced anywhere\n"
    )
    (arch / "failure_chains.yaml").write_text("chains: {}\n")
    (root / "src.py").write_text(
        "# INV-01 governs this branch\n"
        "# INV-1 unpadded reference to the same rule\n"
        "# INV-99 does not exist in the registry\n"
    )
    _commit_all(root)

    api = types.SimpleNamespace(ROOT=root)
    api.load_invariants = lambda: {
        "invariants": [
            {"id": "INV-01", "statement": "x"},
            {"id": "INV-02", "statement": "y"},
        ]
    }
    findings = check_anchor_bidirectional(api)
    codes_paths = {(f["code"], f["path"]) for f in findings}

    assert ("repr_anchor_unpadded_form", "src.py:2") in codes_paths
    assert any(c == "repr_anchor_unregistered_reference" and "src.py:3" in p for c, p in codes_paths)
    assert any(
        c == "repr_anchor_registered_no_reference" and p == "architecture/invariants.yaml#INV-02"
        for c, p in codes_paths
    )
    # INV-01 was correctly referenced -- must not appear as registered_no_reference
    assert not any(
        c == "repr_anchor_registered_no_reference" and p.endswith("#INV-01") for c, p in codes_paths
    )


# --- entry point / advisory contract -----------------------------------------


def test_run_repr_is_always_advisory_ok(tmp_path: Path):
    root = tmp_path
    _init_repo(root)
    arch = root / "architecture"
    arch.mkdir()
    (arch / "invariants.yaml").write_text("invariants: []\n")
    (arch / "failure_chains.yaml").write_text("chains: {}\n")
    (arch / "negative_constraints.yaml").write_text("constraints: []\n")
    (arch / "fatal_misreads.yaml").write_text("misreads: []\n")
    (root / "bad.py").write_text("# ONLY decision authority here\ndef posterior():\n    pass\n")
    _commit_all(root)

    api = types.SimpleNamespace(ROOT=root)
    api.load_invariants = lambda: {"invariants": []}
    api.load_canonical_vocabulary = lambda: _vocab()
    api._git_ls_files = lambda: subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.splitlines()

    def _changes(files):
        return {"bad.py": "modified"}

    api._map_maintenance_changes = _changes

    result = run_repr(api)
    assert result["ok"] is True
    assert result["advisory"] is True
    assert result["finding_count"] > 0


def test_check_agents_token_budgets_reports_all_agents_md(tmp_path: Path):
    root = tmp_path
    (root / "AGENTS.md").write_text("short\n")
    scoped = root / "src"
    scoped.mkdir()
    (scoped / "AGENTS.md").write_text("x" * 3000)
    api = _make_api(root, tracked_files=["AGENTS.md", "src/AGENTS.md"])
    findings = check_agents_token_budgets(api)
    by_path = {f["path"]: f for f in findings}
    assert by_path["AGENTS.md"]["severity"] == "info"
    assert by_path["src/AGENTS.md"]["severity"] == "warning"
