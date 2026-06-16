# Fix #123 (M2): Active resolution of stuck `SUBMIT_UNKNOWN_SIDE_EFFECT` venue commands by authenticated-absence sync

- Created: 2026-06-15
- Authority basis: task #123 (M2 active resolution); `src/state/venue_command_repo.py` `_TRANSITIONS`
  state machine; `src/execution/edli_absence_resolver.py` authenticated-absence proof contract;
  `src/risk_allocator/governor.py` `count_unknown_side_effects` kill-switch gate.
- Surface: Tier 0 (live risk-governor / money path). Build + test only; NOT committed to live, NOT deployed.

## 1. Root cause (verified against live, read-only)

The portfolio governor's kill switch was latched, blocking every `buy_no` submit. Verified live against
`state/zeus_trades.db` and `state/zeus-world.db` (opened `mode=ro`):

- The ONLY unresolved `venue_commands` row is `command_id=01049c6a357d4f97`, `state=SUBMIT_UNKNOWN_SIDE_EFFECT`,
  `venue_order_id=NULL`, `token_id=9527...177126`, side BUY, size 75.149, price 0.76, created
  `2026-06-15T16:29:23Z`. Its event log ends at `SUBMIT_TIMEOUT_UNKNOWN` (seq 3) — never resolved.
  `count_unknown_side_effects` counts it → 1 > `unknown_side_effect_limit` (0) → kill switch
  `unknown_side_effect_threshold` → all `buy_no` submits blocked (family-agnostic).
- The EDLI event-sourced ledger (`zeus-world.db` `edli_live_order_events`) HAS the resolution:
  exactly ONE aggregate carries a `Reconciled` event with
  `reconcile_reason=AUTHENTICATED_CLOB_ABSENCE_NO_OPEN_ORDER_OR_TRADE`, `venue_order_exists=0`,
  `venue_trade_exists=0`, and an `authenticated_absence_proof` with `matching_open_order_count=0`,
  `matching_trade_count=0`, `token_id` equal to the command's token, `proof_hash=9e6f3c28…cec9`.
- THE GAP: the EDLI ledger resolved the absence, but the `venue_commands` row was never transitioned
  out of `SUBMIT_UNKNOWN_SIDE_EFFECT`. The governor reads `venue_commands`, not EDLI, so it stays
  latched forever. The two systems are not synced — exactly the `command_state.py:113`
  ("M2 will own active resolution logic") gap.

Live-confirmed no-exposure facts for the stuck row: envelope `raw_response_json=NULL`,
`signed_order_blob=NULL`, `signed_order_hash=NULL`, `order_id=NULL`; 0 `venue_order_facts`,
0 `venue_trade_facts`; no `venue_order_id`.

(Note: the `_TRANSITIONS` table and `UNRESOLVED_SIDE_EFFECT_STATES` live in
`src/state/venue_command_repo.py` in this checkout, not a separate `command_state.py`. The task's
line references map onto it; behavior is identical.)

## 2. Chosen terminal transition — and WHY

**`SUBMIT_UNKNOWN_SIDE_EFFECT → SUBMIT_REJECTED`** (the direct edge in
`venue_command_repo._TRANSITIONS`, `("SUBMIT_UNKNOWN_SIDE_EFFECT", "SUBMIT_REJECTED") → "SUBMIT_REJECTED"`).

`SUBMIT_REJECTED` is terminal and sits **outside** `_UNRESOLVED_SIDE_EFFECT_STATES`
(`{SUBMIT_UNKNOWN_SIDE_EFFECT, UNKNOWN, REVIEW_REQUIRED}`), so terminalizing the row drops
`count_unknown_side_effects` to 0, clears `unknown_side_effect_threshold`, and `buy_no` resumes.

Why NOT the `REVIEW_REQUIRED → REVIEW_CLEARED_NO_VENUE_EXPOSURE → EXPIRED` path:
that clearance is gated by `_validate_review_no_exposure_payload`, whose `_actual_review_clearance_predicates`
require `no_submit_side_effect_events` — i.e. NO `SUBMIT_TIMEOUT_UNKNOWN` / `CLOSED_MARKET_UNKNOWN` /
`SUBMIT_*` event in the row's history. Our row reached `SUBMIT_UNKNOWN_SIDE_EFFECT` precisely BECAUSE
of a `SUBMIT_TIMEOUT_UNKNOWN` event, so that predicate is `False` and the clearance would (correctly)
raise. The `venue_absence_no_exposure` proof_class is by design only for pre-side-effect-boundary
commands (those that came from `SUBMITTING` with reason `recovery_no_venue_order_id`), not for ones
where the submit side-effect boundary WAS crossed. `SUBMIT_REJECTED` is the semantically correct
terminal for "we did contact the venue, outcome was unknown, the venue is now authenticated-confirmed
to hold no order and no trade" — and it carries no heavy payload validator.

This MIRRORS the existing live-venue path in `_reconcile_row`: when a live client read finds the order
absent after the safe-replay window, the row already goes
`SUBMIT_UNKNOWN_SIDE_EFFECT → SUBMIT_REJECTED` with `safe_replay_permitted_no_order_found`. The new pass
is the **complement**: same terminal, but the absence authority comes from the EDLI
`authenticated_absence_proof` instead of a fresh live venue read — which is exactly what was missing
when the live recovery lane could not complete a venue read.

## 3. The EDLI ↔ venue_commands link (confirmed by reading the data)

Canonical link: `Reconciled.payload.execution_command_id == venue_commands.decision_id`.

This is the SAME join key the pre-existing `_reconcile_edli_pre_venue_unknown_thresholds` uses
(`cmd.decision_id = json_extract(unknown.payload_json, '$.execution_command_id')`). Verified live:
the stuck row's `decision_id` equals the `Reconciled.execution_command_id` exactly (both the long
`edli_exec_cmd:…:buy_no` string), and exactly ONE distinct EDLI aggregate links to it. The proof's
`token_id` is used as a secondary cross-check and must equal the command's `token_id`.

## 4. The fix (files + line ranges)

`src/execution/command_recovery.py` (+269 lines):
- Import: added `UNRESOLVED_SIDE_EFFECT_STATES` to the `src.state.venue_command_repo` import block.
- `_EDLI_ABSENCE_SYNC_SOURCE_FUNCTION` constant (L5463) + design comment on the transition choice.
- `_edli_reconciled_absence_for_decision(conn, *, events_ref, decision_id)` (L5466–5533): READ-only
  resolver. Returns `("absent", payload)` only when exactly one aggregate links, the verdict is
  `venue_order_exists=False AND venue_trade_exists=False`, the `reconcile_reason` is the authenticated
  absence reason, and the proof's matching open-order/trade counts are both 0. Returns
  `"exposure"` / `"ambiguous"` / `"absent_none"` otherwise — all fail-closed.
- `_reconcile_venue_command_absence_sync(conn)` (L5535–5709): the new DB-only pass. Scans
  `venue_commands` rows in `{SUBMIT_UNKNOWN_SIDE_EFFECT, UNKNOWN}` with no `venue_order_id`
  (REVIEW_REQUIRED is deliberately excluded — it has its own proof-gated owner and no
  `SUBMIT_REJECTED` edge). For each, resolves the EDLI absence proof, re-checks the proof token
  matches the command token, then appends `SUBMIT_REJECTED` (per-row SAVEPOINT) with a payload that
  cites the EDLI `proof_hash`, the reconcile verdict, and `safe_replay_permitted=True`.
- Registered the pass in BOTH reconciliation lanes, right after `edli_pre_venue_unknown_thresholds`:
  `_reconcile_passes_inline` (legacy caller-owned conn, L6789–6793) and `_reconcile_passes_short_conn`
  (scheduled short-connection lane, as a `_db_pass`, L7071–7072). Summary key:
  `venue_command_absence_sync`.

`tests/test_command_recovery.py` (+260 lines): new `TestEdliAbsenceVenueCommandSync` class with seed
helpers `_seed_edli_reconciled_absence` / `_seed_unknown_side_effect_with_decision` /
`_venue_read_unavailable_client`.

## 5. Fail-closed guarantees

The pass terminalizes a row ONLY when ALL hold (any failure → row left UNCHANGED in its current
unresolved state):
1. Exactly one EDLI aggregate links via `execution_command_id == decision_id`
   (`> 1` → `"ambiguous"` → skip; `0` → `"absent_none"` → skip).
2. `reconcile_reason == AUTHENTICATED_CLOB_ABSENCE_NO_OPEN_ORDER_OR_TRADE`.
3. `venue_order_exists is False` AND `venue_trade_exists is False` (any `True` → `"exposure"` → skip).
4. `authenticated_absence_proof.matching_open_order_count == 0` AND `matching_trade_count == 0`
   (any positive → `"exposure"` → skip).
5. The proof's `token_id` matches the command's `token_id` (mismatch → skip).
6. The command has no `venue_order_id` (rows with a venue order are never in scope).

Absence is NEVER inferred from local `venue_commands` rows; only the EDLI authenticated proof can
discharge a row. The pass never re-queries the venue and never writes the world ledger (read-only on
`world.*`). Any exception per row rolls back that row's SAVEPOINT and is counted as an error, leaving
the row stuck (the safe default). The `unknown_side_effect_limit` (0) is left untouched — the limit is
correct; only the missing sync is fixed.

## 6. INV-37 (cross-DB) compliance

`venue_commands` is in `zeus_trades.db`; the absence proof is in `zeus-world.db`. The pass reads the
proof through the EXISTING `_edli_live_order_events_ref` → `_maybe_attach_world_for_recovery` helper,
which `ATTACH`es `zeus-world.db` as schema `world` onto the SAME single trade connection — never an
independent uncoordinated connection. Each row's terminal write is wrapped in its own SAVEPOINT
(`sp_edli_absence_sync_<command_id>`), matching the cross-DB savepoint discipline already used
throughout this module. No new `ATTACH DATABASE` literal was introduced
(`command_recovery.py` is the allowlisted ATTACH seam per `tests/test_no_raw_world_attach.py`;
the antibody still passes for this file).

## 7. Test results

New tests (`TestEdliAbsenceVenueCommandSync`):
- (a) absence proven (no order, no trade) → row → `SUBMIT_REJECTED`, `count_unknown_side_effects → 0`.
- (b) no EDLI proof (pending) → row UNCHANGED (`SUBMIT_UNKNOWN_SIDE_EFFECT`), count stays 1.
- (c) proof reports a live venue order (`venue_order_exists=true`, matching open order) → row
  UNCHANGED, count stays 1.
- (d) ambiguous link (two aggregates) → fail-closed, row UNCHANGED.
- (e) proof token_id mismatch → fail-closed, row UNCHANGED.

Each test reproduces the real incident by making the in-flight venue read unavailable
(`_venue_read_unavailable_client`), so the EDLI sync pass is the only thing that can discharge the row.

```
$ python3 -m pytest tests/test_command_recovery.py::TestEdliAbsenceVenueCommandSync -q
.....                                                                    [100%]
5 passed in 1.30s

$ python3 -m pytest tests/test_command_recovery.py tests/test_unknown_side_effect.py -q
........................................................................ [ 50%]
.......................................................................  [100%]
143 passed in 10.31s
```

Regression check: my pass scanning the same unresolved population initially broke
`TestRecoveryResolutionTable::test_review_required_is_skipped` (its `summary["stayed"]` rose from 1 to
2 because the pass also (correctly) "stayed" the untouched REVIEW_REQUIRED row). Fixed by scoping the
pass to `{SUBMIT_UNKNOWN_SIDE_EFFECT, UNKNOWN}` only — the states with a legal direct `SUBMIT_REJECTED`
edge — and leaving REVIEW_REQUIRED to its existing proof-gated owner. After the fix that test passes.

Pre-existing baseline failures (verified identical with this change stashed; NOT caused by #123):
`tests/test_risk_allocator.py` (2 — `ModuleNotFoundError: No module named 'sklearn'` via
`src/calibration/platt.py`), `tests/test_venue_command_repo.py` (NC-18 literal in
`src/execution/exchange_reconcile.py:1243`; a `MATCHED` order-fact case),
`tests/test_no_raw_world_attach.py` (ATTACH literal in `src/engine/cycle_runtime.py:3744` — not
`command_recovery.py`), and `tests/scripts/test_resolve_edli_unknown_by_authenticated_absence.py`
(script `build_absence_proof` API drift). None touch the files changed here.

## 8. Risk / uncertainty for the reviewer

- The pass writes `SUBMIT_REJECTED` carrying `safe_replay_permitted=True`. That is consistent with the
  existing live-venue `safe_replay_permitted_no_order_found` precedent and is sound ONLY because the
  authenticated absence proof guarantees the venue holds no order and no trade — so a future replay of
  the same economic intent cannot double-submit. The duplicate-submit guard
  (`find_unknown_command_by_economic_intent`) keys on `_UNRESOLVED_SIDE_EFFECT_STATES`; once the row is
  `SUBMIT_REJECTED` it no longer blocks an honest re-decision, which is the intended outcome
  (the order genuinely never landed).
- The pass trusts the EDLI proof rather than re-reading the venue. This is deliberate (the live read
  lane could not complete) and matches `edli_absence_resolver`'s own contract, but it means correctness
  depends on the EDLI `Reconciled` event being trustworthy. Mitigation: the pass independently re-checks
  `venue_order_exists`/`venue_trade_exists`, both matching counts, the reconcile reason, link
  uniqueness, and the token match before acting; any deviation fails closed.
- Not deployed. A reviewer should confirm the cycle that runs `reconcile_unresolved_commands` will pick
  up the new pass on next run (both lanes are wired), and may wish to dry-run against a copy of the live
  DBs before enabling on the live daemon.
