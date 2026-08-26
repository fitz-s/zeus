# Created: 2026-08-25
# Last reused/audited: 2026-08-25
# Authority basis: docs/operations/current/plans/db_first_principles_audit_2026-07-20/
#   implementation/capture_policy_spec.md, crossing-instrumentation increment.
"""Antibodies for the DAY0_EXTREME_UPDATED crossing-triggered book capture.

``_maybe_capture_day0_extreme_book`` (src/engine/event_reactor_adapter.py) fires
a fail-soft, rate-bounded, backgrounded book capture for every DAY0_EXTREME_UPDATED
event reaching submit -- the moment a temperature crossing may have physically
decided some bins. These tests exercise the function directly and pin its wiring
into ``_submit_inner`` at the source level; they never touch a live venue, a live
DB, or the reactor's full decision pipeline.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.engine import event_reactor_adapter as era

REPO = Path(__file__).resolve().parent.parent.parent
_CAPTURE_THREAD_NAME = "day0-extreme-book-capture"
_REFRESH_TARGET = "src.data.substrate_observer.refresh_money_path_substrate_now"


def _event(city="Austin", target_date="2026-07-20", metric="high", **extra):
    payload = {"city": city, "target_date": target_date, "metric": metric}
    payload.update(extra)
    return SimpleNamespace(payload_json=json.dumps(payload))


@pytest.fixture(autouse=True)
def _reset_rate_bound_state():
    """The rate-bound clock is process-global; give each test a clean one."""
    era._day0_extreme_capture_last_fired.clear()
    yield
    era._day0_extreme_capture_last_fired.clear()


def _wait_for_capture_threads(timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(
            t.name == _CAPTURE_THREAD_NAME and t.is_alive() for t in threading.enumerate()
        ):
            return
        time.sleep(0.01)


def test_fires_capture_with_correct_family_and_trigger():
    with patch(_REFRESH_TARGET) as mock_refresh:
        era._maybe_capture_day0_extreme_book(_event())
        _wait_for_capture_threads()
    mock_refresh.assert_called_once()
    _, kwargs = mock_refresh.call_args
    assert kwargs["families"] == [("Austin", "2026-07-20", "high")]
    assert kwargs["capture_trigger_override"] == "DAY0_EXTREME_EVENT"


def test_rate_bound_suppresses_second_call_within_window():
    with patch(_REFRESH_TARGET) as mock_refresh:
        era._maybe_capture_day0_extreme_book(_event())
        era._maybe_capture_day0_extreme_book(_event())
        _wait_for_capture_threads()
    assert mock_refresh.call_count == 1


def test_rate_bound_is_scoped_per_family():
    with patch(_REFRESH_TARGET) as mock_refresh:
        era._maybe_capture_day0_extreme_book(_event(city="Austin"))
        era._maybe_capture_day0_extreme_book(_event(city="Denver"))
        _wait_for_capture_threads()
    assert mock_refresh.call_count == 2


def test_missing_family_fields_do_not_fire():
    with patch(_REFRESH_TARGET) as mock_refresh:
        era._maybe_capture_day0_extreme_book(_event(city=""))
        era._maybe_capture_day0_extreme_book(_event(metric="unknown"))
        _wait_for_capture_threads()
    mock_refresh.assert_not_called()


def test_capture_exception_never_propagates():
    with patch(_REFRESH_TARGET, side_effect=RuntimeError("venue unreachable")):
        era._maybe_capture_day0_extreme_book(_event())  # must not raise
        _wait_for_capture_threads()


def test_capture_runs_off_the_calling_thread():
    """Fail-soft by construction, not just by try/except: the venue fetch must
    never block the caller (money-path event submission)."""
    release = threading.Event()

    def _blocking(*_args, **_kwargs):
        release.wait(timeout=2.0)

    with patch(_REFRESH_TARGET, side_effect=_blocking):
        started = time.monotonic()
        era._maybe_capture_day0_extreme_book(_event())
        elapsed = time.monotonic() - started
    release.set()
    _wait_for_capture_threads()
    assert elapsed < 0.5


def test_wired_unconditionally_into_submit_inner_day0_branch():
    """Source-level pin (matches tests/engine/test_no_artificial_age_gate.py
    convention): the capture call sits immediately after is_day0_lane is
    computed and before any block/veto branch, so a blocked or vetoed cycle
    still captures the crossing book."""
    src = (REPO / "src/engine/event_reactor_adapter.py").read_text()
    m = re.search(
        r"is_day0_lane = event_type in _DAY0_LANE_EVENT_TYPES\s*\n\s*if is_day0_lane:\n"
        r".*?_maybe_capture_day0_extreme_book\(event\)",
        src,
        re.DOTALL,
    )
    assert m, "day0 extreme-book capture call site moved or was removed"


def test_taxonomy_value_is_full_eligible():
    from src.state.snapshot_repo import COMPACT_CAPTURE_TRIGGERS, FULL_CAPTURE_TRIGGERS

    assert "DAY0_EXTREME_EVENT" in FULL_CAPTURE_TRIGGERS
    assert "DAY0_EXTREME_EVENT" not in COMPACT_CAPTURE_TRIGGERS
