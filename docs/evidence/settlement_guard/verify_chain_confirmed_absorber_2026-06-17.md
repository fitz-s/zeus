# verifier proof-of-done for chain-confirmed-active-holding position_drift absorber

HEAD: 97b3b8a60a3f612988e9766e2a9d421e3f04dac4
Branch: live/iteration-2026-06-13
Verifier: verifier (independent; writer ≠ reviewer)
Date: 2026-06-17

## Claim
A new absorber in `src/execution/exchange_reconcile.py` resolves the `position_drift`
finding for a ws_gap-era fill confirmed only on-chain (`chain_state='synced'`,
`chain_shares=10.86`) but never journaled (`confirmed_journal=0`), unfreezing the M5
submit latch — while keeping a genuine drift (no chain holding / size mismatch / non-synced)
an OPEN fail-closed finding.

## Verdict
**VERIFIED (PASS)** — with ONE non-blocking residual noted under CHECK 6. The change is
correct and safe: the absorber cannot mask a real loss (tolerance 0.0001 share, requires
fresh exchange == on-chain), the chain_shares provenance is a genuine on-chain data-api read
(not a Zeus projection), the held-token mapping is canonical (`no_token_id` for buy_no), the
placement is disjoint from every sibling absorber, and all tests pass with correct RED-on-revert.

---

## CHECK 1 [STATUS: VERIFIED] — Safety / no-loss-masking
The absorber resolves ONLY when `_chain_confirmed_size > 0 AND _position_size_matches(exchange, chain_confirmed)`.
- `_position_size_matches` (src/execution/exchange_reconcile.py:2719-2720):
  `return abs(left - right) <= _POSITION_DRIFT_ABS_TOLERANCE`
- `_POSITION_DRIFT_ABS_TOLERANCE = Decimal("0.0001")` (line 107). 0.0001 share is far below any
  economically meaningful drift; it cannot mask theft/partial-loss. Seoul mismatch (10.86 vs 5.0)
  = 5.86 >> 0.0001 → stays open (proven by `test_size_mismatch_stays_open_finding`).
- Helper SQL gates `chain_state='synced' AND COALESCE(chain_shares,0) > 0` (lines ~4444-4452).
  A non-synced state (size_mismatch_unresolved / quarantined / unknown) yields no entry → no
  absorption → finding stays open.
- On-chain ABSENT (chain_confirmed=0): guard `_chain_confirmed_size > 0` is False → finding stays
  open (proven by `test_no_chain_holding_stays_open_finding`).
- Direction of safety: the EXCHANGE side of the equality is the FRESH venue read; a real
  loss/theft lowers the fresh exchange size, breaking equality against the persisted
  chain_shares → finding kept. Fail-closed.
VERDICT: cannot mask a real loss/theft/partial-drift. PASS.

## CHECK 2 [STATUS: VERIFIED] — chain_shares provenance (genuine on-chain, not optimistic)
`chain_shares` / `chain_state='synced'` are written by the chain reconciler from the on-chain read:
- src/state/chain_reconciliation.py:967 `chain_by_token = {cp.token_id: cp for cp in chain_positions}`;
  matched/rescue branch sets `rescued.chain_state="synced"` + `rescued.chain_shares = chain.size`
  (lines 1154-1155). `chain.size` is `ChainPosition.size`.
- `ChainPosition` is built in src/engine/cycle_runtime.py:1701-1719 `chain_positions_from_api`
  from `row.get("size")` of the data-api payload, fed by `clob.get_positions_from_api()`
  (cycle_runtime.py:1824).
- `get_positions_from_api` (src/data/polymarket_client.py:791-844) GETs
  `{DATA_API_BASE}/positions?user=<funder>&sizeThreshold=0.01` — the venue's authoritative
  on-chain CTF balance for the wallet. NOT a Zeus optimistic projection.
- `chain_state='synced'` is set ONLY on a MATCH against this on-chain read; a size disagreement
  routes to `size_mismatch_unresolved`/quarantine (not synced). So `synced` genuinely asserts
  "balanceOf confirmed".
CAVEAT (not a defect): the reconciler's `exchange` read (`adapter.get_positions()`,
src/venue/polymarket_v2_adapter.py:623-650) hits the SAME data-api `/positions` endpoint. So the
equality compares a FRESH data-api read (exchange) against a PERSISTED earlier data-api read
(chain_shares) — same surface at two times, not two independent oracles. The doc comment "both
venue surfaces agree" slightly overstates independence. The SAFETY property still holds because
the exchange side is the fresh read and a real loss shows there first. PASS (provenance is
on-chain; the equality is not dangerously circular because chain_shares is a venue read, not a
Zeus-derived projection of the exchange position).

## CHECK 3 [STATUS: VERIFIED] — Held-token mapping (buy_no → no_token_id)
- New helper keys by `row["no_token_id"] if direction=="buy_no" else row["token_id"]` (helper body).
- Canonical confirmation: `_held_token_id_from_position_row` (src/state/portfolio.py:1850-1854)
  returns `no_token_id` for buy_no. The existing sibling `_closed_position_token_holdings_by_token`
  (line 4409) uses the IDENTICAL expression — the new helper mirrors established, tested behavior.
- The live finding's subject_id (8804…2593) is the held NO token for the Seoul buy_no position,
  matching `chain_shares` keyed by no_token_id. The test fixture uses exactly this token.
PASS.

## CHECK 4 [STATUS: VERIFIED] — No regression to sibling absorbers (disjoint placement)
Insert is right after the `_token_is_suppressed_external` check, BEFORE all journal/settlement/
closed/operator/terminal absorbers, on BOTH paths (recorder ~1897-1913; resolver ~2524-2541).
Disjointness by the `exchange_size` discriminant:
- `_absorb_terminal_chain_closed_phantom` returns False immediately when `exchange_size > 0`
  (lines 2267-2268). Ours fires ONLY when exchange>0. Mutually exclusive.
- `_absorb_operator_external_close` requires an operator-ack row (returns False without one,
  lines 1162-1164) AND `exchange_size < journal_long` with positive journal/closed evidence
  (lines 1169-1172). Our case has journal=0 → it returns False anyway; and it sits AFTER ours
  in sequence so it is never reached for the chain-confirmed token.
- `position_drift_cleared` (line 1925) would also resolve a token where exchange==available_wallet;
  if that token additionally had chain_confirmed==exchange, ours resolves it first with a
  different RESOLUTION STRING only — the finding-resolved OUTCOME is identical, no functional change.
- The recorder `tokens` union (lines 1878-1884) is UNCHANGED (does not add chain_confirmed), so no
  new token is dragged into the loop; the absorber only short-circuits tokens already present via
  the exchange/journal/settlement/closed/sell-lock surfaces.
PASS.

## CHECK 5 [STATUS: VERIFIED] — Tests + RED-on-revert
New suite (`.venv/bin/python -m pytest tests/execution/test_chain_confirmed_active_holding_absorber.py`):
6 passed (4 positive-absorption + 2 honest-gate). Note: dispatch said "4 chain-confirmed tests";
the file has 6 total (the 4 absorption tests are the chain-confirmed ones).

Full reconcile regression suite — 142 passed, 0 failed:
  tests/execution/test_terminal_chain_closed_phantom_absorber.py
  tests/test_exchange_reconcile.py
  tests/test_ws_boot_latch_partial_order_deadlock.py
  tests/test_reconcile_foreign_wallet_orders.py
  tests/test_reconcile_operator_acknowledged_orders.py
  tests/test_reconcile_operator_external_close.py
  tests/test_reconcile_pending_redeems_batch_cap.py

RED-on-revert (neutralized guard `if False and _chain_confirmed_size > 0 …` on BOTH call sites
in a scratch copy; backup at /tmp/exchange_reconcile.backup.py, file restored byte-identical
afterward — git diff back to +86):
  FAILED test_full_sweep_does_not_record_chain_confirmed_active_holding
  FAILED test_full_sweep_resolves_preexisting_stuck_finding
  FAILED test_refresh_path_resolves_chain_confirmed_active_holding
  FAILED test_zero_unresolved_after_absorption
  PASSED test_no_chain_holding_stays_open_finding   (honest-gate: stays open even absorber-off)
  PASSED test_size_mismatch_stays_open_finding      (honest-gate: stays open even absorber-off)
→ Exactly the 4 absorption tests are RED on revert; the 2 honest-gate tests assert the finding
  STAYS OPEN and pass independent of the absorber. This proves the honest gates test the
  fail-closed surface, not a coincidental green. PASS.

## CHECK 6 [STATUS: VERIFIED, with non-blocking residual]
Trace `run_ws_gap_reconcile_and_clear` (lines 290-344): when `m5_reconcile_required` →
`run_reconcile_sweep` → `_record_position_drift_findings`. The absorber resolves the Seoul finding
inside the recorder, so it is NOT in `findings`; `list_unresolved_findings` (counts ALL kinds)
returns 0 (the dispatch confirms this is the ONLY unresolved finding) → passes the
`if findings or unresolved` gate → `conn.commit()` → `clear_after_m5_reconcile`. End-to-end
clearing confirmed (proven by `test_zero_unresolved_after_absorption`).
Remaining gates after resolution: only `"trades" in snapshot.captured_surfaces` (a fresh-read
availability gate, orthogonal to this finding). No other latch blocker for this finding.

NON-BLOCKING RESIDUAL (flag, not a fail): the recorder's `tokens` union does NOT include
`chain_confirmed_active_holdings`. The Seoul token enters the loop via `set(exchange)` only
because the FRESH exchange read reports 10.86. If a transient data-api read drops the position
(e.g. below sizeThreshold or a flaky response) during the M5 sweep, the token enters NEITHER the
exchange set NOR (since it is not unioned) the chain-confirmed set, so the recorder does not
iterate it and the ALREADY-OPEN finding is not resolved that cycle → latch stays frozen until the
next sweep where exchange reports it again. This is the fail-closed direction (a missing venue
read should not auto-resolve), is NOT the live scenario, and self-heals on the next good read; the
refresh path `_resolve_position_drift_tokens_from_current_truth` is driven by unresolved-finding
token_ids and DOES always evaluate the stuck token, but `run_ws_gap_reconcile_and_clear` runs the
recorder, not the resolver. Suggest (optional, non-blocking): union `chain_confirmed_active_holdings`
into the recorder `tokens` set so the absorber is robust to a transient fresh-exchange miss. Does
not block the fix for the reported defect. PASS.

## Missing evidence
None required for the PASS verdict. (Optional hardening only: the CHECK 6 residual.)

## Regressions
None. 142/142 reconcile-suite tests pass; new suite 6/6; RED-on-revert exactly the 4 absorption tests.
