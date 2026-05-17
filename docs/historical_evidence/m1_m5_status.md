# M1-M5 Anti-Drift Mechanism Status

**Generated:** 2026-05-06  
**Authority:** ANTI_DRIFT_CHARTER §§3-7; IMPLEMENTATION_PLAN Phase 5.A deliverable A-3  
**Phase:** 5.A (days 71-90) — telemetry consolidation  
**Purpose:** Cutover precondition checklist. All mechanisms must be LIVE before Phase 5.D cutover.

---

## Status table

| Mechanism | Status | Enforcement file(s) | Test file(s) | Last verified | PASS/FAIL/SKIP |
|---|---|---|---|---|---|
| M1 Telemetry | LIVE | `logs/ritual_signal/` (448 entries) + `scripts/ritual_signal_aggregate.py` | `tests/test_ritual_signal_emission.py` + `tests/test_ritual_signal_aggregate.py` | 2026-05-06 | 15 passed / 0 failed / 0 skipped |
| M2 Opt-in (mandatory + evidence) | LIVE | `architecture/invariants.yaml` (forward guard; 0 mandatory:true entries) | `tests/test_charter_mandatory_evidence.py` | 2026-05-06 | 5 passed / 0 failed / 3 skipped |
| M3 Sunset (sunset_date) | LIVE | gate modules (`gate_edit_time`, `gate_commit_time`, `gate_runtime`, `live_executor`) + 3 stable-layer YAMLs | `tests/test_charter_sunset_required.py` | 2026-05-06 | 50 passed / 0 failed / 0 skipped |
| M4 Intent contract (original_intent) | LIVE | `architecture/capabilities.yaml` (6 entries with original_intent blocks) | `tests/test_capability_decorator_coverage.py` | 2026-05-06 | 4 passed / 0 failed / 2 skipped |
| M5 INV-HELP-NOT-GATE | LIVE | `tests/test_help_not_gate.py` (self-enforcing) | `tests/test_help_not_gate.py` | 2026-05-06 | 3 passed / 0 failed / 0 skipped |

**All 5 mechanisms: LIVE.**

---

## Detail per mechanism

### M1 — Telemetry-as-output

- **Enforcement:** Every gate call writes to `logs/ritual_signal/YYYY-MM.jsonl` with full CHARTER §3 schema.
- **Aggregator:** `scripts/ritual_signal_aggregate.py` — reads all `.jsonl` files, computes per-gate / per-cap_id / per-decision counts across 24h / 7d / 30d windows.
- **Baseline:** `evidence/ritual_signal_baseline.json` — current-month distribution as M1 "what good looks like" reference.
- **Schema compliance:** `test_every_invocation_emits_ritual_signal` now asserts cap_id field presence AND resolution to capabilities.yaml (Phase 5.A production assertion). Known Phase 4.D deferred IDs listed in `_PHASE4D_DEFERRED_CAP_IDS`.
- **Tests:** `test_ritual_signal_emission.py` — 7 passed. `test_ritual_signal_aggregate.py` — 8 passed.

### M2 — Opt-in by default, escalation by evidence

- **Enforcement:** `test_charter_mandatory_evidence.py` scans all SKILL.md frontmatter files for `mandatory: true`. Zero such entries exist today (forward guard — test will fail-closed when first `mandatory: true` entry is added without the 3-key evidence block).
- **Tests:** 5 passed, 3 skipped (skip = no mandatory:true entries to validate the `mandatory_evidence` sub-keys against; forward guard functional).

### M3 — Sunset clock per artifact

- **Enforcement:** Every YAML entry in capabilities.yaml, reversibility.yaml, invariants.yaml carries `sunset_date`. Every gate module carries `SUNSET_DATE` constant.
- **Tests:** `test_charter_sunset_required.py` — 50 passed covering all 36 invariant entries + 4 gate modules.

### M4 — Original-intent contract per helper

- **Enforcement:** `architecture/capabilities.yaml` — each of the 6 entries carries `original_intent.intent_test`, `does_not_fit`, `scope_keywords`, `out_of_scope_keywords`. Decorator coverage verified via AST walk.
- **Tests:** `test_capability_decorator_coverage.py` — 4 passed, 2 skipped (authority_doc_rewrite + archive_promotion have no .py hard_kernel_paths — docs-only skip per Phase 2 critic C-6).

### M5 — INV-HELP-NOT-GATE invariant + relationship test

- **Enforcement:** `tests/test_help_not_gate.py` is the enforcement artifact (self-enforcing invariant test).
- **Assertions (all 3 per CHARTER §7):**
  1. `test_no_helper_blocks_unrelated_capability` — PASS (no cross-capability blocking)
  2. `test_every_invocation_emits_ritual_signal` — PASS (Phase 5.A production assertion: schema + cap_id resolution)
  3. `test_does_not_fit_returns_zero` — PASS (no helper has unconstrained forbidden_files)
- **Phase 5.A change:** assertion 2 upgraded from SKIP guard to real cap_id resolution check. Skip count: 1 → 0.

---

## Known gaps (not blocking cutover)

| Gap | Tracked by | Phase |
|---|---|---|
| 7 Phase 4.D cap_ids not yet in capabilities.yaml | `_PHASE4D_DEFERRED_CAP_IDS` in test_help_not_gate.py | capabilities.yaml Phase 1 carry-forward |
| 2 capabilities (authority_doc_rewrite, archive_promotion) with docs-only hard_kernel_paths | test_capability_decorator_coverage.py skip | Phase 4 F-7 carry |
| gate2_live_auth_token + replay_correctness_gate older log format (no cap_id field) | `_HELPERS_WITHOUT_CAP_ID` in test_help_not_gate.py | Phase 4.D format gap |
| M2 mandatory:true forward guard not yet exercised | test_charter_mandatory_evidence.py 3 skips | Will activate on first mandatory:true entry |

---

## Cutover pre-gate status

Per CUTOVER_RUNBOOK: all 5 mechanisms must be LIVE before Phase 5.D. **Condition met.**
