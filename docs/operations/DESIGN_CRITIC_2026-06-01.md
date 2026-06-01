# DESIGN CRITIC — 2026-06-01 (EDLI live-trading gate, designs 1/2/3)

Read-only adversarial review (agent aab33d99). Persisted by orchestrator (Write was blocked in review
mode). Reviews: DAY0_OBSERVATION_WRONGSIDE_ROOT, BEST_ORDER_SELECTION_ROOT, CI_HONESTY_AND_SCORE_GATE_RULING §4.2.

## CROSS-FIX SEQUENCING (FIRM)
```
1. §4.1 CI-honesty            DONE (committed 69bee9b752); re-verify only
2. Design 1 phase-gate        land next (fixes verified wrong-side admission) — REVISE first
3. q-calibration (June/JJA)   separate session (#58); BLOCKS everything below
4. CI-aware Kelly multiplier  wire dynamic_kelly_mult into EDLI evaluate_kelly (#103)
5. §4.2 admit-rescope         only after 3+4
6. Design 2 selector          LAST; only after 2+3 verified (#102)
```
**Selection (Design 2) must NOT turn on before q-correctness is verified — HARD NO.** The verified-wrong
Paris trade ranks #1 in the clean pool; a PnL selector today fires the worst wrong-q trade with confidence.

## DESIGN 1 — DAY0 PHASE-GATE → REVISE
Root VERIFIED: market_phase_for_decision() returns POST_TRADING when decision_time_utc >= polymarket_end_utc
(market_phase.py:164); MarketPhaseEvidence.from_market_dict returns phase=None ONLY on parse failure (no
over-exclusion of valid future markets); 0 EDLI references (event_reactor_adapter.py + src/events/). Paris snap
1152237: members 11.62/12.41/13.00°C, P(14°C)=0, q_NO=0.9968, re-fires per-minute 16:17-21Z, market closed 12:00Z.
REQUIRED REVISIONS (do-NOT-implement-until):
- MAJOR-1: gate inputs from payload.city/target_date/metric (:3882-3883,:4707-4708) + runtime_cities_by_name().get(city).timezone.
  The `family` object in the pseudocode is built at :557, NOT in scope at the admission sites :195/:507.
- MAJOR-2: real API = market_phase_evidence.from_market_dict(market=..., uma_resolved_source=<txhash str or None>).
  NOT the pseudocode name; uma param is Optional[str] tx hash, not bool. Pass None until UMA listener lands.
- MAJOR-3: market_end_at is NULL on 100% of retained rows (probability_trace_fact 5760/5760; executable_market_snapshots=0
  mid-cycle) → gate resolves via F1 12:00Z fallback essentially always (acceptable, F1 is_live_authoritative). DROP the
  uncommitted ae5fe38 `market_end_at IS NULL OR market_end_at > ?` SQL predicate — it is fail-OPEN (NULL passes) and gates
  nothing in production. Use MarketPhaseEvidence + F1 fallback.
- MAJOR-4: pre-noon already-observed LOW is STILL wrong-side under SETTLEMENT_DAY admission. A `low` extremum realized
  overnight (observable before 12:00Z end) but decided at 10:00Z is admitted. The gate kills POST_TRADING, not the full
  "already-observed extremum" category. FIX: restrict forecast_only same-day admission to families whose extremum window
  has NOT yet opened, OR explicitly scope the claim to "post-close" and document the residual pre-noon-low exposure.
- MINOR-1: add a continuous-redecision RED test (ended-market belief enqueues but yields no candidate; the 195/507 gate
  catches the re-enqueued event — assert it).
Independent of calibration — can land next.

## DESIGN 2 — BEST-ORDER SELECTOR → REVISE
ROOT A VERIFIED: no global selector — fetch_pending ORDER BY arrival (event_store.py:107-122), process_pending per-event
commit (reactor.py:165-172), only cross-candidate max within one family (event_reactor_adapter.py:2839-2853). ROOT B
VERIFIED: trade_score (trade_score.py:48-52) is a binary admission gate mis-used as ranker.
REQUIRED REVISIONS:
- CRITICAL-1: expected_PnL = kelly_size × (q−cost) DOUBLE-COUNTS edge. kelly_size ∝ f*·bankroll, f* ∝ (q−cost)/(1−price),
  so the ranker ∝ (q−cost)²/(1−price) — quadratic in edge, over-weights near-certain high-price whales. Use the design's
  OWN §4.3 alternative: rank_key = (q−cost)/cost (EV-per-dollar) or f*·edge — the correct bankroll-constrained objective.
  The headline objective contradicts §4.3; resolve to §4.3.
- MAJOR-1: concentration/correlation risk. top-K PnL selector + removed 1/day cap (0d0939a480) + lifted $5 cap can deploy
  the whole bankroll into one correlated direction (multiple near-certain NOs under the same cold-bias/JJA regime). Require
  a portfolio_heat/correlation guard BEFORE lifting caps.
- MAJOR-2: selector ranks the ADMITTED set; Paris (verified wrong-side) ranks #1 today. Selection AMPLIFIES q-errors into
  capital. HARD SEQUENCING: turn on LAST, only after Design 1 gate + q-calibration verified.

## DESIGN 3 — §4.2 ADMIT-RESCOPE → REJECT AS WRITTEN (§4.1 already done)
§4.1 (unify corrected member surface) committed at HEAD 69bee9b752 — do not re-implement.
- CRITICAL-1: "Kelly carries variance" is FALSE for the live EDLI path. dynamic_kelly_mult is called ONLY in evaluator.py +
  replay.py, NOT in event_reactor_adapter.py/money_path_adapters.py. Live sizing = flat kelly_multiplier=0.25 × coarse
  bias-decay 0.5× (no CI term) → evaluate_kelly(p_posterior, flat) (:724-737, money_path_adapters.py:83-100). Removing the
  q_5pct gate → wide-CI bins sized at full flat Kelly, NO variance penalty. The q_5pct gate is currently the ONLY variance
  control. §4.2 is conditional on first wiring a CI/lead-aware multiplier (#103).
- CRITICAL-2: FDR is NOT a calibration false-confidence guard. FDR p_value = mean(bootstrap_edge ≤ 0) resampled from the
  SAME (possibly miscalibrated) point distribution → cannot detect a confidently-wrong point. Paris: tight forecast (12-13°C)
  → low p_value → FDR PASS → wrong-side at 14°C. fdr_alpha=0.1 (permissive). §4.2 "admit on EV>0 ∧ FDR p<α" would have
  ADMITTED wrong-side Paris. Do NOT represent FDR as the false-confidence floor.
- MAJOR-1: §4.1 makes §4.2 MORE dangerous — narrowing the CI raises q_lcb → lowers p_value → FDR more permissive on exactly
  the cold-bias cities whose correction may be wrong. Re-measure calibration trustworthiness AFTER §4.1 before any §4.2.

## OPEN QUESTIONS
- Is the bias-decay 0.5× haircut sufficient interim variance control for a PARTIAL §4.2 (EV-gate with the haircut as floor)?
  Needs the calibration session.
- Does portfolio_heat have any live EDLI feed, or is the correlation guard greenfield? (Design 2 MAJOR-1 assumes greenfield.)

## SOLID (acknowledged)
- Design 1 wrong-side root: rigorous, reproduced against live DB + forecast members.
- Design 3 §4.1 mechanism (train/serve mean-split) correct + already correctly committed.
- Design 2 ROOT A (no global selector) precisely correct.
