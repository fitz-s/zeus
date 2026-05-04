# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: docs/operations/task_2026-05-02_full_launch_audit/REMEDIATION_PLAN_2026-05-03.md Phase B2 promotion-evidence I/O contract.
"""Round-trip + corruption tests for promotion-evidence atomic JSON I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.control.entry_forecast_promotion_evidence_io import (
    PROMOTION_EVIDENCE_SCHEMA_VERSION,
    PromotionEvidenceCorruption,
    evidence_to_dict,
    read_promotion_evidence,
    write_promotion_evidence,
)
from src.control.entry_forecast_rollout import EntryForecastPromotionEvidence
from src.data.live_entry_status import LiveEntryForecastStatus


def _ready_status() -> LiveEntryForecastStatus:
    return LiveEntryForecastStatus(
        status="LIVE_ELIGIBLE",
        blockers=(),
        executable_row_count=4,
        producer_readiness_count=4,
        producer_live_eligible_count=4,
    )


def _evidence(**overrides) -> EntryForecastPromotionEvidence:
    base: dict = {
        "operator_approval_id": "operator-1",
        "g1_evidence_id": "g1-2026-05-03",
        "status_snapshot": _ready_status(),
        "calibration_promotion_approved": True,
        "canary_success_evidence_id": "canary-2026-05-03-a",
    }
    base.update(overrides)
    return EntryForecastPromotionEvidence(**base)


def test_round_trip_full_evidence(tmp_path: Path) -> None:
    target = tmp_path / "promotion_evidence.json"
    original = _evidence()

    write_promotion_evidence(original, path=target)
    parsed = read_promotion_evidence(path=target)

    assert parsed == original


def test_round_trip_with_nulls(tmp_path: Path) -> None:
    target = tmp_path / "promotion_evidence.json"
    original = _evidence(
        operator_approval_id=None,
        g1_evidence_id=None,
        calibration_promotion_approved=False,
        canary_success_evidence_id=None,
    )

    write_promotion_evidence(original, path=target)
    parsed = read_promotion_evidence(path=target)

    assert parsed == original


def test_missing_file_returns_none(tmp_path: Path) -> None:
    target = tmp_path / "absent.json"
    assert read_promotion_evidence(path=target) is None


def test_invalid_json_raises_corruption(tmp_path: Path) -> None:
    target = tmp_path / "broken.json"
    target.write_text("not json {{{")

    with pytest.raises(PromotionEvidenceCorruption, match="invalid JSON"):
        read_promotion_evidence(path=target)


def test_unsupported_schema_version_raises_corruption(tmp_path: Path) -> None:
    target = tmp_path / "wrong_schema.json"
    target.write_text(json.dumps({"schema_version": 99, "operator_approval_id": "x"}))

    with pytest.raises(PromotionEvidenceCorruption, match="schema_version"):
        read_promotion_evidence(path=target)


def test_non_object_root_raises_corruption(tmp_path: Path) -> None:
    target = tmp_path / "bad_root.json"
    target.write_text(json.dumps([{"schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION}]))

    with pytest.raises(PromotionEvidenceCorruption, match="object"):
        read_promotion_evidence(path=target)


def test_calibration_approved_must_be_strict_bool(tmp_path: Path) -> None:
    target = tmp_path / "truthy_string.json"
    payload = {
        "schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "operator_approval_id": "op-1",
        "g1_evidence_id": "g1",
        "calibration_promotion_approved": "true",
        "canary_success_evidence_id": None,
        "status_snapshot": _ready_status().to_dict(),
    }
    target.write_text(json.dumps(payload))

    with pytest.raises(PromotionEvidenceCorruption, match="bool"):
        read_promotion_evidence(path=target)


def test_status_snapshot_missing_field_raises(tmp_path: Path) -> None:
    target = tmp_path / "broken_snapshot.json"
    snap = _ready_status().to_dict()
    del snap["executable_row_count"]
    payload = {
        "schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "operator_approval_id": "op-1",
        "g1_evidence_id": "g1",
        "calibration_promotion_approved": True,
        "canary_success_evidence_id": None,
        "status_snapshot": snap,
    }
    target.write_text(json.dumps(payload))

    with pytest.raises(PromotionEvidenceCorruption, match="executable_row_count"):
        read_promotion_evidence(path=target)


def test_status_snapshot_blocker_must_be_list_of_str(tmp_path: Path) -> None:
    target = tmp_path / "bad_blockers.json"
    snap = _ready_status().to_dict()
    snap["blockers"] = ["ok", 7]
    payload = {
        "schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "operator_approval_id": None,
        "g1_evidence_id": None,
        "calibration_promotion_approved": False,
        "canary_success_evidence_id": None,
        "status_snapshot": snap,
    }
    target.write_text(json.dumps(payload))

    with pytest.raises(PromotionEvidenceCorruption, match="blockers"):
        read_promotion_evidence(path=target)


def test_atomic_write_does_not_leave_tmp_files(tmp_path: Path) -> None:
    target = tmp_path / "evidence.json"
    write_promotion_evidence(_evidence(), path=target)

    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert target.exists()


def test_evidence_to_dict_for_audit_logging() -> None:
    payload = evidence_to_dict(_evidence())

    assert payload["operator_approval_id"] == "operator-1"
    assert payload["status_snapshot"]["status"] == "LIVE_ELIGIBLE"
    assert isinstance(payload["status_snapshot"]["blockers"], list)


def test_write_creates_sidecar_lock_file(tmp_path: Path) -> None:
    """Phase C-flock: writer must hold an exclusive flock on a sidecar
    file so two concurrent writers cannot race and clobber payloads.
    The lock file is the inode-stable companion of the target;
    persistence after the write is acceptable (next write reuses it).
    """

    target = tmp_path / "evidence.json"
    write_promotion_evidence(_evidence(), path=target)

    lock_path = target.with_suffix(target.suffix + ".lock")
    assert lock_path.exists(), "expected sidecar .lock file to exist after write"


def test_concurrent_writes_serialize_and_last_write_wins(tmp_path: Path) -> None:
    """With flock in place, two threads writing serialized payloads
    cannot interleave at the JSON-write step. The final on-disk file
    must be a complete payload from one of the writers — never a
    partial mix. Atomic ``os.replace`` guarantees this even without
    flock; flock additionally guarantees no lost-update race against
    the lock file.
    """

    import threading

    target = tmp_path / "evidence.json"
    evidence_a = _evidence(operator_approval_id="op-A")
    evidence_b = _evidence(operator_approval_id="op-B")

    barrier = threading.Barrier(2)

    def writer(payload):
        def run():
            barrier.wait(timeout=5.0)
            write_promotion_evidence(payload, path=target)
        return run

    t1 = threading.Thread(target=writer(evidence_a))
    t2 = threading.Thread(target=writer(evidence_b))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not t1.is_alive() and not t2.is_alive()

    parsed = read_promotion_evidence(path=target)
    assert parsed is not None
    assert parsed.operator_approval_id in {"op-A", "op-B"}
