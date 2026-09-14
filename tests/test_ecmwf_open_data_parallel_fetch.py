# Created: 2026-05-11
# Last reused/audited: 2026-08-14
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
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
    from src.state.db import init_schema_forecasts

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_forecasts(conn)
    return conn


def _make_fake_grib(path: Path) -> None:
    """Write a minimal non-empty placeholder so concat has bytes to read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 64)


def _runner_skip_extract_ingest(args, *, label: str, timeout: int) -> dict:
    """Runner that succeeds for extract; ingest is never reached (skip_extract=True)."""
    return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}


def _make_paths(mod, tmp_path: Path):
    root = tmp_path / "51_source_data"
    extract_script = root / "scripts" / "extract_open_ens_localday.py"
    manifest_path = root / "docs" / "tigge_city_coordinate_manifest_full_latest.json"
    extract_script.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    extract_script.write_text("# test extractor\n")
    manifest_path.write_text("{}")
    return mod.OpenDataPaths(
        raw_root=root,
        asset_root=root,
        extract_script=extract_script,
        manifest_path=manifest_path,
        origin="test",
    )


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
        _paths=_make_paths(mod, tmp_path),
        now_utc=NOW_UTC,
    )


# ---------------------------------------------------------------------------
# test 1: all steps OK → SUCCESS / extract fires
# ---------------------------------------------------------------------------

def test_all_ok_returns_SUCCESS_COMPLETE(tmp_path, monkeypatch):
    """All steps return OK → status reflects successful download; extract IS invoked."""
    import src.data.ecmwf_open_data as mod

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        f = mod._step_cache_path(
            output_dir,
            run_date=cycle_date,
            run_hour=cycle_hour,
            step=step,
            param=param,
        )
        _make_fake_grib(f)
        return ("OK", f)

    # skip_extract=False so we can observe whether runner(extract_…) is called.
    extract_called: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "extract" in label:
            extract_called.append(label)
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        _paths=_make_paths(mod, tmp_path),
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
        f = mod._step_cache_path(
            output_dir,
            run_date=cycle_date,
            run_hour=cycle_hour,
            step=step,
            param=param,
        )
        _make_fake_grib(f)
        return ("OK", f)

    extract_called: list[str] = []

    def runner(args, *, label: str, timeout: int) -> dict:
        if "extract" in label:
            extract_called.append(label)
        return {"label": label, "ok": True, "returncode": 0, "stdout_tail": "", "stderr_tail": ""}

    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        _paths=_make_paths(mod, tmp_path),
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

    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        _paths=_make_paths(mod, tmp_path),
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

    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])

    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=runner,
        _paths=_make_paths(mod, tmp_path),
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
    from src.data.ecmwf_open_data import _fetch_one_step, _step_cache_path

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    step = 3
    param = "mx2t3"
    canonical = _step_cache_path(
        output_dir,
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        step=step,
        param=param,
    )
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


def test_step_cache_identity_includes_cycle_hour(tmp_path, monkeypatch):
    """A later same-date source cycle must not reuse an earlier cycle step file."""

    import sys
    import types

    import src.data.ecmwf_open_data as mod

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    step = 3
    param = "mx2t3"
    prior_cycle_file = mod._step_cache_path(
        output_dir,
        run_date=RUN_DATE,
        run_hour=0,
        step=step,
        param=param,
    )
    prior_cycle_file.write_bytes(b"00Z")
    later_cycle_file = mod._step_cache_path(
        output_dir,
        run_date=RUN_DATE,
        run_hour=12,
        step=step,
        param=param,
    )
    assert later_cycle_file != prior_cycle_file

    class _FakeClient:
        def __init__(self, source=None):
            self.source = source

    fake_opendata = types.ModuleType("ecmwf.opendata")
    fake_opendata.Client = _FakeClient
    fake_ecmwf = types.ModuleType("ecmwf")
    fake_ecmwf.opendata = fake_opendata

    def fake_retrieve(_client, **kwargs):
        target = Path(kwargs["target"])
        target.write_bytes(b"PF" if kwargs["type"] == ["pf"] else b"CF")
        return SimpleNamespace(size=target.stat().st_size)

    orig_ecmwf = sys.modules.get("ecmwf")
    orig_opendata = sys.modules.get("ecmwf.opendata")
    sys.modules["ecmwf"] = fake_ecmwf
    sys.modules["ecmwf.opendata"] = fake_opendata
    monkeypatch.setattr(mod, "_retrieve_step_with_controlled_ranges", fake_retrieve)
    try:
        status, detail = mod._fetch_one_step(
            cycle_date=RUN_DATE,
            cycle_hour=12,
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

    assert status == "OK"
    assert detail == later_cycle_file
    assert later_cycle_file.read_bytes() == b"CFPF"
    assert prior_cycle_file.read_bytes() == b"00Z"


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
    partial = output_dir / (
        f".{RUN_DATE.strftime('%Y%m%d')}_{RUN_HOUR:02d}z_"
        f"step{step:03d}_{param}_ens51.grib2.partial"
    )
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


def test_fetch_one_step_does_not_sleep_after_final_retry(tmp_path, monkeypatch):
    """Retryable HTTP errors sleep only between attempts, not after exhaustion."""

    import sys
    import types

    import requests as req_mod
    import src.data.ecmwf_open_data as mod

    class _FakeHTTPError(req_mod.HTTPError):
        def __init__(self):
            resp = type("R", (), {"status_code": 503})()
            super().__init__(response=resp)

    class _FakeClient:
        def __init__(self, source=None):
            self.source = source

    fake_opendata = types.ModuleType("ecmwf.opendata")
    fake_opendata.Client = _FakeClient
    fake_ecmwf = types.ModuleType("ecmwf")
    fake_ecmwf.opendata = fake_opendata
    sleeps: list[float] = []

    def fake_retrieve(_client, **_kwargs):
        raise _FakeHTTPError()

    orig_ecmwf = sys.modules.get("ecmwf")
    orig_opendata = sys.modules.get("ecmwf.opendata")
    sys.modules["ecmwf"] = fake_ecmwf
    sys.modules["ecmwf.opendata"] = fake_opendata
    monkeypatch.setattr(mod, "_retrieve_step_with_controlled_ranges", fake_retrieve)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: sleeps.append(float(seconds)))
    try:
        status, detail = mod._fetch_one_step(
            cycle_date=RUN_DATE,
            cycle_hour=RUN_HOUR,
            param="mx2t3",
            step=3,
            output_dir=tmp_path,
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

    assert status == "FAILED"
    assert str(detail).startswith("HTTP_503_mirror_aws_attempt_")
    assert len(sleeps) == mod._PER_STEP_MAX_RETRIES - 1


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
    from src.data.ecmwf_open_data import _concat_steps, _step_cache_path

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    param = "mx2t3"

    # Create step files with distinctive byte payloads so we can detect order
    step_bytes = {9: b"STEP9", 3: b"STEP3", 6: b"STEP6"}
    for step, payload in step_bytes.items():
        f = _step_cache_path(
            output_dir,
            run_date=RUN_DATE,
            run_hour=RUN_HOUR,
            step=step,
            param=param,
        )
        f.write_bytes(payload)

    out = tmp_path / "merged.grib2"
    _concat_steps(
        [9, 3, 6],
        param,
        output_dir,
        out,
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
    )  # pass steps in non-ascending order

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
        f = mod._step_cache_path(
            output_dir,
            run_date=cycle_date,
            run_hour=cycle_hour,
            step=step,
            param=param,
        )
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


def test_batch_does_not_abandon_worker_at_duplicate_outer_timeout(tmp_path, monkeypatch):
    """The batch consumes the bounded worker result; it never leaves live HTTP behind."""
    import src.data.ecmwf_open_data as mod

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        time.sleep(0.02)
        path = mod._step_cache_path(
            output_dir,
            run_date=cycle_date,
            run_hour=cycle_hour,
            step=step,
            param=param,
        )
        _make_fake_grib(path)
        return ("OK", path)

    monkeypatch.setattr(mod, "STEP_HOURS", [3])
    monkeypatch.setattr(mod, "_PER_STEP_TIMEOUT_SECONDS", 0.001)
    result = mod.collect_open_ens_cycle(
        track="mx2t6_high",
        run_date=RUN_DATE,
        run_hour=RUN_HOUR,
        skip_extract=False,
        conn=_make_conn(),
        _fetch_impl=fetch_impl,
        _runner=_runner_skip_extract_ingest,
        _paths=_make_paths(mod, tmp_path),
        now_utc=NOW_UTC,
    )

    download = next(stage for stage in result["stages"] if "download_parallel" in stage["label"])
    assert download["status"] == "SUCCESS"
    assert download["ok_steps"] == [3]


@pytest.mark.parametrize("track", ["mx2t6_high", "mn2t6_low"])
@pytest.mark.parametrize("middle_raises", [False, True])
def test_free_download_slot_advances_while_first_step_waits(
    tmp_path, monkeypatch, track, middle_raises
):
    """A slow sibling cannot hold a free slot; failures still drain all steps."""
    import src.data.ecmwf_open_data as mod

    first_started = threading.Event()
    third_started = threading.Event()
    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "advanced": False}
    attempted = []

    def fetch_impl(*, cycle_date, cycle_hour, param, step, output_dir, mirrors):
        with lock:
            attempted.append(step)
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            if step == 3:
                first_started.set()
                state["advanced"] = third_started.wait(timeout=2)
            elif step == 6:
                assert first_started.wait(timeout=2)
                if middle_raises:
                    raise RuntimeError("failed middle step")
            else:
                third_started.set()
            path = mod._step_cache_path(
                output_dir, run_date=cycle_date, run_hour=cycle_hour,
                step=step, param=param,
            )
            _make_fake_grib(path)
            return "OK", path
        finally:
            with lock:
                state["active"] -= 1

    monkeypatch.setattr(mod, "STEP_HOURS", [3, 6, 9])
    monkeypatch.setattr(mod, "_DOWNLOAD_MAX_WORKERS", 2)
    conn = _make_conn()
    try:
        result = mod.collect_open_ens_cycle(
            track=track, run_date=RUN_DATE, run_hour=RUN_HOUR,
            skip_extract=True, conn=conn, _fetch_impl=fetch_impl,
            _runner=_runner_skip_extract_ingest,
            _paths=_make_paths(mod, tmp_path), now_utc=NOW_UTC,
        )
    finally:
        conn.close()

    assert state["advanced"], "next step waited for the stalled wave to finish"
    assert sorted(attempted) == [3, 6, 9]
    assert state["active"] == 0
    assert state["peak"] == 2
    download = next(s for s in result["stages"] if "download_parallel" in s["label"])
    assert download["ok_steps"] == ([3, 9] if middle_raises else [3, 6, 9])
    if middle_raises:
        assert result["status"] == "download_failed"
        assert download["failed_steps"] == [6]


def test_fetch_one_step_total_deadline_stops_retry_and_mirror_fanout(tmp_path, monkeypatch):
    """One step deadline bounds the whole operation, not each nested request."""
    import sys
    import types

    import src.data.ecmwf_open_data as mod

    class _FakeClient:
        def __init__(self, source=None):
            self.source = source

    fake_opendata = types.ModuleType("ecmwf.opendata")
    fake_opendata.Client = _FakeClient
    fake_ecmwf = types.ModuleType("ecmwf")
    fake_ecmwf.opendata = fake_opendata
    calls: list[float] = []

    def fake_retrieve(_client, *, _deadline=None, **_kwargs):
        calls.append(float(_deadline))
        time.sleep(0.02)
        raise mod.requests.Timeout("slow request")

    orig_ecmwf = sys.modules.get("ecmwf")
    orig_opendata = sys.modules.get("ecmwf.opendata")
    sys.modules["ecmwf"] = fake_ecmwf
    sys.modules["ecmwf.opendata"] = fake_opendata
    monkeypatch.setattr(mod, "_retrieve_step_with_controlled_ranges", fake_retrieve)
    monkeypatch.setattr(mod, "_PER_STEP_TIMEOUT_SECONDS", 0.001)
    try:
        status, detail = mod._fetch_one_step(
            cycle_date=RUN_DATE,
            cycle_hour=RUN_HOUR,
            param="mx2t3",
            step=3,
            output_dir=tmp_path,
            mirrors=("aws", "google"),
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

    assert (status, detail) == ("FAILED", "STEP_DEADLINE_EXCEEDED")
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# test 9: controlled indexed range downloader does not delegate to multiurl
# ---------------------------------------------------------------------------

def test_fetch_one_step_merges_oper_control_with_enfo_perturbed(tmp_path, monkeypatch):
    """One executable step is 1 oper/fc control + 50 enfo/pf members.

    Relationship under test: current ECMWF Open Data ``enfo/ef`` indexes can
    omit the ``cf`` control field for mx2t3/mn2t3 while publishing all 50
    perturbations. The live daemon must fetch the control field from
    ``oper/fc`` and merge it ahead of the perturbations instead of emitting a
    pf-only step that later fails the 51-member contract.
    """
    import sys
    import types

    import src.data.ecmwf_open_data as mod

    class _FakeClient:
        def __init__(self, source=None):
            self.source = source

    fake_opendata = types.ModuleType("ecmwf.opendata")
    fake_opendata.Client = _FakeClient
    fake_ecmwf = types.ModuleType("ecmwf")
    fake_ecmwf.opendata = fake_opendata

    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_retrieve(client, **kwargs):
        stream = str(kwargs["stream"])
        types_arg = tuple(kwargs["type"])
        calls.append((stream, types_arg))
        target = Path(kwargs["target"])
        if stream == "enfo" and types_arg == ("cf",):
            raise ValueError("Cannot find index entries matching {'type': ['cf']}")
        target.write_bytes(b"PF" if types_arg == ("pf",) else b"CF")
        return SimpleNamespace(size=target.stat().st_size)

    orig_ecmwf = sys.modules.get("ecmwf")
    orig_opendata = sys.modules.get("ecmwf.opendata")
    sys.modules["ecmwf"] = fake_ecmwf
    sys.modules["ecmwf.opendata"] = fake_opendata
    monkeypatch.setattr(mod, "_retrieve_step_with_controlled_ranges", fake_retrieve)
    try:
        status, detail = mod._fetch_one_step(
            cycle_date=RUN_DATE,
            cycle_hour=RUN_HOUR,
            param="mx2t3",
            step=3,
            output_dir=tmp_path,
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

    assert status == "OK"
    assert Path(detail).read_bytes() == b"CFPF"
    assert calls == [("enfo", ("pf",)), ("enfo", ("cf",)), ("oper", ("fc",))]


def test_controlled_range_downloader_writes_single_ranges_in_index_order(tmp_path, monkeypatch):
    """Indexed OpenData parts are fetched with one Range GET per index part.

    Relationship under test: ecmwf-opendata may resolve the index, but Zeus owns
    the HTTP transfer boundary. This prevents multiurl's internal multi-range
    requests and 120-second retry loop from becoming the live daemon's behavior.
    """
    import src.data.ecmwf_open_data as mod

    calls: list[dict[str, Any]] = []

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            payload: bytes,
            *,
            lines: list[bytes] | None = None,
            headers: dict[str, str] | None = None,
        ):
            self.status_code = status_code
            self._payload = payload
            self._lines = lines or []
            self.headers = headers or {}

        def iter_content(self, chunk_size: int):
            yield self._payload

        def iter_lines(self):
            yield from self._lines

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected HTTP error {self.status_code}")

        def close(self):
            pass

    class _FakeSession:
        def get(self, url, *, stream, headers=None, timeout=None, verify=None):
            calls.append(
                {
                    "url": url,
                    "stream": stream,
                    "headers": dict(headers or {}),
                    "timeout": timeout,
                    "verify": verify,
                }
            )
            if str(url).endswith(".index"):
                lines = [
                    b'{"type":"pf","step":3,"param":"mx2t3","_offset":10,"_length":3}',
                    b'{"type":"pf","step":3,"param":"mx2t3","_offset":20,"_length":2}',
                    b'{"type":"pf","step":6,"param":"mx2t3","_offset":99,"_length":1}',
                ]
                return _FakeResponse(200, b"", lines=lines)
            payload = {
                "bytes=10-12": b"abc",
                "bytes=20-21": b"de",
            }[headers["Range"]]
            start, end = headers["Range"].removeprefix("bytes=").split("-")
            return _FakeResponse(
                206,
                payload,
                headers={
                    "Content-Range": f"bytes {start}-{end}/100",
                    "Content-Length": str(len(payload)),
                    "ETag": "index-order-entity",
                },
            )

    class _FakeClient:
        verify = False

        def _get_urls(self, **kwargs):
            assert kwargs["use_index"] is False
            assert kwargs["target"].endswith(".partial")
            return SimpleNamespace(
                urls=["https://example.invalid/step.grib2"],
                target=kwargs["target"],
                for_index={"type": ["cf", "pf"], "step": [3], "param": ["mx2t3"]},
            )

    monkeypatch.setattr(mod, "_RateLimitedSession", _FakeSession)

    target = tmp_path / "step.grib2.partial"
    result = mod._retrieve_step_with_controlled_ranges(
        _FakeClient(),
        target=target,
        date=20260515,
        time=0,
        stream="enfo",
        type=["cf", "pf"],
        step=[3],
        param=["mx2t3"],
    )

    assert target.read_bytes() == b"abcde"
    assert result.size == 5
    assert calls[0]["url"] == "https://example.invalid/step.index"
    assert calls[0]["headers"] == {}
    assert [call["headers"].get("Range") for call in calls[1:]] == ["bytes=10-12", "bytes=20-21"]
    assert all(call["stream"] is True for call in calls)
    assert all(call["verify"] is False for call in calls)


def test_controlled_range_downloader_resumes_only_verified_missing_range(tmp_path, monkeypatch):
    """An interrupted body resumes at the next fully checkpointed range only."""
    import json

    import src.data.ecmwf_open_data as mod

    phase = 1
    calls: list[tuple[str, str | None]] = []

    class _Response:
        def __init__(self, status_code: int, payload: bytes, headers: dict[str, str]):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers

        def iter_content(self, chunk_size: int):
            yield self._payload

        def iter_lines(self):
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":0,"_length":6}'

        def raise_for_status(self):
            raise AssertionError("unexpected HTTP error")

        def close(self):
            pass

    def _range_response(range_header: str, payload: bytes) -> _Response:
        start, end = range_header.removeprefix("bytes=").split("-")
        return _Response(
            206,
            payload,
            {
                "Content-Range": f"bytes {start}-{end}/6",
                "Content-Length": str(int(end) - int(start) + 1),
                "ETag": "immutable-entity-v1",
            },
        )

    class _Session:
        def get(self, url, *, stream, headers=None, timeout=None, verify=None):
            range_header = (headers or {}).get("Range")
            calls.append(("GET", range_header))
            if str(url).endswith(".index"):
                return _Response(200, b"", {"ETag": "index-v1"})
            payload = {
                "bytes=0-1": b"ab",
                "bytes=2-3": b"cd",
                "bytes=4-5": b"e" if phase == 1 else b"ef",
            }[range_header]
            return _range_response(range_header, payload)

        def head(self, url, *, timeout=None, verify=None):
            calls.append(("HEAD", None))
            return _Response(200, b"", {"ETag": "immutable-entity-v1"})

    class _Client:
        verify = True

        def _get_urls(self, **kwargs):
            return SimpleNamespace(
                urls=["https://example.invalid/step.grib2"],
                target=kwargs["target"],
                for_index={"type": ["pf"], "step": [3], "param": ["mx2t3"]},
            )

    monkeypatch.setattr(mod, "_RateLimitedSession", _Session)
    monkeypatch.setattr(mod, "_RANGE_RESUME_CHUNK_BYTES", 2)
    target = tmp_path / "step.pf.partial"
    with pytest.raises(Exception, match="body length 1 does not match 2"):
        mod._retrieve_step_with_controlled_ranges(
            _Client(), target=target, date=20260515, time=0,
            stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
        )

    sidecar = json.loads(mod._range_resume_manifest_path(target).read_text())
    assert sidecar["completed_ranges"] == 2
    assert target.read_bytes() == b"abcde"  # trailing short body is not checkpointed

    phase = 2
    retry_start = len(calls)
    result = mod._retrieve_step_with_controlled_ranges(
        _Client(), target=target, date=20260515, time=0,
        stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
    )

    assert target.read_bytes() == b"abcdef"
    assert result.size == 6
    assert calls[retry_start:] == [("GET", None), ("HEAD", None), ("GET", "bytes=4-5")]


def test_controlled_range_downloader_completes_fresh_transfer_after_strong_then_missing_etag(
    tmp_path, monkeypatch
):
    """A later missing ETag disables checkpointing without aborting fresh bytes."""
    import src.data.ecmwf_open_data as mod

    class _Response:
        def __init__(self, status_code: int, payload: bytes, headers: dict[str, str]):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers

        def iter_content(self, chunk_size: int):
            yield self._payload

        def iter_lines(self):
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":0,"_length":2}'
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":2,"_length":2}'

        def raise_for_status(self):
            raise AssertionError("unexpected HTTP error")

        def close(self):
            pass

    class _Session:
        def get(self, url, *, stream, headers=None, timeout=None, verify=None):
            if str(url).endswith(".index"):
                return _Response(200, b"", {})
            range_header = headers["Range"]
            start, end = range_header.removeprefix("bytes=").split("-")
            response_headers = {
                "Content-Range": f"bytes {start}-{end}/4",
                "Content-Length": "2",
            }
            if range_header == "bytes=0-1":
                response_headers["ETag"] = '"strong-v1"'
            return _Response(
                206,
                {"bytes=0-1": b"ab", "bytes=2-3": b"cd"}[range_header],
                response_headers,
            )

    class _Client:
        verify = True

        def _get_urls(self, **kwargs):
            return SimpleNamespace(
                urls=["https://example.invalid/step.grib2"],
                target=kwargs["target"],
                for_index={"type": ["pf"], "step": [3], "param": ["mx2t3"]},
            )

    monkeypatch.setattr(mod, "_RateLimitedSession", _Session)
    target = tmp_path / "step.pf.partial"
    result = mod._retrieve_step_with_controlled_ranges(
        _Client(), target=target, date=20260515, time=0,
        stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
    )

    assert target.read_bytes() == b"abcd"
    assert result.size == 4
    assert not mod._range_resume_manifest_path(target).exists()


def test_controlled_range_downloader_drops_resume_cache_when_head_is_unsupported(tmp_path):
    """HEAD 405 restarts safely instead of making a cached partial unrecoverable."""
    import src.data.ecmwf_open_data as mod

    target = tmp_path / "step.pf.partial"
    target.write_bytes(b"abc")
    plan = (("https://example.invalid/step.grib2", 0, 3),)
    mod._write_range_resume_manifest(
        target, plan=plan, completed_ranges=1, entity_tags={plan[0][0]: "entity-v1"}
    )

    class _Response:
        status_code = 405
        headers: dict[str, str] = {}

        def close(self):
            pass

    class _Session:
        def head(self, url, *, timeout=None, verify=None):
            return _Response()

    completed, tags = mod._load_range_resume(
        target, plan=plan, session=_Session(), verify=True, deadline=None
    )

    assert (completed, tags) == (0, {})
    assert not target.exists()
    assert not mod._range_resume_manifest_path(target).exists()


def test_controlled_range_downloader_restarts_after_resumed_prefix_loses_etag(tmp_path, monkeypatch):
    """A resumed prefix plus missing ETag retries from byte zero, not forever."""
    import requests

    import src.data.ecmwf_open_data as mod

    target = tmp_path / "step.pf.partial"
    target.write_bytes(b"ab")
    plan = (
        ("https://example.invalid/step.grib2", 0, 2),
        ("https://example.invalid/step.grib2", 2, 2),
    )
    mod._write_range_resume_manifest(
        target, plan=plan, completed_ranges=1, entity_tags={plan[0][0]: '"strong-v1"'}
    )
    phase = 1
    range_calls: list[str] = []

    class _Response:
        def __init__(self, status_code: int, payload: bytes, headers: dict[str, str]):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers

        def iter_content(self, chunk_size: int):
            yield self._payload

        def iter_lines(self):
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":0,"_length":2}'
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":2,"_length":2}'

        def close(self):
            pass

    class _Session:
        def get(self, url, *, stream, headers=None, timeout=None, verify=None):
            range_header = (headers or {}).get("Range")
            if str(url).endswith(".index"):
                return _Response(200, b"", {})
            range_calls.append(range_header)
            start, end = range_header.removeprefix("bytes=").split("-")
            response_headers = {
                "Content-Range": f"bytes {start}-{end}/4",
                "Content-Length": "2",
            }
            if phase == 2:
                response_headers["ETag"] = '"strong-v2"'
            return _Response(
                206,
                {"bytes=0-1": b"ab", "bytes=2-3": b"cd"}[range_header],
                response_headers,
            )

        def head(self, url, *, timeout=None, verify=None):
            return _Response(200, b"", {"ETag": '"strong-v1"'})

    class _Client:
        verify = True

        def _get_urls(self, **kwargs):
            return SimpleNamespace(
                urls=[plan[0][0]],
                target=kwargs["target"],
                for_index={"type": ["pf"], "step": [3], "param": ["mx2t3"]},
            )

    monkeypatch.setattr(mod, "_RateLimitedSession", _Session)
    with pytest.raises(requests.ConnectionError, match="lacks a strong ETag"):
        mod._retrieve_step_with_controlled_ranges(
            _Client(), target=target, date=20260515, time=0,
            stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
        )
    assert not mod._range_resume_manifest_path(target).exists()

    phase = 2
    retry_start = len(range_calls)
    mod._retrieve_step_with_controlled_ranges(
        _Client(), target=target, date=20260515, time=0,
        stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
    )

    assert target.read_bytes() == b"abcd"
    assert range_calls[retry_start:] == ["bytes=0-1", "bytes=2-3"]


def test_controlled_range_downloader_does_not_reuse_weak_etag_prefix(tmp_path, monkeypatch):
    """An interrupted old weak-ETag checkpoint restarts from its first range."""
    import src.data.ecmwf_open_data as mod

    target = tmp_path / "step.pf.partial"
    target.write_bytes(b"ab")
    plan = (
        ("https://example.invalid/step.grib2", 0, 2),
        ("https://example.invalid/step.grib2", 2, 2),
    )
    mod._write_range_resume_manifest(
        target, plan=plan, completed_ranges=1, entity_tags={plan[0][0]: 'W/"v1"'}
    )
    range_calls: list[str] = []

    class _Response:
        def __init__(self, status_code: int, payload: bytes, headers: dict[str, str]):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers

        def iter_content(self, chunk_size: int):
            yield self._payload

        def iter_lines(self):
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":0,"_length":2}'
            yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":2,"_length":2}'

        def close(self):
            pass

    class _Session:
        def get(self, url, *, stream, headers=None, timeout=None, verify=None):
            range_header = (headers or {}).get("Range")
            if str(url).endswith(".index"):
                return _Response(200, b"", {})
            range_calls.append(range_header)
            start, end = range_header.removeprefix("bytes=").split("-")
            return _Response(
                206,
                {"bytes=0-1": b"ab", "bytes=2-3": b"cd"}[range_header],
                {
                    "Content-Range": f"bytes {start}-{end}/4",
                    "Content-Length": "2",
                    "ETag": '"strong-v2"',
                },
            )

        def head(self, url, *, timeout=None, verify=None):
            raise AssertionError("weak sidecar must be rejected before HEAD validation")

    class _Client:
        verify = True

        def _get_urls(self, **kwargs):
            return SimpleNamespace(
                urls=[plan[0][0]],
                target=kwargs["target"],
                for_index={"type": ["pf"], "step": [3], "param": ["mx2t3"]},
            )

    monkeypatch.setattr(mod, "_RateLimitedSession", _Session)
    mod._retrieve_step_with_controlled_ranges(
        _Client(), target=target, date=20260515, time=0,
        stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
    )

    assert target.read_bytes() == b"abcd"
    assert range_calls == ["bytes=0-1", "bytes=2-3"]


def test_controlled_range_downloader_discards_prefix_when_index_ranges_change(tmp_path, monkeypatch):
    """A new index range identity cannot be assembled with an old partial prefix."""
    import src.data.ecmwf_open_data as mod

    phase = 1
    range_calls: list[str] = []

    class _Response:
        def __init__(self, status_code: int, payload: bytes, headers: dict[str, str]):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers

        def iter_content(self, chunk_size: int):
            yield self._payload

        def iter_lines(self):
            if phase == 1:
                yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":0,"_length":3}'
                yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":3,"_length":3}'
            else:
                yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":0,"_length":2}'
                yield b'{"type":"pf","step":3,"param":"mx2t3","_offset":2,"_length":4}'

        def raise_for_status(self):
            raise AssertionError("unexpected HTTP error")

        def close(self):
            pass

    class _Session:
        def get(self, url, *, stream, headers=None, timeout=None, verify=None):
            range_header = (headers or {}).get("Range")
            if str(url).endswith(".index"):
                return _Response(200, b"", {"ETag": "index"})
            range_calls.append(range_header)
            payload = {
                "bytes=0-2": b"abc",
                "bytes=3-5": b"x" if phase == 1 else b"unused",
                "bytes=0-1": b"XY",
                "bytes=2-5": b"Z123",
            }[range_header]
            start, end = range_header.removeprefix("bytes=").split("-")
            return _Response(
                206,
                payload,
                {
                    "Content-Range": f"bytes {start}-{end}/6",
                    "Content-Length": str(int(end) - int(start) + 1),
                    "ETag": "entity-v1",
                },
            )

        def head(self, url, *, timeout=None, verify=None):
            raise AssertionError("changed index must invalidate before entity reuse")

    class _Client:
        verify = True

        def _get_urls(self, **kwargs):
            return SimpleNamespace(
                urls=["https://example.invalid/step.grib2"],
                target=kwargs["target"],
                for_index={"type": ["pf"], "step": [3], "param": ["mx2t3"]},
            )

    monkeypatch.setattr(mod, "_RateLimitedSession", _Session)
    target = tmp_path / "step.pf.partial"
    with pytest.raises(Exception, match="body length 1 does not match 3"):
        mod._retrieve_step_with_controlled_ranges(
            _Client(), target=target, date=20260515, time=0,
            stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
        )

    phase = 2
    retry_start = len(range_calls)
    mod._retrieve_step_with_controlled_ranges(
        _Client(), target=target, date=20260515, time=0,
        stream="enfo", type=["pf"], step=[3], param=["mx2t3"],
    )

    assert target.read_bytes() == b"XYZ123"
    assert range_calls[retry_start:] == ["bytes=0-1", "bytes=2-5"]


# ---------------------------------------------------------------------------
# test 10: token bucket rate limiter (D1 throttle antibody)
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
    from src.data.ecmwf_open_data import _DOWNLOAD_BURST, _DOWNLOAD_RPS, _fetch_bucket

    assert _DOWNLOAD_RPS == 4.0, (
        f"Default RPS must be 4.0 (D1 throttle), got {_DOWNLOAD_RPS}"
    )
    assert isinstance(_fetch_bucket._rate, float), "Bucket rate must be float"
    assert _fetch_bucket._rate == _DOWNLOAD_RPS, (
        f"Bucket rate {_fetch_bucket._rate} != _DOWNLOAD_RPS {_DOWNLOAD_RPS}"
    )
    assert _fetch_bucket._capacity == _DOWNLOAD_BURST == 4.0


def test_token_bucket_burst_and_aimd_feedback():
    """Burst stays bounded while 503 halves and successes recover the rate."""
    from src.data.ecmwf_open_data import _TokenBucket

    bucket = _TokenBucket(64.0, capacity=8.0)

    assert bucket._tokens == bucket._capacity == 8.0
    bucket.observe(503)
    assert bucket._rate == 32.0

    for _ in range(32):
        bucket.observe(200)
    assert 32.9 < bucket._rate < 34.0

    bucket.observe(429)
    assert 16.0 < bucket._rate < 17.0


def test_token_bucket_serializes_waiting_threads():
    """Concurrent waiters cannot all consume the same future token."""
    from concurrent.futures import ThreadPoolExecutor
    import time

    from src.data.ecmwf_open_data import _TokenBucket

    bucket = _TokenBucket(40.0, capacity=1.0)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: bucket.acquire(), range(4)))

    assert time.monotonic() - started >= 0.06
