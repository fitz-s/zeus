# Created: 2026-06-12
# Last reused or audited: 2026-06-12
# Authority basis: operator no-overengineering law 2026-06-12 verbatim
#   "不允许设置任何的cap，实际上消除我系统中的过度设计" + Wave-1 of
#   docs/archive/2026-Q2/operations_historical/overengineering_simplification_plan_2026-06-12.md.
"""Wave-1 antibody suite: artificial throttles stay DELETED.

These tests make the re-introduction of the deleted caps / canary gates / shadow
flags a RED test — not a note, not a doc. HONEST gates (data absent/stale,
settlement-semantics violations, identity mismatches, the operator arm) are
preserved and are NOT asserted-absent here; only the artificial throttles the
operator law forbids are pinned gone.

Deleted in Wave-1 (this file is their tombstone):
  - forecast_sharpness_gate_enabled (50/54-city zero-trade veto)
  - live_canary_enabled + edli_live_min_canary_count + edli_live_promotion_artifact_required
    + edli_arm_gate_artifact_required (canary / promotion-artifact bureaucracy)
  - k1_persist_presubmit_snapshot_enabled (provenance substrate -> unconditional)
  - no_submit_proof_limit (per-cycle proof cap -> unbounded)
  - redecision_max_per_cycle (re-decision cap -> wrapping fair cursor)
  - coverage_fairness_emit_enabled (fairness -> unconditional)
  - mainstream_agreement_reference_enabled (already decoupled annotation)
  - redecision_continuous_enabled + redecision_screen_enabled (fill-rate organ ->
    always-on when live-armed)
  - day0 family $25 notional cap (sizing = q_lcb + Kelly only)
  - canary_force_taker (mode authority twin -> proof policy only)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SETTINGS = _REPO / "config" / "settings.json"
_SRC = _REPO / "src"

# The deleted settings keys (literal scan over settings.json).
_DELETED_SETTINGS_KEYS = (
    "forecast_sharpness_gate_enabled",
    "forecast_sharpness_mae_multiplier",
    "live_canary_enabled",
    "edli_live_min_canary_count",
    "edli_live_promotion_artifact_required",
    "edli_arm_gate_artifact_required",
    "k1_persist_presubmit_snapshot_enabled",
    "no_submit_proof_limit",
    "redecision_max_per_cycle",
    "coverage_fairness_emit_enabled",
    "mainstream_agreement_reference_enabled",
    "redecision_continuous_enabled",
    "redecision_screen_enabled",
)


# ---------------------------------------------------------------------------
# Category 1: settings.json contains NONE of the deleted keys.
# ---------------------------------------------------------------------------
def test_settings_has_none_of_the_deleted_keys():
    raw = _SETTINGS.read_text()
    data = json.loads(raw)  # valid JSON (also fails loudly on corruption)
    edli = data["edli"]
    for key in _DELETED_SETTINGS_KEYS:
        assert key not in edli, f"deleted settings key reappeared in edli: {key}"
    # Literal scan: the key string must not appear as a JSON key anywhere either.
    for key in _DELETED_SETTINGS_KEYS:
        assert f'"{key}"' not in raw, f"deleted key string present in settings.json: {key}"
    # The day0 notional cap key (if it ever existed) must also be absent.
    assert "day0_family_notional_cap_usd" not in raw


# ---------------------------------------------------------------------------
# Category 2: no code in src/ READS the deleted flags (grep-style assertion).
# ---------------------------------------------------------------------------
def _iter_src_py():
    for path in _SRC.rglob("*.py"):
        yield path


def test_no_src_code_reads_the_deleted_flags():
    # A "read" is a settings/edli_cfg .get("<flag>"...) or settings[...]["<flag>"].
    read_patterns = [re.compile(r'get\(\s*["\']' + re.escape(k) + r'["\']') for k in _DELETED_SETTINGS_KEYS]
    offenders: list[str] = []
    for path in _iter_src_py():
        text = path.read_text()
        for pat, key in zip(read_patterns, _DELETED_SETTINGS_KEYS):
            if pat.search(text):
                offenders.append(f"{path.relative_to(_REPO)} reads {key}")
    assert not offenders, "deleted flags are still read in src/:\n" + "\n".join(offenders)


def test_no_src_code_references_deleted_cap_or_canary_symbols():
    forbidden = (
        "_DAY0_FAMILY_NOTIONAL_CAP",
        "_day0_family_notional_cap_usd",
        "day0_headroom_usd",
        "_edli_canary_force_taker_provider",
        "canary_force_taker",
        "_K1_PERSIST_PRESUBMIT_FLAG",
    )
    offenders: list[str] = []
    for path in _iter_src_py():
        text = path.read_text()
        for sym in forbidden:
            # Skip lines that merely document the deletion (comments mentioning DELETED/Wave-1).
            for line in text.splitlines():
                if sym in line and "DELETED" not in line and "Wave-1" not in line and "no-caps" not in line:
                    offenders.append(f"{path.relative_to(_REPO)}: {line.strip()[:90]}")
    assert not offenders, "deleted cap/canary symbols still referenced:\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# Category 3: the redecision screen registers / runs whenever live conditions
# hold — there is NO flag path. The job self-gates on (enabled + event_writer +
# reactor_mode==live), never on a redecision_screen_enabled flag.
# ---------------------------------------------------------------------------
def test_redecision_screen_has_no_flag_gate_only_live_armed():
    src = (_SRC / "main.py").read_text()
    # The screen job body must NOT read a redecision_screen_enabled flag.
    assert 'get("redecision_screen_enabled"' not in src
    assert 'get("redecision_continuous_enabled"' not in src
    # It self-gates on the live-armed conditions instead.
    assert 'reactor_mode", "live_no_submit")) != "live"' in src or 'reactor_mode") != "live"' in src
    # The continuous re-decision block is unconditional (no flag wrapper).
    assert 'get("redecision_max_per_cycle"' not in src


# ---------------------------------------------------------------------------
# Category 4: fair cursor — MORE than the former cap of families all get
# enqueued within bounded cycles (the wrapping round-robin never drops a tail).
# ---------------------------------------------------------------------------
def _fake_rows(n: int) -> list[dict]:
    return [
        {
            "city": f"city{i:03d}",
            "target_local_date": "2026-06-20",
            "temperature_metric": "high",
            "readiness_status": "LIVE_ELIGIBLE",
            "snapshot_city": f"city{i:03d}",
            "snapshot_target_date": "2026-06-20",
            "snapshot_temperature_metric": "high",
        }
        for i in range(n)
    ]


def test_fair_cursor_enqueues_all_families_within_bounded_cycles():
    from src.events.triggers.forecast_snapshot_ready import CoverageFairnessRequest

    # 2x the former cap (200) worth of families, a modest per-cycle batch.
    n_families = 420
    batch = 60
    rows = _fake_rows(n_families)

    seen: set[str] = set()
    # ceil(420/60) = 7 cycles is the minimum; allow a small margin and assert
    # FULL coverage well within a bounded number of cycles.
    for cycle in range(7):
        picked = CoverageFairnessRequest(limit=batch, cycle_index=cycle).select_rows(rows)
        # Each cycle yields a NON-empty batch (the wrapping cursor is never dark
        # while families exist).
        assert picked, f"cycle {cycle} emitted nothing (cursor went dark — tail dropped)"
        for r in picked:
            seen.add(r["city"])
    assert seen == {f"city{i:03d}" for i in range(n_families)}, (
        f"fair cursor dropped families: covered {len(seen)}/{n_families} within 7 cycles"
    )


def test_fair_cursor_never_goes_empty_past_the_list_end():
    """The non-wrapping bug: cycle_index*limit past the list end emitted nothing.
    The wrapping cursor must still emit a full batch at a large cycle index."""
    from src.events.triggers.forecast_snapshot_ready import CoverageFairnessRequest

    rows = _fake_rows(30)
    # cycle_index 100 * limit 10 = 1000 >> 30; the old slice would be empty.
    picked = CoverageFairnessRequest(limit=10, cycle_index=100).select_rows(rows)
    assert len(picked) == 10


# ---------------------------------------------------------------------------
# Category 5: no-submit proofs are NOT truncated at 250 — the cap is gone and the
# reactor processes the pending set unbounded (the 30s wall-clock budget is the
# only honest bound).
# ---------------------------------------------------------------------------
def test_no_submit_proof_limit_is_unbounded_in_main():
    src = (_SRC / "main.py").read_text()
    # The cap read is gone and proof_limit is the unbounded sentinel None.
    assert 'get("no_submit_proof_limit"' not in src
    assert '"no_submit_proof_limit"' not in src
    assert "proof_limit = None" in src
    # process_pending is still called with limit=proof_limit (None => drain all).
    assert "limit=proof_limit" in src


# ---------------------------------------------------------------------------
# Category 6: the forecast-sharpness EDGE veto is gone (MarketAnalysis no longer
# suppresses edges; the gate method is deleted).
# ---------------------------------------------------------------------------
def test_forecast_sharpness_edge_veto_is_deleted():
    from src.strategy.market_analysis import MarketAnalysis

    assert not hasattr(MarketAnalysis, "_sharpness_suppresses_edges"), (
        "the forecast-sharpness edge-suppression gate must be deleted (50/54-city "
        "zero-trade veto; if sharpness ever matters it lives inside calibrated q)"
    )
    ma_src = (_SRC / "strategy" / "market_analysis.py").read_text()
    assert "forecast_sharpness_gate_suppressed" not in ma_src
