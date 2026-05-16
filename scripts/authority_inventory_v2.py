# Created: 2026-05-15
# Last reused or audited: 2026-05-15
# Authority basis: docs/operations/task_2026-05-15_p9_authority_inventory_v2/SCAFFOLD.md (rev 1.2)
"""
Authority Inventory v2 — Cohort 7 Extension.

Generates an INVENTORY.md with drift scores for authority surfaces including
the 7 new Cohort 7 surfaces: .claude/CLAUDE.md (C7-A), ~/.claude/CLAUDE.md
(C7-B), ~/.openclaw/CLAUDE.md (C7-C), architecture/modules/*.yaml (C7-D
LATENT_TARGET), docs/operations/INDEX.md (C7-E), docs/operations/known_gaps.md
(C7-F), docs/operations/packet_scope_protocol.md (C7-G).

P9.1 IMPLEMENTER GAP: load_invariant_failure_hits() returns empty set because
topology_doctor.py --invariants emits the full invariant slice (not a failure
path list). The 0.1 invariant weight contributes 0 for all rows. See SCAFFOLD.md
§3 for full rationale. This is conservative (never inflates drift score).

Deviation from task brief (logged here per deviations_observed):
  - --include-v1 defaults to False; v1 re-scoring deferred (v1 inventory at
    docs/operations/task_2026-05-15_runtime_improvement_engineering_package/
    00_evidence/AUTHORITY_DOCS_INVENTORY.md is authoritative until mw-daemon
    integration).
  - Output path is a direct file path per --output; the canonical
    task_<DATE>_authority_inventory_v2_run/ directory is the recommended
    location for operators but is NOT auto-created by the generator.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional, Union


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cohort 7 default covers: injections (topic labels only; path predicates not
# specified in DRIFT_ASSESSMENT, so 0.5 default weight is used in formula).
COHORT7_COVERS: dict[str, str] = {
    "C7-A": "agent_session_behavior, topology_doctor_protocol",
    "C7-B": "global_methodology, agent_behavioral_rules",
    "C7-C": "openclaw_architecture, agent_routing",
    "C7-D": "module_invariants, module_capabilities",
    "C7-E-index": "operations_directory_structure",
    "C7-E-current": "live_operational_state",
    "C7-F": "known_gaps_worklist",
    "C7-G": "packet_scope_rules",
}

# Verdict thresholds (standard)
VERDICT_THRESHOLDS = [
    (0.7, "URGENT"),
    (0.4, "STALE_REWRITE_NEEDED"),
    (0.2, "MINOR_DRIFT"),
    (0.0, "CURRENT"),
]

# current_*.md tighter stale threshold: if days_since > 7, force STALE_REWRITE_NEEDED
CURRENT_MD_STALE_DAYS = 7

# Table header (v2 — 8 columns)
TABLE_HEADER = "| Last Commit Date | 30d Commits | Lines | Authority? | Path | source_type | drift_score | verdict |"
TABLE_SEPARATOR = "|---|---|---|---|---|---|---|---|"


# ---------------------------------------------------------------------------
# Data structure
# ---------------------------------------------------------------------------

@dataclass
class SurfaceRow:
    path: str                           # repo-relative (git/latent) or absolute (fs)
    last_commit_date: str               # ISO8601 | "mtime:<ISO8601>" | "n/a (latent)"
    commits_30d: Union[int, str]        # int | "n/a (non-git)" | 0 (latent)
    lines: int                          # 0 for latent
    authority_marker: str               # YES | NO | LATENT_TARGET
    source_type: str                    # "git" | "fs" | "latent"
    drift_score: Optional[float]        # None for LATENT_TARGET
    verdict: str                        # CURRENT | MINOR_DRIFT | STALE_REWRITE_NEEDED | URGENT | LATENT_TARGET
    cohort: str                         # "7A" | "7B" | "7C" | "7D" | "7E" | "7F" | "7G"
    # Internal fields (not rendered in table)
    days_since_last_change: Optional[float] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Iterator: git surfaces
# ---------------------------------------------------------------------------

def iter_git_surfaces(
    repo_root: Path,
    path_patterns: list[str],
    as_of: datetime,
    cohort: str,
    authority_marker: str = "YES",
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each matched path tracked by git.

    Skips untracked files silently with a warning to stderr.
    """
    for pattern in path_patterns:
        # Expand any glob patterns within repo_root
        matches = sorted(repo_root.glob(pattern))
        if not matches:
            # Try as literal path
            literal = repo_root / pattern
            if literal.exists():
                matches = [literal]
            else:
                print(f"[authority_inventory_v2] WARNING: pattern '{pattern}' yielded no matches (skipping)", file=sys.stderr)
                continue

        for fpath in matches:
            rel = fpath.relative_to(repo_root)
            rel_str = str(rel)

            # Check if tracked by git
            try:
                result = subprocess.run(
                    ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", rel_str],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0:
                    print(f"[authority_inventory_v2] WARNING: '{rel_str}' not tracked by git (skipping)", file=sys.stderr)
                    continue
            except (subprocess.TimeoutExpired, FileNotFoundError):
                print(f"[authority_inventory_v2] WARNING: git unavailable for '{rel_str}' (skipping)", file=sys.stderr)
                continue

            last_commit_date = _git_last_commit_date(repo_root, rel_str)
            commits_30d = _git_commits_30d(repo_root, rel_str, as_of)
            lines = _count_lines(fpath)
            days_since = _days_since_date_str(last_commit_date, as_of)

            # Placeholder: drift_score computed after; set None then fill
            yield SurfaceRow(
                path=rel_str,
                last_commit_date=last_commit_date,
                commits_30d=commits_30d,
                lines=lines,
                authority_marker=authority_marker,
                source_type="git",
                drift_score=None,
                verdict="",
                cohort=cohort,
                days_since_last_change=days_since,
            )


# ---------------------------------------------------------------------------
# Iterator: glob surfaces (with LATENT_TARGET sentinel on zero match)
# ---------------------------------------------------------------------------

def iter_glob_surfaces(
    repo_root: Path,
    glob_pattern: str,
    as_of: datetime,
    cohort: str,
    authority_marker: str = "YES",
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each glob match.

    If zero matches, yields one sentinel row with verdict=LATENT_TARGET.
    Never errors on zero matches.
    """
    matches = sorted(repo_root.glob(glob_pattern))
    if not matches:
        # Emit LATENT_TARGET sentinel
        # path = the directory portion of the glob pattern (strip the wildcard)
        pattern_path = Path(glob_pattern)
        latent_dir = str(pattern_path.parent) + "/"
        print(
            f"[authority_inventory_v2] WARNING: glob '{glob_pattern}' yielded zero matches "
            f"— emitting LATENT_TARGET sentinel for '{latent_dir}'",
            file=sys.stderr
        )
        yield SurfaceRow(
            path=latent_dir,
            last_commit_date="n/a (latent)",
            commits_30d=0,
            lines=0,
            authority_marker="LATENT_TARGET",
            source_type="latent",
            drift_score=None,
            verdict="LATENT_TARGET",
            cohort=cohort,
            days_since_last_change=None,
        )
        return

    for fpath in matches:
        rel = fpath.relative_to(repo_root)
        rel_str = str(rel)
        last_commit_date = _git_last_commit_date(repo_root, rel_str)
        commits_30d = _git_commits_30d(repo_root, rel_str, as_of)
        lines = _count_lines(fpath)
        days_since = _days_since_date_str(last_commit_date, as_of)

        yield SurfaceRow(
            path=rel_str,
            last_commit_date=last_commit_date,
            commits_30d=commits_30d,
            lines=lines,
            authority_marker=authority_marker,
            source_type="git",
            drift_score=None,
            verdict="",
            cohort=cohort,
            days_since_last_change=days_since,
        )


# ---------------------------------------------------------------------------
# Iterator: filesystem surfaces (non-git, mtime proxy)
# ---------------------------------------------------------------------------

def iter_fs_surfaces(
    paths: list[Path],
    as_of: datetime,
    cohort: str,
    authority_marker: str = "YES",
) -> Iterator[SurfaceRow]:
    """Yield SurfaceRow for each path using mtime as last_commit proxy.

    source_type='fs'. 30d_commits emits 'n/a (non-git)' sentinel.
    Missing paths are skipped with a warning.
    """
    for fpath in paths:
        if not fpath.exists():
            print(
                f"[authority_inventory_v2] WARNING: fs surface '{fpath}' does not exist (skipping)",
                file=sys.stderr
            )
            continue

        try:
            mtime = fpath.stat().st_mtime
            mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
            mtime_str = mtime_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        except OSError as e:
            print(f"[authority_inventory_v2] WARNING: cannot stat '{fpath}': {e} (skipping)", file=sys.stderr)
            continue

        last_commit_date = f"mtime:{mtime_str}"
        lines = _count_lines(fpath)
        # Compare as naive UTC (as_of is always naive UTC internally)
        mtime_dt_naive = mtime_dt.replace(tzinfo=None)
        as_of_naive = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
        days_since = (as_of_naive - mtime_dt_naive).total_seconds() / 86400.0

        yield SurfaceRow(
            path=str(fpath),
            last_commit_date=last_commit_date,
            commits_30d="n/a (non-git)",
            lines=lines,
            authority_marker=authority_marker,
            source_type="fs",
            drift_score=None,
            verdict="",
            cohort=cohort,
            days_since_last_change=days_since,
        )


# ---------------------------------------------------------------------------
# Drift scoring
# ---------------------------------------------------------------------------

def compute_drift_score(
    row: SurfaceRow,
    reference_replacement_hits: set[str],
    invariant_failure_hits: set[str],
) -> tuple[Optional[float], str]:
    """Return (score, verdict).

    Applies fs-adjusted formula for non-git surfaces.
    Applies 7-day override for current_*.md.
    Returns (None, 'LATENT_TARGET') for sentinel rows.

    Note: covers_overrides removed per SCAFFOLD M4; 0.5 default used for all
    Cohort 7 covered-path weights.
    """
    if row.source_type == "latent":
        return None, "LATENT_TARGET"

    days = row.days_since_last_change
    if days is None:
        days = 90.0  # conservative fallback

    # Clamp days to non-negative (handle mtime in future or clock skew)
    days = max(0.0, days)

    ref_hit = 1.0 if row.path in reference_replacement_hits else 0.0
    inv_hit = 1.0 if row.path in invariant_failure_hits else 0.0

    if row.source_type == "fs":
        # fs formula: no covered-path signal; mtime weight raised to 0.6
        score = (
            0.6 * _normalize(days / 90.0) +
            0.0 * 0 +  # [intentional] covered_path weight zeroed: no git log for fs surfaces
            0.2 * ref_hit +
            0.2 * inv_hit
        )
    else:
        # git formula
        # covered-path weight: use 0.5 default (0.3 * 0.5 = 0.15 contribution)
        covered_path_weight = 0.5
        commits_30d = row.commits_30d if isinstance(row.commits_30d, int) else 0
        score = (
            0.4 * _normalize(days / 90.0) +
            0.3 * covered_path_weight +
            0.2 * ref_hit +
            0.1 * inv_hit
        )
        # Note: the 0.3 * covered_path_weight uses 0.5 default baseline;
        # commits in covered paths are not separately tracked (path predicates
        # not specified in DRIFT_ASSESSMENT).

    score = min(1.0, max(0.0, score))

    # Determine verdict
    verdict = _score_to_verdict(score)

    # 7-day override for current_*.md
    path_name = Path(row.path).name
    if path_name.startswith("current_") and path_name.endswith(".md"):
        if days > CURRENT_MD_STALE_DAYS:
            if verdict in ("CURRENT", "MINOR_DRIFT"):
                verdict = "STALE_REWRITE_NEEDED"

    return round(score, 2), verdict


def _normalize(x: float) -> float:
    return min(1.0, x)


def _score_to_verdict(score: float) -> str:
    for threshold, label in VERDICT_THRESHOLDS:
        if score >= threshold:
            return label
    return "CURRENT"


# ---------------------------------------------------------------------------
# Reference replacement hits loader
# ---------------------------------------------------------------------------

def load_reference_replacement_hits(repo_root: Path) -> set[str]:
    """Run topology_doctor.py --reference-replacement and parse output.

    Returns set of paths with missing_entry flags.
    Returns empty set on any failure (conservative — never inflates drift score).
    """
    topology_doctor = repo_root / "scripts" / "topology_doctor.py"
    if not topology_doctor.exists():
        print(
            "[authority_inventory_v2] WARNING: topology_doctor.py not found; "
            "reference_replacement_hits will be empty set",
            file=sys.stderr
        )
        return set()

    try:
        result = subprocess.run(
            [sys.executable, str(topology_doctor), "--reference-replacement"],
            capture_output=True, text=True, timeout=60, cwd=str(repo_root)
        )
        if result.returncode != 0:
            print(
                f"[authority_inventory_v2] WARNING: topology_doctor.py --reference-replacement "
                f"exited {result.returncode}; hits will be empty set",
                file=sys.stderr
            )
            return set()

        hits: set[str] = set()
        for line in result.stdout.splitlines():
            line = line.strip()
            if "missing_entry" in line.lower() or "MISSING_ENTRY" in line:
                # Heuristic: extract a path-like token from the line
                parts = line.split()
                for part in parts:
                    if "/" in part and not part.startswith("-"):
                        hits.add(part.strip(",:"))
        return hits

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(
            f"[authority_inventory_v2] WARNING: failed to run topology_doctor.py: {e}; "
            "hits will be empty set",
            file=sys.stderr
        )
        return set()


def load_invariant_failure_hits(repo_root: Path) -> set[str]:
    """Attempt to extract paths flagged in invariant output.

    P9.1 IMPLEMENTER GAP: topology_doctor.py --invariants emits the full
    invariant slice (topology_doctor_cli.py:29: 'Emit invariant slice,
    optionally by --zone'), not a failure-path list. No existing CLI flag
    isolates invariant failures as a path set. Options at implementation time:
      (a) parse --invariants JSON/text output for failure indicators,
      (b) use --strict and filter its output for invariant-class issues,
      (c) implement a new --invariant-failures flag in topology_doctor_cli.py.
    Until resolved, this function logs a warning and returns an empty set,
    causing the 0.1 invariant weight to contribute 0 for all rows. This is
    conservative (never inflates drift score) and is noted in INVENTORY.md header.
    """
    print(
        "[authority_inventory_v2] WARNING: load_invariant_failure_hits() is a conservative stub "
        "(P9.1 implementer gap — see SCAFFOLD.md §3 C2). Invariant weight contributes 0 for all rows.",
        file=sys.stderr
    )
    return set()


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_row(row: SurfaceRow) -> str:
    """Render a SurfaceRow as a markdown table row (v2 format, 8 columns)."""
    drift_str = f"{row.drift_score:.2f}" if row.drift_score is not None else "n/a"
    commits_str = str(row.commits_30d)  # int or "n/a (non-git)"
    return (
        f"| {row.last_commit_date} "
        f"| {commits_str} "
        f"| {row.lines} "
        f"| {row.authority_marker} "
        f"| {row.path} "
        f"| {row.source_type} "
        f"| {drift_str} "
        f"| {row.verdict} |"
    )


def format_inventory_table(rows: list[SurfaceRow]) -> str:
    """Render markdown table. v2 columns appended to right of v1 columns."""
    lines = [TABLE_HEADER, TABLE_SEPARATOR]
    for row in rows:
        lines.append(render_row(row))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git_last_commit_date(repo_root: Path, rel_path: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%ci", "--", rel_path],
            capture_output=True, text=True, timeout=15
        )
        out = result.stdout.strip()
        if out:
            # Normalize: "2026-05-15 04:17:16 +0000" → "2026-05-15 04:17:16"
            # Strip trailing timezone offset: drop everything after the seconds field
            # git %ci format: "YYYY-MM-DD HH:MM:SS +ZZZZ"
            parts = out.strip().split(" ")
            if len(parts) >= 2:
                return f"{parts[0]} {parts[1]}"
            return parts[0]
        return "n/a (no-history)"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "n/a (git-error)"


def _git_commits_30d(repo_root: Path, rel_path: str, as_of: datetime) -> int:
    since_str = (as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of)
    since_str = (since_str.replace(hour=0, minute=0, second=0) if True else since_str)
    # 30 days before as_of
    from datetime import timedelta
    since = as_of - timedelta(days=30)
    since_fmt = since.strftime("%Y-%m-%d")
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", "--oneline",
             f"--since={since_fmt}", "--", rel_path],
            capture_output=True, text=True, timeout=15
        )
        lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
        return len(lines)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0


def _count_lines(fpath: Path) -> int:
    try:
        with open(fpath, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _days_since_date_str(date_str: str, as_of: datetime) -> Optional[float]:
    """Parse a date string like '2026-05-15 04:17:16' and return days since as_of."""
    if date_str.startswith("n/a"):
        return 90.0  # conservative fallback for no-history
    # Strip timezone suffix if present
    clean = date_str.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(clean[:len(fmt) + 2].strip()[:19], fmt[:19] if len(fmt) > 10 else fmt)
            as_of_naive = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
            return max(0.0, (as_of_naive - dt).total_seconds() / 86400.0)
        except ValueError:
            continue
    return 90.0  # conservative fallback


# ---------------------------------------------------------------------------
# Surface collector: Cohort 7
# ---------------------------------------------------------------------------

def collect_cohort7_rows(repo_root: Path, as_of: datetime) -> list[SurfaceRow]:
    """Collect and return all Cohort 7 surface rows (unscored)."""
    rows: list[SurfaceRow] = []

    # C7-A: Zeus project-local .claude/CLAUDE.md (git surface)
    rows.extend(iter_git_surfaces(
        repo_root=repo_root,
        path_patterns=[".claude/CLAUDE.md"],
        as_of=as_of,
        cohort="7A",
        authority_marker="YES",
    ))

    # C7-B: User-global ~/.claude/CLAUDE.md (fs surface; outside repo)
    rows.extend(iter_fs_surfaces(
        paths=[Path.home() / ".claude" / "CLAUDE.md"],
        as_of=as_of,
        cohort="7B",
        authority_marker="YES",
    ))

    # C7-C: OpenClaw workspace CLAUDE.md (fs surface; outside repo)
    # Exclude ~/.openclaw/workspace-venus/zeus/.claude/CLAUDE.md — same as C7-A
    rows.extend(iter_fs_surfaces(
        paths=[Path.home() / ".openclaw" / "CLAUDE.md"],
        as_of=as_of,
        cohort="7C",
        authority_marker="YES",
    ))

    # C7-D: architecture/modules/*.yaml (LATENT_TARGET glob)
    rows.extend(iter_glob_surfaces(
        repo_root=repo_root,
        glob_pattern="architecture/modules/*.yaml",
        as_of=as_of,
        cohort="7D",
        authority_marker="YES",
    ))

    # C7-E: docs/operations/INDEX.md + current_*.md (git surfaces)
    rows.extend(iter_git_surfaces(
        repo_root=repo_root,
        path_patterns=["docs/operations/INDEX.md"],
        as_of=as_of,
        cohort="7E",
        authority_marker="NO",  # operations class, not authority
    ))
    rows.extend(iter_git_surfaces(
        repo_root=repo_root,
        path_patterns=["docs/operations/current_*.md"],
        as_of=as_of,
        cohort="7E",
        authority_marker="NO",
    ))

    # C7-F: docs/operations/known_gaps.md
    rows.extend(iter_git_surfaces(
        repo_root=repo_root,
        path_patterns=["docs/operations/known_gaps.md"],
        as_of=as_of,
        cohort="7F",
        authority_marker="NO",
    ))

    # C7-G: docs/operations/packet_scope_protocol.md
    rows.extend(iter_git_surfaces(
        repo_root=repo_root,
        path_patterns=["docs/operations/packet_scope_protocol.md"],
        as_of=as_of,
        cohort="7G",
        authority_marker="NO",
    ))

    return rows


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate authority inventory v2 with Cohort 7 extension"
    )
    p.add_argument(
        "--repo-root", type=Path, default=Path("."),
        help="Zeus repo root (default: cwd)"
    )
    p.add_argument(
        "--output", type=Path, required=True,
        help="Output path for INVENTORY.md"
    )
    p.add_argument(
        "--include-v1", action="store_true", default=False,
        help="Emit v1 rows (re-scored) alongside Cohort 7 [deferred; not implemented in P9.1]"
    )
    p.add_argument(
        "--cohort7-only", action="store_true", default=True,
        help="Emit only Cohort 7 rows (default: True)"
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print output to stdout; do not write file"
    )
    p.add_argument(
        "--as-of", type=str, default=None,
        help="ISO8601 datetime for replay/testing (default: now UTC)"
    )
    return p


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run(repo_root: Path, output: Path, as_of: datetime, dry_run: bool, include_v1: bool) -> None:
    if include_v1:
        print(
            "[authority_inventory_v2] WARNING: --include-v1 is not yet implemented in P9.1. "
            "Only Cohort 7 rows will be emitted. See SCAFFOLD.md for rationale.",
            file=sys.stderr
        )

    # Load hit sets (conservative stubs; never inflate drift)
    ref_hits = load_reference_replacement_hits(repo_root)
    inv_hits = load_invariant_failure_hits(repo_root)

    # Collect Cohort 7 rows
    rows = collect_cohort7_rows(repo_root, as_of)

    # Score each row
    for row in rows:
        score, verdict = compute_drift_score(row, ref_hits, inv_hits)
        row.drift_score = score
        row.verdict = verdict

    # Build output text
    now_str = as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    header_lines = [
        "# Authority Docs Inventory v2",
        "",
        f"Generated: {now_str}",
        f"Repo root: {repo_root.resolve()}",
        f"Cohort 7 surfaces: {len(rows)}",
        "",
        "**NOTE — P9.1 implementer gap:** invariant_failure_hits returns empty set.",
        "The 0.1 invariant weight contributes 0 for all rows (conservative).",
        "See SCAFFOLD.md §3 C2 for resolution options.",
        "",
        "**Deviation:** --include-v1 not implemented in P9.1; v1 inventory remains at",
        "docs/operations/task_2026-05-15_runtime_improvement_engineering_package/",
        "00_evidence/AUTHORITY_DOCS_INVENTORY.md",
        "",
    ]
    table_text = format_inventory_table(rows)
    output_text = "\n".join(header_lines) + "\n" + table_text + "\n"

    if dry_run:
        print(output_text)
        return

    # Write output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(output_text, encoding="utf-8")
    print(f"[authority_inventory_v2] Wrote {len(rows)} rows to {output}", file=sys.stderr)


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    if not repo_root.is_dir():
        print(f"[authority_inventory_v2] ERROR: --repo-root '{repo_root}' is not a directory", file=sys.stderr)
        sys.exit(1)

    if args.as_of:
        try:
            as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
            # Normalize to naive UTC for internal comparisons
            if as_of.tzinfo is not None:
                as_of = as_of.replace(tzinfo=None)
        except ValueError as e:
            print(f"[authority_inventory_v2] ERROR: invalid --as-of value: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        as_of = datetime.utcnow()

    _run(
        repo_root=repo_root,
        output=args.output.resolve(),
        as_of=as_of,
        dry_run=args.dry_run,
        include_v1=args.include_v1,
    )


if __name__ == "__main__":
    main()
