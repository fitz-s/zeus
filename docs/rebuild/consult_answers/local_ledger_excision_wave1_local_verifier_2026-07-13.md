# Local-Ledger Excision Wave-1 — Local Adversarial Verifier Report (2026-07-13)

**Auditor:** excision-critic (local, read-only + test-running). Adversarial stance: trying to REFUTE "wave-1 satisfies the adjudicated gates and LX-2R may start."
**Branch:** p2-pending-exit-restart-redecision @ HEAD `fb3e48900` (task cited `f07d91b69`; HEAD had advanced 6 non-wave commits — day0/events/engine/execution fixes).
**Law audited against:** `docs/rebuild/local_ledger_excision_2026-07-12.md` (§Consult 裁决, §Round-2 delta, §修订执行序 LX-0R..5R) + `docs/rebuild/consult_answers/local_ledger_excision_delta_round2_2026-07-13.txt`.

---

## VERDICT: NO-GO (for dispatching LX-2R as the complete reducer + read-model + cent-equivalence unit)

Conditional-GO to begin reducer *implementation* only, with the two missing LX-1R rehomes tracked as hard blockers on the cent-equivalence / read-model-backfill milestone.

The six landed packets each pass their own stated gate and their own new test suites are green. The wave fails as an LX-2R *foundation* because **two of the three LX-1R "before writing the new reducer" rehomes the adjudication mandates are unmet.**

---

## Per-packet results

| Packet | SHA | Verdict |
|---|---|---|
| LX-0R contracts/epoch/manifest | 7f00e92a0 | **PASS w/ MAJOR reservation** |
| LX-T1 payout observer | fa689e8f7 | **PASS** (1 minor) |
| LX-T4 fill synchronizer + alias graph | 3b08139f1 | **PASS** (1 minor/divergence) |
| LX-T2-a balance head + token registry + allocation | 6acb37116 | **PASS** |
| LX-E attribution + append-only receipts | 23248f72e | **PASS** (1 minor) |
| LX-G ghost-sell P&L stop | 19976478d | **PASS** |
| LX-F identity supersession | f2c50ebd5 → REVERTED af902a8e4 | **REVERTED (clean); see LX-F answer** |

### LX-0R (7f00e92a0) — PASS w/ MAJOR reservation
- Forbidden-column contract complete vs round-2 census: `src/contracts/economics_ownership.py:147-218` defines position_current ×10 + edli_live_profit_audit ×5. All 12 census-named bypass sites + projection funnel + both EDLI writers appear in the committed manifest.
- **Scanner catches a census-missed writer** (positively answers the "catch a NEW bypass writer" gate): `command_recovery.py reconcile_hard_terminal_position_projection_repairs` (manifest line, `:5473`) was NOT in the hand census §精化 #1 — the AST/regex scanner surfaced it. Removed `writeback_settlement_pnl_to_audit` correctly no longer appears.
- Epoch machinery real, not stubbed: `src/state/truth_epoch.py:83-116` `transition_truth_epoch` refuses backward (`target_rank <= current_rank`), no-op repeat, and skip (`target_rank != current_rank + 1`); fail-closed default to LEGACY (`read_truth_epoch:70-80`). Capability check present (`current_build_capability:137-145`) — real predicate, INERT (no seam consults it this packet, correctly deferred to LX-3R).
- **MAJOR DEFECT — manifest `--check` gate is red at HEAD and line-drift-fragile by design.** `python scripts/gen_economics_writer_manifest.py --check` exits 1; `tests/scripts/test_gen_economics_writer_manifest.py::test_check_mode_passes_against_committed_manifest` and `::test_check_subprocess_exits_zero_on_no_drift` both FAIL. Root cause: `scripts/gen_economics_writer_manifest.py:362-363` compares full-file text (`content != existing`) and `render_manifest:333` embeds `{h.file}:{h.line}` per row. Any line-shifting commit drifts it. The drift here is pure line-noise from the 6 post-wave commits (verified: `edli_position_bridge.py:1002→1019`, `command_recovery.py:5473→5515`, `reactor.py:7897→7958`); the **writer SET is unchanged and complete.** Impact: the round-2 delta wants this manifest as a drift detector to catch a real new bypass writer before LX-3R; a check chronically red on unrelated commits trains operators to ignore it. Fix: regen + commit, and key the diff on `(file, function, table, columns)` rather than raw `:line`. Realist Check: MAJOR not CRITICAL — no runtime depends on it, detection is immediate, fix trivial, DB firewall at LX-3R is a second layer. *Mitigated by: fast detection, trivial fix, writer set currently correct.*

### LX-T1 (fa689e8f7) — PASS (1 minor)
- **UNKNOWN-never-zero enforced at DB level (not just code):** `src/state/schema/payout_observations_schema.py:68-80` CHECK — `RESOLVED_NONZERO` requires `payout_numerator > 0`, `RESOLVED_ZERO` requires `= 0`, `UNRESOLVED` requires `denominator = 0`. A fabricated "resolved zero" from missing data cannot be inserted; missing data classifies UNKNOWN (`payout_observer.py:142,146,173-175` — writer sets numerator/denominator = None for UNKNOWN). PASS.
- **Append-only + reorg supersession:** rows immutable except a one-time `superseded_by` NULL→non-NULL transition, enforced by DB trigger `payout_observations_guarded_update` (`schema:99-119`, RAISE ABORT) + `payout_observations_no_delete` (`:121-127`). `block_number`/`block_hash` present for reorg keying. Append-then-close-out at `payout_observer.py:319`. PASS.
- **No signing:** module docstring + code — only `eth_call`/`_json_rpc_call`; no signer key / wallet credential / adapter import (`payout_observer.py:26-29`). PASS.
- **NOT wired into settlement grading:** only consumer is the read-only 10-min daemon job + `ensure_table` at boot; no grading/settlement path reads `payout_observations` as authority (grep across src/). PASS — consistent with "runs before activation collecting real facts, non-shadow."
- **MINOR:** the CHECK's `(state = 'UNKNOWN')` branch (`schema:69`) imposes NO constraint on payout columns, so the DB permits an `UNKNOWN` row carrying non-null payout values. The writer never does this (always NULL/NULL for UNKNOWN), and there is no consumer today, so blast radius is nil — but the DB-level guarantee the law asked for is incomplete. Tighten to `(state='UNKNOWN' AND payout_numerator IS NULL AND payout_denominator IS NULL)`.

### LX-T4 (3b08139f1) — PASS (1 minor/divergence)
- **Watermark advance-after-persist genuinely transactional:** `src/ingest/fill_synchronizer.py:210` explicit `conn.execute("BEGIN")` opens the outer transaction; the append loop + `_advance_watermark:263` run inside it; `except: conn.rollback()` (`:274-275`) / `else: conn.commit()` (`:277-278`) → all-or-nothing. Correctly works around the SAVEPOINT auto-commit quirk (documented `:196-209`). PASS.
- **Watermark VALUE semantics — verified safe.** Watermark advances to wall-clock now (`observed = _coerce_dt(None)`), NOT max-trade-time. This is INTENTIONAL and correct: the module docstring (`:46-64`) documents that `get_trades()` "returns ALL currently-visible trades on every call… accepts `since` but does not forward it to the SDK… every cycle already scans everything." Coverage = full re-scan + idempotent re-append rejection (`_fact_already_recorded:236-245`); the watermark is a completeness proof, not a resumption cursor. A late-arriving fill is caught on the next full scan regardless of watermark value — **Attack A does not reopen.**
- **Alias exactly-once property test real:** `tests/state/test_fill_dedup.py::test_economic_reducer_counts_exactly_once_regardless_of_insertion_order` + `::test_economic_reducer_sums_children_once_excludes_aggregate` — pass.
- **exchange_reconcile CTE repoint = zero behavior change:** the two CTE functions are moved verbatim to `src/state/fill_dedup.py` and imported under identical private names (`exchange_reconcile.py:64-67`). Corroborated by MY own baseline diff: the failing-test set for `test_exchange_reconcile.py` is byte-identical (86) at pre-wave `bb36d789a` and at HEAD. PASS.
- **MINOR/DIVERGENCE — foreign-fill discard.** Foreign fills (`command is None or not order_id`) are counted then dropped, never persisted (`fill_synchronizer.py:220-222`). The adjudication KEEP-spine says "foreign/ambiguous 留 observation 不丢." I rate this MINOR (not the external's blocker): foreign fills are correctly excluded from Zeus equity (shared-wallet law), and because `get_trades` re-serves the full history every cycle they are not permanently lost today; `append_trade_fact`'s non-empty command_id contract structurally forbids persisting them into `venue_trade_facts` (a separate observation lane would be needed). Flag for LX-2R only if the reducer's alias-graph/ambiguity evidence requires a durable local foreign-fill lane.

### LX-T2-a (6acb37116) — PASS
- **Head from the SAME snapshot (no second RPC):** `src/execution/post_trade_capital.py` `_upsert_pusd_wallet_balance_head(snapshot, ...)` — "written from the SAME CollateralSnapshot instance ledger.refresh() just persisted" (:78-79). Head upsert failure is non-fatal (history already durable), so it can't break the existing path. `src/state/wallet_balance_head.py:54-113` single-row `ON CONFLICT(wallet,asset) DO UPDATE` with a documented single-writer law. PASS.
- **Registry never-delete-on-absence:** `src/state/schema/ctf_token_registry_schema.py:68-71` `CREATE TRIGGER no_delete_ctf_token_registry BEFORE DELETE … RAISE(ABORT)` — DB-level Attack-F protection; five discovery sources; `/positions` only does discovery, absence never proves zero. PASS.
- **DEGRADED warning on every resolve:** `src/runtime/bankroll_provider.py:520` `resolve_zeus_equity_base` logs `ZEUS_EQUITY_DEGRADED_ATTRIBUTION` on every wallet_total-mode resolve. PASS.
- **wallet_total byte-equal:** `tests/runtime/test_resolve_zeus_equity_base.py` (passed) proves default (unset) mode equals the prior wallet-total value — no behavior change until an explicit allocation is set. PASS.

### LX-E (23248f72e) — PASS (1 minor)
- **Attribution in the SAME transaction as insert_command:** `src/state/venue_command_repo.py:1014-1022` calls `record_position_decision_attribution` inside `insert_command`'s body after the command INSERT/append_event. Proven atomic by `tests/test_venue_command_repo.py::test_rollback_on_mid_transaction_failure_also_rolls_back_attribution` (pass). Attribution table append-only via `UNIQUE(position_id) ON CONFLICT DO NOTHING` (`:1017-1021`). Written at ENTRY command creation from the executor (`executor.py:6460-6465`). PASS.
- **Backfill exact-only with UNATTRIBUTABLE:** `scripts/backfill_position_decision_attribution.py:113,130,137` — exact `command_id → execution_command_id` join only; zero OR multiple distinct hashes both mark UNATTRIBUTABLE with a named reason, never the (condition_id, direction) latest-row guess. PASS.
- **writeback_settlement_pnl_to_audit removed from grading batch:** `src/analysis/settlement_skill_attribution.py:1208` REMOVED; `tests/test_settlement_skill_attribution.py:530` asserts `not hasattr(mod, "writeback_settlement_pnl_to_audit")`. PASS.
- **world_grade_pnl_usd named correctly:** `settlement_skill_attribution.py:205,498,541` — the label goes into `settlement_attribution.world_grade_pnl_usd`, NOT `edli_live_profit_audit`. PASS.
- **MINOR:** the two mutable UPSERTs use archive-before-overwrite (`src/state/append_only_supersession.py` snapshots the prior row into a `<table>_supersessions` sibling before `ON CONFLICT DO UPDATE`) rather than a pure append-only versioned current table. The corpus-non-destruction invariant holds (history retained), but this is a softer reading of "append-only versioned receipts… rather than updating in place"; a reader holding a receipt id still sees the current row's contents mutate.

### LX-G (19976478d) — PASS
- **No P&L from order price:** the `realized_pnl = matched_size * (fill_price - entry_price)` line and the `realized_pnl_usd`/`exit_price` SET clauses are deleted (`exchange_reconcile.py:1418` site, diff). Both columns left UNKNOWN; `cost_basis_usd` stays conservative (`shares × entry_price`). PASS.
- **Strand-check — trade-fact close lane pickup:** `order_status = 'sell_pending_confirmation'` is set, and the existing `exit_lifecycle` trade-fact close path picks the position up on real `venue_trade_facts`. Both LX-G tests pass: `test_live_partial_ghost_sell_recovery_does_not_book_pnl_from_order_price`, `test_recovered_ghost_sell_position_closes_via_existing_trade_fact_path_not_stranded`. PASS.

---

## Critical Findings (block the LX-2R GO)

**C1 — EDLI canonical fact bridge never delivered.** No wave commit touches `src/events/edli_position_bridge.py` (verified across all 6 SHAs); `edli_position_bridge.py:1002 _absorb_same_order_duplicate_bridge_fill` remains a live `position_current` economics writer in the committed manifest. Round-2 delta LX-1R (txt:84-88): "Before writing the new reducer: Replace edli_position_bridge economics output with canonical command/fill/attribution facts." §(b) txt:231: "before LX-3R, not at LX-3R. It must first become a permanent fact bridge." Without it, the reducer's cent-equivalence replay misses every EDLI-originated fill. NOTE: the wave's own Execution log lists packet E as "attribution+EDLI fact bridge" — this over-claims; LX-E delivered attribution + append-only receipts only. Confidence: HIGH.

**C2 — Identity-supersession facts absent (LX-F reverted).** `af902a8e4` reverted `f2c50ebd5`; `src/state/position_duplicate_consolidator.py:370 _merge_equivalent_rows` is byte-restored to synthesizing merged shares/cost_basis (still in the manifest). Round-2 delta [BLOCKER duplicate-position identity]: "before read-model backfill, convert prior consolidations into explicit immutable identity-supersession facts… simply deleting its updates leaves duplicate identities that a new reducer can count twice." txt:93 places it under "Before writing the new reducer." The revert is CLEAN — no dangling `POSITION_IDENTITY_SUPERSEDED` reference anywhere in the landed wave — but the reducer cannot produce a correct cent-equivalent read model until F is reworked and its historical backfill runs. Confidence: HIGH.

---

## LX-F question — answered

**LX-2R's deterministic reducer / read-model HARD-requires identity-supersession facts** (and, symmetrically, the EDLI canonical fact bridge). Both are named in the round-2 delta as preconditions "before writing the new reducer" (txt:84-93). Therefore:

- **LX-2R reducer *implementation* may proceed in parallel** while F is reworked — the reducer MUST be designed to dedup by `POSITION_IDENTITY_SUPERSEDED` and to consume canonical EDLI fill facts.
- **LX-2R's cent-equivalence replay and read-model backfill milestone CANNOT close** until both rehomes land and their backfills run. Because the team-lead's LX-2R definition explicitly includes the cent-equivalence gate, the unit as scoped is NOT clear to dispatch.

**Open risk on the revert:** `af902a8e4` carries NO stated reason and landed immediately after F. F touched load-bearing grammar (the `position_events` CHECK in both `db.py` and the architecture kernel SQL, `CanonicalPositionEventKind`, `kernel_manifest.yaml`, `money_path_objects.yaml`, and dropped the stale `CHAIN_QUARANTINED` enum). Whether the revert was for a defect or for sequencing is unverifiable from git; the reworked F must independently re-validate those grammar edits.

---

## MISSING-before-LX-2R (cent-equivalence gate)

1. **EDLI canonical fact bridge** (C1) — hard block on cent-equivalence gate. Build the EDLI-event→canonical-command/fill/attribution bridge (LX-1R deliverable) before closing the gate.
2. **LX-F identity-supersession facts + historical backfill** (C2) — hard block on read-model backfill. Rework F (re-validating the position_events grammar) and run `backfill_identity_supersession_facts` over history.
3. **Manifest regen + line-insensitive `--check`** (MAJOR) — required for the round-2-mandated drift gate to function before LX-3R.
4. **(Minor)** Payout observer historical backfill — plan LX-1R says "payout observer+backfill"; only the forward observer landed. Needed if the settlement-learning corpus is rebuilt from payouts.
5. **(Minor/conditional)** Durable foreign-fill observation lane — only if the reducer's alias-graph / attribution-ambiguity evidence requires foreign fills retained locally (adjudication "foreign/ambiguous 留 observation 不丢").

---

## Test picture (reproduced, not cited)

- All five spine packets' NEW suites PASS: payout_observer, fill_synchronizer, fill_dedup, wallet_balance_head, ctf_token_registry, resolve_zeus_equity_base, backfill_ctf_token_registry, post_trade_capital_collateral, live_profit_audit, settlement_skill_attribution, plus LX-G's two ghost-sell tests and LX-E's four attribution tests.
- 93 failures in `tests/test_exchange_reconcile.py` (86) + `tests/test_venue_command_repo.py` (7) are **PRE-EXISTING** — the failing test-name set is BYTE-IDENTICAL to a fresh worktree at the pre-wave parent `bb36d789a` (`comm -13`/`-23` both empty). Example: NC-18 architectural lint (`exchange_reconcile.py:1118` writes venue_command_events outside the repo) fails at baseline too. Neither introduced nor fixed by the wave.
- The only wave-attributable red tests are LX-0R's 2 manifest tests (MAJOR).

---

## Divergence vs external review (GPT-5.6 NO-GO) — for cross-check

| External blocker | My independent finding |
|---|---|
| EDLI bridge missing | **CONVERGE** — Critical C1 (verified: no commit touches edli_position_bridge; still a live economics writer). |
| E receipts not versioned | **DIVERGE (I: MINOR)** — archive-before-overwrite preserves the prior corpus in `<table>_supersessions`; the destruction risk the adjudication targets is mitigated. Softer than "append-only versioned," not a blocker. |
| 0R capability over-advertising ACTIVE_NEW | **DIVERGE (I: open-question/acceptable for LX-0R)** — `current_build_capability()` returns the full set, but it is INERT (no seam consults it this packet; narrowing is correctly deferred to LX-2R's activation-readiness checklist per `truth_epoch.py:137-145`). Confirm LX-2R actually wires the narrowing, else the Attack-E stale-daemon fence never materializes. |
| T1 reorg / CHECK permits UNKNOWN-with-values | **PARTIAL DIVERGE (I: MINOR)** — the UNKNOWN-permits-values gap is REAL at the schema level (`schema:69`) but the writer always writes NULLs and no consumer exists; blast radius nil today. Reorg keying: block_number/block_hash + supersession trigger present; log_index absent but arguably N/A (payoutNumerators is a storage read, not an event log). Worth tightening, not a blocker. |
| T4 foreign-fill discard | **PARTIAL DIVERGE (I: MINOR)** — real vs "留 observation 不丢," but foreign fills are correctly excluded from Zeus equity and re-served by every full `get_trades` scan (not permanently lost today); `append_trade_fact` structurally forbids persisting command-less fills. |
| T4 watermark | **DIVERGE / REFUTED (I: correct)** — the watermark is advisory; `get_trades` scans everything every cycle and does not forward `since` to the SDK (documented `fill_synchronizer.py:46-64`), so a wall-clock watermark cannot miss late-arriving fills. Attack A does not reopen. |

**Additional findings the external did not flag:** the manifest `--check` line-drift red tests (MAJOR); LX-F identity-supersession revert as the *second* pre-reducer blocker (Critical C2). Both my and the external's reviews land NO-GO; the shared, highest-confidence blocker is the missing EDLI canonical fact bridge.
