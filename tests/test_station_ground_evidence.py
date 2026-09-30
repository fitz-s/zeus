# Lifecycle: created=2026-09-29; last_reviewed=2026-09-29; last_reused=2026-09-29
# Purpose: Canonical immutable ground entities, causal possession and facts-only RESET.
# Reuse: pytest tests/test_station_ground_evidence.py
# Authority basis: replacement_final_form §1d205–215; AGENTS §0/§2 INV-14/INV-47.
from __future__ import annotations

import hashlib
import json
import sqlite3
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


@pytest.mark.parametrize("mutation", ("artifact_id", "foreign_station", "capture", "facts", "body_symlink", "manifest_symlink", "db_metadata"))
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
    with pytest.raises(OSError, match="manifest disk failure"):
        _archive(db)
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
