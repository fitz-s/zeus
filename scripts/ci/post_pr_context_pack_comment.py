#!/usr/bin/env python3
# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase C
"""
Post or update a sticky Context Pack summary comment on a PR.

Reads a markdown body from --body (file path) and an optional JSON
artifact from --json-summary (file path). Uses `gh api` to look up
existing PR comments matching the hidden sticky marker, then either
updates the existing one (PATCH) or creates a new one (POST).

The sticky marker is an HTML comment string the renderer never emits
itself, so we can identify our comment unambiguously across pushes.

Usage:
    python scripts/ci/post_pr_context_pack_comment.py \\
        --pr 343 \\
        --body /tmp/context-pack-summary.md \\
        [--json-summary /tmp/context-packs.json] \\
        [--repo owner/name] \\
        [--marker zeus-context-pack-summary] \\
        [--dry-run]

Exit codes:
    0 — comment posted/updated successfully, OR --dry-run
    1 — argument parsing / file read error
    2 — gh CLI error (auth, network, repo not found)

This script is invoked by `.github/workflows/topology-context-advisory.yml`
and never runs as a blocker. continue-on-error keeps any failure here
from breaking the PR build.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_MARKER = "zeus-context-pack-summary"


def _marker_html(marker: str) -> str:
    """Return the HTML comment used to identify our sticky comment."""
    return f"<!-- {marker} -->"


def _gh_default_repo() -> str | None:
    try:
        out = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def list_pr_comments(pr: int, repo: str) -> list[dict[str, Any]]:
    """
    Return list of comments on the PR via REST issue comments endpoint.

    Uses `gh api --paginate --slurp` which wraps all pages of JSON arrays
    into a single outer JSON array, then flattens. This is the documented
    correct way to paginate JSON arrays through gh CLI (per `gh api --help`
    `--slurp` description). The earlier implementation parsed concatenated
    JSON arrays via `][` boundary splitting — fragile, and on parse failure
    silently returned an empty list, which could trigger duplicate sticky
    comments (Copilot finding on PR #344).
    """
    out = subprocess.run(
        [
            "gh", "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{pr}/comments",
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh api list comments failed: {out.stderr.strip()}")
    txt = out.stdout.strip()
    if not txt:
        return []
    try:
        pages = json.loads(txt)
    except json.JSONDecodeError as e:
        # With --slurp we expect a single JSON value; refuse to silently
        # treat parse failure as "no comments exist" — that's the bug
        # path that caused duplicate sticky comments before.
        raise RuntimeError(
            f"gh api --paginate --slurp returned unparseable JSON: {e}; "
            f"output head: {txt[:200]!r}"
        ) from e
    if not isinstance(pages, list):
        raise RuntimeError(
            f"gh api --paginate --slurp returned non-array: {type(pages).__name__}"
        )
    # --slurp wraps pages into an array; each element is itself a list of comments.
    merged: list[dict[str, Any]] = []
    for page in pages:
        if isinstance(page, list):
            merged.extend(page)
        elif isinstance(page, dict):
            # Single-page response (no pagination needed) → dict not array
            merged.append(page)
    return merged


def find_sticky_comment(comments: list[dict[str, Any]], marker: str) -> dict[str, Any] | None:
    needle = _marker_html(marker)
    for c in comments:
        if needle in (c.get("body") or ""):
            return c
    return None


def post_new_comment(pr: int, repo: str, body: str) -> dict[str, Any]:
    out = subprocess.run(
        [
            "gh", "api",
            "-X", "POST",
            f"repos/{repo}/issues/{pr}/comments",
            "-f", f"body={body}",
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh api POST comment failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def update_comment(comment_id: int, repo: str, body: str) -> dict[str, Any]:
    out = subprocess.run(
        [
            "gh", "api",
            "-X", "PATCH",
            f"repos/{repo}/issues/comments/{comment_id}",
            "-f", f"body={body}",
        ],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(f"gh api PATCH comment failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def build_comment_body(
    *,
    marker: str,
    markdown_body: str,
    json_summary: dict[str, Any] | None,
    sha: str | None,
) -> str:
    parts: list[str] = [_marker_html(marker), ""]
    parts.append("## Zeus Context Pack (advisory)")
    parts.append("")
    if sha:
        parts.append(f"*Computed against `{sha[:8]}` — updated on each push.*")
        parts.append("")
    if json_summary:
        packs = json_summary.get("packs") or []
        missing = json_summary.get("missing_surfaces_for_files") or []
        parts.append(
            f"**Summary:** {len(packs)} Context Pack(s) emitted; "
            f"{len(missing)} changed file(s) had no surface match."
        )
        parts.append("")
        if packs:
            parts.append("| Pack | Risk tier | FCs | Blocking tests |")
            parts.append("|---|---|---|---|")
            for p in packs:
                fcs = ", ".join(fc["id"] for fc in p.get("failure_chains", []))
                tests = p.get("ci_classification", {}).get("blocking_relationship", [])
                tests_str = ", ".join(f"`{t}`" for t in tests[:3])
                if len(tests) > 3:
                    tests_str += f" (+{len(tests) - 3})"
                parts.append(
                    f"| `{p['id']}` | {p['risk_tier']} | {fcs or '—'} | {tests_str or '—'} |"
                )
            parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("<details><summary>Full Context Pack(s)</summary>")
    parts.append("")
    parts.append(markdown_body)
    parts.append("")
    parts.append("</details>")
    parts.append("")
    parts.append(
        "*Topology routes context; it does not prove runtime truth. "
        "Tests/runtime gates own correctness. See "
        "`docs/operations/current/plans/ci_topology_refactor_refined.md`.*"
    )
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Post/update sticky Context Pack PR summary comment."
    )
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--body", required=True, help="Path to markdown body file")
    p.add_argument("--json-summary", default=None, help="Optional path to context_packs JSON")
    p.add_argument("--repo", default=None)
    p.add_argument("--marker", default=DEFAULT_MARKER)
    p.add_argument("--sha", default=None, help="Optional commit SHA to cite in comment")
    p.add_argument("--dry-run", action="store_true", help="Print the comment body, do not post")
    args = p.parse_args(argv)

    body_path = Path(args.body)
    if not body_path.exists():
        print(f"ERROR: --body file not found: {body_path}", file=sys.stderr)
        return 1
    markdown_body = body_path.read_text()

    json_summary: dict[str, Any] | None = None
    if args.json_summary:
        jpath = Path(args.json_summary)
        if jpath.exists():
            try:
                json_summary = json.loads(jpath.read_text())
            except json.JSONDecodeError as e:
                print(f"WARNING: --json-summary parse failed: {e}", file=sys.stderr)

    full_body = build_comment_body(
        marker=args.marker,
        markdown_body=markdown_body,
        json_summary=json_summary,
        sha=args.sha or os.environ.get("GITHUB_SHA"),
    )

    if args.dry_run:
        print(full_body)
        return 0

    repo = args.repo or _gh_default_repo()
    if not repo:
        print("ERROR: could not determine repo; pass --repo owner/name", file=sys.stderr)
        return 2

    try:
        comments = list_pr_comments(args.pr, repo)
        existing = find_sticky_comment(comments, args.marker)
        if existing:
            update_comment(existing["id"], repo, full_body)
            print(f"Updated existing sticky comment id={existing['id']}")
        else:
            new = post_new_comment(args.pr, repo, full_body)
            print(f"Posted new sticky comment id={new['id']}")
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
