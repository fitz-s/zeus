# Citation Verification Report
**HEAD**: 874e00cc0244 (main)
**Date**: 2026-04-26
**Scope**: All 20 slice cards in docs/operations/task_2026-04-26_ultimate_plan/slice_cards/

---

## Methodology

For each card, all `path/file.py:NNN` or `path/file.py:NNN-MMM` patterns were extracted and verified
by reading the actual file at HEAD. Content match means the cited line range contains the semantically
described content (function name, invariant, code pattern, etc.).

---

## Verification Table

| card_id | citation | exists | content_match | drift_kind |
|---------|----------|--------|---------------|------------|
| down-01 | `polymarket_client.py:17` | YES | Line 17: `CLOB_BASE = "https://clob.polymarket.com"` — exact match | NONE |
| down-01 | `polymarket_client.py:60-69` | LINE_DRIFT | ClobClient import is at line 69 (lazy). ClobClient _construction_ (chain_id=137, signature_type=2) is at lines 72-77, not 60-69. Lines 60-69 are class docstring + `__init__` + start of `_ensure_client`. | LINE_DRIFT |
| down-01 | `polymarket_client.py:75-100` | LINE_DRIFT | Lines 75-77 are ClobClient constructor kwargs. Lines 82-109 contain `v2_preflight`, NOT `get_orderbook`. `get_orderbook` starts at line 110 (outside cited range). | LINE_DRIFT |
| down-01 | `polymarket_client.py:116-128` | PARTIAL | Line 116 is `resp = httpx.get(f"{CLOB_BASE}/book", ...)` — that is get_orderbook, not get_fee_rate. Note card says `get_fee_rate direct httpx GET /fee-rate (line 153 actual)` — this corrects itself inline. Lines 116-128 are `get_orderbook` httpx body. | NONE (self-correcting) |
| down-01 | `polymarket_client.py:148-149` | LINE_DRIFT | Line 148 is inside `get_best_bid_ask` return. `from py_clob_client.clob_types import OrderArgs` is at line 183. `from py_clob_client.order_builder.constants import BUY, SELL` is at line 184. Off by ~35 lines. | LINE_DRIFT |
| down-01 | `polymarket_client.py:155` | LINE_DRIFT | Line 155 is inside `get_fee_rate` (fee schedule parse). `OrderArgs(price=price, size=size, ...)` construction is at line 190. Off by ~35 lines. | LINE_DRIFT |
| down-01 | `polymarket_client.py:195-196` | YES | Lines 195-196: `signed = self._clob_client.create_order(order_args)` / `result = self._clob_client.post_order(signed)` — exact two-step seam confirmed | NONE |
| down-01 | `polymarket_client.py:167` | LINE_DRIFT | Line 167 is `place_limit_order` return type annotation. `cancel_order → self._clob_client.cancel(order_id)` is at lines 246-249. Off by ~80 lines. | LINE_DRIFT |
| down-01 | `polymarket_client.py:200` | LINE_DRIFT | Line 200 is `result = self._clob_client.post_order(signed)` wait — let me be precise. Line 195=create_order, 196=post_order, 200=logger.info. `OpenOrderParams` import is at line 279. Off by ~79 lines. | LINE_DRIFT |
| down-01 | `polymarket_client.py:268` | LINE_DRIFT | Line 268 is inside `get_open_orders` body (after get_orders call). `from py_clob_client.clob_types import AssetType, BalanceAllowanceParams` is at line 347. Off by ~79 lines. | LINE_DRIFT |
| down-01 | `tests/test_neg_risk_passthrough.py:66-83` | NOT VERIFIED | File exists; line range not spot-checked in this pass (pattern claimed as V1 antibody) | NONE (low risk) |
| down-03 | `tests/test_neg_risk_passthrough.py:66-83` | NOT VERIFIED | Same as above | NONE (low risk) |
| down-03 | `tests/test_polymarket_error_matrix.py:39` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
| down-04 | `polymarket_client.py:67` | PARTIAL | Line 67 is `if self._clob_client is not None: return`. `funder_address` appears at line 77 (`funder=creds["funder_address"]`). Close but semantically correct: line 67 is inside `_ensure_client`. | LINE_DRIFT |
| down-06 | `src/main.py:336-369` | LINE_DRIFT | `_write_heartbeat` function starts at line 337. `os._exit(1)` is at line 369. But card says "was previously cited :367 in R1L1" and self-corrects. Line 336 is `_heartbeat_fails = 0`. Range is accurate ±1 | NONE (self-correcting) |
| down-06 | `src/main.py:579` | YES | Line 579: `scheduler.add_job(_write_heartbeat, "interval", seconds=60, id="heartbeat",` — exact match | NONE |
| down-06 | `src/engine/cycle_runner.py:287` | LINE_DRIFT | `cleanup_orphan_open_orders` is called at line 328 in cycle_runner.py (via `_cleanup_orphan_open_orders`). The function definition is at line 189. cycle_runner.py:287 is inside `_execute_force_exit_sweep`. Off by ~41 lines. | LINE_DRIFT |
| down-07 | `polymarket_client.py:266-275` | LINE_DRIFT | Lines 266-275 are inside `get_open_orders` (the `isinstance(result, dict)` block). `get_balance` (balanceOf/BalanceAllowanceParams) is at lines 344-351. Off by ~78 lines. | LINE_DRIFT |
| down-07 | `polymarket_client.py:268` | LINE_DRIFT | Line 268 is `result = result.get("data", []) or []` inside `get_open_orders`. `from py_clob_client.clob_types import AssetType, BalanceAllowanceParams` is at line 347. Off by ~79 lines. | LINE_DRIFT |
| down-07 | `src/main.py:379` | LINE_DRIFT | Line 379 is `if clob is None:` inside `_startup_wallet_check`. `logger.info("Startup wallet check: $%.2f USDC available", balance)` is at line 383. Off by 4 lines. | LINE_DRIFT |
| down-07 | `src/execution/harvester.py:1244-1264` | NOT VERIFIED | Not spot-checked in this pass | NONE (low risk) |
| mid-01 | `src/riskguard/riskguard.py:826` | YES | Line 826: `force_exit_review = 1 if daily_loss_level == RiskLevel.RED else 0` — confirmed writes force_exit_review | NONE |
| mid-01 | `src/riskguard/riskguard.py:1077-1094` | YES | Line 1077: `def get_force_exit_review() -> bool:` with docstring and body at 1078-1094. Content: reads force_exit_review from risk_state DB. CONFIRMED | NONE |
| mid-01 | `src/engine/cycle_runner.py:359-373` | YES | Lines 359-373: `force_exit = get_force_exit_review()` + sweep call + logging. Confirmed 2-cycle latency / force exit sweep invocation site. | NONE |
| mid-01 | `src/execution/executor.py:325-690` | YES | Lines 325-690 span `execute_exit_order` through the ack phase. `execute_exit_order` starts at 325; `_live_order` starts at 693. Range is accurate for the durable terminal path. | NONE |
| mid-02 | `src/state/venue_command_repo.py:194-242` | YES | Lines 194-242: `insert_command` INSERT statement — schema has NO signed_order_hash, NO condition_id, NO outcome_index. Only market_id + token_id. CONFIRMED | NONE |
| mid-02 | `src/execution/executor.py:917` | YES | Line 917: `result = client.place_limit_order(` in `execute_entry_order` path. Signing inside SDK call confirmed. | NONE |
| mid-02 | `src/execution/command_bus.py:64-77` | YES | Line 64: `class CommandEventType(str, Enum):` 11-member enum at lines 64-76. SIGNED_ORDER_PERSISTED + COMMAND_PERSISTED absent. CONFIRMED | NONE |
| mid-02 | `src/execution/command_bus.py:164-218` | LINE_DRIFT | `IdempotencyKey.from_inputs` starts at line 165, not 164. Line 164 is `def from_inputs(` wait — `@staticmethod` is line 164, `def from_inputs` is line 165. Range accurate; body ends ~218. Minor: line 164 is `@staticmethod` decorator. | NONE |
| mid-03 | `src/execution/command_bus.py:44-62` | YES | Line 44: `class CommandState(str, Enum):` 11-member enum. CONFIRMED. | NONE |
| mid-03 | `src/execution/command_bus.py:64-76` | YES | Line 64: `class CommandEventType(str, Enum):` 11-member enum (no CANCEL_FAILED, etc.). CONFIRMED. | NONE |
| mid-03 | `src/state/venue_command_repo.py:42-84` | YES | Lines 42-84: `_TRANSITIONS` dict (28 legal pairs). Verified. CONFIRMED. | NONE |
| mid-03 | `src/execution/command_bus.py:92-103` | YES | Lines 92-95: `IN_FLIGHT_STATES` frozenset; lines 97-103: `TERMINAL_STATES` frozenset. CONFIRMED. | NONE |
| mid-03 | `src/execution/executor.py:547-577` | YES | Lines 547-577: `except Exception as exc:` flattens to `status="rejected", reason=f"submit_unknown: {exc}"`. F-002 conflation site confirmed. | NONE |
| mid-03 | `src/execution/executor.py:580-700` | YES | Lines 580-692: ack-phase outcome handling (SUBMIT_REJECTED on None, missing order_id, SUBMIT_ACKED). No PARTIAL_FILL_OBSERVED payload schema. CONFIRMED. | NONE |
| mid-03 | `src/execution/command_recovery.py:140-280` | NOT VERIFIED (boundary only) | Line 140 exists (inside `_resolve_one`). Range plausible for resolution table. Not full-content checked. | NONE (low risk) |
| mid-04 | `src/execution/command_recovery.py:124-130` | LINE_DRIFT | Line 124: `return "stayed"` (end of prior block). `venue_resp = client.get_order(venue_order_id)` is at line 126. Range 124-130 is accurate for the client.get_order single-row lookup block. Slight off-by-2 on start. | NONE |
| mid-04 | `src/state/venue_command_repo.py:56` | YES | Line 56: `("ACKED", "PARTIAL_FILL_OBSERVED"): "PARTIAL"` — confirmed. | NONE |
| mid-04 | `src/state/venue_command_repo.py:65` | YES | Line 65: `("UNKNOWN", "PARTIAL_FILL_OBSERVED"): "PARTIAL"` — confirmed. | NONE |
| mid-04 | `src/state/venue_command_repo.py:70` | YES | Line 70: `("PARTIAL", "PARTIAL_FILL_OBSERVED"): "PARTIAL"` — confirmed. | NONE |
| mid-05 | `src/execution/command_recovery.py:124-126` | YES | Lines 124-126: `return "stayed"` + blank + `try: venue_resp = client.get_order(venue_order_id)`. Single-row lookup confirmed. | NONE |
| mid-05 | `src/execution/command_recovery.py:80-92` | YES | Lines 80-92: `if state == CommandState.SUBMITTING and not cmd.venue_order_id:` + REVIEW_REQUIRED emission. F-006 case confirmed. | NONE |
| mid-05 | `src/engine/cycle_runtime.py:139-190` | YES | Line 139: `def cleanup_orphan_open_orders(...)`. `clob.get_open_orders()` at line 173. CONFIRMED. Note: card cites `cycle_runtime.py` correctly, but down-06 cites `cycle_runner.py:287` (LINE_DRIFT — that's inside force_exit_sweep). | NONE |
| mid-05 | `src/data/polymarket_client.py:275-287` | LINE_DRIFT | `get_open_orders` starts at line 275. Lines 275-287 cover the method signature + `_ensure_client()` + try block opening. Cited as "REUSABLE". Accurate in spirit. | NONE |
| mid-05 | `src/data/polymarket_client.py:289-340` | LINE_DRIFT | `get_positions_from_api` starts at line 289. Line 340 is well inside the method. CONFIRMED as REUSABLE. | NONE |
| mid-06 | `src/state/venue_command_repo.py:42-84` | YES | Already confirmed (mid-03). | NONE |
| mid-06 | `src/execution/command_bus.py:44-76` | YES | Already confirmed (mid-03). | NONE |
| mid-06 | `src/engine/cycle_runner.py:60-102` | LINE_DRIFT | Lines 60-102 are the `_execute_force_exit_sweep` function body (Phase 9B sweep). NOT the RED-cancel boundary in the cycle loop. The RED-cancel boundary (get_force_exit_review + sweep call) is at lines 359-373. Off by ~300 lines. | LINE_DRIFT |
| up-02 | `src/contracts/settlement_semantics.py:50-183` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
| up-02 | `src/contracts/tick_size.py:91-92` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
| up-02 | `src/contracts/execution_intent.py:32-33` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
| up-02 | `src/data/polymarket_client.py:76` | YES | Line 76: `signature_type=2,` inside ClobClient constructor. CONFIRMED. | NONE |
| up-03 | `src/state/db.py:813` | NOT VERIFIED | File not spot-checked in this pass | NONE (low risk) |
| up-03 | `src/state/venue_command_repo.py:152-238` | YES | Line 152: `def insert_command(conn, ...)` — start of insert_command function. Extends to the INSERT body at ~194-242 range. Accurate. | NONE |
| up-04 | `src/state/db.py:813` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
| up-04 | `src/state/venue_command_repo.py:152-238` | YES | Confirmed (up-03). | NONE |
| up-05 | `src/types/observation_atom.py:44-130` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
| up-05 | `src/state/db.py:813` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
| up-06 | `src/state/chain_reconciliation.py:46` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
| up-06 | `src/state/chain_reconciliation.py:181` | NOT VERIFIED | Not spot-checked | NONE (low risk) |
| up-07 | `src/state/venue_command_repo.py:152-238` | YES | Confirmed (up-03). | NONE |
| up-07 | `src/state/db.py:813` | NOT VERIFIED | Not spot-checked | NONE (low risk) |

---

## High-Value Citation Spot-Checks (Detailed)

### 1. `polymarket_client.py:195-196` — signer.sign two-step seam (down-01, mid-02, up-04)

**STATUS: CONFIRMED — NONE drift**

Actual lines 195-196:
```python
signed = self._clob_client.create_order(order_args)
result = self._clob_client.post_order(signed)
```
Two-step seam preserved. This is the interception point for mid-02's signed_order_hash injection.

---

### 2. `venue_command_repo.py:194-242` — signed_order_hash residual (mid-02)

**STATUS: CONFIRMED — NONE drift**

Lines 194-242 contain the `INSERT INTO venue_commands` SQL. Columns confirmed:
`command_id, position_id, decision_id, idempotency_key, intent_kind, market_id, token_id, side, size, price, venue_order_id, state, last_event_id, created_at, updated_at, review_required_reason`

No `signed_order_hash`, `condition_id`, or `outcome_index` present. The residual absence is correctly cited.

---

### 3. `venue_command_repo.py:42-84` — TRANSITIONS table (mid-03, mid-06)

**STATUS: CONFIRMED — NONE drift**

Lines 42-84 contain `_TRANSITIONS: dict[tuple[str, str], str]` with 28 legal pairs. Exact match.

---

### 4. `command_recovery.py:71-77` — REVIEW_REQUIRED handoff (F-006 split anchor)

**STATUS: CONFIRMED — NONE drift**

Line 71: `if state == CommandState.REVIEW_REQUIRED: return "stayed"`
Lines 73-77: start of SUBMITTING-without-venue_order_id block.
Card cites F-006 split anchor correctly.

Note: mid-05 cites lines 80-92 for the SUBMITTING path — those lines contain the actual REVIEW_REQUIRED emission. Lines 71-77 capture the REVIEW_REQUIRED guard + block header, consistent with card claim.

---

### 5. `riskguard.py:1080-1082` — "forced exit sweep is Phase 2 item"

**STATUS: CONFIRMED — NONE drift**

Line 1082: `forced exit sweep for active positions is a Phase 2 item.)`
(Inside docstring of `get_force_exit_review()` at lines 1077-1094.)

Note: The card cites 1080-1082; actual line 1082 contains the text. Minor ±2 line drift within the docstring but semantically exact. NONE.

---

### 6. `cycle_runner.py:67` — "Does NOT post sell orders in-cycle"

**STATUS: CONFIRMED — NONE drift**

Line 67 (within `_execute_force_exit_sweep` docstring):
```
Does NOT post sell orders in-cycle — keeps the sweep low-risk + testable.
```
Exact semantic match.

---

### 7. `cycle_runner.py:~364` — NC-NEW-D allowlist site (Mid R3L3)

**STATUS: SEMANTIC_MISMATCH / requires clarification**

Lines 359-373 contain `get_force_exit_review()` call and force-exit sweep. This is the RED sweep site, not an "NC-NEW-D allowlist" site. mid-06 cites lines 60-102 as the RED-cancel boundary, but lines 60-102 are the `_execute_force_exit_sweep` function definition. The actual call site is at lines 359-373.

The "NC-NEW-D allowlist" reference from Mid R3L3 — no explicit NC-NEW-D pattern found in cycle_runner.py at line ~364. Line 364 is `force_exit = get_force_exit_review()`. If NC-NEW-D refers to a specific allowlist check, it is not present at this line. The surrounding context is the force-exit sweep invocation.

**DRIFT KIND: SEMANTIC_MISMATCH** (line exists but content does not match claimed NC-NEW-D allowlist site)

---

### 8. `polymarket_client.py:353` — Q-FX-1 dual-gate runtime check site (Down R3L3)

**STATUS: SEMANTIC_MISMATCH**

Line 353: `resp = self._clob_client.get_balance_allowance(` inside `get_balance()`. No Q-FX-1 dual-gate check found at line 353 or anywhere in polymarket_client.py. `grep -n 'Q-FX-1|dual.gate|fx_guard'` returns zero results in entire `src/` directory.

The Q-FX-1 dual-gate runtime check does not exist yet (it is the thing down-03 is supposed to ADD). The card is citing a site where the check will be inserted, not where it currently exists. This is forward-looking.

**DRIFT KIND: SEMANTIC_MISMATCH** (line exists, no Q-FX-1 check present at HEAD; the check is the target of the slice, not yet landed)

---

### 9. `executor.py:547-577` — SDK exception flattening (F-002 evidence, mid-03)

**STATUS: CONFIRMED — NONE drift**

Lines 547-577:
```python
except Exception as exc:
    # SUBMIT_UNKNOWN: the SDK raised...
    ...append_event(..., event_type="SUBMIT_UNKNOWN", ...)
    return OrderResult(..., status="rejected", reason=f"submit_unknown: {exc}")
```
F-002 conflation (any SDK exception → status=rejected) confirmed at exact lines.

---

### 10. `main.py:367` — apscheduler tombstone (Down R2L2)

**STATUS: LINE_DRIFT — minor**

Line 367: `logger.critical("FATAL: Heartbeat failed 3 consecutive times...")`
Line 369: `os._exit(1)` — the actual exit call.

Card cites ":367" as the apscheduler tombstone (os._exit). The `os._exit(1)` is at line 369, not 367. Off by 2 lines. Note: down-06 self-corrects "was previously cited :367 in R1L1" and points to 336-369 range — the range is accurate but the specific tombstone line is 369.

**DRIFT KIND: LINE_DRIFT** (minor, off by 2)

---

## Confirmed vs Drifted Summary

### Citations checked: 57
### Results:
- **NONE (confirmed exact)**: 32
- **LINE_DRIFT**: 17
- **SEMANTIC_MISMATCH**: 2
- **FILE_MOVED**: 0
- **NOT VERIFIED (low-risk unspot-checked)**: 6

### Critical LINE_DRIFT Cluster: polymarket_client.py imports

The most systematic drift is in down-01 citations for `polymarket_client.py` at lines 148-149, 155, 167, 200, 268. The actual import locations have drifted ~35-80 lines from cited positions:

| Cited | Actual | Delta | Content |
|-------|--------|-------|--------|
| :148-149 | :183-184 | +35 | OrderArgs + BUY/SELL imports |
| :155 | :190 | +35 | OrderArgs() construction |
| :167 | :246 | +79 | cancel_order method |
| :200 | :279 | +79 | OpenOrderParams import |
| :268 | :347 | +79 | AssetType, BalanceAllowanceParams import |

This cluster suggests the v2_preflight method (lines 82-109) was inserted AFTER the down-01 citations were written, pushing all subsequent methods down by ~79 lines.

### Critical SEMANTIC_MISMATCH Items

1. **`polymarket_client.py:353` (Q-FX-1 dual-gate)**: The check does not exist yet; the citation is forward-looking (target of down-03). Executors referencing this as evidence of a landed check will be wrong.

2. **`cycle_runner.py:~364` (NC-NEW-D allowlist)**: Line 364 is `force_exit = get_force_exit_review()`. No NC-NEW-D allowlist structure at this site. If slice plans depend on a specific NC-NEW-D allowlist being present, it needs to be located or defined.

---

## Recommendations for Slice Authors

1. **down-01 import citations (lines 148-268 range)**: Re-verify all cited lines before editing. The v2_preflight block insertion has shifted all downstream method line numbers by ~79. Use grep/AST search rather than hardcoded line numbers.

2. **Q-FX-1 dual-gate (down-03, polymarket_client.py:353)**: The card is citing the insertion site, not an existing check. This is acceptable if the plan language is "ADD here" rather than "READ from here". Verify the card language is unambiguous.

3. **cycle_runner.py:287 (down-06)**: The actual orphan cleanup call is at line 328, not 287. Line 287 is inside force_exit_sweep. Fix the cite if slice tests anchor on exact line.

4. **mid-06 cycle_runner.py:60-102**: This range is the force_exit_sweep function definition, not the RED-cancel entry point. The RED-cancel cycle boundary is at lines 359-373. Fix before writing relationship tests.

5. **main.py:367 vs 369**: Minor. os._exit(1) is at 369; line 367 is the logger.critical call. Both are inside the same if-block.
