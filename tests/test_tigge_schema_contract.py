# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: Antibody #16 — TIGGE extractor↔ingester schema drift fix.
#   Operator directive 2026-04-29: "drift needs a one-and-done solution, no asymmetry"
"""Antibody #16: TIGGE schema contract structural tests.

Prevents the drift category (extractor emits field X; ingester later requires X
but extractor does not know) from recurring.

Test 1: HIGH and LOW minimal payloads roundtrip to_json_dict <-> from_json_dict.
Test 2: from_json_dict raises ProvenanceViolation on missing required field.
Test 3: AST scan -- both extractors import TiggeSnapshotPayload.
Test 4: AST scan -- ingester imports TiggeSnapshotPayload; PROVENANCE_VIOLATION
         is the new rejection path (not raw MISSING_CAUSALITY_FIELD string in return).
Test 5: Synthetic round-trip -- HIGH/LOW payloads written via dataclass,
         ingester contract accepts them (no contract rejection).
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.contracts.tigge_snapshot_payload import ProvenanceViolation, TiggeSnapshotPayload
from src.state.schema.v2_schema import apply_v2_schema
from src.types.metric_identity import HIGH_LOCALDAY_MAX, LOW_LOCALDAY_MIN


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_high_track() -> TiggeSnapshotPayload:
    """Minimal valid HIGH track payload (mx2t6)."""
    return TiggeSnapshotPayload.make_high_track(
        generated_at="2026-04-29T00:00:00+00:00",
        data_version=HIGH_LOCALDAY_MAX.data_version,
        physical_quantity=HIGH_LOCALDAY_MAX.physical_quantity,
        param="121.128",
        paramId=121,
        short_name="mx2t6",
        step_type="max",
        aggregation_window_hours=6,
        city="TestCity",
        lat=1.0,
        lon=103.8,
        unit="C",
        timezone="Asia/Singapore",
        manifest_sha256="abc123",
        manifest_hash="def456",
        issue_time_utc="2026-04-29T00:00:00+00:00",
        target_date_local="2026-04-30",
        lead_day=1,
        lead_day_anchor="issue_utc.date()",
        local_day_start_utc="2026-04-29T16:00:00+00:00",
        local_day_end_utc="2026-04-30T16:00:00+00:00",
        local_day_window={"start": "2026-04-29T16:00:00+00:00", "end": "2026-04-30T16:00:00+00:00"},
        step_horizon_hours=40.0,
        step_horizon_deficit_hours=0.0,
        causality={"pure_forecast_valid": True, "status": "OK"},
        nearest_grid_lat=1.0,
        nearest_grid_lon=103.75,
        nearest_grid_distance_km=5.5,
        selected_step_ranges=["6-12", "12-18", "18-24", "24-30", "30-36", "36-40"],
        member_count=51,
        missing_members=[],
        training_allowed=True,
        members=[{"member": i, "value_native_unit": 30.0 + i * 0.01} for i in range(51)],
    )


def _minimal_low_track() -> TiggeSnapshotPayload:
    """Minimal valid LOW track payload (mn2t6)."""
    return TiggeSnapshotPayload.make_low_track(
        generated_at="2026-04-29T00:00:00+00:00",
        data_version=LOW_LOCALDAY_MIN.data_version,
        physical_quantity=LOW_LOCALDAY_MIN.physical_quantity,
        temperature_metric=LOW_LOCALDAY_MIN.temperature_metric,
        param="122.128",
        paramId=122,
        short_name="mn2t6",
        step_type="min",
        aggregation_window_hours=6,
        city="TestCity",
        lat=1.0,
        lon=103.8,
        unit="C",
        members_unit="K",
        timezone="Asia/Singapore",
        manifest_sha256="abc123",
        manifest_hash="def456",
        issue_time_utc="2026-04-29T00:00:00+00:00",
        target_date_local="2026-04-30",
        lead_day=1,
        lead_day_anchor="issue_utc.date()",
        local_day_start_utc="2026-04-29T16:00:00+00:00",
        local_day_end_utc="2026-04-30T16:00:00+00:00",
        local_day_window={"start": "2026-04-29T16:00:00+00:00", "end": "2026-04-30T16:00:00+00:00"},
        step_horizon_hours=40.0,
        step_horizon_deficit_hours=0.0,
        causality={"pure_forecast_valid": True, "status": "OK"},
        boundary_ambiguous=False,
        boundary_policy={
            "training_rule": "use_inner_only_and_exclude_if_boundary_can_win",
            "boundary_ambiguous": False,
            "ambiguous_member_count": 0,
        },
        nearest_grid_lat=1.0,
        nearest_grid_lon=103.75,
        nearest_grid_distance_km=5.5,
        selected_step_ranges_inner=["18-24", "24-30"],
        selected_step_ranges_boundary=["12-18"],
        member_count=51,
        missing_members=[],
        training_allowed=True,
        members=[{"member": i, "value_native_unit": 295.0 + i * 0.01} for i in range(51)],
    )


# ---------------------------------------------------------------------------
# Test 1: roundtrip to_json_dict <-> from_json_dict for both tracks
# ---------------------------------------------------------------------------


def test_high_track_roundtrip():
    """HIGH track: to_json_dict() -> from_json_dict() is lossless for all fields."""
    original = _minimal_high_track()
    d = original.to_json_dict()

    # Serialise to JSON and back to confirm dict is JSON-safe
    d_json = json.loads(json.dumps(d))
    rehydrated = TiggeSnapshotPayload.from_json_dict(d_json)

    assert rehydrated.data_version == original.data_version
    assert rehydrated.causality == original.causality
    assert rehydrated.boundary_ambiguous is False
    assert rehydrated.boundary_policy is None  # HIGH has no boundary_policy
    assert rehydrated.member_count == 51
    assert len(rehydrated.members) == 51
    assert rehydrated.training_allowed is True
    assert rehydrated.members_unit is None  # HIGH has no members_unit
    assert rehydrated.to_json_dict() == d


def test_low_track_roundtrip():
    """LOW track: to_json_dict() -> from_json_dict() is lossless for all fields."""
    original = _minimal_low_track()
    d = original.to_json_dict()

    d_json = json.loads(json.dumps(d))
    rehydrated = TiggeSnapshotPayload.from_json_dict(d_json)

    assert rehydrated.data_version == original.data_version
    assert rehydrated.causality == original.causality
    assert rehydrated.boundary_ambiguous is False
    assert isinstance(rehydrated.boundary_policy, dict)
    assert "boundary_ambiguous" in rehydrated.boundary_policy
    assert rehydrated.members_unit == "K"
    assert rehydrated.temperature_metric == "low"
    assert rehydrated.member_count == 51
    assert len(rehydrated.members) == 51
    assert rehydrated.to_json_dict() == d


# ---------------------------------------------------------------------------
# Test 2: from_json_dict raises ProvenanceViolation on missing required field
# ---------------------------------------------------------------------------


def test_missing_causality_raises():
    """from_json_dict raises ProvenanceViolation when causality is absent."""
    d = _minimal_high_track().to_json_dict()
    del d["causality"]
    with pytest.raises(ProvenanceViolation) as exc_info:
        TiggeSnapshotPayload.from_json_dict(d)
    assert "causality" in str(exc_info.value)
    assert "missing required fields" in str(exc_info.value)


def test_missing_data_version_raises():
    """Removing data_version triggers ProvenanceViolation."""
    d = _minimal_high_track().to_json_dict()
    del d["data_version"]
    with pytest.raises(ProvenanceViolation) as exc_info:
        TiggeSnapshotPayload.from_json_dict(d)
    assert "data_version" in str(exc_info.value)


def test_malformed_causality_string_raises():
    """Causality that is a string (not dict) raises ProvenanceViolation."""
    d = _minimal_high_track().to_json_dict()
    d["causality"] = "OK"
    with pytest.raises(ProvenanceViolation) as exc_info:
        TiggeSnapshotPayload.from_json_dict(d)
    assert "dict" in str(exc_info.value)


def test_causality_missing_status_raises():
    """Causality dict without 'status' key raises ProvenanceViolation."""
    d = _minimal_high_track().to_json_dict()
    d["causality"] = {}
    with pytest.raises(ProvenanceViolation) as exc_info:
        TiggeSnapshotPayload.from_json_dict(d)
    assert "status" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Test 3: AST scan -- both extractors import TiggeSnapshotPayload
# ---------------------------------------------------------------------------


def _get_tigge_imports(path: Path) -> set:
    """Return names imported from src.contracts.tigge_snapshot_payload."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "src.contracts.tigge_snapshot_payload":
                for alias in node.names:
                    imported.add(alias.name)
    return imported


def test_high_extractor_imports_dataclass():
    """extract_tigge_mx2t6_localday_max.py must import TiggeSnapshotPayload."""
    path = PROJECT_ROOT / "scripts" / "extract_tigge_mx2t6_localday_max.py"
    imports = _get_tigge_imports(path)
    assert "TiggeSnapshotPayload" in imports, (
        f"HIGH extractor does not import TiggeSnapshotPayload. Found: {imports}"
    )


def test_low_extractor_imports_dataclass():
    """extract_tigge_mn2t6_localday_min.py must import TiggeSnapshotPayload."""
    path = PROJECT_ROOT / "scripts" / "extract_tigge_mn2t6_localday_min.py"
    imports = _get_tigge_imports(path)
    assert "TiggeSnapshotPayload" in imports, (
        f"LOW extractor does not import TiggeSnapshotPayload. Found: {imports}"
    )


# ---------------------------------------------------------------------------
# Test 4: AST scan -- ingester imports TiggeSnapshotPayload; PROVENANCE_VIOLATION used
# ---------------------------------------------------------------------------


def test_ingester_imports_dataclass():
    """ingest_grib_to_snapshots.py must import TiggeSnapshotPayload."""
    path = PROJECT_ROOT / "scripts" / "ingest_grib_to_snapshots.py"
    imports = _get_tigge_imports(path)
    assert "TiggeSnapshotPayload" in imports, (
        f"Ingester does not import TiggeSnapshotPayload. Found: {imports}"
    )


def test_ingester_uses_provenance_violation_not_bare_string():
    """Ingester return statements must not hard-code 'contract_rejected: MISSING_CAUSALITY_FIELD'.

    The structural fix routes pre-schema JSONs to PROVENANCE_VIOLATION at from_json_dict.
    Any return that emits MISSING_CAUSALITY_FIELD directly is a regression.
    """
    path = PROJECT_ROOT / "scripts" / "ingest_grib_to_snapshots.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant):
            val = str(node.value.value)
            assert "MISSING_CAUSALITY_FIELD" not in val, (
                f"Ingester returns bare MISSING_CAUSALITY_FIELD string at line {node.lineno}. "
                f"Schema contract fix is incomplete."
            )


# ---------------------------------------------------------------------------
# Test 5: Synthetic round-trip -- ingester accepts HIGH and LOW payloads
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_db():
    """In-memory SQLite with v2 schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    apply_v2_schema(conn)
    return conn


def _write_json(tmp_path: Path, payload_dict: dict) -> Path:
    p = tmp_path / "snapshot.json"
    p.write_text(json.dumps(payload_dict), encoding="utf-8")
    return p


def test_high_track_ingester_accepts(mem_db, tmp_path):
    """HIGH track payload built via TiggeSnapshotPayload is accepted by ingester contract."""
    import scripts.ingest_grib_to_snapshots as ingest_mod

    payload = _minimal_high_track().to_json_dict()
    path = _write_json(tmp_path, payload)

    status = ingest_mod.ingest_json_file(
        mem_db,
        path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    assert not status.startswith("contract_rejected"), (
        f"HIGH track payload was incorrectly rejected: {status!r}"
    )
    assert not status.startswith("parse_error"), status


def test_low_track_ingester_accepts(mem_db, tmp_path):
    """LOW track payload built via TiggeSnapshotPayload is accepted by ingester contract."""
    import scripts.ingest_grib_to_snapshots as ingest_mod

    payload = _minimal_low_track().to_json_dict()
    path = _write_json(tmp_path, payload)

    status = ingest_mod.ingest_json_file(
        mem_db,
        path,
        metric=LOW_LOCALDAY_MIN,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    assert not status.startswith("contract_rejected"), (
        f"LOW track payload was incorrectly rejected: {status!r}"
    )
    assert not status.startswith("parse_error"), status


def test_high_track_missing_causality_fails_at_provenance(mem_db, tmp_path):
    """Pre-schema-contract JSON missing causality fails at ProvenanceViolation (not contract layer)."""
    import scripts.ingest_grib_to_snapshots as ingest_mod

    payload = _minimal_high_track().to_json_dict()
    del payload["causality"]
    path = _write_json(tmp_path, payload)

    status = ingest_mod.ingest_json_file(
        mem_db,
        path,
        metric=HIGH_LOCALDAY_MAX,
        model_version="ecmwf_ens",
        overwrite=True,
    )
    assert "PROVENANCE_VIOLATION" in status, (
        f"Expected PROVENANCE_VIOLATION, got: {status!r}"
    )
