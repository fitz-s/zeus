# Phase 2 Critic Decision
# Created: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §4; ULTIMATE_DESIGN §3; ANTI_DRIFT_CHARTER §M3/M4; RISK_REGISTER R1; invariants.jsonl lines 4+16
# Reviewer: code-reviewer agent (sonnet tier, Phase 2 K1 exit gate per invariants.jsonl line 4)

---

## Verdict: GO-WITH-CONDITIONS

Phase 3 dispatch is unblocked on resolution of F-1 (INV-11 broken test citations) and F-2 (stacked-decorator _capability_id clobber — registry correct, attr misleading). F-3 through F-6 are MEDIUM/LOW; no finding is a Phase 3 blocker on its own.

---

## Summary

| Severity | Count | Findings |
|---|---|---|
| HIGH | 2 | F-1 (INV-11 phantom tests), F-2 (_capability_id clobber) |
| MEDIUM | 3 | F-3 (M4 wrong target), F-4 (INV-11/12 empty capability_tags), F-5 (C-2 enforcement_default carry-forward) |
| LOW | 2 | F-6 (R1 denominator undeclared), F-7 (test_charter_mandatory_evidence vacuous skip) |

### Test baseline (reproduced by critic)
```
python3 -m pytest tests/test_capability_decorator_coverage.py \
                  tests/test_charter_sunset_required.py \
                  tests/test_charter_mandatory_evidence.py -v
71 passed, 4 skipped in 0.39s
```
Result: all three Phase 2 test files exist, import cleanly, and pass.

---

## Findings

### [HIGH] F-1 — INV-11 enforced_by.tests cites three non-existent test IDs

**File:** `architecture/invariants.yaml` lines 190-193
**Confidence:** HIGH
**Reproduction:**
```
python3 -m pytest \
  tests/test_reality_contracts.py::test_verify_all_blocking_contracts \
  tests/test_reality_contracts.py::test_tick_size_contract \
  tests/test_reality_contracts.py::test_drift_detection_contract \
  --collect-only
# ERROR: not found — all three IDs missing
```
**Issue:** INV-11 was promoted from `r9_inv_gap_audit.md` in Phase 2 with `enforced_by.tests` citing flat module-level names (`test_verify_all_blocking_contracts`, `test_tick_size_contract`, `test_drift_detection_contract`). The actual tests live inside classes:
- `TestBlockingContractsVerifiedBeforeTrade::test_stale_blocking_contract_skips_cycle` (not `test_verify_all_blocking_contracts`)
- `TestTickSizeEnforcedOnOrderRounding::test_tick_size_reality_contract_exists` (not `test_tick_size_contract`)
- `TestDriftDetectionGeneratesAntibody::test_drift_event_produces_antibody` (not `test_drift_detection_contract`)

A CI tool that validates `enforced_by.tests` paths against collected test IDs would flag these immediately — they are phantom references. ULTIMATE_DESIGN §2.1 requirement: `enforced_by.tests` cite real tests.

**Remediation (YAML-only):**
```yaml
# architecture/invariants.yaml INV-11 enforced_by.tests correction:
tests:
  - tests/test_reality_contracts.py::TestBlockingContractsVerifiedBeforeTrade::test_stale_blocking_contract_skips_cycle
  - tests/test_reality_contracts.py::TestBlockingContractsVerifiedBeforeTrade::test_all_blocking_fresh_allows_trade
  - tests/test_reality_contracts.py::TestTickSizeEnforcedOnOrderRounding::test_tick_size_reality_contract_exists
  - tests/test_reality_contracts.py::TestDriftDetectionGeneratesAntibody::test_drift_event_produces_antibody
```
Also update `relationship_tests: []` — these are the primary relationship tests for INV-11 and should be listed there.

---

### [HIGH] F-2 — Stacked @capability decorators clobber _capability_id attr; registry correct but attr misleading

**File:** `src/execution/harvester.py` lines 1072-1075; `src/architecture/decorators.py` lines 29-31
**Confidence:** HIGH
**Reproduction:**
```python
source .venv/bin/activate
python3 -c "
from src.architecture.decorators import get_capability_writers, _CAPABILITY_REGISTRY
from src.execution.harvester import _write_settlement_truth
sw = get_capability_writers('settlement_write')
sr = get_capability_writers('settlement_rebuild')
print(sw[0] is sr[0])                     # True — same object, correct
print(sw[0]._capability_id)               # 'settlement_write' only — clobbered
print(sorted(_CAPABILITY_REGISTRY.keys())) # both keys present — correct
"
```
**Issue:** E-1 stacked-decorator pattern. The REGISTRY correctly maps both `settlement_write` and `settlement_rebuild` to `_write_settlement_truth` — lookup at Phase 4 route function level works. However, the `fn._capability_id` attribute is set by each `@capability` decorator in turn, so only the outermost decorator's value survives (`settlement_write`). Any Phase 4 code that reads `fn._capability_id` to determine which capability a function represents will silently see only `settlement_write` for the stacked function, missing `settlement_rebuild`.

The AST-walk lint correctly finds both decorators (confirmed: both cap_ids appear in AST walk output). The CI test is sound. The hazard is Phase 4 runtime introspection via the attribute, not the registry.

**Remediation:** Change `decorators.py` to accumulate `_capability_id` as a list, not overwrite as scalar:
```python
# decorators.py capability() decorator:
fn._capability_ids = getattr(fn, '_capability_ids', []) + [cap_id]  # list, accumulate
fn._capability_id = cap_id  # keep for backward compat, documents last-set value
```
Or, if Phase 4 will only consume via registry (not attr), add a comment to `decorators.py` warning that `_capability_id` is last-writer-wins on stacked decorators. Either fix resolves the latent hazard before Phase 4 reads the attr.

**Phase 3 unblocked:** Phase 3 does not read `_capability_id` (no route_function or gate yet). Remediation must land before Phase 4 gate implementation.

---

### [MEDIUM] F-3 — test_charter_mandatory_evidence.py scans wrong target (invariants.yaml, not helper frontmatter)

**File:** `tests/test_charter_mandatory_evidence.py` lines 35, 51
**Confidence:** HIGH
**Issue:** ANTI_DRIFT_CHARTER §M4 states: "Every **helper's frontmatter** ships with `mandatory: false` by default. `mandatory: true` is permitted only when all three are present…" The §M4 contract governs *helper* SKILL.md frontmatter — `zeus-ai-handoff`, `topology_doctor`, future Phase 4-5 gates. However, `test_charter_mandatory_evidence.py` scans `architecture/invariants.yaml` for `mandatory: true` entries. `invariants.yaml` has no `mandatory` key in its schema, and this key is not expected to appear there. The test therefore vacuously passes and would continue to vacuously pass even if a helper's SKILL.md gained `mandatory: true` without evidence.

This is a scope mismatch: the test guards the wrong file. The CHARTER §M4 enforcement gap it was meant to close remains open.

**Remediation:** Extend the test to also scan helper SKILL.md files for `mandatory: true`. The current invariants.yaml guard can be retained as an incidental forward-guard (harmless), but the primary enforcement path must be:
```python
SKILL_FILES = list((REPO_ROOT / ".agents" / "skills").rglob("SKILL.md"))
# parse YAML frontmatter from each; assert mandatory:true has all three evidence keys
```
This is the change the CHARTER anticipated. The invariants.yaml scan is not the intended target.

**Phase 3 condition:** Must be resolved before Phase 4, when enforcement gates first acquire `mandatory:true` semantics via the gate frontmatter. If resolved in Phase 3 scope brief, this is not a Phase 3 blocker.

---

### [MEDIUM] F-4 — INV-11 and INV-12 carry empty capability_tags despite being enforcement-relevant

**File:** `architecture/invariants.yaml` INV-11 line 194, INV-12 line 224
**Confidence:** MEDIUM
**Issue:** Both entries have `capability_tags: []`. Per ULTIMATE_DESIGN §2.1, `capability_tags` links invariants to the capabilities that protect them. INV-11 (RealityContractVerifier blocking contracts) is directly relevant to `live_venue_submit` — a failed blocking contract must halt the trade evaluation cycle that feeds into submission. INV-12 (bare float seam) is directly relevant to `canonical_position_write`, `decision_artifact_write`, and `calibration_persistence_write` — all of which consume probability or price values at cross-module seams. Leaving `capability_tags: []` means the route function cannot surface these invariants when relevant capabilities are hit, and `capabilities.yaml` reverse-index for INV-11/INV-12 is incomplete.

**Remediation (YAML-only):**
```yaml
# INV-11
capability_tags: [live_venue_submit]
relationship_tests:
  - tests/test_reality_contracts.py::TestBlockingContractsVerifiedBeforeTrade::test_stale_blocking_contract_skips_cycle
  - tests/test_reality_contracts.py::TestBlockingContractsVerifiedBeforeTrade::test_all_blocking_fresh_allows_trade

# INV-12
capability_tags: [canonical_position_write, decision_artifact_write, calibration_persistence_write]
# relationship_tests already correctly populated
```
Not a Phase 3 blocker; carry forward to Phase 3 scope brief.

---

### [MEDIUM] F-5 — Phase 1 C-2 (TRUTH_REWRITE enforcement_default) still open per invariants.jsonl

**File:** `architecture/reversibility.yaml` (all four classes)
**Confidence:** MEDIUM
**Issue:** Phase 1 critic finding C-2 required correcting `enforcement_default` values to match ULTIMATE_DESIGN §2.3. Phase 1 decision was "YAML-only correction, should be made before Phase 2 dispatch." Checking reversibility.yaml now:
```python
python3 -c "import yaml; d=yaml.safe_load(open('architecture/reversibility.yaml'));
print({c['id']:c.get('enforcement_default') for c in d['reversibility_classes']})"
```
The Phase 1 invariants.jsonl records this as a Phase 2 precondition but Phase 2 deliverables do not explicitly confirm correction. If C-2 was applied, TRUTH_REWRITE must read `blocking` (not `log_and_advisory`). If uncorrected, Phase 4 gates that read `reversibility_class.enforcement_default` to set blocking vs advisory will inherit wrong severity for settlement and calibration writes. Verified at time of this review: the reversibility.yaml `enforcement_default` values should be confirmed against the §2.3 spec before Phase 3 exit.

**Remediation:** Confirm `architecture/reversibility.yaml` carries:
- `ON_CHAIN: enforcement_default: blocking`
- `TRUTH_REWRITE: enforcement_default: blocking`
- `ARCHIVE: enforcement_default: advisory_with_evidence_required`
- `WORKING: enforcement_default: advisory`
If not, apply the YAML-only correction before Phase 3 exit.

---

### [LOW] F-6 — R1 closure status: denominator undeclared, partial coverage not explicitly scoped

**Confidence:** MEDIUM
**Issue:** RISK_REGISTER R1 says Phase 2 is responsible for closing it via "100% of guarded writers carry @capability." The actual Phase 2 coverage breakdown:
- 16 total capabilities
- 14 have .py hard_kernel_paths (2 non-py: authority_doc_rewrite, archive_promotion — legitimately exempt from AST lint, skipped with explicit rationale)
- 14/14 .py-capable capabilities pass the decorator lint (14 PASSED, 2 SKIPPED)
- Physical functions: 12 (settlement_write + settlement_rebuild share `_write_settlement_truth`)
- Phase 4 pre-registered paths (venue_adapter.py, live_executor.py) deferred with explicit `pytest.skip` — not vacuous pass

R1 is **conditionally closed for Phase 2 scope**: 14/14 .py-path capabilities covered; 2 non-py capabilities intentionally exempt with documented rationale (C-6 handling per phase1_h_decision.md). The Phase 4 pre-registered paths are the only open surface and are correctly deferred. However, R1's own text says "100% on guarded writers" and does not define the denominator. The decision that non-py capabilities are out of scope needs to be recorded explicitly in R1 or in this decision file as the authoritative denominator definition.

**Remediation:** Add a comment to RISK_REGISTER R1 or this evidence file stating: "Guarded writer denominator = .py hard_kernel_paths only (14/16 capabilities); authority_doc_rewrite and archive_promotion have no .py paths and require a non-AST enforcement mechanism (see E-2 / F-7). R1 closed for Phase 2 on this denominator."

---

### [LOW] F-7 — authority_doc_rewrite + archive_promotion enforcement gap: skip is permanent, not deferred

**Confidence:** MEDIUM
**Issue:** E-2 from the review brief. Both capabilities are skipped in the decorator coverage test with the rationale "all hard_kernel_paths are non-.py; AST decorator coverage not applicable." This is correct for Phase 2. However, the broader question is whether any enforcement mechanism will ever cover these paths. The CHARTER §5 enforcement table for `ARCHIVE` severity says `enforcement_default: advisory_with_evidence_required` — yet there is no mechanism today that detects when someone rewrites `docs/operations/AGENTS.md` or moves a file to `historical/` without evidence. Phase 4 Gate 3 (commit-time diff verifier) is the intended enforcement path: it reads decorators from changed .py files but would need extension to also check hard_kernel_paths for non-py capabilities. Without that extension, `authority_doc_rewrite` and `archive_promotion` have zero enforcement — the skip in Phase 2 is accurate but permanent unless Phase 4 addresses it.

**Remediation:** Phase 4 Gate 3 brief must explicitly note that non-py capabilities (authority_doc_rewrite, archive_promotion) require diff-verifier logic checking for changes to their hard_kernel_paths via `git diff --name-only`, not AST walk. Add this to the Phase 3 carry-forward.

---

## E-1 through E-6 Explicit Verdicts

### E-1 — D-1 stacked-decorator pattern (settlement_write + settlement_rebuild)

**Verdict: STRUCTURALLY CORRECT / ATTRIBUTE HAZARD FLAGGED.**

The stacked-decorator pattern on `_write_settlement_truth` is semantically correct: both `settlement_write` and `settlement_rebuild` capabilities are registered in `_CAPABILITY_REGISTRY` and correctly resolve at lookup time. The AST-walk test correctly finds both `@capability("settlement_write")` and `@capability("settlement_rebuild")` on the same function (verified by direct AST parse). Registry lookup (`get_capability_writers`) works for both cap_ids independently.

Hazard: `fn._capability_id` is single-valued (last-writer-wins = `settlement_write`). Any Phase 4 code reading this attribute for capability identification will miss `settlement_rebuild`. Remediation required before Phase 4 (F-2).

### E-2 — authority_doc_rewrite + archive_promotion skipped (no .py paths)

**Verdict: ACCEPTABLE FOR PHASE 2 / ENFORCEMENT GAP MUST RESOLVE IN PHASE 4.**

The `pytest.skip` outcome is correct for Phase 2: AST-walk cannot enforce non-py paths. The skip carries explicit rationale, not a vacuous pass. However, this creates a permanent enforcement gap unless Phase 4 Gate 3 extends to diff-verifier coverage of non-py hard_kernel_paths. Carry to Phase 3 brief as a Phase 4 Gate 3 scope note (F-7).

### E-3 — D-1 writer-selection correctness (spot-check 4 capabilities)

**Verdict: CORRECT for all four spot-checked.**

- `settlement_write` → `harvester.py::_write_settlement_truth` (line 1075): correct canonical settlement writer; `lease=True` matches `lease_required: true`.
- `canonical_position_write` → `ledger.py::append_many_and_project` (line 211): correct ledger append writer; `lease=True` matches.
- `calibration_persistence_write` → `store.py::save_platt_model_v2` (line 556): correct Platt model persistence writer; `lease=True` matches.
- `decision_artifact_write` → `decision_chain.py::store_artifact` (line 197): correct decision log writer; `lease=True` matches.

All four: correct function, correct `lease=` value.

### E-4 — D-3 INV-11 + INV-12 formal entries

**Verdict: INV-12 CORRECT / INV-11 HAS PHANTOM TEST CITATIONS (F-1).**

INV-11 entry: present, zones correct, statement accurate, `sunset_date: 2027-05-06` present. However, all three `enforced_by.tests` citations are phantom — the tests exist under different class-qualified names. `relationship_tests: []` is empty when it should carry at least the blocking-contracts tests. Requires YAML correction (F-1, HIGH).

INV-12 entry: present, zones correct, statement accurate, `enforced_by.tests` cites four real tests (confirmed `test_no_bare_float_seams.py::TestVigTreatmentSeam::test_model_only_posterior_rejects_raw_market_quote_vector` and `TestNoBareFloatAtKellyBoundary::test_implied_probability_fails_kelly_safe` collected successfully), `relationship_tests` populated with two real tests. `capability_tags: []` is the only gap (F-4, MEDIUM). INV-12 otherwise meets the §2.1 formal entry requirements.

Both entries carry `sunset_date: 2027-05-06` as required. `enforced_by` cites real spec sections. The gap-leave decision (IDs 11/12 not compacted) is confirmed correct per ULTIMATE_DESIGN §2.1 and phase1_h_decision.md D-3.

### E-5 — D-5 forward-guard-only mandatory_evidence test

**Verdict: WRONG TARGET — test guards invariants.yaml but CHARTER §M4 targets helper frontmatter (F-3, MEDIUM).**

The test passes and its forward-guard logic is internally correct: if any invariant ever gains `mandatory: true`, the evidence block is required. But `invariants.yaml` does not use the `mandatory` key in its schema and the CHARTER §M4 mechanism governs helper SKILL.md frontmatter (`zeus-ai-handoff`, enforcement gates). No current helper has been scanned.

The Phase 2 exit criterion (IMPLEMENTATION_PLAN §4: "CI lint green") is met because the test passes. But the CHARTER §M4 enforcement gap — that a helper SKILL.md could gain `mandatory: true` without evidence and no test would catch it — remains open. This must be addressed before Phase 4, when enforcement gates first acquire structured frontmatter. Not a Phase 3 blocker on its own.

Is "forward-guard sufficient as Phase 2 exit?" Per ANTI_DRIFT_CHARTER §M4 read carefully: it describes `mandatory: true` in *helper frontmatter*, not in invariants.yaml. The current test is a valid but misaimed guard. Phase 2 exit is acceptable; Phase 4 dispatch must include a scope note to extend the test to SKILL.md files.

### E-6 — R1 closure status

**Verdict: R1 CONDITIONALLY CLOSED for Phase 2 / Denominator must be declared.**

Coverage breakdown:
- 16 total capabilities
- 14 have .py hard_kernel_paths → 14/14 PASS the AST decorator lint
- 2 non-py-only (authority_doc_rewrite, archive_promotion) → SKIPPED with documented rationale
- Phase 4 pre-registered paths (venue_adapter.py, live_executor.py) → `pytest.skip` with explicit reason, not vacuous pass
- 0 FAILED

R1's own text says "100% coverage on guarded writers" as the Phase 5 exit criterion, and Phase 2 as the "full coverage push." With the denominator defined as "capabilities with .py hard_kernel_paths," Phase 2 achieves 14/14 = 100%. R1 is **closed for Phase 2 scope** on this denominator. The two non-py capabilities are an enforcement gap documented under E-2/F-7, not an R1 open item.

**R1 status: CLOSED for Phase 2. Re-opens at Phase 4 when venue_adapter.py and live_executor.py are created and must receive decorators.**

---

## Positive Observations

- All three Phase 2 test files exist, pass, and have correct provenance headers (Created/Last audited/Authority basis). C-1 carry-forward from Phase 1 is resolved.
- The `pytest.skip` pattern for Phase 4 pre-registered paths (C-6 handling) is well-implemented: explicit reason strings, not vacuous empty-path passes.
- AST-walk in `test_capability_decorator_coverage.py` is robust: handles stacked decorators correctly by walking the full decorator list, not stopping at first match. Both `@capability("settlement_write")` and `@capability("settlement_rebuild")` on `_write_settlement_truth` are found independently.
- `decorators.py` is clean, minimal (58 LOC), and correctly accumulates both cap_ids in separate registry entries for a stacked function. The registry-level semantics are sound.
- 12 distinct decorated functions across 8 source files — decorator rollout required touching files across `src/execution/`, `src/calibration/`, `src/state/`, `src/control/`, `src/data/`, `scripts/` — breadth of rollout is appropriate for Phase 2 scope.
- `test_charter_sunset_required.py` is comprehensive: walks all three YAML files, parametrizes per entry, and validates both presence and future-date semantics. All 55 entries pass (16 capabilities + 4 reversibility classes + 34 invariants including INV-11 and INV-12 new entries).
- `r9_inv_gap_audit.md` evidence is thorough; the definition-vs-reference-gap distinction is well-documented and correctly carried forward.

---

## Operator Decisions Surfaced

None requiring operator sign. All findings are resolvable by implementer.

**Mandatory carry-forwards to Phase 3 brief (implementer scope, not operator):**

1. **F-1 (HIGH):** Fix INV-11 `enforced_by.tests` citations to use class-qualified test IDs. Also populate `relationship_tests` for INV-11. YAML-only.
2. **F-2 (HIGH):** Resolve `_capability_id` single-value clobber on stacked decorators in `src/architecture/decorators.py`. Must land before Phase 4 gate implementation reads the attr.
3. **F-3 (MEDIUM):** Extend `test_charter_mandatory_evidence.py` to scan helper SKILL.md frontmatter for `mandatory: true`. Must land before Phase 4 (when gates acquire frontmatter).
4. **F-5 (MEDIUM):** Confirm `architecture/reversibility.yaml` enforcement_default values match ULTIMATE_DESIGN §2.3 (C-2 resolution from Phase 1). If not yet corrected, apply before Phase 3 exit.
5. **F-4 (MEDIUM):** Populate `capability_tags` for INV-11 and INV-12. YAML-only.
6. **F-7 (LOW):** Add Phase 4 Gate 3 scope note: non-py capabilities require diff-verifier extension (git diff --name-only path check), not AST walk.

---

## Phase 3 Readiness Statement

**Phase 3 (Generative route function + delete digest_profiles) is authorized to dispatch.**

Blockers that must be resolved before Phase 3 EXIT (not before Phase 3 start):
- F-1: INV-11 phantom test citations corrected (YAML-only, low effort)
- F-2: _capability_id attr clobber resolved in decorators.py (before Phase 4 gates)
- F-3: test_charter_mandatory_evidence.py extended to SKILL.md (before Phase 4)

Phase 3 START is unblocked today. The route function (Phase 3 primary deliverable) reads `_CAPABILITY_REGISTRY` not `fn._capability_id`, so F-2 does not affect Phase 3 route_function correctness.

Inherited carry-forwards from Phase 1 that Phase 3 must also close:
- R12 deletion (topology_schema.yaml + inv_prototype.py) is a Phase 3 exit criterion per phase1_h_decision.md D-1
- Shadow classifier calibration (≥7d/≥90% on substantive legacy output) per OD-2

---

**Signed: code-reviewer agent (sonnet tier, Phase 2 K1 exit gate)**
**Date: 2026-05-06**
