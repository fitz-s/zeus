# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Lifecycle: created=2026-04-29; last_reviewed=2026-04-30; last_reused=2026-04-30
# Purpose: Lock live runtime Monte Carlo precision floors and assumptions/config synchronization.
# Reuse: Run for runtime probability-chain config, settings.json, assumptions.json, or LAW 4 changes.
# Authority basis: docs/reference/zeus_calibration_weighting_authority.md LAW 4
#                  forbidden move 7 — "NEVER use n_mc < 5000 in live runtime
#                  evaluator. Per-trade precision needs tighter SE; single-snapshot
#                  decisions cannot rely on N_pairs leverage."
"""Antibody test — runtime n_mc never falls below LAW 4 floor.

Pins config/settings.json::ensemble.n_mc and day0.n_mc to >= 5000 (LAW 4
forbidden move 7 floor). Current production value 10000 (preferred). The test
fires if a future config edit accidentally regresses below the floor — the
silent class of failure where per-trade probability SE rises 30%+ without any
visible error.

LAW 4 derivation (zeus_calibration_weighting_authority.md L160-191):
- SE(per-snapshot p_raw) ≈ √(p(1-p)/n_mc)
- At p=0.5: n_mc=5000 → SE=0.0071, n_mc=10000 → SE=0.005
- Per-trade precision dominates marginal trade decisions where edge ≈ noise floor

This test is the first of LAW 4's two prescribed antibodies. Companion test
test_rebuild_n_mc_default_bounded.py is queued as a separate backlog item;
runtime floor is the more critical of the two (training already has N_pairs
leverage to absorb sub-floor n_mc).
"""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

LAW_4_FLOOR = 5000


def test_ensemble_n_mc_at_or_above_law4_floor():
    """ensemble.n_mc >= 5000 (LAW 4 forbidden move 7 floor for live runtime)."""
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    n_mc = settings["ensemble"]["n_mc"]
    assert isinstance(n_mc, int), f"ensemble.n_mc must be int, got {type(n_mc).__name__}"
    assert n_mc >= LAW_4_FLOOR, (
        f"ensemble.n_mc={n_mc} regressed below LAW 4 floor ({LAW_4_FLOOR}). "
        f"Per-trade SE would rise to {((0.25/n_mc)**0.5 if n_mc > 0 else float('inf')):.4f} "
        f"vs the floor's 0.0071 (at p=0.5). LAW 4 forbidden move 7: 'NEVER use "
        f"n_mc < 5000 in live runtime evaluator.' Update settings.json or get "
        f"explicit operator authorization with documented evidence basis."
    )


def test_day0_n_mc_at_or_above_law4_floor():
    """day0.n_mc >= 5000 (Day0 nowcast inherits same per-trade precision floor)."""
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    n_mc = settings["day0"]["n_mc"]
    assert isinstance(n_mc, int), f"day0.n_mc must be int, got {type(n_mc).__name__}"
    assert n_mc >= LAW_4_FLOOR, (
        f"day0.n_mc={n_mc} regressed below LAW 4 floor ({LAW_4_FLOOR}). "
        f"Day0 single-snapshot decisions cannot rely on N_pairs leverage; "
        f"floor enforcement matches ensemble.n_mc per LAW 4 forbidden move 7."
    )


def test_assumptions_registry_in_sync_with_settings():
    """state/assumptions.json mc_count_* must match config/settings.json n_mc.

    Drift between these two surfaces produces the 'ASSUMPTION MISMATCH' WARNING
    at boot (src/main.py L563). Treating the warning as load-bearing — silent
    drift between declared assumptions and actual config is a Fitz-Constraint-#4
    data-provenance failure.
    """
    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    assumptions = json.loads((PROJECT_ROOT / "state" / "assumptions.json").read_text(encoding="utf-8"))

    assert assumptions["signal"]["mc_count_entry"] == settings["ensemble"]["n_mc"], (
        f"Assumption drift: assumptions.signal.mc_count_entry="
        f"{assumptions['signal']['mc_count_entry']} != settings.ensemble.n_mc="
        f"{settings['ensemble']['n_mc']}. Update state/assumptions.json so the "
        f"boot-time mismatch warning does not fire."
    )
    assert assumptions["signal"]["mc_count_monitor"] == settings["ensemble"]["n_mc"], (
        f"Assumption drift: assumptions.signal.mc_count_monitor="
        f"{assumptions['signal']['mc_count_monitor']} != settings.ensemble.n_mc="
        f"{settings['ensemble']['n_mc']}."
    )
