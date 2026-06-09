# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: BLOCKER 1 — preflight_restart_check.py must read flag values from their correct scope (edli_v1 for capture/fusion/arm; feature_flags for soft_anchor) not the top-level cfg dict.
# Reuse: Run with pytest; update if flag scopes or scope-routing in preflight_restart_check.py changes.
"""
tests/test_preflight_restart_check_reads_edli_v1_flags.py

Operator-specified TDD test for BLOCKER 1:
preflight_restart_check.py must read flag values from their ACTUAL scope
in config/settings.json (edli_v1 for capture/fusion/arm/caps/qlcb;
feature_flags for soft_anchor flags) — NOT from the top-level cfg dict.

Assertion spec (operator-named):
  edli_v1.replacement_0_1_u0r_multimodel_capture_enabled=true
  + edli_v1.replacement_0_1_u0r_fusion_enabled=false
  -> stage == ACCRUING (not SHADOW)
  -> next flip == replacement_0_1_u0r_fusion_enabled
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

# Make scripts/ importable without package install
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

import importlib
import types


def _load_preflight():
    """Import preflight_restart_check as a module (bypasses __main__ guard)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "preflight_restart_check",
        os.path.join(SCRIPTS_DIR, "preflight_restart_check.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_preflight(settings: dict, promotion_evidence: dict | None = None) -> dict:
    """
    Write settings + optional promotion_evidence to a temp root dir,
    invoke preflight main(), capture stdout, and return:
      {
        "exit_code": int,
        "stdout": str,
        "stage": str,    # value after "POSTURE  : stage "
        "next_flip": str # value after "NEXT FLIP: "
      }
    """
    import io
    from contextlib import redirect_stdout

    with tempfile.TemporaryDirectory() as root:
        config_dir = os.path.join(root, "config")
        state_dir = os.path.join(root, "state", "replacement_forecast_shadow")
        os.makedirs(config_dir)
        os.makedirs(state_dir)

        with open(os.path.join(config_dir, "settings.json"), "w") as fh:
            json.dump(settings, fh)

        if promotion_evidence is not None:
            pe_path = os.path.join(state_dir, "promotion_evidence.json")
            with open(pe_path, "w") as fh:
                json.dump({"promotion_evidence": promotion_evidence}, fh)

        mod = _load_preflight()

        # Patch sys.argv so argparse reads our --root
        orig_argv = sys.argv[:]
        sys.argv = ["preflight_restart_check.py", "--root", root]
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                exit_code = mod.main()
        finally:
            sys.argv = orig_argv

    output = buf.getvalue()
    stage = ""
    next_flip = ""
    for line in output.splitlines():
        if line.startswith("POSTURE  : stage"):
            stage = line.split("POSTURE  : stage", 1)[1].strip()
        if line.startswith("NEXT FLIP:"):
            next_flip = line.split("NEXT FLIP:", 1)[1].strip()

    return {"exit_code": exit_code, "stdout": output, "stage": stage, "next_flip": next_flip}


# ---------------------------------------------------------------------------
# Minimal settings builder — only the keys relevant to the flag ladder
# ---------------------------------------------------------------------------

def _settings(*, capture: bool, fusion: bool,
               arm: bool = False,
               cap_notional: bool = True,
               cap_daily: bool = True,
               qlcb: bool = False,
               auth: bool = False, kelly: bool = False, flip: bool = False) -> dict:
    return {
        "edli_v1": {
            "replacement_0_1_u0r_multimodel_capture_enabled": capture,
            "replacement_0_1_u0r_fusion_enabled": fusion,
            "replacement_qlcb_settlement_sigma_floor_enabled": qlcb,
            "edli_live_operator_authorized": arm,
            "tiny_live_notional_cap_enabled": cap_notional,
            "tiny_live_daily_order_cap_enabled": cap_daily,
        },
        "feature_flags": {
            "openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled": auth,
            "openmeteo_ecmwf_ifs9_aifs_soft_anchor_kelly_increase_enabled": kelly,
            "openmeteo_ecmwf_ifs9_aifs_soft_anchor_direction_flip_enabled": flip,
        },
    }


# ---------------------------------------------------------------------------
# BLOCKER 1 core assertion (operator spec):
#   capture=true + fusion=false -> stage ACCRUING, next flip == fusion flag
# ---------------------------------------------------------------------------

def test_capture_on_fusion_off_reads_edli_v1_stage_is_accruing():
    """
    With capture=true + fusion=false in edli_v1, stage must be ACCRUING.
    Before the fix, cfg.get() reads top-level (None -> False) so stage
    would fall through to SHADOW — the failure proves the bug exists.
    """
    result = _run_preflight(_settings(capture=True, fusion=False))
    assert "ACCRUING" in result["stage"], (
        f"Expected ACCRUING stage but got: {result['stage']!r}\n"
        f"Full output:\n{result['stdout']}"
    )


def test_capture_on_fusion_off_next_flip_is_fusion_flag():
    """
    With capture=true + fusion=false in edli_v1, next flip must name
    replacement_0_1_u0r_fusion_enabled.
    """
    result = _run_preflight(_settings(capture=True, fusion=False))
    assert "replacement_0_1_u0r_fusion_enabled" in result["next_flip"], (
        f"Expected fusion flag in NEXT FLIP but got: {result['next_flip']!r}\n"
        f"Full output:\n{result['stdout']}"
    )


# ---------------------------------------------------------------------------
# Stage ladder completeness — all rungs read from correct scope
# ---------------------------------------------------------------------------

def test_capture_off_fusion_off_stage_is_shadow():
    result = _run_preflight(_settings(capture=False, fusion=False))
    assert "SHADOW" in result["stage"], (
        f"Expected SHADOW but got: {result['stage']!r}"
    )


def test_capture_off_stage_next_flip_is_capture_flag():
    result = _run_preflight(_settings(capture=False, fusion=False))
    assert "replacement_0_1_u0r_multimodel_capture_enabled" in result["next_flip"], (
        f"Expected capture flag in NEXT FLIP but got: {result['next_flip']!r}"
    )


def test_fusion_on_no_auth_stage_is_shadow_fusion():
    result = _run_preflight(_settings(capture=True, fusion=True))
    assert "SHADOW-FUSION" in result["stage"], (
        f"Expected SHADOW-FUSION but got: {result['stage']!r}"
    )


def test_arm_flag_reads_from_edli_v1():
    """
    arm=true in edli_v1 with fusion=true -> stage ARMED.
    If arm were read from top-level it would be None -> False -> stage 3 or 2.
    """
    result = _run_preflight(_settings(capture=True, fusion=True, arm=True,
                                       auth=True, kelly=True, flip=True))
    assert "ARMED" in result["stage"], (
        f"Expected ARMED but got: {result['stage']!r}\n"
        f"Full output:\n{result['stdout']}"
    )


def test_cap_flags_read_from_edli_v1_coherence_warn_when_off():
    """
    tiny_live caps off in edli_v1 -> COHERENCE section must emit a WARN
    about tiny_live caps not both ON.
    """
    result = _run_preflight(_settings(capture=False, fusion=False,
                                       cap_notional=False, cap_daily=False))
    assert "tiny_live" in result["stdout"], (
        f"Expected tiny_live cap WARN in output but got:\n{result['stdout']}"
    )


def test_soft_anchor_flags_read_from_feature_flags():
    """
    auth/kelly/flip=true in feature_flags (not edli_v1) -> CRITICAL coherence
    warning fires when evidence gate fails (no promotion_evidence file).
    """
    result = _run_preflight(
        _settings(capture=True, fusion=True, auth=True, kelly=True, flip=True)
    )
    assert "CRITICAL" in result["stdout"], (
        f"Expected CRITICAL in output when authority ON + evidence FAIL:\n{result['stdout']}"
    )
    assert result["exit_code"] == 2, (
        f"Expected exit_code=2 for CRITICAL but got {result['exit_code']}"
    )


# ---------------------------------------------------------------------------
# promotion_evidence path still works (no regression)
# ---------------------------------------------------------------------------

def test_promotion_evidence_path_still_read():
    """
    When promotion_evidence.json is present with a passing gate, evidence
    should be PASS and no evidence blockers should appear.
    """
    pe = {
        "official_days": 10,
        "official_rows": 500,
        "q_lcb_coverage": 0.97,
        "after_cost_pnl": 1.5,
        "nested_walk_forward_passed": True,
        "same_clob_replay_passed": True,
        "fee_depth_fill_evidence_passed": True,
        "product_specific_refit_passed": True,
        "anti_lookahead_violations": 0,
        "source_availability_violations": 0,
        "unresolved_regression_clusters": 0,
        "unit_pnl_only": False,
    }
    result = _run_preflight(
        _settings(capture=True, fusion=True),
        promotion_evidence=pe,
    )
    assert "PASS" in result["stdout"], (
        f"Expected evidence gate PASS:\n{result['stdout']}"
    )
    assert "blockers=" not in result["stdout"], (
        f"Expected no blockers with passing evidence:\n{result['stdout']}"
    )
