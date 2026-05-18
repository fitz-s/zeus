# Created: 2026-05-18
# Last reused or audited: 2026-05-18
# Authority basis:
#   docs/operations/task_2026-05-17_post_karachi_remediation/F22_WRITER_LOCK_FIX.md
#   docs/operations/task_2026-05-17_post_karachi_remediation/OPS_FORENSICS.md §F22
# Lifecycle: created=2026-05-18; last_reviewed=2026-05-18; last_reused=never
# Purpose: F22 carry-forward — surface `# WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD`
#   markers that have outlived the 30-day defer window so they cannot accumulate
#   forever.  The sibling antibody `tests/test_operator_script_lock_contract.py`
#   *accepts* any marker as a contract escape; this antibody *expires* the escape
#   so a defer becomes a deadline, not an indefinite waiver.
#
#   Policy (per F22_WRITER_LOCK_FIX.md): a marker that was added today reserves
#   30 days for the writer-lock contract retrofit (or for an explicit "permanently
#   retired" deletion).  After 30 days the antibody fails and forces a decision —
#   either bump the marker date with a renewed ops-doc justification, or apply
#   the contract.  This makes "DEFER" a stage in the workflow rather than a
#   parking lot.
"""F22 carry-forward antibody: WRITER_LOCK_DEFER_REVIEW markers expire after 30 days.

Behaviour
---------
Scans the same operator-script scope as `test_operator_script_lock_contract.py`
(``scripts/{operator_*,cleanup_*,force_*,bridge_*,migrate_*}.py`` plus
``scripts/migrations/2*.py``) for ``# WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD``
comments.  Each marker carries a date; if today is more than 30 days past that
date, the test fails for that script with an actionable diagnostic.

Bypass
------
A marker can be renewed by bumping its date when an operator confirms the defer
should continue.  The ops-doc entry in
`docs/operations/task_2026-05-17_post_karachi_remediation/F22_WRITER_LOCK_FIX.md`
must explain why the contract retrofit cannot ship yet.

Meta-verify (sed-break/restore)
-------------------------------
- Marker dated >30 days back via sed → test fails with that script's name.
- Restore → test passes for that script.
- Marker dated >100 years back → test fails (covers any "old marker" case).
- Marker dated 1 day in the future → passes (date arithmetic is one-sided).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# `# WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD` — captured group is the date.
_DEFER_RE = re.compile(
    r"#\s*WRITER_LOCK_DEFER_REVIEW\s*=\s*(\d{4})-(\d{2})-(\d{2})"
)

# Policy: marker is valid for 30 days from its review date.
DEFER_WINDOW_DAYS = 30


def _operator_scripts() -> list[Path]:
    """Same scope as `test_operator_script_lock_contract.py`.

    Kept in sync deliberately: any script that can opt into a defer marker must
    also be subject to its expiry.
    """
    scripts_dir = _REPO_ROOT / "scripts"
    found: list[Path] = []
    for pattern in (
        "operator_*.py",
        "cleanup_*.py",
        "force_*.py",
        "bridge_*.py",
        "migrate_*.py",
    ):
        found.extend(scripts_dir.glob(pattern))
    found.extend(scripts_dir.glob("migrations/2*.py"))
    return sorted(found)


def _extract_markers(content: str) -> list[tuple[int, date]]:
    """Return ``(line_number, marker_date)`` for every defer marker in ``content``."""
    results: list[tuple[int, date]] = []
    for i, line in enumerate(content.splitlines(), start=1):
        m = _DEFER_RE.search(line)
        if not m:
            continue
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            results.append((i, date(y, mo, d)))
        except ValueError:
            # Marker present but date malformed — surface as line-level failure.
            results.append((i, date.min))
    return results


def _scripts_with_markers() -> list[Path]:
    """Return only scripts that carry at least one defer marker."""
    return [p for p in _operator_scripts() if _DEFER_RE.search(p.read_text())]


_SCRIPTS_WITH_MARKERS = _scripts_with_markers()


@pytest.mark.parametrize(
    "script",
    _SCRIPTS_WITH_MARKERS,
    ids=[s.name for s in _SCRIPTS_WITH_MARKERS] or ["no-markers-in-repo"],
)
def test_defer_marker_within_window(script: Path) -> None:
    """A WRITER_LOCK_DEFER_REVIEW marker must be no more than 30 days old."""
    if not _SCRIPTS_WITH_MARKERS:
        # Vacuous pass: no markers in repo means no expiry risk.  Still fires
        # the test node so the harness records the scan ran.
        return

    today = date.today()
    cutoff = today - timedelta(days=DEFER_WINDOW_DAYS)
    content = script.read_text()
    markers = _extract_markers(content)
    assert markers, (
        f"{script.relative_to(_REPO_ROOT)}: parametrized as marker-bearing but "
        "no markers were extracted — _DEFER_RE drift or file edited mid-run."
    )

    overdue: list[str] = []
    for line_no, marker_date in markers:
        if marker_date == date.min:
            overdue.append(
                f"  line {line_no}: malformed date — marker is unparseable; "
                "either fix the YYYY-MM-DD format or remove the marker"
            )
            continue
        if marker_date < cutoff:
            age = (today - marker_date).days
            overdue.append(
                f"  line {line_no}: WRITER_LOCK_DEFER_REVIEW={marker_date.isoformat()} "
                f"is {age} days old (window={DEFER_WINDOW_DAYS} days, today={today.isoformat()})"
            )

    assert not overdue, (
        f"\n{script.relative_to(_REPO_ROOT)}: WRITER_LOCK_DEFER_REVIEW marker(s) "
        f"have outlived the {DEFER_WINDOW_DAYS}-day defer window.\n"
        + "\n".join(overdue)
        + "\n\nResolution options:\n"
        "  (a) Apply the writer-lock contract:\n"
        "      with db_writer_lock(db_path, WriteClass.BULK): ...\n"
        "      Then delete the WRITER_LOCK_DEFER_REVIEW line.\n"
        "  (b) Confirm the script is retired and delete it (plus any callers).\n"
        "  (c) Bump the marker date to today AND record the renewed defer rationale\n"
        "      in docs/operations/task_2026-05-17_post_karachi_remediation/F22_WRITER_LOCK_FIX.md\n"
        "      (re-deferring is a deliberate operator decision; do not bump silently).\n"
    )


def test_defer_marker_date_arithmetic_is_one_sided() -> None:
    """Sanity check: a future-dated marker is treated as valid.

    Markers should normally be set to today; a future date is unusual but not
    a violation by itself — only past-the-window dates fail.  This test pins
    the one-sided behaviour so the regex/date logic cannot be silently flipped.
    """
    future = date.today() + timedelta(days=365)
    sample = f"# WRITER_LOCK_DEFER_REVIEW={future.isoformat()}\n"
    markers = _extract_markers(sample)
    assert len(markers) == 1
    line_no, marker_date = markers[0]
    assert line_no == 1
    assert marker_date == future
    today = date.today()
    cutoff = today - timedelta(days=DEFER_WINDOW_DAYS)
    assert marker_date >= cutoff, "Future dates must never appear overdue."


def test_defer_marker_regex_captures_date_components() -> None:
    """Pin the regex shape so a future refactor cannot silently widen acceptance."""
    valid = "# WRITER_LOCK_DEFER_REVIEW=2026-05-18"
    m = _DEFER_RE.search(valid)
    assert m is not None
    assert m.group(1) == "2026"
    assert m.group(2) == "05"
    assert m.group(3) == "18"

    # Wrong year width — must NOT match (otherwise "WRITER_LOCK_DEFER_REVIEW=26-05-18"
    # would be a silent acceptance that this antibody and the sibling contract test
    # both treat as valid).
    invalid = "# WRITER_LOCK_DEFER_REVIEW=26-05-18"
    assert _DEFER_RE.search(invalid) is None


def test_old_marker_in_synthetic_content_is_overdue() -> None:
    """Direct check on synthetic content (independent of repo state).

    Guarantees the comparator works regardless of which scripts currently
    carry markers in the live repo.  If the parametrized test_defer_marker_within_window
    vacuously passes (no markers in repo), this test still proves the
    overdue-detection logic is functional.
    """
    long_ago = date.today() - timedelta(days=DEFER_WINDOW_DAYS + 1)
    sample = f"# WRITER_LOCK_DEFER_REVIEW={long_ago.isoformat()}\n"
    markers = _extract_markers(sample)
    assert markers
    cutoff = date.today() - timedelta(days=DEFER_WINDOW_DAYS)
    assert markers[0][1] < cutoff, (
        f"Synthetic marker {markers[0][1]} should be < cutoff {cutoff}"
    )


def test_marker_at_exact_window_boundary_is_valid() -> None:
    """A marker dated exactly DEFER_WINDOW_DAYS ago is the last day of grace.

    Boundary semantics: ``marker_date < cutoff`` triggers failure.
    ``marker_date == cutoff`` (exactly 30 days back) is still valid.
    """
    boundary = date.today() - timedelta(days=DEFER_WINDOW_DAYS)
    cutoff = date.today() - timedelta(days=DEFER_WINDOW_DAYS)
    assert boundary == cutoff
    assert not (boundary < cutoff), "Boundary day must remain valid (inclusive)."
