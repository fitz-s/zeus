# Lifecycle: created=2026-09-29; last_reviewed=2026-09-29; last_reused=2026-09-29
# Purpose: Canonical immutable ground entities, causal possession and facts-only RESET.
# Reuse: pytest tests/test_station_ground_evidence.py
# Authority basis: replacement_final_form §1d205–215; AGENTS §0/§2 INV-14/INV-47.
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.data import station_ground_evidence as ground
from src.state.schema.v2_schema import ensure_replacement_forecast_live_schema

UTC = timezone.utc


def _setup(tmp_path, monkeypatch, city="Hong Kong"):
    import src.config as config
    from tests.test_config import _official_hko_registry, _official_kord_registry

    registry, official_body, claims = (
        _official_hko_registry if city == "Hong Kong" else _official_kord_registry
    )(tmp_path, monkeypatch)
    monkeypatch.setattr(ground, "_store_root", lambda: tmp_path / "state" / "station_ground")
    clock = [datetime(2026, 9, 29, 22, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(ground, "datetime", Clock)
    db = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    return db, registry, official_body, claims, clock


def _archive(db, city="Hong Kong"):
    report = ground.archive_station_ground_evidence(db, [city])
    assert report["status"] == "GROUND_SOURCE_ARCHIVED"
    return report["archived"][city]


def _update_official(registry, official_body, claims, body, checked_at, city="Hong Kong"):
    import src.config as config
    official_body.write_bytes(body)
    claim = claims[city]["station_ground_proof"]
    facts = config.station_ground_facts_from_bytes(
        source_kind=claim["source_kind"], station_id=claim["station_id"], raw_body=body,
    )
    assert facts is not None
    claim.update(facts, body_sha256=hashlib.sha256(body).hexdigest(), checked_at=checked_at)
    registry.write_text(json.dumps(claims))


@pytest.mark.parametrize("city", ("Hong Kong", "Chicago"))
def test_actual_ground_source_first_canonical_possession_does_not_backdate_or_renew(tmp_path, monkeypatch, city):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch, city)
    first = _archive(db, city)
    assert first["source_cycle_role"] == "ground_snapshot_capture_not_forecast_issued"
    assert ground.read_frozen_station_ground_evidence(first, decision_at="2026-09-29T21:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city=city, decision_at="2026-09-29T21:59:59Z") is None
    assert ground.read_frozen_station_ground_evidence(first, decision_at="2026-09-29T22:00:00Z") == first
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone()
    clock[0] = datetime(2026, 9, 29, 23, 50, tzinfo=UTC)
    _update_official(registry, official_body, claims, official_body.read_bytes(), "2026-09-29T23:00:00Z", city)
    again = _archive(db, city)
    assert again == first
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts").fetchone() == row
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1
    assert ground.read_frozen_station_ground_evidence(first, decision_at="2026-09-29T22:00:00Z") == first


def test_frozen_a_unrelated_page_b_and_real_station_c_have_separate_fact_and_capture_identity(tmp_path, monkeypatch):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body_a = official_body.read_bytes()
    a = _archive(db)
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    body_b = body_a + b"<!-- unrelated station/page formatting edit -->"
    _update_official(registry, official_body, claims, body_b, "2026-09-29T22:30:00Z")
    b = _archive(db)
    assert a["artifact_id"] != b["artifact_id"]
    assert a["body_sha256"] != b["body_sha256"]
    assert a["facts_identity"] == b["facts_identity"]
    assert ground.read_frozen_station_ground_evidence(a, decision_at="2026-09-30T00:00:00Z") == a
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:59:59Z") == a
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") == b

    body_c = body_b.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    clock[0] = datetime(2026, 9, 30, 1, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_c, "2026-09-30T00:30:00Z")
    c = _archive(db)
    assert c["facts"]["elevation_m"] == 33
    assert c["facts_identity"] != a["facts_identity"]
    assert ground.read_frozen_station_ground_evidence(a, decision_at="2026-09-29T22:00:00Z") == a
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:59:59Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T01:00:00Z") == c
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 3


@pytest.mark.parametrize("part", ("body", "manifest"))
def test_normal_archive_restores_exact_missing_or_damaged_owned_file_without_new_clock(tmp_path, monkeypatch, part):
    db, _, _, _, clock = _setup(tmp_path, monkeypatch)
    original = _archive(db)
    path = Path(original["body_path" if part == "body" else "manifest_path"])
    expected = path.read_bytes()
    path.write_bytes(b"damaged owned canonical file")
    assert ground.read_frozen_station_ground_evidence(original, decision_at="2026-09-29T22:00:00Z") is None
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    assert _archive(db) == original
    assert path.read_bytes() == expected
    assert len(tuple(path.parent.glob(path.name + ".damaged.*"))) == 1
    assert ground.read_frozen_station_ground_evidence(original, decision_at="2026-09-29T22:00:00Z") == original
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ("artifact_id", "foreign_station", "capture", "facts", "body_symlink", "manifest_symlink", "db_metadata", "db_cycle", "db_path", "db_url", "db_available"))
def test_frozen_ground_entity_rejects_unbound_or_tampered_identity(tmp_path, monkeypatch, mutation):
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    proof = _archive(db)
    altered = json.loads(json.dumps(proof))
    if mutation == "artifact_id":
        altered["artifact_id"] += 1
    elif mutation == "foreign_station":
        altered["station_id"] = "KORD"
    elif mutation == "capture":
        altered["captured_at"] = "2026-09-29T04:00:00Z"
    elif mutation == "facts":
        altered["facts"]["elevation_m"] = 33
    elif mutation == "db_metadata":
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}'")
    elif mutation.startswith("db_"):
        field = {"db_cycle": "source_cycle_time", "db_path": "artifact_path",
                 "db_url": "request_url", "db_available": "source_available_at"}[mutation]
        with sqlite3.connect(db) as conn:
            conn.execute(f"UPDATE raw_forecast_artifacts SET {field}='unbound identity'")
    else:
        path = Path(proof["body_path" if mutation == "body_symlink" else "manifest_path"])
        other = tmp_path / "foreign-copy"
        other.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(other)
    assert ground.read_frozen_station_ground_evidence(altered, decision_at="2026-09-29T22:00:00Z") is None


def test_latest_causal_invalid_metadata_is_not_masked_by_older_valid_ground_entity(tmp_path, monkeypatch):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    a = _archive(db)
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    _update_official(registry, official_body, claims, official_body.read_bytes() + b"<!-- newer entity -->", "2026-09-29T22:30:00Z")
    b = _archive(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}' WHERE artifact_id=?", (b["artifact_id"],))
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z") == a


def test_failed_new_manifest_leaves_no_authorizing_db_reference_and_normal_retry_recovers(tmp_path, monkeypatch):
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    writer = ground._write_immutable

    def fail_manifest(path, body):
        if path.name.endswith(".manifest.json"):
            raise OSError("simulated manifest disk failure")
        writer(path, body)

    monkeypatch.setattr(ground, "_write_immutable", fail_manifest)
    report = ground.archive_station_ground_evidence(db, ["Hong Kong"])
    assert report["status"] == "GROUND_SOURCE_UNPROVEN"
    assert "manifest disk failure" in report["degraded"]["Hong Kong"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 0
    assert tuple(ground._store_root().glob("*.body"))  # recoverable orphan, not authority
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z") is None
    monkeypatch.setattr(ground, "_write_immutable", writer)
    assert ground.read_frozen_station_ground_evidence(_archive(db), decision_at="2026-09-29T22:00:00Z") is not None


def test_connection_identity_is_actual_db_not_memory_or_claimed_path(tmp_path, monkeypatch):
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    with sqlite3.connect(db) as conn:
        assert ground.forecast_db_from_connection(conn) == db.resolve()
    with sqlite3.connect(":memory:") as conn:
        assert ground.forecast_db_from_connection(conn) is None


def _bound_entity(entity, decision_at):
    from src.data.replacement_forecast_materializer import _bind_provider_geometry_identity
    import src.config as config
    station = config.runtime_station_geometry_for_city(config.runtime_cities_by_name()["Hong Kong"])

    @dataclass(frozen=True)
    class Shape:
        shape_hash: str = "current-source-shape"
        provider_geometry_evidence: object = None
        provider_geometry_identity_hash: object = None
        provider_geometry_audit: object = None

    @dataclass(frozen=True)
    class Metadata:
        city: str = "Hong Kong"
        station_id: str = "HKO_HQ"
        station_lat: float = station["lat"]
        station_lon: float = station["lon"]
        station_elevation_m: float = entity["facts"]["elevation_m"]
        source_geometry_proof: object = None

    metadata = Metadata(source_geometry_proof={
        "revision": "openmeteo_ifs9_o1280_source_cell_v1",
        "station_ground_proof": {"revision": "station_ground_roles_v1", "status": "VERIFIED",
            "reason": None, "facts": entity["facts"], "audit": entity["source_audit"]},
    })
    return _bind_provider_geometry_identity(Shape(), {}, anchor_metadata=metadata,
        decision_at=decision_at, station_ground_evidence=entity)


def test_public_ground_gate_replays_own_a_and_not_latest_page_sha_b(tmp_path, monkeypatch):
    from src.data.replacement_forecast_cycle_policy import _anchor_station_ground_has_authority as authority
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    a = _archive(db)
    bound_a = _bound_entity(a, "2026-09-29T22:00:00Z")
    assert authority(bound_a.provider_geometry_evidence, bound_a.provider_geometry_audit, "2026-09-29T22:00:00Z")
    assert not authority(bound_a.provider_geometry_evidence, bound_a.provider_geometry_audit, "2026-09-29T04:00:00Z")
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    body_b = official_body.read_bytes() + b"<!-- unrelated official page edit -->"
    _update_official(registry, official_body, claims, body_b, "2026-09-29T22:30:00Z")
    b = _archive(db)
    bound_b = _bound_entity(b, "2026-09-29T23:00:00Z")
    assert bound_b.shape_hash == bound_a.shape_hash
    assert authority(bound_a.provider_geometry_evidence, bound_a.provider_geometry_audit, "2026-09-29T22:00:00Z")
    # A can also support a genuinely new decision when its own bytes/facts are
    # still true. Updating the audit decision cannot bypass canonical possession.
    new_a = _bound_entity(a, "2026-09-29T23:00:00Z")
    assert authority(new_a.provider_geometry_evidence, new_a.provider_geometry_audit, "2026-09-29T23:00:00Z")
    clock[0] = datetime(2026, 9, 30, 1, tzinfo=UTC)
    body_c = body_b.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    _update_official(registry, official_body, claims, body_c, "2026-09-30T00:30:00Z")
    c = _archive(db)
    current_a = _bound_entity(a, "2026-09-30T01:00:00Z")
    assert not authority(current_a.provider_geometry_evidence, current_a.provider_geometry_audit, "2026-09-30T01:00:00Z")
    bound_c = _bound_entity(c, "2026-09-30T01:00:00Z")
    assert bound_c.shape_hash != bound_a.shape_hash
    assert authority(bound_c.provider_geometry_evidence, bound_c.provider_geometry_audit, "2026-09-30T01:00:00Z")
    assert authority(bound_a.provider_geometry_evidence, bound_a.provider_geometry_audit, "2026-09-29T22:00:00Z")


@pytest.mark.parametrize("metric", ("high", "low"))
def test_ground_facts_only_normal_blocked_fingerprint_reset_not_whole_page_or_future(tmp_path, monkeypatch, metric):
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    payload = {"forecast_db": str(db), "city": "Hong Kong", "target_date": "2026-09-30",
        "temperature_metric": metric, "source_cycle_time": "2026-09-29T18:00:00Z"}
    def fingerprint(cut):
        return _blocked_attempt_fingerprint(input_json=tmp_path / "seed.json", forecast_db=db,
            payload={**payload, "computed_at": cut})
    old = fingerprint("2026-09-29T21:59:59Z")
    assert old is not None
    a = _archive(db)
    assert fingerprint("2026-09-29T21:59:59Z") == old
    ready_a = fingerprint("2026-09-29T22:00:00Z")
    assert ready_a is not None and ready_a != old
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    body_b = official_body.read_bytes() + b"<!-- unrelated official page edit -->"
    _update_official(registry, official_body, claims, body_b, "2026-09-29T22:30:00Z")
    b = _archive(db)
    assert b["artifact_id"] != a["artifact_id"]
    assert fingerprint("2026-09-29T23:00:00Z") == ready_a
    clock[0] = datetime(2026, 9, 30, 1, tzinfo=UTC)
    body_c = body_b.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    _update_official(registry, official_body, claims, body_c, "2026-09-30T00:30:00Z")
    _archive(db)
    assert fingerprint("2026-09-29T23:00:00Z") == ready_a
    assert fingerprint("2026-09-30T01:00:00Z") != ready_a


def test_normal_producer_archives_before_queue_discovery_cutoff(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src.data import replacement_forecast_production as producer
    from src.data import replacement_forecast_live_materialization_queue as queue
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z") is None
    calls = []
    def discover_after_archive(**kwargs):
        assert kwargs["discover"] is True
        entity = ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z")
        assert entity is not None
        calls.append(entity["artifact_id"])
        return SimpleNamespace(processed_count=0, seed_processed_count=0)
    monkeypatch.setattr(queue, "process_replacement_forecast_live_materialization_queue", discover_after_archive)
    cfg = {"forecast_db": db, "limit": 1, "seed_limit": 1, "seed_discovery_limit": 1,
        **{key: tmp_path / key for key in ("request_dir", "processed_dir", "failed_dir", "seed_dir",
            "seed_processed_dir", "seed_failed_dir", "raw_manifest_dir")}}
    producer._run_replacement_forecast_live_materialization_queue_once(cfg)
    assert len(calls) == 1


def test_corrupt_same_body_metadata_normal_archive_appends_causal_recovery_and_preserves_other_city(tmp_path, monkeypatch):
    from tests.test_config import _official_kord_registry
    db, _, _, _, clock = _setup(tmp_path, monkeypatch)
    original = _archive(db)
    _official_kord_registry(tmp_path, monkeypatch)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}' WHERE artifact_id=?", (original["artifact_id"],))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original["artifact_id"],)).fetchone()
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z") is None
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    report = ground.archive_station_ground_evidence(db, ["Hong Kong", "Chicago"])
    assert set(report["archived"]) == {"Hong Kong", "Chicago"}
    recovery = report["archived"]["Hong Kong"]
    assert recovery["revision"] == ground.MANIFEST_KIND
    assert recovery["captured_at"] == original["captured_at"]
    assert recovery["recorded_at"] == "2026-09-29T23:00:00+00:00"
    assert recovery["facts_identity"] == original["facts_identity"]
    assert recovery["recovery_of"]["artifact_id"] == original["artifact_id"]
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") == recovery
    assert ground.read_frozen_station_ground_evidence(recovery, decision_at="2026-09-29T23:00:00Z") == recovery
    assert ground.read_current_station_ground_evidence(db, city="Chicago", decision_at="2026-09-29T23:00:00Z") is not None
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (original["artifact_id"],)).fetchone() == broken
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 3
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    repeat = ground.archive_station_ground_evidence(db, ["Hong Kong", "Chicago"])
    assert repeat["archived"]["Hong Kong"] == recovery
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 3
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}' WHERE artifact_id=?", (recovery["artifact_id"],))
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") is None
    # Real normal re-possession creates a later immutable manifest; an older
    # valid manifest is never chosen to hide the damaged latest candidate.
    healed = ground.archive_station_ground_evidence(db, ["Hong Kong"])["archived"]["Hong Kong"]
    assert healed["artifact_id"] != recovery["artifact_id"]
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") == healed


@pytest.mark.parametrize("failed_city", ("Hong Kong", "Chicago"))
def test_city_local_manifest_failure_does_not_rollback_another_city_progress(tmp_path, monkeypatch, failed_city):
    from tests.test_config import _official_kord_registry
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    _official_kord_registry(tmp_path, monkeypatch)
    writer = ground._write_immutable
    failed_station = "HKO_HQ" if failed_city == "Hong Kong" else "KORD"
    def failed_manifest(path, body):
        if path.name.startswith(failed_station + ".") and path.name.endswith(".manifest.json"):
            raise OSError("city-local storage failure")
        writer(path, body)
    monkeypatch.setattr(ground, "_write_immutable", failed_manifest)
    report = ground.archive_station_ground_evidence(db, ["Hong Kong", "Chicago"])
    valid_city = "Chicago" if failed_city == "Hong Kong" else "Hong Kong"
    assert set(report["archived"]) == {valid_city}
    assert failed_city in report["degraded"]
    assert ground.read_current_station_ground_evidence(db, city=valid_city, decision_at="2026-09-29T22:00:00Z") is not None
    assert ground.read_current_station_ground_evidence(db, city=failed_city, decision_at="2026-09-29T22:00:00Z") is None
    monkeypatch.setattr(ground, "_write_immutable", writer)
    assert set(ground.archive_station_ground_evidence(db, [failed_city])["archived"]) == {failed_city}


def test_real_sqlite_writer_error_remains_visible_and_rolls_back_whole_transaction(tmp_path, monkeypatch):
    from tests.test_config import _official_kord_registry
    from src.state import db as state_db
    db, _, _, _, _ = _setup(tmp_path, monkeypatch)
    _official_kord_registry(tmp_path, monkeypatch)
    class FailedWriter(sqlite3.Connection):
        def execute(self, statement, parameters=()):
            if statement.strip().startswith("INSERT INTO raw_forecast_artifacts") and "station_ground::HKO_HQ" in parameters:
                raise sqlite3.OperationalError("simulated actual DB writer failure")
            return super().execute(statement, parameters)
    monkeypatch.setattr(state_db, "_connect", lambda path, **_kw: sqlite3.connect(path, factory=FailedWriter))
    with pytest.raises(sqlite3.OperationalError, match="actual DB writer failure"):
        ground.archive_station_ground_evidence(db, ["Hong Kong", "Chicago"])
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 0


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_actual_source_a_b_a_confirmation_resets_without_renewing_original_body(tmp_path, monkeypatch, metric):
    from src.data.replacement_forecast_cycle_policy import _anchor_station_ground_has_authority as authority
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    original_body = official_body.read_bytes()
    a = _archive(db)
    def fingerprint(cut):
        return _blocked_attempt_fingerprint(input_json=tmp_path / "seed.json", forecast_db=db,
            payload={"forecast_db": str(db), "city": "Hong Kong", "target_date": "2026-09-30",
                "temperature_metric": metric, "source_cycle_time": "2026-09-29T18:00:00Z", "computed_at": cut})
    fp_a = fingerprint("2026-09-29T22:00:00Z")
    with sqlite3.connect(db) as conn:
        original_row = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone()
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    body_b = original_body.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    _update_official(registry, official_body, claims, body_b, "2026-09-29T22:30:00Z")
    b = _archive(db)
    fp_b = fingerprint("2026-09-29T23:00:00Z")
    assert fp_b != fp_a
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    # This timestamp represents a newly acquired official A response, not a
    # local verification of the old file. Its body is deliberately identical.
    _update_official(registry, official_body, claims, original_body, "2026-09-29T23:30:00Z")
    confirmed = _archive(db)
    assert confirmed["revision"] == ground.MANIFEST_KIND
    assert confirmed["manifest_role"] == "source_capture_confirmation"
    assert "recovery_of" not in confirmed
    assert confirmed["input_bodies"]["ground"]["artifact_id"] == a["artifact_id"]
    assert confirmed["input_bodies"]["ground"]["captured_at"] == a["captured_at"]
    assert confirmed["captured_at"] == "2026-09-29T23:30:00+00:00"
    assert confirmed["recorded_at"] == "2026-09-30T00:00:00+00:00"
    assert confirmed["facts_identity"] == a["facts_identity"]
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:59:59Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") == confirmed
    assert ground.read_frozen_station_ground_evidence(a, decision_at="2026-09-29T22:00:00Z") == a
    bound = _bound_entity(confirmed, "2026-09-30T00:00:00Z")
    assert authority(bound.provider_geometry_evidence, bound.provider_geometry_audit, "2026-09-30T00:00:00Z")
    assert bound.shape_hash == _bound_entity(a, "2026-09-29T22:00:00Z").shape_hash
    assert fingerprint("2026-09-29T23:59:59Z") == fp_b
    assert fingerprint("2026-09-30T00:00:00Z") == fp_a
    clock[0] = datetime(2026, 9, 30, 1, tzinfo=UTC)
    assert _archive(db) == confirmed  # repeated same actual capture is not an event
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone() == original_row
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 3


def test_late_canonical_write_and_old_source_config_cannot_roll_back_newer_ground_capture(tmp_path, monkeypatch):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body_a = official_body.read_bytes()
    a = _archive(db)
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    body_b = body_a.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    _update_official(registry, official_body, claims, body_b, "2026-09-29T22:30:00Z")
    b = _archive(db)
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_a, a["captured_at"])
    _archive(db)
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") == b
    # A genuinely different old source body can arrive late in canonical
    # storage. Source capture, not write order, determines current currency.
    _update_official(registry, official_body, claims, body_a + b"<!-- late old source response -->", "2026-09-29T22:15:00Z")
    _archive(db)
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z") == a


def test_latest_confirmation_invalid_metadata_requires_new_canonical_recovery_not_older_a(tmp_path, monkeypatch):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body_a = official_body.read_bytes()
    a = _archive(db)
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_a.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1), "2026-09-29T22:30:00Z")
    _archive(db)
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_a, "2026-09-29T23:30:00Z")
    confirmed = _archive(db)
    assert confirmed["revision"] == ground.MANIFEST_KIND
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}' WHERE artifact_id=?", (confirmed["artifact_id"],))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (confirmed["artifact_id"],)).fetchone()
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") is None
    clock[0] = datetime(2026, 9, 30, 1, tzinfo=UTC)
    restored = _archive(db)
    assert restored["manifest_role"] == "canonical_metadata_recovery"
    assert restored["captured_at"] == confirmed["captured_at"]
    assert restored["recovery_of"]["artifact_id"] == confirmed["artifact_id"]
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T01:00:00Z") == restored
    assert ground.read_frozen_station_ground_evidence(a, decision_at="2026-09-29T22:00:00Z") == a
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (confirmed["artifact_id"],)).fetchone() == broken
