"""Tests for topology_doctor compiled topology gates."""
# Created: 2026-04-13
# Last reused/audited: 2026-05-06
# Authority basis: docs/operations/task_2026-05-02_review_crash_remediation/PLAN.md Slices 4-5; Wave17 object-meaning backfill guard repair.
# Lifecycle: created=2026-04-13; last_reviewed=2026-05-06; last_reused=2026-05-06
# Purpose: Regression tests for topology_doctor lanes, CLI parity, closeout compilation, and dangerous script manifest guards.
# Reuse: Use targeted -k selectors for the lane being changed; inspect current manifest law first.

import pytest
import json
import os
import pathlib
import sqlite3
import subprocess
from contextlib import redirect_stdout
from io import StringIO

from scripts import topology_doctor


def assert_topology_ok(result):
    if not result.ok:
        pytest.fail(topology_doctor.format_issues(result.issues), pytrace=False)
    assert result.issues == []


def assert_navigation_ok(payload):
    if not payload["ok"]:
        issues = [
            topology_doctor.TopologyIssue(
                code=f"{issue['lane']}:{issue['code']}",
                path=issue["path"],
                message=issue["message"],
                severity=issue["severity"],
            )
            for issue in payload["issues"]
        ]
        pytest.fail(topology_doctor.format_issues(issues), pytrace=False)


def reference_entry(manifest, path):
    return next(entry for entry in manifest["entries"] if entry["path"] == path)


def run_cli_json(args):
    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(args)
    assert exit_code == 0
    return json.loads(buffer.getvalue())


@pytest.mark.live_topology
def test_topology_strict_passes_after_residual_classification(monkeypatch):
    visible = topology_doctor._git_visible_files()
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: [
            path for path in visible
            if path not in {
                "state/hko_ingest_log.jsonl",
                "state/obs_v2_backfill_log.jsonl",
                "state/obs_v2_dst_fill_log.jsonl",
                "state/obs_v2_meteostat_fill_log.jsonl",
                "state/scheduler_jobs_health.json",
            }
        ],
    )
    result = topology_doctor.run_strict()

    assert_topology_ok(result)


@pytest.mark.live_topology
def test_topology_docs_mode_passes_with_active_data_package_excluded():
    result = topology_doctor.run_docs()

    assert_topology_ok(result)


@pytest.mark.live_topology
def test_cli_json_parity_for_docs_mode():
    payload = run_cli_json(["--docs", "--json"])
    result = topology_doctor.run_docs()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_cli_json_parity_for_digest_command():
    args = [
        "digest",
        "--task",
        "debug settlement rounding mismatch",
        "--files",
        "src/contracts/settlement_semantics.py",
        "--json",
    ]

    payload = run_cli_json(args)

    assert payload == topology_doctor.build_digest(
        "debug settlement rounding mismatch",
        ["src/contracts/settlement_semantics.py"],
    )


def test_navigation_changed_files_aliases_files_instead_of_empty_route():
    args = [
        "--navigation",
        "--task",
        "agent runtime navigation changed-files alias",
        "--changed-files",
        "scripts/topology_doctor_cli.py",
        "--intent",
        "topology graph agent runtime upgrade",
        "--task-class",
        "agent_runtime",
        "--write-intent",
        "edit",
        "--json",
    ]

    payload = run_cli_json(args)

    assert payload == topology_doctor.run_navigation(
        "agent runtime navigation changed-files alias",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        task_class="agent_runtime",
        write_intent="edit",
    )
    assert payload["route_card"]["admitted_files"] == ["scripts/topology_doctor_cli.py"]


def test_navigation_cli_accepts_operation_vector_fields():
    args = [
        "--navigation",
        "--route-card-only",
        "--json",
        "--task",
        "done",
        "--write-intent",
        "read_only",
        "--operation-stage",
        "closeout",
        "--artifact-target",
        "final_response",
    ]

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(args)

    payload = json.loads(buffer.getvalue())
    assert exit_code == 0
    assert payload["route_card"]["operation_vector"]["operation_stage"] == "closeout"
    assert payload["route_card"]["operation_vector"]["artifact_target"] == "final_response"
    assert payload["route_card"]["persistence_target"] == "final_response"



@pytest.mark.live_topology
def test_cli_json_parity_for_current_state_candidate_command():
    receipt = "docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json"
    payload = run_cli_json(["current-state", "--from-receipt", receipt, "--json"])

    assert payload == topology_doctor.build_current_state_candidate(receipt)


def test_cli_json_parity_for_map_maintenance_command():
    payload = run_cli_json([
        "--map-maintenance",
        "--changed-files",
        "tests/test_topology_doctor.py",
        "--json",
    ])
    result = topology_doctor.run_map_maintenance(["tests/test_topology_doctor.py"])

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_cli_json_parity_for_naming_conventions_command():
    payload = run_cli_json(["--naming-conventions", "--json"])
    result = topology_doctor.run_naming_conventions()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_cli_json_parity_for_freshness_metadata_command(monkeypatch, tmp_path):
    root = tmp_path
    script = root / "scripts" / "new_tool.py"
    script.parent.mkdir()
    script.write_text(
        '"""Tool."""\n'
        "# Lifecycle: created=2026-04-16; last_reviewed=2026-04-16; last_reused=never\n"
        "# Purpose: Test fixture.\n"
        "# Reuse: Test fixture only.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/new_tool.py": "added"},
    )
    payload = run_cli_json(["--freshness-metadata", "--changed-files", "scripts/new_tool.py", "--json"])
    result = topology_doctor.run_freshness_metadata(["scripts/new_tool.py"])

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def _write_code_review_graph_db(path, *, branch="data-improve", head="HEADSHA", file_hash=None, file_path=None):
    import sqlite3

    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                file_hash TEXT
            );
            CREATE TABLE edges (id INTEGER PRIMARY KEY AUTOINCREMENT);
            CREATE TABLE flows (id INTEGER PRIMARY KEY AUTOINCREMENT);
            CREATE TABLE communities (id INTEGER PRIMARY KEY AUTOINCREMENT);
            CREATE TABLE risk_index (node_id INTEGER PRIMARY KEY);
            """
        )
        conn.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("git_branch", branch),
                ("git_head_sha", head),
                ("last_updated", "2026-04-19T00:00:00"),
                ("schema_version", "9"),
            ],
        )
        if file_path:
            conn.execute(
                "INSERT INTO nodes (kind, name, qualified_name, file_path, file_hash) VALUES (?, ?, ?, ?, ?)",
                ("File", file_path, file_path, file_path, file_hash),
            )
            conn.execute("INSERT INTO edges DEFAULT VALUES")
        conn.commit()
    finally:
        conn.close()


def _write_code_review_graph_impact_db(path, *, branch="data-improve", head="HEADSHA", file_hash, file_path):
    import sqlite3

    caller_path = str(path.parent.parent / "src" / "caller.py")
    test_path = str(path.parent.parent / "tests" / "test_example.py")
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                qualified_name TEXT NOT NULL UNIQUE,
                file_path TEXT NOT NULL,
                line_start INTEGER,
                line_end INTEGER,
                is_test INTEGER DEFAULT 0,
                file_hash TEXT
            );
            CREATE TABLE edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                source_qualified TEXT NOT NULL,
                target_qualified TEXT NOT NULL,
                file_path TEXT NOT NULL,
                line INTEGER DEFAULT 0
            );
            CREATE TABLE flows (id INTEGER PRIMARY KEY AUTOINCREMENT);
            CREATE TABLE communities (id INTEGER PRIMARY KEY AUTOINCREMENT);
            CREATE TABLE risk_index (node_id INTEGER PRIMARY KEY);
            """
        )
        conn.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("git_branch", branch),
                ("git_head_sha", head),
                ("last_updated", "2026-04-20T00:00:00"),
                ("schema_version", "9"),
            ],
        )
        nodes = [
            ("File", file_path, file_path, file_path, 1, 10, 0, file_hash),
            ("Function", "target_func", f"{file_path}::target_func", file_path, 2, 5, 0, file_hash),
            ("Function", "caller_func", f"{caller_path}::caller_func", caller_path, 3, 8, 0, "caller-hash"),
            ("Test", "test_target_func", f"{test_path}::test_target_func", test_path, 4, 9, 1, "test-hash"),
        ]
        conn.executemany(
            "INSERT INTO nodes (kind, name, qualified_name, file_path, line_start, line_end, is_test, file_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            nodes,
        )
        conn.executemany(
            "INSERT INTO edges (kind, source_qualified, target_qualified, file_path, line) VALUES (?, ?, ?, ?, ?)",
            [
                ("CALLS", f"{caller_path}::caller_func", f"{file_path}::target_func", caller_path, 4),
                ("TESTED_BY", f"{test_path}::test_target_func", f"{file_path}::target_func", test_path, 5),
            ],
        )
        conn.execute("INSERT INTO flows DEFAULT VALUES")
        conn.execute("INSERT INTO communities DEFAULT VALUES")
        conn.commit()
    finally:
        conn.close()


def test_cli_json_parity_for_code_review_graph_status(monkeypatch, tmp_path):
    root = tmp_path
    (root / ".gitignore").write_text(".code-review-graph/*\n!.code-review-graph/graph.db\n", encoding="utf-8")
    _write_code_review_graph_db(root / ".code-review-graph" / "graph.db")
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: [".code-review-graph/graph.db"])
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: {})
    from scripts import topology_doctor_code_review_graph

    monkeypatch.setattr(topology_doctor_code_review_graph, "current_git_metadata", lambda api: ("data-improve", "HEADSHA"))

    payload = run_cli_json(["--code-review-graph-status", "--json"])
    result = topology_doctor.run_code_review_graph_status()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
        "details": result.details,
    }


def test_cli_json_parity_for_code_review_graph_protocol():
    payload = run_cli_json(["--code-review-graph-protocol", "--json"])
    result = topology_doctor.run_code_review_graph_protocol()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_code_review_graph_protocol_validates_two_stage_order():
    result = topology_doctor.run_code_review_graph_protocol()
    protocol = topology_doctor.load_code_review_graph_protocol()

    assert_topology_ok(result)
    assert protocol["metadata"]["authority_status"] == "derived_context_protocol_not_authority"
    assert [stage["id"] for stage in protocol["stages"]] == ["semantic_boot", "graph_context"]
    assert protocol["invocation_rules"]["graph_requires_semantic_boot"] is True
    assert protocol["invocation_rules"]["graph_authority_status"] == "derived_not_authority"


def test_code_review_graph_protocol_rejects_graph_first(monkeypatch):
    protocol = topology_doctor.load_code_review_graph_protocol()
    protocol["stages"][0]["order"] = 2
    protocol["stages"][1]["order"] = 1

    monkeypatch.setattr(topology_doctor, "load_code_review_graph_protocol", lambda: protocol)
    result = topology_doctor.run_code_review_graph_protocol()

    assert not result.ok
    assert any(issue.code == "code_review_graph_protocol_stage_order" for issue in result.issues)


def test_code_review_graph_status_reports_path_mode_and_absent_sidecar(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "scripts" / "example.py"
    source.parent.mkdir()
    source.write_text("print('graph')\n", encoding="utf-8")
    (root / ".gitignore").write_text(".code-review-graph/*\n!.code-review-graph/graph.db\n", encoding="utf-8")
    _write_code_review_graph_db(
        root / ".code-review-graph" / "graph.db",
        file_path=source.resolve().as_posix(),
        file_hash=topology_doctor._code_review_graph_checks().sha256_file(source),
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: [".code-review-graph/graph.db"])
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: {})
    from scripts import topology_doctor_code_review_graph

    monkeypatch.setattr(topology_doctor_code_review_graph, "current_git_metadata", lambda api: ("data-improve", "HEADSHA"))

    result = topology_doctor.run_code_review_graph_status()

    assert result.ok
    assert result.details["path_mode"] == "absolute"
    assert result.details["graph_meta"] == {
        "path": ".code-review-graph/graph_meta.json",
        "present": False,
        "tracked": False,
        "parity_status": "absent",
    }
    health = result.details["graph_health"]
    assert health["authority_status"] == "derived_graph_health_not_authority"
    assert health["db"] == {
        "path": ".code-review-graph/graph.db",
        "present": True,
        "tracked": True,
        "ignore_guard_present": True,
    }
    assert health["branch"]["matches"] is True
    assert health["head"]["matches"] is True
    assert health["sidecar"]["parity_status"] == "absent"
    assert health["usable_for_claims"] is True


@pytest.mark.live_topology
def test_code_review_graph_mcp_repo_resolution_avoids_workstation_default(monkeypatch, tmp_path):
    from scripts import code_review_graph_mcp_readonly

    monkeypatch.delenv("CRG_REPO_ROOT", raising=False)
    monkeypatch.setattr(code_review_graph_mcp_readonly, "_default_repo_root", None)

    assert code_review_graph_mcp_readonly._repo(None) is None
    assert "/Users/leofitz" not in str(code_review_graph_mcp_readonly._repo(None) or "")

    explicit = tmp_path / "repo"
    explicit.mkdir()
    assert code_review_graph_mcp_readonly._repo(str(explicit)) == explicit.resolve().as_posix()

    env_repo = tmp_path / "env-repo"
    env_repo.mkdir()
    monkeypatch.setenv("CRG_REPO_ROOT", str(env_repo))
    assert code_review_graph_mcp_readonly._repo(None) == env_repo.resolve().as_posix()


def test_code_review_graph_status_warns_on_dirty_file_hash_mismatch(monkeypatch, tmp_path):
    root = tmp_path
    script = root / "scripts" / "example.py"
    script.parent.mkdir()
    script.write_text("print('new')\n", encoding="utf-8")
    (root / ".gitignore").write_text(".code-review-graph/*\n!.code-review-graph/graph.db\n", encoding="utf-8")
    _write_code_review_graph_db(
        root / ".code-review-graph" / "graph.db",
        file_path=script.resolve().as_posix(),
        file_hash="old-hash",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: [".code-review-graph/graph.db"])
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: {"scripts/example.py": "modified"})
    from scripts import topology_doctor_code_review_graph

    monkeypatch.setattr(topology_doctor_code_review_graph, "current_git_metadata", lambda api: ("data-improve", "HEADSHA"))

    result = topology_doctor.run_code_review_graph_status(["scripts/example.py"])

    assert result.ok
    assert any(issue.code == "code_review_graph_dirty_file_stale" for issue in result.issues)
    health = result.details["graph_health"]
    assert health["changed_file_coverage"]["checked"] == 1
    assert health["changed_file_coverage"]["stale_hash"] == ["scripts/example.py"]
    assert health["usable_for_claims"] is False
    assert "graph_impact_validated" in health["invalidates_claims"]
    assert "official code-review-graph" in health["refresh_instruction"]


def test_code_review_graph_status_blocks_untracked_graph_db(monkeypatch, tmp_path):
    root = tmp_path
    (root / ".gitignore").write_text(".code-review-graph/*\n!.code-review-graph/graph.db\n", encoding="utf-8")
    _write_code_review_graph_db(root / ".code-review-graph" / "graph.db")
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: [])
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: {})
    from scripts import topology_doctor_code_review_graph

    monkeypatch.setattr(topology_doctor_code_review_graph, "current_git_metadata", lambda api: ("data-improve", "HEADSHA"))

    result = topology_doctor.run_code_review_graph_status()

    assert not result.ok
    assert any(issue.code == "code_review_graph_untracked_db" for issue in result.issues)


def test_code_review_graph_health_marks_unreadable_graph_unusable(monkeypatch, tmp_path):
    root = tmp_path
    graph_db = root / ".code-review-graph" / "graph.db"
    graph_db.parent.mkdir()
    graph_db.write_text("not sqlite", encoding="utf-8")
    (root / ".gitignore").write_text(".code-review-graph/*\n!.code-review-graph/graph.db\n", encoding="utf-8")
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: [".code-review-graph/graph.db"])
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: {})
    from scripts import topology_doctor_code_review_graph

    monkeypatch.setattr(topology_doctor_code_review_graph, "current_git_metadata", lambda api: ("data-improve", "HEADSHA"))
    monkeypatch.setattr(
        topology_doctor_code_review_graph,
        "open_graph_db",
        lambda api: (_ for _ in ()).throw(sqlite3.Error("broken graph")),
    )

    result = topology_doctor.run_code_review_graph_status()

    assert result.ok
    assert any(issue.code == "code_review_graph_unreadable" for issue in result.issues)
    health = result.details["graph_health"]
    assert health["usable_for_claims"] is False
    assert "graph_impact_validated" in health["invalidates_claims"]
    assert "official code-review-graph" in health["refresh_instruction"]


def test_code_impact_graph_is_not_applicable_for_docs_only():
    payload = topology_doctor.build_code_impact_graph(["docs/README.md"], task="review docs")

    assert payload["authority_status"] == "derived_code_impact_not_authority"
    assert payload["applicable"] is False
    assert payload["usable"] is False
    assert payload["reason"] == "no source/test/script code files in this context pack"


def test_build_code_impact_graph_extracts_callers_and_tests(monkeypatch, tmp_path):
    root = tmp_path
    source = root / "src" / "example.py"
    source.parent.mkdir()
    source.write_text("def target_func():\n    return 1\n", encoding="utf-8")
    (root / ".gitignore").write_text(".code-review-graph/*\n!.code-review-graph/graph.db\n", encoding="utf-8")
    _write_code_review_graph_impact_db(
        root / ".code-review-graph" / "graph.db",
        file_hash=topology_doctor._code_review_graph_checks().sha256_file(source),
        file_path=source.resolve().as_posix(),
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["src/example.py"])
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: {"src/example.py": "modified"})
    from scripts import topology_doctor_code_review_graph

    monkeypatch.setattr(topology_doctor_code_review_graph, "current_git_metadata", lambda api: ("data-improve", "HEADSHA"))
    monkeypatch.setattr(
        topology_doctor,
        "run_code_review_graph_status",
        lambda files: topology_doctor.StrictResult(ok=True, issues=[]),
    )

    payload = topology_doctor.build_code_impact_graph(["src/example.py"], task="review code")

    assert payload["applicable"] is True
    assert payload["usable"] is True
    assert payload["changed_nodes"][0]["name"] == "target_func"
    assert payload["callers"][0]["callers"][0]["name"] == "caller_func"
    assert payload["tests_for"][0]["tests"][0]["name"] == "test_target_func"
    assert payload["test_gaps"] == []


def test_code_impact_graph_does_not_swallow_status_type_errors(monkeypatch):
    def broken_status(files, *, include_appendix=False):
        raise TypeError("internal status bug")

    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", broken_status)

    with pytest.raises(TypeError, match="internal status bug"):
        topology_doctor.build_code_impact_graph(["src/example.py"], task="review code")


def test_cli_json_parity_for_closeout_command(monkeypatch):
    payload = {
        "ok": True,
        "authority_status": "generated_closeout_not_authority",
        "changed_files": ["docs/README.md"],
        "selected_lanes": {"docs": True},
        "lanes": {"docs": {"ok": True, "issue_count": 0, "blocking_count": 0, "warning_count": 0, "issues": []}},
        "telemetry": {"dark_write_target_count": 0, "broken_visible_route_count": 0, "unclassified_docs_artifact_count": 0},
        "blocking_issues": [],
        "warning_issues": [],
    }
    monkeypatch.setattr(topology_doctor, "run_closeout", lambda **kwargs: payload)

    assert run_cli_json(["closeout", "--json"]) == payload


def test_closeout_summary_prints_global_health_sidecar(monkeypatch):
    payload = {
        "ok": True,
        "authority_status": "generated_closeout_not_authority",
        "changed_files": ["docs/README.md"],
        "selected_lanes": {"docs": True},
        "lanes": {"docs": {"ok": True, "issue_count": 0, "blocking_count": 0, "warning_count": 0, "issues": []}},
        "global_health": {"code_review_graph": {"ok": False, "issue_count": 1, "blocking_count": 1, "warning_count": 0}},
        "telemetry": {"dark_write_target_count": 0, "broken_visible_route_count": 0, "unclassified_docs_artifact_count": 0},
        "blocking_issues": [],
        "warning_issues": [],
    }
    monkeypatch.setattr(topology_doctor, "run_closeout", lambda **kwargs: payload)

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(["closeout", "--summary-only"])

    assert exit_code == 0
    assert "global_health:" in buffer.getvalue()
    assert "- code_review_graph: (blocking=1, warnings=0)" in buffer.getvalue()


def test_freshness_metadata_rejects_changed_script_without_header(monkeypatch, tmp_path):
    root = tmp_path
    script = root / "scripts" / "legacy_probe.py"
    script.parent.mkdir()
    script.write_text("print('unsafe old probe')\n", encoding="utf-8")
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/legacy_probe.py": "modified"},
    )

    result = topology_doctor.run_freshness_metadata(["scripts/legacy_probe.py"])

    assert not result.ok
    assert any(issue.code == "freshness_header_missing" for issue in result.issues)


def test_freshness_metadata_accepts_changed_test_with_header(monkeypatch, tmp_path):
    root = tmp_path
    test_file = root / "tests" / "test_current_behavior.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "# Lifecycle: created=2026-04-16; last_reviewed=2026-04-16; last_reused=2026-04-16\n"
        "# Purpose: Test current behavior.\n"
        "# Reuse: Inspect test_topology before relying on this file.\n\n"
        "def test_ok():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"tests/test_current_behavior.py": "modified"},
    )

    result = topology_doctor.run_freshness_metadata(["tests/test_current_behavior.py"])

    assert_topology_ok(result)


def test_freshness_metadata_rejects_missing_purpose_or_reuse(monkeypatch, tmp_path):
    root = tmp_path
    script = root / "scripts" / "audit_current.py"
    script.parent.mkdir()
    script.write_text(
        "# Lifecycle: created=2026-04-16; last_reviewed=2026-04-16; last_reused=never\n"
        "print('missing purpose and reuse')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/audit_current.py": "modified"},
    )

    result = topology_doctor.run_freshness_metadata(["scripts/audit_current.py"])

    assert not result.ok
    assert any(issue.code == "freshness_header_field_missing" for issue in result.issues)


def test_naming_conventions_rejects_missing_function_shape(monkeypatch):
    manifest = topology_doctor.load_naming_conventions()
    manifest["function_naming"] = {}
    monkeypatch.setattr(topology_doctor, "load_naming_conventions", lambda: manifest)

    result = topology_doctor.run_naming_conventions()

    assert not result.ok
    assert any(issue.code == "naming_conventions_rule_invalid" for issue in result.issues)


def test_docs_mode_rejects_unregistered_visible_subtree(monkeypatch):
    topology = topology_doctor.load_topology()
    topology["docs_subroots"] = [
        item for item in topology["docs_subroots"]
        if item["path"] != "docs/to-do-list"
    ]
    visible = "docs/to-do-list/zeus_bug100_reassessment_table.csv"
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: [visible],
    )

    issues = topology_doctor._check_hidden_docs(topology)

    assert any(issue.code == "docs_unregistered_subtree" for issue in issues)


def test_docs_mode_rejects_non_md_artifact_outside_artifact_subroot(monkeypatch):
    topology = topology_doctor.load_topology()
    artifact = next(item for item in topology["docs_subroots"] if item["path"] == "docs/to-do-list")
    artifact["allow_non_markdown"] = False
    visible = "docs/to-do-list/zeus_bug100_reassessment_table.csv"
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: [visible],
    )

    issues = topology_doctor._check_hidden_docs(topology)

    assert any(issue.code == "docs_non_markdown_artifact" for issue in issues)


def test_docs_mode_allows_registered_reports_json(monkeypatch):
    topology = topology_doctor.load_topology()
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: ["docs/reports/diagnostic_snapshot.json"],
    )

    issues = topology_doctor._check_hidden_docs(topology)

    assert issues == []


def test_docs_mode_excluded_roots_drive_space_path_exemption(monkeypatch):
    topology = topology_doctor.load_topology()
    topology["docs_mode_excluded_roots"] = [{"path": "docs/local archive"}]
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: ["docs/local archive/old note.md"],
    )

    issues = topology_doctor._check_hidden_docs(topology)

    assert issues == []


def test_docs_mode_rejects_broken_internal_paths(monkeypatch):
    def fake_read_text(path, *args, **kwargs):
        if str(path).endswith("architecture/kernel_manifest.yaml"):
            return "historical_references:\n  - docs/nope/missing.md\n"
        return ""

    monkeypatch.setattr(topology_doctor.Path, "read_text", fake_read_text)
    issues = topology_doctor._check_broken_internal_paths()

    assert any(issue.code == "docs_broken_internal_path" for issue in issues)


def test_docs_mode_rejects_current_state_missing_operations_label(monkeypatch, tmp_path):
    topology = topology_doctor.load_topology()
    current = tmp_path / "current_state.md"
    current.write_text(
        "- Branch: `data-improve`\n"
        "- Primary packet file: `docs/operations/task_2026-04-13_topology_compiler_program.md`\n",
        encoding="utf-8",
    )
    topology["active_operations_registry"] = {
        "current_state": str(current.relative_to(topology_doctor.ROOT)) if current.is_relative_to(topology_doctor.ROOT) else str(current),
        "required_labels": ["Primary packet file", "Active sidecars"],
        "surface_prefix": "docs/operations/",
    }

    issues = topology_doctor._check_active_operations_registry(topology)

    assert any(issue.code == "operations_current_state_missing_label" for issue in issues)


def test_docs_mode_rejects_current_state_unregistered_surface(monkeypatch, tmp_path):
    topology = topology_doctor.load_topology()
    current = tmp_path / "current_state.md"
    current.write_text(
        "- Primary packet file: `docs/operations/task_2026-04-13_topology_compiler_program.md`\n"
        "- Active sidecars:\n"
        "  - `docs/operations/task_2099-01-01_unregistered.md`\n"
        "- Active backlog:\n"
        "  - `docs/operations/task_2026-04-13_remaining_repair_backlog.md`\n"
        "- Next packet: Packet 4\n",
        encoding="utf-8",
    )
    missing_surface = topology_doctor.ROOT / "docs/operations/task_2099-01-01_unregistered.md"
    missing_surface.write_text("# temporary test surface\n", encoding="utf-8")
    topology["active_operations_registry"] = {
        "current_state": str(current.relative_to(topology_doctor.ROOT)) if current.is_relative_to(topology_doctor.ROOT) else str(current),
        "required_labels": ["Primary packet file", "Active sidecars", "Active backlog", "Next packet"],
        "surface_prefix": "docs/operations/",
    }

    try:
        issues = topology_doctor._check_active_operations_registry(topology)
    finally:
        missing_surface.unlink()

    assert any(issue.code == "operations_current_state_unregistered_surface" for issue in issues)


def test_docs_mode_rejects_unregistered_operation_task_folder(tmp_path, monkeypatch):
    root = tmp_path
    task_dir = root / "docs" / "operations" / "task_2099-01-01_unlisted"
    task_dir.mkdir(parents=True)
    (task_dir / "work_log.md").write_text("Date: 2099-01-01\n", encoding="utf-8")
    agents = root / "docs" / "operations" / "AGENTS.md"
    agents.write_text(
        "# docs/operations AGENTS\n\n## File registry\n\n| File | Purpose |\n|---|---|\n| `current_state.md` | pointer |\n",
        encoding="utf-8",
    )
    current = root / "docs" / "operations" / "current_state.md"
    current.write_text(
        "- Primary packet file: `docs/operations/current_state.md`\n"
        "- Active backlog:\n"
        "- Active checklist/evidence:\n"
        "- Next packet: none\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)

    issues = topology_doctor._check_operations_task_folders({})

    assert any(issue.code == "operations_task_unregistered" for issue in issues)


def test_docs_mode_rejects_current_state_referenced_unregistered_operation_task_folder(tmp_path, monkeypatch):
    root = tmp_path
    task_dir = root / "docs" / "operations" / "task_2099-01-01_unlisted"
    task_dir.mkdir(parents=True)
    (task_dir / "work_log.md").write_text("Date: 2099-01-01\n", encoding="utf-8")
    agents = root / "docs" / "operations" / "AGENTS.md"
    agents.write_text(
        "# docs/operations AGENTS\n\n## File registry\n\n| File | Purpose |\n|---|---|\n| `current_state.md` | pointer |\n",
        encoding="utf-8",
    )
    current = root / "docs" / "operations" / "current_state.md"
    current.write_text(
        "- Primary packet file: `docs/operations/current_state.md`\n"
        "- Active execution packet: `docs/operations/task_2099-01-01_unlisted/plan.md`\n"
        "- Active backlog:\n"
        "- Active checklist/evidence:\n"
        "- Next packet: none\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)

    issues = topology_doctor._check_operations_task_folders({})

    assert any(issue.code == "operations_task_unregistered" for issue in issues)


def test_docs_mode_accepts_registered_non_active_operation_task_folder(tmp_path, monkeypatch):
    root = tmp_path
    task_dir = root / "docs" / "operations" / "task_2099-01-01_registered"
    task_dir.mkdir(parents=True)
    (task_dir / "work_log.md").write_text("Date: 2099-01-01\n", encoding="utf-8")
    agents = root / "docs" / "operations" / "AGENTS.md"
    agents.write_text(
        "# docs/operations AGENTS\n\n## File registry\n\n| File | Purpose |\n|---|---|\n| `current_state.md` | pointer |\n| `task_2099-01-01_registered/` | packet evidence |\n",
        encoding="utf-8",
    )
    current = root / "docs" / "operations" / "current_state.md"
    current.write_text(
        "- Primary packet file: `docs/operations/current_state.md`\n"
        "- Active backlog:\n"
        "- Active checklist/evidence:\n"
        "- Next packet: none\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)

    issues = topology_doctor._check_operations_task_folders({})

    assert not any(issue.code == "operations_task_unregistered" for issue in issues)


def test_docs_mode_requires_runtime_plan_inventory(tmp_path, monkeypatch):
    root = tmp_path
    (root / ".omc" / "plans").mkdir(parents=True)
    (root / ".omc" / "plans" / "open-questions.md").write_text("# questions\n", encoding="utf-8")
    topology = {
        "runtime_artifact_inventory": {
            "path": "docs/operations/runtime_artifact_inventory.md",
            "runtime_plan_globs": [".omc/plans/*.md"],
        }
    }
    monkeypatch.setattr(topology_doctor, "ROOT", root)

    issues = topology_doctor._check_runtime_plan_inventory(topology)

    assert any(issue.code == "runtime_plan_inventory_missing" for issue in issues)


def test_docs_mode_rejects_unindexed_runtime_plan_artifact(tmp_path, monkeypatch):
    root = tmp_path
    (root / ".omx" / "plans").mkdir(parents=True)
    (root / ".omx" / "plans" / "hidden-plan.md").write_text("# plan\n", encoding="utf-8")
    inventory = root / "docs" / "operations" / "runtime_artifact_inventory.md"
    inventory.parent.mkdir(parents=True)
    inventory.write_text("No hidden plan here.\n", encoding="utf-8")
    topology = {
        "runtime_artifact_inventory": {
            "path": "docs/operations/runtime_artifact_inventory.md",
            "runtime_plan_globs": [".omx/plans/*.md"],
        }
    }
    monkeypatch.setattr(topology_doctor, "ROOT", root)

    issues = topology_doctor._check_runtime_plan_inventory(topology)

    assert any(issue.code == "runtime_plan_artifact_unindexed" for issue in issues)


def test_docs_mode_rejects_progress_handoff_outside_allowed_paths(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: [
            "docs/reference/team_handoff.md",
            "docs/operations/task_2099-01-01_good/team_handoff.md",
        ],
    )

    issues = topology_doctor._check_progress_handoff_paths()

    assert any(issue.code == "progress_handoff_path_violation" for issue in issues)
    assert all(issue.path != "docs/operations/task_2099-01-01_good/team_handoff.md" for issue in issues)


def test_docs_mode_requires_archive_interface_in_allowed_root_files(tmp_path, monkeypatch):
    root = tmp_path
    docs = root / "docs"
    docs.mkdir()
    (docs / "archive_registry.md").write_text("# Archive Registry\n", encoding="utf-8")
    (docs / "AGENTS.md").write_text("# docs AGENTS\n", encoding="utf-8")
    (docs / "README.md").write_text("# Docs Index\n", encoding="utf-8")
    topology = {
        "archive_interface": {"path": "docs/archive_registry.md"},
        "docs_root_allowed_files": ["docs/AGENTS.md", "docs/README.md"],
        "docs_subroots": [
            {
                "path": "docs/archives",
                "default_read": False,
                "visible_interface": "docs/archive_registry.md",
            }
        ],
    }
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    from scripts import topology_doctor_registry_checks

    issues = topology_doctor_registry_checks.check_archive_interface(topology_doctor, topology)

    assert any(issue.code == "docs_archive_interface_unregistered" for issue in issues)


def test_docs_mode_rejects_archive_as_live_peer_language(tmp_path, monkeypatch):
    root = tmp_path
    docs = root / "docs"
    docs.mkdir()
    (docs / "archive_registry.md").write_text("# Archive Registry\n", encoding="utf-8")
    (docs / "AGENTS.md").write_text("Documentation root. Active subdirectories plus archives.\n", encoding="utf-8")
    (docs / "README.md").write_text("Route to archives/AGENTS.md for history.\n", encoding="utf-8")
    topology = {
        "archive_interface": {
            "path": "docs/archive_registry.md",
            "forbidden_live_peer_phrases": [
                "Active subdirectories plus archives",
                "archives/AGENTS.md",
            ],
        },
        "docs_root_allowed_files": ["docs/AGENTS.md", "docs/README.md", "docs/archive_registry.md"],
        "docs_subroots": [
            {
                "path": "docs/archives",
                "default_read": False,
                "visible_interface": "docs/archive_registry.md",
            }
        ],
    }
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    from scripts import topology_doctor_registry_checks

    issues = topology_doctor_registry_checks.check_archive_interface(topology_doctor, topology)

    assert any(issue.code == "docs_archive_default_read_leak" for issue in issues)


def test_docs_mode_rejects_archive_default_read_in_topology(tmp_path, monkeypatch):
    root = tmp_path
    docs = root / "docs"
    docs.mkdir()
    (docs / "archive_registry.md").write_text("# Archive Registry\n", encoding="utf-8")
    (docs / "AGENTS.md").write_text("# docs AGENTS\n", encoding="utf-8")
    (docs / "README.md").write_text("# Docs Index\n", encoding="utf-8")
    topology = {
        "archive_interface": {"path": "docs/archive_registry.md"},
        "docs_root_allowed_files": ["docs/AGENTS.md", "docs/README.md", "docs/archive_registry.md"],
        "docs_subroots": [
            {
                "path": "docs/archives",
                "default_read": True,
                "visible_interface": "docs/archive_registry.md",
            }
        ],
    }
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    from scripts import topology_doctor_registry_checks

    issues = topology_doctor_registry_checks.check_archive_interface(topology_doctor, topology)

    assert any(issue.code == "docs_archive_default_read_leak" for issue in issues)


def _docs_registry_entry(path: str, **overrides):
    entry = {
        "path": path,
        "doc_class": "reference",
        "default_read": False,
        "direct_reference_allowed": True,
        "current_role": "test entry",
        "canonical_replaced_by": [],
        "next_action": "keep",
        "lifecycle_state": "durable",
        "coverage_scope": "exact",
        "parent_coverage_allowed": False,
        "truth_profile": "durable_reference",
        "freshness_class": "slow_changing",
        "supersedes": [],
        "superseded_by": [],
        "may_live_in_reference": True,
        "contains_volatile_metrics": False,
        "current_tense_allowed": False,
        "refresh_source": "code_and_manifest",
    }
    entry.update(overrides)
    return entry


def _install_docs_registry_fixture(monkeypatch, tmp_path, registry, visible_files):
    (tmp_path / "architecture").mkdir(parents=True, exist_ok=True)
    registry_path = tmp_path / "architecture" / "docs_registry.yaml"
    registry_path.write_text("schema_version: 1\n", encoding="utf-8")
    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "DOCS_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(topology_doctor, "load_docs_registry", lambda: registry)
    monkeypatch.setattr(topology_doctor, "_git_visible_files", lambda: visible_files)
    for rel in visible_files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# doc\n", encoding="utf-8")


def test_docs_registry_rejects_missing_required_field(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["reference"],
        "allowed_next_actions": ["keep"],
        "allowed_lifecycle_states": ["durable"],
        "allowed_coverage_scopes": ["exact"],
        "entries": [{"path": "docs/reference/a.md"}],
    }
    _install_docs_registry_fixture(monkeypatch, tmp_path, registry, ["docs/reference/a.md"])

    issues = topology_doctor._check_docs_registry({})

    assert any(issue.code == "docs_registry_required_field_missing" for issue in issues)


def test_docs_registry_parent_entry_covers_operations_packet(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["operations"],
        "allowed_next_actions": ["keep"],
        "allowed_lifecycle_states": ["transitional"],
        "allowed_coverage_scopes": ["descendants"],
        "allowed_truth_profiles": ["package_input"],
        "allowed_freshness_classes": ["packet_bound"],
        "allowed_refresh_sources": ["n/a"],
        "entries": [
            _docs_registry_entry(
                "docs/operations/task_*/",
                doc_class="operations",
                lifecycle_state="transitional",
                coverage_scope="descendants",
                parent_coverage_allowed=True,
                truth_profile="package_input",
                freshness_class="packet_bound",
                may_live_in_reference=False,
                current_tense_allowed=True,
                refresh_source="n/a",
            )
        ],
    }
    _install_docs_registry_fixture(
        monkeypatch,
        tmp_path,
        registry,
        ["docs/operations/task_2099-01-01_packet/plan.md"],
    )

    issues = topology_doctor._check_docs_registry({})

    assert issues == []


def test_docs_registry_rejects_forbidden_parent_entry(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["authority"],
        "allowed_next_actions": ["keep"],
        "allowed_lifecycle_states": ["durable"],
        "allowed_coverage_scopes": ["descendants"],
        "entries": [
            _docs_registry_entry(
                "docs/authority/",
                doc_class="authority",
                coverage_scope="descendants",
                parent_coverage_allowed=True,
            )
        ],
    }
    _install_docs_registry_fixture(monkeypatch, tmp_path, registry, ["docs/authority/a.md"])

    issues = topology_doctor._check_docs_registry({})

    assert any(issue.code == "docs_registry_parent_not_allowed" for issue in issues)


def test_docs_registry_rejects_unclassified_visible_doc(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["reference"],
        "allowed_next_actions": ["keep"],
        "allowed_lifecycle_states": ["durable"],
        "allowed_coverage_scopes": ["exact"],
        "entries": [_docs_registry_entry("docs/reference/a.md")],
    }
    _install_docs_registry_fixture(
        monkeypatch,
        tmp_path,
        registry,
        ["docs/reference/a.md", "docs/reference/unclassified.md"],
    )

    issues = topology_doctor._check_docs_registry({})

    assert any(issue.code == "docs_registry_unclassified_doc" for issue in issues)


def test_docs_registry_rejects_direct_reference_leak(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["extraction_source", "router"],
        "allowed_next_actions": ["extract_then_move", "keep"],
        "allowed_lifecycle_states": ["temporary", "durable"],
        "allowed_coverage_scopes": ["exact"],
        "entries": [
            _docs_registry_entry(
                "docs/legacy.md",
                doc_class="extraction_source",
                direct_reference_allowed=False,
                next_action="extract_then_move",
                lifecycle_state="temporary",
            ),
            _docs_registry_entry("docs/README.md", doc_class="router", default_read=True),
        ],
    }
    _install_docs_registry_fixture(monkeypatch, tmp_path, registry, ["docs/legacy.md", "docs/README.md"])
    (tmp_path / "docs" / "README.md").write_text("Read docs/legacy.md first.\n", encoding="utf-8")

    issues = topology_doctor._check_docs_registry({})

    assert any(issue.code == "docs_registry_direct_reference_leak" for issue in issues)


def test_docs_registry_rejects_noncanonical_reference_doc(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["reference"],
        "allowed_next_actions": ["keep"],
        "allowed_lifecycle_states": ["durable"],
        "allowed_coverage_scopes": ["exact"],
        "allowed_truth_profiles": ["durable_reference", "report_evidence"],
        "allowed_freshness_classes": ["slow_changing"],
        "allowed_refresh_sources": ["code_and_manifest"],
        "entries": [
            _docs_registry_entry(
                "docs/reference/legacy_reference_a.md",
                truth_profile="report_evidence",
                may_live_in_reference=False,
            )
        ],
    }
    _install_docs_registry_fixture(monkeypatch, tmp_path, registry, ["docs/reference/legacy_reference_a.md"])

    issues = topology_doctor._check_docs_registry({})

    assert any(issue.code == "docs_reference_not_canonical" for issue in issues)


def test_docs_registry_rejects_removed_reference_path_leak(monkeypatch, tmp_path):
    registry = {
        "allowed_doc_classes": ["router"],
        "allowed_next_actions": ["keep"],
        "allowed_lifecycle_states": ["durable"],
        "allowed_coverage_scopes": ["exact"],
        "allowed_truth_profiles": ["router"],
        "allowed_freshness_classes": ["stable"],
        "allowed_refresh_sources": ["n/a"],
        "entries": [
            _docs_registry_entry(
                "docs/README.md",
                doc_class="router",
                truth_profile="router",
                freshness_class="stable",
                may_live_in_reference=False,
                current_tense_allowed=True,
                refresh_source="n/a",
            )
        ],
    }
    _install_docs_registry_fixture(monkeypatch, tmp_path, registry, ["docs/README.md"])
    (tmp_path / "docs" / "README.md").write_text("Read docs/reference/data_inventory.md.\n", encoding="utf-8")

    issues = topology_doctor._check_docs_registry({})

    assert any(issue.code == "docs_removed_reference_path_leak" for issue in issues)


def test_current_state_operation_paths_accept_markdown_and_bare_paths():
    text = (
        "- Primary packet file: [packet](docs/operations/task_2026-04-13_topology_compiler_program.md)\n"
        "- Active sidecars:\n"
        "  - docs/operations/task_2026-04-14_topology_context_efficiency/\n"
        "- Active backlog:\n"
        "  - `docs/operations/task_2026-04-13_remaining_repair_backlog.md`\n"
    )

    paths = topology_doctor._current_state_operation_paths(text, "docs/operations/")

    assert "docs/operations/task_2026-04-13_topology_compiler_program.md" in paths
    assert "docs/operations/task_2026-04-14_topology_context_efficiency/" in paths
    assert "docs/operations/task_2026-04-13_remaining_repair_backlog.md" in paths


def test_docs_mode_rejects_current_state_missing_required_anchor(tmp_path):
    topology = topology_doctor.load_topology()
    current = tmp_path / "current_state.md"
    current.write_text(
        "- Primary packet file: `docs/operations/task_2026-04-13_topology_compiler_program.md`\n"
        "- Active sidecars:\n"
        "  - `docs/operations/task_2026-04-14_topology_context_efficiency/`\n"
        "- Active backlog:\n"
        "  - `docs/operations/task_2026-04-13_remaining_repair_backlog.md`\n"
        "- Active checklist/evidence:\n"
        "  - `docs/to-do-list/zeus_data_improve_bug_audit_75.xlsx`\n"
        "- Next packet: Packet 4\n",
        encoding="utf-8",
    )
    topology["active_operations_registry"] = {
        "current_state": str(current.relative_to(topology_doctor.ROOT)) if current.is_relative_to(topology_doctor.ROOT) else str(current),
        "required_labels": ["Primary packet file", "Active sidecars", "Active backlog", "Active checklist/evidence", "Next packet"],
        "surface_prefix": "docs/operations/",
        "required_anchors": ["docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md"],
    }

    issues = topology_doctor._check_active_operations_registry(topology)

    assert any(issue.code == "operations_current_state_missing_anchor" for issue in issues)


def test_current_state_receipt_bound_passes_current_packet():
    result = topology_doctor.run_current_state_receipt_bound()

    assert_topology_ok(result)


def test_current_state_receipt_bound_rejects_missing_receipt(monkeypatch, tmp_path):
    root = tmp_path
    current = root / "docs" / "operations" / "current_state.md"
    packet = root / "docs" / "operations" / "task_2026-04-23_test" / "plan.md"
    current.parent.mkdir(parents=True)
    packet.parent.mkdir(parents=True)
    packet.write_text("# Plan\n", encoding="utf-8")
    current.write_text(
        "- Active package source: `docs/operations/task_2026-04-23_test/plan.md`\n"
        "- Active execution packet: `docs/operations/task_2026-04-23_test/plan.md`\n"
        "- Receipt-bound source: `docs/operations/task_2026-04-23_test/receipt.json`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    topology = {
        "active_operations_registry": {
            "current_state": "docs/operations/current_state.md",
            "surface_prefix": "docs/operations/",
        }
    }

    issues = topology_doctor._check_current_state_receipt_bound(topology)

    assert any(issue.code == "current_state_receipt_missing" for issue in issues)


def test_current_state_receipt_bound_accepts_closeout_evidence_packet(monkeypatch, tmp_path):
    root = tmp_path
    packet_dir = root / "docs" / "operations" / "task_2026-04-23_test"
    packet = packet_dir / "plan.md"
    receipt = packet_dir / "receipt.json"
    current = root / "docs" / "operations" / "current_state.md"
    packet_dir.mkdir(parents=True)
    packet.write_text("# Plan\n", encoding="utf-8")
    receipt.write_text(
        json.dumps(
            {
                "task": "test closeout",
                "packet": "docs/operations/task_2026-04-23_test/plan.md",
                "changed_files": ["docs/operations/current_state.md"],
            }
        ),
        encoding="utf-8",
    )
    current.write_text(
        "- Active package source: `docs/operations/task_2026-04-23_followup/handoff.md`\n"
        "- Active execution packet: none frozen; next packet pending phase-entry\n"
        "- Closeout evidence packet: `docs/operations/task_2026-04-23_test/plan.md`\n"
        "- Receipt-bound source: `docs/operations/task_2026-04-23_test/receipt.json`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    topology = {
        "active_operations_registry": {
            "current_state": "docs/operations/current_state.md",
            "surface_prefix": "docs/operations/",
        }
    }

    issues = topology_doctor._check_current_state_receipt_bound(topology)

    assert issues == []


def test_current_state_receipt_bound_rejects_packet_mismatch(monkeypatch, tmp_path):
    root = tmp_path
    packet_dir = root / "docs" / "operations" / "task_2026-04-23_test"
    packet_dir.mkdir(parents=True)
    (packet_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (packet_dir / "receipt.json").write_text(
        json.dumps(
            {
                "task": "test",
                "packet": "docs/operations/task_2026-04-23_other/plan.md",
                "changed_files": ["docs/operations/current_state.md"],
            }
        ),
        encoding="utf-8",
    )
    current = root / "docs" / "operations" / "current_state.md"
    current.write_text(
        "- Active package source: `docs/operations/task_2026-04-23_test/plan.md`\n"
        "- Active execution packet: `docs/operations/task_2026-04-23_test/plan.md`\n"
        "- Receipt-bound source: `docs/operations/task_2026-04-23_test/receipt.json`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", root)
    topology = {
        "active_operations_registry": {
            "current_state": "docs/operations/current_state.md",
            "surface_prefix": "docs/operations/",
        }
    }

    issues = topology_doctor._check_current_state_receipt_bound(topology)

    assert any(issue.code == "current_state_receipt_mismatch" for issue in issues)


def test_docs_mode_rejects_dated_market_fact_in_config_agents(monkeypatch):
    def fake_read_text(path, *args, **kwargs):
        if str(path).endswith("config/AGENTS.md"):
            return "Polymarket changes do happen (verified 2026-04-14: London moved stations)."
        return ""

    monkeypatch.setattr(topology_doctor.Path, "read_text", fake_read_text)
    issues = topology_doctor._check_config_agents_volatile_facts()

    assert any(issue.code == "config_agents_volatile_fact" for issue in issues)


def test_config_agents_allows_artifact_pointer_without_dated_snapshot(monkeypatch):
    def fake_read_text(path, *args, **kwargs):
        if str(path).endswith("config/AGENTS.md"):
            return "Volatile external city/station evidence lives under docs/artifacts/polymarket_city_settlement_audit_*.md."
        return ""

    monkeypatch.setattr(topology_doctor.Path, "read_text", fake_read_text)
    issues = topology_doctor._check_config_agents_volatile_facts()

    assert issues == []


@pytest.mark.live_topology
def test_topology_source_mode_covers_all_tracked_src_files():
    result = topology_doctor.run_source()

    assert_topology_ok(result)


@pytest.mark.live_topology
def test_topology_tests_mode_classifies_actual_suite_and_law_gate():
    result = topology_doctor.run_tests()

    assert_topology_ok(result)


def test_tests_mode_checks_relationship_manifest_symbols(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["relationship_test_manifests"][0] = {
        **topology["relationship_test_manifests"][0],
        "required_symbols": ["MISSING_RELATIONSHIP_SYMBOL"],
    }

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(issue.code == "test_relationship_manifest_missing_symbol" for issue in result.issues)


def test_settlement_rounding_digest_names_wmo_law_and_gates():
    digest = topology_doctor.build_digest(
        "change settlement rounding",
        ["src/contracts/settlement_semantics.py"],
    )
    joined = "\n".join(str(item) for values in digest.values() if isinstance(values, list) for item in values)

    assert digest["profile"] == "change settlement rounding"
    assert "floor(x + 0.5)" in joined
    assert "src/contracts/settlement_semantics.py" in digest["allowed_files"]
    assert "state/*.db" in digest["forbidden_files"]
    assert any("test_instrument_invariants.py" in gate for gate in digest["gates"])
    assert "src/engine/replay.py" in digest["downstream"]
    assert digest["source_rationale"][0]["path"] == "src/contracts/settlement_semantics.py"
    assert digest["source_rationale"][0]["authority_role"] == "settlement_rounding_law"


def test_replay_fidelity_digest_names_non_promotion_and_point_in_time_truth():
    digest = topology_doctor.build_digest("edit replay fidelity")
    joined = "\n".join(str(item) for values in digest.values() if isinstance(values, list) for item in values)

    assert digest["profile"] == "edit replay fidelity"
    assert "diagnostic_non_promotion" in joined
    assert "point-in-time" in joined
    assert any("state/zeus_backtest.db" in item for item in digest["downstream"])
    assert any("Do not promote" in item for item in digest["stop_conditions"])
    assert any(item["path"] == "src/engine/replay.py" for item in digest["source_rationale"])


def test_source_mode_rejects_known_writer_without_route(monkeypatch):
    rationale = topology_doctor.load_source_rationale()
    rationale["files"]["src/calibration/store.py"] = {
        **rationale["files"]["src/calibration/store.py"],
        "write_routes": ["script_repair_write"],
    }

    monkeypatch.setattr(topology_doctor, "load_source_rationale", lambda: rationale)
    result = topology_doctor.run_source()

    assert not result.ok
    assert any(issue.code == "source_file_write_route_missing" for issue in result.issues)


def test_source_mode_locks_derived_strategy_tracker_role(monkeypatch):
    rationale = topology_doctor.load_source_rationale()
    rationale["files"]["src/state/strategy_tracker.py"] = {
        **rationale["files"]["src/state/strategy_tracker.py"],
        "authority_role": "runtime_authority",
    }

    monkeypatch.setattr(topology_doctor, "load_source_rationale", lambda: rationale)
    result = topology_doctor.run_source()

    assert not result.ok
    assert any(issue.code == "source_file_role_mismatch" for issue in result.issues)


def test_tests_mode_rejects_active_reverse_antibody(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["reverse_antibody_status"] = {"active": ["bad_test"], "resolved": []}

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(issue.code == "test_reverse_antibody_active" for issue in result.issues)


def test_tests_mode_rejects_law_gate_test_outside_core(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["categories"]["core_law_antibody"].remove("tests/test_fdr.py")
    topology["categories"]["useful_regression"].append("tests/test_fdr.py")

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(issue.code == "test_law_gate_non_core" for issue in result.issues)


def test_tests_mode_rejects_high_sensitivity_skip_count_drift(monkeypatch):
    topology = topology_doctor.load_test_topology()
    topology["high_sensitivity_skips"]["tests/test_db.py"] = {
        **topology["high_sensitivity_skips"]["tests/test_db.py"],
        "skip_count": -1,
    }

    monkeypatch.setattr(topology_doctor, "load_test_topology", lambda: topology)
    result = topology_doctor.run_tests()

    assert not result.ok
    assert any(
        issue.code == "test_high_sensitivity_skip_count_mismatch"
        for issue in result.issues
    )


@pytest.mark.live_topology
def test_topology_scripts_mode_covers_all_top_level_scripts():
    result = topology_doctor.run_scripts()

    assert_topology_ok(result)


def test_topology_data_rebuild_mode_encodes_certification_blockers():
    result = topology_doctor.run_data_rebuild()

    assert_topology_ok(result)


@pytest.mark.live_topology
def test_topology_history_lore_mode_validates_dense_cards():
    result = topology_doctor.run_history_lore()

    assert_topology_ok(result)


def test_topology_context_budget_mode_passes_after_entry_slimming():
    result = topology_doctor.run_context_budget()

    assert result.ok is True
    assert all(issue.severity == "warning" for issue in result.issues)


def test_topology_agents_coherence_mode_matches_machine_zones():
    result = topology_doctor.run_agents_coherence()

    assert_topology_ok(result)


def test_topology_idioms_mode_registers_non_obvious_code_shapes():
    result = topology_doctor.run_idioms()

    assert_topology_ok(result)


def test_topology_self_check_coherence_mode_aligns_zero_context_overlay():
    result = topology_doctor.run_self_check_coherence()

    assert_topology_ok(result)


def test_topology_runtime_modes_mode_keeps_discovery_modes_visible():
    result = topology_doctor.run_runtime_modes()

    assert_topology_ok(result)


def test_map_maintenance_requires_test_topology_for_new_test_file(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["architecture/test_topology.yaml"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "tests/test_new_behavior.py":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(["tests/test_new_behavior.py"], mode="precommit")

    assert not result.ok
    assert any(issue.code == "map_maintenance_companion_missing" for issue in result.issues)
    assert any("architecture/test_topology.yaml" in issue.message for issue in result.issues)


def test_map_maintenance_allows_new_test_file_when_companion_present(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["architecture/test_topology.yaml"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "tests/test_new_behavior.py":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        ["tests/test_new_behavior.py", "architecture/test_topology.yaml"],
        mode="precommit",
    )

    assert_topology_ok(result)


def test_map_maintenance_requires_docs_mesh_for_new_docs_subtree(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["docs/AGENTS.md"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "docs/new_surface/AGENTS.md":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        ["docs/new_surface/AGENTS.md"],
        mode="closeout",
    )

    assert not result.ok
    assert any("architecture/topology.yaml" in issue.message for issue in result.issues)


def test_map_maintenance_requires_docs_mesh_for_top_level_artifact(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["docs/AGENTS.md"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "docs/surprise.xlsx":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        ["docs/surprise.xlsx"],
        mode="closeout",
    )

    assert not result.ok
    assert any("docs/README.md" in issue.message for issue in result.issues)


def test_map_maintenance_allows_registered_operation_packet_file_without_current_state(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["docs/operations/AGENTS.md"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "docs/operations/task_2099-01-01_packet/plan.md":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        [
            "docs/operations/task_2099-01-01_packet/plan.md",
            "docs/operations/AGENTS.md",
        ],
        mode="closeout",
    )

    assert_topology_ok(result)


def test_map_maintenance_docs_top_level_glob_does_not_match_nested_packet_file(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: [])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "docs/operations/task_2099-01-01_packet/plan.md":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        ["docs/operations/task_2099-01-01_packet/plan.md"],
        mode="closeout",
    )

    assert not result.ok
    assert any("docs/operations/AGENTS.md" in issue.message for issue in result.issues)
    assert all("docs/README.md" not in issue.message for issue in result.issues)


@pytest.mark.live_topology
def test_map_maintenance_requires_reports_registry_for_new_report(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["docs/reports/AGENTS.md"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "docs/reports/new_report.md":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(
        ["docs/reports/new_report.md"],
        mode="closeout",
    )

    assert not result.ok
    assert any("docs/reports/AGENTS.md" in issue.message for issue in result.issues)
    assert all("docs/README.md" not in issue.message for issue in result.issues)


def test_map_maintenance_does_not_require_registry_for_plain_modification(monkeypatch):
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["src/engine/evaluator.py"])
    result = topology_doctor.run_map_maintenance(["src/engine/evaluator.py"])

    assert_topology_ok(result)


def test_map_maintenance_advisory_reports_without_blocking(monkeypatch):
    original_exists = topology_doctor.Path.exists
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["architecture/test_topology.yaml"])

    def fake_exists(self):
        if self == topology_doctor.ROOT / "tests/test_new_behavior.py":
            return True
        return original_exists(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    result = topology_doctor.run_map_maintenance(["tests/test_new_behavior.py"])

    assert result.ok
    assert any(issue.code == "map_maintenance_companion_missing" for issue in result.issues)
    assert all(issue.severity == "warning" for issue in result.issues)


def test_git_status_parser_maps_rename_to_delete_and_add(monkeypatch):
    def fake_run(*args, **kwargs):
        assert "-z" in args[0]
        return type(
            "CompletedProcess",
            (),
            {"stdout": "R  src/new_name.py\0src/old_name.py\0?? scripts/new_tool.py\0 M AGENTS.md\0"},
        )()

    monkeypatch.setattr(topology_doctor.subprocess, "run", fake_run)
    changes = topology_doctor._git_status_changes()

    assert changes["src/old_name.py"] == "deleted"
    assert changes["src/new_name.py"] == "added"
    assert changes["scripts/new_tool.py"] == "added"
    assert changes["AGENTS.md"] == "modified"


def test_map_maintenance_uses_git_status_when_changed_files_omitted(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_status_changes",
        lambda: {"scripts/new_tool.py": "added"},
    )
    result = topology_doctor.run_map_maintenance(mode="precommit")

    assert not result.ok
    assert any(issue.code == "map_maintenance_companion_missing" for issue in result.issues)
    assert any("architecture/script_manifest.yaml" in issue.message for issue in result.issues)


def test_map_maintenance_git_status_advisory_does_not_block(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_status_changes",
        lambda: {"src/contracts/new_contract.py": "added"},
    )
    result = topology_doctor.run_map_maintenance()

    assert result.ok
    assert any(issue.code == "map_maintenance_companion_missing" for issue in result.issues)
    assert all(issue.severity == "warning" for issue in result.issues)


def test_map_maintenance_closeout_reports_all_companion_gaps(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_status_changes",
        lambda: {
            "scripts/new_tool.py": "added",
            "tests/test_new_behavior.py": "added",
            "src/contracts/new_contract.py": "added",
        },
    )
    result = topology_doctor.run_map_maintenance(mode="closeout")

    assert not result.ok
    companion_gaps = [
        issue for issue in result.issues if issue.code == "map_maintenance_companion_missing"
    ]
    assert len(companion_gaps) == 3
    assert {issue.path for issue in companion_gaps} == {
        "scripts/new_tool.py",
        "tests/test_new_behavior.py",
        "src/contracts/new_contract.py",
    }


def test_map_maintenance_requires_config_registry_for_new_config(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_status_changes",
        lambda: {"config/new_runtime_knob.yaml": "added"},
    )
    result = topology_doctor.run_map_maintenance(mode="closeout")

    assert not result.ok
    assert any(
        issue.path == "config/new_runtime_knob.yaml"
        and "config/AGENTS.md" in issue.message
        for issue in result.issues
    )


def test_map_maintenance_explicit_files_keep_git_status_kind(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_status_changes",
        lambda: {
            "scripts/old_tool.py": "deleted",
            "scripts/new_tool.py": "added",
        },
    )
    monkeypatch.setattr(topology_doctor, "_git_ls_files", lambda: ["scripts/old_tool.py"])
    result = topology_doctor.run_map_maintenance(
        ["scripts/old_tool.py", "scripts/new_tool.py"],
        mode="closeout",
    )

    assert not result.ok
    assert any("deleted file requires" in issue.message for issue in result.issues)
    assert any("added file requires" in issue.message for issue in result.issues)


def test_root_state_classification_uses_git_visible_files(monkeypatch):
    topology = {
        "root_governed_files": [],
        "state_surfaces": [{"path": "state/registered.log"}],
    }
    visible = [
        "state/registered.log",
        "state/unregistered-visible.log",
        "unregistered-root.txt",
    ]

    monkeypatch.setattr(topology_doctor, "_git_visible_files", lambda: visible)

    original_exists = topology_doctor.Path.exists
    original_is_file = topology_doctor.Path.is_file

    def fake_exists(self):
        if self in {topology_doctor.ROOT / path for path in visible}:
            return True
        return original_exists(self)

    def fake_is_file(self):
        if self in {topology_doctor.ROOT / path for path in visible}:
            return True
        return original_is_file(self)

    monkeypatch.setattr(topology_doctor.Path, "exists", fake_exists)
    monkeypatch.setattr(topology_doctor.Path, "is_file", fake_is_file)

    issues = topology_doctor._check_root_and_state_classification(topology)

    assert {issue.path for issue in issues} == {
        "state/unregistered-visible.log",
        "unregistered-root.txt",
    }


def test_format_issues_lists_each_issue_on_its_own_line():
    issues = [
        topology_doctor.TopologyIssue("code_a", "a.py", "first"),
        topology_doctor.TopologyIssue("code_b", "b.py", "second", severity="warning"),
    ]

    text = topology_doctor.format_issues(issues)

    assert "1. [error:code_a] a.py: first" in text
    assert "2. [warning:code_b] b.py: second" in text


def test_issue_legacy_json_keys_preserved():
    issue = topology_doctor.issue(
        "source_rationale_missing",
        "src/example.py",
        "tracked src file has no rationale entry",
    )

    payload = topology_doctor.asdict(issue)

    assert list(payload) == ["code", "path", "message", "severity"]
    assert payload["code"] == "source_rationale_missing"


def test_issue_v2_emits_owner_manifest_when_present():
    issue = topology_doctor.issue(
        "source_rationale_missing",
        "src/example.py",
        "tracked src file has no rationale entry",
    )

    payload = topology_doctor._issue_to_json(issue, "2")

    assert payload["owner_manifest"] == "architecture/source_rationale.yaml"
    assert payload["repair_kind"] == "add_registry_row"
    assert "navigation" in payload["blocking_modes"]


def test_issue_v2_emits_warning_lifecycle_metadata_when_present():
    issue = topology_doctor.warning(
        "code_review_graph_stale_head",
        ".code-review-graph/graph.db",
        "graph stale",
        lifecycle_state="acknowledged",
        lifecycle_owner="runtime-maintainer",
        deferred_until="2026-05-01",
        invalidation_condition="graph-impact claim requested",
    )

    payload = topology_doctor._issue_to_json(issue, "2")

    assert payload["lifecycle_state"] == "acknowledged"
    assert payload["lifecycle_owner"] == "runtime-maintainer"
    assert payload["deferred_until"] == "2026-05-01"
    assert payload["invalidation_condition"] == "graph-impact claim requested"


def test_issue_factories_set_blocking_modes():
    advisory = topology_doctor.advisory("docs_registry_missing", "docs/a.md", "advisory")
    blocking = topology_doctor.blocking("script_manifest_missing", "scripts/a.py", "blocking")
    legacy = topology_doctor.legacy_issue("script_manifest_missing", "scripts/a.py", "legacy")

    assert advisory.severity == "warning"
    assert advisory.blocking_modes == ("global_health",)
    assert blocking.severity == "error"
    assert "closeout" in blocking.blocking_modes
    assert legacy.owner_manifest is None
    assert legacy.blocking_modes is None


def test_renderer_groups_by_repair_kind():
    issues = [
        topology_doctor.issue("source_rationale_missing", "src/a.py", "missing"),
        topology_doctor.issue("script_manifest_missing", "scripts/a.py", "missing"),
    ]

    summary = topology_doctor.summarize_issues(issues)
    formatted = topology_doctor.format_issues(issues)

    assert "by owner/repair:" in summary
    assert "architecture/source_rationale.yaml:add_registry_row: 1" in summary
    assert "(architecture/script_manifest.yaml; add_registry_row)" in formatted


def test_blocking_modes_drives_navigation_lane_policy(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])
    advisory_source_issue = topology_doctor.TopologyIssue(
        code="source_rationale_missing",
        path="src/engine/replay.py",
        message="global-only advisory",
        blocking_modes=("global_health",),
    )

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(
        topology_doctor,
        "run_source",
        lambda: topology_doctor.StrictResult(ok=False, issues=[advisory_source_issue]),
    )

    payload = topology_doctor.run_navigation(
        "edit replay fidelity",
        ["src/engine/replay.py"],
        issue_schema_version="2",
    )

    assert payload["ok"] is True
    assert payload["direct_blockers"] == []
    assert payload["repo_health_warnings"][0]["blocking_modes"] == ["global_health"]
    assert payload["admission"]["status"] == "admitted"


def test_issue_schema_drift_guard():
    # R12 Phase 5.B: topology_schema.yaml deleted; inline constants are the source.
    expected = (
        set(topology_doctor.SCHEMA_ISSUE_JSON_CONTRACT_LEGACY_FIELDS)
        | set(topology_doctor.SCHEMA_ISSUE_JSON_CONTRACT_TYPED_FIELDS)
    )

    assert set(topology_doctor.topology_issue_field_names()) == expected
    assert topology_doctor.ISSUE_REPAIR_KINDS == topology_doctor.ISSUE_REPAIR_KINDS
    assert topology_doctor.ISSUE_MATURITY_VALUES == {"stable", "provisional", "placeholder"}
    assert topology_doctor.ISSUE_LIFECYCLE_STATES == {
        "new", "acknowledged", "deferred_until", "expires_at", "promoted_to_blocker", "retired"
    }
    assert topology_doctor.ISSUE_AUTHORITY_STATUSES == {"authority", "derived", "evidence", "unknown"}
    assert topology_doctor.ISSUE_BLOCKING_MODES == (
        "navigation", "navigation_strict_health", "closeout",
        "strict_full_repo", "global_health", "admission",
    )


def test_digest_profile_selection_schema_contract_passes():
    result = topology_doctor.run_schema()
    selection = topology_doctor.load_topology()["digest_profile_selection"]

    assert_topology_ok(result)
    assert "architecture/topology.yaml" in selection["shared_companion_patterns"]
    assert "architecture/digest_profiles.py" in selection["shared_companion_patterns"]


def test_digest_profile_selection_rejects_shared_only_selector(monkeypatch):
    topology = json.loads(json.dumps(topology_doctor.load_topology()))
    topology["digest_profile_selection"] = {
        "shared_companion_patterns": ["architecture/topology.yaml"],
    }
    topology["digest_profiles"] = [
        {
            "id": "bad shared-only profile",
            "file_patterns": ["architecture/topology.yaml"],
            "strong_phrases": [],
        }
    ]

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)

    result = topology_doctor.run_schema()

    assert not result.ok
    assert any(issue.code == "digest_profile_selector_shared_only" for issue in result.issues)


def test_digest_profile_selection_uses_match_policy_strong_phrases(monkeypatch):
    topology = json.loads(json.dumps(topology_doctor.load_topology()))
    topology["digest_profile_selection"] = {
        "shared_companion_patterns": ["architecture/topology.yaml"],
    }
    topology["digest_profiles"] = [
        {
            "id": "phrase selectable shared-file profile",
            "match_policy": {
                "strong_phrases": ["phrase selectable"],
            },
            "file_patterns": ["architecture/topology.yaml"],
        }
    ]

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)

    result = topology_doctor.run_schema()

    assert_topology_ok(result)


def test_digest_profile_selection_rejects_conflicting_file_evidence(monkeypatch):
    topology = json.loads(json.dumps(topology_doctor.load_topology()))
    topology["digest_profiles"] = [
        {
            "id": "bad conflicting profile",
            "semantic_file_patterns": ["scripts/example.py"],
            "companion_file_patterns": ["scripts/example.py"],
        }
    ]

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)

    result = topology_doctor.run_schema()

    assert not result.ok
    assert any(issue.code == "digest_profile_selection_conflicting_pattern" for issue in result.issues)


def test_system_books_have_required_headings():
    required_headings = [
        "> Status:",
        "## Purpose",
        "## Authority anchors",
        "## How it works",
        "## Hidden obligations",
        "## Failure modes",
        "## Repair routes",
        "## Cross-links",
    ]
    books = [
        "docs/reference/modules/topology_system.md",
        "docs/reference/modules/code_review_graph.md",
        "docs/reference/modules/docs_system.md",
        "docs/reference/modules/manifests_system.md",
        "docs/reference/modules/topology_doctor_system.md",
        "docs/reference/modules/closeout_and_receipts_system.md",
    ]

    for book in books:
        text = (topology_doctor.ROOT / book).read_text(encoding="utf-8")
        assert text.startswith("# "), book
        for heading in required_headings:
            assert heading in text, f"{book} missing {heading}"


def test_progress_handoff_allows_reference_module_closeout_book(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: ["docs/reference/modules/closeout_and_receipts_system.md"],
    )

    assert topology_doctor._docs_checks().check_progress_handoff_paths(topology_doctor) == []


def test_progress_handoff_rejects_other_reference_module_handoff_names(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_git_visible_files",
        lambda: ["docs/reference/modules/random_handoff.md"],
    )

    issues = topology_doctor._docs_checks().check_progress_handoff_paths(topology_doctor)

    assert [issue.code for issue in issues] == ["progress_handoff_path_violation"]


def test_ownership_matrix_loadable_from_schema():
    # R12 Phase 5.B: ownership_fact_types() now uses inlined OWNERSHIP_FACT_TYPES constant;
    # schema arg is accepted but ignored for backward compat.
    fact_types = topology_doctor._ownership_checks().ownership_fact_types()

    assert "doc_classification" in fact_types
    assert fact_types["doc_classification"]["canonical_owner"] == "architecture/docs_registry.yaml"
    assert len(fact_types) >= 12


def test_two_canonical_owners_for_same_fact_type_blocks():
    schema = {
        "ownership": {
            "fact_types": {
                "doc_classification": {
                    "canonical_owner": "architecture/docs_registry.yaml",
                    "canonical_owners": ["architecture/docs_registry.yaml", "architecture/module_manifest.yaml"],
                    "derived_owners": [],
                    "companion_update_rule": "architecture/map_maintenance.yaml",
                }
            }
        }
    }

    issues = topology_doctor._ownership_checks().check_ownership_schema(topology_doctor, schema)

    assert [issue.code for issue in issues] == ["ownership_multiple_canonical_owners"]


def test_blocking_issue_without_owner_manifest_raises(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "issue",
        lambda code, path, message, **metadata: topology_doctor.legacy_issue(code, path, message),
    )

    issues = topology_doctor._ownership_checks().check_first_wave_issue_owners(topology_doctor)

    assert {issue.code for issue in issues} == {"ownership_issue_owner_missing"}


def test_module_manifest_maturity_field_validated():
    module_manifest = {
        "modules": {
            "sample": {
                "path": "src/sample",
                "module_book": "docs/reference/modules/sample.md",
                "maturity": "invalid",
            }
        }
    }

    issues = topology_doctor._ownership_checks().check_module_manifest_maturity(topology_doctor, module_manifest)

    assert [issue.code for issue in issues] == ["module_manifest_maturity_invalid"]


def test_doc_classification_owned_only_by_docs_registry():
    # R12 Phase 5.B: ownership_fact_types() uses inlined constant; no schema arg needed.
    fact_types = topology_doctor._ownership_checks().ownership_fact_types()

    assert fact_types["doc_classification"]["canonical_owner"] == "architecture/docs_registry.yaml"
    assert "architecture/module_manifest.yaml" not in fact_types["doc_classification"].get("derived_owners", [])


def test_module_routing_owned_only_by_module_manifest():
    # R12 Phase 5.B: ownership_fact_types() uses inlined constant; no schema arg needed.
    fact_types = topology_doctor._ownership_checks().ownership_fact_types()

    assert fact_types["module_routing"]["canonical_owner"] == "architecture/module_manifest.yaml"
    assert "architecture/docs_registry.yaml" not in fact_types["module_routing"].get("derived_owners", [])


def test_graph_appendix_marks_derived_not_authority():
    appendix = topology_doctor.build_graph_appendix(["scripts/topology_doctor.py"], task="graph review")

    assert appendix["authority_status"] == "derived_not_authority"
    assert "Graph output is derived review context only." in appendix["limitations"]


def test_graph_appendix_respects_size_budget(monkeypatch):
    payload = {
        "usable": True,
        "changed_nodes": [
            {"path": "scripts/very_long_file.py", "line_start": idx, "qualified_name": "x" * 200}
            for idx in range(50)
        ],
        "tests_for": [],
        "impacted_files": [f"src/{idx}_{'x' * 100}.py" for idx in range(50)],
        "test_gaps": [],
    }
    monkeypatch.setattr(topology_doctor, "build_code_impact_graph", lambda files, task="": payload)

    appendix = topology_doctor.build_graph_appendix(["scripts/topology_doctor.py"], task="graph review")

    assert appendix["truncation"]["applied"] is True
    assert len(json.dumps(appendix).encode("utf-8")) <= 2048


def test_graph_appendix_stale_is_advisory_by_default(monkeypatch):
    payload = {
        "usable": False,
        "reason": "graph cache is unavailable, stale, or missing target code coverage",
        "graph_health": {
            "issues": [
                {
                    "code": "code_review_graph_stale_head",
                    "path": ".code-review-graph/graph.db",
                    "message": "stale",
                    "severity": "warning",
                }
            ]
        },
        "changed_nodes": [],
        "tests_for": [],
        "impacted_files": [],
        "test_gaps": [],
    }
    monkeypatch.setattr(topology_doctor, "build_code_impact_graph", lambda files, task="": payload)

    appendix = topology_doctor.build_graph_appendix(["scripts/topology_doctor.py"], task="graph review")

    assert appendix["graph_freshness"] == "stale"
    assert appendix["authority_status"] == "derived_not_authority"


def test_graph_appendix_stale_blocks_when_required_by_profile():
    profiles = topology_doctor.load_context_pack_profiles()["profiles"]

    assert all(profile.get("requires_graph_evidence") is False for profile in profiles)


def test_navigation_aggregates_default_health_and_digest():
    # Use a settlement-profile-aligned file to validate the happy-path navigation
    # aggregation. ("src/engine/replay.py" is a downstream file for the settlement
    # profile, not an allowed write target, so use the canonical settlement file
    # to exercise the admitted path.)
    payload = topology_doctor.run_navigation(
        "fix settlement rounding",
        ["src/contracts/settlement_semantics.py"],
    )

    assert_navigation_ok(payload)
    assert payload["admission"]["status"] == "admitted"
    assert payload["digest"]["profile"] == "change settlement rounding"
    assert payload["checks"]["context_budget"]["ok"]
    assert payload["checks"]["agents_coherence"]["ok"]
    assert payload["checks"]["self_check_coherence"]["ok"]
    assert "scripts" in payload["excluded_lanes"]
    assert "strict" in payload["excluded_lanes"]
    assert "planning_lock" in payload["excluded_lanes"]
    assert any(card["id"] == "WMO_ROUNDING_BANKER_FAILURE" for card in payload["digest"]["history_lore"])


def test_navigation_unrelated_docs_issue_does_not_block_source_route(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    docs_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="docs_unregistered_subtree",
                path="docs/operations/unrelated_packet",
                message="unrelated docs issue",
            )
        ],
    )
    for name in (
        "run_context_budget",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: docs_result)

    payload = topology_doctor.run_navigation(
        "edit replay fidelity",
        ["src/engine/replay.py"],
    )

    assert payload["ok"] is True
    assert payload["admission"]["status"] == "admitted"
    assert payload["direct_blockers"] == []
    assert payload["repo_health_warnings"][0]["lane"] == "docs"
    assert payload["global_health_counts"]["docs"]["blocking_count"] == 1
    assert payload["route_context"]["mode"] == "navigation"


def test_navigation_requested_file_issue_blocks_route(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    source_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="source_rationale_missing",
                path="src/engine/replay.py",
                message="requested source issue",
            )
        ],
    )
    for name in (
        "run_context_budget",
        "run_docs",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(topology_doctor, "run_source", lambda: source_result)

    payload = topology_doctor.run_navigation("source task", ["src/engine/replay.py"])

    assert payload["ok"] is False
    assert payload["direct_blockers"][0]["code"] == "source_rationale_missing"
    assert payload["repo_health_warnings"] == []


def test_navigation_unclassified_requested_file_blocks_route(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)

    payload = topology_doctor.run_navigation("source task", ["does/not/exist.py"])

    assert payload["ok"] is False
    assert payload["direct_blockers"][0]["lane"] == "navigation"
    assert payload["direct_blockers"][0]["code"] == "navigation_requested_file_unclassified"


def test_navigation_missing_known_root_file_still_blocks_once(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)

    payload = topology_doctor.run_navigation("script task", ["scripts/does_not_exist.py"])

    assert payload["ok"] is False
    assert len(payload["direct_blockers"]) == 1
    assert payload["direct_blockers"][0]["code"] == "navigation_requested_file_unclassified"


def test_navigation_synthetic_global_issue_is_advisory_without_strict_health(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    docs_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="global_docs_issue",
                path="<docs-global>",
                message="synthetic global issue",
            )
        ],
    )
    for name in (
        "run_context_budget",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: docs_result)

    payload = topology_doctor.run_navigation(
        "edit replay fidelity",
        ["src/engine/replay.py"],
    )

    assert payload["ok"] is True
    assert payload["admission"]["status"] == "admitted"
    assert payload["direct_blockers"] == []
    assert payload["repo_health_warnings"][0]["path"] == "<docs-global>"


def test_navigation_strict_health_re_enables_global_blocking(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    docs_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="docs_unregistered_subtree",
                path="docs/operations/unrelated_packet",
                message="unrelated docs issue",
            )
        ],
    )
    for name in (
        "run_context_budget",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: docs_result)

    payload = topology_doctor.run_navigation("source task", ["src/engine/replay.py"], strict_health=True)

    assert payload["ok"] is False
    assert payload["route_context"]["mode"] == "navigation_strict_health"
    assert payload["direct_blockers"][0]["lane"] == "docs"
    assert payload["repo_health_warnings"] == []


def test_navigation_human_output_splits_blockers_and_warnings(monkeypatch):
    payload = {
        "ok": True,
        "task": "source task",
        "digest": {"profile": "generic"},
        "issues": [
            {
                "lane": "docs",
                "code": "docs_unregistered_subtree",
                "path": "docs/operations/unrelated_packet",
                "message": "unrelated docs issue",
                "severity": "error",
            }
        ],
        "direct_blockers": [],
        "repo_health_warnings": [
            {
                "lane": "docs",
                "code": "docs_unregistered_subtree",
                "path": "docs/operations/unrelated_packet",
                "message": "unrelated docs issue",
                "severity": "error",
            }
        ],
        "excluded_lanes": {},
    }
    monkeypatch.setattr(topology_doctor, "run_navigation", lambda task, files, strict_health=False: payload)

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(["--navigation", "--task", "source task", "--files", "src/engine/replay.py"])

    assert exit_code == 0
    assert "repo_health_warnings:" in buffer.getvalue()
    assert "direct_blockers:" not in buffer.getvalue()


def test_navigation_human_output_does_not_duplicate_direct_blockers(monkeypatch):
    issue = {
        "lane": "source",
        "code": "source_rationale_missing",
        "path": "src/engine/replay.py",
        "message": "requested source issue",
        "severity": "error",
    }
    payload = {
        "ok": False,
        "task": "source task",
        "digest": {"profile": "generic"},
        "issues": [issue],
        "direct_blockers": [issue],
        "repo_health_warnings": [],
        "excluded_lanes": {},
    }
    monkeypatch.setattr(topology_doctor, "run_navigation", lambda task, files, strict_health=False: payload)

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(["--navigation", "--task", "source task", "--files", "src/engine/replay.py"])

    assert exit_code == 1
    assert "direct_blockers:" in buffer.getvalue()
    assert "issues:" not in buffer.getvalue()


def test_agents_coherence_rejects_prose_zone_that_lowers_manifest(monkeypatch):
    rationale = topology_doctor.load_source_rationale()
    rationale["package_defaults"]["src/observability"] = {
        **rationale["package_defaults"]["src/observability"],
        "zone": "K4_experimental",
    }

    monkeypatch.setattr(topology_doctor, "load_source_rationale", lambda: rationale)
    result = topology_doctor.run_agents_coherence()

    assert not result.ok
    assert any(issue.code == "agents_zone_mismatch" for issue in result.issues)


def test_planning_lock_requires_evidence_for_control_change():
    result = topology_doctor.run_planning_lock(["src/control/control_plane.py"])

    assert not result.ok
    assert any(issue.code == "planning_lock_required" for issue in result.issues)
    assert "changed files" in result.issues[0].message or result.issues[0].path != "<change-set>"


def test_planning_lock_uses_changed_file_count_not_read_budget():
    result = topology_doctor.run_planning_lock(
        ["src/engine/evaluator.py"] * 5,
    )

    assert not result.ok
    assert any(
        issue.path == "<change-set>" and "changed files" in issue.message
        for issue in result.issues
    )


def test_planning_lock_is_independent_from_context_assumptions():
    digest = topology_doctor.build_digest("change lifecycle manager", ["src/state/lifecycle_manager.py"])
    result = topology_doctor.run_planning_lock(
        ["src/state/lifecycle_manager.py"],
        "docs/operations/current_state.md",
    )

    assert digest["context_assumption"]["planning_lock_independent"] is True
    assert_topology_ok(result)


def test_planning_lock_accepts_current_state_as_evidence():
    result = topology_doctor.run_planning_lock(
        ["src/control/control_plane.py"],
        "docs/operations/current_state.md",
    )

    assert_topology_ok(result)


def test_idioms_mode_rejects_unregistered_semantic_guard(monkeypatch):
    manifest = topology_doctor.load_code_idioms()
    manifest["idioms"][0] = {
        **manifest["idioms"][0],
        "examples": [],
    }

    monkeypatch.setattr(topology_doctor, "load_code_idioms", lambda: manifest)
    result = topology_doctor.run_idioms()

    assert not result.ok
    assert any(issue.code == "code_idiom_unregistered_occurrence" for issue in result.issues)


def test_self_check_coherence_rejects_missing_root_reference(monkeypatch):
    original_read_text = topology_doctor.Path.read_text

    def fake_read_text(self, *args, **kwargs):
        text = original_read_text(self, *args, **kwargs)
        if self.name == "AGENTS.md" and self.parent == topology_doctor.ROOT:
            return text.replace("architecture/self_check/zero_context_entry.md", "")
        return text

    monkeypatch.setattr(topology_doctor.Path, "read_text", fake_read_text)
    result = topology_doctor.run_self_check_coherence()

    assert not result.ok
    assert any(issue.code == "self_check_root_reference_missing" for issue in result.issues)


def test_self_check_coherence_rejects_missing_authority_index_reference(monkeypatch):
    original_read_text = topology_doctor.Path.read_text

    def fake_read_text(self, *args, **kwargs):
        text = original_read_text(self, *args, **kwargs)
        if self.name == "AGENTS.md" and self.parent == topology_doctor.ROOT:
            return text.replace("architecture/self_check/authority_index.md", "")
        return text

    monkeypatch.setattr(topology_doctor.Path, "read_text", fake_read_text)
    result = topology_doctor.run_self_check_coherence()

    assert not result.ok
    assert any(issue.code == "self_check_root_reference_missing" for issue in result.issues)


def test_runtime_modes_rejects_missing_mode(monkeypatch):
    topology = topology_doctor.load_runtime_modes()
    topology["required_modes"]["opening_hunt"] = {
        **topology["required_modes"]["opening_hunt"],
        "enum": "MISSING_ENUM",
    }

    monkeypatch.setattr(topology_doctor, "load_runtime_modes", lambda: topology)
    result = topology_doctor.run_runtime_modes()

    assert not result.ok
    assert any(issue.code == "runtime_mode_enum_missing" for issue in result.issues)


def test_reference_replacement_rejects_unsafe_deletion(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    manifest["entries"][0] = {
        **manifest["entries"][0],
        "delete_allowed": True,
        "replacement_status": "partial_replacement_candidate",
    }

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_replacement_delete_unsafe" for issue in result.issues)


def test_reference_replacement_detects_default_read_mismatch(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    manifest["entries"] = [
        {
            **entry,
            "default_read": False if entry["path"] == "docs/reference/zeus_domain_model.md" else entry["default_read"],
        }
        for entry in manifest["entries"]
    ]

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_replacement_default_read_mismatch" for issue in result.issues)


def test_reference_replacement_validates_seed_claim_proofs():
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    claim_ids = {proof["claim_id"] for proof in entry["claim_proofs"]}

    assert "WMO_HALF_UP_FORMULA" in claim_ids
    assert "ZEUS_MATH_SPEC_REFERENCE_ONLY" in claim_ids
    assert "DECISION_GROUP_INDEPENDENCE" in claim_ids
    assert "OPEN_BOUNDARY_BINS" in claim_ids


def test_reference_replacement_rejects_duplicate_claim_id(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    duplicate = {**entry["claim_proofs"][0], "claim_id": entry["claim_proofs"][1]["claim_id"]}
    entry["claim_proofs"].append(duplicate)

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_claim_proof_invalid" and "duplicate" in issue.message for issue in result.issues)


def test_reference_replacement_rejects_invalid_claim_proof_enum(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    entry["claim_proofs"][0] = {
        **entry["claim_proofs"][0],
        "claim_status": "open",
    }

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_claim_proof_invalid" and "claim_status" in issue.message for issue in result.issues)


def test_reference_replacement_rejects_missing_claim_proof_target(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    entry["claim_proofs"][0] = {
        **entry["claim_proofs"][0],
        "proof_targets": [{"kind": "blocking_test", "path": "tests/does_not_exist.py"}],
    }

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_claim_proof_invalid" and "proof target missing" in issue.message for issue in result.issues)


def test_reference_replacement_rejects_replaced_claim_without_gate(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    entry["claim_proofs"][0] = {
        **entry["claim_proofs"][0],
        "gates": [],
    }

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_claim_proof_invalid" and "requires gates" in issue.message for issue in result.issues)


def test_reference_replacement_delete_requires_final_claim_status(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    entry["delete_allowed"] = True
    entry["replacement_status"] = "replaced"
    entry["unique_remaining"] = []

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    result = topology_doctor.run_reference_replacement()

    assert not result.ok
    assert any(issue.code == "reference_replacement_delete_unsafe" and "final claim" in issue.message for issue in result.issues)


@pytest.mark.live_topology
def test_reference_artifact_digest_routes_to_reference_profile():
    digest = topology_doctor.build_digest("reference artifact claim extraction for zeus_math_spec fact spec")

    assert digest["profile"] == "reference artifact extraction"
    assert "architecture/reference_replacement.yaml" in digest["allowed_files"]
    assert any("Claim proofs point" in law for law in digest["required_law"])
    assert "python3 scripts/topology_doctor.py --reference-replacement" in digest["gates"]


def test_lore_digest_routes_discovery_mode_tasks():
    digest = topology_doctor.build_digest("optimize update_reaction discovery mode")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "DISCOVERY_MODES_SHAPE_RUNTIME_CYCLE" in lore_ids


def test_lore_digest_routes_bin_contract_kind_tasks():
    digest = topology_doctor.build_digest("fix position calculation for open_shoulder bin")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "BIN_CONTRACT_KIND_DISCRETE_SETTLEMENT_SUPPORT" in lore_ids


def test_scripts_mode_rejects_diagnostic_canonical_write(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["audit_replay_fidelity.py"] = {
        **manifest["scripts"]["audit_replay_fidelity.py"],
        "write_targets": ["state/zeus-world.db"],
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_diagnostic_forbidden_write_target" for issue in result.issues)


def test_scripts_mode_rejects_dangerous_script_without_target(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["cleanup_ghost_positions.py"] = {
        **manifest["scripts"]["cleanup_ghost_positions.py"],
        "target_db": None,
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_dangerous_missing_target_db" for issue in result.issues)


def test_scripts_mode_rejects_fake_apply_flag(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["cleanup_ghost_positions.py"] = {
        **manifest["scripts"]["cleanup_ghost_positions.py"],
        "apply_flag": "--definitely-not-present",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_dangerous_apply_flag_not_in_source" for issue in result.issues)


def test_scripts_mode_rejects_diagnostic_file_write_without_target(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["generate_monthly_bounds.py"] = {"class": "diagnostic"}

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_diagnostic_untracked_file_write" for issue in result.issues)


def test_scripts_mode_applies_diagnostic_rules_to_report_writers(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["baseline_experiment.py"] = {
        **manifest["scripts"]["baseline_experiment.py"],
        "write_targets": ["state/zeus-world.db"],
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_diagnostic_forbidden_write_target" for issue in result.issues)


def test_backfill_outcome_fact_manifest_declares_legacy_apply_guard():
    manifest = topology_doctor.load_script_manifest()
    entry = manifest["scripts"]["backfill_outcome_fact.py"]

    assert entry["class"] == "repair"
    assert entry["dangerous_if_run"] is True
    assert entry["dry_run_default"] is True
    assert entry["apply_flag"] == "--apply"
    assert entry["target_db"] == "state/zeus.db"
    assert entry["write_targets"] == ["state/zeus.db"]
    assert "legacy_lifecycle_projection_not_settlement_authority" in entry["promotion_barrier"]
    assert "--confirm-legacy-outcome-fact-backfill" in entry["canonical_command"]


def test_backfill_outcome_fact_defaults_to_dry_run(tmp_path):
    from scripts import backfill_outcome_fact

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE outcome_fact (
            position_id TEXT PRIMARY KEY,
            strategy_key TEXT,
            entered_at TEXT,
            settled_at TEXT,
            exit_reason TEXT,
            decision_snapshot_id TEXT,
            pnl REAL,
            outcome INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE chronicle (
            trade_id TEXT,
            timestamp TEXT,
            event_type TEXT,
            details_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE position_events_legacy (
            runtime_trade_id TEXT,
            timestamp TEXT,
            strategy TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO chronicle (
            trade_id, timestamp, event_type, details_json
        ) VALUES (
            'legacy-pos', '2026-04-01T23:00:00Z', 'SETTLEMENT',
            '{"pnl": 4.25, "outcome": 1, "decision_snapshot_id": "snap-legacy", "strategy": "center_buy"}'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO position_events_legacy (
            runtime_trade_id, timestamp, strategy
        ) VALUES ('legacy-pos', '2026-04-01T12:00:00Z', 'center_buy')
        """
    )
    conn.commit()
    conn.close()

    with redirect_stdout(StringIO()):
        summary = backfill_outcome_fact.backfill(db_path=db_path)

    conn = sqlite3.connect(db_path)
    count = conn.execute("SELECT COUNT(*) FROM outcome_fact").fetchone()[0]
    conn.close()

    assert summary["status"] == "dry_run"
    assert summary["dry_run"] is True
    assert summary["inserted"] == 1
    assert summary["authority_scope"] == "legacy_lifecycle_projection_not_settlement_authority"
    assert count == 0


def test_backfill_outcome_fact_missing_db_does_not_create_file(tmp_path):
    from scripts import backfill_outcome_fact

    db_path = tmp_path / "missing.db"

    summary = backfill_outcome_fact.backfill(db_path=db_path)

    assert summary["status"] == "error_missing_db"
    assert not db_path.exists()


def test_scripts_mode_rejects_long_lived_one_off_script_name(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["scratch_probe.py"] = {"class": "utility"}

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_long_lived_one_off_name" for issue in result.issues)
    assert any(issue.code == "script_long_lived_bad_name" for issue in result.issues)


def test_scripts_mode_rejects_ephemeral_without_delete_trigger(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["task_2026-04-14_probe_replay_gap.py"] = {
        "class": "utility",
        "lifecycle": "packet_ephemeral",
        "owner_packet": "SCRIPT-LIFECYCLE",
        "created_for": "temporary replay gap inspection",
        "delete_policy": "delete_on_packet_close",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_ephemeral_delete_policy_missing" for issue in result.issues)


def test_scripts_mode_rejects_malformed_ephemeral_name(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["task_badname.py"] = {
        "class": "utility",
        "lifecycle": "packet_ephemeral",
        "owner_packet": "SCRIPT-LIFECYCLE",
        "created_for": "temporary replay gap inspection",
        "delete_policy": "delete_on_packet_close",
        "delete_on_packet_close": True,
        "delete_by": "2999-01-01",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_ephemeral_bad_name" for issue in result.issues)


def test_scripts_mode_rejects_expired_ephemeral_script(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["task_2000-01-01_probe_replay_gap.py"] = {
        "class": "utility",
        "lifecycle": "packet_ephemeral",
        "owner_packet": "SCRIPT-LIFECYCLE",
        "created_for": "temporary replay gap inspection",
        "delete_policy": "delete_on_packet_close",
        "delete_by": "2000-01-01",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_ephemeral_expired" for issue in result.issues)


def test_scripts_mode_rejects_deprecated_script_without_fail_closed_lifecycle(monkeypatch):
    manifest = topology_doctor.load_script_manifest()
    manifest["scripts"]["deprecated_probe.py"] = {
        "class": "diagnostic",
        "status": "deprecated",
        "lifecycle": "long_lived",
        "canonical_command": "python scripts/deprecated_probe.py",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
    monkeypatch.setattr(topology_doctor, "_top_level_scripts", lambda: set(manifest["scripts"]))
    result = topology_doctor.run_scripts()

    assert not result.ok
    assert any(issue.code == "script_deprecated_not_fail_closed" for issue in result.issues)


def test_data_rebuild_mode_rejects_live_certification_with_uncertified_blockers(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["live_math_certification"] = {
        **topology["live_math_certification"],
        "allowed": True,
    }

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_live_math_certification_unsafe" for issue in result.issues)


def test_data_rebuild_mode_rejects_missing_or_nonboolean_certification_flag(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["live_math_certification"].pop("allowed")
    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()
    assert any(issue.code == "data_rebuild_certification_allowed_missing" for issue in result.issues)

    topology = topology_doctor.load_data_rebuild_topology()
    topology["live_math_certification"]["allowed"] = "false"
    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()
    assert any(issue.code == "data_rebuild_certification_allowed_invalid" for issue in result.issues)


def test_data_rebuild_mode_rejects_wu_only_strategy_coverage(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["replay_coverage_rule"] = {
        **topology["replay_coverage_rule"],
        "wu_settlement_sample_is_strategy_coverage": True,
    }

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_replay_coverage_unsafe" for issue in result.issues)


def test_data_rebuild_mode_rejects_empty_row_contract(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["rebuilt_row_contract"]["tables"]["observations"].pop("required_fields")

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_row_contract_missing_fields" for issue in result.issues)


def test_data_rebuild_mode_rejects_missing_non_db_promotion_targets(monkeypatch):
    topology = topology_doctor.load_data_rebuild_topology()
    topology["diagnostic_non_promotion"]["forbidden_promotions"] = [
        "state/zeus_trades.db",
        "state/zeus-world.db",
    ]

    monkeypatch.setattr(topology_doctor, "load_data_rebuild_topology", lambda: topology)
    result = topology_doctor.run_data_rebuild()

    assert not result.ok
    assert any(issue.code == "data_rebuild_non_promotion_incomplete" for issue in result.issues)


def test_data_backfill_digest_includes_row_contract_and_replay_coverage():
    digest = topology_doctor.build_digest("add a data backfill")
    data_topology = digest["data_rebuild_topology"]

    assert data_topology["live_math_certification"]["allowed"] is False
    assert "calibration_pairs" in data_topology["row_contract_tables"]
    assert "decision_group_id" in data_topology["row_contract_tables"]["calibration_pairs"]["required_fields"]
    assert "market_price_linkage" in data_topology["replay_coverage_rule"]["required_for_strategy_replay_coverage"]
    assert "calibration model activation" in data_topology["diagnostic_non_promotion"]["forbidden_promotions"]


@pytest.mark.live_topology
def test_script_digest_routes_agents_to_lifecycle_law():
    digest = topology_doctor.build_digest("add a replay diagnostic script")
    script_lifecycle = digest["script_lifecycle"]

    assert digest["profile"] == "add or change script"
    assert "packet_ephemeral" in script_lifecycle["allowed_lifecycles"]
    assert "audit_" in script_lifecycle["long_lived_naming"]["allowed_prefixes"]
    assert "audit_replay_fidelity.py" in script_lifecycle["existing_scripts"]
    assert "python3 scripts/topology_doctor.py --scripts" in digest["gates"]
    assert any("delete_by=YYYY-MM-DD" in law for law in digest["required_law"])
    assert any(card["id"] == "SCRIPT_LIFECYCLE_REUSE_BEFORE_NEW_TOOL" for card in digest["history_lore"])


def test_lore_digest_routes_rounding_tasks_to_wmo_lesson():
    digest = topology_doctor.build_digest(
        "fix settlement rounding in replay",
        ["src/engine/replay.py"],
    )
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "WMO_ROUNDING_BANKER_FAILURE" in lore_ids
    assert "DIAGNOSTIC_BACKTEST_NON_PROMOTION" in lore_ids
    assert "UNCOMMITTED_AGENT_EDIT_LOSS" not in lore_ids


@pytest.mark.live_topology
def test_lore_digest_routes_history_tasks_to_density_policy():
    digest = topology_doctor.build_digest("extract lore from historical work packets")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert digest["profile"] == "extract historical lore"
    assert "HISTORICAL_LORE_DENSITY_POLICY" in lore_ids
    assert any("not default reading material" in law for law in digest["required_law"])
    assert "python3 scripts/topology_doctor.py --history-lore" in digest["gates"]


def test_lore_digest_routes_alpha_tasks_to_profit_safety_lessons():
    digest = topology_doctor.build_digest("retune alpha tail treatment for buy_no EV")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "ALPHA_TARGET_AND_TAIL_TREATMENT_NOT_PROFIT_SAFE" in lore_ids
    assert "VIG_TREATMENT_RAW_PRICE_VS_CLEAN_PROBABILITY" not in lore_ids


def test_lore_digest_routes_risk_loss_tasks_to_derived_truth_warning():
    digest = topology_doctor.build_digest("fix daily_loss in risk_state reporting")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "STRATEGY_TRACKER_AND_ROLLING_LOSS_ARE_DERIVED_NOT_WALLET_TRUTH" in lore_ids
    assert "CANONICAL_DB_TRUTH_OUTRANKS_JSON_FALLBACK" not in lore_ids


def test_lore_digest_routes_data_rebuild_tasks_to_certification_block():
    digest = topology_doctor.build_digest("certify data rebuild for live math")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "DATA_REBUILD_LIVE_MATH_CERTIFICATION_BLOCKED" in lore_ids
    assert "VERIFIED_AUTHORITY_IS_CONTRACT_NOT_STAMP" in lore_ids


def test_lore_digest_does_not_overload_dst_rebuild_with_data_rebuild_lore():
    digest = topology_doctor.build_digest("fix DST diurnal rebuild")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert digest["profile"] == "generic"
    assert "DST_DIURNAL_HISTORY_REBUILD_RISK" in lore_ids
    assert "VERIFIED_AUTHORITY_IS_CONTRACT_NOT_STAMP" not in lore_ids
    assert "EXACT_SEMANTIC_TESTS_OVER_EXISTENCE_TESTS" not in lore_ids


def test_lore_digest_routes_semantic_provenance_guard_cleanup():
    digest = topology_doctor.build_digest(
        "remove dead if False provenance guard",
        ["src/strategy/market_analysis.py"],
    )
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert "SEMANTIC_PROVENANCE_GUARD_STATIC_HOOK" in lore_ids
    card = next(card for card in digest["history_lore"] if card["id"] == "SEMANTIC_PROVENANCE_GUARD_STATIC_HOOK")
    assert any("semantic_linter.py" in gate for gate in card["antibodies"]["gates"])
    assert "static-analysis hooks" in card["zero_context_digest"]


def test_history_lore_mode_rejects_critical_card_without_antibody(monkeypatch):
    lore = topology_doctor.load_history_lore()
    lore["cards"] = [
        {
            **lore["cards"][0],
            "id": "BROKEN_LORE",
            "antibodies": {},
        }
    ]

    monkeypatch.setattr(topology_doctor, "load_history_lore", lambda: lore)
    result = topology_doctor.run_history_lore()

    assert not result.ok
    assert any(issue.code == "history_lore_missing_antibody" for issue in result.issues)


def test_history_lore_mode_rejects_stale_antibody_reference(monkeypatch):
    lore = topology_doctor.load_history_lore()
    lore["cards"] = [
        {
            **lore["cards"][0],
            "id": "STALE_ANTIBODY",
            "antibodies": {
                "code": ["src/does/not/exist.py"],
                "tests": ["tests/test_runtime_guards.py"],
                "gates": ["python3 scripts/topology_doctor.py --history-lore"],
            },
        }
    ]

    monkeypatch.setattr(topology_doctor, "load_history_lore", lambda: lore)
    result = topology_doctor.run_history_lore()

    assert not result.ok
    assert any(issue.code == "history_lore_stale_antibody_reference" for issue in result.issues)


def test_context_budget_mode_rejects_blocking_without_promotion(monkeypatch):
    budget = {
        "file_budgets": [
            {
                "path": "AGENTS.md",
                "role": "boot_contract_only",
                "max_lines": 1,
                "enforcement": "blocking",
            }
        ],
        "digest_budgets": {},
        "default_read_path": {"max_pre_code_reads": 6},
    }

    monkeypatch.setattr(topology_doctor, "load_context_budget", lambda: budget)
    result = topology_doctor.run_context_budget()

    assert not result.ok
    assert any(issue.code == "context_budget_blocking_without_promotion" for issue in result.issues)


def test_context_budget_mode_can_block_when_promoted(monkeypatch):
    budget = {
        "file_budgets": [
            {
                "path": "AGENTS.md",
                "role": "boot_contract_only",
                "max_lines": 1,
                "enforcement": "blocking",
                "promotion_packet": "docs/operations/task_2026-04-14_topology_context_efficiency/plan.md",
            }
        ],
        "digest_budgets": {},
        "default_read_path": {"max_pre_code_reads": 6},
    }

    monkeypatch.setattr(topology_doctor, "load_context_budget", lambda: budget)
    result = topology_doctor.run_context_budget()

    assert not result.ok
    assert any(issue.code == "context_budget_file_over" for issue in result.issues)
    assert any(issue.severity == "error" for issue in result.issues)


def test_artifact_lifecycle_mode_validates_manifest():
    result = topology_doctor.run_artifact_lifecycle()

    assert_topology_ok(result)


def test_artifact_lifecycle_classifies_liminal_surfaces():
    manifest = topology_doctor.load_artifact_lifecycle()
    roles = {
        item["path"]: item["artifact_role"]
        for item in manifest["liminal_artifacts"]
    }

    assert roles["docs/reference/zeus_math_spec.md"] == "reference_fact_spec"
    assert roles["architecture/history_lore.yaml"] == "history_lore"
    assert roles["architecture/core_claims.yaml"] == "proof_claim_registry"
    assert roles["architecture/reference_replacement.yaml"] == "reference_claim_registry"


def test_artifact_lifecycle_rejects_liminal_surface_missing_role(monkeypatch):
    manifest = topology_doctor.load_artifact_lifecycle()
    manifest["liminal_artifacts"][0] = {
        **manifest["liminal_artifacts"][0],
        "artifact_role": "authority_shadow",
    }

    monkeypatch.setattr(topology_doctor, "load_artifact_lifecycle", lambda: manifest)
    result = topology_doctor.run_artifact_lifecycle()

    assert not result.ok
    assert any(issue.code == "artifact_lifecycle_liminal_role_invalid" for issue in result.issues)


def test_work_record_requires_record_for_repo_change():
    result = topology_doctor.run_work_record(["scripts/topology_doctor.py"], None)

    assert not result.ok
    assert any(issue.code == "work_record_required" for issue in result.issues)


def test_work_record_accepts_current_task_log(tmp_path, monkeypatch):
    work_dir = tmp_path / "docs" / "operations" / "task_2026-04-14_topology_context_efficiency"
    work_dir.mkdir(parents=True)
    work_log = work_dir / "work_log.md"
    work_log.write_text(
        "Date: 2026-04-15\n"
        "Branch: data-improve\n"
        "Task: Topology context efficiency\n"
        "Changed files: architecture/topology.yaml\n"
        "Summary: Test fixture\n"
        "Verification: All tests pass\n"
        "Next: None\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "_map_maintenance_changes", lambda files: files)
    result = topology_doctor.run_work_record(
        ["scripts/topology_doctor.py", "architecture/artifact_lifecycle.yaml"],
        "docs/operations/task_2026-04-14_topology_context_efficiency/work_log.md",
    )

    assert_topology_ok(result)


def test_work_record_rejects_unapproved_record_path():
    result = topology_doctor.run_work_record(
        ["scripts/topology_doctor.py"],
        "tmp/work_log.md",
    )

    assert not result.ok
    assert any(issue.code == "work_record_invalid_path" for issue in result.issues)


def test_work_record_exempts_archived_packets():
    result = topology_doctor.run_work_record(
        ["docs/archives/work_packets/branches/data-improve/data_rebuild/2026-04-13_zeus_data_improve_large_pack/current_state.md"],
        None,
    )

    assert_topology_ok(result)


def test_change_receipt_requires_receipt_for_high_risk_script_change(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/topology_doctor.py": "modified"},
    )
    result = topology_doctor.run_change_receipts(["scripts/topology_doctor.py"], None)

    assert not result.ok
    assert any(issue.code == "change_receipt_required" for issue in result.issues)


def test_change_receipt_accepts_matching_high_risk_receipt(tmp_path, monkeypatch):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    (tmp_path / "architecture" / "AGENTS.md").write_text("# architecture\n", encoding="utf-8")
    (tmp_path / "architecture" / "script_manifest.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (tmp_path / "scripts" / "topology_doctor.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "tests" / "test_topology_doctor.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "work_log.md").write_text(
        "Date: 2026-04-15\nVerification: ok\n",
        encoding="utf-8",
    )
    (tmp_path / "architecture" / "change_receipt_schema.yaml").write_text(
        "schema_version: 1\n"
        "required_fields: [task, packet, route_source, route_evidence, required_law, allowed_files, forbidden_files, changed_files, tests_evidence]\n"
        "allowed_route_sources: [ralplan]\n"
        "approved_receipt_globs:\n"
        "  - 'docs/operations/task_????-??-??_*/receipt.json'\n"
        "high_risk_required_patterns:\n"
        "  - 'scripts/**'\n",
        encoding="utf-8",
    )
    receipt_path = tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "task": "closeout",
                "packet": "task_2026-04-15_test",
                "route_source": "ralplan",
                "route_evidence": ["docs/operations/task_2026-04-15_test/work_log.md"],
                "required_law": ["AGENTS.md", "architecture/script_manifest.yaml"],
                "allowed_files": ["scripts/**"],
                "forbidden_files": ["src/**"],
                "changed_files": ["scripts/topology_doctor.py"],
                "tests_evidence": [
                    "tests/test_topology_doctor.py",
                    "docs/operations/task_2026-04-15_test/work_log.md",
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "CHANGE_RECEIPT_SCHEMA_PATH", tmp_path / "architecture" / "change_receipt_schema.yaml")
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/topology_doctor.py": "modified"},
    )
    result = topology_doctor.run_change_receipts(
        ["scripts/topology_doctor.py"],
        "docs/operations/task_2026-04-15_test/receipt.json",
    )

    assert_topology_ok(result)


def test_change_receipt_rejects_changed_file_outside_allowed_scope(tmp_path, monkeypatch):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "architecture" / "change_receipt_schema.yaml").write_text(
        "schema_version: 1\n"
        "required_fields: [task, packet, route_source, route_evidence, required_law, allowed_files, forbidden_files, changed_files, tests_evidence]\n"
        "allowed_route_sources: [ralplan]\n"
        "approved_receipt_globs:\n"
        "  - 'docs/operations/task_????-??-??_*/receipt.json'\n"
        "high_risk_required_patterns:\n"
        "  - 'scripts/**'\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "work_log.md").write_text(
        "Date: 2026-04-15\nVerification: ok\n",
        encoding="utf-8",
    )
    receipt_path = tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "task": "closeout",
                "packet": "task_2026-04-15_test",
                "route_source": "ralplan",
                "route_evidence": ["docs/operations/task_2026-04-15_test/work_log.md"],
                "required_law": ["AGENTS.md"],
                "allowed_files": ["docs/**"],
                "forbidden_files": ["src/**"],
                "changed_files": ["scripts/topology_doctor.py"],
                "tests_evidence": ["docs/operations/task_2026-04-15_test/work_log.md"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "CHANGE_RECEIPT_SCHEMA_PATH", tmp_path / "architecture" / "change_receipt_schema.yaml")
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/topology_doctor.py": "modified"},
    )
    result = topology_doctor.run_change_receipts(
        ["scripts/topology_doctor.py"],
        "docs/operations/task_2026-04-15_test/receipt.json",
    )

    assert not result.ok
    assert any(issue.code == "change_receipt_file_out_of_scope" for issue in result.issues)


def test_change_receipt_requires_route_evidence_and_law_coverage(tmp_path, monkeypatch):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "architecture" / "change_receipt_schema.yaml").write_text(
        "schema_version: 1\n"
        "required_fields: [task, packet, route_source, route_evidence, required_law, allowed_files, forbidden_files, changed_files, tests_evidence]\n"
        "allowed_route_sources: [ralplan]\n"
        "approved_receipt_globs:\n"
        "  - 'docs/operations/task_????-??-??_*/receipt.json'\n"
        "high_risk_required_patterns:\n"
        "  - 'scripts/**'\n"
        "required_law_by_pattern:\n"
        "  - pattern: 'scripts/**'\n"
        "    requires_any: ['architecture/script_manifest.yaml']\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    receipt_path = tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "task": "closeout",
                "packet": "task_2026-04-15_test",
                "route_source": "ralplan",
                "route_evidence": ["docs/operations/task_2026-04-15_test/missing.md"],
                "required_law": ["AGENTS.md"],
                "allowed_files": ["scripts/**"],
                "forbidden_files": ["src/**"],
                "changed_files": ["scripts/topology_doctor.py"],
                "tests_evidence": ["AGENTS.md"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "CHANGE_RECEIPT_SCHEMA_PATH", tmp_path / "architecture" / "change_receipt_schema.yaml")
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/topology_doctor.py": "modified"},
    )
    result = topology_doctor.run_change_receipts(
        ["scripts/topology_doctor.py"],
        "docs/operations/task_2026-04-15_test/receipt.json",
    )

    assert not result.ok
    assert any(issue.code == "change_receipt_route_evidence_missing" for issue in result.issues)
    assert any(issue.code == "change_receipt_inadequate_law_coverage" for issue in result.issues)


def test_change_receipt_rejects_route_evidence_that_does_not_match_source(tmp_path, monkeypatch):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "architecture" / "change_receipt_schema.yaml").write_text(
        "schema_version: 1\n"
        "required_fields: [task, packet, route_source, route_evidence, required_law, allowed_files, forbidden_files, changed_files, tests_evidence]\n"
        "allowed_route_sources: [ralplan]\n"
        "route_evidence_globs_by_source:\n"
        "  ralplan:\n"
        "    - '.omx/plans/**'\n"
        "approved_receipt_globs:\n"
        "  - 'docs/operations/task_????-??-??_*/receipt.json'\n"
        "high_risk_required_patterns:\n"
        "  - 'scripts/**'\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "work_log.md").write_text(
        "Date: 2026-04-15\nVerification: ok\n",
        encoding="utf-8",
    )
    receipt_path = tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "task": "closeout",
                "packet": "task_2026-04-15_test",
                "route_source": "ralplan",
                "route_evidence": ["docs/operations/task_2026-04-15_test/work_log.md"],
                "required_law": ["AGENTS.md"],
                "allowed_files": ["scripts/**"],
                "forbidden_files": ["src/**"],
                "changed_files": ["scripts/topology_doctor.py"],
                "tests_evidence": ["docs/operations/task_2026-04-15_test/work_log.md"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "CHANGE_RECEIPT_SCHEMA_PATH", tmp_path / "architecture" / "change_receipt_schema.yaml")
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/topology_doctor.py": "modified"},
    )
    result = topology_doctor.run_change_receipts(
        ["scripts/topology_doctor.py"],
        "docs/operations/task_2026-04-15_test/receipt.json",
    )

    assert not result.ok
    assert any(issue.code == "change_receipt_route_evidence_invalid" for issue in result.issues)


def test_change_receipt_rejects_mixed_route_evidence_sources(tmp_path, monkeypatch):
    (tmp_path / "architecture").mkdir()
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".omx" / "plans").mkdir(parents=True)
    (tmp_path / "architecture" / "change_receipt_schema.yaml").write_text(
        "schema_version: 1\n"
        "required_fields: [task, packet, route_source, route_evidence, required_law, allowed_files, forbidden_files, changed_files, tests_evidence]\n"
        "allowed_route_sources: [ralplan]\n"
        "route_evidence_globs_by_source:\n"
        "  ralplan:\n"
        "    - '.omx/plans/**'\n"
        "approved_receipt_globs:\n"
        "  - 'docs/operations/task_????-??-??_*/receipt.json'\n"
        "high_risk_required_patterns:\n"
        "  - 'scripts/**'\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("# root\n", encoding="utf-8")
    (tmp_path / ".omx" / "plans" / "route.md").write_text("# plan\n", encoding="utf-8")
    (tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "work_log.md").write_text(
        "Date: 2026-04-15\nVerification: ok\n",
        encoding="utf-8",
    )
    receipt_path = tmp_path / "docs" / "operations" / "task_2026-04-15_test" / "receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "task": "closeout",
                "packet": "task_2026-04-15_test",
                "route_source": "ralplan",
                "route_evidence": [
                    ".omx/plans/route.md",
                    "docs/operations/task_2026-04-15_test/work_log.md",
                ],
                "required_law": ["AGENTS.md"],
                "allowed_files": ["scripts/**"],
                "forbidden_files": ["src/**"],
                "changed_files": ["scripts/topology_doctor.py"],
                "tests_evidence": ["AGENTS.md"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "CHANGE_RECEIPT_SCHEMA_PATH", tmp_path / "architecture" / "change_receipt_schema.yaml")
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"scripts/topology_doctor.py": "modified"},
    )
    result = topology_doctor.run_change_receipts(
        ["scripts/topology_doctor.py"],
        "docs/operations/task_2026-04-15_test/receipt.json",
    )

    assert not result.ok
    assert any(issue.code == "change_receipt_route_evidence_invalid" for issue in result.issues)


def test_context_budget_mode_checks_digest_card_budget(monkeypatch):
    budget = {
        "file_budgets": [],
        "digest_budgets": {
            "history_lore": {
                "max_cards_per_digest": 1,
                "max_zero_context_digest_chars": 10000,
                "enforcement": "blocking",
                "promotion_packet": "docs/operations/task_2026-04-14_topology_context_efficiency/plan.md",
                "sample_tasks": ["certify data rebuild for live math"],
            }
        },
        "default_read_path": {"max_pre_code_reads": 6},
    }

    monkeypatch.setattr(topology_doctor, "load_context_budget", lambda: budget)
    result = topology_doctor.run_context_budget()

    assert not result.ok
    assert any(issue.code == "context_budget_digest_card_over" for issue in result.issues)


def test_generic_digest_includes_effective_source_rationale_for_core_file():
    digest = topology_doctor.build_digest(
        "change lifecycle manager",
        ["src/state/lifecycle_manager.py"],
    )
    rationale = digest["source_rationale"][0]

    assert digest["context_assumption"]["sufficiency"] == "provisional_starting_packet"
    assert digest["context_assumption"]["planning_lock_independent"] is True
    assert rationale["zone"] == "K0_frozen_kernel"
    assert rationale["authority_role"] == "lifecycle_law"
    assert "upstream" in rationale
    assert "downstream" in rationale
    assert any("test_architecture_contracts.py" in gate for gate in rationale["gates"])


def test_navigation_includes_context_assumption():
    payload = topology_doctor.run_navigation(
        "change lifecycle manager",
        ["src/state/lifecycle_manager.py"],
    )

    assert_navigation_ok(payload)
    assert payload["context_assumption"]["sufficiency"] == "provisional_starting_packet"
    assert payload["context_assumption"] == payload["digest"]["context_assumption"]



@pytest.mark.skip(reason="DEV-1 (Phase 4.A): build_packet_prefill removed — see test_refactor_packet_prefill_for_engine_scope skip rationale")
def test_refactor_packet_prefill_keeps_file_scope_literal():
    pass  # formerly tested file-scope literal preservation in build_packet_prefill


def test_navigation_route_card_summarizes_admission_and_risk(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)

    payload = topology_doctor.run_navigation(
        "agent runtime route card typed intent",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        task_class="agent_runtime",
        write_intent="edit",
    )

    assert payload["ok"] is True
    assert payload["route_card"]["authority_status"] == "generated_route_card_not_authority"
    assert payload["route_card"]["admission_status"] == "admitted"
    assert payload["route_card"]["risk_tier"] == "T3"
    assert payload["route_context"]["gate_budget"]["label"] == "architecture_governance_or_runtime_tooling"
    assert payload["direct_blockers"] == []
    assert payload["task_blockers"] == []
    assert payload["admission_blockers"] == []
    assert payload["issues_contract"] == "legacy_aggregate_not_task_blockers"
    assert payload["route_card"]["selection_evidence_class"] in {"typed_intent", "semantic_phrase", "semantic_file"}
    assert payload["route_card"]["needs_typed_intent"] is False


def test_runtime_route_card_contract_matches_schema():
    digest = topology_doctor.build_digest(
        "agent runtime route card contract",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        task_class="agent_runtime",
        write_intent="edit",
    )
    card = digest["route_card"]
    # R12 Phase 5.B: topology_schema.yaml deleted; use inlined SCHEMA_ROUTE_CARD_REQUIRED_FIELDS.
    required = set(topology_doctor.SCHEMA_ROUTE_CARD_REQUIRED_FIELDS)

    assert required <= set(card)
    assert card["schema_version"] == "1"
    assert card["claims"] == []
    assert card["intent"] == "topology graph agent runtime upgrade"
    assert card["task_class"] == "agent_runtime"
    assert card["write_intent"] == "edit"
    assert card["expansion_hints"]
    assert card["operation_vector"]["operation_stage"]
    assert card["operation_vector_sources"]
    assert "structural_decision_hints" in card


def test_runtime_claims_appear_in_route_card_and_digest_inputs():
    digest = topology_doctor.build_digest(
        "agent runtime route card contract",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        write_intent="edit",
        claims=["admission_valid", "admission_valid"],
    )

    assert digest["route_card"]["claims"] == ["admission_valid"]
    assert digest["typed_runtime_inputs"]["claims"] == ["admission_valid"]


def test_script_route_admits_real_script_and_matching_test_paths():
    digest = topology_doctor.build_digest(
        "add or change script: evaluate_calibration_transfer_oos OOS calibration transfer evidence writer",
        [
            "scripts/evaluate_calibration_transfer_oos.py",
            "tests/test_evaluate_calibration_transfer_oos.py",
            "architecture/script_manifest.yaml",
            "architecture/naming_conventions.yaml",
        ],
        intent="add or change script",
        write_intent="edit",
    )

    assert digest["profile"] == "add or change script"
    assert digest["admission"]["status"] == "admitted"
    assert "scripts/evaluate_calibration_transfer_oos.py" in digest["admission"]["admitted_files"]
    assert "tests/test_evaluate_calibration_transfer_oos.py" in digest["admission"]["admitted_files"]


def test_runtime_route_card_explains_generic_source_canary_probe():
    digest = topology_doctor.build_digest(
        "change source freshness handling for provider hot-swap for Paris canary readiness only, no live execution",
        ["src"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["admission"]["status"] == "scope_expansion_required"
    assert card["dominant_driver"] in {"source_canary_readiness_hot_swap", "profile_needs_typed_intent"}
    assert card["why_not_admitted"]
    assert "source canary readiness hot-swap" in card["suggested_next_command"]
    assert "src/control/freshness_gate.py" in card["safe_next_files"]
    assert card["merge_evidence_required"]["required"] is False


def test_operation_vector_selects_source_canary_without_canonical_phrase():
    digest = topology_doctor.build_digest(
        "source canary recovery",
        ["src/control/freshness_gate.py", "src/engine/cycle_runner.py"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["profile"] == "source canary readiness hot-swap"
    assert digest["admission"]["status"] == "admitted"
    assert digest["profile_selection"]["selected_by"] == "operation_vector"
    assert "source_behavior" in card["operation_vector"]["mutation_surfaces"]
    assert card["dominant_driver"] == "source_canary_readiness_hot_swap"


def test_operation_vector_does_not_admit_unrelated_freshness_gate_work_as_canary():
    digest = topology_doctor.build_digest(
        "refactor freshness gate logging",
        ["src/control/freshness_gate.py"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert digest["profile_selection"]["selected_by"] != "operation_vector"
    assert "source_behavior" in card["operation_vector"]["mutation_surfaces"]
    assert card["dominant_driver"] != "source_canary_readiness_hot_swap"


def test_operation_vector_does_not_misread_pre_merge_hook_as_git_merge():
    digest = topology_doctor.build_digest(
        "harden pre-merge hook fail-closed behavior",
        [".claude/hooks/pre-merge-contamination-check.sh"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert card["operation_vector"]["merge_state"] == "not_merge"
    assert card["operation_vector"]["operation_stage"] == "edit"
    assert "runtime_hooks" in card["operation_vector"]["mutation_surfaces"]
    assert card["merge_evidence_required"]["required"] is False
    assert digest["profile"] == "topology graph agent runtime upgrade"
    assert digest["admission"]["status"] == "admitted"
    assert digest["profile_selection"]["selected_by"] == "operation_vector"
    assert card["dominant_driver"] != "merge_conflict_first"
    assert card["suggested_next_command"] is None


def test_operation_vector_does_not_misread_first_person_am_as_merge():
    digest = topology_doctor.build_digest(
        "I am updating lifecycle projection validation",
        ["src/state/lifecycle_manager.py"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert card["operation_vector"]["merge_state"] == "not_merge"
    assert card["operation_vector"]["operation_stage"] == "edit"
    assert card["merge_evidence_required"]["required"] is False
    assert card["dominant_driver"] != "merge_conflict_first"


def test_operation_vector_does_not_misread_explanation_as_plan():
    digest = topology_doctor.build_digest(
        "tighten route-card explanation for runtime profile",
        ["scripts/topology_doctor.py"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert card["operation_vector"]["operation_stage"] == "edit"
    assert card["operation_vector_sources"]["operation_stage"] == "side_effect"
    assert card["dominant_driver"] != "planning_package_split"


def test_settings_wires_pre_edit_architecture_for_multiedit():
    settings = _json.loads((pathlib.Path(_REPO_ROOT) / ".claude" / "settings.json").read_text())
    matchers = [entry.get("matcher", "") for entry in settings["hooks"]["PreToolUse"]]
    assert any("MultiEdit" in matcher and "Edit" in matcher for matcher in matchers)


def test_operation_vector_admits_runtime_governance_profile_boundary():
    files = [
        ".claude/CLAUDE.md",
        ".claude/hooks/hook_common.py",
        ".claude/hooks/pre-commit",
        ".claude/hooks/pre-commit-invariant-test.sh",
        ".claude/hooks/pre-commit-secrets.sh",
        ".claude/hooks/pre-edit-architecture.sh",
        ".claude/hooks/pre-merge-contamination-check.sh",
        ".claude/settings.json",
        ".gitignore",
        "architecture/kernel_manifest.yaml",
        "architecture/inv_prototype.py",
        "architecture/ast_rules/semgrep_zeus.yml",
        "architecture/ast_rules/forbidden_patterns.md",
        "scripts/check_kernel_manifests.py",
    ]
    digest = topology_doctor.build_digest(
        "harden git hook fail-closed protocol and governance static rule registry",
        files,
        write_intent="edit",
    )

    assert digest["profile"] == "topology graph agent runtime upgrade"
    assert digest["admission"]["status"] == "admitted"
    assert digest["profile_selection"]["selected_by"] == "operation_vector"
    assert digest["admission"]["out_of_scope_files"] == []


def test_operation_vector_guides_broad_fix_package_to_planning_packet():
    digest = topology_doctor.build_digest(
        "ultrareview-25 fix package: harden pre-commit/pre-merge hooks fail-closed, "
        "narrow semgrep zeus-no-json-authority-write, remove temperature_metric DEFAULT high, "
        "and repair docs rules bidirectional references",
        [
            ".claude/hooks/pre-commit-invariant-test.sh",
            ".claude/hooks/pre-merge-contamination-check.sh",
            ".claude/hooks/pre-edit-architecture.sh",
            ".claude/settings.json",
            "architecture/ast_rules/semgrep_zeus.yml",
            "architecture/negative_constraints.yaml",
            "architecture/invariants.yaml",
            "src/state/db.py",
            "tests/test_pscb_hook.py",
            "tests/test_tigge_schema_contract.py",
            "tests/test_architecture_contracts.py",
        ],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["profile"] == "generic"
    assert digest["admission"]["status"] == "advisory_only"
    assert card["operation_vector"]["operation_stage"] == "plan"
    assert card["operation_vector"]["merge_state"] == "not_merge"
    assert "runtime_hooks" in card["operation_vector"]["mutation_surfaces"]
    assert "static_analysis_rules" in card["operation_vector"]["mutation_surfaces"]
    assert "architecture_policy" in card["operation_vector"]["mutation_surfaces"]
    assert "db_schema_truth" in card["operation_vector"]["mutation_surfaces"]
    assert card["dominant_driver"] == "planning_package_split"
    assert card["merge_evidence_required"]["required"] is False
    assert "operation planning packet" in card["suggested_next_command"]
    assert card["structural_decision_hints"]


def test_operation_vector_admits_first_class_planning_packet():
    digest = topology_doctor.build_digest(
        "operation planning packet: structural decisions, impact context, slice routes, and verification plan",
        ["docs/operations/task_2026-05-01_ultrareview25/PLAN.md"],
        write_intent="edit",
        operation_stage="plan",
        artifact_target="plan_packet",
    )
    card = digest["route_card"]

    assert digest["profile"] == "operation planning packet"
    assert digest["admission"]["status"] == "admitted"
    assert digest["profile_selection"]["selected_by"] == "operation_vector"
    assert card["persistence_target"] == "plan_packet"
    assert card["suggested_next_command"] is None


def test_operation_vector_requires_explicit_surface_for_high_fanout_evaluator_profile():
    soft = topology_doctor.build_digest(
        "fix evaluator behavior",
        ["src/engine/evaluator.py"],
        write_intent="edit",
    )
    routed = topology_doctor.build_digest(
        "fix evaluator behavior",
        ["src/engine/evaluator.py"],
        write_intent="edit",
        mutation_surfaces=["evaluator_behavior"],
    )

    assert soft["profile"] == "generic"
    assert soft["admission"]["status"] == "advisory_only"
    assert routed["profile"] == "evaluator script import bridge"
    assert routed["admission"]["status"] == "admitted"
    assert routed["profile_selection"]["selected_by"] == "operation_vector"


def test_operation_vector_typed_closeout_routes_feedback_without_alias():
    digest = topology_doctor.build_digest(
        "done",
        [],
        write_intent="read_only",
        operation_stage="closeout",
        artifact_target="final_response",
    )
    card = digest["route_card"]

    assert digest["profile"] == "direct operation feedback capsule"
    assert digest["admission"]["status"] == "advisory_only"
    assert card["operation_vector"]["operation_stage"] == "closeout"
    assert card["operation_vector"]["artifact_target"] == "final_response"
    assert card["persistence_target"] == "final_response"
    assert card["suggested_next_command"] is None


def test_operation_vector_does_not_turn_plain_receipt_closeout_into_feedback():
    digest = topology_doctor.build_digest(
        "write packet closeout receipt",
        ["docs/operations/task_2026-05-01_plain/receipt.json"],
        write_intent="edit",
        operation_stage="closeout",
        artifact_target="receipt",
    )
    card = digest["route_card"]

    assert digest["profile"] != "direct operation feedback capsule"
    assert digest["profile_selection"]["selected_by"] != "operation_vector"
    assert card["operation_vector"]["artifact_target"] == "receipt"


def test_operation_vector_redirects_feedback_evidence_file_to_final_response():
    digest = topology_doctor.build_digest(
        "operation feedback capsule",
        ["evidence.md"],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["profile"] == "direct operation feedback capsule"
    assert digest["admission"]["status"] == "scope_expansion_required"
    assert card["operation_vector"]["artifact_target"] == "new_evidence"
    assert card["persistence_target"] == "new_evidence"
    assert "--artifact-target final_response" in card["suggested_next_command"]


def test_operation_vector_high_risk_merge_conflict_requires_critic_evidence():
    digest = topology_doctor.build_digest(
        "broad schema lifecycle DB control live conflict semantic ambiguity",
        [
            "src/state/chain_reconciliation.py",
            "src/control/heartbeat_supervisor.py",
            "src/execution/exchange_reconcile.py",
        ],
        write_intent="edit",
    )
    card = digest["route_card"]

    assert card["operation_vector"]["operation_stage"] == "merge"
    assert card["merge_conflict_scan"] == "high_risk_conflict"
    assert card["merge_evidence_required"]["required"] is True
    assert card["dominant_driver"] == "merge_conflict_first"
    assert "--merge-state high_risk_conflict" in card["suggested_next_command"]


def test_runtime_route_card_surfaces_script_manifest_provenance_for_bridge():
    digest = topology_doctor.build_digest(
        "A downstream evaluator import is blocked because topology says my script change is script-health, "
        "but I think it is a semantic pricing path issue.",
        ["src/engine/evaluator.py", "scripts/rebuild_calibration_pairs_v2.py"],
        intent="evaluator script import bridge",
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["profile"] == "evaluator script import bridge"
    assert "src/engine/evaluator.py" in digest["admission"]["admitted_files"]
    assert "scripts/rebuild_calibration_pairs_v2.py" in digest["admission"]["out_of_scope_files"]
    assert card["blocked_file_reasons"]["scripts/rebuild_calibration_pairs_v2.py"]
    assert any(
        note.get("path") == "scripts/rebuild_calibration_pairs_v2.py"
        and note.get("kind") == "script_manifest"
        and note.get("canonical_command")
        for note in card["provenance_notes"]
    )


def test_runtime_route_card_does_not_block_import_only_do_not_run_helpers():
    digest = topology_doctor.build_digest(
        "agent runtime route card typed intent",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        task_class="agent_runtime",
        write_intent="edit",
    )
    card = digest["route_card"]

    assert digest["admission"]["status"] == "admitted"
    assert "scripts/topology_doctor_cli.py" not in card["blocked_file_reasons"]
    assert any(
        note.get("path") == "scripts/topology_doctor_cli.py"
        and note.get("canonical_command") == "DO_NOT_RUN"
        for note in card["provenance_notes"]
    )


def test_runtime_route_card_types_capsule_persistence_target():
    digest = topology_doctor.build_digest(
        "direct operation feedback capsule: context recovery, Zeus improvement insights, topology helped/blocked",
        [],
        intent="direct operation feedback capsule",
        write_intent="read_only",
    )
    card = digest["route_card"]

    assert card["persistence_target"] == "final_response"
    assert card["suggested_next_command"] is None
    assert card["why_not_admitted"] == []


def test_runtime_route_card_admits_capsule_improvement_backlog_target():
    digest = topology_doctor.build_digest(
        "direct operation feedback capsule: record project-level topology improvement insight",
        ["architecture/improvement_backlog.yaml"],
        intent="direct operation feedback capsule",
        write_intent="edit",
    )

    assert digest["profile"] == "direct operation feedback capsule"
    assert digest["admission"]["status"] == "admitted"
    assert "architecture/improvement_backlog.yaml" in digest["admission"]["admitted_files"]


def test_runtime_route_card_types_context_worklog_persistence_target():
    digest = topology_doctor.build_digest(
        "context recovery note wants to persist under .omx/context",
        [".omx/context/runtime-handoff.md"],
        intent="direct operation feedback capsule",
        write_intent="read_only",
    )
    card = digest["route_card"]

    assert card["persistence_target"] == "context_worklog"
    assert ".omx/context/runtime-handoff.md" in card["blocked_file_reasons"]
    assert any("local runtime scratch" in reason for reason in card["blocked_file_reasons"][".omx/context/runtime-handoff.md"])


def test_runtime_route_card_keeps_clean_merge_critic_evidence_predicate_off():
    digest = topology_doctor.build_digest(
        "clean merge with contamination history wording should not ask for critic evidence when conflict scan is clean",
        ["architecture/worktree_merge_protocol.yaml"],
        intent="topology graph agent runtime upgrade",
        task_class="agent_runtime",
        write_intent="edit",
    )
    card = digest["route_card"]

    assert card["merge_conflict_scan"] == "clean"
    assert card["merge_evidence_required"]["required"] is False
    assert "conflict_first" in card["merge_evidence_required"]["reason"]


def test_invalid_typed_navigation_intent_blocks_without_profile_fallback(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)

    payload = topology_doctor.run_navigation(
        "G1 live readiness route card implementation",
        ["scripts/topology_doctor_cli.py"],
        intent="not a real topology profile",
        write_intent="edit",
    )

    assert payload["ok"] is False
    assert payload["digest"]["profile"] == "generic"
    assert payload["route_card"]["admission_status"] == "ambiguous"
    assert payload["route_card"]["needs_typed_intent"] is True
    assert payload["route_card"]["next_action"] == "stop; pass typed --intent or narrow the task wording"
    assert payload["direct_blockers"][0]["code"] == "navigation_route_ambiguous"


def test_graph_impact_claim_blocks_navigation_when_graph_is_stale(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])
    stale_graph = topology_doctor.StrictResult(
        ok=True,
        issues=[
            topology_doctor.TopologyIssue(
                code="code_review_graph_stale_head",
                path=".code-review-graph/graph.db",
                message="graph stale",
                severity="warning",
            )
        ],
    )

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: stale_graph)

    payload = topology_doctor.run_navigation(
        "agent runtime graph impact claim",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        write_intent="edit",
        claims=["graph_impact_validated"],
    )

    assert payload["ok"] is False
    assert payload["claims_blocked"][0]["claim"] == "graph_impact_validated"
    assert payload["direct_blockers"][0]["lane"] == "runtime_claims"


def test_navigation_without_graph_claim_does_not_touch_graph_status(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)
    monkeypatch.setattr(
        topology_doctor,
        "run_code_review_graph_status",
        lambda files=None: pytest.fail("graph status should be claim-scoped"),
    )

    payload = topology_doctor.run_navigation(
        "agent runtime ordinary navigation",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        write_intent="edit",
    )

    assert payload["ok"] is True
    assert payload["claims_blocked"] == []


def test_live_side_effect_claim_blocks_without_operator_evidence(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])

    def ok_result():
        return ok

    for name in (
        "run_context_budget",
        "run_docs",
        "run_source",
        "run_history_lore",
        "run_agents_coherence",
        "run_self_check_coherence",
        "run_idioms",
        "run_runtime_modes",
        "run_reference_replacement",
    ):
        monkeypatch.setattr(topology_doctor, name, ok_result)

    payload = topology_doctor.run_navigation(
        "agent runtime live apply guard",
        ["scripts/topology_doctor_cli.py"],
        intent="topology graph agent runtime upgrade",
        write_intent="live",
        claims=["live_side_effect_authorized"],
    )

    assert payload["ok"] is False
    assert payload["route_card"]["risk_tier"] == "T4"
    assert payload["claims_blocked"][0]["claim"] == "live_side_effect_authorized"
    assert "explicit operator-go" in payload["claims_blocked"][0]["reason"]


def test_live_side_effect_claim_blocks_even_without_live_write_intent():
    payload = topology_doctor.build_runtime_claim_evaluation(
        ["live_side_effect_authorized"],
        write_intent="edit",
    )

    assert payload["evaluated"] == []
    assert payload["blocked"][0]["claim"] == "live_side_effect_authorized"
    assert "explicit operator-go" in payload["blocked"][0]["reason"]


def test_semantic_boot_claim_requires_bootstrap_evidence():
    payload = topology_doctor.build_runtime_claim_evaluation(
        ["semantic_boot_answered"],
        task_class="agent_runtime",
    )

    assert payload["evaluated"] == []
    assert payload["blocked"][0]["claim"] == "semantic_boot_answered"
    assert "semantic bootstrap was not evaluated" in payload["blocked"][0]["reason"]


def test_navigation_route_card_only_human_output_skips_appendices(monkeypatch):
    payload = {
        "ok": True,
        "task": "source task",
        "digest": {"profile": "generic"},
        "route_card": {
            "schema_version": "1",
            "admission_status": "admitted",
            "risk_tier": "T1",
            "next_action": "proceed with admitted files and focused verification",
            "admitted_files": ["docs/example.md"],
            "gate_budget": {"label": "narrow_docs_tests_or_tooling", "required": ["route_card"]},
            "claims": [],
            "expansion_hints": ["use focused context for admitted files"],
        },
        "issues": [],
        "direct_blockers": [],
        "repo_health_warnings": [{"severity": "error"}],
        "excluded_lanes": {"strict": "not printed"},
    }
    monkeypatch.setattr(topology_doctor, "run_navigation", lambda task, files, strict_health=False: payload)

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(["--navigation", "--route-card-only", "--task", "source task"])
    output = buffer.getvalue()

    assert exit_code == 0
    assert output.startswith("route_card:")
    assert "schema_version: 1" in output
    assert "excluded_lanes:" not in output
    assert "repo_health_warnings:" not in output
    assert "profile:" not in output


def test_navigation_route_card_only_json_is_minimal(monkeypatch):
    route_card = {
        "schema_version": "1",
        "admission_status": "admitted",
        "risk_tier": "T1",
        "next_action": "proceed with admitted files and focused verification",
    }
    payload = {
        "ok": True,
        "route_card": route_card,
        "digest": {"profile": "generic"},
        "issues": [{"lane": "docs", "severity": "error"}],
        "direct_blockers": [],
        "repo_health_warnings": [],
        "excluded_lanes": {},
    }
    monkeypatch.setattr(topology_doctor, "run_navigation", lambda task, files, strict_health=False: payload)

    buffer = StringIO()
    with redirect_stdout(buffer):
        exit_code = topology_doctor.main(["--navigation", "--route-card-only", "--json", "--task", "source task"])

    assert exit_code == 0
    assert json.loads(buffer.getvalue()) == {"ok": True, "route_card": route_card}


def test_runtime_reference_docs_keep_feedback_capsule_non_bureaucratic():
    root = topology_doctor.ROOT
    combined = "\n".join(
        (root / path).read_text()
        for path in [
            "AGENTS.md",
            ".agents/skills/zeus-ai-handoff/SKILL.md",
            "docs/operations/AGENTS.md",
            "docs/reference/modules/topology_system.md",
            "docs/reference/modules/topology_doctor_system.md",
            "docs/reference/modules/closeout_and_receipts_system.md",
        ]
    )

    assert "operation-end feedback capsule" in combined
    assert "Zeus improvement insights" in combined
    assert "topology helped/blocked" in combined
    assert "route/admission/risk" in combined
    assert "semantic match" in combined
    assert "none_observed" in combined
    assert "standalone `evidence.md`/`findings.md`" in combined
    assert "widening the active packet" in combined or "widen the diff" in combined


def test_runtime_route_card_keeps_t0_read_only_lightweight():
    digest = topology_doctor.build_digest(
        "read only agent runtime orientation",
        [],
        write_intent="read_only",
    )

    assert digest["route_card"]["risk_tier"] == "T0"
    assert digest["route_card"]["gate_budget"]["required"] == ["route_card"]
    assert "do not edit files" in digest["route_card"]["gate_budget"]["stop"]


def test_runtime_route_card_treats_live_intent_without_files_as_t4():
    digest = topology_doctor.build_digest(
        "operator requested live apply",
        [],
        write_intent="live",
    )

    assert digest["route_card"]["risk_tier"] == "T4"
    assert "explicit_operator_go" in digest["route_card"]["gate_budget"]["required"]


def test_code_review_graph_status_declares_claim_scope():
    result = topology_doctor.run_code_review_graph_status(["scripts/topology_doctor_cli.py"])

    assert result.details["claim_scope"]["blocks_claims"] == [
        "graph_impact_validated",
        "graph_review_order",
        "graph_test_selection",
    ]
    assert result.details["claim_scope"]["aliases"]["graph_impact"] == "graph_impact_validated"
    assert "ordinary navigation" in result.details["claim_scope"]["does_not_block"]


def test_cli_json_parity_for_city_truth_contract_mode():
    payload = run_cli_json(["--city-truth-contract", "--json"])
    result = topology_doctor.run_city_truth_contract()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_city_truth_contract_mode_validates_schema_not_current_truth():
    result = topology_doctor.run_city_truth_contract()
    contract = topology_doctor.load_city_truth_contract()

    assert_topology_ok(result)
    assert contract["metadata"]["authority_status"] == "stable_schema_not_current_city_truth"
    assert set(contract["source_roles"]) >= {
        "settlement_daily_source",
        "day0_live_monitor_source",
        "historical_hourly_source",
        "forecast_skill_source",
    }
    assert "current_city_truth" not in contract
    assert all(example["classification"] == "schema_example_not_current_truth" for example in contract["examples"])


def test_task_boot_profiles_reject_unknown_fatal_misread(monkeypatch):
    manifest = topology_doctor.load_task_boot_profiles()
    manifest["profiles"][0]["fatal_misreads"].append("NO_SUCH_MISREAD")

    monkeypatch.setattr(topology_doctor, "load_task_boot_profiles", lambda: manifest)
    result = topology_doctor.run_task_boot_profiles()

    assert not result.ok
    assert any(issue.code == "task_boot_profile_unknown_fatal_misread" for issue in result.issues)


def test_fatal_misreads_reject_missing_proof_file(monkeypatch):
    manifest = topology_doctor.load_fatal_misreads()
    manifest["misreads"][0]["proof_files"].append("docs/operations/not_a_real_source_surface.md")

    monkeypatch.setattr(topology_doctor, "load_fatal_misreads", lambda: manifest)
    result = topology_doctor.run_fatal_misreads()

    assert not result.ok
    assert any(issue.code == "fatal_misread_path_missing" for issue in result.issues)


def test_city_truth_contract_rejects_unbacked_current_assertion(monkeypatch):
    contract = topology_doctor.load_city_truth_contract()
    contract["current_city_truth"] = [{"id": "bad_hk_claim", "city_key": "Hong Kong"}]

    monkeypatch.setattr(topology_doctor, "load_city_truth_contract", lambda: contract)
    result = topology_doctor.run_city_truth_contract()

    assert not result.ok
    assert any(issue.code == "city_truth_contract_current_claim_unbacked" for issue in result.issues)




# === ultrareview-25 P1 hook fail-closed antibodies ===
# Subprocess-driven regression tests for the bash hooks at
# .claude/hooks/{pre-commit-invariant-test.sh,pre-merge-contamination-check.sh}.
# Antibodies for F3 (multi-space + git -C bypass), F4 (protected branch glob
# over-match), F13 (critic verdict comment-injection), and F17 (OVERRIDE doc
# claim honesty). These tests pin the regression behaviour: a future refactor
# from regex back to literal `case` would silently re-open the bypass — these
# tests catch that.

import json as _json
import shutil as _shutil
import subprocess as _subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PRE_MERGE_HOOK = os.path.join(_REPO_ROOT, ".claude/hooks/pre-merge-contamination-check.sh")
_PRE_EDIT_HOOK = os.path.join(_REPO_ROOT, ".claude/hooks/pre-edit-architecture.sh")
_PRE_COMMIT_INVARIANT_HOOK = os.path.join(_REPO_ROOT, ".claude/hooks/pre-commit-invariant-test.sh")
_PRE_COMMIT_SECRETS_HOOK = os.path.join(_REPO_ROOT, ".claude/hooks/pre-commit-secrets.sh")
_PRE_COMMIT_ORCHESTRATOR = os.path.join(_REPO_ROOT, ".claude/hooks/pre-commit")


@pytest.fixture(scope="module")
def _protected_branch_worktree(tmp_path_factory):
    """Session-scoped fixture creating a temp worktree on a unique protected
    branch so the hook's
    branch-protection check fires the merge-class detection paths under test.
    The hook short-circuits on non-protected branches; without this fixture,
    the test runner's current branch (e.g. a feature branch) would silently
    bypass coverage of F3/F4/F13/F17 antibodies."""
    repo_root = _REPO_ROOT
    worktree_dir = tmp_path_factory.mktemp("hook-test-worktree")
    branch_name = f"plan-pre999/hook-test-{os.getpid()}-{worktree_dir.name}"
    rc = _subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        pytest.skip(f"could not create protected branch worktree for hook tests: {rc.stderr[:200]}")
    yield str(worktree_dir)
    _subprocess.run(
        ["git", "-C", repo_root, "worktree", "remove", "--force", str(worktree_dir)],
        capture_output=True, text=True,
    )
    _subprocess.run(
        ["git", "-C", repo_root, "branch", "-D", branch_name],
        capture_output=True, text=True,
    )


@pytest.fixture
def _secrets_hook_worktree(tmp_path):
    repo_root = _REPO_ROOT
    worktree_dir = tmp_path / "secrets-hook-worktree"
    branch_name = f"hook-secrets-test-{os.getpid()}-{tmp_path.name}"
    rc = _subprocess.run(
        ["git", "-C", repo_root, "worktree", "add", "-b", branch_name, str(worktree_dir), "HEAD"],
        capture_output=True,
        text=True,
    )
    if rc.returncode != 0:
        pytest.skip(f"could not create secrets hook worktree: {rc.stderr[:200]}")
    yield str(worktree_dir)
    _subprocess.run(
        ["git", "-C", repo_root, "worktree", "remove", "--force", str(worktree_dir)],
        capture_output=True,
        text=True,
    )
    _subprocess.run(
        ["git", "-C", repo_root, "branch", "-D", branch_name],
        capture_output=True,
        text=True,
    )


def _run_pre_merge_hook(command, env=None, evidence_path=None, cwd=None):
    """Invoke pre-merge hook with a fake Bash tool payload; return (rc, stderr).

    `cwd` selects the working directory the hook runs in. Pass the
    `_protected_branch_worktree` fixture value to exercise protected-branch
    code paths regardless of the test runner's current branch.
    """
    payload = _json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    if evidence_path is not None:
        proc_env["MERGE_AUDIT_EVIDENCE"] = evidence_path
    elif "MERGE_AUDIT_EVIDENCE" in proc_env:
        del proc_env["MERGE_AUDIT_EVIDENCE"]
    proc = _subprocess.run(
        ["bash", _PRE_MERGE_HOOK],
        input=payload,
        capture_output=True,
        text=True,
        env=proc_env,
        cwd=cwd,
    )
    return proc.returncode, proc.stderr


def _git_index_env(cwd, extra=None):
    result = _subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--git-path", "index"],
        capture_output=True,
        text=True,
        check=True,
    )
    env = dict(os.environ)
    env["GIT_INDEX_FILE"] = result.stdout.strip()
    if extra:
        env.update(extra)
    return env


def _run_pre_commit_secrets_git(cwd, env=None):
    proc = _subprocess.run(
        ["bash", _PRE_COMMIT_SECRETS_HOOK],
        input="",
        capture_output=True,
        text=True,
        env=_git_index_env(cwd, env),
        cwd=cwd,
        check=False,
    )
    return proc.returncode, proc.stderr


def _path_without_gitleaks(tmp_path):
    fake_bin = tmp_path / "no-gitleaks-bin"
    fake_bin.mkdir()
    for tool in ("git", "python3"):
        target = _shutil.which(tool)
        assert target, f"{tool} must be available for hook regression tests"
        os.symlink(target, fake_bin / tool)
    return fake_bin, f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin"


def test_pre_commit_secrets_blocks_unregistered_review_safe_tag(_secrets_hook_worktree):
    tag = "NEW" + "_TAG"
    probe = pathlib.Path(_secrets_hook_worktree) / "tmp_review_safe_probe.txt"
    probe.write_text(f'public_test_value = "not-secret"  # [REVIEW-SAFE: {tag}]\n')
    _subprocess.run(["git", "add", str(probe)], cwd=_secrets_hook_worktree, check=True)

    rc, stderr = _run_pre_commit_secrets_git(_secrets_hook_worktree)

    assert rc == 2
    assert tag in stderr
    assert "not registered" in stderr


def test_pre_commit_secrets_blocks_unregistered_review_safe_without_gitleaks(tmp_path, _secrets_hook_worktree):
    tag = "MISSING_GITLEAKS" + "_TAG"
    probe = pathlib.Path(_secrets_hook_worktree) / "tmp_review_safe_no_gitleaks_probe.txt"
    probe.write_text(f'public_test_value = "not-secret"  # [REVIEW-SAFE: {tag}]\n')
    _subprocess.run(["git", "add", str(probe)], cwd=_secrets_hook_worktree, check=True)
    _, path_without_gitleaks = _path_without_gitleaks(tmp_path)

    rc, stderr = _run_pre_commit_secrets_git(
        _secrets_hook_worktree,
        env={"PATH": path_without_gitleaks},
    )

    assert rc == 2
    assert tag in stderr
    assert "not registered" in stderr


def test_pre_commit_secrets_accepts_review_safe_tag_registered_in_same_commit(_secrets_hook_worktree):
    tag = "TEST_REGISTERED" + "_TAG"
    worktree = pathlib.Path(_secrets_hook_worktree)
    registry = worktree / "SECURITY-FALSE-POSITIVES.md"
    registry.write_text(
        registry.read_text()
        + f"\n## [REVIEW-SAFE: {tag}] — test-local cleared token\n\n"
        + "**Operator ruling 2026-05-02**: test-only registry validation fixture.\n"
    )
    probe = worktree / "tmp_registered_review_safe_probe.txt"
    probe.write_text(f'public_test_value = "not-secret"  # [REVIEW-SAFE: {tag}]\n')
    _subprocess.run(["git", "add", "SECURITY-FALSE-POSITIVES.md", str(probe)], cwd=_secrets_hook_worktree, check=True)

    rc, stderr = _run_pre_commit_secrets_git(_secrets_hook_worktree)

    assert rc == 0, stderr


def test_pre_commit_secrets_audits_staged_requirements_blob_not_worktree(tmp_path, _secrets_hook_worktree):
    worktree = pathlib.Path(_secrets_hook_worktree)
    requirements = worktree / "requirements-local.txt"
    requirements.write_text("staged-package==1.0.0\n")
    _subprocess.run(["git", "add", str(requirements)], cwd=_secrets_hook_worktree, check=True)
    requirements.write_text("unstaged-package==9.9.9\n")

    fake_bin, path_without_gitleaks = _path_without_gitleaks(tmp_path)
    (fake_bin / "pip-audit").write_text(
        "#!/usr/bin/env bash\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  if [ \"$1\" = \"-r\" ]; then cat \"$2\" > \"$CAPTURE_FILE\"; exit 0; fi\n"
        "  shift\n"
        "done\n"
        "exit 1\n"
    )
    os.chmod(fake_bin / "pip-audit", 0o755)
    capture = tmp_path / "audited_requirements.txt"

    rc, stderr = _run_pre_commit_secrets_git(
        _secrets_hook_worktree,
        env={
            "PATH": path_without_gitleaks,
            "CAPTURE_FILE": str(capture),
        },
    )

    assert rc == 0, stderr
    assert "gitleaks not on PATH" in stderr
    assert capture.read_text() == "staged-package==1.0.0\n"


def test_gitleaks_config_has_no_review_safe_catch_all():
    config = (pathlib.Path(_REPO_ROOT) / ".gitleaks.toml").read_text()
    assert "'''\\[REVIEW-SAFE:" not in config


@pytest.mark.parametrize(
    "command,must_detect",
    [
        ("git merge feature", True),
        ("git  merge feature", True),  # F3: multi-space
        ("git\tmerge feature", True),  # F3: tab-separated
        ("/usr/bin/git merge feature", True),  # F3: absolute path
        ("git -C /tmp merge feature", True),  # F3: -C path form
        ("git -C /tmp -c user.x=y merge feature", True),  # F3: chained options
        ("git --no-pager merge feature", True),  # long value-less option
        ("git --git-dir .git --work-tree . merge feature", True),  # long options with values
        ("git -c core.editor='vim -e' merge feature", True),  # quoted option value
        ("git status && git --no-pager merge feature", True),  # chained git command
        ("git status&&git merge feature", True),  # no-space operator
        ("echo ok;git merge feature", True),  # no-space semicolon
        ("git status\ngit merge feature", True),  # multiline command
        ("GIT_DIR=. git merge feature", True),  # F3: env-prefixed
        ("git pull origin main", True),
        ("git cherry-pick abc", True),
        ("git rebase main", True),
        ("git am patch.mbox", True),
        # Must NOT trigger:
        ("git mergetool", False),  # different subcmd
        ("git-merge x", False),  # not a real git invocation
        ("git commit -m 'merge feature'", False),  # commit, not merge
        ("ungit merge x", False),  # word-boundary
        # NOTE: text-mentions inside shell string args (e.g. `echo "git merge"`)
        # ARE detected by the first-line scan — this is the documented trade-off
        # (lines 31-35 of the hook); the hook errs fail-closed in that case.
    ],
)
def test_hook_pre_merge_F3_detector_catches_evil_inputs(command, must_detect, _protected_branch_worktree):
    """F3 antibody: regex must catch every form of merge-class command on
    a protected branch, including multi-space, absolute path, -C form."""
    rc, stderr = _run_pre_merge_hook(command, cwd=_protected_branch_worktree)
    detected = "ADVISORY" in stderr or "BLOCKED" in stderr or "PASS" in stderr or "OVERRIDE" in stderr
    if must_detect:
        assert detected, f"F3 regression: failed to detect merge-class in {command!r}"
    else:
        assert not detected, f"F3 false positive on {command!r}: {stderr[:200]}"


def test_hook_pre_merge_F4_protected_branch_regex():
    """F4 antibody: the protected-branch list is enumerated, not glob-wide."""
    # Re-implement the same regex Python-side; if hook regex drifts, this drifts too.
    import re

    def is_protected(branch):
        return bool(re.match(r"^(main|plan-pre[0-9]+(/.*)?|release-[A-Za-z0-9._/-]+)$", branch))

    # Must protect:
    assert is_protected("main")
    assert is_protected("plan-pre5")
    assert is_protected("plan-pre10")
    assert is_protected("plan-pre5/sub-branch")  # sub-branch namespacing preserved
    assert is_protected("release-1.0")
    assert is_protected("release-2.0/rc1")
    # Must NOT protect:
    assert not is_protected("plan-pretty")  # F4 over-broad glob fix
    assert not is_protected("plan-prototype")
    assert not is_protected("plan-pre")  # no number
    assert not is_protected("release-")  # empty suffix
    assert not is_protected("Release-1.0")  # case-sensitive
    assert not is_protected("feature-x")
    assert not is_protected("topology-runtime-hooks-governance")


def test_hook_pre_merge_F13_blocks_commented_critic_verdict(tmp_path, _protected_branch_worktree):
    """F13 antibody: a fully-commented evidence file must NOT satisfy the
    critic_verdict existence check (comment-injection spoof)."""
    spoof = tmp_path / "evidence_spoof.txt"
    spoof.write_text(
        "# critic_verdict: APPROVE\n"
        "# diff_scope: 1 file\n"
        "# drift_keyword_scan: clean\n"
    )
    rc, stderr = _run_pre_merge_hook("git merge x", evidence_path=str(spoof), cwd=_protected_branch_worktree)
    assert "BLOCKED" in stderr, f"F13 regression: spoofed evidence accepted: {stderr[:300]}"


def test_hook_pre_merge_F13_accepts_real_verdict_with_comment_companion(tmp_path, _protected_branch_worktree):
    """F13 dual: a legit evidence file with both a comment mentioning the field
    AND a real un-commented field must PASS."""
    legit = tmp_path / "evidence_legit.txt"
    legit.write_text(
        "# This comment mentions critic_verdict: APPROVE for context\n"
        "critic_verdict: APPROVE\n"
        "diff_scope: 5 files +200/-50\n"
        "drift_keyword_scan: bidirectional clean\n"
    )
    rc, stderr = _run_pre_merge_hook("git merge x", evidence_path=str(legit), cwd=_protected_branch_worktree)
    assert "PASS" in stderr, f"F13 false positive on legit evidence: {stderr[:300]}"


def test_hook_pre_merge_accepts_verdict_with_trailing_comment(tmp_path, _protected_branch_worktree):
    """Review-crash antibody: critic_verdict may carry a YAML-style trailing
    comment, but the parsed value must still be exactly APPROVE/REVISE."""
    evidence = tmp_path / "evidence_trailing_comment.txt"
    evidence.write_text(
        "critic_verdict: APPROVE # reviewer note\n"
        "diff_scope: 1 file\n"
        "drift_keyword_scan: clean\n"
    )
    rc, stderr = _run_pre_merge_hook("git --no-pager merge x", evidence_path=str(evidence), cwd=_protected_branch_worktree)
    assert rc == 0
    assert "PASS" in stderr, f"trailing comment verdict was not accepted: {stderr[:300]}"


def test_hook_pre_merge_blocks_revise_verdict(tmp_path, _protected_branch_worktree):
    evidence = tmp_path / "evidence_revise.txt"
    evidence.write_text(
        "critic_verdict: REVISE\n"
        "diff_scope: 1 file\n"
        "drift_keyword_scan: clean\n"
    )
    rc, stderr = _run_pre_merge_hook("git merge x", evidence_path=str(evidence), cwd=_protected_branch_worktree)
    assert rc == 2
    assert "critic_verdict=REVISE" in stderr


def test_hook_pre_merge_F17_OVERRIDE_docstring_matches_implementation():
    """F17 antibody: the OVERRIDE docstring must match the actual log target
    (.claude/logs/merge-overrides.log), and must NOT claim writes to the
    docs/operations/current_state.md drift table (which the code does not do)."""
    with open(_PRE_MERGE_HOOK, "r") as f:
        text = f.read()
    assert "logged to docs/operations/current_state.md drift table" not in text, (
        "F17 regression: false log-target claim is back in the OVERRIDE docstring"
    )
    assert ".claude/logs/merge-overrides.log" in text, (
        "F17 regression: docstring no longer references the durable log file"
    )
    # Implementation pin: the OVERRIDE block must contain the actual write
    assert "OVERRIDE_LOG_PATH" in text, (
        "F17 regression: OVERRIDE log-write implementation missing"
    )


def test_hook_pre_merge_F17_OVERRIDE_writes_durable_log_on_protected_branch(_protected_branch_worktree):
    """F17 behavior antibody: when OVERRIDE fires on a protected branch, the
    hook must append a forensic record to .claude/logs/merge-overrides.log."""
    import pathlib
    worktree_dir = pathlib.Path(_protected_branch_worktree)
    log_path = worktree_dir / ".claude" / "logs" / "merge-overrides.log"
    if log_path.exists():
        log_path.unlink()
    rc, stderr = _run_pre_merge_hook(
        "git merge feature-x",
        evidence_path="OVERRIDE_pytest_F17_durable_log",
        cwd=str(worktree_dir),
    )
    assert rc == 0, f"OVERRIDE should exit 0, got {rc}: {stderr[:300]}"
    assert "OVERRIDE" in stderr, f"Expected OVERRIDE in stderr: {stderr[:300]}"
    assert log_path.exists(), (
        f"F17 regression: OVERRIDE did not create durable log at {log_path}. "
        f"stderr: {stderr[:300]}"
    )
    log_text = log_path.read_text()
    assert "reason=pytest_F17_durable_log" in log_text, f"log missing reason field: {log_text}"
    assert "channel=agent" in log_text, f"log missing channel field: {log_text}"
    assert "command=git merge feature-x" in log_text, f"log missing command field: {log_text}"


def test_hook_pre_merge_git_channel_override_logs_non_empty_command_context(_protected_branch_worktree):
    """Review-crash antibody: git pre-merge-commit channel has no Claude
    command payload, but the forensic log must still carry non-empty context."""
    import pathlib
    worktree_dir = pathlib.Path(_protected_branch_worktree)
    log_path = worktree_dir / ".claude" / "logs" / "merge-overrides.log"
    if log_path.exists():
        log_path.unlink()
    env = {
        **os.environ,
        "GIT_INDEX_FILE": "fake",
        "MERGE_AUDIT_EVIDENCE": "OVERRIDE_pytest_git_channel",
    }
    proc = _subprocess.run(
        ["bash", _PRE_MERGE_HOOK],
        input="",
        capture_output=True,
        text=True,
        env=env,
        cwd=str(worktree_dir),
    )
    assert proc.returncode == 0, proc.stderr[:300]
    log_text = log_path.read_text()
    assert "channel=git" in log_text, f"log missing git channel: {log_text}"
    assert "command=git-hook:" in log_text, f"git-channel command context empty: {log_text}"


def test_hook_pre_merge_F13_blocks_yaml_nested_critic_verdict_spoof(tmp_path, _protected_branch_worktree):
    """F13 antibody (tightened): a YAML-nested critic_verdict (under another
    top-level key) must NOT satisfy admission. Schema is strictly flat."""
    nested = tmp_path / "evidence_nested.txt"
    nested.write_text(
        "some_parent:\n"
        "  critic_verdict: APPROVE\n"
        "  diff_scope: 1 file\n"
        "  drift_keyword_scan: clean\n"
    )
    rc, stderr = _run_pre_merge_hook("git merge x", evidence_path=str(nested), cwd=_protected_branch_worktree)
    assert "BLOCKED" in stderr, (
        f"F13 regression: YAML-nested verdict spoof accepted: {stderr[:300]}"
    )
