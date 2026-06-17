# Q-Kernel live hotfix + forecast-supply diagnosis — 2026-06-15

Created: 2026-06-15
Last reused or audited: 2026-06-15
Authority basis: GOAL #83 (continuous settlement-graded positive-after-cost fills); live daemon
`live/iteration-2026-06-13`, qkernel spine flag ON since febc328c30.

## PART 1 — Live hotfix LANDED + verified (day0 churn / requeue-backlog)

**Defect (monitor b9w56vec6):** every reactor cycle requeued day0 families with
`QKERNEL_SPINE_NO_TRADE:QKERNEL_DAY0_NOT_WIRED` + a reactor ERROR "UNKNOWN money-path reason
base → fail-open TRANSIENT". The spine bridge reads no day0 observation, so the prior cut
hard-blocked day0 to a typed no-trade — which both (a) killed the day0 revenue lane and
(b) infinitely requeued, accreting a **47,000-family transient-requeue backlog** that
starved reactor throughput (`retried=47483` per cycle).

**Two fixes (commit 37407d1887 → cherry-picked to live 04c9af6eb6):**
1. `event_reactor_adapter.py` seam: `if _spine_flag_on and _spine_eligible_event and not
   _is_day0_event:` — day0 falls through to the legacy `_selected_candidate_proof` lane
   (keeps day0 trading; spine never prices a day0 family). Removed the dead
   `NO_TRADE_QKERNEL_DAY0_NOT_WIRED` import.
2. `reactor.py` classifier: registered base `QKERNEL_SPINE_NO_TRADE` as **TERMINAL**. Every
   spine no-trade is consume-this-event (a fresh FORECAST_SNAPSHOT_READY arrives next cycle,
   exactly like legacy FDR_REJECTED / TRADE_SCORE_NON_POSITIVE) — NOT a requeue. Genuine
   intra-cycle execution races (PRICE_MOVED / MODE_FLIPPED) keep their own transient bases.

**Verified post-restart (daemon PID 61472, restarted 02:09:59Z):**
- `QKERNEL_DAY0_NOT_WIRED` requeues at/after 02:10:00Z = **0** (was every cycle).
- `retried` 47483 → ~5 per cycle (backlog drained).
- reactor cycle healthy (processed=3, dead=0); only legit `EXECUTABLE_SNAPSHOT_BLOCKED`
  freshness requeues remain.
- Tests: spine routing + money_path **193 passed**; blockers BLOCKER-3 updated to assert
  day0→legacy **10 passed**; classifier unit-verified.

**Plus:** day0/settlement test suite 12 failing → 0 (84 passed), landed on live (dab9bf8beb).

## PART 2 — Spine integration is SOUND (not the blocker)

- Bridge restamps `trade_score = selected.point_ev`; spine only selects legs with
  `edge_lcb>0` → `point_ev>0` → **survives** the downstream `trade_score<=0` gate
  (`event_reactor_adapter.py:2890`). So a spine selection is NOT killed by the legacy scalar gate.
- day0 correctly excluded; `q_source="qkernel_spine"` overlaid for receipts.

## PART 3 — The REAL goal blocker: forecast families never reach the spine

`proof_accepted=0` across **all 63 cycles today**; system traded heavily 06-12 (95 exits) then
went near-silent (06-13: 2, today: 2 `opening_inertia` entries only). The EDLI forecast spine
has produced **zero** forecast decisions.

Pipeline ground-truth:
- Snapshot capture ALIVE: `executable_market_snapshots` in **zeus_trades.db** = 4.28M rows,
  42,686 captured since 06:00Z, freshest = now. (NOTE: zeus-world.db copy is EMPTY — the live
  table is in zeus_trades.db.)
- Past-date queue pollution (06-01..06-14, ~40k Gamma-empty lines/day) is GONE post-restart —
  venue-closed skip works; all 92 post-restart FDR-gate lines are 06-15 only.
- Polymarket Gamma DOES have open 06-15 markets. Direct slug probes
  (`/events?slug=highest-temperature-in-<city>-on-june-15-2026`):
  - events=1: beijing(control,binds), **chicago, austin, qingdao, dallas** ← these are in
    Zeus's FDR-gate-stuck list DESPITE the market existing → **binding gap**.
  - events=0: lagos, zhengzhou, auckland ← genuinely no market; "empty" is correct (minor
    terminalization-gap, not a fill blocker).
- Zeus slug builder (`src/main.py:3704-3715`): `slug_fragment = city_obj.slug_names[0]`,
  `slug_date = strftime("%B-%-d-%Y").lower()` (→ `june-15-2026`, CORRECT). Suspect:
  `slug_names[0]` for the stuck-but-live cities (or a time-boxed/un-harvested Gamma future)
  differs from the live slug → empty fetch → stuck at FDR gate → never gets bin identity →
  never reaches the spine.

## PART 4 — Refinement (later cycles, ~02:20-02:30Z)

As 06-15 markets posted, forecast families began BINDING (n=22 candidates) and reaching
evaluation — `processed` rose 3→6/cycle. But the reason is
`EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb...` — the LEGACY
proof-generation gate (q_lcb>price; task #66). This reason is emitted ONLY when the legacy
branch ran (`_spine_no_trade_reason is None`), i.e. all candidate proofs were pre-rejected at
the proof-generation stage (capital_efficiency), upstream of the spine selection seam. So the
binding constraint is the per-proof `capital_efficiency` (q_lcb>price) gate finding no
positive-edge candidate — the SAME honest no-edge the rebuild was meant to fix by correcting
the q-engine. Open question for next cycle: does the spine's OWN q (predictive distribution)
find edge where the per-proof legacy q_lcb did not, and is the spine actually evaluated on
these families or short-circuited by the empty survivor set? There is ONE seam
(`_build_event_bound_no_submit_receipt_core`, both calls inside it) — no parallel path.

## PART 5 — Observability gap (separate)

`edli_no_submit_receipts` (zeus-world.db) last write = **2026-06-12** — no-submit decision
receipts have not persisted since 06-12 (the silent no-submit-lane mode, task #54 class).
Decision outcomes are only visible via the reactor cycle-result log line, not the receipt
table. Worth repairing for settlement-graded EV observability (GOAL #83 needs receipts).

`trade_decisions` (zeus_trades.db): heavy activity 06-12 (95 exits) → near-silent 06-13+
(06-13: 2 exited, today: 2 `opening_inertia` buy_yes entries $1.81/$2.91). The EDLI forecast
spine specifically has produced ZERO fills.

## PART 6 — SETTLEMENT-GRADED ARM REPLAY (decisive; 651 settled families)

Ran the validated `scripts/qkernel_arm_replay.py` (read-only, mode=ro) over the real settled
cohort. The rebuilt q-engine is **calibration-PROVEN**:
- **Center FIXED**: book-wide mean(μ*−realized) = −0.50 (PASS <1.0). The prior +2.8°C warm
  bias that caused the wrong trades is GONE.
- **Point-q CALIBRATED**: pooled modal predicted 0.304 vs realized 0.313 (gap +0.009);
  on-diagonal in the 0.30 bucket (n=588: pred 0.293 vs real 0.298).
- **σ HONEST**: predictive_rss std(z)=0.93, PIT std/uniform=0.92, σ/RMSE=0.99 (high n=604
  σ/RMSE=1.00; low n=93 σ/RMSE=0.97). Not over- or under-dispersed.
- **q_lcb**: pooled mean 0.194 vs realized 0.313, coverage_ratio 1.61. This is a 5th-pctile
  LOWER bound — coverage>1 is BY DESIGN (a lower bound must sit below realized). NOT a
  recalibration target; narrowing it to lift q_lcb above price would be the forbidden
  "loosen q_lcb to fill orders" (operator law). Left untouched.

**The ONE gate the replay could NOT grade — and the actual goal metric:**
- **§5 AFTER-COST EV-BY-CLASS = DATA-COVERAGE-LIMITED**: books=703, graded=570, but the
  snapshot EV table carries **no per-bin label**, so settlement-graded after-cost EV by
  outcome class could not be computed. This is task #117 (the HARD deploy gate) and the
  operator's literal success metric (positive after-cost EV). Everything UPSTREAM of it is
  proven; the EV proof itself is unmade because the per-bin-label join is missing.

## CORRECTED conclusion
The q-engine rebuild WORKS (center/point-q/σ proven on 651 settled families). Zero fills is
NOT "the q is still broken" and NOT "loosen the q_lcb". The unproven link is whether honest
point-q (≈0.30, calibrated) exceeds price+cost on a tradeable class often enough for positive
after-cost EV — which requires grading §5. The `capital_efficiency` gate (q_lcb_5pct > price)
is a deliberate 5%-LCB conservatism; whether that specific confidence level is the right entry
criterion (vs a still-honest point-q>price+cost criterion) is an OPERATOR decision, not a
unilateral loosen. The honest next step is to GRADE the after-cost EV (wire per-bin labels
into the EV join), which either proves tradeable alpha exists (→ the entry-criterion question
becomes live and operator-gated) or proves the markets are efficiently priced at the modal bin
(→ edge must come from a different class/lead, not from relaxing a gate).

## NEXT (the wire to make RUN)
Single-family end-to-end trace of ONE live-market city (e.g. Beijing or Chicago 06-15):
bind (bin identity / condition_id) → fresh executable snapshot → reactor decision cycle →
spine decision (q_source, reason). Confirm whether the bound live-market families reach the
spine and what it decides; fix the slug_names/harvest binding gap for the stuck-but-live
cities (Chicago/Austin/Qingdao/Dallas). That unblocks forecast families → first real
forecast spine fill. NO new gate/cap; honest binding fix only.
