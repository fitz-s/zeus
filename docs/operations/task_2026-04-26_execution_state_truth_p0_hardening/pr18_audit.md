# PR #18 Audit — Refresh execution-state truth operations package

Created: 2026-04-26
Last reused/audited: 2026-04-26
Authority basis: PR #18 (`copilot/task-model-implementation-plan` @ 76a2f42), main HEAD 7507a21, AGENTS.md authority order.

PR base: `midstream_remediation` @ a72e320. PR head: `copilot/task-model-implementation-plan` @ 76a2f42. 7 files changed, +486/-625.

## 1. Claim verification table

Every PR claim was grep-checked against current `main` HEAD source. All citations below are absolute paths in the worktree `zeus-pr18-fix-plan-20260426`.

| # | PR claim | Status | Ground-truth citation |
|---|----------|--------|-----------------------|
| C1 | DB-before-JSON export is fixed (`commit_then_export`) | VERIFIED | [src/engine/cycle_runner.py:25](src/engine/cycle_runner.py:25), [src/engine/cycle_runner.py:426](src/engine/cycle_runner.py:426) |
| C2 | FDR family split exists (`make_hypothesis_family_id`, `make_edge_family_id`) | VERIFIED | [src/strategy/selection_family.py:29](src/strategy/selection_family.py:29), [src/strategy/selection_family.py:56](src/strategy/selection_family.py:56) |
| C3 | Degraded loader keeps monitor/exit/reconciliation alive while suppressing entries | VERIFIED | [src/engine/cycle_runner.py:1226](src/engine/cycle_runner.py:1226), [src/engine/cycle_runner.py:1271](src/engine/cycle_runner.py:1271) |
| C4 | RED is no longer entry-block-only — sweeps active positions with `red_force_exit` | VERIFIED | [src/engine/cycle_runner.py:51](src/engine/cycle_runner.py:51), [src/engine/cycle_runner.py:97](src/engine/cycle_runner.py:97), [src/engine/cycle_runner.py:299](src/engine/cycle_runner.py:299) |
| C5 | `_TRUTH_AUTHORITY_MAP["degraded"] = "VERIFIED"` (still wrong) | VERIFIED OPEN | [src/state/portfolio.py:59](src/state/portfolio.py:59) |
| C6 | No durable `venue_commands` / `venue_command_events` schema or repo | VERIFIED OPEN | absent in `src/state/`, no migration in `src/state/db.py` |
| C7 | Order side effect happens before durable command authority — `execute_intent` runs before `materialize_position` | VERIFIED OPEN | [src/engine/cycle_runtime.py:1285](src/engine/cycle_runtime.py:1285) (execute_intent), [src/engine/cycle_runtime.py:1322](src/engine/cycle_runtime.py:1322) (materialize_position) |
| C8 | Capability labels exceed implementation (`iceberg`/`dynamic_peg`/`liquidity_guard`) | VERIFIED OPEN | [src/execution/executor.py:133](src/execution/executor.py:133), [src/execution/executor.py:134](src/execution/executor.py:134), [src/execution/executor.py:135](src/execution/executor.py:135) |
| C9 | UNKNOWN exists at chain layer but is not command-aware | VERIFIED OPEN | [src/state/chain_state.py:20](src/state/chain_state.py:20), [src/state/chain_reconciliation.py:401](src/state/chain_reconciliation.py:401) |
| C10 | Rescue still writes fabricated `entered_at="unknown_entered_at"` | VERIFIED OPEN | [src/state/chain_reconciliation.py:481](src/state/chain_reconciliation.py:481), [src/state/chain_reconciliation.py:612](src/state/chain_reconciliation.py:612) |
| C11 | `_live_order` calls `place_limit_order` directly (no command boundary) | VERIFIED OPEN | [src/execution/executor.py:291](src/execution/executor.py:291), [src/execution/executor.py:422](src/execution/executor.py:422), [src/data/polymarket_client.py:131](src/data/polymarket_client.py:131) |
| C12 | No CLOB V2 preflight in `PolymarketClient` | VERIFIED OPEN | grep V2/v2/preflight/cutover in [src/data/polymarket_client.py](src/data/polymarket_client.py) returned 0 hits |
| C13 | RED sweep is local exit marking, no durable cancel/derisk/exit commands | VERIFIED OPEN | [src/engine/cycle_runner.py:97](src/engine/cycle_runner.py:97) sets `pos.exit_reason="red_force_exit"`; no command emission downstream |

**Net**: 4 of the PR's "remove from active indictment" claims hold, and 9 of the PR's "still unresolved" claims hold. PR's diagnosis is structurally accurate.

## 2. PR doc-level gaps to reconcile before merge

These are issues *with the PR docs themselves* that should be fixed before the PR can drive a P0 implementation packet. None of them invalidate the PR; all are bounded edits.

### G1. Stale gateway/command wording (low)
PR `task_packet.md` says "direct live placement outside the approved boundary is statically guarded" as a P0 deliverable, but the PR's `implementation_plan.md` Section 3 ("In scope" item 5) phrases it as adding the static guard. **Inconsistent**: one reads it as a precondition, the other as a deliverable. Reword to "add a static guard that only the gateway boundary may call `place_limit_order`."

### G2. Authority order conflict with `current_state.md` (medium)
On `main`, `docs/operations/current_state.md` declares the active execution packet is `task_2026-04-19_execution_state_truth_upgrade/implementation_plan.md` and freezes new implementation slices. PR #18 lands on `midstream_remediation`. Until merged, the active packet wording in `docs/operations/AGENTS.md` (PR head version) cannot be promoted on `main` without an operator freeze. **Fix**: the PR doc edit should be merged independent of any P0 code packet; do not bundle code changes.

### G3. Receipt's `tests_evidence` is generic (low)
PR `receipt.json` lists topology_doctor commands but does not bind them to the planning packet's actual files. **Fix**: add explicit invocation arguments listing the 7 changed paths.

### G4. CLOB V2 cutover date evidence is ungated (medium)
PR text states "2026-04-28 ~11:00 UTC" but cites only "current public Polymarket migration documentation." For a hard-coded cutover date driving a runtime preflight gate, citation must be a URL + retrieval timestamp + version string. **Fix**: either capture the source URL and access time in `work_log.md`, or treat the date as authoritative-pending and have the V2 preflight gate accept the date as configuration, not literal.

### G5. P0 acceptance proofs do not name target tests (medium)
P0 says "targeted unit test + operator payload snapshot" for degraded export but does not name the test file. Existing surfaces are `tests/test_phase5a_truth_authority.py`, `tests/test_live_execution.py`, `tests/test_executor_typed_boundary.py`. **Fix**: either declare new test files (`tests/test_p0_hardening.py`) or extend named existing files; either choice should be locked before coding starts (Fitz Constraint: "tests are antibodies; types/tests are the only ~100% surviving artifacts").

### G6. P0 step "Mark branch posture as `NO_NEW_ENTRIES`" lacks mechanism (medium)
Where is the posture flag stored? `current_state.md`? A new `state/branch_posture.json`? An env var? An invariant constant? Without mechanism, this is decorative. **Fix**: lock the mechanism (recommendation: `architecture/runtime_posture.yaml` + a runtime-enforced gate in `cycle_runner` entry-decision path).

### G7. Missing INV/NC anchors for the new guards (medium)
PR Section 3 says "AST/static-rule surface used by current CI/topology checks" but does not name which `INV-##` or `NC-##` will own each new guard. Confirmed absent on main: no INV or NC currently covers "no direct `place_limit_order` outside gateway" or "degraded export must not say VERIFIED." **Fix**: pre-allocate INV and NC ids and write the law text *before* the AST rules, otherwise the rules will land without authority basis (violates Zeus authority order).

### G8. Missing "Last reused/audited" headers on new files (low)
PR's new files (`task_packet.md`, `work_log.md`) do not carry the global mandatory provenance header (`Created` / `Last reused/audited` / `Authority basis`). **Fix**: add the three-line header to all three new files.

## 3. Non-blockers (informational only)

- PR has +486/-625 — strong net deletion driven by stale-claim removal and template normalization. This is correct direction and does not need rebalancing.
- PR's "command states" (`INTENT_CREATED`/`SUBMITTING`/`ACKED`/...) and "command events" lists are first-pass grammar drafts; they belong in P1 architecture, not P0. Keep.
- PR's "Required monitoring counters" list (Section 7) is forward-looking and does not need to land in P0.
