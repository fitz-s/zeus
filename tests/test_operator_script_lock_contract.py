# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: docs/operations/task_2026-05-17_post_karachi_remediation/F22_WRITER_LOCK_FIX.md
"""CI antibody: operator-named scripts that open a read-write SQLite connection
must either:
  (a) use the db_writer_lock / register_known_connection / acquire_writer_lock
      contract, OR
  (b) use the ?mode=ro URI (read-only acceptable), OR
  (c) carry a # WRITER_LOCK_DEFER_REVIEW=YYYY-MM-DD marker for explicitly
      deferred cleanup.

Failure means the script could race daemon writes during live trading.

F22 finding (OPS_FORENSICS.md §F22):
  "Total raw read-write sqlite3.connect (no ?mode=ro) sites: 43 scripts.
   Operator-action subset: 12 scripts. Top-5 most dangerous during live trading."
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Pattern: literal sqlite3.connect( call (not a comment, not ?mode=ro)
_CONNECT_RE = re.compile(r"sqlite3\.connect\(")
_LOCK_TOKENS = ("db_writer_lock", "register_known_connection", "acquire_writer_lock")
_RO_MARKER = "?mode=ro"
_DEFER_RE = re.compile(r"#\s*WRITER_LOCK_DEFER_REVIEW\s*=\s*\d{4}-\d{2}-\d{2}")


def _operator_scripts() -> list[Path]:
    """Return operator-named scripts that are in scope for the lock contract.

    Scope: scripts/{operator_*,cleanup_*,force_*,bridge_*,migrate_*}.py
           scripts/migrations/2*.py   (date-prefixed migration files only)

    Excluded from scope (runner/package infrastructure):
           scripts/migrations/__init__.py  — runner core
           scripts/migrations/__main__.py  — CLI entry point (connects on behalf of caller)
    """
    scripts_dir = _REPO_ROOT / "scripts"
    found: list[Path] = []

    # Top-level operator scripts
    for pattern in (
        "operator_*.py",
        "cleanup_*.py",
        "force_*.py",
        "bridge_*.py",
        "migrate_*.py",
    ):
        found.extend(scripts_dir.glob(pattern))

    # Date-prefixed migration files (excludes __init__.py and __main__.py)
    found.extend(scripts_dir.glob("migrations/2*.py"))

    return sorted(found)


def _classify(script: Path) -> str:
    """Return 'ok' or a diagnostic string if the script fails the contract."""
    content = script.read_text()

    # No raw sqlite3.connect — not in scope of this contract
    if not _CONNECT_RE.search(content):
        return "ok"

    # All connects are read-only — acceptable
    if _RO_MARKER in content and content.count("sqlite3.connect(") == content.count(_RO_MARKER):
        return "ok"

    # Has at least one non-ro connect — must have lock OR defer marker
    has_lock = any(tok in content for tok in _LOCK_TOKENS)
    if has_lock:
        return "ok"

    has_defer = bool(_DEFER_RE.search(content))
    if has_defer:
        return "ok"

    # Find the first offending line for the diagnostic
    lines = content.splitlines()
    offending = [
        f"  line {i + 1}: {line.strip()}"
        for i, line in enumerate(lines)
        if "sqlite3.connect(" in line
    ]
    return (
        f"raw read-write sqlite3.connect without writer-lock contract or defer marker.\n"
        + "\n".join(offending)
    )


# ── parametrize so each script gets its own test node ──────────────────────
_SCRIPTS = _operator_scripts()


@pytest.mark.parametrize("script", _SCRIPTS, ids=[s.name for s in _SCRIPTS])
def test_operator_script_writer_lock_contract(script: Path) -> None:
    """Each operator script that opens a read-write SQLite connection must
    acquire a writer-lock, use ?mode=ro, or carry a WRITER_LOCK_DEFER_REVIEW
    marker.  Failure = live-trading race condition risk (F22).
    """
    result = _classify(script)
    assert result == "ok", (
        f"\n{script.relative_to(_REPO_ROOT)}: {result}\n\n"
        "Fix options:\n"
        "  (a) Add: with db_writer_lock(db_path, WriteClass.BULK): ...\n"
        "  (b) Use: sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)\n"
        "  (c) Add: # WRITER_LOCK_DEFER_REVIEW=2026-05-17  (requires ops doc entry)\n"
    )
