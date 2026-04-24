# Lifecycle: created=2026-04-24; last_reviewed=2026-04-24; last_reused=never
# Purpose: INV-14 identity spine antibody for harvester settlement writes —
#          pins temperature_metric / physical_quantity / observation_field to
#          canonical HIGH_LOCALDAY_MAX.* so regression to the legacy literal
#          "daily_maximum_air_temperature" fails the test.
# Reuse: Covers src/execution/harvester.py::_write_settlement_truth VERIFIED
#        write path. Residual: 1,561 pre-fix settlement rows on the live DB
#        still carry legacy physical_quantity; historical-data migration is
#        owed but out of scope for this packet.
# Authority basis: POST_AUDIT_HANDOFF_2026-04-24 §3.1 C6 + INV-14 identity spine
#   defined at src/types/metric_identity.py
"""INV-14 identity spine antibody for harvester settlement writes.

Before this antibody, `src/execution/harvester.py::_write_settlement_truth`
hardcoded the settlement's INV-14 identity fields (`temperature_metric`,
`physical_quantity`, `observation_field`) to literal strings
`"high" / "daily_maximum_air_temperature" / "high_temp"`. The
`physical_quantity` literal diverged from canonical
`HIGH_LOCALDAY_MAX.physical_quantity = "mx2t6_local_calendar_day_max"`,
so any downstream JOIN filtering on canonical physical_quantity silently
dropped 100% of harvester-written settlement rows.

This test dry-runs the harvester write and asserts the row carries the
canonical `HIGH_LOCALDAY_MAX.*` identity values. If a future refactor
re-introduces a hardcoded divergent string, this test fires.

Residual: 1,561 pre-fix settlement rows on the live DB still carry
`physical_quantity="daily_maximum_air_temperature"`. A historical-data
migration is owed but is out of scope for this packet (would require
src/state/** changes and is NEEDS_OPERATOR_DECISION).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import City
from src.execution import harvester as harvester_mod
from src.state.db import init_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX


def _make_city(name: str = "testville") -> City:
    return City(
        name=name,
        lat=41.8781,
        lon=-87.6298,
        timezone="America/Chicago",
        settlement_unit="F",
        cluster="north",
        wu_station="KORD",
        settlement_source="KORD",
        country_code="US",
        settlement_source_type="wu_icao",
    )


@pytest.fixture()
def harvester_conn():
    """In-memory settlements schema parity with live DB.

    init_schema creates the modern INV-14 columns but does not add the
    pre-INV-14 bin-evidence columns (pm_bin_lo/pm_bin_hi/unit/
    settlement_source_type) via ALTER — those were created by an older
    schema version. Live DBs already carry them; fresh test DBs don't.
    Extend fresh schema here so the harvester INSERT path can bind all
    columns.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for ddl in [
        "ALTER TABLE settlements ADD COLUMN pm_bin_lo REAL;",
        "ALTER TABLE settlements ADD COLUMN pm_bin_hi REAL;",
        "ALTER TABLE settlements ADD COLUMN unit TEXT;",
        "ALTER TABLE settlements ADD COLUMN settlement_source_type TEXT;",
    ]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    return conn


def test_harvester_settlement_uses_canonical_high_identity(harvester_conn):
    """C6: the VERIFIED settlement row carries HIGH_LOCALDAY_MAX identity."""
    city = _make_city()
    # Force SettlementSemantics to accept the proxy observation — the semantics
    # layer rounds/asserts via assert_settlement_value, and the observation
    # 88.0°F rounds to 88 which sits inside [85, 89].
    obs_row = {"high_temp": 88.0, "source": "wu_icao_history_v1", "id": 99, "fetched_at": "2026-04-24T12:00:00Z"}

    harvester_mod._write_settlement_truth(
        harvester_conn, city, "2026-04-24",
        pm_bin_lo=85.0, pm_bin_hi=89.0,
        event_slug="test-market",
        obs_row=obs_row,
    )

    row = harvester_conn.execute(
        "SELECT temperature_metric, physical_quantity, observation_field, authority "
        "FROM settlements WHERE city = ? AND target_date = ?",
        (city.name, "2026-04-24"),
    ).fetchone()

    assert row is not None, "harvester must write a settlements row"
    assert row["authority"] == "VERIFIED", row["authority"]
    assert row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
    assert row["physical_quantity"] == HIGH_LOCALDAY_MAX.physical_quantity
    assert row["observation_field"] == HIGH_LOCALDAY_MAX.observation_field


def test_physical_quantity_is_not_legacy_string(harvester_conn):
    """C6 regression-bar: catch re-introduction of the legacy literal."""
    city = _make_city("regression_city")
    obs_row = {"high_temp": 70.0, "source": "wu_icao_history_v1", "id": 100, "fetched_at": "2026-04-24T12:00:00Z"}

    harvester_mod._write_settlement_truth(
        harvester_conn, city, "2026-04-24",
        pm_bin_lo=65.0, pm_bin_hi=75.0,
        event_slug="regression-market",
        obs_row=obs_row,
    )

    row = harvester_conn.execute(
        "SELECT physical_quantity FROM settlements WHERE city = ?",
        (city.name,),
    ).fetchone()

    assert row["physical_quantity"] != "daily_maximum_air_temperature", (
        "harvester regressed to pre-C6 hardcoded physical_quantity"
    )
    assert row["physical_quantity"] == "mx2t6_local_calendar_day_max"


def test_harvester_imports_high_localday_max():
    """Structural guard: HIGH_LOCALDAY_MAX must be imported in harvester."""
    text = (PROJECT_ROOT / "src/execution/harvester.py").read_text()
    assert "HIGH_LOCALDAY_MAX" in text
    assert "from src.types.metric_identity" in text
