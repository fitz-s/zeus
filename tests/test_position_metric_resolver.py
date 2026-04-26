# Created: 2026-04-26
# Last reused/audited: 2026-04-26
# Authority basis: docs/operations/task_2026-04-26_full_data_midstream_fix_plan/
#                  phases/task_2026-04-26_phase2_adjacent_fixes/plan.md slice P2-C1
"""Slice P2-C1 relationship + function tests.

Pins the canonical position-metric resolver contract:

- VERIFIED return for valid metric ("high" or "low").
- UNVERIFIED return for missing / None / empty / garbage / non-string,
  with provenance source preserving the raw value for forensic filters.
- Default metric is always "high" (backward-compat with legacy positions).
- DEBUG log fires when default path is taken (operator audit trail).

P2-C2 will route 4 silent-default sites through this helper. The tests
here are the structural anchor for that consolidation.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.state.chain_reconciliation import resolve_position_metric


# -----------------------------------------------------------------------------
# Verified path
# -----------------------------------------------------------------------------


def test_high_position_returns_verified():
    position = SimpleNamespace(temperature_metric="high")
    metric, authority, source = resolve_position_metric(position)
    assert metric == "high"
    assert authority == "VERIFIED"
    assert source == "position_materialized"


def test_low_position_returns_verified():
    position = SimpleNamespace(temperature_metric="low")
    metric, authority, source = resolve_position_metric(position)
    assert metric == "low"
    assert authority == "VERIFIED"
    assert source == "position_materialized"


# -----------------------------------------------------------------------------
# Invalid / missing → UNVERIFIED with provenance
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_metric",
    [None, "", "  ", "garbage", "HIGH", "Low", 0, 42, True, False],
)
def test_invalid_metric_returns_unverified(raw_metric):
    position = SimpleNamespace(temperature_metric=raw_metric)
    metric, authority, source = resolve_position_metric(position)
    assert metric == "high", "default direction is HIGH (preserves backward compat)"
    assert authority == "UNVERIFIED"
    assert source.startswith("position_missing_metric:"), (
        "authority_source must carry concrete provenance for forensic filters."
    )


def test_position_missing_attribute_returns_unverified():
    """A bare object with no temperature_metric attribute → UNVERIFIED."""
    position = SimpleNamespace()
    metric, authority, source = resolve_position_metric(position)
    assert metric == "high"
    assert authority == "UNVERIFIED"
    assert source.startswith("position_missing_metric:")


# -----------------------------------------------------------------------------
# Operator audit trail: DEBUG log on default-fire
# -----------------------------------------------------------------------------


def test_unverified_resolution_logs_debug_with_trade_id(caplog):
    """The default-fire path must emit a DEBUG log identifying the position
    so operators can audit which positions are being defaulted."""
    position = SimpleNamespace(
        temperature_metric=None,
        trade_id="entry-2026-04-15-NYC-001",
    )
    with caplog.at_level(logging.DEBUG, logger="src.state.chain_reconciliation"):
        resolve_position_metric(position)
    assert any(
        "entry-2026-04-15-NYC-001" in rec.message
        and "defaulting to HIGH+UNVERIFIED" in rec.message
        for rec in caplog.records
    ), (
        "DEBUG log must surface the trade_id for operator audit trail. "
        "Without this, silent-HIGH defaults are invisible to ops review."
    )


def test_verified_resolution_does_not_log():
    """VERIFIED path is silent — no log noise on the happy path."""
    position = SimpleNamespace(
        temperature_metric="high",
        trade_id="entry-2026-04-15-NYC-002",
    )
    with caplog_at_debug() as caplog:
        resolve_position_metric(position)
    debug_msgs = [
        r for r in caplog.records
        if r.levelno == logging.DEBUG and "defaulting" in r.message
    ]
    assert not debug_msgs, "VERIFIED path must not emit default-fire log"


# -----------------------------------------------------------------------------
# Symmetry with resolve_rescue_authority — same shape, same semantics
# -----------------------------------------------------------------------------


def test_resolve_position_metric_matches_resolve_rescue_authority_shape():
    """Both resolvers must return identical shape + agree on every input."""
    from src.state.chain_reconciliation import resolve_rescue_authority

    for raw in ("high", "low", None, "", "garbage", 42):
        position = SimpleNamespace(temperature_metric=raw)
        a = resolve_position_metric(position)
        b = resolve_rescue_authority(position)
        assert a == b, (
            f"resolvers must agree for raw={raw!r}: "
            f"position={a!r} vs rescue={b!r}"
        )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


class _CapLogStub:
    """Minimal caplog-style helper for the no-log assertion test (pytest's
    caplog fixture isn't injectable into context-manager idioms easily)."""

    def __init__(self):
        self.records = []
        self._handler = None
        self._logger = None

    def __enter__(self):
        import logging as _logging
        self._logger = _logging.getLogger("src.state.chain_reconciliation")
        self._logger.setLevel(_logging.DEBUG)

        records = self.records

        class _H(_logging.Handler):
            def emit(self, record):
                records.append(record)

        self._handler = _H()
        self._handler.setLevel(_logging.DEBUG)
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._handler and self._logger:
            self._logger.removeHandler(self._handler)


def caplog_at_debug() -> _CapLogStub:
    return _CapLogStub()
