# T-1_KNOWN_GAPS_COVERAGE.md

**Artifact:** T-1.5 (MASTER_PLAN_v2 para 7)
**Source:** docs/to-do-list/known_gaps.md (last main-aligned 2026-05-04, main=cd882ee9)
**Pointer file:** docs/operations/known_gaps.md confirmed as compatibility pointer only.
**Produced:** 2026-05-04T16:49:38Z
**Branch/HEAD:** source-grep-header-only-migration-2026-05-04 / 1116d827

---

## Active gap coverage table

Key for blocks_corrected_* columns: YES = gap must be resolved before that milestone | NO = not a blocker | PARTIAL = partially blocks

| gap_id | one-line title | status | tier_assigned | blocks_corrected_shadow | blocks_corrected_live | blocks_corrected_pnl | blocks_promotion | local_repo_verify_required |
|---|---|---|---|---|---|---|---|---|
| G-SQL | SQLite single-writer lock causes live daemon crash | OPEN | T1E | NO | YES | NO | YES | yes - daemon crash count, launchd state |
| G-FULL | Full-flow live audit (OPEN P1 items below) | OPEN | T1A/T1F/T1BD/T1C/T1G/T3 | PARTIAL | YES | YES | YES | yes - production DB state |
| G-SNAP | No production executable snapshot producer for exit path | OPEN P1 | T1G/T3 | YES | YES | YES | YES | yes - live snapshot table contents |
| G-COMPAT | V2 submit path still uses compatibility envelope | OPEN P1 | T1F | YES | YES | YES | YES | no - code-level finding |
| G-RED1 | RED force-exit sweep is proxy-only, not venue cancel/sell | OPEN P1 | T2H | NO | YES | YES | YES | yes - cycle_runner runtime behavior |
| G-RED2 | Fail-closed RED causes do not trigger force-exit sweep | OPEN P1 | T2H | NO | YES | YES | YES | yes - riskguard state |
| G-ORANGE | ORANGE risk behaves like entry-block-only YELLOW | OPEN P2 | T2H | NO | PARTIAL | PARTIAL | YES | no - code-level finding |
| G-EXITPF | Exit partial fills do not reduce local position exposure | OPEN P1 | T3 | YES | YES | YES | YES | no - code-level finding confirmed by read-only repro |
| G-HARV1 | Harvester live settlement write is HIGH-only for LOW markets | OPEN P1 | T1C | YES | YES | YES | YES | no - code-level finding |
| G-HARV2 | Settlement observation lookup ignores authority/station/metric | OPEN P1 | T1C | YES | YES | YES | YES | no - code-level finding |
| G-SETTLE | Settled pending-exit exposure can be skipped indefinitely | OPEN P1 | T1C | YES | YES | YES | YES | no - code-level finding |
| G-CALMAT | Calibration maturity edge-threshold multiplier is dead on live path | OPEN P1 | T3 | YES | YES | YES | YES | no - code-level finding |
| G-EXCONCIL | M5 exchange reconciliation residual - journal counts MATCHED/MINED | MITIGATED RESIDUAL P2 | T2C/T3 | NO | PARTIAL | PARTIAL | PARTIAL | no - code-level finding |
| G-COLL | Collateral preflight accepts arbitrarily stale snapshots | OPEN P2 | T3 | PARTIAL | YES | YES | YES | no - read-only repro confirmed |
| G-FILLID | Filled-command idempotency collision can rematerialize without order_id | OPEN P2 | T3 | PARTIAL | YES | YES | YES | no - code-level finding |
| G-ENSNAN | ENS local-day NaNs can pass validation and create false posterior edges | OPEN P1 | T3 | YES | YES | YES | YES | no - read-only repro confirmed |
| G-HARV3 | Harvester can rebrand live decision p_raw as TIGGE training data | OPEN P1 | T1C | YES | YES | YES | YES | no - read-only repro confirmed |
| G-DAY0 | Day0 stale/epoch observations can still produce tradeable p_raw | OPEN P1 | T3 | YES | YES | YES | YES | no - read-only repro confirmed |
| G-SDKENV | Final SDK submission envelope not persisted after CLOB submit | OPEN P1 | T1G | YES | YES | YES | YES | no - code-level finding |
| G-HK1 | HK SettlementSemantics uses WMO half-up but PM uses floor | OPEN | T3 | YES | YES | YES | YES | no - code-level finding; system constitution review needed |
| G-HK2 | HK 03-13/03-14 unresolved HKO source/audit mismatch | OPEN | T3 | YES | YES | YES | YES | yes - operator-approved primary-source evidence needed |
| G-WU | WU cities: API max(hourly) != website daily summary high | OPEN | T4 | NO | YES | YES | YES | yes - external WU API/scrape investigation |
| G-TPE | Taipei: PM switched resolution source 3 times | OPEN | T4 | NO | YES | YES | YES | yes - CWA/NOAA historical data needed |
| G-DST | Historical diurnal aggregates need DST-safe rebuild cleanup | OPEN NOT-LIVE-CERTIFIED | T4 | YES | YES | YES | YES | yes - historical rebuild not yet executed |
| G-OMQUOTA | Open-Meteo quota contention is workspace-wide | STALE-UNVERIFIED | T4 | NO | PARTIAL | NO | PARTIAL | yes - recent logs show 200 OK; may be inactive |
| G-SOLAR | CycleRunner fails on malformed solar_daily schema rootpage | STALE-UNVERIFIED | T4 | NO | PARTIAL | NO | PARTIAL | yes - requires deliberate day0_capture run to verify |
| G-ACP | ACP router fallback chain recovers after failure instead of stabilizing | OPEN | UNCOVERED | NO | NO | NO | NO | yes - router/evolution audit surface |
| G-D3 | D3: Entry price not typed through execution economics (residual) | OPEN RESIDUAL | T3 | PARTIAL | YES | YES | YES | no - mitigation deployed; tick/realized-fill remains |
| G-D4 | D4: Entry-exit epistemic asymmetry (structural) | OPEN | T3 | YES | YES | YES | YES | no - code-level structural finding |
| G-D1R | D1 residual: alpha is risk-cap blend not EV-optimized | MITIGATED RESIDUAL | T4 | NO | PARTIAL | PARTIAL | PARTIAL | no |
| G-D2R | D2 residual: tail alpha scale behavior unchanged | MITIGATED RESIDUAL | T4 | NO | PARTIAL | PARTIAL | PARTIAL | no |
| G-S3 | climate_zone field missing from config/cities.json | OPEN | T4 | NO | NO | NO | PARTIAL | no |
| G-S4 | 11 antibody tests for calibration weighting LAW missing | OPEN | T4 | NO | NO | NO | PARTIAL | no |
| G-S6 | PoC v6 cluster-level alpha tuning (blocked by s3) | OPEN blocked | T4 | NO | NO | NO | PARTIAL | no |
| G-S7 | Re-rebuild calibration_pairs_v2 at n_mc=10000 | DEFERRED | T4 | NO | NO | NO | NO | no |
| G-S8 | Vectorize p_raw_vector_from_maxes MC loop | DEFERRED | T4 | NO | NO | NO | NO | no |
| G-TWOSYS | Two-System Independence Phase 4 | DEFERRED | T4 | NO | NO | NO | NO | no |
| G-BACKFILL | Backfill script unification | LOW DEFERRED | T4 | NO | NO | NO | NO | no |
| G-HOURLY | hourly_observations is dead - schedule deletion | LOW OPEN | T4 | NO | NO | NO | NO | no |
| G-MONRES | Missing monitor-to-exit chain - antibody deployed | MITIGATED | T2C | NO | NO | NO | NO | no |
| G-EDGEREV | EDGE_REVERSAL hard divergence kill-switch at 0.30 | PARTIALLY FIXED | T3 | NO | PARTIAL | PARTIAL | PARTIAL | no |

---

## Plan coverage gaps

Gaps not covered by any named tier in MASTER_PLAN_v2:

| gap_id | Reason uncovered |
|---|---|
| G-ACP | ACP router fallback is an OpenClaw/routing layer gap, not a Zeus trading-engine tier. MASTER_PLAN_v2 scopes to Zeus trading engine only. No tier addresses routing daemon recovery behavior. |

---

## Summary

- Total OPEN (including OPEN P1, OPEN P2, STALE-UNVERIFIED, OPEN RESIDUAL, MITIGATED RESIDUAL, PARTIALLY FIXED, LOW OPEN): 39
- Total OPEN strict (OPEN status, not MITIGATED/DEFERRED): 28
- Total covered by a tier: 38
- Total UNCOVERED by plan: 1 (G-ACP)
- Count blocking corrected_live (YES in blocks_corrected_live column): 21
- Count blocking corrected_shadow: 15
- Count blocking corrected_pnl: 22
- Count blocking promotion: 32

Note: local_repo_verify_required=yes entries (G-SQL, G-FULL, G-SNAP, G-RED1, G-RED2, G-HK2, G-WU, G-TPE, G-DST, G-OMQUOTA, G-SOLAR, G-ACP) require operator-local evidence that cannot be confirmed from public/main repo inspection alone.
