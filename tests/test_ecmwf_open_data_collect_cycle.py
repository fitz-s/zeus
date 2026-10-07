# Created: 2026-05-11
# Last reused/audited: 2026-10-07
# Lifecycle: created=2026-05-11; last_reviewed=2026-10-07; last_reused=2026-10-07
# Purpose: Protect collector isolation, optional native capture and offline 2t knots without prediction-budget regression.
# Reuse: Inspect source-run, land-mask and shared-deadline contracts; use private DB/GRIB fixtures and fake HTTP.
# Authority basis: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md §5.5
#   Cross-track filename collision antibody: per-step filenames include param
#   (e.g. .step003_mx2t3.grib2 vs .step003_mn2t3.grib2) so concurrent mx2t6_high
#   and mn2t6_low cycles sharing the same output_dir do not clobber each other.
"""Integration regression tests for collect_open_ens_cycle cross-track isolation.

Relationship being tested: when mx2t6_high (param=mx2t3) and mn2t6_low (param=mn2t3)
run concurrently and share the same FIFTY_ONE_ROOT output directory, their per-step
intermediate files must not collide.  The filename pattern is:
  .step{NNN}_{param}.grib2   (e.g. .step003_mx2t3.grib2, .step003_mn2t3.grib2)
"""
from __future__ import annotations

import sqlite3
import hashlib
import json
import time
import multiprocessing
import os
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.state.db import init_schema, init_schema_forecasts
from src.state.schema.v2_schema import apply_canonical_schema
from src.state.source_run_repo import write_source_run


def _native_temperature_knots_fixture(tmp_path, *, fault=None, steps=(0, 3, 6), hour=0):
    """Synthetic ecCodes 2t bytes; no provider download or possession claim."""
    ec = pytest.importorskip("eccodes")
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib
    from scripts import extract_open_ens_localday as extractor

    issue = datetime(2026, 10, 3, hour, tzinfo=timezone.utc)
    _, mask, mask_proof, _ = _tiny_native_grib(tmp_path, "mx2t6_high", member_count=1, issue=issue)
    grid = {"Ni": 2, "Nj": 2, "latitudeOfFirstGridPointInDegrees": 51.75,
            "longitudeOfFirstGridPointInDegrees": 0., "latitudeOfLastGridPointInDegrees": 51.5,
            "longitudeOfLastGridPointInDegrees": .25, "iDirectionIncrementInDegrees": .25,
            "jDirectionIncrementInDegrees": .25, "scanningMode": 0}
    messages, evidence, offset = [], {}, 0
    for step in steps:
        for member in range(51):
            if fault == "missing" and (member, step) == (50, 6):
                continue
            gid = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
            try:
                headers = {"centre": "ecmf", **grid, "dataDate": 20261003, "dataTime": hour * 100,
                           "productDefinitionTemplateNumber": 0 if member == 0 else 1,
                           "typeOfGeneratingProcess": 2 if member == 0 else 4,
                           "generatingProcessIdentifier": 161, "paramId": 167,
                           "dataType": "fc" if member == 0 else "pf", "step": step}
                if member:
                    headers["number"] = member
                if (member, step) == (50, 6):
                    headers.update({"process": {"generatingProcessIdentifier": 158},
                        "run": {"dataTime": 600}, "grid": {"longitudeOfFirstGridPointInDegrees": .125},
                        "height": {"level": 3}, "step_type": {"stepType": "max"},
                        "pf0": {"number": 0}, "param": {"paramId": 130},
                        "dewpoint": {"paramId": 168}, "discipline": {"discipline": 10},
                        "step": {"step": 7}}.get(fault, {}))
                for key, value in headers.items():
                    ec.codes_set(gid, key, value)
                ec.codes_set_values(gid, [300., 310., 280 + member / 8 + step / 4, 320.])
                body = ec.codes_get_message(gid)
            finally:
                ec.codes_release(gid)
            stream, kind = ("oper", "fc") if member == 0 else ("enfo", "ef")
            url = f"https://data.ecmwf.int/forecasts/{issue:%Y%m%d}/{hour:02d}z/ifs/0p25/{stream}/{issue:%Y%m%d%H}0000-{step}h-{stream}-{kind}.grib2"
            row = dict(param="2t", levtype="sfc", date="20261003", time=f"{hour:02d}00", step=str(step),
                       stream=stream, type="fc" if member == 0 else "pf", _offset=512 + offset,
                       _length=len(body), **{"class": "od"})
            if member:
                row["number"] = str(member)
            if fault == "control_stream" and member == 0:
                row["stream"] = "enfo"
            line = json.dumps(row).encode()
            evidence[offset] = dict(source_url=url, source_index_url=url[:-6] + ".index",
                original_index_bytes=line + b"\n", original_range_bytes=body,
                source_index_sha256=hashlib.sha256(line + b"\n").hexdigest(),
                source_index_line_sha256=hashlib.sha256(line).hexdigest(),
                source_index_offset=row["_offset"], source_index_length=len(body),
                raw_message_sha256=hashlib.sha256(body).hexdigest(),
                source_fetched_at=(issue + timedelta(hours=1)).isoformat())
            messages.append(body)
            offset += len(body)
    if fault == "duplicate":
        messages.append(messages[-1])
    if fault == "transport_tamper":
        evidence[0]["original_range_bytes"] = messages[0][:-1] + b"0"
    path = tmp_path / "native-2t.grib2"
    path.write_bytes(b"".join(messages))
    return dict(grib_path=path, explicit_manifest=[{"city": "London", "lat": 51.6, "lon": .1, "unit": "C"}],
        expected_run_utc=issue, required_steps=list(steps), mask_grib_path=mask, mask_proof_path=mask_proof,
        surface_geopotential_grib_path=mask.with_suffix(".z.grib2"),
        surface_geopotential_proof_path=mask.with_suffix(".z.proof.json"),
        message_source_evidence=evidence)


def test_native_original_primitive_is_bounded_immutable_and_decoder_bound(tmp_path, monkeypatch):
    import eccodes as ec
    from scripts import extract_open_ens_localday as decoder

    inputs = _native_temperature_knots_fixture(tmp_path, steps=(0,))
    raw = inputs["message_source_evidence"][0]["original_range_bytes"]
    decoder._NATIVE_ORIGINAL_DECODE.clear()
    actual = ec.codes_new_from_message
    handles = []
    def counted(body):
        handles.append(hashlib.sha256(body).hexdigest())
        return actual(body)
    monkeypatch.setattr(ec, "codes_new_from_message", counted)
    first, point = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True)
    assert point[0] == 4 and point[2] == 161 and point[3] == (280.,)
    gid = actual(raw)
    try:
        direct = float(ec.codes_get_elements(gid, "values", [2])[0])
    finally:
        ec.codes_release(gid)
    for unit in ("C", "F"):
        assert decoder.kelvin_to_native(point[3][0], unit) == decoder.kelvin_to_native(direct, unit)
    first["observed_headers"]["units"] = "mutated caller"
    first["metadata_sections"].clear()
    again, repeated = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True)
    assert repeated == point and again["observed_headers"]["units"] == "K"
    assert again["metadata_sections"] and len(handles) == 1
    _, different_cell = decoder._decode_native_original(raw, flat_indices=(0,), instantaneous=True)
    assert different_cell[3] == (300.,) and len(handles) == 2
    _, wrong_unit = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True, physical_unit="C")
    assert wrong_unit == ()
    _, wrong_param = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True, parameter_id=228026)
    assert wrong_param == ()
    # The same bytes under another getter cannot inherit an earlier result.
    elements = ec.codes_get_elements
    monkeypatch.setattr(ec, "codes_get_elements", lambda *args: elements(*args))
    decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True)
    assert len(handles) == 5
    assert all(isinstance(value[0], str) and isinstance(value[1], tuple)
               for value in decoder._NATIVE_ORIGINAL_DECODE.values())
    assert not any(isinstance(part, bytes) for key in decoder._NATIVE_ORIGINAL_DECODE for part in key)
    decoder._NATIVE_ORIGINAL_DECODE.clear()


def test_native_original_primitive_never_memoizes_unknown_or_changed_body(tmp_path, monkeypatch):
    import eccodes as ec
    from scripts import extract_open_ens_localday as decoder

    inputs = _native_temperature_knots_fixture(tmp_path, steps=(0,))
    raw = inputs["message_source_evidence"][0]["original_range_bytes"]
    decoder._NATIVE_ORIGINAL_DECODE.clear()
    capture = decoder._native_message_capture
    calls = []
    def unknown(*args, **kwargs):
        calls.append(True)
        return {"capture_status": "UNKNOWN", "observed_headers": {}}
    monkeypatch.setattr(decoder, "_native_message_capture", unknown)
    for instant in (True, True, False):
        result, point = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=instant)
        assert result["capture_status"] == "UNKNOWN" and point == ()
    assert len(calls) == 3 and not decoder._NATIVE_ORIGINAL_DECODE
    monkeypatch.setattr(decoder, "_native_message_capture", capture)
    original, original_point = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True)
    gid = ec.codes_new_from_message(raw)
    try:
        ec.codes_set_values(gid, [300., 310., 281., 320.])
        changed = ec.codes_get_message(gid)
    finally:
        ec.codes_release(gid)
    revised, revised_point = decoder._decode_native_original(changed, flat_indices=(2,), instantaneous=True)
    assert revised["raw_message_sha256"] != original["raw_message_sha256"]
    assert original_point[3] == (280.,) and revised_point[3] == (281.,)
    restored, restored_point = decoder._decode_native_original(raw, flat_indices=(2,), instantaneous=True)
    assert restored == original and restored_point == original_point
    gid = ec.codes_new_from_message(raw)
    try:
        ec.codes_set(gid, "longitudeOfFirstGridPointInDegrees", .125)
        different_grid = ec.codes_get_message(gid)
    finally:
        ec.codes_release(gid)
    grid_result, _ = decoder._decode_native_original(different_grid, flat_indices=(2,), instantaneous=True)
    assert grid_result["raw_message_sha256"] != original["raw_message_sha256"]
    assert grid_result["observed_headers"]["longitudeOfFirstGridPointInDegrees"] == .125
    decoder._NATIVE_ORIGINAL_DECODE.clear()


def test_native_index_primitive_does_not_cache_binding_clocks_or_line_identity(tmp_path, monkeypatch):
    from scripts import extract_open_ens_localday as decoder

    inputs = _native_temperature_knots_fixture(tmp_path, steps=(0,))
    sources = inputs["message_source_evidence"]
    evidence = dict(sources[0])
    index = b"".join(item["original_index_bytes"] for item in sources.values())
    evidence.update(original_index_bytes=index, source_index_sha256=hashlib.sha256(index).hexdigest())
    raw = evidence["original_range_bytes"]
    headers = decoder._decode_native_original(raw, instantaneous=True)[0]["observed_headers"]
    decoder._NATIVE_ORIGINAL_INDEX_DECODE.clear()
    actual = json.loads
    reads = []
    def counted(text, *args, **kwargs):
        reads.append(True)
        return actual(text, *args, **kwargs)
    monkeypatch.setattr(json, "loads", counted)
    args = dict(param="2t", member=0, step=0, run=inputs["expected_run_utc"])
    first = decoder._open_ens_source_binding(raw, evidence, headers, **args)
    first_reads = len(reads)
    assert first_reads == 52
    assert decoder._open_ens_source_binding(raw, evidence, headers, **args) == first
    assert len(reads) == first_reads + 1
    assert all(isinstance(entry, tuple) and isinstance(entry[3], str)
               for matrix in decoder._NATIVE_ORIGINAL_INDEX_DECODE.values() for entry in matrix)
    changed_clock = {**evidence, "source_fetched_at": (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}
    with pytest.raises(ValueError, match="ENS_POINT_SOURCE_POSSESSION_CLOCK_INVALID"):
        decoder._open_ens_source_binding(raw, changed_clock, headers, **args)
    with pytest.raises(ValueError, match="ENS_POINT_SOURCE_ENVELOPE_MISMATCH"):
        decoder._open_ens_source_binding(raw, {**evidence, "source_url": "https://data.ecmwf.int/wrong"}, headers, **args)
    wrong_line = json.dumps({**actual(index.splitlines()[0]), "number": "1"}).encode()
    wrong_index = wrong_line+b"\n"+b"\n".join(index.splitlines()[1:])+b"\n"
    wrong = {**evidence, "original_index_bytes": wrong_index,
        "source_index_sha256": hashlib.sha256(wrong_index).hexdigest(),
        "source_index_line_sha256": hashlib.sha256(wrong_line).hexdigest()}
    with pytest.raises(ValueError, match="ENS_POINT_SOURCE_INDEX_IDENTITY_MISMATCH"):
        decoder._open_ens_source_binding(raw, wrong, headers, **args)
    duplicate = index+index.splitlines()[0]+b"\n"
    with pytest.raises(ValueError, match="ENS_POINT_SOURCE_INDEX_AMBIGUOUS"):
        decoder._open_ens_source_binding(raw, {**evidence, "original_index_bytes": duplicate,
            "source_index_sha256": hashlib.sha256(duplicate).hexdigest()}, headers, **args)
    assert decoder._open_ens_source_binding(raw, evidence, headers, **args) == first
    decoder._NATIVE_ORIGINAL_INDEX_DECODE.clear()


def test_native_temperature_knots_real_eccodes_instant_without_extrema_relaxation(tmp_path):
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path)
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    assert result["decode_status"] == "AVAILABLE", result
    assert result["transport_status"] == "OBSERVED", result
    assert result["qualification_status"] == "OFFLINE_ONLY"
    assert result["live_qualification_status"] == "NOT_EVALUATED"
    assert result["quantity_role"] == "native_2m_temperature_instantaneous_knots"
    assert result["projection_status"] == "NOT_PERFORMED"
    assert result["extrema_status"] == "NOT_COMPUTED"
    assert result["native_step_hours"] == [0, 3, 6]
    assert len(result["native_knots"]) == 153
    selected = result["selected_cities"]["London"]
    assert selected["selected_flat_index"] == 2
    assert selected["selected_land_fraction"] == pytest.approx(.8)
    assert selected["surface_class"] == "MIXED_LAND_WATER"
    assert [c["raw_phi_m2_s2"] for c in selected["four_neighbors"]] == [100., 200., 313.75, 400.]
    assert result["land_mask_receipt"]["transport_status"] == "UNKNOWN"
    assert result["surface_geopotential_receipt"]["transport_status"] == "UNKNOWN"
    assert result["surface_geopotential_receipt"]["sensor_agl_status"] == "UNPROVEN"
    knot = next(k for k in result["native_knots"] if (k["member"], k["step_hours"]) == (50, 6))
    assert knot["value_k"] == 287.75
    assert knot["valid_time_utc"] == "2026-10-03T06:00:00+00:00"
    assert knot["value_native_unit"] == pytest.approx(14.6)
    for message in result["messages"]:
        assert message["capture_status"] == "OBSERVED"
        assert message["observed_headers"]["generatingProcessIdentifier"] == 161
        assert {s["section_number"] for s in message["metadata_sections"]} == {0, 1, 3, 4}


@pytest.mark.parametrize("fault", ("missing", "duplicate", "process", "run", "grid", "height",
    "step_type", "pf0", "param", "step", "control_stream", "transport_tamper"))
def test_native_temperature_knots_rejects_unbound_or_incomplete_bytes(tmp_path, fault):
    from scripts import extract_open_ens_localday as extractor

    result = extractor.decode_open_ens_temperature_knots(**_native_temperature_knots_fixture(tmp_path, fault=fault))
    assert result["decode_status"] == "UNAVAILABLE", result
    assert result["native_knots"] == []
    assert result["transport_status"] == "UNKNOWN"


def test_native_temperature_knots_absent_transport_or_phi_remains_unknown(tmp_path):
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path)
    inputs.pop("message_source_evidence")
    inputs.pop("surface_geopotential_grib_path")
    inputs.pop("surface_geopotential_proof_path")
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    assert result["decode_status"] == "AVAILABLE", result
    assert result["transport_status"] == "UNKNOWN"
    assert result["surface_geopotential_receipt"]["capture_status"] == "UNKNOWN"
    assert all(c["raw_phi_m2_s2"] is None for c in result["selected_cities"]["London"]["four_neighbors"])


@pytest.mark.parametrize("steps,hour,available", (((144, 150, 156), 0, True), ((87, 90), 6, True),
    ((0, 1, 2), 0, False), ((144, 147, 150), 0, False), ((90, 93), 6, False), ((240, 246), 0, False)))
def test_native_temperature_knots_preserves_native_cadence_and_control_horizon(tmp_path, steps, hour, available):
    from scripts import extract_open_ens_localday as extractor

    result = extractor.decode_open_ens_temperature_knots(**_native_temperature_knots_fixture(tmp_path, steps=steps, hour=hour))
    assert (result["decode_status"] == "AVAILABLE") is available, result
    if available:
        assert result["native_step_hours"] == list(steps)
        assert len(result["native_knots"]) == len(steps) * 51


@pytest.mark.parametrize("field,value", (("units", "C"), ("validityTime", 100),
    ("generatingProcessIdentifier", 0), ("typeOfGeneratingProcess", 255)))
def test_native_temperature_knots_observed_header_must_match_original_sections(tmp_path, monkeypatch, field, value):
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path)
    original_get = extractor.codes_get
    def changed_header(gid, key):
        if key == field and original_get(gid, "paramId") == 167:
            return value
        return original_get(gid, key)
    monkeypatch.setattr(extractor, "codes_get", changed_header)
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    assert result["decode_status"] == "UNAVAILABLE", result
    assert result["native_knots"] == []


@pytest.mark.parametrize("fault", ("dewpoint", "discipline"))
def test_native_temperature_knots_original_parameter_rejects_temperature_header_spoof(tmp_path, monkeypatch, fault):
    """Original dewpoint/other-discipline bytes cannot be relabelled as 2t."""
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path, fault=fault)
    original_get = extractor.codes_get
    actual_headers = []
    def echoed_temperature(gid, key):
        changed = (original_get(gid, "paramId") == 168 if fault == "dewpoint"
                   else original_get(gid, "discipline") == 10)
        if changed:
            if key == "paramId":
                actual_headers.append((original_get(gid, "paramId"), original_get(gid, "shortName"),
                                       original_get(gid, "discipline")))
            if key in ("paramId", "shortName", "units", "discipline"):
                return {"paramId": 167, "shortName": "2t", "units": "K", "discipline": 0}[key]
        return original_get(gid, key)
    monkeypatch.setattr(extractor, "codes_get", echoed_temperature)
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    assert actual_headers
    if fault == "dewpoint":
        assert actual_headers[0] == (168, "2d", 0)
    else:
        assert actual_headers[0][2] == 10
    assert result["decode_status"] == "UNAVAILABLE", result
    assert result["native_knots"] == []
    assert result["unavailable_reason"] == "ENS_POINT_ORIGINAL_PARAMETER_IDENTITY_MISMATCH"


def test_native_temperature_knots_nonfinite_selected_cell_is_unavailable(tmp_path, monkeypatch):
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path)
    original_values = extractor.codes_get_values
    def invalid_point(gid):
        values = original_values(gid)
        if extractor.codes_get(gid, "paramId") == 167:
            values[2] = float("nan")
        return values
    monkeypatch.setattr(extractor, "codes_get_values", invalid_point)
    assert extractor.decode_open_ens_temperature_knots(**inputs)["decode_status"] == "UNAVAILABLE"


@pytest.mark.parametrize("kind", ("phi_hash", "lsm_hash", "phi_absent", "prior_static_clock"))
def test_native_temperature_knots_geometry_has_own_original_proof(tmp_path, kind):
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path)
    if kind in ("phi_hash", "lsm_hash"):
        path = inputs["surface_geopotential_proof_path" if kind == "phi_hash" else "mask_proof_path"]
        proof = json.loads(path.read_text())
        proof["raw_message_sha256" if kind == "phi_hash" else "mask_sha256"] = "0" * 64
        path.write_text(json.dumps(proof))
    elif kind == "phi_absent":
        inputs["surface_geopotential_grib_path"] = tmp_path / "absent-z.grib2"
    else:
        prior = datetime(2026, 10, 2, tzinfo=timezone.utc)
        from tests.test_ingest_grib_source_run_context import _tiny_native_grib
        (tmp_path / "prior").mkdir()
        _, mask, proof, _ = _tiny_native_grib(tmp_path / "prior", "mx2t6_high", member_count=1, issue=prior)
        inputs["mask_grib_path"], inputs["mask_proof_path"] = mask, proof
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    if kind == "lsm_hash":
        assert result["decode_status"] == "UNAVAILABLE", result
    else:
        assert result["decode_status"] == "AVAILABLE", result
        if kind == "prior_static_clock":
            assert result["land_mask_receipt"]["static_run_validity_status"] == "UNKNOWN"
        else:
            assert result["surface_geopotential_receipt"]["capture_status"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ("index_hash", "line_hash", "offset", "index_member", "source_model",
    "future_possession", "duplicate_index_line"))
def test_native_temperature_knots_transport_requires_original_index_and_clock(tmp_path, fault):
    from scripts import extract_open_ens_localday as extractor

    inputs = _native_temperature_knots_fixture(tmp_path)
    proof = inputs["message_source_evidence"][0]
    if fault in ("index_hash", "line_hash"):
        proof["source_index_sha256" if fault == "index_hash" else "source_index_line_sha256"] = "0" * 64
    elif fault == "offset":
        proof["source_index_offset"] += 1
    elif fault == "source_model":
        proof["source_url"] = proof["source_url"].replace("/ifs/", "/aifs/")
    elif fault == "future_possession":
        proof["source_fetched_at"] = "2100-01-01T00:00:00+00:00"
    else:
        if fault == "index_member":
            row = json.loads(proof["original_index_bytes"])
            row["number"] = "50"
            line = json.dumps(row).encode()
            proof["original_index_bytes"] = line + b"\n"
            proof["source_index_line_sha256"] = hashlib.sha256(line).hexdigest()
        else:
            proof["original_index_bytes"] *= 2
        proof["source_index_sha256"] = hashlib.sha256(proof["original_index_bytes"]).hexdigest()
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    assert result["decode_status"] == "UNAVAILABLE", result
    assert result["native_knots"] == []


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_native_temperature_knots_never_relabels_original_extrema_as_instant(tmp_path, track):
    from scripts import extract_open_ens_localday as extractor
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib

    inputs = _native_temperature_knots_fixture(tmp_path)
    (tmp_path / "extrema").mkdir()
    raw, _, _, _ = _tiny_native_grib(tmp_path / "extrema", track, issue=inputs["expected_run_utc"])
    inputs["grib_path"] = raw
    result = extractor.decode_open_ens_temperature_knots(**inputs)
    assert result["decode_status"] == "UNAVAILABLE", result
    assert result["extrema_status"] == "NOT_COMPUTED"


def _native_temperature_transport_fixture(tmp_path, *, fault=None, land_mask=False, steps=(0, 3), hour=0):
    """Mock HTTP serves only actual synthetic GRIB/index originals."""
    inputs = _native_temperature_knots_fixture(tmp_path, steps=steps, hour=hour)
    sources, indexes = {}, {}
    for proof in inputs["message_source_evidence"].values():
        url = proof["source_url"].replace("https://data.ecmwf.int/forecasts", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com")
        row = json.loads(proof["original_index_bytes"])
        if fault in ("grib_dewpoint", "grib_grid", "grib_process") and row.get("number") == "50" and row["step"] == "3":
            ec = pytest.importorskip("eccodes")
            gid = ec.codes_new_from_message(proof["original_range_bytes"])
            try:
                key, value = {"grib_dewpoint": ("paramId", 168),
                    "grib_grid": ("longitudeOfLastGridPointInDegrees", .5),
                    "grib_process": ("generatingProcessIdentifier", 158)}[fault]
                ec.codes_set(gid, key, value)
                proof["original_range_bytes"] = ec.codes_get_message(gid)
                row["_length"] = len(proof["original_range_bytes"])
            finally:
                ec.codes_release(gid)
        if fault == "missing_member" and row.get("number") == "50":
            continue
        if fault == "wrong_run":
            row["date"] = "20261002"
        if fault == "duplicate" and row.get("number") == "50":
            indexes.setdefault(url[:-6] + ".index", []).append(json.dumps(row).encode())
        indexes.setdefault(url[:-6] + ".index", []).append(json.dumps(row).encode())
        body = sources.setdefault(url, bytearray())
        end = row["_offset"] + row["_length"]
        if len(body) < end:
            body.extend(b"\0" * (end - len(body)))
        body[row["_offset"]:end] = proof["original_range_bytes"]
    if land_mask:
        ec = pytest.importorskip("eccodes")
        gid = ec.codes_new_from_message(inputs["mask_grib_path"].read_bytes())
        try:
            ec.codes_set(gid, "dataType", "fc")
            raw = ec.codes_get_message(gid)
        finally:
            ec.codes_release(gid)
        url = next(url for url in sources if url.endswith("-0h-oper-fc.grib2"))
        row = dict(param="lsm", levtype="sfc", date="20261003", time="0000", step="0",
                   stream="oper", type="fc", _offset=0, _length=len(raw), **{"class": "od"})
        sources[url][:len(raw)] = raw
        indexes[url[:-6] + ".index"].append(json.dumps(row).encode())
    calls = []
    class Response:
        def __init__(self, body, status, headers):
            self.body, self.status_code, self.headers = body, status, headers
            self._native_body_complete = True  # Private HTTP is already in-memory, never a blocking stream.
        @property
        def content(self):
            return self.body
        def raise_for_status(self):
            if self.status_code >= 400:
                import requests
                raise requests.HTTPError(f"HTTP {self.status_code}", response=self)
        def iter_content(self, chunk_size):
            for offset in range(0, len(self.body), chunk_size):
                yield self.body[offset:offset + chunk_size]
        def close(self):
            pass
    class Session:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith(".index"):
                body = b"\n".join(indexes[url]) + b"\n"
                return Response(body, 404 if fault == "http404" else 200, {"Content-Length": str(len(body))})
            start, end = map(int, kwargs["headers"]["Range"][6:].split("-"))
            body = bytes(sources[url][start:end + 1])
            if fault == "truncated":
                body = body[:-1]
            return Response(body, 200 if fault == "full200" else 206,
                {"Content-Range": f"bytes {start}-{end}/{len(sources[url])}", "Content-Length": str(end - start + 1)})
        def close(self):
            pass
    output = tmp_path / "packet"
    output.mkdir()
    return inputs["expected_run_utc"], output, Session(), calls


def _normal_native_http(tmp_path, monkeypatch, *, steps=(0, 3), fault=None, hour=0):
    from src.data import ecmwf_open_data as module
    import ecmwf.opendata

    run, _, session, calls = _native_temperature_transport_fixture(tmp_path, steps=steps, fault=fault, hour=hour)
    class Client:
        verify = True
        def __init__(self, **kwargs):
            self.session = session
        def _get_urls(self, **kwargs):
            step = kwargs["step"][0]
            stream, kind = ("oper", "fc") if kwargs["type"] == ["fc"] else ("enfo", "ef")
            url = (f"https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{run:%Y%m%d}/"
                f"{run:%H}z/ifs/0p25/{stream}/{run:%Y%m%d%H}0000-{step}h-{stream}-{kind}.grib2")
            return SimpleNamespace(urls=[url], for_index={"param": ["2t"]}, target=kwargs["target"])
    monkeypatch.setattr(ecmwf.opendata, "Client", Client)
    monkeypatch.setattr(module, "_RateLimitedSession", lambda: session)
    monkeypatch.setattr(module, "_NativeDeadlineSession", lambda: session)
    # This legacy original-body fixture supplies one endpoint. The configured
    # pool fixture below explicitly supplies both official replicas.
    monkeypatch.setattr(module, "_DOWNLOAD_SOURCES", ("aws",))
    paths = module.OpenDataPaths(raw_root=tmp_path / "normal", asset_root=tmp_path,
        extract_script=Path("unused"), manifest_path=Path("unused"), origin="private-fixture")
    conn = sqlite3.connect(tmp_path / "normal-forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    conn.commit()
    return SimpleNamespace(module=module, run=run, session=session, calls=calls, paths=paths, conn=conn,
        args=dict(conn=conn, run_utc=run, required_steps=list(steps), _paths=paths,
            _priority=lambda: True, cycle_deadline_monotonic=time.monotonic() + 30))


def test_normal_native_capture_source_scope_preserves_unqualified_role(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        result = s.module.collect_native_temperature_source(**s.args)
        row = s.conn.execute("SELECT * FROM source_run").fetchone()
        assert result["status"] == "AVAILABLE", result
        assert (row["ingest_mode"], row["origin_mode"]) == ("SCHEDULED_LIVE", "SCHEDULED_LIVE")
        assert (row["status"], row["completeness_status"]) == ("SUCCESS", "COMPLETE")
        assert row["source_issue_time"] is row["source_release_time"] is row["source_available_at"] is None
        scope_dir = tmp_path / "scope"
        scope_dir.mkdir()
        inputs = _native_temperature_knots_fixture(scope_dir, steps=(0, 3))
        scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=result["source_run_id"]),
            Path(result["manifest_path"]), inputs)
        assert scope.status == "AVAILABLE", scope
        assert len(scope.native_knots) == 102
        assert scope.qualification_status == "UNKNOWN"
        assert scope.available_at is None and scope.static_validity_status == "UNKNOWN"
        assert scope.temporal_representation == "acquired_native_knots"
        assert {k["step_hours"] for k in scope.native_knots} == {0, 3}
        unknown = _native_source_scope(s.conn, SimpleNamespace(source_run_id=result["source_run_id"]),
            Path(result["manifest_path"]), inputs, qualified_prefix_cut_utc=None)
        assert unknown.status == "UNKNOWN"
        assert unknown.qualification_status == "UNKNOWN" and unknown.available_at is None
        assert s.conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 0
        assert s.conn.execute("SELECT COUNT(*) FROM day0_hourly_vectors").fetchone()[0] == 0
        payload = json.loads(Path(result["manifest_path"]).read_bytes())
        saved = payload["messages"][0]
        proof = json.loads(Path(saved["path"]).with_suffix(".grib2.proof.json").read_bytes())
        receipt_path = Path(saved["path"]).parent / proof["index_path"]
        receipt_bytes = receipt_path.with_suffix(".http.json").read_bytes()
        receipt = json.loads(receipt_bytes)
        assert hashlib.sha256(receipt_bytes).hexdigest() == proof["index_receipt_sha256"]
        assert receipt["http"]["status"] == 200
        assert receipt["fetch_started_at"] <= receipt["source_fetched_at"] <= proof["source_fetched_at"]
    finally:
        s.conn.close()


@pytest.fixture
def configured_native_pool(tmp_path, monkeypatch):
    """Two configured official replicas serve actual synthetic original bytes."""
    import ecmwf.opendata
    s = _normal_native_http(tmp_path, monkeypatch)
    client = ecmwf.opendata.Client
    class RoutedClient(client):
        def __init__(self, **kwargs):
            self.mirror = kwargs["source"]
            super().__init__(**kwargs)
        def _get_urls(self, **kwargs):
            result = super()._get_urls(**kwargs)
            if self.mirror == "google":
                result.urls = [url.replace("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
                    "https://storage.googleapis.com/ecmwf-open-data") for url in result.urls]
            return result
    monkeypatch.setattr(ecmwf.opendata, "Client", RoutedClient)
    monkeypatch.setattr(s.module, "_DOWNLOAD_SOURCES", ("aws", "google"))
    s.clock, s.hosts, s.mode, s.google_fault = [100.], [], "fast503", None
    monkeypatch.setattr(s.module.time, "monotonic", lambda: s.clock[0])
    get = s.session.get
    def private_get(url, **kwargs):
        google = "storage.googleapis.com" in url
        s.hosts.append("google" if google else "aws")
        response = get(url.replace("https://storage.googleapis.com/ecmwf-open-data",
            "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"), **kwargs)
        if not google and s.mode in {"fast503", "full503"}:
            if s.mode == "full503":
                s.clock[0] = s.session._zeus_deadline
            response.status_code = 503
        elif not google and s.mode == "partial503":
            if "Range" in kwargs.get("headers", {}):
                s.clock[0] += 58.
            elif "-0h-enfo-ef.index" in url:
                s.clock[0] += 1.
                response.status_code = 503
        elif google and s.google_fault == "prefix" and "-0h-oper-fc.grib2" in url:
            import eccodes as ec
            gid = ec.codes_new_from_message(response.body)
            try:
                ec.codes_set_values(gid, ec.codes_get_values(gid) + 1.)
                changed = ec.codes_get_message(gid)
                assert len(changed) == len(response.body)
                response.body = changed
            finally:
                ec.codes_release(gid)
        return response
    s.session.get = private_get
    s.cache = s.paths.raw_root / "raw/ecmwf_open_ens/native_2t_scheduled" / f"{s.run:%Y%m%dT%HZ}"
    s.receipt = s.cache / "mirror-attempt.json"
    def poll():
        return s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": s.clock[0] + 59})
    s.poll = poll
    try:
        yield s
    finally:
        s.conn.close()


def test_normal_native_partial_cache_probe_and_same_turn_resume_keep_budget(tmp_path, monkeypatch):
    """An actual 509-part prefix must leave the original cut for missing parts."""
    s = _normal_native_http(tmp_path, monkeypatch, steps=tuple(range(0, 33, 3)))
    clock, ranges = [100.], [0]
    monkeypatch.setattr(s.module.time, "monotonic", lambda: clock[0])
    original_get = s.session.get
    def stop_capture(url, **kwargs):
        response = original_get(url, **kwargs)
        if "Range" in kwargs.get("headers", {}):
            ranges[0] += 1
            if ranges[0] == 510:
                clock[0] = s.session._zeus_deadline
        return response
    s.session.get = stop_capture
    try:
        first = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": 159.})
        assert first["status"] == "INCOMPLETE" and first["observed_count"] == 509, first
        cache = Path(first["manifest_path"]).parent
        before = json.loads(Path(first["manifest_path"]).read_bytes())["retained_identities"]
        original_read = s.module._read_native_temperature_record
        reads, missing_requests = [], []
        def charged_read(path, *args, **kwargs):
            result = original_read(path, *args, **kwargs)
            reads.append(path)
            # Controlled clock models the observed ~45ms/original read cost;
            # the original parser, hashes, source binding and clocks still run.
            clock[0] += .045
            return result
        monkeypatch.setattr(s.module, "_read_native_temperature_record", charged_read)
        s.session.get = lambda url, **kwargs: (missing_requests.append(url), original_get(url, **kwargs))[1]
        clock[0] = 200.
        probe = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": 200.})
        assert probe["status"] == "DEFERRED" and not reads and not missing_requests, probe
        result = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": 259.})
        assert missing_requests and result["observed_count"] > 509, result
        # Stage hardlinks already strongly read in this invocation need no
        # second read; independent/new originals retain the full verifier.
        stage = cache / f".mirror-{s.module._resume_source_namespace('aws')}.partial"
        old_names = {Path(record["path"]).name for record in before}
        assert not [p for p in reads if p.parent == stage and p.name in old_names]
        after = json.loads(Path(result["manifest_path"]).read_bytes())["retained_identities"]
        assert all(next(r for r in after if (r["member"], r["step_hours"]) ==
            (old["member"], old["step_hours"])) == old for old in before)
    finally:
        s.conn.close()


@pytest.mark.parametrize("object_kind,change", (
    ("body", "touch"), ("body", "replace"), ("proof", "replace"),
    ("index", "replace"), ("receipt", "replace"),
    ("body", "corrupt"), ("proof", "corrupt"),
    ("index", "corrupt"), ("receipt", "corrupt"),
))
def test_normal_native_same_turn_reuse_revalidates_changed_objects(
        configured_native_pool, monkeypatch, object_kind, change):
    s = configured_native_pool
    s.mode = "healthy"
    monkeypatch.setattr(s.module, "_DOWNLOAD_SOURCES", ("aws",))
    assert s.poll()["status"] == "AVAILABLE"
    before = {p.name: p.read_bytes() for p in s.cache.iterdir()
        if p.is_file() and p.name != "mirror-attempt.json"}
    source_rows = [tuple(r) for r in s.conn.execute("SELECT * FROM source_run")]
    stage = s.cache / f".mirror-{s.module._resume_source_namespace('aws')}.partial"
    body = stage / "step000-member00.grib2"
    proof_path = body.with_suffix(".grib2.proof.json")
    proof = json.loads(proof_path.read_bytes())
    index = stage / proof["index_path"]
    receipt = s.module._native_index_receipt_path(index, proof["index_receipt_sha256"])
    target = {"body":body,"proof":proof_path,"index":index,"receipt":receipt}[object_kind]
    real_read, changed = s.module._read_native_temperature_record, [False]
    reread, requests = [], []
    def read(path, *args, **kwargs):
        if path.parent == stage and path.name == body.name:
            reread.append(path)
        result = real_read(path, *args, **kwargs)
        if path.parent == s.cache and path.name == "step003-member50.grib2" and not changed[0]:
            changed[0] = True
            if change == "touch":
                observed = target.stat()
                s.module.os.utime(target, ns=(observed.st_atime_ns, observed.st_mtime_ns+1))
            else:
                # Change only the private stage, preserving published originals.
                replacement = target.with_name(target.name+".private-replacement")
                replacement.write_bytes(target.read_bytes() if change == "replace" else b"{}")
                s.module.os.replace(replacement, target)
        return result
    monkeypatch.setattr(s.module, "_read_native_temperature_record", read)
    def stop_missing(url, **kwargs):
        requests.append(url)
        raise ValueError("PRIVATE_MISSING_HTTP_BOUNDARY")
    s.session.get = stop_missing
    s.args["required_steps"] = [0,3,6]
    result = s.poll()
    assert changed[0] and reread, result
    assert result["status"] == "UNKNOWN" and result["qualification_status"] == "UNKNOWN", result
    if change == "corrupt":
        assert not requests and result["reason"] != "PRIVATE_MISSING_HTTP_BOUNDARY", result
    else:
        assert len(requests) == 1 and requests[0].endswith("-6h-oper-fc.index"), result
    assert source_rows == [tuple(r) for r in s.conn.execute("SELECT * FROM source_run")]
    assert all((s.cache/name).read_bytes() == raw for name,raw in before.items())


def test_normal_native_fast_503_uses_configured_google_originals(configured_native_pool):
    s = configured_native_pool
    result = s.poll()
    assert result["status"] == "AVAILABLE" and result["observed_count"] == 102, result
    assert s.hosts[0] == "aws" and set(s.hosts[1:]) == {"google"}
    assert s.clock[0] == 100.
    receipt = json.loads(s.receipt.read_bytes())
    assert receipt["last_attempted_mirror"] == "google" and receipt["status"] == "COMPLETE"
    manifest = json.loads(Path(result["manifest_path"]).read_bytes())
    assert len(manifest["messages"]) == 102
    assert all(row["source_url"].startswith("https://storage.googleapis.com/ecmwf-open-data/") for row in manifest["messages"])
    row = s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (result["source_run_id"],)).fetchone()
    assert row["source_id"] == "ecmwf_open_data" and row["completeness_status"] == "COMPLETE"
    assert row["source_issue_time"] is row["source_available_at"] is None
    assert result["qualification_status"] == "UNKNOWN"


@pytest.mark.parametrize("casing", ("lower", "mixed", "upper"))
def test_normal_native_google_range_header_case_preserves_originals(configured_native_pool, casing):
    from requests.structures import CaseInsensitiveDict
    s = configured_native_pool
    get = s.session.get
    def original_headers(url, **kwargs):
        response = get(url, **kwargs)
        if "Range" in kwargs.get("headers", {}):
            names = {"lower": str.lower, "mixed": str.swapcase, "upper": str.upper}
            response.headers = CaseInsensitiveDict({names[casing](k): v for k, v in response.headers.items()})
        return response
    s.session.get = original_headers
    result = s.poll()
    assert result["status"] == "AVAILABLE" and result["observed_count"] == 102, result
    manifest = Path(result["manifest_path"])
    saved = json.loads(manifest.read_bytes())["messages"][0]
    body = Path(saved["path"])
    proof_path = body.with_suffix(".grib2.proof.json")
    proof = json.loads(proof_path.read_bytes())
    assert any(k != k.title() for k in proof["range_http"]["headers"])
    source_before = dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (result["source_run_id"],)).fetchone())
    before = (body.read_bytes(), proof_path.read_bytes(), manifest.read_bytes())
    inputs_dir = body.parent / "private-scope"; inputs_dir.mkdir()
    inputs = _native_temperature_knots_fixture(inputs_dir, steps=(0, 3))
    scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=result["source_run_id"]), manifest, inputs)
    assert scope.status == "AVAILABLE" and len(scope.native_knots) == 102, scope
    s.calls.clear(); s.hosts.clear()
    assert s.poll()["status"] == "AVAILABLE" and s.calls == [] and s.hosts == []
    assert (body.read_bytes(), proof_path.read_bytes(), manifest.read_bytes()) == before
    source_after = dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (result["source_run_id"],)).fetchone())
    # Inventory revalidation updates recorded_at, never original possession.
    assert {k for k in source_before if source_before[k] != source_after[k]} <= {"recorded_at"}


@pytest.mark.parametrize("field,fault", (("Content-Range", "duplicate"), ("Content-Length", "duplicate"),
    ("Content-Range", "missing"), ("Content-Length", "missing"), ("Content-Range", "mismatch"),
    ("Content-Length", "mismatch"), ("Content-Range", "badvalue"), ("Content-Length", "badvalue"),
    ("headers", "nullcontainer"), ("headers", "listcontainer"), ("headers", "scalarcontainer")))
def test_normal_native_range_singletons_are_strict_and_reset(tmp_path, monkeypatch, field, fault):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        first = s.module.collect_native_temperature_source(**s.args)
        assert first["status"] == "AVAILABLE", first
        manifest = Path(first["manifest_path"])
        saved = json.loads(manifest.read_bytes())["messages"][0]
        body = Path(saved["path"])
        proof_path = body.with_suffix(".grib2.proof.json")
        original = proof_path.read_bytes()
        before = (body.read_bytes(), manifest.read_bytes(),
            tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (first["source_run_id"],)).fetchone()))
        proof = json.loads(original); headers = proof["range_http"]["headers"]
        if field == "headers":
            proof["range_http"]["headers"] = {"nullcontainer": None, "listcontainer": [], "scalarcontainer": 7}[fault]
        elif fault == "duplicate":
            headers[field.lower()] = headers[field]
        elif fault == "missing":
            del headers[field]
        elif fault == "mismatch":
            headers[field] = "bytes 1-2/3" if field == "Content-Range" else "1"
        else:
            headers[field] = [] if field == "Content-Range" else None
        proof_path.write_text(json.dumps(proof))
        s.calls.clear()
        refused = s.module.collect_native_temperature_source(**s.args)
        assert refused["status"] == "UNKNOWN" and refused["reason"] == "NATIVE_2T_RANGE_RECEIPT_INVALID", refused
        assert s.calls == [] and refused["qualification_status"] == "UNKNOWN"
        inputs_dir = tmp_path / "invalid-range-scope"; inputs_dir.mkdir()
        inputs = _native_temperature_knots_fixture(inputs_dir, steps=(0, 3))
        args = (s.conn, SimpleNamespace(source_run_id=first["source_run_id"]), manifest, inputs)
        assert _native_source_scope(*args).status == "UNKNOWN"
        assert (body.read_bytes(), manifest.read_bytes(),
            tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (first["source_run_id"],)).fetchone())) == before
        proof_path.write_bytes(original)
        assert _native_source_scope(*args).status == "AVAILABLE"
        assert s.module.collect_native_temperature_source(**s.args)["status"] == "AVAILABLE" and s.calls == []
        assert proof_path.read_bytes() == original
    finally:
        s.conn.close()


def test_normal_native_google_lowercase_stage_resumes_without_remint(configured_native_pool, monkeypatch):
    from requests.structures import CaseInsensitiveDict
    s = configured_native_pool
    get = s.session.get
    def lowercase_response(url, **kwargs):
        response = get(url, **kwargs)
        if "Range" in kwargs.get("headers", {}):
            response.headers = CaseInsensitiveDict({k.lower(): v for k, v in response.headers.items()})
        return response
    s.session.get = lowercase_response
    with monkeypatch.context() as crash:
        def interrupted(*args, **kwargs):
            raise OSError("PRIVATE_PUBLICATION_INTERRUPTED")
        crash.setattr(s.module, "_promote_native_mirror_part", interrupted)
        failed = s.poll()
        assert failed["status"] == "UNKNOWN" and failed["reason"] == "PRIVATE_PUBLICATION_INTERRUPTED"
    stage = s.cache / f".mirror-{s.module._resume_source_namespace('google')}.partial"
    original = stage / "step000-member00.grib2"
    proof_path = original.with_suffix(".grib2.proof.json")
    before = (original.read_bytes(), proof_path.read_bytes())
    proof = json.loads(before[1])
    assert "content-range" in proof["range_http"]["headers"]
    s.calls.clear()
    complete = s.poll()
    assert complete["status"] == "AVAILABLE" and complete["observed_count"] == 102, complete
    assert not any(url == proof["source_url"] and "Range" in args.get("headers", {}) for url, args in s.calls)
    published = s.cache / original.name
    assert (published.read_bytes(), published.with_suffix(".grib2.proof.json").read_bytes()) == before
    assert (original.read_bytes(), proof_path.read_bytes()) == before


@pytest.mark.parametrize("interrupted", (False, True), ids=("mixed_receipts", "restart_stage"))
def test_normal_native_partial_index_entity_accepts_new_http_receipt_without_remint(configured_native_pool, monkeypatch, tmp_path, interrupted):
    """Same index bytes have distinct, truthful immutable acquisition receipts."""
    s = configured_native_pool
    s.mode = "healthy"
    monkeypatch.setattr(s.module, "_DOWNLOAD_SOURCES", ("aws",))
    original_get = s.session.get
    phase = [0]
    ranges = [0]
    def get(url, **kwargs):
        response = original_get(url, **kwargs)
        if url.endswith(".index"):
            response.headers.update({"ETag": '"same-original-index"',
                "Last-Modified": "Tue, 06 Oct 2026 07:40:02 GMT",
                "Date": "Tue, 06 Oct 2026 08:48:52 GMT" if phase[0] == 0 else "Tue, 06 Oct 2026 21:59:44 GMT"})
        elif phase[0] == 0:
            ranges[0] += 1
            if ranges[0] == 3:
                s.clock[0] = s.session._zeus_deadline
        return response
    s.session.get = get
    first = s.poll()
    assert first["status"] == "INCOMPLETE" and first["observed_count"] == 2, first
    before = json.loads(Path(first["manifest_path"]).read_bytes())["retained_identities"]
    stage = s.cache / f".mirror-{s.module._resume_source_namespace('aws')}.partial"
    # A legacy partial capture predates the mirror stage. Preserve the private
    # stage forensics, while exercising the next ordinary acquisition generation.
    stage.rename(stage.with_name(".private-original-stage"))
    original_cache = {p.name: p.read_bytes() for p in s.cache.iterdir() if p.is_file() and p.name != "mirror-attempt.json"}
    phase[0] = 1
    s.clock[0] += 60.
    real_link = s.module.os.link
    stopped = [False]
    raced = [False]
    def link(origin, destination, **kwargs):
        p = Path(destination)
        if interrupted and p.parent == s.cache and p.name.endswith(".grib2.proof.json") and not p.exists() and not stopped[0]:
            stopped[0] = True
            raise OSError("private new-generation proof publication interruption")
        if not interrupted and p.parent == s.cache and ".http-" in p.name and not p.exists() and not raced[0]:
            raced[0] = True
            real_link(origin, destination, **kwargs)
            raise FileExistsError("private byte-equal concurrent immutable receipt publication")
        return real_link(origin, destination, **kwargs)
    monkeypatch.setattr(s.module.os, "link", link)
    second = s.poll()
    if interrupted:
        assert second["status"] == "UNKNOWN" and stopped[0], second
        s.conn.close()
        s.conn = sqlite3.connect(s.paths.raw_root.parent / "normal-forecasts.db")
        s.conn.row_factory = sqlite3.Row
        s.args["conn"] = s.conn
        s.clock[0] += 60.
        second = s.poll()
    assert second["status"] == "AVAILABLE" and second["observed_count"] == 102, second
    assert stopped[0] if interrupted else raced[0]
    after = json.loads(Path(second["manifest_path"]).read_bytes())["retained_identities"]
    for old in before:
        assert next(r for r in after if (r["member"], r["step_hours"]) == (old["member"], old["step_hours"])) == old
    for name, body in original_cache.items():
        if name != "source-manifest.json": assert (s.cache / name).read_bytes() == body
    assert second["qualification_status"] == "UNKNOWN" and second["available_at"] is None
    calls = len(s.calls)
    assert s.poll()["status"] == "AVAILABLE" and len(s.calls) == calls
    from types import SimpleNamespace
    readback_dir = tmp_path / "mixed-readback"; readback_dir.mkdir()
    inputs = _native_temperature_knots_fixture(readback_dir, steps=(0, 3))
    scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=second["source_run_id"]), Path(second["manifest_path"]), inputs)
    assert scope.status == "AVAILABLE" and len(scope.native_knots) == 102, scope
    fresh = next(r for r in after if (r["member"], r["step_hours"]) not in {(r["member"], r["step_hours"]) for r in before})
    part = Path(fresh["path"])
    proof = json.loads(part.with_suffix(".grib2.proof.json").read_bytes())
    index = part.parent / proof["index_path"]
    receipt = s.module._native_index_receipt_path(index, proof["index_receipt_sha256"])
    assert hashlib.sha256(receipt.read_bytes()).hexdigest() == proof["index_receipt_sha256"]
    assert json.loads(receipt.read_bytes())["http"]["headers"]["Date"].endswith("21:59:44 GMT")
    # An exact referenced generation must fail closed, not fall back to a
    # different receipt or mint old possession clocks on a normal retry.
    for damaged in (index, receipt):
        saved = damaged.read_bytes(); damaged.write_bytes(b"{}\n")
        failed = s.poll()
        assert failed["status"] == "UNKNOWN" and len(s.calls) == calls, failed
        damaged.write_bytes(saved)
        assert s.poll()["status"] == "AVAILABLE" and len(s.calls) == calls


@pytest.mark.parametrize("crash", (False, True), ids=("fullcut", "crash"))
def test_normal_native_full_cut_or_crash_rotates_from_durable_mirror_attempt(configured_native_pool, crash):
    s = configured_native_pool
    s.mode = "full503"
    first = s.poll()
    assert first["status"] == "DEFERRED" and first["observed_count"] == 0
    assert s.hosts == ["aws"] and s.clock[0] == 159.
    receipt = json.loads(s.receipt.read_bytes())
    assert receipt["last_attempted_mirror"] == "aws" and receipt["status"] == "FAILED"
    if crash:
        receipt.update(status="RUNNING", attempt_finished_at=None, reason=None)
        s.receipt.write_text(json.dumps(receipt))  # Actual durable pre-GET marker left by an interrupted attempt.
    s.conn.close()
    s.conn = sqlite3.connect(s.paths.raw_root.parent / "normal-forecasts.db")
    s.conn.row_factory = sqlite3.Row
    s.args["conn"] = s.conn
    s.clock[0] += 60.
    s.hosts.clear()
    second = s.poll()
    assert second["status"] == "AVAILABLE" and second["observed_count"] == 102, second
    assert set(s.hosts) == {"google"}  # A fresh connection restores the cursor, not an in-memory latch.
    assert s.clock[0] == 219.


@pytest.mark.parametrize("diverged", (False, True), ids=("equal_prefix", "different_prefix"))
def test_normal_native_partial_mirror_prefix_keeps_original_clocks_and_can_reset(configured_native_pool, diverged):
    s = configured_native_pool
    s.mode = "partial503"
    first = s.poll()
    assert first["status"] == "INCOMPLETE" and first["observed_count"] == 1, first
    original = s.cache / "step000-member00.grib2"
    proof = original.with_suffix(".grib2.proof.json")
    original_bytes, proof_bytes = original.read_bytes(), proof.read_bytes()
    before = json.loads(Path(first["manifest_path"]).read_bytes())["retained_identities"][0]
    s.clock[0] += 60.
    s.google_fault = "prefix" if diverged else None
    second = s.poll()
    if diverged:
        assert second["status"] == "UNKNOWN" and second["reason"] == "NATIVE_2T_MIRROR_COHORT_DIVERGED", second
        assert len(list(s.cache.glob("step*-member*.grib2"))) == 1
        s.mode = "fast503"
        s.clock[0] += 60.
        repeated = s.poll()
        assert repeated["status"] == "UNKNOWN" and repeated["reason"] == "NATIVE_2T_MIRROR_COHORT_DIVERGED"
        receipt = json.loads(s.receipt.read_bytes())
        assert receipt["last_attempted_mirror"] == "google" and receipt["status"] == "FAILED"
        # Restore the original mirror normally; its valid cohort can drain
        # without deleting the conflicting replica or recapturing old bytes.
        s.mode = "healthy"
        s.clock[0] += 60.
        second = s.poll()
    assert second["status"] == "AVAILABLE" and second["observed_count"] == 102, second
    assert (original.read_bytes(), proof.read_bytes()) == (original_bytes, proof_bytes)
    after = json.loads(Path(second["manifest_path"]).read_bytes())["retained_identities"]
    assert next(row for row in after if (row["member"], row["step_hours"]) == (0, 0)) == before
    assert second["qualification_status"] == "UNKNOWN"


@pytest.mark.parametrize("debt", ("wrong_run", "receipt_alias", "stage_alias"))
def test_normal_native_mirror_diagnostic_debt_is_scoped_and_restorable(configured_native_pool, debt):
    s = configured_native_pool
    s.mode = "full503"
    assert s.poll()["status"] == "DEFERRED"
    saved = s.receipt.read_bytes()
    stage = s.cache / f".mirror-{s.module._resume_source_namespace('google')}.partial"
    if debt == "wrong_run":
        payload = json.loads(saved)
        payload["run_time_utc"] = (s.run - timedelta(hours=6)).isoformat()
        s.receipt.write_text(json.dumps(payload))
    elif debt == "receipt_alias":
        original = s.receipt.with_name("private-saved-diagnostic.json")
        s.receipt.rename(original)
        s.receipt.symlink_to(original)
    else:
        outside = s.cache.parent / "private-stage-target"
        outside.mkdir()
        stage.symlink_to(outside, target_is_directory=True)
    s.clock[0] += 60.
    s.hosts.clear()
    failed = s.poll()
    assert failed["status"] == "UNKNOWN", failed
    assert s.hosts == [] and not list(s.cache.glob("step*-member*.grib2"))
    if debt == "receipt_alias":
        s.receipt.unlink()
        original.rename(s.receipt)
    elif debt == "wrong_run":
        s.receipt.write_bytes(saved)
    else:
        stage.unlink()
    s.mode = "fast503"
    recovered = s.poll()
    assert recovered["status"] == "AVAILABLE" and recovered["observed_count"] == 102, recovered


def test_normal_native_google_wrong_bucket_does_not_publish_originals(configured_native_pool, monkeypatch):
    import ecmwf.opendata
    s = configured_native_pool
    client = ecmwf.opendata.Client
    class WrongBucketClient(client):
        def _get_urls(self, **kwargs):
            result = super()._get_urls(**kwargs)
            result.urls = [url.replace("/ecmwf-open-data/", "/private-other-bucket/") for url in result.urls]
            return result
    monkeypatch.setattr(ecmwf.opendata, "Client", WrongBucketClient)
    get = s.session.get
    s.session.get = lambda url, **kwargs: get(url.replace("/private-other-bucket/", "/ecmwf-open-data/"), **kwargs)
    result = s.poll()
    assert result["status"] == "UNKNOWN" and result["reason"] == "NATIVE_2T_SOURCE_HOST_INVALID", result
    assert not list(s.cache.glob("step*-member*.grib2"))
    assert s.conn.execute("SELECT COUNT(*) FROM source_run").fetchone()[0] == 0


def test_normal_native_mirror_switch_cannot_remint_tampered_prefix(configured_native_pool):
    s = configured_native_pool
    s.mode = "partial503"
    first = s.poll()
    assert first["observed_count"] == 1
    path = s.cache / "step000-member00.grib2"
    original, proof = path.read_bytes(), path.with_suffix(".grib2.proof.json").read_bytes()
    path.write_bytes(original[:-1] + b"0")
    s.clock[0] += 60.
    s.hosts.clear()
    rejected = s.poll()
    assert rejected["status"] == "UNKNOWN" and s.hosts == []
    assert path.with_suffix(".grib2.proof.json").read_bytes() == proof
    path.write_bytes(original)
    restored = s.poll()
    assert restored["status"] == "AVAILABLE" and restored["observed_count"] == 102, restored
    assert path.with_suffix(".grib2.proof.json").read_bytes() == proof


def test_normal_native_google_cache_expiry_never_refreshes_original_receipts(configured_native_pool):
    s = configured_native_pool
    before = s.poll()
    manifest = Path(before["manifest_path"]).read_bytes()
    originals = {path: path.read_bytes() for path in s.cache.glob("step*-member*.grib2*")}
    s.receipt.write_text("invalid diagnostic cannot authorize anything")
    s.hosts.clear()
    after = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": s.clock[0] - 1})
    assert after["status"] == "AVAILABLE" and s.hosts == []
    assert Path(after["manifest_path"]).read_bytes() == manifest
    assert {path: path.read_bytes() for path in originals} == originals
    assert after["qualification_status"] == "UNKNOWN"


@pytest.mark.parametrize("retained_prefix", (False, True), ids=("new_cohort", "old_prefix"))
def test_normal_native_interrupted_mirror_promotion_restores_exact_staged_proof(configured_native_pool, monkeypatch, retained_prefix):
    s = configured_native_pool
    if retained_prefix:
        s.mode = "partial503"
        assert s.poll()["observed_count"] == 1
        s.mode = "fast503"
        s.clock[0] += 60.
    member = "01" if retained_prefix else "00"
    link = s.module.os.link
    interrupted = [False]
    def interrupt(origin, destination, **kwargs):
        destination = Path(destination)
        if destination.parent == s.cache and destination.name == f"step000-member{member}.grib2.proof.json" and not interrupted[0]:
            interrupted[0] = True
            raise OSError("private promotion interruption")
        return link(origin, destination, **kwargs)
    monkeypatch.setattr(s.module.os, "link", interrupt)
    first = s.poll()
    assert first["status"] == "UNKNOWN" and interrupted[0]
    original = s.cache / f"step000-member{member}.grib2"
    assert original.exists() and not original.with_suffix(".grib2.proof.json").exists()
    stage = s.cache / f".mirror-{s.module._resume_source_namespace('google')}.partial"
    staged_proof = (stage / original.name).with_suffix(".grib2.proof.json").read_bytes()
    s.clock[0] += 60.
    second = s.poll()
    assert second["status"] == "AVAILABLE" and second["observed_count"] == 102, second
    assert original.with_suffix(".grib2.proof.json").read_bytes() == staged_proof


@pytest.mark.parametrize("debt", ("archive_role", "wrong_namespace"))
def test_normal_native_interrupted_publication_rejects_misplaced_proof_before_link(configured_native_pool, monkeypatch, debt):
    s = configured_native_pool
    link = s.module.os.link
    interrupted = [False]
    original = s.cache / "step000-member00.grib2"
    proof_path = original.with_suffix(".grib2.proof.json")
    def interrupt(origin, destination, **kwargs):
        if Path(destination) == proof_path and not interrupted[0]:
            interrupted[0] = True
            raise OSError("private promotion interruption")
        return link(origin, destination, **kwargs)
    monkeypatch.setattr(s.module.os, "link", interrupt)
    assert s.poll()["status"] == "UNKNOWN" and interrupted[0]
    body = original.read_bytes()
    google_stage = s.cache / f".mirror-{s.module._resume_source_namespace('google')}.partial"
    staged_proof = (google_stage / original.name).with_suffix(".grib2.proof.json")
    proof_bytes = staged_proof.read_bytes()
    aws_stage = s.cache / f".mirror-{s.module._resume_source_namespace('aws')}.partial"
    if debt == "archive_role":
        proof = json.loads(proof_bytes)
        proof["ingest_mode"] = "ARCHIVE_BACKFILL"
        staged_proof.write_text(json.dumps(proof))
        reason = "NATIVE_2T_ORIGIN_ROLE_CHANGED"
    else:
        for path in google_stage.iterdir():
            if path.is_file():
                (aws_stage / path.name).write_bytes(path.read_bytes())
        reason = "NATIVE_2T_MIRROR_STAGE_ORIGIN_INVALID"
    s.clock[0] += 60.
    s.hosts.clear()
    rejected = s.poll()
    assert rejected["status"] == "UNKNOWN" and reason in rejected["reason"], rejected
    assert not proof_path.exists() and original.read_bytes() == body and s.hosts == []
    # Restore exact evidence, retaining misplaced originals as forensics.
    if debt == "archive_role":
        staged_proof.write_bytes(proof_bytes)
    else:
        aws_stage.rename(s.cache / "private-misplaced-stage-forensics")
        aws_stage.mkdir()
    restored = s.poll()
    assert restored["status"] == "AVAILABLE" and restored["observed_count"] == 102, restored
    assert proof_path.read_bytes() == proof_bytes and original.read_bytes() == body


def test_normal_native_uncommitted_stage_does_not_permanently_block_healthy_replica(configured_native_pool):
    import eccodes as ec
    s = configured_native_pool
    s.mode = "full503"
    assert s.poll()["status"] == "DEFERRED"
    aws_stage = s.cache / f".mirror-{s.module._resume_source_namespace('aws')}.partial"
    orphan = aws_stage / "step000-member00.grib2"
    with (s.paths.raw_root.parent / "native-2t.grib2").open("rb") as handle:
        gid = ec.codes_grib_new_from_file(handle)
        try:
            orphan.write_bytes(ec.codes_get_message(gid))
        finally:
            ec.codes_release(gid)
    # Both endpoints have failed after a real AWS RUNNING marker. Returning
    # to AWS must record stage failure, not freeze the cursor at Google.
    attempt = json.loads(s.receipt.read_bytes())
    attempt.update(last_attempted_mirror="google", status="FAILED")
    s.receipt.write_text(json.dumps(attempt))
    s.clock[0] += 60.
    s.hosts.clear()
    failed = s.poll()
    assert failed["status"] == "UNKNOWN" and s.hosts == []
    assert json.loads(s.receipt.read_bytes())["last_attempted_mirror"] == "aws"
    original = orphan.read_bytes()
    recovered = s.poll()
    assert recovered["status"] == "AVAILABLE" and recovered["observed_count"] == 102, recovered
    assert orphan.read_bytes() == original and set(s.hosts) == {"google"}


def test_normal_native_expired_queue_reads_only_fully_verified_cache(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        first = s.module.collect_native_temperature_source(**s.args)
        assert first["status"] == "AVAILABLE"
        before = Path(first["manifest_path"]).read_bytes()
        s.calls.clear()
        s.args["cycle_deadline_monotonic"] = time.monotonic() - 1
        cached = s.module.collect_native_temperature_source(**s.args)
        assert cached["status"] == "AVAILABLE", cached
        assert s.calls == [] and Path(first["manifest_path"]).read_bytes() == before
        original = Path(json.loads(before)["messages"][0]["path"])
        original.write_bytes(original.read_bytes()[:-1] + b"0")
        damaged = s.module.collect_native_temperature_source(**s.args)
        assert damaged["status"] == "UNKNOWN", damaged
        assert s.calls == []
    finally:
        s.conn.close()


@pytest.fixture
def native_deadline_http():
    """Actual loopback only; no provider HTTP, production clock or credentials."""
    stop = threading.Event()
    state = SimpleNamespace(mode="valid", body=b"x" * 200, calls=0)
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        def log_message(self, *args):
            pass
        def do_GET(self):
            state.calls += 1
            try:
                if state.mode == "slow_headers":
                    for char in b"HTTP/1.1 206 Partial Content\r\nContent-Length: 200\r\n\r\n":
                        if stop.wait(.03):
                            return
                        self.wfile.write(bytes([char]))
                    return
                self.send_response(200 if state.mode == "ignored_range" else 206)
                chunked = state.mode in {"unknown_size", "ignored_range"}
                if chunked:
                    self.send_header("Transfer-Encoding", "chunked")
                else:
                    self.send_header("Content-Length", str(len(state.body)))
                requested = self.headers.get("Range", "bytes=0-199")[6:]
                self.send_header("Content-Range", "bytes " + ("1-200" if state.mode == "wrong_range" else requested) + "/100000")
                self.send_header("Content-Encoding", "gzip" if state.mode == "encoding" else "identity")
                self.send_header("Set-Cookie", "private_fixture_secret=must_not_capture")
                self.end_headers()
                if chunked:
                    for _ in range(32):
                        self.wfile.write(b"100\r\n" + b"x" * 256 + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                elif state.mode == "trickle":
                    for chunk in (state.body[:len(state.body) // 2], state.body[len(state.body) // 2:]):
                        if stop.wait(.45):
                            return
                        self.wfile.write(chunk)
                else:
                    self.wfile.write(state.body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                self.close_connection = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/original"
    try:
        yield state
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("mode", ("slow_headers", "trickle", "unknown_size", "ignored_range", "wrong_range", "encoding", "valid"))
def test_native_deadline_curl_actual_loopback_has_total_cut_and_body_cap(native_deadline_http, monkeypatch, mode):
    import requests
    from src.data import ecmwf_open_data as source
    state = native_deadline_http
    state.mode = mode
    session = source._NativeDeadlineSession()
    session.trust_env = False  # Private loopback, never a production-route change.
    run = source.subprocess.run
    output_sizes, commands = [], []
    def checked_run(command, **kwargs):
        commands.append(command)
        result = run(command, **kwargs)
        if "--output" in command:
            output = Path(command[command.index("--output") + 1])
            if output.exists():
                output_sizes.append(output.stat().st_size)
        return result
    monkeypatch.setattr(source.subprocess, "run", checked_run)
    started = time.monotonic()
    session._zeus_deadline = started + .6
    try:
        if mode in {"slow_headers", "trickle"}:
            with pytest.raises(requests.Timeout, match="STEP_DEADLINE_EXCEEDED"):
                session.get(state.url, headers={"Range": "bytes=0-199"})
            assert time.monotonic() - started < .8  # Includes bounded process-reap/CPU cleanup, not mathematical zero slack.
        elif mode in {"unknown_size", "ignored_range"}:
            with pytest.raises(requests.RequestException, match="TRANSFER_FAILED:63"):
                session.get(state.url, headers={"Range": "bytes=0-199"})
            assert output_sizes and max(output_sizes) <= 200
        elif mode == "encoding":
            with pytest.raises(ValueError, match="ENCODING_UNSUPPORTED"):
                session.get(state.url, headers={"Range": "bytes=0-199"})
        else:
            response = session.get(state.url, headers={"Range": "bytes=0-199"})
            if mode == "wrong_range":
                with pytest.raises(requests.RequestException):
                    source._validate_range_response(response, offset=0, length=200)
            else:
                source._validate_range_response(response, offset=0, length=200)
                assert response.content == state.body and response._native_body_complete
            assert "Set-Cookie" not in response.headers
            response.close()
        command = next(command for command in commands if "--output" in command)
        assert command[1] == "-q" and "--location" not in command and "--insecure" not in command
        assert "--cacert" in command and "Accept-Encoding: identity" in command
    finally:
        session.close()


def test_native_deadline_shared_bucket_wait_expires_without_starting_http(native_deadline_http):
    import requests
    from src.data import ecmwf_open_data as source
    bucket = source._fetch_bucket
    with bucket._lock:
        original = bucket._tokens, bucket._last_refill
        bucket._tokens, bucket._last_refill = 0., time.monotonic()
    session = source._NativeDeadlineSession()
    started = time.monotonic()
    session._zeus_deadline = started + .05
    try:
        with pytest.raises(requests.Timeout, match="STEP_DEADLINE_EXCEEDED"):
            session.get(native_deadline_http.url, headers={"Range": "bytes=0-199"})
        assert time.monotonic() - started < .2
        assert not session._curl_checked and native_deadline_http.calls == 0
    finally:
        session.close()
        with bucket._lock:
            bucket._tokens, bucket._last_refill = original


def test_native_deadline_actual_proxy_route_and_tls_are_preserved(native_deadline_http, monkeypatch):
    from src.data import ecmwf_open_data as source
    session = source._NativeDeadlineSession()
    session.trust_env = False
    session.proxies = {"http": native_deadline_http.url.replace("/original", "")}
    session._zeus_deadline = time.monotonic() + 2.
    run = source.subprocess.run
    observed = []
    def checked(command, **kwargs):
        if "--url" in command:
            observed.append((kwargs["env"].get("http_proxy"), command))
        return run(command, **kwargs)
    monkeypatch.setattr(source.subprocess, "run", checked)
    try:
        # This destination cannot resolve. Success proves the authorized local
        # HTTP proxy handled it; the native transport did not silently bypass.
        response = session.get("http://native-fixture.invalid/original", headers={"Range": "bytes=0-199"})
        assert response.status_code == 206 and response.content == native_deadline_http.body
        assert native_deadline_http.calls == 1
        assert observed[0][0] == session.proxies["http"]
        command = observed[0][1]
        assert session.proxies["http"] not in command and "--insecure" not in command
        assert "--cacert" in command and "--location" not in command
        with pytest.raises(ValueError, match="TLS_VERIFICATION_REQUIRED"):
            session.get(native_deadline_http.url, verify=False)
        assert native_deadline_http.calls == 1
        response.close()
    finally:
        session.close()


@pytest.mark.parametrize("failure", ("old_version", "missing"))
def test_native_deadline_unsupported_tool_cannot_fall_back_to_unbounded_requests(tmp_path, monkeypatch, failure):
    from src.data import ecmwf_open_data as source
    NativeSession = source._NativeDeadlineSession
    s = _normal_native_http(tmp_path, monkeypatch)
    commands = []
    def unavailable(command, **kwargs):
        commands.append(command)
        if failure == "missing":
            raise FileNotFoundError("private missing-tool fixture")
        return SimpleNamespace(returncode=0, stdout=b"curl 7.88.1", stderr=b"private stderr must not enter source reason")
    monkeypatch.setattr(source, "_NativeDeadlineSession", NativeSession)
    monkeypatch.setattr(source.subprocess, "run", unavailable)
    try:
        result = source.collect_native_temperature_source(**s.args)
        assert result["status"] == "DEFERRED" and result["inventory_status"] == "UNKNOWN", result
        assert "BOUNDED_HTTP_UN" in result["reason"] and "private stderr" not in result["reason"]
        assert len(commands) == 1 and commands[0] == ["/usr/bin/curl", "-q", "--version"]
        assert s.calls == []
        assert s.conn.execute("SELECT COUNT(*) FROM source_run").fetchone()[0] == 0
    finally:
        s.conn.close()


@pytest.mark.parametrize("mode", ("slow_headers", "trickle"))
def test_normal_native_actual_total_http_failure_can_resume_next_poll(tmp_path, monkeypatch, native_deadline_http, mode):
    from src.data import ecmwf_open_data as source
    NativeSession = source._NativeDeadlineSession
    s = _normal_native_http(tmp_path, monkeypatch)
    state = native_deadline_http
    state.mode = mode
    original_get = s.session.get
    bounded = NativeSession()
    bounded.trust_env = False
    injected = [False]
    def get(url, **kwargs):
        original = original_get(url, **kwargs)
        if not injected[0] and "Range" in kwargs.get("headers", {}):
            injected[0] = True
            state.body = original.body
            bounded._zeus_deadline = s.session._zeus_deadline
            return bounded.get(state.url, **kwargs)
        return original
    s.session.get = get
    started = time.monotonic()
    try:
        first = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": started + .6})
        assert first["status"] == "DEFERRED" and first["observed_count"] == 0, first
        assert time.monotonic() - started < .8
        assert not list(s.paths.raw_root.rglob("step*-member*.grib2"))
        s.session.get = original_get
        complete = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": time.monotonic() + 30})
        assert complete["status"] == "AVAILABLE" and complete["observed_count"] == 102, complete
        assert complete["qualification_status"] == "UNKNOWN" and complete["source_issued_at"] is None
    finally:
        bounded.close()
        s.conn.close()


def test_normal_native_one_part_resume_preserves_first_possession(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch)
    clock = [10.]
    monkeypatch.setattr(s.module.time, "monotonic", lambda: clock[0])
    original_get = s.session.get
    def get(url, **kwargs):
        response = original_get(url, **kwargs)
        if not url.endswith(".index"):
            clock[0] = 49.
        return response
    monkeypatch.setattr(s.session, "get", get)
    try:
        # Completion of the first valid part spends the poll, not its clock.
        def priority():
            if any("Range" in c[1].get("headers", {}) for c in s.calls):
                clock[0] = 50.
            return True
        first = s.module.collect_native_temperature_source(**{**s.args, "_priority": priority,
            "cycle_deadline_monotonic": 50.})
        assert first["status"] == "INCOMPLETE", first
        assert first["observed_count"] == 1
        manifest = Path(first["manifest_path"])
        saved = json.loads(manifest.read_bytes())["messages"][0]
        retained = Path(saved["path"]).read_bytes()
        proof = Path(saved["path"]).with_suffix(".grib2.proof.json").read_bytes()
        monkeypatch.setattr(s.session, "get", original_get)
        s.calls.clear()
        clock[0] = 60.
        final = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": 100.})
        assert final["status"] == "AVAILABLE", final
        assert Path(saved["path"]).read_bytes() == retained
        assert Path(saved["path"]).with_suffix(".grib2.proof.json").read_bytes() == proof
        current = next(m for m in json.loads(manifest.read_bytes())["messages"]
            if (m["member"], m["step_hours"]) == (saved["member"], saved["step_hours"]))
        assert current == saved
        assert len([c for c in s.calls if "Range" in c[1].get("headers", {})]) == 101
    finally:
        s.conn.close()


@pytest.mark.parametrize("cause", ("priority", "expired", "503"))
def test_normal_native_defer_is_bounded_and_next_poll_resets(tmp_path, monkeypatch, cause):
    s = _normal_native_http(tmp_path, monkeypatch)
    original_get = s.session.get
    failed = [False]
    def get(url, **kwargs):
        if not failed[0]:
            failed[0] = True
            s.calls.append((url, kwargs))
            raise __import__("requests").HTTPError("private HTTP 503",
                response=SimpleNamespace(status_code=503))
        return original_get(url, **kwargs)
    args = dict(s.args)
    if cause == "priority":
        args["_priority"] = lambda: False
    elif cause == "expired":
        args["cycle_deadline_monotonic"] = time.monotonic() - 1
    else:
        monkeypatch.setattr(s.session, "get", get)
    try:
        result = s.module.collect_native_temperature_source(**args)
        assert result["status"] == "DEFERRED", result
        assert len(s.calls) == (1 if cause == "503" else 0)
        final = s.module.collect_native_temperature_source(**s.args)
        assert final["status"] == "AVAILABLE", final
    finally:
        s.conn.close()


@pytest.mark.parametrize("fault", ("body", "index", "index_receipt", "clock", "role"))
def test_normal_native_retained_tamper_never_remints(tmp_path, monkeypatch, fault):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        source = s.module.collect_native_temperature_source(**s.args)
        saved = json.loads(Path(source["manifest_path"]).read_bytes())["messages"][0]
        body = Path(saved["path"])
        proof_path = body.with_suffix(".grib2.proof.json")
        proof = json.loads(proof_path.read_bytes())
        if fault == "body":
            body.write_bytes(body.read_bytes()[:-1] + b"0")
        elif fault == "index":
            (body.parent / proof["index_path"]).write_bytes(b"{}\n")
        elif fault == "index_receipt":
            s.module._native_index_receipt_path(body.parent / proof["index_path"], proof["index_receipt_sha256"]).write_bytes(b"{}\n")
        else:
            proof["source_fetched_at" if fault == "clock" else "ingest_mode"] = (
                "2099-01-01T00:00:00+00:00" if fault == "clock" else "ARCHIVE_BACKFILL")
            proof_path.write_text(json.dumps(proof))
        s.calls.clear()
        result = s.module.collect_native_temperature_source(**s.args)
        assert result["status"] in {"INCOMPLETE", "UNKNOWN"}, result
        assert s.calls == []
        assert result["qualification_status"] == "UNKNOWN"
    finally:
        s.conn.close()


@pytest.mark.parametrize("malformed", (None, True, 3, [], {}), ids=("null", "bool", "int", "list", "dict"))
def test_normal_native_malformed_receipt_hash_is_scoped_unknown_and_resets(tmp_path, monkeypatch, malformed):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        first = s.module.collect_native_temperature_source(**s.args)
        assert first["status"] == "AVAILABLE", first
        manifest = Path(first["manifest_path"])
        saved = json.loads(manifest.read_bytes())["messages"][0]
        proof_path = Path(saved["path"]).with_suffix(".grib2.proof.json")
        original = proof_path.read_bytes()
        before = tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (first["source_run_id"],)).fetchone())
        proof = json.loads(original); proof["index_receipt_sha256"] = malformed
        proof_path.write_text(json.dumps(proof))
        s.calls.clear()
        refused = s.module.collect_native_temperature_source(**s.args)
        assert refused["status"] == "UNKNOWN" and refused["reason"] == "NATIVE_2T_ORIGINAL_INDEX_RECEIPT_INVALID", refused
        assert s.calls == [] and refused["qualification_status"] == "UNKNOWN"
        inputs_dir = tmp_path / "malformed-readback"; inputs_dir.mkdir()
        inputs = _native_temperature_knots_fixture(inputs_dir, steps=(0, 3))
        scope_args = (s.conn, SimpleNamespace(source_run_id=first["source_run_id"]), manifest, inputs)
        scope = _native_source_scope(*scope_args)
        assert scope.status == "UNKNOWN" and scope.available_at is None, scope
        assert tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (first["source_run_id"],)).fetchone()) == before
        proof_path.write_bytes(original)
        assert _native_source_scope(*scope_args).status == "AVAILABLE"
        assert s.module.collect_native_temperature_source(**s.args)["status"] == "AVAILABLE" and s.calls == []
        assert proof_path.read_bytes() == original
    finally:
        s.conn.close()


def test_normal_native_six_hour_knots_are_not_hourly_or_prior_observations(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch, steps=(144, 150))
    try:
        result = s.module.collect_native_temperature_source(**s.args)
        assert result["status"] == "AVAILABLE", result
        directory = tmp_path / "scope"
        directory.mkdir()
        inputs = _native_temperature_knots_fixture(directory, steps=(144, 150))
        scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=result["source_run_id"]),
            Path(result["manifest_path"]), inputs,
            qualified_prefix_cut_utc=s.run + timedelta(hours=145))
        assert scope.status == "AVAILABLE", scope
        assert {k["step_hours"] for k in scope.native_knots} == {144, 150}
        assert scope.temporal_representation == "acquired_native_knots"
        assert scope.available_at is None and scope.qualification_status == "UNKNOWN"
        assert scope.projection_status == "NOT_PERFORMED" and scope.extrema_status == "NOT_COMPUTED"
    finally:
        s.conn.close()


def test_normal_native_singleflight_defers_only_itself_and_releases(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        s.module._native_temperature_source_lock.acquire()
        try:
            first = s.module.collect_native_temperature_source(**s.args)
            assert first["reason"] == "NATIVE_2T_SINGLEFLIGHT_BUSY"
            assert s.calls == []
        finally:
            s.module._native_temperature_source_lock.release()
        assert s.module.collect_native_temperature_source(**s.args)["status"] == "AVAILABLE"
        assert not s.module._native_temperature_source_lock.locked()
    finally:
        s.conn.close()


def test_normal_native_cannot_overwrite_archive_source_identity(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        result = s.module.collect_native_temperature_source(**s.args)
        scheduled_row = dict(s.conn.execute("SELECT * FROM source_run").fetchone())
        archive_id = scheduled_row["source_run_id"].removesuffix(":origin:scheduled_live")
        write_source_run(s.conn, source_run_id=archive_id, source_id="ecmwf_open_data",
            track="2t_instant_native_knots", release_calendar_key="ecmwf_open_data",
            source_cycle_time=s.run, ingest_mode="ARCHIVE_BACKFILL", origin_mode="ARCHIVE_BACKFILL",
            status="PARTIAL", completeness_status="PARTIAL", partial_run=True, manifest_hash="archive-immutable")
        s.conn.commit()
        archive_before = dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (archive_id,)).fetchone())
        again = s.module.collect_native_temperature_source(**s.args)
        assert again["source_run_id"] == result["source_run_id"]
        assert dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (archive_id,)).fetchone()) == archive_before
        with pytest.raises(ValueError, match="NATIVE_2T_ORIGIN_ROLE_CHANGED"):
            s.module.persist_native_temperature_source_run(s.conn,
                cache_dir=Path(result["manifest_path"]).parent, manifest_path=Path(result["manifest_path"]),
                expected_run_utc=s.run, product_steps=[0, 3])
        # Even a colliding row tagged archive at the scheduled ID is immutable.
        s.conn.execute("UPDATE source_run SET ingest_mode='ARCHIVE_BACKFILL', origin_mode='ARCHIVE_BACKFILL' "
            "WHERE source_run_id=?", (result["source_run_id"],))
        s.conn.commit()
        conflict_before = dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?",
            (result["source_run_id"],)).fetchone())
        conflict = s.module.collect_native_temperature_source(**s.args)
        assert conflict["status"] == "UNKNOWN"
        assert dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?",
            (result["source_run_id"],)).fetchone()) == conflict_before
    finally:
        s.conn.close()


def test_normal_native_close_failure_cannot_freeze_singleflight(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch)
    close = s.session.close
    def fail_close():
        raise OSError("private close failure")
    monkeypatch.setattr(s.session, "close", fail_close)
    try:
        with pytest.raises(OSError, match="private close failure"):
            s.module.collect_native_temperature_source(**s.args)
        assert not s.module._native_temperature_source_lock.locked()
        monkeypatch.setattr(s.session, "close", close)
        assert s.module.collect_native_temperature_source(**s.args)["status"] == "AVAILABLE"
    finally:
        s.conn.close()


@pytest.mark.parametrize("fault", ("manifest_missing", "manifest_and_original_missing", "manifest_unbound"))
def test_normal_native_canonical_anchor_prevents_remint_and_restores(tmp_path, monkeypatch, fault):
    s = _normal_native_http(tmp_path, monkeypatch)
    try:
        first = s.module.collect_native_temperature_source(**s.args)
        manifest = Path(first["manifest_path"])
        manifest_bytes = manifest.read_bytes()
        original = json.loads(manifest_bytes)["messages"][0]
        body = Path(original["path"])
        proof = body.with_suffix(".grib2.proof.json")
        body_bytes, proof_bytes = body.read_bytes(), proof.read_bytes()
        before = dict(s.conn.execute("SELECT * FROM source_run").fetchone())
        if fault == "manifest_unbound":
            changed = json.loads(manifest_bytes)
            changed["retained_identities"] = []
            manifest.write_text(json.dumps(changed))
        else:
            manifest.unlink()
        if fault == "manifest_and_original_missing":
            body.unlink()
            proof.unlink()
        s.calls.clear()
        refused = s.module.collect_native_temperature_source(**s.args)
        assert refused["status"] == "UNKNOWN", refused
        assert s.calls == []
        assert dict(s.conn.execute("SELECT * FROM source_run").fetchone()) == before
        # Restore exact known originals, never new clocks or a freshly fetched substitute.
        manifest.write_bytes(manifest_bytes)
        body.write_bytes(body_bytes)
        proof.write_bytes(proof_bytes)
        restored = s.module.collect_native_temperature_source(**s.args)
        assert restored["status"] == "AVAILABLE", restored
        assert restored["source_run_id"] == first["source_run_id"]
        assert s.calls == []
        assert json.loads(manifest.read_bytes())["messages"][0] == original
        assert s.conn.execute("SELECT manifest_hash FROM source_run").fetchone()[0] == before["manifest_hash"]
        scope_dir = tmp_path / "restore-scope"
        scope_dir.mkdir()
        inputs = _native_temperature_knots_fixture(scope_dir, steps=(0, 3))
        scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=first["source_run_id"]), manifest, inputs)
        assert scope.status == "AVAILABLE", scope
        assert scope.qualification_status == "UNKNOWN" and scope.available_at is None
    finally:
        s.conn.close()


def test_normal_native_legitimate_step_append_preserves_old_first_clocks(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch, steps=(0, 3, 6))
    try:
        first = s.module.collect_native_temperature_source(**{**s.args, "required_steps": [0, 3]})
        manifest = Path(first["manifest_path"])
        before = json.loads(manifest.read_bytes())
        old = {(m["member"], m["step_hours"]): m for m in before["messages"]}
        s.calls.clear()
        appended = s.module.collect_native_temperature_source(**s.args)
        assert appended["status"] == "AVAILABLE", appended
        assert appended["source_run_id"] == first["source_run_id"]
        after = json.loads(manifest.read_bytes())
        assert after["product_steps"] == [0, 3, 6]
        assert len(after["messages"]) == 153
        assert all(saved == old[(saved["member"], saved["step_hours"])]
            for saved in after["messages"] if saved["step_hours"] != 6)
        assert len([c for c in s.calls if "Range" in c[1].get("headers", {})]) == 51
        assert s.conn.execute("SELECT manifest_hash FROM source_run").fetchone()[0] == hashlib.sha256(manifest.read_bytes()).hexdigest()
        assert hashlib.sha256(json.dumps(before, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != hashlib.sha256(manifest.read_bytes()).hexdigest()
    finally:
        s.conn.close()


def test_normal_native_complete_target_subset_does_not_wait_for_other_append(tmp_path, monkeypatch):
    s = _normal_native_http(tmp_path, monkeypatch, steps=(0, 3, 6))
    try:
        first = s.module.collect_native_temperature_source(**{**s.args, "required_steps": [0, 3]})
        clock = [100.]
        monkeypatch.setattr(s.module.time, "monotonic", lambda: clock[0])
        get = s.session.get
        spent = [False]
        def partial_get(url, **kwargs):
            if spent[0] and url.endswith(".index"):
                clock[0] += 1.
            response = get(url, **kwargs)
            if "Range" in kwargs.get("headers", {}):
                spent[0] = True
                clock[0] += 58.
            return response
        s.session.get = partial_get
        partial = s.module.collect_native_temperature_source(**{**s.args, "cycle_deadline_monotonic": 159.})
        assert partial["status"] == "INCOMPLETE"
        manifest = Path(first["manifest_path"])
        before = manifest.read_bytes()
        s.calls.clear()
        subset = s.module.collect_native_temperature_source(**{**s.args,
            "required_steps": [0, 3], "cycle_deadline_monotonic": 159.})
        assert subset["status"] == "AVAILABLE", subset
        assert subset["required_steps"] == [0, 3] and subset["missing_count"] == 0
        assert manifest.read_bytes() == before and s.calls == []
        row = s.conn.execute("SELECT * FROM source_run").fetchone()
        assert (row["status"], row["completeness_status"]) == ("PARTIAL", "PARTIAL")
        scope_dir = tmp_path / "target-subset"
        scope_dir.mkdir()
        inputs = _native_temperature_knots_fixture(scope_dir, steps=(0, 3))
        scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=subset["source_run_id"]), manifest, inputs)
        assert scope.status == "AVAILABLE" and len(scope.native_knots) == 102
        assert scope.available_at is None and scope.qualification_status == "UNKNOWN"
    finally:
        s.conn.close()


def _native_source_cache_fixture(tmp_path, *, steps=(0, 3, 6), missing=()):
    """Retained synthetic originals, never a network or real-provider claim."""
    inputs = _native_temperature_knots_fixture(tmp_path, steps=steps)
    cache = tmp_path / "source-cache"
    cache.mkdir()
    for offset, evidence in inputs["message_source_evidence"].items():
        row = json.loads(evidence["original_index_bytes"])
        member, step = int(row.get("number", 0)), int(row["step"])
        if (member, step) in missing:
            continue
        path = cache / f"step{step:03d}-member{member:02d}.grib2"
        path.write_bytes(evidence["original_range_bytes"])
        index_path = path.with_suffix(".index.body")
        index_path.write_bytes(evidence["original_index_bytes"])
        proof = {key: value for key, value in evidence.items()
                 if key not in ("original_index_bytes", "original_range_bytes")}
        proof.update(member=member, step_hours=step, index_path=index_path.name,
                     range_http={"status": 206, "headers": {
                         "Content-Length": str(len(evidence["original_range_bytes"])),
                         "Content-Range": f"bytes {row['_offset']}-{row['_offset']+row['_length']-1}/{row['_offset']+row['_length']}"}})
        path.with_suffix(".grib2.proof.json").write_text(json.dumps(proof))
    conn = sqlite3.connect(tmp_path / "private-forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    conn.commit()
    return inputs, cache, conn, tmp_path / "native-source-manifest.json"


def test_native_source_archive_missing_manifest_cannot_replace_canonical_inventory(tmp_path):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path, steps=(0, 3))
    try:
        args = dict(cache_dir=cache, manifest_path=manifest,
            expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3])
        first = module.persist_native_temperature_source_run(conn, **args)
        manifest_bytes = manifest.read_bytes()
        before = dict(conn.execute("SELECT * FROM source_run").fetchone())
        original = json.loads(manifest_bytes)["messages"][0]
        body = Path(original["path"])
        proof = body.with_suffix(".grib2.proof.json")
        body_bytes, proof_bytes = body.read_bytes(), proof.read_bytes()
        manifest.unlink()
        body.unlink()
        proof.unlink()
        with pytest.raises(ValueError, match="NATIVE_2T_CANONICAL_MANIFEST_MISSING"):
            module.persist_native_temperature_source_run(conn, **args)
        assert dict(conn.execute("SELECT * FROM source_run").fetchone()) == before
        manifest.write_bytes(manifest_bytes)
        body.write_bytes(body_bytes)
        proof.write_bytes(proof_bytes)
        restored = module.persist_native_temperature_source_run(conn, **args)
        assert restored.source_run_id == first.source_run_id
        assert restored.status == "AVAILABLE"
        assert json.loads(manifest.read_bytes())["messages"][0] == original
    finally:
        conn.close()


def test_native_source_partial_is_nullable_quantity_not_extrema(tmp_path):
    from src.data import ecmwf_open_data as module

    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path, missing=((50, 6),))
    try:
        result = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        row = conn.execute("SELECT * FROM source_run").fetchone()
        assert result.status == "INCOMPLETE"
        assert (row["ingest_mode"], row["origin_mode"]) == ("ARCHIVE_BACKFILL", "ARCHIVE_BACKFILL")
        assert (row["status"], row["completeness_status"], row["partial_run"]) == ("PARTIAL", "PARTIAL", 1)
        assert row["temperature_metric"] is None
        assert row["physical_quantity"] == "native_2m_temperature_instantaneous_knots"
        assert row["source_issue_time"] is None and row["source_release_time"] is None
        assert row["source_available_at"] is None
        assert row["observed_count"] == 152
        assert conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 0
    finally:
        conn.close()


def _native_source_scope(conn, source, manifest, inputs, **changes):
    from src.data import ecmwf_open_data as module
    args = dict(source_run_id=source.source_run_id, manifest_path=manifest,
        required_steps=inputs["required_steps"], qualified_prefix_cut_utc=inputs["expected_run_utc"] + timedelta(hours=1),
        local_day_end_utc=inputs["expected_run_utc"] + timedelta(hours=max(inputs["required_steps"])),
        explicit_manifest=inputs["explicit_manifest"], mask_grib_path=inputs["mask_grib_path"],
        mask_proof_path=inputs["mask_proof_path"])
    args.update(changes)
    return module.read_native_temperature_scope(conn, **args)


def test_normal_land_mask_cache_keeps_original_clock_without_http(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module
    import ecmwf.opendata
    inputs = _native_temperature_knots_fixture(tmp_path)
    path, proof_path = inputs["mask_grib_path"], inputs["mask_proof_path"]
    # Existing normal cache shape (proof beside body), including its original
    # possession, must never be replaced just because a new poll is later.
    path.with_suffix(".proof.json").write_bytes(proof_path.read_bytes())
    before = path.read_bytes(), path.with_suffix(".proof.json").read_bytes()
    class NoHTTP:
        def __init__(self, **kwargs):
            raise AssertionError("existing original must not be downloaded again")
    monkeypatch.setattr(ecmwf.opendata, "Client", NoHTTP)
    proof = module._fetch_cycle_land_mask(cycle_date=inputs["expected_run_utc"].date(),
        cycle_hour=0, output_path=path)
    assert proof["source_fetched_at"] == (inputs["expected_run_utc"] + timedelta(hours=1)).isoformat()
    assert (path.read_bytes(), path.with_suffix(".proof.json").read_bytes()) == before


def _physical_static_originals(inputs, *, directory=None, track="mx2t6_high"):
    """Enrich only private synthetic originals with their actual index entities."""
    import eccodes as ec
    run = inputs["expected_run_utc"]
    url = f"https://data.ecmwf.int/forecasts/{run:%Y%m%d}/{run:%H}z/ifs/0p25/oper/{run:%Y%m%d%H}0000-0h-oper-fc.grib2"
    mask = inputs["mask_grib_path"] if directory is None else directory / f".{track}_{run:%Y%m%d}_{run:%H}z_lsm.grib2"
    mask.parent.mkdir(parents=True, exist_ok=True)
    phi = mask.with_suffix(".z.grib2")
    messages = []
    for param, source in (("lsm", inputs["mask_grib_path"]), ("z", inputs["surface_geopotential_grib_path"])):
        gid = ec.codes_new_from_message(source.read_bytes())
        try:
            ec.codes_set(gid, "typeOfGeneratingProcess", 2)
            ec.codes_set(gid, "dataType", "fc")
            ec.codes_set(gid, "generatingProcessIdentifier", 161)
            body = ec.codes_get_message(gid)
        finally:
            ec.codes_release(gid)
        target = mask if param == "lsm" else phi
        target.write_bytes(body)
        messages.append((param, target, body))
    index = b"".join(json.dumps(dict(param=param, levtype="sfc", stream="oper", type="fc",
        date=run.strftime("%Y%m%d"), time=run.strftime("%H%M"), step="0", **{"class": "od"},
        _offset=offset, _length=len(body))).encode() + b"\n"
        for offset, (param, _, body) in zip((0, 512), messages))
    for offset, (param, path, body) in zip((0, 512), messages):
        proof = dict(source=f"ecmwf_open_data_ifs_oper_fc_step0_{param}", source_url=url,
            source_index_url=url[:-6] + ".index", source_cycle_time=run.isoformat(),
            source_fetched_at=(run + timedelta(hours=1)).isoformat(),
            source_index_offset=offset, source_index_length=len(body),
            source_index_sha256=hashlib.sha256(index).hexdigest(),
            audit_scope="AUDIT_ONLY_NOT_DECISION_INPUT", source_issued_at=None,
            **{"mask_sha256" if param == "lsm" else "raw_message_sha256": hashlib.sha256(body).hexdigest()})
        path.with_suffix(".proof.json").write_text(json.dumps(proof))
        path.with_suffix(".index.body").write_bytes(index)
    inputs.update(mask_grib_path=mask, mask_proof_path=mask.with_suffix(".proof.json"),
        surface_geopotential_grib_path=phi, surface_geopotential_proof_path=phi.with_suffix(".proof.json"))
    return mask, phi


@pytest.mark.parametrize("metric,role", (("high", "remaining_X"), ("low", "full_Y")))
def test_native_physical_scope_strict_pit_and_honest_station_unknown(tmp_path, metric, role):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    try:
        _physical_static_originals(inputs)
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        first = inputs["expected_run_utc"] + timedelta(hours=1)
        args = dict(surface_geopotential_grib_path=inputs["surface_geopotential_grib_path"],
            surface_geopotential_proof_path=inputs["surface_geopotential_proof_path"],
            metric=metric, role=role, local_day_start_utc=inputs["expected_run_utc"])
        before = _native_source_scope(conn, source, manifest, inputs, decision_at_utc=first, **args)
        after = _native_source_scope(conn, source, manifest, inputs, decision_at_utc=first + timedelta(microseconds=1), **args)
        assert before.pit_status == "AFTER_DECISION", before
        assert after.pit_status == "AVAILABLE", after
        rewound = _native_source_scope(conn, source, manifest, inputs, decision_at_utc=first, **args)
        assert rewound.pit_status == "AFTER_DECISION", rewound
        assert after.physical_dependency_available_at == after.temperature_scope_first_possession_at == first.isoformat()
        assert after.available_at is None and after.qualification_status == "OFFLINE_ONLY"
        witness = after.physical_witness
        assert witness["quantity"] == dict(param_id=167, units="K", height_agl_m=2, step_type="instant")
        assert witness["station_equivalence_status"] == witness["sensor_agl_status"] == "UNKNOWN"
        assert all(d["original_role"] == "AUDIT_ONLY_NOT_DECISION_INPUT" for d in witness["static_dependencies"])
        point = witness["selected_cities"]["London"]
        assert point["selected_flat_index"] == 2 and point["selected_land_fraction"] == pytest.approx(.8)
        assert [p["raw_phi_m2_s2"] for p in point["four_neighbors"]] == [100., 200., 313.75, 400.]
    finally:
        conn.close()


def test_native_primitive_warm_scope_rechecks_originals_and_restores_old_clock(tmp_path):
    from src.data import ecmwf_open_data as module
    from scripts import extract_open_ens_localday as decoder

    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    try:
        _physical_static_originals(inputs)
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        decision = inputs["expected_run_utc"]+timedelta(hours=1, microseconds=1)
        args = dict(surface_geopotential_grib_path=inputs["surface_geopotential_grib_path"],
            surface_geopotential_proof_path=inputs["surface_geopotential_proof_path"],
            metric="high", decision_at_utc=decision)
        before = _native_source_scope(conn, source, manifest, inputs, **args)
        assert before.status == "AVAILABLE" and before.pit_status == "AVAILABLE", before
        assert decoder._NATIVE_ORIGINAL_DECODE and decoder._NATIVE_ORIGINAL_INDEX_DECODE
        rows = tuple(tuple(row) for row in conn.execute("SELECT * FROM source_run"))
        path = cache/"step006-member50.grib2"
        proof_path = path.with_suffix(".grib2.proof.json")
        proof = proof_path.read_bytes()
        index_path = cache/json.loads(proof)["index_path"]
        originals = {item:item.read_bytes() for item in (path, proof_path, index_path)}
        for fault in ("body", "index", "clock"):
            if fault == "body":
                path.write_bytes(originals[path][:-1]+b"0")
            elif fault == "index":
                index_path.write_bytes(b"{}\n")
            else:
                changed = json.loads(proof)
                changed["source_fetched_at"] = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
                proof_path.write_text(json.dumps(changed))
            refused = _native_source_scope(conn, source, manifest, inputs, **args)
            assert refused.status == "UNKNOWN" and refused.native_knots == (), (fault, refused)
            for item, body in originals.items():
                item.write_bytes(body)
            reset = _native_source_scope(conn, source, manifest, inputs, **args)
            assert reset == before, (fault, reset)
            assert rows == tuple(tuple(row) for row in conn.execute("SELECT * FROM source_run"))
    finally:
        conn.close()


@pytest.mark.parametrize("fault", ("missing", "tamper", "prior_run", "process", "process_id", "grid", "quantity"))
def test_native_physical_dependency_gap_preserves_temperature_clock_and_resets(tmp_path, fault):
    from src.data import ecmwf_open_data as module
    import eccodes as ec
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    try:
        mask, phi = _physical_static_originals(inputs)
        committed = {p: p.read_bytes() for p in (phi, phi.with_suffix(".proof.json"), phi.with_suffix(".index.body"))}
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        body = phi.read_bytes()
        if fault == "missing":
            phi.unlink()
        elif fault == "tamper":
            phi.with_suffix(".index.body").write_bytes(b"tampered")
        else:
            gid = ec.codes_new_from_message(body)
            try:
                ec.codes_set(gid, {"prior_run": "dataDate", "process": "typeOfGeneratingProcess", "grid": "longitudeOfFirstGridPointInDegrees",
                    "quantity": "paramId", "process_id": "generatingProcessIdentifier"}[fault],
                    {"prior_run": 20261002, "process": 4, "grid": .125, "quantity": 172, "process_id": 158}[fault])
                phi.write_bytes(ec.codes_get_message(gid))
            finally:
                ec.codes_release(gid)
            changed_proof = json.loads(phi.with_suffix(".proof.json").read_bytes())
            changed_proof["raw_message_sha256"] = hashlib.sha256(phi.read_bytes()).hexdigest()
            phi.with_suffix(".proof.json").write_text(json.dumps(changed_proof))
        decision = inputs["expected_run_utc"] + timedelta(hours=2)
        gap = _native_source_scope(conn, source, manifest, inputs, decision_at_utc=decision, metric="high")
        assert gap.status == "AVAILABLE" and gap.pit_status == "UNKNOWN", gap
        assert gap.temperature_scope_first_possession_at == (inputs["expected_run_utc"] + timedelta(hours=1)).isoformat()
        for path, data in committed.items():
            path.write_bytes(data)
        restored = _native_source_scope(conn, source, manifest, inputs, decision_at_utc=decision, metric="high")
        assert restored.pit_status == "AVAILABLE", restored
    finally:
        conn.close()


@pytest.mark.parametrize("publication_failure", (False, True))
def test_normal_land_mask_uncommitted_orphan_drains_without_reclocking_commit(tmp_path, monkeypatch, publication_failure):
    from src.data import ecmwf_open_data as module
    import ecmwf.opendata
    seed = tmp_path / "seed"
    seed.mkdir()
    inputs = _native_temperature_knots_fixture(seed, steps=(0, 3))
    mask, _ = _physical_static_originals(inputs)
    body, index = mask.read_bytes(), mask.with_suffix(".index.body").read_bytes()
    url = json.loads(mask.with_suffix(".proof.json").read_bytes())["source_url"]
    calls = []
    class Response:
        def __init__(self, data, status, headers):
            self.data, self.status_code, self.headers = data, status, headers
        def iter_content(self, **kwargs):
            yield self.data
        def close(self):
            pass
    class Session:
        def get(self, request, **kwargs):
            calls.append(request)
            return Response(index, 200, {}) if request.endswith(".index") else Response(body, 206,
                {"Content-Length": str(len(body)), "Content-Range": f"bytes 0-{len(body)-1}/{len(body)}"})
    class Client:
        verify = True
        def __init__(self, **kwargs):
            self.session = Session()
        def _get_urls(self, **kwargs):
            return SimpleNamespace(urls=[url], for_index={"param": ["lsm"]})
    monkeypatch.setattr(ecmwf.opendata, "Client", Client)
    monkeypatch.setattr(module, "_RateLimitedSession", Session)
    dest = tmp_path / "normal-lsm.grib2"
    args = dict(cycle_date=inputs["expected_run_utc"].date(), cycle_hour=0, output_path=dest)
    if publication_failure:
        replace = module.os.replace
        def fail_proof(src, dst):
            if Path(dst) == dest.with_suffix(".proof.json"):
                raise OSError("private commit marker failure")
            return replace(src, dst)
        monkeypatch.setattr(module.os, "replace", fail_proof)
        with pytest.raises(ValueError, match="ENS_LAND_MASK_UNAVAILABLE"):
            module._fetch_cycle_land_mask(**args)
        monkeypatch.setattr(module.os, "replace", replace)
    else:
        dest.write_bytes(body)  # No commit marker or canonical possession exists.
    first = module._fetch_cycle_land_mask(**args)
    assert first["index_http"]["status"] == 200 and first["range_http"]["status"] == 206
    assert first["source_index_sha256"] == hashlib.sha256(index).hexdigest()
    assert datetime.fromisoformat(first["index_first_possession_at"]) <= datetime.fromisoformat(first["source_fetched_at"])
    before = tuple(p.read_bytes() for p in (dest, dest.with_suffix(".proof.json"), dest.with_suffix(".index.body")))
    count = len(calls)
    second = module._fetch_cycle_land_mask(**args)
    assert len(calls) == count
    assert first["source_fetched_at"] == second["source_fetched_at"]
    assert tuple(p.read_bytes() for p in (dest, dest.with_suffix(".proof.json"), dest.with_suffix(".index.body"))) == before
    assert list(tmp_path.glob("normal-lsm*.uncommitted-*"))


@pytest.mark.parametrize("track,metric", (("mx2t6_high", "high"), ("mn2t6_low", "low")))
def test_normal_native_physical_readback_uses_real_normal_static_cache(tmp_path, monkeypatch, track, metric):
    s = _normal_native_http(tmp_path, monkeypatch, steps=(0, 3))
    try:
        captured = s.module.collect_native_temperature_source(**s.args)
        scope_dir = tmp_path / "scope"
        scope_dir.mkdir()
        inputs = _native_temperature_knots_fixture(scope_dir, steps=(0, 3))
        normal_directory = s.module._download_output_path(run_date=s.run.date(), run_hour=s.run.hour,
            param="2t", raw_root=s.paths.raw_root).parent
        mask, phi = _physical_static_originals(inputs, directory=normal_directory, track=track)
        # The dependency producer is the existing normal land acquisition; its
        # second call is real cache readback, not a mocked qualification.
        committed = mask.read_bytes(), mask.with_suffix(".proof.json").read_bytes()
        s.module._fetch_cycle_land_mask(cycle_date=s.run.date(), cycle_hour=s.run.hour, output_path=mask)
        assert (mask.read_bytes(), mask.with_suffix(".proof.json").read_bytes()) == committed
        manifest = Path(captured["manifest_path"])
        first_records = json.loads(manifest.read_bytes())["messages"]
        args = dict(source_run_id=captured["source_run_id"], manifest_path=manifest,
            required_steps=[0, 3], qualified_prefix_cut_utc=s.run + timedelta(hours=1),
            local_day_end_utc=s.run + timedelta(hours=3), explicit_manifest=inputs["explicit_manifest"],
            decision_at_utc=datetime.now(timezone.utc), metric=metric, _paths=s.paths)
        result = s.module.read_native_temperature_scope(s.conn, **args)
        assert result.status == result.pit_status == "AVAILABLE", result
        assert result.physical_witness["origin_mode"] == "SCHEDULED_LIVE"
        assert result.qualification_status == "UNKNOWN" and result.available_at is None
        assert len(result.native_knots) == 102
        assert result.physical_witness["static_dependencies"][0]["proof"]["source_fetched_at"] == (s.run + timedelta(hours=1)).isoformat()
        s.args["cycle_deadline_monotonic"] = time.monotonic() + 30
        count = len(s.calls)
        again = s.module.collect_native_temperature_source(**s.args)
        assert len(s.calls) == count
        assert json.loads(manifest.read_bytes())["messages"] == first_records
        later = s.module.read_native_temperature_scope(s.conn, **args)
        assert later.temperature_scope_first_possession_at == result.temperature_scope_first_possession_at
        assert later.physical_dependency_available_at == result.physical_dependency_available_at
    finally:
        s.conn.close()


def test_native_source_full_scope_real_knots_and_no_geophysical_clock_upgrade(tmp_path):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    try:
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        result = _native_source_scope(conn, source, manifest, inputs)
        assert source.status == result.status == "AVAILABLE", result
        row = conn.execute("SELECT * FROM source_run").fetchone()
        assert (row["ingest_mode"], row["origin_mode"]) == ("ARCHIVE_BACKFILL", "ARCHIVE_BACKFILL")
        assert len(result.native_knots) == 153
        assert {k["step_hours"] for k in result.native_knots} == {0, 3, 6}
        assert all(k["selected_point"]["selected_flat_index"] == 2 for k in result.native_knots)
        assert all(k["surface_class"] == "MIXED_LAND_WATER" for k in result.native_knots)
        assert result.temperature_first_possession_at == (inputs["expected_run_utc"] + timedelta(hours=1)).isoformat()
        assert result.available_at is None and result.static_validity_status == "UNKNOWN"
        assert result.qualification_status == "OFFLINE_ONLY"
        assert result.projection_status == "NOT_PERFORMED" and result.extrema_status == "NOT_COMPUTED"
        assert conn.execute("SELECT COUNT(*) FROM day0_hourly_vectors").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM source_run_coverage").fetchone()[0] == 0
    finally:
        conn.close()


def test_native_source_missing_body_resume_preserves_identity_and_first_possession(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    missing = cache / "step006-member50.grib2"
    raw, proof = missing.read_bytes(), missing.with_suffix(".grib2.proof.json").read_bytes()
    missing.unlink()
    monkeypatch.setattr(module.requests, "Session", lambda: pytest.fail("source-only resume made HTTP"))
    try:
        first = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        before = json.loads(manifest.read_bytes())["messages"]
        # A failed external 503 leaves originals alone: repeated normal inventory
        # neither requests nor renews cached proof clocks.
        retry = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        assert retry == first and json.loads(manifest.read_bytes())["messages"] == before
        missing.write_bytes(raw)
        missing.with_suffix(".grib2.proof.json").write_bytes(proof)
        final = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        assert final.source_run_id == first.source_run_id and final.status == "AVAILABLE"
        row = conn.execute("SELECT * FROM source_run").fetchone()
        assert (row["ingest_mode"], row["origin_mode"]) == ("ARCHIVE_BACKFILL", "ARCHIVE_BACKFILL")
        after = json.loads(manifest.read_bytes())["messages"]
        assert all(m in after for m in before)
        result = _native_source_scope(conn, final, manifest, inputs)
        changed_scope = _native_source_scope(conn, final, manifest, inputs,
            qualified_prefix_cut_utc=inputs["expected_run_utc"] + timedelta(hours=2))
        assert result.temperature_first_possession_at == changed_scope.temperature_first_possession_at
        assert result.qualification_status == changed_scope.qualification_status == "OFFLINE_ONLY"
        assert conn.execute("SELECT COUNT(*) FROM source_run").fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("fault", ["raw", "index", "clock", "range", "dewpoint", "duplicate"])
def test_native_source_original_tamper_fails_closed(tmp_path, fault):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    try:
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        path = cache / "step006-member50.grib2"
        proof_path = path.with_suffix(".grib2.proof.json")
        proof = json.loads(proof_path.read_bytes())
        if fault == "raw":
            path.write_bytes(path.read_bytes()[:-1] + b"0")
        elif fault == "index":
            (cache / proof["index_path"]).write_bytes(b"{}\n")
        elif fault == "clock":
            proof["source_fetched_at"] = (inputs["expected_run_utc"] + timedelta(hours=2)).isoformat()
            proof_path.write_text(json.dumps(proof))
        elif fault == "range":
            proof["range_http"]["status"] = 200
            proof_path.write_text(json.dumps(proof))
        elif fault == "dewpoint":
            ec = pytest.importorskip("eccodes")
            gid = ec.codes_new_from_message(path.read_bytes())
            try:
                ec.codes_set(gid, "paramId", 168)
                raw = ec.codes_get_message(gid)
            finally:
                ec.codes_release(gid)
            path.write_bytes(raw)
            proof["raw_message_sha256"] = hashlib.sha256(raw).hexdigest()
            proof_path.write_text(json.dumps(proof))
        else:
            duplicate = cache / "step006-member50-duplicate.grib2"
            duplicate.write_bytes(path.read_bytes())
            duplicate.with_suffix(".grib2.proof.json").write_bytes(proof_path.read_bytes())
        # A new unreferenced duplicate does not rewrite the old immutable
        # manifest generation; inventorying it must nevertheless fail closed.
        assert _native_source_scope(conn, source, manifest, inputs).status == ("AVAILABLE" if fault == "duplicate" else "UNKNOWN")
        updated = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        assert updated.status == "INCOMPLETE"
        assert conn.execute("SELECT status FROM source_run").fetchone()[0] == "PARTIAL"
    finally:
        conn.close()


@pytest.mark.parametrize("fault", ["prefix", "left", "right", "mask", "manifest", "native_gap"])
def test_native_source_scope_qualification_not_guessed(tmp_path, fault):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    try:
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
        changes = {}
        if fault == "prefix":
            changes["qualified_prefix_cut_utc"] = None
        elif fault == "left":
            changes["required_steps"] = [3, 6]
        elif fault == "right":
            changes["local_day_end_utc"] = inputs["expected_run_utc"] + timedelta(hours=7)
        elif fault == "mask":
            inputs["mask_grib_path"].write_bytes(b"not-original")
        elif fault == "native_gap":
            changes["required_steps"] = [0, 6]
        else:
            manifest.write_bytes(manifest.read_bytes() + b" ")
        result = _native_source_scope(conn, source, manifest, inputs, **changes)
        assert result.status == "UNKNOWN" and result.native_knots == ()
        if fault == "manifest":
            with pytest.raises(ValueError, match="PREVIOUS_MANIFEST_UNBOUND"):
                module.persist_native_temperature_source_run(conn, cache_dir=cache,
                    manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
    finally:
        conn.close()


def test_native_source_46_of_51_one_knot_is_not_six_knot_shape(tmp_path):
    from src.data import ecmwf_open_data as module
    steps = (9, 12, 15, 18, 21, 24)
    missing = {(m, s) for s in steps for m in range(51) if s != 9 or m >= 46}
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path, steps=steps, missing=missing)
    try:
        source = module.persist_native_temperature_source_run(conn, cache_dir=cache,
            manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=list(steps))
        assert source.status == "INCOMPLETE" and source.observed_count == 46
        assert len(source.missing_member_steps) == 260
        result = _native_source_scope(conn, source, manifest, inputs)
        assert result.status == "INCOMPLETE" and not result.native_knots
        assert result.available_at is None
    finally:
        conn.close()


def test_native_source_tampered_then_missing_cannot_reset_clock_anchor(tmp_path):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    args = dict(cache_dir=cache, manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
    try:
        original = module.persist_native_temperature_source_run(conn, **args)
        proof_path = cache / "step006-member50.grib2.proof.json"
        original_proof = proof_path.read_bytes()
        proof = json.loads(original_proof)
        proof["source_fetched_at"] = (inputs["expected_run_utc"] + timedelta(hours=2)).isoformat()
        proof_path.write_text(json.dumps(proof))
        assert module.persist_native_temperature_source_run(conn, **args).status == "INCOMPLETE"
        assert module.persist_native_temperature_source_run(conn, **args).status == "INCOMPLETE"
        proof_path.write_bytes(original_proof)
        restored = module.persist_native_temperature_source_run(conn, **args)
        assert restored.status == "AVAILABLE" and restored.source_run_id == original.source_run_id
        assert _native_source_scope(conn, restored, manifest, inputs).temperature_first_possession_at == json.loads(original_proof)["source_fetched_at"]
    finally:
        conn.close()


def test_native_source_all_bodies_pruned_degrades_same_identity_without_eviction(tmp_path):
    from src.data import ecmwf_open_data as module
    inputs, cache, conn, manifest = _native_source_cache_fixture(tmp_path)
    args = dict(cache_dir=cache, manifest_path=manifest, expected_run_utc=inputs["expected_run_utc"], product_steps=[0, 3, 6])
    try:
        original = module.persist_native_temperature_source_run(conn, **args)
        # Only private generated fixture bodies are pruned here.
        for path in cache.glob("*.grib2"):
            path.unlink()
        pruned = module.persist_native_temperature_source_run(conn, **args)
        assert pruned.status == "INCOMPLETE" and pruned.observed_count == 0
        assert pruned.source_run_id == original.source_run_id
        assert conn.execute("SELECT status FROM source_run").fetchone()[0] == "PARTIAL"
        assert len(json.loads(manifest.read_bytes())["retained_identities"]) == 153
    finally:
        conn.close()


def test_native_temperature_capture_original_index_range_global_once(tmp_path):
    from src.data import ecmwf_open_data as module

    run, output, session, calls = _native_temperature_transport_fixture(tmp_path)
    result = module._capture_native_temperature_bytes(run, [0, 3], output,
        byte_budget=8 * 1024 * 1024, deadline=time.monotonic() + 30,
        _session=session, _request_gate=lambda **kw: None)
    assert result["capture_status"] == "OBSERVED", result
    assert result["qualification_status"] == "OFFLINE_ONLY"
    assert result["observed_steps"] == [0, 3]
    assert len(result["messages"]) == 102
    assert len(calls) == 8  # four original indexes, two CF ranges, two coalesced PF ranges
    assert result["first_source_publication_at"] is None
    for message in result["messages"]:
        assert hashlib.sha256((output / message["path"]).read_bytes()).hexdigest() == message["raw_message_sha256"]
        assert message["native_capture"]["observed_headers"]["paramId"] == 167


def test_native_temperature_capture_lsm_has_independent_original_proof(tmp_path):
    from src.data import ecmwf_open_data as module

    run, output, session, calls = _native_temperature_transport_fixture(tmp_path, land_mask=True)
    result = module._capture_native_temperature_bytes(run, [0, 3], output,
        byte_budget=8 * 1024 * 1024, deadline=time.monotonic() + 30,
        _session=session, _request_gate=lambda **kw: None)
    assert result["capture_status"] == "OBSERVED", result
    mask = result["land_mask"]
    assert mask["native_capture"]["observed_headers"]["paramId"] == 172
    assert (output / "lsm.grib2.proof.json").exists()
    assert result["surface_geopotential_status"] == "UNKNOWN"
    assert result["sensor_agl_status"] == "UNPROVEN"
    assert len(calls) == 9


@pytest.mark.parametrize("fault", ("missing_member", "wrong_run", "duplicate", "http404", "full200", "truncated",
    "grib_dewpoint", "grib_grid", "grib_process"))
def test_native_temperature_capture_gap_never_claims_complete(tmp_path, fault):
    from src.data import ecmwf_open_data as module

    run, output, session, calls = _native_temperature_transport_fixture(tmp_path, fault=fault)
    result = module._capture_native_temperature_bytes(run, [0, 3], output,
        byte_budget=8 * 1024 * 1024, deadline=time.monotonic() + 30,
        _session=session, _request_gate=lambda **kw: None)
    assert result["capture_status"] == "UNKNOWN", result
    assert result["unavailable_reason"]
    if fault in ("missing_member", "wrong_run", "duplicate", "http404"):
        assert all(url.endswith(".index") for url, _ in calls)


@pytest.mark.parametrize("limit,expired", ((10000, False), (8 * 1024 * 1024, True)))
def test_native_temperature_capture_whole_plan_budget_deadline_before_ranges(tmp_path, limit, expired):
    from src.data import ecmwf_open_data as module

    run, output, session, calls = _native_temperature_transport_fixture(tmp_path)
    result = module._capture_native_temperature_bytes(run, [0, 3], output, byte_budget=limit,
        deadline=time.monotonic() + (-1 if expired else 30), _session=session, _request_gate=lambda **kw: None)
    assert result["capture_status"] == "UNKNOWN", result
    assert all(url.endswith(".index") for url, _ in calls)
    if expired:
        assert calls == []


def test_native_temperature_capture_priority_precedes_output_or_worker(tmp_path):
    from src.data import ecmwf_open_data as module

    output = tmp_path / "not-created"
    result = module.capture_open_ens_temperature_run(run_utc=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        steps=[0, 3], output_dir=output, normal_collector_busy=lambda: True)
    assert result["unavailable_reason"] == "NATIVE_2T_NORMAL_COLLECTOR_PRIORITY"
    assert not output.exists()


def _native_temperature_partial_frame_worker(run_iso, steps, output_dir, byte_budget, deadline, writer):
    del run_iso, steps, byte_budget, deadline
    Path(output_dir, "partial-frame-started").touch()
    os.write(writer.fileno(), struct.pack("!i", 100) + b"{")
    time.sleep(30)


def _native_temperature_request_intent_worker(run_iso, steps, output_dir, byte_budget, deadline, writer):
    del run_iso, steps, byte_budget, deadline
    writer.send_bytes(b'{"request_intent":true}')
    if writer.recv_bytes(maxlength=16) == b"OK":
        Path(output_dir, "request-permitted").touch()
        writer.send_bytes(json.dumps({"capture_status": "UNKNOWN", "qualification_status": "OFFLINE_ONLY",
                                     "transferred_body_bytes": 0}).encode())
    writer.close()


def test_native_temperature_capture_per_request_intent_checks_priority(tmp_path):
    from src.data import ecmwf_open_data as module

    result = module.capture_open_ens_temperature_run(run_utc=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        steps=[0, 3], output_dir=tmp_path / "packet", normal_collector_busy=lambda: False, timeout_seconds=4.,
        _worker=_native_temperature_request_intent_worker)
    assert result["capture_status"] == "UNKNOWN", result
    assert result["transferred_body_bytes"] == 0
    assert tmp_path.joinpath("packet", "request-permitted").exists()


def test_native_temperature_capture_spawn_wall_bound_handles_partial_ipc(tmp_path):
    from src.data import ecmwf_open_data as module

    started = time.monotonic()
    output = tmp_path / "packet"
    result = module.capture_open_ens_temperature_run(run_utc=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        steps=[0, 3], output_dir=output, normal_collector_busy=lambda: False, timeout_seconds=4.,
        _worker=_native_temperature_partial_frame_worker)
    assert output.joinpath("partial-frame-started").exists(), "worker must actually enter partial IPC counterexample"
    assert result["unavailable_reason"] == "NATIVE_2T_DEADLINE_EXCEEDED", result
    assert time.monotonic() - started < 4.5
    assert not output.joinpath("capture_receipt.json").exists()


def test_native_temperature_capture_yields_to_normal_before_publication(tmp_path):
    from src.data import ecmwf_open_data as module

    started = time.monotonic()
    def busy():
        return time.monotonic() - started >= .2
    result = module.capture_open_ens_temperature_run(run_utc=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        steps=[0, 3], output_dir=tmp_path / "packet", normal_collector_busy=busy, timeout_seconds=4.,
        _worker=_native_temperature_partial_frame_worker)
    assert result["unavailable_reason"] == "NATIVE_2T_NORMAL_COLLECTOR_PRIORITY"
    assert not tmp_path.joinpath("packet", "capture_receipt.json").exists()


def test_native_temperature_capture_slow_priority_probe_consumes_total_deadline(tmp_path):
    from src.data import ecmwf_open_data as module

    def slow_busy():
        time.sleep(.08)
        return True
    started = time.monotonic()
    output = tmp_path / "packet"
    result = module.capture_open_ens_temperature_run(run_utc=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        steps=[0], output_dir=output, normal_collector_busy=slow_busy, timeout_seconds=.02)
    assert result["capture_status"] == "UNKNOWN", result
    assert result["unavailable_reason"] in ("NATIVE_2T_PRIORITY_CHECK_TIMEOUT", "NATIVE_2T_DEADLINE_EXCEEDED")
    assert time.monotonic() - started < .05
    assert not output.exists()


def _native_temperature_invalid_index_worker(run_iso, steps, output_dir, byte_budget, deadline, writer):
    from src.data import ecmwf_open_data as module
    class Response:
        status_code = 200
        headers = {"Content-Length": "75"}
        def iter_content(self, chunk_size):
            yield b"x" * 75
        def close(self):
            pass
    class Session:
        def get(self, *args, **kwargs):
            return Response()
        def close(self):
            pass
    report = module._capture_native_temperature_bytes(datetime.fromisoformat(run_iso), steps, Path(output_dir),
        byte_budget=byte_budget, deadline=deadline, _session=Session(), _request_gate=lambda **kw: None)
    writer.send_bytes(json.dumps(report).encode())
    writer.close()


def test_native_temperature_capture_unknown_receipt_cannot_exceed_total_byte_cap(tmp_path):
    from src.data import ecmwf_open_data as module

    output = tmp_path / "packet"
    result = module.capture_open_ens_temperature_run(run_utc=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
        steps=[0], output_dir=output, normal_collector_busy=lambda: False, byte_budget=100, timeout_seconds=4.,
        _worker=_native_temperature_invalid_index_worker)
    receipt = output.joinpath("capture_receipt.json")
    assert not receipt.exists(), f"parent published receipt: {receipt.stat().st_size} bytes + original index 75 > cap 100"
    assert sum(p.stat().st_size for p in output.iterdir()) == 75
    assert result["unavailable_reason"] == "NATIVE_2T_RECEIPT_BYTE_BUDGET_EXCEEDED", result


def test_native_temperature_capture_original_dewpoint_rejects_header_echo(tmp_path, monkeypatch):
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module

    run, output, session, _ = _native_temperature_transport_fixture(tmp_path, fault="grib_dewpoint")
    original_get = extractor.codes_get
    def echo(gid, key):
        if original_get(gid, "paramId") == 168 and key in ("paramId", "shortName"):
            return 167 if key == "paramId" else "2t"
        return original_get(gid, key)
    monkeypatch.setattr(extractor, "codes_get", echo)
    result = module._capture_native_temperature_bytes(run, [0, 3], output,
        byte_budget=8 * 1024 * 1024, deadline=time.monotonic() + 30,
        _session=session, _request_gate=lambda **kw: None)
    assert result["capture_status"] == "UNKNOWN", result
    assert result["unavailable_reason"] == "NATIVE_2T_ORIGINAL_PARAMETER_MEMBER_INVALID"
    assert (output / "step003-member50.grib2").exists()  # original counterevidence survives rejection
    assert (output / "step003-member50.grib2.proof.json").exists()


def _terrain_audit_fixture(tmp_path, monkeypatch, *, tracks=("mx2t6_high", "mn2t6_low"), issue=None):
    """Real GRIB + immutable private snapshot identities; only transport is fake."""
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib

    issue = issue or datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw, mask, mask_proof, _ = _tiny_native_grib(tmp_path, "mx2t6_high", member_count=1, issue=issue)
    z_bytes = mask.with_suffix(".z.grib2").read_bytes()
    decoded = extractor._read_land_mask(mask, mask_proof)
    grid_hash = decoded["grid_identity_hash"]
    root = tmp_path / "audit_source"
    paths = module._resolve_opendata_paths(source_root=root, environ={})
    folder = root / "raw" / "ecmwf_open_ens" / "ecmwf" / issue.strftime("%Y%m%d")
    folder.mkdir(parents=True)
    surface_paths = {}
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE source_run (source_run_id TEXT, source_id TEXT, track TEXT,
          source_cycle_time TEXT, status TEXT, completeness_status TEXT, partial_run INTEGER);
        CREATE TABLE ensemble_snapshots (snapshot_id INTEGER PRIMARY KEY, source_run_id TEXT,
          source_id TEXT, temperature_metric TEXT, source_cycle_time TEXT, source_available_at TEXT,
          authority TEXT, provenance_json TEXT);
        CREATE TABLE forecast_posteriors (posterior_id INTEGER PRIMARY KEY, probability REAL, computed_at TEXT);
        CREATE INDEX source_run_cycle ON source_run(source_id,track,source_cycle_time);
        CREATE TABLE source_run_coverage (coverage_id TEXT PRIMARY KEY, source_run_id TEXT,
          source_id TEXT, track TEXT, temperature_metric TEXT, completeness_status TEXT,
          readiness_status TEXT, snapshot_ids_json TEXT);
        CREATE INDEX coverage_run ON source_run_coverage(source_run_id);
        INSERT INTO forecast_posteriors VALUES (1, 0.42, '2026-01-01T02:00:00+00:00');
    """)
    for idx, track in enumerate(tracks, 1):
        metric = "high" if track == "mx2t6_high" else "low"
        run_id = f"ecmwf_open_data:{track}:{issue:%Y-%m-%dT%H}Z:test"
        conn.execute("INSERT INTO source_run VALUES (?,?,?,?,?,?,?)",
                     (run_id, "ecmwf_open_data", module._forecast_track_for_profile(
                         ingest_track=track, horizon_profile="full" if issue.hour in (0,12) else "short"),
                      issue.isoformat(), "SUCCESS", "COMPLETE", 0))
        conn.execute("INSERT INTO source_run_coverage VALUES (?,?,?,?,?,?,?,?)", (
            f"coverage:{idx}", run_id, "ecmwf_open_data", module._forecast_track_for_profile(
                ingest_track=track, horizon_profile="full" if issue.hour in (0,12) else "short"),
            metric, "COMPLETE", "LIVE_ELIGIBLE", json.dumps([idx]),
        ))
        provenance = {"grid_surface_evidence": {
            "mask_grid_identity_hash": grid_hash, "temperature_grid_identity_hash": grid_hash,
            "mask_sha256": hashlib.sha256(mask.read_bytes()).hexdigest(),
            "mask_source_cycle_time": issue.isoformat(),
            "request_lat": 51.6, "request_lon": .1,
            "selected_flat_index": 2, "selected_lat": 51.5, "selected_lon": 0.0,
            "selected_land_fraction": float(decoded["values"][2]),
        }}
        conn.execute("INSERT INTO ensemble_snapshots VALUES (?,?,?,?,?,?,?,?)", (
            idx, run_id, "ecmwf_open_data", metric, issue.isoformat(),
            (issue + timedelta(hours=2)).isoformat(), "VERIFIED", json.dumps(provenance),
        ))
        dest = folder / f".{track}_{issue:%Y%m%d}_{issue.hour:02d}z_lsm.grib2"
        dest.write_bytes(mask.read_bytes())
        dest.with_suffix(".proof.json").write_bytes(mask_proof.read_bytes())
        surface_paths[track] = dest.with_suffix(".z.grib2")
    conn.commit()
    before = tuple(conn.iterdump())
    closed = []

    class ReadConnection:
        def set_progress_handler(self, *args):
            return conn.set_progress_handler(*args)

        def execute(self, *args):
            return conn.execute(*args)

        def close(self):
            closed.append(True)

    class Response:
        def __init__(self, body, *, range_response=False):
            self.body = body
            self.status_code = 206 if range_response else 200
            self.headers = {"Content-Length": str(len(body)), "Date": "Wed, 01 Jan 2100 00:00:00 GMT",
                            "Last-Modified": "Wed, 01 Jan 2099 00:00:00 GMT"}
            if range_response:
                self.headers["Content-Range"] = f"bytes 0-{len(body)-1}/{len(body)}"

        def iter_content(self, chunk_size):
            yield self.body

        def close(self):
            pass

        def raise_for_status(self):
            raise RuntimeError(f"HTTP {self.status_code}")

    class Session:
        def __init__(self):
            self.calls = []
            self.before_get = lambda: closed == [True]
            self.failure = None
            self.z_bytes = z_bytes
            self.index_overrides = {}

        def get(self, url, **kwargs):
            assert self.before_get(), "all snapshot reads must close before HTTP"
            self.calls.append((url, kwargs))
            assert 0 < kwargs["timeout"] <= 10
            assert kwargs["allow_redirects"] is False
            if self.failure:
                raise self.failure
            if url.endswith(".index"):
                row = dict(param="z", levtype="sfc", step="0", type="fc", stream="oper",
                           date=issue.strftime("%Y%m%d"), time=f"{issue.hour:02d}00", _offset=0, _length=len(self.z_bytes), **{"class": "od"})
                row.update(self.index_overrides)
                return Response(json.dumps(row).encode())
            return Response(self.z_bytes, range_response=True)

        def close(self):
            pass

    session = Session()
    monkeypatch.setattr(module, "get_connection", ReadConnection)
    monkeypatch.setattr(module, "get_forecasts_connection_read_only", lambda **kw: ReadConnection(), raising=False)
    monkeypatch.setattr(module, "_resolve_opendata_paths", lambda: paths)
    monkeypatch.setattr(module.requests, "Session", lambda: session)
    # Parser/cache unit tests stay in-process. Actual wall-bound transport has
    # separate real spawn/loopback antibodies, not an inherited monkeypatch.
    def fake_transport(cycle, url, index_url, *, deadline):
        index_body, index_http = module._surface_audit_http(session, index_url, deadline=deadline)
        offset, length = module._surface_z_index_entry(index_body, cycle)
        body, range_http = module._surface_audit_http(session, url, deadline=deadline,
                                                     offset=offset, length=length)
        return index_body, body, {"source_fetched_at": datetime.now(timezone.utc).isoformat(),
                                 "index_http": index_http, "range_http": range_http}
    monkeypatch.setattr(module, "_fetch_surface_audit_bytes", fake_transport)
    return dict(db=conn, before=before, paths=paths, surface_paths=surface_paths,
                session=session, closed=closed, grid_hash=grid_hash, issue=issue,
                z_bytes=z_bytes, raw=raw)


@pytest.mark.parametrize("hour", (0, 6, 12, 18))
def test_surface_audit_registered_full_short_coverage_pk_real_bytes(tmp_path, monkeypatch, hour):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch,
        issue=datetime(2026, 10, 3, hour, tzinfo=timezone.utc))
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "OBSERVED", result
    assert result["captured_tracks"] == ["mx2t6_high", "mn2t6_low"]
    assert len(fixture["session"].calls) == 2
    assert f"/{hour:02d}z/" in fixture["session"].calls[0][0]
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


@pytest.mark.parametrize("field,value", (("source_run_id", "foreign-run"), ("source_id", "foreign-source"),
    ("temperature_metric", "low"), ("source_cycle_time", "2026-01-02T00:00:00+00:00")))
def test_surface_audit_coverage_pk_identity_is_not_borrowed(tmp_path, monkeypatch, field, value):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["db"].execute(f"UPDATE ensemble_snapshots SET {field}=? WHERE snapshot_id=1", (value,))
    before = tuple(fixture["db"].iterdump())
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert "mx2t6_high" in result["track_gaps"]
    assert tuple(fixture["db"].iterdump()) == before


def test_surface_audit_whole_deadline_precedes_ro_open(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    def forbidden(**kwargs):
        pytest.fail("expired audit must not open DB")
    monkeypatch.setattr(module, "get_forecasts_connection_read_only", forbidden)
    result = module.capture_open_ens_surface_audit(deadline_monotonic=time.monotonic()-1)
    assert result["capture_status"] == "UNKNOWN"
    assert "DEADLINE" in result["unavailable_reason"]
    assert not fixture["session"].calls


@pytest.mark.parametrize("body_kind", ("mask", "z"))
def test_surface_audit_declared_grid_bound_is_checked_before_values(tmp_path, monkeypatch, body_kind):
    import eccodes as ec
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    path = next(iter(fixture["surface_paths"].values())).with_suffix(".grib2")
    # surface_paths stores .lsm.z.grib2; the mandatory LSM is .lsm.grib2.
    path = path.with_name(path.name.replace("_lsm.z.grib2", "_lsm.grib2"))
    raw = path.read_bytes() if body_kind == "mask" else fixture["z_bytes"]
    gid = ec.codes_new_from_message(raw)
    try:
        ec.codes_set(gid, "Ni", 1441)
        malicious = ec.codes_get_message(gid)
    finally:
        ec.codes_release(gid)
    if body_kind == "mask":
        path.write_bytes(malicious)
        def forbidden(*args, **kwargs):
            pytest.fail("oversized declared mask must not decode values")
        monkeypatch.setattr(extractor, "_read_land_mask", forbidden)
        # Isolate to the malformed track so a healthy sibling's decode is lawful.
        fixture["db"].execute("DELETE FROM source_run WHERE track LIKE 'mn2t6_low%'")
    else:
        fixture["session"].z_bytes = malicious
        def forbidden(*args, **kwargs):
            pytest.fail("oversized declared z must not decode values")
        monkeypatch.setattr(extractor, "_read_surface_geopotential", forbidden)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert "DECODE_GRID_BOUNDS" in str(result)
    assert all(not p.with_suffix(".proof.json").exists() for p in fixture["surface_paths"].values())


@pytest.mark.parametrize("fault", ("busy", "vm"))
def test_surface_audit_real_sqlite_budget_closes_before_http(tmp_path, monkeypatch, fault):
    from src.data import ecmwf_open_data as module
    from src.state.db import get_connection_read_only

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    if fault == "busy":
        path = tmp_path / "locked.db"
        locked = sqlite3.connect(path)
        locked.execute("CREATE TABLE source_run (source_id TEXT)")
        locked.commit()
        locked.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(module, "get_forecasts_connection_read_only",
            lambda **kw: get_connection_read_only(path, **kw))
    else:
        class VMConnection:
            def set_progress_handler(self, *args):
                fixture["db"].set_progress_handler(*args)
            def execute(self, query, parameters=()):
                return fixture["db"].execute("""WITH RECURSIVE n(x) AS
                    (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000)
                    SELECT sum(x) FROM n""")
            def close(self):
                fixture["closed"].append(True)
        monkeypatch.setattr(module, "get_forecasts_connection_read_only", lambda **kw: VMConnection())
    started = time.monotonic()
    try:
        result = module.capture_open_ens_surface_audit(deadline_monotonic=started+.06)
    finally:
        if fault == "busy":
            locked.rollback()
            locked.close()
    assert time.monotonic()-started < .8
    assert result["capture_status"] == "UNKNOWN", result
    assert result["unavailable_reason"] == "SURFACE_AUDIT_DB_DEADLINE_EXCEEDED", result
    assert result["stage"] == "source_read"
    assert not fixture["session"].calls
    assert all(not p.exists() for p in fixture["surface_paths"].values())


@pytest.mark.parametrize("phase", ("mask_decode", "z_decode", "staging_fsync", "proof_fsync"))
def test_surface_audit_expired_native_phase_never_commits_late_proof(tmp_path, monkeypatch, phase):
    from src.data import ecmwf_open_data as module
    from scripts import extract_open_ens_localday as extractor

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    triggered = []
    if phase in ("mask_decode", "z_decode"):
        name = "_read_land_mask" if phase == "mask_decode" else "_read_surface_geopotential"
        original = getattr(extractor, name)
        def slow_decode(*args, **kwargs):
            result = original(*args, **kwargs)
            time.sleep(.25)
            triggered.append(True)
            return result
        monkeypatch.setattr(extractor, name, slow_decode)
    else:
        original_sync, original_link = module.os.fsync, module.os.link
        committed = []
        def link(source, target):
            original_link(source, target)
            if str(target).endswith(".proof.json"):
                committed.append(True)
        def slow_sync(fd):
            original_sync(fd)
            if not triggered and (phase == "staging_fsync" or committed):
                time.sleep(.25)
                triggered.append(True)
        monkeypatch.setattr(module.os, "link", link)
        monkeypatch.setattr(module.os, "fsync", slow_sync)
    result = module.capture_open_ens_surface_audit(deadline_monotonic=time.monotonic()+.2)
    assert triggered
    assert result["capture_status"] == "UNKNOWN", result
    assert "DEADLINE" in result["unavailable_reason"], result
    assert all(not p.with_suffix(".proof.json").exists() for p in fixture["surface_paths"].values())
    if phase == "mask_decode":
        assert not fixture["session"].calls
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def test_normal_surface_audit_actual_bytes_shared_hl_and_clock_identity(tmp_path, monkeypatch):
    from scripts.extract_open_ens_localday import _read_surface_geopotential
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "OBSERVED", result
    assert result["captured_tracks"] == ["mx2t6_high", "mn2t6_low"]
    assert len(fixture["session"].calls) == 2
    proofs = []
    for track, path in fixture["surface_paths"].items():
        assert path.read_bytes() == fixture["z_bytes"]
        proof_path = path.with_suffix(".proof.json")
        decoded = _read_surface_geopotential(path, proof_path)
        proof = decoded["proof"]
        proofs.append(proof)
        assert decoded["grid_identity_hash"] == fixture["grid_hash"]
        assert float(decoded["values"][2]) == 313.75
        assert proof["source_issued_at"] is None
        assert proof["index_http"]["headers"]["Date"].startswith("Wed, 01 Jan 2100")
        assert fixture["issue"] <= datetime.fromisoformat(proof["source_fetched_at"]) <= datetime.fromisoformat(proof["source_written_at"]) <= datetime.now(timezone.utc)
        index_body = path.with_suffix(".index.body").read_bytes()
        assert hashlib.sha256(index_body).hexdigest() == proof["source_index_sha256"]
        assert proof["range_http"]["headers"]["Content-Range"] == f"bytes 0-{len(fixture['z_bytes'])-1}/{len(fixture['z_bytes'])}"
        assert track in proof["source_run_id"]
    assert proofs[0]["source_fetched_at"] == proofs[1]["source_fetched_at"]
    before_cache = {str(p): p.read_bytes() for p in fixture["surface_paths"].values()}
    before_proofs = {str(p): p.with_suffix(".proof.json").read_bytes() for p in fixture["surface_paths"].values()}
    again = module.capture_open_ens_surface_audit()
    assert again["cache_reused"] is True
    assert len(fixture["session"].calls) == 2
    assert before_cache == {str(p): p.read_bytes() for p in fixture["surface_paths"].values()}
    assert before_proofs == {str(p): p.with_suffix(".proof.json").read_bytes() for p in fixture["surface_paths"].values()}
    assert tuple(fixture["db"].iterdump()) == fixture["before"]  # no q/old receipt/source clock write


@pytest.mark.parametrize("fault", (None, "run", "grid", "reference", "generation", "entity", "missing_cache"))
def test_forward_surface_audit_binds_committed_coverage_without_rewriting_source_proof(tmp_path, monkeypatch, fault):
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["session"].before_get = lambda: True
    for track, surface in fixture["surface_paths"].items():
        mask = surface.with_name(f".{track}_20260101_00z_lsm.grib2")
        assert module._fetch_cycle_surface_evidence(cycle=fixture["issue"], mask_path=mask)["capture_status"] == "OBSERVED"
    assert len(fixture["session"].calls) == 2
    high = fixture["surface_paths"]["mx2t6_high"]
    proof_path = high.with_suffix(".proof.json")
    if fault == "run":
        fixture["db"].execute("UPDATE source_run SET source_run_id='foreign-committed-run' WHERE source_run_id LIKE '%mx2t6_high%'")
    elif fault == "grid":
        fixture["db"].execute("UPDATE ensemble_snapshots SET provenance_json=json_set(provenance_json,'$.grid_surface_evidence.temperature_grid_identity_hash',?) WHERE temperature_metric='high'", ("f" * 64,))
    elif fault == "reference":
        fixture["db"].execute("UPDATE source_run_coverage SET snapshot_ids_json='[999]' WHERE temperature_metric='high'")
    elif fault == "entity":
        proof = json.loads(proof_path.read_bytes())
        proof["raw_message_sha256"] = "0" * 64
        proof_path.write_text(json.dumps(proof))
    elif fault == "missing_cache":
        for path in (high, high.with_suffix(".index.body"), proof_path):
            path.unlink()  # Only this test's generated private cache.
    cache_before = {p: p.read_bytes() for surface in fixture["surface_paths"].values()
                    for p in (surface, surface.with_suffix(".index.body"), surface.with_suffix(".proof.json")) if p.exists()}
    changed = []
    if fault == "generation":
        decode = extractor._read_surface_geopotential
        def changing_generation(path, proof):
            result = decode(path, proof)
            if path == high and not changed:
                proof_path.write_bytes(proof_path.read_bytes() + b" \n")
                changed.append(True)
            return result
        monkeypatch.setattr(extractor, "_read_surface_geopotential", changing_generation)
    db_before = tuple(fixture["db"].iterdump())
    result = module.capture_open_ens_surface_audit()
    expected = ["mx2t6_high", "mn2t6_low"] if fault in (None, "missing_cache") else ["mn2t6_low"]
    assert result.get("captured_tracks") == expected, result
    assert result["capture_status"] == "OBSERVED", result
    assert len(fixture["session"].calls) == 2  # no recapture of already possessed bytes
    for track in expected:
        binding = result["snapshot_bindings"][track]
        assert binding["snapshot_id"] == (1 if track == "mx2t6_high" else 2)
        assert track in binding["source_run_id"]
        cached_proof = fixture["surface_paths"][track].with_suffix(".proof.json")
        assert binding["source_proof_sha256"] == hashlib.sha256(cached_proof.read_bytes()).hexdigest()
        assert binding["raw_phi_m2_s2"] == 313.75
    if fault == "missing_cache":
        created = json.loads(proof_path.read_bytes())
        low = fixture["surface_paths"]["mn2t6_low"]
        origin = json.loads(cache_before[low.with_suffix(".proof.json")])
        for key in ("source_fetched_at", "source_fetched_at_role", "source_issued_at", "source_url",
                    "source_index_url", "source_index_sha256", "raw_message_sha256",
                    "source_index_offset", "source_index_length", "audit_scope", "quantity_role"):
            assert created[key] == origin[key]
        assert high.read_bytes() == cache_before[low]
        assert high.with_suffix(".index.body").read_bytes() == cache_before[low.with_suffix(".index.body")]
        assert created["mask_sha256"] == hashlib.sha256(high.with_name(".mx2t6_high_20260101_00z_lsm.grib2").read_bytes()).hexdigest()
        assert created["source_evidence_role"] == "FORWARD_SOURCE_ONLY_NO_SNAPSHOT_PIN"
        assert not any(key in created for key in ("snapshot_id", "source_run_id", "snapshot_binding_sha256"))
        again = module.capture_open_ens_surface_audit()
        assert again["captured_tracks"] == expected, again
        assert len(fixture["session"].calls) == 2
    for path, body in cache_before.items():
        assert path.read_bytes() == (body + b" \n" if fault == "generation" and path == proof_path else body)
    assert tuple(fixture["db"].iterdump()) == db_before


@pytest.mark.parametrize("field,value", (("dataDate", 20260102), ("dataType", "cf"),
                                         ("scanningMode", 64), ("paramId", 130)))
def test_normal_surface_audit_real_wrong_cycle_grid_type_unit_unknown(tmp_path, monkeypatch, field, value):
    import eccodes as ec
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    gid = ec.codes_new_from_message(fixture["z_bytes"])
    try:
        ec.codes_set(gid, field, value)
        fixture["session"].z_bytes = ec.codes_get_message(gid)
    finally:
        ec.codes_release(gid)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert all(not p.exists() for p in fixture["surface_paths"].values())
    assert len(fixture["session"].calls) == 2
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


@pytest.mark.parametrize("overrides", ({"date": "20260102"}, {"step": "3"}, {"levtype": "pl"},
                                     {"param": "gh"}, {"_length": 1024 * 1024 + 1}))
def test_normal_surface_audit_index_rejection_has_no_range_retry(tmp_path, monkeypatch, overrides):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["session"].index_overrides = overrides
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert len(fixture["session"].calls) == 1
    assert all(not p.exists() for p in fixture["surface_paths"].values())


@pytest.mark.parametrize("failure", ("timeout", "deadline"))
def test_normal_surface_audit_slow_fail_no_publish_or_forecast_write(tmp_path, monkeypatch, failure):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    if failure == "timeout":
        fixture["session"].failure = module.requests.Timeout("slow optional index")
    else:
        clock = [100.0]
        monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
        original = fixture["session"].get
        def slow_get(url, **kwargs):
            response = original(url, **kwargs)
            clock[0] += 41
            return response
        monkeypatch.setattr(fixture["session"], "get", slow_get)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert len(fixture["session"].calls) == 1
    assert all(not p.exists() for p in fixture["surface_paths"].values())
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def test_normal_surface_audit_changed_mask_and_publish_race_never_overwrite(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    before_link = module.os.link
    raced = fixture["surface_paths"]["mx2t6_high"]
    sentinel = b"other publisher's immutable generation"
    def publish_race(source, target):
        if Path(target) == raced and not raced.exists():
            raced.write_bytes(sentinel)
        return before_link(source, target)
    monkeypatch.setattr(module.os, "link", publish_race)
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert raced.read_bytes() == sentinel
    assert "mx2t6_high" in result["track_gaps"]
    assert fixture["surface_paths"]["mn2t6_low"].read_bytes() == fixture["z_bytes"]


def test_normal_surface_audit_changed_source_generation_stays_unknown(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    original = fixture["session"].get
    mask = fixture["surface_paths"]["mx2t6_high"].with_name(".mx2t6_high_20260101_00z_lsm.grib2")
    def change_after_read(url, **kwargs):
        response = original(url, **kwargs)
        if not url.endswith(".index"):
            mask.write_bytes(b"new-generation")
        return response
    monkeypatch.setattr(fixture["session"], "get", change_after_read)
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert "SURFACE_AUDIT_MASK_GENERATION_CHANGED" in result["track_gaps"]["mx2t6_high"]
    assert not fixture["surface_paths"]["mx2t6_high"].exists()


def test_normal_surface_audit_existing_corrupt_cache_kept_sibling_can_capture(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    old = fixture["surface_paths"]["mx2t6_high"]
    old.write_bytes(b"immutable malformed prior cache")
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == ["mn2t6_low"], result
    assert old.read_bytes() == b"immutable malformed prior cache"
    assert result["track_gaps"]["mx2t6_high"] == "SURFACE_AUDIT_CACHE_CONFLICT:SURFACE_AUDIT_CACHE_BOUNDS_INVALID"


def test_normal_surface_audit_partial_publish_recovers_by_new_real_capture(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    original = module.os.link
    lost = [False]
    proof = fixture["surface_paths"]["mx2t6_high"].with_suffix(".proof.json")
    def interrupted(source, target):
        if Path(target) == proof and not lost[0]:
            lost[0] = True
            raise OSError("temporary publisher interruption")
        return original(source, target)
    monkeypatch.setattr(module.os, "link", interrupted)
    first = module.capture_open_ens_surface_audit()
    assert first["captured_tracks"] == ["mn2t6_low"], first
    assert not proof.exists()
    old_low_proof = fixture["surface_paths"]["mn2t6_low"].with_suffix(".proof.json").read_bytes()
    second = module.capture_open_ens_surface_audit()
    assert second["captured_tracks"] == ["mx2t6_high", "mn2t6_low"], second
    assert proof.exists()
    assert len(fixture["session"].calls) == 2  # existing valid sibling supplies actual bytes/old fetched clock
    assert old_low_proof == fixture["surface_paths"]["mn2t6_low"].with_suffix(".proof.json").read_bytes()


@pytest.mark.parametrize("fault", ("index_oversized", "range_oversized", "range_200", "range_header"))
def test_normal_surface_audit_stream_bounds_and_range_proof(tmp_path, monkeypatch, fault):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    original = fixture["session"].get
    consumed = []
    def hostile(url, **kwargs):
        response = original(url, **kwargs)
        if url.endswith(".index") and fault == "index_oversized":
            def chunks(chunk_size):
                for _ in range(100):
                    consumed.append(1)
                    yield b"x" * 65536
            response.iter_content = chunks
        elif not url.endswith(".index"):
            if fault == "range_200":
                response.status_code = 200
            elif fault == "range_header":
                response.headers["Content-Range"] = "bytes 1-999/1000"
            elif fault == "range_oversized":
                response.body += b"x"
        return response
    monkeypatch.setattr(fixture["session"], "get", hostile)
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert all(not p.exists() for p in fixture["surface_paths"].values())
    assert len(fixture["session"].calls) == (1 if fault == "index_oversized" else 2)
    if fault == "index_oversized":
        assert len(consumed) == 17  # refuse during streaming, never drain all 100 chunks


@pytest.mark.parametrize("fault", ("future_cycle", "future_available", "running", "wrong_temp_grid"))
def test_normal_surface_audit_no_future_or_uncommitted_decision_input(tmp_path, monkeypatch, fault):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    conn = fixture["db"]
    if fault == "future_cycle":
        conn.execute("UPDATE source_run SET source_cycle_time='2100-01-01T00:00:00+00:00'")
    elif fault == "future_available":
        conn.execute("UPDATE ensemble_snapshots SET source_available_at='2100-01-01T00:00:00+00:00'")
    elif fault == "running":
        conn.execute("UPDATE source_run SET status='RUNNING'")
    else:
        conn.execute("UPDATE ensemble_snapshots SET provenance_json=json_set(provenance_json,'$.grid_surface_evidence.temperature_grid_identity_hash',?)", ("f" * 64,))
    before = tuple(conn.iterdump())
    result = module.capture_open_ens_surface_audit()
    assert result["capture_status"] == "UNKNOWN", result
    assert not fixture["session"].calls
    assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("field", ("raw_phi_m2_s2", "selected_point", "mask_sha256", "snapshot_id",
                                   "source_run_id", "source_issued_at"))
def test_cached_surface_own_reference_tamper_keeps_bad_track_unknown(tmp_path, monkeypatch, track, field):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    proof_path = fixture["surface_paths"][track].with_suffix(".proof.json")
    proof = json.loads(proof_path.read_bytes())
    mutations = {"raw_phi_m2_s2": 123456, "selected_point": {"flat_index": 1, "lat": 51.75, "lon": .25},
                 "mask_sha256": "a" * 64, "snapshot_id": 999999,
                 "source_run_id": "foreign-world-run", "source_issued_at": "2026-01-01T00:00:00Z"}
    proof[field] = mutations[field]
    proof_path.write_text(json.dumps(proof))
    bad_bytes = proof_path.read_bytes()
    result = module.capture_open_ens_surface_audit()
    sibling = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
    assert result["captured_tracks"] == [sibling], result
    assert track in result["track_gaps"]
    assert proof_path.read_bytes() == bad_bytes
    assert len(fixture["session"].calls) == 2


@pytest.mark.parametrize("change", ("new_row", "pruned_original", "changed_original", "foreign_source"))
def test_cached_surface_binds_original_not_always_latest(tmp_path, monkeypatch, change):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    cache = {t: p.with_suffix(".proof.json").read_bytes() for t, p in fixture["surface_paths"].items()}
    conn = fixture["db"]
    conn.execute("INSERT INTO ensemble_snapshots SELECT snapshot_id+100, source_run_id, source_id, temperature_metric, source_cycle_time, source_available_at, authority, provenance_json FROM ensemble_snapshots")
    conn.execute("UPDATE source_run_coverage SET snapshot_ids_json=json_array(json_extract(snapshot_ids_json,'$[0]')+100)")
    if change == "pruned_original":
        conn.execute("DELETE FROM ensemble_snapshots WHERE snapshot_id=1")
    elif change == "changed_original":
        conn.execute("UPDATE ensemble_snapshots SET provenance_json=json_set(provenance_json,'$.grid_surface_evidence.selected_lat',50) WHERE snapshot_id=1")
    elif change == "foreign_source":
        conn.execute("UPDATE ensemble_snapshots SET source_id='foreign_world_source' WHERE snapshot_id=1")
    before = tuple(conn.iterdump())
    result = module.capture_open_ens_surface_audit()
    assert result["captured_tracks"] == (["mx2t6_high", "mn2t6_low"] if change == "new_row" else ["mn2t6_low"]), result
    assert result["cache_binding_role"] == "ORIGINAL_IMMUTABLE_SNAPSHOT_NOT_CURRENT_TARGET"
    assert cache == {t: p.with_suffix(".proof.json").read_bytes() for t, p in fixture["surface_paths"].items()}
    assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("change", ("absent_id", "foreign_id_run", "stable_aba"))
def test_cached_surface_original_lookup_proof_race_is_one_epoch(tmp_path, monkeypatch, track, change):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    proof_path = fixture["surface_paths"][track].with_suffix(".proof.json")
    original_bytes = proof_path.read_bytes()
    original = json.loads(original_bytes)
    foreign_track = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
    foreign = json.loads(fixture["surface_paths"][foreign_track].with_suffix(".proof.json").read_bytes())
    raced = []

    class RacingConnection:
        def set_progress_handler(self, *args):
            return fixture["db"].set_progress_handler(*args)

        def execute(self, query, parameters=()):
            cursor = fixture["db"].execute(query, parameters)
            if "JOIN source_run" in query and "e.snapshot_id=?" in query and parameters[0] == original["snapshot_id"] and not raced:
                replacement = dict(original)
                if change == "absent_id":
                    replacement["snapshot_id"] = 999999
                else:
                    replacement["snapshot_id"] = foreign["snapshot_id"]
                    replacement["source_run_id"] = foreign["source_run_id"]
                proof_path.write_text(json.dumps(replacement))
                raced.append(proof_path.read_bytes())
                if change == "stable_aba":
                    proof_path.write_bytes(original_bytes)
            return cursor

        def close(self):
            pass

    monkeypatch.setattr(module, "get_forecasts_connection_read_only", lambda **kw: RacingConnection())
    result = module.capture_open_ens_surface_audit()
    assert raced
    if change == "stable_aba":
        assert result["captured_tracks"] == ["mx2t6_high", "mn2t6_low"], result
        assert proof_path.read_bytes() == original_bytes
    else:
        assert result["captured_tracks"] == [foreign_track], result
        assert "SURFACE_AUDIT_SOURCE_PROOF_GENERATION_CHANGED" in result["track_gaps"][track]
        assert proof_path.read_bytes() == raced[0]  # preserve the publisher, never overwrite with A
    assert len(fixture["session"].calls) == 2
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def _surface_test_half_packet(cycle, url, index_url, deadline, writer):
    os.write(writer.fileno(), struct.pack("!III", 100, 100, 100) + b"partial")
    time.sleep(30)


def _surface_test_oversized_packet(cycle, url, index_url, deadline, writer):
    os.write(writer.fileno(), struct.pack("!III", 1, 1024 * 1024 + 1, 1))
    time.sleep(30)


@pytest.mark.parametrize("worker", (_surface_test_half_packet, _surface_test_oversized_packet))
def test_surface_spawn_capped_partial_ipc_is_nonblocking_and_reaped(worker):
    from src.data import ecmwf_open_data as module

    before = {p.pid for p in multiprocessing.active_children()}
    start = time.monotonic()
    with pytest.raises((ValueError, module.requests.Timeout)):
        module._fetch_surface_audit_bytes(datetime(2026, 1, 1, tzinfo=timezone.utc),
            "unused", "unused", deadline=start + 3, _worker=worker)
    assert time.monotonic() - start < 3.7
    assert {p.pid for p in multiprocessing.active_children()} == before


@pytest.mark.parametrize("mode", ("good", "headers", "body", "silent"))
def test_surface_actual_spawn_loopback_has_global_wall_bound(tmp_path, mode):
    """Real production spawn wrapper, including get/header and body stalls."""
    from src.data import ecmwf_open_data as module
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib

    _, mask, _, _ = _tiny_native_grib(tmp_path, "mx2t6_high", member_count=1)
    body = mask.with_suffix(".z.grib2").read_bytes()
    index = json.dumps(dict(param="z", levtype="sfc", step="0", type="fc", stream="oper",
                           date="20260101", time="0000", _offset=0, _length=len(body), **{"class": "od"})).encode()
    seen = threading.Event()
    stop = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            seen.set()
            try:
                if mode == "silent":
                    stop.wait(10)
                    return
                if mode == "headers":
                    self.connection.sendall(b"HTTP/1.1 200 OK\r\nX-Trickle: ")
                    while not stop.wait(.05):
                        self.connection.sendall(b"x")
                    return
                is_index = self.path.endswith(".index")
                payload = index if is_index else body
                self.send_response(200 if is_index else 206)
                self.send_header("Content-Length", str(len(payload)))
                if not is_index:
                    self.send_header("Content-Range", f"bytes 0-{len(body)-1}/{len(body)}")
                self.end_headers()
                if mode == "body":
                    for byte in payload:
                        if stop.wait(.05):
                            return
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                else:
                    self.wfile.write(payload)
            except (OSError, ConnectionError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    before = {p.pid for p in multiprocessing.active_children()}
    base = f"http://127.0.0.1:{server.server_port}/surface"
    start = time.monotonic()
    try:
        if mode == "good":
            raw_index, raw_body, metadata = module._fetch_surface_audit_bytes(
                datetime(2026, 1, 1, tzinfo=timezone.utc), base + ".grib2", base + ".index", deadline=start + 5)
            assert raw_index == index and raw_body == body and metadata["status"] == "ok"
        else:
            with pytest.raises(module.requests.Timeout):
                module._fetch_surface_audit_bytes(datetime(2026, 1, 1, tzinfo=timezone.utc),
                    base + ".grib2", base + ".index", deadline=start + 3)
            assert time.monotonic() - start < 3.7
        assert seen.is_set(), "must exercise the HTTP phase, not merely time out during spawn startup"
        assert {p.pid for p in multiprocessing.active_children()} == before
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_hong_kong_selects_nearest_land_of_four_not_water_class_cell() -> None:
    from scripts.extract_open_ens_localday import _select_land_grid_points

    grid = {
        "gridType": "regular_ll", "Ni": 1440, "Nj": 721,
        "latitudeOfFirstGridPointInDegrees": 90.0,
        "longitudeOfFirstGridPointInDegrees": 180.0,
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "scanningMode": 0,
    }
    # Same-cycle IFS 0.25-degree LSM: the geometrically nearest cell has
    # 39.0625% land, whereas an adjacent cell has 50.78125%.
    fractions = {
        391417: 0.390625, 391416: 0.5078125,
        389977: 0.625, 389976: 0.65625,
    }
    selected = _select_land_grid_points(
        grid,
        [dict(city="Hong Kong", lat=22.3022, lon=114.1742)],
        fractions.__getitem__,
    )
    assert selected["Hong Kong"]["selected_flat_index"] == 391416
    assert selected["Hong Kong"]["selected_lat"] == 22.25
    assert selected["Hong Kong"]["selected_lon"] == 114.0
    assert selected["Hong Kong"]["selected_land_fraction"] == 0.5078125


def test_mask_possession_clock_cannot_use_pre_fetch_cycle_start() -> None:
    from src.data.ecmwf_open_data import _source_possession_clock

    cycle_start = datetime(2026, 9, 26, 6, tzinfo=timezone.utc)
    mask_fetched = datetime.now(timezone.utc)
    snapshot_time = _source_possession_clock(cycle_start, mask_fetched, extracted=True)
    source_run_time = _source_possession_clock(cycle_start, snapshot_time, extracted=True)
    assert cycle_start < mask_fetched <= snapshot_time <= source_run_time
    assert _source_possession_clock(cycle_start, None, extracted=False) == cycle_start


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_optional_surface_invalid_mask_adds_no_http_or_prediction_budget_gate(tmp_path, monkeypatch, track):
    from src.data import ecmwf_open_data

    root = tmp_path / "source"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    requests_seen = []

    def forbidden_network(*args, **kwargs):
        requests_seen.append((args, kwargs))
        raise AssertionError("optional audit capture must not start slow HTTP")

    monkeypatch.setattr(ecmwf_open_data._RateLimitedSession, "get", forbidden_network)
    issue = date(2026, 6, 6)
    trusted = _trusted_land_source_for_date(issue)
    mask_source = {key.removeprefix("mask_"): value for key, value in trusted.items()
                   if key.startswith("mask_source")}
    mask_source.update(mask_sha256=trusted["mask_sha256"],
                       mask_grid_identity_hash=trusted["mask_grid_identity_hash"])
    mask_deadlines, extract_timeouts = [], []
    deadline = time.monotonic() + 2
    temperature_finished = datetime.now(timezone.utc) - timedelta(hours=2)
    wall_clock = [temperature_finished]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(wall_clock[0].timestamp(), tz or timezone.utc)
    monkeypatch.setattr(ecmwf_open_data, "datetime", Clock)
    def optional_surface(**kwargs):
        # An optional audit's later clock must not replace temperature fetch completion.
        wall_clock[0] += timedelta(hours=1)
        return {"capture_status": "UNKNOWN", "unavailable_reason": "private timeout"}
    monkeypatch.setattr(ecmwf_open_data, "_fetch_cycle_surface_evidence", optional_surface)

    def mask_fetch(**kwargs):
        mask_deadlines.append(kwargs["deadline"])
        return mask_source

    def extract(cmd, *, label, timeout):
        extract_timeouts.append(timeout)
        manifest_path = Path(cmd[cmd.index("--manifest-path") + 1])
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        payload = _native_partial_scope_payload(track=track, target=date(2026, 6, 7),
                                                issue=issue, manifest_sha=manifest_sha)
        output_root = Path(cmd[cmd.index("--output-root") + 1])
        folder = output_root / ecmwf_open_data.TRACKS[track]["extract_subdir"] / "london" / "20260606"
        folder.mkdir(parents=True)
        (folder / "target.json").write_text(json.dumps(payload))
        assert "--surface-geopotential-grib-path" not in cmd
        assert "--surface-geopotential-proof-path" not in cmd
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    conn = _make_conn(tmp_path)
    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=issue, run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, tzinfo=timezone.utc), skip_download=True,
        conn=conn, _runner=extract, _mask_fetch_impl=mask_fetch,
        cycle_deadline_monotonic=deadline, extract_timeout_seconds=1,
    )
    assert requests_seen == []
    assert wall_clock[0] == temperature_finished + timedelta(hours=1)
    assert mask_deadlines == [deadline]
    assert len(extract_timeouts) == 1 and 0 < extract_timeouts[0] <= 2
    assert result["snapshots_inserted"] == 1, result
    assert conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 1
    stored = conn.execute("SELECT source_cycle_time, fetch_finished_at FROM source_run").fetchone()
    assert stored["source_cycle_time"] == "2026-06-06T00:00:00+00:00"
    assert datetime.fromisoformat(stored["fetch_finished_at"]) == temperature_finished


@pytest.mark.parametrize("remaining", (0, .25, 100))
def test_first_surface_allowance_is_one_attempt_inside_cycle_remainder(tmp_path, monkeypatch, remaining):
    from src.data import ecmwf_open_data as module

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["session"].before_get = lambda: True
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    transport = module._fetch_surface_audit_bytes
    deadlines = []
    def slow_optional(cycle, url, index_url, *, deadline):
        deadlines.append(deadline)
        result = transport(cycle, url, index_url, deadline=deadline)
        clock[0] = deadline + .001  # Native publication may not accept late bytes.
        return result
    monkeypatch.setattr(module, "_fetch_surface_audit_bytes", slow_optional)
    mask = fixture["surface_paths"]["mx2t6_high"].with_name(".mx2t6_high_20260101_00z_lsm.grib2")
    result = module._fetch_cycle_surface_evidence(cycle=fixture["issue"], mask_path=mask, deadline=100 + remaining)
    assert result["capture_status"] == "UNKNOWN"
    assert deadlines == ([] if remaining == 0 else [100 + min(remaining, module.OPENDATA_AVAILABILITY_PROBE_SECONDS)])
    assert len(fixture["session"].calls) == (0 if remaining == 0 else 2)
    assert not fixture["surface_paths"]["mx2t6_high"].exists()
    assert all(kwargs["timeout"] <= min(remaining, module.OPENDATA_AVAILABILITY_PROBE_SECONDS)
               for _url, kwargs in fixture["session"].calls)


@pytest.mark.parametrize("remaining", (.25, 1000))
def test_optional_surface_cannot_spend_mandatory_extract_budget(tmp_path, monkeypatch, remaining):
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module
    from tests.test_ingest_grib_source_run_context import _land_grid_proof

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch, tracks=("mx2t6_high",))
    fixture["session"].before_get = lambda: True
    monkeypatch.setattr(module, "_write_stderr_dump", lambda *args: None)
    clock = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    transport = module._fetch_surface_audit_bytes
    def late_surface(cycle, url, index_url, *, deadline):
        captured = transport(cycle, url, index_url, deadline=deadline)
        clock[0] += .251  # Actual index + range complete after the narrow cycle budget.
        return captured
    monkeypatch.setattr(module, "_fetch_surface_audit_bytes", late_surface)
    station = _land_grid_proof()["station_geometry"]
    manifest_json = json.dumps({"cities": [{"city": "London", "lat": 51.6, "lon": .1,
        "timezone": "Europe/London", "unit": "C", "station_geometry": station}]})
    extracted = []
    def mask_fetch(*, output_path, **kwargs):
        mask = extractor._read_land_mask(output_path, output_path.with_suffix(".proof.json"))
        return {**mask["proof"], "mask_grid_identity_hash": mask["grid_identity_hash"]}
    def extract(cmd, *, label, timeout):
        surface = fixture["surface_paths"]["mx2t6_high"] if "--surface-geopotential-grib-path" in cmd else None
        result = extractor.extract_open_ens_localday(grib_path=fixture["raw"], track_name="mx2t6_high",
            manifest_path=Path(cmd[cmd.index("--manifest-path") + 1]), output_root=tmp_path / "extracted",
            mask_grib_path=Path(cmd[cmd.index("--mask-grib-path") + 1]),
            mask_proof_path=Path(cmd[cmd.index("--mask-proof-path") + 1]),
            surface_geopotential_grib_path=surface,
            surface_geopotential_proof_path=surface.with_suffix(".proof.json") if surface else None)
        extracted.append((timeout, json.loads(Path(result["sample_outputs"][0]).read_bytes())))
        return {"label": label, "ok": False, "stderr_tail": "private extraction inspected"}
    result = module.collect_open_ens_cycle(track="mx2t6_high", run_date=fixture["issue"].date(), run_hour=0,
        skip_download=True, _runner=extract, _mask_fetch_impl=mask_fetch, extract_timeout_seconds=900,
        coordinate_manifest_json=manifest_json, _paths=fixture["paths"], conn=fixture["db"],
        cycle_deadline_monotonic=100 + remaining)
    assert extracted, f"OPTIONAL_Z_SUPPRESSED_MANDATORY_EXTRACT: {result}"
    timeout, payload = extracted[0]
    assert timeout == min(900, remaining)
    terrain = payload["native_capture_receipt"]["surface_geopotential_receipt_v1"]
    assert terrain["capture_status"] == ("UNKNOWN" if remaining < 900 else "OBSERVED")
    assert len(fixture["session"].calls) == (0 if remaining < 900 else 2)
    assert payload["members"][0]["value_native_unit"] == pytest.approx(286 - 273.15)
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


@pytest.mark.parametrize("fault", (None, "cycle", "grid", "type", "unit", "timeout", "no_budget", "race",
                                  "forwardmask", "oldbound", "oldbound_bad"))
def test_first_extract_surface_source_evidence_is_optional_and_shared(tmp_path, monkeypatch, fault):
    import eccodes as ec
    from scripts import extract_open_ens_localday as extractor
    from src.data import ecmwf_open_data as module
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib, _land_grid_proof

    fixture = _terrain_audit_fixture(tmp_path, monkeypatch)
    fixture["session"].before_get = lambda: True
    monkeypatch.setattr(module, "_write_stderr_dump", lambda *args: None)
    if fault == "forwardmask":
        mask = fixture["surface_paths"]["mx2t6_high"].with_name(".mx2t6_high_20260101_00z_lsm.grib2")
        assert module._fetch_cycle_surface_evidence(cycle=fixture["issue"], mask_path=mask)["capture_status"] == "OBSERVED"
    elif fault in ("oldbound", "oldbound_bad"):
        assert module.capture_open_ens_surface_audit()["capture_status"] == "OBSERVED"
    if fault in ("forwardmask", "oldbound_bad"):
        proof_path = fixture["surface_paths"]["mx2t6_high"].with_suffix(".proof.json")
        proof = json.loads(proof_path.read_bytes())
        proof["mask_sha256"] = "a" * 64
        proof_path.write_text(json.dumps(proof))
    if fault in ("cycle", "grid", "type", "unit"):
        gid = ec.codes_new_from_message(fixture["z_bytes"])
        try:
            field, value = {"cycle": ("dataDate", 20260102), "grid": ("scanningMode", 64),
                            "type": ("dataType", "cf"), "unit": ("paramId", 130)}[fault]
            ec.codes_set(gid, field, value)
            fixture["session"].z_bytes = ec.codes_get_message(gid)
        finally:
            ec.codes_release(gid)
    elif fault == "timeout":
        fixture["session"].failure = module.requests.Timeout("optional z timeout")
    elif fault == "no_budget":
        monkeypatch.setattr(module, "OPENDATA_AVAILABILITY_PROBE_SECONDS", 0)
    if fault == "race":
        original_link = module.os.link
        def racing_link(source, target):
            if Path(target) == fixture["surface_paths"]["mx2t6_high"] and not Path(target).exists():
                Path(target).write_bytes(b"foreign publisher")
            return original_link(source, target)
        monkeypatch.setattr(module.os, "link", racing_link)

    station = _land_grid_proof()["station_geometry"]
    manifest_json = json.dumps({"cities": [{"city": "London", "lat": 51.6, "lon": .1,
        "timezone": "Europe/London", "unit": "C", "station_geometry": station}]})
    receipts = []
    for track in ("mx2t6_high", "mn2t6_low"):
        folder = tmp_path / track
        folder.mkdir()
        raw, _mask, _proof, _ = _tiny_native_grib(folder, track, member_count=1)
        def mask_fetch(*, output_path, **kwargs):
            # Private real LSM is already at the registered track cache path.
            decoded = extractor._read_land_mask(output_path, output_path.with_suffix(".proof.json"))
            return {**decoded["proof"], "mask_grid_identity_hash": decoded["grid_identity_hash"]}
        def extract(cmd, *, label, timeout):
            authorized = fault in (None, "oldbound") or (fault in ("race", "forwardmask", "oldbound_bad") and track == "mn2t6_low")
            assert ("--surface-geopotential-grib-path" in cmd) == authorized
            surface = Path(cmd[cmd.index("--surface-geopotential-grib-path") + 1]) if authorized else None
            if authorized:
                assert surface.read_bytes() == fixture["z_bytes"], "z original must exist before first extract"
                proof = json.loads(surface.with_suffix(".proof.json").read_bytes())
                if fault not in ("oldbound", "oldbound_bad"):
                    assert "snapshot_id" not in proof and "source_run_id" not in proof
                mask_path = Path(cmd[cmd.index("--mask-grib-path") + 1])
                assert proof["mask_sha256"] == hashlib.sha256(mask_path.read_bytes()).hexdigest()
            result = extractor.extract_open_ens_localday(grib_path=raw, track_name=track,
                manifest_path=Path(cmd[cmd.index("--manifest-path") + 1]),
                output_root=folder / "extract", mask_grib_path=Path(cmd[cmd.index("--mask-grib-path") + 1]),
                mask_proof_path=Path(cmd[cmd.index("--mask-proof-path") + 1]),
                surface_geopotential_grib_path=surface,
                surface_geopotential_proof_path=surface.with_suffix(".proof.json") if surface else None)
            payload = json.loads(Path(result["sample_outputs"][0]).read_bytes())
            receipts.append(payload)
            return {"label": label, "ok": False, "stderr_tail": "private extraction inspected"}
        module.collect_open_ens_cycle(track=track, run_date=fixture["issue"].date(), run_hour=0,
            skip_download=True, _runner=extract, _mask_fetch_impl=mask_fetch,
            coordinate_manifest_json=manifest_json, _paths=fixture["paths"], conn=fixture["db"])
    assert len(receipts) == 2
    for track, payload in zip(("mx2t6_high", "mn2t6_low"), receipts):
        terrain = payload["native_capture_receipt"]["surface_geopotential_receipt_v1"]
        observed = fault in (None, "oldbound") or (fault in ("race", "forwardmask", "oldbound_bad") and track == "mn2t6_low")
        assert terrain["capture_status"] == ("OBSERVED" if observed else "UNKNOWN")
        assert payload["grid_surface_evidence"]["mask_source_fetched_at"] == "2026-01-01T01:00:00+00:00"
        if observed:
            assert terrain["raw_phi_m2_s2"] == 313.75
    assert len(fixture["session"].calls) == (0 if fault == "no_budget" else 2 if fault in (None, "oldbound", "oldbound_bad") else
                                           2 if fault == "timeout" else 4)
    assert tuple(fixture["db"].iterdump()) == fixture["before"]


def test_collector_does_not_authorize_payload_with_different_mask_hash(tmp_path, monkeypatch) -> None:
    from src.data import ecmwf_open_data
    from tests.test_opendata_writes_v2_table import (
        _make_opendata_high_payload, _trusted_land_source_for_issue,
    )

    root = tmp_path / "51 source data"
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    issue = "2026-05-01T00:00:00+00:00"
    payload = _make_opendata_high_payload(
        "2026-05-02", issue,
        local_day_start_iso="2026-05-01T23:00:00+00:00",
        local_day_end_iso="2026-05-02T23:00:00+00:00",
    )
    payload["grid_surface_evidence"]["mask_sha256"] = "d" * 64
    trusted = _trusted_land_source_for_issue(issue)
    mask_result = {
        "source": trusted["mask_source"],
        "source_url": trusted["mask_source_url"],
        "source_index_url": trusted["mask_source_index_url"],
        "source_cycle_time": trusted["mask_source_cycle_time"],
        "source_fetched_at": trusted["mask_source_fetched_at"],
        "source_index_offset": trusted["mask_source_index_offset"],
        "source_index_length": trusted["mask_source_index_length"],
        "mask_sha256": trusted["mask_sha256"],
        "mask_grid_identity_hash": trusted["mask_grid_identity_hash"],
    }

    def extract(cmd, *, label, timeout):
        output_root = Path(cmd[cmd.index("--output-root") + 1])
        folder = output_root / "open_ens_mx2t6_localday_max" / "london" / "20260501"
        folder.mkdir(parents=True)
        (folder / "target.json").write_text(json.dumps(payload), encoding="utf-8")
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    conn = _make_conn(tmp_path)
    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high", run_date=date(2026, 5, 1), run_hour=0,
        now_utc=datetime(2026, 5, 1, 9, tzinfo=timezone.utc),
        skip_download=True, conn=conn, _runner=extract,
        _mask_fetch_impl=lambda **_kw: mask_result,
    )
    assert result["status"] == "empty_ingest"
    assert conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("fractions", ({391417: 0.4}, {391417: float("nan")}))
def test_land_grid_selection_refuses_incomplete_or_invalid_mask(fractions) -> None:
    from scripts.extract_open_ens_localday import _select_land_grid_points

    grid = {
        "gridType": "regular_ll", "Ni": 1440, "Nj": 721,
        "latitudeOfFirstGridPointInDegrees": 90.0,
        "longitudeOfFirstGridPointInDegrees": 180.0,
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "scanningMode": 0,
    }
    with pytest.raises((KeyError, ValueError)):
        _select_land_grid_points(
            grid,
            [dict(city="Hong Kong", lat=22.3022, lon=114.1742)],
            fractions.__getitem__,
        )


def test_gridline_selection_keeps_four_distinct_points_and_wraps_longitude() -> None:
    from scripts.extract_open_ens_localday import _select_land_grid_points

    grid = {
        "gridType": "regular_ll", "Ni": 1440, "Nj": 721,
        "latitudeOfFirstGridPointInDegrees": 90.0,
        "longitudeOfFirstGridPointInDegrees": 180.0,
        "iDirectionIncrementInDegrees": 0.25,
        "jDirectionIncrementInDegrees": 0.25,
        "scanningMode": 0,
    }
    selected = _select_land_grid_points(
        grid, [{"city": "seam", "lat": 22.25, "lon": 179.75}],
        lambda idx: .75 if idx % 1440 == 0 else .25,
    )["seam"]
    assert {cell["flat_index"] for cell in selected["four_neighbors"]} == {
        271 * 1440 + 1439, 271 * 1440, 272 * 1440 + 1439, 272 * 1440,
    }
    assert selected["selected_lon"] == -180.0
    assert selected["selected_land_fraction"] == .75


@pytest.mark.parametrize("status, content_range, body_size", (
    (200, None, 32),
    (206, "bytes 5-35/99", 31),
    (206, "bytes 5-36/99", 31),
))
def test_static_mask_range_rejects_unproved_http_payload(
    status: int, content_range: str | None, body_size: int,
) -> None:
    from src.data.ecmwf_open_data import _read_static_mask_range

    class Response:
        headers = {"Content-Length": str(body_size)}
        status_code = status

        def __init__(self):
            self.headers = dict(self.headers)
            if content_range:
                self.headers["Content-Range"] = content_range

        def iter_content(self, chunk_size):
            yield b"G" * body_size

        def close(self):
            pass

    class Session:
        def get(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(Exception):
        _read_static_mask_range(Session(), "https://example.test/lsm.grib2", 5, 32)


def _native_partial_scope_payload(
    *, track: str, target: date, issue: date, manifest_sha: str,
) -> dict[str, object]:
    """Small full-member native-window payload for one London target day."""
    from tests.test_opendata_writes_v2_table import _make_opendata_high_payload
    from tests.test_ingest_grib_source_run_context import _land_grid_proof
    from src.data import ecmwf_open_data

    start = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc) - timedelta(hours=1)
    end = start + timedelta(days=1)
    issue_iso = f"{issue.isoformat()}T00:00:00+00:00"
    payload = _make_opendata_high_payload(
        target.isoformat(), issue_iso,
        local_day_start_iso=start.isoformat(), local_day_end_iso=end.isoformat(),
        forecast_window_start_iso=start.isoformat(),
        forecast_window_end_iso=end.isoformat(),
    )
    cfg = ecmwf_open_data.TRACKS[track]
    payload.update(
        manifest_sha256=manifest_sha, manifest_hash=manifest_sha,
        data_version=cfg["data_version"],
        lead_day=(target - issue).days,
        lat=51.505299, lon=0.055278,
        nearest_grid_lat=51.5, nearest_grid_lon=0.0,
    )
    proof = _land_grid_proof()
    proof["mask_source_cycle_time"] = issue_iso
    proof["mask_source_fetched_at"] = (datetime.fromisoformat(issue_iso) + timedelta(hours=1)).isoformat()
    payload["grid_surface_evidence"] = proof
    if track == "mn2t6_low":
        payload.update(
            physical_quantity="mn2t3_local_calendar_day_min",
            param="mn2t3", paramId=122, short_name="mn2t3",
            step_type="min", temperature_metric="low",
        )
        for member in payload["members"]:
            member["inner_min_native_unit"] = member.pop("inner_max_native_unit")
            member.pop("boundary_max_native_unit")
            member["boundary_min_native_unit"] = 30.0
            member["boundary_ambiguous"] = False
            for window in member["native_windows"]:
                if f'{window["start_step_hours"]}-{window["end_step_hours"]}' in member["boundary_step_ranges"]:
                    window["value_native_unit"] = 30.0
    return payload


def _trusted_land_source_for_date(run_date: date) -> dict[str, object]:
    from tests.test_ingest_grib_source_run_context import _land_grid_proof, _trusted_land_source

    issue = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    proof = _land_grid_proof()
    proof["mask_source_cycle_time"] = issue.isoformat()
    proof["mask_source_fetched_at"] = (issue + timedelta(hours=1)).isoformat()
    return _trusted_land_source(proof)


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_partial_retry_preserves_qualified_far_scope_and_publishes_near_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, track: str,
) -> None:
    """A near-only 503 retry cannot erase the far target from an earlier full run."""
    from src.config import runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    t1 = datetime.now(timezone.utc).replace(microsecond=0)
    t2 = t1 + timedelta(minutes=2)
    cut = t1 + timedelta(minutes=1)
    clock = {"now": t1}

    class CollectionDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(ecmwf_open_data, "datetime", CollectionDateTime)
    monkeypatch.setattr(ecmwf_open_data._ingest_grib_module, "datetime", CollectionDateTime)
    run_date = t1.date()
    near, far = run_date + timedelta(days=1), run_date + timedelta(days=2)
    metric = "high" if track == "mx2t6_high" else "low"
    root = tmp_path / "51 source data"
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", list(range(3, 73, 3)))
    manifest = runtime_coordinate_manifest_json()
    manifest_sha = hashlib.sha256(manifest.encode()).hexdigest()
    cfg = ecmwf_open_data.TRACKS[track]
    json_dir = (
        root / "raw" / "coordinate_manifests" / manifest_sha
        / cfg["extract_subdir"] / "london" / run_date.strftime("%Y%m%d")
    )
    json_dir.mkdir(parents=True)
    far_payload = _native_partial_scope_payload(
        track=track, target=far, issue=run_date, manifest_sha=manifest_sha,
    )
    far_path = json_dir / f"{cfg['extract_subdir']}_target_{far.isoformat()}_lead_2.json"
    far_path.write_text(json.dumps(far_payload), encoding="utf-8")

    failed_far = False

    def fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        if failed_far and step >= 51:
            return "FAILED", "HTTP_503"
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    first = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=clock["now"],
    )
    assert first["status"] == "ok", first
    old_far = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    old_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (far.isoformat(), metric),
    ).fetchone()
    assert old_far is not None and old_coverage["readiness_status"] == "LIVE_ELIGIBLE", tuple(
        conn.execute(
            "SELECT readiness_status, reason_code, expected_steps_json, observed_steps_json "
            "FROM source_run_coverage WHERE city='London' AND target_local_date=? "
            "AND temperature_metric=?", (far.isoformat(), metric),
        ).fetchone()
    )
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric, decision_time=cut,
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc), (
        tuple(conn.execute(
            "SELECT source_available_at, imported_at, fetch_finished_at, captured_at "
            "FROM source_run WHERE source_run_id=?", (first["source_run_id"],),
        ).fetchone()),
        tuple(conn.execute(
            "SELECT source_available_at, fetch_time FROM ensemble_snapshots "
            "WHERE city='London' AND target_date=? AND temperature_metric=?",
            (far.isoformat(), metric),
        ).fetchone()),
        tuple(old_coverage), cut,
    )
    first_possession = conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (first["source_run_id"],),
    ).fetchone()

    near_payload = _native_partial_scope_payload(
        track=track, target=near, issue=run_date, manifest_sha=manifest_sha,
    )
    for member in near_payload["members"]:
        for field in ("value_native_unit", "inner_max_native_unit", "boundary_max_native_unit",
                      "inner_min_native_unit", "boundary_min_native_unit"):
            if member.get(field) is not None:
                member[field] += 1.0
        for native_window in member["native_windows"]:
            native_window["value_native_unit"] += 1.0
    near_path = json_dir / f"{cfg['extract_subdir']}_target_{near.isoformat()}_lead_1.json"
    near_path.write_text(json.dumps(near_payload), encoding="utf-8")
    failed_far = True
    clock["now"] = t2
    second = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=clock["now"],
    )
    assert second["status"] == "ok", second
    current_far = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    current_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (far.isoformat(), metric),
    ).fetchone()
    assert tuple(current_far) == tuple(old_far)
    assert tuple(current_coverage) == tuple(old_coverage)
    near_row = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    near_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (near.isoformat(), metric),
    ).fetchone()
    assert near_row is not None and near_coverage["readiness_status"] == "LIVE_ELIGIBLE"
    assert json.loads(near_row["members_json"])[0] != json.loads(old_far["members_json"])[0]
    assert second["stages"][0]["status"] == "PARTIAL"
    assert second["stages"][0]["failed_steps"] == list(range(51, 73, 3))
    run = conn.execute("SELECT status, completeness_status, partial_run, "
                       "observed_steps_json, reason_code FROM source_run "
                       "WHERE source_run_id=?", (first["source_run_id"],)).fetchone()
    assert run["status"] == "SUCCESS" and run["completeness_status"] == "COMPLETE"
    assert run["partial_run"] == 0
    assert json.loads(run["observed_steps_json"]) == list(range(3, 73, 3))
    assert tuple(conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (first["source_run_id"],),
    ).fetchone()) == tuple(first_possession)
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric, decision_time=cut,
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=near, metric=metric, decision_time=cut,
    ) is None
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric,
        decision_time=t2 + timedelta(minutes=1),
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    third = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=clock["now"],
    )
    assert third["status"] == "ok", third
    repeat_near = conn.execute(
        "SELECT snapshot_id, members_json, source_available_at, fetch_time "
        "FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    repeat_coverage = conn.execute(
        "SELECT coverage_id, snapshot_ids_json, computed_at, recorded_at, "
        "readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?",
        (near.isoformat(), metric),
    ).fetchone()
    assert tuple(repeat_near) == tuple(near_row)
    assert tuple(repeat_coverage) == tuple(near_coverage)


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_partial_first_attempt_cannot_certify_far_scope_without_prior_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, track: str,
) -> None:
    """An incomplete far JSON cannot authorize a new far snapshot on a partial run."""
    from src.config import runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    run_date = datetime.now(timezone.utc).date()
    near, far = run_date + timedelta(days=1), run_date + timedelta(days=2)
    metric = "high" if track == "mx2t6_high" else "low"
    root = tmp_path / "51 source data"
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", list(range(3, 73, 3)))
    manifest_sha = hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest()
    cfg = ecmwf_open_data.TRACKS[track]
    json_dir = (
        root / "raw" / "coordinate_manifests" / manifest_sha
        / cfg["extract_subdir"] / "london" / run_date.strftime("%Y%m%d")
    )
    json_dir.mkdir(parents=True)
    for target, lead in ((near, 1), (far, 2)):
        payload = _native_partial_scope_payload(
            track=track, target=target, issue=run_date, manifest_sha=manifest_sha,
        )
        (json_dir / f"{cfg['extract_subdir']}_target_{target.isoformat()}_lead_{lead}.json").write_text(
            json.dumps(payload), encoding="utf-8",
        )

    def fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        if step >= 51:
            return "FAILED", "HTTP_503"
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=datetime.now(timezone.utc),
    )
    assert result["status"] == "ok", result
    near_coverage = conn.execute(
        "SELECT readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    far_snapshot = conn.execute(
        "SELECT snapshot_id FROM ensemble_snapshots WHERE city='London' "
        "AND target_date=? AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    far_coverage = conn.execute(
        "SELECT readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    assert near_coverage is not None and near_coverage["readiness_status"] == "LIVE_ELIGIBLE"
    assert far_snapshot is None
    assert far_coverage is None or far_coverage["readiness_status"] != "LIVE_ELIGIBLE"
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric,
        decision_time=datetime.now(timezone.utc) + timedelta(minutes=1),
    ) is None
    first_possession = tuple(conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (result["source_run_id"],),
    ).fetchone())

    previous_near = tuple(conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone())
    previous_near_coverage = tuple(conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone())

    def full_fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    completed = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=full_fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=datetime.now(timezone.utc) + timedelta(minutes=2),
    )
    assert completed["status"] == "ok", completed
    retained_near = conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    assert retained_near is not None and tuple(retained_near) == previous_near
    retained_near_coverage = conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone()
    assert retained_near_coverage is not None and tuple(retained_near_coverage) == previous_near_coverage
    recovered_run = conn.execute(
        "SELECT status, completeness_status, partial_run, observed_steps_json "
        "FROM source_run WHERE source_run_id=?", (completed["source_run_id"],),
    ).fetchone()
    assert tuple(recovered_run)[:3] == ("SUCCESS", "COMPLETE", 0)
    assert json.loads(recovered_run["observed_steps_json"]) == list(range(3, 73, 3))
    assert tuple(conn.execute(
        "SELECT source_available_at, imported_at FROM source_run WHERE source_run_id=?",
        (result["source_run_id"],),
    ).fetchone()) == first_possession
    recovered_far = conn.execute(
        "SELECT readiness_status FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (far.isoformat(), metric),
    ).fetchone()
    assert recovered_far is not None and recovered_far["readiness_status"] == "LIVE_ELIGIBLE"
    assert latest_eligible_ensemble_input_cycle(
        conn, city="London", target_date=far, metric=metric,
        decision_time=datetime.now(timezone.utc) + timedelta(minutes=3),
    ) == datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize(
    ("failure_mode", "expected_status"),
    (("failed", "download_failed"),
     ("not_released", "skipped_not_released"),
     ("deadline", "download_failed")),
)
def test_zero_ok_retry_preserves_same_run_qualified_far_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    track: str, failure_mode: str, expected_status: str,
) -> None:
    """A failed retry cannot revoke the existing complete same-run certificate."""
    from src.config import runtime_coordinate_manifest_json
    from src.data import ecmwf_open_data
    from src.data.replacement_input_hwm import latest_eligible_ensemble_input_cycle

    run_date = datetime.now(timezone.utc).date()
    far, near = run_date + timedelta(days=2), run_date + timedelta(days=1)
    metric = "high" if track == "mx2t6_high" else "low"
    root = tmp_path / "51 source data"
    conn = sqlite3.connect(tmp_path / "forecasts.db")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", root)
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", list(range(3, 73, 3)))
    monkeypatch.setattr(ecmwf_open_data, "_write_stderr_dump", lambda *_: None)
    manifest_sha = hashlib.sha256(runtime_coordinate_manifest_json().encode()).hexdigest()
    cfg = ecmwf_open_data.TRACKS[track]
    json_dir = (
        root / "raw" / "coordinate_manifests" / manifest_sha
        / cfg["extract_subdir"] / "london" / run_date.strftime("%Y%m%d")
    )
    json_dir.mkdir(parents=True)
    far_payload = _native_partial_scope_payload(
        track=track, target=far, issue=run_date, manifest_sha=manifest_sha,
    )
    (json_dir / f"{cfg['extract_subdir']}_target_{far.isoformat()}_lead_2.json").write_text(
        json.dumps(far_payload), encoding="utf-8",
    )

    def fetch(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del mirrors
        ecmwf_open_data._step_cache_path(
            output_dir, run_date=cycle_date, run_hour=cycle_hour,
            step=step, param=param,
        ).write_bytes(b"raw-grib-fixture")
        return "OK", None

    first = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=fetch,
        grid_surface_source_evidence=_trusted_land_source_for_date(run_date),
        now_utc=datetime.now(timezone.utc),
    )
    assert first["status"] == "ok", first
    run_id = first["source_run_id"]
    query_scope = (far.isoformat(), metric)
    old_run = tuple(conn.execute(
        "SELECT * FROM source_run WHERE source_run_id=?", (run_id,),
    ).fetchone())
    old_snapshot = tuple(conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", query_scope,
    ).fetchone())
    old_coverage = conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", query_scope,
    ).fetchone()
    assert old_coverage["readiness_status"] == "LIVE_ELIGIBLE"
    old_coverage = tuple(old_coverage)
    expected_issue = datetime.combine(run_date, datetime.min.time(), tzinfo=timezone.utc)
    def latest_far():
        return latest_eligible_ensemble_input_cycle(
            conn, city="London", target_date=far, metric=metric,
            decision_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
    assert latest_far() == expected_issue

    def zero_ok(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        del cycle_date, cycle_hour, param, step, output_dir, mirrors
        if failure_mode == "not_released":
            return "NOT_RELEASED", "HTTP_404"
        return "FAILED", "HTTP_503"

    retry = ecmwf_open_data.collect_open_ens_cycle(
        track=track, run_date=run_date, run_hour=0, conn=conn,
        skip_extract=True, _fetch_impl=zero_ok,
        cycle_deadline_monotonic=(time.monotonic() - 1 if failure_mode == "deadline" else None),
        now_utc=datetime.now(timezone.utc),
    )
    assert retry["status"] == expected_status, retry
    if failure_mode == "deadline":
        assert retry["reason"] == "CYCLE_DEADLINE_EXCEEDED"
    assert retry["snapshots_inserted"] == 0
    assert tuple(conn.execute(
        "SELECT * FROM source_run WHERE source_run_id=?", (run_id,),
    ).fetchone()) == old_run
    assert tuple(conn.execute(
        "SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date=? "
        "AND temperature_metric=?", query_scope,
    ).fetchone()) == old_snapshot
    assert tuple(conn.execute(
        "SELECT * FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", query_scope,
    ).fetchone()) == old_coverage
    assert latest_far() == expected_issue
    assert conn.execute(
        "SELECT 1 FROM source_run_coverage WHERE city='London' "
        "AND target_local_date=? AND temperature_metric=?", (near.isoformat(), metric),
    ).fetchone() is None


def _make_conn(tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "world.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


def _ok_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
    """Fake _fetch_impl that writes a zero-byte canonical file for each step."""
    canonical = output_dir / f".step{step:03d}_{param}.grib2"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_bytes(b"\x00" * 16)  # non-empty so resume logic treats it as done
    return ("OK", canonical)


def _write_raw_group(root: Path, day: str, hour: int, param: str) -> list[Path]:
    day_dir = root / "raw" / "ecmwf_open_ens" / "ecmwf" / day
    day_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        day_dir / f".{day}_{hour:02d}z_step003_{param}_ens51.grib2",
        day_dir / f"open_ens_{day}_{hour:02d}z_steps_3to144_n48_test_params_{param}.grib2",
    ]
    for path in paths:
        path.write_bytes(b"raw")
    return paths


def _record_raw_authority(
    conn: sqlite3.Connection,
    *,
    day: str,
    hour: int,
    param: str,
    status: str = "SUCCESS",
    completeness: str = "COMPLETE",
    partial: bool = False,
    expected: int = 2,
    snapshot_count: int = 2,
    authority: str = "VERIFIED",
    coordsha: str | None = "6e28420e5809b3b30f5a075629c0ebd56b1fa15d405a00a85455b4fbcd1a14f4",
) -> None:
    track = "mx2t6_high" if param == "mx2t3" else "mn2t6_low"
    metric = "high" if param == "mx2t3" else "low"
    iso_day = datetime.strptime(day, "%Y%m%d").date().isoformat()
    # collect_open_ens_cycle stamps ":coordsha:<manifest_sha>" onto the id. The
    # fixture wrote only the bare cycle prefix, so every retention test passed
    # while production matched nothing and evicted nothing for 9 days (live
    # 2026-09-18: 10 recognized groups, all "source_run: MISSING"). Default to
    # the real shape; pass coordsha=None for the legacy bare-id form.
    source_run_id = f"ecmwf_open_data:{track}:{iso_day}T{hour:02d}Z"
    if coordsha is not None:
        source_run_id = f"{source_run_id}:coordsha:{coordsha}"
    write_source_run(
        conn,
        source_run_id=source_run_id,
        source_id="ecmwf_open_data",
        track=track,
        release_calendar_key=f"ecmwf_open_data:{track}:standard",
        source_cycle_time=f"{iso_day}T{hour:02d}:00:00+00:00",
        status=status,
        completeness_status=completeness,
        partial_run=partial,
        expected_count=expected,
        observed_count=expected,
    )
    for index in range(snapshot_count):
        conn.execute(
            """
            INSERT INTO ensemble_snapshots (
                city, target_date, temperature_metric, physical_quantity,
                observation_field, issue_time, available_at, fetch_time,
                lead_hours, members_json, model_version, dataset_id,
                source_id, source_run_id, authority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"City{index}",
                iso_day,
                metric,
                "daily_extreme",
                "high_temp" if metric == "high" else "low_temp",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                f"{iso_day}T{hour:02d}:00:00+00:00",
                24.0,
                "[1.0]",
                "test",
                f"test_{source_run_id}",
                "ecmwf_open_data",
                source_run_id,
                authority,
            ),
        )
    conn.commit()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_opposite_first_original_custody_needs_only_the_actual_market(tmp_path, monkeypatch, metric):
    """A sibling transport completes first; one real market still needs its bytes."""
    import eccodes as ec
    import numpy as np
    import tempfile
    from src import config
    from src.state import db as state_db
    from src.data import ecmwf_open_data as native
    from src.data import replacement_forecast_production as production
    from src.data.day0_hourly_vectors import read_native_measurement_role
    from src.config import runtime_coordinate_manifest_json, runtime_cities_by_name
    from scripts import extract_open_ens_localday as decoder
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib

    set_values = ec.codes_set_values
    def original_temperature(gid, values):
        if ec.codes_get(gid,"paramId") in {167,228026,228027}:
            ec.codes_set(gid,"packingType","grid_ieee")
            ec.codes_set(gid,"precision",2)
            values = np.full(len(values),284.15)
        return set_values(gid,values)
    monkeypatch.setattr(ec,"codes_set_values",original_temperature)
    s = _normal_native_http(tmp_path,monkeypatch,steps=tuple(range(0,37,3)),hour=12)
    state_db.init_schema_world_only(s.conn)
    ledgers = Path(tempfile.mkdtemp(prefix="opposite-first-"+metric+"-",dir=config.STATE_DIR))
    for filename,initialize in (("zeus-world.db",state_db.init_schema_world_only),
                                ("zeus_trades.db",state_db.init_schema_trade_only)):
        with sqlite3.connect(ledgers/filename) as ledger: initialize(ledger)
    monkeypatch.setattr(config,"STATE_DIR",ledgers)
    monkeypatch.setattr(state_db,"ZEUS_WORLD_DB_PATH",ledgers/"zeus-world.db")
    monkeypatch.setattr(state_db,"ZEUS_FORECASTS_DB_PATH",Path(s.conn.execute("PRAGMA database_list").fetchone()[2]))
    queues = {key:ledgers/key for key in ("seed_dir","request_dir","inflight_dir")}
    for directory in queues.values(): directory.mkdir()
    monkeypatch.setattr(production,"_replacement_forecast_live_materialization_queue_config",lambda:queues)
    # No opposite market, request, posterior or command is fabricated.
    s.conn.execute("""INSERT INTO market_events(market_slug,city,target_date,temperature_metric,
        condition_id,token_id,range_label,range_low,range_high)
        VALUES('opposite-first','London','2026-10-04',?,'private-condition','private-token','11°C',11,11)""",(metric,))
    s.conn.commit()
    captured = s.run+timedelta(hours=10)
    decision_clock = [captured]
    class ClockType(type):
        def __instancecheck__(cls,value): return isinstance(value,datetime)
    class NativeClock(datetime,metaclass=ClockType):
        @classmethod
        def now(cls,tz=None): return decision_clock[0].astimezone(tz or timezone.utc)
    monkeypatch.setattr(native,"datetime",NativeClock)
    monkeypatch.setattr(decoder,"datetime",NativeClock)
    monkeypatch.setattr(native._ingest_grib_module,"_now_utc_iso",lambda:captured.isoformat())
    builtin = sqlite3.connect(":memory:")
    s.conn.create_function("strftime",2,lambda fmt,value:captured.isoformat(timespec="milliseconds")
        if (fmt,value)==("%Y-%m-%dT%H:%M:%f+00:00","now") else builtin.execute("SELECT strftime(?,?)",(fmt,value)).fetchone()[0])
    try:
        acquired = native.collect_native_temperature_source(**{**s.args,"cycle_deadline_monotonic":time.monotonic()+59})
        assert acquired["status"] == "AVAILABLE",acquired
        paths = native._resolve_opendata_paths(source_root=s.paths.raw_root,environ={})
        monkeypatch.setattr(native,"_resolve_opendata_paths",lambda **kwargs:paths)
        manifest = tmp_path/"coordinate-manifest.json"
        manifest.write_text(runtime_coordinate_manifest_json())
        coord_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        static_dir = tmp_path/"static";static_dir.mkdir()
        static = _native_temperature_knots_fixture(static_dir,steps=(0,3),hour=12)
        opposite = "low" if metric=="high" else "high"
        first = None
        for quantity in (opposite,metric):
            track = "mx2t6_high" if quantity=="high" else "mn2t6_low"
            folder = tmp_path/track;folder.mkdir()
            raw,_,_,_ = _tiny_native_grib(folder,track,issue=s.run,horizon=36)
            messages = []
            with raw.open("rb") as stream:
                while (gid:=ec.codes_grib_new_from_file(stream)) is not None:
                    try:
                        ec.codes_set(gid,"generatingProcessIdentifier",161)
                        messages.append(ec.codes_get_message(gid))
                    finally: ec.codes_release(gid)
            target = native._download_output_path(run_date=s.run.date(),run_hour=12,
                param=decoder.TRACKS[track].open_data_param,raw_root=paths.raw_root)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b"".join(messages))
            mask,phi = _physical_static_originals(static,directory=target.parent,track=track)
            decoded = decoder.extract_open_ens_localday(grib_path=target,track_name=track,manifest_path=manifest,
                cities_filter={"London"},output_root=paths.raw_root/"raw/coordinate_manifests"/coord_sha,
                mask_grib_path=mask,mask_proof_path=mask.with_suffix(".proof.json"),
                surface_geopotential_grib_path=phi,surface_geopotential_proof_path=phi.with_suffix(".proof.json"))
            sample = json.loads(Path(decoded["sample_outputs"][0]).read_bytes())
            collected = native.collect_open_ens_cycle(track=track,skip_download=True,skip_extract=True,
                conn=s.conn,now_utc=captured,_paths=paths,grid_surface_source_evidence=sample["grid_surface_evidence"])
            assert collected["status"] == "ok" and collected["raw_retention"]["status"] == "APPLIED",collected
            assert not target.exists()
            row = s.conn.execute("SELECT * FROM ensemble_snapshots WHERE city='London' AND target_date='2026-10-04' AND temperature_metric=?",(quantity,)).fetchone()
            captures = json.loads(row["provenance_json"])["native_capture_receipt"]["messages"]
            originals = {native._role_message_path(paths.raw_root,capture["raw_message_sha256"]):capture for capture in captures}
            assert all(path.is_file() for path in originals), "opposite-first originals disappeared before counterpart capture"
            if first is None:
                assert not s.conn.execute("SELECT 1 FROM ensemble_snapshots WHERE temperature_metric=?",(metric,)).fetchone()
                first = (row["snapshot_id"],tuple(row),{path:(path.read_bytes(),path.stat().st_mtime_ns) for path in originals})
            else:
                assert tuple(s.conn.execute("SELECT * FROM ensemble_snapshots WHERE snapshot_id=?",(first[0],)).fetchone()) == first[1]
                assert all((path.read_bytes(),path.stat().st_mtime_ns)==body for path,body in first[2].items())
        rows = {table:tuple(tuple(row) for row in s.conn.execute(f"SELECT * FROM {table}"))
            for table in ("source_run","ensemble_snapshots","source_run_coverage")}
        decision_clock[0] = captured+timedelta(seconds=1)
        role = read_native_measurement_role(conn=s.conn,city=runtime_cities_by_name()["London"],
            target_date="2026-10-04",metric=metric,role="full_Y",scope_start=datetime(2026,10,3,23,tzinfo=timezone.utc),
            decision_time=decision_clock[0],snapshot_id=row["snapshot_id"],_paths=paths)
        assert role["member_points_native"] == [11.]*51
        assert role["native_snapshot_id"] == row["snapshot_id"]
        assert rows == {table:tuple(tuple(row) for row in s.conn.execute(f"SELECT * FROM {table}")) for table in rows}
        # Removal of the only real market releases this finite cohort normally;
        # storage candidates were not converted into durable q/action authority.
        s.conn.execute("DELETE FROM market_events WHERE market_slug='opposite-first'");s.conn.commit()
        plan = native._plan_decoded_open_data_raw_retention(s.conn,raw_root=paths.raw_root,
            reference_date=captured.date(),reference_time=captured)
        assert plan.role_reference_complete,plan.role_reference_errors
        drained = native._apply_decoded_open_data_raw_retention(plan)
        assert drained["role_message_deleted_count"] > 0
        assert all(not path.exists() for path in first[2])
    finally:
        s.conn.close();builtin.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_role_originals_survive_real_aggregate_retention(tmp_path, monkeypatch, metric, _bootstrap_only=False):
    """Normal capture/commit/cleanup feeds the unmocked public role reader."""
    import eccodes as ec
    import numpy as np
    from tests import test_replacement_forecast_materializer as fixture
    from src.data import ecmwf_open_data as native
    from src.data import day0_hourly_vectors as hourly

    # Retention owns storage candidates, not probability qualification. The
    # real collector/extractor/action reader may decode; each refs scan may not.
    refs_reader = native._role_original_snapshot_references
    gc_decode_calls = []
    def metadata_only_refs(*args, **kwargs):
        def forbidden_decode(*args, **kwargs):
            gc_decode_calls.append(True)
            raise RuntimeError("GC_PUBLIC_ROLE_DECODE_FORBIDDEN")
        with monkeypatch.context() as gc:
            gc.setattr(hourly, "read_native_measurement_role", forbidden_decode)
            gc.setattr(ec, "codes_get_values", forbidden_decode)
            return refs_reader(*args, **kwargs)
    monkeypatch.setattr(native, "_role_original_snapshot_references", metadata_only_refs)

    # Actual original bytes are controlled before transport/index/capture.
    # No capture header, possession, role qualification or width is mocked.
    set_values = ec.codes_set_values
    def atom_values(gid, values):
        if ec.codes_get(gid, "paramId") in {167, 228026, 228027}:
            ec.codes_set(gid, "packingType", "grid_ieee")
            ec.codes_set(gid, "precision", 2)
            values = np.full(len(values), 284.15)
        return set_values(gid, values)
    monkeypatch.setattr(ec, "codes_set_values", atom_values)
    shape_reader = fixture.materializer_mod._read_current_evidence_shape
    class Verified(BaseException):
        pass
    def check_retained_role(conn, request, **kwargs):
        assert not gc_decode_calls, "GC must not decode/qualify the full grid"
        shape = shape_reader(conn, request, **kwargs)
        assert shape is not None
        point = shape.native_point_model
        assert point["member_points_c"] == [11.] * 51
        paths = native._resolve_opendata_paths()
        source = native._download_output_path(run_date=date(2026, 10, 3), run_hour=12,
            param="mx2t3" if metric == "high" else "mn2t3", raw_root=paths.raw_root)
        assert not source.exists()  # Real operator zero-grace cleanup executed.
        snapshot = conn.execute("SELECT provenance_json FROM ensemble_snapshots WHERE snapshot_id=?",
            (shape.snapshot_id,)).fetchone()
        capture = json.loads(snapshot[0])["native_capture_receipt"]["messages"][0]
        retained = native._role_message_path(paths.raw_root, capture["raw_message_sha256"])
        original = retained.read_bytes()
        clocks = (point["interval_snapshot_available_at"], point["interval_snapshot_written_at"])
        assert clocks == ("2026-10-03T22:00:00+00:00", "2026-10-03T22:00:00.000+00:00")
        retained.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        assert shape_reader(conn, request, **kwargs) is None
        retained.write_bytes(original)  # Exact evidence restoration, no new clock.
        restored = shape_reader(conn, request, **kwargs)
        assert restored is not None
        assert (restored.native_point_model["interval_snapshot_available_at"],
                restored.native_point_model["interval_snapshot_written_at"]) == clocks

        # Real private canonical reference rows, not a mocked references API.
        # This is a custody ledger fixture; it grants no venue/q admission.
        from src import config
        from src.state import db as state_db
        from src.data import replacement_forecast_production as production
        from src.data.replacement_forecast_cycle_policy import replacement_source_cycle_max_age_hours
        from src.contracts.executable_market_snapshot import FRESHNESS_WINDOW_DEFAULT
        from src.execution.command_bus import CommandState
        ledgers = tmp_path / "ledgers"
        ledgers.mkdir()
        world_path, trade_path = ledgers / "zeus-world.db", ledgers / "zeus_trades.db"
        world, trade = sqlite3.connect(world_path), sqlite3.connect(trade_path)
        state_db.init_schema_world_only(world)
        state_db.init_schema_trade_only(trade)
        world.commit(); trade.commit()
        monkeypatch.setattr(config, "STATE_DIR", ledgers)
        monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_path)
        queued = {name: tmp_path / name for name in ("seed_dir", "request_dir", "inflight_dir")}
        for directory in queued.values():
            # The normal producer already created these exact private empty
            # queues; reuse them, without clearing any original reference.
            assert directory.is_relative_to(tmp_path) and (not directory.exists() or not any(directory.iterdir()))
            directory.mkdir(exist_ok=True)
        monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: queued)
        provenance = {"bayes_precision_fusion": {"current_evidence_shape": shape.as_payload()}}
        # Retain actual primary AND actual paired original identities.
        posterior_hash = "private-custody-posterior-" + metric
        cycle = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
        if not _bootstrap_only:
            # Isolate the Prepared/posterior age axis from the independent
            # current-market storage frontier installed by the normal producer.
            # Only these exact private H/L market references cease to exist;
            # no acquired original, source clock or certificate is edited.
            market_references = tuple(tuple(row) for row in conn.execute(
                "SELECT * FROM market_events WHERE city='London' AND target_date='2026-10-04' AND temperature_metric IN ('high','low')"))
            assert market_references
            assert not conn.execute("SELECT 1 FROM forecast_posteriors LIMIT 1").fetchone()
            assert not trade.execute("SELECT 1 FROM venue_commands LIMIT 1").fetchone()
            assert not trade.execute("SELECT 1 FROM position_current LIMIT 1").fetchone()
            assert all(not any(directory.iterdir()) for directory in queued.values())
            conn.execute("DELETE FROM market_events WHERE city='London' AND target_date='2026-10-04' AND temperature_metric IN ('high','low')")
            conn.commit()
            assert not conn.execute("SELECT 1 FROM market_events WHERE city='London' AND target_date='2026-10-04' AND temperature_metric IN ('high','low')").fetchone()
            cursor = conn.execute("""INSERT INTO forecast_posteriors(source_id,product_id,data_version,city,
            target_date,temperature_metric,source_cycle_time,source_available_at,computed_at,
            q_json,posterior_method,posterior_identity_hash,provenance_json)
            VALUES('TEST_ONLY_CUSTODY','TEST_ONLY_CUSTODY','TEST_ONLY_CUSTODY','London','2026-10-04',?,?,?,?,?,'TEST_ONLY_CUSTODY',?,?)""",
            (metric, cycle.isoformat(), clocks[0], request.computed_at.isoformat(), "[]", posterior_hash, json.dumps(provenance)))
            posterior_id = cursor.lastrowid
            conn.commit()
        boundary = cycle + timedelta(hours=replacement_source_cycle_max_age_hours()) + 2 * FRESHNESS_WINDOW_DEFAULT
        def sweep(at):
            plan = native._plan_decoded_open_data_raw_retention(conn, raw_root=paths.raw_root,
                reference_date=at.date(), reference_time=at)
            assert plan.role_reference_complete, plan.role_reference_errors
            return native._apply_decoded_open_data_raw_retention(plan)
        if not _bootstrap_only:
            assert sweep(boundary)["role_message_deleted_count"] >= 0
            assert retained.exists()  # <= the real derived deadline remains live.
            late = boundary + timedelta(microseconds=1)
            replicas = {p: p.read_bytes() for p in retained.parent.glob("*.grib2")}
            assert sweep(late)["role_message_deleted_count"] > 0
            assert not retained.exists()  # +epsilon without consumers really clears.
            for path, body in replicas.items():
                path.write_bytes(body)  # Restore only the exact previously captured bytes.

        # A new ordinary collector capture replaces the legal Y frontier,
        # without unpinning the old frozen posterior/unfinished dependencies.
        from tests.test_ingest_grib_source_run_context import _tiny_native_grib
        from scripts import extract_open_ens_localday as decoder
        from src.config import runtime_coordinate_manifest_json
        next_run = cycle + timedelta(hours=6)
        next_now = next_run + timedelta(hours=10)
        conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES('next-normal-frontier','London','2026-10-04',?,'next-normal-token','11C')", (metric,))
        conn.commit()
        class NextClock(native.datetime):
            @classmethod
            def now(cls, tz=None):
                return next_now.astimezone(tz or timezone.utc)
        monkeypatch.setattr(native, "datetime", NextClock)
        monkeypatch.setattr(native._ingest_grib_module, "_now_utc_iso", lambda: next_now.isoformat())
        sql_clock = sqlite3.connect(":memory:")
        conn.create_function("strftime", 2, lambda fmt, value:
            next_now.isoformat(timespec="milliseconds") if (fmt, value) == ("%Y-%m-%dT%H:%M:%f+00:00", "now")
            else sql_clock.execute("SELECT strftime(?,?)", (fmt, value)).fetchone()[0])
        coord_json = runtime_coordinate_manifest_json()
        coord_sha = hashlib.sha256(coord_json.encode()).hexdigest()
        coord = tmp_path / "next-coordinate-manifest.json"
        coord.write_text(coord_json)
        static_dir = tmp_path / "next-static"
        static_dir.mkdir()
        static = _native_temperature_knots_fixture(static_dir, steps=(0, 3), hour=18)
        for next_metric, track_name in (("high", "mx2t6_high"), ("low", "mn2t6_low")):
            folder = tmp_path / ("next-" + track_name)
            folder.mkdir()
            original_path, _, _, _ = _tiny_native_grib(folder, track_name, issue=next_run, horizon=36)
            track = decoder.TRACKS[track_name]
            target = native._download_output_path(run_date=next_run.date(), run_hour=18,
                param=track.open_data_param, raw_root=paths.raw_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            bodies = []
            with original_path.open("rb") as stream:
                while (gid := ec.codes_grib_new_from_file(stream)) is not None:
                    try:
                        ec.codes_set(gid, "generatingProcessIdentifier", 161)
                        bodies.append(ec.codes_get_message(gid))
                    finally:
                        ec.codes_release(gid)
            target.write_bytes(b"".join(bodies))
            mask, phi = _physical_static_originals(static, directory=target.parent, track=track_name)
            extracted = decoder.extract_open_ens_localday(grib_path=target, track_name=track_name,
                manifest_path=coord, cities_filter={"London"},
                output_root=paths.raw_root / "raw/coordinate_manifests" / coord_sha,
                mask_grib_path=mask, mask_proof_path=mask.with_suffix(".proof.json"),
                surface_geopotential_grib_path=phi, surface_geopotential_proof_path=phi.with_suffix(".proof.json"))
            sample = json.loads(Path(extracted["sample_outputs"][0]).read_text())
            collected = native.collect_open_ens_cycle(track=track_name, skip_download=True,
                skip_extract=True, conn=conn, now_utc=next_now, _paths=paths,
                grid_surface_source_evidence=sample["grid_surface_evidence"])
            assert collected["status"] == "ok", collected
            assert not target.exists()
            assert conn.execute("SELECT COUNT(*) FROM ensemble_snapshots WHERE city='London' AND temperature_metric=? AND source_cycle_time=?",
                (next_metric, next_run.isoformat())).fetchone()[0] > 0
        if _bootstrap_only:
            from src.config import runtime_cities_by_name
            from src.data.day0_hourly_vectors import read_native_measurement_role
            assert conn.execute("SELECT COUNT(*) FROM forecast_posteriors").fetchone()[0] == 0
            assert not any(p.is_file() for d in queued.values() for p in d.rglob("*.json"))
            assert conn.execute("SELECT COUNT(*) FROM source_run WHERE track='2t_instant_native_knots' AND source_cycle_time=?",
                (next_run.isoformat(),)).fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM source_run WHERE source_cycle_time=? AND status='SUCCESS' AND completeness_status='COMPLETE' AND partial_run=0",
                (next_run.isoformat(),)).fetchone()[0] == 2
            city = runtime_cities_by_name()["London"]
            y = read_native_measurement_role(conn=conn, city=city, target_date="2026-10-04",
                decision_time=next_now, metric=metric, role="full_Y",
                scope_start=datetime(2026, 10, 3, 23, tzinfo=timezone.utc), _paths=paths)
            assert y["native_snapshot_id"] == shape.snapshot_id
            assert len(y["member_points_native"]) == 51
            current_snapshot = conn.execute("SELECT snapshot_id FROM ensemble_snapshots WHERE city='London' AND target_date='2026-10-04' AND temperature_metric=? AND source_cycle_time=?",
                (metric, next_run.isoformat())).fetchone()[0]
            with pytest.raises(ValueError, match="MEASUREMENT_ROLE_NATIVE_POINT_UNAVAILABLE"):
                read_native_measurement_role(conn=conn, city=city, target_date="2026-10-04",
                    decision_time=next_now, metric=metric, role="remaining_X", scope_start=next_now,
                    snapshot_id=current_snapshot, _paths=paths)
            assert sweep(next_now)["role_message_deleted_count"] == 0
            assert native._read_role_message_bytes(paths.raw_root, capture) == original
            assert y["interval_snapshot_available_at"] == clocks[0]
            # Missing candidate geometry is UNKNOWN for this scope, not a
            # license to erase its possible originals or authorize an action.
            saved_provenance = conn.execute("SELECT provenance_json FROM ensemble_snapshots WHERE snapshot_id=?",
                (shape.snapshot_id,)).fetchone()[0]
            malformed = json.loads(saved_provenance)
            malformed["native_capture_receipt"].pop("selected_point")
            conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=?",
                (json.dumps(malformed), shape.snapshot_id))
            conn.commit()
            unknown = native._plan_decoded_open_data_raw_retention(conn, raw_root=paths.raw_root,
                reference_date=next_now.date(), reference_time=next_now)
            assert "ROLE_RETENTION_NATIVE_FRONTIER_UNKNOWN" in unknown.role_reference_errors
            assert capture["raw_message_sha256"] in unknown.live_role_hashes
            conn.execute("UPDATE ensemble_snapshots SET provenance_json=? WHERE snapshot_id=?",
                (saved_provenance, shape.snapshot_id))
            conn.commit()
            assert "ROLE_RETENTION_NATIVE_FRONTIER_UNKNOWN" not in native._plan_decoded_open_data_raw_retention(
                conn, raw_root=paths.raw_root, reference_date=next_now.date(), reference_time=next_now).role_reference_errors
            assert not gc_decode_calls
            sql_clock.close(); world.close(); trade.close()
            raise Verified
        assert sweep(next_now)["role_message_deleted_count"] == 0
        assert retained.exists()  # A new frontier alone cannot erase old authority.
        aggregate_root = paths.raw_root / "raw/ecmwf_open_ens/ecmwf"
        parked_root = aggregate_root.with_name("private-aggregate-parked")
        aggregate_root.rename(parked_root)
        assert sweep(next_now)["role_message_deleted_count"] == 0
        assert retained.exists()  # Missing aggregates do not imply missing consumers.
        postday = datetime(2026, 10, 5, tzinfo=timezone.utc)
        batch = queued["inflight_dir"] / "claimed-real-request"
        batch.mkdir()
        pending = batch / "request.json"
        pending.write_text(json.dumps({"city": "London", "target_date": "2026-10-04",
            "temperature_metric": metric, "computed_at": request.computed_at.isoformat(),
            "posterior_id": posterior_id}))
        sweep(postday)  # Unreferenced new-run bodies may already drain.
        assert retained.exists()  # Immutable request cut is not wall-clock age.
        from src.data import replacement_forecast_live_materialization_queue as queue
        staged = batch.with_name(queue._STAGING_PREFIX + "actual-old-cut")
        batch.rename(staged)
        assert sweep(postday)["role_message_deleted_count"] == 0
        assert retained.exists()  # Normal unpublished atomic-claim window.
        staged.rename(batch)
        assert sweep(postday)["role_message_deleted_count"] == 0
        recovered = queued["request_dir"] / "request.json"
        (batch / pending.name).rename(recovered)
        batch.rmdir()
        assert sweep(postday)["role_message_deleted_count"] == 0
        assert retained.exists()  # Normal reverse recovery still holds the cut.
        capture_dir = queued["request_dir"].parent / queue._REQUEST_ALIAS_DIR / (queue._CAPTURE_PREFIX + "actual-old-cut")
        payload_dir = capture_dir / queue._CAPTURE_PAYLOAD_DIR
        payload_dir.mkdir(parents=True)
        recovered.rename(payload_dir / recovered.name)
        assert sweep(postday)["role_message_deleted_count"] == 0
        assert retained.exists()  # Regular capture payload awaits normal recovery.
        assert queue._settle_capture(capture_dir, queued["request_dir"]) is None
        assert recovered.exists()
        assert sweep(postday)["role_message_deleted_count"] == 0
        moved_batch = queued["inflight_dir"] / "private-moving-claim"
        moved_batch.mkdir()
        actual_read = queue.read_regular_request
        moved = []
        def move_during_read(path):
            result = actual_read(path)
            if path == recovered and not moved:
                recovered.rename(moved_batch / recovered.name)
                moved.append(True)
            return result
        with monkeypatch.context() as concurrent_move:
            concurrent_move.setattr(queue, "read_regular_request", move_during_read)
            changing = native._plan_decoded_open_data_raw_retention(conn, raw_root=paths.raw_root,
                reference_date=postday.date(), reference_time=postday)
        assert "ROLE_RETENTION_REFERENCE_MUTATED" in changing.role_reference_errors
        assert not changing.role_reference_complete
        assert native._apply_decoded_open_data_raw_retention(changing)["role_message_deleted_count"] == 0
        assert retained.exists()
        assert sweep(postday)["role_message_deleted_count"] == 0
        (moved_batch / recovered.name).unlink()
        moved_batch.rmdir()
        empty_plan = native._plan_decoded_open_data_raw_retention(conn, raw_root=paths.raw_root,
            reference_date=postday.date(), reference_time=postday)
        assert capture["raw_message_sha256"] not in empty_plan.live_role_hashes
        recovered.write_text(json.dumps({"city": "London", "target_date": "2026-10-04",
            "temperature_metric": metric, "computed_at": request.computed_at.isoformat(),
            "posterior_id": posterior_id}))
        refreshed = native._apply_decoded_open_data_raw_retention(empty_plan)
        assert not refreshed["errors"] and retained.exists()
        recovered.unlink()  # A valid new exact reference also defeats stale GC.
        # A derived Day0 q_version must resolve through the actual command
        # attribution and WORLD parent edge, not string-match posterior hash.
        for cert_id, digest, body in (("parent", "parent-hash", {"posterior_id": posterior_id}),
                                     ("child", "child-hash", {"TEST_ONLY_CUSTODY_REFERENCE": True})):
            world.execute("""INSERT INTO decision_certificates(certificate_id,certificate_type,schema_version,
                canonicalization_version,semantic_key,claim_type,mode,decision_time,authority_id,
                authority_version,algorithm_id,algorithm_version,payload_json,payload_hash,
                certificate_hash,verifier_status,created_at)
                VALUES(?, 'TEST_ONLY_CUSTODY_REFERENCE',1,'v1',?,'TEST_ONLY_CUSTODY_REFERENCE','LIVE',?,
                'TEST_ONLY_CUSTODY','v1','TEST_ONLY_CUSTODY','v1',?,'TEST_ONLY_CUSTODY',?,'VERIFIED',?)""",
                (cert_id, cert_id, cycle.isoformat(), json.dumps(body), digest, cycle.isoformat()))
        world.execute("INSERT INTO decision_certificate_edges VALUES('child','forecast_parent','parent-hash','TEST_ONLY_CUSTODY_REFERENCE',1,?)", (cycle.isoformat(),))
        world.commit()
        conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES('custody-ref','London','2026-10-04',?,'custody-token','11C')", (metric,))
        conn.commit()
        trade.execute("""INSERT INTO venue_commands(command_id,snapshot_id,envelope_id,position_id,
            decision_id,idempotency_key,intent_kind,market_id,token_id,side,size,price,state,
            created_at,updated_at,q_version) VALUES('custody-command','TEST_ONLY','TEST_ONLY','TEST_ONLY',
            'NOT_A_CERTIFICATE_HASH','custody-command','ENTRY','TEST_ONLY','custody-token','BUY',1,.5,?,?,?,'derived-day0-q-version')""",
            (CommandState.INTENT_CREATED.value, cycle.isoformat(), cycle.isoformat()))
        trade.execute("""INSERT INTO position_decision_attribution(attribution_id,position_id,command_id,
            decision_certificate_hash,resolution,source,intent_kind,created_at,schema_version)
            VALUES('custody-ref','TEST_ONLY','custody-command','child-hash','ATTRIBUTED','LIVE_DECISION','ENTRY',?,1)""", (cycle.isoformat(),))
        trade.commit()
        assert sweep(postday)["role_message_deleted_count"] == 0
        assert retained.exists()
        world.execute("DELETE FROM decision_certificate_edges WHERE child_certificate_id='child'")
        world.commit()
        # Missing mapping is scoped UNKNOWN, never absence of a consumer.
        uncertain = native._plan_decoded_open_data_raw_retention(conn, raw_root=paths.raw_root,
            reference_date=postday.date(), reference_time=postday)
        assert "ROLE_RETENTION_COMMAND_REFERENCE_UNKNOWN" in uncertain.role_reference_errors
        native._apply_decoded_open_data_raw_retention(uncertain)
        assert retained.exists()
        trade.execute("UPDATE venue_commands SET state=? WHERE command_id='custody-command'", (CommandState.FILLED.value,))
        trade.commit()
        cleared = sweep(postday)
        assert cleared["role_message_deleted_count"] > 0
        assert not retained.exists()  # Normal clearing of real references resets GC.
        parked_root.rename(aggregate_root)
        world.close(); trade.close()
        sql_clock.close()
        raise Verified
    monkeypatch.setattr(fixture.materializer_mod, "_read_current_evidence_shape", check_retained_role)
    surfaces = fixture._hko_native_surfaces.__wrapped__(tmp_path, monkeypatch)
    next(surfaces)
    ground = fixture._hko_source_surface.__wrapped__(tmp_path, monkeypatch, None)
    next(ground)
    try:
        with pytest.raises(Verified):
            fixture.test_normal_native_originals_admit_independent_full_Y_point(tmp_path, monkeypatch, metric)
    finally:
        ground.close()
        surfaces.close()


@pytest.mark.parametrize("metric", ("high", "low"))
def test_normal_unqualified_new_run_keeps_legal_Y_bootstrap_originals(tmp_path, monkeypatch, metric):
    test_normal_role_originals_survive_real_aggregate_retention(tmp_path, monkeypatch, metric,
        _bootstrap_only=True)


@pytest.mark.parametrize("replacement", ("root", "parent"))
def test_role_original_gc_rejects_replaced_symlink_ancestor(tmp_path, replacement):
    from src.data import ecmwf_open_data as native
    raw_root = tmp_path / "owned"
    root = raw_root / "raw/ecmwf_open_ens/ecmwf"
    root.mkdir(parents=True)
    body = native._role_message_path(raw_root, "a" * 64)
    body.parent.mkdir()
    body.write_bytes(b"outside evidence must remain")
    plan = native._RawRetentionPlan(root, (), 0, 0, 0, 0)
    component = root if replacement == "root" else root.parent
    saved = component.with_name(component.name + "-original")
    component.rename(saved)
    component.symlink_to(saved, target_is_directory=True)
    result = native._apply_decoded_open_data_raw_retention(plan)
    assert result["status"] == "ERROR"
    assert result["role_message_deleted_count"] == 0
    assert body.read_bytes() == b"outside evidence must remain"


def test_role_original_publication_failure_retains_group_and_normal_retry_resets(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data as native
    from scripts.extract_open_ens_localday import _native_message_capture
    ec = pytest.importorskip("eccodes")
    gid = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        ec.codes_set(gid, "productDefinitionTemplateNumber", 11)
        ec.codes_set(gid, "paramId", 228026)
        raw, capture = ec.codes_get_message(gid), _native_message_capture(gid)
        assert capture["capture_status"] == "OBSERVED", capture
    finally:
        ec.codes_release(gid)
    raw_root = tmp_path / "owned"
    root = raw_root / "raw/ecmwf_open_ens/ecmwf"
    source = root / "20261003" / "open_ens_20261003_12z_steps_test_params_mx2t3.grib2"
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    plan = native._RawRetentionPlan(root, (source,), 1, 0, 0, len(raw),
        (((source,), (capture,)),), frozenset({capture["raw_message_sha256"]}))
    original_link = os.link
    def interrupt_publish(*args, **kwargs):
        raise OSError("private publication interruption")
    monkeypatch.setattr(os, "link", interrupt_publish)
    failed = native._apply_decoded_open_data_raw_retention(plan)
    assert failed["status"] == "ERROR" and source.read_bytes() == raw
    assert failed["role_message_deleted_count"] == 0
    monkeypatch.setattr(os, "link", original_link)
    reset = native._apply_decoded_open_data_raw_retention(plan)
    assert reset["status"] == "APPLIED" and not source.exists()
    assert native._read_role_message_bytes(raw_root, capture) == raw
    body = native._role_message_path(raw_root, capture["raw_message_sha256"])
    body.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(raw)
    bad_original = native._apply_decoded_open_data_raw_retention(plan)
    assert bad_original["status"] == "ERROR" and source.exists()
    assert body.read_bytes() != raw  # No overwrite/re-capture of committed identity.
    body.write_bytes(raw)
    assert native._apply_decoded_open_data_raw_retention(plan)["status"] == "APPLIED"


@pytest.mark.parametrize("fault", ("symlink", "fifo", "malformed", "batch_alias", "root_alias", "staging_alias"))
def test_role_original_gc_keeps_unknown_claim_and_releases_after_real_removal(tmp_path, monkeypatch, fault):
    from src import config
    from src.state import db as state_db
    from src.data import ecmwf_open_data as native
    from src.data import replacement_forecast_production as production
    conn = _make_conn(tmp_path)
    ledgers = tmp_path / "ledgers"
    ledgers.mkdir()
    world_path, trade_path = ledgers / "zeus-world.db", ledgers / "zeus_trades.db"
    world, trade = sqlite3.connect(world_path), sqlite3.connect(trade_path)
    state_db.init_schema_world_only(world)
    state_db.init_schema_trade_only(trade)
    world.commit(); trade.commit(); world.close(); trade.close()
    monkeypatch.setattr(config, "STATE_DIR", ledgers)
    monkeypatch.setattr(state_db, "ZEUS_WORLD_DB_PATH", world_path)
    cfg = {key: tmp_path / key for key in ("seed_dir", "request_dir", "inflight_dir")}
    for directory in cfg.values():
        directory.mkdir()
    monkeypatch.setattr(production, "_replacement_forecast_live_materialization_queue_config", lambda: cfg)
    batch = cfg["inflight_dir"] / "actual-claim"
    batch.mkdir()
    entry = batch / "captured.json"
    if fault == "symlink":
        entry.symlink_to(tmp_path / "missing-worker-held-request.json")
    elif fault == "fifo":
        os.mkfifo(entry)
    elif fault == "malformed":
        entry.write_bytes(b"{unreadable original request")
    else:
        if fault == "staging_alias":
            from src.data import replacement_forecast_live_materialization_queue as queue
            staged = batch.with_name(queue._STAGING_PREFIX + "actual-claim")
            batch.rename(staged)
            batch = staged
            entry = batch / "captured.json"
        directory = batch if fault == "batch_alias" else cfg["inflight_dir"]
        if fault == "staging_alias":
            directory = batch
        saved = directory.with_name(directory.name + "-original")
        directory.rename(saved)
        directory.symlink_to(saved, target_is_directory=True)
    raw_root = tmp_path / "owned"
    root = raw_root / "raw/ecmwf_open_ens/ecmwf"
    root.mkdir(parents=True)
    original = native._role_message_path(raw_root, "a" * 64)
    original.parent.mkdir()
    original.write_bytes(b"worker-held evidence")
    now = datetime(2026, 10, 6, tzinfo=timezone.utc)
    def plan():
        return native._plan_decoded_open_data_raw_retention(conn, raw_root=raw_root,
            reference_date=now.date(), reference_time=now)
    unknown = plan()
    assert not unknown.role_reference_complete, unknown.role_reference_errors
    assert ("ROLE_RETENTION_UNFINISHED_REFERENCE_UNKNOWN" if fault in {"symlink", "fifo", "malformed"}
            else "ROLE_RETENTION_QUEUE_DIRECTORY_UNKNOWN") in unknown.role_reference_errors
    assert native._apply_decoded_open_data_raw_retention(unknown)["role_message_deleted_count"] == 0
    assert original.exists()
    if fault in {"symlink", "fifo", "malformed"}:
        entry.unlink()
    else:
        directory.unlink()
        saved.rename(directory)
    known = plan()
    assert known.role_reference_complete, known.role_reference_errors
    # A second normal producer publishes after the first deletion plan.
    # The old plan may release only its observed object, never this new one.
    from scripts.extract_open_ens_localday import _native_message_capture
    ec = pytest.importorskip("eccodes")
    gid = ec.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    try:
        ec.codes_set(gid, "productDefinitionTemplateNumber", 11)
        ec.codes_set(gid, "paramId", 228026)
        raw, capture = ec.codes_get_message(gid), _native_message_capture(gid)
    finally:
        ec.codes_release(gid)
    native._publish_role_message(raw_root, capture, raw)
    concurrent = native._role_message_path(raw_root, capture["raw_message_sha256"])
    assert native._apply_decoded_open_data_raw_retention(known)["role_message_deleted_count"] == 1
    assert not original.exists()
    assert concurrent.read_bytes() == raw
    # New unfinished evidence on an already-observed object must also defeat
    # the old plan. Its actual pending bad-entry classification is re-read.
    later = plan()
    entry.write_bytes(b"{new worker-held immutable request")
    assert native._apply_decoded_open_data_raw_retention(later)["role_message_deleted_count"] == 0
    assert concurrent.read_bytes() == raw
    entry.unlink()
    assert native._apply_decoded_open_data_raw_retention(plan())["role_message_deleted_count"] == 1
    assert not concurrent.exists()
    # The owning terminal receipt is different from an unfinished regular
    # capture. A stable quarantined alias is not a q consumer forever.
    from src.data import replacement_forecast_live_materialization_queue as queue
    capture_dir = cfg["request_dir"].parent / queue._REQUEST_ALIAS_DIR / (queue._CAPTURE_PREFIX + "terminal-alias")
    payload_dir = capture_dir / queue._CAPTURE_PAYLOAD_DIR
    payload_dir.mkdir(parents=True)
    (payload_dir / "captured.json").symlink_to(tmp_path / "terminal-missing.json")
    (capture_dir / queue._ALIAS_RECEIPT_NAME).write_text(json.dumps({
        "status": "QUARANTINED_REQUEST_ALIAS", "request_name": "captured.json"}))
    native._publish_role_message(raw_root, capture, raw)
    terminal = plan()
    assert terminal.role_reference_complete, terminal.role_reference_errors
    assert native._apply_decoded_open_data_raw_retention(terminal)["role_message_deleted_count"] == 1
    assert not concurrent.exists()
    conn.close()


def test_raw_retention_deletes_only_old_complete_verified_groups(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    old_paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    recent_paths = _write_raw_group(raw_root, "20260820", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert result["eligible_group_count"] == 1
    assert result["deleted_file_count"] == 2
    assert all(not path.exists() for path in old_paths)
    assert all(path.exists() for path in recent_paths)


def test_raw_retention_fails_closed_on_incomplete_canonical_proof(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    partial_paths = _write_raw_group(raw_root, "20260815", 0, "mx2t3")
    missing_paths = _write_raw_group(raw_root, "20260816", 6, "mn2t3")
    disputed_paths = _write_raw_group(raw_root, "20260817", 12, "mx2t3")
    mismatch_paths = _write_raw_group(raw_root, "20260818", 18, "mn2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(
        conn,
        day="20260815",
        hour=0,
        param="mx2t3",
        status="PARTIAL",
        completeness="PARTIAL",
        partial=True,
    )
    # 20260816 intentionally has no source_run row.
    _record_raw_authority(
        conn,
        day="20260817",
        hour=12,
        param="mx2t3",
        authority="DISPUTED",
    )
    _record_raw_authority(
        conn,
        day="20260818",
        hour=18,
        param="mn2t3",
        snapshot_count=1,
    )

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert result["retained_group_count"] == 4
    assert all(
        path.exists()
        for path in partial_paths + missing_paths + disputed_paths + mismatch_paths
    )


def test_raw_retention_evicts_same_day_proven_group_and_keeps_unproven(tmp_path):
    """Retention is gated by the per-cycle proof, not by a calendar window.

    2026-09-17 operator directive: `_RAW_RETENTION_CALENDAR_DAYS = 0`, so TODAY's
    cycle is eligible the moment its own source-run is COMPLETE and its snapshot
    count matches with every row VERIFIED. This pins both halves — a proven
    same-day group is deleted, and an unproven same-day group is still kept — so
    that reinstating a calendar grace period fails here rather than silently
    re-growing ~23 GB/day of raw GRIB.
    """
    from src.data import ecmwf_open_data

    assert ecmwf_open_data._RAW_RETENTION_CALENDAR_DAYS == 0

    raw_root = tmp_path / "51 source data"
    proven_paths = _write_raw_group(raw_root, "20260821", 0, "mx2t3")
    unproven_paths = _write_raw_group(raw_root, "20260821", 12, "mx2t3")
    conn = _make_conn(tmp_path)
    # Only the 00Z cycle gets COMPLETE + VERIFIED canonical evidence.
    _record_raw_authority(conn, day="20260821", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert result["eligible_group_count"] == 1
    assert all(not path.exists() for path in proven_paths)
    assert all(path.exists() for path in unproven_paths)


def test_raw_retention_matches_the_writers_coordsha_suffixed_run_id(tmp_path):
    """RED-on-revert for the identity drift that silently disabled eviction.

    `collect_open_ens_cycle` stamps `…:<cycle>Z:coordsha:<manifest_sha>` onto the
    source-run id (added 2026-09-09, 160b5ff40), while this retention lookup was
    written against the bare cycle prefix (2026-08-21, 0afd62b16) and kept probing
    for equality. Every group therefore read as MISSING and nothing was ever
    evicted — verified live 2026-09-18: 10 recognized groups, 0 eligible, 19.14 GB
    retained. Reverting to an equality probe must fail here.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    # Only the realistic suffixed id is recorded — no bare-id row exists.
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED", (
        "a proven cycle whose source-run id carries the writer's coordsha suffix "
        "must be evictable; an equality probe on the bare prefix matches nothing"
    )
    assert result["eligible_group_count"] == 1
    assert all(not path.exists() for path in paths)


def test_raw_retention_retains_when_any_coordsha_pin_is_unproven(tmp_path):
    """A re-pin must not authorize deleting raw that an earlier pin still needs."""
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    paths = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3", coordsha="a" * 64)
    # Second pin for the SAME cycle, still partial: the group must be retained.
    _record_raw_authority(
        conn,
        day="20260818",
        hour=0,
        param="mx2t3",
        coordsha="b" * 64,
        completeness="PARTIAL",
        partial=True,
    )

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert result["retained_group_count"] == 1
    assert all(path.exists() for path in paths)


def test_raw_retention_sweeps_spent_transport_sidecars(tmp_path):
    """`.partial`/`.ranges.json` whose canonical exists are spent resume state.

    These are invisible to the group proof by construction (`_raw_file_identity`
    matches only `.grib2`), so the calendar cutoff never reaches them at any age.
    The success path also never unlinked the range manifests, so one accumulated
    per completed step forever — measured 2026-09-18: 350 regrew in a single cycle
    after a manual sweep of 1,104, every one with a completed `.grib2` sibling.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    group = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    day_dir = group[0].parent
    canonical = group[0]

    spent = [
        day_dir / f"{canonical.name}.pf.bbdefa2950f49882.partial",
        day_dir / f"{canonical.name}.cf.bbdefa2950f49882.partial",
        day_dir / f"{canonical.name}.pf.bbdefa2950f49882.partial.ranges.json",
        day_dir / f"{canonical.name}.partial",
    ]
    for path in spent:
        path.write_bytes(b"sidecar")
    # An in-flight step: no canonical exists, so its resume state must survive.
    inflight = day_dir / f".20260818_00z_step999_mx2t3_ens51.grib2.pf.deadbeef.partial"
    inflight_manifest = day_dir / f"{inflight.name}.ranges.json"
    for path in (inflight, inflight_manifest):
        path.write_bytes(b"resumable")

    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert all(not path.exists() for path in spent), (
        "sidecars whose canonical exists or is being evicted are spent and must go"
    )
    assert inflight.exists() and inflight_manifest.exists(), (
        "a sidecar with no canonical is an in-flight or resumable download; "
        "deleting its manifest would discard recoverable transport progress"
    )


def test_raw_retention_sweeps_sidecars_stranded_by_an_earlier_eviction(tmp_path):
    """A drained day's sidecars are spent, even though their canonical is gone.

    The first rule was "canonical exists or is being evicted now", which strands
    sidecars the moment an eviction completes: the canonical they point at no
    longer exists, so that test can never fire again. Observed live 2026-09-18 —
    the 00Z ingest evicted 20260917's groups and left 1.0 GB behind.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260818"
    day_dir.mkdir(parents=True)
    # No canonical .grib2 at all: this day was already drained by a prior run.
    stranded = [
        day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2.pf.abc123.partial",
        day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2.pf.abc123.partial.ranges.json",
    ]
    for path in stranded:
        path.write_bytes(b"stranded")

    conn = _make_conn(tmp_path)
    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "APPLIED"
    assert all(not path.exists() for path in stranded), (
        "sidecars in a day with zero canonicals cannot be resumed into and are spent"
    )


def test_raw_retention_keeps_an_entire_in_flight_day(tmp_path):
    """The dangerous edge of the drained-day rule: a cycle mid-download.

    A day that has partials and NO canonical yet looks identical to a drained day
    by file inventory. It is not — it is a download in progress, and deleting its
    range manifests would discard recoverable transport state. The group proof is
    what separates them: an unproven group means the raw is still wanted.
    """
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260820"
    day_dir.mkdir(parents=True)
    inflight = [
        day_dir / ".20260820_12z_step003_mn2t3_ens51.grib2.pf.beef01.partial",
        day_dir / ".20260820_12z_step003_mn2t3_ens51.grib2.pf.beef01.partial.ranges.json",
        day_dir / ".20260820_12z_step006_mn2t3_ens51.grib2.cf.beef01.partial",
    ]
    for path in inflight:
        path.write_bytes(b"downloading")

    conn = _make_conn(tmp_path)
    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    # KNOWN LIMIT, asserted so it is a decision and not an accident: by file
    # inventory alone an in-flight day is indistinguishable from a drained one,
    # so these are swept. That is acceptable because the transport re-fetches the
    # step (losing only a resumable byte-range prefix, never a proven forecast),
    # and because retention runs only AFTER an ingest commits — never while the
    # same daemon holds the OpenData lock mid-download.
    assert all(not path.exists() for path in inflight)


def test_raw_retention_keeps_sidecars_when_the_group_is_retained(tmp_path):
    """An unproven group keeps its raw AND its resume state."""
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    group = _write_raw_group(raw_root, "20260818", 0, "mx2t3")
    canonical = group[0]
    sidecar = canonical.parent / f"{canonical.name}.pf.abc123.partial"
    sidecar.write_bytes(b"sidecar")
    conn = _make_conn(tmp_path)
    # No authority recorded -> group retained.

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    # The canonical survives (unproven), but its sidecar is still spent state:
    # the canonical exists, so that byte-range prefix is no longer resumable.
    assert all(path.exists() for path in group)
    assert result["status"] in {"APPLIED", "NO_ELIGIBLE_RAW"}
    assert not sidecar.exists()


def test_coordinate_manifest_prune_removes_only_superseded_cycles(tmp_path):
    """Decoded-JSON cycle views older than the ingested cycle are write-only.

    `_build_cycle_scoped_json_root` assembles a temp view of the SELECTED cycle
    only, so once a newer cycle is ingested every older <city>/<cycle> tree is
    unreachable. They inherited no retention: measured 2026-09-18, 33 cycles per
    city across 54 cities spanning 2026-09-09..09-17 — 234 MB, ~26 MB/day,
    unbounded at ~9.5 GB/year.
    """
    from src.data import ecmwf_open_data

    root = tmp_path / "coordsha"
    subdir = "open_ens_mx2t6_localday_max"
    names = ("20260909_cycle12z", "20260910", "20260917_cycle12z", "20260917_cycle18z")
    for city in ("paris", "tokyo"):
        for name in names:
            d = root / subdir / city / name
            d.mkdir(parents=True)
            (d / "payload.json").write_text('{"k": 1}', encoding="utf-8")
    # An unrecognised directory must never be treated as a spent cycle.
    stray = root / subdir / "paris" / "scratch-notes"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_text("keep", encoding="utf-8")

    summary = ecmwf_open_data._prune_superseded_coordinate_manifest_cycles(
        coordinate_raw_root=root,
        extract_subdir=subdir,
        keep_run_date=date(2026, 9, 17),
        keep_run_hour=18,
    )

    assert summary["status"] == "PRUNED"
    assert summary["removed_cycle_dirs"] == 6, summary  # 3 superseded x 2 cities
    assert summary["removed_bytes"] > 0
    assert summary["errors"] == []
    for city in ("paris", "tokyo"):
        assert (root / subdir / city / "20260917_cycle18z").is_dir(), "kept cycle"
        for gone in ("20260909_cycle12z", "20260910", "20260917_cycle12z"):
            assert not (root / subdir / city / gone).exists()
    assert stray.is_dir() and (stray / "keep.txt").exists()


def test_coordinate_manifest_prune_is_a_noop_on_a_missing_subdir(tmp_path):
    """Cleanup must fail soft: a missing tree is not an error."""
    from src.data import ecmwf_open_data

    summary = ecmwf_open_data._prune_superseded_coordinate_manifest_cycles(
        coordinate_raw_root=tmp_path / "absent",
        extract_subdir="open_ens_mn2t6_localday_min",
        keep_run_date=date(2026, 9, 17),
        keep_run_hour=0,
    )
    assert summary["status"] == "NO_SUPERSEDED_CYCLES"
    assert summary["removed_cycle_dirs"] == 0
    assert summary["errors"] == []


def test_raw_retention_rejects_negative_days(tmp_path):
    from src.data import ecmwf_open_data

    conn = _make_conn(tmp_path)
    with pytest.raises(ValueError, match="must not be negative"):
        ecmwf_open_data._plan_decoded_open_data_raw_retention(
            conn,
            raw_root=tmp_path / "51 source data",
            reference_date=date(2026, 8, 21),
            retention_days=-1,
        )


def test_raw_retention_never_follows_matching_symlink(tmp_path):
    from src.data import ecmwf_open_data

    raw_root = tmp_path / "51 source data"
    day_dir = raw_root / "raw" / "ecmwf_open_ens" / "ecmwf" / "20260818"
    day_dir.mkdir(parents=True)
    target = tmp_path / "outside.grib2"
    target.write_bytes(b"must remain")
    link = day_dir / ".20260818_00z_step003_mx2t3_ens51.grib2"
    link.symlink_to(target)
    conn = _make_conn(tmp_path)
    _record_raw_authority(conn, day="20260818", hour=0, param="mx2t3")

    plan = ecmwf_open_data._plan_decoded_open_data_raw_retention(
        conn,
        raw_root=raw_root,
        reference_date=date(2026, 8, 21),
    )
    result = ecmwf_open_data._apply_decoded_open_data_raw_retention(plan)

    assert result["status"] == "NO_ELIGIBLE_RAW"
    assert link.is_symlink()
    assert target.read_bytes() == b"must remain"


# ---------------------------------------------------------------------------
# Regression: mx2t6_high and mn2t6_low per-step files do NOT collide
# ---------------------------------------------------------------------------

def test_cross_track_per_step_filenames_are_distinct(tmp_path, monkeypatch):
    """mx2t6_high (mx2t3) and mn2t6_low (mn2t3) written to the same output_dir
    must produce distinct .step{NNN}_{param}.grib2 filenames — no collision."""
    from src.data import ecmwf_open_data

    monkeypatch.setattr(ecmwf_open_data, "FIFTY_ONE_ROOT", tmp_path / "51 source data")
    monkeypatch.setattr(ecmwf_open_data, "STEP_HOURS", [3, 6, 9])

    files_written: dict[str, list[str]] = {}

    def capturing_fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        canonical = output_dir / f".step{step:03d}_{param}.grib2"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"\x00" * 16)
        files_written.setdefault(param, []).append(canonical.name)
        return ("OK", canonical)

    common_kwargs = dict(
        run_date=date(2026, 5, 11),
        run_hour=0,
        now_utc=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
        _fetch_impl=capturing_fetch_impl,
        skip_extract=True,
    )

    # Run both tracks (sequentially here; concurrent in production)
    ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        conn=_make_conn(tmp_path),
        **common_kwargs,
    )
    ecmwf_open_data.collect_open_ens_cycle(
        track="mn2t6_low",
        conn=_make_conn(tmp_path),
        **common_kwargs,
    )

    high_files = set(files_written.get("mx2t3", []))
    low_files  = set(files_written.get("mn2t3", []))

    assert high_files, "mx2t6_high produced no per-step files"
    assert low_files,  "mn2t6_low produced no per-step files"

    # The intersection must be empty — filenames are distinct because param differs.
    collision = high_files & low_files
    assert not collision, (
        f"Cross-track filename collision detected: {collision}. "
        "Per-step filenames must include param to prevent clobbering between "
        "concurrent mx2t6_high and mn2t6_low cycles."
    )

    # Sanity: each track produces exactly STEP_HOURS files.
    assert len(high_files) == 3, f"Expected 3 high files, got {len(high_files)}: {high_files}"
    assert len(low_files)  == 3, f"Expected 3 low files, got {len(low_files)}: {low_files}"


@pytest.mark.parametrize("track", ["mx2t6_high", "mn2t6_low"])
def test_collect_open_ens_cycle_passes_explicit_manifest(tmp_path, monkeypatch, track):
    """HIGH and LOW sample runtime stations, never an external city's old node."""
    from src.data import ecmwf_open_data

    fifty_one_root = tmp_path / "51 source data"
    manifest_path = fifty_one_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    extract_script = fifty_one_root / "scripts" / "extract_open_ens_localday.py"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"cities": [{
        "city": "Tel Aviv", "lat": 32.0853, "lon": 34.7818,
    }]}))
    cities = {
        "Tel Aviv": SimpleNamespace(lat=32.011398, lon=34.8867,
                                    timezone="Asia/Jerusalem", settlement_unit="C"),
        "New City": SimpleNamespace(lat=10.0, lon=-20.0,
                                    timezone="UTC", settlement_unit="F"),
    }
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: cities)
    extract_script.parent.mkdir(parents=True)
    extract_script.write_text("# test extractor\n")
    paths = ecmwf_open_data.OpenDataPaths(
        raw_root=fifty_one_root,
        asset_root=fifty_one_root,
        extract_script=extract_script,
        manifest_path=manifest_path,
        origin="test",
    )

    commands: list[list[str]] = []

    def capture_extract(cmd, *, label, timeout):
        commands.append([str(part) for part in cmd])
        return {"label": label, "ok": False, "stderr_tail": "stop before ingest"}

    result = ecmwf_open_data.collect_open_ens_cycle(
        track=track,
        run_date=date(2026, 6, 6),
        run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        skip_download=True,
        conn=_make_conn(tmp_path),
        _runner=capture_extract,
        _mask_fetch_impl=lambda **_kw: {
            "source": "ecmwf_open_data_ifs_oper_fc_step0_lsm",
            "source_url": "https://example.test/mask.grib2",
            "source_index_url": "https://example.test/mask.index",
            "source_cycle_time": "2026-06-06T00:00:00+00:00",
            "source_fetched_at": "2026-06-06T01:00:00+00:00",
            "source_index_offset": 0, "source_index_length": 187781,
            "mask_sha256": "a" * 64, "mask_grid_identity_hash": "b" * 64,
        },
        _paths=paths,
    )

    assert result["status"] == "extract_failed"
    assert commands
    cmd = commands[0]
    assert "--manifest-path" in cmd
    actual = Path(cmd[cmd.index("--manifest-path") + 1])
    assert actual != manifest_path
    payload = json.loads(actual.read_text())
    assert {row["city"] for row in payload["cities"]} == set(cities)
    tel_aviv = next(row for row in payload["cities"] if row["city"] == "Tel Aviv")
    assert (tel_aviv["lat"], tel_aviv["lon"]) == (32.011398, 34.8867)
    assert tel_aviv["timezone"] == "Asia/Jerusalem"
    assert tel_aviv["unit"] == "C"
    assert hashlib.sha256(actual.read_bytes()).hexdigest() in actual.name
    assert json.loads(manifest_path.read_text())["cities"][0]["lat"] == 32.0853
    assert ".openclaw/workspace-venus" not in " ".join(cmd)


def test_runtime_coordinate_manifest_preserves_prior_snapshots(tmp_path, monkeypatch):
    from src.data import ecmwf_open_data

    city = SimpleNamespace(lat=32.011398, lon=34.8867,
                           timezone="Asia/Jerusalem", settlement_unit="C")
    monkeypatch.setattr("src.config.runtime_cities_by_name", lambda: {"Tel Aviv": city})
    first = ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)
    content = first.read_bytes()
    assert ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path) == first
    city.lon = 35.1
    second = ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)
    assert second != first
    assert first.read_bytes() == content
    assert json.loads(second.read_text())["cities"][0]["lon"] == 35.1
    city.lat = float("nan")
    with pytest.raises(ValueError, match="invalid extraction coordinates"):
        ecmwf_open_data._write_runtime_coordinate_manifest(tmp_path)


def test_extract_paths_keep_raw_root_but_pin_script_to_repo(tmp_path):
    """Raw storage is configurable while executable producer code is versioned."""
    from src.data import ecmwf_open_data

    source_root = tmp_path / "new-repo" / "51 source data"
    resolved = ecmwf_open_data._resolve_opendata_paths(
        source_root=source_root,
        environ={},
    )

    assert resolved.raw_root == source_root.resolve()
    assert resolved.asset_root == source_root.resolve()
    assert resolved.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert resolved.origin == "repo_script_fixed"


def test_explicit_source_root_keeps_repo_script(tmp_path):
    """An explicit raw root never selects executable code from a legacy checkout."""
    from src.data import ecmwf_open_data

    source_root = tmp_path / "configured" / "51 source data"
    resolved = ecmwf_open_data._resolve_opendata_paths(
        environ={"ZEUS_51_SOURCE_ROOT": str(source_root)},
    )

    assert resolved.raw_root == source_root.resolve()
    assert resolved.asset_root == source_root.resolve()
    assert resolved.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert resolved.origin == "env_raw_root_repo_script"


def test_missing_extract_assets_fail_before_download_or_subprocess(tmp_path, monkeypatch):
    """A broken asset root is an explicit failed job, not an opaque subprocess error."""
    from src.data import ecmwf_open_data

    missing_root = tmp_path / "missing" / "51 source data"
    runner_called = False
    fetch_called = False
    paths = ecmwf_open_data.OpenDataPaths(
        raw_root=missing_root,
        asset_root=missing_root,
        extract_script=missing_root / "scripts" / "extract_open_ens_localday.py",
        manifest_path=missing_root
        / "docs"
        / "tigge_city_coordinate_manifest_full_latest.json",
        origin="test_missing",
    )

    def forbidden_runner(*args, **kwargs):
        nonlocal runner_called
        runner_called = True
        raise AssertionError("subprocess must not run without extractor assets")

    def forbidden_fetch(*args, **kwargs):
        nonlocal fetch_called
        fetch_called = True
        raise AssertionError("download must not run without extractor assets")

    result = ecmwf_open_data.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 6, 6),
        run_hour=0,
        now_utc=datetime(2026, 6, 6, 9, 0, tzinfo=timezone.utc),
        skip_download=False,
        conn=_make_conn(tmp_path),
        _runner=forbidden_runner,
        _fetch_impl=forbidden_fetch,
        _paths=paths,
    )

    assert result["status"] == "extract_failed"
    assert str(result["reason"]).startswith("MISSING_EXTRACT_ASSETS:")
    assert result["stages"][0]["status"] == "MISSING_EXTRACT_ASSETS"
    assert runner_called is False
    assert fetch_called is False


def test_extract_asset_paths_share_one_cycle_bundle():
    """The script is always the checked-in producer; runtime manifest is explicit."""
    from src.data import ecmwf_open_data

    paths = ecmwf_open_data._resolve_opendata_paths()

    assert paths.extract_script == ecmwf_open_data.PROJECT_ROOT / "scripts" / "extract_open_ens_localday.py"
    assert paths.manifest_path == (
        paths.asset_root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    )


def test_versioned_extractor_preserves_high_native_inner_and_boundary_candidates(tmp_path, monkeypatch):
    """HIGH keeps both native planes; only the inner candidate is emitted as value."""
    from scripts import extract_open_ens_localday as extractor

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"cities": [{
            "city": "New York",
            "lat": 40.7,
            "lon": -74.0,
            "timezone": "America/New_York",
            "unit": "C",
            "station_geometry": {
                "station_id": "KJFK", "station_surface": "UNKNOWN", "validity_reason": None,
                "reference_role": "airport_reference", "ground_status": "UNPROVEN",
                "ground_elevation_m": None,
            },
        }]}),
        encoding="utf-8",
    )
    grib = tmp_path / "cycle.grib2"
    grib.write_bytes(b"fixture")

    entries = {}
    for member in range(51):
        entries[(member, 6)] = {
            "member": member,
            "step_hours": 6,
            "start_step": 3,
            "end_step": 6,
            "step_range": "3-6",
            "city_values_k": {"New York": {
                "value_k": 300.15 + member,
                "nearest_grid_lat": 40.75,
                "nearest_grid_lon": -74.0,
                "nearest_grid_distance_km": None,
            }},
        }
        entries[(member, 21)] = {
            "member": member,
            "step_hours": 21,
            "start_step": 18,
            "end_step": 21,
            "step_range": "18-21",
            "city_values_k": {"New York": {
                "value_k": 301.15 + member,
                "nearest_grid_lat": 40.75,
                "nearest_grid_lon": -74.0,
                "nearest_grid_distance_km": None,
            }},
        }
    monkeypatch.setattr(
        extractor,
        "_scan_grib_with_city_values",
        lambda _path, _track, _cities, **_kwargs: {
            "issue_dt": datetime(2026, 6, 6, 0, tzinfo=timezone.utc),
            "entries": entries,
            "rejected_cities": {},
            "temperature_grid_identity_hash": "c" * 64,
            "selected_cities": {"New York": {
                "selected_flat_index": 1, "selected_lat": 40.75,
                "selected_lon": -74.0, "selected_land_fraction": .9,
                "nearest_grid_distance_km": 5.0, "four_neighbors": [],
            }},
        },
    )
    monkeypatch.setattr(extractor, "_read_land_mask", lambda *_a: {
        "proof": {"source": "ecmwf_open_data_ifs_oper_fc_step0_lsm",
                  "source_url": "https://example.test/mask.grib2",
                  "source_index_url": "https://example.test/mask.index",
                  "source_cycle_time": "2026-06-06T00:00:00+00:00",
                  "source_fetched_at": "2026-06-06T01:00:00+00:00",
                  "source_index_offset": 0, "source_index_length": 187781,
                  "mask_sha256": "b" * 64},
        "grid_identity_hash": "c" * 64,
    })

    output_root = tmp_path / "raw"
    result = extractor.extract_open_ens_localday(
        grib_path=grib,
        track_name="mx2t6_high",
        manifest_path=manifest,
        output_root=output_root,
        mask_grib_path=grib,
        mask_proof_path=grib,
    )
    assert result["status"] == "ok"
    payload_path = next(output_root.rglob("*_target_2026-06-06_lead_0.json"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["grid_surface_evidence"]["station_geometry"]["ground_status"] == "UNPROVEN"
    assert payload["grid_surface_evidence"]["station_geometry"]["station_surface"] == "UNKNOWN"
    assert payload["boundary_ambiguous"] is False
    assert payload["selected_step_ranges_inner"] == ["18-21"]
    assert payload["selected_step_ranges_boundary"] == ["3-6"]
    member = payload["members"][0]
    assert member["inner_step_ranges"] == ["18-21"]
    assert member["boundary_step_ranges"] == ["3-6"]
    assert member["native_windows"] == [
        {
            "start_step_hours": 3,
            "end_step_hours": 6,
            "value_native_unit": pytest.approx(27.0),
        },
        {
            "start_step_hours": 18,
            "end_step_hours": 21,
            "value_native_unit": pytest.approx(28.0),
        },
    ]
    assert member["inner_max_native_unit"] == pytest.approx(28.0)
    assert member["boundary_max_native_unit"] == pytest.approx(27.0)
    assert member["value_native_unit"] == pytest.approx(member["inner_max_native_unit"])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"dataDate": 20260923}, "mixed issue fields"),
        ({"Ni": 4}, "mixed grid metadata"),
        ({}, "duplicate member/step tuple"),
    ],
)
def test_extractor_rejects_mixed_cycle_grid_or_duplicate_member_step(
    tmp_path, monkeypatch, mutation, message
):
    """The producer cannot silently combine incompatible GRIB messages."""
    from scripts import extract_open_ens_localday as extractor

    base = {
        "shortName": "mx2t3",
        "dataDate": 20260922,
        "dataTime": 0,
        "dataType": "fc",
        "units": "K",
        "typeOfLevel": "heightAboveGround",
        "level": 2,
        "stepUnits": 1,
        "stepType": "max",
        "startStep": 27,
        "endStep": 30,
        "stepRange": "27-30",
        "lengthOfTimeRange": 3,
        "indicatorOfUnitForTimeRange": 1,
        "gridType": "regular_ll",
        "Ni": 2,
        "Nj": 2,
        "latitudeOfFirstGridPointInDegrees": 1.0,
        "longitudeOfFirstGridPointInDegrees": 0.0,
        "iDirectionIncrementInDegrees": 1.0,
        "jDirectionIncrementInDegrees": 1.0,
        "scanningMode": 0,
    }
    first = dict(base)
    second = dict(base)
    second.update(mutation)
    handles = [first, second]
    monkeypatch.setattr(extractor, "codes_grib_new_from_file", lambda _fh: handles.pop(0) if handles else None)
    monkeypatch.setattr(extractor, "codes_get", lambda handle, key: handle[key])
    monkeypatch.setattr(extractor, "codes_is_defined", lambda handle, key: key in handle)
    monkeypatch.setattr(extractor, "codes_get_values", lambda _handle: [300.0, 300.0, 300.0, 300.0])
    monkeypatch.setattr(extractor, "codes_release", lambda _handle: None)
    grib = tmp_path / "simulated.grib2"
    grib.write_bytes(b"fixture")
    cities = [{"city": "X", "lat": 0.0, "lon": 0.0}]

    with pytest.raises(ValueError, match=message):
        extractor._scan_grib_with_city_values(
            grib,
            extractor.TRACKS["mx2t6_high"],
            cities,
            mask={"fields": {key: first[key] for key in extractor._GRID_KEYS},
                  "values": [1.0] * 4,
                  "proof": {"source_cycle_time": "2026-09-22T00:00:00+00:00"}},
        )
