# Created: 2026-05-08
# Last reused/audited: 2026-05-08
# Authority basis: docs/operations/task_2026-05-08_ecmwf_publication_strategy/REPORT.md §6.2
#   F1 subprocess hardening — retry + extended timeout + full stderr capture.
"""Unit tests for ECMWF Open Data subprocess hardening (F1).

Covers:
  - test_timeout_default_1500s: regression guard on the 600→1500 change.
  - test_subprocess_retry_succeeds_on_second_attempt: rc=1 then rc=0 → ok.
  - test_subprocess_retry_exhausts: rc=1×3 → download_failed.
  - test_skipped_not_released_distinguishes_grid_404: 404 on grid-valid step →
      SKIPPED_NOT_RELEASED; 404 on non-grid step (No index entries) → retried.
"""
from __future__ import annotations

import inspect
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.data.ecmwf_open_data import collect_open_ens_cycle

# Marker for tests that exercised the deleted subprocess download path.
# Superseded 2026-05-11 by parallel SDK fetch (test_ecmwf_open_data_parallel_fetch.py).
# Using skip (not xfail) — test bodies would hang on real HTTP connections
# since the subprocess download path they exercised no longer exists.
_SUBPROCESS_SUPERSEDED = pytest.mark.skip(
    reason="Superseded 2026-05-11: subprocess download path deleted. "
    "Parallel SDK _fetch_impl path tested in test_ecmwf_open_data_parallel_fetch.py.",
)


UTC = timezone.utc

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _ok_result(label: str) -> dict:
    return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}


def _fail_result(label: str, stderr: str = "transient error") -> dict:
    return {"label": label, "ok": False, "returncode": 1, "stdout_tail": "", "stderr_tail": stderr}


def _404_result(label: str, *, no_index: bool = False) -> dict:
    """Simulate a 404 failure.

    no_index=True mimics 'No index entries' in stderr (off-grid step, retryable).
    no_index=False mimics a plain 404 on a grid-valid step (SKIPPED_NOT_RELEASED).
    """
    stderr = "HTTPError: 404 Not Found" + (" No index entries for step" if no_index else "")
    return {"label": label, "ok": False, "returncode": 1, "stdout_tail": "", "stderr_tail": stderr}


def _make_conn():
    """In-memory SQLite connection with Zeus schema — used for retry-success tests."""
    from src.state.db import init_schema
    from src.state.schema.v2_schema import apply_canonical_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_canonical_schema(conn)
    return conn


# ---------------------------------------------------------------------------
# (d) regression guard: default download timeout must be 1500s
# ---------------------------------------------------------------------------

def test_timeout_default_1500s() -> None:
    """Default download_timeout_seconds must be 1500 (was 600 — F1 change)."""
    sig = inspect.signature(collect_open_ens_cycle)
    default = sig.parameters["download_timeout_seconds"].default
    assert default == 1500, (
        f"download_timeout_seconds default must be 1500 (empirical full-fetch ~610s). Got {default}. "
        "This is a regression guard for the F1 subprocess hardening change."
    )


# ---------------------------------------------------------------------------
# Helpers to call collect_open_ens_cycle with download only (no extract/ingest)
# ---------------------------------------------------------------------------

def _run_download_only(runner_fn, *, conn=None, monkeypatch=None, tmp_path=None) -> dict:
    """Call collect_open_ens_cycle with a mock runner and skip_extract=True.

    For failure-path tests (download_failed / skipped_not_released), conn can
    be None — the function returns before reaching the ingest stage.

    For the retry-success path, caller must supply conn + monkeypatch + tmp_path
    so the ingest stage has a valid DB and a writable FIFTY_ONE_ROOT.
    """
    from src.data import ecmwf_open_data as _mod

    if monkeypatch is not None and tmp_path is not None:
        fifty_one = tmp_path / "51 source data"
        monkeypatch.setattr(_mod, "FIFTY_ONE_ROOT", fifty_one)

    return collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=date(2026, 5, 8),
        run_hour=0,
        _runner=runner_fn,
        skip_extract=True,
        conn=conn,
        now_utc=datetime(2026, 5, 8, 9, 0, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# (b) retry tests
# ---------------------------------------------------------------------------

@_SUBPROCESS_SUPERSEDED
def test_subprocess_retry_succeeds_on_second_attempt(tmp_path, monkeypatch) -> None:
    """rc=1 on attempt 1, rc=0 on attempt 2 → result ok (not download_failed)."""
    call_count = 0

    def runner(args, *, label: str, timeout: int) -> dict:
        nonlocal call_count
        call_count += 1
        if label.startswith("download") and call_count == 1:
            return _fail_result(label, stderr="connection reset by peer")
        return _ok_result(label)

    conn = _make_conn()
    with patch("src.data.ecmwf_open_data.time.sleep"):  # suppress real sleep
        result = _run_download_only(runner, conn=conn, monkeypatch=monkeypatch, tmp_path=tmp_path)

    # Should not be download_failed — retry succeeded.
    assert result["status"] != "download_failed", (
        f"Expected retry to succeed on attempt 2 but got status={result['status']!r}. "
        "Retry logic may not be looping correctly."
    )
    assert call_count == 2, f"Expected exactly 2 runner calls (1 fail + 1 success), got {call_count}"


@_SUBPROCESS_SUPERSEDED
def test_subprocess_retry_exhausts() -> None:
    """rc=1 on all 2 attempts → download_failed."""
    call_count = 0

    def runner(args, *, label: str, timeout: int) -> dict:
        nonlocal call_count
        call_count += 1
        if label.startswith("download"):
            return _fail_result(label, stderr="network unreachable")
        return _ok_result(label)

    with patch("src.data.ecmwf_open_data.time.sleep"):
        result = _run_download_only(runner)

    assert result["status"] == "download_failed", (
        f"Expected download_failed after 2 retries, got {result['status']!r}"
    )
    assert call_count == 2, (
        f"Expected exactly 2 runner calls (2 retries), got {call_count}"
    )


# ---------------------------------------------------------------------------
# (b) 404 classification tests
# ---------------------------------------------------------------------------

@_SUBPROCESS_SUPERSEDED
def test_skipped_not_released_on_grid_valid_404() -> None:
    """404 without 'No index entries' on a grid-valid step → SKIPPED_NOT_RELEASED (no retry)."""
    call_count = 0

    def runner(args, *, label: str, timeout: int) -> dict:
        nonlocal call_count
        call_count += 1
        if label.startswith("download"):
            return _404_result(label, no_index=False)
        return _ok_result(label)

    with patch("src.data.ecmwf_open_data.time.sleep"):
        result = _run_download_only(runner)

    assert result["status"] == "skipped_not_released", (
        f"Expected skipped_not_released for 404 on grid-valid step, got {result['status']!r}"
    )
    # Must NOT retry — 404 on grid-valid step is not transient.
    assert call_count == 1, (
        f"Expected exactly 1 call (no retry on grid-valid 404), got {call_count}"
    )


@_SUBPROCESS_SUPERSEDED
def test_no_index_entries_404_is_retried_not_skipped() -> None:
    """404 with 'No index entries' (off-grid step) → treated as retryable, not SKIPPED_NOT_RELEASED."""
    call_count = 0

    def runner(args, *, label: str, timeout: int) -> dict:
        nonlocal call_count
        call_count += 1
        if label.startswith("download"):
            return _404_result(label, no_index=True)
        return _ok_result(label)

    with patch("src.data.ecmwf_open_data.time.sleep"):
        result = _run_download_only(runner)

    # 'No index entries' is NOT classified as SKIPPED_NOT_RELEASED.
    assert result["status"] != "skipped_not_released", (
        "A 404 with 'No index entries' (off-grid step) must not be SKIPPED_NOT_RELEASED; "
        "it should be treated as a retryable failure."
    )
    # Both retries fire before exhaustion.
    assert call_count == 2, (
        f"Expected 2 retry calls for 'No index entries' 404, got {call_count}"
    )
    assert result["status"] == "download_failed"


# ---------------------------------------------------------------------------
# PYTHONSAFEPATH sibling-import antibody (2026-06-22)
# ---------------------------------------------------------------------------
# Root cause of a 12h forecast blackout: the forecast-live launchd plist sets
# PYTHONSAFEPATH=1, which (Python 3.11+) suppresses Python's default injection of
# the launched script's own directory into sys.path[0]. The extract subprocess
# (`python ".../51 source data/scripts/extract_open_ens_localday.py"`) then could
# not import its sibling module `tigge_local_calendar_day_common` →
# ModuleNotFoundError → ecmwf extraction rc=1 → bayes_precision_fusion capture
# failed → zero posteriors for 12h → stale belief → blind exits + stale entries.
# Fix: _run_subprocess must explicitly inject the script's dir onto the child
# PYTHONPATH so sibling imports resolve regardless of the parent's PYTHONSAFEPATH.

def test_run_subprocess_injects_script_dir_on_pythonpath(monkeypatch) -> None:
    """The launched .py script's own dir must be FIRST on the child PYTHONPATH."""
    import os
    from src.data import ecmwf_open_data as _mod

    captured: dict = {}

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeCompleted()

    monkeypatch.setattr(_mod.subprocess, "run", fake_run)

    script_dir = "/Users/leofitz/zeus-live-main/51 source data/scripts"
    script = f"{script_dir}/extract_open_ens_localday.py"
    _mod._run_subprocess(
        [sys.executable, script, "--track", "mx2t6_high"],
        label="extract_antibody",
        timeout=5,
    )

    env = captured.get("env")
    assert env is not None, (
        "_run_subprocess must pass an explicit env to subprocess.run so the "
        "child PYTHONPATH can carry the script dir under PYTHONSAFEPATH=1."
    )
    pp = env.get("PYTHONPATH", "")
    first = pp.split(os.pathsep)[0] if pp else ""
    assert os.path.normpath(first) == os.path.normpath(script_dir), (
        "The launched script's own directory must be FIRST on the child "
        f"PYTHONPATH so sibling imports resolve under PYTHONSAFEPATH=1. Got {pp!r}."
    )


def test_run_subprocess_preserves_existing_pythonpath(monkeypatch) -> None:
    """Injecting the script dir must PREPEND, not clobber, an existing PYTHONPATH."""
    import os
    from src.data import ecmwf_open_data as _mod

    captured: dict = {}

    class _FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeCompleted()

    monkeypatch.setenv("PYTHONPATH", "/Users/leofitz/zeus-live-main")
    monkeypatch.setattr(_mod.subprocess, "run", fake_run)

    script_dir = "/Users/leofitz/zeus-live-main/51 source data/scripts"
    _mod._run_subprocess(
        [sys.executable, f"{script_dir}/extract_open_ens_localday.py"],
        label="extract_antibody2",
        timeout=5,
    )
    pp = captured["env"].get("PYTHONPATH", "")
    parts = [os.path.normpath(p) for p in pp.split(os.pathsep)]
    assert parts[0] == os.path.normpath(script_dir), f"script dir must be first; got {pp!r}"
    assert os.path.normpath("/Users/leofitz/zeus-live-main") in parts, (
        f"existing PYTHONPATH entry must be preserved; got {pp!r}"
    )
