# Created: prior; restructured 2026-05-01
# Last reused or audited: 2026-10-06
# Authority basis: architect D1 (ECMWF throttle), AGENTS.md money path
#   Prior: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md
#   ECMWF Open Data has ~6-8h latency (vs. TIGGE's 48h public embargo) so it
#   is the live-trading source for same-day forecasts. Rows must land in
#   ensemble_snapshots with the canonical local-calendar-day data_version
#   so calibration / day0 / opening_hunt readers can consume them alongside
#   TIGGE archive rows via the data_version priority list.
"""Collect ECMWF Open Data ENS member vectors into ensemble_snapshots.

Replaces the legacy 2t-instantaneous + ensemble_snapshots (v1) write path.

Pipeline
--------
1. Download single GRIB containing all 51 members × 71 step hours for the
   requested run (mx2t6 OR mn2t6 per call) via in-process parallel SDK
   fetches at per-step file granularity (``_fetch_one_step`` +
   ``ThreadPoolExecutor(max_workers=5)``), concatenated on success.
   Refactored 2026-05-11 per PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md.
2. Run ``51 source data/scripts/extract_open_ens_localday.py`` to produce
   per-(city, target_local_date, lead_day) JSON records that conform to the
   TiggeSnapshotPayload contract, using a content-addressed runtime station
   coordinate manifest instead of the external package's city coordinates.
3. Reuse the zeus repo's ``scripts/ingest_grib_to_snapshots.ingest_track``
   ingester (importable) which validates against the canonical contract,
   asserts the dataset_id is allow-listed, and writes the row to
   ``ensemble_snapshots`` with manifest_hash + provenance_json + members_unit.

Data version
------------
HIGH: ``ecmwf_opendata_mx2t6_local_calendar_day_max_v1``
LOW : ``ecmwf_opendata_mn2t6_local_calendar_day_min_v1``

Note on params (2026-05-07)
---------------------------
ECMWF Open Data ``enfo`` stream deprecated ``mx2t6``/``mn2t6`` (6h aggregations).
Fetch now uses ``mx2t3``/``mn2t3`` (3h native) per authority doc
``architecture/zeus_grid_resolution_authority_2026_05_07.yaml`` A1+3h.
Step list is 3h-stride (3, 6, 9 … 240). Data versions are unchanged;
calibration learns the 3h→6h envelope mapping downstream.

These data_versions are added to ``CANONICAL_ENSEMBLE_DATA_VERSIONS`` in
``src/contracts/ensemble_snapshot_provenance.py``. The TIGGE archive
``tigge_*_v1`` data_versions remain valid alongside.
"""
from __future__ import annotations

import json
import logging
import math
import multiprocessing
import os
import re
import shutil
import selectors
import sqlite3
import struct
import subprocess
import sys
import hashlib
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import requests

from src.config import PROJECT_ROOT, runtime_cities_by_name, runtime_coordinate_manifest_json
from src.contracts.availability_time import proof_of_possession_available_at
from src.contracts.ensemble_snapshot_provenance import (
    opendata_source_run_revision_suffix,
    ECMWF_OPENDATA_HIGH_DATA_VERSION,
    ECMWF_OPENDATA_LOW_DATA_VERSION,
    ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
    TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
    coordinate_bound_data_version,
)
from src.data.forecast_target_contract import (
    OPENDATA_MAX_STEP_HOURS,
    build_forecast_target_scope,
    evaluate_horizon_coverage,
    evaluate_producer_coverage,
)
from src.data.forecast_extrema_authority import (
    CURRENT_EVIDENCE_ELIGIBILITIES,
    classify_forecast_extrema_authority,
    member_interval_bounds_from_row,
)
from src.data.producer_readiness import build_producer_readiness_for_scope
from src.data.forecast_source_registry import gate_source, gate_source_role
from src.data.release_calendar import FetchDecision, get_entry, select_source_run_for_target_horizon
from src.state.db import (
    ZEUS_FORECASTS_DB_PATH,
    assert_schema_current_forecasts,
    get_forecasts_connection as get_connection,
    get_forecasts_connection_read_only,
)
from src.state.db_writer_lock import WriteClass, db_writer_lock
from src.state.source_run_coverage_repo import write_source_run_coverage
from src.state.source_run_repo import get_source_run, write_source_run

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenDataPaths:
    raw_root: Path
    asset_root: Path
    extract_script: Path
    manifest_path: Path
    origin: str

_NATIVE_2T_QUANTITY = "native_2m_temperature_instantaneous_knots"
_NATIVE_2T_VERSION = "ecmwf_ifs50r1_2t_native_source_v1"


@dataclass(frozen=True)
class NativeTemperatureSource:
    status: str
    source_run_id: str | None
    observed_count: int
    missing_member_steps: tuple[tuple[int, int], ...]
    reason: str | None = None


@dataclass(frozen=True)
class NativeTemperatureScope:
    status: str
    native_knots: tuple[dict, ...] = ()
    available_at: str | None = None
    temperature_first_possession_at: str | None = None
    reason: str | None = None
    quantity_role: str = _NATIVE_2T_QUANTITY
    temporal_representation: str = "acquired_native_knots"
    qualification_status: str = "OFFLINE_ONLY"
    projection_status: str = "NOT_PERFORMED"
    extrema_status: str = "NOT_COMPUTED"
    static_validity_status: str = "UNKNOWN"
    temperature_scope_first_possession_at: str | None = None
    physical_dependency_available_at: str | None = None
    pit_status: str = "UNKNOWN"
    physical_witness: dict | None = None
    scope_role: str = "remaining_X"
    temperature_metric: str | None = None


def _native_temperature_steps(run: datetime, steps: list[int]) -> list[int]:
    if (run.tzinfo is None or run.utcoffset() != timedelta(0) or run.hour not in (0, 6, 12, 18)
            or run.minute or run.second or run.microsecond or run > datetime.now(timezone.utc)):
        raise ValueError("NATIVE_2T_RUN_INVALID")
    horizon = 240 if run.hour in (0, 12) else 90
    if (not steps or len(set(steps)) != len(steps)
            or any(type(s) is not int or s < 0 or s > horizon
                   or s % (3 if s <= 144 else 6) for s in steps)):
        raise ValueError("NATIVE_2T_NATIVE_STEPS_INVALID")
    return sorted(steps)


def _read_native_temperature_record(path: Path, run: datetime) -> tuple[dict, bytes]:
    """Revalidate retained originals; metadata echoes never replace GRIB sections."""
    import base64
    import eccodes as ec
    from urllib.parse import urlsplit
    from scripts import extract_open_ens_localday as decoder

    raw = path.read_bytes()
    proof_path = path.with_suffix(".grib2.proof.json")
    proof_bytes = proof_path.read_bytes()
    proof = json.loads(proof_bytes)
    member, step = proof["member"], proof["step_hours"]
    if type(member) is not int or not 0 <= member <= 50:
        raise ValueError("NATIVE_2T_MEMBER_INVALID")
    _native_temperature_steps(run, [step])
    if (raw[:4] != b"GRIB" or raw[-4:] != b"7777" or len(raw) < 20 or raw[7] != 2
            or int.from_bytes(raw[8:16], "big") != len(raw)):
        raise ValueError("NATIVE_2T_MESSAGE_FRAMING_INVALID")
    envelope = urlsplit(proof["source_url"])
    if (envelope.hostname not in ("data.ecmwf.int", "ecmwf-forecasts.s3.eu-central-1.amazonaws.com")
            and not (envelope.hostname == "storage.googleapis.com"
                     and envelope.path.startswith("/ecmwf-open-data/"))):
        raise ValueError("NATIVE_2T_SOURCE_HOST_INVALID")
    if envelope.username or envelope.password or envelope.query or envelope.fragment:
        raise ValueError("NATIVE_2T_SOURCE_HOST_INVALID")
    if proof["range_http"].get("status") != 206:
        raise ValueError("NATIVE_2T_RANGE_NOT_206")
    headers = proof["range_http"]["headers"]
    span = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers["Content-Range"])
    if not span:
        raise ValueError("NATIVE_2T_RANGE_RECEIPT_INVALID")
    start, end, total = map(int, span.groups())
    if (not 0 <= start <= proof["source_index_offset"]
            or end < proof["source_index_offset"] + len(raw) - 1 or end >= total
            or int(headers["Content-Length"]) != end - start + 1):
        raise ValueError("NATIVE_2T_RANGE_RECEIPT_INVALID")
    index_path = path.parent / proof["index_path"]
    if not index_path.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError("NATIVE_2T_INDEX_OUTSIDE_CACHE")
    index = index_path.read_bytes()
    if proof.get("ingest_mode") == "SCHEDULED_LIVE":
        receipt_bytes = index_path.with_suffix(".http.json").read_bytes()
        receipt = json.loads(receipt_bytes)
        started = datetime.fromisoformat(receipt["fetch_started_at"])
        received = datetime.fromisoformat(receipt["source_fetched_at"])
        fetched = datetime.fromisoformat(proof["source_fetched_at"])
        if (hashlib.sha256(receipt_bytes).hexdigest() != proof["index_receipt_sha256"]
                or receipt["source_url"] != proof["source_url"]
                or receipt["source_index_url"] != proof["source_index_url"]
                or receipt["http"]["status"] != 200
                or any(clock.tzinfo is None for clock in (started, received, fetched))
                or not run <= started <= received <= fetched <= datetime.now(timezone.utc)
                or proof.get("qualification_status") != "UNKNOWN"
                or proof.get("source_issued_at") is not None):
            raise ValueError("NATIVE_2T_ORIGINAL_INDEX_RECEIPT_INVALID")
    gid = ec.codes_new_from_message(raw)
    try:
        capture = decoder._native_message_capture(gid, instantaneous=True)
        if capture["capture_status"] != "OBSERVED":
            raise ValueError("NATIVE_2T_CAPTURE_UNKNOWN")
        h = capture["observed_headers"]
        sections = {s["section_number"]: base64.b64decode(s["bytes_base64"], validate=True)
                    for s in capture["metadata_sections"]}
        s1, s3, s4 = sections[1], sections[3], sections[4]
        valid = run + timedelta(hours=step)
        if (h["paramId"] != 167 or h["shortName"] != "2t" or h["units"] != "K"
                or h["typeOfLevel"] != "heightAboveGround" or h["level"] != 2
                or h["stepType"] != "instant" or h["stepUnits"] != 1
                or h["startStep"] != step or h["endStep"] != step or str(h["stepRange"]) != str(step)
                or h["centre"] != "ecmf" or h["generatingProcessIdentifier"] != 161
                or h["dataType"] != ("fc" if member == 0 else "pf")
                or h.get("number", 0) != member
                or (h["dataDate"], h["dataTime"]) != (int(run.strftime("%Y%m%d")), run.hour * 100)
                or (h["validityDate"], h["validityTime"]) != (int(valid.strftime("%Y%m%d")), valid.hour * 100)
                or raw[6] != 0 or s4[9:11] != b"\x00\x00"
                or int.from_bytes(s1[5:7], "big") != 98
                or int.from_bytes(s1[12:14], "big") != run.year
                or tuple(s1[14:19]) != (run.month, run.day, run.hour, 0, 0)
                or s1[20] != (1 if member == 0 else 4)
                or int.from_bytes(s4[7:9], "big") != (0 if member == 0 else 1)
                or h["productDefinitionTemplateNumber"] != (0 if member == 0 else 1)
                or s4[13] != 161 or s4[11] != h["typeOfGeneratingProcess"]
                or h["typeOfGeneratingProcess"] != (2 if member == 0 else 4)
                or s4[17] != 1 or int.from_bytes(s4[18:22], "big") != step
                or s4[22] != 103 or s4[23] != 0 or int.from_bytes(s4[24:28], "big") != 2
                or (member and s4[35] != member)
                or decoder._open_ens_original_grid(s3) != {k: h[k] for k in decoder._GRID_KEYS}):
            raise ValueError("NATIVE_2T_ORIGINAL_IDENTITY_MISMATCH")
        binding = decoder._open_ens_source_binding(raw, {**proof, "original_index_bytes": index,
            "original_range_bytes": raw}, h, param="2t", member=member, step=step, run=run)
        return {**binding, "member": member, "step_hours": step, "valid_time_utc": valid.isoformat(),
            "path": str(path.resolve()), "proof_sha256": hashlib.sha256(proof_bytes).hexdigest(),
            "grid_sha256": hashlib.sha256(s3).hexdigest(),
            "observed_headers": h,
            "original_section_sha256": {str(n): hashlib.sha256(body).hexdigest() for n, body in sections.items()},
            "process_type": h["typeOfGeneratingProcess"]}, raw
    finally:
        ec.codes_release(gid)


def persist_native_temperature_source_run(conn: sqlite3.Connection, *, cache_dir: Path,
        manifest_path: Path, expected_run_utc: datetime, product_steps: list[int],
        ingest_mode: str = "ARCHIVE_BACKFILL") -> NativeTemperatureSource:
    """Source inventory only; caller owns the FORECAST_CLASS transaction.

    No HTTP, schedule, source activation, snapshots, hourly vectors or q writes.
    SCOPE: stable product/run/original-grid. DRAIN: normal cadence may retain the
    missing original ranges; this function only inventories them. RESET: exact
    proofs are rechecked on every call, without renewing retained possession clocks.
    A product's expected steps may grow, never shrink with a city/cut scope view.
    """
    steps = _native_temperature_steps(expected_run_utc, product_steps)
    if ingest_mode not in {"ARCHIVE_BACKFILL", "SCHEDULED_LIVE"}:
        raise ValueError("NATIVE_2T_INGEST_ROLE_INVALID")
    if conn.in_transaction:
        raise ValueError("NATIVE_2T_REQUIRES_OWN_TRANSACTION")
    previous_bytes = manifest_path.read_bytes() if manifest_path.exists() else None
    previous = json.loads(previous_bytes) if previous_bytes else None
    if previous and previous.get("ingest_mode", "ARCHIVE_BACKFILL") != ingest_mode:
        raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
    if previous:
        previous_row = get_source_run(conn, previous["source_run_id"])
        if not previous_row or previous_row["manifest_hash"] != hashlib.sha256(previous_bytes).hexdigest():
            raise ValueError("NATIVE_2T_PREVIOUS_MANIFEST_UNBOUND")
    old = {(m["member"], m["step_hours"]): m for m in
           (previous or {}).get("retained_identities", (previous or {}).get("messages", []))}
    if previous and (previous["run_time_utc"] != expected_run_utc.isoformat()
                     or not set(previous["product_steps"]).issubset(steps)):
        raise ValueError("NATIVE_2T_CACHE_RUN_OR_PLAN_CHANGED")
    records, errors, seen = [], [], set()
    for path in sorted(cache_dir.glob("step*-member*.grib2")):
        try:
            record, _ = _read_native_temperature_record(path, expected_run_utc)
            if ingest_mode == "SCHEDULED_LIVE":
                proof = json.loads(path.with_suffix(".grib2.proof.json").read_bytes())
                if proof.get("ingest_mode") != ingest_mode:
                    raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
            key = record["member"], record["step_hours"]
            if key in seen:
                raise ValueError("NATIVE_2T_DUPLICATE_MEMBER_STEP")
            seen.add(key)
            if key in old and old[key] != record:
                raise ValueError("NATIVE_2T_RETAINED_ORIGINAL_CHANGED")
            records.append(record)
        except Exception as exc:
            errors.append(f"{path.name}:{exc}")
    if previous:
        compatible = [m for m in records if m["grid_sha256"] == previous["grid_sha256"]]
        if len(compatible) != len(records):
            errors.append("NATIVE_2T_RETAINED_GRID_CHANGED")
            records = compatible
    grids = {m["grid_sha256"] for m in records}
    process_types = {(m["member"] == 0, m["process_type"]) for m in records}
    if ((len(grids) != 1 and not (previous and not grids))
            or any(sum(role == other for other, _ in process_types) > 1
                               for role, _ in process_types)):
        return NativeTemperatureSource("UNKNOWN", None, len(records), (), "NATIVE_2T_GRID_OR_PROCESS_UNKNOWN")
    grid = next(iter(grids)) if grids else previous["grid_sha256"]
    run_id = f"ecmwf_open_data:2t_instant_native_knots:{expected_run_utc:%Y%m%dT%H%MZ}:grid:{grid}:ifs50r1"
    if ingest_mode == "SCHEDULED_LIVE":
        run_id += ":origin:scheduled_live"
    existing = get_source_run(conn, run_id)
    if existing and (existing["ingest_mode"], existing["origin_mode"]) != (ingest_mode, ingest_mode):
        raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
    if existing and previous is None:
        # Canonical identity outlives a missing file. Restoration must supply
        # the original bound manifest/proofs, not replace its first clocks.
        raise ValueError("NATIVE_2T_CANONICAL_MANIFEST_MISSING")
    if previous and (previous["source_run_id"] != run_id or previous["grid_sha256"] != grid):
        raise ValueError("NATIVE_2T_CACHE_IDENTITY_CHANGED")
    wanted = {(m, s) for s in steps for m in range(51)}
    actual = {(m["member"], m["step_hours"]) for m in records}
    missing = tuple(sorted(wanted - actual))
    complete = not missing and not errors and wanted == actual
    clocks = [m["source_fetched_at"] for m in records]
    observed_steps = [s for s in steps if {(m, s) for m in range(51)} <= actual]
    payload = {"version": _NATIVE_2T_VERSION, "quantity_role": _NATIVE_2T_QUANTITY,
        "source_run_id": run_id, "run_time_utc": expected_run_utc.isoformat(), "grid_sha256": grid,
        "product_steps": steps, "messages": records, "errors": errors,
        # Keep original identity anchors even when a later scan finds a missing
        # or tampered body: repair cannot reset its first-possession clock.
        "retained_identities": list({**old, **{(m["member"], m["step_hours"]): m for m in records}}.values()),
        "source_issued_at": None, "source_publication_at": None,
        "qualification_status": "UNKNOWN" if ingest_mode == "SCHEDULED_LIVE" else "OFFLINE_ONLY"}
    if ingest_mode == "SCHEDULED_LIVE":
        payload.update(ingest_mode=ingest_mode,
            temperature_first_possession_at=max(clocks, key=datetime.fromisoformat) if clocks else None)
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(content).hexdigest()
    with conn:
        write_source_run(conn, source_run_id=run_id, source_id="ecmwf_open_data",
            track="2t_instant_native_knots", release_calendar_key="ecmwf_open_data",
            ingest_mode=ingest_mode, origin_mode=ingest_mode,
            source_cycle_time=expected_run_utc, source_issue_time=None, source_release_time=None,
            source_available_at=(max(clocks, key=datetime.fromisoformat)
                if complete and ingest_mode == "ARCHIVE_BACKFILL" else None),
            status="SUCCESS" if complete else "PARTIAL", completeness_status="COMPLETE" if complete else "PARTIAL",
            temperature_metric=None, physical_quantity=_NATIVE_2T_QUANTITY, observation_field="2t",
            valid_time_start=expected_run_utc + timedelta(hours=steps[0]),
            valid_time_end=expected_run_utc + timedelta(hours=steps[-1]),
            data_version=_NATIVE_2T_VERSION, expected_members=51,
            observed_members=len({m["member"] for m in records}), expected_steps_json=steps,
            observed_steps_json=observed_steps, expected_count=len(wanted), observed_count=len(actual),
            partial_run=not complete, manifest_hash=digest,
            raw_payload_hash=hashlib.sha256(json.dumps(sorted(m["raw_message_sha256"] for m in records)).encode()).hexdigest(),
            reason_code=None if complete else "NATIVE_2T_INCOMPLETE_OR_INVALID_ORIGINAL")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    # DB commits first. Failed publication leaves readback UNKNOWN, not a false ready row.
    with tempfile.NamedTemporaryFile(dir=manifest_path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
    return NativeTemperatureSource("AVAILABLE" if complete else "INCOMPLETE", run_id,
        len(actual), missing, errors[0] if errors else None)


def read_native_temperature_scope(conn: sqlite3.Connection, *, source_run_id: str,
        manifest_path: Path, required_steps: list[int], qualified_prefix_cut_utc: datetime | None,
        local_day_end_utc: datetime, explicit_manifest: list[dict], mask_grib_path: Path | None = None,
        mask_proof_path: Path | None = None, surface_geopotential_grib_path: Path | None = None,
        surface_geopotential_proof_path: Path | None = None, decision_at_utc: datetime | None = None,
        metric: str | None = None, role: str = "remaining_X", local_day_start_utc: datetime | None = None,
        _paths: OpenDataPaths | None = None) -> NativeTemperatureScope:
    """Qualified scope view of immutable originals, never hourly or live q authority.

    SCOPE: supplied qualified prefix cut through local end, with native brackets.
    DRAIN: supply missing bytes/prefix/static proof through existing source cadence.
    RESET: revalidate this scope independently; a different city/cut never changes cache identity.
    ``pit_status`` proves possession of this exact model quantity/scope, not
    hourly extrema, station equivalence, q authority or provider publication.
    """
    import base64
    import eccodes as ec
    from scripts import extract_open_ens_localday as decoder

    mode, temperature_clock = None, None
    try:
        content = manifest_path.read_bytes()
        payload = json.loads(content)
        row = get_source_run(conn, source_run_id)
        mode = payload.get("ingest_mode", "ARCHIVE_BACKFILL")
        if (not row or row["manifest_hash"] != hashlib.sha256(content).hexdigest()
                or payload["source_run_id"] != source_run_id or payload["version"] != _NATIVE_2T_VERSION
                or row["physical_quantity"] != _NATIVE_2T_QUANTITY or row["temperature_metric"] is not None
                or row["source_id"] != "ecmwf_open_data" or row["track"] != "2t_instant_native_knots"
                or row["dataset_id"] != _NATIVE_2T_VERSION or row["source_issue_time"] is not None
                or row["source_release_time"] is not None
                or row["expected_count"] != 51 * len(payload["product_steps"])
                or row["observed_count"] != len(payload["messages"])
                or row["source_cycle_time"] != payload["run_time_utc"] or payload["errors"]
                or mode not in {"ARCHIVE_BACKFILL", "SCHEDULED_LIVE"}
                or (row["ingest_mode"], row["origin_mode"]) != (mode, mode)
                or payload["qualification_status"] != ("UNKNOWN" if mode == "SCHEDULED_LIVE" else "OFFLINE_ONLY")
                or (mode == "SCHEDULED_LIVE" and row["source_available_at"] is not None)):
            raise ValueError("NATIVE_2T_MANIFEST_OR_ROW_INVALID")
        run = datetime.fromisoformat(payload["run_time_utc"])
        steps = _native_temperature_steps(run, required_steps)
        if steps != [s for s in range(steps[0], steps[-1] + 1) if s % (3 if s <= 144 else 6) == 0]:
            raise ValueError("NATIVE_2T_SCOPE_NATIVE_GRID_GAP")
        wanted = {(m, s) for s in steps for m in range(51)}
        selected = [m for m in payload["messages"] if m["step_hours"] in steps]
        if len(selected) != len(wanted) or {(m["member"], m["step_hours"]) for m in selected} != wanted:
            return NativeTemperatureScope("INCOMPLETE", reason="NATIVE_2T_MEMBER_STEP_SET_INCOMPLETE",
                qualification_status=payload["qualification_status"])
        scope_start = local_day_start_utc if role == "full_Y" else qualified_prefix_cut_utc
        if (role not in {"remaining_X", "full_Y"} or metric not in {None, "high", "low"}
                or scope_start is None or scope_start.tzinfo is None
                or local_day_end_utc.tzinfo is None or not run <= scope_start < local_day_end_utc
                or run + timedelta(hours=steps[0]) > scope_start
                or run + timedelta(hours=steps[-1]) < local_day_end_utc):
            raise ValueError("NATIVE_2T_QUALIFIED_PREFIX_OR_BRACKET_UNKNOWN")
        if decision_at_utc is not None and (decision_at_utc.tzinfo is None
                or decision_at_utc.utcoffset() != timedelta(0)
                or decision_at_utc > datetime.now(timezone.utc) or metric is None):
            raise ValueError("NATIVE_2T_DECISION_SCOPE_INVALID")
        originals = []
        for saved in selected:
            record, raw = _read_native_temperature_record(Path(saved["path"]), run)
            if mode == "SCHEDULED_LIVE" and json.loads(Path(saved["path"]).with_suffix(
                    ".grib2.proof.json").read_bytes()).get("ingest_mode") != mode:
                raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
            if record != saved:
                raise ValueError("NATIVE_2T_RETAINED_ORIGINAL_CHANGED")
            originals.append((saved, raw))
        temperature_clock = max((s["source_fetched_at"] for s, _ in originals), key=datetime.fromisoformat)
        # Resolve the actual normal collector caches, not an offline/audit HTTP
        # helper. Both mandatory tracks share this product/run/grid inventory.
        if mask_grib_path is None:
            paths = _paths or _resolve_opendata_paths()
            directory = _download_output_path(run_date=run.date(), run_hour=run.hour,
                param="2t", raw_root=paths.raw_root).parent
            candidates = [directory / f".{track}_{run:%Y%m%d}_{run:%H}z_lsm.grib2" for track in TRACKS]
            mask_grib_path = next((p for p in candidates if p.exists() and p.with_suffix(".z.grib2").exists()), candidates[0])
        mask_proof_path = mask_proof_path or mask_grib_path.with_suffix(".proof.json")
        surface_geopotential_grib_path = surface_geopotential_grib_path or mask_grib_path.with_suffix(".z.grib2")
        surface_geopotential_proof_path = surface_geopotential_proof_path or surface_geopotential_grib_path.with_suffix(".proof.json")
        mask = decoder._read_land_mask(mask_grib_path, mask_proof_path)
        mask_capture = decoder._open_ens_original_surface_capture(mask_grib_path)
        mask_gid = ec.codes_new_from_message(mask_grib_path.read_bytes())
        try:
            if (ec.codes_get(mask_gid, "paramId") != 172 or ec.codes_get(mask_gid, "shortName") != "lsm"
                    or ec.codes_get(mask_gid, "units") != "(0 - 1)"
                    or ec.codes_get(mask_gid, "typeOfLevel") != "surface"):
                raise ValueError("NATIVE_2T_LSM_ORIGINAL_QUANTITY_INVALID")
        finally:
            ec.codes_release(mask_gid)
        s3 = next(base64.b64decode(s["bytes_base64"], validate=True)
                  for s in mask_capture["metadata_sections"] if s["section_number"] == 3)
        if (hashlib.sha256(s3).hexdigest() != payload["grid_sha256"]
                or decoder._open_ens_original_grid(s3) != mask["fields"]):
            raise ValueError("NATIVE_2T_LSM_ORIGINAL_GRID_MISMATCH")
        if not explicit_manifest or len({c["city"] for c in explicit_manifest}) != len(explicit_manifest):
            raise ValueError("NATIVE_2T_CITY_SCOPE_INVALID")
        points = decoder._select_land_grid_points(mask["fields"], explicit_manifest, mask["values"].__getitem__)
        knots = []
        for saved, raw in originals:
            gid = ec.codes_new_from_message(raw)
            try:
                values = ec.codes_get_values(gid)
                if len(values) != mask["fields"]["Ni"] * mask["fields"]["Nj"]:
                    raise ValueError("NATIVE_2T_VALUES_GRID_INVALID")
                for city in explicit_manifest:
                    point = points[city["city"]]
                    value = float(values[point["selected_flat_index"]])
                    if not math.isfinite(value) or value == ec.codes_get(gid, "missingValue"):
                        raise ValueError("NATIVE_2T_SELECTED_VALUE_INVALID")
                    knots.append({"city": city["city"], "member": saved["member"],
                        "step_hours": saved["step_hours"], "valid_time_utc": saved["valid_time_utc"],
                        "value_k": value, "raw_message_sha256": saved["raw_message_sha256"],
                        "selected_point": point, "lsm_raw_sha256": mask_capture["raw_message_sha256"],
                        "surface_class": "PURE_LAND" if point["selected_land_fraction"] == 1 else "MIXED_LAND_WATER"})
            finally:
                ec.codes_release(gid)
        static_status, dependency_clock, pit, witness, static_reason = "UNKNOWN", None, "UNKNOWN", None, None
        try:
            phi = decoder.read_native_static_dependency(path=surface_geopotential_grib_path,
                proof_path=surface_geopotential_proof_path, param="z", run=run, grid_sha256=payload["grid_sha256"])
            own_index = mask_grib_path.with_suffix(".index.body")
            land = decoder.read_native_static_dependency(path=mask_grib_path, proof_path=mask_proof_path,
                param="lsm", run=run, grid_sha256=payload["grid_sha256"],
                index_path=own_index if own_index.exists() else surface_geopotential_grib_path.with_suffix(".index.body"),
                index_proof_path=mask_proof_path if own_index.exists() else surface_geopotential_proof_path)
            for point in points.values():
                for cell in point["four_neighbors"]:
                    value = float(phi["values"][cell["flat_index"]])
                    if not math.isfinite(value) or value == phi["missing_value"]:
                        raise ValueError("NATIVE_2T_STATIC_PHI_NONFINITE")
                    cell["raw_phi_m2_s2"] = value
                point["selected_raw_phi_m2_s2"] = float(phi["values"][point["selected_flat_index"]])
                point["surface_class"] = "PURE_LAND" if point["selected_land_fraction"] == 1 else "MIXED_LAND_WATER"
            dependency_clock = max((temperature_clock, land["available_at"], phi["available_at"]), key=datetime.fromisoformat)
            static_status = "SAME_RUN_OBSERVED"
            pit = ("AVAILABLE" if decision_at_utc is not None and datetime.fromisoformat(dependency_clock) < decision_at_utc
                   else "AFTER_DECISION" if decision_at_utc is not None else "UNKNOWN")
            witness = {"quantity": {"param_id": 167, "units": "K", "height_agl_m": 2, "step_type": "instant"},
                "run_time_utc": run.isoformat(), "scope_role": role, "temperature_metric": metric,
                "origin_mode": mode, "source_run_id": source_run_id,
                "scope_start_utc": scope_start.isoformat(), "scope_end_utc": local_day_end_utc.isoformat(),
                "grid_sha256": payload["grid_sha256"], "selected_cities": points,
                "temperature_messages": [s for s, _ in originals],
                "static_dependencies": [{k: v for k, v in dep.items() if k not in {"values", "fields"}}
                                        for dep in (land, phi)],
                "sensor_agl_status": "UNKNOWN", "station_ground_datum_status": "UNKNOWN",
                "precision_status": "UNKNOWN", "representativeness_status": "UNKNOWN",
                "station_equivalence_status": "UNKNOWN", "source_issued_at": None}
        except (ValueError, KeyError, OSError, TypeError) as exc:
            static_reason = str(exc)
        return NativeTemperatureScope("AVAILABLE", tuple(knots),
            temperature_first_possession_at=temperature_clock, reason=static_reason,
            qualification_status=payload["qualification_status"], static_validity_status=static_status,
            temperature_scope_first_possession_at=temperature_clock,
            physical_dependency_available_at=dependency_clock, pit_status=pit, physical_witness=witness,
            scope_role=role, temperature_metric=metric)
    except Exception as exc:
        return NativeTemperatureScope("UNKNOWN", reason=str(exc),
            temperature_first_possession_at=temperature_clock, temperature_scope_first_possession_at=temperature_clock,
            qualification_status="UNKNOWN" if mode == "SCHEDULED_LIVE" else "OFFLINE_ONLY",
            scope_role=role, temperature_metric=metric)





def _write_runtime_coordinate_manifest(raw_root: Path, *, manifest_json: str | None = None) -> Path:
    """Bind extraction to the same station locations as current provider forecasts."""
    content = (
        manifest_json if manifest_json is not None else runtime_coordinate_manifest_json()
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    directory = raw_root / "docs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"opendata_coordinates_{digest}.json"
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("runtime extraction manifest content hash mismatch")
        return path
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def _resolve_opendata_paths(
    *,
    source_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    legacy_external_root: Path | None = None,
) -> OpenDataPaths:
    """Bind raw storage and the versioned in-repo producer for one cycle.

    Raw bytes remain under the configured ``ZEUS_51_SOURCE_ROOT`` (or the
    existing default).  The extractor is always the checked-in script under
    ``PROJECT_ROOT/scripts``; no unversioned external checkout may provide
    executable code.  The coordinate manifest is generated from the current
    runtime city config and passed explicitly by ``collect_open_ens_cycle``.
    """
    env = os.environ if environ is None else environ
    configured = str(env.get("ZEUS_51_SOURCE_ROOT", "")).strip()
    raw_root = Path(
        configured or source_root or FIFTY_ONE_ROOT
    ).expanduser().resolve()
    asset_root = raw_root
    origin = "repo_script_fixed" if not configured else "env_raw_root_repo_script"
    extract_script = PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    return OpenDataPaths(
        raw_root=raw_root,
        asset_root=asset_root,
        extract_script=extract_script,
        manifest_path=asset_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json",
        origin=origin,
    )


FIFTY_ONE_ROOT = (PROJECT_ROOT / "51 source data").resolve()
# DOWNLOAD_SCRIPT deleted 2026-05-11: replaced by in-process parallel SDK fetch
# (see PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md)
INGEST_SCRIPT_DIR = PROJECT_ROOT / "scripts"

# ECMWF hang antibody #1 (2026-05-13) — eager-import ingest_grib_to_snapshots
# at module load so the first ``collect_open_ens_cycle`` call cannot block
# on first-time module init while holding the forecasts.db BULK writer-lock.
# Witnessed 2026-05-12 13:31 PDT: daemon held BULK flock for 12h with WAL=0
# bytes (no SQL write ever opened) — see /tmp/zeus_ecmwf_critic_review.md.
if str(INGEST_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(INGEST_SCRIPT_DIR))
import ingest_grib_to_snapshots as _ingest_grib_module  # type: ignore  # noqa: E402
from ingest_grib_to_snapshots import (  # type: ignore  # noqa: E402
    SourceRunContext as _ingest_grib_SourceRunContext,
    ingest_track as _ingest_grib_ingest_track,
)

# ECMWF Open Data ENS dissemination grid (enfo cf/pf, mx2t3/mn2t3/2t):
#   0–144h by 3h, then 150–360h by 6h.
# Note: the underlying IFS model produces hourly steps 0–90h and 3h steps
#       93–144h (per https://www.ecmwf.int/en/forecasts/datasets/set-iii),
#       but Open Data subsamples to the 3h/6h grid above. Hourly steps are
#       only available via MARS, which Zeus does not use.
# Period-aligned params: mx2t3/mn2t3 valid at every disseminated step;
#       mx2t6/mn2t6 (deprecated 2026-05-07) were valid only at 6h multiples.
# We request 3h-native steps through OPENDATA_MAX_STEP_HOURS (144h) and NO further.
#
# 5-day cap (2026-05-29): Polymarket retired all weather markets beyond 5 days.
# D+5 plus the largest trading-city UTC offset lands at ≤144h, so steps 150-282
# are never traded. The previous 282h tail (fix/#134, LOW D+10 authority) is
# RETIRED — fetching it wasted bandwidth and left a fail-closed >144h coverage
# path that no live market exercised. STEP_HOURS is now DERIVED from the cap
# constant so the tail is unconstructable: re-adding it would break the coupling
# antibody in tests/test_ecmwf_open_data_step_hours.py.
#
# Authority: src/data/forecast_target_contract.OPENDATA_MAX_STEP_HOURS (=144),
#            architecture/zeus_grid_resolution_authority_2026_05_07.yaml A1+3h (stride).
# ECMWF Open Data `enfo` stream serves mx2t3/mn2t3 (3h aggregations) at 3h stride
# through 144h. We fetch 3h-native and let calibration learn the 3h→6h envelope
# downstream. We do NOT re-aggregate to 6h at fetch time (forbidden_patterns).
STEP_HOURS = list(range(3, OPENDATA_MAX_STEP_HOURS + 3, 3))  # 3, 6, …, 144 (A1+3h native grid)

# Track config — local to this module so the daemon's ingest knob is one
# clean dict rather than two parallel param lists.
TRACKS: dict[str, dict] = {
    "mx2t6_high": {
        "open_data_param": "mx2t3",   # was mx2t6; deprecated — API returns ValueError
        "data_version": ECMWF_OPENDATA_HIGH_DATA_VERSION,
        "ingest_track": "mx2t6_high",
        "extract_subdir": "open_ens_mx2t6_localday_max",
    },
    "mn2t6_low": {
        "open_data_param": "mn2t3",   # was mn2t6; deprecated — API returns ValueError
        "data_version": ECMWF_OPENDATA_LOW_DATA_VERSION,
        "ingest_track": "mn2t6_low",
        "extract_subdir": "open_ens_mn2t6_localday_min",
    },
}

SOURCE_ID = "ecmwf_open_data"
FORECAST_SOURCE_ROLE = "entry_primary"
MODEL_VERSION = "ecmwf_open_data"

# Raw GRIB is a decode input, not an archive: it is deleted as soon as its own
# cycle+track has durable COMPLETE source-run evidence AND an equal count of
# VERIFIED canonical snapshots (the proof gate in
# _plan_decoded_open_data_raw_retention is unchanged and remains the only thing
# that authorizes aggregate deletion). Still-consumable role receipts keep
# byte-identical original messages separately; a re-fetch is not proof of the
# original acquisition clock.
#
# 2026-09-17 (operator directive, twice restated): was 2, which retained ~46 GB
# across two day-dirs on a host at 96% full — for the only one of 13 sources that
# keeps raw payloads at all. 0 means "no calendar grace": eligibility is decided
# purely by the per-cycle proof gate, so an unproven cycle is still retained no
# matter how old. Set to 1 to keep the current UTC day, 2 for the prior behaviour.
_RAW_RETENTION_CALENDAR_DAYS = 0
_RAW_STEP_NAME = re.compile(
    r"^\.(?P<date>\d{8})_(?P<hour>\d{2})z_step\d{3}_"
    r"(?P<param>mx2t3|mn2t3)_ens51\.grib2$"
)
_RAW_CONCAT_NAME = re.compile(
    r"^open_ens_(?P<date>\d{8})_(?P<hour>\d{2})z_steps_.*_params_"
    r"(?P<param>mx2t3|mn2t3)\.grib2$"
)
_RAW_PARAM_AUTHORITY = {
    "mx2t3": ("mx2t6_high", "high"),
    "mn2t3": ("mn2t6_low", "low"),
}


@dataclass(frozen=True)
class _RawRetentionPlan:
    root: Path
    files: tuple[Path, ...]
    eligible_group_count: int
    retained_group_count: int
    unrecognized_file_count: int
    planned_bytes: int
    role_originals: tuple[tuple[tuple[Path, ...], tuple[dict, ...]], ...] = ()
    live_role_hashes: frozenset[str] = frozenset()
    role_reference_errors: tuple[str, ...] = ()
    role_reference_complete: bool = False
    role_gc_files: tuple[tuple[Path, int, int, int, int], ...] = ()
    reference_db: Path | None = None
    reference_time: datetime | None = None


def _role_message_path(raw_root: Path, digest: str) -> Path:
    """Address an original by its canonical receipt, never a new capture clock."""
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("ROLE_ORIGINAL_DIGEST_INVALID")
    raw_root = raw_root.absolute()
    path = raw_root / "raw" / "ecmwf_open_ens" / "role_messages" / f"{digest}.grib2"
    if raw_root.is_symlink():
        raise ValueError("ROLE_ORIGINAL_ROOT_SYMLINK")
    parent = path.parent
    while parent != raw_root:
        if parent.is_symlink():
            raise ValueError("ROLE_ORIGINAL_DIRECTORY_SYMLINK")
        parent = parent.parent
    return path


def _read_role_message_bytes(raw_root: Path, capture: Mapping[str, object]) -> bytes:
    path = _role_message_path(raw_root, str(capture["raw_message_sha256"]))
    if path.is_symlink() or not path.is_file():
        raise ValueError("ROLE_ORIGINAL_BODY_UNAVAILABLE")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if os.fstat(stream.fileno()).st_size != int(capture["raw_message_length"]):
            raise ValueError("ROLE_ORIGINAL_LENGTH_MISMATCH")
        raw = stream.read()
    if hashlib.sha256(raw).hexdigest() != capture["raw_message_sha256"]:
        raise ValueError("ROLE_ORIGINAL_HASH_MISMATCH")
    return raw


def _publish_role_message(raw_root: Path, capture: Mapping[str, object], raw: bytes) -> None:
    """An exclusive atomic replica of observed bytes; no receipt or clock minting."""
    import tempfile
    if (len(raw) != int(capture["raw_message_length"])
            or hashlib.sha256(raw).hexdigest() != capture["raw_message_sha256"]):
        raise ValueError("ROLE_ORIGINAL_PUBLICATION_MISMATCH")
    path = _role_message_path(raw_root, str(capture["raw_message_sha256"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    _role_message_path(raw_root, str(capture["raw_message_sha256"]))
    if path.exists() or path.is_symlink():
        if _read_role_message_bytes(raw_root, capture) != raw:
            raise ValueError("ROLE_ORIGINAL_PUBLICATION_CONFLICT")
        return
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=path.parent) as directory:
        staged = Path(directory) / "original.grib2"
        with staged.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(staged, path)
        except FileExistsError:
            if _read_role_message_bytes(raw_root, capture) != raw:
                raise ValueError("ROLE_ORIGINAL_PUBLICATION_CONFLICT")
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _preserve_role_originals(raw_root: Path, sources: tuple[Path, ...], captures: tuple[dict, ...]) -> None:
    """Complete the original-body replica before any proven aggregate deletion.

    SCOPE: this exact run/quantity group. DRAIN: ordinary collection retries
    publication from its retained original bytes. RESET: every canonical
    capture has a byte-identical replica. No network or acquisition clock.
    """
    import eccodes as ec
    from scripts.extract_open_ens_localday import _native_message_capture

    remaining = {str(capture["raw_message_sha256"]): capture for capture in captures}
    for digest, capture in tuple(remaining.items()):
        path = _role_message_path(raw_root, digest)
        if path.exists() or path.is_symlink():
            _read_role_message_bytes(raw_root, capture)
            del remaining[digest]
    for source in sources:
        if not remaining:
            break
        if source.is_symlink() or not source.is_file():
            raise ValueError("ROLE_ORIGINAL_SOURCE_UNAVAILABLE")
        with source.open("rb") as stream:
            while (gid := ec.codes_grib_new_from_file(stream)) is not None:
                try:
                    raw = ec.codes_get_message(gid)
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest not in remaining:
                        continue
                    capture = remaining[digest]
                    if _native_message_capture(gid) != capture:
                        raise ValueError("ROLE_ORIGINAL_CAPTURE_MISMATCH")
                    _publish_role_message(raw_root, capture, raw)
                    _read_role_message_bytes(raw_root, capture)
                    del remaining[digest]
                finally:
                    ec.codes_release(gid)
    if remaining:
        raise ValueError("ROLE_ORIGINAL_CAPTURE_INCOMPLETE")


def _role_original_snapshot_references(conn, now_utc: datetime, *, raw_root: Path | None = None) -> tuple[set[int], set[tuple[str, str, str]], list[str]]:
    """Read existing consumers only; never turn missing evidence into no reference.

    SCOPE: original-message hashes of exact normal source snapshots. DRAIN:
    each normal cleanup recomputes market frontiers, current-revision posterior
    age plus the existing Prepared two-stage bound, unfinished claims and
    nonterminal command parents. RESET: expiry/removal/terminal state without
    another exact consumer releases that original; unresolved mappings retain
    their scope, never require an always-newest manifest or a guard clear.
    """
    from src.config import STATE_DIR
    from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
    from src.data.replacement_forecast_cycle_policy import (
        CURRENT_EVIDENCE_SEMANTICS_REVISION, replacement_source_cycle_max_age_hours,
    )
    from src.state.db import _connect_read_only, ZEUS_WORLD_DB_PATH
    from src.execution.command_bus import TERMINAL_STATES
    from src.data.replacement_forecast_production import _replacement_forecast_live_materialization_queue_config
    from src.data import replacement_forecast_live_materialization_queue as queue

    selected: set[int] = set()
    uncertain: set[tuple[str, str, str]] = set()
    errors: list[str] = []
    visited_posteriors = set()

    def scope_of(payload):
        scope = (str(payload.get("city", "")), str(payload.get("target_date", "")),
                 str(payload.get("temperature_metric", payload.get("metric", ""))))
        return scope if scope[0] and scope[1] and scope[2] in {"high", "low"} else None

    def preserve_scope(scope):
        if scope is None:
            errors.append("ROLE_RETENTION_REFERENCE_SCOPE_UNKNOWN")
            return
        uncertain.add(scope)
        selected.update(int(row[0]) for row in conn.execute(
            "SELECT snapshot_id FROM ensemble_snapshots WHERE city=? AND target_date=? AND temperature_metric=?",
            scope))

    def consume(payload, scope=None):
        found = False
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                if key == "native_snapshot_id":
                    selected.add(int(value)); found = True
                elif key == "paired_snapshot_ids":
                    selected.update(int(v) for v in value); found = True
                elif key in {"posterior_id", "posterior_identity_hash"} and value not in (None, ""):
                    column = "posterior_id" if key == "posterior_id" else "posterior_identity_hash"
                    if (column, str(value)) not in visited_posteriors:
                        visited_posteriors.add((column, str(value)))
                        row = conn.execute(f"SELECT provenance_json FROM forecast_posteriors WHERE {column}=?", (value,)).fetchone()
                        if row is not None:
                            found = consume(json.loads(row[0]), scope) or found
                elif isinstance(value, (Mapping, list, tuple)):
                    found = consume(value, scope) or found
        elif isinstance(payload, (list, tuple)):
            for value in payload:
                found = consume(value, scope) or found
        return found

    # Storage candidates are not qualified actions. Keep the transport frontier
    # and the public role selector's finite native-run envelope without decoding
    # every global field once per city/metric. Only the action reader qualifies
    # bytes, grid, 51 members and causal possession; missing X permits no fallback.
    frontiers = conn.execute("""SELECT coverage.city, coverage.target_local_date,
            coverage.temperature_metric, coverage.target_window_start_utc,
            coverage.target_window_end_utc, source.source_cycle_time, coverage.source_run_id
        FROM source_run_coverage coverage JOIN source_run source
          ON source.source_run_id=coverage.source_run_id
        WHERE coverage.source_id=? AND source.status='SUCCESS'
          AND source.completeness_status='COMPLETE' AND source.partial_run=0
          AND source.ingest_mode IN ('SCHEDULED_LIVE','BOOT_CATCHUP')
          AND source.source_cycle_time<=? AND coverage.target_window_end_utc>?
          AND EXISTS (SELECT 1 FROM market_events market
            WHERE market.city=coverage.city AND market.target_date=coverage.target_local_date
              AND market.temperature_metric=coverage.temperature_metric
              AND market.token_id IS NOT NULL AND market.range_label IS NOT NULL)
        ORDER BY source.source_cycle_time DESC""", (SOURCE_ID, now_utc.isoformat(), now_utc.isoformat()))
    chosen = set()
    for row in frontiers:
        scope = tuple(str(v) for v in row[:3])
        start, end, cycle = (datetime.fromisoformat(str(v)) for v in row[3:6])
        roles = ("Y",) if cycle <= start else ()
        if start <= now_utc < end:
            roles += ("X",)
        for role in roles:
            if (*scope, role) not in chosen:
                chosen.add((*scope, role))
                transport_ids = tuple(int(r[0]) for r in conn.execute(
                    "SELECT snapshot_id FROM ensemble_snapshots WHERE city=? AND target_date=? AND temperature_metric=? AND source_run_id=? ORDER BY snapshot_id DESC",
                    (*scope, row[6])))
                selected.update(transport_ids)
                try:
                    # Match read_native_measurement_role's existing query, not
                    # a new Y age cutoff: an independent prior-start Y can be
                    # older than the current X. Partial/unknown bodies remain
                    # possible storage dependencies, never action permission.
                    runs = conn.execute("""SELECT source_cycle_time FROM source_run
                        WHERE source_id='ecmwf_open_data' AND track='2t_instant_native_knots'
                          AND ingest_mode='SCHEDULED_LIVE' AND origin_mode='SCHEDULED_LIVE'
                          AND julianday(source_cycle_time)<=julianday(?)
                        ORDER BY source_cycle_time DESC, source_run_id LIMIT 16""",
                        ((start if role == "Y" else now_utc).isoformat(),)).fetchall()
                    if not runs:
                        raise ValueError("ROLE_RETENTION_NATIVE_CANDIDATES_UNKNOWN")
                    candidates = conn.execute("""SELECT snapshot_id,provenance_json,
                            COALESCE(source_available_at,available_at),recorded_at
                        FROM ensemble_snapshots WHERE snapshot_id IN (
                            SELECT MAX(snapshot_id) FROM ensemble_snapshots
                            WHERE city=? AND target_date=? AND temperature_metric=?
                              AND source_cycle_time IN (""" + ",".join("?" for _ in runs) +
                        ") GROUP BY source_cycle_time)", (*scope, *(r[0] for r in runs))).fetchall()
                    if not candidates:
                        raise ValueError("ROLE_RETENTION_NATIVE_CANDIDATES_UNKNOWN")
                    selected.update(int(candidate[0]) for candidate in candidates)
                    for candidate in candidates:
                        capture = json.loads(candidate[1])["native_capture_receipt"]
                        point = capture["selected_point"]
                        if (capture["capture_status"] != "OBSERVED" or not capture["messages"]
                                or int(point["flat_index"]) < 0
                                or not all(math.isfinite(float(point[k])) for k in ("lat", "lon"))
                                or any(datetime.fromisoformat(str(clock)).tzinfo is None
                                       for clock in candidate[2:])):
                            raise ValueError("ROLE_RETENTION_NATIVE_CANDIDATES_UNKNOWN")
                except (OSError, ValueError, TypeError, KeyError, IndexError):
                    # SCOPE: this exact market family. DRAIN: normal capture or
                    # original metadata restoration. RESET: known finite
                    # candidates, or target expiry without another consumer.
                    preserve_scope(scope)
                    errors.append("ROLE_RETENTION_NATIVE_FRONTIER_UNKNOWN")

    # A Prepared may be selected at T+C and execute until its book's S+C.
    # This is the existing contract's two-stage upper bound, not a new TTL.
    bound = replacement_source_cycle_max_age_hours()
    if not math.isfinite(bound):
        errors.append("ROLE_RETENTION_AGE_POLICY_UNBOUNDED")
        return selected, uncertain, errors
    earliest = now_utc - timedelta(hours=bound) - 2 * FRESHNESS_WINDOW_DEFAULT
    for row in conn.execute("""SELECT city,target_date,temperature_metric,provenance_json
            FROM forecast_posteriors WHERE runtime_layer='live'
              AND source_cycle_time>=? AND source_cycle_time<=?""",
            (earliest.isoformat(), now_utc.isoformat())):
        scope = tuple(str(v) for v in row[:3])
        try:
            payload = json.loads(row[3])
            shape = payload.get("bayes_precision_fusion", {}).get("current_evidence_shape", {})
            if shape.get("semantics_revision") == CURRENT_EVIDENCE_SEMANTICS_REVISION:
                if not consume(payload, scope):
                    preserve_scope(scope)
        except (ValueError, TypeError, KeyError):
            preserve_scope(scope)
            errors.append("ROLE_RETENTION_POSTERIOR_REFERENCE_UNKNOWN")

    cfg = _replacement_forecast_live_materialization_queue_config()
    capture_root = Path(cfg["request_dir"]).parent / queue._REQUEST_ALIAS_DIR
    def queue_inventory():
        # An IO mutation fence only. It confers no temporal qualification.
        directories = {Path(cfg[k]) for k in ("seed_dir", "request_dir", "inflight_dir")}
        directories.add(capture_root)
        for base in (Path(cfg["inflight_dir"]), capture_root):
            if base.is_symlink():
                continue
            if base.exists():
                for entry in base.iterdir():
                    if base == capture_root and not entry.name.startswith(queue._CAPTURE_PREFIX):
                        continue
                    if (base != capture_root and entry.name.startswith(".")
                            and not entry.name.startswith(queue._STAGING_PREFIX)):
                        continue
                    if entry.is_dir() and not entry.is_symlink():
                        directories.add(entry)
                        if base == capture_root:
                            directories.add(entry / queue._CAPTURE_PAYLOAD_DIR)
        objects = set(directories)
        for directory in directories:
            if directory.is_dir() and not directory.is_symlink():
                objects.update(directory.iterdir())
        result = []
        for path in sorted(objects):
            try:
                stat = path.lstat()
                identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            except FileNotFoundError:
                identity = None
            result.append((str(path), identity))
        return tuple(result)
    before_inventory = queue_inventory()
    paths = []
    for key in ("seed_dir", "request_dir"):
        directory = Path(cfg[key])
        if directory.is_symlink():
            errors.append("ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN")
        elif directory.exists():
            paths.extend(directory.glob("*.json"))
    inflight = Path(cfg["inflight_dir"])
    if inflight.is_symlink():
        errors.append("ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN")
    elif inflight.exists():
        batches = []
        for entry in inflight.iterdir():
            if ((entry.name.startswith(".") and not entry.name.startswith(queue._STAGING_PREFIX))
                    or entry.name == queue._lease.LEASE_DIR_NAME):
                continue
            if entry.is_symlink() or not entry.is_dir():
                errors.append("ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN")
            else:
                batches.append(entry)
        for batch in sorted(batches, key=lambda p: not p.name.startswith(queue._STAGING_PREFIX)):
            # A replaced captured entry can still have a live worker holding
            # its parsed immutable body. Regular-only enumeration would turn
            # missing proof into a false no-consumer declaration.
            paths.extend(queue._captured_entries(batch))
        # Recovery can move a published claim back to its source directory.
        # Observe that reverse destination again. A captured path moved while
        # reading is UNKNOWN, never proof that its frozen request disappeared.
        for key in ("seed_dir", "request_dir"):
            directory = Path(cfg[key])
            if directory.is_symlink():
                errors.append("ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN")
            elif directory.exists():
                paths.extend(directory.glob("*.json"))
    if capture_root.is_symlink():
        errors.append("ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN")
    else:
        for capture in queue._capture_dirs(Path(cfg["request_dir"])):
            payload = capture / queue._CAPTURE_PAYLOAD_DIR
            if capture.is_symlink() or not capture.is_dir() or payload.is_symlink():
                errors.append("ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN")
                continue
            # Only the owning terminal classification may remove this from
            # the consumer union. Unfinished regular payloads are requests.
            if not queue._capture_settled(capture):
                paths.extend(queue._capture_entries(capture))
    for path in paths:
        try:
            body, _ = queue.read_regular_request(path)
            payload = json.loads(body)
            scope = scope_of(payload)
            if not consume(payload, scope):
                # Unfinished construction has no point certificate yet. Its
                # committed baseline and counterpart are still dependencies.
                preserve_scope(scope)
        except (OSError, ValueError, TypeError, KeyError):
            errors.append("ROLE_RETENTION_UNFINISHED_REFERENCE_UNKNOWN")

    if before_inventory != queue_inventory():
        errors.append("ROLE_RETENTION_REFERENCE_MUTATED")

    terminal = {state.value for state in TERMINAL_STATES}
    trade = world = None
    try:
        trade = _connect_read_only(STATE_DIR / "zeus_trades.db")
        world = _connect_read_only(ZEUS_WORLD_DB_PATH)
        trade.execute("PRAGMA query_only=ON"); world.execute("PRAGMA query_only=ON")
        for command in trade.execute("SELECT command_id,token_id,q_version,state FROM venue_commands WHERE state NOT IN (" + ",".join("?" for _ in terminal) + ")", tuple(terminal)):
            scope_row = conn.execute("SELECT city,target_date,temperature_metric FROM market_events WHERE token_id=? LIMIT 1", (command[1],)).fetchone()
            scope = None if scope_row is None else tuple(str(v) for v in scope_row)
            row = conn.execute("SELECT provenance_json FROM forecast_posteriors WHERE posterior_identity_hash=?", (command[2],)).fetchone()
            found = row is not None and consume(json.loads(row[0]), scope)
            bridge = trade.execute("SELECT decision_certificate_hash FROM position_decision_attribution WHERE command_id=? AND resolution='ATTRIBUTED'", (command[0],)).fetchone()
            pending, visited = ([] if bridge is None else [str(bridge[0])]), set()
            while pending:
                digest = pending.pop()
                if digest in visited:
                    continue
                visited.add(digest)
                cert = world.execute("SELECT payload_json FROM decision_certificates WHERE certificate_hash=?", (digest,)).fetchone()
                if cert is None:
                    preserve_scope(scope)
                    errors.append("ROLE_RETENTION_COMMAND_CERTIFICATE_UNKNOWN")
                    continue
                found = consume(json.loads(cert[0]), scope) or found
                pending.extend(str(r[0]) for r in world.execute("SELECT parent_certificate_hash FROM decision_certificate_edges WHERE child_certificate_id=(SELECT certificate_id FROM decision_certificates WHERE certificate_hash=?)", (digest,)))
            if not found or bridge is None:
                preserve_scope(scope)
                errors.append("ROLE_RETENTION_COMMAND_REFERENCE_UNKNOWN")
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
        errors.append("ROLE_RETENTION_COMMAND_AUTHORITY_UNKNOWN")
    finally:
        for connection in (trade, world):
            if connection is not None:
                connection.close()
    return selected, uncertain, errors


def _raw_file_identity(path: Path) -> tuple[str, int, str] | None:
    match = _RAW_STEP_NAME.fullmatch(path.name) or _RAW_CONCAT_NAME.fullmatch(path.name)
    if match is None:
        return None
    hour = int(match.group("hour"))
    if hour not in {0, 6, 12, 18}:
        return None
    return match.group("date"), hour, match.group("param")


def _canonical_for_transport_sidecar(path: Path) -> Path | None:
    """Return the completed ``.grib2`` a ``.partial``/``.ranges.json`` belongs to.

    Transport names are ``<canonical>.<pf|cf>.<hash>.partial`` and that name plus
    ``.ranges.json``. Returns None for anything that is not one of those, so an
    unrecognised file is never treated as a sidecar.
    """

    name = path.name
    if name.endswith(_RANGE_RESUME_MANIFEST_SUFFIX):
        name = name[: -len(_RANGE_RESUME_MANIFEST_SUFFIX)]
    if not name.endswith(".partial"):
        return None
    name = name[: -len(".partial")]
    # Strip the ``.<pf|cf>.<hexhash>`` transport namespace, when present.
    match = re.fullmatch(r"(?P<base>.+?)\.(?:pf|cf)\.[0-9a-f]+", name)
    if match is not None:
        name = match.group("base")
    return path.with_name(name)


def _orphaned_transport_sidecars(
    root: Path,
    *,
    planned_canonical: set[Path],
) -> list[Path]:
    """Plan `.partial` / `.ranges.json` files whose download is demonstrably over.

    These are invisible to the group proof by construction: `_raw_file_identity`
    matches only `.grib2` names, so the calendar cutoff never reaches a sidecar at
    any age. Measured 2026-09-18: 350 regrew in one cycle after a manual sweep, and
    every one of 1,104 swept earlier had a completed `.grib2` sibling.

    A sidecar is planned ONLY when its canonical target already exists (the step
    finished, so the resume state is spent) or that canonical is being evicted in
    this same plan. A sidecar whose canonical is absent is left alone — that is an
    in-flight or resumable download, and deleting its manifest would discard
    recoverable transport progress.
    """

    orphans: list[Path] = []
    for day_dir in sorted(root.iterdir()):
        if day_dir.is_symlink() or not day_dir.is_dir():
            continue
        entries = [path for path in day_dir.iterdir() if not path.is_symlink()]
        # A day whose canonicals are all gone is a DRAINED day: either every group
        # was evicted, or none was ever completed. Either way no download can
        # resume into it, so its leftover sidecars are spent. Without this, the
        # first eviction strands them permanently — the canonical they point at no
        # longer exists, so the exists() test below can never fire again. Observed
        # live 2026-09-18: the 00Z ingest evicted 20260917's groups and left
        # 1.0 GB of sidecars behind with zero surviving canonicals.
        day_has_canonical = any(
            path.is_file()
            and path.name.endswith(".grib2")
            and _canonical_for_transport_sidecar(path) is None
            for path in entries
        )
        for candidate in entries:
            if not candidate.is_file():
                continue
            canonical = _canonical_for_transport_sidecar(candidate)
            if canonical is None:
                continue
            if (
                canonical in planned_canonical
                or canonical.exists()
                or not day_has_canonical
            ):
                orphans.append(candidate)
    return orphans


def _sql_like_escape(value: str) -> str:
    """Escape LIKE wildcards so a literal prefix cannot match more than itself.

    Source-run ids are built from a date and a track name, so they carry no
    wildcards today — but a `_` in a future track label would silently match any
    character and widen an eviction probe, which is the one direction this gate
    must never fail in.
    """

    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _plan_decoded_open_data_raw_retention(
    conn,
    *,
    raw_root: Path,
    reference_date: date,
    retention_days: int = _RAW_RETENTION_CALENDAR_DAYS,
    reference_time: datetime | None = None,
) -> _RawRetentionPlan:
    """Plan deletion only for raw groups reproduced by canonical DB truth.

    Planning happens after the canonical commit while the forecasts connection
    is still authoritative. Applying the plan happens after releasing the BULK
    writer lock so multi-gigabyte filesystem cleanup cannot stall probability
    writers. Unknown files and symlinks are never candidates.
    """
    if retention_days < 0:
        raise ValueError("OpenData raw retention days must not be negative")
    root = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        return _RawRetentionPlan(root, (), 0, 0, 0, 0,
            role_reference_errors=("ROLE_RETENTION_RAW_DIRECTORY_UNKNOWN",))

    cutoff = reference_date - timedelta(days=retention_days - 1)
    groups: dict[tuple[str, int, str], list[Path]] = {}
    blocked_groups: set[tuple[str, int, str]] = set()
    unrecognized = 0
    for day_dir in (sorted(root.iterdir()) if root.exists() else ()):
        if day_dir.is_symlink() or not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y%m%d").date()
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for candidate in day_dir.iterdir():
            identity = _raw_file_identity(candidate)
            if identity is None or identity[0] != day_dir.name:
                if candidate.name.endswith(".grib2"):
                    unrecognized += 1
                continue
            if candidate.is_symlink() or not candidate.is_file():
                blocked_groups.add(identity)
                continue
            groups.setdefault(identity, []).append(candidate)

    planned: list[Path] = []
    role_originals: list[tuple[tuple[Path, ...], tuple[dict, ...]]] = []
    eligible_groups = 0
    retained_groups = 0
    for (day_text, hour, param), paths in sorted(groups.items()):
        identity = (day_text, hour, param)
        if identity in blocked_groups:
            retained_groups += 1
            continue
        track, metric = _RAW_PARAM_AUTHORITY[param]
        # The writer stamps ``…:<cycle>Z:coordsha:<manifest_sha>`` (see
        # collect_open_ens_cycle), so an equality probe on the bare cycle prefix
        # matches nothing and every group reads as MISSING -> retained forever.
        # 2026-08-21 (0afd62b16) wrote this lookup before the coordinate-manifest
        # identity existed; 2026-09-09 (160b5ff40) added the suffix to the writer
        # without updating this reader, silently disabling raw eviction. Match the
        # cycle+track prefix and require EVERY run recorded for that cycle to be
        # proven: one unproven coordsha retains the group, so a re-pin under a new
        # manifest can never authorize deleting raw that a prior pin still needs.
        source_run_prefix = (
            f"{SOURCE_ID}:{track}:"
            f"{datetime.strptime(day_text, '%Y%m%d').date().isoformat()}T{hour:02d}Z"
        )
        source_runs = conn.execute(
            """
            SELECT status, completeness_status, partial_run,
                   expected_count, observed_count
              FROM source_run
             WHERE source_id = ?
               AND (source_run_id = ? OR source_run_id LIKE ? ESCAPE '\\')
            """,
            (
                SOURCE_ID,
                source_run_prefix,
                _sql_like_escape(source_run_prefix) + ":%",
            ),
        ).fetchall()
        if not source_runs:
            retained_groups += 1
            continue
        observed_counts: set[int] = set()
        source_complete = True
        for source_run in source_runs:
            expected = source_run["expected_count"]
            observed = source_run["observed_count"]
            if not (
                source_run["status"] == "SUCCESS"
                and source_run["completeness_status"] == "COMPLETE"
                and int(source_run["partial_run"]) == 0
                and expected is not None
                and observed is not None
                and int(expected) > 0
                and int(expected) == int(observed)
            ):
                source_complete = False
                break
            observed_counts.add(int(observed))
        # Two pins disagreeing on how many snapshots the cycle owes leaves the
        # snapshot proof below no single number to check against; retain.
        if not source_complete or len(observed_counts) != 1:
            retained_groups += 1
            continue
        observed = next(iter(observed_counts))
        # Same prefix match as the source_run probe above: the snapshots carry the
        # writer's full coordsha-suffixed id, so an equality probe on the bare
        # cycle prefix would count zero and retain even a fully proven cycle.
        snapshot_proof = conn.execute(
            """
            SELECT COUNT(*) AS snapshot_count,
                   SUM(CASE WHEN authority = 'VERIFIED' THEN 1 ELSE 0 END)
                       AS verified_count
              FROM ensemble_snapshots
             WHERE source_id = ?
               AND temperature_metric = ?
               AND (source_run_id = ? OR source_run_id LIKE ? ESCAPE '\\')
            """,
            (
                SOURCE_ID,
                metric,
                source_run_prefix,
                _sql_like_escape(source_run_prefix) + ":%",
            ),
        ).fetchone()
        snapshot_count = int(snapshot_proof["snapshot_count"] or 0)
        verified_count = int(snapshot_proof["verified_count"] or 0)
        if snapshot_count != int(observed) or verified_count != snapshot_count:
            retained_groups += 1
            continue
        eligible_groups += 1
        planned.extend(paths)
        # Canonical observed captures are the evidence already published by
        # normal ingest, not a newly inferred source role or first clock.
        captures: dict[str, dict] = {}
        for snapshot in conn.execute(
            """SELECT snapshot.provenance_json FROM ensemble_snapshots snapshot
               JOIN source_run source ON source.source_run_id=snapshot.source_run_id
               WHERE snapshot.source_id=? AND snapshot.temperature_metric=?
                 AND (snapshot.source_run_id=? OR snapshot.source_run_id LIKE ? ESCAPE '\\')
                 AND source.ingest_mode IN ('SCHEDULED_LIVE','BOOT_CATCHUP')""",
            (SOURCE_ID, metric, source_run_prefix, _sql_like_escape(source_run_prefix) + ":%"),
        ):
            provenance = json.loads(snapshot[0] or "{}")
            capture = provenance.get("native_capture_receipt", {})
            if capture.get("capture_status") == "OBSERVED":
                for message in capture.get("messages", ()):
                    digest = str(message["raw_message_sha256"])
                    if digest in captures and captures[digest] != message:
                        raise ValueError("ROLE_ORIGINAL_CANONICAL_CAPTURE_CONFLICT")
                    captures[digest] = message
        if captures:
            role_originals.append((tuple(paths), tuple(captures.values())))

    if root.exists():
        planned.extend(_orphaned_transport_sidecars(root, planned_canonical=set(planned)))

    live_hashes: set[str] = set()
    reference_errors: list[str] = []
    reference_complete = True
    store = raw_root / "raw/ecmwf_open_ens/role_messages"
    if role_originals or store.exists() or store.is_symlink():
        try:
            selected, _, reference_errors = _role_original_snapshot_references(conn,
                reference_time or datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc),
                raw_root=raw_root)
            # A straddling native interval requires the same-run opposite
            # extrema quantity even when that metric has no separate market.
            for snapshot_id in tuple(selected):
                scope = conn.execute("SELECT city,target_date,temperature_metric,source_cycle_time FROM ensemble_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
                if scope is not None:
                    selected.update(int(r[0]) for r in conn.execute(
                        "SELECT snapshot_id FROM ensemble_snapshots WHERE city=? AND target_date=? AND temperature_metric=? AND source_cycle_time=?",
                        (scope[0], scope[1], "low" if scope[2] == "high" else "high", scope[3])))
            for snapshot_id in selected:
                row = conn.execute("SELECT provenance_json FROM ensemble_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
                if row is None:
                    reference_errors.append("ROLE_RETENTION_SNAPSHOT_REFERENCE_UNKNOWN")
                    continue
                capture = json.loads(row[0])["native_capture_receipt"]
                if capture.get("capture_status") != "OBSERVED" or not capture.get("messages"):
                    reference_errors.append("ROLE_RETENTION_CAPTURE_REFERENCE_UNKNOWN")
                    continue
                live_hashes.update(str(m["raw_message_sha256"]) for m in capture["messages"])
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            reference_errors.append("ROLE_RETENTION_CANONICAL_REFERENCE_UNKNOWN")
        global_unknown = {
            "ROLE_RETENTION_REFERENCE_SCOPE_UNKNOWN", "ROLE_RETENTION_AGE_POLICY_UNBOUNDED",
            "ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN", "ROLE_RETENTION_UNFINISHED_REFERENCE_UNKNOWN",
            "ROLE_RETENTION_COMMAND_AUTHORITY_UNKNOWN", "ROLE_RETENTION_CANONICAL_REFERENCE_UNKNOWN",
            "ROLE_RETENTION_SNAPSHOT_REFERENCE_UNKNOWN", "ROLE_RETENTION_CAPTURE_REFERENCE_UNKNOWN",
            "ROLE_RETENTION_REFERENCE_MUTATED",
        }
        reference_complete = not global_unknown.intersection(reference_errors)
        if reference_complete:
            role_originals = [(sources, tuple(m for m in captures if str(m["raw_message_sha256"]) in live_hashes))
                              for sources, captures in role_originals]

    gc_files = []
    if reference_complete and store.exists() and not store.is_symlink():
        for path in store.glob("*.grib2"):
            if re.fullmatch(r"[0-9a-f]{64}\.grib2", path.name) and not path.is_symlink() and path.is_file():
                stat = path.stat()
                gc_files.append((path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    database = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    return _RawRetentionPlan(
        root=root,
        files=tuple(sorted(planned)),
        eligible_group_count=eligible_groups,
        retained_group_count=retained_groups,
        unrecognized_file_count=unrecognized,
        planned_bytes=sum(path.stat().st_size for path in planned),
        role_originals=tuple(role_originals),
        live_role_hashes=frozenset(live_hashes),
        role_reference_errors=tuple(reference_errors),
        role_reference_complete=reference_complete,
        role_gc_files=tuple(gc_files),
        reference_db=Path(database) if database else None,
        reference_time=reference_time or datetime.combine(reference_date, datetime.min.time(), tzinfo=timezone.utc),
    )


def _apply_decoded_open_data_raw_retention(plan: _RawRetentionPlan) -> dict[str, object]:
    """Apply a proof-built plan without recursion or symlink traversal."""
    deleted_files = 0
    deleted_bytes = 0
    errors: list[str] = []
    parents: set[Path] = set()
    protected: set[Path] = set()
    raw_root = plan.root.parents[2]
    try:
        store = _role_message_path(raw_root, "0" * 64).parent
        unsafe_root = plan.root.is_symlink()
    except ValueError:
        store = None
        unsafe_root = True
    if unsafe_root:
        errors.append("raw_root_became_symlink")
    else:
        for sources, captures in plan.role_originals:
            try:
                _preserve_role_originals(plan.root.parents[2], sources, captures)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # A storage/receipt gap does not undo already-committed
                # mandatory truth; it only prevents this group's raw cleanup.
                protected.update(sources)
                errors.append(f"ROLE_ORIGINAL_RETENTION_DEFERRED:{type(exc).__name__}")
        for path in plan.files:
            if path in protected:
                continue
            parents.add(path.parent)
            if path.parent.parent != plan.root:
                errors.append(f"path_outside_raw_root:{path}")
                continue
            try:
                if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
                    errors.append(f"path_changed:{path}")
                    continue
                size = path.stat().st_size
                path.unlink()
                deleted_files += 1
                deleted_bytes += size
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(f"{path}:{type(exc).__name__}")
        for parent in sorted(parents):
            try:
                parent.rmdir()
            except OSError:
                pass
    status = "ERROR" if errors else ("APPLIED" if plan.files else "NO_ELIGIBLE_RAW")
    role_deleted = 0
    if (not unsafe_root and store is not None and store.exists()
            and plan.role_reference_complete and not protected and plan.role_gc_files):
        if store.is_symlink():
            errors.append("ROLE_ORIGINAL_GC_DIRECTORY_SYMLINK")
        else:
            # The other H/L collector can publish after this plan leaves the
            # writer lock. Never extend the deletion set to its new objects.
            # Refresh exact consumers before unlinking an existing object;
            # timestamps below are mutation fences, not possession or TTL.
            live_hashes = set(plan.live_role_hashes)
            try:
                if plan.reference_db is None or plan.reference_time is None:
                    raise ValueError("ROLE_RETENTION_REFERENCE_REVALIDATION_UNKNOWN")
                from src.state.db import _connect_read_only
                fresh_conn = _connect_read_only(plan.reference_db)
                try:
                    fresh_conn.execute("PRAGMA query_only=ON")
                    fresh = _plan_decoded_open_data_raw_retention(fresh_conn,
                        raw_root=raw_root, reference_date=plan.reference_time.date(),
                        reference_time=plan.reference_time)
                    if not fresh.role_reference_complete:
                        raise ValueError("ROLE_RETENTION_REFERENCE_REVALIDATION_UNKNOWN")
                    live_hashes.update(fresh.live_role_hashes)
                finally:
                    fresh_conn.close()
            except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
                errors.append("ROLE_RETENTION_REFERENCE_REVALIDATION_UNKNOWN")
                live_hashes.update(path.stem for path, *_ in plan.role_gc_files)
            for path, dev, ino, size, modified in plan.role_gc_files:
                if path.stem in live_hashes:
                    continue
                if path.is_symlink() or not path.is_file():
                    errors.append("ROLE_ORIGINAL_GC_FILE_UNKNOWN")
                    continue
                # Original bodies are released only after the complete live
                # reference union is known. Missing bodies are not proof of
                # unreferenced state; unknown authority prevents this sweep.
                try:
                    stat = path.stat()
                    if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (dev, ino, size, modified):
                        continue
                    path.unlink()
                    role_deleted += 1
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    errors.append(f"ROLE_ORIGINAL_GC_DEFERRED:{type(exc).__name__}")
    return {
        "status": "ERROR" if errors else status,
        "eligible_group_count": plan.eligible_group_count,
        "retained_group_count": plan.retained_group_count,
        "unrecognized_file_count": plan.unrecognized_file_count,
        "planned_file_count": len(plan.files),
        "planned_bytes": plan.planned_bytes,
        "deleted_file_count": deleted_files,
        "deleted_bytes": deleted_bytes,
        "errors": errors[:10],
        "role_reference_errors": list(plan.role_reference_errors),
        "role_message_deleted_count": role_deleted,
    }

# ECMWF Open Data is replicated across multiple mirrors. AWS is fastest but
# returns S3 SlowDown when byte-range requests burst. Google rejects multi-range
# GETs, but supports the single-range GETs emitted by Zeus' controlled downloader
# below. The ECMWF origin does not reliably serve byte ranges, so it is opt-in.
_DOWNLOAD_SOURCES: tuple[str, ...] = tuple(
    source.strip()
    for source in os.environ.get("ZEUS_ECMWF_SOURCES", "aws,google").split(",")
    if source.strip()
)

# ---------------------------------------------------------------------------
# Token-bucket rate limiter (D1 throttle antibody — 2026-05-12)
# ---------------------------------------------------------------------------
# AWS S3 / ECMWF multiurl returns HTTP 503 Slow Down when request burst rate
# exceeds provider limits. The token bucket is a module-level singleton shared
# across ALL worker threads and BOTH tracks (mx2t6_high + mn2t6_low run at
# minute=30 and minute=35 respectively via ingest_main.py, so up to
# 2 × _DOWNLOAD_MAX_WORKERS fetches can be in flight simultaneously).
#
# The token bucket caps sustained throughput at ZEUS_ECMWF_RPS regardless of
# worker count. ZEUS_ECMWF_BURST bounds the startup burst independently, while
# 429/503 responses reduce the live rate and successful responses recover it.

class _TokenBucket:
    """Adaptive token-bucket rate limiter (thread-safe).

    Fills at ``rate`` tokens/sec; each ``acquire()`` consumes one token,
    sleeping until a token is available.  Implemented as a leaky-bucket
    gate (refill on demand) rather than a background thread so there is no
    daemon thread to manage across fork/test boundaries.
    """

    def __init__(self, rate: float, *, capacity: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity is not None and capacity <= 0:
            raise ValueError("capacity must be positive")
        self._max_rate = float(rate)
        self._min_rate = min(1.0, self._max_rate)
        self._rate = self._max_rate
        self._capacity = max(1.0, float(capacity if capacity is not None else rate))
        self._lock = threading.Lock()
        self._tokens = self._capacity
        self._last_refill: float = time.monotonic()

    def _refill_locked(self, now: float) -> None:
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self, *, deadline: float | None = None) -> None:
        """Block until one token is available, then consume it."""
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise requests.Timeout("STEP_DEADLINE_EXCEEDED")
            with self._lock:
                self._refill_locked(time.monotonic())
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic()))
            time.sleep(wait)

    def observe(self, status_code: int) -> None:
        """Apply AIMD feedback from a completed provider request."""
        with self._lock:
            self._refill_locked(time.monotonic())
            if status_code in {429, 503}:
                self._rate = max(self._min_rate, self._rate * 0.5)
            elif status_code < 500 and self._rate < self._max_rate:
                self._rate = min(
                    self._max_rate,
                    self._rate + 1.0 / max(self._rate, 1.0),
                )


_DOWNLOAD_RPS: float = float(os.environ.get("ZEUS_ECMWF_RPS", "4.0"))
_DOWNLOAD_BURST: float = float(
    os.environ.get("ZEUS_ECMWF_BURST", str(min(8.0, _DOWNLOAD_RPS)))
)
_fetch_bucket: _TokenBucket = _TokenBucket(_DOWNLOAD_RPS, capacity=_DOWNLOAD_BURST)

# ---------------------------------------------------------------------------
# Parallel-fetch constants (antibody-style: no call-site kwargs).
# Single-writer antibody: SQLite writes are PROHIBITED inside worker threads —
# HTTP fetch only; all DB writes happen on the main thread after futures complete.
# ---------------------------------------------------------------------------
# Env override: ZEUS_ECMWF_MAX_WORKERS (operator-set; survives linter audits).
_DOWNLOAD_MAX_WORKERS: int = int(os.environ.get("ZEUS_ECMWF_MAX_WORKERS", "2"))
_PER_STEP_TIMEOUT_SECONDS: int = int(os.environ.get("ZEUS_ECMWF_STEP_TIMEOUT_SECONDS", "180"))
_PER_STEP_MAX_RETRIES: int = int(os.environ.get("ZEUS_ECMWF_PER_STEP_RETRIES", "2"))
_PER_STEP_RETRY_AFTER: int = int(os.environ.get("ZEUS_ECMWF_PER_STEP_RETRY_AFTER", "5"))
# A release probe is one metadata/index handoff, not a GRIB fetch. Reuse the
# existing retry handoff interval so an unresponsive newest cycle cannot spend
# the older cycle's entire one-minute continuity window.
OPENDATA_AVAILABILITY_PROBE_SECONDS: int = _PER_STEP_RETRY_AFTER
_PROBE_SAFE_CLIENT_SOURCES = frozenset({"aws", "google"})
# 404 → NOT_RELEASED (no retry); all others below trigger retry then failover.
_RETRYABLE_HTTP: frozenset[int] = frozenset({500, 502, 503, 504, 408, 429})


def _remaining_step_timeout(deadline: float | None) -> float:
    """Return the request timeout remaining inside one step-owned deadline."""

    if deadline is None:
        return float(_PER_STEP_TIMEOUT_SECONDS)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout("STEP_DEADLINE_EXCEEDED")
    return max(0.001, min(float(_PER_STEP_TIMEOUT_SECONDS), remaining))


def _sleep_step_retry(deadline: float) -> bool:
    """Sleep only inside the step budget; false means retries are exhausted by time."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(float(_PER_STEP_RETRY_AFTER), remaining))
    return time.monotonic() < deadline


def _deadline_failure_reason(*, cycle_deadline: float | None) -> str:
    if cycle_deadline is not None and time.monotonic() >= cycle_deadline:
        return "CYCLE_DEADLINE_EXCEEDED"
    return "STEP_DEADLINE_EXCEEDED"


def _clamp_request_timeout(timeout: Any, *, deadline: float) -> float | tuple[float, ...]:
    """Apply the remaining cycle budget to every requests timeout shape."""
    remaining = _remaining_step_timeout(deadline)
    if timeout is None:
        return remaining
    if isinstance(timeout, tuple):
        return tuple(
            remaining if value is None else min(float(value), remaining)
            for value in timeout
        )
    try:
        return min(float(timeout), remaining)
    except (TypeError, ValueError):
        return remaining


def _is_ecmwf_download_url(url: str) -> bool:
    return (
        "ecmwf-forecasts" in url
        or "ecmwf-open-data" in url
        or "data.ecmwf.int/forecasts" in url
    )


class _RateLimitedSession(requests.Session):
    """requests.Session that rate-limits ECMWF download HEAD/GET calls."""

    def request(self, method: str, url: str, *args, **kwargs):  # type: ignore[override]
        limited = method.upper() in {"GET", "HEAD"} and _is_ecmwf_download_url(str(url))
        deadline = getattr(self, "_zeus_deadline", None)
        if limited:
            _fetch_bucket.acquire(deadline=deadline)
        if deadline is not None:
            # Do this after a token wait: a caller-supplied None/long timeout
            # cannot outlive the same absolute continuity budget.
            kwargs["timeout"] = _clamp_request_timeout(
                kwargs.get("timeout"), deadline=deadline
            )
        response = super().request(method, url, *args, **kwargs)
        if limited:
            _fetch_bucket.observe(response.status_code)
        return response


class _NativeDeadlineSession(requests.Session):
    """Optional public-original transport with a cancellable total HTTP cut.

    curl owns DNS/connect/headers/body under --max-time; the parent owns the
    same absolute subprocess timeout and reaps it. This is not an idle socket
    timeout or a daemon worker. No unsupported-tool requests fallback exists.
    Only unverified in-flight scratch is disposable; committed native parts
    remain in their original cache across timeout/503/partial normal polls.
    """

    def __init__(self):
        super().__init__()
        self._curl_checked = False

    def get(self, url, **kwargs):
        from urllib.parse import urlsplit
        from requests.utils import select_proxy
        from requests.structures import CaseInsensitiveDict

        deadline = getattr(self, "_zeus_deadline", None)
        if deadline is None:
            raise ValueError("NATIVE_2T_HTTP_DEADLINE_REQUIRED")
        _fetch_bucket.acquire(deadline=deadline)
        if not self._curl_checked:
            try:
                checked = subprocess.run(["/usr/bin/curl", "-q", "--version"], capture_output=True,
                    timeout=_remaining_step_timeout(deadline), check=False)
            except subprocess.TimeoutExpired as exc:
                raise requests.Timeout("STEP_DEADLINE_EXCEEDED") from exc
            except OSError as exc:
                raise ValueError("NATIVE_2T_BOUNDED_HTTP_UNAVAILABLE") from exc
            version = re.match(rb"curl (\d+)\.(\d+)\.(\d+)", checked.stdout)
            if (checked.returncode or version is None
                    or tuple(int(n) for n in version.groups()) < (8, 4, 0)):
                raise ValueError("NATIVE_2T_BOUNDED_HTTP_UNSUPPORTED")
            self._curl_checked = True
        parts = urlsplit(str(url))
        if (parts.scheme not in {"http", "https"} or parts.username or parts.password
                or parts.query or parts.fragment):
            raise ValueError("NATIVE_2T_HTTP_ORIGIN_INVALID")
        headers = CaseInsensitiveDict(kwargs.get("headers", {}))
        maximum = _RANGE_RESUME_CHUNK_BYTES
        if "Range" in headers:
            match = re.fullmatch(r"bytes=(\d+)-(\d+)", headers["Range"])
            if match is None or int(match[2]) < int(match[1]):
                raise ValueError("NATIVE_2T_HTTP_RANGE_INVALID")
            maximum = int(match[2]) - int(match[1]) + 1
            if not 100 <= maximum <= 32 * 1024 * 1024:
                raise ValueError("NATIVE_2T_HTTP_RANGE_INVALID")
        if {name.lower() for name in headers} - {"range"}:
            raise ValueError("NATIVE_2T_HTTP_HEADERS_UNSUPPORTED")
        # Match requests' trust_env/explicit session proxy and verify routing;
        # credentials stay in the child environment, never argv or error logs.
        settings = self.merge_environment_settings(str(url), {}, True, kwargs.get("verify", True), None)
        verify = settings["verify"]
        if verify is False:
            raise ValueError("NATIVE_2T_TLS_VERIFICATION_REQUIRED")
        if settings["cert"] is not None or self.auth is not None or self.cookies:
            raise ValueError("NATIVE_2T_HTTP_AUTH_UNSUPPORTED")
        proxy = select_proxy(str(url), settings["proxies"])
        child_env = {key: value for key, value in os.environ.items()
            if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}}
        if proxy:
            child_env[parts.scheme + "_proxy"] = proxy
        ca = verify if isinstance(verify, str) else requests.certs.where()
        safe_headers = {"content-length", "content-range", "content-type", "content-encoding", "date",
            "last-modified", "etag", "server", "cache-control", "age", "transfer-encoding"}
        with tempfile.TemporaryDirectory(prefix="zeus-native-http-") as directory:
            body_path, header_path = Path(directory) / "body", Path(directory) / "headers"
            # No redirect, retry, decompression, curlrc, insecure TLS or second
            # budget. libcurl >=8.4 also caps unknown-length/chunked bodies.
            remaining = _remaining_step_timeout(deadline)
            command = ["/usr/bin/curl", "-q", "--silent", "--proto", "=" + parts.scheme,
                "--max-time", str(remaining), "--max-filesize", str(maximum),
                "--header", "Accept-Encoding: identity", "--dump-header", str(header_path),
                "--output", str(body_path), "--write-out", "%{http_code}",
                "--capath" if Path(ca).is_dir() else "--cacert", str(ca)]
            if "Range" in headers:
                command.extend(["--header", "Range: " + headers["Range"]])
            command.extend(["--url", str(url)])
            try:
                completed = subprocess.run(command, env=child_env, capture_output=True,
                    timeout=_remaining_step_timeout(deadline), check=False)
            except subprocess.TimeoutExpired as exc:
                raise requests.Timeout("STEP_DEADLINE_EXCEEDED") from exc
            except OSError as exc:
                raise ValueError("NATIVE_2T_BOUNDED_HTTP_UNAVAILABLE") from exc
            if completed.returncode == 28:
                raise requests.Timeout("STEP_DEADLINE_EXCEEDED")
            if completed.returncode:
                # curl stderr can contain a proxy credential or private route.
                raise requests.RequestException("NATIVE_2T_HTTP_TRANSFER_FAILED:" + str(completed.returncode))
            _remaining_step_timeout(deadline)
            if (not re.fullmatch(rb"\d{3}", completed.stdout) or not body_path.is_file()
                    or body_path.stat().st_size > maximum or not header_path.is_file()
                    or header_path.stat().st_size > 65536):
                raise ValueError("NATIVE_2T_HTTP_ENVELOPE_INVALID")
            blocks = [block for block in header_path.read_bytes().split(b"\r\n\r\n") if block]
            # Proxy CONNECT headers and informational responses are not the
            # origin entity. Bind the final status/header block only.
            lines = blocks[-1].decode("latin1").split("\r\n") if blocks else []
            status = re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", lines[0]) if lines else None
            if status is None or int(status[1]) != int(completed.stdout):
                raise ValueError("NATIVE_2T_HTTP_ENVELOPE_INVALID")
            captured = CaseInsensitiveDict()
            for line in lines[1:]:
                name, sep, value = line.partition(":")
                if not sep or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                    raise ValueError("NATIVE_2T_HTTP_HEADERS_INVALID")
                if name.lower() in safe_headers:
                    if name in captured:
                        raise ValueError("NATIVE_2T_HTTP_HEADER_DUPLICATE")
                    captured[name] = value.strip()
            if captured.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("NATIVE_2T_HTTP_ENCODING_UNSUPPORTED")
            content = body_path.read_bytes()
            if "Content-Length" in captured and captured["Content-Length"] != str(len(content)):
                raise ValueError("NATIVE_2T_HTTP_LENGTH_INVALID")
            response = requests.Response()
            response.status_code, response.headers, response.url = int(status[1]), captured, str(url)
            response._content, response._content_consumed = content, True
            response._native_body_complete = True
            _fetch_bucket.observe(response.status_code)
            return response


def _part_offset_length(part: Any) -> tuple[int, int]:
    """Return an ECMWF index part as ``(offset, length)``.

    ecmwf-opendata currently returns plain tuples; multiurl wraps them as part
    objects internally. Supporting both keeps this helper stable across package
    updates without delegating download control back to multiurl.
    """

    if hasattr(part, "offset") and hasattr(part, "length"):
        return int(part.offset), int(part.length)
    offset, length = part
    return int(offset), int(length)


def _http_error_for_response(response: requests.Response, message: str) -> requests.HTTPError:
    err = requests.HTTPError(message)
    err.response = response
    return err


_RANGE_RESUME_VERSION = 1
# An index entry can represent a whole field collection, so its natural length
# is not a safe progress unit for the 180-second per-step deadline.
_RANGE_RESUME_CHUNK_BYTES = 8 * 1024 * 1024


_RANGE_RESUME_MANIFEST_SUFFIX = ".ranges.json"


def _range_resume_manifest_path(target: Path) -> Path:
    """Return the sidecar that proves which byte-range prefix is reusable."""

    return target.with_name(f"{target.name}{_RANGE_RESUME_MANIFEST_SUFFIX}")


def _resume_source_namespace(source: str) -> str:
    """Return a short, deterministic namespace for one configured source."""

    return hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]


def _source_partial_path(legacy_target: Path, *, kind: str, source: str) -> Path:
    """Return the source-specific PF/CF partial path.

    ``legacy_target`` is the pre-namespace ``*.grib2.partial`` path. Keeping
    the source suffix before ``.partial`` leaves the canonical step filename
    unchanged while preventing mirror rotation from sharing range sidecars.
    """

    if kind not in {"pf", "cf"}:
        raise ValueError(f"Unknown ECMWF partial kind {kind!r}")
    return legacy_target.with_name(
        f"{legacy_target.stem}.{kind}.{_resume_source_namespace(source)}"
        f"{legacy_target.suffix}"
    )


def _path_present(path: Path) -> bool:
    """Treat broken symlinks as existing destinations during adoption."""

    return path.exists() or path.is_symlink()


def _url_is_under_source(url: object, source_base: object) -> bool:
    """Return whether a persisted URL belongs to one exact source prefix."""

    if not isinstance(url, str) or not url:
        return False
    base = str(source_base or "").strip().rstrip("/")
    if not base:
        return False
    return url == base or url.startswith(f"{base}/")


def _legacy_checkpoint_matches_source(target: Path, *, source_base: object) -> bool:
    """Validate enough legacy metadata to move it without cross-source reuse."""

    manifest = _range_resume_manifest_path(target)
    if (
        not target.is_file()
        or target.is_symlink()
        or not manifest.is_file()
        or manifest.is_symlink()
    ):
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return False
        ranges = payload.get("ranges")
        entity_tags = payload.get("entity_tags")
        completed = payload.get("completed_ranges")
        completed_bytes = payload.get("completed_bytes")
        if (
            payload.get("version") != _RANGE_RESUME_VERSION
            or not isinstance(ranges, list)
            or not ranges
            or not isinstance(entity_tags, dict)
            or not isinstance(completed, int)
            or completed < 0
            or completed > len(ranges)
            or not isinstance(completed_bytes, int)
            or completed_bytes < 0
            or target.stat().st_size < completed_bytes
        ):
            return False
        for item in ranges:
            if not isinstance(item, list) or len(item) != 3:
                return False
            if (
                not _url_is_under_source(item[0], source_base)
                or isinstance(item[1], bool)
                or not isinstance(item[1], int)
                or item[1] < 0
                or isinstance(item[2], bool)
                or not isinstance(item[2], int)
                or item[2] <= 0
            ):
                return False
        if any(
            not _url_is_under_source(url, source_base)
            or not _is_strong_etag(tag)
            for url, tag in entity_tags.items()
        ):
            return False
        expected_bytes = sum(item[2] for item in ranges[:completed])
        expected_urls = {item[0] for item in ranges[:completed]}
        if completed_bytes != expected_bytes or set(entity_tags) != expected_urls:
            return False
    except (OSError, TypeError, ValueError, AttributeError, json.JSONDecodeError, KeyError):
        return False
    return True


def _adopt_legacy_checkpoint(
    legacy_target: Path,
    destination: Path,
    *,
    source_base: object,
) -> bool:
    """Move one proven legacy body+sidecar pair into a vacant source namespace.

    Hardlinks followed by source unlink provide a same-directory, pair-safe
    move: an interruption before both links exist leaves the legacy pair
    intact, while a partially-created destination is never accepted without
    the existing loader's full plan/ETag validation.
    """

    legacy_manifest = _range_resume_manifest_path(legacy_target)
    destination_manifest = _range_resume_manifest_path(destination)
    if _path_present(destination) or _path_present(destination_manifest):
        return False
    if not _legacy_checkpoint_matches_source(legacy_target, source_base=source_base):
        return False

    created: list[Path] = []
    try:
        os.link(str(legacy_manifest), str(destination_manifest))
        created.append(destination_manifest)
        os.link(str(legacy_target), str(destination))
        created.append(destination)
    except FileExistsError:
        for path in created:
            path.unlink(missing_ok=True)
        return False
    except OSError:
        for path in created:
            path.unlink(missing_ok=True)
        return False

    try:
        legacy_manifest.unlink()
        legacy_target.unlink()
    except OSError:
        # The destination is complete and remains valid; leaving a duplicate
        # legacy pair is safer than deleting a checkpoint after partial cleanup.
        logger.warning("ECMWF legacy range checkpoint cleanup incomplete for %s", legacy_target)
    return True


def _is_strong_etag(value: Any) -> bool:
    """Return whether an HTTP ETag can identify byte-for-byte entity content."""

    return isinstance(value, str) and bool(value) and not value.lstrip().startswith("W/")


def _range_resume_plan(urls: Any) -> tuple[tuple[str, int, int], ...] | None:
    """Flatten indexed URLs into the exact ordered range identity we assemble."""

    plan: list[tuple[str, int, int]] = []
    for item in urls:
        if not (
            isinstance(item, tuple)
            and len(item) == 2
            and not isinstance(item[1], (str, bytes))
        ):
            return None
        url, parts = item
        for part in parts:
            offset, length = _part_offset_length(part)
            if offset < 0 or length <= 0:
                raise ValueError(f"Invalid indexed range offset={offset} length={length}")
            remaining = length
            chunk_offset = offset
            while remaining:
                chunk_length = min(remaining, _RANGE_RESUME_CHUNK_BYTES)
                plan.append((str(url), chunk_offset, chunk_length))
                chunk_offset += chunk_length
                remaining -= chunk_length
    return tuple(plan) if plan else None


def _reset_range_resume(target: Path) -> None:
    """Discard an unverified partial prefix; it must never reach canonical GRIB."""

    target.unlink(missing_ok=True)
    _range_resume_manifest_path(target).unlink(missing_ok=True)


def _write_range_resume_manifest(
    target: Path,
    *,
    plan: tuple[tuple[str, int, int], ...],
    completed_ranges: int,
    entity_tags: Mapping[str, str],
) -> None:
    """Atomically checkpoint only a fully received, fsync'd range prefix."""

    completed_bytes = sum(length for _, _, length in plan[:completed_ranges])
    payload = {
        "version": _RANGE_RESUME_VERSION,
        "ranges": [list(part) for part in plan],
        "completed_ranges": completed_ranges,
        "completed_bytes": completed_bytes,
        "entity_tags": dict(entity_tags),
    }
    manifest = _range_resume_manifest_path(target)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=manifest.parent,
        prefix=f".{manifest.name}.",
        suffix=".tmp",
        delete=False,
    ) as out:
        temp_path = Path(out.name)
        json.dump(payload, out, sort_keys=True, separators=(",", ":"))
        out.flush()
        os.fsync(out.fileno())
    try:
        os.replace(temp_path, manifest)
    finally:
        temp_path.unlink(missing_ok=True)


def _load_range_resume(
    target: Path,
    *,
    plan: tuple[tuple[str, int, int], ...],
    session: Any,
    verify: Any,
    deadline: float | None,
) -> tuple[int, dict[str, str]]:
    """Return a verified reusable prefix, resetting malformed or changed cache.

    A partial body is not a completed range.  The sidecar is written only after
    the range bytes have been fsync'd, and an existing prefix is accepted only
    when the current index plan and the source entity ETags still agree.
    """

    manifest = _range_resume_manifest_path(target)
    if not target.exists() and not manifest.exists():
        return 0, {}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        completed = payload["completed_ranges"]
        completed_bytes = payload["completed_bytes"]
        entity_tags = payload["entity_tags"]
        expected_ranges = [list(part) for part in plan]
        if (
            payload.get("version") != _RANGE_RESUME_VERSION
            or payload.get("ranges") != expected_ranges
            or not isinstance(completed, int)
            or not 0 <= completed <= len(plan)
            or not isinstance(completed_bytes, int)
            or completed_bytes != sum(length for _, _, length in plan[:completed])
            or not isinstance(entity_tags, dict)
            or target.stat().st_size < completed_bytes
        ):
            raise ValueError("range resume sidecar does not match current source identity")
        expected_urls = {url for url, _, _ in plan[:completed]}
        if set(entity_tags) != expected_urls or any(
            not _is_strong_etag(tag) for tag in entity_tags.values()
        ):
            raise ValueError("range resume sidecar has incomplete entity identity")
    except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
        logger.warning("Discarding invalid ECMWF range resume cache for %s", target)
        _reset_range_resume(target)
        return 0, {}

    for url in sorted(expected_urls):
        response = session.head(
            url,
            timeout=_remaining_step_timeout(deadline),
            verify=verify,
        )
        try:
            if response.status_code != 200:
                if response.status_code == 429 or response.status_code >= 500:
                    # A transient validation failure says nothing about the
                    # entity. Preserve the verified prefix so the caller's
                    # retry/failover loop can resume it later.
                    raise _http_error_for_response(
                        response,
                        f"ECMWF resume entity validation transiently unavailable "
                        f"(HTTP {response.status_code}) for {url}",
                    )
                logger.info(
                    "ECMWF resume entity validation is unavailable (HTTP %s) for %s; restarting full partial",
                    response.status_code,
                    url,
                )
                _reset_range_resume(target)
                return 0, {}
            entity_tag = getattr(response, "headers", {}).get("ETag")
            if not _is_strong_etag(entity_tag):
                logger.info(
                    "ECMWF resume entity validation has no strong ETag for %s; restarting full partial",
                    url,
                )
                _reset_range_resume(target)
                return 0, {}
            if entity_tag != entity_tags[url]:
                logger.warning("Discarding ECMWF range resume cache after entity change for %s", url)
                _reset_range_resume(target)
                return 0, {}
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    with target.open("r+b") as out:
        out.truncate(completed_bytes)
    return completed, dict(entity_tags)


def _validate_range_response(
    response: Any,
    *,
    offset: int,
    length: int,
) -> str | None:
    """Prove a range response is exactly the requested bytes before checkpointing."""

    end = offset + length - 1
    if response.status_code != 206:
        if response.status_code >= 400:
            response.raise_for_status()
        raise _http_error_for_response(
            response,
            f"Expected HTTP 206 for range GET, got {response.status_code}",
        )
    headers = getattr(response, "headers", {})
    content_range = headers.get("Content-Range")
    if content_range is None:
        raise _http_error_for_response(response, "Range GET omitted Content-Range")
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(?:\d+|\*)", content_range.strip())
    if not match or (int(match.group(1)), int(match.group(2))) != (offset, end):
        raise _http_error_for_response(
            response,
            f"Range GET Content-Range {content_range!r} does not match bytes={offset}-{end}",
        )
    content_length = headers.get("Content-Length")
    if content_length is not None:
        try:
            if int(content_length) != length:
                raise ValueError
        except ValueError:
            raise _http_error_for_response(
                response,
                f"Range GET Content-Length {content_length!r} does not match {length}",
            )
    return headers.get("ETag")


def _resolve_index_parts(
    client: Any,
    result: Any,
    *,
    deadline: float | None = None,
    original_indexes: list[dict] | None = None,
    native_complete_body: bool = False,
) -> list[tuple[str, tuple[tuple[int, int], ...]]]:
    """Resolve ECMWF ``.index`` parts without multiurl's 120-second retry loop."""

    for_index = getattr(result, "for_index", {}) or {}
    if not for_index:
        return []

    verify = getattr(client, "verify", True)
    resolved: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    for url in result.urls:
        base, _ = os.path.splitext(str(url))
        index_url = f"{base}.index"
        started = datetime.now(timezone.utc).isoformat() if original_indexes is not None else None
        response = client.session.get(
            index_url,
            stream=True,
            timeout=_remaining_step_timeout(deadline),
            verify=verify,
        )
        try:
            if response.status_code != 200:
                response.raise_for_status()
            parts: list[tuple[int, int]] = []
            if original_indexes is not None:
                if native_complete_body:
                    _remaining_step_timeout(deadline)
                    if getattr(response, "_native_body_complete", False) is not True:
                        raise ValueError("NATIVE_2T_HTTP_BODY_NOT_BOUNDED")
                    body = response.content
                    if len(body) > _RANGE_RESUME_CHUNK_BYTES:
                        raise ValueError("NATIVE_2T_INDEX_BODY_OVERSIZED")
                else:
                    chunks, size = [], 0
                    for chunk in response.iter_content(chunk_size=65536):
                        _remaining_step_timeout(deadline)
                        size += len(chunk)
                        if size > _RANGE_RESUME_CHUNK_BYTES:
                            raise ValueError("NATIVE_2T_INDEX_BODY_OVERSIZED")
                        chunks.append(chunk)
                    body = b"".join(chunks)
                original_indexes.append({"source_url": str(url), "source_index_url": index_url,
                    "body": body, "fetch_started_at": started,
                    "source_fetched_at": datetime.now(timezone.utc).isoformat(),
                    "http": {"status": response.status_code, "headers": dict(response.headers)}})
                lines = body.splitlines()
            else:
                lines = response.iter_lines()
            for raw_line in lines:
                _remaining_step_timeout(deadline)
                if not raw_line:
                    continue
                line = json.loads(raw_line)
                if all(line.get(name) in values for name, values in for_index.items()):
                    parts.append((int(line["_offset"]), int(line["_length"])))
            if parts:
                resolved.append((str(url), tuple(sorted(parts))))
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    if not resolved:
        raise ValueError(f"Cannot find index entries matching {for_index!r}")
    return resolved


def _read_static_mask_range(session: Any, url: str, offset: int, length: int, *, deadline: float | None = None,
                            receipt: dict | None = None) -> bytes:
    """Read exactly one indexed static LSM GRIB message; never the full GRIB."""
    if not 0 <= offset or not 100 <= length <= 1024 * 1024:
        raise ValueError("ENS_LAND_MASK_INDEX_BOUNDS_INVALID")
    response = session.get(
        url, stream=True,
        headers={"Range": f"bytes={offset}-{offset + length - 1}"},
        timeout=_remaining_step_timeout(deadline),
    )
    try:
        _validate_range_response(response, offset=offset, length=length)
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            _remaining_step_timeout(deadline)
            total += len(chunk)
            if total > length:
                raise ValueError("ENS_LAND_MASK_RANGE_OVERSIZED")
            chunks.append(chunk)
        message = b"".join(chunks)
        if len(message) != length or not message.startswith(b"GRIB") or not message.endswith(b"7777"):
            raise ValueError("ENS_LAND_MASK_MESSAGE_INVALID")
        if receipt is not None:
            receipt.update(status=response.status_code, headers=dict(response.headers),
                           source_fetched_at=datetime.now(timezone.utc).isoformat())
        return message
    finally:
        response.close()


def _fetch_cycle_land_mask(
    *, cycle_date: date, cycle_hour: int, output_path: Path,
    deadline: float | None = None,
) -> dict[str, object]:
    """Acquire bounded same-cycle IFS control static geometry for ENS extraction."""
    from ecmwf.opendata import Client
    from scripts.extract_open_ens_localday import _read_land_mask

    proof_path = output_path.with_suffix(".proof.json")
    cycle = datetime.combine(cycle_date, datetime.min.time(), timezone.utc).replace(hour=cycle_hour)
    # SCOPE: this exact static run/body. DRAIN: restore its original committed
    # evidence; RESET: a valid original or an independent new run. Old caches
    # without indexes remain diagnostic; re-fetching cannot renew their clock.
    if proof_path.exists():
        if output_path.is_symlink() or proof_path.is_symlink():
            raise ValueError("ENS_LAND_MASK_CACHE_SYMLINK")
        before = output_path.read_bytes(), proof_path.read_bytes()
        observed = _read_land_mask(output_path, proof_path)
        proof = observed["proof"]
        fetched = datetime.fromisoformat(str(proof["source_fetched_at"]))
        if (proof["source_cycle_time"] != cycle.isoformat() or fetched.tzinfo is None
                or not cycle <= fetched <= datetime.now(timezone.utc)
                or proof["source_index_length"] != len(before[0])
                or (output_path.read_bytes(), proof_path.read_bytes()) != before):
            raise ValueError("ENS_LAND_MASK_CACHE_ORIGINAL_INVALID")
        return {**proof, "mask_grid_identity_hash": observed["grid_identity_hash"]}
    # A body without the final proof marker has no committed possession clock.
    # Preserve that interrupted publication, then normal bounded acquisition can
    # drain it. This is not permission to replace a committed invalid original.
    for orphan in (output_path, output_path.with_suffix(".index.body")):
        if orphan.exists():
            if orphan.is_symlink():
                raise ValueError("ENS_LAND_MASK_CACHE_SYMLINK")
            digest = hashlib.sha256(orphan.read_bytes()).hexdigest()
            preserved = orphan.with_name(f"{orphan.name}.uncommitted-{digest}")
            if preserved.exists():
                if preserved.read_bytes() != orphan.read_bytes():
                    raise ValueError("ENS_LAND_MASK_ORPHAN_HASH_CONFLICT")
                orphan.unlink()
            else:
                orphan.rename(preserved)

    last_error: Exception | None = None
    for mirror in _DOWNLOAD_SOURCES:
        try:
            client = Client(source=mirror)
            client.session = _RateLimitedSession()
            client.session._zeus_deadline = deadline
            result = client._get_urls(
                target=str(output_path), use_index=False,
                date=int(cycle_date.strftime("%Y%m%d")), time=cycle_hour,
                stream="oper", type=["fc"], step=[0], param=["lsm"],
            )
            originals: list[dict] = []
            parts = _resolve_index_parts(client, result, deadline=deadline, original_indexes=originals)
            if len(parts) != 1 or len(parts[0][1]) != 1:
                raise ValueError("ENS_LAND_MASK_INDEX_NOT_SINGLE_MESSAGE")
            url, ((offset, length),) = parts[0]
            range_receipt: dict = {}
            message = _read_static_mask_range(
                client.session, url, offset, length, deadline=deadline, receipt=range_receipt,
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            proof: dict[str, object] = {
                "source": "ecmwf_open_data_ifs_oper_fc_step0_lsm",
                "source_url": url,
                "source_index_url": f"{os.path.splitext(url)[0]}.index",
                "source_cycle_time": datetime.combine(
                    cycle_date, datetime.min.time(), timezone.utc
                ).replace(hour=cycle_hour).isoformat(),
                "source_index_offset": offset,
                "source_index_length": length,
                "source_fetched_at": range_receipt.pop("source_fetched_at"),
                "mask_sha256": hashlib.sha256(message).hexdigest(),
                "source_index_sha256": hashlib.sha256(originals[0]["body"]).hexdigest(),
                "index_http": originals[0]["http"], "range_http": range_receipt,
                "index_first_possession_at": originals[0]["source_fetched_at"],
                "source_issued_at": None,
            }
            with tempfile.TemporaryDirectory(prefix=".lsm_capture_", dir=output_path.parent) as temp:
                staging = Path(temp)
                body_path, index_path, marker = staging / "lsm.grib2", staging / "index.body", staging / "proof.json"
                for path, data in ((body_path, message), (index_path, originals[0]["body"]),
                                   (marker, json.dumps(proof, sort_keys=True).encode())):
                    with path.open("wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                decoded = _read_land_mask(body_path, marker)
                _remaining_step_timeout(deadline)
                os.replace(body_path, output_path)
                os.replace(index_path, output_path.with_suffix(".index.body"))
                os.replace(marker, proof_path)  # Last publication is the commit marker.
            proof["mask_grid_identity_hash"] = decoded["grid_identity_hash"]
            return proof
        except (OSError, ValueError, requests.RequestException) as exc:
            last_error = exc
            # A publication failure is drained on the next poll, not retried
            # through another mirror after bytes have been locally published.
            if output_path.exists():
                break
    raise ValueError(f"ENS_LAND_MASK_UNAVAILABLE:{type(last_error).__name__ if last_error else 'NO_MIRROR'}")


def _native_temperature_index_parts(body: bytes, run: datetime, step: int, *, control: bool) -> list[dict]:
    """Exact original-index 2t fields; control follows the 50r1 oper/fc envelope."""
    wanted = {"param": "2t", "levtype": "sfc", "class": "od", "date": run.strftime("%Y%m%d"),
              "time": run.strftime("%H%M"), "step": str(step),
              "stream": "oper" if control else "enfo", "type": "fc" if control else "pf"}
    parts = []
    for line in body.splitlines():
        row = json.loads(line)
        if row.get("param") != "2t":
            continue
        if any(str(row.get(k)) != v for k, v in wanted.items()):
            raise ValueError("NATIVE_2T_INDEX_IDENTITY_MISMATCH")
        number = str(row.get("number", "0"))
        if not re.fullmatch(r"\d+", number):
            raise ValueError("NATIVE_2T_INDEX_MEMBER_INVALID")
        member = int(number)
        offset, length = row["_offset"], row["_length"]
        if (type(offset) is not int or offset < 0 or type(length) is not int
                or not 100 <= length <= 32 * 1024 * 1024):
            raise ValueError("NATIVE_2T_INDEX_RANGE_INVALID")
        parts.append({"member": member, "source_index_offset": offset, "source_index_length": length,
                      "source_index_line_sha256": hashlib.sha256(line).hexdigest(), "index_record": row})
    expected = [0] if control else list(range(1, 51))
    if sorted(p["member"] for p in parts) != expected:
        raise ValueError("NATIVE_2T_INDEX_MEMBER_SET_INCOMPLETE")
    ordered = sorted(parts, key=lambda p: p["source_index_offset"])
    if any(a["source_index_offset"] + a["source_index_length"] > b["source_index_offset"]
           for a, b in zip(ordered, ordered[1:])):
        raise ValueError("NATIVE_2T_INDEX_RANGES_OVERLAP")
    return ordered


_native_temperature_source_lock = threading.Lock()


def _native_mirror_attempt(cache: Path, root: Path, run: datetime, mirrors: tuple[str, ...],
                           update: dict | None = None) -> dict:
    """Runtime scheduling only; no original reader or identity consumes this file.

    SCOPE: this normal run's diagnostic slot. DRAIN: restore a regular, valid
    slot on a normal poll. RESET: valid receipt or an independent run; an
    already verified complete subset does not depend on this cursor.
    """
    import stat
    for parent in (cache, *cache.parents):
        if parent.is_symlink():
            raise ValueError("NATIVE_2T_MIRROR_DIAGNOSTIC_ALIAS")
        if parent == root:
            break
    path = cache / "mirror-attempt.json"
    previous = {}
    if _path_present(path):
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > 65536:
            raise ValueError("NATIVE_2T_MIRROR_DIAGNOSTIC_ALIAS")
        try:
            previous = json.loads(path.read_bytes())
            started = datetime.fromisoformat(previous["attempt_started_at"])
            finished = datetime.fromisoformat(previous["attempt_finished_at"]) if previous.get("attempt_finished_at") else None
            if (previous["version"] != 1 or previous["run_time_utc"] != run.isoformat()
                    or previous["ingest_mode"] != "SCHEDULED_LIVE"
                    or previous["status"] not in {"RUNNING", "FAILED", "PARTIAL", "COMPLETE"}
                    or previous["last_attempted_mirror"] not in previous["configured_mirrors"]
                    or any(mirror not in {"aws", "google"} for mirror in previous["configured_mirrors"])
                    or started.tzinfo is None or not run <= started <= datetime.now(timezone.utc)
                    or (finished and (finished.tzinfo is None or not started <= finished <= datetime.now(timezone.utc)))):
                raise ValueError("invalid mirror attempt")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("NATIVE_2T_MIRROR_DIAGNOSTIC_INVALID") from exc
    if update is not None:
        content = json.dumps({"version": 1, "run_time_utc": run.isoformat(), "ingest_mode": "SCHEDULED_LIVE",
            "configured_mirrors": list(mirrors), **update}, sort_keys=True).encode()
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return previous


def _promote_native_mirror_part(path: Path, cache: Path, run: datetime) -> dict:
    """Publish a verified missing part without changing any original receipt."""
    record, raw = _read_native_temperature_record(path, run)
    proof = json.loads(path.with_suffix(".grib2.proof.json").read_bytes())
    if proof.get("ingest_mode") != "SCHEDULED_LIVE":
        raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
    # Recovery publishes only from the configured replica's own stage. A
    # misplaced original is evidence to restore, not permission to relabel it.
    origins = {"aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/",
        "google": "https://storage.googleapis.com/ecmwf-open-data/"}
    mirror = next((mirror for mirror in _DOWNLOAD_SOURCES if mirror in origins
        and path.parent == cache / f".mirror-{_resume_source_namespace(mirror)}.partial"), None)
    if mirror is None or not record["source_url"].startswith(origins[mirror]):
        raise ValueError("NATIVE_2T_MIRROR_STAGE_ORIGIN_INVALID")
    index = path.parent / proof["index_path"]
    # Links also retain the staged proof across interrupted publication. An
    # identical body-only publication can restore this exact proof, not recapture.
    for origin, destination in ((index, cache / index.name),
            (index.with_suffix(".http.json"), cache / index.with_suffix(".http.json").name),
            (path, cache / path.name),
            (path.with_suffix(".grib2.proof.json"), cache / path.with_suffix(".grib2.proof.json").name)):
        if origin.is_symlink() or not origin.is_file():
            raise ValueError("NATIVE_2T_MIRROR_STAGE_ALIAS")
        if _path_present(destination):
            if destination.is_symlink() or not destination.is_file() or destination.read_bytes() != origin.read_bytes():
                raise ValueError("NATIVE_2T_MIRROR_PUBLICATION_CONFLICT")
        else:
            os.link(origin, destination)
    return _read_native_temperature_record(cache / path.name, run)[0]


def collect_native_temperature_source(*, conn: sqlite3.Connection, run_utc: datetime,
        required_steps: list[int], cycle_deadline_monotonic: float,
        _priority: Any, _paths: OpenDataPaths | None = None) -> dict:
    """Drain current target knots on the ordinary scheduler's remaining budget.

    SCOPE: optional native product/run/member-step, never mandatory H/L or q.
    DRAIN: next normal poll resumes individually proven parts after priority,
    deadline or HTTP deferral. RESET: singleflight releases in finally; missing
    parts retry next poll without renewing retained bytes or possession clocks.
    step0 is a forecast knot, not a prior observation; 3h/6h stays native.
    """
    report = {"status": "DEFERRED", "qualification_status": "UNKNOWN",
        "source_issued_at": None, "available_at": None, "observed_count": 0}
    if not callable(_priority):
        return {**report, "reason": "NATIVE_2T_PRIORITY_UNKNOWN"}
    def admitted():
        _remaining_step_timeout(cycle_deadline_monotonic)
        if _priority() is not True:
            raise ValueError("NATIVE_2T_MANDATORY_PRIORITY")
        _remaining_step_timeout(cycle_deadline_monotonic)
    try:
        steps = _native_temperature_steps(run_utc, required_steps)
    except (ValueError, requests.Timeout) as exc:
        return {**report, "reason": str(exc)}
    if not _native_temperature_source_lock.acquire(blocking=False):
        return {**report, "reason": "NATIVE_2T_SINGLEFLIGHT_BUSY"}
    session = None
    try:
        paths = _paths or _resolve_opendata_paths()
        cache = paths.raw_root / "raw" / "ecmwf_open_ens" / "native_2t_scheduled" / f"{run_utc:%Y%m%dT%HZ}"
        manifest = cache / "source-manifest.json"
        manifest_bytes = manifest.read_bytes() if manifest.exists() else None
        previous = json.loads(manifest_bytes) if manifest_bytes is not None else {}
        anchors = conn.execute("""
            SELECT source_run_id, manifest_hash, ingest_mode, origin_mode FROM source_run
             WHERE source_id='ecmwf_open_data' AND track='2t_instant_native_knots'
               AND source_cycle_time=? AND source_run_id GLOB ?
        """, (run_utc.isoformat(),
            f"ecmwf_open_data:2t_instant_native_knots:{run_utc:%Y%m%dT%H%MZ}:*:origin:scheduled_live")).fetchall()
        # SCOPE: exact run/normal origin. DRAIN: restore the canonical-bound
        # original manifest and proofs; each poll rechecks them. RESET: a true
        # hash/clock restoration (or an independent new run) admits work again.
        # Valid step appends still update the hash after validating the old one.
        if anchors:
            if (len(anchors) != 1 or manifest_bytes is None
                    or hashlib.sha256(manifest_bytes).hexdigest() != anchors[0]["manifest_hash"]
                    or previous.get("source_run_id") != anchors[0]["source_run_id"]
                    or previous.get("ingest_mode") != "SCHEDULED_LIVE"
                    or (anchors[0]["ingest_mode"], anchors[0]["origin_mode"]) != ("SCHEDULED_LIVE", "SCHEDULED_LIVE")):
                raise ValueError("NATIVE_2T_CANONICAL_MANIFEST_UNBOUND")
        elif previous:
            raise ValueError("NATIVE_2T_CANONICAL_MANIFEST_UNBOUND")
        plan = sorted(set(steps) | set(previous.get("product_steps", [])))
        old = {(m["member"], m["step_hours"]): m for m in previous.get("retained_identities", [])}
        # Validate before requesting: unproved old bytes cannot become a first
        # normal capture, and a tampered identity must not be overwritten.
        retained = {}
        for path in sorted(cache.glob("step*-member*.grib2")):
            if not _path_present(path.with_suffix(".grib2.proof.json")):
                # Only a complete staged original can repair interrupted
                # promotion. Bound old parts additionally require the exact
                # original proof digest; a new replica cannot renew its clock.
                for mirror in _DOWNLOAD_SOURCES:
                    staged_path = cache / f".mirror-{_resume_source_namespace(mirror)}.partial" / path.name
                    if not _path_present(staged_path) or staged_path.parent.is_symlink():
                        continue
                    candidate, raw = _read_native_temperature_record(staged_path, run_utc)
                    key = candidate["member"], candidate["step_hours"]
                    if (raw == path.read_bytes()
                            and (key not in old or candidate["proof_sha256"] == old[key]["proof_sha256"])):
                        if key not in old:
                            candidate_origin = candidate["source_url"].split(f"/{run_utc:%Y%m%d}/", 1)[0]
                            for old_key, saved in old.items():
                                if saved["source_url"].startswith(candidate_origin + "/"):
                                    continue
                                prefix_path = staged_path.parent / f"step{old_key[1]:03d}-member{old_key[0]:02d}.grib2"
                                comparable, _ = _read_native_temperature_record(prefix_path, run_utc)
                                if comparable["raw_message_sha256"] != saved["raw_message_sha256"]:
                                    raise ValueError("NATIVE_2T_MIRROR_COHORT_DIVERGED")
                        _promote_native_mirror_part(staged_path, cache, run_utc)
                        break
            record, _ = _read_native_temperature_record(path, run_utc)
            proof = json.loads(path.with_suffix(".grib2.proof.json").read_bytes())
            if proof.get("ingest_mode") != "SCHEDULED_LIVE":
                raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
            retained[(record["member"], record["step_hours"])] = record
        if any(key in retained and retained[key] != saved for key, saved in old.items()):
            raise ValueError("NATIVE_2T_RETAINED_ORIGINAL_CHANGED")
        if any(key not in retained for key in old):
            # A removed original is repair debt, not permission to mint it again.
            raise ValueError("NATIVE_2T_RETAINED_ORIGINAL_MISSING")
        if all((member, step) in retained for step in steps for member in range(51)):
            source = persist_native_temperature_source_run(conn, cache_dir=cache, manifest_path=manifest,
                expected_run_utc=run_utc, product_steps=plan, ingest_mode="SCHEDULED_LIVE")
            # The target subset may be complete while another city's appended
            # product steps are still PARTIAL. Never upgrade the global row.
            return {**report, "status": "AVAILABLE" if source.source_run_id else source.status,
                "source_run_id": source.source_run_id,
                "observed_count": 51 * len(steps), "missing_count": 0,
                "inventory_status": source.status, "inventory_observed_count": source.observed_count,
                "manifest_path": str(manifest), "required_steps": steps, "reason": source.reason}
        # Queue expiry limits new transport, not readback of already possessed
        # original evidence. A nonempty or old unproved cache never earns this.
        try:
            admitted()
        except (ValueError, requests.Timeout) as exc:
            return {**report, "observed_count": len(retained), "reason": str(exc)}
        from ecmwf.opendata import Client
        cache.mkdir(parents=True, exist_ok=True)
        mirrors = tuple(dict.fromkeys(_DOWNLOAD_SOURCES))
        if not mirrors or any(mirror not in {"aws", "google"} for mirror in mirrors):
            raise ValueError("NATIVE_2T_MIRROR_POOL_UNSUPPORTED")
        cursor = _native_mirror_attempt(cache, paths.raw_root, run_utc, mirrors)
        if cursor.get("configured_mirrors") == list(mirrors):
            offset = mirrors.index(cursor["last_attempted_mirror"])
            if cursor["status"] != "COMPLETE":
                offset = (offset + 1) % len(mirrors)
            mirrors = mirrors[offset:] + mirrors[:offset]
        configured = tuple(dict.fromkeys(_DOWNLOAD_SOURCES))
        session = _NativeDeadlineSession()
        session._zeus_deadline = cycle_deadline_monotonic
        failure = None
        for mirror in mirrors:
            admitted()
            attempt = {"last_attempted_mirror": mirror, "status": "RUNNING",
                "attempt_started_at": datetime.now(timezone.utc).isoformat(), "attempt_finished_at": None, "reason": None}
            _native_mirror_attempt(cache, paths.raw_root, run_utc, configured, attempt)
            try:
                # Even an interrupted stage is a durable failed turn, not a
                # permanent first-mirror gate. Preserve it; the next normal
                # poll can try another configured replica under a fresh cut.
                stage = cache / f".mirror-{_resume_source_namespace(mirror)}.partial"
                if stage.is_symlink():
                    raise ValueError("NATIVE_2T_MIRROR_STAGE_ALIAS")
                stage.mkdir(exist_ok=True)
                prefix = dict(retained)
                staged = {}
                for path in sorted(stage.glob("step*-member*.grib2")):
                    if path.is_symlink() or path.with_suffix(".grib2.proof.json").is_symlink():
                        raise ValueError("NATIVE_2T_MIRROR_STAGE_ALIAS")
                    record, _ = _read_native_temperature_record(path, run_utc)
                    if json.loads(path.with_suffix(".grib2.proof.json").read_bytes()).get("ingest_mode") != "SCHEDULED_LIVE":
                        raise ValueError("NATIVE_2T_ORIGIN_ROLE_CHANGED")
                    expected_origin = ("https://storage.googleapis.com/ecmwf-open-data/" if mirror == "google"
                        else "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/")
                    if not record["source_url"].startswith(expected_origin):
                        raise ValueError("NATIVE_2T_MIRROR_STAGE_ORIGIN_INVALID")
                    staged[(record["member"], record["step_hours"])] = record
                # Same-endpoint originals require no re-download. A different
                # replica must reproduce every possessed field, not merely its grid.
                for key, record in prefix.items():
                    own_source = ("google" if record["source_url"].startswith("https://storage.googleapis.com/ecmwf-open-data/") else
                        "aws" if record["source_url"].startswith("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/") else None)
                    if own_source == mirror:
                        staged[key] = record

                def promote_verified():
                    for key in prefix.keys() & staged.keys():
                        if staged[key]["raw_message_sha256"] != prefix[key]["raw_message_sha256"]:
                            raise ValueError("NATIVE_2T_MIRROR_COHORT_DIVERGED")
                    if not prefix.keys() <= staged.keys():
                        return
                    for key, record in sorted(staged.items()):
                        if key not in retained and key[1] in steps:
                            if retained and record["grid_sha256"] != next(iter(retained.values()))["grid_sha256"]:
                                raise ValueError("NATIVE_2T_RETAINED_GRID_CHANGED")
                            retained[key] = _promote_native_mirror_part(Path(record["path"]), cache, run_utc)

                promote_verified()
                capture_steps = sorted(set(steps) | {key[1] for key in prefix})
                for step in capture_steps:
                    for control in (True, False):
                        members = [0] if control else list(range(1, 51))
                        if all((member, step) in staged for member in members):
                            continue
                        admitted()
                        client = Client(source=mirror)
                        client.session = session
                        result = client._get_urls(target=str(stage / "unused"), use_index=False,
                            date=int(run_utc.strftime("%Y%m%d")), time=run_utc.hour,
                            stream="oper" if control else "enfo", type=["fc" if control else "pf"],
                            step=[step], param=["2t"])
                        indexes = []
                        _resolve_index_parts(client, result, deadline=cycle_deadline_monotonic,
                            original_indexes=indexes, native_complete_body=True)
                        if len(indexes) != 1:
                            raise ValueError("NATIVE_2T_INDEX_ENTITY_AMBIGUOUS")
                        index = indexes[0]
                        body = index["body"]
                        parts = _native_temperature_index_parts(body, run_utc, step, control=control)
                        digest = hashlib.sha256(body).hexdigest()
                        entity = hashlib.sha256(index["source_url"].encode() + body).hexdigest()
                        index_path = stage / f"index-{entity}.body"
                        if index_path.exists():
                            if index_path.is_symlink() or index_path.read_bytes() != body:
                                raise ValueError("NATIVE_2T_INDEX_ENTITY_CHANGED")
                        else:
                            with index_path.open("xb") as handle:
                                handle.write(body)
                            with index_path.with_suffix(".http.json").open("x") as handle:
                                json.dump({k: v for k, v in index.items() if k != "body"}, handle, sort_keys=True)
                        receipt_sha = hashlib.sha256(index_path.with_suffix(".http.json").read_bytes()).hexdigest()
                        for part in parts:
                            key = part["member"], step
                            if key in staged:
                                continue
                            admitted()
                            offset, length = part["source_index_offset"], part["source_index_length"]
                            response = session.get(index["source_url"], stream=True,
                                headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                                timeout=_remaining_step_timeout(cycle_deadline_monotonic),
                                verify=getattr(client, "verify", True))
                            try:
                                _validate_range_response(response, offset=offset, length=length)
                                _remaining_step_timeout(cycle_deadline_monotonic)
                                if getattr(response, "_native_body_complete", False) is not True:
                                    raise ValueError("NATIVE_2T_HTTP_BODY_NOT_BOUNDED")
                                raw = response.content
                                if len(raw) != length:
                                    raise ValueError("NATIVE_2T_RANGE_TRUNCATED")
                                proof = {**part, "step_hours": step, "source_url": index["source_url"],
                                    "source_index_url": index["source_index_url"], "index_path": index_path.name,
                                    "source_index_sha256": digest, "raw_message_sha256": hashlib.sha256(raw).hexdigest(),
                                    "index_receipt_sha256": receipt_sha,
                                    "source_fetched_at": datetime.now(timezone.utc).isoformat(), "ingest_mode": "SCHEDULED_LIVE",
                                    "source_issued_at": None, "qualification_status": "UNKNOWN",
                                    "range_http": {"status": response.status_code, "headers": dict(response.headers)}}
                                path = stage / f"step{step:03d}-member{part['member']:02d}.grib2"
                                if _path_present(path) or _path_present(path.with_suffix(".grib2.proof.json")):
                                    raise ValueError("NATIVE_2T_UNBOUND_EXISTING_BYTES")
                                with path.open("xb") as handle:
                                    handle.write(raw)
                                with path.with_suffix(".grib2.proof.json").open("x") as handle:
                                    json.dump(proof, handle, sort_keys=True)
                                record, _ = _read_native_temperature_record(path, run_utc)
                                staged[key] = record
                                promote_verified()
                            finally:
                                response.close()
                failure = None
                _native_mirror_attempt(cache, paths.raw_root, run_utc, configured,
                    {**attempt, "status": "COMPLETE", "attempt_finished_at": datetime.now(timezone.utc).isoformat()})
                break
            except requests.RequestException as exc:
                failure = str(exc) or type(exc).__name__
                _native_mirror_attempt(cache, paths.raw_root, run_utc, configured,
                    {**attempt, "status": "FAILED", "reason": failure,
                     "attempt_finished_at": datetime.now(timezone.utc).isoformat()})
                if time.monotonic() >= cycle_deadline_monotonic:
                    break
            except (OSError, ValueError) as exc:
                _native_mirror_attempt(cache, paths.raw_root, run_utc, configured,
                    {**attempt, "status": "FAILED", "reason": str(exc),
                     "attempt_finished_at": datetime.now(timezone.utc).isoformat()})
                raise
        source = persist_native_temperature_source_run(conn, cache_dir=cache, manifest_path=manifest,
            expected_run_utc=run_utc, product_steps=plan, ingest_mode="SCHEDULED_LIVE")
        missing = {(member, step) for step in steps for member in range(51)} - retained.keys()
        return {**report, "status": ("INCOMPLETE" if missing else "AVAILABLE") if source.source_run_id else "DEFERRED",
            "source_run_id": source.source_run_id, "observed_count": 51 * len(steps) - len(missing),
            "missing_count": len(missing), "manifest_path": str(manifest),
            "inventory_status": source.status, "inventory_observed_count": source.observed_count,
            "required_steps": steps, "reason": failure or source.reason}
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        return {**report, "status": "UNKNOWN", "reason": str(exc) or type(exc).__name__}
    finally:
        try:
            if session is not None:
                session.close()
        finally:
            _native_temperature_source_lock.release()


def _capture_native_temperature_bytes(run: datetime, steps: list[int], output_dir: Path,
    *, byte_budget: int, deadline: float, _session: Any = None, _request_gate: Any = None) -> dict:
    """One ephemeral transport owner; immutable packet artifacts, no DB access."""
    session = _session or requests.Session()
    gate = _request_gate or _TokenBucket(min(2., _DOWNLOAD_RPS), capacity=1.).acquire
    transferred = metadata_written = 0
    started = datetime.now(timezone.utc).isoformat()
    report: dict[str, Any] = {"capture_status": "UNKNOWN", "qualification_status": "OFFLINE_ONLY",
        "source_run_utc": run.isoformat(), "requested_steps": steps, "observed_steps": [],
        "first_source_publication_at": None, "fetch_started_at": started, "messages": [], "indexes": [],
        "surface_geopotential_status": "UNKNOWN", "static_model_validity_status": "NOT_EVALUATED",
        "station_ground_status": "UNPROVEN", "sensor_agl_status": "UNPROVEN"}
    reserve = min(4 * 1024 * 1024, byte_budget // 8)

    def checkpoint():
        if time.monotonic() >= deadline:
            raise requests.Timeout("NATIVE_2T_DEADLINE_EXCEEDED")

    def get(url, *, offset=None, length=None):
        nonlocal transferred
        checkpoint()
        gate(deadline=deadline)
        remaining = deadline - time.monotonic()
        checkpoint()
        limit = min(2 * 1024 * 1024 if length is None else length, byte_budget - reserve - transferred)
        if limit <= 0 or (length is not None and length > limit):
            raise ValueError("NATIVE_2T_BYTE_BUDGET_EXCEEDED")
        headers = {} if offset is None else {"Range": f"bytes={offset}-{offset + length - 1}"}
        response = session.get(url, headers=headers, stream=True, timeout=min(10., remaining), allow_redirects=False)
        try:
            if offset is None:
                if response.status_code != 200:
                    raise ValueError(f"NATIVE_2T_INDEX_HTTP_{response.status_code}")
            else:
                _validate_range_response(response, offset=offset, length=length)
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                transferred += len(chunk)
                size += len(chunk)
                checkpoint()
                if size > limit or transferred > byte_budget - reserve:
                    raise ValueError("NATIVE_2T_BYTE_BUDGET_EXCEEDED")
                chunks.append(chunk)
            body = b"".join(chunks)
            if length is not None and len(body) != length:
                raise ValueError("NATIVE_2T_RANGE_TRUNCATED")
            checkpoint()
            http = {"status": response.status_code, "headers": {str(k): str(v) for k, v in response.headers.items()}}
            if len(json.dumps(http)) > 16384:
                raise ValueError("NATIVE_2T_HTTP_METADATA_OVERSIZED")
            return body, http, datetime.now(timezone.utc).isoformat()
        finally:
            response.close()

    def original(name, body):
        checkpoint()
        with (output_dir / name).open("xb") as handle:
            handle.write(body)
        return name

    try:
        plans = []
        for step in steps:
            for control in (True, False):
                stream, kind = ("oper", "fc") if control else ("enfo", "ef")
                stem = f"{run:%Y%m%d%H}0000-{step}h-{stream}-{kind}"
                url = (f"https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{run:%Y%m%d}/"
                       f"{run:%H}z/ifs/0p25/{stream}/{stem}.grib2")
                index_url = url[:-6] + ".index"
                index, index_http, fetched = get(index_url)
                index_path = original(stem + ".index", index)
                parts = _native_temperature_index_parts(index, run, step, control=control)
                for part in parts:
                    part["param"] = "2t"
                if step == 0 and control:
                    mask_rows = [(line, json.loads(line)) for line in index.splitlines()
                                 if json.loads(line).get("param") == "lsm"]
                    if len(mask_rows) == 1:
                        line, row = mask_rows[0]
                        if (all(str(row.get(k)) == v for k, v in {"class": "od", "levtype": "sfc",
                            "type": "fc", "stream": "oper", "step": "0", "date": run.strftime("%Y%m%d"),
                            "time": run.strftime("%H%M")}.items())
                            and type(row.get("_offset")) is int and row["_offset"] >= 0
                            and type(row.get("_length")) is int and 100 <= row["_length"] <= 1024 * 1024):
                            parts.append({"member": 0, "param": "lsm", "source_index_offset": row["_offset"],
                                "source_index_length": row["_length"], "index_record": row,
                                "source_index_line_sha256": hashlib.sha256(line).hexdigest()})
                    report["surface_geopotential_index_records"] = [json.loads(line) for line in index.splitlines()
                        if json.loads(line).get("param") in ("z", "gh")]
                    parts.sort(key=lambda p: p["source_index_offset"])
                plans.append((step, url, index_url, index_path, hashlib.sha256(index).hexdigest(), index_http, parts))
                report["indexes"].append({"path": index_path, "source_url": index_url,
                    "sha256": hashlib.sha256(index).hexdigest(), "body_bytes": len(index),
                    "source_fetched_at": fetched, "http": index_http})
        planned = sum(p["source_index_length"] for plan in plans for p in plan[-1])
        report["planned_grib_bytes"] = planned
        if transferred + planned + reserve > byte_budget:
            raise ValueError("NATIVE_2T_PLANNED_BYTE_BUDGET_EXCEEDED")
        # Coalesce only contiguous selected original messages, never gap bytes.
        # Single requests and at most 32MiB per response keep memory/connection costs bounded.
        original_grid = None
        for step, url, index_url, index_path, index_hash, index_http, parts in plans:
            groups: list[list[dict]] = []
            for part in parts:
                if (groups and groups[-1][-1]["source_index_offset"] + groups[-1][-1]["source_index_length"]
                        == part["source_index_offset"]
                        and sum(p["source_index_length"] for p in groups[-1]) + part["source_index_length"] <= 32 * 1024 * 1024):
                    groups[-1].append(part)
                else:
                    groups.append([part])
            for group in groups:
                offset = group[0]["source_index_offset"]
                length = sum(p["source_index_length"] for p in group)
                body, range_http, fetched = get(url, offset=offset, length=length)
                cursor = 0
                for part in group:
                    raw = body[cursor:cursor + part["source_index_length"]]
                    cursor += len(raw)
                    if (raw[:4] != b"GRIB" or raw[-4:] != b"7777" or len(raw) < 20
                            or raw[7] != 2 or int.from_bytes(raw[8:16], "big") != len(raw)):
                        raise ValueError("NATIVE_2T_ORIGINAL_GRIB_FRAMING_INVALID")
                    name = ("lsm.grib2" if part["param"] == "lsm"
                            else f"step{step:03d}-member{part['member']:02d}.grib2")
                    original(name, raw)
                    report["last_received_original"] = {"path": name, "source_url": url,
                        "source_index_offset": part["source_index_offset"], "source_index_length": len(raw),
                        "raw_message_sha256": hashlib.sha256(raw).hexdigest()}
                    transport_proof = {**part, "step_hours": step, "source_url": url,
                        "source_index_url": index_url, "index_path": index_path,
                        "source_index_sha256": index_hash, "raw_message_sha256": hashlib.sha256(raw).hexdigest(),
                        "source_fetched_at": fetched, "range_http": range_http,
                        "first_source_publication_at": None, "qualification_status": "OFFLINE_ONLY"}
                    proof_bytes = json.dumps(transport_proof, sort_keys=True).encode()
                    if metadata_written + len(proof_bytes) > reserve:
                        raise ValueError("NATIVE_2T_METADATA_BYTE_BUDGET_EXCEEDED")
                    original(name + ".proof.json", proof_bytes)
                    metadata_written += len(proof_bytes)
                    import base64
                    import eccodes as ec
                    from scripts.extract_open_ens_localday import _native_message_capture, _open_ens_original_grid, _GRID_KEYS
                    gid = ec.codes_new_from_message(raw)
                    try:
                        capture = _native_message_capture(gid, instantaneous=True)
                        if capture["capture_status"] != "OBSERVED":
                            raise ValueError("NATIVE_2T_ORIGINAL_METADATA_UNAVAILABLE")
                        h = capture["observed_headers"]
                        sections = {s["section_number"]: base64.b64decode(s["bytes_base64"], validate=True)
                                    for s in capture["metadata_sections"]}
                        s1, s4 = sections[1], sections[4]
                        expected_type = "fc" if part["member"] == 0 else "pf"
                        if ((h["dataDate"], h["dataTime"], h["endStep"], h["centre"], h["dataType"])
                                != (int(run.strftime("%Y%m%d")), run.hour * 100, step, "ecmf", expected_type)):
                            raise ValueError("NATIVE_2T_ORIGINAL_RUN_MEMBER_STEP_MISMATCH")
                        valid = run + timedelta(hours=step)
                        if (h["stepUnits"] != 1 or h["startStep"] != step or h["endStep"] != step
                            or (h["validityDate"], h["validityTime"]) != (int(valid.strftime("%Y%m%d")), valid.hour * 100)
                            or int.from_bytes(s1[5:7], "big") != 98
                            or int.from_bytes(s1[12:14], "big") != run.year
                            or tuple(s1[14:19]) != (run.month, run.day, run.hour, 0, 0)
                            or s4[17] != 1 or int.from_bytes(s4[18:22], "big") != step
                            or s4[13] != h["generatingProcessIdentifier"]
                            or _open_ens_original_grid(sections[3]) != {k: h[k] for k in _GRID_KEYS}):
                            raise ValueError("NATIVE_2T_ORIGINAL_SECTIONS_HEADER_MISMATCH")
                        if original_grid is None:
                            original_grid = sections[3]
                        elif original_grid != sections[3]:
                            raise ValueError("NATIVE_2T_ORIGINAL_GRID_MISMATCH")
                        if part["param"] == "2t" and (
                            h["paramId"] != 167 or h["units"] != "K" or h["typeOfLevel"] != "heightAboveGround"
                            or h["level"] != 2 or h["stepType"] != "instant" or h["generatingProcessIdentifier"] != 161
                            or (part["member"] != 0 and h.get("number") != part["member"])):
                            raise ValueError("NATIVE_2T_ORIGINAL_PARAMETER_MEMBER_INVALID")
                        if part["param"] == "2t" and (
                            raw[6] != 0 or s4[9:11] != b"\x00\x00" or s4[22] != 103 or s4[23] != 0
                            or int.from_bytes(s4[24:28], "big") != 2
                            or int.from_bytes(s4[7:9], "big") != (0 if part["member"] == 0 else 1)
                            or s1[20] != (1 if part["member"] == 0 else 4)
                            or (part["member"] != 0 and s4[35] != part["member"])):
                            raise ValueError("NATIVE_2T_ORIGINAL_PARAMETER_MEMBER_INVALID")
                        if part["param"] == "lsm" and (h["paramId"] != 172 or h["typeOfLevel"] != "surface"):
                            raise ValueError("NATIVE_2T_ORIGINAL_LSM_INVALID")
                    finally:
                        ec.codes_release(gid)
                    captured = {**part, "step_hours": step, "path": name, "native_capture": capture,
                        "source_url": url, "source_index_url": index_url, "index_path": index_path,
                        "source_index_sha256": index_hash, "raw_message_sha256": hashlib.sha256(raw).hexdigest(),
                        "source_fetched_at": fetched, "range_http": range_http}
                    if part["param"] == "lsm":
                        report["land_mask"] = captured
                    else:
                        report["messages"].append(captured)
        report["observed_steps"] = [s for s in steps if sorted(m["member"] for m in report["messages"]
            if m["step_hours"] == s) == list(range(51))]
        report["capture_status"] = "OBSERVED" if report["observed_steps"] == steps else "UNKNOWN"
    except Exception as exc:
        report["unavailable_reason"] = str(exc)[:300] or type(exc).__name__
    finally:
        session.close()
        report["observed_steps"] = [s for s in steps if sorted(m["member"] for m in report["messages"]
            if m["step_hours"] == s) == list(range(51))]
        report.update(transferred_body_bytes=transferred, written_proof_bytes=metadata_written,
                      fetch_finished_at=datetime.now(timezone.utc).isoformat())
    return report


def _native_temperature_capture_worker(run_iso, steps, output_dir, byte_budget, deadline, writer):
    """Spawn-only packet transport; parent owns completion and absolute wall bound."""
    try:
        os.nice(10)
        bucket = _TokenBucket(min(2., _DOWNLOAD_RPS), capacity=1.)
        def request_permission(*, deadline):
            bucket.acquire(deadline=deadline)
            writer.send_bytes(b'{"request_intent":true}')
            remaining = max(0., deadline - time.monotonic())
            if not writer.poll(remaining) or writer.recv_bytes(maxlength=16) != b"OK":
                raise requests.Timeout("NATIVE_2T_PRIORITY_PERMISSION_UNAVAILABLE")
            if time.monotonic() >= deadline:
                raise requests.Timeout("NATIVE_2T_DEADLINE_EXCEEDED")
        report = _capture_native_temperature_bytes(datetime.fromisoformat(run_iso), steps, Path(output_dir),
            byte_budget=byte_budget, deadline=deadline, _request_gate=request_permission)
        packet = json.dumps(report).encode()
        if len(packet) <= 4 * 1024 * 1024:
            if report["transferred_body_bytes"] + report["written_proof_bytes"] + len(packet) > byte_budget:
                report["capture_status"] = "UNKNOWN"
                report["unavailable_reason"] = "NATIVE_2T_RECEIPT_BYTE_BUDGET_EXCEEDED"
                packet = json.dumps(report).encode()
            writer.send_bytes(packet)
    finally:
        writer.close()


def _native_temperature_priority_probe(check, deadline: float) -> bool:
    """Only an affirmative idle result from an ephemeral, killable read-only probe."""
    context = multiprocessing.get_context("fork")
    reader, writer = context.Pipe(duplex=False)
    def inspect():
        try:
            writer.send_bytes(b"FALSE" if check() is False else b"BUSY")
        except Exception:
            writer.send_bytes(b"UNKNOWN")
        finally:
            writer.close()
    process = context.Process(target=inspect)
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.Timeout("NATIVE_2T_DEADLINE_EXCEEDED")
        probe_deadline = time.monotonic() + min(.5, remaining / 2)
        process.start()
        writer.close()
        if not reader.poll(max(0., probe_deadline - time.monotonic())):
            raise requests.Timeout("NATIVE_2T_PRIORITY_CHECK_TIMEOUT")
        answer = reader.recv_bytes(maxlength=8)
        if time.monotonic() >= deadline:
            raise requests.Timeout("NATIVE_2T_DEADLINE_EXCEEDED")
        return answer == b"FALSE"
    finally:
        reader.close()
        writer.close()
        if process.pid is not None:
            if process.is_alive():
                process.kill()
            process.join(timeout=max(0., deadline - time.monotonic()))
            if process.is_alive():
                raise RuntimeError("NATIVE_2T_PRIORITY_PROBE_REAP_UNCONFIRMED")
        process.close()


def capture_open_ens_temperature_run(*, run_utc: datetime, steps: list[int], output_dir: Path,
    normal_collector_busy: Any, byte_budget: int = 512 * 1024 * 1024,
    timeout_seconds: float = 300., _worker: Any = None) -> dict:
    """Explicit offline preparation only: global run capture, no scheduled/live hook.

    SCOPE: this run's requested native 2t members. DRAIN: a later explicitly
    authorized packet attempt after normal collection/budget/source gaps clear.
    RESET: each attempt uses fresh original indexes in an empty packet directory.
    A required current busy check yields to normal collectors before HTTP and
    throughout the ephemeral worker. Neither the worker nor this parent touches DBs.
    """
    started = time.monotonic()
    unavailable = {"capture_status": "UNKNOWN", "qualification_status": "OFFLINE_ONLY"}
    process = reader = writer = None
    try:
        if (not callable(normal_collector_busy) or type(byte_budget) is not int
                or not 0 < byte_budget <= 512 * 1024 * 1024
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300.):
            raise ValueError("NATIVE_2T_BUDGET_OR_PRIORITY_INVALID")
        deadline = started + max(0., timeout_seconds - min(.5, timeout_seconds / 4))
        if (run_utc.tzinfo is None or run_utc.utcoffset() != timedelta(0) or run_utc.minute or run_utc.second
                or run_utc.microsecond or run_utc.hour not in (0, 6, 12, 18)
                or run_utc > datetime.now(timezone.utc)):
            raise ValueError("NATIVE_2T_RUN_INVALID")
        horizon = 240 if run_utc.hour in (0, 12) else 90
        if (not steps or len(set(steps)) != len(steps)
                or any(type(s) is not int or s < 0 or s > horizon or (s % 3 if s <= 144 else s % 6) for s in steps)):
            raise ValueError("NATIVE_2T_NATIVE_STEP_SET_INVALID")
        if not _native_temperature_priority_probe(normal_collector_busy, deadline):
            raise ValueError("NATIVE_2T_NORMAL_COLLECTOR_PRIORITY")
        output_dir = Path(output_dir)
        if (not output_dir.is_absolute() or "state" in output_dir.resolve().parts
                or output_dir.is_symlink()
                or (output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())))):
            raise ValueError("NATIVE_2T_PACKET_OUTPUT_NOT_EMPTY")
        output_dir.mkdir(parents=True, exist_ok=True)
        # Reserve bounded reap time within the operator's total wall allowance.
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=True)
        process = context.Process(target=_worker or _native_temperature_capture_worker,
            args=(run_utc.isoformat(), sorted(steps), str(output_dir), byte_budget, deadline, writer))
        process.start()
        writer.close()
        os.set_blocking(reader.fileno(), False)
        received, expected = bytearray(), None
        while time.monotonic() < deadline:
            if not _native_temperature_priority_probe(normal_collector_busy, deadline):
                raise ValueError("NATIVE_2T_NORMAL_COLLECTOR_PRIORITY")
            if reader.poll(min(.1, max(0., deadline - time.monotonic()))):
                try:
                    chunk = os.read(reader.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise ValueError("NATIVE_2T_WORKER_INCOMPLETE")
                received.extend(chunk)
                if expected is None and len(received) >= 4:
                    expected = struct.unpack("!i", received[:4])[0]
                    if not 0 < expected <= 4 * 1024 * 1024:
                        raise ValueError("NATIVE_2T_IPC_BOUNDS_INVALID")
                if len(received) > 4 * 1024 * 1024 + 4 or (expected is not None and len(received) > expected + 4):
                    raise ValueError("NATIVE_2T_IPC_BOUNDS_INVALID")
                if expected is None or len(received) != expected + 4:
                    continue
                report = json.loads(received[4:])
                if time.monotonic() >= deadline:
                    raise requests.Timeout("NATIVE_2T_DEADLINE_EXCEEDED")
                if report == {"request_intent": True}:
                    if not _native_temperature_priority_probe(normal_collector_busy, deadline):
                        raise ValueError("NATIVE_2T_NORMAL_COLLECTOR_PRIORITY")
                    reader.send_bytes(b"OK")
                    received, expected = bytearray(), None
                    continue
                if report.get("transferred_body_bytes", byte_budget + 1) > byte_budget:
                    raise ValueError("NATIVE_2T_BYTE_BUDGET_EXCEEDED")
                receipt_bytes = json.dumps(report, sort_keys=True).encode()
                if (type(report.get("written_proof_bytes", 0)) is not int
                        or report.get("written_proof_bytes", 0) < 0
                        or report.get("transferred_body_bytes", byte_budget + 1)
                           + report.get("written_proof_bytes", 0) + len(receipt_bytes) > byte_budget):
                    raise ValueError("NATIVE_2T_RECEIPT_BYTE_BUDGET_EXCEEDED")
                if not _native_temperature_priority_probe(normal_collector_busy, deadline):
                    raise ValueError("NATIVE_2T_NORMAL_COLLECTOR_PRIORITY")
                with (output_dir / "capture_receipt.json").open("xb") as handle:
                    handle.write(receipt_bytes)
                return {**report, "elapsed_seconds": time.monotonic() - started, "output_dir": str(output_dir)}
            if not process.is_alive():
                raise ValueError("NATIVE_2T_WORKER_INCOMPLETE")
        raise requests.Timeout("NATIVE_2T_DEADLINE_EXCEEDED")
    except Exception as exc:
        return {**unavailable, "unavailable_reason": str(exc)[:300] or type(exc).__name__,
                "elapsed_seconds": time.monotonic() - started, "output_dir": str(output_dir)}
    finally:
        if reader is not None:
            reader.close()
        if writer is not None:
            writer.close()
        if process is not None:
            if process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join(timeout=min(.5, max(0., started + timeout_seconds - time.monotonic())))
            process.close()


def _surface_audit_http(session: Any, url: str, *, deadline: float,
                        offset: int | None = None, length: int | None = None) -> tuple[bytes, dict]:
    """One bounded, non-retrying audit request, isolated from forecast transports."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
    headers = {} if offset is None else {"Range": f"bytes={offset}-{offset + length - 1}"}
    if offset is not None and (offset < 0 or length is None or not 100 <= length <= 1024 * 1024):
        raise ValueError("SURFACE_AUDIT_RANGE_BOUNDS_INVALID")
    response = session.get(url, headers=headers, stream=True,
                           timeout=min(10.0, remaining), allow_redirects=False)
    try:
        if offset is None:
            if response.status_code != 200:
                raise ValueError(f"SURFACE_AUDIT_INDEX_HTTP_{response.status_code}")
        else:
            _validate_range_response(response, offset=offset, length=length)
        limit = 1024 * 1024 if length is None else length
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            if time.monotonic() >= deadline:
                raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
            total += len(chunk)
            if total > limit:
                raise ValueError("SURFACE_AUDIT_RESPONSE_OVERSIZED")
            chunks.append(chunk)
        body = b"".join(chunks)
        if time.monotonic() >= deadline:
            raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
        if length is not None and (len(body) != length or not body.startswith(b"GRIB") or not body.endswith(b"7777")):
            raise ValueError("SURFACE_AUDIT_MESSAGE_INVALID")
        observed_headers = {str(k): str(v) for k, v in response.headers.items()}
        if len(json.dumps(observed_headers)) > 16384:
            raise ValueError("SURFACE_AUDIT_HTTP_HEADERS_OVERSIZED")
        return body, {"status": response.status_code, "headers": observed_headers}
    finally:
        response.close()


def _surface_z_index_entry(body: bytes, cycle: datetime) -> tuple[int, int]:
    matches = []
    for line in body.splitlines():
        item = json.loads(line)
        if all(str(item.get(k)) == v for k, v in {
            "param": "z", "levtype": "sfc", "step": "0", "type": "fc", "stream": "oper",
            "class": "od", "date": cycle.strftime("%Y%m%d"), "time": f"{cycle.hour:02d}00",
        }.items()):
            matches.append(item)
    if len(matches) != 1:
        raise ValueError("SURFACE_AUDIT_INDEX_NOT_SINGLE_Z")
    offset, length = matches[0]["_offset"], matches[0]["_length"]
    if type(offset) is not int or type(length) is not int or offset < 0 or not 100 <= length <= 1024 * 1024:
        raise ValueError("SURFACE_AUDIT_INDEX_BOUNDS_INVALID")
    return offset, length


def _surface_audit_worker(cycle_iso: str, url: str, index_url: str, deadline: float, writer: Any) -> None:
    """Spawn-only transport: no connection, cache path, decoder or publication."""
    session = requests.Session()
    index_bytes = body = b""
    try:
        index_bytes, index_http = _surface_audit_http(session, index_url, deadline=deadline)
        offset, length = _surface_z_index_entry(index_bytes, datetime.fromisoformat(cycle_iso))
        body, range_http = _surface_audit_http(session, url, deadline=deadline, offset=offset, length=length)
        metadata = {"status": "ok", "index_http": index_http, "range_http": range_http,
                    "source_index_offset": offset, "source_index_length": length,
                    "source_fetched_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        index_bytes = body = b""
        metadata = {"status": "UNKNOWN", "reason": str(exc)[:200] or type(exc).__name__}
    try:
        encoded = json.dumps(metadata).encode()
        if len(encoded) > 65536 or len(index_bytes) > 1024 * 1024 or len(body) > 1024 * 1024:
            return
        packet = struct.pack("!III", len(encoded), len(index_bytes), len(body)) + encoded + index_bytes + body
        view = memoryview(packet)
        while view:
            written = os.write(writer.fileno(), view[:65536])
            view = view[written:]
    finally:
        writer.close()
        session.close()


def _fetch_surface_audit_bytes(cycle: datetime, url: str, index_url: str, *, deadline: float,
                               _worker: Any = None) -> tuple[bytes, bytes, dict]:
    """Wall-bounded spawn + capped nonblocking IPC; only the parent may publish."""
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_worker or _surface_audit_worker,
                              args=(cycle.isoformat(), url, index_url, deadline, writer))
    started = False
    try:
        if time.monotonic() >= deadline:
            raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
        process.start()
        started = True
        writer.close()
        os.set_blocking(reader.fileno(), False)
        received = bytearray()
        expected = None
        with selectors.DefaultSelector() as selector:
            selector.register(reader.fileno(), selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
                if not selector.select(remaining):
                    raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
                try:
                    chunk = os.read(reader.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise ValueError("SURFACE_AUDIT_WORKER_INCOMPLETE_FRAME")
                received.extend(chunk)
                if expected is None and len(received) >= 12:
                    meta_len, index_len, body_len = struct.unpack("!III", received[:12])
                    if not 0 < meta_len <= 65536 or index_len > 1024 * 1024 or body_len > 1024 * 1024:
                        raise ValueError("SURFACE_AUDIT_IPC_BOUNDS_INVALID")
                    expected = 12 + meta_len + index_len + body_len
                if len(received) > 12 + 65536 + 2 * 1024 * 1024 or (expected is not None and len(received) > expected):
                    raise ValueError("SURFACE_AUDIT_IPC_BOUNDS_INVALID")
                if expected is not None and len(received) == expected:
                    if time.monotonic() >= deadline:
                        raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
                    metadata = json.loads(received[12:12 + meta_len])
                    if metadata.get("status") != "ok":
                        raise ValueError(str(metadata.get("reason") or "SURFACE_AUDIT_WORKER_FAILED"))
                    index_bytes = bytes(received[12 + meta_len:12 + meta_len + index_len])
                    body = bytes(received[12 + meta_len + index_len:])
                    return index_bytes, body, metadata
    finally:
        reader.close()
        writer.close()
        if started:
            if process.is_alive():
                process.kill()
            # Bounded cleanup grace is separate from the acceptance deadline.
            process.join(timeout=0.5)
            if process.is_alive():
                raise RuntimeError("SURFACE_AUDIT_WORKER_REAP_UNCONFIRMED")
        process.close()


def _surface_audit_snapshot_binding(row: Mapping[str, object]) -> str:
    fields = {key: row[key] for key in ("snapshot_id", "source_run_id", "temperature_metric",
                                       "source_cycle_time", "source_available_at", "surface_json")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_surface_source_lock = threading.Lock()


def _fetch_cycle_surface_evidence(*, cycle: datetime, mask_path: Path,
                                  deadline: float | None = None) -> dict[str, object]:
    """Optional source proof before extraction, without a future snapshot pin.

    SCOPE: exact cycle and distributed LSM grid/body. DRAIN: the next normal
    collection retries UNKNOWN once. RESET: actual matching z bytes and proof.
    The existing availability allowance and cycle remainder bound all work.
    """
    from scripts.extract_open_ens_localday import _read_land_mask, _read_surface_geopotential

    started = time.monotonic()
    deadline = min(started + float(OPENDATA_AVAILABILITY_PROBE_SECONDS),
                   deadline if deadline is not None else float("inf"))
    acquired = False

    def checkpoint():
        if time.monotonic() >= deadline:
            raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")

    def decode(path, proof_path, expected_grid):
        # Check declared allocation size before the native values decoder.
        import eccodes as ec
        checkpoint()
        gid = ec.codes_new_from_message(path.read_bytes())
        try:
            ni, nj = ec.codes_get(gid, "Ni"), ec.codes_get(gid, "Nj")
            if not 0 < ni * nj <= 2_000_000:
                raise ValueError("SURFACE_AUDIT_GRID_BOUNDS_INVALID")
        finally:
            ec.codes_release(gid)
        observed = _read_surface_geopotential(path, proof_path)
        checkpoint()
        if observed["fields"] != expected_grid:
            raise ValueError("SURFACE_AUDIT_Z_GRID_MISMATCH")
        return observed

    try:
        checkpoint()
        acquired = _surface_source_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        checkpoint()
        if not acquired:
            raise requests.Timeout("SURFACE_AUDIT_DEADLINE_EXCEEDED")
        mask_proof_path = mask_path.with_suffix(".proof.json")
        if (mask_path.is_symlink() or mask_proof_path.is_symlink()
                or not 100 <= mask_path.stat().st_size <= 1024 * 1024
                or mask_proof_path.stat().st_size > 65536):
            raise ValueError("SURFACE_AUDIT_MASK_BOUNDS_INVALID")
        mask_bytes, mask_proof_bytes = mask_path.read_bytes(), mask_proof_path.read_bytes()
        mask = _read_land_mask(mask_path, mask_proof_path)
        checkpoint()
        if mask["proof"]["source_cycle_time"] != cycle.isoformat():
            raise ValueError("SURFACE_AUDIT_MASK_CYCLE_INVALID")
        dest = mask_path.with_suffix(".z.grib2")
        url = mask["proof"]["source_url"]
        index_url = mask["proof"]["source_index_url"]
        cached = None
        # Reuse the physical source proof without assigning its original
        # snapshot pin to a future snapshot or to another track.
        audit_url = (f"https://data.ecmwf.int/forecasts/{cycle:%Y%m%d}/{cycle.hour:02d}z/ifs/0p25/oper/"
                     f"{cycle:%Y%m%d}{cycle.hour:02d}0000-0h-oper-fc.grib2")
        own_track = next(track for track in TRACKS if mask_path.name.startswith(f".{track}_"))
        for track in (own_track, *(track for track in TRACKS if track != own_track)):
            path = mask_path.with_name(f".{track}_{cycle:%Y%m%d}_{cycle.hour:02d}z_lsm.z.grib2")
            proof_path, index_path = path.with_suffix(".proof.json"), path.with_suffix(".index.body")
            if not proof_path.exists():
                continue
            try:
                if (any(p.is_symlink() for p in (path, proof_path, index_path))
                        or proof_path.stat().st_size > 65536 or index_path.stat().st_size > 1024 * 1024
                        or not 100 <= path.stat().st_size <= 1024 * 1024):
                    raise ValueError("SURFACE_AUDIT_CACHE_BOUNDS_INVALID")
                body, index_bytes, proof_bytes = path.read_bytes(), index_path.read_bytes(), proof_path.read_bytes()
                proof = json.loads(proof_bytes)
                forward = proof.get("source_evidence_role") == "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN"
                pinned = (proof.get("source_evidence_role") is None and type(proof.get("snapshot_id")) is int
                          and proof["snapshot_id"] > 0 and isinstance(proof.get("source_run_id"), str)
                          and track in proof["source_run_id"]
                          and re.fullmatch(r"[0-9a-f]{64}", str(proof.get("snapshot_binding_sha256"))) is not None)
                if (not (forward or pinned)
                        or (forward and any(key in proof for key in ("snapshot_id", "source_run_id", "snapshot_binding_sha256")))
                        or proof.get("mask_sha256") != hashlib.sha256(mask_bytes).hexdigest()
                        or proof.get("mask_grid_identity_hash") != mask["grid_identity_hash"]
                        or proof.get("source_cycle_time") != cycle.isoformat()
                        or proof.get("source_url") not in (url, audit_url)
                        or proof.get("source_index_url") != str(proof.get("source_url")).rsplit(".", 1)[0] + ".index"
                        or proof.get("source_issued_at") is not None
                        or proof.get("source_index_sha256") != hashlib.sha256(index_bytes).hexdigest()
                        or _surface_z_index_entry(index_bytes, cycle) != (proof.get("source_index_offset"), proof.get("source_index_length"))):
                    raise ValueError("SURFACE_AUDIT_SOURCE_CACHE_BINDING_INVALID")
                observed = decode(path, proof_path, mask["fields"])
                fetched, written = (datetime.fromisoformat(str(proof[key])) for key in ("source_fetched_at", "source_written_at"))
                if (fetched.tzinfo is None or written.tzinfo is None
                        or not cycle <= fetched <= written <= datetime.now(timezone.utc)):
                    raise ValueError("SURFACE_AUDIT_SOURCE_CACHE_CLOCK_INVALID")
                if pinned:
                    point = proof["selected_point"]
                    phi = float(observed["values"][point["flat_index"]])
                    if (proof.get("temperature_grid_identity_hash") != mask["grid_identity_hash"]
                            or proof.get("raw_phi_m2_s2") != phi or not math.isfinite(phi)):
                        raise ValueError("SURFACE_AUDIT_SOURCE_CACHE_POINT_INVALID")
                if (path.read_bytes(), index_path.read_bytes(), proof_path.read_bytes()) != (body, index_bytes, proof_bytes):
                    raise ValueError("SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED")
                cached = body, index_bytes, proof, (path, proof_bytes)
                break
            except Exception:
                if path == dest:
                    raise  # Never replace or relax a prior committed generation.
        with tempfile.TemporaryDirectory(prefix=".surface_audit_", dir=mask_path.parent) as temp:
            staging = Path(temp)
            if cached is None:
                checkpoint()
                # The LSM parser does not retain its original index response.
                # Obtain honest independent index bytes, never rebuild lines.
                index_bytes, body, metadata = _fetch_surface_audit_bytes(cycle, url, index_url, deadline=deadline)
                checkpoint()
                offset, length = _surface_z_index_entry(index_bytes, cycle)
                if len(body) != length:
                    raise ValueError("SURFACE_AUDIT_IPC_MESSAGE_LENGTH_INVALID")
                proof = {"source": "ecmwf_open_data_ifs_oper_fc_step0_z", "source_url": url,
                    "source_index_url": index_url, "source_cycle_time": cycle.isoformat(),
                    "source_index_offset": offset, "source_index_length": length,
                    "source_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
                    "raw_message_sha256": hashlib.sha256(body).hexdigest(),
                    "source_fetched_at": metadata["source_fetched_at"],
                    "source_fetched_at_role": "PUBLIC_HTTP_BODY_POSSESSION", "source_issued_at": None,
                    "index_http": metadata["index_http"], "range_http": metadata["range_http"],
                    "audit_scope": "AUDIT_ONLY_NOT_DECISION_INPUT",
                    "quantity_role": "model_surface_geopotential_on_wire_distribution_grid",
                    "source_evidence_role": "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN",
                    "mask_sha256": hashlib.sha256(mask_bytes).hexdigest(),
                    "mask_grid_identity_hash": mask["grid_identity_hash"]}
            else:
                body, index_bytes, proof, _fence = cached
                if not dest.with_suffix(".proof.json").exists():
                    proof = {key: value for key, value in proof.items() if key not in (
                        "snapshot_id", "source_run_id", "snapshot_binding_sha256", "selected_point", "raw_phi_m2_s2")}
                    proof["source_evidence_role"] = "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN"
            staged_body, staged_index, staged_proof = staging / "z.grib2", staging / "z.index", staging / "z.proof.json"
            for path, content in ((staged_body, body), (staged_index, index_bytes)):
                with path.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                checkpoint()
            staged_proof.write_text(json.dumps(proof), encoding="utf-8")
            decode(staged_body, staged_proof, mask["fields"])
            if mask_path.read_bytes() != mask_bytes or mask_proof_path.read_bytes() != mask_proof_bytes:
                raise ValueError("SURFACE_AUDIT_MASK_GENERATION_CHANGED")
            if cached is not None:
                cache_path, cache_proof_bytes = cached[3]
                if (cache_path.read_bytes(), cache_path.with_suffix(".index.body").read_bytes(),
                        cache_path.with_suffix(".proof.json").read_bytes()) != (body, index_bytes, cache_proof_bytes):
                    raise ValueError("SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED")
            for source, target in ((staged_body, dest), (staged_index, dest.with_suffix(".index.body"))):
                checkpoint()
                try:
                    os.link(source, target)
                except FileExistsError:
                    if target.is_symlink() or target.read_bytes() != source.read_bytes():
                        raise ValueError("SURFACE_AUDIT_CACHE_PUBLISH_CONFLICT")
            proof_dest = dest.with_suffix(".proof.json")
            if proof_dest.exists():
                if cached is None or cached[3][0] != dest or proof_dest.read_bytes() != cached[3][1]:
                    raise ValueError("SURFACE_AUDIT_CACHE_PROOF_CONFLICT")
            else:
                proof = {**proof, "source_written_at": datetime.now(timezone.utc).isoformat(),
                         "source_written_at_role": "LOCAL_AUDIT_DURABLE_PUBLICATION"}
                with staged_proof.open("w") as handle:
                    json.dump(proof, handle, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                checkpoint()
                os.link(staged_proof, proof_dest)  # The proof is the final commit marker.
                directory_fd = os.open(dest.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                if time.monotonic() >= deadline and os.path.samestat(proof_dest.stat(), staged_proof.stat()):
                    proof_dest.unlink()
                checkpoint()
            return {"capture_status": "OBSERVED", "cache_reused": cached is not None}
    except Exception as exc:  # Optional evidence failure cannot stop temperature extraction.
        return {"capture_status": "UNKNOWN", "unavailable_reason": str(exc)[:200] or type(exc).__name__}
    finally:
        if acquired:
            _surface_source_lock.release()


def capture_open_ens_surface_audit(*, deadline_monotonic: float | None = None) -> dict[str, object]:
    """Retention-lane-only fc reference capture; never refresh old ENS/q truth.

    SCOPE: one already committed cycle, exact LSM/temperature grid and track.
    DRAIN: existing hourly retention cadence retries UNKNOWN without pass retries.
    RESET: genuine matching bytes/cache receipt. No source-run or snapshot write.
    """
    from scripts.extract_open_ens_localday import (
        _GRID_KEYS, _grid_identity, _read_land_mask, _read_surface_geopotential, _select_land_grid_points,
    )

    started = time.monotonic()
    deadline = min(started + 40.0, deadline_monotonic) if deadline_monotonic is not None else started + 40.0
    now = datetime.now(timezone.utc)
    report: dict[str, object] = {"capture_status": "UNKNOWN", "audit_scope": "AUDIT_ONLY_NOT_DECISION_INPUT"}
    gaps = {}
    report["track_gaps"] = gaps
    elapsed = {}
    report["stage_elapsed_seconds"] = elapsed
    stage, stage_started = "source_read", started

    def checkpoint(next_stage=None):
        nonlocal stage, stage_started
        current = time.monotonic()
        elapsed[stage] = round(current - stage_started, 6)
        report["elapsed_seconds"] = round(current - started, 6)
        report["stage"] = stage
        if current >= deadline:
            raise requests.Timeout(f"SURFACE_AUDIT_DEADLINE_EXCEEDED:{stage}")
        if next_stage is not None:
            stage, stage_started = next_stage, current

    def check_grid_header(raw, expected_hash):
        # A small compressed GRIB can declare a huge decoded field. Inspect
        # headers before the extractor allocates values; OpenData is 0.25deg.
        import eccodes as ec
        checkpoint()
        gid = ec.codes_new_from_message(raw)
        try:
            fields = {key: ec.codes_get(gid, key) for key in _GRID_KEYS}
            ni, nj = fields["Ni"], fields["Nj"]
            if (fields["gridType"] != "regular_ll" or not 0 < ni <= 1440 or not 0 < nj <= 721
                    or ec.codes_get(gid, "numberOfPoints") != ni * nj
                    or _grid_identity(fields) != expected_hash):
                raise ValueError("SURFACE_AUDIT_DECODE_GRID_BOUNDS_OR_IDENTITY_INVALID")
        finally:
            ec.codes_release(gid)
        checkpoint()

    try:
        checkpoint()
        paths = _resolve_opendata_paths()
        conn = get_forecasts_connection_read_only(deadline_monotonic=deadline)
        try:
            conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            # Registered producer profiles, not guessed metric prefixes or base cache stems.
            runs = []
            for base, metric in (("mx2t6_high", "high"), ("mn2t6_low", "low")):
                for profile in ("full", "short"):
                    checkpoint()
                    registered = _forecast_track_for_profile(ingest_track=base, horizon_profile=profile)
                    run = conn.execute("""
                        SELECT source_run_id, source_cycle_time, track FROM source_run
                        WHERE source_id=? AND track=? AND status='SUCCESS'
                          AND completeness_status='COMPLETE' AND partial_run=0
                          AND source_cycle_time<=?
                        ORDER BY source_cycle_time DESC LIMIT 1
                    """, (SOURCE_ID, registered, now.isoformat())).fetchone()
                    if run is not None:
                        run_cycle = datetime.fromisoformat(run["source_cycle_time"])
                        if run_cycle.tzinfo is None or run_cycle.utcoffset() != timedelta(0) or run_cycle > now:
                            raise ValueError("SURFACE_AUDIT_CYCLE_INVALID")
                        runs.append((base, metric, dict(run), run_cycle))
            if not runs:
                return {**report, "unavailable_reason": "SURFACE_AUDIT_NO_COMMITTED_CYCLE"}
            cycle = max(r[3] for r in runs)
            if cycle.tzinfo is None or cycle.utcoffset() != timedelta(0) or cycle.hour not in (0, 6, 12, 18) or cycle.minute or cycle.second or cycle.microsecond:
                raise ValueError("SURFACE_AUDIT_CYCLE_INVALID")
            pinned = []
            for track, metric, run, run_cycle in runs:
                if run_cycle != cycle:
                    continue
                checkpoint()
                # Indexed run -> finite coverage references -> snapshot INTEGER PRIMARY KEY.
                coverages = conn.execute("""
                    SELECT snapshot_ids_json FROM source_run_coverage
                    WHERE source_run_id=? AND source_id=? AND track=? AND temperature_metric=?
                      AND completeness_status='COMPLETE' AND readiness_status='LIVE_ELIGIBLE'
                    ORDER BY coverage_id LIMIT 16
                """, (run["source_run_id"], SOURCE_ID, run["track"], metric)).fetchall()
                selected = None
                for coverage in coverages:
                    checkpoint()
                    try:
                        ids = json.loads(coverage["snapshot_ids_json"])
                    except (ValueError, TypeError):
                        gaps[track] = "SURFACE_AUDIT_COVERAGE_REFERENCE_INVALID"
                        continue
                    if not isinstance(ids, list) or len(ids) > 64 or any(type(sid) is not int or sid <= 0 for sid in ids):
                        gaps[track] = "SURFACE_AUDIT_COVERAGE_REFERENCE_INVALID"
                        continue
                    for sid in ids:
                        checkpoint()
                        row = conn.execute("""
                            SELECT e.snapshot_id, e.source_run_id, e.temperature_metric,
                              e.source_cycle_time, e.source_available_at,
                              json_extract(e.provenance_json,'$.grid_surface_evidence') AS surface_json
                            FROM ensemble_snapshots e WHERE e.snapshot_id=? AND e.source_run_id=?
                              AND e.source_id=? AND e.temperature_metric=? AND e.authority='VERIFIED'
                              AND e.source_cycle_time=? AND julianday(e.source_available_at)<=julianday(?)
                        """, (sid, run["source_run_id"], SOURCE_ID, metric, cycle.isoformat(), now.isoformat())).fetchone()
                        if row is not None and (selected is None or row["snapshot_id"] > selected["snapshot_id"]):
                            selected = dict(row)
                if selected is not None:
                    gaps.pop(track, None)
                    pinned.append((track, selected))
                else:
                    gaps.setdefault(track, "SURFACE_AUDIT_COVERAGE_SNAPSHOT_UNAVAILABLE")
            original_refs = {}
            forward_refs = {}
            for track, _row in pinned:
                checkpoint()
                folder = _download_output_path(run_date=cycle.date(), run_hour=cycle.hour,
                    param=TRACKS[track]["open_data_param"], raw_root=paths.raw_root).parent
                cached_proof = folder / f".{track}_{cycle:%Y%m%d}_{cycle.hour:02d}z_lsm.z.proof.json"
                try:
                    if cached_proof.is_symlink() or cached_proof.stat().st_size > 65536:
                        continue
                    original_proof_bytes = cached_proof.read_bytes()
                    original = json.loads(original_proof_bytes)
                    if original.get("source_evidence_role") == "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN":
                        if any(key in original for key in ("snapshot_id", "source_run_id", "snapshot_binding_sha256")):
                            raise ValueError("SURFACE_AUDIT_FORWARD_PROOF_HAS_SNAPSHOT_PIN")
                        # The already committed coverage selected this exact
                        # snapshot above. Bind externally; keep the earlier
                        # source-only proof immutable and free of future pins.
                        forward_refs[track] = (dict(_row), original_proof_bytes)
                        continue
                    reference = conn.execute("""
                        SELECT e.snapshot_id, e.source_run_id, e.temperature_metric,
                          e.source_cycle_time, e.source_available_at,
                          json_extract(e.provenance_json,'$.grid_surface_evidence') AS surface_json
                        FROM ensemble_snapshots e JOIN source_run sr ON sr.source_run_id=e.source_run_id
                        WHERE e.snapshot_id=? AND e.source_run_id=? AND e.source_id=?
                          AND sr.source_id=e.source_id AND sr.track IN (?,?) AND e.authority='VERIFIED'
                          AND sr.source_cycle_time=e.source_cycle_time
                          AND sr.status='SUCCESS' AND sr.completeness_status='COMPLETE'
                          AND sr.partial_run=0 AND julianday(e.source_available_at)<=julianday(?)
                    """, (original.get("snapshot_id"), original.get("source_run_id"), SOURCE_ID,
                          _forecast_track_for_profile(ingest_track=track, horizon_profile="full"),
                          _forecast_track_for_profile(ingest_track=track, horizon_profile="short"), now.isoformat())).fetchone()
                    if reference is not None:
                        original_refs[track] = (dict(reference), original_proof_bytes)
                except (OSError, ValueError, TypeError):
                    pass  # missing original proof remains a narrow cache gap below
        finally:
            conn.set_progress_handler(None, 0)
            conn.close()
        checkpoint("mask_decode")
        report["source_cycle_time"] = cycle.isoformat()
        candidates = []
        for track, row in pinned:
            try:
                checkpoint()
                surface = json.loads(row["surface_json"])
                mask_path = _download_output_path(run_date=cycle.date(), run_hour=cycle.hour,
                    param=TRACKS[track]["open_data_param"], raw_root=paths.raw_root).with_name(
                        f".{track}_{cycle:%Y%m%d}_{cycle.hour:02d}z_lsm.grib2")
                proof_path = mask_path.with_suffix(".proof.json")
                if mask_path.is_symlink() or proof_path.is_symlink():
                    raise ValueError("SURFACE_AUDIT_MASK_SYMLINK")
                if not 100 <= mask_path.stat().st_size <= 1024 * 1024 or proof_path.stat().st_size > 65536:
                    raise ValueError("SURFACE_AUDIT_MASK_BOUNDS_INVALID")
                mask_bytes, mask_proof_bytes = mask_path.read_bytes(), proof_path.read_bytes()
                check_grid_header(mask_bytes, surface["temperature_grid_identity_hash"])
                mask = _read_land_mask(mask_path, proof_path)
                checkpoint()
                fetched = datetime.fromisoformat(str(mask["proof"]["source_fetched_at"]))
                if (mask_path.read_bytes() != mask_bytes or proof_path.read_bytes() != mask_proof_bytes
                        or mask["proof"]["source_cycle_time"] != cycle.isoformat()
                        or fetched.tzinfo is None or not cycle <= fetched <= now
                        or surface["mask_sha256"] != hashlib.sha256(mask_bytes).hexdigest()
                        or surface["mask_source_cycle_time"] != cycle.isoformat()
                        or surface["mask_grid_identity_hash"] != mask["grid_identity_hash"]
                        or surface["temperature_grid_identity_hash"] != mask["grid_identity_hash"]):
                    raise ValueError("SURFACE_AUDIT_MASK_TEMPERATURE_BINDING_INVALID")
                selected = _select_land_grid_points(mask["fields"], [{
                    "city": "audit", "lat": surface["request_lat"], "lon": surface["request_lon"],
                }], mask["values"].__getitem__)["audit"]
                if any(selected[key] != surface[key] for key in (
                    "selected_flat_index", "selected_lat", "selected_lon", "selected_land_fraction",
                )):
                    raise ValueError("SURFACE_AUDIT_SELECTED_POINT_INVALID")
                row["selected_point"] = {"flat_index": selected["selected_flat_index"],
                                         "lat": selected["selected_lat"], "lon": selected["selected_lon"]}
                candidates.append((track, row, mask_path, mask_bytes, mask_proof_bytes, mask))
            except requests.Timeout:
                raise
            except Exception as exc:  # optional audit isolates malformed/missing track inputs
                gaps[track] = str(exc)[:200] or type(exc).__name__
        report["track_gaps"] = gaps
        if not candidates:
            return {**report, "unavailable_reason": "SURFACE_AUDIT_NO_MATCHING_MASK"}
        grid_hashes = {c[5]["grid_identity_hash"] for c in candidates}
        if len(grid_hashes) != 1:
            raise ValueError("SURFACE_AUDIT_TRACK_GRID_MISMATCH")
        grid_hash = next(iter(grid_hashes))
        url = (f"https://data.ecmwf.int/forecasts/{cycle:%Y%m%d}/{cycle.hour:02d}z/ifs/0p25/oper/"
               f"{cycle:%Y%m%d}{cycle.hour:02d}0000-0h-oper-fc.grib2")
        index_url = url.rsplit(".", 1)[0] + ".index"

        # Reuse only complete, independently decoded cache proof; existence is not proof.
        cached = None
        checkpoint("cache_decode")
        cache_fences = {}
        snapshot_bindings = {}
        for track, row, mask_path, mask_bytes, mask_proof_bytes, mask in candidates:
            path = mask_path.with_suffix(".z.grib2")
            proof_path = path.with_suffix(".proof.json")
            index_path = path.with_suffix(".index.body")
            if path.exists() or proof_path.exists() or index_path.exists():
                try:
                    checkpoint()
                    if any(p.is_symlink() for p in (path, proof_path, index_path)):
                        raise ValueError("SURFACE_AUDIT_CACHE_SYMLINK")
                    if (not 100 <= path.stat().st_size <= 1024 * 1024
                            or index_path.stat().st_size > 1024 * 1024 or proof_path.stat().st_size > 65536):
                        raise ValueError("SURFACE_AUDIT_CACHE_BOUNDS_INVALID")
                    proof_bytes = proof_path.read_bytes()
                    cache_body = path.read_bytes()
                    check_grid_header(cache_body, grid_hash)
                    observed = _read_surface_geopotential(path, proof_path)
                    checkpoint()
                    if observed["proof"].get("source_evidence_role") == "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN":
                        if track not in forward_refs:
                            raise ValueError("SURFACE_AUDIT_FORWARD_REFERENCE_UNAVAILABLE")
                        reference, source_proof_bytes = forward_refs[track]
                        source_proof = observed["proof"]
                        index_bytes = index_path.read_bytes()
                        offset, length = _surface_z_index_entry(index_bytes, cycle)
                        written = datetime.fromisoformat(str(source_proof["source_written_at"]))
                        available = datetime.fromisoformat(str(reference["source_available_at"]))
                        phi = float(observed["values"][row["selected_point"]["flat_index"]])
                        if (proof_bytes != source_proof_bytes or source_proof != json.loads(source_proof_bytes)
                                or _surface_audit_snapshot_binding(reference) != _surface_audit_snapshot_binding(row)
                                or any(key in source_proof for key in ("snapshot_id", "source_run_id", "snapshot_binding_sha256"))
                                or source_proof["mask_sha256"] != hashlib.sha256(mask_bytes).hexdigest()
                                or source_proof["mask_grid_identity_hash"] != grid_hash
                                or observed["fields"] != mask["fields"] or observed["grid_identity_hash"] != grid_hash
                                or source_proof["source_cycle_time"] != cycle.isoformat()
                                or source_proof["source_url"] not in (url, mask["proof"]["source_url"])
                                or source_proof["source_index_url"] != source_proof["source_url"].rsplit(".", 1)[0] + ".index"
                                or hashlib.sha256(index_bytes).hexdigest() != source_proof["source_index_sha256"]
                                or hashlib.sha256(cache_body).hexdigest() != source_proof["raw_message_sha256"]
                                or (source_proof["source_index_offset"], source_proof["source_index_length"]) != (offset, length)
                                or source_proof["index_http"]["status"] != 200 or source_proof["range_http"]["status"] != 206
                                or not re.fullmatch(rf"bytes {offset}-{offset + length - 1}/(?:\d+|\*)",
                                    str(source_proof["range_http"]["headers"].get("Content-Range", "")))
                                or source_proof["source_issued_at"] is not None
                                or source_proof["source_fetched_at_role"] != "PUBLIC_HTTP_BODY_POSSESSION"
                                or source_proof["source_written_at_role"] != "LOCAL_AUDIT_DURABLE_PUBLICATION"
                                or source_proof["audit_scope"] != "AUDIT_ONLY_NOT_DECISION_INPUT"
                                or written.tzinfo is None
                                or available.tzinfo is None or not cycle <= available <= now
                                or not datetime.fromisoformat(source_proof["source_fetched_at"]) <= written <= now
                                or not math.isfinite(phi) or abs(phi) >= 1e10):
                            raise ValueError("SURFACE_AUDIT_FORWARD_REFERENCE_MISMATCH")
                        if (path.read_bytes(), index_path.read_bytes(), proof_path.read_bytes()) != (cache_body, index_bytes, source_proof_bytes):
                            raise ValueError("SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED")
                        cached = (cache_body, index_bytes, source_proof)
                        cache_fences[track] = (cache_body, index_bytes, source_proof_bytes)
                        snapshot_bindings[track] = {"snapshot_id": reference["snapshot_id"],
                            "source_run_id": reference["source_run_id"],
                            "snapshot_binding_sha256": _surface_audit_snapshot_binding(reference),
                            "source_proof_sha256": hashlib.sha256(source_proof_bytes).hexdigest(),
                            "grid_identity_hash": grid_hash, "selected_point": row["selected_point"], "raw_phi_m2_s2": phi}
                        continue
                    if track not in original_refs:
                        raise ValueError("SURFACE_AUDIT_ORIGINAL_REFERENCE_UNAVAILABLE")
                    reference, pinned_proof = original_refs[track]
                    if (pinned_proof != proof_bytes or proof_path.read_bytes() != pinned_proof
                            or observed["proof"] != json.loads(pinned_proof)):
                        raise ValueError("SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED")
                    original_surface = json.loads(reference["surface_json"])
                    original_point = {"flat_index": original_surface["selected_flat_index"],
                                      "lat": original_surface["selected_lat"], "lon": original_surface["selected_lon"]}
                    original_selected = _select_land_grid_points(mask["fields"], [{
                        "city": "original", "lat": original_surface["request_lat"], "lon": original_surface["request_lon"],
                    }], mask["values"].__getitem__)["original"]
                    phi = float(observed["values"][original_point["flat_index"]])
                    if (observed["proof"]["snapshot_id"] != reference["snapshot_id"]
                            or observed["proof"]["source_run_id"] != reference["source_run_id"]
                            or reference["temperature_metric"] != row["temperature_metric"]
                            or reference["source_cycle_time"] != cycle.isoformat()
                            or observed["proof"]["snapshot_binding_sha256"] != _surface_audit_snapshot_binding(reference)
                            or observed["proof"]["selected_point"] != original_point
                            or observed["proof"]["raw_phi_m2_s2"] != phi or not math.isfinite(phi)
                            or observed["proof"]["mask_sha256"] != original_surface["mask_sha256"]
                            or observed["proof"]["mask_grid_identity_hash"] != original_surface["mask_grid_identity_hash"]
                            or observed["proof"]["temperature_grid_identity_hash"] != original_surface["temperature_grid_identity_hash"]
                            or original_surface["mask_grid_identity_hash"] != grid_hash
                            or original_surface["temperature_grid_identity_hash"] != grid_hash
                            or original_surface["mask_sha256"] != hashlib.sha256(mask_bytes).hexdigest()
                            or any(original_selected[key] != original_surface[key] for key in (
                                "selected_flat_index", "selected_lat", "selected_lon", "selected_land_fraction"))
                            or observed["proof"]["source_issued_at"] is not None
                            or observed["proof"]["source_fetched_at_role"] != "PUBLIC_HTTP_BODY_POSSESSION"
                            or observed["proof"]["source_written_at_role"] != "LOCAL_AUDIT_DURABLE_PUBLICATION"
                            or observed["proof"]["audit_scope"] != "AUDIT_ONLY_NOT_DECISION_INPUT"):
                        raise ValueError("SURFACE_AUDIT_ORIGINAL_REFERENCE_MISMATCH")
                    index_bytes = index_path.read_bytes()
                    offset, length = _surface_z_index_entry(index_bytes, cycle)
                    written = datetime.fromisoformat(str(observed["proof"]["source_written_at"]))
                    if (observed["grid_identity_hash"] != grid_hash
                            or observed["proof"]["source_cycle_time"] != cycle.isoformat()
                            or hashlib.sha256(index_bytes).hexdigest() != observed["proof"]["source_index_sha256"]
                            or observed["proof"]["source_url"] != url
                            or observed["proof"]["source_index_url"] != index_url
                            or (observed["proof"]["source_index_offset"], observed["proof"]["source_index_length"]) != (offset, length)
                            or observed["proof"]["index_http"]["status"] != 200
                            or observed["proof"]["range_http"]["status"] != 206
                            or not re.fullmatch(rf"bytes {offset}-{offset + length - 1}/(?:\d+|\*)",
                                               str(observed["proof"]["range_http"]["headers"].get("Content-Range", "")))
                            or written.tzinfo is None
                            or datetime.fromisoformat(reference["source_available_at"]) > written
                            or not datetime.fromisoformat(observed["proof"]["source_fetched_at"]) <= written <= now):
                        raise ValueError("SURFACE_AUDIT_CACHE_IDENTITY_INVALID")
                    if proof_path.read_bytes() != pinned_proof:
                        raise ValueError("SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED")
                    cached = (path.read_bytes(), index_bytes, observed["proof"])
                    cache_fences[track] = (cached[0], index_bytes, proof_bytes)
                except requests.Timeout:
                    raise
                except FileNotFoundError:
                    # Interrupted publication may leave a body but no commit
                    # marker. A new real HTTP capture can complete it only if
                    # every already-present byte still matches (no overwrite).
                    pass
                except Exception as exc:
                    gaps[track] = f"SURFACE_AUDIT_CACHE_CONFLICT:{str(exc)[:160]}"
        active = [c for c in candidates if c[0] not in gaps]
        if not active:
            return {**report, "unavailable_reason": "SURFACE_AUDIT_CACHE_CONFLICT"}
        with tempfile.TemporaryDirectory(prefix=".surface_audit_", dir=active[0][2].parent) as temp:
            staging = Path(temp)
            if cached is None:
                checkpoint("http")
                index_bytes, body, metadata = _fetch_surface_audit_bytes(cycle, url, index_url, deadline=deadline)
                checkpoint()
                offset, length = _surface_z_index_entry(index_bytes, cycle)
                if len(body) != length:
                    raise ValueError("SURFACE_AUDIT_IPC_MESSAGE_LENGTH_INVALID")
                proof = {"source": "ecmwf_open_data_ifs_oper_fc_step0_z", "source_url": url,
                         "source_index_url": index_url, "source_cycle_time": cycle.isoformat(),
                         "source_index_offset": offset, "source_index_length": length,
                         "source_index_sha256": hashlib.sha256(index_bytes).hexdigest(),
                         "raw_message_sha256": hashlib.sha256(body).hexdigest(),
                         "source_fetched_at": metadata["source_fetched_at"],
                         "source_fetched_at_role": "PUBLIC_HTTP_BODY_POSSESSION",
                         "source_issued_at": None, "index_http": metadata["index_http"], "range_http": metadata["range_http"],
                         "audit_scope": "AUDIT_ONLY_NOT_DECISION_INPUT",
                         "quantity_role": "model_surface_geopotential_on_wire_distribution_grid"}
            else:
                body, index_bytes, proof = cached
            checkpoint("z_decode")
            staged_body, staged_index, staged_proof = staging / "z.grib2", staging / "z.index", staging / "z.proof.json"
            for path, content in ((staged_body, body), (staged_index, index_bytes)):
                with path.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            staged_proof.write_text(json.dumps(proof), encoding="utf-8")
            check_grid_header(body, grid_hash)
            observed = _read_surface_geopotential(staged_body, staged_proof)
            checkpoint("publish")
            if observed["grid_identity_hash"] != grid_hash or observed["fields"] != active[0][5]["fields"]:
                raise ValueError("SURFACE_AUDIT_Z_GRID_MISMATCH")
            captured = []
            for track, row, mask_path, mask_bytes, mask_proof_bytes, mask in active:
                try:
                    checkpoint()
                    if mask_path.read_bytes() != mask_bytes or mask_path.with_suffix(".proof.json").read_bytes() != mask_proof_bytes:
                        raise ValueError("SURFACE_AUDIT_MASK_GENERATION_CHANGED")
                    dest = mask_path.with_suffix(".z.grib2")
                    if track in cache_fences:
                        actual_cache = (dest.read_bytes(), dest.with_suffix(".index.body").read_bytes(),
                                        dest.with_suffix(".proof.json").read_bytes())
                        if actual_cache != cache_fences[track]:
                            raise ValueError("SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED")
                        captured.append(track)
                        continue
                    phi = float(observed["values"][row["selected_point"]["flat_index"]])
                    if not math.isfinite(phi) or abs(phi) >= 1e10:
                        raise ValueError("SURFACE_AUDIT_PHI_INVALID")
                    # Atomic no-overwrite hard links; proof is the final commit marker.
                    for source, target in ((staged_body, dest), (staged_index, dest.with_suffix(".index.body"))):
                        checkpoint()
                        try:
                            os.link(source, target)
                        except FileExistsError:
                            if target.is_symlink() or target.read_bytes() != source.read_bytes():
                                raise ValueError("SURFACE_AUDIT_CACHE_PUBLISH_CONFLICT")
                    directory_fd = os.open(dest.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    track_proof = {**proof, "source_written_at": datetime.now(timezone.utc).isoformat(),
                                   "source_written_at_role": "LOCAL_AUDIT_DURABLE_PUBLICATION",
                                   "mask_sha256": hashlib.sha256(mask_bytes).hexdigest(),
                                   "mask_grid_identity_hash": grid_hash,
                                   "temperature_grid_identity_hash": grid_hash,
                                   "snapshot_id": row["snapshot_id"], "source_run_id": row["source_run_id"],
                                   "snapshot_binding_sha256": _surface_audit_snapshot_binding(row),
                                   "selected_point": row["selected_point"], "raw_phi_m2_s2": phi}
                    if proof.get("source_evidence_role") == "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN":
                        # A missing sibling cache inherits actual source bytes,
                        # never another track's snapshot or a pin added later.
                        for key in ("snapshot_id", "source_run_id", "snapshot_binding_sha256", "selected_point", "raw_phi_m2_s2"):
                            track_proof.pop(key)
                    proof_dest = dest.with_suffix(".proof.json")
                    track_proof_file = staging / f"{track}.proof.json"
                    with track_proof_file.open("w") as handle:
                        json.dump(track_proof, handle, sort_keys=True)
                        handle.flush()
                        os.fsync(handle.fileno())
                    checkpoint()
                    try:
                        os.link(track_proof_file, proof_dest)
                    except FileExistsError:
                        if proof_dest.is_symlink() or proof_dest.read_bytes() != track_proof_file.read_bytes():
                            raise ValueError("SURFACE_AUDIT_CACHE_PROOF_CONFLICT")
                    directory_fd = os.open(dest.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                    if time.monotonic() >= deadline:
                        # Expired durability is not a committed audit receipt.
                        # Remove only our own inode, never a concurrent publisher.
                        if os.path.samestat(proof_dest.stat(), track_proof_file.stat()):
                            proof_dest.unlink()
                        checkpoint()
                    checkpoint()
                    if proof.get("source_evidence_role") == "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN":
                        snapshot_bindings[track] = {"snapshot_id": row["snapshot_id"], "source_run_id": row["source_run_id"],
                            "snapshot_binding_sha256": _surface_audit_snapshot_binding(row),
                            "source_proof_sha256": hashlib.sha256(proof_dest.read_bytes()).hexdigest(),
                            "grid_identity_hash": grid_hash, "selected_point": row["selected_point"], "raw_phi_m2_s2": phi}
                    captured.append(track)
                except requests.Timeout:
                    raise
                except Exception as exc:
                    gaps[track] = str(exc)[:200] or type(exc).__name__
            return {**report, "capture_status": "OBSERVED" if captured else "UNKNOWN",
                    "captured_tracks": captured, "grid_identity_hash": grid_hash,
                    "raw_message_sha256": proof["raw_message_sha256"], "cache_reused": cached is not None,
                    "snapshot_bindings": {track: snapshot_bindings[track] for track in captured if track in snapshot_bindings},
                    "cache_binding_role": ("SOURCE_ONLY_BOUND_TO_COMMITTED_COVERAGE" if any(track in snapshot_bindings for track in captured)
                                           else "ORIGINAL_IMMUTABLE_SNAPSHOT_NOT_CURRENT_TARGET")}
    except Exception as exc:  # failures never modify the mandatory forecast result
        elapsed[stage] = round(time.monotonic() - stage_started, 6)
        report["stage"] = stage
        report["elapsed_seconds"] = round(time.monotonic() - started, 6)
        if isinstance(exc, sqlite3.OperationalError):
            reason = "SURFACE_AUDIT_DB_DEADLINE_EXCEEDED" if time.monotonic() >= deadline else "SURFACE_AUDIT_DB_READ_FAILED"
            return {**report, "unavailable_reason": reason}
        return {**report, "unavailable_reason": str(exc)[:200] or type(exc).__name__}


def _probe_index_member_count(
    client: Any,
    *,
    cycle_date: date,
    cycle_hour: int,
    param: str,
    stream: str,
    ensemble_type: str,
    step: int,
    deadline_monotonic: float,
) -> int:
    """Read one exact ensemble index without downloading any GRIB bytes."""
    session = _RateLimitedSession()
    session._zeus_deadline = deadline_monotonic
    client.session = session
    try:
        target = Path(tempfile.gettempdir()) / "zeus_opendata_availability_probe.grib2"
        result = client._get_urls(
            target=str(target),
            use_index=False,
            date=int(cycle_date.strftime("%Y%m%d")),
            time=cycle_hour,
            stream=stream,
            type=[ensemble_type],
            step=[step],
            param=[param],
        )
        _remaining_step_timeout(deadline_monotonic)
        return sum(
            len(parts)
            for _url, parts in _resolve_index_parts(
                client,
                result,
                deadline=deadline_monotonic,
            )
        )
    finally:
        session.close()


def probe_open_ens_cycle_release(
    *,
    track: str,
    run_date: date,
    run_hour: int,
    deadline_monotonic: float,
) -> dict[str, object]:
    """Classify exact-cycle release from index evidence without source writes.

    ``released`` means the exact track parameter's nearest step has the full
    51-member ENS shape (50 PF + 1 CF/oper fallback). It is only permission to
    run the normal collector, never source-run or readiness truth itself.
    """
    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    if time.monotonic() >= deadline_monotonic:
        return {"status": "unknown", "reason": "AVAILABILITY_PROBE_DEADLINE"}
    source_spec = gate_source(SOURCE_ID)
    gate_source_role(source_spec, FORECAST_SOURCE_ROLE)

    cfg = TRACKS[track]
    probe_step = min(STEP_HOURS)
    absent_pf_mirrors = 0
    first_unknown_reason: str | None = None

    def remember_unknown(reason: str) -> None:
        nonlocal first_unknown_reason
        if first_unknown_reason is None:
            first_unknown_reason = reason

    def network_reason(exc: BaseException) -> str:
        if time.monotonic() >= deadline_monotonic:
            return "AVAILABILITY_PROBE_DEADLINE"
        return f"NETWORK:{type(exc).__name__}"

    # Azure's Client constructor obtains an SAS token before the
    # deadline-aware session is installed. Do not create that network path.
    if any(mirror not in _PROBE_SAFE_CLIENT_SOURCES for mirror in _DOWNLOAD_SOURCES):
        return {
            "status": "unknown",
            "reason": f"UNSAFE_PROBE_SOURCE:{next(mirror for mirror in _DOWNLOAD_SOURCES if mirror not in _PROBE_SAFE_CLIENT_SOURCES)}",
        }
    from ecmwf.opendata import Client  # imported here: conda env only on daemon worker

    for mirror in _DOWNLOAD_SOURCES:
        if time.monotonic() >= deadline_monotonic:
            return {"status": "unknown", "reason": "AVAILABILITY_PROBE_DEADLINE"}
        try:
            client = Client(source=mirror)
            pf_members = _probe_index_member_count(
                client,
                cycle_date=run_date,
                cycle_hour=run_hour,
                param=cfg["open_data_param"],
                stream="enfo",
                ensemble_type="pf",
                step=probe_step,
                deadline_monotonic=deadline_monotonic,
            )
        except requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) == 404:
                absent_pf_mirrors += 1
                continue
            remember_unknown(f"HTTP_{getattr(exc.response, 'status_code', 'UNKNOWN')}")
            continue
        except ValueError as exc:
            if "Cannot find index entries matching" in str(exc):
                absent_pf_mirrors += 1
                continue
            remember_unknown(f"INDEX_ERROR:{type(exc).__name__}")
            continue
        except (requests.RequestException, OSError) as exc:
            remember_unknown(network_reason(exc))
            continue

        # Any PF index evidence means the newest cycle has begun publishing.
        # Missing/incomplete CF must therefore retain newest-cycle priority.
        if pf_members != 50:
            remember_unknown(f"INCOMPLETE_PF_MEMBERS:{pf_members}")
            continue
        try:
            try:
                cf_members = _probe_index_member_count(
                    client,
                    cycle_date=run_date,
                    cycle_hour=run_hour,
                    param=cfg["open_data_param"],
                    stream="enfo",
                    ensemble_type="cf",
                    step=probe_step,
                    deadline_monotonic=deadline_monotonic,
                )
            except ValueError as exc:
                if "Cannot find index entries matching" not in str(exc):
                    raise
                try:
                    cf_members = _probe_index_member_count(
                        client,
                        cycle_date=run_date,
                        cycle_hour=run_hour,
                        param=cfg["open_data_param"],
                        stream="oper",
                        ensemble_type="fc",
                        step=probe_step,
                        deadline_monotonic=deadline_monotonic,
                    )
                except ValueError as exc:
                    if "Cannot find index entries matching" in str(exc):
                        raise ValueError("Cannot find index entries matching {'type': ['cf', 'fc']}")
                    raise
            if cf_members == 1:
                return {
                    "status": "released",
                    "mirror": mirror,
                    "pf_members": pf_members,
                    "cf_members": cf_members,
                    "step": probe_step,
                }
            remember_unknown(f"INCOMPLETE_CF_MEMBERS:{cf_members}")
        except requests.HTTPError as exc:
            if getattr(exc.response, "status_code", None) == 404:
                remember_unknown("INCOMPLETE_CF")
            else:
                remember_unknown(f"HTTP_{getattr(exc.response, 'status_code', 'UNKNOWN')}")
        except (requests.RequestException, OSError) as exc:
            remember_unknown(network_reason(exc))
        except ValueError as exc:
            if "Cannot find index entries matching" in str(exc):
                remember_unknown("INCOMPLETE_CF")
            else:
                remember_unknown(f"INDEX_ERROR:{type(exc).__name__}")

    if _DOWNLOAD_SOURCES and absent_pf_mirrors == len(_DOWNLOAD_SOURCES):
        return {"status": "not_released", "reason": "NOT_RELEASED_PF_INDEX"}
    return {
        "status": "unknown",
        "reason": first_unknown_reason or "AVAILABILITY_PROBE_INCOMPLETE",
    }


def _retrieve_step_with_controlled_ranges(
    client: Any,
    *,
    target: Path,
    _deadline: float | None = None,
    _cycle_deadline_enforced: bool = False,
    **kwargs: Any,
) -> Any:
    """Retrieve one indexed OpenData step with Zeus-owned single Range GETs.

    ecmwf-opendata's default ``Client.retrieve`` delegates indexed GRIB assembly
    to multiurl. In live forecast runs multiurl was the broken boundary: it can
    emit bursty multi-range/internal-retry traffic outside Zeus' token bucket,
    producing AWS S3 SlowDown / HTTP 429 followed by 120-second sleeps. This
    downloader keeps index resolution in ecmwf-opendata but owns every HTTP GET.
    Outer retry/failover remains in ``_fetch_one_step``.
    """

    if not hasattr(client, "_get_urls"):
        if _cycle_deadline_enforced:
            # The legacy SDK path does not expose request timeouts or chunk
            # boundaries, so it cannot honor a bounded continuity retry.
            raise requests.Timeout("CYCLE_DEADLINE_EXCEEDED")
        _fetch_bucket.acquire(deadline=_deadline)
        return client.retrieve(target=str(target), **kwargs)

    client.session = _RateLimitedSession()
    client.session._zeus_deadline = _deadline
    result = client._get_urls(target=str(target), use_index=False, **kwargs)
    _remaining_step_timeout(_deadline)
    indexed_parts = _resolve_index_parts(client, result, deadline=_deadline)
    if indexed_parts:
        result.urls = indexed_parts

    target_path = Path(result.target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    verify = getattr(client, "verify", True)
    session = client.session
    plan = _range_resume_plan(result.urls)
    if plan is None:
        # Plain-object downloads have no byte-range identity to prove.  Do not
        # reuse a stale indexed prefix if an upstream response shape changes.
        _reset_range_resume(target_path)
        bytes_written = 0
        with target_path.open("wb") as out:
            for item in result.urls:
                response = session.get(
                    item,
                    stream=True,
                    timeout=_remaining_step_timeout(_deadline),
                    verify=verify,
                )
                try:
                    if response.status_code != 200:
                        response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        _remaining_step_timeout(_deadline)
                        if chunk:
                            out.write(chunk)
                            bytes_written += len(chunk)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
        result.size = bytes_written
        return result

    completed_ranges, entity_tags = _load_range_resume(
        target_path,
        plan=plan,
        session=session,
        verify=verify,
        deadline=_deadline,
    )
    resumable = True
    with target_path.open("ab") as out:
        for range_index, (url, offset, length) in enumerate(plan[completed_ranges:], completed_ranges):
            end = offset + length - 1
            response = session.get(
                url,
                stream=True,
                headers={"Range": f"bytes={offset}-{end}"},
                timeout=_remaining_step_timeout(_deadline),
                verify=verify,
            )
            try:
                entity_tag = _validate_range_response(response, offset=offset, length=length)
                if not _is_strong_etag(entity_tag):
                    # A fresh transfer can still complete without a resume
                    # identity.  A reused prefix cannot: discard it and use
                    # the existing retry loop to restart from byte zero.
                    resumable = False
                    _range_resume_manifest_path(target_path).unlink(missing_ok=True)
                    if completed_ranges:
                        raise requests.ConnectionError(
                            "ECMWF range response lacks a strong ETag after resuming a prefix"
                        )
                known_tag = entity_tags.get(url)
                if _is_strong_etag(entity_tag) and known_tag is not None and entity_tag != known_tag:
                    raise _http_error_for_response(
                        response,
                        f"Range GET entity changed while downloading {url}",
                    )
                range_bytes = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    _remaining_step_timeout(_deadline)
                    if chunk:
                        if range_bytes + len(chunk) > length:
                            raise _http_error_for_response(
                                response,
                                f"Range GET body exceeds requested length {length}",
                            )
                        out.write(chunk)
                        range_bytes += len(chunk)
                if range_bytes != length:
                    raise _http_error_for_response(
                        response,
                        f"Range GET body length {range_bytes} does not match {length}",
                    )
                if _is_strong_etag(entity_tag):
                    entity_tags[url] = entity_tag
                out.flush()
                os.fsync(out.fileno())
                if resumable:
                    _write_range_resume_manifest(
                        target_path,
                        plan=plan,
                        completed_ranges=range_index + 1,
                        entity_tags=entity_tags,
                    )
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

    result.size = sum(length for _, _, length in plan)
    return result


_CONDA_PYTHON_PROBE_MODULE = "eccodes"
_CONDA_PYTHON_PROBE_TIMEOUT_SECONDS = 8.0


def _interpreter_has_dependency(candidate: str, *, probe_module: str) -> bool:
    """Return True iff `candidate` is executable and can `import probe_module`.

    Existence alone does not prove the interpreter is the right one — a
    ~/miniconda3/bin/python that exists but predates the ecmwf deps (or
    belongs to an unrelated env) would otherwise be silently accepted and
    every extract subprocess launched under it would fail. A dependency
    probe is the only cheap proof that matters.
    """
    path = Path(candidate)
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    try:
        result = subprocess.run(
            [candidate, "-c", f"import {probe_module}"],
            capture_output=True,
            timeout=_CONDA_PYTHON_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _conda_python() -> str:
    """Path to the Python interpreter with ecmwf.opendata + eccodes installed.

    Resolution order:
      1. ZEUS_ECMWF_PYTHON env var (explicit deployment config; trusted
         as-is — an operator who sets this is asserting it is correct).
      2. ~/miniconda3/bin/python (conda default install location, portable
         across machines/usernames via Path.home()), ACCEPTED ONLY if it is
         executable and can import eccodes.
      3. `python3`, then `python`, resolved on PATH (covers non-default
         conda install dirs) — `python3` first because a bare `python` can
         resolve to Python 2 on some systems; same executability +
         dependency-import validation either way (belt-and-suspenders: the
         import probe alone already rejects a Python 2 interpreter, since
         these deps aren't installed there).
      4. sys.executable, with a logged warning — no candidate proved it has
         the ecmwf deps, so the extract subprocess launched under it is
         expected to fail; this keeps the daemon alive to log the failure
         rather than crash outright, and test/dry-run environments that
         already carry the deps on sys.executable are unaffected.
    """
    from_env = os.environ.get("ZEUS_ECMWF_PYTHON")
    if from_env:
        return from_env
    candidate = str(Path.home() / "miniconda3" / "bin" / "python")
    if _interpreter_has_dependency(candidate, probe_module=_CONDA_PYTHON_PROBE_MODULE):
        return candidate
    for path_candidate in (shutil.which("python3"), shutil.which("python")):
        if path_candidate and _interpreter_has_dependency(
            path_candidate, probe_module=_CONDA_PYTHON_PROBE_MODULE
        ):
            return path_candidate
    logger.warning(
        "_conda_python: no interpreter with '%s' importable found (checked "
        "ZEUS_ECMWF_PYTHON, %s, PATH `python3`/`python`); falling back to "
        "sys.executable (%s) — the extract subprocess will fail if it lacks "
        "ecmwf deps.",
        _CONDA_PYTHON_PROBE_MODULE,
        candidate,
        sys.executable,
    )
    return sys.executable


def _step_hours_signature() -> str:
    """Compact filename-safe signature of STEP_HOURS.

    Encodes range + count + sha8 to stay under NAME_MAX (255 bytes on macOS,
    HFS+/APFS) regardless of grid size. Joining all 70+ steps with '-'
    produced a ~280-byte filename and crashed the download with OSError 63
    "File name too long" at write time — every Open Data fetch from 2026-05-08
    onward failed at byte 0 because of this. The signature stays stable per
    STEP_HOURS configuration so cached files are reusable across restarts.
    """
    import hashlib
    sig = ",".join(str(value) for value in STEP_HOURS)
    digest = hashlib.sha256(sig.encode()).hexdigest()[:8]
    return f"{min(STEP_HOURS)}to{max(STEP_HOURS)}_n{len(STEP_HOURS)}_h{digest}"


def _download_output_path(
    *,
    run_date: date,
    run_hour: int,
    param: str,
    raw_root: Path | None = None,
) -> Path:
    steps_sig = _step_hours_signature()
    return (
        (raw_root or FIFTY_ONE_ROOT)
        / "raw"
        / "ecmwf_open_ens"
        / "ecmwf"
        / run_date.strftime("%Y%m%d")
        / f"open_ens_{run_date.strftime('%Y%m%d')}_{run_hour:02d}z_steps_{steps_sig}_params_{param}.grib2"
    )


def _step_cache_path(output_dir: Path, *, run_date: date, run_hour: int, step: int, param: str) -> Path:
    """Per-step cache for the full executable member set.

    Older ``.stepNNN_<param>.grib2`` cache files can contain only 50
    perturbed ``enfo/ef`` members when ECMWF omits ``cf`` from that file. The
    explicit suffix prevents resume from reusing those pf-only artifacts after
    the control/oper merge fix.

    The cycle identity is part of the cache key. Reusing 00Z step bytes for a
    later 12Z source_run would preserve file bytes while corrupting forecast
    provenance.
    """

    return output_dir / f".{run_date.strftime('%Y%m%d')}_{run_hour:02d}z_step{step:03d}_{param}_ens51.grib2"


def _cycle_extract_dir_name(*, run_date: date, run_hour: int) -> str:
    base = run_date.strftime("%Y%m%d")
    if run_hour == 0:
        return base
    return f"{base}_cycle{run_hour:02d}z"


def _parse_cycle_extract_dir_name(name: str) -> tuple[date, int] | None:
    """Inverse of `_cycle_extract_dir_name`; None when the name is not one of ours.

    Keeps the pruner from ever treating an unrecognised directory as a spent cycle.
    """

    match = re.fullmatch(r"(?P<date>\d{8})(?:_cycle(?P<hour>\d{2})z)?", name)
    if match is None:
        return None
    try:
        day = datetime.strptime(match.group("date"), "%Y%m%d").date()
    except ValueError:
        return None
    hour = int(match.group("hour") or 0)
    if hour not in {0, 6, 12, 18}:
        return None
    return day, hour


def _prune_superseded_coordinate_manifest_cycles(
    *,
    coordinate_raw_root: Path,
    extract_subdir: str,
    keep_run_date: date,
    keep_run_hour: int,
) -> dict[str, object]:
    """Delete decoded-JSON cycle views the ingest can no longer consume.

    `_build_cycle_scoped_json_root` assembles a temp view of the SELECTED cycle only
    ("stale raw directories cannot satisfy a new source_run"), so once a newer cycle
    is ingested every older `<city>/<cycle>` tree under the manifest sha is
    write-only. Nothing pruned them: the dirs inherited none of the grib cache's
    retention, and measured 2026-09-18 they held 33 cycles per city across 54
    cities spanning 2026-09-09..09-17 — 234 MB, ~26 MB/day, unbounded (~9.5 GB/yr).

    Only cycles STRICTLY OLDER than the one just ingested are removed, and only
    directories whose name parses as one of ours. The kept cycle is never touched,
    so a retry of the same cycle still finds its view. Best-effort by design: a
    failure here must never fail an ingest that already committed its canonical
    truth, so errors are counted and returned rather than raised.
    """

    source_subdir = coordinate_raw_root / extract_subdir
    summary: dict[str, object] = {
        "status": "NO_SUPERSEDED_CYCLES",
        "removed_cycle_dirs": 0,
        "removed_bytes": 0,
        "errors": [],
    }
    if source_subdir.is_symlink() or not source_subdir.is_dir():
        return summary

    keep = (keep_run_date, keep_run_hour)
    removed = 0
    removed_bytes = 0
    errors: list[str] = []
    for city_dir in sorted(source_subdir.iterdir()):
        if city_dir.is_symlink() or not city_dir.is_dir():
            continue
        for cycle_dir in sorted(city_dir.iterdir()):
            if cycle_dir.is_symlink() or not cycle_dir.is_dir():
                continue
            parsed = _parse_cycle_extract_dir_name(cycle_dir.name)
            if parsed is None or parsed >= keep:
                continue
            try:
                size = sum(
                    path.stat().st_size
                    for path in cycle_dir.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
                shutil.rmtree(cycle_dir)
            except OSError as exc:
                errors.append(f"{cycle_dir.name}:{type(exc).__name__}")
                continue
            removed += 1
            removed_bytes += size

    summary["status"] = "PRUNED" if removed else "NO_SUPERSEDED_CYCLES"
    summary["removed_cycle_dirs"] = removed
    summary["removed_bytes"] = removed_bytes
    summary["errors"] = errors
    return summary


def _build_cycle_scoped_json_root(
    *,
    raw_root: Path,
    extract_subdir: str,
    run_date: date,
    run_hour: int,
    tmp_root: Path,
    preserve_scopes: set[tuple[str, str, str]] | None = None,
    available_steps: set[int] | None = None,
) -> tuple[Path, str, int]:
    """Build an ingest view containing only the selected source cycle's JSON.

    On a target-scoped PARTIAL retry, already-qualified same-run scopes are
    excluded from this view.  A partial extractor payload for such a scope must
    not INSERT OR REPLACE its prior qualified snapshot before the coverage
    writer has a chance to preserve the old possession clock.  When
    ``available_steps`` is supplied, a payload whose native window extends
    beyond the current attempt is excluded as well; stale far-horizon JSON must
    not become a new blocked snapshot on a near-only retry.
    """

    cycle_dir_name = _cycle_extract_dir_name(run_date=run_date, run_hour=run_hour)
    source_subdir = raw_root / extract_subdir
    view_subdir = tmp_root / extract_subdir
    view_subdir.mkdir(parents=True, exist_ok=True)
    if not source_subdir.exists():
        return tmp_root, cycle_dir_name, 0

    linked = 0
    preserve_scopes = preserve_scopes or set()
    metric = "high" if extract_subdir.endswith("_max") else "low"
    for city_dir in source_subdir.iterdir():
        if not city_dir.is_dir():
            continue
        source_cycle_dir = city_dir / cycle_dir_name
        if not source_cycle_dir.is_dir():
            continue
        view_cycle_dir = view_subdir / city_dir.name / cycle_dir_name
        view_cycle_dir.mkdir(parents=True, exist_ok=True)
        for source_json in sorted(source_cycle_dir.glob("*.json")):
            target_match = re.search(r"_target_(\d{4}-\d{2}-\d{2})_", source_json.name)
            scope = (city_dir.name, target_match.group(1), metric) if target_match else None
            if scope in preserve_scopes:
                continue
            if available_steps is not None:
                try:
                    payload = json.loads(source_json.read_text(encoding="utf-8"))
                    ranges = [
                        *payload.get("selected_step_ranges_inner", []),
                        *payload.get("selected_step_ranges_boundary", []),
                    ]
                    required_max_step = max(
                        int(str(step_range).split("-", 1)[1])
                        for step_range in ranges
                        if "-" in str(step_range)
                    ) if ranges else None
                except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
                    required_max_step = None
                if required_max_step is not None and required_max_step not in available_steps:
                    continue
            target_json = view_cycle_dir / source_json.name
            try:
                target_json.symlink_to(source_json.resolve())
            except OSError:
                shutil.copy2(source_json, target_json)
            linked += 1
    return tmp_root, cycle_dir_name, linked


def _qualified_partial_retry_scopes(
    conn,
    *,
    source_run_id: str,
) -> set[tuple[str, str, str]]:
    """Return same-run scopes whose LIVE evidence must survive a PARTIAL retry."""

    rows = conn.execute(
        """
        SELECT city, target_local_date, temperature_metric
          FROM source_run_coverage
         WHERE source_run_id = ?
           AND completeness_status = 'COMPLETE'
           AND readiness_status = 'LIVE_ELIGIBLE'
        """,
        (source_run_id,),
    ).fetchall()
    return {
        (
            str(row[0]).strip().lower().replace(" ", "-"),
            str(row[1]).strip(),
            str(row[2]).strip(),
        )
        for row in rows
        if row[0] and row[1] and row[2]
    }


def _snapshot_scope_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("city") or "").strip().lower().replace(" ", "-"),
        str(row.get("target_date") or "").strip(),
        str(row.get("temperature_metric") or "").strip(),
    )


def _select_cycle_for_track(*, track: str, now_utc: datetime) -> tuple[FetchDecision, dict[str, object]]:
    """Select a release-calendar-approved source run for the configured horizon."""
    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    return select_source_run_for_target_horizon(
        now_utc=now_utc,
        source_id=SOURCE_ID,
        track=track,
        required_max_step_hours=max(STEP_HOURS),
        allow_partial=True,
    )


def _status_for_ingest_summary(summary: dict) -> str:
    written = int(summary.get("written", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)
    if written == 0 and skipped == 0:
        return "empty_ingest"
    return "ok"


def _stable_id(prefix: str, *parts: object) -> str:
    payload = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _source_cycle_expires_at(source_cycle_time: datetime, forecast_track: str) -> datetime:
    """Coverage/readiness expiry ANCHORED TO THE CYCLE, never to the write wall-clock (M3 fix).

    The prior ``computed_at + 24h`` was a GUESS: re-stamping computed_at on a re-ingest granted a
    fresh 24h TTL and the expiry clock disagreed with the source's real staleness law (the same
    twin-clock disease ``replacement_readiness_expires_at`` killed for the replacement path). The
    lawful lifetime of a forecast cycle's data is ``max_source_lag_seconds`` after the CYCLE time —
    the calendar's own publication tolerance — not after we happened to write the row. That bound
    is the single source of truth in the release calendar, keyed by the source's ingest track
    (``TRACKS`` keys ARE the calendar track keys); we read it and anchor expiry to the CYCLE.

    ``forecast_track`` is the horizon-suffixed label (e.g. "mx2t6_high_full_horizon"); the calendar
    is keyed by the base ingest track ("mx2t6_high"). A missing calendar entry is a real config
    error on a Tier-0 money path and raises (fail-loud) rather than substituting a guessed lag.
    """
    cycle = source_cycle_time if source_cycle_time.tzinfo else source_cycle_time.replace(tzinfo=timezone.utc)
    cycle = cycle.astimezone(timezone.utc)
    base_track = next(
        (t for t in TRACKS if forecast_track == t or forecast_track.startswith(f"{t}_")),
        None,
    )
    entry = get_entry(SOURCE_ID, base_track) if base_track is not None else None
    if entry is None:
        raise ValueError(
            f"release calendar has no entry for {SOURCE_ID!r} track derived from "
            f"{forecast_track!r}; cannot derive a cycle-anchored expiry (refusing a guessed TTL)"
        )
    return cycle + timedelta(seconds=int(entry.max_source_lag_seconds))


def _horizon_profile_for_cycle(
    *,
    cycle_hour: int,
    selection_metadata: dict[str, object],
    manual_cycle_override: bool,
) -> str:
    if not manual_cycle_override:
        profile = selection_metadata.get("horizon_profile")
        if isinstance(profile, str) and profile:
            return profile
    if cycle_hour in (0, 12):
        return "full"
    if cycle_hour in (6, 18):
        return "short"
    return "manual"


def _forecast_track_for_profile(*, ingest_track: str, horizon_profile: str) -> str:
    if horizon_profile in {"full", "short"}:
        return f"{ingest_track}_{horizon_profile}_horizon"
    return f"{ingest_track}_{horizon_profile}"


def _source_run_outcome(summary: dict, status: str) -> tuple[str, str, bool, str | None]:
    written = int(summary.get("written", 0) or 0)
    errors = int(summary.get("errors", 0) or 0)
    if status == "ok" and written > 0 and errors == 0:
        return "SUCCESS", "COMPLETE", False, None
    if written > 0:
        return "PARTIAL", "PARTIAL", True, status.upper()
    return "FAILED", "MISSING", False, status.upper()


def _json_list(value: object) -> list[Any]:
    if not isinstance(value, str) or not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def _usable_member_count(value: object) -> int:
    """Count finite member values, not placeholder slots."""

    count = 0
    for item in _json_list(value):
        if isinstance(item, dict):
            item = item.get("value_native_unit")
        if item is None or isinstance(item, bool):
            continue
        try:
            numeric = float(item)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            count += 1
    return count


def _effective_expected_members(row: Mapping[str, Any], *, full_ensemble: int = 51) -> int:
    """51 minus lawfully boundary-quarantined members, for a minority-ambiguous row.

    LOW-track members individually nulled by the boundary-quarantine rule
    (extract_open_ens_localday.py:574-580) are a lawful exclusion, not a missing
    observation, once the snapshot-level majority rule
    (ambiguous_member_count < majority threshold) has already decided the day is
    usable -- ``row['boundary_ambiguous']`` is 0 in that case. Those quarantined
    members must not count against the 51-member floor, or a lawful minority
    exclusion reads identically to a genuine ingest gap
    (MISSING_EXPECTED_MEMBERS). A majority-ambiguous row (``boundary_ambiguous``
    == 1) keeps the full expectation: it is embargoed regardless
    (contributes_to_target_extrema=0), so any member shortfall must still
    surface undiminished.
    """
    if int(row.get("boundary_ambiguous") or 0):
        return full_ensemble
    return max(0, full_ensemble - int(row.get("ambiguous_member_count") or 0))


def _run_level_observed_members(
    row: Mapping[str, Any],
    *,
    full_ensemble: int = 51,
) -> int:
    """Normalize lawful member exclusions onto the source-run's fixed scale."""

    interval_bounds = member_interval_bounds_from_row(row)
    if interval_bounds is not None:
        return min(full_ensemble, len(interval_bounds))
    usable = _usable_member_count(row.get("members_json"))
    lawful_exclusions = full_ensemble - _effective_expected_members(
        row,
        full_ensemble=full_ensemble,
    )
    return min(full_ensemble, usable + lawful_exclusions)


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _snapshot_coordinate_manifest_sha(row: dict[str, Any]) -> str:
    raw = row.get("provenance_json")
    if not isinstance(raw, str) or not raw:
        return ""
    try:
        provenance = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(provenance, dict):
        return ""
    return str(provenance.get("manifest_sha256") or "").strip()


def _snapshot_provenance(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("provenance_json")
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        provenance = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return provenance if isinstance(provenance, dict) else {}


def _is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _station_grid_provenance_reason(row: dict[str, Any]) -> str | None:
    from src.contracts.ensemble_snapshot_provenance import split_coordinate_bound_data_version
    from src.data.executable_forecast_reader import grid_surface_evidence_reason

    version = str(row.get("dataset_id") or "")
    parsed = split_coordinate_bound_data_version(version)
    if (parsed[0] if parsed is not None else version) in {
        ECMWF_OPENDATA_HIGH_DATA_VERSION, ECMWF_OPENDATA_LOW_DATA_VERSION,
    }:
        return grid_surface_evidence_reason(row)
    provenance = _snapshot_provenance(row)
    contract = provenance.get("contract_outcome_evidence")
    if not isinstance(contract, dict):
        contract = {}
    source_type = str(
        row.get("settlement_source_type")
        or contract.get("settlement_source_type")
        or ""
    ).strip().lower()
    # Twin of executable_forecast_reader._station_grid_provenance_reason; both
    # families settle off an ICAO airport station and so both owe the proof.
    if source_type not in {"wu_icao", "noaa"}:
        return None
    required = (
        provenance.get("nearest_grid_lat"),
        provenance.get("nearest_grid_lon"),
        provenance.get("nearest_grid_distance_km"),
    )
    if not all(_is_finite_number(value) for value in required):
        return "EXECUTABLE_FORECAST_STATION_GRID_PROVENANCE_MISSING"
    return None


def _snapshot_rows_for_source_run(conn, *, source_run_id: str, data_version: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT * FROM ensemble_snapshots
            WHERE source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
              AND dataset_id = ?
            ORDER BY city, target_date, temperature_metric, snapshot_id
            """,
            (SOURCE_ID, "ensemble_snapshots_db_reader", source_run_id, data_version),
        ).fetchall()
    ]


def _clear_source_run_authority(
    conn,
    *,
    source_run_id: str,
    preserve_existing_authority: bool = False,
) -> dict[str, int]:
    """Clear small derived rows before rebuilding a source_run.

    Snapshot rows are intentionally not pre-deleted. The ingester uses the
    canonical unique key with ``INSERT OR REPLACE``, so pre-deleting the same
    run turns a small deterministic overwrite into a slow table/index rewrite
    on the multi-GB forecasts DB. Residual rows that were not replaced by the
    new JSON set are removed after ingest by fetch_time.

    A target-scoped PARTIAL retry must retain prior verified scopes from the
    same source run.  Its current attempt is evaluated against the newly fetched
    step set, while older scope coverage keeps its own possession clock.  The
    caller therefore skips this destructive derived-row reset (and stale
    snapshot cleanup) for PARTIAL attempts; a later complete retry drains the
    old rows through the normal reset path.
    """

    if preserve_existing_authority:
        return {
            "snapshots_deleted": 0,
            "coverage_deleted": 0,
            "producer_readiness_deleted": 0,
            "source_run_deleted": 0,
        }

    coverage_ids = [
        str(row[0])
        for row in conn.execute(
            "SELECT coverage_id FROM source_run_coverage WHERE source_run_id = ?",
            (source_run_id,),
        ).fetchall()
    ]
    readiness_deleted = conn.execute(
        """
        DELETE FROM readiness_state
        WHERE strategy_key = 'producer_readiness'
          AND source_run_id = ?
        """,
        (source_run_id,),
    ).rowcount
    if coverage_ids:
        readiness_ids = [f"producer_readiness:{coverage_id}" for coverage_id in coverage_ids]
        for readiness_id in readiness_ids:
            readiness_deleted += conn.execute(
                """
            DELETE FROM readiness_state
            WHERE strategy_key = 'producer_readiness'
              AND readiness_id = ?
                """,
                (readiness_id,),
            ).rowcount

    coverage_deleted = conn.execute(
        "DELETE FROM source_run_coverage WHERE source_run_id = ?",
        (source_run_id,),
    ).rowcount
    source_run_deleted = conn.execute(
        "DELETE FROM source_run WHERE source_run_id = ?",
        (source_run_id,),
    ).rowcount
    return {
        "snapshots_deleted": 0,
        "coverage_deleted": int(coverage_deleted),
        "producer_readiness_deleted": int(readiness_deleted),
        "source_run_deleted": int(source_run_deleted),
    }


def _delete_stale_source_run_snapshots(
    conn,
    *,
    source_run_id: str,
    replace_started_at_iso: str,
) -> int:
    """Delete same-run snapshots not refreshed by this ingest attempt."""

    stale_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM ensemble_snapshots
            WHERE source_id = ?
              AND source_transport = ?
              AND source_run_id = ?
              AND fetch_time < ?
            """,
            (
                SOURCE_ID,
                "ensemble_snapshots_db_reader",
                source_run_id,
                replace_started_at_iso,
            ),
        ).fetchone()[0]
    )
    if stale_count <= 0:
        return 0
    deleted = conn.execute(
        """
        DELETE FROM ensemble_snapshots
        WHERE source_id = ?
          AND source_transport = ?
          AND source_run_id = ?
          AND fetch_time < ?
        """,
        (
            SOURCE_ID,
            "ensemble_snapshots_db_reader",
            source_run_id,
            replace_started_at_iso,
        ),
    ).rowcount
    return int(deleted)


def _observed_steps_for_snapshot(
    *,
    required_steps: tuple[int, ...],
    step_horizon_hours: object,
    downloaded_steps: list[int] | None = None,
) -> tuple[int, ...]:
    try:
        horizon = float(step_horizon_hours)
    except (TypeError, ValueError):
        return ()
    horizon_steps = tuple(step for step in required_steps if step <= horizon)
    if downloaded_steps is None:
        return horizon_steps
    downloaded = {int(step) for step in downloaded_steps}
    return tuple(step for step in horizon_steps if step in downloaded)


def _coverage_reason(reason_codes: list[str]) -> str:
    preferred = (
        "TARGET_LOCAL_DAY_BOUNDARY_AMBIGUOUS",
        "MISSING_EXPECTED_MEMBERS",
        "MISSING_REQUIRED_STEPS",
        "EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA",
        "EXECUTABLE_FORECAST_STATION_GRID_PROVENANCE_MISSING",
        "SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH",
        "SOURCE_RUN_PARTIAL",
    )
    for reason in preferred:
        if reason in reason_codes:
            return reason
    return next(
        (reason for reason in reason_codes if reason != "FUTURE_TARGET_DATE_COVERED"),
        "FUTURE_TARGET_DATE_COVERAGE_PARTIAL",
    )


def _merge_source_run_aggregate_preserving_possession(
    conn,
    *,
    existing: Mapping[str, Any],
    source_run_id: str,
    source_run_status: str,
    source_run_completeness: str,
    partial_run: bool,
    observed_steps: list[int],
    expected_steps: list[int],
    expected_members: int,
    observed_members: int,
    expected_count: int,
    observed_count: int,
    valid_time_start: str | None,
    valid_time_end: str | None,
    reason_code: str | None,
) -> tuple[str, str, bool, str | None]:
    """Advance aggregate facts without re-stamping an already-qualified run.

    ``source_run`` is the aggregate identity for one exact source cycle.  A
    retry can add observed steps or upgrade PARTIAL to SUCCESS, but it must not
    rewrite the prior possession clocks merely because an already-qualified
    target was intentionally excluded from this attempt's coverage rebuild.
    """

    try:
        prior_steps_raw = json.loads(str(existing.get("observed_steps_json") or "[]"))
        prior_steps = {
            int(step) for step in prior_steps_raw
            if isinstance(step, (int, float, str)) and str(step).strip()
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        prior_steps = set()
    merged_steps = sorted(prior_steps | {int(step) for step in observed_steps})
    prior_status = str(existing.get("status") or "")
    prior_completeness = str(existing.get("completeness_status") or "")
    if prior_status == "SUCCESS" and prior_completeness == "COMPLETE":
        merged_status = "SUCCESS"
        merged_completeness = "COMPLETE"
        merged_partial = bool(existing.get("partial_run") or 0)
    elif (
        prior_status == "PARTIAL"
        and prior_completeness == "PARTIAL"
        and source_run_status in {"FAILED", "SKIPPED_NOT_RELEASED"}
    ):
        merged_status = prior_status
        merged_completeness = prior_completeness
        merged_partial = bool(existing.get("partial_run") or 0)
    elif source_run_status == "SUCCESS":
        merged_status = "SUCCESS"
        merged_completeness = "COMPLETE"
        merged_partial = False
    else:
        merged_status = source_run_status
        merged_completeness = source_run_completeness
        merged_partial = partial_run
    merged_reason = None if merged_status == "SUCCESS" else (reason_code or existing.get("reason_code"))
    if merged_status not in {"SUCCESS", "FAILED", "PARTIAL", "SKIPPED_NOT_RELEASED"}:
        raise ValueError(f"invalid merged source_run status: {merged_status}")
    if merged_completeness not in {"COMPLETE", "MISSING", "PARTIAL", "NOT_RELEASED"}:
        raise ValueError(f"invalid merged source_run completeness: {merged_completeness}")
    if merged_partial and merged_completeness != "PARTIAL":
        raise ValueError("merged partial_run requires PARTIAL completeness")
    prior_start = existing.get("valid_time_start")
    prior_end = existing.get("valid_time_end")
    starts = [value for value in (prior_start, valid_time_start) if value]
    ends = [value for value in (prior_end, valid_time_end) if value]
    merged_start = min(starts) if starts else None
    merged_end = max(ends) if ends else None
    conn.execute(
        """
        UPDATE source_run
           SET expected_members = ?, observed_members = ?,
               expected_steps_json = ?, observed_steps_json = ?,
               expected_count = ?, observed_count = ?,
               valid_time_start = ?, valid_time_end = ?,
               completeness_status = ?, partial_run = ?,
               status = ?, reason_code = ?
         WHERE source_run_id = ?
        """,
        (
            max(int(existing.get("expected_members") or 0), expected_members),
            max(int(existing.get("observed_members") or 0), observed_members),
            json.dumps(expected_steps, sort_keys=True, separators=(",", ":")),
            json.dumps(merged_steps, sort_keys=True, separators=(",", ":")),
            max(int(existing.get("expected_count") or 0), expected_count),
            max(int(existing.get("observed_count") or 0), observed_count),
            merged_start,
            merged_end,
            merged_completeness,
            1 if merged_partial else 0,
            merged_status,
            merged_reason,
            source_run_id,
        ),
    )
    return merged_status, merged_completeness, merged_partial, merged_reason


def _write_source_authority_chain(
    conn,
    *,
    summary: dict,
    status: str,
    source_run_id: str,
    source_cycle_time: datetime,
    source_release_time: datetime,
    release_calendar_key: str,
    forecast_track: str,
    data_version: str,
    computed_at: datetime,
    fetch_started_at: datetime | None = None,
    fetch_finished_at: datetime | None = None,
    captured_at: datetime | None = None,
    download_observed_steps: list[int] | None = None,
    download_partial_run: bool | None = None,
    download_reason_code: str | None = None,
    attempt_started_at: str | None = None,
    preserve_scopes: set[tuple[str, str, str]] | None = None,
) -> dict[str, int | str | None]:
    """Write source_run + coverage rows for a completed ingest cycle.

    download_observed_steps: when provided (PARTIAL cycles), records the
    source_run-level ground-truth step list from the download phase. Per-target
    coverage still derives observed_steps from each snapshot's local-day
    horizon so the readiness row describes that exact target window.
    """
    rows = _snapshot_rows_for_source_run(
        conn,
        source_run_id=source_run_id,
        data_version=data_version,
    )
    # Keep the complete stored snapshot set for source_run-level facts
    # (manifest, member count, valid-date range, and observed count).  A
    # target-scoped PARTIAL retry may intentionally exclude already-qualified
    # rows from the current coverage rebuild; treating that filtered set as the
    # whole run would erase the prior run-level possession evidence.
    coverage_rows = rows
    if preserve_scopes:
        coverage_rows = [
            row for row in coverage_rows
            if _snapshot_scope_key(row) not in preserve_scopes
        ]
    if download_partial_run and attempt_started_at:
        attempt_start = _parse_utc(attempt_started_at)
        if attempt_start is not None:
            coverage_rows = [
                row
                for row in coverage_rows
                if (_parse_utc(row.get("fetch_time")) or datetime.min.replace(tzinfo=timezone.utc))
                >= attempt_start
            ]
    source_run_status, source_run_completeness, partial_run, reason_code = _source_run_outcome(summary, status)
    snapshot_coordinate_manifest_shas = {
        manifest_sha
        for row in rows
        if (manifest_sha := _snapshot_coordinate_manifest_sha(row))
    }
    source_run_manifest_sha = (
        next(iter(snapshot_coordinate_manifest_shas))
        if len(snapshot_coordinate_manifest_shas) == 1
        else None
    )
    if rows and source_run_manifest_sha is None:
        source_run_status = "FAILED"
        source_run_completeness = "MISSING"
        partial_run = False
        reason_code = (
            "SNAPSHOT_COORDINATE_MANIFEST_SHA_MISSING"
            if not snapshot_coordinate_manifest_shas
            else "SNAPSHOT_COORDINATE_MANIFEST_SHA_MISMATCH"
        )
    # source_run.observed_members is the run-level "did we ingest the ensemble"
    # signal that gates the decision certificate (compiler
    # _validate_forecast_authority_payload reads source_run_completeness_status).
    # It MUST aggregate over only the snapshots that contribute to a target
    # extrema window. Boundary-ambiguous / far-horizon-overflow snapshots are
    # written as all-null placeholders (contributes_to_target_extrema=0,
    # forecast_window_attribution_status=AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY);
    # including them in a min() over ALL rows let a single unfillable Southern-
    # hemisphere / D+5 window zero out observed_members for the entire global
    # run, vetoing every per-city certificate even though the contributing
    # windows each carried the full 51-member ensemble. Per-target member
    # adequacy is still enforced per-scope below (observed_members_for_scope)
    # and again by the executable forecast reader's member floor.
    contributing_rows = [
        row for row in rows if int(row.get("contributes_to_target_extrema") or 0) == 1
    ]
    member_count_rows = contributing_rows if contributing_rows else rows
    observed_member_counts = [
        _run_level_observed_members(row) for row in member_count_rows
    ]
    observed_members = min(observed_member_counts) if observed_member_counts else 0
    if rows and observed_members < 51 and source_run_status == "SUCCESS":
        source_run_status = "PARTIAL"
        source_run_completeness = "PARTIAL"
        partial_run = True
        reason_code = "MISSING_EXPECTED_MEMBERS"
    # PR 6: capture member timing chain fields for DecisionSourceContext.
    # min/max use default="" so empty filtered generators never raise ValueError
    # (all rows could legitimately have NULL source_available_at on degraded ingest).
    _avail_times = [str(row["source_available_at"]) for row in rows if row.get("source_available_at")]
    first_member_observed_time_iso: str = min(_avail_times, default="")
    observed_step_horizons = [
        float(row["step_horizon_hours"])
        for row in rows
        if row.get("step_horizon_hours") is not None
    ]
    # download_observed_steps (from parallel fetch) takes precedence over the
    # ingest-derived approximation: ingest computes steps from step_horizon_hours
    # (a per-row high-water mark), which can overstate when far-horizon steps
    # are absent. The download phase knows exactly which steps were fetched.
    if download_observed_steps is not None:
        observed_steps = list(download_observed_steps)
        if download_partial_run is not None:
            partial_run = partial_run or download_partial_run
            if download_partial_run:
                source_run_completeness = "PARTIAL"
                source_run_status = "PARTIAL"
        if download_reason_code is not None:
            reason_code = download_reason_code
    else:
        observed_steps = [step for step in STEP_HOURS if observed_step_horizons and step <= min(observed_step_horizons)]
    run_complete_time_iso: str = "" if partial_run else max(_avail_times, default="")

    source_run_kwargs = dict(
        source_run_id=source_run_id,
        source_id=SOURCE_ID,
        track=forecast_track,
        release_calendar_key=release_calendar_key,
        source_cycle_time=source_cycle_time,
        source_issue_time=source_cycle_time,
        source_release_time=source_release_time,
        # C1-AVAIL-CLOCK (2026-06-16): source_available_at is PROOF OF POSSESSION = the real
        # authority-write wall-clock (computed_at), routed through the canonical antibody producer.
        # It is NOT source_release_time, which falls back to the raw model cycle (~8h early) and is
        # the safe-fetch GATE, not a publish event — never credited as a nominal availability.
        source_available_at=proof_of_possession_available_at(computed_at),
        # M5-COLLECTION-CLOCK (2026-06-16): each collection-plane instant is the REAL wall-clock at
        # its own code point in collect_open_ens_cycle, NOT computed_at (the model run-init, which
        # fabricated collection latency=0). fetch_started/finished are stamped around the parallel
        # download loop; captured is snapshot_possession_at (taken right before the snapshot write);
        # imported is now() at this persist write. None where the caller could not observe the event.
        fetch_started_at=fetch_started_at,
        fetch_finished_at=fetch_finished_at,
        captured_at=captured_at,
        imported_at=datetime.now(timezone.utc),
        valid_time_start=min((str(row["target_date"]) for row in rows), default=None),
        valid_time_end=max((str(row["target_date"]) for row in rows), default=None),
        data_version=data_version,
        expected_members=51,
        observed_members=observed_members,
        expected_steps_json=STEP_HOURS,
        observed_steps_json=observed_steps,
        expected_count=len(rows),
        observed_count=len(rows),
        completeness_status=source_run_completeness,
        partial_run=partial_run,
        manifest_hash=source_run_manifest_sha,
        status=source_run_status,
        reason_code=reason_code,
    )
    existing_source_run = get_source_run(conn, source_run_id)
    if preserve_scopes and existing_source_run is not None:
        (
            source_run_status,
            source_run_completeness,
            partial_run,
            reason_code,
        ) = _merge_source_run_aggregate_preserving_possession(
            conn,
            existing=existing_source_run,
            source_run_id=source_run_id,
            source_run_status=source_run_status,
            source_run_completeness=source_run_completeness,
            partial_run=partial_run,
            observed_steps=observed_steps,
            expected_steps=STEP_HOURS,
            expected_members=51,
            observed_members=observed_members,
            expected_count=len(rows),
            observed_count=len(rows),
            valid_time_start=min((str(row["target_date"]) for row in rows), default=None),
            valid_time_end=max((str(row["target_date"]) for row in rows), default=None),
            reason_code=reason_code,
        )
    else:
        write_source_run(conn, **source_run_kwargs)

    cities_by_name = runtime_cities_by_name()
    coverage_written = 0
    readiness_written = 0
    # M3 (2026-06-16): expiry anchors to the CYCLE + the calendar's max source lag, never to the
    # write wall-clock. The old computed_at+24h was a guess that re-stamped a fresh TTL on every
    # re-ingest and disagreed with the source's real staleness bound. See _source_cycle_expires_at.
    expires_at = _source_cycle_expires_at(source_cycle_time, forecast_track)
    for row in coverage_rows:
        city = cities_by_name.get(str(row["city"]))
        if city is None:
            logger.warning("ecmwf_open_data authority chain: city not configured: %s", row["city"])
            continue
        target_local_date = date.fromisoformat(str(row["target_date"]))
        scope = build_forecast_target_scope(
            city_id=city.name.upper().replace(" ", "_"),
            city_name=city.name,
            city_timezone=city.timezone,
            target_local_date=target_local_date,
            temperature_metric=str(row["temperature_metric"]),
            source_cycle_time=source_cycle_time,
            data_version=data_version,
        )
        observed_steps_for_scope = _observed_steps_for_snapshot(
            required_steps=scope.required_step_hours,
            step_horizon_hours=row.get("step_horizon_hours"),
            downloaded_steps=download_observed_steps,
        )
        interval_bounds = member_interval_bounds_from_row(row)
        if interval_bounds is None:
            observed_members_for_scope = _usable_member_count(row.get("members_json"))
            expected_members_for_scope = _effective_expected_members(row)
        else:
            # Interval-censored members are observed through their bounds; their
            # point values are lawfully null (leakage law), never missing.
            observed_members_for_scope = len(interval_bounds)
            expected_members_for_scope = 51
        horizon_decision = evaluate_horizon_coverage(
            required_steps=scope.required_step_hours,
            live_max_step_hours=int(float(row.get("step_horizon_hours") or 0)),
        )
        coverage_decision = evaluate_producer_coverage(
            city_id=scope.city_id,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            source_id=SOURCE_ID,
            source_transport="ensemble_snapshots_db_reader",
            source_run_status=source_run_status,
            source_run_completeness=source_run_completeness,
            snapshot_target_date=target_local_date,
            snapshot_metric=str(row["temperature_metric"]),
            expected_steps=scope.required_step_hours,
            observed_steps=observed_steps_for_scope,
            expected_members=expected_members_for_scope,
            observed_members=observed_members_for_scope,
            has_source_linkage=all(
                row.get(field)
                for field in (
                    "source_id",
                    "source_transport",
                    "source_run_id",
                    "release_calendar_key",
                    "source_cycle_time",
                    "source_release_time",
                    "source_available_at",
                )
            ),
        )
        reason_codes = list(
            horizon_decision.reason_codes
            if horizon_decision.status != "LIVE_ELIGIBLE"
            else coverage_decision.reason_codes
        )
        snapshot_window_start = _parse_utc(row.get("local_day_start_utc"))
        if snapshot_window_start != scope.target_window_start_utc:
            reason_codes.append("SNAPSHOT_LOCAL_DAY_WINDOW_MISMATCH")
        attribution_status = str(row.get("forecast_window_attribution_status") or "")
        # One classifier for writer and reader: point, interval and remaining-window
        # rows are current evidence; only the reader's consumer decides which it reads.
        current_evidence = (
            classify_forecast_extrema_authority(row).eligibility
            in CURRENT_EVIDENCE_ELIGIBILITIES
        )
        if not current_evidence:
            if (
                attribution_status == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
                and int(row.get("boundary_ambiguous") or 0) == 1
            ):
                reason_codes.append("TARGET_LOCAL_DAY_BOUNDARY_AMBIGUOUS")
            reason_codes.append("EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA")
        grid_reason = _station_grid_provenance_reason(row)
        if grid_reason is not None:
            reason_codes.append(grid_reason)
        live_eligible = (
            source_run_status in {"SUCCESS", "PARTIAL"}
            and source_run_completeness in {"COMPLETE", "PARTIAL"}
            and horizon_decision.status == "LIVE_ELIGIBLE"
            and coverage_decision.status == "LIVE_ELIGIBLE"
            and snapshot_window_start == scope.target_window_start_utc
            and current_evidence
            and grid_reason is None
        )
        if live_eligible:
            completeness_status = "COMPLETE"
            readiness_status = "LIVE_ELIGIBLE"
            coverage_reason = None
        elif "SOURCE_RUN_HORIZON_OUT_OF_RANGE" in reason_codes:
            completeness_status = "HORIZON_OUT_OF_RANGE"
            readiness_status = "BLOCKED"
            coverage_reason = "SOURCE_RUN_HORIZON_OUT_OF_RANGE"
        else:
            completeness_status = "PARTIAL"
            readiness_status = "BLOCKED"
            coverage_reason = _coverage_reason(reason_codes)

        coverage_id = _stable_id(
            "source_run_coverage",
            source_run_id,
            forecast_track,
            scope.city_id,
            scope.city_timezone,
            scope.target_local_date.isoformat(),
            scope.temperature_metric,
            data_version,
        )
        write_source_run_coverage(
            conn,
            coverage_id=coverage_id,
            source_run_id=source_run_id,
            source_id=SOURCE_ID,
            source_transport="ensemble_snapshots_db_reader",
            release_calendar_key=release_calendar_key,
            track=forecast_track,
            city_id=scope.city_id,
            city=scope.city_name,
            city_timezone=scope.city_timezone,
            target_local_date=scope.target_local_date,
            temperature_metric=scope.temperature_metric,
            physical_quantity=str(row["physical_quantity"]),
            observation_field=str(row["observation_field"]),
            data_version=data_version,
            expected_members=expected_members_for_scope,
            observed_members=observed_members_for_scope,
            expected_steps_json=scope.required_step_hours,
            observed_steps_json=observed_steps_for_scope,
            snapshot_ids_json=[int(row["snapshot_id"])],
            target_window_start_utc=scope.target_window_start_utc,
            target_window_end_utc=scope.target_window_end_utc,
            completeness_status=completeness_status,
            readiness_status=readiness_status,
            reason_code=coverage_reason,
            computed_at=computed_at,
            expires_at=expires_at if readiness_status == "LIVE_ELIGIBLE" else None,
        )
        coverage_written += 1
        build_producer_readiness_for_scope(
            conn,
            scope=scope,
            source_id=SOURCE_ID,
            source_transport="ensemble_snapshots_db_reader",
            track=forecast_track,
            computed_at=computed_at,
            release_calendar_key=release_calendar_key,
        )
        readiness_written += 1

    return {
        "source_run_status": source_run_status,
        "source_run_completeness": source_run_completeness,
        "coverage_written": coverage_written,
        "producer_readiness_written": readiness_written,
        "first_member_observed_time": first_member_observed_time_iso,
        "run_complete_time": run_complete_time_iso,
    }


def _fetch_one_step(
    *,
    cycle_date: date,
    cycle_hour: int,
    param: str,
    step: int,
    output_dir: Path,
    mirrors: tuple[str, ...],
    _deadline: float | None = None,
) -> tuple[str, Any]:
    """Fetch a single step for one param into a per-step canonical file.

    Returns (status, detail) where status is one of:
      "OK"           — file written and atomic-renamed; detail = Path
      "NOT_RELEASED" — every configured mirror reports a definite 404 or
                       missing-index for this step; detail = None
      "FAILED"       — retry budget exhausted; detail = error string

    Per-step file naming uses param to avoid cross-track collision when
    mx2t6_high (param=mx2t3) and mn2t6_low (param=mn2t3) run concurrently
    (src/ingest_main.py:1133-1142, minute=30 vs minute=35; worst-case
    2 × _DOWNLOAD_MAX_WORKERS in flight on the same output_dir).

    Single-writer antibody: NO SQLite writes in this function — HTTP only.
    All DB writes occur on the main thread after all futures complete.
    """
    deadline = min(
        time.monotonic() + float(_PER_STEP_TIMEOUT_SECONDS),
        _deadline if _deadline is not None else float("inf"),
    )
    if time.monotonic() >= deadline:
        return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
    canonical = _step_cache_path(
        output_dir,
        run_date=cycle_date,
        run_hour=cycle_hour,
        step=step,
        param=param,
    )
    partial   = canonical.with_suffix(".grib2.partial")
    if canonical.exists() and canonical.stat().st_size > 0:
        return ("OK", canonical)   # resume: already fetched in a prior attempt

    from ecmwf.opendata import Client  # imported here: conda env only on main interpreter

    last_err: str | None = None
    definitive_missing_mirrors = 0
    had_mirror_failure = False
    for mirror in mirrors:
        mirror_missing = False
        for attempt in range(_PER_STEP_MAX_RETRIES):
            if time.monotonic() >= deadline:
                return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
            try:
                client = Client(source=mirror)
                source_base = getattr(client, "url", None)
                pf_partial = _source_partial_path(partial, kind="pf", source=mirror)
                cf_partial = _source_partial_path(partial, kind="cf", source=mirror)
                _adopt_legacy_checkpoint(
                    partial.with_suffix(".pf.partial"),
                    pf_partial,
                    source_base=source_base,
                )
                _adopt_legacy_checkpoint(
                    partial.with_suffix(".cf.partial"),
                    cf_partial,
                    source_base=source_base,
                )
                _retrieve_step_with_controlled_ranges(
                    client,
                    date=int(cycle_date.strftime("%Y%m%d")),
                    time=cycle_hour,
                    stream="enfo",
                    type=["pf"],
                    step=[step],
                    param=[param],
                    target=pf_partial,
                    _deadline=deadline,
                    _cycle_deadline_enforced=_deadline is not None,
                )
                try:
                    _retrieve_step_with_controlled_ranges(
                        client,
                        date=int(cycle_date.strftime("%Y%m%d")),
                        time=cycle_hour,
                        stream="enfo",
                        type=["cf"],
                        step=[step],
                        param=[param],
                        target=cf_partial,
                        _deadline=deadline,
                        _cycle_deadline_enforced=_deadline is not None,
                    )
                except ValueError as exc:
                    if "Cannot find index entries matching" not in str(exc):
                        raise
                    _retrieve_step_with_controlled_ranges(
                        client,
                        date=int(cycle_date.strftime("%Y%m%d")),
                        time=cycle_hour,
                        stream="oper",
                        type=["fc"],
                        step=[step],
                        param=[param],
                        target=cf_partial,
                        _deadline=deadline,
                        _cycle_deadline_enforced=_deadline is not None,
                    )
                with partial.open("wb") as out:
                    out.write(cf_partial.read_bytes())
                    out.write(pf_partial.read_bytes())
                os.replace(str(partial), str(canonical))   # atomic rename
                pf_partial.unlink(missing_ok=True)
                cf_partial.unlink(missing_ok=True)
                # The range-resume manifests must go with their partials. Only
                # _reset_range_resume (checksum/ETag mismatch mid-retry) and the
                # weak-ETag discard path unlinked these, never the success path, so
                # every completed step left one behind — and because
                # _raw_file_identity recognises neither `.partial` nor
                # `.ranges.json`, the retention plan can never reach them at any
                # age. Measured 2026-09-18: 350 sidecar files regrew within a single
                # cycle after a manual sweep. Unbounded in file count.
                _range_resume_manifest_path(pf_partial).unlink(missing_ok=True)
                _range_resume_manifest_path(cf_partial).unlink(missing_ok=True)
                _range_resume_manifest_path(partial).unlink(missing_ok=True)
                return ("OK", canonical)
            except requests.HTTPError as exc:
                code = getattr(exc.response, "status_code", None)
                if code == 404:
                    # A 404 is definitive only for this mirror.  Replicas can
                    # publish at different times, so continue with the next
                    # configured source before classifying the step absent.
                    mirror_missing = True
                    break
                if code in _RETRYABLE_HTTP:
                    had_mirror_failure = True
                    last_err = f"HTTP_{code}_mirror_{mirror}_attempt_{attempt}"
                    if attempt + 1 < _PER_STEP_MAX_RETRIES and not _sleep_step_retry(deadline):
                        return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
                    continue
                had_mirror_failure = True
                last_err = f"HTTP_{code}_mirror_{mirror}"
                break   # non-retryable; try next mirror
            except (requests.ConnectionError, requests.Timeout) as exc:
                had_mirror_failure = True
                last_err = f"NET_{type(exc).__name__}_mirror_{mirror}_attempt_{attempt}"
                if time.monotonic() >= deadline:
                    return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
                if attempt + 1 < _PER_STEP_MAX_RETRIES and not _sleep_step_retry(deadline):
                    return ("FAILED", _deadline_failure_reason(cycle_deadline=_deadline))
                continue
            except OSError as exc:
                # disk/path errors during atomic rename or partial-file write
                had_mirror_failure = True
                last_err = f"OS_{type(exc).__name__}_mirror_{mirror}"
                break   # unexpected at filesystem layer; try next mirror
            except ValueError as exc:
                # SDK raises ValueError("Cannot find index entries matching ...")
                # when the requested step is absent from the .index file
                # (step not yet published). This is definitive only for the
                # current mirror; multiurl resolves the index BEFORE the
                # byte-range GET, so a missing step manifests as ValueError,
                # not HTTPError. Continue with the next configured mirror.
                if "Cannot find index entries matching" in str(exc):
                    mirror_missing = True
                    break
                raise   # Unknown ValueError — propagate
            # ImportError, AttributeError, TypeError, etc. propagate to the
            # ThreadPoolExecutor future; main thread surfaces them in logs.
            # Antibody 2026-05-11: silent-swallow of ModuleNotFoundError caused
            # post-deploy 23ms-fast-fail with no traceback.
        if mirror_missing:
            definitive_missing_mirrors += 1

    if mirrors and definitive_missing_mirrors == len(mirrors) and not had_mirror_failure:
        return ("NOT_RELEASED", None)
    return ("FAILED", last_err or "EXHAUSTED")


def _concat_steps(
    ok_steps: list[int],
    param: str,
    output_dir: Path,
    output_path: Path,
    *,
    run_date: date,
    run_hour: int,
) -> None:
    """Concatenate per-step GRIB2 files into the canonical output_path.

    GRIB2 is self-delimiting; step order does not affect extractor correctness
    (REL-1, REL-6). We write in ascending step order for determinism.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as out:
        for step in sorted(ok_steps):
            step_file = _step_cache_path(
                output_dir,
                run_date=run_date,
                run_hour=run_hour,
                step=step,
                param=param,
            )
            if step_file.exists():
                out.write(step_file.read_bytes())


def _subprocess_env_with_script_dir(args: list[str]) -> dict:
    """Child env that injects the launched .py script's own directory FIRST on
    PYTHONPATH, so sibling-module imports resolve even when the parent process
    sets PYTHONSAFEPATH=1 (Python 3.11+ then suppresses the default script-dir
    injection into sys.path[0]).

    Antibody (2026-06-22): the forecast-live launchd plist sets PYTHONSAFEPATH=1,
    which broke `from tigge_local_calendar_day_common import ...` inside the
    extract subprocess → ecmwf extraction rc=1 → fusion capture failed → 12h of
    zero posteriors → stale belief → blind exits. See
    tests/test_ecmwf_open_data_subprocess_hardening.py.
    """
    import os as _os

    env = dict(_os.environ)
    script = next(
        (a for a in args[1:] if isinstance(a, str) and a.endswith(".py")), None
    )
    if script:
        script_dir = _os.path.dirname(_os.path.abspath(script))
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            script_dir + (_os.pathsep + existing if existing else "")
        )
    return env


def _run_subprocess(args: list[str], *, label: str, timeout: int) -> dict:
    logger.info("ecmwf_open_data %s: %s", label, " ".join(args[:6]) + " ...")
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_subprocess_env_with_script_dir(args),
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("ecmwf_open_data %s: TIMEOUT after %ds", label, timeout)
        partial_stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return {"label": label, "ok": False, "error": f"timeout after {timeout}s",
                "stderr_tail": partial_stderr[-4096:]}
    except FileNotFoundError as exc:
        return {"label": label, "ok": False, "error": f"script not found: {exc}",
                "stderr_tail": ""}
    stderr_full = result.stderr or ""
    if result.returncode != 0:
        logger.warning("ecmwf_open_data %s: rc=%d stderr_tail=%s",
                       label, result.returncode, stderr_full[-4096:])
    return {
        "label": label,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout_tail": (result.stdout or "")[-4096:],
        "stderr_tail": stderr_full[-4096:],
    }


def _write_stderr_dump(dump_path: Path, stderr: str) -> None:
    """Write stderr tail (up to 4096 chars) to a postmortem file under tmp/. Silently no-ops on error."""
    try:
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(stderr, encoding="utf-8")
        logger.info("ecmwf_open_data: stderr dump written to %s", dump_path)
    except OSError as exc:
        logger.warning("ecmwf_open_data: could not write stderr dump to %s: %s", dump_path, exc)


def _source_possession_clock(
    now_utc: datetime | None, fetched_at: datetime | None, *, extracted: bool,
) -> datetime:
    """Never predate a real decoded source with the daemon's cycle-start clock."""
    candidates = [(now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)]
    if fetched_at is not None:
        candidates.append(fetched_at.astimezone(timezone.utc))
    if extracted:
        candidates.append(datetime.now(timezone.utc))
    return max(candidates)


def collect_open_ens_cycle(
    *,
    track: str = "mx2t6_high",
    run_date: Optional[date] = None,
    run_hour: Optional[int] = None,
    download_timeout_seconds: int = 1500,  # kept for API compat; parallel fetch uses _PER_STEP_TIMEOUT_SECONDS
    extract_timeout_seconds: int = 900,
    skip_download: bool = False,
    skip_extract: bool = False,
    conn=None,
    _runner=None,
    _fetch_impl=None,  # test seam: replaces _fetch_one_step; callable with same signature
    _mask_fetch_impl=None,  # test seam: exact indexed static LSM acquisition
    grid_surface_source_evidence: dict[str, object] | None = None,  # skip_extract test seam only
    _paths: OpenDataPaths | None = None,
    now_utc: datetime | None = None,
    coordinate_manifest_json: str | None = None,
    cycle_deadline_monotonic: float | None = None,
) -> dict:
    """Download + extract + ingest one Open Data ENS run for one track.

    Parameters
    ----------
    track : "mx2t6_high" | "mn2t6_low"
        Which physical-quantity track to fetch. The daemon calls this twice
        per cycle (once per track) so each track has independent failure
        semantics.
    run_date / run_hour :
        Optional override of the auto-selected run. Used for boot-time
        catch-up.
    skip_download / skip_extract :
        Test seams. The daemon never sets these.
    conn :
        Optional pre-opened world DB connection. Tests pass an in-memory
        sqlite connection.
    _runner :
        Test seam to swap subprocess execution.
    """
    if track not in TRACKS:
        raise ValueError(f"Unknown track {track!r}; expected one of {sorted(TRACKS)}")
    if grid_surface_source_evidence is not None and not skip_extract:
        raise ValueError("ENS_LAND_MASK_SOURCE_OVERRIDE_REQUIRES_SKIP_EXTRACT")
    cfg = dict(TRACKS[track])
    manifest_json = (
        runtime_coordinate_manifest_json()
        if coordinate_manifest_json is None else coordinate_manifest_json
    )
    manifest_sha = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    cfg["data_version"] = coordinate_bound_data_version(cfg["data_version"], manifest_sha)
    runner = _runner or _run_subprocess

    source_spec = gate_source(SOURCE_ID)
    gate_source_role(source_spec, FORECAST_SOURCE_ROLE)

    now = now_utc or datetime.now(timezone.utc)
    manual_cycle_override = run_date is not None or run_hour is not None
    selection_metadata: dict[str, object] = {}
    if run_date is None or run_hour is None:
        selection, selection_metadata = _select_cycle_for_track(track=track, now_utc=now)
        if selection is not FetchDecision.FETCH_ALLOWED:
            return {
                "status": selection.value.lower(),
                "track": track,
                "data_version": cfg["data_version"],
                "source_id": SOURCE_ID,
                "forecast_source_role": FORECAST_SOURCE_ROLE,
                "selection": selection_metadata,
                "stages": [],
                "snapshots_inserted": 0,
            }
        selected_cycle = selection_metadata["selected_cycle_time"]
        if not isinstance(selected_cycle, datetime):
            raise TypeError("release calendar selected_cycle_time must be datetime")
        cycle_date, cycle_hour = selected_cycle.date(), selected_cycle.hour
    else:
        cycle_date, cycle_hour = run_date, run_hour
    if run_date is not None:
        cycle_date = run_date
    if run_hour is not None:
        cycle_hour = run_hour
    source_cycle_time = datetime.combine(cycle_date, datetime.min.time(), tzinfo=timezone.utc).replace(hour=cycle_hour)
    horizon_profile = _horizon_profile_for_cycle(
        cycle_hour=cycle_hour,
        selection_metadata=selection_metadata,
        manual_cycle_override=manual_cycle_override,
    )
    forecast_track = _forecast_track_for_profile(
        ingest_track=cfg["ingest_track"],
        horizon_profile=horizon_profile,
    )
    source_release_time = selection_metadata.get("next_safe_fetch_at")
    if not isinstance(source_release_time, datetime):
        source_release_time = source_cycle_time
    source_run_id = (
        f"{SOURCE_ID}:{track}:{cycle_date.isoformat()}T{cycle_hour:02d}Z"
        f":coordsha:{manifest_sha}"
        # HIGH now carries native inner/boundary evidence.  Keep its source-run
        # identity disjoint from the pre-boundary producer so a new run cannot
        # clear or overwrite rows that were materialized under the old law.
        f"{opendata_source_run_revision_suffix(cfg['data_version'])}"
    )
    release_calendar_key = f"{SOURCE_ID}:{track}:{horizon_profile}"

    paths = _paths or _resolve_opendata_paths()
    coordinate_raw_root = paths.raw_root / "raw" / "coordinate_manifests" / manifest_sha
    output_path = _download_output_path(
        run_date=cycle_date,
        run_hour=cycle_hour,
        param=cfg["open_data_param"],
        raw_root=paths.raw_root,
    )
    stages: list[dict] = []

    trusted_surface = grid_surface_source_evidence
    if not skip_extract:
        # The producer is versioned in this repository.  The coordinate
        # manifest is generated from the live runtime city config immediately
        # below and supplied as an explicit subprocess argument, so a stale
        # external manifest is not a required asset.
        missing_extract_assets = [
            str(paths.extract_script)
            for path in (paths.extract_script,)
            if not path.is_file()
        ]
        if missing_extract_assets:
            reason = "MISSING_EXTRACT_ASSETS:" + ",".join(missing_extract_assets)
            stage = {
                "label": f"extract_assets_preflight_{track}",
                "ok": False,
                "status": "MISSING_EXTRACT_ASSETS",
                "stderr_tail": reason,
            }
            logger.error("ecmwf_open_data: %s", reason)
            return {
                "status": "extract_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "reason": reason,
                "stages": [stage],
                "snapshots_inserted": 0,
            }
        if paths.asset_root != paths.raw_root:
            logger.info(
                "ecmwf_open_data: path_bundle origin=%s raw_root=%s asset_root=%s",
                paths.origin,
                paths.raw_root,
                paths.asset_root,
            )
        coordinate_manifest = _write_runtime_coordinate_manifest(
            paths.raw_root, manifest_json=manifest_json,
        )

    # download_observed_steps / _partial_cycle track which steps were actually
    # fetched so _write_source_authority_chain can set the authoritative
    # observed_steps_json and partial_run flag on the source_run row.
    download_observed_steps: list[int] | None = None
    _partial_cycle: bool = False
    _download_reason_code: str | None = None

    # M5-COLLECTION-CLOCK (2026-06-16): real fetch-plane instants. Stamped at the actual code
    # points around the parallel download loop below — left None on the skip_download test seam
    # (no HTTP issued, so no real fetch instant to record).
    _fetch_started_at: datetime | None = None
    _fetch_finished_at: datetime | None = None

    if not skip_download:
        fetch_fn = _fetch_impl or _fetch_one_step
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Keep only the configured number of steps outstanding. Refill a free
        # slot immediately: a slow step must not idle the rest of its wave.
        # Workers own HTTP deadlines; the collecting thread consumes every
        # result before any SQLite write, without a second timeout.
        tasks = [(s, cfg["open_data_param"]) for s in STEP_HOURS]
        results: dict[int, tuple[str, Any]] = {}
        deadline_exceeded = False
        # M5-COLLECTION-CLOCK: fetch_started = the real wall-clock immediately before the first
        # HTTP GET is dispatched (the batch loop below submits fetch_fn → session.get).
        _fetch_started_at = datetime.now(timezone.utc)
        remaining_tasks = iter(tasks)
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_MAX_WORKERS) as ex:
            def submit_step(task):
                step, param = task
                if cycle_deadline_monotonic is not None and time.monotonic() >= cycle_deadline_monotonic:
                    return None
                kwargs = {
                    "cycle_date": cycle_date,
                    "cycle_hour": cycle_hour,
                    "param": param,
                    "step": step,
                    "output_dir": output_dir,
                    "mirrors": _DOWNLOAD_SOURCES,
                }
                if _fetch_impl is None:
                    kwargs["_deadline"] = cycle_deadline_monotonic
                return ex.submit(
                    fetch_fn,
                    **kwargs,
                )

            pending = {}
            for _ in range(min(_DOWNLOAD_MAX_WORKERS, len(tasks))):
                task = next(remaining_tasks)
                future = submit_step(task)
                if future is None:
                    deadline_exceeded = True
                    results[task[0]] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")
                else:
                    pending[future] = task[0]
            while pending:
                timeout = None
                if cycle_deadline_monotonic is not None:
                    timeout = max(0.0, cycle_deadline_monotonic - time.monotonic())
                completed, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                if not completed:
                    deadline_exceeded = True
                    for future, step in pending.items():
                        future.cancel()
                        results[step] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")
                    break
                for fut in completed:
                    step = pending.pop(fut)
                    try:
                        results[step] = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        results[step] = ("FAILED", f"UNCAUGHT_{type(exc).__name__}: {exc}")
                    task = next(remaining_tasks, None)
                    if task is not None:
                        future = submit_step(task)
                        if future is None:
                            deadline_exceeded = True
                            results[task[0]] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")
                        else:
                            pending[future] = task[0]

            if deadline_exceeded:
                for task in remaining_tasks:
                    results[task[0]] = ("FAILED", "CYCLE_DEADLINE_EXCEEDED")

        # M5-COLLECTION-CLOCK: fetch_finished = the real wall-clock once every batch future has
        # resolved (bytes received, timed out, or failed) — the moment the download phase ends.
        _fetch_finished_at = datetime.now(timezone.utc)

        ok_steps       = sorted(s for s, (st, _) in results.items() if st == "OK")
        released_404   = sorted(s for s, (st, _) in results.items() if st == "NOT_RELEASED")
        failed_steps   = sorted(s for s, (st, _) in results.items() if st == "FAILED")

        logger.info(
            "ecmwf_open_data parallel_fetch %s: ok=%d not_released=%d failed=%d mirror_first_try=aws",
            track, len(ok_steps), len(released_404), len(failed_steps),
        )

        # --- Early-return branches: FAILED and pure-NOT_RELEASED only ---
        # A transport failure is fatal when there is no validated step to ingest
        # (or when the hard cycle deadline fired).  With at least one validated
        # step, however, retain the failure as PARTIAL evidence and let the
        # existing target-scoped coverage gate decide which local days qualify.
        # This keeps a remote 503 from being relabelled NOT_RELEASED while
        # avoiding a false whole-cycle SUCCESS.

        if failed_steps and (not ok_steps or deadline_exceeded):
            reason_parts = [
                f"step{s}:{results[s][1]}" for s in failed_steps[:5]
            ]
            if released_404:
                reason_parts.append(f"NOT_RELEASED_STEPS={released_404}")
            reason = ";".join(reason_parts)
            _write_stderr_dump(
                PROJECT_ROOT / "tmp"
                / f"ecmwf_open_data_{cycle_date.isoformat()}_{cycle_hour:02d}z_{track}.stderr.txt",
                reason,
            )
            # Write source_run FAILED row directly (no ingest will run).
            computed_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
            try:
                _sr_conn = conn
                _sr_own = _sr_conn is None
                if _sr_own:
                    from src.state.db import get_forecasts_connection as _gfc
                    _sr_conn = _gfc()
                _sr_lock = (
                    db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.BULK)
                    if _sr_own else None
                )
                with (_sr_lock if _sr_lock is not None else nullcontext()):
                    if _qualified_partial_retry_scopes(
                        _sr_conn,
                        source_run_id=source_run_id,
                    ):
                        logger.info(
                            "ecmwf_open_data: preserving qualified source_run on failed retry "
                            "source_run_id=%s",
                            source_run_id,
                        )
                    else:
                        write_source_run(
                            _sr_conn,
                            source_run_id=source_run_id,
                            source_id=SOURCE_ID,
                            track=forecast_track,
                            release_calendar_key=release_calendar_key,
                            source_cycle_time=source_cycle_time,
                            source_issue_time=source_cycle_time,
                            source_release_time=source_release_time,
                            # C1-AVAIL-CLOCK (2026-06-16): proof of possession = computed_at (the real
                            # wall-clock), via the canonical producer — never the cycle-fallback
                            # source_release_time (the safe-fetch gate, not a publish event).
                            source_available_at=proof_of_possession_available_at(computed_at),
                            # M5-COLLECTION-CLOCK (2026-06-16): the download WAS attempted on this branch,
                            # so fetch_started/finished are real (stamped around the loop above). No decode
                            # ran and no forecast data was persisted (this is a FAILED-status row only), so
                            # captured_at / imported_at are honestly NULL — never re-stamped with computed_at.
                            fetch_started_at=_fetch_started_at,
                            fetch_finished_at=_fetch_finished_at,
                            captured_at=None,
                            imported_at=None,
                            data_version=cfg["data_version"],
                            expected_members=51,
                            observed_members=0,
                            expected_steps_json=STEP_HOURS,
                            observed_steps_json=ok_steps,
                            expected_count=0,
                            observed_count=0,
                            completeness_status="MISSING",
                            partial_run=False,
                            status="FAILED",
                            reason_code=reason[:500],
                        )
                    if _sr_own:
                        _sr_conn.commit()
                        _sr_conn.close()
            except Exception as _sr_exc:  # noqa: BLE001
                logger.warning("ecmwf_open_data: could not write FAILED source_run: %s", _sr_exc)
            stages.append({
                "label": f"download_parallel_{track}",
                "ok": False,
                "status": "FAILED",
                "ok_steps": ok_steps,
                "failed_steps": failed_steps,
                "not_released_steps": released_404,
            })
            return {
                "status": "download_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "reason": "CYCLE_DEADLINE_EXCEEDED" if deadline_exceeded else reason,
                "stages": stages,
                "snapshots_inserted": 0,
            }

        if not ok_steps and released_404:
            # Pure NOT_RELEASED: no usable steps at all.
            reason = f"NOT_RELEASED_STEPS={released_404}"
            computed_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
            try:
                _sr_conn = conn
                _sr_own = _sr_conn is None
                if _sr_own:
                    from src.state.db import get_forecasts_connection as _gfc
                    _sr_conn = _gfc()
                _sr_lock = (
                    db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.BULK)
                    if _sr_own else None
                )
                with (_sr_lock if _sr_lock is not None else nullcontext()):
                    if _qualified_partial_retry_scopes(
                        _sr_conn,
                        source_run_id=source_run_id,
                    ):
                        logger.info(
                            "ecmwf_open_data: preserving qualified source_run on not-released retry "
                            "source_run_id=%s",
                            source_run_id,
                        )
                    else:
                        write_source_run(
                            _sr_conn,
                            source_run_id=source_run_id,
                            source_id=SOURCE_ID,
                            track=forecast_track,
                            release_calendar_key=release_calendar_key,
                            source_cycle_time=source_cycle_time,
                            source_issue_time=source_cycle_time,
                            source_release_time=source_release_time,
                            # C1-AVAIL-CLOCK (2026-06-16): proof of possession = computed_at (the real
                            # wall-clock), via the canonical producer — never the cycle-fallback
                            # source_release_time (the safe-fetch gate, not a publish event).
                            source_available_at=proof_of_possession_available_at(computed_at),
                            # M5-COLLECTION-CLOCK (2026-06-16): the download WAS attempted, so fetch_started/
                            # finished are real (stamped around the loop above). Nothing was released, so no
                            # decode ran and no forecast data was persisted — captured_at / imported_at are
                            # honestly NULL rather than re-stamped with computed_at.
                            fetch_started_at=_fetch_started_at,
                            fetch_finished_at=_fetch_finished_at,
                            captured_at=None,
                            imported_at=None,
                            data_version=cfg["data_version"],
                            expected_members=51,
                            observed_members=0,
                            expected_steps_json=STEP_HOURS,
                            observed_steps_json=[],
                            expected_count=0,
                            observed_count=0,
                            completeness_status="NOT_RELEASED",
                            partial_run=False,
                            status="SKIPPED_NOT_RELEASED",
                            reason_code=reason[:500],
                        )
                    if _sr_own:
                        _sr_conn.commit()
                        _sr_conn.close()
            except Exception as _sr_exc:  # noqa: BLE001
                logger.warning("ecmwf_open_data: could not write SKIPPED_NOT_RELEASED source_run: %s", _sr_exc)
            stages.append({
                "label": f"download_parallel_{track}",
                "ok": False,
                "status": "SKIPPED_NOT_RELEASED",
                "ok_steps": [],
                "failed_steps": [],
                "not_released_steps": released_404,
            })
            return {
                "status": "skipped_not_released",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

        # SUCCESS (no released_404/failed) OR PARTIAL (some OK plus a 404 and/or
        # transport failure).  Both fall through to extract+ingest.
        # _write_source_authority_chain receives download_observed_steps and the
        # partial flag so the canonical source_run remains PARTIAL.
        _partial_cycle = bool(released_404 or failed_steps)
        partial_reasons: list[str] = []
        if failed_steps:
            failed_detail = ";".join(
                f"step{s}:{results[s][1]}" for s in failed_steps
            )
            partial_reasons.append(f"FAILED_STEPS={failed_detail}")
        if released_404:
            partial_reasons.append(f"NOT_RELEASED_STEPS={released_404}")
        _download_reason_code = ";".join(partial_reasons) if partial_reasons else None
        download_observed_steps = ok_steps

        # Concat per-step files into the canonical output_path for the extractor.
        if (
            cycle_deadline_monotonic is not None
            and time.monotonic() >= cycle_deadline_monotonic
        ):
            stages.append({
                "label": f"concat_{track}",
                "ok": False,
                "status": "CYCLE_DEADLINE_EXCEEDED",
            })
            return {
                "status": "download_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "reason": "CYCLE_DEADLINE_EXCEEDED",
                "stages": stages,
                "snapshots_inserted": 0,
            }
        _concat_steps(
            ok_steps,
            cfg["open_data_param"],
            output_dir,
            output_path,
            run_date=cycle_date,
            run_hour=cycle_hour,
        )

        stages.append({
            "label": f"download_parallel_{track}",
            "ok": True,
            "status": "PARTIAL" if _partial_cycle else "SUCCESS",
            "ok_steps": ok_steps,
            "failed_steps": failed_steps,
            "not_released_steps": released_404,
        })

    if not skip_extract:
        mask_path = output_path.with_name(
            f".{track}_{cycle_date:%Y%m%d}_{cycle_hour:02d}z_lsm.grib2"
        )
        try:
            mask_source = (_mask_fetch_impl or _fetch_cycle_land_mask)(
                cycle_date=cycle_date,
                cycle_hour=cycle_hour,
                output_path=mask_path,
                deadline=cycle_deadline_monotonic,
            )
            trusted_surface = {
                "mask_source": mask_source["source"],
                "mask_source_url": mask_source["source_url"],
                "mask_source_index_url": mask_source["source_index_url"],
                "mask_source_cycle_time": mask_source["source_cycle_time"],
                "mask_source_fetched_at": mask_source["source_fetched_at"],
                "mask_source_index_offset": mask_source["source_index_offset"],
                "mask_source_index_length": mask_source["source_index_length"],
                "mask_sha256": mask_source["mask_sha256"],
                "mask_grid_identity_hash": mask_source["mask_grid_identity_hash"],
            }
        except (OSError, ValueError, KeyError, TypeError, requests.RequestException) as exc:
            stages.append({"label": f"land_mask_{track}", "ok": False,
                           "status": "ENS_LAND_MASK_UNAVAILABLE", "reason": str(exc)[:300]})
            return {"status": "extract_failed", "track": track,
                    "data_version": cfg["data_version"],
                    "reason": "ENS_LAND_MASK_UNAVAILABLE", "stages": stages,
                    "snapshots_inserted": 0}
        _fetch_finished_at = datetime.now(timezone.utc)
        # Freeze the mandatory forecast fetch clock before optional source work.
        # The existing extractor phase owns its full timeout first. Optional
        # bytes may spend only headroom beyond it, never the cycle's last slice.
        surface_deadline = (
            cycle_deadline_monotonic - float(extract_timeout_seconds)
            if cycle_deadline_monotonic is not None else None
        )
        if surface_deadline is not None and time.monotonic() >= surface_deadline:
            surface_evidence = {"capture_status": "UNKNOWN", "unavailable_reason": "SURFACE_AUDIT_NO_PREDICTION_HEADROOM"}
        else:
            surface_evidence = _fetch_cycle_surface_evidence(
                cycle=source_cycle_time, mask_path=mask_path, deadline=surface_deadline,
            )
        stages.append({"label": f"surface_source_{track}", **surface_evidence})
        surface_args = []
        if surface_evidence["capture_status"] == "OBSERVED":
            surface_path = mask_path.with_suffix(".z.grib2")
            surface_args = ["--surface-geopotential-grib-path", str(surface_path),
                            "--surface-geopotential-proof-path", str(surface_path.with_suffix(".proof.json"))]
        if cycle_deadline_monotonic is not None:
            remaining = cycle_deadline_monotonic - time.monotonic()
            if remaining <= 0:
                return {
                    "status": "extract_failed",
                    "track": track,
                    "data_version": cfg["data_version"],
                    "reason": "CYCLE_DEADLINE_EXCEEDED",
                    "stages": stages,
                    "snapshots_inserted": 0,
                }
            extract_timeout_seconds = min(float(extract_timeout_seconds), remaining)
        extract = runner(
            [
                _conda_python(),
                str(paths.extract_script),
                "--grib-path", str(output_path),
                "--mask-grib-path", str(mask_path),
                "--mask-proof-path", str(mask_path.with_suffix(".proof.json")),
                *surface_args,
                "--track", cfg["ingest_track"],
                "--output-root", str(coordinate_raw_root),
                "--manifest-path", str(coordinate_manifest),
            ],
            label=f"extract_{track}",
            timeout=extract_timeout_seconds,
        )
        extract["coordinate_manifest_path"] = str(coordinate_manifest)
        stages.append(extract)
        if not extract["ok"]:
            _write_stderr_dump(
                PROJECT_ROOT / "tmp"
                / f"ecmwf_open_data_{cycle_date.isoformat()}_{cycle_hour:02d}z_{track}.extract_stderr.txt",
                extract.get("stderr_tail", ""),
            )
            return {
                "status": "extract_failed",
                "track": track,
                "data_version": cfg["data_version"],
                "stages": stages,
                "snapshots_inserted": 0,
            }

    # Ingest stage — import in-process, share a single connection so the
    # caller's test fixture (in-memory sqlite) is honored. Production
    # caller passes ``conn=None`` and we open the forecasts DB (K1 split).
    if (
        cycle_deadline_monotonic is not None
        and time.monotonic() >= cycle_deadline_monotonic
    ):
        return {
            "status": "extract_failed",
            "track": track,
            "data_version": cfg["data_version"],
            "reason": "CYCLE_DEADLINE_EXCEEDED",
            "stages": stages,
            "snapshots_inserted": 0,
        }
    own_conn = conn is None
    if own_conn:
        _lock_ctx = db_writer_lock(ZEUS_FORECASTS_DB_PATH, WriteClass.BULK)
    else:
        # Injected connection (test seam with in-memory sqlite) — skip file lock.
        _lock_ctx = nullcontext()
    cleared_authority = {
        "snapshots_deleted": 0,
        "coverage_deleted": 0,
        "producer_readiness_deleted": 0,
        "source_run_deleted": 0,
    }
    retention_plan: _RawRetentionPlan | None = None
    retention_summary: dict[str, object] = {"status": "NOT_PLANNED"}
    with _lock_ctx:
        # ECMWF hang antibody #3 (2026-05-13) — boundary INFO logs at every
        # transition inside the BULK lock so the next 12h hang has a log
        # line pinpointing the failing stage. Witnessed 2026-05-12 13:31
        # PDT silence; see /tmp/zeus_ecmwf_critic_review.md.
        _ingest_t0 = time.monotonic()
        logger.info(
            "ingest_stage: lock_acquired track=%s source_run_id=%s",
            track,
            source_run_id,
        )
        if own_conn:
            conn = get_connection()
        try:
            assert_schema_current_forecasts(conn)
            logger.info(
                "ingest_stage: schema_ok track=%s elapsed_ms=%d",
                track,
                int((time.monotonic() - _ingest_t0) * 1000),
            )
            preserve_scopes = _qualified_partial_retry_scopes(
                conn,
                source_run_id=source_run_id,
            )
            cleared_authority = _clear_source_run_authority(
                conn,
                source_run_id=source_run_id,
                preserve_existing_authority=bool(preserve_scopes),
            )
            logger.info(
                "ingest_stage: cleared_prior_source_run track=%s source_run_id=%s %s",
                track,
                source_run_id,
                cleared_authority,
            )
            # The opendata extract writes JSON files to a different subdir than
            # TIGGE — reuse the same ingester by passing the parent directory and
            # the matching track name, and override the json_subdir lookup via the
            # _TRACK_CONFIGS dict. Cleanest in-process integration: temporarily
            # rebind the json_subdir for this call.
            # NOTE 2026-05-13: ingest_grib_to_snapshots is now eager-imported at
            # module top (antibody #1); we reference _ingest_grib_module rather
            # than re-running the import inside the BULK lock.
            original_subdir = _ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"]
            _ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = cfg["extract_subdir"]
            cycle_json_files = 0
            cycle_extract_dir = _cycle_extract_dir_name(run_date=cycle_date, run_hour=cycle_hour)
            try:
                with tempfile.TemporaryDirectory(prefix="zeus_opendata_cycle_") as scoped_tmp:
                    scoped_json_root, cycle_extract_dir, cycle_json_files = _build_cycle_scoped_json_root(
                        raw_root=coordinate_raw_root,
                        extract_subdir=cfg["extract_subdir"],
                        run_date=cycle_date,
                        run_hour=cycle_hour,
                        tmp_root=Path(scoped_tmp),
                        preserve_scopes=preserve_scopes,
                        available_steps=set(download_observed_steps) if _partial_cycle else None,
                    )
                    # Boundary marker — rglob happens inside ingest_track. The
                    # temporary view is the selected source cycle only, so stale
                    # raw directories cannot satisfy a new source_run.
                    logger.info(
                        "ingest_stage: rglob_start track=%s subdir=%s cycle_dir=%s cycle_json_files=%d",
                        track,
                        cfg["extract_subdir"],
                        cycle_extract_dir,
                        cycle_json_files,
                    )
                    # Real possession wall-clock captured immediately before the snapshot write.
                    # Used both for stale-row cleanup (ISO string below) and as the proof-of-
                    # possession basis for the snapshots' source_available_at (C1-AVAIL-CLOCK).
                    # The live daemon supplies its cycle-start now_utc, which may precede
                    # this newly fetched LSM. The evidence cannot be possessed before the
                    # mask was fetched; only synthetic skip-extract tests retain their
                    # injected historical clock without a real collection event.
                    snapshot_possession_at = _source_possession_clock(
                        now_utc, _fetch_finished_at, extracted=not skip_extract,
                    )
                    snapshot_replace_started_at = snapshot_possession_at.isoformat()
                    summary = _ingest_grib_ingest_track(
                        track=cfg["ingest_track"],
                        json_root=scoped_json_root,
                        conn=conn,
                        date_from=None,
                        date_to=None,
                        cities=None,
                        overwrite=True,
                        require_files=False,
                        source_run_context=_ingest_grib_SourceRunContext(
                            source_id=SOURCE_ID,
                            source_transport="ensemble_snapshots_db_reader",
                            source_run_id=source_run_id,
                            release_calendar_key=release_calendar_key,
                            source_cycle_time=source_cycle_time,
                            source_release_time=source_release_time,
                            # C1-AVAIL-CLOCK (2026-06-16): the snapshots' source_available_at /
                            # available_at must be PROOF OF POSSESSION, not source_release_time
                            # (the raw cycle, ~8h early — the exact lie that stamped
                            # ensemble_snapshots.available_at == cycle in 5000/5000 rows). The real
                            # possession wall-clock is snapshot_possession_at (captured just above,
                            # immediately before the write); routed through the canonical producer.
                            # SourceRunContext requires a datetime, so we parse the canonical ISO
                            # string back. No nominal is credited (no real publish estimate exists).
                            source_available_at=datetime.fromisoformat(
                                proof_of_possession_available_at(snapshot_possession_at)
                            ),
                            dataset_id=cfg["data_version"],
                            coordinate_manifest_sha=manifest_sha,
                            grid_surface_source_evidence=trusted_surface,
                        ),
                    )
                logger.info(
                    "ingest_stage: rglob_end track=%s cycle_dir=%s written=%s skipped_exists=%s parse_error=%s",
                    track,
                    cycle_extract_dir,
                    summary.get("written"),
                    summary.get("skipped_exists"),
                    summary.get("parse_error"),
                )
                stale_snapshots_deleted = (
                    0
                    if preserve_scopes
                    else _delete_stale_source_run_snapshots(
                        conn,
                        source_run_id=source_run_id,
                        replace_started_at_iso=snapshot_replace_started_at,
                    )
                )
                cleared_authority["snapshots_deleted"] += stale_snapshots_deleted
                logger.info(
                    "ingest_stage: stale_snapshot_cleanup track=%s source_run_id=%s deleted=%d",
                    track,
                    source_run_id,
                    stale_snapshots_deleted,
                )
            finally:
                _ingest_grib_module._TRACK_CONFIGS[cfg["ingest_track"]]["json_subdir"] = original_subdir
            status = _status_for_ingest_summary(summary)
            if (
                status == "empty_ingest"
                and preserve_scopes
                and cycle_json_files == 0
                and int(summary.get("parse_error", 0) or 0) == 0
            ):
                # A repeated PARTIAL retry may have no current JSON after all
                # already-qualified scopes were excluded.  This is a truthful
                # idempotent no-op, not an ingest failure; retained coverage
                # remains the authority and the run still carries PARTIAL
                # download facts below.
                status = "ok"
            authority_computed_at = _source_possession_clock(
                now_utc, snapshot_possession_at, extracted=not skip_extract,
            )
            authority_summary = _write_source_authority_chain(
                conn,
                summary=summary,
                status=status,
                source_run_id=source_run_id,
                source_cycle_time=source_cycle_time,
                source_release_time=source_release_time,
                release_calendar_key=release_calendar_key,
                forecast_track=forecast_track,
                data_version=cfg["data_version"],
                computed_at=authority_computed_at,
                # M5-COLLECTION-CLOCK (2026-06-16): real collection-plane instants threaded from their
                # actual code points. fetch_started/finished bracket the parallel download loop above;
                # captured = snapshot_possession_at, the now() taken immediately before the snapshot
                # write (decode-into-memory complete). imported is stamped inside the chain at the
                # source_run persist. None of these is computed_at (the model run-init).
                fetch_started_at=_fetch_started_at,
                fetch_finished_at=_fetch_finished_at,
                captured_at=snapshot_possession_at,
                # Pass download ground-truth so source_run.observed_steps_json
                # reflects actual fetched steps, not an ingest-derived approximation.
                # evaluate_producer_coverage:184 uses this for per-step MISSING detection.
                download_observed_steps=download_observed_steps,
                download_partial_run=_partial_cycle if download_observed_steps is not None else None,
                download_reason_code=_download_reason_code,
                attempt_started_at=(
                    snapshot_replace_started_at if _partial_cycle else None
                ),
                preserve_scopes=preserve_scopes,
            )
            logger.info(
                "ingest_stage: commit_start track=%s status=%s",
                track,
                status,
            )
            conn.commit()
            logger.info(
                "ingest_stage: commit_end track=%s status=%s total_ms=%d",
                track,
                status,
                int((time.monotonic() - _ingest_t0) * 1000),
            )
            try:
                retention_plan = _plan_decoded_open_data_raw_retention(
                    conn,
                    raw_root=paths.raw_root,
                    reference_date=now.date(),
                    reference_time=now,
                )
            except Exception as exc:  # noqa: BLE001 - retention must fail closed
                retention_summary = {
                    "status": "PLAN_ERROR",
                    "error": f"{type(exc).__name__}:{exc}",
                }
                logger.warning("ecmwf_open_data raw retention plan failed: %s", exc)
        finally:
            if own_conn:
                conn.close()

    # Large raw files are removed only after the canonical transaction commits
    # and the BULK writer lock is released. A retention failure never changes
    # the already-truthful source-run result or blocks the next probability job.
    if retention_plan is not None:
        retention_summary = _apply_decoded_open_data_raw_retention(retention_plan)
        logger.info("ecmwf_open_data raw_retention=%s", retention_summary)

    # Same placement rationale as the raw retention above: after the canonical
    # commit and outside the BULK writer lock, so filesystem cleanup can never
    # stall probability writers, and a cleanup failure never rewrites an
    # already-truthful source_run result. Only runs for an ok ingest — a failed
    # cycle's predecessor view is the one a retry would still want.
    if status == "ok":
        try:
            manifest_prune_summary = _prune_superseded_coordinate_manifest_cycles(
                coordinate_raw_root=coordinate_raw_root,
                extract_subdir=cfg["extract_subdir"],
                keep_run_date=cycle_date,
                keep_run_hour=cycle_hour,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must fail soft
            logger.warning(
                "ecmwf_open_data coordinate_manifest_prune failed: %s: %s",
                type(exc).__name__,
                exc,
            )
        else:
            logger.info(
                "ecmwf_open_data coordinate_manifest_prune=%s", manifest_prune_summary
            )

    stages = [
        *stages,
        {"label": "ingest", "ok": status == "ok", "error": status if status != "ok" else None},
    ]
    return {
        "status": status,
        "track": track,
        "data_version": cfg["data_version"],
        "run_date": cycle_date.isoformat(),
        "run_hour": cycle_hour,
        "source_run_id": source_run_id,
        "coordinate_manifest_sha": manifest_sha,
        "release_calendar_key": release_calendar_key,
        "forecast_track": forecast_track,
        "source_id": SOURCE_ID,
        "forecast_source_role": FORECAST_SOURCE_ROLE,
        "degradation_level": source_spec.degradation_level,
        "cycle_extract_dir": cycle_extract_dir,
        "cycle_json_files": cycle_json_files,
        "cleared_authority": cleared_authority,
        "download_path": str(output_path),
        "snapshots_inserted": int(summary.get("written", 0)),
        "snapshots_skipped": int(summary.get("skipped", 0)),
        "raw_retention": retention_summary,
        **authority_summary,
        "stages": stages,
    }


def data_version_priority_for_metric(temperature_metric: str) -> tuple[str, ...]:
    """Return read-priority tuple for a given metric.

    HIGH keeps the original OpenData → TIGGE ordering.  LOW prefers rows with
    contract-window evidence first, then falls back to legacy OpenData/TIGGE
    rows.  All entries remain in the same HIGH/LOW metric family.

    Use this in any reader that wants "freshest source first, fall back to
    archive". Equivalent SQL pattern::

        SELECT ... FROM ensemble_snapshots
         WHERE temperature_metric = ?
           AND dataset_id IN (<one placeholder per priority entry>)
         ORDER BY CASE dataset_id WHEN ? THEN 0 ELSE 1 END, available_at DESC

    where the bound parameters are the priority tuple followed by the
    priority tuple's first element again.
    """
    if temperature_metric == "high":
        return (ECMWF_OPENDATA_HIGH_DATA_VERSION, "tigge_mx2t6_local_calendar_day_max")
    if temperature_metric == "low":
        return (
            ECMWF_OPENDATA_LOW_CONTRACT_WINDOW_DATA_VERSION,
            ECMWF_OPENDATA_LOW_DATA_VERSION,
            TIGGE_LOW_CONTRACT_WINDOW_DATA_VERSION,
            "tigge_mn2t6_local_calendar_day_min",
        )
    raise ValueError(f"Unknown temperature_metric {temperature_metric!r}; expected 'high' or 'low'.")


# Back-compat shim — pre-2026-05-01 callers imported ``DATA_VERSION`` from this
# module assuming a single legacy v1 data_version. The structural fix splits
# the path into mx2t6 / mn2t6 tracks; the alias points at the high-track
# opendata data_version so existing imports keep working but new code should
# use ECMWF_OPENDATA_HIGH_DATA_VERSION / _LOW_DATA_VERSION explicitly.
DATA_VERSION = ECMWF_OPENDATA_HIGH_DATA_VERSION

__all__ = [
    "TRACKS",
    "STEP_HOURS",
    "SOURCE_ID",
    "MODEL_VERSION",
    "DATA_VERSION",
    "collect_open_ens_cycle",
    "data_version_priority_for_metric",
]
