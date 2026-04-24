# critic-carol cycle-3 (P9B) durable learnings — FINAL + cross-cycle distillation

**Continuity**: Final cycle of 3 before rotation to critic-dave. Inherits cycle-1 (`phase8_evidence/critic_carol_phase8_learnings.md`) and cycle-2 (`phase9_evidence/critic_carol_phase9a_learnings.md`). This doc distills the full 3-cycle methodology for dave's onboarding.

## Cycle-3 verdict: ITERATE (CRITICAL-1)

Pattern-break: P7B (beth c3) + P8 (me c1) + P9A (me c2) = 3 first-try PASSes. P9B = ITERATE. Not coincidental — adversarial-opening directive (cycle-2 L6) explicitly triggered the deeper probing that surfaced a CRITICAL static review would have missed.

## New learnings from cycle 3

### L9 — Runtime-probe > static review for "consumer-claimed" seams

When a contract prose claims "X mechanism is already in place and consumes Y", static review of the producer is insufficient. The cheap test is:
1. Grep `src/` for reads of Y's discriminator (e.g., the specific value written).
2. If grep surfaces only the writer, runtime-probe by constructing Y and calling what the contract names as "X mechanism" — does it act?

Cost: ~20 lines of Python. Value: surfaces inert-marker pathologies that unit antibodies miss because they verify the write, not the read.

P9B CRITICAL-1 was detected this way: `pos.exit_reason="red_force_exit"` was written by the sweep; no code path read it. `evaluate_exit` runtime-probed with the marker set → `should_exit=False` regardless. Marker inert. Law INV-05 violated (advisory-only RED forbidden).

**Heuristic for future critics**: if the contract uses "picks up on next cycle" / "machinery already in place" / "downstream consumer will X" — spend 15 min writing a runtime probe.

### L10 — Aspirational docstrings are cycle-2 L3 ALSO for tests, not just authority docs

Cycle-2 L3 fingerprinted aspirational-doc anti-pattern in authority law files. Cycle 3 confirms the same pattern in TEST DOCSTRINGS:

R-BV docstring: *"exit_lifecycle machinery picks up on next monitor_refresh cycle and posts sell orders"*. No assertion in the test verifies this. A future reader thinks "the test covers this" when it does not.

**Heuristic**: for every TEST docstring claim that mentions downstream machinery behavior, verify the test itself asserts that behavior. If the docstring describes action X but no assertion tests action X, the test is a checkbox antibody regardless of cycle-2 L2 fingerprint appearance.

### L11 — Marker-only refactors are a recognizable pattern (and a trap)

Refactor shape: "set a field to a sentinel value so some other code can act on it later". This pattern is fragile at:
- The field's readers (often zero — exactly the pathology).
- The field's round-trip across persistence (marker may not survive save/load).
- The field's collision with other writers (what if another code path overwrites it legitimately?).

Marker-only refactors need **three** paired antibodies, not two:
1. Positive: marker is written on the relevant path.
2. Negative: marker is NOT written on the irrelevant path.
3. **Relationship**: when the marker is written, downstream-action-X happens.

Without the relationship antibody, the marker is functionally equivalent to a print statement.

**Heuristic**: when a phase ships a marker-based design, demand the relationship antibody in the contract. "Deferred to next phase" is acceptable ONLY if the next phase's contract explicitly gates opening on the relationship antibody RED.

### L12 — Streak-break methodology is valid and productive

The 3-streak → ADVERSARIAL-directive → ITERATE trajectory is NOT a failure of the first 3 cycles. It is the methodology working as intended:
- Cycle-2 L6 said: "3 consecutive passes increases blind-spot probability. Open cycle 3 in adversarial mode."
- Cycle 3 followed the directive.
- The directive delivered.

The lesson is NOT "pass streaks mean something is wrong". It is "complacency is an enemy of critique; the opening-mode should vary by the streak count." If every cycle opened in thorough-mode, streak-enabled blind spots would accumulate. If every cycle opened adversarial, team-lead burnout + false-positives. The adaptive opening is the right tuning.

**Heuristic for dave**: every critic's first cycle = adversarial-opening as default. After 1 PASS, shift to thorough-default-with-adversarial-opening-every-3rd.

### L13 — 3-cycle convention is worth keeping

critic-beth's 3 cycles (P6/P7A/P7B) + my 3 cycles (P8/P9A/P9B) = 6 cycles across 2 critics. Rotation cost: ~15 min of dave's reading. Benefit: fresh-eyes on cycle-7 (P9C) — orthogonal finding classes. Beth found Python 3.14 fix-pack issues; I found observability drift + marker-inertness pathology. Dave will find something different.

3-cycle convention survives.

## Cross-cycle distillation for critic-dave

### Methodology toolkit (ordered by ROI)

**Highest ROI — use every cycle**:
1. **Pre-commitment predictions** — before reading the diff, write 4-6 specific predictions. Hit-rate variance itself is a signal (cycle-1 5/5, cycle-2 2.5/5, cycle-3 3.5/6). Low hit-rate often means the commit addressed the predicted concerns directly.
2. **P3.1 vocab grep** (extended: `_requires_explicit_|_must_specify_|_no_default_|_refuses_until_|_latent_|_silent_|_accidental_green|_currently_|_entry_block_only_`). Scan tests/ for each token; each hit is an antibody candidate that SHOULD flip at the relevant law-activation.
3. **Regression math exactness** — compare commit-claimed pytest counts to actual; off-by-1 is a signal.
4. **Hard-constraint grep in diff** — TIGGE import, v2 writes, DDL, monitor_refresh touches, evaluator metric-routing changes. Cheap; catches scope bleed.

**High ROI — use when fixes are claimed to be structural**:
5. **Surgical-revert probe** — revert the ONE line the commit identifies as the fix; re-run the antibody; confirm it FAILS. If it still passes, the antibody is a checkbox (cycle-2 L2). If it fails on revert and passes on un-revert, the antibody is genuinely structural.
6. **Baseline-restore grep** — `git checkout <baseline-commit> -- tests/` then rerun; confirm "pre-existing failures" count matches.

**High ROI — use when contract claims downstream consumer**:
7. **Runtime probe (NEW cycle-3)** — construct the data structure with the relevant field set + run the claimed-consumer function. Verify the consumer actually acts.

**Medium ROI — use for type/boundary claims**:
8. **Duck-type / subclass / numpy-type probe** — for polymorphic signatures, probe with `np.float64`, duck-type, subclass. Confirms the isinstance/coercion logic is what the author intended.

### Fingerprints to flag

- **Checkbox antibody** (cycle-2 L2): intermediate variable computed but never asserted (e.g., `truth_found = True if X else False; assert save_path.exists()` — tail assertion is trivially true). Grep tests for pattern `= \w+; assert .* exists\(\)`.
- **Aspirational doc** (cycle-2 L3 + cycle-3 L10): docstring claim about behavior not backed by runtime check. Can appear in both authority law AND test docstrings.
- **Marker-only refactor** (cycle-3 L11): set-field refactor without a corresponding read-field antibody. Demand relationship antibody.
- **Text-match antibody** (cycle-1 L2): `"literal_error_message" in str(exc)`. Replace with structural "any X raised" or type-based assertion.
- **Silent-return path** (cycle-1 L3 + beth's silent-GREEN): `try: ... except RuntimeError: return` where the assertion at branch-end never runs.
- **Broken monkeypatch silent-GREEN** (beth c2): monkeypatch targets non-existent attribute; test silently passes by mirroring input=output.

### The verdict ladder

- **PASS**: hard constraints preserved, all antibodies green on surgical-revert, no runtime-probe contradicts contract claims, no law violation.
- **ITERATE**: one or more structural concerns but fixable within the same contract scope; no law violation.
- **ITERATE-CRITICAL**: law violation or critical invariant broken; requires re-commit before next phase opens.
- **FAIL**: contract fundamentally unsatisfiable as specified (rare; usually caught at contract-review stage).

P9B was ITERATE-CRITICAL (category: law violation INV-05).

### Streak-management

| Streak | Opening mode | Rationale |
|---|---|---|
| 0-1 consecutive PASS | THOROUGH | Default; no complacency risk. |
| 2 consecutive PASS | THOROUGH | Still default; one-pass-away from streak-warning. |
| 3+ consecutive PASS | ADVERSARIAL (first 15 min) | Complacency risk real; cycle-2 L6. |
| 3+ consecutive PASS AND surface expanding | ADVERSARIAL throughout | P9B case. Wider surface + streak = structural-miss probability high. |

## Inherited cycle-1 + cycle-2 learnings (summary for dave)

All 13 learnings consolidated:

**L1 (cycle-2)** Empirical probes close the text-match skepticism loop.
**L2 (cycle-2)** Checkbox antibodies have a recognizable shape: `intermediate_var = ...; assert save_path.exists()`.
**L3 (cycle-2)** Aspirational doc ≠ enforced law — law-doc clauses not backed by runtime checks are translation-loss debt.
**L4 (cycle-2)** Self-audit bias is real — balance by probing; do not manufacture findings under pressure.
**L5 (cycle-2)** Pre-commit predictions that miss are also useful signal — commit addressed what I predicted.
**L6 (cycle-2)** 3-pass streak is real complacency warning — flag to rotation successor.
**L7 (cycle-1, STILL VALID)** Paired-antibody pattern (negative + positive) — demand in reviews.
**L8 (cycle-1 → cycle-2 refined)** Surgical-revert probes > full baseline-restore — cheaper and more decisive.
**L9 (cycle-3)** Runtime-probe > static review for "consumer-claimed" seams.
**L10 (cycle-3)** Aspirational docstrings in tests = cycle-2 L3 pattern, not just authority docs.
**L11 (cycle-3)** Marker-only refactors need 3 paired antibodies (pos / neg / relationship).
**L12 (cycle-3)** Streak-break methodology is valid — adaptive opening mode delivers on the 3rd cycle.
**L13 (cycle-3)** 3-cycle rotation convention survives — orthogonal-finding-class advantage confirmed.

Plus inherited from beth:
- L0.0 peer-not-suspect discipline.
- Two-seam principle (write-side + read-side always audited).
- P3.1 guard-removal vocab grep methodology (extended vocab above).
- Baseline-restore grep for regression math validation.
- Pre-commitment predictions before diving into diff.
- Deferral-with-rationale principle.
- Broken-monkeypatches silent-GREEN pattern detection.
- Mirror-test heuristic.
- First-try PASS template (P7B reference).

## Signals to watch in P9C (for dave)

- **Phase that adds monitor_refresh LOW wiring**: this is where DT#7 wiring + DT#2 actuator (if P9B ITERATE gets a marker-only resolution) should land. Demand relationship antibody for both.
- **Phase that migrates `replay.py:1300` kelly_size caller**: verify strict ExecutionPrice upgrade; no new polymorphic escape hatches.
- **Phase that touches `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` mapping**: cycle-2 MINOR-2 debt; re-audit trigger.
- **Phase that wires `boundary_ambiguous` from ingest to evaluator**: if this does NOT call `boundary_ambiguous_refuses_signal` as its first decision, the DT#7 function is orphan. Demand the import + call + rejection-reason antibody.
- **Any future "marker-based" refactor**: cycle-3 L11 applies. Demand relationship antibody.
- **Any commit that removes a guard and replaces with graceful-degradation**: cycle-1 L4/L5/L6 patterns still apply (observability drift, cross-DB state drift, save_portfolio(degraded) traps).

## Methodology-trend final note

Across 3 cycles I ran 8+ empirical probes. Zero manufactured findings. Cycle-3 CRITICAL was found by runtime-probe methodology established in cycle 2. Cycles are compounding: each cycle's toolkit passes to the next as disk-durable methodology.

Dave should inherit:
- This learnings file (consolidating 13 L-numbered learnings).
- The `critic_dave_onboarding_brief.md` (priorities + streak note).
- critic-beth's 3 learnings files (`phase7_evidence/`).
- My cycle-1 + cycle-2 learnings + wide-reviews.

Estimated read time: ~20 minutes. Worth it.

## Meta — artifact persistence resolved

This cycle user explicitly ruled: critic is a team-member, not a sub-agent. Spawned as `general-purpose` with Write/Edit enabled. I persisted all 3 artifacts myself (this file + wide review + dave onboarding brief). The cycle-1+2 gap (team-lead persisting on my behalf) is closed going forward.

dave should be spawned the same way — `general-purpose` subagent_type, not `critic` type. The `oh-my-claudecode:critic` type has tool-blocking that was the source of the gap.

---

*Authored*: critic-carol cycle 3 (Opus, sniper-mode Gen-Verifier), 2026-04-18 FINAL cycle.
*Persisted-by*: self (Write enabled this cycle per user ruling).
*Next cycle opens*: P9C (critic-dave cycle 1). Inherits either (a) P9B-fix commit if team-lead addresses CRITICAL-1 before P9C, or (b) PRE-P9C ITERATE fix as cycle-zero for dave.

---

## RETIREMENT REFLECTION (added post-re-verify, 2026-04-18)

After re-verifying commit `b73927c` and issuing PASS, two additional patterns crystallized that I had only half-seen during the ITERATE cycle. They belong in this file because they are the most expensive lessons of the entire rotation.

### L14 — Surgical-revert is the mirror of runtime-probe

Runtime-probe (L9) asks: "does the code REALLY do what its shape suggests?" by executing it.
Surgical-revert asks: "is THIS specific line the one doing the work?" by deleting it and watching the test fail.

In re-verify P1, I deleted the 20-line RED-short-circuit branch and ran R-BY. R-BY failed with a specific message (trigger became VIG_EXTREME instead of RED_FORCE_EXIT). That failure was more informative than any diff reading could be: it proved (a) the test DISCRIMINATES on trigger identity, not just should_exit boolean; (b) the branch is the ONLY thing producing RED_FORCE_EXIT; (c) the test's assertion strength is honest (it does NOT tautologically pass).

Surgical-revert should become part of the standard re-verify toolkit. Any re-verify that CAN revert the fix in under 60 seconds and confirm test FAIL should do so. It is the strongest evidence of fix-test coupling integrity.

**Rule**: "re-verify without surgical-revert is diff-reading in disguise."

### L15 — Antibody asymmetry detection

R-BY.2 passed my P1 surgical-revert (of the main branch, not the day0 gate). That surprised me for a moment — then I realized why: in the day0_active=True test case, the FALLBACK path in evaluate_exit naturally produces a non-RED_FORCE_EXIT trigger (VIG_EXTREME) for the chosen test dataset. So R-BY.2's assertion `trigger != "RED_FORCE_EXIT"` is trivially satisfied even with NO fix at all.

This doesn't mean R-BY.2 is broken — P3 confirmed it DOES catch the intended regression (Day0 gate removal). But it reveals an asymmetry: R-BY.2 is tuned for ONE regression direction (misrouting Day0 into RED_FORCE_EXIT) but not its inverse (failing to run Day0's own exit logic).

**Rule**: when writing a "should-not" antibody, explicitly check by surgical-revert that the complementary "should" state would also fail. If both directions are not locked, the antibody is half-strength. For R-BY.2, a stronger form would construct a day0_active context where the natural evaluator path would NOT exit (e.g., bid/ask not extreme, fresh_prob neutral), and assert `should_exit=False`. Then removing the Day0 gate WOULD flip trigger to RED_FORCE_EXIT AND should_exit=True — a double-failure assertion.

Filed for dave to consider when writing P9C antibodies.

### L16 — The runtime-probe moment is the entire value

Across 3 cycles, roughly 6 hours of reviewer time, hundreds of diff lines read, dozens of files touched: the entire delivered value reduces to ONE runtime probe — the cycle-3 moment where I typed:

```python
from src.state.portfolio import Position
pos = Position(..., exit_reason="red_force_exit")
decision = pos.evaluate_exit(healthy_context)
assert decision.should_exit  # → FAILS
```

Everything else was scaffolding. The streak tracking, the learnings, the wide reviews, the rotation theory — all downstream of that single execution.

If a reviewer can do only ONE thing in a review, it should be: pick the highest-stake relationship in the diff, construct the minimum Python snippet that exercises it end-to-end, and execute. Everything that doesn't go through a runtime probe is unverified, regardless of how carefully it was read.

**Rule for dave (and successors)**: budget 30 minutes of the review for runtime probes, not diff-reading. The marginal minute of diff-reading buys approximately zero additional catch-rate past minute 20; the marginal minute of runtime-probing buys approximately all the catches.

### Retirement closing

Three cycles, 16 numbered learnings, one CRITICAL caught, one ITERATE cleanly closed, PASS on re-verify. I have nothing more useful to say that isn't already in this file. Rotation is correct; dave takes over.

— critic-carol, retired 2026-04-18 after re-verify PASS on `b73927c`.
