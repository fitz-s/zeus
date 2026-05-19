"""Canonical per-table schema preference map for v2 tables.

Single source of truth shared by every observability/runtime reader that
needs to know where each v2 table physically lives across attached SQLite
schemas (forecasts.db / world.db / main).

K1 migration moved a subset of v2 tables from world.db to forecasts.db but
left empty ghost shells in world.db. Any sensor that searched only
["world", "main"] would hit the ghost first and return 0 rows, reporting
BLOCKED to operators even though forecasts.db held tens of millions of
verified rows.

The map below MUST be the only place the per-table preference is declared
— operators previously suffered a 2026-05-19 false-BLOCKED outage caused
by `_TABLE_SCHEMA_PREFERENCE` in calibration_serving_status.py drifting
from `_V2_ROW_COUNT_SCHEMA_PREFERENCE` in status_summary.py. The shared
module makes that drift impossible: import the map, do not redefine.

Update rule: if a new v2 table is added or a table migrates between
schemas (K-class migrations), update this map. The accompanying invariant
test asserts every v2 table mentioned in src/state/schema/v2_schema.py
appears here.
"""

from __future__ import annotations


# Per-table candidate schema list, in priority order. The first attached
# schema in which the table physically exists wins. "main" is the trade DB
# (zeus_trades) for in-process callers and a standalone DB for ETL/test
# fixtures; "world" is zeus-world.db (entry-side surfaces); "forecasts" is
# zeus-forecasts.db (calibration + ensemble + settlement archives).
V2_TABLE_SCHEMA_PREFERENCE: dict[str, tuple[str, ...]] = {
    "calibration_pairs_v2": ("forecasts", "world", "main"),
    "ensemble_snapshots_v2": ("forecasts", "world", "main"),
    "settlements_v2": ("forecasts", "world", "main"),
    "platt_models_v2": ("world", "main"),
    "historical_forecasts_v2": ("world", "main"),
}


# Public alias matches the original status_summary.py name so the import
# rewrite is a one-line per-file change. New callers should prefer
# V2_TABLE_SCHEMA_PREFERENCE.
_V2_ROW_COUNT_SCHEMA_PREFERENCE = V2_TABLE_SCHEMA_PREFERENCE
