# BATCH C Review — Critic-Harness

Reviewer: critic-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Scope: BATCH C per round2_verdict.md §4.1 #4 (HK HKO type-encoded antibody) — SettlementRoundingPolicy ABC + WMO_HalfUp + HKO_Truncation + settle_market dispatch + relationship tests + fatal_misreads.yaml row update + BATCH-B-CAVEAT operational follow-ups
Pre-batch baseline: 73 passed / 22 skipped / 0 failed
Post-batch baseline: 76 passed / 22 skipped / 0 failed (3 new tests pass first run)

## Verdict

**APPROVE-WITH-CAVEATS** (4 caveats, 1 of HIGH severity for Tier 3 P8 migration; none blocking BATCH D)

The K0_frozen_kernel zone work is well-executed:
- ABC + 2 subclasses correctly implement the type-encoded antibody pattern.
- 3 relationship tests use `pytest.raises(TypeError, match=r"...")` not `match=r"substring"` (defensive).
- Existing surface (`assert_settlement_value`, `round_single`, `round_values`, `default_wu_fahrenheit`, `for_city`) verified intact.
- 3 of 3 BATCH-B operational follow-ups landed (BASELINE_PASSED 73→76 + TEST_FILES expansion + workspace env propagation).
- Self-test antibody (executor's own pre-edit-architecture.sh blocked their fatal_misreads.yaml edit) is empirical proof that BATCH B's hooks work in production.

I articulate WHY this APPROVE-WITH-CAVEATS rather than APPROVE: 4 substantive caveats surface that the executor + operator should know about, with one (CAVEAT-C4) genuinely affecting Tier 3 P8 unification:

1. **CAVEAT-C1 (predicate brittleness)**: `city_name == "Hong Kong"` is case- and whitespace-sensitive. Verified consistent with rest of codebase (`HKO_CITY_NAME = "Hong Kong"` in `daily_obs_append.py:268`; matching pattern in `observation_instants_v2_writer.py:230` and 5+ test files), so NOT a regression — but a future caller passing `"hong kong"` or `"HK"` will silently get the WRONG branch.
2. **CAVEAT-C2 (test-block coverage)**: `fatal_misreads.yaml` row tests block adds 2 of 3 new tests; the 3rd `test_invalid_policy_type_rejected` is NOT registered.
3. **CAVEAT-C3 (env block weakens default state)**: setting `ARCH_PLAN_EVIDENCE` in `.claude/settings.json env:` block means future workspace sessions get a default-passing pre-edit-architecture.sh hook for ANY architecture/** edit. Not a bypass token (the file IS the canonical plan), but the protective gate is now session-default-permissive.
4. **CAVEAT-C4 (HIGH — arithmetic divergence on negative half-values)**: new `WMO_HalfUp.round_to_settlement()` uses `Decimal ROUND_HALF_UP` which differs from existing `np.floor(x + 0.5)` for negative half-values: `-3.5` → old=-3 / new=-4; `-0.5` → old=0 / new=-1. **For positive values (the Zeus universe today): arithmetic equivalent.** For cold-weather markets (negative °F): silent 1-degree divergence. New path is NOT called by any existing code today — so no live impact — but Tier 3 P8 unification MUST resolve this before swapping callers.

None of these blocks BATCH D. CAVEAT-C4 is tracked as a Tier 3 prerequisite.

## Pre-review independent reproduction

```
$ git diff --stat HEAD -- src/contracts/settlement_semantics.py architecture/fatal_misreads.yaml \
                          .claude/hooks/pre-commit-invariant-test.sh .claude/settings.json
 .claude/hooks/pre-commit-invariant-test.sh | 15 +++--
 .claude/settings.json                      |  3 +
 architecture/fatal_misreads.yaml           | 17 +++++-
 src/contracts/settlement_semantics.py      | 90 +++++++++++++++++++++++++++++-
 4 files changed, 118 insertions(+), 7 deletions(-)

$ ls -la tests/test_settlement_semantics.py
2707 bytes / 64 LOC (NEW)

$ .venv/bin/python -m pytest tests/test_architecture_contracts.py tests/test_settlement_semantics.py -q --no-header
76 passed, 22 skipped in 3.81s
```

EXACT MATCH 76/22/0 baseline. ZERO regression. Executor's claim verified.

## ATTACK C1.1 (ABC structural — cannot instantiate directly) [VERDICT: PASS]

```python
$ .venv/bin/python -c "from src.contracts.settlement_semantics import SettlementRoundingPolicy; SettlementRoundingPolicy()"
TypeError: Can't instantiate abstract class SettlementRoundingPolicy without an implementation for abstract methods 'round_to_settlement', 'source_authority'
```

PASS. Both abstract methods (`round_to_settlement`, `source_authority`) flagged on instantiation attempt. `abc.ABC` + `@abstractmethod` decorators work as documented (settlement_semantics.py L199, L210, L214). Cannot construct a half-baked policy.

## ATTACK C1.2 (Type discipline — subclass + ClassVar dispatch) [VERDICT: PASS]

```python
>>> w = WMO_HalfUp(); h = HKO_Truncation()
>>> isinstance(w, SettlementRoundingPolicy)  # → True
>>> isinstance(h, SettlementRoundingPolicy)  # → True
>>> w.name, h.name  # → ('wmo_half_up', 'hko_truncation')
>>> w.source_authority(), h.source_authority()  # → ('WMO', 'HKO')
```

PASS. Both subclasses inherit ABC; `ClassVar[str] name` correctly bound to subclass-specific values; `source_authority()` returns documented strings. Round-trip dispatch verified.

## ATTACK C1.3 (settle_market dispatch — TypeError on mismatch) [VERDICT: PASS, with CAVEAT-C1]

Live independent dispatch test (positive + negative + edge cases):

| Input | Expected | Actual | Result |
|---|---|---|---|
| `settle_market("Hong Kong", Decimal("28.7"), HKO_Truncation())` | 28 (truncation) | 28 | PASS (positive case) |
| `settle_market("New York", Decimal("74.5"), WMO_HalfUp())` | 75 (half-up) | 75 | PASS (positive case) |
| `settle_market("New York", Decimal("74.45"), WMO_HalfUp())` | 74 | 74 | PASS (positive case 74.45 < 74.5) |
| `settle_market("Hong Kong", Decimal("28.7"), WMO_HalfUp())` | TypeError "Hong Kong.*require.*HKO_Truncation" | TypeError correct | PASS |
| `settle_market("New York", Decimal("74.5"), HKO_Truncation())` | TypeError "HKO_Truncation.*Hong Kong only" | TypeError correct | PASS |
| `settle_market("New York", Decimal("74.5"), FakePolicy())` | TypeError "requires a SettlementRoundingPolicy" | TypeError correct | PASS |
| `settle_market("hong kong" lowercase, Decimal, HKO_Truncation())` | TypeError "HKO_Truncation valid for Hong Kong only" | TypeError raised; treated as NON-HK | **CAVEAT-C1** |
| `settle_market("HK" short form, Decimal, HKO_Truncation())` | TypeError | TypeError raised; only exact "Hong Kong" matches | **CAVEAT-C1** |
| `settle_market("Hong Kong " trailing space, Decimal, HKO_Truncation())` | TypeError | TypeError raised; whitespace-sensitive | **CAVEAT-C1** |

**CAVEAT-C1 (predicate brittleness)**: The HK predicate `city_name == "Hong Kong"` is exact-match string equality. Survey of existing codebase confirms this is the project-canonical form: `HKO_CITY_NAME = "Hong Kong"` at `src/data/daily_obs_append.py:268`; `if self.city == "Hong Kong":` at `src/data/observation_instants_v2_writer.py:230`; `city="Hong Kong"` in 5+ test files; `tier_for_city("Hong Kong")` test fixture. **The predicate matches existing convention; not a regression.** But future callers must canonicalize HK city names BEFORE passing to `settle_market`. Worth a defensive normalization (`city_name.strip()` + `city_name == "Hong Kong"`) for safety, but not blocking.

## ATTACK C1.4 (fatal_misreads.yaml HK row update) [VERDICT: PASS, with CAVEAT-C2]

Diff verified:
- Row PRESERVED (id=`hong_kong_hko_explicit_caution_path` still present).
- `correction:` extended with verdict §1.1 #4 + §4.1 #4 cross-ref + explanation that the rounding-policy half is now type-encoded (L122-128).
- `type_encoded_at: src/contracts/settlement_semantics.py:HKO_Truncation` added (L141) — defense-in-depth marker.
- Comment block (L133-140) explains why row is RETAINED not DELETED (broader audit/source-validity caution remains prose-bearing; type guard covers only the rounding-policy sub-case).
- `tests:` block extended with 2 pytest entries (L147-148): `test_hko_policy_required_for_hong_kong` + `test_hko_policy_invalid_for_non_hong_kong`.

**CAVEAT-C2 (test-block coverage)**: 3 new relationship tests exist in `tests/test_settlement_semantics.py` but only 2 are registered in fatal_misreads.yaml `tests:` block. The 3rd test `test_invalid_policy_type_rejected` (which protects against duck-typed policy substitutes — a different attack surface than HK city/policy mismatch) is NOT cited. **Should be added** for completeness — it's the same load-bearing antibody type. Minor; tracked.

## ATTACK C1.5 (BASELINE_PASSED + TEST_FILES update) [VERDICT: PASS]

`pre-commit-invariant-test.sh` diff verified:
- L52-57: comment block explaining the change.
- L52: `TEST_FILES="tests/test_architecture_contracts.py tests/test_settlement_semantics.py"` — 2-file space-separated list.
- L53: `BASELINE_PASSED=76` (was 73; +3 for new relationship tests).
- L67: `RESULT=$("$PYTEST_BIN" -m pytest $TEST_FILES -q --no-header 2>&1 || true)` — variable expansion via word-splitting (intentional; `$TEST_FILES` not `"$TEST_FILES"` so 2 args expand correctly).

Live smoke test with bash -x trace: hook invocation parses `PASSED=76`, `FAILED=0`, `ERRORS=0`, evaluates `[ 76 -lt 76 ]` as false → exit 0 (allow). Hook is functional with new baseline.

## ATTACK C1.6 (settings.json env propagation) [VERDICT: PASS, with CAVEAT-C3]

`.claude/settings.json` diff:
```diff
+  "env": {
+    "ARCH_PLAN_EVIDENCE": "/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-04-27_harness_debate/round2_verdict.md"
+  },
```

Schema: top-level `env` block matches the canonical pattern in global `~/.claude/settings.json` L4-13. Valid Claude Code workspace settings field.

Effect: Claude Code injects `ARCH_PLAN_EVIDENCE` into ALL hook subshells AND all Bash tool invocations FOR THIS WORKSPACE, with the round2_verdict.md path as default value. `pre-edit-architecture.sh` hook reads this env at L41 (`if [ -n "${ARCH_PLAN_EVIDENCE:-}" ] && [ -f "${ARCH_PLAN_EVIDENCE}" ]; then exit 0`).

Live smoke test (with env set): `ARCH_PLAN_EVIDENCE=<path> echo '{"tool_input":{"file_path":"architecture/topology.yaml"}}' | hook` → exit 0 (allowed). Live smoke test (env unset): same input → exit 2 (blocked). Hook enforces the gate; settings.json provides the default value.

**CAVEAT-C3 (env block weakens default state)**: The hook becomes session-default-permissive for the harness-debate scope. ANY architecture/** edit in this workspace (not just BATCH C+D) silently passes the gate as long as round2_verdict.md exists on disk. **This is a known trade-off of the OP-FOLLOWUP-2 design**: BATCH C+D don't self-block (good); but a future unrelated session in this workspace also won't be gated (bad if the work is unrelated to the harness-debate plan).

**Tier 2 governance follow-up**: when the harness-debate work concludes, EITHER:
(a) Remove the `env` block from `.claude/settings.json` so the hook reverts to strict gate, OR
(b) Update `ARCH_PLAN_EVIDENCE` to point to the next active packet plan, so the env block stays current with the active work.

Per round-2 verdict §4.2 governance norm: harness mechanisms should not silently outlive their explicit authorization scope.

## ATTACK C1.7 (test pattern robustness) [VERDICT: PASS]

3 new relationship tests audit:

```python
# Test 1: HK + WMO → TypeError
with pytest.raises(TypeError, match=r"Hong Kong.*require.*HKO_Truncation"):
```
- `match=r"..."` uses regex (not substring), with `.*` between key tokens. PASS — robust against minor message reformatting.

```python
# Test 2: NY + HKO → TypeError
with pytest.raises(TypeError, match=r"HKO_Truncation.*Hong Kong only"):
```
- Same regex pattern. PASS.

```python
# Test 3: FakePolicy → TypeError
with pytest.raises(TypeError, match=r"requires a SettlementRoundingPolicy"):
```
- PASS.

**Positive cases NOT explicitly tested in test file** (HK + HKO valid; NY + WMO valid). Independently verified by me via direct dispatch: HK+HKO+28.7 → 28; NY+WMO+74.5 → 75. The relationship tests focus on the negative case (Fitz "make category impossible") which is the load-bearing assertion. **Acceptable** — positive cases are implicit (would fail the existing test if rounding broke). Worth adding 1-2 explicit positive tests in Tier 2 for full coverage.

## ATTACK C1.8 (K0_frozen_kernel append-only — existing surface intact) [VERDICT: PASS, with CAVEAT-C4]

Diff verified surgical-append:
- L1-3: 3 new imports (`abc`, `decimal`, `ClassVar`) — additive, existing imports unchanged.
- L185: blank line separating existing dataclass from new ABC code.
- L187-271: new code block (provenance comment + ABC + 2 subclasses + dispatch function) — pure addition.
- ZERO modifications to existing `SettlementSemantics` dataclass, `round_values`, `round_single`, `assert_settlement_value`, `default_wu_fahrenheit`, `default_wu_celsius`, `for_city`, or any helper.

Live verification of existing surface:
```
sem = SettlementSemantics.default_wu_fahrenheit('LGA')
sem.round_values([74.45, 74.55, 74.5])  # → [74.0, 75.0, 75.0]  CORRECT
sem.round_single(74.5)                   # → 75.0  CORRECT
sem.assert_settlement_value(74.5)        # → 75.0  CORRECT
```

K0 zone unperturbed. All existing callers (`src/calibration/store.py:98,171`; `src/execution/harvester.py:706`; `src/engine/replay.py:1215`+5; `src/engine/evaluator.py:1003,1219,1336`; `src/signal/ensemble_signal.py:217,316`) continue to invoke the OLD path unchanged.

**CAVEAT-C4 (HIGH severity for Tier 3 P8 migration) — arithmetic divergence on negative half-values**:

Old path: `np.floor(x + 0.5)` (settlement_semantics.py:24, used throughout).
New path: `Decimal(str(x)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)` (settlement_semantics.py:224).

For positive values: ARITHMETIC EQUIVALENT (verified 7/7 positive test cases).

For NEGATIVE half-values: DIVERGENT.

| value | old `np.floor(x+0.5)` | new `Decimal ROUND_HALF_UP` | diff |
|---|---|---|---|
| `74.45` | 74 | 74 | match |
| `74.5` | 75 | 75 | match |
| `74.55` | 75 | 75 | match |
| `-3.5` | **-3** | **-4** | DIVERGENT 1° |
| `-3.45` | -3 | -3 | match |
| `-3.55` | -4 | -4 | match |
| `-0.5` | **0** | **-1** | DIVERGENT 1° |
| `0.49999` | 0 | 0 | match |

The semantics differ at half-values for negative inputs:
- `np.floor(x + 0.5)` is "half-towards-positive-infinity" (always rounds UP toward +∞).
- `Decimal ROUND_HALF_UP` is "half-away-from-zero" (rounds AWAY from zero, so -3.5 → -4).

**Today's impact**: ZERO. The new `settle_market` is NOT called by any existing code path (verified via grep — only the 3 new tests invoke it). No live arithmetic divergence.

**Tier 3 P8 migration impact (judge §4.3 P8)**: When the type-encoded path replaces the legacy string-dispatch path, callers passing negative temperatures will see a 1° silent divergence. **MUST be reconciled BEFORE Tier 3 P8 swap.** Options:
(a) Change `WMO_HalfUp.round_to_settlement` to use `np.floor(x + 0.5)` semantics (match legacy; "WMO half-up half-towards-positive-infinity" — actually IS the WMO definition per the existing `round_wmo_half_up_values` docstring at settlement_semantics.py:L17 "floor(x + 0.5)").
(b) Verify all settlement values in Zeus are POSITIVE (eg, all weather markets are in cities that don't go below 0°F or 0°C respectively) — provide proof in Tier 3 P8 boot evidence.

**Recommend (a)** — the existing `round_wmo_half_up_values` docstring explicitly defines WMO as `floor(x + 0.5)`, and the new path's "ROUND_HALF_UP" is a NumPy/Python semantic that doesn't match. Tier 3 P8 should fix `WMO_HalfUp.round_to_settlement` to use `np.floor(float(raw_temp_c) + 0.5)` or equivalent.

This is the strongest finding of BATCH C. Not blocking BATCH D (the new path isn't wired in production). MUST be on the Tier 3 P8 boot checklist.

## Cross-batch coherence (longlast critic discipline)

- **BATCH A SKILL.md → BATCH C tests**: SKILL §"Closeout" L29 says "Dispatch verifier subagent... If verifier flags coverage gap, address." If verifier had been dispatched, it would have flagged CAVEAT-C2 (3rd test not in fatal_misreads). Verifier was NOT dispatched. **Process gap, not BATCH C scope.**
- **BATCH B drift-checker → BATCH C edits**: drift-checker did not flag any new RED introduced by BATCH C. Independently re-ran `scripts/r3_drift_check.py --architecture-yaml`: still 4035 GREEN / 34 RED (same pre-existing entries). BATCH C did not introduce architecture/*.yaml citation drift.
- **BATCH B hook self-test (executor's empirical proof)**: executor reported "pre-edit-architecture.sh BLOCKED their fatal_misreads.yaml edit until env was wired — hook-determinism empirically confirmed." This is the strongest-possible cross-batch coherence signal. The system's own protective gate fired against the system's own work. Per Fitz Constraint #3 "Immune System > Security Guard": the hook acted as antibody, not just alarm.
- **BATCH B → BATCH C operational follow-ups**: 2/2 honored (BASELINE_PASSED bumped 73→76; ARCH_PLAN_EVIDENCE wired via settings.json env).
- **planning_lock receipt for BATCH C** (independently verified): `topology check ok` with plan-evidence round2_verdict.md against fatal_misreads.yaml + invariants.yaml + 2 hooks + settings.json + 2 src files + 1 test file. Architecture/** edit was correctly authorized.
- **No new failures across the full architecture contract suite**: 76/22/0; baseline preserved.

## Anti-rubber-stamp self-check

I have written APPROVE-WITH-CAVEATS, not APPROVE. The 4 CAVEATs are real: C1 is benign (verified consistent with codebase) but worth tracking; C2 is minor coverage gap; C3 is a known trade-off in the OP-FOLLOWUP design with an actionable Tier 2 follow-up; C4 is the strongest finding — a 1-degree silent arithmetic divergence on negative half-values that MUST be reconciled before Tier 3 P8 swap.

I have NOT written "narrow scope self-validating" or "pattern proven" without test citation. I have engaged the strongest claim (BATCH C delivers a working type-encoded antibody per Fitz "make the category impossible") at face value before pivoting to the caveats. I have independently exercised every advertised behavior (3 positive + 3 negative + 3 case-sensitivity + ABC abstract instantiation + ClassVar binding + existing surface preservation + arithmetic equivalence trace).

The C4 finding came from going beyond the dispatch-listed attack vectors (C1.8 was "verify append-only didn't perturb" but I additionally verified arithmetic equivalence between the OLD and NEW WMO paths and found the divergence at negative half-values). This is what longlast cross-batch critic discipline produces.

## CAVEATs tracked forward (non-blocking)

| ID | Severity | Concern | Action | Owner |
|---|---|---|---|---|
| CAVEAT-C1 | LOW | `city_name == "Hong Kong"` is case/whitespace-sensitive; future callers must canonicalize | Add defensive `city_name.strip()` normalization OR document the canonical form in settle_market docstring; project-consistent behavior so non-blocking | Tier 2 |
| CAVEAT-C2 | LOW | `fatal_misreads.yaml` tests block has 2 of 3 new tests; missing `test_invalid_policy_type_rejected` | Add the 3rd test to the YAML row tests block | Tier 1 sidecar (could be done now or BATCH D) |
| CAVEAT-C3 | MEDIUM | `settings.json env.ARCH_PLAN_EVIDENCE` makes the hook session-default-permissive | At harness-debate work conclusion: remove env block OR rotate to next active plan path | Tier 2 governance |
| CAVEAT-C4 | **HIGH** (for Tier 3 P8) | `WMO_HalfUp.round_to_settlement` uses `Decimal ROUND_HALF_UP` which differs from existing `np.floor(x + 0.5)` for negative half-values (1° divergence at -3.5, -0.5, etc) | Reconcile semantics BEFORE Tier 3 P8 swap; recommend changing new path to `np.floor(float(raw_temp_c) + 0.5)` to match legacy WMO definition | Tier 3 P8 prerequisite |

## Required follow-up before BATCH D

1. **Set `ARCH_PLAN_EVIDENCE` is already pre-set in settings.json env block** — BATCH D's invariants.yaml edits will pass the pre-edit-architecture.sh gate without manual override. (CAVEAT-C3 trade-off in action.)

2. **BATCH D should NOT bump BASELINE_PASSED**: BATCH D deletes 2 INVs from invariants.yaml; this affects YAML structure but NOT pytest count. The 76/22/0 baseline should remain at 76/22/0 post-BATCH-D unless a test that specifically asserts INV-16/17 schema presence breaks.

3. **NC-12/NC-13 cascade audit (per executor §D.2)**: NC-12 is referenced by INV-14 (line 108: `negative_constraints: [NC-11, NC-12]`). If executor does NC-12 cascade-delete, this orphans INV-14. Read-only audit ONLY in BATCH D; flag-only as executor pledged.

4. **CRITICAL FINDING from my BOOT §2 BATCH D attack vectors** that I want to re-flag for BATCH D operator dispatch: my boot identified that `tests/test_phase6_causality_status.py` (3 tests) and `tests/test_dt1_commit_ordering.py` (6 tests) cite INV-16 and INV-17 by name in docstrings. The PRUNE_CANDIDATE markers + verdict §6.1 #1 + round-2 §4.2 #9 may be WORKING ON INCOMPLETE GREP — the verdict says "pure prose-as-law on HEAD" but tests using the INV name in docstrings exist. **Whether these tests count as "enforcement" depends on the project's strict definition.** This is a verdict-source defect potential, not necessarily a BATCH D execution defect. **Recommend operator decide**: are docstring-cited but YAML-block-not-cited tests considered "enforcement"? If YES, INV-16/17 are NOT pure prose-as-law and the DELETE recommendation is wrong. If NO (only YAML `enforced_by.tests:` block counts), DELETE proceeds as planned. Will surface this in BATCH D review formally.

## Final verdict

**APPROVE-WITH-CAVEATS** — proceed with BATCH D. 4 CAVEATs tracked forward; only C4 has high-severity status, and only for Tier 3 P8 unification (not BATCH D).

End BATCH C review.
