"""Tests for topology_doctor compiled topology gates."""
# Lifecycle: created=2026-04-13; last_reviewed=2026-04-21; last_reused=2026-04-21
# Purpose: Regression tests for topology_doctor lanes, CLI parity, and closeout compilation.
# Reuse: Use targeted -k selectors for the lane being changed; inspect current manifest law first.

import pytest
import json
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


def test_topology_docs_mode_passes_with_active_data_package_excluded():
    result = topology_doctor.run_docs()

    assert_topology_ok(result)


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


def test_cli_json_parity_for_context_pack_command():
    args = [
        "context-pack",
        "--pack-type",
        "debug",
        "--task",
        "debug settlement rounding mismatch",
        "--files",
        "src/contracts/settlement_semantics.py",
        "--json",
    ]

    payload = run_cli_json(args)

    assert payload == topology_doctor.build_context_pack(
        "debug",
        task="debug settlement rounding mismatch",
        files=["src/contracts/settlement_semantics.py"],
    )


def test_cli_json_parity_for_semantic_bootstrap_command():
    args = [
        "semantic-bootstrap",
        "--task-class",
        "source_routing",
        "--task",
        "audit Hong Kong source routing",
        "--files",
        "src/data/tier_resolver.py",
        "--json",
    ]

    payload = run_cli_json(args)

    assert payload == topology_doctor.build_semantic_bootstrap(
        "source_routing",
        task="audit Hong Kong source routing",
        files=["src/data/tier_resolver.py"],
    )


def test_cli_json_parity_for_current_state_candidate_command():
    receipt = "docs/operations/task_2026-04-23_guidance_kernel_semantic_boot/receipt.json"
    payload = run_cli_json(["current-state", "--from-receipt", receipt, "--json"])

    assert payload == topology_doctor.build_current_state_candidate(receipt)


def test_cli_json_parity_for_impact_command():
    args = [
        "impact",
        "--files",
        "src/contracts/settlement_semantics.py",
        "src/calibration/platt.py",
        "--json",
    ]

    payload = run_cli_json(args)

    assert payload == topology_doctor.build_impact(
        ["src/contracts/settlement_semantics.py", "src/calibration/platt.py"],
    )


def test_cli_json_parity_for_core_map_command():
    payload = run_cli_json(["core-map", "--profile", "probability-chain", "--json"])

    assert payload == topology_doctor.build_core_map("probability-chain")


def test_cli_json_parity_for_compiled_topology_shape():
    payload = run_cli_json(["compiled-topology", "--json"])

    assert payload["authority_status"] == "derived_not_authority"
    assert payload["freshness_status"] == "ok"
    assert {
        "generated_at",
        "source_manifests",
        "docs_subroots",
        "reviewer_visible_routes",
        "local_only_routes",
        "active_operations_surfaces",
        "artifact_roles",
        "broken_visible_routes",
        "unclassified_docs_artifacts",
        "telemetry",
    }.issubset(payload)


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


def test_topology_source_mode_covers_all_tracked_src_files():
    result = topology_doctor.run_source()

    assert_topology_ok(result)


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


def test_topology_scripts_mode_covers_all_top_level_scripts():
    result = topology_doctor.run_scripts()

    assert_topology_ok(result)


def test_topology_data_rebuild_mode_encodes_certification_blockers():
    result = topology_doctor.run_data_rebuild()

    assert_topology_ok(result)


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


def test_topology_reference_replacement_mode_tracks_reference_docs():
    result = topology_doctor.run_reference_replacement()

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

    payload = topology_doctor.run_navigation("source task", ["src/engine/replay.py"], issue_schema_version="2")

    assert payload["ok"] is True
    assert payload["direct_blockers"] == []
    assert payload["repo_health_warnings"][0]["blocking_modes"] == ["global_health"]


def test_issue_schema_drift_guard():
    schema = topology_doctor.load_schema()["issue_json_contract"]
    expected = set(schema["legacy_fields"]) | set(schema["typed_fields"])

    assert set(topology_doctor.topology_issue_field_names()) == expected
    assert set(schema["enums"]["repair_kind"]) == topology_doctor.ISSUE_REPAIR_KINDS
    assert set(schema["enums"]["maturity"]) == topology_doctor.ISSUE_MATURITY_VALUES
    assert set(schema["enums"]["authority_status"]) == topology_doctor.ISSUE_AUTHORITY_STATUSES
    assert tuple(schema["enums"]["blocking_modes"]) == topology_doctor.ISSUE_BLOCKING_MODES


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
    fact_types = topology_doctor._ownership_checks().ownership_fact_types(topology_doctor.load_schema())

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
    fact_types = topology_doctor._ownership_checks().ownership_fact_types(topology_doctor.load_schema())

    assert fact_types["doc_classification"]["canonical_owner"] == "architecture/docs_registry.yaml"
    assert "architecture/module_manifest.yaml" not in fact_types["doc_classification"].get("derived_owners", [])


def test_module_routing_owned_only_by_module_manifest():
    fact_types = topology_doctor._ownership_checks().ownership_fact_types(topology_doctor.load_schema())

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


def test_context_pack_includes_graph_appendix_for_code_review_profile():
    payload = topology_doctor.build_context_pack(
        "package_review",
        task="package review graph appendix",
        files=["scripts/topology_doctor.py"],
    )

    assert payload["graph_appendix"]["authority_status"] == "derived_not_authority"


def test_context_pack_handles_missing_graph_db_gracefully(monkeypatch):
    payload = {
        "usable": False,
        "reason": "graph DB missing",
        "graph_health": {
            "issues": [
                {
                    "code": "code_review_graph_missing",
                    "path": ".code-review-graph/graph.db",
                    "message": "missing",
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

    pack = topology_doctor.build_context_pack(
        "debug",
        task="debug missing graph",
        files=["scripts/topology_doctor.py"],
    )

    assert pack["graph_appendix"]["graph_freshness"] == "missing"
    assert pack["graph_appendix"]["authority_status"] == "derived_not_authority"


def test_navigation_aggregates_default_health_and_digest():
    payload = topology_doctor.run_navigation(
        "fix settlement rounding in replay",
        ["src/engine/replay.py"],
    )

    assert_navigation_ok(payload)
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

    payload = topology_doctor.run_navigation("source task", ["src/engine/replay.py"])

    assert payload["ok"] is True
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

    payload = topology_doctor.run_navigation("source task", ["src/engine/replay.py"])

    assert payload["ok"] is True
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


def test_reference_artifact_digest_routes_to_reference_profile():
    digest = topology_doctor.build_digest("reference artifact claim extraction for zeus_math_spec fact spec")

    assert digest["profile"] == "reference artifact extraction"
    assert "architecture/reference_replacement.yaml" in digest["allowed_files"]
    assert any("Claim proofs point" in law for law in digest["required_law"])
    assert "python scripts/topology_doctor.py --reference-replacement" in digest["gates"]


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
    manifest["scripts"]["analyze_paper_trading.py"] = {
        **manifest["scripts"]["analyze_paper_trading.py"],
        "status": "deprecated",
        "lifecycle": "long_lived",
        "canonical_command": "python scripts/analyze_paper_trading.py",
    }

    monkeypatch.setattr(topology_doctor, "load_script_manifest", lambda: manifest)
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


def test_script_digest_routes_agents_to_lifecycle_law():
    digest = topology_doctor.build_digest("add a replay diagnostic script")
    script_lifecycle = digest["script_lifecycle"]

    assert digest["profile"] == "add or change script"
    assert "packet_ephemeral" in script_lifecycle["allowed_lifecycles"]
    assert "audit_" in script_lifecycle["long_lived_naming"]["allowed_prefixes"]
    assert "audit_replay_fidelity.py" in script_lifecycle["existing_scripts"]
    assert "python scripts/topology_doctor.py --scripts" in digest["gates"]
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


def test_lore_digest_routes_history_tasks_to_density_policy():
    digest = topology_doctor.build_digest("extract lore from historical work packets")
    lore_ids = {card["id"] for card in digest["history_lore"]}

    assert digest["profile"] == "extract historical lore"
    assert "HISTORICAL_LORE_DENSITY_POLICY" in lore_ids
    assert any("not default reading material" in law for law in digest["required_law"])
    assert "python scripts/topology_doctor.py --history-lore" in digest["gates"]


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
                "gates": ["python scripts/topology_doctor.py --history-lore"],
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


def test_closeout_compiles_selected_lanes(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {
            "scripts/topology_doctor.py": "modified",
            "docs/README.md": "modified",
            "architecture/context_budget.yaml": "modified",
        },
    )
    monkeypatch.setattr(topology_doctor, "run_planning_lock", lambda files, evidence=None: ok)
    monkeypatch.setattr(topology_doctor, "run_work_record", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_change_receipts", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_map_maintenance", lambda files, mode="closeout": ok)
    monkeypatch.setattr(topology_doctor, "run_artifact_lifecycle", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_naming_conventions", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_freshness_metadata", lambda files: ok)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: ok)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_source", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_tests", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_scripts", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_data_rebuild", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_context_budget", lambda: ok)
    monkeypatch.setattr(
        topology_doctor,
        "build_compiled_topology",
        lambda: {
            "telemetry": {
                "dark_write_target_count": 0,
                "broken_visible_route_count": 0,
                "unclassified_docs_artifact_count": 0,
            }
        },
    )

    payload = topology_doctor.run_closeout(
        changed_files=[
            "scripts/topology_doctor.py",
            "docs/README.md",
            "architecture/context_budget.yaml",
        ],
        plan_evidence="docs/operations/task_2026-04-15_topology_enforcement_hardening/plan.md",
        work_record_path="docs/operations/task_2026-04-15_topology_enforcement_hardening/work_log.md",
        receipt_path="docs/operations/task_2026-04-15_topology_enforcement_hardening/receipt.json",
    )

    assert payload["ok"] is True
    assert payload["selected_lanes"]["docs"] is True
    assert payload["selected_lanes"]["scripts"] is True
    assert payload["selected_lanes"]["context_budget"] is True
    assert "change_receipts" in payload["lanes"]
    assert payload["telemetry"]["dark_write_target_count"] == 0


def test_closeout_filters_repo_global_lane_noise(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])
    docs_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="docs_unregistered_subtree",
                path="docs/other_surface",
                message="unrelated docs issue",
            )
        ],
    )
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"docs/README.md": "modified"},
    )
    monkeypatch.setattr(topology_doctor, "run_planning_lock", lambda files, evidence=None: ok)
    monkeypatch.setattr(topology_doctor, "run_work_record", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_change_receipts", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_map_maintenance", lambda files, mode="closeout": ok)
    monkeypatch.setattr(topology_doctor, "run_artifact_lifecycle", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_naming_conventions", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_freshness_metadata", lambda files: ok)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: ok)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: docs_result)
    monkeypatch.setattr(topology_doctor, "run_context_budget", lambda: ok)
    monkeypatch.setattr(
        topology_doctor,
        "build_compiled_topology",
        lambda: {"telemetry": {"dark_write_target_count": 0}},
    )

    payload = topology_doctor.run_closeout(changed_files=["docs/README.md"])

    assert payload["ok"] is True
    assert payload["lanes"]["docs"]["issue_count"] == 0
    assert payload["global_health"]["docs"]["issue_count"] == 1
    assert "source" in payload["global_health"]
    assert "tests" in payload["global_health"]
    assert "scripts" in payload["global_health"]


def test_closeout_changed_source_missing_rationale_still_blocks(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])
    source_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="source_rationale_missing",
                path="src/contracts/tick_size.py",
                message="changed source missing rationale",
            )
        ],
    )
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"src/contracts/tick_size.py": "modified"},
    )
    monkeypatch.setattr(topology_doctor, "run_planning_lock", lambda files, evidence=None: ok)
    monkeypatch.setattr(topology_doctor, "run_work_record", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_change_receipts", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_map_maintenance", lambda files, mode="closeout": ok)
    monkeypatch.setattr(topology_doctor, "run_artifact_lifecycle", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_naming_conventions", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_freshness_metadata", lambda files: ok)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: ok)
    monkeypatch.setattr(topology_doctor, "run_source", lambda: source_result)
    monkeypatch.setattr(
        topology_doctor,
        "build_compiled_topology",
        lambda: {"telemetry": {"dark_write_target_count": 0}},
    )

    payload = topology_doctor.run_closeout(changed_files=["src/contracts/tick_size.py"])

    assert payload["ok"] is False
    assert payload["blocking_issues"][0]["lane"] == "source"
    assert payload["blocking_issues"][0]["code"] == "source_rationale_missing"


def test_closeout_synthetic_global_issue_stays_in_global_health(monkeypatch):
    ok = topology_doctor.StrictResult(ok=True, issues=[])
    docs_result = topology_doctor.StrictResult(
        ok=False,
        issues=[
            topology_doctor.TopologyIssue(
                code="docs_global_issue",
                path="<docs-global>",
                message="synthetic global docs issue",
            )
        ],
    )
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {"docs/README.md": "modified"},
    )
    monkeypatch.setattr(topology_doctor, "run_planning_lock", lambda files, evidence=None: ok)
    monkeypatch.setattr(topology_doctor, "run_work_record", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_change_receipts", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_map_maintenance", lambda files, mode="closeout": ok)
    monkeypatch.setattr(topology_doctor, "run_artifact_lifecycle", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_naming_conventions", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_freshness_metadata", lambda files: ok)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: ok)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: docs_result)
    monkeypatch.setattr(topology_doctor, "run_context_budget", lambda: ok)
    monkeypatch.setattr(
        topology_doctor,
        "build_compiled_topology",
        lambda: {"telemetry": {"dark_write_target_count": 0}},
    )

    payload = topology_doctor.run_closeout(changed_files=["docs/README.md"])

    assert payload["ok"] is True
    assert payload["lanes"]["docs"]["issue_count"] == 0
    assert payload["global_health"]["docs"]["blocking_count"] == 1


def test_closeout_prefers_staged_files_when_changed_files_omitted(monkeypatch):
    from scripts import topology_doctor_closeout

    ok = topology_doctor.StrictResult(ok=True, issues=[])
    monkeypatch.setattr(
        topology_doctor_closeout,
        "staged_changed_files",
        lambda api: ["docs/README.md"],
    )
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {path: "modified" for path in files},
    )
    monkeypatch.setattr(topology_doctor, "run_planning_lock", lambda files, evidence=None: ok)
    monkeypatch.setattr(topology_doctor, "run_work_record", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_change_receipts", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_map_maintenance", lambda files, mode="closeout": ok)
    monkeypatch.setattr(topology_doctor, "run_artifact_lifecycle", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_naming_conventions", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_freshness_metadata", lambda files: ok)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: ok)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_context_budget", lambda: ok)
    monkeypatch.setattr(
        topology_doctor,
        "build_compiled_topology",
        lambda: {"telemetry": {"dark_write_target_count": 0}},
    )

    payload = topology_doctor.run_closeout()

    assert payload["changed_files"] == ["docs/README.md"]


def test_closeout_falls_back_to_receipt_when_clean(monkeypatch, tmp_path):
    from scripts import topology_doctor_closeout

    ok = topology_doctor.StrictResult(ok=True, issues=[])
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"changed_files": ["docs/README.md"]}), encoding="utf-8")
    monkeypatch.setattr(topology_doctor_closeout, "staged_changed_files", lambda api: [])
    monkeypatch.setattr(
        topology_doctor,
        "_map_maintenance_changes",
        lambda files: {} if not files else {path: "modified" for path in files},
    )
    monkeypatch.setattr(topology_doctor, "run_planning_lock", lambda files, evidence=None: ok)
    monkeypatch.setattr(topology_doctor, "run_work_record", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_change_receipts", lambda files, path=None: ok)
    monkeypatch.setattr(topology_doctor, "run_map_maintenance", lambda files, mode="closeout": ok)
    monkeypatch.setattr(topology_doctor, "run_artifact_lifecycle", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_naming_conventions", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_freshness_metadata", lambda files: ok)
    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", lambda files=None: ok)
    monkeypatch.setattr(topology_doctor, "run_docs", lambda: ok)
    monkeypatch.setattr(topology_doctor, "run_context_budget", lambda: ok)
    monkeypatch.setattr(
        topology_doctor,
        "build_compiled_topology",
        lambda: {"telemetry": {"dark_write_target_count": 0}},
    )

    payload = topology_doctor.run_closeout(receipt_path=str(receipt))

    assert payload["changed_files"] == ["docs/README.md"]


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


def test_invariants_slice_filters_by_zone():
    payload = topology_doctor.build_invariants_slice("K3_extension")
    ids = {invariant["id"] for invariant in payload["invariants"]}

    assert payload["zone"] == "K3_extension"
    assert "INV-04" in ids
    assert "INV-06" in ids
    assert "INV-07" not in ids


def test_refactor_packet_prefill_for_engine_scope():
    packet = topology_doctor.build_packet_prefill(
        packet_type="refactor",
        task="split cycle runner orchestration helpers",
        scope="src/engine/",
    )

    assert packet["packet_type"] == "refactor_packet"
    assert packet["scope"] == "src/engine"
    assert packet["zones_touched"] == ["K2_runtime"]
    assert "INV-06" in packet["invariants_touched"]
    assert "src/engine/**" in packet["files_may_change"]
    assert "src/contracts" in packet["files_may_not_change"]
    assert "src/engine/AGENTS.md" in packet["required_reads"]
    assert "architecture/source_rationale.yaml" in packet["required_reads"]
    assert any("semantic_linter.py --check src/engine" in gate for gate in packet["ci_gates_required"])
    assert packet["context_assumption"]["sufficiency"] == "provisional_starting_packet"


def test_refactor_packet_prefill_detects_cross_zone_files():
    packet = topology_doctor.build_packet_prefill(
        packet_type="refactor",
        task="extract evaluator strategy helper",
        files=["src/engine/cycle_runner.py", "src/strategy/market_analysis.py"],
    )

    assert "K2_runtime" in packet["zones_touched"]
    assert "K3_extension" in packet["zones_touched"]
    assert packet["replay_required"] is True
    assert any("tests/test_market_analysis.py" in test for test in packet["tests_required"])


def test_refactor_packet_prefill_keeps_file_scope_literal():
    packet = topology_doctor.build_packet_prefill(
        packet_type="refactor",
        task="refactor platt calibration",
        scope="src/calibration/platt.py",
    )

    assert packet["files_may_change"] == ["src/calibration/platt.py"]
    assert "src/calibration/platt.py/**" not in packet["files_may_change"]


def test_impact_reports_write_routes_and_tests_for_store():
    impact = topology_doctor.build_impact(["src/calibration/store.py"])
    entry = impact["entries"][0]

    assert entry["path"] == "src/calibration/store.py"
    assert entry["zone"] == "K3_extension"
    assert "calibration_persistence_write" in entry["write_routes"]
    assert "tests/test_platt.py" in impact["aggregate"]["tests_required"]
    assert "python scripts/semantic_linter.py --check src/calibration/store.py" in impact["aggregate"]["static_checks"]
    assert impact["context_assumption"]["planning_lock_independent"] is True


def test_impact_marks_missing_relations_provisional_for_platt():
    impact = topology_doctor.build_impact(["src/calibration/platt.py"])
    entry = impact["entries"][0]

    assert entry["confidence"] == "provisional"
    assert entry["relations_complete"] is False
    assert "missing_relations" in impact["context_assumption"]["confidence_basis"]


def test_context_pack_profiles_mode_validates_manifest():
    result = topology_doctor.run_context_packs()

    assert_topology_ok(result)


def test_module_book_and_manifest_lanes_pass():
    books = topology_doctor.run_module_books()
    manifest = topology_doctor.run_module_manifest()

    assert books.ok is True
    assert manifest.ok is True
    assert all(issue.severity == "warning" for issue in books.issues)
    assert all(issue.severity == "warning" for issue in manifest.issues)


def test_cli_json_parity_for_module_book_lane():
    payload = run_cli_json(["--module-books", "--json"])
    result = topology_doctor.run_module_books()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_cli_json_parity_for_module_manifest_lane():
    payload = run_cli_json(["--module-manifest", "--json"])
    result = topology_doctor.run_module_manifest()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_package_review_context_pack_shapes_k1_style_review():
    files = [
        "src/contracts/execution_price.py",
        "src/types/observation_atom.py",
        "src/contracts/provenance_registry.py",
        "src/supervisor_api/contracts.py",
        "src/config.py",
        "src/engine/evaluator.py",
        "src/strategy/kelly.py",
        "src/strategy/market_fusion.py",
        "src/data/market_scanner.py",
        "src/state/portfolio.py",
        "src/riskguard/policy.py",
    ]

    packet = topology_doctor.build_context_pack(
        "auto",
        task="K1 package-level review for contract/provenance/settings consistency",
        files=files,
    )

    assert packet["pack_type"] == "package_review"
    assert packet["authority_status"] == "generated_review_packet_not_authority"
    assert packet["selected_by"] == {"requested": "auto", "selected": "package_review"}
    assert set(packet["zones_touched"]) >= {"K0_frozen_kernel", "K1_governance", "K2_runtime", "K3_extension"}
    assert "files_may_change" not in packet
    assert packet["changed_files"] == sorted(files)
    assert packet["route_health"]["ok"] is True
    assert packet["context_assumption"]["sufficiency"] == "provisional_starting_packet"
    assert "context_pack_profile" in packet["context_assumption"]["confidence_basis"]
    assert any("coherent contract system" in question for question in packet["cross_slice_questions"])
    assert any(surface["path"] == "src/strategy/kelly.py" for surface in packet["contract_surfaces"])
    assert any(claim["claim_id"] == "EXECUTION_PRICE_NOT_IMPLIED_PROBABILITY" for claim in packet["proof_claims_touched"])
    assert any(item["module"] == "contracts" for item in packet["module_context"])
    assert any(gap["kind"] == "provisional_relation_gap" for gap in packet["coverage_gaps"])
    assert any(risk["kind"] == "cross_zone_contract_review" for risk in packet["downstream_risks"])
    assert "python scripts/semantic_linter.py --check " + " ".join(sorted(files)) in packet["static_checks"]


def test_package_review_separates_route_health_from_repo_health(monkeypatch):
    monkeypatch.setattr(
        topology_doctor,
        "run_core_claims",
        lambda: topology_doctor.StrictResult(
            ok=False,
            issues=[
                topology_doctor.TopologyIssue(
                    code="core_claim_gate_target_missing",
                    path="architecture/core_claims.yaml:FAKE",
                    message="synthetic repo-health issue",
                )
            ],
        ),
    )

    packet = topology_doctor.build_context_pack(
        "package_review",
        task="package review",
        files=["src/engine/evaluator.py"],
    )

    assert packet["route_health"]["ok"] is True
    assert packet["repo_health"]["ok"] is False
    assert packet["repo_health"]["checks"]["core_claims"]["blocking_count"] == 1
    assert packet["blocking_for_this_pack"] == []


def test_package_review_lore_keeps_broad_matches_summary_only(monkeypatch):
    lore = {
        "cards": [
            {
                "id": "DIRECT_CARD",
                "status": "active_law",
                "severity": "high",
                "failure_mode": "direct failure",
                "wrong_moves": ["full direct card may include wrong moves"],
                "correct_rule": "direct rule",
                "routing": {"task_terms": [], "file_patterns": ["src/engine/evaluator.py"]},
                "zero_context_digest": "direct digest",
            },
            {
                "id": "BROAD_CARD",
                "status": "active_law",
                "severity": "high",
                "failure_mode": "broad failure",
                "wrong_moves": ["broad wrong moves must not be dumped"],
                "correct_rule": "broad rule",
                "routing": {"task_terms": ["package review"], "file_patterns": []},
                "zero_context_digest": "broad digest",
            },
        ]
    }
    monkeypatch.setattr(topology_doctor, "load_history_lore", lambda: lore)

    packet = topology_doctor.build_context_pack(
        "package_review",
        task="package review",
        files=["src/engine/evaluator.py"],
    )

    assert packet["lore"]["direct_evidence"][0]["id"] == "DIRECT_CARD"
    assert packet["lore"]["direct_evidence"][0]["zero_context_digest"] == "direct digest"
    assert "wrong_moves" not in packet["lore"]["direct_evidence"][0]
    assert packet["lore"]["broad_relevant"][0]["id"] == "BROAD_CARD"
    assert packet["lore"]["broad_relevant"][0]["zero_context_digest"] == "broad digest"
    assert "wrong_moves" not in packet["lore"]["broad_relevant"][0]


def test_debug_context_pack_shapes_single_file_symptom():
    packet = topology_doctor.build_context_pack(
        "debug",
        task="debug settlement rounding mismatch in replay",
        files=["src/contracts/settlement_semantics.py"],
    )
    text = str(packet)

    assert packet["pack_type"] == "debug"
    assert packet["authority_status"] == "generated_debug_packet_not_authority"
    assert packet["symptom"] == "debug settlement rounding mismatch in replay"
    assert packet["target_files"] == ["src/contracts/settlement_semantics.py"]
    assert packet["route_health"]["ok"] is True
    assert any(item["module"] == "contracts" for item in packet["module_context"])
    assert packet["module_context"][0]["module_book"] == "docs/reference/modules/contracts.md"
    assert packet["context_assumption"]["sufficiency"] == "provisional_starting_packet"
    assert "context_pack_profile" in packet["context_assumption"]["confidence_basis"]
    assert any(surface["path"] == "src/contracts/settlement_semantics.py" for surface in packet["contract_surfaces"])
    assert any(claim["claim_id"] == "WMO_HALF_UP_FORMULA" for claim in packet["proof_claims_touched"])
    assert any(check["id"] == "semantic_linter_target" for check in packet["red_green_checks"])
    assert any(check["id"] == "targeted_tests" for check in packet["red_green_checks"])
    assert any(boundary["kind"] == "verified_claim_boundary" for boundary in packet["suspected_boundaries"])
    assert "files_may_change" not in packet
    assert "write_scope" not in packet
    assert "root_cause" not in packet
    assert "complete_understanding" not in text


def test_debug_context_pack_marks_provisional_boundaries():
    packet = topology_doctor.build_context_pack(
        "debug",
        task="debug platt calibration regression",
        files=["src/calibration/platt.py"],
    )

    assert any(gap["kind"] == "provisional_relation_gap" for gap in packet["coverage_gaps"])
    assert any(
        boundary["kind"] == "unknown_relation_boundary"
        and boundary["confidence"] == "provisional"
        for boundary in packet["suspected_boundaries"]
    )


def test_debug_context_pack_lore_is_tiered_summary_only(monkeypatch):
    lore = {
        "cards": [
            {
                "id": "DIRECT_DEBUG_CARD",
                "status": "active_law",
                "severity": "high",
                "failure_mode": "direct failure body must not be dumped",
                "wrong_moves": ["wrong move must not be dumped"],
                "correct_rule": "direct rule",
                "routing": {"task_terms": [], "file_patterns": ["src/engine/evaluator.py"]},
                "zero_context_digest": "direct debug digest",
            },
            {
                "id": "BROAD_DEBUG_CARD",
                "status": "active_law",
                "severity": "high",
                "failure_mode": "broad failure body must not be dumped",
                "wrong_moves": ["broad wrong move must not be dumped"],
                "correct_rule": "broad rule",
                "routing": {"task_terms": ["debug"], "file_patterns": []},
                "zero_context_digest": "broad debug digest",
            },
        ]
    }
    monkeypatch.setattr(topology_doctor, "load_history_lore", lambda: lore)

    packet = topology_doctor.build_context_pack(
        "debug",
        task="debug evaluator failure",
        files=["src/engine/evaluator.py"],
    )

    assert packet["lore"]["direct_evidence"][0]["id"] == "DIRECT_DEBUG_CARD"
    assert packet["lore"]["direct_evidence"][0]["zero_context_digest"] == "direct debug digest"
    assert "wrong_moves" not in packet["lore"]["direct_evidence"][0]
    assert "failure_mode" not in packet["lore"]["direct_evidence"][0]
    assert packet["lore"]["broad_relevant"][0]["id"] == "BROAD_DEBUG_CARD"
    assert packet["lore"]["broad_relevant"][0]["zero_context_digest"] == "broad debug digest"
    assert "wrong_moves" not in packet["lore"]["broad_relevant"][0]
    assert "failure_mode" not in packet["lore"]["broad_relevant"][0]


def test_context_pack_auto_selects_debug_without_stealing_package_review():
    debug_packet = topology_doctor.build_context_pack(
        "auto",
        task="debug market fusion regression",
        files=["src/strategy/market_fusion.py"],
    )
    review_packet = topology_doctor.build_context_pack(
        "auto",
        task="package-level review for contract consistency after debug fixes",
        files=["src/contracts/execution_price.py", "src/strategy/kelly.py"],
    )

    assert debug_packet["selected_by"] == {"requested": "auto", "selected": "debug"}
    assert review_packet["selected_by"] == {"requested": "auto", "selected": "package_review"}


def test_context_pack_auto_rejects_ambiguous_task():
    with pytest.raises(ValueError, match="package_review or debug"):
        topology_doctor.build_context_pack(
            "auto",
            task="inspect this area",
            files=["src/strategy/market_fusion.py"],
        )


def test_context_pack_includes_inferred_semantic_bootstrap():
    packet = topology_doctor.build_context_pack(
        "debug",
        task="debug settlement rounding mismatch in replay",
        files=["src/contracts/settlement_semantics.py"],
    )

    assert packet["semantic_bootstrap"]["task_class"] == "settlement_semantics"
    assert packet["semantic_bootstrap"]["authority_status"] == "generated_semantic_bootstrap_not_authority"
    assert any(
        item["id"] == "daily_day0_hourly_forecast_sources_are_not_interchangeable"
        for item in packet["semantic_bootstrap"]["fatal_misreads"]
    )


def test_semantic_bootstrap_source_routing_shape():
    payload = topology_doctor.build_semantic_bootstrap(
        "source_routing",
        task="audit Hong Kong source routing",
        files=["src/data/tier_resolver.py"],
    )

    assert payload["ok"] is True
    assert payload["authority_status"] == "generated_semantic_bootstrap_not_authority"
    assert "docs/operations/current_source_validity.md" in payload["required_reads"]
    assert any(item["path"] == "docs/operations/current_source_validity.md" for item in payload["current_fact_surfaces"])
    assert any(item["id"] == "api_returns_data_not_settlement_correct_source" for item in payload["fatal_misreads"])
    assert any(item["claim_id"] == "SOURCE_ROLES_NOT_INTERCHANGEABLE" for item in payload["current_core_claims"])
    assert payload["graph_usage"]["authority_status"] == "derived_not_authority"


def test_semantic_bootstrap_unknown_task_class_returns_issue():
    payload = topology_doctor.build_semantic_bootstrap("nope", task="unknown")

    assert payload["ok"] is False
    assert payload["issues"][0]["code"] == "semantic_bootstrap_unknown_task_class"


def test_semantic_bootstrap_missing_manifest_returns_issue(monkeypatch, tmp_path):
    monkeypatch.setattr(topology_doctor, "TASK_BOOT_PROFILES_PATH", tmp_path / "missing.yaml")

    payload = topology_doctor.build_semantic_bootstrap("source_routing")

    assert payload["ok"] is False
    assert payload["issues"][0]["code"] == "task_boot_profiles_manifest_missing"


def test_semantic_bootstrap_warns_missing_current_fact_surface(monkeypatch):
    manifest = topology_doctor.load_task_boot_profiles()
    profile = next(item for item in manifest["profiles"] if item["id"] == "source_routing")
    profile["current_fact_surfaces"] = ["docs/operations/not_a_real_current_fact.md"]
    monkeypatch.setattr(topology_doctor, "load_task_boot_profiles", lambda: manifest)

    payload = topology_doctor.build_semantic_bootstrap("source_routing")

    assert any(warning["code"] == "current_fact_surface_missing" for warning in payload["warnings"])


def test_semantic_bootstrap_warns_stale_current_fact_surface(monkeypatch, tmp_path):
    fact = tmp_path / "docs" / "operations" / "old_fact.md"
    fact.parent.mkdir(parents=True)
    fact.write_text("# Old Fact\n\nLast audited: 2000-01-01\n", encoding="utf-8")
    manifest = topology_doctor.load_task_boot_profiles()
    profile = next(item for item in manifest["profiles"] if item["id"] == "docs_authority")
    profile["current_fact_surfaces"] = ["docs/operations/old_fact.md"]
    profile["graph_usage"]["stage"] = "not_required"

    monkeypatch.setattr(topology_doctor, "ROOT", tmp_path)
    monkeypatch.setattr(topology_doctor, "load_task_boot_profiles", lambda: manifest)

    payload = topology_doctor.build_semantic_bootstrap("docs_authority")

    assert any(warning["code"] == "current_fact_surface_stale" for warning in payload["warnings"])


def test_semantic_bootstrap_warns_unavailable_graph(monkeypatch):
    def fake_graph_status(files):
        return topology_doctor.StrictResult(
            ok=False,
            issues=[
                topology_doctor.TopologyIssue(
                    code="code_review_graph_db_missing",
                    path=".code-review-graph/graph.db",
                    message="missing graph",
                )
            ],
        )

    monkeypatch.setattr(topology_doctor, "run_code_review_graph_status", fake_graph_status)

    payload = topology_doctor.build_semantic_bootstrap("source_routing", files=["src/data/tier_resolver.py"])

    assert payload["graph_usage"]["availability"] == "unavailable_or_stale"
    assert any(warning["code"] == "code_review_graph_unavailable_or_stale" for warning in payload["warnings"])


def test_core_map_probability_chain_is_proof_backed_and_bounded():
    payload = topology_doctor.build_core_map("probability-chain")
    text = str(payload)
    settlement = next(node for node in payload["nodes"] if node["id"] == "settlement_semantics")
    wmo_fact = settlement["facts"][0]

    assert payload["authority_status"] == "generated_view_not_authority"
    assert payload["context_assumption"]["sufficiency"] == "provisional_starting_packet"
    assert "core_map_profile" in payload["context_assumption"]["confidence_basis"]
    profile = next(item for item in topology_doctor.load_topology()["core_map_profiles"] if item["id"] == "probability-chain")
    assert len(payload["nodes"]) <= profile["max_nodes"]
    assert len(payload["edges"]) <= profile["max_edges"]
    assert wmo_fact["claim_id"] == "WMO_HALF_UP_FORMULA"
    assert wmo_fact["confidence"] == "verified_claim"
    assert "floor(x + 0.5)" in wmo_fact["text"]
    assert "round(value + 0.5)" not in text
    assert "Python round" not in text
    assert all(edge["confidence"] == "proof_backed_edge" for edge in payload["edges"])
    assert not payload["invalid"]


def test_compiled_topology_is_derived_read_model():
    payload = topology_doctor.build_compiled_topology()

    assert payload["authority_status"] == "derived_not_authority"
    assert payload["freshness_status"] == "ok"
    assert "generated_at" in payload
    assert any(item["path"] == "architecture/topology.yaml" for item in payload["source_manifests"])
    assert any(item["path"] == "docs/to-do-list" for item in payload["docs_subroots"])
    assert any(item["path"] == "docs/to-do-list" for item in payload["reviewer_visible_routes"])
    assert payload["local_only_routes"] == [
        {
            "path": "docs/archives",
            "role": "historical_archive",
            "route_status": "ignored_archive",
            "reviewer_visible": False,
        }
    ]
    assert "docs/operations/task_2026-04-23_authority_rehydration/plan.md" in payload["active_operations_surfaces"]["required_anchors"]
    assert any(item["path"] == "docs/reference/zeus_math_spec.md" for item in payload["artifact_roles"])
    assert payload["broken_visible_routes"] == []
    assert payload["unclassified_docs_artifacts"] == []


def test_core_claims_mode_validates_first_wave_claims():
    result = topology_doctor.run_core_claims()
    claims = topology_doctor.load_core_claims()["claims"]
    claim_ids = {claim["claim_id"] for claim in claims}

    assert claim_ids
    assert all(issue.code == "core_claim_source_missing" for issue in result.issues)
    assert {
        "TEMPERATURE_DELTA_SCALE_ONLY",
        "EXECUTION_PRICE_NOT_IMPLIED_PROBABILITY",
        "ALPHA_TARGET_COMPATIBILITY",
        "VIG_BEFORE_BLEND",
        "SOURCE_ROLES_NOT_INTERCHANGEABLE",
        "HONG_KONG_CURRENT_TRUTH_REQUIRES_FRESH_AUDIT",
        "HOURLY_EXTREMA_PRESERVATION_REQUIRED",
        "CODE_REVIEW_GRAPH_DERIVED_NOT_SEMANTIC_AUTHORITY",
    }.issubset(claim_ids)


def test_cli_json_parity_for_task_boot_profiles_mode():
    payload = run_cli_json(["--task-boot-profiles", "--json"])
    result = topology_doctor.run_task_boot_profiles()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_cli_json_parity_for_fatal_misreads_mode():
    payload = run_cli_json(["--fatal-misreads", "--json"])
    result = topology_doctor.run_fatal_misreads()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_cli_json_parity_for_city_truth_contract_mode():
    payload = run_cli_json(["--city-truth-contract", "--json"])
    result = topology_doctor.run_city_truth_contract()

    assert payload == {
        "ok": result.ok,
        "issues": [topology_doctor.asdict(issue) for issue in result.issues],
    }


def test_task_boot_profiles_mode_validates_semantic_profiles():
    result = topology_doctor.run_task_boot_profiles()
    manifest = topology_doctor.load_task_boot_profiles()
    profile_ids = {profile["id"] for profile in manifest["profiles"]}
    source_profile = next(profile for profile in manifest["profiles"] if profile["id"] == "source_routing")

    assert_topology_ok(result)
    assert set(manifest["required_task_classes"]) == profile_ids
    assert "docs/operations/current_source_validity.md" in source_profile["current_fact_surfaces"]
    assert "api_returns_data_not_settlement_correct_source" in source_profile["fatal_misreads"]
    assert source_profile["graph_usage"]["authority_status"] == "derived_not_authority"


def test_fatal_misreads_mode_validates_semantic_antibodies():
    result = topology_doctor.run_fatal_misreads()
    manifest = topology_doctor.load_fatal_misreads()
    misread_ids = {item["id"] for item in manifest["misreads"]}

    assert_topology_ok(result)
    assert {
        "api_returns_data_not_settlement_correct_source",
        "daily_day0_hourly_forecast_sources_are_not_interchangeable",
        "hong_kong_hko_explicit_caution_path",
        "code_review_graph_answers_where_not_what_settles",
    }.issubset(misread_ids)


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


def test_core_map_probability_chain_uses_core_claims():
    payload = topology_doctor.build_core_map("probability-chain")
    node_claims = {
        node["id"]: {fact["claim_id"] for fact in node["facts"]}
        for node in payload["nodes"]
    }

    assert "ALPHA_TARGET_COMPATIBILITY" in node_claims["evaluator"]
    assert "VIG_BEFORE_BLEND" in node_claims["market_fusion"]
    assert "TEMPERATURE_DELTA_SCALE_ONLY" in node_claims["market_fusion"]
    assert "EXECUTION_PRICE_NOT_IMPLIED_PROBABILITY" in node_claims["kelly"]


def test_core_claims_mode_rejects_missing_proof_target(monkeypatch):
    manifest = topology_doctor.load_core_claims()
    manifest["claims"][0]["proof_targets"][0]["path"] = "src/nope.py"

    monkeypatch.setattr(topology_doctor, "load_core_claims", lambda: manifest)
    result = topology_doctor.run_core_claims()

    assert not result.ok
    assert any(issue.code == "core_claim_proof_target_missing" for issue in result.issues)


def test_core_claims_mode_rejects_missing_locator(monkeypatch):
    manifest = topology_doctor.load_core_claims()
    manifest["claims"][0]["proof_targets"][0]["locator"] = "NO_SUCH_LOCATOR"

    monkeypatch.setattr(topology_doctor, "load_core_claims", lambda: manifest)
    result = topology_doctor.run_core_claims()

    assert not result.ok
    assert any("locator missing" in issue.message for issue in result.issues)


def test_core_claims_mode_rejects_cross_manifest_duplicate(monkeypatch):
    manifest = topology_doctor.load_core_claims()
    manifest["claims"][0]["claim_id"] = "WMO_HALF_UP_FORMULA"

    monkeypatch.setattr(topology_doctor, "load_core_claims", lambda: manifest)
    result = topology_doctor.run_core_claims()

    assert not result.ok
    assert any(issue.code == "core_claim_duplicate_id" for issue in result.issues)


def test_core_map_rejects_unreplaced_core_claim(monkeypatch):
    manifest = topology_doctor.load_core_claims()
    claim = next(item for item in manifest["claims"] if item["claim_id"] == "VIG_BEFORE_BLEND")
    claim["claim_status"] = "partial_replacement_candidate"

    monkeypatch.setattr(topology_doctor, "load_core_claims", lambda: manifest)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("required claim VIG_BEFORE_BLEND is not replaced" in item for item in payload["invalid"])


def test_core_map_mode_passes_current_profiles():
    result = topology_doctor.run_core_maps()

    assert_topology_ok(result)


def test_core_map_missing_required_claim_is_invalid(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    profile["nodes"][0] = {
        **profile["nodes"][0],
        "required_claims": ["DOES_NOT_EXIST"],
    }

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("missing required claim" in item for item in payload["invalid"])


def test_core_map_missing_edge_proof_is_invalid(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    profile["edges"][0] = {
        **profile["edges"][0],
        "proof": {
            **profile["edges"][0]["proof"],
            "symbol": "NO_SUCH_SYMBOL",
        },
    }

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("edge settlement_semantics->ensemble_signal invalid" in item for item in payload["invalid"])


def test_core_map_rejects_unknown_edge_endpoints(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    profile["edges"][0] = {
        **profile["edges"][0],
        "from": "missing_node",
    }

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("unknown edge endpoint" in item for item in payload["invalid"])


def test_core_map_rejects_import_proof_on_unrelated_file(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    profile["edges"][0] = {
        **profile["edges"][0],
        "proof": {
            "kind": "import_or_call",
            "path": "src/engine/evaluator.py",
            "contains": "SettlementSemantics",
        },
    }

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("proof path must equal target node file" in item for item in payload["invalid"])


def test_core_map_rejects_partial_required_claim(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    profile["nodes"][0] = {
        **profile["nodes"][0],
        "required_claims": ["DECISION_GROUP_INDEPENDENCE"],
    }

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("is not replaced" in item for item in payload["invalid"])


def test_core_map_forbidden_phrase_guard_catches_round_variant(monkeypatch):
    manifest = topology_doctor.load_reference_replacement()
    entry = reference_entry(manifest, "docs/reference/zeus_math_spec.md")
    entry["claim_proofs"][0] = {
        **entry["claim_proofs"][0],
        "assertion": "Settlement-aligned values use round( value + 0.5 ).",
    }

    monkeypatch.setattr(topology_doctor, "load_reference_replacement", lambda: manifest)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("forbidden phrase emitted" in item for item in payload["invalid"])


def test_core_map_relationship_test_requires_locator(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    missing_symbol = "DEFINITELY_ABSENT_" + "RELATIONSHIP_SYMBOL_XYZ"
    profile["edges"][0] = {
        **profile["edges"][0],
        "proof": {
            "kind": "relationship_test",
            "path": "tests/test_topology_doctor.py",
            "contains": missing_symbol,
        },
    }

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    payload = topology_doctor.build_core_map("probability-chain")

    assert any("relationship_test proof text not found" in item for item in payload["invalid"])


def test_core_map_rejects_reference_doc_authority_node(monkeypatch):
    topology = topology_doctor.load_topology()
    profile = next(item for item in topology["core_map_profiles"] if item["id"] == "probability-chain")
    profile["nodes"].append(
        {"id": "bad_reference", "file": "docs/reference/zeus_math_spec.md"}
    )
    profile["max_nodes"] = 20

    monkeypatch.setattr(topology_doctor, "load_topology", lambda: topology)
    result = topology_doctor.run_core_maps()

    assert not result.ok
    assert any(issue.code == "core_map_reference_authority_leak" for issue in result.issues)
