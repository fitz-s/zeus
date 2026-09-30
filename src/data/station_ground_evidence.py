"""Immutable official station-ground entities in the existing raw artifact store."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

KIND = "station_ground_source_entity_body_v1"
MANIFEST_KIND = "station_ground_evidence_manifest_v1"
UTC = timezone.utc
_READ_BUDGET_SECONDS = 2.0


def _deadline(external: float | None = None) -> float:
    own = time.monotonic() + _READ_BUDGET_SECONDS
    return own if external is None else min(own, external)


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("STATION_GROUND_EVIDENCE_DEADLINE")


def forecast_db_from_connection(conn: sqlite3.Connection) -> Path | None:
    """Use the caller's actual main database, never a self-claimed cutoff/path."""
    row = next((row for row in conn.execute("PRAGMA database_list") if row[1] == "main"), None)
    return None if row is None or not row[2] else Path(str(row[2])).resolve()


def _stamp(value: object) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("ground evidence clock must be aware")
    return result.astimezone(UTC)


def _encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def ground_facts_identity(facts: Mapping[str, object]) -> str:
    """Only normalized station facts, never page hash, query date or possession."""
    return hashlib.sha256(_encoded(dict(facts))).hexdigest()


def _store_root() -> Path:
    from src.config import state_path
    return state_path("replacement_forecast_live/raw_manifests/station_ground").resolve()


def _write_immutable(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("station ground store refuses symlinks")
    if path.exists():
        if path.read_bytes() == body:
            return
        # Restore exactly the content-addressed canonical bytes, without minting
        # another source capture or altering the existing DB record. Preserve
        # damaged, owned regular files for investigation/recovery.
        if not path.is_file() or path.parent.resolve() != _store_root():
            raise ValueError("station ground immutable path collision")
        damaged = path.with_name(path.name + f".damaged.{time.time_ns()}")
        os.replace(path, damaged)
    fd, temporary = tempfile.mkstemp(prefix=".station-ground-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # Another producer may already have stored these exact content bytes.
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != body:
                raise ValueError("station ground immutable path collision")
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


_BODY_FIELDS = ("source_id", "product_id", "data_version", "source_cycle_time",
    "source_available_at", "captured_at", "recorded_at", "artifact_path", "sha256",
    "byte_size", "request_url", "request_params_json", "training_allowed")


def _body_dependency(conn: sqlite3.Connection, artifact_id: int) -> dict[str, object]:
    if isinstance(artifact_id, bool) or not isinstance(artifact_id, int) or artifact_id <= 0:
        raise ValueError("station ground body artifact identity is invalid")
    row = conn.execute(f"SELECT {','.join(_BODY_FIELDS)},artifact_metadata_json FROM raw_forecast_artifacts WHERE artifact_id=?", (artifact_id,)).fetchone()
    if row is None:
        raise ValueError("station ground body artifact missing")
    return {"artifact_id": artifact_id, **dict(zip(_BODY_FIELDS, tuple(row)[:-1], strict=True)),
        "original_metadata_sha256": hashlib.sha256(str(row[-1]).encode()).hexdigest()}


def _read_body_dependency(dependency: Mapping[str, object], *, conn: sqlite3.Connection, decision: datetime) -> bytes:
    if isinstance(dependency.get("artifact_id"), bool) or not isinstance(dependency.get("artifact_id"), int):
        raise ValueError("station ground body artifact identity is invalid")
    if _body_dependency(conn, int(dependency["artifact_id"])) != dependency:
        raise ValueError("station ground body DB identity differs")
    capture, available, recorded, cycle = (_stamp(dependency[key]) for key in
        ("captured_at", "source_available_at", "recorded_at", "source_cycle_time"))
    if not capture == available == cycle or not capture <= recorded <= decision:
        raise ValueError("station ground body original clocks differ")
    path = Path(str(dependency["artifact_path"]))
    if path.parent.resolve() != _store_root() or path.is_symlink() or not path.is_file() or path.stat().st_size > 256*1024:
        raise ValueError("station ground body owned path differs")
    body = path.read_bytes()
    if len(body) != dependency["byte_size"] or hashlib.sha256(body).hexdigest() != dependency["sha256"] or dependency["training_allowed"] != 0:
        raise ValueError("station ground body bytes differ")
    return body


def _latest_candidate(conn, station_id, decision, deadline):
    rows = conn.execute("SELECT artifact_id,artifact_metadata_json,captured_at,recorded_at FROM raw_forecast_artifacts WHERE source_id=? AND data_version IN (?,?)", (f"station_ground::{station_id}", KIND, MANIFEST_KIND))
    latest = None
    while batch := rows.fetchmany(32):
        _check(deadline)
        for artifact_id, metadata, captured_at, recorded_at in batch:
            recorded = _stamp(recorded_at)
            if recorded > decision:
                continue
            captured = _stamp(captured_at)
            if captured > recorded:
                raise ValueError("ground source capture is after canonical possession")
            # Actual source event precedes canonical write order. A delayed
            # old response cannot roll back a newer source snapshot. Metadata
            # is deliberately not parsed until this candidate is selected.
            candidate = (captured, recorded, int(artifact_id), metadata)
            if latest is None or candidate[:3] > latest[:3]:
                latest = candidate
    return latest


def _candidate_evidence(candidate, now, deadline):
    if candidate is None:
        return None
    try:
        evidence = json.loads(str(candidate[3]))["station_ground_evidence"]
        return read_frozen_station_ground_evidence(evidence, decision_at=now, deadline_monotonic=deadline)
    except (KeyError, TypeError, ValueError):
        return None


def _archive_manifest(conn, prepared, old_id, deadline, *, role, prior):
    """Append a typed confirmation/recovery; the original body stays immutable."""
    from src.config import station_ground_facts_from_bytes
    name, source_kind, station_id, facts, audit, digest, body, body_path, captured = prepared
    now = datetime.now(UTC)
    dependency = _body_dependency(conn, int(old_id))
    original_body = _read_body_dependency(dependency, conn=conn, decision=now)
    source_id, product_id = f"station_ground::{station_id}", f"station_ground::{source_kind}::{station_id}"
    if (original_body != body or dependency["sha256"] != digest
        or dependency["source_id"] != source_id or dependency["product_id"] != product_id
        or dependency["data_version"] != KIND or dependency["request_url"] != facts["source_url"]
        or json.loads(str(dependency["request_params_json"])) != {"source_kind": source_kind, "station_id": station_id}
        or station_ground_facts_from_bytes(source_kind=source_kind, station_id=station_id, raw_body=original_body) != facts):
        raise ValueError("ground manifest original entity is unbound")
    if captured < _stamp(dependency["captured_at"]) or (prior is not None and captured < prior[0]):
        raise ValueError("old source capture cannot restore newer ground evidence")
    previous = _body_dependency(conn, prior[2] if prior is not None else int(old_id))
    original = captured.isoformat()
    payload = {"revision": MANIFEST_KIND, "source_id": source_id, "product_id": product_id,
        "source_kind": source_kind, "station_id": station_id, "body_sha256": digest,
        "byte_size": len(body), "body_path": str(body_path), "source_url": facts["source_url"],
        "approved_artifact_ref": audit["artifact_ref"], "source_cycle_time": original,
        "source_cycle_role": "ground_snapshot_capture_not_forecast_issued", "source_available_at": original,
        "captured_at": original, "recorded_at": now.isoformat(), "facts": facts,
        "facts_identity": ground_facts_identity(facts), "source_audit": audit,
        "forecast_db": str(Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()),
        "input_bodies": {"ground": dependency}, "manifest_role": role,
        "previous_source_evidence": previous}
    if role == "canonical_metadata_recovery":
        payload["recovery_of"] = {"artifact_id": previous["artifact_id"],
            "invalid_metadata_sha256": previous["original_metadata_sha256"]}
    elif role != "source_capture_confirmation" or captured <= _stamp(previous["captured_at"]):
        raise ValueError("ground confirmation requires a genuinely newer source capture")
    manifest = _encoded(payload)
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    manifest_path = _store_root() / f"{station_id}.{manifest_sha}.manifest.json"
    _write_immutable(manifest_path, manifest)
    _check(deadline)
    cursor = conn.execute("""INSERT INTO raw_forecast_artifacts
        (source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
         artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at,training_allowed)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'{}',?,0)""", (source_id, product_id, MANIFEST_KIND,
        original, original, original, str(manifest_path), manifest_sha, len(manifest), facts["source_url"],
        json.dumps({"source_kind": source_kind, "station_id": station_id}, sort_keys=True), payload["recorded_at"]))
    evidence = {**payload, "artifact_id": int(cursor.lastrowid), "manifest_path": str(manifest_path), "manifest_sha256": manifest_sha}
    conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=? WHERE artifact_id=? AND artifact_metadata_json='{}'",
        (json.dumps({"station_ground_evidence": evidence}, sort_keys=True), cursor.lastrowid))
    return evidence


def _archive_entity(conn, prepared, forecast_db, deadline):
    name, source_kind, station_id, facts, audit, digest, body, body_path, captured = prepared
    source_id, product_id = f"station_ground::{station_id}", f"station_ground::{source_kind}::{station_id}"
    old = conn.execute("SELECT artifact_id,artifact_metadata_json FROM raw_forecast_artifacts WHERE source_id=? AND product_id=? AND data_version=? AND sha256=? ORDER BY artifact_id LIMIT 1", (source_id, product_id, KIND, digest)).fetchone()
    if old is not None:
        now = datetime.now(UTC)
        latest = _latest_candidate(conn, station_id, now, deadline)
        latest_evidence = _candidate_evidence(latest, now, deadline)
        try:
            evidence = json.loads(str(old[1]))["station_ground_evidence"]
            manifest = _encoded({key:value for key,value in evidence.items() if key not in {"manifest_path", "manifest_sha256"}})
            manifest_path = Path(evidence["manifest_path"])
            if manifest_path.parent.resolve() != _store_root() or hashlib.sha256(manifest).hexdigest() != evidence["manifest_sha256"]:
                raise ValueError("station ground canonical manifest identity differs")
            _write_immutable(manifest_path, manifest)
            if read_frozen_station_ground_evidence(evidence, decision_at=now, deadline_monotonic=deadline) is None:
                raise ValueError("station ground canonical entity is invalid")
        except (KeyError, TypeError, ValueError):
            if latest_evidence is not None and latest_evidence["facts"] == facts:
                return latest_evidence
            return _archive_manifest(conn, prepared, old[0], deadline,
                role="canonical_metadata_recovery", prior=latest)
        # Exact canonical file restoration above can make this very same
        # immutable candidate readable again; it does not create possession.
        latest_evidence = _candidate_evidence(latest, now, deadline)
        if latest_evidence is not None and latest_evidence["facts"] == facts:
            return latest_evidence  # same facts/capture polling never mints an event
        if latest is None:
            return evidence
        if latest_evidence is None and captured < latest[0]:
            raise ValueError("old source capture cannot hide invalid latest ground evidence")
        if captured < latest[0] or (latest_evidence is not None and captured == latest[0]):
            return evidence  # old config/source response cannot wash a newer transition
        return _archive_manifest(conn, prepared, old[0], deadline,
            role="source_capture_confirmation" if latest_evidence is not None else "canonical_metadata_recovery", prior=latest)
    recorded, original = datetime.now(UTC).isoformat(), captured.isoformat()
    cursor = conn.execute("""INSERT INTO raw_forecast_artifacts
        (source_id,product_id,data_version,source_cycle_time,source_available_at,captured_at,
         artifact_path,sha256,byte_size,request_url,request_params_json,artifact_metadata_json,recorded_at,training_allowed)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'{}',?,0)""", (source_id, product_id, KIND, original, original,
        original, str(body_path), digest, len(body), facts["source_url"],
        json.dumps({"source_kind": source_kind, "station_id": station_id}, sort_keys=True), recorded))
    evidence = {"revision": KIND, "artifact_id": int(cursor.lastrowid), "source_id": source_id,
        "product_id": product_id, "source_kind": source_kind, "station_id": station_id,
        "body_sha256": digest, "byte_size": len(body), "body_path": str(body_path),
        "source_url": facts["source_url"], "approved_artifact_ref": audit["artifact_ref"],
        "source_cycle_time": original, "source_cycle_role": "ground_snapshot_capture_not_forecast_issued",
        "source_available_at": original, "captured_at": original, "recorded_at": recorded,
        "facts": facts, "facts_identity": ground_facts_identity(facts), "source_audit": audit,
        "forecast_db": str(Path(forecast_db).resolve())}
    manifest = _encoded(evidence)
    manifest_sha = hashlib.sha256(manifest).hexdigest()
    manifest_path = _store_root() / f"{station_id}.{digest}.{manifest_sha}.manifest.json"
    _write_immutable(manifest_path, manifest)
    evidence.update(manifest_path=str(manifest_path), manifest_sha256=manifest_sha)
    # Only this newly inserted, still-uncommitted row is completed. Never amend
    # an existing body, broken metadata, raw value or any source clock.
    conn.execute("UPDATE raw_forecast_artifacts SET artifact_metadata_json=? WHERE artifact_id=? AND artifact_metadata_json='{}'",
        (json.dumps({"station_ground_evidence": evidence}, sort_keys=True), cursor.lastrowid))
    return evidence


def archive_station_ground_evidence(
    forecast_db: Path, cities: Sequence[str], *, deadline_monotonic: float | None = None,
) -> Mapping[str, object]:
    """Normal producer archives already acquired official bytes before seeds.

    No network request occurs. Original audited source capture and first
    canonical storage possession remain separate. Files are completed before
    committed DB references; rollback leftovers have no authorizing DB row.
    """
    from src.config import CONFIG_DIR, runtime_cities_by_name, runtime_station_geometry_for_city, station_ground_source_artifact_ref, station_ground_facts_from_bytes
    from src.state.db import _connect
    deadline = _deadline(deadline_monotonic)
    prepared, degraded = [], {}
    roster = runtime_cities_by_name()
    for name in sorted(set(cities)):
        _check(deadline)
        city = roster.get(name)
        if city is None:
            continue
        station = runtime_station_geometry_for_city(city)
        facts, audit = station.get("ground_facts"), station.get("ground_audit")
        if station.get("ground_status") != "VERIFIED" or not isinstance(facts, Mapping) or not isinstance(audit, Mapping):
            continue
        source_kind, station_id = str(facts["source_kind"]), str(facts["station_id"])
        ref = station_ground_source_artifact_ref(source_kind=source_kind, station_id=station_id)
        if ref is None or ref != audit.get("artifact_ref"):
            continue
        path = CONFIG_DIR / Path(ref).name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256*1024:
            continue
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != audit.get("body_sha256") or station_ground_facts_from_bytes(source_kind=source_kind, station_id=station_id, raw_body=body) != facts:
            continue
        captured = _stamp(audit["checked_at"])
        if captured > datetime.now(UTC):
            continue
        body_path = _store_root() / f"{station_id}.{digest}.body"
        try:
            _write_immutable(body_path, body)
        except (OSError, ValueError) as exc:
            degraded[name] = f"{type(exc).__name__}:{exc}"
            continue
        prepared.append((name, source_kind, station_id, dict(facts), dict(audit), digest, body, body_path, captured))
    if not prepared:
        return {"archived": {}, "degraded": degraded, "status": "GROUND_SOURCE_UNPROVEN"}
    conn = _connect(Path(forecast_db), write_class="live", deadline_monotonic=deadline)
    archived = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for item in prepared:
            _check(deadline)
            name = item[0]
            conn.execute("SAVEPOINT station_ground_entity")
            try:
                archived[name] = _archive_entity(conn, item, forecast_db, deadline)
            except (KeyError, TypeError, ValueError, OSError) as exc:
                conn.execute("ROLLBACK TO SAVEPOINT station_ground_entity")
                degraded[name] = f"{type(exc).__name__}:{exc}"
            finally:
                conn.execute("RELEASE SAVEPOINT station_ground_entity")
        conn.commit()
        return {"archived":archived,"degraded": degraded,
            "status":"GROUND_SOURCE_ARCHIVED" if archived else "GROUND_SOURCE_UNPROVEN"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_frozen_station_ground_evidence(evidence: object, *, decision_at: object, deadline_monotonic: float | None = None) -> Mapping[str, object] | None:
    """Reproduce this immutable body's own clocks/facts, not the newest page."""
    from src.config import station_ground_source_artifact_ref, station_ground_facts_from_bytes
    from src.state.db import _connect_read_only
    try:
        if not isinstance(evidence, Mapping) or evidence.get("revision") not in {KIND, MANIFEST_KIND}:
            return None
        if evidence.get("revision") == MANIFEST_KIND:
            return _read_manifest_evidence(evidence, decision_at=decision_at, deadline_monotonic=deadline_monotonic)
        deadline = _deadline(deadline_monotonic)
        decision = _stamp(decision_at)
        manifest_path, body_path = Path(str(evidence["manifest_path"])), Path(str(evidence["body_path"]))
        if manifest_path.parent.resolve() != _store_root() or body_path.parent.resolve() != _store_root() or manifest_path.is_symlink() or body_path.is_symlink():
            return None
        if manifest_path.stat().st_size > 32*1024 or body_path.stat().st_size > 256*1024:
            return None
        manifest = manifest_path.read_bytes()
        if hashlib.sha256(manifest).hexdigest() != evidence["manifest_sha256"] or not manifest_path.name.endswith(f".{evidence['manifest_sha256']}.manifest.json"):
            return None
        frozen = json.loads(manifest)
        expected = {key:value for key,value in evidence.items() if key not in {"manifest_path","manifest_sha256"}}
        if frozen != expected or evidence["approved_artifact_ref"] != station_ground_source_artifact_ref(source_kind=str(evidence["source_kind"]),station_id=str(evidence["station_id"])):
            return None
        if (evidence["source_id"] != f"station_ground::{evidence['station_id']}"
            or evidence["product_id"] != f"station_ground::{evidence['source_kind']}::{evidence['station_id']}"
            or evidence["source_cycle_role"] != "ground_snapshot_capture_not_forecast_issued"
            or evidence["source_audit"]["body_sha256"] != evidence["body_sha256"]
            or evidence["source_audit"]["artifact_ref"] != evidence["approved_artifact_ref"]
            or isinstance(evidence["artifact_id"], bool) or int(evidence["artifact_id"]) <= 0):
            return None
        captured, available, recorded = (_stamp(evidence[key]) for key in ("captured_at","source_available_at","recorded_at"))
        if not captured == available == _stamp(evidence["source_cycle_time"]) == _stamp(evidence["source_audit"]["checked_at"]) or not captured <= recorded <= decision:
            return None
        body = body_path.read_bytes()  # One immutable snapshot for hash and parse.
        if len(body) != evidence["byte_size"] or hashlib.sha256(body).hexdigest() != evidence["body_sha256"]:
            return None
        facts = station_ground_facts_from_bytes(source_kind=str(evidence["source_kind"]),station_id=str(evidence["station_id"]),raw_body=body)
        if facts != evidence["facts"] or facts is None or ground_facts_identity(facts) != evidence["facts_identity"]:
            return None
        if facts["station_id"] != evidence["station_id"] or facts["source_kind"] != evidence["source_kind"] or facts["source_url"] != evidence["source_url"]:
            return None
        _check(deadline)
        conn = _connect_read_only(Path(str(evidence["forecast_db"])), deadline_monotonic=deadline)
        try:
            row = conn.execute("""SELECT source_id,product_id,data_version,sha256,captured_at,
                source_available_at,recorded_at,artifact_metadata_json,source_cycle_time,
                artifact_path,byte_size,request_url,request_params_json,training_allowed
                FROM raw_forecast_artifacts WHERE artifact_id=?""", (evidence["artifact_id"],)).fetchone()
        finally:
            conn.close()
        if row is None or tuple(row[:7]) != (evidence["source_id"],evidence["product_id"],KIND,evidence["body_sha256"],evidence["captured_at"],evidence["source_available_at"],evidence["recorded_at"]):
            return None
        if json.loads(str(row[7])) != {"station_ground_evidence":dict(evidence)}:
            return None
        if tuple(row[8:12]) != (evidence["source_cycle_time"], evidence["body_path"], evidence["byte_size"], evidence["source_url"]):
            return None
        if json.loads(str(row[12])) != {"source_kind": evidence["source_kind"], "station_id": evidence["station_id"]} or row[13] != 0:
            return None
        return dict(evidence)
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, TimeoutError):
        return None


def _read_manifest_evidence(evidence, *, decision_at, deadline_monotonic=None):
    from src.config import station_ground_facts_from_bytes, station_ground_source_artifact_ref
    from src.state.db import _connect_read_only
    deadline = _deadline(deadline_monotonic)
    decision = _stamp(decision_at)
    path = Path(str(evidence["manifest_path"]))
    if path.parent.resolve() != _store_root() or path.is_symlink() or not path.is_file() or path.stat().st_size > 32*1024:
        return None
    manifest = path.read_bytes()
    if hashlib.sha256(manifest).hexdigest() != evidence["manifest_sha256"] or path.name != f"{evidence['station_id']}.{evidence['manifest_sha256']}.manifest.json":
        return None
    payload = json.loads(manifest)
    if payload != {key:value for key,value in evidence.items() if key not in {"artifact_id", "manifest_path", "manifest_sha256"}}:
        return None
    if (evidence["approved_artifact_ref"] != station_ground_source_artifact_ref(source_kind=evidence["source_kind"], station_id=evidence["station_id"])
        or _stamp(evidence["source_audit"]["checked_at"]) > _stamp(evidence["recorded_at"])
        or not _stamp(evidence["captured_at"]) <= _stamp(evidence["recorded_at"]) <= decision
        or evidence["source_id"] != f"station_ground::{evidence['station_id']}"
        or evidence["product_id"] != f"station_ground::{evidence['source_kind']}::{evidence['station_id']}"
        or evidence["source_cycle_role"] != "ground_snapshot_capture_not_forecast_issued"
        or evidence["source_audit"]["body_sha256"] != evidence["body_sha256"]
        or evidence["source_audit"]["artifact_ref"] != evidence["approved_artifact_ref"]
        or isinstance(evidence["artifact_id"], bool) or not isinstance(evidence["artifact_id"], int)):
        return None
    capture = _stamp(evidence["captured_at"])
    if not capture == _stamp(evidence["source_cycle_time"]) == _stamp(evidence["source_available_at"]):
        return None
    _check(deadline)
    conn = _connect_read_only(Path(str(evidence["forecast_db"])), deadline_monotonic=deadline)
    try:
        row = conn.execute(f"SELECT {','.join(_BODY_FIELDS)},artifact_metadata_json FROM raw_forecast_artifacts WHERE artifact_id=?", (evidence["artifact_id"],)).fetchone()
        expected = (evidence["source_id"], evidence["product_id"], MANIFEST_KIND, evidence["source_cycle_time"],
            evidence["source_available_at"], evidence["captured_at"], evidence["recorded_at"], str(path),
            evidence["manifest_sha256"], len(manifest), evidence["source_url"],
            json.dumps({"source_kind": evidence["source_kind"], "station_id": evidence["station_id"]}, sort_keys=True), 0)
        if row is None or tuple(row)[:-1] != expected or json.loads(str(row[-1])) != {"station_ground_evidence": dict(evidence)}:
            return None
        bodies = evidence["input_bodies"]
        if set(bodies) != {"ground"}:
            return None  # WMD identity bridge has its own separately approved branch.
        dependency = bodies["ground"]
        body = _read_body_dependency(dependency, conn=conn, decision=decision)
        if (dependency["source_id"] != evidence["source_id"] or dependency["product_id"] != evidence["product_id"]
            or dependency["data_version"] != KIND or dependency["sha256"] != evidence["body_sha256"]
            or dependency["artifact_path"] != evidence["body_path"] or dependency["byte_size"] != evidence["byte_size"]
            or dependency["request_url"] != evidence["source_url"]
            or _stamp(dependency["recorded_at"]) > _stamp(evidence["recorded_at"])
            or _stamp(dependency["captured_at"]) > capture
            or json.loads(str(dependency["request_params_json"])) != {"source_kind": evidence["source_kind"], "station_id": evidence["station_id"]}
        ):
            return None
        role = evidence.get("manifest_role")
        if role is None:
            # Exact old metadata-recovery manifests keep their original clocks;
            # this is not compatibility for an unbound entity or new capture.
            if dependency["captured_at"] != evidence["captured_at"] or evidence["recovery_of"] != {"artifact_id": dependency["artifact_id"], "invalid_metadata_sha256": dependency["original_metadata_sha256"]}:
                return None
        else:
            previous = evidence["previous_source_evidence"]
            if (_body_dependency(conn, previous["artifact_id"]) != previous
                or previous["source_id"] != evidence["source_id"]
                or previous["product_id"] != evidence["product_id"]
                or previous["data_version"] not in {KIND, MANIFEST_KIND}
                or not _stamp(previous["captured_at"]) <= _stamp(previous["recorded_at"]) <= _stamp(evidence["recorded_at"])
                or _stamp(previous["captured_at"]) > capture
                or capture != _stamp(evidence["source_audit"]["checked_at"])):
                return None
            if role == "source_capture_confirmation":
                if "recovery_of" in evidence or capture <= _stamp(previous["captured_at"]):
                    return None
            elif role == "canonical_metadata_recovery":
                if evidence["recovery_of"] != {"artifact_id": previous["artifact_id"], "invalid_metadata_sha256": previous["original_metadata_sha256"]}:
                    return None
            else:
                return None
        facts = station_ground_facts_from_bytes(source_kind=evidence["source_kind"], station_id=evidence["station_id"], raw_body=body)
        if facts != evidence["facts"] or facts is None or ground_facts_identity(facts) != evidence["facts_identity"]:
            return None
        return dict(evidence)
    finally:
        conn.close()


def read_current_station_ground_evidence(forecast_db: Path, *, city: str, decision_at: object) -> Mapping[str, object] | None:
    """Read the latest causal canonical source entity; no network or file writes."""
    from src.config import runtime_cities_by_name
    from src.state.db import _connect_read_only
    cfg = runtime_cities_by_name().get(city)
    if cfg is None:
        return None
    from src.config import runtime_station_geometry_for_city
    station_id = str(runtime_station_geometry_for_city(cfg).get("station_id"))
    try:
        cutoff = _stamp(decision_at)
    except (ValueError, TypeError):
        return None
    deadline = _deadline()
    conn = None
    try:
        conn = _connect_read_only(Path(forecast_db), deadline_monotonic=deadline)
        latest = _latest_candidate(conn, station_id, cutoff, deadline)
        _check(deadline)
        evidence = None if latest is None else json.loads(str(latest[3]))["station_ground_evidence"]
    except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, TimeoutError):
        return None
    finally:
        if conn is not None:
            conn.close()
    return read_frozen_station_ground_evidence(evidence, decision_at=decision_at, deadline_monotonic=deadline)
