# B1 — Swept-winner latch freeze: terminal-chain-closed phantom absorber

Created: 2026-06-13
Authority basis: live incident 2026-06-13 (M5 ws-gap submit latch frozen since
2026-06-12T22:58Z); regression of completed task #31 "Settled-class external-close
absorber" (commit 6629d35a54, 2026-06-11). Prepared in an isolated worktree — NOT deployed.

## One-line root cause

The settled-external absorber (task #31) was added ONLY to the M5 *refresh* path and
recognizes a swept winner ONLY via a market-calendar +24h buffer; the *full-sweep* path that
`run_ws_gap_reconcile_and_clear` actually runs has no settled absorber at all, so a settled
winner swept off-chain before its market's +24h tick (live: Denver 06-12, token `25998072…`)
is re-recorded as a blocking `position_drift` every sweep and freezes `allow_submit=False`.

## Exact mechanism (file:line)

Latch chain:
- `src/execution/executor.py:84` `_assert_ws_gap_allows_submit` → `assert_ws_allows_submit`
  (`src/control/ws_gap_guard.py:345`). The guard stays `m5_reconcile_required=True` (blocks
  submit) until `clear_after_m5_reconcile` is called with **zero unresolved findings**
  (`ws_gap_guard.py:240-245`).
- `src/main.py:2386` `run_ws_gap_reconcile_and_clear` → `run_reconcile_sweep`
  (`exchange_reconcile.py:499`) → `_record_position_drift_findings`
  (`exchange_reconcile.py:1857`). This is the path that runs while the latch is closed. ONE
  unresolved finding here keeps the latch shut (`run_ws_gap_reconcile_and_clear`
  `exchange_reconcile.py:331-333`).

The frozen finding `5bbc2be2` (kind `position_drift`, context `ws_gap`, `resolved_at NULL`)
is written by `_record_position_drift_findings` at `exchange_reconcile.py:2009-2038`
(`reason=exchange_position_differs_from_expected_wallet_facts`). Its inputs, verified against
the live `zeus_trades.db`:
- `exchange_size=0` (venue holds nothing for the token);
- `confirmed_wallet_size=17.05` — `_journal_positions_by_token` CONFIRMED long
  (`exchange_reconcile.py:4016`); the entry buy has no matching active position row, so the
  LEFT-JOIN `pc.position_id IS NULL` branch includes it;
- `closed_position_token_size=17.05` — `_closed_position_token_holdings_by_token`
  (`exchange_reconcile.py:4139`): there IS a `position_current` row
  (`position_id=edli789b…`, `phase=voided`, `chain_state=synced`, `direction=buy_no`,
  `no_token_id=25998072…`, `shares=17.05`, `exit_reason=PHANTOM_NOT_ON_CHAIN`). The position
  was voided by `chain_reconciliation` Rule 2 when its CTF tokens left the wallet (the
  third-party auto-redeemer swept the settled winner);
- `expected_wallet_size = available(17.05) + 0 + closed(17.05) = 34.10` ≠ exchange 0 → drift.

Why task #31 never clears it (the regression, two compounding gaps):
1. **Full-sweep path has no settled absorber.** `_record_position_drift_findings`
   (`exchange_reconcile.py:1857-2039`) carries only the operator-ack absorber
   `_absorb_operator_external_close` (`:1994`). The settled-external calendar absorber lives
   ONLY in the refresh path `_resolve_position_drift_tokens_from_current_truth`
   (`exchange_reconcile.py:2432-2473`, added by commit 6629d35a). So every ws-gap full sweep
   re-records `5bbc2be2`.
2. **Calendar absorber is blind before +24h.** Even the refresh-path absorber gates on
   `_market_calendar_terminal_evidence` with `_SETTLED_EXTERNAL_TERMINAL_BUFFER_HOURS=24`.
   Denver local day 06-12 ends 06-13T06:00Z; +24h ⇒ terminal only from **06-14T06:00Z**. The
   latch froze 06-12T22:58Z — a ~31h blind window in which neither path could absorb. (The
   NO-side token is reachable only via the condition_id bridge, condition
   `0xaa77bbce…` → Denver 06-12; verified live.)

The on-chain terminal CLOSE was already provable from evidence in hand the whole time: venue
size 0 against a terminal (voided/synced) chain-holding row IS the external close — it does
not need the calendar proxy.

## Fix (systemic, recurrence-proof)

New absorber `_absorb_terminal_chain_closed_phantom` (`exchange_reconcile.py`, after
`_SETTLED_EXTERNAL_RESOLUTION`). It recognizes a swept-winner phantom from two independent
terminal signals, fail-closed on both:
1. **On-chain terminal close:** `exchange_size <= 0` AND `open_sell_locked_size <= 0` AND
   `closed_position_size > 0` (a terminal voided/settled/admin_closed chain-holding row).
2. **Market settledness:** day-end market-calendar evidence (`buffer_hours=0`) — the market's
   target local day has ENDED. Signal (1) already proves the tokens left the wallet, so the
   +24h venue-lag margin is redundant; day-end is sufficient.

On match it registers `token_suppression('settled_position')`
(`source_module=exchange_reconcile.terminal_chain_closed_phantom_absorber`) and resolves the
finding (`resolution=position_drift_terminal_chain_closed_phantom_suppressed`). The existing
suppression door `_token_is_suppressed_external` (`:4208`, checked at the top of both drift
loops) keeps it resolved on every future sweep → idempotent. **No synthetic money is booked**
(unlike the operator-ack absorber): settlement P&L stays with the settlement organs + the
Confirm-pending-deposit check; only the drift/latch accounting is corrected.

Requiring signal (2) cleanly separates a SETTLED-winner sweep from an OPERATOR-MANUAL
open-market sale (the `_absorb_operator_external_close` domain, commit 57c441049d): the
operator-sale market is not day-ended / not in the registry → no day-end evidence → stays on
the strict operator-ack path. This is why `tests/test_reconcile_operator_external_close.py`'s
open-market negative controls remain valid (their `condition-m5` market is absent from the
registry).

Wired on BOTH paths:
- Full sweep `_record_position_drift_findings`: inserted after the operator-ack absorber,
  before the terminal `findings.append(...)`; passes
  `settled_terminal=_day_end_terminal_evidence_for_token(conn, token, observed_at)`.
- Refresh path `_resolve_position_drift_tokens_from_current_truth`: inserted before the
  calendar settled-external branch (direct on-chain proof takes precedence); a batch
  `day_end_terminal` map is computed alongside the existing `calendar_terminal`.

Supporting changes:
- `_market_calendar_terminal_evidence` gains `buffer_hours: float = 24` (default preserves the
  task #31 calendar absorber behavior exactly).
- New helper `_day_end_terminal_evidence_for_token` (per-token, `buffer_hours=0`, bridge-aware,
  fail-closed).

### Diff summary
- `src/execution/exchange_reconcile.py`:
  - `+ _TERMINAL_CHAIN_CLOSED_RESOLUTION` constant.
  - `+ _absorb_terminal_chain_closed_phantom(...)` (the absorber).
  - `+ _day_end_terminal_evidence_for_token(...)`.
  - `_market_calendar_terminal_evidence(... , buffer_hours=...)` parameterized.
  - 2 call sites wired (full-sweep + refresh) + `day_end_terminal` batch computation.
- `tests/execution/test_terminal_chain_closed_phantom_absorber.py` (new, 9 tests).

## New test

`tests/execution/test_terminal_chain_closed_phantom_absorber.py` — RELATIONSHIP tests over
`reconcile drift detector → token_suppression → finding resolution → ws_gap latch`. Uses the
live Denver shape (NOW=06-13T12:00Z: day-ended but NOT +24h, i.e. inside the freeze window):
- full sweep absorbs + suppresses (no blocking finding);
- full sweep resolves the pre-existing stuck `5bbc2be2`-shape finding by absorption;
- refresh path resolves it;
- idempotent via the suppression door (one history row; second resolution comes through the
  door);
- end-to-end: zero unresolved findings → `clear_after_m5_reconcile` reopens the latch
  (`blocks_market` False);
- honest-gate / RED-on-revert guards: NO terminal holding → stays open; open sell lock →
  blocked; **open (un-settled) market → stays open** (operator-ack only); registry unavailable
  → stays open.

## Test results

- New suite: `9 passed`.
- RED-on-revert (neutralize `_absorb_terminal_chain_closed_phantom`): 5 latch-relevant tests
  FAIL, the 4 fail-closed tests still pass — as intended.
- Existing reconcile/ws-gap/executor suite (incl. the operator-external-close negative
  controls): `147 passed`, no regressions.
- Pre-existing unrelated failures (`test_architecture_contracts.py`,
  `test_phase10a_hygiene.py` — `src/calibration/platt.py` ModuleNotFoundError + harvester
  state) reproduce on baseline WITHOUT this change; not caused by this fix.

### End-to-end validation against the live `zeus_trades.db` (read-only copy)

With `ZEUS_FORECASTS_DB_PATH` pointed at the live registry and a realistic venue snapshot
(the one active day0 position present, the swept winner absent):
- BEFORE corrected sweep: 1 unresolved finding (`5bbc2be2`).
- corrected `_record_position_drift_findings`: logs `terminal_chain_closed_phantom … on
  settled market highest-temperature-in-denver-on-june-12-2026 …`; `5bbc2be2` →
  `resolved_at=2026-06-13T12:00:00Z`, `resolution=position_drift_terminal_chain_closed_phantom_suppressed`;
  `token_suppression('settled_position')` registered; **zero new findings; zero unresolved →
  latch can reopen.**
- (An empty-wallet sim spuriously drifted active token `94801573…`; with the real venue
  snapshot that position is present and does not drift — the absorber correctly does NOT touch
  it. Confirms the honest-gate: active/on-chain positions are untouched.)

## DEPLOY PROCEDURE (operator)

What to merge: the worktree branch (one source file + one new test):
- `src/execution/exchange_reconcile.py`
- `tests/execution/test_terminal_chain_closed_phantom_absorber.py`

Daemon restart: **required.** The reactor process imports `exchange_reconcile` at boot; the
new absorber is only loaded on (re)start. The in-memory ws-gap latch state
(`ws_gap_guard._status`) is rebuilt on boot — no manual latch poke is needed.

After restart, the corrected M5 ws-gap sweep (and the 1-minute refresh) will, on its next run:
1. absorb token `25998072…`: register `token_suppression('settled_position',
   source_module=exchange_reconcile.terminal_chain_closed_phantom_absorber)` and resolve
   `5bbc2be2` (`resolution=position_drift_terminal_chain_closed_phantom_suppressed`) — by
   re-evaluation, NOT a manual `resolved_at` write;
2. with zero unresolved findings + a healthy SUBSCRIBED stream, `clear_after_m5_reconcile`
   reopens the submit latch.

Do NOT manually UPDATE the finding row or the DB. The latch reopens via the normal sweep.

Confirm the latch reopened and the finding cleared (read-only, from the main checkout):
```sh
# (a) the stuck finding is resolved by the absorber (not a manual write):
sqlite3 state/zeus_trades.db \
 "SELECT resolved_at, resolution, resolved_by FROM exchange_reconcile_findings \
  WHERE finding_id='5bbc2be2-350c-4bdf-ac0e-f080e41f9012';"
#   expect: resolved_at set, resolution=position_drift_terminal_chain_closed_phantom_suppressed,
#           resolved_by=src.execution.exchange_reconcile

# (b) the token is now in the suppression registry:
sqlite3 state/zeus_trades.db \
 "SELECT suppression_reason, source_module FROM token_suppression \
  WHERE token_id='25998072565711727698258544609688934677406873903623466853003437606533488235694';"
#   expect: settled_position | exchange_reconcile.terminal_chain_closed_phantom_absorber

# (c) zero unresolved findings -> latch precondition met:
sqlite3 state/zeus_trades.db \
 "SELECT COUNT(*) FROM exchange_reconcile_findings WHERE resolved_at IS NULL;"
#   expect: 0  (or only genuinely-new, non-swept-winner findings)
```
Then confirm submit resumed: the reactor log should show the M5 ws-gap reconcile clearing the
latch (`M5 WS-gap reconcile cleared submit latch`) and order submission resuming. Any further
`position_drift` that is NOT a terminal-chain-closed swept winner (e.g. an active on-chain
position, or a non-settled disappearance) correctly stays fail-closed on the operator-ack path.

Worktree-sim caveat (not a deploy concern): a sandbox simulation that uses the worktree's own
empty `state/zeus-forecasts.db` sees no day-end evidence and would NOT absorb. Production is
unaffected — the daemon runs from the main checkout whose `ZEUS_FORECASTS_DB_PATH` is the live
registry.
