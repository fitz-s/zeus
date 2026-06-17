# Live state — VERIFIED ground truth (2026-06-15) + the open decision

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: direct live inspection (PID 21155/56786 stack dump, logs/zeus-live.{log,err},
  state/zeus-world.db, config/settings.json, scripts/measure_arm_gate_settlement.py +
  scripts/sigma_kernel_holdout_replay.py runs). READ-ONLY diagnosis. This doc CORRECTS a
  compacted-session summary that was wrong on several load-bearing points.

## Corrections to the prior (compacted) summary — do NOT trust that summary
- The summary's planned fix ("forecast runs ~1.1°C cold → apply per-city `b_shrunk` in the
  event_reactor_adapter cheap-stash") is **WRONG and must not be deployed.** With
  `edli_emos_sole_calibrator_enabled:true` (settings.json:86) and `edli_bias_correction_enabled:false`
  (:85), the EMOS predictive N(mu,sigma) is the calibrator on the primary lane and the legacy bias
  maze is INERT. Bolting a legacy `b_shrunk` center-shift into the cheap-stash is a parallel-authority
  shim fighting EMOS-sole — forbidden by the 大一统 one-authority law. The center is NOT the proven defect.
- Config keys are nested under `feature_flags`, not top-level. `venue_commands` lives in
  state/zeus-world.db (NOT zeus_trades.db, which is empty). Positions: position_current /
  venue_trade_facts (state/zeus-world.db).

## Verified live facts
1. **Daemon self-heals.** launchd `com.zeus.live-trading` has `KeepAlive:1`; it auto-restarts on crash.
   The recurring symptom is the decision lane overrunning its interval (123% CPU, apscheduler
   "maximum number of running instances reached" on `_edli_continuous_redecision_screen_cycle`), NOT
   total death. SIGUSR1 has `chain=True` → it TERMINATES the daemon after dumping (one good dump =
   one restart). Do not SIGUSR1 the live daemon casually.
2. **Live decision path = the qkernel spine** (`qkernel_spine_enabled:true`, settings.json:270). It
   behaves CORRECTLY on the favorite-NO class: a cost-0.999 NO gets `dU<0`, `stake=0` — it REFUSES the
   loss trade the operator complained about. The spine is not making the bad trade.
3. **The system trades ~nothing.** Over 753 reactor cycles in-window: proof_accepted=3, rejected=5639,
   0 LIVE ORDER lines. Recent orders in edli_live_order_projection carry `probability_authority=replacement_0_1`
   (legacy), not the spine.
4. **The spine's only positive-edge candidates are non-modal YES** (q≈0.18 @ cost 0.09, edge_lcb +0.03,
   dU>0, stake>0 — the SAME two tokens f308a2d7/aed9a31b recurring). They PASS the spine's economic
   filter but die at `_select` step 1 (`after_direction`, family_decision_engine.py:876) because
   `direction_law_ok` (line 405) makes YES legal only on the modal bin. Family → NO_POSITIVE_EDGE_CANDIDATE.
5. **Settlement truth, legacy cohort (≤06-12): capital-weighted ROI −13.3%, ARM DENIED**
   (scripts/measure_arm_gate_settlement.py; row-rate 54% is row-democracy only; several cities −100%).
   The belief that historically traded LOST money once sized. This is the real reason the old system lost.
6. **The ARM validator is BLIND to the live spine.** It reads `edli_no_submit_receipts`, which is DEAD
   since 2026-06-12T12:12 (0 rows after). So it grades stale legacy beliefs, not the live spine. The
   spine persists NO gradeable per-family q with identity → its belief has never been settlement-graded.
7. **The EMOS/legacy q-shape is settlement-proven OVER-DISPERSED** (scripts/sigma_kernel_holdout_replay.py,
   temporal holdout split 06-11, n=270 test cells): ring realized/expected ratios dist0=2.21, dist1=1.95,
   dist2=1.06, dist≥4=0.37, **tail=0.09** — far too much tail mass. A tighter refit (k≈1.02, w≈0) is
   out-of-sample materially better on the tails (dist≥4 0.37→0.875). Over-dispersed tails MANUFACTURE
   false non-modal edge. BUT this grades the EMOS `k/w` form, **not** the live spine's `joint_q` (spine σ
   = `sigma_authority.build_sigma` realized-floor, a different regime).

## The open decision (pending evidence)
Relaxing the direction-law to admit the spine's non-modal YES is justified ONLY if the SPINE's q
(joint_q) is settlement-calibrated on that tail. If the spine's predictive is over-dispersed like EMOS,
that "edge" is a false artifact and the direction-law is correctly guarding — relaxing would trade losers.
This is undecidable without grading the SPINE's actual q.

→ DECISIVE measurement IN FLIGHT: settlement-grade the live spine's `build_predictive_distribution →
build_joint_q` over settled cells (06-08..06-15), ring-distance ratios + after-cost EV by direction×ring,
isolating the non-modal YES class. Verdict file: `spine_q_settlement_grade_2026-06-15.md`.
- If spine tail is calibrated (ratio≈1, non-modal YES after-cost EV>0) → relax direction-law (settlement-justified).
- If spine tail is over-dispersed (ratio<1, non-modal YES after-cost EV<0) → tighten the spine σ/shape first; do NOT relax.
Either way: NO gate relaxation and NO σ change deploys without the settlement verdict (operator law:
settlement-validated, no loosening to fill orders, no fabrication).

## What is NOT the fix
- Not the cheap-stash `b_shrunk` center shift (fights EMOS-sole).
- Not the DAY0_ORACLE INCONCLUSIVE spew — that does NOT pause families (only a SET divergence flag pauses;
  INCONCLUSIVE just sets a short retry memo). It is log noise, not the throughput blocker.
- Not "relax direction-law" on #121's moderate evidence alone — #121 graded the LEGACY posterior q, and
  gated its own recommendation on fixing modal over-dispersion first. The live spine must be graded directly.
