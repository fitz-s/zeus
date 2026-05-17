# Created: 2026-05-17
# Last reused or audited: 2026-05-17
# Authority basis: FIX_SEV1_BUNDLE.md §F15 + PLAN.md WAVE-4 §F15
"""Antibody test: settlements_v2 backfill parity.

Verifies:
  1. Migration runs against an in-memory DB with settlements populated.
  2. After migration, settlements_v2 contains >= 99% of eligible settlements rows.
  3. Migration is idempotent (second run does not raise, count stable).
  4. Rows with NULL temperature_metric are skipped (not inserted into v2).
  5. v1-only columns are preserved in provenance_json under 'v1_extra'.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import types
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MIGRATION_PATH = REPO_ROOT / "scripts" / "migrations" / "202605_backfill_settlements_v2.py"

_SETTLEMENTS_DDL = """
CREATE TABLE settlements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    market_slug TEXT,
    winning_bin TEXT,
    settlement_value REAL,
    settlement_source TEXT,
    settled_at TEXT,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED',
    pm_bin_lo REAL,
    pm_bin_hi REAL,
    unit TEXT,
    settlement_source_type TEXT,
    physical_quantity TEXT,
    observation_field TEXT,
    data_version TEXT,
    provenance_json TEXT,
    temperature_metric TEXT
)
"""

_SETTLEMENTS_V2_DDL = """
CREATE TABLE settlements_v2 (
    settlement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    temperature_metric TEXT NOT NULL CHECK (temperature_metric IN ('high', 'low')),
    market_slug TEXT,
    winning_bin TEXT,
    settlement_value REAL,
    settlement_source TEXT,
    settled_at TEXT,
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
    provenance_json TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(city, target_date, temperature_metric)
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_f15", MIGRATION_PATH)
    mod = types.ModuleType("mig_f15")
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _make_db(n_rows: int = 200, include_null_metric: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_SETTLEMENTS_DDL)
    conn.execute(_SETTLEMENTS_V2_DDL)
    cities = ["Atlanta", "Chicago", "London", "Karachi", "Tokyo"]
    for i in range(n_rows):
        city = cities[i % len(cities)]
        target_date = f"2026-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}"
        metric = "high" if i % 2 == 0 else "low"
        conn.execute(
            "INSERT INTO settlements (city, target_date, temperature_metric, "
            "market_slug, settlement_value, authority, provenance_json, "
            "pm_bin_lo, pm_bin_hi, unit, settlement_source_type, data_version) "
            "VALUES (?, ?, ?, ?, ?, 'VERIFIED', '{\"src\":\"test\"}', 40.0, 50.0, 'F', 'WU', 'v1')",
            (city, target_date, metric, f"slug-{i}", float(i)),
        )
    if include_null_metric:
        conn.execute(
            "INSERT INTO settlements (city, target_date, temperature_metric, authority, provenance_json) "
            "VALUES ('NullCity', '2026-01-01', NULL, 'UNVERIFIED', '{}')"
        )
    conn.commit()
    return conn


class TestSettlementsParity:

    def test_backfill_covers_99pct_of_eligible(self):
        mod = _load_migration()
        conn = _make_db(200)
        mod.up(conn)

        eligible = conn.execute(
            "SELECT COUNT(*) FROM settlements WHERE temperature_metric IS NOT NULL"
        ).fetchone()[0]
        v2_count = conn.execute("SELECT COUNT(*) FROM settlements_v2").fetchone()[0]
        assert eligible > 0
        parity = v2_count / eligible
        assert parity >= 0.99, f"v2 parity {parity:.3f} < 0.99 (v2={v2_count}, eligible={eligible})"

    def test_idempotent(self):
        mod = _load_migration()
        conn = _make_db(50)
        mod.up(conn)
        count_after_first = conn.execute("SELECT COUNT(*) FROM settlements_v2").fetchone()[0]
        mod.up(conn)
        count_after_second = conn.execute("SELECT COUNT(*) FROM settlements_v2").fetchone()[0]
        assert count_after_first == count_after_second

    def test_null_metric_rows_skipped(self):
        mod = _load_migration()
        conn = _make_db(10, include_null_metric=True)
        mod.up(conn)
        # NullCity row must not appear in v2
        null_rows = conn.execute(
            "SELECT COUNT(*) FROM settlements_v2 WHERE city='NullCity'"
        ).fetchone()[0]
        assert null_rows == 0

    def test_v1_extra_preserved_in_provenance(self):
        mod = _load_migration()
        conn = _make_db(5)
        mod.up(conn)
        row = conn.execute(
            "SELECT provenance_json FROM settlements_v2 LIMIT 1"
        ).fetchone()
        assert row is not None
        prov = json.loads(row[0])
        assert "v1_extra" in prov, "v1_extra key missing from provenance_json"
        assert "pm_bin_lo" in prov["v1_extra"]
        assert "data_version" in prov["v1_extra"]
