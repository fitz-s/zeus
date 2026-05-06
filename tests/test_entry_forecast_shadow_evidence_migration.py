# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: architecture/calibration_transfer_oos_design_2026-05-05.md Phase X.1
"""Verify evaluate_entry_forecast_shadow delegates to evidence-gated function.

Tests:
  1. The callsite uses evaluate_calibration_transfer_policy_with_evidence (not legacy).
  2. Same-domain route → LIVE_ELIGIBLE calibration verdict (flag on).
  3. Cross-domain route with no validated row → SHADOW_ONLY (flag on).
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import date, datetime, timezone
from unittest import mock

import pytest

from src.config import entry_forecast_config
from src.data import entry_forecast_shadow
from src.data.calibration_transfer_policy import (
    evaluate_calibration_transfer_policy_with_evidence,
)
from src.state.schema.v2_schema import apply_v2_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    apply_v2_schema(conn)
    return conn


def _cfg():
    return entry_forecast_config()


NOW = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Callsite audit: legacy function must NOT be called directly
# ---------------------------------------------------------------------------

def test_shadow_module_calls_evidence_gated_function():
    """evaluate_entry_forecast_shadow must call the _with_evidence variant."""
    source = inspect.getsource(entry_forecast_shadow.evaluate_entry_forecast_shadow)
    assert "evaluate_calibration_transfer_policy_with_evidence" in source, (
        "Callsite was not migrated to evidence-gated function"
    )
    # Legacy bare call must be gone
    import re
    bare_calls = re.findall(
        r"\bevaluate_calibration_transfer_policy\b(?!_with_evidence)", source
    )
    assert not bare_calls, (
        f"Legacy bare callsite still present in evaluate_entry_forecast_shadow: {bare_calls}"
    )


# ---------------------------------------------------------------------------
# 2. Same-domain → LIVE_ELIGIBLE when flag is on
# ---------------------------------------------------------------------------

def test_same_domain_returns_live_eligible_when_flag_on():
    """With flag on and same source_id, calibration fast-path fires LIVE_ELIGIBLE."""
    cfg = _cfg()
    conn = _make_conn()

    with mock.patch.dict("os.environ", {"ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED": "true"}):
        result = evaluate_calibration_transfer_policy_with_evidence(
            config=cfg,
            source_id=cfg.source_id,
            target_source_id=cfg.source_id,
            source_cycle="00",
            target_cycle="00",
            horizon_profile=None,   # not reached — same-domain fast-path fires first
            season=None,
            cluster=None,
            metric="high",
            platt_model_key=None,
            conn=conn,
            now=NOW,
        )
    assert result.status == "LIVE_ELIGIBLE", result


# ---------------------------------------------------------------------------
# 3. Cross-domain, no evidence row → SHADOW_ONLY when flag is on
# ---------------------------------------------------------------------------

def test_cross_domain_no_evidence_returns_shadow_only():
    """Cross-domain route with no validated row → SHADOW_ONLY (fail-closed)."""
    cfg = _cfg()
    conn = _make_conn()

    with mock.patch.dict("os.environ", {"ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED": "true"}):
        result = evaluate_calibration_transfer_policy_with_evidence(
            config=cfg,
            source_id="ecmwf",
            target_source_id="noaa_gfs",
            source_cycle="00",
            target_cycle="12",
            horizon_profile="medium",
            season="summer",
            cluster="midwest",
            metric="high",
            platt_model_key="gfs_high_v1",
            conn=conn,
            now=NOW,
        )
    assert result.status == "SHADOW_ONLY", result
    assert "CALIBRATION_TRANSFER_NO_EVIDENCE" in result.reason_codes
