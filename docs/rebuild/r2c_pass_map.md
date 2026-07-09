# R2-c command_recovery pass map (2026-07-08)

Base: `c4d4d45e3` (combo/wave1-integration). Branch: `rebuild/r2c-recovery-predicate-table`.
Method: read every pass body + docstring; git-verify each blueprint deletion claim; run
each pass's candidate-selection helper READ-ONLY against the live 84GB trades DB
(`/Users/leofitz/zeus/state/zeus_trades.db`, `mode=ro`), and count corrective events each
pass actually appended to `position_events` since 2026-06-17 and since R0-a landed
(2026-07-08T02:36). This is the same empirical bar R0-e (dbfbedd53) used to delete residue
passes ("a live read-only query proves 0 rows match").

## Headline finding (overturns blueprint §7.1)

**No pass in `command_recovery.py` is safe to delete in this packet.** The blueprint tagged
~30 passes as scars obsoleted by landed fixes (R0-a, 8f22bb3de). The live DB falsifies that
for every pass I could measure: they are firing on the money path **today**. R2-c cannot be a
"batch-delete the scars" wave against current state — the passes are load-bearing projection/
terminal repairers, not dead residue. Deletion is gated on TWO things that do not exist yet:
(1) the diff-engine WRITE-migration (its matching predicates are `writes=False` today — report
only), and (2) a replay harness that can prove WRITE-equivalence (replay.py can only compare
report-only classifications; see "Tooling gap" below).

Already done by prior waves (verified in base, not re-done here):
- **R0-a** `53494ef76` — single close-economics funnel (`upsert_position_current` +
  `MissingRealizedPnlOnCloseError`). Ancestor of base. Did NOT obsolete the projection passes
  (see Class-2 below).
- **R0-e** `dbfbedd53` — already deleted `repair_spurious_model_divergence_pending_exits` and
  `repair_structural_win_pending_exits` (+ 3 tests) with 0-rows proof. Ancestor of base. So the
  blueprint's `repair_spurious_model_divergence` SCAR_DELETE_NOW is ALREADY EXECUTED.
- **8f22bb3de** — chain_reconciliation forward fix (`_quarantine_confirmed_chain_absence`).
  Ancestor of base. Reduced but did NOT eliminate the phantom-void condition (see Class-3).

## Live-DB empirical activity (position_events written by src.execution.command_recovery)

| proof_class / event_type | total since 06-17 | post-R0-a | last_seen | current backlog |
|---|---:|---:|---|---:|
| live_entry_command_order_fact_without_position_current | 1436 | 60 | 2026-07-08T21:23 | 0 (transient) |
| ENTRY_ORDER_VOIDED (in-flight/terminal scan) | 638 | 24 | 2026-07-08T21:29 | — |
| filled_entry_command_trade_fact_without_position_current | 78 | 3 | 2026-07-08T10:32 | 0 (transient) |
| _latest_matched_order_fact_candidates backlog | — | — | — | **688** |
| _filled_entry_execution_fact_repair_candidates backlog | — | — | — | **60** |
| confirmed_fill_chain_absence_projection_preserved_current_money_risk | 29 | 0 | 2026-07-04T17:47 | 0 |
| EXIT_ORDER_FILLED | 21 | — | 2026-07-08T21:27 | — |
| confirmed_fill_phantom_void_reclassified_to_review | 20 | 0 | 2026-07-02T19:53 | **3** |
| _filled_entry_lot_materialization_candidates backlog | — | — | — | **6** |

Key: a pass showing **0 backlog now but firing post-R0-a** (live_entry projection: 60 events
today, 0 candidates this instant) proves the condition is transient/recurring — "0 candidates
now" is NOT a deletion signal for these passes (unlike R0-e's one-time historical residue).

## Full pass inventory + verdict

Legend: LB = load-bearing (firing / has backlog / live caller). REAL = real venue behavior
already encoded as a report-only diff_engine predicate; delete only after write-migration.
FLAG = blueprint said delete, evidence says do not. C1/C2/C3 = blueprint scar classes.

| # | pass (file:line) | purpose | class | live caller (non-test) | backlog | verdict |
|---|---|---|---|---|---:|---|
| 1 | `_reconcile_row` :14339 | M2 SUBMIT_UNKNOWN in-flight resolver (core scan) | REAL | (orchestrator) | — | KEEP (core) |
| 2 | reconcile_matched_order_facts :7137 | recover ACKED fill when point truth says matched | REAL cancel_match_race | venue_command_repo.py | 688 | LB — write-migrate first |
| 3 | reconcile_terminal_order_facts :6992 | close ACKED entry on terminal no-fill order fact | REAL ws_unreliable | venue_cancel_journal.py | 0 | LB (fires ENTRY_ORDER_VOIDED) |
| 4 | reconcile_terminal_point_orders :9307 | terminal no-fill when CLOB point truth closes stale ACKED | REAL ws_unreliable_rest | — | 0 | LB (restart+tick) |
| 5 | reconcile_completed_partial_order_facts :7397 | finalize PARTIAL when remainder gone | REAL partial_fill | — | 0 | LB |
| 6 | reconcile_partial_remainders :9531 | terminalize filled remainder when open-order surface empty | REAL partial_fill | (diff_engine docstring ref only) | 0 | LB |
| 7 | reconcile_cancel_ack_terminal_no_fill_facts :8945 | materialize terminal no-fill from acked cancels | REAL cancel_match_race | venue_cancel_journal.py | 0 | LB |
| 8 | reconcile_local_orphan_no_fill_findings :9033 | proven no-fill orphan → terminal fact | REAL | — | 0 | LB (terminal flow) |
| 9 | reconcile_stale_terminal_no_fill_findings :9101 | resolve orphan after terminal no-fill recovery | REAL | — | 0 | LB |
| 10 | reconcile_matched_cancel_review_required_entries :7890 | clear REVIEW_REQUIRED when matched-cancel proves exposure | REAL cancel_match_race | venue_command_repo.py | 0 | LB |
| 11 | reconcile_live_entry_projection_repairs :4335 | materialize position_current for ACKED entry (projection lag) | C2 | — | 0 (transient) | FLAG — 1436 events, 60 post-R0-a TODAY; R0-a did NOT obsolete |
| 12 | reconcile_filled_entry_projection_repairs :4677 | materialize position_current for FILLED entry | C2 | — | 0 (transient) | FLAG — 78 events, 3 post-R0-a |
| 13 | reconcile_terminal_positive_entry_projection_repairs :4763 | FILLED entry projection when terminal order truth outruns trade facts | C2 | — | 0 | FLAG — projection lag, recurring |
| 14 | reconcile_filled_entry_position_link_repairs :5569 | relink filled ENTRY cmd to materialized position | C2 | — | 0 | FLAG — recurring |
| 15 | reconcile_filled_entry_execution_fact_repairs :5912 | repair stale execution_fact when lot truth exists | C2 | test_exchange_reconcile | 60 | LB — backlog 60 now |
| 16 | reconcile_filled_entry_position_lot_repairs :5963 | materialize lots for filled ENTRY | C2 | — | 6 | LB — backlog 6 now |
| 17 | reconcile_hard_terminal_position_projection_repairs :5474 | restore position_current.phase from durable terminal event | C2 (R2-core hole (a)) | — | 0 | FLAG — projection drift, recurring |
| 18 | reconcile_exit_pending_projections :6319 | repair restart-visible exit side effects → pending_exit | C2 | — | 0 | FLAG — restart-scoped, recurring |
| 19 | reconcile_exit_lifecycle_alignment_repairs :7830 | repair EXIT cmd/projection disagreement at restart | C2 | — | 0 | FLAG — restart-scoped |
| 20 | reconcile_invalid_open_entry_authority_reviews :5284 | record invalid EDLI entry authority (no exposure change) | C2/authority | — | 0 | FLAG — recurring authority gate |
| 21 | reconcile_invalid_pending_entry_authority_cancels :5084 | cancel zero-fill pending entry rests w/ invalid authority | authority | test | 0 | KEEP — real authority gate |
| 22 | reconcile_edli_confirmed_legacy_command_repairs :4647 | terminalize legacy cmd when EDLI aggregate has fill proof | C1 EDLI | test | 0 | FLAG — blocked on EDLI dual-ledger removal (NOT landed) |
| 23 | reconcile_edli_entry_posterior_projection_repairs :4862 | backfill EDLI entry authority from Actionable cert | C1/C2 | test | 0 | FLAG — EDLI dual-ledger |
| 24 | _reconcile_venue_command_absence_sync :12273 | discharge venue_commands rows absence-proven by EDLI (#123/M2) | C1 EDLI | — | 0 | FLAG — EDLI dual-ledger |
| 25 | reconcile_edli_acknowledged_venue_command_sync :12448 | mirror ACKED venue_commands → EDLI ledger | C1 EDLI | **edli_absence_resolver.py** | 0 | FLAG — EDLI dual-ledger + live caller |
| 26 | reconcile_edli_rejected_venue_command_sync :12636 | mirror terminal no-fill rejection → EDLI ledger | C1 EDLI | — | 0 | FLAG — EDLI dual-ledger |
| 27 | _reconcile_edli_pre_venue_unknown_thresholds :12856 | EDLI pre-venue unknown threshold resolve | C1 EDLI | — | 0 | FLAG — EDLI dual-ledger |
| 28 | _reconcile_edli_post_submit_unknown_absence :13076 | release EDLI post-submit unknowns after authenticated absence | C1 EDLI | — | 0 | FLAG — EDLI dual-ledger |
| 29 | repair_confirmed_phantom_voids :8117 | recover confirmed fills wrongly voided as phantom | C3 (blueprint: DELETE) | test | **3** | **FLAG — blueprint WRONG: 3 live candidates, fired 2026-07-02 (post 06-20 fix). Forward fix reduced but did not eliminate. Do NOT delete.** |
| 30 | repair_confirmed_chain_absence_positive_projections :8432 | promote/clear positions in the NEW forward-fix chain_state | downstream of 8f22bb3de | test | 0 | KEEP — processes the fix's OUTPUT, not pre-fix residue; 29+2 events last 2026-07-04 |
| 31 | reconcile_abandoned_unsubmitted_ghosts :11526 | terminalize EDLI aggregate abandoned at ExecCommandCreated (unblocks family lock) | REAL (live_order_pathology) | conftest/tests | 0 | KEEP — real ghost/family-lock behavior |
| 32 | reconcile_stale_intent_created_no_submit :11619 | terminalize pre-submit shells never crossing venue boundary | REAL | — | 0 | KEEP — pre-venue cleanup |
| 33 | reconcile_filled_exit_trade_fact_tx_repairs :7711 | repair exit trade-fact tx | C2/exit | test | 0 | FLAG — recurring |
| 34 | release_closed_shift_bin_exit_leases :1082 | release SHIFT_BIN exit leases whose old leg closed | lease lifecycle | test_venue_sync_contract | — | KEEP — real lease lifecycle |
| 35 | release_stale_rebalance_entry_leases :1163 | release stale rebalance entry leases | lease lifecycle | test_venue_sync_contract | — | KEEP — real lease lifecycle |
| 36 | reconcile_restart_no_venue_exit_retry_projections :15502 | project proven no-side-effect EXIT submit failures → retry | restart | — | 0 | KEEP — restart safety |

## Tally

- REAL venue behavior (already encoded as report-only diff_engine predicate; delete after
  write-migration + write-replay): 10 passes (#2-10, plus #1 core resolver stays). 0 deleted.
- C1 EDLI dual-ledger sync: 7 passes (#22-28). 0 deleted — blocked on EDLI dual-ledger removal
  (a separate wave; the EDLI event ledger in zeus-world.db still exists; #25 also has a live
  caller). Blueprint's "7 pass" count matches.
- C2 multi-writer projection drift: ~10 passes (#11-20, #33). 0 deleted — blueprint's "obsoleted
  by R0-a" is EMPIRICALLY FALSE (they fire post-R0-a; two carry live backlogs of 60 and 6).
- C3 sibling-bug residue: blueprint named 2. Both variants (#29 phantom_voids, #30
  chain_absence) are STILL ACTIVE / have live backlog — neither deletable. The two genuine
  residue passes (spurious, structural_win) were already deleted by R0-e.
- KEEP (real, not scar): #21, #30, #31, #32, #34, #35, #36.
- **Deleted this packet: 0. Line delta on command_recovery.py: 0.**

## Tooling gap (blocks the DoD's "identical corrective events" replay proof)

`src/reconcile/replay.py` compares diff-engine FINDINGS against legacy WRITES by position-id
overlap. But every diff_engine predicate that matches a load-bearing pass is `writes=False`
(report-only) except `reservation_orphan`. So a replay shows "legacy pass wrote N events, diff
engine produced report-only findings on the same positions" — it CANNOT certify that the diff
engine reproduces the pass's WRITE (the `UPDATE position_current` / `INSERT position_events`
mutation). The harness's own docstring concedes it is not a byte-exact time-travel replay.
Therefore the acceptance evidence the DoD demands ("old pass and diff engine produce IDENTICAL
corrective events") is NOT CONSTRUCTIBLE for any load-bearing pass with the current tooling.

## What R2-c actually requires next (re-scope)

1. Build the WRITE-migration in diff_engine: give each REAL predicate (cancel_match_race,
   ws_unreliable, partial_fill, terminal-no-fill) a `writes=True` `apply_corrective_event`
   body reproducing the legacy pass's exact mutation — one predicate per batch, PREPARE-level
   review.
2. Upgrade replay.py to point-in-time WRITE replay (rebuild projections event-by-event) so
   write-equivalence can be certified; only then is any REAL pass deletable.
3. C1 EDLI passes: gated on removing the EDLI dual-ledger (zeus-world.db event lane) — a
   separate wave; until then these 7 are load-bearing sync.
4. C2 projection passes: they repair position_current materialization lag, a DIFFERENT concern
   than close-economics — R0-a does not obsolete them. Deletion requires the single-writer
   projection funnel (R5), not R0-a.
5. phantom_voids (#29): investigate why the 8f22bb3de forward fix still leaves 3 live
   candidates + fired 2026-07-02 (either the fix has a gap or there is a slow backlog); do NOT
   delete until the forward condition is proven fully closed AND backlog drained.
