# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 4: No _v<N>, _old, _new, _legacy siblings of canonical tables.

STRICT after PR3 B3 (2026-05-28): all _v2 table siblings dropped except
observation_instants_v2 which is retained intentionally (V1V2 inventory §C:
parallel observation tier with distinct schema, not a migration pair).
"""
import re
import sqlite3

import pytest

# Build banned suffix patterns by concatenation
_V_SUFFIX    = "_v" + r"\d+"   # _v<N>
_OLD_SUFFIX  = "_" + "old"
_NEW_SUFFIX  = "_" + "new"
_LEG_SUFFIX  = "_" + "leg" + "acy"

_SIBLING_RE = re.compile(
    r"(?:" + _V_SUFFIX + r"|" + re.escape(_OLD_SUFFIX)
    + r"|" + re.escape(_NEW_SUFFIX) + r"|" + re.escape(_LEG_SUFFIX) + r")$",
    re.IGNORECASE,
)

# Intentional retentions — must have documented authority basis.
# observation_instants_v2: V1V2 inventory §C — parallel observation tier,
#   distinct schema (NOT a migration pair). Retained permanently.
ALLOWED_TABLE_SIBLINGS = {
    "observation_instants_v2",
}


def _get_all_tables(init_fn):
    conn = sqlite3.connect(":memory:")
    try:
        init_fn(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def test_world_schema_no_versioned_table_siblings():
    """init_schema must produce no _v<N>/_old/_new/_legacy table siblings."""
    from src.state.db import init_schema  # type: ignore[import]
    tables = _get_all_tables(init_schema)
    bad = [t for t in tables if _SIBLING_RE.search(t) and t not in ALLOWED_TABLE_SIBLINGS]
    assert bad == [], (
        f"World schema contains {len(bad)} versioned-sibling tables: {bad}"
    )


def test_forecasts_schema_no_versioned_table_siblings():
    """init_schema_forecasts must produce no _v<N>/_old/_new/_legacy siblings."""
    from src.state.db import init_schema_forecasts  # type: ignore[import]
    tables = _get_all_tables(init_schema_forecasts)
    bad = [t for t in tables if _SIBLING_RE.search(t) and t not in ALLOWED_TABLE_SIBLINGS]
    assert bad == [], (
        f"Forecasts schema contains {len(bad)} versioned-sibling tables: {bad}"
    )
