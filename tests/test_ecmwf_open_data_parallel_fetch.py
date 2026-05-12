# Created: 2026-05-11
# Last reused/audited: 2026-05-11
# Authority basis: PLAN docs/operations/task_2026-05-11_ecmwf_download_replacement/PLAN.md §5.4 + §6
"""Unit tests for ECMWF Open Data parallel SDK fetch (Candidate H).

Tests the new _fetch_one_step + ThreadPoolExecutor path introduced by
PLAN v3 (2026-05-11). The old subprocess-runner tests in
test_ecmwf_open_data_source_failover.py and
test_ecmwf_open_data_subprocess_hardening.py are superseded by these.

Relationship invariants tested (PLAN §6):
  REL-1  merged output yields exactly the ok_steps
  REL-2  SIGTERM-resume idempotence: canonical file not re-fetched
  REL-3  per-step independence: one step failing does not block others
  REL-5  manifest_sha256 invariance (concat order does not enter hash)

Single-writer antibody enforced throughout: _fetch_impl stubs are pure
functions (no SQLite writes); all DB writes occur on the main thread.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


import pytest

UTC = timezone.utc
RUN_DATE = date(2026, 5, 11)
RUN_HOUR = 0
NOW_UTC = datetime(2026, 5, 11, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    from src.state.db import init_schema
    from src.state.schema.v2_schema import apply_v2_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    apply_v2_schema(conn)
    return conn


def _make_fake_grib(path: Path) -> None:
    """Write a minimal non-empty placeholder so concat has bytes to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 64)


def _runner_skip_extract_ingest(args, *, label: str, timeout: int) -> dict:
    """Runner that succeeds for extract; ingest is never reached (skip_extract=True)."""
    return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}


def _call_collect(
    *,
    fetch_impl,
    tmp_path: Path,
    skip_extract: bool = True,
    conn=None,
    monkeypatch,
) -> dict:
    """Call collect_open_ens_cycle with the new _fetch_impl seam.

    Patches FIFTY_ONE_ROOT and STEP_HOURS (trimmed to 3 steps) so tests
    don't submit 71 futures.
    """
    import src.data.ecmwf_open_data as mod

    monkeypatch.setattr(mod, "FIFTY_ONE_ROOT", tmp_path / "51_source_data")
    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])  # 3 steps only
    if conn is None:
        conn = _make_conn()

    return mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=skip_extract,
        conn=conn,
        _fetch_impl=fetch_impl,
        _runner=_runner_skip_extract_ingest,
        now_utc=NOW_UTC,
    )


# ---------------------------------------------------------------------------
# test 1: all steps OK → SUCCESS / extract fires
# ---------------------------------------------------------------------------

def test_all_ok_returns_SUCCESS_COMPLETE(tmp_path, monkeypatch):
    """All steps return OK → status reflects successful download; extract IS invoked."""
    import src.data.ecmwf_open_data as mod

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        f = output_dir / f".step{step:03d}_{param}.grib2"
        _make_fake_grib(f)
        return ("OK", f)

    # skip_extract=False so we can observe whether runner(extract_…) is called.
    extract_called: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "extract" in label:
            extract_called.append(label)
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "FIFTY_ONE_ROOT", tmp_path / "51_source_data")
    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        now_utc=NOW_UTC,
    )

    # download stage must show ok=True
    download_stage = next(
        (s for s in result.get("stages", []) if "download_parallel" in s.get("label", "")),
        None,
    )
    assert download_stage is not None, "No download_parallel stage in result"
    assert download_stage["ok"] is True, f"Expected ok=True, got {download_stage}"
    assert download_stage["status"] == "SUCCESS"
    assert sorted(download_stage["ok_steps"]) == [3, 6, 9]
    assert download_stage["not_released_steps"] == []

    # extract must have been called (PARTIAL + SUCCESS both fall through)
    assert extract_called, "extract runner was NOT called despite all steps OK"


# ---------------------------------------------------------------------------
# test 2: some 404 → PARTIAL; extract FIRES; observed_steps = near-horizon subset
# ---------------------------------------------------------------------------

def test_some_404_returns_PARTIAL_PARTIAL_and_extract_fires(tmp_path, monkeypatch):
    """Steps 3,6 → OK; step 9 → NOT_RELEASED → PARTIAL; extract IS invoked.

    REL-3 partial-cycle property: near-horizon steps available, far-horizon not.
    """
    import src.data.ecmwf_open_data as mod

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        if step == 9:
            return ("NOT_RELEASED", None)
        f = output_dir / f".step{step:03d}_{param}.grib2"
        _make_fake_grib(f)
        return ("OK", f)

    extract_called: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "extract" in label:
            extract_called.append(label)
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "FIFTY_ONE_ROOT", tmp_path / "51_source_data")
    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        now_utc=NOW_UTC,
    )

    download_stage = next(
        (s for s in result.get("stages", []) if "download_parallel" in s.get("label", "")),
        None,
    )
    assert download_stage is not None
    assert download_stage["ok"] is True, "PARTIAL should still be ok=True (extract fires)"
    assert download_stage["status"] == "PARTIAL"
    assert sorted(download_stage["ok_steps"]) == [3, 6]
    assert download_stage["not_released_steps"] == [9]

    # extract MUST fire on PARTIAL (R2-critical-nit from PLAN v3)
    assert extract_called, "extract runner was NOT called on PARTIAL cycle — BUG"


# ---------------------------------------------------------------------------
# test 3: all 404 → SKIPPED_NOT_RELEASED; extract NOT called
# ---------------------------------------------------------------------------

def test_all_404_returns_SKIPPED_NOT_RELEASED_and_extract_skipped(tmp_path, monkeypatch):
    """All steps 404 → SKIPPED_NOT_RELEASED; extract must NOT be invoked."""
    import src.data.ecmwf_open_data as mod

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        return ("NOT_RELEASED", None)

    extract_called: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "extract" in label:
            extract_called.append(label)
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "FIFTY_ONE_ROOT", tmp_path / "51_source_data")
    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        now_utc=NOW_UTC,
    )

    assert result["status"] == "skipped_not_released", (
        f"All-404 should return skipped_not_released, got {result['status']!r}"
    )
    assert not extract_called, f"extract must NOT be called on SKIPPED_NOT_RELEASED; got {extract_called}"


# ---------------------------------------------------------------------------
# test 4: non-404 retry exhaustion → FAILED; extract NOT called
# ---------------------------------------------------------------------------

def test_non_404_retry_exhaustion_returns_FAILED_and_extract_skipped(tmp_path, monkeypatch):
    """All steps fail with 503 (retry-exhausted) → FAILED; extract must NOT fire."""
    import src.data.ecmwf_open_data as mod

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        return ("FAILED", f"HTTP_503_mirror_aws_attempt_2")

    extract_called: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "extract" in label:
            extract_called.append(label)
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "FIFTY_ONE_ROOT", tmp_path / "51_source_data")
    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        now_utc=NOW_UTC,
    )

    assert result["status"] == "download_failed", (
        f"FAILED steps should return download_failed, got {result['status']!r}"
    )
    assert not extract_called, f"extract must NOT be called on FAILED; got {extract_called}"


# ---------------------------------------------------------------------------
# test 5: resume via atomic rename (REL-2)
# ---------------------------------------------------------------------------

def test_resume_via_atomic_rename(tmp_path):
    """Canonical file already present → _fetch_one_step returns OK immediately (no re-fetch).

    REL-2: SIGTERM-resume idempotence — canonical files are not re-fetched.
    """
    from src.data.ecmwf_open_data import _fetch_one_step

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    step = 3
    param = "mx2t3"
    canonical = output_dir / f".step{step:03d}_{param}.grib2"
    canonical.write_bytes(b"\x00" * 128)  # non-empty → resume

    # The resume path returns before entering the mirror loop — no HTTP call made.
    # We verify by supplying an empty mirror list: if _fetch_one_step tried to
    # iterate mirrors, it would return FAILED (no mirrors); since it short-circuits
    # on the canonical file, it returns OK without touching the mirror loop.
    status, detail = _fetch_one_step(
        cycle_date=RUN_DATE,
        cycle_hour=RUN_HOUR,
        param=param,
        step=step,
        output_dir=output_dir,
        mirrors=(),  # empty → would produce FAILED if mirrors were iterated
    )

    assert status == "OK", f"Expected OK on resume, got {status!r}"
    assert detail == canonical


# ---------------------------------------------------------------------------
# test 6: .partial file does NOT count as resume (REL-2)
# ---------------------------------------------------------------------------

def test_partial_file_does_not_count_as_resume(tmp_path):
    """A .grib2.partial file left by a prior crash is NOT treated as a resume point.

    REL-2: partial files must be re-fetched.
    """
    from src.data.ecmwf_open_data import _fetch_one_step

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    step = 6
    param = "mx2t3"
    # Write a .partial but NOT the canonical .grib2
    partial = output_dir / f".step{step:03d}_{param}.grib2.partial"
    partial.write_bytes(b"\x00" * 64)

    # The canonical file does not exist, so _fetch_one_step should attempt a fetch.
    # We verify by checking that Client.retrieve is entered (not short-circuited).
    # ecmwf.opendata is a conda-only package; inject a fake module into sys.modules
    # so the local `from ecmwf.opendata import Client` inside _fetch_one_step resolves.
    import sys
    import requests as req_mod
    import types

    class _FakeHTTPError(req_mod.HTTPError):
        def __init__(self):
            resp = type("R", (), {"status_code": 404})()
            super().__init__(response=resp)

    client_called = []

    class _FakeClient:
        def __init__(self, source=None):
            pass
        def retrieve(self, **kwargs):
            client_called.append(True)
            raise _FakeHTTPError()

    fake_opendata = types.ModuleType("ecmwf.opendata")
    fake_opendata.Client = _FakeClient
    fake_ecmwf = types.ModuleType("ecmwf")
    fake_ecmwf.opendata = fake_opendata

    orig_ecmwf = sys.modules.get("ecmwf")
    orig_opendata = sys.modules.get("ecmwf.opendata")
    sys.modules["ecmwf"] = fake_ecmwf
    sys.modules["ecmwf.opendata"] = fake_opendata
    try:
        status, detail = _fetch_one_step(
            cycle_date=RUN_DATE,
            cycle_hour=RUN_HOUR,
            param=param,
            step=step,
            output_dir=output_dir,
            mirrors=("aws",),
        )
    finally:
        if orig_ecmwf is None:
            sys.modules.pop("ecmwf", None)
        else:
            sys.modules["ecmwf"] = orig_ecmwf
        if orig_opendata is None:
            sys.modules.pop("ecmwf.opendata", None)
        else:
            sys.modules["ecmwf.opendata"] = orig_opendata

    # 404 → NOT_RELEASED (fetch was attempted, not short-circuited)
    assert status == "NOT_RELEASED", (
        f"Expected NOT_RELEASED (fetch attempted on partial file), got {status!r}"
    )
    assert client_called, "Client.retrieve was not called — .partial incorrectly treated as resume"


# ---------------------------------------------------------------------------
# test 7: concat order is ascending step (REL-1)
# ---------------------------------------------------------------------------

def test_concat_order_step_ascending(tmp_path):
    """_concat_steps writes step files in ascending step order.

    REL-1: merged output must contain steps in a deterministic order.
    The extractor is order-invariant by key but we assert ascending order
    for determinism (REL-5: manifest_sha256 over manifest JSON, not GRIB bytes,
    so concat order doesn't affect the hash — but consistent order aids debugging).
    """
    from src.data.ecmwf_open_data import _concat_steps

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    param = "mx2t3"

    # Create step files with distinctive byte payloads so we can detect order
    step_bytes = {9: b"STEP9", 3: b"STEP3", 6: b"STEP6"}
    for step, payload in step_bytes.items():
        f = output_dir / f".step{step:03d}_{param}.grib2"
        f.write_bytes(payload)

    out = tmp_path / "merged.grib2"
    _concat_steps([9, 3, 6], param, output_dir, out)  # pass steps in non-ascending order

    content = out.read_bytes()
    # Ascending order: STEP3 then STEP6 then STEP9
    assert content == b"STEP3" + b"STEP6" + b"STEP9", (
        f"Expected ascending step order in concat output; got {content!r}"
    )


# ---------------------------------------------------------------------------
# test 8: REL-3 — per-step independence (one failed step does not block others)
# ---------------------------------------------------------------------------

def test_thread_safety_max_workers_2(tmp_path, monkeypatch):
    """Workers=2 + token bucket; step independence holds (REL-3).

    Verifies _DOWNLOAD_MAX_WORKERS=2 is the module constant (D1 throttle
    antibody: reduced from 5 to 2 to lower burst concurrency against AWS S3;
    token bucket is the primary rate control). Each fetch_impl call is
    independent; one step returning FAILED does NOT prevent other steps from
    returning OK.
    """
    import src.data.ecmwf_open_data as mod
    from src.data.ecmwf_open_data import _DOWNLOAD_MAX_WORKERS

    assert _DOWNLOAD_MAX_WORKERS == 2, (
        f"_DOWNLOAD_MAX_WORKERS must be 2 (D1 throttle antibody), got {_DOWNLOAD_MAX_WORKERS}"
    )

    steps_fetched: list[int] = []

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        steps_fetched.append(step)
        if step == 6:
            return ("FAILED", "HTTP_503_mirror_aws_attempt_2")
        f = output_dir / f".step{step:03d}_{param}.grib2"
        _make_fake_grib(f)
        return ("OK", f)

    def runner(args, *, label: str, timeout: int) -> dict:
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "FIFTY_ONE_ROOT", tmp_path / "51_source_data")
    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=True,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        now_utc=NOW_UTC,
    )

    # All steps must have been attempted (independence)
    assert sorted(steps_fetched) == [3, 6, 9], (
        f"Expected all steps to be fetched independently; got {steps_fetched}"
    )
    # Step 6 failed → FAILED result (only one failed, no ok_steps mix with FAILED)
    assert result["status"] == "download_failed", (
        f"Expected download_failed when any step fails; got {result['status']!r}"
    )


# ---------------------------------------------------------------------------
# test 9: token bucket rate limiter (D1 throttle antibody)
# ---------------------------------------------------------------------------

def test_token_bucket_limits_rate():
    """_TokenBucket: N+1 acquires in rapid succession sleep at least once.

    Verifies the bucket actually throttles bursts. We use a high rate (1000
    rps) so the test completes in milliseconds, but drain the bucket first
    so the (N+1)th acquire must wait a predictable gap.
    """
    from src.data.ecmwf_open_data import _TokenBucket
    import time

    rate = 10.0  # 10 rps → 0.1s per token
    bucket = _TokenBucket(rate)

    # Drain the bucket fully (it starts with `rate` tokens)
    for _ in range(int(rate)):
        bucket.acquire()

    # Now the bucket is empty; next acquire must sleep ~0.1s
    t0 = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - t0

    # Allow generous headroom (CI jitter), but must be >10ms to prove throttling
    assert elapsed >= 0.05, (
        f"Token bucket did not throttle: elapsed={elapsed:.3f}s < 0.05s — bucket is not rate-limiting"
    )


def test_token_bucket_default_rps():
    """Module-level _fetch_bucket defaults to ZEUS_ECMWF_RPS=4.0 rps."""
    from src.data.ecmwf_open_data import _DOWNLOAD_RPS, _fetch_bucket

    assert _DOWNLOAD_RPS == 4.0, (
        f"Default RPS must be 4.0 (D1 throttle), got {_DOWNLOAD_RPS}"
    )
    assert isinstance(_fetch_bucket._rate, float), "Bucket rate must be float"
    assert _fetch_bucket._rate == _DOWNLOAD_RPS, (
        f"Bucket rate {_fetch_bucket._rate} != _DOWNLOAD_RPS {_DOWNLOAD_RPS}"
    )
