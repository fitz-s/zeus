# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: docs/operations/task_2026-05-14_k1_followups/PLAN.md §1.1 (REV 4)
"""Zeus table ownership registry loader.

Canonical source: architecture/db_table_ownership.yaml
Load-failure (YAML parse error, missing required field, duplicate (name, db),
unknown enum value) raises at MODULE IMPORT TIME, propagating to FATAL at
daemon boot per INV-05 fail-closed. No fallback; no partial-load mode.

Public API (5 functions per PLAN §1.1):
  owner(table_name)             → DBIdentity (unique; forecasts or world — raises if ambiguous)
  tables_for(db)                → frozenset[str]
  tables_for_class(schema_class) → frozenset[str]
  is_forecast_class(table_name) → bool
  assert_db_matches_registry(conn, db_identity) → None  (A4 antibody)

INV-37 carve-out note: get_forecasts_connection_with_world uses ATTACH+SAVEPOINT to
write both forecasts.observations and world.data_coverage in one atomic transaction.
INV-37 forbids two-independent-connection writes across DBs; ATTACH-mediated SAVEPOINT
is the sanctioned cross-DB atomicity pattern and is NOT forbidden.
"""
from __future__ import annotations

import sqlite3
from enum import Enum
from pathlib import Path
from typing import NamedTuple

import yaml

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

VALID_DB_VALUES = frozenset({"world", "forecasts", "trade", "risk_state", "backtest"})
VALID_SCHEMA_CLASS_VALUES = frozenset({
    "forecast_class", "world_class", "trade_class", "risk_class",
    "backtest_class", "legacy_archived",
})


class DBIdentity(str, Enum):
    WORLD = "world"
    FORECASTS = "forecasts"
    TRADE = "trade"
    RISK_STATE = "risk_state"
    BACKTEST = "backtest"


class SchemaClass(str, Enum):
    FORECAST_CLASS = "forecast_class"
    WORLD_CLASS = "world_class"
    TRADE_CLASS = "trade_class"
    RISK_CLASS = "risk_class"
    BACKTEST_CLASS = "backtest_class"
    LEGACY_ARCHIVED = "legacy_archived"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class ColumnSpec(NamedTuple):
    name: str
    type: str
    nullable: bool


class TableEntry(NamedTuple):
    name: str
    db: DBIdentity
    schema_class: SchemaClass
    schema_version_owner: str
    created_by: str
    pk_col: str | None
    required_columns: list[ColumnSpec]
    notes: str


# ---------------------------------------------------------------------------
# RegistryAssertionError (INV-05 fatal per PLAN §1.1)
# ---------------------------------------------------------------------------

class RegistryAssertionError(RuntimeError):
    """Raised by assert_db_matches_registry on table-set or column-shape mismatch.

    Per INV-05 fail-closed semantics: daemon refuses to boot. No advisory mode.
    """


# ---------------------------------------------------------------------------
# Registry load
# ---------------------------------------------------------------------------

_REGISTRY_PATH = Path(__file__).parent.parent.parent / "architecture" / "db_table_ownership.yaml"

REQUIRED_ENTRY_FIELDS = frozenset({
    "name", "db", "schema_class", "schema_version_owner", "created_by", "pk_col",
})


def _parse_column_spec(raw: object, table_name: str) -> ColumnSpec:
    """Parse a required_columns dict entry into ColumnSpec."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"registry: table '{table_name}' required_columns entry must be a dict, got {type(raw)}"
        )
    for field in ("name", "type", "nullable"):
        if field not in raw:
            raise ValueError(
                f"registry: table '{table_name}' required_columns entry missing field '{field}'"
            )
    return ColumnSpec(
        name=str(raw["name"]),
        type=str(raw["type"]),
        nullable=bool(raw["nullable"]),
    )


def _load_registry() -> dict[tuple[str, DBIdentity], TableEntry]:
    """Load and validate architecture/db_table_ownership.yaml.

    Raises ValueError (subclass of Exception) at import time on any
    structural violation. Propagates to FATAL per INV-05.
    """
    try:
        raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(
            f"table registry YAML not found: {_REGISTRY_PATH}. "
            "This is a load-fatal error per INV-05."
        ) from None
    except yaml.YAMLError as exc:
        raise ValueError(f"table registry YAML parse error: {exc}") from exc

    if not isinstance(raw, dict) or "tables" not in raw:
        raise ValueError("table registry YAML missing top-level 'tables' key")

    table_list = raw["tables"]
    if not isinstance(table_list, list):
        raise ValueError("table registry YAML 'tables' must be a list")

    result: dict[tuple[str, DBIdentity], TableEntry] = {}

    for i, entry in enumerate(table_list):
        if not isinstance(entry, dict):
            raise ValueError(f"table registry entry #{i} must be a dict, got {type(entry)}")

        missing = REQUIRED_ENTRY_FIELDS - entry.keys()
        if missing:
            raise ValueError(
                f"table registry entry #{i} missing required fields: {sorted(missing)}"
            )

        name = str(entry["name"])
        db_raw = str(entry["db"])
        sc_raw = str(entry["schema_class"])

        if db_raw not in VALID_DB_VALUES:
            raise ValueError(
                f"table registry entry '{name}': unknown db value '{db_raw}'. "
                f"Valid: {sorted(VALID_DB_VALUES)}"
            )
        if sc_raw not in VALID_SCHEMA_CLASS_VALUES:
            raise ValueError(
                f"table registry entry '{name}': unknown schema_class '{sc_raw}'. "
                f"Valid: {sorted(VALID_SCHEMA_CLASS_VALUES)}"
            )

        db_identity = DBIdentity(db_raw)
        schema_class = SchemaClass(sc_raw)
        key = (name, db_identity)

        if key in result:
            raise ValueError(
                f"table registry: duplicate (name, db) pair: '{name}' on '{db_raw}'. "
                "Each (table_name, db) pair must be unique."
            )

        raw_cols = entry.get("required_columns") or []
        if not isinstance(raw_cols, list):
            raise ValueError(
                f"table registry entry '{name}': required_columns must be a list, "
                f"got {type(raw_cols)}"
            )
        required_columns = [_parse_column_spec(c, name) for c in raw_cols]

        result[key] = TableEntry(
            name=name,
            db=db_identity,
            schema_class=schema_class,
            schema_version_owner=str(entry["schema_version_owner"]),
            created_by=str(entry["created_by"]),
            pk_col=entry["pk_col"],  # may be None
            required_columns=required_columns,
            notes=str(entry.get("notes", "")),
        )

    return result


# Load at module import time — any error here propagates FATAL per INV-05.
_REGISTRY: dict[tuple[str, DBIdentity], TableEntry] = _load_registry()


# ---------------------------------------------------------------------------
# Public API (5 functions per PLAN §1.1)
# ---------------------------------------------------------------------------

def owner(table_name: str) -> DBIdentity:
    """Return the authoritative DBIdentity for a table.

    Raises KeyError if the table is not in the registry.
    Raises ValueError if the table appears on multiple DBs with non-legacy_archived
    entries (ambiguous ownership). Legacy-archived entries are skipped.
    """
    canonical = [
        entry for (name, _), entry in _REGISTRY.items()
        if name == table_name and entry.schema_class != SchemaClass.LEGACY_ARCHIVED
    ]
    if not canonical:
        raise KeyError(
            f"table_registry.owner: '{table_name}' not found in registry "
            "(or only has legacy_archived entries). "
            "Add a non-archived entry to architecture/db_table_ownership.yaml."
        )
    if len(canonical) > 1:
        dbs = [e.db.value for e in canonical]
        raise ValueError(
            f"table_registry.owner: '{table_name}' has ambiguous ownership: {dbs}. "
            "Each table must have at most one non-legacy_archived entry."
        )
    return canonical[0].db


def tables_for(db: DBIdentity) -> frozenset[str]:
    """Return all non-legacy_archived table names owned by db.

    Legacy-archived entries are excluded (they are ghost copies on the wrong DB,
    not owned by that DB for purposes of set-equality checks).
    """
    return frozenset(
        name
        for (name, db_id), entry in _REGISTRY.items()
        if db_id == db and entry.schema_class != SchemaClass.LEGACY_ARCHIVED
    )


def tables_for_class(schema_class: SchemaClass) -> frozenset[str]:
    """Return all table names with the given schema_class.

    For LEGACY_ARCHIVED, returns only the table names (not the underlying
    authoritative name — those are under their true schema_class entries).
    """
    return frozenset(
        name
        for (name, _), entry in _REGISTRY.items()
        if entry.schema_class == schema_class
    )


def is_forecast_class(table_name: str) -> bool:
    """Return True iff table_name has schema_class=forecast_class (non-legacy) in the registry."""
    for (name, _), entry in _REGISTRY.items():
        if name == table_name and entry.schema_class == SchemaClass.FORECAST_CLASS:
            return True
    return False


def required_columns_for(table_name: str) -> list[ColumnSpec] | None:
    """Return the required_columns list for table_name (non-legacy_archived entry).

    Returns None if table_name has no entry or has no required_columns declared.
    Used internally by assert_db_matches_registry and by callers needing column introspection.
    """
    for (name, _), entry in _REGISTRY.items():
        if name == table_name and entry.schema_class != SchemaClass.LEGACY_ARCHIVED:
            return entry.required_columns if entry.required_columns else None
    return None


def assert_db_matches_registry(conn: sqlite3.Connection, db_identity: DBIdentity) -> None:
    """A4 antibody: assert the live DB matches the registry (table-set + column-shape).

    Two checks per PLAN §1.1:
    1. TABLE-SET EQUALITY: sqlite_master tables == tables_for(db_identity).
       Both missing-from-disk (registry declared but not created) AND
       extra-on-disk (ghost table not in registry) are hard failures.
       legacy_archived entries are excluded from both sides.
    2. COLUMN-SHAPE SUBSET: for every table with required_columns declared,
       every (name, type, nullable) tuple must be present in PRAGMA table_info.
       Extra columns on disk are permitted (subset semantics per PLAN §1.1 #2).

    Raises RegistryAssertionError on any mismatch. No advisory mode — FATAL per INV-05.

    Args:
        conn: sqlite3.Connection to the DB being checked.
        db_identity: which DB this connection points to (WORLD or FORECASTS).
    """
    # Read live sqlite_master — exclude internal sqlite_* tables.
    live_tables: frozenset[str] = frozenset(
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    )

    # Registry-declared tables for this DB (non-legacy_archived only).
    registry_tables = tables_for(db_identity)

    # Check 1: set equality.
    missing_from_disk = registry_tables - live_tables
    extra_on_disk = live_tables - registry_tables

    if missing_from_disk or extra_on_disk:
        msg_parts = [
            f"assert_db_matches_registry FAILED for db={db_identity.value}."
        ]
        if missing_from_disk:
            msg_parts.append(
                f"  Registry declares these tables but they are missing from disk "
                f"(incomplete migration or stale registry): {sorted(missing_from_disk)}"
            )
        if extra_on_disk:
            msg_parts.append(
                f"  Disk has these tables not declared in registry "
                f"(ghost table or missing registry entry): {sorted(extra_on_disk)}"
            )
        msg_parts.append(
            "  Fix: update architecture/db_table_ownership.yaml to match the deployed schema, "
            "or run the relevant migration script."
        )
        raise RegistryAssertionError("\n".join(msg_parts))

    # Check 2: column-shape subset for tables with required_columns declared.
    for table_name in registry_tables:
        cols = required_columns_for(table_name)
        if not cols:
            continue

        pragma_rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        pragma_map: dict[str, tuple[str, bool]] = {
            row[1]: (row[2].upper(), row[3] == 0)  # name → (TYPE, nullable)
            for row in pragma_rows
        }

        for col_spec in cols:
            if col_spec.name not in pragma_map:
                raise RegistryAssertionError(
                    f"assert_db_matches_registry: table '{table_name}' on {db_identity.value}.db "
                    f"missing required column '{col_spec.name}' "
                    f"(registry declares type={col_spec.type}, nullable={col_spec.nullable})."
                )
            live_type, live_nullable = pragma_map[col_spec.name]
            if live_type != col_spec.type.upper():
                raise RegistryAssertionError(
                    f"assert_db_matches_registry: table '{table_name}' on {db_identity.value}.db "
                    f"column '{col_spec.name}' has type '{live_type}' but registry requires "
                    f"'{col_spec.type.upper()}'."
                )
            if live_nullable != col_spec.nullable:
                raise RegistryAssertionError(
                    f"assert_db_matches_registry: table '{table_name}' on {db_identity.value}.db "
                    f"column '{col_spec.name}' nullable={live_nullable} but registry requires "
                    f"nullable={col_spec.nullable}."
                )
