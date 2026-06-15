# Live Order-Submit Capability Verification — 2026-06-15

# Created: 2026-06-15
# Last reused or audited: 2026-06-15
# Authority basis: operator verification request (diagnose-order-emission-before-belief memory law);
#   read-only audit of MAIN TREE /Users/leofitz/zeus live daemon checkout. NO write / NO real order / NO tx.

## VERDICT

**ALIVE and CAPABLE.** The live order-submit path is wired end-to-end to the
Polymarket CLOB and is being SELECTED every reactor cycle. If the decision
engine produced a candidate that passed the economic gates (trade_score > 0,
positive after-cost EV), it WOULD reach `PolymarketV2Adapter.submit()` and post
a real order. The wallet authenticates right now, capital (~$1255) is available,
and there is NO gate currently blocking all submits.

**The current silence is THIN-EDGE, not dead plumbing.** Every cycle in the last
100k log lines reports `proof_accepted=0` — candidates ARE being evaluated and
correctly declined on negative/thin EV (e.g. `ev_per_dollar=-0.7493`,
`TRADE_SCORE_NON_POSITIVE`, `capital_efficiency_lcb_ev`). This is the decision
gate working, not the submit lane being dead. Contrast with the
edli_no_submit_receipts-dead-since-06-06 incident: there the *receipt* lane was
silent; here the receipt + reactor-cycle lanes are LIVE and emitting rejections
every cycle.

## Daemon liveness (now)

- `python -m src.main` PID 80540, started Sat 17:23, actively burning ~81% CPU.
- `logs/zeus-live.log` mtime = current minute (00:42 CDT / 05:42 UTC); log lines streaming live.
- `state/daemon-heartbeat.json`: `{"alive": true, "mode": "live"}` written this minute.
- `state/venue-heartbeat-keeper.json`: HEALTHY, 3338 consecutive successes,
  `last_success_at` this minute, `resting_order_safe: true`. The venue
  connection/lease is alive NOW.
- NO ERROR/CRITICAL/Traceback in last 100k log lines.

## Exact submit call chain (file:line)

1. Reactor decision → `OpportunityEventReactor` (final_intent_submit = submit_adapter)
2. `src/main.py:6006` selects the LIVE adapter IFF `(live_submit_effective and operator_arm is not None)`
3. `event_bound_live_adapter_from_trade_conn(...)` with `executor_submit=` →
   `submit_event_bound_final_intent_via_existing_executor` (`src/engine/event_bound_final_intent.py:107`)
4. `src/engine/event_reactor_adapter.py:1763-1766` — after gate chain passes:
   `_build_live_execution_command_certificates(...)` then `executor_submit(final_intent, command)`;
   `_live_submit_count[0] += 1` counts real venue calls.
5. `src/execution/executor.py:2178` → `VenueAdapterExecutor().submit(order)`
6. `src/venue/polymarket_v2_adapter.py:489` → `PolymarketV2Adapter.submit(envelope)`:
   `assert_live_submit_bound()` → `preflight()` (`get_ok()` auth probe) →
   `client.create_and_post_order(...)` (or `create_order`+`post_order`) — the real
   py-clob-client-v2 SDK sign+POST to the CLOB.
- Order params built by `create_submission_envelope` (polymarket_v2_adapter.py:430):
  token_id, side, price, size, tick_size, neg_risk, fee_details, post_only — intact.

**No break in the chain.** Every hop is present and wired.

## Gate chain — all OPEN (config + runtime verified)

Config (`config/settings.json` `edli` block):
- `reactor_mode = 'live'`  ✓ (this is what flips `real_submit_effective` True)
- `real_order_submit_enabled = true`  ✓
- `durable_submit_outbox_enabled = true`  ✓ (else EDLI_DURABLE_SUBMIT_OUTBOX_REQUIRED)
- `edli_live_operator_authorized = true`  ✓ (mints the operator_arm token)
- `edli_live_scope = 'forecast_plus_day0'`  ✓ (full live scope, not shadow)
- `pre_submit_balance_allowance_check_enabled = true`  ✓

Runtime proof the live lane is SELECTED, not degraded:
- **ZERO `LIVE LANE DARK` log lines** in last 300k lines. That loud per-cycle
  ERROR fires whenever the no-submit adapter is chosen while arm is on. It is NOT
  firing → the live adapter IS selected each cycle.
- ZERO degrade causes (`live_submit_effective_false:*`, `operator_arm_none`,
  `portfolio_state_unavailable`, `allocator_not_configured`) in the log.
- `EDLI live-bridge allocator refresh: CONFIGURED drawdown_pct=0.000 bankroll=1255.32`
  every cycle — allocator configured, no drawdown halt, portfolio state available.

No RiskGuard HALT / FROZEN / global freshness block. (15 `STALE_OBS_BOUNDARY_GUARD`
are day0-scoped, not a global submit block.)

## Credentials / wallet

- Proxy wallet = signature_type=2 (POLY_GNOSIS_SAFE; `DEFAULT_SIGNATURE_TYPE = 2`,
  polymarket_v2_adapter.py:69). Funder address sourced outside settings.json
  (env/keychain) — behavior proves it is correctly wired.
- **349 `GET .../balance-allowance?...COLLATERAL "HTTP/1.1 200 OK"`** in last 100k
  log lines (most recent at 00:41:48 this session). The SDK client authenticates
  against the CLOB successfully RIGHT NOW.
- Per-position CONDITIONAL token balance checks also returning 200 OK.
- **bankroll ≈ $1254–1255**, `drawdown_pct=0.000`. Capital available (matches
  expected ~$1162+ proxy wallet, grown slightly).

## Recent submit / receipt evidence (read-only, mode=ro)

`state/zeus_trades.db venue_commands` (50 rows, 2026-06-06 → 06-12):
- State dist: FILLED 40, EXPIRED 6, REJECTED 2, PARTIAL 1, SUBMIT_REJECTED 1.
- 40 FILLED orders carry real venue_order_ids (tx hashes) — the submit path
  DID reach the venue and fill within the window.
- Last venue_command created 2026-06-12T13:04 (EXPIRED limit). Last FILLED:
  EXIT SELL 2026-06-11T17:18 (`0x25f55df7...`). These are the MAINLINE executor lane.

`state/zeus-world.db`:
- `edli_no_submit_receipts`: 62,874 total; last at 2026-06-12T12:12, all recent =
  NO_SUBMIT (no candidate passed to a live submit).
- `edli_live_order_projection`: **0 rows** — the EDLI live lane has not minted a
  live order. Consistent with thin-edge: no EDLI candidate cleared the gates.
- `edli_live_cap_usage`: 441 rows, last 2026-06-12T17:05 (capital reservations
  during the active 06-10..06-12 window).

So the last actual ATTEMPT to create a venue order was ~2026-06-12; outcome of
the window = mix of fills/expiries/rejects (normal). Since then the engine has
declined every candidate on EV — not a silent failure.

## Why nothing has submitted since 06-12 (root cause = economics, not plumbing)

Live reactor-cycle log (current, 00:35–00:42 this session) shows the engine
evaluating 22-candidate event batches and rejecting them:
- `EVENT_BOUND_ALL_CANDIDATES_REJECTED:n=22 capital_efficiency_lcb_ev=15 direction_law=1 other=6`
- `TRADE_SCORE_NON_POSITIVE`
- Best candidates have negative EV: `ev_per_dollar=-0.7493 / -0.0622 / -0.0196`.
The submit seam is gated upstream on `proof_accepted` / `trade_score_positive`;
those candidates never get there because they have no edge. Correct behavior.

## Safe dry-run

No isolated build-calldata-without-send entry point was exercised (would require
constructing a live ExecutionIntent + fresh snapshot — out of read-only scope).
However the order-construction path is proven live by:
- `create_submission_envelope` building full envelopes in code (params verified).
- `preflight().get_ok()` succeeding (the 200-OK balance-allowance probes are the
  same authenticated SDK client the submit path uses).
- 40 historically-FILLED venue_commands with real tx hashes from this same lane.

## What it would take to make it submit (it is NOT blocked)

Nothing is blocked. The path is provably live. A real order requires only that
the decision engine produce a candidate with `trade_score > 0` and positive
after-cost EV passing the FDR/capital-efficiency gates. When that occurs the
existing, selected live adapter will post it. No flag flip, credential, or
plumbing repair is needed.

## Caveats (honest disclosure)

- Funder/proxy address value not directly inspected (sourced outside settings);
  liveness inferred from repeated authenticated 200-OK CLOB calls (strong, not a
  direct address assert).
- No live order was placed to prove an end-to-end fill (read-only constraint).
  The chain is proven by code wiring + auth success + historical fills, not by a
  fresh in-session fill.
- `edli_live_order_projection` empty means the EDLI-specific live lane has no
  *recent EDLI* fill on record; recent historical fills came via the mainline
  executor lane. Both share the same venue adapter terminal call.
