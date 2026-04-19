# critic-carol cycle 3 — P9B RE-VERIFY VERDICT

**Reviewer:** critic-carol (cycle 3, re-verify pass — retirement cycle)
**Target:** commit `b73927c` — fix(phase9b): ITERATE resolution — DT#2 actuator wiring (CRITICAL-1 resolved)
**Basis:** my own cycle-3 ITERATE delivered on `0974a62` (CRITICAL-1: DT#2 force-exit sweep was INERT; recommended fix option (a) = add branch in `evaluate_exit` + relationship antibody).
**Date:** 2026-04-18
**Created:** 2026-04-18 / **Last reused/audited:** 2026-04-18
**Authority basis:** Phase 9B contract, `docs/authority/zeus_dual_track_architecture.md` §DT#2 / INV-05 / INV-19

---

## VERDICT: **PASS**

The ITERATE fix at `b73927c` correctly resolves CRITICAL-1. DT#2's sweep marker is no longer inert: a sweep-written `exit_reason="red_force_exit"` now produces a real `ExitDecision(should_exit=True, trigger="RED_FORCE_EXIT", urgency="immediate")` on the next `evaluate_exit` call. Day0 positions are correctly gated out. Regression math matches commit claim exactly. Antibody pair (R-BY + R-BY.2) is structural, not checkbox.

**P9B closes at `b73927c`.** No further ITERATE.

---

## Probe results

### P1 — Fix correctness (surgical-revert PASS)

Deleted lines 310-329 of `src/state/portfolio.py` (the new RED short-circuit branch) and ran R-BY + R-BY.2.

**Result:** R-BY FAILS with `AssertionError: trigger must be RED_FORCE_EXIT; got 'VIG_EXTREME'`. R-BY.2 passes (because the fallback path for day0 also returns a non-RED trigger).

- The R-BY assertion on `decision.trigger == "RED_FORCE_EXIT"` is load-bearing — it is what discriminates "fix wired" from "fix absent".
- Sub-observation (noted in cycle-3 learnings): the test's "healthy" context `(bid=0.39, ask=0.41, vig=0.02, fresh_prob=0.60, current_market_price=0.40)` happens to trigger `VIG_EXTREME` on the fallback path. This does NOT weaken R-BY — the assertion is on trigger IDENTITY, not should_exit boolean. But it means R-BY.2 is LESS discriminating than it could be (the fallback path naturally returns non-RED trigger for this dataset). A stronger R-BY.2 would construct a context that would naturally FAIL evaluate_exit (no vig extreme) and assert `should_exit=False` — that would catch the inverse regression where Day0 is accidentally force-exited via some other path. Filed to learnings.

After restore, R-BY + R-BY.2 both PASS. Git diff clean (verified via `git diff --stat`).

**P1: PASS.**

### P2 — R-BY structural vs. checkbox

R-BY is structural. It:
1. Constructs a real `Position` with `exit_reason="red_force_exit"` — the exact output of `_execute_force_exit_sweep` (cycle_runner:97).
2. Invokes `evaluate_exit(healthy_context)` — the actual runtime consumer that was MISSING pre-fix.
3. Asserts on the RETURNED `ExitDecision`'s fields: `should_exit`, `trigger`, `urgency`, `applied_validations` — the decision surface consumed by `exit_lifecycle` / `execute_exit`.

This is the exact cross-module shape my cycle-3 L9 runtime-probe learning demanded: "inspect the decision object, not marker presence." R-BY catches the inert-marker pathology that R-BV could not detect (R-BV only verified the WRITE side of the sweep; R-BY verifies the READ side → DECISION side).

**P2: PASS.**

### P3 — R-BY.2 pair-negative (surgical-revert PASS)

Removed the `and not exit_context.day0_active` condition on portfolio.py:316-319, leaving `if self.exit_reason == "red_force_exit"`.

**Result:** R-BY.2 FAILS with `AssertionError: Day0 position must NOT be short-circuited by RED marker; got trigger='RED_FORCE_EXIT'. The day0_active gate on the DT#2 branch has regressed.` R-BY continues to pass.

The Day0 gate is load-bearing — the test correctly catches its removal. This is the paired-antibody discipline: both false-negative (marker fails to fire) AND false-positive (Day0 misrouted) are locked.

After restore, both tests pass. Git diff clean.

**P3: PASS.**

### P4 — Branch precedence (RED before missing-authority)

The fix places the RED short-circuit BEFORE the missing-authority fail-closed check. Scenario: `exit_context` missing `fresh_prob` + `exit_reason="red_force_exit"`.
- **Pre-fix behavior**: `INCOMPLETE_EXIT_CONTEXT` → `should_exit=False`.
- **Post-fix behavior**: `RED_FORCE_EXIT` → `should_exit=True`.

**Law check** (`docs/authority/zeus_dual_track_architecture.md` §DT#2, lines 175-185):
> "RED must cancel all pending orders AND initiate an exit sweep on active positions."
> "The exit sweep runs even if it is more destructive than ORANGE behavior, because **RED is a truth claim about system integrity, not a throttle.**"
> "RED is not permitted to remain entry-block-only while existing positions sit untreated."

**Verdict**: the post-fix precedence is law-aligned. Missing price authority (best_bid stale, vig unavailable, etc.) is EXACTLY the kind of "more destructive than ORANGE" condition the law anticipates. The law's explicit stance is: under RED, "can't-know-current-price" does NOT override "can't-hold-the-risk". Exit at whatever the orderbook offers; risk containment takes precedence over price-quality gating.

The commit message articulates this correctly. No contradictions with law.

**P4: PASS** (precedence choice is correct).

### P5 — Scope (no bleed)

`git show --stat b73927c`:
```
 src/state/portfolio.py             |  32 ++++++++++
 tests/test_dual_track_law_stubs.py | 123 +++++++++++++++++++++++++++++++++++++
 2 files changed, 155 insertions(+)
```

Exactly the expected two files. No monitor_refresh changes (correctly deferred to P9C per contract). No evaluator, no v2 writes, no DDL, no TIGGE. Golden Window untouched.

**P5: PASS.**

### P6 — Regression math (exact)

Ran full suite at b73927c:
```
144 failed, 1856 passed, 93 skipped, 7 subtests passed in 46.26s
```

Commit claim: "144 failed / 1856 passed / 93 skipped (post-0974a62 baseline 144/1854/93; delta +2 passed exact match for R-BY/R-BY.2; zero new failures)."

**Exact match.** R-BY and R-BY.2 account for the +2. No prior P5/P6/P7A/P7B/P8/P9A/P9B-original antibody regressed.

**P6: PASS (exact).**

### P7 — Inert-marker pathology class (broader scan)

Searched src/ for `pos.exit_reason =` writes vs. `self.exit_reason` / `pos.exit_reason` reads:

**Writes** (5 sites):
- `cycle_runner.py:97` — `"red_force_exit"` (was inert pre-fix; NOW consumed by portfolio.py:317).
- `cycle_runtime.py:498` — admin_exit_reason copy.
- `cycle_runtime.py:624` — general setter.
- `portfolio.py:1207, 1238, 1273, 1296` — portfolio-internal setters.
- `fill_tracker.py:290` — fill-driven setter.

**Reads** (4 consumption sites):
- `portfolio.py:317` — NEW, DT#2 R-BY short-circuit.
- `portfolio.py:693-694` — ADMIN_EXITS membership check in `is_admin_exit`.
- `cycle_runtime.py:441` — fallback `"DEFERRED_SELL_FILL"` on filled_pos.
- `exit_lifecycle.py:575` — fallback `"DEFERRED_SELL_FILL"` in exit lifecycle.

**Verdict**: no other `exit_reason` value is written and never read. The fix closes the specific asymmetry. However, the TYPE of asymmetry (write-without-consumer) is a class-level pathology per my cycle-3 L11 learning — P9C should do a broader scan of lifecycle/risk marker fields (`exit_state`, `pre_exit_state`, `admin_exit_reason`, risk flags) for similar inert-marker patterns before they become next-cycle CRITICAL-1s.

**P7: PASS, with P9C forward-log item.**

---

## Streak outcome (predicted at cycle 2)

From my cycle-3 opening (carried over from cycle-2 L6 learning):
> "Three consecutive first-try PASSes (P7B, P8, P9A) is historically where reviewer vigilance collapses. I must enter cycle 3 with the prior probability that something IS wrong, even if nothing is visible. Static diff review is insufficient — runtime probing (L9) is required."

**Result:** exactly as predicted. P9B broke the streak at cycle 3. Static diff review would have passed `0974a62` (the sweep mechanism met its own contract; R-BV was green). Runtime probing — specifically `python -c "from ...; pos = Position(exit_reason='red_force_exit'); decision = pos.evaluate_exit(ctx); print(decision)"` — exposed `should_exit=False` on a RED-marked position, which is the CRITICAL-1 signal. Adversarial-opening directive delivered its value.

**The methodology is validated, not the reviewer.** A different reviewer running the same runtime probe would have caught the same gap. A reviewer running only static diff review — regardless of cycle count — would have missed it.

Streak table:
| Phase | Cycle | Verdict | Rationale |
|-------|-------|---------|-----------|
| P7B | 1 | PASS | Real fix landed |
| P8 | 1 | PASS | Clean absorption |
| P9A | 1 | PASS | Interpretation B honest-clarification |
| **P9B** | **3** | **ITERATE → PASS on b73927c** | **Inert marker caught via runtime probe; fixed cleanly** |

---

## Final retirement statement

**I, critic-carol, retire from the metric-spine reviewer rotation at the close of this cycle.**

Rotation rationale (per my cycle-2 L6 / cycle-3 wide review): sustained single-reviewer presence across 4+ phases induces complacency bias — the reviewer's own prior "nothing has gone wrong" becomes a blind spot independent of the code's actual state. Adversarial opening (cycle 3 stance) partially compensates but is not a permanent fix. Rotation IS the fix. Fresh eyes see what familiarity hides.

**Handoff**:
- Dave onboarding brief at `critic_dave_onboarding_brief.md` (already prepared cycle 3).
- 13 L-numbered learnings persisted at `critic_carol_phase9b_learnings.md` (plus retirement reflection appended below).
- P9C forward-log item: broader inert-marker / marker-without-consumer scan (not just `exit_reason` — include `exit_state`, `admin_exit_reason`, any risk/lifecycle flag that is written in one module and may or may not be read in another).
- Team-lead: dave takes over for P9C. If dave's onboarding brief is insufficient in any dimension, page team-lead, not me.

**Closing observation**: the most valuable thing I did across three cycles was not the static reviews (which caught minor issues and sometimes missed real ones), but the ONE runtime probe in cycle 3 that exercised `evaluate_exit` with a marked position. That single concrete invocation of the cross-module contract was worth more than every diff-reading pass combined. Dave: do this first, every time. Static review is necessary but never sufficient. The inert-marker pathology cannot be seen; it can only be executed.

— critic-carol, retired, 2026-04-18
