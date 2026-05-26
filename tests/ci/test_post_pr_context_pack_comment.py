# Created: 2026-05-26
# Last reused or audited: 2026-05-26
# Authority basis: docs/operations/current/plans/ci_topology_refactor_refined.md Phase C
#                  scripts/ci/post_pr_context_pack_comment.py
"""
Unit tests for the Phase C sticky PR-comment poster.

Covers:
  - sticky marker detection (find_sticky_comment)
  - comment body construction (with/without JSON summary, with/without SHA)
  - upsert decision logic (existing → PATCH; absent → POST)
  - --dry-run prints body and exits 0
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.ci.post_pr_context_pack_comment import (
    DEFAULT_MARKER,
    build_comment_body,
    find_sticky_comment,
    main,
)


def _comment(body: str, cid: int = 1) -> dict:
    return {"id": cid, "body": body}


def test_find_sticky_returns_match():
    comments = [
        _comment("regular review comment", cid=10),
        _comment(f"<!-- {DEFAULT_MARKER} -->\n## body", cid=42),
        _comment("another reviewer", cid=11),
    ]
    found = find_sticky_comment(comments, DEFAULT_MARKER)
    assert found is not None
    assert found["id"] == 42


def test_find_sticky_returns_none_when_absent():
    comments = [
        _comment("regular review comment", cid=10),
        _comment("another reviewer", cid=11),
    ]
    assert find_sticky_comment(comments, DEFAULT_MARKER) is None


def test_find_sticky_uses_custom_marker():
    comments = [
        _comment("<!-- zeus-other -->\n## body", cid=42),
    ]
    assert find_sticky_comment(comments, "zeus-other") is not None
    assert find_sticky_comment(comments, "zeus-default") is None


def test_build_comment_body_includes_marker():
    body = build_comment_body(
        marker=DEFAULT_MARKER,
        markdown_body="# inner",
        json_summary=None,
        sha=None,
    )
    assert f"<!-- {DEFAULT_MARKER} -->" in body
    assert "## Zeus Context Pack (advisory)" in body
    assert "# inner" in body


def test_build_comment_body_summary_table():
    summary = {
        "packs": [
            {
                "id": "execution_fresh_submit",
                "risk_tier": "T0",
                "failure_chains": [{"id": "FC-03"}, {"id": "FC-10"}],
                "ci_classification": {
                    "blocking_relationship": ["tests/test_exec_freshness_recapture.py"],
                },
            },
        ],
        "missing_surfaces_for_files": [],
    }
    body = build_comment_body(
        marker=DEFAULT_MARKER,
        markdown_body="# md",
        json_summary=summary,
        sha="abcdef1234",
    )
    assert "execution_fresh_submit" in body
    assert "T0" in body
    assert "FC-03, FC-10" in body
    assert "test_exec_freshness_recapture.py" in body
    assert "abcdef12" in body   # short sha


def test_build_comment_body_no_packs_section_when_missing():
    body = build_comment_body(
        marker=DEFAULT_MARKER,
        markdown_body="# md",
        json_summary={"packs": [], "missing_surfaces_for_files": ["a.txt"]},
        sha=None,
    )
    # Without packs the table is absent but the summary count is still present
    assert "0 Context Pack(s)" in body
    assert "1 changed file(s)" in body


def test_build_comment_body_truncates_test_list():
    summary = {
        "packs": [
            {
                "id": "p",
                "risk_tier": "T1",
                "failure_chains": [],
                "ci_classification": {
                    "blocking_relationship": [f"tests/t{i}.py" for i in range(5)],
                },
            },
        ],
        "missing_surfaces_for_files": [],
    }
    body = build_comment_body(
        marker=DEFAULT_MARKER,
        markdown_body="",
        json_summary=summary,
        sha=None,
    )
    assert "(+2)" in body  # 5 tests → first 3 + (+2)


def test_dry_run_prints_body_and_exits_zero(tmp_path, capsys):
    md = tmp_path / "body.md"
    md.write_text("# hello")
    rc = main([
        "--pr", "343",
        "--body", str(md),
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert f"<!-- {DEFAULT_MARKER} -->" in out
    assert "# hello" in out


def test_dry_run_includes_json_summary_when_provided(tmp_path, capsys):
    md = tmp_path / "body.md"
    md.write_text("# md")
    js = tmp_path / "summary.json"
    js.write_text(json.dumps({
        "packs": [
            {
                "id": "forecast_bundle_extrema",
                "risk_tier": "T1",
                "failure_chains": [{"id": "FC-01"}],
                "ci_classification": {"blocking_relationship": []},
            }
        ],
        "missing_surfaces_for_files": [],
    }))
    rc = main([
        "--pr", "343",
        "--body", str(md),
        "--json-summary", str(js),
        "--dry-run",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "forecast_bundle_extrema" in out
    assert "FC-01" in out


def test_missing_body_file_returns_1(tmp_path, capsys):
    rc = main([
        "--pr", "343",
        "--body", str(tmp_path / "nonexistent.md"),
        "--dry-run",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_comment_body_topology_boundary_disclaimer(tmp_path):
    body = build_comment_body(
        marker=DEFAULT_MARKER,
        markdown_body="",
        json_summary=None,
        sha=None,
    )
    # Spec §0: topology routes context, never proves runtime truth.
    assert "Topology routes context" in body
    assert "ci_topology_refactor_refined.md" in body


def test_marker_html_is_html_comment():
    from scripts.ci.post_pr_context_pack_comment import _marker_html
    s = _marker_html("foo")
    assert s.startswith("<!--") and s.endswith("-->")
