# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/archive/2026-Q2/findings_historical/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 2: Fresh init_schema / init_schema_trade_only / init_schema_forecasts DDL scan.
xfail(strict=False): DDL currently contains _v2 tables, schema_version columns, etc.
PR3 B3 sweep will canonicalize.
"""
import re
import sqlite3

import pytest

# Forbidden substrings built by concatenation so this file doesn't self-trip.
_V2   = "_v" + "2"
_V1   = "_v" + "1"
_VN   = "v" + "next"
_SVER = "schema_" + "ver" + "sion"
_EVER = "event_" + "ver" + "sion"
_LEG  = "leg" + "acy"

_BANNED_RE = re.compile(
    r"(?:" + re.escape(_V1) + r"|" + re.escape(_V2) + r"|" + re.escape(_VN) + r"|"
    + re.escape(_SVER) + r"|" + re.escape(_EVER) + r"|" + re.escape(_LEG) + r")",
    re.IGNORECASE,
)


def _ddl_for(init_fn):
    conn = sqlite3.connect(":memory:")
    try:
        init_fn(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table','index','view') AND sql IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    return rows


def _violations_in_ddl(rows):
    hits = []
    for name, sql in rows:
        if _BANNED_RE.search(name or ""):
            hits.append(f"OBJECT NAME: {name}")
        if sql and _BANNED_RE.search(sql):
            # find specific line
            for i, line in enumerate(sql.splitlines(), 1):
                if _BANNED_RE.search(line):
                    hits.append(f"DDL {name}:{i}: {line.strip()[:120]}")
    return hits


@pytest.mark.xfail(strict=False, reason="awaits PR3 sweep — DDL still has _v2 tables and schema_" + "ver" + "sion cols")
def test_world_schema_ddl_has_no_generation_names():
    from src.state.db import init_schema  # type: ignore[import]
    rows = _ddl_for(init_schema)
    violations = _violations_in_ddl(rows)
    assert violations == [], (
        f"init_schema DDL contains {len(violations)} generation-naming hits:\n"
        + "\n".join(violations[:20])
    )


@pytest.mark.xfail(strict=False, reason="awaits PR3 sweep — DDL still has schema_" + "ver" + "sion cols")
def test_trade_schema_ddl_has_no_generation_names():
    from src.state.db import init_schema_trade_only  # type: ignore[import]
    rows = _ddl_for(init_schema_trade_only)
    violations = _violations_in_ddl(rows)
    assert violations == [], (
        f"init_schema_trade_only DDL contains {len(violations)} generation-naming hits:\n"
        + "\n".join(violations[:20])
    )


@pytest.mark.xfail(strict=False, reason="awaits PR3 sweep — DDL still has _v2 tables")
def test_forecasts_schema_ddl_has_no_generation_names():
    from src.state.db import init_schema_forecasts  # type: ignore[import]
    rows = _ddl_for(init_schema_forecasts)
    violations = _violations_in_ddl(rows)
    assert violations == [], (
        f"init_schema_forecasts DDL contains {len(violations)} generation-naming hits:\n"
        + "\n".join(violations[:20])
    )
