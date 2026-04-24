# Lifecycle: created=2026-04-24; last_reviewed=2026-04-24; last_reused=never
# Purpose: Antibody for Law 5 (R-AJ) at the ingest layer — asserts the
#          `ingest_json_file` path surfaces MISSING_CAUSALITY_FIELD instead of
#          silently defaulting absent causality via setdefault.
# Reuse: Covers scripts/ingest_grib_to_snapshots.py::ingest_json_file. If a
#        future refactor re-introduces any causality default or bypass, this
#        test will fire. Originating handoff:
#        docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md
#        §3.1 M1.
# Authority basis: POST_AUDIT_HANDOFF_2026-04-24 §3.1 M1 + Law 5 / R-AJ at
#   src/contracts/snapshot_ingest_contract.py:54-58
"""Law 5 (R-AJ) antibody at the ingest layer.

`src/contracts/snapshot_ingest_contract.py` enforces R-AJ ("absent causality
field -> rejected") in `validate_snapshot_contract`. Before this antibody,
`scripts/ingest_grib_to_snapshots.ingest_json_file` silently bypassed that
rule by `setdefault("causality", {"status": "OK"})` before calling the
validator, so pre-Phase-5B JSON without causality looked like clean
training rows.

This test feeds `ingest_json_file` a payload that does NOT declare
`causality` and asserts the ingest path surfaces
`contract_rejected: MISSING_CAUSALITY_FIELD`. If a future refactor
re-introduces a causality default (or any other bypass), this test fires.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX


def _write_payload(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _base_payload() -> dict:
    """Minimum high-track payload that would otherwise pass the contract."""
    return {
        "data_version": HIGH_LOCALDAY_MAX.data_version,
        "temperature_metric": HIGH_LOCALDAY_MAX.temperature_metric,
        "physical_quantity": HIGH_LOCALDAY_MAX.physical_quantity,
        "members": [
            {"value_native_unit": 20.0 + i * 0.01} for i in range(51)
        ],
        "members_unit": "degC",
        "unit": "C",
        "city": "test_city",
        "target_date_local": "2026-04-24",
        "issue_time_utc": "2026-04-24T00:00:00Z",
        "local_day_start_utc": "2026-04-24T05:00:00Z",
        "step_horizon_hours": 24,
        "lead_day": 0,
    }


@pytest.fixture()
def ingest_env(tmp_path, monkeypatch):
    """In-memory SQLite with v2 schema; isolate get_world_connection to it."""
    import scripts.ingest_grib_to_snapshots as ingest_mod

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_v2_schema(conn)
    return conn, ingest_mod


def test_absent_causality_field_is_rejected_by_ingest(ingest_env, tmp_path):
    """R-AJ at the ingest layer: missing causality must not be defaulted."""
    conn, ingest_mod = ingest_env

    payload = _base_payload()
    assert "causality" not in payload  # pre-condition: pre-Phase-5B shape

    path = _write_payload(tmp_path, payload)
    status = ingest_mod.ingest_json_file(
        conn,
        path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    assert status == "contract_rejected: MISSING_CAUSALITY_FIELD", status

    count = conn.execute("SELECT COUNT(*) FROM ensemble_snapshots_v2").fetchone()[0]
    assert count == 0, "rejected payload must not write to ensemble_snapshots_v2"


def test_present_causality_field_survives_ingest_contract(ingest_env, tmp_path):
    """Control: a payload with explicit causality clears the contract check.

    Note: we stop short of asserting `ingested` because the ingest path
    after contract acceptance calls commit_then_export which expects extra
    infra. We only pin the contract-acceptance boundary — the complement to
    the rejection case above.
    """
    conn, ingest_mod = ingest_env

    payload = _base_payload()
    payload["causality"] = {"status": "OK"}

    path = _write_payload(tmp_path, payload)
    status = ingest_mod.ingest_json_file(
        conn,
        path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    # Must NOT be a contract rejection; may be a downstream-wiring error, but
    # Law 5 is no longer the gate.
    assert not status.startswith("contract_rejected: MISSING_CAUSALITY_FIELD"), status
