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


@pytest.mark.parametrize("bad_capture", ("bad-old-clock", "2026-09-30T12:00:00Z"))
def test_new_actual_source_capture_drains_old_invalid_capture_without_authorising_invalid_latest(tmp_path, monkeypatch, bad_capture):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body_a = official_body.read_bytes()
    a = _archive(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at=? WHERE artifact_id=?", (bad_capture, a["artifact_id"]))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone()
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:00:00Z") is None
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    body_b = body_a.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1)
    _update_official(registry, official_body, claims, body_b, "2026-09-29T22:30:00Z")
    b = _archive(db)
    assert ground.read_frozen_station_ground_evidence(b, decision_at="2026-09-29T23:00:00Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:59:59Z") is None
    # A latest bad source clock cannot be hidden by old B. A truly future
    # canonical row cannot revoke B at the preceding independent cutoff.
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    body_c = body_b.replace(b'<td class="td1_normal_class">33</td>', b'<td class="td1_normal_class">34</td>', 1)
    _update_official(registry, official_body, claims, body_c, "2026-09-29T23:30:00Z")
    c = _archive(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='broken-latest-clock' WHERE artifact_id=?", (c["artifact_id"],))
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone() == broken
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:59:59Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") is None
    clock[0] = datetime(2026, 9, 30, 1, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_c + b"<!-- actual later official response -->", "2026-09-30T00:30:00Z")
    d = _archive(db)
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T01:00:00Z") == d


@pytest.mark.parametrize("bad_capture", ("broken-source-clock", "2026-10-01T00:00:00Z"))
def test_real_recapture_a_confirms_after_invalid_b_clock_without_claiming_a_clock_for_b(tmp_path, monkeypatch, bad_capture):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body_a = official_body.read_bytes()
    a = _archive(db)
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_a.replace(b'<td class="td1_normal_class">32</td>', b'<td class="td1_normal_class">33</td>', 1), "2026-09-29T22:30:00Z")
    b = _archive(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at=? WHERE artifact_id=?", (bad_capture, b["artifact_id"]))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (b["artifact_id"],)).fetchone()
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_a, a["captured_at"])
    old_poll = ground.archive_station_ground_evidence(db, ["Hong Kong"])
    assert not old_poll["archived"]
    assert "Hong Kong" in old_poll["degraded"]
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") is None
    _update_official(registry, official_body, claims, body_a, "2026-09-29T23:30:00Z")
    confirmed = _archive(db)
    assert confirmed["manifest_role"] == "source_capture_confirmation"
    assert confirmed["previous_source_capture_invalid"] is True
    assert confirmed["previous_source_evidence"]["captured_at"] == bad_capture
    assert "recovery_of" not in confirmed
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-30T00:00:00Z") == confirmed
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (b["artifact_id"],)).fetchone() == broken


@pytest.mark.parametrize("source_capture", ("2026-09-29T21:50:00Z", "2026-09-29T22:00:00Z"))
def test_late_old_or_equal_capture_cannot_wash_invalid_event_bound(tmp_path, monkeypatch, source_capture):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body = official_body.read_bytes()
    a = _archive(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at='broken-source-clock' WHERE artifact_id=?", (a["artifact_id"],))
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    _update_official(registry, official_body, claims, body + b"<!-- delayed old response -->", source_capture)
    _archive(db)
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") is None


@pytest.mark.parametrize("bad_capture", ("bad-original-clock", "2026-09-30T12:00:00Z"))
def test_actual_same_byte_source_recapture_recovers_unknown_original_clock_only_at_new_cut(tmp_path, monkeypatch, bad_capture):
    db, registry, official_body, claims, clock = _setup(tmp_path, monkeypatch)
    body_a = official_body.read_bytes()
    a = _archive(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at=? WHERE artifact_id=?", (bad_capture, a["artifact_id"]))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone()
    clock[0] = datetime(2026, 9, 29, 23, tzinfo=UTC)
    _update_official(registry, official_body, claims, body_a, "2026-09-29T22:00:00Z")
    equal_event = ground.archive_station_ground_evidence(db, ["Hong Kong"])
    assert not equal_event["archived"]  # equal to the known possession bound is insufficient
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") is None
    _update_official(registry, official_body, claims, body_a, "2026-09-29T22:30:00Z")
    confirmation = _archive(db)
    assert confirmation["manifest_role"] == "source_capture_confirmation"
    assert confirmation["original_source_clock_invalid"] is True
    assert "recovery_of" not in confirmation
    assert confirmation["input_bodies"]["ground"]["captured_at"] == bad_capture
    assert confirmation["captured_at"] == "2026-09-29T22:30:00+00:00"
    assert confirmation["recorded_at"] == "2026-09-29T23:00:00+00:00"
    assert ground.read_frozen_station_ground_evidence(a, decision_at="2026-09-29T22:00:00Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T22:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at="2026-09-29T23:00:00Z") == confirmation
    assert ground.read_frozen_station_ground_evidence(confirmation, decision_at="2026-09-29T23:00:00Z") == confirmation
    clock[0] = datetime(2026, 9, 30, tzinfo=UTC)
    assert _archive(db) == confirmation
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone() == broken
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 2


def _wmd_setup(tmp_path, monkeypatch, city="Paris"):
    """Actual retained WMDR/AWC bytes through the approved private registry."""
    from tests.test_config import _official_wmd_registry
    registry, primary, bridge, claims = _official_wmd_registry(tmp_path, monkeypatch, city)
    monkeypatch.setattr(ground, "_store_root", lambda: tmp_path / "state" / "station_ground")
    clock = [datetime(2026, 9, 30, 1, 15, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(ground, "datetime", Clock)
    db = tmp_path / "zeus-forecasts.db"
    with sqlite3.connect(db) as conn:
        ensure_replacement_forecast_live_schema(conn)
    return db, registry, primary, bridge, claims, clock


def test_wmd_normal_archive_binds_two_whole_entities_and_original_possession(tmp_path, monkeypatch):
    db, _, primary, bridge, _, clock = _wmd_setup(tmp_path, monkeypatch)
    first = _archive(db, "Paris")
    assert first["revision"] == ground.MANIFEST_KIND
    assert first["manifest_role"] == "source_entity_combination"
    assert first["facts"]["elevation_m"] == 67
    assert first["captured_at"] == "2026-09-30T01:00:00+00:00"
    assert first["facts_effective_at"] == first["recorded_at"] == clock[0].isoformat()
    bodies = first["input_bodies"]
    assert set(bodies) == {"ground", "identity_bridge"}
    for role, original in (("ground", primary), ("identity_bridge", bridge)):
        dependency = bodies[role]
        assert Path(dependency["artifact_path"]).read_bytes() == original.read_bytes()
        assert dependency["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
        assert dependency["byte_size"] == len(original.read_bytes())
        assert dependency["recorded_at"] == clock[0].isoformat()
    assert bodies["ground"]["captured_at"] == "2026-09-30T01:00:00+00:00"
    assert bodies["identity_bridge"]["captured_at"] == "2026-09-29T23:47:57+00:00"
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T01:14:59Z") is None
    assert ground.read_frozen_station_ground_evidence(first, decision_at=clock[0]) == first
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == first
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id").fetchall()
        assert len(rows) == 3
    clock[0] = datetime(2026, 9, 30, 2, tzinfo=UTC)
    assert _archive(db, "Paris") == first
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id").fetchall() == rows


@pytest.mark.parametrize("role", ("ground", "identity_bridge"))
def test_wmd_self_consistent_resigned_foreign_source_url_is_not_official_authority(tmp_path, monkeypatch, role):
    """Keep whole bytes/facts/clocks valid; falsify and re-seal only the source URL."""
    db, _, primary, bridge, _, clock = _wmd_setup(tmp_path, monkeypatch)
    first = _archive(db, "Paris")
    assert ground.read_frozen_station_ground_evidence(first, decision_at=clock[0]) == first
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == first
    changed = json.loads(json.dumps(first))
    foreign = "https://untrusted.example/foreign-station.xml" if role == "ground" else "https://untrusted.example/foreign-station.json"
    changed["input_bodies"][role]["request_url"] = foreign
    if role == "ground":
        changed["source_url"] = foreign
    else:
        changed["source_audit"]["bridge"]["source_url"] = foreign
    # Not a mere DB tuple mismatch: bind the altered URL into the complete
    # frozen input tuple, canonical manifest bytes and its actual DB descriptor.
    payload = {key:value for key,value in changed.items()
        if key not in {"artifact_id", "manifest_path", "manifest_sha256", "recorded_at"}}
    manifest = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    changed["manifest_sha256"] = hashlib.sha256(manifest).hexdigest()
    changed["manifest_path"] = str(ground._store_root() / f"{changed['station_id']}.{changed['manifest_sha256']}.manifest.json")
    Path(changed["manifest_path"]).write_bytes(manifest)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET request_url=? WHERE artifact_id=?",
            (foreign, changed["input_bodies"][role]["artifact_id"]))
        conn.execute("""UPDATE raw_forecast_artifacts SET request_url=?, artifact_path=?,
            sha256=?, byte_size=?, artifact_metadata_json=? WHERE artifact_id=?""",
            (changed["source_url"], changed["manifest_path"], changed["manifest_sha256"], len(manifest),
             json.dumps({"station_ground_evidence": changed}), changed["artifact_id"]))
    assert changed["facts"] == first["facts"]
    assert changed["facts_identity"] == first["facts_identity"]
    assert Path(changed["body_path"]).read_bytes() == primary.read_bytes()
    assert Path(changed["input_bodies"]["identity_bridge"]["artifact_path"]).read_bytes() == bridge.read_bytes()
    assert ground.read_frozen_station_ground_evidence(changed, decision_at=clock[0]) is None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None


def _wmd_bridge_capture(registry, bridge, claims, *, captured, note):
    """TEST_ONLY new acquired AWC body, not a rewrite of canonical evidence."""
    rows = json.loads(bridge.read_bytes())
    rows[0]["test_only_unrelated_page_note"] = note
    body = json.dumps(rows).encode()
    bridge.write_bytes(body)
    claim = claims["Paris"]["station_ground_proof"]
    claim["bridge"].update(body_sha256=hashlib.sha256(body).hexdigest(), checked_at=captured)
    claim["checked_at"] = max(ground._stamp(claim["source_checked_at"]), ground._stamp(captured)).isoformat()
    registry.write_text(json.dumps(claims))


def test_same_wmd_xml_new_real_bridge_appends_combination_without_mutating_either_first_body(tmp_path, monkeypatch):
    db, registry, primary, bridge, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    a = _archive(db, "Paris")
    with sqlite3.connect(db) as conn:
        old_rows = {row[0]: row for row in conn.execute("SELECT * FROM raw_forecast_artifacts")}
    _wmd_bridge_capture(registry, bridge, claims, captured="2026-09-30T01:20:00Z", note="new bridge entity")
    # Source possession is not retroactive; the old canonical tuple still
    # proves the old cut and the late bridge cannot be inserted before capture.
    report = ground.archive_station_ground_evidence(db, ["Paris"])
    assert not report["archived"]
    clock[0] = datetime(2026, 9, 30, 1, 30, tzinfo=UTC)
    b = _archive(db, "Paris")
    assert b["manifest_role"] == "source_capture_confirmation"
    assert b["input_bodies"]["ground"] == a["input_bodies"]["ground"]
    assert b["input_bodies"]["identity_bridge"]["artifact_id"] != a["input_bodies"]["identity_bridge"]["artifact_id"]
    assert b["captured_at"] == "2026-09-30T01:20:00+00:00"
    assert b["facts_identity"] == a["facts_identity"]
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) == a
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T01:29:59Z") == a
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == b
    with sqlite3.connect(db) as conn:
        assert len(conn.execute("SELECT * FROM raw_forecast_artifacts").fetchall()) == 5
        for artifact_id, old in old_rows.items():
            assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone() == old
    clock[0] = datetime(2026, 9, 30, 2, tzinfo=UTC)
    assert _archive(db, "Paris") == b


@pytest.mark.parametrize("metric", ("high", "low"))
def test_known_wmd_future_period_transitions_at_actual_cut_without_new_http_or_body_clock(tmp_path, monkeypatch, metric):
    """TEST_ONLY future period in retained XML; all parser/producer gates real."""
    import copy
    import xml.etree.ElementTree as ET
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    db, registry, primary, _, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    ns = {"w": "http://def.wmo.int/wmdr/2017", "g": "http://www.opengis.net/gml/3.2"}
    tree = ET.fromstring(primary.read_bytes())
    facility = tree.find("w:facility/w:ObservingFacility", ns)
    future = copy.deepcopy(facility.find("w:geospatialLocation", ns))
    future.find("w:GeospatialLocation/w:validPeriod/g:TimePeriod/g:beginPosition", ns).text = "2026-10-01"
    future.find("w:GeospatialLocation/w:geoLocation/g:Point/g:pos", ns).text = "48.9675 2.4275 100"
    facility.append(future)
    body = ET.tostring(tree)
    primary.write_bytes(body)
    claims["Paris"]["station_ground_proof"]["body_sha256"] = hashlib.sha256(body).hexdigest()
    registry.write_text(json.dumps(claims))
    a = _archive(db, "Paris")
    assert a["facts"]["elevation_m"] == 67  # future version does not poison A

    def fingerprint(cut):
        return _blocked_attempt_fingerprint(input_json=tmp_path / "seed.json", forecast_db=db,
            payload={"forecast_db": str(db), "city": "Paris", "target_date": "2026-10-01",
                     "temperature_metric": metric, "source_cycle_time": "2026-09-30T12:00:00Z", "computed_at": cut})

    fp_a = fingerprint("2026-09-30T23:59:59Z")
    with sqlite3.connect(db) as conn:
        originals = conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id").fetchall()
    clock[0] = datetime(2026, 10, 1, tzinfo=UTC)
    # Pure current lookup refuses stale A interval; it cannot write or silently
    # elevate known future facts. Frozen A still reproduces its own old cut.
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) == a
    fp_blocked = fingerprint(clock[0].isoformat())
    assert fp_blocked != fp_a
    b = _archive(db, "Paris")
    assert b["manifest_role"] == "known_effective_period_transition"
    assert b["captured_at"] == a["captured_at"]
    assert b["source_audit"] == a["source_audit"]
    assert b["input_bodies"] == a["input_bodies"]
    assert b["facts"]["elevation_m"] == 100
    assert b["facts_identity"] != a["facts_identity"]
    assert b["facts_effective_at"] == clock[0].isoformat()
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T23:59:59Z") == a
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == b
    assert fingerprint(clock[0].isoformat()) not in {fp_a, fp_blocked}
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT * FROM raw_forecast_artifacts ORDER BY artifact_id").fetchall()
        assert rows[:len(originals)] == originals
        assert len(rows) == 4  # only a typed transition, no fabricated body/HTTP
    clock[0] = datetime(2026, 10, 1, 1, tzinfo=UTC)
    assert _archive(db, "Paris") == b


def test_wmd_invalid_latest_manifest_never_falls_back_and_normal_recovery_is_only_later_canonical(tmp_path, monkeypatch):
    db, _, _, _, _, clock = _wmd_setup(tmp_path, monkeypatch)
    a = _archive(db, "Paris")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json='{}' WHERE artifact_id=?", (a["artifact_id"],))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone()
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) is None
    clock[0] = datetime(2026, 9, 30, 2, tzinfo=UTC)
    recovered = _archive(db, "Paris")
    assert recovered["manifest_role"] == "canonical_metadata_recovery"
    assert recovered["input_bodies"] == a["input_bodies"]
    assert recovered["captured_at"] == a["captured_at"]
    assert recovered["facts_identity"] == a["facts_identity"]
    assert recovered["recovery_of"]["artifact_id"] == a["artifact_id"]
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T01:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == recovered
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (a["artifact_id"],)).fetchone() == broken
    clock[0] = datetime(2026, 9, 30, 3, tzinfo=UTC)
    assert _archive(db, "Paris") == recovered


@pytest.mark.parametrize("role", ("ground", "identity_bridge"))
@pytest.mark.parametrize("mutation", ("bytes", "symlink", "db_identity", "late_possession"))
def test_wmd_dual_whole_body_proof_rejects_one_fault_after_normal_positive(tmp_path, monkeypatch, role, mutation):
    db, _, _, _, _, clock = _wmd_setup(tmp_path, monkeypatch)
    a = _archive(db, "Paris")
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) == a
    dependency = a["input_bodies"][role]
    path = Path(dependency["artifact_path"])
    if mutation == "bytes":
        path.write_bytes(path.read_bytes() + b"damaged")
    elif mutation == "symlink":
        copy = tmp_path / "foreign-body"
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    else:
        field, value = ("request_url", "https://example.invalid/wrong-product") if mutation == "db_identity" else ("recorded_at", "2026-09-30T03:00:00Z")
        with sqlite3.connect(db) as conn:
            conn.execute(f"UPDATE raw_forecast_artifacts SET {field}=? WHERE artifact_id=?", (value, dependency["artifact_id"]))
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) is None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None


@pytest.mark.parametrize("bad_target", ("manifest", "ground"))
@pytest.mark.parametrize("bad_capture", ("invalid-original-clock", "2026-10-01T12:00:00Z"))
def test_wmd_actual_later_primary_recapture_drains_invalid_clock_without_changing_original_tuple(tmp_path, monkeypatch, bad_target, bad_capture):
    db, registry, _, _, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    a = _archive(db, "Paris")
    target_id = a["artifact_id"] if bad_target == "manifest" else a["input_bodies"]["ground"]["artifact_id"]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE raw_forecast_artifacts SET captured_at=? WHERE artifact_id=?", (bad_capture, target_id))
        broken = conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (target_id,)).fetchone()
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None
    clock[0] = datetime(2026, 9, 30, 2, tzinfo=UTC)
    assert not ground.archive_station_ground_evidence(db, ["Paris"])["archived"]
    # A real new acquisition of the same primary body is separately audited;
    # unknown original clocks stay unknown, never relabeled as the new capture.
    claim = claims["Paris"]["station_ground_proof"]
    claim["source_checked_at"] = claim["checked_at"] = "2026-09-30T02:30:00Z"
    registry.write_text(json.dumps(claims))
    clock[0] = datetime(2026, 9, 30, 3, tzinfo=UTC)
    confirmed = _archive(db, "Paris")
    assert confirmed["manifest_role"] == "source_capture_confirmation"
    assert confirmed["captured_at"] == "2026-09-30T02:30:00+00:00"
    if bad_target == "ground":
        assert confirmed["original_source_clock_invalid_by_role"] == {"ground": True, "identity_bridge": False}
        assert confirmed["input_bodies"]["ground"]["captured_at"] == bad_capture
    else:
        assert confirmed["previous_source_capture_invalid"] is True
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T02:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == confirmed
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) is None
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (target_id,)).fetchone() == broken
    clock[0] = datetime(2026, 9, 30, 4, tzinfo=UTC)
    assert _archive(db, "Paris") == confirmed


def test_wmd_whole_body_and_manifest_storage_failure_has_no_authorizing_reference_and_retries_normally(tmp_path, monkeypatch):
    db, _, _, _, _, clock = _wmd_setup(tmp_path, monkeypatch)
    writer = ground._write_immutable
    def fail_manifest(path, body):
        if path.name.endswith(".manifest.json"):
            raise OSError("controlled WMD manifest disk failure")
        writer(path, body)
    monkeypatch.setattr(ground, "_write_immutable", fail_manifest)
    report = ground.archive_station_ground_evidence(db, ["Paris"])
    assert "controlled WMD manifest disk failure" in report["degraded"]["Paris"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts").fetchone()[0] == 0
    assert len(list(ground._store_root().glob("*.body"))) == 2
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None
    monkeypatch.setattr(ground, "_write_immutable", writer)
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None
    proof = _archive(db, "Paris")
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == proof


def test_wmd_actual_insert_clocks_follow_whole_files_and_cannot_borrow_preparation_knowledge(tmp_path, monkeypatch):
    from datetime import timedelta
    db, _, _, _, _, clock = _wmd_setup(tmp_path, monkeypatch)
    writer = ground._write_immutable
    def delayed_store(path, body):
        writer(path, body)
        clock[0] += timedelta(minutes=1)  # Real fixture storage delay, not capture.
    monkeypatch.setattr(ground, "_write_immutable", delayed_store)
    proof = _archive(db, "Paris")
    assert proof["input_bodies"]["ground"]["recorded_at"] == "2026-09-30T01:16:00+00:00"
    assert proof["input_bodies"]["identity_bridge"]["recorded_at"] == "2026-09-30T01:17:00+00:00"
    assert proof["facts_effective_at"] == proof["manifest_prepared_at"] == "2026-09-30T01:17:00+00:00"
    assert proof["recorded_at"] == "2026-09-30T01:18:00+00:00"
    payload = json.loads(Path(proof["manifest_path"]).read_bytes())
    assert "recorded_at" not in payload  # Actual later own DB INSERT, not prewritten future time.
    assert payload["input_bodies"] == proof["input_bodies"]
    assert ground.read_frozen_station_ground_evidence(proof, decision_at="2026-09-30T01:17:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T01:17:59Z") is None
    assert ground.read_frozen_station_ground_evidence(proof, decision_at=clock[0]) == proof


def _wmd_known_periods(primary, registry, claims, periods, *, captured):
    """TEST_ONLY acquired WMDR input retaining future direct ground intervals."""
    import copy
    import xml.etree.ElementTree as ET
    raw = primary.read_bytes()
    ns = {"w": "http://def.wmo.int/wmdr/2017", "g": "http://www.opengis.net/gml/3.2"}
    tree = ET.fromstring(raw)
    facility = tree.find("w:facility/w:ObservingFacility", ns)
    prototype = facility.find("w:geospatialLocation", ns)
    for begin, height in periods:
        future = copy.deepcopy(prototype)
        future.find("w:GeospatialLocation/w:validPeriod/g:TimePeriod/g:beginPosition", ns).text = begin
        future.find("w:GeospatialLocation/w:geoLocation/g:Point/g:pos", ns).text = f"48.9675 2.4275 {height}"
        facility.append(future)
    body = ET.tostring(tree)
    primary.write_bytes(body)
    claim = claims["Paris"]["station_ground_proof"]
    claim.update(body_sha256=hashlib.sha256(body).hexdigest(), source_checked_at=captured,
                 checked_at=max(ground._stamp(captured), ground._stamp(claim["bridge"]["checked_at"])).isoformat())
    registry.write_text(json.dumps(claims))


def test_later_wmd_body_same_current_geometry_discloses_future_target_change_without_future_q(tmp_path, monkeypatch):
    from zoneinfo import ZoneInfo
    db, registry, primary, _, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    a = _archive(db, "Paris")
    def coverage(proof, day, decision):
        from datetime import timedelta
        start = datetime.combine(day, datetime.min.time(), ZoneInfo("Europe/Paris"))
        return ground.station_ground_target_coverage(proof, decision_at=decision,
            target_start_utc=start.astimezone(UTC), target_end_utc=(start+timedelta(days=1)).astimezone(UTC))
    from datetime import date
    target = date(2026, 10, 1)
    before = coverage(a, target, clock[0])
    other_before = coverage(a, date(2026, 9, 30), clock[0])
    assert before["status"] == other_before["status"] == "VERIFIED"
    _wmd_known_periods(primary, registry, claims, [("2026-10-01", 100)], captured="2026-09-30T01:20:00Z")
    clock[0] = datetime(2026, 9, 30, 1, 30, tzinfo=UTC)
    b = _archive(db, "Paris")
    assert b["facts"] == a["facts"] and b["facts_identity"] == a["facts_identity"]
    after = coverage(b, target, clock[0])
    assert after["status"] == "DATA_DEGRADED"
    assert after["reason"] == "TARGET_STATION_GROUND_GEOMETRY_UNSUPPORTED"
    assert after["first_conflicting_at"] == "2026-10-01T00:00:00+00:00"
    assert after["applicability_identity"] != before["applicability_identity"]
    assert after["facts_identity"] == b["facts_identity"]  # Never select 100 as current q geometry.
    assert coverage(b, date(2026, 9, 30), clock[0])["applicability_identity"] == other_before["applicability_identity"]
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T01:29:59Z") == a
    assert coverage(a, target, "2026-09-30T01:29:59Z") == before  # Old knowledge cut remains lawful.


def test_wmd_target_half_open_edges_and_internal_a_b_a_are_not_endpoint_only(tmp_path, monkeypatch):
    db, registry, primary, _, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    _wmd_known_periods(primary, registry, claims,
        [("2026-10-01T06:00:00Z", 100), ("2026-10-01T12:00:00Z", 67)], captured="2026-09-30T01:00:00Z")
    proof = _archive(db, "Paris")
    def coverage(start, end):
        return ground.station_ground_target_coverage(proof, decision_at=clock[0], target_start_utc=start, target_end_utc=end)
    assert coverage("2026-10-01T00:00:00Z", "2026-10-01T06:00:00Z")["status"] == "VERIFIED"
    middle = coverage("2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    assert middle["status"] == "DATA_DEGRADED"
    assert middle["first_conflicting_at"] == "2026-10-01T06:00:00+00:00"
    assert coverage("2026-10-01T12:00:00Z", "2026-10-02T00:00:00Z")["status"] == "VERIFIED"


def test_single_body_and_unrelated_wmd_deployment_periods_gain_no_new_target_ban(tmp_path, monkeypatch):
    import xml.etree.ElementTree as ET
    db, registry, primary, _, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    original = _archive(db, "Paris")
    kwargs = {"decision_at": clock[0], "target_start_utc": "2026-10-01T00:00:00Z", "target_end_utc": "2026-10-02T00:00:00Z"}
    expected = ground.station_ground_target_coverage(original, **kwargs)
    tree = ET.fromstring(primary.read_bytes())
    root = ET.SubElement(tree, "test_only_unrelated_deployment")
    ET.SubElement(root, "beginPosition").text = "2026-10-01T06:00:00Z"
    ET.SubElement(root, "endPosition").text = "unrelated-non-ground-label"
    body = ET.tostring(tree)
    primary.write_bytes(body)
    claims["Paris"]["station_ground_proof"].update(body_sha256=hashlib.sha256(body).hexdigest(),
        checked_at="2026-09-30T01:20:00Z", source_checked_at="2026-09-30T01:20:00Z")
    registry.write_text(json.dumps(claims))
    clock[0] = datetime(2026, 9, 30, 1, 30, tzinfo=UTC)
    next_proof = _archive(db, "Paris")
    kwargs["decision_at"] = clock[0]
    assert ground.station_ground_target_coverage(next_proof, **kwargs) == expected
    from tests.test_config import _official_hko_registry
    _official_hko_registry(tmp_path, monkeypatch)
    hko = _archive(db, "Hong Kong")
    assert ground.station_ground_target_coverage(hko, **kwargs)["status"] == "VERIFIED"


@pytest.mark.parametrize("metric", ("high", "low"))
def test_wmd_real_source_a_b_a_requires_new_capture_and_preserves_first_entities(tmp_path, monkeypatch, metric):
    import src.config as config
    from src.data.replacement_forecast_live_materialization_queue import _blocked_attempt_fingerprint
    db, registry, primary, bridge, claims, clock = _wmd_setup(tmp_path, monkeypatch)
    original = primary.read_bytes()
    a = _archive(db, "Paris")
    def fingerprint(cut):
        return _blocked_attempt_fingerprint(input_json=tmp_path / "seed.json", forecast_db=db,
            payload={"forecast_db": str(db), "city": "Paris", "target_date": "2026-10-01",
                     "temperature_metric": metric, "source_cycle_time": "2026-09-30T12:00:00Z", "computed_at": cut})
    fp_a = fingerprint(clock[0].isoformat())
    with sqlite3.connect(db) as conn:
        originals = {row[0]: row for row in conn.execute("SELECT * FROM raw_forecast_artifacts")}

    def acquired(body, captured):
        primary.write_bytes(body)
        claim = claims["Paris"]["station_ground_proof"]
        actual = config.station_ground_facts_from_bytes(source_kind=config.OSCAR_WMD_SOURCE_KIND,
            station_id="LFPB", raw_body=body, identity_bridge_bytes=bridge.read_bytes(), effective_at=ground._stamp(captured))
        assert actual is not None
        claim.update(actual, body_sha256=hashlib.sha256(body).hexdigest(),
                     source_checked_at=captured, checked_at=captured)
        registry.write_text(json.dumps(claims))

    # TEST_ONLY controlled newly acquired source B changes the direct facility
    # ground, not nested equipment elevation or AWC airport-reference height.
    b_body = original.replace(b"<gml:pos>48.9675 2.4275 67.0</gml:pos>",
                              b"<gml:pos>48.9675 2.4275 68.0</gml:pos>", 1)
    acquired(b_body, "2026-09-30T01:20:00Z")
    clock[0] = datetime(2026, 9, 30, 1, 30, tzinfo=UTC)
    b = _archive(db, "Paris")
    assert b["facts"]["elevation_m"] == 68
    fp_b = fingerprint(clock[0].isoformat())
    assert fp_b != fp_a
    acquired(original, "2026-09-30T01:00:00Z")  # Merely re-reading old A cannot undo B.
    clock[0] = datetime(2026, 9, 30, 2, tzinfo=UTC)
    assert not ground.archive_station_ground_evidence(db, ["Paris"])["archived"]
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == b
    acquired(original, "2026-09-30T02:20:00Z")  # Genuine later reacquisition, same bytes.
    clock[0] = datetime(2026, 9, 30, 2, 30, tzinfo=UTC)
    confirmation = _archive(db, "Paris")
    assert confirmation["manifest_role"] == "source_capture_confirmation"
    assert confirmation["input_bodies"] == a["input_bodies"]
    assert confirmation["captured_at"] == "2026-09-30T02:20:00+00:00"
    assert confirmation["facts_identity"] == a["facts_identity"]
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T02:29:59Z") == b
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == confirmation
    assert ground.read_frozen_station_ground_evidence(a, decision_at=clock[0]) == a
    assert fingerprint(clock[0].isoformat()) == fp_a
    with sqlite3.connect(db) as conn:
        for artifact_id, row in originals.items():
            assert conn.execute("SELECT * FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone() == row
    clock[0] = datetime(2026, 9, 30, 3, tzinfo=UTC)
    assert _archive(db, "Paris") == confirmation


@pytest.mark.parametrize("part", ("identity_bridge", "manifest"))
def test_wmd_city_local_file_failure_keeps_healthy_single_body_city_and_later_retry_cut(tmp_path, monkeypatch, part):
    from tests.test_config import _official_hko_registry
    db, _, _, _, _, clock = _wmd_setup(tmp_path, monkeypatch)
    _official_hko_registry(tmp_path, monkeypatch)
    writer = ground._write_immutable
    def failed(path, body):
        if path.name.startswith("LFPB.") and (
            (part == "manifest" and path.name.endswith(".manifest.json")) or
            (part == "identity_bridge" and ".identity-bridge." in path.name)):
            raise OSError("controlled WMD city-local storage failure")
        writer(path, body)
    monkeypatch.setattr(ground, "_write_immutable", failed)
    report = ground.archive_station_ground_evidence(db, ["Paris", "Hong Kong"])
    assert set(report["archived"]) == {"Hong Kong"}
    assert "Paris" in report["degraded"]
    assert ground.read_current_station_ground_evidence(db, city="Hong Kong", decision_at=clock[0]) is not None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) is None
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM raw_forecast_artifacts WHERE source_id LIKE '%LFPB%'").fetchone()[0] == 0
    monkeypatch.setattr(ground, "_write_immutable", writer)
    clock[0] = datetime(2026, 9, 30, 2, tzinfo=UTC)
    healed = _archive(db, "Paris")
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at="2026-09-30T01:59:59Z") is None
    assert ground.read_current_station_ground_evidence(db, city="Paris", decision_at=clock[0]) == healed
