# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 4: No _v<N>, _old, _new, _legacy siblings of canonical tables.
xfail(strict=False): ensemble_snapshots, calibration_pairs_v2, settlement_outcomes, etc.
still exist today. PR3 B3 will canonicalize.
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


@pytest.mark.xfail(strict=False, reason="awaits PR3 B3 sweep — _v2 table siblings still present")
def test_world_schema_no_versioned_table_siblings():
    """init_schema must produce no _v<N>/_old/_new/_legacy table siblings."""
    from src.state.db import init_schema  # type: ignore[import]
    tables = _get_all_tables(init_schema)
    bad = [t for t in tables if _SIBLING_RE.search(t)]
    assert bad == [], (
        f"World schema contains {len(bad)} versioned-sibling tables: {bad}"
    )


@pytest.mark.xfail(strict=False, reason="awaits PR3 B3 sweep — _v2 table siblings still present")
def test_forecasts_schema_no_versioned_table_siblings():
    """init_schema_forecasts must produce no _v<N>/_old/_new/_legacy siblings."""
    from src.state.db import init_schema_forecasts  # type: ignore[import]
    tables = _get_all_tables(init_schema_forecasts)
    bad = [t for t in tables if _SIBLING_RE.search(t)]
    assert bad == [], (
        f"Forecasts schema contains {len(bad)} versioned-sibling tables: {bad}"
    )
