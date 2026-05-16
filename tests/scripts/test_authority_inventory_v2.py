# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p9_authority_inventory_v2/SCAFFOLD.md (rev 1.2)
"""
Tests for scripts/authority_inventory_v2.py (P9.1).

Coverage:
  - iter_git_surfaces: with git-tracked tmp repo fixture
  - iter_fs_surfaces: with tmp_path fixtures
  - iter_glob_surfaces: zero-match LATENT_TARGET sentinel; non-zero match
  - compute_drift_score: formula edges (zero commits, future mtime, no history)
  - render_row: format matches v1 first-5-column schema; v2 columns appended
  - LATENT_TARGET sentinel row shape
  - main() end-to-end on fixture repo
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Add scripts/ to sys.path so we can import the module directly
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

from authority_inventory_v2 import (
    SurfaceRow,
    build_arg_parser,
    compute_drift_score,
    format_inventory_table,
    iter_fs_surfaces,
    iter_git_surfaces,
    iter_glob_surfaces,
    load_invariant_failure_hits,
    main,
    render_row,
    _count_lines,
    _days_since_date_str,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

AS_OF = datetime(2026, 5, 15, 12, 0, 0)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one tracked file."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True, capture_output=True
    )
    # Create a tracked file
    tracked = tmp_path / "authority_doc.md"
    tracked.write_text("# Authority\nLine 2\nLine 3\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "init"],
        check=True, capture_output=True
    )
    return tmp_path


@pytest.fixture
def git_repo_with_claude(tmp_path: Path) -> Path:
    """Create a minimal git repo with .claude/CLAUDE.md tracked."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True, capture_output=True
    )
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    claude_md = claude_dir / "CLAUDE.md"
    claude_md.write_text("# Claude instructions\nLine 2\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "add .claude/CLAUDE.md"],
        check=True, capture_output=True
    )
    return tmp_path


# ---------------------------------------------------------------------------
# iter_git_surfaces tests
# ---------------------------------------------------------------------------

class TestIterGitSurfaces:
    def test_yields_row_for_tracked_file(self, git_repo: Path) -> None:
        rows = list(iter_git_surfaces(
            repo_root=git_repo,
            path_patterns=["authority_doc.md"],
            as_of=AS_OF,
            cohort="7A",
            authority_marker="YES",
        ))
        assert len(rows) == 1
        row = rows[0]
        assert row.path == "authority_doc.md"
        assert row.source_type == "git"
        assert row.cohort == "7A"
        assert row.authority_marker == "YES"
        assert row.lines == 3
        assert isinstance(row.commits_30d, int)

    def test_skips_untracked_file(self, git_repo: Path, capsys) -> None:
        (git_repo / "untracked.md").write_text("untracked\n")
        rows = list(iter_git_surfaces(
            repo_root=git_repo,
            path_patterns=["untracked.md"],
            as_of=AS_OF,
            cohort="7A",
        ))
        assert rows == []

    def test_skips_missing_pattern(self, git_repo: Path) -> None:
        rows = list(iter_git_surfaces(
            repo_root=git_repo,
            path_patterns=["does_not_exist.md"],
            as_of=AS_OF,
            cohort="7A",
        ))
        assert rows == []

    def test_glob_pattern_expands(self, git_repo: Path) -> None:
        # Add a second file matching a glob
        (git_repo / "op_1.md").write_text("op1\n")
        (git_repo / "op_2.md").write_text("op2\n")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], capture_output=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "add ops"],
            capture_output=True
        )
        rows = list(iter_git_surfaces(
            repo_root=git_repo,
            path_patterns=["op_*.md"],
            as_of=AS_OF,
            cohort="7E",
        ))
        assert len(rows) == 2

    def test_authority_marker_no(self, git_repo: Path) -> None:
        rows = list(iter_git_surfaces(
            repo_root=git_repo,
            path_patterns=["authority_doc.md"],
            as_of=AS_OF,
            cohort="7F",
            authority_marker="NO",
        ))
        assert rows[0].authority_marker == "NO"


# ---------------------------------------------------------------------------
# iter_fs_surfaces tests
# ---------------------------------------------------------------------------

class TestIterFsSurfaces:
    def test_yields_row_for_existing_file(self, tmp_path: Path) -> None:
        fpath = tmp_path / "CLAUDE.md"
        fpath.write_text("# Global\nLine 2\n")
        rows = list(iter_fs_surfaces(
            paths=[fpath],
            as_of=AS_OF,
            cohort="7B",
        ))
        assert len(rows) == 1
        row = rows[0]
        assert row.source_type == "fs"
        assert row.commits_30d == "n/a (non-git)"
        assert row.last_commit_date.startswith("mtime:")
        assert row.lines == 2
        assert row.cohort == "7B"
        assert str(fpath) == row.path

    def test_skips_missing_path(self, tmp_path: Path) -> None:
        rows = list(iter_fs_surfaces(
            paths=[tmp_path / "nonexistent.md"],
            as_of=AS_OF,
            cohort="7C",
        ))
        assert rows == []

    def test_days_since_positive(self, tmp_path: Path) -> None:
        fpath = tmp_path / "old.md"
        fpath.write_text("content\n")
        # Artificially set mtime 10 days ago
        import os, time
        old_time = AS_OF.timestamp() - 10 * 86400
        os.utime(fpath, (old_time, old_time))
        rows = list(iter_fs_surfaces(paths=[fpath], as_of=AS_OF, cohort="7C"))
        assert rows[0].days_since_last_change is not None
        assert 9.5 < rows[0].days_since_last_change < 10.5


# ---------------------------------------------------------------------------
# iter_glob_surfaces tests
# ---------------------------------------------------------------------------

class TestIterGlobSurfaces:
    def test_latent_target_sentinel_on_zero_match(self, tmp_path: Path) -> None:
        rows = list(iter_glob_surfaces(
            repo_root=tmp_path,
            glob_pattern="architecture/modules/*.yaml",
            as_of=AS_OF,
            cohort="7D",
        ))
        assert len(rows) == 1
        row = rows[0]
        assert row.verdict == "LATENT_TARGET"
        assert row.source_type == "latent"
        assert row.path == "architecture/modules/"
        assert row.authority_marker == "LATENT_TARGET"
        assert row.last_commit_date == "n/a (latent)"
        assert row.commits_30d == 0
        assert row.lines == 0
        assert row.drift_score is None

    def test_yields_rows_on_matches(self, git_repo: Path) -> None:
        # Create some yaml files in the repo (not necessarily tracked — glob doesn't check git)
        (git_repo / "modules_dir").mkdir()
        (git_repo / "modules_dir" / "foo.yaml").write_text("key: val\n")
        (git_repo / "modules_dir" / "bar.yaml").write_text("key: val2\n")
        rows = list(iter_glob_surfaces(
            repo_root=git_repo,
            glob_pattern="modules_dir/*.yaml",
            as_of=AS_OF,
            cohort="7D",
        ))
        # Should yield 2 rows, not a sentinel
        assert len(rows) == 2
        for row in rows:
            assert row.verdict != "LATENT_TARGET"
            assert row.source_type == "git"


# ---------------------------------------------------------------------------
# compute_drift_score tests
# ---------------------------------------------------------------------------

class TestComputeDriftScore:
    def _make_git_row(self, days_since: float = 10.0, commits_30d: int = 5,
                      path: str = "architecture/foo.md") -> SurfaceRow:
        return SurfaceRow(
            path=path,
            last_commit_date="2026-05-05 00:00:00",
            commits_30d=commits_30d,
            lines=100,
            authority_marker="YES",
            source_type="git",
            drift_score=None,
            verdict="",
            cohort="7A",
            days_since_last_change=days_since,
        )

    def _make_fs_row(self, days_since: float = 10.0) -> SurfaceRow:
        return SurfaceRow(
            path="/home/user/.claude/CLAUDE.md",
            last_commit_date="mtime:2026-05-05T00:00:00+00:00",
            commits_30d="n/a (non-git)",
            lines=50,
            authority_marker="YES",
            source_type="fs",
            drift_score=None,
            verdict="",
            cohort="7B",
            days_since_last_change=days_since,
        )

    def _make_latent_row(self) -> SurfaceRow:
        return SurfaceRow(
            path="architecture/modules/",
            last_commit_date="n/a (latent)",
            commits_30d=0,
            lines=0,
            authority_marker="LATENT_TARGET",
            source_type="latent",
            drift_score=None,
            verdict="LATENT_TARGET",
            cohort="7D",
            days_since_last_change=None,
        )

    def test_latent_returns_none_latent_verdict(self) -> None:
        row = self._make_latent_row()
        score, verdict = compute_drift_score(row, set(), set())
        assert score is None
        assert verdict == "LATENT_TARGET"

    def test_git_row_score_zero_days(self) -> None:
        row = self._make_git_row(days_since=0.0)
        score, verdict = compute_drift_score(row, set(), set())
        assert score is not None
        assert 0.0 <= score <= 1.0
        # With 0 days, only covered_path weight contributes: 0.3 * 0.5 = 0.15
        assert score == pytest.approx(0.15, abs=0.01)

    def test_git_row_score_90_days(self) -> None:
        row = self._make_git_row(days_since=90.0)
        score, verdict = compute_drift_score(row, set(), set())
        assert score is not None
        # 0.4 * 1.0 + 0.3 * 0.5 + 0 + 0 = 0.55
        assert score == pytest.approx(0.55, abs=0.01)
        assert verdict in ("STALE_REWRITE_NEEDED", "URGENT")

    def test_git_row_score_exceeds_90_days_clamped(self) -> None:
        row = self._make_git_row(days_since=200.0)
        score, verdict = compute_drift_score(row, set(), set())
        # 0.4 * min(1, 200/90) = 0.4 * 1.0 = 0.4 + 0.15 = 0.55; capped at 1.0
        assert score is not None
        assert score <= 1.0

    def test_fs_row_score_formula(self) -> None:
        row = self._make_fs_row(days_since=45.0)
        score, verdict = compute_drift_score(row, set(), set())
        assert score is not None
        # 0.6 * (45/90) + 0 + 0 + 0 = 0.3
        assert score == pytest.approx(0.30, abs=0.01)

    def test_fs_row_score_zero_days(self) -> None:
        row = self._make_fs_row(days_since=0.0)
        score, verdict = compute_drift_score(row, set(), set())
        assert score == pytest.approx(0.0, abs=0.01)
        assert verdict == "CURRENT"

    def test_reference_hit_adds_to_score(self) -> None:
        row = self._make_git_row(days_since=0.0)
        score_no_hit, _ = compute_drift_score(row, set(), set())
        score_hit, _ = compute_drift_score(row, {row.path}, set())
        assert score_hit is not None and score_no_hit is not None
        assert score_hit > score_no_hit
        assert score_hit == pytest.approx(score_no_hit + 0.2, abs=0.01)

    def test_invariant_hit_adds_to_score(self) -> None:
        row = self._make_git_row(days_since=0.0)
        score_no_hit, _ = compute_drift_score(row, set(), set())
        score_hit, _ = compute_drift_score(row, set(), {row.path})
        assert score_hit is not None and score_no_hit is not None
        assert score_hit > score_no_hit

    def test_future_mtime_clamped_to_zero_days(self) -> None:
        row = self._make_fs_row(days_since=-5.0)  # mtime in future
        score, verdict = compute_drift_score(row, set(), set())
        assert score is not None
        assert score >= 0.0
        assert verdict == "CURRENT"

    def test_current_md_stale_override_triggered(self) -> None:
        row = SurfaceRow(
            path="docs/operations/current_state.md",
            last_commit_date="2026-05-05 00:00:00",
            commits_30d=2,
            lines=68,
            authority_marker="NO",
            source_type="git",
            drift_score=None,
            verdict="",
            cohort="7E",
            days_since_last_change=10.0,  # > 7 days
        )
        score, verdict = compute_drift_score(row, set(), set())
        # With 10 days: 0.4*(10/90) + 0.15 ≈ 0.19; normally CURRENT but override fires
        assert verdict == "STALE_REWRITE_NEEDED"

    def test_current_md_stale_override_not_triggered_within_7_days(self) -> None:
        row = SurfaceRow(
            path="docs/operations/current_state.md",
            last_commit_date="2026-05-14 00:00:00",
            commits_30d=2,
            lines=68,
            authority_marker="NO",
            source_type="git",
            drift_score=None,
            verdict="",
            cohort="7E",
            days_since_last_change=1.0,  # < 7 days
        )
        score, verdict = compute_drift_score(row, set(), set())
        assert verdict in ("CURRENT", "MINOR_DRIFT")  # override does not fire


# ---------------------------------------------------------------------------
# render_row tests
# ---------------------------------------------------------------------------

class TestRenderRow:
    def _base_row(self) -> SurfaceRow:
        return SurfaceRow(
            path=".claude/CLAUDE.md",
            last_commit_date="2026-05-15 04:17:16",
            commits_30d=5,
            lines=16,
            authority_marker="YES",
            source_type="git",
            drift_score=0.18,
            verdict="MINOR_DRIFT",
            cohort="7A",
        )

    def test_render_row_has_8_pipe_segments(self) -> None:
        row = self._base_row()
        rendered = render_row(row)
        # Markdown row: | col1 | col2 | ... | col8 |
        # Split by | gives 10 parts (leading+trailing empty)
        parts = rendered.split("|")
        assert len(parts) == 10, f"Expected 10 pipe parts, got {len(parts)}: {rendered}"

    def test_render_row_first_5_columns_match_v1_schema(self) -> None:
        row = self._base_row()
        rendered = render_row(row)
        parts = [p.strip() for p in rendered.split("|")]
        # parts[0] is empty (before leading |), parts[1..5] are the first 5 columns
        assert parts[1] == "2026-05-15 04:17:16"  # Last Commit Date
        assert parts[2] == "5"                      # 30d Commits
        assert parts[3] == "16"                     # Lines
        assert parts[4] == "YES"                    # Authority?
        assert parts[5] == ".claude/CLAUDE.md"      # Path

    def test_render_row_v2_columns(self) -> None:
        row = self._base_row()
        rendered = render_row(row)
        parts = [p.strip() for p in rendered.split("|")]
        assert parts[6] == "git"          # source_type
        assert parts[7] == "0.18"         # drift_score
        assert parts[8] == "MINOR_DRIFT"  # verdict

    def test_render_row_latent_target(self) -> None:
        row = SurfaceRow(
            path="architecture/modules/",
            last_commit_date="n/a (latent)",
            commits_30d=0,
            lines=0,
            authority_marker="LATENT_TARGET",
            source_type="latent",
            drift_score=None,
            verdict="LATENT_TARGET",
            cohort="7D",
        )
        rendered = render_row(row)
        parts = [p.strip() for p in rendered.split("|")]
        assert parts[4] == "LATENT_TARGET"           # Authority? uses LATENT_TARGET
        assert parts[5] == "architecture/modules/"   # Path
        assert parts[6] == "latent"                  # source_type
        assert parts[7] == "n/a"                     # drift_score
        assert parts[8] == "LATENT_TARGET"            # verdict

    def test_render_row_fs_surface_sentinel(self) -> None:
        row = SurfaceRow(
            path="/Users/fitz/.claude/CLAUDE.md",
            last_commit_date="mtime:2026-05-10T08:00:00+00:00",
            commits_30d="n/a (non-git)",
            lines=200,
            authority_marker="YES",
            source_type="fs",
            drift_score=0.33,
            verdict="MINOR_DRIFT",
            cohort="7B",
        )
        rendered = render_row(row)
        parts = [p.strip() for p in rendered.split("|")]
        assert parts[2] == "n/a (non-git)"  # 30d commits sentinel
        assert parts[1].startswith("mtime:")  # last commit date uses mtime prefix


# ---------------------------------------------------------------------------
# format_inventory_table tests
# ---------------------------------------------------------------------------

class TestFormatInventoryTable:
    def test_header_and_separator_present(self) -> None:
        rows: list[SurfaceRow] = []
        table = format_inventory_table(rows)
        assert "| Last Commit Date |" in table
        assert "|---|" in table

    def test_row_count(self) -> None:
        rows = [
            SurfaceRow(
                path=f"doc_{i}.md", last_commit_date="2026-05-15", commits_30d=1,
                lines=10, authority_marker="YES", source_type="git",
                drift_score=0.1, verdict="CURRENT", cohort="7A"
            )
            for i in range(3)
        ]
        table = format_inventory_table(rows)
        data_rows = [l for l in table.splitlines() if l.startswith("| ") and "Last Commit" not in l and "|---|" not in l]
        assert len(data_rows) == 3


# ---------------------------------------------------------------------------
# load_invariant_failure_hits tests
# ---------------------------------------------------------------------------

class TestLoadInvariantFailureHits:
    def test_returns_empty_set(self, tmp_path: Path) -> None:
        result = load_invariant_failure_hits(tmp_path)
        assert result == set()

    def test_returns_set_type(self, tmp_path: Path) -> None:
        result = load_invariant_failure_hits(tmp_path)
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# main() end-to-end test
# ---------------------------------------------------------------------------

class TestMainEndToEnd:
    def test_main_creates_output_file(self, git_repo_with_claude: Path, tmp_path: Path) -> None:
        output_path = tmp_path / "INVENTORY.md"
        sys.argv = [
            "authority_inventory_v2.py",
            "--repo-root", str(git_repo_with_claude),
            "--output", str(output_path),
            "--as-of", "2026-05-15T12:00:00",
        ]
        main()
        assert output_path.exists()
        content = output_path.read_text()
        assert "# Authority Docs Inventory v2" in content

    def test_main_dry_run_writes_to_stdout(self, git_repo_with_claude: Path, tmp_path: Path, capsys) -> None:
        output_path = tmp_path / "DRY_RUN_SHOULD_NOT_EXIST.md"
        sys.argv = [
            "authority_inventory_v2.py",
            "--repo-root", str(git_repo_with_claude),
            "--output", str(output_path),
            "--dry-run",
            "--as-of", "2026-05-15T12:00:00",
        ]
        main()
        captured = capsys.readouterr()
        assert "# Authority Docs Inventory v2" in captured.out
        assert not output_path.exists()

    def test_main_cohort7_row_present(self, git_repo_with_claude: Path, tmp_path: Path) -> None:
        output_path = tmp_path / "INVENTORY.md"
        sys.argv = [
            "authority_inventory_v2.py",
            "--repo-root", str(git_repo_with_claude),
            "--output", str(output_path),
            "--as-of", "2026-05-15T12:00:00",
        ]
        main()
        content = output_path.read_text()
        # .claude/CLAUDE.md should appear (C7-A)
        assert ".claude/CLAUDE.md" in content

    def test_main_latent_target_sentinel_present(self, git_repo_with_claude: Path, tmp_path: Path) -> None:
        output_path = tmp_path / "INVENTORY.md"
        sys.argv = [
            "authority_inventory_v2.py",
            "--repo-root", str(git_repo_with_claude),
            "--output", str(output_path),
            "--as-of", "2026-05-15T12:00:00",
        ]
        main()
        content = output_path.read_text()
        assert "LATENT_TARGET" in content
        assert "architecture/modules/" in content

    def test_main_output_has_table_header(self, git_repo_with_claude: Path, tmp_path: Path) -> None:
        output_path = tmp_path / "INVENTORY.md"
        sys.argv = [
            "authority_inventory_v2.py",
            "--repo-root", str(git_repo_with_claude),
            "--output", str(output_path),
            "--as-of", "2026-05-15T12:00:00",
        ]
        main()
        content = output_path.read_text()
        assert "| Last Commit Date |" in content
        assert "| source_type |" in content
        assert "| drift_score |" in content
        assert "| verdict |" in content


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_count_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("line1\nline2\nline3\n")
        assert _count_lines(f) == 3

    def test_count_lines_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("")
        assert _count_lines(f) == 0

    def test_days_since_date_str_valid(self) -> None:
        days = _days_since_date_str("2026-05-05 00:00:00", AS_OF)
        assert days is not None
        # AS_OF = 2026-05-15 12:00:00, target = 2026-05-05 00:00:00 → 10.5 days
        assert 10.0 <= days <= 11.0

    def test_days_since_date_str_na_returns_conservative(self) -> None:
        days = _days_since_date_str("n/a (no-history)", AS_OF)
        assert days == 90.0

    def test_days_since_date_str_unparseable_returns_conservative(self) -> None:
        days = _days_since_date_str("garbage", AS_OF)
        assert days == 90.0


# ---------------------------------------------------------------------------
# Argparse tests
# ---------------------------------------------------------------------------

class TestBuildArgParser:
    def test_required_output_flag(self) -> None:
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--repo-root", "."])

    def test_defaults(self, tmp_path: Path) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--output", str(tmp_path / "out.md")])
        assert args.repo_root == Path(".")
        assert args.include_v1 is False
        assert args.dry_run is False

    def test_dry_run_flag(self, tmp_path: Path) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["--output", str(tmp_path / "out.md"), "--dry-run"])
        assert args.dry_run is True
