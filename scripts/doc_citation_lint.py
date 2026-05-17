#!/usr/bin/env python3
# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_docs_taxonomy_design/SCAFFOLD.md §4 FM-01/FM-04
#                  + EXECUTION_PLAN.md §2 W2 + D2 contract (locked sha8 format)
"""doc_citation_lint.py — Citation-rot detector for Zeus docs.

Scans markdown files and other specified files (per D6 extension: .md, .py, .yaml, .yml, .json)
for `<!-- cite: path:LINE sha=XXXXXXXX -->` markers, validates each marker against
the live repo, and fails with a non-zero exit code if any citation is stale.

Citation marker format (D2 contract, locked):
    <!-- cite: path/to/file.py:42 sha=abc12345 -->
    <prose that references this location>

Rules enforced:
  1. Marker must appear on its OWN line directly above the line it annotates.
  2. Referenced file must exist in the repo.
  3. Referenced line number must be within the file's line count.
  4. sha8 must match `git show HEAD:<path> | sha256sum | cut -c1-8`.

Usage:
    python scripts/doc_citation_lint.py [path ...]
    python scripts/doc_citation_lint.py docs/authority/ architecture/ AGENTS.md REVIEW.md

Returns exit code 0 if all citations pass; non-zero if any fail.

Enumeration regex for bare path:line cites (used by retro-cite pass):
    [a-zA-Z][a-zA-Z0-9_./\\-]+\\.(?:py|yaml|yml|md|json):\\d+

Excluded patterns (NOT citations):
  - Markdown headings (lines starting with #)
  - §5 anchor lists (paths without :line suffix)
  - The citation markers themselves (<!-- cite: ... -->)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterator, NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent

# D2 marker format: <!-- cite: path:LINE sha=XXXXXXXX -->
# The sha8 is 8 hex chars; the path may contain / . _ -
MARKER_RE = re.compile(
    r"<!--\s*cite:\s*(?P<path>[a-zA-Z][a-zA-Z0-9_./\-]+):(?P<line>\d+)\s+sha=(?P<sha>[0-9a-f]{8})\s*-->"
)

# Bare path:line pattern for retro-cite enumeration (tightened per CRITIC_ROUND_3 followup #2)
# Matches: topology_doctor.py:11  architecture/naming_conventions.yaml:140  docs/authority/AGENTS.md:10
# Does NOT match: http://foo:80  key: value  § anchors without line
BARE_CITE_RE = re.compile(
    r"(?<![:/\w])([a-zA-Z][a-zA-Z0-9_./\-]+\.(?:py|yaml|yml|md|json)):(\d+(?:-\d+)?)"
)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class CitationError(NamedTuple):
    file: Path
    marker_line: int  # 1-indexed line of the <!-- cite: ... --> marker
    path: str
    line: int
    sha: str
    kind: str  # "missing_file" | "stale_sha" | "line_out_of_range" | "malformed" | "placement"
    detail: str


class Citation(NamedTuple):
    file: Path
    marker_line: int
    path: str
    line: int
    sha: str


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def compute_sha8(repo_root: Path, file_path: str) -> str | None:
    """Compute git show HEAD:<path> | sha256sum | cut -c1-8.

    Returns None if the file is not tracked by git.
    Re-fetches from HEAD every call — never cached — to avoid mid-pass drift.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None

    import hashlib

    digest = hashlib.sha256(blob).hexdigest()
    return digest[:8]


def count_lines(repo_root: Path, file_path: str) -> int | None:
    """Return line count of file at HEAD, or None if not tracked."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            cwd=repo_root,
            capture_output=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None
    return blob.count(b"\n") + (1 if blob and not blob.endswith(b"\n") else 0)


def check_marker_placement(file: Path, lines: list[str], marker_line_idx: int) -> CitationError | None:
    """Check D2 placement contract for a marker at lines[marker_line_idx] (0-indexed).

    Contract:
      1. Marker must occupy its OWN line — no other non-whitespace content on the same line.
      2. The next non-blank line after the marker must exist (i.e., the marker is not at EOF
         with nothing following it), ensuring the marker binds to a claim line.

    Returns CitationError with kind="placement" if either rule is violated, else None.
    Note: the Citation object is not yet available when this is called; the caller
    synthesises a minimal CitationError using the raw regex match fields.
    """
    raw_line = lines[marker_line_idx]
    m = MARKER_RE.search(raw_line)
    if m is None:
        return None  # caller should not call this if no match

    marker_line_1indexed = marker_line_idx + 1  # for display

    # Rule 1: marker must be the sole non-whitespace content on its line.
    # Strip the marker text itself and check what remains.
    without_marker = raw_line[:m.start()] + raw_line[m.end():]
    if without_marker.strip():
        return CitationError(
            file=file,
            marker_line=marker_line_1indexed,
            path=m.group("path"),
            line=int(m.group("line")),
            sha=m.group("sha"),
            kind="placement",
            detail=(
                f"marker must occupy its own line (no other non-whitespace content); "
                f"found extra content: {without_marker.strip()!r}"
            ),
        )

    # Rule 2: there must be at least one non-blank line following the marker.
    has_claim_line = False
    for j in range(marker_line_idx + 1, len(lines)):
        if lines[j].strip():
            has_claim_line = True
            break
    if not has_claim_line:
        return CitationError(
            file=file,
            marker_line=marker_line_1indexed,
            path=m.group("path"),
            line=int(m.group("line")),
            sha=m.group("sha"),
            kind="placement",
            detail=(
                "marker must be followed by at least one non-blank line (the cited claim); "
                "marker is at end of file or followed only by blank lines"
            ),
        )

    return None


def enumerate_markers(file: Path) -> Iterator[Citation]:
    """Yield all <!-- cite: ... --> markers found in file."""
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, start=1):
        m = MARKER_RE.search(line)
        if m:
            yield Citation(
                file=file,
                marker_line=i,
                path=m.group("path"),
                line=int(m.group("line")),
                sha=m.group("sha"),
            )


def validate_citation(cite: Citation, repo_root: Path) -> CitationError | None:
    """Return CitationError if the citation is stale; None if it passes."""
    # 1. File must exist at HEAD
    actual_sha = compute_sha8(repo_root, cite.path)
    if actual_sha is None:
        return CitationError(
            file=cite.file,
            marker_line=cite.marker_line,
            path=cite.path,
            line=cite.line,
            sha=cite.sha,
            kind="missing_file",
            detail=f"'{cite.path}' not found in git HEAD",
        )

    # 2. sha8 must match
    if actual_sha != cite.sha:
        return CitationError(
            file=cite.file,
            marker_line=cite.marker_line,
            path=cite.path,
            line=cite.line,
            sha=cite.sha,
            kind="stale_sha",
            detail=f"sha mismatch: stored={cite.sha} actual={actual_sha}",
        )

    # 3. Line number must be within file bounds
    n_lines = count_lines(repo_root, cite.path)
    if n_lines is not None and cite.line > n_lines:
        return CitationError(
            file=cite.file,
            marker_line=cite.marker_line,
            path=cite.path,
            line=cite.line,
            sha=cite.sha,
            kind="line_out_of_range",
            detail=f"line {cite.line} exceeds file length {n_lines}",
        )

    return None


def collect_files(paths: list[str], repo_root: Path) -> list[Path]:
    """Expand paths (files or directories) into a sorted list of scanned files.

    Per D6, scans .md, .py, .yaml, .yml, and .json files.
    """
    allowed_exts = {".md", ".py", ".yaml", ".yml", ".json"}
    result: list[Path] = []
    for p in paths:
        target = Path(p) if Path(p).is_absolute() else repo_root / p
        if target.is_file() and target.suffix in allowed_exts:
            result.append(target)
        elif target.is_dir():
            for ext in allowed_exts:
                result.extend(target.rglob(f"*{ext}"))
        else:
            # Silently skip non-allowed files (e.g. AGENTS.md passed as bare name, but valid)
            # but still try absolute resolution
            abs_target = repo_root / p
            if abs_target.is_file() and abs_target.suffix in allowed_exts:
                result.append(abs_target)
    return sorted(set(result))


# ---------------------------------------------------------------------------
# Retro-cite helpers (used by the W2 retro-cite pass, not by lint itself)
# ---------------------------------------------------------------------------


def enumerate_bare_cites(file: Path) -> list[tuple[int, str, str, str]]:
    """Find bare path:line cites in a file NOT already wrapped in a cite marker.

    Returns list of (line_number, full_match, path, line_spec) tuples.
    Excludes: lines starting with '#' (headings), existing cite markers.
    """
    results = []
    lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Skip markdown headings
        if stripped.startswith("#"):
            continue
        # Skip existing cite markers
        if "<!-- cite:" in line:
            continue
        for m in BARE_CITE_RE.finditer(line):
            results.append((i, m.group(0), m.group(1), m.group(2)))
    return results


def build_retro_cite_marker(path: str, line_spec: str, repo_root: Path) -> str | None:
    """Build a <!-- cite: path:LINE sha=XXXXXXXX --> marker string.

    line_spec may be "42" or "42-55" — marker always uses the start line.
    Returns None if sha8 cannot be computed (file not in HEAD).
    """
    start_line = int(line_spec.split("-")[0])
    sha = compute_sha8(repo_root, path)
    if sha is None:
        return None
    return f"<!-- cite: {path}:{start_line} sha={sha} -->"


# ---------------------------------------------------------------------------
# Main lint runner
# ---------------------------------------------------------------------------


def lint_files(paths: list[str], repo_root: Path) -> list[CitationError]:
    """Lint all files in paths. Return list of errors.

    Checks both sha/file validity (rules 2-4) and placement (rule 1: marker on own line,
    next non-blank line is the cited claim).
    """
    files = collect_files(paths, repo_root)
    errors: list[CitationError] = []
    for f in files:
        file_lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(file_lines):
            if not MARKER_RE.search(line):
                continue
            # Placement check (D2 rule 1)
            placement_err = check_marker_placement(f, file_lines, i)
            if placement_err:
                errors.append(placement_err)
            # sha/file/line validation (D2 rules 2-4)
            m = MARKER_RE.search(line)
            if m:
                cite = Citation(
                    file=f,
                    marker_line=i + 1,
                    path=m.group("path"),
                    line=int(m.group("line")),
                    sha=m.group("sha"),
                )
                err = validate_citation(cite, repo_root)
                if err:
                    errors.append(err)
    return errors


def main(argv: list[str] | None = None) -> int:
    """Main entry point. Returns exit code."""
    parser = argparse.ArgumentParser(
        description="Validate <!-- cite: path:LINE sha=SHA8 --> markers in markdown files."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["docs/authority/", "architecture/", "AGENTS.md", "REVIEW.md"],
        help="Files or directories to scan (default: docs/authority/ architecture/ AGENTS.md REVIEW.md)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repo root directory (default: parent of scripts/)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="Strict mode (currently identical to default; reserved for future tighter rules)",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root) if args.repo_root else REPO_ROOT

    errors = lint_files(args.paths, repo_root)

    if not errors:
        print("doc_citation_lint: all citations valid.")
        return 0

    for err in errors:
        print(
            f"CITE_ERROR [{err.kind}] {err.file}:{err.marker_line} "
            f"-> {err.path}:{err.line} sha={err.sha}: {err.detail}"
        )

    print(f"\ndoc_citation_lint: {len(errors)} error(s). Fix stale citations before merging.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
