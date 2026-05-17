# T1A Executor Boot Evidence

Executor: executor-sonnet (subagent dispatched 2026-05-04 from coordinator)
Phase: T1A — Single Source Of Truth For settlement_commands DDL
Phase contract: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1A/phase.json
Phase scope: docs/operations/task_2026-05-04_zeus_may3_review_remediation/phases/T1A/scope.yaml
HEAD at boot: 1116d827

---

## 0 Read Summary

- `AGENTS.md` (root): money path, K0/K1/K2/K3 surface taxonomy, planning-lock rules (src/state/** requires planning evidence), mesh maintenance, change classification.
- `src/execution/AGENTS.md`: settlement_commands.py is HIGH danger; settlement redemption side effects flow through it; do not call adapter paths directly.
- `src/state/AGENTS.md`: db.py is CRITICAL (K0/K1 truth zone); any schema/truth-ownership change requires approved packet and planning-lock evidence; append-first discipline is load-bearing.
- `MASTER_PLAN_v2.md §10 T1A`: single-source DDL objective, allowed/forbidden files, function-scope import mitigation for circular import, grep acceptance criterion, rollback = revert import commit only.
- `MASTER_PLAN_v2.md §9 / §13`: mandatory packet preamble ("do not opportunistically refactor"), manifest/header rules for new test files.
- `ORCHESTRATOR_RUNBOOK.md §7`: executor boot protocol — write evidence, BOOT_ACK, idle.
- `ORCHESTRATOR_RUNBOOK.md §8`: GO/DONE/REVIEW protocol; batch evidence must be written to disk before reporting.
- `ORCHESTRATOR_RUNBOOK.md §11`: git-master rules — no broad staging; explicit files only.
- `ORCHESTRATOR_RUNBOOK.md §16`: dispatch checklist — all gates must be cleared before GO_BATCH_1.
- `phases/T1A/phase.json`: files_touched = [settlement_commands.py, db.py, test_settlement_commands_schema.py, architecture/test_topology.yaml]; 3 asserted invariants; key planner note: topology admission BLOCKED at planner time — executor MUST run topology gate at GO_BATCH_1 and stop if rejected.
- `phases/T1A/scope.yaml`: in_scope = 3 source/test files + test_topology.yaml companion; out_of_scope includes harvester.py, executor.py, src/venue/**, src/strategy/**, src/contracts/**, src/engine/**, src/riskguard/**; tests/test_settlement_commands.py is READ-ONLY context (not in_scope).
- `T-1_SCHEMA_SCAN.md`: confirmed 2 DDL sites — settlement_commands.py:28 (canonical) and db.py:1398 (duplicate); both define identical 14-column schema and 3 indexes; db.py comment explicitly says "kept here to avoid circular import src.execution."
- `planner_output.md §1 (Round-1)`: topology engine returns NOT ADMITTED for T1A's file set under any current profile; planning-lock passes with --plan-evidence PLAN.md; operator blockers T0.2 (RiskGuard) and T0.3 (venue) still unresolved at planner time.
- `planner_output.md §Round-2 AMD-10`: T1E has a predecessor dependency on T1A's db.py edit (both phases touch db.py); T1A's edit must be structured so T1E's addition of ZEUS_DB_BUSY_TIMEOUT_MS at lines ~40 and ~349 does not conflict — keep T1A's change localized to the ~1395-1437 block (the inline executescript call site).

---

## 1 KEY OPEN QUESTIONS

**T1A-Q-1**: Topology admission status — phase.json `_planner_notes.topology_admission` says BLOCKED at planner time under generic profile. MASTER_PLAN_v2 LOCK_DECISION Amendment 4 says operator must either (a) add 7 new digest profiles, or (b) authorize advisory-only admission with critic enforcing scope. Has Amendment 4 been resolved?
- Proposed default: if coordinator has not confirmed Amendment 4 resolution, executor runs `python3 scripts/topology_doctor.py --navigation` at GO_BATCH_1 and STOPS if result is still NOT ADMITTED, per phase.json `_planner_notes.topology_admission` instruction.

**T1A-Q-2**: Circular import guard — db.py comment at line 1395-1396 says "Keep DDL in the schema owner so DB initialization does not import src.execution during startup." The plan specifies function-scope import as mitigation. Does the coordinator confirm function-scope import is acceptable, or does an init-helper re-export from settlement_commands.py need a separate module to avoid pulling execution-side deps into db.py's import chain?
- Proposed default: use function-scope import inside `init_schema()` (deferred to call time, not module-load time), which is the exact mitigation named in MASTER_PLAN_v2 §10 T1A and phase.json `_planner_notes.circular_import_risk`. No separate module introduced.

**T1A-Q-3**: T0 operator blockers — T0.2 (RiskGuard unload) and T0.3 (venue quiescence) were unresolved at planner time. Has the coordinator confirmed both are now attested in T0_PROTOCOL_ACK.md with `Decision: proceed_to_T1`?
- Proposed default: executor idles and does not implement if T0_PROTOCOL_ACK.md is absent or does not say `proceed_to_T1`. This is a hard gate per MASTER_PLAN_v2 §8 and ORCHESTRATOR_RUNBOOK.md §14.

---

## 2 File/Scope Confirmation

| File | Exists | Current LOC (approx) | Planned Change | Notes |
|---|---|---|---|---|
| src/execution/settlement_commands.py | YES | ~200 LOC | Remains DDL owner; add `init_db(conn)` helper function that calls `conn.executescript(SETTLEMENT_COMMAND_SCHEMA)` (or expose existing `_init_schema` helper if one exists) | Canonical DDL already at line 27-71; executescript call at line 196. No DDL change. |
| src/state/db.py | YES | ~1500+ LOC | Replace inline executescript block at lines 1395-1437 with function-scope import of settlement_commands + call to its init helper | K0/K1 zone; planning-lock required; plan evidence = PLAN.md (admitted at planner time). |
| tests/test_settlement_commands_schema.py | NO (new) | 0 | Create new test file with relationship test `test_settlement_commands_single_source_of_truth` | Must carry lifecycle provenance header per MASTER_PLAN_v2 §13. |
| architecture/test_topology.yaml | YES | ~250+ rows | Add row for tests/test_settlement_commands_schema.py | Companion update; must register new test file before using it as closeout evidence. |

Path ambiguity: none. All four files are at expected paths at HEAD 1116d827. The planner's file:line citations (`settlement_commands.py:27`, `settlement_commands.py:196`, `db.py:1398`) grep-verified by executor at boot time — confirmed accurate.

---

## 3 Risk Map

| File | K-Zone | Specific Failure Mode | Mitigation |
|---|---|---|---|
| src/execution/settlement_commands.py | K2 (execution schema) | Adding an `init_db()` helper that imports execution-side deps could cause them to be eagerly evaluated at import time → circular import at db.py module load | Ensure the helper only calls `conn.executescript(SETTLEMENT_COMMAND_SCHEMA)` with no new imports; SETTLEMENT_COMMAND_SCHEMA is a plain string constant. |
| src/state/db.py | K0/K1 (truth ownership / schema init) | If the function-scope import fails at runtime (e.g., settlement_commands.py has a broken dep), `init_schema()` would raise mid-initialization and leave DB partially initialized | The import is of a string constant; Python will fail fast at import time, not silently. Risk is low but noted. Execution-side deps (CutoverGuard, FXClassification, collateral_ledger) are module-level in settlement_commands.py — function-scope import of the module will trigger their import. Coordinator must confirm function-scope import of the whole module is acceptable vs. a narrower re-export. |
| src/state/db.py | K0/K1 | Transaction-boundary breakage: the inline executescript is called within `init_schema()` which may or may not be inside a transaction boundary. Replacing it with a call to another module's function changes nothing semantically (executescript still runs) but must not introduce a second transaction or autocommit. | The replacement call must be `import src.execution.settlement_commands as _sc; conn.executescript(_sc.SETTLEMENT_COMMAND_SCHEMA)` or equivalent — no new transaction context. |
| src/state/db.py | K0/K1 | db.py's module-level imports from src.state.* (e.g., collateral_ledger, portfolio) may already pull in execution deps indirectly; but the explicit warning in the comment at line 1395 suggests this was a known risk at time of writing | Must verify that `src/execution/settlement_commands.py`'s module-level imports do not create a new import cycle through db.py. Grep confirms settlement_commands.py imports from `src.state.collateral_ledger` and `src.control.cutover_guard` — NOT from db.py directly. Cycle risk: settlement_commands.py → collateral_ledger.py → (does collateral_ledger import db.py?) needs verification at GO_BATCH_1 before editing. |
| tests/test_settlement_commands_schema.py | Test (new) | New test file with no provenance header → violates MASTER_PLAN_v2 §13 manifest/header rule | Must add lifecycle header at file creation. |
| architecture/test_topology.yaml | Manifest companion | Omitting the row registration makes the new test file invisible to topology routing → future agents cannot discover it | Must add row before using test as closeout evidence. |

**K0/K1 call-out specific to db.py**: `init_schema()` is a CRITICAL schema-init path. Any exception or behavior change inside it can prevent DB initialization entirely, which would crash the daemon startup. The change is structurally safe (string constant import vs inline string) but must be verified against:
- The import chain of settlement_commands.py does not loop back to db.py.
- The `executescript()` call is functionally identical (same SQL string).

---

## 4 Planned Batches

**Batch 1 (B1): Circular import analysis + failing relationship test**
- Run import-cycle verification: check that settlement_commands.py → collateral_ledger.py → ... does not import db.py at module level.
- Write `tests/test_settlement_commands_schema.py` with `test_settlement_commands_single_source_of_truth` (grep-based, fails on HEAD because db.py has inline DDL).
- Update `architecture/test_topology.yaml` with the new test file row.
- Run test to confirm it fails on current HEAD (expected pre-fix failure evidence).

**Batch 2 (B2): Implement single-source redirect in db.py**
- Replace the inline `conn.executescript("""CREATE TABLE IF NOT EXISTS settlement_commands ...""")` block at db.py lines 1395-1437 with a function-scope import and call: `from src.execution.settlement_commands import SETTLEMENT_COMMAND_SCHEMA; conn.executescript(SETTLEMENT_COMMAND_SCHEMA)`.
- No other changes to db.py.
- No changes to settlement_commands.py DDL content.
- Run `python -m pytest -q tests/test_settlement_commands_schema.py` — should now pass.
- Run `python -m pytest -q tests/test_settlement_commands.py` — existing tests must still pass (T1A-NO-BEHAVIOR-CHANGE invariant).

**Batch 3 (B3): Verification and grep acceptance gate**
- Run `git grep -n "CREATE TABLE IF NOT EXISTS settlement_commands"` and confirm exactly one result: `src/execution/settlement_commands.py:28`.
- Run existing DB init tests identified by topology.
- Write `execution/execution_result.md` with all evidence before reporting BATCH_DONE.

Each batch is independently testable. Batch 1 produces a failing test. Batch 2 makes it pass. Batch 3 confirms the grep invariant and existing test suite.

---

## 5 Tests Expected To Fail Before Fix

**Pre-fix expected failures (on HEAD 1116d827):**

1. `test_settlement_commands_single_source_of_truth` (in `tests/test_settlement_commands_schema.py`) — this file does not exist yet at HEAD; once created in B1, it will fail because `git grep` will find 2 DDL sites.

**ORCHESTRATOR_RUNBOOK §9 Attack-2 signal**: The test file does not yet exist. The executor will CREATE it in B1 with a deliberately failing assertion, then confirm the failure before proceeding to B2. This is the verification-first pattern for a new test. The pre-fix failure evidence must be explicitly captured in `execution_result.md`.

**Existing passing tests**: `tests/test_settlement_commands.py` currently passes. It must continue to pass after T1A (T1A-NO-BEHAVIOR-CHANGE invariant). If it fails after B2, that is a regression, not a pre-fix failure.

**Note**: No existing test currently enforces the single-source-of-truth property. This is exactly why the duplicate exists — the constraint is not machine-checked. T1A adds the machine check.

---

## 6 Out-Of-Scope Reaffirmation

Per `phases/T1A/scope.yaml` and MASTER_PLAN_v2 §10 T1A "Forbidden files":

- `src/execution/harvester.py` — NOT TOUCHED. Settlement harvesting logic is T1C scope.
- `src/execution/executor.py` — NOT TOUCHED. Live order placement is T1G scope.
- `src/venue/**` — NOT TOUCHED. Venue adapter is T1F scope.
- `src/strategy/**` — NOT TOUCHED.
- `src/contracts/**` — NOT TOUCHED.
- `src/engine/**` — NOT TOUCHED.
- `src/riskguard/**` — NOT TOUCHED.
- `src/calibration/**` — NOT TOUCHED.
- `src/data/**` — NOT TOUCHED.
- `.github/**` — NOT TOUCHED.
- `config/**` — NOT TOUCHED.
- `*.db`, `*.sqlite` — NOT TOUCHED (no production DB mutation).
- `architecture/**` beyond `architecture/test_topology.yaml` — NOT TOUCHED. The test_topology.yaml row is a required manifest companion per phase.json; no other architecture files are modified.
- `src/state/lifecycle_manager.py` — NOT TOUCHED.
- `src/state/chain_reconciliation.py` — NOT TOUCHED.
- `src/control/**` — NOT TOUCHED.
- `src/supervisor_api/**` — NOT TOUCHED.
- No schema migration that alters existing rows.
- No DB row writes.
- `tests/test_settlement_commands.py` — READ-ONLY (existing file; must remain unmodified; it is not in in_scope per scope.yaml note: "add a NEW test file").

---

## 7 Defaults If Coordinator Does Not Override

**T1A-Q-1 (Topology admission):** Default = STOP. Executor runs `python3 scripts/topology_doctor.py --navigation --task "Zeus May3 R5 T1A single-source DDL" --files src/execution/settlement_commands.py src/state/db.py tests/test_settlement_commands_schema.py architecture/test_topology.yaml --intent "single source of truth for settlement_commands DDL" --write-intent edit --operation-stage edit --side-effect repo_edit` at GO_BATCH_1. If result is `navigation ok: False` or `admission_status: ambiguous`, executor stops and sends STOP_REPLAN to coordinator. Does not proceed without explicit coordinator resolution of Amendment 4.

**T1A-Q-2 (Circular import guard):** Default = function-scope import of `SETTLEMENT_COMMAND_SCHEMA` string constant only, via `from src.execution.settlement_commands import SETTLEMENT_COMMAND_SCHEMA` inside `init_schema()`. This is the smallest safe change: it imports only the string, not the whole module's side effects... except that Python module imports always execute module-level code. If `src.execution.settlement_commands`'s module-level imports (collateral_ledger, CutoverGuard, FXClassification) create a cycle back to db.py, the function-scope `import` will still fail. Executor will verify the import chain before editing and escalate to coordinator if a cycle is found.

**T1A-Q-3 (T0 operator blockers):** Default = STOP. Executor checks `T0_PROTOCOL_ACK.md` at GO_BATCH_1. If `Decision:` line is not `proceed_to_T1`, executor does not proceed. This is a non-negotiable hard gate.

---

## 8 Cross-Phase Awareness

**AMD-10 (T1E predecessor dependency on T1A):** T1A and T1E both edit `src/state/db.py`. T1A's change is localized to the block at approximately lines 1395-1437 (the inline `conn.executescript("""CREATE TABLE IF NOT EXISTS settlement_commands ...""")`). T1E's change adds `ZEUS_DB_BUSY_TIMEOUT_MS` env-var reads at the connection constructor sites near lines 40 and 349.

These edit regions are non-overlapping:
- T1A touches: lines ~1395-1437 (end of `init_schema()` function body, schema init block).
- T1E touches: lines ~40 (module-level config read or `get_connection()` helper) and ~349 (second connection constructor).

To minimize T1E rebase pain, T1A will:
1. Replace ONLY the inline executescript block (lines 1395-1437) with the import + call pattern.
2. NOT touch lines 40, 349, or any connection constructor/timeout surfaces.
3. NOT reformat surrounding code.
4. Keep the change to the smallest possible diff within `init_schema()`.

The comment at line 1395-1396 ("Keep DDL in the schema owner so DB initialization does not import src.execution during startup") will be replaced or updated to reflect the new import pattern. This is a 1-line comment change, not a structural refactor.

T1C's `settlement_commands.py` surface: T1C may add a `SettlementStatus` enum integration to `settlement_commands.py`. T1A does NOT add new functions or enums to `settlement_commands.py` beyond the DDL string and the existing `init_db()` helper (if one needs to be added). T1A's change to `settlement_commands.py` is additive at most (exposing an `init_db(conn)` helper); T1C can add beside it without conflict.

**T1A's edit to db.py is strictly inside the body of `init_schema()` at the settlement_commands DDL block — the smallest possible region, minimizing T1E rebase surface.**
