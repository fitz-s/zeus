# Created: 2026-10-01
# Last audited: 2026-10-01
# Lifecycle: created=2026-10-01; last_reviewed=2026-10-01; last_reused=2026-10-01
# Purpose: Pin the failed-seed fence to the exact dependency set its request build reads.
# Reuse: Run when seed request preparation, local-proof transport, or any seed producer changes.
# Authority basis: external round-2 merge-safety review REQ-20261001-073420 §2 (S1 derived
#   transport omitted, S2 unreadable identity hashed, S4 marker versioning) and the
#   Denver 2026-10-01 producer storm (committed-ENS supersede republished a fenced seed).
"""Real-reader dependency mutations for the failed-seed input-identity fence."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data import replacement_forecast_live_materialization_queue as queue
from tests.data.test_fusion_upgrade_trigger import (
    _SEOUL_ANCHOR_CYCLE,
    _blocked_identity_harness,
    _insert_local_proof,
)

UTC = timezone.utc


def _transport_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The blocked-identity harness plus an anchor body row the real transport
    selector finds, so the build reads a derived precision transport. Only the
    validated local-proof object and its deterministic path are stubbed; the
    selector, the identity, the queue and the durable fence are real."""
    from src.data import raw_forecast_artifact_manifest as manifest
    from src.data.openmeteo_ecmwf_ifs9_anchor import HIGH_DATA_VERSION, PRODUCT_ID, SOURCE_ID

    db, raw, _revision, tick, queue_root, builds = _blocked_identity_harness(tmp_path, monkeypatch)
    body = raw / "openmeteo.json"
    data = body.read_bytes()
    with sqlite3.connect(db) as conn:
        cursor = conn.execute(
            """INSERT INTO raw_forecast_artifacts (source_id, product_id, data_version,
               source_cycle_time, source_available_at, captured_at, artifact_path, sha256,
               byte_size, request_params_json, artifact_metadata_json, training_allowed,
               recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, 0, '2026-07-24T12:40:00+00:00')""",
            (SOURCE_ID, PRODUCT_ID, HIGH_DATA_VERSION, _SEOUL_ANCHOR_CYCLE, _SEOUL_ANCHOR_CYCLE,
             _SEOUL_ANCHOR_CYCLE, str(body), hashlib.sha256(data).hexdigest(), len(data),
             json.dumps({"city": "Seoul", "target_date": "2026-07-25", "metric": "high",
                         "source_run_id": "openmeteo:test"})),
        )
        body_id = int(cursor.lastrowid)
    precision = {"fixture_precision": "valid local proof returned by proof-reader stub"}
    local = SimpleNamespace(original_body_artifact={"artifact_path": str(body)},
                            owned_body={"path": str(body)}, precision_metadata=precision)
    derived = raw / "derived.precision.json"
    monkeypatch.setattr(manifest, "read_anchor_local_proof", lambda *_a, **_k: local)
    monkeypatch.setattr(manifest, "anchor_precision_transport_path", lambda *_a, **_k: derived)
    return db, raw, derived, manifest._proof_json(precision), body_id, tick, queue_root, builds


def _touch(path: Path, seconds: float) -> None:
    stamp = datetime(2026, 7, 24, 12, 30, tzinfo=UTC).timestamp() + seconds
    os.utime(path, (stamp, stamp))


@pytest.mark.parametrize("dependency", (
    "derived_transport_restored", "derived_transport_rewritten", "declared_precision",
    "declared_body", "anchor_local_proof_row",
))
def test_fence_reopens_on_each_dependency_its_build_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dependency: str,
) -> None:
    """S1: each dependency class the build reads reopens the fence immediately;
    a file the build never reads leaves it fenced."""
    db, raw, derived, sealed, body_id, tick, _root, builds = _transport_harness(
        tmp_path, monkeypatch,
    )
    if dependency not in ("derived_transport_restored",):
        derived.write_bytes(sealed)  # transport valid: the guarded build BLOCKS
        _touch(derived, 0)
    if dependency == "derived_transport_rewritten":
        derived.write_bytes(sealed + b" ")  # present but changed: transport rejected
        _touch(derived, 0)
    tick()
    failed_builds = len(builds)
    for _ in range(5):
        tick()
    (raw / "unrelated.precision.json").write_text("{}\n", encoding="utf-8")
    tick()
    assert len(builds) == failed_builds, "unchanged reads (plus an unread file) stay fenced"

    if dependency in ("derived_transport_restored", "derived_transport_rewritten"):
        derived.write_bytes(sealed)
        _touch(derived, 1)
    elif dependency == "declared_precision":
        (raw / "precision.json").write_text('{"v": 2}\n', encoding="utf-8")
        _touch(raw / "precision.json", 1)
    elif dependency == "declared_body":
        _touch(raw / "openmeteo.json", 1)
    else:
        _insert_local_proof(db, original_id=body_id, cycle=_SEOUL_ANCHOR_CYCLE)
    tick()
    assert len(builds) == failed_builds + 1, f"{dependency}: a read input changed but stayed fenced"


def test_unreadable_dependency_neither_installs_nor_honors_a_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2: a proven-absent file is a known state; a permission/I-O failure is unknown."""
    db, raw, _revision, tick, queue_root, _builds = _blocked_identity_harness(tmp_path, monkeypatch)
    tick()
    seed = next(json.loads(f.read_text()) for f in (queue_root / "seed_failed").glob("*.json")
                if "city" in json.loads(f.read_text()))
    cut = datetime.fromisoformat(seed["computed_at"])
    with sqlite3.connect(db) as conn:
        assert queue.blocked_seed_identity_fenced(seed, queue_root=queue_root, conn=conn, decision_at=cut)
        original = Path.stat

        def unreadable(path, *a, **k):
            if path == raw / "precision.json":
                raise PermissionError("fixture")
            return original(path, *a, **k)

        monkeypatch.setattr(Path, "stat", unreadable)
        deps = queue.seed_build_dependencies(
            seed, seeds_dir=queue_root / "seeds", conn=conn, decision_at=cut,
        )
        assert deps.identity is None and isinstance(deps.unknown, PermissionError)
        assert not queue.blocked_seed_identity_fenced(
            seed, queue_root=queue_root, conn=conn, decision_at=cut,
        )
        assert queue._record_blocked_seed_identity(
            queue_root / "seeds" / "x.json", seed, reason_codes=("X",), conn=conn, forecast_db=db,
        ) is None
        monkeypatch.setattr(Path, "stat", original)
        (raw / "precision.json").unlink()
        absent = queue.seed_build_dependencies(
            seed, seeds_dir=queue_root / "seeds", conn=conn, decision_at=cut,
        )
        assert absent.identity is not None, "a proven-absent file is fingerprintable"
        assert absent.identity != deps.identity


def test_pre_v2_fence_markers_are_obsolete_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S4: an unversioned marker (fc85ad238/dfbabe2b4) stays on disk, never fences."""
    _db, _raw, _revision, tick, queue_root, builds = _blocked_identity_harness(tmp_path, monkeypatch)
    assert tick() == 1
    marker_dir = queue_root / queue.BLOCKED_SEED_IDENTITY_DIR
    markers = list(marker_dir.glob("*.json"))
    tag = f".{queue.SEED_IDENTITY_SCHEMA}."
    assert markers and all(tag in m.name for m in markers)
    for marker in markers:
        marker.rename(marker.with_name(marker.name.replace(tag, ".")))
    assert tick() == 1, "an obsolete marker must not fence the rebuilt seed"
    assert len(builds) == 2
    assert any(tag not in m.name for m in marker_dir.glob("*.json")), "legacy evidence is kept"


@pytest.mark.parametrize("producer", ("cycle_advance", "fusion_upgrade"))
def test_fenced_identity_makes_every_producer_write_zero_seed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, producer: str,
) -> None:
    """Denver 2026-10-01 low: the committed-ENS supersede path of the cycle-advance
    producer republished a consumed seed every wake (7 files in 22 min) whose
    identity the consumer had already fenced. Each producer's shared seed writer
    consults the fence before writing; a changed read input writes again."""
    from src.data import replacement_cycle_advance_trigger as cycle_advance
    from src.data import replacement_fusion_upgrade_trigger as fusion

    write_upgrade_seed = fusion._build_and_write_upgrade_seed  # the harness stubs it
    db, raw, _revision, tick, queue_root, _builds = _blocked_identity_harness(tmp_path, monkeypatch)
    tick()
    payload = next(json.loads(f.read_text()) for f in (queue_root / "seed_failed").glob("*.json")
                   if "city" in json.loads(f.read_text()))
    seeds = queue_root / "seeds"
    written: list[Path] = []

    def build(conn, *, computed_at):
        seed = {**payload, "computed_at": computed_at.isoformat()}
        seed.pop("upgrade_trigger", None)
        common = dict(
            city="Seoul", target_date="2026-07-25", metric="high", manifests=(), raw_dir=raw,
            seed_path=seeds, computed_at=computed_at,
            build_seed=lambda **_k: SimpleNamespace(ok=True, seed=dict(seed)),
            latest_baseline_coverage=lambda *_a, **_k: object(),
            market_bins=lambda *_a, **_k: [{"bin_id": "20C"}],
            write_seed=lambda path, _seed: written.append(Path(path)),
            latest_manifest=lambda *_a, **_k: SimpleNamespace(
                artifact_path=str(raw / "openmeteo.json")),
            manifest_path_value=lambda _m, key: str(
                raw / ("precision.json" if "precision" in key else "openmeteo.json")),
            manifest_base_dir=lambda *_a, **_k: raw,
            resolve_path=lambda value, base_dir: str(value),
            expected_identity=lambda _metric: {
                "openmeteo_ifs9_anchor": SimpleNamespace(source_id="s", data_version="d")},
        )
        if producer == "cycle_advance":
            return cycle_advance._build_and_write_advance_seed(
                conn, **common, carrier_cycle_time=payload["source_cycle_time"],
                seed_name=lambda *_a, **_k: "Seoul.next.json",
            )
        return write_upgrade_seed(
            conn, **common, seed_file=seeds / "Seoul.next.json",
            source_cycle_time=payload["source_cycle_time"],
            current_temperature_state=payload.get("day0_current_temperature_state"),
        )

    later = datetime.fromisoformat(payload["computed_at"]).replace(minute=59)
    with sqlite3.connect(db) as conn:
        for _ in range(7):
            with pytest.raises(queue.SeedInputIdentityFenced):
                build(conn, computed_at=later)
        assert written == []
        (raw / "precision.json").write_text('{"v": 2}\n', encoding="utf-8")
        assert build(conn, computed_at=later) is not None
    assert len(written) == 1
