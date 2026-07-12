# Census — local-ledger↔chain/venue drift-fighting machinery

Date: 2026-07-12. Base: HEAD `3682397ae` (post T5-migration + bridge retirement).
Method: read-only. Line numbers refreshed against HEAD (not the r2c base `c4d4d45e3`).
Seed inputs: `docs/rebuild/r2c_pass_map.md`, `docs/rebuild/quarantine_excision_2026-07-11.md`.

## Framing

The controlling law is `src/state/chain_reconciliation.py:1` — **"Chain is truth. Portfolio
is cache. Chain > Chronicler > Portfolio. Always."** Every unit below exists because Zeus
keeps a **local parallel ledger** — `position_current` (the "cache"), `venue_commands` (local
command-state machine), `position_lots`, `execution_fact`, the `realized_pnl/exit/settlement`
booked columns, the EDLI event lane in `zeus-world.db`, and `settlements.authority` — and then
has to continuously drag that cache back into agreement with chain/venue truth (on-chain
balances, venue order/trade facts, settlement oracles).

Under the operator-ordered **local bookkeeping excision** (local parallel ledgers die; chain
facts become the sole authority for chain-knowable content), the drift machinery loses its
target. The census maps each unit to the specific ledger death that removes its reason to exist,
and flags which units are the **chain-sync spine itself** (they survive: they read chain/venue
truth, they do not police a local copy of it).

### The five ledger-death conditions (referenced as D1–D5 below)

- **D1 — command-state ledger dies**: `venue_commands` stops being an authoritative local
  state machine; command outcome is read from venue order/trade facts directly.
- **D2 — position_current projection-cache dies**: `position_current`/`position_lots` stop
  being a durably-materialized parallel copy of exposure; exposure is projected on read from
  events + chain, or the cache becomes a single-writer read-through.
- **D3 — EDLI dual-ledger dies**: the `zeus-world.db` EDLI event lane is removed; venue command
  facts have one home.
- **D4 — booked close-economics columns die**: `realized_pnl_usd`/`exit_price`/`settlement_price`/
  `settled_at`/`cost_basis_usd` stop being locally-booked; P&L and cost are derived from
  chain fills + settlement oracle on demand.
- **D5 — settlement local-authority dies**: `settlements.authority` (+ the `DISPUTED`/harvester
  writer pair) stops being a local tier; settlement is the oracle read.

---

## Ranked census (by line weight)

Legend for KEEP-candidate: **SPINE** = becomes/already-is the chain-sync read layer, survives;
**DIES** = pure drift-repair against a local ledger, no survival role; **MIXED** = one unit doing
both (needs splitting, not deleting).

| # | unit (file:line) | drift it fights (local ledger ↔ truth) | weight (lines) | dies-when | KEEP? |
|---|---|---|---:|---|---|
| 1 | `src/execution/command_recovery.py` (whole; 42 reconcile/repair defs) | `venue_commands` state + `position_current`/`position_lots`/`execution_fact` projection ↔ venue order/trade facts + on-chain absence | 16549 (drift mass ~13k) | D1+D2+D3+D4 (per sub-family below) | MIXED — see breakdown |
| 2 | `src/execution/exchange_reconcile.py` (M5 sweep; ~130 defs) | `position_current` + `collateral_reservations` ↔ exchange positions/balances/open-orders/maker-fill economics | 6060 | D2 (materializers) / SPINE (exchange-fact reads + collateral identity) | MIXED |
| 3 | `scripts/check_live_restart_preflight.py` | restart-time: local `position_current`/`venue_commands`/reservations ↔ chain before ARM | 5913 | D1+D2 (its whole premise is "is the cache trustworthy after restart") | MIXED (center of live branch `p2-pending-exit-restart-redecision`) |
| 4 | `src/state/chain_reconciliation.py` (3-rule reconciler + ~40 nested appenders) | `position_current` cache ↔ on-chain positions (rescue / size-correction / absence-void / ChainOnlyFact) | 2392 | D2 (write-back appenders die) / SPINE (classify + ChainOnlyFact emission) | MIXED |
| 5 | `src/execution/fill_tracker.py` | optimistic local position ↔ venue-confirmed fill (pending_tracked→entered\|voided) | 2061 | D2 (it MINTS the cache) — but also a write path | MIXED (mints + reconciles) |
| 6 | `src/state/chain_mirror_reconciler.py` (`classify_local_position` :244, `reconcile` :1107, `apply_size_correction_finding` :901) | on-chain balance ↔ `position_current.shares`; absent-vs-unobserved 2-run force-resolve | 1312 | SPINE (this is the target-form kernel) — `apply_size_correction_finding` write-side reshapes under D2 | **SPINE** (KEEP) |
| 7 | `scripts/drain_settlement_disputes.py` | `settlements.authority='DISPUTED'` rows ↔ oracle re-resolution | 707 | D5 | DIES (fold into harvester cycle) |
| 8 | `src/state/projection.py` (guards subset: `MissingRealizedPnlOnCloseError` :212, `_preserve_existing_*` :334-512, `_projection_allows_terminal_restore_exposure` :533) | booked P&L column presence; multi-writer clobber of `chain_seen_at`/`chain_shares`/monitor snapshot | 666 (guard subset ~330) | `MissingRealizedPnl`→D4; `_preserve_*`→D2 | MIXED (F109/NullConditionId are local-integrity, NOT drift — KEEP) |
| 9 | `src/state/ledger.py::backfill_fill_authority` (:462) + authority-column ensures | `position_current.fill_authority` ↔ venue trade facts | 651 (backfill ~120) | D2/D4 (fill_authority column) | DIES (backfill) |
| 10 | `scripts/repair_review_required_no_venue_exposure.py` | review-required local rows ↔ proven no venue exposure | 509 | D1+D2 | DIES |
| 11 | `scripts/backfill_settlements_via_gamma_2026.py` | local `settlements` ↔ Gamma settlement truth | 395 | D5 | DIES |
| 12 | `scripts/rebuild_settlements.py` | local settlement rows ↔ oracle | 336 | D5 | DIES |
| 13 | `scripts/repair_dust_exit_projection.py` | `position_current` exit projection ↔ chain dust residual | 315 | D2 | DIES |
| 14 | `scripts/run_redeem_reconcile_with_onchain_proof.py` | local redeem/close ↔ on-chain redemption proof | 314 | D2/D5 | DIES (redeem is third-party per memory) |
| 15 | `scripts/repair_terminal_order_fact_sequence.py` | `venue_order_facts` local sequence ↔ venue truth | 249 | D1 | DIES |
| 16 | `scripts/repair_review_required_matched_order_fact.py` | matched-order review rows ↔ venue fill | 238 | D1 | DIES |
| 17 | `scripts/backfill_close_economics.py` | `position_current` booked P&L ↔ recomputed close economics | 237 | D4 | DIES |
| 18 | `src/reconcile/diff_engine.py` (classify + 5 predicates + `apply_corrective_event`) | `local_truth` (cmd+position+reservation) ↔ `chain_truth` (order/trade/settlement facts) | 542 | D1+D2 (4 report-only predicates); reservation-orphan writer→SPINE | MIXED (INERT today) |
| 19 | `scripts/reconcile_wellington_zombie_2026_06_22.py` | one-shot zombie position ↔ chain | 196 | D2 | DIES (one-shot, historical) |
| 20 | `scripts/backfill_harvester_settlements.py` | local settlements ↔ harvester | 194 | D5 | DIES |
| 21 | `src/state/chain_truth.py` (`ChainTruthSnapshot`, `load_chain_truth_snapshot`) | (none — this IS chain truth) | 220 | never | **SPINE** (KEEP) |
| 22 | `src/reconcile/local_truth.py` (`LocalTruthSnapshot`, `load_local_truth_snapshot`) | snapshots the local ledger for comparison | 227 | D1+D2 (its purpose is snapshotting the cache) | DIES |
| 23 | `src/reconcile/replay.py` (migration acceptance harness) | compares diff_engine findings ↔ legacy pass writes | 226 | dies when the legacy passes it validates die | DIES (migration tool) |
| 24 | `src/state/chain_state.py::classify_chain_state` (:75) | `Position.chain_verified_at` freshness ↔ empty chain snapshot (EMPTY vs UNKNOWN) | 143 | SPINE (chain-absence confidence contract) — reads local mirror-warmth timestamp | MIXED |
| 25 | `scripts/reconcile_realized_fees.py` | local `realized_fees` ↔ venue fee truth | 138 | D4 | DIES |
| 26 | `scripts/reconcile_chain_mirror.py` | `position_current` ↔ chain mirror (CLI driver of the spine) | 99 | SPINE-driver (re-points to spine) | MIXED |
| 27 | `src/state/fill_dedup.py::canonical_trade_fact_cte` (:37) + `_economic_trade_fact_cte` (economic-identity reducer, `exchange_reconcile.py:215` / `command_recovery.py`) | de-duplicates the venue observation log (`venue_trade_facts` re-delivers same fill 1x-4x; tx_hash alias vs child trade IDs) | ~90 + 3 dup copies | never (inherent to READING venue truth) | **SPINE** (KEEP) — consolidate the 3-4 copies |
| 27b | `scripts/audit_realtime_pnl.py` | booked P&L ↔ recomputed | 83 | D4 | DIES (read-only audit) |

---

## `command_recovery.py` sub-family breakdown (row #1 detail)

All def lines HEAD-accurate. Note `_void_absorbed_chain_only_projection` was deleted in the
bridge-retirement packet (`c966f7fe2`); the two chain-truth repairs below were **re-pointed** to
write true-phase + `ReviewWorkItem` (not deleted).

| sub-family | passes (file:line) | ledger ↔ truth | dies-when |
|---|---|---|---|
| **C2 projection materialization** (position_current lag) | `reconcile_live_entry_projection_repairs` :4306, `reconcile_filled_entry_projection_repairs` :4648, `reconcile_terminal_positive_entry_projection_repairs` :4734, `reconcile_hard_terminal_position_projection_repairs` :5472, `reconcile_filled_entry_position_link_repairs` :5567, `reconcile_filled_entry_execution_fact_repairs` :5910, `reconcile_filled_entry_position_lot_repairs` :5961, `reconcile_exit_pending_projections` :6320, `reconcile_exit_lifecycle_alignment_repairs` :7836, `reconcile_filled_exit_trade_fact_tx_repairs` :7714 | `position_current`/`position_lots`/`execution_fact` ↔ position_events + command truth | **D2** (single-writer projection funnel / cache death) — R0-a did NOT obsolete (r2c: fire post-R0-a) |
| **REAL venue-behavior resolvers** (already modeled as diff_engine predicates) | `reconcile_terminal_order_facts` :6993, `reconcile_matched_order_facts` :7138, `reconcile_completed_partial_order_facts` :7402, `reconcile_matched_cancel_review_required_entries` :7896, `reconcile_cancel_ack_terminal_no_fill_facts` :9055, `reconcile_local_orphan_no_fill_findings` :9143, `reconcile_stale_terminal_no_fill_findings` :9211, `reconcile_terminal_point_orders` :9417, `reconcile_partial_remainders` :9641, `_reconcile_row` :14470 (M2 core) | `venue_commands` state ↔ venue order/trade facts (cancel-race, ws-unreliable, partial-fill, terminal-no-fill) | **D1** — the venue-truth logic is SPINE (migrate into diff_engine writes, R2-c); the local-command-state target dies |
| **C1 EDLI dual-ledger sync** | `reconcile_edli_confirmed_legacy_command_repairs` :4618, `reconcile_edli_entry_posterior_projection_repairs` :4833, `_reconcile_edli_pending_no_order_if_proven` :12017, `_reconcile_venue_command_absence_sync` :12388, `reconcile_edli_acknowledged_venue_command_sync` :12563, `reconcile_edli_rejected_venue_command_sync` :12751, `_reconcile_edli_pre_venue_unknown_thresholds` :12971, `_reconcile_edli_post_submit_unknown_absence` :13191 | `venue_commands` ↔ EDLI event ledger (`zeus-world.db`) | **D3** (EDLI dual-ledger removal) |
| **chain-truth review promotions** | `repair_confirmed_phantom_voids` :8172, `repair_confirmed_chain_absence_positive_projections` :8490 | local phase ↔ chain fill/absence truth (write true-phase + ReviewWorkItem post-T5) | **D2** — become direct chain reads |
| **KEEP (not local↔chain drift)** — authority gates / lease lifecycle / pre-venue / restart | `reconcile_invalid_pending_entry_authority_cancels` :5091, `reconcile_invalid_open_entry_authority_reviews` :5282, `reconcile_abandoned_unsubmitted_ghosts` :11641, `reconcile_stale_intent_created_no_submit` :11734, `release_closed_shift_bin_exit_leases` :1112, `release_stale_rebalance_entry_leases` :1193, `reconcile_restart_no_venue_exit_retry_projections` :15725 | real authority / lease / pre-venue / restart-safety behavior | survive (flag: NOT drift machinery) |
| orchestration (not passes) | `reconcile_unresolved_commands` :14983, `_reconcile_passes_inline` :15067, `_reconcile_passes_short_conn` :15779 | — | thin with the passes they drive |

---

## `exchange_reconcile.py` sub-family breakdown (row #2 detail)

| sub-family | defs (file:line) | ledger ↔ truth | dies-when |
|---|---|---|---|
| **M5 sweep + drift findings** | `run_reconcile_sweep` :549, `_record_position_drift_findings` :2754, `_resolve_position_drift_tokens_from_current_truth` :3377, `check_collateral_identity` :2578 | `position_current`/`collateral_reservations` ↔ exchange positions/balances | **D2** — except `check_collateral_identity` (chain-collateral identity, SPINE) |
| **projection materializers** (mirror command_recovery C2) | `_missing_entry_projection_from_linked_fill` :4405, `_ensure_entry_fill_position_event` :4523, `_ensure_exit_fill_position_event` :4741, `_apply_entry_fill_projection_and_execution_fact` :4996, `_apply_exit_fill_projection_and_execution_fact` :5053, `_append_entry_position_lots_for_command` :5099, `reconcile_recorded_maker_fill_economics` :1843 | `position_current`/`position_lots`/`execution_fact` ↔ venue fills | **D2/D4** |
| **ghost-order resolvers** | `_recover_live_ghost_sell_order_for_known_position` :1103, `_resolve_disappeared_ghost_order_findings` :3833, ghost-proof A/B/C/D :3736-3805, foreign/operator-ack ghost handlers :831-1053 | local open-order view ↔ exchange open-order surface | **D1** — foreign-wallet/operator-ack handlers are SPINE (co-trading is expected per memory) |
| **operator external-close absorption** | `_absorb_operator_external_close` :1598, `_book_external_operator_close_exit_fact` :1671, `_tag_external_operator_closed_position_holdings` :1791 | local position ↔ operator's out-of-band close | SPINE (shared-wallet co-trading; reads external truth) |
| **fill-dedup / order-truth primitives** | `_canonical_trade_fact_cte` :174, `_economic_trade_fact_cte` :215 | (reads venue truth) | **SPINE** (dup of `fill_dedup.py`) |

---

## Top-3 heaviest dies-when clusters

1. **D1+D2 — local command-state + position_current projection-cache retires as authority.**
   By far the largest mass: `command_recovery.py` C2+REAL families (~7-9k lines), the
   `exchange_reconcile.py` M5 sweep + projection materializers (~4k), `check_live_restart_preflight.py`
   (5913), `chain_reconciliation.py` write-back appenders (~1.5k), `reconcile/local_truth.py` +
   `diff_engine.py` report-only predicates, `fill_tracker.py`, and ~8 `scripts/repair_*`.
   **≈ 24-27k lines.** Dies when `venue_commands`/`position_current`/`position_lots` stop being a
   durable parallel ledger and command/exposure outcomes are read from venue+chain facts directly.

2. **D3 — EDLI dual-ledger (`zeus-world.db` event lane) removal.**
   `command_recovery.py`'s 8 EDLI-sync passes (`reconcile_edli_*` / `_reconcile_edli_*` /
   `_reconcile_venue_command_absence_sync`, ~2.5k lines) plus the EDLI fill/trade bridges.
   Dies when venue command facts have a single home.

3. **D4+D5 — booked close-economics columns + settlement local-authority die.**
   Settlement scripts (`drain_settlement_disputes` 707, `backfill_settlements_via_gamma` 395,
   `rebuild_settlements` 336, `backfill_harvester_settlements` 194) + harvester settlement writers +
   the D4 P&L set (`backfill_close_economics` 237, `reconcile_realized_fees` 138, `audit_realtime_pnl`
   83, `ledger.backfill_fill_authority`, `projection.MissingRealizedPnlOnCloseError`).
   **≈ 2.4k lines.** Dies when P&L/cost/settlement are derived from chain fills + oracle on read.

---

## Chain-sync SPINE — survives the excision (KEEP)

These read chain/venue truth; they do not police a local copy of it. They are the target-form
layer the drift machinery collapses into:

- `src/state/chain_mirror_reconciler.py` — `classify_local_position`, `reconcile`,
  `classify_chain_only_asset`, settlement/size/closed-exited finding appliers (the target kernel;
  its `apply_size_correction_finding` **write-side** reshapes under D2).
- `src/state/chain_truth.py` (`ChainTruthSnapshot`) + `src/reconcile/_order_fact_queries.py`.
- `src/state/fill_dedup.py::canonical_trade_fact_cte` + the `_economic_trade_fact_cte`
  economic-identity reducer (consolidate the 3-4 copies in command_recovery / exchange_reconcile /
  venue_command_repo into this one).
- `src/state/chain_state.py::classify_chain_state` — chain-absence confidence (BLOCKER-3
  `ChainObservationEnvelope` territory); reshapes to stop keying on local `chain_verified_at`.
- `chain_reconciliation.py` rule-3 `ChainOnlyFact` emission + `classify_chain_state` consumption.
- `exchange_reconcile.py::check_collateral_identity` + operator-external-close absorption +
  foreign/operator-ack ghost handling (co-trading is expected, not drift).

## Mixed-concern units — split, do not delete

- `projection.py` — DELETE the D4 P&L guard + D2 `_preserve_existing_*` clobber-guards; **KEEP**
  `DuplicatePositionOpenError`/F109 + `NullConditionIdOnOpenPhaseError` (local write-integrity,
  not chain drift).
- `fill_tracker.py` — it both **mints** the local position ledger (write path) and runs
  drift-hold logic (`_hold_pending_*`, `_confirmed_absent_or_defer`, `ChainObservationEnvelope`).
  The verification-hold logic survives as chain-arbitration; the ledger-minting reshapes under D2.
- `diff_engine.py` — 4 report-only predicates die with the local-command target; the
  `reservation_orphan` money-reconciliation writer survives as a chain-fact-driven correction.
- `chain_mirror_reconciler.apply_size_correction_finding` — the classification survives; the
  `UPDATE position_current` write-back reshapes under D2.

## Coverage / caveats

- `command_recovery.py` and `exchange_reconcile.py` per-pass line SPANS are approximate (def-line
  deltas); the def file:line anchors are HEAD-exact.
- Not separately weighed here (adjacent, verify if in scope): `src/events/edli_*_bridge.py`
  (EDLI lane, D3), `src/state/venue_command_repo.py` inline `canonical_trade_fact` copy (:2329,
  dedup dup), `src/execution/harvester.py` settlement writers (`record_settlement_result` :560,
  `_write_settlement_truth` :1525, `rediscover_disputed_settlements` :1164 — D5).
- `settlements`/`observations` `DISPUTED` authority tier is owned by excision packet **T2b**
  (separate); listed here only where scripts touch it.
</content>
