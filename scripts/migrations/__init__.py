# Lifecycle: created=2026-05-16; last_reviewed=2026-05-18; last_reused=2026-05-18
# Purpose: scripts.migrations package — apply_migrations() runner framework.
#   Each migration module at scripts/migrations/YYYYMM_*.py exposes def up(conn).
#   Ledger table _migrations_applied tracks applied migrations in the target DB.
# Reuse: Run through scripts/migrations/__main__.py or direct tests with db_identity.
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


def _bootstrap_applied_for_db(db_identity: str | None) -> set[str]:
    """Return pre-ledger migrations that are valid for this DB identity."""
    if db_identity in (None, "trade"):
        return set(_BOOTSTRAP_APPLIED)
    return set()


def _ensure_ledger(conn: sqlite3.Connection, *, db_identity: str | None = None) -> None:
    """Create _migrations_applied and seed bootstrap entries on first create."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS _migrations_applied
           (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"""
    )
    # Seed pre-ledger migrations only when the table is brand-new (empty).
    existing = {r[0] for r in conn.execute("SELECT name FROM _migrations_applied")}
    if not existing:
        now = datetime.now(UTC).isoformat()
        for name in sorted(_bootstrap_applied_for_db(db_identity)):
            conn.execute(
                "INSERT OR IGNORE INTO _migrations_applied VALUES (?,?)", (name, now)
            )
    conn.commit()


def _get_pending(
    conn: sqlite3.Connection,
    *,
    ensure: bool = True,
    db_identity: str | None = None,
) -> list[Path]:
    """Return migration scripts not yet recorded in the ledger.

    Args:
        ensure: if True (default), call _ensure_ledger() to create and seed the
            ledger table before querying it.  Pass False for dry-run inspection
            where committing bootstrap rows would violate the advertised no-write
            contract.  When False the ledger must already exist or the SELECT
            will raise OperationalError; callers must guard accordingly.
    """
    if ensure:
        _ensure_ledger(conn, db_identity=db_identity)
    applied: set[str] = set()
    try:
        applied = {r[0] for r in conn.execute("SELECT name FROM _migrations_applied")}
    except Exception:
        # Ledger absent and ensure=False — treat all scripts as pending.
        pass
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


def _load_migration_module(script: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(script.stem, script)
    mod = types.ModuleType(script.stem)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _normalize_db_identity(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "trade": "trade",
        "trades": "trade",
        "zeus-trades": "trade",
        "world": "world",
        "zeus-world": "world",
        "forecast": "forecasts",
        "forecasts": "forecasts",
        "zeus-forecasts": "forecasts",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown db_identity={value!r}") from exc


def _migration_target_db(mod: types.ModuleType) -> str | None:
    target = getattr(mod, "TARGET_DB", None)
    if target is None:
        return None
    return _normalize_db_identity(str(target))


def target_db_for_migration(name: str) -> str | None:
    """Return TARGET_DB for a migration stem, or None for legacy modules."""
    script = MIGRATIONS_DIR / f"{name}.py"
    if not script.exists():
        raise FileNotFoundError(f"migration not found: {name}")
    _check_header(script)
    return _migration_target_db(_load_migration_module(script))


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    target: str | None = None,
    db_identity: str | None = None,
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
        db_identity: canonical identity of ``conn``: trade, world, or
            forecasts. Required for migrations declaring TARGET_DB and used to
            skip other DBs when applying all pending migrations.

    Returns:
        List of migration names that were applied (or would be applied).
    """
    resolved_db_identity = _normalize_db_identity(db_identity)
    applied_names: list[str] = []
    # dry_run=True must not commit any rows — pass ensure=False so _ensure_ledger
    # is skipped and no bootstrap rows land in the DB.  Non-dry-run always
    # initialises the ledger via ensure=True (the default).
    pending = _get_pending(conn, ensure=not dry_run, db_identity=resolved_db_identity)
    plan: list[tuple[Path, types.ModuleType]] = []
    for script in pending:
        if target and script.stem != target:
            continue
        # F30: enforce header presence before any work.
        _check_header(script)
        mod = _load_migration_module(script)
        migration_target = _migration_target_db(mod)
        if migration_target is not None and resolved_db_identity is None:
            raise RuntimeError(
                f"{script.stem} declares TARGET_DB={migration_target!r}; "
                "apply_migrations caller must pass db_identity to prevent "
                "cross-DB migration writes."
            )
        if migration_target is None and resolved_db_identity is not None:
            raise RuntimeError(
                f"{script.stem} is missing TARGET_DB metadata; refusing to "
                f"apply under db_identity={resolved_db_identity!r}."
            )
        if migration_target is not None and migration_target != resolved_db_identity:
            if target:
                raise RuntimeError(
                    f"{script.stem} targets {migration_target!r}, not "
                    f"{resolved_db_identity!r}."
                )
            continue
        plan.append((script, mod))
    for script, mod in plan:
        if dry_run:
            print(f"[dry-run] would apply: {script.stem}")
            applied_names.append(script.stem)
            continue
        mod.up(conn)
        conn.execute(
            "INSERT INTO _migrations_applied VALUES (?,?)",
            (script.stem, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        print(f"applied: {script.stem}")
        applied_names.append(script.stem)
    return applied_names
