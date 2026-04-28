# opponent-mid — Solo Context Boot Evidence

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d
Author: opponent-mid (Region Mid, adversarial role)
Status: pre-A2A; awaiting judge greenlight

## Files read (path:line — grep-verified within last 10 min)

1. `architecture/invariants.yaml` — INV-30 (zones K2_runtime, statement on row-state SUBMITTING before SDK), INV-31 (recovery scan unresolved states), INV-32 (materialize gates on ACKED/PARTIAL/FILLED).
2. `src/execution/command_bus.py:44-110` — CommandState 11-member closed enum; CommandEventType 11-member closed enum.
3. `src/execution/command_bus.py:92-103` — IN_FLIGHT_STATES = {SUBMITTING, UNKNOWN, REVIEW_REQUIRED, CANCEL_PENDING}; TERMINAL_STATES = {FILLED, CANCELLED, EXPIRED, REJECTED}.
4. `src/state/venue_command_repo.py:194-242` — venue_commands schema: command_id, position_id, decision_id, idempotency_key, intent_kind, market_id, token_id, side, size, price, venue_order_id, state, last_event_id, created_at, updated_at, review_required_reason. **NO** signed_order_hash, payload_hash, condition_id, outcome, no_token_id, clobTokenIds_raw, invariant_hash columns.
5. `src/state/venue_command_repo.py:50-72` — _TRANSITIONS table: CANCEL_REQUESTED appears as EVENT trigger producing CANCEL_PENDING state, never as a state itself.
6. `src/execution/executor.py:537-577` — submit-phase SDK call with `except Exception as exc:` → flattens to `OrderResult(status="rejected", reason=f"submit_unknown: ...")`. Inner event = SUBMIT_UNKNOWN; outer API status = rejected. F-002 RESIDUAL (caller cannot distinguish UNKNOWN from REJECTED).
7. `src/engine/cycle_runner.py:60-102, 352-372` — `_execute_force_exit_sweep` only sets `pos.exit_reason="red_force_exit"`; comment line 67 explicitly: "Does NOT post sell orders in-cycle". **No SDK cancel_order call**; no durable cancel command persisted on RED.
8. `src/engine/cycle_runtime.py:1357-1424` — INV-32 gate present: skips materialize_position when command_state is SUBMITTING/UNKNOWN/None.
9. `src/execution/command_recovery.py:120-140` — recovery uses `client.get_order(venue_order_id)` only; no `get_open_orders` enumeration, no `get_trades` sweep. INV-31 reconciles only when local row has venue_order_id.
10. `src/data/polymarket_client.py:166-249` — `place_limit_order` (post_order via SDK) and `cancel(order_id)` exist; no `get_open_orders` wrapper.
11. `src/riskguard/risk_level.py:11,20,29` — RED is a level enum; doc string says "Cancel all pending orders, exit all positions immediately"; runtime does NOT execute that.
12. `tests/test_executor_command_split.py:125-172, 449-482` — SUBMIT_UNKNOWN tests assert `result.status == "rejected"` (line 152). Tests confirm flattening, do not flag it as a gap.
13. `docs/operations/task_2026-04-26_ultimate_plan/evidence/apr26_findings_routing.yaml:34-167` — F-001/F-003/F-006/F-008 all `bucket: NET_NEW`; F-001/F-008 routed to PROPOSED_EXECUTION_ORDER_COMMAND_JOURNAL; F-006 to PROPOSED_EXECUTION_RECONCILIATION_LOOP; F-004/F-005 to PROPOSED_EXECUTION_STATE_MACHINE; plan_coverage_pct: 25.8%.
14. `docs/operations/task_2026-04-26_execution_state_truth_p1_command_bus/implementation_plan.md:432` — NC-19 idempotency = local `H(decision_id, token_id, side, price_units)` hash; pre-signing.

## Attack vectors (≥5, grep-verified)

### AV-1 (F-001 RESIDUAL — payload binding gap)
**Claim**: INV-30 mandates row-state=SUBMITTING before SDK call but does NOT mandate signed-order-payload binding to the row.
**Evidence**: `venue_commands` schema (venue_command_repo.py:197-208) has zero columns for signed_payload, payload_hash, signed_order_hash, or invariant_hash. F-001 (per routing yaml) wants COMMAND_PERSISTED event with payload binding before signing. Currently the row carries only the *intent* (token_id, side, size, price), not the *signed order bytes* that actually went to the venue.
**Burden on proponent**: cite a column or event_type that captures the signed payload. None exists.

### AV-2 (F-006 RESIDUAL — exchange-side enumeration absent)
**Claim**: INV-31 reconciles command rows by `venue_order_id` lookup. F-006 wants exchange-side enumeration (open_orders, trades) without trusting local journal.
**Evidence**: `command_recovery.py:126` calls `client.get_order(venue_order_id)`. If venue_order_id is NULL (SDK exception before assignment), recovery cannot find the order via the venue. `polymarket_client.py` has no `get_open_orders` method. Authority direction: INV-31 reconciles "what we think we submitted"; F-006 reconciles "what the exchange thinks exists" — different direction.
**Burden on proponent**: show the codepath where Zeus enumerates exchange-side open orders without local order_id. None exists.

### AV-3 (F-010 RESIDUAL — RED is decorative-capability)
**Claim**: NC-17 forbids decorative-capability. RED documentation promises "cancel all pending orders, exit all positions" but runtime only marks `pos.exit_reason="red_force_exit"`.
**Evidence**: `cycle_runner.py:67` explicit comment "Does NOT post sell orders in-cycle". `cycle_runner.py:60-102` `_execute_force_exit_sweep` mutates Python objects in memory only. No `client.cancel(order_id)` call in RED path. No `submit_command(intent_kind=CANCEL)` either. RED is currently a flag-mutation, not an order action.
**Burden on proponent**: cite where RED durably submits CANCEL or DERISK commands at HEAD 874e00c. Not in cycle_runner.py, not in riskguard.py, not in executor.py.

### AV-4 (F-002/F-012 RESIDUAL — SDK error matrix collapsed)
**Claim**: SDK exception classification is missing. All exceptions flatten to status="rejected" with reason="submit_unknown: ...".
**Evidence**: `executor.py:547-577` catches `except Exception as exc` and emits SUBMIT_UNKNOWN event but returns `OrderResult.status="rejected"`. Test at `test_executor_command_split.py:140-152` asserts the rejected outcome with `RuntimeError("simulated venue timeout")` — there is no test for HTTP 404, post-submit-timeout, signature-rejection, or rate-limit. F-002 wants typed SUBMIT_TIMEOUT_UNKNOWN, SDK_REJECT, SDK_404 distinguished; current code conflates.
**Burden on proponent**: cite the typed-error classification site. None exists.

### AV-5 (§8.3 state-grammar gaps — K is 6; routing yaml over-counts NET_NEW by 4)

**Claim**: §8.3 17 transitions don't collapse to K=2 (proponent) and aren't K=17 NET_NEW (yaml). Audit at HEAD against `src/state/venue_command_repo.py:42-84` _TRANSITIONS table (28 legal pairs):

| §8.3 transition | _TRANSITIONS at HEAD | Verdict |
|---|---|---|
| COMMAND_PERSISTED | absent | NET_NEW |
| SIGNED_ORDER_PERSISTED | absent | NET_NEW |
| SUBMIT_TIMEOUT_UNKNOWN | SUBMIT_UNKNOWN line 51, no TIMEOUT typing | typing extension |
| ACCEPTED vs RESTING | RESTING absent | NET_NEW |
| PARTIALLY_FILLED | (ACKED/UNKNOWN/PARTIAL, PARTIAL_FILL_OBSERVED) → PARTIAL lines 56,65,70 | **EXISTS** |
| REMAINING_CANCEL_REQUESTED | (PARTIAL, CANCEL_REQUESTED) → CANCEL_PENDING line 72 | **EXISTS** |
| CANCEL_FAILED | absent | NET_NEW |
| CANCEL_REPLACE_BLOCKED | absent | NET_NEW |
| CLOSED_MARKET_UNKNOWN | absent | NET_NEW |
| TRADE_CONFIRMED | FILL_CONFIRMED event lines 57,66,71; exchange-side enumeration absent | event exists / semantic only |
| POSITION_CONFIRMED_FROM_EXCHANGE | absent (chain_reconciliation is position-side, not command-side) | NET_NEW |
| RECONCILED_BY_OPEN_ORDERS | absent (no get_open_orders) | NET_NEW (F-006) |
| RECONCILED_BY_TRADES | absent (no get_trades) | NET_NEW (F-006) |
| RECONCILED_BY_POSITION | chain_reconciliation.py exists | **EXISTS (different module)** |
| REVIEW_REQUIRED | reachable from 7 source states (lines 45,52,59,68,75,78,83) | **EXISTS — yaml over-count** |
| REDEEM_REQUESTED | absent | NET_NEW |
| REDEEMED | absent | NET_NEW |

**Tally**:
- ALREADY EXISTS (full): REVIEW_REQUIRED, PARTIALLY_FILLED, REMAINING_CANCEL_REQUESTED, RECONCILED_BY_POSITION = **4 over-counted by routing yaml**.
- EVENT/PARTIAL EXISTS (typing extension, not new state): SUBMIT_TIMEOUT_UNKNOWN, TRADE_CONFIRMED = **2 typing-only**.
- GENUINELY NET_NEW: COMMAND_PERSISTED, SIGNED_ORDER_PERSISTED, ACCEPTED-vs-RESTING, CANCEL_FAILED, CANCEL_REPLACE_BLOCKED, CLOSED_MARKET_UNKNOWN, POSITION_CONFIRMED_FROM_EXCHANGE, RECONCILED_BY_OPEN_ORDERS, RECONCILED_BY_TRADES, REDEEM_REQUESTED, REDEEMED = **11 genuinely new**.

**Real K-decision count for §8.3** (after deduping):
- (K1) Pre-submit payload binding (COMMAND_PERSISTED + SIGNED_ORDER_PERSISTED) — 2 transitions, ~F-001/F-003 cluster
- (K2) Post-submit error typing (SUBMIT_TIMEOUT_UNKNOWN + CLOSED_MARKET_UNKNOWN) — 2 transitions, ~F-002 cluster
- (K3) Book-resting state grammar (ACCEPTED-vs-RESTING) — 1 transition
- (K4) Cancel-failure terminal grammar (CANCEL_FAILED + CANCEL_REPLACE_BLOCKED) — 2 transitions, ~F-005
- (K5) Exchange-side reconciliation (RECONCILED_BY_OPEN_ORDERS + RECONCILED_BY_TRADES + POSITION_CONFIRMED_FROM_EXCHANGE) — 3 transitions, ~F-006
- (K6) Settlement post-trade (REDEEM_REQUESTED + REDEEMED) — 2 transitions, ~F-011 adjacent

**Real K = 6, not 2 (proponent) and not 17 (yaml). Yaml plan_coverage_pct: 25.8 understates by ~24%.**

**Burden on proponent**: enumerate their K=2 against this audit. Routing yaml flagged transition_REVIEW_REQUIRED `bucket: A1 / target_slice: A1` despite REVIEW_REQUIRED already being reachable from 7 states — yaml-heuristic mismatch.

### AV-6 (§8 amendment is closed-law amendment, not extension)
**Claim**: Extending CommandState breaks INV-29 closed-grammar invariant; every site pattern-matching CommandState (IN_FLIGHT_STATES, TERMINAL_STATES, _TRANSITIONS table, command_recovery resolution) must be re-audited per planning-lock.
**Evidence**: `command_bus.py:46` docstring: "Closed grammar of venue_commands.state values. The repo's _TRANSITIONS table at src/state/venue_command_repo.py uses these exact string values. A test_command_state_strings_match_repo asserts the round-trip." Adding PARTIAL_FILLED_RESTING / CANCEL_FAILED / RESTING is a CLOSED-LAW AMENDMENT, not a "state-machine extension".
**Burden on proponent**: produce a slice card titled "INV-29 grammar amendment" listing every site to re-audit, OR concede dual-grammar (Fitz Constraint #2 translation-loss).

### AV-7 (X1 sequencing risk — A1 ↔ D2.A)
**Claim**: A1 K4-RED→durable-cmd assumes venue_commands schema is stable. D2.A is CLOB V2 transport migration — likely renames `place_limit_order` / `post_order` and changes error-shape. A1's typed-error matrix may be invalidated by D2.A landing afterward.
**Evidence**: `polymarket_client.py:196` `self._clob_client.post_order(signed)` is the V1 surface. D2.A migrates this to V2.
**Burden on proponent**: produce dependency graph asserting independence, or concede sequencing risk.

## Counter-claims to expected proponent positions

**Proponent claim 1**: "F-001/F-006 structurally discharged on HEAD 874e00c by INV-30/31/32+NC-19+venue_commands."
**Counter**: NC-19 idempotency is local hash, not signed-payload hash (F-003). venue_commands has no signed-payload column (F-001 residual AV-1). INV-31 has no exchange-side enumeration (F-006 residual AV-2). DISCHARGE INCOMPLETE.

**Proponent claim 2**: "§8.3 17 transitions reduce to K=2 design decisions; mid-scope §8 extension covers F-004+F-005."
**Counter**: K ≥ 4 axes (state-grammar, partial-fill schema, cancel-failure, exchange-enumeration). Extending CommandState breaks INV-29 closed-law (AV-6). PARTIAL exists but PARTIAL_FILLED-event schema is empty.

**Proponent claim 3**: "PR18 P2 A1 K4 RED→durable-cmd preserves authority direction."
**Counter**: At HEAD, RED does not submit any durable cancel/derisk command (AV-3); it only mutates in-memory Python objects. A1 must EITHER add a new authority surface (risk module emits commands directly) OR push RED through cycle-runtime (latency cost). Either choice is an authority-direction decision, not preservation.

**Proponent claim 4**: "F-003 thin extension to venue_commands schema (one column + one repo write)."
**Counter**: F-003 + F-008 together require ≥ 5 columns (signed_order_hash, condition_id, outcome, no_token_id, invariant_hash). That is a schema decision under planning-lock, not a thin extension.

## Working hypothesis: NET_NEW packets needed

**Refined answer (post-§8.3 audit): 3 NET_NEW slices + 3 deferred-discharge, K=6 design decisions for state-grammar.**

After auditing §8.3 against `_TRANSITIONS` table at HEAD, partial concession to proponent's "structural decision" framing — but K=6 not K=2:

### NET_NEW slices (mid scope) — 3 packets

1. **EXEC_JOURNAL_PAYLOAD_BIND** (covers K1 + F-001 + F-003 + F-008)
   - 5-6 venue_commands columns: signed_order_hash, payload_hash, condition_id, outcome, no_token_id, invariant_hash
   - 2 new event_types: COMMAND_PERSISTED, SIGNED_ORDER_PERSISTED
   - Signing-bind hook in `src/execution/executor.py` _live_order/execute_exit_order between persist phase and submit phase.
   - INV-30 currently mandates row-state SUBMITTING before SDK; this slice extends to mandate signed-payload binding to the row.

2. **EXEC_STATE_GRAMMAR_AMEND** (covers K2 + K3 + K4 + F-002 + F-004 + F-005)
   - Closed-law amendment to INV-29: add CommandState members RESTING, CANCEL_FAILED; add CommandEventType members SUBMIT_TIMEOUT_UNKNOWN, CLOSED_MARKET_UNKNOWN, CANCEL_REJECTED, CANCEL_REPLACE_BLOCKED.
   - Re-audit sites: IN_FLIGHT_STATES, TERMINAL_STATES, _TRANSITIONS table, command_recovery resolution table, cycle_runtime materialize gate.
   - Test: extend `test_command_state_strings_match_repo` round-trip; add transition tests for new pairs.

3. **EXEC_EXCHANGE_RECONCILE** (covers K5 + F-006)
   - New `polymarket_client` methods: `get_open_orders()`, `get_trades(since)`.
   - New recovery codepath in `src/execution/command_recovery.py` that enumerates exchange-side without local venue_order_id (handles SUBMIT_UNKNOWN orders where exception fired before order_id assignment).
   - 3 new event_types: RECONCILED_BY_OPEN_ORDERS, RECONCILED_BY_TRADES, POSITION_CONFIRMED_FROM_EXCHANGE.

### Deferred-discharge (PR18 P2 + Wave 2) — 3 packets

- **A1 (F-010 RED→durable cancel)**: authority-direction proof needed. RED currently only mutates `pos.exit_reason` in memory (cycle_runner.py:60-102). Slice must emit durable CANCEL command via existing INV-30 path.
- **A3 (F-002 typed SDK errors)**: depends on EXEC_STATE_GRAMMAR_AMEND landing CANCEL_FAILED + SUBMIT_TIMEOUT_UNKNOWN + CLOSED_MARKET_UNKNOWN events first.
- **C1 (F-012 heavy integration)**: cross-MODULE relationship tests covering executor↔command_bus↔chain_reconciliation↔portfolio. Must include SUBMIT_UNKNOWN→recovery→materialize_position invariant chain.

### Settlement K6 (REDEEM_REQUESTED + REDEEMED)

Out of mid-scope; lives in harvester / settlement zone (Down region likely owns).

### Concession to proponent

K=6 is a "structural decision" framing more than K=11 transitions, so Fitz Constraint #1 partially holds. But proponent's K=2 collapse is wrong: payload-binding (K1) and exchange-recon (K5) are NOT collapsible into a §8 state-machine extension — they require schema/method additions, not just state-grammar.

### Disagreement with judge consensus (line 61 of judge_ledger)

Judge ledger says "§8.3 reduces to 3-4 K-decisions." My audit says K=6 (or K=5 if settlement K6 is excluded as out-of-scope). I will defend K=5-6 in L1; if Up region's snapshot/recon work absorbs K5, the residual mid-K is 4 (K1, K2+K3, K4, settlement TBD).

Judge ledger also says "F-001 closed by INV-30+venue_commands" (line 60). My AV-1 disputes: row-state-only is necessary but not sufficient; payload binding required. Will frame as "F-001 PARTIALLY closed; AV-1 residual at the signing boundary."

**Total surface for Mid region: 3 NET_NEW + 3 deferred-discharge = 6 slices**, contradicting proponent's claim that mid-scope is sufficient.

## Confidence

- AV-1, AV-2, AV-3, AV-4, AV-6: HIGH (grep-verified at HEAD 874e00c).
- AV-5: HIGH for axes (a,b,c,d); MEDIUM for axis count (could be 4-6 depending on whether RESTING is a real V2 SDK concept — needs CLOB V2 doc check).
- AV-7: MEDIUM (depends on D2.A scope, which Up-region owns).

Apr26 §3 source review file (line 303-736) was not located on disk; proponent cited line numbers, but the canonical Apr26 review may be a working/uncommitted doc. Routing yaml is the latest persisted summary; using it as authoritative until source review is produced.

## L1 attack ready, L3 deferred

L1 attack queued: 10 adversarial asks already drafted to proponent-mid (sent before judge correction; will resend post-greenlight if needed).
L3 file:line citations all grep-verified within last 15 minutes. Will hold until judge dispatches L3.
