# Created: 2026-05-28
# Last reused or audited: 2026-05-28
# Authority basis: docs/findings_2026_05_28.md §B1 — generation-naming denylist
"""
Test 2: Fresh init_schema / init_schema_trade_only / init_schema_forecasts DDL scan.
xfail(strict=False): DDL retains schema_version CHECK constraints (33 world, 6 trade,
11 forecasts hits), event_version column, idx_ens_v2_* index names, unknown_legacy
defaults, and legacy comments. These are embedded in db.py (concurrent-worker exclusion
zone) and require B2/B6 db.py surgery — deferred post-PR3.
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


@pytest.mark.xfail(
    strict=False,
    reason=(
        "World DDL has 33 generation-naming hits: schema_version CHECK constraints on "
        "book_hash_transitions/decision_events/etc., event_version column in position_events "
        "(B6 deferred), idx_ens_v2_* index names, unknown_legacy defaults, legacy comments. "
        "Fixes require db.py surgery — deferred post-PR3."
    ),
)
def test_world_schema_ddl_has_no_generation_names():
    from src.state.db import init_schema  # type: ignore[import]
    rows = _ddl_for(init_schema)
    violations = _violations_in_ddl(rows)
    assert violations == [], (
        f"init_schema DDL contains {len(violations)} generation-naming hits:\n"
        + "\n".join(violations[:20])
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Trade DDL has 6 generation-naming hits: schema_version CHECK constraints "
        "in venue_submission_envelopes + settlement_commands unknown_legacy default. "
        "Fixes require db.py surgery — deferred post-PR3."
    ),
)
def test_trade_schema_ddl_has_no_generation_names():
    from src.state.db import init_schema_trade_only  # type: ignore[import]
    rows = _ddl_for(init_schema_trade_only)
    violations = _violations_in_ddl(rows)
    assert violations == [], (
        f"init_schema_trade_only DDL contains {len(violations)} generation-naming hits:\n"
        + "\n".join(violations[:20])
    )


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Forecasts DDL has 11 generation-naming hits: schema_version CHECK constraints "
        "in day0_nowcast_runs/day0_horizon_platt_fits, v2_ prefixed object names. "
        "Fixes require db.py surgery — deferred post-PR3."
    ),
)
def test_forecasts_schema_ddl_has_no_generation_names():
    from src.state.db import init_schema_forecasts  # type: ignore[import]
    rows = _ddl_for(init_schema_forecasts)
    violations = _violations_in_ddl(rows)
    assert violations == [], (
        f"init_schema_forecasts DDL contains {len(violations)} generation-naming hits:\n"
        + "\n".join(violations[:20])
    )
