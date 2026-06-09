#!/usr/bin/env python3
# Lifecycle: created=2026-06-08; last_reviewed=2026-06-08; last_reused=2026-06-08
# Purpose: Read-only JSON preflight check (no DB/network/writes) that answers two questions: is the current flag posture coherent, and what is the single next flag to flip?
# Reuse: Run anytime against any deployment root; safe to run on live. Inspect config/settings.json and promotion_evidence.json before relying on its output.
# Created: 2026-06-08
# Last reused or audited: 2026-06-08
# Authority basis: docs/the_path/RESTART_RUNBOOK.md + CONTINUITY_AND_WIRING.md (flag ladder),
#   replacement_forecast_runtime_policy.py (evidence gate thresholds), operator directive
#   2026-06-08 ("开关太多了，需要打开哪一个我也不知道" — collapse the flag sprawl to one self-checked next-action).
"""
PREFLIGHT RESTART CHECK — the antibody for "too many switches".

Reads ONLY json (config/settings.json + the promotion_evidence.json); NO sqlite, NO
network, NO writes — safe to run anytime, against any deployment root. It answers the
two questions the operator actually has:
  1. Is the current flag posture COHERENT? (no fusion-without-data, no authority-without-evidence)
  2. What is the SINGLE next switch to flip, and is its gate green yet?

Run:  python3 scripts/preflight_restart_check.py --root /Users/leofitz/zeus
Exit code 0 = coherent; 2 = an incoherent/hazard combo is set (read the CRITICAL lines).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ---- the evidence gate (mirror of replacement_forecast_runtime_policy thresholds) ----
EVIDENCE_GATE = {
    "official_days": (">=", 5),
    "official_rows": (">=", 250),
    "q_lcb_coverage": (">=", 0.95),
    "after_cost_pnl": (">", 0.0),
    "nested_walk_forward_passed": ("is", True),
    "same_clob_replay_passed": ("is", True),
    "fee_depth_fill_evidence_passed": ("is", True),
    "product_specific_refit_passed": ("is", True),
    "anti_lookahead_violations": ("==", 0),
    "source_availability_violations": ("==", 0),
    "unresolved_regression_clusters": ("==", 0),
    "unit_pnl_only": ("is", False),
}


def _cmp(op, val, thr):
    if val is None:
        return False
    if op == ">=":
        return val >= thr
    if op == ">":
        return val > thr
    if op == "==":
        return val == thr
    if op == "is":
        return val is thr or val == thr
    return False


def _evidence_blockers(pe: dict) -> list[str]:
    out = []
    for k, (op, thr) in EVIDENCE_GATE.items():
        v = pe.get(k)
        if not _cmp(op, v, thr):
            out.append(f"{k}={v!r} (need {op} {thr!r})")
    return out


# ---- the ladder: ordered rungs, each ONE flag + its gate ----
# rung = (flag, human, gate_fn(state)->(ok, why))
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/Users/leofitz/zeus", help="deployment root (config/ + state/)")
    args = ap.parse_args()

    cfg_p = os.path.join(args.root, "config", "settings.json")
    pe_p = os.path.join(args.root, "state", "replacement_forecast_shadow", "promotion_evidence.json")
    cfg = json.load(open(cfg_p)) if os.path.exists(cfg_p) else {}
    pe_doc = json.load(open(pe_p)) if os.path.exists(pe_p) else {}
    pe = (pe_doc.get("promotion_evidence") or {}) if isinstance(pe_doc, dict) else {}

    # Each flag group lives in its own config sub-object — NOT at the top level.
    # Reading from cfg.get(name) always returns None (wrong posture/next-flip).
    edli = cfg.get("edli_v1", {})
    feature_flags = cfg.get("feature_flags", {})

    def f_edli(name, default=False):
        return bool(edli.get(name, default))

    def f_ff(name, default=False):
        return bool(feature_flags.get(name, default))

    capture = f_edli("replacement_0_1_u0r_multimodel_capture_enabled")
    fusion = f_edli("replacement_0_1_u0r_fusion_enabled")
    eb = f_edli("replacement_0_1_eb_bias_correction_enabled")
    smooth = f_edli("replacement_0_1_member_vote_smoothing_enabled")
    qlcb = f_edli("replacement_qlcb_settlement_sigma_floor_enabled")
    auth = f_ff("openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled")
    kelly = f_ff("openmeteo_ecmwf_ifs9_aifs_soft_anchor_kelly_increase_enabled")
    flip = f_ff("openmeteo_ecmwf_ifs9_aifs_soft_anchor_direction_flip_enabled")
    arm = f_edli("edli_live_operator_authorized")

    ev_blockers = _evidence_blockers(pe)
    ev_ok = not ev_blockers

    print("=" * 72)
    print("PREFLIGHT RESTART CHECK   root=%s" % args.root)
    print("=" * 72)

    # ---- posture ----
    if not capture and not fusion:
        stage = "0  SHADOW (single-anchor only; U0R not active)"
    elif capture and not fusion:
        stage = "1  ACCRUING (multi-model data persisting; posterior unchanged)"
    elif fusion and not auth:
        stage = "2  SHADOW-FUSION (T2_BAYES posterior computed, NO live trades)"
    elif auth and not arm:
        stage = "3  LIVE-AUTHORITY (resolver may grant; arm key still closed)"
    else:
        stage = "4  ARMED (real money path open)"
    print("POSTURE  : stage", stage)
    print("EVIDENCE : gate %s%s" % (
        "PASS" if ev_ok else "FAIL",
        "" if ev_ok else "  blockers=" + "; ".join(ev_blockers)))

    # ---- coherence / hazards ----
    issues = []
    if fusion and not capture:
        issues.append(("WARN", "fusion ON but capture OFF — forward history will go stale; "
                               "ensure raw_model_forecasts was seeded (scripts/backfill_u0r_history_from_b0.py)"))
    if (auth or kelly or flip) and not ev_ok:
        issues.append(("CRITICAL", "soft_anchor trade_authority/kelly/flip ON while evidence gate FAILS — "
                                   "resolver footgun. Set these FALSE until the gate passes (Fault C)."))
    if arm and not ev_ok:
        issues.append(("CRITICAL", "edli_live_operator_authorized ON while evidence gate FAILS — "
                                   "real money path open without promotion evidence. CLOSE the arm."))
    if (auth or arm) and not fusion:
        issues.append(("WARN", "authority/arm ON but fusion OFF — you would trade the single-anchor path, "
                               "not the proven U0R fusion."))

    print("-" * 72)
    if issues:
        print("COHERENCE:")
        for sev, msg in issues:
            print("  [%s] %s" % (sev, msg))
    else:
        print("COHERENCE: OK — no incoherent/hazard flag combo set.")

    # ---- the single next action ----
    print("-" * 72)
    if not capture:
        nxt = ("replacement_0_1_u0r_multimodel_capture_enabled = TRUE",
               "start multi-model accrual (shadow, zero trading effect). "
               "Pair with: run scripts/backfill_u0r_history_from_b0.py --db <forecasts.db> to seed history NOW.")
    elif not fusion:
        nxt = ("replacement_0_1_u0r_fusion_enabled = TRUE (+ eb_bias + member_vote_smoothing)",
               "history is seeded/accruing -> fusion reaches T2_BAYES. Shadow first; verify "
               "posterior_method shows the_path_u0r_fusion before granting authority.")
    elif not qlcb:
        nxt = ("replacement_qlcb_settlement_sigma_floor_enabled = TRUE",
               "license after >=30 settled per-band coverage passes (only-lowers, conservative).")
    elif not ev_ok:
        nxt = ("(blocked) regenerate/satisfy the evidence gate",
               "trade_authority cannot be granted until the gate passes. "
               "Blockers: " + "; ".join(ev_blockers) + ". "
               "NOTE: the retrospective backtest (14,370 settled, HIGH win 63.9%) is settlement-grade "
               "evidence — operator decision whether to regenerate promotion_evidence from it vs forward accrual.")
    elif not (auth and kelly and flip):
        nxt = ("openmeteo_ecmwf_ifs9_aifs_soft_anchor_{trade_authority,kelly_increase,direction_flip}_enabled = TRUE",
               "evidence gate PASSES -> safe to grant authority (resolver now enforces it).")
    elif not arm:
        nxt = ("edli_live_operator_authorized = TRUE  (FINAL ARM — operator only)",
               "open the real money path. Confirm shadow candidates match internal + mainstream forecast first.")
    else:
        nxt = ("(none)",
               "fully armed. Monitor after-cost EV/PnL/log-growth, q_lcb coverage, "
               "drawdown, fill quality, and price-bucketed win-rate. "
               "Raw win-rate alone is not the capital objective for prediction-market tokens "
               "(a 0.90 token at 60% win = -0.30 EV; a 0.20 token at 40% win = +0.20 EV).")
    print("NEXT FLIP: %s" % nxt[0])
    print("           %s" % nxt[1])
    print("=" * 72)

    return 2 if any(s == "CRITICAL" for s, _ in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
