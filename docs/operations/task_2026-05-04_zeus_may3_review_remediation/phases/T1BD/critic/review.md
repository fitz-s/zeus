# T1BD Critic Review (10-ATTACK)

Reviewer: critic (subagent `a03538fb0b5f999ed`), 2026-05-05.
HEAD at review: `0468a9ad` (T1F closeout commit). T1BD changes in worktree, not yet committed.
Phase contract: `phases/T1BD/phase.json` (5 asserted invariants, paired-commit constraint).
Phase scope: `phases/T1BD/scope.yaml` (K0/K1 STATE TRUTH per RUNBOOK §12).

## Phase context

- Single paired batch (T1B + T1D landed together per `T1BD-PAIRED-COMMIT`). Worktree contains chain-reconciliation freeze + projection/loader counters + 2 new tests + topology in one logical change set; ready for git-master to stage as one commit.
- Tracked diff: `architecture/test_topology.yaml` (+36/-0), `src/state/chain_reconciliation.py` (+34/-9), `src/state/portfolio.py` (+47/-5). Untracked: `tests/test_chain_reconciliation_corrected_guard.py`, `tests/test_position_projection_d6_counters.py` (in_scope, expected at critic stage).
- 2 substantive deviations from executor: QUARANTINE-guard-not-applicable, COMMIT-BACK passthrough — see verdicts below.
- Independent reproduction: `python -m pytest -q tests/test_chain_reconciliation_corrected_guard.py tests/test_position_projection_d6_counters.py` returns `18 passed in 0.13s`. Matches phase-test commands in `phase.json`.
- Cross-phase invariants intact: T1A-DDL-SINGLE-SOURCE (1 source match, settlement_commands.py:28); T1F surface unchanged (no diff in `src/state/db.py` or `src/venue/polymarket_v2_adapter.py`); T0-D6-FIELD-LOCK preserved.

## Deviation verdicts

### Deviation 1 — QUARANTINE

**verdict: APPROVE**

Evidence:
- `src/state/chain_reconciliation.py:692-724` — the QUARANTINE branch is a single `Position(...)` constructor call with positional/keyword arguments setting `size_usd=0.0` (line 708), `entry_price=0.0` (line 709), `cost_basis_usd=chain.cost or (chain.size * chain.avg_price)` (line 717), `shares=chain.size` (line 718). These are CONSTRUCTOR KWARGS, not field assignments on an existing position. The `corrected_executable_economics_eligible` kwarg is NOT passed, so the dataclass default `False` (`src/state/portfolio.py:286`) applies.
- The QUARANTINE branch is reached at line 686 `if tid not in local_tokens` — i.e. for chain tokens that are NOT in the local portfolio. By definition such tokens have never been touched by `src/execution/fill_tracker.py:452` (the only set-true site for `corrected_executable_economics_eligible`). The eligibility flag CANNOT be True for a position constructed in the QUARANTINE branch.
- Adding a guard predicate at lines 708/709/717/718 would be tautologically dead code: `if not False: assign 0.0` is identical to `assign 0.0`. The invariant text at phase.json line 31 says "cannot mutate entry_price, cost_basis_usd, size_usd, or shares" — when the position is freshly constructed (no prior value to overwrite), there is no "mutation" to block; the eligibility predicate is materializable on existing positions only (T0_D6_FIELD_LOCK §4 confirms).
- Convention check (`grep` on Zeus): the project's other guard sites (e.g. `polymarket_client.py:407`) operate on inherited objects, not freshly-constructed ones; "mutate" in this codebase consistently means "overwrite an existing field on an inherited object," not "set during construction." The phase.json invariant text aligns with this convention.
- Caveat (LOW): the 5-branch claim in phase.json `_planner_notes:f5_count` includes QUARANTINE as one of the 5 branches needing guard coverage, but the underlying invariant is a no-mutation invariant which the QUARANTINE branch satisfies vacuously by construction. The executor's deviation is principled, not lazy. T0_D6_FIELD_LOCK.md §2 acknowledges QUARANTINE assignments are "zeros — still a mutation" but that's a write to a fresh object, not a mutation of an existing eligible position. APPROVE without test addition; the structural argument is sufficient.

### Deviation 2 — commit-back passthrough

**verdict: APPROVE**

Evidence:
- RESCUE branch: `rescued = replace(pos)` at line 523 creates a fresh dataclass copy preserving all of `pos.*` fields. The source-assignment block at lines 531-547 is the ONLY write path to `rescued.entry_price` / `rescued.cost_basis_usd` / `rescued.size_usd` / `rescued.shares`, and is now guarded — when `_rescue_eligible=True`, those four fields retain `pos.*` values exactly. Commit-back at lines 585-588 (`pos.entry_price = rescued.entry_price` etc.) is then a tautological self-assign; no T1BD invariant violation possible.
- SIZE-MISMATCH branch: same pattern. `corrected = replace(pos)` at line 626. Source-assignment block at lines 632-664 is the only write path to `corrected.entry_price` / `corrected.cost_basis_usd` / `corrected.size_usd` / `corrected.shares`. All four are guarded. Commit-back at lines 674-677 is tautological-safe.
- Audit of intermediate callees: `_append_canonical_rescue_if_available` (line 257) reads `position.trade_id` and writes canonical events to DB — does NOT mutate D6 fields on the `Position`. `_sync_reconciled_trade_lifecycle` (line 414) calls `update_trade_lifecycle(conn, position)` which is read-only on the position. `_emit_rescue_event` (line 344) builds an event from position fields — read-only. `_append_canonical_size_correction_if_available` (line 280) reads `position.trade_id` to query `position_current.phase` and conditionally writes canonical size-correction events — read-only on the `Position` D6 fields.
- Adding commit-back guards would emit DUPLICATE counter increments per blocked attempt (8 emits per RESCUE iteration instead of 4, 8-9 per SIZE-MISMATCH iteration instead of 4-5), corrupting the `cost_basis_chain_mutation_blocked_total` cardinality and violating phase.json invariant text "Each blocked attempt increments cost_basis_chain_mutation_blocked_total{field}" (singular per attempt). The executor's deviation is a counter-correctness concern, not just a code-economy one.

## Attack table

| # | Attack | Verdict | Evidence (file:line) |
|---|--------|---------|---------------------|
| 1 | Cite-rot | PASS | All 20 planner-cited mutation lines verified post-edit. Lines have shifted +0 to +12 due to inserted guard wrappers, but each cited mutation is identifiable: `chain_reconciliation.py:533` rescued.entry_price (was 531), :538-539 cost_basis_usd/size_usd (was 533-534), :545 rescued.shares (was 536), :585-588 RESCUE commit-back (was 574-577), :634 corrected.entry_price (was 621), :639-640 cost_basis_usd/size_usd (was 623-624), :647 corrected.shares (was 627), :662 size-mismatch-unresolved corrected.shares = local_shares (was 639), :674-677 SIZE-MISMATCH commit-back (was 649-652), :708-709 QUARANTINE size_usd/entry_price=0.0 (was 683-684), :717-718 cost_basis_usd/shares (was 692-693). T0_D6_FIELD_LOCK.md table cross-checks all 20. Zero drift. |
| 2 | Deviation 1 verdict (QUARANTINE) | APPROVE | See Deviation 1 section above. Constructor-kwarg discipline + unreachability of eligible=True at construction time. |
| 3 | Deviation 2 verdict (commit-back) | APPROVE | See Deviation 2 section above. Sole write-path discipline + counter-cardinality correctness. |
| 4 | Eligible=True path coverage | PASS | `grep -c 'cost_basis_chain_mutation_blocked_total' src/state/chain_reconciliation.py` = 9 emits (lines 535, 541, 542, 547, 636, 642, 643, 649, 664). Decomposition: RESCUE branch (4 fields × 1 branch = 4 emits at 535/541/542/547) + SIZE-MISMATCH primary (4 fields × 1 branch = 4 emits at 636/642/643/649) + SIZE-MISMATCH-UNRESOLVED secondary `corrected.shares = local_shares` write (1 additional emit at 664 for shares-only). Total 9, which matches phase.json `_planner_notes:f5_count` 20 mutation lines collapsed onto unique (branch, field) pairs minus the 8 commit-backs (tautological-safe per Deviation 2) and minus the 4 QUARANTINE constructor sites (vacuous per Deviation 1): 20 − 8 − 4 + 1 (size-mismatch-unresolved shares double-write) = 9. Coverage is COMPLETE for live mutation paths. |
| 5 | Eligible=False path preserves legacy | PASS | Walked 3 sample lines: (a) line 531 `if chain.avg_price > 0:` then 532 `if not _rescue_eligible: rescued.entry_price = chain.avg_price` — when eligible=False the assignment is identical to the original line 531 unguarded form; (b) line 632/634 corrected.entry_price = chain.avg_price gated identically; (c) line 644 `if abs(chain.size - local_shares) > 0.01:` SIZE MISMATCH log preserved unchanged at line 645, then 646 `if not _size_mismatch_eligible: corrected.shares = chain.size` — legacy path identical to the pre-T1BD form. Independent test `tests/test_chain_reconciliation_corrected_guard.py` parametrizes over `(branch, eligible_flag)` and asserts `T1BD-LEGACY-CHAIN-MUTATION-UNCHANGED`; passes. |
| 6 | Counter event-name discipline | PASS | Three exact counter event names: `cost_basis_chain_mutation_blocked_total` (9 emits in chain_reconciliation.py), `position_projection_field_dropped_total` (1 emit in portfolio.py:1707), `position_loader_field_defaulted_total` (1 emit in portfolio.py:1177). All emits use `field=...` label (chain_reconciliation.py uses literal field names in the format string e.g. `field=entry_price`; portfolio.py uses `field=%s` with field_name parameter). `grep 'import.*counter\|from.*counter' src/state/chain_reconciliation.py src/state/portfolio.py` returns zero matches: NO new counter module introduced. All emits use `logger.warning("telemetry_counter event=...")` consistent with T1F C-1 pattern. |
| 7 | chain_shares NOT touched | PASS | `grep chain_shares` results: chain_reconciliation.py lines 527, 582, 628, 671, 720 — all UNCHANGED in diff (the diff hunks are around lines 530-547 and 631-665; lines 527/582/628/671/720 are outside the hunk regions and identical pre/post-T1BD). portfolio.py: chain_shares at 327 (dataclass field), 1270 (loader read), 1300 (rescue write) — also outside diff hunks (which target 1161-1186 and 1693-1714). Diagnostic-metadata field correctly preserved as locked from the freeze. |
| 8 | Portfolio helper minimalism | PASS_WITH_NOTE | Three helpers added: `_D6_LOCKED_FIELDS` frozenset (1 line, line 1164), `_load_d6_field` (15 lines, 1167-1180), `_project_d6_field` (16 lines, 1696-1712). phase.json line 17 says `"introduces_abstraction": false`. The three helpers ARE arguably new abstractions, but they (a) are module-private (underscore prefix), (b) directly serve the two T1BD invariants `T1BD-PROJECTION-DROP-COUNTER` and `T1BD-LOADER-DEFAULT-COUNTER`, (c) replace 4 inline `float(row.get(...) or 0.0)` patterns at lines 1207-1210 with semantically-equivalent function calls, and (d) the projection write at line 1736-1739 is the only consumer of `_project_d6_field`. Inlining would duplicate the counter-emit logic 8 times (4 fields × 2 paths). The helpers are a borderline `introduces_abstraction:true` violation but justified by counter-emit DRY; severity LOW. Note: phase.json `loc_delta_estimate: 480` already implies non-trivial structure. APPROVE the helpers; flag as Caveat C-1. |
| 9 | Out-of-scope discipline | PASS | `git diff --stat` shows exactly 5 files: 3 modified (`architecture/test_topology.yaml`, `src/state/chain_reconciliation.py`, `src/state/portfolio.py`) + 2 untracked test files (`tests/test_chain_reconciliation_corrected_guard.py`, `tests/test_position_projection_d6_counters.py`). `src/engine/lifecycle_events.py` is in phase.json `files_touched` but UNCHANGED (`grep` shows D6 field references at lines 82-85, 140-142, 348-349 — all are read-only `getattr(position, field, default)` patterns, no assignments to the four locked fields). Other untracked entries (`.claude/orchestrator/`, `.zeus/`, `docs/.../phases/T1C..T1H/`) are coordinator-owned packet artifacts predating T1BD dispatch. Zero out-of-scope source/test files touched. C-2: phase.json files_touched lists `lifecycle_events.py` but it's unchanged — same documentation-drift pattern as T1F C-2. |
| 10 | No T1A / T1F regression | PASS | T1A: `git grep 'CREATE TABLE IF NOT EXISTS settlement_commands' src/ scripts/ tests/` = 1 source match (`src/execution/settlement_commands.py:28`) + 1 test-pattern constant. T1A-DDL-SINGLE-SOURCE intact. T1F: `git diff src/state/db.py src/venue/polymarket_v2_adapter.py` returns empty (no diff against committed T1F state at commit `0468a9ad`). T1F-ADAPTER-ASSERTS-LIVE-BOUND-BEFORE-SDK and T1F-COMPAT-SUBMIT-LIMIT-ORDER-REJECTS-OR-FAKE both intact. Cross-phase consumed_invariants ledger preserved. |

## Caveats

- **C-1 (LOW)**: Three helpers (`_D6_LOCKED_FIELDS`, `_load_d6_field`, `_project_d6_field`) added to portfolio.py. phase.json line 17 declares `"introduces_abstraction": false`. The helpers are borderline new abstractions but justified by counter-emit DRY (inlining would duplicate the counter-emit logic 8 times across 4 fields × 2 paths). Module-private (underscore prefix) and directly serve the asserted invariants. Severity LOW; not blocking.
- **C-2 (LOW)**: `src/engine/lifecycle_events.py` is in `phase.json::files_touched` but unchanged in the worktree (D6 field references are read-only `getattr` patterns, no assignments to the locked fields). Same documentation-drift pattern as T1F C-2 (`venue_submission_envelope.py`). Not a correctness issue.
- **C-3 (LOW carry-forward from T1F)**: Counter emits use `logger.warning("telemetry_counter event=...")` text-tap pattern; tests assert behavior not emit-text via caplog. Same structural-vs-test-asserted gap as T1F C-1 (deferred to T2F typed sink per T1F closeout). NOT new in T1BD; consistent treatment.

## T1BD Verdict

CRITIC_DONE_T1BD
verdict: APPROVE_WITH_CAVEATS
deviation_1_quarantine_verdict: APPROVE
deviation_2_commitback_verdict: APPROVE
caveats: ["C-1 LOW: 3 helpers added to portfolio.py vs phase.json introduces_abstraction:false; justified by counter-emit DRY", "C-2 LOW: src/engine/lifecycle_events.py listed in phase.json::files_touched but unchanged (D6 references are read-only getattr); documentation drift only", "C-3 LOW (carry-forward from T1F): counter emits use logger.warning text-tap pattern not test-asserted via caplog; same structural-vs-test-asserted gap as T1F C-1, deferred to T2F"]
chain_mutation_branches_guarded: ["RESCUE (lines 530-547, 4 emits)", "SIZE-MISMATCH primary (lines 631-649, 4 emits)", "SIZE-MISMATCH-UNRESOLVED secondary (line 661-664, 1 emit for shares=local_shares)"]
counter_emit_count_in_chain_recon: 9
counter_emit_field_label_count: 9
chain_shares_untouched: yes
abstraction_minimal: yes
no_t1a_regression: yes
no_t1f_regression: yes
ready_for_close: yes
