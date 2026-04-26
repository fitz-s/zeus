"""Code Review Graph status checker family for topology_doctor."""
# Lifecycle: created=2026-04-19; last_reviewed=2026-04-21; last_reused=2026-04-21
# Purpose: Validate tracked code-review-graph online context without making it authority.
# Reuse: Keep freshness/coverage warning-first; graph evidence must not bypass Zeus topology gates.

from __future__ import annotations

import hashlib
import inspect
import json
import sqlite3
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


GRAPH_DIR = ".code-review-graph"
GRAPH_DB = ".code-review-graph/graph.db"
GRAPH_META = ".code-review-graph/graph_meta.json"
GRAPH_APPENDIX_BUDGET_BYTES = 2048
CODE_PATTERNS = ("src/*.py", "src/**/*.py", "scripts/*.py", "scripts/*.sh", "tests/test_*.py")
GRAPH_UNUSABLE_WARNING_CODES = {
    "code_review_graph_missing",
    "code_review_graph_unreadable",
    "code_review_graph_stale_head",
    "code_review_graph_stale_branch",
    "code_review_graph_partial_coverage",
    "code_review_graph_dirty_file_stale",
    "code_review_graph_git_status_failed",
}


def path_matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def current_git_metadata(api: Any) -> tuple[str, str]:
    branch_proc = api.subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=api.ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    head_proc = api.subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=api.ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return branch_proc.stdout.strip(), head_proc.stdout.strip()


def graph_db_tracked(api: Any) -> bool:
    return GRAPH_DB in set(api._git_ls_files())


def graph_ignore_guard_present(api: Any) -> bool:
    root_gitignore = api.ROOT / ".gitignore"
    if root_gitignore.exists():
        lines = {raw.strip() for raw in root_gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()}
        if ".code-review-graph/*" in lines and "!.code-review-graph/graph.db" in lines:
            return True
    local_gitignore = api.ROOT / GRAPH_DIR / ".gitignore"
    if local_gitignore.exists():
        lines = {line.strip() for line in local_gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()}
        if "*" in lines and "!graph.db" in lines:
            return True
    return False


def open_graph_db(api: Any) -> sqlite3.Connection:
    db_path = api.ROOT / GRAPH_DB
    uri = f"file:{db_path}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def metadata(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM metadata")}


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] if row else 0)


def graph_file_paths(conn: sqlite3.Connection) -> list[str]:
    return [
        str(row["file_path"])
        for row in conn.execute(
            "SELECT file_path FROM nodes WHERE kind = 'File' AND file_path IS NOT NULL"
        ).fetchall()
    ]


def infer_path_mode(api: Any, conn: sqlite3.Connection) -> str:
    paths = graph_file_paths(conn)
    if not paths:
        return "unknown"
    absolute = 0
    repo_relative = 0
    other = 0
    for value in paths:
        path = Path(value)
        if path.is_absolute():
            absolute += 1
        elif value and not value.startswith(("../", "./")):
            repo_relative += 1
        else:
            other += 1
    modes = sum(1 for count in (absolute, repo_relative, other) if count)
    if modes > 1:
        return "mixed"
    if absolute:
        return "absolute"
    if repo_relative:
        return "repo_relative"
    return "unknown"


def graph_file_hash(conn: sqlite3.Connection, file_path: str) -> str | None:
    row = conn.execute(
        "SELECT file_hash FROM nodes WHERE kind = 'File' AND file_path = ? LIMIT 1",
        (file_path,),
    ).fetchone()
    if row and row["file_hash"]:
        return str(row["file_hash"])
    row = conn.execute(
        "SELECT file_hash FROM nodes WHERE file_path = ? AND file_hash IS NOT NULL LIMIT 1",
        (file_path,),
    ).fetchone()
    return str(row["file_hash"]) if row and row["file_hash"] else None


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def graph_meta_status(api: Any, *, db_meta: dict[str, str], counts: dict[str, int], path_mode: str) -> dict[str, Any]:
    meta_path = api.ROOT / GRAPH_META
    tracked = GRAPH_META in set(api._git_ls_files())
    if not meta_path.exists():
        return {
            "path": GRAPH_META,
            "present": False,
            "tracked": tracked,
            "parity_status": "absent",
        }
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "path": GRAPH_META,
            "present": True,
            "tracked": tracked,
            "parity_status": "unreadable",
            "error": str(exc),
        }
    mismatches: list[str] = []
    if str(data.get("git_head") or data.get("git_head_sha") or "") != str(db_meta.get("git_head_sha") or ""):
        mismatches.append("git_head")
    if str(data.get("git_branch") or "") != str(db_meta.get("git_branch") or ""):
        mismatches.append("git_branch")
    if str(data.get("path_mode") or "") != path_mode:
        mismatches.append("path_mode")
    data_counts = data.get("counts") or {}
    for key, value in counts.items():
        if key in data_counts and int(data_counts.get(key) or 0) != int(value):
            mismatches.append(f"counts.{key}")
    return {
        "path": GRAPH_META,
        "present": True,
        "tracked": tracked,
        "parity_status": "mismatch" if mismatches else "ok",
        "mismatches": mismatches,
    }


def effective_changes(api: Any, changed_files: list[str] | None) -> dict[str, str]:
    try:
        return api._map_maintenance_changes(changed_files or [])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(str(exc)) from exc


def run_code_review_graph_status(api: Any, changed_files: list[str] | None = None, *, include_appendix: bool = True) -> Any:
    issues: list[Any] = []
    db_path = api.ROOT / GRAPH_DB
    details: dict[str, Any] = {
        "authority_status": "derived_code_impact_not_authority",
        "graph_db": GRAPH_DB,
        "path_mode": "unknown",
        "graph_meta": {
            "path": GRAPH_META,
            "present": (api.ROOT / GRAPH_META).exists(),
            "tracked": GRAPH_META in set(api._git_ls_files()),
            "parity_status": "not_checked",
        },
    }
    if changed_files and include_appendix:
        details["appendix"] = build_graph_appendix(api, changed_files, task=None)

    if not db_path.exists():
        return api.StrictResult(
            ok=True,
            issues=[
                api._warning(
                    "code_review_graph_missing",
                    GRAPH_DB,
                    "local Code Review Graph DB is missing; graph evidence is unavailable",
                )
            ],
            details=details,
        )

    if not graph_db_tracked(api):
        issues.append(
            api._issue(
                "code_review_graph_untracked_db",
                GRAPH_DB,
                "graph.db is the tracked derived online-context artifact; stage it or rebuild before relying on online graph context",
            )
        )
    if not graph_ignore_guard_present(api):
        issues.append(
            api._issue(
                "code_review_graph_ignore_missing",
                GRAPH_DIR,
                "missing .code-review-graph ignore guard",
            )
        )

    try:
        branch, head = current_git_metadata(api)
    except subprocess.CalledProcessError as exc:
        issues.append(api._warning("code_review_graph_git_status_failed", "<git>", f"could not read git metadata: {exc}"))
        branch, head = "", ""

    try:
        conn = open_graph_db(api)
    except sqlite3.Error as exc:
        return api.StrictResult(
            ok=True,
            issues=[
                *issues,
                api._warning("code_review_graph_unreadable", GRAPH_DB, f"could not read graph DB: {exc}"),
            ],
            details=details,
        )

    try:
        meta = metadata(conn)
        counts = graph_counts(conn)
        path_mode = infer_path_mode(api, conn)
        details.update(
            {
                "git_branch": meta.get("git_branch", ""),
                "git_head_sha": meta.get("git_head_sha", ""),
                "last_updated": meta.get("last_updated", ""),
                "schema_version": meta.get("schema_version", ""),
                "path_mode": path_mode,
                "counts": counts,
                "graph_meta": graph_meta_status(api, db_meta=meta, counts=counts, path_mode=path_mode),
            }
        )
        graph_head = meta.get("git_head_sha", "")
        graph_branch = meta.get("git_branch", "")
        if head and graph_head and graph_head != head:
            issues.append(
                api._warning(
                    "code_review_graph_stale_head",
                    GRAPH_DB,
                    f"graph built at {graph_head[:12]}, current HEAD is {head[:12]}",
                )
            )
        if branch and graph_branch and graph_branch != branch:
            issues.append(
                api._warning(
                    "code_review_graph_stale_branch",
                    GRAPH_DB,
                    f"graph built on branch {graph_branch!r}, current branch is {branch!r}",
                )
            )

        files_count = counts["files"]
        nodes_count = counts["nodes"]
        edges_count = counts["edges"]
        if files_count == 0 or nodes_count == 0 or edges_count == 0:
            issues.append(
                api._warning(
                    "code_review_graph_partial_coverage",
                    GRAPH_DB,
                    f"graph coverage is thin: files={files_count}, nodes={nodes_count}, edges={edges_count}",
                )
            )

        flows_count = counts["flows"]
        communities_count = counts["communities"]
        if flows_count == 0 or communities_count == 0:
            issues.append(
                api._warning(
                    "code_review_graph_postprocess_empty",
                    GRAPH_DB,
                    f"postprocess summaries are incomplete: flows={flows_count}, communities={communities_count}",
                )
            )

        try:
            changes = effective_changes(api, changed_files)
        except RuntimeError as exc:
            issues.append(api._warning("code_review_graph_git_status_failed", "<git-status>", f"could not read changed files: {exc}"))
            changes = {}
        for rel_path, kind in sorted(changes.items()):
            if kind == "deleted" or not path_matches_any(rel_path, CODE_PATTERNS):
                continue
            file_path = api.ROOT / rel_path
            if not file_path.exists() or not file_path.is_file():
                continue
            abs_path = file_path.resolve().as_posix()
            stored_hash = graph_file_hash(conn, abs_path)
            if not stored_hash:
                issues.append(
                    api._warning(
                        "code_review_graph_partial_coverage",
                        rel_path,
                        "changed code file is not represented in graph DB",
                    )
                )
                continue
            current_hash = sha256_file(file_path)
            if current_hash != stored_hash:
                issues.append(
                    api._warning(
                        "code_review_graph_dirty_file_stale",
                        rel_path,
                        "changed code file hash differs from graph DB; update graph before relying on code-impact evidence",
                    )
                )
    finally:
        conn.close()

    blocking = [issue for issue in issues if issue.severity == "error"]
    return api.StrictResult(ok=not blocking, issues=issues, details=details)


def _node_ref(node: dict[str, Any]) -> str:
    path = node.get("path") or ""
    line = node.get("line_start")
    name = node.get("qualified_name") or node.get("name") or ""
    return f"{path}:{line}:{name}" if line else f"{path}:{name}"


def _appendix_freshness(impact: dict[str, Any]) -> tuple[str, str]:
    if impact.get("usable"):
        return "fresh", "graph impact extraction succeeded"
    reason = str(impact.get("reason") or "")
    issues = (impact.get("graph_health") or {}).get("issues") or []
    codes = {str(issue.get("code") or "") for issue in issues}
    if "code_review_graph_missing" in codes or "graph db missing" in reason.lower():
        return "missing", reason or "graph DB missing"
    if codes & GRAPH_UNUSABLE_WARNING_CODES:
        return "stale", reason or "graph health reported advisory issues"
    if issues or reason:
        return "stale", reason or "graph health reported advisory issues"
    if not impact.get("applicable"):
        return "fresh", "no graph-applicable code files requested"
    return "stale", "graph appendix unavailable for unknown reason"


def _budget_appendix(payload: dict[str, Any]) -> dict[str, Any]:
    payload["truncation"] = {"applied": False, "hint": ""}
    encoded = json.dumps(payload, sort_keys=True)
    if len(encoded.encode("utf-8")) <= GRAPH_APPENDIX_BUDGET_BYTES:
        return payload
    for key in ("changed_nodes", "likely_tests", "impacted_files", "missing_coverage"):
        payload[key] = [str(item)[:160] for item in payload.get(key, [])[:3]]
    payload["graph_freshness_reason"] = str(payload.get("graph_freshness_reason") or "")[:240]
    payload["truncation"] = {
        "applied": True,
        "hint": "graph appendix exceeded 2048 bytes; run code-review-graph or topology_doctor graph commands directly",
    }
    while len(json.dumps(payload, sort_keys=True).encode("utf-8")) > GRAPH_APPENDIX_BUDGET_BYTES:
        reduced = False
        for key in ("changed_nodes", "likely_tests", "impacted_files", "missing_coverage"):
            if payload.get(key):
                payload[key] = payload[key][:-1]
                reduced = True
        if not reduced:
            payload["graph_freshness_reason"] = str(payload.get("graph_freshness_reason") or "")[:80]
            payload["limitations"] = ["Graph output is derived review context only."]
            break
    return payload


def build_graph_appendix(api: Any, files: list[str], task: str | None = None) -> dict[str, Any]:
    impact = api.build_code_impact_graph(files, task=task or "")
    freshness, reason = _appendix_freshness(impact)
    likely_tests = sorted(
        {
            test.get("path") or _node_ref(test)
            for entry in impact.get("tests_for") or []
            for test in entry.get("tests") or []
        }
    )
    payload = {
        "authority_status": "derived_not_authority",
        "graph_freshness": freshness,
        "graph_freshness_reason": reason,
        "limitations": [
            "Graph output is derived review context only.",
            "Graph output never waives topology navigation, planning lock, manifests, receipts, tests, or source truth.",
        ],
        "changed_nodes": [_node_ref(node) for node in (impact.get("changed_nodes") or [])[:8]],
        "likely_tests": likely_tests[:8],
        "impacted_files": (impact.get("impacted_files") or [])[:12],
        "missing_coverage": [
            _node_ref(node) for node in (impact.get("test_gaps") or [])[:8]
        ] + ([reason] if reason and not impact.get("usable") else []),
    }
    return _budget_appendix(payload)


def relativize(api: Any, path: str) -> str:
    try:
        return Path(path).resolve().relative_to(api.ROOT).as_posix()
    except ValueError:
        return path


def node_payload(api: Any, row: sqlite3.Row) -> dict[str, Any]:
    return {
        "name": row["name"],
        "kind": row["kind"],
        "path": relativize(api, row["file_path"]),
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "qualified_name": row["qualified_name"],
    }


def graph_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "files": scalar(conn, "SELECT COUNT(*) FROM nodes WHERE kind = 'File'"),
        "nodes": scalar(conn, "SELECT COUNT(*) FROM nodes"),
        "edges": scalar(conn, "SELECT COUNT(*) FROM edges"),
        "flows": scalar(conn, "SELECT COUNT(*) FROM flows"),
        "communities": scalar(conn, "SELECT COUNT(*) FROM communities"),
    }


def rows_for_file(conn: sqlite3.Connection, file_path: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT name, kind, qualified_name, file_path, line_start, line_end, is_test
        FROM nodes
        WHERE file_path = ? AND kind != 'File'
        ORDER BY COALESCE(line_start, 0), name
        """,
        (file_path,),
    ).fetchall()


def related_nodes(
    conn: sqlite3.Connection,
    *,
    edge_kind: str,
    target_qualified: str,
    target_name: str,
    limit: int = 8,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT n.name, n.kind, n.qualified_name, n.file_path, n.line_start, n.line_end, n.is_test
        FROM edges e
        JOIN nodes n ON n.qualified_name = e.source_qualified
        WHERE e.kind = ? AND e.target_qualified IN (?, ?)
        ORDER BY n.file_path, COALESCE(n.line_start, 0), n.name
        LIMIT ?
        """,
        (edge_kind, target_qualified, target_name, limit),
    ).fetchall()


def impacted_files_for_nodes(api: Any, conn: sqlite3.Connection, qualified_names: list[str], limit: int = 30) -> list[str]:
    if not qualified_names:
        return []
    placeholders = ",".join("?" for _ in qualified_names)
    rows = conn.execute(
        f"""
        SELECT DISTINCT COALESCE(n.file_path, e.file_path) AS file_path
        FROM edges e
        LEFT JOIN nodes n
          ON n.qualified_name = CASE
            WHEN e.source_qualified IN ({placeholders}) THEN e.target_qualified
            ELSE e.source_qualified
          END
        WHERE e.source_qualified IN ({placeholders})
           OR e.target_qualified IN ({placeholders})
        LIMIT ?
        """,
        [*qualified_names, *qualified_names, *qualified_names, limit],
    ).fetchall()
    return sorted(
        {
            relativize(api, row["file_path"])
            for row in rows
            if row["file_path"]
        }
    )


def build_code_impact_graph(api: Any, files: list[str], task: str = "") -> dict[str, Any]:
    target_files = sorted(dict.fromkeys(files))
    code_files = [path for path in target_files if path_matches_any(path, CODE_PATTERNS)]
    payload: dict[str, Any] = {
        "authority_status": "derived_code_impact_not_authority",
        "task": task,
        "target_files": target_files,
        "code_files": code_files,
        "applicable": bool(code_files),
        "usable": False,
        "graph_health": {},
        "changed_nodes": [],
        "callers": [],
        "tests_for": [],
        "test_gaps": [],
        "impacted_files": [],
        "limitations": [
            "Graph output is derived review evidence only.",
            "A low graph risk result never waives topology gates, planning-lock, manifests, receipts, or canonical truth checks.",
        ],
    }
    if not code_files:
        payload["reason"] = "no source/test/script code files in this context pack"
        return payload

    status_signature = inspect.signature(api.run_code_review_graph_status)
    supports_include_appendix = (
        "include_appendix" in status_signature.parameters
        or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in status_signature.parameters.values()
        )
    )
    if supports_include_appendix:
        status = api.run_code_review_graph_status(code_files, include_appendix=False)
    else:
        status = api.run_code_review_graph_status(code_files)
    health_issues = [api.asdict(issue) for issue in status.issues]
    payload["graph_health"] = {
        "ok": status.ok,
        "issue_count": len(status.issues),
        "blocking_count": len([issue for issue in status.issues if issue.severity == "error"]),
        "warning_count": len([issue for issue in status.issues if issue.severity == "warning"]),
        "issues": health_issues,
    }
    if (
        not status.ok
        or any(issue.code in GRAPH_UNUSABLE_WARNING_CODES for issue in status.issues)
    ):
        payload["reason"] = "graph cache is unavailable, stale, or missing target code coverage"
        return payload

    try:
        conn = open_graph_db(api)
    except sqlite3.Error as exc:
        payload["reason"] = f"graph DB unreadable: {exc}"
        return payload

    try:
        meta = metadata(conn)
        counts = graph_counts(conn)
        path_mode = infer_path_mode(api, conn)
        payload["graph_metadata"] = {
            "git_branch": meta.get("git_branch", ""),
            "git_head_sha": meta.get("git_head_sha", ""),
            "last_updated": meta.get("last_updated", ""),
            "schema_version": meta.get("schema_version", ""),
            "path_mode": path_mode,
            "graph_meta": graph_meta_status(api, db_meta=meta, counts=counts, path_mode=path_mode),
            "counts": counts,
        }
        changed_rows: list[sqlite3.Row] = []
        for rel_path in code_files:
            abs_path = (api.ROOT / rel_path).resolve().as_posix()
            changed_rows.extend(rows_for_file(conn, abs_path))
        payload["changed_nodes"] = [node_payload(api, row) for row in changed_rows[:40]]

        callers: list[dict[str, Any]] = []
        tests_for: list[dict[str, Any]] = []
        test_gaps: list[dict[str, Any]] = []
        qualified_names: list[str] = []
        for row in changed_rows:
            qualified = row["qualified_name"]
            name = row["name"]
            qualified_names.append(qualified)
            if row["kind"] not in {"Function", "Class"} or row["is_test"]:
                continue
            caller_rows = related_nodes(
                conn,
                edge_kind="CALLS",
                target_qualified=qualified,
                target_name=name,
            )
            test_rows = related_nodes(
                conn,
                edge_kind="TESTED_BY",
                target_qualified=qualified,
                target_name=name,
            )
            if caller_rows:
                callers.append(
                    {
                        "target": node_payload(api, row),
                        "callers": [node_payload(api, caller) for caller in caller_rows],
                    }
                )
            if test_rows:
                tests_for.append(
                    {
                        "target": node_payload(api, row),
                        "tests": [node_payload(api, test) for test in test_rows],
                    }
                )
            else:
                test_gaps.append(node_payload(api, row))
        payload["callers"] = callers[:20]
        payload["tests_for"] = tests_for[:20]
        payload["test_gaps"] = test_gaps[:20]
        payload["impacted_files"] = impacted_files_for_nodes(api, conn, qualified_names)
        payload["usable"] = True
        return payload
    finally:
        conn.close()
