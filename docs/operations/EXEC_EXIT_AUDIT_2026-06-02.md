# EXEC / EXIT END-TO-END CORRECTNESS AUDIT — 2026-06-02

```
Created: 2026-06-02
Last reused or audited: 2026-06-02
Authority basis: GOAL#36 (3 e2e-correctness-checked FILLED orders + 120-min exit 守护)
Repo: zeus @ branch edli-correctness-recover-2026-06-02 @ d95f5e67
Mode at audit time: SHADOW (live_execution_mode=edli_shadow_no_submit,
  real_order_submit_enabled=false, edli_user_channel_reconcile_enabled=false), $0 capital
Type: READ-ONLY correctness audit. No edits, no flag flips, no restart.
```

This document traces ONE hypothetical *armed* fill end-to-end through all five legs of
the execution → exit → settlement → P&L chain, marks where semantic context is lost or a
bug breaks the chain, ranks every correctness bug by severity, and produces the explicit
BLOCKER LIST to GOAL#36. All code claims carry file:line; all data claims carry row
counts verified against the live DBs at audit time (HEAD d95f5e67).

---

## 1. THE END-TO-END CHAIN (per-leg verdict)

```
                        entry submit → ack → fill
LEG1  PROGRAMMATIC FILL COMPLETION ........................ BUG
        execute_final_intent → _live_order (6-phase) → place_limit_order
        → V2Adapter.submit → SDK post_order → ack commit (executor.py:3981)
        Path is fully automated (no manual gate). BUT 100% of orders die at a
        pre-venue gate; fill is NEVER persisted (venue_trade_facts=0,
        edli_live_profit_audit.filled_size NULL on all 92 rows).
                                |
                                v
                 fill → position_current + chain_shares
LEG2  EDLI FILL → POSITION BRIDGE ........................ CORRECT (but INERT)
        materialize_position_current_from_edli_fill (edli_position_bridge.py:384)
        writes canonical position_current; chain_shares populated by the
        always-on chain_sync cycle. Code correct. Gated OFF in shadow
        (edli_user_channel_reconcile_enabled=false) — no fill arrives to bridge.
                                |
                                v
                      hold → exit decision
LEG3  EXIT DECISION / 120-min 守护 ....................... BUG
        Active path = Position.evaluate_exit (portfolio.py) — fail-closed on
        missing evidence, D4 symmetry-gated EDGE_REVERSAL. BUT FLASH_CRASH_PANIC
        fires on a bare price move BEFORE the probability-authority guard, and
        the CI-separation 守护 design (screen_exit) is UNWIRED dead code.
                                |
                                v
                  settle → redeem → realize P&L
LEG4  SETTLEMENT / REDEEM / P&L .......................... BUG
        P&L formula correct. BUT harvester is gated OFF
        (ZEUS_HARVESTER_LIVE_ENABLED!=1), settlement_outcomes=0,
        and realized P&L has NO durable DB column — lives only in positions.json.
        8 positions stuck in exit_pending_missing / settled-not-redeemed.
                                |
                                v
                          sizing of the bet
LEG5  KELLY SIZING (#107/#103/#111) ...................... BUG
        Bankroll is on-chain-only (correct), #103 variance-carry FIXED. BUT
        single-Kelly: every bet sizes against the FULL bankroll independently
        (#107 open); portfolio_heat / win_rate / drawdown inputs use defaults.
```

**Net:** The chain is structurally complete and the math is correct where it runs, but
**no order has ever cleared leg 1**, and even if one did, legs 3 and 4 contain a spurious-exit
trigger and a settlement chain that cannot fire. Four of five legs carry an active BUG.

---

## 2. CORRECTNESS BUGS (severity-ranked)

Severity key: **SEV1** = would corrupt capital/P&L or fire a spurious exit · **SEV2** =
blocks a verified fill · **SEV3** = hygiene / latent.

### SEV1 — capital / P&L corruption or spurious exit

**SEV1-A — FLASH_CRASH_PANIC fires on a bare price move, before the belief gate.**
`exit_triggers.py:98` checks `current_edge_context.market_velocity_1h <= -0.15` and returns
an `immediate` FLASH_CRASH_PANIC exit *before* the probability-authority guard at
`exit_triggers.py:118`. The identical ordering exists in the live path at
`portfolio.py:995-1004`. `market_velocity_1h` is computed purely from `token_price_log`
raw market price delta (`monitor_refresh.py:1745`: `current_p_market - old_native_p`) — not
from any evidence-gated belief. **There is no consecutive-cycle confirmation and no CI
separation.** A thin book, a single large seller, a 15¢/hr data anomaly, or a temporary
liquidity event on a freshly-filled position fires a market-sell within minutes. This is
exactly the GOAL#36 failure mode: *"a short price move is NOT edge reversal."* The
code-comment defense ("Quote-only safety exits stay active when probability refresh is
degraded") is intentional design, which makes it a *design* SEV1, not an accident — and the
120-min 守护 must verify each exit is real, which this trigger cannot guarantee.
*Confirmed verbatim at exit_triggers.py:97-104; ordering before line 118 confirmed.*

**SEV1-B — The CI-separation 守护 (screen_exit) is UNWIRED dead code.**
`event_reactor_adapter.py:3192-3201` explicitly documents that
`enqueue_live_redecisions` / `screen_exit` are unwired dead code as of the 2026-05-31
audit (belief-cache write disabled to prevent a SQLite self-deadlock). The CI-separation
exit (SD7) and the EVIDENCE_UNAVAILABLE third state in `continuous_redecision.py:293-360`
are **unreachable** in the live daemon. The design claim — *"exit fires ONLY when belief CI
is DISJOINT below entry CI"* — is FALSE in the live path. The active exit is
`Position.evaluate_exit()` with a flat `consecutive_confirmations=2` EDGE_REVERSAL gate and
no CI-separation logic. GOAL#36's "verify each exit is correct, not spurious" depends on a
gate that does not run.

**SEV1-C — Realized P&L has no durable DB representation.**
`position_current` has no `exit_price` and no `realized_pnl` column. `compute_settlement_close`
(`portfolio.py:2165`) computes `_compute_realized_pnl` = `round(shares*exit_price -
cost_basis_usd, 2)` (formula correct, verified algebraically: Karachi +$1.00, London +$4.14)
but stores it only on the in-memory `pos.pnl` and in `positions.json` `recent_exits[].pnl`.
`edli_live_profit_audit` (92 rows, **0 with pnl_usd non-null**) is a decision-receipt table,
not a settlement-P&L table; neither REDEEM_CONFIRMED condition_id appears in it. **Loss of
positions.json = permanent loss of all realized P&L.** For a "3 e2e-correctness-checked
FILLED orders" goal, the P&L of those orders is not durably recorded anywhere queryable.

### SEV2 — blocks a verified fill

**SEV2-A — Pre-venue gate rejects 100% of orders (broader than tick_size).**
`executor.py:1746` raises on `intent.tick_size != snapshot.min_tick_size`. DB confirms this
is the dominant killer, but the wall is **multi-pronged**: of the rejected rows in
`edli_live_profit_audit`, reject_reason counts are tick_size=28, expected_fill_price
mismatch=9, DEPTH_INSUFFICIENT=5, decision_source_context integrity=2, submit_unknown
depth=1. Adjacent raises live at executor.py:1740 (token/direction), :1748 (min_order_size),
:1750 (neg_risk). **Zero orders have ever cleared this gate** (venue_trade_facts=0,
filled_size NULL on all 92 rows, most recent reject 2026-06-01T09:31:07). Fixing only
tick_size will surface the next gate; all five reject categories must be cleared.

**SEV2-B — Silent envelope drop (no DB row, no exception).**
`executor.py:3558` binds `pre_submit_envelope` only `if pre_submit_envelope is not None`. If
`snapshot_id` is absent upstream and the envelope is None, `place_limit_order()` returns a
`BOUND_ENVELOPE_REQUIRED` *dict* (not an exception) at
`polymarket_client.py:666-671`, which is treated as a non-fill with **no DB row and no log**.
This is an undetectable fill loss. Needs an audit of `snapshot_id` presence in
`FinalExecutionIntent` construction before arming.

**SEV2-C — fill_tracker quarantines CONFIRMED fills lacking an extractable trade_id.**
`fill_tracker.py:41` sets `FILL_STATUSES=frozenset({"CONFIRMED"})`; MATCHED/MINED/FILLED are
`OPTIMISTIC_FILL_STATUSES` only (confirmed at fill_tracker.py:42) and do not promote via this
path. `fill_tracker.py:944` quarantines any CONFIRMED position whose CLOB response yields no
trade_id via `_extract_trade_id`. For a FOK taker fill the executor ack path
(executor.py:3837-3895) is the primary promoter *if the ack carries a trade_id*; if ack
trade_id extraction fails and fill_tracker is the recovery path, **quarantine = permanent
fill loss.** No test proves ack trade_id extraction covers all Polymarket response shapes.

**SEV2-D — Settlement chain cannot fire (harvester gated OFF).**
`harvester_pnl_resolver.py:57` returns `status="disabled_by_feature_flag"` unless
`ZEUS_HARVESTER_LIVE_ENABLED=="1"` (env is UNSET at audit time → default "0"). With the gate
OFF, `resolve_pnl_for_settled_markets` never runs, so no position transitions to settled even
when truth exists. The Shanghai 2026-05-29 position (phase=active, 4 days past target,
chain_shares=16.75 synced) is stuck precisely because of this. **A FILLED order that wins
will never realize its P&L while this flag is OFF.**

### SEV3 — hygiene / latent

**SEV3-A — chain_shares NULL on 100/101 position_current rows.** All 100 are terminal-phase
(voided=75, settled=18, economically_closed=4, admin_closed=3); reconcile does not write
chain_shares to terminal phases — **expected, not a defect.** The single active row
(Shanghai) has chain_shares=16.75 synced. The #94 "100/101 NULL for *active* positions"
symptom is resolved in current state (active populated 1/1).

**SEV3-B — 8 positions in exit_pending_missing / settled-not-redeemed.** zeus_trades.db:
7 rows with chain_state=exit_pending_missing overall; among phase=settled rows the
chain_state breakdown is synced=15, exit_pending_missing=1, local_only=1, unknown=1 — i.e.
the redeem-incompletion touches *settled* rows too (London May19-class: settled but on-chain
redeem never completed). No settlement_commands exist to progress them. Micro-$ now, but a
structural redeem-completion gap that will strand winning capital post-arm.

**SEV3-C — DAY0_OBSERVATION_REVERSAL uses entry_ci_width frozen at entry.**
`portfolio.py:1147,1307` call `conservative_forward_edge(forward_edge, entry_ci_width)`
where `entry_ci_width` is frozen at entry (`monitor_refresh.py:1767-1768` "known caveat").
After process restart / JSON fallback reload, an under-estimated entry_ci_width makes the
exit threshold too loose. Known-gap, latent.

**SEV3-D — #107 single-Kelly vs full bankroll.** `kelly_size()` = `f* × kelly_mult ×
bankroll` (kelly.py:31-63) with bankroll = full on-chain wallet; no open-exposure
subtraction, no portfolio correction. `max_single_position_pct=0.1` /
`max_portfolio_heat_pct=0.5` exist in settings.json but are **unwired** into the sizing path.
At $43 bankroll × 0.25 mult × f*<1 the practical cap is ~$10.75/bet (not the 25%/$43 cited),
so not a *fill* blocker at shadow scale — but the portfolio-Kelly design gap is real and N
concurrent bets each size against the full wallet. **#103 variance-carry is FIXED** (#111):
`SizingContext.from_candidate_proof` feeds ci_width+lead into `dynamic_kelly_mult`
(money_path_adapters.py:130-145); however win_rate/heat/drawdown still use defaults.

**SEV3-E — bankroll cache staleness up to 1800s (#96).** `bankroll_provider.cached()` uses a
30-min resilient bound. Intentional (wallet moves only on own fills/settlements) but a stale
value can size against an out-of-date wallet if RPC has been failing. Low risk at canary
scale; unresolved.

---

## 3. WHERE SEMANTIC CONTEXT IS LOST (relationship view)

These are the cross-module boundaries where Module A's output reaches Module B and meaning
is dropped — the Fitz-Constraint-#4 surfaces:

1. **price_log → exit decision (LEG3).** `token_price_log` stores *raw market prices*, not
   evidence-gated beliefs. When that delta crosses −0.15/hr it becomes a FLASH_CRASH_PANIC
   "edge reversal" with no belief context attached. A market move is laundered into an edge
   claim. (exit_triggers.py:98 ← monitor_refresh.py:1745)

2. **belief CI → exit (LEG3).** The CI-separation gate that *would* preserve "is this a real
   edge reversal" was severed at the wiring layer (event_reactor_adapter.py:3192). The
   semantic intent ("exit only on disjoint CI") survives in code comments but not in the
   executing path.

3. **settlement truth → P&L durability (LEG4).** `compute_settlement_close` knows the exact
   realized P&L at the moment of settlement, then drops it into an in-memory object + JSON.
   The DB — the canonical truth store — never receives it. Settlement *event* survives;
   settlement *value* does not.

4. **forecasts.settlements → grading authority (LEG4).** settlement_outcomes (the table the
   audit ground names as "resolved truth: winning_bin/settlement_value/settlement_unit") has
   **0 rows**; the live grading path reads forecasts.settlements (6488 rows / 6134 VERIFIED)
   instead. The "authoritative" outcomes table and the table actually consulted are
   different objects — a provenance split.

5. **envelope binding → fill outcome (LEG1).** A missing snapshot_id collapses into a
   BOUND_ENVELOPE_REQUIRED dict that looks identical to "no fill." The reason for non-fill is
   lost at the dict-vs-exception boundary (polymarket_client.py:666 ← executor.py:3558).

---

## 4. BLOCKER LIST → "3 e2e-correctness-checked FILLED orders + 120-min 守护"

Ordered by what must be cleared first. Each is a hard precondition to arming.

| # | Blocker | Leg | Sev | File:line | Gate to clear |
|---|---------|-----|-----|-----------|---------------|
| B1 | Pre-venue gate rejects 100% (tick_size + 4 more) | 1 | SEV2 | executor.py:1740-1750 | All 5 reject categories pass; one order reaches V2Adapter.submit |
| B2 | Silent envelope drop (no row, no exception) | 1 | SEV2 | executor.py:3558 / polymarket_client.py:666 | snapshot_id present in every FinalExecutionIntent; non-fill emits a DB row |
| B3 | fill_tracker quarantine on missing trade_id | 1 | SEV2 | fill_tracker.py:944 | test proving ack trade_id extraction covers all Polymarket response shapes |
| B4 | Bridge inert in shadow | 2 | — | main.py:4506,4724 | edli_user_channel_reconcile_enabled=true at arm (required by edli_live boot anyway) |
| B5 | FLASH_CRASH_PANIC spurious exit | 3 | **SEV1** | exit_triggers.py:98 / portfolio.py:995 | gate behind belief/CI evidence OR consecutive-cycle confirm before it can fire the 守护 |
| B6 | CI-separation 守护 unwired | 3 | **SEV1** | event_reactor_adapter.py:3192 | re-wire screen_exit without the SQLite self-deadlock, OR prove evaluate_exit's D4 gate is a sufficient 守护 |
| B7 | Harvester gated OFF | 4 | SEV2 | harvester_pnl_resolver.py:57 | ZEUS_HARVESTER_LIVE_ENABLED=1 with forecasts.settlements cadence verified |
| B8 | Realized P&L not durable | 4 | **SEV1** | portfolio.py:2165 | exit_price/realized_pnl persisted to a DB column, not just positions.json |
| B9 | Stranded redeem (8 positions) | 4 | SEV3 | exit_lifecycle.py:763 | exit_pending_missing → REDEEM completion path proven on-chain |

**Minimum arm-readiness set:** B1, B2, B3 (a fill can happen and is recorded) + B5, B6
(the 120-min 守护 cannot fire a spurious exit) + B7, B8 (a winning fill realizes durable
P&L). B4 is satisfied automatically by entering edli_live mode. B9 is required before any
*winning* order can be redeemed but does not block the first *fill*.

**Do NOT arm until B1, B5, B6, B8 are closed.** B1 because no fill is otherwise possible;
B5/B6 because the 守护 would not be trustworthy (spurious exit risk to capital); B8 because
a "correctness-checked filled order" with no durable P&L record cannot be correctness-checked
after the fact.

---

## 5. WHAT IS ACTUALLY CORRECT (so it is not re-litigated)

- P&L formula `round(shares*exit_price - cost_basis_usd, 2)` — algebraically verified on
  both REDEEM_CONFIRMED positions (portfolio.py:2132).
- Tick alignment: ROUND_FLOOR on both buy and sell (executor.py:1635,1663) — correct for taker.
- Bridge canonical-write path + token placement by direction (edli_position_bridge.py:361-364)
  + local_only phantom-void protection (chain_reconciliation.py:1038-1040) — correct.
- Bankroll is on-chain-wallet-only, authority=canonical enforced, no hardcode
  (event_reactor_adapter.py:4854-4875).
- evaluate_exit fail-closed on missing evidence (portfolio.py:165-179) + D4 evidence-symmetry
  gate on EDGE_REVERSAL/BUY_NO (cycle_runtime.py:325-392) — correct anti-twitch for the
  belief-driven exits (the FLASH_CRASH path bypasses it, SEV1-A).
- #103 variance-carry into dynamic_kelly_mult (#111 done) — ci_width+lead wired
  (money_path_adapters.py:130-145).

---

*All data claims verified against state/zeus-world.db, state/zeus-forecasts.db,
state/zeus_trades.db at HEAD d95f5e67, 2026-06-02. Code claims confirmed by direct file read
within this audit session. READ-ONLY: no file outside this doc was created or modified.*
