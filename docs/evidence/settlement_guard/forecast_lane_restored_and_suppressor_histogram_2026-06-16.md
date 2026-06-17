# Forecast alpha lane: `_math` regression FIXED+DEPLOYED, and the real cross-suppressor histogram

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: operator standing goal (continuous settlement-graded POSITIVE-after-cost
  alpha) + RULE 1 (a suppression is OUR defect until settlement proves otherwise).
- Supersedes the fill-lane sections of `world_db_bloat_prune_and_forecast_lane_diagnosis_2026-06-16.md`
  (that doc's "PARTIALLY_MATCHED blocks family" was a real but SECONDARY blocker; the binding
  proximate cause of "no crosses since 08:17" was the `_math` SPINE_WIRING_FAULT below).

## RESOLVED — the proximate cause of "dark since 08:17" was a spine code regression

Every forecast family that reached the q-kernel spine from ~08:44 onward threw:

```
QKERNEL_SPINE_NO_TRADE:SPINE_WIRING_FAULT:UnboundLocalError:cannot access local
variable '_math' where it is not associated with a value
```

Root: `src/decision/payoff_vector.py` `_argmax_robust_delta_u` referenced `_math` in the
`delta_u_at_min` NaN guard, but `import math as _math` sat BELOW that first use → the import
made `_math` a function-local that was unbound at the guard → UnboundLocalError on EVERY spine
decision → zero crosses. The last healthy cross was 08:17; the regression hit at 08:44.

FIX (commit `1574d5ce6b`, 2026-06-16 09:16): moved `import math as _math` BEFORE its first use.
DEPLOY: the 12:16 daemon restart (pid 30614, main tree `live/iteration-2026-06-13`) loaded the
fixed file. **0 `_math` errors post-restart** (verified). The forecast/spine lane is RESTORED:
post-restart 15 FORECAST_SNAPSHOT_READY processed, 438 past-close expired (the #92 sweep),
147 live families pending (td 06-17/06-18). Spine flag `qkernel_spine_enabled=true`.

## The real cross-suppressor histogram (today, full day — was previously unmeasured)

The reactor cycle-reason histogram for 2026-06-16 (forecast lane only; day0
TRADE_SCORE_NON_POSITIVE excluded), most-binding first:

| Suppressor | cycle-reasons | transient-requeues | class |
|---|---|---|---|
| EXECUTABLE_SNAPSHOT_BLOCKED / STALE:freshness_deadline | 5099 | 212k / 45k | snapshot freshness+coverage (#122/#67) |
| LIVE_INFERENCE_INPUTS_MISSING (all sub-types) | ~6000 | — | input-assembly, BEFORE the spine |
|  └ REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE#UR_CAPTURE_MISSING | 672 | — | bundle materialized non-fused |
|  └ CALIBRATION_AUTHORITY_MISSING:model_row | 2493 | — | legacy calibration row absent |
|  └ REPLACEMENT_ (FORECAST_HOOK / DIRECTION / READINESS) | ~440 | — | replacement hook gates |
| EVENT_BOUND_ALL_CANDIDATES_REJECTED + capital_efficiency_lcb_ev | 1572 | — | q_lcb ≤ price (RULE-1 our defect; q-quality) |
| FDR_REJECTED | 1171 | — | |
| SUBMIT_ABORTED_EDGE_REVERSED / FAMILY_REVERSED / PRICE_MOVED | 270 / 72 / 20 | 120 | recapture (NOT dominant) |

8 LIVE ORDER crosses placed today (all buy_no maker rests, last 08:17). The dominant blockers
are UPSTREAM of the edge decision ("couldn't evaluate"), NOT "no edge" — per RULE 1, our defect.

## KEY: the materialization is NOT the dominant gap — live-eligible posteriors DO exist

`forecast_posteriors.provenance_json.replacement_q_mode` for the live target dates
(computed FRESH — 06-17 max computed_at 17:33Z, i.e. current):

- **06-18**: 55 FUSED_NORMAL_PARTIAL (100% live-eligible)
- **06-17**: 79 FUSED_NORMAL_FULL + 379 FUSED_NORMAL_PARTIAL = **458 live-eligible** + 404 BAYES_PRECISION_FUSION_CAPTURE_MISSING (~53% eligible)
- **06-16**: 27 FULL + 286 PARTIAL = 313 eligible + 844 CAPTURE_MISSING

So ~half of live families PASS the replacement-q-mode gate and reach the healthy spine, yet
still don't cross. Their binding constraint is therefore DOWNSTREAM:

1. **EXECUTABLE_SNAPSHOT_BLOCKED self-heal timing bug — THE dominant rate-limiter, fully
   diagnosed.** Flow (reactor.py:1626): a forecast family whose executable snapshot is not
   fresh at the decision gate transient-requeues `EXECUTABLE_SNAPSHOT_BLOCKED` and records
   itself for the end-of-cycle drain (`_drain_substrate_refreshes`, reactor.py:1170) which is
   meant to capture its book so the NEXT cycle processes it (the "ALWAYS-DECIDABLE" self-heal).
   THE BUG: the drain captures the book at the END of the 60s reactor cycle, but the freshness
   window is 30s (`_K1_DEFAULT_PRESUBMIT_FRESHNESS_SECONDS=30.0`, adapter:434). The family is
   re-decided ~60s later at the next cycle — by which point the just-captured book is ALREADY
   STALE (>30s) → it blocks AGAIN → infinite requeue (212,159/day). The self-heal can never
   converge because heal→retry latency (60s cycle) > freshness window (30s). COMPOUNDING: the
   169 pending forecast families span **49 cities**, but the 20s substrate-warm + 10s-budget
   drain cover only ~21 cities/cycle, so ~28 cities' families are never even warm-captured and
   depend entirely on the broken end-of-cycle self-heal. Net: only families the 20s warm
   happens to capture within 30s of their decision reach the spine and cross (the 8/day). The
   fix is JIT-fresh capture of the family at the DECISION boundary (outside any txn, respecting
   the three-phase no-network-in-txn law), so the gate sees a <30s book → passes → reaches the
   healthy spine. WIDENING the 30s window is FORBIDDEN (operator law: mid/last-cost ban, never
   loosen freshness to fill). Tier-0-adjacent; test+verify required, NOT a tail-of-session rush.
   This is task #122 (snapshot freshness/coverage oscillation), now root-caused.
2. **Submit-time recapture** — the SAME 30s window also gates the JIT recapture at submit
   (SUBMIT_ABORTED_PRICE_MOVED, 20+120/day); the same JIT-fresh-capture fix (task #132) applies.
3. **q-edge gate** (capital_efficiency: q_lcb ≤ price) — the q-quality lever; the rebuild's core.

## Evidence-ranked next levers (none a one-liner; all RULE-1 our defect)

1. JIT-fresh recapture of the spine-selected family at submit (converts the 458 eligible
   06-17 families' spine selections into honest fresh-book crosses; settlement-honest — does
   NOT touch the 30s window). Highest leverage, Tier-0 risk.
2. Snapshot capture coverage/oscillation (#122): stop `fresh_executable_city_count` 0↔21
   oscillation so every candidate family has a <30s book each cycle.
3. Replacement materialization CAPTURE_MISSING (~404 of 862 for 06-17): feed the precision-
   fusion captures so those families materialize FUSED_NORMAL_* instead of CAPTURE_MISSING.
4. Observability: `edli_no_submit_receipts` is DEAD since 2026-06-12T12:12 (max decision_time),
   so the spine's per-family no-trade reason is unreadable from the ledger — the cycle log is
   the only window. Restoring it is the instrument for "continuous correct analysis" (the
   plan's Phase-G named silent-failure mode).

## What is NOT the blocker (ruled out this session, with evidence)

- NOT day0 throughput flood: forecast lane IS flowing post-restart (15 processed, 147 pending).
- NOT the PARTIALLY_MATCHED family block: that order is EXPIRED; the command_recovery fix holds.
- NOT world-DB bloat: pruned (7.0M→69k terminal).
- NOT "spine not deciding": spine flag on, `_math` fixed, spine decides on live families.
- NOT recapture FAMILY/PRICE reversal as the dominant cause: only 20+72 today.
