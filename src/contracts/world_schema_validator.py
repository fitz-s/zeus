# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Authority basis: docs/operations/task_2026-04-30_two_system_independence/design.md §1 axis 4 + §6 antibody #5
"""World schema validator — boot-time manifest check.

Reads architecture/world_schema_manifest.yaml, runs PRAGMA table_info(...)
for each listed table, and verifies required columns are present.

Called from src/main.py boot path. Phase 2: returns False on mismatch (caller
logs WARN). Phase 3: caller will sys.exit() on False.

Usage:
    from src.contracts.world_schema_validator import validate_world_schema_at_boot
    ok = validate_world_schema_at_boot(world_conn)
    if not ok:
        logger.warning("World schema mismatch — check architecture/world_schema_manifest.yaml")
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).parent.parent.parent / "architecture" / "world_schema_manifest.yaml"


def _load_manifest() -> Optional[dict]:
    """Load world_schema_manifest.yaml. Returns None on error."""
    try:
        import yaml  # type: ignore[import]
        with open(_MANIFEST_PATH) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except ImportError:
        # yaml not available — try minimal key=value parse
        logger.debug("PyYAML not available — using JSON fallback for schema manifest")
        return _load_manifest_json_fallback()
    except (FileNotFoundError, OSError, Exception) as exc:
        logger.warning("Failed to load world_schema_manifest.yaml: %s", exc)
        return None


def _load_manifest_json_fallback() -> Optional[dict]:
    """Minimal YAML parser for simple key: value manifest (no PyYAML)."""
    try:
        # Re-use a json sidecar if present
        import json
        json_path = _MANIFEST_PATH.with_suffix(".json")
        if json_path.exists():
            with open(json_path) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _get_table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return set of column names for a table via PRAGMA table_info."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] if not hasattr(row, '__getitem__') or isinstance(row[1], str) else row["name"]
                for row in rows}
    except sqlite3.OperationalError:
        return set()


def validate_world_schema_at_boot(world_conn: sqlite3.Connection) -> bool:
    """Validate that world DB tables match architecture/world_schema_manifest.yaml.

    Returns True if all required columns are present.
    Returns False on any mismatch (caller decides WARN vs FATAL).
    Returns True (pass) if manifest is absent — fail-open until manifest is populated.
    """
    manifest = _load_manifest()
    if manifest is None:
        logger.debug("world_schema_manifest.yaml not loaded — skipping schema validation")
        return True

    tables = manifest.get("tables") or {}
    if not tables:
        logger.debug("world_schema_manifest.yaml has no tables — skipping schema validation")
        return True

    all_ok = True
    mismatches: list[str] = []

    for table_name, table_spec in tables.items():
        if not isinstance(table_spec, dict):
            continue
        required = table_spec.get("required_columns") or []
        if not required:
            continue

        actual_columns = _get_table_columns(world_conn, table_name)
        if not actual_columns:
            # Table absent from DB — could be expected if not yet created
            logger.debug("world_schema_manifest: table '%s' not found in DB (may not be created yet)", table_name)
            continue

        missing = [col for col in required if col not in actual_columns]
        if missing:
            mismatches.append(f"{table_name}: missing required columns {missing}")
            all_ok = False

    if not all_ok:
        for m in mismatches:
            logger.warning("World schema mismatch: %s", m)
        logger.warning(
            "World schema validation FAILED (%d mismatches). "
            "Phase 3 will make this FATAL. Check architecture/world_schema_manifest.yaml.",
            len(mismatches),
        )
    else:
        logger.info("World schema validation passed (%d tables checked)", len(tables))

    return all_ok
