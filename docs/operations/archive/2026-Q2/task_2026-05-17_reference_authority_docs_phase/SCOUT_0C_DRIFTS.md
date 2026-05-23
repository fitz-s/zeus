# SCOUT 0C v2 — Per-Claim Enumeration for TIER 0C Authority MDs

Produced: 2026-05-16
Branch: feat/ref-authority-docs-2026-05-17
Method: grep-verify every cited path / symbol / function in each doc against worktree disk state.
Scope rule: IN = dead-path-ref, dead-symbol-ref, stale-claim, wrong-value. OUT = gap-analysis, prose readability.

---

## Per-file drift count summary

| file | lines | claims audited | in-scope drifts | out-of-scope (gap/prose) noted |
|------|-------|----------------|-----------------|-------------------------------|
| `docs/authority/zeus_change_control_constitution.md` | 646 | ~239 | 8 | 0 |
| `docs/authority/zeus_current_architecture.md` | 440 | ~99 | 4 | 0 |
| `docs/authority/zeus_current_delivery.md` | 352 | ~94 | 0 | 0 |
| `docs/authority/AGENTS.md` | 47 | ~14 | 0 | 0 |

---

## Drifts by file

### docs/authority/zeus_change_control_constitution.md

| # | file:line | old_text | category | evidence | suggested_fix |
|---|-----------|----------|----------|----------|---------------|
| 1 | constitution:237 | `cycle_runner.py` 只负责 orchestration | dead-symbol-ref | `src/execution/cycle_runner.py` MISSING; actual: `src/engine/cycle_runner.py` | change path ref to `src/engine/cycle_runner.py` |
| 2 | constitution:238 | `evaluator.py` 只负责 signal + decision，不负责 authority write | dead-symbol-ref | `src/execution/evaluator.py` MISSING; actual: `src/engine/evaluator.py` | change path ref to `src/engine/evaluator.py` |
| 3 | constitution:240 | `status_summary.py` 只读 projection，不得自持状态 | dead-symbol-ref | `src/state/status_summary.py` MISSING; actual: `src/observability/status_summary.py` | change path ref to `src/observability/status_summary.py` |
| 4 | constitution:265 | `fold_event(...)` | dead-symbol-ref | `grep -rn "def fold_event" src/` → 0 results; symbol does not exist | change to `append_many_and_project(...)` or `project_from_events(...)` per ledger.py |
| 5 | constitution:266 | `apply_transition(...)` | dead-symbol-ref | `grep -rn "def apply_transition" src/` → 0 results; symbol does not exist | remove or replace with actual lifecycle API in `src/state/lifecycle_manager.py` |
| 6 | constitution:278 | 仅允许从 `StrategyKey` enum 取值 | dead-symbol-ref | `grep -rn "class StrategyKey" src/` → 0 results; `StrategyKey` does not exist as an enum | update to reflect actual strategy governance mechanism (strategy is currently a plain string; no enum class exists) |
| 7 | constitution:308 | 只有 `append_event_and_project` / `append_many_and_project` 可同时触达 | dead-symbol-ref | `def append_event_and_project` → 0 results in src/; only `append_many_and_project` exists in `src/state/ledger.py:213` | remove `append_event_and_project` from the cited list; keep `append_many_and_project` only |
| 8 | constitution:309 | 禁止其它模块直接 `INSERT INTO position_events` | stale-claim | `src/state/chain_reconciliation.py:463` has direct `INSERT INTO position_events` outside ledger; also `src/state/ledger.py:156,266` — multiple direct INSERTs exist in ledger itself as the canonical path | clarify that the prohibition is for modules OUTSIDE state/ ledger; note chain_reconciliation.py as approved exception or flag for review |

---

### docs/authority/zeus_current_architecture.md

| # | file:line | old_text | category | evidence | suggested_fix |
|---|-----------|----------|----------|----------|---------------|
| 1 | architecture:237 | `cycle_runner.py` 只负责 orchestration | dead-symbol-ref | `src/execution/cycle_runner.py` MISSING; actual: `src/engine/cycle_runner.py` | change to `src/engine/cycle_runner.py` |
| 2 | architecture:238 | `evaluator.py` 只负责 signal + decision | dead-symbol-ref | `src/execution/evaluator.py` MISSING; actual: `src/engine/evaluator.py` | change to `src/engine/evaluator.py` |
| 3 | architecture:240 | `status_summary.py` 只读 projection | dead-symbol-ref | `src/state/status_summary.py` MISSING; actual: `src/observability/status_summary.py` | change to `src/observability/status_summary.py` |
| 4 | architecture:247 | only `append_event_and_project` / `append_many_and_project` may touch `position_events`/`position_current` | dead-symbol-ref | `append_event_and_project` (singular) → 0 defs; only `append_many_and_project` in `src/state/ledger.py:213` | remove singular form; keep `append_many_and_project` |

**Note:** All 8 file paths cited in §15 Machine-Checkable Sources exist on disk (verified). All lifecycle phase strings in §8.2 match `src/state/lifecycle_manager.py` exactly. `ExecutionPrice`, `HeldSideProbability`, `NativeSidePrice`, `Temperature`, `TemperatureDelta` all verified present.

**ATTRIBUTION_CORRECTED 2026-05-17**: The 4 drifts in the table above (`cycle_runner.py`, `evaluator.py`, `status_summary.py`, `append_event_and_project`) actually live in `zeus_change_control_constitution.md` (rows 1–3 = constitution:237/238/240; row 4 = constitution:308), NOT in `zeus_current_architecture.md`. Independent grep (`git log -S "src/execution/cycle_runner" -- docs/authority/zeus_current_architecture.md`) returns empty — these symbols never existed in `architecture.md`. `zeus_current_architecture.md` was **0-drift CLEAN** per TIER 0C execution. The executor correctly skipped all 4 phantom rows (no fix applied because none were needed). This erratum corrects the SCOUT artifact only; execution was correct. Source: WAVE_4_FINAL_AUDIT.md §Finding F2.

---

### docs/authority/zeus_current_delivery.md

No in-scope drifts found.

All file paths cited in §2–§14 verified present:
- `docs/authority/zeus_current_architecture.md` ✓
- `docs/authority/zeus_change_control_constitution.md` ✓
- `docs/reference/zeus_domain_model.md` ✓
- `docs/operations/current_state.md` ✓
- `docs/operations/current_data_state.md` ✓
- `docs/operations/current_source_validity.md` ✓
- `architecture/task_boot_profiles.yaml` ✓
- `architecture/fatal_misreads.yaml` ✓
- `architecture/city_truth_contract.yaml` ✓
- `architecture/docs_registry.yaml` ✓
- `architecture/map_maintenance.yaml` ✓
- `scripts/topology_doctor.py` ✓ (--planning-lock behavior confirmed in source)
- `workspace_map.md` ✓
- `AGENTS.md` ✓

---

### docs/authority/AGENTS.md

No in-scope drifts found.

File registry at lines 40–44 lists 3 files; all 3 exist. `docs/reports/authority_history/` cited in line 46 verified present.

---

## Aggregate

WAVE 3 TIER 0C in-scope total: **12 drifts across 2 files** (8 in constitution, 4 in architecture; 0 in delivery, 0 in AGENTS.md).

All 12 drifts are concentrated in the same 3 symbol families:
- **Module paths** (cycle_runner, evaluator, status_summary): 6 drifts (3 per doc × 2 docs) — modules moved from `src/execution/` and `src/state/` to `src/engine/` and `src/observability/` respectively.
- **Dead API name** (`append_event_and_project` singular): 2 drifts (1 per doc × 2 docs) — only `append_many_and_project` exists.
- **Dead enum** (`StrategyKey`, `fold_event`, `apply_transition`): 4 drifts in constitution only — these symbols appear to have never been implemented or were removed before the doc was written.

**Operator decision item**: `StrategyKey` absence is notable. The constitution §8.2 mandates "only allow values from `StrategyKey` enum" as an AST enforcement rule, but no such enum class exists anywhere in `src/`. Either (a) the enum was planned but never implemented (gap that WAVE 3 should not fix in authority doc — only note), or (b) strategy governance is enforced via another mechanism (plain string + runtime check). WAVE 3 should note this as a stale-claim with "implementation gap" annotation rather than a doc-only fix. Operator awareness recommended before WAVE 3 applies the suggested fix for drift #6.
