# Created: 2026-07-30
# Last reused/audited: 2026-10-06
# Lifecycle: created=2026-07-30; last_reviewed=2026-10-06; last_reused=2026-10-06
# Authority basis: operator-directed held SELL terminal-wake hotfix.
# Purpose: Protect held wake completion and bounded normal native source drainage without starving mandatory ENS.
# Reuse: Inspect forecast_live_daemon scheduling, scope, immutable source clocks and transport budgets using private fixtures.
"""Held SELL terminal-wake completion antibodies."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("fault", (None, "wrong_hash", "future_clock", "wrong_header", "expired", "fast503", "fullcut", "partialcut", "fair_debts"))
def test_normal_journaled_paired_original_restoration_keeps_old_clocks(normal_native_poll, tmp_path, monkeypatch, track, fault):
    """Normal wrapper restores actual original bodies without re-ingesting truth."""
    import hashlib
    import time
    import eccodes as ec
    from types import SimpleNamespace
    from scripts import extract_open_ens_localday as decoder
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib
    s = normal_native_poll
    if fault == "fair_debts":
        class ClockType(type):
            def __instancecheck__(cls, value): return isinstance(value, datetime)
        class CaptureClock(datetime, metaclass=ClockType):
            @classmethod
            def now(cls, tz=None): return s.now.astimezone(tz or timezone.utc)
        monkeypatch.setattr(s.module, "datetime", CaptureClock)
    if fault != "fair_debts":
        # This antibody targets paired debt; acquire native originals through
        # the normal producer first, so a paired-only turn is the only debt.
        initial = s.daemon._run_journaled_opendata_track_if_due(track)["native_temperature_source"]
        assert initial["status"] == "AVAILABLE", initial
    metric = "high" if track == "mx2t6_high" else "low"
    folder = tmp_path / "paired-capture"; folder.mkdir()
    original, _, _, _ = _tiny_native_grib(folder, track, issue=s.run, horizon=24)
    captures, ranges, index_rows = [], {}, {}
    with original.open("rb") as stream:
        while (gid := ec.codes_grib_new_from_file(stream)) is not None:
            try:
                ec.codes_set(gid, "generatingProcessIdentifier", 161)
                member = int(ec.codes_get(gid, "perturbationNumber"))
                if member == 0: ec.codes_set(gid, "dataType", "fc")
                raw = ec.codes_get_message(gid); capture = decoder._native_message_capture(gid)
                h = capture["observed_headers"]; step = int(h["endStep"])
                stream_name, kind = ("oper", "fc") if member == 0 else ("enfo", "ef")
                url = (f"https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/{s.run:%Y%m%d}/"
                    f"{s.run:%H}z/ifs/0p25/{stream_name}/{s.run:%Y%m%d%H}0000-{step}h-{stream_name}-{kind}.grib2")
                offset = 1000000 + member * 2000
                ranges[(url, offset)] = raw
                index_rows.setdefault(url[:-6] + ".index", []).append(dict(param=decoder.TRACKS[track].open_data_param,
                    levtype="sfc", date=s.run.strftime("%Y%m%d"), time=s.run.strftime("%H%M"),
                    step=str(step), stream=stream_name, type="fc" if member == 0 else "pf",
                    number=str(member), _offset=offset, _length=len(raw), **{"class":"od"}))
                captures.append(capture)
                s.module._publish_role_message(s.paths.raw_root, capture, raw)
            finally: ec.codes_release(gid)
    original.unlink()  # Private aggregate really goes away, not a no-delete mock.
    missing = captures[0]
    missing_path = s.module._role_message_path(s.paths.raw_root, missing["raw_message_sha256"])
    missing_path.unlink()
    if fault in {"partialcut", "fair_debts"}:
        second_missing=captures[1]
        second_path=s.module._role_message_path(s.paths.raw_root,second_missing["raw_message_sha256"])
        second_path.unlink()
    if fault == "wrong_header": missing["observed_headers"]["level"] = 3
    if fault == "wrong_hash": missing["raw_message_sha256"] = "0" * 64
    written = s.now + timedelta(hours=1) if fault == "future_clock" else s.now
    run_id = s.daemon._expected_source_run_id(s.identities[track])
    s.conn.execute("""INSERT INTO ensemble_snapshots(city,target_date,temperature_metric,source_run_id,
        source_cycle_time,source_available_at,recorded_at,provenance_json,physical_quantity,
        observation_field,available_at,fetch_time,lead_hours,members_json,model_version,dataset_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("London",s.run.date().isoformat(),metric,run_id,s.run.isoformat(),
        s.now.isoformat(),written.replace(tzinfo=None).isoformat(" "),json.dumps({"native_capture_receipt":
            {"capture_status":"OBSERVED","messages":captures}}),decoder.TRACKS[track].physical_quantity,
        "high_temp" if metric == "high" else "low_temp",s.now.isoformat(),s.now.isoformat(),1.0,
        json.dumps([11.0]*51),"ecmwf_open_data",s.identities[track]["data_version"]))
    s.conn.commit()
    before = tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (run_id,)).fetchone())
    snapshot_before = tuple(s.conn.execute("SELECT * FROM ensemble_snapshots WHERE source_run_id=?", (run_id,)).fetchone())
    old_get = s.session.get
    mirrors=[];clock=[100.]
    if fault in {"fast503","fullcut","partialcut","fair_debts"}:
        monkeypatch.setattr(s.module,"_DOWNLOAD_SOURCES",("aws","google"))
        monkeypatch.setattr(s.module.time,"monotonic",lambda:clock[0])
    native_ranges = [0]
    def get(url, **kwargs):
        google="storage.googleapis.com" in url
        if fault in {"fast503","fullcut","partialcut"}:
            mirrors.append("google" if google else "aws")
            if not google and fault != "partialcut":
                prototype=old_get(url)
                if fault == "fullcut": clock[0]=s.session._zeus_deadline
                return type(prototype)(b"",503,{"Content-Length":"0"})
        url=url.replace("https://storage.googleapis.com/ecmwf-open-data",
            "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com")
        response = old_get(url, **kwargs) if url.endswith(".index") or "Range" not in kwargs.get("headers",{}) else None
        if url.endswith(".index"):
            body=b"\n".join([*response.content.splitlines(),
                *(json.dumps(v).encode() for v in index_rows.get(url,[]))])+b"\n"
            return type(response)(body,200,{"Content-Length":str(len(body))})
        offset,end=map(int,kwargs["headers"]["Range"][6:].split("-"))
        if (url,offset) not in ranges:
            if fault == "fair_debts":
                native_ranges[0] += 1
                if native_ranges[0] == 2:
                    clock[0] = s.session._zeus_deadline
            return old_get(url,**kwargs)
        s.calls.append((url,kwargs))
        prototype=old_get(url[:-6]+".index")
        if fault == "partialcut" and clock[0] == 100.: clock[0]=s.session._zeus_deadline
        return type(prototype)(ranges[(url,offset)],206,
            {"Content-Range":f"bytes {offset}-{end}/2000000","Content-Length":str(end-offset+1)})
    monkeypatch.setattr(s.session,"get",get)
    import ecmwf.opendata
    client=ecmwf.opendata.Client
    class PairedClient(client):
        def __init__(self,**kwargs):
            self.mirror=kwargs["source"];super().__init__(**kwargs)
        def _get_urls(self,**kwargs):
            result=super()._get_urls(**kwargs);result.for_index={"param":kwargs["param"]}
            if self.mirror == "google":
                result.urls=[url.replace("https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
                    "https://storage.googleapis.com/ecmwf-open-data") for url in result.urls]
            return result
    monkeypatch.setattr(ecmwf.opendata,"Client",PairedClient)
    # The normal dispatcher recomputes its own strong transport plan. Keep the
    # sibling market out of this exact restoration case, not its mandatory job.
    s.conn.execute("DELETE FROM market_events WHERE temperature_metric!=?",(metric,));s.conn.commit()
    if fault == "fair_debts":
        from concurrent.futures import Future
        ordinary_calls = []
        ordinary = s.daemon._run_opendata_track_if_due
        def ordinary_checked(*args, **kwargs):
            ordinary_calls.append(args[0])
            return ordinary(*args, **kwargs)
        monkeypatch.setattr(s.daemon, "_run_opendata_track_if_due", ordinary_checked)
        class ImmediateExecutor:
            def submit(self, fn, selected_track):
                future = Future()
                try: future.set_result(fn(selected_track))
                except Exception as exc: future.set_exception(exc)
                return future
        def poll():
            sibling = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
            inflight = {sibling: Future()}  # Normal sibling already has work.
            report = s.daemon._dispatch_due_opendata_tracks(_executor=ImmediateExecutor(), _inflight=inflight)
            assert report[sibling]["status"] == "in_flight"
            return inflight[track].result()["native_temperature_source"]
        mandatory_before = tuple(s.conn.execute("SELECT * FROM job_run WHERE source_run_id=?", (run_id,)).fetchone())
        first = poll()
        assert not missing_path.exists() and not second_path.exists(), "first native turn was spent restoring paired debt"
        assert first["transport_kind"] == "native", first
        assert first["status"] == "INCOMPLETE" and first["observed_count"] == 1
        assert clock[0] == 159. and s.session._zeus_deadline == 159.
        assert not any(int(k["headers"]["Range"][6:].split("-")[0]) >= 1000000
            for _, k in s.calls if "Range" in k.get("headers", {}))
        retained = next(Path(first["manifest_path"]).parent.glob("step*-member*.grib2"))
        retained_before = (retained.read_bytes(), retained.with_suffix(".grib2.proof.json").read_bytes(), retained.stat().st_mtime_ns)
        assert json.loads(retained_before[1])["source_fetched_at"] == s.now.isoformat()
        receipt = s.conn.execute("SELECT meta_json FROM job_run WHERE job_name=?", ("forecast_live_native_2t_"+track,)).fetchone()
        assert json.loads(receipt[0])["transport_kind"] == "native"
        # Simulate a process loss after its durable native RUNNING receipt.
        # This diagnostic fault cannot upgrade the real PARTIAL source/body.
        s.conn.execute("UPDATE job_run SET status='RUNNING',finished_at=NULL WHERE job_name=?",
            ("forecast_live_native_2t_"+track,))
        s.conn.commit()
        # Reopened normal connections and cleared dispatcher state must resume
        # the persisted phase, not begin again with an in-memory native latch.
        s.daemon._OPENDATA_SAFE_CYCLE_FUTURES.clear()
        clock[0] = 200.; call_start = len(s.calls)
        second = poll()
        assert second["transport_kind"] == "paired", second
        assert second["paired_originals"]["status"] == "AVAILABLE"
        assert second["paired_originals"]["restored_count"] == 2
        assert s.session._zeus_deadline == 259.
        assert missing_path.exists() and second_path.exists()
        assert all(int(k["headers"]["Range"][6:].split("-")[0]) >= 1000000
            for _, k in s.calls[call_start:] if "Range" in k.get("headers", {}))
        clock[0] = 300.
        third = poll()
        assert third["transport_kind"] == "native" and third["status"] == "AVAILABLE", third
        assert third["qualification_status"] == "UNKNOWN" and third["available_at"] is None
        assert s.session._zeus_deadline == 359. and ordinary_calls == []
        assert (retained.read_bytes(), retained.with_suffix(".grib2.proof.json").read_bytes(), retained.stat().st_mtime_ns) == retained_before
        call_count = len(s.calls); clock[0] = 400.
        fourth = poll()
        assert fourth["status"] == "AVAILABLE" and len(s.calls) == call_count
        assert ordinary_calls == [track]  # Complete debt returns to ordinary mandatory admission.
        assert tuple(s.conn.execute("SELECT * FROM job_run WHERE source_run_id=?", (run_id,)).fetchone()) == mandatory_before
        assert tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (run_id,)).fetchone()) == before
        assert tuple(s.conn.execute("SELECT * FROM ensemble_snapshots WHERE source_run_id=?", (run_id,)).fetchone()) == snapshot_before
        return
    if fault == "expired":
        plan=s.daemon._native_temperature_transport_plans(s.conn,now_utc=s.now)[0]
        result=s.module.restore_paired_role_originals(s.conn,plan=plan,decision_at=s.now,
            deadline_monotonic=time.monotonic(),_paths=s.paths)
    else:
        normal=s.daemon._run_journaled_opendata_track_if_due(track)["native_temperature_source"]
        if fault is None: assert missing_path.exists(), "normal SUCCESS poll did not restore paired original body"
        result=normal["paired_originals"]
    if fault == "fullcut":
        assert result["status"] == "UNKNOWN" and not missing_path.exists()
        receipt=s.paths.raw_root/"raw/ecmwf_open_ens/native_2t_scheduled"/f"{s.run:%Y%m%dT%HZ}"/".paired-originals/mirror-attempt.json"
        assert json.loads(receipt.read_bytes())["last_attempted_mirror"] == "aws"
        assert mirrors == ["aws"]
        clock[0]=200.;mirrors.clear()
        result=s.daemon._run_journaled_opendata_track_if_due(track)["native_temperature_source"]["paired_originals"]
        assert mirrors[0] == "google"
    if fault == "partialcut":
        assert result["status"] == "UNKNOWN" and missing_path.exists() and not second_path.exists()
        first_bytes=missing_path.read_bytes();first_stat=missing_path.stat()
        clock[0]=200.;mirrors.clear();call_start=len(s.calls)
        result=s.daemon._run_journaled_opendata_track_if_due(track)["native_temperature_source"]["paired_originals"]
        assert result["restored_count"] == 1 and second_path.exists() and mirrors[0] == "google"
        assert missing_path.read_bytes()==first_bytes and missing_path.stat().st_mtime_ns==first_stat.st_mtime_ns
        first_range=next((url,offset) for (url,offset),raw in ranges.items() if hashlib.sha256(raw).hexdigest()==missing["raw_message_sha256"])
        assert not any(url==first_range[0] and kwargs.get("headers",{}).get("Range","").startswith(f"bytes={first_range[1]}-")
            for url,kwargs in s.calls[call_start:])
    if fault in {None,"fast503","fullcut","partialcut"}:
        assert result["status"] == "AVAILABLE",result
        if fault == "fast503": assert mirrors[0] == "aws" and "google" in mirrors
        assert s.module._read_role_message_bytes(s.paths.raw_root,missing) == ranges[next(iter(ranges))]
        call_count=len(s.calls)
        second=s.daemon._run_journaled_opendata_track_if_due(track)["native_temperature_source"]["paired_originals"]
        assert second == {"status":"AVAILABLE","restored_count":0}
        assert not any(int(call[1]["headers"]["Range"][6:].split("-")[0]) >= 1000000
            for call in s.calls[call_count:] if "Range" in call[1].get("headers",{}))
        if fault is None: assert len(s.calls)==call_count
    else:
        assert result["status"] == "UNKNOWN",result
        assert not s.module._role_message_path(s.paths.raw_root,missing["raw_message_sha256"]).exists()
    assert tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?",(run_id,)).fetchone()) == before
    assert tuple(s.conn.execute("SELECT * FROM ensemble_snapshots WHERE source_run_id=?",(run_id,)).fetchone()) == snapshot_before

@pytest.fixture
def normal_native_poll(tmp_path, monkeypatch, request):
    """Real normal wrapper, journals, GRIB and source inventory; fake HTTP only."""
    from types import SimpleNamespace
    from src.ingest import forecast_live_daemon as daemon
    from src.data.release_calendar import FetchDecision
    from src.data import ecmwf_open_data as source
    from src.config import runtime_coordinate_manifest_json
    from src.data.forecast_fetch_plan import data_version_for_track
    from src.state import db
    from src.state.source_run_repo import write_source_run
    from src.state.source_run_coverage_repo import write_source_run_coverage
    from tests.test_ecmwf_open_data_collect_cycle import _normal_native_http

    options = getattr(request, "param", 0)
    hour = options.get("hour", 0) if isinstance(options, dict) else options
    future_supply = isinstance(options, dict) and options.get("future", False)
    wanted = tuple(range(0, 25 - hour, 3))
    supplied = tuple(range(0, 49 - hour, 3)) if future_supply else wanted
    s = _normal_native_http(tmp_path, monkeypatch, steps=supplied, hour=hour)
    now = s.run + timedelta(hours=1)
    identities = {}
    manifest_json = runtime_coordinate_manifest_json()
    for track, metric in (("mx2t6_high", "high"), ("mn2t6_low", "low")):
        identity = dict(track=track, decision=FetchDecision.FETCH_ALLOWED, scheduled_for=s.run,
            job_name="private-normal-" + track, source_id="ecmwf_open_data",
            release_calendar_key="ecmwf_open_data:" + track + ":full_horizon",
            coordinate_manifest_json=manifest_json, data_version=data_version_for_track(track, manifest_json), metadata={})
        identities[track] = identity
        run_id = daemon._expected_source_run_id(identity)
        write_source_run(s.conn, source_run_id=run_id, source_id="ecmwf_open_data",
            track=track + "_full_horizon", release_calendar_key=identity["release_calendar_key"],
            source_cycle_time=s.run, status="SUCCESS", completeness_status="COMPLETE",
            expected_steps_json=list(supplied[1:]), observed_steps_json=list(supplied[1:]),
            fetch_started_at=now, fetch_finished_at=now, data_version=identity["data_version"])
        daemon._write_job_run(s.conn, identity=identity, status="SUCCESS", now_utc=now,
            started_at=now, lock_acquired_at=now,
            result={"status": "ok", "source_run_id": run_id, "snapshots_inserted": 1})
        for future in (False, True):
            start = s.run.replace(hour=0) + timedelta(days=int(future))
            expected = list(range(27 - hour, 49 - hour, 3)) if future else list(wanted[1:])
            write_source_run_coverage(s.conn, coverage_id=f"private-{metric}-{future}", source_run_id=run_id,
                source_id="ecmwf_open_data", source_transport="ensemble_snapshots_db_reader",
                release_calendar_key=identity["release_calendar_key"], track=track + "_full_horizon",
                city_id="fixture-city", city="London", city_timezone="UTC", target_local_date=start.date(),
                temperature_metric=metric, physical_quantity=metric + "_extreme", observation_field=metric,
                data_version=identity["data_version"], expected_members=51, observed_members=51,
                expected_steps_json=expected, observed_steps_json=expected,
                target_window_start_utc=start, target_window_end_utc=start + timedelta(days=1),
                completeness_status="PARTIAL" if metric == "low" and not (future and future_supply) else "COMPLETE",
                readiness_status="BLOCKED" if metric == "low" and not (future and future_supply) else "LIVE_ELIGIBLE",
                reason_code="PRIVATE_INTERVAL_CENSORED_Y" if metric == "low" else None,
                computed_at=now, expires_at=start + timedelta(days=1))
            s.conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) "
                "VALUES(?,?,?,?,?,?)", (f"private-{metric}-{future}", "London", start.date().isoformat(),
                    metric, "private-token", "point"))
    s.conn.commit()
    path = tmp_path / "normal-forecasts.db"
    def connection(**kwargs):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(db, "get_forecasts_connection", connection)
    monkeypatch.setattr(daemon, "_forecast_work_identity", lambda track, **kwargs: identities[track])
    monkeypatch.setattr(daemon, "_is_source_paused", lambda _: False)
    monkeypatch.setattr(daemon, "_utcnow", lambda: now)
    monkeypatch.setattr(daemon, "_held_revision_migration_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon, "_committed_held_opendata_wake", lambda *args, **kwargs: None)
    monkeypatch.setattr(source, "_resolve_opendata_paths", lambda: s.paths)
    yield SimpleNamespace(**vars(s), daemon=daemon, identities=identities, now=now)
    s.conn.close()


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
def test_normal_dispatch_drains_native_after_real_coordinate_bound_collectors(tmp_path, monkeypatch, track):
    """Actual H/L collectors write the identity consumed by the normal poll."""
    import hashlib
    import eccodes as ec
    import numpy as np
    from concurrent.futures import Future
    from src import config
    from src.state import db
    from src.state.source_run_repo import write_source_run
    from src.state.source_run_coverage_repo import write_source_run_coverage
    from src.data import ecmwf_open_data as source
    from src.ingest import forecast_live_daemon as daemon
    from src.data.forecast_fetch_plan import data_version_for_track
    from src.data.forecast_target_contract import compute_target_local_day_window_utc, required_period_end_steps
    from scripts import extract_open_ens_localday as decoder
    from tests.test_ingest_grib_source_run_context import _tiny_native_grib
    from tests.test_ecmwf_open_data_collect_cycle import (
        _normal_native_http, _native_temperature_knots_fixture, _physical_static_originals,
    )

    s = _normal_native_http(tmp_path, monkeypatch, steps=tuple(range(0, 25, 3)))
    now = s.run + timedelta(hours=10)
    monkeypatch.setattr(daemon, "_utcnow", lambda: now)
    sql_clock = sqlite3.connect(":memory:")
    s.conn.create_function("strftime", 2, lambda fmt, value:
        now.isoformat(timespec="milliseconds") if (fmt, value) == ("%Y-%m-%dT%H:%M:%f+00:00", "now")
        else sql_clock.execute("SELECT strftime(?,?)", (fmt, value)).fetchone()[0])
    class ClockType(type):
        def __instancecheck__(cls, value): return isinstance(value, datetime)
    class CaptureClock(datetime, metaclass=ClockType):
        @classmethod
        def now(cls, tz=None): return now.astimezone(tz or timezone.utc)
    monkeypatch.setattr(source, "datetime", CaptureClock)
    monkeypatch.setattr(source._ingest_grib_module, "_now_utc_iso", lambda: now.isoformat())
    original_values = ec.codes_set_values
    def constant_original(gid, values):
        if ec.codes_get(gid, "paramId") in {167, 228026, 228027}:
            ec.codes_set(gid, "packingType", "grid_ieee")
            ec.codes_set(gid, "precision", 2)
            values = np.full(len(values), 284.15)
        return original_values(gid, values)
    monkeypatch.setattr(ec, "codes_set_values", constant_original)
    manifest_json = config.runtime_coordinate_manifest_json()
    manifest_sha = hashlib.sha256(manifest_json.encode()).hexdigest()
    manifest = tmp_path / "current-coordinate-manifest.json"
    manifest.write_text(manifest_json)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    static = _native_temperature_knots_fixture(static_dir, steps=(0, 3), hour=0)
    identities = {}
    old_ids = []
    day = compute_target_local_day_window_utc(city_timezone=config.runtime_cities_by_name()["London"].timezone,
        target_local_date=s.run.date())
    # Previously journaled old-coordinate coverage stays as historical truth.
    # It must not occupy setdefault ahead of the real current collectors below.
    for old_track, metric in (("mx2t6_high", "high"), ("mn2t6_low", "low")):
        frame = json.loads(manifest_json)
        frame["cities"][0]["lon"] += .001
        old_manifest = json.dumps(frame, sort_keys=True, separators=(",", ":"))
        old = daemon._forecast_work_identity_for_cycle(old_track, cycle_time=s.run, now_utc=now)
        old.update(coordinate_manifest_json=old_manifest,
            coordinate_manifest_sha=hashlib.sha256(old_manifest.encode()).hexdigest(),
            data_version=data_version_for_track(old_track, old_manifest))
        old_id = daemon._expected_source_run_id(old)
        old_ids.append(old_id)
        write_source_run(s.conn, source_run_id=old_id, source_id="ecmwf_open_data",
            track=old_track + "_full_horizon", release_calendar_key=old["release_calendar_key"],
            source_cycle_time=s.run, status="SUCCESS", completeness_status="COMPLETE",
            expected_steps_json=source.STEP_HOURS, observed_steps_json=source.STEP_HOURS,
            data_version=old["data_version"])
        daemon._write_job_run(s.conn, identity=old, status="SUCCESS", now_utc=now,
            result={"status": "ok", "source_run_id": old_id, "snapshots_inserted": 1})
        ends = list(required_period_end_steps(source_cycle_time=s.run,
            target_window_start_utc=day.start_utc, target_window_end_utc=day.end_utc, period_hours=3))
        write_source_run_coverage(s.conn, coverage_id="old-coordinate-" + metric, source_run_id=old_id,
            source_id="ecmwf_open_data", source_transport="ensemble_snapshots_db_reader",
            release_calendar_key=old["release_calendar_key"], track=old_track + "_full_horizon",
            city_id="LONDON", city="London", city_timezone=config.runtime_cities_by_name()["London"].timezone,
            target_local_date=s.run.date(), temperature_metric=metric,
            physical_quantity=metric + "_extreme", observation_field=metric,
            data_version=old["data_version"], expected_members=51, observed_members=51,
            expected_steps_json=ends, observed_steps_json=ends,
            target_window_start_utc=day.start_utc, target_window_end_utc=day.end_utc,
            completeness_status="COMPLETE", readiness_status="LIVE_ELIGIBLE", computed_at=now,
            expires_at=day.end_utc)
    s.conn.commit()
    for source_track, metric in (("mx2t6_high", "high"), ("mn2t6_low", "low")):
        folder = tmp_path / source_track
        folder.mkdir()
        raw, _, _, _ = _tiny_native_grib(folder, source_track, issue=s.run, horizon=144)
        definition = decoder.TRACKS[source_track]
        target = source._download_output_path(run_date=s.run.date(), run_hour=0,
            param=definition.open_data_param, raw_root=s.paths.raw_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        bodies = []
        with raw.open("rb") as stream:
            while (gid := ec.codes_grib_new_from_file(stream)) is not None:
                try:
                    ec.codes_set(gid, "generatingProcessIdentifier", 161)
                    bodies.append(ec.codes_get_message(gid))
                finally:
                    ec.codes_release(gid)
        target.write_bytes(b"".join(bodies))
        mask, phi = _physical_static_originals(static, directory=target.parent, track=source_track)
        extracted = decoder.extract_open_ens_localday(grib_path=target, track_name=source_track,
            manifest_path=manifest, cities_filter={"London"},
            output_root=s.paths.raw_root / "raw/coordinate_manifests" / manifest_sha,
            mask_grib_path=mask, mask_proof_path=mask.with_suffix(".proof.json"),
            surface_geopotential_grib_path=phi, surface_geopotential_proof_path=phi.with_suffix(".proof.json"))
        sample = json.loads(Path(extracted["sample_outputs"][0]).read_text())
        identity = daemon._forecast_work_identity_for_cycle(source_track, cycle_time=s.run, now_utc=now)
        identities[source_track] = identity
        def local_capture(*, track, **kwargs):
            return source.collect_open_ens_cycle(track=track, **kwargs, skip_download=True,
                skip_extract=True, conn=s.conn, _paths=s.paths,
                grid_surface_source_evidence=sample["grid_surface_evidence"])
        result = daemon.run_opendata_track(source_track, _job_conn=s.conn, _now_utc=now,
            _identity=identity, _collector=local_capture, _source_paused=lambda _: False,
            _locks_dir_override=tmp_path / "locks")
        assert result["status"] == "ok", result
        row = s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (result["source_run_id"],)).fetchone()
        assert row["dataset_id"] == data_version_for_track(source_track, manifest_json)
        assert row["dataset_id"] != source.TRACKS[source_track]["data_version"]
        assert row["status"] == "SUCCESS" and row["observed_members"] == 51
        assert set(json.loads(row["expected_steps_json"])) <= set(json.loads(row["observed_steps_json"]))
        assert s.conn.execute("SELECT status FROM job_run WHERE source_run_id=?", (result["source_run_id"],)).fetchone()[0] == "SUCCESS"
        s.conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES(?,?,?,?,?,?)",
            ("normal-current-" + metric, "London", s.run.date().isoformat(), metric, "private-" + metric, "11C"))
        s.conn.commit()
    assert s.conn.execute("SELECT COUNT(*) FROM source_run WHERE track='2t_instant_native_knots'").fetchone()[0] == 0
    # The old frame alone is refused. Current rows were not made eligible by
    # deleting history: both frames coexist throughout the actual dispatch.
    saved = tuple(s.conn.execute("SELECT coverage_id FROM source_run_coverage WHERE source_run_id NOT IN (?,?)",
        tuple(old_ids)).fetchall())
    s.conn.execute("SAVEPOINT old_coordinate_only")
    s.conn.executemany("DELETE FROM source_run_coverage WHERE coverage_id=?", saved)
    assert daemon._native_temperature_transport_plans(s.conn, now_utc=now) == []
    s.conn.execute("ROLLBACK TO old_coordinate_only")
    s.conn.execute("RELEASE old_coordinate_only")
    def connection(**kwargs):
        result = sqlite3.connect(tmp_path / "normal-forecasts.db")
        result.row_factory = sqlite3.Row
        return result
    monkeypatch.setattr(db, "get_forecasts_connection", connection)
    monkeypatch.setattr(daemon, "_forecast_work_identity", lambda track, **kwargs: identities[track])
    monkeypatch.setattr(daemon, "_utcnow", lambda: now)
    monkeypatch.setattr(daemon, "_is_source_paused", lambda _: False)
    monkeypatch.setattr(daemon, "_held_revision_migration_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon, "_committed_held_opendata_wake", lambda *args, **kwargs: None)
    monkeypatch.setattr(source, "_resolve_opendata_paths", lambda **kwargs: s.paths)
    class Immediate:
        def submit(self, fn, *args):
            future = Future()
            try: future.set_result(fn(*args))
            except BaseException as exc: future.set_exception(exc)
            return future
    pending = {}
    daemon._dispatch_due_opendata_tracks(_executor=Immediate(), _inflight=pending)
    result = pending[track].result()
    assert result["native_temperature_source"]["status"] == "AVAILABLE", result
    assert any("Range" in call[1].get("headers", {}) for call in s.calls)
    assert s.conn.execute("SELECT COUNT(*) FROM source_run WHERE track='2t_instant_native_knots' AND ingest_mode='SCHEDULED_LIVE'").fetchone()[0] == 1
    assert s.conn.execute("SELECT COUNT(*) FROM source_run_coverage WHERE source_run_id IN (?,?)", tuple(old_ids)).fetchone()[0] == 2
    s.conn.close()
    sql_clock.close()


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("debt", ("bare", "old_coordinate", "wrong_metric", "missing_raw_partial",
    "failed", "unfinished_job", "raw_full_interval_partial"))
def test_normal_native_coordinate_priority_rejects_bad_identity_and_resets(normal_native_poll, track, debt):
    from src.data import ecmwf_open_data as source
    from src.contracts.ensemble_snapshot_provenance import coordinate_bound_data_version
    s = normal_native_poll
    identity = s.identities[track]
    run_id = s.daemon._expected_source_run_id(identity)
    previous = dict(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (run_id,)).fetchone())
    job = dict(s.conn.execute("SELECT * FROM job_run WHERE source_run_id=?", (run_id,)).fetchone())
    if debt in {"bare", "old_coordinate", "wrong_metric"}:
        version = (source.TRACKS[track]["data_version"] if debt == "bare" else
            coordinate_bound_data_version(source.TRACKS[track]["data_version"], "0" * 64) if debt == "old_coordinate" else
            s.identities["mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"]["data_version"])
        s.conn.execute("UPDATE source_run SET dataset_id=? WHERE source_run_id=?", (version, run_id))
    elif debt in {"missing_raw_partial", "raw_full_interval_partial"}:
        s.conn.execute("UPDATE source_run SET status='PARTIAL',completeness_status='PARTIAL',partial_run=1 WHERE source_run_id=?", (run_id,))
        s.conn.execute("UPDATE job_run SET status='PARTIAL' WHERE source_run_id=?", (run_id,))
        if debt == "missing_raw_partial":
            s.conn.execute("UPDATE source_run SET observed_steps_json='[3]' WHERE source_run_id=?", (run_id,))
    elif debt == "failed":
        s.conn.execute("UPDATE source_run SET status='FAILED' WHERE source_run_id=?", (run_id,))
    else:
        s.conn.execute("UPDATE job_run SET finished_at=NULL WHERE source_run_id=?", (run_id,))
    s.conn.commit()
    plans = s.daemon._native_temperature_transport_plans(s.conn, now_utc=s.now)
    if debt == "raw_full_interval_partial":
        assert plans and all(plan["priority"]() for plan in plans)
    else:
        assert plans == []
        assert s.calls == []  # A refused plan cannot start optional HTTP.
    s.conn.execute("UPDATE source_run SET dataset_id=?,status=?,completeness_status=?,partial_run=?,observed_steps_json=? WHERE source_run_id=?",
        (previous["dataset_id"], previous["status"], previous["completeness_status"], previous["partial_run"], previous["observed_steps_json"], run_id))
    s.conn.execute("UPDATE job_run SET status=?,finished_at=? WHERE source_run_id=?",
        (job["status"], job["finished_at"], run_id))
    s.conn.commit()
    reset = s.daemon._native_temperature_transport_plans(s.conn, now_utc=s.now)
    assert reset and all(plan["priority"]() for plan in reset)
    assert s.calls == []  # Inventory planning itself never acquires source bytes.


@pytest.mark.parametrize("normal_native_poll", ({"hour": 0, "future": True},
    {"hour": 6, "future": True}, {"hour": 18, "future": True}), indirect=True, ids=("00", "06", "18"))
def test_normal_native_real_future_market_does_not_remain_without_y_points(normal_native_poll):
    s = normal_native_poll
    result = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")
    active = result["native_temperature_source"]
    manifest = json.loads(Path(active["manifest_path"]).read_bytes())
    wanted = tuple(range(0, 49 - s.run.hour, 3))
    assert manifest["product_steps"] == list(wanted)
    assert len(manifest["messages"]) == 51 * len(wanted)
    assert s.conn.execute("SELECT COUNT(*) FROM ensemble_snapshots").fetchone()[0] == 0
    assert active["qualification_status"] == "UNKNOWN"
    assert active["future_runs"][0]["required_steps"] == list(range(24 - s.run.hour, 49 - s.run.hour, 3))
    assert active["future_runs"][0]["status"] == "AVAILABLE"
    calls = [call for call in s.calls if "Range" in call[1].get("headers", {})]
    assert len(calls) == 51 * len(wanted)  # Shared boundary and city/metric requests are not downloaded twice.


@pytest.mark.parametrize("normal_native_poll", ({"hour": 0, "future": True},), indirect=True)
def test_normal_native_future_append_partial_keeps_active_subset_and_resumes_originals(normal_native_poll, monkeypatch):
    s = normal_native_poll
    clock = [100.]
    monkeypatch.setattr(s.daemon.time, "monotonic", lambda: clock[0])
    get = s.session.get
    captured, spent = [False], [False]
    def partial_get(url, **kwargs):
        if captured[0] and not spent[0] and url.endswith(".index"):
            spent[0] = True
            clock[0] += 1.
        response = get(url, **kwargs)
        if not captured[0] and "-27h-" in url and "Range" in kwargs.get("headers", {}):
            captured[0] = True
            clock[0] += 58.
        return response
    s.session.get = partial_get
    first = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")["native_temperature_source"]
    assert first["status"] == "AVAILABLE" and first["future_runs"][0]["status"] == "INCOMPLETE", first
    assert first["future_runs"][0]["observed_count"] == 52
    manifest = Path(first["manifest_path"])
    originals = json.loads(manifest.read_bytes())["messages"]
    row = s.conn.execute("SELECT * FROM source_run WHERE track='2t_instant_native_knots'").fetchone()
    assert row["status"] == "PARTIAL" and row["source_available_at"] is None
    current = s.module.collect_native_temperature_source(conn=s.conn, run_utc=s.run,
        required_steps=list(range(0, 25, 3)), cycle_deadline_monotonic=159., _priority=lambda: True, _paths=s.paths)
    assert current["status"] == "AVAILABLE" and current["observed_count"] == 459
    s.session.get = get
    clock[0] = 200.
    second = s.daemon._run_journaled_opendata_track_if_due("mn2t6_low")["native_temperature_source"]
    assert second["status"] == second["future_runs"][0]["status"] == "AVAILABLE", second
    after = {(m["member"], m["step_hours"]): m for m in json.loads(manifest.read_bytes())["messages"]}
    assert all(after[(m["member"], m["step_hours"])] == m for m in originals)
    assert len(after) == 867
    assert s.conn.execute("SELECT status FROM source_run WHERE track='2t_instant_native_knots'").fetchone()[0] == "SUCCESS"
    assert second["qualification_status"] == "UNKNOWN"


@pytest.mark.parametrize("timezone_name,day_offset,expected_knots", (
    ("Asia/Kolkata", 1, tuple(range(18, 46, 3))),
    ("UTC", 6, (144, 150, 156, 162, 168)),
    ("UTC", 10, ()),
))
def test_normal_native_future_plan_uses_exact_timezone_end_and_native_horizon(normal_native_poll, timezone_name, day_offset, expected_knots):
    from src.data.forecast_target_contract import compute_target_local_day_window_utc, required_period_end_steps
    s = normal_native_poll
    target_day = s.run.date() + timedelta(days=day_offset)
    window = compute_target_local_day_window_utc(city_timezone=timezone_name, target_local_date=target_day)
    ends = list(required_period_end_steps(source_cycle_time=s.run,
        target_window_start_utc=window.start_utc, target_window_end_utc=window.end_utc, period_hours=3))
    s.conn.execute("DELETE FROM market_events")
    s.conn.execute("DELETE FROM source_run_coverage WHERE temperature_metric='low' OR target_local_date!=?", (s.run.date().isoformat(),))
    s.conn.execute("UPDATE source_run_coverage SET target_local_date=?,city_timezone=?,target_window_start_utc=?, "
        "target_window_end_utc=?,expected_steps_json=?,observed_steps_json=?,expires_at=?",
        (target_day.isoformat(), timezone_name, window.start_utc.isoformat(), window.end_utc.isoformat(),
         json.dumps(ends), json.dumps(ends), window.end_utc.isoformat()))
    s.conn.execute("UPDATE source_run SET expected_steps_json=?,observed_steps_json=?",
        (json.dumps(ends), json.dumps(ends)))
    s.conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES(?,?,?,?,?,?)",
        ("private-native-future-geometry", "London", target_day.isoformat(), "high", "private-token", "point"))
    s.conn.commit()
    plans = s.daemon._native_temperature_transport_plans(s.conn, now_utc=s.now)
    assert [plan["steps"] for plan in plans] == ([list(expected_knots)] if expected_knots else [])
    if plans:
        assert plans[0]["future"] and plans[0]["targets"] == [("London", target_day.isoformat(), "high", "full_Y")]
        assert s.run + timedelta(hours=expected_knots[0]) <= window.start_utc
        assert s.run + timedelta(hours=expected_knots[-1]) >= window.end_utc
    assert s.daemon._native_temperature_transport_plans(s.conn, now_utc=window.end_utc) == []
    assert s.calls == []  # Geometry evidence is not transport or source qualification.


@pytest.mark.parametrize("normal_native_poll", (0, 6), indirect=True)
def test_normal_native_actual_journaled_entry_drains_blocked_y_current_day(normal_native_poll, tmp_path):
    s = normal_native_poll
    result = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")
    assert result["status"] == "current_cycle_already_journaled"
    native = result["native_temperature_source"]
    assert native["status"] == "AVAILABLE", native
    steps = tuple(range(0, 25 - s.run.hour, 3))
    assert native["required_steps"] == list(steps)
    assert native["qualification_status"] == "UNKNOWN"
    assert len([c for c in s.calls if "Range" in c[1].get("headers", {})]) == 51 * len(steps)
    from tests.test_ecmwf_open_data_collect_cycle import _native_temperature_knots_fixture, _native_source_scope
    inputs_dir = tmp_path / "scope"
    inputs_dir.mkdir()
    inputs = _native_temperature_knots_fixture(inputs_dir, steps=steps, hour=s.run.hour)
    from types import SimpleNamespace
    scope = _native_source_scope(s.conn, SimpleNamespace(source_run_id=native["source_run_id"]),
        Path(native["manifest_path"]), inputs)
    assert scope.status == "AVAILABLE", scope
    assert len(scope.native_knots) == 51 * len(steps)
    assert scope.qualification_status == "UNKNOWN" and scope.available_at is None
    before = Path(native["manifest_path"]).read_bytes()
    s.calls.clear()
    again = s.daemon._run_journaled_opendata_track_if_due("mn2t6_low")
    assert again["native_temperature_source"]["source_run_id"] == native["source_run_id"]
    assert Path(native["manifest_path"]).read_bytes() == before
    assert s.calls == []


@pytest.mark.parametrize("debt", ("running", "missing_raw", "other_run", "paused", "expired", "expired_before_start", "unknown_scope"))
def test_normal_native_poll_mandatory_debt_never_spends_optional_http(normal_native_poll, monkeypatch, debt):
    s = normal_native_poll
    low = s.identities["mn2t6_low"]
    run_id = s.daemon._expected_source_run_id(low)
    if debt == "running":
        s.conn.execute("UPDATE job_run SET status='RUNNING' WHERE source_run_id=?", (run_id,))
    elif debt == "missing_raw":
        s.conn.execute("UPDATE source_run SET observed_steps_json='[3]' WHERE source_run_id=?", (run_id,))
    elif debt == "other_run":
        s.conn.execute("UPDATE source_run SET source_cycle_time=? WHERE source_run_id=?",
            ((s.run - timedelta(hours=6)).isoformat(), run_id))
    elif debt == "paused":
        monkeypatch.setattr(s.daemon, "_is_source_paused", lambda _: True)
    elif debt == "expired":
        # SUCCESS fair admission intentionally precedes the ordinary commit.
        # Expire at its real transport boundary, not an unreached callback.
        original = s.module._NativeDeadlineSession
        def session():
            monkeypatch.setattr(s.daemon.time, "monotonic", lambda: 10**10)
            return original()
        monkeypatch.setattr(s.module, "_NativeDeadlineSession", session)
    elif debt == "expired_before_start":
        original = s.daemon._utcnow
        ticks = [0]
        def utcnow():
            ticks[0] += 1
            if ticks[0] == 2:  # Exact optional journal's real start timestamp.
                monkeypatch.setattr(s.daemon.time, "monotonic", lambda: 10**10)
            return original()
        monkeypatch.setattr(s.daemon, "_utcnow", utcnow)
    else:
        s.conn.execute("UPDATE source_run_coverage SET expected_steps_json='[]'")
    s.conn.commit()
    result = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")
    assert s.calls == []
    assert s.conn.execute("SELECT COUNT(*) FROM source_run WHERE track='2t_instant_native_knots'").fetchone()[0] == 0
    assert result["native_temperature_source"]["status"] == "DEFERRED"


def test_normal_success_non_deadline_native_corruption_remains_unknown(normal_native_poll):
    s = normal_native_poll
    first = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")["native_temperature_source"]
    assert first["status"] == "AVAILABLE", first
    cache = Path(first["manifest_path"]).parent
    part = next(cache.glob("step*-member*.grib2"))
    original = part.read_bytes()
    source_before = tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (first["source_run_id"],)).fetchone())
    part.write_bytes(b"broken private GRIB original")
    s.calls.clear()
    result = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")["native_temperature_source"]
    assert result["status"] == "UNKNOWN" and result["reason"] != "STEP_DEADLINE_EXCEEDED", result
    assert s.calls == [] and part.read_bytes() != original
    assert tuple(s.conn.execute("SELECT * FROM source_run WHERE source_run_id=?", (first["source_run_id"],)).fetchone()) == source_before


def test_normal_mandatory_work_commits_after_poll_cut_without_borrowing_it(normal_native_poll, monkeypatch):
    s = normal_native_poll
    clock = [100.]
    monkeypatch.setattr(s.daemon.time, "monotonic", lambda: clock[0])
    high = s.identities["mx2t6_high"]
    s.conn.execute("DELETE FROM job_run WHERE job_run_id=?", (s.daemon._job_run_id(high),))
    s.conn.commit()
    seen = []
    def mandatory(**kwargs):
        seen.append(kwargs)
        assert "cycle_deadline_monotonic" not in kwargs
        clock[0] += 180.  # Legal source work is not the scheduler poll cadence.
        return {"status": "ok", "source_run_id": s.daemon._expected_source_run_id(high),
            "data_version": high["data_version"], "snapshots_inserted": 1}
    monkeypatch.setattr(s.module, "collect_open_ens_cycle", mandatory)
    result = s.daemon._run_journaled_opendata_track_if_due("mx2t6_high")
    assert seen and clock[0] == 280.
    row = s.conn.execute("SELECT * FROM job_run WHERE job_run_id=?", (s.daemon._job_run_id(high),)).fetchone()
    assert row["status"] == "SUCCESS" and row["rows_written"] == 1
    assert result["native_temperature_source"]["status"] == "DEFERRED"
    assert s.calls == []


@pytest.mark.parametrize("metric,track", (("high", "mx2t6_high"), ("low", "mn2t6_low")))
def test_normal_native_single_metric_market_preserves_sibling_raw_priority(normal_native_poll, metric, track):
    s = normal_native_poll
    s.conn.execute("DELETE FROM market_events WHERE temperature_metric!=?", (metric,))
    s.conn.commit()
    result = s.daemon._run_journaled_opendata_track_if_due(track)
    assert result["native_temperature_source"]["status"] == "AVAILABLE", result
    assert len([call for call in s.calls if "Range" in call[1].get("headers", {})]) == 459
    sibling = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
    source_id = s.daemon._expected_source_run_id(s.identities[sibling])
    s.conn.execute("UPDATE source_run SET observed_steps_json='[]' WHERE source_run_id=?", (source_id,))
    s.conn.commit()
    s.calls.clear()
    debt = s.daemon._run_journaled_opendata_track_if_due(track)
    assert debt["native_temperature_source"]["status"] == "DEFERRED"
    assert s.calls == []


@pytest.mark.parametrize("track", ("mx2t6_high", "mn2t6_low"))
@pytest.mark.parametrize("turn", ("inflight", "partial", "503", "rival503"))
def test_normal_quick_tick_drains_y12_while_x18_is_inflight_and_resumes(tmp_path, monkeypatch, track, turn):
    from types import SimpleNamespace
    from src.data import ecmwf_open_data as source, job_lock
    from src.data.release_calendar import FetchDecision
    from src.ingest import forecast_live_daemon as daemon
    from src.config import runtime_coordinate_manifest_json
    from src.data.forecast_fetch_plan import data_version_for_track
    from src.state import db
    from src.state.source_run_repo import write_source_run
    from src.state.source_run_coverage_repo import write_source_run_coverage
    from tests.test_ecmwf_open_data_collect_cycle import _normal_native_http
    from contextlib import nullcontext

    steps = tuple(range(12, 37, 3))
    s = _normal_native_http(tmp_path, monkeypatch, steps=steps, hour=12)
    now = s.run + timedelta(hours=13)
    latest = s.run + timedelta(hours=12 if turn == "rival503" else 6)
    latest_identities = {}
    manifest_json = runtime_coordinate_manifest_json()
    for source_track, metric in (("mx2t6_high", "high"), ("mn2t6_low", "low")):
        old = dict(track=source_track, decision=FetchDecision.FETCH_ALLOWED, scheduled_for=s.run,
            job_name="private-normal-" + source_track, source_id="ecmwf_open_data",
            release_calendar_key="ecmwf_open_data:" + source_track + ":full_horizon",
            coordinate_manifest_json=manifest_json, data_version=data_version_for_track(source_track, manifest_json), metadata={})
        run_id = daemon._expected_source_run_id(old)
        write_source_run(s.conn, source_run_id=run_id, source_id="ecmwf_open_data", track=source_track + "_full_horizon",
            release_calendar_key=old["release_calendar_key"], source_cycle_time=s.run,
            status="SUCCESS", completeness_status="COMPLETE", expected_steps_json=list(steps[1:]),
            observed_steps_json=list(steps[1:]), data_version=old["data_version"])
        daemon._write_job_run(s.conn, identity=old, status="SUCCESS", now_utc=now,
            result={"status": "ok", "source_run_id": run_id, "snapshots_inserted": 1})
        day = (s.run + timedelta(hours=12)).date()
        start = datetime.combine(day, datetime.min.time(), timezone.utc)
        write_source_run_coverage(s.conn, coverage_id="private-y12-" + metric, source_run_id=run_id,
            source_id="ecmwf_open_data", track=source_track + "_full_horizon", city="London", city_timezone="UTC",
            release_calendar_key=old["release_calendar_key"], city_id="fixture-city",
            physical_quantity=metric + "_extreme", observation_field=metric,
            expected_members=51, observed_members=51,
            source_transport="private_fake_http", computed_at=now, expires_at=start + timedelta(days=1),
            target_local_date=day, temperature_metric=metric, target_window_start_utc=start,
            target_window_end_utc=start + timedelta(days=1), expected_steps_json=list(steps[1:]),
            observed_steps_json=list(steps[1:]), completeness_status="COMPLETE", readiness_status="LIVE_ELIGIBLE",
            data_version=old["data_version"])
        s.conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES(?,?,?,?,?,?)",
            ("private-y12-" + metric, "London", day.isoformat(), metric, "private-" + metric, "20"))
        current = {**old, "scheduled_for": latest,
            "release_calendar_key": "ecmwf_open_data:" + source_track + ":short_horizon"}
        latest_identities[source_track] = current
        write_source_run(s.conn, source_run_id=daemon._expected_source_run_id(current), source_id="ecmwf_open_data",
            track=source_track + "_short_horizon", release_calendar_key=current["release_calendar_key"],
            source_cycle_time=latest, status="FAILED", completeness_status="MISSING",
            expected_steps_json=[6, 9], observed_steps_json=[], data_version=current["data_version"])
        daemon._write_job_run(s.conn, identity=current, status="RUNNING", now_utc=now,
            started_at=now, result={"status": "running"})
    s.conn.commit()
    if turn == "rival503":
        # A separate active Paris full-Y scope from 18 sorts before London's
        # 12 scope. Neither scope is removed when the first provider is 503.
        from src.data.forecast_target_contract import compute_target_local_day_window_utc, required_period_end_steps
        import ecmwf.opendata
        rival = s.run + timedelta(hours=6)
        window = compute_target_local_day_window_utc(city_timezone="Europe/Paris", target_local_date=day)
        ends = list(required_period_end_steps(source_cycle_time=rival,
            target_window_start_utc=window.start_utc, target_window_end_utc=window.end_utc, period_hours=3))
        future_window = compute_target_local_day_window_utc(city_timezone="Europe/Paris", target_local_date=day + timedelta(days=1))
        future_ends = list(required_period_end_steps(source_cycle_time=rival,
            target_window_start_utc=future_window.start_utc, target_window_end_utc=future_window.end_utc, period_hours=3))
        for rival_track, metric in (("mx2t6_high", "high"), ("mn2t6_low", "low")):
            identity = {**latest_identities[rival_track], "scheduled_for": rival}
            run_id = daemon._expected_source_run_id(identity)
            write_source_run(s.conn, source_run_id=run_id, source_id="ecmwf_open_data", track=rival_track + "_short_horizon",
                release_calendar_key=identity["release_calendar_key"], source_cycle_time=rival,
                status="SUCCESS", completeness_status="COMPLETE", expected_steps_json=ends + future_ends,
                observed_steps_json=ends + future_ends, data_version=identity["data_version"])
            daemon._write_job_run(s.conn, identity=identity, status="SUCCESS", now_utc=now,
                result={"status": "ok", "source_run_id": run_id, "snapshots_inserted": 1})
            write_source_run_coverage(s.conn, coverage_id="private-y18-" + metric, source_run_id=run_id,
                source_id="ecmwf_open_data", track=rival_track + "_short_horizon", city="Paris", city_timezone="Europe/Paris",
                release_calendar_key=identity["release_calendar_key"], city_id="fixture-paris",
                physical_quantity=metric + "_extreme", observation_field=metric,
                expected_members=51, observed_members=51, source_transport="private_fake_http", computed_at=now,
                expires_at=window.end_utc, target_local_date=day, temperature_metric=metric,
                target_window_start_utc=window.start_utc, target_window_end_utc=window.end_utc,
                expected_steps_json=ends, observed_steps_json=ends, completeness_status="COMPLETE",
                readiness_status="LIVE_ELIGIBLE", data_version=identity["data_version"])
            s.conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES(?,?,?,?,?,?)",
                ("private-y18-" + metric, "Paris", day.isoformat(), metric, "private-paris-" + metric, "20"))
            write_source_run_coverage(s.conn, coverage_id="private-future-y18-" + metric, source_run_id=run_id,
                source_id="ecmwf_open_data", track=rival_track + "_short_horizon", city="Paris", city_timezone="Europe/Paris",
                release_calendar_key=identity["release_calendar_key"], city_id="fixture-paris",
                physical_quantity=metric + "_extreme", observation_field=metric,
                expected_members=51, observed_members=51, source_transport="private_fake_http", computed_at=now,
                expires_at=future_window.end_utc, target_local_date=day + timedelta(days=1), temperature_metric=metric,
                target_window_start_utc=future_window.start_utc, target_window_end_utc=future_window.end_utc,
                expected_steps_json=future_ends, observed_steps_json=future_ends, completeness_status="COMPLETE",
                readiness_status="LIVE_ELIGIBLE", data_version=identity["data_version"])
            s.conn.execute("INSERT INTO market_events(market_slug,city,target_date,temperature_metric,token_id,range_label) VALUES(?,?,?,?,?,?)",
                ("private-future-y18-" + metric, "Paris", (day + timedelta(days=1)).isoformat(), metric, "private-future-paris-" + metric, "20"))
        s.conn.commit()
        client = ecmwf.opendata.Client
        class RoutedClient(client):
            def _get_urls(self, **kwargs):
                result = super()._get_urls(**kwargs)
                if kwargs["time"] == 18:
                    result.urls = [url.replace("/12z/", "/18z/").replace(f"{s.run:%Y%m%d%H}", f"{rival:%Y%m%d%H}") for url in result.urls]
                return result
        monkeypatch.setattr(ecmwf.opendata, "Client", RoutedClient)
    def connection(**kwargs):
        conn = sqlite3.connect(tmp_path / "normal-forecasts.db")
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(db, "get_forecasts_connection", connection)
    utc_clock = [now]
    monkeypatch.setattr(daemon, "_utcnow", lambda: utc_clock[0])
    monkeypatch.setattr(daemon, "_forecast_work_identity", lambda track, **kwargs: latest_identities[track])
    monkeypatch.setattr(daemon, "_is_source_paused", lambda _: False)
    monkeypatch.setattr(daemon, "_held_revision_migration_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(daemon, "_committed_held_opendata_wake", lambda *args, **kwargs: None)
    monkeypatch.setattr(source, "_resolve_opendata_paths", lambda: s.paths)
    if turn == "rival503":
        import hashlib
        from src.state.job_run_repo import write_job_run
        plans = daemon._native_temperature_transport_plans(s.conn, now_utc=now, full_y_only=True)
        assert [(plan["run"].hour, plan["future"]) for plan in plans] == [(18, False), (12, False), (18, True)]
        old_plan = plans[1]
        scope = {"run": old_plan["run"].isoformat(), "targets": old_plan["targets"], "required_steps": old_plan["steps"]}
        scope_hash = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        # The first tranche chooses never-attempted active 18 over this older
        # receipt. After 18 fails, least-recent active 12 must beat untouched
        # future 18. Fresh SQLite connections on each dispatcher tick restore
        # this history; there is no process-local cursor.
        job_name = "forecast_live_native_2t_" + track
        write_job_run(s.conn, job_run_id=job_name + ":" + scope_hash, job_name=job_name, plane="forecast",
            scheduled_for=old_plan["run"], source_id="ecmwf_open_data", track="2t_instant_native_knots",
            release_calendar_key="ecmwf_open_data:native_2t:" + scope_hash,
            started_at=now - timedelta(hours=1), status="RUNNING" if track == "mx2t6_high" else "PARTIAL",
            expected_scope_json=scope, meta_json={"qualification_status": "UNKNOWN", "mandatory_attempt": []})
        s.conn.commit()
    class CaptureClock(datetime):
        @classmethod
        def now(cls, tz=None): return utc_clock[0].astimezone(tz or timezone.utc)
    monkeypatch.setattr(source, "datetime", CaptureClock)
    clock, expired, spent = [100.], [False], [False]
    rival_http = []
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock[0])
    get = s.session.get
    def private_get(url, **kwargs):
        if turn == "rival503":
            if "/18z/" in url:
                rival_http.append(url)
                assert url.endswith(".index") and kwargs["timeout"] <= 59.
                clock[0] += kwargs["timeout"]  # The one attempted scope spends the entire original cut.
                fixture_url = url.replace("/18z/", "/12z/").replace(f"{rival:%Y%m%d%H}", f"{s.run:%Y%m%d%H}")
                response = get(fixture_url.replace("-3h-", "-12h-"), **kwargs)
                response.status_code = 503
                return response
            return get(url, **kwargs)
        if expired[0] and turn != "503" and not spent[0] and url.endswith(".index"):
            spent[0] = True
            clock[0] += 1.
        response = get(url, **kwargs)
        if turn == "503" and not expired[0]:
            expired[0] = True
            response.status_code = 503
            return response
        if "Range" in kwargs.get("headers", {}) and not expired[0]:
            expired[0] = True
            clock[0] += 58.  # Complete original before the cut; next index spends its remaining second.
        return response
    s.session.get = private_get
    runner = daemon.run_opendata_track
    locks = tmp_path / "normal-locks"
    monkeypatch.setattr(daemon, "run_opendata_track", lambda track, **kwargs:
        runner(track, **{**kwargs, "_locks_dir_override": locks}))
    mandatory_calls = []
    def long_mandatory(**kwargs):
        assert "cycle_deadline_monotonic" not in kwargs
        mandatory_calls.append(kwargs)
        clock[0] += 180.
        utc_clock[0] += timedelta(seconds=180)
        identity = latest_identities[track]
        return {"status": "failed", "source_run_status": "FAILED", "source_run_completeness": "MISSING",
            "source_run_id": daemon._expected_source_run_id(identity), "error": "HTTP503",
            "data_version": identity["data_version"], "snapshots_inserted": 0}
    if turn != "inflight":
        monkeypatch.setattr(source, "collect_open_ens_cycle", long_mandatory)
        s.conn.execute("DELETE FROM job_run WHERE job_run_id=?", (daemon._job_run_id(latest_identities[track]),))
        s.conn.commit()
        # The real normal dispatcher and default safe-poll wrapper remain in
        # the chain. Only executor timing, release HTTP and weather bodies are
        # private; neither qualification nor source journal writes are mocked.
        from concurrent.futures import Future
        class InlineExecutor:
            def submit(self, runner, selected):
                future = Future()
                try:
                    future.set_result(runner(selected))
                except Exception as exc:
                    future.set_exception(exc)
                return future
        sibling = "mn2t6_low" if track == "mx2t6_high" else "mx2t6_high"
        inflight = {sibling: Future()}
        probe_cuts = []
        def private_release_probe(identity, *, poll_deadline_monotonic):
            probe_cuts.append(poll_deadline_monotonic)
            return {"status": "released", "source": "private_release_http"}
        monkeypatch.setattr(daemon, "_probe_newest_opendata_cycle_availability", private_release_probe)
        def tick():
            before = clock[0]
            report = daemon._dispatch_due_opendata_tracks(_executor=InlineExecutor(), _inflight=inflight)
            assert report[sibling]["status"] == "in_flight"
            result = inflight[track].result()
            if result["status"] == "failed":
                assert probe_cuts[-1] == before + 59.
            return result
    else:
        def tick():
            return daemon._run_journaled_opendata_track_if_due(track)
    try:
        if turn != "inflight":
            first_mandatory = tick()
            assert first_mandatory["status"] == "failed" and len(mandatory_calls) == 1
            if turn == "rival503":
                assert first_mandatory["native_temperature_source"]["status"] == "DRAINED"
                assert all(result["status"] == "DEFERRED" for result in first_mandatory["native_temperature_source"]["runs"])
            else:
                assert first_mandatory["native_temperature_source"]["status"] == "DEFERRED"
            assert s.calls == []
            clock[0] += 60.
            utc_clock[0] += timedelta(seconds=60)
        context = (job_lock.acquire_opendata_track_lock(track, _locks_dir_override=locks)
            if turn == "inflight" else nullcontext((True, None)))
        with context as (acquired, _):
            assert acquired
            before_turn = clock[0]
            first = tick()
            native = first["native_temperature_source"]
            assert first["status"] == ("skipped_lock_held" if turn == "inflight" else "native_temperature_optional_turn"), first
            if turn in {"503", "rival503"}:
                assert native["status"] == "DEFERRED" and "503" in native["reason"], native
                if turn == "rival503":
                    assert clock[0] == before_turn + 59.
                    assert len(rival_http) == len(s.calls) == 1
                    assert native["transport_run_utc"] == rival.isoformat()
                original = None
            else:
                assert native["status"] == "INCOMPLETE" and native["observed_count"] == 1, native
                original = json.loads(Path(native["manifest_path"]).read_bytes())["messages"][0]
            if turn != "inflight":
                journal = s.conn.execute("SELECT * FROM job_run WHERE track='2t_instant_native_knots' ORDER BY started_at DESC LIMIT 1").fetchone()
                assert journal["status"] == ("FAILED" if turn in {"503", "rival503"} else "PARTIAL")
                assert journal["rows_written"] == 0  # Native bodies are not mandatory forecast rows.
                metadata = json.loads(journal["meta_json"])
                assert metadata["collector_status"] == native["status"]
                expected_city = "Paris" if turn == "rival503" else "London"
                assert json.loads(journal["expected_scope_json"])["targets"] == [
                    [expected_city, day.isoformat(), "high", "full_Y"], [expected_city, day.isoformat(), "low", "full_Y"]]
                assert len(mandatory_calls) == 1
                before = len(s.calls)
                mandatory_again = tick()
                assert mandatory_again["status"] == "failed" and len(mandatory_calls) == 2
                assert len(s.calls) == before
                assert dict(s.conn.execute("SELECT * FROM job_run WHERE job_run_id=?", (journal["job_run_id"],)).fetchone()) == dict(journal)
            clock[0] += 60.
            utc_clock[0] += timedelta(seconds=60)
            second = tick()
            complete = second["native_temperature_source"]
            assert complete["status"] == "AVAILABLE" and complete["observed_count"] == 459, complete
            if turn == "rival503":
                assert complete["transport_run_utc"] == s.run.isoformat()
                assert len(rival_http) == 1  # Second tranche did not restart the exhausted scope.
                assert dict(s.conn.execute("SELECT * FROM job_run WHERE job_run_id=?", (journal["job_run_id"],)).fetchone()) == dict(journal)
                assert s.conn.execute("SELECT COUNT(*) FROM job_run WHERE track='2t_instant_native_knots'").fetchone()[0] == 2
            if original:
                assert json.loads(Path(complete["manifest_path"]).read_bytes())["messages"][0] == original
            if turn != "inflight":
                assert len(mandatory_calls) == 2
                s.calls.clear()
                # Completion resets this target's optional debt; subsequent
                # normal ticks go back to mandatory, never a perpetual turn.
                reset = tick()
                assert reset["status"] == "failed" and len(mandatory_calls) == 3
                assert s.calls == []
        rows = s.conn.execute("SELECT source_cycle_time FROM source_run WHERE track='2t_instant_native_knots'").fetchall()
        assert [row[0] for row in rows] == [s.run.isoformat()]
        for source_track, identity in latest_identities.items():
            job = s.conn.execute("SELECT * FROM job_run WHERE job_run_id=?", (daemon._job_run_id(identity),)).fetchone()
            assert job["status"] == ("FAILED" if turn != "inflight" and source_track == track else "RUNNING")
            assert job["rows_written"] == 0
    finally:
        s.conn.close()

import src.main as main
from src.runtime import reactor_wake


def _request(
    *,
    position_id: str,
    schema_version: int = 3,
    generation: str = "generation-1",
    probability_content_identity: str = "q-current",
    held_best_bid: float = 0.22,
    book_state: str = "EXECUTABLE",
) -> reactor_wake.HeldSellReauctionRequest:
    return reactor_wake.make_held_sell_reauction_request(
        position_id=position_id,
        family=("Paris", "2026-07-30", "low"),
        probability_content_identity=probability_content_identity,
        held_token_id=f"token-{position_id}",
        held_best_bid=held_best_bid,
        bid_observed_at="2026-07-30T12:00:00+00:00",
        probability_observed_at="2026-07-30T12:00:00+00:00",
        schema_version=schema_version,
        generation=generation,
        book_state=book_state,
    )


def _terminal_receipt(
    request: reactor_wake.HeldSellReauctionRequest,
    *,
    phase: str = "settled",
    chain_state: str = "synced",
    chain_shares: float | None = 4.0,
    settled_at: str = "2026-07-30T13:00:00+00:00",
    reason: str | None = None,
) -> reactor_wake.HeldSellReauctionReceipt:
    receipt_reason = reason or reactor_wake.held_sell_no_longer_exposed_reason(
        lifecycle_phase=phase,
        chain_state=chain_state,
        chain_shares=chain_shares,
        settled_at=settled_at,
    )
    return reactor_wake.HeldSellReauctionReceipt(
        request_id=request.request_id,
        material_identity=request.material_identity,
        generation=request.generation,
        schema_version=request.schema_version,
        scope_identity=request.scope_identity,
        book_state=request.book_state,
        attempt_identity=request.attempt_identity,
        status=reactor_wake.POSITION_NO_LONGER_EXPOSED,
        reason=receipt_reason or "INVALID_NO_EXPOSURE_PROOF",
        lifecycle_phase=phase,
        chain_state=chain_state,
        chain_shares=chain_shares,
        settled_at=settled_at,
    )


@pytest.mark.parametrize("schema_version", (1, 2, 3))
def test_terminal_receipt_completes_each_supported_request_version(
    tmp_path: Path, schema_version: int
) -> None:
    request = _request(
        position_id=f"terminal-v{schema_version}", schema_version=schema_version
    )
    receipt = _terminal_receipt(
        request,
        phase="economically_closed",
        chain_state="chain_confirmed_zero",
        chain_shares=0.0,
        settled_at="",
    )

    assert reactor_wake.persist_held_sell_reauction_receipts(
        (receipt,), path=tmp_path / "wake.json"
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=tmp_path / "wake.json"
    )


def test_terminal_receipt_requires_explicit_canonical_terminal_phase(
    tmp_path: Path,
) -> None:
    request = _request(position_id="bad-terminal")
    invalid = _terminal_receipt(request, phase="active")
    actuated_without_v3_q = reactor_wake.HeldSellReauctionReceipt(
        request_id=request.request_id,
        material_identity=request.material_identity,
        generation=request.generation,
        schema_version=3,
        scope_identity=request.scope_identity,
        attempt_identity=request.attempt_identity,
        status="ACTUATED",
        reason="must_remain_strict",
        selection_epoch_identity="epoch",
        sell_book_witness_identity="book",
    )

    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (invalid,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (actuated_without_v3_q,), path=tmp_path / "wake.json"
    )


@pytest.mark.parametrize("schema_version", (1, 2, 3))
@pytest.mark.parametrize(
    ("phase", "chain_state", "chain_shares", "settled_at"),
    (
        ("economically_closed", "unknown", 0.0, ""),
        ("economically_closed", "synced", None, ""),
        ("economically_closed", "synced", 1e-12, ""),
        ("settled", "synced", 4.0, ""),
    ),
)
def test_terminal_receipt_rejects_incomplete_chain_first_proof_for_all_versions(
    tmp_path: Path,
    schema_version: int,
    phase: str,
    chain_state: str,
    chain_shares: float | None,
    settled_at: str,
) -> None:
    request = _request(
        position_id=f"negative-{schema_version}-{phase}-{chain_state}",
        schema_version=schema_version,
    )
    receipt = _terminal_receipt(
        request,
        phase=phase,
        chain_state=chain_state,
        chain_shares=chain_shares,
        settled_at=settled_at,
    )

    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (receipt,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=tmp_path / "wake.json"
    )


def test_terminal_receipt_is_idempotent_and_cannot_cover_hash_drift(
    tmp_path: Path,
) -> None:
    old = _request(
        position_id="same-position",
        generation="stable-generation",
        probability_content_identity="q-old",
        held_best_bid=0.0,
        book_state="NO_EXECUTABLE_BOOK",
    )
    fresh = _request(
        position_id="same-position",
        generation="stable-generation",
        probability_content_identity="q-fresh",
        held_best_bid=0.23,
    )
    first = _terminal_receipt(old, chain_shares=7.0)
    rewritten = _terminal_receipt(old, chain_shares=0.0)

    assert old.request_id != fresh.request_id
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (first,), path=tmp_path / "wake.json"
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        (rewritten,), path=tmp_path / "wake.json"
    )
    assert (
        reactor_wake._read_held_sell_reauction_receipt(
            old.request_id, path=tmp_path / "wake.json"
        )
        == first
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (old,), path=tmp_path / "wake.json"
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (fresh,), path=tmp_path / "wake.json"
    )


def _install_trade_reader(
    monkeypatch, tmp_path: Path, rows: tuple[tuple[object, ...], ...]
):
    db_path = tmp_path / "zeus_trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT NOT NULL,
                chain_state TEXT,
                chain_shares REAL,
                settled_at TEXT
            )
            """
        )
        conn.executemany("INSERT INTO position_current VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()
    finally:
        conn.close()

    def _reader():
        return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    monkeypatch.setattr("src.state.db.get_trade_connection_read_only", _reader)


def test_canonical_terminal_query_drains_only_explicit_no_exposure_rows(
    monkeypatch, tmp_path: Path
) -> None:
    economically_closed = _request(position_id="economically-closed")
    settled_with_residual = _request(position_id="settled-residual")
    admin_closed = _request(position_id="admin-closed")
    voided = _request(position_id="voided")
    active = _request(position_id="active")
    day0_window = _request(position_id="day0-window")
    pending_exit = _request(position_id="pending-exit")
    missing = _request(position_id="missing")
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            (
                "economically-closed",
                "economically_closed",
                "chain_confirmed_zero",
                0.0,
                None,
            ),
            (
                "settled-residual",
                "settled",
                "synced",
                12.5,
                "2026-07-30T13:00:00+00:00",
            ),
            (
                "admin-closed",
                "admin_closed",
                "chain_confirmed_zero",
                0.0,
                None,
            ),
            ("voided", "voided", "closed_exited", 0.0, None),
            ("active", "active", "synced", 3.0, None),
            ("day0-window", "day0_window", "synced", 3.0, None),
            ("pending-exit", "pending_exit", "exit_pending", 3.0, None),
        ),
    )

    receipts = main._terminal_held_sell_reauction_receipts(
        (
            economically_closed,
            settled_with_residual,
            admin_closed,
            voided,
            active,
            day0_window,
            pending_exit,
            missing,
        )
    )

    assert {receipt.request_id for receipt in receipts} == {
        economically_closed.request_id,
        settled_with_residual.request_id,
        admin_closed.request_id,
        voided.request_id,
    }
    settled = next(
        receipt
        for receipt in receipts
        if receipt.request_id == settled_with_residual.request_id
    )
    assert (
        settled.lifecycle_phase,
        settled.chain_state,
        settled.chain_shares,
        settled.settled_at,
    ) == (
        "settled",
        "synced",
        12.5,
        "2026-07-30T13:00:00+00:00",
    )
    assert (
        settled.reason
        == reactor_wake.SELL_OBLIGATION_ENDED_BY_SETTLEMENT_ONLY
    )
    assert "REDEEM" not in settled.reason


def test_canonical_terminal_query_rejects_ambiguous_or_incomplete_proof(
    monkeypatch, tmp_path: Path
) -> None:
    requests = tuple(
        _request(position_id=position_id)
        for position_id in (
            "economic-unknown",
            "economic-null",
            "economic-positive",
            "settled-missing-time",
        )
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            ("economic-unknown", "economically_closed", "unknown", 0.0, None),
            ("economic-null", "economically_closed", "synced", None, None),
            ("economic-positive", "economically_closed", "synced", 1e-12, None),
            ("settled-missing-time", "settled", "synced", 8.0, None),
        ),
    )

    assert main._terminal_held_sell_reauction_receipts(requests) == ()


def test_canonical_terminal_query_retains_wake_on_trade_read_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")),
    )

    assert (
        main._terminal_held_sell_reauction_receipts(
            (_request(position_id="db-failure"),)
        )
        == ()
    )


def _install_structural_win_reader(
    monkeypatch,
    tmp_path: Path,
    request: reactor_wake.HeldSellReauctionRequest,
    *,
    command_state: str = "EXPIRED",
    debt_overrides: dict[str, object] | None = None,
    monitor_overrides: dict[str, object] | None = None,
    debt_sequence: int = 10,
    monitor_sequence: int = 11,
) -> Path:
    db_path = tmp_path / "structural-win-trades.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                chain_state TEXT,
                chain_shares REAL,
                settled_at TEXT,
                direction TEXT,
                token_id TEXT,
                no_token_id TEXT,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT,
                bin_label TEXT,
                condition_id TEXT
            )
            """
        )
        direction = "buy_no"
        bin_label = "Will the lowest temperature in Paris be 18°C on July 30?"
        condition_id = "condition-structural-win"
        conn.execute(
            "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.position_id,
                "day0_window",
                "synced",
                7.0,
                None,
                direction,
                f"yes-{request.position_id}",
                request.held_token_id,
                request.family[0],
                request.family[1],
                request.family[2],
                bin_label,
                condition_id,
            ),
        )
        conn.execute(
            """
            CREATE TABLE position_events (
                event_id TEXT PRIMARY KEY,
                position_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                occurred_at TEXT,
                payload_json TEXT
            )
            """
        )
        obligation = {
            "schema_version": 4,
            "request_id": request.request_id,
            "material_identity": request.material_identity,
            "scope_identity": request.scope_identity,
            "generation": request.generation,
            "attempt_identity": request.attempt_identity,
            "position_id": request.position_id,
            "held_token_id": request.held_token_id,
            "state": "ARMED",
        }
        if debt_overrides:
            obligation.update(debt_overrides)
        debt_payload = {
            "release_reason": "GLOBAL_SELL_SNAPSHOT_REAUCTION_REQUIRED",
            "status": "durable_wake_reserved",
            "held_sell_reauction_obligation": obligation,
        }
        monitor_payload = {
            "applied_validations": [
                "day0_absorbing_hard_fact",
                "day0_hard_fact_structural_win_hold",
            ],
            "bin_label": bin_label,
            "city": request.family[0],
            "condition_id": condition_id,
            "direction": direction,
            "exit_decision_selected_method": "day0_absorbing_hard_fact",
            "exit_decision_should_exit": False,
            "exit_decision_trigger": "DAY0_HARD_FACT_STRUCTURAL_WIN_HOLD",
            "last_monitor_prob": 1.0,
            "last_monitor_prob_is_fresh": True,
            "monitor_probability_receipt": {
                "hard_fact_evidence": {"source": "ogimet_metar_lfpg"}
            },
            "selected_method": "day0_absorbing_hard_fact",
            "target_date": request.family[1],
        }
        if monitor_overrides:
            monitor_payload.update(monitor_overrides)
        conn.executemany(
            "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?)",
            (
                (
                    "debt-event",
                    request.position_id,
                    debt_sequence,
                    "EXIT_RETRY_RELEASED",
                    "2026-07-30T12:00:00+00:00",
                    json.dumps(debt_payload, sort_keys=True),
                ),
                (
                    f"{request.position_id}:monitor_refreshed:{monitor_sequence}",
                    request.position_id,
                    monitor_sequence,
                    "MONITOR_REFRESHED",
                    "2026-07-30T12:01:00+00:00",
                    json.dumps(monitor_payload, sort_keys=True),
                ),
            ),
        )
        conn.execute(
            """
            CREATE TABLE venue_commands (
                command_id TEXT PRIMARY KEY,
                state TEXT,
                venue_order_id TEXT,
                idempotency_key TEXT,
                position_id TEXT,
                token_id TEXT,
                side TEXT,
                intent_kind TEXT,
                updated_at TEXT,
                created_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE venue_command_events (
                event_id TEXT PRIMARY KEY,
                command_id TEXT,
                sequence_no INTEGER,
                event_type TEXT,
                payload_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "prior-exit-command",
                command_state,
                None,
                "prior-exit-key",
                request.position_id,
                request.held_token_id,
                "SELL",
                "EXIT",
                "2026-07-30T12:00:00+00:00",
                "2026-07-30T12:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO venue_command_events VALUES (?, ?, ?, ?, ?)",
            (
                "prior-exit-event",
                "prior-exit-command",
                1,
                command_state,
                "{}",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: sqlite3.connect(f"file:{db_path}?mode=ro", uri=True),
    )
    return db_path


def test_structural_win_supersedes_exact_v4_debt_after_terminal_command(
    monkeypatch, tmp_path: Path
) -> None:
    request = _request(position_id="structural-win", schema_version=4)
    _install_structural_win_reader(monkeypatch, tmp_path, request)

    receipts = main._terminal_held_sell_reauction_receipts((request,))

    assert len(receipts) == 1
    receipt = receipts[0]
    assert (
        receipt.status,
        receipt.reason,
        receipt.position_id,
        receipt.held_token_id,
        receipt.debt_sequence_no,
        receipt.monitor_sequence_no,
    ) == (
        reactor_wake.SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
        reactor_wake.SUPERSEDED_BY_DAY0_HARD_FACT_STRUCTURAL_WIN,
        request.position_id,
        request.held_token_id,
        10,
        11,
    )
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-structural-win",
        held_sell_reauction_requests=(request,),
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        receipts, path=wake_path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=wake_path,
        allow_structural_win_supersession=True,
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"monitor_payload_sha256": "0" * 63},
        {"monitor_sequence_no": 10},
        {"monitor_probability_is_fresh": False},
        {"held_token_id": "different-held-token"},
        {"hard_fact_source": ""},
        {"hard_fact_finality": ""},
        {"hard_fact_source": "wu_icao_history"},
        {"hard_fact_finality": "FINAL_DAILY_SETTLEMENT"},
    ),
)
def test_structural_win_receipt_rejects_invalid_proof_or_lineage(
    monkeypatch,
    tmp_path: Path,
    mutation: dict[str, object],
) -> None:
    request = _request(position_id="invalid-structural-receipt", schema_version=4)
    _install_structural_win_reader(monkeypatch, tmp_path, request)
    receipt = main._terminal_held_sell_reauction_receipts((request,))[0]
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-invalid-structural-receipt",
        held_sell_reauction_requests=(request,),
    )

    assert not reactor_wake.persist_held_sell_reauction_receipts(
        (replace(receipt, **mutation),), path=wake_path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )


@pytest.mark.parametrize(
    "command_state",
    (
        "REVIEW_REQUIRED",
        "UNKNOWN",
        "SUBMIT_UNKNOWN_SIDE_EFFECT",
        "ACKED",
        "UNRECOGNIZED_STATE",
    ),
)
def test_structural_win_cannot_supersede_nonterminal_or_unknown_command(
    monkeypatch, tmp_path: Path, command_state: str
) -> None:
    request = _request(position_id=f"blocked-{command_state}", schema_version=4)
    _install_structural_win_reader(
        monkeypatch,
        tmp_path,
        request,
        command_state=command_state,
    )

    assert main._terminal_held_sell_reauction_receipts((request,)) == ()


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("hko_daily_api", True),
        ("ogimet_metar_lfpg", True),
        ("hko_hourly_accumulator_v1", False),
        ("wu_icao_history", False),
    ),
)
def test_structural_win_requires_absorbing_source_finality(
    monkeypatch,
    tmp_path: Path,
    source: str,
    expected: bool,
) -> None:
    request = _request(
        position_id=f"structural-source-{source}",
        schema_version=4,
    )
    _install_structural_win_reader(
        monkeypatch,
        tmp_path,
        request,
        monitor_overrides={
            "monitor_probability_receipt": {
                "hard_fact_evidence": {"source": source}
            }
        },
    )

    assert bool(main._terminal_held_sell_reauction_receipts((request,))) is expected


@pytest.mark.parametrize(
    ("monitor_overrides", "debt_overrides", "debt_sequence", "monitor_sequence"),
    (
        ({"last_monitor_prob": 0.99}, None, 10, 11),
        ({"last_monitor_prob_is_fresh": False}, None, 10, 11),
        ({"selected_method": "model_only_v1"}, None, 10, 11),
        ({"exit_decision_should_exit": True}, None, 10, 11),
        ({"exit_decision_trigger": "SELL_REVERSAL"}, None, 10, 11),
        ({"monitor_probability_receipt": {}}, None, 10, 11),
        (
            {
                "monitor_probability_receipt": {
                    "hard_fact_evidence": {"source": "wu_icao_history"}
                }
            },
            None,
            10,
            11,
        ),
        (None, {"attempt_identity": "stale-attempt"}, 10, 11),
        (None, {"schema_version": 3}, 10, 11),
        (None, {"schema_version": None}, 10, 11),
        (None, None, 11, 11),
    ),
)
def test_structural_win_supersession_rejects_stale_or_mismatched_proof(
    monkeypatch,
    tmp_path: Path,
    monitor_overrides: dict[str, object] | None,
    debt_overrides: dict[str, object] | None,
    debt_sequence: int,
    monitor_sequence: int,
) -> None:
    request = _request(position_id="mismatched-proof", schema_version=4)
    _install_structural_win_reader(
        monkeypatch,
        tmp_path,
        request,
        monitor_overrides=monitor_overrides,
        debt_overrides=debt_overrides,
        debt_sequence=debt_sequence,
        monitor_sequence=monitor_sequence,
    )

    assert main._terminal_held_sell_reauction_receipts((request,)) == ()


def test_structural_win_supersession_is_exact_and_leaves_sibling_bytes_unchanged(
    monkeypatch, tmp_path: Path
) -> None:
    matched = _request(position_id="matched-structural-win", schema_version=4)
    sibling = _request(position_id="sibling-still-pending", schema_version=4)
    _install_structural_win_reader(monkeypatch, tmp_path, matched)
    wake_path = tmp_path / "wake.json"
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-matched",
        held_sell_reauction_requests=(matched,),
    )
    sibling_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-sibling",
        held_sell_reauction_requests=(sibling,),
    )
    sibling_file = reactor_wake._wake_queue_target(sibling_wake, path=wake_path)
    sibling_bytes = sibling_file.read_bytes()

    receipts = main._terminal_held_sell_reauction_receipts((matched, sibling))

    assert tuple(receipt.position_id for receipt in receipts) == (
        matched.position_id,
    )
    assert reactor_wake.persist_held_sell_reauction_receipts(
        receipts, path=wake_path
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (matched,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (matched,),
        path=wake_path,
        allow_structural_win_supersession=True,
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (sibling,), path=wake_path
    )
    assert sibling_file.read_bytes() == sibling_bytes


def _structural_win_coordinator(db_path: Path):
    from src.state.write_coordinator import DBIdentity, WriteCoordinator

    return WriteCoordinator({DBIdentity.TRADE: db_path})


def test_structural_win_atomic_revalidation_persists_and_acks_under_writer_lock(
    monkeypatch, tmp_path: Path
) -> None:
    request = _request(position_id="atomic-structural-win", schema_version=4)
    db_path = _install_structural_win_reader(monkeypatch, tmp_path, request)
    wake_path = tmp_path / "wake.json"
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-atomic-structural-win",
        held_sell_reauction_requests=(request,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)

    completed, failed = main._atomically_ack_structural_win_wakes(
        (wake,),
        wake_path=wake_path,
        coordinator=_structural_win_coordinator(db_path),
    )

    assert failed is False
    assert completed == (wake,)
    assert not queue_file.exists()
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )
    assert reactor_wake.held_sell_reauction_requests_completed(
        (request,),
        path=wake_path,
        allow_structural_win_supersession=True,
    )


@pytest.mark.parametrize("race", ("unknown_command", "newer_monitor_reversal"))
def test_structural_win_atomic_revalidation_closes_snapshot_to_ack_race(
    monkeypatch,
    tmp_path: Path,
    race: str,
) -> None:
    request = _request(position_id=f"race-{race}", schema_version=4)
    db_path = _install_structural_win_reader(monkeypatch, tmp_path, request)
    wake_path = tmp_path / "wake.json"
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id=f"wake-race-{race}",
        held_sell_reauction_requests=(request,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    queue_bytes = queue_file.read_bytes()
    assert main._terminal_held_sell_reauction_receipts((request,))

    conn = sqlite3.connect(db_path)
    try:
        if race == "unknown_command":
            conn.execute(
                "INSERT INTO venue_commands VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "racing-unknown-command",
                    "UNKNOWN",
                    None,
                    "racing-unknown-key",
                    request.position_id,
                    request.held_token_id,
                    "SELL",
                    "EXIT",
                    "2026-07-30T12:02:00+00:00",
                    "2026-07-30T12:02:00+00:00",
                ),
            )
            conn.execute(
                "INSERT INTO venue_command_events VALUES (?, ?, ?, ?, ?)",
                (
                    "racing-unknown-event",
                    "racing-unknown-command",
                    1,
                    "SUBMIT_UNKNOWN_SIDE_EFFECT",
                    "{}",
                ),
            )
        else:
            prior = conn.execute(
                """
                SELECT payload_json FROM position_events
                 WHERE position_id = ? AND event_type = 'MONITOR_REFRESHED'
                """,
                (request.position_id,),
            ).fetchone()
            payload = json.loads(prior[0])
            payload.update(
                {
                    "last_monitor_prob": 0.4,
                    "selected_method": "model_only_v1",
                    "exit_decision_selected_method": "model_only_v1",
                    "exit_decision_should_exit": True,
                    "exit_decision_trigger": "SELL_REVERSAL",
                }
            )
            conn.execute(
                "INSERT INTO position_events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"{request.position_id}:monitor_refreshed:12",
                    request.position_id,
                    12,
                    "MONITOR_REFRESHED",
                    "2026-07-30T12:02:00+00:00",
                    json.dumps(payload, sort_keys=True),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    completed, failed = main._atomically_ack_structural_win_wakes(
        (wake,),
        wake_path=wake_path,
        coordinator=_structural_win_coordinator(db_path),
    )

    assert failed is True
    assert completed == ()
    assert queue_file.read_bytes() == queue_bytes
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (request,), path=wake_path
    )


def test_multi_wake_ack_restores_all_visible_bytes_when_staging_fails(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / "wake.json"
    first = reactor_wake.publish_reactor_wake(
        source="test",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-stage-first",
    )
    second = reactor_wake.publish_reactor_wake(
        source="test",
        reason="forecast_posterior_advanced",
        path=wake_path,
        wake_id="wake-stage-second",
    )
    first_file = reactor_wake._wake_queue_target(first, path=wake_path)
    second_file = reactor_wake._wake_queue_target(second, path=wake_path)
    before = (first_file.read_bytes(), second_file.read_bytes())
    real_replace = reactor_wake.os.replace
    stage_count = 0

    def _fail_second_stage(source, destination):
        nonlocal stage_count
        if str(destination).endswith(".ack-stage"):
            stage_count += 1
            if stage_count == 2:
                raise OSError("injected second-stage failure")
        return real_replace(source, destination)

    monkeypatch.setattr(reactor_wake.os, "replace", _fail_second_stage)

    assert not reactor_wake.acknowledge_reactor_wakes(
        (first, second), path=wake_path
    )
    assert (first_file.read_bytes(), second_file.read_bytes()) == before


def test_multi_wake_ack_reports_success_after_all_are_staged(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / "wake.json"
    wakes = tuple(
        reactor_wake.publish_reactor_wake(
            source="test",
            reason=reason,
            path=wake_path,
            wake_id=f"wake-cleanup-{index}",
        )
        for index, reason in enumerate(
            ("market_price_advanced", "forecast_posterior_advanced")
        )
    )
    queue_files = tuple(
        reactor_wake._wake_queue_target(wake, path=wake_path) for wake in wakes
    )
    real_unlink = Path.unlink
    stage_cleanup_count = 0

    def _fail_second_hidden_cleanup(target, *args, **kwargs):
        nonlocal stage_cleanup_count
        if str(target).endswith(".ack-stage"):
            stage_cleanup_count += 1
            if stage_cleanup_count == 2:
                raise OSError("injected hidden cleanup failure")
        return real_unlink(target, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _fail_second_hidden_cleanup)

    assert reactor_wake.acknowledge_reactor_wakes(wakes, path=wake_path)
    assert all(not queue_file.exists() for queue_file in queue_files)


def test_listener_ack_staging_failure_never_runs_stale_structural_global_cut(
    monkeypatch, tmp_path: Path
) -> None:
    request = _request(position_id="listener-stage-failure", schema_version=4)
    db_path = _install_structural_win_reader(monkeypatch, tmp_path, request)
    wake_path = tmp_path / "wake.json"
    monkeypatch.setattr("src.config.state_path", lambda _filename: wake_path)
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-listener-stage-failure",
        held_sell_reauction_requests=(request,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    queue_bytes = queue_file.read_bytes()
    if not wake_path.exists():
        wake_path.write_bytes(queue_bytes)
    legacy_bytes = wake_path.read_bytes()
    actual_acknowledge_many = reactor_wake.acknowledge_reactor_wakes
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wakes",
        actual_acknowledge_many,
    )
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        "src.state.write_coordinator.default_runtime_write_coordinator",
        lambda: _structural_win_coordinator(db_path),
    )
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("global_cut") or True,
    )
    real_replace = reactor_wake.os.replace
    stage_count = 0

    def _fail_second_stage(source, destination):
        nonlocal stage_count
        if str(destination).endswith(".ack-stage"):
            stage_count += 1
            if stage_count == 2:
                raise OSError("injected listener stage failure")
        return real_replace(source, destination)

    monkeypatch.setattr(reactor_wake.os, "replace", _fail_second_stage)

    assert main._edli_reactor_wake_poll_once() is False
    assert order == []
    assert queue_file.read_bytes() == queue_bytes
    assert wake_path.read_bytes() == legacy_bytes


def _wake(*requests: reactor_wake.HeldSellReauctionRequest) -> reactor_wake.ReactorWake:
    return reactor_wake.ReactorWake(
        wake_id="wake-terminal",
        published_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc).isoformat(),
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        held_sell_reauction_requests=requests,
    )


def _install_listener_dependencies(monkeypatch, wake, order: list[str]) -> None:
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_edli_last_reactor_wake_id", None)
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(
        main,
        "_edli_day0_post_monitor_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(reactor_wake, "read_reactor_wake", lambda **_kwargs: wake)
    monkeypatch.setattr(
        reactor_wake, "coalescible_reactor_wakes", lambda _wake: (wake,)
    )
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wake",
        lambda _wake: order.append("ack") or True,
    )
    monkeypatch.setattr(
        reactor_wake,
        "acknowledge_reactor_wakes",
        lambda _wakes: order.append("ack") or True,
    )


def _install_position_fill_scope_readers(
    monkeypatch,
    tmp_path: Path,
    *,
    event_rows: tuple[tuple[str, str, str], ...],
    position_rows: tuple[tuple[object, ...], ...],
) -> None:
    world_path = tmp_path / "zeus-world.db"
    world = sqlite3.connect(world_path)
    try:
        world.execute(
            "CREATE TABLE opportunity_events "
            "(event_id TEXT PRIMARY KEY, event_type TEXT, payload_json TEXT)"
        )
        world.executemany(
            "INSERT INTO opportunity_events VALUES (?, ?, ?)", event_rows
        )
        world.commit()
    finally:
        world.close()

    trade_path = tmp_path / "zeus_trades.db"
    trade = sqlite3.connect(trade_path)
    try:
        trade.execute(
            """
            CREATE TABLE position_current (
                position_id TEXT PRIMARY KEY,
                phase TEXT,
                shares REAL,
                cost_basis_usd REAL,
                city TEXT,
                target_date TEXT,
                temperature_metric TEXT
            )
            """
        )
        trade.executemany(
            "INSERT INTO position_current VALUES (?, ?, ?, ?, ?, ?, ?)",
            position_rows,
        )
        trade.commit()
    finally:
        trade.close()

    def _reader(path: Path):
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    monkeypatch.setattr(
        main,
        "get_world_connection_read_only",
        lambda: _reader(world_path),
    )
    monkeypatch.setattr(
        "src.state.db.get_trade_connection_read_only",
        lambda: _reader(trade_path),
    )


def _position_fill_wake(*, wake_id: str = "wake-position-fill"):
    return reactor_wake.ReactorWake(
        wake_id=wake_id,
        published_at="2026-08-08T12:00:00+00:00",
        source="fill_tracker",
        reason="position_fill_projected",
        event_ids=("event-position-fill",),
    )


def _position_fill_event_payload(*position_ids: str) -> str:
    return json.dumps(
        {
            "redecision_origin": "position_fill",
            "position_fill_position_ids": list(position_ids),
        }
    )


def test_position_fill_scope_uses_current_local_only_position_family(
    monkeypatch, tmp_path: Path
) -> None:
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("local-only"),
            ),
        ),
        position_rows=(
            (
                "local-only",
                "active",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )

    assert main._position_fill_wake_held_families(("event-position-fill",)) == frozenset(
        {("Paris", "2026-08-08", "low")}
    )


def test_position_fill_scope_missing_current_identity_requires_full_book(
    monkeypatch, tmp_path: Path
) -> None:
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("present", "missing"),
            ),
        ),
        position_rows=(
            (
                "present",
                "active",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )

    assert main._position_fill_wake_held_families(("event-position-fill",)) is None


def test_finished_position_fill_wake_monitors_before_reactor_and_ack(
    monkeypatch, tmp_path: Path
) -> None:
    wake = _position_fill_wake()
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("local-only"),
            ),
        ),
        position_rows=(
            (
                "local-only",
                "day0_window",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=False,
            finished=True,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: True)

    def _dispatch(wake_ids, target_families):
        assert wake_ids == (wake.wake_id,)
        assert target_families == frozenset({("Paris", "2026-08-08", "low")})
        order.append("monitor")
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("cycle") or True,
    )
    main._forecast_exit_monitor_attempts.clear()

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert order == ["monitor", "cycle", "ack"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_uncertain_position_fill_scope_uses_full_book_monitor(
    monkeypatch, tmp_path: Path
) -> None:
    wake = _position_fill_wake(wake_id="wake-position-fill-uncertain")
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                json.dumps({"redecision_origin": "position_fill"}),
            ),
        ),
        position_rows=(),
    )
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=False,
            finished=True,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: True)

    def _dispatch(wake_ids, target_families):
        assert target_families is None
        order.append("monitor")
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("cycle") or True,
    )
    main._forecast_exit_monitor_attempts.clear()

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert order == ["monitor", "cycle", "ack"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_position_fill_monitor_failure_retains_wake_for_retry(
    monkeypatch, tmp_path: Path
) -> None:
    wake = _position_fill_wake(wake_id="wake-position-fill-retry")
    _install_position_fill_scope_readers(
        monkeypatch,
        tmp_path,
        event_rows=(
            (
                "event-position-fill",
                "EDLI_REDECISION_PENDING",
                _position_fill_event_payload("local-only"),
            ),
        ),
        position_rows=(
            (
                "local-only",
                "pending_exit",
                2.0,
                0.40,
                "Paris",
                "2026-08-08",
                "low",
            ),
        ),
    )
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=False,
            finished=True,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: True)
    attempts = iter((False, True))

    def _allow_retry() -> frozenset[str]:
        with main._forecast_exit_monitor_attempts_lock:
            main._forecast_exit_monitor_attempts.pop(wake.wake_id, None)
        return frozenset()

    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", _allow_retry)

    def _dispatch(wake_ids, _target_families):
        order.append("monitor")
        succeeded = next(attempts)
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = succeeded
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: order.append("cycle") or True,
    )
    main._forecast_exit_monitor_attempts.clear()

    try:
        assert main._edli_reactor_wake_poll_once() is False
        assert order == ["monitor"]
        assert main._edli_reactor_wake_poll_once() is True
        assert order == ["monitor", "monitor", "cycle", "ack"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_listener_persists_terminal_before_exact_cut_and_acks_mixed_batch(
    monkeypatch,
) -> None:
    terminal = _request(position_id="terminal", schema_version=1)
    active = _request(position_id="active", schema_version=1)
    wake = _wake(terminal, active)
    order: list[str] = []
    _install_listener_dependencies(monkeypatch, wake, order)
    monkeypatch.setattr(
        main,
        "_terminal_held_sell_reauction_receipts",
        lambda _requests: (_terminal_receipt(terminal),),
    )
    monkeypatch.setattr(
        reactor_wake,
        "persist_held_sell_reauction_receipts",
        lambda _receipts: order.append("persist") or True,
    )
    cycle_finished = False

    def _completed(requests):
        request_ids = {request.request_id for request in requests}
        if request_ids == {terminal.request_id}:
            return True
        if request_ids == {active.request_id}:
            return cycle_finished
        return cycle_finished

    def _cycle(**kwargs):
        nonlocal cycle_finished
        assert kwargs["producer_held_sell_reauction_requests"] == (active,)
        order.append("cycle")
        cycle_finished = True
        return True

    monkeypatch.setattr(
        reactor_wake, "held_sell_reauction_requests_completed", _completed
    )
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)

    assert main._edli_reactor_wake_poll_once() is True
    assert order == ["persist", "cycle", "ack"]


def test_listener_acks_old_terminal_wake_but_never_short_circuits_active(
    monkeypatch,
) -> None:
    terminal = _request(position_id="terminal-only", schema_version=1)
    terminal_wake = _wake(terminal)
    terminal_order: list[str] = []
    _install_listener_dependencies(monkeypatch, terminal_wake, terminal_order)
    monkeypatch.setattr(
        main,
        "_terminal_held_sell_reauction_receipts",
        lambda _requests: (_terminal_receipt(terminal),),
    )
    monkeypatch.setattr(
        reactor_wake,
        "persist_held_sell_reauction_receipts",
        lambda _receipts: terminal_order.append("persist") or True,
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: terminal_order.append("complete") or True,
    )
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **_kwargs: pytest.fail("terminal wake must not run an exact cut"),
    )

    assert main._edli_reactor_wake_poll_once() is True
    assert terminal_order == ["persist", "complete", "ack"]

    active = _request(position_id="active-only", schema_version=1)
    active_wake = _wake(active)
    active_order: list[str] = []
    _install_listener_dependencies(monkeypatch, active_wake, active_order)
    monkeypatch.setattr(main, "_terminal_held_sell_reauction_receipts", lambda _r: ())
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: False)

    assert main._edli_reactor_wake_poll_once() is False
    assert active_order == []


def test_oldest_active_wake_cannot_starve_later_terminal_queue_files(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    active = _request(position_id="oldest-active", schema_version=3)
    terminals = tuple(
        _request(position_id=f"terminal-{index:02d}", schema_version=3)
        for index in range(31)
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            ("oldest-active", "active", "synced", 5.0, None),
            *(
                (
                    request.position_id,
                    "settled",
                    "synced",
                    float(index + 1),
                    "2026-07-30T13:00:00+00:00",
                )
                for index, request in enumerate(terminals)
            ),
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_position_fill_wake_held_families",
        lambda _event_ids: frozenset(),
    )
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected_reasons: list[str] = []

    def _exact_cut(**kwargs):
        reason = kwargs["producer_wake_reason"]
        selected_reasons.append(reason)
        if reason == reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON:
            requests = kwargs["producer_held_sell_reauction_requests"]
            assert tuple(request.position_id for request in requests) == (
                "oldest-active",
            )
            return False
        return True

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _exact_cut)
    published_at = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    active_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-active",
        published_at=published_at,
        held_sell_reauction_requests=(active,),
    )
    for index, request in enumerate(terminals, start=1):
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=wake_path,
            wake_id=f"wake-terminal-{index:02d}",
            published_at=published_at + timedelta(microseconds=index),
            held_sell_reauction_requests=(request,),
        )
    other_wakes = tuple(
        reactor_wake.publish_reactor_wake(
            source="test_producer",
            reason=reason,
            path=wake_path,
            wake_id=f"wake-{reason}",
            published_at=published_at + timedelta(microseconds=40 + index),
        )
        for index, reason in enumerate(
            (
                "position_fill_projected",
                "market_price_advanced",
                "forecast_posterior_advanced",
            )
        )
    )
    active_queue_file = reactor_wake._wake_queue_target(
        active_wake, path=wake_path
    )
    active_bytes = active_queue_file.read_bytes()

    results = tuple(main._edli_reactor_wake_poll_once() for _ in range(8))

    remaining = reactor_wake.coalescible_reactor_wakes(
        reactor_wake.read_reactor_wake(path=wake_path),
        path=wake_path,
        max_wakes=100,
    )
    assert tuple(wake.wake_id for wake in remaining) == ("wake-active",)
    assert active_queue_file.read_bytes() == active_bytes
    assert results == (False, True, False, True, False, True, False, False)
    assert selected_reasons == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "position_fill_projected",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "market_price_advanced",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "forecast_posterior_advanced",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert all(
        not reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        for wake in other_wakes
    )
    assert all(
        reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )
        for request in terminals
    )
    assert not reactor_wake.held_sell_reauction_requests_completed(
        (active,), path=wake_path
    )


def test_global_completion_yield_preserves_day0_and_resets_without_work_or_restart(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    active = _request(position_id="active-only-yield", schema_version=3)
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (("active-only-yield", "active", "synced", 2.0, None),),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    exact_cut_count = 0
    selected_reasons: list[str] = []

    def _incomplete_exact_cut(**kwargs):
        nonlocal exact_cut_count
        reason = kwargs["producer_wake_reason"]
        selected_reasons.append(reason)
        if reason == "day0_extreme_event_committed":
            return True
        assert reason == reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        exact_cut_count += 1
        return False

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _incomplete_exact_cut)
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: False)
    monkeypatch.setattr(
        main, "_pending_held_day0_wake_families", lambda: frozenset()
    )
    wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-active-only-yield",
        published_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
        held_sell_reauction_requests=(active,),
    )
    queue_file = reactor_wake._wake_queue_target(wake, path=wake_path)
    queue_bytes = queue_file.read_bytes()

    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 1
    day0_wake = reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-during-global-yield",
        published_at=datetime(2026, 7, 30, 12, 1, tzinfo=timezone.utc),
    )
    day0_queue_file = reactor_wake._wake_queue_target(
        day0_wake, path=wake_path
    )

    assert main._edli_reactor_wake_poll_once() is True
    assert exact_cut_count == 1
    assert not day0_queue_file.exists()
    assert queue_file.read_bytes() == queue_bytes
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 2
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 2
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 3

    main._edli_initialize_reactor_wake_cursor()
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 4
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 4
    assert main._edli_reactor_wake_poll_once() is False
    assert exact_cut_count == 5
    assert selected_reasons[:3] == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        "day0_extreme_event_committed",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert queue_file.read_bytes() == queue_bytes
    assert reactor_wake.read_reactor_wake(path=wake_path) == wake


def test_executable_v4_exact_debt_does_not_yield_its_book_window(monkeypatch):
    request = _request(position_id="executable-v4", schema_version=4)
    wake = _wake(request)
    exclusion = main._OneTurnWakeExclusion()
    monkeypatch.setattr(main, "_edli_global_completion_yield", exclusion)

    main._yield_incomplete_global_completion_once(
        wake,
        (request,),
        wake_ids=(wake.wake_id,),
    )

    assert exclusion.consume() == frozenset()


def test_unfinished_day0_monitor_cannot_starve_exact_held_sell_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    request = _request(position_id="capital-debt-behind-unfinished-day0")
    monkeypatch.setattr("src.config.state_path", lambda filename: tmp_path / filename)
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main, "_edli_global_completion_yield", main._OneTurnWakeExclusion()
    )
    monkeypatch.setattr(
        main, "_edli_day0_post_monitor_yield", main._OneTurnWakeExclusion()
    )
    main._edli_initialize_reactor_wake_cursor()
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_day0_exit_monitor_attempt_state",
        lambda _wake_id: (False, None),
    )
    monkeypatch.setattr(
        main,
        "_dispatch_day0_exit_monitor",
        lambda _wake_id, _families: None,
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    selected_reasons: list[str] = []
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: selected_reasons.append(kwargs["producer_wake_reason"])
        or False,
    )
    published_at = datetime(2026, 8, 1, 11, 0, tzinfo=timezone.utc)
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-capital-debt-unfinished-day0",
        published_at=published_at,
        held_sell_reauction_requests=(request,),
    )
    reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-monitor-unfinished",
        published_at=published_at + timedelta(seconds=1),
        event_ids=("event-day0-unfinished",),
        forecast_families=(request.family,),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is False
    assert selected_reasons == [reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON]
    main._edli_initialize_reactor_wake_cursor()


def test_completed_day0_monitor_yields_one_turn_to_exact_held_sell_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    request = _request(position_id="capital-debt-behind-day0", schema_version=3)
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(
        main,
        "_edli_day0_post_monitor_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected_reasons: list[str] = []

    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_day0_exit_monitor_attempt_state",
        lambda _wake_id: (True, True),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )

    def _cycle(**kwargs):
        selected_reasons.append(kwargs["producer_wake_reason"])
        if (
            kwargs["producer_wake_reason"]
            == reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        ):
            assert kwargs["producer_held_sell_reauction_requests"] == (request,)
        return False

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)
    published_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    held_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-capital-debt",
        published_at=published_at,
        held_sell_reauction_requests=(request,),
    )
    day0_wake = reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-monitor-complete",
        published_at=published_at + timedelta(seconds=1),
        event_ids=("event-day0-incomplete",),
        forecast_families=(request.family,),
    )
    other_day0_wake = reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-also-queued",
        published_at=published_at + timedelta(milliseconds=500),
        event_ids=("event-day0-also-incomplete",),
        forecast_families=(request.family,),
    )
    held_bytes = reactor_wake._wake_queue_target(
        held_wake, path=wake_path
    ).read_bytes()
    day0_bytes = reactor_wake._wake_queue_target(
        day0_wake, path=wake_path
    ).read_bytes()
    other_day0_bytes = reactor_wake._wake_queue_target(
        other_day0_wake, path=wake_path
    ).read_bytes()

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is False
    assert selected_reasons == [
        "day0_extreme_event_committed",
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert (
        reactor_wake._wake_queue_target(held_wake, path=wake_path).read_bytes()
        == held_bytes
    )
    assert (
        reactor_wake._wake_queue_target(day0_wake, path=wake_path).read_bytes()
        == day0_bytes
    )
    assert (
        reactor_wake._wake_queue_target(
            other_day0_wake, path=wake_path
        ).read_bytes()
        == other_day0_bytes
    )


def test_day0_monitor_ack_failure_still_yields_exact_held_sell_turn(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    request = _request(position_id="capital-debt-after-ack-failure")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    monkeypatch.setattr(
        main,
        "_edli_day0_post_monitor_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_day0_exit_monitor_attempt_state",
        lambda _wake_id: (True, True),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=True,
        ),
    )
    monkeypatch.setattr(
        reactor_wake,
        "held_sell_reauction_requests_completed",
        lambda _requests: False,
    )
    acknowledgements: list[str] = []
    monkeypatch.setattr(
        main,
        "_acknowledge_edli_reactor_wake_batch",
        lambda wake, *_args, **_kwargs: acknowledgements.append(wake.wake_id)
        or False,
    )
    selected_reasons: list[str] = []
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: selected_reasons.append(kwargs["producer_wake_reason"])
        or False,
    )
    published_at = datetime(2026, 8, 1, 13, 0, tzinfo=timezone.utc)
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-capital-debt-after-ack-failure",
        published_at=published_at,
        held_sell_reauction_requests=(request,),
    )
    reactor_wake.publish_reactor_wake(
        source="day0_test_producer",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-ack-failure",
        published_at=published_at + timedelta(seconds=1),
        event_ids=("event-day0-terminal",),
        forecast_families=(request.family,),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert acknowledgements == ["wake-day0-ack-failure"]
    assert main._edli_reactor_wake_poll_once() is False
    assert selected_reasons == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
    ]


def test_incomplete_exact_shards_yield_one_price_and_keep_all_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    requests = tuple(
        _request(position_id=f"incomplete-shard-{index}", schema_version=3)
        for index in range(3)
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        tuple(
            (request.position_id, "active", "synced", 2.0, None)
            for request in requests
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected: list[tuple[str, tuple[str, ...]]] = []

    def _cycle(**kwargs):
        selected.append(
            (
                kwargs["producer_wake_reason"],
                tuple(
                    request.position_id
                    for request in kwargs.get("producer_held_sell_reauction_requests", ())
                ),
            )
        )
        return kwargs["producer_wake_reason"] != (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        )

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)
    published_at = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    exact_wakes = tuple(
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=wake_path,
            wake_id=f"wake-incomplete-{index}",
            published_at=published_at + timedelta(microseconds=index),
            held_sell_reauction_requests=(request,),
        )
        for index, request in enumerate(requests)
    )
    price_wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-after-exact",
        published_at=published_at + timedelta(seconds=1),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert selected == [
        (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            tuple(request.position_id for request in requests),
        )
    ]
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        wake.wake_id for wake in exact_wakes
    }
    assert all(
        reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        for wake in exact_wakes
    )
    assert all(
        not reactor_wake.held_sell_reauction_requests_completed(
            (request,), path=wake_path
        )
        for request in requests
    )

    assert main._edli_reactor_wake_poll_once() is True
    assert selected[-1] == ("market_price_advanced", ())
    assert not reactor_wake._wake_queue_target(price_wake, path=wake_path).exists()
    assert all(
        reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        for wake in exact_wakes
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert selected[-1] == (
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        tuple(request.position_id for request in requests),
    )


def test_exact_wake_snapshot_does_not_exclude_new_exact_debt(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    first = _request(position_id="snapshot-first", schema_version=3)
    newer = _request(position_id="snapshot-newer", schema_version=3)
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        (
            (first.position_id, "active", "synced", 2.0, None),
            (newer.position_id, "active", "synced", 2.0, None),
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected: list[str] = []
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: selected.append(kwargs["producer_wake_reason"]) or False,
    )
    published_at = datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc)
    first_wake = reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-snapshot-first",
        published_at=published_at,
        held_sell_reauction_requests=(first,),
    )
    reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-snapshot-price",
        published_at=published_at + timedelta(seconds=1),
    )

    assert main._edli_reactor_wake_poll_once() is False
    reactor_wake.publish_reactor_wake(
        source="held_position_monitor",
        reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        path=wake_path,
        wake_id="wake-snapshot-newer",
        published_at=published_at + timedelta(seconds=2),
        held_sell_reauction_requests=(newer,),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert selected == [
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
        reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
    ]
    assert reactor_wake._wake_queue_target(first_wake, path=wake_path).exists()
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        "wake-snapshot-first",
        "wake-snapshot-newer",
    }


def test_exact_fairness_keeps_price_and_forecast_batch_coalescing(
    monkeypatch, tmp_path: Path
) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    requests = tuple(
        _request(position_id=f"batch-exact-{index}", schema_version=3)
        for index in range(3)
    )
    _install_trade_reader(
        monkeypatch,
        tmp_path,
        tuple(
            (request.position_id, "active", "synced", 2.0, None)
            for request in requests
        ),
    )
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_edli_global_completion_yield",
        main._OneTurnWakeExclusion(),
    )
    main._edli_initialize_reactor_wake_cursor()
    selected: list[tuple[str, tuple[str, ...]]] = []

    def _cycle(**kwargs):
        selected.append(
            (
                kwargs["producer_wake_reason"],
                tuple(kwargs["producer_wake_ids"]),
            )
        )
        return kwargs["producer_wake_reason"] != (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON
        )

    monkeypatch.setattr(main, "_edli_event_reactor_cycle", _cycle)
    published_at = datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
    for index, request in enumerate(requests):
        reactor_wake.publish_reactor_wake(
            source="held_position_monitor",
            reason=reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            path=wake_path,
            wake_id=f"wake-batch-exact-{index}",
            published_at=published_at + timedelta(microseconds=index),
            held_sell_reauction_requests=(request,),
        )
    for index in range(2):
        reactor_wake.publish_reactor_wake(
            source="price_channel",
            reason="market_price_advanced",
            path=wake_path,
            wake_id=f"wake-batch-price-{index}",
            published_at=published_at + timedelta(seconds=1, microseconds=index),
        )
    for index in range(2):
        reactor_wake.publish_reactor_wake(
            source="forecast_producer",
            reason="forecast_posterior_advanced",
            path=wake_path,
            wake_id=f"wake-batch-forecast-{index}",
            published_at=published_at + timedelta(seconds=2, microseconds=index),
        )

    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is True
    assert main._edli_reactor_wake_poll_once() is False
    assert main._edli_reactor_wake_poll_once() is True
    assert selected[:3] == [
        (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            tuple(f"wake-batch-exact-{index}" for index in range(3)),
        ),
        ("market_price_advanced", ("wake-batch-price-0", "wake-batch-price-1")),
        (
            reactor_wake.GLOBAL_AUCTION_COMPLETION_WAKE_REASON,
            tuple(f"wake-batch-exact-{index}" for index in range(3)),
        ),
    ]
    assert selected[3][0] == "forecast_posterior_advanced"
    assert set(selected[3][1]) == {
        "wake-batch-forecast-0",
        "wake-batch-forecast-1",
    }
    assert reactor_wake.exact_held_sell_completion_wake_ids(path=wake_path) == {
        f"wake-batch-exact-{index}" for index in range(3)
    }


def test_price_wake_dispatches_held_monitor_before_reactor(monkeypatch, tmp_path: Path) -> None:
    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    family = ("Paris", "2026-07-30", "low")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(main, "_price_wake_target_families", lambda _event_ids: frozenset({family}))
    monkeypatch.setattr(main, "_forecast_wake_held_families", lambda _families: frozenset({family}))
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(ready=True, finished=False),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _event_ids: True)
    main._edli_initialize_reactor_wake_cursor()
    main._forecast_exit_monitor_attempts.clear()
    monitored = []
    dispatched = []

    def _dispatch(wake_ids, target_families, *, urgent_price=False):
        monitored.append((wake_ids, target_families, urgent_price))
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    monkeypatch.setattr(
        main,
        "_edli_event_reactor_cycle",
        lambda **kwargs: dispatched.append(kwargs["producer_wake_reason"]) or True,
    )
    wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-held-monitor",
        event_ids=("price-event",),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert monitored == [((wake.wake_id,), frozenset({family}), True)]
        assert dispatched == ["market_price_advanced"]
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_degraded_price_wake_retires_hint_after_held_monitor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Non-GREEN BUY debt stays durable without replaying one monitor hint forever."""

    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    family = ("Paris", "2026-07-30", "low")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_price_wake_target_families",
        lambda _event_ids: frozenset({family}),
    )
    monkeypatch.setattr(
        main,
        "_forecast_wake_held_families",
        lambda _families: frozenset({family}),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.DATA_DEGRADED)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    main._edli_initialize_reactor_wake_cursor()
    main._forecast_exit_monitor_attempts.clear()

    def _dispatch(wake_ids, _target_families, *, urgent_price=False):
        assert urgent_price is True
        with main._forecast_exit_monitor_attempts_lock:
            for wake_id in wake_ids:
                main._forecast_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_forecast_exit_monitor", _dispatch)
    wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-degraded",
        event_ids=("durable-price-event",),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert not reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        assert main._edli_reactor_wake_poll_once() is False
    finally:
        main._forecast_exit_monitor_attempts.clear()


def test_green_price_wake_keeps_hint_until_entry_event_finishes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Normal BUY-capable operation retains the exact event-completion fence."""

    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(
        main,
        "_price_wake_target_families",
        lambda _event_ids: frozenset({("Paris", "2026-07-30", "low")}),
    )
    monkeypatch.setattr(
        main,
        "_forecast_wake_held_families",
        lambda _families: frozenset(),
    )
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.GREEN)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    main._edli_initialize_reactor_wake_cursor()
    wake = reactor_wake.publish_reactor_wake(
        source="price_channel",
        reason="market_price_advanced",
        path=wake_path,
        wake_id="wake-price-green",
        event_ids=("unfinished-price-event",),
    )

    assert main._edli_reactor_wake_poll_once() is False
    assert reactor_wake._wake_queue_target(wake, path=wake_path).exists()


def test_degraded_day0_wake_retires_hint_after_held_monitor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A completed hard-fact monitor must not replay behind blocked BUY work."""

    from src.riskguard import riskguard
    from src.riskguard.risk_level import RiskLevel

    wake_path = tmp_path / reactor_wake.REACTOR_WAKE_FILENAME
    family = ("Paris", "2026-07-30", "high")
    monkeypatch.setattr(
        "src.config.state_path",
        lambda filename: tmp_path / filename,
    )
    monkeypatch.setattr(main, "_defer_for_held_position_monitor", lambda _job: False)
    monkeypatch.setattr(main, "_exit_monitor_excluded_wake_ids", lambda: frozenset())
    monkeypatch.setattr(main, "_day0_wake_requires_exit_monitor", lambda _scope: True)
    monkeypatch.setattr(
        main,
        "_reactor_wake_event_state",
        lambda _event_ids: main._ReactorWakeEventState(
            ready=True,
            finished=False,
        ),
    )
    monkeypatch.setattr(main, "_reactor_wake_events_finished", lambda _ids: False)
    monkeypatch.setattr(riskguard, "get_current_level", lambda: RiskLevel.YELLOW)
    monkeypatch.setattr(main, "_edli_event_reactor_cycle", lambda **_kwargs: True)
    main._edli_initialize_reactor_wake_cursor()
    main._day0_exit_monitor_attempts.clear()

    def _dispatch(wake_id, target_families):
        assert target_families == frozenset({family})
        with main._day0_exit_monitor_attempts_lock:
            main._day0_exit_monitor_attempts[wake_id] = True
        return True

    monkeypatch.setattr(main, "_dispatch_day0_exit_monitor", _dispatch)
    wake = reactor_wake.publish_reactor_wake(
        source="day0",
        reason="day0_extreme_event_committed",
        path=wake_path,
        wake_id="wake-day0-degraded",
        event_ids=("durable-day0-event",),
        forecast_families=(family,),
    )

    try:
        assert main._edli_reactor_wake_poll_once() is True
        assert not reactor_wake._wake_queue_target(wake, path=wake_path).exists()
        assert main._edli_reactor_wake_poll_once() is False
    finally:
        main._day0_exit_monitor_attempts.clear()
