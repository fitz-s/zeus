# Lifecycle: created=2026-04-24; last_reviewed=2026-04-24; last_reused=never
# Purpose: C5 antibody — asserts that harvest_settlement routes HIGH-track
#          calibration pairs through add_calibration_pair_v2 (canonical
#          path) and NOT through legacy add_calibration_pair, so HIGH pairs
#          actually reach calibration_pairs where refit_platt reads.
# Reuse: Covers src/execution/harvester.py::harvest_settlement HIGH + LOW
#        branches. Regression-bar: if a future refactor re-introduces a
#        split HIGH-via-legacy / LOW-via-v2 routing (the pre-C5 bug), this
#        test fires. Originating handoff: POST_AUDIT_HANDOFF_2026-04-24.md
#        §3.1 C5. Related: refit_platt reads only v2 per
#        scripts/refit_platt.py:29.
# Authority basis: POST_AUDIT_HANDOFF_2026-04-24 §3.1 C5 + INV-14 spine +
#   INV-15 source/data_version whitelist at src/calibration/store.py:116.
"""C5 antibody: harvester HIGH-track pairs reach calibration_pairs."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import City
from src.execution.harvester import harvest_settlement
from src.state.db import init_schema
from src.state.schema.v2_schema import apply_canonical_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


def _make_city(name: str = "testville", tz: str = "America/Chicago") -> City:
    return City(
        name=name,
        lat=41.8781,
        lon=-87.6298,
        timezone=tz,
        settlement_unit="F",
        cluster="north",
        wu_station="KORD",
        settlement_source="KORD",
        country_code="US",
        settlement_source_type="wu_icao",
    )


@pytest.fixture()
def harvest_conn():
    """Full schema with both legacy + v2 tables so both branches can write."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _seed_high_harvest(conn, city: City):
    """Drive a single harvest_settlement call for a HIGH-track market."""
    return harvest_settlement(
        conn,
        city,
        target_date="2026-04-24",
        winning_bin_label="86-88°F",
        bin_labels=["85°F or below", "86-88°F", "89°F or higher"],
        p_raw_vector=[0.2, 0.5, 0.3],
        lead_days=1.0,
        forecast_issue_time="2026-04-23T00:00:00Z",
        forecast_available_at="2026-04-23T00:00:00Z",
        forecast_model_id="ecmwf_ens",
        settlement_value=87.0,
        temperature_metric="high",
    )


def test_high_pairs_land_in_calibration_pairs(harvest_conn):
    """C5: HIGH harvest must populate calibration_pairs (not just legacy)."""
    count = _seed_high_harvest(harvest_conn, _make_city())
    assert count == 3, f"expected 3 pairs for 3 bins, got {count}"

    v2_rows = harvest_conn.execute(
        "SELECT city, target_date, temperature_metric, dataset_id, training_allowed "
        "FROM calibration_pairs WHERE city = 'testville' AND target_date = '2026-04-24'"
    ).fetchall()

    assert len(v2_rows) == 3, f"expected 3 v2 rows; got {len(v2_rows)}"
    for row in v2_rows:
        assert row["temperature_metric"] == HIGH_LOCALDAY_MAX.temperature_metric
        assert row["dataset_id"] == HIGH_LOCALDAY_MAX.data_version
        assert row["training_allowed"] == 1, (
            "INV-15: data_version starts with 'tigge' → training_allowed "
            "must resolve to True"
        )


def test_high_pairs_land_in_calibration_pairs_canonical(harvest_conn):
    """B3 rename: calibration_pairs is now the canonical table (v2 schema).
    HIGH harvest must populate calibration_pairs with temperature_metric='high'."""
    _seed_high_harvest(harvest_conn, _make_city("no_legacy_city"))

    count = harvest_conn.execute(
        "SELECT COUNT(*) FROM calibration_pairs "
        "WHERE city = 'no_legacy_city' AND target_date = '2026-04-24'"
        "  AND temperature_metric = 'high'"
    ).fetchone()[0]
    assert count == 3, (
        f"B3: HIGH harvest must write 3 rows to canonical calibration_pairs, got {count}"
    )


def test_low_pairs_still_land_in_v2_after_c5(harvest_conn):
    """C5 symmetry: LOW branch continues to route through v2 (regression)."""
    city = _make_city("low_city")
    harvest_settlement(
        harvest_conn, city, target_date="2026-04-24",
        winning_bin_label="70-72°F",
        bin_labels=["69°F or below", "70-72°F", "73°F or higher"],
        p_raw_vector=[0.3, 0.4, 0.3],
        lead_days=1.0,
        forecast_issue_time="2026-04-23T00:00:00Z",
        forecast_available_at="2026-04-23T00:00:00Z",
        forecast_model_id="ecmwf_ens",
        settlement_value=71.0,
        temperature_metric="low",
    )

    v2_rows = harvest_conn.execute(
        "SELECT temperature_metric, dataset_id FROM calibration_pairs "
        "WHERE city = 'low_city' AND target_date = '2026-04-24'"
    ).fetchall()
    assert len(v2_rows) == 3
    for row in v2_rows:
        assert row["temperature_metric"] == LOW_LOCALDAY_MIN.temperature_metric
        assert row["dataset_id"] == LOW_LOCALDAY_MIN.data_version


def test_legacy_add_calibration_pair_no_longer_imported_in_harvester():
    """Structural regression-bar: harvester imports canonical add_calibration_pair (not v2 suffix).

    Post-B3 collapse: add_calibration_pair_v2 was renamed to add_calibration_pair.
    Harvester must import the canonical name; the _v2 suffix must be absent.
    """
    text = (PROJECT_ROOT / "src/execution/harvester.py").read_text()
    # Find the exact import statement line from src.calibration.store.
    import_lines = [
        ln for ln in text.splitlines()
        if ln.strip().startswith("from src.calibration.store")
    ]
    assert len(import_lines) == 1, (
        f"expected exactly one calibration.store import; got {import_lines}"
    )
    import_line = import_lines[0]
    # Canonical name must appear in the import.
    assert "add_calibration_pair" in import_line, (
        f"harvester import line missing add_calibration_pair: {import_line}"
    )
    # The _v2 suffix must be gone.
    assert "add_calibration_pair_v2" not in import_line, (
        f"harvester import still references legacy add_calibration_pair_v2: {import_line}"
    )


def test_v2_pair_training_allowed_respects_inv15(harvest_conn):
    """INV-15: no explicit source passed; data_version 'tigge_*' enables training.

    The v2 row's `training_allowed` field (1) proves _resolve_training_allowed
    accepted the call. calibration_pairs does not store the `source`
    argument — it's used only for the INV-15 check at insert time.
    """
    _seed_high_harvest(harvest_conn, _make_city("inv15_city"))

    rows = harvest_conn.execute(
        "SELECT training_allowed, dataset_id FROM calibration_pairs "
        "WHERE city = 'inv15_city'"
    ).fetchall()
    assert len(rows) > 0
    for row in rows:
        # _resolve_training_allowed: empty source skips src_ok check; dv
        # starts with 'tigge' → dv_ok; result = requested (True).
        assert row["training_allowed"] == 1
        assert row["dataset_id"].startswith("tigge")
