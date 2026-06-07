# The Path — Full End-to-End PR: Execution & Verification Spec

> Authority: this file is the CONTRACT agents execute against. Branch `thepath/audit-realign` (worktree `/Users/leofitz/zeus-thepath-audit`) off `16c35e7445` (latest hotfix). Redeploy is operator-gated; building on this branch deploys nothing until the operator redeploys.
> Created: 2026-06-07. Authority basis: 20-agent adversarial audit (`tasks/wtla104oh.output`) + inline code verification.
> Rule for "full" scope (operator directive 2026-06-07): complexity is allowed ONLY where a verification proves it improves the outcome vs the simpler form. Every component below carries a JUSTIFY+VERIFY gate. No component ships on assertion.

---

## §0 PROCESS MEMORY — the twists, cause→effect (do not repeat these)

These are the error categories that recurred. Before any conclusion, check against this list FIRST (immune system, not security guard).

1. **Memory ≠ reality.** Prior summary + The Path report both said "shadow / not-yet-live." Re-probed DB: 171 real fills, 138 open positions, last order today 17:25. CAUSE: trusting a stale narrative. EFFECT: nearly designed an overhaul for a system that is already live. LAW: decide on re-probed reality (query the DB / process / config), never memory.
2. **Antibody defeated by syntax.** Commit `16c35e7445` rewrote `1-x` → `(1/x-1)*x` (byte-identical) to pass the AST complement guard. CAUSE: guard matches AST shape, not value. EFFECT: green CI = false confidence; complement reasoning still constructable. LAW: an antibody must make the error category *unconstructable* (type), not *unwriteable in one syntax* (grep).
3. **Evidence accepted but not consumed.** `runtime_policy.py` accepts promotion/capital evidence params, never reads them; LIVE_AUTHORITY from 5 flags alone; the antibody test for this CURRENTLY FAILS. CAUSE: a gate that alerts (dataclass exists) but doesn't block (resolver ignores it). LAW: if evidence exists it must be load-bearing by type, or it is theater.
4. **Master arm that arms nothing.** `edli_live_operator_authorized=false` is a no-op for canary (only gates the separate `edli_live` mode). CAUSE: flag name implies a chokepoint it doesn't own. LAW: a kill-switch must gate EVERY live submit path by type, verified by a test enumerating modes×switch.
5. **Cross-module semantic drop.** Direction-flip under LIVE_AUTHORITY does not re-assert DIRECTION LAW at the flip site; trusts the upstream `candidate_direction` string. CAUSE: Module A's output flows into B without re-checking the invariant at the boundary (Fitz #2). LAW: re-assert cross-module invariants at the consuming boundary, ideally in `__post_init__`.
6. **Strawman objective.** The Path report claimed ">51% is the wrong objective, use EV." Live code already does EV (`(q_lcb-price)/price>0` ∧ `q_lcb≥0.51` floor ∧ log-growth rank). CAUSE: critiquing a goal the system doesn't actually hold. LAW: read the live gate before asserting the objective is wrong.
7. **Rebuild what already exists.** §4b resolution bridge is ~80% built (`ens_bias_model.transport_bias_prior` + `BiasCandidate` accept-gate). CAUSE: design doc written without provenance-auditing existing code. LAW: audit-for-reuse (CURRENT_REUSABLE verdict) before greenfield.
8. **Lead-confound (historical).** previous-runs (fixed-lead) vs single-runs (run availability) vs historical-forecast (near-nowcast stitched) are different objects; `run_time ≠ source_available_at ≠ observed_at ≠ imported_at`. LAW: every forecast/obs row carries an availability timestamp with provenance; lookahead is a typed BLOCKED, not a convention.
9. **Resolution-σ mismatch.** Porting a coarse prior to finer res must NOT sharpen σ (ρ_σ<1 is an average, not a law; coast/terrain/urban can have ρ>1). LAW: σ widen-only across a bridge until own-resolution settled coverage earns the tightening.

---

## §1 VERIFIED CURRENT STATE (2026-06-07)

- **LIVE.** `state/zeus_trades.db`: `venue_trade_facts`=171 fills, `position_current`=138, `venue_order_facts`=293 (last MATCHED 17:25 today). `edli_live_order_events`=0 → the 293 are the **mainline executor** (`src/execution/executor.py`), NOT the EDLI/replacement paths.
- **Config (`config/settings.json`):** `live_execution_mode=edli_live_canary`, `reactor_mode=live`, `real_order_submit_enabled=true`, `live_canary_enabled=true`, `edli_live_operator_authorized=false`, `tiny_live_notional_cap_enabled=false`, `tiny_live_daily_order_cap_enabled=false`. Replacement flags 294-298 all `true`.
- **Confirmed gaps (all on NEW paths, armed-but-not-yet-filling):** see §2.
- **Already-correct (do not touch):** settlement VERIFIED-only gate (`harvester.py:972`); native ask/depth + fee execution cost (`executable_cost.py:70-84`, `assert_not_midpoint/last/complement`); bias sign+unit (`ens_bias_repo.py:475`, `event_reactor_adapter.py:6540`); replay anti-lookahead (`replacement_forecast_replay.py:191-196`); ensemble allowlist (`ensemble_snapshot_provenance.py`); day0 running-max monotone law (`day0_high_nowcast_signal.py:19-21`).

---

## §2 PHASE −1 — SAFETY FIXES (hotfix; land first, TDD relationship-test-first)

Each fix: write the failing test FIRST, then implement, then run test + neighbors + the existing antibody tests. NEVER self-approve — a separate critic + verifier lane reviews.

### FIX-1 (CRITICAL) — Evidence must be load-bearing for LIVE_AUTHORITY
- **Files:** `src/data/replacement_forecast_runtime_policy.py` (`resolve_replacement_forecast_runtime_policy`, 195-248); `src/data/replacement_forecast_switch_decision.py` (`evaluate_replacement_forecast_switch_decision`, 95-193).
- **Problem:** evidence params accepted (198-199), never referenced; LIVE_AUTHORITY from flags alone (238). `switch_decision` ignores `capital_objective_evidence`.
- **Fix:** in the resolver, when status would be LIVE_AUTHORITY, REQUIRE `promotion_evidence is not None AND promotion_evidence.promotion_allowed() AND capital_objective_evidence is not None AND capital_objective_evidence.capital_objective_allowed()`. If not → cap at `SHADOW_VETO_ONLY` with reason `REPLACEMENT_LIVE_AUTHORITY_REQUIRES_EVIDENCE` + the blocking codes. Mirror: `switch_decision` folds `capital_objective_evidence.blocking_reason_codes()` into `reasons` before admitting LIVE_AUTHORITY.
- **TDD:** make `tests/test_replacement_forecast_runtime_policy.py:177` (flags-all-true + evidence=None → BLOCKED/SHADOW_VETO) PASS; add: flags-all-true + evidence-with-blocking-codes → not LIVE_AUTHORITY.
- **Mainline-safety:** affects ONLY the replacement path; degrading it to SHADOW_VETO removes live flips/kelly. Mainline executor untouched. Verify callers (`main.py:5160-5199`, `replacement_forecast_hook_factory.py:482`) don't crash on the degraded status.

### FIX-2 (CRITICAL) — operator arm must gate every real submit; re-enable caps
- **Files:** `src/main.py` (567, 1105-1119, 5106-5120), `src/engine/event_reactor_adapter.py` (submit gate 916-939, 986), `config/settings.json` (142-143).
- **Problem:** `edli_live_operator_authorized` checked only for `mode==edli_live` (1118), not canary; canary POSTs real orders with it false; tiny caps disabled.
- **Fix (a) — caps:** set `tiny_live_notional_cap_enabled=true`, `tiny_live_daily_order_cap_enabled=true` (limits blast radius). Independently safe.
- **Fix (b) — operator arm by type:** introduce an `OperatorArm` token constructible ONLY in `main.py` after asserting `edli_live_operator_authorized==true`; the live submit adapter requires the token regardless of mode (canary included). Absent token → no-submit adapter.
- **REQUIRED TRACE BEFORE EDIT:** confirm the mainline executor path that produced the 293 orders does NOT route through `event_reactor_adapter` submit gate 916-939 (edli_live_order_events=0 implies separate path). If mainline DOES depend on this gate, the OperatorArm must admit the mainline path so FIX-2 does not halt live trading. Output the trace as evidence before changing 916-939.
- **TDD:** modes×operator_authorized matrix → real submit reachable ONLY when operator_authorized=true; canary with false → no-submit.

### FIX-3 (HIGH) — DIRECTION LAW re-asserted at the flip boundary
- **Files:** `src/engine/event_reactor_adapter.py` (1544-1581, `_replacement_live_authority_proof_for_direction` 5203-5223); `src/engine/replacement_forecast_reactor_hook.py` (243-251); `src/engine/replacement_forecast_hook_factory.py` (`_h3_direction_for_candidate_bin` 289-294).
- **Problem:** LIVE_AUTHORITY flip uses replacement `candidate_direction` verbatim; no law recheck (SHADOW_VETO path hard-blocks flips, LIVE doesn't).
- **Fix:** at the flip site re-derive lawful direction from (selected bin vs replacement forecast point): `buy_yes ⟺ bin≈argmax(replacement.q)`, else `buy_no`. If `effective_direction` disagrees → typed `REPLACEMENT_FORECAST_DIRECTION_LAW_VIOLATION` receipt, refuse flip. Structural: assert the law in `ReplacementForecastCandidateView.__post_init__` so a law-violating candidate is unconstructable.
- **TDD:** wrong-side replacement posterior → flip refused with the typed reason.

### FIX-4 (HIGH) — close the buy_no escape hatch; allow-list ⊆ carrier vocab
- **Files:** `src/strategy/live_inference/live_admission.py` (150-156 hatch; 23 allow-list); `src/calibration/qlcb_provenance.py` (43-46 vocab).
- **Problem:** material-YES-bin buy_no ADMITTED without an allowed LCB source when `conservative_edge>confidence_gap` (self-referential on the same un-provenanced q_lcb). `YES_UCB_DERIVED` in allow-list but not in `CalibrationSource` vocab.
- **Fix:** delete the `conservative_edge>confidence_gap` waiver (153-156) — material-YES buy_no REQUIRES an allowed native NO source unconditionally. Remove `YES_UCB_DERIVED` from line 23. Add invariant test: `LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES ⊆ CALIBRATION_SOURCES`.
- **Mainline-safety:** general buy_no is already q=0 (disabled), so tightening is fail-safe. Confirm no live strategy relied on the waiver.

### FIX-5 (HIGH) — make complement-pricing unconstructable by TYPE
- **Files:** new `src/contracts/side_probability.py` (newtypes); call sites in the 17 `LIVE_PROBABILITY_PATHS`; `tests/test_probability_complement_ast_guard.py`.
- **Problem:** AST guard defeated by value-identical rewrites; div-safety moved intrinsic→extrinsic.
- **Fix (staged):** (a) NOW: keep the AST guard, ADD a value-level test that evaluates candidate expressions and fails on any form numerically equal to `1-x` for the banned probability sites; un-rewrite the gratuitous `(1/x-1)*x` back to a named `payout_odds(price)`/`one_minus(p)` helper with a docstring (readability + removes the obfuscation). (b) LATER (P-types): `YesProb`/`NoProb` newtypes where `NoProb` cannot be derived from `YesProb` arithmetic; then the AST guard becomes belt-and-suspenders. Keep the genuine fix already in `16c35e7445` (`db.py:6810` buy_no early-None).
- **TDD:** value-equivalence test catches `(1/x-1)*x`.

**Phase −1 exit gate:** all new + existing relationship/antibody tests green; critic finds no live-money regression; verifier confirms `git diff` touches only intended files and the daemon boot path still constructs (dry boot). THEN operator redeploys.

---

## §3 FULL END-TO-END ARCHITECTURE (P0→P4) — each component JUSTIFY+VERIFY

Build order is fixed: a later phase may not start until the prior phase's verify-gate passes. "Full" components (belief ledger, survival model, Student-t, etc.) are INCLUDED only if their VERIFY shows they beat the simpler form on settlement truth.

- **P0 — Bias/resolution: VERIFY-and-persist, not build.** Audit `transport_bias_prior`+`BiasCandidate` as CURRENT_REUSABLE; add explicit `resolution` to the gate key; prove live 0.25° is bridged not raw-reused. Add EMOS serving 4th key axis `product_id` (`emos.py:249` — currently product-blind 3-key; F1 HIGH). VERIFY: settled-truth backtest shows keyed serving ≥ current.
- **P1 — THE MAKE-OR-BREAK GATE (build first, hard-stop).** (a) obs availability: backfill plane stops using `MAX(imported_at)` proxy, nowcast runs carry per-run obs-timing (the live field exists in `observation_client.py:403`; close the backfill/nowcast gap only). (b) ask/depth/fee fill model for Day0. VERIFY (G-DAY0): re-run the obs-timing edge under honest queryable-time + real fills → **ROI≤0 kills the Day0 profit thesis; keep mask as safety only.**
- **P2 — Simple forecast product (shadow→veto).** per-city EB bias + equal-weight top-K + error-σ + product-keyed EMOS. JUSTIFY+VERIFY each complexity add: equal-weight top-3 is the baseline; learned weights / softmax / Student-t / AIFS-member / survival-Day0 each ship ONLY if they beat baseline by >2pp in a MAJORITY of settlement contexts (the parsimony guardrail). Forecast is quality/veto, NOT profit (F4) — no profit-priority.
- **P3 — Day0 trading (shadow→veto→size-down).** q_d0 from running-max + (if P2-justified) future-upside survival; q_lcb−ask−cost>δ; fractional Kelly on q_lcb; native YES/NO token only.
- **P4 — Promotion ladder + portfolio.** shadow→veto→size-down→small-capital→bucket-authority, per metric/region/lead. Correlation cap (simple aggregate exposure ceiling, NOT learned covariance) once multiple simultaneous live positions exist.

"Full Path" extras (belief_revisions/decision_revisions ledger, survival model, log-growth rewrite, 11 tables) are CANDIDATES, each gated: include only if its VERIFY proves outcome gain. Reuse existing tables (`day0_nowcast_runs`, `decision_events`, `model_bias`) before adding new ones.

---

## §4 VERIFY LIVE WORKS AFTER THE PR (mandatory closure)

Before declaring done / redeploy:
1. **Boot parity:** with all new flags OFF, daemon boot + first cycle is byte-identical to pre-PR (no behavior change unless flag-on).
2. **Mainline untouched:** the 293-order executor path still places + monitors + exits (FIX-2 trace proves independence; a dry replay confirms).
3. **Safety posture:** modes×operator_authorized×evidence matrix test proves no live submit without (operator arm ∧ evidence ∧ direction-law) — the antibody test that currently FAILS now passes.
4. **Open positions safe:** 138 positions still graded/exited under VERIFIED-only.
5. **Shadow proof for any new authority:** new path produces q in shadow ledger matching internal + external mainstream forecast before any arm (operator ARM condition).

---

## §5 ORCHESTRATION MAP (save-context: agents read THIS file, not the chat)

- Phase −1 build: `executor` (opus, live-money subtlety) implements FIX-1,3,4,5a + FIX-2a; FIX-2b after the required trace. TDD relationship-test-first. → `critic` (opus) review → `verifier` runs tests + dry boot. Separate lanes.
- P0: `explore`/`document-specialist` provenance-audit reuse, then `executor` adds keys + persists; `verifier` settled-truth backtest.
- P1: `executor` builds obs-gate + fill model; `scientist` runs G-DAY0 verify (hard-stop).
- P2/P3: per-component JUSTIFY+VERIFY workflows (evaluator-optimizer): build shadow → settled-truth compare vs baseline → keep only if >2pp majority.
- P4: promotion ladder, operator-gated.
