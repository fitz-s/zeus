# Critic-beth Learnings — Phase 7A → Phase 7B/8

**Date**: 2026-04-18
**Commit reviewed**: `a872e50` (metric-aware rebuild cutover)
**Verdict**: ITERATE-CRITICAL (1 CRITICAL + 2 MAJOR + 2 MINOR)

## Patterns that recurred vs Phase 6

1. **Write-side fix without read-side fix (pattern repeats)**. P6 had `RemainingMemberExtrema` protecting the write path, forcing reads through typed fields. P7A fixed the write-side metric tag at `_process_snapshot_v2:298` but left the observation-read side hardcoded to `high_temp`. Same category: "the seam that wrote was fixed; the seam that reads is still HIGH-only." When a phase claims "metric-aware", BOTH read and write seams must be audited. P3.1 already says "contract inversion" — extend to "contract is symmetric across read/write."

2. **Fixture-integration bypass pattern (methodology anti-pattern #4 recurs)**. R-BJ-1 atomicity test uses `patch("...rebuild_v2", side_effect=fake)` — outer SAVEPOINT is tested, inner SAVEPOINT nesting is trusted transitively. Also surfaced a pre-existing R-AZ-2 mirror test from P5C (try/except: pass swallows TypeError). Forward-log to replace with real E2E.

## New patterns surfaced in P7A

3. **Schema DEFAULT as accidental antibody regression**. Adding `DEFAULT 'high_temp'` to `observation_field` "for test fixture ergonomics" silently opened a CROSS-PAIRING category that `MetricIdentity.__post_init__` had been guarding at the Python layer. A rule: any ADD COLUMN or CHANGE DEFAULT on a column that participates in a typed-invariant (MetricIdentity, settlement contract, unit contract) MUST preserve the invariant at the SQL layer OR document why Python-layer enforcement is sufficient. Defaults that happen to pass a CHECK constraint ARE silent fallbacks — same category as the L3 checklist.

4. **Speculative schema columns without consumer**. P7A added `contract_version`, `boundary_min_value` that only the test fixture uses. Scaffolding-ahead is fine if documented; undocumented it becomes dead-schema that future phases inherit. Rule: every new column should be justified by a named consumer in the same commit OR in a commit-message forward-log.

## What the next critic should carry into P7B / P8

1. **P7B naming hygiene must not close this review's ITERATE items**. If team-lead folds CRITICAL-1 / MAJOR-1 / MAJOR-2 into P7B, the P7B critic needs to explicitly re-verify the read-seam + schema-default + backfill-gate fixes. "Naming pass" commits often don't attract a full wide-review — schedule one anyway.

2. **Phase 8 shadow activation is the detonator**. Under Zero-Data Golden Window, every CRITICAL/MAJOR finding here is dormant. Phase 8's first job before activating LOW shadow should be a full read-side audit of every "uses HIGH semantics" seam in rebuild / refit / ingest / evaluator / settlement paths. The P7A commit message's "metric-aware cutover" claim should be independently re-audited by P8's critic at commit candidate time.

3. **Observation-column symmetry**. Observations table has `high_temp` + `low_temp`. Every consumer of observations should take metric as input and select the correct column. Grep `SELECT.*high_temp FROM observations` and `SELECT.*low_temp FROM observations` across src/ and scripts/ — each hit needs to be audited for metric-agnosticism.

## P3.1 methodology — working as intended?

**Yes**. P3.1 caught zero false positives in P7A. Forward-facing antibodies R-BI-2 and R-BK-2 are the right shape. One refinement candidate: extend vocabulary to include `_requires_explicit_|_must_specify_|_no_default_` for required-kwarg antibodies. The commit that REMOVED a default AND installed a `spec_param.default is inspect.Parameter.empty` check is the paradigm example — P3.1's current vocabulary covers the RED-side (what was refused), not the new GREEN-side (what's now required). Both are useful antibody classes.

## Meta-observation on my own process

Pre-commitment predictions caught 4 of 6 real findings before the diff read. This is the second wide review where pre-commitment materially improved coverage vs passive reading. Recommend: pre-commit predictions become a standing first-step, not optional.

Escalating to ADVERSARIAL mode after CRITICAL-1 surfaced two additional findings (MAJOR-1, MAJOR-2) that "narrow hunt list" review would have missed. The methodology's `ADVERSARIAL = assume more hidden issues` heuristic is high-ROI.

(~295 words)
