# Created: 2026-05-05
# Last reused/audited: 2026-05-05
# Authority basis: PR #64 Copilot review — live_promotion_approved passthrough fix
"""Regression: live_promotion_approved must be plumbed through _with_evidence to legacy fallback.

Bug (commit 3a6f5693): flag-OFF branch of _with_evidence hardcoded live_promotion_approved=False,
making LIVE_ELIGIBLE unreachable in shadow path even when rollout-gate approved calibration.

Tests:
  1. Flag OFF + live_promotion_approved=True + same-domain → LIVE_ELIGIBLE
  2. Flag OFF + live_promotion_approved=False + same-domain → SHADOW_ONLY
  3. Flag OFF + cross-domain (mismatched source_id) + promotion=True → BLOCKED (source mismatch)
  4. Signature audit: _with_evidence accepts live_promotion_approved keyword
"""

from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone
from unittest import mock

import pytest

from src.config import entry_forecast_config
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

_FLAG_OFF = {"ZEUS_CALIBRATION_TRANSFER_OOS_EVAL_ENABLED": "false"}


# ---------------------------------------------------------------------------
# 1. Signature audit
# ---------------------------------------------------------------------------

def test_with_evidence_accepts_live_promotion_approved_kwarg():
    """_with_evidence must have live_promotion_approved in its signature."""
    sig = inspect.signature(evaluate_calibration_transfer_policy_with_evidence)
    assert "live_promotion_approved" in sig.parameters, (
        "evaluate_calibration_transfer_policy_with_evidence missing live_promotion_approved param"
    )
    param = sig.parameters["live_promotion_approved"]
    assert param.default is False, (
        "live_promotion_approved should default to False"
    )


# ---------------------------------------------------------------------------
# 2. Flag OFF + approved=True + same-domain → LIVE_ELIGIBLE
# ---------------------------------------------------------------------------

def test_flag_off_promotion_approved_same_domain_live_eligible():
    """When flag is OFF and live_promotion_approved=True, LIVE_ELIGIBLE is reachable."""
    cfg = _cfg()
    conn = _make_conn()

    with mock.patch.dict("os.environ", _FLAG_OFF):
        result = evaluate_calibration_transfer_policy_with_evidence(
            config=cfg,
            source_id=cfg.source_id,
            target_source_id=cfg.source_id,
            source_cycle=None,
            target_cycle=None,
            horizon_profile=None,
            season=None,
            cluster=None,
            metric="high",
            platt_model_key=None,
            conn=conn,
            now=NOW,
            live_promotion_approved=True,
        )

    assert result.status == "LIVE_ELIGIBLE", (
        f"Expected LIVE_ELIGIBLE with promotion approved, got {result.status} "
        f"reason_codes={result.reason_codes}"
    )


# ---------------------------------------------------------------------------
# 3. Flag OFF + approved=False + same-domain → SHADOW_ONLY
# ---------------------------------------------------------------------------

def test_flag_off_promotion_not_approved_same_domain_shadow_only():
    """When flag is OFF and live_promotion_approved=False, LIVE_ELIGIBLE is NOT reached."""
    cfg = _cfg()
    conn = _make_conn()

    with mock.patch.dict("os.environ", _FLAG_OFF):
        result = evaluate_calibration_transfer_policy_with_evidence(
            config=cfg,
            source_id=cfg.source_id,
            target_source_id=cfg.source_id,
            source_cycle=None,
            target_cycle=None,
            horizon_profile=None,
            season=None,
            cluster=None,
            metric="high",
            platt_model_key=None,
            conn=conn,
            now=NOW,
            live_promotion_approved=False,
        )

    assert result.status != "LIVE_ELIGIBLE", (
        f"LIVE_ELIGIBLE must not be reachable when live_promotion_approved=False, "
        f"got {result.status}"
    )
    assert result.status == "SHADOW_ONLY", (
        f"Expected SHADOW_ONLY with promotion not approved, got {result.status}"
    )


# ---------------------------------------------------------------------------
# 4. Flag OFF + cross-domain (mismatched source_id) + promotion=True → BLOCKED
# ---------------------------------------------------------------------------

def test_flag_off_cross_domain_promotion_approved_blocked():
    """Cross-domain (source_id mismatch) with promotion=True → BLOCKED (legacy mapping)."""
    cfg = _cfg()
    conn = _make_conn()

    with mock.patch.dict("os.environ", _FLAG_OFF):
        result = evaluate_calibration_transfer_policy_with_evidence(
            config=cfg,
            source_id="ecmwf-open-data-cross-domain-other",  # mismatched
            target_source_id=cfg.source_id,
            source_cycle=None,
            target_cycle=None,
            horizon_profile=None,
            season=None,
            cluster=None,
            metric="high",
            platt_model_key=None,
            conn=conn,
            now=NOW,
            live_promotion_approved=True,
        )

    # Legacy mapping: source_id != config.source_id → BLOCKED (SOURCE_MISMATCH)
    assert result.status == "BLOCKED", (
        f"Cross-domain with mismatched source_id must be BLOCKED, got {result.status}"
    )
    assert "CALIBRATION_TRANSFER_SOURCE_MISMATCH" in result.reason_codes, (
        f"Expected SOURCE_MISMATCH reason, got {result.reason_codes}"
    )
