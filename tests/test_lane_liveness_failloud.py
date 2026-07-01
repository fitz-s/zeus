# Created: 2026-06-16
# Last reused or audited: 2026-06-16
# Authority basis: timing-semantics fix AB3 (fail-loud lane counters); docs/evidence/timing_audit/ZEUS_TIMING_COMPLETE_PLAN_2026-06-16.md
# Lifecycle: created=2026-06-16; last_reviewed=2026-06-16; last_reused=never
# Purpose: Antibody for the exact blindness that cost weeks — a decision lane
#   (edli_no_submit_receipts) sat dead from 2026-06-06 because lane-write
#   failures were swallowed silently and a dead lane looked identical to a
#   quiet one. These unit tests pin: (a) a swallowed lane-write failure now
#   FAILS LOUD (logger.error naming the lane) + is COUNTED on the summary while
#   marking explicit observability degradation; (b) the heartbeat lane-liveness check
#   emits a WARNING naming a failed / zero-write lane and stays SILENT when all
#   expected lanes wrote. Unit-level: no engine boot, no live DB; summaries are
#   constructed directly.
# Reuse: Run when modifying cycle_runtime._record_lane_write_failure /
#   _record_lane_write_success or heartbeat_supervisor.data_lane_health_check.
"""Unit antibodies for AB3 fail-loud lane counters + lane-liveness health check."""

from __future__ import annotations

import logging

import pytest

from src.engine.cycle_runtime import (
    _record_lane_write_failure,
    _record_lane_write_success,
)
from src.control.heartbeat_supervisor import data_lane_health_check


# --------------------------------------------------------------------------- #
# Part 1: _record_lane_write_failure / _record_lane_write_success (cycle_runtime)
# --------------------------------------------------------------------------- #
def test_record_lane_write_failure_counts_sets_observability_degraded_and_logs_error(caplog):
    summary: dict = {}
    exc = RuntimeError("db is locked")

    with caplog.at_level(logging.ERROR, logger="src.engine.cycle_runtime"):
        _record_lane_write_failure(summary, "decision_signal_build", exc)

    # (a) per-lane failure counter incremented
    assert summary["lane_write_failures"] == {"decision_signal_build": 1}
    # (b) telemetry/write degradation is explicit and not confused with RiskGuard.
    assert summary["observability_degraded"] is True
    # (c) failed LOUD: an ERROR was logged naming the lane
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "expected a logger.error on lane write failure"
    assert any("decision_signal_build" in r.getMessage() for r in error_records)
    assert any("LANE WRITE FAILED" in r.getMessage() for r in error_records)


def test_record_lane_write_failure_accumulates_per_lane():
    summary: dict = {}
    _record_lane_write_failure(summary, "forward_market_substrate", ValueError("x"))
    _record_lane_write_failure(summary, "forward_market_substrate", ValueError("y"))
    _record_lane_write_failure(summary, "opportunity_fact", ValueError("z"))

    assert summary["lane_write_failures"] == {
        "forward_market_substrate": 2,
        "opportunity_fact": 1,
    }
    assert summary["observability_degraded"] is True


def test_record_lane_write_success_counts_per_lane():
    summary: dict = {}
    _record_lane_write_success(summary, "opportunity_fact")
    _record_lane_write_success(summary, "opportunity_fact")
    _record_lane_write_success(summary, "probability_trace")

    assert summary["decision_lane_writes"] == {
        "opportunity_fact": 2,
        "probability_trace": 1,
    }
    # success path does NOT flip observability degradation.
    assert "observability_degraded" not in summary


# --------------------------------------------------------------------------- #
# Part 2: data_lane_health_check (heartbeat_supervisor)
# --------------------------------------------------------------------------- #
def test_health_check_warns_on_failed_lane(caplog):
    with caplog.at_level(logging.WARNING, logger="src.control.heartbeat_supervisor"):
        verdict = data_lane_health_check(
            lane_write_failures={"opportunity_fact": 3},
            decision_lane_writes={"opportunity_fact": 0},
        )

    assert verdict["ok"] is False
    assert verdict["failed_lanes"] == {"opportunity_fact": 3}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING naming the failed lane"
    assert any("opportunity_fact" in r.getMessage() for r in warnings)
    assert any("DATA LANE UNHEALTHY" in r.getMessage() for r in warnings)


def test_health_check_warns_on_zero_write_expected_lane(caplog):
    with caplog.at_level(logging.WARNING, logger="src.control.heartbeat_supervisor"):
        verdict = data_lane_health_check(
            lane_write_failures={},
            decision_lane_writes={"probability_trace": 5},  # this one wrote
            expected_lanes={"probability_trace", "opportunity_fact"},  # latter is dead
        )

    assert verdict["ok"] is False
    assert verdict["zero_write_lanes"] == ["opportunity_fact"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("opportunity_fact" in r.getMessage() for r in warnings)
    assert any("ZERO" in r.getMessage().upper() for r in warnings)


def test_health_check_silent_when_all_expected_lanes_wrote(caplog):
    with caplog.at_level(logging.WARNING, logger="src.control.heartbeat_supervisor"):
        verdict = data_lane_health_check(
            lane_write_failures={},
            decision_lane_writes={"opportunity_fact": 4, "probability_trace": 2},
            expected_lanes={"opportunity_fact", "probability_trace"},
        )

    assert verdict["ok"] is True
    assert verdict["failed_lanes"] == {}
    assert verdict["zero_write_lanes"] == []
    # silent: no lane-liveness WARNING emitted on the healthy path
    lane_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "DATA LANE UNHEALTHY" in r.getMessage()
    ]
    assert not lane_warnings, "health check must stay silent when all lanes wrote"


def test_health_check_does_not_flag_quiet_lane_without_expectation(caplog):
    # Without an explicit expected_lanes set, a quiet (zero-write) lane is NOT
    # assumed dead — only recorded FAILURES are loud. This avoids false alarms
    # on lanes that legitimately have nothing to write some cycles.
    with caplog.at_level(logging.WARNING, logger="src.control.heartbeat_supervisor"):
        verdict = data_lane_health_check(
            lane_write_failures={},
            decision_lane_writes={},  # nothing wrote, but nothing expected either
            expected_lanes=None,
        )

    assert verdict["ok"] is True
    assert verdict["zero_write_lanes"] == []
    lane_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "DATA LANE UNHEALTHY" in r.getMessage()
    ]
    assert not lane_warnings


def test_health_check_handles_none_inputs_gracefully():
    # Defensive: a cycle summary that never recorded either map (e.g. an
    # early-aborted cycle) must not raise.
    verdict = data_lane_health_check(
        lane_write_failures=None,
        decision_lane_writes=None,
        expected_lanes=None,
    )
    assert verdict["ok"] is True
    assert verdict["failed_lanes"] == {}
    assert verdict["zero_write_lanes"] == []
