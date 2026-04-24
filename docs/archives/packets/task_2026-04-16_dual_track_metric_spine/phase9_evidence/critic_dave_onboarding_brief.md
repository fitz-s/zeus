# critic-dave onboarding brief (spawning for P9C)

**Prepared by**: critic-carol cycle 3 (final), 2026-04-18
**Status of P9B**: ITERATE — CRITICAL-1 requires a pre-P9C fix (DT#2 sweep is inert; violates INV-05). See `phase9_evidence/critic_carol_phase9b_wide_review.md`.

## What you are inheriting

You are critic rotation 2-of-N. critic-beth ran 3 cycles (P6/P7A/P7B), then rotated. I ran 3 cycles (P8/P9A/P9B), now rotating. You open P9C as cycle-1-of-3 (rotate at P9E or equivalent).

### Mandatory onboarding reading (in order)

~20 min total:
1. `phase9_evidence/critic_carol_phase9b_learnings.md` — cycle-3 learnings + cross-cycle distillation (13 L-numbered learnings + methodology toolkit). **Read this FIRST** — it is the consolidation of everything.
2. `phase9_evidence/critic_carol_phase9b_wide_review.md` — the CRITICAL-1 finding is your P9C entry context. If team-lead has patched it pre-P9C, understand what the patch does.
3. `phase9_evidence/critic_carol_phase9a_learnings.md` — cycle-2 learnings (checkbox-antibody fingerprint, surgical-revert, aspirational-doc).
4. `phase8_evidence/critic_carol_phase8_learnings.md` — cycle-1 learnings (observability drift, silent-return path, paired-antibody pattern).
5. `phase7_evidence/critic_beth_phase7a_learnings.md` + `critic_beth_phase7b_learnings.md` — beth's 3-cycle run (broken monkeypatches, P3.1 vocab, deferral-with-rationale).
6. `docs/authority/zeus_current_architecture.md` + `docs/authority/zeus_dual_track_architecture.md` — law.
7. `docs/operations/task_2026-04-16_dual_track_metric_spine/team_lead_operating_contract.md` — P1.1, P2.1, P3.1 methodology.

## Prior PASS streak — explicit "prior not evidence" note

**P7B + P8 + P9A = 3 first-try PASSes.** **P9B broke the streak with ITERATE**.

**Key lesson for your cycle 1 opening**:
- The 3-streak correlated with small-surface + contract-first + pair-based antibody commits.
- The streak broke at P9B when the commit expanded to 3-DT-closures-in-one-commit.
- Adversarial opening (cycle-2 L6 recommendation, L12 validation) delivered — it surfaced a CRITICAL static review would have missed.

**Do not infer from the prior PASSes that team-lead's work is consistently above scrutiny.** The PASSes were genuine on small surfaces; the ITERATE emerged on expanded surface. Your P9C prior should be: adversarial-opening for first cycle (default). Earn the streak again cycle-by-cycle.

**Do not infer from the P9B ITERATE that team-lead's work is sloppy.** DT#5 and DT#7 landed clean on surgical-revert probing. DT#2's issue was a subtle structural gap that a contract-review gate could have caught before execution. The lesson is "verify consumer-claims at runtime", not "distrust the executor".

## Priority entry tasks for P9C cycle 1

### Task 0 (before P9C review): verify CRITICAL-1 resolution

If team-lead ships a pre-P9C fix for DT#2 sweep actuator, your first job is to verify the fix:
- Is there now a consumer of `pos.exit_reason == "red_force_exit"` that actually posts a sell order?
- Is there a **relationship antibody** (cycle-3 L11) that GIVEN a sweep-marked position + healthy ExitContext, WHEN a cycle runs, THEN a sell order is posted?
- Surgical-revert the fix — does the new antibody FAIL correctly?
- If the fix is a law amendment (option (c) in my review), is the law doc updated AND the P9C forward-log updated to include "DT#2 actuator" as a tracked debt?

If team-lead defers the fix to P9C scope, treat DT#2 actuator as a P9C entry requirement (should be in the P9C contract as a gate).

### Task 1: P9C expected scope (per current forward-log)

P9C is expected to include:
- DT#7 full enforcement (evaluator wiring + leverage reduction + oracle penalty isolation)
- `monitor_refresh.py` LOW wiring
- `Day0LowNowcastSignal.p_vector` proper impl (Gate F prep)
- Possibly: `--temperature-metric` CLI flag
- Possibly: `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` re-audit
- Possibly: `save_portfolio` `source:` param for DT#6 B enforcement

**Expected larger surface than P9B**. Apply cycle-3 L12: adversarial throughout, not just opening.

### Task 2: watch for these specific patterns

Per cycle-3 L9 (runtime-probe > static review for consumer-claimed seams):
- P9C will wire boundary_ambiguous → evaluator. Verify the wiring uses `boundary_ambiguous_refuses_signal` (my cycle-3 MINOR-1). If evaluator recomputes the logic locally, DT#7 function becomes orphan forever.
- P9C will wire monitor_refresh LOW. Verify actually-used fields are read (no dead write-side seams). **Two-seam audit mandatory** (beth's foundational learning).
- If P9C ships "marker-based" anything, demand relationship antibody (cycle-3 L11).

### Task 3: inherited MINOR-2 from cycle 3

`replay.py:1300` still calls `kelly_size(..., entry_price, ...)` with bare float. Polymorphic `kelly_size` accepts it without `assert_kelly_safe`. Shadow/backtest lane misses DT#5 enforcement. Forward-logged as migration chore but this is a real enforcement gap. Flag if P9C touches replay.py without closing this.

## Recommended opening mode

**ADVERSARIAL** (first 15 min minimum, possibly throughout):
- Surface is larger (multi-DT + monitor_refresh + LOW wiring).
- Prior cycle ITERATE indicates bugs-per-surface is not zero.
- You are cycle-1 of rotation — fresh eyes are your comparative advantage; use them for the patterns critic-carol normalized.

## Calibration warning

I (carol) gave 3 PASS + 1 ITERATE. Base rate from beth's 3 cycles: 1 ITERATE + 2 PASS. 6 cycles total: 5 PASS + 1 ITERATE + 1 ITERATE-CRITICAL = baseline ~17% non-PASS.

If you find nothing after full review, consider one more adversarial-probe round before PASSing. But do NOT manufacture findings (cycle-2 L4). If empirical probes confirm all claims, PASS is honest.

## Artifact persistence

Cycle 3 resolved the Write/Edit gap: you should be spawned as `general-purpose` subagent_type, not `critic`. You can persist your own artifacts. Directory convention:
- `phase9_evidence/` for P9B (my cycle 3) + P9C (your cycle 1) — shared directory across P9-series.
- `phase10_evidence/` or further for later phases.

Your wide-review path: `phase9_evidence/critic_dave_phase9c_wide_review.md`.
Your learnings path: `phase9_evidence/critic_dave_phase9c_learnings.md` (or `phase10_evidence/` if P10 is the first).

## Questions to ask team-lead at P9C handoff

Before you begin your review, get answers to:
1. Was DT#2 CRITICAL-1 fixed pre-P9C or folded into P9C scope?
2. If folded, does P9C contract include a relationship antibody for DT#2 actuator?
3. Does the P9C forward-log now include DT#2 actuator as tracked debt?
4. Has `monitor_refresh.py` LOW wiring been pre-audited for cross-seam symmetry (beth's foundational read-seam-audit heuristic)?

## Handoff completeness check

- [x] 13 cumulative learnings consolidated in `critic_carol_phase9b_learnings.md`
- [x] P9B wide review with CRITICAL-1 evidence captured in `critic_carol_phase9b_wide_review.md`
- [x] This onboarding brief with task priorities + streak-context
- [x] Cycle-3 methodology tools inherited: pre-commit predictions, P3.1 extended vocab, surgical-revert, runtime-probe, duck-type/subclass probes, regression-math exactness, hard-constraint grep.
- [x] Fingerprint catalog: checkbox antibody, aspirational doc, marker-only refactor, text-match antibody, silent-return path, broken monkeypatch silent-GREEN.

Good luck. The methodology compounds when preserved; the critic role's value grows with each disk-durable cycle.

---

*Authored*: critic-carol cycle 3 (final), 2026-04-18.
*Reads-by*: critic-dave cycle 1, first invocation of P9C review.
