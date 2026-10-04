# Created: 2026-10-03
# Last reused/audited: 2026-10-03
# Authority basis: production incident 2026-10-03 (London 2026-10-02 low retained
#   643x; Atlanta 2026-10-03 high CURRENT_EVIDENCE_NOT_LIVE retained 51x).
"""Retained requests must reach a terminal outcome their own facts decide.

A. A request whose own contract lapsed (expires_at passed, or the city-local
   target day ended with no chain-held exposure) is retired with a receipt.
B. The materialization clock includes the bound anchor row's recorded_at, so a
   request that names only a manifest is not refused forever by the shared
   live-shape law, and CURRENT_EVIDENCE_NOT_LIVE from that clock is never a
   permanent verdict.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

UTC = timezone.utc


# --------------------------------------------------------------------------- B


def test_materialization_clock_lifts_to_bound_anchor_recorded_at(tmp_path):
    """The worker inserts a manifest-only request's anchor row after the request
    was stamped. The clock must not precede that row, or the shared live-shape
    law (recorded_at <= decision) refuses the posterior on every retry."""
    from src.data.materialization_block_evidence import effective_computed_at
    from src.data.replacement_forecast_materializer import _request_with_materialization_clock
    from dataclasses import make_dataclass

    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute("CREATE TABLE source_run(source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT)")
    conn.execute("CREATE TABLE raw_forecast_artifacts(artifact_id INTEGER PRIMARY KEY, recorded_at TEXT)")
    conn.execute("INSERT INTO raw_forecast_artifacts VALUES (1417076, '2026-10-03T21:27:48.728432+00:00')")
    conn.execute("INSERT INTO raw_forecast_artifacts VALUES (7, '2026-10-03 20:00:00')")  # legacy naive
    conn.commit()
    payload = dict(
        computed_at="2026-10-03T21:23:01.648394+00:00",
        baseline_source_run_id="base", openmeteo_source_run_id="om",
        baseline_source_available_at="2026-10-03T07:51:54+00:00",
        openmeteo_source_available_at="2026-10-03T18:35:09+00:00",
    )
    Request = make_dataclass("R", [*((k, object) for k in payload), ("anchor_artifact_id", object)])
    try:
        for anchor_id, expected in (
            (1417076, datetime(2026, 10, 3, 21, 27, 48, 728432, tzinfo=UTC)),  # lifted
            (7, datetime(2026, 10, 3, 21, 23, 1, 648394, tzinfo=UTC)),  # earlier row: unchanged
            (None, datetime(2026, 10, 3, 21, 23, 1, 648394, tzinfo=UTC)),  # unbound
            (999, datetime(2026, 10, 3, 21, 23, 1, 648394, tzinfo=UTC)),  # absent row
        ):
            request = Request(**{**payload, "computed_at": datetime.fromisoformat(payload["computed_at"])},
                              anchor_artifact_id=anchor_id)
            assert _request_with_materialization_clock(conn, request).computed_at == expected
            # The typed-evidence clock is the same rule, never a second one.
            assert effective_computed_at(conn, payload, anchor_artifact_id=anchor_id) == expected
    finally:
        conn.close()


def test_worker_serves_live_after_anchor_row_recorded_past_request_cut(tmp_path, monkeypatch):
    """End to end on the licensed current fixture: re-stamp the bound anchor row
    after the request's cut (what the worker's manifest write does in production).
    Before the fix the worker returned CAPTURE:CURRENT_EVIDENCE_NOT_LIVE forever."""
    from tests.test_cycle_monotone_materialization import _licensed_worker_queue

    gen, conn, _consume, _fenced, worker, _responses, _q = _licensed_worker_queue(tmp_path, monkeypatch)
    try:
        request = worker.request
        original = conn.execute(
            "SELECT recorded_at FROM raw_forecast_artifacts WHERE artifact_id=?",
            (request.anchor_artifact_id,),
        ).fetchone()[0]
        later = (request.computed_at + timedelta(minutes=4, seconds=47)).isoformat()
        conn.execute("UPDATE raw_forecast_artifacts SET recorded_at=? WHERE artifact_id=?",
                     (later, request.anchor_artifact_id))
        conn.commit()
        try:
            code, out, err = worker()
            response = json.loads((out or err).strip().splitlines()[-1])
            assert code == 0, response
            assert response["status"] == "READY", response
        finally:
            conn.execute("UPDATE raw_forecast_artifacts SET recorded_at=? WHERE artifact_id=?",
                         (original, request.anchor_artifact_id))
            conn.commit()
    finally:
        next(gen, None)


def test_clock_evidence_binds_anchor_recording(tmp_path):
    """A covered BLOCKED's CLOCK item names the bound anchor row's recording; a
    changed recording reopens it, and a malformed anchor item binds nothing."""
    from types import SimpleNamespace
    from src.data import materialization_block_evidence as e

    conn = sqlite3.connect(tmp_path / "f.db")
    conn.execute("CREATE TABLE source_run(source_run_id TEXT PRIMARY KEY, fetch_finished_at TEXT)")
    conn.execute("CREATE TABLE raw_forecast_artifacts(artifact_id INTEGER PRIMARY KEY, recorded_at TEXT)")
    conn.execute("INSERT INTO raw_forecast_artifacts VALUES (5, '2026-10-02T07:00:00+00:00')")
    conn.commit()
    payload = dict(city="Shanghai", target_date="2026-10-03", temperature_metric="high",
                   computed_at="2026-10-02T09:00:00+00:00", source_cycle_time="2026-10-02T00:00:00+00:00",
                   openmeteo_source_cycle_time="2026-08-01T00:00:00+00:00",
                   baseline_source_run_id="base", openmeteo_source_run_id="om",
                   baseline_source_available_at="2026-10-02T07:00:00+00:00",
                   openmeteo_source_available_at="2026-10-02T07:00:00+00:00",
                   anchor_artifact_id=5)
    try:
        proof = e.blocked_evidence(conn, SimpleNamespace(**payload), e.STALE_CYCLE)
        assert proof["items"][0]["anchor_artifact"] == {
            "artifact_id": 5, "recorded_at": "2026-10-02T07:00:00+00:00"}
        assert e.evidence_holds(conn, proof)
        assert e.evidence_holds(conn, proof, payload)
        conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='2026-10-02T08:00:00+00:00'")
        conn.commit()
        assert not e.evidence_holds(conn, proof)
        conn.execute("UPDATE raw_forecast_artifacts SET recorded_at='2026-10-02T07:00:00+00:00'")
        conn.commit()
        for bad in ([], {"artifact_id": "5", "recorded_at": None}, {"artifact_id": 5}):
            mutated = json.loads(json.dumps(proof))
            mutated["items"][0]["anchor_artifact"] = bad
            assert not e.evidence_holds(conn, mutated)
    finally:
        conn.close()


# --------------------------------------------------------------------------- A

_NOW = datetime(2026, 10, 3, 23, 59, tzinfo=UTC)  # London local 2026-10-04 00:59


def _london_request(**overrides):
    body = {
        "city": "London", "city_timezone": "Europe/London", "target_date": "2026-10-02",
        "temperature_metric": "low", "source_cycle_time": "2026-10-02T06:00:00+00:00",
        "computed_at": "2026-10-02T23:43:05.727910+00:00",
        "expires_at": "2026-10-03T12:00:00+00:00",
        "baseline_source_run_id": "base", "openmeteo_source_run_id": "om",
        "baseline_source_available_at": "2026-10-02T13:18:49+00:00",
        "openmeteo_source_available_at": "2026-10-02T18:40:25+00:00",
        "openmeteo_payload_json": "payload.json", "precision_metadata_json": "precision.json",
        "bins": [{"bin_id": "8C"}],
    }
    body.update(overrides)
    return body


def _drive(tmp_path, monkeypatch, body, *, held=frozenset(), held_error=False):
    import src.data.replacement_forecast_live_materialization_queue as queue

    root = tmp_path / "replacement_forecast_live"
    requests = root / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    name = f"{body['city']}.{body['target_date']}.{body['temperature_metric']}.timeout-retry-7-1.json"
    path = requests / name
    path.write_text(json.dumps(body))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return _NOW if tz is None else _NOW.astimezone(tz)

    monkeypatch.setattr(queue, "datetime", Clock)
    monkeypatch.setattr(queue, "_validate_request_payload", lambda _p: (True, "", ""))
    monkeypatch.setattr(queue, "_seed_source_cycle_boundary", lambda **_k: None)
    monkeypatch.setattr(queue, "_day0_carrier_vector_preflight_reason", lambda **_k: None)
    monkeypatch.setattr(queue, "_blocked_attempt_fingerprint", lambda **_k: None)
    monkeypatch.setattr(queue, "_instrument_set_expansion_already_applied", lambda **_k: False)
    reads = []

    def exposure(scopes, *, trade_conn=None, strict=False):
        reads.append((set(scopes), strict))
        if held_error:
            raise sqlite3.OperationalError("trade db unavailable")
        return frozenset(scopes) & held

    monkeypatch.setattr(queue, "_current_money_risk_scopes_for_exact_seeds", exposure)
    spawned = []

    def runner(argv):
        spawned.append(argv)
        return subprocess.CompletedProcess(argv, 1, json.dumps({
            "status": "BLOCKED",
            "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET",
                             "CAPTURE:CURRENT_EVIDENCE_NOT_LIVE"]}), "")

    report = queue._process_claimed_materialization_batch(
        request_path=requests, processed_path=root / "processed", failed_path=root / "failed",
        forecast_db=None, limit=1, runner=runner, marker_dir=root / "blocked_attempts",
        retry_path=requests,
    )
    receipt_dir = root / "superseded_latest"
    receipts = ([json.loads(p.read_text()) for p in receipt_dir.glob("*.json")]
                if receipt_dir.is_dir() else [])
    return queue, report, spawned, receipts, path, reads


def test_london_incident_request_is_retired_with_a_truthful_receipt(tmp_path, monkeypatch):
    """The production request: settled family, expired contract, ended local day."""
    queue, report, spawned, receipts, path, _reads = _drive(tmp_path, monkeypatch, _london_request())
    assert spawned == []
    assert not path.exists()
    assert queue._REQUEST_CONTRACT_LAPSED_STATUS in report.reason_codes
    assert len(receipts) == 1
    assert receipts[0]["status"] == "SKIPPED_REQUEST_CONTRACT_LAPSED"
    assert receipts[0]["reason_codes"] == [queue._REQUEST_EXPIRED_REASON]
    assert receipts[0]["result_evidence"]["expires_at"] == "2026-10-03T12:00:00+00:00"
    assert receipts[0]["result_evidence"]["subprocess_spawned"] is False


def test_ended_local_day_alone_retires_an_unheld_family(tmp_path, monkeypatch):
    body = _london_request(expires_at="2026-10-05T12:00:00+00:00")
    queue, _report, spawned, receipts, path, _reads = _drive(tmp_path, monkeypatch, body)
    assert spawned == [] and not path.exists()
    assert receipts[0]["reason_codes"] == [queue._REQUEST_TARGET_DAY_ENDED_REASON]


def test_held_family_after_its_local_day_keeps_its_request(tmp_path, monkeypatch):
    """Post-day held redecision and settlement still consume statistical q."""
    body = _london_request(expires_at="2026-10-05T12:00:00+00:00")
    scope = ("London", "2026-10-02", "low")
    _q, _report, spawned, receipts, _path, reads = _drive(
        tmp_path, monkeypatch, body, held=frozenset({scope}))
    assert len(spawned) == 1 and receipts == []
    assert reads and all(strict for _s, strict in reads)


def test_expired_contract_of_a_held_family_is_kept_while_its_cycle_is_in_bound(tmp_path, monkeypatch):
    """The pinned held reader ignores readiness expiry and checks only the cycle-age bound."""
    body = _london_request(target_date="2026-10-04", source_cycle_time="2026-10-03T00:00:00+00:00",
                           expires_at="2026-10-03T23:00:00+00:00")
    scope = ("London", "2026-10-04", "low")
    _q, _r, spawned, receipts, _p, _reads = _drive(tmp_path, monkeypatch, body, held=frozenset({scope}))
    assert len(spawned) == 1 and receipts == []
    stale = dict(body, source_cycle_time="2026-10-02T12:00:00+00:00")  # 36h > 30h bound
    q, _r, spawned, receipts, _p, _reads = _drive(
        tmp_path / "b", monkeypatch, stale, held=frozenset({scope}))
    assert spawned == [] and receipts[0]["reason_codes"] == [q._REQUEST_EXPIRED_REASON]


def test_unreadable_exposure_never_retires_what_exposure_decides(tmp_path, monkeypatch):
    """Only a lapse that depends on "not held" reads exposure; unknown is held."""
    body = _london_request(expires_at="2026-10-05T12:00:00+00:00")  # ended day, open contract
    _q, _r, spawned, receipts, path, reads = _drive(tmp_path, monkeypatch, body, held_error=True)
    assert reads and len(spawned) == 1 and receipts == [] and path.exists()


def test_expired_out_of_bound_cycle_retires_without_reading_exposure(tmp_path, monkeypatch):
    """The London cycle is ~42h old: no reader, held included, serves it."""
    q, _r, spawned, receipts, _p, reads = _drive(
        tmp_path, monkeypatch, _london_request(), held_error=True)
    assert spawned == [] and reads == []
    assert receipts[0]["reason_codes"] == [q._REQUEST_EXPIRED_REASON]


def test_open_unexpired_request_is_untouched_and_never_reads_exposure(tmp_path, monkeypatch):
    body = _london_request(target_date="2026-10-04", expires_at="2026-10-04T12:00:00+00:00",
                           source_cycle_time="2026-10-03T06:00:00+00:00")
    _q, _r, spawned, receipts, _p, reads = _drive(tmp_path, monkeypatch, body)
    assert len(spawned) == 1 and receipts == [] and reads == []


def test_unknown_city_or_bad_date_is_not_a_lapse():
    from src.data.replacement_forecast_live_materialization_queue import (
        _request_contract_lapse_reason,
    )

    def held(_scope):
        return False

    assert _request_contract_lapse_reason(
        _london_request(city="Atlantis", expires_at=None), now_utc=_NOW, held=held) is None
    assert _request_contract_lapse_reason(
        _london_request(target_date="not-a-date", expires_at=None), now_utc=_NOW, held=held) is None


def test_station_revision_producer_never_republishes_an_ended_unheld_day():
    """The London request came from the physical-current wake. Its scopes are each
    city's own local today plus held families, never an ended, unheld day."""
    from types import SimpleNamespace
    from src.data.physical_current_delivery import current_temperature_delivery_scopes

    london = SimpleNamespace(name="London", timezone="Europe/London")
    scopes = current_temperature_delivery_scopes((london,), now=_NOW, held={})
    assert ("London", "2026-10-02", "low") not in scopes
    assert {s[1] for s in scopes} == {"2026-10-04"}
    held = {("London", "2026-10-02", "low"): 1}
    assert ("London", "2026-10-02", "low") in current_temperature_delivery_scopes(
        (london,), now=_NOW, held=held)
