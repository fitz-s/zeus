# Quarantine excision census — src part TAIL3 (11 files)

Read-only classification. No edits made. Buckets: B1 dies-with-disease (names owning T-target /
re-implementation), B2 reshape-and-rename (new name proposed), B3 dead-code, B4 text-only.

## 1. `src/engine/AGENTS.md`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| src/engine/AGENTS.md | 40-41 | Doc line: "chain/local convergence check (SYNCED/VOID/QUARANTINE) must complete before evaluator" — describes T2/T5 gate in prose | B4 text-only | rewrite as "SYNCED/VOID/scoped-block" or post-T5 vocabulary once T2/T5 land | none (doc) | none |

## 2. `src/engine/cycle_runner.py` (T2 target — verified)

Doc claim `:129-155,:400-402` **CONFIRMED EXACT**: `_has_quarantined_positions` is precisely
129-155; the `and not has_quarantine` line inside `_discovery_gates_allow_entries`'s return is
precisely line 402 (function starts 370, return starts 400). No drift from doc.

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| cycle_runner.py | 17 | imports `has_acknowledged_quarantine_clear` from control_plane | B1 dies with T6 (control-plane ack machinery) | — | cycle_runtime.py:3854 (via deps) | control_plane.json ack tokens |
| cycle_runner.py | 129-155 | `_has_quarantined_positions()`: portfolio-wide bool — any ChainOnlyFact blocking, any pos chain_state in {quarantined,quarantine_expired}, or any pos state==quarantined not redecision-eligible → True (global freeze fact) | B1 dies with T2 | — | called cycle_runner.py:856; `_runtime._quarantined_position_can_redecision` at :146 is the one release-path carve-out (defined cycle_runtime.py:3940, out of file set) | position_current.state/chain_state, portfolio.chain_only_facts |
| cycle_runner.py | 146 | call to `_runtime._quarantined_position_can_redecision(pos)` inside the gate — the redecision release-path carve-out (branch this task's name references) | B1 dies with T2 (container rewritten); underlying redecision helper (cycle_runtime.py:3940, not in file set) looks independently legitimate/scoped and may survive renamed | — | cycle_runtime.py:3940 (definition, out of scope) | position_current |
| cycle_runner.py | 219-228 | force-exit sweep: `state_val == "quarantined"` skip check alongside `_TERMINAL_POSITION_STATES_FOR_SWEEP`, with comment explaining quarantined dropped out of canonical TERMINAL_STATES | B1 dies with T5 | — | `_execute_force_exit_sweep` (this fn) | position rows in-memory |
| cycle_runner.py | 370-415 | `_discovery_gates_allow_entries(...)`: `has_quarantine` kwarg + `and not has_quarantine` in the single-authority AND-chain (global freeze) | B1 dies with T2 | — | called cycle_runner.py:1042-1054 | — |
| cycle_runner.py | 419-420, 429 | comments explaining quarantine exclusion rationale for `_collect_execution_truth_warnings` | B4 text-only, describes B1(T2) mechanism | rewrite once T2 lands | — | — |
| cycle_runner.py | 759 | `chain_stats.get("quarantined")` — one of 4 flags that mark `portfolio_dirty=True` after chain sync | B1 dies with T5 (chain sync stats keying disappears once nothing mints quarantine) | — | inline in `run_cycle` | — |
| cycle_runner.py | 762-766 | imports + calls `check_quarantine_timeouts(portfolio)`; on nonzero writes `summary["quarantine_expired"]` and dirties portfolio | B1 dies with T5 for the quarantine-position-expiry portion; NOTE: chain_reconciliation.py's `check_quarantine_timeouts` (out of scope, def at chain_reconciliation.py:2306) is documented by its own callers (tests/test_k4_slice_j.py) as "retained ONLY for its unrelated ChainOnlyFact [48h review escalation]" — i.e. the function is mixed: one branch is disease (T5), one branch is legitimate ChainOnlyFact review escalation (B2 candidate, out of scope to rename here) | — | cycle_runner.py:762,764 (this call site) | position_current.chain_state='quarantine_expired' |
| cycle_runner.py | 856 | `has_quarantine = _has_quarantined_positions(portfolio)` | B1 dies with T2 | — | feeds 959,978,1050 | — |
| cycle_runner.py | 959-960 | `entries_blocked_reason = "portfolio_quarantined"` operator-facing reason string | B1 dies with T2 | replaced by scoped-block/DATA_DEGRADED reason strings per T2 target form | Discord/status_summary readers (out of scope) | summary JSON key |
| cycle_runner.py | 978-979 | `summary["portfolio_quarantined"] = True` | B1 dies with T2 | — | cycle report consumers (out of scope) | summary JSON key |
| cycle_runner.py | 1050 | `has_quarantine=has_quarantine` kwarg passed at the one call site of `_discovery_gates_allow_entries` | B1 dies with T2 | — | — | — |

## 3. `src/engine/event_reactor_adapter.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| event_reactor_adapter.py | 959-994 | `_position_phase_or_positive_chain_clause()`: builds SQL OR-clause — normal phases OR (`phase='quarantined' AND chain_state IN CURRENT_MONEY_RISK_CHAIN_STATES` with positive chain_shares) — includes quarantined-but-chain-risky rows in live family selection instead of excluding them (already does T2's "bounded exposure inclusion" in spirit) | B1 dies with T5 for the `phase='quarantined'` branch only; the chain_state-driven bounded-exposure inclusion logic is independently B2-legitimate and survives once that branch is dropped | rename fn to drop "quarantine" framing, e.g. `_position_phase_or_chain_risk_clause` (already phase-neutral in name) | 6 call sites: :1087, :1178, :1292, :1456, :1926, :19934 (all internal) | position_current.phase, chain_state, chain_shares |

## 4. `src/engine/lifecycle_events.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| lifecycle_events.py | 42 | `QUARANTINED = LifecyclePhase.QUARANTINED.value` module constant | B1 dies with T5 | — | used throughout this file + `_ENTRY_HELD_...` sets etc. | position_current.phase enum |
| lifecycle_events.py | 53 | comment: `"unknown_entered_at" is the QUARANTINE_SENTINEL used by chain_reconciliation.py` — documents a timestamp fallback, code itself (`_non_empty`) is generic and not quarantine-specific | B4 text-only | rewrite comment to describe as "unknown-entry-timestamp sentinel", drop "QUARANTINE_SENTINEL" name | `_non_empty()` (this file, generic helper) | — |
| lifecycle_events.py | 606-610 | `build_monitor_refreshed_canonical_write`: `phase_after` allow-set includes `QUARANTINED` alongside ACTIVE/DAY0_WINDOW/PENDING_EXIT | B1 dies with T5 | — | callers in cycle_runtime.py (out of scope) | position_events.phase_after |
| lifecycle_events.py | 1271-1356 | `build_review_required_canonical_write()`: mints durable `REVIEW_REQUIRED` event, hard-fails unless `phase_after==QUARANTINED`, persists `position_current.phase='quarantined'` + `chain_state='size_mismatch_unresolved'` — this is a live MINTING writer not explicitly named in doc's T1-T4 list | B1 dies with T5 — flagged as an **unlisted minting writer**; T8 census should fold this into the T4/T5 "all minting writers" removal set alongside fill_tracker | rename to fact-based semantics per doc's decision_integrity direction, e.g. `build_size_mismatch_review_canonical_write` with a non-quarantine terminal phase | src/state/chain_reconciliation.py:920,928 (live caller, out of scope); tests/state/test_inv_review_required_durable.py; tests/state/test_inv_f2_typed_event_timestamps.py | position_events (event_type=REVIEW_REQUIRED), position_current.phase/chain_state |
| lifecycle_events.py | 1359-1429 | `build_chain_quarantined_canonical_write()`: mints `CHAIN_QUARANTINED` event, hard-fails unless `phase_after==QUARANTINED`, payload reason `"chain_only_quarantined"` | **B3 DEAD-CODE — verified zero production callers.** Only reference outside this file is `tests/test_architecture_contracts.py` (:1999,2012,2025,2041). chain_reconciliation.py imports 4 other lifecycle_events builders (rescue, venue_position_observed, chain_economics_observed, chain_size_corrected, review_required) but never this one. | delete function + its tests | tests/test_architecture_contracts.py only | none live (dead) |

## 5. `src/events/reactor.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| reactor.py | 7785-7883 (`_edli_current_held_position_condition_scope`), hit at :7829 | SQL: position_current rows where (`phase IN (active,day0_window,pending_exit) AND chain_state risky`) OR (`phase='quarantined' AND chain_state risky`), positive chain_shares — builds EDLI market-substrate scope including quarantined-but-chain-risky positions | B1 dies with T5 for the `phase='quarantined'` disjunct; non-quarantine chain-risk-scope logic survives | — | reactor.py:7878 (self); tests/events/test_continuous_redecision_emit.py:1970,2022; tests/money_path/test_edli_market_substrate_warm_cycle.py:1577 | position_current.phase, chain_state, chain_shares, condition_id |

## 6. `src/execution/edli_presence_resolver.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| edli_presence_resolver.py | 161-164 | RuntimeError message text: "no CONFIRMED trade ... not a presence (absence resolver or quarantine applies)" — comment/error-text only, no quarantine table/state read or write | B4 text-only | reword error message to name the actual alternative resolver, drop "quarantine" | — (raised, caught by generic error handling upstream) | none |

## 7. `src/execution/exchange_reconcile.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| exchange_reconcile.py | 116-121 | `_EXIT_FILL_PROJECTION_PHASES` frozenset includes `"quarantined"` (with comment: confirmed EXIT sell is stronger venue truth than local quarantine, must be allowed to economically-close) | B1 dies with T5 (member drops once phase retires); underlying "venue fill truth overrides stale local phase" logic survives | — | :4738 (`_reconcile_recorded_exit_fill_projections` gate check, verify exact fn near that line) | position_current.phase |
| exchange_reconcile.py | 2181 | `pc.phase IN ('active','day0_window','pending_exit','economically_closed','quarantined')` inside `_reconcile_recorded_exit_fill_projections` (def at :2129) trade-fact matching query | B1 dies with T5 | — | this fn only | position_current.phase, venue_commands, trade_facts |
| exchange_reconcile.py | 5754-5755 | docstring/comment on `_token_is_suppressed_external`: "chain_reconciliation quarantines chain-only / operator-manual holdings there ('chain_only_quarantined')" — documents a real, live, scoped mechanism owned elsewhere (token_suppression.suppression_reason='chain_only_quarantined', minted at chain_reconciliation.py:1331, read at db.py:11542+ and portfolio.py:2512+, all out of scope) | B4 text-only (comment); the mechanism it describes is genuinely B2-legitimate (scoped per-token suppression, evidence-backed) and lives in files outside this file set | rewrite comment once the owning mechanism is renamed (e.g. to `chain_only_unattributed_holding`) | `_token_is_suppressed_external` (this file, generic reader — reads `token_suppression` table by token_id, not by reason string, so the function itself needs no change) | token_suppression.suppression_reason='chain_only_quarantined' (owned elsewhere) |

## 8. `src/execution/executor.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| executor.py | 172-179 | comment + `_ENTRY_DUPLICATE_NON_OPEN_PHASES` frozenset = TERMINAL_STATES ∪ {ECONOMICALLY_CLOSED, QUARANTINED} — treats quarantined rows as non-open (not a duplicate-entry blocker) | B1 dies with T5 (QUARANTINED member drops); TERMINAL_STATES/ECONOMICALLY_CLOSED membership survives | — | :1753,1766,1781,1816 (duplicate-entry SQL query placeholders) | position_current.phase |
| executor.py | 1148-1169 | **`_entry_actionable_certificate_payload_and_component()` — CONFIRMED live pre-submit safety caller named in mission doc.** At :1163 calls `_decision_certificate_is_quarantined(conn, certificate_hash)`; if true, returns `allowed=False, reason="actionable_certificate_quarantined"` and blocks the entry capability component before submit | B1 dies with decision_integrity re-implementation (per excision doc: "R1-b erratum #8 found live callers (executor.py pre-submit safety, ...)" — this IS that caller) | re-point to fact-validity semantics on the authoritative row/revocation record, e.g. `_entry_actionable_certificate_is_revoked()` reading `decision_certificates.revoked_reason` (or equivalent) instead of the side-table | called from `_build_entry_capability_components`-style caller at :1281 and :5824 (both this file) | decision_certificates (schema), used as certificate_hash key |
| executor.py | 1384-1422 | `_decision_certificate_is_quarantined()`: reads `decision_integrity_quarantine` table (schema-qualified across attached DBs) WHERE `table_name='decision_certificates' AND row_id=certificate_hash AND reason_code IN (REASON_INVALID_LIVE_ACTIONABLE, REASON_INVALID_LIVE_PARENT_MODE)` — imports constants from `src.state.decision_integrity_quarantine` (out of file set) with a hardcoded fallback if import fails | B1 dies with decision_integrity re-implementation | rename/re-implement per doc: validity lives ON `decision_certificates` row or a precisely-named revocation record; readers consult validity, not a side "does this fact exist" table | executor.py:1163 (only in-file caller); **note**: `src/execution/command_recovery.py` defines its **own separate, duplicate** `_decision_certificate_is_quarantined` at line 3042 (own logic, not calling this one) called at command_recovery.py:3111 — out of scope but flagged as a sibling reimplementation that must be re-pointed in the same packet | `decision_integrity_quarantine` table: cols `table_name`, `row_id`, `reason_code`; literals `QUARANTINED_INVALID_LIVE_ACTIONABLE_CERTIFICATE`, `QUARANTINED_INVALID_LIVE_MONEY_PARENT_MODE`; source module `src/state/decision_integrity_quarantine.py` + `src/state/schema/decision_integrity_quarantine_schema.py` (both out of file set, top of T8 mass list at 124 hits) |

## 9. `src/execution/exit_lifecycle.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| exit_lifecycle.py | 58-66 | `_PENDING_EXIT_SCAN_INACTIVE_STATES` frozenset includes `"quarantined"` alongside settled/voided/admin_closed/economically_closed | B1 dies with T5 | — | :236 inside `_rotated_pending_exit_scan_positions` (def :229) — skips quarantined positions from pending-exit rotation scan | position.state (runtime) |
| exit_lifecycle.py | 1075-1093 | `mark_market_closed_hold_to_settlement()`: if `current_state in {QUARANTINED, ECONOMICALLY_CLOSED, SETTLED, VOIDED, ADMIN_CLOSED}` → preserve state as-is (don't manufacture a sell failure) rather than proceeding to hold-to-settlement logic | B1 dies with T5 (QUARANTINED member drops; other terminal-ish members survive) | — | this fn (public, called by monitor/exit lane, out of scope for exact caller) | position.state (runtime) |
| exit_lifecycle.py | 6024-6052 | `_check_monitor_cadence_watchdog()`: `pc.phase IN ('active','day0_window','pending_exit','quarantined')` — includes quarantined in the set of phases whose last MONITOR_REFRESHED timestamp is checked for cadence-gap staleness | B1 dies with T5 | — | this fn only | position_current.phase, position_events (MONITOR_REFRESHED) |

## 10. `src/ingest/polymarket_user_channel.py`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| polymarket_user_channel.py | 53-58 | `UNRESOLVED_LOT_STATES` tuple includes `"QUARANTINED"` as a `position_lots.state` literal, consumed by `_local_side_effect_surface_empty()` (~:744-770) to gate the M5 clean-reconnect proof — a QUARANTINED lot blocks the "local side-effects are resolved" claim | B1 dies with T4 (lot-level sibling of fill_tracker's position-level `_mark_entry_quarantined` minting; unlisted in doc's T1-T4 file list — flag for T8 inclusion) | — | :748,767 (this file, query construction) | **producer confirmed elsewhere**: `src/state/venue_command_repo.py` writes `position_lots.state='QUARANTINED'` (INSERT :3212 region, read/filter at :351, :3317) — out of file set but is the actual minting site; not dead code |

## 11. `src/control/AGENTS.md`

| File | Lines | Mechanism | Bucket | New name if B2 | Consumers | DB artifacts |
|---|---|---|---|---|---|---|
| src/control/AGENTS.md | 16 | doc table row lists `acknowledge_quarantine_clear` as one of 8 supported control-plane commands | B4 text-only, describes T6 (control-plane ack machinery, dies with T2/T5) | rewrite row once T6 lands — command removed from the 8, not renamed (T6 says the whole ack lane dies) | — (doc) | control_plane.json command payload |

---

## Summary

- **Total distinct mechanisms classified: 32** across the 11 files (AGENTS.md doc hits counted as 1 mechanism each; multi-line frozensets/functions with one coherent behavior counted once even if grep hit multiple times inside them).
- **Bucket counts**: B1 (dies-with-disease) = 25; B2 (reshape-and-rename, standalone) = 0 pure — every real-function case found in these 11 files is either a comment describing a B2 mechanism owned by an out-of-scope file (counted B4) or a B1 branch riding a survivable B2 core (noted inline, not double-counted); B3 (dead-code) = 1 (`build_chain_quarantined_canonical_write`); B4 (text-only) = 6.
- **T2 doc line numbers**: CONFIRMED exact, no drift. `_has_quarantined_positions` = cycle_runner.py:129-155 (verified: def starts :129, closes :155). Global gate `and not has_quarantine` = cycle_runner.py:402 exactly (function def :370, return statement opens :400, the `has_quarantine` line is the 3rd return-clause line = :402). Doc's `:400-402` range is precisely the return statement's opening 3 lines.
- **executor.py pre-submit safety check (named live caller in mission doc)**: CONFIRMED. `_entry_actionable_certificate_payload_and_component()` (executor.py:1148-1169) calls `_decision_certificate_is_quarantined()` (executor.py:1384-1422) at line 1163; on a match it blocks the entry capability component with `reason="actionable_certificate_quarantined"` before submit. This reads the `decision_integrity_quarantine` side-table (schema-qualified across attached DBs) keyed on `table_name='decision_certificates', row_id=certificate_hash, reason_code IN (...)`. Classified B1 dies-with-decision_integrity-re-implementation per the excision doc's own §"decision_integrity_quarantine" note. **Additional finding not in the mission doc**: `src/execution/command_recovery.py` has its own independently-defined, differently-implemented `_decision_certificate_is_quarantined` (command_recovery.py:3042, called :3111) — a duplicate/sibling of the executor.py one, not a shared import. Both must be re-pointed to the new fact-validity semantics in the same packet, per the doc's "callers re-pointed in the same packet" instruction — this doubles the known live-caller count from 1 to 2 within just the files touched by this census.
- **Notable dead-code finding**: `build_chain_quarantined_canonical_write` (lifecycle_events.py:1359-1429) has zero production callers — chain_reconciliation.py imports 5 other lifecycle_events builders but never this one; only `tests/test_architecture_contracts.py` references it. Straight delete, no re-implementation needed.
- **Notable unlisted-minting-site findings for T8 fold-in**: (a) `build_review_required_canonical_write` (lifecycle_events.py:1271-1356, live caller chain_reconciliation.py:920/928) mints QUARANTINED phase on unresolved chain/local size mismatch — not named in doc's T1-T4 file list; (b) `position_lots.state='QUARANTINED'` minted in `src/state/venue_command_repo.py` (lot-level, distinct from fill_tracker's position-level T4 scar) and consumed by polymarket_user_channel.py's M5 clean-reconnect gate — also not named in doc's T1-T4 list.
