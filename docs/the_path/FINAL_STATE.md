# The Path — Final State & Redeploy Guide (branch thepath/audit-realign, 13 commits off live e5e5f022ee)

Complete end-to-end. Every layer flag-gated (flag-OFF byte-identical), settlement-proven where provable, mainline (executor.py/cycle_runtime.py trading path) byte-identical. Live profit remains forward-gated (operator redeploy + settled markets — unconjurable).

## What shipped (the money path, download→order)
- **Capture:** U0R multi-model fail-soft capture (OM globals + ICON-D2 EU + AROME FR) alongside the AIFS+0.1 anchor.
- **Forecast (U0R-Bayes, the core):** universal 0.1 anchor (prior) + EB per-city bias + decorrelated globals (likelihoods) + Ledoit-Wolf Bayesian shrink-to-equal fusion + ICON-D2 EU regional expert (in-polygon, lead≤1, dedup, zero-leak) + member-vote smoothing (kills the zero-prior veto). **Proven on VERIFIED settlement: ~15% Brier (A0→C1), all 5 proof targets PASS; un-hittable 38%→0%.**
- **Calibration:** q_lcb settlement-sigma floor (fixes 3.2× underdispersion) + EB over-correction guard + EMOS product-keyed.
- **Day0:** obs_available_at clock + lane activation (identity fit; operator runs the persist script).
- **Execution/safety:** single evidence gate (flag-alone can't grant live authority) + operator-arm + direction-law recheck + buy_no-hatch closed + complement-immunity + tiny caps ON + H3 staleness + H4 settlement-identity + ZEUS-NOBYPASS-1 guard + full e2e order trace (forecast→FILL SQL-reconstructable).

## Deploy posture (redeploy this; net live delta = caps tighten, nothing loosens)
All edge capabilities default-OFF (flag-OFF = byte-identical):
`replacement_0_1_u0r_fusion_enabled=false` · `replacement_0_1_eb_bias_correction_enabled=false` · `replacement_0_1_member_vote_smoothing_enabled=false` · `replacement_qlcb_settlement_sigma_floor_enabled=false` · soft_anchor trade_authority/kelly/flip now **evidence-gated** (flags alone inert) · `edli_live_operator_authorized=false` · tiny_live caps **TRUE** (tightening). Operator action: run `scripts/persist_day0_horizon_identity_fit.py` on LIVE to start the obs clock. Apply FIX-2b OperatorArm patch (proposed, mainline-safe) if arming canary.

## Promotion ladder (each shadow→promote on forward VERIFIED settlement; reserved for operator)
1. **U0R fusion** (`u0r_fusion_enabled`) — the ~15% Brier core. Shadow: compare fused vs single-anchor on forward settled; promote when the proper-score win replicates on ≥30 live settled markets (not the proxy cohort).
2. **member-vote smoothing** — un-hittable kill is structural (generalizes by construction); promote with fusion. (Pre-existing `test_aifs_prior_uses_dirichlet_floor.py` wants it unconditional — decide flag-gated-interim vs always-on after the cohort confirms.)
3. **q_lcb floor** — license after ≥30 settled markets pass per-band coverage (realized ≥ q_lcb).
4. **ICON-D2 EU regional expert** — real + zero-leak but small (sub-1pp) + lead-1-only; promote after EU forward settled confirms; low priority.
5. **0.1 live authority** — only when `promotion_evidence.json` passes its own gate (currently denies on 4 codes).

## The one unconjurable input
Live profit (after-cost win-rate on traded markets) needs the live path to accrue settled markets. First replacement fills 2026-06-06; the buy_no book (06-07/08/09) settles now→2 days. OBSERVE→MEASURE→PROMOTE turns on those settlements; no code produces them.

## Deferred (flagged, non-blocking)
Real Day0 holdout fit (identity fit is the interim) · `day0_extreme` dissemination-lag constants · AIFS-as-feature (E0/E1 needs an AIFS column re-fetch) · Student-t serving (v2 shadow) · LOW-metric proof beyond Paris · C3/H1 type-newtype antibodies · `day0_extreme` MAX(imported_at) backfill fix.
