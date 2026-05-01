# Region-Mid R2L2 — Converged

Created: 2026-04-26
HEAD anchor: 874e00cc0244135f49708682cab434b4d151d25d (main)
Region: Mid (state grammar + journal payload + reconciliation + RED-cancel emission)
Layer: 2 (cross-module data-provenance / relationship invariants)
Status: CONVERGED — pending judge accept

Signed-by:
- proponent-mid @ 2026-04-26
- opponent-mid @ 2026-04-26

Turns: 3 each direction.

---

## Consensus

5 L2 OPEN questions from R1L1 all closed at HEAD-grep-verified L2. Plus 1 new negative-constraint mint (NC-NEW-D) covering insert_command emitter allowlist.

## L2 attack disposition (5 attacks + 1 mint)

| # | Attack | Disposition | Lock |
|---|---|---|---|
| A1 | mid-02 ↔ up-04 ALTER consolidation | MERGE into ONE migration | up-04 owns 15-col ALTER (12 + signed_order_hash + payload_hash + signed_at_utc); mid-02 owns event-types + repo write at polymarket_client.py:195-196 |
| A2 | mid-05 ↔ INV-31 boundary | CLEAN SPLIT | INV-31 = single-row `client.get_order(venue_order_id)` lookup at command_recovery.py:80,126,209; mid-05 = enumeration via `get_open_orders` + `get_trades`. Both write via canonical `append_event` helper (single-writer integrity). NEW sibling writer module `src/execution/exchange_reconcile.py` delegates to canonical helper. NEW `exchange_reconcile_findings` table for orphans (NEVER fabricate venue_commands rows). |
| A3 | mid-01 ownership | cycle_runner-PROXY (preserves existing pattern) | riskguard.py:826 unchanged (writes force_exit_review flag); cycle_runner.py:359-373 extension reads flag EARLIER (pre-execution-loop reorder) and emits CANCEL/DERISK commands inline. Authority direction PRESERVED (riskguard signals, cycle_runner emits). cycle_runner gains FIRST `insert_command` import. NC-17 grammar-bound to {CANCEL, DERISK}. |
| A4 | mid-04 K3 RESTING | PAYLOAD-DISCRIMINATION via venue_status enum-string | 0 lifecycle_manager/chain_reconciliation matches on CommandState.RESTING; payload_json.venue_status='RESTING' = 1 string-match site at command_recovery.py post-ACKED handler (~line 175). NO INV-29 closed-law amendment. |
| A5 | signer.sign seam X1 | polymarket_client.py:195-196 interception | py-clob-client OrderBuilder.create_order is unified V1+V2 SDK surface; build+sign fused; NO separate signer.sign(). 3-line insertion BETWEEN line 195 (after `signed = create_order(...)`) and line 196 (`post_order(signed)`): extract signature + compute signed_order_hash + append_event SIGNED_ORDER_PERSISTED. Stable across V1+V2 because OrderBuilder is unified. X1 conditional verdict → APPROVED at L2. |
| MINT | NC-NEW-D `zeus-insert-command-emitter-only` | NEW negative constraint | Allowed `insert_command` Python callers = {executor.py:476 (place_limit_order_with_command_journal), executor.py:815 (execute_exit_order), cycle_runner.py:<TBD line in 359-373 region> (mid-01 RED-cancel emission)}. Mirror NC-16 semgrep shape. |

## R1L1-OPEN closures

| Open item | R2L2 resolution |
|---|---|
| mid-02 ↔ up-04 ALTER consolidation | MERGED. up-04 owns ALL schema; mid-02 owns runtime (event types + writer interception). |
| mid-05 ↔ INV-31 journal-write boundary | CLEAN SPLIT. INV-31 single-row lookup, mid-05 enumeration. Both write via canonical append_event helper. mid-05 sibling-writer module + new findings table for orphans. |
| mid-01 ownership | cycle_runner-PROXY. riskguard signals via existing flag (unchanged); cycle_runner emits commands. Same-cycle latency via flag-read reorder. |
| mid-04 K3 RESTING enum-vs-payload | PAYLOAD-DISCRIM. No INV-29 amendment. 1 string-match site. |
| signer.sign seam | polymarket_client.py:195-196 interception. OrderBuilder.create_order V1+V2 unified surface. Stable seam. |

## NC-NEW-D body (final, ready for paste)

### NC-NEW-D `zeus-insert-command-emitter-only`
- statement: No `insert_command` Python call outside the emitter allowlist (allowed callers: src/execution/executor.py:place_limit_order_with_command_journal, src/execution/executor.py:execute_exit_order, src/engine/cycle_runner.py:_emit_red_cancel_commands_inline (NEW for mid-01)).
- invariants_referenced: [INV-30 (durable command journal), NC-17 (no decorative-capability)]
- semgrep_rule_id: `zeus-insert-command-emitter-only`
- semgrep pattern (mirror NC-16 shape):
  ```yaml
  rules:
    - id: zeus-insert-command-emitter-only
      pattern: insert_command(...)
      paths:
        include: ['src/**/*.py']
        exclude:
          - src/state/venue_command_repo.py  # the writer itself
          - src/execution/executor.py        # allowed: place_limit_order_with_command_journal + execute_exit_order
          - src/engine/cycle_runner.py       # allowed: mid-01 RED-cancel emission
          - tests/**/*.py
      message: "insert_command outside emitter allowlist; see NC-NEW-D"
      severity: ERROR
  ```
- antibody: `tests/test_p0_hardening.py::test_insert_command_emitter_allowlist`
  - assertion:
    ```python
    import subprocess
    result = subprocess.run(
        ['git','grep','-l','insert_command','--','src/'],
        capture_output=True, text=True
    )
    files = set(result.stdout.split()) - {
        'src/state/venue_command_repo.py',  # the writer
        'src/execution/executor.py',         # allowed
        'src/engine/cycle_runner.py',        # allowed (post-mid-01)
        'src/execution/command_recovery.py', # imports it (uses append_event only, not insert_command)
        'src/execution/command_bus.py',      # imports it as type/grammar
    }
    assert files == set(), f"insert_command outside allowlist: {files}"
    ```

## File:line locks (all grep-verified at HEAD 874e00c)

| Slice | File:line | Verified |
|---|---|---|
| mid-01 | `src/engine/cycle_runner.py:359-373` (force_exit_review block extension) | ✓ (reads `get_force_exit_review()` then calls `_execute_force_exit_sweep`; mid-01 inserts CommandBus emission inline before position-mark) |
| mid-01 | `src/riskguard/riskguard.py:826` (UNCHANGED — writes force_exit_review flag) | ✓ |
| mid-01 | `src/riskguard/riskguard.py:1077-1094` (`get_force_exit_review` reader, doc says "Phase 2 item") | ✓ |
| mid-02 | `src/data/polymarket_client.py:195-196` (create_order → post_order interception) | ✓ |
| mid-02 | `src/state/venue_command_repo.py:append_event` (existing helper, used for SIGNED_ORDER_PERSISTED writes) | ✓ |
| mid-02 | `src/execution/command_bus.py:CommandEventType` (extend with SIGNED_ORDER_PERSISTED + COMMAND_PERSISTED enum members) | ✓ |
| mid-03 | `src/execution/command_bus.py:CommandEventType` (extend with SUBMIT_TIMEOUT_UNKNOWN + CLOSED_MARKET_UNKNOWN + CANCEL_FAILED + CANCEL_REPLACE_BLOCKED) | ✓ |
| mid-03 | `src/state/venue_command_repo.py:42-84 _TRANSITIONS` (extend transition table per closed-law amendment) | ✓ |
| mid-04 | `src/execution/command_recovery.py:175 (post-ACKED handler)` (1-site payload-discrim insert) | ✓ |
| mid-05 | `src/execution/exchange_reconcile.py` (NEW module) | (path commitment; module to be created) |
| mid-05 | `src/state/db.py:exchange_reconcile_findings` (NEW table) | (table commitment; schema to be added) |
| mid-05 | `src/execution/command_recovery.py:80,126,209` (boundary-citation: INV-31 single-row only) | ✓ |
| mid-06 | `src/execution/command_bus.py:46-103` (INV-29 closed-grammar; compat-map test target) | ✓ |

## Sequencing graph (R2L2-revised)

```
up-04 (Up region — owns ALL 15 schema columns including mid-02 signing cols)
   ↓
mid-02 (event types + writer interception — no schema work)
   ↓
mid-01 (cycle_runner-proxy CommandBus emission — depends on mid-02 + mid-03)

mid-03 (state grammar amend — independent; blocks mid-01/04/05/06)
   ↓
mid-04 (PARTIAL/RESTING via payload-discrim — depends on mid-03 for event grammar)
   ↓
mid-05 (sibling writer at NEW exchange_reconcile.py — depends on mid-03)
   ↓
mid-06 (relationship tests + INV-29 compat-map — depends on mid-01..05)
```

## Slice card refinements (R2L2)

All 6 mid slice cards updated to reflect:
- mid-02: schema_changes section is a POINTER to up-04; runtime owns event types + writer interception
- mid-05: sibling writer module + canonical append_event delegation; NEW exchange_reconcile_findings table; antibody-boundary citation `command_recovery.py:80,126,209`
- mid-01: emission seam at cycle_runner.py:359-373 extension; riskguard.py:826 unchanged; allowed-emitter allowlist
- mid-04: payload-discrim only; no INV-29 amendment; 1-site test
- mid-02 seam_commitment: `polymarket_client.py:195-196 (post-create_order, pre-post_order)` — 3-line insertion
- mid-03: closed-law amendment list of {SUBMIT_TIMEOUT_UNKNOWN, CLOSED_MARKET_UNKNOWN, CANCEL_FAILED, CANCEL_REPLACE_BLOCKED} event_types + matching state transitions
- mid-06: NC-NEW-D antibody added to relationship test bank

## Cross-region asks (no new for R2L2)

R1L1 cross-region asks (X-UD-1, X-MD-1, X-UM-1) all remain folded; no new asks emerge from L2.

## L3 deferred (cleanly scoped)

- Exact cycle_runner.py file:line for mid-01 emission seam (range cited 359-373; impl-packet pins exact line).
- mid-05 exchange_reconcile_findings table column list (orphan_class enum: ORPHAN_VENUE / ORPHAN_LOCAL; venue_order_id; client_order_id; idempotency_key; observed_at; resolved_by; etc.).
- mid-03 _TRANSITIONS table specific-pair additions per closed-law amendment.
- Ordering of mid-01 within cycle_runner sequence: emission BEFORE position-mark vs AFTER (impl detail; same-cycle latency requires emission-first).
- NC-NEW-D regression antibody fixture (mirrors UP NC-NEW-A pattern from R3L3): tmp_path imposter that does insert_command outside allowlist; semgrep catches it.

## Closure conditions met

- 3 turns each direction (≥2 required) ✓
- A3 ping-pong resolved via T3 first-principles re-examination (cycle_runner-proxy preserves existing pattern; locked) ✓
- All 5 L2 OPEN closed ✓
- NC-NEW-D minted ✓
- Sequencing graph revised on disk ✓
- Slice card refinement summary on disk ✓

L2 closed pending judge accept. Standing by for L3 dispatch (Mid R3L3) or next region.
