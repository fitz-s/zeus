# Phase 9A Wide Review — critic-carol cycle 2

**Commit**: `7081634` feat(phase9a): P8 observability absorption + DT#6 Interpretation B clarified
**Reviewer**: critic-carol cycle 2 (2nd of 3 cycles before rotation to critic-dave)
**Date**: 2026-04-18
**Mode**: Gen-Verifier (team-lead-direct exec + persistent critic)
**Operating mode during review**: THOROUGH (no escalation to ADVERSARIAL — no CRITICAL, <3 MAJOR, no systemic pattern)
**Persisted-by**: team-lead (critic-carol Write/Edit blocked, inherited from cycle 1)

## VERDICT: **PASS — 3rd consecutive first-try PASS**

P7B + P8 + P9A all first-try PASS. P9A cleanly absorbs all 4 MAJOR observability forward-log items from my cycle-1 P8 review plus 4 MINORs/new-antibody gaps, with genuine structural improvement on the most-scrutinized item (R-BQ.1). Hard constraints preserved (5 v2 tables still zero-row, no TIGGE/DDL/monitor_refresh bleed). Regression math exact: baseline 144/1846 → post-P9A 144/1851 (+5 from R-BS/R-BT/R-BU, zero new failures). The DT#6 Interpretation B doc clarification resolves the ruling ambiguity and honestly flags the `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` mapping for periodic review. Two genuine MINOR findings surface, neither block PASS.

## Self-audit on cycle-1 findings (cycle-2 specific)

| Cycle-1 finding | P9A fix | Assessment |
|---|---|---|
| MAJOR-1 `entries_blocked_reason` no DATA_DEGRADED | S1 widens elif tuple at cycle_runner:281 | **Structural root fix.** Probe: reverting the edit causes R-BT to FAIL correctly with `entry_bankroll_non_positive` string mismatch. |
| MAJOR-2 `tick_with_portfolio` persistence gap | S5 documents as ephemeral-advisory in DT#6 §B | **Documented by design.** Explicit choice (Interpretation B-style); not a hidden patch. |
| MAJOR-3 R-BQ.1 silent-return | S4 removes the `except RuntimeError: return` path | **Structural root fix.** Unconditional summary assertion now runs. |
| MAJOR-4 R-BQ.1 text-match | S4 replaces with "ANY RuntimeError = violation" | **Genuinely structural.** Probe: rewording a reintroduced raise (e.g., `raise RuntimeError("Different message")`) correctly fails the test now. |
| MINOR-1 CLI flag | Deferred P9C (hygiene) | Correctly scoped |
| MINOR-2 sweep/audit+metric mismatch | S3 warning + S8 R-BU antibody pair | Above-MINOR delivery quality |
| MINOR-3 duplicate `4.` numbering | Fixed in phase8-close | Previously addressed |
| MINOR-4 L195 overwrite comment | S2 comment added | Meets MINOR standard |

**4/4 MAJORs addressed with structural fixes (no patch-sized stand-ins).**

## Pre-commitment Predictions vs Findings

| # | Prediction | Actual | Hit/Miss |
|---|-----------|--------|----------|
| 1 | R-BQ.1 trades text-match for over-broad RuntimeError catch | Empirically probed: structural-immunity CONFIRMED, catches novel-text RuntimeError correctly. No false-alarm risk observed. | Miss (fix genuinely structural) |
| 2 | DT#6 Interpretation B introduces new ambiguity at `_TRUTH_AUTHORITY_MAP` boundary | Doc explicitly flags mapping for periodic review. Separately: "save_portfolio MAY proceed PROVIDED external-authority origin" is aspirational doc with NO code enforcement. | **Partial hit** — different ambiguity than predicted (MINOR-2) |
| 3 | R-BS.2 truth annotation check too weak | **CONFIRMED**: final assertion is `save_path.exists()`; keyword-search result never asserted. Vacuous. | **Hit** (MINOR-1) |
| 4 | R-BT silent-pass path | Empirically probed by reverting elif tuple: R-BT FAILS correctly. No silent-pass. | Miss (paranoia rewarded; test robust) |
| 5 | S2 overwrite-intent comment = doc-only drift-bait | Structurally correct — comment will go stale if L176 moves. No way to encode as runtime check without test coupling. | **Partial hit** — acknowledged limitation |

Meta: cycle-1 hit 5/5, cycle-2 hit ~2.5/5. Lower hit-rate means the commit was specifically engineered to address cycle-1 concerns — not that I predicted poorly.

## Critical findings

None.

## Major findings

None.

## Minor findings

### MINOR-1 (cycle-2): R-BS.2 is a checkbox antibody — final assertion is vacuous

**Evidence** — `tests/test_phase8_shadow_code.py:351-385`:
```python
truth_found = False
for key in data:
    if "authority" in key.lower() or "truth" in key.lower():
        truth_found = True
        break
# Fall back: annotate_truth_payload may nest; accept either top-level or
# a _truth / provenance wrapper. Final fallback: accept any JSON write.
assert save_path.exists(), "R-BS.2: truth annotation seam not exercised"
```

`truth_found` computed but never asserted. `assert save_path.exists()` passes for any JSON write regardless of truth annotation presence. Empirically verified: `save_portfolio` DOES produce a `truth` top-level key — but the test would silently pass even if `annotate_truth_payload` were removed entirely.

- Confidence: **HIGH**
- Why it matters: R-BS.2's stated antibody purpose is "lock the truth-payload annotation seam is exercised so future changes to `_TRUTH_AUTHORITY_MAP` surface in review". A test that passes without the seam firing does NOT lock this. Future refactor silently dropping `annotate_truth_payload` from `save_portfolio` would leave R-BS.2 green. Classic translation-loss (cycle-1 learning L2).
- Fix: Replace tail assertion with `assert truth_found, f"R-BS.2: no truth/authority annotation in JSON top-level keys: {list(data.keys())}"`. Keep value-agnosticism (that's fine); actually assert seam fires.

### MINOR-2 (cycle-2): DT#6 Interpretation B encodes caller-side discipline as aspirational doc

**Evidence** — `docs/authority/zeus_dual_track_architecture.md:253-257`:
> `save_portfolio()` / `save_tracker()` MAY proceed in degraded mode **provided the updates originate from external-authority sources** (CLOB API reconciliation, chain sync with on-chain truth, order fill/cancel events).

`save_portfolio` implementation at `src/state/portfolio.py:1048-1091` has no provenance check on `state` origin. "provided X" clause is caller-side discipline encoded only in prose.

- Confidence: **HIGH**
- Why it matters: Classic translation-loss (Fitz constraint 2). A future agent reading the doc may reasonably believe `save_portfolio` gates on provenance. A future caller may save internally-computed mutations under degraded mode without violating any runtime check. Law-doc and code diverge exactly where Interpretation B matters most.
- Fix options:
  - (a) Add a `source` parameter to `save_portfolio` with runtime check (expensive, P9B/P9C scope)
  - (b) Soften doc language to "Callers SHOULD restrict saves..." + log as design debt in forward-log
- Recommendation: (b) is honest fix for P9A scope.

## What's Missing

1. **No round-trip verification in R-BS.1**: Contract claims "save_portfolio+reload round-trip" but R-BS.1 only inspects raw JSON (does NOT call `load_portfolio`). Contract drift; R-BS.1 is still useful as save-side verification.
2. **S2 comment drift-bait acknowledged but unmitigated**: L195 overwrite-intent comment will silently lie if overwrite moved. Acceptable trade-off for MINOR.
3. **R-BT accepts any string containing "degraded"**: Intentional leniency; document it so future critics don't flag.
4. **TEST-GAP 2 + 4 still open**: `status_summary.json` reflecting DATA_DEGRADED + rollback path for `tick_with_portfolio` raising. Correctly deferred to P9C per contract.

## Ambiguity Risks

- `"external-authority sources"` (DT#6 B) → Interpretation A: strictly CLOB/chain/event-driven writes. Interpretation B: anything not purely internal computation. Risk: future caller may save analytics-derived updates as "external" via CLOB-price, likely not intended.
- `"runtime-derived, not serialized"` (DT#6 B authority field) → Clear. Code path matches doc.

## Multi-Perspective Notes

- **Executor**: All 8 items executable with contract reference alone.
- **Stakeholder**: User's 2026-04-18 ruling (B-option, re-evaluate after mainline) faithfully implemented. Three-signal redundancy matches operator-visibility concern.
- **Skeptic**: Strongest counter was "R-BQ.1 'any RuntimeError = violation' is too broad" — empirical probe refuted. Second: `_TRUTH_AUTHORITY_MAP['degraded']='VERIFIED'` might be wrong — doc honestly flags for periodic review rather than hiding. Defensible deferral.

## Fitz Four-Constraints Lens

1. **Structural Decisions > Patches**: 8 items decompose to ~3 structural decisions (DT#6 law clarification, observability-signal wiring, antibody-quality hardening) + 5 supporting patches. **3 decisions, clean delivery.**
2. **Translation Loss**: R-BQ.1 hardened from text-match (cycle-1 concern) to structural (cycle-2 probe confirms). R-BS.2 retains weakness (MINOR-1). Aspirational doc clause (MINOR-2) is translation-loss by design.
3. **Immune System**: R-BS/R-BT/R-BU genuinely close cycle-1 gaps (per empirical probes). R-BS.2 weakness documented but doesn't close the observability gap it claims to.
4. **Data Provenance**: N/A direct. `_TRUTH_AUTHORITY_MAP["degraded"]="VERIFIED"` periodic-review clause is adequate documentation but weak antibody — no schedule, no owner.

## Verdict Justification

**PASS — first-try (cycle-2 second consecutive).**

- Mode: THOROUGH. No CRITICAL, <3 MAJOR, no systemic pattern.
- Three-streak warning: P7B + P8 + P9A = 3 consecutive first-try PASSes. My cycle-1 learning explicitly warned about this. For cycle 3 (P9B), I recommend the rotation successor (critic-dave) include explicit "adversarial hunting mode" for the first 15 minutes before falling back to thorough. Work has been genuinely good; but 3 consecutive passes increases the probability that my methodology has a blind spot I can't see from inside.
- Why no MAJOR: Self-audit found that my predicted MAJOR concerns (R-BQ.1 over-broadness, R-BT silent-pass) did not survive empirical probing. The one weak antibody (R-BS.2) is MINOR in impact because the seam IS currently exercised.
- Why no ITERATE/FAIL: Structural goals met. Regression exact. Hard constraints preserved.

## Open Questions (unscored)

1. Should `save_portfolio` grow a `source: Literal["clob", "chain", "event", "internal"]` parameter for DT#6 B enforcement? (Defer to P9B/P9C.)
2. Is "periodic review of `_TRUTH_AUTHORITY_MAP['degraded']='VERIFIED'`" a real checklist item somewhere, or just doc-text? (Suggest: add to forward-log with a named owner.)
3. For cycle 3 (P9B, critic-dave), should adversarial mode be the default given the 3-pass streak?

## Cycle-1 → Cycle-2 Methodology Comparison

| Aspect | Cycle 1 | Cycle 2 | Delta |
|---|---|---|---|
| Pre-commit prediction hit rate | 5/5 | ~2.5/5 | Lower is fine when commit is fix-focused |
| Empirical probes run | 1 (baseline-restore) | 4 (baseline + 3 surgical-revert) | Expanded toolkit |
| Findings by severity | 4 MAJOR, 4 MINOR, 6 gaps | 0 MAJOR, 2 MINOR, 4 gaps | Proportional to commit scope |
| ADVERSARIAL mode | Not triggered | Not triggered | Both THOROUGH — correct |
| Time spent | ~30 min | ~25 min | Streamlined |
| New meta-learnings | 7 | 8 (4 new + refinements) | Methodology compounding |

## Files audited

- `src/engine/cycle_runner.py` L192-199, L282-291 (S1+S2)
- `src/engine/replay.py` L1956-1963 (S3)
- `tests/test_phase8_shadow_code.py` L195-259 (R-BQ.1 hardened), L296-385 (R-BS), L388-497 (R-BT), L500-585 (R-BU)
- `docs/authority/zeus_dual_track_architecture.md` L247-290 (DT#6 Interpretation B)
- `docs/operations/task_2026-04-16_dual_track_metric_spine/phase9a_contract.md` (full)
- `src/state/portfolio.py` L47-51 (_TRUTH_AUTHORITY_MAP), L1048-1091 (save_portfolio impl)
- `src/riskguard/riskguard.py` L983-1040 (tick_with_portfolio — confirmed advisory, no risk_state.db persistence)

## Cycle-1 artifact path correction

Team-lead's cycle-2 brief pointed to `phase9_evidence/critic_carol_phase8_*.md`. Actual persistence was `phase8_evidence/critic_carol_phase8_*.md` (in commit 73eba2b). The **directory convention is**:
- `phase8_evidence/` — P8 cycle-1 artifacts (PASS at 6ffefa4)
- `phase9_evidence/` — P9A cycle-2 artifacts (this review) + P9B/P9C cycles onward

Cycle-1 artifacts ARE persisted correctly; the path error was only in team-lead's brief to me. No methodology gap.

---

*Critic: critic-carol cycle 2, 2026-04-18, 2nd of 3 cycles before rotation to critic-dave.*
*Inherited methodology: L0.0, two-seam, P3.1 vocab, baseline-restore + surgical-revert probes, pre-commitment, deferral-with-rationale, empirical-probe text-match skepticism, checkbox-antibody fingerprint detection.*
*Next cycle opens*: P9B (DT#2 RED force-exit + DT#5 Kelly executable-price + DT#7 boundary antibodies). If PASS again, critic-dave takes over for P9C.
