# Adversarial Review — `_direction_admitted` divergence fix

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: live money-path pre-deploy review; root REVIEW.md + docs/review/code_review.md; Tier-0 surface (src/decision/** money path)
- Branch: live/iteration-2026-06-13
- File under review: src/decision/family_decision_engine.py (uncommitted, 2-edit diff)
- Reviewer: code-reviewer (read-only)

## Verdict: SHIP

No CRITICAL or HIGH issue at HIGH confidence. The fix is correct, minimal, and provably
collapses the after_direction admission and the live_candidate_passes re-proof onto a
single predicate so they cannot diverge.

---

## Bug confirmed (independent verification)

The described silent-undo is real and reproduced from git history:

- Relaxation commit `3c4aeecc75` (ancestor of HEAD — `git merge-base --is-ancestor` = YES)
  changed `after_direction` from `[d ... if d.direction_law_ok]` to
  `[d ... if d.direction_law_ok or (d.route.side=="NO" and d.economics.edge_lcb>0.0)]`,
  BUT left the final re-proof at
  `family_decision_engine.py:924 (in 3c4aeecc75) direction_law_proof_present=d.direction_law_ok`.
- `live_candidate_passes` (payoff_vector.py:742-748) hard-ANDs `direction_law_proof_present`.
  For a NO-on-modal candidate `direction_law_ok == False`, so the re-proof returned False →
  the exact harvest class the relaxation admitted (settlement NO win 0.778, after-cost +0.125)
  was silently re-zeroed. Confirmed verbatim from `git show 3c4aeecc75`.

The working-tree fix defines `_direction_admitted(d)` once and uses it at BOTH sites.

---

## HUNT findings

### 1. CORRECTNESS — re-proof admits EXACTLY the after_direction set. PASS (confidence HIGH)
- `after_direction` filter (line 910) and the re-proof arg (line 935) BOTH call the
  identical closure `_direction_admitted(d) = d.direction_law_ok or (d.route.side=="NO"
  and d.economics.edge_lcb>0.0)` (lines 905-908). Same function object, same fields,
  textually one definition. No second copy to drift.
- The three fields the predicate reads are immutable between call sites: `CandidateDecision`
  (line 309), `CandidateRoute` (payoff_vector.py:185), `CandidateEconomics`
  (payoff_vector.py:225) are all `@dataclass(frozen=True)`. `direction_law_ok`, `route.side`,
  `economics.edge_lcb` cannot mutate between line 910 and line 935 → re-proof predicate
  value is byte-identical to the admission predicate value for every surviving candidate.
  No "passes after_direction but re-proof differs" case exists.

### 2. NO OVER-ADMISSION — other live-pass gates unchanged. PASS (confidence HIGH)
- `live_candidate_passes` body (payoff_vector.py:742-748) is NOT in the diff. It still ANDs:
  `edge_lcb>0`, `delta_u_at_min>0`, `optimal_delta_u>0`, `route_cost.executable`,
  `direction_law_proof_present`, `market_coherence_accepted`. The fix relaxes ONLY the
  `direction_law_proof_present` ARGUMENT passed from `_select`, and only via a predicate
  that itself requires `side=="NO" and edge_lcb>0`.
- Non-modal YES (`direction_law_ok=False`, `side=="YES"`) → `_direction_admitted` is
  `False or (False and ...) = False` → excluded at after_direction AND would fail the
  re-proof. STAYS BANNED. Correct (it graded after-cost negative).
- `delta_u_at_min>0` is enforced ONLY inside `live_candidate_passes` (the pre-pass survivor
  filter at line 921 checks `edge_lcb>0 and optimal_delta_u>0`, NOT delta_u_at_min). The fix
  does not touch that, so the delta_u_at_min gate remains active for the relaxed class — good,
  no weakening.

### 3. DIRECTION-LAW INTEGRITY. PASS (confidence HIGH)
- Modal-YES and non-modal-NO both have `direction_law_ok=True` → `_direction_admitted`
  short-circuits True on the first disjunct, UNCHANGED behavior (admitted exactly as before;
  the second disjunct is never reached). NO behavior change for the legal classes.
- NO-on-modal (`direction_law_ok=False`, `side=="NO"`) is admitted ONLY when
  `edge_lcb>0.0` — strict `>`, never unconditional. The conservative `q_no_lcb>cost` gate is
  the sole admission key for this class, exactly as specified.

### 4. COHERENCE / ΔU ORDER. PASS (confidence HIGH)
- Gate order is preserved and unchanged:
  executable (870) → after_direction (910) → coherence (914) → survivors edge_lcb>0 &
  optimal_delta_u>0 (918-922) → live_candidate_passes re-proof (929-938) → argmax (945).
  The diff edits only the predicate inside steps 910 and 935; it does not reorder, remove,
  or skip any stage. `market_coherence_accepted=d.coherence_allows` is still passed
  independently into the re-proof (line 936), so coherence is still double-enforced.

### 5. OTHER DEFECTS — scoping, comment accuracy, double-eval. PASS (confidence HIGH)
- Scoping: `_direction_admitted` is a local `def` inside `_select`, defined at line 905
  BEFORE both uses (910, 935). No closure-capture hazard — it takes `d` as a parameter and
  closes over nothing mutable. `python3 -m py_compile` = COMPILE_OK.
- Double evaluation: the predicate runs once per candidate at after_direction, then once
  more per survivor at the re-proof. Pure boolean field reads on frozen dataclasses, O(1),
  no side effects, deterministic — re-evaluation is free and cannot disagree. Acceptable.
- Comment accuracy: the new comments (896-904, 923-928) correctly describe the historical
  divergence, name the real ancestor commit `3c4aeecc75`, and correctly state
  live_candidate_passes hard-requires `direction_law_proof_present=True`. Verified against
  payoff_vector.py:747. No misleading comment.

## Stage-1 spec compliance
Solves exactly the stated problem (re-unify the two direction predicates). No scope creep,
nothing extra, nothing missing. The requester would recognize this as their request.

## lsp_diagnostics note
`ty` language server not installed in this env (lsp_diagnostics unavailable). Substituted
`python3 -m py_compile` → COMPILE_OK and an AST/field-immutability audit (all three
dataclasses frozen). Type-safety claim rests on: no new types introduced, predicate returns
`bool` from boolean field reads, identical to the pre-existing inline expression.

## Positive observations
- Single-source-of-truth predicate is the right structural fix — eliminates the divergence
  class entirely rather than patching one call site.
- Frozen dataclasses make the "two evaluations agree" property a compile-time guarantee, not
  a hope.
- The comment block preserves the why (and the real commit hash) so the next session does not
  re-derive the bug. Good provenance discipline.

## Open Questions (low-confidence, non-blocking)
- None. No low-confidence CRITICAL/HIGH findings to surface.

## Recommendation: SHIP
