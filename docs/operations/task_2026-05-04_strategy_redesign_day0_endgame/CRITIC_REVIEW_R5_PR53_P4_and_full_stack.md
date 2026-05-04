# critic-opus R5 review — PR #53 P4 D-A two-clock unification + full-stack scrutiny

HEAD: e81e22f8454c42fd456bd45a0fec0f4df65abc2f
Reviewer: critic-opus
Date: 2026-05-04

## Subject

Adversarial review of PR #53 (`strategy-day0-redesign-p2-2026-05-04`) at full stack — 8 commits, 2929+ additions across P2+P3+P4 — with attack focus on the new P4 commit `e81e22f8` (D-A two-clock unification: 2-site flag-gated phase-axis dispatch, 5 files / 788 insertions / 17 new tests).

Reference inputs:
- Zeus_May4_review_bugs.md (external bug review, especially Finding F)
- PLAN_v3.md §6.P4 + §8 T2 + §8 T5 + §0.1 amendments 11-15
- CRITIC_REVIEW_R3 / R4 prior verdicts (residual caveats carry forward)

## VERDICT: APPROVED-WITH-CAVEATS

Net: 0 HIGH (no merge blocker), 4 MEDIUM (operational guardrails before flag flip), 4 LOW (translation-loss / hygiene). P4 itself is a clean flag-OFF byte-equal migration with reasonable test coverage at the helper level. The verdict is APPROVED-WITH-CAVEATS rather than APPROVED because three structural concerns from the external bug review (Finding F) are unresolved at code level even though PLAN-side framing acknowledges them: F1 fallback enters live dispatch authority once flag flips with no `phase_source` provenance tag; market_phase=None silently accepts dispatch-axis-A as "no info but OK to trade"; T1 cycle-snapshot stability is degraded at the new monitor-loop site (per-position `_now_utc` not frozen). None of these block flag-OFF merge but each must be closed before `ZEUS_MARKET_PHASE_DISPATCH=1` becomes a live decision.

## BOTTOM LINE (1-PARAGRAPH)

P4 ships byte-equal pre-existing behavior under flag OFF; 17 new helper-level tests pass at HEAD; full canonical-venv regression sweep returns 4710 passed / 134 failed / 113 skipped — but the 134 failures are PRE-EXISTING at the immediate parent (`2bd16006`), not P4-introduced (verified by stash-and-checkout of pre-P4 src+tests, same 134/4693 result). Symmetry is broken at one undocumented site: `evaluator.py:1427` (`is_day0_mode = candidate.discovery_mode == "day0_capture"`) drives EntryMethod selection and is NOT migrated nor referenced in PLAN_v3 §6.P3/P4. Under flag ON with a divergent (mode=opening_hunt, market_phase=settlement_day) candidate, that site routes to ENS_MEMBER_COUNTING while strategy dispatch routes to settlement_capture — a new phase/method incoherence not captured by any test. This is the load-bearing finding the operator must see.

---

## ATTACK 1 [VERDICT: PASS] — Provenance citation rot

**Spot-checked 5 citations from PLAN_v3 §6.P4 + commit body**:

| Citation | Claim | HEAD truth |
|---|---|---|
| `cycle_runtime.py:1501` (commit body says line 1501 = DAY0_WINDOW transition) | new helper inserted | matches: line 1501 captures `_now_utc`, helper called at 1518-1525 |
| `cycle_runtime.py:2003` (commit body site 2 candidate filter) | DAY0_CAPTURE candidate filter migrated | actual migration at lines 2023-2044 (commit body line drift ~20 lines — the literal reference is rotted but the migration is present) |
| `dispatch.py:_is_settlement_day_phase` tri-state | Optional[bool] | matches at dispatch.py:162-220 |
| `architecture/source_rationale.yaml` registers `src/engine/dispatch.py` | yes | matches at line 1014-1027 |
| `architecture/test_topology.yaml` registers 5 test files | yes | matches at lines 110-114 |

**LOW finding A1-L1**: commit body says "src/engine/cycle_runtime.py:2003" but the actual migrated block starts at line 2023. Five-line buffer between the textual line and the literal commit. Not a bug; line-drift through prior edits in the same file. Memory L20 (grep-gate before contract lock) applies — a future agent grepping for `2003` lands inside `_missing_execution_snapshot_fields`, not the migration point.

## ATTACK 2 [VERDICT: PASS-WITH-LOW] — Premise (flag-OFF byte-equal)

Walked both sites at flag-OFF:

**Site 1** (cycle_runtime.py:1500-1528): pre-P4 baseline (verified via `git show e81e22f8^:src/engine/cycle_runtime.py | sed -n '1495,1515p'`) called `deps._utcnow()` ONCE inside `lead_hours_to_settlement_close()` and used `hours_to_settlement <= 6.0` as the gate. Post-P4 captures `_now_utc = deps._utcnow()` ONCE up-front, passes it to both `lead_hours_to_settlement_close` and `should_enter_day0_window`. With flag OFF, `should_enter_day0_window` only reads `legacy_hours_to_settlement <= legacy_threshold_hours` — `_now_utc` doesn't affect the OFF path. Byte-equal preserved.

**Site 2** (cycle_runtime.py:2023-2044): pre-P4 was the single inline list-comprehension; post-P4 wraps the comprehension in `if market_phase_dispatch_enabled():` else-branch. With flag OFF, `else` branch IS the original list-comprehension, character-for-character. Byte-equal preserved.

3 dedicated T6 byte-equal tests (`test_t6_filter_flag_off_returns_true_so_legacy_filter_is_authority`, `test_t6_day0_transition_flag_off_uses_legacy_threshold`, `test_t6_day0_transition_flag_off_ignores_phase_axis`) actively pin both sites' OFF behavior.

**LOW finding A2-L2**: site 1's pre-P4 baseline did NOT explicitly bind `_now_utc`; the new code does. With the underlying `deps._utcnow()` returning equivalent timestamps for the same call, this is semantically identical, but it IS a structural change. A future agent re-reading site 1 sees a new local variable `_now_utc` and the call form differs slightly. No behavior risk; pure cosmetic translation-loss.

## ATTACK 3 [VERDICT: FAIL — MEDIUM] — Symmetry / single-locus claim broken

`src/engine/dispatch.py:37-39` claims:

> "This module is the single locus for the dispatch decision so the six call sites (3 in evaluator.py + 1 obs-fetch gate + 2 D-A sites in cycle_runtime.py) all read the same flag and the same logic."

**This is false today.** A 7th dispatch site exists at `src/engine/evaluator.py:1427`:

```python
is_day0_mode = candidate.discovery_mode == "day0_capture"
selected_method = (
    EntryMethod.DAY0_OBSERVATION.value
    if is_day0_mode
    else EntryMethod.ENS_MEMBER_COUNTING.value
)
...
if is_day0_mode and candidate.observation is None:
    return [EdgeDecision(False, ..., rejection_reasons=["Day0 observation unavailable"], ...)]
if is_day0_mode:
    source_rejection_reason = _day0_observation_source_rejection_reason(...)
```

This site:
1. Drives EntryMethod (DAY0_OBSERVATION vs ENS_MEMBER_COUNTING) — a non-trivial branch in the pipeline
2. Reads `candidate.discovery_mode == "day0_capture"` directly (not `is_settlement_day_dispatch`)
3. Is NOT documented in PLAN_v3 §6.P3/P4 site-list (which lists only 5+1 = 6 sites for P3 + 2 sites for P4)
4. Is NOT covered by any `ZEUS_MARKET_PHASE_DISPATCH` test fixture

**The concrete failure mode under flag ON**: a candidate whose `discovery_mode=opening_hunt` but `market_phase=SETTLEMENT_DAY` (legitimate per P3 docs and `tests/test_market_phase_dispatch.py:213-229`) will:
- Route to `settlement_capture` strategy_key (P3 site 2/3)
- Route to ENS_MEMBER_COUNTING entry method (the unmigrated site 1427) — i.e., NOT use Day0 observation as the entry method
- Skip the `Day0 observation unavailable` rejection (because `is_day0_mode=False`)

This is a phase/method incoherence: the strategy dispatcher says "this is a Day0 settlement-capture entry" but the entry method says "use ENS_MEMBER_COUNTING and don't require an observation". No test exercises this incoherence today.

**MEDIUM finding A3-M1**: 7th dispatch site at evaluator.py:1427 not migrated, not documented, not tested under flag-ON. PLAN_v3 §6.P3 site count "5+1" is wrong. Prior to flag flip, this must either:
- be migrated to read `is_settlement_day_dispatch(candidate)` like the other evaluator sites,
- be explicitly documented as a "must be on cycle-axis" exception,
- or have a flag-ON test that asserts the divergent (mode=opening_hunt, market_phase=settlement_day) candidate still routes correctly through EntryMethod selection.

## ATTACK 4 [VERDICT: APPROVED-WITH-CAVEATS] — Test adequacy / T5 coverage

The 17 new tests pass. T5 samples 6 UTC hours (00, 06, 09, 12, 15, 21) × 51 cities — 306 (city, UTC-hour) tuples, asserting equality with the pure-phase computation `_city_in_settlement_day`. This is reasonable for a relationship invariant.

**Concerns**:

1. **Test-vs-implementation tautology** (A4-L3 LOW): `_city_in_settlement_day` in the test fixture is a re-implementation of `market_phase_for_decision` flow. If the implementation has a bug AND the re-implementation has the same bug, T5 is silent. Stronger: pin against the cities matrix in INTERNAL Q5 directly (which was hand-verified externally). The test asserts only that the filter and the pure-phase function agree; it does not pin that either is correct.

2. **DST edge cases not covered** (A4-L4 LOW): T5's target_date is 2026-05-08 — past spring-forward for all DST cities. `settlement_day_entry_utc` claims correctness on DST-transition target dates ("23h or 25h window in UTC wall-clock terms"), but no test exercises a DST-transition target_date (e.g., 2026-03-08 or 2026-11-01 for US cities). The Copilot comment 3179345263 reference in the docstring suggests this WAS reviewed; verify by adding 1 spring-forward target_date assertion.

3. **6 UTC hours is sparse for diurnal sweep** (A4-L5 LOW): 0/6/9/12/15/21 leaves UTC 03 (LA boundary) and UTC 23 (Wellington boundary) unsampled. The minimal 24×51 = 1224 grid takes microseconds to evaluate; sampling-only is premature optimization. Recommend exhaustive sweep.

## ATTACK 5 [VERDICT: APPROVED-WITH-CAVEATS] — Fail-soft tri-state correctness

`_is_settlement_day_phase` correctly returns `Optional[bool]`. Caller propagation:

**`filter_market_to_settlement_day` (site 2)**:
- `result is True` → True
- `result is False` → False (correct; phase says no)
- `result is None` (parse error) → False (intentional fail-soft toward exclusion)

This is documented and consistent with site 4's gate semantics. Untested case: a market dict with a malformed `market_end_at` like `"NOT-A-DATE"` would land in `result=None`. Without flag-ON the legacy filter would have admitted the market. With flag-ON it is excluded — silent loss of candidate. Mostly fine but:

**LOW finding A5-L6**: when `result is None` happens at site 2 under flag ON, no log/warning is emitted. The candidate disappears from the cycle silently. Recommend a `deps.logger.warning` to record the parse-error fail-soft so operators can observe Gamma payload corruption when the flag is flipped.

**`should_enter_day0_window` (site 1)**:
- `result is True` → True
- `result is False` → False (respects phase, does NOT fall back to legacy 6h)
- `result is None` (parse error) → falls back to legacy `legacy_hours_to_settlement <= legacy_threshold_hours`

This is correct per the docstring intent. Test `test_day0_transition_flag_on_falls_back_to_legacy_on_parse_error` at line 294 pins it.

**MEDIUM finding A5-M2**: the `result is False` branch (line 322) — comment on line 318-321 correctly identifies that re-falling-back to legacy here would re-introduce D-A. But there is a SUBTLE risk for `RESOLVED` phase: `_is_settlement_day_phase` calls `market_phase_for_decision` with `uma_resolved=False` always (line 216 hardcoded). A market that has actually been UMA-resolved could still pass the legacy 6h gate AND have phase=POST_TRADING → returns False → no DAY0_WINDOW transition. Acceptable today, but documents a `uma_resolved` plumbing gap that PLAN_v3 §6.P3 stage 3 calls out (TODO-style "uma_resolved parameter but no on-chain wiring"). External review Finding F (page 282) flags this exact concern.

## ATTACK 6 [VERDICT: FAIL — MEDIUM] — External-review constraint (Finding F) on F1 fallback

External review Zeus_May4_review_bugs.md §6.5 requires:

> "market_end_at present + parseable + source=Gamma/market snapshot → phase_source=verified_gamma. market_end_at missing but family invariant holds → phase_source=fallback_f1, observability allowed, live dispatch/Kelly requires explicit feature gate or degraded multiplier."

PR #53 P4 with flag ON does NOT implement this distinction:

1. **Site 2** (`filter_market_to_settlement_day`): when `market.get("market_end_at")` is missing, `market_phase_from_market_dict` (`src/strategy/market_phase.py:229-232`) silently substitutes `_f1_fallback_end_utc(target_local_date)` (12:00 UTC) and returns a phase. The caller cannot distinguish verified-Gamma vs F1-fallback origin.

2. **Site 1** (`should_enter_day0_window`): ALWAYS calls `_is_settlement_day_phase(market=None, ...)` — the monitor loop has no Gamma payload, so it ALWAYS uses F1 fallback (12:00 UTC). Per Finding F this is fallback-only and should not enter live dispatch authority.

The PR description and PLAN_v3 §0.1 amendment 11 acknowledge that R3 ATTACK 8 fixed the `_parse_event` to surface `market_end_at` onto the market dict — but this only helps site 2, not site 1, and even at site 2 there's no `phase_source` tag distinguishing the two paths.

**MEDIUM finding A6-M3**: F1 fallback (12:00 UTC) becomes silent live dispatch authority once `ZEUS_MARKET_PHASE_DISPATCH=1`. Per external review Finding F this is a Stage-0 gate concern. Recommended remediation:

- Add `phase_source: Literal["verified_gamma", "fallback_f1", "unknown"]` field to whatever object surfaces phase to dispatch.
- At site 2, when `_f1_fallback_end_utc` was used, log/warn and (optionally) reject under a stricter sub-flag.
- At site 1, since site 1 ALWAYS uses fallback, document that flag-ON site 1 is "fallback-authority only" and ensure no Kelly multiplier reads market_phase from this path until a Gamma-verified source plumbs through.

**Important**: F1 has been verified across 13 of 51 cities (CRITIC_REVIEW_R2.md confirms 7+6=13). 38 cities are extrapolation. This is acceptable as observability scaffolding (P3+P4 default OFF), but flag-ON without per-cycle Gamma timestamp verification flips F1 from "validated invariant" to "production assumption that may fail silently on a new city or schedule change". F4 (`UMA settlement is variable`) reinforces that the 12:00 UTC anchor is a trading-cutoff fact, NOT a settlement-truth fact.

## ATTACK 7 [VERDICT: FAIL — MEDIUM] — OracleEvidenceStatus contamination parallel

External review Finding A demands evidence-status separation: OK ≠ MISSING ≠ INSUFFICIENT_SAMPLE. PR #53's `market_phase=None` is the parallel structural risk for the phase axis.

`market_phase=None` reaches dispatch in three concrete paths today:
1. `cycle_runtime.py:2125` exception path (Gamma parse error logs warning, leaves None)
2. Test fixtures and any direct `MarketCandidate(...)` construction
3. Cohort: pre-flag-flip rows in `probability_trace_fact.market_phase` are NULL

Under flag ON:
- `is_settlement_day_dispatch` (dispatch.py:84-89) falls back to legacy `discovery_mode == "day0_capture"` when `market_phase is None`. This is documented fail-soft.
- BUT — same as Finding A's "missing = OK" pattern — None silently equates to "trade as if no phase info, fall back to mode dispatch". There is no `MarketPhaseEvidence` object distinguishing:
  - VERIFIED (Gamma payload parsed cleanly)
  - FALLBACK (F1 used because Gamma omitted endDate)
  - PARSE_FAILED (Gamma payload corrupt)
  - PRE_FLAG_FLIP_NULL (legacy row, no phase-axis populated)
  - GENUINE_PRE_TRADING (market not yet trading)

**MEDIUM finding A7-M4**: Phase-axis evidence states are collapsed into a single Optional[MarketPhase], replicating Finding A's failure-mode for the phase axis. Pre-flag-flip and parse-failure rows are indistinguishable in `probability_trace_fact.market_phase` (both NULL). Cohort attribution (PLAN §6.P9) cannot honestly slice "phase-axis-aware decisions" from "phase-axis-degraded decisions".

Remediation (deferrable to P5/P9, but document now):
- Define `MarketPhaseEvidence` per external review §6.4 (phase + phase_source + decision_time_utc + city_timezone)
- Persist `phase_source` separately from `market_phase` in the trace fact
- Reject phase-aware Kelly (when P5 lands) on PARSE_FAILED rows; allow on VERIFIED; degrade-multiplier on FALLBACK

## ATTACK 8 [VERDICT: PASS-WITH-LOW] — Cohort split / NULL handling

`probability_trace_fact.market_phase` schema (db.py:694, 1440) correctly uses `TEXT NULL`. Pre-flag-flip rows have NULL; flag-ON rows have populated string values.

`grep -rn "market_phase IS NULL\|GROUP BY market_phase"` in `src/` returns no consumer SQL today. Cohort attribution (PLAN §6.P9) is a future packet. This is acceptable as "scaffold the column now, write the cohort SQL when consumer lands".

**LOW finding A8-L7**: a future cohort report that does `GROUP BY market_phase` will get NULL as a separate group (per SQL semantics), conflating "pre-flag-flip" (cohort: legacy dispatch) with "parse-failed" (cohort: phase computation broke). Recommend P9 commit time include either a `phase_source` column OR an explicit `... WHERE market_phase IS NOT NULL OR cycle_started_after_flag_flip` discipline.

## ATTACK 9 [VERDICT: APPROVED-WITH-CAVEATS] — Schema / index tightness

`idx_probability_trace_market_phase` (db.py:1450) is a single-column index on `market_phase`. PLAN §6.P9 expects `(strategy_key, market_phase)` to be the cohort unit. A composite index `(strategy_key, market_phase)` would be tighter for cohort SQL than a single-column index.

**LOW finding A9-L8**: index is non-optimal for the documented future cohort SQL pattern. Single-column suffices for filter `WHERE market_phase = X`; composite needed for `WHERE strategy_key = X AND market_phase = Y`. Not blocking for P4; trivial to add at P9 time. Note for the operator who lands P9.

## ATTACK 10 [VERDICT: PASS-WITH-DISCLOSURE] — Pre-existing-failure baseline

The commit body claims `1 pre-existing failure in test_market_scanner_provenance.py confirmed identical to baseline via stash-and-run`. Independent verification:

1. **At HEAD e81e22f8** (full canonical-venv sweep, ignoring 3 unrelated module-not-found / collection-error files):
   ```
   /Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/ \
     --ignore=tests/test_market_scanner_provenance.py \
     --ignore=tests/test_phase4_5_extractor.py \
     --ignore=tests/test_phase4_6_cities_drift.py
   → 134 failed, 4710 passed, 113 skipped, 16 deselected, 2 xfailed, 2 errors, 9 subtests passed
   ```

2. **At parent 2bd16006** (same exclusions plus test_market_phase_d_a_unification.py since the dispatch helpers don't exist yet):
   ```
   → 134 failed, 4693 passed, 113 skipped, 16 deselected, 2 xfailed, 2 errors, 9 subtests passed
   ```

P4 introduces 0 new failures. The 134 baseline failures are PRE-EXISTING at the immediate parent and inherited by P4. The commit body's `1 pre-existing failure` count is an undersample — only the sanity subset run.

**MEDIUM finding A10-M5**: The 134 failures include `test_runtime_guards.py` (live-entry / executable-snapshot tests), `test_pre_live_integration.py`, `test_topology_doctor.py`, and `test_structural_linter.py`. While none are P4-introduced, these are LIVE-relevant test surfaces that should not be silently inherited as "OK" — operator must triage these BEFORE flipping `ZEUS_MARKET_PHASE_DISPATCH=1` in production. Some may be relevant to P3/P4 by virtue of touching dispatch / runtime guards; cursory inspection is warranted.

Honest disclosure (from R5): the team-lead L22+L28 gate (full canonical regression run) was NOT done in commit e81e22f8 — commit body explicitly disclosed this. R5 closed that gap, found 0 new failures introduced but 134 inherited.

---

## Required fixes summary

| # | Severity | Finding | Remediation |
|---|----------|---------|-------------|
| A3-M1 | MEDIUM | 7th unmigrated dispatch site at `evaluator.py:1427` — `is_day0_mode = candidate.discovery_mode == "day0_capture"` drives EntryMethod selection but is not in PLAN site-list and not flag-aware | Either migrate to `is_settlement_day_dispatch(candidate)`, document as "must be cycle-axis", OR add explicit flag-ON test for divergent (mode=opening_hunt, market_phase=settlement_day) candidate's EntryMethod path |
| A5-M2 | MEDIUM | `_is_settlement_day_phase` always passes `uma_resolved=False`; a UMA-resolved market could still pass legacy 6h with phase=POST_TRADING and inhibit DAY0_WINDOW transition | Plumb `uma_resolved` from on-chain evidence, OR document the gap inline at line 216 with the Finding-F-style "no on-chain truth source" caveat. Out-of-scope for P4 itself; gate flag flip on this. |
| A6-M3 | MEDIUM | F1 fallback (12:00 UTC) becomes silent live dispatch authority once flag ON; no `phase_source` distinction; site 1 always uses fallback | Add `phase_source: Literal["verified_gamma", "fallback_f1", "unknown"]` to evidence object; reject Kelly (P5) on fallback-only paths; log site-2 fallback usage |
| A7-M4 | MEDIUM | `market_phase=None` collapses MISSING + PARSE_FAILED + PRE_FLAG_FLIP into one indistinguishable state — Finding A parallel for phase axis | Define `MarketPhaseEvidence` per external review §6.4; persist phase_source separately; do NOT use phase-axis-None as authority for live dispatch under any future Kelly migration |
| A10-M5 | MEDIUM | 134 pre-existing test failures inherited from base, including live-relevant runtime_guards/pre_live tests | Operator must triage these before `ZEUS_MARKET_PHASE_DISPATCH=1` flips in production; team-lead gate per L22+L28 |
| A1-L1 | LOW | Commit body cites `cycle_runtime.py:2003` but actual migration is at line 2023 (5-line drift) | Update commit body OR future PR description |
| A2-L2 | LOW | Site 1 introduces new local `_now_utc` binding — semantically identical but structurally different | None required; cosmetic |
| A4-L3 | LOW | T5 fixture `_city_in_settlement_day` is a re-implementation of `market_phase_for_decision`; tautology risk | Strengthen by pinning against INTERNAL Q5 hand-verified matrix |
| A4-L4 | LOW | T5 doesn't exercise DST-transition target dates | Add 1 spring-forward + 1 fall-back target_date case |
| A4-L5 | LOW | T5 samples 6 UTC hours of 24; LA-boundary (UTC 03) and Wellington-boundary (UTC 23) unsampled | Run exhaustive 24×51 grid (microsecond runtime) |
| A5-L6 | LOW | Site 2 silent candidate drop on parse failure under flag ON | Add `deps.logger.warning` at parse-failure path |
| A8-L7 | LOW | Future cohort SQL will conflate NULL = pre-flag + NULL = parse-failed | At P9 commit time, add phase_source OR explicit NULL discipline |
| A9-L8 | LOW | `idx_probability_trace_market_phase` single-column; cohort SQL needs `(strategy_key, market_phase)` composite | Add composite index at P9 commit time |

## Carry-forward residual caveats from R3/R4

| Source | Severity | Status at HEAD |
|---|---|---|
| R4 A4-M1 (attribution_drift not P3-aware) | MEDIUM | UNRESOLVED — `src/state/attribution_drift.py:148` still has `if sig.discovery_mode == "day0_capture": return "settlement_capture"`. Closed only at P3 fix bundle 2bd16006 ATTACK in PLAN comment field, not in code. Verify with team-lead. |
| R4 A7-M2 (obs-fetch gate integration test missing) | MEDIUM | UNRESOLVED at integration-test level — helper has unit tests; cycle_runtime.py:2150-2153 calls helper but no `tests/test_runtime_guards.py` integration test confirms end-to-end. P4 commit doesn't address this. |
| R4 A5-L1 (settlement_day_dispatch_for_mode dead helper) | LOW | RESOLVED — cycle_runner.py:325 + 440 now call the helper for grep-symmetry |
| R4 A2-LOW (test_market_phase.py docstring `_require_utc`) | LOW | UNVERIFIED at HEAD (out of P4 scope) |
| R4 A8-L3 (PLAN file/header drift) | LOW | RESOLVED — file is now PLAN_v3.md per commit 2bd16006 |
| R4 A10-L4 (PR doesn't disclose P4+P5 deferral) | LOW | OBSOLETE — P4 lands in this PR |

The two MEDIUM carry-forwards (A4-M1 attribution_drift + A7-M2 integration test) remain blockers for flag-ON live behavior, NOT for flag-OFF merge.

## Honest disclosure

- Full canonical-venv regression (~7min) executed for both HEAD and parent. 134 failures inherited, 0 P4-introduced. This closes the L22+L28 gap the commit body honestly disclosed.
- I did NOT verify whether `attribution_drift._infer_strategy_from_signature` is actively called in any production path. R4 A4-M1 carry-forward severity hinges on this; team-lead must confirm.
- I did NOT exhaustively walk the 134 pre-existing failures. Some are likely live-relevant (runtime_guards, pre_live_integration); operator must triage before flag flip.
- The 7th dispatch site (A3-M1, evaluator.py:1427) was found by grepping `mode\s*==\s*DiscoveryMode.DAY0_CAPTURE\|discovery_mode\s*==\s*"day0_capture"` in `src/`. The PLAN-stated count `5+1+2 = 8 sites` plus `2 cycle-axis fallback sites in cycle_runner` totals 10; reality has 11 (the additional one being evaluator.py:1427). This is real translation-loss between PLAN and code.
- The east-west asymmetry economics are NOT bugs in P4 itself but ARE a downstream operational concern that PLAN_v3 §3 acknowledges: under flag ON, LA's SETTLEMENT_DAY window is 5h (07:00-12:00 UTC); Auckland's is 24h (12:00 UTC of day before to 12:00 UTC of target). The commit body's "all 24 hours before midnight of the local market" framing is operator-correct geographically but semantically misleading for west-of-UTC cities. Phase-aware Kelly (P5) is the real corrective.
- 17/17 new tests in `test_market_phase_d_a_unification.py` pass at HEAD via canonical venv (0.06s). 73/73 across all 5 phase-axis test files pass.
- I did NOT find any rubber-stamp false-positives in the 17 new tests — they would actually fail on a real regression of the helpers. T5 has the test-vs-implementation tautology concern (A4-L3) but is not rubber-stamp.
- Fitz Constraint #4 (data provenance): `phase_source` provenance gap (A6-M3) is the textbook example. Code is currently correct as observability scaffolding; once flag flips, code without provenance becomes a Finding-A-equivalent for the phase axis.
- Memory L20 grep-gate spot-checked 5/19 PLAN line citations; 5-line drift on commit body's `:2003` reference (A1-L1). 4/5 cleanly resolved at HEAD.

End of R5 review.
