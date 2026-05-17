# Lifecycle: created=2026-05-16; last_reviewed=2026-05-17; last_reused=2026-05-17
# Purpose: scripts.migrations package — apply_migrations() runner framework.
#   Each migration module at scripts/migrations/YYYYMM_*.py exposes def up(conn).
#   Ledger table _migrations_applied tracks applied migrations in the target DB.
# Authority: docs/operations/task_2026-05-17_post_karachi_remediation/FIX_SEV1_BUNDLE.md §F23
import importlib.util
import re
import sqlite3
import types
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent

# Pattern that satisfies F30 header drift enforcement.
_LAST_REVIEWED_RE = re.compile(r"last_reviewed=\d{4}-\d{2}-\d{2}")

# Migration that was already applied to production before the ledger existed.
# Seeded into _migrations_applied at table-create time so the runner never
# re-applies it.  Option (a) per §F23 spec.
_BOOTSTRAP_APPLIED = {"202605_add_redeem_operator_required_state"}


def _ensure_ledger(conn: sqlite3.Connection) -> None:
    """Create _migrations_applied and seed bootstrap entries on first create."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _migrations_applied
           (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"""
    )
    # Seed pre-ledger migrations only when the table is brand-new (empty).
    existing = {r[0] for r in conn.execute("SELECT name FROM _migrations_applied")}
    if not existing:
        now = datetime.now(UTC).isoformat()
        for name in sorted(_BOOTSTRAP_APPLIED):
            conn.execute(
                "INSERT OR IGNORE INTO _migrations_applied VALUES (?,?)", (name, now)
            )
    conn.commit()


def _get_pending(conn: sqlite3.Connection) -> list[Path]:
    """Return migration scripts not yet recorded in the ledger."""
    _ensure_ledger(conn)
    applied = {r[0] for r in conn.execute("SELECT name FROM _migrations_applied")}
    scripts = sorted(MIGRATIONS_DIR.glob("2*.py"))
    return [s for s in scripts if s.stem not in applied]


def _check_header(script: Path) -> None:
    """F30: refuse to apply if last_reviewed= header is absent."""
    source = script.read_text()
    if not _LAST_REVIEWED_RE.search(source):
        raise ValueError(
            f"Migration {script.name} is missing a 'last_reviewed=YYYY-MM-DD' "
            "header. Add or update the Lifecycle comment block before applying."
        )


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target: str | None = None,
) -> list[str]:
    """Apply pending migrations from MIGRATIONS_DIR.

    Each migration module must expose ``def up(conn) -> None``.
    Returns list of applied (or would-apply in dry_run) migration names.

    F30: refuses to apply any migration whose source file lacks a
    ``last_reviewed=YYYY-MM-DD`` header.

    Args:
        conn: open SQLite connection to the target database.
        dry_run: if True, print pending migrations but do not apply.
        target: if given, apply only the migration with this stem name.

    Returns:
        List of migration names that were applied (or would be applied).
    """
    applied_names: list[str] = []
    for script in _get_pending(conn):
        if target and script.stem != target:
            continue
        # F30: enforce header presence before any work.
        _check_header(script)
        if dry_run:
            print(f"[dry-run] would apply: {script.stem}")
            applied_names.append(script.stem)
            continue
        spec = importlib.util.spec_from_file_location(script.stem, script)
        mod = types.ModuleType(script.stem)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        mod.up(conn)
        conn.execute(
            "INSERT INTO _migrations_applied VALUES (?,?)",
            (script.stem, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        print(f"applied: {script.stem}")
        applied_names.append(script.stem)
    return applied_names
