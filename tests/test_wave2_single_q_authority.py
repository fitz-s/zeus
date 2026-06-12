# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator law 2026-06-12 ("需要测试的现在就测试好，然后依次全执行")
#   + Wave-2 of docs/operations/overengineering_simplification_plan_2026-06-12.md
#   (items 1, 5, 6, 7, 8). Standing law: feature correct -> always-on + delete OFF
#   branch; feature refuted -> delete code AND key; never leave deprecated keys.
"""Wave-2 antibody suite: single q authority + collapsed/merged/deleted knobs.

Pins the Wave-2 simplifications so a re-introduction is a RED test, not a note:

  item 1  baseline LCB no longer caps the replacement (live) q — single q authority.
          The baseline is diagnostics-only (baseline_q_lcb_reference), never min()-joined.
  item 5  live_execution_mode collapses to "edli_live" (canary string deleted; mapped
          old->new at the read boundary so persisted data stays readable).
  item 6  the settlement σ-floor applies by PER-CELL DATA AVAILABILITY (no flag); the
          three σ-floor knobs are merged + deleted.
  item 7  settlement-refuted bias branches (bias_treatment_v2 / replacement_0_1 EB bias)
          deleted — code AND keys AND module.
  item 8  taker FOK/FAK legality is UNCONDITIONAL in the execution certificate — the
          taker_fok_fak_live_enabled key + OFF branch + gate function deleted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SETTINGS = _REPO / "config" / "settings.json"
_SRC = _REPO / "src"


# The Wave-2 deleted settings keys (literal scan over settings.json).
_DELETED_SETTINGS_KEYS = (
    # item 5
    "edli_live_canary_artifact_path",
    # item 6 (three σ-floor knobs merged to one data-availability rule)
    "edli_settlement_sigma_floor_enabled",
    "edli_settlement_sigma_floor_required",
    "replacement_qlcb_settlement_sigma_floor_enabled",
    # item 7 (settlement-refuted, deleted)
    "bias_treatment_v2_enabled",
    "replacement_0_1_eb_bias_correction_enabled",
    # item 8 (taker law folded unconditional)
    "taker_fok_fak_live_enabled",
)

# Deleted code symbols that must not be referenced (outside deletion-documenting comments).
_DELETED_SYMBOLS = (
    "assert_taker_live_allowed",
    "_replacement_eb_bias_shift_c",
    "resolve_replacement_eb_bias_shift_c",
    "_edli_bias_treatment_for_bucket",
    "BiasTreatment",
    "_edli_settlement_sigma_floor_enabled",
    "_edli_settlement_sigma_floor_required",
    "_replacement_qlcb_settlement_sigma_floor_enabled",
    "_resolve_replacement_settlement_floor_lcb",
    "_replacement_settlement_grounded_lcb",
    "cap_to_baseline",
    "cap_replacement_q_lcb_to_baseline",
)

# Deleted modules / files.
_DELETED_FILES = (
    "src/calibration/replacement_eb_bias.py",
    "src/contracts/bias_treatment.py",
)


def _iter_src_py():
    for path in _SRC.rglob("*.py"):
        yield path


def _is_deletion_comment(line: str) -> bool:
    """A line that merely documents the deletion (Wave-2 tombstone comment)."""
    stripped = line.strip()
    if not stripped.startswith("#"):
        # docstring lines also tolerated when they describe the deletion
        pass
    markers = ("Wave-2", "DELETED", "deleted", "no longer", "formerly", "refuted")
    return any(m in line for m in markers)


# ---------------------------------------------------------------------------
# Literal scans: deleted keys absent from settings + not read in src.
# ---------------------------------------------------------------------------
def test_settings_has_none_of_the_wave2_deleted_keys():
    raw = _SETTINGS.read_text()
    data = json.loads(raw)  # valid JSON (fails loudly on corruption)
    edli = data["edli"]
    for key in _DELETED_SETTINGS_KEYS:
        assert key not in edli, f"deleted Wave-2 key reappeared in edli: {key}"
        assert f'"{key}"' not in raw, f"deleted key string present in settings.json: {key}"
    # Their explanatory *_note siblings must be gone too (no deprecated keys left behind).
    for note in ("_bias_treatment_v2_note", "_replacement_0_1_eb_bias_correction_enabled_note"):
        assert note not in edli, f"deprecated note key left behind: {note}"


def test_no_src_code_reads_the_wave2_deleted_flags():
    read_patterns = [
        (re.compile(r'get\(\s*["\']' + re.escape(k) + r'["\']'), k)
        for k in _DELETED_SETTINGS_KEYS
    ]
    offenders: list[str] = []
    for path in _iter_src_py():
        text = path.read_text()
        for pat, key in read_patterns:
            if pat.search(text):
                offenders.append(f"{path.relative_to(_REPO)} reads {key}")
    assert not offenders, "deleted Wave-2 flags still read in src/:\n" + "\n".join(offenders)


def test_no_src_code_references_deleted_symbols():
    offenders: list[str] = []
    for path in _iter_src_py():
        for line in path.read_text().splitlines():
            for sym in _DELETED_SYMBOLS:
                if sym in line and not _is_deletion_comment(line):
                    offenders.append(f"{path.relative_to(_REPO)}: {line.strip()[:90]}")
    assert not offenders, "deleted Wave-2 symbols still referenced:\n" + "\n".join(offenders)


def test_deleted_modules_are_gone():
    for rel in _DELETED_FILES:
        assert not (_REPO / rel).exists(), f"deleted module still present: {rel}"


# ---------------------------------------------------------------------------
# item 1 — single q authority. The live replacement q_lcb is NOT capped by the
# (lower) legacy baseline. Construct baseline < replacement; assert the candidate
# view uses the replacement value, with the baseline carried diagnostics-only.
# ---------------------------------------------------------------------------
def test_item1_replacement_q_lcb_not_capped_by_lower_baseline():
    from src.engine.replacement_forecast_hook_factory import _replacement_q_lcb_for_candidate

    class _Bin:
        low, high, unit = 24.0, 26.0, "C"

    class _Cand:
        condition_id = "cond-x"
        bin = _Bin()

    class _Proof:
        candidate = _Cand()
        direction = "buy_yes"
        q_lcb_5pct = 0.50  # baseline LCB is LOWER than the replacement value below
        token_id = "tok"
        executable_snapshot_id = "snap"

    class _Bundle:
        # replacement q_lcb for the bound bin is 0.72 > baseline 0.50
        q = {"bin-a": 0.80}
        q_lcb = {"bin-a": 0.72}
        q_ucb = {"bin-a": 0.88}
        provenance_json = {
            "bin_topology": [{"bin_id": "bin-a", "lower_c": 24.0, "upper_c": 26.0}]
        }

    value = _replacement_q_lcb_for_candidate(_Proof(), replacement_bundle=_Bundle())
    # SINGLE AUTHORITY: the replacement 0.72 is used, NOT min(0.72, baseline 0.50)=0.50.
    assert value == pytest.approx(0.72), "baseline must not cap the replacement live q_lcb"


def test_item1_baseline_fallback_when_no_replacement_data_is_honest_not_a_cap():
    """When the bundle carries no q_lcb for the bin, the honest baseline fallback stays
    (legacy strategy genuinely running on baseline q) — that is NOT the deleted cap."""
    from src.engine.replacement_forecast_hook_factory import _replacement_q_lcb_for_candidate

    class _Bin:
        low, high, unit = 24.0, 26.0, "C"

    class _Cand:
        condition_id = "cond-x"
        bin = _Bin()

    class _Proof:
        candidate = _Cand()
        direction = "buy_yes"
        q_lcb_5pct = 0.50
        token_id = "tok"
        executable_snapshot_id = "snap"

    class _BundleNoLcb:
        q = {"bin-a": 0.80}
        q_lcb = {}  # no replacement bound for the bin -> honest baseline fallback
        q_ucb = {}
        provenance_json = {
            "bin_topology": [{"bin_id": "bin-a", "lower_c": 24.0, "upper_c": 26.0}]
        }

    value = _replacement_q_lcb_for_candidate(_Proof(), replacement_bundle=_BundleNoLcb())
    assert value == pytest.approx(0.50)


def test_item1_adapter_records_baseline_diagnostics_not_min_join():
    """The SHADOW_VETO seam must record baseline_q_lcb_reference and NOT join the deleted
    min(proof.q_lcb_5pct, ...) against the replacement effective q_lcb."""
    src = (_SRC / "engine" / "event_reactor_adapter.py").read_text()
    assert "effective_q_lcb = min(proof.q_lcb_5pct, replacement_hook_result.effective_q_lcb)" not in src
    assert '"baseline_q_lcb_reference": float(proof.q_lcb_5pct)' in src
    assert "effective_q_lcb = replacement_hook_result.effective_q_lcb" in src


# ---------------------------------------------------------------------------
# item 5 — live_execution_mode collapse.
# ---------------------------------------------------------------------------
def test_item5_routing_tables_use_edli_live_only():
    import src.main as main

    assert "edli_live" in main.LIVE_EXECUTION_MODES
    assert "edli_live_canary" not in main.LIVE_EXECUTION_MODES
    assert "edli_live_canary" not in main.EDLI_EVENT_DRIVEN_MODES
    assert "edli_live_canary" not in main.REACTOR_MODE_BY_LIVE_STAGE
    assert main.REACTOR_MODE_BY_LIVE_STAGE["edli_live"] == "live"
    # The remaining modes that route real behavior are kept.
    for kept in ("legacy_cron", "edli_shadow_no_submit", "edli_submit_disabled_bridge"):
        assert kept in main.LIVE_EXECUTION_MODES


def test_item5_old_canary_mode_string_in_persisted_data_still_readable():
    """A persisted/config row carrying the historical 'edli_live_canary' must map to
    'edli_live' at the read boundary (data tolerance), never raise UNSUPPORTED."""
    import src.main as main

    assert main._live_execution_mode({"live_execution_mode": "edli_live_canary"}) == "edli_live"
    # A genuinely unknown mode still fails closed.
    with pytest.raises(ValueError):
        main._live_execution_mode({"live_execution_mode": "totally_unknown_mode"})


def test_item5_settings_mode_is_edli_live():
    data = json.loads(_SETTINGS.read_text())
    assert data["edli"]["live_execution_mode"] == "edli_live"


# ---------------------------------------------------------------------------
# item 6 — settlement σ-floor applies by per-cell data availability (no flag).
# When the fitted floor cell exists it widens σ; when absent it is inert; there
# is no flag path either way.
# ---------------------------------------------------------------------------
def test_item6_emos_q_floor_applies_when_cell_data_exists_inert_when_absent(monkeypatch):
    import numpy as np
    import src.calibration.emos_q_builder as qb

    class _Bin:
        def __init__(self, lo, hi):
            self.lower_c, self.upper_c = lo, hi

    bins = [_Bin(None, 24.0), _Bin(24.0, 26.0), _Bin(26.0, None)]
    members = np.array([25.0, 25.1, 24.9, 25.05, 24.95], dtype=float)

    # A predictive (mu, sigma) with a TIGHT sigma so the floor can widen it.
    monkeypatch.setattr(qb, "emos_predictive", lambda *a, **k: (25.0, 0.4))

    # Cell HAS fitted floor data (3.0 C) -> floor applies, sigma widens to >= 3.0.
    monkeypatch.setattr(qb, "settlement_sigma_floor", lambda *a, **k: 3.0)
    res_floored = qb.build_emos_q(
        city="X", season="JJA", metric="high", lead_days=1.0,
        members_native=members, unit="C", bins=bins,
        apply_settlement_floor=True, require_settlement_floor=False,
    )
    assert res_floored is not None
    _q, _mu, sigma_floored = res_floored
    assert sigma_floored == pytest.approx(3.0)

    # Cell has NO fitted floor data -> lookup returns None -> floor inert (sigma stays 0.4).
    monkeypatch.setattr(qb, "settlement_sigma_floor", lambda *a, **k: None)
    res_inert = qb.build_emos_q(
        city="X", season="JJA", metric="high", lead_days=1.0,
        members_native=members, unit="C", bins=bins,
        apply_settlement_floor=True, require_settlement_floor=False,
    )
    assert res_inert is not None
    _q2, _mu2, sigma_inert = res_inert
    assert sigma_inert == pytest.approx(0.4)


def test_item6_no_flag_gate_on_the_floor_seams():
    """The materializer and adapter seams must not read any of the deleted σ-floor flags."""
    mat = (_SRC / "data" / "replacement_forecast_materializer.py").read_text()
    adp = (_SRC / "engine" / "event_reactor_adapter.py").read_text()
    mon = (_SRC / "engine" / "monitor_refresh.py").read_text()
    for txt in (mat, adp, mon):
        assert 'get("edli_settlement_sigma_floor_enabled"' not in txt
        assert 'get("edli_settlement_sigma_floor_required"' not in txt
        assert 'get("replacement_qlcb_settlement_sigma_floor_enabled"' not in txt


# ---------------------------------------------------------------------------
# item 8 — taker FOK/FAK legality is unconditional in the execution certificate.
# ---------------------------------------------------------------------------
def test_item8_cert_builder_has_no_taker_flag_param():
    import inspect
    from src.decision_kernel.certificates.execution import (
        build_final_intent_certificate_from_actionable,
    )

    sig = inspect.signature(build_final_intent_certificate_from_actionable)
    assert "taker_fok_fak_live_enabled" not in sig.parameters


def test_item8_taker_gate_function_deleted():
    import src.strategy.live_inference.trade_score as ts

    assert not hasattr(ts, "assert_taker_live_allowed")
