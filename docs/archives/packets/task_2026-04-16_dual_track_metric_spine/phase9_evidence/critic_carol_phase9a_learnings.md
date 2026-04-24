# critic-carol cycle-2 (P9A) durable learnings

**Continuity**: Cycle 2 of 3 before rotation to critic-dave. Inherits cycle-1 learnings from `phase8_evidence/critic_carol_phase8_learnings.md`. This doc appends post-P9A observations and refines cycle-1 patterns.

## New learnings from P9A (cycle 2)

### L1 — Empirical probes close the text-match skepticism loop
Cycle 1 I flagged R-BQ.1's literal-string match as MAJOR-4. Cycle 2 team-lead rewrote to "ANY RuntimeError". My skepticism ("trades text-match for over-broad catch") was refutable by running a 30-line probe that injects a novel RuntimeError.

**LESSON**: When reviewing a fix to a test antibody, the decisive audit is NOT re-reading the test — it's running a regression probe where I break the fix and confirm the test fails. This is cheap and almost always feasible. Apply routinely to all "structural hardening" claims.

### L2 — Checkbox antibodies have a recognizable shape
R-BS.2's pattern `truth_found = ...; assert save_path.exists()` is a fingerprint. The computation of `truth_found` is never asserted; the tail assertion is trivially true after a successful save.

**LESSON**: Grep the test for intermediate variables (like `truth_found`, `match_count`, `captured_*`) and verify each appears in an `assert` statement. If it only appears in a conditional that falls through to a weaker assertion, the test is vacuous. Make this a standing grep in antibody reviews.

### L3 — Aspirational doc ≠ enforced law
DT#6 Interpretation B says "save_portfolio MAY proceed PROVIDED external-authority origin". No code enforces "external-authority". This is pure caller-side discipline encoded in prose, which translation-loss will erase.

**LESSON**: When auditing a law-doc update, grep the referenced function for runtime checks matching the doc's clauses. If the clause is prose-only, flag it (even if accepting as MINOR for scope). The doc may be accurate at write-time; translation-loss erodes it by cycle N.

### L4 — Self-audit bias is real
I came in expecting to find that the fixes were weaker than claimed. Reality: 4/4 of my cycle-1 MAJORs had real structural fixes; the only weakness was cycle-2's R-BS.2 (which I correctly predicted as a new finding).

**LESSON**: The bias to find "gotcha" fixes is real. Balance by actually probing — probes either confirm the fix or decisively refute it. Do not manufacture findings under self-audit pressure; PASS a genuinely good fix even when it would be more satisfying to ITERATE.

### L5 — Pre-commit predictions that miss are also useful signal
Cycle 1 hit 5/5; cycle 2 hit ~2.5/5. Lower hit-rate correlated with a stronger commit (fix-focused absorbs tend to close exactly the gaps the predictor anticipated).

**LESSON**: A cycle where my pre-commit predictions all miss is NOT a failed review; it's a signal that the author actually addressed the predicted problems. The critic's job is to verify that missing, not invent substitutes. Embrace the miss.

### L6 — 3-pass streak is real complacency warning
P7B + P8 + P9A all first-try PASS. Cycle-1 explicitly warned about this.

**LESSON**: The honest move is to flag to the rotation successor rather than artificially hunt for issues. PASS with a streak-warning is more valuable than ITERATE with manufactured findings. Document the streak visibility in the wide review so next critic enters with appropriate prior.

### L7 (inherited from cycle 1, STILL VALID) — Paired-antibody pattern (negative + positive)
R-BU.1/2 pair (mode=WU_SWEEP+metric=low → warning; mode=WU_SWEEP+metric=high → no warning) is the same pattern as cycle-1's R-BP.1/2. Prevents both "false negative" (warning never fires) and "false positive" (warning fires when it shouldn't).

Continues to be worth demanding in future reviews. Each P3.1 guard-removal or contract-inversion should have a paired NEGATIVE+POSITIVE antibody.

### L8 (inherited from cycle 1, REFINED) — Surgical-revert probes > full baseline-restore
Cycle 1 taught me to checkout the pre-fix file and re-run tests. Cycle 2 I refined: write targeted probes that surgically break ONE LINE (e.g., revert elif tuple) to confirm the antibody catches the regression.

Surgical-revert is cheaper than full baseline-restore AND more decisive (isolates the exact line under test). For cycle 3, use surgical-revert probes by default.

## Signals to watch in future phases

- **Phase that rewires risk_state / status_summary read lanes**: check for the `tick_with_portfolio` persistence gap fix (may surface during P9B DT#2/#5 work).
- **Phase that adds new DATA_DEGRADED producers**: check that `entries_blocked_reason` tuple includes it (pattern template established in P9A).
- **Any phase that adds metric-aware SQL filter (P9C B093 half-2)**: verify both SQL WHERE clause AND cache-key changes.
- **Any commit that removes a `raise` guard or clarifies an ambiguous law**: look for text-match antibody anti-pattern (L2) + aspirational-doc anti-pattern (L3) in replacement.
- **Test files with intermediate variables in conditional-assertion shape**: L2 fingerprint; likely checkbox antibodies.

## Cycle-1 → Cycle-2 refinement summary

| Pattern | Cycle 1 | Cycle 2 refinement |
|---|---|---|
| Empirical probes | 1 (baseline-restore) | 4 (baseline + 3 surgical-revert) |
| Text-match skepticism | Flagged as latent bomb | Validated by probe; fix confirmed genuinely structural |
| Observability drift severity | Proposed as MAJOR class | Confirmed by P9A commit: 3-signal redundancy is the structural answer |
| Paired antibodies | Recommended pattern | Demonstrated value (R-BU.1/2 matches R-BP.1/2) |
| Self-audit bias | Unaware | Explicit awareness + L4 correction protocol |

## 3-pass streak analysis + rotation recommendation

P7B (critic-beth cycle 3) → P8 (critic-carol cycle 1) → P9A (critic-carol cycle 2) = 3 consecutive first-try PASSes.

**Observations**:
- The pattern is real. It correlates with contract-first + small-surface + pair-based antibody design (cycle-1 L7).
- The pattern is risky. 3 passes increases blind-spot probability.
- critic-carol has been scrutinizing her own cycle-1 findings in cycle 2; this is a useful discipline but also a risk (self-audit bias, L4).

**Rotation recommendation**:
- **P9B (critic-carol cycle 3)**: proceed, with explicit ADVERSARIAL mode for the first 15 minutes. Deliberately hunt for issues: grep for patterns I've dismissed as PASS-worthy; stress-test assumption boundaries.
- **P9C (critic-dave cycle 1)**: fresh eyes. Brief should include inherited critic-beth + critic-carol learnings (expected reading time ~15 min) plus explicit note: "3-pass streak from cycle-carol — treat pass-worthy assessment as prior but not evidence."

## Meta — cycle 2 artifact persistence note

Write/Edit blocked (inherited from cycle 1). Team-lead persisted this file + wide-review on my behalf. Same methodology gap noted in cycle 1. Recommend OMC agent-definition review: critic's wide-review deliverables ARE disk artifacts; Write/Edit should align.

Additional note for cycle 3: the directory convention is
- `phase8_evidence/` for P8 cycle-1 (critic-carol)
- `phase9_evidence/` for P9A cycle-2+ (critic-carol) + P9B/P9C
- `phase7_evidence/` for P7A/B (critic-beth)

Team-lead should include correct directory path in critic brief prompts.

---

*Authored*: critic-carol cycle 2 (Opus, sniper-mode Gen-Verifier), 2026-04-18
*Preserved-by*: team-lead (Write/Edit on critic's behalf)
*Next cycle opens*: P9B (DT#2 + DT#5 + DT#7 risk-critical packet). Explicit adversarial-mode recommendation for cycle 3 entry.
