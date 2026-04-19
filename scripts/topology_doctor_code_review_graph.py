"""Code Review Graph status checker family for topology_doctor."""
# Lifecycle: created=2026-04-19; last_reviewed=2026-04-19; last_reused=never
# Purpose: Validate local code-review-graph cache freshness without making it authority.
# Reuse: Keep this lane warning-only; graph evidence must not bypass Zeus topology gates.

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


GRAPH_DIR = ".code-review-graph"
GRAPH_DB = ".code-review-graph/graph.db"
CODE_PATTERNS = ("src/**/*.py", "scripts/*.py", "scripts/*.sh", "tests/test_*.py")


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
        for raw in root_gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if line in {".code-review-graph", ".code-review-graph/"}:
                return True
    local_gitignore = api.ROOT / GRAPH_DIR / ".gitignore"
    if local_gitignore.exists():
        text = local_gitignore.read_text(encoding="utf-8", errors="ignore")
        if "*" in {line.strip() for line in text.splitlines()}:
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


def effective_changes(api: Any, changed_files: list[str] | None) -> dict[str, str]:
    try:
        return api._map_maintenance_changes(changed_files or [])
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(str(exc)) from exc


def run_code_review_graph_status(api: Any, changed_files: list[str] | None = None) -> Any:
    issues: list[Any] = []
    db_path = api.ROOT / GRAPH_DB

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
        )

    if graph_db_tracked(api):
        issues.append(
            api._issue(
                "code_review_graph_tracked_db",
                GRAPH_DB,
                "graph.db contains absolute paths and must remain untracked",
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
        )

    try:
        meta = metadata(conn)
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

        files_count = scalar(conn, "SELECT COUNT(*) FROM nodes WHERE kind = 'File'")
        nodes_count = scalar(conn, "SELECT COUNT(*) FROM nodes")
        edges_count = scalar(conn, "SELECT COUNT(*) FROM edges")
        if files_count == 0 or nodes_count == 0 or edges_count == 0:
            issues.append(
                api._warning(
                    "code_review_graph_partial_coverage",
                    GRAPH_DB,
                    f"graph coverage is thin: files={files_count}, nodes={nodes_count}, edges={edges_count}",
                )
            )

        flows_count = scalar(conn, "SELECT COUNT(*) FROM flows")
        communities_count = scalar(conn, "SELECT COUNT(*) FROM communities")
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
    return api.StrictResult(ok=not blocking, issues=issues)
