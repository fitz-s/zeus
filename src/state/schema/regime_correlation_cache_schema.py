# Created: 2026-05-21
# Last reused or audited: 2026-05-21
# Authority basis: docs/operations/task_2026-05-21_strategy_vnext_phase5_regime_correlation/PHASE_5_PLAN.md §Track2 + 06_PHASE_5_WEATHER_REGIME_CORRELATION.md §T2

"""Phase 5 T2 — CREATE TABLE DDL for regime_correlation_cache (world DB).

Per plan §Track2:
  - New table under SCHEMA_VERSION 23→24 bump.
  - PK: regime TEXT (one row per WeatherRegimeTag value that has been fit).
  - Columns: cities_json (JSON array of city names in matrix column order),
    matrix_json (JSON 2-D array of shrunk correlation matrix entries),
    fitted_at (ISO-8601 UTC timestamp), n_observations INT,
    intensity REAL (δ* used in the fit).
  - schema_version CHECK (24,) — this table was introduced at version 24.

DB location: zeus-world.db (NOT zeus-forecasts.db).
Rationale: per-regime fit data is canonical operator-tunable truth, not
a derived forecast/observation stream (plan §Track2).

INV-37: caller supplies conn; never auto-opens.
"""

from __future__ import annotations

import sqlite3

# Schema version stamped into each row; bump in sync with db.py SCHEMA_VERSION.
SCHEMA_VERSION = 24

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS regime_correlation_cache (
    regime              TEXT PRIMARY KEY,
    cities_json         TEXT NOT NULL,
    matrix_json         TEXT NOT NULL,
    fitted_at           TEXT NOT NULL,
    n_observations      INTEGER NOT NULL,
    intensity           REAL NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version IN (24, 25, 26))
)
"""

def ensure_table(conn: sqlite3.Connection) -> None:
    """Create regime_correlation_cache table if it does not exist.

    Idempotent (IF NOT EXISTS). Called from:
      1. db.py init_schema (daemon boot, world DB) — Phase 5 T2 production pass
      2. RegimeCorrelationStore constructor (in-memory test connections).

    INV-37: caller provides conn; never auto-opens.
    """
    conn.execute(CREATE_TABLE_SQL)
