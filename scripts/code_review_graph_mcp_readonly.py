#!/usr/bin/env python3
"""Zeus-safe read-only MCP facade for Code Review Graph."""
# Lifecycle: created=2026-04-19; last_reviewed=2026-04-21; last_reused=2026-04-21
# Purpose: Expose Code Review Graph review/search tools without source-writing apply_refactor.
# Reuse: Keep source-writing tools out of this facade; graph cache writes are allowed.

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from code_review_graph.tools import (
    build_or_update_graph,
    detect_changes_func,
    find_large_functions,
    get_affected_flows_func,
    get_architecture_overview_func,
    get_community_func,
    get_flow,
    get_impact_radius,
    get_minimal_context,
    get_review_context,
    list_communities_func,
    list_flows,
    list_graph_stats,
    query_graph,
    refactor_func,
    run_postprocess,
    semantic_search_nodes,
)


_default_repo_root: Optional[str] = os.environ.get("CRG_REPO_ROOT") or None

mcp = FastMCP(
    "code-review-graph",
    instructions=(
        "Zeus-safe Code Review Graph facade. Use after Zeus topology_doctor "
        "routing. This server intentionally omits apply_refactor_tool and wiki "
        "generation so graph output remains derived review evidence, not source "
        "authority."
    ),
)


def _normalize_repo_root(repo_root: Optional[str]) -> Optional[str]:
    if not repo_root:
        return None
    return str(Path(repo_root).expanduser().resolve())


def _repo(repo_root: Optional[str]) -> Optional[str]:
    """Resolve repo root without baking a workstation path into the repo."""
    return (
        _normalize_repo_root(repo_root)
        or _normalize_repo_root(_default_repo_root)
        or _normalize_repo_root(os.environ.get("CRG_REPO_ROOT"))
    )


@mcp.tool()
async def build_or_update_graph_tool(
    full_rebuild: bool = False,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
    postprocess: str = "minimal",
    recurse_submodules: Optional[bool] = None,
) -> dict:
    """Build/update the local graph cache; does not write source files."""
    return await asyncio.to_thread(
        build_or_update_graph,
        full_rebuild=full_rebuild,
        repo_root=_repo(repo_root),
        base=base,
        postprocess=postprocess,
        recurse_submodules=recurse_submodules,
    )


@mcp.tool()
async def run_postprocess_tool(
    flows: bool = True,
    communities: bool = True,
    fts: bool = True,
    repo_root: Optional[str] = None,
) -> dict:
    """Run graph-cache postprocessing for flows, communities, and FTS."""
    return await asyncio.to_thread(
        run_postprocess,
        flows=flows,
        communities=communities,
        fts=fts,
        repo_root=_repo(repo_root),
    )


@mcp.tool()
def list_graph_stats_tool(repo_root: Optional[str] = None) -> dict:
    """Return graph statistics and freshness metadata."""
    return list_graph_stats(repo_root=_repo(repo_root))


@mcp.tool()
def get_minimal_context_tool(
    task: str = "",
    changed_files: Optional[list[str]] = None,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
) -> dict:
    """Return compact graph context after Zeus topology routing."""
    return get_minimal_context(task=task, changed_files=changed_files, repo_root=_repo(repo_root), base=base)


@mcp.tool()
async def detect_changes_tool(
    base: str = "HEAD~1",
    changed_files: Optional[list[str]] = None,
    include_source: bool = False,
    max_depth: int = 2,
    repo_root: Optional[str] = None,
    detail_level: str = "standard",
) -> dict:
    """Risk-score code changes using the local graph cache."""
    return await asyncio.to_thread(
        detect_changes_func,
        base=base,
        changed_files=changed_files,
        include_source=include_source,
        max_depth=max_depth,
        repo_root=_repo(repo_root),
        detail_level=detail_level,
    )


@mcp.tool()
def get_review_context_tool(
    changed_files: Optional[list[str]] = None,
    max_depth: int = 2,
    include_source: bool = True,
    max_lines_per_file: int = 200,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
    detail_level: str = "standard",
) -> dict:
    """Generate source snippets and graph context for code review."""
    return get_review_context(
        changed_files=changed_files,
        max_depth=max_depth,
        include_source=include_source,
        max_lines_per_file=max_lines_per_file,
        repo_root=_repo(repo_root),
        base=base,
        detail_level=detail_level,
    )


@mcp.tool()
def get_impact_radius_tool(
    changed_files: Optional[list[str]] = None,
    max_depth: int = 2,
    repo_root: Optional[str] = None,
    base: str = "HEAD~1",
    detail_level: str = "standard",
) -> dict:
    """Analyze code blast radius for changed files."""
    return get_impact_radius(
        changed_files=changed_files,
        max_depth=max_depth,
        repo_root=_repo(repo_root),
        base=base,
        detail_level=detail_level,
    )


@mcp.tool()
def get_affected_flows_tool(
    changed_files: Optional[list[str]] = None,
    base: str = "HEAD~1",
    repo_root: Optional[str] = None,
) -> dict:
    """Find execution flows affected by changed code files."""
    return get_affected_flows_func(changed_files=changed_files, base=base, repo_root=_repo(repo_root))


@mcp.tool()
def query_graph_tool(
    pattern: str,
    target: str,
    repo_root: Optional[str] = None,
    detail_level: str = "standard",
) -> dict:
    """Trace callers, callees, imports, importers, children, tests, or file summaries."""
    return query_graph(pattern=pattern, target=target, repo_root=_repo(repo_root), detail_level=detail_level)


@mcp.tool()
def semantic_search_nodes_tool(
    query: str,
    kind: Optional[str] = None,
    limit: int = 20,
    repo_root: Optional[str] = None,
    model: Optional[str] = None,
    detail_level: str = "standard",
) -> dict:
    """Search graph nodes by name or keyword."""
    return semantic_search_nodes(query=query, kind=kind, limit=limit, repo_root=_repo(repo_root), model=model, detail_level=detail_level)


@mcp.tool()
def get_architecture_overview_tool(repo_root: Optional[str] = None) -> dict:
    """Return derived graph community overview; not Zeus authority."""
    return get_architecture_overview_func(repo_root=_repo(repo_root))


@mcp.tool()
def list_communities_tool(
    sort_by: str = "size",
    min_size: int = 0,
    detail_level: str = "standard",
    repo_root: Optional[str] = None,
) -> dict:
    """List derived graph communities."""
    return list_communities_func(repo_root=_repo(repo_root), sort_by=sort_by, min_size=min_size, detail_level=detail_level)


@mcp.tool()
def get_community_tool(
    community_name: Optional[str] = None,
    community_id: Optional[int] = None,
    include_members: bool = False,
    repo_root: Optional[str] = None,
) -> dict:
    """Inspect one derived graph community."""
    return get_community_func(community_name=community_name, community_id=community_id, include_members=include_members, repo_root=_repo(repo_root))


@mcp.tool()
def list_flows_tool(
    sort_by: str = "criticality",
    limit: int = 50,
    kind: Optional[str] = None,
    detail_level: str = "standard",
    repo_root: Optional[str] = None,
) -> dict:
    """List derived execution flows."""
    return list_flows(repo_root=_repo(repo_root), sort_by=sort_by, limit=limit, kind=kind, detail_level=detail_level)


@mcp.tool()
def get_flow_tool(
    flow_id: Optional[int] = None,
    flow_name: Optional[str] = None,
    include_source: bool = False,
    repo_root: Optional[str] = None,
) -> dict:
    """Inspect one derived execution flow."""
    return get_flow(flow_id=flow_id, flow_name=flow_name, include_source=include_source, repo_root=_repo(repo_root))


@mcp.tool()
def find_large_functions_tool(
    min_lines: int = 50,
    kind: Optional[str] = None,
    file_path_pattern: Optional[str] = None,
    limit: int = 50,
    repo_root: Optional[str] = None,
) -> dict:
    """Find large graph nodes for review triage."""
    return find_large_functions(min_lines=min_lines, kind=kind, file_path_pattern=file_path_pattern, limit=limit, repo_root=_repo(repo_root))


@mcp.tool()
def refactor_tool(
    mode: str = "dead_code",
    old_name: Optional[str] = None,
    new_name: Optional[str] = None,
    kind: Optional[str] = None,
    file_pattern: Optional[str] = None,
    repo_root: Optional[str] = None,
) -> dict:
    """Preview refactors or find dead code. Source-writing apply_refactor is intentionally unavailable."""
    return refactor_func(mode=mode, old_name=old_name, new_name=new_name, kind=kind, file_pattern=file_pattern, repo_root=_repo(repo_root))


def main(argv: list[str] | None = None) -> int:
    global _default_repo_root
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=os.environ.get("CRG_REPO_ROOT"),
        help="Repository root for graph operations; defaults to CRG_REPO_ROOT or upstream auto-detection",
    )
    args = parser.parse_args(argv)
    _default_repo_root = _normalize_repo_root(args.repo)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
