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


# ---------------------------------------------------------------------------
# Law 5 presence-only gate — current-behavior pin (2026-04-24, T2-S4 followup).
#
# Con-nyx T2-S4 adversarial finding 4: Law 5 at
# `src/contracts/snapshot_ingest_contract.py:54-68` is a presence-only
# gate, not a well-formedness gate. Three malformed shapes of
# `causality` currently SILENTLY BYPASS R-AJ and get training_allowed=True:
#   - `causality={}`                (empty dict, no status key)
#   - `causality="string"`          (scalar string, not a dict)
#   - `causality={"status": None}`  (dict with null status)
#
# Empirically verified 2026-04-24 via direct call to
# validate_snapshot_contract: all three return accepted=True,
# training_allowed=True, and the ingest path writes to
# ensemble_snapshots_v2 (or would, once the write-path wiring is
# complete).
#
# These tests PIN CURRENT BEHAVIOR so an operator can see the gap is
# real and make an explicit decision: harden the contract (require
# isinstance(dict) AND non-empty-string status in the known enum) OR
# keep presence-only. If a future refactor hardens the gate WITHOUT
# updating these tests, the tests will fail and surface the policy
# change.
#
# The three cases all share one dangerous outcome: training_allowed=True.
# This means any of these malformed shapes could enter the Platt
# calibration corpus — the worst possible location for unlabeled
# provenance.
# ---------------------------------------------------------------------------


def test_law5_gap_empty_dict_causality_currently_bypasses(ingest_env, tmp_path):
    """KNOWN GAP: causality={} bypasses Law 5, training_allowed resolves True.

    Pins current behavior. If the contract is hardened to reject
    empty-dict causality, update this test + remove the 'GAP' framing.
    """
    from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

    conn, ingest_mod = ingest_env
    payload = _base_payload()
    payload["causality"] = {}  # malformed — no status key

    decision = validate_snapshot_contract(payload)
    assert decision.accepted is True, (
        "If this fails, the contract was hardened; update the test + lore."
    )
    assert decision.reason == "OK"
    assert decision.causality_status == "UNKNOWN"
    assert decision.training_allowed is True, (
        "Critical: UNKNOWN-status rows currently enter training corpus."
    )


def test_law5_gap_string_causality_currently_bypasses(ingest_env, tmp_path):
    """KNOWN GAP: causality=<string> bypasses Law 5, training_allowed resolves True.

    The dict-ness check at snapshot_ingest_contract.py:59 uses
    `isinstance(..., dict)` to extract status, falling back to
    "UNKNOWN" when not a dict — but does NOT reject the payload.
    """
    from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

    conn, ingest_mod = ingest_env
    payload = _base_payload()
    payload["causality"] = "OK"  # malformed — string, not dict

    decision = validate_snapshot_contract(payload)
    assert decision.accepted is True
    assert decision.reason == "OK"
    assert decision.causality_status == "UNKNOWN"
    assert decision.training_allowed is True


def test_law5_gap_null_status_causality_currently_bypasses(ingest_env, tmp_path):
    """KNOWN GAP: causality={'status': None} bypasses Law 5.

    `dict.get('status', 'UNKNOWN')` returns None (not 'UNKNOWN') when
    the key exists with a None value, so causality_status is None on
    the decision. training_allowed still resolves True.
    """
    from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

    conn, ingest_mod = ingest_env
    payload = _base_payload()
    payload["causality"] = {"status": None}

    decision = validate_snapshot_contract(payload)
    assert decision.accepted is True
    assert decision.reason == "OK"
    assert decision.causality_status is None, (
        "causality_status should be None (not 'UNKNOWN') — dict.get "
        "returns None when key exists with a None value."
    )
    assert decision.training_allowed is True


def test_law5_gap_explicit_none_is_rejected_same_as_absent(ingest_env, tmp_path):
    """REGRESSION BAR: causality=None (explicit) rejects same as absent.

    Both `payload['causality'] = None` and omitting the key produce
    `payload.get('causality')` returning None, which hits the explicit
    None-check at :56 and rejects. Pin this so a future refactor that
    distinguishes "absent" from "explicit None" surfaces the drift.
    """
    from src.contracts.snapshot_ingest_contract import validate_snapshot_contract

    payload = _base_payload()
    payload["causality"] = None

    decision = validate_snapshot_contract(payload)
    assert decision.accepted is False
    assert decision.reason == "MISSING_CAUSALITY_FIELD"
    assert decision.training_allowed is False
