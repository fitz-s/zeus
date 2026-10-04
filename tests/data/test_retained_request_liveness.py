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
