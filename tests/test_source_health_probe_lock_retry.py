# Created: 2026-06-14
# Last reused or audited: 2026-06-14
# Authority basis: day0-edge-lane revival 2026-06-14. The ingest all-source
#   health probe is the SOLE refresher of open_meteo_archive / wu_pws. It shares
#   the "source_health" advisory lock with the forecast-live OpenData partial
#   refresh, which runs on the SAME 10-min cadence. The pre-fix probe ABANDONED
#   its cycle on a single lock-held skip, so the contending forecast-live write
#   permanently starved the all-source probe -> open_meteo_archive / wu_pws never
#   advanced -> drifted > 6h stale -> the boot freshness gate disabled
#   DAY0_CAPTURE -> the entire settlement-day edge lane went dark (0 orders).
"""Relationship test (RED-on-revert): the ingest source_health probe RETRIES
through a transient lock-held instead of abandoning the cycle.

Cross-component invariant pinned across ingest_main (all-source probe) and
forecast_live_daemon (OpenData partial refresh) which share the same advisory
lock: a transient contention must NOT starve the source whose staleness gates
DAY0_CAPTURE. The contending hold is sub-second; the probe must outlast it.
"""
from __future__ import annotations

import contextlib

import pytest


def _flaky_lock(fail_times: int):
    """A fake acquire_lock held by the contending writer for the first
    ``fail_times`` calls, then free."""
    state = {"calls": 0}

    @contextlib.contextmanager
    def _acquire_lock(table_name, **kwargs):
        state["calls"] += 1
        yield state["calls"] > fail_times

    return _acquire_lock, state


@pytest.fixture
def _patched(monkeypatch):
    import src.data.job_lock as dl
    import src.data.source_health_probe as shp
    import src.ingest_main as im

    probed: list = []
    written: list = []
    monkeypatch.setattr(
        shp, "probe_all_sources",
        lambda *a, **k: (probed.append(1), {"open_meteo_archive": {"status": "OK"}})[1],
    )
    monkeypatch.setattr(shp, "write_source_health", lambda results: written.append(results))
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)
    # Silence the scheduler-health side-effect write from the @_scheduler_job wrapper.
    monkeypatch.setattr(
        "src.observability.scheduler_health._write_scheduler_health",
        lambda *a, **k: None,
    )
    return im, dl, probed, written, monkeypatch


def test_probe_retries_through_transient_lock(_patched):
    """RED-on-revert: lock held for 2 cycles then free -> probe still completes
    (probe_all_sources + write_source_health each called once after retrying).
    Pre-fix the FIRST lock-held returned immediately and the probe never ran."""
    im, dl, probed, written, monkeypatch = _patched
    flaky, state = _flaky_lock(fail_times=2)
    monkeypatch.setattr(dl, "acquire_lock", flaky)

    im._source_health_probe_tick()

    assert state["calls"] == 3, f"expected 2 retries then acquire; got {state['calls']} calls"
    assert len(probed) == 1, "all-source probe must run after retrying through contention"
    assert len(written) == 1, "source_health must be written after a successful retry"


def test_probe_runs_immediately_when_lock_free(_patched):
    """Control: no contention -> single acquire, probe + write once (no behavior
    change on the happy path)."""
    im, dl, probed, written, monkeypatch = _patched
    flaky, state = _flaky_lock(fail_times=0)
    monkeypatch.setattr(dl, "acquire_lock", flaky)

    im._source_health_probe_tick()

    assert state["calls"] == 1
    assert len(probed) == 1 and len(written) == 1


def test_probe_gives_up_after_budget_without_crashing(_patched):
    """If the lock is held for the full retry budget, the tick gives up quietly
    (no probe, no crash) — staleness is then surfaced by the freshness gate, not
    a daemon exception."""
    im, dl, probed, written, monkeypatch = _patched
    flaky, state = _flaky_lock(fail_times=999)  # never free
    monkeypatch.setattr(dl, "acquire_lock", flaky)

    im._source_health_probe_tick()  # must not raise

    assert state["calls"] == im._SOURCE_HEALTH_LOCK_RETRIES
    assert len(probed) == 0 and len(written) == 0
