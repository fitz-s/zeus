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

---

## Second-cycle observations (commit `c496c36`)

**Date**: 2026-04-18 (same-day second cycle)
**Verdict**: PASS — all 3 ITERATE findings closed on disk with real antibodies.

### What worked

1. **Sniper-mode cycle 2 delivered**. ~35 LOC across 3 source files + 2 fixture updates + 3 new antibody classes (R-BM/R-BN/R-BO). Small enough to verify in one reading; focused enough to avoid scope creep. This is the shape cycle-N should take — laser-focus on critic's findings, no opportunistic refactors folded in.

2. **Executor wrote REAL end-to-end antibodies, not mirror tests**. R-BM-2 inserts observations with both `high_temp=82.0` AND `low_temp=60.0` and asserts LOW-spec fetch returns 60.0. If CRITICAL-1 were still broken (hardcoded `high_temp`), the assert would catch it (82 vs 60). R-BM-3 tests NYC where `low_temp IS NULL` — if the query wrongly falls back to `high_temp IS NOT NULL` logic, the assert `obs is None` would FAIL. R-BN tests raise `sqlite3.IntegrityError` on real SQLite — category-impossibility preserved at SQL seam. R-BO fires `DataVersionQuarantinedError` on a real UPDATE path. Zero mirror tests in the fix commit's antibody surface.

3. **P3.1 vocabulary grep with extended forward-facing vocab worked**. The `_requires_explicit_|_must_specify_|_no_default_` extension I proposed post-cycle-1 surfaced R-BI-2, R-BK-2, and sibling antibodies in unrelated modules — all legitimate forward-facing required-kwarg guards. No stale antibody found. Extension vocabulary stays.

4. **Independent reproduction of MAJOR-1 fix**. My sqlite3 CLI reproduction (INSERT without observation_field → IntegrityError) matched the R-BN test result. Fresh repro catches the category-impossibility restoration separately from the test suite, which is the right layering (test could have been written to pass trivially; reproduction against prod schema code proves the invariant).

### Pattern confirmed: "read-seam symmetry with write-seam"

The cycle-1 finding ("P7A fixed write-time metric tag, left read-time observation fetch HIGH-hardcoded") was a generalized version of Fitz's P3.1 lesson from P6. The pattern for future phases: **any "X-aware" claim must be verified on both the write-side and the read-side of the same contract**. In P7A the contract was "metric identity"; the write seam was `metric_identity=spec.identity` in `add_calibration_pair_v2`, and the read seam was `SELECT high_temp FROM observations`. Fixing one without the other leaves a half-closed antibody.

Carry forward to P7B / P8: when evaluating ingest or settlement for LOW-metric activation, grep for every `SELECT.*high_temp FROM observations` AND every `.high_temp` dict/row access. Each hit is a read-seam candidate for the same symmetry check.

### What pattern to carry into P7B / P8

1. **Per-phase read-seam audit as a standing step**. Whenever a phase claims metric-awareness, unit-awareness, or any other symmetry upgrade, critic's first-pass should be: list every read seam (SQL SELECT, dict key access, column reference by name) that touches the symmetry dimension and verify each is dispatched, not hardcoded.

2. **Fixture-INSERT checklist as a MAJOR-class smell**. When a commit adds `DEFAULT '<something>'` to a column that participates in a typed invariant, the commit should either (a) justify why the Python-layer type still holds, OR (b) restore the invariant via a VIOLATING default (e.g. `DEFAULT 'MUST_SPECIFY'`). MAJOR-1 was this exact pattern and may recur.

3. **Commit-message forward-log as critic input**. c496c36's commit message explicitly lists MINOR-1 / MINOR-2 / MAJOR-3 as P7B-deferred with specific paths and tasks. That's the right shape — critic can accept PASS and the next phase's critic inherits a seeded forward-log. Cycle this pattern.

### Meta on my own process (second cycle)

Cycle 2 was faster because the evidence base was already built — cycle 1's wide review cited file:line for every finding, so cycle 2 verification was a targeted `git show + pytest` pass. Total cycle 2 time was probably 15% of cycle 1. This argues for: **thorough wide review at cycle 1 pays multiplicative dividends at cycle 2+** (and for any future P8/P9 critic who inherits the forward-log).

The risk of second-cycle complacency ("commit claims match; trust the author"): sidestepped by running an independent CLI reproduction of the schema fix (MAJOR-1) rather than only running the pytest that the commit added. If the test had been accidentally-green (e.g. wrong expected match substring), reproduction would have caught it. Keep this belt-and-suspenders for cycle-2 PASS verdicts on fix commits.

(~400 words)
