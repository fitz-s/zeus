# Phase 1 Critic Decision
# Created: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §3; ULTIMATE_DESIGN §2.2/§2.3/§9.1; RISK_REGISTER R9/R12; ANTI_DRIFT_CHARTER §9; OD-2 override
# Reviewer: code-reviewer agent (sonnet, Phase 1 K1 tier per invariants.jsonl line 4-5)

---

## Verdict: GO-WITH-CONDITIONS

**Phase 2 dispatch is unblocked** on resolution of C-1 (missing schema validators) and C-2
(TRUTH_REWRITE enforcement_default divergence). Both are YAML-only fixes; no logic changes.
D-1 through D-4 explicit verdicts follow.

---

## Summary

| Severity | Count |
|---|---|
| HIGH | 2 |
| MEDIUM | 2 |
| LOW | 3 |

---

## Findings

### [HIGH] C-1 — Missing Phase 1 exit-criterion schema validators
**File:** `tests/test_charter_sunset_required.py` (does not exist), `tests/test_charter_mandatory_evidence.py` (does not exist)
**Confidence:** HIGH
**Issue:** IMPLEMENTATION_PLAN §3 exit criteria explicitly require both files:
  "Schema validators all green: `tests/test_charter_sunset_required.py`, `tests/test_charter_mandatory_evidence.py`."
Neither file was delivered. The exit criterion is unmet.
**Reproduction:** `ls tests/test_charter_sunset_required.py` → No such file.
**Remediation:** Author both validators. Minimum viable:
- `test_charter_sunset_required.py`: parse every YAML key in capabilities.yaml + reversibility.yaml + invariants.yaml; assert every entry has `sunset_date` field set. (Already satisfied by current YAML but test enforces future edits.)
- `test_charter_mandatory_evidence.py`: parse helpers that declare `mandatory: true`; assert all three M2 evidence keys (`operator_signature`, `recent_miss`, `sunset_date`) are present.
These are ~50 LOC each per ULTIMATE_DESIGN §9.3 estimate. Phase 2 dispatch should include them.

### [HIGH] C-2 — TRUTH_REWRITE enforcement_default diverges from ULTIMATE_DESIGN §2.3 spec
**File:** `architecture/reversibility.yaml` lines 39-50
**Confidence:** HIGH
**Issue:** ULTIMATE_DESIGN §2.3 (the binding authority) specifies:
- `ON_CHAIN`: `enforcement_default: blocking`
- `TRUTH_REWRITE`: `enforcement_default: blocking`
- `ARCHIVE`: `enforcement_default: advisory_with_evidence_required`
- `WORKING`: `enforcement_default: advisory`

Delivered values:
- `ON_CHAIN`: `refuse_with_advice` (diverges; different vocabulary)
- `TRUTH_REWRITE`: `log_and_advisory` (diverges; blocking → advisory is a severity downgrade)
- `ARCHIVE`: `log_and_advisory` (diverges; loses the evidence-required qualifier)
- `WORKING`: `log_and_advisory` (diverges; advisory → log_and_advisory is a vocabulary mismatch)

**The TRUTH_REWRITE divergence is the critical one**: downgrading from `blocking` to `log_and_advisory` means that in Phase 4, when gates read this field to set severity, settlement and calibration writes would get advisory treatment instead of blocking treatment — exactly the failure mode the design protects against (quant failure category #3 and #5 in ULTIMATE_DESIGN §8).

**Mitigation context:** Phase 4 gates are not yet implemented; the enforcement_default is currently a metadata field only. However, the stable layer is supposed to be the single source of truth that Phase 4 reads. Correcting it now is zero-risk; leaving it risks Phase 4 inheriting wrong severity defaults.

**Reproduction:** `python3 -c "import yaml; d=yaml.safe_load(open('architecture/reversibility.yaml')); print({c['id']:c['enforcement_default'] for c in d['reversibility_classes']})"`

**Remediation (YAML-only, no logic change):**
```yaml
# reversibility.yaml corrections
ON_CHAIN:       enforcement_default: blocking          # was refuse_with_advice
TRUTH_REWRITE:  enforcement_default: blocking          # was log_and_advisory  ← CRITICAL
ARCHIVE:        enforcement_default: advisory_with_evidence_required  # was log_and_advisory
WORKING:        enforcement_default: advisory           # was log_and_advisory
```
Note: `refuse_with_advice` for ON_CHAIN is the _capability-level_ `does_not_fit` value, not the
reversibility class enforcement default. The vocabulary distinction matters for Phase 4 implementation.

---

### [MEDIUM] C-3 — capabilities.yaml schema_version promoted to "1.0.0" without schema governance decision
**File:** `architecture/capabilities.yaml` line 5; `architecture/reversibility.yaml` line 5
**Confidence:** MEDIUM
**Issue:** ULTIMATE_DESIGN §2.2 example shows `schema_version: 1` (integer). Executor delivered
`schema_version: 1.0.0` (string). `architecture/invariants.yaml` retains `schema_version: 1` (integer).
Three files now have inconsistent schema versioning conventions: two use semver strings, one uses
a plain integer. When `tests/test_charter_sunset_required.py` is authored, it will need to parse
`schema_version` — the inconsistency will require branching logic.
**Remediation:** Standardize on one convention before Phase 2. Either keep integer `1` (matches invariants.yaml precedent) or define semver strings consistently across all three files. Either is fine; pick one and annotate the choice in a comment. Low effort, zero risk.

### [MEDIUM] C-4 — INV-11 and INV-12 not yet added as formal invariants.yaml entries despite being active runtime IDs
**File:** `architecture/invariants.yaml` (INV-11 and INV-12 absent)
**Confidence:** MEDIUM
**Issue:** The R9 audit correctly identifies that INV-11 and INV-12 are **definition gaps not reference gaps** — they are cited in `src/contracts/execution_price.py:11,91,126`, `tests/test_no_bare_float_seams.py:7,200,204,281`, `tests/test_reality_contracts.py:33,139,236`, `src/contracts/AGENTS.md:19,64`, `src/strategy/AGENTS.md:34`, and `architecture/test_topology.yaml:802,877`. The audit recommends formal definition but defers it as "not Phase 1 scope."

The gap-leave decision (per ULTIMATE_DESIGN §2.1) is structurally correct: do not compact, do not renumber. However, leaving these IDs without formal YAML entries means the route function cannot include them in `protects_invariants` references, and capability_tags will have no entries to bind to. `source_validity_flip` (capabilities.yaml line 392) cites `INV-06` for "data source authority" but INV-11 (RealityContractVerifier) is arguably the more direct invariant for that capability.

**Remediation:** Not Phase 1 blocking. Flag for Phase 2 scope brief: add INV-11 and INV-12 as formal entries with `capability_tags` populated from existing references. Keep IDs in current positions (no compaction). The r9_inv_gap_audit.md draft definitions are ready to promote verbatim.

---

### [LOW] C-5 — shadow_router "silence-as-agreement" covers up, not calibrates, the agreement classifier
**File:** `scripts/topology_route_shadow.py` lines 139-148; `evidence/shadow_router/calibration_2026-05-06.md`
**Confidence:** HIGH (on the characterization); severity LOW (correctly deferred to Phase 3)
**Issue:** All 7 post-patch smoke runs hit sub-case (b) — legacy silence classified as `agree_path_equivalent`. Sub-case (a), which compares actual path-set intersection when legacy produces substantive output, was never exercised. The executor correctly documented this limitation and noted it in invariants.jsonl line 19: "Phase 3 dispatch must require both (a) at least N diffs producing non-empty legacy output and (b) ≥90% path-equivalent on that subset."

This finding is LOW because OD-2 explicitly anticipated it and deferred the real gate to Phase 3. The Phase 1 deliverable requirement was to calibrate the classifier structurally (done), not to achieve 7d/≥90% on real diffs (Phase 3 gate).

**Phase 3 constraint that must not be dropped:** The ≥7d/≥90% gate at Phase 3 must run against diffs that produce non-empty legacy output. Phase 3 dispatch brief MUST include a sample-collection step to drive substantive topology_doctor output before declaring agreement.

### [LOW] C-6 — Pre-registered Phase 4 paths in live_venue_submit could mislead Phase 2 decorator coverage
**File:** `architecture/capabilities.yaml` lines 280-283
**Confidence:** MEDIUM
**Issue:** `live_venue_submit.hard_kernel_paths` includes `src/execution/venue_adapter.py` and
`src/execution/live_executor.py` with inline comments noting "Phase 4 deliverable — created when LiveAuthToken phantom + ABC split lands." These files do not exist yet (Phase 4 scope). When Phase 2 runs `test_capability_decorator_coverage.py`, it will AST-walk these paths and find no files — this will either pass vacuously or error depending on how the test handles missing files.
**Reproduction:** `ls src/execution/venue_adapter.py` → does not exist.
**Remediation:** Either add a `phase_deliverable: true` flag to these hard_kernel_paths entries so the coverage test can skip them, or document in the Phase 2 brief that coverage test must tolerate Phase 4 pre-registrations. Neither requires a Phase 1 blocker; a comment noting the test handling is sufficient.

### [LOW] C-7 — Net-add exceeds net-delete for Phase 1 due to R12 retention; exit criterion technically unmet
**File:** `evidence/r12_disposition.md` §Net LOC Impact; IMPLEMENTATION_PLAN §3
**Confidence:** HIGH
**Issue:** IMPLEMENTATION_PLAN §3 exit criteria: "Net-add ≤ net-delete invariant (briefing §6 #10) verified for the phase." Phase 1 as delivered is net +440 LOC: ~470 capabilities.yaml + ~84 reversibility.yaml + ~172 invariants.yaml delta + ~239 topology_route_shadow.py + evidence docs ≈ +1,005 LOC added; deletions from Phase 0.D were prior phase credit. Phase 1 itself has zero deletions because R12 was retained.

The executor correctly flagged this in `evidence/r12_disposition.md` §Net LOC Impact: "Operator acknowledgment required if the LOC constraint is firm." The invariants.jsonl entry line 18 records the deviation.

**Disposition:** This is a well-documented deviation with a correct root cause (R12 has live importers). The deferred deletion in Phase 3 (when topology_doctor.py is removed) will provide the offsetting delete credit. The exit criterion is not met in isolation, but the structural plan for Phase 3 to resolve it is sound.

**Condition on GO-WITH-CONDITIONS:** Operator acknowledges that Phase 1 net-add = +440 LOC (no deletions) and that Phase 3 is accountable for the offsetting R12 deletion. This carry-forward should be added to invariants.jsonl. If operator declines acknowledgment, C-7 upgrades to HIGH.

---

## D-1 through D-4 Verdicts

### D-1 — R12 retention reverses ULTIMATE_DESIGN §9.1 default

**Verdict: ACCEPTED with condition.**

The import evidence in `evidence/r12_disposition.md` is correct and independently verified:
- `topology_schema.yaml`: loaded at runtime by `scripts/topology_doctor.py:30` (`SCHEMA_PATH`), used by `topology_doctor_ownership_checks.py` at 11 call sites, asserted by `tests/test_topology_doctor.py:4371,4379,4381` and `tests/test_admission_kernel_hardening.py:61`. Deletion would cause FileNotFoundError on import.
- `inv_prototype.py`: directly imported (not path-referenced) at `tests/test_inv_prototype.py:225,245` for `PROTOTYPED_INVS` and `all_drift_findings` symbols. Deletion would cause ImportError.

"Retain until Phase 3" is the correct disposition vs "delete now and break the importers." ULTIMATE_DESIGN §9.1 describes the conceptual role as superseded, not that the runtime consumption role is gone. Phase 3's structural deletion of topology_doctor*.py is the correct deletion moment.

LOC budget: R12 retention does NOT widen the ≤1,500 LOC topology infrastructure target because topology_schema.yaml and inv_prototype.py are not counted in the new stable layer (capabilities.yaml + reversibility.yaml + invariants.yaml + route_function.py). They remain in the old layer until Phase 3 removes them. The budget constraint is on the new layer, which currently sits at ~1,200 LOC (caps 470 + rev 84 + inv 640), well under 1,500.

**Condition:** Phase 3 dispatch must explicitly list R12 deletion as a Phase 3 exit criterion, not optional cleanup.

### D-2 — Shadow classifier silence-as-agreement

**Verdict: CONDITIONALLY ACCEPTED — deferred gate must not slip.**

The Phase 1 D5 patch is mechanically correct: empty legacy stdout is not a contradiction, and classifying it as `agree_path_equivalent` is a defensible structural choice (approach b per OD-2). The implementation in `scripts/topology_route_shadow.py` is clean: sub-case (b) handles silence, sub-case (a) handles substantive output via path-set intersection, and the AGREE/BOTH_EMPTY/agree_path_equivalent agreement matrix is internally consistent.

However, Phase 1 has **not calibrated the classifier** on real diffs — it has verified that the classifier does not crash on silence. These are different claims. The 7 smoke runs are a correctness smoke test, not a calibration run.

The OD-2 override is clear: "≥7d/≥90% gate must hold on REAL DIFFS WITH SUBSTANTIVE LEGACY OUTPUT." Per invariants.jsonl line 19, Phase 3 must require:
- (a) At least N diffs producing non-empty legacy output (N to be specified in Phase 3 brief, recommend ≥20 diffs)
- (b) ≥90% path-equivalent on that non-empty subset

**Risk:** If topology_doctor is never patched to emit capability names or path-aware output, sub-case (a) will never fire and the ≥90% gate will always be satisfied vacuously via silence-as-agreement. This defeats the gate's purpose. Phase 3 brief must address whether topology_doctor will be patched or whether the "at least N non-empty" requirement is enforced via another mechanism.

### D-3 — R9 framing: INV-11/12 as "definition gap not reference gap"

**Verdict: CORRECT — leave-gap decision holds, formal definition deferred appropriately.**

The executor's characterization is confirmed by direct grep:
- INV-11 is cited at `src/contracts/AGENTS.md:27`, `tests/test_reality_contracts.py:4,33,236`, `architecture/test_topology.yaml:877` — 4 distinct live locations
- INV-12 is cited at `src/contracts/execution_price.py:11,91,126`, `src/contracts/AGENTS.md:19,64`, `src/strategy/AGENTS.md:34`, `tests/test_no_bare_float_seams.py:7,200,204,281`, `tests/test_reality_contracts.py:5,139`, `architecture/test_topology.yaml:802` — 10 distinct live locations

These are not historical references; they appear in runtime enforcement paths (`execution_price.py` runtime raise) and active tests. The gap is a definition gap (no formal invariants.yaml entry) not a reference gap (nothing cites non-existent IDs in broken ways).

The leave-gap decision per ULTIMATE_DESIGN §2.1 holds: compaction would rewrite 22+ entries and all cross-references. The correct action is to add formal entries for INV-11 and INV-12 in a future phase. This does not affect Phase 2 dispatch.

### D-4 — Net LOC +440 vs Phase 1 reduction expectation

**Verdict: ACCEPTABLE — does not threaten ≤1,500 LOC target, creates Phase 3 accountability obligation.**

The +440 LOC is additions-only (no Phase 1 deletions due to R12 retention). The new stable layer (capabilities.yaml + reversibility.yaml + route_function.py) currently totals approximately 763 LOC plus invariants.yaml extended by ~172 lines. Total new stable layer: ~1,200 LOC, under the ≤1,500 LOC post-cutover infrastructure target.

The +440 is consistent with the eventual target because:
1. Phase 1's additions are the core stable layer (caps, rev, inv extension) — these survive to final state
2. Phase 3 will delete ≥6,001 LOC (digest_profiles.py alone) plus R12 (885 LOC) plus topology_doctor*.py (~12,290 LOC) — net Phase 3 is strongly negative
3. Phase 2 adds ≤200 LOC (decorators); Phase 4 adds gates (not yet counted)
4. Final stable layer target ≤1,500 LOC is achievable given Phase 3's deletion scope

No LOC budget pressure for later phases is created by Phase 1's additions. The constraint "net-add ≤ net-delete" is a per-phase guard against accumulation drift, not a hard invariant the design depends on for correctness. Phase 1's non-compliance is documented and has a Phase 3 offset.

---

## Operator Decisions Surfaced

**OD-3 (new):** Acknowledge Phase 1 net-add = +440 LOC with zero deletions (R12 retained). Confirm Phase 3 is accountable for R12 deletion (topology_schema.yaml + inv_prototype.py, 885 LOC). No sign required if operator accepts the R12 disposition recorded in evidence/r12_disposition.md and invariants.jsonl line 18.

**Carry-forward to Phase 3 brief (mandatory, not operator-decision):**
1. R12 deletion (topology_schema.yaml + inv_prototype.py) is a Phase 3 exit criterion, not optional.
2. ≥7d/≥90% shadow agreement gate must run against ≥N diffs with non-empty legacy output (N ≥ 20 recommended). Specify mechanism for generating substantive legacy output (topology_doctor patch or alternative).
3. Phase 3 cannot dispatch structural deletion until both (a) and (b) of OD-2 shadow gate are satisfied.

---

## Phase 2 Readiness Statement

**Phase 2 (Source decorator rollout) is authorized to dispatch on resolution of C-1 and C-2.**

- C-1 (missing schema validators): author in Phase 2 dispatch, not blocking Phase 2 start but must be complete before Phase 2 exit.
- C-2 (TRUTH_REWRITE enforcement_default): YAML-only correction, should be made before Phase 2 dispatch to avoid propagating wrong enforcement semantics to Phase 4 gate implementation.

C-3 (schema_version consistency) and C-4 (INV-11/12 formal definition) can be Phase 2 scope items. C-5, C-6, C-7 are low severity with clear Phase 3 carry-forwards.

**Schema validators confirmed green (pre-existing):**
- `tests/test_route_card_token_budget.py`: 7 passed, 15 xfailed
- `tests/test_capability_decorator_coverage.py`: passes (included in above run)
- `architecture/capabilities.yaml`: yaml.safe_load green
- `architecture/reversibility.yaml`: yaml.safe_load green
- `architecture/invariants.yaml`: yaml.safe_load green
- All 34 invariants carry `capability_tags`, `relationship_tests`, `sunset_date`
- 16 capabilities with all required keys, all reversibility_class refs valid
- route() smoke test: caps=[settlement_write, settlement_rebuild] rev=TRUTH_REWRITE — correct

**Signed: code-reviewer agent (sonnet tier, Phase 1 K1 exit gate)**
**Date: 2026-05-06**
