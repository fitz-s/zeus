# Phase 9B Wide Review — critic-carol cycle 3 (final)

**Commit**: `0974a62` feat(phase9b): risk-critical DT closure (DT#2 + DT#5 + DT#7)
**Reviewer**: critic-carol cycle 3 (3rd of 3 cycles before rotation to critic-dave)
**Date**: 2026-04-18
**Mode**: Gen-Verifier (team-lead-direct exec + persistent critic)
**Operating mode**: ADVERSARIAL-opening (mandatory 15 min per cycle-2 L6 streak-warning) → ESCALATED after CRITICAL surfaced
**Persisted-by**: self (cycle 3 spawned as `general-purpose` with Write/Edit enabled per user ruling 2026-04-18)

## VERDICT: **ITERATE — CRITICAL-1 breaks the 4-streak**

One CRITICAL finding (DT#2 sweep is inert — violates the very law it claims to implement, INV-05 forbids advisory-only RED behavior), zero MAJOR, two MINOR, plus test-gap forward-log items. DT#5 and DT#7 land cleanly on surgical-revert probing. DT#2's R-BV antibody passes but ONLY because R-BV asserts the marker gets written — NOT that a sell order follows. The marker is never read by any runtime lane. Contract prose claims "exit_lifecycle machinery picks up on next monitor_refresh cycle and posts sell orders" — empirical probe refutes this: `evaluate_exit`, `check_pending_exits`, and `handle_exit_pending_missing` all do NOT consume `pos.exit_reason == "red_force_exit"`.

This is the **Fitz Constraint #3 (Immune System) pathology**: a test locks in the seam that exists (marker writing) while the seam that would fulfill the law (marker reading + sell-order posting) remains absent. The antibody is a fingerprint-match antibody for a fake immune response.

## Pre-commitment predictions vs actuals (NEW for cycle 3 — not recycled)

| # | Prediction | Actual | Hit/Miss |
|---|-----------|--------|----------|
| 1 | DT#5 isinstance vs np.float64 / duck-type / subclass loophole | Probed 3 ways: np.float64→bare-float branch (works), duck-type→TypeError on float() (loud fail), subclass→correctly triggers assert_kelly_safe. NO loophole. | **Miss** (defensive probe confirmed polymorphic design is sound) |
| 2 | DT#2 sweep re-run idempotency on next-cycle: positions carrying prior `red_force_exit` counted as `already_exiting` | Confirmed; but the deeper issue is: IRRELEVANT, because the marker does not trigger a sell order either cycle | **Miss at surface, hit at depth** — surfaced CRITICAL-1 |
| 3 | DT#7 orphan code — zero runtime callers | CONFIRMED by grep; documented deferral to P9C; borderline MINOR | **Hit** (MINOR-1) |
| 4 | Stale-antibody flip uses `"float" in ann_text` substring (too permissive) | Confirmed — would pass for `np.float64`/`floating`. Paired with R-BW behavioral assertion this is acceptable. Low impact. | **Partial hit** (documented, not MINOR-worthy) |
| 5 | Polymorphic + caller migration — `replay.py:1300` still bare-float | CONFIRMED: replay.py:1300 passes bare `edge.entry_price`. Kelly boundary for the shadow/backtest lane silently loses DT#5 enforcement. Documented in forward-log. | **Hit** (MINOR-2) |
| 6 | Regression math exactness | 144/1854/93 matches commit claim exactly | Hit (nothing to flag) |

Cycle 3 hit rate: ~3.5/6. Lower than cycle-1 (5/5) and cycle-2 (2.5/5). The miss on PRED-2's surface masked a deeper CRITICAL that adversarial probing surfaced — **cycle-2 L8 (surgical-revert) discipline was decisive here**. A static review of R-BV alone would have PASSED; the runtime probe of `evaluate_exit` + `check_pending_exits` exposed the inert-marker pathology.

## Adversarial-hunt results (mandatory first 15 min, per cycle-2 L6 streak-warning)

### Hunt 1 — DT#5 polymorphic loophole
**Finding: NONE.**
- `np.float64(0.4)` → isinstance(ExecutionPrice) False → bare-float branch → correct math.
- Duck-type with `.value/.price_type/.fee_deducted` attributes → `float(entry_price)` raises TypeError loud, not silent bypass.
- `class Ext(ExecutionPrice)` subclass → isinstance returns True → `assert_kelly_safe()` fires correctly.

### Hunt 2 — DT#2 sweep override risk → UPGRADED TO CRITICAL-1
**Finding: CRITICAL.** See CRITICAL-1 below. Adversarial probing revealed that the sweep marker is not consumed by any runtime lane, making the implementation advisory-only (which the law explicitly forbids).

### Hunt 3 — DT#7 orphan-code risk
**Finding: MINOR-1.** Function has zero runtime callers (confirmed by grep across src/). Documented as P9C wiring deferral — acceptable if P9C lands on schedule. If P9C slips, this becomes dead code.

### Hunt 4 — Stale-antibody flip correctness
**Finding: NONE.** Flips are structurally correct — R-BW assertion requires BOTH `"float"` AND `"ExecutionPrice"` substrings in the union annotation. Surgical-revert probes on both flipped tests fail correctly.

### Hunt 5 — Regression math gap
**Finding: NONE.** Full pytest run: 144 failed / 1854 passed / 93 skipped — exactly matches commit claim.

### Hunt 6 — Hard-constraint bleed
**Finding: NONE.** Evaluator diff is 5 lines (3 comments + 1 ExecutionPrice-object arg change). No monitor_refresh touches. No v2 writes. No DDL. No TIGGE imports.

## Critical findings

### CRITICAL-1: DT#2 RED force-exit sweep is inert — violates INV-05

**Law** (`zeus_current_architecture.md §17` + `zeus_dual_track_architecture.md §DT#2`):
> "`RED` must cancel all pending orders AND initiate an exit sweep on active positions. A 'force_exit_review' that only blocks new entries is an advisory-only output; per INV-05 it is forbidden."

**Pre-P9B state**: `cycle_runner.py:246-250` sets `force_exit_review_scope="entry_block_only"` and logs — explicitly the advisory-only form that INV-05 forbids.

**P9B delivery**: `_execute_force_exit_sweep(portfolio)` iterates positions and sets `pos.exit_reason="red_force_exit"` on each non-terminal, not-already-exiting position. `summary["force_exit_sweep"]={"attempted":N,...}` returned. `portfolio_dirty=True` so positions persist.

**The pathology (empirically verified)**: `"red_force_exit"` is NEVER read by any runtime consumer that posts sell orders.

Grep evidence (`src/`):
```
cycle_runner.py:97    pos.exit_reason = "red_force_exit"      # WRITE
cycle_runner.py:65    (docstring)                              # DOC
cycle_runner.py:303   (comment)                                # COMMENT
```
Zero reads. Only tests assert the marker.

Runtime-probe evidence (script at verbatim reproducible):
```python
pos = Position(..., state="holding", exit_reason="red_force_exit", ...)
ctx = ExitContext(  # complete, no triggers fired
    current_market_price=0.51, best_bid=0.50, best_ask=0.52,
    market_vig=0.05, whale_toxicity=False, chain_is_fresh=True,
    fresh_prob=0.55, fresh_prob_is_fresh=True,
    hours_to_settlement=24.0, position_state="holding",
    divergence_score=0.0, market_velocity_1h=0.0, day0_active=False,
)
decision = pos.evaluate_exit(ctx)
# should_exit=False, reason=''
# pos.exit_reason UNCHANGED = "red_force_exit"
# pos.exit_state = ExitState.NONE
```

Code-path analysis:
1. `cycle_runtime.py:589` calls `pos.evaluate_exit(exit_context)`. `evaluate_exit` checks settlement-imminent, whale-toxicity, divergence, Day0 triggers — **none of them read `self.exit_reason`**. Under normal conditions (position looks fine individually), `should_exit=False`.
2. `cycle_runtime.py:622` guards `if should_exit:` before `pos.exit_reason = exit_reason` and `execute_exit(...)`. Sweep-marked positions with fine contexts skip this branch.
3. `exit_lifecycle.check_pending_exits` at L523 filters `pos.exit_state not in ("sell_placed","sell_pending","exit_intent")` and CONTINUES (skips) — sweep sets `exit_reason` but never touches `exit_state`. Sweep-marked positions are skipped.
4. `handle_exit_pending_missing` at L250 gates on `position.chain_state == "exit_pending_missing"` — unrelated path.
5. `monitor_refresh.py` has ZERO references to `exit_reason` — not a consumer.
6. The only `pos.exit_reason` consumer in `exit_lifecycle.py:575` is the `DEFERRED_SELL_FILL` branch — runs only for positions ALREADY in `sell_placed/sell_pending` state whose order fills.

**Conclusion**: The sweep marker functions as documentation-on-the-object. It does not "initiate an exit sweep". In steady-state (positions healthy individually, RED risk fires due to bankroll drawdown or macro risk), NO sell order is ever posted in response to the sweep. The sweep is advisory-only — exactly the form INV-05 forbids.

**Why R-BV passes despite this**: R-BV asserts only that the marker was WRITTEN (result counts + `assert pos.exit_reason == "red_force_exit"`). It does NOT verify that a sell order was POSTED. The test's docstring aspirationally claims "exit_lifecycle machinery picks up on next monitor_refresh cycle and posts sell orders" but no assertion verifies this. This is the Fitz translation-loss pathology L2 + the cycle-1 checkbox-antibody fingerprint: the antibody captures the visible artifact (marker text) while the invariant (sell-order-follows) is unenforced.

**Scope of impact**:
- Law INV-05 is violated: advisory-only RED is forbidden; P9B delivers advisory-only RED under a structural-sounding name.
- The Gate claim "Phase 9B closes death-trap DT#2" is false as-shipped.
- Future critic (dave) inheriting a PASS from this cycle would reasonably believe DT#2 is closed when it is not.
- In a real RED event (macro drawdown, chain compromise), the bot sweep-marks positions but does not exit them. This is the exact failure mode §17 calls out.

**Required fix options** (any one sufficient):
- **(a) Minimal**: Add a branch in `evaluate_exit` that returns `ExitDecision(True, "RED_FORCE_EXIT", trigger="RED_FORCE_EXIT")` when `self.exit_reason == "red_force_exit"` AND `exit_context.day0_active is False` (day0 has its own exit logic). This routes the sweep-marked positions through the same `execute_exit` path as other triggers.
- **(b) Structural**: Add an explicit `_execute_red_force_exit_sells(portfolio, clob, conn)` in cycle_runner AFTER the sweep that iterates sweep-marked positions with `exit_state == ""` and calls `execute_exit(...)` directly with an ExitContext marked `exit_reason="RED_FORCE_EXIT"`.
- **(c) Law update**: If the P9B design intent is "sweep-as-marker, wiring in P9C with monitor_refresh LOW", state this explicitly in the law (§17 / §DT#2) AND in the P9C forward-log. Currently DT#2 is NOT in the P9C forward-log — team-lead believes P9B closes DT#2. This must be corrected.

**Relationship antibody required** (Fitz P3 structural immune):
- GIVEN: a sweep-marked position + a complete healthy ExitContext
- WHEN: the next monitor_refresh cycle runs (or the current cycle's own later stages)
- THEN: a sell order is posted (mock CLOB captures the order) AND `pos.exit_state` transitions to `sell_placed`.

Without such a relationship antibody, any future refactor of `evaluate_exit` / `check_pending_exits` / `monitor_refresh` can silently remove the (not-yet-present) consumer without the test suite surfacing it.

- **Confidence**: HIGH
- **Severity**: CRITICAL — law violation, not just hygiene.
- **Why this matters (Fitz Four Constraints)**:
  1. Structural decisions > patches: the marker is a patch. The structural decision (wire evaluate_exit or add a sweep-sells step) is not made.
  2. Translation loss: contract prose promises "machinery picks up"; no machinery does. The code does NOT encode the intent.
  3. Immune system > security guard: R-BV is a pure security-guard antibody. It detects no real pathogen.
  4. Data provenance: N/A.

## Major findings

None.

## Minor findings

### MINOR-1 (cycle-3): DT#7 `boundary_ambiguous_refuses_signal` is orphan code

**Evidence**: Grep across `src/` shows zero callers outside tests. The function is reachable only by R-BX test.

**Why it matters**: Acceptable if P9C wires the function as planned. But P9C is itself gated on `monitor_refresh` LOW wiring + B093 half-2 replay (Golden Window lift). Risk: multi-dependency deferral chains erode through reorganization; the function may still be orphan at P10. Fitz P3 — borderline immune-system fake.

- Confidence: HIGH
- Recommendation: ACCEPT for P9B scope (deferral explicitly documented in both contract §S3 and commit message), but P9C contract MUST include a `grep 'from src.contracts.boundary_policy import'` antibody that FAILS until the evaluator consumer exists. Also add a REGRESSION antibody: evaluator candidate with `boundary_ambiguous=True` → rejection with `DT#7` in reason string. If P9C slips, transfer to forward-log as a tracked debt.

### MINOR-2 (cycle-3): `replay.py:1300` still bare-float — shadow/backtest lane misses DT#5 enforcement

**Evidence**: `src/engine/replay.py:1300` calls `kelly_size(edge.p_posterior, edge.entry_price, ...)` where `edge.entry_price` is a bare float. Polymorphic `kelly_size` accepts it via the bare-float branch. No `assert_kelly_safe` fires. If `edge.entry_price` is actually an implied_probability value (which is exactly the D3 gap the type system was designed to prevent), Kelly oversizes on the shadow lane and any dashboards/analyses computed from replay carry the D3 bias.

**Why it matters**: Forward-log already includes "Migration of remaining bare-float kelly_size callers across src/" — so team-lead knows. But the word "migration" softens what is a law-enforcement gap on a production-ish lane (backtest analysis is authoritative for MN2T6 smoke tests per phase-5 evidence). Dashboards/reports from `replay.py` may carry systematic oversizing that future model retraining absorbs as if it were signal.

- Confidence: HIGH
- Recommendation: Forward-log entry should be upgraded from "migration chore" to "DT#5 partial enforcement — shadow/backtest lane pending". P9C should close this or explicitly document which lanes remain advisory-only.

## What's Missing (gaps, unhandled edge cases)

1. **No E2E antibody that DT#2 sweep posts a sell order.** R-BV proves marker-written, not order-posted. This is the CRITICAL-1 structural root.
2. **No paired-antibody for DT#2.** Cycle-1 L3 taught paired NEGATIVE+POSITIVE antibodies. R-BV has a paired (attempted=2 / already_exiting=1 / skipped_terminal=1) but all three test the WRITE seam. The READ seam has no test — because there is no read seam.
3. **No test that the marker is preserved across cycle boundaries.** If between RED and the next cycle, any code path (e.g., `save_portfolio → load_portfolio`) drops or mutates `exit_reason`, the sweep is further neutered. No round-trip antibody.
4. **No test that `exit_lifecycle.check_pending_exits` is a no-op for sweep-marked positions.** The scout's claim "exit_lifecycle machinery picks up" is an aspirational doc (cycle-2 L3 fingerprint) — no runtime assertion locks it.
5. **DT#7 has no "candidate-rejected-on-boundary-ambiguous" relationship test.** This is P9C scope — fine for now, but flag for dave.
6. **`replay.py:1300` kelly_size call has no DT#5 enforcement path.** See MINOR-2.

## Ambiguity Risks

- "Exit sweep" (§17 / §DT#2 law): the law reads as "initiate an exit sweep on active positions" — naturally parsed as "sells get posted". P9B implementation reads it as "mark positions for later action". The contract prose bridges the gap ("exit_lifecycle machinery picks up") but no such machinery exists. Both the law text and the contract prose imply action; the code delivers marking only.
- If team-lead's intent was "P9B lands marker, P9C lands actuator", this must be in BOTH the law doc (§17 amendment) AND the P9C forward-log. Currently it is in NEITHER.

## Multi-Perspective Notes

- **Executor**: code is clean and testable. Critical issue is not in the code delivered; it is in the absence of a seam that should accompany it.
- **Stakeholder**: User ruling "same commit" was respected. The three DT closures landed together. CRITICAL-1 would have been caught under either Route-A or Route-B if the contract had required a relationship antibody for DT#2.
- **Skeptic**: The strongest counterargument to CRITICAL-1 is "the contract explicitly defers in-cycle order posting — 'not in-cycle — low-risk + testable sweep'". But "not in-cycle" means P9C or later posts the orders; currently NO phase/cycle/lane posts them. There is no consumer at all.
- **Security-/ops-engineer lens**: In a real RED event, the bot's dashboard would show `force_exit_sweep.attempted=N` while positions quietly sit unsold. Operator confusion + real money exposure. Classic "green dashboard, red reality" — the Zeus constitution's canonical failure mode.

## Fitz Four-Constraints Lens

1. **Structural Decisions > Patches**: 3 DT closures + 2 stale-antibody flips = claimed 3 structural decisions. Actual: 2 structural (DT#5, DT#7-scaffold) + 1 patch masquerading as structural (DT#2 marker without actuator). The structural decision for DT#2 — "how do we make exit-sweep executable in-cycle or next-cycle" — was not made.
2. **Translation Loss**: DT#5 genuinely structural (probe confirms). DT#7 function is precise prose→code (low loss). DT#2 contract→code DROPPED the "exit happens" semantic: prose says "sweep on active positions → sell orders"; code says "mark exit_reason → (nothing)".
3. **Immune System > Security Guard**: R-BW + R-BX are structural antibodies (surgical revert fires). R-BV is a security-guard antibody that detects no pathogen (the marker being unset) while the real pathogen (no sell order) is invisible.
4. **Data Provenance**: N/A for P9B.

## Self-audit (cycle-2 L4 bias correction)

Cycle 3's pressure was two-sided:
- **Pressure to break the streak**: 3 consecutive first-try PASSes + explicit adversarial-mode directive + "cycle 3 is my last" finality. Temptation to manufacture findings.
- **Pressure to confirm team-lead's narrative**: 4 structural fixes were delivered with genuine effort; the contract is detailed; the antibodies are present.

Calibration check:
- CRITICAL-1 survived three independent empirical probes: grep (no readers), runtime `evaluate_exit` (no should_exit), runtime `check_pending_exits` (wrong exit_state filter). This is not a manufactured finding.
- MINOR-1 and MINOR-2 match the author's own forward-log (DT#7 + migration). Documented deferrals promoted to MINOR severity because the deferral chains have dependencies.
- CRITICAL-1 would have been missed by cycle-1 static review (pre-empirical-probe era). This is exactly the case where cycle-2's surgical-revert-probe methodology pays off: the probe (revert one line + see if antibody fires) confirms the DT#5 and DT#7 fixes as genuine AND the live-runtime probe (trace what reads the marker) surfaces the DT#2 gap.

**Conclusion**: No manufactured findings. PASS would have been dishonest.

## Verdict Justification

**ITERATE — CRITICAL-1 requires a fix before P9C opens.**

The fix can be small (option (a) above: ~10-line edit to `evaluate_exit` + one new relationship antibody). Alternatively team-lead can argue (c): amend law §17 + add P9C forward-log entry, but this shifts DT#2 from "closed" to "structurally-incomplete" — the P9B commit message would need to stop claiming DT#2 is closed.

- Mode upgrade: ADVERSARIAL → Mandatory because of the streak-risk opening directive. Empirical probes found a CRITICAL that static review would have missed.
- Regression math matches exactly. All P9B antibodies GREEN. Hard constraints preserved. DT#5 and DT#7 are genuinely structural.
- DT#2 fails the law-compliance test. INV-05 specifically forbids advisory-only RED behavior, which is what P9B ships.

## Streak-analysis

P7B + P8 + P9A = 3 consecutive first-try PASSes. P9B = ITERATE. The streak breaks at 3.

**Interpretation**: the 3-streak was real but contract scope was contracting (P7B was 2 seams; P8 was 2 seams; P9A was absorption of P8 forward-log). P9B was 3 DT closures in ONE commit — highest surface of any recent phase. Wider surface + streak-induced complacency risk (acknowledged but counteracted by adversarial opening) = likely to surface a structural miss.

The adversarial-opening directive delivered exactly its intended value: a CRITICAL that polite review would have missed.

## Open Questions (unscored)

1. Is DT#2 intended to ship with actuator (my read of §17) or as marker-only with P9C actuator (team-lead's read)? User adjudication needed.
2. If marker-only is acceptable, should law §17 and the P9C forward-log be updated to reflect "DT#2 P1 (marker) / DT#2 P2 (actuator)" split?
3. Should a relationship antibody for DT#2 actuator be required BEFORE P9C opens (not after), so dave's cycle-1 entry criterion includes "DT#2 relationship antibody RED"?

## Cycle-3 methodology notes

- Adversarial-opening payoff: CRITICAL-1 surfaced at minute ~12 via Hunt 2 empirical probe. Without this directive, the review would have PASSED on the strength of R-BV unit test.
- Surgical-revert probes (cycle-2 L8) confirmed DT#5 + DT#7 as genuine. One line reverted → test fails → antibody fires correctly.
- Runtime probes (new cycle-3 technique): when a contract claims "X machinery consumes Y", write a 20-line script that constructs the object with Y set and runs the X machinery to see what happens. Cheaper than reading hundreds of lines of code.
- 4-consecutive-PASS streak did NOT materialize. Rotation to critic-dave now carries an explicit "fresh eyes" benefit since my methodology has been confirmed productive but not infallible (3 PASS + 1 ITERATE over 4 phases).

## Files audited

- `src/engine/cycle_runner.py` L48-103 (sweep function) + L296-320 (integration)
- `src/strategy/kelly.py` L10-80 (polymorphic signature + type-gated assert)
- `src/contracts/execution_price.py` L23-149 (contract underlying DT#5)
- `src/contracts/boundary_policy.py` FULL (DT#7 scaffold)
- `src/engine/evaluator.py` L170-220 (kelly_size callsite upgrade + shadow-raw lane)
- `src/engine/replay.py` L1290-1310 (unmigrated kelly_size caller)
- `src/engine/cycle_runtime.py` L560-650 (monitor phase — confirmed does NOT consume pos.exit_reason)
- `src/execution/exit_lifecycle.py` L247-260 (handle_exit_pending_missing), L500-580 (check_pending_exits — confirmed filters on exit_state not exit_reason)
- `src/engine/monitor_refresh.py` (confirmed zero exit_reason references)
- `tests/test_dual_track_law_stubs.py` L121-340 (R-BV, R-BW, R-BX)
- `tests/test_no_bare_float_seams.py` L270-312 (stale-antibody flip 1)
- `tests/test_runtime_guards.py` L1268-1290 (stale-antibody flip 2)
- `docs/authority/zeus_current_architecture.md` §17 (INV-05 law)
- `docs/authority/zeus_dual_track_architecture.md` §DT#2 (extension to INV-05)
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase9b_contract.md` (full)

---

*Critic: critic-carol cycle 3 (Opus sniper-mode Gen-Verifier), 2026-04-18.*
*Inherited methodology (cycle 1 + 2 + beth's 3): L0.0 peer-not-suspect, two-seam, P3.1 vocab + extended `_currently_|_entry_block_only_`, baseline-restore + surgical-revert + runtime-probe, pre-commitment, deferral-with-rationale, checkbox-antibody fingerprint detection, aspirational-doc translation-loss detection.*
*Rotation trigger*: cycle 3 concludes the 3-cycle convention. Regardless of next verdict on the ITERATE fix, critic-dave opens P9C. Hand-off brief: `phase9_evidence/critic_dave_onboarding_brief.md`.
