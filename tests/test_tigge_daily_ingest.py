# Created: 2026-05-01
# Last reused/audited: 2026-05-01
# Authority basis: Operator directive 2026-05-01 — TIGGE retrieval moves into the
#   ingest daemon. Antibody tests for: MARS-credential-missing auto-pause,
#   idempotent re-run, control_plane pause honored, catch-up window bounds.
"""Antibody tests for src.data.tigge_pipeline + src.ingest_main TIGGE wiring.

These tests mock all subprocesses; they never reach MARS, never spawn child
processes, and do not require ECMWF credentials to run.

What's covered
--------------
- check_mars_credentials: missing file, malformed JSON, empty values, OK.
- determine_catch_up_dates: cap, db_max_issue floor, no-op when current.
- run_tigge_daily_cycle: control-plane pause short-circuit, missing-creds
  auto-pause, success path with mocked stages, idempotent re-run.
- Antibody: the daemon decorator does not crash the daemon when the cycle
  raises (verified via _scheduler_job's exception swallowing).
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data import tigge_pipeline


# ---------------------------------------------------------------------------
# check_mars_credentials
# ---------------------------------------------------------------------------


def test_check_mars_credentials_missing_file(tmp_path):
    rc = tmp_path / "nonexistent_ecmwfapirc"
    result = tigge_pipeline.check_mars_credentials(rc_path=rc)
    assert result["ok"] is False
    assert "missing" in result["error"]
    assert result["source"] == "tigge_mars"


def test_check_mars_credentials_malformed_json(tmp_path):
    rc = tmp_path / ".ecmwfapirc"
    rc.write_text("not json {{")
    result = tigge_pipeline.check_mars_credentials(rc_path=rc)
    assert result["ok"] is False
    assert "not valid JSON" in result["error"]


def test_check_mars_credentials_missing_field(tmp_path):
    rc = tmp_path / ".ecmwfapirc"
    rc.write_text(json.dumps({"url": "x", "key": "y"}))  # missing email
    result = tigge_pipeline.check_mars_credentials(rc_path=rc)
    assert result["ok"] is False
    assert "missing fields" in result["error"]


def test_check_mars_credentials_empty_value(tmp_path):
    rc = tmp_path / ".ecmwfapirc"
    rc.write_text(json.dumps({"url": "https://api.ecmwf.int/v1", "key": "", "email": "a@b"}))
    result = tigge_pipeline.check_mars_credentials(rc_path=rc)
    assert result["ok"] is False
    assert "empty value" in result["error"]


def test_check_mars_credentials_ok(tmp_path):
    rc = tmp_path / ".ecmwfapirc"
    rc.write_text(json.dumps({
        "url": "https://api.ecmwf.int/v1",
        "key": "abc123",
        "email": "test@example.com",
    }))
    result = tigge_pipeline.check_mars_credentials(rc_path=rc)
    assert result["ok"] is True
    assert result["error"] is None


# ---------------------------------------------------------------------------
# determine_catch_up_dates
# ---------------------------------------------------------------------------


def test_determine_catch_up_dates_caps_at_max_lookback():
    today = date(2026, 5, 1)
    out = tigge_pipeline.determine_catch_up_dates(
        today_utc=today, max_lookback_days=7, db_max_issue=date(2026, 1, 1),
    )
    # Max 7 days back from yesterday (2026-04-30): floor=2026-04-24.
    assert out[0] == date(2026, 4, 24)
    assert out[-1] == date(2026, 4, 30)
    assert len(out) == 7


def test_determine_catch_up_dates_floor_uses_db_max_issue():
    today = date(2026, 5, 1)
    out = tigge_pipeline.determine_catch_up_dates(
        today_utc=today, max_lookback_days=7, db_max_issue=date(2026, 4, 28),
    )
    # Floor = max(today-7, db_max_issue+1) = 2026-04-29.
    assert out == [date(2026, 4, 29), date(2026, 4, 30)]


def test_determine_catch_up_dates_noop_when_current():
    today = date(2026, 5, 1)
    out = tigge_pipeline.determine_catch_up_dates(
        today_utc=today, max_lookback_days=7, db_max_issue=date(2026, 4, 30),
    )
    # db_max_issue == yesterday → nothing to do.
    assert out == []


def test_determine_catch_up_dates_no_db_returns_full_window():
    today = date(2026, 5, 1)
    out = tigge_pipeline.determine_catch_up_dates(
        today_utc=today, max_lookback_days=3, db_max_issue=None,
    )
    assert len(out) == 3
    assert out[-1] == date(2026, 4, 30)


# ---------------------------------------------------------------------------
# run_tigge_daily_cycle — control-plane pause
# ---------------------------------------------------------------------------


def test_run_cycle_paused_by_control_plane():
    with patch("src.control.control_plane.read_ingest_control_state") as m_read:
        m_read.return_value = {"paused_sources": {"tigge_mars"}}
        result = tigge_pipeline.run_tigge_daily_cycle()
    assert result["status"] == "paused_by_control_plane"
    assert result["source"] == "tigge_mars"
    assert result["stages"] == []


# ---------------------------------------------------------------------------
# run_tigge_daily_cycle — MARS credential missing → auto-pause antibody
# ---------------------------------------------------------------------------


def test_run_cycle_pauses_on_missing_credentials():
    """Antibody: missing MARS creds must auto-pause the source, not crash daemon."""
    pause_calls: list[tuple[str, bool]] = []

    def fake_pause(source_id: str, paused: bool) -> None:
        pause_calls.append((source_id, paused))

    def fake_check() -> dict:
        return {"ok": False, "error": "synthetic missing rc", "source": "tigge_mars"}

    with patch("src.control.control_plane.read_ingest_control_state",
               return_value={"paused_sources": set()}):
        result = tigge_pipeline.run_tigge_daily_cycle(
            _credential_checker=fake_check,
            _pause_source=fake_pause,
        )

    assert result["status"] == "paused_mars_credentials"
    assert result["error"] == "synthetic missing rc"
    assert pause_calls == [("tigge_mars", True)]
    # No subprocess stages were entered.
    assert result["stages"] == []


# ---------------------------------------------------------------------------
# run_tigge_daily_cycle — happy path with mocked stages
# ---------------------------------------------------------------------------


def _ok_runner(args, *, timeout, label):
    return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}


def _ok_creds():
    return {"ok": True, "error": None, "source": "tigge_mars"}


def test_run_cycle_happy_path_invokes_all_stages_per_track():
    """Success path: control-plane clear, creds OK, three stages per track."""
    runner_calls: list[str] = []

    def runner(args, *, timeout, label):
        runner_calls.append(label)
        return _ok_runner(args, timeout=timeout, label=label)

    fake_ingest_summary = {"track": "x", "data_version": "y", "json_root": "z",
                           "written": 5, "skipped": 0, "errors": 0}

    with patch("src.control.control_plane.read_ingest_control_state",
               return_value={"paused_sources": set()}), \
         patch("src.data.tigge_pipeline._ingest_track",
               return_value={"label": "ingest_mock", "ok": True,
                             "written": 5, "skipped": 0, "errors": 0,
                             "summary": fake_ingest_summary}), \
         patch("src.data.tigge_pipeline.determine_catch_up_dates",
               return_value=[date(2026, 4, 30)]):
        result = tigge_pipeline.run_tigge_daily_cycle(
            _runner=runner,
            _credential_checker=_ok_creds,
        )

    assert result["status"] == "ok"
    assert result["dates"] == ["2026-04-30"]
    # download + extract per track = 4 subprocess calls (mx + mn × dl + ex)
    assert sorted(runner_calls) == [
        "download_mn2t6_low", "download_mx2t6_high",
        "extract_mn2t6_low", "extract_mx2t6_high",
    ]
    # Aggregate counters from both tracks (5 each).
    assert result["written"] == 10
    assert result["skipped"] == 0
    assert result["errors"] == 0


def test_run_cycle_target_date_overrides_catch_up():
    """Single-date backfill via the same code path as the daemon."""
    runner_calls: list[tuple[str, list[str]]] = []

    def runner(args, *, timeout, label):
        runner_calls.append((label, args))
        return _ok_runner(args, timeout=timeout, label=label)

    with patch("src.control.control_plane.read_ingest_control_state",
               return_value={"paused_sources": set()}), \
         patch("src.data.tigge_pipeline._ingest_track",
               return_value={"label": "ingest_mock", "ok": True,
                             "written": 0, "skipped": 0, "errors": 0,
                             "summary": {}}):
        result = tigge_pipeline.run_tigge_daily_cycle(
            target_date="2026-04-15",
            _runner=runner,
            _credential_checker=_ok_creds,
        )

    assert result["status"] == "ok"
    assert result["dates"] == ["2026-04-15"]
    # All subprocess invocations must carry the requested date in args.
    for _label, args in runner_calls:
        assert "--date-from" in args and "2026-04-15" in args
        assert "--date-to" in args


def test_run_cycle_noop_when_db_current():
    """Empty catch-up window → noop_no_dates, no subprocesses, no ingests."""
    runner_calls: list[str] = []
    ingest_calls: list[str] = []

    def runner(args, *, timeout, label):
        runner_calls.append(label)
        return _ok_runner(args, timeout=timeout, label=label)

    def fake_ingest(track, *, date_from, date_to):
        ingest_calls.append(track)
        return {"label": "ingest_mock", "ok": True, "written": 0}

    with patch("src.control.control_plane.read_ingest_control_state",
               return_value={"paused_sources": set()}), \
         patch("src.data.tigge_pipeline._ingest_track", side_effect=fake_ingest), \
         patch("src.data.tigge_pipeline.determine_catch_up_dates", return_value=[]):
        result = tigge_pipeline.run_tigge_daily_cycle(
            _runner=runner,
            _credential_checker=_ok_creds,
        )

    assert result["status"] == "noop_no_dates"
    assert runner_calls == []
    assert ingest_calls == []


# ---------------------------------------------------------------------------
# Idempotent re-run — same date called twice should produce identical
# write counts on the SECOND call when the underlying ingest is idempotent.
# (The pipeline relies on UNIQUE constraint in ensemble_snapshots_v2; we
# simulate that here with a stateful fake.)
# ---------------------------------------------------------------------------


def test_run_cycle_idempotent_re_run():
    """Second invocation for the same date writes 0 new rows (UNIQUE skip)."""
    written_so_far = {"count": 0}

    def stateful_ingest(track, *, date_from, date_to):
        if written_so_far["count"] == 0:
            written_so_far["count"] = 51
            return {"label": "ingest", "ok": True, "written": 51, "skipped": 0, "errors": 0}
        return {"label": "ingest", "ok": True, "written": 0, "skipped": 51, "errors": 0}

    with patch("src.control.control_plane.read_ingest_control_state",
               return_value={"paused_sources": set()}), \
         patch("src.data.tigge_pipeline._ingest_track", side_effect=stateful_ingest), \
         patch("src.data.tigge_pipeline.determine_catch_up_dates",
               return_value=[date(2026, 4, 30)]):
        first = tigge_pipeline.run_tigge_daily_cycle(
            _runner=_ok_runner, _credential_checker=_ok_creds,
        )
        second = tigge_pipeline.run_tigge_daily_cycle(
            _runner=_ok_runner, _credential_checker=_ok_creds,
        )

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    # First run writes 51 per track (×2 tracks); second writes 0 (all skipped).
    assert first["written"] >= 51
    assert second["written"] == 0
    assert second["skipped"] >= first["written"]


# ---------------------------------------------------------------------------
# Daemon-level antibody: scheduler decorator swallows exceptions so a single
# cycle failure must NOT crash other ingest jobs.
# ---------------------------------------------------------------------------


def test_scheduler_decorator_swallows_exceptions():
    """Antibody: a raised exception in run_tigge_daily_cycle must NOT propagate
    out of the @_scheduler_job wrapper (daemon stays alive for other jobs).

    2026-05-01 rename: ``_tigge_daily_cycle`` → ``_tigge_archive_backfill_cycle``
    so the daemon health surface reflects the role (TIGGE has a 48h embargo
    and serves as a 2-day-lagged backfill source, not the live trading feed).
    """
    from src import ingest_main

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic tigge crash")

    # Patch the scheduler-health writer to a no-op so the test cannot pollute
    # production state/scheduler_jobs_health.json with a synthetic FAILED row.
    with patch("src.data.tigge_pipeline.run_tigge_daily_cycle", side_effect=boom), \
         patch("src.ingest_main._is_source_paused", return_value=False), \
         patch("src.observability.scheduler_health._write_scheduler_health"):
        # _tigge_archive_backfill_cycle is the decorated wrapper — should NOT raise.
        ingest_main._tigge_archive_backfill_cycle()
